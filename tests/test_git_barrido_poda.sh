#!/bin/bash
# test_git_barrido_poda.sh — la poda diaria NO depende de que el modelo esté disponible.
#
# EL FALLO (22-sep-2026). La rutina com.btp.git-barrido era solo `run_agent.sh <prompt>`: la poda
# la decidía el agente Haiku. `git-barrido.err` acumula decenas de «cadena de modelos agotada por
# límite → aplazo git»: esos días no se podaba nada y los worktrees ya fusionados se quedaban
# colgados. Ahora la poda va primero, determinista, y el agente después.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
pass=0; fail=0
ok() { pass=$((pass+1)); }
no() { fail=$((fail+1)); printf '  ✗ %s\n' "$1"; }

echo "== Barrido de git: la poda no depende del agente =="

# 1. La rutina instalada apunta al envoltorio, no directo al agente.
grep -q "tools/git_barrido.sh" "$ROOT/tools/launchd/com.btp.git-barrido.plist" && ok || \
  no "el plist de com.btp.git-barrido debe llamar a tools/git_barrido.sh"
grep -q '"script": "git_barrido.sh"' "$ROOT/tools/launchd/REGISTRO.json" && ok || \
  no "REGISTRO.json debe declarar git_barrido.sh para com.btp.git-barrido"

# Casa base de mentira: stubs de ramas.py y run_agent.sh que solo dejan traza.
TMP="$(mktemp -d)"; mkdir -p "$TMP/tools"
cat >"$TMP/tools/ramas.py" <<'PY'
import sys
open(sys.argv[0] + ".traza", "a").write(" ".join(sys.argv[1:]) + "\n")
sys.exit(int(__import__("os").environ.get("STUB_RC_PODA", "0")))
PY
cat >"$TMP/tools/run_agent.sh" <<'SH'
#!/bin/bash
printf '%s\n' "$@" >>"$0.traza"
exit "${STUB_RC_AGENTE:-0}"
SH
chmod +x "$TMP/tools/run_agent.sh"
run() { env HOME="$TMP" BTP_REPO="$TMP" "$@" bash "$ROOT/tools/git_barrido.sh" "PROMPT del charter"; }

# 2. Camino normal: poda ANTES, con `autopoda`, y el agente recibe el prompt intacto.
rc=0; run >"$TMP/out" 2>&1 || rc=$?
[ "$rc" = "0" ] && ok || no "camino normal: rc=0 (got $rc)"
grep -qx "autopoda" "$TMP/tools/ramas.py.traza" && ok || \
  no "debe llamar a ramas.py autopoda (traza: $(cat "$TMP/tools/ramas.py.traza" 2>/dev/null))"
grep -qx "PROMPT del charter" "$TMP/tools/run_agent.sh.traza" && ok || \
  no "el agente debe recibir el prompt tal cual"

# 3. Si el agente se cae por límite de modelo, la poda YA se hizo (es el punto de todo esto).
rm -f "$TMP"/tools/*.traza
rc=0; run STUB_RC_AGENTE=1 >/dev/null 2>&1 || rc=$?
[ "$rc" = "1" ] && ok || no "el rc de la rutina es el del agente (got $rc)"
grep -qx "autopoda" "$TMP/tools/ramas.py.traza" && ok || no "la poda ocurre aunque el agente falle"

# 4. Si la poda falla, el agente corre igual (no se pierde el barrido entero).
rm -f "$TMP"/tools/*.traza
rc=0; run STUB_RC_PODA=3 >"$TMP/out" 2>&1 || rc=$?
[ "$rc" = "0" ] && ok || no "una poda con rc!=0 no debe tumbar la rutina (got $rc)"
grep -q "autopoda: rc=3" "$TMP/out" && ok || no "una poda fallida se DICE en el log (para que no pase inadvertida)"
grep -qx "PROMPT del charter" "$TMP/tools/run_agent.sh.traza" && ok || no "el agente corre aunque la poda falle"

# 5. HALT: pausa total, no se muta el .git de nadie ni se llama al agente.
rm -f "$TMP"/tools/*.traza; : >"$TMP/.HALT"
rc=0; run >"$TMP/out" 2>&1 || rc=$?
[ "$rc" = "0" ] && ok || no "con HALT sale limpio (got $rc)"
[ ! -f "$TMP/tools/ramas.py.traza" ] && ok || no "con HALT NO se poda"
[ ! -f "$TMP/tools/run_agent.sh.traza" ] && ok || no "con HALT NO se llama al agente"
grep -q "HALT" "$TMP/out" && ok || no "con HALT lo dice en el log"

rm -rf "$TMP"
echo
echo "RESULTADO barrido/poda: $pass OK, $fail fallos"
[ "$fail" -eq 0 ] && echo "✅ EN VERDE" || echo "❌ revisar fallos"
exit "$fail"
