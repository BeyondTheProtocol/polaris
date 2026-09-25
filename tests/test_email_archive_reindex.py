#!/usr/bin/env python3
"""test_email_archive_reindex.py — tras archivar correo, el RAG se reindexa en el acto.

POR QUÉ (25-sep-2026, deuda kb-index-venv-bloqueada-en-autonomo, escalada). Tras `email_archive.py
archive` los cuerpos de correo nuevos no se podían consultar hasta el siguiente reindexado del
daemon, cada 3 h: justo la ventana de HOY, que es cuando se leen. Aquí se fija que el archivador
reindexa él mismo, solo cuando archivó algo, con el intérprete que tiene pypdf, y que nunca lo
hace en `--dry`. Todo con un lanzador falso: no se reindexa nada de verdad.
"""
import os
import sys
import tempfile
import unittest

os.environ.setdefault("BTP_STATE_DIR", tempfile.mkdtemp(prefix="email_archive_reindex_"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import email_archive as ea  # noqa: E402


class _R:
    def __init__(self, rc):
        self.returncode = rc


class Reindexa(unittest.TestCase):
    def setUp(self):
        self.llamadas = []
        self._venv, ea.VENV_PY = ea.VENV_PY, sys.executable   # un intérprete que existe seguro

    def tearDown(self):
        ea.VENV_PY = self._venv

    def _lanzar(self, rc=0):
        def lanzar(orden, **kw):
            self.llamadas.append(orden)
            return _R(rc)
        return lanzar

    def test_si_archivo_algo_reindexa_con_el_interprete_del_venv(self):
        rc = ea.reindexar(361, dry=False, lanzar=self._lanzar())
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.llamadas), 1)
        orden = self.llamadas[0]
        self.assertEqual(orden[0], ea.VENV_PY, "con el intérprete que tiene pypdf, no el del PATH")
        self.assertTrue(orden[1].endswith(os.path.join("tools", "kb.py")) and orden[2] == "index")

    def test_nada_archivado_no_reindexa(self):
        self.assertIsNone(ea.reindexar(0, dry=False, lanzar=self._lanzar()))
        self.assertEqual(self.llamadas, [], "un reindexado completo cuesta minutos: sin novedad, no")

    def test_en_dry_nunca_reindexa(self):
        self.assertIsNone(ea.reindexar(50, dry=True, lanzar=self._lanzar()))
        self.assertEqual(self.llamadas, [])

    def test_sin_venv_no_revienta_y_lo_dice(self):
        ea.VENV_PY = "/no/existe/python3"
        self.assertIsNone(ea.reindexar(5, dry=False, lanzar=self._lanzar()))
        self.assertEqual(self.llamadas, [], "sin el intérprete bueno lo deja al daemon")

    def test_un_reindexado_fallido_se_informa_con_su_codigo(self):
        self.assertEqual(ea.reindexar(3, dry=False, lanzar=self._lanzar(rc=1)), 1)


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    print("✅ REINDEXADO TRAS ARCHIVAR EN VERDE" if res.wasSuccessful() else "❌ revisar fallos")
    sys.exit(0 if res.wasSuccessful() else 1)
