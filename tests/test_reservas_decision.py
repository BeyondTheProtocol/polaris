#!/usr/bin/env python3
"""test_reservas_decision.py — un carrito no es una decisión, lo pase quien lo pase.

NORMA: `feedback-carrito-no-es-decidido` (clase BLOQUEO) — «el carrito/lista de deseos de Amazon
(o cualquier "guardado") NO significa decidido — no reservar/comprar algo solo porque esté ahí».

EL FRENO YA EXISTÍA Y NO SERVÍA. `clasificar()` exigía `decision_tomada` para el carril verde
desde siempre… pero se fiaba del booleano que le pasaran. El estado vivo del 18-sep-26 enseña
que ese booleano no era de fiar: de 24 encargos, **14 declaraban `decision_tomada=True` desde un
origen que no es una decisión suya** — 1 de `amazon-carrito` (la norma, literal), 6 de
`propuesta-conserje` (una propuesta mía contada como decisión suya) y 7 de `estudio-viaje-julio`.
Ninguno se compró porque en F1 `AUTO_PAGO_ACTIVO=False`: el daño era LATENTE, esperando a F3.

Lo que prueba este fichero: que la PROCEDENCIA manda sobre el booleano, en los dos puntos —
al registrar el encargo (`crear`) y al decidir el riesgo (`clasificar`, que es lo que protege a
los 14 que YA están guardados sin reescribir el estado vivo por debajo).

Y lo que NO se puede romper: `agencia-viajes`, que sí emite tras una confirmación suya (así lo
manda su charter), tiene que seguir pudiendo declarar la decisión.
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import shutil  # noqa: E402
import tempfile  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="reservas_dec_")
_os.environ["BTP_STATE_DIR"] = _TMP          # estado AISLADO: no se toca el vivo
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "tools"))
import reservas as r  # noqa: E402

# `viaje` y no `compra`: el sobre por defecto deja las compras FUERA del carril verde
# ("compras grandes fuera por defecto"), así que con `compra` todo saldría ámbar por
# otra razón y el test no probaría nada. Con `viaje` lo ÚNICO que separa verde de ámbar
# es la decisión — que es justo lo que se está probando.
VERDE = dict(categoria="viaje", importe_eur=10.0, reembolsable=True)
fallos = 0
casos = []


def check(desc, ok):
    global fallos
    casos.append((desc, ok))
    if not ok:
        fallos += 1


# ── la procedencia rebaja la decisión al REGISTRAR ────────────────────────────────────────
for origen, etiqueta in (("amazon-carrito", "el carrito de Amazon (la norma, literal)"),
                         ("propuesta-conserje", "una propuesta MÍA"),
                         ("estudio-viaje-julio", "un estudio"),
                         ("wishlist-navidad", "una lista de deseos"),
                         ("guardados-mayo", "unos guardados"),
                         ("AMAZON-CARRITO", "el carrito en MAYÚSCULAS")):
    e = r.crear("prueba %s" % origen, "compra", importe_eur=10.0, reembolsable=True,
                decision_tomada=True, origen=origen)
    check("crear desde %s → decision_tomada rebajada a False" % etiqueta,
          e["decision_tomada"] is False)
    check("   …y queda escrito POR QUÉ (decision_rebajada)", bool(e.get("decision_rebajada")))
    check("   …pero el encargo SÍ se crea (se rechaza la afirmación, no el encargo)",
          e.get("id") and e.get("estado") == "pendiente")

# ── la procedencia manda también al CLASIFICAR (los 14 que ya están guardados) ────────────
enc_guardado = dict(VERDE, origen="amazon-carrito", decision_tomada=True)
riesgo, razones = r.clasificar(enc_guardado)
check("un encargo YA guardado con carrito+decisión NO entra en verde", riesgo != "verde")
check("   …y la razón nombra el origen, no un genérico",
      any("carrito" in x for x in razones))

riesgo, _ = r.clasificar(dict(VERDE, origen="propuesta-conserje", decision_tomada=True))
check("propuesta-conserje guardada con decisión → tampoco entra en verde", riesgo != "verde")

# ── lo que NO se puede romper ─────────────────────────────────────────────────────────────
e = r.crear("tren a Zúrich", "viaje", importe_eur=10.0, reembolsable=True,
            decision_tomada=True, origen="agencia-viajes")
check("agencia-viajes (confirmación suya) mantiene decision_tomada=True",
      e["decision_tomada"] is True)
check("   …y no se le pone motivo de rebaja", e.get("decision_rebajada") is None)

riesgo, razones = r.clasificar(dict(VERDE, origen="agencia-viajes", decision_tomada=True))
check("agencia-viajes con todo en regla SÍ entra en verde", riesgo == "verde")

e = r.crear("compra manual", "compra", importe_eur=10.0, reembolsable=True,
            decision_tomada=True, origen="manual")
check("origen 'manual' mantiene la decisión", e["decision_tomada"] is True)

e = r.crear("algo del carrito sin decidir", "compra", importe_eur=10.0,
            decision_tomada=False, origen="amazon-carrito")
check("carrito SIN declarar decisión: no se inventa nada", e["decision_tomada"] is False)

riesgo, _ = r.clasificar(dict(VERDE, origen="", decision_tomada=True))
check("origen vacío no se rebaja (no es un guardado, es desconocido)", riesgo == "verde")
check("decision_no_vale sobre None no revienta", r.decision_no_vale(None) == (False, None))

# ── el rojo y el ámbar siguen mandando por encima de esto ─────────────────────────────────
riesgo, _ = r.clasificar(dict(VERDE, origen="amazon-carrito", decision_tomada=True,
                              irreversible=True))
check("irreversible sigue siendo rojo (el orden de las reglas no cambia)", riesgo == "rojo")
riesgo, _ = r.clasificar({"categoria": "viaje", "importe_eur": None,
                          "origen": "amazon-carrito", "decision_tomada": True})
check("importe desconocido sigue siendo rojo", riesgo == "rojo")

# ── la auditoría ve lo que hay, sin tocarlo ───────────────────────────────────────────────
malos = r.auditar()
check("auditar() no encuentra nada (crear ya rebaja: el estado nuevo nace limpio)", malos == [])

import json  # noqa: E402
with open(_os.path.join(_TMP, "reservas.json"), encoding="utf-8") as fh:
    data = json.load(fh)
data["encargos"].append({"id": "plantado", "titulo": "plantado a mano", "categoria": "compra",
                         "origen": "amazon-carrito", "decision_tomada": True,
                         "estado": "a_un_clic", "riesgo": "ambar"})
with open(_os.path.join(_TMP, "reservas.json"), "w", encoding="utf-8") as fh:
    json.dump(data, fh)
malos = r.auditar()
check("auditar() SÍ ve una contradicción escrita directamente en el estado",
      len(malos) == 1 and malos[0]["id"] == "plantado")

for desc, ok in casos:
    print(("  ✅ " if ok else "  ❌ ") + desc)
shutil.rmtree(_TMP, ignore_errors=True)
print()
print("RESULTADO reservas_decision: %d de %d OK" % (len(casos) - fallos, len(casos)))
print("✅ UN CARRITO NO ES UNA DECISIÓN" if not fallos else "❌ revisar")
raise SystemExit(1 if fallos else 0)
