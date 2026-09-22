#!/usr/bin/env python3
"""test_stdin_canalizado.py — un cliente de LLM lanzado por un agente no se queda colgado en stdin.

POR QUÉ (22-sep-2026). `nvidia.py` y `chatgpt.py` hacían `sys.stdin.read()` siempre que stdin no
fuera una terminal. Desde el Bash de un agente, stdin no es terminal pero tampoco se cierra, y la
herramienta esperaba para siempre. Pareció un modelo colgado durante horas.
"""
import os
import select
import subprocess
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
CODIGO = "import sys; sys.path.insert(0, %r); import _net; print(repr(_net.stdin_canalizado(1.0)))" % TOOLS


class StdinCanalizado(unittest.TestCase):
    def test_stdin_abierto_que_no_se_cierra_no_bloquea(self):
        # stdin=PIPE y NUNCA se escribe ni se cierra: es justo lo que ve un CLI lanzado por un agente.
        p = subprocess.Popen([sys.executable, "-c", CODIGO], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
        t = time.monotonic()
        try:
            listo, _, _ = select.select([p.stdout], [], [], 8)   # si se cuelga, rojo; no bloquea la batería
            out = p.stdout.readline() if listo else "<colgado esperando stdin>"
        finally:
            p.kill()
            p.wait()
            p.stdin.close()
            p.stdout.close()
        self.assertLess(time.monotonic() - t, 5, "se quedó esperando a stdin")
        self.assertEqual(out.strip(), "''")

    def test_texto_canalizado_si_llega(self):
        r = subprocess.run([sys.executable, "-c", CODIGO], input="hola desde el pipe",
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.stdout.strip(), repr("hola desde el pipe"))

    def test_los_clientes_usan_la_lectura_segura(self):
        for f in ("nvidia.py", "chatgpt.py"):
            src = open(os.path.join(TOOLS, f), encoding="utf-8").read()
            self.assertIn("stdin_canalizado(", src, f)
            self.assertNotIn("sys.stdin.read()", src, "%s vuelve a leer stdin a ciegas" % f)


if __name__ == "__main__":
    unittest.main()
