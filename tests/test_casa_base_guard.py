#!/usr/bin/env python3
"""test_casa_base_guard.py — el hook que impide mover el árbol vivo de casa base «para mirar».

Casos reales del 22-sep-2026 (los dos incidentes) + lo legítimo que NO debe bloquear. Casa base se
simula con BTP_CASA_BASE apuntando a un directorio temporal: nunca se toca la de verdad.
"""
import json
import os
import subprocess
import sys
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, ".claude", "hooks", "casa_base_guard.py")
CASA = os.path.realpath(tempfile.mkdtemp(prefix="casa_guard_"))
WT = os.path.join(CASA, ".claude", "worktrees", "rama-x")
os.makedirs(WT)
os.makedirs(os.path.join(CASA, "tools"))


def correr(command, cwd=CASA, tool="Bash", env_extra=None):
    env = dict(os.environ, BTP_CASA_BASE=CASA)
    env.pop("BTP_CASA_BASE_OK", None)
    env.pop("MURO_PROFILE", None)
    env.update(env_extra or {})
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(
        {"tool_name": tool, "tool_input": {"command": command}, "cwd": cwd}),
        capture_output=True, text=True, env=env, timeout=20)
    if not p.stdout.strip():
        return "allow"
    return json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"]


class CaminoHastaCasaBase(unittest.TestCase):
    """Formas de LLEGAR a casa base que se colaban (rondas adversariales de `verificacion`, 22 y
    24-sep-26). Vienen de la regla `git-mueve-casa-base` de `regla_en_accion.py`, que hacía lo
    mismo que este hook hasta que se unificaron en `_git_camino`."""

    def test_se_cuelan_por_el_camino(self):
        for cmd, cwd in (
                ("git -c core.x=y checkout abc1234 --", CASA),
                ("git --git-dir=%s/.git --work-tree=%s checkout abc1234 --" % (CASA, CASA), WT),
                ("GIT_DIR=%s/.git git checkout abc1234 --" % CASA, WT),
                ('GIT_DIR="%s/.git" git checkout abc1234 --' % CASA, WT),
                ("env -i PATH=/usr/bin git checkout abc1234 --", CASA),
                ("command git checkout abc1234 --", CASA),
                ("Git checkout abc1234 --", CASA),
                ("git chec''kout abc1234 --", CASA),
                ("if true; then git checkout abc1234 --; fi", CASA),
                ("x=$(git checkout abc1234 --)", CASA),
                ("pushd %s && git stash" % CASA, WT),
                ("cd %s && cd - && git stash" % WT, CASA),
                ("sh -c 'git checkout abc1234 --'", CASA),
                ("bash -lc 'cd %s && git reset --hard'" % CASA, WT),
                ("(cd %s; git checkout abc1234 --)" % CASA, WT),
                ("bash <<'EOF'\ncd %s\ngit checkout abc1234 --\nEOF" % CASA, WT),
                ("git -C %s/tools checkout abc1234 --" % CASA, WT),
                ("git --no-pager -C %s checkout abc1234 --" % CASA, WT),
                ("git -C %s/.claude/worktrees checkout abc1234 --" % CASA, WT)):
            self.assertEqual(correr(cmd, cwd=cwd), "deny", cmd)

    def test_subcomandos_que_tambien_mueven(self):
        for cmd in ("git reset feature-x", "git bisect start HEAD HEAD~10", "git apply x.patch",
                    "git update-ref refs/heads/master HEAD~1", "git symbolic-ref HEAD refs/heads/otra",
                    "git rebase master", "git cherry-pick abc1234", "git clean -fd", "git pull"):
            self.assertEqual(correr(cmd), "deny", cmd)


class NoSonMovimientos(unittest.TestCase):
    """Lo legítimo que NO puede bloquearse (falsos positivos reales del replay y de verificacion)."""

    def test_solo_leen_o_solo_tocan_el_indice(self):
        for cmd in ("git stash list", "git stash show -p", "git restore --staged tools/x.json",
                    "git reset HEAD tools/x.json", "git reset tools/x.json", "git clean -n",
                    "git checkout --help", "git symbolic-ref --short HEAD", "git merge claude/x",
                    "git show HEAD~1:tools/x.py", "git diff A B -- tools", "git log --oneline -3"):
            self.assertEqual(correr(cmd), "allow", cmd)

    def test_el_cd_de_un_subshell_no_contamina(self):
        for cmd in ("pushd %s; git log -1; popd; git checkout -b foo" % CASA,
                    "(cd %s && git log -1); git checkout -b foo" % CASA):
            self.assertEqual(correr(cmd, cwd=WT), "allow", cmd)

    def test_texto_que_solo_menciona_el_comando(self):
        for cmd in ("echo 'git checkout master'",
                    "python3 tools/deuda.py abrir x 'hice git checkout en casa base'",
                    'python3 -c "print(\'GIT_DIR=%s/.git git checkout master\')"' % CASA):
            self.assertEqual(correr(cmd), "allow", cmd)

    def test_el_otro_escape_tambien_vale(self):
        self.assertEqual(correr("git checkout abc1234 --",
                                env_extra={"BTP_ALLOW_CASA_BASE": "1"}), "allow")


class RondaDelDosCuatro(unittest.TestCase):
    """Lo que encontró `verificacion` al unificar los dos frenos (24-sep-26)."""

    def test_reset_a_una_rama_o_tag_mueve(self):
        for cmd in ("git reset claude/otra-rama", "git reset origin/master", "git reset v1.0.0"):
            self.assertEqual(correr(cmd), "deny", cmd)

    def test_reset_a_un_fichero_no_mueve(self):
        for cmd in ("git reset tools/x.json", "git reset -- tools/x.json",
                    "git reset HEAD tools/x.json"):
            self.assertEqual(correr(cmd), "allow", cmd)

    def test_volver_a_master_no_vale_para_reescribirlo(self):
        # `-B master` con HEAD desacoplado REESCRIBE master al commit viejo: el incidente, pero
        # permanente. La excepción es para `checkout/switch master` a secas.
        for cmd in ("git checkout -B master", "git switch -C master",
                    "git checkout --detach master", "git checkout --orphan master"):
            self.assertEqual(correr(cmd), "deny", cmd)
        for cmd in ("git checkout master", "git switch -q master", "git checkout -q -f master"):
            self.assertEqual(correr(cmd), "allow", cmd)

    def test_si_el_troceo_no_carga_el_hook_calla(self):
        """FAIL-OPEN: el analizador usa `salida_guard._ordenes`. Si ese fichero deja de importar,
        esto no puede pasar a denegar TODO git de todas las sesiones (era un regex sin cwd)."""
        sitio = tempfile.mkdtemp(prefix="camino_roto_")
        for f in ("casa_base_guard.py", "_git_camino.py"):
            shutil.copy(os.path.join(ROOT, ".claude", "hooks", f), os.path.join(sitio, f))
        with open(os.path.join(sitio, "salida_guard.py"), "w") as fh:
            fh.write("def _ordenes(  # roto a propósito\n")
        env = dict(os.environ, BTP_CASA_BASE=CASA)
        for k in ("BTP_CASA_BASE_OK", "BTP_ALLOW_CASA_BASE", "MURO_PROFILE"):
            env.pop(k, None)
        for cmd, cwd in (("git stash list", "/tmp"), ("git checkout -b feature-x", "/tmp"),
                         ("git pull", "/tmp")):
            p = subprocess.run([sys.executable, os.path.join(sitio, "casa_base_guard.py")],
                               input=json.dumps({"tool_name": "Bash", "cwd": cwd,
                                                 "tool_input": {"command": cmd}}),
                               capture_output=True, text=True, env=env, timeout=20)
            self.assertNotIn("deny", p.stdout, cmd)


class Deniega(unittest.TestCase):
    def test_incidente_1510_checkout_de_commit_en_casa_base(self):
        self.assertEqual(correr("git checkout -q 9528f4e --"), "deny")

    def test_con_menos_C_hacia_casa_base_desde_otro_sitio(self):
        self.assertEqual(correr("git -C %s checkout HEAD~1 --" % CASA, cwd="/tmp"), "deny")

    def test_cd_a_casa_base_en_el_mismo_comando(self):
        self.assertEqual(correr("cd %s && git stash" % CASA, cwd="/tmp"), "deny")

    def test_subdirectorio_de_casa_base_tambien_es_casa_base(self):
        self.assertEqual(correr("git reset --hard HEAD~1", cwd=os.path.join(CASA, "tools")), "deny")

    def test_switch_restore_stash(self):
        for c in ("git switch -d HEAD~2", "git restore tools/x.py", "git stash push -m x"):
            self.assertEqual(correr(c), "deny", c)

    def test_escondido_tras_otra_orden(self):
        self.assertEqual(correr("git status; git checkout feature-x"), "deny")


class DejaPasar(unittest.TestCase):
    def test_volver_a_master_es_la_reparacion(self):
        for c in ("git checkout master", "git checkout -q master", "git switch master"):
            self.assertEqual(correr(c), "allow", c)

    def test_leer_historia(self):
        for c in ("git show 9528f4e:CLAUDE.md", "git diff A B -- tools/x.py", "git log -3",
                  "git status --short", "git stash list", "git stash show -p stash@{0}"):
            self.assertEqual(correr(c), "allow", c)

    def test_en_un_worktree_no_aplica(self):
        self.assertEqual(correr("git checkout -q claude/rama-x", cwd=WT), "allow")
        self.assertEqual(correr("git -C %s stash" % WT, cwd="/tmp"), "allow")

    def test_otro_repo(self):
        self.assertEqual(correr("git checkout main", cwd="/tmp"), "allow")
        self.assertEqual(correr("git reset --hard", cwd="/tmp"), "allow")

    def test_ok_humano(self):
        self.assertEqual(correr("git checkout -q abc --", env_extra={"BTP_CASA_BASE_OK": "1"}), "allow")

    def test_el_lazo_no_pasa_por_aqui(self):
        """La auto-mejora hace `git checkout -b auto-mejora-…` en casa base por diseño, y el lazo
        tiene su propio guard (muro_guard). Con MURO_PROFILE puesto, este hook no interviene."""
        self.assertEqual(correr("git checkout -b auto-mejora-2026-09-23",
                                env_extra={"MURO_PROFILE": "privileged"}), "allow")

    def test_otras_tools_y_basura(self):
        self.assertEqual(correr("git checkout x", tool="Read"), "allow")
        self.assertEqual(correr("echo 'comillas sin cerrar"), "allow")    # fail-open


if __name__ == "__main__":
    unittest.main()
