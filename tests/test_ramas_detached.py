#!/usr/bin/env python3
"""tests/test_ramas_detached.py — que `ramas limpia` no llame «vacío» a un worktree con trabajo.

EL FALLO (20-sep-2026). `_candidatas_vacias` contaba los commits propios con el NOMBRE de la
rama del worktree. Un worktree en *detached HEAD* no tiene nombre, así que corría
`git rev-list --count master..(detached)`, que FALLA; `_run` devuelve "" ante el fallo y el
`or "0"` lo leía como «cero commits propios». Resultado: `limpia` proponía podar un worktree
cuyo HEAD era la punta exacta de una rama con **14 commits sin fusionar** (los gemelos de
criterio, el radar muerto de 86 días, el arreglo del RAG), etiquetado como «limpio y vacío».

Es la misma forma de mentira que persigue el resto del muro: **un comando que falla no es un
cero**. Aquí además la consecuencia es destructiva, porque lo que sigue a esa lista es un
borrado. Por eso el arreglo cuenta contra el HEAD y, si NO se puede contar, no propone podar.

Sin tocar el repo real: se monta un git de juguete con tres worktrees.
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import ramas  # noqa: E402

OK, FALLOS = [], []


def ok(msg, cond, extra=""):
    (OK if cond else FALLOS).append(msg)
    print(("  ✅ " if cond else "  ❌ ") + msg + (("  " + extra) if extra and not cond else ""))


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True).stdout.strip()


tmp = tempfile.mkdtemp(prefix="test-ramas-")
base = os.path.join(tmp, "base")
os.makedirs(base)
git(base, "init", "-q", "-b", "master")
git(base, "config", "user.email", "t@t"); git(base, "config", "user.name", "t")
open(os.path.join(base, "a.txt"), "w").write("1\n")
git(base, "add", "-A"); git(base, "commit", "-qm", "base")

# Rama con trabajo SIN fusionar; el worktree se queda en detached sobre su punta.
git(base, "branch", "con-trabajo")
git(base, "worktree", "add", "-q", os.path.join(tmp, "wt-trabajo"), "con-trabajo")
wt = os.path.join(tmp, "wt-trabajo")
open(os.path.join(wt, "b.txt"), "w").write("2\n")
git(wt, "add", "-A"); git(wt, "commit", "-qm", "trabajo sin fusionar")
punta = git(wt, "rev-parse", "HEAD")
git(wt, "checkout", "-q", "--detach", punta)

# Worktree detached sobre un commit YA fusionado: ese sí se puede podar.
git(base, "worktree", "add", "-q", "--detach", os.path.join(tmp, "wt-vacio"), "master")

_ROOT = ramas.ROOT
try:
    ramas.ROOT = base
    cand = {os.path.basename(w["path"]) for w in ramas._candidatas_vacias()}

    # 1 — el que motiva el test
    ok("un worktree detached con commits SIN fusionar no se propone podar",
       "wt-trabajo" not in cand, "-> candidatas: %s" % sorted(cand))
    # 2 — y no vale arreglarlo dejando de proponer todo
    ok("un worktree detached ya fusionado SÍ se propone", "wt-vacio" in cand,
       "-> candidatas: %s" % sorted(cand))
    # 3 — la ref de un detached es su HEAD, no el literal "(detached)"
    w_det = {"branch": "(detached)", "head": punta[:10]}
    ok("_ref_de usa el HEAD cuando no hay rama", ramas._ref_de(w_det) == punta[:10],
       "-> %r" % ramas._ref_de(w_det))
    ok("_ref_de usa la rama cuando la hay",
       ramas._ref_de({"branch": "con-trabajo", "head": "x"}) == "con-trabajo")
    # 4 — «no se puede contar» es None, nunca 0: es lo que evita el borrado a ciegas
    ok("_ahead_ref devuelve None si git no puede contar",
       ramas._ahead_ref("no-existe-esta-ref", "master") is None,
       "-> %r" % ramas._ahead_ref("no-existe-esta-ref", "master"))
    ok("_ahead_ref devuelve None sin ref", ramas._ahead_ref("", "master") is None)
    ok("_ahead_ref cuenta de verdad cuando puede",
       ramas._ahead_ref(punta, "master") == 1, "-> %r" % ramas._ahead_ref(punta, "master"))
finally:
    ramas.ROOT = _ROOT
    subprocess.run(["rm", "-rf", tmp], capture_output=True)

print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS else "test_ramas_detached: %d/%d OK" % (len(OK), len(OK)))
sys.exit(1 if FALLOS else 0)
