#!/usr/bin/env python3
"""test_canario_muro.py — si Claude Code deja de cargar los hooks del muro, el lazo falla CERRADO.

POR QUÉ EXISTE (P9, 25-sep-2026). Idea de {{CONTACTO}} {{CONTACTO}} (https://contacto.com), con su agente KAI,
revisión del 25-sep-2026. La doc oficial de Claude Code (https://code.claude.com/docs/en/headless)
dice que `--bare` «will become the default for -p in a future release», y `--bare` no carga hooks:
el muro_guard PreToolUse desaparecería sin error y la rutina correría sin muro, en silencio.
Hecho cuando: un cambio en `-p` hace fallar el canario, no la rutina en silencio.

Qué se fija aquí (`run_agent.sh` + `.claude/hooks/canario_muro.sh`), con un `claude` falso:
  1. hooks cargados → el job corre, el OK del preflight se cachea y no se repite;
  2. cambia la versión del CLI → el preflight se repite;
  3. el preflight no deja testigo (lo que haría `--bare` por defecto) → código rojo, exit 1, y el
     run real NO llega a lanzarse;
  4. el run real no deja testigo y el modelo trabajó → código rojo y OK invalidado;
  5. el run real no deja testigo y no trabajó → exit 1 sin código rojo, OK invalidado;
  6. el hook sin su variable no hace nada ni escribe a stdout (sesiones interactivas intactas);
  7. todos los settings.<perfil>.json del lazo llevan el canario en SessionStart.
Nunca toca el código rojo real: BTP_CODIGO_ROJO apunta a un espía.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="canario_")
HOOK = os.path.join(ROOT, ".claude", "hooks", "canario_muro.sh")
fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


# `claude` falso. Distingue el preflight (clave del canario) del run real, y según FAKE_PRE /
# FAKE_RUN ejecuta o no el hook de verdad (simula hooks cargados o `--bare`). Cuenta sus llamadas.
FALSO = os.path.join(TMP, "claude_falso.sh")
open(FALSO, "w").write(r"""#!/bin/bash
if [ "${1:-}" = "--version" ]; then echo "${FAKE_VERSION:-9.9.9} (Claude Code)"; exit 0; fi
if [ "${ANTHROPIC_API_KEY:-}" = "sk-ant-canario-sin-credito" ]; then
  echo pre >> "$FAKE_LOG"
  [ "${FAKE_PRE:-1}" = 1 ] && echo '{}' | "$FAKE_HOOK"
  echo '{"is_error":true,"num_turns":1,"total_cost_usd":0,"usage":{"output_tokens":0},"result":"Invalid API key"}'
  exit 1
fi
echo run >> "$FAKE_LOG"
[ "${FAKE_RUN:-1}" = 1 ] && echo '{}' | "$FAKE_HOOK"
echo "{\"is_error\":false,\"num_turns\":3,\"total_cost_usd\":${FAKE_COSTE:-0.01},\"usage\":{\"output_tokens\":${FAKE_TOKENS:-50}},\"result\":\"ok\"}"
""")
os.chmod(FALSO, 0o755)

ESPIA = os.path.join(TMP, "rojo_espia.py")
open(ESPIA, "w").write("import sys\nopen(%r,'a').write(' | '.join(sys.argv[1:])+'\\n')\n"
                       % os.path.join(TMP, "rojo.log"))


def correr(nombre, **fake):
    st = os.path.join(TMP, "state")
    log = os.path.join(TMP, nombre + ".log")
    env = {k: v for k, v in os.environ.items() if not k.startswith("BTP_") and k != "MURO_PROFILE"}
    env.update(BTP_CLAUDE_BIN=FALSO, BTP_CANARIO="1", BTP_API_KEY_OVERRIDE="x", BTP_COST_GUARDED="1",
               BTP_STATE_DIR=st, BTP_AGENT="tecnico", BTP_MODEL="sonnet", BTP_REPO=ROOT,
               BTP_CASA_GIT=os.path.join(TMP, "no-casa"), BTP_DEUDA_OFF="1", BTP_OTROS_AGENTES="0",
               BTP_CODIGO_ROJO=ESPIA, BTP_CANARIO_ESPERA="10",
               BTP_HALT_FILES=os.path.join(TMP, "nh_a") + ":" + os.path.join(TMP, "nh_b"),
               FAKE_LOG=log, FAKE_HOOK=HOOK)
    env.update({k: str(v) for k, v in fake.items()})
    p = subprocess.run(["bash", os.path.join(ROOT, "tools", "run_agent.sh"), "haz algo"],
                       capture_output=True, text=True, env=env, timeout=120)
    llamadas = open(log).read().split() if os.path.exists(log) else []
    return p, llamadas


def rojos():
    p = os.path.join(TMP, "rojo.log")
    return open(p).read().splitlines() if os.path.exists(p) else []


def stamps():
    d = os.path.join(TMP, "state", "canario")
    return [f for f in os.listdir(d) if f.startswith("ok-")] if os.path.isdir(d) else []


def ultimo_json(out):
    for linea in reversed(out.strip().splitlines()):
        try:
            return json.loads(linea)
        except Exception:
            continue
    return {}


print("1. hooks cargados → corre y cachea el OK")
p, ll = correr("c1")
check(p.returncode == 0, "exit 0 (rc=%s, err=%s)" % (p.returncode, p.stderr[-300:]))
check(ll == ["pre", "run"], "preflight y luego el run (%s)" % ll)
check(len(stamps()) == 1, "OK del preflight cacheado")
check(not rojos(), "sin código rojo")

print("2. misma versión → no repite el preflight")
p, ll = correr("c2")
check(p.returncode == 0 and ll == ["run"], "solo el run (%s, rc=%s)" % (ll, p.returncode))

print("3. cambia la versión del CLI → repite el preflight")
p, ll = correr("c3", FAKE_VERSION="9.9.10")
check(ll == ["pre", "run"], "preflight de nuevo (%s)" % ll)
check(len(stamps()) == 1, "un solo OK vivo (el viejo se borra)")

print("4. el preflight no carga hooks (como --bare por defecto) → falla CERRADO")
p, ll = correr("c4", FAKE_VERSION="10.0.0", FAKE_PRE="0")
j = ultimo_json(p.stdout)
check(p.returncode == 1, "exit 1 (rc=%s)" % p.returncode)
check(ll == ["pre"], "el run real NO se lanza (%s)" % ll)
check(j.get("is_error") is True and "canario" in j.get("result", ""), "JSON de error del canario")
r = rojos()
check(len(r) == 1 and r[0].startswith("trigger | canario del muro"), "código rojo disparado (%s)" % r)
check(len(stamps()) == 1, "no cachea OK para la versión rota (solo queda el de la anterior)")

print("5. el run no deja testigo y el modelo trabajó → código rojo, OK invalidado")
p, ll = correr("c5", FAKE_VERSION="10.0.1", FAKE_RUN="0")
check(p.returncode == 1 and ll == ["pre", "run"], "exit 1 tras el run (%s, rc=%s)" % (ll, p.returncode))
check(len(rojos()) == 2 and "trabajó SIN dejar testigo" in rojos()[-1], "código rojo por run sin muro")
check(not stamps(), "OK invalidado")

print("6. el run no deja testigo y no trabajó → exit 1 sin código rojo")
p, ll = correr("c6", FAKE_VERSION="10.0.2", FAKE_RUN="0", FAKE_COSTE="0", FAKE_TOKENS="0")
check(p.returncode == 1 and ll == ["pre", "run"], "falla cerrado (%s, rc=%s)" % (ll, p.returncode))
check(len(rojos()) == 2, "no añade código rojo")
check(not stamps(), "OK invalidado: el siguiente repite el preflight")

print("7. apagado con binario de test sin BTP_CANARIO (los tests ajenos no cambian)")
p, ll = correr("c7", BTP_CANARIO="0", FAKE_RUN="0")
check(p.returncode == 0 and ll == ["run"], "corre sin canario (%s, rc=%s)" % (ll, p.returncode))

print("8. el hook sin su variable no hace nada")
env = {k: v for k, v in os.environ.items() if not k.startswith("BTP_CANARIO")}
h = subprocess.run([HOOK], input="{}", capture_output=True, text=True, env=env, timeout=10)
check(h.returncode == 0 and h.stdout == "", "exit 0 y stdout vacío")

print("9. los settings del lazo llevan el canario en SessionStart")
# Todos los settings.<perfil>.json: run_agent.sh admite cualquiera por BTP_SETTINGS. Fuera solo
# settings.json y settings.local.json, que son de las sesiones interactivas.
import glob
perfiles = sorted(os.path.basename(p)[9:-5] for p in glob.glob(os.path.join(ROOT, ".claude", "settings.*.json"))
                  if os.path.basename(p) != "settings.local.json")
check(len(perfiles) >= 4, "hay perfiles del lazo que revisar (%s)" % perfiles)
for f in perfiles:
    d = json.load(open(os.path.join(ROOT, ".claude", "settings.%s.json" % f)))
    cmds = [x.get("command", "") for g in d.get("hooks", {}).get("SessionStart", []) for x in g.get("hooks", [])]
    check(any(c.endswith("/.claude/hooks/canario_muro.sh") for c in cmds), "settings.%s.json" % f)
check(os.access(HOOK, os.X_OK), "hook ejecutable")

print("\n%s" % ("🔴 %d fallo(s)" % len(fallos) if fallos else "✅ canario del muro: todo en verde"))
sys.exit(1 if fallos else 0)
