#!/usr/bin/env python3
"""test_gate_dia_semana.py — `dia_semana_mal`: el día de la semana cuadra con el calendario.

Norma `feedback-dia-de-la-semana-calculado-no-escrito`. Caso real, 8-oct-26 (jueves): «mañana
jueves a las 9» para una cita que era el viernes 9. Aquí «hoy» se fija a ese día.
"""
import datetime
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gate_salida as g  # noqa: E402
import normas  # noqa: E402

LARGO = " Te dejo el resto del detalle en la nota archivada para cuando tengas un rato." * 2


class DiaSemana(unittest.TestCase):
    def setUp(self):
        self._hoy = g._hoy
        g._hoy = lambda: datetime.date(2026, 10, 8)      # jueves

    def tearDown(self):
        g._hoy = self._hoy

    def test_positivos(self):
        for t in ["Tu cita es mañana jueves a las 9.",             # el caso real
                  "Hoy viernes tienes la analítica.",
                  "Pasado mañana jueves vuelves a consulta.",
                  "Cita el viernes 8 de octubre en el {{CENTRO}}.",
                  "Cita el jueves 9 de octubre de 2026.",
                  "Plazo: 2026-10-09, jueves.",
                  "El martes 2026-10-12 tienes la prueba."]:      # el 12 es lunes
            self.assertIsNotNone(g.dia_semana_mal(t + LARGO), t)

    def test_negativos(self):
        for t in ["Tu cita es mañana viernes a las 9.",
                  "Hoy jueves tienes la analítica.",
                  "Pasado mañana sábado vuelves a consulta.",
                  "Cita el jueves 8 de octubre en el {{CENTRO}}.",
                  "Cita el viernes 9 de octubre de 2026.",
                  "Plazo: 2026-10-09 (viernes).",
                  "El jueves de la semana que viene lo vemos.",       # sin fecha pegada
                  "Mañana por la mañana te lo mando.",
                  "El 5 de octubre de 2026 fue lunes; mañana es viernes.",
                  "```\nmañana jueves\n```"]:
            self.assertIsNone(g.dia_semana_mal(t + LARGO), t)

    def test_sin_ano_toma_el_mas_cercano(self):
        # 31 dic 2026 es jueves; hoy = 2 ene 2027 (sábado): «jueves 31 de diciembre» es 2026.
        g._hoy = lambda: datetime.date(2027, 1, 2)
        self.assertIsNone(g.dia_semana_mal("Fue el jueves 31 de diciembre." + LARGO))
        self.assertIsNotNone(g.dia_semana_mal("Fue el viernes 31 de diciembre." + LARGO))

    def test_fecha_imposible_no_revienta(self):
        self.assertIsNone(g.dia_semana_mal("El jueves 31 de febrero." + LARGO))

    def test_registrado_en_aviso_y_activo(self):
        r = {x["check"]: x for x in normas.reglas_salida()}
        self.assertEqual(r["dia_semana_mal"]["slug"], "feedback-dia-de-la-semana-calculado-no-escrito")
        self.assertEqual(r["dia_semana_mal"]["modo"], "aviso", "bloqueo exige el OK de {{TITULAR}}")
        self.assertIn("dia_semana_mal", {c for c, _s, _m in g._reglas_activas()[0]})
        self.assertTrue(g.revisar("Tu cita es mañana jueves a las 9." + LARGO))


if __name__ == "__main__":
    unittest.main()
