#!/usr/bin/env python3
"""test_kb_extensiones.py — issue #39: kb indexa `.PDF`, `.MD` y `.TXT` aunque la extensión venga
en mayúsculas.

`build()` buscaba con `glob("*.pdf")`, que distingue mayúsculas también en macOS: un
`SCAN0001.PDF` descargado del portal del hospital no entraba en el índice y nada lo decía. Si
además traía capa de texto, `ocr_informes.py` tampoco lo tocaba («kb.py ya lo lee»).

Se indexa una fuente de verdad sintética en un tmp, con `kb.FV/IDX/DB/VEC` redirigidos como en
`test_kb_pdf_avisos.py`, y se mira el efecto: la ruta está en el índice y `retrieve()` la
encuentra. Para los PDF va un doble de `pypdf`: el CI no lo instala, y lo que se prueba aquí es
qué ficheros recoge `build()`, no cómo se extrae el texto.
"""
import contextlib
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "tools"))
import kb  # noqa: E402


class _Pagina:
    def __init__(self, texto):
        self._texto = texto

    def extract_text(self):
        return self._texto


class _PdfReader:
    def __init__(self, ruta):
        with open(ruta, encoding="utf-8") as f:
            self.pages = [_Pagina(f.read())]


def _pypdf_falso():
    modulo = types.ModuleType("pypdf")
    modulo.PdfReader = _PdfReader
    return modulo


class _FuenteSintetica(unittest.TestCase):
    PYPDF = "falso"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kb_ext_")
        self._rutas = (kb.FV, kb.IDX, kb.DB, kb.VEC)
        kb.FV = self.tmp
        kb.IDX = os.path.join(self.tmp, ".kb_index.json")
        kb.DB = os.path.join(self.tmp, ".kb_index.db")
        kb.VEC = os.path.join(self.tmp, ".kb_index.vectors.npy")
        self._pypdf = sys.modules.get("pypdf", "no-estaba")
        sys.modules["pypdf"] = _pypdf_falso() if self.PYPDF == "falso" else None

    def tearDown(self):
        kb.FV, kb.IDX, kb.DB, kb.VEC = self._rutas
        if self._pypdf == "no-estaba":
            sys.modules.pop("pypdf", None)
        else:
            sys.modules["pypdf"] = self._pypdf
        shutil.rmtree(self.tmp, ignore_errors=True)

    def escribir(self, nombre, texto):
        with open(os.path.join(self.tmp, nombre), "w", encoding="utf-8") as f:
            f.write(texto)

    def indexar(self):
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            rc = kb.build()
        self.stderr = err.getvalue()
        return rc

    def rutas_indexadas(self):
        if not os.path.exists(kb.DB):
            return set()
        con = sqlite3.connect(kb.DB)
        try:
            return {r[0] for r in con.execute("SELECT DISTINCT path FROM chunks")}
        finally:
            con.close()


class ExtensionEnMayusculas(_FuenteSintetica):
    def test_md_y_txt_en_mayusculas_se_indexan(self):
        self.escribir("nota.md", "Control: la revisión anual en minúsculas sigue igual que siempre.")
        self.escribir("NOTA.MD", "Nota con extensión en mayúsculas sobre la revisión trimestral.")
        self.escribir("Resumen.Md", "Resumen con la extensión mezclada, preparado para la consulta.")
        self.escribir("apuntes.TXT", "Apuntes en texto plano con la extensión escrita en mayúsculas.")
        self.indexar()
        self.assertEqual(self.rutas_indexadas(),
                         {"nota.md", "NOTA.MD", "Resumen.Md", "apuntes.TXT"})

    def test_pdf_en_mayusculas_se_indexa_y_se_encuentra(self):
        """Antes quedaba fuera y `retrieve()` no lo devolvía nunca."""
        self.escribir("informe.pdf", "Informe de control en minúsculas, sin cambios desde marzo.")
        self.escribir("SCAN0001.PDF", "Escaneo del portal con la palabra clave zurcidora dentro.")
        self.escribir("Analitica.Pdf", "Analítica descargada con la extensión escrita a medias.")
        self.indexar()
        self.assertEqual(self.rutas_indexadas(), {"informe.pdf", "SCAN0001.PDF", "Analitica.Pdf"})
        rutas = [r[0] for r in kb.retrieve("zurcidora")]
        self.assertIn("SCAN0001.PDF", rutas)

    def test_el_resto_de_extensiones_sigue_fuera(self):
        """El arreglo no amplía lo que se indexa: solo deja de mirar las mayúsculas."""
        self.escribir("nota.md", "Control para que el índice no quede vacío en esta prueba.")
        self.escribir("foto.JPG", "no es texto que kb deba leer, aunque aquí lo parezca.")
        self.escribir("datos.JSON", "tampoco es una extensión que kb indexe hoy en ningún caso.")
        self.escribir("borrador.MDX", "se parece a .MD pero es otra extensión distinta del todo.")
        self.indexar()
        self.assertEqual(self.rutas_indexadas(), {"nota.md"})


class SinPypdf(_FuenteSintetica):
    PYPDF = "ausente"

    def test_un_pdf_en_mayusculas_tambien_frena_el_build(self):
        """El FAIL-LOUD sin pypdf solo contaba los `.pdf`: con un `.PDF` seguía adelante y lo
        dejaba fuera del índice sin decir nada. Ahora se planta y no toca el índice."""
        self.escribir("nota.md", "Nota normal que sí se podría indexar sin pypdf en el intérprete.")
        self.escribir("SCAN0001.PDF", "Escaneo que este intérprete no sabe leer por faltarle pypdf.")
        self.assertEqual(self.indexar(), 1)
        self.assertIn("se perderían 1 PDFs", self.stderr)
        self.assertFalse(os.path.exists(kb.DB))


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    print("✅ KB EXTENSIONES EN VERDE" if res.wasSuccessful() else "❌ revisar fallos")
    sys.exit(0 if res.wasSuccessful() else 1)
