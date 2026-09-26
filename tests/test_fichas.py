#!/usr/bin/env python3
"""test_fichas.py — ninguna herramienta de tools/ vive sin ficha, y crear una sin ficha falla.

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.
Criterio de «hecho» que propusieron: crear una herramienta sin ficha falla en un test. Eso es
`TestFantasma`: el catálogo real tiene que estar limpio, y una pieza fantasma sin ficha en un
árbol de prueba tiene que salir señalada, igual que la saldría en el de verdad.

También cubre el hook `ficha_guard.py` (deniega el Write que crea, deja pasar lo demás) y que
el README no vuelva a contar mal los agentes (el «189 herramientas / 33 agentes» caducado del
19-sep fue lo primero que vieron desde fuera).
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))
import fichas  # noqa: E402

HOOK = os.path.join(RAIZ, ".claude", "hooks", "ficha_guard.py")


def _completa(rel, test):
    f = fichas.plantilla(rel)
    f.update(para="hace algo", cuando_si="cuando toca", cuando_no="cuando no",
             reemplaza="nada", test=test, origen="test")
    return f


class Arbol:
    """Un repo de juguete: tools/ con fichas.py y capacidades.py de verdad, y una pieza `a.py`."""

    def __init__(self):
        self.raiz = tempfile.mkdtemp(prefix="fichas_")
        os.makedirs(os.path.join(self.raiz, ".git"))
        os.makedirs(os.path.join(self.raiz, "tools", "fichas"))
        os.makedirs(os.path.join(self.raiz, "tests"))
        for f in ("fichas.py", "capacidades.py"):
            shutil.copy(os.path.join(RAIZ, "tools", f), os.path.join(self.raiz, "tools", f))
            self.ficha(f, _completa(f, "tests/test_%s" % f))
            self.escribe("tests/test_%s" % f, "")
        self.escribe("tools/a.py", '"""a.py — lee el radar de ensayos."""\n')
        self.escribe("tests/test_a.py", "")
        fa = _completa("a.py", "tests/test_a.py")
        fa["para"] = "lee el radar de ensayos"
        self.ficha("a.py", fa)

    def escribe(self, rel, texto):
        p = os.path.join(self.raiz, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(texto)

    def ficha(self, rel, datos):
        with open(fichas.ruta_ficha(rel, self.raiz), "w", encoding="utf-8") as fh:
            json.dump(datos, fh)

    def limpia(self):
        shutil.rmtree(self.raiz, ignore_errors=True)


class TestRepoDeVerdad(unittest.TestCase):
    def test_todas_las_piezas_tienen_ficha_valida(self):
        fuera = fichas.comprobar(RAIZ)
        self.assertEqual(fuera, [], "\n".join(fuera[:20]))

    def test_hay_catalogo(self):
        self.assertGreater(len(fichas.piezas(RAIZ)), 200)

    def test_las_subcarpetas_tambien_cuentan(self):
        self.assertIn("hooks/muro_commit_guard.py", fichas.piezas(RAIZ))
        self.assertFalse(any(p.startswith(("state/", "fichas/", "retired/"))
                             for p in fichas.piezas(RAIZ)))


class TestFantasma(unittest.TestCase):
    def setUp(self):
        self.a = Arbol()

    def tearDown(self):
        self.a.limpia()

    def test_arbol_limpio_no_da_problemas(self):
        self.assertEqual(fichas.comprobar(self.a.raiz), [])

    def test_crear_herramienta_sin_ficha_falla(self):
        self.a.escribe("tools/fantasma.py", "print(1)\n")
        fuera = fichas.comprobar(self.a.raiz)
        self.assertTrue(any("fantasma.py: sin ficha" in f for f in fuera), fuera)

    def test_tambien_por_sh_y_en_subcarpeta(self):
        self.a.escribe("tools/pilotos/b.sh", "echo\n")
        self.assertTrue(any("pilotos/b.sh" in f for f in fichas.comprobar(self.a.raiz)))

    def test_una_nueva_no_puede_colarse_como_semilla(self):
        self.a.escribe("tools/nueva.py", "")
        self.a.ficha("nueva.py", {"pieza": "nueva.py", "estado_ficha": "semilla", "para": "x"})
        self.assertTrue(any("no es heredada" in f for f in fichas.comprobar(self.a.raiz)))

    def test_ficha_completa_sin_campo_falla(self):
        f = _completa("a.py", "tests/test_a.py")
        f["cuando_no"] = ""
        self.a.ficha("a.py", f)
        self.assertTrue(any("cuando_no" in x for x in fichas.comprobar(self.a.raiz)))

    def test_el_test_declarado_tiene_que_existir(self):
        self.a.ficha("a.py", _completa("a.py", "tests/no_existe.py"))
        self.assertTrue(any("no existe" in x for x in fichas.comprobar(self.a.raiz)))

    def test_ficha_sin_pieza_se_dice_salvo_retirada(self):
        self.a.ficha("ida.py", _completa("ida.py", "tests/test_a.py"))
        self.assertTrue(any("ficha sin pieza" in x for x in fichas.comprobar(self.a.raiz)))
        r = _completa("ida.py", "tests/test_a.py")
        r["estado_ficha"] = "retirada"
        self.a.ficha("ida.py", r)
        self.assertEqual(fichas.comprobar(self.a.raiz), [])

    def test_la_lista_de_heredadas_solo_encoge(self):
        self.a.escribe("tools/fichas/_heredadas.txt", "a.py\n")   # a.py ya es completa
        self.assertTrue(any("sobra a.py" in x for x in fichas.comprobar(self.a.raiz)))

    def test_semilla_heredada_vale(self):
        self.a.escribe("tools/vieja.py", '"""vieja.py — hace lo viejo."""\n')
        self.a.escribe("tools/fichas/_heredadas.txt", "vieja.py\n")
        self.assertEqual(fichas.sembrar(self.a.raiz), ["vieja.py"])
        self.assertEqual(fichas.leer("vieja.py", self.a.raiz)["para"], "hace lo viejo.")
        self.assertEqual(fichas.comprobar(self.a.raiz), [])


class TestHook(unittest.TestCase):
    def setUp(self):
        self.a = Arbol()

    def tearDown(self):
        self.a.limpia()

    def _hook(self, ruta, tool="Write"):
        entrada = json.dumps({"tool_name": tool, "cwd": self.a.raiz,
                              "tool_input": {"file_path": ruta, "content": "x"}})
        return subprocess.run([sys.executable, HOOK], input=entrada, capture_output=True,
                              text=True, timeout=30)

    def test_crear_sin_ficha_se_deniega_y_enseña_parecidas(self):
        r = self._hook(os.path.join(self.a.raiz, "tools", "radar_ensayos.py"))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn(fichas.AVISO, r.stderr)
        self.assertIn("tools/a.py", r.stderr)          # «lee el radar de ensayos»

    def test_ruta_relativa_tambien(self):
        self.assertEqual(self._hook("tools/otra.sh").returncode, 2)

    def test_editar_una_existente_pasa(self):
        self.assertEqual(self._hook(os.path.join(self.a.raiz, "tools", "a.py")).returncode, 0)

    def test_con_ficha_completa_pasa_aunque_el_test_no_exista_aun(self):
        self.a.ficha("nueva.py", _completa("nueva.py", "tests/test_nueva.py"))
        self.assertEqual(self._hook(os.path.join(self.a.raiz, "tools", "nueva.py")).returncode, 0)

    def test_fuera_de_tools_o_en_state_pasa(self):
        for rel in ("docs/x.py", "tools/state/x.py", "tools/fichas/x.py.json", "tools/nota.md"):
            self.assertEqual(self._hook(os.path.join(self.a.raiz, rel)).returncode, 0, rel)

    def test_otras_herramientas_no_se_miran(self):
        r = self._hook(os.path.join(self.a.raiz, "tools", "z.py"), tool="Edit")
        self.assertEqual(r.returncode, 0)

    def test_esta_registrado_en_settings(self):
        with open(os.path.join(RAIZ, ".claude", "settings.json"), encoding="utf-8") as fh:
            texto = fh.read()
        self.assertIn("ficha_guard.py", texto)


class TestCifras(unittest.TestCase):
    def test_el_readme_cuenta_bien_los_agentes(self):
        with open(os.path.join(RAIZ, "README.md"), encoding="utf-8") as fh:
            m = re.search(r"Los (\d+) agentes", fh.read())
        if not m:
            self.skipTest("el README ya no da la cifra")
        n = len(glob.glob(os.path.join(RAIZ, ".claude", "agents", "*.md")))
        self.assertEqual(int(m.group(1)), n)


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=1).result
    ok = r.testsRun - len(r.failures) - len(r.errors)
    print("RESULTADO fichas: %d OK, %d fallos" % (ok, len(r.failures) + len(r.errors)))
    sys.exit(0 if r.wasSuccessful() else 1)
