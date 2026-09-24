#!/usr/bin/env python3
"""Cumbre: gestión de estado con locking para evitar escrituras concurrentes perdidas."""

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Optional


STATE_DIR = Path(os.environ.get("BTP_STATE_DIR", Path.home() / ".polaris"))
STATE_FILE = STATE_DIR / "cumbre.json"
LOCK_FILE = STATE_DIR / "cumbre.lock"
SEMILLA_FILE = STATE_DIR / "cumbre.semilla.json"


@contextmanager
def _lock_exclusivo(timeout: float = 10.0):
    """Adquirir lock exclusivo con timeout."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(LOCK_FILE, "w")
    start = time.time()
    
    while True:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.time() - start > timeout:
                lock_fd.close()
                raise TimeoutError(f"No se pudo adquirir el lock en {timeout}s")
            time.sleep(0.05)
    
    try:
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def _leer_estado() -> Optional[Dict[str, Any]]:
    """Leer estado desde archivo, devuelve None si no existe."""
    if not STATE_FILE.exists():
        return None
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _leer_semilla() -> Dict[str, Any]:
    """Leer semilla para primera instalación."""
    if not SEMILLA_FILE.exists():
        return {"version": 1, "inicializado": time.time()}
    with open(SEMILLA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _guardar_estado(estado: Dict[str, Any]):
    """Guardar estado atómicamente con os.replace."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = STATE_FILE.with_suffix(".tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(estado, f, indent=2)
    os.replace(tmp_file, STATE_FILE)


def ensure() -> Dict[str, Any]:
    """Asegurar que existe el estado, creando con semilla si es primera vez.
    
    NO rellena estado desaparecido con la semilla (fail-closed).
    """
    with _lock_exclusivo():
        estado = _leer_estado()
        if estado is not None:
            return estado
        
        if SEMILLA_FILE.exists():
            raise FileNotFoundError(
                f"Estado no existe en {STATE_FILE}. "
                "Si es primera instalación, ejecute init() primero."
            )
        else:
            semilla = {"version": 1, "inicializado": time.time()}
            _guardar_estado(semilla)
            return semilla


def init(semilla: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Inicializar estado con semilla explícita."""
    with _lock_exclusivo():
        if semilla is None:
            semilla = {"version": 1, "inicializado": time.time()}
        
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(SEMILLA_FILE, "w", encoding="utf-8") as f:
            json.dump(semilla, f, indent=2)
        
        _guardar_estado(semilla.copy())
        return semilla.copy()


def mutar(actualizar: Dict[str, Any]) -> Dict[str, Any]:
    """Mutación atómica: leer → modificar → guardar con lock."""
    with _lock_exclusivo():
        estado = _leer_estado()
        if estado is None:
            raise FileNotFoundError(
                f"Estado no existe en {STATE_FILE}. Ejecute init() primero."
            )
        
        estado_actualizado = {**estado, **actualizar}
        _guardar_estado(estado_actualizado)
        return estado_actualizado


def obtener() -> Dict[str, Any]:
    """Obtener estado actual (solo lectura, sin lock)."""
    estado = _leer_estado()
    if estado is None:
        raise FileNotFoundError(
            f"Estado no existe en {STATE_FILE}. Ejecute init() primero."
        )
    return estado


def reset():
    """Resetear estado a la semilla (con lock)."""
    with _lock_exclusivo():
        semilla = _leer_semilla()
        _guardar_estado(semilla.copy())
        return semilla.copy()


def borrar():
    """Borrar estado (no la semilla). Requiere lock."""
    with _lock_exclusivo():
        if STATE_FILE.exists():
            STATE_FILE.unlink()


def main():
    import sys
    
    if len(sys.argv) < 2:
        print("Uso: python3 cumbre.py <comando> [args]")
        print("Comandos: init, get, mutar, reset, borrar")
        sys.exit(1)
    
    comando = sys.argv[1]
    
    if comando == "init":
        estado = init()
        print(f"Estado inicializado: {estado}")
    
    elif comando == "get":
        try:
            estado = obtener()
            print(json.dumps(estado, indent=2))
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    
    elif comando == "mutar":
        if len(sys.argv) < 3:
            print("Uso: python3 cumbre.py mutar '{\"clave\": \"valor\"}'")
            sys.exit(1)
        actualizar = json.loads(sys.argv[2])
        estado = mutar(actualizar)
        print(f"Estado actualizado: {estado}")
    
    elif comando == "reset":
        estado = reset()
        print(f"Estado reseteado: {estado}")
    
    elif comando == "borrar":
        borrar()
        print("Estado borrado")
    
    else:
        print(f"Comando desconocido: {comando}")
        sys.exit(1)


if __name__ == "__main__":
    main()
