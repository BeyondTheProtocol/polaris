#!/usr/bin/env python3
"""test_memoria_contradicciones.py — el detector caza los 4 casos reales del 26-sep y no el ruido.

Los casos son las versiones de ANTES del arreglo (resumidas, sin datos clínicos): la descripción de
Vega «ejecuta» corregida más abajo (R1), el nombre «no-perplexity» con un cuerpo que lo retira (R2),
una memoria que afirma lo que «sigue diciendo» otra (R3) y la cita de una memoria DESCARTADA (R4).
Negativos: un «ya no» en prosa corriente y las versiones ya arregladas. Todo en una carpeta temporal
(`BTP_MEMORY_DIR`): nunca lee ni toca las memorias reales.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "memoria_contradicciones.py")
fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def mem(d, slug, cuerpo):
    with open(os.path.join(d, slug + ".md"), "w", encoding="utf-8") as f:
        f.write("---\nname: %s\ndescription: x\nmetadata:\n  type: feedback\n---\n\n%s\n" % (slug, cuerpo))


RELLENO = ("Detalle del plan por fases con mucho texto para que la corrección quede lejos del "
           "principio que inyecta la ficha del recall. " * 8)


def detectar(d):
    p = subprocess.run([sys.executable, TOOL, "--json"], capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, BTP_MEMORY_DIR=d))
    return json.loads(p.stdout)


def main():
    d = tempfile.mkdtemp(prefix="memcontra_")
    mem(d, "project-comite-tareas", "Listón nuevo: TODAS las tareas controladas y EJECUTADAS a tiempo.\n\n"
        + RELLENO + "\n\n- Fase 2: ⚠️ {{TITULAR}} REDEFINIÓ el alcance: Vega solo supervisa y avisa.")
    mem(d, "feedback-evidencia-grok-no-perplexity", "Las citas de cualquier buscador se cotejan.\n\n"
        "- Perplexity SÍ es fuerte descubriendo lo último. Retiro el «saltarla».")
    mem(d, "reference-perfil", "Perfil actual cotejado. Ya NO es el de junio.")
    mem(d, "feedback-no-asumir", "Lección sobre asumir.\n\nCausa: la memoria `reference-perfil` sigue "
        "diciendo lo de junio y no lo nombra.")
    mem(d, "reference-bot-viejo", "🪦 DESCARTADA (11-sep): el bot ya no responde.")
    mem(d, "project-catalogo", "Catálogo de IAs en [[reference-bot-viejo]]; lo demás sigue igual.")
    # Negativos: prosa corriente, y memorias ya arregladas.
    mem(d, "feedback-secreto", "Un secreto impreso en el chat ya no es un secreto.\n\n" + RELLENO
        + "\n\nVías descartadas por frágiles: ninguna aplica.")
    mem(d, "project-arreglada", "**Vigente: Vega SOLO supervisa.** El listón de ejecutar es historia.\n\n"
        + RELLENO + "\n\n- Fase 2: {{TITULAR}} REDEFINIÓ el alcance.")
    mem(d, "project-cita-bien", "Catálogo histórico en [[reference-bot-viejo]] (DESCARTADA).")

    h = detectar(d)
    por = {(x["regla"], x["slug"]) for x in h}
    print("── casos reales del 26-sep ──")
    check(("R1", "project-comite-tareas") in por, "R1: la corrección de Vega por debajo de la ficha")
    check(("R2", "feedback-evidencia-grok-no-perplexity") in por, "R2: el nombre «no-perplexity» retirado en el cuerpo")
    check(("R3", "feedback-no-asumir") in por, "R3: afirma lo que «sigue diciendo» otra memoria")
    check(("R4", "project-catalogo") in por, "R4: cita una DESCARTADA sin decirlo")
    print("── ruido y arregladas: no salen ──")
    check(not any(x["slug"] == "feedback-secreto" for x in h), "«ya no es un secreto» en prosa no cuenta")
    check(not any(x["slug"] == "project-arreglada" for x in h), "la que ya avisa arriba de lo vigente no sale")
    check(not any(x["slug"] == "project-cita-bien" for x in h), "citar una descartada DICIÉNDOLO no sale")
    check(not any(x["regla"] == "ERROR" for x in h), "ninguna memoria rompe el informe")
    ficheros_antes = sorted(os.listdir(d))
    detectar(d)
    check(sorted(os.listdir(d)) == ficheros_antes, "solo lee: no crea ni borra nada")

    print("\ntest_memoria_contradicciones: %d fallos" % len(fallos))
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
