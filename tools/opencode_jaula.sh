#!/bin/bash
# opencode_jaula.sh — invoca OpenCode ENJAULADO contra el gateway del borde (F3, plan
# playful-crafting-pancake). La forma CANÓNICA de usar el orquestador abierto sin romper el muro.
#
# Triple cerrojo (validado 23/6/26, ver tools/F3-PILOTO-OPENCODE-BORRADOR.md):
#   1. env fregado (env -i) → CERO claves de nube heredadas.
#   2. HOME contenido + un único provider → su base_url es el gateway; sin otra vía a un modelo.
#   3. sandbox-exec solo-loopback → a nivel de SO solo puede hablar con 127.0.0.1 (→ el gateway → borde).
# El borde clasifica/bloquea lo sensible y sella cada llamada. Aunque OpenCode pida algo clínico o
# intente exfiltrar un canario, el borde lo corta y nada sale fuera.
#
# Uso:  tools/opencode_jaula.sh "tu mensaje no clínico"   [--model borde/local-ollama]
#       tools/opencode_jaula.sh --tui                      # abre la TUI enjaulada
set -uo pipefail
REPO="${BTP_REPO:-$HOME/claudecode}"
PORT="${BTP_GATEWAY_PORT:-8799}"
PY="$(command -v python3 || echo /usr/bin/python3)"
OC="$(command -v opencode || echo /opt/homebrew/bin/opencode)"
JAULA="$REPO/tools/state/f3_piloto"
HOMEJ="$JAULA/home"
SB="$JAULA/solo-loopback.sb"
TOKFILE="$REPO/tools/state/borde_gateway/token"

[ -x "$OC" ] || { echo "✗ OpenCode no instalado (brew install opencode)"; exit 3; }

mkdir -p "$HOMEJ/.config/opencode" "$HOMEJ/.local/share" "$HOMEJ/.cache" "$JAULA/work"

# 1) gateway vivo en loopback? si no, lo levanto (efímero; o ya lo tiene launchd) ───────────────
if ! lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "· gateway no escucha en $PORT → lo levanto…"
  mkdir -p "$REPO/tools/launchd/logs"
  ( cd "$REPO" && nohup "$PY" tools/borde_gateway.py serve --port "$PORT" \
      >"$REPO/tools/launchd/logs/borde-gateway.out" 2>&1 & )
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && break; sleep 0.5
  done
fi
[ -f "$TOKFILE" ] || "$PY" "$REPO/tools/borde_gateway.py" --token >/dev/null 2>&1
TOKEN="$(cat "$TOKFILE" 2>/dev/null)"
[ -n "$TOKEN" ] || { echo "✗ sin token del gateway ($TOKFILE)"; exit 4; }

# 2) (re)escribo la config de OpenCode con el token vivo + permisos DENY-BY-DEFAULT ─────────────
cat > "$HOMEJ/.config/opencode/opencode.json" <<JSON
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "borde": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Borde Polaris (gateway)",
      "options": { "baseURL": "http://127.0.0.1:$PORT/v1", "apiKey": "$TOKEN" },
      "models": {
        "local-ollama": { "name": "local-ollama (via borde)" },
        "nvidia-free":  { "name": "nvidia-free (via borde)" }
      }
    }
  },
  "model": "borde/local-ollama",
  "permission": { "bash": "ask", "edit": "ask", "webfetch": "deny" }
}
JSON

# 3) perfil sandbox: todo permitido salvo RED; la red SOLO a loopback ────────────────────────────
cat > "$SB" <<'SBPROF'
(version 1)
(allow default)
(deny network*)
(allow network* (remote ip "localhost:*"))
(allow network* (local ip "localhost:*"))
(allow network-bind (local ip "localhost:*"))
(allow network* (remote unix-socket))
SBPROF

# 4) lanzo OpenCode enjaulado (sandbox + env fregado) ───────────────────────────────────────────
args=("run"); [ "${1:-}" = "--tui" ] && { args=(); shift; }
cd "$JAULA/work"
exec sandbox-exec -f "$SB" \
  env -i PATH="/opt/homebrew/bin:/usr/bin:/bin" HOME="$HOMEJ" \
    XDG_CONFIG_HOME="$HOMEJ/.config" XDG_DATA_HOME="$HOMEJ/.local/share" \
    XDG_CACHE_HOME="$HOMEJ/.cache" TERM="${TERM:-xterm}" \
    "$OC" "${args[@]}" "$@"
