#!/usr/bin/env python3
"""test_healthcheck_drive.py — el permiso de escritura de Drive no puede caducar en silencio.

Nace del 11-sep-2026: `drive.py` dio `invalid_grant` a mitad de reorganizar el historial y
nadie lo había visto, porque la lectura (cuenta de servicio) seguía sana. Sin red: el doctor
se sustituye por un falso que devuelve la salida que toque.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import healthcheck as hc   # noqa: E402

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


class _R:
    def __init__(self, out):
        self.stdout = out.encode("utf-8")
        self.returncode = 0 if "✗" not in out else 1


def _doctor(out):
    llamadas = []

    def run(cmd, **kw):
        llamadas.append(cmd)
        return _R(out)
    return run, llamadas


CADUCADO = "✅ Service Account (lectura): auth OK\n✗ %s/revocado.\n" % hc.DRIVE_MARCA_CADUCADO
SANO = "✅ Service Account (lectura): auth OK\n✅ OAuth de usuario (escritura): OK\n"

with tempfile.TemporaryDirectory() as d:
    run, ll = _doctor(CADUCADO)
    alertas, info = hc._check_drive_oauth(run=run, state_dir=d)
    check("token caducado → alerta con clave estable", [a[0] for a in alertas] == ["drive_oauth_caducado"])
    check("el aviso dice el arreglo concreto", "drive.py auth" in alertas[0][1])
    check("el aviso nombra la causa de fondo (modo Testing → producción)", "producción" in alertas[0][1])
    check("pregunta al doctor de drive.py", ll and ll[0][-1] == "doctor")

    run2, ll2 = _doctor(SANO)
    alertas2, info2 = hc._check_drive_oauth(run=run2, state_dir=d)
    check("dentro del throttle no vuelve a sondear", ll2 == [] and info2.get("throttled"))
    check("dentro del throttle mantiene la alerta viva (no parece arreglado)", len(alertas2) == 1)

with tempfile.TemporaryDirectory() as d:
    run, _ = _doctor(SANO)
    alertas, info = hc._check_drive_oauth(run=run, state_dir=d)
    check("token sano → sin alerta", alertas == [] and info["caducado"] is False)

with tempfile.TemporaryDirectory() as d:
    run, _ = _doctor("⛔ HALT activo: drive.py bloqueado\n")
    alertas, _ = hc._check_drive_oauth(run=run, state_dir=d)
    check("HALT u otro fallo no se confunde con caducado", alertas == [])

    def revienta(cmd, **kw):
        raise OSError("sin red")
    with tempfile.TemporaryDirectory() as d2:
        alertas, info = hc._check_drive_oauth(run=revienta, state_dir=d2)
        check("si el doctor revienta, no grita y lo deja en el log técnico", alertas == [] and "error" in info)

fuente = open(os.path.join(ROOT, "tools", "healthcheck.py"), encoding="utf-8").read()
check("main() engancha el check", "_check_drive_oauth()" in fuente)

print("test_healthcheck_drive: %d OK, %d fallos" % (_pass, _fail))
sys.exit(1 if _fail else 0)
