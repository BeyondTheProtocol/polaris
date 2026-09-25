#!/usr/bin/env python3
"""tests/test_git_mutex_merge_fallido.py — un merge fallido por git_mutex no deja el repo a medias.

EL FALLO (deuda merge-bloqueado-deja-casa-base-sucia, 24-sep-2026, visto 2 veces el mismo día).
Un `git_mutex.py -C ~/claudecode merge …` bloqueado por el freno de la base (hook
reference-transaction sin BTP_GIT_BASE_OK) salía con rc=128, pero dejaba el árbol de casa base
mezclado y staged, con MERGE_HEAD: el sistema vivo corriendo código sin commitear hasta que alguien
hiciera `merge --abort` a mano. `cerrar_sesion.py` ya abortaba; la puerta manual no.

Git REAL en un repo temporal (nunca casa base) y candado aislado con BTP_STATE_DIR.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OK, FALLOS = [], []


def ok(msg, cond, extra=""):
    (OK if cond else FALLOS).append(msg)
    print(("  ✅ " if cond else "  ❌ ") + msg + (("  " + extra) if extra and not cond else ""))


TMP = os.path.realpath(tempfile.mkdtemp(prefix="git_mutex_merge_"))
os.environ["BTP_STATE_DIR"] = os.path.join(TMP, "state")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import git_mutex  # noqa: E402

ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
    ENV.pop(k, None)
os.environ.update({k: v for k, v in ENV.items() if k.startswith("GIT_")})


def git(repo, *a):
    return subprocess.run(["git", "-C", repo] + list(a), capture_output=True, text=True, env=ENV)


def repo_nuevo(nombre, conflicto=False):
    """Repo con master y una rama `tema`. Con conflicto=True ambas tocan la misma línea."""
    r = os.path.join(TMP, nombre)
    os.makedirs(r)
    git(r, "init", "-q", "-b", "master")
    open(os.path.join(r, "a.txt"), "w").write("base\n")
    git(r, "add", "a.txt")
    git(r, "commit", "-qm", "base")
    git(r, "checkout", "-qb", "tema")
    open(os.path.join(r, "a.txt" if conflicto else "b.txt"), "w").write("tema\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "tema")
    git(r, "checkout", "-q", "master")
    if conflicto:
        open(os.path.join(r, "a.txt"), "w").write("master\n")
        git(r, "commit", "-qam", "master")
    return r


def hay_merge_head(r):
    return git(r, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0


def limpio(r):
    return git(r, "status", "--porcelain").stdout.strip() == ""


# 1. Freno de la base: el hook reference-transaction REAL (tools/githooks), armado como en casa base.
r = repo_nuevo("freno")
REAL = os.path.join(ROOT, "tools", "githooks", "reference-transaction")
os.makedirs(os.path.join(r, "tools", "githooks"))       # el freno solo manda si la base ya lo lleva
shutil.copy(REAL, os.path.join(r, "tools", "githooks", "reference-transaction"))
git(r, "add", "tools")
git(r, "commit", "-qm", "freno en la base")
hook = os.path.join(r, ".git", "hooks", "reference-transaction")
shutil.copy(REAL, hook)
os.chmod(hook, os.stat(hook).st_mode | stat.S_IEXEC)
os.makedirs(os.path.join(r, ".claude", "hooks"))
open(os.path.join(r, ".claude", "hooks", ".base_gate_on"), "w").write("")
open(os.path.join(r, ".git", "info", "exclude"), "a").write(".claude/\n")
os.environ.pop("BTP_GIT_BASE_OK", None)
ENV.pop("BTP_GIT_BASE_OK", None)
head0 = git(r, "rev-parse", "HEAD").stdout
rc, _o, err = git_mutex.run(["-C", r, "merge", "--no-ff", "tema", "-m", "m"])
ok("el merge bloqueado por el freno sale con error", rc != 0)
ok("y no deja MERGE_HEAD", not hay_merge_head(r))
ok("ni el árbol mezclado o staged", limpio(r), "-> %r" % git(r, "status", "--porcelain").stdout)
ok("HEAD no se ha movido", git(r, "rev-parse", "HEAD").stdout == head0)
ok("y lo dice", "merge --abort" in err, "-> %r" % err[-200:])

# 2. Conflicto: también se deshace (misma política que cerrar_sesion.py).
r = repo_nuevo("conflicto", conflicto=True)
rc, _o, err = git_mutex.run(["-C", r, "merge", "--no-ff", "tema", "-m", "m"])
ok("un merge con conflicto sale con error", rc != 0)
ok("y queda deshecho: sin MERGE_HEAD ni marcadores", not hay_merge_head(r) and limpio(r)
   and "<<<<<<<" not in open(os.path.join(r, "a.txt")).read())

# 3. Un MERGE_HEAD que YA estaba (otra sesión resolviendo) no se toca.
r = repo_nuevo("ajeno", conflicto=True)
git(r, "merge", "--no-ff", "tema", "-m", "m")          # a pelo: deja el conflicto abierto
ok("preparación: hay una fusión ajena abierta", hay_merge_head(r))
rc, _o, _e = git_mutex.run(["-C", r, "merge", "--no-ff", "tema", "-m", "m"])
ok("un segundo merge falla", rc != 0)
ok("y NO aborta la fusión ajena", hay_merge_head(r))

# 4. `merge --abort` por git_mutex sigue funcionando (no se trata como merge nuevo).
rc, _o, _e = git_mutex.run(["-C", r, "merge", "--abort"])
ok("merge --abort vía git_mutex funciona", rc == 0 and not hay_merge_head(r) and limpio(r))

# 5. Un merge que funciona no se toca.
r = repo_nuevo("bien")
rc, _o, err = git_mutex.run(["-C", r, "merge", "--no-ff", "tema", "-m", "m"])
ok("un merge que funciona sale bien", rc == 0)
ok("hace el commit de merge", git(r, "log", "-1", "--format=%s").stdout.strip() == "m")
ok("y no avisa de ningún abort", "abort" not in err)

# 6. Lo que no es merge ni se mira.
ok("_es_merge_nuevo solo reconoce merges que empiezan",
   git_mutex._es_merge_nuevo(["-C", "x", "merge", "rama"])[0]
   and not git_mutex._es_merge_nuevo(["-C", "x", "merge", "--continue"])[0]
   and not git_mutex._es_merge_nuevo(["-C", "x", "worktree", "prune"])[0])

shutil.rmtree(TMP, ignore_errors=True)
print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS
      else "test_git_mutex_merge_fallido: %d/%d OK" % (len(OK), len(OK)))
sys.exit(1 if FALLOS else 0)
