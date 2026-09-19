#!/usr/bin/env python3
"""test_migrar_secretos.py — no confundir «no está» con «no puedo mirar».

La primera versión de esta tool decidía si una clave estaba en el Llavero mirando el TEXTO del
stderr de `security`. En el caso que importa —el item existe pero este proceso no puede leerlo—
`security` sale con 36 y **stderr vacío**, así que la tool decía «falta en el Llavero» para las
tres claves que sí estaban. Con eso habría migrado y PISADO claves buenas.

Fue la tercera medición mentirosa del mismo día (el informe de uso que no veía a los daemons, el
test que buscaba nombres en base64, y esta). El patrón se repite lo suficiente como para que el
test valga más por lo que vigila que por lo que prueba: **una herramienta que mide tiene que
distinguir el cero del no-lo-sé.**
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))


class Secretos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="secretos-")
        os.makedirs(os.path.join(self.tmp, "tools"))
        os.environ["BTP_REPO"] = self.tmp
        sys.modules.pop("migrar_secretos", None)
        import migrar_secretos
        self.m = migrar_secretos
        self.m.CASA = self.tmp
        self.m.TOOLS = os.path.join(self.tmp, "tools")

    def tearDown(self):
        os.environ.pop("BTP_REPO", None)
        sys.modules.pop("migrar_secretos", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fichero(self, nombre, datos):
        p = os.path.join(self.tmp, "tools", nombre)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(datos, f)
        os.chmod(p, 0o600)
        return p

    def test_rc36_es_ESTA_pero_no_lo_leo_no_es_que_falte(self):
        """El bug entero en una línea."""
        self.m._kc_leer = lambda s: (True, False)
        p = self._fichero(".telegram_secrets.json", {"token": "t", "chat_id": "c"})
        self.m.retirar(escribir=True)
        self.assertFalse(os.path.exists(p), "está en el Llavero: el fichero sobra")
        self.assertTrue(os.path.exists(p + ".sobra.bak"), "se aparta, no se borra")

    def test_si_la_clave_FALTA_de_verdad_el_fichero_se_queda(self):
        self.m._kc_leer = lambda s: (False, True)          # rc 44 = no existe
        p = self._fichero(".nvidia_secrets.json", {"api_key": "k"})
        self.m.retirar(escribir=True)
        self.assertTrue(os.path.exists(p), "si no está en el Llavero, el fichero AÚN hace falta")

    def test_basta_con_que_falte_UNA_clave_para_no_tocar_el_fichero(self):
        self.m._kc_leer = lambda s: (True, False) if s.endswith("token") else (False, True)
        p = self._fichero(".telegram_secrets.json", {"token": "t", "chat_id": "c"})
        self.m.retirar(escribir=True)
        self.assertTrue(os.path.exists(p))

    def test_el_huerfano_se_aparta_aunque_no_haya_servicio(self):
        self.m._kc_leer = lambda s: (False, True)
        p = self._fichero(".openrouter_secrets.json", {"api_key": "k"})
        self.m.retirar(escribir=True)
        self.assertFalse(os.path.exists(p))

    def test_sin_si_no_toca_nada(self):
        self.m._kc_leer = lambda s: (True, False)
        p = self._fichero(".telegram_secrets.json", {"token": "t", "chat_id": "c"})
        self.m.retirar(escribir=False)
        self.assertTrue(os.path.exists(p))

    def test_no_imprime_el_valor_de_ninguna_clave(self):
        import io
        import contextlib
        self.m._kc_leer = lambda s: (True, False)
        secreto = "sk-esto-no-puede-salir-por-pantalla-jamas"
        self._fichero(".nvidia_secrets.json", {"api_key": secreto})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.m.revisar()
            self.m.retirar(escribir=False)
        self.assertNotIn(secreto, buf.getvalue())
        self.assertIn("chars", buf.getvalue(), "sí dice la longitud, que es lo útil sin ser el valor")


if __name__ == "__main__":
    unittest.main(verbosity=2)
