#!/bin/bash
# run_agent.sh — arranque SEGURO del agente Claude 24/7 de Polaris.
#
# Une todo lo de setup day: API key del Llavero + el MURO (settings.autonomous)
# como barrera (hook fail-closed) + kill-switch .HALT. NUNCA usa
# --dangerously-skip-permissions (el plan lo prohíbe: el hook es la barrera).
#
# ⚠️ NO ENCENDER el lazo sin: (1) backup en USB hecho, (2) validar el permission-mode
# en una pasada supervisada, (3) OK de {{TITULAR}}. Esto es la PIEZA, no el interruptor.
set -euo pipefail

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin"
# BTP_REPO = gancho de test (igual que BTP_CLAUDE_BIN); en producción va la casa base.
REPO="${BTP_REPO:-$HOME/claudecode}"
cd "$REPO"

# Kill-switch global: $REPO/.HALT (interno) o ~/.btp.HALT (externo, A1, el que ve {{TITULAR}}).
if [ -f "$REPO/.HALT" ] || [ -f "$HOME/.btp.HALT" ]; then echo "MURO: HALT activo → no arranco."; exit 0; fi

# --- BARANDA DE ANIDAMIENTO (26/6/26) ------------------------------------------
# Cada invocación de run_agent.sh sube BTP_AGENT_DEPTH en 1.
# Nivel 0 = el dispatcher/launchd lanza el primer agente.
# Nivel 1 = ese agente puede enqueue un sub-job (p. ej. orquestador → comite-medico).
# Nivel 2 = ese sub-job puede lanzar otro (p. ej. comite-medico → verificacion). MÁXIMO.
# Si una cadena rebasa el límite → RECHAZA (stderr + JSON is_error, rc 0 para no
# envenenar al dispatcher con un rc raro). Fail-safe: variable ausente = 0, NUNCA falla.
#   BTP_AGENT_DEPTH   — profundidad HEREDADA del agente padre (propagada automáticamente).
#   BTP_DEPTH_MAX     — límite configurable (default 2); tests pueden bajar a 1.
# El hijo recibe depth=padre+1 porque exportamos el valor incrementado ANTES de lanzar claude.
_DEPTH_ACTUAL="${BTP_AGENT_DEPTH:-0}"
_DEPTH_MAX="${BTP_DEPTH_MAX:-2}"
if [ "$((_DEPTH_ACTUAL + 0))" -gt "$((_DEPTH_MAX + 0))" ] 2>/dev/null; then
  echo "BARANDA: profundidad $_DEPTH_ACTUAL > $_DEPTH_MAX → rechazo agente '${BTP_AGENT:-?}' (recursión desbocada)." >&2
  echo '{"result":"baranda de anidamiento: job rechazado por exceso de profundidad (ver BTP_DEPTH_MAX)","total_cost_usd":0.0,"is_error":true}'
  exit 0
fi
export BTP_AGENT_DEPTH="$((_DEPTH_ACTUAL + 1))"
# -------------------------------------------------------------------------------

# Perfil del muro: privileged (orquestador/trabajo) o quarantine (lee contenido no confiable).
PROFILE="${MURO_PROFILE:-privileged}"
SETTINGS="$REPO/.claude/settings.autonomous.json"
[ "$PROFILE" = "quarantine" ] && SETTINGS="$REPO/.claude/settings.quarantine.json"
# FASE 1 (14-jul-26): override de settings POR RUTINA, para pilotar la apertura de permisos
# en UNA sola (el radar) sin abrirsela a todo el lazo. Solo admite un NOMBRE de fichero bajo
# .claude/ (no una ruta arbitraria) -> no se puede apuntar fuera del repo.
if [ -n "${BTP_SETTINGS:-}" ]; then
  _cand="$REPO/.claude/$(basename "$BTP_SETTINGS")"
  [ -f "$_cand" ] && SETTINGS="$_cand"
fi
export MURO_PROFILE="$PROFILE"
[ -f "$SETTINGS" ] || { echo "Falta $SETTINGS"; exit 1; }

# Python del repo (stdlib) — definido AQUÍ ARRIBA porque la caja de arquitecto y el gate lo usan.
PY="$REPO/.venv/bin/python3"; [ -x "$PY" ] || PY="$(command -v python3 || echo python3)"

# --- CAJA DE ARQUITECTO (orquestador intercambiable) ---------------------------------------
# El arquitecto es una CAJA (tools/orquestadores.json): de la elegida (BTP_ORQUESTADOR, default
# 'claude') se toman binario, secreto del Llavero y variable de auth. Fallback 100% retrocompatible:
# si el registro falta/ilegible o la caja no existe, valen los defaults de Claude de siempre. El MURO
# NO depende de esto (es estructural en borde/cost_guard/HALT). Cambiar de arquitecto REAL (no-Claude)
# va por el gateway, no por aquí (los flags de abajo son del CLI de Claude Code).
ORQ="${BTP_ORQUESTADOR:-claude}"
ORQ_REG="${BTP_ORQUESTADORES:-$REPO/tools/orquestadores.json}"
CMD="claude"; SECRET="btp-anthropic-api"; AUTH_ENV="ANTHROPIC_API_KEY"
# VIA = con qué se paga este run. "api" = dinero real por uso (lo gatean los topes de cost_guard);
# "suscripcion" = cuota de un plan YA pagado (Max) → € marginal 0, no toca el presupuesto de dinero.
# Sale del campo `medido` del registro. FAIL-CLOSED por partida doble: si el registro no se puede
# leer, si la caja no declara `medido`, o si el valor no es exactamente false → cuenta como DINERO.
VIA="api"
ORQ_LINE="$("$PY" - "$ORQ_REG" "$ORQ" 2>/dev/null <<'PYEOF' || true
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
for o in d.get("orquestadores", []):
    if o.get("name") == sys.argv[2] and o.get("enabled", True):
        print("\t".join([o.get("cmd", "claude"), o.get("secret", "btp-anthropic-api"),
                         o.get("auth_env", "ANTHROPIC_API_KEY"),
                         "api" if o.get("medido", True) is not False else "suscripcion"]))
        break
PYEOF
)"
if [ -n "$ORQ_LINE" ]; then
  CMD="$(printf '%s' "$ORQ_LINE" | cut -f1)"
  SECRET="$(printf '%s' "$ORQ_LINE" | cut -f2)"
  AUTH_ENV="$(printf '%s' "$ORQ_LINE" | cut -f3)"
  _VIA_REG="$(printf '%s' "$ORQ_LINE" | cut -f4)"
  [ "$_VIA_REG" = "suscripcion" ] && VIA="suscripcion"
fi

# Auth: clave del Llavero según la caja de arquitecto (estable para 24/7, no OAuth que caduca).
# BTP_API_KEY_OVERRIDE = gancho de test (igual que BTP_CLAUDE_BIN); en producción va el Llavero.
KEY="${BTP_API_KEY_OVERRIDE:-$(security find-generic-password -s "$SECRET" -w 2>/dev/null || true)}"
# Gancho de test (como BTP_CLAUDE_BIN/BTP_API_KEY_OVERRIDE): simula el secreto del orquestador NO
# default ausente en el Llavero, para probar la salvaguarda de abajo sin tocar el Llavero real.
[ -n "${BTP_ORQ_TOKEN_MISSING:-}" ] && [ "$ORQ" != "claude" ] && KEY=""
# SALVAGUARDA de disponibilidad (piloto de auth por suscripción): si el orquestador ELEGIDO no es el
# 'claude' por defecto y su secreto NO está en el Llavero (p. ej. el token de `claude setup-token`
# aún no generado, o caducado a los 12 meses y borrado), NO se rompe el daemon → cae de vuelta a
# 'claude' (API medida, siempre disponible) y avisa una vez al día. Regla feedback-credito-no-bloquea-
# degrada / auto-mejora-no-degradar-silencio: un daemon NUNCA se queda mudo por un fallo de auth de la
# vía barata. (El default 'claude' sin su API key SÍ es error de config → exit 1: no hay a qué caer.)
if [ -z "$KEY" ] && [ "$ORQ" != "claude" ]; then
  _FBFLAG="${BTP_STATE_DIR:-$REPO/tools/state}/dispatcher/aviso-orq-${ORQ}-sin-token-$(date +%F).flag"
  if [ ! -f "$_FBFLAG" ]; then
    mkdir -p "$(dirname "$_FBFLAG")" 2>/dev/null && : > "$_FBFLAG" 2>/dev/null || true
    echo "run_agent: orquestador '$ORQ' sin '$SECRET' en el Llavero → sigo con la API medida (claude). Regenera el token con 'claude setup-token' para volver a la suscripción." >&2
  fi
  ORQ="claude"; CMD="claude"; SECRET="btp-anthropic-api"; AUTH_ENV="ANTHROPIC_API_KEY"
  VIA="api"   # al caer a la API medida esto YA es dinero real: el contador vuelve al cubo de siempre
  KEY="${BTP_API_KEY_OVERRIDE:-$(security find-generic-password -s "$SECRET" -w 2>/dev/null || true)}"
fi
[ -n "$KEY" ] || { echo "Falta $SECRET en el Llavero."; exit 1; }
export "$AUTH_ENV=$KEY"

PROMPT="${1:-Revisa la cola de trabajo y avanza lo autónomo según CLAUDE.md y el muro. Para en el gate de salida.}"

# Nombre del HEARTBEAT (rastro de vida) — por defecto = el agente, así que sin tocar nada se
# comporta igual que siempre. Override con BTP_HEARTBEAT_NAME para SEPARAR el latido de varios
# plists que comparten BTP_AGENT (p. ej. correo-urgente usa --agent asistente pero NO debe escribir
# en asistente.json: si revienta su tope de turnos, el vigía lo leería como "el barrido diario de
# Vega falló" cuando el barrido de las 7:55 está perfecto). El agente (BTP_AGENT) sigue mandando en
# la lógica (modelo/esencial/clínico); esto solo cambia el FICHERO de latido.
HB_NAME="${BTP_HEARTBEAT_NAME:-${BTP_AGENT:-asistente}}"

# ($PY ya se definió arriba, junto a la resolución de la caja de arquitecto.)

# --- PAUSA NOCTURNA (opt-in: BTP_QUIET_NIGHT) ----------------------------------
# Los monitores RUIDOSOS en bucle (com.btp.correo-urgente) NO deben encender el LLM de madrugada:
# {{TITULAR}} duerme (silencio 23-8) y el correo urgente lo recoge igual la pasada COMPLETA de Vega de las
# 07:55 + el parte HOY de las 08:10. En la ventana 23:00–07:59 (hora LOCAL) salimos GRATIS (rc 0, JSON
# neutro, rastro de vida). OPT-IN por plist → NUNCA afecta a lo clínico/crítico ni a la pasada diaria
# (no llevan el flag). Es defensa de COSTE, no del muro (que es el HALT de arriba).
if [ "${BTP_QUIET_NIGHT:-}" = "1" ]; then
  H="$(date +%H)"   # 10#$H fuerza base-10 (08/09 no son octal válido y reventarían el aritmético).
  if [ "$((10#$H))" -ge 23 ] || [ "$((10#$H))" -lt 8 ]; then
    HBDIR="${BTP_STATE_DIR:-$REPO/tools/state}/heartbeat"; mkdir -p "$HBDIR" 2>/dev/null || true
    printf '{"agente":"%s","ts":"%s","estado":"quiet_nocturno","modelo":"%s"}\n' \
      "$HB_NAME" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${BTP_MODEL:-sonnet}" \
      >"$HBDIR/$HB_NAME.json" 2>/dev/null || true
    echo '{"result":"pausa nocturna (23-8): no se encendió el LLM","total_cost_usd":0.0,"is_error":false}'
    exit 0
  fi
fi

# --- GATE DETERMINISTA (opt-in: BTP_GATE) --------------------------------------
# Frugalidad sin perder fiabilidad (23/6/26): los monitores que arrancan en bucle (p. ej.
# com.btp.correo-urgente, ~cada 15 min) no deben encender el LLM si NO hay novedad. El
# "mirar" es gratis (tools/vega_gate.py: solo mira mtime/hash de ficheros locales, 0 tokens,
# 0 red); solo el "pensar" gasta. Si el gate dice "sin novedad" → salimos GRATIS (rc 0, sin
# tocar el cost_guard ni el LLM) y dejamos rastro de vida. Si dice "novedad" → seguimos y le
# inyectamos al prompt SOLO el delta (qué cambió), no todo otra vez.
#   FAIL-SAFE: ante CUALQUIER duda el gate devuelve "novedad" (primera pasada, fuente ilegible,
#   marcador corrupto, sin fuentes) → nunca se cae un hilo crítico por ahorrar. La pasada
#   COMPLETA 1×/día (com.btp.asistente 07:55) NO lleva BTP_GATE: corre siempre.
if [ "${BTP_GATE:-}" = "1" ]; then
  GATE_BIN="${BTP_VEGA_GATE:-$REPO/tools/vega_gate.py}"
  if [ -f "$GATE_BIN" ]; then
    if "$PY" "$GATE_BIN" check >/dev/null 2>&1; then
      # rc 0 = hay novedad → inyecta el delta al prompt (best-effort; si falla, prompt intacto).
      DELTA="$("$PY" "$GATE_BIN" delta 2>/dev/null || true)"
      [ -n "$DELTA" ] && PROMPT="$PROMPT"$'\n\n'"$DELTA"
    else
      # rc 1 = sin novedad → no se enciende el LLM. Rastro de vida + JSON neutro a stdout
      # (coste 0) para no contaminar a quien parsee la salida. Marcador intacto (no cambió nada).
      HBDIR="${BTP_STATE_DIR:-$REPO/tools/state}/heartbeat"; mkdir -p "$HBDIR" 2>/dev/null || true
      printf '{"agente":"%s","ts":"%s","estado":"gate_sin_novedad","modelo":"%s"}\n' \
        "$HB_NAME" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${BTP_MODEL:-sonnet}" \
        >"$HBDIR/$HB_NAME.json" 2>/dev/null || true
      echo '{"result":"gate: sin novedad, no se encendió el LLM","total_cost_usd":0.0,"is_error":false}'
      exit 0
    fi
  fi
fi

# El HOOK del muro (settings.autonomous) deniega lo peligroso (exit 2, fail-closed).
# permission-mode a validar en P1 antes de encender de forma desatendida.
# Agente/modelo opcionales (los pasan los plists del gabinete por env).
# --max-turns acota el coste de UN run (defensa en profundidad sobre el cost_guard).
# Carril barato por DEFECTO: si el job no fija modelo, va en Sonnet (lo rutinario no necesita
# Opus). Opus solo cuando el job lo pide explícitamente (clínico/muro/plan-primero → el
# orquestador lanza un sub-job con modelo=opus). Esto corta el grueso del gasto del lazo.
# Modelo pedido + CADENA de degradación (plan F0-mínima: Fable→Opus→Sonnet→Haiku según el tier
# pedido). Si un intento topa con un LÍMITE de capacidad (429/529/overloaded/usage-limit) y NO
# es saldo agotado, se baja al siguiente modelo más barato en vez de morir; si se agota la
# cadena, se APLAZA (no muere callado — fue lo que tumbó al comité de Zúrich). El carril clínico
# degrada con AVISO RUIDOSO (fiabilidad reducida), nunca en silencio.
# Fable = tier de MÁXIMA potencia (por encima de Opus, verificado en vivo 2/jul/26 — `claude
# --model fable` responde en esta cuenta); si Fable falla/limita, degrada a Opus y luego Sonnet.
MODELO_PEDIDO="${BTP_MODEL:-sonnet}"
case "$MODELO_PEDIDO" in
  fable*)  CADENA=(fable opus sonnet) ;;
  opus*)   CADENA=(opus sonnet haiku) ;;
  sonnet*) CADENA=(sonnet haiku) ;;
  haiku*)  CADENA=(haiku) ;;
  "")      CADENA=(sonnet haiku) ;;
  *)       CADENA=("$MODELO_PEDIDO") ;;   # modelo explícito no estándar → no se degrada
esac
MODELO="$MODELO_PEDIDO"   # el bucle lo actualiza al modelo realmente usado (heartbeat/coste)
# ¿carril clínico? (degradación ruidosa) — por env o por agente del comité clínico.
CLINICO="${BTP_CLINICO:-}"
case "${BTP_AGENT:-}" in
  comite-medico|oncologo-virtual|verificacion|herramientas-medicas) CLINICO=1 ;;
esac
# Opt-in de LECTURA del clínico para el muro (check_read/check_subcommand en
# muro_guard.py). SOLO el comité clínico puede leer 00_Salud/historial; el resto del
# lazo lo tiene vedado (el egress ya está cerrado, esto cierra también la lectura).
[ -n "$CLINICO" ] && export MURO_ALLOW_CLINICAL=1
# 🔴 ¿tarea CRÍTICA? (freno de criticidad — seguridad clínica). Por BTP_CRITICIDAD=critico (lo pone
# el dispatcher desde el campo del job) o por ser clínica. Una tarea crítica NUNCA se sirve con un
# cerebro flojo: si Claude no está, PARA (exit 75 + estado 'critico_bloqueado' + aviso FUERTE), no
# degrada. 'rutina' (default) sigue como hoy (degrada-y-sirve). Fail-safe: solo crítico bloquea.
CRITICO=""
[ "${BTP_CRITICIDAD:-rutina}" = "critico" ] && CRITICO=1
[ -n "$CLINICO" ] && CRITICO=1

# GUARDIÁN nivel_min (25-jul-26): el registro dice desde el 17-jul que 'claude-suscripcion' es SOLO
# rutinario ("NUNCA clinico/critico"), pero ESO NO ESTABA ESCRITO EN NINGÚN SITIO — un plist podía
# poner el comité médico en la suscripción y nadie lo paraba (fail-open). Ahora se cumple aquí: si el
# job es CLÍNICO/CRÍTICO y la caja elegida no llega a ese nivel, se vuelve a la API medida (siempre
# disponible mientras haya prepago) en vez de arriesgar que una cuota agotada corte un análisis
# clínico a medias. Es el mismo criterio del muro: para lo clínico manda la fiabilidad, no el ahorro.
if [ -n "$CRITICO" ] && [ "$ORQ" != "claude" ]; then
  NIVEL_MIN="$("$PY" - "$ORQ_REG" "$ORQ" 2>/dev/null <<'PYEOF' || true
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
for o in d.get("orquestadores", []):
    if o.get("name") == sys.argv[2]:
        print(o.get("nivel_min", "rutina"))
        break
PYEOF
)"
  if [ "$NIVEL_MIN" != "critico" ]; then
    echo "run_agent: job CRÍTICO/CLÍNICO (${BTP_AGENT:-?}) con arquitecto '$ORQ' (nivel_min=${NIVEL_MIN:-rutina}) → vuelvo a la API medida (fiabilidad > ahorro)." >&2
    ORQ="claude"; CMD="claude"; SECRET="btp-anthropic-api"; AUTH_ENV="ANTHROPIC_API_KEY"; VIA="api"
    KEY="${BTP_API_KEY_OVERRIDE:-$(security find-generic-password -s "$SECRET" -w 2>/dev/null || true)}"
    [ -n "$KEY" ] && export "$AUTH_ENV=$KEY"
  fi
fi
# --max-turns acota el coste de UN run (defensa en profundidad sobre cost_guard). La RUTINA (correo,
# triaje, recordatorio…) no necesita runs largos → se acota a 25 para que un run que "se va de madre"
# no rebase el tope por-job (la fuga del 24/6: correo-urgente, 60 turnos → 3,58 USD con Task ya vetado
# por el muro, así que fue su PROPIO run el que se alargó). Lo CLÍNICO/CRÍTICO mantiene margen AMPLIO
# (60): truncar un análisis clínico a medias sería peor que el coste (el muro: fiabilidad clínica >
# ahorro). Un BTP_MAX_TURNS explícito (plist) manda siempre sobre este default.
if [ -n "$CRITICO" ]; then TURNOS_DEF=60; else TURNOS_DEF=25; fi
# Prompt-caching (auditoría de coste, 3/7/26): el CLI YA cachea el prefijo (system prompt +
# CLAUDE.md + el .md del --agent) AUTOMÁTICAMENTE, TTL 1h, aunque cada invocación sea un
# proceso nuevo (verificado empíricamente: llamadas repetidas con el MISMO --agent y prompt
# muestran cache_read_input_tokens subiendo hasta cache_creation=0). NO hay nada que tocar
# aquí. Se probó --exclude-dynamic-system-prompt-sections: con --agent el ratio de caché es
# IDÉNTICO con o sin la flag (el propio --help ya avisa que se ignora con --system-prompt;
# --agent se comporta igual a estos efectos) → NO se añade.
ARGS=(-p "$PROMPT" --settings "$SETTINGS" --permission-mode acceptEdits --output-format json
      --max-turns "${BTP_MAX_TURNS:-$TURNOS_DEF}")
if [ -n "${BTP_AGENT:-}" ]; then ARGS+=(--agent "$BTP_AGENT"); fi

# --- Coste + prueba de vida (asistente, Fase 0) --------------------------------
# ($PY ya se definió arriba, antes del gate.)
AGENT_NAME="${BTP_AGENT:-orquestador}"
HBDIR="${BTP_STATE_DIR:-$REPO/tools/state}/heartbeat"  # BTP_STATE_DIR unset en prod → misma ruta
mkdir -p "$HBDIR" 2>/dev/null || true
# Prueba de vida: escribe SIEMPRE a fichero, NUNCA a stdout (stdout = JSON del agente,
# que el dispatcher parsea con jq). Así un fallo silencioso deja rastro fresco/viejo.
# El FICHERO de latido es HB_NAME (= AGENT_NAME salvo que un plist ponga BTP_HEARTBEAT_NAME para
# separarse, p. ej. correo-urgente). La LÓGICA (esencial/clínico/modelo) sigue usando AGENT_NAME.
heartbeat() {
  local tmp="$HBDIR/.$HB_NAME.tmp"
  printf '{"agente":"%s","ts":"%s","estado":"%s","modelo":"%s"}\n' \
    "$HB_NAME" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$MODELO" >"$tmp" 2>/dev/null \
    && mv -f "$tmp" "$HBDIR/$HB_NAME.json" 2>/dev/null || true
}

# --- Respaldo por la CENTRALITA (solo trabajo DISCRETO, OPT-IN) -----------------------------
# OJO: las rutinas son AGÉNTICAS (necesitan el agente con herramientas). Un cerebro suelto de la
# centralita NO hace ese trabajo: si "completara" el job estaría FINGIENDO (y el dispatcher lo
# entregaría como real). Por eso el respaldo es OPT-IN: solo si el job marca BTP_FREE_OK=1 (=
# "este trabajo SÍ lo puede hacer un cerebro suelto"). Sin esa marca, run_agent APLAZA (exit 75) y
# el dispatcher lo reintenta cuando Claude vuelva. Clínico → jamás. (El carril GRATIS del bot no
# pasa por aquí: llama a ia.ask directamente para preguntas discretas.)
# A1 (10-jul-26, hallazgo de memoria/logging — fin de junio): un bug real dejó a la centralita
# (cerebro SIN herramientas) narrando en texto comandos de `git` como si los hubiera ejecutado,
# con heartbeat "sano" — indistinguible de un éxito real salvo cruzando con el log a mano. Hoy
# está mitigado porque NINGÚN .plist de un rol con shell activa BTP_FREE_OK=1 (verificado: ver
# tools/launchd/*.plist) — pero eso es "seguro por AUSENCIA de una variable en cada plist", no una
# regla dura. Esta lista negra excluye por CÓDIGO, sin mirar BTP_FREE_OK, los roles cuyo trabajo
# ES mutar el sistema de verdad (git/código) — un cerebro suelto "completándolo" sería FINGIR.
# NO incluye roles ya cubiertos por un opt-in probado y en producción (p.ej. `orquestador`, cuyo
# respaldo discreto vía centralita ya tiene test de regresión en test_run_agent_f2.py) — ampliar
# esta lista es una decisión del comité `git` + {{TITULAR}} (cambia comportamiento de producción).
BTP_CENTRALITA_BLACKLIST_DEFAULT="git tecnico constructor"
_en_blacklist_centralita() {  # $1 = nombre del agente
  local lista=" ${BTP_CENTRALITA_BLACKLIST:-$BTP_CENTRALITA_BLACKLIST_DEFAULT} "
  case "$lista" in *" $1 "*) return 0 ;; *) return 1 ;; esac
}

intentar_centralita() {  # $1 = motivo (para el log)
  _en_blacklist_centralita "${AGENT_NAME:-}" && return 1   # A1: exclusión estructural, ignora BTP_FREE_OK
  [ "${BTP_FREE_OK:-}" = "1" ] || return 1            # opt-in: solo trabajo marcado free-safe (discreto)
  [ -z "$CLINICO" ] || return 1                       # el muro: clínico jamás cae a un cerebro de nube
  [ -z "$CRITICO" ] || return 1                       # 🔴 freno: crítico jamás cae a un cerebro flojo
  local RESP
  RESP="$("$PY" -c 'import sys;sys.path.insert(0,sys.argv[1]);import ia;r=ia.ask(sys.argv[2],clinico=False);sys.stdout.write(r.get("text") or "")' "$REPO/tools" "$PROMPT" 2>/dev/null)"
  [ -n "$RESP" ] || return 1
  RESP="$RESP" "$PY" -c 'import json,os;print(json.dumps({"result":os.environ["RESP"],"total_cost_usd":0.0,"is_error":False},ensure_ascii=False))'
  echo "run_agent: Claude agotado ($1) → $AGENT_NAME respondido por la centralita (cerebro de respaldo, no clínico)." >&2
  heartbeat "centralita"
  return 0
}

# cost_guard: el dispatcher YA hace check+add. Si venimos de él (BTP_COST_GUARDED=1) NO lo
# repetimos (evita doble conteo). En las rutinas-agente DIRECTAS de launchd (hoy-compose,
# auto-mejora, prensa, radar) sí lo aplicamos aquí — antes era el agujero que se saltaba el tope.
GUARDED="${BTP_COST_GUARDED:-}"
if [ -z "$GUARDED" ]; then
  # ¿agente ESENCIAL? (vigía de Vega, healthcheck) → puede tirar de la reserva de gasto.
  ESENCIAL=""; case "$AGENT_NAME" in asistente|healthcheck) ESENCIAL="--esencial";; esac
  set +e
  CG_OUT="$("$PY" "$REPO/tools/cost_guard.py" check $ESENCIAL --via "$VIA" 2>/dev/null)"; CG_RC=$?
  set -e
  # 🔴 Regla de {{TITULAR}} (2/7/26): lo CLÍNICO/CRÍTICO/SENSIBLE NUNCA se corta por nuestro TOPE
  # DIARIO/MENSUAL interno cuando el saldo REAL de Anthropic está sano. El tope es una baranda de
  # gasto RUTINARIO, no un muro al goal. `cost_guard.py check` SOLO sabe ver el tope que nosotros
  # fijamos ([tope_local]) — el prepago real agotado ([prepago_agotado]/400 "Credit balance is too
  # low") NO lo puede detectar sin un round-trip a la API, y ese round-trip solo ocurre más abajo,
  # en el bucle de degradación real. Por eso, si CRITICO y el único motivo de bloqueo es el tope
  # local, IGNORAMOS este check y dejamos que el script siga su curso normal: el bucle de abajo
  # llama a Claude de verdad, y SOLO si la API devuelve el 400 real (is_credit_out) es cuando se
  # activa el bloqueo+aviso fuerte de "critico_bloqueado" (ver más abajo) — ese caso (a) SÍ debe
  # parar. El tope MENSUAL sigue aplicando a TODOS (no se toca aquí: cost_guard.py ya lo aplica
  # antes que el diario y seguimos devolviendo el mismo [tope_local] para él, así que un crítico
  # también lo salta — decisión deliberada: NED no se corta ni por el tope diario ni por el mensual,
  # el freno real es el saldo de Anthropic o la ausencia de un cerebro de confianza).
  if [ $CG_RC -ne 0 ] && [ -n "$CRITICO" ] && printf '%s' "$CG_OUT" | grep -q '\[tope_local\]'; then
    echo "run_agent: tarea CRÍTICA topó el TOPE interno pero el saldo real sigue sano → SALTO el tope, sigo a Claude ($AGENT_NAME)." >&2
  elif [ $CG_RC -ne 0 ]; then
    # SIN saldo de PAGO → no se gasta. Si el job es DISCRETO (BTP_FREE_OK=1) → respaldo por la
    # centralita. Si no (rutina agéntica) → APLAZA con exit 75 (EX_TEMPFAIL): el dispatcher lo
    # reintenta cuando haya saldo, sin fingir trabajo ni perderlo. Clínico nunca cae a la centralita.
    if intentar_centralita "sin saldo de pago"; then exit 0; fi
    # CST/SALIDA_PY se definen más abajo; aquí los necesitamos para el aviso crítico → defínelos local.
    CST="${BTP_STATE_DIR:-$REPO/tools/state}"; SALIDA_PY="${BTP_SALIDA:-$REPO/tools/salida.py}"
    if [ -n "$CRITICO" ]; then
      # Fail-safe: llegamos aquí solo si CRITICO topó el check por un motivo que NO reconocemos
      # como "[tope_local]" puro (hoy cost_guard.py check no genera otro marcador — defensa en
      # profundidad por si cambia). Ante la duda, seguridad clínica > saltar el tope a ciegas:
      # pausa + pide aprobación (comportamiento previo a este cambio), NUNCA corta en silencio.
      echo "run_agent: 🔴 tarea CRÍTICA topó el TOPE de gasto (motivo no identificado como tope local) → PAUSA $AGENT_NAME, pido aprobación." >&2
      mkdir -p "$CST/dispatcher" 2>/dev/null || true
      AVFLAG="$CST/dispatcher/aviso-aprob-gasto-$(date +%F).flag"
      if [ ! -e "$AVFLAG" ] && [ -f "$CST/notif/config.json" ]; then
        "$PY" "$SALIDA_PY" report-urgente \
          "🔴 Una tarea hacia NED ha tocado tu TOPE de gasto de hoy. La dejo EN PAUSA, no la corto. Si quieres que siga (gastar un poco por encima de lo normal hacia NED), dime «sube» y amplío el tope para que corra. No se pierde nada — el tope es una baranda, no un muro a NED." >/dev/null 2>&1 || true
      fi
      : > "$AVFLAG" 2>/dev/null || true
      heartbeat "critico_bloqueado"; exit 75
    fi
    if [ -n "$ESENCIAL" ]; then
      # Un job ESENCIAL hacia NED (Vega/healthcheck) topó el TOPE de gasto del día. NO se aplaza
      # MUDO (fue lo que dejó a {{TITULAR}} a ciegas el 25/6: Vega murió de hambre sin avisar). Se PIDE
      # APROBACIÓN de 1 clic — ella responde «sube» y bot_telegram amplía el tope SOLO-HOY
      # (cost_guard.aprobar_tope_hoy, DETERMINISTA, sin gastar Claude → sin deadlock). Anti-spam:
      # 1 aviso/día por flag (el job reintenta cada ciclo; sin flag el aviso saldría en bucle).
      echo "run_agent: tarea ESENCIAL ($AGENT_NAME) topó el TOPE de gasto → pido aprobación (no aplazo mudo)." >&2
      mkdir -p "$CST/dispatcher" 2>/dev/null || true
      AVFLAG="$CST/dispatcher/aviso-aprob-gasto-$(date +%F).flag"
      if [ ! -e "$AVFLAG" ] && [ -f "$CST/notif/config.json" ]; then
        "$PY" "$SALIDA_PY" report \
          "Vega ha tocado el tope de gasto de hoy, así que la he dejado en PAUSA (no se pierde nada). Si quieres que siga trabajando hacia NED el resto del día, respóndeme «sube» y amplío el tope solo por hoy. Mañana vuelve sola al normal. 💜" >/dev/null 2>&1 || true
      fi
      : > "$AVFLAG" 2>/dev/null || true
      heartbeat "aplazado_tope_local"; exit 75
    fi
    # Una RUTINA programada hacia NED (p. ej. auto-mejora) que se aplaza por falta de saldo NO
    # puede caerse EN SILENCIO día tras día (lección 24-26/6: la auto-mejora se quedó sin saldo y
    # nadie se enteró; encima una versión vieja registró basura del cerebro de respaldo como si
    # fuera la rutina real). Si el job lo pide (BTP_AVISA_APLAZO=1), avisamos a {{TITULAR}} 1×/día por su
    # vía normal (salida.py respeta el HALT) de que ESA rutina concreta no pudo correr por saldo y
    # que recargar es su gesto. Encaja con el carve-out crítico: lo importante no se degrada mudo.
    # Default OFF → para todo lo demás, comportamiento idéntico (aplazo callado, reintenta solo).
    if [ "${BTP_AVISA_APLAZO:-}" = "1" ]; then
      ETIQ_RUTINA="${BTP_RUTINA_NOMBRE:-la rutina de $AGENT_NAME}"
      echo "run_agent: rutina NED ($AGENT_NAME) sin saldo → aviso a {{TITULAR}} (no aplazo mudo)." >&2
      mkdir -p "$CST/dispatcher" 2>/dev/null || true
      AVFLAG="$CST/dispatcher/aviso-rutina-aplazada-$AGENT_NAME-$(date +%F).flag"
      if [ ! -e "$AVFLAG" ] && [ -f "$CST/notif/config.json" ]; then
        "$PY" "$SALIDA_PY" report \
          "Hoy no he podido correr $ETIQ_RUTINA porque ha tocado tu TOPE de gasto local de hoy (tu prepago SÍ tiene saldo — esto es la baranda, no el banco). No la he fingido ni medio-hecho: la dejo pendiente. Para desbloquearla, dime «sube» y amplío el tope solo por hoy. 💜" \
          >/dev/null 2>&1 || true
      fi
      : > "$AVFLAG" 2>/dev/null || true
      heartbeat "aplazado_tope_local"; exit 75
    fi
    echo "cost_guard: TOPE LOCAL de gasto alcanzado (NO es falta de saldo de prepago) → aplazo $AGENT_NAME (reintenta tras el reset diario o un «sube»)." >&2
    heartbeat "aplazado_tope_local"; exit 75
  fi
fi

# CST: raíz de estado (avisos/flags). Una sola definición (la usan el degradado y el crédito).
CST="${BTP_STATE_DIR:-$REPO/tools/state}"
SALIDA_PY="${BTP_SALIDA:-$REPO/tools/salida.py}"

# Aviso RUIDOSO al carril clínico cuando se degrada (fiabilidad reducida). Best-effort.
aviso_clinico() {  # $1 = modelo destino
  [ -n "$CLINICO" ] || return 0
  [ -f "$CST/notif/config.json" ] || return 0
  "$PY" "$SALIDA_PY" report \
    "Aviso: por un límite de capacidad estoy respondiendo el trabajo clínico con un modelo de menor potencia ($1). La fiabilidad puede bajar — conviene contrastarlo con tu equipo. 💜" \
    >/dev/null 2>&1 || true
}
# 🔴 Aviso FUERTE cuando se BLOQUEA una tarea crítica (no se sirve con un cerebro flojo). A
# diferencia del aplazado de rutina (calmado), esto es de SEGURIDAD CLÍNICA: ella tiene que
# enterarse, urgente, con QUÉ lo desbloquea. Best-effort (no rompe el exit 75 si la salida falla).
# Anti-spam (regla: TODO aviso a {{TITULAR}} necesita dedup — feedback-todo-aviso-a-titular-necesita-
# anti-spam): el dispatcher REINTENTA el job cada ciclo mientras Claude siga sin disponibilidad
# real, así que sin dedup este aviso salía en BUCLE (varias veces en segundos, visto en vivo el
# 26/6). REUSAMOS el mecanismo de cooldown por hash de motivo que ya usa ia.py::_parar (2/7/26:
# generalizado a `ia._debe_avisar`, expuesto aquí vía CLI) en vez de reinventar un flag nuevo —
# ventana de 1h (no 1/día: si el bloqueo se resuelve y reaparece por otra causa en la MISMA hora,
# igual avisa porque el hash del motivo cambia; si es el MISMO motivo persistente, no re-machaca).
aviso_critico_bloqueado() {  # $1 = motivo (saldo/límite)
  [ -f "$CST/notif/config.json" ] || return 0
  "$PY" "$REPO/tools/ia.py" --debe-avisar "run_agent_critico:$AGENT_NAME" 1 "$1" >/dev/null 2>&1 || return 0
  "$PY" "$SALIDA_PY" report-urgente \
    "🔴 He PARADO una tarea importante (clínica/crítica) en vez de atenderla con un cerebro de respaldo, porque el cerebro principal de esa tarea (hoy, Claude) no está disponible ($1). La dejo pendiente y la retomo en cuanto vuelva. Para desbloquearla ya: recarga el saldo de Anthropic. No se pierde nada." \
    >/dev/null 2>&1 || true
}
is_limit() {  # $1 = OUT — límite de capacidad/rate REINTENTABLE (NO el saldo agotado).
  printf '%s' "$1" | grep -qE '"api_error_status":[[:space:]]*(429|529)' && return 0
  printf '%s' "$1" | grep -qiE 'overloaded|rate[ _-]?limit|too many requests|usage limit|quota exceeded' && return 0
  return 1
}
is_credit_out() {  # $1 = OUT — saldo de prepago AGOTADO (ningún modelo más barato ayuda).
  printf '%s' "$1" | grep -q '"api_error_status":[[:space:]]*400' \
    && printf '%s' "$1" | grep -qi 'Credit balance is too low'
}
# 🩺 Agujero silencioso del carril clínico (evaluación de stack 6/7/26, rescatado 12/7): un
# clasificador de seguridad (bio/cyber) puede rechazar una petición LEGÍTIMA devolviendo HTTP 200
# con stop_reason:"refusal" y content VACÍO (la doc oficial de Anthropic confirma falsos positivos
# en life-sciences). Eso NO es api_error_status → is_credit_out/is_limit NO lo ven, y una pregunta
# clínica de {{TITULAR}} a oncologo-virtual/comite-medico moriría en silencio como "ok" hueco. Detección
# doble: (a) stop_reason explícito; (b) defensiva — éxito aparente sin "result" útil.
is_refusal() {  # $1 = OUT
  printf '%s' "$1" | grep -qi '"stop_reason"[[:space:]]*:[[:space:]]*"refusal"' && return 0
  printf '%s' "$1" | grep -qi '"stop_details"[^}]*"refusal"' && return 0
  return 1
}
is_respuesta_vacia() {  # $1 = OUT — defensivo: JSON "éxito" (is_error:false, sin api_error_status)
  # pero SIN "result" útil (un refusal pre-output no se factura ni deja resultado). Solo se evalúa
  # dentro del bucle/al cierre; un éxito real con "result" no vacío NUNCA cae aquí.
  printf '%s' "$1" | grep -q '"is_error":[[:space:]]*false' || return 1
  printf '%s' "$1" | grep -q '"api_error_status"' && return 1
  printf '%s' "$1" | grep -qE '"result":[[:space:]]*(""|null)' && return 0
  printf '%s' "$1" | grep -q '"result"' || return 0
  return 1
}

# --- Bucle de degradación: prueba la cadena hasta que uno responda o se agote --------------
# Observabilidad (pieza 8): marca el instante de inicio para calcular duración.
_OBS_TS_INI="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
heartbeat "arranca"
OUT=""; rc=0; DEGRADADO=0
N=${#CADENA[@]}
for i in "${!CADENA[@]}"; do
  M="${CADENA[$i]}"; MODELO="$M"
  set +e
  OUT="$("${BTP_CLAUDE_BIN:-$CMD}" "${ARGS[@]}" --model "$M")"; rc=$?   # BTP_CLAUDE_BIN = gancho de test; $CMD = binario de la caja
  set -e
  if is_credit_out "$OUT"; then break; fi          # saldo vacío → al manejador de crédito
  if is_limit "$OUT" && [ $((i + 1)) -lt "$N" ]; then
    SIG="${CADENA[$((i + 1))]}"
    echo "run_agent: límite de capacidad en $M → degrado a $SIG." >&2
    if [ "$DEGRADADO" = 0 ]; then aviso_clinico "$SIG"; fi
    DEGRADADO=1
    continue
  fi
  # 🩺 Refusal / respuesta hueca (no es límite ni crédito): si queda otro modelo, degrada y
  # reintenta LA MISMA petición (mismo patrón que is_limit). Cierra el agujero silencioso clínico.
  if (is_refusal "$OUT" || is_respuesta_vacia "$OUT") && [ $((i + 1)) -lt "$N" ]; then
    SIG="${CADENA[$((i + 1))]}"
    echo "run_agent: FALLBACK: refusal en $M → $SIG (respuesta hueca/rechazada)." >&2
    if [ "$DEGRADADO" = 0 ]; then aviso_clinico "$SIG"; fi
    DEGRADADO=1
    continue
  fi
  break                                            # éxito / fallo no degradable / último modelo
done

# Coste: registra lo que Claude consumió (sobre OUT; en error ≈ 0). ANTES de decidir la salida —
# OJO: aún NO imprimimos OUT, para no mezclar un JSON de error con el del respaldo de la centralita.
if [ -z "$GUARDED" ]; then
  printf '%s' "$OUT" | "$PY" "$REPO/tools/cost_guard.py" add --stdin \
    --job "${AGENT_NAME}-$(date +%Y%m%dT%H%M%S)" --via "$VIA" >/dev/null 2>&1 || true
fi

# 🔴 Claude AGOTADO: saldo de prepago a 0 (400 "Credit balance is too low") o cadena de modelos
# topada por LÍMITE. Antes aquí se aplazaba a secas (fue lo que mató al comité de Zúrich). Ahora,
# si el job NO es clínico, lo RELEVA la CENTRALITA (cerebro de respaldo, tras el borde). Clínico →
# aplaza EXACTAMENTE como antes (el muro): aviso + heartbeat, sin respaldo a nube.
if is_credit_out "$OUT" || is_limit "$OUT"; then
  if is_limit "$OUT"; then MOTIVO="límite de capacidad"; else MOTIVO="saldo de prepago agotado"; fi
  # Crédito agotado: avisa 1×/día SIEMPRE (ANTES de cualquier respaldo). Aunque el respaldo cubra
  # lo no-clínico, {{TITULAR}} debe saber que el saldo de PAGO está a 0 para recargar el carril de
  # calidad/clínico (que NO se releva). El flag corta el spam; salida.py respeta el muro.
  if is_credit_out "$OUT"; then
    mkdir -p "$CST/dispatcher" 2>/dev/null || true
    CFLAG="$CST/dispatcher/aviso-credito-$(date +%F).flag"
    if [ ! -e "$CFLAG" ] && [ -f "$CST/notif/config.json" ]; then
      "$PY" "$SALIDA_PY" report \
        "Se agotó el saldo de prepago de Anthropic. Lo de rutina sigue en marcha con un cerebro de respaldo, pero lo de calidad/clínico se queda en pausa. Cuando puedas, recarga un poco en la consola de Anthropic. 💜" \
        >/dev/null 2>&1 || true
    fi
    : > "$CFLAG" 2>/dev/null || true
  fi
  # Respaldo SOLO si es trabajo DISCRETO opt-in (BTP_FREE_OK). Clínico/crítico/agéntico → no.
  if intentar_centralita "$MOTIVO"; then exit 0; fi
  # APLAZAR con exit 75 (EX_TEMPFAIL): el dispatcher REINTENTA cuando Claude vuelva, sin fingir
  # trabajo (un 3B no hace lo agéntico) ni perder el job. printf OUT solo para que el cost-add lea
  # el coste real (~0 en error); el dispatcher NO lo entrega cuando ve rc=75.
  printf '%s' "$OUT"
  # 🔴 CRÍTICO: estado DISTINTO ('critico_bloqueado', no 'aplazado') + aviso FUERTE. No se ha
  # servido la tarea con un cerebro flojo: se PARÓ a propósito (seguridad clínica). El dispatcher
  # la requeue igual (rc 75), pero el heartbeat deja claro que fue un BLOQUEO, no un simple aplazo.
  if [ -n "$CRITICO" ]; then
    echo "run_agent: 🔴 tarea CRÍTICA y Claude no disponible ($MOTIVO) → BLOQUEO $AGENT_NAME (no la sirvo con un cerebro flojo)." >&2
    aviso_critico_bloqueado "$MOTIVO"
    heartbeat "critico_bloqueado"
    exit 75
  fi
  # Rutina NED opt-in (BTP_AVISA_APLAZO=1, p. ej. auto-mejora): aviso 1×/día NOMBRANDO la rutina
  # concreta, además del aviso-credito genérico. Así {{TITULAR}} sabe que ESA rutina hacia NED no corrió
  # (no solo "el saldo está a 0"). Mismo patrón anti-spam por flag. No clínico/crítico (esos van
  # arriba con su propio bloqueo fuerte).
  if [ "${BTP_AVISA_APLAZO:-}" = "1" ] && [ -z "$CRITICO" ]; then
    ETIQ_RUTINA="${BTP_RUTINA_NOMBRE:-la rutina de $AGENT_NAME}"
    mkdir -p "$CST/dispatcher" 2>/dev/null || true
    RFLAG="$CST/dispatcher/aviso-rutina-aplazada-$AGENT_NAME-$(date +%F).flag"
    if [ ! -e "$RFLAG" ] && [ -f "$CST/notif/config.json" ]; then
      "$PY" "$SALIDA_PY" report \
        "Hoy no he podido correr $ETIQ_RUTINA porque no quedaba saldo de Anthropic. No la he fingido ni medio-hecho: la dejo pendiente y la retomo en cuanto haya saldo. Para desbloquearla, recarga un poco en la consola de Anthropic cuando puedas. 💜" \
        >/dev/null 2>&1 || true
    fi
    : > "$RFLAG" 2>/dev/null || true
  fi
  if is_credit_out "$OUT"; then
    heartbeat "credito_agotado"
  else
    echo "run_agent: cadena de modelos agotada por límite → aplazo $AGENT_NAME (reintenta al volver Claude)." >&2
    aviso_clinico "ninguno disponible ahora"
    heartbeat "aplazado_limite"
  fi
  exit 75
fi

# 🩺 Cadena AGOTADA por refusal (agujero silencioso, 6/7/26): el ÚLTIMO modelo TAMBIÉN devolvió
# stop_reason:"refusal" o content vacío (is_error:false, sin api_error_status). No es crédito ni
# límite → el bloque de arriba no lo vio, y esto se colaría como "ok" con una respuesta HUECA (era
# EXACTAMENTE el agujero). Claude SÍ está disponible; lo que rechaza es el CONTENIDO. No se inventa
# escalado nuevo (la CADENA ya impone el tope): se APLAZA (exit 75, el dispatcher reintenta) y, si
# es carril CRÍTICO/clínico, se BLOQUEA con aviso fuerte (no se sirve una respuesta vacía).
if [ "$rc" -eq 0 ] && (is_refusal "$OUT" || is_respuesta_vacia "$OUT"); then
  printf '%s' "$OUT"                               # el JSON del refusal, para que el dispatcher vea el motivo
  if [ -n "$CRITICO" ]; then
    echo "run_agent: 🔴 refusal en toda la CADENA ($AGENT_NAME) → BLOQUEO (respuesta hueca, no la sirvo con un cerebro flojo)." >&2
    aviso_critico_bloqueado "clasificador de seguridad rechazó la petición (refusal) en toda la cadena de modelos"
    heartbeat "critico_bloqueado"
  else
    echo "run_agent: refusal en toda la CADENA ($AGENT_NAME) → aplazo (ningún modelo dio contenido)." >&2
    aviso_clinico "ninguno — la cadena rechazó el contenido (refusal)"
    heartbeat "aplazado_refusal"
  fi
  exit 75
fi

# Camino normal (Claude respondió): stdout EXACTO = JSON del agente (intacto para el dispatcher).
printf '%s' "$OUT"
# ÉXITO REAL (12-sep-2026, jobs_caidos "rc=1 · success · N turnos"): la fuente de verdad es el
# JSON que el propio Claude reporta (`is_error`), NO el exit code crudo del proceso. Un hook
# PreToolUse del MURO puede denegar una herramienta (exit 2, fail-closed, A PROPÓSITO) sin que
# eso impida que el modelo termine su turno y entregue un resultado válido — el binario `claude`
# puede salir con rc≠0 aunque su propio JSON diga "is_error":false (confirmado con
# claude-code-guide: el exit code no está documentado como reflejo fiel de `is_error` en ese
# caso). Con el criterio viejo (rc==0 Y is_error!=true) ese turno se contaba como FALLO: el
# dispatcher lo reintentaba 3 veces —siempre con el mismo resultado, porque el muro deniega
# igual cada vez— y el job acababa en failed/ con "rc=1 · success · N turnos": un no-op donde el
# muro hizo bien su trabajo, contado como avería. Verificado 12-sep-2026: 35/40 jobs de
# tools/state/queue/failed/ tenían exactamente ese patrón. Si `is_error` no es legible (crash,
# OUT vacío), se cae al criterio de siempre (rc==0) — no se inventa éxito de un JSON roto.
if printf '%s' "$OUT" | grep -q '"is_error":[[:space:]]*false'; then
  EXITO_REAL=1
elif [ "$rc" -eq 0 ] && ! printf '%s' "$OUT" | grep -q '"is_error":[[:space:]]*true'; then
  EXITO_REAL=1
else
  EXITO_REAL=0
fi
if [ "$EXITO_REAL" = 1 ]; then
  if [ "$DEGRADADO" = 1 ]; then heartbeat "ok_degradado"; else heartbeat "ok"; fi
  # GATE: el run con novedad terminó BIEN → sella "último visto" para que el próximo ciclo
  # parta de aquí. Solo al ÉXITO (si falló, el delta sigue pendiente y se reintenta).
  if [ "${BTP_GATE:-}" = "1" ]; then
    GATE_BIN="${BTP_VEGA_GATE:-$REPO/tools/vega_gate.py}"
    [ -f "$GATE_BIN" ] && "$PY" "$GATE_BIN" mark >/dev/null 2>&1 || true
  fi
  _OBS_RESULTADO="ok"
else
  # FALLO. Distinguimos el fallo ESTRUCTURAL no-recuperable (el run agotó --max-turns) del
  # transitorio (API/red/crédito). Un error_max_turns NO se arregla reintentando lo mismo: el
  # agente ya quemó todo su presupuesto de turnos. Si en un daemon GATEADO (correo-urgente) NO
  # sellamos el gate, el mismo delta (p. ej. un correo nuevo) lo re-dispara cada ciclo y re-gasta
  # ~$0,70 indefinidamente (la sangría 24/6→30-jun). Por eso: en error_max_turns con gate activo,
  # SELLA igual (corta la sangría). El resto de fallos NO sella → siguen reintentando (fail-safe).
  if printf '%s' "$OUT" | grep -q 'error_max_turns'; then
    heartbeat "fallo_max_turns"
    if [ "${BTP_GATE:-}" = "1" ]; then
      GATE_BIN="${BTP_VEGA_GATE:-$REPO/tools/vega_gate.py}"
      [ -f "$GATE_BIN" ] && "$PY" "$GATE_BIN" mark >/dev/null 2>&1 || true
    fi
  else
    heartbeat "fallo"
  fi
  _OBS_RESULTADO="fail"
fi

# Observabilidad (pieza 8): registra la traza sin tocar stdout ni alterar el rc.
# Los tokens y el coste se extraen del JSON de Claude (best-effort; falla en silencio).
# El `job` lleva el NOMBRE DEL TRABAJO (HB_NAME), no un "run_agent" a secas (31-jul-26). Varios
# plists corren con el mismo `--agent` pero son trabajos distintos: `com.btp.correo` invoca al
# agente `asistente` con BTP_HEARTBEAT_NAME=correo-triaje. Con el job en blanco, la bitácora
# atribuía sus fallos a «asistente» y el libro de deuda acabó con un hallazgo culpando a Vega de
# 58 fallos que en su mayoría eran del pase de correo. Un número mal atribuido manda a arreglar
# lo que no está roto.
"$PY" - "$REPO/tools" "$AGENT_NAME" "$MODELO" "$_OBS_TS_INI" "$_OBS_RESULTADO" "$HB_NAME" \
  >/dev/null 2>/dev/null <<'_OBS_PY' || true
import sys, os, json
tools_dir, agente, modelo, ts_ini, resultado = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
hb = sys.argv[6] if len(sys.argv) > 6 else ""
sys.path.insert(0, tools_dir)
try:
    import observabilidad
    # Intentar leer tokens del JSON que quedó en $OUT no es posible desde aquí (ya se imprimió).
    # Se registra solo agente/modelo/ts/resultado; el dispatcher añade coste por su lado.
    observabilidad.registrar(
        agente=agente,
        job=("run_agent:%s" % hb) if hb else "run_agent",
        ts_ini=ts_ini,
        modelo=modelo,
        resultado=resultado,
    )
except Exception:
    pass
_OBS_PY

# EXITO_REAL manda sobre el rc crudo (ver comentario arriba, 12-sep-2026): si el JSON dijo éxito,
# el dispatcher NO debe verlo como fallo aunque el proceso `claude` saliera con rc≠0 por una
# denegación del muro a mitad de turno. Si no hubo éxito real, se preserva el rc de siempre (el
# dispatcher lo usa para el motivo "rc=N · ...").
[ "$EXITO_REAL" = 1 ] && exit 0
exit "$rc"
