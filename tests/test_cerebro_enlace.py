#!/usr/bin/env python3
"""test_cerebro_enlace.py — cuando el enlace del cerebro queda colgando, se repunta solo.

POR QUÉ (24-sep-2026, deuda `cerebro_inalcanzable`, 4 detecciones desde el 15-sep). El lazo llama
a `claude` por `~/.local/bin/claude`, que es un ENLACE a un binario CON VERSIÓN
(`…/versions/2.1.236`). Cuando Claude Code se actualiza y esa versión desaparece, el enlace queda
colgando: `which` lo sigue encontrando, ejecutarlo falla, y el lazo se queda sin cerebro — o sea,
las tareas hacia NED se paran. El libro de deuda lo marcó como INTERMITENTE: sin mecanismo no se
cierra, y esto es el mecanismo.

Todo sobre enlaces y binarios FALSOS en un tmp: no toca ~/.local/bin ni el Claude de verdad.
"""
import os
import stat
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import healthcheck as hc  # noqa: E402


def _falso_binario(ruta, rc=0):
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\nexit %d\n" % rc)
    os.chmod(ruta, os.stat(ruta).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return ruta


class RepuntaElEnlace(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="cerebro_"))
        self.versions = os.path.join(self.tmp, "share", "claude", "versions")
        os.makedirs(self.versions)
        os.makedirs(os.path.join(self.tmp, "bin"))
        self.enlace = os.path.join(self.tmp, "bin", "claude")

    def test_enlace_colgante_se_repunta_a_la_version_mas_nueva(self):
        vieja = _falso_binario(os.path.join(self.versions, "2.0.0"))
        nueva = _falso_binario(os.path.join(self.versions, "2.1.999"))
        os.utime(nueva, (0, 9_000_000_000))          # la más reciente por mtime
        os.symlink(os.path.join(self.versions, "2.1.500-que-ya-no-esta"), self.enlace)
        self.assertFalse(os.path.exists(os.path.realpath(self.enlace)), "el enlace debe colgar")
        nombre = hc._repara_enlace_cerebro(self.enlace)
        self.assertEqual(nombre, "2.1.999")
        self.assertEqual(os.path.realpath(self.enlace), os.path.realpath(nueva))
        self.assertTrue(os.path.exists(vieja), "no toca las otras versiones")

    def test_si_el_destino_existe_no_toca_nada(self):
        """Un fallo con el destino presente es OTRA cosa: taparlo sería esconder el problema."""
        real = _falso_binario(os.path.join(self.versions, "2.1.1"))
        os.symlink(real, self.enlace)
        self.assertIsNone(hc._repara_enlace_cerebro(self.enlace))
        self.assertEqual(os.path.realpath(self.enlace), os.path.realpath(real))

    def test_sin_candidatos_no_inventa(self):
        os.symlink(os.path.join(self.versions, "no-existe"), self.enlace)
        self.assertIsNone(hc._repara_enlace_cerebro(self.enlace))

    def test_si_el_candidato_no_ejecuta_se_declara_fallo(self):
        malo = os.path.join(self.versions, "2.2.0")
        with open(malo, "w", encoding="utf-8") as f:     # sin permiso de ejecución
            f.write("no soy un binario\n")
        os.symlink(os.path.join(self.versions, "no-existe"), self.enlace)
        self.assertIsNone(hc._repara_enlace_cerebro(self.enlace))

    def test_solo_dentro_de_versions(self):
        """Si el enlace no cuelga de un directorio `versions`, no es el caso conocido: no se toca."""
        otro = os.path.join(self.tmp, "otro")
        os.makedirs(otro)
        _falso_binario(os.path.join(otro, "algo"))
        enlace = os.path.join(self.tmp, "bin", "claude2")
        os.symlink(os.path.join(otro, "no-existe"), enlace)
        self.assertIsNone(hc._repara_enlace_cerebro(enlace))

    def test_un_fichero_normal_no_es_un_enlace(self):
        real = _falso_binario(self.enlace)
        self.assertIsNone(hc._repara_enlace_cerebro(real))


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    print("✅ ENLACE DEL CEREBRO EN VERDE" if res.wasSuccessful() else "❌ revisar fallos")
    sys.exit(0 if res.wasSuccessful() else 1)
