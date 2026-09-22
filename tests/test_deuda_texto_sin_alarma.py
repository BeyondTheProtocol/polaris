#!/usr/bin/env python3
"""tests/test_deuda_texto_sin_alarma.py — que un aviso ya triado no se lea como una emergencia.

EL CASO (20-sep-2026). El libro tenía un hallazgo `saldo_prepago_urgente` en estado **remitido**,
del 18-sep, cuyo texto empieza con «🔴 La API de Anthropic responde SIN CRÉDITO: … el núcleo se
para hasta que recargues». El render lo marcaba bien —`💤 remitido · 2d`— pero el emoji y el
presente viven DENTRO de la prosa, así que sobreviven a cualquier lectura parcial: un `grep 🔴`,
un digest, un resumen a Telegram. Leído así parecía que el sistema estaba parado en ese momento.
No lo estaba: `ia/credito.json` decía ok, y el lazo llevaba 504 jobs ese día.

Ya había mordido antes por otro camino (13-sep): un estimador mal leído llevó a pedirle a {{TITULAR}}
que recargara saldo sin motivo. La urgencia la declara el ESTADO, no la prosa de quien abrió el
hallazgo — y la prosa es lo único que viaja cuando alguien cita media línea.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import deuda  # noqa: E402

OK, FALLOS = [], []


def ok(msg, cond, extra=""):
    (OK if cond else FALLOS).append(msg)
    print(("  ✅ " if cond else "  ❌ ") + msg + (("  " + extra) if extra and not cond else ""))


CASO = "🔴 La API de Anthropic responde SIN CRÉDITO: el núcleo se para hasta que recargues"

# 1. El caso real: remitido → sin el emoji, pero con el texto entero.
t = deuda.texto({"estado": "remitido", "que": CASO})
ok("un hallazgo remitido pierde el 🔴 de la prosa", not t.startswith("🔴"), "-> %r" % t[:40])
ok("y NO pierde el contenido", "SIN CRÉDITO" in t and "recargues" in t, "-> %r" % t[:60])

# 2. Lo que SÍ grita conserva su emoji: no se trata de silenciar, sino de no mentir.
for estado in deuda.GRITAN:
    ok("un hallazgo %s conserva el 🔴" % estado,
       deuda.texto({"estado": estado, "que": CASO}).startswith("🔴"))

# 3. Varios emojis encadenados, que es como se escriben de verdad.
ok("limpia una ristra de alarmas",
   deuda.texto({"estado": "abierto", "que": "🔴 ⚠️ 💳 algo pasó"}) == "algo pasó",
   "-> %r" % deuda.texto({"estado": "abierto", "que": "🔴 ⚠️ 💳 algo pasó"}))

# 4. Un emoji en MEDIO no se toca: puede ser parte de lo que se cuenta.
medio = "el daemon escribió 🔴 en el log"
ok("un emoji en medio del texto se respeta",
   deuda.texto({"estado": "abierto", "que": medio}) == medio)

# 5. Sin texto, sin drama.
ok("un hallazgo sin `que` no revienta", deuda.texto({"estado": "abierto"}) == "")

# 6. Y el panel lo usa: saberlo sin aplicarlo donde se lee no sirve.
src = open(os.path.join(ROOT, "tools", "observatorio.py"), encoding="utf-8").read()
ok("el Observatorio muestra el texto ya limpio", "deuda.texto(v)" in src,
   "-> el panel volvería a heredar el emoji de la prosa")

print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS
      else "test_deuda_texto_sin_alarma: %d/%d OK" % (len(OK), len(OK)))
sys.exit(1 if FALLOS else 0)
