#!/usr/bin/env python3
"""test_reescribe_consulta.py — el reescritor de búsquedas a TEMA falla SIEMPRE a None.

`borde.preparar_consulta` bloquea cuando esto devuelve None, así que cada fallo aquí (sin clave,
sin saldo, JSON roto, respuesta cortada, rechazo) tiene que acabar en None y nunca en un texto a
medias. Nada sale a la red: la llamada HTTP se sustituye por `llamar`. (22-sep-2026)"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import reescribe_consulta as rc  # noqa: E402


def _resp(texto, stop="end_turn"):
    return lambda cuerpo: {"stop_reason": stop, "usage": {"input_tokens": 10, "output_tokens": 5},
                           "content": [{"type": "text", "text": texto}]}


class FallaANone(unittest.TestCase):
    def setUp(self):
        self._add, self._check = rc._anotar_coste, None
        rc._anotar_coste = lambda usd: None
        import cost_guard
        self._cg = cost_guard.check_before_job
        cost_guard.check_before_job = lambda *a, **k: (True, "ok", 1.0)

    def tearDown(self):
        import cost_guard
        rc._anotar_coste = self._add
        cost_guard.check_before_job = self._cg

    def test_bueno(self):
        r = rc.reescribir("x", llamar=_resp('{"tema": "vacuna  personalizada", "instrucciones": "cita fuentes"}'))
        self.assertEqual(r, {"tema": "vacuna personalizada", "instrucciones": "cita fuentes"})

    def test_prosa_alrededor_del_json(self):
        self.assertEqual(rc.reescribir("x", llamar=_resp('Aquí va: {"tema": "a b", "instrucciones": ""}'))["tema"], "a b")

    def test_cortada_o_rechazo(self):
        for stop in ("max_tokens", "refusal"):
            self.assertIsNone(rc.reescribir("x", llamar=_resp('{"tema": "a b"}', stop)))

    def test_json_roto_o_forma_rara(self):
        for t in ("no puedo", '{"tema": ', '{"tema": ["a"]}', '{"tema": ""}', '{"instrucciones": "x"}',
                  '{"tema": "a", "instrucciones": 3}'):
            self.assertIsNone(rc.reescribir("x", llamar=_resp(t)), t)

    def test_red_caida(self):
        def boom(cuerpo):
            raise TimeoutError("timeout")
        self.assertIsNone(rc.reescribir("x", llamar=boom))

    def test_sin_saldo(self):
        import cost_guard
        cost_guard.check_before_job = lambda *a, **k: (False, "tope", 0.0)
        self.assertIsNone(rc.reescribir("x", llamar=_resp('{"tema": "a b", "instrucciones": ""}')))

    def test_sin_clave(self):
        orig_env, orig = os.environ.pop("ANTHROPIC_API_KEY", None), rc._clave
        rc._clave = lambda: ""
        try:
            self.assertIsNone(rc.reescribir("x"))
        finally:
            rc._clave = orig
            if orig_env:
                os.environ["ANTHROPIC_API_KEY"] = orig_env

    def test_vacio(self):
        self.assertIsNone(rc.reescribir("   ", llamar=_resp('{"tema": "a b"}')))


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=0).run(unittest.defaultTestLoader.loadTestsFromTestCase(FallaANone))
    sys.exit(0 if res.wasSuccessful() else 1)
