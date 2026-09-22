#!/usr/bin/env python3
"""test_ciclo_agentes.py — cada agente deja id, turno y motivo de salida; y nadie borra transcripts.

14-sep-2026, a raíz de @IAenBruto: «¿Polaris conserva el agent_id y el motivo de salida?». Lo que
protege, con fixtures de la forma REAL del harness (meta.json, notificación de fin, stop_reason):
  1. Que el lanzamiento quede con agent_id y tool_use_id, sin prompt ni descripción ni respuesta.
  2. Que el final se resuelva de la fuente correcta: notificación (completed/failed/stopped), diario
     del workflow, o el transcript (end_turn / cortado), y que un agente activo siga «vivo».
  3. Que sea idempotente: un final por agente, se pase las veces que se pase.
  4. Que descubra en disco los agentes que no pasaron por el hook.
  5. Que `ttl` SOLO informe: ningún fichero bajo projects se borra ni se mueve.
  6. Que el healthcheck (cada 30 min) haga la pasada solo, sin caerse si falla.
"""
import inspect
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))

SECRETO = "PROMPT-QUE-NO-DEBE-QUEDAR-EN-EL-REGISTRO-9921"


class TestCicloAgentes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ciclo-")
        self.state = os.path.join(self.tmp, "state")
        self.projects = os.path.join(self.tmp, "projects")
        os.environ["BTP_STATE_DIR"] = self.state
        os.environ["BTP_PROJECTS_DIR"] = self.projects
        sys.modules.pop("ciclo_agentes", None)
        import ciclo_agentes
        self.c = ciclo_agentes
        self.sid = "11111111-2222-3333-4444-555555555555"
        self.proy = os.path.join(self.projects, "-proyecto")
        self.sub = os.path.join(self.proy, self.sid, "subagents")
        os.makedirs(self.sub)
        self.padre = os.path.join(self.proy, self.sid + ".jsonl")
        open(self.padre, "w").close()

    def tearDown(self):
        for k in ("BTP_STATE_DIR", "BTP_PROJECTS_DIR"):
            os.environ.pop(k, None)
        sys.modules.pop("ciclo_agentes", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── utilidades con la forma real ────────────────────────────────────────────────────
    def _agente(self, aid, tuid, tipo="verificacion", final="end_turn", viejo=False, carpeta=None):
        d = carpeta or self.sub
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "agent-%s.meta.json" % aid), "w") as f:
            json.dump({"agentType": tipo, "description": SECRETO, "toolUseId": tuid,
                       "spawnDepth": 1, "requestShape": "background"}, f)
        ruta = os.path.join(d, "agent-%s.jsonl" % aid)
        with open(ruta, "w") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": SECRETO}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {
                "role": "assistant", "stop_reason": final,
                "content": [{"type": "text", "text": SECRETO}]}}) + "\n")
        if viejo:
            t = time.time() - 30 * 3600
            os.utime(ruta, (t, t))
        return ruta

    def _notificacion(self, tuid, status, resumen):
        texto = ("<task-notification>\n<task-id>x</task-id>\n<tool-use-id>%s</tool-use-id>\n"
                 "<status>%s</status>\n<summary>%s</summary>\n</task-notification>" % (tuid, status, resumen))
        with open(self.padre, "a") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": texto}}) + "\n")

    def _registro_crudo(self):
        crudo = ""
        d = os.path.join(self.state, "agentes")
        for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            crudo += open(os.path.join(d, n), encoding="utf-8").read()
        return crudo

    # ── 1. lanzamiento ──────────────────────────────────────────────────────────────────
    def test_lanzamiento_guarda_ids_sin_contenido(self):
        fila = self.c.apuntar_lanzamiento({
            "tool_name": "Agent", "session_id": self.sid, "tool_use_id": "toolu_A",
            "transcript_path": self.padre,
            "tool_input": {"subagent_type": "verificacion", "prompt": SECRETO, "description": SECRETO},
            "tool_response": {"agentId": "a1b2c3", "status": "async_launched", "isAsync": True,
                              "resolvedModel": "claude-opus-5", "description": SECRETO, "prompt": SECRETO}})
        self.assertEqual("a1b2c3", fila["agent_id"])
        self.assertEqual("toolu_A", fila["tool_use_id"])
        self.assertTrue(fila["async"])
        self.assertNotIn(SECRETO, self._registro_crudo(), "ni prompt, ni descripción, ni respuesta")

    def test_lanzamiento_ignora_lo_que_no_es_un_agente(self):
        self.assertIsNone(self.c.apuntar_lanzamiento({"tool_name": "Bash", "tool_response": {"agentId": "x"}}))
        self.assertIsNone(self.c.apuntar_lanzamiento({"tool_name": "Agent", "tool_response": {}}))
        self.assertIsNone(self.c.apuntar_lanzamiento({"tool_name": "Agent",
                                                     "tool_response": {"agentId": "../../etc"}}))
        self.assertIsNone(self.c.apuntar_lanzamiento("basura"))

    # ── 2. el final, de la fuente correcta ──────────────────────────────────────────────
    def test_notificacion_completed_failed_stopped(self):
        for aid, tuid, st in (("a1", "toolu_1", "completed"), ("a2", "toolu_2", "failed"),
                              ("a3", "toolu_3", "stopped")):
            self._agente(aid, tuid, final=None)
            self._notificacion(tuid, st, "Agent %s terminó así" % aid)
        r = self.c.cerrar()
        self.assertEqual({"a1": "completed", "a2": "failed", "a3": "stopped"}, r)
        filas = {f["agent_id"]: f for f in self.c.estado()}
        self.assertEqual("notificacion", filas["a2"]["fin_fuente"])
        self.assertIn("a2", filas["a2"]["motivo"])

    def test_sin_notificacion_el_transcript_decide(self):
        self._agente("b1", "toolu_b1", final="end_turn")
        self._agente("b2", "toolu_b2", final=None, viejo=True)
        self._agente("b3", "toolu_b3", final=None)
        r = self.c.cerrar()
        self.assertEqual("completed", r["b1"])
        self.assertEqual("cortado", r["b2"])
        self.assertEqual("vivo", r["b3"], "activo y reciente: no se le inventa un final")

    def test_sesion_repartida_entre_carpetas_de_proyecto(self):
        """14-sep-2026: la sesión entra en un worktree y su transcript sigue en otra carpeta de
        proyecto; los meta.json se quedan donde nacieron. Sin buscar fuera, 35 agentes reales
        salían «vivo» teniendo su notificación escrita."""
        self._agente("f1", "toolu_f1", final=None)
        os.remove(self.padre)
        otra = os.path.join(self.projects, "-proyecto--claude-worktrees-otro")
        os.makedirs(otra)
        self.padre = os.path.join(otra, self.sid + ".jsonl")
        self._notificacion("toolu_f1", "completed", "terminó en la otra carpeta")
        self.assertEqual("completed", self.c.cerrar()["f1"])

    def test_agente_de_workflow_con_resultado_en_el_diario(self):
        wf = os.path.join(self.sub, "workflows", "wf_abc-123")
        self._agente("w1", None, tipo="workflow-subagent", final=None, carpeta=wf)
        with open(os.path.join(wf, "journal.jsonl"), "w") as f:
            f.write(json.dumps({"type": "started", "agentId": "w1", "label": "x"}) + "\n")
            f.write(json.dumps({"type": "result", "agentId": "w1", "result": {"a": 1}}) + "\n")
        self.assertEqual("completed", self.c.cerrar()["w1"])

    # ── 3. idempotencia ─────────────────────────────────────────────────────────────────
    def test_un_final_por_agente(self):
        self._agente("c1", "toolu_c1")
        self.c.cerrar()
        self.assertEqual({}, self.c.cerrar(), "segunda pasada: nada nuevo")
        terminales = [ln for ln in self._registro_crudo().splitlines() if '"terminal"' in ln]
        self.assertEqual(1, len(terminales))

    # ── 4. descubre lo que no pasó por el hook ──────────────────────────────────────────
    def test_descubre_en_disco_y_liga_el_turno(self):
        self._agente("d1", "toolu_d1")
        self.c.cerrar()
        fila = [f for f in self.c.estado() if f["agent_id"] == "d1"][0]
        self.assertEqual("disco", fila["fuente"])
        self.assertEqual("toolu_d1", fila["tool_use_id"])
        self.assertEqual(self.sid, fila["sesion"])
        self.assertNotIn(SECRETO, self._registro_crudo())

    # ── 5. ttl solo informa ─────────────────────────────────────────────────────────────
    def test_ttl_no_borra_ni_mueve_nada(self):
        ruta = self._agente("e1", "toolu_e1", viejo=True)
        t = time.time() - 40 * 86400
        os.utime(ruta, (t, t))
        antes = sorted(os.listdir(self.sub))
        info = self.c.ttl(dias=30)
        self.assertEqual(1, info["transcripts_viejos"])
        self.assertIn("ninguna", info["accion"])
        self.assertEqual(antes, sorted(os.listdir(self.sub)), "nada borrado ni movido")
        self.assertTrue(os.path.exists(ruta))

    def test_huerfanos(self):
        self._agente("f1", "toolu_f1", final=None, viejo=True)
        self.c.cerrar()
        self.assertEqual([], self.c.huerfanos(horas=6), "el cortado ya tiene final… ")
        # …pero uno lanzado por el hook hace mucho y sin rastro en disco sí es huérfano
        self.c.apuntar_lanzamiento({"tool_name": "Agent", "session_id": self.sid, "tool_use_id": "toolu_g",
                                    "tool_response": {"agentId": "g1", "status": "async_launched"}})
        viejo = time.time() + 7 * 3600
        self.assertIn("g1", [f["agent_id"] for f in self.c.huerfanos(horas=6, ahora=viejo)])

    # ── 6. la pasada la hace el healthcheck ─────────────────────────────────────────────
    def _healthcheck(self):
        os.environ["BTP_TEST_BATTERY"] = "1"
        import healthcheck
        return healthcheck

    def test_el_healthcheck_apunta_los_finales(self):
        """Sin esto el hook apuntaba lanzamientos y nadie escribía el final (14-sep-26)."""
        self._agente("h1", "toolu_h1", final=None)
        self._notificacion("toolu_h1", "failed", "API error")
        info = self._healthcheck()._ciclo_agentes()
        for k, v in {"resueltos": 1, "por_estado": {"failed": 1}, "huerfanos_24h": 0}.items():
            self.assertEqual(v, info[k], k)
        # el recuento por punto de atasco se añadió el 20-sep-26; va aparte para que el contrato
        # base siga siendo estricto y una clave nueva no lo rompa en silencio
        self.assertEqual({"ejecucion": 1}, info.get("atascos"))
        self.assertEqual("failed", [f for f in self.c.estado() if f["agent_id"] == "h1"][0]["estado"])

    def test_en_la_bateria_no_toca_los_transcripts_reales(self):
        os.environ.pop("BTP_PROJECTS_DIR")
        self.assertEqual({"omitido": "bateria"}, self._healthcheck()._ciclo_agentes())

    def test_run_llama_a_la_pasada_y_no_se_cae_si_falla(self):
        fuente = inspect.getsource(self._healthcheck().run)
        i = fuente.find('chk["ciclo_agentes"] = _ciclo_agentes()')
        self.assertGreater(i, 0, "run() ya no hace la pasada del ciclo de agentes")
        self.assertIn("try:", fuente[i - 40:i], "la pasada tiene que ir dentro de try")
        self.assertIn('chk["ciclo_agentes_error"]', fuente[i:i + 200])


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=0).result
    if res.wasSuccessful():
        print("✅ CICLO DE AGENTES EN VERDE (%d casos)" % res.testsRun)
    sys.exit(0 if res.wasSuccessful() else 1)


class DondeSeAtasca(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="atasco-")
        os.environ["BTP_STATE_DIR"] = os.path.join(self.tmp, "state")
        sys.modules.pop("ciclo_agentes", None)
        import ciclo_agentes
        self.c = ciclo_agentes

    def tearDown(self):
        os.environ.pop("BTP_STATE_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    """Destilado de {{CONTACTO}} {{CONTACTO}} (20-sep-26): antes de dar OTRA herramienta a un agente que
    falla, mira DÓNDE se atasca — entender el encargo, ejecutarlo, o retomar el hilo. Son tres
    arreglos distintos y añadir un MCP más no cura ninguno. El motivo ya se guardaba; esto lo
    agrupa para poder responder la pregunta con datos."""

    def test_lo_que_termina_bien_no_es_un_atasco(self):
        self.assertEqual(self.c.clasificar_atasco("completed", ""), "ok")

    def test_los_tres_puntos_se_distinguen(self):
        self.assertEqual(self.c.clasificar_atasco("failed", "Traceback: KeyError"), "ejecucion")
        self.assertEqual(self.c.clasificar_atasco("failed", "el encargo es ambiguo, no entendí qué"), "encargo")
        self.assertEqual(self.c.clasificar_atasco("stopped", "se perdió el contexto tras el compact"), "retomar")

    def test_sin_senal_dice_sin_clasificar_en_vez_de_inventar(self):
        self.assertEqual(self.c.clasificar_atasco("failed", "vete tú a saber"), "sin_clasificar")
        self.assertEqual(self.c.clasificar_atasco("failed", ""), "sin_clasificar")

    def test_el_recuento_solo_mira_finales_resueltos(self):
        cuenta = self.c.atascos(dias=30)
        self.assertIsInstance(cuenta, dict)
        self.assertTrue(set(cuenta) <= {"ok", "encargo", "ejecucion", "retomar", "sin_clasificar"})
