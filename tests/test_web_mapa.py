#!/usr/bin/env python3
"""test_web_mapa.py — ninguna afirmación de /ciencia se queda sin vigilar.

POR QUÉ EXISTE (17-oct-2026, plan «web al día»). El estado del single-cell en /ciencia caducó sin
que nadie lo supiera, porque no estaba atado a nada. Este test exige dos cosas:
  · que toda prueba de `science.yml` (ES y EN) tenga fila en `tools/web_afirmaciones.json`;
  · que el valor del mapa sea el publicado: si alguien cambia la web a mano, el mapa se pone al
    día en el mismo movimiento, o el detector compararía contra un estado que ya no existe.
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_mapa as M  # noqa: E402
from _entorno import es_espejo  # noqa: E402

ES = """panelRows:
  - component: Exoma (WES)
    method: 'ADN del tejido'
    status: 'Material en el laboratorio'
    statusTone: curso
  - component: Single-cell RNA-seq (10x Genomics Flex)
    status: 'Primeros resultados (octubre 2026)'
    statusTone: curso
"""
EN = ES.replace("Exoma (WES)", "Exome (WES)").replace("Material en el laboratorio", "Material at the lab") \
       .replace("Primeros resultados (octubre 2026)", "First results (October 2026)")


def fila(id_, ce, cn, ve, vn, tono="curso"):
    return {"id": id_, "pagina": "ciencia", "componente": {"es": ce, "en": cn},
            "valor": {"es": ve, "en": vn}, "tono": tono}


MAPA = {"afirmaciones": [
    fila("wes", "Exoma (WES)", "Exome (WES)", "Material en el laboratorio", "Material at the lab"),
    fila("scrna", "Single-cell RNA-seq (10x Genomics Flex)", "Single-cell RNA-seq (10x Genomics Flex)",
         "Primeros resultados (octubre 2026)", "First results (October 2026)"),
]}


class Comprobar(unittest.TestCase):
    def test_cuadra(self):
        self.assertEqual(M.comprobar(MAPA, {"es": ES, "en": EN}), [])

    def test_prueba_nueva_sin_fila_falla(self):
        es = ES + "  - component: Proteómica\n    status: 'Pendiente'\n    statusTone: pendiente\n"
        fallos = M.comprobar(MAPA, {"es": es, "en": EN})
        self.assertTrue(any("Proteómica" in f and "no en el mapa" in f for f in fallos), fallos)

    def test_el_caso_del_17_oct_deriva(self):
        """La web cambió a mano (PR #281) y el mapa no: tiene que saltar."""
        viejo = json.loads(json.dumps(MAPA))
        viejo["afirmaciones"][1]["valor"]["es"] = "Material en el laboratorio"
        fallos = M.comprobar(viejo, {"es": ES, "en": EN})
        self.assertTrue(any("Single-cell" in f and "pon el mapa al día" in f for f in fallos), fallos)

    def test_tono_distinto_falla(self):
        otro = json.loads(json.dumps(MAPA))
        otro["afirmaciones"][0]["tono"] = "hecho"
        self.assertTrue(M.comprobar(otro, {"es": ES, "en": EN}))

    def test_fila_que_ya_no_esta_en_la_web(self):
        self.assertTrue(any("ya no en la web" in f for f in
                            M.comprobar(MAPA, {"es": ES.split("  - component: Single")[0], "en": EN})))

    def test_idioma_ilegible_no_es_un_pase(self):
        self.assertTrue(any("no pude leer" in f for f in M.comprobar(MAPA, {"es": ES, "en": None})))

    def test_parser_quita_comillas(self):
        self.assertEqual(M.estados_ciencia(ES)["Exoma (WES)"],
                         {"status": "Material en el laboratorio", "statusTone": "curso"})


class ElMapaReal(unittest.TestCase):
    def test_estructura(self):
        mapa = M.cargar()
        ids = [a["id"] for a in mapa["afirmaciones"]]
        self.assertEqual(len(ids), len(set(ids)), "ids repetidos")
        for a in mapa["afirmaciones"]:
            for i in M.IDIOMAS:
                self.assertTrue(a["componente"].get(i) and a["valor"].get(i), a["id"])
            # nada se propone solo hasta que voz-titular y diseno aprueben las frases
            self.assertEqual(a.get("transiciones"), {}, a["id"])
        self.assertIn("content/*/press.yml", mapa["a_mano"]["rutas"])

    def test_cubre_la_web_publicada(self):
        if es_espejo():
            self.skipTest("el espejo público no tiene el repo de la web")
        textos = M.textos_publicados()
        if any(t is None for t in textos.values()):
            self.skipTest("no está el repo de la web en %s" % M.WEB)
        self.assertEqual(M.comprobar(M.cargar(), textos), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
