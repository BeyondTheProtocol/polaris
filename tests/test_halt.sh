#!/bin/bash

# El muro mira SIEMPRE `~/claudecode` (muro_guard.py, a propósito: nunca un repo
# arbitrario). Fuera de la casa base este test no puede decir nada cierto, así que lo
# dice en vez de ponerse rojo. rc=77 = saltado (ver tests/_entorno.py).
if [ "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)" != "$(cd ~/claudecode 2>/dev/null && pwd -P)" ]; then
  echo "⏭️  SKIP: necesita estar en ~/claudecode: el muro mira esa ruta, no un repo cualquiera"
  exit 77
fi
# test_halt.sh — H1: el muro frena al agente EN VUELO ante CUALQUIER kill-switch.
# El de fuera del repo (~/.btp.HALT) es el que se le enseña a {{TITULAR}}: tiene que parar
# el guard, no solo $REPO/.HALT. Se prueba con BTP_HALT_FILES aislado (sin tocar los reales).
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GUARD_SH="$ROOT/.claude/hooks/muro_guard.sh"
PY=/usr/bin/python3; [ -x "$PY" ] || PY=python3
pass=0; fail=0
TMP="$(mktemp -d)"; EXT="$TMP/ext.HALT"

guard() { # esperado(DENY|ALLOW)  → alimenta 'ls -la' con BTP_HALT_FILES=$EXT
  local exp="$1" code=0 got=ALLOW
  echo '{"tool_name":"Bash","tool_input":{"command":"ls -la"}}' \
    | MURO_PROFILE=privileged BTP_HALT_FILES="$EXT" "$GUARD_SH" >/dev/null 2>&1 || code=$?
  [ "$code" -ne 0 ] && got=DENY
  if [ "$got" = "$exp" ]; then pass=$((pass+1)); else fail=$((fail+1)); printf '  ✗ esperaba %s, obtuvo %s\n' "$exp" "$got"; fi
}

echo "== H1: kill-switch externo frena el muro =="
rm -f "$EXT"; guard ALLOW          # sin HALT → permite
: >"$EXT";   guard DENY            # HALT externo presente → DENIEGA todo
rm -f "$EXT"; guard ALLOW          # retirado → vuelve a permitir
rm -rf "$TMP"

echo "RESULTADO halt: $pass OK, $fail fallos"
[ "$fail" -eq 0 ] && echo "✅ HALT EXTERNO EN VERDE" || echo "❌ revisar fallos"
exit "$fail"
