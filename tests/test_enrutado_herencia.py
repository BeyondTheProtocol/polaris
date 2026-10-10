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


# ── 10-oct-2026: el enrutador convocaba comités en sesiones de puro tooling ─────────────────────
# Tres causas medidas sobre una sesión real que solo tocó configuración, hooks y tests:
#  1. el aviso <system-reminder> del worktree lleva el nombre de la rama («…dms-linkedin») y
#     convocaba redes-contenido + voz-titular;
#  2. el informe de un subagente («Another Claude session sent a message» / «[Subagent hand-back]»)
#     se enrutaba como si lo hubiera escrito {{TITULAR}};
#  3. `_contexto_clinico` trataba el comando Bash ENTERO como una ruta: un heredoc que nombraba una
#     carpeta clínica (al añadir reglas `deny` a settings.json) marcaba la sesión, y un Read
#     DENEGADO por el guard contaba como haber abierto material.
# Lo que NO se relaja: la ventanilla ejecutada, un Read permitido en zona clínica y cualquier
# petición con contenido clínico real siguen convocando comite-medico + verificacion.
REMINDER = ("<system-reminder>\nYou are operating in a worktree.\n"
            "Worktree path: /x/.claude/worktrees/render-lote-dms-linkedin-88ee69\n"
            "Worktree name: render-lote-dms-linkedin-88ee69\n</system-reminder>\n\n")
# a trozos: escritas enteras, estas cadenas dispararían el clinico_guard al correr el test
EXPEDIENTE = "_PRIVADO_" + "EXPEDIENTE"
SALUD = "00_" + "Salud"
DENEGADO = ("PreToolUse:Read hook error: [clinico_guard.py]: MURO ⛔ lectura de datos clínicos "
            "fuera de la ventanilla auditada.")


def uso(id_, nombre, entrada):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": id_, "name": nombre, "input": entrada}]}})


def resultado(id_, texto, error=False):
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": id_, "content": texto, "is_error": error}]}})


class _ConHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="enrutado_tooling_")
        self.transcript = os.path.join(self.tmp, "t.jsonl")
        self.sesion = "test-tooling"
        self.env = dict(os.environ, BTP_STATE_DIR=self.tmp, CLAUDE_PROJECT_DIR=ROOT)
        self.env.pop("BTP_ENRUTADO_OFF", None)
        self.escribe([])

    def escribe(self, lineas):
        with open(self.transcript, "w", encoding="utf-8") as f:
            f.write("\n".join(lineas) + ("\n" if lineas else ""))

    def plan(self, texto):
        ruta = os.path.join(self.tmp, "plan_enrutado", self.sesion + ".json")
        if os.path.exists(ruta):
            os.remove(ruta)
        p = subprocess.run([sys.executable, HOOK], input=json.dumps(
            {"prompt": texto, "session_id": self.sesion, "transcript_path": self.transcript}),
            capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        if not os.path.exists(ruta):
            return None
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)


class TestAvisosYSubagentes(_ConHook):

    def test_el_aviso_del_worktree_no_convoca_comites(self):
        d = self.plan(REMINDER + "quiero que optimices a tope claude para que funcione polaris "
                      "perfecto desde las carpetas de claudecode, la configuracion de la app y "
                      "sacarle el maximo rendimiento para que nos ayude a llegar a NED")
        self.assertIsNotNone(d)
        self.assertEqual(d["nivel"], "directo", d)
        self.assertEqual(d["comites"], [], d)

    def test_informe_de_subagente_no_se_enruta(self):
        informe = ("Another Claude session sent a message:\n<agent-message from=\"a1\">\n"
                   "[Subagent hand-back] The text below is the final report of a subagent.\n"
                   "  Revisé el diseño del hook y el comité médico no interviene; verificacion ok.")
        self.assertIsNone(self.plan(informe), "un informe de subagente no escribe plan")
        for pref in ("[Subagent hand-back] x", "<agent-message from=\"a\">x",
                     "<cross-session-message from=\"a\">x"):
            self.assertTrue(dp.es_del_sistema(pref), pref)
        # un aviso delante de un mensaje de {{TITULAR}} NO lo convierte en mensaje del sistema
        self.assertFalse(dp.es_del_sistema(REMINDER + "hay uno fuera del higado no?"))

    def test_el_aviso_no_abre_el_muro(self):
        con_pii = dp.decidir("<system-reminder>\nTitular {{APELLIDO}} abrió x\n</system-reminder>\nhola")
        self.assertTrue(con_pii["sensible"], "limpiar el aviso no puede abrir un LLM de fuera")


class TestSesionDeToolingNoEsClinica(_ConHook):

    def _tooling(self):
        # La forma EXACTA de la orden real del 10-oct-26 (añadir reglas deny a settings.json): el
        # guard la dejó pasar, y la marca vieja (comando entero tratado como ruta) la daba por clínica.
        heredoc = ("cp ~/.claude/settings.json ~/.claude/backups/x.json && python3 - <<'EOF'\n"
                   "nuevas=[\"Read(~/DICOM_Seattle/**)\",\"Read(//**/%s/**)\",\"Read(//**/%s/**)\"]\n"
                   "EOF" % (SALUD, EXPEDIENTE))
        return [
            uso("t1", "Bash", {"command": heredoc}),
            resultado("t1", "ok"),
            uso("t2", "Read", {"file_path": "/Users/x/DICOM_Seattle/informe.md"}),
            resultado("t2", DENEGADO, error=True),
            uso("t3", "Bash", {"command": "grep -rn %s .claude/hooks tests" % EXPEDIENTE}),
            resultado("t3", "hooks/zonas_clinicas.py:1"),
        ]

    def test_seguimiento_en_sesion_de_tooling_no_convoca(self):
        self.escribe(self._tooling())
        self.assertFalse(eg._contexto_clinico(self._tooling()))
        for texto in ("como va esto", "dale al 1", "y sigue arreglando lo abierto"):
            d = self.plan(texto)
            self.assertEqual(d["comites"], [], "«%s» no puede convocar por herencia" % texto)
            self.assertFalse(d["sensible"], texto)

    def test_la_denegacion_sola_no_marca_la_sesion(self):
        lineas = [uso("t2", "Read", {"file_path": "01/%s/informe.md" % EXPEDIENTE}),
                  resultado("t2", DENEGADO, error=True)]
        self.assertFalse(eg._contexto_clinico(lineas))
        # …pero el mismo Read SIN denegación (permitido) sí la marca
        self.assertTrue(eg._contexto_clinico(lineas[:1] + [resultado("t2", "contenido")]))
        # y un error cualquiera (fichero que no existe) no prueba que no se abriera nada
        self.assertTrue(eg._contexto_clinico(lineas[:1] + [resultado("t2", "No such file", True)]))


class TestLoClinicoSigueConvocando(_ConHook):

    def test_ventanilla_ejecutada_y_pregunta_sustantiva(self):
        self.escribe([uso("v1", "Bash", {"command": "python3 tools/lector_clinico.py %s/x.md"
                                         % EXPEDIENTE}), resultado("v1", "texto del informe")])
        d = self.plan("hay uno fuera del higado no? revisa bien")
        self.assertIn("comite-medico", d["comites"])
        self.assertIn("verificacion", d["comites"])

    def test_denegacion_antes_no_tapa_una_lectura_posterior_real(self):
        self.escribe([uso("a", "Read", {"file_path": "%s/informe.md" % ZONA}),
                      resultado("a", DENEGADO, error=True),
                      uso("b", "Read", {"file_path": "%s/informe.md" % ZONA}),
                      resultado("b", "contenido")])
        d = self.plan("hay uno fuera del higado no? revisa bien")
        self.assertIn("comite-medico", d["comites"])
        self.assertIn("verificacion", d["comites"])

    def test_cat_directo_a_la_zona_clinica_sigue_marcando(self):
        lineas = [uso("c", "Bash", {"command": "cat 01/%s/informe.md" % EXPEDIENTE}),
                  resultado("c", "contenido")]
        self.assertTrue(eg._contexto_clinico(lineas))

    def test_peticion_clinica_con_aviso_delante_convoca(self):
        d = self.plan(REMINDER + "analiza el informe molecular de hueso y dime qué dianas ves")
        self.assertIn("comite-medico", d["comites"], d)
        self.assertIn("verificacion", d["comites"], d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
