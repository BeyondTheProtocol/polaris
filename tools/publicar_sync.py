#!/usr/bin/env python3
"""tools/publicar_sync.py — mantiene el repo PÚBLICO al día con lo que se fusiona a casa base.

POR QUÉ (19-sep-2026, petición de {{TITULAR}}): el repo público se generó a mano el 17-sep y se
quedó congelado. Un espejo que se actualiza a mano se desactualiza y, peor, invita a empujar
«rápido» sin pasar por el barrido — que es exactamente como salió publicado un término vetado.
Esto lo hace siempre igual: deriva, verifica, y solo entonces empuja.

GATES, en orden (todos fail-closed: a la primera duda no se empuja):
  1. HALT / código rojo activo → no se toca nada hacia fuera.
  2. `publicar.py` genera el árbol: aborta si falta el overlay (rc=3) o si el barrido final
     encuentra un término vetado (rc=1). Este script NO repite esa lógica, la exige.
  3. El árbol tiene que COMPILAR (`compileall`): publicar código roto es publicar basura.
  4. Si no hay diferencias con lo ya publicado, no se hace commit ni push (silencioso).

Lo que se publica sale SIEMPRE de casa base (`BTP_REPO`), nunca de un worktree: el árbol se
deriva de `git ls-files`, y una rama a medias publicaría trabajo sin fusionar.

Uso:
  python3 tools/publicar_sync.py            # deriva, verifica y empuja si hay cambios
  python3 tools/publicar_sync.py --dry      # todo menos el commit y el push
  python3 tools/publicar_sync.py --estado   # qué se publicaría, sin generar nada
"""
import argparse
import os
import subprocess
import sys
from datetime import date

AQUI = os.path.dirname(os.path.abspath(__file__))
if AQUI not in sys.path:
    sys.path.insert(0, AQUI)

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
ESPEJO = os.environ.get("BTP_ESPEJO_DIR") or os.path.join(REPO, "_cajita", "publico")
REMOTO = "https://github.com/BeyondTheProtocol/polaris.git"
RAMA = "master"


def _halt():
    return [r for r in (os.path.expanduser("~/.btp.HALT"), os.path.join(REPO, ".HALT"))
            if os.path.exists(r)]


def _git(*args, cwd=None, check=True):
    return subprocess.run(["git", "-C", cwd or ESPEJO] + list(args),
                          capture_output=True, text=True, check=check)


def generar():
    """Deriva el árbol público. Devuelve (rc, salida). rc≠0 = no hay nada que publicar."""
    r = subprocess.run([sys.executable, os.path.join(REPO, "tools", "publicar.py"), ESPEJO, "--forzar"],
                       capture_output=True, text=True, env=dict(os.environ, BTP_REPO=REPO))
    return r.returncode, (r.stdout + r.stderr).strip()


def compila(destino=None):
    """El árbol público tiene que ser código válido antes de salir."""
    destino = destino or ESPEJO
    dirs = [d for d in ("tools", "tests", "pipeline") if os.path.isdir(os.path.join(destino, d))]
    if not dirs:
        return False, "el árbol no tiene ni tools/ ni tests/: algo fue mal al generar"
    r = subprocess.run([sys.executable, "-m", "compileall", "-q"] + dirs,
                       cwd=destino, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()[:400]


def _preparar_git():
    if not os.path.isdir(os.path.join(ESPEJO, ".git")):
        _git("init", "-q", "-b", RAMA)
        _git("remote", "add", "origin", REMOTO)
        # El espejo nace vacío pero el repo remoto ya tiene historia: sin esto el primer
        # push sería un non-fast-forward y la única salida un --force, que es justo lo que
        # no queremos como rutina. `reset --soft` deja el commit colgando del remoto.
        if _git("fetch", "-q", "origin", RAMA, check=False).returncode == 0:
            _git("reset", "--soft", "origin/%s" % RAMA, check=False)
    else:
        actual = _git("remote", "get-url", "origin", check=False).stdout.strip()
        if actual != REMOTO:
            _git("remote", "set-url", "origin", REMOTO, check=False)


def sincronizar(dry=False):
    parado = _halt()
    if parado:
        return 2, "HALT activo (%s): no se publica nada hacia fuera" % ", ".join(parado)

    rc, salida = generar()
    if rc != 0:
        return rc, "publicar.py se negó (rc=%d):\n%s" % (rc, salida)

    ok, detalle = compila()
    if not ok:
        return 4, "el árbol público no compila, no se publica:\n%s" % detalle

    _preparar_git()
    _git("add", "-A")
    if not _git("status", "--porcelain").stdout.strip():
        return 0, "sin cambios: el repo público ya está al día"
    if dry:
        n = len(_git("diff", "--cached", "--name-only").stdout.split())
        return 0, "--dry: %d fichero(s) cambiarían; no se ha empujado" % n

    _git("-c", "user.name=Polaris (Beyond the Protocol)",
         "-c", "user.email=%s" % os.environ.get("BTP_GIT_EMAIL", "noreply@anthropic.com"),
         "commit", "-q", "-m", "sync %s: árbol derivado del privado, barrido limpio" % date.today())
    r = _git("push", "origin", RAMA, check=False)
    if r.returncode != 0:
        return 5, "commit hecho, push falló:\n%s" % (r.stderr or r.stdout).strip()[:400]
    return 0, "publicado: %s" % _git("log", "--oneline", "-1").stdout.strip()


def main(argv=None):
    p = argparse.ArgumentParser(description="Mantiene el repo público al día")
    p.add_argument("--dry", action="store_true", help="genera y verifica, pero no empuja")
    p.add_argument("--estado", action="store_true", help="solo dice cómo está el espejo")
    a = p.parse_args(argv)

    if a.estado:
        print("espejo: %s (%s)" % (ESPEJO, "existe" if os.path.isdir(ESPEJO) else "sin generar"))
        print("HALT: %s" % (", ".join(_halt()) or "no"))
        if os.path.isdir(os.path.join(ESPEJO, ".git")):
            print("último: %s" % _git("log", "--oneline", "-1", check=False).stdout.strip())
        return 0

    rc, msg = sincronizar(a.dry)
    print(("✅ " if rc == 0 else "🔴 ") + msg)
    return rc


if __name__ == "__main__":
    sys.exit(main())
