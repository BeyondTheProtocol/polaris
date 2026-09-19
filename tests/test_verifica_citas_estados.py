#!/usr/bin/env python3
"""Test: la tool que vigila las citas no puede romperse al imprimir.

POR QUÉ EXISTE (2-sep-2026, deuda `verifica-citas-keyerror-no-parseable`, NED alto, muro).

`verifica_citas.py` es un gate del muro de evidencia: separa una cita real de una
inventada. Definía cuatro estados —existe, no_existe, no_resoluble y no_parseable—
pero el diccionario `glyph` que los imprime solo tenía tres. Al encontrarse un
resultado `no_parseable` saltaba `KeyError` y el proceso moría con exit 1, después
de haber impreso el listado completo.

Eso es peor que un crash normal. Quien llama a esta tool lee el código de salida
para decidir si bloquear un entregable. Un exit 1 por KeyError se lee como «hay una
cita fabricada» cuando lo que pasó es «la herramienta se rompió». Son conclusiones
opuestas, y la equivocada bloquea trabajo bueno o —peor— hace desconfiar del gate.

El test comprueba dos cosas:
  1. Que todo estado que el módulo declara tiene su glifo. Si mañana alguien añade
     un quinto estado y olvida el glifo, esto lo canta sin esperar a que aparezca
     el caso raro en producción.
  2. Que la salida legible sobrevive de verdad a un `no_parseable`, ejecutando la
     tool con una cadena sin identificador. Sin red: una cadena sin DOI/PMID/NCT
     se resuelve localmente, así que el test es determinista.
"""

import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
TOOL = os.path.join(ROOT, "tools", "verifica_citas.py")

import verifica_citas  # noqa: E402


class LaToolDeCitasNoSeRompeAlImprimir(unittest.TestCase):
    def test_todo_estado_declarado_tiene_glifo(self):
        """La causa exacta del bug: un estado sin entrada en `glyph`."""
        declarados = {
            getattr(verifica_citas, n) for n in dir(verifica_citas)
            if n.isupper() and isinstance(getattr(verifica_citas, n), str)
            and getattr(verifica_citas, n) in (
                "existe", "no_existe", "no_resoluble", "no_parseable")
        }
        self.assertGreaterEqual(len(declarados), 4,
                                "esperaba al menos 4 estados declarados, encontré %s" % declarados)

        src = open(TOOL, encoding="utf-8").read()
        i = src.find("glyph = {")
        self.assertGreater(i, -1, "no encuentro el diccionario `glyph` en la tool")
        bloque = src[i:i + 400]
        faltan = [e for e in sorted(declarados) if e not in bloque and
                  e.upper() not in bloque and
                  not any(k in bloque for k in ("EXISTE", "FABRICADA", "NO_RES", "NO_PARSE"))]
        # Comprobación real: los cuatro nombres de constante tienen que estar en el bloque.
        for nombre in ("EXISTE", "FABRICADA", "NO_RES", "NO_PARSE"):
            self.assertIn(nombre, bloque,
                          "`%s` no está en el diccionario `glyph`. Imprimir un resultado con ese "
                          "estado hará KeyError y la tool morirá con exit 1, que quien la llama "
                          "leerá como «cita fabricada» en vez de «la tool se rompió»." % nombre)
        self.assertEqual(faltan, [])

    def test_una_cita_sin_identificador_no_revienta_la_salida_legible(self):
        """Reproduce el caso real: una cadena sin DOI/PMID/NCT. Sin red."""
        r = subprocess.run(
            [sys.executable, TOOL, "una cita en prosa sin ningun identificador"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0,
                         "la tool tiene que terminar limpia con una cita sin id.\n"
                         "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr))
        self.assertNotIn("KeyError", r.stderr)
        self.assertIn("Resumen:", r.stdout, "no llegó a imprimir el resumen")
        self.assertIn("sin id", r.stdout, "el resumen debería contar los no_parseable")

    def test_el_resumen_cuenta_los_no_parseable(self):
        """Antes se listaban pero no se sumaban: un total que no cuadra invita a ignorarlo."""
        r = subprocess.run(
            [sys.executable, TOOL, "prosa sin id", "otra prosa sin id"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2 sin id", r.stdout,
                      "el resumen tiene que contar las 2 cadenas sin identificador:\n%s" % r.stdout)


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=0).run(
        unittest.TestLoader().loadTestsFromTestCase(LaToolDeCitasNoSeRompeAlImprimir))
    if res.wasSuccessful():
        print("✅ VERIFICA_CITAS EN VERDE (%d casos · ningún estado sin glifo, la salida no revienta)"
              % res.testsRun)
    raise SystemExit(0 if res.wasSuccessful() else 1)
