#!/usr/bin/env python3
"""test_nivel_salida.py — P3 · F1: el código pone nivel a cada salida, y en sombra NO manda.

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.

Lo que se fija:
  1. los niveles que pidió el comité (merge/push a main L3, correo L2, tarea programada L2 mínimo,
     clic en host de pago L3, clic en localhost/preview propia L0, lo ilegible L2, fallo L3);
  2. el hook anota el nivel en `nivel_salida.jsonl` sin cambiar su decisión;
  3. si la sombra revienta, el hook decide EXACTAMENTE igual (misma salida, mismo rc);
  4. no se guarda contenido: solo un hash corto.
Todo con `BTP_STATE_DIR` temporal: nada toca el estado vivo.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import nivel_salida as N  # noqa: E402

HOOK = os.path.join(ROOT, ".claude", "hooks", "salida_guard.py")


class Niveles(unittest.TestCase):
    def test_publicar_es_L3(self):
        for m in ("git push que publica (casa base, o main/master) en /x", "gh pr merge en /x",
                  "despliegue (netlify)", "gh issue create"):
            self.assertEqual(N.nivel("Bash", {}, "envia", m)[0], N.L3, m)

    def test_correo_y_dm_son_L2(self):
        for t in ("mcp__b47695e8__send_message", "mcp__b47695e8__reply", "mcp__x__send_chat_message"):
            self.assertEqual(N.nivel(t, {}, "envia")[0], N.L2, t)

    def test_tarea_programada_es_L2_minimo(self):
        self.assertEqual(N.nivel("mcp__scheduled-tasks__create_scheduled_task", {}, "envia")[0], N.L2)

    def test_tercero_propio_no_es_L0(self):
        self.assertEqual(N.nivel("mcp__6e48e780__update_file", {}, "clic")[0], N.L2)

    def test_clic_por_host(self):
        self.assertEqual(N.nivel("mcp__Claude_Browser__computer", {}, "clic", host="localhost")[0], N.L0)
        self.assertEqual(N.nivel("x__computer", {}, "clic",
                                 host="deploy-preview-223--helptitular-web.netlify.app")[0], N.L0)
        for h in ("checkout.iherb.com", "www.amazon.es", "venta.renfe.com", "riders.uber.com"):
            self.assertEqual(N.nivel("x__computer", {}, "clic", host=h)[0], N.L3, h)
        self.assertEqual(N.nivel("x__computer", {}, "clic", host="github.com")[0], N.L2)
        self.assertEqual(N.nivel("x__computer", {}, "clic", host="")[0], N.L2)

    def test_host_falso_no_cuela_como_propio(self):
        for h in ("localhost.evil.com", "helptitular-web.netlify.app.evil.com", "evil-helptitular-web.netlify.app"):
            self.assertNotEqual(N.nivel("x__computer", {}, "clic", host=h)[0], N.L0, h)

    def test_fallo_sube_a_L3(self):
        class Raro:
            def lower(self):
                raise ValueError
        self.assertEqual(N.nivel(Raro(), {}, "envia")[0], N.L3)

    def test_host_navegado_en_batch(self):
        ent = {"actions": [{"name": "navigate", "input": {"url": "https://checkout.iherb.com/pago"}},
                           {"name": "computer", "input": {"action": "left_click"}}]}
        self.assertEqual(N.host_navegado("mcp__claude-in-chrome__browser_batch", ent), "checkout.iherb.com")

    def test_hash_no_guarda_contenido(self):
        h = N.hash_contenido({"body": "secreto clínico"})
        self.assertEqual(len(h), 16)
        self.assertNotIn("secreto", h)


class HookEnSombra(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nivelsalida-")
        self.env = dict(os.environ, BTP_STATE_DIR=self.tmp, BTP_OK_ENVIO_CLAVE="c" * 64)
        self.env.pop("BTP_NIVEL_SALIDA_FALLA", None)

    def _hook(self, payload, env=None):
        payload = dict(payload, session_id="s-test", cwd="/tmp", permission_mode="bypassPermissions")
        return subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                              text=True, timeout=60, env=env or self.env)

    def _registros(self):
        p = os.path.join(self.tmp, "nivel_salida.jsonl")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(l) for l in f]

    def test_merge_se_deniega_igual_y_se_anota_L3(self):
        r = self._hook({"tool_name": "Bash", "tool_input": {"command": "gh pr merge 5 --repo a/b"}})
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        reg = self._registros()
        self.assertEqual([(x["nivel"], x["decision"]) for x in reg], [(3, "denegado")])
        self.assertNotIn("gh pr merge 5", json.dumps(reg))      # ni el comando en claro

    def test_clic_tras_navegar_a_pago_es_L3_y_solo_avisa(self):
        self._hook({"tool_name": "mcp__claude-in-chrome__navigate",
                    "tool_input": {"url": "https://checkout.iherb.com/x"}})
        r = self._hook({"tool_name": "mcp__claude-in-chrome__computer",
                        "tool_input": {"action": "left_click", "coordinate": [1, 1]}})
        self.assertNotIn("permissionDecision", json.loads(r.stdout)["hookSpecificOutput"])
        reg = self._registros()
        self.assertEqual((reg[-1]["nivel"], reg[-1]["host"], reg[-1]["decision"]),
                         (3, "checkout.iherb.com", "avisado"))

    def test_lectura_no_se_anota(self):
        self._hook({"tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}})
        self.assertEqual(self._registros(), [])

    def test_si_la_sombra_revienta_el_hook_decide_igual(self):
        casos = [{"tool_name": "Bash", "tool_input": {"command": "gh pr merge 5 --repo a/b"}},
                 {"tool_name": "mcp__claude-in-chrome__computer", "tool_input": {"action": "left_click"}},
                 {"tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}}]
        roto = dict(self.env, BTP_NIVEL_SALIDA_FALLA="1")
        for c in casos:
            a, b = self._hook(c), self._hook(c, roto)
            self.assertEqual((a.returncode, a.stdout), (b.returncode, b.stdout), c["tool_name"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
