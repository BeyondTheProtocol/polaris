#!/usr/bin/env python3
"""tests/test_seguimiento_objetivo_ned.py — un hilo sin «¿acerca a NED?» se ve, no se pierde.

EL FALLO (deuda seguimiento-add-sin-objetivo-ned, 21-sep-2026). El KPI de etiquetado del Tablero
cayó del 92 % al 0 % en ocho días sin que nadie tocara nada: `add_hilo` aceptaba el campo vacío
por defecto y todo hilo nacía así, incluidos tres creados a mano.

Decisión: NO rechazar (perder una tarea es peor que tenerla sin etiqueta, y rechazar rompería el
Observatorio y dos detectores) y NO rellenar con un genérico (inflaría el indicador con plantilla).
Se AVISA al crear y se LISTA lo que falta, para que lo complete quien tiene criterio.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OK, FALLOS = [], []


def ok(msg, cond, extra=""):
    (OK if cond else FALLOS).append(msg)
    print(("  ✅ " if cond else "  ❌ ") + msg + (("  " + extra) if extra and not cond else ""))


tmp = tempfile.mkdtemp(prefix="test-seg-ned-")
os.environ["BTP_STATE_DIR"] = tmp
sys.path.insert(0, os.path.join(ROOT, "tools"))
import seguimiento as sg  # noqa: E402

# Que el hook de tarea-nueva NO mande nada a {{TITULAR}}: solo avisa para los orígenes de
# ORIGENES_AUTONOMOS_AVISO, así que el test usa orígenes que están FUERA y lo comprueba aquí.
# (Una versión anterior de este test se fiaba de una variable de entorno que no existía: no
# mandó nada por suerte, no por diseño.)
_ORIGENES_TEST = ("calendar", "manual")
if any(o in sg.ORIGENES_AUTONOMOS_AVISO for o in _ORIGENES_TEST):
    print("  ❌ un origen del test avisa por Telegram: no se corre para no mandarle nada")
    sys.exit(1)

# Aislar el fichero del Tablero en el tmp, sea cual sea la ruta que resuelva el módulo.
for nombre in ("SEGUIMIENTO", "SEG", "SEG_PATH", "SEGUIMIENTO_PATH"):
    if hasattr(sg, nombre):
        setattr(sg, nombre, os.path.join(tmp, "seguimiento.json"))
ruta = next((getattr(sg, n) for n in ("SEGUIMIENTO", "SEG", "SEG_PATH", "SEGUIMIENTO_PATH")
             if hasattr(sg, n)), None)
ok("el test aísla el Tablero fuera del sistema vivo", bool(ruta) and ruta.startswith(tmp),
   "-> %r" % ruta)
if ruta and ruta.startswith(tmp):
    json.dump({"hilos": []}, open(ruta, "w"))

    # 1. Sin objetivo_ned: ENTRA (no se pierde) y AVISA.
    err = io.StringIO()
    with redirect_stderr(err):
        hid = sg.add_hilo({"titulo": "Recoger el CD de la RM", "estado": "esperando",
                           "origen": "calendar"})
    ok("un hilo sin objetivo_ned se CREA igual (no se pierde ninguna tarea)", bool(hid))
    ok("y avisa al crearlo, con su origen", "SIN objetivo_ned" in err.getvalue()
       and "calendar" in err.getvalue(), "-> %r" % err.getvalue()[:120])

    # 2. Con objetivo_ned: entra y NO avisa.
    err = io.StringIO()
    with redirect_stderr(err):
        sg.add_hilo({"titulo": "Pedir HLA de alta resolución", "estado": "esperando",
                     "origen": "manual",
                     "objetivo_ned": "sin HLA no se pueden predecir neoantígenos"})
    ok("con objetivo_ned no hay aviso", "SIN objetivo_ned" not in err.getvalue())

    # 3. Y lo que falta se puede LISTAR.
    faltan = [h["titulo"] for h in sg.sin_objetivo_ned()]
    ok("sin_objetivo_ned lista el que falta", "Recoger el CD de la RM" in faltan, "-> %r" % faltan)
    ok("y no el que sí lo declara", "Pedir HLA de alta resolución" not in faltan)

    # 4. No rellena a escondidas: el campo sigue vacío, así el KPI dice la verdad.
    h = next(x for x in sg.load_seguimiento()["hilos"] if x["titulo"] == "Recoger el CD de la RM")
    ok("NO se rellena con un genérico (el KPI no se infla con plantilla)",
       not (h.get("objetivo_ned") or "").strip(), "-> %r" % h.get("objetivo_ned"))

# 5. Vega sabe que tiene que escribirlo.
vega = open(os.path.join(ROOT, ".claude", "agents", "asistente.md"), encoding="utf-8").read()
ok("Vega tiene la instrucción de escribir objetivo_ned al crear", "`objetivo_ned`" in vega
   and "sin-ned" in vega)

subprocess.run(["rm", "-rf", tmp], capture_output=True)
print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS
      else "test_seguimiento_objetivo_ned: %d/%d OK" % (len(OK), len(OK)))
sys.exit(1 if FALLOS else 0)
