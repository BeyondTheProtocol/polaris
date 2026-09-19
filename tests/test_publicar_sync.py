#!/usr/bin/env python3
"""test_publicar_sync.py — el espejo público no empuja si algo huele mal.

19-sep-2026: el repo público estuvo congelado desde que se generó a mano, y el empujón manual
fue justo lo que dejó salir un término vetado. El sincronizador solo vale si sus frenos son
de verdad: HALT, negativa de `publicar.py`, árbol que no compila, y «sin cambios no se toca».
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import publicar_sync as ps  # noqa: E402


class TestFrenos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._espejo, ps.ESPEJO = ps.ESPEJO, os.path.join(self.tmp, "publico")
        self._halt, self._generar, self._compila = ps._halt, ps.generar, ps.compila
        ps._halt = lambda: []
        self.empujado = []
        self._git = ps._git
        ps._git = lambda *a, **k: self._falso_git(*a, **k)

    def tearDown(self):
        ps.ESPEJO, ps._halt, ps.generar, ps.compila, ps._git = (
            self._espejo, self._halt, self._generar, self._compila, self._git)

    def _falso_git(self, *args, **kw):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        r = R()
        if args[:1] == ("status",):
            r.stdout = self.sucio
        if args[:1] == ("push",):
            self.empujado.append(args)
        return r

    sucio = " M tools/onco.py\n"

    def test_con_halt_no_se_publica(self):
        ps._halt = lambda: ["/Users/x/.btp.HALT"]
        rc, msg = ps.sincronizar()
        self.assertEqual(rc, 2)
        self.assertIn("HALT", msg)
        self.assertEqual(self.empujado, [])

    def test_si_publicar_se_niega_no_se_publica(self):
        ps.generar = lambda: (3, "falta el overlay")
        rc, msg = ps.sincronizar()
        self.assertEqual(rc, 3)
        self.assertIn("overlay", msg)
        self.assertEqual(self.empujado, [])

    def test_si_el_arbol_no_compila_no_se_publica(self):
        ps.generar = lambda: (0, "ok")
        ps.compila = lambda destino=None: (False, "SyntaxError: linea 3")
        rc, msg = ps.sincronizar()
        self.assertEqual(rc, 4)
        self.assertIn("no compila", msg)
        self.assertEqual(self.empujado, [])

    def test_sin_cambios_no_hace_commit_ni_push(self):
        ps.generar, ps.compila = lambda: (0, "ok"), lambda destino=None: (True, "")
        self.sucio = ""
        rc, msg = ps.sincronizar()
        self.assertEqual(rc, 0)
        self.assertIn("al día", msg)
        self.assertEqual(self.empujado, [])

    def test_dry_verifica_pero_no_empuja(self):
        ps.generar, ps.compila = lambda: (0, "ok"), lambda destino=None: (True, "")
        rc, msg = ps.sincronizar(dry=True)
        self.assertEqual(rc, 0)
        self.assertIn("--dry", msg)
        self.assertEqual(self.empujado, [])

    def test_con_cambios_limpios_si_empuja(self):
        ps.generar, ps.compila = lambda: (0, "ok"), lambda destino=None: (True, "")
        rc, _ = ps.sincronizar()
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.empujado), 1)


class TestCompila(unittest.TestCase):
    def test_detecta_sintaxis_rota_y_arbol_vacio(self):
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "tools"))
        with open(os.path.join(tmp, "tools", "roto.py"), "w") as fh:
            fh.write("def (:\n")
        self.assertFalse(ps.compila(tmp)[0])
        self.assertFalse(ps.compila(tempfile.mkdtemp())[0])   # sin tools/ ni tests/


if __name__ == "__main__":
    unittest.main()
