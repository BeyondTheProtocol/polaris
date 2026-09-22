#!/usr/bin/env python3
"""test_f1_opus_supervision.py — dos hardenings (17-jul-26):
  F1: gate del borde en el TRANSPORTE de nvidia.post (no-bypassable; clínico/PII nunca sale a NVIDIA).
  Opus-híbrido: excepción de supervisión en el muro — Task SOLO para verificacion/consejero-arquitectura
  en privileged, DENY para cualquier otro subagent_type y en cualquier otro perfil."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(ROOT, ".claude", "hooks", "muro_guard.py")
sys.path.insert(0, os.path.join(ROOT, "tools"))

ok = 0
fallos = []


def check(cond, msg):
    global ok
    if cond:
        ok += 1
    else:
        fallos.append(msg)


def guard(tool_name, tool_input, profile):
    """Corre muro_guard.py con un tool call; devuelve el exit code (0=permite, 2=deny)."""
    env = dict(os.environ, MURO_PROFILE=profile)
    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    r = subprocess.run([sys.executable, GUARD], input=payload, capture_output=True, text=True, env=env)
    return r.returncode


# ── Opus-híbrido: excepción de supervisión (Task allowlist) ──
check(guard("Task", {"subagent_type": "verificacion"}, "privileged") == 0,
      "Task/verificacion en privileged debe PERMITIR")
check(guard("Task", {"subagent_type": "consejero-arquitectura"}, "privileged") == 0,
      "Task/consejero-arquitectura en privileged debe PERMITIR")
check(guard("Task", {"subagent_type": "general-purpose"}, "privileged") == 2,
      "Task/general-purpose debe DENEGAR (fuera de la excepción)")
check(guard("Task", {"subagent_type": "tecnico"}, "privileged") == 2,
      "Task/tecnico debe DENEGAR (fuera de la excepción)")
check(guard("Task", {}, "privileged") == 2,
      "Task sin subagent_type debe DENEGAR (fail-closed)")
check(guard("Task", {"subagent_type": "verificacion"}, "quarantine") == 2,
      "Task/verificacion en cuarentena debe DENEGAR (solo privileged)")

# ── El mismo tool llega con DOS nombres: el harness manda "Agent", no "Task" (fix 25-jul-26).
# Sin esto la excepción de supervisión llevaba 12 reconfirmaciones muerta: el DENY genérico se
# comía a verificacion/consejero-arquitectura en privileged.
check(guard("Agent", {"subagent_type": "verificacion"}, "privileged") == 0,
      "Agent/verificacion en privileged debe PERMITIR (el harness manda 'Agent')")
check(guard("Agent", {"subagent_type": "consejero-arquitectura"}, "privileged") == 0,
      "Agent/consejero-arquitectura en privileged debe PERMITIR")
check(guard("Agent", {"subagent_type": "general-purpose"}, "privileged") == 2,
      "Agent/general-purpose debe DENEGAR (la puerta sigue siendo el subagent_type)")
check(guard("Agent", {}, "privileged") == 2,
      "Agent sin subagent_type debe DENEGAR (fail-closed)")
check(guard("Agent", {"subagent_type": "verificacion"}, "quarantine") == 2,
      "Agent/verificacion en cuarentena debe DENEGAR (solo privileged)")
# no reabre egress: WebFetch sigue denegado en privileged
check(guard("WebFetch", {}, "privileged") == 2, "WebFetch en privileged sigue DENY (no se reabrió egress)")

# ── F1: gate del borde en el transporte de nvidia.post ──
import nvidia
import borde

# clínico/PII → bloqueado ANTES de red (devuelve (None, "BORDE...")), sin tocar la url
d, e = nvidia.post(nvidia.CHAT_URL, "fakekey",
                   {"model": "x", "messages": [{"role": "user", "content": "el HLA-A*02:01 y rs121913529 de {{TITULAR}}"}]})
check(d is None and e and "BORDE" in e, "nvidia.post debe BLOQUEAR contenido clínico/PII (fue: %r)" % e)

# genómico crudo → bloqueado
d, e = nvidia.post(nvidia.CHAT_URL, "fakekey",
                   {"model": "x", "messages": [{"role": "user", "content": "chr17:43044295 A>G genotype 1/1"}]})
check(d is None and e and "BORDE" in e, "nvidia.post debe BLOQUEAR genómico crudo (fue: %r)" % e)

# público → clasificar NO lo marca sensible (pasaría el gate; el envío real requiere red/clave)
sens, _ = borde.clasificar("resume esta noticia pública de prensa sobre modelos de IA")
check(sens is False, "contenido público NO debe marcarse sensible (pasaría el gate)")

if fallos:
    print("❌ F1_OPUS_SUPERVISION: %d OK, %d FALLOS" % (ok, len(fallos)))
    for f in fallos:
        print("   - " + f)
    sys.exit(1)
print("✅ F1_OPUS_SUPERVISION EN VERDE (%d ok / 0 fallos)" % ok)
