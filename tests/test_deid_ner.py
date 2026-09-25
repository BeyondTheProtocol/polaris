#!/usr/bin/env python3
"""test_deid_ner.py — la capa NER del BSC en deid.py: suma lo que el regex no ve y, si se pide y cae,
no se degrada en silencio.

POR QUÉ (25-sep-26, P4; idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión
del 25-sep-2026). Reproducido ese día: un texto inventado con nombre de paciente, hospital, médico y
domicilio salía del regex con esos cuatro intactos y el veredicto «sin identificadores detectados».
Con el modelo del BSC delante, los cuatro quedan tapados.

SOLO TEXTO INVENTADO. Dos bloques:
  1. Mecánica sin modelo (siempre): fusión de tramos, fail-closed al caer el NER, `ner=False` por
     defecto no arranca nada.
  2. Con el modelo real (solo si existe .venv-deid; si no, se SALTA y lo dice): lo inventado queda
     tapado con NER y el regex solo sigue dejando pasar lo mismo (si algún día deja de hacerlo, este
     test avisa de que la foto ha cambiado).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")

import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import deid  # noqa: E402

INVENTADO = ("Paciente Lucía Fernández Ortega, de 47 años, atendida en el Hospital Universitario "
             "La Paz por el Dr. Andrés Molina. Vive en Calle Mayor 14, Getafe. "
             "Carcinoma ductal infiltrante.")
IDENTIFICADORES = ("Fernández", "Ortega", "La Paz", "Molina", "Calle Mayor")
IDENTIFICADORES_NORM = ("Fernandez", "Ortega", "La Paz", "Molina", "Calle Mayor")


class Mecanica(unittest.TestCase):
    def setUp(self):
        self._orig = deid._spans_ner

    def tearDown(self):
        deid._spans_ner = self._orig

    def test_fusiona_tramos_solapados_y_contiguos(self):
        t, n = deid._enmascarar_spans("abcdefghij", [(0, 2, "X", 1), (2, 4, "X", 1), (3, 5, "X", 1),
                                                     (7, 9, "Y", 1)])
        self.assertEqual(t, deid.MARCA + "fg" + deid.MARCA + "j")
        self.assertEqual(n, 2)

    def test_une_nombre_y_apellido_separados_por_un_espacio(self):
        t, n = deid._enmascarar_spans("Ana Gil vino", [(0, 3, "N", 1), (4, 7, "N", 1)])
        self.assertEqual((t, n), (deid.MARCA + " vino", 1))

    def test_ignora_tramos_imposibles(self):
        t, n = deid._enmascarar_spans("abc", [(2, 1, "X", 1), (-1, 2, "X", 1), (0, 99, "X", 1)])
        self.assertEqual((t, n), ("abc", 0))

    def test_ner_suma_a_regex_no_lo_sustituye(self):
        ini = INVENTADO.index("Molina")
        deid._spans_ner = lambda t: [(ini, ini + len("Molina"), "NOMBRE_PERSONAL_SANITARIO", 0.99)]
        out, _ = deid.de_identificar(INVENTADO + " Tel 612345678.", ner=True)
        self.assertNotIn("Molina", out, "el tramo del NER tiene que taparse")
        self.assertNotIn("612345678", out, "el regex tiene que seguir actuando detrás del NER")

    def test_ner_caido_verificado_devuelve_none(self):
        def cae(t):
            raise deid.NERNoDisponible("simulado")
        deid._spans_ner = cae
        txt, n, ok, motivo = deid.de_identificar_verificado(INVENTADO, ner=True)
        self.assertIsNone(txt, "NER pedido y caído: nada de devolver el resultado solo-regex")
        self.assertFalse(ok)
        self.assertIn("NER", motivo)

    def test_sin_venv_es_no_disponible_no_lista_vacia(self):
        orig = deid._python_ner
        deid._python_ner = lambda: None
        try:
            with self.assertRaises(deid.NERNoDisponible):
                self._orig("texto")
        finally:
            deid._python_ner = orig

    def test_por_defecto_no_arranca_el_ner(self):
        def no_toques(t):
            raise AssertionError("ner=False no puede llamar al modelo")
        deid._spans_ner = no_toques
        deid.de_identificar(INVENTADO)
        deid.de_identificar_verificado(INVENTADO)

    def test_cli_ner_caido_sale_1_sin_texto(self):
        r = subprocess.run([sys.executable, "-c",
                            "import sys; sys.path.insert(0, %r); import deid; "
                            "deid._python_ner = lambda: None; sys.exit(deid.main(['--ner', 'hola']))"
                            % os.path.join(ROOT, "tools")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.strip(), "", "caído: stdout vacío, nunca el texto a medias")


@unittest.skipUnless(deid._python_ner(), "sin .venv-deid: se salta la prueba con el modelo real")
class ConModeloReal(unittest.TestCase):
    def test_regex_solo_deja_pasar_lo_inventado(self):
        """La foto del hueco. Si esto empieza a fallar, el regex ha mejorado: actualiza el test."""
        out, _ = deid.de_identificar(INVENTADO)
        escapan = [x for x in IDENTIFICADORES_NORM if x in out]
        self.assertTrue(escapan, "el regex ya tapa lo inventado; revisa si este test sigue teniendo sentido")

    def test_con_ner_no_escapa_ninguno(self):
        out, _ = deid.de_identificar(INVENTADO, ner=True)
        for x in IDENTIFICADORES + IDENTIFICADORES_NORM:
            self.assertNotIn(x, out, "%r escapa con la capa NER" % x)
        self.assertIn("Carcinoma ductal infiltrante", out, "lo clínico no se toca")


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=1).result
    if res.wasSuccessful():
        print("✅ CAPA NER EN VERDE (%d casos, %d saltados)" % (res.testsRun, len(res.skipped)))
    raise SystemExit(0 if res.wasSuccessful() else 1)
