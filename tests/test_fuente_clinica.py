#!/usr/bin/env python3
"""test_fuente_clinica.py — el cotejo contra la bóveda abre el fichero de verdad.

POR QUÉ EXISTE (24-sep-2026, auditoría externa de Marcos Gorgojo, hallazgo 1.1). El panel de
decisión de alto riesgo sellaba como «verificado» un veredicto cuya `contra_fuente` era una ruta
que NO existía: se miraba la FORMA del puntero con una regex y nadie abría nada. Lo único que un
modelo no puede fingir es abrir la fuente y encontrar dentro el fragmento que cita. Este test fija
que `tools/fuente_clinica.py` lo hace, y que lo hace sin sacar el dato clínico de donde está.

Sin red, sin LLM, sin tocar la bóveda real: la bóveda es un tmp con informes sintéticos.
"""
import hashlib
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

_TMP = tempfile.mkdtemp(prefix="fuente_clinica_test_")
BOVEDA = os.path.join(_TMP, "_PRIVADO_CLINICO")
os.makedirs(os.path.join(BOVEDA, "sub"))
os.environ["BTP_BOVEDA_CLINICA"] = BOVEDA

import fuente_clinica as fc  # noqa: E402

FRAGMENTO = "Receptores hormonales: RE 90 %, RP negativo"
TEXTO = "Informe sintético.\n\n" + FRAGMENTO + ".\nFirma: nadie.\n"


def _escribe(rel, contenido, modo="w"):
    ruta = os.path.join(BOVEDA, rel)
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, modo, **({} if "b" in modo else {"encoding": "utf-8"})) as f:
        f.write(contenido)
    return ruta


_escribe("informe.md", TEXTO)
_escribe("sub/vacio.txt", "")
_escribe("imagen.dcm", b"DICM\x00\x01\x02\xff\xfe", "wb")
_escribe("escaneado.pdf", b"%PDF-1.4 no es un pdf de verdad", "wb")
_escribe("con-sidecar.pdf", b"%PDF-1.4 tampoco", "wb")
_escribe("con-sidecar.pdf.ocr.txt", "Texto OCR: " + FRAGMENTO + "\n")
FUERA = os.path.join(_TMP, "fuera.md")
with open(FUERA, "w", encoding="utf-8") as _f:
    _f.write(TEXTO)
os.symlink(FUERA, os.path.join(BOVEDA, "enlace-afuera.md"))


class Cotejo(unittest.TestCase):
    def setUp(self):
        self.accesos = []
        self.identidad = ("coincide", "stub")
        fc._IDENTIDAD = lambda texto, ruta: self.identidad
        fc._LOG = lambda agente, resultado, ruta: self.accesos.append((agente, resultado, ruta))

    # ── existencia ────────────────────────────────────────────────────────────────────────
    def test_fichero_que_no_existe(self):
        r = fc.cotejar("_PRIVADO_CLINICO/informe-que-no-existe-2099.md", FRAGMENTO)
        self.assertFalse(r["ok"])
        self.assertEqual(r["estado"], "no_existe")

    def test_existe_sin_fragmento_es_solo_existencia(self):
        r = fc.cotejar("_PRIVADO_CLINICO/informe.md")
        self.assertTrue(r["ok"])
        self.assertEqual(r["estado"], "existe")
        self.assertEqual(r["sha256"], hashlib.sha256(TEXTO.encode("utf-8")).hexdigest())

    def test_fichero_vacio(self):
        r = fc.cotejar("_PRIVADO_CLINICO/sub/vacio.txt")
        self.assertFalse(r["ok"])
        self.assertEqual(r["estado"], "vacia")

    def test_ruta_absoluta_dentro_de_la_boveda(self):
        self.assertTrue(fc.cotejar(os.path.join(BOVEDA, "informe.md"), FRAGMENTO)["ok"])

    # ── contención: no se sale de la bóveda ni con `..` ni con enlaces ─────────────────────
    def test_escape_con_puntos(self):
        r = fc.cotejar("_PRIVADO_CLINICO/../fuera.md", FRAGMENTO)
        self.assertFalse(r["ok"])
        self.assertEqual(r["estado"], "fuera_de_boveda")

    def test_enlace_que_sale_de_la_boveda(self):
        r = fc.cotejar("_PRIVADO_CLINICO/enlace-afuera.md", FRAGMENTO)
        self.assertFalse(r["ok"])
        self.assertEqual(r["estado"], "fuera_de_boveda")

    def test_ruta_absoluta_fuera(self):
        self.assertEqual(fc.cotejar(FUERA, FRAGMENTO)["estado"], "fuera_de_boveda")

    def test_referencia_sin_segmento_de_boveda(self):
        self.assertEqual(fc.cotejar("informe.md", FRAGMENTO)["estado"], "fuera_de_boveda")

    # ── el fragmento tiene que estar DENTRO ───────────────────────────────────────────────
    def test_fragmento_presente(self):
        r = fc.cotejar("_PRIVADO_CLINICO/informe.md", FRAGMENTO)
        self.assertTrue(r["ok"])
        self.assertEqual(r["estado"], "confirmada")

    def test_fragmento_tolera_tildes_mayusculas_y_espacios(self):
        r = fc.cotejar("_PRIVADO_CLINICO/informe.md", "receptores   HORMONALES: re 90%,  rp negativo")
        self.assertTrue(r["ok"], r["motivo"])

    def test_fragmento_ausente(self):
        r = fc.cotejar("_PRIVADO_CLINICO/informe.md", "Receptores hormonales: RE 10 %, RP positivo")
        self.assertFalse(r["ok"])
        self.assertEqual(r["estado"], "fragmento_ausente")

    def test_fragmento_demasiado_corto_no_es_cotejo(self):
        r = fc.cotejar("_PRIVADO_CLINICO/informe.md", "RE 90")
        self.assertFalse(r["ok"])
        self.assertEqual(r["estado"], "fragmento_corto")

    # ── formatos ──────────────────────────────────────────────────────────────────────────
    def test_binario_no_es_cotejable(self):
        r = fc.cotejar("_PRIVADO_CLINICO/imagen.dcm", FRAGMENTO)
        self.assertFalse(r["ok"])
        self.assertEqual(r["estado"], "no_cotejable")

    def test_pdf_sin_capa_de_texto_ni_sidecar_no_es_cotejable(self):
        r = fc.cotejar("_PRIVADO_CLINICO/escaneado.pdf", FRAGMENTO)
        self.assertFalse(r["ok"])
        self.assertEqual(r["estado"], "no_cotejable")

    def test_pdf_con_sidecar_ocr(self):
        self.assertTrue(fc.cotejar("_PRIVADO_CLINICO/con-sidecar.pdf", FRAGMENTO)["ok"])

    # ── de quién es el informe ────────────────────────────────────────────────────────────
    def test_informe_de_otra_paciente_no_confirma(self):
        self.identidad = ("otro_paciente", "stub")
        r = fc.cotejar("_PRIVADO_CLINICO/informe.md", FRAGMENTO)
        self.assertFalse(r["ok"])
        self.assertEqual(r["estado"], "identidad_ajena")

    def test_sin_saber_de_quien_es_no_confirma(self):
        self.identidad = ("sin_overlay", "stub")
        self.assertFalse(fc.cotejar("_PRIVADO_CLINICO/informe.md", FRAGMENTO)["ok"])

    # ── muro: se registra cada lectura y el fragmento no sale ─────────────────────────────
    def test_cada_lectura_deja_linea_en_el_registro(self):
        fc.cotejar("_PRIVADO_CLINICO/informe.md", FRAGMENTO, quien="verificacion")
        self.assertTrue(self.accesos)
        agente, resultado, ruta = self.accesos[-1]
        self.assertIn("verificacion", agente)
        self.assertTrue(resultado.startswith("COTEJO-"))

    def test_el_resultado_no_lleva_el_fragmento_ni_el_contenido(self):
        r = fc.cotejar("_PRIVADO_CLINICO/informe.md", FRAGMENTO)
        plano = repr(r).lower()
        self.assertNotIn("receptores", plano)
        self.assertNotIn("negativo", plano)
        self.assertEqual(r["sha256_fragmento"], fc.huella_fragmento(FRAGMENTO))

    def test_sin_ventanilla_auditada_no_se_lee(self):
        fc._LOG = None
        fc._IDENTIDAD = None
        viejo = fc._ventanilla
        fc._ventanilla = lambda: None
        try:
            r = fc.cotejar("_PRIVADO_CLINICO/informe.md", FRAGMENTO)
        finally:
            fc._ventanilla = viejo
        self.assertFalse(r["ok"])
        self.assertEqual(r["estado"], "sin_ventanilla")

    # ── punteros dentro de un texto libre ─────────────────────────────────────────────────
    def test_punteros_en_texto_libre(self):
        t = ("reference-clinical-profile; _PRIVADO_CLINICO/informe.md; PMID:39538331\n"
             "y además _PRIVADO_CLINICO/sub/otro informe con espacios.pdf")
        self.assertEqual(fc.punteros(t), ["_PRIVADO_CLINICO/informe.md",
                                          "_PRIVADO_CLINICO/sub/otro informe con espacios.pdf"])


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=0).run(unittest.TestLoader().loadTestsFromTestCase(Cotejo))
    if res.wasSuccessful():
        print("✅ FUENTE CLÍNICA EN VERDE (%d casos)" % res.testsRun)
    sys.exit(0 if res.wasSuccessful() else 1)
