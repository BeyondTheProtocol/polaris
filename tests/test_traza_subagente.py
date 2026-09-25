#!/usr/bin/env python3
"""Tests del hook traza_subagente.py (el cuaderno de bitácora de sub-agentes).

Lo que se protege:
  · que apunte al comité que se usó (sin eso, el mapa nunca se enciende)
  · que NO apunte ni el prompt ni la respuesta (es un cuaderno, no un espía)
  · que sea fail-open de verdad: pase lo que pase, rc 0 y sin bloquear
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, ".claude", "hooks", "traza_subagente.py")


def correr(payload, state_dir):
    """Llama al hook como lo llama el harness: JSON por stdin."""
    env = dict(os.environ, BTP_STATE_DIR=state_dir, BTP_REPO=ROOT)
    p = subprocess.run([sys.executable, HOOK], input=payload, env=env,
                       capture_output=True, text=True, timeout=20)
    return p


def trazas(state_dir):
    out = []
    for f in glob.glob(os.path.join(state_dir, "observabilidad", "*.jsonl")):
        with open(f, encoding="utf-8") as fh:
            out += [json.loads(l) for l in fh if l.strip()]
    return out


class TestTrazaSubagente(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="traza-")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _payload(self, **kw):
        base = {"tool_name": "Agent",
                "tool_input": {"subagent_type": "verificacion"},
                "tool_response": {"content": "ok"}}
        base.update(kw)
        return json.dumps(base)

    def test_apunta_el_comite(self):
        correr(self._payload(), self.d)
        t = trazas(self.d)
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["agente"], "verificacion")
        self.assertEqual(t[0]["job"], "sesion")
        self.assertEqual(t[0]["resultado"], "ok")

    def test_entiende_Task_ademas_de_Agent(self):
        """El harness manda 'Agent'; comprobar solo 'Task' dejó una regla del muro
        muerta una semana entera. Aquí se aceptan los dos."""
        correr(self._payload(tool_name="Task"), self.d)
        self.assertEqual(len(trazas(self.d)), 1)

    def test_marca_el_fallo(self):
        correr(self._payload(tool_response={"is_error": True}), self.d)
        self.assertEqual(trazas(self.d)[0]["resultado"], "fail")

    def test_ignora_los_agentes_de_fabrica(self):
        """Explore, Plan o general-purpose no son comités del gabinete: si se
        cuelan, el mapa dice que trabajó gente que no existe."""
        for n in ("general-purpose", "Explore", "Plan", "statusline-setup"):
            correr(self._payload(tool_input={"subagent_type": n}), self.d)
        self.assertEqual(trazas(self.d), [])

    def test_ignora_otras_herramientas(self):
        correr(self._payload(tool_name="Read",
                             tool_input={"file_path": "/tmp/x"}), self.d)
        self.assertEqual(trazas(self.d), [])

    def test_no_guarda_ni_el_prompt_ni_la_respuesta(self):
        """La traza dice QUIÉN trabajó, nunca QUÉ dijo: si se colara el prompt,
        el registro pasaría a tener datos del caso."""
        secreto = "PALABRA-QUE-NO-DEBE-APARECER-JAMAS"
        correr(self._payload(
            tool_input={"subagent_type": "verificacion", "prompt": secreto},
            tool_response={"content": secreto}), self.d)
        crudo = json.dumps(trazas(self.d))
        self.assertNotIn(secreto, crudo)

    # ── ciclo de vida (14-sep-2026, tools/ciclo_agentes.py) ──────────────────────────────
    def _registro_ciclo(self):
        out = []
        for f in glob.glob(os.path.join(self.d, "agentes", "ciclo-*.jsonl")):
            with open(f, encoding="utf-8") as fh:
                out += [json.loads(l) for l in fh if l.strip()]
        return out

    def test_apunta_el_lanzamiento_con_agent_id(self):
        correr(self._payload(session_id="sesion-abc", tool_use_id="toolu_X",
                             tool_response={"agentId": "a123", "status": "async_launched",
                                            "isAsync": True, "resolvedModel": "claude-opus-5"}), self.d)
        filas = self._registro_ciclo()
        self.assertEqual(1, len(filas))
        self.assertEqual(("a123", "toolu_X", "lanzado"),
                         (filas[0]["agent_id"], filas[0]["tool_use_id"], filas[0]["evento"]))
        self.assertEqual(1, len(trazas(self.d)), "la línea de observabilidad se sigue escribiendo")

    def test_el_registro_de_ciclo_tampoco_guarda_contenido(self):
        secreto = "OTRA-PALABRA-QUE-NO-DEBE-APARECER"
        correr(self._payload(tool_input={"subagent_type": "verificacion", "prompt": secreto,
                                         "description": secreto},
                             tool_response={"agentId": "a9", "content": secreto,
                                            "description": secreto, "prompt": secreto}), self.d)
        self.assertNotIn(secreto, json.dumps(self._registro_ciclo()))

    def test_agente_de_fabrica_entra_en_el_ciclo_pero_no_en_el_mapa(self):
        correr(self._payload(tool_input={"subagent_type": "Explore"},
                             tool_response={"agentId": "e77"}), self.d)
        self.assertEqual([], trazas(self.d), "Explore no es comité: no enciende el mapa")
        self.assertEqual(["e77"], [f["agent_id"] for f in self._registro_ciclo()])

    def test_no_se_deja_enganar_con_una_ruta(self):
        for malo in ("../../etc/passwd", "/etc/passwd", ".ssh/config", ""):
            correr(self._payload(tool_input={"subagent_type": malo}), self.d)
        self.assertEqual(trazas(self.d), [])

    def test_fail_open_pase_lo_que_pase(self):
        """Corre en CADA sub-agente: un error suyo no puede tumbar una sesión."""
        for basura in ("no soy json", "", "{}", "[]", '{"tool_name":null}',
                       '{"tool_name":"Agent"}',
                       '{"tool_name":"Agent","tool_input":"no es un dict"}'):
            p = correr(basura, self.d)
            self.assertEqual(p.returncode, 0, "rc != 0 con %r" % basura)
            self.assertEqual(p.stdout.strip(), "",
                             "el hook ensucia stdout con %r" % basura)

    def test_esta_enganchado_en_settings(self):
        """Un hook que existe pero nadie invoca es peor que no tenerlo: parece
        que estás midiendo y no estás midiendo nada."""
        with open(os.path.join(ROOT, ".claude", "settings.json"),
                  encoding="utf-8") as f:
            cfg = json.load(f)
        post = cfg.get("hooks", {}).get("PostToolUse", [])
        cmds = [h.get("command", "") for b in post for h in b.get("hooks", [])]
        self.assertTrue(any("traza_subagente.py" in c for c in cmds),
                        "traza_subagente.py no está enganchado en PostToolUse")
        matchers = [b.get("matcher", "") for b in post]
        self.assertTrue(any("Agent" in m for m in matchers))


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if res.wasSuccessful():
        print("✅ TRAZA DE SUB-AGENTES EN VERDE (%d tests)" % res.testsRun)
        sys.exit(0)
    print("❌ TRAZA DE SUB-AGENTES EN ROJO: %d fallos, %d errores"
          % (len(res.failures), len(res.errors)))
    sys.exit(1)
