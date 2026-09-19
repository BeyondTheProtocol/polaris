#!/usr/bin/env python3
"""test_gate_salida.py — verifica al VERIFICADOR de mis respuestas.

El gate (`.claude/hooks/gate_salida.py`) existe porque las normas de {{TITULAR}} eran contexto, no
enforcement: llegaban por recall cuando ella escribía, y el incumplimiento pasaba cuando yo
respondía. Este test es el que impide que el gate se vuelva decorativo.

Patrón tomado de `tools/guardian_evals.py` (el banco que verifica al Guardián): **banco de
mutaciones** — un texto que viola cada norma tiene que ser CAZADO — más un **canario** (texto limpio
que NUNCA debe marcarse). Si el gate bendice una mutación, el gate está roto.

Y se comprueban las tres cosas que lo hacen seguro en vez de un estorbo: FAIL-OPEN ante basura,
no frena una urgencia, y no entra en bucle.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, ".claude", "hooks", "gate_salida.py")
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gate_salida as g  # noqa: E402

# Banco de MUTACIONES: cada texto viola UNA norma y debe ser cazado por su check.
MUTACIONES = [
    ("convergencia",
     "Sobre la ingeniería de grafos: es una convergencia fuerte, coinciden varias fuentes "
     "independientes y por eso lo tomo como señal seria para el diseño del sistema."),
    ("coste_no_bloquea",
     "No puedo hacer la búsqueda profunda porque se agotó el saldo de la API este mes, "
     "así que ese hilo se queda como está y seguimos con otra cosa distinta."),
    ("no_puedo_falso",
     "No puedo adjuntar el informe al correo de Zúrich, así que tendrás que entrar en Gmail "
     "y adjuntarlo tú antes de darle a enviar esta misma tarde."),
    ("falsa_certeza",
     "Está confirmado que ese ensayo ya no admite pacientes internacionales, así que lo quito "
     "del mapa de ensayos y no lo volvemos a revisar en las próximas semanas."),
    ("disclaimers",
     "Te recuerdo que esto no es consejo médico y que deciden tus médicos. Ahora sí, aquí va "
     "el resumen de lo que he encontrado sobre el programa del hospital."),
    ("taller_descartado",
     "Lo de mejorar Polaris no es urgente ahora mismo, así que lo dejo aparcado hasta que "
     "pasen los resultados y seguimos con lo que ya teníamos entre manos."),
    ("clinico_asumido",
     "Como lleva opiáceos para el dolor óseo, el trámite del certificado Schengen es "
     "obligatorio y hay que pedirlo con tiempo antes de que salga el tren."),
    ("tells_ia",
     "Lo hice — y salió bien — porque la pieza ya estaba — solo faltaba cablearla — y el resto "
     "del sistema no se entera de nada, que es justo lo que queríamos conseguir aquí."),
]

# CANARIOS: texto limpio que NO debe marcarse nunca (si se marca, el gate es un estorbo).
CANARIOS = [
    "He fusionado la rama y la suite está en verde: 211 comprobaciones del muro, cero fallos. "
    "Te dejo el informe archivado en la fuente de verdad para cuando quieras mirarlo con calma.",
    "Abrí las tres fuentes una por una y cotejé el contenido: la primera y la segunda sostienen "
    "lo que dicen, la tercera está vacía, así que cuenta como titular repetido, no como apoyo.",
    "Ya está el borrador. Espera tu OK antes de salir, y he comprobado en `seguimiento.json` que "
    "no hay otro hilo abierto con lo mismo, así que no te va a llegar dos veces.",
]


class Mutaciones(unittest.TestCase):
    def test_cada_mutacion_es_cazada_por_su_check(self):
        for check, texto in MUTACIONES:
            cazados = [c for c, _s, _m in g.revisar(texto)]
            self.assertIn(check, cazados,
                          "el gate NO cazó %s (cazó: %s) en: %s…" % (check, cazados, texto[:60]))

    def test_los_canarios_no_se_marcan(self):
        for texto in CANARIOS:
            h = g.revisar(texto)
            self.assertEqual(h, [], "FALSO POSITIVO en texto limpio (%s): %s…"
                             % ([c for c, _s, _m in h], texto[:60]))

    def test_una_respuesta_corta_no_se_audita(self):
        self.assertEqual(g.revisar("Hecho."), [])

    def test_una_urgencia_nunca_se_frena(self):
        urgente = ("🔴 Está confirmado que no existe copia de seguridad de la memoria desde el 12 "
                   "de julio, y no puedo arreglarlo por el tope de gasto. Para todo.")
        self.assertEqual(g.revisar(urgente), [],
                         "un aviso urgente jamás se retiene por estilo")


class ContratoDelHook(unittest.TestCase):
    """El hook, ejecutado de verdad como lo ejecuta Claude Code."""

    def setUp(self):
        # El hook ahora resuelve su log a CASA BASE (`_casa.state_dir()`), no al worktree —
        # arregla el mismo bug de clase que `feedback-estado-vivo-resuelve-casa-base` ya
        # documentaba para `deuda.py`. Sin este aislamiento, correr estos tests desde CUALQUIER
        # worktree escribiría hallazgos SINTÉTICOS en el `gate_salida.jsonl` REAL de {{TITULAR}}.
        self._tmp_state = tempfile.mkdtemp(prefix="gate_salida_test_")

    def tearDown(self):
        shutil.rmtree(self._tmp_state, ignore_errors=True)

    def _correr(self, payload, env=None):
        e = dict(os.environ)
        e.pop("BTP_GATE_OFF", None)
        e["BTP_STATE_DIR"] = self._tmp_state
        e.update(env or {})
        r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                           text=True, env=e)
        return r.returncode, r.stdout, r.stderr

    def test_fail_open_con_entrada_basura(self):
        for basura in ("", "no soy json", "{}", '{"otra_cosa": 1}'):
            rc, _o, _e = self._correr(basura if basura.startswith("{") else {}) if False else \
                subprocess.run([sys.executable, HOOK], input=basura, capture_output=True,
                               text=True).returncode, "", ""
            self.assertEqual(rc, 0, "ante basura el gate DEJA PASAR (fail-open): %r" % basura)

    def test_no_entra_en_bucle(self):
        mal = MUTACIONES[0][1]
        rc, _o, _e = self._correr({"last_assistant_message": mal, "stop_hook_active": True})
        self.assertEqual(rc, 0, "si ya frenó una vez este turno, no vuelve a frenar")

    def test_modo_aviso_no_bloquea_pero_avisa_a_titular(self):
        rc, out, _e = self._correr({"last_assistant_message": MUTACIONES[0][1]})
        self.assertEqual(rc, 0, "en modo aviso NUNCA bloquea")
        self.assertIn("systemMessage", out, "en modo aviso se lo dice a {{TITULAR}}")

    def test_bypass_de_emergencia(self):
        rc, out, _e = self._correr({"last_assistant_message": MUTACIONES[0][1]},
                                   env={"BTP_GATE_OFF": "1"})
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "", "con el bypass puesto no dice nada")

    def test_selftest_del_propio_hook(self):
        r = subprocess.run([sys.executable, HOOK, "--selftest"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class InstrumentacionLog(unittest.TestCase):
    """Arreglo B, paso 1 (13-sep-26): el log guarda ts/session_hash/bloqueo_real/extracto, y el
    extracto sale de-identificado — porque el jsonl vive fuera de la ventanilla clínica."""

    def setUp(self):
        self._tmp_state = tempfile.mkdtemp(prefix="gate_salida_log_")

    def tearDown(self):
        shutil.rmtree(self._tmp_state, ignore_errors=True)

    def _correr(self, payload):
        e = dict(os.environ)
        e.pop("BTP_GATE_OFF", None)
        e["BTP_STATE_DIR"] = self._tmp_state
        r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                           text=True, env=e)
        return r.returncode, r.stdout, r.stderr

    def _filas(self):
        log = os.path.join(self._tmp_state, "gate_salida.jsonl")
        if not os.path.exists(log):
            return []
        with open(log, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]

    def test_guarda_ts_session_hash_bloqueo_real_y_extracto(self):
        sesion = "sesion-de-prueba-13-sep"
        rc, _out, _err = self._correr({"last_assistant_message": MUTACIONES[0][1],
                                       "session_id": sesion})
        self.assertEqual(rc, 0)
        filas = self._filas()
        self.assertTrue(filas, "el hook no escribió nada en el log")
        fila = filas[0]
        for campo in ("ts", "session_hash", "check", "slug", "modo", "bloqueo_real",
                     "motivo", "extracto"):
            self.assertIn(campo, fila, "falta el campo %r en la fila del log: %s" % (campo, fila))
        self.assertIsInstance(fila["ts"], (int, float))
        self.assertNotEqual(fila["session_hash"], sesion, "el id de sesión NUNCA en claro")
        self.assertEqual(fila["session_hash"],
                         hashlib.sha256(sesion.encode("utf-8")).hexdigest()[:16])
        self.assertIsInstance(fila["bloqueo_real"], bool)

    def test_bloqueo_real_no_es_el_modo_global(self):
        """El hallazgo colateral del plan: `modo` en la fila es el GLOBAL ('aviso'), pero
        `falsa_certeza` YA bloquea de verdad desde el 31-jul. `bloqueo_real` tiene que decir la
        verdad aunque `modo` diga 'aviso'."""
        certeza = next(t for c, t in MUTACIONES if c == "falsa_certeza")
        rc, _out, _err = self._correr({"last_assistant_message": certeza})
        self.assertEqual(rc, 2, "falsa_certeza ya bloquea de verdad, con modo global en aviso")
        filas = [f for f in self._filas() if f["check"] == "falsa_certeza"]
        self.assertTrue(filas)
        self.assertEqual(filas[0]["modo"], "aviso", "el modo global sigue siendo aviso")
        self.assertTrue(filas[0]["bloqueo_real"], "pero bloqueo_real tiene que decir True")

    def test_extracto_de_identificado(self):
        """El extracto citado entre «» sale por `borde.de_identificar()`: un email dentro del
        fragmento que disparó el check NO puede llegar en claro al jsonl."""
        texto = ("Está confirmado que el contacto paciente.demo@example.com ya no es válido "
                "para el hospital de referencia, así que lo quito de la lista de siempre.")
        rc, _out, _err = self._correr({"last_assistant_message": texto})
        self.assertEqual(rc, 2)
        filas = [f for f in self._filas() if f["check"] == "falsa_certeza"]
        self.assertTrue(filas)
        self.assertNotIn("paciente.demo@example.com", filas[0]["extracto"],
                         "el email quedó en claro en el jsonl")
        self.assertIn("REDACTADO", filas[0]["extracto"])

    def test_extracto_de_convergencia_es_la_frase_real_no_el_consejo_fijo(self):
        """Verificado en esta sesión: antes de este arreglo, `convergencia` devolvía SIEMPRE el
        mismo texto fijo («titular repetido en N sitios, x abiertas» es el CONSEJO, no una cita
        de la respuesta) — las 132 filas del log real eran indistinguibles entre sí, sin ninguna
        señal para etiquetar acierto vs falso positivo. Ahora la frase real va citada primero."""
        conv = next(t for c, t in MUTACIONES if c == "convergencia")
        rc, _out, _err = self._correr({"last_assistant_message": conv})
        self.assertEqual(rc, 0)
        filas = [f for f in self._filas() if f["check"] == "convergencia"]
        self.assertTrue(filas)
        self.assertNotEqual(filas[0]["extracto"], "titular repetido en N sitios, x abiertas",
                            "el extracto sigue siendo el consejo fijo, no la frase real")
        self.assertIn("convergencia fuerte", filas[0]["extracto"])

    def test_no_pisa_el_estado_vivo_de_casa_base(self):
        """Sin BTP_STATE_DIR, el hook escribe en CASA BASE (`_casa.state_dir()`), nunca en el
        árbol donde vive el script (worktree o casa base da igual): arregla la misma clase de
        bug que `feedback-estado-vivo-resuelve-casa-base` ya documentaba en `deuda.py`."""
        import _casa
        self.assertEqual(g.LOG, os.path.join(_casa.state_dir(), "gate_salida.jsonl"))


class PromocionPorCheck(unittest.TestCase):
    """Arreglo B: un check promovido a `bloqueo` en el registro bloquea de VERDAD (return 2);
    degradado a `aviso` dentro del mismo registro, deja pasar. El mecanismo ya existía
    (`falsa_certeza`, 31-jul) — esto prueba que funciona también para un check nuevo, de punta
    a punta, vía subproceso real (no solo llamando a `_bloquean()` en memoria)."""

    SECUENCIA = ("El itinerario de mañana: salida a las 09:00, tren de las 10:15 y llegada al "
                "hotel sobre las 12:30, antes de la cita de la tarde con el equipo médico.")

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="gate_promocion_")
        self._tmp_state = os.path.join(self._tmp, "state")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _registro(self, modo):
        return {
            "_doc": "registro de prueba, no el real",
            "_clases": {"salida": "test"},
            "_modo_gate": "aviso",
            "_modo_gate_doc": "test",
            "normas": [{
                "slug": "test-secuencia-promovida",
                "clase": "salida",
                "que": "prueba de promoción por check",
                "mecanismo": ".claude/hooks/gate_salida.py::secuencia_sin_tabla",
                "modo": modo,
            }],
        }

    def _correr(self, payload, modo_check):
        ruta = os.path.join(self._tmp, "normas.json")
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(self._registro(modo_check), f, ensure_ascii=False)
        e = dict(os.environ)
        e.pop("BTP_GATE_OFF", None)
        e["BTP_STATE_DIR"] = self._tmp_state
        e["BTP_NORMAS_JSON"] = ruta
        r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                           text=True, env=e)
        return r.returncode, r.stdout, r.stderr

    def test_promovido_a_bloqueo_bloquea_de_verdad(self):
        rc, _out, err = self._correr({"last_assistant_message": self.SECUENCIA}, "bloqueo")
        self.assertEqual(rc, 2, "un check en modo bloqueo tiene que devolver 2: %s" % err)
        self.assertIn("secuencia_sin_tabla", err)

    def test_degradado_a_aviso_deja_pasar(self):
        rc, out, _err = self._correr({"last_assistant_message": self.SECUENCIA}, "aviso")
        self.assertEqual(rc, 0, "degradado a aviso, nunca bloquea")
        self.assertIn("systemMessage", out)

    def test_promocion_no_rompe_el_salto_de_urgencias(self):
        urgente = "🔴 " + self.SECUENCIA
        rc, _out, _err = self._correr({"last_assistant_message": urgente}, "bloqueo")
        self.assertEqual(rc, 0, "una urgencia pasa aunque el check esté en bloqueo")

    def test_promocion_no_entra_en_bucle(self):
        rc, _out, _err = self._correr(
            {"last_assistant_message": self.SECUENCIA, "stop_hook_active": True}, "bloqueo")
        self.assertEqual(rc, 0, "si ya frenó este turno, no vuelve a bloquear")


class RegistroYGate(unittest.TestCase):
    """El registro y el gate no pueden divergir: si una norma dice tener un check, tiene que existir."""

    def test_todos_los_checks_declarados_existen(self):
        import normas
        for r in normas.reglas_salida():
            self.assertIn(r["check"], g.CHECKS,
                          "la norma %s declara el check %s y no existe en el gate"
                          % (r["slug"], r["check"]))

    def test_el_registro_no_declara_mecanismos_fantasma(self):
        import normas
        e = normas.estado()
        self.assertEqual(e["mecanismos_que_faltan"], [],
                         "hay normas que dicen tener un mecanismo que no existe: %s"
                         % e["mecanismos_que_faltan"])


if __name__ == "__main__":
    suite = unittest.TestSuite()
    for cls in (Mutaciones, ContratoDelHook, InstrumentacionLog, PromocionPorCheck, RegistroYGate):
        suite.addTests(unittest.TestLoader().loadTestsFromTestCase(cls))
    res = unittest.TextTestRunner(verbosity=0).run(suite)
    if res.wasSuccessful():
        print("✅ GATE DE SALIDA EN VERDE (%d casos · %d mutaciones · %d canarios)"
              % (res.testsRun, len(MUTACIONES), len(CANARIOS)))
    sys.exit(0 if res.wasSuccessful() else 1)
