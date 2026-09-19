#!/usr/bin/env bash
# tools/migrar_a_air.sh — empaqueta Polaris (casa base) para llevarlo FÍSICO al MacBook Air.
# Corre en el MINI. Copia el repo a un destino (SSD/carpeta para AirDrop) excluyendo lo que se
# RECREA (venvs) y, en modo núcleo, los binarios pesados (DICOM/media). Genera requirements.txt y
# un manifest. NO toca secretos (eso es migrar_secretos_air.sh, gateado).
#
#   tools/migrar_a_air.sh <DEST_dir> [completo|nucleo]
#     completo (def): todo salvo venvs/caches → parida real (~15G; para SSD/cable)
#     nucleo        : sin DICOM/zip/audio/.git → ligero (~pocos GB; para AirDrop). El RAG sigue por índice.
set -euo pipefail
SRC="${BTP_REPO:-$HOME/claudecode}"
DEST="${1:?uso: migrar_a_air.sh <DEST_dir> [completo|nucleo]}"
MODO="${2:-completo}"
OUT="$DEST/claudecode"
mkdir -p "$OUT"

echo "→ Empaquetando Polaris desde $SRC"
echo "  destino: $OUT   modo: $MODO"

# requirements del venv principal (para recrearlo en el Air)
if [ -x "$SRC/.venv/bin/pip" ]; then
  "$SRC/.venv/bin/pip" freeze > "$OUT/requirements.txt" 2>/dev/null || true
  echo "  ✓ requirements.txt ($(wc -l < "$OUT/requirements.txt" | tr -d ' ') paquetes)"
fi

EXC=(--exclude='.venv' --exclude='.venv-*' --exclude='__pycache__' --exclude='*.pyc'
     --exclude='.DS_Store' --exclude='node_modules' --exclude='*.tmp'
     --exclude='tools/state/backups_openwebui')
if [ "$MODO" = "nucleo" ]; then
  # Fuera lo pesado/recreable: imagen clínica, comprimidos, audio, historial git.
  EXC+=(--exclude='*.dcm' --exclude='*.zip' --exclude='*.gz' --exclude='*.mov'
        --exclude='*.mp4' --exclude='*.opus' --exclude='*.ogg' --exclude='.git')
  echo "  (modo núcleo: sin DICOM/media/.git — el RAG responde por .kb_index.json)"
fi

rsync -a "${EXC[@]}" "$SRC/" "$OUT/"

# Manifest + huella (para verificar la copia en el Air)
( cd "$OUT" && find . -type f 2>/dev/null | wc -l | tr -d ' ' > .MIGRACION_MANIFEST )
echo "  ✓ ficheros copiados: $(cat "$OUT/.MIGRACION_MANIFEST")"
echo "  tamaño: $(du -sh "$OUT" | cut -f1)"
echo
echo "✓ LISTO. Siguiente:"
echo "  1) (secretos) en el mini:  tools/migrar_secretos_air.sh \"$DEST\""
echo "  2) lleva \"$DEST/claudecode\" al Air (SSD/cable/AirDrop)"
echo "  3) en el Air:  bash claudecode/tools/setup_polaris_air.sh"
