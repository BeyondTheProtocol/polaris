#!/usr/bin/env python3
"""El Mapa: que dibuje lo que hay, que congele bien y que la cara pública calle.

Nada de red, nada de Chrome: aquí se prueba el SVG/HTML que genera el tool.
La captura (png/gif) depende de Chrome y ffmpeg y no entra en la batería.
"""

import os
import re
import sys
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))

import anatomia_mapa as m  # noqa: E402


INV = {
    "fecha": "2026-07-27",
    "comites": [{"nombre": "orquestador", "oficio": "x"},
                {"nombre": "verificacion", "oficio": "y"},
                {"nombre": "git", "oficio": "z"}],
    "herramientas": {"familias": {"a": ["t1", "t2"], "b": ["t3"]},
                     "total": 3, "lineas": 1234},
    "rutinas": [{"nombre": "r1", "cargado": True},
                {"nombre": "r2", "cargado": False}],
    "guardas": ["muro_guard", "gate_salida"],
    "cola": {"pending": 1, "processing": 0, "failed": 2, "done": 9},
    "cajas": ["viaje-{{CIUDAD}}-prueba"],
    "memoria": {"memorias": 300, "estado": 21, "tests": 121},
    "numeros": {"comites": 3, "cerebros": 5, "cerebros_on": 4, "tools": 3,
                "lineas": 1234, "rutinas_cargadas": 1, "rutinas_total": 2,
                "skills": 13, "skills_rotas": 0, "mcp": 18, "guardas": 2,
                "cajas": 1, "memorias": 300, "estado": 21},
}

PUL = {"ahora": "19:40", "agentes": {}, "heartbeats": {},
       "despiertos": ["verificacion"], "fontaneria": ["vigia"],
       "con_traza": ["verificacion", "git"], "sin_traza": ["orquestador"],
       "ultimas": [], "hoy_total": 259, "hoy_fallos": 20}

CAD = [{"etiqueta": "biopsia", "estado": "hecho", "foco": False},
       {"etiqueta": "resultados", "estado": "en_curso", "foco": True},
       {"etiqueta": "dianas", "estado": "pendiente", "foco": False}]


class TestDibujo(unittest.TestCase):

    def test_un_punto_por_comite_y_por_rutina(self):
        """El dibujo no se inventa el censo: sale del inventario."""
        s = m.svg(INV, PUL, CAD, "privada")
        # estrellas del anillo; la muestra de la leyenda va como «quieta»
        anillo = [c for c in re.findall(r'class="(cmt[^"]*)"', s)
                  if "quieta" not in c]
        self.assertEqual(len(anillo), 3)
        self.assertEqual(len(re.findall(r'class="rt on"', s)), 1)
        self.assertEqual(len(re.findall(r'class="rt off"', s)), 1)
        # una rayita por herramienta (las de la leyenda van como «tk quieta»)
        self.assertEqual(len(re.findall(r'<line class="tk"', s)), 3)
        self.assertEqual(len(re.findall(r'<line class="tk quieta"', s)), 3)

    def test_el_comite_despierto_va_encendido(self):
        s = m.svg(INV, PUL, CAD, "privada")
        self.assertIn('class="cmt vivo"', s)
        self.assertIn('class="cmt hoy"', s)

    def test_ninguna_particula_pasa_del_muro(self):
        """El muro también manda en el dibujo: el viaje acaba antes.

        Se lee el alcance real del CSS en vez de repetir la fórmula, para que
        el test siga sujetando aunque se muevan los radios.
        """
        alcance = int(re.search(r"translateY\(-(\d+)px\)", m._css()).group(1))
        self.assertLess(m.R_NUCLEO + alcance, m.R_MURO)

    def test_los_numeros_son_los_del_inventario(self):
        s = m.svg(INV, PUL, CAD, "privada")
        self.assertIn("4 de 5 cerebros", s)
        self.assertIn("1 de 2 rutinas 24/7", s)
        self.assertIn("259 VUELTAS HOY", s)
        self.assertIn("20 CAÍDAS", s)

    def test_el_foco_de_la_cadena_se_marca(self):
        s = m.svg(INV, PUL, CAD, "privada")
        self.assertIn("AQUÍ ESTAMOS", s)   # eyebrow: va en mayúsculas
        self.assertEqual(len(re.findall(r'class="cd foco"', s)), 1)
        self.assertEqual(len(re.findall(r'class="cd hecho"', s)), 1)


class TestCongelar(unittest.TestCase):

    def test_sin_t_la_animacion_corre(self):
        h = m.render(t=None, inv=INV, pul=PUL, cad=CAD)
        self.assertNotIn('class="congelado"', h)   # la regla CSS sí está siempre
        self.assertEqual(h.count("--t:"), 1)       # solo el 0s del :root

    def test_con_t_se_para_en_ese_instante(self):
        h = m.render(t=2.5, inv=INV, pul=PUL, cad=CAD)
        self.assertIn('class="congelado"', h)
        self.assertIn("--t:2.5s", h)
        self.assertIn("animation-play-state:paused", h)

    def test_dos_instantes_dan_html_distinto(self):
        """Si los frames salieran iguales, el GIF sería una foto fija."""
        a = m.render(t=0.0, inv=INV, pul=PUL, cad=CAD)
        b = m.render(t=2.0, inv=INV, pul=PUL, cad=CAD)
        self.assertNotEqual(a, b)


class TestMarca(unittest.TestCase):
    """Las reglas del design system que más se incumplen (DS §2.2)."""

    def test_solo_los_dos_fondos_de_la_marca(self):
        for tema, fondo in (("oscuro", "#2d1b3d"), ("claro", "#faf6f0")):
            css = m._css(tema)
            self.assertIn("--bg:%s" % fondo, css)
            self.assertNotIn("#000000", css)
            self.assertNotIn("#ffffff", css.lower())

    def test_el_violeta_es_el_canonico_no_el_stale(self):
        """El DS marca #a855b5 como stale (D1): aquí va #a44db2."""
        for tema in ("oscuro", "claro"):
            self.assertNotIn("a855b5", m._css(tema).lower())
        self.assertIn("#a44db2", m._css("oscuro"))

    def test_el_coral_es_un_solo_foco_y_solo_sobre_berenjena(self):
        """Coral = acción, nunca repartido y nunca sobre crema (DS §2.2)."""
        self.assertIn("--cta:#ff6b47", m._css("oscuro"))
        self.assertNotIn("#ff6b47", m._css("claro"))     # sobre crema, no
        s = m.svg(INV, PUL, CAD, "privada")
        # el coral lo llevan el nodo del foco y su halo, y nada más
        self.assertEqual(len(re.findall(r'class="cd foco"', s)), 1)
        self.assertEqual(len(re.findall(r'class="cd-halo"', s)), 1)

    def test_la_estrella_es_el_path_canonico(self):
        """El motivo se instancia, no se redibuja a mano (DS §5)."""
        s = m.svg(INV, PUL, CAD, "privada")
        self.assertIn("M10 1.6 C10.8 5", s)
        self.assertNotIn("✦", s)
        self.assertNotIn("★", s)


class TestFuentes(unittest.TestCase):
    """Fraunces y JetBrains Mono van incrustadas; sin ellas, fallback limpio."""

    def test_sin_carpeta_no_revienta_y_cae_al_fallback(self):
        real = m.dir_fuentes
        m.dir_fuentes = lambda: None
        try:
            self.assertEqual(m.fuentes_css(), "")
            h = m.render(inv=INV, pul=PUL, cad=CAD)
            self.assertNotIn("@font-face", h)
            self.assertIn("Georgia", h)          # el fallback que declara el DS
        finally:
            m.dir_fuentes = real

    def test_si_estan_se_incrustan_en_el_html(self):
        if not m.dir_fuentes():
            self.skipTest("sin tools/fonts en esta máquina")
        h = m.render(inv=INV, pul=PUL, cad=CAD)
        self.assertIn("@font-face", h)
        self.assertIn("font-family:'Fraunces'", h)
        # autocontenido: la fuente viaja dentro, no se pide a la red
        self.assertIn("base64,", h)
        self.assertNotIn("fonts.googleapis.com", h)
        self.assertNotIn("fonts.gstatic.com", h)

    def test_fraunces_va_por_delante_de_georgia(self):
        self.assertIn("font-family:Fraunces,Georgia", m._css("oscuro"))


class TestCaraPublica(unittest.TestCase):

    def test_un_eslabon_desconocido_no_sale_en_crudo(self):
        """Un nodo nuevo de la brújula es dato sin revisar: no se enseña.

        Lista blanca a propósito: `_lexico_publico` caza léxico vetado, pero
        NO nombres de médico ni de hospital, así que aquí no vale de red.
        """
        cad = [{"etiqueta": "biopsia hepatica Dr Lopez", "estado": "en_curso",
                "foco": True, "conocida": False}]
        s = m.svg(INV, PUL, cad, "publica")
        self.assertNotIn("Lopez", s)
        self.assertIn("(un paso más)", s)

    def test_un_eslabon_de_la_tabla_si_sale(self):
        cad = [{"etiqueta": "dianas", "estado": "pendiente", "foco": False,
                "conocida": True}]
        self.assertIn("dianas", m.svg(INV, PUL, cad, "publica"))

    def test_la_cara_privada_no_mete_medico_ni_hospital(self):
        """Ni en privado: el dibujo tiene que poder enseñarse tal cual."""
        s = m.svg(INV, PUL, m.cadena() or CAD, "privada")
        for palabra in ("{{CONTACTO}}", "{{CENTRO}}", "Zúrich", "{{CENTRO}}", "{{CENTRO}}"):
            self.assertNotIn(palabra, s)

    def test_html_bien_formado(self):
        from xml.etree import ElementTree
        s = m.svg(INV, PUL, CAD, "privada")
        ElementTree.fromstring(s)  # revienta si el SVG no cierra bien


if __name__ == "__main__":
    unittest.main()
