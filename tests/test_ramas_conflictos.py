#!/usr/bin/env python3
"""test_ramas_conflictos.py — avisar de la colisión ANTES de que dos sesiones se pisen.

Qué se protege (25-jul-2026, idea copiada de Fleet Deck `x:2076775237577023513`): `ramas.py
conflictos` cruza los ficheros que está tocando cada worktree vivo y saca los que toca MÁS DE UNO.

Dos detalles que son el fondo del asunto:
  · cuenta lo NO commiteado, porque la colisión real pasa mientras dos sesiones escriben, mucho
    antes de que ninguna commitee;
  · incluye casa base, porque la trampa conocida es justo esa (un worktree nace de HEAD y no se
    lleva lo no commiteado).
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import ramas  # noqa: E402

WTS = [
    {"path": "/repo", "branch": "master", "es_base": True},
    {"path": "/repo/.claude/worktrees/uno", "branch": "claude/uno", "es_base": False},
    {"path": "/repo/.claude/worktrees/dos", "branch": "claude/dos", "es_base": False},
    # los worktrees de sub-agentes no cuentan (son efímeros y no son sesiones de {{TITULAR}})
    {"path": "/repo/.claude/worktrees/agent-xyz", "branch": "claude/agente", "es_base": False},
]
TOCADOS = {
    "master": {"tools/salida.py"},
    "claude/uno": {"tools/salida.py", "tools/cola.py"},
    "claude/dos": {"tools/cola.py", "tools/kb.py"},
    "claude/agente": {"tools/cola.py"},
}


class Conflictos(unittest.TestCase):
    def setUp(self):
        self._orig = (ramas.worktrees, ramas._ficheros_tocados, ramas._rama_base)
        ramas.worktrees = lambda: [dict(w) for w in WTS]
        ramas._rama_base = lambda: "master"
        ramas._ficheros_tocados = lambda w, base: set(TOCADOS.get(w.get("branch"), ()))

    def tearDown(self):
        ramas.worktrees, ramas._ficheros_tocados, ramas._rama_base = self._orig

    def test_saca_los_ficheros_compartidos_y_quien_los_toca(self):
        c = ramas.conflictos()
        self.assertEqual(c["n_choques"], 2)
        por_fichero = {x["fichero"]: x["ramas"] for x in c["choques"]}
        self.assertEqual(por_fichero["tools/cola.py"], ["claude/dos", "claude/uno"])
        self.assertEqual(por_fichero["tools/salida.py"], ["casa base", "claude/uno"],
                         "casa base tiene que aparecer: es la trampa conocida")
        self.assertNotIn("tools/kb.py", por_fichero, "un fichero de una sola rama no es colisión")

    def test_ignora_los_worktrees_de_subagente(self):
        c = ramas.conflictos()
        todas = {r for x in c["choques"] for r in x["ramas"]}
        self.assertNotIn("claude/agente", todas)

    def test_el_comando_sale_con_1_cuando_hay_colision(self):
        with redirect_stdout(io.StringIO()) as buf:
            rc = ramas.main(["conflictos"])
        self.assertEqual(rc, 1, "exit 1 para poder usarlo como check")
        self.assertIn("tools/cola.py", buf.getvalue())

    def test_sin_colisiones_sale_con_0_y_lo_dice(self):
        ramas._ficheros_tocados = lambda w, base: {"solo/" + (w.get("branch") or "x")}
        with redirect_stdout(io.StringIO()) as buf:
            rc = ramas.main(["conflictos"])
        self.assertEqual(rc, 0)
        self.assertIn("Sin colisiones", buf.getvalue())

    def test_resumen_lo_expone_para_el_observatorio(self):
        ramas.sesiones = lambda: []
        ramas.huerfanas = lambda: []
        r = ramas.resumen()
        self.assertEqual(r["n_conflictos"], 2)
        self.assertTrue(any(x["fichero"] == "tools/cola.py" for x in r["conflictos"]))


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=0).run(
        unittest.TestLoader().loadTestsFromTestCase(Conflictos))
    if res.wasSuccessful():
        print("✅ RAMAS CONFLICTOS EN VERDE (%d ok / 0 fallos)" % res.testsRun)
    sys.exit(0 if res.wasSuccessful() else 1)
