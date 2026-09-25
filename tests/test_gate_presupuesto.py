#!/usr/bin/env python3
"""test_gate_presupuesto.py — el gate de salida acaba ANTES de que el harness lo mate.

Deuda `gate_salida_timeout_desalineado` (25-sep-26). La vio consejero-arquitectura al revisar el
punto de rotura 08 (Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión
del 25-sep-2026): `settings.json` da 8 s al hook Stop y cada llamada de red del gate tenía 20 s
propios. Con el registro lento, el harness mataba el hook y se perdían en silencio TODOS los
checks del turno, incluido el aviso de «cita sin verificar».

Aquí se simula lo peor: los tres verificadores externos (citas, preclínico, cifras) se cuelgan
30 s. El gate tiene que volver dentro de su presupuesto, con hallazgos PENDIENTES y no con
«verificado», y el margen contra el timeout de `settings.json` tiene que seguir existiendo.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gate_salida as g  # noqa: E402

MARGEN = 2.0   # arranque de Python + lectura del transcript + regex, medido holgado

COLGADO = "import time\ntime.sleep(30)\n"
TEXTO = ("El fármaco funciona en {{DIAGNOSTICO}}: respuesta objetiva del 45 % "
         "(PMID: 12345678), así que encaja con el caso y lo subo en la lista de opciones.")


def _timeout_del_hook():
    d = json.load(open(os.path.join(ROOT, ".claude", "settings.json")))
    for bloque in d.get("hooks", {}).get("Stop", []):
        for h in bloque.get("hooks", []):
            if "gate_salida.py" in h.get("command", ""):
                return h.get("timeout")
    return None


class Presupuesto(unittest.TestCase):
    def test_el_presupuesto_cabe_en_el_timeout_del_hook(self):
        t = _timeout_del_hook()
        self.assertIsNotNone(t, "no encuentro gate_salida.py en los hooks Stop de settings.json")
        self.assertLessEqual(g.PRESUPUESTO_RED + MARGEN, t,
                             "PRESUPUESTO_RED=%.1f s + %.1f de margen no cabe en los %s s del hook"
                             % (g.PRESUPUESTO_RED, MARGEN, t))

    def test_no_queda_timeout_por_llamada_suelto(self):
        src = open(os.path.join(ROOT, ".claude", "hooks", "gate_salida.py")).read()
        self.assertNotIn("TIMEOUT_CITAS", src, "vuelve a haber un timeout por llamada fuera del reloj común")


class VerificadoresColgados(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "tools"))
        for f in ("verifica_citas.py", "tier_evidencia.py", "soporte_cita.py"):
            with open(os.path.join(self.tmp, "tools", f), "w") as fh:
                fh.write(COLGADO)
        self.repo = g.REPO
        g.REPO = self.tmp

    def tearDown(self):
        g.REPO = self.repo
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_vuelve_a_tiempo_y_sin_dar_nada_por_verificado(self):
        t0 = time.monotonic()
        h = g.revisar(TEXTO)
        dur = time.monotonic() - t0
        self.assertLess(dur, g.PRESUPUESTO_RED + 1.0,
                        "el gate tardó %.1f s con los verificadores colgados" % dur)
        red = [x for x in h if x[0] in ("citas_fabricadas", "preclinico_aplanado", "cita_no_respalda")]
        self.assertTrue(red, "con la red colgada tiene que salir un aviso PENDIENTE, no silencio: %s" % h)
        self.assertEqual(g._bloquean(red, "aviso"), [],
                         "un PENDIENTE por red no puede bloquear")

    def test_el_reloj_es_por_respuesta(self):
        g.revisar(TEXTO)                       # gasta el presupuesto entero
        g.REPO = self.repo                     # verificadores de verdad no hacen falta: solo el reloj
        g.revisar("Hecho.")
        self.assertGreater(g._queda(), g.PRESUPUESTO_RED - 0.5,
                           "el presupuesto no se reinició en la respuesta siguiente")


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    ok = r.wasSuccessful()
    print("✅ presupuesto del gate: %d tests en verde" % r.testsRun if ok
          else "❌ presupuesto del gate: %d fallo(s)" % (len(r.failures) + len(r.errors)))
    sys.exit(0 if ok else 1)
