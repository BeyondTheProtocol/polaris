#!/usr/bin/env python3
"""test_gate_citas.py — una cita inventada no puede llegar a {{TITULAR}}.

POR QUÉ EXISTE (31-jul-2026). El gate de salida revisaba estilo y certeza, pero no comprobaba
si una referencia EXISTE. Hueco demostrado: una respuesta con `PMID: 99999999` y
`doi 10.9999/inventado.2026.0001` sosteniendo un 41% de respuesta objetiva en carcinoma
metaplásico salió del gate con **exit 0, cero hallazgos**. `tools/verifica_citas.py` ya sabía
cazar eso, pero solo lo llamaban el agente `verificacion` y tres tools: cualquier otra vía
llegaba a ella sin filtro.

Este test fija las cuatro propiedades del check, sin tocar la red (subprocess mockeado):
  1. Una cita que NO existe se caza.
  2. Una cita que existe pasa.
  3. Red caída / API muda (`no_resoluble`) NO acusa ni bloquea — la cita puede existir — pero
     tampoco calla: sale PENDIENTE, «sin verificar» (25-sep-26; antes era fail-open silencioso).
  4. Bloquea aunque el gate global esté en modo `aviso` (`SIEMPRE_BLOQUEA`).
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gate_salida as g  # noqa: E402

# Respuesta larga (supera MIN_CHARS) con una referencia dentro.
CON_PMID = ("Sobre la vía que preguntabas, el ensayo de fase II (PMID: 99999999) describe una "
            "respuesta objetiva del 41% en carcinoma metaplásico metastásico, así que puede "
            "valer la pena llevárselo a tu oncóloga en la próxima visita y preguntarle por ello.")
CON_DOI = ("Te dejo la referencia del trabajo de perfilado genómico (doi 10.9999/inventado.2026.0001) "
           "que sostiene lo que te acabo de contar sobre las alteraciones de CDKN2A, para que lo "
           "puedas mirar con calma antes de la consulta del mes que viene con el equipo.")
LIMPIO = ("He fusionado la rama y la suite está en verde: 211 comprobaciones del muro sin fallos. "
          "Te dejo el enlace del informe en la fuente de verdad para que lo mires cuando quieras, "
          "y el resto sigue igual que esta mañana sin nada nuevo que mirar.")


class _Resp:
    def __init__(self, out):
        self.stdout = out
        self.returncode = 0


def _mock(estado):
    """Sustituye la llamada real a verifica_citas.py por un estado fijo."""
    def run(cmd, **kw):
        ids = [a for a in cmd[3:]]
        return _Resp(json.dumps([{"id": i, "tipo": "x", "estado": estado,
                                  "fuente": "test", "detalle": ""} for i in ids]))
    return run


class GateCitas(unittest.TestCase):
    def setUp(self):
        self._real = g.subprocess.run
        # el check sale antes si no encuentra el verificador: en el repo existe, pero lo fijamos
        self.assertTrue(os.path.exists(os.path.join(g.REPO, "tools", "verifica_citas.py")),
                        "falta tools/verifica_citas.py: el check quedaría inerte")

    def tearDown(self):
        g.subprocess.run = self._real

    def test_pmid_inventado_se_caza(self):
        g.subprocess.run = _mock("no_existe")
        self.assertIsNotNone(g.citas_fabricadas(CON_PMID))

    def test_doi_inventado_se_caza(self):
        g.subprocess.run = _mock("no_existe")
        self.assertIsNotNone(g.citas_fabricadas(CON_DOI))

    def test_cita_real_pasa(self):
        g.subprocess.run = _mock("existe")
        self.assertIsNone(g.citas_fabricadas(CON_PMID))

    def test_red_caida_no_acusa_pero_avisa(self):
        g.subprocess.run = _mock("no_resoluble")
        m = g.citas_fabricadas(CON_PMID)
        self.assertTrue(m and m.startswith(g.PENDIENTE), m)
        self.assertIn("SIN VERIFICAR", m)
        self.assertEqual(g._bloquean([("citas_fabricadas", "s", m)], "aviso"), [])

    def test_sin_ids_no_toca_la_red(self):
        def boom(*a, **k):
            raise AssertionError("no debe llamar a verifica_citas sin ids en el texto")
        g.subprocess.run = boom
        self.assertIsNone(g.citas_fabricadas(LIMPIO))

    def test_excepcion_avisa_sin_bloquear(self):
        def boom(*a, **k):
            raise OSError("red caída")
        g.subprocess.run = boom
        m = g.citas_fabricadas(CON_PMID)
        self.assertTrue(m and m.startswith(g.PENDIENTE), m)
        self.assertEqual(g._bloquean([("citas_fabricadas", "s", m)], "aviso"), [])

    def test_extraccion_de_ids(self):
        ids = g._ids_cita("PMID: 39538331 y 10.1186/s13073-024-01388-3 y NCT07112053 y arXiv: 2402.10588")
        self.assertIn("PMID:39538331", ids)
        self.assertIn("10.1186/s13073-024-01388-3", ids)
        self.assertIn("NCT07112053", ids)
        self.assertIn("arXiv:2402.10588", ids)

    def test_tope_de_ids(self):
        muchos = " ".join("PMID: %d" % (10000000 + i) for i in range(20))
        self.assertLessEqual(len(g._ids_cita(muchos)), g.MAX_IDS_CITA)

    def test_bloquea_aunque_el_modo_sea_aviso(self):
        self.assertIn("citas_fabricadas", g.SIEMPRE_BLOQUEA)

    def test_check_registrado_como_norma(self):
        """Sin norma en tools/normas.json el check no se activa: quedaría decorativo."""
        import normas
        checks = [r["check"] for r in normas.reglas_salida()]
        self.assertIn("citas_fabricadas", checks)


class ModosPorNorma(unittest.TestCase):
    """31-jul-26: cada norma puede llevar su `modo`, que manda sobre el interruptor global."""

    def test_citas_bloquea_con_gate_en_aviso(self):
        self.assertEqual(g._bloquean([("citas_fabricadas", "s", "m")], "aviso"),
                         ["citas_fabricadas"])

    def test_falsa_certeza_bloquea_con_gate_en_aviso(self):
        self.assertEqual(g._bloquean([("falsa_certeza", "s", "m")], "aviso"), ["falsa_certeza"])

    def test_convergencia_sigue_avisando(self):
        self.assertEqual(g._bloquean([("convergencia", "s", "m")], "aviso"), [])

    def test_el_global_sigue_mandando_sobre_las_que_no_tienen_modo(self):
        self.assertEqual(g._bloquean([("convergencia", "s", "m")], "bloqueo"), ["convergencia"])

    def test_sin_checks_duplicados(self):
        """Dos normas pueden compartir mecanismo; el check NO puede correr dos veces."""
        activas, _ = g._reglas_activas()
        checks = [c for c, _s, _m in activas]
        self.assertEqual(len(checks), len(set(checks)), "check duplicado: %s" % checks)

    def test_mecanismo_compartido_sigue_siendo_legitimo(self):
        """El dedup NO debe tentar a nadie a borrar una de las dos normas del registro."""
        import normas
        mecs = [n.get("mecanismo") for n in normas.cargar()["normas"]]
        self.assertGreaterEqual(mecs.count(".claude/hooks/gate_salida.py::coste_no_bloquea"), 2)



# ── cita_no_respalda (24-sep-26): la cita existe, pero ¿dice la cifra que le atribuyo? ──────────
ABSTRACT_14 = "Objective response rate was 14% (95% CI, 8 to 22) in the HER2-low cohort of 120 patients."
ATRIBUYE_41 = ("Sobre lo que preguntabas del anticuerpo conjugado, la tasa de respuesta objetiva fue "
               "del 41 % en la cohorte HER2-low (PMID: 12345678), así que podría merecer la pena "
               "llevarlo a la consulta con tu oncóloga.")
ATRIBUYE_14 = ATRIBUYE_41.replace("41 %", "14 %")


def _mock_soporte(abstract):
    """Sustituye el subproceso de soporte_cita por el cotejo REAL contra un abstract fijo."""
    import soporte_cita as sc

    def run(cmd, **kw):
        pares = json.loads(kw.get("input") or "[]")
        res = [dict(sc.soporte(p["afirmacion"], p["cita"], fetch=lambda c: ("pmid", c, abstract)),
                    afirmacion=p["afirmacion"]) for p in pares]
        return _Resp(json.dumps(res))
    return run


class CitaNoRespalda(unittest.TestCase):
    def setUp(self):
        self._real = g.subprocess.run

    def tearDown(self):
        g.subprocess.run = self._real

    def test_cifra_que_el_abstract_no_dice_se_avisa(self):
        g.subprocess.run = _mock_soporte(ABSTRACT_14)
        m = g.cita_no_respalda(ATRIBUYE_41)
        self.assertIsNotNone(m)
        self.assertIn("41", m)

    def test_cifra_correcta_calla(self):
        g.subprocess.run = _mock_soporte(ABSTRACT_14)
        self.assertIsNone(g.cita_no_respalda(ATRIBUYE_14))

    def test_registro_mudo_avisa_sin_bloquear(self):
        """25-sep-26: antes callaba, que era dar la cifra por cotejada."""
        g.subprocess.run = _mock_soporte(None)
        m = g.cita_no_respalda(ATRIBUYE_41)
        self.assertTrue(m and m.startswith(g.PENDIENTE), m)
        self.assertEqual(g._bloquean([("cita_no_respalda", "s", m)], "bloqueo"), [])

    def test_halt_calla(self):
        """HALT es una parada deliberada de {{TITULAR}}, no un fallo de red: no se avisa en cada turno."""
        g.subprocess.run = lambda cmd, **kw: _Resp(json.dumps([
            {"estado": "PENDIENTE", "id": "12345678", "motivo": "HALT activo: no se consulta"}]))
        self.assertIsNone(g.cita_no_respalda(ATRIBUYE_41))

    def test_sin_cita_no_consulta(self):
        llamado = []
        g.subprocess.run = lambda *a, **k: llamado.append(1)
        self.assertIsNone(g.cita_no_respalda(LIMPIO))
        self.assertEqual(llamado, [])

    def test_varias_citas_en_el_parrafo_no_adivina(self):
        g.subprocess.run = _mock_soporte(ABSTRACT_14)
        texto = ("La respuesta fue del 41 %. Lo cuentan dos trabajos (PMID: 12345678) y "
                 "(PMID: 23456789) con poblaciones distintas que conviene no mezclar.")
        # la frase con la cifra no trae cita y el párrafo trae dos: no se atribuye a ninguna
        self.assertIsNone(g.cita_no_respalda(texto))

    def test_nct_de_etiqueta_en_texto_operativo_calla(self):
        """Replay 24-sep: «Moffitt (NCT…): borrador con el PDF de 75 págs» no es un resultado."""
        g.subprocess.run = _mock_soporte(ABSTRACT_14)
        texto = ("Te lo dejo a un clic: el borrador para Moffitt (NCT06691035) lleva el PDF de 75 "
                 "páginas y la RM del 19-jun, y lo revisas tú antes de mandarlo mañana a las 10:30.")
        self.assertIsNone(g.cita_no_respalda(texto))

    def test_porcentaje_de_una_url_no_es_resultado(self):
        """Etiquetado 24-sep: el %20 de un enlace hacía pasar «Apartado 7» por una cifra de resultado."""
        g.subprocess.run = _mock_soporte(ABSTRACT_14)
        texto = ("**Dónde quedó archivado** · Apartado 7 de [Notas/fiebre](00_FUENTE-DE-VERDAD/04%20·%20IA"
                 "/Notas/fiebre-nct07222267.md) sobre NCT07222267, con el resto de lo que dice cada fuente.")
        self.assertIsNone(g.cita_no_respalda(texto))

    def test_dato_suyo_junto_a_la_cita_calla(self):
        g.subprocess.run = _mock_soporte(ABSTRACT_14)
        texto = ("Tu Ki-67 del 30 % encaja con la cohorte del trabajo (PMID: 12345678), que es de "
                 "donde sale la comparación que te comentaba antes sobre la proliferación.")
        self.assertIsNone(g.cita_no_respalda(texto))

    def test_nace_en_aviso_no_bloquea(self):
        """Modo aviso en normas.json: hasta medir falsos positivos, no frena la respuesta."""
        self.assertNotIn("cita_no_respalda", g.SIEMPRE_BLOQUEA)
        self.assertEqual(g._bloquean([("cita_no_respalda", "s", "m")], "aviso"), [])

    def test_esta_registrado_en_normas(self):
        activas, _ = g._reglas_activas()
        self.assertIn("cita_no_respalda", [c for c, _s, _m in activas])


if __name__ == "__main__":
    unittest.main(verbosity=2)
