#!/usr/bin/env python3
"""test_hoy_ruta_unica.py — el parte de HOY tiene UNA ruta, y los agentes la citan bien.

POR QUÉ (31-jul-2026). Ocho ficheros de agente citaban la ruta pelada `Gestion/HOY.md` como
destino del parte y —lo caro— de las PROPUESTAS «⏸️ NECESITO DE TI». El código real usa
`00_FUENTE-DE-VERDAD/Gestion/HOY.md`. Resultado: en la raíz del repo creció un huérfano de 61.621
bytes con notas apiladas desde el 13-jul que **nunca llegaron al parte que {{TITULAR}} lee**, mientras
el bueno seguía sano y todo parecía funcionar.

Es el peor modo de fallo del sistema: no perder la nota por un error visible, sino escribirla
con cuidado en un sitio que nadie mira. Un agente que deja ahí una decisión que espera su OK ha
hecho todo el trabajo y no ha avisado a nadie.

El test es un lint, no una prueba de comportamiento: la ruta se escribe en prosa dentro de los
prompts, así que lo único que se puede vigilar es que nadie vuelva a escribirla a medias.
"""
import glob
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUENA = "00_FUENTE-DE-VERDAD/Gestion/HOY.md"
# La ruta pelada, entre backticks: `Gestion/HOY.md` sin el prefijo de la fuente de verdad.
PELADA = re.compile(r"(?<!VERDAD/)`Gestion/HOY\.md`")


class RutaUnicaDeHoy(unittest.TestCase):
    def test_ningun_agente_cita_la_ruta_pelada(self):
        malos = []
        for p in sorted(glob.glob(os.path.join(ROOT, ".claude", "agents", "*.md"))):
            with open(p, encoding="utf-8") as f:
                for i, linea in enumerate(f, 1):
                    if PELADA.search(linea):
                        malos.append("%s:%d" % (os.path.basename(p), i))
        self.assertEqual([], malos,
                         "estos agentes mandarían el parte y las PROPUESTAS a un fichero "
                         "huérfano que {{TITULAR}} no lee: %s" % ", ".join(malos))

    def test_la_ruta_buena_sigue_siendo_la_que_usa_el_codigo(self):
        """Si alguien mueve la fuente de verdad, este lint deja de tener sentido y hay que verlo.

        Se comprueba contra el valor REAL que calcula `seguimiento.HOY`, no contra un literal:
        la ruta se construye con os.path.join, así que buscar la cadena entera daba un falso rojo.
        """
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        sys.modules.pop("seguimiento", None)
        import seguimiento
        self.assertTrue(seguimiento.HOY.endswith(os.path.join(*BUENA.split("/"))),
                        "seguimiento.HOY apunta a %s, no a %s: si la ruta cambió, cambia también "
                        "el lint y los prompts de los agentes" % (seguimiento.HOY, BUENA))


if __name__ == "__main__":
    unittest.main(verbosity=2)
