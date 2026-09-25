#!/usr/bin/env python3
"""test_terminos_carril.py — «vacuna» y «neoantígeno» se vetan en marketing, NO en ciencia.

NORMA: `feedback-neoantigeno-restringido-solo-marketing` (clase BLOQUEO) — «los términos
protegidos (vacuna/neoantígeno) se vetan SOLO en marketing/público; en INVESTIGACIÓN/clínico son
términos científicos normales y SÍ se usan».

LA PROTECCIÓN YA EXISTÍA Y NADIE LA VIGILABA. `tools/borde.py` tiene `veto_publico` como
parámetro desde hace tiempo y `clasificar_consulta()` lo apaga a propósito — su propio docstring
dice «NO aplica el veto público (vacuna/neoantíg) porque la ciencia genérica es legítima». Pero
la norma estaba con `mecanismo: null` y ningún test fijaba la DISTINCIÓN, que es justo lo que
importa: un endurecimiento futuro del veto la borraría sin que nada se pusiera rojo, y el
sistema dejaría de poder buscar literatura sobre su propio tratamiento.

LO QUE SE PRUEBA NO ES EL VETO, ES EL CARRIL: el MISMO texto tiene que caer en público y pasar
en consulta. Por eso cada caso se ejecuta contra los dos carriles.

Verificado ejecutando `borde.py` el 18-sep-26.
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige  # noqa: E402
_exige("identidad")   # el detector del borde necesita los overlays gitignored
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "tools"))
import borde  # noqa: E402

CIENCIA = [
    "La vacuna de neoantígenos personalizada mostró respuesta en un ensayo fase 1.",
    "neoantigen vaccine trial NCT06691035 eligibility criteria",
    "¿Qué evidencia hay de vacunas de neoantígenos en {{DIAGNOSTICO}}?",
    "{{VACUNA}}: vacuna de ARNm individualizada, resultados en páncreas",
]
fallos = 0
casos = []


def check(desc, ok):
    global fallos
    casos.append((desc, ok))
    if not ok:
        fallos += 1


print("=== el MISMO texto: vetado en público, limpio en consulta ===")
for texto in CIENCIA:
    bloq_pub, motivo_pub = borde.clasificar(texto, veto_publico=True)
    bloq_con, motivo_con = borde.clasificar_consulta(texto)
    et = texto[:52]
    check("«%s…» → vetado en el carril PÚBLICO" % et, bloq_pub)
    check("   …y LIMPIO en el carril de investigación", not bloq_con)
    print("  %s %s" % ("✅" if (bloq_pub and not bloq_con) else "❌", et))
    if not (bloq_pub and not bloq_con):
        print("       público=%s (%s) · consulta=%s (%s)" % (bloq_pub, motivo_pub, bloq_con, motivo_con))

print()
print("=== apagar el veto NO abre la puerta a lo demás ===")
# El carril de investigación quita el embargo de PALABRAS, no el resto del borde. Si esto se
# rompiera, la norma se habría convertido en un agujero de PII.
SU_CASO = [
    ("Estamos desarrollando una vacuna de neoantígenos para {{TITULAR}}.", "su nombre"),
    ("Mi vacuna de neoantígenos, ¿qué opciones tengo?", "habla de SU caso"),
]
for texto, porque in SU_CASO:
    bloq, motivo = borde.clasificar_consulta(texto)
    check("en consulta, «%s» sigue bloqueado (%s)" % (texto[:34], porque), bloq)
    print("  %s %s → %s" % ("✅" if bloq else "❌", porque, motivo))

print()
print("=== el veto público sigue siendo veto ===")
for texto in ("Apoya la vacuna de {{TITULAR}}", "neoantígenos: dona ahora"):
    bloq, _ = borde.clasificar(texto, veto_publico=True)
    check("marketing «%s» → bloqueado" % texto[:30], bloq)
    print("  %s %s" % ("✅" if bloq else "❌", texto[:40]))

print()
print("RESULTADO terminos_carril: %d de %d OK" % (len(casos) - fallos, len(casos)))
print("✅ LA CIENCIA PUEDE DECIR «VACUNA»" if not fallos else "❌ revisar")
raise SystemExit(1 if fallos else 0)
