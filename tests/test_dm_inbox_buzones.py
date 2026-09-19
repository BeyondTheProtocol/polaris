#!/usr/bin/env python3
"""Test: dm-inbox no puede volver a ignorar un buzón.

POR QUÉ EXISTE (2-sep-2026, deuda `dm-inbox-buzon-titular-mgp-no-barrido`).

El recogedor deja los avisos de IG y LinkedIn en `tools/state/correo/`, y hay TRES ficheros
ahí, no uno. La ficha del agente nombraba solo `buzon.json`, así que
`buzon-titular-mgp-gmail-com.json` llevaba **35 pasadas sin leerse** desde el 27-jun. No estaba
vacío: usa un rango de UID distinto (90000+ frente a 900-1000) y trae avisos de LinkedIn que
los otros dos no ven. Ahí apareció un DM de Alejandra Medina-Rivera del 1-ago que nadie habría
leído.

Un DM de LinkedIn es una vía de contacto hacia acceso. Perder uno cuesta más que cualquier
falso positivo, y perderlo en silencio durante cinco semanas es exactamente lo que este test
existe para impedir.
"""

import glob
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FICHA = os.path.join(ROOT, ".claude", "agents", "dm-inbox.md")
BUZONES = os.path.join(ROOT, "tools", "state", "correo")


class DmInboxLeeTodosLosBuzones(unittest.TestCase):
    def test_la_ficha_nombra_los_tres_buzones(self):
        s = io.open(FICHA, encoding="utf-8").read()
        for fichero in ("buzon.json", "buzon-titular-gmail-com.json",
                        "buzon-titular-mgp-gmail-com.json"):
            self.assertIn(fichero, s,
                          "la ficha de dm-inbox no nombra %s. Si no está escrito, el agente no "
                          "lo lee, y ese buzón lleva DMs de LinkedIn que los otros no traen."
                          % fichero)

    def test_no_hay_buzones_en_disco_que_la_ficha_ignore(self):
        """El guardia de verdad: si mañana aparece un cuarto fichero, esto lo canta."""
        if not os.path.isdir(BUZONES):
            self.skipTest("no hay directorio de buzones en esta máquina")
        en_disco = {os.path.basename(p) for p in glob.glob(os.path.join(BUZONES, "buzon*.json"))}
        if not en_disco:
            self.skipTest("no hay buzones recogidos todavía")
        s = io.open(FICHA, encoding="utf-8").read()
        ignorados = sorted(f for f in en_disco if f not in s)
        self.assertEqual(
            ignorados, [],
            "hay buzones en disco que la ficha de dm-inbox NO nombra: %s\n"
            "Añádelos a la ficha o se barrerán en silencio, como pasó 35 pasadas seguidas con "
            "buzon-titular-mgp-gmail-com.json." % ignorados)

    def test_la_ficha_sigue_prohibiendo_entrar_en_las_redes(self):
        """Regresión del muro: se leen los AVISOS por email, nunca se entra a IG/LinkedIn."""
        s = io.open(FICHA, encoding="utf-8").read()
        self.assertRegex(s, r"NO entras en IG ni LinkedIn",
                         "la ficha ha perdido la prohibición de entrar en las redes: scrapear "
                         "su sesión le banea la cuenta")


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=0).run(
        unittest.TestLoader().loadTestsFromTestCase(DmInboxLeeTodosLosBuzones))
    if res.wasSuccessful():
        print("✅ DM-INBOX EN VERDE (%d casos · ningún buzón se queda sin leer)" % res.testsRun)
    raise SystemExit(0 if res.wasSuccessful() else 1)
