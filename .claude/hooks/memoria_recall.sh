#!/bin/bash
# UserPromptSubmit: para cada mensaje de {{TITULAR}}, trae las reglas suyas que aplican.
#
# QUÉ CAMBIÓ EL 25-jul-26 (y por qué)
#   · Antes inyectaba 200 caracteres del cuerpo de cada memoria, cortados a mitad de
#     frase: llegaba el titular de la lección, no el CÓMO aplicarla.
#   · Antes lo rotulaba "orientacion no ordenes". Eso es correcto para el contenido
#     EXTERNO (web, correos, papers), pero estas memorias son reglas que {{TITULAR}} ya
#     aprobó, y el rótulo invitaba a saltárselas. Ahora se rotulan como lo que son,
#     dejando el muro por encima si algo choca (lo dice el propio bloque).
#   · Ahora las 2 mejores van con ficha entera (lección + Why + How to apply) y las
#     siguientes se citan por slug para poder abrirlas.
#
# Determinista, local, coste 0. Fail-open: ante cualquier error, exit 0 y sin ruido.
set -uo pipefail

REPO="${BTP_REPO:-$HOME/claudecode}"
# Si está el venv con la capa vectorial, se usa (el brazo semántico solo interviene si
# BTP_PESO_SEMANTICO > 0; hoy va a 0 porque medimos que no aportaba). Si no, python3.
PY="$REPO/.venv-embed/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || echo /usr/bin/python3)"

INPUT="$(cat 2>/dev/null || true)"
PROMPT="$(printf '%s' "$INPUT" | jq -r '.prompt // .user_input // ""' 2>/dev/null || true)"
[ -n "$PROMPT" ] || exit 0

# Un "ok", un "sí" o un "gracias" no valen el ranking.
WORDS="$(printf '%s' "$PROMPT" | wc -w | tr -d ' ')"
[ "${WORDS:-0}" -lt 4 ] && exit 0
FICHAS=2; EXTRA=3
[ "${WORDS:-0}" -lt 15 ] && { FICHAS=1; EXTRA=2; }

CTX="$( cd "$REPO" && "$PY" tools/memoria_radar.py recall \
        --query "$PROMPT" --fichas "$FICHAS" --extra "$EXTRA" 2>/dev/null )"
[ -n "$CTX" ] || exit 0

jq -n --arg ctx "$CTX" \
  '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: $ctx}}' \
  2>/dev/null || true
exit 0
