#!/usr/bin/env python3
"""test_etiquetar_hilos.py — el barrido PROPONE, y lo que escribe no puede quedarse a medias.

El riesgo de esta tool no es equivocarse etiquetando: es tocar el Tablero, que es la fuente única.
Por eso lo que se cubre aquí es lo que duele si falla — que sin `--si` no escriba nada, que un
fichero mal rellenado NO se aplique a medias, que cerrar exija motivo escrito, que renombrar no
pierda el texto que dijo {{TITULAR}}, y que nunca proponga cerrar algo clínico, legal o privado.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))


def _hilo(hid, **kw):
    h = {"id": hid, "titulo": hid, "estado": "en_curso", "categoria": "otros"}
    h.update(kw)
    return h


class Barrido(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="etiq-")
        os.makedirs(os.path.join(self.tmp, "tools", "state"))
        os.environ["BTP_REPO"] = self.tmp
        sys.modules.pop("etiquetar_hilos", None)
        import etiquetar_hilos
        self.e = etiquetar_hilos
        self.e.CASA = self.tmp
        self.seg = {"actualizado": "2026-07-30", "hilos": [
            _hilo("completo", objetivo_ned="directo", dueno="tecnico", plazo="2026-09-01"),
            _hilo("pelado"),
            _hilo("habla", estado="por_confirmar", titulo="pues ponme tarea de buscar labs no?"),
            _hilo("clinico-feo", estado="por_confirmar", categoria="clinico",
                  titulo="deberia mandar mail al lab?"),
            _hilo("privado-feo", estado="por_confirmar", privado=True,
                  titulo="manda un email a quien sea?"),
            _hilo("cerrado", estado="hecho"),
        ]}
        self._guardar()

    def _guardar(self):
        with open(os.path.join(self.tmp, "tools", "state", "seguimiento.json"),
                  "w", encoding="utf-8") as f:
            json.dump(self.seg, f, ensure_ascii=False)

    def _releer(self):
        with open(os.path.join(self.tmp, "tools", "state", "seguimiento.json"),
                  encoding="utf-8") as f:
            return {h["id"]: h for h in json.load(f)["hilos"]}

    def tearDown(self):
        os.environ.pop("BTP_REPO", None)
        sys.modules.pop("etiquetar_hilos", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_solo_mira_los_abiertos(self):
        self.assertNotIn("cerrado", [h["id"] for h in self.e.abiertos()])

    def test_lo_clinico_y_lo_privado_nunca_se_proponen_para_cerrar(self):
        por_id = {d["id"]: d for d in self.e.revisar()["hilos"]}
        self.assertEqual("revisar-si-cerrar", por_id["habla"]["sugerencia"])
        self.assertEqual("etiquetar", por_id["clinico-feo"]["sugerencia"])
        self.assertEqual("etiquetar", por_id["privado-feo"]["sugerencia"])

    def test_sin_si_no_escribe_nada(self):
        cambios = {"cambios": [{"id": "pelado", "veredicto": "etiquetar",
                                "objetivo_ned": "directo"}]}
        r = self.e.aplicar(cambios)
        self.assertTrue(r["ensayo"])
        self.assertFalse(self._releer()["pelado"].get("objetivo_ned"))

    def test_un_fichero_mal_rellenado_no_se_aplica_a_medias(self):
        cambios = {"cambios": [
            {"id": "pelado", "veredicto": "etiquetar", "objetivo_ned": "directo"},
            {"id": "habla", "veredicto": "cerrar", "motivo_cierre": ""},   # ← sin motivo
        ]}
        r = self.e.aplicar(cambios, escribir=True)
        self.assertEqual(0, r["aplicados"])
        self.assertEqual(1, len(r["problemas"]))
        self.assertFalse(self._releer()["pelado"].get("objetivo_ned"))   # ni el bueno pasó

    def test_etiquetar_escribe_ned_y_dueno_sin_pisar_uno_que_ya_habia(self):
        cambios = {"cambios": [
            {"id": "pelado", "veredicto": "etiquetar", "objetivo_ned": "abre la puerta X",
             "dueno": "tecnico"},
            {"id": "completo", "veredicto": "etiquetar", "objetivo_ned": "nuevo",
             "dueno": "prensa"},
        ]}
        self.assertEqual(2, self.e.aplicar(cambios, escribir=True)["aplicados"])
        d = self._releer()
        self.assertEqual("abre la puerta X", d["pelado"]["objetivo_ned"])
        self.assertEqual("tecnico", d["pelado"]["dueno"])
        self.assertEqual("tecnico", d["completo"]["dueno"])   # el dueño que ya había manda

    def test_renombrar_guarda_lo_que_ella_dijo(self):
        cambios = {"cambios": [{"id": "habla", "veredicto": "etiquetar",
                                "objetivo_ned": "la lista de labs es acceso",
                                "titulo": "Buscar y ordenar la lista de labs"}]}
        self.e.aplicar(cambios, escribir=True)
        h = self._releer()["habla"]
        self.assertEqual("Buscar y ordenar la lista de labs", h["titulo"])
        self.assertEqual("pues ponme tarea de buscar labs no?", h["titulo_original"])

    def test_cerrar_deja_el_motivo_escrito(self):
        cambios = {"cambios": [{"id": "habla", "veredicto": "cerrar",
                                "motivo_cierre": "pregunta ya contestada"}]}
        self.e.aplicar(cambios, escribir=True)
        h = self._releer()["habla"]
        self.assertEqual("descartado", h["estado"])
        self.assertEqual("pregunta ya contestada", h["motivo_cierre"])

    def test_dejar_no_toca_nada(self):
        cambios = {"cambios": [{"id": "pelado", "veredicto": "dejar"}]}
        self.assertEqual(0, self.e.aplicar(cambios, escribir=True)["aplicados"])
        self.assertFalse(self._releer()["pelado"].get("objetivo_ned"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
