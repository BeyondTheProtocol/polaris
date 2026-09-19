#!/bin/sh
# Activa el hook del muro para este repo y sus worktrees (config compartida).
# Acto de {{TITULAR}} (o con su OK): a partir de aquí, tocar CLAUDE.md exige BTP_MURO_OK=1.
set -e
cd "$(git rev-parse --show-toplevel)"
chmod +x tools/hooks/pre-commit tools/hooks/muro_commit_guard.py 2>/dev/null || true
git config core.hooksPath tools/hooks
echo "🧱 Hook del muro ACTIVADO (core.hooksPath=tools/hooks)."
echo "   Cambiar CLAUDE.md ahora exige:  BTP_MURO_OK=1 git commit …"
echo "   Desactivar (si hiciera falta):  git config --unset core.hooksPath"
