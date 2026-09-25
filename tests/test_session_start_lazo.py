#!/usr/bin/env python3
"""test_session_start_lazo.py — el lazo 24/7 no lanza el drenaje de reels (sesión de IG).

POR QUÉ EXISTE (25-sep-2026). `session_start.sh` decía que el lazo tenía SessionStart vacío y que la
sesión de Instagram solo se usaba en sesiones atendidas. Era falso: `claude -p` también carga los
hooks del `.claude/settings.json` del proyecto, y en vivo (Claude Code 2.1.236, settings.autonomous
del lazo) el hook se ejecutó y creó la marca del drenaje. Se destapó al comprobar el canario del muro
(P9, idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026).

Qué se fija aquí, con un `reel_digest.py` falso que deja marca si lo lanzan:
  1. con BTP_AGENT_DEPTH (lo exporta run_agent.sh) → ni marca de tiempo ni drenaje;
  2. sin ella (sesión interactiva) → drenaje, como siempre;
  3. el hook sale 0 en los dos casos.
"""
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, ".claude", "hooks", "session_start.sh")
fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def correr(lazo):
    d = tempfile.mkdtemp(prefix="ss_lazo_")
    repo, tmp = os.path.join(d, "repo"), os.path.join(d, "tmp")
    os.makedirs(os.path.join(repo, "tools"))
    os.makedirs(tmp)
    marca = os.path.join(d, "drenado")
    open(os.path.join(repo, "tools", "reel_digest.py"), "w").write(
        "open(%r, 'w').write('si')\n" % marca)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BTP_", "MURO_"))}
    env.update(BTP_REPO=repo, TMPDIR=tmp + "/", BTP_HOSTNAME="Polaris")
    if lazo:
        env["BTP_AGENT_DEPTH"] = "1"
    p = subprocess.run(["bash", HOOK], input="{}", capture_output=True, text=True, env=env, timeout=60)
    for _ in range(50):                      # el drenaje va detachado: se le da hasta 5 s
        if os.path.exists(marca):
            break
        time.sleep(0.1)
    return p.returncode, os.path.exists(marca), os.path.exists(os.path.join(tmp, ".btp_reel_drain.stamp"))


print("1. en el lazo (BTP_AGENT_DEPTH) → no drena")
rc, drenado, stamp = correr(lazo=True)
check(rc == 0, "exit 0")
check(not drenado, "no lanza reel_digest")
check(not stamp, "no deja la marca de tiempo")

print("2. sesión interactiva → drena como siempre")
rc, drenado, stamp = correr(lazo=False)
check(rc == 0, "exit 0")
check(drenado, "lanza reel_digest")
check(stamp, "deja la marca de tiempo")

print("\n%s" % ("🔴 %d fallo(s)" % len(fallos) if fallos else "✅ session_start sin IG en el lazo: todo en verde"))
sys.exit(1 if fallos else 0)
