"""Prepara los recursos del vídeo en <build>/assets: recortes de las capturas y fotogramas del hígado.

Uso: python3 preparar.py <carpeta_capturas> <carpeta_higado> <build>
  <carpeta_capturas> sale de capturar.mjs; <carpeta_higado>, de grabar_higado.mjs.

Los recortes están elegidos a mano sobre capturas a 1920×1080 (27-sep-2026) para que
en el vídeo no se lea ninguna cifra clínica (comité de diseño) ni el badge de CI:
  biopsia     solo la microfotografía con los contornos, sin el panel de medidas
  datos-cielo solo el campo de puntos, sin la cifra grande de encima
  science     solo el título de /science; la ficha de debajo lleva el perfil molecular
  home        la presentación de la home, sin la fila de cifras (lo recaudado no sale)
  esqueleto   solo el lienzo del esqueleto de /mapa-metastasis, sin los paneles con SUV y medidas
  repo        enlace + topics de la ficha «About», sin la lista de commits (lleva la ✗ del CI)
                y sin la descripción, que está en español
  hígado      el lienzo del visor, ya sin rótulos de medida ni botones (grabar_higado.mjs)
"""
import json, os, shutil, subprocess, sys
from PIL import Image

AQUI = os.path.dirname(os.path.abspath(__file__))
ANATOMIA = os.path.join(AQUI, "..", "anatomia.py")
FUENTES = os.path.expanduser("~/claudecode/00_FUENTE-DE-VERDAD/05 · Web/piloto-video-el-caso-en-datos/fonts")
RECORTES = {  # (x0, y0, x1, y1) en px de la captura
    "biopsia": (388, 233, 1204, 522),
    "datos-cielo": (475, 345, 1440, 670),
    "repo": (1290, 325, 1580, 492),
    # vídeo con historia (28-sep): mismas reglas, ni una cifra clínica ni lo recaudado
    "science": (540, 170, 1380, 320),     # solo el título y la frase «behaves like two diseases at once»
    "home": (400, 140, 1520, 580),        # la presentación, cortada ANTES de la fila de cifras (lleva lo recaudado)
    "esqueleto": (369, 247, 695, 868),    # el lienzo del esqueleto; los paneles de la derecha llevan SUV/mm/ml
}

def main(capturas, higado, build):
    out = os.path.join(build, "assets"); os.makedirs(out, exist_ok=True)
    for n, caja in RECORTES.items():
        Image.open(os.path.join(capturas, n + ".png")).crop(caja).save(os.path.join(out, n + ".png"))
        print("✓ recorte", n, caja)
    hig = os.path.join(out, "higado"); os.makedirs(hig, exist_ok=True)
    r = json.load(open(os.path.join(higado, "recorte.json")))
    fotos = sorted(f for f in os.listdir(higado) if f.endswith(".jpg"))
    esc = Image.open(os.path.join(higado, fotos[0])).width / 1920  # deviceScaleFactor de la grabación
    caja = tuple(round(v * esc) for v in (r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]))
    for f in fotos:
        Image.open(os.path.join(higado, f)).crop(caja).save(os.path.join(hig, f), quality=92)
    print("✓ hígado", len(fotos), "fotogramas", caja)

    # Tipografías de marca (Hanken Grotesk llega de Google Fonts en polaris.html).
    for f in ("Fraunces-600-normal.ttf", "JetBrains_Mono-400-normal.ttf", "JetBrains_Mono-600-normal.ttf"):
        shutil.copy(os.path.join(FUENTES, f), out)
    # Las cifras del bloque 0:17 salen de la Anatomía EL DÍA DEL RENDER, nunca a mano.
    inv = json.loads(subprocess.run([sys.executable, ANATOMIA, "inventario", "--json"],
                                    capture_output=True, text=True, check=True).stdout)
    cifras = {"fecha": inv["fecha"], "comites": inv["numeros"]["comites"], "tools": inv["numeros"]["tools"],
              "rutinas": inv["numeros"]["rutinas_cargadas"], "llms": inv["numeros"]["cerebros_on"], "guardas": inv["numeros"]["guardas"],
              "tests": inv["memoria"]["tests"]}
    with open(os.path.join(out, "cifras.js"), "w") as fh:
        fh.write("window.CIFRAS = " + json.dumps(cifras) + ";\n")
    print("✓ cifras", cifras)

def extra(build, mama=None, vertebra=None):
    """Visores extra (28-sep, {{TITULAR}}: «enseñar la mama, el hígado, el esqueleto y algún hueso»).
    mama: carpeta de grabar_higado.mjs con tarjeta «Right breast» (rótulos ocultos en la grabación).
    vertebra: carpeta de grabar_higado.mjs con «#0» en /en/mapa-metastasis; se queda la primera de sus tres vistas."""
    out = os.path.join(build, "assets")
    if mama:
        d = os.path.join(out, "mama"); os.makedirs(d, exist_ok=True)
        r = json.load(open(os.path.join(mama, "recorte.json")))
        fotos = sorted(f for f in os.listdir(mama) if f.endswith(".jpg"))
        esc = Image.open(os.path.join(mama, fotos[0])).width / 1920
        caja = tuple(round(v * esc) for v in (r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]))
        for f in fotos:
            Image.open(os.path.join(mama, f)).crop(caja).save(os.path.join(d, f), quality=92)
        print("✓ mama", len(fotos), "fotogramas")
    if vertebra:
        r = json.load(open(os.path.join(vertebra, "recorte.json")))
        im = Image.open(os.path.join(vertebra, "0001.jpg")); esc = im.width / 1920
        x0, y0 = r["x"] * esc, r["y"] * esc
        im.crop((round(x0), round(y0), round(x0 + r["w"] * esc / 3), round(y0 + r["h"] * esc))).save(os.path.join(out, "vertebra.png"))
        print("✓ vértebra (primera vista)")
        # la tira entera de los tres huesos (galio / FDG / TC), girada a mano en la grabación (ARRASTRE): secuencia
        d = os.path.join(out, "vertebra3"); os.makedirs(d, exist_ok=True)
        fotos = sorted(f for f in os.listdir(vertebra) if f.endswith(".jpg"))
        caja = (round(x0), round(y0), round(x0 + r["w"] * esc), round(y0 + r["h"] * esc))
        for f in fotos:
            Image.open(os.path.join(vertebra, f)).crop(caja).save(os.path.join(d, f), quality=92)
        print("✓ tres huesos", len(fotos), "fotogramas")


if __name__ == "__main__":
    if sys.argv[1] == "--extra":   # python3 preparar.py --extra <build> <carpeta_mama> <carpeta_vertebra>
        extra(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        main(*sys.argv[1:4])
