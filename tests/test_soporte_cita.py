#!/usr/bin/env python3
"""test_soporte_cita.py — una cita que EXISTE pero no dice la cifra que se le atribuye se caza.

POR QUÉ EXISTE (24-sep-2026). `verifica_citas`, `tier_evidencia` y `fuente_clinica` dejaban pasar
«41 % de respuesta (PMID X)» si X existía, aunque X dijera 14 %. Deuda `cita-afirmacion-sin-soporte`.
Sin red: el abstract se inyecta. Cobertura medida con mutantes reales:
`python3 tools/mutantes.py tests/mutantes/soporte_cita.json` (BTP_SOPORTE apunta al mutante).
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import importlib.util  # noqa: E402

_RUTA = os.environ.get("BTP_SOPORTE") or os.path.join(ROOT, "tools", "soporte_cita.py")
_spec = importlib.util.spec_from_file_location("soporte_cita_bajo_prueba", _RUTA)
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)

ABSTRACT = ("Trastuzumab deruxtecan in previously treated HER2-low advanced breast cancer. "
            "In this phase 3 trial, 557 patients were randomized. Median progression-free survival "
            "was 9.9 months with T-DXd versus 5.1 months with chemotherapy (hazard ratio, 0.50). "
            "A total of 1,234 patients were screened.")


def fetch_fijo(abstract):
    return lambda cita: ("pmid", "35665782", abstract)


class Cotejo(unittest.TestCase):
    def test_cifra_real_respalda_literal(self):
        e, d = sc.cotejar("Con T-DXd la mediana de SLP fue de 9,9 meses frente a 5,1 (HR 0,50) "
                          "en HER2-low (PMID: 35665782)", ABSTRACT)
        self.assertEqual(e, sc.RESPALDA, d)

    def test_cifra_inventada_no_respalda(self):
        e, d = sc.cotejar("Con T-DXd la mediana de SLP fue de 12,9 meses en HER2-low", ABSTRACT)
        self.assertEqual(e, sc.NO_RESPALDA)
        self.assertEqual(d["faltan_numeros"], ["12.9"])

    def test_formatos_de_numero(self):
        # coma decimal, cero final, % separado y miles en español frente a inglés
        self.assertEqual(sc.cotejar("HR de 0,5; 1.234 cribadas", ABSTRACT)[0], sc.RESPALDA)
        self.assertEqual(sc.numeros("un 41 % y un 41% y 12,50"), ["41", "12.5"])

    def test_decimal_lancet_y_entidades_html(self):
        """PubMed devuelve «14&#xb7;4» crudo; el punto medio de Lancet es un decimal."""
        abs_lancet = "median 14&#xb7;4 months vs 11&#xb7;2 months; HR 0&#xb7;79; p&lt;0&#xb7;05"
        e, d = sc.cotejar("mediana de 14,4 frente a 11,2 meses (HR 0,79)", abs_lancet)
        self.assertEqual(e, sc.RESPALDA, d)

    def test_no_son_cifras(self):
        # siglas con dígito, la propia cita, fase y año no se cotejan como cifras
        self.assertEqual(sc.numeros("HER2 y CDK4/6, ensayo de fase 3 (PMID: 35665782) de 2022"), [])

    def test_identificadores_no_son_cifras(self):
        """Replay 24-sep: referencias bibliográficas, PMIDs pelados y códigos de fármaco."""
        self.assertEqual(sc.numeros("Mukohara et al., Nat Med 2024;30:2242-50"), [])
        self.assertEqual(sc.numeros("ver 41303519 y 21293939"), [])
        self.assertEqual(sc.numeros("KAT6i PF\u201107248144 + fulvestrant"), [])
        self.assertEqual(sc.numeros("el riesgo es ~0. 3. **Ginseng**"), ["0"])
        self.assertEqual(sc.numeros("fase 1, n = 86"), ["86"])

    def test_entidad_por_alias(self):
        e, _ = sc.cotejar("trastuzumab deruxtecan (T-DXd) en HER2-low", ABSTRACT)
        self.assertEqual(e, sc.RESPALDA)
        # el abstract solo trae la DCI; la frase, el código: casan por el alias, no por el literal
        e, d = sc.cotejar("T-DXd redujo el riesgo con HR 0,50", "trastuzumab deruxtecan, HR 0.50")
        self.assertEqual(e, sc.RESPALDA, d)

    def test_entidad_ausente_es_dudoso(self):
        e, d = sc.cotejar("PIK3CA mutado, mediana de 9,9 meses", ABSTRACT)
        self.assertEqual(e, sc.DUDOSO)
        self.assertIn("pik3ca", d["faltan_entidades"])

    def test_sin_cifras_ni_siglas_no_evaluable(self):
        self.assertEqual(sc.cotejar("el tratamiento fue bien tolerado", ABSTRACT)[0], sc.NO_EVALUABLE)

    def test_sin_abstract_no_evaluable(self):
        self.assertEqual(sc.cotejar("41 % de respuesta", "")[0], sc.NO_EVALUABLE)


class Red(unittest.TestCase):
    def test_registro_mudo_pendiente(self):
        r = sc.soporte("41 % de respuesta", "PMID:1", fetch=lambda c: ("pmid", "1", None))
        self.assertEqual(r["estado"], sc.PENDIENTE)

    def test_excepcion_pendiente(self):
        def boom(c):
            raise OSError("sin red")
        self.assertEqual(sc.soporte("41 %", "PMID:1", fetch=boom)["estado"], sc.PENDIENTE)

    def test_halt_pendiente_sin_tocar_la_red(self):
        d = tempfile.mkdtemp()
        halt = os.path.join(d, ".HALT")
        open(halt, "w").close()
        viejo = os.environ.get("BTP_HALT_FILES")
        os.environ["BTP_HALT_FILES"] = halt
        llamado = []
        real = sc.resolver
        sc.resolver = lambda c: llamado.append(c)
        try:
            r = sc.soporte("41 %", "PMID:1")
        finally:
            sc.resolver = real
            if viejo is None:
                os.environ.pop("BTP_HALT_FILES", None)
            else:
                os.environ["BTP_HALT_FILES"] = viejo
        self.assertEqual(r["estado"], sc.PENDIENTE)
        self.assertEqual(llamado, [], "con HALT no se consulta el registro")

    def test_lote_es_la_via_del_gate(self):
        """El gate de salida llama con --lote por stdin: un resultado por par, y rc 2 si alguno falla."""
        import io
        real, stdin, real_tc = sc.resolver, sys.stdin, sc.texto_completo_oa
        sc.resolver = lambda c: ("pmid", "1", ABSTRACT)
        sc.texto_completo_oa = lambda t, i: None
        os.environ["BTP_HALT_FILES"] = os.path.join(tempfile.mkdtemp(), "no-existe")
        sys.stdin = io.StringIO('[{"afirmacion": "mediana de 12,9 meses", "cita": "PMID:1"},'
                                ' {"afirmacion": "mediana de 9,9 meses", "cita": "PMID:1"}]')
        try:
            self.assertEqual(sc.main(["--lote"]), 2)
        finally:
            sc.resolver, sys.stdin, sc.texto_completo_oa = real, stdin, real_tc
            os.environ.pop("BTP_HALT_FILES", None)

    def test_doi_va_codificado_para_curl(self):
        """24-sep: `[doi]` sin codificar lo lee curl como glob y el DOI daba PENDIENTE siempre."""
        urls = []
        real = sc._curl
        sc._curl = lambda url, accept="application/json": (urls.append(url), (200, '{"esearchresult":{"idlist":[]}}'))[1]
        try:
            sc._doi_a_pmid("10.1056/NEJMoa2203690")
        finally:
            sc._curl = real
        self.assertNotIn("[", urls[0])
        self.assertIn("%5Bdoi%5D", urls[0])

    def test_texto_completo_oa_rescata_la_cifra(self):
        """Etiquetado 24-sep: 15 de 28 avisos eran cifras del texto completo, no del abstract."""
        tc = lambda tipo, ident: ("PMC1", "Results: grade 3 stomatitis occurred in 6.4% of patients.")
        r = sc.soporte("estomatitis de grado 3 en el 6,4 %", "PMID:1",
                       fetch=lambda c: ("pmid", "1", ABSTRACT), texto_completo=tc)
        self.assertEqual(r["estado"], sc.RESPALDA)
        self.assertIn("PMC1", r["cotejado_contra"])

    def test_texto_completo_sin_la_cifra_sigue_acusando(self):
        tc = lambda tipo, ident: ("PMC1", "Results: stomatitis was frequent.")
        r = sc.soporte("estomatitis de grado 3 en el 6,4 %", "PMID:1",
                       fetch=lambda c: ("pmid", "1", ABSTRACT), texto_completo=tc)
        self.assertEqual(r["estado"], sc.NO_RESPALDA)
        self.assertNotIn("presentes", r["motivo"], "el motivo no puede decir que la cifra está")

    def test_sin_oa_sigue_acusando(self):
        r = sc.soporte("estomatitis de grado 3 en el 6,4 %", "PMID:1",
                       fetch=lambda c: ("pmid", "1", ABSTRACT), texto_completo=lambda t, i: None)
        self.assertEqual(r["estado"], sc.NO_RESPALDA)

    def test_apartado_no_es_cifra(self):
        self.assertEqual(sc.numeros("ampliado en el apartado 7 de la nota"), [])

    def test_cli_sale_2_con_no_respalda(self):
        real, real_tc = sc.resolver, sc.texto_completo_oa
        sc.resolver = lambda c: ("pmid", "1", ABSTRACT)
        sc.texto_completo_oa = lambda t, i: None
        viejo = os.environ.pop("BTP_HALT_FILES", None)
        os.environ["BTP_HALT_FILES"] = os.path.join(tempfile.mkdtemp(), "no-existe")
        try:
            self.assertEqual(sc.main(["--json", "mediana de 12,9 meses", "PMID:1"]), 2)
            self.assertEqual(sc.main(["--json", "mediana de 9,9 meses", "PMID:1"]), 0)
        finally:
            sc.resolver, sc.texto_completo_oa = real, real_tc
            os.environ.pop("BTP_HALT_FILES", None)
            if viejo is not None:
                os.environ["BTP_HALT_FILES"] = viejo


if __name__ == "__main__":
    unittest.main(verbosity=1)
