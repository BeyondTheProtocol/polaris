#!/usr/bin/env python3
"""Tests de capacidades.py (reusar-antes-de-crear). Lee los agentes reales del repo; sin red."""
import os
import sys

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
import capacidades as cap  # noqa: E402

fallos = 0


def check(nombre, cond):
    global fallos
    print(("  ✓ " if cond else "  ✗ ") + nombre)
    if not cond:
        fallos += 1


def top(q):
    r = cap.buscar(q, 5)
    return r[0][1] if r else None


def slugs(q):
    return [s for _sc, s, _d in cap.buscar(q, 5)]


# stopwords y tokens cortos fuera
check("_toks filtra palabras vacías y cortas", cap._toks("de la el en un yo") == [])

# un viaje → agencia-viajes el más fuerte
check("viaje → agencia-viajes", top("viaje vuelos alojamiento para una cita en otra ciudad") == "agencia-viajes")

# prensa/medios → la familia de prensa entre los primeros
check("periodistas/medios → prensa en el top", "prensa" in slugs("redactar pitch para periodistas y medios"))

# capacidad inventada sin encaje → hueco (lista vacía tras filtrar ruido)
check("capacidad inexistente → hueco (sin resultados)", cap.buscar("criar abejas en el tejado", 5) == [])

# evidencia clínica → cae en un agente clínico/evidencia (comite-medico o verificacion)
check("evidencia clínica → agente clínico/evidencia",
      top("revisar la literatura ingeniera y la evidencia clínica") in {"comite-medico", "verificacion"})

print("RESULTADO capacidades: %d OK, %d fallos" % (5 - fallos, fallos))
print("✅ CAPACIDADES EN VERDE" if not fallos else "❌ revisar fallos")
sys.exit(0 if not fallos else 1)
