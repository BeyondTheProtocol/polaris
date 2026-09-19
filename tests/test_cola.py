#!/usr/bin/env python3
"""test_cola.py — batería de la cola persistente (P1).

Aísla el estado en un tmp (parchea q.QUEUE) y verifica prioridad/FIFO, caducidad,
dead-letter, schema inválido, reap y claim atómico. Cada bug nuevo → caso aquí.
"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import cola as q  # noqa: E402

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def setup(tmp):
    q.QUEUE = os.path.join(tmp, "queue")
    q._ensure_dirs()


def main():
    tmp = tempfile.mkdtemp(prefix="test_queue_")
    setup(tmp)

    # 1. prioridad: alta sale antes que normal/baja; FIFO dentro del carril.
    q.enqueue("normal-1", prioridad="normal", procedencia="t")
    time.sleep(0.01)
    q.enqueue("baja-1", prioridad="baja", procedencia="t")
    time.sleep(0.01)
    q.enqueue("alta-1", prioridad="alta", procedencia="t")
    time.sleep(0.01)
    q.enqueue("alta-2", prioridad="alta", procedencia="t")
    orden = [q.dequeue()["intencion"] for _ in range(4)]
    check("prioridad+FIFO", orden == ["alta-1", "alta-2", "normal-1", "baja-1"])
    check("cola vacia → dequeue None", q.dequeue() is None)

    # 2. caducidad: un job expirado no se ejecuta, va a failed/.
    ayer = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    q.enqueue("caduco", procedencia="t", expira=ayer)
    check("dequeue salta el caducado", q.dequeue() is None)
    check("caducado en failed/", q.get_status()["failed"] >= 1)

    # 2b. una FECHA SUELTA vale, y vale hasta el final de ese día (30-jul-26).
    # Antes solo se parseaba el formato largo: `2026-07-31` caía al `except` y se leía como
    # caducada en el acto. Como `healthcheck.py` emitía exactamente eso al auto-encolarse un
    # arreglo, TODOS sus jobs de auto-reparación morían al nacer con intentos=0 — y el detector de
    # jobs caídos encolaba otro igual, que moría igual. 6 encargos muertos, 146 re-detecciones.
    hoy_suelto = datetime.now().strftime("%Y-%m-%d")
    q.enqueue("caduca-hoy-pero-aun-vale", procedencia="t", expira=hoy_suelto)
    j = q.dequeue()
    check("fecha suelta de HOY todavía vale", j is not None and j["intencion"] == "caduca-hoy-pero-aun-vale")
    if j:
        q.mark_done(j)
    ayer_suelto = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    q.enqueue("caduco-suelto", procedencia="t", expira=ayer_suelto)
    check("fecha suelta de AYER sí caduca", q.dequeue() is None)
    q.enqueue("basura", procedencia="t", expira="mañana por la tarde")
    check("fecha ilegible sigue siendo fail-closed", q.dequeue() is None)

    # 2c. Lo que el sistema se AUTO-adjudica lleva techo de gasto (31-jul-26). Un job de
    # radar-taller quemó 2,53 USD en dos intentos fallidos sin producir un commit, con
    # tope_job_usd=null: sin techo. Lo que nadie aprobó no puede gastar sin límite.
    jid = q.enqueue("mejora del taller", procedencia="radar-taller")
    job = q._load(q._find_by_id("pending", jid))
    check("radar-taller lleva tope por defecto", job["tope_job_usd"] == q.TOPE_AUTO_USD)
    jid = q.enqueue("investiga la alerta", procedencia="healthcheck:jobs_caidos:rc=1")
    check("las auto-investigaciones de salud, tambien",
          q._load(q._find_by_id("pending", jid))["tope_job_usd"] == q.TOPE_AUTO_USD)
    jid = q.enqueue("lo que pidio {{TITULAR}}", procedencia="telegram")
    check("lo que pide una persona NO se capa solo",
          q._load(q._find_by_id("pending", jid))["tope_job_usd"] is None)
    jid = q.enqueue("mejora cara aprobada", procedencia="radar-taller", tope_job_usd=5.0)
    check("un tope explicito manda sobre el defecto",
          q._load(q._find_by_id("pending", jid))["tope_job_usd"] == 5.0)
    for _ in range(4):
        j = q.dequeue()
        if j:
            q.mark_done(j)

    # 3. dead-letter: a max_intentos fallos → failed/, no re-encola.
    q.enqueue("venenoso", procedencia="t", max_intentos=2)
    j = q.dequeue()
    r1 = q.mark_failed(j, "boom")
    check("1er fallo reencola", r1 == "reencolado" and q.get_status()["pending"] == 1)
    j = q.dequeue()
    r2 = q.mark_failed(j, "boom")
    check("2o fallo dead-letter", r2 == "dead-letter")
    check("no reencola tras dead-letter", q.dequeue() is None)

    # 4. schema inválido: campo extra / tipo malo → failed/, nunca se ejecuta.
    import json
    bad = os.path.join(q.QUEUE, "pending", "1-x-bad1.json")
    json.dump({"id": "bad1", "prioridad": "normal", "intencion": "x", "perfil": "privileged",
               "intentos": 0, "max_intentos": 3, "creado": "2026-01-01T00:00:00",
               "procedencia": "t", "EXTRA": "inyección"}, open(bad, "w"))
    check("schema inválido no se sirve", q.dequeue() is None)
    bad2 = os.path.join(q.QUEUE, "pending", "1-x-bad2.json")
    json.dump({"id": "bad2", "prioridad": "normal", "intencion": "x", "perfil": "privileged",
               "intentos": "NO_INT", "max_intentos": 3, "creado": "2026-01-01T00:00:00",
               "procedencia": "t"}, open(bad2, "w"))
    check("tipo inválido no se sirve", q.dequeue() is None)

    # 4b. forward-compat: un campo TOP-LEVEL desconocido (skew de versión: worktree con un
    # campo nuevo, consumidor viejo) NO se ejecuta (fail-closed, anti-inyección) PERO se
    # etiqueta `schema-desconocido` (recuperable), no `schema-invalido`. Un campo nuevo
    # podría ser un FRENO → rechazar es lo correcto, solo que de forma diagnosticable.
    def _failed_motivo(job_id):
        p = q._find_by_id("failed", job_id)
        return q._load(p).get("ultimo_error", "") if p else ""
    # Campo sintético que NO está (ni estará) en la allowlist: prueba el skew sin apostar por
    # un nombre de negocio que otra sesión podría legitimar (p.ej. 'criticidad', que ya entró).
    nuevo = os.path.join(q.QUEUE, "pending", "1-x-skew1.json")
    json.dump({"id": "skew1", "prioridad": "normal", "intencion": "x", "perfil": "privileged",
               "intentos": 0, "max_intentos": 3, "creado": "2026-01-01T00:00:00",
               "procedencia": "t", "tipo": "exec", "__campo_futuro__": "x"}, open(nuevo, "w"))
    check("campo desconocido no se sirve", q.dequeue() is None)
    check("campo desconocido → schema-desconocido (recuperable)",
          "schema-desconocido" in _failed_motivo("skew1") and "__campo_futuro__" in _failed_motivo("skew1"))

    # 4c. un VALOR de enum fuera de dominio (perfil/tipo basura) es malformado/plantado, NO
    # skew → sigue siendo rechazo DURO `schema-invalido`, nunca `schema-desconocido`.
    valmal = os.path.join(q.QUEUE, "pending", "1-x-valmal.json")
    json.dump({"id": "valmal", "prioridad": "normal", "intencion": "x", "perfil": "root",
               "intentos": 0, "max_intentos": 3, "creado": "2026-01-01T00:00:00",
               "procedencia": "t", "tipo": "exec"}, open(valmal, "w"))
    check("valor de enum malo no se sirve", q.dequeue() is None)
    mv = _failed_motivo("valmal")
    check("valor de enum malo → schema-invalido duro (no desconocido)",
          "schema-invalido" in mv and "schema-desconocido" not in mv)

    # 5. claim atómico: un job solo sale una vez aunque se pida 'dequeue' de más.
    q.enqueue("unico", procedencia="t")
    a = q.dequeue()
    b = q.dequeue()
    check("claim único", a is not None and b is None)
    check("unico en processing", q.get_status()["processing"] >= 1)

    # 6. mark_done mueve a done/.
    q.mark_done(a, coste_usd=0.12)
    check("done tras mark_done", q.get_status()["done"] >= 1)

    # 7. reap_stuck: un processing viejo → failed.
    q.enqueue("atasco", procedencia="t")
    jj = q.dequeue()
    old = jj["_path"]
    os.utime(old, (time.time() - 9999, time.time() - 9999))
    n = q.reap_stuck(max_edad_seg=10)
    check("reap_stuck rescata el atascado", n >= 1)

    # 8. requeue devuelve a pending sin contar intento.
    q.enqueue("re", procedencia="t")
    jr = q.dequeue()
    intentos_antes = jr["intentos"]
    q.requeue(jr["id"])
    again = q.dequeue()
    check("requeue no cuenta intento", again is not None and again["intentos"] == intentos_antes)

    print("RESULTADO cola.py: %d OK, %d fallos" % (_pass, _fail))
    print("✅ COLA EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
