#!/usr/bin/env python3
"""test_biomarcadores_vhio.py — el hueco RAG_md may-ago-2026 no se come el pico de CA15.3 ni la Hb.

POR QUÉ EXISTE (24-sep-2026). `tools/biomarcadores.py` solo lee analíticas OCR'd de
`00_FUENTE-DE-VERDAD/.../RAG_md/`, y ese corpus se para en 2026-04-30. Las 3 analíticas del {{CENTRO}}
(19-ago, 26-ago, 8-sep-2026) que sí importan — CA15.3 sube a 509,5, Hb baja a 7,8 — solo vivían
como tabla markdown en la nota de puesta al día. Se cerró el hueco TRANSCRIBIENDO esa tabla al
mismo formato de línea que ya usa `parse_line()` (alias + valor + unidad + rango), como 3 ficheros
nuevos en RAG_md — no se tocó el parser, se reusó tal cual. Este test es la única defensa de que
esa transcripción sigue produciendo lo que dice: sin él, un fichero borrado o mal editado no lo
nota nadie hasta que alguien mire el panel y falte el dato que más importa.

Autocontenido: no toca RAG_md real (fixture propia en tmp vía BTP_REPO), así que corre igual en
casa base o en CI. `tests/mutantes/biomarcadores.json` mutila `parse_line()` y comprueba que ESTE
test se pone rojo (canario del autor).
"""
import importlib.util
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_TMP = tempfile.mkdtemp(prefix="biomarcadores_vhio_test_")
os.environ["BTP_REPO"] = _TMP
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "tools", "state")

RAG_MD = os.path.join(_TMP, "00_FUENTE-DE-VERDAD", "00 · Bandeja de entrada", "RAG_md")
os.makedirs(RAG_MD, exist_ok=True)

# Mismas 3 fechas y mismas cifras que las transcritas en casa base desde §A6 de
# `puesta-al-dia-clinica-15-ago-a-11-sep-y-perfil-para-terapia-2026-09-11.md` (y cotejadas en
# ESTADO-ACTUAL.md §1 para el 8-sep). Fixture propia: no depende de que casa base no cambie.
_FICHEROS = {
    "2026-08-19 - Lab - Analítica - HUVH.md": """---
date: "2026-08-19"
---
Hemoglobina                    9.0     g/dL         12 - 15
Leucocitos                     3.48    x10^3/uL     4 - 11
Plaquetas                      202     x10^3/uL     140 - 400
Ca 15.3                        215.4   UI/ml        0 - 35
""",
    "2026-08-26 - Lab - Analítica - HUVH.md": """---
date: "2026-08-26"
---
Hemoglobina                    8.4     g/dL         12 - 15
Plaquetas                      187     x10^3/uL     140 - 400
""",
    "2026-09-08 - Lab - Analítica - HUVH.md": """---
date: "2026-09-08"
---
Hemoglobina                    7.8     g/dL         12 - 15
Leucocitos                     3.14    x10^3/uL     4 - 11
Plaquetas                      164     x10^3/uL     140 - 400
Ca 15.3                        509.5   UI/ml        0 - 35
CEA                            0.7     ng/ml
""",
}
for _nombre, _contenido in _FICHEROS.items():
    with open(os.path.join(RAG_MD, _nombre), "w", encoding="utf-8") as _f:
        _f.write(_contenido)

# BTP_BIOMARCADORES: ruta al .py bajo prueba. La usa tools/mutantes.py (tests/mutantes/
# biomarcadores.json) para apuntar a una copia mutada sin tocar el fichero real; por defecto,
# el de esta misma rama/worktree (no el de casa base, aunque BTP_REPO apunte allí para los datos).
_RUTA_BIO = os.environ.get("BTP_BIOMARCADORES") or os.path.join(ROOT, "tools", "biomarcadores.py")
_spec = importlib.util.spec_from_file_location("biomarcadores", _RUTA_BIO)
bio = importlib.util.module_from_spec(_spec)
sys.modules["biomarcadores"] = bio
_spec.loader.exec_module(bio)


def _puntos(payload, key):
    for g in payload["grupos"].values():
        for a in g["analitos"]:
            if a["key"] == key:
                return {p["fecha"]: p for p in a["puntos"]}
    return {}


class TestBiomarcadoresVHIO(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.payload = bio.build()

    def test_ca153_pico_8_sep(self):
        """El pico de CA15.3 (509,5, ×3,5 en 8 semanas) entra y se marca fuera de rango."""
        pts = _puntos(self.payload, "ca153")
        self.assertIn("2026-09-08", pts, "falta el CA15.3 del 8-sep en el timeline")
        p = pts["2026-09-08"]
        self.assertEqual(p["valor"], 509.5)
        self.assertTrue(p["fuera"], "509,5 sobre un límite de 35 debe marcar fuera de rango")

    def test_ca153_basal_19_ago_y_hueco_26_ago(self):
        """19-ago tiene CA15.3 (215,4); 26-ago NO — la tabla origen trae '—' y no se inventa."""
        pts = _puntos(self.payload, "ca153")
        self.assertEqual(pts["2026-08-19"]["valor"], 215.4)
        self.assertNotIn("2026-08-26", pts, "26-ago no tenía CA15.3 en la tabla: no debe aparecer")

    def test_hemoglobina_7_8_en_urgencias(self):
        """Hb 7,8 (transfundida en urgencias el 9-sep) entra y se marca fuera de rango (ref 12-15)."""
        pts = _puntos(self.payload, "hemoglobina")
        self.assertIn("2026-09-08", pts)
        p = pts["2026-09-08"]
        self.assertEqual(p["valor"], 7.8)
        self.assertTrue(p["fuera"])
        # las 3 fechas de la transcripción {{CENTRO}} están todas, ninguna se pierde
        for fecha in ("2026-08-19", "2026-08-26", "2026-09-08"):
            self.assertIn(fecha, pts)

    def test_leucocitos_unidad_10e9_por_l_se_lee_como_10e3_por_ul(self):
        """La tabla {{CENTRO}} da leucocitos en x10^9/L; transcritos como x10^3/uL deben leerse con
        confianza alta (unidad reconocida), no acabar en 'por confirmar' por un choque de unidad."""
        pts = _puntos(self.payload, "leucocitos")
        self.assertEqual(pts["2026-09-08"]["valor"], 3.14)
        self.assertEqual(pts["2026-09-08"]["confianza"], "alta")


if __name__ == "__main__":
    unittest.main()
