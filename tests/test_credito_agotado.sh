#!/bin/bash
# test_credito_agotado.sh — ante un 400 "Credit balance is too low" de la API de Anthropic,
# run_agent.sh avisa a {{TITULAR}} UNA vez/día (flag anti-spam) y aplaza, en vez de morir callado.
# Aislado: mock de `claude` (BTP_CLAUDE_BIN) + mock de `salida.py` (BTP_SALIDA) + estado en /tmp.
# NO toca producción ni manda nada de verdad.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
fail=0
ok(){ echo "  ok · $1"; }
no(){ echo "  FALLO · $1"; fail=$((fail+1)); }

# El kill-switch del muro aborta run_agent antes de llegar a la lógica → no podemos testear bajo HALT.
if [ -f "$ROOT/.HALT" ] || [ -f "$HOME/.btp.HALT" ]; then
  echo "RESULTADO credito-agotado: ⏭️  SALTADO (HALT activo)"; exit 0
fi

# --- mocks ---
C400="$TMP/claude_400.sh"; printf '%s\n' '#!/bin/bash' \
  'echo '\''{"is_error":true,"api_error_status":400,"result":"Credit balance is too low","total_cost_usd":0}'\''' >"$C400"
COK="$TMP/claude_ok.sh"; printf '%s\n' '#!/bin/bash' \
  'echo '\''{"is_error":false,"result":"ok","total_cost_usd":0.01}'\''' >"$COK"
chmod +x "$C400" "$COK"
SAL="$TMP/salida_mock.py"; cat >"$SAL" <<EOF
import sys
open("$TMP/calls.log","a").write("call\n")
print('{"ok":true}')
EOF

calls(){ [ -f "$TMP/calls.log" ] && wc -l <"$TMP/calls.log" | tr -d ' ' || echo 0; }
runrc(){ # $1 = mock de claude ; $2 = dir de estado
  # BTP_REPO="$ROOT": prueba el run_agent de ESTE repo. BTP_IA_FAKE="": desactiva el respaldo de la
  # centralita (F2) para AISLAR el carril de crédito sin red — el relevo se prueba en test_run_agent_f2.
  # BTP_API_KEY_OVERRIDE=x: gancho de test (run_agent.sh:80) para saltar el Llavero -- en la mini
  # headless no hay Keychain y run_agent abortaria con "Falta btp-anthropic-api" antes del 400.
  BTP_AGENT=test-credito BTP_COST_GUARDED=1 BTP_STATE_DIR="$2" BTP_REPO="$ROOT" BTP_IA_FAKE="" \
  BTP_API_KEY_OVERRIDE=x BTP_CLAUDE_BIN="$1" BTP_SALIDA="$SAL" BTP_MODEL=haiku \
  bash "$ROOT/tools/run_agent.sh" "prueba aislada" >/dev/null 2>&1
}

ST="$TMP/state"; FLAG="$ST/dispatcher/aviso-credito-$(date +%F).flag"
mkdir -p "$ST/notif"; echo '{"activo":true}' >"$ST/notif/config.json"

# 1) 400 + notif ON → crea el flag y avisa UNA vez
runrc "$C400" "$ST"
[ -e "$FLAG" ] && ok "400: crea el flag de aviso" || no "400: debería crear el flag"
[ "$(calls)" = "1" ] && ok "400: avisa a {{TITULAR}} una vez" || no "400: debería avisar 1 vez (calls=$(calls))"

# 2) segundo 400 el mismo día → NO re-avisa (el flag corta el spam)
runrc "$C400" "$ST"
[ "$(calls)" = "1" ] && ok "400 repetido: no spamea (sigue 1)" || no "400 repetido: no debe re-avisar (calls=$(calls))"

# 3) éxito normal → no dispara el carril de crédito
runrc "$COK" "$ST"
[ "$(calls)" = "1" ] && ok "éxito: no dispara aviso de crédito" || no "éxito: no debería avisar (calls=$(calls))"

# 4) 400 + notif OFF (sin config) → deja rastro (flag) pero NO avisa
ST2="$TMP/state2"; FLAG2="$ST2/dispatcher/aviso-credito-$(date +%F).flag"
runrc "$C400" "$ST2"
[ -e "$FLAG2" ] && ok "notif OFF: deja rastro (flag) sin avisar" || no "notif OFF: debería dejar el flag"
[ "$(calls)" = "1" ] && ok "notif OFF: no avisa (sigue 1)" || no "notif OFF: no debe avisar (calls=$(calls))"

echo
[ "$fail" -eq 0 ] && echo "RESULTADO credito-agotado: ✅ EN VERDE (4/4)" || echo "RESULTADO credito-agotado: ❌ $fail fallo(s)"
exit "$fail"
