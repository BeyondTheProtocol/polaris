#!/usr/bin/env python3
"""test_cola_turnos.py — si no cabía en los turnos, se sube de marcha; no se repite el techo.

POR QUÉ EXISTE (31-jul-2026). Complemento de `test_rc_turnos_agotados.py`, que fija el
DIAGNÓSTICO (guardar el motivo y no reintentar a ciegas). Esto fija la otra mitad: que el trabajo
LLEGUE A HACERSE. `error_max_turns` no significa que el encargo esté mal, significa que no cabía
en 25 turnos. Antes se reintentaba idéntico 3 veces (medido: 2,34 USD para acabar igual) y luego
dead-letter. Ahora se dobla el presupuesto UNA vez y se reintenta de verdad.

Regla suya que lo justifica: el coste nunca corta el camino a NED — se baja de marcha o se pide
aprobación, no se para. Aquí se sube de marcha, con tope.

El campo `turnos` nace SIEMPRE ausente y lo escribe solo `mark_failed`, igual que `historial`:
ningún productor lo emite, así que no rompe el contrato consumer-first.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import cola as q  # noqa: E402

DISPATCHER = os.path.join(ROOT, "tools", "btp_dispatcher.sh")


class PresupuestoDeTurnos(unittest.TestCase):
    def setUp(self):
        q.QUEUE = os.path.join(tempfile.mkdtemp(prefix="test_turnos_"), "queue")
        q._ensure_dirs()

    def _job_en_curso(self, intencion="trabajo largo"):
        q.enqueue(intencion, procedencia="t")
        return q.dequeue()

    def test_nace_sin_turnos(self):
        """Ningún productor emite el campo: los jobs viejos siguen siendo válidos."""
        j = self._job_en_curso()
        self.assertIsNone(j.get("turnos"))

    def test_turnos_agotados_sube_de_marcha_y_reencola(self):
        j = self._job_en_curso()
        res = q.mark_failed(j, "rc=1 · error_max_turns · 25 turnos")
        self.assertIn("reencolado", res)
        vuelto = q.dequeue()
        self.assertEqual(vuelto["turnos"], 50)

    def test_no_gasta_intento_al_subir_de_marcha(self):
        """El techo no es culpa del trabajo: no puede consumir uno de los 3 intentos."""
        j = self._job_en_curso()
        antes = j["intentos"]
        q.mark_failed(j, "rc=1 · error_max_turns · 25 turnos")
        self.assertEqual(q.dequeue()["intentos"], antes)

    def test_una_sola_escalada_y_luego_dead_letter(self):
        j = self._job_en_curso()
        q.mark_failed(j, "rc=1 · error_max_turns · 25 turnos")     # 25 → 50
        j2 = q.dequeue()
        q.mark_failed(j2, "rc=1 · error_max_turns · 50 turnos")    # 50 → 60 (tope)
        j3 = q.dequeue()
        self.assertEqual(j3["turnos"], q.TURNOS_TOPE)
        res = q.mark_failed(j3, "rc=1 · error_max_turns · 60 turnos")
        self.assertEqual(res, "dead-letter", "en el tope ya no se sube más: se cuenta y se para")

    def test_otros_errores_se_comportan_igual_que_antes(self):
        """No romper lo que funcionaba: un error normal gasta intento y no toca los turnos."""
        j = self._job_en_curso()
        q.mark_failed(j, "rc=1")
        vuelto = q.dequeue()
        self.assertEqual(vuelto["intentos"], 1)
        self.assertIsNone(vuelto.get("turnos"))

    def test_el_schema_acepta_el_campo(self):
        j = self._job_en_curso()
        j.pop("_path", None)                       # clave interna del dequeue, no del schema
        j["turnos"] = 50
        self.assertIsNone(q._validate(j), "un job con turnos no puede irse a failed/ por schema")


class ElDispatcherPasaElPresupuesto(unittest.TestCase):
    def setUp(self):
        with open(DISPATCHER, encoding="utf-8") as f:
            self.src = f.read()

    def test_lee_el_campo(self):
        self.assertRegex(self.src, r"jq -r '\.turnos // empty'")

    def test_solo_exporta_si_lo_hay(self):
        """Sin presupuesto, run_agent.sh sigue con su default (25 rutina / 60 crítico)."""
        self.assertIn('${turnos_job:+BTP_MAX_TURNS="$turnos_job"}', self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
