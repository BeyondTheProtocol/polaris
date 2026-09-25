#!/usr/bin/env python3
"""test_gate_red_caida.py — sin red, una cita NUNCA sale como verificada.

POR QUÉ EXISTE (25-sep-26). Punto de rotura 07 del feedback de {{CONTACTO}} + KAI: `citas_fabricadas`
dejaba pasar la respuesta si fallaba la red. Reproducido aquí mismo con la red cortada de verdad
(proxy a un puerto muerto: curl falla al conectar, igual que sin wifi): un PMID inventado salía con
CERO hallazgos y sin fila en el log. La clase entera: los tres checks que consultan red
(`citas_fabricadas`, `preclinico_aplanado`, `cita_no_respalda`).

Contrato que fija este test, de punta a punta por el hook real (`main()` con stdin):
  1. Sin red, la respuesta con cita sale AVISADA («CITA SIN VERIFICAR») por systemMessage.
  2. No se bloquea a ciegas: exit 0 (la cita puede existir; la culpa es de la red).
  3. Queda registrado en gate_salida.jsonl con `bloqueo_real: false`.
  4. Una cita que NO existe sigue bloqueando aunque haya tres avisos de estilo delante (el recorte a
     MAX_HALLAZGOS la tiraba antes de decidir si bloqueaba).
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, ".claude", "hooks", "gate_salida.py")
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gate_salida as g  # noqa: E402

MUERTO = "http://127.0.0.1:9"      # puerto discard: nadie escucha, curl falla en ~0 ms
SIN_RED = {k: MUERTO for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                               "ALL_PROXY", "all_proxy")}
CON_PMID = ("Sobre la vía que preguntabas, el ensayo de fase II (PMID: 99999999) describe una "
            "respuesta objetiva del 41% en carcinoma metaplásico metastásico, así que puede "
            "valer la pena llevárselo a tu oncóloga en la próxima visita y preguntarle por ello.")


def _hook(texto, state_dir):
    env = dict(os.environ, BTP_STATE_DIR=state_dir, **SIN_RED)
    env.pop("BTP_GATE_OFF", None)
    env.pop("BTP_HALT_FILES", None)
    return subprocess.run([sys.executable, HOOK], input=json.dumps({"last_assistant_message": texto}),
                          capture_output=True, text=True, env=env, timeout=120)


class RedCaidaDePuntaAPunta(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_cita_sin_red_sale_marcada_registrada_y_sin_bloquear(self):
        r = _hook(CON_PMID, self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)                 # 2. no bloquea a ciegas
        salida = json.loads(r.stdout)["systemMessage"]
        self.assertIn("CITA SIN VERIFICAR", salida)                 # 1. sale marcada
        self.assertIn("PMID:99999999", salida)
        with open(os.path.join(self.tmp, "gate_salida.jsonl"), encoding="utf-8") as f:
            filas = [json.loads(l) for l in f]
        cita = [f for f in filas if f["check"] == "citas_fabricadas"]
        self.assertEqual(len(cita), 1, filas)                       # 3. queda registrado
        self.assertFalse(cita[0]["bloqueo_real"])
        self.assertTrue(cita[0]["motivo"].startswith(g.PENDIENTE))

    def test_cifra_sin_red_tambien_sale_marcada(self):
        """Misma clase en `cita_no_respalda` y `preclinico_aplanado`: sin red, ninguno calla."""
        old = {k: os.environ.get(k) for k in SIN_RED}
        os.environ.update(SIN_RED)
        try:
            t = CON_PMID
            m = g.cita_no_respalda(t)
            self.assertTrue(m and m.startswith(g.PENDIENTE), m)
            t2 = ("El inhibidor reduce el tumor en pacientes con tu subtipo (PMID: 99999999), así "
                  "que tiene sentido llevarlo a la próxima consulta con tu oncóloga del hospital.")
            m2 = g.preclinico_aplanado(t2)
            self.assertTrue(m2 and m2.startswith(g.PENDIENTE), m2)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class RecorteNoTiraLoQueBloquea(unittest.TestCase):
    def test_bloqueante_va_primero(self):
        estilo = [("convergencia", "s", "a"), ("tells_ia", "s", "b"), ("verborrea", "s", "c")]
        cita = ("citas_fabricadas", "s", "Cita(s) que NO existen en su registro público: PMID:1")
        pend = ("cita_no_respalda", "s", g.PENDIENTE + " x")
        orden = g._prioriza(estilo + [pend, cita], "aviso")[:g.MAX_HALLAZGOS]
        self.assertEqual(orden[0], cita)
        self.assertEqual(orden[1], pend)
        self.assertEqual(g._bloquean(orden, "aviso"), ["citas_fabricadas"])

    def test_main_bloquea_con_tres_avisos_delante(self):
        estilo = [("convergencia", "s", "a"), ("tells_ia", "s", "b"), ("verborrea", "s", "c")]
        cita = ("citas_fabricadas", "s", "Cita(s) que NO existen en su registro público: PMID:1")
        real_rev, real_ap = g.revisar, g._apunta
        g.revisar = lambda t, tools=None: estilo + [cita]
        g._apunta = lambda h, m: None
        old_argv, old_stdin = sys.argv, sys.stdin
        sys.argv, sys.stdin = ["gate"], io.StringIO(json.dumps({"last_assistant_message": "x" * 200}))
        err = io.StringIO()
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                rc = g.main()
        finally:
            g.revisar, g._apunta = real_rev, real_ap
            sys.argv, sys.stdin = old_argv, old_stdin
        self.assertEqual(rc, 2, err.getvalue())
        self.assertIn("citas_fabricadas", err.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
