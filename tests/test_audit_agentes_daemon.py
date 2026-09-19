#!/usr/bin/env python3
"""test_audit_agentes_daemon.py — un agente que corre como daemon NO está «sin usar».

19-sep-2026. `audit_agentes.py` contaba solo las invocaciones que aparecen en los transcripts
de sesión (`subagent_type`). Un agente lanzado por launchd no deja rastro ahí, así que salía con
CERO usos llevando meses trabajando cada día.

El daño no fue teórico: con ese cero delante, el `orquestador` —que corre cada mañana desde
`com.btp.hoy-compose`, con su latido en `tools/state/heartbeat/orquestador.json`— entró en la
lista de «agentes sin usar» y llegó a discutirse su retirada. Un medidor que solo ve una de las
dos puertas no mide: engaña, y encima con autoridad de número.

Aquí se protege que las dos puertas sumen: sesión + daemon.
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import audit_agentes  # noqa: E402


class TestCuentaLasDosPuertas(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        d = os.path.join(self.tmp, "tools", "state", "observabilidad")
        os.makedirs(d)
        hoy = datetime.now().strftime("%Y-%m-%d")
        with open(os.path.join(d, "observabilidad-%s.jsonl" % hoy), "w", encoding="utf-8") as fh:
            for _ in range(20):
                fh.write(json.dumps({"agente": "orquestador", "estado": "ok"}) + "\n")
            fh.write(json.dumps({"agente": "periodista", "estado": "ok"}) + "\n")
            fh.write(json.dumps({"sin_agente": True}) + "\n")      # ruido: se ignora
            fh.write("esto no es json\n")                          # basura: no revienta
        self._repo = audit_agentes.REPO
        audit_agentes.REPO = self.tmp

    def tearDown(self):
        audit_agentes.REPO = self._repo

    def test_las_pasadas_de_daemon_cuentan(self):
        usos = audit_agentes._usos_daemon(30)
        self.assertEqual(usos["orquestador"], 20)
        self.assertEqual(usos["periodista"], 1)

    def test_una_linea_rota_no_tumba_el_recuento(self):
        self.assertGreater(sum(audit_agentes._usos_daemon(30).values()), 0)

    def test_sin_carpeta_de_observabilidad_devuelve_vacio(self):
        audit_agentes.REPO = tempfile.mkdtemp()
        self.assertEqual(dict(audit_agentes._usos_daemon(30)), {})


class TestSobreElSistemaReal(unittest.TestCase):
    """El orquestador corre cada mañana: no puede salir con cero."""

    def test_el_orquestador_no_sale_sin_usar(self):
        latido = os.path.join(audit_agentes.REPO, "tools", "state", "heartbeat", "orquestador.json")
        if not os.path.exists(latido):
            self.skipTest("sin estado vivo del lazo")
        usos = audit_agentes._usos_daemon(30)
        self.assertGreater(usos.get("orquestador", 0), 0,
                           "corre a diario por launchd y el auditor lo da por muerto")


if __name__ == "__main__":
    unittest.main()
