#!/bin/bash
# lazo_telegram.sh — interruptor del lazo 24/7 por Telegram.
#
# GATE DE TITULAR: este script lo ejecuta ELLA (su mano = el gate del muro). Enciende/para
# los 3 daemons del lazo: bot (entrada) + dispatcher (el cerebro privilegiado) + flush
# nocturno. Todo el código ya está probado en verde. El muro sigue: nada hacia fuera sin
# su firma, tope 5 €/día, silencio 23:00-08:00, cortable por HALT/código rojo.
#
# USO:
#   bash ~/claudecode/tools/lazo_telegram.sh on       # encender
#   bash ~/claudecode/tools/lazo_telegram.sh off      # parar
#   bash ~/claudecode/tools/lazo_telegram.sh estado   # ver estado
set -uo pipefail
REPO="$HOME/claudecode"
LA="$HOME/Library/LaunchAgents"
PY="$REPO/.venv/bin/python"
U="$(id -u)"
PLISTS="com.btp.bot-telegram com.btp.dispatcher com.btp.notif-flush"

estado() {
  echo "== Daemons del lazo =="
  launchctl list | grep -E "bot-telegram|dispatcher|notif-flush" || echo "  (ninguno cargado)"
  echo "== Cola =="
  "$PY" "$REPO/tools/cola.py" status 2>/dev/null | grep -E 'pending|processing|done|failed' | sed 's/^/  /'
  echo "== Gasto de hoy =="
  "$PY" "$REPO/tools/cost_guard.py" today 2>/dev/null | grep -E 'gastado_usd|tope_diario' | sed 's/^/  /'
}

case "${1:-estado}" in
  on)
    echo "→ Encendiendo el lazo 24/7 por Telegram…"
    for p in $PLISTS; do
      launchctl bootout "gui/$U/$p" 2>/dev/null || true   # saca cualquier instancia previa
      launchctl unload "$LA/$p.plist" 2>/dev/null || true
      cp "$REPO/tools/launchd/$p.plist" "$LA/" 2>/dev/null || true
    done
    sleep 1
    for p in $PLISTS; do
      launchctl load -w "$LA/$p.plist" 2>/dev/null && echo "  ✓ $p" || echo "  ✗ $p (revisar)"
    done
    sleep 2
    echo; estado
    echo
    echo "✅ Lazo encendido. Pruébalo: abre Telegram y escríbele al bot @titular_hoy_bot."
    echo "   Lo verás en ~/claudecode/BANDEJA.md y te avisará por Telegram."
    echo "   Para parar en cualquier momento:  bash $REPO/tools/lazo_telegram.sh off"
    ;;
  off)
    echo "→ Parando el lazo…"
    for p in $PLISTS; do
      launchctl bootout "gui/$U/$p" 2>/dev/null || true
      launchctl unload -w "$LA/$p.plist" 2>/dev/null || true
    done
    echo "⏹  Lazo parado: el bot deja de leer y el dispatcher se detiene."
    ;;
  estado|*)
    estado
    ;;
esac
