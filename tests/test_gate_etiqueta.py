#!/usr/bin/env python3
"""test_gate_etiqueta.py — la tool de etiquetado (Arreglo B, 13-sep-26) no puede mentir sobre el
resumen que ve {{TITULAR}}, ni escribir sobre el jsonl vivo, ni perder una etiqueta a medio guardar.

Aísla TODO en un `BTP_STATE_DIR` temporal antes de importar el módulo: sin esto, cualquier corrida
de este test escribiría (o leería) en el estado REAL de casa base
(`feedback-estado-vivo-resuelve-casa-base`).
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

_TMP = tempfile.mkdtemp(prefix="gate_etiqueta_test_")
os.environ["BTP_STATE_DIR"] = _TMP

import gate_etiqueta as ge  # noqa: E402

# Por si el módulo ya se había importado en este proceso con otro STATE (orden de tests):
ge.STATE = _TMP
ge.LOG = os.path.join(_TMP, "gate_salida.jsonl")
ge.ETIQUETAS = os.path.join(_TMP, "gate_etiquetas.json")


def _escribir_log(filas):
    with open(ge.LOG, "w", encoding="utf-8") as f:
        for fila in filas:
            f.write(json.dumps(fila, ensure_ascii=False) + "\n")


FILA_TIPO = {"ts": 1.0, "session_hash": "abc123", "check": "tells_ia", "slug": "feedback-x",
            "modo": "aviso", "bloqueo_real": False, "motivo": "motivo de prueba",
            "extracto": "extracto de prueba"}


class GateEtiqueta(unittest.TestCase):
    def setUp(self):
        # cada test con su propio log/etiquetas, para no arrastrar estado entre tests
        self._d = tempfile.mkdtemp(prefix="ge_caso_", dir=_TMP)
        ge.LOG = os.path.join(self._d, "gate_salida.jsonl")
        ge.ETIQUETAS = os.path.join(self._d, "gate_etiquetas.json")

    def test_list_sin_etiquetar_no_repite_lo_ya_marcado(self):
        _escribir_log([dict(FILA_TIPO), dict(FILA_TIPO, check="convergencia")])
        ok, _m = ge.marcar("0", "acierto")
        self.assertTrue(ok)
        pendientes = ge.listar(solo_sin_etiquetar=True)
        self.assertEqual([id_ for id_, _f, _e in pendientes], ["1"])

    def test_list_filtra_por_check(self):
        _escribir_log([dict(FILA_TIPO, check="tells_ia"), dict(FILA_TIPO, check="convergencia")])
        filas = ge.listar(check="convergencia")
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0][1]["check"], "convergencia")

    def test_marcar_id_inexistente_falla(self):
        _escribir_log([dict(FILA_TIPO)])
        ok, motivo = ge.marcar("99", "acierto")
        self.assertFalse(ok)
        self.assertIn("99", motivo)

    def test_marcar_veredicto_invalido_falla(self):
        _escribir_log([dict(FILA_TIPO)])
        ok, motivo = ge.marcar("0", "mas_o_menos")
        self.assertFalse(ok)

    def test_marcar_persiste_atomico(self):
        _escribir_log([dict(FILA_TIPO)])
        ge.marcar("0", "falso_positivo", "ejemplo de nota")
        # relee desde disco, no del dict en memoria
        et = ge._cargar_etiquetas()
        self.assertEqual(et["0"]["veredicto"], "falso_positivo")
        self.assertEqual(et["0"]["nota"], "ejemplo de nota")
        self.assertTrue(os.path.exists(ge.ETIQUETAS))
        self.assertFalse(os.path.exists(ge.ETIQUETAS + ".tmp"), "no debe quedar el fichero temporal")

    def test_resumen_cuenta_aciertos_fp_y_sin_etiquetar(self):
        _escribir_log([dict(FILA_TIPO, check="no_se_sin_mirar")] * 7)
        for i in range(5):
            ge.marcar(str(i), "acierto")
        ge.marcar("5", "falso_positivo")
        r = ge.resumen("no_se_sin_mirar")
        self.assertEqual(r["no_se_sin_mirar"],
                         {"total": 7, "aciertos": 5, "falsos_positivos": 1, "sin_etiquetar": 1})

    def test_resumen_no_expone_fila_a_fila(self):
        """Lo único que ve {{TITULAR}} es el resumen — nunca el extracto ni el motivo por fila."""
        _escribir_log([dict(FILA_TIPO, extracto="dato-sensible-de-ejemplo")])
        r = ge.resumen()
        volcado = json.dumps(r, ensure_ascii=False)
        self.assertNotIn("dato-sensible-de-ejemplo", volcado)

    def test_log_vacio_no_revienta(self):
        self.assertEqual(ge.listar(), [])
        self.assertEqual(ge.resumen(), {})
        ok, _m = ge.marcar("0", "acierto")
        self.assertFalse(ok)


class ContratoCLI(unittest.TestCase):
    """El CLI, tal cual se invoca."""

    def setUp(self):
        self._d = tempfile.mkdtemp(prefix="ge_cli_", dir=_TMP)
        ge.LOG = os.path.join(self._d, "gate_salida.jsonl")
        ge.ETIQUETAS = os.path.join(self._d, "gate_etiquetas.json")
        _escribir_log([dict(FILA_TIPO)])

    def test_list_imprime_algo_y_devuelve_0(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ge.main(["list", "--check", "tells_ia"])
        self.assertEqual(rc, 0)
        self.assertIn("tells_ia", buf.getvalue())

    def test_uso_sin_argumentos_no_revienta(self):
        rc = ge.main([])
        self.assertEqual(rc, 0)                          # por defecto: resumen


if __name__ == "__main__":
    suite = unittest.TestSuite()
    for cls in (GateEtiqueta, ContratoCLI):
        suite.addTests(unittest.TestLoader().loadTestsFromTestCase(cls))
    res = unittest.TextTestRunner(verbosity=0).run(suite)
    if res.wasSuccessful():
        print("✅ GATE_ETIQUETA EN VERDE (%d casos)" % res.testsRun)
    sys.exit(0 if res.wasSuccessful() else 1)
