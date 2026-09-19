#!/usr/bin/env python3
"""test_gate_preclinico.py — un resultado en ratones no sale con cara de que «funciona».

POR QUÉ EXISTE (17-sep-2026). La regla 17 del protocolo —etiquetar el tier de evidencia y
no aplanar nunca un preclínico en «esto funciona»— vivía SOLO en el prompt. El gate ya sabía
cazar una cita FABRICADA, pero no una cita REAL usada para sostener una promesa que el
estudio no sostiene: un xenoinjerto citado bajo «esto reduce el tumor» salía limpio.

El daño es asimétrico y peor que el de la cita inventada: una cita falsa se desmiente
enseñando el registro; una contacto construida sobre un estudio en ratones ya se ha leído.

Fija las propiedades del check, sin tocar la red (subprocess mockeado):
  1. Afirmar eficacia sobre una cita preclínica sin nombrarlo → se caza.
  2. Si la frase YA dice «en ratones» / «in vitro», la norma se cumple y el check CALLA.
  3. Describir sin prometer no se toca (no es el check de estilo).
  4. Una cita clínica (RCT) no dispara nada.
  5. Red caída o tier desconocido → FAIL-OPEN, nunca frenar por la red.
  6. Sin PMID no se consulta el registro (ni coste ni latencia).
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gate_salida as g  # noqa: E402

# Respuestas sintéticas (>MIN_CHARS). El PMID es inventado a propósito: el registro se mockea.
APLANADO = ("Sobre la vía que preguntabas, hay trabajo que apunta a que el inhibidor funciona y "
            "reduce el tumor de forma consistente (PMID: 41299692), así que puede valer la pena "
            "llevárselo a tu oncóloga en la visita del mes que viene y preguntarle por ello.")
ETIQUETADO = ("Sobre la vía que preguntabas, el inhibidor funciona y reduce el tumor EN RATONES, "
              "en modelo de xenoinjerto (PMID: 41299692); en personas no se ha probado todavía, "
              "así que sirve para preguntar, no para pedir el fármaco en la próxima visita.")
DESCRIPTIVO = ("El trabajo (PMID: 41299692) mide el volumen tumoral a las seis semanas y describe "
               "el comportamiento de la vía, sin más lectura por ahora. Te lo dejo apuntado en la "
               "fuente de verdad para cuando toque revisar el bloque de dianas con calma.")
SIN_CITA = ("He fusionado la rama y la suite está en verde: 211 comprobaciones del muro sin fallos. "
            "Esto funciona y reduce el tiempo de cada turno a la mitad, así que lo dejo activo y "
            "te lo cuento mañana con el resto del estado del sistema.")


class _Resp:
    def __init__(self, out):
        self.stdout = out
        self.returncode = 0


def _mock(preclinico, etiqueta="modelo animal (PRECLÍNICO)"):
    """Sustituye la llamada real a tier_evidencia.py por un veredicto fijo."""
    def run(cmd, **kw):
        ids = [a for a in cmd[3:]]
        return _Resp(json.dumps([{"id": i, "tier": "modelo_animal" if preclinico else "rct",
                                  "etiqueta": etiqueta, "preclinico": preclinico,
                                  "entregable": True, "motivo": "test"} for i in ids]))
    return run


class GatePreclinico(unittest.TestCase):
    def setUp(self):
        self._real = g.subprocess.run
        # Si falta el clasificador el check sale por la puerta de atrás y queda decorativo.
        self.assertTrue(os.path.exists(os.path.join(g.REPO, "tools", "tier_evidencia.py")),
                        "falta tools/tier_evidencia.py: el check quedaría inerte")

    def tearDown(self):
        g.subprocess.run = self._real

    def test_afirmar_eficacia_sobre_un_preclinico_se_caza(self):
        g.subprocess.run = _mock(True)
        motivo = g.preclinico_aplanado(APLANADO)
        self.assertIsNotNone(motivo)
        self.assertIn("PRECLÍNICA", motivo)

    def test_si_la_frase_ya_dice_en_ratones_el_check_calla(self):
        """La norma no es «no cites preclínico», es «di que lo es». Cumplida → silencio."""
        g.subprocess.run = _mock(True)
        self.assertIsNone(g.preclinico_aplanado(ETIQUETADO))

    def test_describir_sin_prometer_no_se_toca(self):
        g.subprocess.run = _mock(True)
        self.assertIsNone(g.preclinico_aplanado(DESCRIPTIVO))

    def test_una_cita_clinica_no_dispara(self):
        g.subprocess.run = _mock(False, "ensayo aleatorizado (RCT)")
        self.assertIsNone(g.preclinico_aplanado(APLANADO))

    def test_red_caida_es_fail_open(self):
        def boom(*a, **k):
            raise OSError("red caída")
        g.subprocess.run = boom
        self.assertIsNone(g.preclinico_aplanado(APLANADO))

    def test_tier_desconocido_no_frena(self):
        """Un paper recién indexado aún no tiene MeSH. Bloquear ahí sería ruido constante."""
        def run(cmd, **kw):
            return _Resp(json.dumps([{"id": "x", "tier": "desconocido", "preclinico": False,
                                      "entregable": False}]))
        g.subprocess.run = run
        self.assertIsNone(g.preclinico_aplanado(APLANADO))

    def test_sin_pmid_no_toca_la_red(self):
        def boom(*a, **k):
            raise AssertionError("no debe consultar el registro sin PMID en el texto")
        g.subprocess.run = boom
        self.assertIsNone(g.preclinico_aplanado(SIN_CITA))

    def test_check_registrado_en_el_gate(self):
        self.assertIn("preclinico_aplanado", g.CHECKS)

    def test_check_registrado_como_norma(self):
        """Sin norma en tools/normas.json el check no se activa: quedaría decorativo."""
        import normas
        checks = [r["check"] for r in normas.reglas_salida()]
        self.assertIn("preclinico_aplanado", checks)

    def test_bloquea_aunque_el_gate_global_este_en_aviso(self):
        """Su modo propio manda: una contacto falsa, una vez leída, tampoco se deshace."""
        self.assertEqual(g._bloquean([("preclinico_aplanado", "s", "m")], "aviso"),
                         ["preclinico_aplanado"])


if __name__ == "__main__":
    unittest.main(verbosity=0)
