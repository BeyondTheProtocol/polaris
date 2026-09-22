#!/usr/bin/env python3
"""Test: el dispatcher no puede volver a matar los reintentos con rc=127.

POR QUÉ EXISTE (2-sep-2026, deuda `jobs_caidos:rc=127`, 129 detecciones en 36 días).

`btp_dispatcher.sh` lanzaba el agente así:

    OUT="$(MURO_PROFILE=… BTP_COST_GUARDED=1 ${turnos_job:+BTP_MAX_TURNS="$turnos_job"} "$RUN_AGENT" …)"

Bash decide qué palabra es una asignación de prefijo durante el PARSEO, antes de
expandir nada. `${turnos_job:+…}` empieza por `$`, así que nunca se reconoce como
NAME=VALUE: al expandir a `BTP_MAX_TURNS=50`, bash intentaba EJECUTARLO como si
fuera un comando y devolvía 127, «command not found».

Lo venenoso era cuándo saltaba. Con `turnos_job` vacío la expansión desaparece y
el intento 1 corre perfecto. Solo fallaba el REINTENTO de un job que ya había
perdido por error_max_turns, porque `cola.py mark_failed` pone turnos=50 y lo
reencola. Un bug que solo aparece en el segundo intento parece intermitente, y
por eso sobrevivió 36 días alimentando además `frescura_agente_fallo:tecnico` y
`frescura_agente_fallo:calendar-sync`.

El arreglo es `env`: recibe las asignaciones como argumentos normales y las
aplica DESPUÉS de expandir.

Este test comprueba las dos mitades: que el script sigue usando `env` en esa
invocación, y que la semántica de bash que lo provocó sigue siendo la que
creemos (si algún día bash cambiara, este test lo cantaría en vez de dejarnos
con una creencia caducada).
"""

import io
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DISPATCHER = os.path.join(ROOT, "tools", "btp_dispatcher.sh")


def _linea_de_invocacion():
    """La línea que lanza RUN_AGENT con las variables de entorno del job."""
    src = io.open(DISPATCHER, encoding="utf-8").read()
    for linea in src.split("\n"):
        if "$RUN_AGENT" in linea and "turnos_job" in linea:
            return linea
    return None


class DispatcherNoRompeLosReintentos(unittest.TestCase):
    def test_el_script_existe_y_es_sintacticamente_valido(self):
        self.assertTrue(os.path.exists(DISPATCHER), "falta tools/btp_dispatcher.sh")
        r = subprocess.run(["bash", "-n", DISPATCHER], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, "btp_dispatcher.sh no parsea:\n%s" % r.stderr)

    def test_la_invocacion_usa_env(self):
        """El corazón de la deuda: sin `env`, el reintento muere con rc=127."""
        linea = _linea_de_invocacion()
        self.assertIsNotNone(
            linea, "no encuentro la línea que invoca $RUN_AGENT con turnos_job. "
                   "Si se ha reescrito, actualiza este test a conciencia, no lo borres.")
        self.assertRegex(
            linea.strip(), r'^OUT="\$\(env\s',
            "la invocación de $RUN_AGENT tiene que ir por `env`. Sin él, "
            "${turnos_job:+BTP_MAX_TURNS=…} no es una asignación para bash (la palabra "
            "empieza por `$`, se decide en el parseo) y se ejecuta como comando → rc=127. "
            "Solo se nota en el REINTENTO de un job que perdió por error_max_turns.\n"
            "Línea actual: %s" % linea.strip())

    def test_la_semantica_de_bash_sigue_siendo_la_que_creemos(self):
        """Guardia contra una creencia caducada: reproduce el bug y el arreglo de verdad.

        Si un día bash empezara a aceptar esa forma como asignación, este test lo
        cantaría en vez de dejarnos protegiéndonos de un fantasma.
        """
        roto = 'turnos_job=50; ${turnos_job:+BTP_MAX_TURNS="$turnos_job"} /bin/echo hola'
        r = subprocess.run(["bash", "-c", roto], capture_output=True, text=True)
        self.assertEqual(r.returncode, 127,
                         "bash ya NO devuelve 127 aquí (rc=%d). La causa raíz de la deuda "
                         "`jobs_caidos:rc=127` ha cambiado: revisa si `env` sigue haciendo "
                         "falta antes de tocar nada." % r.returncode)

        arreglado = 'turnos_job=50; env ${turnos_job:+BTP_MAX_TURNS="$turnos_job"} /bin/echo hola'
        r = subprocess.run(["bash", "-c", arreglado], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, "con `env` debería funcionar:\n%s" % r.stderr)
        self.assertEqual(r.stdout.strip(), "hola")

        vacio = 'turnos_job=""; env ${turnos_job:+BTP_MAX_TURNS="$turnos_job"} /bin/echo hola'
        r = subprocess.run(["bash", "-c", vacio], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, "el intento 1 (turnos vacío) también tiene que correr")


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=0).run(
        unittest.TestLoader().loadTestsFromTestCase(DispatcherNoRompeLosReintentos))
    if res.wasSuccessful():
        print("✅ DISPATCHER EN VERDE (%d casos · los reintentos con BTP_MAX_TURNS no dan rc=127)"
              % res.testsRun)
    raise SystemExit(0 if res.wasSuccessful() else 1)
