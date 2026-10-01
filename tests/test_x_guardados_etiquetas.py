#!/usr/bin/env python3
"""test_x_guardados_etiquetas.py — las palabras clave casan por palabra, no dentro de otras.

1-oct-2026: una reseña de cine («testigos de cargo») salía como [sistema] porque «rag» se buscaba
como subcadena; «nim» casaba en «animal» y «ned » en «designed ».
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import x_guardados  # noqa: E402

flag = x_guardados._flag


class TestEtiquetas(unittest.TestCase):
    def test_falsos_positivos_de_subcadena(self):
        self.assertEqual(flag("A mí me gustan más testigos de cargo, 12 hombres sin piedad."), [])
        self.assertEqual(flag("an animal model, minimal storage"), [])
        self.assertNotIn("NED", flag("we designed and trained it"))
        self.assertNotIn("sistema", flag("the reagent kit"))

    def test_raices_siguen_casando(self):
        self.assertEqual(flag("Metastatic breast cancer trial"), ["NED"])
        self.assertEqual(flag("inmunoterapia con vacuna"), ["NED"])
        self.assertEqual(flag("Claude agents with RAG and MCP"), ["sistema"])
        self.assertEqual(flag("camino a NED, agentes"), ["NED", "sistema"])
        self.assertIn("NED", flag("{{DIAGNOSTICO}}"))

    def test_vacio(self):
        self.assertEqual(flag(""), [])
        self.assertEqual(flag(None), [])


if __name__ == "__main__":
    unittest.main()
