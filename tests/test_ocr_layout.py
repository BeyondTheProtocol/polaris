#!/usr/bin/env python3
"""test_ocr_layout.py — el OCR conserva las celdas y canta cuando no se fía de sí mismo.

POR QUÉ EXISTE (12-sep-2026). `ocr_informes.py` pedía a tesseract `stdout` pelado. Tesseract no
trae modelo de layout, así que una fila de tabla salía como «Linfocitos 0.8 1.0-4.5»: los límites
entre celdas desaparecían y `kb.py` indexaba ESO, o sea que el destrozo llegaba a todas las
búsquedas del RAG sobre lo escaneado. Y peor: una página ilegible producía un sidecar
indistinguible de uno limpio, sin ninguna señal de que no había que fiarse.

El arreglo no necesita dependencia nueva: el TSV de tesseract ya trae `left/top/width` y `conf`
por palabra. Con eso se separan las celdas por el hueco horizontal y se reporta la confianza.

Este test trabaja sobre el TSV directamente (no invoca tesseract ni pdftoppm), así que corre en
cualquier máquina y no depende de que haya binarios instalados.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import ocr_informes as O  # noqa: E402

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


CAB = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def palabra(blk, par, ln, wn, left, top, width, conf, txt):
    return "5\t1\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t20\t%s\t%s" % (blk, par, ln, wn, left, top, width,
                                                             conf, txt)


def tsv(*filas):
    return "\n".join([CAB] + list(filas))


def main():
    # --- 1. Una fila de tabla: las celdas están separadas por huecos anchos ---
    t = tsv(palabra(1, 1, 1, 1, 100, 100, 200, 95, "Linfocitos"),
            palabra(1, 1, 1, 2, 900, 100, 60, 95, "0.8"),
            palabra(1, 1, 1, 3, 1600, 100, 220, 95, "1.0-4.5"))
    txt, conf, n = O._tsv_a_texto(t)
    ok("Linfocitos  ·  0.8  ·  1.0-4.5" == txt,
       "las celdas de una fila salen separadas, no pegadas (%r)" % txt)
    ok(n == 3 and conf == 95.0, "cuenta palabras y calcula la confianza media")

    # --- 2. Palabras contiguas de una frase NO se separan como si fueran celdas ---
    t2 = tsv(palabra(1, 1, 1, 1, 100, 100, 120, 90, "el"),
             palabra(1, 1, 1, 2, 240, 100, 150, 90, "zorro"),
             palabra(1, 1, 1, 3, 410, 100, 130, 90, "veloz"))
    txt2, _, _ = O._tsv_a_texto(t2)
    ok(txt2 == "el zorro veloz", "una frase normal se mantiene como frase (%r)" % txt2)

    # --- 3. Confianza baja → el llamante puede cantarlo (aquí se comprueba el cálculo) ---
    t3 = tsv(palabra(1, 1, 1, 1, 100, 100, 200, 20, "iIegibIe"),
             palabra(1, 1, 1, 2, 400, 100, 200, 30, "rnaI"))
    _, conf3, _ = O._tsv_a_texto(t3)
    ok(conf3 < O.CONF_MINIMA, "una página adivinada queda por debajo del umbral (%.1f)" % conf3)
    ok(O.CONF_MINIMA > 0, "hay umbral definido, no es un número mágico suelto")

    # --- 4. Las palabras con conf -1 (no-palabras de tesseract) no ensucian la media ---
    t4 = tsv(palabra(1, 1, 1, 1, 100, 100, 200, -1, "x"),
             palabra(1, 1, 1, 2, 400, 100, 200, 90, "bueno"))
    _, conf4, n4 = O._tsv_a_texto(t4)
    ok(conf4 == 90.0 and n4 == 1, "conf -1 se ignora en la media y en el recuento")

    # --- 5. Bloques distintos quedan separados por una línea en blanco ---
    t5 = tsv(palabra(1, 1, 1, 1, 100, 100, 200, 90, "primero"),
             palabra(2, 1, 1, 1, 100, 400, 200, 90, "segundo"))
    txt5, _, _ = O._tsv_a_texto(t5)
    ok(txt5 == "primero\n\nsegundo", "se conserva la separación entre bloques (%r)" % txt5)

    # --- 6. Se lee de arriba abajo aunque el TSV venga desordenado ---
    t6 = tsv(palabra(2, 1, 1, 1, 100, 900, 200, 90, "abajo"),
             palabra(1, 1, 1, 1, 100, 100, 200, 90, "arriba"))
    txt6, _, _ = O._tsv_a_texto(t6)
    ok(txt6.splitlines()[0] == "arriba", "ordena por posición vertical, no por orden de llegada")

    # --- 7. Entrada vacía o basura no revienta ---
    ok(O._tsv_a_texto("") == ("", 0.0, 0), "TSV vacío devuelve tupla vacía, no lanza")
    ok(O._tsv_a_texto("basura\nsin\ttabs")[0] == "", "TSV malformado se ignora limpiamente")

    print("RESULTADO ocr_layout: %d OK, %d fallos" % (_pass, _fail))
    print("✅ OCR LAYOUT EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
