#!/usr/bin/env python3
"""test_escalado_inteligencia.py — «subir la inteligencia» no puede depender del recall.

La memoria `feedback-elegir-modelo-y-modo` existía desde julio, pero solo llegaba al contexto
cuando el recall la traía: en tareas clínicas largas se lanzaban subagentes con `model: sonnet`
o a esfuerzo bajo sin que nadie lo decidiera (plan de las laminillas DFCI, F0, 1-oct-26).
Desde entonces vive como UNA línea en `normas-que-se-me-olvidan.md`, que no lleva `paths:` y
por tanto se carga siempre. Este test frena que esa línea se borre, se diluya o deje de
cargarse, y que crezca hasta romper el presupuesto de carga fija.
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))

import salud_memoria  # noqa: E402

NORMAS = os.path.join(RAIZ, ".claude", "rules", "normas-que-se-me-olvidan.md")
MEMORIA = "feedback-elegir-modelo-y-modo"
TOPE_LINEA = 200

fallos = []


def check(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        fallos.append(msg)


def _linea():
    with open(NORMAS, encoding="utf-8") as f:
        for linea in f:
            if "Subir la inteligencia" in linea:
                return linea.rstrip("\n")
    return None


def test_carga_siempre():
    print("── la norma vive en un fichero que se carga siempre ──")
    check(os.path.exists(NORMAS), "existe normas-que-se-me-olvidan.md")
    check(not salud_memoria._tiene_paths(NORMAS),
          "normas-que-se-me-olvidan.md NO lleva paths: (si lo lleva, deja de cargarse siempre)")


def test_contenido():
    print("── la línea dice lo que tiene que decir ──")
    linea = _linea()
    check(linea is not None, "hay una línea «Subir la inteligencia»")
    if linea is None:
        return
    for ancla in ("clínico", "muro", "verificación", "Opus", "esfuerzo alto", "subagentes",
                  "Haiku", "atascado", "bloquea", "[[%s]]" % MEMORIA):
        check(ancla in linea, "la línea nombra «%s»" % ancla)
    check(len(linea.encode("utf-8")) <= TOPE_LINEA,
          "la línea ocupa %d B ≤ %d B" % (len(linea.encode("utf-8")), TOPE_LINEA))


def test_memoria_enlazada():
    print("── la memoria a la que apunta existe (en casa base) ──")
    home = os.path.expanduser("~/.claude/projects")
    if not os.path.isdir(home):
        print("  ⏭️  sin ~/.claude/projects (CI): no hay memorias que mirar")
        return
    hallada = any(os.path.exists(os.path.join(home, d, "memory", MEMORIA + ".md"))
                  for d in os.listdir(home))
    check(hallada, "existe la memoria %s.md" % MEMORIA)


def main():
    test_carga_siempre()
    test_contenido()
    test_memoria_enlazada()
    if fallos:
        print("\n❌ %d fallo(s) en el escalado de inteligencia" % len(fallos))
        return 1
    print("\n✅ ESCALADO DE INTELIGENCIA EN VERDE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
