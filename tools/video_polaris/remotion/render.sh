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
D="${DESTINO:-$HOME/claudecode/00_FUENTE-DE-VERDAD/07 · Marca/Videos-Polaris}"  # DESTINO=… para otra carpeta de marca
mkdir -p "$D"
if [ "$COMP" = "Polaris" ]; then
  BASE="$D/polaris-story-$V-remotion"; TL=public/timeline.json
else
  BASE="$D/${3:?falta el nombre del vídeo}-$V"; TL=public/narrado/timeline.json
fi
[ -d public ] || { echo "falta public/ (recursos: $D/recursos-remotion + voz/música/sfx)"; exit 1; }
# AUDIO_DE=<máster anterior>.mp4: solo cambia la imagen y la pista de audio se copia tal cual de ese máster (ni se
# vuelve a mezclar ni a codificar). Es el único camino cuando voz.wav es el silencio de taller_orellana.py.
AUDIO_DE="${AUDIO_DE:-}"
if [ "$COMP" != "Polaris" ] && [ -f public/narrado/voz.SILENCIO ] && [ -z "$AUDIO_DE" ]; then
  echo "public/narrado/voz.wav es silencio (no hay tomas): renderiza con AUDIO_DE=<máster anterior>"; exit 1
fi
mkdir -p out
npx remotion render src/index.ts "$COMP" out/render.mp4 --concurrency="${CONCURRENCIA:-8}" --crf=16 --gl=angle  # CONCURRENCIA=4 si la máquina va cargada: con 8 y carga ~50 el codificador se atascó (16-oct-2026)
if [ -n "$AUDIO_DE" ]; then
  DV=$(ffprobe -v error -show_entries format=duration -of csv=p=0 out/render.mp4)
  DA=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$AUDIO_DE")
  awk -v v="$DV" -v a="$DA" 'BEGIN { d = v - a; if (d < 0) d = -d; exit (d > 0.1) }' \
    || { echo "la imagen dura $DV s y el audio de AUDIO_DE dura $DA s: no son la misma narración"; exit 1; }
  ffmpeg -v error -y -i out/render.mp4 -i "$AUDIO_DE" -map 0:v -map 1:a -c copy -movflags +faststart \
    "$BASE-1920x1080-BORRADOR.mp4"
else
  ffmpeg -v error -y -i out/render.mp4 -c:v copy \
    -af "loudnorm=I=-16:TP=-4:LRA=11,aresample=48000" -c:a aac -b:a 192k -movflags +faststart \
    "$BASE-1920x1080-BORRADOR.mp4"
fi
cp "$TL" "$BASE-timeline.json"
ffmpeg -v error -y -i "$BASE-1920x1080-BORRADOR.mp4" \
  -c:v libx264 -preset slow -crf 23 -pix_fmt yuv420p -c:a copy -movflags +faststart \
  "${BASE/-remotion/}-movil-BORRADOR.mp4"
# comprobación: el pico tiene que quedar ≤ -3 dBTP
ffmpeg -hide_banner -i "$BASE-1920x1080-BORRADOR.mp4" -af loudnorm=print_format=summary -f null /dev/null 2>&1 \
  | grep -E 'Input (Integrated|True Peak)'
