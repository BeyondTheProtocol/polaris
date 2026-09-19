#!/usr/bin/env python3
"""tools/_lock.py — candado repo-wide para SERIALIZAR escrituras a ficheros COMPARTIDOS
(registro de comités, CLAUDE.md, índice de la constelación, índice de kb). Lo usan los
scripts que mutan lo compartido (p.ej. un registrar de cajas) y el lazo, para que el
constructor y la rutina diaria `auto-mejora` no se pisen (H5, Fase 0/2).

Lockdir atómico (`os.mkdir` es atómico en POSIX), timeout FAIL-CLOSED (si no se puede
tomar, lanza en vez de escribir a ciegas), y reclamo de lock huérfano (proceso muerto).
Stdlib puro. El estado vive en tools/state/locks/ (lo escribe python, no la tool Write,
así el muro no lo frena; y NO es tools/state/cost/, que sí está protegido).

Nota de uso: un AGENTE (Claude) no puede sostener este context manager entre tool-calls;
por eso la serialización a nivel de agente se hace con un SCRIPT que toma el candado
mientras muta (constructor → helper de registro), o por el despachador único del lazo.
"""
import contextlib
import os
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCKS = os.path.join(os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state"), "locks")


@contextlib.contextmanager
def lock(nombre="constelacion", timeout=30.0):
    """Exclusión mutua por `nombre`. Espera hasta `timeout`; si no puede y el lock es
    huérfano (mtime más viejo que timeout) lo reclama; si ni eso → TimeoutError."""
    os.makedirs(LOCKS, mode=0o700, exist_ok=True)
    d = os.path.join(LOCKS, nombre + ".lock")
    t0 = time.time()
    while True:
        try:
            os.mkdir(d)
            break
        except FileExistsError:
            if time.time() - t0 > timeout:
                try:
                    if time.time() - os.path.getmtime(d) > timeout:
                        os.rmdir(d)
                        continue
                except FileNotFoundError:
                    continue
                raise TimeoutError("no pude tomar el candado %r (otro proceso lo tiene)" % nombre)
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            os.rmdir(d)
        except OSError:
            pass


def held(nombre="constelacion"):
    """True si el candado existe ahora mismo (informativo, no toma el lock)."""
    return os.path.isdir(os.path.join(LOCKS, nombre + ".lock"))


if __name__ == "__main__":
    # Auto-test mínimo: tomar, comprobar held, soltar, reentrada.
    import sys
    with lock("selftest", timeout=2):
        assert held("selftest"), "el candado debería estar tomado"
    assert not held("selftest"), "el candado debería estar libre"
    with lock("selftest", timeout=2):
        pass
    print("✅ _lock OK (toma/suelta/reentra)")
    sys.exit(0)
