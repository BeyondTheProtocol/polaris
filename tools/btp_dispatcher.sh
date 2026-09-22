#!/bin/bash
# btp_dispatcher.sh — el "único cerebro" del lazo 24/7 de Polaris (P1).
#
# Daemon (launchd KeepAlive). NO es un agente Claude (no pasa por el muro): es shell que
# ORQUESTA. Por cada ciclo: comprueba los kill-switches, late (heartbeat), pregunta al
# cost_guard si queda presupuesto, toma UN job de la cola (lock mkdir = un cerebro),
# re-comprueba HALT, lo lanza con run_agent.sh (que SÍ tiene el muro), captura el JSON,
# le SUMA el coste (siempre, aun si falla), y marca done/failed con back-off + dead-letter.
#
# Ganchos de entorno (para tests aislados; en producción se dejan por defecto):
#   BTP_REPO         raíz del repo            (def $HOME/claudecode)
#   BTP_STATE_DIR    estado del lazo          (def $REPO/tools/state)  → lo heredan queue/cost
#   BTP_HALT_FILES   kill-switches ":" sep    (def $HOME/.btp.HALT:$REPO/.HALT)
#   BTP_RUN_AGENT    arranque del agente      (def $REPO/tools/run_agent.sh)
#   BTP_ONCE         si !="" → un ciclo y sale (tests)
#   BTP_IDLE         seg de espera sin job     (def 15)
#   BTP_HEARTBEAT_INTERVAL  seg entre latidos DURANTE un job (def 60)
set -uo pipefail

REPO="${BTP_REPO:-$HOME/claudecode}"
TOOLS="$REPO/tools"
STATE="${BTP_STATE_DIR:-$TOOLS/state}"
export BTP_STATE_DIR="$STATE"            # cola.py / cost_guard.py lo heredan
RUN_AGENT="${BTP_RUN_AGENT:-$TOOLS/run_agent.sh}"
HALT_LIST="$(printf '%s' "${BTP_HALT_FILES:-$HOME/.btp.HALT:$REPO/.HALT}" | tr ':' ' ')"
IDLE="${BTP_IDLE:-15}"
# Cada cuánto refresca el latido MIENTRAS corre un job. 60 s en producción; la batería lo baja a 1
# para poder comprobar el refresco sin que el test tarde un minuto (el umbral del dead-man son 600).
HB_INTERVAL="${BTP_HEARTBEAT_INTERVAL:-60}"
PY="$(command -v python3 || echo /usr/bin/python3)"
# Tope diario real (USD) para las etiquetas/avisos; el techo absoluto vive en cost_guard.
CAP="$("$PY" -c 'import sys;sys.path.insert(0,"'"$TOOLS"'");import cost_guard;print("%.2f"%cost_guard._limits()[0])' 2>/dev/null || echo 30)"
LOG="$TOOLS/launchd/logs"; mkdir -p "$LOG" "$STATE/dispatcher" 2>/dev/null || true
LOCK="$STATE/dispatcher/lock"
BACKOFF_F="$STATE/dispatcher/backoff"

log() { printf '%s %s\n' "$(date +%FT%T)" "$*" >>"$LOG/dispatcher.out"; }
halted() { local h; for h in $HALT_LIST; do [ -e "$h" ] && return 0; done; return 1; }
heartbeat() {
  # tmp por PROCESO ($BASHPID, no $$: en el subshell refrescador $$ sigue siendo el del padre): el
  # latido de fondo durante un job y el del bucle principal escriben a la vez, y un tmp compartido
  # los haría pisarse a medio printf. El mv -f final sí es atómico.
  # ⚠️ El `:-$$` NO es decorativo: macOS trae bash 3.2, que NO tiene $BASHPID, y con
  # `set -u` esto abortaba el dispatcher entero en esta línea (11 de 21 pruebas en
  # rojo el 25-jul-26). Con el respaldo, en bash 4+ separa por subshell como se
  # quería, y en 3.2 degrada al PID del padre en vez de tumbar el lazo.
  local tmp="$STATE/dispatcher/heartbeat.json.tmp.${BASHPID:-$$}"
  printf '{"ts":"%s","pid":%d,"last_job":"%s"}\n' "$(date +%FT%T)" "$$" "${1:-}" >"$tmp" \
    && mv -f "$tmp" "$STATE/dispatcher/heartbeat.json"
}

# ── Lock "un solo cerebro": mkdir atómico + PID vivo ──
if ! mkdir "$LOCK" 2>/dev/null; then
  oldpid="$(cat "$LOCK/pid" 2>/dev/null || true)"
  # H10: pid VACÍO = otro dispatcher acaba de hacer mkdir y aún no lo escribió → NO se lo
  # robo (back-off), si no habría 2 cerebros en la ventana entre mkdir y echo pid.
  if [ -z "$oldpid" ]; then log "lock recién tomado (pid pendiente) → salgo"; exit 0; fi
  if kill -0 "$oldpid" 2>/dev/null; then log "otro dispatcher vivo (pid $oldpid) → salgo"; exit 0; fi
  rm -rf "$LOCK" 2>/dev/null || true   # huérfano (PID muerto): el lockdir lleva pid dentro
  mkdir "$LOCK" 2>/dev/null || { log "no pude tomar el lock → salgo"; exit 0; }
fi
echo $$ >"$LOCK/pid"
trap 'rm -rf "$LOCK" 2>/dev/null || true' EXIT
log "dispatcher arriba (pid $$, state $STATE)"

backoff_get() { cat "$BACKOFF_F" 2>/dev/null || echo 0; }
backoff_reset() { echo 0 >"$BACKOFF_F"; }
backoff_bump() {
  local n; n="$(backoff_get)"; n=$((n + 1)); echo "$n" >"$BACKOFF_F"
  local s=$((2 ** (n < 6 ? n : 6))); [ "$s" -gt 1800 ] && s=1800   # cap 30 min
  log "back-off: fallo #$n → duermo ${s}s"; sleep "$s"
}

while true; do
  # 1. KILL-SWITCH antes de nada
  if halted; then log "HALT activo → paro el bucle"; break; fi
  heartbeat ""

  # 2. ¿saldo de PAGO? Si no queda, NO se bloquea en silencio: lo rutinario espera al reset
  #    diario y se avisa a {{TITULAR}} UNA vez al día (nunca un bloqueo mudo; ella puede subir el
  #    tope a un clic). Lo esencial (Vega) no pasa por aquí: es rutina-agente directa y baja
  #    sola a su suelo gratis en run_agent.sh.
  # El MOTIVO importa y antes se tiraba a /dev/null: el log decía «sin saldo de pago» siempre,
  # también cuando lo que pasaba era el tope diario que ponemos nosotros con el prepago sano. El
  # 20-sep-2026 eso se leyó como «la API no tiene crédito» y estuvo a un paso de acabar en
  # «recarga la cuenta». cost_guard ya distingue [tope_local] de [prepago_agotado]: se usa.
  CG_MOTIVO="$("$PY" "$TOOLS/cost_guard.py" check 2>>"$LOG/dispatcher.err")"
  if [ $? -ne 0 ]; then
    case "$CG_MOTIVO" in
      *"[prepago_agotado]"*) CG_QUE="prepago de la API agotado" ;;
      *"[tope_local]"*)      CG_QUE="tope de gasto diario alcanzado (el prepago está bien)" ;;
      *)                     CG_QUE="cost_guard dice que no: ${CG_MOTIVO:-sin motivo}" ;;
    esac
    FLAG="$STATE/dispatcher/aviso-tope-$(date +%F).flag"
    if [ ! -e "$FLAG" ] && [ -f "$STATE/notif/config.json" ]; then
      case "$CG_MOTIVO" in
        *"[prepago_agotado]"*)
          "$PY" "$TOOLS/salida.py" report "La cuenta de la API se ha quedado sin saldo, así que dejo en pausa los recados de rutina. Se recarga en console.anthropic.com → Billing. 💜" >/dev/null 2>&1 || true ;;
        *)
          "$PY" "$TOOLS/salida.py" report "He llegado al tope de gasto de hoy (\$$CAP), así que dejo en pausa los recados de rutina hasta esta noche (se reactivan solos). Si algo urge de verdad, dímelo y le subo el tope un momento. 💜" >/dev/null 2>&1 || true ;;
      esac
      : > "$FLAG" 2>/dev/null || true
    fi
    log "cost_guard: $CG_QUE → pauso rutina 60s"; [ -n "${BTP_ONCE:-}" ] && break; sleep 60; continue
  fi

  # 3. tomar UN job
  JOB="$("$PY" "$TOOLS/cola.py" dequeue 2>>"$LOG/dispatcher.err")" || JOB=""
  if [ -z "$JOB" ]; then [ -n "${BTP_ONCE:-}" ] && break; sleep "$IDLE"; continue; fi
  id="$(printf '%s' "$JOB" | jq -r '.id')"
  intencion="$(printf '%s' "$JOB" | jq -r '.intencion')"
  agente="$(printf '%s' "$JOB" | jq -r '.agente // empty')"
  modelo="$(printf '%s' "$JOB" | jq -r '.modelo // empty')"
  perfil="$(printf '%s' "$JOB" | jq -r '.perfil // "privileged"')"
  topejob="$(printf '%s' "$JOB" | jq -r '.tope_job_usd // empty')"
  tipo="$(printf '%s' "$JOB" | jq -r '.tipo // "exec"')"
  criticidad="$(printf '%s' "$JOB" | jq -r '.criticidad // "rutina"')"
  # Presupuesto de turnos del job (31-jul-26): vacío salvo que una vuelta anterior muriera por
  # `error_max_turns` y `cola.mark_failed` subiera de marcha. run_agent.sh ya respeta
  # BTP_MAX_TURNS por encima de su default (25 rutina / 60 crítico).
  turnos_job="$(printf '%s' "$JOB" | jq -r '.turnos // empty')"
  # Qué tiene que existir para poder cerrar el job (20-sep-26). Vacío = comportamiento de siempre.
  prueba="$(printf '%s' "$JOB" | jq -c '.prueba // empty' 2>/dev/null)"
  heartbeat "$id"

  # 4. RE-chequeo HALT justo antes de lanzar (ventana mínima) → si aparece, devuelvo el job
  if halted; then "$PY" "$TOOLS/cola.py" requeue --id "$id" >/dev/null 2>&1; log "HALT pre-run → requeue $id"; break; fi

  # 5. lanzar el agente (con el muro dentro) y capturar el JSON de stdout.
  #    Los jobs `exec` arrancan con el CONTEXTO del lazo (brújula + continuidad) para no
  #    ser amnésicos ni disparar a ciegas; los `triage` van crudos a cuarentena.
  #    Y si el job YA falló antes, va con su DIARIO de intentos: un reintento amnésico repite el
  #    mismo camino que ya falló y quema intentos hasta el dead-letter (25-jul-26).
  diario="$(printf '%s' "$JOB" | "$PY" "$TOOLS/cola.py" diario 2>/dev/null)" || diario=""
  if [ "$tipo" = "exec" ]; then
    ctx="$("$PY" "$TOOLS/contexto_lazo.py" 2>/dev/null)"
    prompt="${ctx}"$'\n\n'"${intencion}"
  else
    prompt="$intencion"
  fi
  if [ -n "$diario" ]; then prompt="${prompt}"$'\n\n'"${diario}"; fi
  log "lanzo job $id [tipo=$tipo perfil=$perfil agente=${agente:-} modelo=${modelo:-} criticidad=$criticidad]"
  # BTP_COST_GUARDED=1: el dispatcher ya hace cost_guard check+add (pasos 2 y 6) → que
  # run_agent.sh NO lo repita (evita doble conteo). Las rutinas-agente directas de launchd
  # no llevan este flag, así que allí sí lo aplica run_agent.sh.
  # BTP_CRITICIDAD: el freno de criticidad — un job 'critico' que no puede ir a un cerebro capaz
  # se BLOQUEA (no se sirve flojo); run_agent devuelve rc 75 con heartbeat 'critico_bloqueado'.
  _OBS_TS_JOB="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  # Instante de referencia de la prueba: justo ANTES de lanzar. No vale `creado` — un job
  # reencolado daría por buena una huella que dejó otro proceso hace dos días.
  _TS_PRE_RUN="$(date +%s)"
  # LATIDO DURANTE EL JOB: el bucle solo latía ENTRE jobs, así que un job largo dejaba el latido
  # envejeciendo mientras el motor estaba perfectamente vivo. Medido sobre 1604 corridas: la más
  # larga fue 451 s, el 75% del umbral de 600 s del dead-man — margen, no seguridad. Un refresco en
  # segundo plano cada 60 s separa las dos preguntas que se estaban confundiendo: "¿sigue vivo el
  # dispatcher?" (esto) y "¿este job tarda demasiado?" (el rc/timeout del propio run). El PID del
  # refrescador va al fichero para que `cola.reap_stuck` sepa que el job SIGUE corriendo y no lo
  # rescate por debajo (doble ejecución y doble cobro).
  ( while :; do sleep "$HB_INTERVAL"; heartbeat "$id" || break; done ) & _hb_pid=$!
  # `env` y no asignaciones-prefijo sueltas: bash decide qué palabra es una asignación en el
  # PARSEO, antes de expandir. `${turnos_job:+BTP_MAX_TURNS="$turnos_job"}` empieza por `$`, así
  # que nunca se reconoce como NAME=VALUE: al expandir a `BTP_MAX_TURNS=50` bash intentaba
  # EJECUTARLO como comando y devolvía rc=127. Solo saltaba en el REINTENTO de un job que ya
  # había perdido por error_max_turns (cola.py mark_failed pone turnos=50 y reencola), así que el
  # intento 1 corría bien y el bug parecía intermitente. Estaba detrás de jobs_caidos:rc=127,
  # frescura_agente_fallo:tecnico y frescura_agente_fallo:calendar-sync. `env` recibe las
  # asignaciones como argumentos y las aplica DESPUÉS de expandir, que es lo que hacía falta.
  OUT="$(env MURO_PROFILE="$perfil" BTP_AGENT="$agente" BTP_MODEL="$modelo" BTP_CRITICIDAD="$criticidad" BTP_COST_GUARDED=1 ${turnos_job:+BTP_MAX_TURNS="$turnos_job"} "$RUN_AGENT" "$prompt" 2>>"$LOG/dispatcher.err")"
  rc=$?
  kill "$_hb_pid" 2>/dev/null || true; wait "$_hb_pid" 2>/dev/null || true
  heartbeat ""   # job terminado: el latido deja de nombrarlo (reap_stuck vuelve a poder rescatarlo)

  # 6. CONTABILIZAR coste SIEMPRE (aun si falló: los tokens ya se gastaron) → pesimista si no hay JSON
  usd="$(printf '%s' "$OUT" | "$PY" "$TOOLS/cost_guard.py" add --stdin --job "$id" ${topejob:+--tope-job "$topejob"} 2>>"$LOG/dispatcher.err")" || usd="?"
  spent="$("$PY" "$TOOLS/cost_guard.py" today 2>/dev/null | jq -r '.gastado_usd // "?"')"

  # 7a. APLAZADO (rc 75 = EX_TEMPFAIL): Claude no disponible (sin saldo / límite). NO marcar done,
  # NO entregar resultado: devolver el job a la cola SIN gastar intento (requeue) → se reintenta
  # cuando Claude vuelva, sin perderse ni dead-letter. Aviso CALMADO 1×/día (no el de error).
  if [ "$rc" -eq 75 ]; then
    "$PY" "$TOOLS/cola.py" requeue --id "$id" >/dev/null 2>&1
    "$PY" "$TOOLS/panel.py" append --job "$id" --did "aplazado (Claude no disponible) → reintentar" --cost "\$$usd" >/dev/null 2>&1
    AVFLAG="$STATE/dispatcher/aviso-aplazado-$(date +%F).flag"
    if [ ! -e "$AVFLAG" ] && [ -f "$STATE/notif/config.json" ]; then
      "$PY" "$TOOLS/salida.py" report "El cerebro principal de esas tareas (hoy, Claude) está sin saldo ahora mismo, así que dejo en pausa lo que lo necesita y lo retomo en cuanto vuelva. Para tus preguntas sigo respondiendo con tus datos mientras tanto. No se pierde nada 💜" >/dev/null 2>&1
    fi
    : > "$AVFLAG" 2>/dev/null || true
    log "job $id APLAZADO (rc=75) → requeue (reintenta al volver Claude)"
    [ -z "${BTP_ONCE:-}" ] && backoff_bump
  # 7b. éxito = rc 0 y el JSON no marca is_error true
  elif [ "$rc" -eq 0 ] && ! printf '%s' "$OUT" | jq -e '.is_error==true' >/dev/null 2>&1; then
    if [ "$tipo" = "triage" ]; then
      # cuarentena → puente determinista: escala a privilegiado SOLO si sale seguro
      route="$(printf '%s' "$OUT" | "$PY" "$TOOLS/triage_route.py" 2>>"$LOG/dispatcher.err")" || route="rechazado error"
      did="triaje en cuarentena → $route"
    else
      did="agente ${agente:-orquestador} ejecutado"
    fi
    # 7b-bis. GATE DE ENTREGABLE (20-sep-26, deuda cola-marca-done-sin-verificar-cambio-real).
    # «Terminó sin error» no es «entregó». Un job que chocó contra el muro y contestó en prosa
    # salía limpio y se cerraba: 6,03 USD por un arreglo que nunca existió. Si el job declaró qué
    # debía quedar escrito, se mira el disco ANTES de cerrarlo. Sin `prueba`, nada cambia.
    # La cadena de error es FIJA y la escribe el dispatcher: si arrastrara "timeout" o
    # "connection", errores.clasificar la haría TRANSITORIO y el job se reintentaría.
    if [ -n "$prueba" ] && [ "${BTP_PRUEBA:-1}" != "0" ]; then
      if pmotivo="$("$PY" "$TOOLS/prueba_entregable.py" --prueba "$prueba" --desde "$_TS_PRE_RUN" 2>>"$LOG/dispatcher.err")"; then
        did="$did · entregable verificado"
      else
        pstatus=$?
        perr="sin-entregable"
        [ "$pstatus" -gt 1 ] && perr="verificacion-indisponible"
        res="$("$PY" "$TOOLS/cola.py" mark-failed --id "$id" --error "$perr" 2>/dev/null)"
        "$PY" "$TOOLS/panel.py" append --job "$id" --failed "ejecutó sin entregar: ${pmotivo:-sin motivo} ($res)" --cost "\$$usd" >/dev/null 2>&1
        log "job $id EJECUTÓ SIN ENTREGAR (coste \$$usd) · $perr · ${pmotivo:-}"
        [ -n "${BTP_ONCE:-}" ] && break
        continue
      fi
    fi
    "$PY" "$TOOLS/cola.py" mark-done --id "$id" --coste "${usd:-0}" >/dev/null 2>&1
    backoff_reset
    "$PY" "$TOOLS/panel.py" append --job "$id" --did "$did" \
      --cost "\$$usd (hoy \$$spent / \$$CAP)" >/dev/null 2>&1
    # Aviso por Telegram, gateado por la config opt-in (en tests, estado aislado sin
    # config → NO envía). El silencio nocturno lo aplica salida.report (no-urgente).
    # Aviso a {{TITULAR}} en voz humana (gateado por la config opt-in):
    #  · exec (encargo suyo) → le entrego la RESPUESTA real del agente (.result, lo único que lee).
    #  · triage (plumbing) → callado si escaló (el exec ya hablará); si se rechazó, aviso suave.
    if [ -f "$STATE/notif/config.json" ]; then
      if [ "$tipo" = "exec" ]; then
        msg="$(printf '%s' "$OUT" | jq -r '.result // empty' | head -c 3500)"
        if [ -z "$msg" ]; then
          FB=("Listo 💜 Me ocupé de lo que me dejaste y ya está." "Hecho 💜 Lo de antes ya está resuelto." "Ya está 💜 Lo dejé arreglado.")
          msg="${FB[$((RANDOM % 3))]}"
        fi
        "$PY" "$TOOLS/salida.py" report "$msg" >/dev/null 2>&1
      elif printf '%s' "${route:-}" | grep -qi 'rechaz'; then
        RJ=("Le he dado una vuelta a lo que me dejaste pero no me quedó claro del todo 💜 ¿Me lo cuentas con otras palabras?" "Lo he leído pero no acabo de pillar qué necesitas 💜 Dímelo de otra forma y voy." "Me he quedado a medias con lo que pedías 💜 ¿Me lo concretas un poco más?")
        "$PY" "$TOOLS/salida.py" report "${RJ[$((RANDOM % 3))]}" >/dev/null 2>&1
      fi
    fi
    log "job $id OK [$tipo] (coste \$$usd, hoy \$$spent) $did"
  else
    # El MOTIVO, no solo el número (31-jul-26). `rc=1` a secas no dice nada: el job c4fb4d06fc
    # estuvo 2 días en failed/ con «rc=1» y hubo que reconstruir por arqueología (coste, horas)
    # que lo que pasaba era que se quedaba sin turnos. El JSON del CLI ya trae `subtype` y
    # `num_turns` en la mano; se guardan para que la próxima vez se lea de un vistazo — y para que
    # `errores.clasificar` pueda mandar `error_max_turns` a dead-letter sin quemar 3 intentos.
    err="rc=$rc"
    sub="$(printf '%s' "$OUT" | jq -r '.subtype // empty' 2>/dev/null)"
    turnos="$(printf '%s' "$OUT" | jq -r '.num_turns // empty' 2>/dev/null)"
    [ -n "$sub" ] && err="$err · $sub"
    [ -n "$turnos" ] && err="$err · ${turnos} turnos"
    res="$("$PY" "$TOOLS/cola.py" mark-failed --id "$id" --error "$err" 2>/dev/null)"
    "$PY" "$TOOLS/panel.py" append --job "$id" --failed "$err ($res)" --cost "\$$usd" >/dev/null 2>&1
    if [ -f "$STATE/notif/config.json" ]; then
      ERFLAG="$STATE/dispatcher/aviso-fallo-$(date +%F).flag"
      if [ ! -e "$ERFLAG" ]; then
        ER=("Uy, se me ha atragantado lo último que me dejaste 💜 ¿Me lo dices de otra forma y lo intento otra vez?" "Algo se me ha torcido con eso y no he podido sacarlo 💜 Vuelve a mandármelo y lo retomo." "Esto se me ha resistido 💜 Si me lo pones de otra manera, le doy otra vuelta.")
        "$PY" "$TOOLS/salida.py" report "${ER[$((RANDOM % 3))]}" >/dev/null 2>&1
        : > "$ERFLAG" 2>/dev/null || true
      fi
    fi
    log "job $id FALLÓ ($err, $res, coste \$$usd)"
    [ -z "${BTP_ONCE:-}" ] && backoff_bump
  fi

  # Observabilidad (pieza 8): traza del ciclo completo del dispatcher (agente, job-id, rc).
  # Silencioso (>/dev/null 2>&1 || true): un fallo de traza nunca detiene el bucle.
  if [ "$rc" -eq 0 ]; then _OBS_RC="ok"; elif [ "$rc" -eq 75 ]; then _OBS_RC="aplazado"; else _OBS_RC="fail"; fi
  "$PY" - "$TOOLS" "${agente:-orquestador}" "$id" "$_OBS_TS_JOB" "$_OBS_RC" \
    >/dev/null 2>/dev/null <<'_OBS_PY' || true
import sys, os
tools_dir, agente, job_id, ts_ini, resultado = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
sys.path.insert(0, tools_dir)
try:
    import observabilidad
    observabilidad.registrar(agente=agente, job=job_id, ts_ini=ts_ini, resultado=resultado)
except Exception:
    pass
_OBS_PY

  [ -n "${BTP_ONCE:-}" ] && break
done

log "dispatcher abajo (pid $$)"
exit 0
