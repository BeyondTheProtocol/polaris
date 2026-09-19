#!/usr/bin/env python3
"""tools/git_mutex.py — CANDADO ÚNICO para TODA mutación del `.git` de la CASA BASE.

Origen (A1, comité de arquitectura, 10-jul-26 — hallazgo B): `cerrar_sesion.py` solo tomaba
el candado `_lock.py` alrededor del `git merge`, NO alrededor de la poda del worktree
(`git worktree remove`, dos bloques después); `tools/ramas.py` (`limpia`/`limpia --si`) hace
`git worktree prune` y `git worktree remove` SIN candado alguno; y el agente `git`/rutina
`git-barrido` (charter, shell libre) también muta ramas/worktrees por Bash sin pasar por
ningún candado. Resultado: al menos tres actores mutando el MISMO `.git/worktrees/` y las
refs de la casa base con SOLO una de las operaciones de UNO de los tres protegida — el hueco
concreto que separa "loop stateless con checks idempotentes" de una carrera real entre dos
mutaciones idempotentes que se pisan sobre el mismo recurso no versionado.

Este módulo cierra el hueco para los actores en PYTHON (cerrar_sesion.py, ramas.py): en vez
de invocar `git` a pelo para una mutación de la casa base, invocan `git_mutex.run([...])`, que
envuelve la llamada real en el candado compartido `_lock.py` con nombre fijo "git-mutex" (el
MISMO nombre para TODOS los llamantes → serializa entre sí, no solo consigo mismo). Reentrante
en el sentido de _lock.py: si el candado no se puede tomar en `timeout` s, lanza (fail-closed;
mejor fallar visible que mutar sin candado).

El agente `git`/`git-barrido` invoca `git` DIRECTAMENTE por Bash (es un charter en lenguaje
natural, no un script) — este módulo no lo intercepta (ver limitación en el propio A1: mover
esa vía a `git_mutex.sh`/allowlist del muro es un cambio de PRODUCCIÓN sobre el charter y el
muro, fuera del alcance de "solo tools/" de este parche; queda como riesgo residual documentado).

Uso (en vez de `git -C <repo> <args...>` para una mutación de la casa base):
  python3 tools/git_mutex.py -C <repo> worktree remove <path>
  python3 tools/git_mutex.py -C <repo> worktree prune
  python3 tools/git_mutex.py -C <repo> branch -d <rama>
  python3 tools/git_mutex.py -C <repo> merge --no-ff <rama> -m "..."

O desde Python: `import git_mutex; rc, out, err = git_mutex.run(["-C", repo, "worktree", "remove", wt])`.

Stdlib puro. Mismo patrón que `_lock.py`/`cerrar_sesion.py`.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _lock  # noqa: E402

NOMBRE = "git-mutex"
TIMEOUT = float(os.environ.get("BTP_GIT_MUTEX_TIMEOUT", "60"))


def run(args, timeout=None, check_output_timeout=120):
    """Ejecuta `git <args>` bajo el candado compartido "git-mutex". Devuelve (rc, stdout, stderr).

    Lanza TimeoutError si no se puede tomar el candado en `timeout` s (otro actor lo tiene) —
    fail-closed: el llamante debe tratarlo como "aplázalo", NUNCA como "mútalo sin candado"."""
    with _lock.lock(NOMBRE, timeout=timeout if timeout is not None else TIMEOUT):
        p = subprocess.run(["git"] + list(args), capture_output=True, text=True,
                            timeout=check_output_timeout)
        return p.returncode, p.stdout, p.stderr


def main(argv):
    if not argv:
        sys.stderr.write("uso: git_mutex.py <args de git...>\n")
        return 2
    try:
        rc, out, err = run(argv)
    except TimeoutError as e:
        sys.stderr.write("git_mutex: no pude tomar el candado %r (otro actor muta la casa base ahora mismo): %s\n"
                          % (NOMBRE, e))
        return 75  # EX_TEMPFAIL — aplázalo; nunca lo sortees sin candado.
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
