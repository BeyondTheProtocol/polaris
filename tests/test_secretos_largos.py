#!/usr/bin/env python3
"""El Llavero trunca los secretos largos, y hay que enterarse.

`security add-generic-password -w`, tanto por su prompt como por stdin, corta el valor a 128
caracteres SIN avisar: dice que ha guardado y el secreto queda inservible. Pasó de verdad el
20-sep-2026 con un token de Synapse de 765 caracteres (tres intentos, tres truncados, 401 en la
API) hasta que se pasó como argumento.

Esto comprueba que `_secrets.set()` guarda entero y que además lo verifica releyendo. Usa un
servicio de usar y tirar y lo borra al salir; nunca toca un secreto real.
"""
import os
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))
import _secrets  # noqa: E402

# Nombre ÚNICO por ejecución (25-sep-26, deuda test-secretos-largos-flake-concurrencia, 3x): con
# un nombre fijo, dos sesiones corriendo test_all.sh a la vez se pisaban en el Llavero (rc=45 al
# guardar, o el `limpia()` de una borraba el secreto de la otra a medio test).
SERVICIO = "btp-test-secreto-largo-%d-%s" % (os.getpid(), os.urandom(4).hex())
fallos = []


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


def limpia():
    while subprocess.run(["security", "delete-generic-password", "-s", SERVICIO],
                         capture_output=True).returncode == 0:
        pass


if sys.platform != "darwin":
    print("SKIP: el Llavero es de macOS")
    sys.exit(77)

limpia()
try:
    largo = ("eyJ" + "a1B2c3D4" * 100 + ".fin")[:765]   # 765 caracteres, como el token real
    check(len(largo) == 765, "el secreto de prueba mide 765 caracteres")

    # 1) la vía vieja (stdin) TRUNCA: si algún día deja de hacerlo, este test lo cantará
    subprocess.run(["security", "add-generic-password", "-U", "-a", os.environ.get("USER", ""),
                    "-s", SERVICIO, "-w"], input="%s\n%s\n" % (largo, largo),
                   capture_output=True, text=True)
    por_stdin = _secrets.get(SERVICIO) or ""
    check(len(por_stdin) < len(largo),
          "  por stdin se guarda TRUNCADO (%d de %d), que es el fallo que motiva esto"
          % (len(por_stdin), len(largo)))
    limpia()

    # 2) _secrets.set() guarda entero
    n = _secrets.set(SERVICIO, largo)
    check(n == len(largo), "_secrets.set() dice haber guardado los %d" % len(largo))
    check(_secrets.get(SERVICIO) == largo, "  y al releerlo es EXACTAMENTE el mismo secreto")

    # 3) un secreto vacío no se guarda en silencio
    try:
        _secrets.set(SERVICIO, "   ")
        check(False, "un secreto vacío aborta")
    except ValueError:
        check(True, "un secreto vacío aborta")
finally:
    limpia()

print("\nVEREDICTO: %s" % ("TODO CORRECTO" if not fallos else "%d FALLOS" % len(fallos)))
sys.exit(1 if fallos else 0)
