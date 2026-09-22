#!/usr/bin/env python3
"""test_githooks_marcas.py — el pre-commit versionado (tools/githooks) no deja commitear marcas de
conflicto, en ninguna rama y sin interruptor.

POR QUÉ (22-sep-2026). El cierre automático 05f897d commiteó `tools/run_agent.sh` con `<<<<<<<` y
`>>>>>>>` dentro: `bash -n` daba rc=2 y los agentes del lazo no podían arrancar hasta que otra
sesión lo limpió (4240df8). `cerrar_sesion.py` ya se para ante eso (fb0e144); esto cubre el resto
de caminos que hacen commit. Funcional: repo de usar y tirar con el hook de verdad enganchado.
No exige casa base (a diferencia de test_githooks_base.py): corre en worktrees y en el CI.
"""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(ROOT, "tools", "githooks")


def _git(args, cwd, env=None):
    e = dict(os.environ)
    for k in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        e[k] = "t"
    for k in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        e[k] = "t@t"
    e.pop("BTP_GIT_MARCAS_OK", None)
    if env:
        e.update(env)
    return subprocess.run(["git"] + args, cwd=cwd, env=e, capture_output=True, text=True)


class NoSeCommiteanMarcasDeConflicto(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="githooks_marcas_")
        _git(["init", "-q", "-b", "master"], self.tmp)
        _git(["config", "core.hooksPath", HOOKS], self.tmp)
        with open(os.path.join(self.tmp, "run.sh"), "w") as f:
            f.write("echo uno\n")
        _git(["add", "-A"], self.tmp)
        assert _git(["commit", "-q", "-m", "base"], self.tmp).returncode == 0
        _git(["checkout", "-q", "-b", "rama"], self.tmp)

    def _commit(self, contenido, env=None):
        with open(os.path.join(self.tmp, "run.sh"), "w") as f:
            f.write(contenido)
        _git(["add", "-A"], self.tmp)
        return _git(["commit", "-q", "-m", "x"], self.tmp, env=env)

    def test_marcas_bloqueadas_en_una_rama(self):
        r = self._commit("f() {\n<<<<<<< HEAD\n  a\n=======\n  b\n>>>>>>> master\n}\n")
        self.assertNotEqual(r.returncode, 0, "commiteó marcas de conflicto")
        self.assertIn("MARCAS DE CONFLICTO", r.stderr)
        self.assertIn("run.sh", r.stderr)

    def test_merge_con_conflicto_no_se_cierra_con_add_y_commit(self):
        """El camino exacto de 05f897d: merge que choca + `add -A` + commit."""
        self._commit("echo rama\n")
        _git(["checkout", "-q", "master"], self.tmp)
        with open(os.path.join(self.tmp, "run.sh"), "w") as f:
            f.write("echo master\n")
        _git(["add", "-A"], self.tmp)
        _git(["commit", "-q", "-m", "m"], self.tmp)
        _git(["checkout", "-q", "rama"], self.tmp)
        _git(["merge", "master"], self.tmp)                      # choca
        _git(["add", "-A"], self.tmp)
        r = _git(["commit", "-q", "--no-edit"], self.tmp)
        self.assertNotEqual(r.returncode, 0, "cerró un merge con los marcadores dentro")

    def test_texto_que_solo_las_cita_pasa(self):
        r = self._commit("# si ves `<<<<<<< HEAD` en un fichero, resuélvelo\necho uno\n")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_salida_a_proposito(self):
        r = self._commit("<<<<<<< fixture\n", env={"BTP_GIT_MARCAS_OK": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
