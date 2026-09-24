#!/usr/bin/env python3
"""test_estado_rutina.py — issue #4: «nada que hacer» frente a «lleva un mes rota».

Casos del PR #22 de j7j7j7 (24-sep-2026), pasados a unittest como el resto de la batería, más
los bordes que el issue pedía y faltaban: justo en 2P, fecha ilegible, fecha del futuro, éxito
posterior a la última ejecución y el timestamp 0. Su `test_doble_retraso_es_rota` quedaba en
rojo contra su propio código (`>` estricto): el umbral de «rota» es ahora `>= 2P`, con el porqué
en el docstring de `tools/estado_rutina.py`.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "tools"))
from estado_rutina import ESTADOS, clasificar, clasificar_desde_timestamps  # noqa: E402

AHORA = datetime(2026, 9, 24, 12, 0, 0)
DIA, SEMANA = timedelta(days=1), timedelta(days=7)


def hace(**kw):
    return AHORA - timedelta(**kw)


class NuncaCorrio(unittest.TestCase):
    def test_nunca_ejecuto(self):
        self.assertEqual(clasificar(None, None, DIA, AHORA), "nunca-corrio")

    def test_sin_ejecucion_aunque_haya_exito(self):
        self.assertEqual(clasificar(None, hace(days=10), DIA, AHORA), "nunca-corrio")


class AlDia(unittest.TestCase):
    def test_ejecucion_reciente(self):
        self.assertEqual(clasificar(hace(hours=2), hace(hours=2), DIA, AHORA), "al-dia")

    def test_justo_en_el_periodo_sigue_al_dia(self):
        self.assertEqual(clasificar(hace(days=1), hace(days=1), DIA, AHORA), "al-dia")

    def test_un_minuto_antes_del_periodo(self):
        self.assertEqual(clasificar(hace(hours=23, minutes=59), hace(hours=23, minutes=59),
                                    DIA, AHORA), "al-dia")


class Atrasada(unittest.TestCase):
    def test_un_dia_de_retraso(self):
        self.assertEqual(clasificar(hace(days=8), hace(days=8), SEMANA, AHORA), "atrasada")

    def test_un_segundo_pasado_el_periodo(self):
        self.assertEqual(clasificar(hace(days=1, seconds=1), hace(days=1, seconds=1), DIA, AHORA),
                         "atrasada")

    def test_un_segundo_antes_de_2p_aun_no_es_rota(self):
        antes = hace(days=13, hours=23, minutes=59, seconds=59)
        self.assertEqual(clasificar(antes, antes, SEMANA, AHORA), "atrasada")


class Rota(unittest.TestCase):
    def test_justo_en_2p_es_rota(self):
        """El caso del PR que quedaba rojo: a 2P ya pasó una ventana entera sin éxito."""
        self.assertEqual(clasificar(hace(days=14), hace(days=14), SEMANA, AHORA), "rota")

    def test_tres_periodos(self):
        self.assertEqual(clasificar(hace(days=3), hace(days=3), DIA, AHORA), "rota")

    def test_corre_pero_nunca_termina_bien(self):
        self.assertEqual(clasificar(hace(hours=1), None, DIA, AHORA), "rota")

    def test_corre_a_tiempo_pero_el_exito_es_viejo(self):
        self.assertEqual(clasificar(hace(hours=1), hace(days=20), SEMANA, AHORA), "rota")


class FallaCerrado(unittest.TestCase):
    """El issue: una fecha ilegible o ausente NO puede devolver `al-dia`."""

    def test_nada_ausente_da_al_dia(self):
        for ej, ex in ((None, None), (hace(hours=1), None), (None, hace(hours=1))):
            with self.subTest(ej=ej, ex=ex):
                self.assertNotEqual(clasificar(ej, ex, DIA, AHORA), "al-dia")

    def test_fecha_ilegible(self):
        for mala in ("ayer", 12345, object()):
            with self.subTest(mala=mala):
                self.assertEqual(clasificar(mala, hace(hours=1), DIA, AHORA), "rota")
                self.assertEqual(clasificar(hace(hours=1), mala, DIA, AHORA), "rota")

    def test_fecha_del_futuro(self):
        futuro = AHORA + timedelta(hours=1)
        self.assertEqual(clasificar(futuro, futuro, DIA, AHORA), "rota")

    def test_exito_posterior_a_la_ultima_ejecucion_no_cuadra(self):
        self.assertEqual(clasificar(hace(hours=5), hace(hours=1), DIA, AHORA), "rota")

    def test_periodo_invalido_es_error_de_quien_llama(self):
        for p in (timedelta(0), -DIA, 7):
            with self.subTest(p=p):
                with self.assertRaises(ValueError):
                    clasificar(hace(hours=1), hace(hours=1), p, AHORA)

    def test_siempre_devuelve_un_estado_conocido(self):
        for ej in (None, hace(hours=1), hace(days=30), "x"):
            for ex in (None, hace(hours=1), hace(days=30), "x"):
                self.assertIn(clasificar(ej, ex, SEMANA, AHORA), ESTADOS)


class DesdeTimestamps(unittest.TestCase):
    def test_timestamps(self):
        ahora = AHORA.timestamp()
        self.assertEqual(clasificar_desde_timestamps(ahora - 3600, ahora - 3600, 1, ahora),
                         "al-dia")

    def test_ninguna_ejecucion(self):
        self.assertEqual(clasificar_desde_timestamps(None, None, 1, AHORA.timestamp()),
                         "nunca-corrio")

    def test_el_cero_es_una_fecha_no_una_ausencia(self):
        """Antes `if ts` convertía 0 en «nunca corrió»: un dato corrupto pasaba por estado."""
        self.assertEqual(clasificar_desde_timestamps(0, 0, 1, AHORA.timestamp()), "rota")

    def test_timestamp_ilegible(self):
        self.assertEqual(clasificar_desde_timestamps("ayer", None, 1, AHORA.timestamp()), "rota")


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    print("✅ ESTADO DE RUTINA EN VERDE" if res.wasSuccessful() else "❌ revisar fallos")
    sys.exit(0 if res.wasSuccessful() else 1)
