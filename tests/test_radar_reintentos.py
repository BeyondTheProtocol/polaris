#!/usr/bin/env python3
"""test_radar_reintentos.py — un fallo de red pasajero no deja un tema del radar sin nada ese día.

21-sep-26: 48 avisos de «error de red» en la pasada de las 06:40 (9 el día antes), con temas ALTA
entre ellos, y la misma API respondiendo bien a mediodía. `_get` no reintentaba. El test fija que
un timeout o un 503 se reintentan hasta 3 veces, que un 404 no, y que el aviso lleva el código.
Offline: `urlopen` y `sleep` son falsos.
"""
import io
import os
import sys
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import radar_ned_diario as r  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def con(fallos):
    """urlopen falso: lanza los errores de `fallos` en orden y luego responde {}."""
    n = {"i": 0}

    def urlopen(req, timeout=None):
        n["i"] += 1
        if fallos:
            raise fallos.pop(0)
        return Resp(b'{"ok": 1}')
    return urlopen, n


def http(code):
    return urllib.error.HTTPError("u", code, "x", {}, None)


def main():
    orig = r.urllib.request.urlopen
    dormido = []
    try:
        r.urllib.request.urlopen, n = con([TimeoutError(), http(503)])
        try:
            d = r._get("https://x", {"q": "a"}, _dormir=dormido.append)
        except Exception:
            d = None
        check("timeout + 503 y a la tercera responde", d == {"ok": 1} and n["i"] == 3)
        check("espera entre intentos", dormido == list(r.REINTENTOS))

        r.urllib.request.urlopen, n = con([http(404)])
        try:
            r._get("https://x", {}, _dormir=lambda s: None)
            check("un 404 lanza", False)
        except urllib.error.HTTPError:
            check("un 404 no se reintenta", n["i"] == 1)

        r.urllib.request.urlopen, n = con([TimeoutError(), TimeoutError(), TimeoutError()])
        try:
            r._get("https://x", {}, _dormir=lambda s: None)
            check("tres timeouts acaban lanzando", False)
        except TimeoutError:
            check("tres timeouts: 3 intentos y lanza", n["i"] == 3)
    finally:
        r.urllib.request.urlopen = orig
    import contextlib
    buf = io.StringIO()
    r.urllib.request.urlopen, n = con([http(503)])
    try:
        with contextlib.redirect_stdout(buf):
            r._get("https://www.ebi.ac.uk/x", {}, _dormir=lambda s: None)
    finally:
        r.urllib.request.urlopen = orig
    check("el reintento queda en el log con host y código",
          "reintento 1/" in buf.getvalue() and "www.ebi.ac.uk" in buf.getvalue() and "503" in buf.getvalue())
    check("el aviso lleva el código HTTP", r._motivo_red(http(429)) == "error de red (HTTPError 429)")
    print("test_radar_reintentos: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
