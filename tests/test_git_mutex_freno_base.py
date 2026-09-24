#!/usr/bin/env python3
"""Test: `git_mutex.py` no deja casa base a medio fusionar.

POR QUÉ EXISTE (24-sep-2026, deuda `freno-base-deja-el-arbol-a-medio-fusionar`). Una sesión lanzó
`git_mutex.py -C casa-base merge --no-ff <rama>` sin BTP_GIT_BASE_OK=1. El freno nativo
(`tools/githooks/reference-transaction`) rechazó mover master, pero TARDE: git ya había escrito el
índice y el árbol. Casa base quedó con MERGE_HEAD y la rama staged unos 17 minutos, y ninguna otra
sesión podía fusionar («No has concluido la fusión»). `cerrar_sesion.py` ya comprobaba el gate
antes; la puerta de `git_mutex.py`, no.

Repos git REALES y desechables (BTP_REPO a un tmp). Qué se exige:
  · sin el OK, un merge / push .:master sobre casa base no se lanza (rc 3) y no toca nada;
  · en un worktree de trabajo, fusionar no pide nada (el freno es de la base, no de todo);
  · con el OK, si el merge falla (conflicto, o un hook que rechaza la ref TARDE, como el real),
    casa base vuelve a como estaba: sin MERGE_HEAD, limpia, HEAD igual.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # este árbol: probamos su código
GM = os.path.join(ROOT, "tools", "git_mutex.py")

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def g(repo, *args, env=None):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, env=env)


def escribe(repo, rel, txt):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(txt)


def montar():
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="gm_freno_"))
    base = os.path.join(tmp, "base")
    os.makedirs(base)
    g(base, "init", "-q", "-b", "master")
    g(base, "config", "user.email", "t@t"); g(base, "config", "user.name", "t")
    escribe(base, "a.txt", "uno\n")
    g(base, "add", "."); g(base, "commit", "-qm", "inicio")
    escribe(base, ".claude/hooks/.base_gate_on", "")          # el interruptor del freno
    with open(os.path.join(base, ".git", "info", "exclude"), "a") as f:
        f.write(".claude/\n")
    g(base, "branch", "rama")
    wt = os.path.join(tmp, "wt")
    g(base, "worktree", "add", "-q", wt, "rama")
    escribe(wt, "b.txt", "nuevo\n")
    g(wt, "add", "."); g(wt, "commit", "-qm", "trabajo")
    env = dict(os.environ, BTP_REPO=base, BTP_STATE_DIR=os.path.join(tmp, "state"))
    env.pop("BTP_GIT_BASE_OK", None)
    return tmp, base, wt, env


def gm(env, *args):
    return subprocess.run([sys.executable, GM] + list(args), capture_output=True, text=True, env=env)


def intacta(base, head0, etiqueta):
    ok(g(base, "rev-parse", "HEAD").stdout.strip() == head0, etiqueta + ": HEAD no se mueve")
    ok(not os.path.exists(os.path.join(base, ".git", "MERGE_HEAD")), etiqueta + ": sin MERGE_HEAD")
    ok(g(base, "status", "--porcelain").stdout.strip() == "", etiqueta + ": árbol limpio")


def main():
    tmp, base, wt, env = montar()
    try:
        head0 = g(base, "rev-parse", "HEAD").stdout.strip()

        # 1) sin OK: el merge sobre casa base no se lanza
        p = gm(env, "-C", base, "merge", "--no-ff", "rama", "-m", "x")
        ok(p.returncode == 3, "sin OK el merge a la base devuelve 3 (rc=%s)" % p.returncode)
        ok("BTP_GIT_BASE_OK" in p.stderr, "dice qué falta")
        intacta(base, head0, "merge sin OK")

        # 2) sin OK: push .:master tampoco (mueve la ref sin tocar el árbol, pero es la base)
        p = gm(env, "-C", base, "push", ".", "rama:master")
        ok(p.returncode == 3, "sin OK push .:master devuelve 3 (rc=%s)" % p.returncode)
        intacta(base, head0, "push sin OK")

        # 3) merge --abort nunca se frena (es justo la salida)
        p = gm(env, "-C", base, "merge", "--abort")
        ok(p.returncode != 3, "merge --abort no lo frena el gate")

        # 4) en el worktree de trabajo no se pide nada
        p = gm(env, "-C", wt, "merge", "--no-edit", "master")
        ok(p.returncode == 0, "fusionar en un worktree de trabajo no pide OK (rc=%s %s)"
           % (p.returncode, p.stderr[-200:]))

        # 5) con OK y un hook que rechaza la ref TARDE (como el freno real): se deshace
        hooks = os.path.join(tmp, "hooks")
        escribe(hooks, "reference-transaction",
                '#!/bin/sh\n[ "$1" = prepared ] || exit 0\n'
                'while read -r o n r; do [ "$r" = refs/heads/master ] && [ "$o" != "$n" ] && '
                '{ echo RECHAZO >&2; exit 1; }; done; exit 0\n')
        os.chmod(os.path.join(hooks, "reference-transaction"), 0o755)
        g(base, "config", "core.hooksPath", hooks)
        env_ok = dict(env, BTP_GIT_BASE_OK="1")
        p = gm(env_ok, "-C", base, "merge", "--no-ff", "rama", "-m", "x")
        ok(p.returncode != 0, "el hook rechaza la ref (rc=%s)" % p.returncode)
        ok("deshecho" in p.stderr, "avisa de que lo deshizo")
        intacta(base, head0, "rechazo tardío")
        g(base, "config", "--unset", "core.hooksPath")

        # 6) con OK y conflicto real: se deshace
        escribe(wt, "a.txt", "de la rama\n"); g(wt, "commit", "-qam", "choca")
        escribe(base, "a.txt", "de la base\n"); g(base, "commit", "-qam", "base avanza")
        head1 = g(base, "rev-parse", "HEAD").stdout.strip()
        p = gm(env_ok, "-C", base, "merge", "--no-ff", "rama", "-m", "x")
        ok(p.returncode != 0, "el conflicto falla (rc=%s)" % p.returncode)
        intacta(base, head1, "conflicto")

        # 6b) con OK y cherry-pick con conflicto: también se deshace (esto no lo cubre run())
        choca = g(wt, "rev-parse", "HEAD").stdout.strip()
        p = gm(env_ok, "-C", base, "cherry-pick", choca)
        ok(p.returncode != 0, "el cherry-pick choca (rc=%s)" % p.returncode)
        ok(not os.path.exists(os.path.join(base, ".git", "CHERRY_PICK_HEAD")), "cherry-pick: sin CHERRY_PICK_HEAD")
        intacta(base, head1, "cherry-pick")

        # 7) con OK y sin conflicto: fusiona de verdad (el freno no rompe el camino legítimo)
        g(base, "reset", "-q", "--hard", head0)
        g(wt, "reset", "-q", "--hard", "HEAD~1")
        p = gm(env_ok, "-C", base, "merge", "--no-ff", "rama", "-m", "fusion")
        ok(p.returncode == 0, "con OK la fusión limpia entra (rc=%s %s)" % (p.returncode, p.stderr[-200:]))
        ok(os.path.exists(os.path.join(base, "b.txt")), "el trabajo de la rama llega a la base")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("Resultado: %d OK · %d FAIL" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
