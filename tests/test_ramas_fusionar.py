#!/usr/bin/env python3
"""test_ramas_fusionar.py — fusionar a la base no puede dejar ningún árbol a medias.

POR QUÉ EXISTE (13-sep-2026, hallazgo `freno-base-deja-el-arbol-a-medio-fusionar`, que pasó DOS
veces el mismo día). El freno de la base es un hook `reference-transaction`: exige
`BTP_GIT_BASE_OK=1` para mover `refs/heads/master`, y salta también en fast-forward. El problema no
es el gate, es CUÁNDO bloquea: git ya ha actualizado el working tree y el índice cuando el hook
rechaza el movimiento de la ref, así que casa base queda con **los ficheros del commit nuevo y HEAD
en el viejo**. A medio fusionar, sin nada en `git log` que lo delate.

La salida no fue ablandar el freno, sino no tocar el árbol: `git push . <rama>:<base>` mueve la
referencia y nada más. Y de regalo resuelve el otro caso del mismo día, que casa base estuviera en
OTRA rama con dos sesiones vivas encima: `git merge` habría exigido cambiar de rama bajo sus pies,
que es lo que en junio hizo desaparecer `salida.py` del disco.

Se prueba sobre un repo de mentira, en tmp. No toca el repo real.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True,
                          timeout=30)


def main():
    repo = tempfile.mkdtemp(prefix="ramas_fus_")
    git(repo, "init", "-q", "-b", "master")
    git(repo, "config", "user.email", "t@t.t")
    git(repo, "config", "user.name", "t")
    open(os.path.join(repo, "a.txt"), "w").write("1\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-qm", "uno")
    git(repo, "branch", "trabajo")
    git(repo, "checkout", "-q", "trabajo")
    open(os.path.join(repo, "a.txt"), "w").write("2\n")
    git(repo, "commit", "-qam", "dos")

    os.environ["BTP_REPO"] = repo
    os.makedirs(os.path.join(repo, "tools"), exist_ok=True)
    import shutil
    shutil.copy(os.path.join(ROOT, "tools", "git_mutex.py"), os.path.join(repo, "tools"))
    shutil.copy(os.path.join(ROOT, "tools", "_lock.py"), os.path.join(repo, "tools"))
    import ramas
    import importlib
    importlib.reload(ramas)

    # --- 1. Sin el OK de {{TITULAR}}, no se toca la base ---
    okf, msg = ramas.fusionar_a_base("trabajo", ok_humano=False)
    ok(okf is False, "sin OK humano no fusiona")
    ok("gate de {{TITULAR}}" in msg, "y dice que el gate es suyo")
    ok(git(repo, "rev-parse", "master").stdout != git(repo, "rev-parse", "trabajo").stdout,
       "master sigue donde estaba")

    # --- 2. Si la base está CHECKED OUT en un árbol, no se mueve su ref por debajo ---
    git(repo, "checkout", "-q", "master")
    okf, msg = ramas.fusionar_a_base("trabajo", ok_humano=True)
    ok(okf is False, "con master checked out NO fusiona")
    ok("checked out" in msg, "y explica por qué (%s)" % msg[:60])
    git(repo, "checkout", "-q", "trabajo")

    # --- 3. Con la base suelta y OK humano: fusiona, y el árbol NO se toca ---
    antes_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    antes_status = git(repo, "status", "--porcelain").stdout
    okf, msg = ramas.fusionar_a_base("trabajo", ok_humano=True)
    ok(okf is True, "con la base suelta y OK humano, fusiona (%s)" % msg[:70])
    ok(git(repo, "rev-parse", "master").stdout.strip() ==
       git(repo, "rev-parse", "trabajo").stdout.strip(), "master quedó en el commit de la rama")
    ok(git(repo, "rev-parse", "HEAD").stdout.strip() == antes_head,
       "el HEAD del árbol NO se movió")
    ok(git(repo, "status", "--porcelain").stdout == antes_status,
       "y el working tree quedó intacto — esto es lo que fallaba")
    ok(git(repo, "branch", "--show-current").stdout.strip() == "trabajo",
       "seguimos en la misma rama, no hubo checkout")

    # --- 4. Si la base ha avanzado por su cuenta, NO se mezcla a ciegas ---
    git(repo, "checkout", "-q", "-b", "otra", "master")
    open(os.path.join(repo, "b.txt"), "w").write("x\n")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-qm", "base avanza")
    git(repo, "branch", "-f", "master", "otra")
    git(repo, "checkout", "-q", "trabajo")
    okf, msg = ramas.fusionar_a_base("trabajo", ok_humano=True)
    ok(okf is False, "si la base divergió, no fusiona sola")
    ok("fast-forward" in msg, "y lo dice (%s)" % msg[:60])

    # --- 5. No se fusiona una rama consigo misma ---
    ok(ramas.fusionar_a_base("master", ok_humano=True)[0] is False,
       "no fusiona master sobre master")

    os.environ.pop("BTP_REPO", None)
    print("RESULTADO ramas_fusionar: %d OK, %d fallos" % (_pass, _fail))
    print("✅ FUSIÓN SIN TOCAR EL ÁRBOL — EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
