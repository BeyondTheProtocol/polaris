#!/usr/bin/env bash
# tools/setup_polaris_air.sh — monta Polaris en el MacBook Air desde el bundle. Corre EN EL AIR.
# Idempotente y verboso. Deja el cockpit listo: rutas reescritas al usuario del Air, venv recreado,
# secretos importados (si traes el .enc), daemons INSTALABLES pero APAGADOS, y batería de tests.
#
#   bash <bundle>/claudecode/tools/setup_polaris_air.sh
set -euo pipefail

# 0) Ubicar el repo destino = $HOME/claudecode (vale para cualquier usuario).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
DEST_REPO="$HOME/claudecode"
echo "→ Usuario del Air: $(whoami)   HOME: $HOME"

if [ "$SRC_REPO" != "$DEST_REPO" ]; then
  echo "→ Colocando el repo en $DEST_REPO"
  mkdir -p "$DEST_REPO"
  rsync -a "$SRC_REPO/" "$DEST_REPO/"
fi
cd "$DEST_REPO"

# 1) FileVault (los secretos exigen disco cifrado).
if fdesetup status 2>/dev/null | grep -qi "On"; then
  echo "  ✓ FileVault ON"
else
  echo "  ⚠ FileVault parece APAGADO. Enciéndelo (Ajustes › Privacidad y seguridad › FileVault)"
  echo "    antes de importar secretos. Puedo seguir con el resto."
fi

# 2) Reescribir rutas /Users/polaris/ → $HOME/ (el Air usa OTRO usuario).
echo "→ Reescribiendo rutas al usuario del Air…"
TARGETS=$(grep -rIl "/Users/polaris/" tools/launchd tools/backup.sh tools/staging.py \
            tools/mcp_server.py .claude 2>/dev/null | grep -vE '/logs/|state/.*\.log' || true)
for f in $TARGETS; do
  sed -i '' "s|/Users/polaris/|$HOME/|g" "$f"
done
echo "  ✓ rutas reescritas en $(printf '%s\n' $TARGETS | grep -c . || echo 0) ficheros"

# 3) venv principal + dependencias (necesita internet 1 vez).
echo "→ Recreando el entorno Python (.venv)…"
python3 -m venv .venv
./.venv/bin/python -m pip install --quiet --upgrade pip
if [ -f requirements.txt ]; then
  ./.venv/bin/pip install --quiet -r requirements.txt && echo "  ✓ dependencias instaladas" \
    || echo "  ⚠ algunas dependencias fallaron (revisa arriba; el núcleo stdlib funciona igual)"
fi
# Venvs pesados de ML local (voz/imagen/pipeline): best-effort, opcionales — NO bloquean el cockpit.

# 4) Secretos: importar del blob cifrado si lo trajiste.
ENC=""
for c in "$DEST_REPO/secretos-polaris.enc" "$SCRIPT_DIR/../../secretos-polaris.enc" "$HOME/secretos-polaris.enc"; do
  [ -f "$c" ] && ENC="$c" && break
done
if [ -n "$ENC" ]; then
  echo "→ Importando secretos al Llavero del Air (mete la passphrase que elegiste en el mini)…"
  TMP="$(mktemp)"; trap 'rm -P "$TMP" 2>/dev/null || rm -f "$TMP"' EXIT
  if openssl enc -d -aes-256-cbc -pbkdf2 -in "$ENC" -out "$TMP" 2>/dev/null; then
    n=0
    while IFS=$'\t' read -r name b64; do
      [ -n "$name" ] || continue
      val=$(printf '%s' "$b64" | base64 -d 2>/dev/null || true)
      [ -n "$val" ] || continue
      security add-generic-password -U -s "$name" -a "$(whoami)" -w "$val" 2>/dev/null && n=$((n+1))
    done < "$TMP"
    echo "  ✓ $n secretos importados al Llavero"
  else
    echo "  ⚠ passphrase incorrecta o blob ilegible — secretos NO importados (reintenta luego)"
  fi
else
  echo "  (sin secretos-polaris.enc a la vista — impórtalos luego cuando lo traigas)"
fi

# 5) Daemons launchd: NO se encienden (el mini sigue 24/7 en casa; evitar doble envío).
chmod +x tools/polaris-daemons 2>/dev/null || true
echo "→ Daemons 24/7: quedan APAGADOS por defecto (toggle: tools/polaris-daemons on|off|estado)."

# 6) Verificación.
echo "→ Batería de tests (paridad del muro + lazo)…"
if bash tests/test_all.sh >/tmp/polaris_air_tests.log 2>&1; then
  echo "  ✅ test_all.sh EN VERDE"
else
  echo "  ⚠ algún test falló — mira /tmp/polaris_air_tests.log (tail):"
  tail -5 /tmp/polaris_air_tests.log
fi

echo
echo "✅ Polaris montado en el Air. Pruebas rápidas (smoke):"
echo "   python3 tools/seguimiento.py            # tablero"
echo "   python3 tools/correo_imap.py | head     # lee tu Gmail (necesita secretos)"
echo "   python3 tools/kb.py ask \"…\"             # RAG sobre la fuente de verdad"
echo "   (y abre Claude Code en ~/claudecode para el gabinete completo)"
