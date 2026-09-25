#!/usr/bin/env python3
"""test_espejo_reiniciar_historial.py — reiniciar el historial del espejo es irreversible fuera,
así que por defecto es un ensayo, se planta con HALT o con restos, y sin la palabra no empuja."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import espejo_reiniciar_historial as e  # noqa: E402


def _git(d, *a):
    return subprocess.run(["git", "-C", d] + list(a), capture_output=True, text=True, check=True).stdout


class TestReiniciar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.espejo = os.path.join(self.tmp, "publico")
        self.remoto = os.path.join(self.tmp, "remoto.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "master", self.remoto], check=True)
        os.makedirs(self.espejo)
        _git(self.espejo, "init", "-q", "-b", "master")
        _git(self.espejo, "remote", "add", "origin", self.remoto)
        for i in range(3):
            with open(os.path.join(self.espejo, "a.txt"), "w") as fh:
                fh.write("v%d\n" % i)
            _git(self.espejo, "add", "-A")
            _git(self.espejo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "c%d" % i)
        _git(self.espejo, "push", "-q", "origin", "master")
        self.orig = (e.ESPEJO, e.REPO, e.publicar_sync._halt, e.publicar_sync.generar, e.restos)
        e.ESPEJO, e.REPO = self.espejo, self.tmp
        os.makedirs(os.path.join(self.tmp, "_cajita"))
        e.publicar_sync._halt = lambda: []
        e.publicar_sync.generar = lambda: (0, "")
        e.restos = lambda d: []
        self.env = {k: os.environ.get(k) for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                                                    "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")}
        os.environ.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                          GIT_COMMITTER_EMAIL="t@t")

    def tearDown(self):
        e.ESPEJO, e.REPO, e.publicar_sync._halt, e.publicar_sync.generar, e.restos = self.orig
        for k, v in self.env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _remoto_commits(self):
        return int(_git(self.remoto, "rev-list", "--count", "master").strip())

    def test_por_defecto_es_ensayo(self):
        rc, _ = e.reiniciar()
        self.assertEqual(rc, 0)
        self.assertEqual(self._remoto_commits(), 3)
        self.assertEqual(int(_git(self.espejo, "rev-list", "--count", "HEAD").strip()), 3)

    def test_halt_y_restos_plantan(self):
        e.publicar_sync._halt = lambda: ["/x/.HALT"]
        self.assertEqual(e.reiniciar(ejecutar=True, confirmar=lambda _: e.PALABRA)[0], 2)
        e.publicar_sync._halt = lambda: []
        e.restos = lambda d: ["tools/x.py"]
        self.assertEqual(e.reiniciar(ejecutar=True, confirmar=lambda _: e.PALABRA)[0], 4)
        self.assertEqual(self._remoto_commits(), 3)

    def test_sin_la_palabra_no_empuja(self):
        rc, _ = e.reiniciar(ejecutar=True, confirmar=lambda _: "si")
        self.assertEqual(rc, 1)
        self.assertEqual(self._remoto_commits(), 3)

    def test_con_la_palabra_queda_un_commit_y_copia_local(self):
        rc, _ = e.reiniciar(ejecutar=True, confirmar=lambda _: e.PALABRA)
        self.assertEqual(rc, 0)
        self.assertEqual(self._remoto_commits(), 1)
        copias = [f for f in os.listdir(os.path.join(self.tmp, "_cajita")) if f.endswith(".bundle")]
        self.assertEqual(len(copias), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
