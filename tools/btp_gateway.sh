#!/bin/bash
# btp_gateway.sh — interruptor del daemon del Gateway del borde (F3). on | off | status.
#
# ⚠️ ENCENDER (on) cambia el estado de la máquina (daemon 24/7) → GATE de {{TITULAR}}.
# Es muro-POSITIVO: solo AÑADE enforcement del borde sobre quien quiera usar un modelo; no abre
# ninguna vía nueva de egress (loopback, sin claves). 'off' descarga el daemon (no es HALT).
set -uo pipefail
REPO="$HOME/claudecode"
LA="$HOME/Library/LaunchAgents"
PLIST_DIR="$REPO/tools/launchd"
P="com.btp.borde-gateway"
U="$(id -u)"
PORT="${BTP_GATEWAY_PORT:-8799}"

case "${1:-status}" in
  on)
    mkdir -p "$LA" "$PLIST_DIR/logs"
    launchctl bootout "gui/$U/$P" 2>/dev/null || true
    cp -f "$PLIST_DIR/$P.plist" "$LA/$P.plist"
    launchctl load -w "$LA/$P.plist" 2>/dev/null && echo "✅ gateway del borde encendido (daemon)" \
      || echo "✗ no se pudo cargar $P (revisar)"
    sleep 1; lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 \
      && echo "   escuchando en 127.0.0.1:$PORT ✅" || echo "   ⚠️ aún no escucha en $PORT" ;;
  off)
    launchctl bootout "gui/$U/$P" 2>/dev/null || true
    launchctl unload -w "$LA/$P.plist" 2>/dev/null || true
    echo "gateway del borde apagado (daemon descargado)" ;;
  status)
    echo "== launchd =="; launchctl list 2>/dev/null | grep "$P" || echo "(no cargado)"
    echo "== puerto =="; lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | tail -1 || echo "(8799 libre)"
    echo "== salud =="; python3 "$REPO/tools/borde_gateway.py" --health 2>/dev/null | head -6 ;;
  *) echo "uso: btp_gateway.sh [on|off|status]"; exit 2 ;;
esac
