#!/usr/bin/env python3
"""test_vega_vision_fusiones.py — Vega ve lo que las sesiones han fusionado a casa base.

1-oct-2026 («Vega orquesta todo»): `cerrar_sesion.py` apunta cada fusión en el registro de
aprobaciones con quien = sesion:<rama>. La visión de Vega lo enseña; lo que apruebe ella misma
(quien = vega) no se mezcla ahí, y lo de hace más de 24 h tampoco.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

tmp = tempfile.mkdtemp(prefix="vv_fus_")
os.environ["BTP_STATE_DIR"] = tmp
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import vega_vision  # noqa: E402

fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


check(vega_vision.fusiones_de_sesiones() == ["Fusiones de las sesiones en 24 h: ninguna."],
      "sin registro: dice «ninguna»")

ahora = datetime.now()
os.makedirs(os.path.join(tmp, "vega"))
filas = [
    {"ts": (ahora - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S"), "quien": "sesion:worktree-a",
     "que": "fix(x): arreglo A", "nivel": "A"},
    {"ts": (ahora - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S"), "quien": "vega",
     "que": "aprobada propuesta: B", "nivel": "A"},
    {"ts": (ahora - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%S"), "quien": "sesion:worktree-viejo",
     "que": "fix(y): viejo", "nivel": "A"},
]
with open(os.path.join(tmp, "vega", "aprobaciones.jsonl"), "w") as fh:
    fh.write("\n".join(json.dumps(f) for f in filas) + "\n")

out = vega_vision.fusiones_de_sesiones()
texto = "\n".join(out)
check(out[0].endswith(": 1"), "cuenta solo las de sesión de las últimas 24 h (%r)" % out[0])
check("worktree-a" in texto and "arreglo A" in texto, "nombra rama y qué se fusionó")
check("aprobada propuesta" not in texto, "lo aprobado por Vega no se mezcla")
check("viejo" not in texto, "lo de hace más de 24 h no sale")
check("Fusiones de las sesiones" in vega_vision.bloque(), "el bloque completo incluye la sección")

print("\n" + ("✅ VEGA_VISION FUSIONES EN VERDE" if not fallos else "❌ %d fallo(s)" % len(fallos)))
sys.exit(1 if fallos else 0)
