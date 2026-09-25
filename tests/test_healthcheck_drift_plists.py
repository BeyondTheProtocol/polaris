#!/usr/bin/env python3
"""test_healthcheck_drift_plists.py — el plist instalado no puede decir algo distinto del repo.

20-sep-2026. El plist ACTIVO de `com.btp.correo-imap` exportaba `BTP_GMAIL_USER=<la secundaria>` y
corría `once`, mientras el del repo decía otra cosa. Resultado: el correo de la cuenta PRINCIPAL
no llegaba a `tools/state/correo/buzon.json` —el fichero que leen diez consumidores, tres de los
cuales no miran ningún fichero por cuenta— desde el 25-jun. **Tres meses sin un solo aviso.**

Por qué no lo cazó nada: el drift que había compara NOMBRES de daemon entre cuatro capas, y
`_drift_rutas_rotas` abre el plist instalado pero solo pregunta «¿existe esta ruta?» —
`/usr/bin/python3` existe, `once` no es una ruta, y `EnvironmentVariables` no lo leía nadie.

Lo que se protege aquí: que se comparen los dos campos que deciden QUÉ corre y CON QUÉ entorno,
que `PATH` no cuente (es ruido de máquina) y que un plist ilegible no rompa el chequeo.
"""
import os
import plistlib
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))
os.environ.setdefault("BTP_TEST_BATTERY", "1")
import healthcheck as hc  # noqa: E402

PATH_MAQUINA = "/Users/polaris/.local/bin:/opt/homebrew/bin:/usr/bin:/bin"


def _escribe(carpeta, label, args, env=None):
    os.makedirs(carpeta, exist_ok=True)
    d = {"Label": label, "ProgramArguments": args,
         "EnvironmentVariables": dict(env or {}, PATH=PATH_MAQUINA)}
    with open(os.path.join(carpeta, label + ".plist"), "wb") as f:
        plistlib.dump(d, f)


class TestDriftPorContenido(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="drift-")
        self.repo = os.path.join(self.tmp, "repo")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.repo)
        os.makedirs(self.home)

    def _correr(self):
        return hc._drift_contenido_plists(self.home, self.repo)

    def test_iguales_callan(self):
        for carpeta in (self.repo, self.home):
            _escribe(carpeta, "com.btp.demo", ["/usr/bin/python3", "x.py", "todas"],
                     {"BTP_STATE_DIR": "/s"})
        alertas, difs = self._correr()
        self.assertEqual(([], {}), (alertas, difs))

    def test_argumento_distinto_avisa(self):
        _escribe(self.repo, "com.btp.demo", ["/usr/bin/python3", "x.py", "todas"])
        _escribe(self.home, "com.btp.demo", ["/usr/bin/python3", "x.py", "once"])
        alertas, difs = self._correr()
        self.assertEqual(["ProgramArguments"], difs["com.btp.demo"])
        self.assertEqual("plist_drift", alertas[0][0], "la clave es ESTABLE, sin el label dentro")
        self.assertIn("com.btp.demo", alertas[0][1])

    def test_variable_de_entorno_distinta_avisa(self):
        """El caso REAL: `once` vs `todas` se veía a ojo; la cuenta iba en el entorno."""
        _escribe(self.repo, "com.btp.demo", ["/usr/bin/python3", "x.py"], {})
        _escribe(self.home, "com.btp.demo", ["/usr/bin/python3", "x.py"],
                 {"BTP_GMAIL_USER": "la-secundaria@gmail.com"})
        alertas, difs = self._correr()
        self.assertEqual(["EnvironmentVariables"], difs["com.btp.demo"])
        self.assertTrue(alertas)

    def test_el_PATH_no_cuenta(self):
        _escribe(self.repo, "com.btp.demo", ["/usr/bin/python3", "x.py"])
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "com.btp.demo.plist"), "wb") as f:
            plistlib.dump({"Label": "com.btp.demo",
                           "ProgramArguments": ["/usr/bin/python3", "x.py"],
                           "EnvironmentVariables": {"PATH": "/otra/cosa:/usr/bin"}}, f)
        self.assertEqual(([], {}), self._correr())

    def test_un_plist_ilegible_no_rompe_el_chequeo(self):
        _escribe(self.repo, "com.btp.demo", ["/usr/bin/python3", "x.py"])
        _escribe(self.repo, "com.btp.roto", ["/usr/bin/python3", "y.py"])
        _escribe(self.home, "com.btp.demo", ["/usr/bin/python3", "x.py"])
        with open(os.path.join(self.home, "com.btp.roto.plist"), "w") as f:
            f.write("<esto no es un plist")
        alertas, difs = self._correr()          # el ilegible ya tiene su alerta en otro chequeo
        self.assertEqual(([], {}), (alertas, difs))

    def test_lo_que_no_esta_instalado_no_es_drift(self):
        _escribe(self.repo, "com.btp.demo", ["/usr/bin/python3", "x.py"])
        self.assertEqual(([], {}), self._correr())

    def test_va_en_la_vuelta_del_healthcheck(self):
        fuente = open(os.path.join(RAIZ, "tools", "healthcheck.py"), encoding="utf-8").read()
        self.assertIn("_drift_contenido_plists(os.path.expanduser", fuente,
                      "el chequeo existe pero run() no lo llama: no protege de nada")
        self.assertIn('"plist_drift"', fuente)


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=0).result
    if res.wasSuccessful():
        print("✅ DRIFT DE PLISTS POR CONTENIDO EN VERDE (%d casos)" % res.testsRun)
    sys.exit(0 if res.wasSuccessful() else 1)
