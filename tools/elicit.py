#!/usr/bin/env python3
"""Busca literatura ingeniera en Elicit (138M+ papers) vía su API oficial.

Carril de EVIDENCIA — complementa a BioMCP/PubMed. Solo búsquedas a nivel
de TEMA; JAMÁS envíes PII, datos crudos del tumor, mutaciones/HLA/VCF ni
nombre de paciente (regla del borde). El resultado es DATO a verificar contra
la fuente primaria, no verdad.

Setup (una sola vez):
  Guarda tu clave de Elicit en el Llavero:
    security add-generic-password -s btp-elicit-api -a key -w <tu_clave>
  La clave empieza por 'elk_'. Plan mínimo requerido: Pro.

Uso:
  python3 elicit.py "PD-L1 expression in breast cancer"
  python3 elicit.py --n 10 "CDK4/6 inhibitor resistance mechanisms"
  python3 elicit.py --keyword "FGFR1 amplification breast cancer"
  python3 elicit.py --pubmed "HER2 low breast cancer treatment"
  python3 elicit.py --json "neoantigens personalized cancer vaccine"

Como módulo:
  from elicit import buscar
  papers = buscar("PD-L1 breast cancer", max_results=5)
  # devuelve lista de dicts: {id, title, authors, year, doi, pmid, venue, citations, abstract, urls}
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secrets import get as get_secret

BASE_URL = "https://elicit.com/api/v1/search"
# Cloudflare bloquea el User-Agent por defecto de Python (urllib); necesitamos uno real.
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
DEFAULT_MAX = 10
MAX_RESULTS = 100   # techo del plan Pro (200 en Scale)


def _load_key() -> str:
    """Lee la clave del Llavero. Lanza RuntimeError si no está o no es válida."""
    key = get_secret("btp-elicit-api", None, "key")
    if not key or not key.startswith("elk_"):
        raise RuntimeError(
            "Falta la clave de Elicit en el Llavero (debe empezar por 'elk_'). "
            "Guárdala con:\n"
            "  security add-generic-password -s btp-elicit-api -a key -w <tu_clave>"
        )
    return key


def _borde_check(query: str) -> bool:
    """Comprueba que la query pasa el borde antes de salir a Elicit."""
    try:
        import borde
        return borde.guard_cli(query, "elicit")
    except Exception:
        # Si borde no carga, FAIL-CLOSED por seguridad
        print("[elicit] ERROR: borde.py no disponible — llamada bloqueada", file=sys.stderr)
        return False


def _post(key: str, body: dict) -> dict:
    """Ejecuta la llamada POST y devuelve el JSON o lanza RuntimeError."""
    req = urllib.request.Request(
        BASE_URL,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _UA,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:600]
        hints = {
            401: "Clave inválida — revisa el Llavero (btp-elicit-api).",
            402: "Cuota de rate-limit agotada para este mes.",
            403: (
                "Acceso denegado (403). Posibles causas:\n"
                "  - Plan insuficiente: se necesita Pro o superior\n"
                "  - Clave caducada o revocada\n"
                "  - Cloudflare bloqueando la solicitud"
            ),
            429: "Rate-limit alcanzado. Espera antes de reintentar.",
        }
        raise RuntimeError(
            f"Elicit API {e.code}: {hints.get(e.code, detail)}\n{detail}"
        ) from e
    except Exception as e:
        raise RuntimeError(f"Error de red al llamar a Elicit: {e}") from e


def _parse_papers(raw_papers: list) -> list:
    """Convierte la lista cruda del API al formato mínimo interno."""
    out = []
    for p in raw_papers:
        authors = p.get("authors") or []
        out.append({
            "id":        p.get("elicitId", ""),
            "title":     (p.get("title") or "").strip("[]"),
            "authors":   authors[:6],           # máx 6 para no saturar
            "year":      p.get("year"),
            "doi":       p.get("doi", ""),
            "pmid":      p.get("pmid", ""),
            "venue":     p.get("venue", ""),
            "citations": p.get("citedByCount", 0),
            "abstract":  p.get("abstract") or "",
            "urls":      p.get("urls") or [],
        })
    return out


def buscar(query: str,
           max_results: int = DEFAULT_MAX,
           search_mode: str = "semantic",
           corpus: str = "elicit",
           _raw_override: "dict | None" = None) -> list:
    """Busca papers en Elicit. Devuelve lista de dicts o lanza RuntimeError.

    Parámetros:
      query        -- pregunta de investigación (nivel de tema, sin PII)
      max_results  -- nº máximo de resultados (1-100; plan Pro: 100/día)
      search_mode  -- "semantic" (por defecto) o "keyword"
      corpus       -- "elicit" (por defecto, 138M papers) o "pubmed"
      _raw_override -- inyectar respuesta mock en tests (evita llamada real)

    El resultado es DATO a verificar; no lo uses como verdad clínica sin cotejo.
    """
    if not _borde_check(query):
        raise RuntimeError(
            "Borde bloqueó la llamada a Elicit: la query contiene contenido "
            "sensible (PII/clínico/genómico). Solo envía preguntas a nivel de TEMA."
        )

    if _raw_override is not None:
        return _parse_papers(_raw_override.get("papers", []))

    key = _load_key()
    max_results = max(1, min(MAX_RESULTS, max_results))
    body = {
        "query":      query,
        "maxResults": max_results,
        "searchMode": search_mode,
        "corpus":     corpus,
    }
    data = _post(key, body)
    return _parse_papers(data.get("papers", []))


def _fmt_paper(i: int, p: dict) -> str:
    authors_str = ", ".join(p["authors"][:3])
    if len(p["authors"]) > 3:
        authors_str += " et al."
    year_str    = f" ({p['year']})" if p["year"] else ""
    doi_str     = f"  DOI: {p['doi']}" if p["doi"] else ""
    pmid_str    = f"  PMID: {p['pmid']}" if p["pmid"] else ""
    cit_str     = f"  Citaciones: {p['citations']}" if p["citations"] else ""
    venue_str   = f"  Revista: {p['venue']}" if p["venue"] else ""
    abst_str    = ""
    if p["abstract"]:
        abst_str = f"\n  Abstract: {p['abstract'][:300]}..."
    return (
        f"[{i}] {p['title']}{year_str}\n"
        f"  {authors_str}"
        f"{doi_str}{pmid_str}{cit_str}{venue_str}{abst_str}"
    )


def main() -> None:
    args = sys.argv[1:]
    mode   = "semantic"
    corpus = "elicit"
    max_n  = DEFAULT_MAX
    show_json = False

    if "--json" in args:
        show_json = True
        args.remove("--json")
    if "--keyword" in args:
        mode = "keyword"
        args.remove("--keyword")
    if "--pubmed" in args:
        corpus = "pubmed"
        args.remove("--pubmed")

    if "--n" in args:
        idx = args.index("--n")
        if idx + 1 >= len(args):
            print("--n necesita un valor (ej: --n 20)", file=sys.stderr)
            sys.exit(2)
        try:
            max_n = int(args[idx + 1])
        except ValueError:
            print("--n debe ser un número entero", file=sys.stderr)
            sys.exit(2)
        del args[idx:idx + 2]

    query = " ".join(args).strip()
    if not query:
        print(__doc__)
        sys.exit(0)

    try:
        papers = buscar(query, max_results=max_n, search_mode=mode, corpus=corpus)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if show_json:
        print(json.dumps(papers, ensure_ascii=False, indent=2))
        return

    if not papers:
        print("(sin resultados)")
        return

    print(f"Elicit: {len(papers)} resultado(s) para \"{query}\"\n")
    for i, p in enumerate(papers, 1):
        print(_fmt_paper(i, p))
        print()

    print("AVISO: datos sin verificar contra fuente primaria. "
          "Coteja DOI/PMID antes de usar en decisiones clinicas.")


if __name__ == "__main__":
    main()
