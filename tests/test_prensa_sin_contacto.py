#!/usr/bin/env python3
"""test_prensa_sin_contacto.py — ninguna ficha, regla ni memoria vuelve a mandar la prensa por {{CONTACTO}}.

POR QUÉ EXISTE (14-oct-2026). {{TITULAR}}: «la parte de prensa la llevaba {{CONTACTO}}, ya no la lleva, es un
cuello de botella; ahora la estrategia y la gestión de prensa son de Polaris». La premisa vieja
(20-jun) estaba cableada en fichas que se cargan siempre, y el recall la siguió inyectando como
vigente el mismo día que ella la cambió. Una memoria sola ya había fallado: esto es el freno.

Falla si una ficha de agente, una regla o una memoria feedback-* dice que la prensa va «vía /
por / para {{CONTACTO}}», que «lo lleva / coordina {{CONTACTO}}», o que {{CONTACTO}} es el «canal» de prensa. Las
menciones que lo NIEGAN a su lado («ya no», «nunca», «hasta el 14-oct») pasan: son la norma nueva.
Norma: memoria feedback-prensa-la-lleva-polaris.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import normas  # noqa: E402

CONTACTO = r"Mar[ií]a(?!\s+(?:Paula|Jos[eé]|Riveiro|Bouhad))"
PATRONES = [
    re.compile(rf"\b(?:v[ií]a|va por|por|para)\s+\**{CONTACTO}", re.I),
    re.compile(rf"\b(?:lo lleva|la lleva|coordina|gestiona)\s+\**{CONTACTO}", re.I),
    re.compile(rf"{CONTACTO}[^.\n]{{0,40}}\bcanal\b", re.I),
    re.compile(r"latodoterrenomc@", re.I),
]
NIEGA = re.compile(r"ya no|nunca|no longer|never|hasta el 14-oct|sustituye a", re.I)
PRENSA = re.compile(r"prensa|press|periodist|medios|entrevist|pitch|canal|CC", re.I)


def _ficheros():
    for d in (".claude/agents", ".claude/rules"):
        base = os.path.join(ROOT, d)
        if os.path.isdir(base):
            for f in sorted(os.listdir(base)):
                if f.endswith(".md"):
                    yield os.path.join(base, f)
    for slug in normas._memorias_feedback():
        yield os.path.join(normas.MEM_DIR, slug + ".md")


def infracciones(texto):
    malas = []
    for n, linea in enumerate(texto.splitlines(), 1):
        if not PRENSA.search(linea):
            continue
        for p in PATRONES:
            m = p.search(linea)
            # La negación solo cuenta si va pegada a la mención («ya no la lleva {{CONTACTO}}»), no si
            # aparece en otra frase de la misma línea («…nunca WebFetch»).
            if m and not NIEGA.search(linea[max(0, m.start() - 60):m.end() + 5]):
                malas.append((n, linea.strip()[:160]))
                break
    return malas


class LaPrensaNoVaPorMaria(unittest.TestCase):
    def test_detector_caza_la_premisa_vieja(self):
        viejas = [
            "> **Contacto/outreach con periodistas = lo lleva {{CONTACTO}}** (canal humano de prensa).",
            "- Periodistas/prensa → el material va al gabinete de prensa (vía {{CONTACTO}}).",
            "## Va por {{CONTACTO}} (prensa) — no lo mandas tú directamente",
            "Radar de prensa. Solo lee; **no contactes** (el contacto humano lo lleva {{CONTACTO}}). Vía Grok, nunca WebFetch.",
            "Los pitches son material para {{CONTACTO}}, que contacta a los periodistas.",
        ]
        for v in viejas:
            self.assertTrue(infracciones(v), v)

    def test_detector_deja_pasar_la_norma_nueva(self):
        buenas = [
            "{{CONTACTO}} {{CONTACTO}} ya no lleva la prensa; la gestión es de Polaris.",
            "**Nunca CC a {{CONTACTO}}** ni «las entrevistas las coordina {{CONTACTO}} {{CONTACTO}}».",
            "Relaciones cálidas: {{CONTACTO}} {{CONTACTO}} (EFE, contacto cálido).",
            "Penguin Random House respondió ({{CONTACTO}} Riveiro) → prensa lo lee.",
        ]
        for b in buenas:
            self.assertFalse(infracciones(b), b)

    def test_fichas_reglas_y_memorias_limpias(self):
        fallos = []
        for ruta in _ficheros():
            try:
                with open(ruta, encoding="utf-8") as fh:
                    texto = fh.read()
            except OSError:
                continue
            for n, linea in infracciones(texto):
                fallos.append(f"{os.path.relpath(ruta, ROOT)}:{n}: {linea}")
        self.assertEqual(fallos, [], "La prensa vuelve a pasar por {{CONTACTO}}:\n" + "\n".join(fallos))


if __name__ == "__main__":
    unittest.main()
