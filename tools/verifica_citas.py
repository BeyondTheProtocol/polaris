#!/usr/bin/env python3
"""Gate determinista de EXISTENCIA de citas — caza citas FABRICADAS antes de razonar.

Por qué: los buscadores LLM inventan ~1 de cada 3 citas (CJR mar-2025). Antes de que
un agente le ponga a {{TITULAR}} delante un DOI / PMID / ensayo (NCT) / arXiv, este gate
comprueba —sin LLM, con APIs públicas gratis— si la referencia EXISTE de verdad. Lo
que no existe se marca FABRICADA y se bloquea; lo que existe pasa al juicio adversarial
de siempre. Patrón copiado (no el código) de academic-research-skills, aislado del
multiagente caro. Lo usa el agente `verificacion` como PRIMER filtro barato.

Sin pip. Usa curl (consistente con tools/umami.py; evita líos de TLS/anti-bot).
Solo IDs PÚBLICOS de literatura — CERO PII, no toca el muro.

APIs (todas gratis, sin clave):
  DOI   -> Crossref      https://api.crossref.org/works/{doi}
           si 404 -> DataCite  https://api.datacite.org/dois/{doi}   (Zenodo, figshare…)
           si 404 -> doi.org   https://doi.org/api/handles/{doi}     (cualquier agencia)
  PMID  -> NCBI eutils   https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi
  NCT   -> ClinicalTrials.gov v2   https://clinicaltrials.gov/api/v2/studies/{nct}
  arXiv -> http://export.arxiv.org/api/query?id_list={id}

Uso:
  python3 tools/verifica_citas.py "10.1186/s13073-024-01388-3" "PMID:39538331" "NCT07112053"
  python3 tools/verifica_citas.py --json "<id>" ...        # salida JSON (para agentes)
  echo "10.xxx ; PMID:123 ; NCT012..." | python3 tools/verifica_citas.py -   # desde stdin
Salida: por cada id -> existe / FABRICADA / no_resoluble  (+ título canónico si existe).
Código de salida: 2 si hay alguna FABRICADA (para que verificacion la bloquee), si no 0.
"""
import os, sys, re, json, subprocess

UA = "BeyondTheProtocol-citecheck/1.0"
TIMEOUT = "25"

# --- estados ---
EXISTE = "existe"
FABRICADA = "no_existe"        # confirmado que NO existe en el registro -> cita fabricada
NO_RES = "no_resoluble"        # red caída / API muda -> no acusar (puede existir)
# Distinto de NO_RES a propósito: aquí el problema NO es la red, es que no supimos leer un id
# de la cadena. Mezclarlos hacía que una cita ilegible se leyera como «la red falló», que el
# agente `verificacion` tiene instrucción de NO acusar → pasaba limpia. Con la etiqueta propia,
# quien la reciba sabe que hay que mirarla a mano en vez de darla por buena.
NO_PARSE = "no_parseable"      # no encontré ningún id verificable en la cadena


def _curl(url, accept="application/json", timeout=TIMEOUT):
    """GET con curl. Devuelve (codigo_http:int|None, cuerpo:str). None si curl falla."""
    p = subprocess.run(
        ["curl", "-sS", "-L", "--max-time", str(timeout), "-A", UA,
         "-H", "accept: " + accept, "-w", "\n%{http_code}", url],
        capture_output=True, text=True)
    if p.returncode != 0:
        return None, (p.stderr or "")[:160]
    raw = p.stdout or ""
    nl = raw.rfind("\n")
    if nl < 0:
        return None, raw[:160]
    body, code = raw[:nl], raw[nl + 1:].strip()
    try:
        return int(code), body
    except ValueError:
        return None, body[:160]


# --- detección de tipo de id ---
# Lo que el markdown o la frase pegan al final de un DOI (24-sep-2026: el gate extrajo
# «10.5281/zenodo.20005708`» con el backtick del código en línea y lo dio por fabricado).
_COLA_DOI = ".,;:`*_]>\"'"


def _limpia_doi(d):
    """Quita markdown y puntuación de los bordes de un DOI. El `)` final solo se quita si
    está desparejado: «10.1016/S1470-2045(25)00001-9» lleva paréntesis legítimos."""
    d = d.lstrip("`*_(<[\"'")
    while d:
        if d[-1] in _COLA_DOI:
            d = d[:-1]
        elif d[-1] == ")" and d.count(")") > d.count("("):
            d = d[:-1]
        else:
            break
    return d


def clasifica(raw):
    """Devuelve (tipo, id_limpio). tipo ∈ {doi,pmid,nct,arxiv,desconocido}."""
    s = (raw or "").strip().strip(".,;()[]<>\"' ")
    low = s.lower()
    # DOI EMBEBIDO, sin exigir que la cadena empiece por 10./doi/http.
    # Antes solo se extraía si la cita venía "pelada", así que una referencia bibliográfica
    # normal —«Pérez J, et al. Lancet Oncol. 2025;26(4):e123. doi:10.1016/…»— caía a
    # `desconocido` → `no_resoluble`, y el agente `verificacion` tiene instrucción de NO
    # acusar ante `no_resoluble` (lo trata como red caída). O sea que un DOI FABRICADO dentro
    # de una bibliografía pasaba limpio: justo el fallo nº1 de los buscadores LLM que este
    # tool existe para cazar. El patrón `10.\d{4,9}/` es muy distintivo y no colisiona con
    # dosis clínicas tipo «10.5/mg» (exige 4+ dígitos tras el punto).
    m = re.search(r'10\.\d{4,9}/\S+', s)
    if m:
        return "doi", _limpia_doi(m.group(0))
    # NCT
    m = re.search(r'(NCT\d{8})', s, re.I)
    if m:
        return "nct", m.group(1).upper()
    # PMID con prefijo
    m = re.match(r'pmid[:\s]*([0-9]{1,8})$', low)
    if m:
        return "pmid", m.group(1)
    # arXiv (con o sin prefijo): 2401.01234 ó arXiv:2401.01234v2
    m = re.search(r'(\d{4}\.\d{4,5})(v\d+)?', s)
    if "arxiv" in low or (m and "/" not in s and len(s) <= 16):
        if m:
            return "arxiv", m.group(1) + (m.group(2) or "")
    # DOI suelto (10.xxxx/yyy) sin url
    m = re.match(r'(10\.\d{4,9}/\S+)$', s)
    if m:
        return "doi", _limpia_doi(m.group(1))
    # dígitos pelados -> en nuestro contexto clínico casi siempre es un PMID
    if re.fullmatch(r'[0-9]{1,8}', s):
        return "pmid", s
    return "desconocido", s


# --- comprobadores por fuente ---
# Tras el 404 de Crossref, las consultas de respaldo van con timeout corto: el gate de
# salida da 20 s a todo el lote y, si no llega, no caza nada (fail-open).
TIMEOUT_RESPALDO = 8


def check_doi(doi):
    """Crossref → DataCite → doi.org. Un 404 de Crossref NO prueba que el DOI no exista:
    Zenodo, figshare y otros registran en DataCite (24-sep-2026 el gate acusó de fabricados
    tres DOI reales de Zenodo, uno el del preprint de la firma de {{TITULAR}}). Solo es FABRICADA
    si las tres fuentes dicen que no; si alguna calla, no_resoluble (no se acusa sin prueba)."""
    code, body = _curl("https://api.crossref.org/works/" + doi)
    if code == 200:
        try:
            t = (json.loads(body).get("message", {}).get("title") or [""])[0]
        except Exception:
            t = ""
        return EXISTE, t or "(sin título)", "Crossref"
    if code != 404:
        return NO_RES, "Crossref no respondió (%s)" % (code if code else body[:60]), "Crossref"

    code, body = _curl("https://api.datacite.org/dois/" + doi, timeout=TIMEOUT_RESPALDO)
    if code == 200:
        try:
            titulos = json.loads(body).get("data", {}).get("attributes", {}).get("titles") or []
            t = (titulos[0] or {}).get("title", "") if titulos else ""
        except Exception:
            t = ""
        return EXISTE, t or "(sin título)", "DataCite"
    if code != 404:
        return NO_RES, "no está en Crossref y DataCite no respondió (%s)" % (
            code if code else body[:60]), "DataCite"

    # Última palabra: el Handle System de doi.org conoce los DOI de TODAS las agencias
    # (mEDRA, JaLC, KISTI, CNKI…) y también los de Crossref que su API no devuelve
    # (10.1093/oncolo/oyaf031, OUP: API 404, registrado en Crossref según doi.org/ra).
    # responseCode 1 = existe, 100 = no existe.
    code, body = _curl("https://doi.org/api/handles/" + doi, timeout=TIMEOUT_RESPALDO)
    try:
        rc = json.loads(body).get("responseCode") if code in (200, 404) else None
    except Exception:
        rc = None
    if rc == 1:
        return EXISTE, "(registrado en doi.org; las APIs de Crossref y DataCite no lo devuelven)", "doi.org"
    if rc == 100:
        return FABRICADA, "no está en Crossref, DataCite ni doi.org", "Crossref+DataCite+doi.org"
    return NO_RES, "no está en Crossref ni DataCite y doi.org no respondió (%s)" % (
        code if code else body[:60]), "doi.org"


def check_pmid(pmid):
    url = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
           "?db=pubmed&retmode=json&id=" + pmid)
    code, body = _curl(url)
    if code != 200:
        return NO_RES, "eutils no respondió (%s)" % (code if code else body[:60]), "PubMed"
    try:
        res = json.loads(body).get("result", {})
    except Exception:
        return NO_RES, "respuesta no-JSON de eutils", "PubMed"
    rec = res.get(pmid)
    # PMID inexistente: eutils mete una 'error' en el registro o no lo incluye en uids
    if isinstance(rec, dict) and not rec.get("error") and pmid in (res.get("uids") or [pmid]):
        return EXISTE, rec.get("title") or "(sin título)", "PubMed"
    if isinstance(rec, dict) and rec.get("error"):
        return FABRICADA, "PMID inexistente (%s)" % rec.get("error"), "PubMed"
    return FABRICADA, "PMID no encontrado en PubMed", "PubMed"


def check_nct(nct):
    code, body = _curl("https://clinicaltrials.gov/api/v2/studies/" + nct)
    if code == 200:
        try:
            t = (json.loads(body).get("protocolSection", {})
                 .get("identificationModule", {}).get("briefTitle") or "")
        except Exception:
            t = ""
        return EXISTE, t or "(sin título)", "ClinicalTrials.gov"
    if code == 404:
        return FABRICADA, "ensayo inexistente en ClinicalTrials.gov", "ClinicalTrials.gov"
    return NO_RES, "ClinicalTrials no respondió (%s)" % (code if code else body[:60]), "ClinicalTrials.gov"


def check_arxiv(aid):
    code, body = _curl("https://export.arxiv.org/api/query?id_list=" + aid, accept="application/atom+xml")
    if code != 200 or not body:
        return NO_RES, "arXiv no respondió (%s)" % (code if code else ""), "arXiv"
    # un id inexistente devuelve un <entry> con <title>Error</title>
    m = re.search(r'<entry>.*?<title>(.*?)</title>', body, re.S)
    if not m:
        return FABRICADA, "sin entrada en arXiv", "arXiv"
    title = re.sub(r'\s+', ' ', m.group(1)).strip()
    if title.lower() == "error":
        return FABRICADA, "id inexistente en arXiv", "arXiv"
    return EXISTE, title, "arXiv"


_CHECKERS = {"doi": check_doi, "pmid": check_pmid, "nct": check_nct, "arxiv": check_arxiv}


def verifica(ids):
    """Lista de strings -> lista de dicts {entrada,tipo,id,estado,detalle,fuente}.
    Importable por otras tools/agentes (verificacion la usa como primer filtro)."""
    out = []
    for raw in ids:
        raw = (raw or "").strip()
        if not raw:
            continue
        tipo, cid = clasifica(raw)
        fn = _CHECKERS.get(tipo)
        if not fn:
            out.append({"entrada": raw, "tipo": tipo, "id": cid,
                        "estado": NO_PARSE,
                        "detalle": "no encontré DOI/PMID/NCT/arXiv en la cita — "
                                   "NO es «la red falló»: verifícala a mano antes de darla por buena",
                        "fuente": "—"})
            continue
        estado, detalle, fuente = fn(cid)
        out.append({"entrada": raw, "tipo": tipo, "id": cid,
                    "estado": estado, "detalle": detalle, "fuente": fuente})
    return out


def _leer_ids(argv):
    if argv == ["-"] or (not argv and not sys.stdin.isatty()):
        data = sys.stdin.read()
        return re.split(r'[\s;,]+', data.strip())
    return argv


def main():
    argv = sys.argv[1:]
    as_json = False
    if argv and argv[0] in ("--json", "-j"):
        as_json, argv = True, argv[1:]
    ids = _leer_ids(argv)
    ids = [x for x in ids if x and x != "-"]
    if not ids:
        print(__doc__.strip().split("\n\n")[0])
        print("\nUso: python3 tools/verifica_citas.py \"10.xxx/yyy\" \"PMID:123\" \"NCT012...\"")
        return 0
    res = verifica(ids)
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        # NO_PARSE estaba definido y se emitía (línea ~177) pero faltaba aquí: imprimir un
        # resultado no_parseable reventaba con KeyError DESPUÉS de listar todo, y un exit 1
        # en una tool del muro de evidencia se lee como "hay una cita fabricada" cuando en
        # realidad era "la herramienta se rompió". Son cosas opuestas.
        glyph = {EXISTE: "✓ existe", FABRICADA: "✗ FABRICADA", NO_RES: "? no resoluble",
                 NO_PARSE: "· sin id"}
        print("=== Verificación de existencia de citas (gate determinista, sin LLM) ===")
        for r in res:
            print("[%-14s] %-30s (%s · %s)" % (glyph.get(r["estado"], "? %s" % r["estado"]), r["id"], r["tipo"], r["fuente"]))
            print("                 ↳ %s" % r["detalle"])
        nf = sum(1 for r in res if r["estado"] == FABRICADA)
        ne = sum(1 for r in res if r["estado"] == EXISTE)
        nr = sum(1 for r in res if r["estado"] == NO_RES)
        np_ = sum(1 for r in res if r["estado"] == NO_PARSE)
        print("Resumen: %d existe · %d FABRICADA · %d no resoluble · %d sin id" % (ne, nf, nr, np_))
        if nf:
            print("⚠️  Hay %d cita(s) que NO existen → trátalas como FABRICADAS y bloquéalas." % nf)
    return 2 if any(r["estado"] == FABRICADA for r in res) else 0


if __name__ == "__main__":
    sys.exit(main())
