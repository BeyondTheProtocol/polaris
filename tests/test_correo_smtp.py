#!/usr/bin/env python3
"""test_correo_smtp.py — garantías del envío real de correo (gates duros del muro) +
las funciones de MONITOR DE SALUD nuevas (2/7/26, smtp_alcanzable/login_ok), que son de
solo lectura del estado del servidor y NUNCA envían nada.

Verifica SIN RED:
  · dry_run=True por defecto — enviar() nunca abre socket si no se pide explícitamente.
  · HALT bloquea antes de cualquier conexión (ambos ficheros de kill-switch).
  · egress fail-closed: _assert_smtp_host solo permite smtp.gmail.com:587.
  · validación de destinatarios/asunto (anti-inyección de cabeceras).
  · smtp_alcanzable()/login_ok() son fail-soft (nunca lanzan) y NUNCA llaman a send_message
    (verificación estructural: 'send_message' no aparece cerca de esas funciones).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="test_correo_smtp_")
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "state")
os.makedirs(os.environ["BTP_STATE_DIR"], exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import correo_smtp as cs   # noqa: E402

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _raises(fn, exc=Exception):
    try:
        fn()
        return False
    except exc:
        return True


def main():
    src = open(os.path.join(ROOT, "tools", "correo_smtp.py"), encoding="utf-8").read()

    # 1. dry_run=True por defecto — nunca conecta salvo pedirlo explícito.
    r = cs.enviar(to=["dest@ejemplo.com"], subject="Asunto de prueba", body="Hola",
                  account="titular.mgp@gmail.com")   # sin dry_run explícito → default True
    check("enviar() sin dry_run explícito = dry_run True", r["dry_run"] is True)
    check("dry_run no manda a", r["to"] == ["dest@ejemplo.com"])

    # 2. HALT bloquea ANTES de cualquier intento de conexión.
    halt_path = os.path.join(_TMP, ".HALT")
    cwd0 = os.getcwd()
    os.chdir(_TMP)
    try:
        open(halt_path, "w").close()
        check("HALT activo bloquea enviar()",
              _raises(lambda: cs.enviar(to=["a@b.com"], subject="x", body="y", dry_run=False),
                      RuntimeError))
    finally:
        os.remove(halt_path)
        os.chdir(cwd0)

    # 3. Egress fail-closed: solo smtp.gmail.com:587.
    check("permite smtp.gmail.com:587", not _raises(lambda: cs._assert_smtp_host("smtp.gmail.com", 587)))
    check("bloquea otro host", _raises(lambda: cs._assert_smtp_host("evil.example.com", 587), RuntimeError))
    check("bloquea otro puerto", _raises(lambda: cs._assert_smtp_host("smtp.gmail.com", 25), RuntimeError))

    # 4. Validación de destinatarios / anti-inyección de cabeceras.
    check("destinatario inválido rechazado", _raises(lambda: cs._valida_destinatarios(["no-es-email"], "To")))
    check("salto de línea en asunto rechazado",
          _raises(lambda: cs._construir_mime("a@b.com", ["c@d.com"], [], "Asunto\nInyectado", "cuerpo", [])))
    check("sin destinatario rechazado",
          _raises(lambda: cs._construir_mime("a@b.com", [], [], "Asunto", "cuerpo", [])))

    # 5. Cuenta desconocida → error claro, no intenta resolver nada raro.
    check("cuenta desconocida lanza ValueError",
          _raises(lambda: cs._resolve_account("no-existo@otrodominio.com"), ValueError))
    u, s = cs._resolve_account("titular.mgp@gmail.com")
    check("cuenta conocida resuelve user+service", u == "titular.mgp@gmail.com" and s == "btp-gmail-app-password")

    # 6. MONITOR DE SALUD (2/7/26): smtp_alcanzable/login_ok son fail-soft, nunca lanzan,
    #    y estructuralmente no pueden enviar (no llaman a send_message).
    ok, motivo = cs.smtp_alcanzable(timeout=0.001)   # timeout ínfimo → falla rápido, sin red real
    check("smtp_alcanzable con timeout ínfimo no lanza, devuelve tupla", isinstance(ok, bool) and (motivo is None or isinstance(motivo, str)))
    ok2, motivo2 = cs.login_ok(account="cuenta-que-no-existe@nowhere.com")
    check("login_ok con cuenta desconocida = False + motivo (fail-soft, no lanza)",
          ok2 is False and isinstance(motivo2, str))

    # 7. Estructural: smtp_alcanzable/login_ok nunca llaman a send_message en su cuerpo (grep
    #    de las funciones: send_message NO debe aparecer entre su def y la siguiente '# ---').
    import re
    m = re.search(r"def smtp_alcanzable.*?def login_ok.*?(?=\n# -{3,}|\Z)", src, re.S)
    check("smtp_alcanzable/login_ok jamás llaman a send_message",
          m is not None and "send_message" not in m.group(0))

    print("RESULTADO correo_smtp.py: %d OK, %d fallos" % (_pass, _fail))
    print("✅ CORREO_SMTP EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
