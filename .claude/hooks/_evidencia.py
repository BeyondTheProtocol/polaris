#!/usr/bin/env python3
"""_evidencia.py — qué herramienta MCP es un buscador de evidencia, y de cuál.

POR QUÉ EXISTE (10-oct-2026). `scite_guard.py` reconocía a scite por el prefijo `mcp__scite__`.
Los conectores de claude.ai no se llaman por su nombre: llegan como `mcp__<uuid>__<herramienta>`,
así que el Scite conectado, Elicit, Consensus y Scholar Gateway salían sin pasar por
`borde.egress_cientifico` ni por `borde.senas_caso`. Prueba en seco de ese día: una consulta
inventada con cinco señas pasaba por los dos conectores y solo el nombre antiguo la paraba.

Se reconoce de dos maneras, y basta una:
  1. por servidor: el nombre local (`scite`) o el identificador del conector;
  2. por nombre de herramienta, cuando es inconfundible. Es la red por si el identificador de un
     conector cambia: `search_literature` es de scite se llame como se llame el servidor.
Los nombres genéricos (`search`, `fetch`) no entran en la segunda vía: los comparten X y Notion.

Lo importan `scite_guard.py` (lo que sale) y `entrada_guard.py` (lo que entra).
"""

# Identificador del conector de claude.ai → familia. Verlos: `session_connectors_status`.
SERVIDORES = {
    "scite": "scite",
    # biomcp es local (stdio), pero sus herramientas preguntan a PubMed, ClinicalTrials.gov,
    # MyVariant y openFDA: lo que se le pasa sale igual. `variant_*` avisará siempre en sombra,
    # porque buscar una variante es mandar una variante; si eso se admite lo decide {{TITULAR}}.
    "biomcp": "biomcp",
    "c3041ca0-441e-42c5-9506-d697a7a03413": "scite",
    "2f62c1c8-d0b5-4cb4-9cbf-8dd48a783cf1": "elicit",
    "fd386fb4-6a93-4061-9ba4-c645c3668c05": "consensus",
    "572eb2c5-1ab7-49e0-9551-fe96bcbb5f2d": "scholar-gateway",
    "b08b78ef-28ae-4b17-baf9-5cfd6efef4b0": "pubmed",
    "2b889bc6-e3f1-498b-bc68-58ad91fadea3": "biorxiv",
    "94719adc-11f1-487e-b7fd-68ec3a3b8b7c": "clinical-trials",
}

# Herramienta inconfundible → familia (red por si cambia el identificador del conector).
POR_NOMBRE = {
    "search_literature": "scite", "read_fulltext": "scite", "citation_graph": "scite",
    "citation_report": "scite", "report_citations": "scite", "bibliography": "scite",
    "search_papers": "elicit", "create_systematic_review": "elicit",
    "stage_library_upload": "elicit", "stage_file_upload": "elicit",
    "import_library_files": "elicit", "send_agent_session_message": "elicit",
    "semanticSearch": "scholar-gateway",
    "search_articles": "pubmed", "lookup_article_by_citation": "pubmed",
    "search_preprints": "biorxiv", "search_published_preprints": "biorxiv",
    "search_by_eligibility": "clinical-trials",
}

# Lo que un agente no hace nunca en cada familia, lleve el texto que lleve.
ESCRIBE = {
    "scite": {
        "create_collection", "update_collection", "delete_collection",
        "add_dois_to_collection", "remove_dois_from_collection",
        "create_collection_note", "update_collection_note", "delete_collection_note",
        "upload_collection_file", "delete_collection_file",
    },
    # Elicit: veredicto solo-local (29-jun-26). Vale para buscar literatura; subir, nunca.
    "elicit": {"stage_library_upload", "stage_file_upload", "import_library_files"},
}
MOTIVO_ESCRIBE = {
    "scite": "escribe colecciones de scite (los agentes solo las leen)",
    "elicit": "sube ficheros a Elicit (veredicto solo-local: sirve para buscar, no para subir)",
}


def matcher():
    """La matcher de settings.json, derivada de las dos listas. Estrecha a propósito: con
    `mcp__.*` el guard arrancaría en cada llamada a Gmail o Notion, y con el Mac cargado su
    watchdog las denegaría. `tests/test_scite_guard.py` exige que settings.json lleve esta."""
    nombres = set(POR_NOMBRE)
    for n in ESCRIBE.values():
        nombres |= n
    return "mcp__(%s)__.*|mcp__.*__(%s)" % ("|".join(sorted(SERVIDORES)), "|".join(sorted(nombres)))


def familia(tool):
    """'scite', 'elicit', … o None si la herramienta no es un buscador de evidencia."""
    partes = (tool or "").split("__")
    if len(partes) < 3 or partes[0] != "mcp":
        return None
    corto = partes[-1]
    servidor = "__".join(partes[1:-1])
    if servidor in SERVIDORES:
        return SERVIDORES[servidor]
    if servidor.startswith("ccd_"):
        return None
    for fam, nombres in ESCRIBE.items():
        if fam == "scite" and corto in nombres:
            return fam
    return POR_NOMBRE.get(corto)


def escribe(tool, fam):
    """Motivo si la herramienta escribe o sube donde no debe; None si solo lee."""
    if tool.split("__")[-1] in ESCRIBE.get(fam, ()):
        return MOTIVO_ESCRIBE[fam]
    return None
