#!/usr/bin/env python3
"""test_acciones_datadas.py — Vega propone lo que tiene fecha antes de que {{TITULAR}} lo pida (2-oct-2026).

Fixture: el caso real de Barcelona (tres reservas encadenadas, cancelación gratis en la nota) y un
vuelo con fecha_salida.
  1. deriva los dos cambios de alojamiento y el fin de la cancelación, con plazo ISO correcto
  2. vuelo: check-in a T-1 y la pregunta de asistencia a T-3
  3. lo pasado y lo no reservado no se persiguen
  4. modo sombra (por defecto): apunta, NO crea hilos; y no duplica la sombra
  5. modo activo: crea hilos derivados, silenciosos (origen fuera de los que avisan) y con plazo
  6. segunda pasada: 0 nuevas; un hilo que {{TITULAR}} cerró no se reabre
  7. lint: hilo con fecha en el título y sin plazo → se lista; «15 marcas» no es una fecha
  8. cableado: hoy_compose.sh lo lanza con --write
"""
import json
import os
import sys
import tempfile
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="acc_datadas_")
os.environ["BTP_STATE_DIR"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))
import seguimiento  # noqa: E402
import acciones_datadas as ad  # noqa: E402

_fail = 0


def ok(cond, name):
    global _fail
    if not cond:
        _fail += 1
        print("  ✗ %s" % name)


HOY = date(2026, 10, 1)
json.dump({"encargos": {
    "a": {"localizador": "1", "checkin": "2026-09-30", "checkout": "2026-10-02",
          "nota": "Aparthotel Aura Park, L'Hospitalet. Pago en el hotel."},
    "b": {"localizador": "2", "checkin": "2026-10-02", "checkout": "2026-10-04",
          "nota": "Aparthotel Castell Beach, Castelldefels. Cancelación gratis hasta 30-sep."},
    "c": {"localizador": "3", "checkin": "2026-10-04", "checkout": "2026-10-08",
          "nota": "Aparthotel Aura Park, L'Hospitalet. Cancelación gratis hasta 3-oct."},
    "d": {"estado": "reservado", "titulo": "Vuelo BCN→AGP — {{TITULAR}}", "fecha_salida": "2026-10-08"},
    "e": {"checkin": "2026-10-10", "checkout": "2026-10-12", "nota": "Opción sin reservar"},
}}, open(os.path.join(_TMP, "reservas.json"), "w"))
seguimiento.add_hilo({"id": "manual-1", "titulo": "Mañana 2-oct: cambiar de apartamento",
                      "origen": "manual", "objetivo_ned": "x"})
seguimiento.add_hilo({"id": "manual-2", "titulo": "Correos de las 15 marcas", "origen": "manual",
                      "objetivo_ned": "x"})

# 1-3
acc = {a["id"]: a for a in ad.derivar(HOY)}
ok(acc.get("derivado-cambio-aloj-2026-10-02", {}).get("plazo") == "2026-10-02",
   "1: ⭐ cambio de alojamiento del 2-oct con plazo (%s)" % sorted(acc))
ok(acc.get("derivado-cambio-aloj-2026-10-04", {}).get("plazo") == "2026-10-04", "1: y el del 4-oct")
canc = [a for a in acc.values() if a["id"].startswith("derivado-cancelacion-")]
ok(len(canc) == 1 and canc[0]["plazo"] == "2026-10-03",
   "1: ⭐ fin de cancelación gratis 3-oct; la del 30-sep ya pasó (%s)" % canc)
ok(any(a["plazo"] == "2026-10-07" and "Check-in" in a["titulo"] for a in acc.values()),
   "2: check-in online el día antes del vuelo")
ok(any(a["plazo"] == "2026-10-05" and "asistencia" in a["titulo"] for a in acc.values()),
   "2: pregunta de asistencia 3 días antes")
ok(not any("2026-10-10" in a["id"] for a in acc.values()), "3: lo no reservado no se persigue")
ok(not any("2026-09-30" in a["id"] for a in acc.values()), "3: lo pasado no se persigue")

# 4
r = ad.aplicar(write=True, hoy=HOY)
hilos = {h["id"] for h in seguimiento.load_seguimiento()["hilos"]}
ok(r["modo"] == "sombra" and not any(h.startswith("derivado-") for h in hilos),
   "4: ⭐ en sombra no crea hilos")
sombra = os.path.join(_TMP, "vega", "acciones_sombra.jsonl")
n1 = len(open(sombra).read().splitlines())
ad.aplicar(write=True, hoy=HOY)
ok(n1 == len(acc) and len(open(sombra).read().splitlines()) == n1, "4: la sombra no se duplica")

# 5
json.dump({"modo": "activo"}, open(os.path.join(_TMP, "vega", "acciones_datadas.json"), "w"))
avisos = []
seguimiento._avisar_tarea_nueva = lambda h: avisos.append(h)
r = ad.aplicar(write=True, hoy=HOY)
h2 = {h["id"]: h for h in seguimiento.load_seguimiento()["hilos"]}
ok(len(r["creadas"]) == len(acc), "5: crea los hilos en modo activo")
ok(h2["derivado-cambio-aloj-2026-10-02"]["plazo"] == "2026-10-02"
   and h2["derivado-cambio-aloj-2026-10-02"]["origen"] == "derivado", "5: con plazo y origen derivado")
ok(avisos == [], "5: ⭐ silenciosos al nacer (no disparan aviso)")
ok("derivado" not in seguimiento.ORIGENES_AUTONOMOS_AVISO, "5: «derivado» no está en los que avisan")

# 6
seguimiento.cerrar_tarea("derivado-cambio-aloj-2026-10-04")
r = ad.aplicar(write=True, hoy=HOY)
estado = {h["id"]: h["estado"] for h in seguimiento.load_seguimiento()["hilos"]}
ok(r["nuevas"] == [] and estado["derivado-cambio-aloj-2026-10-04"] == "hecho",
   "6: ⭐ 0 nuevas y lo que cerró {{TITULAR}} sigue cerrado")

# 7
lint = {h["id"] for h in ad.sin_plazo_con_fecha()}
ok("manual-1" in lint, "7: ⭐ el hilo con fecha y sin plazo se lista")
ok("manual-2" not in lint, "7: «15 marcas» no es una fecha")

# 8
hoy_sh = open(os.path.join(ROOT, "tools", "hoy_compose.sh")).read()
ok("acciones_datadas.py\" --write" in hoy_sh, "8: ⭐ hoy_compose.sh lo lanza antes del parte")

if _fail:
    print("❌ test_acciones_datadas: %d fallo(s)" % _fail)
    sys.exit(1)
print("✅ test_acciones_datadas: lo que tiene fecha se propone solo, con plazo, sin avisar al nacer y sin pisar lo cerrado")
