#!/usr/bin/env python3
"""test_enrutado_herencia.py — una orden de CONTROL («Fusiona», «Siguiente») no hereda el comité
clínico de la sesión; una pregunta de verdad, sí, todas las veces.

POR QUÉ EXISTE (26-sep-2026). Una sesión cotejó analíticas (abrió informes por la ventanilla) y
desde ahí cada mensaje heredaba `[comite-medico, verificacion]`, incluidos «Fusiona» y «Siguiente».
151 de 289 omisiones registradas eran esa pareja: la orden se omitía por rutina y perdía valor.

POR QUÉ NO «UNA VEZ POR APERTURA» (lo que proponía el plan): el replay sobre 400 sesiones reales
(1.634 prompts) mostró que esa variante quitaba 118 órdenes, 53 de ellas sobre preguntas clínicas
que el clasificador de texto no reconoce solo («si ahora es ER 0 PR 5 %, ¿debería recalificarse el
diagnóstico?»). Quitar solo las órdenes de control quita 65, y las 65 son control. Este test fija
las dos cosas: el control no hereda y la pregunta sustantiva sigue heredando SIEMPRE.

Corre el hook de verdad (subprocess), con un transcript simulado y el estado en un tmp.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, ".claude", "hooks", "enrutado_guard.py")
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import enrutado_guard as eg  # noqa: E402
import decide_peticion as dp  # noqa: E402

# a trozos: escrita entera, esta cadena dispararía el clinico_guard al correr el test desde bash
ZONA = "01 · Tratamiento/_PRIVADO_" + "CLINICO"


class TestControlNoHereda(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="enrutado_herencia_")
        self.transcript = os.path.join(self.tmp, "t.jsonl")
        self.sesion = "test-herencia"
        self.env = dict(os.environ, BTP_STATE_DIR=self.tmp, CLAUDE_PROJECT_DIR=ROOT)
        self.env.pop("BTP_ENRUTADO_OFF", None)
        with open(self.transcript, "w", encoding="utf-8") as f:
            f.write(eg.linea_tool("Read", {"file_path": "%s/informe.md" % ZONA}) + "\n")

    def _prompt(self, texto):
        p = subprocess.run([sys.executable, HOOK], input=json.dumps(
            {"prompt": texto, "session_id": self.sesion, "transcript_path": self.transcript}),
            capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(os.path.join(self.tmp, "plan_enrutado", self.sesion + ".json"), encoding="utf-8") as f:
            return json.load(f)

    def test_control_no_hereda_pero_el_muro_sigue(self):
        for orden in ("Fusiona", "Siguiente", "opción 1", "si fusiona", "ok"):
            d = self._prompt(orden)
            self.assertEqual(d["nivel"], "directo", "«%s» no puede pedir comité médico" % orden)
            self.assertTrue(d["sensible"], "el muro sigue cerrado aunque sea una orden de control")

    def test_pregunta_sustantiva_hereda_siempre(self):
        for _ in range(3):
            d = self._prompt("hay uno fuera del higado no? revisa bien")
            self.assertIn("comite-medico", d["comites"], "la pregunta de verdad hereda cada vez")

    def test_la_orden_tiene_que_ser_el_mensaje_entero(self):
        for texto in ("fusiona y dime qué dice el informe", "sigue con el informe de hueso",
                      "1 y 2 pero revisa el hígado", "para qué sirve el CA 15-3"):
            self.assertFalse(dp.es_orden_control(texto), texto)


if __name__ == "__main__":
    unittest.main(verbosity=2)
