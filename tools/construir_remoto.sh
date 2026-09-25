#!/bin/bash
# tools/construir_remoto.sh — el MODO CONSTRUIR de Polaris en remoto: OpenCode (agente que ACTÚA
# sobre tu repo real) con UI web, en el navegador del móvil/Mac, vía Tailscale. La hermana de
# consola_remota.sh, pero con la UI bonita de OpenCode en vez de una terminal.
#
#   tools/construir_remoto.sh estado   # qué hay y qué falta
#   tools/construir_remoto.sh up       # arranca opencode web (loopback) y lo publica con `tailscale serve`
#   tools/construir_remoto.sh down     # lo para todo (opencode + la publicación)
#   tools/construir_remoto.sh rotar    # rota la contraseña de acceso
#
# ⚠️ MODELO DE AMENAZA (sé honesto): el agente actúa sobre tu Polaris real con permisos «ask»
# (te pide cada bash/edit), pero quien entra opera tu sistema. El cerebro va por el BORDE
# (gateway → muro sella el egress; carril NO clínico). La barrera real es la red (Tailscale,
# tu tailnet) + el login. NUNCA 24/7: se enciende con OK de {{TITULAR}} y se auto-apaga.
set -uo pipefail
REPO="${BTP_REPO:-$HOME/claudecode}"
SELF="$REPO/tools/construir_remoto.sh"
STATE="${BTP_STATE_DIR:-$REPO/tools/state}/construir"
LOG="$STATE/logs/web.out"
CONFIG="$STATE/opencode.json"
WATCH_PID_F="$STATE/watchdog.pid"
LOCAL_PORT="${CONSTRUIR_PORT:-7682}"     # opencode web en loopback
TS_PORT="${CONSTRUIR_TS:-7692}"          # puerto HTTPS de tailscale serve
USUARIO="opencode"                        # usuario fijo del Basic auth de OpenCode
TTL_MIN="${CONSTRUIR_TTL_MIN:-120}"
TS_HOST="polaris.taild7f51c.ts.net"
TS_BIN="${TAILSCALE_BIN:-/Applications/Tailscale.app/Contents/MacOS/Tailscale}"
OC="$(command -v opencode || echo /opt/homebrew/bin/opencode)"
PY="$REPO/.venv/bin/python3"; [ -x "$PY" ] || PY="$(command -v python3 || echo python3)"
GW_PORT="${BTP_GATEWAY_PORT:-8799}"
GW_TOKFILE="$REPO/tools/state/borde_gateway/token"
MODELO="${CONSTRUIR_MODEL:-borde/polaris-construir}"

c_red(){ printf '\033[31m%s\033[0m\n' "$*"; }
c_grn(){ printf '\033[32m%s\033[0m\n' "$*"; }
c_yel(){ printf '\033[33m%s\033[0m\n' "$*"; }
have(){ command -v "$1" >/dev/null 2>&1; }
oc_pid(){ pgrep -f "opencode web --port $LOCAL_PORT" 2>/dev/null || true; }
serve_on(){ local s; s="$("$TS_BIN" serve status 2>/dev/null)"; case "$s" in *":$TS_PORT"*) return 0;; *) return 1;; esac; }

# Contraseña del Llavero; se genera la 1ª vez. `rotar` la regenera.
password(){
  local t; t="$(security find-generic-password -s btp-construir-token -w 2>/dev/null || true)"
  if [ -z "$t" ]; then
    t="$(openssl rand -hex 20)"
    security add-generic-password -s btp-construir-token -a "$USUARIO" -w "$t" -U >/dev/null 2>&1 || true
  fi
  printf '%s' "$t"
}

# Token del gateway (cerebro por el borde). Si no hay, lo pide al gateway.
gw_token(){
  [ -f "$GW_TOKFILE" ] || "$PY" "$REPO/tools/borde_gateway.py" --token >/dev/null 2>&1 || true
  cat "$GW_TOKFILE" 2>/dev/null
}

# (re)escribe la config de OpenCode: cerebro por el borde, acciones PIDEN permiso, sin webfetch.
escribe_config(){
  local tok; tok="$(gw_token)"
  [ -n "$tok" ] || { c_red "✗ sin token del gateway ($GW_TOKFILE) — ¿borde vivo?"; return 1; }
  mkdir -p "$STATE/logs"
  cat > "$CONFIG" <<JSON
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "borde": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Borde Polaris (gateway, muro)",
      "options": { "baseURL": "http://127.0.0.1:$GW_PORT/v1", "apiKey": "$tok" },
      "models": { "polaris-construir": { "name": "Polaris · Construir" } }
    }
  },
  "model": "$MODELO",
  "permission": { "bash": "ask", "edit": "ask", "webfetch": "deny" }
}
JSON
  chmod 600 "$CONFIG"
}

aviso(){ "$PY" - "$1" <<'PY' 2>/dev/null || true
import sys, os
sys.path.insert(0, os.path.join(os.environ.get("BTP_REPO", os.path.expanduser("~/claudecode")), "tools"))
try:
    import salida; salida.report_to_titular(sys.argv[1], urgente=False)
except Exception:
    pass
PY
}

prechequeo(){
  local fallo=0
  if [ -x "$OC" ]; then c_grn "✅ OpenCode en $OC"; else c_red "❌ no encuentro 'opencode' (brew install opencode)"; fallo=1; fi
  if [ -x "$TS_BIN" ]; then c_grn "✅ Tailscale CLI"; else c_red "❌ no encuentro Tailscale ($TS_BIN)"; fallo=1; fi
  if lsof -nP -iTCP:"$GW_PORT" -sTCP:LISTEN >/dev/null 2>&1; then c_grn "✅ gateway del borde vivo (:$GW_PORT)"; else c_yel "○ gateway no escucha en :$GW_PORT (lo levanta la jaula/launchd)"; fi
  return $fallo
}

case "${1:-estado}" in
  estado)
    echo "— Estado del modo Construir remoto —"
    prechequeo || true
    [ -n "$(oc_pid)" ] && c_grn "✅ opencode web vivo (loopback :$LOCAL_PORT)" || c_yel "○ opencode web parado"
    serve_on && c_grn "✅ publicado por tailscale serve (HTTPS :$TS_PORT, tailnet-only)" || c_yel "○ no publicado"
    echo "— acceso —  https://$TS_HOST:$TS_PORT   (usuario: $USUARIO)"
    echo "— contraseña:  security find-generic-password -s btp-construir-token -w"
    ;;
  up)
    prechequeo || { c_red "Prerrequisitos sin cumplir. No arranco a medias."; exit 1; }
    escribe_config || exit 1
    PW="$(password)"
    if [ -z "$(oc_pid)" ]; then
      OPENCODE_CONFIG="$CONFIG" OPENCODE_SERVER_PASSWORD="$PW" \
        nohup "$OC" web --port "$LOCAL_PORT" --hostname 127.0.0.1 >>"$LOG" 2>&1 &
      disown || true
      # esperar a que escuche
      for _ in 1 2 3 4 5 6 7 8 9 10; do lsof -nP -iTCP:"$LOCAL_PORT" -sTCP:LISTEN >/dev/null 2>&1 && break; sleep 0.6; done
      c_grn "✅ opencode web arrancado (loopback :$LOCAL_PORT, repo=$REPO)"
    else c_yel "opencode web ya estaba vivo"; fi
    "$TS_BIN" serve --bg --https="$TS_PORT" "http://127.0.0.1:$LOCAL_PORT" >/dev/null 2>&1 \
      && c_grn "✅ publicado: https://$TS_HOST:$TS_PORT (HTTPS, tailnet-only)" \
      || c_yel "⚠️  no pude publicar con tailscale serve (¿permiso? ¿Tailscale ON?)"
    [ -f "$WATCH_PID_F" ] && kill "$(cat "$WATCH_PID_F" 2>/dev/null)" 2>/dev/null || true
    ( sleep $((TTL_MIN*60)); "$SELF" down >/dev/null 2>&1 ) & echo $! >"$WATCH_PID_F"; disown || true
    aviso "🏗️ Modo Construir ABIERTO ($(date '+%H:%M')) en https://$TS_HOST:$TS_PORT (usuario opencode). Se auto-cierra en ${TTL_MIN} min o con 'down'. Si no fuiste tú, avísame."
    echo ""
    c_grn "Listo (Tailscale ON):  https://$TS_HOST:$TS_PORT   ·  usuario: $USUARIO"
    echo "Contraseña:  security find-generic-password -s btp-construir-token -w"
    c_yel "Cerebro: $MODELO (no clínico, egress sellado por el borde) · acciones con permiso · auto-apagado ${TTL_MIN} min."
    ;;
  down)
    P="$(oc_pid)"; [ -n "$P" ] && kill $P 2>/dev/null && c_grn "✅ opencode web parado" || c_yel "○ no estaba vivo"
    "$TS_BIN" serve --https="$TS_PORT" off >/dev/null 2>&1 && c_grn "✅ despublicado (:$TS_PORT)" || c_yel "○ serve ya estaba off"
    [ -f "$WATCH_PID_F" ] && { kill "$(cat "$WATCH_PID_F" 2>/dev/null)" 2>/dev/null || true; rm -f "$WATCH_PID_F"; }
    ;;
  rotar)
    security add-generic-password -s btp-construir-token -a "$USUARIO" -w "$(openssl rand -hex 20)" -U >/dev/null 2>&1 \
      && c_grn "✅ contraseña rotada (reinicia 'up' si estaba viva)" || c_red "❌ no pude rotar"
    ;;
  *)
    echo "uso: tools/construir_remoto.sh estado|up|down|rotar"; exit 2 ;;
esac
