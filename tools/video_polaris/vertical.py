"""Máster vertical 1080×1920 de un render 16:9 (condición 8 de legal, 3-oct-2026): el vídeo entero, sin recortar, con el
aviso de voz clonada repetido en la franja de abajo para que no se pierda si alguien lo pasa a vertical.
La etiqueta se dibuja con PIL porque el ffmpeg de esta máquina no trae drawtext.
Uso: ~/claudecode/.venv/bin/python vertical.py <entrada_16x9.mp4> <salida_vertical.mp4> "<texto del aviso>"
"""
import os, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

ENT, SAL, AVISO = sys.argv[1], sys.argv[2], sys.argv[3]
FUENTES = ["/System/Library/Fonts/Supplemental/Georgia.ttf", "/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial Unicode.ttf"]
fuente = next(f for f in FUENTES if os.path.exists(f))

W, H, CREMA = 1080, 1920, (250, 246, 240)
lienzo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
d = ImageDraw.Draw(lienzo)
f = ImageFont.truetype(fuente, 38)
# líneas centradas en la franja inferior (debajo del 16:9, que ocupa 1080×608 en el centro)
palabras, lineas, cur = AVISO.split(), [], ""
for w in palabras:
    if d.textlength((cur + " " + w).strip(), font=f) > W - 120:
        lineas.append(cur); cur = w
    else:
        cur = (cur + " " + w).strip()
lineas.append(cur)
y = 1920 - 300
for ln in lineas:
    d.text(((W - d.textlength(ln, font=f)) / 2, y), ln, font=f, fill=CREMA)
    y += 52
png = SAL + ".aviso.png"
lienzo.save(png)
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", ENT, "-i", png, "-filter_complex",
                f"[0:v]scale={W}:-2,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x1D1127[v];[v][1:v]overlay=0:0[o]",
                "-map", "[o]", "-map", "0:a", "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "copy", "-movflags", "+faststart", SAL], check=True)
print("ok:", SAL)
