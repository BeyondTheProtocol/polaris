#!/usr/bin/env python3
"""test_bash_return_explicito.py — ningún script usa `return` a secas justo después de una comprobación.

POR QUÉ (22-sep-2026). `run_agent.sh` tenía `[ "$X" = 1 ]; return` dentro de una función que llama
el trap EXIT. En bash 3.2 (el del Mac) devuelve el resultado de la comprobación; en bash 5.x
(Linux, el CI) un `return` a secas dentro de un trap devuelve el estado de ANTES del trap. El
resultado: en Linux el lazo creía siempre que había otro agente vivo y nunca devolvía casa base a
master. Pasaba todos los tests en el Mac. Se fija la CLASE, no el caso.
"""
import glob
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# `[ ... ]; return` / `]] && return` / `] || return` sin número detrás.
PATRON = re.compile(r"\]\]?\s*(;|&&|\|\|)\s*return\s*(;|$|\})")


class ReturnExplicito(unittest.TestCase):
    def test_ningun_script_depende_del_return_a_secas(self):
        malos = []
        for f in sorted(glob.glob(os.path.join(ROOT, "tools", "*.sh")) +
                        glob.glob(os.path.join(ROOT, ".claude", "hooks", "*.sh")) +
                        glob.glob(os.path.join(ROOT, "tests", "*.sh"))):
            for n, ln in enumerate(open(f, encoding="utf-8", errors="replace"), 1):
                if ln.lstrip().startswith("#"):
                    continue
                if PATRON.search(ln):
                    malos.append("%s:%d: %s" % (os.path.relpath(f, ROOT), n, ln.strip()))
        self.assertEqual(malos, [], "return a secas tras una comprobación (en bash 5 dentro de un "
                                    "trap devuelve otra cosa):\n" + "\n".join(malos))

    def test_el_patron_caza_el_caso_real(self):
        self.assertTrue(PATRON.search('  if [ -n "$X" ]; then [ "$X" = 1 ]; return; fi'))
        self.assertFalse(PATRON.search('    if [ "$X" = 1 ]; then return 0; else return 1; fi'))


if __name__ == "__main__":
    unittest.main()
