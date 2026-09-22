#!/usr/bin/env python3
"""test_rc_turnos_agotados.py — quedarse sin turnos no es un fallo transitorio.

POR QUÉ EXISTE (31-jul-2026). El hallazgo `jobs_caidos:rc=1` llevaba **73 detecciones en 2 días**
sin que nadie supiera qué pasaba, porque la cola guardaba el número y no el motivo: `ultimo_error`
decía «rc=1» y nada más. La arqueología (registro de coste + horarios de los 3 intentos) dio la
causa: el CLI sale con `rc=1` y `subtype=error_max_turns` cuando el agente agota los turnos, y el
job `c4fb4d06fc` lo hizo 3 veces seguidas (30-jul 19:24 / 19:28 / 19:32) gastando 1,35 USD y
0,99 USD para acabar exactamente igual.

Dos cosas se arreglan y este test las fija:
  1. `errores.clasificar` manda `error_max_turns` a dead-letter en el primer intento. Reintentar
     el MISMO job con el MISMO presupuesto de turnos vuelve a agotarlos: es permanente.
  2. El dispatcher guarda el MOTIVO (`subtype` + `num_turns`), que ya tenía en la mano en el JSON
     del CLI, y no solo el número.

Verificado a mano el 31-jul contra el CLI real: `claude -p ... --max-turns 1` devuelve rc=1 con
`{"subtype": "error_max_turns", "is_error": true}`.
"""
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import errores  # noqa: E402

DISPATCHER = os.path.join(ROOT, "tools", "btp_dispatcher.sh")


class TurnosAgotadosNoSeReintenta(unittest.TestCase):
    def test_error_max_turns_no_se_reintenta(self):
        sev = errores.clasificar("rc=1 · error_max_turns · 25 turnos")
        self.assertEqual(sev, errores.CONFIG)
        self.assertFalse(errores.POLITICA[sev]["reintentar"],
                         "reintentar el mismo job con los mismos turnos vuelve a agotarlos")

    def test_el_rc_pelado_sigue_siendo_reintentable(self):
        """Sin motivo no se puede afirmar que sea permanente: se reintenta como antes."""
        self.assertNotEqual(errores.clasificar("rc=1"), errores.CONFIG)

    def test_timeout_sigue_siendo_transitorio(self):
        """La red que se cae SÍ merece reintento. No romper lo que ya funcionaba."""
        self.assertEqual(errores.clasificar("connection timed out"), errores.TRANSITORIO)

    def test_saldo_sigue_siendo_degradado(self):
        self.assertEqual(errores.clasificar("credit balance is too low"), errores.DEGRADADO)


class ElDispatcherGuardaElMotivo(unittest.TestCase):
    """No se puede diagnosticar lo que no se guarda: el JSON del CLI ya trae subtype y num_turns."""

    def setUp(self):
        with open(DISPATCHER, encoding="utf-8") as f:
            self.src = f.read()

    def test_extrae_subtype_y_turnos(self):
        self.assertRegex(self.src, r"jq -r '\.subtype // empty'")
        self.assertRegex(self.src, r"jq -r '\.num_turns // empty'")

    def test_mark_failed_recibe_el_motivo_no_solo_el_numero(self):
        self.assertIn('mark-failed --id "$id" --error "$err"', self.src)
        self.assertNotIn('mark-failed --id "$id" --error "rc=$rc"', self.src,
                         "volvió el 'rc=1' pelado: sin motivo no hay diagnóstico")

    def test_el_dispatcher_sigue_siendo_bash_valido(self):
        r = subprocess.run(["bash", "-n", DISPATCHER], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
