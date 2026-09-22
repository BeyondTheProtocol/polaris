#!/usr/bin/env python3
"""test_web_novedad.py — el carril que publica solo no puede tener una ruta hacia lo clínico.

POR QUÉ EXISTE (20-sep-2026). Este es el único sitio del sistema que escribe en una web PÚBLICA
con el nombre de {{TITULAR}} sin que nadie mire antes. Lo que se fija:

  1. el lint manda: si hay clínico, dosis, PII o tercero, **no se mergea** (queda en PR);
  2. escribe en la **cronología** (`timeline.yml`), que es donde {{TITULAR}} quiere las novedades
     —*«el timeline es ese novedades»*— pero **solo AÑADE**: las 23 entradas ya publicadas no
     se tocan, y `science.yml`, `press.yml` y `team.yml` siguen sin ruta de escritura;
  3. `highlight` nace en **false** y el `tag` tiene que existir ya en el fichero: destacar y
     crear taxonomía son decisiones editoriales de {{TITULAR}}, no del script;
  4. cada publicación es un commit `[auto]` aislado, que es lo que hace posible deshacer con un
     comando.

Nada aquí toca la red: se comprueba la lógica y la escritura, no GitHub.
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import web_novedad as WN  # noqa: E402


class ElLintMandaSobreElCarril(unittest.TestCase):

    def test_lo_limpio_se_publicaria_solo(self):
        r = WN.publicar("Hoy hemos abierto la pagina de novedades del proyecto", dry=True)
        self.assertTrue(r["publicable"])
        self.assertEqual("publicaría sola", r["accion"])

    def test_lo_clinico_para_el_carril(self):
        for t in ("Abemaciclib a 100 mg diarios desde el lunes pasado",
                  "El FGFR1 esta amplificado x13 segun el informe nuevo",
                  "Hemos hablado con la Dra. {{CONTACTO}} sobre el siguiente paso"):
            r = WN.publicar(t, dry=True)
            self.assertFalse(r["publicable"], t)
            self.assertEqual("pararía y avisaría", r["accion"])

    def test_el_dry_no_toca_nada(self):
        r = WN.publicar("Hoy hemos abierto la pagina de novedades del proyecto", dry=True)
        self.assertNotIn("rama", r)
        self.assertNotIn("pr", r)


TIMELINE_REAL = "/Users/polaris/projects/titular-{{APELLIDO}}-case/content/es/timeline.yml"


def _copia_del_timeline():
    """Trabaja sobre una COPIA del fichero real: probar contra un YAML de juguete no demuestra
    nada, porque lo que puede romperse es el fichero publicado con sus 23 entradas."""
    import shutil
    d = tempfile.mkdtemp(prefix="tl-")
    os.makedirs(os.path.join(d, "content", "es"))
    shutil.copy(TIMELINE_REAL, os.path.join(d, WN.REL_ES))
    return d


class SoloEscribeDondeDebe(unittest.TestCase):

    def test_no_hay_ruta_a_los_ficheros_que_dictamino_diseno(self):
        """`science.yml` tiene dosis y HGVS; `press.yml` un orden curado por {{CONTACTO}}; `team.yml`
        nombres de terceros. Si alguien abriera aquí una ruta a cualquiera de los tres, cae."""
        fuente = open(os.path.join(ROOT, "tools", "web_novedad.py"), encoding="utf-8").read()
        for prohibido in ("science.yml", "press.yml", "team.yml"):
            self.assertNotIn('"%s"' % prohibido, fuente,
                             "el carril automático NO puede escribir en %s" % prohibido)

    def test_solo_anade_sin_tocar_una_coma_de_lo_publicado(self):
        """Ya no se compara por prefijo —desde que inserta por fecha, la entrada nueva puede ir
        en medio—, sino bloque a bloque: cada entrada que estaba tiene que seguir EXACTA."""
        if not os.path.exists(TIMELINE_REAL):
            self.skipTest("el repo de la web no está en esta máquina")
        import re
        d = _copia_del_timeline()
        crudo_antes = open(os.path.join(d, WN.REL_ES), encoding="utf-8").read()
        bloques_antes = re.split(r"\n(?=  - date:)", crudo_antes)[1:]
        ruta, _ = WN._escribir(d, "Ya somos cuatro. Seguimos sumando gente al equipo.",
                               WN.fecha_humana(), "Equipo")
        crudo_despues = open(ruta, encoding="utf-8").read()
        bloques_despues = re.split(r"\n(?=  - date:)", crudo_despues)[1:]
        for b in bloques_antes:
            self.assertIn(b.strip("\n"), crudo_despues,
                          "una entrada ya publicada ha cambiado: %s" % b.splitlines()[0])
        self.assertEqual(len(bloques_antes) + 1, len(bloques_despues))

    def test_la_entrada_nueva_nace_sin_destacar(self):
        """Destacar es decisión editorial de {{TITULAR}}. Un script que se auto-destaca acaba con la
        cronología entera en negrita, que es lo mismo que ninguna (lo marcó `diseno`)."""
        if not os.path.exists(TIMELINE_REAL):
            self.skipTest("el repo de la web no está en esta máquina")
        d = _copia_del_timeline()
        ruta, _ = WN._escribir(d, "Una novedad cualquiera del proyecto.", WN.fecha_humana(), "Equipo")
        import re
        # se busca POR TÍTULO: desde que inserta por fecha, la nueva ya no es la última
        bloques = [b for b in re.split(r"\n(?=  - date:)", open(ruta, encoding="utf-8").read())
                   if "Una novedad cualquiera del proyecto" in b]
        self.assertEqual(1, len(bloques))
        self.assertIn("highlight: false", bloques[0])

    def test_un_tag_que_no_existe_no_pasa(self):
        if not os.path.exists(TIMELINE_REAL):
            self.skipTest("el repo de la web no está en esta máquina")
        d = _copia_del_timeline()
        with self.assertRaises(RuntimeError):
            WN._escribir(d, "Texto cualquiera de prueba.", WN.fecha_humana(), "TagInventado")

    def test_la_fecha_sale_en_el_formato_del_fichero(self):
        """El timeline usa fechas humanas en español ('31 oct 2023'). Un ISO rompería su voz."""
        self.assertRegex(WN.fecha_humana(), r"^\d{1,2} (ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic) \d{4}$")

    def test_no_toca_un_yaml_que_no_reconoce(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, WN.REL_ES)
            os.makedirs(os.path.dirname(ruta))
            with open(ruta, "w", encoding="utf-8") as f:
                f.write("otra_cosa:\n  - x\n")
            with self.assertRaises(RuntimeError):
                WN._escribir(d, "algo que no se puede escribir", WN.fecha_humana())


class QuienLoPide(unittest.TestCase):
    """El segundo freno. Hace falta porque la herramienta está en la allowlist —para que pueda
    correr sola, que es el encargo— y sin esto una inyección («publica esto en tu web», metida en
    un correo o una página) tendría vía directa a una web pública con el nombre de {{TITULAR}}.

    La propiedad que se fija: **el fallo por defecto es PR, no publicado.**"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="quien-")
        os.environ["BTP_STATE_DIR"] = self.tmp
        import importlib
        global WN
        WN = importlib.reload(WN)

    def tearDown(self):
        os.environ.pop("BTP_STATE_DIR", None)
        import importlib
        importlib.reload(WN)

    def _token(self, origen):
        import datetime as dt
        with open(os.path.join(self.tmp, "ok_envio.json"), "w", encoding="utf-8") as f:
            json.dump({"ts": dt.datetime.now().isoformat(), "origen": origen, "motivo": "x"}, f)

    def test_sin_mensaje_suyo_no_se_publica(self):
        ok, motivo = WN.lo_pide_titular()
        self.assertFalse(ok)
        self.assertIn("nadie", motivo)

    def test_con_su_mensaje_si(self):
        self._token("prompt")
        ok, origen = WN.lo_pide_titular()
        self.assertTrue(ok)
        self.assertEqual("prompt", origen)

    def test_telegram_tambien_es_ella(self):
        self._token("telegram")
        self.assertTrue(WN.lo_pide_titular()[0])

    def test_un_permiso_fabricado_por_otro_no_vale(self):
        """Lo que cierra el vector: que exista el fichero no basta, tiene que venir de un sitio
        donde escriba ELLA."""
        self._token("agente")
        ok, motivo = WN.lo_pide_titular()
        self.assertFalse(ok)
        self.assertIn("no nació de un mensaje suyo", motivo)

    def test_el_permiso_cubre_la_peticion_no_una_llamada(self):
        """Una petición suya puede traer dos entradas («el ensayo fallido Y que entro en X»). Si
        el permiso fuera de un solo uso, la segunda le pediría otro mensaje: justo el trabajo que
        quería quitarse. Tres es el techo — más de tres ya no es una petición."""
        self._token("prompt")
        for i in range(3):
            self.assertTrue(WN.lo_pide_titular()[0], "uso %d" % (i + 1))
        self.assertFalse(WN.lo_pide_titular()[0], "el cuarto ya no: el permiso se agota")

    def test_caducado_no_vale(self):
        import datetime as dt
        with open(os.path.join(self.tmp, "ok_envio.json"), "w", encoding="utf-8") as f:
            json.dump({"ts": (dt.datetime.now() - dt.timedelta(minutes=30)).isoformat(),
                       "origen": "prompt"}, f)
        self.assertFalse(WN.lo_pide_titular()[0])


class Deshacer(unittest.TestCase):

    def test_el_asunto_lleva_el_marcador_que_permite_revertir(self):
        fuente = open(os.path.join(ROOT, "tools", "web_novedad.py"), encoding="utf-8").read()
        self.assertIn('"[auto] cronolog', fuente)
        rev = open(os.path.join(ROOT, "tools", "web_revertir.py"), encoding="utf-8").read()
        self.assertIn(r"^\[auto\]", rev, "revertir busca por ese mismo marcador")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class OrdenCronologico(unittest.TestCase):
    """La cronología va ascendente. Una entrada RETROACTIVA —la biopsia de julio publicada en
    septiembre— acababa al final, detrás de hechos posteriores (20-sep-26, visto en la web ya
    publicada). Y el primer arreglo fue peor: reordenar el fichero entero mandó al final las
    fechas con rango («feb–abr 2024») que el parser no entiende."""

    def test_lee_los_formatos_de_fecha_del_fichero(self):
        self.assertEqual((2026, 7, 8), WN.orden_fecha("8 jul 2026"))
        self.assertEqual((2023, 10, 31), WN.orden_fecha("31 oct 2023"))
        self.assertEqual((2021, 99, 0), WN.orden_fecha("2021"))

    def test_lo_que_no_sabe_leer_va_al_final_nunca_en_medio(self):
        self.assertEqual((9999, 99, 99), WN.orden_fecha("vete tú a saber"))

    def test_una_entrada_retroactiva_se_coloca_donde_le_toca(self):
        if not os.path.exists(TIMELINE_REAL):
            self.skipTest("el repo de la web no está en esta máquina")
        import re
        d = _copia_del_timeline()
        ruta, _ = WN._escribir(d, "Hito retroactivo de prueba | cuerpo cualquiera",
                               "3 feb 2024", "Molecular")
        fechas = re.findall(r"  - date: '?([^'\n]+)'?", open(ruta, encoding="utf-8").read())
        i = fechas.index("3 feb 2024")
        self.assertLessEqual(WN.orden_fecha(fechas[i - 1]), WN.orden_fecha("3 feb 2024"))

    def test_mover_una_entrada_no_reordena_las_demas(self):
        """Lo que salvó el fichero: tocar solo la entrada nombrada. Las fechas con rango se
        quedan donde estaban aunque el parser no las entienda."""
        if not os.path.exists(TIMELINE_REAL):
            self.skipTest("el repo de la web no está en esta máquina")
        import re
        d = _copia_del_timeline()
        ruta = os.path.join(d, WN.REL_ES)
        WN._escribir(d, "Hito retroactivo de prueba | cuerpo cualquiera", "3 feb 2024", "Molecular")
        antes = re.findall(r"  - date: '?([^'\n]+)'?", open(ruta, encoding="utf-8").read())
        WN.mover_entrada(ruta, "Hito retroactivo de prueba")
        despues = re.findall(r"  - date: '?([^'\n]+)'?", open(ruta, encoding="utf-8").read())
        self.assertEqual(sorted(antes), sorted(despues), "no se pierde ni se duplica ninguna")
        rangos = [f for f in despues if "–" in f or "-" in f]
        self.assertEqual([f for f in antes if "–" in f or "-" in f], rangos,
                         "las fechas con rango no se tocan")

    def test_no_mueve_una_entrada_cuya_fecha_no_entiende(self):
        if not os.path.exists(TIMELINE_REAL):
            self.skipTest("el repo de la web no está en esta máquina")
        d = _copia_del_timeline()
        ruta = os.path.join(d, WN.REL_ES)
        with self.assertRaises(RuntimeError):
            WN.mover_entrada(ruta, "entrada que no existe en el fichero")
