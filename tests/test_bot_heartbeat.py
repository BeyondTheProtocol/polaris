#!/usr/bin/env python3
"""test_bot_heartbeat.py — heartbeat de salud de bot_telegram (incidente 2/7/26).

Bug: el 2/7 el daemon com.btp.bot-telegram estaba VIVO (launchctl status 0) pero su long-poll
llevaba un buen rato en bucle de resets de red (URLError/ConnectionResetError), sin ningún poll
OK entre medias. healthcheck.py solo revive daemons CAÍDOS ("vivo ≠ sano"), así que no lo cazó;
se arregló a mano con `launchctl kickstart -k`.

Fix: poll_once() escribe un heartbeat propio (state/heartbeat/bot-telegram.json) SOLO cuando
salida.poll_updates() llegó a hablar de verdad con Telegram (con o sin mensajes nuevos) — nunca
cuando solo vio una excepción de red. Así un bucle sostenido de resets deja el heartbeat rancio,
y healthcheck._salud_daemons (VEGA_DAEMONS) lo detecta y hace kickstart×1 (ver
test_healthcheck.bot_telegram_autofix_tests para esa mitad).

Aislado (estado en tempfile, mocks de salida.poll_updates/halted): no toca Telegram real.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="bot_hb_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_HALT_FILES"] = os.path.join(_TMP, "nh_a") + ":" + os.path.join(_TMP, "nh_b")
os.environ["BTP_PERIPHERIES"] = os.path.join(_TMP, "peripheries.json")
json.dump({"cerebros": []}, open(os.environ["BTP_PERIPHERIES"], "w"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bot_telegram as bot  # noqa: E402

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _leer_hb():
    try:
        return json.load(open(bot.HB_PATH, encoding="utf-8"))
    except FileNotFoundError:
        return None


def _limpiar_hb():
    try:
        os.remove(bot.HB_PATH)
    except OSError:
        pass


def main():
    bot.salida.halted = lambda: False

    # 1. poll con mensajes nuevos, SIN excepción de red → heartbeat 'ok' fresco
    _limpiar_hb()
    def poll_con_mensajes(*, timeout=0, estado_out=None):
        if isinstance(estado_out, dict):
            estado_out["ok"] = True
        return []   # sin mensajes para no disparar handle_message (fuera de alcance de este test)
    bot.salida.poll_updates = poll_con_mensajes
    n = bot.poll_once()
    hb = _leer_hb()
    ok(hb is not None, "poll exitoso (sin mensajes) escribe heartbeat")
    ok(hb is not None and hb.get("estado") == "ok", "heartbeat marca estado 'ok'")
    ok(hb is not None and hb.get("agente") == "bot-telegram", "heartbeat trae el nombre de agente correcto")
    ok(n == 0, "sin mensajes → 0 procesados")

    # 2. poll que SOLO vio una excepción de red (estado_out ok=False) → NO se toca el heartbeat
    _limpiar_hb()
    def poll_fallo_red(*, timeout=0, estado_out=None):
        if isinstance(estado_out, dict):
            estado_out["ok"] = False
            estado_out["motivo"] = "URLError(ConnectionResetError(54, 'Connection reset by peer'))"
        return []
    bot.salida.poll_updates = poll_fallo_red
    bot.poll_once()
    ok(_leer_hb() is None, "fallo de red → NO escribe heartbeat (se queda sin latido, no falso-ok)")

    # 3. bucle sostenido de fallos de red (varias pasadas seguidas) → el heartbeat sigue AUSENTE
    #    todo el rato (esto es justo lo que healthcheck necesita para detectar el atasco: la
    #    ausencia de refresco, no un parseo de logs).
    _limpiar_hb()
    for _ in range(5):
        bot.poll_once()
    ok(_leer_hb() is None, "bucle sostenido de resets de red → heartbeat sigue sin refrescarse")

    # 4. tras el bucle, un poll que POR FIN habla con Telegram → el heartbeat vuelve a refrescarse
    bot.salida.poll_updates = poll_con_mensajes
    bot.poll_once()
    hb = _leer_hb()
    ok(hb is not None and hb.get("estado") == "ok", "tras recuperarse la red, el heartbeat vuelve a refrescarse")

    # 5. en HALT no se toca Telegram → tampoco se refresca el heartbeat (no estamos hablando de verdad)
    _limpiar_hb()
    bot.salida.halted = lambda: True
    n = bot.poll_once()
    ok(n == 0, "en HALT, poll_once no procesa nada")
    ok(_leer_hb() is None, "en HALT no se escribe heartbeat (no hubo poll real)")
    bot.salida.halted = lambda: False

    # 6. estado_out con mensajes reales SÍ procesa handle_message y AÚN ASÍ marca heartbeat 'ok'.
    #    Mockeamos handle_message para no encolar de verdad (fuera de alcance) y solo comprobar
    #    que el heartbeat se escribe tras el bucle, incluso con mensajes.
    _limpiar_hb()
    orig_handle = bot.handle_message
    procesados = []
    bot.handle_message = lambda msg: procesados.append(msg)

    def poll_dos_msgs(*, timeout=0, estado_out=None):
        if isinstance(estado_out, dict):
            estado_out["ok"] = True
        return [{"kind": "text", "text": "hola"}, {"kind": "text", "text": "buenas"}]
    bot.salida.poll_updates = poll_dos_msgs
    n = bot.poll_once()
    ok(n == 2, "procesa los 2 mensajes")
    ok(len(procesados) == 2, "handle_message se llamó 2 veces")
    hb = _leer_hb()
    ok(hb is not None and hb.get("estado") == "ok" and hb.get("procesados") == 2,
       "heartbeat con mensajes también queda 'ok' y anota cuántos procesó")
    bot.handle_message = orig_handle

    print("RESULTADO bot heartbeat (incidente 2/7/26): %d OK, %d fallos" % (_pass, _fail))
    print("✅ BOT HEARTBEAT EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
