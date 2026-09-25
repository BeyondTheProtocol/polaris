#!/usr/bin/env python3
"""test_bot_free.py — router de carril del bot (planes expressive-plotting-flame + polished-swimming-deer).

Verifica el router de carril del bot:
  · saludo/cortesía → carril GRATIS directo (sin encolar, sin reunir contexto);
  · pregunta/charla/redacción (no acción, no hostil) → RESPONDEDOR AGNÓSTICO solo-lectura
    (`responder_con_datos`, con tus datos, sin encolar) — incluida la clínica/sensible, que el borde
    enruta DENTRO del respondedor a un cerebro de confianza (aquí mockeado: solo se prueba la ruta);
  · acción (verbo de efecto / prefijo `agente:`) u hostil → carril AGENTE (encola triaje en cuarentena);
  · «aprobar …» sigue igual.
Aislado (estado en /tmp, BTP_IA_FAKE, mocks de salida/queue/healthcheck/responder): no toca Telegram,
ni la cola, ni el respondedor reales.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="bot_free_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_HALT_FILES"] = os.path.join(_TMP, "nh_a") + ":" + os.path.join(_TMP, "nh_b")
os.environ["BTP_PERIPHERIES"] = os.path.join(_TMP, "peripheries.json")
os.environ["BTP_IA_FAKE"] = "RESPUESTA-GRATIS"
json.dump({"cerebros": [
    # incluye un cerebro local GRATIS y de CONFIANZA (como el real): el caso clínico NO debe
    # irse a él (regla del muro: clínico = Claude por calidad), aunque sea trusted+free.
    {"name": "local-ollama", "kind": "openai_local", "destino": "local:ollama",
     "url": "http://127.0.0.1:1/x", "trusted": True, "free": True, "capability": 4,
     "orden": 5, "enabled": True},
    {"name": "nvidia-free", "kind": "carril_gratis", "destino": "nvidia",
     "trusted": False, "free": True, "capability": 3, "orden": 10, "enabled": True},
    {"name": "claude", "kind": "claude", "destino": "cleared:claude",
     "trusted": True, "free": False, "capability": 9, "orden": 20, "enabled": True,
     "models": ["sonnet"]},
]}, open(os.environ["BTP_PERIPHERIES"], "w"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bot_telegram as bot  # noqa: E402
from _entorno import exige_cola_aislada as _exige_cola_aislada  # noqa: E402
_exige_cola_aislada()              # nada de fixtures en la cola de producción (20-sep-26)

_pass = 0
_fail = 0
REPLIES = []
ENQUEUED = []


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# Mocks: nada sale a Telegram ni a la cola real.
bot.healthcheck.mark_seen = lambda *a, **k: None
bot.salida.report_to_titular = lambda *a, **k: REPLIES.append(a[0] if a else "")
bot.q.enqueue = lambda *a, **k: (ENQUEUED.append((a, k)) or "JID123")
bot.salida.approve_and_deliver = lambda *a, **k: {"delivered": True, "reason": "ok"}
bot.salida.typing_to_titular = lambda *a, **k: None
# El respondedor agnóstico se prueba aparte (test_responder_datos). Aquí solo verificamos la RUTA:
# que la pregunta caiga en él (no en la cola). Mock canónico read-only.
bot.responder_con_datos.responder = lambda text, **k: {
    "mensaje": "RESPUESTA-RESPONDER", "text": "RESPUESTA-RESPONDER", "brain": "local-ollama",
    "deferred": False, "parado": False}


def envia(text):
    REPLIES.clear(); ENQUEUED.clear()
    return bot.handle_message({"text": text})


def main():
    # _es_accion (router determinista)
    ok(not bot._es_accion("¿qué es una caché LRU?"), "pregunta NO es acción")
    ok(bot._es_accion("investiga a la Dra. X"), "investiga → acción")
    ok(bot._es_accion("agente: piensa conmigo"), "prefijo agente: → acción")
    ok(bot._es_accion("Archiva esto en la fuente de verdad"), "archiva → acción (mayúscula)")
    ok(not bot._es_accion("resume estos tres párrafos"), "redactar/resumir NO es acción (va gratis)")
    # nuevo (24/6): URL pegada y "guárdalo/analízalo" en cualquier posición → acción (no respondedor)
    ok(bot._es_accion("https://www.instagram.com/reel/DYSpMlzo3FP/"), "URL pegada → acción (manejar el enlace)")
    ok(bot._es_accion("Yo te lo paso para que lo guardes para analizar"), "guardar (mid-frase) → acción")
    ok(bot._es_accion("mira esto y analízalo: www.ejemplo.com/x"), "URL + analizar → acción")
    ok(not bot._es_accion("¿qué tareas tengo hoy?"), "pregunta de tareas NO es acción (va al respondedor)")

    # 1) SALUDO/cortesía → carril GRATIS, sin encolar
    envia("hola, gracias")
    ok(len(ENQUEUED) == 0, "saludo: no encola")
    ok(any("RESPUESTA-GRATIS" in r for r in REPLIES), "saludo: responde por el carril gratis")
    ok(any("Claude" in r for r in REPLIES), "saludo: avisa que fue sin gastar Claude")

    # 1b) pregunta sobre SUS cosas → RESPONDEDOR agnóstico (con tus datos), NO encola, NO cerebro pelado
    st = envia("¿qué tareas tengo hoy?")
    ok(len(ENQUEUED) == 0 and st.startswith("respondido")
       and any("RESPUESTA-RESPONDER" in r for r in REPLIES)
       and not any("RESPUESTA-GRATIS" in r for r in REPLIES),
       "pregunta sobre sus cosas → respondedor agnóstico (sin encolar, sin cerebro pelado)")

    # 2) acción → carril Claude (encola triaje), no responde por gratis
    st = envia("investiga a la Dra. {{CONTACTO}}")
    ok(len(ENQUEUED) == 1 and st.startswith("triage"), "acción: encola el carril Claude")
    ok(not any("RESPUESTA-GRATIS" in r for r in REPLIES), "acción: NO usa el carril gratis")

    # 3) prefijo agente: → carril Claude
    st = envia("agente: piensa conmigo el siguiente paso")
    ok(len(ENQUEUED) == 1, "agente:: encola el carril Claude")

    # 4) pregunta SENSIBLE/clínica → RESPONDEDOR (read-only). Dentro de responder_con_datos, el borde
    #    la enruta a un cerebro de CONFIANZA o, si el principal no está, da lo objetivo (probado en
    #    test_responder_datos). Aquí: cae en el respondedor, NO se encola, NO usa el cerebro pelado.
    st = envia("¿qué significa que mi KI-67 salió alto y la variante R175H?")
    ok(len(ENQUEUED) == 0 and st.startswith("respondido")
       and not any("RESPUESTA-GRATIS" in r for r in REPLIES),
       "pregunta clínica → respondedor read-only (el borde gobierna dentro), sin encolar")

    # 5) «aprobar …» sigue igual (no router)
    st = envia("aprobar borrador-1 nonce123")
    ok(st.startswith("aprobacion") and len(ENQUEUED) == 0, "aprobar: ruta de aprobación intacta")

    print("RESULTADO bot gratis: %d OK, %d fallos" % (_pass, _fail))
    print("✅ BOT GRATIS EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
