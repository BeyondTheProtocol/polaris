#!/usr/bin/env python3
"""test_biomarcadores_hormonas.py — marcadores y hormonas nuevos, valores censurados («<15») y la
analítica de MD Anderson restringida a lo cotejado.

POR QUÉ EXISTE (26-sep-2026). {{TITULAR}} pidió «más analítica» en /datos: CA 19-9, CA 27.29, beta-2-
microglobulina, cromogranina A, NSE, TSH, T4 libre, estradiol, FSH y LH estaban en los informes y
no se extraían. Al añadirlos salió un fallo viejo: la regex saltaba el «<» y el panel público
enseñaba «<9 U/L» como 9 (ALT/AST abr-2024, PCR may-2024). Y el informe de MD Anderson trae su
hemograma en otro formato («10 /µL» partido): de él solo entran las claves nuevas.

Autocontenido (BTP_REPO a un tmp), corre igual en CI.
"""
import importlib.util
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_TMP = tempfile.mkdtemp(prefix="biomarcadores_hormonas_test_")
os.environ["BTP_REPO"] = _TMP
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "tools", "state")

RAG_MD = os.path.join(_TMP, "00_FUENTE-DE-VERDAD", "00 · Bandeja de entrada", "RAG_md")
os.makedirs(RAG_MD, exist_ok=True)

with open(os.path.join(RAG_MD, "2024-04-11 - Lab - Analítica - bioquímica.md"), "w", encoding="utf-8") as f:
    f.write('---\ndate: "2024-04-11"\n---\n'
            "GPT (ALT)                             <9          U/L              7 -  35\n"
            "Ca 19.9                               26.0         UI/ml           0.0       -       37.0\n"
            "Beta 2-microglobulina                 1.40         mg/L           1.00       -       2.40\n"
            "FSH                                   8.2         mUI/ml          1.5 -                   116.0\n"
            "Estradiol                             <12         pg/ml            0 -                    440\n"
            "Estradiol libre                       99          pg/ml            0 -                    440\n"
            "TSH                              *    0.145       uUI/ml       0.551     -   4.781\n"
            "T4 libre                         *    0.84             ng/dl                  0.89    -   1.76\n"
            "LDH                                   300            UI/L             135 - 214\n")

with open(os.path.join(RAG_MD, "2024-07-22 - Prueba - X - Bioquímica (MD Anderson).md"), "w", encoding="utf-8") as f:
    f.write('---\ndate: "2024-07-22"\n---\n'
            "                       Fecha toma muestra: 12/Jul/2024\n"
            "Hematíes:                    4.58    10 /µL               4.20 - 5.40 10^6/µL\n"
            "LDH                                   999            UI/L             135 - 214\n"
            " CA 19-9                                                            * 42.8     U/mL                    < 34 U/mL\n"
            " (i) Ca 27.29                                                       * 64.9     U/mL                    < 38.6 U/mL\n"
            " Beta-2-Microglobulina                                                1.46     mcg/mL                  0.8 - 2.4 mcg/mL\n"
            " (i) Cromogranina A                                                  57.67     mcg/L                   < 100 mcg/L\n"
            " TSH                                                    2.25     µUI/mL           0.35 - 4.94 µUI/mL\n"
            "LH                                                * <0.216      mU/mL            2.58 - 12.1 mU/mL F. Folicular\n")

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


class TestHormonasYCensurados(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.p = bio.build()

    def test_censurado_lleva_su_comparador(self):
        alt = _serie(self.p, "gpt")["2024-04-11"]
        self.assertEqual(alt["valor"], 9.0)
        self.assertEqual(alt.get("cmp"), "<", "«<9» se publicaría como 9")
        self.assertFalse(alt["fuera"], "«<9» con rango 7-35 no se puede dar por fuera")

    def test_estradiol_censurado_y_no_confunde_estradiol_libre(self):
        e = _serie(self.p, "estradiol")
        self.assertEqual(e["2024-04-11"]["valor"], 12.0)
        self.assertEqual(e["2024-04-11"].get("cmp"), "<")
        self.assertIsNone(bio.parse_line("Estradiol libre   99   pg/ml   0 - 440"), "«Estradiol libre» es otra prueba")

    def test_marcadores_y_hormonas_de_murcia(self):
        self.assertEqual(_serie(self.p, "ca199")["2024-04-11"]["valor"], 26.0)
        self.assertEqual(_serie(self.p, "b2m")["2024-04-11"]["valor"], 1.40)
        self.assertEqual(_serie(self.p, "fsh")["2024-04-11"]["valor"], 8.2)
        tsh = _serie(self.p, "tsh")["2024-04-11"]
        self.assertEqual((tsh["valor"], tsh["fuera"], tsh["confianza"]), (0.145, True, "alta"))
        self.assertTrue(_serie(self.p, "t4l")["2024-04-11"]["fuera"])

    def test_md_anderson_fecha_de_la_toma(self):
        self.assertIn("2024-07-12", _serie(self.p, "ca199"), "la fecha es la de la toma, no la del informe")
        self.assertNotIn("2024-07-22", _serie(self.p, "ca199"))

    def test_md_anderson_formato(self):
        ca = _serie(self.p, "ca199")["2024-07-12"]
        self.assertEqual((ca["valor"], ca["fuera"], ca["ref_high"]), (42.8, True, 34.0))
        self.assertEqual(_serie(self.p, "ca2729")["2024-07-12"]["valor"], 64.9, "el «(i)» delante tapaba el nombre")
        self.assertEqual(_serie(self.p, "b2m")["2024-07-12"]["confianza"], "alta", "mcg/mL = mg/L")
        self.assertEqual(_serie(self.p, "cga")["2024-07-12"]["valor"], 57.67)
        self.assertEqual(_serie(self.p, "tsh")["2024-07-12"]["confianza"], "alta", "µUI/mL es la unidad de TSH")
        lh = _serie(self.p, "lh")["2024-07-12"]
        self.assertEqual((lh["valor"], lh.get("cmp"), lh["fuera"]), (0.216, "<", True))
        self.assertEqual(lh.get("ref_fase"), "folicular", "MD Anderson imprime un rango por fase: tiene que decirse cuál")
        self.assertIsNone(_serie(self.p, "tsh")["2024-07-12"].get("ref_fase"))

    def test_md_anderson_no_entra_en_series_viejas(self):
        self.assertNotIn("2024-07-12", _serie(self.p, "ldh"), "el hemograma/bioquímica de MD Anderson no está cotejado")
        self.assertNotIn("2024-07-12", _serie(self.p, "hematies"))

    def test_unidades(self):
        self.assertEqual(bio.unit_family("mU/L"), "uui/ml")
        self.assertEqual(bio.unit_family("mUI/ml"), "mui/ml")
        self.assertEqual(bio.unit_family("U/mL"), "ui/ml")
        self.assertEqual(bio.unit_family("mcg/L"), "ug/l")
        self.assertEqual(bio.unit_family("UI/L"), "u/l", "la LDH no puede caer en otra familia")


if __name__ == "__main__":
    unittest.main(verbosity=2)
