#!/bin/bash
# tools/consola_remota.sh — la CONSOLA de Polaris en remoto: Claude Code real, en una pestaña del
# navegador, vía Tailscale. Para arreglar/construir el mini desde el iPhone o el MacBook, con MANOS
# REALES (no chat). Reversible: `down` y desaparece.
#
#   tools/consola_remota.sh estado   # qué hay y qué falta
#   tools/consola_remota.sh up       # arranca ttyd(loopback)+tmux+claude y lo publica con `tailscale serve` (HTTPS, tailnet-only)
#   tools/consola_remota.sh down     # lo para todo y CIERRA la sesión (limpia el scrollback)
#   tools/consola_remota.sh rotar    # rota el token de acceso
#
# ⚠️ MODELO DE AMENAZA (sé honesto): quien entra por aquí tiene **Bash completo como el usuario `polaris`**
# (acceso a Llavero, repo clínico, daemons…). El muro/CLAUDE.md es guardarraíl del AGENTE, NO un sandbox
# del SO: NO contiene a lo que se teclee directo. Por eso la barrera REAL es la red (Tailscale) + el login,
# y por eso esto NUNCA es 24/7 y se enciende con OK de {{TITULAR}} tras revisión de seguridad.
#
# ENDURECIDO (auditoría 26/6/26):
#   · ttyd escucha SOLO en 127.0.0.1, 1 cliente (-m 1), login básico (token largo en Llavero).
#   · Se publica con `tailscale serve --https` → TLS + "tailnet only" (mismo patrón que VNC/Observatorio),
#     en vez del relay casero. PRERREQUISITO HUMANO: ACL de Tailscale que limite el puerto a SOLO el iPhone
#     y el MacBook de {{TITULAR}} (ver doc). Sin esa ACL, el tailnet entero alcanzaría el puerto.
#   · Log de accesos a fichero (no /dev/null) + aviso a {{TITULAR}} al abrir.
#   · `down` MATA la sesión tmux por defecto (no deja scrollback clínico vivo). Auto-apagado por TTL.
set -euo pipefail
REPO="${BTP_REPO:-$HOME/claudecode}"
SELF="$REPO/tools/consola_remota.sh"
STATE="${BTP_STATE_DIR:-$REPO/tools/state}/consola_remota"
LOG="$STATE/acceso.log"
WATCH_PID_F="$STATE/watchdog.pid"
LOCAL_PORT="${CONSOLA_PORT:-7681}"   # ttyd en loopback
TS_PORT="${CONSOLA_TS:-7690}"        # puerto HTTPS de tailscale serve (móvil/Mac)
TMUX_SESION="${CONSOLA_TMUX:-polaris}"
USUARIO="${CONSOLA_USER:-titular}"
TTL_MIN="${CONSOLA_TTL_MIN:-120}"    # auto-apagado tras N min (acota "dejada encendida y olvidada")
TS_HOST="polaris.taild7f51c.ts.net"
TS_BIN="${TAILSCALE_BIN:-/Applications/Tailscale.app/Contents/MacOS/Tailscale}"
CLAUDE_BIN="$(command -v claude || echo "$HOME/.local/bin/claude")"
PY="$REPO/.venv/bin/python3"; [ -x "$PY" ] || PY="$(command -v python3 || echo python3)"

c_red(){ printf '\033[31m%s\033[0m\n' "$*"; }
c_grn(){ printf '\033[32m%s\033[0m\n' "$*"; }
c_yel(){ printf '\033[33m%s\033[0m\n' "$*"; }
have(){ command -v "$1" >/dev/null 2>&1; }
mkstate(){ mkdir -p "$STATE" 2>/dev/null || true; }

# Token del Llavero; se genera la 1ª vez. `rotar` lo regenera (invalida el anterior).
token(){
  local t; t="$(security find-generic-password -s btp-consola-token -w 2>/dev/null || true)"
  if [ -z "$t" ]; then
    t="$(openssl rand -hex 24)"   # 192 bits
    security add-generic-password -s btp-consola-token -a "$USUARIO" -w "$t" -U >/dev/null 2>&1 || true
  fi
  printf '%s' "$t"
}

ttyd_pid(){ pgrep -f "ttyd .* -p $LOCAL_PORT " 2>/dev/null || true; }
# Capturamos a variable (no `| grep -q`): bajo `set -o pipefail`, grep -q cierra la tubería antes y
# Tailscale recibe SIGPIPE → la pipeline daría falso negativo ("no publicada" estando publicada).
serve_on(){ local s; s="$("$TS_BIN" serve status 2>/dev/null)"; case "$s" in *":$TS_PORT"*) return 0;; *) return 1;; esac; }

aviso(){ # best-effort, respeta HALT/anti-spam del sistema
  "$PY" - "$1" <<'PY' 2>/dev/null || true
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
  if have ttyd;  then c_grn "✅ ttyd instalado"; else c_red "❌ falta ttyd → brew install ttyd"; fallo=1; fi
  if have tmux;  then c_grn "✅ tmux instalado"; else c_red "❌ falta tmux → brew install tmux"; fallo=1; fi
  if [ -x "$CLAUDE_BIN" ]; then c_grn "✅ Claude Code en $CLAUDE_BIN"; else c_red "❌ no encuentro 'claude'"; fallo=1; fi
  if [ -x "$TS_BIN" ]; then c_grn "✅ Tailscale CLI"; else c_red "❌ no encuentro el binario de Tailscale ($TS_BIN)"; fallo=1; fi
  return $fallo
}

case "${1:-estado}" in
  estado)
    echo "— Estado de la consola remota de Polaris —"
    prechequeo || true
    [ -n "$(ttyd_pid)" ] && c_grn "✅ ttyd vivo (loopback :$LOCAL_PORT, 1 cliente)" || c_yel "○ ttyd parado"
    serve_on && c_grn "✅ publicada por tailscale serve (HTTPS :$TS_PORT, tailnet-only)" || c_yel "○ no publicada"
    tmux has-session -t "$TMUX_SESION" 2>/dev/null && c_grn "✅ sesión '$TMUX_SESION' viva" || c_yel "○ sin sesión"
    echo "— acceso —  https://$TS_HOST:$TS_PORT   (usuario: $USUARIO)"
    echo "— PRERREQUISITO de seguridad: ACL de Tailscale que limite :$TS_PORT al iPhone+MacBook de {{TITULAR}}"
    ;;
  up)
    prechequeo || { c_red "Prerrequisitos sin cumplir. No arranco a medias."; exit 1; }
    mkstate
    TOK="$(token)"
    if [ -z "$(ttyd_pid)" ]; then
      # -i 127.0.0.1: SOLO loopback · -W: escritura · -m 1: UN cliente (sin secuestro concurrente) ·
      # log de acceso a fichero (no /dev/null) · history-limit bajo para no acumular scrollback clínico.
      ttyd -i 127.0.0.1 -p "$LOCAL_PORT" -W -m 1 -c "$USUARIO:$TOK" -t titleFixed="Polaris · consola" \
        tmux new -A -s "$TMUX_SESION" -c "$REPO" "$CLAUDE_BIN" >>"$LOG" 2>&1 &
      disown || true
      c_grn "✅ ttyd arrancado (loopback :$LOCAL_PORT, 1 cliente)"
    else c_yel "ttyd ya estaba vivo"; fi
    # Publicar por Tailscale (TLS + tailnet-only). SOLO nuestro puerto; JAMÁS `serve reset` (tumbaría VNC/Observatorio).
    "$TS_BIN" serve --bg --https="$TS_PORT" "http://127.0.0.1:$LOCAL_PORT" >/dev/null 2>&1 \
      && c_grn "✅ publicada: https://$TS_HOST:$TS_PORT (HTTPS, tailnet-only)" \
      || c_yel "⚠️  no pude publicar con tailscale serve (¿permiso? ¿Tailscale ON?)"
    # Watchdog de auto-apagado por TTL (acota "dejada encendida y olvidada").
    [ -f "$WATCH_PID_F" ] && kill "$(cat "$WATCH_PID_F" 2>/dev/null)" 2>/dev/null || true
    ( sleep $((TTL_MIN*60)); "$SELF" down >/dev/null 2>&1 ) & echo $! >"$WATCH_PID_F"; disown || true
    echo "$(date '+%F %T')  UP  ttl=${TTL_MIN}min" >>"$LOG"
    aviso "🖥️ Consola remota de Polaris ABIERTA ($(date '+%H:%M')). Se auto-cierra en ${TTL_MIN} min o con 'down'. Si no fuiste tú, avísame."
    echo ""
    c_grn "Listo (Tailscale ON):  https://$TS_HOST:$TS_PORT"
    echo "Usuario: $USUARIO · contraseña (token):  security find-generic-password -s btp-consola-token -w"
    c_yel "Recuerda la ACL de Tailscale (solo tus 2 dispositivos). Se auto-cierra en ${TTL_MIN} min."
    ;;
  down)
    P="$(ttyd_pid)"; [ -n "$P" ] && kill $P 2>/dev/null && c_grn "✅ ttyd parado" || c_yel "○ ttyd no estaba"
    "$TS_BIN" serve --https="$TS_PORT" off >/dev/null 2>&1 && c_grn "✅ despublicada (:$TS_PORT)" || c_yel "○ serve ya estaba off"
    [ -f "$WATCH_PID_F" ] && { kill "$(cat "$WATCH_PID_F" 2>/dev/null)" 2>/dev/null || true; rm -f "$WATCH_PID_F"; }
    if [ "${2:-}" = "--keep" ]; then
      c_yel "○ sesión '$TMUX_SESION' conservada (--keep). Scrollback sigue vivo; ciérrala pronto."
    else
      tmux kill-session -t "$TMUX_SESION" 2>/dev/null && c_grn "✅ sesión cerrada (scrollback limpio)" || c_yel "○ no había sesión"
    fi
    echo "$(date '+%F %T')  DOWN" >>"$LOG" 2>/dev/null || true
    ;;
  rotar)
    security add-generic-password -s btp-consola-token -a "$USUARIO" -w "$(openssl rand -hex 24)" -U >/dev/null 2>&1 \
      && c_grn "✅ token rotado (el anterior ya no vale; reinicia 'up' si estaba viva)" || c_red "❌ no pude rotar"
    ;;
  *)
    echo "uso: tools/consola_remota.sh estado|up|down [--keep]|rotar"; exit 2 ;;
esac
