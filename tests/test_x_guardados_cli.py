#!/usr/bin/env python3
"""test_x_guardados_cli.py — pedir ayuda no puede gastar una lectura de la API de X.

19-sep-2026: `python3 tools/x_guardados.py --help` no mostraba la ayuda, caía al `fetch` por
defecto, llamaba a `xurl bookmarks` y escribía un estado nuevo. Aquí se comprueba que `--help`,
`-h` y cualquier flag desconocido responden sin llamar a `cmd_fetch` ni a `cmd_listar`.
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import x_guardados  # noqa: E402


class TestCliSinApi(unittest.TestCase):
    def setUp(self):
        self.llamadas = []
        self._fetch, self._listar = x_guardados.cmd_fetch, x_guardados.cmd_listar
        x_guardados.cmd_fetch = lambda *a, **k: self.llamadas.append("fetch") or 0
        x_guardados.cmd_listar = lambda *a, **k: self.llamadas.append("listar") or 0

    def tearDown(self):
        x_guardados.cmd_fetch, x_guardados.cmd_listar = self._fetch, self._listar

    def _main(self, argv):
        with redirect_stdout(io.StringIO()) as out:
            rc = x_guardados.main(argv)
        return rc, out.getvalue()

    def test_help_no_llama_a_la_api(self):
        for flag in ("--help", "-h"):
            rc, out = self._main([flag])
            self.assertEqual(rc, 0)
            self.assertIn("x_guardados.py", out)
        self.assertEqual(self.llamadas, [])

    def test_flag_desconocido_no_llama_a_la_api(self):
        rc, out = self._main(["--versión"])
        self.assertEqual(rc, 1)
        self.assertIn("no reconocido", out)
        self.assertEqual(self.llamadas, [])

    def test_uso_del_daemon_sigue_igual(self):
        self.assertEqual(self._main(["fetch"])[0], 0)
        self.assertEqual(self._main(["fetch", "--max", "100", "--notify"])[0], 0)
        self.assertEqual(self._main(["listar", "--since", "7"])[0], 0)
        self.assertEqual(self.llamadas, ["fetch", "fetch", "listar"])


if __name__ == "__main__":
    unittest.main()
