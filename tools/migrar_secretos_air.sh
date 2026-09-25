#!/usr/bin/env bash
# tools/migrar_secretos_air.sh — exporta los secretos btp-* del Llavero del MINI a un blob CIFRADO
# para llevarlos al Air. Corre en el MINI. GATEADO: pide una passphrase que TÚ eliges (la misma hará
# falta en el Air). Nunca deja secretos en claro en disco. El muro: solo lo cifras tú, a una máquina
# tuya y con FileVault.
#
#   tools/migrar_secretos_air.sh <DEST_dir>
set -euo pipefail
DEST="${1:?uso: migrar_secretos_air.sh <DEST_dir>}"
mkdir -p "$DEST"
OUTENC="$DEST/secretos-polaris.enc"

echo "→ Buscando secretos btp-* en el Llavero…"
NAMES=$(security dump-keychain 2>/dev/null \
        | grep -oE '"svce"<blob>="btp-[^"]*"' \
        | sed -E 's/.*="([^"]*)"/\1/' | sort -u)
[ -n "$NAMES" ] || { echo "No encontré secretos btp-* (¿Llavero bloqueado?)."; exit 1; }

TMP="$(mktemp)"; trap 'rm -P "$TMP" 2>/dev/null || rm -f "$TMP"' EXIT
n=0
while IFS= read -r name; do
  [ -n "$name" ] || continue
  val=$(security find-generic-password -s "$name" -w 2>/dev/null || true)
  [ -n "$val" ] || { echo "  ⚠ vacío/no accesible: $name (lo salto)"; continue; }
  # base64 del valor → inmune a saltos/tabs en el secreto
  printf '%s\t%s\n' "$name" "$(printf '%s' "$val" | base64)" >> "$TMP"
  n=$((n+1))
done <<< "$NAMES"
echo "  ✓ $n secretos recogidos"

echo
echo "Elige una PASSPHRASE para cifrarlos (la necesitarás en el Air; no la pierdas):"
openssl enc -aes-256-cbc -pbkdf2 -salt -in "$TMP" -out "$OUTENC"
echo "✓ Secretos cifrados en: $OUTENC"
echo "  Llévalo junto al bundle. En el Air, setup_polaris_air.sh te pedirá la passphrase para importarlos."
