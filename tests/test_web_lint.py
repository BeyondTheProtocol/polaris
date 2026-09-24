#!/usr/bin/env python3
"""test_web_lint.py — si este freno falla, se publica solo algo que no debía.

POR QUÉ EXISTE (20-sep-2026). {{TITULAR}} pidió que la web se actualice sola. Eso solo puede existir
si algo decide, sin humano delante, qué NO sale. `web_lint` es ese algo, así que es la pieza con
más consecuencia de todo el carril: un falso negativo aquí es una cifra clínica publicada en una
web con su nombre, indexada por Google en minutos.

Dos direcciones, las dos importantes:
  · **no dejar pasar** lo clínico, la dosis, el identificador, el tercero, el léxico vetado;
  · **no bloquear lo inocuo**, porque un lint que rechaza todo obliga a apagarlo, y entonces no
    protege nada. El caso canónico: el nombre de {{TITULAR}}. `borde.clasificar()` lo trata como PII
    —correcto para mandar texto a un tercero— y heredarlo aquí habría bloqueado su propia web.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_lint as W  # noqa: E402
from _entorno import es_espejo  # noqa: E402


def solo_con_datos_reales(test, t):
    """El espejo público (tools/publicar.py) sustituye los nombres del caso por marcadores
    y reescribe el léxico vetado: «{{TITULAR}} es una ingeniera» llega allí como «{{TITULAR}} es
    una ingeniera». Justo lo que estos casos comprueban que se caza ya no está, así que en el
    espejo NO se pueden probar. Se saltan con el motivo dicho, no se dan por buenos en
    silencio: en casa base y en los worktrees, con los datos reales, se prueban de verdad.

    Se sabe por la marca del espejo (`es_espejo()`), no adivinando. Adivinar por `{{` dejaba
    fuera los casos sin nombre propio («según la ingeniera»), y el CI público estuvo rojo el
    21-22 y el 24-sep-26 con el código bien."""
    if es_espejo() or "{{" in t:
        test.skipTest("dato de prueba reescrito por el espejo público: %r" % t[:60])


WEB = "/Users/polaris/projects/titular-{{APELLIDO}}-case"


def _clases(texto):
    return {c for c, _m in W.revisar(texto)}


class NoDejaPasar(unittest.TestCase):

    def test_dosis_de_farmaco(self):
        """El hueco real de `borde`, verificado en vivo el 20-sep-26: «abemaciclib a 100 mg
        diarios» le pasaba como limpio. Publicar una pauta sin un médico detrás invita a que
        alguien se la aplique."""
        for t in ("Abemaciclib a 100 mg diarios desde el lunes pasado.",
                  "Le han puesto 2,5 mg cada 12 horas durante tres semanas.",
                  "Van seis ciclos de tratamiento y seguimos adelante con ello."):
            self.assertIn("clinico", _clases(t), t)

    def test_genes_variantes_y_marcadores(self):
        for t in ("El FGFR1 esta amplificado x13 segun el ultimo informe del hospital.",
                  "La variante RB1 p.V622Yfs*33 aparece en la ultima biopsia realizada.",
                  "El Ki67 ha bajado al 60% en la ultima revision que hemos tenido."):
            self.assertIn("clinico", _clases(t), t)

    def test_identificadores_directos(self):
        self.assertIn("pii", _clases("Escribeme a contacto@ejemplo.com si quieres ayudar."))

    def test_medicos_e_instituciones(self):
        for t in ("Hemos hablado con la Dra. {{CONTACTO}} sobre el siguiente paso a dar.",
                  "El bloque ya ha llegado a {{CENTRO}} esta misma semana pasada."):
            with self.subTest(t=t):
                solo_con_datos_reales(self, t)
                self.assertIn("terceros", _clases(t), t)

    def test_lexico_de_marca(self):
        for t in ("{{TITULAR}} es una ingeniera que construye su sistema.",
                  "Hoy hemos avanzado mucho con el proyecto {{CONTACTO}}."):
            with self.subTest(t=t):
                solo_con_datos_reales(self, t)
                self.assertIn("marca", _clases(t), t)

    def test_tells_de_ia(self):
        """Lo pidió `diseno`: un lint de palabras no caza la FORMA, y la forma también publica."""
        self.assertIn("voz", _clases("Esto no es un blog, es una bitacora de lo que pasa."))
        self.assertIn("voz", _clases("Hoy hemos avanzado — y mucho — con el tramite pendiente."))

    def test_un_texto_demasiado_corto_no_se_publica(self):
        self.assertIn("formato", _clases("ok"))


class NoBloqueaLoInocuo(unittest.TestCase):

    def test_el_nombre_de_titular_en_su_propia_web_es_normal(self):
        """`borde` lo marca como PII, y hace bien para egress a terceros. Heredarlo aquí habría
        bloqueado absolutamente todo: es SU web."""
        self.assertEqual([], W.revisar(
            "{{TITULAR}} ha presentado hoy la solicitud de la beca que preparaba."))

    def test_novedades_normales_del_proyecto(self):
        for t in ("Hoy hemos abierto la pagina de novedades y ya se puede leer entera.",
                  "Ya somos cuatro personas en la asociacion y seguimos sumando gente.",
                  "Hoy no hemos podido avanzar con el tramite, lo retomamos manana."):
            self.assertEqual([], W.revisar(t), t)

    def test_la_palabra_vacuna_ya_no_esta_vetada(self):
        """El veto se levantó el 29-7-26. Un lint que siga bloqueándola estaría aplicando una
        regla muerta, y eso también es un fallo."""
        self.assertEqual([], W.revisar(
            "Seguimos trabajando en el camino hacia la vacuna personalizada."))


class ContraElContenidoRealDeLaWeb(unittest.TestCase):
    """La prueba que de verdad vale: los ficheros que YA están publicados y que el carril
    automático no debe poder tocar nunca."""

    def setUp(self):
        if not os.path.isdir(WEB):
            self.skipTest("el repo de la web no está en esta máquina")

    def test_los_ficheros_clinicos_de_la_web_no_pasarian_el_lint(self):
        for nombre in ("timeline", "science", "team"):
            ruta = os.path.join(WEB, "content", "es", "%s.yml" % nombre)
            if not os.path.exists(ruta):
                continue
            with open(ruta, encoding="utf-8") as f:
                fallos = W.revisar(f.read())
            self.assertIn("clinico", {c for c, _ in fallos},
                          "%s.yml debería ser rechazado: está lleno de datos clínicos" % nombre)


class LaExcepcionDelPanelDatos(unittest.TestCase):
    """24-sep-2026. El panel /datos publica dato clínico A PROPÓSITO (lo pidió {{TITULAR}}), por la
    puerta `revisar_caso()`. Aquí se fija que esa puerta relaja lo clínico y las instituciones,
    y NADA más: terceros con nombre, claves administrativas y marca siguen cerrados."""

    def _caso(self, **extra):
        dato = {"valor": "Bloque del primario", "fuente": "ap", "sello": "verificado"}
        dato.update(extra)
        return {"fuentes": {"ap": {"publico": "Informes de anatomía patológica"}},
                "material": [dato]}

    def test_clinico_e_institucion_pasan_dentro_del_caso(self):
        self.assertEqual(W.revisar_caso(self._caso(donde="{{CENTRO}}", gen="ESR1 p.{{VARIANTE}}")), [])

    def test_terceros_admin_y_marca_siguen_cerrados(self):
        for extra, clase in (({"nota": "Lo corta la Dra. Pérez"}, "terceros"),
                             ({"nota": "episodio 12345678"}, "pii"),
                             ({"nota": "escribe a nadie@caso.example"}, "pii"),
                             ({"nota": "según la ingeniera"}, "marca")):
            with self.subTest(extra=extra):
                if clase in ("terceros", "marca"):     # el espejo reescribe nombres y léxico
                    solo_con_datos_reales(self, extra["nota"])
                self.assertIn(clase, {c for c, _ in W.revisar_caso(self._caso(**extra))}, extra)

    def test_sin_estructura_no_hay_excepcion(self):
        self.assertTrue(W.revisar_caso({"texto": "ESR1 p.{{VARIANTE}} en plasma"}))
        self.assertIn("estructura", {c for c, _ in W.revisar_caso(self._caso(sello="seguro"))})


if __name__ == "__main__":
    unittest.main(verbosity=2)
