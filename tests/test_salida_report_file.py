#!/usr/bin/env python3
"""test_salida_report_file.py — CLI `salida.py report-file` (17-oct-26).

El lazo no puede usar `python3 -c` (lo veta el muro), así que mandar un fichero a {{TITULAR}}
necesita un subcomando. Verifica, sin red:
  · report-file-dry con fichero real → dry, no toca el canal, rc 0.
  · report-file con fichero real → entrega SOLO al chat propio (mock), rc 0.
  · ruta inexistente → bloqueado, rc 1, sin entrega.
  · un tercer argumento de más (intento de pasar destino) → uso, rc 2, sin entrega.
  · sin argumentos → uso, rc 2.
  · HALT → bloqueado, sin entrega.
"""
import io
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


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def fake_doc(chat_id, path, caption=""):
    _docs.append((chat_id, path, caption))
    return True, "mock"


def run(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = salida.main(argv)
    return rc, buf.getvalue()


def main():
    tmp = tempfile.mkdtemp(prefix="test_salida_rf_")
    salida.OUTBOX = os.path.join(tmp, "outbox")
    salida.PENDING = os.path.join(salida.OUTBOX, "pending")
    salida.STATE = tmp
    salida.NOTIF_CFG = os.path.join(tmp, "notif", "config.json")
    salida.HALT_FILES = (os.path.join(tmp, ".btp.HALT"), os.path.join(tmp, ".HALT"))
    salida._self_chatid = lambda: SELF
    salida._deliver_telegram_document = fake_doc

    f = os.path.join(tmp, "clip.mp4")
    with open(f, "wb") as fh:
        fh.write(b"\x00" * 64)

    _docs.clear()
    rc, out = run(["report-file-dry", f, "pie"])
    check("dry rc 0", rc == 0)
    check("dry no toca el canal", _docs == [])
    check("dry lo dice", '"dry": true' in out)

    _docs.clear()
    rc, out = run(["report-file", f, "pie de prueba"])
    check("entrega rc 0", rc == 0)
    check("una sola entrega", len(_docs) == 1)
    check("solo al chat propio", _docs and _docs[0][0] == SELF)
    check("es el fichero pedido", _docs and _docs[0][1] == f)

    _docs.clear()
    rc, out = run(["report-file", os.path.join(tmp, "no-existe.mp4")])
    check("inexistente rc 1", rc == 1)
    check("inexistente sin entrega", _docs == [])
    check("inexistente bloqueado", '"blocked": true' in out)

    _docs.clear()
    rc, out = run(["report-file", f, "pie", "555000222"])
    check("destino extra rc 2", rc == 2)
    check("destino extra sin entrega", _docs == [])

    rc, out = run(["report-file"])
    check("sin args rc 2", rc == 2)

    _docs.clear()
    open(salida.HALT_FILES[0], "w").close()
    rc, out = run(["report-file", f])
    check("HALT sin entrega", _docs == [])
    check("HALT rc 1", rc == 1)
    os.remove(salida.HALT_FILES[0])

    print("test_salida_report_file: %d ok, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
