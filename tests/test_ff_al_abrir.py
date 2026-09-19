#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de ff_al_abrir.sh — el Air se pone al día solo, sin pisar nada.

Lo que se protege es que las CUATRO guardas aguanten. Sin ellas, un pull automático
al abrir sesión no es comodidad, es una forma elegante de perder trabajo:
  1. en el mini no corre (hablaría consigo mismo)
  2. con trabajo sin commitear no toca nada
  3. fuera de master tampoco
  4. pase lo que pase, sale 0 y calla
"""
import os
import shutil
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(RAIZ, "tools", "ff_al_abrir.sh")

fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def _repo_falso(base, hostname="MacBook-Air", con_ssh=True, sucio=False, rama="master"):
    """Monta un repo git de mentira + un `hostname` falso en el PATH."""
    repo = os.path.join(base, "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q", "-b", "master", repo], check=True)
    with open(os.path.join(repo, "f.txt"), "w") as fh:
        fh.write("hola\n")
    env_git = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True, env=env_git)
    subprocess.run(["git", "-C", repo, "commit", "-qm", "x"], check=True, env=env_git)
    if rama != "master":
        subprocess.run(["git", "-C", repo, "checkout", "-qb", rama], check=True)
    if sucio:
        with open(os.path.join(repo, "f.txt"), "a") as fh:
            fh.write("cambio a medias\n")

    # `hostname` falso, para poder probar la guarda del mini sin estar en el mini.
    binp = os.path.join(base, "bin")
    os.makedirs(binp)
    hp = os.path.join(binp, "hostname")
    with open(hp, "w") as fh:
        fh.write("#!/bin/bash\necho %s\n" % hostname)
    os.chmod(hp, 0o755)

    casa = os.path.join(base, "home")
    os.makedirs(os.path.join(casa, ".ssh"))
    if con_ssh:
        with open(os.path.join(casa, ".ssh", "config"), "w") as fh:
            fh.write("Host polaris\n  HostName 100.64.0.1\n")

    entorno = dict(os.environ, BTP_REPO=repo, HOME=casa,
                   PATH=binp + os.pathsep + os.environ["PATH"],
                   TMPDIR=os.path.join(base, "tmp"))
    os.makedirs(entorno["TMPDIR"], exist_ok=True)
    return repo, entorno


def _log(repo):
    ruta = os.path.join(repo, ".claude", "logs", "ff-al-abrir.log")
    return open(ruta).read() if os.path.exists(ruta) else ""


def correr(entorno):
    p = subprocess.run(["bash", SCRIPT], capture_output=True, text=True, env=entorno, timeout=60)
    return p


def main():
    base = tempfile.mkdtemp(prefix="btp_ff_")
    try:
        print("── guarda 1: en el mini no corre ──")
        d = os.path.join(base, "c1"); os.makedirs(d)
        repo, env = _repo_falso(d, hostname="Polaris")
        p = correr(env)
        check(p.returncode == 0, "sale 0 en el mini")
        check(_log(repo) == "", "en el mini no intenta nada (log vacío)")

        print("── refuerzo: sin alias ssh tampoco ──")
        d = os.path.join(base, "c2"); os.makedirs(d)
        repo, env = _repo_falso(d, con_ssh=False)
        p = correr(env)
        check(p.returncode == 0 and _log(repo) == "", "sin `Host polaris` en ssh/config, no hace nada")

        print("── guarda 2: árbol sucio ──")
        d = os.path.join(base, "c3"); os.makedirs(d)
        repo, env = _repo_falso(d, sucio=True)
        p = correr(env)
        check(p.returncode == 0, "sale 0 con el árbol sucio")
        check("sin commitear" in _log(repo), "lo apunta y NO toca el repo")

        print("── guarda 3: fuera de master ──")
        d = os.path.join(base, "c4"); os.makedirs(d)
        repo, env = _repo_falso(d, rama="claude/algo")
        p = correr(env)
        check("no está en master" in _log(repo) or "no en master" in _log(repo)
              or "rama" in _log(repo), "en una rama de trabajo, se abstiene")

        print("── guarda 4: fail-open ──")
        d = os.path.join(base, "c5"); os.makedirs(d)
        repo, env = _repo_falso(d)          # ssh a un host que no existe → deploy_ff falla
        p = correr(env)
        check(p.returncode == 0, "con el mini inalcanzable, sale 0")
        check("sin efecto" in _log(repo) or "al día" in _log(repo),
              "deja constancia del intento fallido")

        print("── portabilidad: nada que macOS no traiga ──")
        # El fail-open es tan silencioso que tapaba un bug real: `timeout` no existe en
        # macOS, el comando fallaba siempre y el log decía «sin efecto», igual que si el
        # mini estuviese apagado. Un guardarraíl no puede esconder que la pieza no corre.
        fuente = open(SCRIPT, encoding="utf-8").read()
        import re as _re
        crudo = [ln for ln in fuente.splitlines()
                 if _re.search(r"^\s*(timeout|gtimeout)\s+\d", ln)]
        check(not crudo, "no invoca `timeout` a pelo (%s)" % crudo)
        check("command -v timeout" in fuente, "comprueba antes si existe")

        print("── throttle ──")
        d = os.path.join(base, "c6"); os.makedirs(d)
        repo, env = _repo_falso(d)
        correr(env)
        n1 = len(_log(repo).splitlines())
        correr(env)
        n2 = len(_log(repo).splitlines())
        check(n1 == n2, "la segunda llamada seguida no vuelve a intentarlo")
    finally:
        shutil.rmtree(base, ignore_errors=True)

    print()
    if fallos:
        print("❌ %d fallo(s): %s" % (len(fallos), "; ".join(fallos)))
        return 1
    print("✅ ff_al_abrir OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
