#!/usr/bin/env python3
"""test_vega_metrics.py — F4.4 observabilidad. Métricas deterministas de Vega (throughput + daemons
vivos por frescura de heartbeat), read-only. Aislado en tmp."""
import datetime
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="vmet_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_REPO"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))
import vega_metrics as vm    # noqa: E402
import seguimiento           # noqa: E402

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _hb(agente, edad_h):
    os.makedirs(os.path.join(_TMP, "heartbeat"), exist_ok=True)
    ts = (datetime.datetime.utcnow() - datetime.timedelta(hours=edad_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
    json.dump({"agente": agente, "ts": ts, "estado": "ok"},
              open(os.path.join(_TMP, "heartbeat", agente + ".json"), "w"))


def main():
    hoy = datetime.date.today()
    seguimiento.recopilar = lambda: {"items": [
        {"id": "a", "estado": "hecho", "hecho_el": hoy.isoformat()},
        {"id": "b", "estado": "hecho", "hecho_el": (hoy - datetime.timedelta(days=3)).isoformat()},
        {"id": "c", "estado": "en_curso"},
    ], "pendientes_ok": [{"id": "d1"}, {"id": "d2"}]}
    seguimiento.perseguir = lambda ejecutar=False: {"salidas": [{"hilo_id": "c"}]}

    os.makedirs(os.path.join(_TMP, "persecucion"), exist_ok=True)
    json.dump({"job_id": "j1"}, open(os.path.join(_TMP, "persecucion", "c.json"), "w"))
    _hb("centinela-ned", 0.05)     # fresco (< 0.5h) → vivo
    _hb("asistente", 48)           # 2 días → caído

    m = vm.metricas()
    ok(m["hilos"]["cerrados_hoy"] == 1, "cerrados_hoy = 1")
    ok(m["hilos"]["cerrados_7d"] == 2, "cerrados_7d = 2")
    ok(m["hilos"]["cayendo"] == 1, "cayendo = 1 (de perseguir)")
    ok(m["hilos"]["borradores_a_un_clic"] == 2, "borradores a un clic = 2")
    ok(m["persecucion_en_curso"] == 1, "persecución en curso = 1")
    ok(m["daemons"]["centinela-ned"]["vivo"] is True, "centinela fresco → vivo")
    ok(m["daemons"]["asistente"]["vivo"] is False, "asistente stale (48h) → caído")
    ok("Vega" in vm._texto(m) and "cerrados hoy" in vm._texto(m), "resumen legible")

    print("RESULTADO métricas de Vega (F4.4): %d OK, %d fallos" % (_pass, _fail))
    print("✅ VEGA-METRICS EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
