#!/usr/bin/env python3
"""tools/tier_evidencia.py — etiqueta el TIER de evidencia SIN que lo decida un modelo.

Por qué existe: la regla 17 del protocolo ("etiqueta cada pieza de evidencia por tier:
in vitro, modelo animal, caso clínico, cohorte retrospectiva, RCT o guía clínica; nunca
aplanes un resultado preclínico en «esto funciona»") vivía SOLO en el prompt. Una regla
que solo vive en el prompt se cumple cuando el modelo se acuerda. `verifica_citas.py` ya
demostró el patrón contrario: la EXISTENCIA de una cita la certifica el registro, no el
modelo. Esto hace lo mismo con el NIVEL de la cita.

EL PATRÓN (el que importa, más que este fichero):
  no se le pide a un modelo "clasifica esto según esta descripción" —una clasificación de
  categoría abierta, donde el error es silencioso y no auditable—. Se descompone en
  SEÑALES BOOLEANAS atómicas (¿es un ensayo aleatorizado? ¿hay MeSH «Animals»? ¿hay
  «Humans»?), cada una con su procedencia, y el TEJIDO lo hace el CÓDIGO con una jerarquía
  fija. Consecuencias: (a) el resultado es reproducible y explicable señal a señal;
  (b) cuando una señal falta se ve, en vez de quedar tapada por una etiqueta confiada;
  (c) la jerarquía se revisa y se testea como código, no como prosa en un prompt.
  Idea recibida por DM de otro constructor (sept-2026) y adaptada; él la usaba para subir
  la precisión de un modelo pequeño, aquí sirve además para quitarle la decisión al modelo.

DE DÓNDE SALEN LAS SEÑALES, por orden de autoridad:
  1. REGISTRO (PubMed efetch): PublicationType + MeSH. Es un hecho catalogado por NLM,
     no una lectura. Camino feliz: cero LLM.
  2. CUESTIONARIO sí/no (`--preguntas` / `--desde-respuestas`): para un paper sin PMID
     (preprint, abstract pegado, póster). Un modelo contesta booleanos atómicos; el tejido
     lo sigue haciendo este código, con la MISMA función.
  3. LÉXICO del texto: solo como red, y solo puede DEGRADAR (marcar preclínico), nunca
     ascender a RCT/guía.
     Porqué-no: si el léxico pudiera ascender, un abstract que dice "randomized" en la
     discusión ascendería un estudio en ratones a RCT — justo el aplanamiento que la
     regla 17 prohíbe. Degradar por error cuesta una comprobación a mano; ascender por
     error pone un preclínico delante de ella con cara de ensayo.

FAIL-CLOSED: sin señales suficientes el tier es `desconocido` y el exit code es 1. Un
`desconocido` NO se puede presentar como evidencia clínica; se mira a mano.

MURO: solo IDs públicos de literatura (PMID). Cero PII, no toca la carpeta clínica, no
publica nada. Sin pip: usa curl, igual que verifica_citas.py.

Uso:
  python3 tools/tier_evidencia.py 42749858 "PMID: 42750626"     # desde el registro
  python3 tools/tier_evidencia.py --json 42737612               # salida JSON (agentes)
  python3 tools/tier_evidencia.py --preguntas                   # el cuestionario sí/no
  python3 tools/tier_evidencia.py --desde-respuestas '{"rct":true,"humanos":true}'
  python3 tools/tier_evidencia.py --texto "abstract pegado..."  # red de léxico (degrada)
"""
import json
import os
import re
import subprocess
import sys

UA = "BeyondTheProtocol-tier/1.0"
TIMEOUT = "25"

REPO = os.environ.get("BTP_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
CACHE = os.path.join(STATE, "tier_evidencia_cache.json")

# ── LAS SEÑALES ──────────────────────────────────────────────────────────────
# Cada una es un booleano atómico con UNA pregunta contestable con sí/no. True/False =
# sabido; None = no se pudo averiguar (y eso se propaga, no se disfraza de False).
# El orden de este dict es el orden del cuestionario.
SENALES = {
    "guia":        "¿Es una guía de práctica clínica o un documento de consenso de una sociedad ingeniera?",
    "metaanalisis": "¿Es un metaanálisis (combina cuantitativamente resultados de varios estudios)?",
    "rev_sistematica": "¿Es una revisión sistemática con método de búsqueda explícito?",
    "rct":         "¿Es un ensayo clínico ALEATORIZADO en personas?",
    "ensayo":      "¿Es un ensayo clínico en personas NO aleatorizado (fase 1, fase 2 de un solo brazo)?",
    "prospectivo": "¿Los datos se recogieron de forma prospectiva, definidos antes de empezar?",
    "retrospectivo": "¿Los datos se sacaron de historias o registros ya existentes (retrospectivo)?",
    "serie_casos": "¿Es una serie de casos (varios pacientes descritos, sin grupo de comparación)?",
    "caso":        "¿Es el informe de UN solo paciente (case report)?",
    "humanos":     "¿El estudio se hizo en personas o en muestras de personas?",
    "animal":      "¿El estudio se hizo en animales (ratón, rata, xenoinjerto, modelo animal)?",
    "in_vitro":    "¿El estudio se hizo en células o líneas celulares en cultivo?",
    "rev_narrativa": "¿Es una revisión narrativa, editorial o comentario, sin método sistemático?",
    "preprint":    "¿Es un preprint que aún no ha pasado revisión por pares?",
}

# ── LA JERARQUÍA (el tejido; esto es código, no prosa de prompt) ─────────────
# Etiquetas en el vocabulario de la regla 17, ordenadas de más a menos fuerte.
TIERS = ("guia_clinica", "metaanalisis", "revision_sistematica", "rct", "ensayo_clinico",
         "cohorte_prospectiva", "cohorte_retrospectiva", "serie_casos", "caso_clinico",
         "modelo_animal", "in_vitro", "revision_narrativa", "preprint", "desconocido")

TIER_TXT = {
    "guia_clinica": "guía clínica / consenso",
    "metaanalisis": "metaanálisis",
    "revision_sistematica": "revisión sistemática",
    "rct": "ensayo aleatorizado (RCT)",
    "ensayo_clinico": "ensayo clínico no aleatorizado (fase 1/2)",
    "cohorte_prospectiva": "cohorte prospectiva",
    "cohorte_retrospectiva": "cohorte retrospectiva",
    "serie_casos": "serie de casos",
    "caso_clinico": "caso clínico",
    "modelo_animal": "modelo animal (PRECLÍNICO)",
    "in_vitro": "in vitro (PRECLÍNICO)",
    "revision_narrativa": "revisión narrativa / opinión",
    "preprint": "preprint sin revisar",
    "desconocido": "DESCONOCIDO — no clasificable, mírala a mano",
}

PRECLINICOS = ("modelo_animal", "in_vitro")


def _si(s, k):
    """True solo si la señal está y vale True. None nunca cuenta como sí."""
    return s.get(k) is True


def teje(senales):
    """Señales booleanas -> dict con el tier. NINGÚN modelo interviene aquí.

    Devuelve {tier, etiqueta, preclinico, motivo, señales_usadas, faltan}.
    `preclinico` es la bandera que la regla 17 exige: si es True, quien escriba encima
    NO puede decir "esto funciona" — como mucho "esto funcionó en células/en ratón".
    """
    s = {k: senales.get(k) for k in SENALES}
    faltan = [k for k in SENALES if s.get(k) is None]

    # Preclínico = hay señal de banco Y no hay señal de personas. Se calcula ANTES de la
    # jerarquía porque manda sobre ella: un paper en ratones que cita un RCT en su
    # discusión no es un RCT, y la degradación no debe poder perderse por el camino.
    banco = _si(s, "animal") or _si(s, "in_vitro")
    es_preclinico = banco and not _si(s, "humanos")

    if es_preclinico:
        tier = "modelo_animal" if _si(s, "animal") else "in_vitro"
        motivo = "señal de banco (%s) sin señal de personas" % (
            "animal" if _si(s, "animal") else "in vitro")
        return _resultado(tier, motivo, s, faltan)

    # Jerarquía clínica: la primera que case manda.
    reglas = (
        ("guia_clinica",        lambda: _si(s, "guia"),                 "catalogado como guía/consenso"),
        ("metaanalisis",        lambda: _si(s, "metaanalisis"),         "catalogado como metaanálisis"),
        ("revision_sistematica", lambda: _si(s, "rev_sistematica"),     "revisión sistemática sin metaanálisis"),
        ("rct",                 lambda: _si(s, "rct"),                  "ensayo aleatorizado"),
        ("ensayo_clinico",      lambda: _si(s, "ensayo"),               "ensayo clínico no aleatorizado"),
        ("cohorte_prospectiva", lambda: _si(s, "prospectivo") and not _si(s, "retrospectivo"),
         "datos recogidos prospectivamente"),
        ("cohorte_retrospectiva", lambda: _si(s, "retrospectivo"),      "datos retrospectivos de registro/historia"),
        ("serie_casos",         lambda: _si(s, "serie_casos"),          "serie de casos sin comparador"),
        ("caso_clinico",        lambda: _si(s, "caso"),                 "informe de un solo paciente"),
        ("revision_narrativa",  lambda: _si(s, "rev_narrativa"),        "revisión narrativa / opinión"),
    )
    for tier, cond, motivo in reglas:
        if cond():
            # Un preprint no deja de ser un RCT, pero hay que poder verlo: va en el motivo.
            if _si(s, "preprint"):
                motivo += " · ⚠️ PREPRINT, sin revisión por pares"
            return _resultado(tier, motivo, s, faltan)

    # Ninguna señal de diseño. Un preprint sin más es el último escalón nombrable.
    if _si(s, "preprint"):
        return _resultado("preprint", "preprint sin diseño identificable", s, faltan)

    # Fail-closed: sin señales de diseño no se inventa un tier. `desconocido` bloquea.
    return _resultado("desconocido",
                      "ninguna señal de diseño resuelta (faltan: %s)" % (", ".join(faltan) or "—"),
                      s, faltan)


def _resultado(tier, motivo, s, faltan):
    return {
        "tier": tier,
        "etiqueta": TIER_TXT[tier],
        "preclinico": tier in PRECLINICOS,
        "motivo": motivo,
        "senales": {k: v for k, v in s.items() if v is not None},
        "faltan": faltan,
        "entregable": tier != "desconocido",
    }


# ── FUENTE 1: EL REGISTRO (PubMed) ───────────────────────────────────────────
def _curl(url, accept="application/json"):
    p = subprocess.run(
        ["curl", "-sS", "-L", "--max-time", TIMEOUT, "-A", UA,
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


# PublicationType (NLM) -> señal. Verificado contra efetch el 17-sep-2026 con PMIDs reales
# de cada tipo (RCT, Case Reports, Meta-Analysis): estas cadenas son las que devuelve NLM.
_PT = {
    "practice guideline": "guia", "guideline": "guia",
    "consensus development conference": "guia",
    "consensus development conference, nih": "guia",
    "meta-analysis": "metaanalisis",
    "systematic review": "rev_sistematica",
    "randomized controlled trial": "rct",
    "controlled clinical trial": "rct",
    "clinical trial, phase iii": "rct",
    "clinical trial": "ensayo",
    "clinical trial, phase i": "ensayo",
    "clinical trial, phase ii": "ensayo",
    "case reports": "caso",
    "review": "rev_narrativa",
    "editorial": "rev_narrativa",
    "comment": "rev_narrativa",
    "preprint": "preprint",
}

# MeSH descriptor -> señal. Distinguir banco de clínica depende de esto, no del abstract.
_MESH = {
    "humans": "humanos",
    "animals": "animal", "mice": "animal", "rats": "animal",
    "disease models, animal": "animal",
    "xenograft model antitumor assays": "animal",
    "heterografts": "animal",
    "in vitro techniques": "in_vitro",
    "cell line, tumor": "in_vitro", "tumor cells, cultured": "in_vitro",
    "cells, cultured": "in_vitro", "cell line": "in_vitro",
    "retrospective studies": "retrospectivo",
    "prospective studies": "prospectivo",
    "cohort studies": "prospectivo",
    "longitudinal studies": "prospectivo",
}


def senales_desde_registro(pmid, fetch=None):
    """PMID -> (senales, procedencia). `fetch` inyectable para tests sin red.

    Fail-closed: si el registro no responde o el artículo aún no tiene MeSH asignado
    (pasa con lo recién indexado), las señales que dependen de MeSH quedan en None y
    el tejido acabará en `desconocido` en vez de inventarse un tier.
    """
    xml = (fetch or _fetch_pubmed)(pmid)
    if not xml:
        return {}, "registro mudo (fail-closed)"
    pts = [m.lower() for m in re.findall(r'<PublicationType[^>]*>([^<]*)</PublicationType>', xml)]
    mesh = [m.lower() for m in re.findall(r'<DescriptorName[^>]*>([^<]*)</DescriptorName>', xml)]
    if not pts and not mesh:
        return {}, "sin PublicationType ni MeSH en el registro"

    senales = {}
    for p in pts:
        k = _PT.get(p.strip())
        if k:
            senales[k] = True
    for d in mesh:
        k = _MESH.get(d.strip())
        if k:
            senales[k] = True
    # "Review" viene junto a "Meta-Analysis"/"Systematic Review" en los metaanálisis: si hay
    # método sistemático, la etiqueta de revisión narrativa sobra y confundiría la jerarquía.
    if senales.get("metaanalisis") or senales.get("rev_sistematica"):
        senales.pop("rev_narrativa", None)
    # Un RCT también trae "Clinical Trial": la señal débil no debe competir con la fuerte.
    if senales.get("rct"):
        senales.pop("ensayo", None)
    proc = "PubMed efetch · %d PublicationType · %d MeSH" % (len(pts), len(mesh))
    return senales, proc


def _fetch_pubmed(pmid):
    url = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
           "?db=pubmed&retmode=xml&id=" + str(pmid))
    code, body = _curl(url, accept="application/xml")
    if code != 200 or not body:
        return ""
    return body


# ── FUENTE 3: LÉXICO (solo degrada) ──────────────────────────────────────────
_LEX_BANCO = ("in vitro", "cell line", "línea celular", "linea celular", "cultivo celular",
              "xenograft", "xenoinjerto", "mouse model", "murine", "en ratones", "en ratón",
              "en raton", "organoid", "organoide", "pdx model")
_LEX_HUMANOS = ("patients", "pacientes", "participants", "participantes", "enrolled",
                "reclutados", "cohort of", "cohorte de")
_LEX_CASO = ("we report a case", "presentamos el caso", "case report", "a 54-year-old",
             "caso clínico", "caso clinico")


def senales_desde_texto(texto):
    """Red de léxico. SOLO señales que degradan (banco, caso) o que niegan preclínico.

    Porqué-no: aquí no se detecta "rct" ni "guía" a propósito. Un abstract que menciona
    "randomized" en su discusión ascendería un estudio en ratones a ensayo aleatorizado.
    Ascender por error es el fallo caro; degradar por error solo cuesta comprobarlo.
    """
    t = (texto or "").lower()
    s = {}
    if any(k in t for k in _LEX_BANCO):
        s["in_vitro" if ("vitro" in t or "cell" in t or "celular" in t) else "animal"] = True
        if any(k in t for k in ("mouse", "murine", "ratón", "raton", "xenograft", "xenoinjerto")):
            s["animal"] = True
    if any(k in t for k in _LEX_HUMANOS):
        s["humanos"] = True
    if any(k in t for k in _LEX_CASO):
        s["caso"] = True
    return s


def clasifica_pmid(pmid, fetch=None):
    senales, proc = senales_desde_registro(pmid, fetch=fetch)
    r = teje(senales)
    r["id"] = str(pmid)
    r["procedencia"] = proc
    return r


# ── CLI ──────────────────────────────────────────────────────────────────────
def _cuestionario():
    print("Cuestionario de señales — contesta SOLO sí/no por línea; el tier lo teje el código.")
    print("Devuelve un JSON {clave: true|false}; omite la clave si no lo sabes (no adivines).\n")
    for k, q in SENALES.items():
        print("  %-16s %s" % (k + ":", q))
    print("\nLuego: python3 tools/tier_evidencia.py --desde-respuestas '{\"rct\":true,\"humanos\":true}'")


def _pinta(r):
    marca = "🔬 PRECLÍNICO" if r["preclinico"] else ("⛔" if r["tier"] == "desconocido" else "✓")
    print("[%s] %s%s" % (marca, r["etiqueta"], (" · %s" % r["id"]) if r.get("id") else ""))
    print("     ↳ %s" % r["motivo"])
    if r.get("procedencia"):
        print("     ↳ fuente: %s" % r["procedencia"])
    if r["preclinico"]:
        print("     ⚠️  NO se puede escribir «esto funciona» encima de esto (regla 17).")


def main():
    argv = sys.argv[1:]
    as_json = False
    if argv and argv[0] in ("--json", "-j"):
        as_json, argv = True, argv[1:]
    if not argv:
        print(__doc__.strip().split("\n\n")[0])
        print("\nUso: python3 tools/tier_evidencia.py <PMID>... | --preguntas | "
              "--desde-respuestas '<json>' | --texto '<abstract>'")
        return 0
    if argv[0] == "--preguntas":
        _cuestionario()
        return 0
    if argv[0] == "--desde-respuestas":
        try:
            senales = json.loads(argv[1] if len(argv) > 1 else "{}")
        except (IndexError, ValueError):
            print("ERROR: falta un JSON válido de respuestas", file=sys.stderr)
            return 1
        r = teje(senales)
        r["procedencia"] = "cuestionario sí/no (el tejido lo hizo el código)"
        print(json.dumps(r, ensure_ascii=False, indent=2) if as_json else "")
        if not as_json:
            _pinta(r)
        return 0 if r["entregable"] else 1
    if argv[0] == "--texto":
        texto = argv[1] if len(argv) > 1 else sys.stdin.read()
        r = teje(senales_desde_texto(texto))
        r["procedencia"] = "léxico del texto (solo degrada; nunca asciende a RCT/guía)"
        print(json.dumps(r, ensure_ascii=False, indent=2) if as_json else "")
        if not as_json:
            _pinta(r)
        return 0 if r["entregable"] else 1

    # PMIDs
    out = []
    for raw in argv:
        m = re.search(r'(\d{1,8})', raw or "")
        if not m:
            out.append({"id": raw, "tier": "desconocido", "etiqueta": TIER_TXT["desconocido"],
                        "preclinico": False, "entregable": False,
                        "motivo": "no encontré un PMID en la cadena", "senales": {}, "faltan": list(SENALES)})
            continue
        out.append(clasifica_pmid(m.group(1)))
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print("=== Tier de evidencia (determinista · el código teje, el modelo no decide) ===")
        for r in out:
            _pinta(r)
        npre = sum(1 for r in out if r["preclinico"])
        nds = sum(1 for r in out if r["tier"] == "desconocido")
        print("Resumen: %d clasificadas · %d PRECLÍNICAS · %d desconocidas" % (len(out), npre, nds))
    return 1 if any(not r["entregable"] for r in out) else 0


if __name__ == "__main__":
    sys.exit(main())
