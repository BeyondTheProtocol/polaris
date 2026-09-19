#!/usr/bin/env python3
"""test_correo_smtp_gate.py — el gate humano de correo_smtp.py.

Garantía que se protege aquí: un AGENTE NO PUEDE ENVIAR CORREO.
El envío real exige teclear ENVIAR en una terminal real (/dev/tty). Un agente corre sin
TTY, así que muere antes de abrir SMTP.

  · enviar(dry_run=False) sin human_ok  → RuntimeError, sin tocar la red.
  · enviar(dry_run=True)                → construye el MIME, no conecta.
  · --send sin TTY (como en Bash de un agente) → sale con error, no envía.
  · _confirmar_en_tty aborta si la palabra tecleada no es exactamente ENVIAR.

Contexto: 8-jul-2026 envié un correo a su oncólogo interpretando «mándalo» como permiso.
Regla de {{TITULAR}}: por defecto TODO correo se queda en borrador (tools/correo_outbox.py).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")
import os
import subprocess
import sys
import unittest

_here = os.path.dirname(os.path.abspath(__file__))
_repo = os.path.dirname(_here)
sys.path.insert(0, os.path.join(_repo, "tools"))

import correo_smtp  # noqa: E402


class TestGateHumano(unittest.TestCase):

    def test_api_sin_human_ok_no_envia(self):
        """dry_run=False sin human_ok debe abortar ANTES de abrir SMTP."""
        with self.assertRaises(RuntimeError) as ctx:
            correo_smtp.enviar(to=["a@b.com"], subject="x", body="y", dry_run=False)
        self.assertIn("BLOQUEADO", str(ctx.exception))

    def test_dry_run_no_conecta(self):
        """El dry-run construye el MIME y devuelve resumen sin red."""
        r = correo_smtp.enviar(to=["a@b.com"], subject="x", body="y", dry_run=True)
        self.assertTrue(r["dry_run"])
        self.assertEqual(r["to"], ["a@b.com"])

    def test_cli_send_sin_tty_falla(self):
        """--send desde un proceso sin TTY (un agente) no envía."""
        cuerpo = os.path.join(_here, "fixtures", "_gate_body.txt")
        os.makedirs(os.path.dirname(cuerpo), exist_ok=True)
        with open(cuerpo, "w") as f:
            f.write("cuerpo de prueba\n")
        try:
            p = subprocess.run(
                [sys.executable, os.path.join(_repo, "tools", "correo_smtp.py"),
                 "--to", "a@b.com", "--subject", "x", "--body-file", cuerpo, "--send"],
                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30,
            )
            self.assertNotEqual(p.returncode, 0, "el envío sin TTY debería fallar")
            self.assertIn("BLOQUEADO", p.stderr)
        finally:
            os.path.exists(cuerpo) and os.remove(cuerpo)

    def test_no_hay_bypass_por_variable_de_entorno(self):
        """Ninguna env var debe conceder human_ok: el único camino es el TTY."""
        os.environ["BTP_SEND_OK"] = "1"
        os.environ["CORREO_SMTP_FORCE"] = "1"
        try:
            with self.assertRaises(RuntimeError):
                correo_smtp.enviar(to=["a@b.com"], subject="x", body="y", dry_run=False)
        finally:
            os.environ.pop("BTP_SEND_OK", None)
            os.environ.pop("CORREO_SMTP_FORCE", None)

    def test_palabra_de_confirmacion_es_exacta(self):
        self.assertEqual(correo_smtp.PALABRA_CONFIRMACION, "ENVIAR")


if __name__ == "__main__":
    unittest.main(verbosity=2)
