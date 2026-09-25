#!/usr/bin/env python3
"""test_gate_punto08.py — los 4 checks de salida que nacieron del punto de rotura 08.

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026: 28 de
las 53 normas de salida de `tools/normas.json` no tenían mecanismo. Estos checks mecanizan las
cuatro más cercanas al muro y a NED. Cada una con POSITIVOS (lo que tiene que cazar) y NEGATIVOS
(los falsos positivos que el replay de 30 días encontró en la primera versión, y los que se
parecen). Además, el contrato: registradas en normas.json, activas y en modo `aviso` (la subida a
bloqueo es de la escalera del gate, no de aquí).
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gate_salida as g  # noqa: E402
import normas  # noqa: E402

CASOS = {
    "muro_en_borrador": {
        "pos": [
            "Te dejo el post para LinkedIn:\n\n```\nEstoy construyendo {{CONTACTO}}, un sistema para mi "
            "tratamiento.\n```\n",
            "Borrador del pie de foto para Instagram:\n\n> Soy ingeniera y llevo meses con esto.\n",
            "Copy para la web:\n\n> Como O\u200blune me ayuda a leer papers cada noche…\n",
        ],
        "neg": [
            # Replay 25-sep-26: una ruta de fichero en un bloque de shell no es copy.
            "La POC funciona. Para arrancarla:\n```bash\nnode ~/projects/contacto-poc/servidor.js\n```\n"
            "Después abre la web local.",
            "Post para X:\n\n```\nSoy ingeniera y construyo el sistema que ordena mi caso.\n```\n",
            "Borrador:\n\n> Un artículo ingeniero nuevo sobre vacunas personalizadas.\n",
            "{{CONTACTO}} va bien por dentro; hoy no hay ningún post ni borrador pendiente de revisar.",
        ],
    },
    "importes_recaudacion": {
        "pos": [
            "Mensaje para el DM:\n\n> ¡Gracias! Ya llevamos recaudados 12.400 € entre todos.\n",
            "Borrador del post:\n\n```\nEl GoFundMe sigue abierto, entra y comparte.\n```\n",
        ],
        "neg": [
            "La recaudación va por 12.400 € según el panel; no la meto en ningún borrador.",
            "Borrador del post:\n\n```\nGracias a todas por estar. Seguimos.\n```\n",
        ],
    },
    "cita_ia_buscador": {
        "pos": [
            "Según Perplexity, el ensayo NCT01234567 da una respuesta del 42 % en HER2-low, así que "
            "encaja muy bien con el caso.",
            "Grok dice que el estudio de fase 2 tuvo una mediana de supervivencia de 18 meses.",
        ],
        "neg": [
            # Replay 25-sep-26: «GPT-5.6» en un benchmark es un modelo, no un buscador.
            "No es más listo: su propio benchmark dice 67,8 % contra 67,9 % de GPT-5.6.",
            "Según Perplexity, el ensayo NCT01234567 da un 42 % [sin verificar]; lo cotejo mañana.",
            "Perplexity me trae un estudio con 42 %, pero es una pista: aún no abrí la fuente primaria.",
            "Perplexity va lento hoy, así que he usado scite para la búsqueda de literatura.",
        ],
    },
    "consenso_lentes": {
        "pos": [
            # Replay 25-sep-26: el único disparo, y es el caso exacto de la norma.
            "Lo he mandado a los dos comités y convergen solos, cada uno por su lado, en la cruz.",
            "Las cinco lentes coinciden en que la diana es sólida, así que la subo de prioridad.",
        ],
        "neg": [
            "Lo declarado y lo que corre ya coinciden; dos agentes suben a opus y dos a sonnet.",
            "Los cinco modelos coinciden en queratosis seborreica según la validación de la sesión.",
            "Las cinco lentes coinciden, pero son el mismo modelo: cuenta como un testigo, no cinco.",
            "Los tres comités no coinciden: el de acceso discrepa del de arquitectura.",
        ],
    },
}


class Punto08(unittest.TestCase):
    def test_positivos(self):
        for check, c in CASOS.items():
            for t in c["pos"]:
                self.assertIsNotNone(g.CHECKS[check](t), "%s NO cazó: %s" % (check, t[:70]))

    def test_negativos(self):
        for check, c in CASOS.items():
            for t in c["neg"]:
                self.assertIsNone(g.CHECKS[check](t), "%s falso positivo: %s" % (check, t[:70]))

    def test_registradas_activas_y_en_aviso(self):
        reglas = {r["check"]: r for r in normas.reglas_salida()}
        for check in CASOS:
            self.assertIn(check, reglas, "%s no está declarado en normas.json" % check)
            self.assertEqual(reglas[check]["modo"], "aviso",
                             "%s: la subida a bloqueo va por la escalera del gate" % check)
        activas = {c for c, _s, _m in g._reglas_activas()[0]}
        self.assertTrue(set(CASOS) <= activas, "no corren en el hook: %s" % (set(CASOS) - activas))

    def test_en_aviso_no_bloquean(self):
        texto = CASOS["cita_ia_buscador"]["pos"][0] + " Lo dejo en el mapa y seguimos."
        h = [x for x in g.revisar(texto) if x[0] == "cita_ia_buscador"]
        self.assertTrue(h)
        self.assertEqual(g._bloquean(h, "aviso"), [])


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    ok = r.wasSuccessful()
    print("✅ gate punto 08: %d tests en verde" % r.testsRun if ok
          else "❌ gate punto 08: %d fallo(s)" % (len(r.failures) + len(r.errors)))
    sys.exit(0 if ok else 1)
