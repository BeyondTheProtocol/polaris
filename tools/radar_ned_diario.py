#!/usr/bin/env python3
"""tools/radar_ned_diario.py — barrido DIARIO y DETERMINISTA de novedades que pueden acercar a NED.

Por qué existe (17-sep-2026): el radar de literatura (`tools/radar_literatura.py`) es MENSUAL,
cuesta dinero (Perplexity) y el 1-sep no corrió (HALT activo). {{TITULAR}} pidió revisión DIARIA y
"en todo el internet". Este módulo es la mirada GRATIS de ese carril: barre las fuentes públicas
que se consultan sin clave y sin LLM, deduplica contra lo ya visto, y solo cuando hay algo NUEVO
en una diana de prioridad ALTA da paso al triaje que sí piensa (y cuesta). Espejo de la filosofía
de `tools/vega_gate.py`: mirar es gratis, pensar se gatea.

Fuentes (todas públicas, sin clave, $0, HTTPS, GET):
  1. Europe PMC  — indexa PubMed/MEDLINE + PMC + **preprints** (bioRxiv/medRxiv, SRC:PPR).
  2. Europe PMC  — colección de **patentes** (SRC:PAT). Una patente nueva de una diana llega
     años antes que el paper; así se detectó la de FGFR4 de Ona.
  3. ClinicalTrials.gov API v2 — ensayos nuevos o con cambio de estado en la ventana.

Lo que este módulo NO cubre, a propósito (necesita navegador o juicio, no se finge aquí):
  · **CTIS** (registro de ensayos de la UE): SPA Angular, sin API pública → solo Chrome MCP.
    ONA-255 vive SOLO ahí, no en ClinicalTrials.gov. El digest lo deja escrito como pendiente.
  · Congresos (ASCO/ESMO/SABCS/AACR), notas de prensa de biotech y X: carriles propios
    (`tools/x_radar.py`) o sesión con navegador.

Anti-inyección: el JSON de cada fuente es DATO EXTERNO. Solo se extraen campos escalares
(id, fecha, título, estado, doi) y el título se sanea (una línea, sin markdown, recortado).
Nada de lo que venga de fuera se ejecuta ni se interpola en un prompt desde aquí.

Privacidad / borde: cada consulta pasa por `borde.egress_cientifico` (fail-closed). Las queries
son términos de ciencia genéricos por construcción — NUNCA nombre, contacto, variante ni
citobanda (por eso el tema de CCND1 se pregunta como "CCND1 amplification", no por la banda).

Uso:
  python3 tools/radar_ned_diario.py run       # barre, escribe digest + estado, avisa si ALTA
  python3 tools/radar_ned_diario.py run --dry  # barre pero NO escribe nada ni avisa
  python3 tools/radar_ned_diario.py check     # rc 0 = hay novedad ALTA sin triar; rc 1 = nada
  python3 tools/radar_ned_diario.py status    # qué hay en el estado, sin red
  Opciones: --dias N (ventana, def. 3) · --tema CLAVE (solo ese) · --sin-aviso
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
DIR_ESTADO = os.path.join(STATE, "radar_ned")
VISTO = os.path.join(DIR_ESTADO, "visto.json")
ULTIMO = os.path.join(DIR_ESTADO, "ultimo.json")
COLA = os.path.join(DIR_ESTADO, "cola_verificacion.json")
RADAR_DIR = os.path.join(REPO, "00_FUENTE-DE-VERDAD", "04 · IA", "Radar")

UA = "btp-radar-ned/1.0 (investigacion clinica personal; contacto via helptitular.com)"
TIMEOUT = 25
MAX_POR_FUENTE = 25
MAX_TITULO = 160
PAT_DIAS = 400   # antiguedad maxima de una patente para entrar en el digest
PAT_TOPE = 5     # tope de patentes por tema (el indice PAT no filtra por fecha)
COLA_TOPE = 40   # si la cola pasa de aqui, el sistema esta fallando: se dice, no se acumula
COLA_TOPE_ABIERTO = 3  # por tema ABIERTO y dia: la capa amplia informa, no inunda la cola

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
CTGOV = "https://clinicaltrials.gov/api/v2/studies"

# --- WATCHLIST -----------------------------------------------------------------------------
# Las dianas del caso a 17-sep-2026. `prio` ALTA = si hay algo nuevo, se despierta al triaje.
# Fuente de las dianas: revisión central {{CENTRO}} 19-may-2026 (luminal B HER2-, NE focal), DIPCAN
# 2024 (FGF19 x18), protocolo ONA-255-101 (CTIS 2025-522233-62-00) y el hueco NE marcado dos
# ciclos seguidos por el radar mensual. Al tocar esta lista, decir por qué en el CHANGELOG.
TEMAS_DIANA = [
    {
        "clave": "fgfr4",
        "titulo": "FGFR4 / FGF19 como diana (ADC, CAR-T, inhibidor, degradador)",
        "prio": "alta",
        "epmc": '(TITLE:"FGFR4" OR ABSTRACT:"FGFR4" OR TITLE:"FGF19" OR ABSTRACT:"FGF19") '
                'AND (TITLE:cancer OR TITLE:tumor OR TITLE:tumour OR TITLE:carcinoma '
                'OR ABSTRACT:cancer OR ABSTRACT:carcinoma)',
        "ctgov": 'AREA[InterventionName]("FGFR4" OR "FGF19") OR AREA[BriefTitle]("FGFR4" OR "FGF19")',
    },
    {
        "clave": "ona255",
        "titulo": "ONA-255 y Ona Therapeutics (ADC anti-FGFR4 con MMAE)",
        "prio": "alta",
        "epmc": '(TITLE:"ONA-255" OR ABSTRACT:"ONA-255" OR ABSTRACT:"Ona Therapeutics" '
                'OR (TITLE:"FGFR4" AND ABSTRACT:"antibody-drug conjugate"))',
        "ctgov": 'AREA[InterventionName]("ONA-255") OR AREA[LeadSponsorName]("Ona Therapeutics")',
    },
    {
        "clave": "ne-mama",
        "titulo": "Diferenciación neuroendocrina en mama ({{DIANA3}}, {{DIANA4}}, {{DIANA2}})",
        "prio": "alta",
        "epmc": '((TITLE:"neuroendocrine" OR ABSTRACT:"neuroendocrine differentiation") '
                'AND (TITLE:"breast" OR ABSTRACT:"breast")) '
                'OR ((TITLE:"{{DIANA2}}" OR TITLE:"{{DIANA3}}" OR TITLE:"{{DIANA4}}" OR ABSTRACT:"{{DIANA2}}") '
                'AND (TITLE:"breast" OR ABSTRACT:"breast"))',
        "ctgov": 'AREA[ConditionSearch]("breast") AND (AREA[BriefTitle]("neuroendocrine" OR "{{DIANA2}}") '
                 'OR AREA[InterventionName]("{{DIANA2}}" OR "tarlatamab"))',
    },
    {
        "clave": "adc-hr",
        "titulo": "ADC en mama HR+/HER2- (payload exatecan, TROP2, HER3, B7-H4)",
        "prio": "alta",
        "epmc": '(TITLE:"antibody-drug conjugate" OR ABSTRACT:"antibody-drug conjugate" '
                'OR TITLE:"exatecan" OR TITLE:"datopotamab" OR TITLE:"patritumab" OR ABSTRACT:"HER2-ultralow" OR ABSTRACT:"HER2 ultralow") '
                'AND (TITLE:"breast" OR ABSTRACT:"breast") '
                'AND (ABSTRACT:"hormone receptor" OR ABSTRACT:"HER2-negative" OR ABSTRACT:"luminal" '
                'OR TITLE:"hormone receptor")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND (AREA[InterventionName]("antibody-drug conjugate" '
                 'OR "exatecan" OR "datopotamab" OR "patritumab" OR "trastuzumab deruxtecan") '
                 'OR AREA[BriefTitle]("HER2-ultralow" OR "HER2 ultralow"))',
    },
    {
        "clave": "cdk",
        "titulo": "Resistencia a CDK4/6 y CDK2 (contexto RB1 perdido)",
        "prio": "alta",
        "epmc": '(TITLE:"CDK2" OR ABSTRACT:"CDK2 inhibitor" OR ABSTRACT:"CDK4/6 resistance") '
                'AND (TITLE:"breast" OR ABSTRACT:"breast") '
                'AND (ABSTRACT:resistance OR ABSTRACT:"RB1" OR TITLE:resistance)',
        "ctgov": 'AREA[ConditionSearch]("breast") AND AREA[InterventionName]("CDK2")',
    },
    {
        "clave": "prrt",
        "titulo": "Teranostica {{DIANA}} / PRRT en mama (DOTATATE, 177Lu, 225Ac)",
        "prio": "media",
        "epmc": '(TITLE:"DOTATATE" OR ABSTRACT:"DOTATATE" OR ABSTRACT:"peptide receptor radionuclide" '
                'OR TITLE:"{{DIANA}}" OR ABSTRACT:"somatostatin receptor") '
                'AND (TITLE:"breast" OR ABSTRACT:"breast")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND (AREA[InterventionName]("dotatate" OR "lutetium" '
                 'OR "PRRT") OR AREA[BriefTitle]("DOTATATE" OR "somatostatin"))',
    },
    {
        "clave": "vacuna-frio",
        "titulo": "Vacunas / neoantigenos de ARN en tumor frio y TMB baja",
        "prio": "media",
        "epmc": '(TITLE:"neoantigen" OR ABSTRACT:"neoantigen" OR TITLE:"cancer vaccine" '
                'OR ABSTRACT:"mRNA vaccine") AND (TITLE:"breast" OR ABSTRACT:"breast") '
                'AND (ABSTRACT:"tumor mutational burden" OR ABSTRACT:"cold tumor" '
                'OR ABSTRACT:"immune desert" OR ABSTRACT:splicing OR ABSTRACT:"RNA-derived")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND AREA[InterventionName]("neoantigen" '
                 'OR "personalized vaccine" OR "mRNA vaccine")',
    },
    {
        "clave": "ccnd1-fgfr1",
        "titulo": "Amplificacion CCND1 / FGFR1 en mama (inhibidor o degradador)",
        "prio": "media",
        "epmc": '(ABSTRACT:"CCND1 amplification" OR ABSTRACT:"FGFR1 amplification" '
                'OR TITLE:"FGFR degrader" OR TITLE:"rogaratinib" OR TITLE:"erdafitinib") '
                'AND (TITLE:"breast" OR ABSTRACT:"breast")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND AREA[InterventionName]("erdafitinib" '
                 'OR "rogaratinib" OR "FGFR inhibitor")',
    },
    {
        "clave": "serd",
        "titulo": "SERD orales y ESR1 (camizestrant, imlunestrant, elacestrant)",
        "prio": "media",
        "epmc": '(TITLE:"camizestrant" OR TITLE:"imlunestrant" OR TITLE:"elacestrant" '
                'OR ABSTRACT:"oral SERD" OR ABSTRACT:"selective estrogen receptor degrader") '
                'AND (TITLE:"breast" OR ABSTRACT:"breast")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND AREA[InterventionName]("camizestrant" '
                 'OR "imlunestrant" OR "elacestrant")',
    },
    {
        "clave": "ned-oligo",
        "titulo": "Intencion de NED: oligometastasico, tratamiento local, enfermedad osea",
        "prio": "media",
        "epmc": '(TITLE:"oligometastatic" OR ABSTRACT:"oligometastatic" '
                'OR TITLE:"no evidence of disease" OR ABSTRACT:"local consolidative") '
                'AND (TITLE:"breast" OR ABSTRACT:"breast")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND AREA[BriefTitle]("oligometastatic" '
                 'OR "no evidence of disease" OR "local consolidative")',
    },
]

# --- CAPA ABIERTA (18-sep-2026) ---------------------------------------------------------------
# {{TITULAR}} lo dijo así: "son novedades en general que nos acerquen a NED, da igual de lo que sea".
# La lista de arriba solo encuentra lo que ya sabemos buscar; por construcción NO puede traer una
# diana que nadie ha nombrado todavía. Esta capa barre SIN saber qué busca: agentes y ensayos
# nuevos sea cual sea su mecanismo, el vocabulario de la erradicación (respuesta completa, curación,
# enfermedad residual), lo que revierte resistencia, y lo que funciona en OTRO tumor con su misma
# biología. Prioridad "abierta": entra al digest y a la cola con tope, pero no dispara aviso por sí
# sola — el juicio de si acerca a NED es de la sesión, no de una lista de palabras.
TEMAS_ABIERTOS = [
    {
        "clave": "nuevo-agente",
        "titulo": "Agentes y ensayos NUEVOS en mama, sea cual sea la diana",
        "prio": "abierta",
        "epmc": '(TITLE:"first-in-class" OR ABSTRACT:"first-in-class" OR ABSTRACT:"first-in-human" '
                'OR TITLE:"novel target" OR ABSTRACT:"first-in-human study") '
                'AND (TITLE:"breast" OR ABSTRACT:"metastatic breast")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND AREA[StudyType]INTERVENTIONAL',
        # ensayos recien REGISTRADOS (no actualizados): asi aparecen dianas que aun no tienen nombre
        "ctgov_filtro": "AREA[StudyFirstPostDate]RANGE[{desde},MAX] AND AREA[Phase](PHASE1 OR PHASE2)",
    },
    {
        "clave": "ned-erradicacion",
        "titulo": "Erradicar, no contener: respuesta completa, curacion, oligometastasico",
        "prio": "abierta",
        "epmc": '(ABSTRACT:"complete response" OR ABSTRACT:"eradication" OR ABSTRACT:"cure" '
                'OR ABSTRACT:"exceptional responder" OR ABSTRACT:"durable remission" '
                'OR ABSTRACT:"treatment-free") AND (ABSTRACT:"metastatic breast" OR TITLE:"breast")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND AREA[BriefTitle]("complete response" '
                 'OR "curative" OR "eradication" OR "treatment-free" OR "de-escalation")',
    },
    {
        "clave": "residual",
        "titulo": "Medir si hay NED: enfermedad residual molecular, ctDNA, imagen sensible",
        "prio": "abierta",
        "epmc": '(ABSTRACT:"minimal residual disease" OR ABSTRACT:"molecular residual disease" '
                'OR ABSTRACT:"ctDNA-guided" OR TITLE:"residual disease") '
                'AND (TITLE:"breast" OR ABSTRACT:"breast cancer")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND (AREA[BriefTitle]("ctDNA" OR "residual disease" '
                 'OR "minimal residual") OR AREA[InterventionName]("ctDNA"))',
    },
    {
        "clave": "resistencia",
        "titulo": "Revertir resistencia (endocrina, CDK4/6) por cualquier mecanismo",
        "prio": "abierta",
        "epmc": '(TITLE:"resistance" OR ABSTRACT:"overcome resistance" OR ABSTRACT:"reverse resistance" '
                'OR ABSTRACT:"resensitiz") AND (ABSTRACT:"endocrine therapy" OR ABSTRACT:"CDK4/6") '
                'AND (TITLE:"breast" OR ABSTRACT:"breast cancer")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND AREA[BriefTitle]("resistant" OR "resistance") '
                 'AND AREA[InterventionType]DRUG',
    },
    {
        "clave": "trasladable",
        "titulo": "Mismo mecanismo en OTRO tumor con su biologia (NE, FGF/FGFR, ciclina D1)",
        "prio": "abierta",
        "epmc": '((TITLE:"neuroendocrine" AND (ABSTRACT:"targeted therapy" OR ABSTRACT:"antibody-drug conjugate" '
                'OR ABSTRACT:"bispecific" OR ABSTRACT:"{{DIANA2}}")) OR (ABSTRACT:"FGF19" OR ABSTRACT:"FGFR4" '
                'OR TITLE:"cyclin D1")) AND (ABSTRACT:"tumor" OR ABSTRACT:"tumour" OR ABSTRACT:"carcinoma") '
                'AND (ABSTRACT:"treatment" OR ABSTRACT:"therapy" OR ABSTRACT:"trial")',
        "ctgov": 'AREA[InterventionName]("FGFR4" OR "{{DIANA2}}" OR "{{DIANA3}}") OR AREA[BriefTitle]("cyclin D1")',
    },
]


# --- CAPA DE MODALIDADES (18-sep-2026) --------------------------------------------------------
# Segunda correccion suya, el mismo dia: "no solo ensayos, toda la literatura que a mi me puede
# afectar: CAR-T, vacunas...". Las dos capas anteriores buscan por DIANA y por NOVEDAD REGISTRADA;
# ninguna busca por PLATAFORMA. Una terapia celular que funciona en otro tumor solido, o una vacuna
# que resuelve el problema del tumor frio, no llevan el nombre de ninguna de sus dianas y pueden no
# tener aun un ensayo: viven en la literatura. Esta capa barre por modalidad terapeutica, sin
# exigir que haya ensayo detras, e incluye preprints (Europe PMC indexa bioRxiv/medRxiv como PPR).
TEMAS_MODALIDAD = [
    {
        "clave": "celular",
        "titulo": "Terapia celular: CAR-T, CAR-NK, TIL, TCR-T, CAR in vivo",
        "prio": "abierta",
        "epmc": '(TITLE:"CAR-T" OR ABSTRACT:"CAR T cell" OR ABSTRACT:"CAR-NK" OR ABSTRACT:"TCR-T" '
                'OR ABSTRACT:"tumor-infiltrating lymphocyte" OR ABSTRACT:"in vivo CAR" '
                'OR ABSTRACT:"adoptive cell therapy") '
                'AND (ABSTRACT:"solid tumor" OR ABSTRACT:"solid tumour" OR ABSTRACT:"breast")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND AREA[InterventionName]("CAR-T" OR "CAR T" '
                 'OR "TIL" OR "cell therapy")',
    },
    {
        "clave": "vacunas",
        "titulo": "Vacunas terapeuticas y virus oncoliticos (cualquier plataforma)",
        "prio": "abierta",
        "epmc": '(ABSTRACT:"cancer vaccine" OR ABSTRACT:"therapeutic vaccine" OR ABSTRACT:"mRNA vaccine" '
                'OR ABSTRACT:"dendritic cell vaccine" OR ABSTRACT:"peptide vaccine" '
                'OR ABSTRACT:"oncolytic") AND (ABSTRACT:"breast" OR ABSTRACT:"solid tumor" '
                'OR ABSTRACT:"solid tumour")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND AREA[InterventionName]("vaccine" OR "oncolytic")',
    },
    {
        "clave": "inmuno-frio",
        "titulo": "Convertir un tumor FRIO en caliente (TILs bajos, MSS, TMB baja)",
        "prio": "abierta",
        "epmc": '(ABSTRACT:"cold tumor" OR ABSTRACT:"immune desert" OR ABSTRACT:"immunologically cold" '
                'OR ABSTRACT:"turn cold tumors hot" OR ABSTRACT:"immune exclusion" '
                'OR ABSTRACT:"STING agonist") AND (ABSTRACT:"breast" OR ABSTRACT:"solid tumor")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND AREA[InterventionName]("STING" OR "TLR" '
                 'OR "interleukin" OR "oncolytic")',
    },
    {
        "clave": "radioligando",
        "titulo": "Radioligandos y teranostica en mama (cualquier diana)",
        "prio": "abierta",
        "epmc": '(ABSTRACT:"radioligand" OR ABSTRACT:"radiopharmaceutical" OR ABSTRACT:"theranostic" '
                'OR ABSTRACT:"177Lu" OR ABSTRACT:"225Ac" OR ABSTRACT:"212Pb" '
                'OR ABSTRACT:"targeted radionuclide") AND (ABSTRACT:"breast" OR TITLE:"breast")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND AREA[InterventionName]("lutetium" OR "actinium" '
                 'OR "radioligand" OR "radionuclide")',
    },
    {
        "clave": "mecanismo-nuevo",
        "titulo": "Revertir el fenotipo: epigenetica, diferenciacion, metabolismo, reposicionamiento",
        "prio": "abierta",
        "epmc": '(ABSTRACT:"epigenetic therapy" OR ABSTRACT:"differentiation therapy" '
                'OR ABSTRACT:"metabolic vulnerability" OR ABSTRACT:"drug repurposing" '
                'OR ABSTRACT:"synthetic lethality" OR ABSTRACT:"dormancy") '
                'AND (ABSTRACT:"breast cancer" OR ABSTRACT:"metastatic")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND AREA[BriefTitle]("repurpos" OR "dormancy" '
                 'OR "epigenetic")',
    },
]


# --- CAPA CHINA (18-sep-2026) -----------------------------------------------------------------
# Peticion suya: "busca en China tambien, que para eso tenemos hueco en servidor alli". China
# produce hoy buena parte de los ADC, biespecificos y terapias celulares nuevas, y muchas de esas
# moleculas llegan a Europa anios despues -- o nunca. Dos vias, y aqui va la primera:
#   1. ESTA capa: Europe PMC filtrando por AFILIACION china (AFF:"China"). Funciona desde Espana,
#      $0, sin proxy, y captura la investigacion china publicada en ingles, que es donde vive lo
#      importante (Hengrui, BeiGene, Kelun, SystImmune...).
#   2. tools/radar_cn.py: los registros que NO estan en Europe PMC ni en ClinicalTrials.gov (CDE
#      de la NMPA, ChinaXiv) via el VPS de Hong Kong + proxy chino. Verificado el 18-sep-2026: el
#      VPS responde, el CDE y ChinaXiv abren por proxy, y ChiCTR solo sirve la portada (su buscador
#      responde 405 sin navegador). Lo que exige navegador queda marcado, no fingido.
TEMAS_CN = [
    {
        "clave": "cn-adc",
        "titulo": "China: ADC y biespecificos (Hengrui, Kelun, SystImmune, BeiGene...)",
        "prio": "abierta",
        "cn": True,
        "epmc": '(AFF:"China") AND (ABSTRACT:"antibody-drug conjugate" OR ABSTRACT:"bispecific antibody" '
                'OR TITLE:"antibody-drug conjugate") AND (ABSTRACT:"breast" OR ABSTRACT:"solid tumor")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND AREA[LocationCountry]("China") '
                 'AND AREA[InterventionName]("antibody-drug conjugate" OR "ADC")',
    },
    {
        "clave": "cn-celular",
        "titulo": "China: terapia celular en tumores solidos (CAR-T, CAR-NK, TIL)",
        "prio": "abierta",
        "cn": True,
        "epmc": '(AFF:"China") AND (ABSTRACT:"CAR-T" OR ABSTRACT:"CAR T cell" OR ABSTRACT:"CAR-NK" '
                'OR ABSTRACT:"cell therapy" OR ABSTRACT:"TCR-T") '
                'AND (ABSTRACT:"solid tumor" OR ABSTRACT:"solid tumour" OR ABSTRACT:"breast")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND AREA[LocationCountry]("China") '
                 'AND AREA[InterventionName]("CAR-T" OR "CAR T" OR "cell therapy")',
    },
    {
        "clave": "cn-inmuno",
        "titulo": "China: vacunas, inmunoterapia y radioligandos",
        "prio": "abierta",
        "cn": True,
        "epmc": '(AFF:"China") AND (ABSTRACT:"cancer vaccine" OR ABSTRACT:"neoantigen" '
                'OR ABSTRACT:"oncolytic" OR ABSTRACT:"radioligand" OR ABSTRACT:"radionuclide therapy") '
                'AND (ABSTRACT:"breast" OR ABSTRACT:"solid tumor")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND AREA[LocationCountry]("China") '
                 'AND AREA[InterventionName]("vaccine" OR "radionuclide" OR "oncolytic")',
    },
    {
        "clave": "cn-dianas",
        "titulo": "China: sus dianas concretas (FGFR4/FGF19, {{DIANA2}}, TROP2, HER3, neuroendocrino)",
        "prio": "abierta",
        "cn": True,
        "epmc": '(AFF:"China") AND (ABSTRACT:"FGFR4" OR ABSTRACT:"FGF19" OR ABSTRACT:"{{DIANA2}}" '
                'OR ABSTRACT:"TROP2" OR ABSTRACT:"HER3" OR ABSTRACT:"neuroendocrine") '
                'AND (ABSTRACT:"cancer" OR ABSTRACT:"tumor" OR ABSTRACT:"carcinoma")',
        "ctgov": 'AREA[LocationCountry]("China") AND AREA[InterventionName]("FGFR4" OR "{{DIANA2}}" '
                 'OR "TROP2" OR "HER3")',
    },
]

TEMAS = TEMAS_DIANA + TEMAS_ABIERTOS + TEMAS_MODALIDAD + TEMAS_CN
CLAVES = [t["clave"] for t in TEMAS]


# --- utilidades ----------------------------------------------------------------------------
def _hoy():
    return datetime.now().date()


def _limpia(texto):
    """Sanea un campo de texto EXTERNO: una línea, sin markdown ni control, recortado."""
    if not isinstance(texto, str):
        return ""
    t = re.sub(r"[\r\n\t]+", " ", texto)
    t = re.sub(r"[`*_\[\]<>|]", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t[:MAX_TITULO]


def _get(url, params):
    q = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    req = urllib.request.Request(url + "?" + q, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _borde_ok(query, destino):
    """fail-closed: sin borde no se sale a la red."""
    try:
        sys.path.insert(0, os.path.join(REPO, "tools"))
        import borde
    except Exception as e:  # noqa: BLE001
        return False, f"borde no importable ({e.__class__.__name__})"
    try:
        return borde.egress_cientifico(query, destino=destino)
    except Exception as e:  # noqa: BLE001
        return False, f"borde falló ({e.__class__.__name__})"



# Heuristica de VALIDEZ EXTERNA (18-sep-2026). Al abrir el barrido a modalidades entra mucha
# literatura de OTRO subtipo (triple negativo, HER2-positivo, microcitico) o de modelo animal.
# No se descarta -- un mecanismo puede ser trasladable -- pero el digest lo DICE, para que nadie
# lea un resultado de 4T1 murino o de TNBC como si fuera de su luminal B. Es texto, no juicio:
# marca la sospecha, no dictamina.
_OTRO_SUBTIPO = re.compile(
    r"triple[- ]negative|\bTNBC\b|HER2[- ]positive|small[- ]cell|\bSCLC\b|"
    r"pancrea|gastric|hepatocellular|\bHCC\b|colorectal|prostate|melanoma|glioblastoma|ovarian",
    re.I)
_PRECLINICO = re.compile(
    r"\bmurine\b|\bmice\b|\bmouse\b|4T1|xenograft|in vitro|cell line|organoid|\bPDX\b",
    re.I)


def _flags(titulo):
    f = []
    if _OTRO_SUBTIPO.search(titulo or ""):
        f.append("otro subtipo")
    if _PRECLINICO.search(titulo or ""):
        f.append("preclinico")
    return f


# --- fuentes -------------------------------------------------------------------------------
def fuente_epmc(tema, desde, hasta, solo=None):
    """Europe PMC. solo=None → MED/PMC/PPR (papers + preprints); solo='PAT' → patentes."""
    base = tema["epmc"]
    if solo == "PAT":
        q = f'SRC:PAT AND ({base})'
        params_fecha = {}
    else:
        q = f'({base}) AND (FIRST_PDATE:[{desde} TO {hasta}])'
        etiqueta, params_fecha = "paper/preprint", {}
    ok, motivo = _borde_ok(q, destino="europepmc")
    if not ok:
        return [], f"borde: {motivo}"
    params = {"query": q, "format": "json", "pageSize": str(MAX_POR_FUENTE),
              "sort": "P_PDATE_D desc", "resultType": "lite"}
    params.update(params_fecha)
    try:
        d = _get(EPMC, params)
    except Exception as e:  # noqa: BLE001
        return [], f"error de red ({e.__class__.__name__})"
    out = []
    for r in (d.get("resultList", {}) or {}).get("result", []) or []:
        src, rid = _limpia(r.get("source"))[:8], _limpia(r.get("id"))[:24]
        if not rid:
            continue
        doi = _limpia(r.get("doi"))
        tipo = "preprint" if src == "PPR" else ("patente" if src == "PAT" else "paper")
        out.append({
            "uid": f"epmc:{src}:{rid}",
            "tipo": tipo,
            "titulo": _limpia(r.get("title")),
            "fecha": _limpia(r.get("firstPublicationDate")),
            "ref": doi or (f"PMID {rid}" if src == "MED" else rid),
            "url": (f"https://doi.org/{doi}" if doi else
                    f"https://europepmc.org/article/{src}/{rid}"),
            "flags": _flags(_limpia(r.get("title"))),
        })
    if solo == "PAT":
        # sin filtro de fecha en el indice: recorto a lo publicado en los ultimos PAT_DIAS
        corte = (_hoy() - timedelta(days=PAT_DIAS)).isoformat()
        out = [o for o in out if (o["fecha"] or "") >= corte][:PAT_TOPE]
    return out, None


def fuente_ctgov(tema, desde, hasta, **_):
    """ClinicalTrials.gov v2: estudios con cambio publicado dentro de la ventana."""
    term = tema.get("ctgov")
    if not term:
        return [], None
    ok, motivo = _borde_ok(term, destino="clinicaltrials.gov")
    if not ok:
        return [], f"borde: {motivo}"
    filtro = (tema.get("ctgov_filtro") or "AREA[LastUpdatePostDate]RANGE[{desde},MAX]").format(desde=desde)
    params = {
        "query.term": term,
        "filter.advanced": filtro,
        "pageSize": str(MAX_POR_FUENTE),
        "fields": "NCTId,BriefTitle,OverallStatus,LastUpdatePostDate,Phase",
        "format": "json",
    }
    try:
        d = _get(CTGOV, params)
    except Exception as e:  # noqa: BLE001
        return [], f"error de red ({e.__class__.__name__})"
    out = []
    for s in d.get("studies", []) or []:
        p = (s.get("protocolSection") or {})
        ident, st = p.get("identificationModule") or {}, p.get("statusModule") or {}
        nct = _limpia(ident.get("nctId"))[:16]
        if not nct:
            continue
        estado = _limpia(st.get("overallStatus"))[:32]
        fecha = _limpia(((st.get("lastUpdatePostDateStruct") or {}).get("date")))
        out.append({
            # el uid lleva el estado: si un ensayo cambia de estado, vuelve a ser novedad
            "uid": f"ctgov:{nct}:{estado}",
            "tipo": "ensayo",
            "titulo": f"[{estado}] {_limpia(ident.get('briefTitle'))}",
            "fecha": fecha,
            "ref": nct,
            "url": f"https://clinicaltrials.gov/study/{nct}",
            "flags": _flags(_limpia(ident.get("briefTitle"))),
        })
    return out, None


# OnCo (onco.cc, 19-sep-2026): grafo abierto de oncologia, consultado en LOCAL (`tools/onco.py`).
# Solo trae lo NUEVO o RE-VERIFICADO entre dos builds del volcado, y solo en los temas de diana
# con palabras propias aqui. Ninguna consulta sale: el unico GET es el volcado publico. Es mapa,
# no evidencia: el lead se abre en la fuente primaria que enlaza el registro, como todo el radar.
ONCO_CLAVES = {
    "fgfr4": ("fgfr4", "fgf19"),
    "ona255": ("ona-255", "ona therapeutics"),
    "ne-mama": ("{{DIANA2}}", "{{DIANA3}}", "{{DIANA4}}"),
    "vacuna-frio": ("neoantigen", "personalized vaccine", "personalised vaccine", "mrna cancer vaccine"),
    "ccnd1-fgfr1": ("ccnd1", "fgfr1"),
    "serd": ("camizestrant", "imlunestrant", "elacestrant", "vepdegestrant", "giredestrant", "palazestrant"),
}
ONCO_TIPOS = ("trial", "drug", "paper", "idea", "target", "company")
_ONCO = {}


def _onco_novedades():
    """Una sola sync por barrido. Sin volcado previo no hay novedades (la 1a sync no inunda)."""
    if "items" in _ONCO or "error" in _ONCO:
        return _ONCO.get("items"), _ONCO.get("error")
    try:
        aqui = os.path.dirname(os.path.abspath(__file__))
        if aqui not in sys.path:
            sys.path.insert(0, aqui)
        import onco
        onco.sync()
        previo = onco.cargar("all.prev.ndjson")
        _ONCO["items"] = onco.novedades(onco.cargar(), previo, None) if previo else []
        _ONCO["texto"] = onco._texto
    except Exception as e:  # noqa: BLE001
        _ONCO["error"] = f"OnCo no disponible ({e.__class__.__name__})"
    return _ONCO.get("items"), _ONCO.get("error")


def fuente_onco(tema, desde, hasta, **_):
    claves = ONCO_CLAVES.get(tema.get("clave"))
    if not claves:
        return [], None
    items, err = _onco_novedades()
    if err:
        return [], err
    out = []
    for r in items:
        if r.get("kind") not in ONCO_TIPOS or not any(k in _ONCO["texto"](r) for k in claves):
            continue
        nombre = _limpia(r.get("name"))
        out.append({
            "uid": f"onco:{r['id']}:{r.get('asOf') or ''}",
            "tipo": r.get("kind"),
            "titulo": f"[OnCo {r.get('_novedad', '')}] {nombre}",
            "fecha": r.get("asOf") or "",
            "ref": r.get("nct") or r["id"],
            "url": "https://onco.cc" + (r.get("route") or ""),
            "flags": _flags(nombre),
        })
    return out[:MAX_POR_FUENTE], None


FUENTES = [
    ("Europe PMC (papers + preprints)", fuente_epmc, {}),
    ("Europe PMC (patentes)", fuente_epmc, {"solo": "PAT"}),
    ("ClinicalTrials.gov", fuente_ctgov, {}),
    ("OnCo (grafo abierto, local)", fuente_onco, {}),
]


# --- estado --------------------------------------------------------------------------------
def carga_visto():
    try:
        with open(VISTO) as f:
            d = json.load(f)
        return set(d if isinstance(d, list) else d.get("uids", []))
    except (OSError, json.JSONDecodeError):
        return set()


def guarda_visto(uids):
    os.makedirs(DIR_ESTADO, exist_ok=True)
    # tope duro: el caché no crece sin fin (se queda con los más recientes añadidos)
    lista = list(uids)[-8000:]
    tmp = VISTO + ".tmp"
    with open(tmp, "w") as f:
        json.dump(lista, f)
    os.replace(tmp, VISTO)


def guarda_ultimo(res):
    os.makedirs(DIR_ESTADO, exist_ok=True)
    tmp = ULTIMO + ".tmp"
    with open(tmp, "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ULTIMO)


def lee_ultimo():
    try:
        with open(ULTIMO) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}



# --- cola de verificación ------------------------------------------------------------------
# La REGLA que manda (capa NED del CLAUDE.md): ninguna cita cuenta hasta abrirla en la sesión.
# El barrido diario es automático; ABRIR la fuente no lo es (en modo autónomo el muro deniega
# WebFetch/WebSearch a propósito: texto externo crudo no entra al lazo privilegiado). Por eso
# lo que el barrido produce NO es un juicio, es una COLA: cada lead de una diana de prioridad
# ALTA queda aquí hasta que alguien lo abre y lo cierra con veredicto. La cola se inyecta en la
# brújula (tools/contexto_lazo.py) → aparece en CADA sesión y en CADA job. Lo que no está en
# pantalla no existe; esto es el mecanismo que lo pone en pantalla siempre.
def lee_cola():
    try:
        with open(COLA) as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except (OSError, json.JSONDecodeError):
        pass
    return {"pendientes": [], "cerrados": []}


def guarda_cola(c):
    os.makedirs(DIR_ESTADO, exist_ok=True)
    c["cerrados"] = c.get("cerrados", [])[-200:]
    tmp = COLA + ".tmp"
    with open(tmp, "w") as f:
        json.dump(c, f, ensure_ascii=False, indent=1)
    os.replace(tmp, COLA)


def encola(res):
    """Mete en la cola los leads de diana ALTA que aun no estan ni pendientes ni cerrados.
    Las patentes NO entran (el indice no da fecha fiable; son contexto, no lead que verificar)."""
    c = lee_cola()
    ya = {p["uid"] for p in c["pendientes"]} | {p["uid"] for p in c.get("cerrados", [])}
    nuevos = fuera_cupo = 0
    for t in res["temas"]:
        if t["prio"] not in ("alta", "abierta"):
            continue
        # las dianas ALTA entran enteras; la capa abierta entra con tope, para informar sin inundar
        cupo = COLA_TOPE_ABIERTO if t["prio"] == "abierta" else None
        puestos = 0
        for h in t["hits"]:
            if h["uid"] in ya or h["tipo"] == "patente":
                continue
            if cupo is not None and puestos >= cupo:
                fuera_cupo += 1
                continue
            puestos += 1
            c["pendientes"].append({
                "uid": h["uid"], "tema": t["clave"], "tipo": h["tipo"],
                "titulo": h["titulo"], "ref": h["ref"], "url": h["url"],
                "fecha_fuente": h["fecha"], "encolado": res["fecha"],
            })
            ya.add(h["uid"])
            nuevos += 1
    guarda_cola(c)
    return nuevos, len(c["pendientes"]), fuera_cupo



# --- PRIORIZACION DE LA COLA (18-sep-2026) ----------------------------------------------------
# Al abrir el barrido a cuatro capas mas el carril de navegador, la cola paso de 11 a 103 leads en
# un dia. Una cola que no se puede vaciar deja de ser una lista de trabajo y pasa a ser un archivo
# que nadie lee -- justo el fallo que la cola venia a corregir. Asi que se PUNTUA contra su perfil
# real y lo que no llega al umbral se archiva CON MOTIVO (no se borra: `cola --todo` lo lista y
# `rescatar` lo devuelve). El criterio esta escrito aqui, es auditable y se puede discutir; lo que
# no vale es que el filtro sea "lo que me dio tiempo a mirar".
#
# Su perfil, del que sale el peso de cada senal: luminal B HR+ (ER 85 %), HER2 IHQ 0, componente
# neuroendocrino focal, metastasico oseo difuso + higado, {{LOCUS}} y 8p11 amplificados, PIK3CA y ESR1
# sin alteracion, HRD negativa, MSS, TMB 4, quimio-naive y ADC-naive, mielotoxicidad G3-G4 previa.
SUMA = [
    (7, r"HER2[- ]ultra ?low|ultra ?low HER2|超低表达|HER2[- ]low"),
    (7, r"\bFGFR4\b|\bFGF19\b|ONA-255"),
    (6, r"\bDLL3\b|\bINSM1\b|\bASCL1\b|tarlatamab|obrixtamig"),
    (6, r"\bTROP2\b|\bTROP-2\b|datopotamab|sacituzumab|Dato-DXd"),
    (5, r"\bHER3\b|patritumab|HER3-DXd|izalontamab|iza-bren"),
    (5, r"hormone receptor|HR[- ]positive|HR\+|luminal|ER[- ]positive|estrogen receptor[- ]positive|乳腺癌"),
    (5, r"antibody[- ]drug conjugate|\bADC\b|bispecific|biespecific"),
    (4, r"neuroendocrine|neuroendocrino|神经内分泌"),
    (5, r"双特异性|抗体偶联|偶联药物"),
    (4, r"HR阳性|激素受体阳性|HER2阴性"),
    (4, r"耐药|逆转耐药"),
    (4, r"no evidence of disease|complete response|eradicat|curative|oligometasta|treatment[- ]free"),
    (4, r"residual disease|ctDNA|liquid biopsy|MRD\b"),
    (4, r"\bCAR[- ]?T\b|CAR[- ]NK|cell therapy|TCR-T|neoantigen|cancer vaccine|oncolytic"),
    (3, r"radioligand|radionuclide|DOTATATE|177Lu|225Ac|theranostic"),
    (3, r"\bCCND1\b|\bFGFR1\b|cyclin D1|\bCDK2\b|CDK4/6 resistance"),
    (3, r"camizestrant|imlunestrant|elacestrant|\bSERD\b|giredestrant"),
    (3, r"first[- ]in[- ]class|first[- ]in[- ]human|novel target"),
    (2, r"\bSpain\b|España|\bES\b\]|phase (III|3|II|2)\b|fase (III|3|II|2)"),
    (2, r"resistan|resensitiz|overcome resistance|dormancy|synthetic lethal"),
]
RESTA = [
    (-6, r"triple[- ]negative|\bTNBC\b|HER2[- ]positive|HER2\+|small[- ]cell|\bSCLC\b|三阴性|HER2阳性"),
    (-5, r"pancrea|gastric|hepatocellular|\bHCC\b|colorectal|prostate|melanoma|glioblastom|"
         r"ovarian|bladder|cervical|lymphoma|leukemi|myeloma|thyroid|sarcoma|renal|esophag"),
    (-5, r"\bmurine\b|\bmice\b|\bmouse\b|4T1|xenograft|in vitro|cell line|organoid|zebrafish"),
    (-5, r"bioequivalence|生物等效|生物利用度|pharmacokinetic study in healthy|healthy (adult|volunteer)|健康成人|健康受试者"),
    (-4, r"quality of life|screening|mammograph|ultrasound|questionnaire|survey|nursing|"
         r"psycholog|exercise|rehabilitat|anesthe|analgesi|lymphedema|fatigue management"),
    (-3, r"early[- ]stage|neoadjuvant|adjuvant|node[- ]negative|DCIS|non[- ]metastatic|\bM0\b|辅助治疗|新辅助"),
    (-3, r"deep learning|artificial intelligence|radiomic|machine learning|MRI|CT imaging|"
         r"PET/CT for (diagnos|stag)"),
]
UMBRAL = 5   # por debajo de esto, el lead se archiva con motivo en vez de ocupar cola


def puntua(titulo):
    """(score, senales) de un lead contra su perfil. Determinista, sin LLM, auditable."""
    t = titulo or ""
    score, senales = 0, []
    for peso, pat in SUMA:
        if re.search(pat, t, re.I):
            score += peso
            senales.append("+%d %s" % (peso, pat.split("|")[0][:22]))
    for peso, pat in RESTA:
        if re.search(pat, t, re.I):
            score += peso
            senales.append("%d %s" % (peso, pat.split("|")[0][:22]))
    return score, senales


def cmd_priorizar(a):
    """Puntua la cola entera y archiva bajo umbral. Nada se borra: queda en 'descartados'."""
    c = lee_cola()
    pend = c.get("pendientes", [])
    if not pend:
        print("cola vacía: nada que priorizar")
        return 1
    umbral = a.umbral if a.umbral is not None else UMBRAL
    quedan, fuera = [], []
    for p in pend:
        sc, sen = puntua(p.get("titulo", ""))
        p["score"] = sc
        p["senales"] = sen[:6]
        (quedan if sc >= umbral else fuera).append(p)
    quedan.sort(key=lambda x: -x["score"])
    print(f"cola: {len(pend)} → {len(quedan)} por verificar · {len(fuera)} archivados "
          f"(umbral {umbral})")
    print("\nLO QUE SE QUEDA, de mayor a menor encaje:")
    for p in quedan[:25]:
        print(f"  [{p['score']:3}] {p['tema']:14} {p['titulo'][:88]}")
    if len(quedan) > 25:
        print(f"  … y {len(quedan) - 25} más")
    if a.dry:
        print("\n--dry: no toco la cola.")
        return 0
    for p in fuera:
        p["archivado"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        p["motivo"] = f"score {p['score']} < {umbral}: " + (", ".join(p["senales"][:3]) or "sin señal de encaje")
    c["pendientes"] = quedan
    c["descartados"] = (c.get("descartados") or []) + fuera
    guarda_cola(c)
    print(f"\narchivados {len(fuera)} (recuperables: `cola --todo`, `rescatar --ref <ref>`)")
    return 0


def cmd_rescatar(a):
    """Devuelve a la cola un lead archivado por el filtro. El filtro se equivoca; esto lo corrige."""
    if not a.ref:
        print("hace falta --ref")
        return 2
    c = lee_cola()
    desc = c.get("descartados") or []
    for i, p in enumerate(desc):
        if a.ref.lower() in (p.get("ref") or "").lower() or a.ref.lower() in (p.get("uid") or "").lower():
            p.pop("archivado", None)
            p.pop("motivo", None)
            c["descartados"] = desc[:i] + desc[i + 1:]
            c["pendientes"] = c.get("pendientes", []) + [p]
            guarda_cola(c)
            print(f"rescatado: {p['titulo'][:90]}")
            return 0
    print(f"no hay ningún archivado que case con '{a.ref}'")
    return 1


def cmd_cola(a):
    c = lee_cola()
    if getattr(a, "todo", False):
        desc = c.get("descartados") or []
        print(f"ARCHIVADOS POR EL FILTRO — {len(desc)} (se rescatan con `rescatar --ref <ref>`):")
        for p in desc[-40:]:
            print(f"  [{p.get('score', '?'):>3}] {p['titulo'][:95]}")
            print(f"        {p.get('motivo', '')[:110]}")
        return 0
    pend = c["pendientes"]
    if not pend:
        print("cola de verificación VACÍA: nada pendiente de abrir.")
        return 1
    pend.sort(key=lambda p: p.get("encolado") or "")
    print(f"COLA DE VERIFICACIÓN — {len(pend)} lead(s) sin abrir "
          f"(el más viejo, del {pend[0].get('encolado')}):")
    for p in pend:
        sc = p.get("score")
        marca = f"[{sc:3}]" if sc is not None else "[  ?]"
        print(f"  {marca} [{p['tema']:12}] {p.get('encolado')} · {p['tipo']:8} · {p['titulo']}")
        print(f"                 {p['url']}  (ref {p['ref']})")
    if len(pend) > COLA_TOPE:
        print(f"⚠️ la cola pasa de {COLA_TOPE}: se está acumulando trabajo sin verificar.")
    print("Cerrar uno: python3 tools/radar_ned_diario.py cerrar --ref <ref> "
          "--veredicto 'qué dice la fuente abierta'")
    return 0


def cmd_cerrar(a):
    """Cierra un lead SOLO con veredicto: sin haber abierto la fuente no hay nada que cerrar."""
    if not a.ref or not a.veredicto:
        print("hacen falta --ref y --veredicto (lo que dice la fuente que ABRISTE)")
        return 2
    c = lee_cola()
    quedan, cerrado = [], None
    for p in c["pendientes"]:
        if cerrado is None and (a.ref.lower() in (p["ref"] or "").lower()
                                or a.ref.lower() in (p["uid"] or "").lower()):
            cerrado = p
        else:
            quedan.append(p)
    if not cerrado:
        print(f"no hay ningún pendiente que case con '{a.ref}'")
        return 1
    cerrado["veredicto"] = _limpia(a.veredicto)[:400]
    cerrado["cerrado"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    c["pendientes"] = quedan
    c["cerrados"] = c.get("cerrados", []) + [cerrado]
    guarda_cola(c)
    print(f"cerrado: {cerrado['titulo'][:70]} → {cerrado['veredicto'][:80]}")
    print(f"quedan {len(quedan)} pendientes")
    return 0


# --- barrido -------------------------------------------------------------------------------
def barrido(dias=3, solo_tema=None):
    hasta = _hoy()
    desde = hasta - timedelta(days=max(1, dias))
    visto = carga_visto()
    nuevos_uids = set()
    temas_out, avisos = [], []
    for tema in TEMAS:
        if solo_tema and tema["clave"] != solo_tema:
            continue
        hits, errores = [], []
        for nombre, fn, kw in FUENTES:
            items, err = fn(tema, desde.isoformat(), hasta.isoformat(), **kw)
            if err:
                errores.append(f"{nombre}: {err}")
                continue
            for it in items:
                if it["uid"] in visto or it["uid"] in nuevos_uids:
                    continue
                nuevos_uids.add(it["uid"])
                it["fuente"] = nombre
                hits.append(it)
        hits.sort(key=lambda x: x.get("fecha") or "", reverse=True)
        temas_out.append({"clave": tema["clave"], "titulo": tema["titulo"], "prio": tema["prio"],
                          "cn": bool(tema.get("cn")), "hits": hits, "errores": errores})
        if hits and tema["prio"] == "alta":
            avisos.append(tema)
    return {
        "fecha": hasta.isoformat(),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ventana": {"desde": desde.isoformat(), "hasta": hasta.isoformat()},
        "temas": temas_out,
        "nuevos": len(nuevos_uids),
        "alta_con_novedad": [t["clave"] for t in avisos],
        "triado": False,
    }, nuevos_uids


def digest_md(res):
    L = [f"# Radar NED diario — {res['fecha']}", "",
         f"Barrido determinista (sin LLM, $0) · ventana {res['ventana']['desde']} → {res['ventana']['hasta']}"
         f" · **{res['nuevos']} novedades** no vistas antes.", "",
         "🔴 y 🔵 son sus dianas conocidas. ⚪ es la CAPA ABIERTA: barre sin saber qué busca "
         "(agentes nuevos sea cual sea la diana, erradicación, enfermedad residual, resistencia, "
         "mecanismos trasladables de otros tumores) mas una capa de MODALIDADES que barre por plataforma y no por diana: terapia celular, vacunas, radioligandos, tumor frio y reversion de fenotipo. La marca ⚠️ senala lo que probablemente NO es su subtipo (triple negativo, HER2-positivo, microcitico) o es preclinico: no se descarta, se avisa. Una lista de dianas nunca puede traer "
         "la que aún no tiene nombre.", "",
         "Fuentes: Europe PMC (PubMed/PMC + preprints bioRxiv/medRxiv), colección de patentes de "
         "Europe PMC, ClinicalTrials.gov v2. **Todo aquí es descubrimiento SIN VERIFICAR**: ninguna "
         "cita cuenta hasta abrirla contra la fuente primaria.", "",
         "⚠️ **No cubierto por este barrido** (necesita navegador): **CTIS** (registro de ensayos de "
         "la UE — ONA-255 solo vive ahí), congresos (ASCO/ESMO/SABCS/AACR), notas de prensa de "
         "biotech y X.", ""]
    for t in res["temas"]:
        marca = {"alta": "🔴", "media": "🔵"}.get(t["prio"], "⚪")
        if t.get("cn"):
            marca = "🇨🇳"
        L.append(f"## {marca} {t['titulo']} ({len(t['hits'])})")
        if t["errores"]:
            L.append("")
            for e in t["errores"]:
                L.append(f"- ⚠️ {e}")
        if not t["hits"]:
            L += ["", "_Sin novedades en la ventana._", ""]
            continue
        L.append("")
        for h in t["hits"]:
            aviso = (" ⚠️ " + " · ".join(h["flags"])) if h.get("flags") else ""
            L.append(f"- **{h['tipo']}** · {h['fecha'] or 's/f'} · {h['titulo']} "
                     f"([{h['ref']}]({h['url']})){aviso}")
        L.append("")
    L += ["---", "",
          "Siguiente paso humano/LLM: triar los 🔴, verificar contra fuente primaria (PMID/DOI "
          "abiertos), y mirar CTIS con navegador.", ""]
    return "\n".join(L)


def escribe_digest(res):
    """Escribe el digest del dia. Si ya hay uno de hoy (segunda pasada), NO lo machaca:
    si no hubo novedades no toca nada, y si las hubo las añade como pasada aparte. Un digest
    bueno sobrescrito por una pasada vacia seria peor que no correr."""
    os.makedirs(RADAR_DIR, exist_ok=True)
    ruta = os.path.join(RADAR_DIR, f"Radar-NED-diario-{res['fecha']}.md")
    if os.path.exists(ruta):
        if not res["nuevos"]:
            return ruta + " (sin cambios: ya existia y no hubo novedades)"
        hora = datetime.now().strftime("%H:%M")
        with open(ruta, "a") as f:
            f.write(f"\n\n---\n\n# Pasada adicional de las {hora}\n\n"
                    + digest_md(res).split("\n", 1)[1])
        return ruta + f" (añadida pasada de las {hora})"
    with open(ruta, "w") as f:
        f.write(digest_md(res))
    return ruta


def avisa(res, ruta):
    """Aviso NO urgente (respeta el silencio nocturno) solo si hay novedad en diana ALTA."""
    claves = res["alta_con_novedad"]
    if not claves:
        return "sin aviso (nada nuevo en diana de prioridad alta)"
    try:
        sys.path.insert(0, os.path.join(REPO, "tools"))
        import salida
    except Exception as e:  # noqa: BLE001
        return f"aviso no enviado: salida no importable ({e.__class__.__name__})"
    lineas = [f"Radar NED diario ({res['fecha']}): {res['nuevos']} novedades, "
              f"{len(claves)} diana(s) de prioridad alta con algo nuevo."]
    for t in res["temas"]:
        if t["clave"] in claves:
            lineas.append(f"· {t['titulo']}: {len(t['hits'])} nuevo(s). "
                          f"El primero: {t['hits'][0]['titulo']}")
    lineas.append("Sin verificar todavía: son leads, no pruebas. Digest en 04 · IA/Radar/.")
    try:
        r = salida.report_to_titular("\n".join(lineas), voz="sobria", urgente=False,
                                    fuente="radar_ned_diario")
        return f"aviso: {r}"
    except Exception as e:  # noqa: BLE001
        return f"aviso no enviado ({e.__class__.__name__})"


# --- comandos ------------------------------------------------------------------------------
def cmd_run(a):
    res, nuevos = barrido(dias=a.dias, solo_tema=a.tema)
    print(f"Radar NED diario {res['fecha']} · ventana {res['ventana']['desde']}→{res['ventana']['hasta']}")
    for t in res["temas"]:
        print(f"  [{t['prio']:5}] {t['clave']:12} {len(t['hits']):3} nuevos"
              + (f"  ⚠️ {'; '.join(t['errores'])}" if t["errores"] else ""))
    print(f"TOTAL nuevos: {res['nuevos']} · dianas ALTA con novedad: "
          f"{', '.join(res['alta_con_novedad']) or 'ninguna'}")
    if a.dry:
        print("--dry: no escribo digest, ni estado, ni aviso.")
        return 0
    ruta = escribe_digest(res)
    guarda_visto(carga_visto() | nuevos)
    guarda_ultimo(res)
    n_cola, total_cola, fuera = encola(res)
    print(f"digest → {ruta}")
    print(f"cola de verificación: +{n_cola} · {total_cola} pendientes de abrir "
          f"(salen en la brújula de cada sesión)")
    if fuera:
        print(f"⚠️ {fuera} lead(s) de la capa abierta quedaron fuera del cupo diario: NO están en "
              f"la cola, pero sí en el digest — míralos ahí antes de darlos por vistos.")
    if not a.sin_aviso:
        print(avisa(res, ruta))
    return 0


def cmd_check(a):
    """rc 0 = hay novedad ALTA del último barrido sin triar (el LLM merece arrancar)."""
    u = lee_ultimo()
    if not u:
        print("sin barrido previo → novedad (fail-safe)")
        return 0
    if u.get("triado"):
        print("último barrido ya triado → nada")
        return 1
    if u.get("alta_con_novedad"):
        print(f"novedad ALTA sin triar: {', '.join(u['alta_con_novedad'])}")
        return 0
    print("sin novedad en diana de prioridad alta")
    return 1


def cmd_status(a):
    u = lee_ultimo()
    c = lee_cola()
    print(f"visto: {len(carga_visto())} uids · cola: {len(c['pendientes'])} pendientes / "
          f"{len(c.get('cerrados', []))} cerrados · estado: {DIR_ESTADO}")
    if not u:
        print("sin barrido previo")
        return 0
    print(f"último: {u.get('fecha')} ({u.get('ts')}) · nuevos {u.get('nuevos')} · "
          f"ALTA {', '.join(u.get('alta_con_novedad') or []) or 'ninguna'} · "
          f"triado={u.get('triado')}")
    for t in u.get("temas", []):
        print(f"  {t['clave']:12} {len(t.get('hits') or []):3}")
    return 0


def cmd_sellar(a):
    """Marca el último barrido como triado (lo llama el agente cuando ha hecho el triaje)."""
    u = lee_ultimo()
    if not u:
        print("sin barrido previo")
        return 1
    u["triado"] = True
    u["triado_ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    guarda_ultimo(u)
    print(f"sellado: barrido {u.get('fecha')} marcado como triado")
    return 0


def main():
    p = argparse.ArgumentParser(description="Radar NED diario (determinista, $0)")
    p.add_argument("cmd", choices=["run", "check", "status", "sellar", "cola", "cerrar",
                                   "priorizar", "rescatar"])
    p.add_argument("--dias", type=int, default=3, help="ventana de frescura (def. 3)")
    p.add_argument("--tema", choices=CLAVES, help="barrer solo un tema")
    p.add_argument("--dry", action="store_true", help="no escribe nada")
    p.add_argument("--sin-aviso", dest="sin_aviso", action="store_true")
    p.add_argument("--ref", help="ref/uid del lead a cerrar (cerrar)")
    p.add_argument("--veredicto", help="qué dice la fuente que abriste (cerrar)")
    p.add_argument("--umbral", type=int, help="corte de score en priorizar (def. %d)" % UMBRAL)
    p.add_argument("--todo", action="store_true", help="cola: lista los archivados por el filtro")
    a = p.parse_args()
    return {"run": cmd_run, "check": cmd_check, "status": cmd_status, "sellar": cmd_sellar,
            "cola": cmd_cola, "cerrar": cmd_cerrar, "priorizar": cmd_priorizar,
            "rescatar": cmd_rescatar}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
