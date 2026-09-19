#!/usr/bin/env python3
"""Test: Polaris decide quién responde cada petición, y la respuesta no sale sin haberlo ejecutado.

POR QUÉ EXISTE (11-sep-2026). {{TITULAR}}: *«esa decisión no es mía… lo tiene que hacer Polaris y no
tengo que decirle yo nada»*. Había dos enrutadores (comité y LLM) que no se hablaban, y los dos
solo avisaban. Aquí se prueba la cadena entera:

  1. `tools/decide_peticion.py` — UNA decisión (directo / llm / comite / panel), <1 s, y con una
     precisión mínima contra un set etiquetado (`tests/fixtures/enrutado_eval.jsonl`).
  2. `.claude/hooks/enrutado_guard.py` — la guarda por sesión e ignora las notificaciones.
  3. `.claude/hooks/gate_salida.py::enrutado_incumplido` — si el turno no la ejecutó, la acusa.

Lo que no puede fallar nunca: el muro. Con dato suyo, jamás se ordena un LLM de fuera.
"""
import json
import io
import shutil
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))

os.environ["BTP_ENRUTA_SIN_REFRESCO"] = "1"
_STATE = tempfile.mkdtemp()
os.environ["BTP_STATE_DIR"] = _STATE

import decide_peticion as dp   # noqa: E402
import enruta                  # noqa: E402

VIVOS = {n: {"ok": True, "detalle": "test"} for n in enruta.PROVEEDORES}
EVAL = os.path.join(ROOT, "tests", "fixtures", "enrutado_eval.jsonl")
PRECISION_MINIMA = 0.90


def _d(prompt, **kw):
    return dp.decidir(prompt, estado=VIVOS, **kw)


def _linea_user(texto):
    return json.dumps({"type": "user", "message": {"role": "user", "content": texto}})


def _linea_tool(nombre, entrada):
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": nombre, "input": entrada}]}})


def _linea_result():
    return json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "content": "ok"}]}})


class LaDecision(unittest.TestCase):
    def test_charla_va_directo_y_sin_coste(self):
        d = _d("qué tal el día")
        self.assertEqual(d["nivel"], dp.DIRECTO)
        self.assertEqual((d["comites"], d["llms"]), ([], []))
        self.assertEqual(d["orden"], "")

    def test_clinico_exige_comite_y_watchdog(self):
        d = _d("analiza el informe del panel molecular y dime qué dianas ves")
        self.assertIn(d["nivel"], (dp.COMITE, dp.PANEL))
        self.assertIn("comite-medico", d["comites"])
        self.assertIn("verificacion", d["comites"])

    def test_buscar_en_x_ordena_grok(self):
        d = _d("mira qué se dice en X sobre Polaris esta semana")
        self.assertEqual(d["nivel"], dp.LLM)
        self.assertEqual(d["llms"], ["grok"])
        self.assertIn("enruta.py --ejecutar", d["orden"])

    def test_la_orden_es_de_polaris_no_de_titular(self):
        d = _d("analiza el informe molecular")
        self.assertIn("decisión de Polaris, no de {{TITULAR}}", d["orden"])
        self.assertIn(dp.VALVULA, d["orden"])

    def test_notificacion_del_sistema_no_es_una_peticion(self):
        d = _d("<task-notification><summary>FGFR1 amplificado, tarea pendiente</summary>"
               "</task-notification>")
        self.assertEqual(d["nivel"], dp.DIRECTO)
        self.assertEqual(d["comites"], [])

    def test_decidir_es_rapido(self):
        t0 = time.time()
        for _ in range(20):
            dp.decidir("busca qué se ha publicado esta semana sobre FGFR1 en mama HR+")
        self.assertLess((time.time() - t0) / 20, 0.5, "decidir tiene que caber en un hook")

    def test_nunca_revienta(self):
        for entrada in (None, "", " ", "x" * 5000, "🙂", 12345):
            with self.subTest(entrada=repr(entrada)[:20]):
                self.assertIsInstance(_d(entrada), dict)


class ElMuroMandaSobreLaDecision(unittest.TestCase):
    def test_dato_clinico_nunca_ordena_un_llm_de_fuera(self):
        for frase in ("busca qué se ha publicado esta semana sobre FGFR1 amplificado en mama HR+",
                      "busca en X quién habla de la variante BRCA1 c.68_69delAG",
                      "dame papers sobre la vacuna personalizada con HLA-A*02:01"):
            with self.subTest(frase=frase):
                d = _d(frase)
                for n in d["llms"]:
                    self.assertTrue(enruta.PROVEEDORES[n]["confianza"],
                                    "%s ordenado para contenido sensible: fuga" % n)

    def test_sesion_clinica_cierra_los_llms_de_fuera(self):
        d = _d("mira qué se dice en X sobre Polaris esta semana", sesion_sensible=True)
        self.assertEqual(d["llms"], [])
        self.assertTrue(d["sensible"])

    def test_vision_se_sugiere_pero_nunca_se_ordena(self):
        """Una imagen «neutra» puede ser su biopsia: el material va dentro, así que no se ordena."""
        d = _d("mira esta imagen y dime qué se ve")
        self.assertNotIn("gemini", d["llms"])

    def test_pii_cruda_no_cambia_sensible_ni_llms_ni_comites(self):
        """Arreglo A (13-sep-26, `plan-enrutado-crudo-solo-local-y-gate-por-check`) toca
        `enruta.elegir()` para que crudo identificable (N2) excluya a `claude`, NO
        `decide_peticion.decidir()`: éste solo mira `de.sensible` (que ya era True para PII
        antes del arreglo, porque `clasificar()` incluye las mismas regex de PII) y, cuando NO
        es sensible, `de.elegidos` filtrando a los NO-confiables. Con PII, sensible es siempre
        True, así que el arreglo no puede cambiar ni la sensibilidad ni el triaje. Congela el
        contrato para un texto con PII real (DNI + email + NHC), antes y después del arreglo."""
        d = _d("Informe del caso, DNI 12345678Z, contacto alguien@ejemplo.com, NHC 999001")
        self.assertTrue(d["sensible"])
        self.assertEqual(d["llms"], [], "con dato sensible nunca se ordena un LLM de fuera")


class Precision(unittest.TestCase):
    def test_precision_minima_contra_el_set_etiquetado(self):
        casos, fallos = dp.evaluar(EVAL)
        self.assertGreaterEqual(casos, 40, "el set de evaluación se ha quedado corto")
        precision = 1 - len(fallos) / casos
        self.assertGreaterEqual(precision, PRECISION_MINIMA,
                                "precisión %.0f%%; fallos: %s" % (precision * 100, fallos))


class ElCumplimiento(unittest.TestCase):
    """De punta a punta: hook de entrada → plan guardado → Stop con el transcript del turno."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.transcript = os.path.join(self.tmp, "t.jsonl")
        self.sesion = "test-%d" % (time.time() * 1000)

    def _entrar(self, prompt):
        r = subprocess.run([sys.executable, os.path.join(ROOT, ".claude/hooks/enrutado_guard.py")],
                           input=json.dumps({"prompt": prompt, "session_id": self.sesion,
                                             "transcript_path": self.transcript}),
                           capture_output=True, text=True, timeout=20,
                           env=dict(os.environ, CLAUDE_PROJECT_DIR=ROOT))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def _turno(self, lineas):
        with open(self.transcript, "w") as f:
            f.write("\n".join(lineas) + "\n")

    def _acusa(self, texto):
        import gate_salida as g
        g.SESION = self.sesion
        return g.enrutado_incumplido(texto, g._tools_del_turno(self.transcript))

    def test_la_entrada_inyecta_la_orden_y_la_guarda(self):
        out = self._entrar("analiza el informe molecular de hueso")
        self.assertIn("ENRUTADO (", json.loads(out)["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(dp.cargar(self.sesion)["nivel"], dp.COMITE)

    def test_la_notificacion_no_pisa_el_plan_del_turno(self):
        self._entrar("analiza el informe molecular de hueso")
        self.assertEqual(self._entrar("<task-notification>FGFR1 hecho</task-notification>"), "")
        self.assertEqual(dp.cargar(self.sesion)["nivel"], dp.COMITE)

    def test_sin_ejecutar_se_acusa(self):
        self._entrar("analiza el informe molecular de hueso")
        self._turno([_linea_user("analiza el informe molecular de hueso"),
                     _linea_tool("Read", {"file_path": "README.md"}), _linea_result()])
        self.assertIsNotNone(self._acusa("Aquí va mi análisis de las dianas, hecho a pelo."))

    def test_un_hecho_corto_tambien_se_acusa(self):
        self._entrar("analiza el informe molecular de hueso")
        self._turno([_linea_user("analiza el informe molecular de hueso")])
        import gate_salida as g
        g.SESION = self.sesion
        checks = [c for c, _s, _m in g.revisar("hecho", g._tools_del_turno(self.transcript))]
        self.assertIn("enrutado_incumplido", checks)

    def test_ejecutado_pasa_aunque_haya_tool_results_por_medio(self):
        """Regresión: los tool_result llevan role=user y borraban del turno el Agent anterior."""
        self._entrar("analiza el informe molecular de hueso")
        self._turno([_linea_user("analiza el informe molecular de hueso"),
                     _linea_tool("Agent", {"subagent_type": "comite-medico"}), _linea_result(),
                     _linea_tool("Agent", {"subagent_type": "verificacion"}), _linea_result(),
                     _linea_tool("Read", {"file_path": "README.md"}), _linea_result()])
        self.assertIsNone(self._acusa("Síntesis de comite-medico y verificacion. Respondió: …"))

    def test_llm_ordenado_se_da_por_ejecutado_via_enruta(self):
        self._entrar("mira qué se dice en X sobre Polaris esta semana")
        self._turno([_linea_user("mira qué se dice en X sobre Polaris esta semana"),
                     _linea_tool("Bash", {"command": "python3 tools/enruta.py --ejecutar \"...\""}),
                     _linea_result()])
        self.assertIsNone(self._acusa("Esto es lo que se dice en X. Respondió: grok."))

    def test_la_valvula_pasa_y_queda_registrada(self):
        self._entrar("analiza el informe molecular de hueso")
        self._turno([_linea_user("analiza el informe molecular de hueso")])
        self.assertIsNone(self._acusa("Nada que analizar aún.\nENRUTADO-OMITIDO: aún no hay informe"))
        import gate_salida as g
        registro = open(g.LOG_OMITIDOS, encoding="utf-8").read()
        self.assertIn("aún no hay informe", registro)

    def test_directo_nunca_acusa(self):
        self._entrar("qué tal el día")
        self._turno([_linea_user("qué tal el día")])
        self.assertIsNone(self._acusa("Bien, tranquilo."))

    def test_la_norma_esta_registrada(self):
        import gate_salida as g
        activas, _ = g._reglas_activas()
        self.assertIn("enrutado_incumplido", [c for c, _s, _m in activas])


class LaCajaSeConvoca(unittest.TestCase):
    """La petición entra en su caja, y el gate exige sus asientos.

    Antes la caja se SUGERÍA (una cadena recordando el dossier) y los asientos se
    enumeraban desde la tabla de dominio, sin el goal. Lo que esto protege:
      1. Que los asientos del charter entren en `comites` — ahí es donde el gate de Stop
         mira, así que soldarlo aquí es lo que lo vuelve exigible sin tocar el gate.
      2. Que ante DOS cajas empatadas no se convoque ninguna: convocar la equivocada gasta
         presupuesto y contesta a otra pregunta.
      3. Que una petición ajena a toda caja siga comportándose exactamente igual que antes.
    """

    def setUp(self):
        self.raiz = tempfile.mkdtemp(prefix="dp-caja-")
        self.cons = os.path.join(self.raiz, "00_FUENTE-DE-VERDAD", "04 · IA", "Constelacion")
        os.makedirs(self.cons)
        import caja as CJ
        self.CJ, self._previo = CJ, CJ.ROOT
        CJ.ROOT = self.raiz

    def tearDown(self):
        self.CJ.ROOT = self._previo
        shutil.rmtree(self.raiz, ignore_errors=True)

    def _caja(self, slug, goal, estado="activa", dueno="finanzas-transparencia",
              expertos="[legal-burocracia]"):
        d = os.path.join(self.cons, slug)
        os.makedirs(d, exist_ok=True)
        with io.open(os.path.join(d, "CAJA.md"), "w", encoding="utf-8") as f:
            f.write("---\ncaja: {s}\nestado: {e}\ndueno: {d}\nexpertos: {x}\n"
                    "arquetipo: solo-lectura\n---\n# X\n\n## Goal\n{g}\n\n## Fin\nx\n"
                    .format(s=slug, e=estado, d=dueno, x=expertos, g=goal))

    def test_los_asientos_del_charter_entran_en_comites(self):
        self._caja("donaciones", "Que la gente pueda apoyar donando directamente y legal.")
        d = dp.decidir("quiero que puedan apoyar donando directamente, legal y transparente")
        self.assertEqual("donaciones", d.get("caja"))
        self.assertIn("finanzas-transparencia", d["comites"])
        self.assertIn("legal-burocracia", d["comites"])

    def test_el_gate_los_exige(self):
        """La prueba de que la soldadura sirve: el enforcement existente ya los reclama."""
        self._caja("donaciones", "Que la gente pueda apoyar donando directamente y legal.")
        d = dp.decidir("quiero que puedan apoyar donando directamente, legal y transparente")
        self.assertTrue(dp.falta_por_ejecutar(d, []))
        self.assertEqual([], dp.falta_por_ejecutar(
            d, ["finanzas-transparencia", "legal-burocracia"]))

    def test_la_orden_convoca_en_vez_de_enumerar(self):
        self._caja("donaciones", "Que la gente pueda apoyar donando directamente y legal.")
        d = dp.decidir("quiero que puedan apoyar donando directamente, legal y transparente")
        self.assertIn("caja.py convocar --caja donaciones", d["orden"])
        self.assertIn("dossier --caja donaciones", d["orden"])

    def test_una_caja_que_no_esta_activa_no_se_enruta(self):
        for estado in ("propuesta", "en-pausa", "archivada"):
            self._caja("parada", "Que la gente pueda apoyar donando directamente y legal.",
                       estado=estado)
            d = dp.decidir("quiero que puedan apoyar donando directamente, legal y transparente")
            self.assertIsNone(d.get("caja"), estado)

    def test_dos_cajas_empatadas_no_convocan_ninguna(self):
        self._caja("una", "Apoyar donando directamente legal transparente.")
        self._caja("otra", "Apoyar donando directamente legal transparente.")
        d = dp.decidir("quiero que puedan apoyar donando directamente, legal y transparente")
        self.assertIsNone(d.get("caja"))

    def test_peticion_ajena_no_cambia_de_comportamiento(self):
        self._caja("donaciones", "Que la gente pueda apoyar donando directamente y legal.")
        d = dp.decidir("busca en redes qué se dice de la vacuna")
        self.assertIsNone(d.get("caja"))

    def test_un_solo_termino_no_basta(self):
        """Con un término común cualquier petición caería en la caja."""
        self._caja("donaciones", "Que la gente pueda apoyar donando directamente y legal.")
        self.assertEqual((None, []), dp.caja_aplicable("apoyar"))


if __name__ == "__main__":
    suite = unittest.TestSuite()
    for cls in (LaDecision, ElMuroMandaSobreLaDecision, Precision, ElCumplimiento,
                LaCajaSeConvoca):
        suite.addTests(unittest.TestLoader().loadTestsFromTestCase(cls))
    res = unittest.TextTestRunner(verbosity=0).run(suite)
    if res.wasSuccessful():
        print("✅ DECIDE Y EJECUTA EN VERDE (%d casos · el muro manda · se acusa lo no ejecutado)"
              % res.testsRun)
    sys.exit(0 if res.wasSuccessful() else 1)
