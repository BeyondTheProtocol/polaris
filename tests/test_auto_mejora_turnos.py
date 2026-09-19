#!/usr/bin/env python3
"""test_auto_mejora_turnos.py — el presupuesto de turnos de auto-mejora no puede quedarse corto.

POR QUÉ EXISTE (13-sep-2026, deuda `daemon_fallando:com.btp.auto-mejora`, 4 detecciones en 46
días, remitida 3 veces como "intermitente" — a veces cabía, a veces no).

Diagnóstico en vivo el 13-sep-2026: la pasada de las 05:08 corrió 14 minutos y terminó con
`is_error:true, subtype:"error_max_turns", num_turns:81` (coste 7,61 USD; sesión
75f15716-593c-4a31-bb5e-41488ecfc92d, log tools/launchd/logs/auto-mejora.out) — agotó el tope
de `BTP_MAX_TURNS=80` sin acabar el barrido diario (KPI, deuda, normas, 6 audits, salud,
healthcheck, memoria_radar, cosecha de correcciones/entregables, reglas repetidas, radar del
taller y de {{CONTACTO}}, x_guardados, grok, prensa, kb.py index... más de 20 pasos, 99 tool-calls en
81 turnos).

No es la primera vez que este tope se queda corto: el plist llevaba 45 turnos hace poco (ver
`tools/launchd/com.btp.auto-mejora.plist.bak`) y ya no bastó; se subió a 80 y TAMPOCO bastó.
Cada subida tímida reabre la misma deuda. Este test fija un SUELO con margen real (no otro
parche de +10) y, sobre todo, evita que alguien lo vuelva a bajar sin darse cuenta.

Esto NO prueba que el nuevo tope baste para siempre (el barrido crece cada día); es una defensa
de regresión contra volver a un valor que YA demostró no bastar, no una garantía de que el
daemon nunca vuelva a agotar turnos.
"""
import os
import plistlib
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLIST = os.path.join(ROOT, "tools", "launchd", "com.btp.auto-mejora.plist")

# Suelo, no el valor real del plist (que puede subir más): 120 dobla holgadamente el último
# valor que demostró no bastar (80) y deja margen sobre lo observado en vivo (81 turnos,
# incompleto).
SUELO_TURNOS = 120


def _max_turns(ruta):
    with open(ruta, "rb") as f:
        data = plistlib.load(f)
    valor = data.get("EnvironmentVariables", {}).get("BTP_MAX_TURNS")
    return int(valor) if valor is not None else None


class AutoMejoraNoSeQuedaCortaDeTurnos(unittest.TestCase):
    def test_el_plist_existe_y_es_valido(self):
        self.assertTrue(os.path.exists(PLIST), "falta %s" % PLIST)
        r = subprocess.run(["plutil", "-lint", PLIST], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0,
                         "%s no es un plist válido:\n%s" % (PLIST, r.stdout or r.stderr))

    def test_btp_max_turns_tiene_margen_real(self):
        turnos = _max_turns(PLIST)
        self.assertIsNotNone(turnos, "el plist no fija BTP_MAX_TURNS")
        self.assertGreaterEqual(
            turnos, SUELO_TURNOS,
            "BTP_MAX_TURNS=%s en el plist instalado. La pasada real del 13-sep-2026 agotó "
            "80 turnos (rc=1, error_max_turns, 81 turnos, sesión "
            "75f15716-593c-4a31-bb5e-41488ecfc92d) sin acabar el barrido. Bajar de %d "
            "reabre la misma deuda que ya escaló 4 veces." % (turnos, SUELO_TURNOS))

    def test_canario_el_valor_que_fallo_hoy_no_pasa(self):
        """80 es justo el valor que agotó turnos hoy. El suelo tiene que pillarlo, o es
        decorativo (regla del canario: un test que no puede ir ROJO no prueba nada)."""
        with tempfile.NamedTemporaryFile(suffix=".plist", delete=False) as f:
            ruta = f.name
        try:
            with open(ruta, "wb") as fh:
                plistlib.dump({"EnvironmentVariables": {"BTP_MAX_TURNS": "80"}}, fh)
            turnos = _max_turns(ruta)
            self.assertEqual(turnos, 80)
            self.assertLess(turnos, SUELO_TURNOS,
                            "el suelo (%d) ya no distingue el valor que falló en vivo (80)" % SUELO_TURNOS)
        finally:
            os.unlink(ruta)


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=0).run(
        unittest.TestLoader().loadTestsFromTestCase(AutoMejoraNoSeQuedaCortaDeTurnos))
    if res.wasSuccessful():
        print("✅ AUTO-MEJORA EN VERDE (%d casos · BTP_MAX_TURNS con margen real)" % res.testsRun)
    raise SystemExit(0 if res.wasSuccessful() else 1)
