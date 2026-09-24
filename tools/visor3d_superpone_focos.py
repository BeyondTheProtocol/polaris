#!/usr/bin/env python3
"""¿Puede el esqueleto REAL sustituir al dibujo sin recolocar los 19 focos a ojo?

La respuesta corta es no directamente: el dibujo y el esqueleto real tienen proporciones
distintas, así que pegar los focos con sus coordenadas de hoy los deja fuera de sitio.

Lo que sí se puede: ALINEAR los dos. Seis de los focos nombran un hueso cuyo centroide sí
tengo verificado en el esqueleto real (sacro, los dos ilíacos, fémur, escápula y costilla).
Con esos seis pares se ajusta por mínimos cuadrados una escala y una traslación por eje, y esa
transformación se aplica a los DIECINUEVE. Los vertebrales, que son los que el modelo no sabe
nombrar, conservan así su posición RELATIVA al resto del esqueleto —exactamente la que tienen
hoy publicada— pero sobre anatomía real.

No es inventar posiciones: es mover las que ya había al fondo nuevo. El error residual de los
seis anclas dice si el ajuste vale; si es grande, el esqueleto real no sirve de sustituto
directo y hay que recolocar a mano.
"""
import json
import os
import sys

from PIL import Image, ImageDraw

MARCA = "/Users/polaris/claudecode/00_FUENTE-DE-VERDAD/07 · Marca/Esqueleto-3D"

# El hueso que nombra el informe para cada foco. Varios focos comparten hueso (el pedículo y el
# cuerpo de la misma vértebra, los tres del ilíaco derecho): el centroide es uno solo, así que
# ahí se verá superposición y hará falta un desplazamiento dentro del hueso.
FOCOS = [
    (1,  220,  90, "vertebrae_C3",   "C3 apófisis espinosa"),
    (2,  206, 104, "vertebrae_C4",   "C4 lámina derecha"),
    (3,  120, 205, "scapula_right",  "escápula derecha"),
    (4,  220, 156, "vertebrae_T1",   "D1 cuerpo"),
    (5,  220, 234, "vertebrae_T5",   "D5 cuerpo"),
    (6,  212, 313, "vertebrae_T9",   "D9 cuerpo derecho"),
    (7,  220, 352, "vertebrae_T11",  "D11 cuerpo"),
    (8,  237, 352, "vertebrae_T11",  "D11 pedículo izquierdo"),
    (9,  220, 392, "vertebrae_L1",   "L1 apófisis espinosa"),
    (10, 241, 399, "vertebrae_L1",   "L1 pedículo izquierdo"),
    (11, 220, 470, "vertebrae_L5",   "L5 cuerpo"),
    (12, 205, 505, "sacrum",         "ala sacra derecha"),
    (13, 165, 545, "hip_right",      "ilíaco derecho, ala"),
    (14, 172, 585, "hip_right",      "ilíaco derecho supraacetabular"),
    (15, 275, 585, "hip_left",       "ilíaco izquierdo supraacetabular"),
    (16, 158, 628, "femur_right",    "fémur proximal derecho"),
    (17, 182, 198, "rib_right_3",    "tórax alto, costilla"),
    (18, 178, 560, "hip_right",      "ilíaco derecho, unión ilíaco-femoral"),
    (19, 232, 150, "vertebrae_T1",   "C7-D2 transición"),
]
VB = (440, 700)   # viewBox del esquema de hoy

# Los seis focos que sirven de ancla: su hueso tiene centroide verificado en el esqueleto real.
# Los vertebrales NO entran aquí, porque el modelo no los nombra bien (esa es toda la historia).
ANCLAS = [3, 12, 13, 14, 15, 16, 17, 18]

# Cuando el mapa trae niveles vertebrales, cada foco va al centroide de SU vértebra y no hace
# falta alinear nada: la posición sale de la anatomía, no de estirar el dibujo viejo.
DIRECTO = True


def alinea(mapa, ancho, alto):
    """Escala y traslación por eje que llevan el dibujo al esqueleto real. Devuelve (fn, error)."""
    pares = []
    for id_, x, y, hueso, _ in FOCOS:
        c = mapa["huesos"].get(hueso)
        if id_ in ANCLAS and c:
            pares.append((x / VB[0], y / VB[1], c["u"], c["v"]))
    if len(pares) < 3:
        raise SystemExit("ABORTA: %d anclas, hacen falta al menos 3" % len(pares))

    def _ajusta(i, j):
        # mínimos cuadrados de una recta: destino = a * origen + b
        n = len(pares)
        sx = sum(p[i] for p in pares); sy = sum(p[j] for p in pares)
        sxx = sum(p[i] * p[i] for p in pares); sxy = sum(p[i] * p[j] for p in pares)
        den = n * sxx - sx * sx
        if abs(den) < 1e-9:
            raise SystemExit("ABORTA: las anclas están alineadas, no definen escala")
        a = (n * sxy - sx * sy) / den
        return a, (sy - a * sx) / n

    au, bu = _ajusta(0, 2)
    av, bv = _ajusta(1, 3)

    def fn(x, y):
        return ((au * (x / VB[0]) + bu) * ancho, (av * (y / VB[1]) + bv) * alto)

    err = []
    for x0, y0, u, v in pares:
        px, py = fn(x0 * VB[0], y0 * VB[1])
        err.append((((px / ancho - u) * ancho) ** 2 + ((py / alto - v) * alto) ** 2) ** 0.5)
    return fn, (sum(err) / len(err), max(err))


def main():
    mapa = json.load(open(os.path.join(MARCA, "esqueleto-BORRADOR.json"), encoding="utf-8"))
    im = Image.open(os.path.join(MARCA, "esqueleto-anterior-BORRADOR.png")).convert("RGBA")
    fondo = Image.new("RGBA", im.size, (14, 17, 23, 255))
    fondo.alpha_composite(im)
    d = ImageDraw.Draw(fondo)
    W, H = im.size
    directo = all(f[3] in mapa["huesos"] for f in FOCOS)
    if not directo:
        fn, (medio, peor) = alinea(mapa, W, H)
        print("SIN niveles: alineando el dibujo · error medio %.0f px · peor %.0f px"
              % (medio, peor))
    else:
        print("con niveles: cada foco va al centroide de su propio hueso")
    faltan = []
    for id_, x, y, hueso, texto in FOCOS:
        c = mapa["huesos"].get(hueso)
        if directo and c:
            px, py = c["u"] * W, c["v"] * H
        elif not directo:
            px, py = fn(x, y)
        else:
            faltan.append((id_, hueso, texto))
            continue
        r = 10
        d.ellipse([px - r, py - r, px + r, py + r], fill=(242, 178, 60, 235))
        d.text((px + r + 3, py - 7), str(id_), fill=(255, 255, 255, 255))
        hx, hy = x / VB[0] * W, y / VB[1] * H
        d.ellipse([hx - 4, hy - 4, hx + 4, hy + 4], outline=(120, 200, 255, 200), width=2)
        d.line([px, py, hx, hy], fill=(120, 200, 255, 90), width=1)
    puestos = len(FOCOS) - len(faltan)
    salida = os.path.join(MARCA, "superposicion-focos-BORRADOR.png")
    fondo.convert("RGB").save(salida, "PNG", optimize=True)
    print("puestos %d de %d · %s" % (puestos, len(FOCOS), salida))
    for f in faltan:
        print("  SIN HUESO: foco %d → %s (%s)" % f)
    return 1 if faltan else 0


if __name__ == "__main__":
    sys.exit(main())
