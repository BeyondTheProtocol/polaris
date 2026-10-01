#!/usr/bin/env python3
"""test_radar_revision.py — lo descartado se revisa (1-oct-2026, recomendación de {{CONTACTO}} en el
directo del 28-sep). Cola temporal, datos falsos.

  · descartado por «solo hueso» ANTES del cambio de perfil → vuelve a la cola, con el veredicto anterior
  · descartado por «solo hueso» DESPUÉS del cambio → no (ya se juzgó sabiéndolo)
  · descartado por otro motivo → no
  · ensayo cerrado hace >180 días → vuelve; un paper viejo, no
  · lo del archivo append-only también cuenta
  · una segunda pasada no reabre lo mismo
"""
import datetime
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="radar_rev_")
os.environ["BTP_STATE_DIR"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))
import radar_ned_diario as r  # noqa: E402

r.DIR_ESTADO = os.path.join(_TMP, "radar_ned")
r.COLA = os.path.join(r.DIR_ESTADO, "cola_verificacion.json")
r.ARCHIVO_CERRADOS = os.path.join(r.DIR_ESTADO, "cerrados_archivo.jsonl")
r.REVISADOS = os.path.join(r.DIR_ESTADO, "revisados.json")
os.makedirs(r.DIR_ESTADO)
_fail = 0


def ok(cond, name):
    global _fail
    if not cond:
        _fail += 1
        print("  ✗ %s" % name)


def lead(uid, tipo, cerrado, veredicto):
    return {"uid": uid, "tema": "t", "tipo": tipo, "titulo": uid, "ref": "", "url": "",
            "fecha_fuente": "", "encolado": cerrado, "cerrado": cerrado + "T10:00:00", "veredicto": veredicto}


r.guarda_cola({"pendientes": [], "cerrados": [
    lead("antes", "ensayo", "2026-07-10", "Descartado: exige enfermedad visceral y ella es solo hueso."),
    lead("despues", "ensayo", "2026-08-25", "Descartado: pensado para solo hueso."),
    lead("otro", "ensayo", "2026-07-10", "Descartado: exige mutación BRCA."),
    lead("paper-viejo", "paper", "2025-12-01", "Sin relación."),
]})
with open(r.ARCHIVO_CERRADOS, "w", encoding="utf-8") as f:
    f.write(json.dumps(lead("viejo", "ensayo", "2025-12-01", "No recluta en Europa.")) + "\n")

conf = os.path.join(_TMP, "conf.json")
json.dump({"dias_revision": 180, "tipos_por_tiempo": ["ensayo"], "cambios_perfil": [
    {"clave": "visceral", "desde": "2026-08-18", "patron": "solo hueso", "motivo": "ya no es solo hueso"}]},
    open(conf, "w"))

hoy = datetime.date(2026, 10, 1)
n = r.revisar_descartes(hoy, conf)
pend = {p["uid"]: p for p in r.lee_cola()["pendientes"]}
ok(n == 2 and set(pend) == {"antes", "viejo"}, "reabre lo que toca: %r" % sorted(pend))
ok(pend.get("antes", {}).get("revision") == "ya no es solo hueso"
   and "solo hueso" in pend.get("antes", {}).get("veredicto_anterior", ""),
   "lleva el motivo de la revisión y el veredicto anterior")
ok("veredicto" not in pend.get("antes", {}), "vuelve como pendiente, sin veredicto")
ok(r.revisar_descartes(hoy, conf) == 0, "segunda pasada: no reabre lo mismo")
ok(r.revisar_descartes(hoy, os.path.join(_TMP, "no_existe.json")) == 0, "sin config, no hace nada")
ok(json.load(open(os.path.join(ROOT, "tools", "config", "radar_revision.json")))["cambios_perfil"],
   "la config real tiene cambios de perfil")

print("test_radar_revision: %s" % ("OK" if not _fail else "%d FALLOS" % _fail))
sys.exit(1 if _fail else 0)
