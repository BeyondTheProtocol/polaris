#!/bin/bash
# Pull-al-abrir del playbook clínico (Fase 2, pieza B).
# Intenta coser ESTADO-ACTUAL.md con la otra máquina ANTES de que el orquestador lo lea,
# pero solo si hace rato del último intento (--si-viejo) para no pagar ssh en cada sesión.
#
# Fail-OPEN total: pase lo que pase (mini apagado, sin red, conflicto), exit 0 y en silencio.
# NUNCA bloquea ni ralentiza la sesión más allá del ConnectTimeout corto del propio sync.
#
# Enganche (GATED — activarlo es decisión de {{TITULAR}}, y SOLO después del bootstrap):
#   · en la Air: añadir una línea a .claude/hooks/session_start.sh que llame a este script en background
#   · o invocarlo a mano / desde el arranque del flujo del orquestador
# Variables:
#   SYNC_REMOTO  alias ssh del otro equipo (def: polaris = el mini desde la Air)
#   SYNC_MIN     minutos de throttle (def: 20)
set -uo pipefail

REPO="${BTP_REPO:-$HOME/claudecode}"
PY="${REPO}/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || echo /usr/bin/python3)"
REMOTO="${SYNC_REMOTO:-polaris}"
MIN="${SYNC_MIN:-20}"

( cd "$REPO" && "$PY" tools/sync_playbook.py --apply --si-viejo "$MIN" --remoto "$REMOTO" ) \
  >/dev/null 2>&1 || true

# Y de paso, el CÓDIGO (25-jul-26, lo pidió {{TITULAR}}): el Air se pone al día solo por
# fast-forward, con sus propias guardas (solo en el Air, solo con el árbol limpio, solo
# si es FF). Fail-open igual que lo de arriba: si falla, ni se nota.
[ -x "$REPO/tools/ff_al_abrir.sh" ] && ( "$REPO/tools/ff_al_abrir.sh" & ) >/dev/null 2>&1

exit 0
