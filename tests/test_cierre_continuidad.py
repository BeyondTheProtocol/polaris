#!/usr/bin/env python3
"""Test: cerrar una sesión sin dejar memoria de lo que pasó tiene que avisar.

POR QUÉ EXISTE (3-sep-2026). {{TITULAR}} preguntó por qué no se guarda lo importante de cada sesión.
El mecanismo existía (`tools/continuity.py`) y el ritual de cierre no lo tocaba: commiteaba,
fusionaba, reindexaba el RAG y podaba el worktree sin dejar una sola línea de QUÉ había pasado.

Medido ese día: la última entrada de continuidad era del **27-jul**. Treinta y ocho días
cerrando sesiones con normalidad y sin memoria. Todo lo aprendido quedaba solo en las
transcripciones, que nadie relee y que además se borran — la sesión que produjo el paquete de
FGFR4 desapareció, y con ella el entregable que esperaba un gate.

El aviso NO bloquea el cierre. Bloquear por una nota sería peor que la enfermedad. Lo que hace
es que deje de ser invisible.
"""

import os
import sys
import time
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))


def _modulo_con_repo(raiz):
    """Recarga cerrar_sesion apuntando BASE a una raíz de mentira."""
    os.environ["BTP_REPO"] = raiz
    for m in ("cerrar_sesion",):
        sys.modules.pop(m, None)
    import cerrar_sesion
    return cerrar_sesion


class ElCierreAvisaSiNoHayMemoria(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("BTP_REPO", None)
        sys.modules.pop("cerrar_sesion", None)

    def _raiz(self, edad_dias):
        d = tempfile.mkdtemp()
        cont = os.path.join(d, "tools", "state", "continuity")
        os.makedirs(cont)
        idx = os.path.join(cont, "INDEX.md")
        open(idx, "w").write("# memoria de mentira\n")
        cuando = time.time() - edad_dias * 86400
        os.utime(idx, (cuando, cuando))
        return d

    def test_memoria_vieja_avisa_y_dice_cuantos_dias(self):
        c = _modulo_con_repo(self._raiz(38))
        al_dia, dias, _ = c._continuidad_al_dia()
        self.assertFalse(al_dia, "38 días sin memoria tenían que dar aviso")
        self.assertGreater(dias, 37)
        linea = c._una_linea({"aplicado": True, "rama": "x", "sin_commitear": [],
                              "docs_nuevos": [], "fusionado": True, "podado": True})
        self.assertIn("MEMORIA DE SESIÓN sin escribir", linea)
        self.assertIn("continuity.py record", linea,
                      "el aviso tiene que decir el comando exacto, o no se usa")

    def test_memoria_de_hoy_no_molesta(self):
        c = _modulo_con_repo(self._raiz(0))
        al_dia, _, _ = c._continuidad_al_dia()
        self.assertTrue(al_dia)
        linea = c._una_linea({"aplicado": True, "rama": "x", "sin_commitear": [],
                              "docs_nuevos": [], "fusionado": True, "podado": True})
        self.assertNotIn("MEMORIA DE SESIÓN", linea,
                         "si la memoria está al día no se avisa: un aviso que sale siempre se "
                         "vuelve invisible")

    def test_el_aviso_no_bloquea_el_cierre(self):
        """Sale como texto en el resumen, no como excepción ni como código de error."""
        c = _modulo_con_repo(self._raiz(99))
        linea = c._una_linea({"aplicado": True, "rama": "x", "sin_commitear": [],
                              "docs_nuevos": [], "fusionado": True, "podado": True})
        self.assertTrue(linea.startswith("✅"),
                        "el cierre sigue siendo un éxito; el aviso va detrás, no lo tumba")

    def test_sin_directorio_de_continuidad_no_revienta(self):
        """Fail-soft: es un aviso, no un guardia. Ante la duda, no molesta."""
        c = _modulo_con_repo(tempfile.mkdtemp())
        al_dia, dias, ultima = c._continuidad_al_dia()
        self.assertIn(ultima, ("nunca", "?"))


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=0).run(
        unittest.TestLoader().loadTestsFromTestCase(ElCierreAvisaSiNoHayMemoria))
    if res.wasSuccessful():
        print("✅ CIERRE/CONTINUIDAD EN VERDE (%d casos · cerrar sin memoria avisa, y no bloquea)"
              % res.testsRun)
    raise SystemExit(0 if res.wasSuccessful() else 1)
