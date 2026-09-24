#!/usr/bin/env python3
"""Verificación de citas bibliográficas con validación de DOI en cascada."""

import json
import re
import sys
from typing import Dict, List, Optional, Tuple
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError


AGENCIAS = [
    ("doi.org", "https://doi.org/api/handles/{doi}"),
    ("Crossref", "https://api.crossref.org/works/{doi}"),
    ("DataCite", "https://api.datacite.org/doi/{doi}"),
]


def verificar_doi(doi: str, timeout: float = 10.0) -> Dict:
    """Verificar un DOI en cascada contra múltiples agencias.

    Consulta las agencias en orden: doi.org → Crossref → DataCite.
    Devuelve un dict con:
    - estado: "valido" | "indeterminado" | "no_encontrado" | "invalido"
    - agencia: nombre de la agencia que confirmó el DOI (si existe)
    - datos: metadatos de la respuesta (si existe)
    - detalle: dict con el resultado de cada agencia consultada

    Casos:
    - DOI válido en alguna agencia → estado="valido"
    - Fallo de red en todas → estado="indeterminado"
    - 404 en todas → estado="no_encontrado"
    - Mixto (algunas 404, otras error red) → estado="indeterminado"
    """
    if not doi or not re.match(r"^\d+\.\d+\/.+$", doi):
        return {"estado": "invalido", "detalle": {"error": "DOI mal formado"}}

    resultados = {}
    datos = None
    agencia_confirmada = None

    for nombre, url_template in AGENCIAS:
        url = url_template.format(doi=doi)
        try:
            req = Request(url, headers={"Accept": "application/json"})
            with urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    datos = json.loads(resp.read().decode("utf-8"))
                    agencia_confirmada = nombre
                    resultados[nombre] = "encontrado"
                    break
        except HTTPError as e:
            if e.code == 404:
                resultados[nombre] = "no_encontrado"
            else:
                resultados[nombre] = f"error_{e.code}"
        except URLError as e:
            resultados[nombre] = "indeterminado_red"
        except Exception as e:
            resultados[nombre] = f"error_{type(e).__name__}"

    if agencia_confirmada:
        return {
            "estado": "valido",
            "agencia": agencia_confirmada,
            "datos": datos,
            "detalle": resultados,
        }

    # Verificar si hubo al menos un error de red
    hubo_error_red = any("indeterminado" in str(v) or "error" in str(v) for v in resultados.values())
    if hubo_error_red:
        return {
            "estado": "indeterminado",
            "detalle": resultados,
        }

    # Todas las agencias dijeron 404
    return {
        "estado": "no_encontrado",
        "detalle": resultados,
    }


def verificar_citas(citas: List[Dict]) -> List[Dict]:
    """Verificar una lista de citas y devolver resultados con estado DOI.

    Cada cita debe tener al menos un campo "doi".
    Devuelve lista de dicts con:
    - doi: el DOI verificado
    - estado_doi: "valido" | "indeterminado" | "no_encontrado" | "invalido"
    - agencia: agencia que confirmó (si aplica)
    - original: la cita original
    """
    resultados = []
    for cita in citas:
        doi = cita.get("doi", "")
        if not doi:
            resultados.append({
                "doi": None,
                "estado_doi": "sin_doi",
                "original": cita,
            })
            continue

        verif = verificar_doi(doi)
        resultados.append({
            "doi": doi,
            "estado_doi": verif["estado"],
            "agencia": verif.get("agencia"),
            "detalle": verif.get("detalle"),
            "original": cita,
        })

    return resultados


def main():
    if len(sys.argv) < 2:
        print("Uso: python3 verifica_citas.py <archivo_json_con_citas>")
        print("El archivo debe contener una lista de objetos con campo 'doi'")
        sys.exit(1)

    archivo = sys.argv[1]
    with open(archivo, "r", encoding="utf-8") as f:
        citas = json.load(f)

    resultados = verificar_citas(citas)

    validos = sum(1 for r in resultados if r["estado_doi"] == "valido")
    indeterminados = sum(1 for r in resultados if r["estado_doi"] == "indeterminado")
    no_encontrados = sum(1 for r in resultados if r["estado_doi"] == "no_encontrado")
    invalidos = sum(1 for r in resultados if r["estado_doi"] == "invalido")
    sin_doi = sum(1 for r in resultados if r["estado_doi"] == "sin_doi")

    print(f"Total: {len(resultados)} citas")
    print(f"  Válidas: {validos}")
    print(f"  Indeterminadas: {indeterminados}")
    print(f"  No encontradas: {no_encontrados}")
    print(f"  Inválidas: {invalidos}")
    print(f"  Sin DOI: {sin_doi}")

    for r in resultados:
        estado_icono = {
            "valido": "✓",
            "indeterminado": "?",
            "no_encontrado": "✗",
            "invalido": "✗",
            "sin_doi": "-",
        }.get(r["estado_doi"], "?")

        print(f"  {estado_icono} {r['doi'] or 'sin DOI'} → {r['estado_doi']}")
        if r["estado_doi"] == "valido":
            print(f"      Agencia: {r['agencia']}")


if __name__ == "__main__":
    main()
