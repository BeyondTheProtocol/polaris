#!/usr/bin/env python3
"""tools/_lock.py — candado repo-wide para SERIALIZAR escrituras a ficheros COMPARTIDOS
(registro de comités, CLAUDE.md, índice de la constelación, índice de kb). Lo usan los
scripts que mutan lo compartido (p.ej. un registrar de cajas) y el lazo, para que el
constructor y la rutina diaria `auto-mejora` no se pisen (H5, Fase 0/2).

Lockdir atómico (`os.mkdir` es atómico en POSIX), timeout FAIL-CLOSED (si no se puede
tomar, lanza en vez de escribir a ciegas), y reclamo de lock huérfano (proceso muerto: el
candado guarda el PID de su dueño desde el 25-sep-2026; antes se juzgaba por antigüedad).
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


# Tope duro (25-sep-2026): pasado esto, un candado se reclama aunque su dueño siga vivo. Es el
# «nunca bloquea para siempre» del diseño original, pero a una escala que ninguna corrida sana
# alcanza (publicar dos pasadas bajo carga ronda los 5 min; `bucles_colgados` mata a los 45).
TOPE_DURO_S = 1800.0
DUENO = "dueno"


def _dueno_vivo(d):
    """True/False si el candado declara su PID y ese proceso vive o no; None si no lo declara
    (candado de antes del 25-sep, o tomado hace microsegundos y aún sin escribir)."""
    try:
        with open(os.path.join(d, DUENO), encoding="utf-8") as f:
            pid = int(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None, None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False, pid
    except PermissionError:
        return True, pid              # existe, pero es de otro usuario
    except OSError:
        return None, pid
    return True, pid


def _huerfano(d, timeout, espero_ya):
    """¿Se puede reclamar el candado `d`? (reclamar, pid_juzgado)

    25-sep-2026: antes bastaba con que el candado fuera más viejo que el `timeout` DEL QUE ESPERA,
    aunque su dueño siguiera trabajando. `publicar_sync` lo sujeta dos pasadas (~90 s cada una)
    con 180 s de timeout; bajo carga 70 la corrida siguiente lo borró y publicó a la vez: el
    árbol del espejo reventó con FileNotFoundError y `rmtree` sobre un directorio no vacío.
    Ahora manda el DUEÑO: muerto → se reclama ya; vivo → se respeta hasta TOPE_DURO_S."""
    try:
        edad = time.time() - os.path.getmtime(d)
    except FileNotFoundError:
        return False, None
    if edad > max(TOPE_DURO_S, timeout):
        return True, None
    vivo, pid = _dueno_vivo(d)
    if vivo is False:
        return True, pid
    if vivo is None:
        return espero_ya and edad > timeout, None   # sin dueño declarado: la regla de siempre
    return False, pid


def _reclamar(d, pid_juzgado):
    """Quita un candado huérfano. Si al ir a quitarlo ya es de OTRO dueño (lo reclamó y tomó
    alguien entre medias), no se toca."""
    if pid_juzgado is not None:
        _, pid_ahora = _dueno_vivo(d)
        if pid_ahora != pid_juzgado:
            return
    try:
        os.remove(os.path.join(d, DUENO))
    except OSError:
        pass
    try:
        os.rmdir(d)
    except OSError:
        pass


@contextlib.contextmanager
def lock(nombre="constelacion", timeout=30.0):
    """Exclusión mutua por `nombre`. Espera hasta `timeout`; si el dueño del candado ha muerto
    lo reclama (en el acto); si sigue vivo, espera, y agotado el plazo → TimeoutError. Solo un
    candado más viejo que TOPE_DURO_S se reclama con el dueño vivo."""
    os.makedirs(LOCKS, mode=0o700, exist_ok=True)
    d = os.path.join(LOCKS, nombre + ".lock")
    t0 = time.time()
    while True:
        try:
            os.mkdir(d)
            break
        except FileExistsError:
            reclamar, pid = _huerfano(d, timeout, time.time() - t0 > timeout)
            if reclamar:
                _reclamar(d, pid)
                continue
            if time.time() - t0 > timeout:
                raise TimeoutError("no pude tomar el candado %r (otro proceso lo tiene)" % nombre)
            time.sleep(0.05)
    try:
        with open(os.path.join(d, DUENO), "w", encoding="utf-8") as f:
            f.write("%d\n" % os.getpid())
    except OSError:
        pass                          # sin dueño declarado vale la regla vieja: no es peor que antes
    try:
        yield
    finally:
        try:
            os.remove(os.path.join(d, DUENO))
        except OSError:
            pass
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
