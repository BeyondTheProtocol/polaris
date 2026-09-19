#!/bin/bash
# btp_run.sh — interruptor del lazo 24/7 (P1). install | start | stop | status | seen.
#
# ⚠️ ENCENDER (start) cambia el estado de la máquina → es GATE de {{TITULAR}}. No lo corre
# ningún agente: lo ejecuta ella (o yo, supervisado) tras su OK. `stop` ≠ HALT: para
# frenar al instante TODO (incl. un agente en vuelo) usa  touch ~/.btp.HALT.
set -uo pipefail
REPO="$HOME/claudecode"
LA="$HOME/Library/LaunchAgents"
PLIST_DIR="$REPO/tools/launchd"
PY="$(command -v python3 || echo /usr/bin/python3)"
LAZO=(com.btp.dispatcher com.btp.healthcheck com.btp.bot-telegram)

cmd="${1:-status}"
case "$cmd" in
  install)
    mkdir -p "$LA"
    for p in "${LAZO[@]}"; do
      # REESCRIBE el home al de ESTA maquina en vez de copiar literal. El repo es union de dos
      # equipos (Air + mini) y sus plists llevan la ruta de quien los versiono: el 2-sep-2026
      # los 48 del repo apuntaban a /Users/titular, un usuario que aqui NO EXISTE. Un
      # `cp -f` habria machacado los plists BUENOS de ~/Library/LaunchAgents con rutas muertas
      # y tumbado el lazo entero (dispatcher, healthcheck, bot-telegram) sin que nadie lo viera
      # hasta la siguiente pasada. `activar_daemon.py` ya reescribia el home; esto no, y era el
      # unico camino que quedaba abierto.
      sed -E "s#/Users/[^/\"]+/claudecode#$REPO#g" "$PLIST_DIR/$p.plist" > "$LA/$p.plist" \
        && echo "instalado $p (home reescrito a $HOME)"
    done ;;
  start)
    for p in "${LAZO[@]}"; do
      [ -f "$LA/$p.plist" ] || sed -E "s#/Users/[^/\"]+/claudecode#$REPO#g" \
        "$PLIST_DIR/$p.plist" > "$LA/$p.plist"
      launchctl load -w "$LA/$p.plist" 2>/dev/null && echo "✅ cargado $p" || echo "· ya cargado/aviso $p"
    done
    echo "lazo encendido. Frénalo con: touch ~/.btp.HALT" ;;
  stop)
    for p in "${LAZO[@]}"; do
      launchctl unload -w "$LA/$p.plist" 2>/dev/null && echo "descargado $p" || true
    done
    echo "NOTA: stop ≠ HALT. Para frenar al instante: touch ~/.btp.HALT" ;;
  status)
    echo "== launchd (com.btp) =="; launchctl list 2>/dev/null | grep com.btp || echo "(ninguno cargado)"
    echo "== cola =="; "$PY" "$REPO/tools/cola.py" status 2>/dev/null
    echo "== gasto hoy =="; "$PY" "$REPO/tools/cost_guard.py" today 2>/dev/null | jq -c '{gastado_usd,n_jobs}' 2>/dev/null || echo "(sin gasto)"
    echo "== heartbeat dispatcher =="; cat "$REPO/tools/state/dispatcher/heartbeat.json" 2>/dev/null || echo "(sin heartbeat)"
    echo "== kill-switch =="; for h in "$HOME/.btp.HALT" "$REPO/.HALT"; do [ -e "$h" ] && echo "⛔ HALT activo: $h" || echo "ok: $h ausente"; done ;;
  seen)
    BTP_PRESENCE_OK=1 "$PY" "$REPO/tools/healthcheck.py" seen manual ;;
  *)
    echo "uso: btp_run.sh [install|start|stop|status|seen]"; exit 2 ;;
esac
