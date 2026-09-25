#!/usr/bin/env python3
"""Test: lo decidido y el paso en el que íbamos sobreviven a un /compact.

POR QUÉ EXISTE (24-sep-2026). `settings.json` no tenía hook PreCompact: al compactar se perdía lo
que inyectó SessionStart, las decisiones ya cerradas y el paso en curso, y se volvían a preguntar
cosas contestadas. Contrato verificado ese día en code.claude.com/docs/en/hooks.md: PreCompact
recibe `transcript_path` y no puede inyectar contexto; SessionStart vuelve a disparar con
`source: "compact"` y sí puede. Así que PreCompact ESCRIBE y SessionStart RELEE.

Qué se exige:
  · PreCompact deja en tools/state/traspasos/<sid>.md el paso, los planes aprobados, las respuestas
    a AskUserQuestion, los ficheros tocados y los prompts humanos (no los inyectados), sin stdout;
  · nunca bloquea: con transcript roto, inexistente o sin session_id sale 0;
  · no toca el INDEX de continuidad (lo protege el aviso de cierre y la dieta de la brújula);
  · session_start.sh lo inyecta con source=compact y NO con source=startup ni para otra sesión.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # este árbol: probamos su código
HOOK = os.path.join(ROOT, ".claude", "hooks", "precompact_traspaso.py")
SS = os.path.join(ROOT, ".claude", "hooks", "session_start.sh")
SID = "sesion-prueba-1234"


def _l(**d):
    return json.dumps(d)


def _transcript(ruta):
    lineas = [
        _l(type="user", origin={"kind": "human"},
           message={"role": "user", "content": "<system-reminder>ruido inyectado</system-reminder>\nMonta el hook PreCompact"}),
        _l(type="user", message={"role": "user", "content": "aviso de subagente, no es humano"}),
        _l(type="assistant", message={"content": [
            {"type": "text", "text": "Paso 1 de 5: leo el código."},
            {"type": "tool_use", "id": "t1", "name": "ExitPlanMode", "input": {}}]}),
        _l(type="user", message={"content": [{"type": "tool_result", "tool_use_id": "t1", "content":
            "User has approved your plan. You can now start coding.\n\nYour plan has been saved to: "
            "/tmp/planes/mi-plan.md\n\n## Approved Plan:\n# Traspaso antes de compactar\n\n## TL;DR"}]}),
        _l(type="assistant", message={"content": [
            {"type": "tool_use", "id": "t2", "name": "AskUserQuestion", "input": {}}]}),
        _l(type="user", message={"content": [{"type": "tool_result", "tool_use_id": "t2", "content":
            [{"type": "text", "text": "User has answered your questions: \"¿WES o WGS?\"=\"WES\""}]}]}),
        "esto no es json {",
        _l(type="assistant", message={"content": [
            {"type": "text", "text": "Paso 3 de 5: escribo el test."},
            {"type": "tool_use", "id": "t3", "name": "Edit", "input": {"file_path": "/x/tools/continuity.py"}},
            {"type": "tool_use", "id": "t4", "name": "Write", "input": {"file_path": "/x/tests/t.py"}}]}),
    ]
    with open(ruta, "w") as f:
        f.write("\n".join(lineas) + "\n")


class Traspaso(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = os.path.join(self.tmp, "state")
        # Casa base de mentira: continuity real + brújula falsa. Así session_start.sh no dispara
        # sync_al_abrir ni el drenaje de reels (no existen aquí).
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "tools"))
        for n in ("continuity.py", "_casa.py"):
            shutil.copy(os.path.join(ROOT, "tools", n), os.path.join(self.repo, "tools", n))
        with open(os.path.join(self.repo, "tools", "contexto_lazo.py"), "w") as f:
            f.write("print('BRUJULA-FALSA')\n")
        self.env = dict(os.environ, BTP_STATE_DIR=self.state, BTP_REPO=self.repo, TMPDIR=self.tmp)
        self.env.pop("BTP_TRASPASO_OFF", None)
        self.tp = os.path.join(self.tmp, "t.jsonl")
        _transcript(self.tp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _precompact(self, entrada):
        return subprocess.run([sys.executable, HOOK], input=json.dumps(entrada), env=self.env,
                              capture_output=True, text=True, timeout=30)

    def _fichero(self):
        p = os.path.join(self.state, "traspasos", SID + ".md")
        if not os.path.exists(p):
            return ""
        with open(p) as f:
            return f.read()

    def test_precompact_escribe_lo_que_importa(self):
        p = self._precompact({"session_id": SID, "transcript_path": self.tp, "cwd": ROOT,
                              "hook_event_name": "PreCompact", "trigger": "auto",
                              "custom_instructions": None})
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "")
        t = self._fichero()
        self.assertIn("[confiable]", t)
        self.assertIn("Paso: 3 de 5", t)
        self.assertIn("Traspaso antes de compactar (/tmp/planes/mi-plan.md)", t)
        self.assertIn('=\"WES\"', t)
        self.assertIn("/x/tools/continuity.py", t)
        self.assertIn("Monta el hook PreCompact", t)
        self.assertNotIn("ruido inyectado", t)
        self.assertNotIn("aviso de subagente", t)
        self.assertIn("Prompt de continuación", t)
        self.assertFalse(os.path.exists(os.path.join(self.state, "continuity", "INDEX.md")))
        self.assertEqual(oct(os.stat(os.path.join(self.state, "traspasos", SID + ".md")).st_mode & 0o777), "0o600")

    def test_nunca_bloquea(self):
        for entrada in ({"session_id": SID, "transcript_path": "/no/existe.jsonl", "cwd": self.tmp},
                        {"transcript_path": self.tp},
                        "no-json"):
            p = subprocess.run([sys.executable, HOOK], env=self.env, capture_output=True, text=True,
                               input=entrada if isinstance(entrada, str) else json.dumps(entrada))
            self.assertEqual(p.returncode, 0, entrada)
            self.assertEqual(p.stdout, "")
        # sin transcript legible sigue dejando la estructura (rama, paso «no consta»)
        self.assertIn("Paso: no consta", self._fichero())

    def _session_start(self, source, sid=SID):
        p = subprocess.run(["bash", SS], input=json.dumps({"session_id": sid, "source": source,
                           "hook_event_name": "SessionStart"}), env=self.env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0)
        return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_session_start_reinyecta_solo_tras_compact(self):
        self._precompact({"session_id": SID, "transcript_path": self.tp, "cwd": ROOT, "trigger": "manual"})
        ctx = self._session_start("compact")
        self.assertIn("TRASPASO ANTES DE COMPACTAR", ctx)
        self.assertIn("Paso: 3 de 5", ctx)
        self.assertIn("BRUJULA-FALSA", ctx)
        self.assertLess(ctx.index("TRASPASO"), ctx.index("BRUJULA-FALSA"))
        self.assertNotIn("TRASPASO ANTES", self._session_start("startup"))
        self.assertNotIn("TRASPASO ANTES", self._session_start("compact", sid="otra-sesion"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
