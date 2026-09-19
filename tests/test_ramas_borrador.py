#!/usr/bin/env python3
"""test_ramas_borrador.py — `ramas.py limpia` no puede llevarse un borrador del gate.

`_candidatas_vacias` decidía «limpio y vacío» con `git status --porcelain`, que NO ve
`tools/state/` porque está gitignored. Un worktree cuyo único contenido nuevo fuese un
borrador del outbox —lo que está esperando la firma de {{TITULAR}}— se consideraba vacío y
`limpia --si` lo borraba. Sin excepción, sin log: el borrador desaparecía.

Monta repos y worktrees de verdad en un tmp; no toca el repo real.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True).stdout


def main():
    tmp = tempfile.mkdtemp(prefix="ramas_borrador_")
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "master")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    os.makedirs(os.path.join(repo, "tools"))
    with open(os.path.join(repo, ".gitignore"), "w") as f:
        f.write("tools/state/\n")
    with open(os.path.join(repo, "tools", "x.py"), "w") as f:
        f.write("x = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")

    # Dos worktrees sin commits propios y sin cambios trackeados: los dos parecen "vacíos".
    wt_limpio = os.path.join(tmp, "wt_limpio")
    wt_borrador = os.path.join(tmp, "wt_borrador")
    git(repo, "worktree", "add", "-q", "-b", "rama-limpia", wt_limpio)
    git(repo, "worktree", "add", "-q", "-b", "rama-borrador", wt_borrador)

    # …pero uno tiene un borrador del gate de salida esperando la firma de {{TITULAR}}.
    outbox = os.path.join(wt_borrador, "tools", "state", "outbox", "pending")
    os.makedirs(outbox)
    with open(os.path.join(outbox, "correo-contacto.json"), "w") as f:
        f.write('{"canal":"correo","estado":"pendiente_OK","nonce":"abc123"}')

    # git status "a secas" NO lo ve: esa es la raíz del bug.
    check("git status a secas dice que el worktree con borrador está limpio",
          not git(wt_borrador, "status", "--porcelain").strip())

    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import importlib
    import ramas
    ramas.ROOT = repo
    importlib.reload  # (no recargamos: solo reapuntamos ROOT)
    ramas.ROOT = repo

    cand = [os.path.basename(w["path"]) for w in ramas._candidatas_vacias()]
    check("el worktree de verdad vacío SÍ es candidato a podar", "wt_limpio" in cand)
    check("el que guarda un borrador NO es candidato", "wt_borrador" not in cand)

    # Y el estado del lazo (cola) cuenta igual: tampoco se poda por debajo.
    cola = os.path.join(wt_limpio, "tools", "state", "queue", "pending")
    os.makedirs(cola)
    with open(os.path.join(cola, "job.json"), "w") as f:
        f.write("{}")
    cand2 = [os.path.basename(w["path"]) for w in ramas._candidatas_vacias()]
    check("un worktree con un job en la cola tampoco se poda", "wt_limpio" not in cand2)

    # Y lo que descubrió esto en vivo: `limpia` proponía podar worktrees que tenían SESIONES
    # de {{TITULAR}} trabajando dentro en ese momento. Podar por debajo de una sesión que está
    # escribiendo es la forma más rápida de perderle trabajo a otra de sus ventanas.
    import shutil
    shutil.rmtree(os.path.join(wt_limpio, "tools", "state"))     # vuelve a estar «vacío»
    cand3 = [os.path.basename(w["path"]) for w in ramas._candidatas_vacias()]
    check("(control) vuelve a ser candidato al quitarle el trabajo vivo", "wt_limpio" in cand3)

    _orig = ramas.sesiones
    ramas.sesiones = lambda: [{"rama": "rama-limpia", "es_base": False, "pid": 1}]
    try:
        cand4 = [os.path.basename(w["path"]) for w in ramas._candidatas_vacias()]
        check("con una sesión viva dentro NO se poda", "wt_limpio" not in cand4)
    finally:
        ramas.sesiones = _orig

    print("RESULTADO ramas/borrador: %d OK, %d fallos" % (_pass, _fail))
    print("✅ LA PODA NO SE LLEVA BORRADORES" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
