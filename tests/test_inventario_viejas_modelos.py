#!/usr/bin/env python3
"""Tests para Issues #2 y #5 de tools/inventario.py."""
from datetime import date
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import inventario


def test_viejas_filtra_y_ordena():
    filas = [
        {"pieza": "reciente.py", "estado": "viva", "ultimo_commit": "2026-09-20"},
        {"pieza": "vieja.py", "estado": "huerfana", "ultimo_commit": "2026-06-01"},
        {"pieza": "media.py", "estado": "entrada", "ultimo_commit": "2026-07-15"},
    ]
    resultado = inventario.herramientas_viejas(
        60, filas=filas, hoy=date(2026, 9, 24)
    )
    assert [fila["pieza"] for fila in resultado] == ["vieja.py", "media.py"]
    assert resultado[0]["estado"] == "huerfana"
    assert resultado[0]["dias_sin_tocar"] > resultado[1]["dias_sin_tocar"]


def test_viejas_ignora_fecha_desconocida_y_rechaza_negativo():
    filas = [{"pieza": "sin_fecha.py", "estado": "viva", "ultimo_commit": "?"}]
    assert inventario.herramientas_viejas(0, filas=filas, hoy=date(2026, 9, 24)) == []
    try:
        inventario.herramientas_viejas(-1, filas=[])
    except ValueError:
        pass
    else:
        raise AssertionError("N negativo debe fallar")


def test_parsea_ollama_list():
    salida = """NAME              ID              SIZE      MODIFIED
qwen2.5:7b       aaa             4 GB      2 days ago
llama3.2:3b      bbb             2 GB      1 week ago
"""
    assert inventario._modelos_ollama(salida) == ["qwen2.5:7b", "llama3.2:3b"]


def test_modelos_no_usados_compara_nombre_completo():
    salida = """NAME              ID              SIZE      MODIFIED
qwen2.5:7b       aaa             4 GB      2 days ago
qwen2.5:14b      bbb             9 GB      1 week ago
"""
    codigo = 'MODELO = "qwen2.5:7b"\n'
    assert inventario.modelos_no_usados(salida, codigo) == ["qwen2.5:14b"]


def test_sin_ollama_falla_explicito():
    with patch.object(inventario.subprocess, "run", side_effect=FileNotFoundError):
        try:
            inventario.modelos_no_usados()
        except RuntimeError as exc:
            assert "ollama no está instalado" in str(exc)
        else:
            raise AssertionError("Sin Ollama debe fallar explícitamente")


if __name__ == "__main__":
    test_viejas_filtra_y_ordena()
    test_viejas_ignora_fecha_desconocida_y_rechaza_negativo()
    test_parsea_ollama_list()
    test_modelos_no_usados_compara_nombre_completo()
    test_sin_ollama_falla_explicito()
    print("OK: tests de --viejas y --modelos")
