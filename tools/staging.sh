#!/bin/bash
# staging.sh — interruptor de la ANTESALA (ver/probar una feature como en producción,
# en privado por Tailscale, antes de mandarla a producción).
#
# GATE DE TITULAR: "on" deja la antesala viva 24/7 (sobrevive a reinicios) y lo enciendes
# TÚ (tu mano = el gate del muro). No hace falta para usarla puntualmente: `web`/`port`
# ya la levantan solas. Privada: nada sale a internet. No despliega nada.
#
# USO:
#   bash ~/claudecode/tools/staging.sh web <rama> [--modo prod|dev]  # cargar una feature web
#   bash ~/claudecode/tools/staging.sh port <N> [--nombre "X"]       # cargar un tool web
#   bash ~/claudecode/tools/staging.sh on        # dejarla viva 24/7 (launchd)
#   bash ~/claudecode/tools/staging.sh off       # parar todo
#   bash ~/claudecode/tools/staging.sh estado    # ver estado
set -uo pipefail
REPO="$HOME/claudecode"
LA="$HOME/Library/LaunchAgents"
PY="$REPO/.venv/bin/python"
U="$(id -u)"
P="com.btp.staging"

case "${1:-estado}" in
  on)
    echo "→ Dejando la antesala viva 24/7 (sobrevive a reinicios)…"
    launchctl bootout "gui/$U/$P" 2>/dev/null || true
    launchctl unload "$LA/$P.plist" 2>/dev/null || true
    cp "$REPO/tools/launchd/$P.plist" "$LA/" 2>/dev/null || true
    sleep 1
    launchctl load -w "$LA/$P.plist" 2>/dev/null && echo "  ✓ $P" || echo "  ✗ $P (revisar)"
    sleep 1
    "$PY" "$REPO/tools/staging.py" estado
    echo "✅ Antesala 24/7 encendida. Abre en tu dispositivo: http://polaris.taild7f51c.ts.net:9091"
    ;;
  off)
    echo "→ Parando la antesala…"
    launchctl bootout "gui/$U/$P" 2>/dev/null || true
    launchctl unload -w "$LA/$P.plist" 2>/dev/null || true
    "$PY" "$REPO/tools/staging.py" off
    ;;
  web|port|limpiar)
    "$PY" "$REPO/tools/staging.py" "$@"
    ;;
  estado|*)
    "$PY" "$REPO/tools/staging.py" estado
    echo "== launchd =="
    launchctl list | grep -E "com.btp.staging" || echo "  (no cargado en launchd; usa 'on' para 24/7)"
    ;;
esac
