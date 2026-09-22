#!/usr/bin/env bash
# setup_macbook.sh — convierte un MacBook en la CABINA DE MANDO portátil de
# "Beyond the Protocol". Trae SOLO el sistema (código + agentes + filosofía).
# NO trae datos clínicos ni secretos: esos viven en Polaris (el Mac mini).
#
# Filosofía: Polaris = cerebro + obrero 24/7 (fuente de verdad única).
#            MacBook = cabina: mandas intenciones, revisas, apruebas. No almacena el clínico.
#
# Ejecútalo TÚ en el MacBook:   bash setup_macbook.sh <url-del-remoto-PRIVADO>
# Es idempotente (puedes re-ejecutarlo). Las claves las pegas tú (paso 6, gate).
set -euo pipefail

REPO_REMOTE="${1:-}"          # URL del remoto git PRIVADO (te la da el Comité de Git). NUNCA el repo público.
DEST="${2:-$HOME/claudecode}" # misma ruta que en Polaris => las memorias casan solas

say() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

if [ -z "$REPO_REMOTE" ]; then
  echo "Uso: bash setup_macbook.sh <git-url-privado> [destino]"
  echo "Ej:  bash setup_macbook.sh git@github.com:TUUSUARIO/btp-sistema-privado.git"
  exit 1
fi

say "1. Homebrew"
command -v brew >/dev/null || /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
eval "$($(command -v brew) shellenv)" 2>/dev/null || true

say "2. Herramientas (mismas que Polaris)"
brew install node@22 restic gitleaks git ffmpeg || true

say "3. Claude Code CLI standalone (NO la app)"
# Instala el CLI en ~/.local/bin, separado de la app de escritorio (igual que en Polaris).
# VERIFICA que es el mismo método/instalador usado en Polaris antes de fiarte.
if ! command -v claude >/dev/null; then
  curl -fsSL https://claude.ai/install.sh | bash || echo "  ⚠️ revisa la instalación del CLI a mano"
fi
echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zshrc" 2>/dev/null || true

say "4. Clonar el SISTEMA (solo código, sin clínico)"
if [ -d "$DEST/.git" ]; then
  echo "  · ya existe $DEST (git) — hago pull"
  # Solo fast-forward: sincroniza, no crea contenido. El freno de la base lo pide explícito (11-sep-26).
  BTP_GIT_BASE_OK=1 git -C "$DEST" pull --ff-only || echo "  ⚠️ pull manual"
else
  git clone "$REPO_REMOTE" "$DEST"
fi
cd "$DEST"
echo "  · el repo NO incluye 00_FUENTE-DE-VERDAD/ (clínico) ni secretos: viven en Polaris."

say "5. venv de tools (opcional en la cabina)"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip -q install reportlab pypdf numpy soundfile openai-whisper torch 2>/dev/null || echo "  · deps de tools opcionales (instala las que necesites)"
# Nota: los MCP bio (biomcp/cbioportal) viven en .mcp-servers/ que está gitignored;
#       en la cabina o se reinstalan aparte o se delega a Polaris. Para mando, no hacen falta.

say "6. Secretos -> Llavero (TÚ pegas las claves — gate)"
echo "  Ejecuta:  bash tools/setup_keychain.sh"
echo "  Te pedirá pegar cada clave (Grok, Telegram, Umami, Perplexity, Anthropic-API para el CLI)."
echo "  No se guardan en disco; van al Llavero (btp-grok-api, btp-anthropic-api, ...)."

say "LISTO"
cat <<'EOF'
La cabina ya entiende la MISMA filosofía (CLAUDE.md + AGENTS.md + agentes del repo).
Recuerda las reglas de oro:
  • UNA fuente de verdad = Polaris. La cabina MANDA y CONSULTA; no duplica el clínico.
  • El lazo autónomo 24/7 corre en POLARIS, no aquí.
  • NADA por iCloud/Drive entre las dos máquinas (fuga + carreras). Git para el sistema; el clínico, en Polaris.
  • Seguridad del portátil: FileVault ON + llave física 2FA. Si lo pierdes, solo perdiste un mando a distancia.
Para mandar trabajo a Polaris desde aquí: usa el bot de Telegram (cuando esté montado) o acceso remoto seguro.
EOF
