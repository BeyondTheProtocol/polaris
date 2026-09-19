#!/usr/bin/env python3
"""test_fugu_egress.py — el cortafuegos de fugu.py es FAIL-CLOSED.

Verifica que NADA sensible (clínico/genómico/PII/términos vetados) puede salir hacia la
IA externa Fugu, y que una consulta benigna no-clínica sí pasa. No toca la red."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import fugu  # noqa: E402

_p = _f = 0


def chk(name, cond):
    global _p, _f
    if cond:
        _p += 1
    else:
        _f += 1
        print("  ✗ %s" % name)


BLOQUEAR = [
    ("nombre {{TITULAR}}", "Qué le digo a {{TITULAR}} sobre esto"),
    ("apellido", "el informe de {{APELLIDO}} para el caso"),
    ("vetado: vacuna", "ayúdame a diseñar la vacuna"),
    ("vetado: {{CONTACTO}}", "el plan de negocio de {{CONTACTO}}"),
    ("vetado: neoantígenos", "prioriza estos neoantígenos"),
    ("HLA", "mi tipo es HLA-A*02:01"),
    ("rsID", "la variante rs113488022 importa"),
    ("coordenada genómica", "está en chr17:7579472 del genoma"),
    ("variante proteica", "la mutación p.Arg175His del tumor"),
    ("variante cDNA", "c.524G>A en el gen"),
    ("genotipo VCF", "el genotipo es 0/1 en esa posición"),
    ("marcador clínico", "mi TP53 y PIK3CA están mutados"),
    ("Ki-67", "Ki-67 al 60% en el informe"),
    ("cifra ER%", "ER 95% en la biopsia"),
    ("email", "escribe a contacto@ejemplo.com"),
    ("teléfono", "mi número es +34 619 718 173"),
    ("DNI", "mi DNI es 12345678Z"),
    ("tercero del deny-list", "qué le mando a {{CONTACTO}} mañana"),
    ("vacío", ""),
]

PASAR = [
    ("coding EN", "Explain the difference between a hash map and a binary search tree in Python."),
    ("algoritmos ES", "¿Cuál es la complejidad de quicksort frente a mergesort?"),
    ("idea genérica", "Dame ideas para estructurar un changelog de un proyecto de software."),
]

for name, t in BLOQUEAR:
    ok, _m = fugu.egress_check(t)
    chk("BLOQUEA: " + name, ok is False)
for name, t in PASAR:
    ok, _m = fugu.egress_check(t)
    chk("PASA: " + name, ok is True)

print("RESULTADO fugu egress: %d OK, %d fallos" % (_p, _f))
print("✅ FUGU EGRESS EN VERDE" if _f == 0 else "❌ revisar fallos")
sys.exit(_f)
