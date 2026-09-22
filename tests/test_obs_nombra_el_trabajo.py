#!/usr/bin/env python3
"""test_obs_nombra_el_trabajo.py — la bitácora tiene que decir QUÉ trabajo corrió, no solo quién.

POR QUÉ (31-jul-2026). Varios plists invocan al MISMO agente para trabajos distintos:
`com.btp.correo` corre con `--agent asistente` pero es el pase de correo (por eso lleva
`BTP_HEARTBEAT_NAME=correo-triaje`). La bitácora registraba `agente=asistente, job=run_agent`
para los dos, así que al contar fallos salía que «el asistente falló 58 veces en julio» y el
libro de deuda abrió un hallazgo culpando a Vega.

Al desglosar por hora, 22 de esos fallos eran de las 08:20 y las 14:00 — las horas de
`com.btp.correo`, no de Vega. Un número mal atribuido no es un número flojo: manda a arreglar lo
que no está roto, que cuesta lo mismo que arreglar lo que sí.

Este test ejecuta el MISMO bloque de Python que va incrustado en `run_agent.sh` (se extrae del
heredoc `_OBS_PY`), porque probar el script entero exige la clave del Llavero y aquí lo que
importa es la línea que se escribe.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_AGENT = os.path.join(ROOT, "tools", "run_agent.sh")


def _bloque_obs():
    """El cuerpo del heredoc `_OBS_PY` tal cual viaja en run_agent.sh."""
    txt = open(RUN_AGENT, encoding="utf-8").read()
    # El marcador lleva cola en su línea (`<<'_OBS_PY' || true`), así que no se puede anclar al \n.
    m = re.search(r"<<'_OBS_PY'[^\n]*\n(.*?)\n_OBS_PY", txt, re.S)
    assert m, "no encuentro el bloque _OBS_PY en run_agent.sh"
    return m.group(1)


class BitacoraNombraElTrabajo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="obs-job-")
        self.script = os.path.join(self.tmp, "obs.py")
        with open(self.script, "w", encoding="utf-8") as f:
            f.write(_bloque_obs())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _correr(self, *args):
        env = dict(os.environ, BTP_STATE_DIR=self.tmp)
        subprocess.run([sys.executable, self.script, os.path.join(ROOT, "tools"), *args],
                       env=env, capture_output=True, text=True, timeout=30)
        filas = []
        for f in glob.glob(os.path.join(self.tmp, "observabilidad", "*.jsonl")):
            for ln in open(f, encoding="utf-8"):
                filas.append(json.loads(ln))
        return filas

    def test_el_job_lleva_el_nombre_del_trabajo(self):
        filas = self._correr("asistente", "sonnet", "2026-07-31T08:20:00Z", "fail", "correo-triaje")
        self.assertEqual(1, len(filas))
        self.assertEqual("asistente", filas[0]["agente"])
        self.assertEqual("run_agent:correo-triaje", filas[0]["job"],
                         "sin esto, el pase de correo y Vega son indistinguibles en la bitácora")
        self.assertEqual("fail", filas[0]["resultado"])

    def test_los_dos_trabajos_del_mismo_agente_se_distinguen(self):
        self._correr("asistente", "sonnet", "2026-07-31T07:55:00Z", "ok", "asistente")
        filas = self._correr("asistente", "sonnet", "2026-07-31T08:20:00Z", "fail", "correo-triaje")
        jobs = {f["job"]: f["resultado"] for f in filas}
        self.assertEqual({"run_agent:asistente": "ok", "run_agent:correo-triaje": "fail"}, jobs)

    def test_sin_nombre_de_trabajo_no_rompe_nada(self):
        """Compatibilidad: una invocación vieja sin el 6º argumento sigue registrando."""
        filas = self._correr("orquestador", "sonnet", "2026-07-31T08:10:00Z", "ok")
        self.assertEqual("run_agent", filas[0]["job"])

    def test_run_agent_pasa_el_nombre_del_latido(self):
        """El cableado en el .sh: si alguien quita el argumento, el bloque queda ciego otra vez."""
        txt = open(RUN_AGENT, encoding="utf-8").read()
        self.assertIn('"$_OBS_RESULTADO" "$HB_NAME"', txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
