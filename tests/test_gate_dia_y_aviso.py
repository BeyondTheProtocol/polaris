#!/usr/bin/env python3
"""test_gate_dia_y_aviso.py — dos normas de salida que estaban sin mecanismo y ahora lo tienen.

  · `dia_semana_mal` (feedback-dia-de-la-semana-calculado-no-escrito): el 8-oct-26 escribí
    «mañana jueves» siendo jueves; mañana era viernes. El caso real es el primer positivo.
  · `aviso_sin_novedad` (feedback-aviso-condicionado-no-parte-de-estado): tras «avísame cuando
    respondan» le di un parte de «todavía nadie ha respondido».

Hoy se fija a 2026-10-08 (jueves) con BTP_HOY, para que el test no dependa del calendario real.
Positivos (tiene que cazar), negativos (no puede cazar) y contrato (registrado, activo, en aviso).
"""
import datetime
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ["BTP_HOY"] = "2026-10-08"
import gate_salida as g  # noqa: E402
import normas  # noqa: E402

RELLENO = " Te dejo el resto en la nota archivada para cuando tengas un rato."


class DiaSemana(unittest.TestCase):
    def test_calendario_de_referencia(self):
        """Ancla del test: si esto falla, los casos de abajo no significan nada."""
        self.assertEqual(datetime.date(2026, 10, 8).weekday(), 3)       # jueves
        self.assertEqual(datetime.date(2026, 10, 9).weekday(), 4)       # viernes

    def test_positivos(self):
        for t in (
            "Tu cita queda confirmada para mañana jueves a las 9.",                  # el caso real
            "Hoy viernes tienes la analítica a las 10.",
            "Fue ayer martes cuando llegó el informe.",                                # ayer fue miércoles 7
            "La cita es el viernes 8 de octubre a las 9:00.",                          # 8-oct es jueves
            "La cita es el jueves 9 de octubre a las 9:00.",
            "Cita: jueves, 2026-10-09, 9:00.",
            "Nos vemos el 9 de octubre (jueves) en el hospital.",
            "Resultado el 2026-10-09 (lunes).",
            "Plazo: 2026-10-09, jueves.",                                              # forma de master
            "Tienes consulta el lunes 10/10 por la tarde.",                            # 10-oct-26 es sábado
            "Pasado mañana lunes tienes la prueba.",                                    # pasado mañana = sábado
            "Tu cita del domingo 21 de octubre de 2026 sigue en pie.",                 # 21-oct-26 es miércoles
        ):
            self.assertIsNotNone(g.dia_semana_mal(t), "NO cazó: " + t)

    def test_negativos(self):
        for t in (
            "Tu cita queda confirmada para mañana viernes a las 9.",
            "Hoy jueves tienes la analítica a las 10.",
            "La cita es el jueves 8 de octubre a las 9:00.",
            "La cita es el viernes 9 de octubre a las 9:00.",
            "Cita: viernes, 2026-10-09, 9:00.",
            "Nos vemos el 9 de octubre (viernes) en el hospital.",
            "Tienes consulta el sábado 10/10 por la tarde.",
            "Pasado mañana sábado tienes la prueba.",
            "El miércoles 21 de octubre de 2026 es la cita con {{CONTACTO}}.",
            # fecha histórica sin año, dentro del último año: se acepta su ocurrencia real
            "Se lo dije el sábado 25 de julio, antes de irme.",
            # «mañana» como parte del día, no como fecha
            "Esta mañana lunes no pude, pero por la mañana jueves sí.",
            # replay 30 d: dos cláusulas, no «mañana = martes»
            "Si el presupuesto no llega mañana y no se paga mañana, el martes se cae del todo.",
            # lo entrecomillado con «» es una cita del error, no una afirmación
            "El fallo fue escribir «mañana jueves» siendo jueves; ahora se calcula.",
            # código: no es prosa
            "```\nprint('mañana jueves')\n```\nHecho.",
            # día sin mes ni fecha completa: no hay calendario contra el que contrastar
            "Quedamos el lunes 12 si te viene bien.",
            # una fracción no es una fecha
            "Pasaron 2/3 del tiempo y el jueves 2/3 de los casos se resolvieron.",
            "Sin ninguna fecha ni día de la semana en esta respuesta.",
        ):
            self.assertIsNone(g.dia_semana_mal(t), "falso positivo: " + t)

    def test_con_fecha_de_hoy_distinta(self):
        """El mismo texto es correcto o no según el hoy real: no hay un día fijado en el código."""
        os.environ["BTP_HOY"] = "2026-10-09"
        try:
            self.assertIsNone(g.dia_semana_mal("Hoy viernes tienes la cita."))
            self.assertIsNotNone(g.dia_semana_mal("Mañana jueves tienes la cita."))
        finally:
            os.environ["BTP_HOY"] = "2026-10-08"

    def test_corre_en_respuesta_corta_via_revisar(self):
        h = [c for c, _s, _m in g.revisar("Mañana jueves a las 9.")]
        self.assertIn("dia_semana_mal", h)


class AvisoSinNovedad(unittest.TestCase):
    def setUp(self):
        self._suyos = g.SUYOS

    def tearDown(self):
        g.SUYOS = self._suyos

    def test_positivos(self):
        g.SUYOS = ["Avísame cuando respondan a los correos de hoy."]
        for t in (
            "Vigilancia de correos de hoy activada. Estado actual: sin respuestas a correos de hoy.",
            "Todavía nadie ha respondido a los correos de hoy; sigo mirando.",
            "Sin novedades por ahora.",
            "Armado. Aún no ha llegado nada, te digo.",
        ):
            self.assertIsNotNone(g.aviso_sin_novedad(t), "NO cazó: " + t)
        g.SUYOS = ["Hola", "avisame en cuanto salga el resultado de la analítica"]
        self.assertIsNotNone(g.aviso_sin_novedad("De momento sin novedades."))

    def test_negativos(self):
        g.SUYOS = ["Avísame cuando respondan a los correos de hoy."]
        for t in (
            "Vigilado. Te aviso en cuanto responda alguien.",                              # la confirmación buena
            "Sin respuestas, pero ojo: las credenciales del buzón han caducado y no puedo mirar los correos.",
            "Sin novedades que dar: no puedo vigilar el buzón ahora mismo, sin acceso al servidor de correo.",
            "Todavía nadie ha respondido, salvo Ana, que ha contestado hace cinco minutos con el informe.",
            ("Informe completo de la mañana. " * 40) + "Sin novedades en el resto.",           # un informe, no un parte
        ):
            self.assertIsNone(g.aviso_sin_novedad(t), "falso positivo: " + t[:70])

    def test_si_ella_pregunta_se_contesta(self):
        g.SUYOS = ["Avísame cuando respondan. ¿Ha respondido ya alguien?"]
        self.assertIsNone(g.aviso_sin_novedad("Todavía nadie ha respondido."))

    def test_sin_aviso_armado_no_opina(self):
        g.SUYOS = ["Mira a ver si han respondido a los correos de hoy."]
        self.assertIsNone(g.aviso_sin_novedad("Todavía nadie ha respondido."))
        g.SUYOS = ["Avísame cuando respondan.", "Cámbiame el nombre del fichero."]
        self.assertIsNone(g.aviso_sin_novedad("Sin novedades en el renombrado."))
        g.SUYOS = None
        self.assertIsNone(g.aviso_sin_novedad("Sin novedades por ahora."))
        g.SUYOS = []
        self.assertIsNone(g.aviso_sin_novedad("Sin novedades por ahora."))


class Contrato(unittest.TestCase):
    CHECKS = {"dia_semana_mal": "feedback-dia-de-la-semana-calculado-no-escrito",
              "aviso_sin_novedad": "feedback-aviso-condicionado-no-parte-de-estado"}

    def test_registrados_activos_y_en_aviso(self):
        reglas = {r["check"]: r for r in normas.reglas_salida()}
        activas = {c for c, _s, _m in g._reglas_activas()[0]}
        for check, slug in self.CHECKS.items():
            self.assertIn(check, reglas, "%s no está declarado en normas.json" % check)
            self.assertEqual(reglas[check]["slug"], slug)
            self.assertEqual(reglas[check]["modo"], "aviso", "subir a bloqueo exige el OK de {{TITULAR}}")
            self.assertIn(check, activas)

    def test_en_aviso_no_bloquean(self):
        g.SUYOS = None
        h = [x for x in g.revisar("Mañana jueves a las 9.") if x[0] == "dia_semana_mal"]
        self.assertTrue(h)
        self.assertEqual(g._bloquean(h, "aviso"), [])


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    ok = r.wasSuccessful()
    print("✅ gate día/aviso: %d tests en verde" % r.testsRun if ok
          else "❌ gate día/aviso: %d fallo(s)" % (len(r.failures) + len(r.errors)))
    sys.exit(0 if ok else 1)
