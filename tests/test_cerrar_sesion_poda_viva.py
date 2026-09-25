#!/usr/bin/env python3
"""test_cerrar_sesion_poda_viva.py — cerrar no poda el worktree de una sesión que sigue viva.

POR QUÉ EXISTE (25-sep-2026). Los hooks de una sesión se resuelven con `${CLAUDE_PROJECT_DIR}`, que
es su worktree. `cerrar_sesion.py --apply` podaba siempre el worktree desde el que se le llamaba:
la sesión que fusionaba y SEGUÍA trabajando se quedaba sin ningún hook del muro. Probado en vivo:
`gh issue create --help` pasó sin denegar en la sesión podada, mientras el mismo `salida_guard.py`
en casa base sí lo denegaba. Deuda `cerrar-sesion-poda-worktree-vivo-apaga-muro`.

Se reproduce con una casa base de usar y tirar (BTP_REPO): el test nunca toca la de verdad.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "cerrar_sesion.py")
fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True).stdout.strip()


def casa_con_worktree(tmp, nombre):
    base = os.path.join(tmp, "casa")
    os.makedirs(base)
    git(base, "init", "-q", "-b", "master")
    git(base, "config", "user.email", "t@t")
    git(base, "config", "user.name", "t")
    with open(os.path.join(base, ".gitignore"), "w") as f:
        f.write(".claude/worktrees/\n")
    git(base, "add", "-A")
    git(base, "commit", "-q", "-m", "inicio")
    wt = os.path.join(base, ".claude", "worktrees", nombre)
    git(base, "worktree", "add", "-q", "-b", "claude/" + nombre, wt)
    with open(os.path.join(wt, "g.txt"), "w") as f:
        f.write("trabajo\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "trabajo")
    return base, wt


def cerrar(base, wt, tmp, sesion):
    env = dict(os.environ, BTP_REPO=base, BTP_GIT_BASE_OK="1", BTP_STATE_DIR=os.path.join(tmp, "state"))
    env.pop("CLAUDECODE", None)
    if sesion:
        env.update(CLAUDECODE="1", CLAUDE_PID="4242")
    p = subprocess.run([sys.executable, TOOL, "--apply"], cwd=wt, env=env,
                       capture_output=True, text=True, timeout=180)
    return p.stdout + p.stderr


def main():
    print("── llamado desde una sesión viva: fusiona y NO poda ──")
    tmp = tempfile.mkdtemp(prefix="cerrar_viva_")
    base, wt = casa_con_worktree(tmp, "viva")
    salida = cerrar(base, wt, tmp, sesion=True)
    check(os.path.exists(os.path.join(base, "g.txt")), "la fusión a casa base se hace igual")
    check(os.path.isdir(wt), "el worktree de la sesión viva sigue en disco (sus hooks siguen ahí)")
    check("aplazada" in salida and "4242" in salida, "la salida dice que la poda se aplaza y por qué")

    print("── sin sesión dentro: poda como siempre ──")
    tmp = tempfile.mkdtemp(prefix="cerrar_libre_")
    base, wt = casa_con_worktree(tmp, "libre")
    salida = cerrar(base, wt, tmp, sesion=False)
    check(os.path.exists(os.path.join(base, "g.txt")), "fusiona")
    check(not os.path.isdir(wt), "y poda el worktree (el freno no rompe el caso normal): %r" % salida[-160:])

    print("\ntest_cerrar_sesion_poda_viva: %d fallos" % len(fallos))
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
