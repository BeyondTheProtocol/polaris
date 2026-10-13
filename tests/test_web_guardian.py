#!/usr/bin/env python3
"""test_web_guardian.py — el detector de la web detecta, clasifica bien y en sombra no hace nada.

POR QUÉ EXISTE (13-oct-2026, plan «guardián de la web»). Lo que se fija:

  1. cada señal salta con el caso real que la motivó (ancla rota con `highlight: false`, sitemap
     con un 404, cronología parada, /datos más viejo que sus fuentes, ES y EN descuadrados);
  2. lo que nace de una fuente EXTERNA nunca cae en el peldaño 1 (el que viaja en un «fusiona»
     sin leer), y lo clínico nuevo es siempre peldaño 3;
  3. en modo SOMBRA no escribe en el buzón de Vega ni pone nada en el parte;
  4. en activo mantiene UNA sola propuesta viva, no una por pasada;
  5. si algo falla al mirar, se dice como «no pude mirar», no como «todo bien».

Nada toca la red: las lecturas se inyectan.
"""
import datetime
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="webguard-")
os.environ["BTP_STATE_DIR"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))
import web_guardian as G  # noqa: E402

HOY = datetime.date(2026, 10, 13)

TL_ES = """entries:
  - date: '9 sep 2026'
    tag: Progresión
    title: Uno
    description: >
      x
    highlight: true

  - date: '20 sep 2026'
    tag: Ensayos clínicos
    title: Dos
    description: >
      x
    highlight: %s
"""
SITEMAP = ("<urlset><url><loc>https://helptitular.com/</loc></url>"
           "<url><loc>https://helptitular.com/es/historia/01-el-comienzo</loc></url></urlset>")


def _claves(hallazgos):
    return {h["clave"] for h in hallazgos}


class LaCronologia(unittest.TestCase):

    def test_lee_la_ultima_fecha_y_si_esta_destacada(self):
        ent = G.entradas(TL_ES % "false")
        self.assertEqual(2, len(ent))
        self.assertEqual("20 sep 2026", ent[-1]["date"])
        self.assertFalse(ent[-1]["highlight"])

    def test_parada_23_dias_salta(self):
        h = G.senal_cronologia_parada(G.entradas(TL_ES % "false"), HOY)
        self.assertEqual(["cronologia-parada"], [x["clave"] for x in h])
        self.assertIn("23", h[0]["detalle"])
        self.assertEqual(3, h[0]["peldano"], "una entrada nueva lleva su voz: la firma ella")

    def test_al_dia_no_salta(self):
        self.assertEqual([], G.senal_cronologia_parada(G.entradas(TL_ES % "false"),
                                                       datetime.date(2026, 9, 25)))

    def test_fecha_ilegible_se_dice(self):
        h = G.senal_cronologia_parada([{"date": "primavera", "highlight": False}], HOY)
        self.assertEqual(["cronologia-fecha-ilegible"], [x["clave"] for x in h])


class Paridad(unittest.TestCase):

    def test_es_y_en_descuadrados(self):
        es = G.entradas(TL_ES % "false")
        h = G.senal_paridad(es, es[:1])
        self.assertEqual(["cronologia-en-atrasada"], [x["clave"] for x in h])
        self.assertEqual(2, h[0]["peldano"], "el inglés se escribe: es texto visible")
        self.assertEqual([], G.senal_paridad(es, es))


class SaludEnVivo(unittest.TestCase):

    def _lee(self, paginas):
        return lambda url: paginas.get(url, (404, ""))

    def test_ancla_rota_cuando_la_ultima_no_esta_destacada(self):
        """El caso real del 13-oct-26: `curl` a /timeline daba 0 apariciones de id="lo-ultimo"."""
        lee = self._lee({G.SITIO + "/timeline": (200, "<li>sin ancla</li>"),
                         G.SITIO + "/sitemap_index.xml": (200, "")})
        self.assertIn("ancla-lo-ultimo-rota", _claves(G.senal_en_vivo(lee)))
        lee = self._lee({G.SITIO + "/timeline": (200, '<li id="lo-ultimo">'),
                         G.SITIO + "/sitemap_index.xml": (200, "")})
        self.assertNotIn("ancla-lo-ultimo-rota", _claves(G.senal_en_vivo(lee)))

    def test_el_sitemap_con_un_404(self):
        paginas = {G.SITIO + "/timeline": (200, 'id="lo-ultimo"'),
                   G.SITIO + "/sitemap_index.xml": (200, "<sitemap><loc>%s/s.xml</loc></sitemap>" % G.SITIO),
                   G.SITIO + "/s.xml": (200, SITEMAP),
                   "https://helptitular.com/": (200, "ok")}
        h = G.senal_en_vivo(self._lee(paginas))
        self.assertEqual(["sitemap-con-404"], [x["clave"] for x in h])
        self.assertIn("/es/historia/01-el-comienzo", h[0]["detalle"])
        self.assertEqual(1, h[0]["peldano"])

    def test_si_no_puedo_mirar_lo_digo(self):
        h = G.senal_en_vivo(lambda url: (None, ""))
        self.assertEqual(["web-no-se-pudo-mirar"], [x["clave"] for x in h])


class LosDatos(unittest.TestCase):

    def test_datos_mas_viejos_que_sus_fuentes(self):
        h = G.senal_datos_viejos("2026-09-29T23:09:20", {"analíticas": "2026-10-05T10:00:00"})
        self.assertEqual(["datos-atrasados"], [x["clave"] for x in h])
        self.assertEqual(1, h[0]["peldano"], "regenerar lo ya sellado está aprobado desde el 24-sep")
        self.assertEqual([], G.senal_datos_viejos("2026-09-29T23:09:20",
                                                  {"analíticas": "2026-09-26T17:17:32"}))

    def test_el_perfil_curado_a_mano_es_peldano_3(self):
        h = G.senal_datos_viejos("2026-09-29T23:09:20", {"perfil curado": "2026-10-05T10:00:00"})
        self.assertEqual(3, h[0]["peldano"])

    def test_panel_y_cronologia_divergen(self):
        es = G.entradas(TL_ES % "false")
        h = G.senal_panel_vs_cronologia(es, [{"fecha_texto": "9 sep 2026", "titulo": {"es": "Uno"}}])
        self.assertEqual(["panel-sin-la-ultima-entrada"], [x["clave"] for x in h])
        self.assertEqual([], G.senal_panel_vs_cronologia(
            es, [{"fecha_texto": e["date"], "titulo": {"es": e["title"]}} for e in es]))


class InformesYDeudas(unittest.TestCase):

    def test_informes_posteriores_a_la_ultima_entrada(self):
        lineas = ["· 2026-09-10 · Anatomía patológica · informe", "· 2026-10-02 · Analíticas · x",
                  "· sin fecha · Otros"]
        h = G.senal_informes(lineas, datetime.date(2026, 9, 20))
        self.assertEqual(["informes-sin-reflejar"], [x["clave"] for x in h])
        self.assertIn("1 informe", h[0]["detalle"])
        self.assertEqual(3, h[0]["peldano"])
        self.assertNotIn("Analíticas · x", json.dumps(h, ensure_ascii=False),
                         "cuenta, no copia la descripción del informe")

    def test_deudas_de_contenido_abiertas(self):
        libro = {"web-datos-sin-ap-higado": {"estado": "abierto"},
                 "ciencia-fgfr1-x13-desactualizado": {"estado": "abierto"},
                 "web-test-ciencia-nivel-rmdir-flaky": {"estado": "abierto"},
                 "web-timeline-ancla-lo-ultimo": {"estado": "cerrado"},
                 "La web de síntomas no se alcanza": {"estado": "escalado"},
                 "umami-rango-absoluto-da-cero": {"estado": "abierto"}}
        h = G.senal_deudas(libro)
        self.assertEqual(1, len(h))
        self.assertIn("2", h[0]["detalle"])
        self.assertIn("web-datos-sin-ap-higado", h[0]["detalle"])
        self.assertNotIn("flaky", h[0]["detalle"], "un test inestable no es contenido atrasado")


class LaEscalera(unittest.TestCase):

    def test_lo_externo_nunca_es_peldano_1(self):
        for peldano in (1, 2, 3):
            h = G.hallazgo("x", peldano, "t", "d", origen="externo")
            self.assertGreaterEqual(h["peldano"], 2)
        self.assertEqual(1, G.hallazgo("x", 1, "t", "d")["peldano"])

    def test_prensa_viene_de_fuera(self):
        h = G.senal_prensa(en_registro=6, en_web=4)
        self.assertEqual("externo", h[0]["origen"])
        self.assertGreaterEqual(h[0]["peldano"], 2)
        self.assertEqual([], G.senal_prensa(en_registro=4, en_web=4))


class SombraYActivo(unittest.TestCase):

    HALL = [G.hallazgo("ancla-lo-ultimo-rota", 1, "El ancla no existe", "d"),
            G.hallazgo("cronologia-parada", 3, "Cronología parada", "23 días")]

    def setUp(self):
        for nombre in ("modo.json", "sombra.jsonl", "hallazgos.json"):
            try:
                os.remove(os.path.join(G.DIR, nombre))
            except OSError:
                pass
        try:
            os.remove(G.BUZON)
        except OSError:
            pass

    def test_por_defecto_es_sombra(self):
        self.assertEqual("sombra", G.modo())

    def test_en_sombra_apunta_y_no_propone_ni_sale_en_el_parte(self):
        G.guardar(self.HALL, HOY)
        self.assertTrue(os.path.exists(os.path.join(G.DIR, "sombra.jsonl")))
        self.assertFalse(os.path.exists(G.BUZON), "en sombra no toca el buzón de Vega")
        self.assertEqual([], G.resumen_hoy(hoy=HOY))

    def test_la_sombra_no_repite_lo_ya_apuntado(self):
        G.guardar(self.HALL, HOY)
        G.guardar(self.HALL, HOY)
        with open(os.path.join(G.DIR, "sombra.jsonl"), encoding="utf-8") as f:
            self.assertEqual(2, len(f.read().strip().splitlines()))

    def test_en_activo_mantiene_una_sola_propuesta(self):
        os.makedirs(G.DIR, exist_ok=True)
        with open(os.path.join(G.DIR, "modo.json"), "w") as f:
            json.dump({"modo": "activo"}, f)
        os.makedirs(os.path.dirname(G.BUZON), exist_ok=True)
        with open(G.BUZON, "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "x", "titulo": "otra cosa", "origen": "sesion"}) + "\n")
        G.guardar(self.HALL, HOY)
        G.guardar(self.HALL + [G.hallazgo("sitemap-con-404", 1, "t", "d")], HOY)
        with open(G.BUZON, encoding="utf-8") as f:
            lineas = [json.loads(x) for x in f.read().strip().splitlines()]
        mias = [x for x in lineas if x.get("origen") == G.ORIGEN]
        self.assertEqual(1, len(mias))
        self.assertIn("3", mias[0]["titulo"])
        self.assertEqual("otra cosa", lineas[0]["titulo"], "y no toca las propuestas de otros")

    def test_en_activo_el_parte_solo_cuenta_lo_que_le_pide_atencion(self):
        os.makedirs(G.DIR, exist_ok=True)
        with open(os.path.join(G.DIR, "modo.json"), "w") as f:
            json.dump({"modo": "activo"}, f)
        G.guardar(self.HALL, HOY)
        lineas = G.resumen_hoy(hoy=HOY)
        self.assertEqual(1, len(lineas))
        self.assertIn("1 ", lineas[0])
        G.guardar([self.HALL[0]], HOY)
        self.assertEqual([], G.resumen_hoy(hoy=HOY), "solo peldaño 1: nada que leer, nada en el parte")

    def test_los_lunes_el_parte_da_la_frase_de_la_pasada_semanal(self):
        """Sustituye a la rutina de la app, que una sesión en Polaris no puede crear."""
        os.makedirs(G.DIR, exist_ok=True)
        with open(os.path.join(G.DIR, "modo.json"), "w") as f:
            json.dump({"modo": "activo"}, f)
        lunes = datetime.date(2026, 10, 19)
        G.guardar([self.HALL[0]], lunes)                 # solo peldaño 1: nada que leer
        lineas = G.resumen_hoy(hoy=lunes)
        self.assertEqual(1, len(lineas))
        self.assertIn("haz la pasada semanal del guardián de la web", lineas[0])
        self.assertEqual([], G.resumen_hoy(hoy=datetime.date(2026, 10, 20)), "el martes ya no")
        G.guardar([], lunes)
        self.assertEqual([], G.resumen_hoy(hoy=lunes), "sin hallazgos no hay pasada que hacer")

    def test_en_sombra_tampoco_los_lunes(self):
        G.guardar(self.HALL, datetime.date(2026, 10, 19))
        self.assertEqual([], G.resumen_hoy(hoy=datetime.date(2026, 10, 19)))

    def test_un_estado_roto_no_tumba_el_parte(self):
        os.makedirs(G.DIR, exist_ok=True)
        with open(os.path.join(G.DIR, "hallazgos.json"), "w") as f:
            f.write("{roto")
        self.assertEqual([], G.resumen_hoy(hoy=HOY))


if __name__ == "__main__":
    unittest.main(verbosity=1)
