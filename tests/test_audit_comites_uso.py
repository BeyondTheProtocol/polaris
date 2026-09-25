#!/usr/bin/env python3
"""tests/test_audit_comites_uso.py — La auditoría de superficie mira las DOS fuentes.

Existe por el fallo del 30-jul-26: `--uso` contaba solo `subagent_type` en los transcripts, así que
todo comité que corre como daemon salía con CERO invocaciones. El `orquestador`, con 830 ejecuciones
reales, figuraba como "nunca invocado", y por poco cuesta el archivado de 6 agentes cableados.

Cubre lo que se rompería en silencio: que se lea la bitácora, que NO se cuente dos veces lo que ya
ven los transcripts (`job == "sesion"`), que los alias sigan valiendo y que una línea a medio
escribir no tumbe el informe.

Bitácora mockeada en un temporal (BTP_OBS_DIR). No toca el estado real. Exit 0 = OK, 1 = falló.
"""

import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))


class TestUsoDaemon(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="btp_test_uso_")
        os.environ["BTP_OBS_DIR"] = self.tmpdir
        # Import tardío: el módulo resuelve OBS_DIR al importarse.
        for mod in ("audit_comites",):
            sys.modules.pop(mod, None)
        import audit_comites
        self.mod = audit_comites

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("BTP_OBS_DIR", None)
        sys.modules.pop("audit_comites", None)

    def _bitacora(self, dia, registros):
        path = os.path.join(self.tmpdir, "observabilidad-%s.jsonl" % dia)
        with open(path, "a", encoding="utf-8") as f:
            for r in registros:
                f.write(json.dumps(r) + "\n")

    def test_cuenta_daemons_y_descarta_las_de_sesion(self):
        hoy = datetime.date.today().isoformat()
        self._bitacora(hoy, [
            {"agente": "orquestador", "job": "run_agent", "resultado": "ok"},
            {"agente": "orquestador", "job": "33b27084a4", "resultado": "ok"},
            # Esta la escribe el hook y los transcripts YA la cuentan: no se suma.
            {"agente": "orquestador", "job": "sesion", "resultado": "ok"},
        ])
        cuenta, leidos = self.mod.uso_daemon()
        self.assertEqual(cuenta.get("orquestador"), 2)
        self.assertEqual(leidos, 1)

    def test_aplica_alias_de_nombres_historicos(self):
        hoy = datetime.date.today().isoformat()
        self._bitacora(hoy, [
            {"agente": "contacto", "job": "run_agent", "resultado": "ok"},
            {"agente": "consejero-marketing", "job": "run_agent", "resultado": "ok"},
        ])
        cuenta, _ = self.mod.uso_daemon()
        self.assertEqual(cuenta.get("consejero-marketing"), 2)
        self.assertNotIn("contacto", cuenta)

    def test_ventana_de_dias_descarta_la_bitacora_vieja(self):
        hoy = datetime.date.today()
        viejo = (hoy - datetime.timedelta(days=45)).isoformat()
        self._bitacora(hoy.isoformat(), [{"agente": "prensa", "job": "run_agent"}])
        self._bitacora(viejo, [{"agente": "prensa", "job": "run_agent"}])
        self.assertEqual(self.mod.uso_daemon()[0].get("prensa"), 2)
        self.assertEqual(self.mod.uso_daemon(dias=30)[0].get("prensa"), 1)

    def test_linea_corrupta_no_tumba_el_informe(self):
        hoy = datetime.date.today().isoformat()
        path = os.path.join(self.tmpdir, "observabilidad-%s.jsonl" % hoy)
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"agente": "git", "job": "run_agent"}\n')
            f.write('{"agente": "git", "job":\n')           # a medio escribir
            f.write('{"agente": "git", "job": "run_agent"}\n')
        self.assertEqual(self.mod.uso_daemon()[0].get("git"), 2)

    def test_sin_bitacora_devuelve_vacio_sin_reventar(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        cuenta, leidos = self.mod.uso_daemon()
        self.assertEqual(cuenta, {})
        self.assertEqual(leidos, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
