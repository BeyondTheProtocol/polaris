#!/usr/bin/env python3
"""test_healthcheck_syspath.py — el healthcheck no se queda sin tools/ en el sys.path a mitad de run().

14-sep-2026, visto en vivo bajo launchd: `_check_correo_salud` importaba `correo_smtp`, que borra
tools/ del sys.path del proceso. Todo import perezoso posterior de run() fallaba en silencio:
  · frescura_error  ModuleNotFoundError('seguimiento')   → detector de frescura ciego
  · ciclo_agentes_error ModuleNotFoundError('ciclo_agentes')
Los demás tests no lo veían porque meten tools/ en sys.path por su cuenta.

Lo que protege:
  1. `_importar_sin_tocar_path` deja sys.path igual aunque el módulo lo destroce al importarse.
  2. Con el `correo_smtp` REAL, tools/ sigue en el path y un import perezoso posterior funciona.
  3. Clase entera: en healthcheck.py ningún módulo de los que borran tools/ se importa a pelo.
"""
import os
import re
import shutil
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(RAIZ, "tools")
os.environ.setdefault("BTP_TEST_BATTERY", "1")
sys.path.insert(0, TOOLS)
import healthcheck  # noqa: E402


class TestHealthcheckSysPath(unittest.TestCase):
    def setUp(self):
        self.path_antes = list(sys.path)

    def tearDown(self):
        sys.path[:] = self.path_antes

    def test_un_modulo_que_borra_tools_no_contamina_el_proceso(self):
        tmp = tempfile.mkdtemp(prefix="syspath-")
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, "modulo_destrozon_14sep.py"), "w") as f:
            f.write("import sys, os\n"
                    "T = %r\n"
                    "sys.path = [p for p in sys.path if os.path.abspath(p) != T]\n" % TOOLS)
        sys.path.insert(0, tmp)
        antes = list(sys.path)
        healthcheck._importar_sin_tocar_path("modulo_destrozon_14sep")
        self.assertEqual(antes, sys.path)
        sys.modules.pop("modulo_destrozon_14sep", None)

    def test_con_correo_smtp_real_los_imports_perezosos_siguen_funcionando(self):
        # Desde el 20-sep-26 `correo_smtp` ya no purga el path, así que esto es cierto por partida
        # doble: por el helper y por el módulo. Se queda como red por si alguien reintroduce el
        # patrón; quien vigila la causa es tests/test_tools_no_sombrea_stdlib.py.
        sys.modules.pop("correo_smtp", None)
        sys.modules.pop("ciclo_agentes", None)
        healthcheck._importar_sin_tocar_path("correo_smtp")
        self.assertIn(TOOLS, [os.path.abspath(p) for p in sys.path],
                      "correo_smtp dejó el proceso sin tools/ en el path")
        import ciclo_agentes  # noqa: F401  (lo que falló en vivo)

    def test_ningun_modulo_que_borra_tools_se_importa_a_pelo(self):
        fuente = open(os.path.join(TOOLS, "healthcheck.py"), encoding="utf-8").read()
        nombres = "|".join(healthcheck._BORRAN_TOOLS_DEL_PATH)
        a_pelo = re.findall(r"^\s*(?:import|from)\s+(%s)\b" % nombres, fuente, re.M)
        self.assertEqual([], a_pelo, "importar estos módulos a pelo vuelve a dejar ciego al healthcheck")

    def test_la_lista_cubre_a_todos_los_que_borran_tools(self):
        """Si mañana otro módulo copia el patrón, entra en la lista o este test se pone rojo."""
        patron = re.compile(r"^sys\.path(\[:\])?\s*=\s*\[p for p in sys\.path if", re.M)
        excluidos = {"consensus_mcp", "scite_mcp", "_oauth_refresh"}  # servidores MCP en proceso propio
        encontrados = set()
        for n in os.listdir(TOOLS):
            if n.endswith(".py"):
                with open(os.path.join(TOOLS, n), encoding="utf-8", errors="replace") as f:
                    if patron.search(f.read()):
                        encontrados.add(n[:-3])
        self.assertEqual(sorted(encontrados - excluidos), sorted(healthcheck._BORRAN_TOOLS_DEL_PATH))


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=0).result
    if res.wasSuccessful():
        print("✅ HEALTHCHECK SYS.PATH EN VERDE (%d casos)" % res.testsRun)
    sys.exit(0 if res.wasSuccessful() else 1)
