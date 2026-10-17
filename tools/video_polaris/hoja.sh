#!/bin/bash
# Hoja de contactos de un render: un fotograma por instante, en una rejilla de 4×5 (hasta 20).
# Uso: bash tools/video_polaris/hoja.sh <video.mp4> <salida.jpg> <t1> <t2> ...
# Sin el segundo rotulado en cada fotograma: el ffmpeg de esta máquina no trae drawtext. El orden es el de los argumentos.
set -e
V="$1"; SAL="$2"; shift 2
O="$(dirname "$SAL")/hoja-tmp"; rm -rf "$O"; mkdir -p "$O"
i=0
for s in "$@"; do
  i=$((i+1))
  ffmpeg -v error -y -ss "$s" -i "$V" -frames:v 1 -vf "scale=640:-1" "$O/$(printf %02d $i).jpg"
done
ffmpeg -v error -y -pattern_type glob -i "$O/*.jpg" -vf "tile=4x5:padding=6:color=black" -frames:v 1 "$SAL"
echo "ok: $SAL"
