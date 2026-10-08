#!/usr/bin/env python3
"""test_nan.py — el cliente de NaN Community (tools/nan.py) y su entrada en el registro.

Sin red y en estado AISLADO (BTP_STATE_DIR temporal). Comprueba:
  1. Sin clave no se llama a la red y se dice qué falta.
  2. Con clave, la petición va a la URL de NaN, con Bearer y con el modelo pedido.
  3. Lo clínico no sale: el borde corta ANTES de buscar la clave o tocar la red.
  4. El registro lo tiene como NO confiable, en el carril de volumen y con su secreto.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="nan_test_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_HALT_FILES"] = os.path.join(_TMP, "no_halt_a") + ":" + os.path.join(_TMP, "no_halt_b")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import borde  # noqa: E402
import nan    # noqa: E402


def _correr(argv):
    """Ejecuta nan.main() con argv y devuelve (stdout, llamadas a la red)."""
    llamadas = []

    def falso_stream(url, body, headers):
        llamadas.append({"url": url, "body": body, "headers": headers})
        return "respuesta de prueba", {"prompt_tokens": 3, "completion_tokens": 2}

    viejo_argv, viejo_stream = sys.argv, nan.stream_chat
    sys.argv, nan.stream_chat = ["nan.py"] + argv, falso_stream
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            nan.main()
    finally:
        sys.argv, nan.stream_chat = viejo_argv, viejo_stream
    return out.getvalue(), llamadas


class TestCliente(unittest.TestCase):
    def setUp(self):
        self._clave = nan._clave
        self._guard = borde.guard_cli
        self._gasto = sys.modules.get("gasto")
        # El ledger de gasto es del sistema vivo: aquí se sustituye por uno mudo.
        sys.modules["gasto"] = type(sys)("gasto")
        sys.modules["gasto"].registrar = lambda *a, **k: None

    def tearDown(self):
        nan._clave = self._clave
        borde.guard_cli = self._guard
        if self._gasto is None:
            sys.modules.pop("gasto", None)
        else:
            sys.modules["gasto"] = self._gasto

    def test_sin_clave_no_toca_la_red(self):
        nan._clave = lambda: None
        borde.guard_cli = lambda *a, **k: True
        out, llamadas = _correr(["resume esto"])
        self.assertEqual(llamadas, [])
        self.assertIn("btp-nan-api", out)

    def test_con_clave_va_a_nan_con_bearer_y_modelo(self):
        nan._clave = lambda: "sk-prueba"
        borde.guard_cli = lambda *a, **k: True
        out, llamadas = _correr(["--model", "deepseek-v4-flash", "resume", "esto"])
        self.assertEqual(len(llamadas), 1)
        ll = llamadas[0]
        self.assertEqual(ll["url"], "https://api.nan.builders/v1/chat/completions")
        self.assertEqual(ll["headers"]["Authorization"], "Bearer sk-prueba")
        # Sin User-Agent propio, el Cloudflare de NaN responde 403 «error code: 1010».
        self.assertEqual(ll["headers"]["User-Agent"], nan.UA)
        self.assertEqual(ll["body"]["model"], "deepseek-v4-flash")
        self.assertEqual(ll["body"]["messages"][0]["content"], "resume esto")
        self.assertIn("respuesta de prueba", out)

    def test_modelo_por_defecto(self):
        nan._clave = lambda: "sk-prueba"
        borde.guard_cli = lambda *a, **k: True
        _, llamadas = _correr(["hola"])
        self.assertEqual(llamadas[0]["body"]["model"], nan.DEFAULT_MODEL)

    def test_el_borde_corta_antes_de_la_clave_y_de_la_red(self):
        pedidas = []
        nan._clave = lambda: pedidas.append(1) or "sk-prueba"
        visto = []
        borde.guard_cli = lambda texto, destino, **k: visto.append(destino) or False
        out, llamadas = _correr(["texto que el borde niega"])
        self.assertEqual(visto, ["nan"])
        self.assertEqual(pedidas, [])
        self.assertEqual(llamadas, [])

    def test_nan_no_es_destino_confiable(self):
        self.assertFalse(borde.es_trusted("nan"))


class TestRegistro(unittest.TestCase):
    def test_entrada_del_registro(self):
        with open(os.path.join(ROOT, "tools", "peripheries.json"), encoding="utf-8") as f:
            cerebros = {c["name"]: c for c in json.load(f)["cerebros"]}
        self.assertIn("nan", cerebros)
        c = cerebros["nan"]
        self.assertIs(c["trusted"], False)
        self.assertEqual(c["destino"], "nan")
        self.assertEqual(c["secret"], "btp-nan-api")
        self.assertEqual(c["bin"], "nan.py")
        self.assertIn("volumen", c["para"])
        self.assertEqual(c["models"][0], nan.DEFAULT_MODEL)


if __name__ == "__main__":
    unittest.main()
