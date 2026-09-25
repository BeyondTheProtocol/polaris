#!/usr/bin/env python3
"""test_subir_historial_ocr_sidecar.py — el verificador de identidad tiene que LEER el OCR.

20-sep-26. `subir_historial_drive._texto()` buscaba el sidecar como `X.ocr.txt`, pero el que
genera el pipeline se llama `X.pdf.ocr.txt`. Un PDF escaneado no da texto por `pdftotext`, así
que `es_suyo()` decidía sobre 1 o 2 bytes: retenía sin haber leído nada.

No es un problema de limpieza. `es_suyo()` es el control que impide que el informe de OTRA
persona acabe en el historial de {{TITULAR}} ([[feedback-verificar-identidad-paciente-en-informe]]),
y un verificador que mira un fichero vacío no verifica: acierta por defecto. Medido sobre los
419 PDF del historial: 5 se verificaban a ciegas y 3 de ellos SÍ quedan identificados al leer
el OCR que se ignoraba.

El arreglo NO relaja el criterio —sigue exigiendo fecha de nacimiento o identificador—, solo lo
aplica sobre texto que ya estaba en disco.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import subir_historial_drive as S  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# Un PDF escaneado es, para `_texto()`, uno del que `pdftotext` no saca texto. Eso se FUERZA
# aquí en vez de fabricar un PDF inválido y confiar en cómo reaccione el binario: con poppler
# instalado o sin él, en macOS o en Linux, la respuesta cambia, y este test no va de poppler —
# va de CUÁL de los dos sidecares se lee. La primera versión sí dependía del binario y se cayó
# en el CI (Linux) pasando en casa base (macOS): 2 OK, 2 fallos.
class _SinTexto:
    stdout = ""


subir_historial_drive_run = S.subprocess.run


def _pdftotext_mudo(cmd, *a, **k):
    if cmd and str(cmd[0]).endswith("pdftotext"):
        return _SinTexto()
    return subir_historial_drive_run(cmd, *a, **k)


S.subprocess.run = _pdftotext_mudo

# Y patrones PROPIOS, no los de la titular. `_compilar()` devuelve `(?!)` —que no casa
# nunca— cuando falta `perfil.local.json`, que es un overlay gitignored y por tanto NO existe
# en el repo público: allí `es_suyo()` diría False a todo y este test moriría sin que nada
# estuviera roto. Pasó: verde en casa base, rojo en el CI. Un test que viaja a un repo público
# no puede necesitar los datos reales de nadie para correr.
S.DOB = __import__("re").compile(r"MARCA-DE-NACIMIENTO-DE-PRUEBA")
S.IDS = __import__("re").compile(r"(?!)")


def pdf_mudo(d, nombre):
    """Un .pdf del que `pdftotext` no saca nada: el caso del escaneado."""
    p = os.path.join(d, nombre)
    open(p, "wb").write(b"%PDF-1.4\n%%EOF\n")
    return p


def main():
    d = tempfile.mkdtemp(prefix="ocr_sidecar_")
    marca = "MARCA-DE-NACIMIENTO-DE-PRUEBA"   # lo que reconoce el patrón inyectado arriba

    p1 = pdf_mudo(d, "informe-a.pdf")
    open(p1 + ".ocr.txt", "w").write("Informe de imagen. " + marca)
    check("lee el sidecar 'X.pdf.ocr.txt' (el que genera el pipeline)",
          S.es_suyo(S._texto(p1)))

    p2 = pdf_mudo(d, "informe-b.pdf")
    open(os.path.join(d, "informe-b.ocr.txt"), "w").write("Informe de imagen. " + marca)
    check("sigue leyendo la convención vieja 'X.ocr.txt'", S.es_suyo(S._texto(p2)))

    p3 = pdf_mudo(d, "informe-c.pdf")
    open(p3 + ".ocr.txt", "w").write("Informe de imagen de OTRA persona, sin identificadores.")
    check("con OCR pero sin identidad, NO lo da por suyo (el criterio no se relaja)",
          not S.es_suyo(S._texto(p3)))

    p4 = pdf_mudo(d, "informe-d.pdf")
    check("sin ningún sidecar, no revienta y no lo da por suyo",
          not S.es_suyo(S._texto(p4)))

    print("test_subir_historial_ocr_sidecar: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
