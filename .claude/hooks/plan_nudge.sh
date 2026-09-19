#!/bin/bash
# UserPromptSubmit hook: detecta señales de tarea de ALCANCE grande e inyecta un recordatorio de
# plan-primero. NO bloquea, NO cambia de modo (los hooks no pueden): solo añade additionalContext.
# El muro y el gate de salida siguen mandando. Fail-open: ante cualquier duda, exit 0 (no estorbar).
set -euo pipefail
INPUT="$(cat 2>/dev/null || true)"
PROMPT="$(printf '%s' "$INPUT" | jq -r '.prompt // .user_input // ""' 2>/dev/null | tr '[:upper:]' '[:lower:]' || true)"
[ -n "$PROMPT" ] || exit 0

REGEX='monta|montar|instala|instalar|activa(r)?|crea(r)? (un|el|una|la)? ?(comit|agente|sistema|tool|rutina|subsistema|caja)|comit[eé]|nuevo agente|nueva rutina|monta.*caja|nueva caja|constructor|constelaci[oó]n|estrategia|redise|landing|hero|haz todo lo necesario|todo lo que (haga falta|necesite)|varias|m[uú]ltiples|pipeline|refactor|migrar|migraci[oó]n'
WORDS="$(printf '%s' "$PROMPT" | wc -w | tr -d ' ')"

if printf '%s' "$PROMPT" | grep -Eq "$REGEX" || [ "${WORDS:-0}" -gt 60 ]; then
  jq -n '{
    hookSpecificOutput: {
      hookEventName: "UserPromptSubmit",
      additionalContext: "RECORDATORIO (plan-primero): este prompt tiene señales de ALCANCE grande. Evalúa la checklist del punto 2 de CLAUDE.md; si cumple ≥1 disparador, entra en EnterPlanMode y presenta un PLAN (TL;DR) ANTES de tocar nada. El CÓMO técnico sigue siendo autónomo. El muro y el gate de salida mandan sobre esto."
    }
  }' 2>/dev/null || true
fi
exit 0
