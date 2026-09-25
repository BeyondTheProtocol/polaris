#!/usr/bin/env python3
"""test_rutinas_latido.py — que una rutina parada se note a tiempo (25-sep-2026).

Tres fallos de la misma clase, «el vigía mira un número que no es el real»:
  1. `healthcheck._roster_meta` daba cadencia SEMANAL a toda rutina con `Weekday`: la auto-mejora
     (lun/mié/vie/dom, hueco real 2 días) avisaba de inactividad a los 21 días.
  2. `RUTINAS_NED` no declaraba periodo: una rutina que dejaba de arrancar con su último latido en
     «ok» no avisaba nunca. Aquí se comprueba que el periodo declarado cuadra con su plist.
  3. `radar_ned_diario._ya_hay_job_del_comite` buscaba en `running/`, que la cola no tiene.

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.
"""
import json
import os
import plistlib
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="rutinas_latido_")
os.environ["BTP_STATE_DIR"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import healthcheck as hc        # noqa: E402
import radar_ned_diario as rnd  # noqa: E402
import cola                     # noqa: E402
from _entorno import exige_cola_aislada  # noqa: E402

_pass = _fail = 0


def ok(cond, msg):
    global _pass, _fail
    if cond:
        _pass += 1
        print("  ✅", msg)
    else:
        _fail += 1
        print("  ❌", msg)


H = 3600
D = 86400


def cadencia_tests():
    cad = hc._cadencia_calendario
    auto_mejora = [{"Hour": 5, "Minute": 8, "Weekday": w} for w in (1, 3, 5, 0)]
    ok(cad(auto_mejora) == 2 * D, "auto-mejora lun/mié/vie/dom → 2 días, no 7")
    ok(cad({"Hour": 5, "Minute": 40}) == D, "una vez al día → 1 día")
    ok(cad([{"Hour": 8, "Minute": 20}, {"Hour": 14, "Minute": 0}]) == 18 * H + 20 * 60,
       "dos veces al día (8:20 y 14:00) → el hueco largo, 18 h 20 min")
    ok(cad({"Hour": 5, "Minute": 0, "Weekday": 1}) == 7 * D, "una vez a la semana → 7 días")
    ok(cad({"Hour": 5, "Minute": 0, "Weekday": 7}) == 7 * D, "Weekday 7 es domingo, igual que 0")
    ok(cad({"Minute": 15}) == H, "sin Hour → cada hora")
    ok(cad({"Day": 1, "Hour": 9, "Minute": 0}) == 30 * D, "mensual → 30 días (conservador)")
    ok(cad([]) is None and cad("basura") is None, "ilegible → None (no se alerta)")
    ok(cad({"Hour": "x"}) is None, "valor no numérico → None")


def roster_meta_tests():
    """Que `_roster_meta` USE la cadencia real: los tests de healthcheck lo mockean, así que
    revertir solo esa llamada dejaba todo en verde (cazado por verificación, 25-sep)."""
    real = hc._dir_plists
    hc._dir_plists = lambda: os.path.join(ROOT, "tools", "launchd")
    try:
        meta = hc._roster_meta()
    finally:
        hc._dir_plists = real
    ok(meta.get("com.btp.auto-mejora", (None,))[0] == 2 * D,
       "_roster_meta da 2 días a la auto-mejora con su plist real (antes 7 → aviso a los 21)")


def periodo_declarado_tests():
    """El periodo que declara RUTINAS_NED no puede ser más corto que el hueco real de su plist
    (avisaría de más) ni más de 2× (avisaría tarde)."""
    for d in hc.RUTINAS_NED:
        p = os.path.join(ROOT, "tools", "launchd", d["plist"] + ".plist")
        ok(os.path.exists(p), "%s: su plist %s existe" % (d["agente"], d["plist"]))
        if not os.path.exists(p):
            continue
        with open(p, "rb") as fh:
            pl = plistlib.load(fh)
        ok(pl.get("EnvironmentVariables", {}).get("BTP_AGENT") == d["agente"],
           "%s: el plist corre ese mismo agente" % d["agente"])
        real_h = hc._cadencia_calendario(pl.get("StartCalendarInterval")) / H
        if d["plist"] == "com.btp.radar-lit":
            real_h = 31 * 24          # mensual: el mes más largo, no los 30 conservadores
            # (el latido del agente lo escribe también radar_ned_dia.sh a diario: esto es cota)
        ok(real_h <= d["periodo_h"] <= 2 * real_h,
           "%s: periodo declarado %d h cuadra con su plist (%d h)" % (d["agente"], d["periodo_h"], real_h))


def cola_comite_tests():
    exige_cola_aislada()       # escribe fixtures en la cola: nunca en la viva
    for sub in cola.SUBDIRS:
        os.makedirs(os.path.join(cola.QUEUE, sub), exist_ok=True)
    ok(cola.QUEUE.startswith(_TMP), "la cola del test es la temporal, no la viva")
    ok(rnd._ya_hay_job_del_comite() is False, "cola vacía → no hay comité")
    p = os.path.join(cola.QUEUE, "processing", "1-x.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"agente": "comite-medico"}, fh)
    ok(rnd._ya_hay_job_del_comite() is True, "comité en processing/ → se ve (antes se miraba running/)")
    os.remove(p)
    p = os.path.join(cola.QUEUE, "pending", "1-y.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"agente": "comite-medico"}, fh)
    ok(rnd._ya_hay_job_del_comite() is True, "comité en pending/ → se ve")
    os.remove(p)


def main():
    print("test_rutinas_latido")
    cadencia_tests()
    roster_meta_tests()
    periodo_declarado_tests()
    cola_comite_tests()
    print("RESULTADO: %d OK, %d fallos" % (_pass, _fail))
    print("✅ EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
