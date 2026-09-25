#!/bin/bash
# ff_al_abrir.sh — el PORTÁTIL se pone al día de código solo, al abrir una sesión.
#
# POR QUÉ (25-jul-26, lo pidió {{TITULAR}})
#   El mini (Polaris) es la fuente: se trabaja aquí y el Air no debe divergir. Pero la
#   conexión solo va en un sentido — el mini es fijo y el Air es portátil, con IP
#   cambiante y a menudo dormido —, así que el pull SIEMPRE lo inicia el Air. Hasta hoy
#   eso era un `tools/deploy_ff.sh from-polaris` a mano, y lo que se hace a mano se
#   olvida: el portátil se quedaba atrás y luego tocaba reconciliar.
#
# QUÉ HACE
#   Al abrir sesión en el Air, y como mucho una vez cada 30 min, trae el `master` del
#   mini por FAST-FORWARD. Nada más.
#
# LAS CUATRO GUARDAS (sin ellas esto sería peligroso, no cómodo)
#   1. Solo corre en el AIR. En el mini sale de inmediato: intentar `ssh polaris` desde
#      Polaris sería hablar consigo mismo.
#   2. Solo con el árbol LIMPIO. Si hay trabajo sin commitear, no se toca nada: mover
#      `master` bajo los pies de una sesión a medias es justo lo que no queremos.
#   3. Solo FAST-FORWARD. Lo garantiza `deploy_ff.sh`, que REHÚSA si las dos máquinas
#      han divergido. Un fork nunca se crea en silencio.
#   4. FAIL-OPEN y en segundo plano. Mini apagado, sin red, sin ssh: sale 0 y calla.
#      Nunca bloquea ni ralentiza el arranque de la sesión.
#
# Deja constancia en `.claude/logs/ff-al-abrir.log` (una línea por intento) para que se
# pueda auditar si el portátil se está poniendo al día de verdad o solo lo parece.
set -uo pipefail

REPO="${BTP_REPO:-$HOME/claudecode}"
REMOTO="${SYNC_REMOTO:-polaris}"
MIN="${FF_MIN:-30}"
LOG="$REPO/.claude/logs/ff-al-abrir.log"
STAMP="${TMPDIR:-/tmp}/.btp_ff_al_abrir.stamp"

apunta() { mkdir -p "$(dirname "$LOG")" 2>/dev/null; printf '%s\t%s\n' "$(date +%FT%T)" "$1" >>"$LOG" 2>/dev/null || true; }

# Guarda 1 — en el mini, nada que hacer.
if [ "$(hostname -s 2>/dev/null)" = "Polaris" ]; then
  exit 0
fi
# Refuerzo: sin alias ssh configurado no somos el Air (o no está el túnel montado).
grep -qiE "^host[[:space:]]+$REMOTO\b" "$HOME/.ssh/config" 2>/dev/null || exit 0

# Throttle: como mucho una vez cada $MIN minutos.
if [ -f "$STAMP" ]; then
  edad=$(( $(date +%s) - $(stat -f %m "$STAMP" 2>/dev/null || echo 0) ))
  [ "$edad" -lt $(( MIN * 60 )) ] && exit 0
fi
touch "$STAMP" 2>/dev/null

cd "$REPO" 2>/dev/null || exit 0

# Guarda 2 — árbol limpio (incluye ficheros sin seguir: un `??` también es trabajo).
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
  apunta "saltado: hay cambios sin commitear en el Air"
  exit 0
fi
# Y en master, no en una rama de trabajo.
rama="$(git branch --show-current 2>/dev/null)"
if [ "$rama" != "master" ]; then
  apunta "saltado: el Air está en la rama '$rama', no en master"
  exit 0
fi

antes="$(git rev-parse --short HEAD 2>/dev/null)"
# OJO: macOS NO trae `timeout` (ni `gtimeout` sin coreutils). Usarlo a secas hacía que el
# comando fallara SIEMPRE y el fail-open lo disimulaba: el log decía «sin efecto» y parecía
# el mini apagado. Se usa si está; si no, se corre directo — deploy_ff ya va con
# ConnectTimeout corto en su ssh, así que no se queda colgado.
CONTIMEOUT=""
command -v timeout  >/dev/null 2>&1 && CONTIMEOUT="timeout 120"
[ -z "$CONTIMEOUT" ] && command -v gtimeout >/dev/null 2>&1 && CONTIMEOUT="gtimeout 120"

# Guardas 3 y 4 las pone deploy_ff.sh: solo mueve master si es fast-forward limpio.
salida="$($CONTIMEOUT tools/deploy_ff.sh from-polaris 2>&1)" || {
  apunta "sin efecto: $(printf '%s' "$salida" | tail -1 | cut -c1-120)"
  exit 0
}
despues="$(git rev-parse --short HEAD 2>/dev/null)"

if [ "$antes" != "$despues" ]; then
  apunta "al día: $antes → $despues"
else
  apunta "ya estaba al día ($antes)"
fi
exit 0
