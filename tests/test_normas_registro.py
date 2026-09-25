#!/usr/bin/env python3
"""test_normas_registro.py — el registro de normas cuadra con las memorias, y no canta falso verde.

POR QUÉ EXISTE (31-jul-2026, Fase 0 de `normas_sin_clasificar`). Dos cosas se rompieron a la vez
al meter las 169 normas que faltaban:

  1. **El conteo salía de una RESTA** (197 memorias − 29 en registro), no de un join. Cualquier
     renombrado descuadraba el número sin avisar a nadie, y de hecho había una entrada fantasma
     en el registro sin fichero de memoria detrás.
  2. **`estado()` contaba `aplicable` en vez de `mecanismo`.** Con 29 normas el error pasaba
     desapercibido; con 198 el registro cantó «189 de 198 con mecanismo» cuando solo 23 lo
     tienen. Eso es FALSO VERDE — exactamente lo que este registro existe para evitar.

Este test es el freno de las dos. Con él, la deuda `normas_sin_clasificar` se puede cerrar: no
porque estén mecanizadas (no lo están: 175 siguen sin mecanismo, a propósito y visible), sino
porque **ninguna puede volver a quedarse fuera del registro sin que la suite se entere**.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("nombres")
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import normas  # noqa: E402


class ElRegistroCuadraConLasMemorias(unittest.TestCase):
    def setUp(self):
        self.d = normas.cargar()
        self.slugs = {n["slug"] for n in self.d["normas"]}
        self.memorias = set(normas._memorias_feedback())

    def test_ninguna_memoria_se_queda_fuera(self):
        """El hueco que abrió la deuda. Una norma suya fuera del registro es una norma invisible.

        Con margen para las recién escritas (normas.GRACIA_SIN_CLASIFICAR_S): se LISTAN aquí, así
        que no son invisibles, pero no rompen la batería de quien no las escribió. Pasado el
        margen, falla como siempre."""
        pendientes, vencidas = normas.sin_clasificar(self.d)
        if pendientes:
            print("\n  ⏳ memorias feedback recién escritas, pendientes de clasificar (%d h de margen): %s"
                  % (normas.GRACIA_SIN_CLASIFICAR_S // 3600, ", ".join(pendientes)))
        self.assertEqual(vencidas, [], "memorias `feedback` sin clasificar en el registro: %s" % vencidas)

    def test_ninguna_entrada_fantasma(self):
        """Al revés: un slug en el registro sin memoria detrás descuadra el conteo en silencio."""
        fantasmas = sorted(s for s in self.slugs - self.memorias if s.startswith("feedback-"))
        self.assertEqual(fantasmas, [], "slugs en el registro sin fichero de memoria: %s" % fantasmas)

    def test_el_conteo_no_es_una_resta(self):
        e = normas.estado(self.d)
        pendientes, _ = normas.sin_clasificar(self.d)
        self.assertEqual(e["en_registro"], len(self.slugs))
        self.assertGreaterEqual(e["en_registro"] + len(pendientes), e["memorias_feedback"],
                                "el registro tiene que cubrir TODAS las memorias feedback "
                                "(salvo las recién escritas, que se listan arriba)")


class NadaDeFalsoVerde(unittest.TestCase):
    def setUp(self):
        self.d = normas.cargar()
        self.e = normas.estado(self.d)

    def test_con_mecanismo_cuenta_mecanismos_no_flags(self):
        """`aplicable` dice si la norma viene al caso; NO si hay algo que la frene."""
        declarados = [n for n in self.d["normas"] if n.get("mecanismo")]
        self.assertLessEqual(self.e["con_mecanismo_aplicable"], len(declarados),
                             "se están contando como mecanizadas normas sin mecanismo declarado")

    def test_las_de_fase_0_no_se_cuentan_como_mecanizadas(self):
        sin_mec = [n for n in self.d["normas"] if not n.get("mecanismo")]
        self.assertGreater(len(sin_mec), 100, "la Fase 0 metió las normas sin mecanizar: deben verse")
        for n in sin_mec:
            self.assertFalse(n.get("auditable"),
                             "%s no tiene mecanismo: no puede declararse auditable" % n["slug"])

    def test_todo_mecanismo_declarado_existe(self):
        self.assertEqual(self.e["mecanismos_que_faltan"], [],
                         "hay normas que declaran un mecanismo que no está en el repo")

    def test_toda_norma_tiene_clase_valida(self):
        for n in self.d["normas"]:
            self.assertIn(n.get("clase"), normas.CLASES, n["slug"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
