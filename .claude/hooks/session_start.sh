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
# `date -r <fichero>` vale en macOS y en Linux; `stat -f %m` era solo de Mac, y en Linux no falla:
# devuelve basura, la cuenta de abajo revienta y el hook sale con 1 (CI público rojo, 24-sep-26).
if [ ! -f "$DRAIN_STAMP" ] || [ "$(( $(date +%s) - $(date -r "$DRAIN_STAMP" +%s 2>/dev/null || echo 0) ))" -gt 900 ]; then
  touch "$DRAIN_STAMP" 2>/dev/null
  [ -f "$REPO/tools/reel_digest.py" ] && ( "$PY" "$REPO/tools/reel_digest.py" --drain --remote polaris --max 5 >/dev/null 2>&1 & )
fi

# Guardarraíl de topología (12-jul): el Air lleva .HALT a propósito (cliente congelado). Recuérdalo
# para no escribir en la copia equivocada — el trabajo real va en el mini.
# 24-sep: el HALT NO identifica la máquina. El CÓDIGO ROJO también lo pone, y en el mini con código
# rojo activo este aviso decía «este es el Air» (deuda sessionstart-miente-topologia). La máquina se
# identifica por hostname, como en tools/ff_al_abrir.sh y tools/mini.sh. BTP_HOSTNAME solo es para el test.
MAQUINA="${BTP_HOSTNAME:-$(hostname -s 2>/dev/null)}"
HALT_MSG=""
if [ -f "$REPO/.HALT" ] || [ -f "$HOME/.btp.HALT" ]; then
  if [ "$MAQUINA" = "Polaris" ]; then
    HALT_MSG=$'⚠️ HALT activo en el MINI (Polaris): código rojo o parada manual. El lazo 24/7 está parado y hay tests que se saltan por eso. Esto NO es el Air: el repo, el estado y la memoria son los canónicos.\n\n'
  else
    HALT_MSG=$'⚠️ TOPOLOGÍA: este es el PORTÁTIL (Air), cliente congelado a propósito (.HALT). El cerebro canónico 24/7 es el MINI (Polaris). Para trabajar de verdad —mismo repo, estado y memoria— abre `polaris-claude` (corre en el mini). Evita editar el estado local del Air (tareas, memoria): divergiría de la mini.\n\n'
  fi
fi

# Traspaso tras compactar (24-sep-26): SessionStart vuelve a disparar con source=compact. Si el hook
# PreCompact (precompact_traspaso.py) dejó el estado de ESTA sesión, va delante de todo: es lo que
# el /compact acaba de borrar (paso en el que íbamos, decisiones cerradas, lo sin commitear).
ENTRADA=""; [ -t 0 ] || ENTRADA="$(cat 2>/dev/null || true)"
TRASPASO=""
if [ -n "$ENTRADA" ]; then
  TRASPASO="$(printf '%s' "$ENTRADA" | (cd "$REPO" && "$PY" -c '
import json, sys
sys.path.insert(0, "tools")
e = json.loads(sys.stdin.read() or "{}")
if e.get("source") == "compact" and e.get("session_id"):
    import continuity
    t = continuity.traspaso_leer(e["session_id"]).strip()
    if t:
        print(t[:4000] + ("\n… (recortado: python3 tools/continuity.py traspaso <sid>)" if len(t) > 4000 else ""))
') 2>/dev/null || true)"
fi
[ -n "$TRASPASO" ] && TRASPASO="${TRASPASO}"$'\n\n'

CTX="$( (cd "$REPO" && "$PY" tools/contexto_lazo.py --brujula) 2>/dev/null || true )"
CTX="${HALT_MSG}${TRASPASO}${CTX}"
[ -n "$CTX" ] || exit 0

jq -n --arg ctx "$CTX" \
  '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}' \
  2>/dev/null || true
exit 0
