#!/usr/bin/env python3
"""test_reap_vivo.py — `cola.reap_stuck` no puede rescatar un job que SIGUE ejecutándose.

Hallazgo medio nº7 de la auditoría del 25-jul-26: `reap_stuck` decidía por mtime a secas
(>3600 s → atascado), sin mirar si el dispatcher lo estaba corriendo. El mtime no distingue
«murió a mitad» de «tarda mucho», y confundirlos abre la puerta a ejecutar el job dos veces
(y a cobrarlo dos veces). Ahora consulta el latido del dispatcher: mismo job + latido fresco +
PID vivo = en curso, no se toca.

El fail-closed es hacia el RESCATE: sin latido, con latido rancio o con el PID muerto, el job SÍ
se rescata. Un crash real no puede quedarse colgado porque una lectura falle.
"""
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import cola as q  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _preparar(tmp, job_id="abc123"):
    """Deja UN job en processing/ con mtime viejo (candidato a rescate por edad)."""
    q.STATE = tmp
    q.QUEUE = os.path.join(tmp, "queue")
    q._ensure_dirs()
    p = os.path.join(q.QUEUE, "processing", "job-%s.json" % job_id)
    job = {
        "id": job_id, "prioridad": "normal", "intencion": "x", "agente": None,
        "modelo": None, "perfil": "privileged", "intentos": 1, "max_intentos": 3,
        "creado": "2026-07-25T00:00:00", "expira": None, "procedencia": "test",
        "tope_job_usd": None, "ultimo_error": None, "terminado": None,
        "coste_usd": None, "tipo": "exec", "criticidad": "rutina",
        "caja_id": None, "linaje": None, "profundidad": 0,
    }
    with open(p, "w", encoding="utf-8") as f:
        json.dump(job, f)
    viejo = time.time() - 7200          # 2 h: muy por encima del umbral por defecto
    os.utime(p, (viejo, viejo))
    return p


def _latido(tmp, last_job, pid, edad_seg=0):
    d = os.path.join(tmp, "dispatcher")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "heartbeat.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"ts": "2026-07-25T20:00:00", "pid": pid, "last_job": last_job}, f)
    if edad_seg:
        t = time.time() - edad_seg
        os.utime(p, (t, t))
    return p


def main():
    vivo = os.getpid()          # este mismo proceso: un PID que existe, garantizado

    # 1. El dispatcher lo está ejecutando AHORA → NO se rescata (el caso que faltaba)
    with tempfile.TemporaryDirectory() as tmp:
        p = _preparar(tmp)
        _latido(tmp, "abc123", vivo)
        n = q.reap_stuck(max_edad_seg=3600)
        check("job en curso (latido fresco + PID vivo) no se rescata", n == 0 and os.path.exists(p))

    # 2. Sin latido ninguno → se rescata (fail-closed hacia el rescate)
    with tempfile.TemporaryDirectory() as tmp:
        p = _preparar(tmp)
        n = q.reap_stuck(max_edad_seg=3600)
        check("sin latido, el job atascado SÍ se rescata", n == 1 and not os.path.exists(p))

    # 3. El latido nombra OTRO job → este quedó huérfano, se rescata
    with tempfile.TemporaryDirectory() as tmp:
        p = _preparar(tmp)
        _latido(tmp, "otro999", vivo)
        n = q.reap_stuck(max_edad_seg=3600)
        check("latido de otro job → este se rescata", n == 1 and not os.path.exists(p))

    # 4. Latido RANCIO (el dispatcher dejó de refrescar = murió a mitad) → se rescata
    with tempfile.TemporaryDirectory() as tmp:
        p = _preparar(tmp)
        _latido(tmp, "abc123", vivo, edad_seg=q.LATIDO_VIVO_SEG + 60)
        n = q.reap_stuck(max_edad_seg=3600)
        check("latido rancio → se rescata (no blinda a un muerto)", n == 1 and not os.path.exists(p))

    # 5. PID muerto → se rescata. PID 999999 no existe en macOS (tope ~99998).
    with tempfile.TemporaryDirectory() as tmp:
        p = _preparar(tmp)
        _latido(tmp, "abc123", 999999)
        n = q.reap_stuck(max_edad_seg=3600)
        check("PID muerto → se rescata", n == 1 and not os.path.exists(p))

    # 6. Latido ilegible (JSON roto) → se rescata, no lanza
    with tempfile.TemporaryDirectory() as tmp:
        p = _preparar(tmp)
        hb = _latido(tmp, "abc123", vivo)
        with open(hb, "w", encoding="utf-8") as f:
            f.write("{no soy json")
        n = q.reap_stuck(max_edad_seg=3600)
        check("latido ilegible → se rescata sin lanzar", n == 1 and not os.path.exists(p))

    # 7. Un job JOVEN no se toca ni con latido ni sin él (no cambia el contrato de siempre)
    with tempfile.TemporaryDirectory() as tmp:
        q.STATE = tmp
        q.QUEUE = os.path.join(tmp, "queue")
        q._ensure_dirs()
        jid = q.enqueue("recién llegado", procedencia="test")
        j = q.dequeue()
        n = q.reap_stuck(max_edad_seg=3600)
        check("job joven sigue en processing", n == 0 and j and j["id"] == jid)

    print("test_reap_vivo: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
