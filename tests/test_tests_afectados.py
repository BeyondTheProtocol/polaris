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


class PuertaDeFusion(unittest.TestCase):
    """10-oct-26 · la puerta por impacto: lo que puede romper el muro SIN que un grafo lo vea exige
    la suite COMPLETA; el resto, afectadas + núcleo fijo. Aquí se fija que tocar el muro sigue
    disparando la suite entera, con los ficheros REALES del repo (no solo con ejemplos)."""

    REALES = T.baterias()

    def test_cualquier_hook_real_exige_la_completa(self):
        carpeta = os.path.join(ROOT, ".claude", "hooks")
        hooks = sorted(os.listdir(carpeta))
        self.assertGreater(len(hooks), 20, "no vio los hooks: el test pasaría en vacío")
        for h in hooks:
            r = T.puerta([".claude/hooks/" + h], self.REALES, GrafoFalso({}))
            self.assertEqual(r["veredicto"], "COMPLETA", h)
            self.assertEqual(r["seleccion"], self.REALES, h)

    def test_cada_tool_que_un_hook_usa_exige_la_completa(self):
        muro = T.tools_de_hooks()
        self.assertGreater(len(muro), 20, "tools_de_hooks() salió casi vacío: pasaría en vacío")
        for need in ("tools/salida.py", "tools/_secrets.py", "tools/lector_clinico.py",
                     "tools/permiso_envio.py"):
            self.assertIn(need, muro, "ya no se ve que un hook use %s" % need)
        for t in sorted(muro):
            self.assertEqual(T.puerta([t], self.REALES, GrafoFalso({}))["veredicto"], "COMPLETA", t)

    def test_settings_runner_y_ayudantes_exigen_la_completa(self):
        for f in (".claude/settings.json", ".claude/settings.local.json", "tests/test_all.sh",
                  "tests/_entorno.py", "tools/tests_afectados.py", "tools/normas.json",
                  "tools/config/politica_aprobacion.json"):
            self.assertEqual(T.puerta([f], self.REALES, GrafoFalso({}))["veredicto"], "COMPLETA", f)

    def test_un_cambio_ajeno_al_muro_es_rapida_y_lleva_el_nucleo(self):
        r = T.puerta(["tools/viajes_precios.py", "README.md"], self.REALES, GrafoFalso({}),
                     muro_tools=set())
        self.assertEqual(r["veredicto"], "RAPIDA")
        self.assertTrue(T.nucleo_muro(self.REALES) <= r["seleccion"])
        self.assertIn("test_fuga.sh", r["seleccion"])
        self.assertNotEqual(r["seleccion"], self.REALES, "la rápida no puede ser la completa")

    def test_solo_documentacion_es_rapida_con_solo_el_nucleo(self):
        r = T.puerta(["README.md"], self.REALES, GrafoFalso({}), muro_tools=set())
        self.assertEqual(r["veredicto"], "RAPIDA")
        self.assertEqual(r["seleccion"], T.nucleo_muro(self.REALES))

    def test_constitucion_reglas_y_agentes_exigen_la_completa(self):
        """Hueco hallado al medir (10-oct): un .md no tiene dependientes en el grafo, así que tocar
        CLAUDE.md o una regla caía en RAPIDA con solo el núcleo y no corría test_constitucion_*."""
        for f in ("CLAUDE.md", ".claude/rules/clinico.md", ".claude/agents/tecnico.md",
                  ".claude/skills/a11y/SKILL.md"):
            self.assertEqual(T.puerta([f], self.REALES, GrafoFalso({}))["veredicto"], "COMPLETA", f)
        self.assertEqual(T.puerta(["README.md"], self.REALES, GrafoFalso({}),
                                  muro_tools=set())["veredicto"], "RAPIDA")

    def test_mezcla_basta_un_fichero_del_muro(self):
        r = T.puerta(["README.md", "tools/viajes_precios.py", ".claude/hooks/muro_guard.py"],
                     self.REALES, GrafoFalso({}))
        self.assertEqual(r["veredicto"], "COMPLETA")

    def test_el_cli_dice_la_primera_linea(self):
        import subprocess
        out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "tests_afectados.py"), "--puerta"],
                             capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL).stdout
        self.assertIn(out.splitlines()[0], ("COMPLETA", "RAPIDA"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
