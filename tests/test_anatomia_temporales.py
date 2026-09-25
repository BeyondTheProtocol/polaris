#!/usr/bin/env python3
"""test_anatomia_temporales.py — el panel no cuenta las copias `_mutante_*.py` como tools.

`tools/mutantes.py` las escribe junto al original mientras corre una batería en casa base. Si
`anatomia.herramientas()` las cuenta, la huella del panel oscila según haya o no una batería en
marcha, y un sello hecho en ese momento miente (deuda `anatomia_cuenta_mutantes_temporales`,
26-sep-26). Sin `_entorno.exige`: usa un árbol falso, así que corre también con el HALT puesto.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import anatomia  # noqa: E402


class SinTemporales(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="anatomia_tmp_test_")
        os.makedirs(os.path.join(self.tmp, "tools"))
        for n in ("kb.py", "_mutante_abc123.py", "_mutante_bla_wiu5.py"):
            with open(os.path.join(self.tmp, "tools", n), "w") as f:
                f.write("x = 1\n")
        self.viejo, anatomia.ROOT = anatomia.ROOT, self.tmp

    def tearDown(self):
        anatomia.ROOT = self.viejo

    def test_no_cuenta_mutantes(self):
        h = anatomia.herramientas()
        nombres = [n for v in h["familias"].values() for n in v]
        self.assertEqual(nombres, ["kb"])
        self.assertEqual(h["total"], 1)

    def test_la_superficie_no_cambia_con_una_bateria_en_marcha(self):
        con = anatomia.herramientas()["familias"]
        for n in os.listdir(os.path.join(self.tmp, "tools")):
            if n.startswith("_mutante_"):
                os.remove(os.path.join(self.tmp, "tools", n))
        self.assertEqual(anatomia.herramientas()["familias"], con)


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    ok = r.wasSuccessful()
    print("✅ anatomía sin temporales: %d tests en verde" % r.testsRun if ok
          else "❌ anatomía sin temporales: %d fallo(s)" % (len(r.failures) + len(r.errors)))
    sys.exit(0 if ok else 1)
