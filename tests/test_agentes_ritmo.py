#!/usr/bin/env python3
"""test_agentes_ritmo.py — cada agente declara CADA CUÁNTO se espera que trabaje.

19-sep-2026, del plan de gestión de agentes. El problema que cierra: con 33 agentes, un cero
en el informe de uso no se podía interpretar. `constructor` a cero es correcto —solo despierta
cuando se monta una caja nueva— y `orquestador` a cero habría sido una alarma. Los dos se veían
igual, y con esa ambigüedad se llegó a proponer retirar al que corre cada mañana.

`estado:` (activo|borrador|archivado) ya existía y lo valida `audit_comites.py`: es el ciclo de
vida DOCUMENTAL de la ficha. `ritmo:` es el otro eje, el de OPERACIÓN:

  · permanente — lo llama una rutina o el lazo; si marca cero, es un fallo
  · a-demanda  — duerme hasta que aparece su caso; el cero es lo normal
  · estacional — tiene temporada (un lanzamiento, un viaje, una entrevista)
  · dormido    — debería usarse y no se usa. NO es un estado estable: exige `porque:`

Dos ejes separados a propósito: una ficha puede estar `activo` y `a-demanda` a la vez, y eso
no es una contradicción — es la cola larga del catálogo, que es sana.
"""
import os
import re
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTES = os.path.join(RAIZ, ".claude", "agents")
VALIDOS = {"permanente", "a-demanda", "estacional", "dormido"}


def _frontmatter(ruta):
    with open(ruta, encoding="utf-8") as fh:
        texto = fh.read()
    campos = {}
    for linea in texto.splitlines():
        if linea.strip() == "---" and campos:
            break
        m = re.match(r"^([a-z_]+):\s*(.*)$", linea)
        if m:
            campos[m.group(1)] = m.group(2).strip()
    return campos


def _fichas():
    return [os.path.join(AGENTES, f) for f in sorted(os.listdir(AGENTES)) if f.endswith(".md")]


class TestRitmoDeclarado(unittest.TestCase):
    def test_todas_las_fichas_declaran_un_ritmo_valido(self):
        malas = []
        for ruta in _fichas():
            r = _frontmatter(ruta).get("ritmo")
            if r not in VALIDOS:
                malas.append("%s → %r" % (os.path.basename(ruta), r))
        self.assertEqual(malas, [], "ritmo ausente o inválido: %s" % malas)

    def test_un_dormido_tiene_que_explicarse(self):
        """`dormido` es un fallo declarado, no una etiqueta para aparcar algo sin pensarlo."""
        sin_motivo = []
        for ruta in _fichas():
            campos = _frontmatter(ruta)
            if campos.get("ritmo") == "dormido" and not campos.get("porque"):
                sin_motivo.append(os.path.basename(ruta))
        self.assertEqual(sin_motivo, [], "dormidos sin `porque:`: %s" % sin_motivo)

    def test_el_otro_eje_sigue_intacto(self):
        """`estado:` lo valida audit_comites; `ritmo:` no lo sustituye ni lo pisa."""
        for ruta in _fichas():
            campos = _frontmatter(ruta)
            self.assertIn(campos.get("estado"), ("activo", "borrador", "archivado"),
                          "%s perdió su estado" % os.path.basename(ruta))

    def test_hay_de_los_tres_ritmos_vivos(self):
        ritmos = {_frontmatter(r).get("ritmo") for r in _fichas()}
        self.assertTrue({"permanente", "a-demanda"} <= ritmos)


if __name__ == "__main__":
    unittest.main()
