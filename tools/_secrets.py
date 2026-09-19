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


def _keychain(service):
    """Devuelve el secreto del Llavero o None."""
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
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
