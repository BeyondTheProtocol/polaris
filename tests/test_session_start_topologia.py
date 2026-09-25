#!/usr/bin/env python3
"""test_session_start_topologia.py — el aviso de arranque no confunde el mini con el Air.

POR QUÉ EXISTE (24-sep-2026, deuda `sessionstart-miente-topologia`, 2x). `.claude/hooks/session_start.sh`
decía «este es el PORTÁTIL (Air)» con solo ver un `.HALT`. El CÓDIGO ROJO también pone `.HALT`, así
que en el mini con código rojo activo la sesión arrancaba creyéndose el Air: ese día llevó a fusionar
con la premisa equivocada, a lanzar `deploy_ff.sh to-polaris` contra la propia máquina y a que {{TITULAR}}
tecleara una contraseña para nada.

Ahora la máquina se identifica por hostname (`Polaris` = mini, la convención de `tools/ff_al_abrir.sh`
y `tools/mini.sh`). El test ejecuta el hook de verdad con un repo de usar y tirar y un HOME aislado, y
fija el hostname con `BTP_HOSTNAME`.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, ".claude", "hooks", "session_start.sh")


def correr(hostname, halt):
    """Ejecuta el hook y devuelve el additionalContext ('' si no dice nada)."""
    tmp = tempfile.mkdtemp(prefix="btp-sstop-")
    try:
        repo = os.path.join(tmp, "repo")
        home = os.path.join(tmp, "home")
        os.makedirs(repo)
        os.makedirs(home)
        if halt:
            open(os.path.join(repo, ".HALT"), "w").close()
        env = dict(os.environ, BTP_REPO=repo, HOME=home, BTP_HOSTNAME=hostname, TMPDIR=tmp)
        r = subprocess.run(["bash", HOOK], env=env, capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        out = r.stdout.strip()
        if not out:
            return ""
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(shutil.which("jq"), "el hook necesita jq")
class TopologiaSessionStart(unittest.TestCase):
    def test_mini_con_halt_no_dice_air(self):
        ctx = correr("Polaris", halt=True)
        self.assertNotIn("PORTÁTIL", ctx)
        self.assertIn("HALT activo en el MINI", ctx)
        self.assertIn("NO es el Air", ctx)

    def test_air_con_halt_avisa_air(self):
        ctx = correr("MacBook-Air-de-{{TITULAR}}", halt=True)
        self.assertIn("PORTÁTIL (Air)", ctx)
        self.assertNotIn("HALT activo en el MINI", ctx)

    def test_mini_sin_halt_calla(self):
        self.assertEqual(correr("Polaris", halt=False), "")

    def test_air_sin_halt_calla(self):
        self.assertEqual(correr("MacBook-Air-de-{{TITULAR}}", halt=False), "")


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=0).result
    ok = res.testsRun - len(res.failures) - len(res.errors) - len(res.skipped)
    print("test_session_start_topologia: %d OK, %d fallos" % (ok, len(res.failures) + len(res.errors)))
    raise SystemExit(0 if res.wasSuccessful() else 1)
