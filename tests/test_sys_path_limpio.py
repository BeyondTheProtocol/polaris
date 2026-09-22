#!/usr/bin/env python3
"""test_sys_path_limpio.py — importar una tool no puede decidir de dónde importan las demás.

EL FALLO (20-sep-2026). `tools/polaris_estado.py` hacía, a nivel de módulo:

    ROOT = casa_base(); TOOLS = os.path.join(ROOT, "tools")
    sys.path.insert(0, TOOLS)   # para poder importar salida sin depender del cwd

La intención era buena y está documentada: ese módulo MIDE el sistema vivo, así que sus rutas
son las de casa base a propósito (el 12-sep dio 93/100 desde un worktree y 98/100 desde casa
base, el mismo segundo). Pero `sys.path` es del PROCESO: desde un worktree, eso dejaba
`<casa base>/tools` al FRENTE para siempre, y todo lo que se importara después —por cualquiera—
resolvía a casa base en vez de a la rama.

CÓMO SE VIO. `healthcheck._check_roster_daemons()` importa `polaris_estado`. A partir de ahí,
`import seguimiento` daba el fichero de CASA BASE. Un test de la rama se quedaba probando el
código de casa base y **pasaba en verde**: verificar antes de fusionar era teatro. Es la misma
clase que el bug que `tests/test_clinico_guard.py` ya documenta («apuntaba a ~/claudecode fijo,
así que desde un worktree probaba el guard viejo»), pero invisible, porque aquí no hay ninguna
ruta escrita a mano — solo un `sys.path` que alguien dejó torcido.

QUÉ FIJA ESTE TEST. Que importar cualquiera de estas tools deje el `sys.path` como estaba, salvo
su propio directorio. No prohíbe mirar a casa base: `polaris_estado` sigue midiéndola, y sigue
dando la misma nota desde los dos árboles. Lo que ya no puede es decidírselo al resto del
proceso.

Corre en subproceso por módulo: un import ya hecho no se puede deshacer dentro del mismo intérprete.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")

# Los que miran a casa base a propósito y por eso son los que pueden ensuciar. Añadir aquí
# cualquier tool nueva que use `casa_base()` para construir rutas de import.
SOSPECHOSOS = ["polaris_estado", "healthcheck", "seguimiento", "salida"]

_SONDA = r"""
import json, os, sys
sys.path.insert(0, %r)
antes = list(sys.path)
import importlib
try:
    importlib.import_module(%r)
except Exception as e:
    print(json.dumps({"error": "%%s: %%s" %% (type(e).__name__, e)}))
    raise SystemExit(0)
nuevos = [p for p in sys.path if p not in antes]
print(json.dumps({"nuevos": nuevos}))
"""

_pass = _fail = 0


def ok(cond, que):
    global _pass, _fail
    if cond:
        _pass += 1
        print("  ✓ %s" % que)
    else:
        _fail += 1
        print("  ✗ %s" % que)


def main():
    for mod in SOSPECHOSOS:
        p = subprocess.run([sys.executable, "-c", _SONDA % (TOOLS, mod)],
                           capture_output=True, text=True, timeout=120, cwd=ROOT)
        linea = (p.stdout or "").strip().splitlines()
        if not linea:
            ok(False, "%s: la sonda no devolvió nada (%s)" % (mod, (p.stderr or "")[-120:]))
            continue
        try:
            d = json.loads(linea[-1])
        except ValueError:
            ok(False, "%s: salida ilegible: %s" % (mod, linea[-1][:80]))
            continue
        if "error" in d:
            print("  ⏭️  %s no se pudo importar aquí (%s): no se juzga" % (mod, d["error"][:60]))
            continue
        # Su propio directorio es legítimo; cualquier OTRA ruta añadida es contaminación.
        sucias = [n for n in d["nuevos"] if os.path.abspath(n) != os.path.abspath(TOOLS)]
        ok(not sucias, "importar %s no añade rutas ajenas al sys.path%s"
           % (mod, "" if not sucias else ": %s" % sucias))

    print("test_sys_path_limpio: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
