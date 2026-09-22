#!/usr/bin/env python3
"""test_modelo_coherente.py — el modelo que declara la ficha y el que corre son el mismo.

19-sep-2026, el fallo que pagamos en carne propia. `audit_agentes.py` destapó que el
`BTP_MODEL` del plist pisa el `model:` del frontmatter, sin avisar a nadie:

  · `auto-mejora` y `orquestador` declaraban **opus** y corrían en **sonnet**
  · `git`, `periodista` y `prensa` declaraban **sonnet** y corrían en **haiku**

Dos meses así. Una ficha que miente sobre su cerebro es peor que no declararlo: se toman
decisiones —«esto lo hace el agente bueno»— sobre algo que no es verdad. Se resolvió caso por
caso (subiendo el plist donde mandaba la calidad, sincerando la ficha donde mandaba la
realidad) y esto impide que vuelva a separarse en silencio.
"""
import os
import plistlib
import re
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTES = os.path.join(RAIZ, ".claude", "agents")
LAUNCHD = os.path.join(RAIZ, "tools", "launchd")


def _modelo_de_ficha(slug):
    ruta = os.path.join(AGENTES, slug + ".md")
    if not os.path.exists(ruta):
        return None
    with open(ruta, encoding="utf-8") as fh:
        for linea in fh:
            m = re.match(r"^model:\s*(\S+)", linea)
            if m:
                return m.group(1).strip()
    return None


def _plists():
    """[(fichero, agente, modelo)] de los plists que fijan los dos."""
    out = []
    if not os.path.isdir(LAUNCHD):
        return out
    for fn in sorted(os.listdir(LAUNCHD)):
        if not fn.endswith(".plist"):
            continue
        try:
            with open(os.path.join(LAUNCHD, fn), "rb") as fh:
                d = plistlib.load(fh)
        except Exception:
            continue
        env = d.get("EnvironmentVariables") or {}
        ag, mod = env.get("BTP_AGENT"), env.get("BTP_MODEL")
        if ag and mod:
            out.append((fn, ag, mod))
    return out


class TestModeloCoherente(unittest.TestCase):
    def test_ficha_y_plist_dicen_lo_mismo(self):
        desajustes = []
        for fn, agente, modelo_plist in _plists():
            modelo_ficha = _modelo_de_ficha(agente)
            if modelo_ficha is None:
                continue          # agentes sin ficha propia (p. ej. alias) no aplican
            if modelo_ficha.lower() != modelo_plist.lower():
                desajustes.append("%s: ficha dice %s, %s corre en %s"
                                  % (agente, modelo_ficha, fn, modelo_plist))
        self.assertEqual(desajustes, [],
                         "el plist pisa la ficha sin que nadie se entere:\n  " +
                         "\n  ".join(desajustes))

    def test_hay_plists_que_comprobar(self):
        """Si esta lista se queda vacía, el test de arriba pasa sin mirar nada."""
        self.assertGreater(len(_plists()), 3)


if __name__ == "__main__":
    unittest.main()
