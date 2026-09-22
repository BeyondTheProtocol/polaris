#!/usr/bin/env python3
"""test_salud_reconciliar.py — «lo estoy mirando» no puede sobrevivir a quien lo miraba.

El acuse nació el 3-jul-26 para que el primer mensaje que le llega a {{TITULAR}} no sea un grito seco:
«🔧 Detecté X, lo estoy mirando». A cambio, la alerta se calla mientras alguien la atiende.

El 31-jul-26 se vio el precio de no comprobar nunca si ese alguien sigue existiendo: cinco acuses
llevaban días en `en_arreglo` prometiendo un encargo que había muerto el primer minuto (caducado o
rc=1). Uno de ellos, 89 horas. La alerta no volvió a llamar ni una vez.

Es el MISMO fallo que el acuse vino a arreglar —«decía que se ponía y detrás no miraba nadie»—
reaparecido una capa más arriba. Y es peor que el grito, porque un silencio que se sostiene solo
parece que todo va bien.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))


class Reconciliar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="salud-rec-")
        os.environ["BTP_STATE_DIR"] = self.tmp
        for sub in ("pending", "processing", "failed"):
            os.makedirs(os.path.join(self.tmp, "queue", sub))
        for m in ("salud", "cola"):
            sys.modules.pop(m, None)
        import salud
        self.s = salud

    def tearDown(self):
        os.environ.pop("BTP_STATE_DIR", None)
        for m in ("salud", "cola"):
            sys.modules.pop(m, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _job(self, sub, jid):
        p = os.path.join(self.tmp, "queue", sub, "0-20260731T000000000000-%s.json" % jid)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"id": jid}, f)

    def _acuses(self):
        with open(os.path.join(self.tmp, "healthcheck", "acuses.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_encargo_muerto_libera_el_acuse(self):
        self.s.ack("jobs_caidos:rc=1", "investigando (job 864ca438c5 encolado)", por="healthcheck")
        # el job no está ni en pending ni en processing: murió
        self.assertEqual(["jobs_caidos:rc=1"], self.s.reconciliar_acuses())
        rec = self._acuses()["jobs_caidos:rc=1"]
        self.assertEqual("detectado", rec["estado"], "la alerta tiene que volver a llamar")
        self.assertIn("ya no está en la cola", rec["nota"])

    def test_encargo_VIVO_no_se_toca(self):
        self.s.ack("deuda_escalada", "investigando (job abc123def encolado)", por="healthcheck")
        self._job("pending", "abc123def")
        self.assertEqual([], self.s.reconciliar_acuses())
        self.assertEqual("en_arreglo", self._acuses()["deuda_escalada"]["estado"])

    def test_encargo_EN_CURSO_tampoco(self):
        self.s.ack("frescura_hoy", "investigando (job beef1234 encolado)", por="healthcheck")
        self._job("processing", "beef1234")
        self.assertEqual([], self.s.reconciliar_acuses())
        self.assertEqual("en_arreglo", self._acuses()["frescura_hoy"]["estado"])

    def test_el_autofix_no_prometia_encargo_y_se_respeta(self):
        """`autofix: kickstart` no encola nada: no hay encargo que pueda morir."""
        self.s.ack("daemon_fallando:com.btp.backup", "autofix: kickstart", por="autofix")
        self.assertEqual([], self.s.reconciliar_acuses())
        self.assertEqual("en_arreglo", self._acuses()["daemon_fallando:com.btp.backup"]["estado"])

    def test_lo_resuelto_se_queda_resuelto(self):
        self.s.ack("x", "investigando (job deadbeef encolado)", por="healthcheck")
        self.s.resuelto("x", "arreglado a mano")
        self.assertEqual([], self.s.reconciliar_acuses())
        self.assertEqual("resuelto", self._acuses()["x"]["estado"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
