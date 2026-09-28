#!/usr/bin/env bash
# Render de un vídeo de Remotion → BORRADOR 1920×1080 + versión móvil en la carpeta de marca.
#   bash tools/video_polaris/remotion/render.sh v16                                  # el vídeo «Polaris»
#   bash tools/video_polaris/remotion/render.sh v1 Narrado polaris-pieza-a-pieza     # un vídeo narrado (narrado.py)
# Hasta la v15 esta receta solo vivía en el historial de una sesión; aquí queda fija.
# Techo del audio en -4 dBTP (el AAC sube ~0,5 dB: con -3.5 quedaba en -3,0): X recodifica al subir y el AAC con -1.5 salía a -1.3 dBTP
# (oráculo de onetake: pico ≤ -3 dBFS; 28-sep-2026).
set -euo pipefail
V="${1:?uso: render.sh <version> [Composicion] [nombre]}"
COMP="${2:-Polaris}"
cd "$(dirname "$0")"
D="$HOME/claudecode/00_FUENTE-DE-VERDAD/07 · Marca/Videos-Polaris"
if [ "$COMP" = "Polaris" ]; then
  BASE="$D/polaris-story-$V-remotion"; TL=public/timeline.json
else
  BASE="$D/${3:?falta el nombre del vídeo}-$V"; TL=public/narrado/timeline.json
fi
[ -d public ] || { echo "falta public/ (recursos: $D/recursos-remotion + voz/música/sfx)"; exit 1; }
mkdir -p out
npx remotion render src/index.ts "$COMP" out/render.mp4 --concurrency=8 --crf=16 --gl=angle
ffmpeg -v error -y -i out/render.mp4 -c:v copy \
  -af "loudnorm=I=-16:TP=-4:LRA=11,aresample=48000" -c:a aac -b:a 192k -movflags +faststart \
  "$BASE-1920x1080-BORRADOR.mp4"
cp "$TL" "$BASE-timeline.json"
ffmpeg -v error -y -i "$BASE-1920x1080-BORRADOR.mp4" \
  -c:v libx264 -preset slow -crf 23 -pix_fmt yuv420p -c:a copy -movflags +faststart \
  "${BASE/-remotion/}-movil-BORRADOR.mp4"
# comprobación: el pico tiene que quedar ≤ -3 dBTP
ffmpeg -hide_banner -i "$BASE-1920x1080-BORRADOR.mp4" -af loudnorm=print_format=summary -f null /dev/null 2>&1 \
  | grep -E 'Input (Integrated|True Peak)'
