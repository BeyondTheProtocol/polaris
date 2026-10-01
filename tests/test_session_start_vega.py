#!/usr/bin/env python3
"""test_session_start_vega.py — toda sesión interactiva arranca viendo lo que ve Vega.

POR QUÉ (1-oct-2026, {{TITULAR}}: «quiero que Vega sea la orquestadora de todo»). Las sesiones de Claude
Code se saltaban a Vega: no veían su visión (promesas vencidas, incongruencias, atascos, buzón).
Con un `vega_vision.py` falso se fija:
  1. sesión interactiva → la visión entra en el additionalContext, con el aviso «VEGA ORQUESTA»;
  2. job del lazo (BTP_AGENT_DEPTH) → no entra (Vega la recibe en su propia sesión);
  3. si vega_vision.py falla, el hook sigue saliendo 0 (fail-open).
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, ".claude", "hooks", "session_start.sh")
fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def correr(lazo, vision_ok=True):
    d = tempfile.mkdtemp(prefix="ss_vega_")
    repo = os.path.join(d, "repo")
    os.makedirs(os.path.join(repo, "tools"))
    cuerpo = "print('== VISION FALSA DE VEGA ==')\n" if vision_ok else "raise SystemExit(3)\n"
    open(os.path.join(repo, "tools", "vega_vision.py"), "w").write(cuerpo)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BTP_", "MURO_"))}
    env.update(BTP_REPO=repo, TMPDIR=d, BTP_HOSTNAME="Polaris")
    if lazo:
        env["BTP_AGENT_DEPTH"] = "1"
    p = subprocess.run(["bash", HOOK], input="", capture_output=True, text=True, env=env, timeout=60)
    ctx = ""
    if p.stdout.strip():
        try:
            ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
        except Exception:  # noqa: BLE001
            ctx = p.stdout
    return p.returncode, ctx


rc, ctx = correr(lazo=False)
check(rc == 0, "sesión interactiva: el hook sale 0")
check("VISION FALSA DE VEGA" in ctx, "sesión interactiva: la visión de Vega entra en el contexto")
check("VEGA ORQUESTA" in ctx, "sesión interactiva: lleva el aviso de que la sesión es un brazo de Vega")

rc, ctx = correr(lazo=True)
check(rc == 0, "lazo: el hook sale 0")
check("VISION FALSA DE VEGA" not in ctx, "lazo: la visión no se duplica en los jobs")

rc, ctx = correr(lazo=False, vision_ok=False)
check(rc == 0, "visión rota: el hook sigue saliendo 0 (fail-open)")
check("VEGA ORQUESTA" not in ctx, "visión rota: no se inyecta un aviso vacío")

print("\n" + ("✅ SESSION_START VEGA EN VERDE" if not fallos else "❌ %d fallo(s)" % len(fallos)))
sys.exit(1 if fallos else 0)
