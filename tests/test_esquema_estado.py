#!/usr/bin/env python3
"""test_esquema_estado.py — un pendiente con el estado roto es un pendiente INVISIBLE.

POR QUÉ EXISTE (20-sep-2026). `seguimiento.add_hilo()` ya validaba enums y fechas ISO, pero nada
impedía escribir `seguimiento.json` a mano y saltárselo. Al auditarlo aparecieron **8 hilos de 232**
con estados que no existen (`pendiente`, `descartado`, `sin_abrir`) — uno de ellos lo metí yo en
esta misma sesión.

Lo grave no es el campo mal escrito: la severidad del Tablero se calcula **solo** con fechas ISO y
enums (su defensa contra una orden plantada en prosa). Un estado que no casa con ningún enum
**no lo persigue nadie**, pero sigue contando en los totales. Parece vigilado y no lo está — que
es justo lo contrario de «la fuente única lo contiene todo».

Este test corre sobre el Tablero REAL: si alguien vuelve a escribir por la puerta de atrás, la
batería se pone roja y se sabe el mismo día, no tres meses después.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import esquema_estado as EE  # noqa: E402


def _tablero(hilos):
    d = tempfile.mkdtemp(prefix="esquema-")
    ruta = os.path.join(d, "seguimiento.json")
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump({"actualizado": "2026-09-20", "hilos": hilos, "tareas": []}, f)
    return ruta


class ElTableroReal(unittest.TestCase):
    """El que importa: el fichero vivo, no uno de juguete."""

    def test_ningun_hilo_del_tablero_tiene_el_esquema_roto(self):
        if not os.path.exists(EE.SEG):
            self.skipTest("sin estado vivo (worktree sin tools/state)")
        rotos, total = EE.revisar()
        self.assertEqual([], rotos,
                         "hay hilos que NADIE persigue porque su estado no casa con el enum.\n"
                         "Arréglalos: python3 tools/esquema_estado.py --arreglar")
        self.assertGreater(total, 0)


class Deteccion(unittest.TestCase):

    def test_caza_los_tres_estados_que_aparecieron_de_verdad(self):
        ruta = _tablero([
            {"id": "a", "titulo": "x", "estado": "pendiente"},
            {"id": "b", "titulo": "y", "estado": "descartado"},
            {"id": "c", "titulo": "z", "estado": "sin_abrir"},
        ])
        rotos, total = EE.revisar(ruta)
        self.assertEqual(3, len(rotos))
        self.assertEqual(3, total)

    def test_una_fecha_que_no_es_iso_tambien_se_caza(self):
        """La severidad sale de fechas ISO: «mañana» no es una fecha, es prosa."""
        ruta = _tablero([{"id": "a", "titulo": "x", "estado": "en_curso", "plazo": "mañana"}])
        rotos, _ = EE.revisar(ruta)
        self.assertEqual(1, len(rotos))
        self.assertIn("plazo", rotos[0][1][0])

    def test_un_hilo_sin_id_o_sin_titulo_no_pasa(self):
        ruta = _tablero([{"titulo": "sin id", "estado": "en_curso"},
                         {"id": "sin-titulo", "estado": "en_curso"}])
        rotos, _ = EE.revisar(ruta)
        self.assertEqual(2, len(rotos))

    def test_un_tablero_sano_no_da_falsos_positivos(self):
        ruta = _tablero([{"id": "a", "titulo": "x", "estado": "en_curso",
                          "plazo": "2026-10-01", "esperando_desde": "2026-09-20"},
                         {"id": "b", "titulo": "y", "estado": "hecho", "hecho_el": "2026-09-19"}])
        rotos, _ = EE.revisar(ruta)
        self.assertEqual([], rotos)


class Arreglo(unittest.TestCase):

    def test_traduce_lo_conocido_y_deja_lo_demas_a_la_vista(self):
        ruta = _tablero([
            {"id": "a", "titulo": "x", "estado": "pendiente"},
            {"id": "b", "titulo": "y", "estado": "estado_que_nadie_ha_visto"},
        ])
        tocados, sin_traducir = EE.arreglar(ruta)
        self.assertEqual(1, len(tocados))
        self.assertEqual(("a", "pendiente", "en_curso"), tocados[0])
        self.assertEqual(1, len(sin_traducir),
                         "adivinar el estado de un pendiente ajeno sería peor que dejarlo roto")

    def test_el_arreglo_no_toca_lo_que_ya_estaba_bien(self):
        ruta = _tablero([{"id": "a", "titulo": "x", "estado": "esperando", "plazo": "2026-10-01"}])
        antes = open(ruta, encoding="utf-8").read()
        tocados, _ = EE.arreglar(ruta)
        self.assertEqual([], tocados)
        self.assertEqual(antes, open(ruta, encoding="utf-8").read())

    def test_la_cli_sale_en_rojo_cuando_hay_algo_roto(self):
        ruta = _tablero([{"id": "a", "titulo": "x", "estado": "pendiente"}])
        orig = EE.SEG
        try:
            r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "esquema_estado.py")],
                               capture_output=True, text=True, timeout=60,
                               env=dict(os.environ, BTP_STATE_DIR=os.path.dirname(ruta)))
            self.assertEqual(1, r.returncode, "un Tablero roto no puede salir en verde")
            self.assertIn("add_hilo", r.stdout, "el aviso tiene que decir por dónde se coló")
        finally:
            EE.SEG = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
