#!/usr/bin/env python3
"""Tests de la CARA TÉCNICA de La Anatomía (tools/anatomia.py).

La cara técnica existe para enseñarle el sistema a alguien que sabe de sistemas
agénticos: todo el detalle de arquitectura, máquina, modelos y gasto, pero sin
los nombres que delatan la vida de {{TITULAR}}. Lo que se protege aquí:

  · que NO se escape ninguno de esos nombres (comités reservados, cajas, los
    daemons que nombran un canal personal) — y que la cara privada sí los tenga,
    porque un test que pasa por estar todo vacío no protege nada;
  · que lo que la cara técnica PROMETE enseñar, lo enseñe (modelos exactos,
    carriles, máquina, gasto);
  · que el panel no MIENTA: lo que declara sobre el lazo tiene que seguir siendo
    verdad en el shell que lo ejecuta (anti-desincronización);
  · que las secciones nuevas no rompan los invariantes del HTML (autocontenido,
    sin red, sin JavaScript) ni el castellano.
"""

import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import anatomia  # noqa: E402


def _texto(html):
    """El HTML despojado de etiquetas: lo que de verdad LEE quien lo abre."""
    t = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t)


class _Render(unittest.TestCase):
    """Las tres caras se renderizan UNA vez: el inventario toca disco y el gasto
    escanea las trazas, y hacerlo por test multiplicaba la suite por diez."""

    @classmethod
    def setUpClass(cls):
        cls.inv = anatomia.inventario()
        cls.pul = anatomia.pulso()
        cls.html = {}
        for cara in anatomia.CARAS:
            cls.html[cara] = anatomia.render(cara=cara, inv=cls.inv, pul=cls.pul)
        cls.txt = {c: _texto(h) for c, h in cls.html.items()}


class TestNoSeEscapaLoQueNoDebe(_Render):

    def test_ningun_comite_reservado_sale_en_tecnica(self):
        for nombre in anatomia.PUBLICO_RESERVADO:
            self.assertNotIn(">%s<" % nombre, self.html["tecnica"],
                             "el comité %s no debería salir en técnica" % nombre)

    def test_la_privada_si_los_lista(self):
        # Sin esto, el test de arriba pasaría igual con el panel roto y vacío.
        for nombre in anatomia.PUBLICO_RESERVADO:
            self.assertIn(">%s<" % nombre, self.html["privada"])

    def test_las_cajas_salen_contadas_pero_no_nombradas(self):
        for caja in self.inv["cajas"]:
            self.assertNotIn(caja, self.html["tecnica"],
                             "la caja %s se llama como un trozo de su vida" % caja)
        if self.inv["cajas"]:
            self.assertIn(self.inv["cajas"][0], self.html["privada"])

    def test_los_daemons_de_canales_personales_no_salen(self):
        personales = [r["nombre"] for r in self.inv["rutinas"]
                      if anatomia._reservada_en_tecnica(r["nombre"])]
        self.assertTrue(personales, "la lista TECNICA_RESERVADO ya no casa con nada")
        for nombre in personales:
            self.assertNotIn(">%s<" % nombre, self.html["tecnica"])

    def test_no_sale_el_numero_de_serie_de_la_maquina(self):
        # `maquina()` va por sysctl justamente para no traerlo. Si alguien lo
        # cambia a system_profiler, esto lo caza antes de que se publique.
        try:
            serie = subprocess.run(
                ["sysctl", "-n", "kern.uuid"], capture_output=True,
                text=True, timeout=5).stdout.strip()
        except Exception:
            self.skipTest("sin sysctl en esta máquina")
        if not serie:
            self.skipTest("esta máquina no expone identificador")
        for cara in anatomia.CARAS:
            self.assertNotIn(serie, self.html[cara])

    def test_lo_ultimo_que_ha_pasado_es_solo_de_la_privada(self):
        # Son nombres de job en crudo: no pasan por el scrubber y no hacen falta
        # para entender la arquitectura.
        self.assertIn("lo último que ha pasado", self.txt["privada"])
        self.assertNotIn("lo último que ha pasado", self.txt["tecnica"])

    def test_la_tecnica_pasa_por_el_scrubber(self):
        import _lexico_publico
        self.assertEqual(_lexico_publico.revisar(self.txt["tecnica"]), [])

    def test_el_scrubber_de_verdad_muerde_en_tecnica(self):
        # Canario: si `limpiar` dejara de filtrar en técnica, este test se cae y
        # el de arriba podría seguir pasando por casualidad.
        self.assertEqual(anatomia.limpiar("metastasis en el higado", "tecnica"),
                         "(reservado)")
        self.assertEqual(anatomia.limpiar("un router de intención", "tecnica"),
                         "un router de intención")


class TestLoQuePrometeEnsenar(_Render):

    def test_cada_llm_encendido_sale_con_su_modelo_exacto(self):
        for c in self.inv["cerebros"]:
            if not c["encendido"] or not c["modelos"]:
                continue
            self.assertIn(c["modelos"][0], self.html["tecnica"],
                          "falta el modelo de %s" % c["nombre"])
            self.assertIn(">%s<" % c["nombre"], self.html["tecnica"])

    def test_el_carril_clinico_no_lo_coge_nadie_sin_confianza(self):
        for r in anatomia.ruteo():
            if r["carril"] != "clinico":
                continue
            self.assertTrue(r["especialistas"], "nadie atiende lo sensible")
            confianza = {c["nombre"] for c in self.inv["cerebros"] if c["confianza"]}
            for quien in r["especialistas"] + r["luego"]:
                self.assertIn(quien, confianza)

    def test_el_ruteo_cubre_TODOS_los_perfiles_del_router(self):
        import ia
        self.assertEqual([r["carril"] for r in anatomia.ruteo()], list(ia.PERFILES))

    def test_cada_carril_esta_explicado(self):
        import ia
        self.assertEqual(set(anatomia.CARRILES), set(ia.PERFILES),
                         "hay un carril del router que el panel no explica")

    def test_la_maquina_sale_con_chip_memoria_y_disco(self):
        t = self.txt["tecnica"]
        for trozo in ("la máquina", "memoria", "disco"):
            self.assertIn(trozo, t)
        self.assertIn("Mac mini", t)

    def test_el_gasto_sale_en_tecnica_y_no_en_publica(self):
        self.assertIn("lo que cuesta tenerlo en pie", self.txt["tecnica"])
        self.assertNotIn("lo que cuesta tenerlo en pie", self.txt["publica"])

    def test_el_camino_del_encargo_sale_entero(self):
        t = self.txt["tecnica"]
        for nombre, fichero, _ in anatomia.CAMINO:
            self.assertIn(nombre, t)
            self.assertIn(fichero, t)

    def test_el_glosario_traduce_las_palabras_de_la_casa_que_se_usan(self):
        t = self.txt["tecnica"]
        for casa, estandar, _ in anatomia.GLOSARIO:
            self.assertIn(casa, t)
            self.assertIn(estandar, t)

    def test_el_panel_habla_de_LLM_no_solo_de_cerebros(self):
        # La regla de {{TITULAR}} (27/7/26): el término estándar manda y la palabra de
        # la casa va al lado. «cerebro» solo puede aparecer glosándose.
        t = self.txt["tecnica"]
        self.assertIn("LLM", t)
        self.assertIn("proveedores de inferencia", t)
        self.assertLessEqual(t.count("cerebro"), 2)

    def test_cada_nodo_del_mapa_lleva_a_una_seccion_que_existe(self):
        # Un ancla a ninguna parte es una promesa rota, y en el móvil no se ve.
        html = self.html["tecnica"]
        for cid in re.findall(r'<a href="#d-([a-z]+)"', html):
            self.assertIn('id="d-%s"' % cid, html,
                          "el mapa enlaza a #d-%s y esa sección no existe" % cid)


class TestElPanelNoMiente(unittest.TestCase):
    """El panel AFIRMA cosas sobre el lazo. Si el lazo cambia y el panel no, el
    panel miente en silencio — que es peor que no contar nada."""

    def setUp(self):
        with open(os.path.join(ROOT, "tools", "run_agent.sh"),
                  encoding="utf-8") as f:
            self.sh = f.read()

    def test_la_cadena_de_degradacion_sigue_siendo_la_del_shell(self):
        for cadena in anatomia.CADENA_MODELOS:
            self.assertIn("CADENA=(%s)" % " ".join(cadena), self.sh,
                          "el panel anuncia una cadena que run_agent.sh ya no usa")

    def test_el_modelo_por_defecto_sigue_siendo_el_del_shell(self):
        self.assertIn('MODELO_PEDIDO="${BTP_MODEL:-%s}"' % anatomia.MODELO_POR_DEFECTO,
                      self.sh)

    def test_los_ficheros_del_camino_existen(self):
        for _, fichero, _ in anatomia.CAMINO:
            for pieza in fichero.split(" · "):
                if pieza == "MCP":                      # no es una ruta
                    continue
                self.assertTrue(
                    os.path.exists(os.path.join(ROOT, pieza)),
                    "el camino cita %s y ahí no hay nada" % pieza)


class TestFailSoft(unittest.TestCase):
    """El panel es un MAPA: prefiere no saber algo antes que caerse entero."""

    def test_la_maquina_no_revienta_si_sysctl_falla(self):
        real = anatomia.subprocess.run
        anatomia.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(OSError())
        try:
            m = anatomia.maquina()
            self.assertNotIn("chip", m)
            self.assertIn("llm_local_vivo", m)          # esto no va por sysctl
        finally:
            anatomia.subprocess.run = real

    def test_el_gasto_aguanta_un_estado_corrupto(self):
        # cost_guard levanta SystemExit ante un fichero de coste ilegible; el
        # panel no puede morir por no poder pintar una cifra.
        import cost_guard
        real = cost_guard.today_spent
        cost_guard.today_spent = lambda: (_ for _ in ()).throw(SystemExit("boom"))
        try:
            self.assertIsInstance(anatomia.gasto(), dict)
        finally:
            cost_guard.today_spent = real

    def test_render_sigue_en_pie_sin_registro_de_arquitectos(self):
        real = anatomia.arquitectos
        anatomia.arquitectos = lambda: []
        try:
            h = anatomia.render(cara="tecnica")
            self.assertIn('id="d-arquitecto"', h)       # la sección sale igual
        finally:
            anatomia.arquitectos = real

    def test_una_cara_inventada_no_se_renderiza_desde_el_CLI(self):
        rc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", "anatomia.py"),
             "render", "--cara", "loquesea"],
            capture_output=True, text=True).returncode
        self.assertEqual(rc, 2)


class TestElHTMLSigueSiendoSano(_Render):

    def test_ninguna_cara_pide_nada_a_la_red(self):
        for cara, h in self.html.items():
            self.assertNotIn("http://", h, cara)
            self.assertNotIn("https://", h, cara)

    def test_ninguna_cara_lleva_javascript_sin_servidor(self):
        for cara, h in self.html.items():
            self.assertNotIn("<script", h, cara)
            self.assertNotIn("onclick", h, cara)

    def test_las_tres_caras_son_html_autocontenido(self):
        for cara, h in self.html.items():
            self.assertTrue(h.startswith("<!doctype html>"), cara)
            self.assertIn("</html>", h, cara)

    def test_el_castellano_de_las_secciones_nuevas(self):
        # «1 rutinas», «1 subagentes» y compañía: la pieza se VE, y esto se lee.
        for cara, t in self.txt.items():
            for plural in ("subagentes", "proveedores", "guardarraíles",
                           "herramientas", "rutinas", "días"):
                self.assertIsNone(re.search(r"(?<!\d)1 %s\b" % plural, t),
                                  "«1 %s» en la cara %s" % (plural, cara))

    def test_los_dolares_se_escriben_en_castellano(self):
        self.assertEqual(anatomia._usd(4.85), "4,85 $")
        self.assertEqual(anatomia._usd(60.0), "60 $")
        self.assertEqual(anatomia._usd(1500), "1.500 $")
        self.assertEqual(anatomia._usd(4.999), "5 $")     # sin «4,100»
        self.assertEqual(anatomia._usd(None), "?")


if __name__ == "__main__":
    unittest.main(verbosity=1)
