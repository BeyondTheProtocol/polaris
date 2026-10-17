"""Busca tramos con el escenario vacío en un render (solo fondo): muestrea cada 0,25 s la franja central, sin la
zona de subtítulos ni la de la etiqueta de arriba, y marca los fotogramas con muy poco contraste.
Uso: ~/claudecode/.venv/bin/python huecos.py <video.mp4> [umbral]"""
import io, subprocess, sys
from PIL import Image, ImageStat

V = sys.argv[1]; UMBRAL = float(sys.argv[2]) if len(sys.argv) > 2 else 14.0
dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", V]))
cmd = ["ffmpeg", "-v", "error", "-i", V, "-vf", "fps=4,crop=1700:640:110:200,scale=340:128,format=gray", "-f", "image2pipe", "-vcodec", "png", "-"]
datos = subprocess.check_output(cmd)
pngs, i = [], 0
while True:
    j = datos.find(b"\x89PNG", i + 1)
    pngs.append(datos[i:j if j > 0 else len(datos)])
    if j < 0: break
    i = j
vacios = []
for k, p in enumerate(pngs):
    s = ImageStat.Stat(Image.open(io.BytesIO(p))).stddev[0]
    if s < UMBRAL: vacios.append(k / 4)
tramos, ini, prev = [], None, None
for t in vacios:
    if ini is None: ini = prev = t
    elif t - prev > 0.3: tramos.append((ini, prev)); ini = prev = t
    else: prev = t
if ini is not None: tramos.append((ini, prev))
print(f"duración {dur:.1f} s · tramos vacíos (≥ 0,5 s):")
for a, b in tramos:
    if b - a >= 0.5: print(f"  {a:6.2f} → {b:6.2f}  ({b - a:.2f} s)")
