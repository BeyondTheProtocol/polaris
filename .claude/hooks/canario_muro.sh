#!/bin/bash
# SessionStart hook del lazo (settings.autonomous / quarantine / los BTP_SETTINGS): CANARIO DEL MURO.
# Idea de {{CONTACTO}} {{CONTACTO}} (https://contacto.com), con su agente KAI, revisión del 25-sep-2026.
#
# Deja un testigo con el nonce que run_agent.sh le pasa por entorno. Si el testigo NO aparece,
# Claude Code no cargó los hooks del --settings (p. ej. `--bare` por defecto en `-p`, anunciado en
# https://code.claude.com/docs/en/headless) y el muro_guard PreToolUse tampoco está: run_agent.sh
# falla CERRADO. El testigo prueba que los hooks de ESE fichero se cargaron; que el PreToolUse del
# mismo fichero también lo hiciera es inferencia (mismo mecanismo de carga).
#
# Sin BTP_CANARIO_TESTIGO no hace nada (sesiones interactivas, tests ajenos). Nunca escribe a
# stdout (no inyecta contexto) y siempre sale 0: un canario que tumba la sesión sería otro fallo.
cat >/dev/null 2>&1 || true
[ -n "${BTP_CANARIO_TESTIGO:-}" ] || exit 0
printf '%s\n' "${BTP_CANARIO_NONCE:-sin-nonce}" >"$BTP_CANARIO_TESTIGO" 2>/dev/null || true
exit 0
