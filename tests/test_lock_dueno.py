#!/usr/bin/env python3
"""test_lock_dueno.py — el candado compartido no se le quita a un dueño VIVO.

POR QUÉ EXISTE (25-sep-2026). `_lock.lock` reclamaba por «huérfano» cualquier candado más viejo que
el timeout de quien esperaba, aunque su dueño siguiera trabajando. `publicar_sync` lo sujeta dos
pasadas (~90 s cada una) con 180 s de timeout; bajo carga 70 la corrida siguiente lo borró y las dos
publicaron a la vez sobre el mismo árbol (FileNotFoundError y `rmtree` sobre un directorio no vacío).
Mismo candado que serializa launchd, la fusión a casa base y `seguimiento`.
REPRODUCIDO antes del arreglo: dueño con timeout 1 s y 3 s de trabajo → el intruso entraba a 1,23 s.
"""
import os
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ["BTP_STATE_DIR"] = tempfile.mkdtemp(prefix="lock_dueno_")
import _lock  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _pid_muerto():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _candado_a_mano(nombre, pid=None, edad=0.0):
    d = os.path.join(_lock.LOCKS, nombre + ".lock")
    os.makedirs(d)
    if pid is not None:
        with open(os.path.join(d, _lock.DUENO), "w") as f:
            f.write("%d\n" % pid)
    if edad:
        t = time.time() - edad
        os.utime(d, (t, t))
    return d


def main():
    os.makedirs(_lock.LOCKS, exist_ok=True)

    # 1. El caso del 25-sep: el dueño tarda más que el timeout del que espera.
    log = []

    def dueno():
        with _lock.lock("solape", timeout=0.5):
            log.append("A dentro")
            time.sleep(1.5)
            log.append("A sale")

    def intruso():
        time.sleep(0.1)
        try:
            with _lock.lock("solape", timeout=0.5):
                log.append("B dentro")
        except TimeoutError:
            log.append("B se rinde")

    a, b = threading.Thread(target=dueno), threading.Thread(target=intruso)
    a.start(); b.start(); a.join(); b.join()
    check("dueño vivo más lento que el timeout: el otro NO entra a la vez (%s)" % log,
          log == ["A dentro", "B se rinde", "A sale"])

    # 2. El que espera lo bastante, entra cuando el dueño suelta.
    log.clear()

    def paciente():
        time.sleep(0.1)
        with _lock.lock("solape", timeout=5.0):
            log.append("B dentro")

    a, b = threading.Thread(target=dueno), threading.Thread(target=paciente)
    a.start(); b.start(); a.join(); b.join()
    check("con plazo suficiente entra DESPUÉS del dueño", log == ["A dentro", "A sale", "B dentro"])

    # 3. Dueño muerto: se reclama en el acto, sin esperar el timeout.
    _candado_a_mano("muerto", pid=_pid_muerto())
    t0 = time.time()
    with _lock.lock("muerto", timeout=10.0):
        tardo = time.time() - t0
    check("dueño muerto → se reclama ya (%.2f s)" % tardo, tardo < 1.0)

    # 4. Candado sin dueño declarado (de antes del cambio): regla vieja, solo tras esperar el plazo.
    _candado_a_mano("viejo", edad=5.0)
    t0 = time.time()
    with _lock.lock("viejo", timeout=0.3):
        tardo = time.time() - t0
    check("sin dueño y viejo → se reclama tras el plazo", 0.25 <= tardo < 2.0)
    _candado_a_mano("fresco")
    t0 = time.time()
    with _lock.lock("fresco", timeout=0.3):
        tardo = time.time() - t0
    check("sin dueño y recién tomado → nunca se reclama ANTES del plazo (%.2f s)" % tardo, tardo >= 0.25)

    # 5. Tope duro: con el dueño vivo, pasado TOPE_DURO_S se reclama igual (nunca para siempre).
    _candado_a_mano("colgado", pid=os.getpid(), edad=_lock.TOPE_DURO_S + 60)
    t0 = time.time()
    with _lock.lock("colgado", timeout=10.0):
        tardo = time.time() - t0
    check("dueño vivo pero más viejo que el tope duro → se reclama", tardo < 1.0)

    # 6. Soltar no deja nada, y el dueño queda escrito mientras se sujeta.
    with _lock.lock("limpio", timeout=1.0):
        d = os.path.join(_lock.LOCKS, "limpio.lock")
        dueno_escrito = open(os.path.join(d, _lock.DUENO)).read().split()[0]
    check("mientras se sujeta, el candado declara su PID", dueno_escrito == str(os.getpid()))
    check("al soltar no queda el directorio", not os.path.exists(d))

    # 7. Reclamar no le quita el candado a quien lo tomó entre medias.
    d = _candado_a_mano("carrera", pid=os.getpid())
    _lock._reclamar(d, pid_juzgado=_pid_muerto())
    check("si el dueño ya no es el juzgado muerto, no se toca", os.path.isdir(d))
    _lock._reclamar(d, pid_juzgado=os.getpid())

    print("test_lock_dueno: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
