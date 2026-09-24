#!/bin/bash
# test_dispatcher.sh — integración del despachador del lazo (P1). Estado aislado + mock.
# Casos: HALT corta · lock «un cerebro» · caducidad no corre · dead-letter · coste aun si falla.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY=/usr/bin/python3; [ -x "$PY" ] || PY=python3
pass=0; fail=0
ok()   { pass=$((pass+1)); }
no()   { fail=$((fail+1)); printf '  ✗ %s\n' "$1"; }

mkmock() { # $1=salida_rc  $2=cost  $3=is_error  → escribe un run_agent simulado que cuenta invocaciones
  cat >"$MOCK" <<EOF
#!/bin/bash
echo invoked >>"$COUNTER"
echo '{"total_cost_usd": $2, "is_error": $3}'
exit $1
EOF
  chmod +x "$MOCK"
}

# BTP_REPO="$ROOT": el dispatcher hace REPO=${BTP_REPO:-$HOME/claudecode}, así que SIN esto
# probaría las tools de CASA BASE, no las del árbol bajo prueba (bug de test que enmascara el
# skew de versión: un worktree que añade un campo nuevo nunca cazaría el problema en su CI).
q()  { BTP_REPO="$ROOT" BTP_STATE_DIR="$ST" "$PY" "$ROOT/tools/cola.py" "$@"; }
cnt() { wc -l <"$COUNTER" 2>/dev/null | tr -d ' '; }
run_once() { BTP_TEST_BATTERY=1 BTP_REPO="$ROOT" BTP_STATE_DIR="$ST" BTP_HALT_FILES="$HALT" BTP_RUN_AGENT="$MOCK" BTP_BANDEJA="$TMP/bandeja.md" BTP_PANEL="$TMP/panel.md" BTP_ONCE=1 bash "$ROOT/tools/btp_dispatcher.sh" >/dev/null 2>&1; }
fresh() { TMP="$(mktemp -d)"; ST="$TMP/state"; MOCK="$TMP/mock.sh"; COUNTER="$TMP/counter"; HALT="$TMP/.halt"; : >"$COUNTER"; }

echo "== Dispatcher (integración) =="

# El panel del árbol bajo prueba NO se toca (22-sep-2026): con BTP_REPO="$ROOT", cada pasada
# dejaba 12 jobs falsos en su PANEL-LAZO.md (gitignored). En un worktree bloqueaba la poda; en
# casa base ensuciaba el panel real. Foto antes; se compara al final.
PANEL_REAL="$ROOT/00_FUENTE-DE-VERDAD/Gestion/PANEL-LAZO.md"
panel_sz() { if [ -f "$PANEL_REAL" ]; then wc -c <"$PANEL_REAL" | tr -d " "; else echo 0; fi; }
PANEL_ANTES="$(panel_sz)"

# 1. HALT corta: no se invoca al agente, el job se queda en cola.
fresh; mkmock 0 0.05 false
q enqueue --prioridad alta --procedencia t "trabajo" >/dev/null
: >"$HALT"                      # HALT presente
run_once
[ "$(cnt)" = "0" ] && ok || no "HALT: el agente NO debe invocarse (cnt=$(cnt))"
[ "$(q status | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["pending"])')" = "1" ] && ok || no "HALT: el job debe quedar en pending"
rm -rf "$TMP"

# 2. camino feliz: 1 job → invoca 1 vez, queda en done, coste sumado.
fresh; mkmock 0 0.07 false
q enqueue --prioridad alta --procedencia t "trabajo" >/dev/null
run_once
[ "$(cnt)" = "1" ] && ok || no "feliz: debe invocar 1 vez (cnt=$(cnt))"
[ "$(q status | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["done"])')" = "1" ] && ok || no "feliz: job en done"
spent="$(BTP_STATE_DIR="$ST" "$PY" "$ROOT/tools/cost_guard.py" today | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["gastado_usd"])')"
[ "$spent" = "0.07" ] && ok || no "feliz: coste 0.07 sumado (got $spent)"
grep -q "^## .* job " "$TMP/panel.md" 2>/dev/null && ok || no "feliz: la entrada del panel va al panel del tmp (BTP_PANEL)"
rm -rf "$TMP"

# 3. lock «un cerebro»: si el lockdir lo tiene un PID vivo, el dispatcher sale sin trabajar.
fresh; mkmock 0 0.05 false
q enqueue --procedencia t "trabajo" >/dev/null
mkdir -p "$ST/dispatcher/lock"
sleep 30 & livepid=$!; echo "$livepid" >"$ST/dispatcher/lock/pid"
run_once
[ "$(cnt)" = "0" ] && ok || no "lock: con otro cerebro vivo NO debe trabajar (cnt=$(cnt))"
kill "$livepid" 2>/dev/null
rm -rf "$TMP"

# 4. caducidad: un job expirado no se ejecuta (va a failed).
fresh; mkmock 0 0.05 false
q enqueue --procedencia t --expira 2000-01-01T00:00:00 "viejo" >/dev/null
run_once
[ "$(cnt)" = "0" ] && ok || no "caducidad: NO debe invocar (cnt=$(cnt))"
[ "$(q status | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["failed"])')" = "1" ] && ok || no "caducidad: job en failed"
rm -rf "$TMP"

# 5. dead-letter + coste aun si falla: mock que falla (rc=1, is_error true) con max_intentos=2.
fresh; mkmock 1 0.03 true
q enqueue --procedencia t --max-intentos 2 "venenoso" >/dev/null
run_once     # intento 1 → reencola
[ "$(q status | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["pending"])')" = "1" ] && ok || no "fallo1: reencola"
run_once     # intento 2 → dead-letter
[ "$(q status | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["failed"])')" = "1" ] && ok || no "fallo2: dead-letter en failed"
spent="$(BTP_STATE_DIR="$ST" "$PY" "$ROOT/tools/cost_guard.py" today | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["gastado_usd"])')"
[ "$spent" = "0.06" ] && ok || no "fallo: coste se suma aun fallando (esperaba 0.06, got $spent)"
rm -rf "$TMP"

# 6. jobs exec arrancan con el CONTEXTO del lazo (brújula); el prompt lo lleva.
fresh
cat >"$MOCK" <<EOF
#!/bin/bash
printf '%s' "\$1" >"$TMP/captured"
echo '{"total_cost_usd": 0.01, "is_error": false}'
EOF
chmod +x "$MOCK"
q enqueue --procedencia t "intencion exec de prueba" >/dev/null
run_once
grep -q "CONTEXTO DEL LAZO" "$TMP/captured" && ok || no "exec: el prompt debe llevar el contexto del lazo (brújula)"
grep -q "intencion exec de prueba" "$TMP/captured" && ok || no "exec: el prompt debe llevar la intención"
rm -rf "$TMP"

# 7. los jobs triage (cuarentena) van CRUDOS: NO reciben la brújula/continuidad.
fresh
cat >"$MOCK" <<EOF
#!/bin/bash
printf '%s' "\$1" >"$TMP/captured"
echo '{"total_cost_usd": 0.01, "is_error": false}'
EOF
chmod +x "$MOCK"
q enqueue --tipo triage --perfil quarantine --procedencia t "texto a triar" >/dev/null
run_once
grep -q "CONTEXTO DEL LAZO" "$TMP/captured" && no "triage: NO debe recibir el contexto del lazo (cuarentena)" || ok
rm -rf "$TMP"

# 8. APLAZADO (rc 75): Claude no disponible → requeue (vuelve a pending), NO done, NO gasta intento.
fresh; mkmock 75 0 false
q enqueue --procedencia t --max-intentos 2 "trabajo agéntico" >/dev/null
run_once
[ "$(cnt)" = "1" ] && ok || no "aplazado: invoca 1 vez (cnt=$(cnt))"
[ "$(q status | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["pending"])')" = "1" ] && ok || no "aplazado: vuelve a pending (requeue)"
[ "$(q status | "$PY" -c 'import json,sys;d=json.load(sys.stdin);print(d["done"]+d["failed"])')" = "0" ] && ok || no "aplazado: NO marca done ni failed"
run_once   # 2o aplazado: sigue reintentando, NO dead-letter (no gasta intento)
[ "$(q status | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["pending"])')" = "1" ] && ok || no "aplazado x2: sigue en pending (no dead-letter)"
rm -rf "$TMP"

# 9. forward-compat / anti-inyección: un job con un campo TOP-LEVEL desconocido (skew de
# versión: worktree con un campo nuevo, este consumidor viejo) NO se ejecuta (fail-closed)
# y va a failed con motivo `schema-desconocido` (recuperable), distinto de un plantado.
# Se planta el .json directo en pending/ (simula el job que escribió la otra versión).
fresh; mkmock 0 0.05 false
mkdir -p "$ST/queue/pending"
cat >"$ST/queue/pending/0-x-skew.json" <<'JSON'
{"id":"skew","prioridad":"alta","intencion":"x","perfil":"privileged","intentos":0,"max_intentos":3,"creado":"2026-01-01T00:00:00","procedencia":"t","tipo":"exec","__campo_futuro__":"x"}
JSON
run_once
[ "$(cnt)" = "0" ] && ok || no "skew: el agente NO debe invocarse con campo desconocido (cnt=$(cnt))"
[ "$(q status | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["failed"])')" = "1" ] && ok || no "skew: job en failed"
motivo="$("$PY" -c 'import json;print(json.load(open("'"$ST"'/queue/failed/0-x-skew.json")).get("ultimo_error",""))' 2>/dev/null)"
printf '%s' "$motivo" | grep -q "schema-desconocido" && ok || no "skew: motivo schema-desconocido (got: $motivo)"
rm -rf "$TMP"

# 10. LATIDO DURANTE EL JOB (hallazgo medio nº6, 25-jul-26): el bucle solo latía ENTRE jobs, así
# que un job largo dejaba el latido envejeciendo con el motor perfectamente vivo (medido: el job
# más largo de 1604 corridas fue 451 s, el 75% del umbral de 600 s del dead-man). El refrescador
# de fondo lo mantiene fresco, y mientras corre el latido NOMBRA el job — que es la señal que usa
# `cola.reap_stuck` para no rescatar por debajo algo que sigue ejecutándose.
fresh
cat >"$MOCK" <<EOF
#!/bin/bash
sleep 3
echo '{"total_cost_usd": 0.01, "is_error": false}'
EOF
chmod +x "$MOCK"
q enqueue --procedencia t "trabajo largo" >/dev/null
HB="$ST/dispatcher/heartbeat.json"
# Latido pre-envejecido 300 s: si NADIE lo refresca durante el job, se queda viejo y se ve.
mkdir -p "$ST/dispatcher"; printf '{"ts":"x","pid":1,"last_job":""}\n' >"$HB"
touch -t "$(date -v-300S +%Y%m%d%H%M.%S 2>/dev/null || date -d '300 seconds ago' +%Y%m%d%H%M.%S)" "$HB"
# Fotografía del latido a mitad del job (a los 2 s, con el mock aún durmiendo).
( sleep 2; cp "$HB" "$TMP/hb_mitad.json" 2>/dev/null
  "$PY" -c "import os,time;print(int(time.time()-os.path.getmtime('$HB')))" >"$TMP/hb_edad" 2>/dev/null ) &
BTP_HEARTBEAT_INTERVAL=1 BTP_TEST_BATTERY=1 BTP_REPO="$ROOT" BTP_STATE_DIR="$ST" BTP_HALT_FILES="$HALT" BTP_RUN_AGENT="$MOCK" BTP_BANDEJA="$TMP/bandeja.md" BTP_PANEL="$TMP/panel.md" BTP_ONCE=1 bash "$ROOT/tools/btp_dispatcher.sh" >/dev/null 2>&1
wait
edad="$(cat "$TMP/hb_edad" 2>/dev/null || echo 999)"
{ [ -n "$edad" ] && [ "$edad" -le 5 ] 2>/dev/null; } && ok || no "latido: debe refrescarse DURANTE el job (edad a mitad: ${edad}s)"
"$PY" -c 'import json,sys;d=json.load(open("'"$TMP"'/hb_mitad.json"));sys.exit(0 if d.get("last_job") else 1)' 2>/dev/null && ok || no "latido: durante el job debe NOMBRAR el job (reap_stuck depende de eso)"
"$PY" -c 'import json,sys;d=json.load(open("'"$HB"'"));sys.exit(0 if not d.get("last_job") else 1)' 2>/dev/null && ok || no "latido: al terminar deja de nombrarlo (reap vuelve a poder rescatar)"
rm -rf "$TMP"

# 11. GATE DE ENTREGABLE (20-sep-26, deuda cola-marca-done-sin-verificar-cambio-real).
# «Terminó sin error» no es «entregó»: un job de 6,03 USD se cerró OK sin que el cambio existiera.
# `tools/state/` está en .gitignore, así que el fichero de prueba no ensucia el árbol.
REL="tools/state/test-entregable-$$.md"
qp() { BTP_REPO="$ROOT" BTP_STATE_DIR="$ST" "$PY" -c "
import sys; sys.path.insert(0, '$ROOT/tools')
import cola; print(cola.enqueue('trabajo con entregable', procedencia='t', prueba={'tipo':'ruta','ruta':'$REL'}))"; }

# 11a. prometió un fichero y no lo dejó → failed, NO done, y no se reintenta (dead-letter).
fresh; mkmock 0 0.05 false
rm -f "$ROOT/$REL"
qp >/dev/null
run_once
[ "$(cnt)" = "1" ] && ok || no "gate: el agente sí se invoca (cnt=$(cnt))"
[ "$(q status | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["done"])')" = "0" ] && ok || no "gate: sin entregable NO puede quedar en done"
[ "$(q status | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["failed"])')" = "1" ] && ok || no "gate: sin entregable va a failed"
run_once   # segunda vuelta: si se reintentara, el contador subiría
[ "$(cnt)" = "1" ] && ok || no "gate: 'sin-entregable' NO se reintenta (cnt=$(cnt))"
rm -rf "$TMP"

# 11b. el agente deja el fichero → se cierra como hecho.
fresh
cat >"$MOCK" <<EOF
#!/bin/bash
echo invoked >>"$COUNTER"
mkdir -p "\$(dirname "$ROOT/$REL")"; echo hecho >"$ROOT/$REL"
echo '{"total_cost_usd": 0.05, "is_error": false}'
exit 0
EOF
chmod +x "$MOCK"
rm -f "$ROOT/$REL"
qp >/dev/null
run_once
[ "$(q status | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["done"])')" = "1" ] && ok || no "gate: con el entregable en disco SÍ se cierra"
rm -f "$ROOT/$REL"; rm -rf "$TMP"

# 11c. un job SIN prueba se cierra como siempre (no-regresión), y BTP_PRUEBA=0 apaga el gate.
fresh; mkmock 0 0.05 false
q enqueue --procedencia t "trabajo de siempre" >/dev/null
run_once
[ "$(q status | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["done"])')" = "1" ] && ok || no "gate: sin prueba, el camino de hoy intacto"
rm -rf "$TMP"

fresh; mkmock 0 0.05 false
rm -f "$ROOT/$REL"
qp >/dev/null
BTP_PRUEBA=0 BTP_TEST_BATTERY=1 BTP_REPO="$ROOT" BTP_STATE_DIR="$ST" BTP_HALT_FILES="$HALT" BTP_RUN_AGENT="$MOCK" BTP_BANDEJA="$TMP/bandeja.md" BTP_PANEL="$TMP/panel.md" BTP_ONCE=1 bash "$ROOT/tools/btp_dispatcher.sh" >/dev/null 2>&1
[ "$(q status | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["done"])')" = "1" ] && ok || no "gate: BTP_PRUEBA=0 lo apaga (interruptor de emergencia)"
rm -rf "$TMP"

[ "$(panel_sz)" = "$PANEL_ANTES" ] && ok || no "el PANEL-LAZO del árbol bajo prueba cambió ($PANEL_ANTES → $(panel_sz) bytes): un test escribe en el panel de verdad"

echo
echo "RESULTADO dispatcher: $pass OK, $fail fallos"
[ "$fail" -eq 0 ] && echo "✅ DISPATCHER EN VERDE" || echo "❌ revisar fallos"
exit "$fail"
