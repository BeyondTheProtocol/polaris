#!/usr/bin/env python3
"""test_casa_base_guard.py — el hook que impide mover el árbol vivo de casa base «para mirar».

Casos reales del 22-sep-2026 (los dos incidentes) + lo legítimo que NO debe bloquear. Casa base se
simula con BTP_CASA_BASE apuntando a un directorio temporal: nunca se toca la de verdad.
"""
import json
import os
import subprocess
import sys
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
