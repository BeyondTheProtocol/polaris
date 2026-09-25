#!/usr/bin/env python3
"""test_deid_diccionario.py — la capa de diccionario propio de deid.py tapa SUS identificadores
concretos aunque lleguen sin etiqueta, partidos o con otra grafía.

POR QUÉ (25-sep-26, P4 paso 4; idea de {{CONTACTO}} (https://contacto), con su agente KAI,
revisión del 25-sep-2026). Medido ese día con sus valores reales (sin mostrarlos): sin esta capa
escapaban 28 de 31 formas de escribir sus ids, fecha de nacimiento, contactos y lugares de su ruta;
con ella, 0 de 31. Los overlays ya existían; deid.py solo usaba la lista de nombres.

SOLO VALORES INVENTADOS: los overlays se fabrican en un tmp y se apunta la capa a ellos.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import deid  # noqa: E402

PERFIL = {"titular": {"nombre": "Zuriñe", "apellidos": ["Etxeberría Lasa", "Lasa"],
                      "nacimiento": ["3 de abril de 1971"], "contactos": ["zl0419ab"],
                      "telefonos": ["699112233"]}}
# el patrón de nacimiento usa una forma que el detector genérico de fechas NO caza, para que el
# test mida esta capa y no la de fechas
IDENT = {"ids": ["55667788", "9081726"], "dob_patrones": [r"abril\s+del?\s+71", "(roto"],
         "deid_terminos": ["CARM999000111", "ZURINEH"]}
NOMBRES = {"nombres": [], "lugares_ruta": ["Hospital de Txagorritxu"]}


def _escribe(d, nombre, datos):
    with open(os.path.join(d, nombre), "w", encoding="utf-8") as fh:
        json.dump(datos, fh, ensure_ascii=False)


class Diccionario(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = tempfile.TemporaryDirectory()
        _escribe(self.tmp.name, "perfil.local.json", PERFIL)
        _escribe(self.tmp.name, "identidad.local.json", IDENT)
        _escribe(self.base.name, "nombres.local.json", NOMBRES)   # en «casa base»: se une
        self._orig = (deid._OVERLAYS_DIR, deid._DICC)
        deid._OVERLAYS_DIR = [self.tmp.name, self.base.name]
        deid._DICC = None

    def tearDown(self):
        deid._OVERLAYS_DIR, deid._DICC = self._orig
        self.tmp.cleanup()
        self.base.cleanup()

    def _sale(self, texto):
        return deid.de_identificar(texto)[0]

    def test_id_sin_etiqueta_y_con_separadores(self):
        import re
        for forma, cifras in (("55667788", "55667788"), ("55.667.788", "55667788"),
                              ("55 667 788", "55667788"), ("5566-7788", "55667788"),
                              ("908 17 26", "9081726")):
            out = self._sale("Ref %s del informe" % forma)
            self.assertNotIn(cifras, re.sub(r"\D", "", out), "el id escrito como %r escapa" % forma)

    def test_id_dentro_de_un_numero_mayor_no_se_toca(self):
        out = self._sale("lote 1556677889 de reactivo")
        self.assertIn("1556677889", out, "el diccionario no puede morder dentro de otro número")

    def test_nombre_sin_tildes_ni_mayusculas(self):
        out = self._sale("SRA. ETXEBERRIA LASA, ZURINE, 55a")
        self.assertNotIn("ETXEBERRIA", out)
        self.assertNotIn("ZURINE", out.upper())

    def test_deid_terminos_solo_para_tapar(self):
        out = self._sale("CIP CARM999000111, firmado ZURINEH")
        self.assertNotIn("CARM999000111", out)
        self.assertNotIn("ZURINEH", out)

    def test_nombre_con_guiones_bajos_de_fichero(self):
        out = self._sale("paquete ZURINE_ETXEBERRIA_LASA.pdf del archivo")
        self.assertNotIn("ZURINE", out)
        self.assertNotIn("ETXEBERRIA", out)

    def test_apellido_compuesto_no_deja_restos(self):
        out = self._sale("Paciente Etxeberría Lasa acude")
        self.assertNotIn("Lasa", out)
        self.assertNotIn("Etxeberr", out)

    def test_nacimiento_literal_y_por_patron(self):
        self.assertNotIn("1971", self._sale("nacida el 3 de abril de 1971 en"))
        self.assertNotIn("del 71", self._sale("nacida en abril del 71, acude"))

    def test_contacto_y_telefono(self):
        out = self._sale("usuario zl0419ab, movil 699 11 22 33")
        self.assertNotIn("zl0419ab", out)
        self.assertNotIn("112233", out.replace(" ", ""))

    def test_lugar_de_casa_base_se_une(self):
        self.assertNotIn("Txagorritxu", self._sale("derivada al Hospital de Txagorritxu"))

    def test_patron_roto_no_rompe_la_capa(self):
        self.assertTrue(deid._diccionario(), "un patrón inválido se ignora, el resto carga")

    def test_lo_clinico_no_se_toca(self):
        self.assertIn("Carcinoma lobulillar", self._sale("Carcinoma lobulillar, Zuriñe"))


class SinOverlays(unittest.TestCase):
    def test_sin_overlays_queda_vacio_y_deid_sigue(self):
        orig = (deid._OVERLAYS_DIR, deid._DICC)
        with tempfile.TemporaryDirectory() as vacio:
            deid._OVERLAYS_DIR, deid._DICC = [vacio], None
            try:
                self.assertEqual(deid._diccionario(), [])
                self.assertNotIn("612345678", deid.de_identificar("tel 612345678")[0])
            finally:
                deid._OVERLAYS_DIR, deid._DICC = orig


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=1).result
    if res.wasSuccessful():
        print("✅ DICCIONARIO PROPIO EN VERDE (%d casos)" % res.testsRun)
    raise SystemExit(0 if res.wasSuccessful() else 1)
