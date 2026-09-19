#!/usr/bin/env python3
"""test_onco.py — OnCo se consulta en local, sin red en los tests y sin instalar nada.

Protege (19-sep-2026): buscar exige todos los términos; `novedades` solo trae lo nuevo o lo
re-verificado y respeta el filtro; `sync` no sustituye el volcado local si el remoto viene vacío
ni si el build no cambió.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import onco  # noqa: E402

R1 = {"id": "fgfr1-amp", "kind": "target", "name": "FGFR1 amplification", "aka": [], "tldr": "breast", "asOf": "2026-09-01"}
R2 = {"id": "nct1", "kind": "trial", "name": "Neoantigen vaccine in HR+ breast cancer", "aka": [],
      "tldr": "phase 1", "asOf": "2026-09-01", "nct": "NCT1"}
R3 = {"id": "gbm", "kind": "cancer", "name": "Glioblastoma", "aka": [], "tldr": "brain", "asOf": "2026-09-01"}


class TestOnco(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        onco.DIR = self.tmp

    def _escribir(self, nombre, registros):
        with open(os.path.join(self.tmp, nombre), "w") as f:
            f.write("\n".join(json.dumps(r) for r in registros) + "\n")

    def test_buscar_exige_todos_los_terminos_y_filtra_tipo(self):
        regs = [R1, R2, R3]
        self.assertEqual([r["id"] for r in onco.buscar(regs, "neoantigen breast")], ["nct1"])
        self.assertEqual(onco.buscar(regs, "breast", tipo="cancer"), [])
        self.assertEqual({r["id"] for r in onco.buscar(regs, "breast")}, {"fgfr1-amp", "nct1"})

    def test_novedades_nuevo_actualizado_y_filtro(self):
        previo = [R1, R3]
        actual = [dict(R1, asOf="2026-09-18"), R2, dict(R3, asOf="2026-09-18")]
        res = {r["id"]: r["_novedad"] for r in onco.novedades(actual, previo)}
        self.assertEqual(res, {"fgfr1-amp": "actualizado", "nct1": "nuevo"})  # glioblastoma fuera por filtro
        self.assertEqual(len(onco.novedades(actual, previo, None)), 3)
        self.assertEqual(onco.novedades(previo, previo), [])

    def test_sync_no_rebaja_si_build_igual_ni_pisa_con_volcado_vacio(self):
        self._escribir("all.ndjson", [R1])
        with open(os.path.join(self.tmp, "meta.json"), "w") as f:
            json.dump({"built": "B1"}, f)
        llamadas = []

        def falso_get(url, destino=None):
            llamadas.append(url)
            if url.endswith("meta.json"):
                return json.dumps({"built": self.build}).encode()
            open(destino, "w").close()  # volcado remoto vacío

        onco._get, original = falso_get, onco._get
        try:
            self.build = "B1"
            self.assertFalse(onco.sync()["cambio"])
            self.assertEqual(len(llamadas), 1)  # solo meta
            self.build = "B2"
            with self.assertRaises(RuntimeError):
                onco.sync()
            self.assertEqual([r["id"] for r in onco.cargar()], ["fgfr1-amp"])  # el local sigue intacto
        finally:
            onco._get = original

    def test_cli_sin_volcado_lo_dice(self):
        with redirect_stdout(io.StringIO()) as out:
            rc = onco.main(["buscar", "x"])
        self.assertEqual(rc, 1)
        self.assertIn("sync", out.getvalue())


class TestRadarOnco(unittest.TestCase):
    """La fuente OnCo del radar: solo temas con claves, solo novedades, fallo = error dicho."""

    def setUp(self):
        import radar_ned_diario as rad
        self.rad = rad
        rad._ONCO.clear()

    def tearDown(self):
        self.rad._ONCO.clear()

    def test_solo_temas_con_claves_y_solo_lo_que_casa(self):
        rad = self.rad
        rad._ONCO.update({"items": [dict(R2, _novedad="nuevo", route="/trials/nct1/"), dict(R3, _novedad="nuevo")],
                          "texto": onco._texto})
        items, err = rad.fuente_onco({"clave": "vacuna-frio"}, "", "")
        self.assertIsNone(err)
        self.assertEqual([i["ref"] for i in items], ["NCT1"])
        self.assertTrue(items[0]["uid"].startswith("onco:nct1:"))
        self.assertEqual(items[0]["url"], "https://onco.cc/trials/nct1/")
        self.assertEqual(rad.fuente_onco({"clave": "prrt"}, "", ""), ([], None))

    def test_fallo_de_onco_se_dice_no_se_calla(self):
        rad = self.rad
        rad._ONCO["error"] = "OnCo no disponible (URLError)"
        self.assertEqual(rad.fuente_onco({"clave": "fgfr4"}, "", ""), ([], "OnCo no disponible (URLError)"))

    def test_esta_en_fuentes(self):
        self.assertIn(self.rad.fuente_onco, [f for _, f, _ in self.rad.FUENTES])


if __name__ == "__main__":
    unittest.main()
