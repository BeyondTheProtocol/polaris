#!/bin/bash
# SessionStart hook (A1): inyecta la brújula NED (foco + cuello de botella + continuidad)
# al abrir cada sesión de chat, igual que el dispatcher ya hace con los jobs.
# Reusa tools/contexto_lazo.py --brujula. Sin LLM, sin red, coste 0.
# Fail-open: ante cualquier error, exit 0 (NUNCA bloquear la sesión).
set -uo pipefail

REPO="${BTP_REPO:-$HOME/claudecode}"
PY="$(command -v python3 || echo /usr/bin/python3)"

# Pull-al-abrir del playbook clínico (Fase 2): cose ESTADO-ACTUAL.md con la otra máquina
# antes de leerlo. Detachado + fail-open + throttled: NUNCA bloquea ni ralentiza la sesión.
[ -x "$REPO/tools/sync_al_abrir.sh" ] && ( "$REPO/tools/sync_al_abrir.sh" & ) >/dev/null 2>&1

# Drenaje auto de enlaces de vídeo (12-jul, aprobado por {{TITULAR}}): entiende los reels que manda por
# Telegram (caption + VOZ vía Whisper + TEXTO EN PANTALLA vía Apple Vision), SIN que esté pendiente.
# Detachado + fail-open + THROTTLE (1/15min) + cap 5/pasada. Opera sobre el buzón CANÓNICO del mini.
# MURO: la descarga usa la sesión de IG SOLO aquí — SessionStart solo corre en sesiones INTERACTIVAS
# (atendidas); las autónomas tienen SessionStart vacío → NUNCA toca la sesión en el lazo 24/7. En una
# máquina sin Chrome-IG la descarga falla y se difiere (⏳ intacto). Ver tools/reel_digest.py.
DRAIN_STAMP="${TMPDIR:-/tmp}/.btp_reel_drain.stamp"
if [ ! -f "$DRAIN_STAMP" ] || [ "$(( $(date +%s) - $(stat -f %m "$DRAIN_STAMP" 2>/dev/null || echo 0) ))" -gt 900 ]; then
  touch "$DRAIN_STAMP" 2>/dev/null
  [ -f "$REPO/tools/reel_digest.py" ] && ( "$PY" "$REPO/tools/reel_digest.py" --drain --remote polaris --max 5 >/dev/null 2>&1 & )
fi

# Guardarraíl de topología (12-jul): si este Mac tiene .HALT, es el PORTÁTIL (cliente congelado
# a propósito). Recuérdalo para no escribir en la copia equivocada — el trabajo real va en el mini.
HALT_MSG=""
if [ -f "$REPO/.HALT" ] || [ -f "$HOME/.btp.HALT" ]; then
  HALT_MSG=$'⚠️ TOPOLOGÍA: este es el PORTÁTIL (Air), cliente congelado a propósito (.HALT). El cerebro canónico 24/7 es el MINI (Polaris). Para trabajar de verdad —mismo repo, estado y memoria— abre `polaris-claude` (corre en el mini). Evita editar el estado local del Air (tareas, memoria): divergiría de la mini.\n\n'
fi

CTX="$( (cd "$REPO" && "$PY" tools/contexto_lazo.py --brujula) 2>/dev/null || true )"
CTX="${HALT_MSG}${CTX}"
[ -n "$CTX" ] || exit 0

jq -n --arg ctx "$CTX" \
  '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}' \
  2>/dev/null || true
exit 0
