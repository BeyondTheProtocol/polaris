#!/bin/bash
# canario_preflight.sh <claude> <settings.json> [<dir_testigo>] — ¿carga ESTE binario los hooks de ESE
# settings? Sale 0 si el hook SessionStart .claude/hooks/canario_muro.sh dejó su testigo; 1 si no.
# Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.
#
# Lo usan run_agent.sh (antes de cada versión nueva, P9) y claude_lazo.py (antes de subir la versión
# fijada del lazo). Clave inválida a propósito: el hook corre al arrancar la sesión, la llamada al
# modelo falla sin coste (verificado 25-sep con 2.1.236: total_cost_usd 0) y no esperamos a sus
# reintentos: en cuanto aparece el testigo, se corta el CLI. Se ejecuta desde el directorio actual
# (el repo), igual que el lazo.
#   BTP_CANARIO_ESPERA — segundos máximos esperando el testigo (def. 60).
set -u
BIN="${1:?uso: canario_preflight.sh <claude> <settings> [dir]}"
SET="${2:?uso: canario_preflight.sh <claude> <settings> [dir]}"
DIR="${3:-${TMPDIR:-/tmp}}"
mkdir -p "$DIR" 2>/dev/null || true
T="$DIR/pre.$$.testigo"; N="pre-$$-$RANDOM$RANDOM"; rm -f "$T"
ok() { [ -f "$T" ] && [ "$(head -1 "$T" 2>/dev/null)" = "$N" ]; }
# exec → el PID es el del CLI y el kill de abajo lo corta.
( exec env -u CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY="sk-ant-canario-sin-credito" \
    BTP_CANARIO_TESTIGO="$T" BTP_CANARIO_NONCE="$N" \
    "$BIN" -p "canario del muro" --settings "$SET" --permission-mode acceptEdits \
    --output-format json --max-turns 1 --model haiku </dev/null >/dev/null 2>&1 ) &
PID=$!
PASOS=$(( ${BTP_CANARIO_ESPERA:-60} * 5 ))   # en pasos de 0,2 s
while [ "$PASOS" -gt 0 ]; do
  ok && break
  kill -0 "$PID" 2>/dev/null || { sleep 0.2; break; }
  sleep 0.2; PASOS=$((PASOS - 1))
done
kill "$PID" 2>/dev/null || true; wait "$PID" 2>/dev/null || true
if ok; then rm -f "$T"; exit 0; fi
rm -f "$T" 2>/dev/null || true
exit 1
