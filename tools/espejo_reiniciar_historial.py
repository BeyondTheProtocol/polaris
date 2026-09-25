#!/usr/bin/env python3
"""tools/espejo_reiniciar_historial.py — reinicia el historial del espejo público en UN commit.

POR QUÉ: `publicar.py` limpia el árbol que se publica AHORA. Si algo que no debía salir ya salió,
sigue en cada commit anterior del espejo, y regenerar no lo toca. Esto deja el espejo en una
rama huérfana con un único commit, derivado y barrido, y lo empuja con --force.

Es una salida irreversible hacia fuera: GATE DE LA TITULAR. Por defecto es un ensayo.

GATES, en orden (todos fail-closed):
  1. HALT / código rojo → no se toca nada.
  2. Toma el mismo candado que `publicar_sync.py`: el daemon no publica mientras tanto.
  3. `publicar.py` regenera el árbol con rc=0 (su barrido, sus colisiones, sus fichas).
  4. Barrido propio e independiente con los vetados del overlay, y `compileall`.
  5. Con `--ejecutar`: copia `git bundle` local del historial viejo (no se sube), rama huérfana,
     y pide escribir REESCRIBIR antes del push --force. Sin esa palabra, no empuja.

Lo que esto NO limpia (se hace a mano, ver la nota de la sesión): forks de terceros, las refs
`refs/pull/*` de PRs ya abiertos y las vistas en caché de GitHub. Eso pasa por GitHub Support.

Uso (desde casa base, nunca desde un worktree):
  python3 tools/espejo_reiniciar_historial.py              # ensayo, no toca GitHub
  python3 tools/espejo_reiniciar_historial.py --ejecutar   # reescribe (pide confirmación)
"""
import argparse
import os
import subprocess
import sys
from datetime import date

AQUI = os.path.dirname(os.path.abspath(__file__))
if AQUI not in sys.path:
    sys.path.insert(0, AQUI)

import _lock  # noqa: E402
import publicar_sync  # noqa: E402 — mismas rutas, mismo HALT, mismo candado

REPO = publicar_sync.REPO
ESPEJO = publicar_sync.ESPEJO
PALABRA = "REESCRIBIR"


def _git(*args):
    r = subprocess.run(["git", "-C", ESPEJO] + list(args), capture_output=True, text=True)
    if r.returncode:
        raise SystemExit("🔴 git %s: %s" % (args[0], (r.stderr or r.stdout).strip()[-400:]))
    return r.stdout.strip()


def restos(destino):
    """Ficheros del árbol con un término vetado, barridos aparte de `publicar.py`."""
    os.environ["BTP_REPO"] = REPO
    import publicar
    vetos = publicar.VETADOS + publicar._vetados_del_titular()
    sucios = []
    for base, dirs, fs in os.walk(destino):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in fs:
            ruta = os.path.join(base, f)
            rel = os.path.relpath(ruta, destino)
            if rel == os.path.join("tools", "publicar.py"):
                continue
            try:
                with open(ruta, encoding="utf-8") as fh:
                    txt = fh.read()
            except (UnicodeDecodeError, OSError):
                continue
            if any(p.search(txt) for p in vetos):
                sucios.append(rel)
    return sucios


def reiniciar(ejecutar=False, confirmar=input):
    parado = publicar_sync._halt()
    if parado:
        return 2, "HALT activo (%s): no se toca nada" % ", ".join(parado)
    with _lock.lock("publicar-sync", timeout=publicar_sync.LOCK_TIMEOUT_S):
        rc, salida = publicar_sync.generar()
        if rc != 0:
            return 3, "publicar.py rc=%d, no se reescribe:\n%s" % (rc, salida[-600:])
        sucios = restos(ESPEJO)
        if sucios:
            return 4, "%d fichero(s) con restos tras regenerar; no se reescribe" % len(sucios)
        c = subprocess.run([sys.executable, "-m", "compileall", "-q", ESPEJO], capture_output=True)
        if c.returncode:
            return 4, "el árbol no compila; no se reescribe"
        n = _git("rev-list", "--count", "--all")
        if not ejecutar:
            return 0, "ENSAYO limpio: %s commits se sustituirían por 1. GitHub intacto." % n
        copia = os.path.join(REPO, "_cajita", "espejo-antes-de-reiniciar-%s.bundle" % date.today())
        _git("bundle", "create", copia, "--all")
        os.chmod(copia, 0o600)
        _git("checkout", "-q", "--orphan", "reinicio")
        _git("add", "-A")
        _git("commit", "-q", "-m", "Polaris: árbol público derivado del privado (historial reiniciado)")
        _git("branch", "-M", "reinicio", publicar_sync.RAMA)
        if confirmar("Escribe %s para hacer push --force a GitHub: " % PALABRA).strip() != PALABRA:
            return 1, "no se ha empujado; el espejo local ya es huérfano (copia en %s)" % copia
        _git("push", "--force", "origin", publicar_sync.RAMA)
        _git("reflog", "expire", "--expire=now", "--all")
        _git("gc", "-q", "--prune=now")
        return 0, "historial reiniciado en GitHub (copia local en %s)" % copia


def main(argv=None):
    p = argparse.ArgumentParser(description="Reinicia el historial del espejo público en un commit.")
    p.add_argument("--ejecutar", action="store_true", help="reescribe de verdad (pide confirmación)")
    a = p.parse_args(argv)
    rc, msg = reiniciar(a.ejecutar)
    print(("✅ " if rc == 0 else "🔴 ") + msg)
    return rc


if __name__ == "__main__":
    sys.exit(main())
