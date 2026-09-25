#!/usr/bin/env python3
"""test_cola_diario.py — el reintento tiene que SABER qué falló ya.

Qué se protege (25-jul-2026, idea de `x:2069713940989231590`): un loop sin memoria entre vueltas
reintenta el mismo arreglo que ya falló y quema los intentos hasta el dead-letter. `mark_failed` ya
guardaba `ultimo_error`, pero nadie se lo contaba al reintento: el prompt se montaba con contexto +
intención y nada más. Ahora el job lleva un `historial` de intentos y el dispatcher lo mete en el
prompt vía `cola.py diario`.

Se comprueba además que la allowlist sigue CERRADA (el campo nuevo no abre la puerta a otros) y que
el diario está vacío en el primer intento (no se le mete ruido a un job que aún no ha fallado).
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)


class Diario(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["BTP_STATE_DIR"] = self.tmp
        for m in ("cola",):
            sys.modules.pop(m, None)
        import cola
        self.cola = cola
        self.cola._ensure_dirs()

    def tearDown(self):
        os.environ.pop("BTP_STATE_DIR", None)
        sys.modules.pop("cola", None)

    def _job_fallado(self, veces, error="boom: no encuentro el fichero"):
        jid = self.cola.enqueue("arregla lo que sea", procedencia="test")
        for _ in range(veces):
            job = self.cola.dequeue()
            self.assertIsNotNone(job, "el job tiene que volver a pending tras fallar")
            self.cola.mark_failed(job, error)
        return jid

    def test_el_primer_intento_no_lleva_diario(self):
        self.cola.enqueue("haz algo", procedencia="test")
        job = self.cola.dequeue()
        self.assertEqual(self.cola.diario(job), "")

    def test_tras_fallar_el_job_guarda_que_paso_y_el_diario_lo_cuenta(self):
        self._job_fallado(2, "boom: falta la clave de la API")
        job = self.cola.dequeue()
        self.assertEqual(len(job["historial"]), 2)
        self.assertEqual(job["historial"][0]["intento"], 1)
        txt = self.cola.diario(job)
        self.assertIn("YA FALLÓ 2", txt)
        self.assertIn("falta la clave de la API", txt)
        self.assertIn("NO repitas el mismo camino", txt)

    def test_el_diario_no_crece_sin_fin(self):
        self.cola.enqueue("haz algo", procedencia="test",
                          max_intentos=self.cola.MAX_INTENTOS_TOPE)
        for i in range(self.cola.HISTORIAL_TOPE + 3):
            job = self.cola.dequeue()
            self.cola.mark_failed(job, "fallo %d" % i)
        job = self.cola.dequeue()
        self.assertEqual(len(job["historial"]), self.cola.HISTORIAL_TOPE)
        self.assertIn("fallo %d" % (self.cola.HISTORIAL_TOPE + 2), job["historial"][-1]["error"])

    def test_el_job_con_historial_sigue_pasando_el_schema(self):
        """El campo nuevo está en la allowlist: un job reencolado no cae a failed/ por schema."""
        self._job_fallado(1)
        st = self.cola.get_status()
        self.assertEqual(st.get("failed", 0), 0, "un job con historial NO puede caer a failed/")
        self.assertEqual(st.get("pending", 0), 1)

    def test_la_allowlist_sigue_cerrada(self):
        base = {"id": "x", "prioridad": "normal", "intencion": "a", "perfil": "privileged",
                "intentos": 0, "max_intentos": 3, "creado": "2026-07-25",
                "procedencia": "test", "tipo": "exec"}
        self.assertIsNone(self.cola._validate(dict(base, historial=[{"intento": 1}])),
                          "el campo nuevo tiene que ser válido")
        motivo = self.cola._validate(dict(base, campo_inventado=1))
        self.assertIsNotNone(motivo, "un campo desconocido sigue rechazándose (fail-closed)")
        self.assertIn("campo_inventado", motivo)
        self.assertIsNotNone(self.cola._validate(dict(base, historial="no soy una lista")),
                             "historial tiene que ser una lista, no cualquier cosa")

    def test_el_cli_diario_lee_el_job_por_stdin(self):
        job = {"historial": [{"intento": 1, "error": "explotó", "ts": "2026-07-25T10:00:00"}]}
        stdin, sys.stdin = sys.stdin, io.StringIO(json.dumps(job))
        try:
            with redirect_stdout(io.StringIO()) as buf:
                rc = self.cola.main(["diario"])
        finally:
            sys.stdin = stdin
        self.assertEqual(rc, 0)
        self.assertIn("explotó", buf.getvalue())


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=0).run(
        unittest.TestLoader().loadTestsFromTestCase(Diario))
    if res.wasSuccessful():
        print("✅ COLA DIARIO EN VERDE (%d ok / 0 fallos)" % res.testsRun)
    sys.exit(0 if res.wasSuccessful() else 1)
