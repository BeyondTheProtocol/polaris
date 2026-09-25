#!/usr/bin/env python3
"""test_espejo_ensayo.py — el árbol público se reconoce por su marca, no adivinando.

POR QUÉ (24-sep-2026). El espejo público (`tools/publicar.py`) sustituye los nombres y reescribe
el léxico vetado: «ingeniera» llega como «ingeniera». Los tests que comprueban que eso se caza
no tienen allí qué cazar, y lo adivinaban buscando `{{` en el texto, que solo deja la
sustitución de nombres. Un caso sin nombre propio no se enteraba y el CI público se puso rojo
el 21-22 y el 24-sep, con el código bien. Aquí se fija:
  · que `publicar.py` deja la marca `.espejo-publico` en el árbol que genera, y solo ahí;
  · que `_entorno.es_espejo()` la busca junto al CÓDIGO y no en `BTP_REPO`: hay tests que
    redirigen `BTP_REPO` a un tmp para aislarse y desde ahí nunca la veían;
  · que en casa base y en los worktrees es False, así que ahí se prueba de verdad.

(El nombre viene del plan original, que además ensayaba la batería antes de publicar. Esa
parte vive en la rama `claude/espejo-ensayo-linux`: en el Mac no imita al runner de Linux.)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(AQUI)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, AQUI)
import publicar  # noqa: E402


class LaMarcaDelEspejo(unittest.TestCase):
    def test_publicar_deja_la_marca(self):
        """Contra `publicar.publicar()` de verdad, sobre un repo mínimo con su overlay."""
        from test_publicar_fuga import _overlay     # el overlay mínimo de su propia batería
        base, destino = tempfile.mkdtemp(), os.path.join(tempfile.mkdtemp(), "pub")
        _overlay(base)
        with open(os.path.join(base, "README.md"), "w", encoding="utf-8") as f:
            f.write("Un README sin nada privado.\n")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@localhost",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@localhost")
        for orden in (["git", "init", "-q"], ["git", "add", "README.md"],
                      ["git", "commit", "-q", "-m", "x"]):
            subprocess.run(orden, cwd=base, env=env, check=True, capture_output=True)
        viejo = publicar.ROOT
        publicar.ROOT = base
        try:
            if publicar._overlay_ok():
                self.skipTest("el overlay mínimo no basta en esta versión de publicar.py")
            self.assertEqual(publicar.publicar(destino), 0)
        finally:
            publicar.ROOT = viejo
        self.assertTrue(os.path.exists(os.path.join(destino, publicar.MARCA_ESPEJO)))
        self.assertFalse(os.path.exists(os.path.join(base, publicar.MARCA_ESPEJO)),
                         "la marca va en el árbol publicado, nunca en el origen")

    def _arbol(self, con_marca):
        """Un árbol con `tests/_entorno.py`, con o sin la marca en su raíz."""
        raiz = tempfile.mkdtemp()
        os.makedirs(os.path.join(raiz, "tests"))
        shutil.copy(os.path.join(AQUI, "_entorno.py"), os.path.join(raiz, "tests"))
        if con_marca:
            open(os.path.join(raiz, publicar.MARCA_ESPEJO), "w").close()
        return raiz

    def _es_espejo(self, raiz, btp_repo=None):
        env = dict(os.environ)
        env.pop("BTP_REPO", None)
        if btp_repo:
            env["BTP_REPO"] = btp_repo
        r = subprocess.run([sys.executable, "-c",
                            "import sys; sys.path.insert(0, %r); import _entorno; "
                            "print(_entorno.es_espejo())" % os.path.join(raiz, "tests")],
                           env=env, capture_output=True, text=True)
        return r.stdout.strip()

    def test_es_espejo_lee_la_marca_del_codigo(self):
        self.assertEqual(self._es_espejo(self._arbol(True)), "True")
        self.assertEqual(self._es_espejo(self._arbol(False)), "False")

    def test_btp_repo_redirigido_no_la_esconde(self):
        """El caso real: `test_caso_publico` pone `BTP_REPO` en un tmp, y el caso del léxico
        seguía corriendo (rojo) en el espejo aunque la marca estuviera allí."""
        espejo, tmp = self._arbol(True), tempfile.mkdtemp()
        self.assertEqual(self._es_espejo(espejo, btp_repo=tmp), "True")
        self.assertEqual(self._es_espejo(self._arbol(False), btp_repo=espejo), "False",
                         "y al revés: un BTP_REPO que apunte a un espejo no convierte el código")

    def test_aqui_no_es_espejo(self):
        """En casa base o en un worktree NO es espejo: ahí los tests con datos reales corren.
        En el propio espejo este caso no aplica (allí SÍ lo es), y dar por hecho dónde corre un
        test es justo la clase de fallo que este fichero existe para cerrar."""
        if os.path.exists(os.path.join(ROOT, publicar.MARCA_ESPEJO)):
            self.assertEqual(self._es_espejo(ROOT), "True")
        else:
            self.assertEqual(self._es_espejo(ROOT), "False")


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    print("✅ MARCA DEL ESPEJO EN VERDE" if res.wasSuccessful() else "❌ revisar fallos")
    sys.exit(0 if res.wasSuccessful() else 1)
