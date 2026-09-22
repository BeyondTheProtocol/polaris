#!/usr/bin/env python3
"""El libro de deuda avisa cuando la clave nueva se parece a una que YA está abierta.

POR QUÉ (20-sep-2026): el libro acumuló CINCO entradas abiertas del mismo agujero del guard
clínico, cada una abierta por quien lo volvía a encontrar sin ver que ya estaba. Cinco entradas
de 1x no escalan nunca — el mismo bug detectado cinco veces se lee como cinco bugs menores, que
es justo lo contrario de lo que R2 («repetir escala y escuece») quiere conseguir. La norma ya
decía «si ya estaba, `deuda.py visto`»; esto deja de confiar en que te acuerdes de buscar.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import deuda  # noqa: E402

# Un libro de mentira: el caso real que lo motivó, más vecinos que NO deben dar aviso.
LIBRO = {
    "clinico-guard-variable-shell": {"estado": "abierto"},
    "clinico-guard-ruta-en-variable": {"estado": "abierto"},
    "clinico-guard-variables-evaden-bash": {"estado": "abierto"},
    "clinico-guard-deniega-escrituras": {"estado": "abierto"},
    "clinico-guard-ventanilla-por-substring": {"estado": "escalado"},
    "archivar-nota-dice-reindexado-sin-reindexar": {"estado": "abierto"},
    "kpi-ned-sin-fuente": {"estado": "cerrado"},
    "kpi-ned-sin-fuente-primaria": {"estado": "cerrado"},
    "daemon_fallando:com.btp.backup": {"estado": "abierto"},
}

CASOS = [
    # (clave nueva, tiene_que_avisar, trozo que debe salir en ALGUNA sugerencia, por qué)
    ("clinico-guard-no-resuelve-variables", True, "variable",
     "el caso real: 5ª entrada del mismo agujero de variables"),
    ("clinico-guard-ventanilla-subcadena", True, "ventanilla-por-substring",
     "el mismo bypass con otro nombre"),
    ("radar-ned-cola-sin-abrir", False, None,
     "nada que ver con lo que hay abierto"),
    ("kpi-ned-sin-fuente-verificada", False, None,
     "lo parecido está CERRADO: no se avisa de lo que ya se arregló"),
    ("daemon_fallando:com.btp.kb-reindex", False, None,
     "`familia:instancia` la abre un detector; parecerse ahí es lo normal"),
]

fallos = 0
for clave, debe, esperado, porque in CASOS:
    cerca = deuda.parecidas(clave, dict(LIBRO))
    hubo = bool(cerca)
    ok = (hubo == debe)
    if ok and debe and esperado:
        ok = any(esperado in k for k, _e, _n in cerca)
    if not ok:
        fallos += 1
    print("  %s %-40s -> %s" % ("✅" if ok else "❌ MAL", clave,
                                (", ".join(k for k, _e, _n in cerca[:2]) if cerca else "sin aviso")))
    if not ok:
        print("       esperaba: %s (%s)" % ("alguna con %r" % esperado if debe else "sin aviso", porque))

# Una clave que YA está en el libro no se sugiere a sí misma (sugerir vecinos sí vale).
propia = "clinico-guard-deniega-escrituras"
if any(k == propia for k, _e, _n in deuda.parecidas(propia, dict(LIBRO))):
    print("  ❌ MAL %s se sugiere a sí misma" % propia)
    fallos += 1
else:
    print("  ✅ %-40s -> no se sugiere a sí misma" % propia)

# Y el freno de verdad: ninguna sugerencia puede ser una deuda ya cerrada.
cerradas = {k for k, v in LIBRO.items() if v["estado"] == "cerrado"}
for clave, _d, _e, _p in CASOS:
    for k, _est, _n in deuda.parecidas(clave, dict(LIBRO)):
        if k in cerradas:
            print("  ❌ MAL %s sugiere la cerrada %s" % (clave, k))
            fallos += 1

print()
print("test_deuda_duplicadas: %d casos, %d fallos" % (len(CASOS), fallos))
raise SystemExit(1 if fallos else 0)
