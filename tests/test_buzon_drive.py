#!/usr/bin/env python3
"""test_buzon_drive.py — buzón de subidas a Drive (17-oct-26). Sin red ni Drive real:
  · encolar no toca Drive; fuera de raíces o > 200 MB no se encola.
  · vaciar sube UNA por vuelta, mueve a subidos/ y avisa con el enlace.
  · fallo de subida: reintenta y a los 3 intentos a fallidos/.
  · HALT: no sube nada.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import salida  # noqa: E402
import buzon_drive  # noqa: E402

_pass = 0
_fail = 0
_subidas = []
_avisos = []
_ok = [True]


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def fake_subir(ruta, carpeta):
    _subidas.append((ruta, carpeta))
    if not _ok[0]:
        raise RuntimeError("drive caído (mock)")
    return {"id": "X1", "name": os.path.basename(ruta), "webViewLink": "https://drive/X1"}


def fake_avisar(texto):
    _avisos.append(texto)


def n(sub):
    d = buzon_drive._dir(sub)
    return len([x for x in os.listdir(d) if x.endswith(".json")]) if os.path.isdir(d) else 0


def main():
    tmp = tempfile.mkdtemp(prefix="test_buzon_drive_")
    salida.STATE = os.path.join(tmp, "state")
    salida.OUTBOX = os.path.join(tmp, "outbox")
    salida.HALT_FILES = (os.path.join(tmp, ".btp.HALT"), os.path.join(tmp, ".HALT"))
    raiz = os.path.join(tmp, "fuente")
    os.makedirs(raiz)
    buzon_drive.RAICES = (raiz,)

    a = os.path.join(raiz, "a.mp4")
    b = os.path.join(raiz, "b.mp4")
    fuera = os.path.join(tmp, "fuera.mp4")
    for f in (a, b, fuera):
        with open(f, "wb") as fh:
            fh.write(b"\x00" * 32)

    check("encola a", buzon_drive.encolar(a)["encolado"])
    check("encola b con carpeta", buzon_drive.encolar(b, "CARP")["encolado"])
    check("fuera no encola", not buzon_drive.encolar(fuera)["encolado"])
    buzon_drive.MAX_BYTES = 10
    check("grande no encola", not buzon_drive.encolar(a)["encolado"])
    buzon_drive.MAX_BYTES = 200 * 1024 * 1024
    check("2 pendientes, nada subido", n("pendientes") == 2 and _subidas == [])

    open(salida.HALT_FILES[0], "w").close()
    check("HALT no sube", buzon_drive.vaciar(fake_subir, fake_avisar) == 0 and _subidas == [])
    os.remove(salida.HALT_FILES[0])

    check("vuelta 1 sube una", buzon_drive.vaciar(fake_subir, fake_avisar) == 1 and len(_subidas) == 1)
    check("vuelta 2 sube la otra", buzon_drive.vaciar(fake_subir, fake_avisar) == 1 and len(_subidas) == 2)
    check("carpeta respetada", ("CARP" in [c for _, c in _subidas]))
    check("2 subidos, 0 pendientes", n("subidos") == 2 and n("pendientes") == 0)
    check("avisa con enlace", len(_avisos) == 2 and "https://drive/X1" in _avisos[0])

    buzon_drive.encolar(a)
    _ok[0] = False
    for _ in range(3):
        buzon_drive.vaciar(fake_subir, fake_avisar)
    check("tras 3 fallos a fallidos", n("pendientes") == 0 and n("fallidos") == 1)

    print("test_buzon_drive: %d ok, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
