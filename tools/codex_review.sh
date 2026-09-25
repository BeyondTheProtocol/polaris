#!/usr/bin/env bash
# Pide a Codex (OpenAI, incluido en el plan ChatGPT de {{TITULAR}}) una SEGUNDA OPINIÓN de código,
# de forma SEGURA por construcción:
#   - read-only (Codex NUNCA escribe ni ejecuta nada)
#   - aislado en carpeta temporal (Codex solo ve los ficheros que le pasas)
#   - MURO: rechaza rutas de la fuente de verdad clínica, PII o claves.
# Codex ACONSEJA; Claude/{{TITULAR}} reconcilian y escriben. Coste: tu cuota ChatGPT (gratis).
#
# Uso:
#   tools/codex_review.sh tools/perplexity.py
#   tools/codex_review.sh tools/perplexity.py tools/grok.py        # varios ficheros
#   tools/codex_review.sh -p "céntrate en seguridad" tools/kb.py    # foco personalizado
#
# Requisitos (1 vez): brew install codex && codex  → "Sign in with ChatGPT".
set -euo pipefail
export PATH="/opt/homebrew/bin:$PATH"

PROMPT='Eres un revisor de código senior. Revisa el/los fichero(s) y responde SOLO con bullets concisos: bugs reales, riesgos de seguridad y simplificaciones concretas. No edites nada.'
FILES=()
while [ $# -gt 0 ]; do
  case "$1" in
    -p|--prompt) PROMPT="$2"; shift 2;;
    -h|--help) sed -n '2,15p' "$0"; exit 0;;
    *) FILES+=("$1"); shift;;
  esac
done

[ ${#FILES[@]} -gt 0 ] || { echo 'Uso: tools/codex_review.sh [-p "foco"] <fichero> [más ficheros]'; exit 1; }
command -v codex >/dev/null 2>&1 || { echo "Codex no instalado → brew install codex && codex (Sign in with ChatGPT)"; exit 1; }

SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

NAMES=()
for f in "${FILES[@]}"; do
  [ -f "$f" ] || { echo "No existe: $f"; exit 1; }
  # MURO — solo código compartible. Bloquea fuente de verdad / privado / claves.
  case "$f" in
    *00_FUENTE-DE-VERDAD*|*_PRIVADO*|*PRIVADO*|*_secrets.json|*.secrets*|*.env*)
      echo "🚫 BLOQUEADO por el muro: '$f' no es código compartible con Codex (datos clínicos/PII/claves)."; exit 2;;
  esac
  cp "$f" "$SCRATCH/$(basename "$f")"
  NAMES+=("$(basename "$f")")
done

echo "=== Codex revisa (read-only, aislado): ${NAMES[*]} ==="
codex exec -C "$SCRATCH" -s read-only --skip-git-repo-check --ephemeral \
  "$PROMPT Ficheros a revisar: ${NAMES[*]}."
