#!/usr/bin/env python3
"""Test: una traza del borde con el FINAL borrado no pasa por íntegra (issue #15).

POR QUÉ EXISTE (auditoría externa 22-sep-2026, punto 3.4).
`verificar_cadena()` comprobaba secuencia, enlace y sello de lo que HABÍA en el ledger. Borrar
el último evento deja una cadena más corta pero bien encadenada, y se declaraba íntegra aunque
`head.txt` dijera que había uno más. Borrar el final es justo lo que haría quien quiere ocultar
la última salida.

Ahora se coteja lo recorrido contra la cabecera. El test comprueba que:
  1. N eventos sellados → íntegra; borrar el último → FALLO que dice «truncada».
  2. Vaciar la traza entera con la cabecera intacta → truncada.
  3. Alterar un evento del medio sigue siendo «rota», no «truncada»: los mensajes se distinguen.
  4. Fail-closed con la cabecera: ausente con eventos, ilegible o atrasada → FALLO.
  5. Instalación limpia (sin traza ni cabecera) → íntegra, sin falsos rojos.

HERMÉTICO: cada caso usa su propio directorio temporal. Sin red, sin overlays privados, así que
corre igual en un fork.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Estado aislado ANTES de importar: borde calcula sus rutas al importarse.
os.environ["BTP_STATE_DIR"] = tempfile.mkdtemp(prefix="borde_trunc_")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import borde  # noqa: E402

N = 3


class LaCadenaTruncadaNoPasaPorIntegra(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="borde_trunc_caso_")
        self._orig = (borde.BORDE_DIR, borde.HEAD_FILE, borde.LOCK_FILE)
        borde.BORDE_DIR = self.dir
        borde.HEAD_FILE = os.path.join(self.dir, "head.txt")
        borde.LOCK_FILE = os.path.join(self.dir, ".lock")

    def tearDown(self):
        borde.BORDE_DIR, borde.HEAD_FILE, borde.LOCK_FILE = self._orig
        shutil.rmtree(self.dir, ignore_errors=True)

    def _sellar_n(self, n=N):
        for i in range(n):
            self.assertIsNotNone(borde._sellar({"evento": "prueba", "i": i}), "no selló")
        ledgers = sorted(f for f in os.listdir(self.dir) if f.startswith("ledger-"))
        self.assertEqual(len(ledgers), 1, "se esperaba un único ledger del día")
        return os.path.join(self.dir, ledgers[0])

    @staticmethod
    def _lineas(path):
        return open(path, encoding="utf-8").read().splitlines()

    @staticmethod
    def _escribir(path, lineas):
        open(path, "w", encoding="utf-8").write("".join(l + "\n" for l in lineas))

    def test_integra_tras_sellar(self):
        self._sellar_n()
        ok, det = borde.verificar_cadena()
        self.assertTrue(ok, det)
        self.assertIn("%d eventos" % N, det)

    def test_borrar_el_ultimo_evento_es_truncada(self):
        path = self._sellar_n()
        self._escribir(path, self._lineas(path)[:-1])
        ok, det = borde.verificar_cadena()
        self.assertFalse(ok, "un ledger sin su último evento se dio por íntegro: %s" % det)
        self.assertIn("TRUNCADA", det)
        self.assertIn("%d" % N, det, "el mensaje debe decir cuántos esperaba")
        self.assertIn("%d" % (N - 1), det, "y cuántos encontró")

    def test_vaciar_la_traza_con_cabecera_es_truncada(self):
        path = self._sellar_n()
        self._escribir(path, [])
        ok, det = borde.verificar_cadena()
        self.assertFalse(ok, det)
        self.assertIn("TRUNCADA", det)

    def test_alterar_un_evento_es_rota_no_truncada(self):
        path = self._sellar_n()
        lineas = self._lineas(path)
        rec = json.loads(lineas[1])
        rec["i"] = 999
        lineas[1] = json.dumps(rec, ensure_ascii=False, sort_keys=True)
        self._escribir(path, lineas)
        ok, det = borde.verificar_cadena()
        self.assertFalse(ok, det)
        self.assertNotIn("TRUNCADA", det, "una alteración no debe describirse como truncado")

    def test_sin_cabecera_y_con_eventos_falla(self):
        self._sellar_n()
        os.remove(borde.HEAD_FILE)
        ok, det = borde.verificar_cadena()
        self.assertFalse(ok, "sin cabecera no se puede probar que no falte el final: %s" % det)
        self.assertIn("cabecera", det)

    def test_cabecera_ilegible_falla(self):
        self._sellar_n()
        open(borde.HEAD_FILE, "w", encoding="utf-8").write("basura")
        ok, det = borde.verificar_cadena()
        self.assertFalse(ok, "una cabecera ilegible no puede leerse como GENESIS: %s" % det)
        self.assertIn("ilegible", det)

    def test_cabecera_atrasada_falla(self):
        self._sellar_n()
        seq, h = borde._read_head()
        open(borde.HEAD_FILE, "w", encoding="utf-8").write("%d %s" % (seq - 1, h))
        ok, det = borde.verificar_cadena()
        self.assertFalse(ok, det)
        self.assertIn("atrasada", det)

    def test_cabecera_con_otro_hash_falla(self):
        self._sellar_n()
        seq, _ = borde._read_head()
        open(borde.HEAD_FILE, "w", encoding="utf-8").write("%d %s" % (seq, "0" * 64))
        ok, det = borde.verificar_cadena()
        self.assertFalse(ok, det)
        self.assertIn("no coincide con la cabecera", det)

    def test_instalacion_limpia_es_integra(self):
        ok, det = borde.verificar_cadena()
        self.assertTrue(ok, "sin traza ni cabecera no hay nada que falte: %s" % det)


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=0).run(
        unittest.TestLoader().loadTestsFromTestCase(LaCadenaTruncadaNoPasaPorIntegra))
    if res.wasSuccessful():
        print("✅ BORDE CADENA TRUNCADA EN VERDE (%d casos · borrar el final ya no pasa por íntegro)"
              % res.testsRun)
    raise SystemExit(0 if res.wasSuccessful() else 1)
