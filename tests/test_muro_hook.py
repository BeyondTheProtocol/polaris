#!/usr/bin/env python3
"""Tests del guardián del muro (tools/hooks/muro_commit_guard.py). Sin git real; prueba la lógica."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "hooks"))
import muro_commit_guard as g  # noqa: E402

fallos = 0


def check(nombre, cond):
    global fallos
    print(("  ✓ " if cond else "  ✗ ") + nombre)
    if not cond:
        fallos += 1


# tocar el muro sin firma → BLOQUEA
b, t = g.bloquea(["CLAUDE.md"], {})
check("CLAUDE.md sin firma → bloquea", b and t == ["CLAUDE.md"])

# tocar el muro CON firma → permite
b, _ = g.bloquea(["CLAUDE.md"], {"BTP_MURO_OK": "1"})
check("CLAUDE.md con BTP_MURO_OK=1 → permite", not b)

# no tocar el muro → permite
b, _ = g.bloquea(["tools/x.py", "tests/y.py"], {})
check("sin tocar el muro → permite", not b)

# muro mezclado con otros, sin firma → bloquea
b, t = g.bloquea(["tools/x.py", "CLAUDE.md"], {})
check("muro mezclado sin firma → bloquea", b and "CLAUDE.md" in t)

# commit vacío → permite
b, _ = g.bloquea([], {})
check("commit sin ficheros → permite", not b)

# una firma con valor distinto de 1 no vale
b, _ = g.bloquea(["CLAUDE.md"], {"BTP_MURO_OK": "0"})
check("BTP_MURO_OK=0 no cuenta como firma", b)

print("RESULTADO muro_hook: %d OK, %d fallos" % (6 - fallos, fallos))
print("✅ HOOK DEL MURO EN VERDE" if not fallos else "❌ revisar fallos")
sys.exit(0 if not fallos else 1)
