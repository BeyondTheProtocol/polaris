#!/bin/bash
# Monta frames + sonido en el mp4 final. Uso: bash montar.sh <build> <salida.mp4> [fps=30] [voz.wav]
# Con voz.wav (vídeo con historia) el pad se agacha bajo la voz; sin él, sale el pad solo (vídeo de 26 s).
# H.264 yuv420p a 8 Mbps (con CRF el fondo plano bajaba a ~1 Mbps y la serif fina hacía banding; diseño 27-sep) + AAC: lo que X y LinkedIn aceptan sin recodificar mal. +faststart para reproducir al vuelo.
set -euo pipefail
BUILD="$1"; OUT="$2"; FPS="${3:-30}"; VOZ="${4:-}"
VIDEO=(-c:v libx264 -preset slow -b:v 8M -maxrate 10M -bufsize 16M -pix_fmt yuv420p -r "$FPS")
if [ -n "$VOZ" ]; then
  # Vídeo con historia: la voz manda. El pad baja ~18 dB mientras ella habla (sidechain) y se suma debajo.
  ffmpeg -v error -y -framerate "$FPS" -i "$BUILD/frames/%05d.jpg" -i "$BUILD/sonido.wav" -i "$VOZ" \
    -filter_complex "[2:a]asplit=2[vz][key];[1:a][key]sidechaincompress=threshold=0.02:ratio=12:attack=15:release=450[pad];[pad]volume=0.55[padb];[vz][padb]amix=inputs=2:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[a]" \
    -map 0:v -map "[a]" "${VIDEO[@]}" -c:a aac -b:a 192k -shortest -movflags +faststart "$OUT"
else
  ffmpeg -v error -y -framerate "$FPS" -i "$BUILD/frames/%05d.jpg" -i "$BUILD/sonido.wav" \
    "${VIDEO[@]}" -c:a aac -b:a 192k -shortest -movflags +faststart "$OUT"
fi
ffprobe -v error -show_entries format=duration:stream=codec_name,width,height -of compact "$OUT"
