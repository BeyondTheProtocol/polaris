#!/bin/bash
# tools/git_barrido.sh — el barrido diario de git (rutina com.btp.git-barrido), en dos mitades.
#
# POR QUÉ existe (22-sep-2026). La rutina era solo `run_agent.sh <prompt>`: el agente Haiku decidía
# y ejecutaba la poda. Pero `git-barrido.err` está lleno de «cadena de modelos agotada por límite →
# aplazo git»: los días de límite el agente no arranca y NADA se poda, así que los worktrees ya
# fusionados se acumulaban hasta que {{TITULAR}} los limpiaba a mano. La poda es determinista y de cero
# pérdida: no necesita un modelo. Va PRIMERO y sin depender de él; el criterio (juzgar cabos
# sueltos, surfacear lo que ella debe decidir) sigue siendo del agente, después.
#
# Orden: HALT → poda determinista (ramas.py autopoda) → agente (charter, capítulo «Barrido diario»).
# El código de salida es el del agente: la poda no falla la rutina, y run_agent ya sabe aplazarse.
set -u
REPO="${BTP_REPO:-$HOME/claudecode}"
PY="${BTP_PY:-/usr/bin/python3}"; [ -x "$PY" ] || PY=python3

# Kill-switch, igual que run_agent.sh: en pausa total no se muta el .git de nadie.
if [ -f "$REPO/.HALT" ] || [ -f "$HOME/.btp.HALT" ]; then
  echo "MURO: HALT activo → no arranco (ni poda)."; exit 0
fi

echo "── poda determinista de worktrees ($(date '+%Y-%m-%dT%H:%M:%S')) ──"
"$PY" "$REPO/tools/ramas.py" autopoda || echo "autopoda: rc=$? (sigo con el agente)"

exec "$REPO/tools/run_agent.sh" "$@"
