#!/usr/bin/env bash
# tests/test_digest.sh — el digest para el RAG de {{CONTACTO}} NUNCA fuga PII clínica cruda.
# (El léxico estratégico 'vacuna'/'neoantígeno' SÍ se permite: el bot es canal privado.)
set -uo pipefail
cd "$(dirname "$0")/.."
PY=python3
fail=0
ok(){ echo "  ✓ $1"; }
ko(){ echo "  ✗ $1"; fail=1; }

echo "== test_digest =="

OUT="$($PY tools/digest.py build 2>/dev/null)"; rc=$?
[ "$rc" -eq 0 ] && ok "build sale 0" || ko "build falló (rc=$rc)"

# 1) NUNCA PII clínica cruda en el digest: genes, variantes HGVS, teléfonos, cifras/depósito, rutas privadas.
if echo "$OUT" | grep -iqE '\b(tp53|ccne1|rb1|brca1|brca2|esr1|pik3ca|her2|pten|kras|egfr)\b'; then
  ko "FUGA: gen/biomarcador nombrado"; else ok "sin genes/biomarcadores"; fi
if echo "$OUT" | grep -iqE '\b[cp]\.[0-9]|p\.[A-Za-z]{3}[0-9]'; then
  ko "FUGA: variante genómica (HGVS)"; else ok "sin variantes HGVS"; fi
if echo "$OUT" | grep -qE '(?<!\d)[6-9][0-9]{8}|[0-9]{3}[ .-][0-9]{2,3}[ .-][0-9]{2,3}' 2>/dev/null || echo "$OUT" | grep -qE '\b[6-9][0-9]{8}\b'; then
  ko "FUGA: posible teléfono"; else ok "sin teléfonos"; fi
if echo "$OUT" | grep -qiE '2700|CHF|_PRIVADO'; then
  ko "FUGA: cifra cruda / ruta privada"; else ok "sin cifras crudas ni rutas _PRIVADO"; fi

# 2) Contenido esperado presente (es un digest útil, no vacío).
echo "$OUT" | grep -q "Meta (estrella polar)" && ok "tiene Meta" || ko "falta Meta"
echo "$OUT" | grep -q "AQUÍ ESTAMOS" && ok "tiene foco (AQUÍ ESTAMOS)" || ko "falta el foco"
echo "$OUT" | grep -q "Cadena hasta NED" && ok "tiene la cadena" || ko "falta la cadena"

# 3) El scrub omite PII clínica si algo se cuela (unit del _scrub).
SCRUB="$($PY -c "import sys; sys.path.insert(0,'tools'); import digest; print(digest._scrub('Mutacion TP53 p.R175H, RB1; llamame al 612345678'))")"
echo "$SCRUB" | grep -qi 'tp53\|p\.r175h\|612345678' && ko "el scrub NO omitió PII: $SCRUB" || ok "el scrub omite genes/variante/teléfono"

# 4) build es determinista (idempotente cuerpo a cuerpo salvo la fecha).
A="$($PY tools/digest.py build 2>/dev/null | grep -v '^# Digest')"
B="$($PY tools/digest.py build 2>/dev/null | grep -v '^# Digest')"
[ "$A" = "$B" ] && ok "build determinista" || ko "build no determinista"

echo
[ "$fail" -eq 0 ] && echo "✅ test_digest VERDE" || echo "❌ test_digest ROJO"
exit $fail
