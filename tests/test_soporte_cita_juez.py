#!/usr/bin/env python3
"""test_soporte_cita_juez.py — el juez semántico (fase 2) no puede inventarse el apoyo.

POR QUÉ EXISTE (24-sep-2026). La fase 1 da RESPALDA_LITERAL cuando las cifras están en la fuente,
aunque se atribuyan a otra cohorte, endpoint o brazo: en el benchmark, los 13 volteos pasaban.
El juez local lo mira, pero un 8B puede alucinar: su `cita` tiene que estar LITERAL en la fuente.
Sin modelo y sin red: `responder` inyectado. Mutantes: tests/mutantes/soporte_cita.json.
"""
import importlib.util
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
_RUTA = os.environ.get("BTP_SOPORTE") or os.path.join(ROOT, "tools", "soporte_cita.py")
_spec = importlib.util.spec_from_file_location("soporte_cita_juez_bajo_prueba", _RUTA)
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)

FUENTE = ("In the hormone receptor-positive cohort, the median progression-free survival was 10.1 "
          "months in the trastuzumab deruxtecan group and 5.4 months in the physician's choice group. "
          "Among all patients, the median progression-free survival was 9.9 months in the trastuzumab "
          "deruxtecan group and 5.1 months in the physician's choice group.")
FRASE_MAL = "En la cohorte HR+ la mediana de SLP fue de 9,9 meses con T-DXd frente a 5,1."
CITA_REAL = "Among all patients, the median progression-free survival was 9.9 months"


def responde(veredicto, cita=CITA_REAL, eje="poblacion"):
    return lambda prompt, system: json.dumps({"veredicto": veredicto, "eje": eje, "cita": cita})


class Juez(unittest.TestCase):
    def test_contradice_con_cita_literal(self):
        r = sc.juez(FRASE_MAL, FUENTE, responder=responde("contradice"))
        self.assertEqual(r["estado"], sc.CONTRADICE)
        self.assertEqual(r["eje"], "poblacion")

    def test_cita_inventada_no_cuenta(self):
        r = sc.juez(FRASE_MAL, FUENTE, responder=responde(
            "contradice", cita="In the HR-positive cohort the PFS was 12.0 months with the drug"))
        self.assertEqual(r["veredicto"], "no_sabe")
        self.assertEqual(r["estado"], sc.RESPALDA)          # vuelve al literal: no acusa ni avala

    def test_cita_trivial_no_cuenta(self):
        r = sc.juez(FRASE_MAL, FUENTE, responder=responde("contradice", cita="9.9 months"))
        self.assertEqual(r["veredicto"], "no_sabe")

    def test_json_roto_pendiente(self):
        r = sc.juez(FRASE_MAL, FUENTE, responder=lambda p, s: "creo que no cuadra")
        self.assertEqual(r["estado"], sc.PENDIENTE)

    def test_veredicto_desconocido_pendiente(self):
        r = sc.juez(FRASE_MAL, FUENTE, responder=lambda p, s: '{"veredicto": "quizas", "cita": ""}')
        self.assertEqual(r["estado"], sc.PENDIENTE)

    def test_modelo_caido_pendiente(self):
        def boom(p, s):
            raise OSError("ollama no responde")
        self.assertEqual(sc.juez(FRASE_MAL, FUENTE, responder=boom)["estado"], sc.PENDIENTE)

    def test_contexto_trae_las_frases_con_las_cifras(self):
        ctx = sc.contexto_juez(FRASE_MAL, FUENTE)
        self.assertIn("9.9 months", ctx)
        self.assertLessEqual(len(ctx), sc.MAX_CONTEXTO)

    def test_juez_solo_corre_sobre_respalda_literal(self):
        llamado = []

        def espia(p, s):
            llamado.append(1)
            return json.dumps({"veredicto": "respalda", "eje": "-", "cita": CITA_REAL})
        r = sc.soporte("mediana de 12,9 meses", "PMID:1", fetch=lambda c: ("pmid", "1", FUENTE),
                       texto_completo=lambda t, i: None, con_juez=True, responder=espia)
        self.assertEqual(r["estado"], sc.NO_RESPALDA)
        self.assertEqual(llamado, [], "con la cifra ya ausente, el juez no hace falta")

    def test_soporte_con_juez_contradice(self):
        r = sc.soporte(FRASE_MAL, "PMID:1", fetch=lambda c: ("pmid", "1", FUENTE),
                       con_juez=True, responder=responde("contradice"))
        self.assertEqual(r["estado"], sc.CONTRADICE)
        self.assertEqual(sc._rc([r]), 2)

    def test_nace_apagado(self):
        """Se enciende solo tras pasar el benchmark; hasta entonces el panel no lo llama."""
        self.assertIsInstance(sc.JUEZ_ACTIVO, bool)

    def test_bench_mide(self):
        ruta = os.path.join(ROOT, "evals", "soporte_cita_juez.json")
        met, filas = sc.bench(ruta, responder=lambda p, s: '{"veredicto": "no_sabe", "eje": "-", "cita": ""}')
        self.assertEqual(met["n"], len(filas))
        self.assertFalse(met["pasa"], "un juez que nunca decide no puede pasar el benchmark")


if __name__ == "__main__":
    unittest.main(verbosity=1)
