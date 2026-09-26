#!/usr/bin/env python3
"""test_healthcheck_cpu.py — la CPU saturada avisa, un pico no.

POR QUÉ EXISTE (26-sep-26, deuda `healthcheck_sin_cpu`). Tres Chrome headless huérfanos pasaron
~27 h al 100 % de CPU: la carga del Mac en 46-82 con 10 núcleos y todo Polaris lento. Nadie
avisó, porque `_check_recursos` solo mira memoria y swap. `_check_cpu` cubre la clase entera.

Se fija:
  · carga alta DOS vueltas seguidas → aviso, con quién gasta;
  · una sola vuelta alta (un build, una suite) → no avisa;
  · carga normal → no avisa;
  · throttle: la tercera vuelta alta seguida, dentro de la hora, no repite el aviso;
  · si no se puede leer la carga → ni aviso ni excepción.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ["BTP_STATE_DIR"] = tempfile.mkdtemp(prefix="hc_cpu_test_")
os.environ["BTP_TEST_BATTERY"] = "1"
import healthcheck as hc  # noqa: E402

TOP = [(100.0, 60159, "Google Chrome Helper (Renderer)"), (99.0, 25927, "Google Chrome Helper")]


def _medida(carga):
    return lambda: (carga, 10, TOP)


class CheckCpu(unittest.TestCase):

    def setUp(self):
        try:
            os.remove(os.path.join(hc.HC, "cpu.json"))
        except FileNotFoundError:
            pass

    def test_dos_vueltas_altas_avisan_y_dicen_quien(self):
        self.assertEqual(hc._check_cpu(_medida(46.0))[0], [])
        alertas, info = hc._check_cpu(_medida(52.0))
        self.assertEqual(len(alertas), 1)
        self.assertIn("Google Chrome Helper (Renderer)", alertas[0])
        self.assertIn("60159", alertas[0])
        self.assertTrue(info["ahogo"])

    def test_un_pico_solo_no_avisa(self):
        self.assertEqual(hc._check_cpu(_medida(46.0))[0], [])
        self.assertEqual(hc._check_cpu(_medida(6.0))[0], [])
        self.assertEqual(hc._check_cpu(_medida(46.0))[0], [])

    def test_carga_normal_no_avisa(self):
        for _ in range(3):
            self.assertEqual(hc._check_cpu(_medida(12.0))[0], [])

    def test_throttle_no_repite_en_la_hora(self):
        hc._check_cpu(_medida(46.0))
        self.assertEqual(len(hc._check_cpu(_medida(46.0))[0]), 1)
        alertas, info = hc._check_cpu(_medida(46.0))
        self.assertEqual(alertas, [])
        self.assertTrue(info.get("throttled"))

    def test_sin_lectura_ni_aviso_ni_excepcion(self):
        alertas, info = hc._check_cpu(lambda: None)
        self.assertEqual(alertas, [])
        self.assertIn("error", info)

    def test_la_medida_real_se_puede_tomar(self):
        m = hc._cpu_ahora()
        self.assertIsNotNone(m)
        carga, nucleos, _top = m
        self.assertGreaterEqual(carga, 0)
        self.assertGreaterEqual(nucleos, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
