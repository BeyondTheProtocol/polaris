#!/bin/bash
# muro_guard.sh — wrapper FAIL-CLOSED del guard del muro.
# Regla de oro: SOLO exit 0 del guard permite; CUALQUIER otra salida -> exit 2 (deny).
# Así, si el guard crashea, falta python, o el .py desaparece -> se DENIEGA.
set -uo pipefail

PY=/usr/bin/python3
[ -x "$PY" ] || PY="$(command -v python3 2>/dev/null || true)"
[ -n "${PY:-}" ] || { echo "MURO ⛔ no hay python3 (fail-closed)" >&2; exit 2; }

GUARD="$(cd "$(dirname "$0")" 2>/dev/null && pwd)/muro_guard.py"
[ -f "$GUARD" ] || { echo "MURO ⛔ falta muro_guard.py (fail-closed)" >&2; exit 2; }

CODE=0
"$PY" "$GUARD" || CODE=$?
[ "$CODE" -eq 0 ] && exit 0 || exit 2
