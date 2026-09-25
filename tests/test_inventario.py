#!/usr/bin/env python3
"""test_inventario.py — el inventario tiene que acertar en las dos direcciones.

19-sep-2026, problema nº2 de `docs/lo-que-falta.md`: con 189 herramientas, nadie sabe cuáles
están muertas. Un inventario solo sirve si no miente hacia ningún lado:

  · si marca viva una pieza muerta, el catálogo sigue creciendo sin freno;
  · si marca huérfana una pieza viva, se borra algo que hace falta — y eso ya pasó en la primera
    pasada: 3 de 9 «huérfanas» sí estaban citadas, por el nombre del módulo SIN `.py`
    (`cn_fetch` en el panel). De ahí la búsqueda con límite de palabra.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import inventario  # noqa: E402


class TestClasifica(unittest.TestCase):
    def setUp(self):
        self.citas = {}
        self._grep = inventario._grep
        inventario._grep = lambda t, palabra=False: self.citas.get((t, palabra), [])

    def tearDown(self):
        inventario._grep = self._grep

    def test_viva_si_la_nombra_otra_pieza(self):
        self.citas[("onco.py", False)] = ["tools/radar_ned_diario.py"]
        estado, quien = inventario.clasificar("onco.py", set())
        self.assertEqual(estado, "viva")
        self.assertIn("tools/radar_ned_diario.py", quien)

    def test_solo_test_si_unicamente_la_cita_su_test(self):
        self.citas[("onco.py", False)] = ["tests/test_onco.py"]
        self.assertEqual(inventario.clasificar("onco.py", set())[0], "solo-test")

    def test_huerfana_si_no_la_nombra_nadie(self):
        self.assertEqual(inventario.clasificar("fantasma.py", set())[0], "huerfana")

    def test_entrada_si_la_ejecuta_un_daemon(self):
        self.assertEqual(inventario.clasificar("x_guardados.py", {"x_guardados.py"})[0], "entrada")

    def test_el_modulo_desnudo_tambien_cuenta(self):
        """`cn_fetch` sin `.py`: así lo nombran el panel y las reglas. Era el falso positivo."""
        self.citas[("cn_fetch", True)] = ["tools/anatomia.py"]
        self.assertEqual(inventario.clasificar("cn_fetch.py", set())[0], "viva")

    def test_citarse_a_si_misma_no_la_salva(self):
        self.citas[("sola.py", False)] = ["tools/sola.py"]
        self.assertEqual(inventario.clasificar("sola.py", set())[0], "huerfana")


class TestAgentes(unittest.TestCase):
    """Un agente se invoca por su nombre a secas, no por su fichero."""

    def test_la_extension_se_quita_siempre_no_solo_a_los_py(self):
        citas = {("diseno", True): [".claude/agents/orquestador.md"]}
        g = inventario._grep
        inventario._grep = lambda t, palabra=False: citas.get((t, palabra), [])
        try:
            estado, _ = inventario.clasificar("diseno.md", set(), ".claude/agents")
            self.assertEqual(estado, "viva")
        finally:
            inventario._grep = g

    def test_los_33_agentes_del_repo_estan_vivos(self):
        filas = inventario.inventario(inventario.AGENTES, ".md")
        huerfanos = [f["pieza"] for f in filas if f["estado"] == "huerfana"]
        self.assertEqual(huerfanos, [], "agentes sin citar: %s" % huerfanos)


class TestSobreElRepoDeVerdad(unittest.TestCase):
    def test_corre_y_clasifica_el_repo(self):
        filas = inventario.inventario()
        self.assertGreater(len(filas), 50)
        estados = {f["estado"] for f in filas}
        self.assertTrue({"viva", "entrada"} <= estados)
        # una pieza muy citada NO puede salir huérfana
        salida = [f for f in filas if f["pieza"] == "salida.py"]
        if salida:
            self.assertIn(salida[0]["estado"], ("viva", "entrada"))


if __name__ == "__main__":
    unittest.main()
