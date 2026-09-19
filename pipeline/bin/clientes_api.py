"""
clientes_api.py — clientes de APIs públicas SIN CLAVE, egress controlado por muro.

Solo se manda terminología GENÉRICA (símbolo de gen, accession de proteína).
Nada de VCF/HLA/PII. Cada petición pasa por `muro.comprobar_salida`.

Incluido:
  - AlphaFold DB API (esquema actual; la legacy se retiró jun-2026): estructura 3D.
  - Ensembl REST: lookup de gen por símbolo, secuencia de proteína.

NO incluido (requieren registro/clave de {{TITULAR}} — ver docs/PIEZAS-GATED.md):
  - COSMIC (descarga tras login), OncoKB (token), NetMHCpan (licencia DTU).
"""
from __future__ import annotations
import requests
from muro import comprobar_salida, es_termino_generico, MuroError

_TIMEOUT = 30
_UA = {"User-Agent": "btp-pipeline-neoantigenos/0.1 (local; egress-genericos-solo)"}


def alphafold_por_accession(accession: str) -> dict | None:
    """Metadatos del modelo AlphaFold para un accession UniProt (genérico).
    Devuelve dict con accession, gene, pdbUrl, cifUrl — o None si no hay modelo."""
    if not es_termino_generico(accession):
        raise MuroError(f"MURO: '{accession}' no parece un accession genérico.")
    url = f"https://alphafold.ebi.ac.uk/api/prediction/{accession}"
    comprobar_salida(url, accession)
    r = requests.get(url, headers=_UA, timeout=_TIMEOUT)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    d = data[0]
    return {
        "accession": d.get("uniprotAccession"),
        "gene": d.get("gene"),
        "organism": d.get("organismScientificName"),
        "pdbUrl": d.get("pdbUrl"),
        "cifUrl": d.get("cifUrl"),
        "paeImageUrl": d.get("paeImageUrl"),
    }


def ensembl_lookup_gen(simbolo: str, especie: str = "homo_sapiens") -> dict | None:
    """Lookup Ensembl de un gen por símbolo (genérico)."""
    if not es_termino_generico(simbolo):
        raise MuroError(f"MURO: '{simbolo}' no parece un símbolo de gen genérico.")
    url = f"https://rest.ensembl.org/lookup/symbol/{especie}/{simbolo}"
    comprobar_salida(url, simbolo)
    r = requests.get(url, headers={**_UA, "Content-Type": "application/json"}, timeout=_TIMEOUT)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    d = r.json()
    return {
        "gene": simbolo,
        "ensembl_id": d.get("id"),
        "chromosome": d.get("seq_region_name"),
        "biotype": d.get("biotype"),
        "description": d.get("description"),
    }


def uniprot_accession_de_gen(simbolo: str, especie_taxid: str = "9606") -> str | None:
    """Resuelve un símbolo de gen humano a su accession UniProt (genérico),
    para luego pedir la estructura a AlphaFold."""
    if not es_termino_generico(simbolo):
        raise MuroError(f"MURO: '{simbolo}' no parece un símbolo de gen genérico.")
    url = ("https://rest.uniprot.org/uniprotkb/search"
           f"?query=gene:{simbolo}+AND+organism_id:{especie_taxid}+AND+reviewed:true"
           "&fields=accession&format=json&size=1")
    comprobar_salida(url, simbolo)
    r = requests.get(url, headers=_UA, timeout=_TIMEOUT)
    r.raise_for_status()
    results = r.json().get("results", [])
    if not results:
        return None
    return results[0].get("primaryAccession")
