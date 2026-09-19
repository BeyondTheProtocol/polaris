#!/usr/bin/env python3
"""Tests de La Anatomía (tools/anatomia.py).

Lo que se protege aquí:
  · que el inventario cuadre con lo que hay de verdad en el disco
  · que la CARA PÚBLICA no emita léxico vetado ni PII (el muro, fail-closed)
  · que el castellano salga bien (concordancia y miles) — es una pieza que se ve
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("contenido", "estado", "nombres")

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import anatomia  # noqa: E402


class TestInventario(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.inv = anatomia.inventario()

    def test_cuenta_los_comites_que_hay_en_disco(self):
        reales = len([f for f in os.listdir(anatomia.AGENTS_DIR)
                      if f.endswith(".md")])
        self.assertEqual(self.inv["numeros"]["comites"], reales)
        self.assertGreater(reales, 0)

    def test_cada_comite_trae_nombre(self):
        for c in self.inv["comites"]:
            self.assertTrue(c["nombre"], "un comité sin nombre: %r" % c)

    def test_toda_tool_cae_en_una_familia(self):
        """Si 'otras' se dispara es que falta una familia: la vista miente menos
        cuanto menos cajón de sastre tiene."""
        otras = self.inv["herramientas"]["familias"].get("otras", [])
        self.assertLessEqual(
            len(otras), 5, "demasiadas tools sin familia: %s" % otras)

    def test_los_numeros_son_coherentes(self):
        n = self.inv["numeros"]
        self.assertEqual(n["tools"],
                         sum(len(v) for v in self.inv["herramientas"]["familias"].values()))
        self.assertLessEqual(n["rutinas_cargadas"], n["rutinas_total"])
        self.assertEqual(n["skills"], len(self.inv["skills"]))

    def test_detecta_una_skill_que_no_carga(self):
        """Una carpeta de skill sin SKILL.md existe pero NO la carga Claude.
        La Anatomía tiene que verlo, que para eso está."""
        rotas = [s["nombre"] for s in self.inv["skills"] if not s["carga"]]
        for n in rotas:
            self.assertFalse(
                os.path.isfile(os.path.join(anatomia.SKILLS_DIR, n, "SKILL.md")))
        self.assertEqual(self.inv["numeros"]["skills_rotas"], len(rotas))


class TestPulso(unittest.TestCase):

    def test_no_revienta_sin_trazas(self):
        p = anatomia.pulso()
        for k in ("ahora", "agentes", "despiertos", "con_traza", "sin_traza"):
            self.assertIn(k, p)

    def test_despiertos_son_solo_comites(self):
        """vigia y healthcheck salen en las trazas pero NO son comités: si se
        cuelan, el titular dice 'N comités despiertos' de más."""
        p = anatomia.pulso()
        nombres = {c["nombre"] for c in anatomia.comites()}
        for a in p["despiertos"]:
            self.assertIn(a, nombres)
        for a in p["fontaneria"]:
            self.assertNotIn(a, nombres)


class TestMuro(unittest.TestCase):
    """La cara pública es OUTWARD: pasa por el mismo scrubber que la boca."""

    def test_limpia_lexico_vetado(self):
        for malo in ("el neoantígeno elegido", "su oncólogo de Zúrich",
                     "{{CONTACTO}}", "es ingeniera"):
            self.assertEqual(anatomia.limpiar(malo, "publica"), "(reservado)")

    def test_vacuna_ya_es_publica(self):
        # {{TITULAR}} levantó el veto de «vacuna» el 29-7-26 (.claude/rules/marca-copy.md).
        # Se carga el _lexico_publico de ESTE árbol por ruta: anatomia importa el de casa base
        # a propósito (retrata el sistema vivo), y desde un worktree mediría la versión vieja.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_lexico_publico_arbol", os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "tools", "_lexico_publico.py"))
        lx = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lx)
        self.assertEqual(lx.revisar("la vacuna personalizada"), [])
        self.assertTrue(lx.revisar("el neoantígeno elegido"))

    def test_limpia_telefono(self):
        self.assertEqual(
            anatomia.limpiar("teléfono 600 12 34 56", "publica"), "(reservado)")

    def test_deja_pasar_lo_inocuo(self):
        self.assertEqual(anatomia.limpiar("reparte el trabajo", "publica"),
                         "reparte el trabajo")

    def test_la_cara_privada_no_toca_nada(self):
        self.assertEqual(anatomia.limpiar("oncólogo", "privada"), "oncólogo")

    def test_el_html_publico_no_lleva_lexico_vetado(self):
        import _lexico_publico
        html = anatomia.render(cara="publica")
        # El texto visible, sin etiquetas: es lo que ve quien mire el vídeo.
        visible = re.sub(r"<[^>]+>", " ", html)
        visible = visible.split("La Anatomía", 1)[-1]
        avisos = _lexico_publico.revisar(visible)
        self.assertEqual(avisos, [], "fuga en la cara pública: %s" % avisos)

    def test_la_cara_publica_no_lista_nombres_de_rutinas(self):
        """El nombre de un daemon no le dice nada a quien mira y sí cuenta de
        más sobre la vida digital de {{TITULAR}}."""
        html = anatomia.render(cara="publica")
        for delator in ("wa-tracker", "x-guardados", "correo-imap",
                        "observatorio-kiosk"):
            self.assertNotIn(">%s<" % delator, html)

    def test_la_cara_publica_no_lista_los_nombres_de_las_cajas(self):
        """Una caja se llama como el trozo de vida al que sirve
        («viaje-{{CIUDAD}}-prueba»): ahí no hay nada seguro por defecto. Y el
        scrubber NO basta — no veta «biopsia» a secas, solo junto a un patrón de
        variante, así que se coló en la primera versión."""
        inv = anatomia.inventario()
        html = anatomia.render(cara="publica", inv=inv)
        for caja in inv["cajas"]:
            self.assertNotIn(caja, html, "se ve el nombre de la caja %r" % caja)
        if inv["cajas"]:
            self.assertIn(caja, anatomia.render(cara="privada", inv=inv))

    def test_la_cara_publica_reserva_los_comites_delatores(self):
        """El scrubber caza léxico, no contexto: 'forense-pericial' no dice nada
        prohibido y aun así delata que hay un caso de acoso abierto."""
        html = anatomia.render(cara="publica")
        for delator in anatomia.PUBLICO_RESERVADO:
            self.assertNotIn(">%s<" % delator, html)
        # …y en la privada sí están, que es donde ella los necesita ver.
        priv = anatomia.render(cara="privada")
        for delator in anatomia.PUBLICO_RESERVADO:
            self.assertIn(">%s<" % delator, priv)

    def test_la_cara_privada_si_los_lista(self):
        html = anatomia.render(cara="privada")
        self.assertIn("healthcheck", html)


class TestCastellano(unittest.TestCase):

    def test_miles_con_punto(self):
        self.assertEqual(anatomia._num(45599), "45.599")
        self.assertEqual(anatomia._num(58), "58")

    def test_concordancia(self):
        self.assertEqual(anatomia._pl(1, "despierto"), "1 despierto")
        self.assertEqual(anatomia._pl(3, "despierto"), "3 despiertos")
        self.assertEqual(anatomia._pl(1, "conexión", "conexiones"), "1 conexión")
        self.assertEqual(anatomia._pl(0, "comité", "comités"), "0 comités")

    def test_fecha_en_cristiano(self):
        self.assertEqual(anatomia._fecha("2026-07-25"), "25 de julio de 2026")

    def test_no_quedan_numeros_pegados_al_plural_mal(self):
        """Con frontera de palabra, no por subcadena: «61 rutinas» es correcto y
        contiene «1 rutinas» dentro. Buscarlo a lo bruto daba un rojo falso."""
        html = anatomia.render(cara="privada")
        visible = re.sub(r"<[^>]+>", " ", html)
        for plural in ("comités", "despiertos", "rutinas", "memorias",
                       "herramientas", "conexiones", "skills", "ejecuciones"):
            self.assertIsNone(
                re.search(r"(?<!\d)1 %s\b" % plural, visible),
                "sale «1 %s» (debería ir en singular)" % plural)


class TestRender(unittest.TestCase):

    def test_es_html_autocontenido(self):
        html = anatomia.render()
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn("</html>", html)

    def test_sin_recursos_externos(self):
        """Nada de CDN ni fuentes remotas: tiene que verse sin red y sin fugas
        de referer a terceros."""
        html = anatomia.render()
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)

    def test_los_nodos_son_enlaces_de_verdad(self):
        """Enlace, no onclick: se alcanza con el teclado solo, funciona sin JS y
        permite servir el panel con una CSP que prohíbe scripts."""
        html = anatomia.render()
        self.assertIn('<a href="#d-comites"', html)
        self.assertIn('aria-label=', html)

    def test_sin_servidor_no_mete_javascript(self):
        """El fichero suelto y el panel no tienen /api/pulso detrás: meter un
        <script> ahí solo sirve para que la CSP lo bloquee y ensucie la consola."""
        self.assertNotIn("<script", anatomia.render())
        self.assertIn("<script", anatomia.render(vivo=True))

    def test_el_svg_se_anuncia(self):
        html = anatomia.render()
        self.assertIn('role="img"', html)
        self.assertIn("<title", html)

    def test_modo_vertical(self):
        self.assertIn('class="vert"', anatomia.render(vertical=True))


if __name__ == "__main__":
    # Salida por stdout como el resto de la batería: test_all.sh muestra la
    # última línea y descarta stderr, que es donde escribe unittest.
    res = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if res.wasSuccessful():
        print("✅ LA ANATOMÍA EN VERDE (%d tests)" % res.testsRun)
        sys.exit(0)
    print("❌ LA ANATOMÍA EN ROJO: %d fallos, %d errores"
          % (len(res.failures), len(res.errors)))
    sys.exit(1)
