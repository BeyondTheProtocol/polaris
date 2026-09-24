#!/usr/bin/env python3
"""test_canarios.py — los señuelos del muro: que existan, que el borde los vea como SEMBRADOS,
y que su valor no se enseñe nunca.

Por qué importa (24-sep-2026): el tripwire de exfiltración llevaba meses sin poder dispararse
porque `canarios.json` no existía, y desde ese día solo escala el canario SEMBRADO. Si esta
batería se cae, volvemos a un guardia sin munición.

Todo en un `BTP_STATE_DIR` temporal: no toca el estado vivo, no toca el Llavero de verdad (se
sustituye `_secrets.set`) y no dispara nada (no se llama a `egress_check` con escalar=True sin
espía).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="canarios_")
os.environ["BTP_STATE_DIR"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))
import canarios as c  # noqa: E402
import borde  # noqa: E402


def _vals():
    with open(c.CANARIOS, encoding="utf-8") as f:
        return json.load(f)


class Sembrar(unittest.TestCase):
    def setUp(self):
        for f in (c.CANARIOS, c.REGISTRO, c.SENUELO_ESTADO):
            if os.path.exists(f):
                os.remove(f)

    def test_estado_siembra_registra_y_el_borde_lo_ve_sembrado(self):
        ok, donde = c.sembrar_estado()
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(c.SENUELO_ESTADO), donde)
        token = _vals()[0]
        self.assertTrue(token.startswith(c.PREFIJO))
        # lo que de verdad importa: el borde lo trata como SEMBRADO (y por tanto escala)
        self.assertEqual(borde.canario_y_origen("bla %s bla" % token)[1], "fichero")
        v = borde.egress_check("informe con %s dentro" % token, destino="nvidia")
        self.assertFalse(v.permitido)
        self.assertTrue(v.alarma)

    def test_el_senuelo_dice_lo_que_es(self):
        c.sembrar_estado()
        with open(c.SENUELO_ESTADO, encoding="utf-8") as f:
            d = json.load(f)
        self.assertIn("SEÑUELO", d["_que_es"])
        self.assertIn("canarios.py", d["_que_es"])

    def test_llavero_con_el_guardado_simulado(self):
        import _secrets
        guardado = {}
        orig = _secrets.set
        _secrets.set = lambda s, v, cuenta=None: guardado.update({s: v}) or len(v)
        try:
            ok, detalle = c.sembrar_llavero()
        finally:
            _secrets.set = orig
        self.assertTrue(ok, detalle)
        self.assertEqual(guardado.get(c.SERVICIO_LLAVERO), _vals()[0])

    def test_rotar_retira_el_token_viejo(self):
        c.sembrar_estado()
        viejo = _vals()[0]
        c.sembrar_estado()          # rotar = volver a sembrar esa etiqueta
        self.assertEqual(len(_vals()), 1, "el token viejo tiene que salir de la lista")
        self.assertNotEqual(_vals()[0], viejo)
        self.assertIsNone(borde.canario_y_origen("texto con %s" % viejo)[1],
                          "un canario rotado ya no puede dar alarma")

    def test_dos_etiquetas_conviven(self):
        import _secrets
        orig = _secrets.set
        _secrets.set = lambda s, v, cuenta=None: len(v)
        try:
            c.sembrar_estado()
            c.sembrar_llavero()
        finally:
            _secrets.set = orig
        self.assertEqual(len(_vals()), 2)
        with open(c.REGISTRO, encoding="utf-8") as f:
            self.assertEqual(sorted(json.load(f)), ["estado", "llavero"])


class NoEnsenaElValor(unittest.TestCase):
    def test_estado_no_imprime_el_token(self):
        c.sembrar_estado()
        token = _vals()[0]
        p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "canarios.py"), "estado"],
                           capture_output=True, text=True,
                           env=dict(os.environ, BTP_STATE_DIR=_TMP), timeout=30)
        self.assertNotIn(token, p.stdout + p.stderr)
        self.assertIn("sembrados", p.stdout)

    def test_sin_canarios_avisa_y_sale_con_1(self):
        for f in (c.CANARIOS, c.REGISTRO):
            if os.path.exists(f):
                os.remove(f)
        p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "canarios.py"), "estado"],
                           capture_output=True, text=True,
                           env=dict(os.environ, BTP_STATE_DIR=_TMP), timeout=30)
        self.assertEqual(p.returncode, 1)
        self.assertIn("NINGÚN canario", p.stdout)


class Registrar(unittest.TestCase):
    def test_registra_uno_plantado_fuera_y_rechaza_basura(self):
        fichero = os.path.join(_TMP, "token.txt")
        with open(fichero, "w", encoding="utf-8") as f:
            f.write(c.PREFIJO + "x" * 20 + "\n")
        p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "canarios.py"), "registrar",
                            "--etiqueta", "boveda", "--valor-de", fichero, "--donde", "bóveda"],
                           capture_output=True, text=True,
                           env=dict(os.environ, BTP_STATE_DIR=_TMP), timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(fichero, "w", encoding="utf-8") as f:
            f.write("esto no es un token\n")
        p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "canarios.py"), "registrar",
                            "--etiqueta", "boveda", "--valor-de", fichero],
                           capture_output=True, text=True,
                           env=dict(os.environ, BTP_STATE_DIR=_TMP), timeout=30)
        self.assertEqual(p.returncode, 1)


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    print("✅ CANARIOS EN VERDE" if res.wasSuccessful() else "❌ revisar fallos")
    sys.exit(0 if res.wasSuccessful() else 1)
