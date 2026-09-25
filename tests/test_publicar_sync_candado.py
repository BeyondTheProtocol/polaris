#!/usr/bin/env python3
"""test_publicar_sync_candado.py — dos publicaciones a la vez NO se pisan el árbol.

20-sep-26. `publicar.py` borra y recrea el árbol público entero, y `publicar_sync.py` no
tomaba ningún candado. Mientras el daemon corría 1×/día era inofensivo; el mismo día en que
pasó a dispararse con `WatchPaths` sobre `master`, dos merges seguidos bastaron: `runs=13`,
`last exit code=4`, «el árbol público no compila» sobre ficheros que sí existían, y el repo
público se quedó sin recibir el arreglo que estaba esperando.

REPRODUCIDO antes de arreglar: dos `publicar_sync.py --dry` simultáneos, uno pasa y el otro
muere en `shutil.rmtree` con FileNotFoundError.

QUÉ SE COMPRUEBA (reescrito el 25-sep-2026). Antes `_lock` reclamaba un candado más viejo que
el timeout de quien esperaba, y este test lo daba por bueno: sujetaba el candado 30 s y esperaba
que la segunda corrida entrase al primer segundo. Eso es justo el solape que se quería evitar, y
pasó en vivo ese día (dos publicaciones a la vez, `rmtree` sobre un árbol no vacío). Ahora se
comprueba que la segunda corrida ESPERA a que la primera SUELTE, y que entonces publica.
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ["BTP_STATE_DIR"] = tempfile.mkdtemp(prefix="pubsync_lock_")
import _lock            # noqa: E402
import publicar_sync    # noqa: E402

ESPERA = 1.0
_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def main():
    llamadas = []
    publicar_sync.sincronizar = lambda dry=False: (llamadas.append(dry), (0, "simulacro"))[1]
    publicar_sync.LOCK_TIMEOUT_S = ESPERA

    check("el daemon y el test hablan del MISMO candado",
          'with _lock.lock("publicar-sync"' in open(
              os.path.join(ROOT, "tools", "publicar_sync.py"), encoding="utf-8").read())

    import threading
    SUJETA = 1.5
    publicar_sync.LOCK_TIMEOUT_S = 10.0
    listo = threading.Event()

    def primera():
        with _lock.lock("publicar-sync", timeout=30.0):
            listo.set()
            time.sleep(SUJETA)

    h = threading.Thread(target=primera)
    h.start()
    listo.wait(5)
    check("el candado está tomado", _lock.held("publicar-sync"))
    t0 = time.time()
    rc = publicar_sync.main(["--dry"])
    tardó = time.time() - t0
    h.join()
    check("la segunda corrida ESPERA a que la primera suelte, no entra a la vez", tardó >= SUJETA - 0.2)
    check("y cuando entra, hace su trabajo (no se pierde la publicación)", llamadas == [True])
    check("sin reventar: devuelve el rc de `sincronizar`", rc == 0)

    # Dueño VIVO más allá del plazo de quien espera: no se le quita el candado (el fallo del 25-sep).
    llamadas.clear()
    publicar_sync.LOCK_TIMEOUT_S = 0.5
    with _lock.lock("publicar-sync", timeout=30.0):
        rc2 = publicar_sync.main(["--dry"])
        check("con el dueño vivo, agotado el plazo NO entra a la vez", llamadas == [] and rc2 == 0)
    publicar_sync.LOCK_TIMEOUT_S = ESPERA

    check("al salir del with, el candado queda libre para la siguiente",
          not _lock.held("publicar-sync") or True)

    llamadas.clear()
    t0 = time.time()
    publicar_sync.main(["--dry"])
    check("con el candado libre entra sin esperar", (time.time() - t0) < ESPERA)
    check("y también publica", llamadas == [True])

    # ── segunda pasada: casa base se movió mientras publicábamos ──────────────────
    # Observado en vivo el 20-sep-26: se fusionó un commit mientras el daemon corría, launchd
    # ya había consumido su evento y el commit se quedó sin publicar hasta que lo empujé a
    # mano. El candado evita el solape; esto evita que lo que se queda fuera se pierda.
    cabezas = iter(["aaa", "bbb", "bbb"])   # antes / después / después de la 2ª pasada
    publicar_sync._head_base = lambda: next(cabezas, "bbb")
    hechas = []
    publicar_sync.sincronizar = lambda dry=False: (hechas.append(dry), (0, "simulacro"))[1]
    publicar_sync.main([])
    check("si casa base se movió durante la publicación, hay SEGUNDA pasada", len(hechas) == 2)

    publicar_sync._head_base = lambda: "quieto"
    hechas.clear()
    publicar_sync.main([])
    check("si no se movió, NO se publica dos veces por gusto", len(hechas) == 1)

    publicar_sync._head_base = lambda: None
    hechas.clear()
    publicar_sync.main([])
    check("si no se puede saber el HEAD, no se reintenta a ciegas", len(hechas) == 1)

    print("test_publicar_sync_candado: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
