#!/usr/bin/env python3
"""test_estado_caso.py — estado vivo del caso (29-sep-2026, plan «Vega al mando» Fase 2).

Datos falsos en un estado temporal (ningún dato real):
  · un informe que cumple una espera → se PROPONE cerrarla (no se cierra sola)
  · un informe sin relación          → no propone nada
  · el fichero de estado lista informes recientes, esperas con retraso y próximos plazos
  · una segunda pasada sin informes nuevos no vuelve a proponer
"""
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="estado_caso_")
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "state")
os.environ["BTP_HISTORIAL"] = os.path.join(_TMP, "clinico", "_historial")
os.makedirs(os.environ["BTP_STATE_DIR"])
os.makedirs(os.environ["BTP_HISTORIAL"])
sys.path.insert(0, os.path.join(ROOT, "tools"))
import estado_caso as ec  # noqa: E402

_fail = 0


def ok(cond, name):
    global _fail
    if not cond:
        _fail += 1
        print("  ✗ %s" % name)


hoy = date.today()
ahora = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
hilos = [
    {"id": "h1", "titulo": "Resultado HLA de alta resolución", "estado": "esperando",
     "quien_espera": "laboratorio", "plazo": (hoy - timedelta(days=3)).isoformat()},
    {"id": "h2", "titulo": "Cita de revisión con traumatología", "estado": "esperando",
     "quien_espera": "hospital", "plazo": (hoy + timedelta(days=2)).isoformat()},
    {"id": "h3", "titulo": "Algo ya resuelto HLA", "estado": "hecho"},
    # mismo plazo que h2: en los datos reales dos esperas con el mismo plazo rompían la ordenación
    {"id": "h4", "titulo": "Segunda opinión de radiología", "estado": "esperando",
     "quien_espera": "centro", "plazo": (hoy + timedelta(days=2)).isoformat()},
]
json.dump({"hilos": hilos}, open(os.path.join(os.environ["BTP_STATE_DIR"], "seguimiento.json"), "w"))
with open(os.path.join(os.environ["BTP_HISTORIAL"], "_eventos.jsonl"), "w") as fh:
    for f in ("2026-09-28 - LAB - Tipaje HLA alta resolucion.pdf", "2026-09-28 - HOSP - Analitica rutina.pdf"):
        fh.write(json.dumps({"ts": ahora, "tipo": "informe", "fichero": f, "fecha": "2026-09-28",
                             "centro": "X", "sha": "0", "identidad": "coincide"}) + "\n")

enviados = []


class _S:
    @staticmethod
    def report_to_titular(texto, **_k):
        enviados.append(texto)
        return {"delivered": True}


sys.modules["salida"] = _S

r = ec.actualizar(avisar=True)
ok(r["nuevos"] == 2, "lee los 2 informes nuevos (%d)" % r["nuevos"])
ok([p["hilo"] for p in r["propuestas"]] == ["h1"], "⭐ propone cerrar solo la espera que cumple (%s)"
   % [p["hilo"] for p in r["propuestas"]])
ok(enviados and "HLA" in enviados[0], "el aviso nombra el informe y la espera")
seg = json.load(open(os.path.join(os.environ["BTP_STATE_DIR"], "seguimiento.json")))
ok(all(h["estado"] != "hecho" for h in seg["hilos"] if h["id"] == "h1"), "⭐ no cierra la tarjeta sola")

md = open(r["estado"]).read()
ok("Tipaje HLA" in md and "Analitica rutina" in md, "el estado lista los informes recientes")
ok("3 días" in md, "⭐ la espera vencida muestra su retraso")
ok("traumatología" in md.split("Plazos de los próximos 7 días")[1], "el plazo próximo aparece")
ok("Algo ya resuelto" not in md, "lo hecho no aparece como espera")
ok(os.path.dirname(r["estado"]) == os.path.join(_TMP, "clinico"), "el estado vive junto al historial (zona clínica)")

enviados.clear()
r2 = ec.actualizar(avisar=True)
ok(r2["nuevos"] == 0 and not enviados, "una segunda pasada sin novedades no repite el aviso")

if _fail:
    print("❌ test_estado_caso: %d fallo(s)" % _fail)
    sys.exit(1)
print("✅ test_estado_caso: cruza informes con esperas, propone sin cerrar y mantiene el estado")
