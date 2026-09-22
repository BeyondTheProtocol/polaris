#!/usr/bin/env python3
"""test_oauth_refresh.py — el carril de evidencia se mantiene SOLO, sin login humano (27/7/26).

Nace de dos hallazgos del mismo día:
  · Consensus llevaba 28 días sin responder porque el SDK de MCP, al ver el access token caducado,
    arrancaba un OAuth NUEVO por navegador en vez de usar el refresh token que tenía guardado.
  · scite es peor: su access token dura **15 minutos**, así que sin renovación automática el carril
    solo funcionaba dentro de la misma sesión en la que se hacía el login.

Y de un tropiezo mío que también hay que codificar: el refresh token es de **un solo uso con
rotación**. Una prueba «de solo lectura» que lo canjea y tira el resultado DESTRUYE el acceso. Por
eso `renovar()` guarda siempre el token nuevo, y hay un caso que lo comprueba.

Sin red: se levanta un servidor OAuth de mentira en localhost.
Estilo test_ia/test_borde: cada caso suma OK/fallo; exit = nº de fallos.
"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import _oauth_refresh as R      # noqa: E402

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# ── Servidor OAuth de mentira ───────────────────────────────────────────────────────────
class _Estado:
    validos = {"refresh-1"}
    emitidos = 0
    ultimo_body = ""


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/.well-known/"):
            cuerpo = json.dumps({"token_endpoint": "http://127.0.0.1:%d/token" % self.server.server_port,
                                 "grant_types_supported": ["authorization_code", "refresh_token"]})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(cuerpo.encode())
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode()
        _Estado.ultimo_body = body
        rt = ""
        for par in body.split("&"):
            if par.startswith("refresh_token="):
                rt = par.split("=", 1)[1]
        if rt not in _Estado.validos:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"detail":"invalid_grant: expired refresh token"}')
            return
        _Estado.emitidos += 1
        nuevo = "refresh-%d" % (_Estado.emitidos + 1)
        _Estado.validos = {nuevo}                    # ROTACIÓN: el viejo deja de valer
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"access_token": "acc-%d" % _Estado.emitidos,
                                     "refresh_token": nuevo, "expires_in": 900,
                                     "token_type": "Bearer", "scope": "search"}).encode())


def _arranca():
    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/mcp" % srv.server_port


def _escribe(state_dir, tok, edad_seg=0):
    os.makedirs(state_dir, exist_ok=True)
    ruta = os.path.join(state_dir, "tokens.json")
    json.dump(tok, open(ruta, "w"))
    if edad_seg:
        t = time.time() - edad_seg
        os.utime(ruta, (t, t))
    json.dump({"client_id": "cli-1"}, open(os.path.join(state_dir, "client.json"), "w"))
    return ruta


def main():
    srv, server_url = _arranca()
    tmp = tempfile.mkdtemp(prefix="oauthref_")

    # 1 · token fresco → no se toca nada (ni un round-trip de más)
    d1 = os.path.join(tmp, "fresco")
    _escribe(d1, {"access_token": "a", "refresh_token": "refresh-1", "expires_in": 900})
    est, det = R.renovar(d1, server_url)
    ok(est == "vigente", "token fresco → 'vigente' (no renueva por renovar)")

    # 2 · token caducado con refresh → renueva y GUARDA el nuevo par
    d2 = os.path.join(tmp, "caducado")
    ruta = _escribe(d2, {"access_token": "viejo", "refresh_token": "refresh-1", "expires_in": 900},
                    edad_seg=28 * 86400)
    est, det = R.renovar(d2, server_url)
    ok(est == "renovado", "token caducado con refresh → renueva sin humano (%s)" % est)
    guardado = json.load(open(ruta))
    ok(guardado.get("access_token") == "acc-1", "el access token NUEVO queda guardado")
    ok(guardado.get("refresh_token") == "refresh-2",
       "la ROTACIÓN se guarda: si se tira el refresh nuevo, se destruye el acceso")
    ok("client_id=cli-1" in _Estado.ultimo_body, "manda el client_id que tiene cacheado")

    # 3 · y a la siguiente, el token nuevo vale (no se quedó a medias)
    est, det = R.renovar(d2, server_url)
    ok(est == "vigente", "tras renovar, la siguiente llamada ya lo ve vigente")
    est, det = R.renovar(d2, server_url, forzar=True)
    ok(est == "renovado", "forzar=True renueva aunque quede tiempo (encadena rotaciones)")

    # 4 · refresh muerto → se dice que hace falta el humano, sin colgarse
    d4 = os.path.join(tmp, "muerto")
    _escribe(d4, {"access_token": "x", "refresh_token": "ya-no-vale", "expires_in": 900},
             edad_seg=86400)
    est, det = R.renovar(d4, server_url)
    ok(est == "sin_refresh", "refresh rechazado → 'sin_refresh' (ahí SÍ hace falta login)")
    ok("login" in det, "y lo dice con el paso exacto")

    # 5 · sin refresh token guardado
    d5 = os.path.join(tmp, "sinrt")
    _escribe(d5, {"access_token": "x", "expires_in": 900}, edad_seg=86400)
    ok(R.renovar(d5, server_url)[0] == "sin_refresh", "sin refresh token → 'sin_refresh'")
    ok(not R.renovable(d5), "renovable() dice que no")

    # 6 · sin token ninguno → no revienta
    d6 = os.path.join(tmp, "vacio")
    os.makedirs(d6, exist_ok=True)
    ok(R.renovar(d6, server_url)[0] == "sin_token", "sin token → 'sin_token', sin excepción")

    # 7 · servidor inalcanzable → 'fallo', nunca una excepción hacia el llamante
    d7 = os.path.join(tmp, "caido")
    _escribe(d7, {"access_token": "x", "refresh_token": "refresh-1", "expires_in": 900},
             edad_seg=86400)
    ok(R.renovar(d7, "http://127.0.0.1:9/mcp")[0] == "fallo",
       "servidor caído → 'fallo' fail-soft (el llamante decide)")

    # 8 · permisos: el token no queda legible por otros
    modo = os.stat(os.path.join(d2, "tokens.json")).st_mode & 0o777
    ok(modo == 0o600, "el token renovado se guarda 0600 (era %o)" % modo)

    srv.shutdown()
    print("RESULTADO oauth refresh: %d OK, %d fallos" % (_pass, _fail))
    if not _fail:
        print("✅ EL CARRIL DE EVIDENCIA SE MANTIENE SOLO")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
