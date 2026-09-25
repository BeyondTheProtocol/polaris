#!/usr/bin/env python3
"""test_biomarcadores_fecha_extraccion.py — cada analítica lleva la fecha de su EXTRACCIÓN.

POR QUÉ EXISTE (24-sep-2026). `verificacion`, cotejando el panel público /datos, encontró que
las analíticas se fechaban por la «Fecha Envío» del informe (la del nombre del fichero y del
frontmatter). Dos informes enviados el 18-mar-2024 (extracciones del 15 y del 18) caían en la
misma fecha, el acumulador guarda un punto por fecha, y la analítica del 18 desaparecía mientras
la del 15 salía como del 18. En 75 informes, 10 cambian de fecha 1-4 días.

Autocontenido (BTP_REPO a un tmp), corre igual en CI.
"""
import importlib.util
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="biomarcadores_fecha_test_")
os.environ["BTP_REPO"] = _TMP
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "tools", "state")
RAG_MD = os.path.join(_TMP, "00_FUENTE-DE-VERDAD", "00 · Bandeja de entrada", "RAG_md")
os.makedirs(RAG_MD, exist_ok=True)

LINEA_HB = "Hemoglobina                          **     %s            g/dL             11.7 - 16.1\n"
for nombre, recepcion, hb in (
        ("2024-03-18 - Lab - Analítica - hemograma.md", "18/3/24 11:16:23", "10.0"),
        ("2024-03-18 - Lab - Analítica - hemograma (4).md", "15/3/24 7:53:20", "11.4")):
    with open(os.path.join(RAG_MD, nombre), "w", encoding="utf-8") as f:
        f.write('---\ndate: "2024-03-18"\n---\n'
                "       Fecha Solicitud:%s            Fecha Recepción:%s\n"
                "                    ANÁLISIS CLÍNICOS                Fecha Envío:18/3/24   15:59:30\n"
                % (recepcion.split()[0], recepcion) + LINEA_HB % hb)
# Un informe sin fecha de recepción (transcrito) conserva la de su frontmatter.
with open(os.path.join(RAG_MD, "2026-09-08 - Lab - Analítica - HUVH (transcrito).md"), "w",
          encoding="utf-8") as f:
    f.write('---\ndate: "2026-09-08"\n---\n' + LINEA_HB % "7.8")

_RUTA = os.environ.get("BTP_BIOMARCADORES") or os.path.join(ROOT, "tools", "biomarcadores.py")
_spec = importlib.util.spec_from_file_location("biomarcadores", _RUTA)
bio = importlib.util.module_from_spec(_spec)
sys.modules["biomarcadores"] = bio
_spec.loader.exec_module(bio)


class FechaDeExtraccion(unittest.TestCase):

    def setUp(self):
        payload = bio.build()
        hb = [a for g in payload["grupos"].values() for a in g["analitos"] if a["key"] == "hemoglobina"][0]
        self.pts = {p["fecha"]: p["valor"] for p in hb["puntos"]}

    def test_dos_informes_del_mismo_envio_son_dos_analiticas(self):
        self.assertEqual(self.pts.get("2024-03-15"), 11.4, self.pts)
        self.assertEqual(self.pts.get("2024-03-18"), 10.0, self.pts)

    def test_sin_fecha_de_recepcion_manda_la_del_informe(self):
        self.assertEqual(self.pts.get("2026-09-08"), 7.8, self.pts)

    def test_fecha_imposible_no_se_inventa(self):
        self.assertEqual(bio.fecha_de("2024-03-18 - x.md", "Fecha Recepción:31/2/24\n"), "2024-03-18")


if __name__ == "__main__":
    unittest.main()
