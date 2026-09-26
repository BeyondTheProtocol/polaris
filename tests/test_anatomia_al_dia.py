#!/usr/bin/env python3
"""Test: si Polaris cambia, La Anatomía tiene que contarlo.

Regla de {{TITULAR}} (30-jul-2026): «cada vez que hayan cambios y mejoras en la
estructura y funcionamiento de polaris el panel debe actualizarse».

El panel se empuja cifrado cada 5 min y REFRESCA SOLO lo que puede derivar del
disco (cuántas tools, qué comités, qué rutinas están cargadas). Lo que está
escrito a mano en `anatomia.py` no se entera de nada: el camino de un encargo,
el glosario, los conectores de cuenta, quién queda reservado en la cara pública.

Este test no revisa el panel por nadie — eso no lo puede hacer un test. Lo que
hace es impedir que el cambio pase inadvertido: compara la superficie del
sistema con la última sellada y, si se movió algo, obliga a mirar antes de
cerrar la sesión.

Se cierra con:  python3 tools/anatomia.py sellar
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("contenido", "estado", "nombres")

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import anatomia  # noqa: E402


class TestPanelAlDia(unittest.TestCase):

    def test_hay_huella_sellada(self):
        self.assertIsNotNone(
            anatomia.huella_sellada(),
            "falta %s — séllala con: python3 tools/anatomia.py sellar"
            % anatomia.HUELLA)

    def test_la_superficie_es_la_que_el_panel_cuenta(self):
        movido = anatomia.diff_superficie(anatomia.huella_sellada(),
                                          anatomia.superficie())
        self.assertEqual(
            movido, [],
            "Polaris cambió y nadie ha revisado La Anatomía:\n  "
            + "\n  ".join(movido)
            + "\n\nMira el panel (python3 tools/anatomia.py render --cara privada),"
              "\nactualiza a mano lo que no se deriva solo (el camino, el glosario,"
              "\nCONECTORES_CUENTA) y, si el comité nuevo delata su vida, métele en"
              "\nPUBLICO_RESERVADO antes de que salga por su nombre en la cara"
              "\npública. Luego: python3 tools/anatomia.py sellar")

    def test_todo_fichero_del_camino_existe(self):
        """El camino de un encargo cita ficheros reales. Si uno se renombra, el
        panel enseña una ruta muerta a quien viene a entender el sistema."""
        for paso, cita, _ in anatomia.CAMINO:
            # Una cita puede juntar varios ("a.py · b.py") o ser un directorio.
            for ruta in [r.strip() for r in cita.split("·")]:
                if ruta in ("tools/", "MCP") or not ruta:
                    continue
                self.assertTrue(
                    os.path.exists(os.path.join(anatomia.CODIGO, ruta)),
                    "el paso «%s» del camino cita %s y ya no existe" % (paso, ruta))

    def test_la_huella_no_nombra_lo_que_no_va_a_git(self):
        """La huella se versiona. Las cajas viven en 00_FUENTE-DE-VERDAD (fuera
        de git a propósito) y sus nombres cuentan su vida, no su arquitectura:
        van contadas, nunca nombradas."""
        self.assertIsInstance(anatomia.superficie()["cajas"], int)


if __name__ == "__main__":
    unittest.main(verbosity=2)
