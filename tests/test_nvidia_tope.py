#!/usr/bin/env python3
"""test_nvidia_tope.py — una llamada a NVIDIA no puede colgarse más que su tope total.

POR QUÉ (22-sep-26). `nvidia.post` hacía hasta 5 intentos de 180 s con backoff: una sola llamada
podía quedarse ~15 min colgada, y el carril gratis la usa SOLO (al degradar). Visto en vivo:
nemotron-3-ultra colgado más de 5 min y, poco después, respondiendo en 1-4 s.

Sin red: se sustituye `urllib.request.urlopen` por uno que falla con un error de red reintentable
o que «tarda» lo que le pidan, y se mide el tiempo real.
"""
import os
import sys
import time
import unittest
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import _net  # noqa: E402
import nvidia  # noqa: E402


class WithRetriesDeadline(unittest.TestCase):
    def test_sin_deadline_se_comporta_como_antes(self):
        n = {"i": 0}

        def fn():
            n["i"] += 1
            if n["i"] < 3:
                raise urllib.error.URLError("caido")
            return "ok"
        self.assertEqual(_net.with_retries(fn, base=0.01, cap=0.02), "ok")
        self.assertEqual(n["i"], 3)

    def test_con_deadline_no_duerme_mas_alla_del_tope(self):
        def fn():
            raise urllib.error.URLError("caido")
        t = time.monotonic()
        with self.assertRaises(urllib.error.URLError):
            # backoff de 1, 2, 4, 8 s: sin tope serían ~15 s; con tope de 1,5 s, corta antes.
            _net.with_retries(fn, base=1.0, deadline=time.monotonic() + 1.5)
        self.assertLess(time.monotonic() - t, 2.5)


class NvidiaPostTope(unittest.TestCase):
    def setUp(self):
        self._orig_urlopen = nvidia.urllib.request.urlopen
        self._orig_tope = nvidia.TOPE_TOTAL_S
        self.timeouts = []

    def tearDown(self):
        nvidia.urllib.request.urlopen = self._orig_urlopen
        nvidia.TOPE_TOTAL_S = self._orig_tope

    def test_modelo_colgado_corta_en_el_tope_total(self):
        nvidia.TOPE_TOTAL_S = 8

        def colgado(req, timeout=None):
            self.timeouts.append(timeout)
            time.sleep(min(timeout, 1.0))          # «tarda» y luego da error de red reintentable
            raise urllib.error.URLError("timed out")
        nvidia.urllib.request.urlopen = colgado
        t = time.monotonic()
        data, err = nvidia.post(nvidia.CHAT_URL, "nvapi-x",
                                {"model": "m", "messages": [{"role": "user", "content": "hola"}]})
        dur = time.monotonic() - t
        self.assertIsNone(data)
        self.assertTrue(err)
        self.assertLess(dur, nvidia.TOPE_TOTAL_S + 2, "se pasó del tope total: %.1f s" % dur)
        self.assertTrue(all(x <= nvidia.TOPE_TOTAL_S for x in self.timeouts),
                        "algún intento pidió más tiempo del que quedaba: %s" % self.timeouts)

    def test_cuerpo_que_gotea_tambien_corta_en_el_tope(self):
        """El caso real del 22-sep: cabeceras rápidas, cuerpo que no termina. El timeout del
        socket no lo cubre; el tope total sí tiene que cubrirlo."""
        # > 5 s a propósito: con menos, la guarda «quedan <5 s» corta ANTES de leer el cuerpo y el
        # test pasaría sin probar nada (le pasó a la primera versión de este mismo test).
        nvidia.TOPE_TOTAL_S = 8

        class Goteo:
            def read(self, *a, **k):
                time.sleep(30)            # el cuerpo no acaba nunca dentro del test
                return b"{}"
        nvidia.urllib.request.urlopen = lambda req, timeout=None: Goteo()
        t = time.monotonic()
        data, err = nvidia.post(nvidia.CHAT_URL, "nvapi-x",
                                {"model": "m", "messages": [{"role": "user", "content": "hola"}]})
        dur = time.monotonic() - t
        self.assertIsNone(data)
        self.assertIn("tope total", err)
        self.assertLess(dur, nvidia.TOPE_TOTAL_S + 2, "el cuerpo lento se saltó el tope: %.1f s" % dur)

    def test_una_respuesta_normal_sigue_llegando(self):
        import io
        import json

        def bien(req, timeout=None):
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode())
        nvidia.urllib.request.urlopen = bien
        data, err = nvidia.post(nvidia.CHAT_URL, "nvapi-x",
                                {"model": "m", "messages": [{"role": "user", "content": "hola"}]})
        self.assertIsNone(err)
        self.assertEqual(data["choices"][0]["message"]["content"], "ok")

    def test_stream_pasa_por_el_borde(self):
        """El modo por trozos (CLI) es otro camino hacia NVIDIA: tiene que vetar lo sensible
        igual que post(), y sin llegar a abrir la conexión."""
        llamado = []
        orig = nvidia.stream_chat
        nvidia.stream_chat = lambda *a, **k: (llamado.append(1), ("no debía salir", {}))[1]
        try:
            texto, err = nvidia.chat_stream("nvapi-x", {"model": "m", "messages": [
                {"role": "user", "content": "Abemaciclib a 100 mg diarios y biopsia hepatica HER2 IHC 0"}]})
        finally:
            nvidia.stream_chat = orig
        self.assertIsNone(texto)
        self.assertIn("BORDE", err)
        self.assertEqual(llamado, [], "el contenido sensible llegó a salir hacia NVIDIA")

    def test_stream_devuelve_el_texto(self):
        orig = nvidia.stream_chat
        nvidia.stream_chat = lambda *a, **k: ("hola por trozos", {})
        try:
            texto, err = nvidia.chat_stream("nvapi-x", {"model": "m", "messages": [
                {"role": "user", "content": "Traduce este título de un ensayo, por favor."}]})
        finally:
            nvidia.stream_chat = orig
        self.assertIsNone(err)
        self.assertEqual(texto, "hola por trozos")

    def test_el_tope_por_defecto_es_razonable(self):
        self.assertGreaterEqual(self._orig_tope, 30)
        self.assertLessEqual(self._orig_tope, 180)


if __name__ == "__main__":
    unittest.main()
