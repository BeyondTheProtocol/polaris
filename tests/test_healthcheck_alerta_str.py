#!/usr/bin/env python3
"""test_healthcheck_alerta_str.py — una cadena suelta no son N alertas de una letra.

19-sep-2026, fallo real encontrado en `tools/state/queue/failed/`: había encargos pidiendo
investigar «la alerta *a*» y «la alerta *c*». La causa es de manual: `_emitir_si_cambia` hace
`for a in alertas`, y si alguien pasa un `str` en vez de una lista, Python recorre la cadena
LETRA A LETRA. Cada carácter se convirtió en una clave de alerta, con su acuse y su encargo al
agente técnico — minutos de LLM por cada letra, y basura en la cola que luego alimenta la
alerta de «jobs caídos».

Se normaliza en la puerta: un str es UNA alerta.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import healthcheck as hc  # noqa: E402


class TestCadenaSuelta(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.encolados = []
        self._enc = hc._encolar_investigacion
        hc._encolar_investigacion = lambda clave, texto: self.encolados.append(clave) or "job1"
        self._state = hc.STATE if hasattr(hc, "STATE") else None

    def tearDown(self):
        hc._encolar_investigacion = self._enc

    def _claves(self, alertas):
        """Reproduce la normalización de la puerta, que es lo que se protege."""
        if isinstance(alertas, (str, bytes)):
            alertas = [alertas if isinstance(alertas, str) else alertas.decode("utf-8", "replace")]
        claves = []
        for a in alertas:
            claves.append(a[0] if isinstance(a, tuple) and len(a) == 2 else a)
        return claves

    def test_un_str_es_UNA_alerta_no_una_por_letra(self):
        self.assertEqual(self._claves("abc"), ["abc"])
        self.assertEqual(len(self._claves("el daemon x fallo")), 1)

    def test_las_listas_siguen_funcionando_igual(self):
        self.assertEqual(self._claves(["uno", "dos"]), ["uno", "dos"])
        self.assertEqual(self._claves([("clave", "texto visible")]), ["clave"])

    def test_el_codigo_normaliza_antes_del_bucle(self):
        fuente = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "tools", "healthcheck.py"), encoding="utf-8").read()
        i_norm = fuente.index("if isinstance(alertas, (str, bytes)):")
        i_bucle = fuente.index("for a in alertas:", i_norm - 3000)
        self.assertLess(i_norm, i_bucle, "la normalización va ANTES del bucle que itera")


class TestClaveSinNumeros(unittest.TestCase):
    """La clave de una alerta no puede llevar contadores: cada número nuevo era una alerta nueva.

    19-sep-2026: `frescura_otro:` se construía con los primeros 40 caracteres del aviso, que
    incluyen «2 rama(s) … (7 commit[s])». Con seis variantes vivas del MISMO problema había seis
    alertas, seis acuses, seis encargos y seis entradas en el libro de deuda."""

    def test_el_mismo_aviso_con_otros_numeros_es_la_misma_clave(self):
        claves = {hc._clave_frescura(a) for a in (
            "🌿 2 rama(s) con trabajo SIN fusionar (7 commit[s]) y sin sesión",
            "🌿 5 rama(s) con trabajo SIN fusionar (32 commit[s]) y sin sesión",
            "🌿 11 rama(s) con trabajo SIN fusionar (100 commit[s]) y sin sesión")}
        self.assertEqual(len(claves), 1, claves)

    def test_condiciones_distintas_siguen_separadas(self):
        self.assertNotEqual(hc._clave_frescura("🌿 2 rama(s) sin fusionar"),
                            hc._clave_frescura("💾 la copia de seguridad no corre"))


if __name__ == "__main__":
    unittest.main()
