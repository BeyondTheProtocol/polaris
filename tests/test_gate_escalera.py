#!/usr/bin/env python3
"""test_gate_escalera.py — la escalera del gate (P2, 25-sep-26) no puede mentir sobre la cota
ni proponer subir un check sin datos. Idea de {{CONTACTO}} (https://contacto), con su
agente KAI, revisión del 25-sep-2026.

Aísla el estado en un `BTP_STATE_DIR` temporal antes de importar (nunca toca el log vivo).
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
_TMP = tempfile.mkdtemp(prefix="gate_escalera_test_")
os.environ["BTP_STATE_DIR"] = _TMP

import gate_etiqueta as ge  # noqa: E402
import gate_escalera as gs  # noqa: E402

ge.STATE = _TMP
ge.LOG = os.path.join(_TMP, "gate_salida.jsonl")
ge.ETIQUETAS = os.path.join(_TMP, "gate_etiquetas.json")


def _montar(check, veredictos):
    with open(ge.LOG, "w", encoding="utf-8") as f:
        for _ in veredictos:
            f.write(json.dumps({"check": check}) + "\n")
    with open(ge.ETIQUETAS, "w", encoding="utf-8") as f:
        json.dump({str(i): {"veredicto": v, "check": check} for i, v in enumerate(veredictos)}, f)


class Cota(unittest.TestCase):
    def test_clopper_pearson_una_cola(self):
        # Valores recalculados a mano el 25-sep-26 (y por el comité verificacion, por separado).
        for fp, n, esperado in [(0, 1, 95.0), (0, 7, 34.8), (2, 20, 28.3), (1, 30, 14.9),
                                (0, 59, 5.0), (2, 100, 6.2), (2, 11, 47.0), (3, 31, 23.2)]:
            self.assertAlmostEqual(gs.cota_superior(fp, n) * 100, esperado, delta=0.06)

    def test_sin_datos_cota_total(self):
        self.assertEqual(gs.cota_superior(0, 0), 1.0)


class Propuestas(unittest.TestCase):
    def test_estilo_sube_con_30_y_1_fp(self):
        _montar("tells_ia", ["acierto"] * 29 + ["falso_positivo"])
        m = gs.medir({"tells_ia": {"modo": "aviso"}})["tells_ia"]
        self.assertEqual(m["propone"], "bloqueo")

    def test_estilo_no_sube_con_20_y_1_fp(self):
        # 20 limpios SÍ bastan (cota 13,9 %); con 1 FP la cota es 21,6 % y no llega.
        _montar("tells_ia", ["acierto"] * 19 + ["falso_positivo"])
        self.assertEqual(gs.medir({"tells_ia": {"modo": "aviso"}})["tells_ia"]["propone"], "aviso")

    def test_suprime_info_nunca_sube_sin_recall(self):
        _montar("falsa_certeza", ["acierto"] * 100)
        m = gs.medir({"falsa_certeza": {"modo": "aviso"}}, rec={})["falsa_certeza"]
        self.assertEqual(m["propone"], "aviso")
        self.assertIn("recall", m["porque"])

    def test_tres_fp_seguidos_bajan(self):
        _montar("no_puedo_falso", ["acierto"] * 5 + ["falso_positivo"] * 3)
        m = gs.medir({"no_puedo_falso": {"modo": "bloqueo"}})["no_puedo_falso"]
        self.assertEqual(m["propone"], "aviso")

    def test_bloqueo_sin_etiquetas_se_denuncia(self):
        _montar("x", [])
        m = gs.medir({"falsa_certeza": {"modo": "bloqueo"}})["falsa_certeza"]
        self.assertIn("SIN ninguna etiqueta", m["porque"])

    def test_suprime_info_sube_con_fp_y_recall(self):
        _montar("falsa_certeza", ["acierto"] * 59)
        m = gs.medir({"falsa_certeza": {"modo": "aviso"}},
                     rec={"falsa_certeza": (30, 30)})["falsa_certeza"]
        self.assertEqual(m["propone"], "bloqueo")

    def test_recall_27_de_30_no_basta(self):
        _montar("falsa_certeza", ["acierto"] * 59)
        m = gs.medir({"falsa_certeza": {"modo": "aviso"}},
                     rec={"falsa_certeza": (27, 30)})["falsa_certeza"]
        self.assertEqual(m["propone"], "aviso")
        self.assertIn("76 %", m["porque"])

    def test_sembrados_reales_se_miden(self):
        r = gs.recall()
        for c in ("falsa_certeza", "no_puedo_falso"):
            self.assertEqual(r[c][1], 30, "30 casos sembrados por check")

    def test_fijo_no_baja(self):
        _montar("citas_fabricadas", ["falso_positivo"] * 4)
        m = gs.medir({"citas_fabricadas": {"modo": "bloqueo"}})["citas_fabricadas"]
        self.assertEqual(m["propone"], "bloqueo")
        self.assertIn("arreglar el check", m["porque"])

    def test_desde_descarta_lo_de_la_version_vieja(self):
        with open(ge.LOG, "w", encoding="utf-8") as f:
            f.write(json.dumps({"check": "x", "ts": 1000.0, "session_hash": "a"}) + "\n")
            f.write(json.dumps({"check": "x", "ts": 4e9, "session_hash": "a"}) + "\n")
        with open(ge.ETIQUETAS, "w", encoding="utf-8") as f:
            json.dump({"0": {"veredicto": "falso_positivo"}, "1": {"veredicto": "acierto"}}, f)
        m = gs.medir({"x": {"modo": "bloqueo", "desde": "2026-09-25"}}, rec={})["x"]
        self.assertEqual((m["disparos"], m["etiquetados"], m["fp"]), (1, 1, 0))

    def test_fila_sintetica_no_cuenta(self):
        with open(ge.LOG, "w", encoding="utf-8") as f:
            f.write(json.dumps({"check": "x", "ts": 1.0, "session_hash": None}) + "\n")
            f.write(json.dumps({"check": "x", "ts": 2.0, "session_hash": "abc"}) + "\n")
        with open(ge.ETIQUETAS, "w", encoding="utf-8") as f:
            json.dump({}, f)
        self.assertEqual(gs.medir({})["x"]["disparos"], 1)

    def test_no_escribe_normas(self):
        antes = open(gs.NORMAS, encoding="utf-8").read()
        gs.main([])
        self.assertEqual(antes, open(gs.NORMAS, encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
