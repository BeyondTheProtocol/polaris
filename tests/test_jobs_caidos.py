#!/usr/bin/env python3
"""test_jobs_caidos.py — un encargo que no llegó a ejecutarse no puede morir en silencio.

`cola.py` manda a failed/ por json-ilegible, schema-invalido, caducado, profundidad y
dead-letter. De todos ellos solo `schema-desconocido` tenía red (el auto-recover B2 del
healthcheck); el resto se quedaba ahí para siempre sin que nadie lo dijera.

Importa sobre todo por la CADUCIDAD: `bot_telegram` es el único productor que pone `expira`,
o sea que el job perecedero es justo el que manda {{TITULAR}} por Telegram. Un encargo suyo hecho
con el lazo parado caducaba y se evaporaba.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="jobs_caidos_")
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "state")
os.environ["BTP_REPO"] = _TMP
FAILED = os.path.join(os.environ["BTP_STATE_DIR"], "queue", "failed")
os.makedirs(FAILED, exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import healthcheck as h  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _job(nombre, motivo, intencion="algo", terminado=None):
    d = {"id": nombre, "intencion": intencion, "ultimo_error": motivo}
    if terminado:
        d["terminado"] = terminado
    json.dump(d, open(os.path.join(FAILED, nombre + ".json"), "w", encoding="utf-8"))


def _hace(dias):
    """Fecha ISO de hace N días, como la escribe `cola.mark_failed` en `terminado`."""
    import datetime
    return (datetime.datetime.now() - datetime.timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%S")


def _limpiar():
    for f in os.listdir(FAILED):
        os.remove(os.path.join(FAILED, f))


def claves(alertas):
    return {k for k, _ in alertas}


def main():
    # Cola limpia → silencio. Nunca "0 jobs caídos", que sería ruido diario.
    al, info = h._check_jobs_caidos()
    check("sin jobs caídos → sin alerta", not al)
    check("info reporta 0", info.get("failed_total") == 0)

    # El caso que de verdad duele: un encargo de {{TITULAR}} que caducó.
    _job("j1", "caducado", "reservar el tren a Barcelona")
    _job("j2", "caducado", "otra")
    al, info = h._check_jobs_caidos()
    check("caducados → alerta", "jobs_caidos:caducado" in claves(al))
    texto = next(t for k, t in al if k == "jobs_caidos:caducado")
    check("dice cuántos son", "2 encargo" in texto)
    check("nombra el primero, para que sea accionable", "tren a Barcelona" in texto)
    check("explica que caducó, no el código interno", "caducó antes de ejecutarse" in texto)

    # Clave estable: el número es volátil y meterlo en la clave rompería el anti-flapping
    # (avisaría de nuevo cada vez que cambia el conteo).
    _job("j3", "caducado", "una tercera")
    al2, _ = h._check_jobs_caidos()
    check("la clave NO cambia al cambiar el conteo", claves(al) == claves(al2))

    # Motivos distintos → alertas distintas (no se funden en una sola línea inútil).
    _limpiar()
    _job("a", "schema-invalido")
    _job("b", "json-ilegible")
    al, _ = h._check_jobs_caidos()
    check("un motivo, una alerta", claves(al) == {"jobs_caidos:schema-invalido", "jobs_caidos:json-ilegible"})

    # `schema-desconocido` NO alerta: ya tiene su propio auto-recover (se re-encola solo tras
    # fusionar el consumidor). Duplicarlo sería avisar de algo que se arregla solo.
    _limpiar()
    _job("c", "schema-desconocido: campo nuevo")
    al, info = h._check_jobs_caidos()
    check("schema-desconocido no duplica el auto-recover", not al)
    check("…pero sí se cuenta en info", info.get("failed_total") == 1)

    # Un job ilegible no puede tumbar el check.
    _limpiar()
    open(os.path.join(FAILED, "roto.json"), "w").write("{esto no es json")
    al, info = h._check_jobs_caidos()
    check("un fichero corrupto no revienta el check", info.get("failed_total") == 1)

    # ── Ventana de frescura (31-jul-26) ──────────────────────────────────────────────────────
    # Nadie vacía failed/. Sin ventana, dos cadáveres del 30-jul tuvieron el hallazgo escalado
    # 73 veces y la batería roja tres días, mientras el problema real ya no ocurría. Una alarma
    # que confunde «hay cadáveres» con «se está muriendo gente» deja de leerse.
    _limpiar()
    _job("viejo", "rc=1", "un encargo de la semana pasada", terminado=_hace(9))
    al, info = h._check_jobs_caidos()
    check("un cadáver viejo NO grita", not al)
    check("…pero se sigue contando, no se esconde", info.get("failed_viejos") == 1)
    check("y el total no miente", info.get("failed_total") == 1)

    _job("nuevo", "rc=1", "un encargo de ayer", terminado=_hace(0))
    al, info = h._check_jobs_caidos()
    check("una muerte reciente SÍ grita", "jobs_caidos:rc=1" in claves(al))
    texto = next(t for k, t in al if k == "jobs_caidos:rc=1")
    check("cuenta solo los recientes, no los 2", "1 encargo" in texto)
    check("dice la ventana, para que se entienda el número", "últimos 2 días" in texto)

    # ── La misión del job de auto-reparación tiene que CABER en el muro ───────────────────────
    # El 30-jul el encargo decía «arréglala, la fontanería se ejecuta, no se apunta», y en modo
    # autónomo el guard bloquea python -c, awk, ToolSearch, el binario claude y editar código.
    # El agente chocaba contra la pared hasta salir con rc=1, 3 veces por alerta.
    orig = h.q.enqueue
    capturado = {}
    h.q.enqueue = lambda intencion, **kw: capturado.setdefault("i", intencion)
    try:
        h._encolar_investigacion("clave_x", "pasa algo")
    finally:
        h.q.enqueue = orig
    mision = capturado.get("i") or ""
    check("el encargo se emite", bool(mision))
    check("NO le pide arreglar código", "arréglala" not in mision)
    check("le dice que el muro le bloquea", "muro" in mision and "autónomo" in mision)
    check("le dice qué SÍ puede usar", "tools/" in mision)
    check("le exige dejarlo escrito en el libro", "deuda.py abrir" in mision)
    check("no le deja cerrar en falso", "EFECTO" in mision)

    print("RESULTADO jobs caídos: %d OK, %d fallos" % (_pass, _fail))
    print("✅ JOBS CAÍDOS VIGILADOS" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
