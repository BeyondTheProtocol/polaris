#!/usr/bin/env python3
"""test_correo_outbox_hilo.py — el borrador con adjuntos va DENTRO del hilo, y el Llavero cerrado se nombra.

POR QUÉ EXISTE (17-oct-2026). Al adjuntar 3 imágenes a la respuesta al HuffPost fallaron dos cosas de
`tools/correo_outbox.py`:
  1. `construir_mime` no ponía In-Reply-To ni References: el borrador salía como hilo NUEVO;
  2. con el login.keychain bloqueado (rc=36) `status` decía «falta en el Llavero» cuando la clave
     estaba: mandaba a buscar una credencial en vez de pedir el desbloqueo.

Lo que se fija (sin red, sin Llavero de verdad):
  · `--reply-to-message-id` pone In-Reply-To y References (con la cadena previa si se da);
  · el id del hilo de la web (`1a139c…`) se rechaza: no es un Message-ID y no ataría el hilo;
  · CR/LF en el Message-ID se rechaza (inyección de cabeceras);
  · sin el flag, nada cambia (hilo nuevo, sin cabeceras);
  · `_secrets.estado` distingue ok / bloqueado (rc=36 con la clave presente) / falta, y el motivo
    de `FaltaClave` dice por qué (sesión SSH) y cuál es la salida (broker; para adjuntar, Chrome).
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import _secrets  # noqa: E402
import correo_outbox as co  # noqa: E402

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def main():
    adj = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    adj.write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    adj.close()

    padre = "<CAJx1@mail.example>"
    msg, r = co.construir_mime("m@cuenta.example", ["a@b.example"], [], "Re: Entrevista", "hola",
                               [adj.name], reply_to=padre)
    ok(msg["In-Reply-To"] == padre, "In-Reply-To = el padre")
    ok(msg["References"] == padre, "References = el padre si no hay cadena")
    ok(r["in_reply_to"] == padre, "el resumen dice que va en hilo")
    ok(any(p.get_filename() == os.path.basename(adj.name) for p in msg.iter_attachments()),
       "el adjunto sigue ahí")

    msg, _ = co.construir_mime("m@cuenta.example", ["a@b.example"], [], "Re: x", "hola", [],
                               reply_to="CAJx1@mail.example", references="<r0@x.example> <r1@y.example>")
    ok(msg["In-Reply-To"] == padre, "sin ángulos se le ponen")
    ok(msg["References"] == "<r0@x.example> <r1@y.example> " + padre, "la cadena previa + el padre al final")

    msg, _ = co.construir_mime("m@cuenta.example", ["a@b.example"], [], "Re: x", "hola", [],
                               reply_to=padre, references="<r0@x.example> " + padre)
    ok(msg["References"] == "<r0@x.example> " + padre, "el padre no se duplica si ya va en la cadena")

    msg, r = co.construir_mime("m@cuenta.example", ["a@b.example"], [], "Nuevo", "hola", [])
    ok(msg["In-Reply-To"] is None and msg["References"] is None, "sin flag: hilo nuevo, sin cabeceras")
    ok(r["in_reply_to"] is None, "sin flag: el resumen lo dice")

    mk = lambda **k: co.construir_mime("m@cuenta.example", ["a@b.example"], [], "Re: x", "b", [], **k)  # noqa: E731
    ok(_raises(lambda: mk(reply_to="1a139c245a646593")), "el id de hilo de la web se rechaza")
    ok(_raises(lambda: mk(reply_to="<a@b.example>\r\nBcc: x@evil.example")), "CR/LF en el Message-ID se rechaza")
    ok(_raises(lambda: mk(reply_to=padre, references="<ok@x.example> <mal\n@y.example>")), "CR/LF en References se rechaza")
    ok(_raises(lambda: mk(references="<r0@x.example>")), "References sin padre se rechaza")

    a = co._parse(["--to", "a@b.example", "--reply-to-message-id", padre, "--references", "<r0@x.example>",
                   "--body-file", "b.txt"])
    ok(a["reply_to"] == padre and a["references"] == "<r0@x.example>", "el CLI lee los dos flags")

    # Llavero: rc=36 en `-w` con la clave presente = bloqueado; rc=44 en ambas = falta.
    real = subprocess.run

    def falso(rc_w, rc_meta):
        def run(cmd, **k):
            return subprocess.CompletedProcess(cmd, rc_w if "-w" in cmd else rc_meta, "", "")
        return run

    try:
        subprocess.run = falso(36, 0)
        ok(_secrets.estado("btp-x") == "bloqueado", "rc=36 con la clave presente → bloqueado")
        m = co._sin_clave("btp-x", "m@cuenta.example")
        ok("BLOQUEADO" in m and _secrets.DESBLOQUEO in m, "el motivo dice bloqueado y la salida, no «busca la clave»")
        subprocess.run = falso(44, 44)
        ok(_secrets.estado("btp-x") == "falta", "rc=44 → falta")
        ok("falta la App Password" in co._sin_clave("btp-x", "m@cuenta.example"), "si falta, dice que falta")
    finally:
        subprocess.run = real

    os.unlink(adj.name)
    print("RESULTADO correo_outbox hilo: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
