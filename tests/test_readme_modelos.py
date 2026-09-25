#!/usr/bin/env python3
"""test_readme_modelos.py — la tabla de modelos del README cuenta las puertas que hay de verdad.

POR QUÉ (22-sep-2026). {{TITULAR}} pidió que el README nombre los LLMs. Una lista a mano en un README
público se desfasa en silencio: se añade un proveedor en `tools/enruta.py` y la portada sigue
diciendo lo de antes. Este test ata las dos cosas: cada puerta de `enruta.PROVEEDORES` sale en la
tabla, la tabla no cuenta puertas que no existen, y el número que dice el texto es el real.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import enruta  # noqa: E402

# Nombre con el que cada puerta aparece en la tabla del README.
NOMBRE = {"claude": "Claude", "local": "Local", "grok": "Grok", "perplexity": "Perplexity",
          "gemini": "Gemini", "chatgpt": "ChatGPT", "nvidia": "NVIDIA", "glm": "GLM"}


def _tabla():
    txt = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    m = re.search(r"### 🧠 Qué modelos hay detrás(.*?)(?=\n### |\n## |\Z)", txt, re.S)
    return txt, (m.group(1) if m else "")


class ReadmeModelos(unittest.TestCase):
    def test_la_seccion_existe(self):
        _txt, sec = _tabla()
        self.assertTrue(sec, "falta la sección «🧠 Qué modelos hay detrás» en el README")

    def test_cada_puerta_real_sale_en_la_tabla(self):
        _txt, sec = _tabla()
        filas = [ln for ln in sec.splitlines() if ln.startswith("| ") and "---" not in ln][1:]
        primeras = [ln.split("|")[1] for ln in filas]
        for clave in enruta.PROVEEDORES:
            self.assertIn(clave, NOMBRE, "puerta nueva en enruta.py sin nombre en este test: %s" % clave)
            self.assertTrue(any(NOMBRE[clave] in c for c in primeras),
                            "la puerta %r no sale en la tabla del README" % clave)
        self.assertEqual(len(filas), len(enruta.PROVEEDORES),
                         "la tabla tiene %d filas y enruta.py %d puertas" % (len(filas), len(enruta.PROVEEDORES)))

    def test_el_numero_del_texto_es_el_real(self):
        _txt, sec = _tabla()
        m = re.search(r"\*\*(\d+) puertas\*\*", sec)
        self.assertTrue(m, "el texto ya no dice «**N puertas**»")
        self.assertEqual(int(m.group(1)), len(enruta.PROVEEDORES))

    def test_quien_ve_lo_sensible_coincide_con_el_codigo(self):
        """La promesa pública tiene que coincidir con el código: en `enruta`, solo los destinos
        de confianza pueden recibir material sensible."""
        _txt, sec = _tabla()
        con_si = [ln.split("|")[1] for ln in sec.splitlines() if ln.startswith("| ") and "✅" in ln]
        confiables = [NOMBRE[k] for k, v in enruta.PROVEEDORES.items() if v.get("confianza")]
        self.assertEqual(len(con_si), len(confiables), (con_si, confiables))
        for n in confiables:
            self.assertTrue(any(n in c for c in con_si),
                            "%s es de confianza en enruta.py y el README no lo dice" % n)


if __name__ == "__main__":
    unittest.main()
