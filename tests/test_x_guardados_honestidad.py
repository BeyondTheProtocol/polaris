#!/usr/bin/env python3
"""test_x_guardados_honestidad.py — el digest no puede fingir «sin novedad».

Garantía que se protege aquí: «NO PUDE MIRARLO» y «sin novedad» NUNCA se confunden.

  · sin sesión de X            → exit 2, dice NO PUDE MIRARLO, y NUNCA «sin guardados nuevos».
  · con sesión y caché rancio  → avisa de que `fetch` no corre (lo que muestra es viejo).
  · con sesión y caché fresco  → «sin guardados nuevos» es legítimo (exit 0).

Contexto: 8-jul-2026. `~/.agent-browser/x_state.json` no existía y el corpus era del 28-jun,
pero `x_guardados.py --digest` imprimía «(sin guardados nuevos en la ventana)». Diez días
informando «sin novedad» sobre una fuente que nadie estaba leyendo: la señal ya pre-filtrada
por {{TITULAR}} (sus bookmarks) se perdía en silencio.
Regla de {{TITULAR}} (innegociable): si una fuente no se barrió, se dice con esas palabras.
"""
import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import x_guardados  # noqa: E402
import _xurl  # noqa: E402  (mismo objeto módulo que usa x_guardados: parchearlo aquí le llega)


def _escribir_store(path, dias_atras):
    """Un guardado cosechado hace `dias_atras` días."""
    fetched = (datetime.date.today() - datetime.timedelta(days=dias_atras)).isoformat()
    rec = {"id": "1", "handle": "alguien", "name": "Alguien", "text": "un tweet",
           "url": "https://x.com/alguien/status/1", "ts": 0, "iso": fetched,
           "fetched": fetched, "tags": ["sistema"]}
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


class DigestHonesto(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = os.path.join(self.tmp.name, "guardados.jsonl")
        self._store_orig = x_guardados.STORE_FILE
        x_guardados.STORE_FILE = self.store
        # La vía de lectura es `xurl` (OAuth), no las cookies de navegador: `_load_cookies`
        # y `STATE_FILE` se borraron el 3-jul al migrar a xurl y este test seguía apuntándolos,
        # así que reventaba en setUp y sus 6 casos llevaban sin correr desde entonces.
        self._disp_orig = _xurl.disponible
        _xurl.disponible = lambda: False        # por defecto: SIN vía de lectura

    def tearDown(self):
        x_guardados.STORE_FILE = self._store_orig
        _xurl.disponible = self._disp_orig
        self.tmp.cleanup()

    def _sesion_valida(self):
        _xurl.disponible = lambda: True

    def _correr(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = x_guardados.cmd_listar(None)
        return rc, buf.getvalue()

    def test_sin_sesion_no_dice_sin_novedad(self):
        """El caso real del 8-jul: caché rancio y sesión caída → jamás «sin novedad»."""
        _escribir_store(self.store, dias_atras=10)
        rc, out = self._correr()
        self.assertEqual(rc, 2, "sin sesión el digest debe fallar-cerrado (exit 2)")
        self.assertIn("NO PUDE MIRARLO", out)
        self.assertNotIn("sin guardados nuevos", out)

    def test_sin_sesion_reporta_antiguedad(self):
        _escribir_store(self.store, dias_atras=10)
        _, out = self._correr()
        self.assertIn("10 día(s)", out)

    def test_sin_sesion_y_sin_store(self):
        """Ni sesión ni cosecha previa: sigue siendo «no pude mirarlo», no «no hay nada»."""
        rc, out = self._correr()
        self.assertEqual(rc, 2)
        self.assertIn("NO PUDE MIRARLO", out)
        self.assertIn("no hay ninguna cosecha previa", out)

    def test_con_sesion_cache_rancio_avisa(self):
        self._sesion_valida()
        _escribir_store(self.store, dias_atras=10)
        rc, out = self._correr()
        self.assertEqual(rc, 0)
        self.assertIn("CACHÉ RANCIO", out)

    def test_con_sesion_y_fresco_sin_novedad_es_legitimo(self):
        """Con sesión y cosecha de hoy, «sin guardados nuevos» sí es una verdad."""
        self._sesion_valida()
        _escribir_store(self.store, dias_atras=0)
        rc, out = self._correr()
        self.assertEqual(rc, 0)
        self.assertNotIn("NO PUDE MIRARLO", out)
        self.assertNotIn("CACHÉ RANCIO", out)

    def test_antiguedad_se_calcula_bien(self):
        _escribir_store(self.store, dias_atras=3)
        store = x_guardados._load_store()
        self.assertEqual(x_guardados._dias_desde_ultima_cosecha(store), 3)
        self.assertIsNone(x_guardados._dias_desde_ultima_cosecha({}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
