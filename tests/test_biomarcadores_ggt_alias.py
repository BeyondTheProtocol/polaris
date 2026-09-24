#!/usr/bin/env python3
"""test_biomarcadores_ggt_alias.py — «GAMMA GT» (así la escribe casi todo el corpus histórico
de Morales Meseguer) tiene que casar con el analito GGT.

POR QUÉ EXISTE (24-sep-2026, deuda `biomarcadores-gammagt-alias-no-casa`). Detectado al verificar
el efecto de meter las analíticas {{CENTRO}}: GGT solo tenía 3 puntos en 5 años de corpus porque los
aliases de tools/biomarcadores.py eran ["ggt", "gamma-glutamil", "gamma glutamil"] y ninguno casa
con "GAMMA GT" (dos palabras, sin guion, abreviado). GGT es enzima hepática y entra en el panel
hepático de la vista de primer vistazo de /datos (F1): sin este alias, esa serie teóricamente
llevaba solo 3 puntos de 5 años.

Autocontenido (BTP_REPO a un tmp), corre igual en CI.
"""
import importlib.util
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_TMP = tempfile.mkdtemp(prefix="biomarcadores_ggt_test_")
os.environ["BTP_REPO"] = _TMP
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "tools", "state")

RAG_MD = os.path.join(_TMP, "00_FUENTE-DE-VERDAD", "00 · Bandeja de entrada", "RAG_md")
os.makedirs(RAG_MD, exist_ok=True)

with open(os.path.join(RAG_MD, "2024-01-15 - Lab - Analítica - bioquímica.md"), "w",
          encoding="utf-8") as f:
    f.write('---\ndate: "2024-01-15"\n---\n'
            "GAMMA GT                             *      55             U/L              2   -   38\n")

_RUTA_BIO = os.environ.get("BTP_BIOMARCADORES") or os.path.join(ROOT, "tools", "biomarcadores.py")
_spec = importlib.util.spec_from_file_location("biomarcadores", _RUTA_BIO)
bio = importlib.util.module_from_spec(_spec)
sys.modules["biomarcadores"] = bio
_spec.loader.exec_module(bio)


class TestGammaGTAlias(unittest.TestCase):

    def test_gamma_gt_casa_con_ggt(self):
        payload = bio.build()
        ggt = None
        for g in payload["grupos"].values():
            for a in g["analitos"]:
                if a["key"] == "ggt":
                    ggt = a
        self.assertIsNotNone(ggt, "GAMMA GT no se reconoció como GGT")
        pts = {p["fecha"]: p for p in ggt["puntos"]}
        self.assertIn("2024-01-15", pts)
        self.assertEqual(pts["2024-01-15"]["valor"], 55.0)
        self.assertEqual(ggt["unidad"], "U/L")


if __name__ == "__main__":
    unittest.main()
