#!/usr/bin/env python3
"""test_salida_reintento.py — un hipo de DNS no puede tragarse un aviso, y un reintento no puede
duplicarlo.

POR QUÉ EXISTE (12-sep-2026). El daemon `enviar-hoy` llevaba 1266 detecciones de fallo en el libro
de deuda, y ese mismo día otros dos (`calendar-sync`, `anatomia-push`) fallaron en la MISMA ventana
horaria por resolución DNS. No eran tres bugs: era la red yéndose y volviendo (la caja corre
Tailscale y NordVPN a la vez). `salida.py` no reintentaba, así que un aviso que ella necesitaba se
perdía por dos minutos de DNS caído.

El único peligro de reintentar es DUPLICAR un aviso, y por eso la regla es estrecha: se reintenta
solo cuando la conexión NI SIQUIERA LLEGÓ A ESTABLECERSE. Este test protege las dos mitades —que
reintente lo que debe, y que NO reintente lo que pudo haber salido.

No toca la red: sustituye `urlopen` por un doble.
"""
import os
import socket
import sys
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
# Mutis explícito de salida.py ANTES de importarla: ningún test de la batería puede escribirle de
# verdad a {{TITULAR}}. Lo exige `tests/test_normas_mecanizadas.py`, y con razón — aquí además se
# sustituye `urlopen`, pero el cinturón va puesto igual, no solo los tirantes.
os.environ["BTP_TEST_BATTERY"] = "1"
import salida  # noqa: E402

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def main():
    # --- Qué se considera «seguro que no salió» ---
    casos = [
        (socket.gaierror(8, "nodename nor servname provided"), True, "gaierror (DNS no resolvió)"),
        (urllib.error.URLError(socket.gaierror(8, "dns")), True, "URLError envolviendo gaierror"),
        (ConnectionRefusedError(61, "refused"), True, "conexión rechazada (nunca conectó)"),
        (urllib.error.HTTPError("u", 429, "Too Many", None, None), False,
         "HTTP 429: el servidor CONTESTÓ, no se reintenta"),
        (urllib.error.HTTPError("u", 500, "boom", None, None), False, "HTTP 500 tampoco"),
        (TimeoutError("timed out"), False, "timeout: PUDO llegar, no se reintenta (no duplicar)"),
        (urllib.error.URLError(TimeoutError("t")), False, "URLError envolviendo timeout tampoco"),
    ]
    for exc, esperado, nombre in casos:
        ok(salida._no_llego_seguro(exc) is esperado, nombre)

    # --- El reintento de verdad: falla dos veces por DNS y entra a la tercera ---
    intentos = {"n": 0}

    class _Resp:
        status = 200

        def read(self):
            return b"{}"

    def _urlopen_flaky(req, timeout=None):
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided"))
        return _Resp()

    orig_urlopen, orig_sleep, orig_secret = (salida.urllib.request.urlopen, salida.time.sleep,
                                             salida.get_secret)
    salida.urllib.request.urlopen = _urlopen_flaky
    salida.time.sleep = lambda s: None            # el test no espera de verdad
    salida.get_secret = lambda *a, **k: "token-de-prueba"
    try:
        enviado, motivo = salida._deliver_telegram("123", "aviso de prueba")
        ok(enviado is True, "tras dos fallos de DNS, el aviso SÍ acaba saliendo (%s)" % motivo)
        ok(intentos["n"] == 3, "lo intentó 3 veces, no una (%d)" % intentos["n"])

        # --- Un fallo que PUDO haber salido no se reintenta: nada de duplicar avisos ---
        intentos["n"] = 0

        def _urlopen_timeout(req, timeout=None):
            intentos["n"] += 1
            raise TimeoutError("timed out")

        salida.urllib.request.urlopen = _urlopen_timeout
        enviado2, _ = salida._deliver_telegram("123", "otro aviso")
        # 24-sep-26 (auditoría 3.5): un timeout ya no es `False` («seguro que no salió») sino `None`
        # («no se sabe»), que sigue siendo falsy para `send()`. Así `approve_and_deliver` no deja el
        # borrador «a un clic» para que ella lo apruebe dos veces. Ver test_salida_idempotente.py.
        ok(not enviado2, "un timeout se reporta como no entregado")
        ok(enviado2 is None, "y como INCIERTO, no como «seguro que no salió» (%r)" % (enviado2,))
        ok(intentos["n"] == 1, "y NO se reintenta: pudo haber llegado (%d intento)" % intentos["n"])

        # --- Un 429 tampoco: el servidor decidió, reintentar sería insistir contra su criterio ---
        intentos["n"] = 0

        def _urlopen_429(req, timeout=None):
            intentos["n"] += 1
            raise urllib.error.HTTPError("u", 429, "Too Many Requests", None, None)

        salida.urllib.request.urlopen = _urlopen_429
        salida._deliver_telegram("123", "tercero")
        ok(intentos["n"] == 1, "un HTTP 429 no se reintenta (%d intento)" % intentos["n"])
    finally:
        salida.urllib.request.urlopen = orig_urlopen
        salida.time.sleep = orig_sleep
        salida.get_secret = orig_secret

    # --- El reintento NO abre una boca de egress nueva: sigue habiendo un solo urlopen ahí ---
    src = open(os.path.join(ROOT, "tools", "salida.py"), encoding="utf-8").read()
    i = src.index("def _deliver_telegram")
    cuerpo = src[i:src.index("\n# ─", i)]
    ok(cuerpo.count("urlopen(") == 1, "el canal de Telegram sigue teniendo UNA sola llamada de red")
    ok("api.telegram.org" in cuerpo and cuerpo.count("https://") == 1,
       "y un único destino, construido igual que antes")

    print("RESULTADO salida_reintento: %d OK, %d fallos" % (_pass, _fail))
    print("✅ REINTENTO DE SALIDA EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
