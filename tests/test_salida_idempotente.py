#!/usr/bin/env python3
"""test_salida_idempotente.py — una aprobación entrega UNA vez, y lo incierto no se reenvía solo.

POR QUÉ EXISTE (24-sep-2026, auditoría externa de Marcos Gorgojo, hallazgo 3.5). `approve_and_deliver`
leía el borrador, lo entregaba y DESPUÉS lo movía a `sent/`. Reproducido aquí el mismo día: dos
hilos con la misma aprobación dieron **dos entregas**, y el segundo reventó con `FileNotFoundError`
al mover un fichero que ya no estaba. El camino que de verdad duplica hoy es otro y más tonto:
`_deliver_telegram` devolvía `False` igual si era SEGURO que no salió que si hubo un timeout
DESPUÉS de conectar (el mensaje pudo llegar). El borrador se quedaba en `pending/`, {{TITULAR}} volvía
a aprobar y le llegaba dos veces.

Lo que se fija:
  1. reclamación exclusiva: con dos aprobaciones a la vez, UNA entrega y la otra lo dice;
  2. resultado INCIERTO → el borrador se queda en `sending/` y no se reenvía hasta reconciliar;
  3. fallo SEGURO → vuelve a `pending/` y se puede reintentar;
  4. `reconciliar` es la única salida de `sending/`, y lo decide ella;
  5. `sending/` se ve en el estado (un estado que nadie mira es peor que el bug);
  6. el entregador real distingue «no salió» de «no sé».

⚠️ El 12-jul-2026 una pasada de tests le mandó 14 Telegram de verdad a {{TITULAR}}. Aquí van TRES
cinturones: BTP_TEST_BATTERY=1 + BTP_STATE_DIR en un tmp (las dos señales de `_bajo_bateria_test`),
los entregadores sustituidos por dobles, y `urlopen` convertido en una bomba que tumba el test si
alguien llega a la red sin querer.
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="salida-idem-")
os.environ["BTP_TEST_BATTERY"] = "1"
os.environ["BTP_STATE_DIR"] = TMP
os.environ["BTP_HALT_FILES"] = os.path.join(TMP, "halt-a") + ":" + os.path.join(TMP, "halt-b")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import salida  # noqa: E402

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


class _Bomba(Exception):
    pass


def _bomba(*a, **k):
    raise _Bomba("un test ha llegado a la red de verdad")


salida.urllib.request.urlopen = _bomba            # cinturón 3: nada sale, pase lo que pase
salida._self_chatid = lambda: "999"
DEST = "999"


_NONCES = {}
salida._avisar_nonce = lambda d, n, c: _NONCES.__setitem__(n, c)   # el nonce ya no está en disco


def _nuevo(texto):
    name = salida._draft("telegram", salida.REPORT, DEST, texto, "test")
    return name, _NONCES[name]


def _donde(name):
    for sub in ("pending", "sending", "sent"):
        if os.path.exists(os.path.join(salida.OUTBOX, sub, name)):
            return sub
    return None


def main():
    # ── 1. Dos aprobaciones a la vez → UNA entrega ──────────────────────────────────────────
    entregas = []
    cerrojo = threading.Lock()

    def lento(dest, text, **k):
        time.sleep(0.2)                           # el POST real tarda: ahí se colaba el segundo
        with cerrojo:
            entregas.append(text)
        return True, "doble"
    salida._DELIVERERS["telegram"] = lento
    name, nonce = _nuevo("carrera")
    res, errores = [], []

    def hilo():
        try:
            res.append(salida.approve_and_deliver(name, nonce))
        except Exception as e:                   # antes: FileNotFoundError en el segundo hilo
            errores.append(e)
    hs = [threading.Thread(target=hilo) for _ in range(4)]
    [h.start() for h in hs]
    [h.join() for h in hs]
    ok(len(entregas) == 1, "4 aprobaciones simultáneas → 1 sola entrega (hubo %d)" % len(entregas))
    ok(not errores, "ningún hilo revienta (%r)" % errores[:1])
    ok(sum(1 for r in res if r.get("delivered")) == 1, "solo una aprobación dice «entregado»")
    ok(_donde(name) == "sent", "el ganador lo deja en sent/ (está en %s)" % _donde(name))
    perdedores = [r for r in res if not r.get("delivered")]
    ok(perdedores and all(r.get("blocked") for r in perdedores), "los perdedores quedan bloqueados")

    # ── 2. Resultado INCIERTO → sending/, y no se reenvía solo ──────────────────────────────
    llamadas = []

    def incierto(dest, text, **k):
        llamadas.append(text)
        return None, "timeout tras conectar"
    salida._DELIVERERS["telegram"] = incierto
    name, nonce = _nuevo("incierto")
    r = salida.approve_and_deliver(name, nonce)
    ok(not r.get("delivered") and r.get("blocked"), "incierto no se da por entregado")
    ok(_donde(name) == "sending", "incierto se queda en sending/ (está en %s)" % _donde(name))
    ok("incierto" in r.get("reason", "").lower(), "el motivo dice «incierto»: %r" % r.get("reason"))
    r2 = salida.approve_and_deliver(name, nonce)
    ok(len(llamadas) == 1, "re-aprobar algo incierto NO vuelve a entregar (%d llamadas)" % len(llamadas))
    ok("reconcilia" in r2.get("reason", "").lower(),
       "re-aprobar pide reconciliar, no «no encontrado»: %r" % r2.get("reason"))
    # se ve en el estado
    vivos = salida.listar_sending()
    ok(any(v["nombre"] == name for v in vivos), "listar_sending() lo enseña")

    # ── 3. Fallo SEGURO → vuelve a pending/, reintentable ───────────────────────────────────
    salida._DELIVERERS["telegram"] = lambda dest, text, **k: (False, "DNS no resolvió")
    name3, nonce3 = _nuevo("seguro")
    r = salida.approve_and_deliver(name3, nonce3)
    ok(not r.get("delivered") and _donde(name3) == "pending",
       "fallo seguro → vuelve a pending/ (está en %s)" % _donde(name3))
    salida._DELIVERERS["telegram"] = lambda dest, text, **k: (True, "doble")
    r = salida.approve_and_deliver(name3, nonce3)
    ok(r.get("delivered") and _donde(name3) == "sent", "tras un fallo seguro, re-aprobar entrega")

    # ── 4. Reconciliar: la ÚNICA salida de sending/ ─────────────────────────────────────────
    r = salida.reconciliar(name, "reintentar")
    ok(r.get("ok") and _donde(name) == "pending", "reconciliar reintentar → pending/")
    nonce_nuevo = _NONCES[name]
    ok("nonce" not in json.load(open(os.path.join(salida.PENDING, name), encoding="utf-8")),
       "reintentar tampoco deja el nonce en claro en disco")
    ok(nonce_nuevo != nonce, "reintentar estrena nonce (la aprobación vieja no vale)")
    ok(salida.approve_and_deliver(name, nonce).get("delivered") is False, "el nonce viejo ya no entrega")

    salida._DELIVERERS["telegram"] = incierto
    name4, nonce4 = _nuevo("se dio por llegado")
    salida.approve_and_deliver(name4, nonce4)
    r = salida.reconciliar(name4, "entregado")
    ok(r.get("ok") and _donde(name4) == "sent", "reconciliar entregado → sent/")
    ok(not salida.reconciliar("no-existe.json", "entregado").get("ok"), "reconciliar algo que no está → no")
    ok(not salida.reconciliar(name4, "lo-que-sea").get("ok"), "decisión desconocida → no")

    # ── 5. El estado lo cuenta ──────────────────────────────────────────────────────────────
    salida._DELIVERERS["telegram"] = incierto
    name5, nonce5 = _nuevo("para el status")
    salida.approve_and_deliver(name5, nonce5)
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        salida._cli_status()
    ok("sending" in buf.getvalue() and "reconciliar" in buf.getvalue(),
       "`salida.py status` enseña lo que está en sending/ y cómo salir")

    # ── 5b. Y los demás sitios que antes solo miraban pending/ ──────────────────────────────
    import ramas
    ok(any(r.endswith("outbox/sending") for r in ramas._RUTAS_TRABAJO_VIVO),
       "la poda de worktrees no se lleva un envío incierto")
    for mod, marca in (("seguimiento", "OUTBOX_SENDING"), ("observatorio", "outbox\", \"sending")):
        src = open(os.path.join(ROOT, "tools", mod + ".py"), encoding="utf-8").read()
        ok(marca in src, "%s mira outbox/sending" % mod)
    src_bot = open(os.path.join(ROOT, "tools", "bot_telegram.py"), encoding="utf-8").read()
    ok("salida.reconciliar(" in src_bot, "el bot tiene «reconciliar» (ella decide sin abrir la terminal)")

    # ── 6. El entregador REAL distingue «no salió» de «no sé» ───────────────────────────────
    salida.get_secret = lambda *a, **k: "tok"
    for exc, esperado, que in (
            (urllib.error.URLError(socket.gaierror(8, "nodename")), False, "DNS no resolvió"),
            (urllib.error.URLError(ConnectionRefusedError(61, "refused")), False, "conexión rechazada"),
            (socket.timeout("timed out"), None, "timeout tras conectar"),
            (ConnectionResetError(54, "reset"), None, "conexión cortada a mitad")):
        def _falla(*a, _e=exc, **k):
            raise _e
        salida.urllib.request.urlopen = _falla
        salida.time.sleep = lambda s: None        # los reintentos no esperan en el test
        got, _info = salida._deliver_telegram(DEST, "x")
        ok(got is esperado, "_deliver_telegram con %s → %r (dio %r)" % (que, esperado, got))
    salida.urllib.request.urlopen = _bomba

    print("RESULTADO salida idempotente: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
