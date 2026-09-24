#!/usr/bin/env python3
"""Tests para inventario.solapes()."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.inventario import solapes


class TestSolapes(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tools_dir = os.path.join(self.tmpdir.name, "tools")
        os.makedirs(self.tools_dir)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _crear_py(self, nombre: str, docstring: str):
        with open(os.path.join(self.tools_dir, nombre), "w", encoding="utf-8") as f:
            f.write(f'"""{docstring}"""\n# dummy\n')

    def _patch(self):
        import tools.inventario as inv
        def patched(p):
            if "tools" in p:
                return self.tools_dir if p.endswith("tools") else os.path.join(self.tools_dir, os.path.basename(p))
            return os.path.abspath(p)
        orig = os.path.abspath
        os.path.abspath = patched
        return orig

    def test_sin_solapes(self):
        self._crear_py("a.py", "Herramienta para auditar logs")
        self._crear_py("b.py", "Genera gráficos de rendimiento")
        self._crear_py("inventario.py", "Inventario de herramientas Polaris")
        orig = self._patch()
        try:
            self.assertEqual(len(solapes(0.9)), 0)
        finally:
            os.path.abspath = orig

    def test_con_solap(self):
        self._crear_py("x.py", "Herramienta para auditar logs")
        self._crear_py("y.py", "Herramienta para auditar log")
        self._crear_py("inventario.py", "Inventario de herramientas Polaris")
        orig = self._patch()
        try:
            pares = solapes(0.85)
            self.assertGreater(len(pares), 0)
            self.assertGreater(pares[0][2], 0.85)
        finally:
            os.path.abspath = orig

    def test_orden(self):
        self._crear_py("a.py", "Herramienta para auditar logs")
        self._crear_py("b.py", "Herramienta para auditar log")
        self._crear_py("c.py", "Herramienta para auditar")
        self._crear_py("inventario.py", "Inventario de herramientas Polaris")
        orig = self._patch()
        try:
            sims = [p[2] for p in solapes(0.7)]
            self.assertEqual(sims, sorted(sims, reverse=True))
        finally:
            os.path.abspath = orig


if __name__ == "__main__":
    unittest.main()
