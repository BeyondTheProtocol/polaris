#!/usr/bin/env python3
"""Test: la sonda del cerebro distingue «roto» de «máquina ahogada».

POR QUÉ EXISTE (24-sep-2026, deuda `cerebro_inalcanzable`, escalada a 4x). La sonda ejecuta
`claude --version` una vez con 30 s. Medido en `tools/launchd/logs/healthcheck.out`: 8 fallos en
3.293 lecturas, los 8 transitorios (5 TimeoutExpired, 3 BlockingIOError) y ninguno de binario
roto; el del 24-sep coincidió con 21,8 GB de swap. Cada uno decía «NO puede ejecutarlo, las
tareas clínicas van a PARARSE», abría deuda y ponía test_all en rojo para todas las sesiones.

Qué se exige:
  · lo transitorio se reintenta: si el reintento va bien, no hay alerta;
  · si sigue transitorio, la clave es `cerebro_lento`, no `cerebro_inalcanzable`;
  · lo roto (rc≠0, no está en el PATH) sigue siendo `cerebro_inalcanzable` y dice el motivo;
  · fuera de launchd no afirma nada.
"""
import os
import subprocess
import sys
import unittest

os.environ["BTP_CEREBRO_ESPERA_S"] = "0"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import healthcheck as hc  # noqa: E402


class _R:
    def __init__(self, rc=0, out="2.1.236 (Claude Code)", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class Cerebro(unittest.TestCase):
    def setUp(self):
        self._orig = (hc.subprocess.run, hc.shutil.which, hc.salida._origen, hc.CEREBRO_ESPERA_S)
        hc.shutil.which = lambda b: "/fake/bin/claude"
        hc.salida._origen = lambda: ("launchd", True)
        hc.CEREBRO_ESPERA_S = 0
        self.llamadas = []

    def tearDown(self):
        hc.subprocess.run, hc.shutil.which, hc.salida._origen, hc.CEREBRO_ESPERA_S = self._orig

    def _secuencia(self, *pasos):
        pasos = list(pasos)

        def fake(cmd, **kw):
            self.llamadas.append(kw.get("timeout"))
            p = pasos.pop(0)
            if isinstance(p, BaseException):
                raise p
            return p
        hc.subprocess.run = fake

    def _claves(self):
        alertas, info = hc._check_cerebro_alcanzable()
        return [a[0] for a in alertas], alertas, info

    def test_ok_a_la_primera(self):
        self._secuencia(_R())
        claves, _, info = self._claves()
        self.assertEqual(claves, [])
        self.assertEqual(self.llamadas, [30])
        self.assertIn("2.1.236", info["version"])

    def test_timeout_y_luego_ok_no_avisa(self):
        self._secuencia(subprocess.TimeoutExpired(["claude"], 30), _R())
        claves, _, info = self._claves()
        self.assertEqual(claves, [])
        self.assertEqual(self.llamadas, [30, 60])
        self.assertIn("TimeoutExpired", info["transitorio"])

    def test_blockingio_y_luego_ok_no_avisa(self):
        self._secuencia(BlockingIOError(35, "Resource temporarily unavailable"), _R())
        self.assertEqual(self._claves()[0], [])

    def test_timeout_dos_veces_es_lento_no_roto(self):
        self._secuencia(subprocess.TimeoutExpired(["claude"], 30), subprocess.TimeoutExpired(["claude"], 60))
        claves, alertas, _ = self._claves()
        self.assertEqual(claves, ["cerebro_lento"])
        self.assertIn("TimeoutExpired", alertas[0][1])
        self.assertNotIn("PARARSE", alertas[0][1])

    def test_rc_distinto_de_cero_es_roto_y_dice_por_que(self):
        self._secuencia(_R(rc=1, out="", err="dyld: Library not loaded"))
        claves, alertas, _ = self._claves()
        self.assertEqual(claves, ["cerebro_inalcanzable"])
        self.assertIn("rc=1", alertas[0][1])
        self.assertIn("dyld", alertas[0][1])
        self.assertEqual(len(self.llamadas), 1, "lo roto no se reintenta")

    def test_permiso_denegado_es_roto(self):
        self._secuencia(PermissionError(13, "Permission denied"))
        self.assertEqual(self._claves()[0], ["cerebro_inalcanzable"])

    def test_no_esta_en_el_path(self):
        hc.shutil.which = lambda b: None
        self.assertEqual(self._claves()[0], ["cerebro_inalcanzable"])

    def test_fuera_de_launchd_no_afirma(self):
        hc.salida._origen = lambda: ("ssh", False)
        claves, _, info = self._claves()
        self.assertEqual(claves, [])
        self.assertIn("saltado", info)


if __name__ == "__main__":
    unittest.main(verbosity=1)
