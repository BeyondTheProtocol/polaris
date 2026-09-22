#!/usr/bin/env python3
"""test_lentes.py — el enrutador de reserva: calla con la charla, sienta lentes con las decisiones.

Paso 4 del plan de la caja (13-sep-2026). Lo que protege, por orden:
  1. Que NO grite: charla, agradecimientos y preguntas sueltas no dan panel. Un fallback que salta
     con cualquier cosa se ignora, y entonces no sirve ni cuando toca.
  2. Que `contra` (verificacion) esté SIEMPRE, y que el panel tenga entre 3 y 5 asientos.
  3. Que no reproduzca el fallo medido en el plan: `capacidades` sentaba a `git` para una mudanza.
  4. Que sea SUGERIDO: con la tabla sin comité, la decisión de `decide_peticion` (nivel y orden) no
     cambia; el panel solo se apunta, y solo al guardar el plan real (no al evaluar).
  5. Que la tabla mande: si una regla ya da comité, no hay fallback.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))

TMP = tempfile.mkdtemp(prefix="lentes-test-")
os.environ["BTP_STATE_DIR"] = TMP

import lentes  # noqa: E402
import decide_peticion as dp  # noqa: E402

MUDANZA = ("quiero mudarme de casa antes de noviembre, cuesta 900 € más al mes y me pilla lejos "
           "del hospital, ¿qué hago?")
SIN_RED = {}


class TestLentes(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP, ignore_errors=True)

    # ── 1. silencio ─────────────────────────────────────────────────────────────────────
    def test_la_charla_no_da_panel(self):
        for p in ("gracias", "vale, perfecto", "qué tal el día", "ok", "jajaja qué bueno",
                  "¿y esto qué es?", "Dale", "Opcion 1"):
            self.assertEqual([], lentes.panel(p), p)

    def test_encargo_sin_plazo_ni_consecuencia_no_da_panel(self):
        self.assertEqual([], lentes.panel("quiero ver una película"))

    # ── 2. forma del panel ──────────────────────────────────────────────────────────────
    def test_decision_con_peso_da_panel_con_contra_siempre(self):
        asientos = lentes.panel(MUDANZA)
        self.assertTrue(3 <= len(asientos) <= 5, asientos)
        self.assertIn(("contra", "verificacion"), [(a["lente"], a["comite"]) for a in asientos])
        lentes_vistas = [a["lente"] for a in asientos]
        self.assertIn("impacto-ned", lentes_vistas)
        self.assertIn("coste-plazo", lentes_vistas, "hay dinero en juego")

    def test_sin_dinero_ni_acceso_sigue_habiendo_tres_asientos(self):
        asientos = lentes.panel("tengo que decidir antes del viernes si me apunto al curso de cerámica")
        self.assertTrue(3 <= len(asientos) <= 5, asientos)
        self.assertEqual("contra", asientos[0]["lente"] if asientos[0]["lente"] != "dominio"
                         else asientos[1]["lente"])

    def test_nunca_mas_de_cinco(self):
        texto = ("necesito conseguir cita con el hospital antes del lunes, cuesta 300 € y quiero "
                 "decidir si contratar el seguro o pedir recomendación")
        self.assertLessEqual(len(lentes.panel(texto)), 5)

    # ── 3. el fallo medido ──────────────────────────────────────────────────────────────
    def test_git_no_se_sienta_a_planificar_una_mudanza(self):
        comites = [a["comite"] for a in lentes.panel(MUDANZA)]
        self.assertNotIn("git", comites)
        self.assertNotIn("tecnico", comites)

    def test_los_comites_de_las_lentes_existen(self):
        for slug in ("verificacion", "asistente", "finanzas-transparencia", "consejero-acceso"):
            self.assertTrue(os.path.isfile(os.path.join(RAIZ, ".claude", "agents", slug + ".md")), slug)

    # ── 4. sugerido, no ordenado ────────────────────────────────────────────────────────
    def test_decide_peticion_no_cambia_la_orden_con_el_fallback(self):
        d = dp.decidir(MUDANZA, estado=SIN_RED)
        self.assertEqual([], d["comites"], "la tabla no da comité: el fallback no lo inventa")
        self.assertIn(d["nivel"], (dp.DIRECTO, dp.LLM))
        self.assertTrue(d.get("sugerido_fallback"), "el panel queda calculado")
        self.assertNotIn("verificacion", d["orden"], "pero NO se ordena")

    def test_se_apunta_al_guardar_y_no_al_evaluar(self):
        log = os.path.join(TMP, "enrutado", "fallback.jsonl")
        dp.decidir(MUDANZA, estado=SIN_RED)
        self.assertFalse(os.path.exists(log), "decidir (eval, tests) no ensucia la medición")
        dp.guardar("sesion-de-prueba", dp.decidir(MUDANZA, estado=SIN_RED))
        with open(log, encoding="utf-8") as f:
            fila = json.loads(f.read().strip().splitlines()[-1])
        self.assertEqual("sesion-de-prueba"[:8], fila["sesion"])
        self.assertIn("verificacion", [a["comite"] for a in fila["panel"]])

    # ── 5. la tabla manda ───────────────────────────────────────────────────────────────
    def test_si_la_tabla_da_comite_no_hay_fallback(self):
        d = dp.decidir("analiza el informe molecular de hueso antes del lunes, cuesta 300 €",
                       estado=SIN_RED)
        self.assertTrue(d["comites"])
        self.assertFalse(d.get("sugerido_fallback"))


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=0).result
    if res.wasSuccessful():
        print("✅ LENTES EN VERDE (%d casos)" % res.testsRun)
    sys.exit(0 if res.wasSuccessful() else 1)
