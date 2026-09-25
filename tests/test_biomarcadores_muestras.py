#!/usr/bin/env python3
"""test_biomarcadores_muestras.py — un analito medido en otra muestra (líquido pleural, orina, LCR…)
no entra en la serie de sangre.

POR QUÉ EXISTE (25-sep-2026). Revisando el panel público /datos: el 14-mar-2024 salían LDH 840,
glucosa <4, albúmina 1,8 y CEA 1,3 «sin rango del informe». En el PDF original son de LÍQUIDO
PLEURAL, no de sangre; tools/biomarcadores.py casaba el nombre del analito al principio de la línea
y no miraba la muestra. Pintarlos como sangre es un dato clínico falso en público.

Autocontenido (BTP_REPO a un tmp), corre igual en CI.
"""
import importlib.util
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_TMP = tempfile.mkdtemp(prefix="biomarcadores_muestras_test_")
os.environ["BTP_REPO"] = _TMP
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "tools", "state")

RAG_MD = os.path.join(_TMP, "00_FUENTE-DE-VERDAD", "00 · Bandeja de entrada", "RAG_md")
os.makedirs(RAG_MD, exist_ok=True)

with open(os.path.join(RAG_MD, "2024-03-14 - Lab - Analítica - bioquímica.md"), "w", encoding="utf-8") as f:
    f.write('---\ndate: "2024-03-14"\n---\n'
            "LDH                                  300            UI/L             135 - 214\n"
            "LDH Liquido pleural                            840             UI/L\n"
            "Glucosa Líquido pleural                        <4              mg/dl\n"
            "Albúmina Liquido pleural                       1.8             g/dl\n"
            "CEA Liquido pleural                            1.3             ng/mL\n"
            "Glucosa en orina                               15              mg/dl\n")

_RUTA_BIO = os.environ.get("BTP_BIOMARCADORES") or os.path.join(ROOT, "tools", "biomarcadores.py")
_spec = importlib.util.spec_from_file_location("biomarcadores", _RUTA_BIO)
bio = importlib.util.module_from_spec(_spec)
sys.modules["biomarcadores"] = bio
_spec.loader.exec_module(bio)


def _serie(payload, key):
    for g in payload["grupos"].values():
        for a in g["analitos"]:
            if a["key"] == key:
                return {p["fecha"]: p for p in a["puntos"]}
    return {}


class TestMuestrasNoSangre(unittest.TestCase):

    def setUp(self):
        self.payload = bio.build()

    def test_ldh_de_sangre_si_entra(self):
        ldh = _serie(self.payload, "ldh")
        self.assertIn("2024-03-14", ldh, "la LDH de sangre de ese día tiene que seguir entrando")
        self.assertEqual(ldh["2024-03-14"]["valor"], 300.0, "entró la LDH pleural (840) en vez de la de sangre (300)")

    def test_pleural_y_orina_no_entran(self):
        for key in ("glucosa", "albumina", "cea"):
            self.assertNotIn("2024-03-14", _serie(self.payload, key), f"{key} de líquido pleural u orina entró como sangre")

    def test_la_regla_no_se_come_la_sangre(self):
        self.assertIsNotNone(bio.parse_line("LDH                                  300            UI/L             135 - 214"))
        self.assertIsNone(bio.parse_line("LDH Liquido pleural                            840             UI/L"))


if __name__ == "__main__":
    unittest.main()
