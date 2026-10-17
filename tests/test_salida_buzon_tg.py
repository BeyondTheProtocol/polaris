#!/usr/bin/env python3
"""test_salida_buzon_tg.py — buzón de ficheros a Telegram (17-oct-26).

Una sesión sin Llavero deja el fichero en el buzón y el bot lo vacía. Sin red:
  · sin chat_id → report_file_to_titular encola (no envía) y la CLI sale con 0.
  · fuera de las raíces, tipo raro o > 50 MB → no se encola.
  · vaciar sin chat_id → no hace nada; con chat_id → manda SOLO al chat propio y mueve a enviados/.
  · fallo de entrega → reintenta y a los 3 intentos pasa a fallidos/.
  · HALT → vaciar no manda nada.
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import salida  # noqa: E402

SELF = "999000111"
_pass = 0
_fail = 0
_docs = []
_ok_entrega = [True]


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def fake_doc(chat_id, path, caption=""):
    _docs.append((chat_id, path, caption))
    return (True, "mock") if _ok_entrega[0] else (False, "fallo mock")


def pend():
    d = salida._buzon_dir()
    return sorted(n for n in os.listdir(d) if n.endswith(".json")) if os.path.isdir(d) else []


def main():
    tmp = tempfile.mkdtemp(prefix="test_buzon_tg_")
    salida.OUTBOX = os.path.join(tmp, "outbox")
    salida.PENDING = os.path.join(salida.OUTBOX, "pending")
    salida.STATE = os.path.join(tmp, "state")
    salida.NOTIF_CFG = os.path.join(tmp, "notif", "config.json")
    salida.HALT_FILES = (os.path.join(tmp, ".btp.HALT"), os.path.join(tmp, ".HALT"))
    salida._deliver_telegram_document = fake_doc
    raiz = os.path.join(tmp, "fuente")
    os.makedirs(raiz)
    salida.BUZON_RAICES = (raiz,)

    vid = os.path.join(raiz, "clip.mp4")
    with open(vid, "wb") as fh:
        fh.write(b"\x00" * 64)
    fuera = os.path.join(tmp, "fuera.mp4")
    with open(fuera, "wb") as fh:
        fh.write(b"\x00" * 64)
    raro = os.path.join(raiz, "x.exe")
    with open(raro, "wb") as fh:
        fh.write(b"\x00" * 64)

    # 1. Sin Llavero: encola, no envía.
    salida._self_chatid = lambda: None
    _docs.clear()
    r = salida.report_file_to_titular(vid, caption="pie")
    check("sin chat_id encola", r.get("encolado") is True)
    check("sin chat_id no envía", _docs == [] and not r["delivered"])
    check("queda 1 pendiente", len(pend()) == 1)

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = salida.main(["report-file", vid, "pie cli"])
    check("CLI encolado rc 0", rc == 0)
    check("quedan 2 pendientes", len(pend()) == 2)

    # 2. Frenos de encolado.
    check("fuera de raíz no encola", salida.encolar_fichero(fuera)["blocked"])
    check("tipo raro no encola", salida.encolar_fichero(raro)["blocked"])
    salida.BUZON_MAX_BYTES = 10
    check("grande no encola", salida.encolar_fichero(vid)["blocked"])
    salida.BUZON_MAX_BYTES = 50 * 1024 * 1024
    check("siguen 2 pendientes", len(pend()) == 2)

    # 3. Vaciar sin chat_id: nada.
    check("vaciar sin chat_id no hace nada", salida.vaciar_buzon_ficheros() == 0 and _docs == [])

    # 4. HALT: nada.
    salida._self_chatid = lambda: SELF
    open(salida.HALT_FILES[0], "w").close()
    check("vaciar con HALT no manda", salida.vaciar_buzon_ficheros() == 0 and _docs == [])
    os.remove(salida.HALT_FILES[0])

    # 5. Vaciar con chat_id: manda al chat propio y mueve a enviados.
    n = salida.vaciar_buzon_ficheros()
    check("vacía los 2", n == 2)
    check("solo al chat propio", len(_docs) == 2 and all(c == SELF for c, _, _ in _docs))
    check("pendientes vacío", pend() == [])
    check("2 en enviados", len(os.listdir(salida._buzon_dir("enviados"))) == 2)

    # 6. Fallo de entrega: 3 intentos y a fallidos.
    salida.encolar_fichero(vid, "falla")
    _ok_entrega[0] = False
    for _ in range(3):
        salida.vaciar_buzon_ficheros()
    check("tras 3 fallos no queda pendiente", pend() == [])
    fall = os.listdir(salida._buzon_dir("fallidos"))
    check("1 en fallidos", len(fall) == 1)
    with open(os.path.join(salida._buzon_dir("fallidos"), fall[0]), encoding="utf-8") as f:
        check("lleva 3 intentos", json.load(f).get("intentos") == 3)

    print("test_salida_buzon_tg: %d ok, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
