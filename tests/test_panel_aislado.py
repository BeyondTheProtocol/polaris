#!/usr/bin/env python3
"""tests/test_panel_aislado.py — la batería no escribe en el PANEL-LAZO de verdad (22-sep-2026).

test_dispatcher.sh pasa BTP_REPO=<árbol bajo prueba> y cada pasada de test_all dejaba 12 jobs
falsos en su PANEL-LAZO.md (gitignored): en un worktree bloqueaba la poda, en casa base
ensuciaba el panel real. Congela: BTP_PANEL redirige; en batería sin BTP_PANEL, nunca casa base;
fuera de la batería, casa base como siempre.
"""
import os
import subprocess
import sys
import tempfile

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
fallos = 0


def ruta(env_extra):
    env = {k: v for k, v in os.environ.items() if k not in ("BTP_PANEL", "BTP_TEST_BATTERY")}
    env.update(env_extra)
    return subprocess.run([sys.executable, "-c", "import panel; print(panel.ruta_panel())"],
                          cwd=TOOLS, env=env, capture_output=True, text=True).stdout.strip()


def check(nombre, cond, detalle=""):
    global fallos
    print(("  ✓ " if cond else "  ✗ ") + nombre + ("" if cond else "  (%s)" % detalle))
    fallos += 0 if cond else 1


with tempfile.TemporaryDirectory() as tmp:
    falsa = os.path.join(tmp, "casa")
    real = os.path.join(falsa, "00_FUENTE-DE-VERDAD", "Gestion", "PANEL-LAZO.md")
    r = ruta({"BTP_REPO": falsa, "BTP_PANEL": os.path.join(tmp, "p.md")})
    check("BTP_PANEL manda", r == os.path.join(tmp, "p.md"), r)
    r = ruta({"BTP_REPO": falsa, "BTP_TEST_BATTERY": "1"})
    check("en batería sin BTP_PANEL no toca el panel de casa base", r != real and not r.startswith(falsa), r)
    r = ruta({"BTP_REPO": falsa})
    check("fuera de la batería, el panel de casa base", r == real, r)

    # Extremo a extremo: un append en batería no crea nada bajo BTP_REPO.
    env = {k: v for k, v in os.environ.items() if k != "BTP_PANEL"}
    env.update(BTP_REPO=falsa, BTP_TEST_BATTERY="1", BTP_BANDEJA=os.path.join(tmp, "b.md"))
    subprocess.run([sys.executable, os.path.join(TOOLS, "panel.py"), "append", "--job", "t", "--did", "x"],
                   env=env, capture_output=True)
    check("append en batería no crea el panel bajo BTP_REPO", not os.path.exists(real))

print("RESULTADO panel aislado: %s" % ("OK" if not fallos else "%d fallo(s)" % fallos))
sys.exit(1 if fallos else 0)
