#!/usr/bin/env python3
"""test_contrato_asiento.py — el contrato de la caja llega a cada asiento de análisis, y nada más cambia.

13-sep-2026. Sin este hook, `cosecha_panel.py` no tenía nada que juntar: los subagentes lanzados desde
el chat no recibían el contrato JSON (medido: 0 decisiones en el último panel real). Lo que protege:
  1. Que SOLO cambie el prompt, y solo de los asientos de análisis. `updatedInput` reemplaza la
     entrada entera: perder un campo sería cambiar la llamada sin querer.
  2. Que NO toque permisos: nada de `permissionDecision` (con `allow` se saltaría el prompt de permiso).
  3. Que el texto que añade sirva de verdad: un asiento que lo cumpla sale NO degradado en la caja.
  4. Fail-open, sin repetir, y que ningún otro hook reescriba la entrada (la doc: el último gana).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("estado")
import glob
import json
import os
import re
import subprocess
import sys
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(RAIZ, ".claude", "hooks", "contrato_asiento.py")
sys.path.insert(0, os.path.join(RAIZ, "tools"))
import caja  # noqa: E402

ENTRADA = {"description": "Triaje del paper", "subagent_type": "comite-medico",
           "prompt": "Analiza este paper y dime si aplica.", "run_in_background": True, "model": "opus"}


def correr(datos=None, crudo=None, **env_extra):
    env = dict(os.environ, **env_extra)
    if "BTP_CONTRATO_OFF" not in env_extra:
        env.pop("BTP_CONTRATO_OFF", None)
    entrada = crudo if crudo is not None else json.dumps(datos)
    p = subprocess.run([sys.executable, HOOK], input=entrada, capture_output=True, text=True,
                       env=env, timeout=30)
    return p.returncode, p.stdout.strip()


class TestContratoAsiento(unittest.TestCase):
    # ── 1. solo cambia el prompt de los asientos de análisis ────────────────────────────
    def test_asiento_de_analisis_recibe_el_contrato_y_nada_mas_cambia(self):
        rc, out = correr({"tool_name": "Agent", "tool_input": ENTRADA})
        self.assertEqual(0, rc)
        spec = json.loads(out)["hookSpecificOutput"]
        self.assertEqual("PreToolUse", spec["hookEventName"])
        nuevo = spec["updatedInput"]
        self.assertEqual(set(ENTRADA), set(nuevo), "no se pierde ni se inventa ningún campo")
        for k in ENTRADA:
            if k != "prompt":
                self.assertEqual(ENTRADA[k], nuevo[k], "el campo %s no se toca" % k)
        self.assertTrue(nuevo["prompt"].startswith(ENTRADA["prompt"]), "el pedido original va primero")
        self.assertIn(caja.MARCA_CONTRATO, nuevo["prompt"])

    def test_no_toca_permisos(self):
        _rc, out = correr({"tool_name": "Agent", "tool_input": ENTRADA})
        self.assertNotIn("permissionDecision", out)

    def test_redaccion_y_operativos_no_reciben_contrato(self):
        for slug in ("voz-titular", "redes-contenido", "prensa", "git", "tecnico", "Explore",
                     "general-purpose"):
            datos = {"tool_name": "Agent", "tool_input": dict(ENTRADA, subagent_type=slug)}
            self.assertEqual((0, ""), correr(datos), slug)

    def test_otro_tool_no_se_toca(self):
        self.assertEqual((0, ""), correr({"tool_name": "Bash", "tool_input": {"command": "ls"}}))

    def test_task_heredado_tambien(self):
        _rc, out = correr({"tool_name": "Task", "tool_input": ENTRADA})
        self.assertIn(caja.MARCA_CONTRATO, out)

    # ── 2. no repite, fail-open, bypass ────────────────────────────────────────────────────
    def test_no_lo_pone_dos_veces(self):
        con = dict(ENTRADA, prompt=ENTRADA["prompt"] + caja.CONTRATO_ASIENTO)
        self.assertEqual((0, ""), correr({"tool_name": "Agent", "tool_input": con}))

    def test_entrada_rota_sale_callado(self):
        self.assertEqual((0, ""), correr(crudo="no es json"))
        self.assertEqual((0, ""), correr({"tool_name": "Agent", "tool_input": "no es un dict"}))
        self.assertEqual((0, ""), correr({"tool_name": "Agent",
                                          "tool_input": dict(ENTRADA, prompt="   ")}))

    def test_bypass(self):
        self.assertEqual((0, ""), correr({"tool_name": "Agent", "tool_input": ENTRADA},
                                         BTP_CONTRATO_OFF="1"))

    # ── 3. el texto sirve: quien lo cumple, entra en la caja ───────────────────────────────
    def test_la_plantilla_es_json_valido(self):
        d = json.loads(caja.PLANTILLA_CONTRATO)
        self.assertIn("decisiones", d)
        self.assertIn("acciones", d)

    def test_un_asiento_que_cumple_entra_no_degradado(self):
        respuesta = ("Mi análisis.\n\n```json\n%s\n```" % caja.PLANTILLA_CONTRATO)
        self.assertFalse(caja.parsear(respuesta, "comite-medico")["degradado"])
        self.assertIn("```json", caja.CONTRATO_ASIENTO, "el texto pide el bloque que parsear busca")

    def test_los_asientos_existen_como_comites(self):
        for slug in caja.ASIENTOS_CON_CONTRATO:
            self.assertTrue(os.path.isfile(os.path.join(RAIZ, ".claude", "agents", slug + ".md")), slug)

    # ── 3b. ventanilla clínica desde un worktree (13-sep-2026) ───────────────────────────
    # El caso real: un `verificacion` lanzado desde un worktree hacía `cd` a casa base, el arnés le
    # rechazó los 26 comandos y entregó un cotejo solo con resúmenes. Con ruta absoluta y sin `cd`
    # funciona (reproducido en primer y segundo plano).
    WT = "/Users/polaris/claudecode/.claude/worktrees/una-rama"

    def _prompt(self, datos):
        rc, out = correr(datos)
        self.assertEqual(0, rc)
        return json.loads(out)["hookSpecificOutput"]["updatedInput"]["prompt"] if out else None

    def test_lector_clinico_desde_worktree_recibe_el_aviso_con_ruta_absoluta(self):
        entrada = dict(ENTRADA, subagent_type="verificacion")
        p = self._prompt({"tool_name": "Agent", "tool_input": entrada, "cwd": self.WT})
        self.assertIn(caja.MARCA_CONTRATO, p, "verificacion es asiento: lleva también el contrato")
        self.assertIn("VENTANILLA CLÍNICA DESDE UN WORKTREE", p)
        self.assertIn("tools/lector_clinico.py", p)
        self.assertRegex(p, r"python3 /\S+/tools/lector_clinico\.py", "ruta ABSOLUTA, no relativa")
        self.assertIn("BLOQUEADO", p, "si no abre el original, lo dice en vez de degradar")

    def test_desde_casa_base_no_hay_aviso_de_worktree(self):
        entrada = dict(ENTRADA, subagent_type="verificacion")
        p = self._prompt({"tool_name": "Agent", "tool_input": entrada,
                          "cwd": "/Users/polaris/claudecode"})
        self.assertNotIn("VENTANILLA CLÍNICA DESDE UN WORKTREE", p)

    def test_lector_clinico_que_no_es_asiento_solo_recibe_el_aviso(self):
        entrada = dict(ENTRADA, subagent_type="herramientas-medicas")
        p = self._prompt({"tool_name": "Agent", "tool_input": entrada, "cwd": self.WT})
        self.assertIn("VENTANILLA CLÍNICA DESDE UN WORKTREE", p)
        self.assertNotIn(caja.MARCA_CONTRATO, p)

    def test_agente_no_clinico_desde_worktree_no_se_toca(self):
        entrada = dict(ENTRADA, subagent_type="git")
        self.assertEqual((0, ""), correr({"tool_name": "Agent", "tool_input": entrada, "cwd": self.WT}))

    def test_el_aviso_no_se_repite(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("contrato_asiento", HOOK)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ya = dict(ENTRADA, subagent_type="verificacion",
                  prompt=ENTRADA["prompt"] + caja.CONTRATO_ASIENTO + mod.aviso_ventanilla())
        self.assertEqual((0, ""), correr({"tool_name": "Agent", "tool_input": ya, "cwd": self.WT}))

    # ── 4. enganchado, y nadie más reescribe la entrada ────────────────────────────────────
    def test_enganchado_con_matcher_propio(self):
        with open(os.path.join(RAIZ, ".claude", "settings.json"), encoding="utf-8") as f:
            pre = json.load(f)["hooks"]["PreToolUse"]
        bloques = [b for b in pre if any("contrato_asiento.py" in h.get("command", "")
                                         for h in b.get("hooks", []))]
        self.assertEqual(1, len(bloques))
        self.assertEqual("Agent|Task", bloques[0].get("matcher"), "no va en el matcher * del muro")
        self.assertTrue(os.access(HOOK, os.X_OK), "el hook tiene que ser ejecutable")

    def test_ningun_otro_hook_devuelve_updatedInput(self):
        otros = [p for p in glob.glob(os.path.join(RAIZ, ".claude", "hooks", "*"))
                 if os.path.isfile(p) and not p.endswith("contrato_asiento.py")]
        culpables = []
        for p in otros:
            with open(p, encoding="utf-8", errors="replace") as f:
                if re.search(r"updatedInput", f.read()):
                    culpables.append(os.path.basename(p))
        self.assertEqual([], culpables, "si dos hooks reescriben la entrada, gana el último en terminar")


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=0).result
    if res.wasSuccessful():
        print("✅ CONTRATO DE ASIENTO EN VERDE (%d casos)" % res.testsRun)
    sys.exit(0 if res.wasSuccessful() else 1)
