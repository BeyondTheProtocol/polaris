#!/usr/bin/env python3
"""tools/bench_determinista.py — el clasificador que YA está en producción, contra el set dorado.

Por qué existe (20-sep-26): se midió Jev contra local antes de medir lo que ya tenemos. Con un
baseline trivial del 84,9% («di tarea a todo»), la pregunta previa a «¿qué modelo?» es
«¿hace falta un modelo?». Esto la responde, gratis y en segundos.

La diferencia que importa, y por la que no vale comparar a pelo con un LLM:
`triage_tareas.clasificar` tiene TRES salidas, no dos — `tarea`, `no` y **`dudosa`**. Un
«dudosa» no es un fallo, es una **abstención**: el sistema dice «no lo sé, que lo mire un
humano». Así que se miden dos cosas por separado:

  · COBERTURA — en qué fracción se moja. Abstenerse mucho es barato pero inútil.
  · ACIERTO EN LO QUE DECIDE — cuando se moja, ¿cuánto acierta?

Un clasificador que cubre el 60% con 97% de acierto es MEJOR para este carril que uno que
cubre el 100% con 82%, porque el 40% restante va a una cola que un humano drena, y ahí no se
pierde nada. Lo que se pierde de verdad es un correo de su equipo médico descartado con
confianza, que es justo el modo de fallo que enseñaron los modelos locales.

Uso:
  python3 tools/bench_determinista.py            # contra el set dorado, texto crudo
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import triage_tareas as tt
import score_local as sl


def bench(pregunta="es_tarea"):
    filas = sl._casos_crudos(pregunta)
    if not filas:
        raise SystemExit(f"⛔ no hay casos de la pregunta {pregunta!r}")

    decide = aciertos = 0
    abstiene = 0
    fallos = []
    for f in filas:
        v = tt.clasificar(f["texto"]).get("veredicto")
        if v == "dudosa":
            abstiene += 1
            continue
        decide += 1
        esperado = "tarea" if f["etiqueta"] == "tarea" else "no"
        if v == esperado:
            aciertos += 1
        else:
            fallos.append((f["texto"][:88], esperado, v))

    n = len(filas)
    mayoria = max(sum(1 for f in filas if f["etiqueta"] == e) for e in ("tarea", "no")) / n

    print(f"\n  clasificador determinista (`triage_tareas.clasificar`) · {n} casos")
    print(f"  cobertura            {decide / n:.1%}   ({decide} decididos · {abstiene} a la cola de dudosas)")
    if decide:
        print(f"  acierto al decidir   {aciertos / decide:.1%}")
    print(f"  baseline trivial     {mayoria:.1%}   («di tarea a todo», sobre los {n})")
    print(f"  latencia             ~0 ms (sin modelo, sin red)")

    if fallos:
        print(f"\n  se equivocó al mojarse, {len(fallos)} veces:")
        for txt, esp, dijo in fallos[:8]:
            print(f"   era «{esp}», dijo «{dijo}»: {txt}")
    return {"cobertura": decide / n, "acierto": aciertos / decide if decide else 0}


if __name__ == "__main__":
    bench()
