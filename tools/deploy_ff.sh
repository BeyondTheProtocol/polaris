#!/usr/bin/env bash
# tools/deploy_ff.sh — despliegue de código ENTRE el Air y Polaris SOLO por fast-forward.
#
# Por qué existe: el fork Air↔Polaris del 12-jul se creó porque los deploys RE-COMMITEABAN
# el trabajo del otro lado (mensajes "…desplegado desde portatil c969ca0") en vez de mover
# la ref por fast-forward. Cada deploy ensanchaba la brecha y arriesgaba borrar trabajo.
#
# Esta herramienta NUNCA re-commitea, NUNCA reescribe mensajes, NUNCA hace push a GitHub.
# Mueve `master` de una máquina a la otra SOLO si es un fast-forward limpio. Si las dos
# han divergido (la otra tiene commits que esta no), REHÚSA y pide reconciliar a mano
# — así un fork nunca se crea en silencio: se ve y se para.
#
# Uso (desde el Air):
#   tools/deploy_ff.sh to-polaris     # manda el master del Air  → FF del master del mini
#   tools/deploy_ff.sh from-polaris   # trae  el master del mini → FF del master del Air
#
# Regla de oro: tras commitear en UNA máquina, despliega a la otra ANTES de que la otra
# commitee. Si ambas commitean a la vez, el siguiente deploy REHÚSA (no es FF) y toca
# reconciliar (como el 12-jul). Eso es lo correcto: forks ruidosos y raros, no silenciosos.
set -euo pipefail

DIR="${1:-}"
REMOTE="polaris"
LOCAL_REPO="$HOME/claudecode"
TS="$(date +%Y%m%d-%H%M%S)"
BUNDLE="/tmp/deploy-ff-$TS.bundle"

# Aplica un bundle recibido como FF sobre `master`. Args: $1=bundle $2=ts $3=etiqueta-maquina
_aplicar_ff='
set -e
cd ~/claudecode
BUNDLE="$1"; TS="$2"; ETQ="$3"
git bundle verify "$BUNDLE" >/dev/null 2>&1 || { echo "🛑 bundle corrupto"; exit 6; }
git fetch "$BUNDLE" master:"refs/heads/incoming/deploy-$TS" >/dev/null 2>&1
if ! git merge-base --is-ancestor master "incoming/deploy-$TS"; then
  echo "🛑 NO es fast-forward: $ETQ tiene commits que el otro lado no tiene."
  echo "   Reconcilia a mano (merge auditado) antes de desplegar. NO se ha tocado nada."
  git branch -D "incoming/deploy-$TS" >/dev/null 2>&1 || true
  exit 5
fi
CUR="$(git rev-parse --abbrev-ref HEAD)"
# Gate de la base (11-sep-26): el freno nativo para cualquier cambio de master sin OK. Aquí se
# abre a propósito y SOLO para este paso: es un fast-forward ya comprobado arriba hacia el master
# de la otra máquina, que pasó su propio gate al fusionarse allí. No crea contenido nuevo.
export BTP_GIT_BASE_OK=1
if [ "$CUR" = "master" ]; then
  git merge --ff-only "incoming/deploy-$TS" >/dev/null
else
  # master no está checked-out: mover la ref por FF sin tocar el árbol de trabajo
  git branch -f master "incoming/deploy-$TS"
  echo "  (master no estaba activo; ref FF-actualizada, árbol de trabajo intacto)"
fi
git branch -D "incoming/deploy-$TS" >/dev/null 2>&1 || true
rm -f "$BUNDLE"
echo "✔ $ETQ FF → $(git rev-parse --short master)"
'

case "$DIR" in
  to-polaris)
    cd "$LOCAL_REPO"
    git bundle create "$BUNDLE" master >/dev/null
    scp -q "$BUNDLE" "$REMOTE:$BUNDLE"
    rm -f "$BUNDLE"
    ssh "$REMOTE" 'bash -s' "$BUNDLE" "$TS" "mini" <<< "$_aplicar_ff"
    ;;
  from-polaris)
    ssh "$REMOTE" "cd ~/claudecode && git bundle create '$BUNDLE' master >/dev/null"
    scp -q "$REMOTE:$BUNDLE" "$BUNDLE"
    ssh "$REMOTE" "rm -f '$BUNDLE'" || true
    cd "$LOCAL_REPO"
    bash -c "$_aplicar_ff" _ "$BUNDLE" "$TS" "Air"
    ;;
  *)
    echo "uso: tools/deploy_ff.sh {to-polaris|from-polaris}" >&2
    exit 2
    ;;
esac
