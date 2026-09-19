#!/usr/bin/env python3
"""test_githooks_base.py — el freno que impide commitear y empujar a la base, sujeto por fin.

POR QUÉ EXISTE (31-jul-2026, Fase 2 de las normas). `feedback-comite-git` YA tenía freno real —
`tools/githooks/pre-commit` y `pre-push` frenan commit y push directos a master/main— y de hecho
funcionó hoy mismo: paró un commit mío a casa base y me obligó a usar la salida documentada
(`BTP_GIT_BASE_OK=1` con OK humano explícito).

Pero no tenía NI UN TEST, y ese freno es especialmente fácil de perder sin ruido:

  · vive en `core.hooksPath`, una opción de git que cualquiera puede cambiar sin tocar el repo;
  · depende de un fichero-interruptor, `.claude/hooks/.base_gate_on`, que si desaparece deja los
    hooks presentes pero MUDOS. Un guardián apagado se ve igual que uno que no salta.

Casa base es el sistema vivo 24/7 y {{TITULAR}} trabaja con muchas sesiones en paralelo: si este freno
se cae, se cae en silencio y nadie se entera hasta que alguien pisa el árbol vivo.

El test es FUNCIONAL, no de existencia: monta un repo de usar y tirar, apunta `core.hooksPath` a
los hooks de verdad y comprueba que bloquean, que la salida documentada funciona, y que en una
rama no estorban.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("casa-base")
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(ROOT, "tools", "githooks")


def _git(args, cwd, env=None, entrada=None):
    e = dict(os.environ)
    e.setdefault("GIT_AUTHOR_NAME", "t"); e.setdefault("GIT_AUTHOR_EMAIL", "t@t")
    e.setdefault("GIT_COMMITTER_NAME", "t"); e.setdefault("GIT_COMMITTER_EMAIL", "t@t")
    e.pop("BTP_GIT_BASE_OK", None)
    if env:
        e.update(env)
    return subprocess.run(["git"] + args, cwd=cwd, env=e, input=entrada,
                          capture_output=True, text=True)


class ElFrenoDeLaBaseExiste(unittest.TestCase):
    def test_los_hooks_estan_y_son_ejecutables(self):
        for h in ("pre-commit", "pre-push", "reference-transaction"):
            ruta = os.path.join(HOOKS, h)
            self.assertTrue(os.path.exists(ruta), "falta %s" % ruta)
            self.assertTrue(os.access(ruta, os.X_OK), "%s no es ejecutable: no frena nada" % h)

    def test_el_repo_apunta_a_esos_hooks(self):
        """Si `core.hooksPath` se mueve, los hooks siguen en el repo pero ya no corren.

        En un worktree la config es la MISMA que la de casa base, así que apunta a los hooks de
        casa base y no a los de la copia. Eso es lo correcto: el freno es uno para todos. Por eso
        se comprueba que la ruta acabe en tools/githooks y que exista, no que sea la de aquí.
        """
        r = _git(["config", "core.hooksPath"], ROOT)
        self.assertEqual(r.returncode, 0, "core.hooksPath sin configurar: los hooks no corren")
        ruta = os.path.realpath(os.path.join(ROOT, r.stdout.strip()))
        self.assertTrue(ruta.endswith(os.path.join("tools", "githooks")), ruta)
        for h in ("pre-commit", "pre-push"):
            self.assertTrue(os.access(os.path.join(ruta, h), os.X_OK),
                            "core.hooksPath apunta a %s pero ahí no hay %s ejecutable" % (ruta, h))

    def test_el_interruptor_sigue_encendido(self):
        """Sin `.base_gate_on` los hooks salen por la puerta de atrás y no dicen nada."""
        self.assertTrue(os.path.exists(os.path.join(ROOT, ".claude", "hooks", ".base_gate_on")),
                        "el interruptor del gate no está: los hooks están mudos")


class ElFrenoDeLaBaseFrena(unittest.TestCase):
    """Funcional: un repo de usar y tirar con los hooks de verdad enganchados."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="githooks_test_")
        _git(["init", "-q", "-b", "master"], self.tmp)
        _git(["config", "core.hooksPath", HOOKS], self.tmp)
        os.makedirs(os.path.join(self.tmp, ".claude", "hooks"), exist_ok=True)
        open(os.path.join(self.tmp, ".claude", "hooks", ".base_gate_on"), "w").close()
        # Primer commit CON la salida documentada: antes de él la rama no existe todavía
        # (`rev-parse --abbrev-ref HEAD` devuelve "HEAD" en una rama sin nacer) y el hook no
        # reconocería master. Sin esto el test montaba mal y daba verde sin frenar nada.
        with open(os.path.join(self.tmp, "a.txt"), "w") as f:
            f.write("uno\n")
        _git(["add", "-A"], self.tmp)
        r = _git(["commit", "-m", "base"], self.tmp, env={"BTP_GIT_BASE_OK": "1"})
        assert r.returncode == 0, r.stderr
        with open(os.path.join(self.tmp, "a.txt"), "a") as f:
            f.write("dos\n")
        _git(["add", "-A"], self.tmp)

    def test_commit_a_master_bloqueado(self):
        r = _git(["commit", "-m", "directo a la base"], self.tmp)
        self.assertNotEqual(r.returncode, 0, "el commit a master pasó: el freno no frena")
        self.assertIn("BLOQUEADO", r.stderr)

    def test_la_salida_documentada_funciona(self):
        """El OK humano explícito tiene que seguir existiendo, o el gate es una pared."""
        r = _git(["commit", "-m", "con OK"], self.tmp, env={"BTP_GIT_BASE_OK": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_en_una_rama_no_estorba(self):
        _git(["checkout", "-q", "-b", "worktree-lo-que-sea"], self.tmp)
        r = _git(["commit", "-m", "en rama"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_sin_interruptor_el_gate_calla(self):
        """Documenta el modo de fallo: el interruptor apagado es indistinguible de todo bien."""
        os.remove(os.path.join(self.tmp, ".claude", "hooks", ".base_gate_on"))
        r = _git(["commit", "-m", "sin interruptor"], self.tmp)
        self.assertEqual(r.returncode, 0,
                         "si esto cambia, actualiza el test: el gate ya no depende del fichero")


class NadaMueveLaBaseSinGate(unittest.TestCase):
    """El hueco del 11-sep-26: pre-commit solo corre en `git commit`. Un merge limpio, un
    fast-forward, un reset o un cherry-pick movían master sin pasar por él. Lo cierra
    `reference-transaction`, que corre en CUALQUIER cambio de ref."""

    OK = {"BTP_GIT_BASE_OK": "1"}

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="githooks_reftx_")
        _git(["init", "-q", "-b", "master"], self.tmp)
        _git(["config", "core.hooksPath", HOOKS], self.tmp)
        os.makedirs(os.path.join(self.tmp, ".claude", "hooks"), exist_ok=True)
        open(os.path.join(self.tmp, ".claude", "hooks", ".base_gate_on"), "w").close()
        # La base lleva el hook COMMITEADO, como la casa base de verdad: el freno solo manda
        # cuando master ya lo contiene (arranque seguro, ver el propio hook).
        os.makedirs(os.path.join(self.tmp, "tools", "githooks"), exist_ok=True)
        import shutil
        shutil.copy(os.path.join(HOOKS, "reference-transaction"),
                    os.path.join(self.tmp, "tools", "githooks", "reference-transaction"))
        self._commit("a", env=self.OK)
        _git(["checkout", "-q", "-b", "rama"], self.tmp)
        self._commit("b")                                   # en rama: libre
        _git(["checkout", "-q", "master"], self.tmp)

    def _commit(self, nombre, env=None):
        with open(os.path.join(self.tmp, nombre), "w") as f:
            f.write(nombre + "\n")
        _git(["add", "-A"], self.tmp, env=env)
        r = _git(["commit", "-q", "-m", nombre], self.tmp, env=env)
        assert r.returncode == 0, r.stderr
        return r

    def _head(self):
        return _git(["rev-parse", "master"], self.tmp).stdout.strip()

    def _no_mueve(self, args, etiqueta):
        antes = self._head()
        r = _git(args, self.tmp)
        self.assertEqual(self._head(), antes, "%s movió master sin gate: el hueco sigue" % etiqueta)
        self.assertIn("BLOQUEADO", r.stderr, etiqueta)

    def test_merge_limpio_bloqueado(self):
        """El caso exacto del 11-sep: fusión sin conflictos a la base."""
        self._no_mueve(["merge", "--no-ff", "rama", "-m", "fusion sin OK"], "merge --no-ff")

    def test_fast_forward_bloqueado(self):
        self._no_mueve(["merge", "--ff-only", "rama"], "fast-forward")

    def test_reset_bloqueado(self):
        _git(["merge", "-q", "--ff-only", "rama"], self.tmp, env=self.OK)
        self._no_mueve(["reset", "-q", "--hard", "HEAD~1"], "reset --hard")

    def test_update_ref_y_branch_f_bloqueados(self):
        self._no_mueve(["update-ref", "refs/heads/master", "rama"], "update-ref")
        _git(["checkout", "-q", "rama"], self.tmp)
        self._no_mueve(["branch", "-f", "master", "rama"], "branch -f")

    def test_borrar_master_bloqueado(self):
        _git(["checkout", "-q", "rama"], self.tmp)
        r = _git(["update-ref", "-d", "refs/heads/master"], self.tmp)
        self.assertIn("BLOQUEADO", r.stderr)
        self.assertTrue(self._head(), "borró master sin gate")

    def test_cherry_pick_bloqueado(self):
        self._no_mueve(["cherry-pick", "rama"], "cherry-pick")

    def test_con_ok_humano_pasa(self):
        r = _git(["merge", "--no-ff", "rama", "-m", "con OK"], self.tmp, env=self.OK)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_las_ramas_de_trabajo_no_se_tocan(self):
        _git(["checkout", "-q", "rama"], self.tmp)
        self._commit("c")
        r = _git(["reset", "-q", "--hard", "HEAD~1"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_gc_y_pack_refs_no_se_bloquean(self):
        """Reescriben refs sin cambiarlas (old == new): no mueven la base."""
        for args in (["pack-refs", "--all"], ["gc", "-q"]):
            with self.subTest(args=args):
                r = _git(args, self.tmp)
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_desde_un_worktree_tambien_frena(self):
        """El interruptor se busca en la casa base (git-common-dir), no en la copia."""
        wt = os.path.join(tempfile.mkdtemp(prefix="githooks_wt_"), "wt")
        r = _git(["worktree", "add", "-q", "-b", "otra", wt, "master"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        os.remove(os.path.join(wt, ".claude", "hooks", ".base_gate_on")) \
            if os.path.exists(os.path.join(wt, ".claude", "hooks", ".base_gate_on")) else None
        antes = self._head()
        r = _git(["branch", "-f", "master", "rama"], wt)
        self.assertEqual(self._head(), antes, "desde un worktree se movió master sin gate")


class ElArranqueNoDejaUnRepoSucio(unittest.TestCase):
    """El fast-forward que TRAE el hook a otra máquina (el portátil, con el deploy_ff viejo que no
    abre el gate) no puede quedarse a medias. Reproducido el 11-sep-26 antes del arreglo:
    ficheros escritos y en el índice, master sin mover."""

    def test_el_ff_que_trae_el_hook_pasa_y_deja_el_arbol_limpio(self):
        base = tempfile.mkdtemp(prefix="githooks_arranque_")
        mini, air = os.path.join(base, "mini"), os.path.join(base, "air")
        _git(["init", "-q", "-b", "master", mini], base)
        os.makedirs(os.path.join(mini, ".claude", "hooks"))
        open(os.path.join(mini, ".claude", "hooks", ".base_gate_on"), "w").close()
        _git(["add", "-A"], mini)
        _git(["commit", "-q", "-m", "base sin el hook"], mini)
        _git(["clone", "-q", mini, air], base)
        _git(["config", "core.hooksPath", "tools/githooks"], air)       # relativa, como el Air
        import shutil
        os.makedirs(os.path.join(mini, "tools", "githooks"))
        shutil.copy(os.path.join(HOOKS, "reference-transaction"),
                    os.path.join(mini, "tools", "githooks", "reference-transaction"))
        _git(["add", "-A"], mini)
        _git(["commit", "-q", "-m", "llega el hook"], mini)
        _git(["fetch", "-q", mini, "master:refs/heads/incoming"], air)
        r = _git(["merge", "--ff-only", "incoming"], air)                # SIN gate, como el viejo
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_git(["status", "--porcelain"], air).stdout.strip(), "",
                         "el fast-forward dejó el árbol a medias")
        # Y a partir de ahí, ya manda.
        _git(["checkout", "-q", "-b", "rama"], air)
        _git(["commit", "-q", "--allow-empty", "-m", "x"], air)
        _git(["checkout", "-q", "master"], air)
        r = _git(["merge", "--ff-only", "rama"], air)
        self.assertIn("BLOQUEADO", r.stderr, "con el hook ya en master, tiene que frenar")


class CerrarSesionPideElOk(unittest.TestCase):
    """`cerrar_sesion.py --apply` fusiona a la base: tiene que pedir el OK ANTES de tocar nada."""

    def test_sin_ok_no_empieza(self):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import cerrar_sesion
        orig = (cerrar_sesion.plan, cerrar_sesion._gate_base_encendido, cerrar_sesion._git)
        tocado = []
        cerrar_sesion.plan = lambda: {"worktree": "/wt", "rama_base": "master", "rama": "x",
                                      "sin_commitear": [("M", "a")], "viola_deny": []}
        cerrar_sesion._gate_base_encendido = lambda: True
        cerrar_sesion._git = lambda *a, **k: tocado.append(a) or (0, "", "")
        os.environ.pop("BTP_GIT_BASE_OK", None)
        try:
            r = cerrar_sesion.cerrar(apply=True)
        finally:
            cerrar_sesion.plan, cerrar_sesion._gate_base_encendido, cerrar_sesion._git = orig
        self.assertIn("falta el OK humano", r.get("error", ""))
        self.assertEqual(tocado, [], "empezó el cierre (commit) antes de saber si podía fusionar")


if __name__ == "__main__":
    unittest.main(verbosity=2)
