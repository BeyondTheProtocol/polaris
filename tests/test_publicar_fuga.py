#!/usr/bin/env python3
"""test_publicar_fuga.py — las tres grietas por las que se escapó un nombre al repo público.

19-sep-2026, fuga REAL (`BeyondTheProtocol/polaris`, público desde el 17-sep): el fichero
`pipeline/bin/muro.py` salió con el nombre propio y un término vetado escritos dentro de sus
propias reglas, `re.compile(r"\\bNombre\\b")`. Tres fallos encadenados, uno por test:

  1. La sustitución no casaba después de `\\b`: el carácter anterior es la `b`, una letra,
     así que el lookbehind `(?<![a-z0-9])` la bloqueaba. El sitio donde un veto ESCRIBE el
     término es justo el que hay que limpiar.
  2. El barrido final usaba el mismo lookbehind, así que tampoco lo veía: el fail-closed
     daba luz verde sobre un árbol con el nombre dentro.
  3. Sin overlay (`perfil.local.json`), no hay ni sustituciones ni vetados: el árbol sale
     crudo y el barrido lo aprueba porque no le queda nada que buscar. Publicar desde un
     worktree, que no hereda los gitignored, caía justo ahí.
"""
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import publicar  # noqa: E402

# La línea exacta que se publicó, con un nombre de pega.
LINEA = '    re.compile(r"\\bZoraida\\b", re.IGNORECASE),        # nombre'


def _overlay(dirtmp, nombre="Zoraida", sustituciones=None, nombres=None):
    os.makedirs(os.path.join(dirtmp, "tools"), exist_ok=True)
    with io.open(os.path.join(dirtmp, "tools", "perfil.local.json"), "w", encoding="utf-8") as fh:
        json.dump({"titular": {"nombre": nombre, "apellidos": ["Pergamino"], "nacimiento": [], "contactos": []},
                   "sustituciones": sustituciones if sustituciones is not None else [[r"\bVetada\b", "{{X}}"]],
                   "bloques": []}, fh)
    with io.open(os.path.join(dirtmp, "tools", "nombres.local.json"), "w", encoding="utf-8") as fh:
        json.dump({"nombres": nombres if nombres is not None else ["Zenobia"]}, fh)


class TestFugaEnPatron(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _overlay(self.tmp)
        self._root = publicar.ROOT
        publicar.ROOT = self.tmp
        self._recargar()

    def tearDown(self):
        publicar.ROOT = self._root
        self._recargar()

    def _recargar(self):
        """SUSTITUCIONES se construye al importar: hay que rehacerla con el ROOT de turno."""
        publicar._NOMBRE = publicar._nombre_titular()
        publicar.SUSTITUCIONES = (
            (publicar.re.compile(r"voz-%s" % publicar._NOMBRE, publicar.re.I), "voz-titular"),
            (publicar.re.compile(r"%s[a-z0-9]*" % publicar._NOMBRE, publicar.re.I),
             publicar._por_caso),
        ) + tuple(
            (publicar.re.compile(r"%s%s,?\s*" % (publicar._TRAS, publicar.re.escape(a)), publicar.re.I), "")
            for a in publicar._titular("apellidos")
        ) + tuple(
            (publicar.re.compile(r"(?:(?<=\\b)|\b|(?<=_))%s(?=[A-Z_]|\b)" % publicar.re.escape(n), publicar.re.I),
             publicar._contacto)
            for n in publicar._nombres_de_terceros()
        )

    def test_el_nombre_dentro_de_un_patron_se_sustituye(self):
        salida = publicar.despersonalizar(LINEA)
        self.assertNotIn("Zoraida", salida)
        # pegado a la `b` de `\b` va sin llaves: `{{TITULAR}}` dentro de un identificador
        # no compila, y el árbol público tiene que seguir corriendo
        self.assertIn("Titular", salida)

    def test_el_nombre_pegado_dentro_de_una_palabra_tambien(self):
        """`HelpZoraida/Prensa`, `helpzoraida.com`, `RealZoraidaBot`: el nombre se lee igual."""
        for antes, espera in (("HelpZoraida/Prensa", "HelpTitular/Prensa"),
                              ("helpzoraida.com", "helptitular.com"),
                              ("NoPuedenEscribirleAZoraida", "NoPuedenEscribirleATitular")):
            self.assertEqual(publicar.despersonalizar(antes), espera)

    def test_el_nombre_suelto_sigue_llevando_llaves(self):
        self.assertEqual(publicar.despersonalizar("el OK de Zoraida manda"),
                         "el OK de {{TITULAR}} manda")

    def test_el_barrido_lo_ve_si_sobrevive(self):
        self.assertEqual(publicar.vetados_en(LINEA), ["Zoraida"])

    def test_tercero_dentro_de_un_patron_tambien(self):
        linea = '    re.compile(r"\\bZenobia\\b"),'
        self.assertNotIn("Zenobia", publicar.despersonalizar(linea))

    def test_texto_normal_sigue_igual(self):
        self.assertEqual(publicar.despersonalizar("nada que ver aquí"), "nada que ver aquí")


class TestCasoExplicito(TestFugaEnPatron):
    """Un fichero puede contar el CASO a propósito; la IDENTIDAD no se negocia nunca.

    19-sep-2026: pedir ayuda pública sobre qué modelo usar exige contar para qué caso es
    («tan genérico no sirve»). El marcador levanta solo el perfil clínico, y solo en el
    fichero que lo lleva."""

    MARCADO = ("<!-- publicar: caso-explicito -->\n"
               "el caso es una enfermedad Vetada, y lo firma Zoraida Pergamino")

    def test_con_marcador_el_perfil_clinico_sobrevive(self):
        salida = publicar.despersonalizar(self.MARCADO, con_perfil=not publicar.caso_explicito(self.MARCADO))
        self.assertIn("Vetada", salida)          # el perfil clínico se queda
        self.assertNotIn("Zoraida", salida)      # la identidad, no

    def test_sin_marcador_el_perfil_clinico_desaparece(self):
        sin = "el caso es una enfermedad Vetada"
        self.assertNotIn("Vetada", publicar.despersonalizar(sin))

    def test_el_barrido_no_denuncia_el_perfil_del_fichero_marcado(self):
        limpio = publicar.despersonalizar(self.MARCADO, con_perfil=False)
        self.assertEqual(publicar.vetados_en(limpio, con_perfil=False), [])
        # pero sí lo denunciaría en un fichero normal
        self.assertTrue(publicar.vetados_en("una enfermedad Vetada"))


class TestSinOverlayNoSePublica(unittest.TestCase):
    def test_sin_overlay_aborta_en_vez_de_publicar_en_crudo(self):
        vacio, destino = tempfile.mkdtemp(), tempfile.mkdtemp()
        root = publicar.ROOT
        publicar.ROOT = vacio
        try:
            self.assertTrue(publicar._overlay_ok())          # faltan los tres
            self.assertEqual(publicar.publicar(destino, forzar=True), 3)
        finally:
            publicar.ROOT = root

    def test_con_overlay_completo_no_se_queja(self):
        lleno = tempfile.mkdtemp()
        _overlay(lleno)
        root = publicar.ROOT
        publicar.ROOT = lleno
        try:
            self.assertEqual(publicar._overlay_ok(), [])
        finally:
            publicar.ROOT = root


class TestMuroDelPipeline(unittest.TestCase):
    """El muro del pipeline no puede llevar escritos los términos que veta."""

    def test_el_codigo_versionado_no_nombra_a_nadie(self):
        ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "pipeline", "bin", "muro.py")
        with io.open(ruta, encoding="utf-8") as fh:
            fuente = fh.read()
        # los términos viven en el overlay gitignored, no aquí
        self.assertIn("vetados.local.json", fuente)
        self.assertNotIn('re.compile(r"\\bTitular', fuente)
        self.assertNotIn("{{CONTACTO}}", fuente)

    def test_el_overlay_carga_y_bloquea(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                        "pipeline", "bin"))
        import muro  # noqa: E402
        tmp = tempfile.mkdtemp()
        ruta = os.path.join(tmp, "vetados.local.json")
        with io.open(ruta, "w", encoding="utf-8") as fh:
            json.dump({"terminos": ["Zoraida"]}, fh)
        pats = muro._identificativos(ruta)
        self.assertTrue(any(p.search("un texto con Zoraida dentro") for p in pats))
        self.assertEqual(muro._identificativos(os.path.join(tmp, "no-existe.json")), [])
        # y lo estructural sigue en pie sin overlay
        self.assertTrue(any(p.search("ACGTACGTACGTACGT") for p in muro._PROHIBIDO_ESTRUCTURAL))


if __name__ == "__main__":
    unittest.main()
