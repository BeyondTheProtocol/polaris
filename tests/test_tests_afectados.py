#!/usr/bin/env python3
"""test_tests_afectados.py — `test_all.sh --cambiados` elige las baterías que toca y no menos.

POR QUÉ EXISTE (26-sep-26). La suite completa tarda más de 10 minutos y varias sesiones la
lanzaban a la vez para comprobaciones intermedias. `tools/tests_afectados.py` elige solo las
baterías afectadas. Si se equivoca por DEFECTO, un fallo pasa sin verse hasta la suite completa;
por eso lo que más se fija aquí es que no se quede corto donde más duele (el muro).

Se fija:
  · tocar un hook del muro → entran todas las baterías del muro, aunque el grafo no las vea;
  · tocar `.claude/settings.json` → igual;
  · tocar `tests/test_all.sh` → TODO;
  · tocar un test → entra ese test;
  · tocar una tool → entran los tests que dependen de ella según el grafo;
  · tocar solo `.md` → ninguna batería;
  · una batería que test_all.sh no conoce no se cuela.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import tests_afectados as T  # noqa: E402

DISPONIBLES = {"test_muro_fase0.py", "test_salida_guard.py", "test_ok_envio_blindado.py",
               "test_fuga.sh", "test_healthcheck_cpu.py", "test_bucles_colgados.py",
               "test_web_lint.py"}


class GrafoFalso:
    def __init__(self, deps):
        self.deps = deps

    def buscar(self, f):
        return f

    def quien(self, dst, hondo=False):
        return {d: {"import"} for d in self.deps.get(dst, [])}


class Seleccion(unittest.TestCase):

    def test_hook_del_muro_arrastra_todo_el_muro(self):
        todo, sel = T.afectados([".claude/hooks/salida_guard.py"], DISPONIBLES, GrafoFalso({}))
        self.assertFalse(todo)
        for b in ("test_muro_fase0.py", "test_salida_guard.py", "test_ok_envio_blindado.py",
                  "test_fuga.sh"):
            self.assertIn(b, sel)
        self.assertNotIn("test_web_lint.py", sel)

    def test_settings_tambien_es_muro(self):
        _todo, sel = T.afectados([".claude/settings.json"], DISPONIBLES, GrafoFalso({}))
        self.assertIn("test_muro_fase0.py", sel)

    def test_tocar_test_all_es_todo(self):
        todo, sel = T.afectados(["tests/test_all.sh"], DISPONIBLES, GrafoFalso({}))
        self.assertTrue(todo)
        self.assertEqual(sel, DISPONIBLES)

    def test_tocar_un_test_lo_incluye(self):
        _todo, sel = T.afectados(["tests/test_healthcheck_cpu.py"], DISPONIBLES, GrafoFalso({}))
        self.assertEqual(sel, {"test_healthcheck_cpu.py"})

    def test_tocar_una_tool_trae_sus_dependientes(self):
        g = GrafoFalso({"tools/bucles_colgados.py": ["tests/test_bucles_colgados.py",
                                                      "tools/healthcheck.py"]})
        _todo, sel = T.afectados(["tools/bucles_colgados.py"], DISPONIBLES, g)
        self.assertEqual(sel, {"test_bucles_colgados.py"})

    def test_lo_que_el_grafo_no_ve_entra_por_nombre(self):
        """Un .mjs no está en el grafo: entra el test que lo nombra (el repo real)."""
        _todo, sel = T.afectados(["tools/_chrome_headless.mjs"], T.baterias(), GrafoFalso({}))
        self.assertIn("test_chrome_headless_cierra.py", sel)

    def test_solo_documentacion_no_corre_nada(self):
        todo, sel = T.afectados(["README.md", "00_FUENTE-DE-VERDAD/x.md"], DISPONIBLES,
                                GrafoFalso({}))
        self.assertFalse(todo)
        self.assertEqual(sel, set())

    def test_bateria_desconocida_no_se_cuela(self):
        _todo, sel = T.afectados(["tests/test_que_no_esta_en_test_all.py"], DISPONIBLES,
                                 GrafoFalso({}))
        self.assertEqual(sel, set())

    def test_con_el_repo_real_no_revienta(self):
        self.assertTrue(T.baterias(), "no encontró baterías en test_all.sh")
        T.cambiados()


if __name__ == "__main__":
    unittest.main(verbosity=2)
