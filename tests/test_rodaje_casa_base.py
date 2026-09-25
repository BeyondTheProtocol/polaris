#!/usr/bin/env python3
"""tests/test_rodaje_casa_base.py — que el rodaje del muro no pierda la memoria ni se quede ciego.

Dos fallos del mismo origen (resolver rutas contra el árbol donde vive el fichero):

1. EL ESTADO SE PERDÍA (deuda rodaje-muro-guarda-estado-en-el-worktree). `rodaje_muro.py`
   guardaba `<árbol>/tools/state/rodaje_muro.json`. Un rodaje se lanza desde el worktree de la
   rama que se rueda, así que el estado moría con la poda: el 20-sep la deuda del guard de la
   ventanilla clínica decía «está en rodaje» y el fichero no existía.

2. SE QUEDABA CIEGO (21-sep, al arreglar lo anterior). Desde el 20-sep el log de auditoría
   clínica vive siempre en casa base. `_lineas_log` seguía leyendo el del árbol, que en un
   worktree o no existe o es una copia congelada que el harness pone al reciclarlo (57.473
   líneas, 440 por detrás de la real). Resultado: «0 accesos nuevos» con aspecto de funcionar.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OK, FALLOS = [], []


def ok(msg, cond, extra=""):
    (OK if cond else FALLOS).append(msg)
    print(("  ✅ " if cond else "  ❌ ") + msg + (("  " + extra) if extra and not cond else ""))


tmp = tempfile.mkdtemp(prefix="test-rodaje-")
casa = os.path.join(tmp, "casa")
os.makedirs(os.path.join(casa, ".claude", "logs"))
open(os.path.join(casa, ".claude", "logs", "clinico-access.log"), "w").write(
    "".join("2026-09-21T0%d:00:00\tALLOW\tBash\tx\n" % i for i in range(5)))

codigo = r"""
import os, sys, json
sys.path.insert(0, %r)
import rodaje_muro as r
print(json.dumps({"estado": r.ESTADO, "lineas": len(r._lineas_log())}))
""" % os.path.join(ROOT, "tools")
env = dict(os.environ, BTP_REPO=casa, BTP_STATE_DIR=os.path.join(casa, "tools", "state"))
import json  # noqa: E402
out = json.loads(subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True,
                                env=env, timeout=30).stdout)

ok("el estado vive en casa base, no junto al fichero",
   out["estado"].startswith(os.path.join(casa, "tools", "state")), "-> %r" % out["estado"])
ok("nunca dentro de .claude/worktrees", "/.claude/worktrees/" not in out["estado"])
ok("uno por rama (dos rodajes a la vez no se pisan)",
   os.path.dirname(out["estado"]).endswith("rodaje_muro") and out["estado"].endswith(".json"))
ok("lee el log de auditoría de CASA BASE", out["lineas"] == 5,
   "-> vio %r líneas; el de casa base tiene 5" % out["lineas"])

# Y si el árbol trae una copia congelada del log, NO se lee esa.
arbol_log = os.path.join(ROOT, ".claude", "logs", "clinico-access.log")
if os.path.exists(arbol_log):
    ok("ignora la copia del log que haya en el árbol", out["lineas"] == 5)

subprocess.run(["rm", "-rf", tmp], capture_output=True)
print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS
      else "test_rodaje_casa_base: %d/%d OK" % (len(OK), len(OK)))
sys.exit(1 if FALLOS else 0)
