#!/bin/bash
# muro_guard.sh — wrapper FAIL-CLOSED del guard del muro.
# Regla de oro: SOLO exit 0 del guard permite; CUALQUIER otra salida -> exit 2 (deny).
# Así, si el guard crashea, falta python, o el .py desaparece -> se DENIEGA.
#
# Y si TARDA, también (25-sep-26). Idea de {{CONTACTO}} (https://contacto), con su agente
# KAI, revisión del 25-sep-2026. Un hook que agota su `timeout` de settings (10 s en los cuatro
# perfiles del lazo) NO bloquea: Claude Code lo cancela y la llamada sigue por el flujo normal de
# permisos (https://code.claude.com/docs/en/hooks.md, «Timeouts»). Este reloj corta a los 9 s y
# deniega antes. El guard lleva además su propio watchdog en Python (_watchdog.py, 8 s); este de
# bash cubre lo que aquel no ve: el arranque del intérprete y los imports.
set -uo pipefail

PY=/usr/bin/python3
[ -x "$PY" ] || PY="$(command -v python3 2>/dev/null || true)"
[ -n "${PY:-}" ] || { echo "MURO ⛔ no hay python3 (fail-closed)" >&2; exit 2; }

GUARD="$(cd "$(dirname "$0")" 2>/dev/null && pwd)/muro_guard.py"
[ -f "$GUARD" ] || { echo "MURO ⛔ falta muro_guard.py (fail-closed)" >&2; exit 2; }

# Plazo: 9 s (timeout de settings − 1). MURO_WATCHDOG_S solo puede ACORTARLO (lo usan los tests).
LIMITE=9
case "${MURO_WATCHDOG_S:-}" in
  ''|*[!0-9]*) ;;
  *) [ "$MURO_WATCHDOG_S" -lt "$LIMITE" ] && LIMITE="$MURO_WATCHDOG_S" ;;
esac

# `<&0` explícito: sin él, bash manda el stdin de un proceso en segundo plano a /dev/null y el
# guard no vería el JSON de la llamada.
"$PY" "$GUARD" <&0 &
GPID=$!
( trap 'kill "$S" 2>/dev/null; exit 0' TERM
  sleep "$LIMITE" & S=$!
  wait "$S"
  kill -KILL "$GPID" 2>/dev/null && echo "MURO ⛔ el guard no terminó en ${LIMITE} s (watchdog, fail-closed)" >&2
) &
WPID=$!

CODE=0
wait "$GPID" || CODE=$?
kill -TERM "$WPID" 2>/dev/null
wait "$WPID" 2>/dev/null
[ "$CODE" -eq 0 ] && exit 0 || exit 2
