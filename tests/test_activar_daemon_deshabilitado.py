#!/usr/bin/env python3
"""test_activar_daemon_deshabilitado.py — un daemon DESHABILITADO no se arregla reintentando.

19-sep-2026, fallo real: `com.btp.dispatcher` (el que vacía la cola del lazo) y
`com.btp.bot-telegram` llevaban días caídos. `launchctl list` no los mostraba, y al intentar
arrancarlos el error era «Bootstrap failed: 5: Input/output error», que no dice nada. La causa
era una marca PERSISTENTE de `launchctl disable`, invisible salvo con `print-disabled`.

El coste: la cola sin vaciarse, y las alertas de salud repitiéndose sin fin —una de ellas 679
veces— porque los encargos que debían cerrarlas nunca llegaban a ejecutarse.

Aquí se protege que `activar_daemon.py` mire esa marca y la levante antes de arrancar.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import activar_daemon  # noqa: E402

SALIDA_REAL = '''disabled services = {
\t\t"com.btp.bot-telegram" => disabled
\t\t"com.btp.dispatcher" => disabled
\t\t"com.btp.vigia" => enabled
}'''


class _R:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, "", returncode


class TestDetectaLaMarca(unittest.TestCase):
    def setUp(self):
        self.llamadas = []
        self._run = activar_daemon.subprocess.run

        def falso(cmd, *a, **k):
            self.llamadas.append(cmd)
            if "print-disabled" in cmd:
                return _R(SALIDA_REAL)
            return _R()

        activar_daemon.subprocess.run = falso

    def tearDown(self):
        activar_daemon.subprocess.run = self._run

    def test_ve_el_deshabilitado(self):
        self.assertTrue(activar_daemon._deshabilitado("com.btp.dispatcher"))
        self.assertTrue(activar_daemon._deshabilitado("com.btp.bot-telegram"))

    def test_no_confunde_al_habilitado_ni_al_ausente(self):
        self.assertFalse(activar_daemon._deshabilitado("com.btp.vigia"))
        self.assertFalse(activar_daemon._deshabilitado("com.btp.no-existe"))

    def test_launchctl_mudo_no_es_un_deshabilitado(self):
        """Si `print-disabled` no responde, no se inventa una marca que no se vio."""
        activar_daemon.subprocess.run = lambda cmd, *a, **k: _R("", 1)
        self.assertFalse(activar_daemon._deshabilitado("com.btp.dispatcher"))


class TestElArranqueLoLevanta(unittest.TestCase):
    def test_activar_llama_a_enable_antes_del_bootstrap(self):
        fuente = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "tools", "activar_daemon.py"), encoding="utf-8").read()
        i_check = fuente.index("if _deshabilitado(label):")
        i_enable = fuente.index("'enable'", i_check)
        i_boot = fuente.index("'bootstrap'", i_check)
        self.assertLess(i_enable, i_boot, "el enable tiene que ir ANTES del bootstrap")


if __name__ == "__main__":
    unittest.main()
