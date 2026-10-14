#!/usr/bin/env python3
"""Carga de secretos para las tools de Beyond the Protocol.

Orden de búsqueda:
  1) Llavero de macOS  (security find-generic-password -s <servicio> -w)
  2) fichero .X_secrets.json  (fallback de retro-compatibilidad)

Objetivo de seguridad: las claves viven en el Llavero; el fichero queda solo
como red mientras se completa la migración. Nunca imprime el valor.
"""
import json
import os
import subprocess

# Ruta ABSOLUTA (13-oct-26). Con el nombre desnudo, el `security` que se ejecuta lo elige el PATH
# de quien lanza la tool: un `security` falso en un directorio anterior recibiría el secreto que
# se guarda (va en argv) y podría devolver el valor que quisiera al leer.
# Test: tests/test_permiso_security_ruta.py.
SECURITY = "/usr/bin/security"
# Las claves que firman el permiso de salida no se leen ni se escriben por aquí (14-oct-26):
# `_secrets.get("btp-firmante-ed25519")` desde cualquier script era la vía más corta para
# sacarlas en una sesión local. Quien las necesita las lee él mismo (permiso_envio, firmante).
RESERVADOS = frozenset(("btp-ok-envio-mac", "btp-firmante-ed25519"))


def _keychain(service):
    """Devuelve el secreto del Llavero o None."""
    if str(service or "").strip().lower() in RESERVADOS:
        return None
    try:
        r = subprocess.run(
            [SECURITY, "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            v = r.stdout.strip()
            return v or None
    except Exception:
        pass
    return None


def _file_value(path, key):
    try:
        with open(path) as f:
            return (json.load(f) or {}).get(key)
    except Exception:
        return None


def set(service, valor, cuenta=None):
    """Guarda un secreto en el Llavero y COMPRUEBA que entró entero.

    Por qué no vale `security add-generic-password -w` a secas: su prompt interactivo —y su
    lectura por stdin— TRUNCAN el valor a 128 caracteres sin decir nada. El 20-sep-26 el token
    personal de Synapse (765 caracteres) se guardó cortado tres veces y la API respondía
    401; el mismo valor pasado como ARGUMENTO entró entero. Afecta a cualquier token largo
    (OAuth, refresh tokens, JWT).

    El valor aparece un instante en `argv`, visible en `ps`: por eso esto lo llama Polaris y no
    se teclea en una shell (no deja rastro en el historial), y si el secreto ya se ha visto en
    claro, se revoca y se regenera. A cambio, se guarda entero y se verifica releyéndolo.
    """
    import getpass
    valor = (valor or "").strip()
    if not valor:
        raise ValueError("secreto vacío: no se guarda")
    if str(service or "").strip().lower() in RESERVADOS:
        raise ValueError("esa clave firma el permiso de salida: no se escribe por aquí")
    cuenta = cuenta or os.environ.get("USER") or getpass.getuser()
    r = subprocess.run([SECURITY, "add-generic-password", "-U", "-a", cuenta,
                        "-s", service, "-w", valor], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("el Llavero rechazó el secreto (rc=%d)" % r.returncode)
    leido = _keychain(service) or ""
    if leido != valor:
        raise RuntimeError("el Llavero guardó %d caracteres de %d: NO te fíes de este secreto"
                           % (len(leido), len(valor)))
    return len(valor)


def get(service, file_path=None, file_key=None, default=None):
    """Secreto: Llavero[service] o, si falta, fichero[file_key]; si no, default."""
    v = _keychain(service)
    if v:
        return v
    if file_path and file_key:
        v = _file_value(file_path, file_key)
        if v:
            return v
    return default
