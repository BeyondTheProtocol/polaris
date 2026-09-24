#!/usr/bin/env python3
"""Inventario de herramientas Polaris: estado, antigüedad, solapes y modelos."""

import argparse
import difflib
import glob
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple


def inventario() -> List[Dict]:
    tools_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
    py_files = sorted(glob.glob(os.path.join(tools_dir, "*.py")))
    return [{"archivo": os.path.basename(py), "estado": "viva", "dias": 0} for py in py_files]


def solapes(umbral: float = 0.85) -> List[Tuple[str, str, float, str, str]]:
    """Detectar herramientas con docstrings similares (primera línea)."""
    tools_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
    py_files = sorted(glob.glob(os.path.join(tools_dir, "*.py")))

    def primera_linea_docstring(filepath: str) -> Optional[str]:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        match = re.search(r"^\s*(?:\"\"\"(.*?)\"\"\"|\'\'\'(.*?)\'\'\')", content, re.DOTALL | re.MULTILINE)
        if not match:
            return None
        doc = match.group(1) or match.group(2) or ""
        primera = doc.strip().splitlines()[0] if doc.strip() else None
        return primera.strip() if primera else None

    archivos_con_doc = [(os.path.basename(py), primera_linea_docstring(py)) for py in py_files]
    archivos_con_doc = [(n, l) for n, l in archivos_con_doc if l]

    resultados = []
    n = len(archivos_con_doc)
    for i in range(n):
        for j in range(i + 1, n):
            nom1, lin1 = archivos_con_doc[i]
            nom2, lin2 = archivos_con_doc[j]
            sim = difflib.SequenceMatcher(None, lin1, lin2).ratio()
            if sim >= umbral:
                resultados.append((nom1, nom2, sim, lin1, lin2))

    resultados.sort(key=lambda x: -x[2])
    return resultados


def modelos() -> List[str]:
    """Listar modelos de Ollama descargados sin uso aparente."""
    try:
        resultado = subprocess.run(["ollama", "list"], capture_output=True, text=True, check=True, timeout=30)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"Ollama no disponible: {e}")

    lineas = resultado.stdout.strip().splitlines()
    if not lineas:
        return []

    modelos_descargados = [linea.split()[0] for linea in lineas[1:] if linea.split()]

    tools_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
    codigo = ""
    for py in glob.glob(os.path.join(tools_dir, "*.py")):
        with open(py, "r", encoding="utf-8") as f:
            codigo += f.read() + "\n"

    return [m for m in modelos_descargados if m not in codigo]


def main():
    parser = argparse.ArgumentParser(description="Inventario de herramientas Polaris")
    parser.add_argument("--solapes", action="store_true", help="Listar herramientas con docstrings similares")
    parser.add_argument("--umbral", type=float, default=0.85, help="Umbral de similitud (default: 0.85)")
    parser.add_argument("--modelos", action="store_true", help="Listar modelos Ollama sin uso")
    args = parser.parse_args()

    if args.solapes:
        pares = solapes(umbral=args.umbral)
        if not pares:
            print(f"No se detectaron solapes con umbral {args.umbral}")
        else:
            print(f"Solapes detectados (umbral >= {args.umbral}):")
            for nom1, nom2, sim, lin1, lin2 in pares:
                print(f"  {nom1} ↔ {nom2} (similitud: {sim:.3f})")
                print(f"    \"{lin1}\"")
                print(f"    \"{lin2}\"")
        return

    if args.modelos:
        try:
            sin_uso = modelos()
            if not sin_uso:
                print("Todos los modelos descargados tienen uso aparente")
            else:
                print("Modelos descargados sin uso aparente:")
                for m in sin_uso:
                    print(f"  - {m}")
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        return

    for item in inventario():
        print(f"{item['archivo']}: {item['estado']}")


if __name__ == "__main__":
    main()
