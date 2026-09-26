#!/usr/bin/env python3
"""test_juez_normas.py — pasos 3 y 4 del juez de las normas de salida (capa 3).

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026: punto de
rotura 08 y deuda `capa3_juez_pendiente`. Pasos 3 y 4 aprobados por {{TITULAR}} el 26-sep-2026.

Fija:
  · el juez: parseo de su JSON, prompt con la rúbrica de SU norma, contexto recortado; el borde va
    ANTES de llamar (si deniega, no se lanza nada) y la llamada es `claude -p --bare` sin tools.
  · las métricas: tasa de FP con cota Clopper-Pearson, recall con cota inferior, kappa, listón.
  · la rutina diaria: apunta SOLO los `incumple`, en modo `sombra`, con session_hash; idempotente.
  · el banco versionado: ids de turno, etiquetas válidas y motivos sin nada que `borde` redacte.
Sin red, sin gastar: el binario de `claude` es un guion falso y el estado va a un tmp.
"""
import json
import os
import re
import stat
import sys
import tempfile
import types
import unittest

_TMP = tempfile.mkdtemp(prefix="test_juez_normas_")
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "state")
os.makedirs(os.environ["BTP_STATE_DIR"])

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import juez_normas as jn  # noqa: E402

BANCO = os.path.join(ROOT, "tests", "juez_banco.json")


def _caso(norma="cotejar_fuente", **kw):
    c = {"id": "abcd1234@2026-09-20T10:00:00.000Z", "norma": norma, "slug": "x",
         "session_hash": "0123456789abcdef", "pregunta": "¿cuánto medía?",
         "previa": "La lesión medía lo que dije.", "respuesta": "Tu lesión mide 12 mm.",
         "tools": ["Bash", "Bash", "cat informe.md"]}
    c.update(kw)
    return c


class Juez(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(jn.parse('bla {"veredicto": "incumple", "frase": "x", "motivo": "y"} fin')
                         ["veredicto"], "incumple")
        self.assertEqual(jn.parse('{"veredicto": "No aplica"}')["veredicto"], "no_aplica")
        self.assertIsNone(jn.parse('{"veredicto": "quizá"}'))
        self.assertIsNone(jn.parse("sin json"))
        self.assertIsNone(jn.parse(None))

    def test_prompt_lleva_su_rubrica_y_el_contexto_justo(self):
        p = jn.prompt(_caso())
        self.assertIn(jn.rubricas()["cotejar_fuente"]["pregunta"], p)
        self.assertNotIn("RESPUESTA PREVIA", p)
        self.assertEqual(p.count("cat informe.md"), 1)
        self.assertEqual(jn.render_caso(_caso()).count("- Bash"), 1)      # sin duplicados
        pc = jn.prompt(_caso("calibrar"))
        self.assertIn("RESPUESTA PREVIA", pc)
        self.assertIn(jn.rubricas()["calibrar"]["pregunta"], pc)
        largo = jn.render_caso(_caso(respuesta="a" * 20000))
        self.assertIn("[…recortado]", largo)
        self.assertLess(len(largo), 8000)

    def test_rubricas_completas(self):
        r = jn.rubricas()
        for n in jn.NORMAS:
            for k in ("slug", "norma", "pregunta", "incumple", "cumple", "no_aplica"):
                self.assertTrue(r[n][k], (n, k))


class Llamada(unittest.TestCase):
    def setUp(self):
        self.argv = os.path.join(_TMP, "argv.json")
        self.bin = os.path.join(_TMP, "claude_falso")
        with open(self.bin, "w") as f:
            f.write("#!%s\nimport json,sys\njson.dump(sys.argv[1:], open(%r,'w'))\n"
                    "print(json.dumps({'result': '{\"veredicto\": \"cumple\", \"frase\": \"f\", "
                    "\"motivo\": \"m\"}', 'total_cost_usd': 0.01, 'is_error': False}))\n"
                    % (sys.executable, self.argv))
        os.chmod(self.bin, os.stat(self.bin).st_mode | stat.S_IEXEC)
        os.environ["BTP_CLAUDE_BIN"] = self.bin
        os.environ["ANTHROPIC_API_KEY"] = "falsa"
        self._cg = sys.modules.get("cost_guard")
        cg = types.ModuleType("cost_guard")
        cg.check_before_job = lambda *a, **k: (True, "", 1.0)
        cg.gastos = []
        cg.add_cost = lambda usd, **k: cg.gastos.append(usd)
        sys.modules["cost_guard"] = cg
        self.cg = cg

    def tearDown(self):
        os.environ.pop("BTP_CLAUDE_BIN", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        if self._cg is not None:
            sys.modules["cost_guard"] = self._cg
        else:
            sys.modules.pop("cost_guard", None)
        if os.path.exists(self.argv):
            os.remove(self.argv)

    def test_claude_bare_sin_tools_y_apunta_gasto(self):
        r = jn.juzgar(_caso())
        self.assertEqual(r["veredicto"], "cumple")
        args = json.load(open(self.argv))
        self.assertIn("--bare", args)
        self.assertEqual(args[args.index("--tools") + 1], "")
        self.assertEqual(args[args.index("--model") + 1], jn.MODELO)
        self.assertEqual(self.cg.gastos, [0.01])

    def test_borde_deniega_no_llama(self):
        import borde
        orig = borde.egress_check
        borde.egress_check = lambda *a, **k: types.SimpleNamespace(permitido=False, motivo="test")
        try:
            r = jn.juzgar(_caso())
        finally:
            borde.egress_check = orig
        self.assertTrue(r["error"].startswith("borde"))
        self.assertFalse(os.path.exists(self.argv))

    def test_tope_no_llama(self):
        self.cg.check_before_job = lambda *a, **k: (False, "tope", 0.0)
        self.assertEqual(jn.juzgar(_caso())["error"], "tope_local")
        self.assertFalse(os.path.exists(self.argv))


class Metricas(unittest.TestCase):
    def test_cuentas_y_cotas(self):
        pares = ([("incumple", "incumple")] * 8 + [("cumple", "incumple")] * 1
                 + [("incumple", "cumple")] * 2 + [("no_aplica", "no_aplica")] * 5)
        m = jn.metricas(pares)
        self.assertEqual((m["tp"], m["fp"], m["fn"], m["tn"]), (8, 1, 2, 5))
        self.assertAlmostEqual(m["recall"], 0.8)
        self.assertGreater(m["fp_cota"], m["fp_tasa"])          # la cota siempre por encima
        self.assertLess(m["recall_cota_inf"], m["recall"])
        self.assertFalse(m["pasa"])                              # 1/9 FP: cota > 20 %
        m2 = jn.metricas([("incumple", "incumple")] * 14 + [("cumple", "cumple")] * 5)
        self.assertTrue(m2["pasa"])                              # 0/14 → cota ≈ 19 %
        self.assertFalse(jn.metricas([("cumple", "cumple")] * 10)["pasa"])   # sin positivos no se mide

    def test_kappa(self):
        a = ["incumple", "cumple", "no_aplica", "cumple"]
        self.assertAlmostEqual(jn.kappa(a, a), 1.0)
        self.assertLess(jn.kappa(a, ["cumple", "incumple", "cumple", "no_aplica"]), 0)


class Diario(unittest.TestCase):
    def test_sombra_solo_incumple_e_idempotente(self):
        import gate_etiqueta as ge
        import prefiltro_juez as pj
        casos = [_caso(), _caso("calibrar", id="ffff0000@2026-09-20T11:00:00.000Z")]
        orig_c, orig_j = pj.candidatos, jn.juzgar
        pj.candidatos = lambda **k: [dict(c) for c in casos]
        jn.juzgar = lambda c, *a, **k: ({"veredicto": "incumple", "frase": "Tu lesión mide 12 mm.",
                                         "motivo": "sin fuente", "coste_usd": 0.02}
                                        if c["norma"] == "cotejar_fuente" else
                                        {"veredicto": "cumple", "coste_usd": 0.02})
        try:
            r = jn.diario(forzar=True)
            r2 = jn.diario(forzar=True)
        finally:
            pj.candidatos, jn.juzgar = orig_c, orig_j
        self.assertEqual(r["por_norma"]["cotejar_fuente"]["incumple"], 1)
        self.assertEqual(r["por_norma"]["calibrar"]["cumple"], 1)
        self.assertEqual(r2["por_norma"]["cotejar_fuente"]["candidatos"], 0)   # ya juzgado
        filas = [json.loads(l) for l in open(ge.LOG, encoding="utf-8")]
        self.assertEqual(len(filas), 1)
        f = filas[0]
        self.assertEqual((f["check"], f["modo"], f["bloqueo_real"]),
                         ("juez_cotejar_fuente", "sombra", False))
        self.assertEqual(f["session_hash"], "0123456789abcdef")
        self.assertNotIn("12 mm", f["extracto"] + f["motivo"])   # de-identificado con borde


class Modos(unittest.TestCase):
    """Regla de subida ({{TITULAR}}, 26-sep-2026): sombra con precisión ≥ 50 % en la reserva; aviso
    solo con el listón estricto (FP ≤ 20 % por cota Y recall ≥ 60 %)."""

    def test_modo_por_defecto_es_sombra(self):
        self.assertEqual(jn.MODO, "sombra")
        self.assertEqual(jn.NORMAS_ACTIVAS, ("cotejar_fuente",))   # calibrar, fuera de la fase 1

    def test_sombra_pide_precision_50(self):
        self.assertTrue(jn.puede("sombra", {"tp": 5, "fp": 5, "fn": 9, "tn": 0})[0])
        self.assertFalse(jn.puede("sombra", {"tp": 4, "fp": 5, "fn": 0, "tn": 9})[0])

    def test_aviso_exige_liston_estricto(self):
        # precisión 90 % pero pocos disparos: la cota de FP pasa del 20 % → no sube a aviso
        self.assertFalse(jn.puede("aviso", {"tp": 9, "fp": 1, "fn": 0, "tn": 5})[0])
        # 14 aciertos sin FP pero recall 50 % → no sube
        self.assertFalse(jn.puede("aviso", {"tp": 14, "fp": 0, "fn": 14, "tn": 5})[0])
        self.assertTrue(jn.puede("aviso", {"tp": 14, "fp": 0, "fn": 2, "tn": 5})[0])
        # y con la medida real de la reserva, hoy NO
        self.assertFalse(jn.puede("aviso")[0])

    def test_diario_aviso_se_niega_sin_liston(self):
        if jn.puede("aviso")[0]:
            self.skipTest("la reserva ya pasa el listón estricto")
        with self.assertRaises(SystemExit):
            jn.diario(modo="aviso")

    def test_sombra_nunca_avisa(self):
        import prefiltro_juez as pj
        orig = (pj.candidatos, jn.juzgar, jn._avisar)
        llamado = []
        pj.candidatos = lambda **k: [_caso(id="aaaa1111@2026-09-21T10:00:00.000Z")]
        jn.juzgar = lambda c, *a, **k: {"veredicto": "incumple", "frase": "x", "motivo": "y"}
        jn._avisar = lambda r: llamado.append(r)
        try:
            r = jn.diario(modo="sombra", forzar=True)
        finally:
            pj.candidatos, jn.juzgar, jn._avisar = orig
        self.assertEqual(r["modo"], "sombra")
        self.assertEqual(llamado, [])


class Rutina(unittest.TestCase):
    def test_plist_en_sombra_y_registrado(self):
        import plistlib
        p = os.path.join(ROOT, "tools", "launchd", "com.btp.juez-normas.plist")
        d = plistlib.load(open(p, "rb"))
        self.assertEqual(d["Label"], "com.btp.juez-normas")
        self.assertFalse(d.get("RunAtLoad"))
        args = d["ProgramArguments"]
        self.assertTrue(args[1].endswith("tools/juez_normas.py"))
        self.assertEqual(args[2:], ["diario", "--modo", "sombra"])
        reg = json.load(open(os.path.join(ROOT, "tools", "launchd", "REGISTRO.json")))["daemons"]
        self.assertEqual(reg["com.btp.juez-normas"]["script"], "juez_normas.py")
        otros = [k for k, v in reg.items() if v.get("concern") == "juez-normas"]
        self.assertEqual(otros, ["com.btp.juez-normas"])        # concern único: no duplica


class OtraSesion(unittest.TestCase):
    """Regla de {{TITULAR}} (26-sep-2026): un «verificado» apoyado en un cotejo de OTRA sesión que no
    cita el informe concreto ni lo abre en esta INCUMPLE; en otra sesión eso es memoria."""

    def test_regla_en_rubrica_y_pautas(self):
        r = jn.rubricas()["cotejar_fuente"]["incumple"]
        self.assertIn("OTRA sesión", r)
        self.assertIn("memoria", r)
        p = jn.prompt(_caso())
        self.assertIn("OTRA sesión", p)
        self.assertIn("ESTA SESIÓN EMPEZÓ EL", p)

    def test_documentos_de_la_misma_sesion_llegan_al_juez(self):
        c = _caso(sesion_desde="2026-09-14", dia="2026-09-16",
                  documentos_sesion_previos=["2026-09-15 · 2026-05-26 - {{CENTRO}} - pet.pdf"])
        p = jn.prompt(c)
        self.assertIn("ESTA SESIÓN EMPEZÓ EL 2026-09-14", p)
        self.assertIn("2026-09-15 · 2026-05-26 - {{CENTRO}} - pet.pdf", p)

    def test_documentos_leidos_ve_la_entrada_entera(self):
        import prefiltro_juez as pj
        ruta = os.path.join(_TMP, "sesion.jsonl")
        largo = "x" * 600 + ' "$H/03 · Imagen/2026-05-26 - {{CENTRO}} - pet-galio68.pdf"'
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "assistant", "timestamp": "2026-09-15T09:25:17Z",
                                "message": {"content": [{"type": "tool_use", "name": "Bash",
                                                         "input": {"command": largo}}]}}) + "\n")
        docs = pj.documentos_leidos(ruta)
        self.assertEqual(docs, [("2026-09-15T09:25:17Z", "2026-05-26 - {{CENTRO}} - pet-galio68.pdf")])


class Banco(unittest.TestCase):
    def test_banco_versionado_sin_pii(self):
        import borde
        d = json.load(open(BANCO, encoding="utf-8"))
        casos = d["casos"]
        self.assertGreaterEqual(len(casos), 20)
        for c in casos:
            self.assertRegex(c["id"], r"^[0-9a-f]{8}@\d{4}-\d\d-\d\dT[\d:.]+Z$")
            self.assertIn(c["norma"], jn.NORMAS)
            self.assertIn(c["etiqueta"], ("incumple", "cumple", "no_aplica"))
            self.assertEqual(set(c), {"id", "norma", "etiqueta", "motivo", "particion"})
            self.assertIn(c["particion"], ("afinar", "reserva", "todo"))
            _t, n = borde.de_identificar(c["motivo"])
            self.assertEqual(n, 0, c["motivo"])
            self.assertIsNone(re.search(r"\d", c["motivo"]), c["motivo"])
        for k in ("acuerdo", "kappa", "n_etiquetados"):
            self.assertIn(k, d["medida"])
        # la reserva existe y está separada de lo que se usó para afinar el prompt
        res = [c for c in casos if c["norma"] == "cotejar_fuente" and c["particion"] == "reserva"]
        self.assertGreaterEqual(len(res), 10)
        self.assertEqual(d["version"], jn.rubricas()["version"])   # banco y rúbrica, misma versión

    def test_lecturas_previas_de_la_sesion_llegan_al_juez(self):
        c = _caso(lecturas_sesion_previas=["python3 tools/lector_clinico.py informe-pet.pdf"])
        self.assertIn("lector_clinico.py informe-pet.pdf", jn.prompt(c))
        self.assertIn("PAUTAS DE APLICACIÓN", jn.prompt(c))
        self.assertNotIn("TURNOS ANTERIORES", jn.prompt(_caso("calibrar")))
        self.assertNotIn("ESTA SESIÓN EMPEZÓ", jn.prompt(_caso("calibrar")))

    def test_calibrar_amplio_usa_senales_de_correccion(self):
        import prefiltro_juez as pj
        d = {"previa": "El marcador subió.", "pregunta": "no, eso está mal, corrígelo"}
        self.assertTrue(pj.calibrar_amplio(d))
        self.assertIsNone(pj.calibrar_amplio({"previa": "", "pregunta": "no, eso está mal"}))
        self.assertIsNone(pj.calibrar_amplio({"previa": "x", "pregunta": "vale, sigue"}))


if __name__ == "__main__":
    unittest.main(verbosity=1)
