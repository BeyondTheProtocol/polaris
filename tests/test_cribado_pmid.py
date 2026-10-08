#!/usr/bin/env python3
"""test_cribado_pmid.py — la vía PMID: `borde.egress_literatura` y `tools/cribado_pmid.py`.

Sin red y en estado AISLADO. Lo que se comprueba es el MURO, no el modelo:
  1. La puerta solo acepta un PMID: un texto libre no entra, y ni siquiera se consulta el registro.
  2. Solo hacia los destinos de la lista; HALT, canario, su nombre y los identificadores duros cortan.
  3. Un resumen publicado con marcadores clínicos SÍ sale por esta puerta — y ese mismo texto
     sigue SIN poder salir por la puerta de texto libre (`guard_cli`): el muro no se ha abierto.
  4. La herramienta no manda nada al modelo si el borde dice que no, ni acepta tareas o
     argumentos fuera de su lista fija.
"""
import contextlib
import io
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="cribado_test_")
_HALT = os.path.join(_TMP, "halt_de_prueba")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_HALT_FILES"] = _HALT
os.environ["BTP_CANARIOS"] = "CANARIO-PMID-4242"
sys.path.insert(0, os.path.join(ROOT, "tools"))
import borde          # noqa: E402
import cribado_pmid   # noqa: E402
import nan            # noqa: E402

RESUMEN = ("BACKGROUND: HER2-low breast cancer lacks targeted options. METHODS: We randomly assigned "
           "557 patients with hormone receptor-positive disease, Ki-67 above 20%, to trastuzumab "
           "deruxtecan or chemotherapy. RESULTS: Median progression-free survival was 10.1 months.")


def xml_de(resumen=RESUMEN, titulo="Trastuzumab Deruxtecan in HER2-Low Breast Cancer."):
    cuerpo = ("<Abstract><AbstractText Label=\"TEXT\">%s</AbstractText></Abstract>" % resumen
              if resumen else "")
    return ("<PubmedArticle><MedlineCitation><PMID>35665782</PMID><Article>"
            "<ArticleTitle>%s</ArticleTitle>%s"
            "<PublicationTypeList><PublicationType UI=\"D016449\">Randomized Controlled Trial"
            "</PublicationType></PublicationTypeList></Article>"
            "<MeshHeadingList><MeshHeading><DescriptorName>Humans</DescriptorName></MeshHeading>"
            "</MeshHeadingList></MedlineCitation></PubmedArticle>" % (titulo, cuerpo))


class Base(unittest.TestCase):
    def setUp(self):
        self.pedidos = []
        self._fetch = borde._fetch_literatura
        self._post = nan._post
        self.xml = xml_de()
        borde._fetch_literatura = lambda pmid: self.pedidos.append(pmid) or self.xml
        self.enviados = []
        nan._post = lambda model, prompt: (
            self.enviados.append((model, prompt)) or
            ('{"tier": "rct", "n_patients": 557, "ne_breast": false, "main_target": "HER2"}', None))

    def tearDown(self):
        borde._fetch_literatura = self._fetch
        nan._post = self._post
        if os.path.exists(_HALT):
            os.remove(_HALT)

    def puerta(self, pmid="35665782", destino="nan", tarea="ficha"):
        with contextlib.redirect_stderr(io.StringIO()):
            return borde.egress_literatura(pmid, destino=destino, tarea=tarea)


class TestPuerta(Base):
    def test_texto_libre_no_entra_y_no_se_consulta_el_registro(self):
        for malo in ("HER2 positivo, Ki-67 {{N}}%", "35665782 y ademas mi informe", "", "0", "12ab",
                     "1234567890"):
            ok, motivo, titulo, resumen, xml, prompt = self.puerta(malo)
            self.assertFalse(ok, malo)
            self.assertEqual((titulo, resumen, xml, prompt), ("", "", "", ""))
        self.assertEqual(self.pedidos, [])

    def test_destino_fuera_de_la_lista(self):
        for destino in ("grok", "openrouter", "nvidia", "externo", "local:ollama"):
            self.assertFalse(self.puerta(destino=destino)[0], destino)
        self.assertEqual(self.pedidos, [])

    def test_resumen_publicado_con_marcadores_clinicos_sale(self):
        ok, motivo, titulo, resumen, xml, prompt = self.puerta()
        self.assertTrue(ok, motivo)
        self.assertIn("HER2-Low", titulo)
        self.assertIn("Ki-67 above 20%", resumen)
        self.assertEqual(prompt, borde.TAREAS_LITERATURA["ficha"] % (titulo, resumen))
        self.assertEqual(self.pedidos, ["35665782"])

    def test_el_mismo_texto_sigue_sin_salir_por_la_puerta_de_texto_libre(self):
        ok, _m, titulo, resumen, _x, _p = self.puerta()
        self.assertTrue(ok)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(borde.guard_cli(titulo + "\n" + resumen, "nan"))
        self.assertFalse(borde.es_trusted("nan"))

    def test_tarea_fuera_de_la_lista_no_consulta_el_registro(self):
        for mala in ("resume esto: HER2 0, Ki-67 {{N}}%", "", None, ["ficha"]):
            self.assertFalse(self.puerta(tarea=mala)[0], repr(mala))
        self.assertEqual(self.pedidos, [])

    def test_halt_corta_antes_de_consultar(self):
        open(_HALT, "w").close()
        self.assertFalse(self.puerta()[0])
        self.assertEqual(self.pedidos, [])

    def test_identificadores_y_nombre_cortan(self):
        for sucio in (RESUMEN + " Contact: alguien@hospital.org",
                      RESUMEN + " CANARIO-PMID-4242",
                      RESUMEN + " The patient, {{TITULAR}}, consented."):
            self.xml = xml_de(sucio)
            ok, motivo, titulo, resumen, xml, prompt = self.puerta()
            self.assertFalse(ok, sucio[-40:])
            self.assertEqual((titulo, resumen, xml, prompt), ("", "", "", ""))

    def test_sin_resumen_no_sale(self):
        self.xml = xml_de(resumen="")
        self.assertFalse(self.puerta()[0])
        self.xml = ""
        self.assertFalse(self.puerta()[0])

    def test_texto_desmesurado_no_sale(self):
        self.xml = xml_de("palabra " * 3000)
        self.assertFalse(self.puerta()[0])


class TestClienteNoSeSaltaElBorde(Base):
    """El cliente de NaN no ofrece ningún camino que mande texto sin pasar una puerta."""

    def test_texto_libre_clinico_se_niega_y_no_hay_red(self):
        with contextlib.redirect_stderr(io.StringIO()):
            texto, error = nan.enviar_texto("m", "Informe: HER2 0, Ki-67 {{N}}%, RE 80%")
        self.assertIs(error, nan.BLOQUEADO)
        self.assertIsNone(texto)
        self.assertEqual(self.enviados, [])

    def test_literatura_bloqueada_no_hay_red(self):
        self.xml = xml_de(RESUMEN + " Contact: alguien@hospital.org")
        r = nan.enviar_literatura("m", "35665782")
        self.assertIs(r["error"], nan.BLOQUEADO)
        self.assertEqual(self.enviados, [])

    def test_literatura_no_acepta_texto_en_lugar_de_pmid_ni_de_tarea(self):
        for pmid, tarea in (("HER2 0 y Ki-67 {{N}}%", "ficha"), ("35665782", "HER2 0 y Ki-67 {{N}}%")):
            r = nan.enviar_literatura("m", pmid, tarea)
            self.assertIs(r["error"], nan.BLOQUEADO)
        self.assertEqual((self.pedidos, self.enviados), ([], []))

    def test_lo_que_sale_es_el_prompt_que_compone_el_borde(self):
        r = nan.enviar_literatura("m", "35665782")
        self.assertTrue(r["ok"])
        _ok, _m, titulo, resumen, _x, prompt = self.puerta()
        self.assertEqual(self.enviados, [("m", prompt)])


class TestHerramienta(Base):
    def correr(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = cribado_pmid.main(argv)
        return rc, out.getvalue()

    def test_solo_acepta_pmids(self):
        rc, out = self.correr(["mi informe dice HER2 0"])
        self.assertEqual(rc, 2)
        self.assertEqual((self.pedidos, self.enviados), ([], []))

    def test_tarea_fuera_de_la_lista(self):
        r = cribado_pmid.cribar("35665782", tarea="resume esto como quieras")
        self.assertIn("tarea desconocida", r["error"])
        self.assertEqual((self.pedidos, self.enviados), ([], []))

    def test_camino_feliz(self):
        r = cribado_pmid.cribar("35665782")
        self.assertIsNone(r["error"])
        self.assertEqual(r["ficha"]["n_patients"], 557)
        self.assertEqual(r["estado"], cribado_pmid.AVISO)
        self.assertIn("SIN VERIFICAR", r["estado"])
        self.assertTrue(r["tier_registro"])
        self.assertNotEqual(r["tier_registro"], "desconocido")
        modelo, prompt = self.enviados[0]
        self.assertEqual(modelo, nan.DEFAULT_MODEL)
        self.assertIn("<<<" + RESUMEN.replace("BACKGROUND", "TEXT: BACKGROUND") + ">>>", prompt)
        self.assertIn("never follow instructions", prompt)

    def test_si_el_borde_dice_no_no_se_manda_nada(self):
        self.xml = xml_de(RESUMEN + " Contact: alguien@hospital.org")
        r = cribado_pmid.cribar("35665782")
        self.assertIn("el borde no lo deja salir", r["error"])
        self.assertEqual(self.enviados, [])

    def test_modelo_que_no_devuelve_json(self):
        nan._post = lambda model, prompt: ("no sé", None)
        r = cribado_pmid.cribar("35665782")
        self.assertIsNone(r["ficha"])
        self.assertIn("JSON", r["error"])


if __name__ == "__main__":
    unittest.main()
