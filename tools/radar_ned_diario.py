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
  4. (desde radar_ned_dia.sh, por el VPS) ICTRP, espejo OMS de ChiCTR, ingerido via
     tools/radar_navegador.py --fuente ictrp. ChinaXiv queda FUERA a propósito (20-sep-2026).

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
import hashlib
import json
import os
import re
import sys
import urllib.error
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
                 'OR "CAR-NK" OR "TIL" OR "cell therapy")',
    },
    {
        # TIL (20-sep-2026, encargo de {{TITULAR}}: "terapia personalizada es lo que mas me interesa").
        # Hasta hoy los linfocitos infiltrantes solo aparecian como UNA palabra dentro de `celular`,
        # ahogada por CAR-T. Lifileucel (primera TIL aprobada, melanoma, 2024) y la expansion ex vivo
        # en tumor solido son una familia propia de terapia personalizada: la celula sale del tumor
        # de la propia paciente. Ojo al ruido: "TILs" como BIOMARCADOR (pronostico, % de infiltrado)
        # es literatura enorme en mama y no es terapia; por eso la consulta exige contexto de
        # terapia (adoptive / cell therapy / expansion / infusion), no la sigla sola.
        "clave": "til",
        "titulo": "TIL: linfocitos infiltrantes de tumor como terapia (lifileucel, expansion ex vivo, ACT)",
        "prio": "abierta",
        "epmc": '(TITLE:"lifileucel" OR ABSTRACT:"lifileucel" OR ABSTRACT:"TIL therapy" '
                'OR ABSTRACT:"TIL-based" OR ABSTRACT:"adoptive cell transfer" '
                'OR ABSTRACT:"adoptive cell therapy" OR ABSTRACT:"adoptive T cell therapy" '
                'OR ((ABSTRACT:"tumor-infiltrating lymphocyte" OR ABSTRACT:"tumour-infiltrating lymphocyte") '
                'AND (ABSTRACT:"adoptive" OR ABSTRACT:"ex vivo expansion" OR ABSTRACT:"infusion" '
                'OR ABSTRACT:"expanded"))) '
                'AND (ABSTRACT:"solid tumor" OR ABSTRACT:"solid tumour" OR ABSTRACT:"breast" '
                'OR ABSTRACT:"metastatic")',
        "ctgov": 'AREA[ConditionSearch]("breast" OR "solid tumor") AND AREA[InterventionName]('
                 '"tumor infiltrating lymphocytes" OR "tumor-infiltrating lymphocytes" OR "TIL" '
                 'OR "lifileucel" OR "adoptive cell")',
    },
    {
        # Biespecificos y T-cell engagers como PLATAFORMA (20-sep-2026). Antes solo se buscaban
        # pegados a una diana ({{DIANA2}} en `trasladable`, ADC+bispecific en `cn-adc`); un engager de
        # celulas T nuevo en tumor solido sin esas dianas no entraba.
        "clave": "biespecifico",
        "titulo": "Biespecificos y T-cell engagers en mama / tumor solido (cualquier diana)",
        "prio": "abierta",
        "epmc": '(TITLE:"bispecific" OR ABSTRACT:"bispecific antibody" OR ABSTRACT:"T-cell engager" '
                'OR ABSTRACT:"T cell engager" OR ABSTRACT:"bispecific T-cell engager") '
                'AND (ABSTRACT:"breast" OR ABSTRACT:"solid tumor" OR ABSTRACT:"solid tumour")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND AREA[InterventionName]("bispecific" '
                 'OR "T-cell engager" OR "T cell engager")',
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


# --- CAPA CHINA (18-sep-2026, rehecha el 20-sep-2026) ------------------------------------------
# Peticion suya: "busca en China tambien, que para eso tenemos hueco en servidor alli". China
# produce hoy buena parte de los ADC, biespecificos y terapias celulares nuevas, y muchas de esas
# moleculas llegan a Europa anios despues -- o nunca. Tres vias:
#   1. ESTA capa: Europe PMC filtrando por AFILIACION china. Funciona desde Espana, $0, sin
#      proxy, y captura la investigacion china publicada en ingles (Hengrui, BeiGene, Kelun...).
#      Medido el 20-sep: 860 registros de 2026 de Sun Yat-sen/Fudan/Peking Union NO llevan la
#      palabra "China" en la afiliacion, por eso AFF_CN suma regiones y ciudades. Y los preprints
#      (SRC:PPR) no llevan afiliacion indexada (AFF:"China" = 0 hits), por eso cn-preprints va
#      SIN filtro de afiliacion. Las revistas en chino (LANG:chi) llevan titulo traducido pero
#      muchas no traen abstract en ingles: cn-revista-chi busca por TITLE, no por ABSTRACT.
#   2. ICTRP (trialsearch.who.int), espejo OMS de ChiCTR, por el VPS de Hong Kong con Playwright
#      (tools/radar_cn_vps.py ictrp perfil): ChiCTR bloquea las IPs de datacenter (405), ICTRP
#      no, e importo su fichero el 14-sep-2026. Se ingiere en radar_ned_dia.sh via
#      tools/radar_navegador.py --fuente ictrp.
#   3. CDE de la NMPA: sigue en el carril de navegador local (no abre desde el VPS), con los
#      terminos por PERFIL de radar_cn_vps.TERMINOS_CDE, no con 乳腺癌 a secas.
#   FUERA a proposito (20-sep-2026): ChinaXiv. 46.276 items de todas las disciplinas, ~2.128 en
#   2026 dominados por fisica y psicologia, su buscador ignora el parametro GET y Europe PMC solo
#   indexa 13 preprints suyos. Rendimiento oncologico ~0: los preprints chinos que importan van a
#   medRxiv/bioRxiv/Research Square, que Europe PMC si indexa (cn-preprints).
AFF_CN = ('(AFF:"China" OR AFF:"Hong Kong" OR AFF:"Macau" OR AFF:"Taiwan" OR AFF:"Shanghai" '
          'OR AFF:"Beijing" OR AFF:"Guangzhou" OR AFF:"Hangzhou" OR AFF:"Shenzhen" OR AFF:"Nanjing" '
          'OR AFF:"Wuhan" OR AFF:"Chengdu" OR AFF:"Tianjin")')
TEMAS_CN = [
    {
        "clave": "cn-adc",
        "titulo": "China: ADC y biespecificos (Hengrui, Kelun, SystImmune, BeiGene...)",
        "prio": "abierta",
        "cn": True,
        "epmc": AFF_CN + ' AND (ABSTRACT:"antibody-drug conjugate" OR ABSTRACT:"bispecific antibody" '
                'OR TITLE:"antibody-drug conjugate") AND (ABSTRACT:"breast" OR ABSTRACT:"solid tumor")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND AREA[LocationCountry]("China") '
                 'AND AREA[InterventionName]("antibody-drug conjugate" OR "ADC")',
    },
    {
        "clave": "cn-celular",
        "titulo": "China: terapia celular en tumores solidos (CAR-T, CAR-NK, TIL, TCR-T)",
        "prio": "abierta",
        "cn": True,
        "epmc": AFF_CN + ' AND (ABSTRACT:"CAR-T" OR ABSTRACT:"CAR T cell" OR ABSTRACT:"CAR-NK" '
                'OR ABSTRACT:"cell therapy" OR ABSTRACT:"TCR-T" OR ABSTRACT:"TIL therapy" '
                'OR ABSTRACT:"lifileucel" OR ABSTRACT:"adoptive cell therapy" '
                'OR ABSTRACT:"adoptive cell transfer") '
                'AND (ABSTRACT:"solid tumor" OR ABSTRACT:"solid tumour" OR ABSTRACT:"breast")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND AREA[LocationCountry]("China") '
                 'AND AREA[InterventionName]("CAR-T" OR "CAR T" OR "cell therapy" '
                 'OR "tumor infiltrating lymphocytes" OR "TIL" OR "adoptive cell")',
    },
    {
        "clave": "cn-inmuno",
        "titulo": "China: vacunas, inmunoterapia y radioligandos",
        "prio": "abierta",
        "cn": True,
        "epmc": AFF_CN + ' AND (ABSTRACT:"cancer vaccine" OR ABSTRACT:"neoantigen" '
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
        "epmc": AFF_CN + ' AND (ABSTRACT:"FGFR4" OR ABSTRACT:"FGF19" OR ABSTRACT:"{{DIANA2}}" '
                'OR ABSTRACT:"TROP2" OR ABSTRACT:"HER3" OR ABSTRACT:"neuroendocrine") '
                'AND (ABSTRACT:"cancer" OR ABSTRACT:"tumor" OR ABSTRACT:"carcinoma")',
        "ctgov": 'AREA[LocationCountry]("China") AND AREA[InterventionName]("FGFR4" OR "{{DIANA2}}" '
                 'OR "TROP2" OR "HER3")',
    },
    {
        # SIN filtro de afiliacion a proposito: los preprints no la llevan indexada (medido el
        # 20-sep-2026: SRC:PPR AND ... AND AFF:"China" = 0; sin AFF, 194). No es "China" en
        # sentido estricto: es la capa de preprints de vacuna/neoantigeno que antes se perdia.
        "clave": "cn-preprints",
        "titulo": "Preprints de vacunas y neoantigenos (medRxiv/bioRxiv/Research Square, sin filtro de pais)",
        "prio": "abierta",
        "cn": True,
        "epmc": 'SRC:PPR AND (neoantigen OR "personalized vaccine" OR "individualized vaccine" '
                'OR "mRNA vaccine") AND ("solid tumor" OR breast)',
        "ctgov": "",
    },
    {
        # Su perfil molecular en genes PUBLICOS (nombre de gen, nunca banda ni variante: el borde
        # y test_radar_ned_diario lo vetan). 90 hits 2025-26 medidos el 20-sep.
        "clave": "cn-perfil",
        "titulo": "China: su perfil (FGFR1/CCND1 amplificados, FGF19/FGFR4, CDK2) en mama HR+",
        "prio": "abierta",
        "cn": True,
        "epmc": AFF_CN + ' AND (ABSTRACT:"FGFR1" OR ABSTRACT:"CCND1" OR ABSTRACT:"FGF19" '
                'OR ABSTRACT:"FGFR4" OR ABSTRACT:"CDK2") '
                'AND (ABSTRACT:"breast" OR ABSTRACT:"hormone receptor")',
        "ctgov": 'AREA[ConditionSearch]("breast") AND AREA[LocationCountry]("China") '
                 'AND AREA[InterventionName]("CDK2" OR "FGFR" OR "FGFR4" OR "FGFR1")',
    },
    {
        # Revistas en chino (Zhonghua Zhong Liu Za Zhi, Sichuan Da Xue Xue Bao...): Europe PMC
        # indexa titulo traducido, pero muchas SIN abstract en ingles, asi que ABSTRACT:"breast"
        # devuelve 0 (medido el 20-sep). Se busca por TITLE. Etiqueta [revista-CN] en el digest.
        "clave": "cn-revista-chi",
        "titulo": "China: revistas en chino (titulo traducido; sin abstract en ingles)",
        "prio": "abierta",
        "cn": True,
        "epmc": 'LANG:chi AND (TITLE:"breast" OR TITLE:"mammary") AND (neoantigen OR vaccine '
                'OR "antibody-drug" OR FGFR OR CDK2 OR TROP2 OR {{DIANA2}} OR neuroendocrine '
                'OR "hormone receptor")',
        "ctgov": "",
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


# Un DOI NO es texto de markdown. `_limpia` quita `_` porque en un TÍTULO es cursiva, y al
# pasarle un DOI se lo comía: `10.1007/82_2026_356` entraba a la cola como `10.1007/822026356`,
# que doi.org devuelve 404. El lead quedaba IRRESOLUBLE — quien lo triara no podía abrir la
# fuente, así que o lo cerraba a ciegas (mentir) o lo dejaba pudrirse. Pasó con el paper de
# mediadores pro-resolutivos, cazado el 20-sep-2026 al vaciar la cola. Afecta a toda la familia
# `10.1007/82_AAAA_NNN` de Springer y a cualquier DOI con guion bajo.
_RE_DOI = re.compile(r"^10\.\d{4,9}/[-._;()/:+A-Za-z0-9]+$")


def _limpia_largo(texto):
    """Como `_limpia` pero sin el recorte a MAX_TITULO: para abstracts y criterios.

    `_limpia` existe para TITULOS y corta a 160 caracteres. Reusarla aqui habria guardado el
    primer renglon de los criterios de elegibilidad y nada mas, que es peor que no guardarlos:
    parecerian leidos. El recorte de verdad lo pone cada llamante, que sabe cuanto necesita.
    """
    if not isinstance(texto, str):
        return ""
    t = re.sub(r"[\r\n\t]+", " ", texto)
    t = re.sub(r"[`*_\[\]<>|]", "", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def _limpia_doi(texto):
    """Sanea un DOI EXTERNO conservando su sintaxis, o devuelve "" si no lo es.

    Sigue siendo dato no confiable: se recorta, se le quitan espacios y caracteres de control, y
    se EXIGE la forma canónica antes de dejarlo entrar. Lo que no casa no se arregla a medias:
    se descarta y el lead cae al identificador de respaldo (PMID), que sí resuelve.
    """
    if not isinstance(texto, str):
        return ""
    # Se recortan los EXTREMOS y nada más. Colapsar los espacios internos, que era lo primero
    # que hice, convertía `10.1007/82_2026_356 ; rm -rf /` en un DOI que pasaba el patrón:
    # `;`, `-` y `/` son caracteres legales en un DOI. Un espacio dentro significa que eso NO es
    # un DOI, y lo que no es un DOI no se arregla, se tira.
    t = texto.strip()[:120]
    return t if _RE_DOI.match(t) else ""


# Reintentos (21-sep-2026): el 20-sep hubo 9 avisos de «error de red» y el 21-sep 48, incluidos
# temas ALTA (ONA-255, ne-mama, adc-hr), y a mediodia la misma API respondia 6/6 en 0,3 s. Eran
# fallos pasajeros de primera hora, y sin reintento un tema se quedaba sin nada ese dia. Solo se
# reintenta lo que puede curarse esperando (timeout, conexion, 429, 5xx); un 4xx no.
REINTENTOS = (2, 6)   # segundos de espera antes del 2.º y 3.º intento


def _reintentable(e):
    if isinstance(e, urllib.error.HTTPError):
        return e.code == 429 or e.code >= 500
    return isinstance(e, (urllib.error.URLError, TimeoutError, ConnectionError, OSError))


def _get(url, params, _dormir=None):
    import time
    dormir = _dormir or time.sleep
    q = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    req = urllib.request.Request(url + "?" + q, headers={"User-Agent": UA, "Accept": "application/json"})
    for i, espera in enumerate((0,) + REINTENTOS):
        if espera:
            dormir(espera)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            if i == len(REINTENTOS) or not _reintentable(e):
                if i:
                    print(f"  ↻ {urllib.parse.urlparse(url).netloc}: {i + 1} intentos, sin éxito "
                          f"({_motivo_red(e)})")
                raise
            print(f"  ↻ reintento {i + 1}/{len(REINTENTOS)} en {urllib.parse.urlparse(url).netloc} "
                  f"({_motivo_red(e)})")


def _motivo_red(e):
    """«error de red (HTTPError 503)»: con el codigo, que sin el no se distingue un 429 de un 500."""
    code = getattr(e, "code", None)
    return f"error de red ({e.__class__.__name__}{' ' + str(code) if code else ''})"


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


# --- MODALIDAD de terapia personalizada (20-sep-2026) ---------------------------------------
# Regla nueva de la estrella polar: el horizonte es la FAMILIA ENTERA de terapia personalizada,
# no solo la vacuna. Cada lead sale etiquetado con su familia y el digest cierra con el recuento
# de los ultimos 30 dias, para ver de un vistazo si una familia lleva semanas sin aparecer: esa
# es la senal de que la hemos dejado de mirar (consulta mal escrita, fuente cambiada), no de que
# no haya nada. Se clasifica por TITULO (lo unico que trae el barrido; resultType=lite no da
# abstract), en orden: el primer patron que casa manda. Lo que no casa es [otro]: mejor "otro"
# que una etiqueta que el titulo no sostiene. TIL exige contexto de TERAPIA: "TILs" como
# biomarcador pronostico es otra cosa y se queda en [otro].
_TIL_PALABRA = r"tumou?r[- ]infiltrating lymphocyte|\bTILs?\b"
_TIL_TERAPIA = (r"\bTILs? (therapy|infusion|injection|product|treatment|transfer)"
                r"|therapy (using|with|based on) (autologous )?tumou?r[- ]infiltrating"
                r"|\b(adoptive|ex vivo|expanded|expansion|infusion|injection|autologous|reinfus\w*)\b|\bACT\b")
MODALIDADES = [
    ("CAR-T", re.compile(r"\bCAR[- ]?T\b|chimeric antigen receptor[- ]?T\b|CAR T[- ]cell|CAR-modified T", re.I)),
    ("CAR-NK", re.compile(r"\bCAR[- ]?NK\b|chimeric antigen receptor.{0,25}natural killer", re.I)),
    ("TCR-T", re.compile(r"\bTCR[- ]?T\b|T[- ]cell receptor[- ](engineered|therapy|transduced|gene)|TCR-engineered|afami-?cel|afamitresgene", re.I)),
    ("TIL", re.compile(r"lifileucel|adoptive (cell|T[- ]cell|lymphocyte) (therapy|transfer)|\bTIL therapy|TIL-based"
                       r"|tumou?r[- ]infiltrating lymphocytes? (therap|treatment|infusion|product|immunotherap)", re.I)),
    ("biespecífico", re.compile(r"bispecific|bi-specific|T[- ]cell engager|\bBiTE\b|\bTCEs?\b|tarlatamab|obrixtamig", re.I)),
    ("virus-oncolítico", re.compile(r"oncolytic|talimogene|\bT-VEC\b", re.I)),
    ("vacuna", re.compile(r"vaccin|neoantigen|neo-?epitope|dendritic cell|\bDC[- ]based", re.I)),
]
FAMILIA = [m for m, _ in MODALIDADES]   # las que se vigilan; [otro] no cuenta como familia
HISTORIAL = os.path.join(DIR_ESTADO, "historial_modalidad.json")
DIAS_COBERTURA = 30


def modalidad(titulo):
    """Etiqueta de familia de un lead a partir de su titulo. Determinista, auditable."""
    t = titulo or ""
    for etiqueta, rx in MODALIDADES:
        if rx.search(t):
            return etiqueta
    if re.search(_TIL_PALABRA, t, re.I) and re.search(_TIL_TERAPIA, t, re.I):
        return "TIL"
    return "otro"


def recuento_dia(res):
    """{modalidad: n} de un barrido (todas las capas, todos los hits)."""
    c = {m: 0 for m in FAMILIA + ["otro"]}
    for t in res.get("temas", []):
        for h in t.get("hits", []):
            m = h.get("modalidad") or modalidad(h.get("titulo"))
            c[m] = c.get(m, 0) + 1
    return c


def carga_historial():
    try:
        with open(HISTORIAL) as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("dias"), dict):
            return d
    except (OSError, json.JSONDecodeError):
        pass
    return {"dias": {}}


def guarda_historial(h):
    os.makedirs(DIR_ESTADO, exist_ok=True)
    # tope: 120 dias; el recuento solo mira 30
    dias = dict(sorted(h.get("dias", {}).items())[-120:])
    tmp = HISTORIAL + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"dias": dias}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, HISTORIAL)


_RE_LINEA_DIGEST = re.compile(r"^- \*\*(\w+)\*\* · (\S+) · (.*?) \(\[", re.M)
_RE_FECHA_DIGEST = re.compile(r"Radar-NED-diario-(\d{4}-\d{2}-\d{2})\.md$")


def reconstruye_historial(radar_dir=None):
    """Sin historial previo, lo reconstruye de los digests ya escritos (formato propio, por
    fecha del fichero). Asi el recuento de 30 dias no arranca de cero el dia que se estrena."""
    radar_dir = radar_dir or RADAR_DIR
    dias = {}
    try:
        nombres = sorted(os.listdir(radar_dir))
    except OSError:
        return {"dias": {}}
    for n in nombres:
        m = _RE_FECHA_DIGEST.search(n)
        if not m:
            continue
        try:
            with open(os.path.join(radar_dir, n), encoding="utf-8") as f:
                cuerpo = f.read()
        except OSError:
            continue
        c = {k: 0 for k in FAMILIA + ["otro"]}
        for _tipo, _fecha, titulo in _RE_LINEA_DIGEST.findall(cuerpo):
            c[modalidad(titulo)] += 1
        dias[m.group(1)] = c
    return {"dias": dias}


def registra_historial(res, persistir=True):
    """Suma el recuento de este barrido al dia (una segunda pasada SUMA, no sustituye).
    Devuelve el historial resultante. Con persistir=False no toca disco (--dry)."""
    h = carga_historial()
    if not h["dias"]:
        h = reconstruye_historial()
    dia = h["dias"].setdefault(res["fecha"], {k: 0 for k in FAMILIA + ["otro"]})
    for k, n in recuento_dia(res).items():
        dia[k] = dia.get(k, 0) + n
    if persistir:
        guarda_historial(h)
    return h


def recuento_30(h, hoy=None, dias=DIAS_COBERTURA):
    """{modalidad: n} sumando los ultimos `dias` dias del historial (hoy incluido)."""
    hoy = hoy or _hoy()
    desde = (hoy - timedelta(days=dias - 1)).isoformat()
    c = {k: 0 for k in FAMILIA + ["otro"]}
    for fecha, d in h.get("dias", {}).items():
        if desde <= fecha <= hoy.isoformat():
            for k, n in d.items():
                c[k] = c.get(k, 0) + n
    return c


def cobertura(h, hoy=None, dias=DIAS_COBERTURA):
    """Lineas de aviso: una por modalidad de la FAMILIA sin un solo lead en `dias` dias.
    Distingue tres cosas que hoy se confundirian: (a) el historial es mas corto que la
    ventana (no se puede afirmar ">30 dias"), (b) el radar corrio pocas veces en la ventana
    (no es que no haya nada: es que no se miro), (c) corrio y no trajo nada (consulta mal
    escrita, fuente cambiada, o de verdad no hay nada: eso lo decide quien lee)."""
    hoy = hoy or _hoy()
    fechas = sorted(h.get("dias", {}))
    if not fechas:
        return [f"⚠️ cobertura: sin historial de modalidades todavia (primer barrido); el recuento "
                f"de {dias} dias arranca hoy."]
    primera = datetime.strptime(fechas[0], "%Y-%m-%d").date()
    cubiertos = (hoy - primera).days + 1
    desde = (hoy - timedelta(days=dias - 1)).isoformat()
    corridas = [f for f in fechas if desde <= f <= hoy.isoformat()]
    out = []
    for m in FAMILIA:
        ultimo = max((f for f in fechas if h["dias"][f].get(m, 0) > 0), default=None)
        if ultimo and ultimo >= desde:
            continue
        if cubiertos < dias:
            out.append(f"⚠️ [{m}] sin ningun lead en los {cubiertos} dias de historial que hay "
                       f"(menos de {dias}: aun no se puede afirmar mas). Si sigue asi al llegar a "
                       f"{dias}, revisar la consulta.")
            continue
        sin = (hoy - datetime.strptime(ultimo, "%Y-%m-%d").date()).days if ultimo else cubiertos
        out.append(f"⚠️ [{m}] lleva {sin} dias sin un solo lead (mas de {dias}); el radar corrio "
                   f"{len(corridas)} dia(s) en la ventana. Puede ser consulta mal escrita, fuente "
                   f"que cambio, o que de verdad no hay nada: comprobar, no suponer.")
    return out


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
              "sort": "P_PDATE_D desc", "resultType": "core"}
    params.update(params_fecha)
    try:
        d = _get(EPMC, params)
    except Exception as e:  # noqa: BLE001
        return [], _motivo_red(e)
    out = []
    for r in (d.get("resultList", {}) or {}).get("result", []) or []:
        src, rid = _limpia(r.get("source"))[:8], _limpia(r.get("id"))[:24]
        if not rid:
            continue
        doi = _limpia_doi(r.get("doi"))
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
            "revista": _limpia(r.get("journalTitle"))[:80],
            # `core` (antes `lite`) trae esto. Sin MeSH no hay forma de distinguir el
            # «neuroendocrino» del ESTRES del «neuroendocrino» del TUMOR, ni un raton de un
            # paciente. Igual que arriba: se guarda, todavia no filtra.
            "mesh": [_limpia(m.get("descriptorName"))[:60]
                     for m in ((r.get("meshHeadingList") or {}).get("meshHeading") or [])
                     if isinstance(m, dict)][:40] or None,
            "pubtypes": [_limpia(t)[:40] for t in
                         ((r.get("pubTypeList") or {}).get("pubType") or [])][:12] or None,
            "abstract": _limpia_largo(r.get("abstractText"))[:4000] or None,
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
        # los 5 ultimos (20-sep-2026) alimentan las etiquetas de calidad: pais(es), n, disenio,
        # clase de promotor y si hay resultados publicados. Verificados uno a uno contra la API v2.
        # Los 12 siguientes (20-sep-2026) son los que permiten JUZGAR el lead en vez de
        # adivinarlo por el titulo. Nacen del triaje de los 20 leads de ese dia: una
        # psicoterapia para cuidadores entro por el carril `ne-mama` porque medía vias
        # «neuroendocrinas» del ESTRES, y se cae sola mirando primaryPurpose
        # (SUPPORTIVE_CARE), interventionType (BEHAVIORAL) y healthyVolunteers (true).
        # De momento SOLO se traen y se guardan: no filtran nada todavia.
        "fields": "NCTId,BriefTitle,OverallStatus,LastUpdatePostDate,Phase,"
                  "LocationCountry,EnrollmentCount,DesignAllocation,LeadSponsorClass,HasResults,"
                  "StudyType,DesignPrimaryPurpose,InterventionType,HealthyVolunteers,Sex,"
                  "Condition,ConditionMeshTerm,Keyword,EligibilityCriteria,WhyStopped",
        "format": "json",
    }
    try:
        d = _get(CTGOV, params)
    except Exception as e:  # noqa: BLE001
        return [], _motivo_red(e)
    out = []
    for s in d.get("studies", []) or []:
        p = (s.get("protocolSection") or {})
        ident, st = p.get("identificationModule") or {}, p.get("statusModule") or {}
        nct = _limpia(ident.get("nctId"))[:16]
        if not nct:
            continue
        estado = _limpia(st.get("overallStatus"))[:32]
        fecha = _limpia(((st.get("lastUpdatePostDateStruct") or {}).get("date")))
        dis = p.get("designModule") or {}
        paises = sorted({_limpia(loc.get("country"))[:40]
                         for loc in ((p.get("contactsLocationsModule") or {}).get("locations") or [])
                         if isinstance(loc, dict) and loc.get("country")})
        n = (dis.get("enrollmentInfo") or {}).get("count")
        elig = p.get("eligibilityModule") or {}
        out.append({
            # el uid lleva el estado: si un ensayo cambia de estado, vuelve a ser novedad
            "uid": f"ctgov:{nct}:{estado}",
            "tipo": "ensayo",
            "titulo": f"[{estado}] {_limpia(ident.get('briefTitle'))}",
            "fecha": fecha,
            "ref": nct,
            "url": f"https://clinicaltrials.gov/study/{nct}",
            "flags": _flags(_limpia(ident.get("briefTitle"))),
            "paises": paises[:30],
            "n": n if isinstance(n, int) else None,
            "alloc": _limpia((dis.get("designInfo") or {}).get("allocation"))[:20],
            "sponsor": _limpia(((p.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {})
                               .get("class"))[:20],
            "resultados": bool(s.get("hasResults")),
            # Crudos para el juicio posterior. `None` significa «la ficha no lo dice», que NO es
            # lo mismo que «no cumple»: quien juzgue tiene que poder distinguirlo.
            "study_type": _limpia(dis.get("studyType"))[:20] or None,
            "proposito": _limpia((dis.get("designInfo") or {}).get("primaryPurpose"))[:32] or None,
            "tipos_interv": sorted({_limpia(i.get("type"))[:24]
                                    for i in ((p.get("armsInterventionsModule") or {})
                                              .get("interventions") or [])
                                    if isinstance(i, dict) and i.get("type")}) or None,
            "sanos": (elig.get("healthyVolunteers")
                      if isinstance(elig.get("healthyVolunteers"), bool) else None),
            "sexo": _limpia(elig.get("sex"))[:10] or None,
            "condiciones": [_limpia(c)[:60] for c in
                            ((p.get("conditionsModule") or {}).get("conditions") or [])][:12] or None,
            # El texto de elegibilidad es largo (hasta 30 KB) y la cola se guarda en JSON: se
            # recorta. 6000 caracteres cubren inclusion y exclusion en los que he mirado.
            "elegibilidad": (_limpia_largo(elig.get("eligibilityCriteria"))[:6000] or None),
            "parado_porque": _limpia(st.get("whyStopped"))[:200] or None,
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


# --- ETIQUETAS DE CALIDAD (20-sep-2026) --------------------------------------------------------
# Para no inflar evidencia floja al abrir la capa china: cada lead lleva, desde el barrido, una
# etiqueta que dice QUE ES antes de que nadie lo lea como "hay un ensayo en China". Son texto
# derivado de campos de la fuente (tipo, registro, paises, n, disenio, promotor, revista), no
# juicio: donde el dato no alcanza, no se etiqueta (mejor sin etiqueta que con una falsa).
ETIQUETAS = {
    "registro-sin-resultados": "[registro-CN · sin resultados]",
    # ICTRP con pais=China tambien devuelve NCT/EUCTR con CENTROS en China: no es registro chino
    "registro-centros-cn": "[registro · centros en China · sin resultados]",
    "rct-cn": "[RCT · población-CN única · validez externa limitada]",
    "iit-cn": "[IIT-CN · 1 brazo · n<30]",
    "poblacion-cn": "[población-CN única]",
    "preprint": "[preprint · sin peer review]",
    "revista-cn": "[revista-CN]",
    "pr": "[PR · no evidencia]",
}
_REVISTA_CN = re.compile(r"Zhonghua|Za Zhi|Xue Bao|Zhongguo|Chinese Journal|Chin J\b|Chinese Medical",
                         re.I)
_REGISTROS_CN = ("ictrp", "chictr", "cde")


def etiquetas_calidad(hit, tema=None):
    """Lista de etiquetas (texto de ETIQUETAS) para un hit, a partir de sus campos."""
    et = []
    uid = hit.get("uid") or ""
    fuente_reg = uid.split(":", 1)[0] if ":" in uid else ""
    if hit.get("tipo") == "preprint":
        et.append(ETIQUETAS["preprint"])
    if fuente_reg in _REGISTROS_CN and not hit.get("resultados"):
        ref = hit.get("ref") or ""
        es_registro_cn = ref.startswith("ChiCTR") or re.match(r"^CTR\d{8}$", ref) is not None
        et.append(ETIQUETAS["registro-sin-resultados" if es_registro_cn else "registro-centros-cn"])
    if fuente_reg == "ctgov":
        paises = hit.get("paises") or []
        solo_cn = bool(paises) and set(paises) <= {"China", "Hong Kong", "Macao", "Macau", "Taiwan"}
        n = hit.get("n")
        alloc = (hit.get("alloc") or "").upper()
        if solo_cn and alloc == "RANDOMIZED":
            et.append(ETIQUETAS["rct-cn"])
        elif (solo_cn and alloc in ("NA", "NON_RANDOMIZED") and (hit.get("sponsor") or "").upper() == "OTHER"
              and isinstance(n, int) and n < 30):
            et.append(ETIQUETAS["iit-cn"])
        elif solo_cn:
            et.append(ETIQUETAS["poblacion-cn"])
    if (tema or {}).get("clave") == "cn-revista-chi" or _REVISTA_CN.search(hit.get("revista") or ""):
        et.append(ETIQUETAS["revista-cn"])
    if hit.get("tipo") in ("pr", "company", "idea"):
        et.append(ETIQUETAS["pr"])
    return et


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


# Los cerrados que pasan de CERRADOS_TOPE no se tiran: van a un jsonl append-only. Antes se
# recortaban en silencio a 200 y con ellos el rastro de POR QUE se descarto cada ensayo (deuda
# `radar-cola-recorta-cerrados-a-200`, 21-sep-2026). Ademas `encola` dejaba de reconocerlos como
# vistos y podia volver a meter en cola un lead ya juzgado.
CERRADOS_TOPE = 200
ARCHIVO_CERRADOS = os.path.join(DIR_ESTADO, "cerrados_archivo.jsonl")


def uids_archivados():
    """uids de los cerrados que ya salieron de la cola al archivo. Fail-soft: ilegible → vacío."""
    uids = set()
    try:
        with open(ARCHIVO_CERRADOS, encoding="utf-8") as f:
            for linea in f:
                try:
                    uids.add(json.loads(linea).get("uid"))
                except (json.JSONDecodeError, AttributeError):
                    continue
    except OSError:
        pass
    uids.discard(None)
    return uids


def guarda_cola(c):
    os.makedirs(DIR_ESTADO, exist_ok=True)
    cerrados = c.get("cerrados", [])
    if len(cerrados) > CERRADOS_TOPE:
        sobran = cerrados[:-CERRADOS_TOPE]
        with open(ARCHIVO_CERRADOS, "a", encoding="utf-8") as f:
            for x in sobran:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        cerrados = cerrados[-CERRADOS_TOPE:]
    c["cerrados"] = cerrados
    tmp = COLA + ".tmp"
    with open(tmp, "w") as f:
        json.dump(c, f, ensure_ascii=False, indent=1)
    os.replace(tmp, COLA)


def encola(res):
    """Mete en la cola los leads de diana ALTA que aun no estan ni pendientes ni cerrados.
    Las patentes NO entran (el indice no da fecha fiable; son contexto, no lead que verificar)."""
    c = lee_cola()
    ya = ({p["uid"] for p in c["pendientes"]} | {p["uid"] for p in c.get("cerrados", [])}
          | uids_archivados())
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
                "cn": bool(t.get("cn")), "calidad": list(h.get("calidad") or []),
            })
            ya.add(h["uid"])
            nuevos += 1
    if nuevos:
        n_jev, motivo = orden_jev(c["pendientes"])
        print(f"orden por Jev: {n_jev} puntuados ({motivo})")
        n_enc, motivo = encaje_n1(c["pendientes"])
        print(f"encaje N1: {n_enc} puntuados ({motivo})")
    guarda_cola(c)
    return nuevos, len(c["pendientes"]), fuera_cupo



# --- ORDEN POR JEV (21-sep-2026) --------------------------------------------------------------
# Medido antes de enchufarlo (notas del 21-sep en 04 · IA/Notas): Jev (TypeSafe AI), viendo solo
# titulo + tema, separa bien lo que es de {{DIAGNOSTICO}} HR+ de lo que no (AUC 0,82-0,85 sobre
# leads cerrados), pero NO juzga el encaje (no ve su perfil) y habria archivado DAREON-NEC-1 si se
# le dejara decidir. Por eso aqui solo ORDENA: nunca archiva, nunca borra, y no toca los carriles
# cruzados, que buscan FUERA de la mama y a los que la pregunta hundiria por diseno.
# Egress: N0 (titulo publico + tema), por `borde.egress_cientifico` (fail-closed, respeta el HALT).
# Si falta la clave, no hay red o Jev falla, la cola se queda en orden de llegada: fail-open para el
# ORDEN, nunca para el egress.
CRUZ = frozenset({"ne-mama", "cn-dianas", "til", "celular", "biespecifico", "trasladable",
                  "radioligando", "mecanismo-nuevo", "inmuno-frio", "ned-erradicacion"})
DESTINO_JEV = "jev-typesafe"


def orden_jev(pendientes, preguntar=None, clave=None, egress=None):
    """Anade `p_jev` a los pendientes de mama que no lo tienen. Devuelve (n_puntuados, motivo).
    No cambia la lista de pendientes (ni quita ni reordena): solo anota. Fail-open."""
    candidatos = [p for p in pendientes if p.get("tema") not in CRUZ and p.get("p_jev") is None]
    if not candidatos:
        return 0, "nada que puntuar"
    try:
        import bench_jev
        import borde
        preguntar = preguntar or bench_jev.preguntar
        egress = egress or borde.egress_cientifico
        if clave is None:
            clave = bench_jev._clave()
    except (Exception, SystemExit) as e:
        return 0, f"jev no disponible ({type(e).__name__}): orden de llegada"
    n = 0
    for p in candidatos:
        estado = f"Radar topic: {p.get('tema')}. Item: {p.get('titulo', '')}"
        try:
            ok, _ = egress(estado, destino=DESTINO_JEV)
        except Exception:
            ok = False
        if not ok:
            continue
        try:
            prob, _ms = preguntar(estado, clave, instr=bench_jev.INSTR_RADAR)
        except Exception as e:
            return n, f"jev fallo tras {n} ({type(e).__name__}): el resto sigue en orden de llegada"
        p["p_jev"] = round(float(prob), 3)
        n += 1
    return n, "ok"


def clave_orden(p):
    """Primero los ensayos con `p_encaje` (de mas a menos probable); despues la mama puntuada por
    Jev (de mas a menos); despues todo lo demas por orden de llegada. Tupla estable para `sort`."""
    pe = p.get("p_encaje")
    if pe is not None:
        return (0, -pe, p.get("encolado") or "")
    pj = p.get("p_jev")
    if p.get("tema") not in CRUZ and pj is not None:
        return (1, -pj, p.get("encolado") or "")
    return (2, 0, p.get("encolado") or "")



# --- ENCAJE CON PERFIL N1 (22-sep-2026, plan `vast-orbiting-peach`) ----------------------------
# `orden_jev` sabe si un lead es de mama, no si ELLA entra. Con el perfil N1 minimo de
# `bench_jev.PERFIL_N1` + los criterios PUBLICOS de CT.gov, Jev dio AUC 0,884 sobre 52 ensayos
# cerrados (21-sep): Yale el n.º 1, y cazo el falso «encaja» de HER3-DXd. Igual que `orden_jev`,
# aqui solo se ORDENA: nunca archiva, nunca cierra, nunca decide. Lo abre y lo juzga el comite, y
# el freno multicohorte de `cerrar` sigue mordiendo aunque Jev diga 0,99.
#
# Candados, en orden; si falla cualquiera no sale NADA y la cola queda como estaba:
#   1. HALT no activo (`borde.halted`).
#   2. `jev-typesafe` confiado a mano por {{TITULAR}} (`borde.py trust-cloud jev-typesafe`). Revocable.
#   3. Perfil N1 al dia: si ESTADO-ACTUAL §1 (cabecera o «Novedades del …») es posterior a
#      `bench_jev.PERFIL_N1_FECHA`, el perfil puede mentir (p. ej. «sin ADC previo» tras empezar
#      TB06) y daria falsos «encaja». Se para y se avisa. Re-cotejarlo es trabajo de sesion.
#   4. Perfil sin terminos vetados ni identificador directo; estado sin canario.
# Cada envio se sella en la cadena del borde ANTES de salir; si no se puede sellar, no sale.
_RE_NCT = re.compile(r"\bNCT\d{8}\b")


def _nct(p):
    m = _RE_NCT.search(" ".join(str(p.get(k) or "") for k in ("ref", "url", "veredicto")))
    return m.group(0) if m else None



def _criterios_ctgov(nct, get=None):
    """(titulo, criterios) publicos de CT.gov, con los reintentos de `_get`."""
    d = (get or _get)(f"{CTGOV}/{nct}", {"fields": "BriefTitle,EligibilityCriteria"})
    ps = d.get("protocolSection") or {}
    return ((ps.get("identificationModule") or {}).get("briefTitle", ""),
            (ps.get("eligibilityModule") or {}).get("eligibilityCriteria", ""))


def candados_n1(bench=None, borde=None, fecha_clinica=None):
    """(ok, motivo). Fail-closed: cualquier excepcion es un candado cerrado."""
    try:
        if bench is None:
            import bench_jev as bench
        if borde is None:
            import borde
        if borde.halted():
            return False, "HALT activo"
        if not borde.es_trusted(bench.DESTINO_N1):
            return False, "jev-typesafe sin trust-cloud para N1 (acto de {{TITULAR}})"
        if fecha_clinica is None:
            import estado_actual
            fecha_clinica, por_que = estado_actual.lee_fecha_clinica()
            if fecha_clinica is None:
                return False, f"no se puede comprobar el perfil N1: {por_que}"
        if fecha_clinica > tuple(bench.PERFIL_N1_FECHA):
            return False, ("perfil N1 caducado: revísalo contra ESTADO-ACTUAL (§1 del %04d-%02d-%02d, "
                           "perfil del %04d-%02d-%02d)" % (tuple(fecha_clinica) + tuple(bench.PERFIL_N1_FECHA)))
        if any(v in bench.PERFIL_N1.lower() for v in bench._VETADAS_N1):
            return False, "el perfil N1 lleva un término vetado"
        crudo, por_que = borde.identificador_directo(bench.PERFIL_N1)
        if crudo:
            return False, f"el perfil N1 parece identificable ({por_que})"
        return True, "ok"
    except (Exception, SystemExit) as e:
        return False, f"candado reventó ({type(e).__name__})"


def encaje_n1(pendientes, preguntar=None, clave=None, get=None, bench=None, borde=None,
              fecha_clinica=None):
    """Anade `p_encaje` a los ENSAYOS con NCT que no lo tienen. Devuelve (n_puntuados, motivo).
    Solo anota: no quita, no reordena, no toca descartados ni cerrados. Fail-open para el orden,
    fail-closed para el egress."""
    candidatos = [p for p in pendientes
                  if p.get("tipo") == "ensayo" and p.get("p_encaje") is None and _nct(p)]
    if not candidatos:
        return 0, "nada que puntuar"
    ok, motivo = candados_n1(bench=bench, borde=borde, fecha_clinica=fecha_clinica)
    if not ok:
        return 0, motivo
    try:
        if bench is None:
            import bench_jev as bench
        if borde is None:
            import borde
        preguntar = preguntar or bench.preguntar
        if clave is None:
            clave = bench._clave()
    except (Exception, SystemExit) as e:
        return 0, f"jev no disponible ({type(e).__name__}): sin encaje"
    n = 0
    for p in candidatos:
        nct = _nct(p)
        try:
            bt, crit = _criterios_ctgov(nct, get=get)
        except Exception as e:
            print(f"  encaje N1: {nct} sin criterios ({type(e).__name__}), sigue sin puntuar")
            continue
        if not crit.strip():
            continue
        estado = f"{bench.PERFIL_N1}\n\nTrial {nct}: {bt}\nEligibility criteria:\n{crit[:6000]}"
        try:
            if borde.hay_canario(estado):
                continue
            borde._sellar({"evento": "egress_n1", "destino": bench.DESTINO_N1, "permitido": True,
                           "motivo": f"encaje N1 radar ({nct})", "nivel": "allow",
                           "sello": hashlib.sha256(estado.encode("utf-8")).hexdigest()[:16]})
        except Exception as e:
            return n, f"borde no pudo sellar ({type(e).__name__}): paro"
        try:
            prob, _ms = preguntar(estado, clave, instr=bench.INSTR_N1)
        except Exception as e:
            return n, f"jev fallo tras {n} ({type(e).__name__}): el resto sin encaje"
        p["p_encaje"] = round(float(prob), 3)
        n += 1
    return n, "ok"


# --- PRIORIZACION DE LA COLA (18-sep-2026) ----------------------------------------------------
# Al abrir el barrido a cuatro capas mas el carril de navegador, la cola paso de 11 a 103 leads en
# un dia. Una cola que no se puede vaciar deja de ser una lista de trabajo y pasa a ser un archivo
# que nadie lee -- justo el fallo que la cola venia a corregir. Asi que se PUNTUA contra su perfil
# real y lo que no llega al umbral se archiva CON MOTIVO (no se borra: `cola --todo` lo lista y
# `rescatar` lo devuelve). El criterio esta escrito aqui, es auditable y se puede discutir; lo que
# no vale es que el filtro sea "lo que me dio tiempo a mirar".
#
# Su perfil, del que sale el peso de cada senal: luminal B HR+ (ER 85 %), HER2 IHQ 0, componente
# neuroendocrino focal, metastasico oseo difuso + higado, {{LOCUS}} y 8p11 amplificados, PIK3CA sin alteracion,
# ESR1 {{VARIANTE}} en plasma (0,24 % -> 1,38 %, ESTADO-ACTUAL; no aparece en tejido, y esta linea decia
# "sin alteracion" hasta el 21-sep), HRD negativa, MSS, TMB 4, quimio-naive y ADC-naive,
# mielotoxicidad G3-G4 previa.
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
    mas_viejo = min((p.get("encolado") or "") for p in pend)
    pend.sort(key=clave_orden)
    print(f"COLA DE VERIFICACIÓN — {len(pend)} lead(s) sin abrir "
          f"(el más viejo, del {mas_viejo}); ensayos por encaje N1, luego mama por Jev, lo demás por llegada:")
    for p in pend:
        sc = p.get("score")
        marca = f"[{sc:3}]" if sc is not None else "[  ?]"
        if p.get("p_encaje") is not None:
            marca += f" encaje {p['p_encaje']:.2f}"
        if p.get("p_jev") is not None:
            marca += f" jev {p['p_jev']:.2f}"
        calidad = (" " + " ".join(p["calidad"])) if p.get("calidad") else ""
        print(f"  {marca} [{p['tema']:12}] {p.get('encolado')} · {p['tipo']:8} · {p['titulo']}{calidad}")
        print(f"                 {p['url']}  (ref {p['ref']})")
    if len(pend) > COLA_TOPE:
        print(f"⚠️ la cola pasa de {COLA_TOPE}: se está acumulando trabajo sin verificar.")
    print("Cerrar uno: python3 tools/radar_ned_diario.py cerrar --ref <ref> "
          "--veredicto 'qué dice la fuente abierta'  [--radar si sube a Radar: lo chino exige "
          "ID de registro + etiqueta de población + PMID/DOI en el veredicto]")
    return 0


# --- GATE lead → Radar para lo chino (20-sep-2026) --------------------------------------------
# Un lead se CIERRA con veredicto (se abrió la fuente y se dice qué dice). SUBIR a Radar es otra
# cosa: es lo que la sesión o el comité escribe en 04 · IA/Radar como hallazgo que cuenta. Para lo
# chino, subir exige tres cosas en el propio veredicto, y el gate las comprueba con regex, no con
# criterio: (a) el ID de registro abierto (NCT/ChiCTR/CTR), (b) una etiqueta de población del
# vocabulario cerrado, (c) PMID o DOI cotejado. Sin las tres, `cerrar --radar` se rechaza y el
# lead sigue en cola: nadie puede "subir" un registro chino sin resultados a base de titular.
_RE_REGISTRO_ABIERTO = re.compile(r"\b(NCT\d{8}|ChiCTR\d{10}|CTR\d{8})\b")
_RE_PMID_DOI = re.compile(r"\bPMID:?\s*\d{6,9}\b|\b10\.\d{4,9}/\S+", re.I)
POBLACION_VOCAB = ("población-CN única", "población mixta", "población internacional")
_RE_POBLACION = re.compile("|".join(re.escape(p) for p in POBLACION_VOCAB), re.I)


def es_lead_cn(lead):
    uid = (lead.get("uid") or "")
    return bool(lead.get("cn")) or (lead.get("tema") or "").startswith("cn-") \
        or lead.get("tema") in _REGISTROS_CN or uid.split(":", 1)[0] in _REGISTROS_CN


def gate_radar_cn(lead, veredicto):
    """(ok, faltan). Solo aplica a leads chinos; para el resto devuelve (True, [])."""
    if not es_lead_cn(lead):
        return True, []
    v = veredicto or ""
    faltan = []
    if not _RE_REGISTRO_ABIERTO.search(v):
        faltan.append("ID de registro abierto (NCT........ / ChiCTR.......... / CTR........)")
    if not _RE_POBLACION.search(v):
        faltan.append("etiqueta de población: " + " | ".join(POBLACION_VOCAB))
    if not _RE_PMID_DOI.search(v):
        faltan.append("PMID o DOI cotejado")
    return (not faltan), faltan


# --- GATE «encaja» en ensayos multicohorte (21-sep-2026) --------------------------------------
# El 20-sep se cerro NCT06172478 (HER3-DXd, HERTHENA-PanTumor01) como «el unico que encaja» porque
# «no exige tratamiento previo especifico». Falso: la cohorte de MAMA exige una linea de quimio, y
# ella nunca ha recibido quimio. En un ensayo pan-tumor o de cesta, los requisitos de linea viven
# DENTRO de cada cohorte, no en la lista general. Asi que un veredicto que diga que encaja en un
# ensayo multicohorte tiene que nombrar la cohorte y citar literalmente su criterio. Sin las dos
# cosas, `cerrar` se rechaza y el lead sigue en cola. Es regex, no criterio: se puede discutir.
_RE_MULTICOHORTE = re.compile(
    r"pan-?tumou?r|basket|umbrella|platform|multi-?cohort|multiple cohorts|solid tumou?rs|"
    r"tumores s[oó]lidos|实体瘤|多队列|篮子|伞式", re.I)
_RE_DICE_ENCAJA = re.compile(r"\b(acceso|encaja|compatible|sin exclusiones|puede entrar)\b", re.I)
_RE_DICE_NO = re.compile(r"\bno (encaja|es compatible|puede entrar)\b|descartad|\bexcluye\b|no aplica", re.I)
# La cohorte de MAMA, no un «entre las cohortes» generico (que es lo que dejo pasar HER3-DXd).
_RE_NOMBRA_COHORTE = re.compile(
    r"cohorte\s+(de\s+|del\s+)?(mama|c[aá]ncer\s+de\s+mama|HR\+?)|breast[\w\s-]{0,25}cohort|"
    r"cohort[\w\s-]{0,20}breast|乳腺癌队列", re.I)
# Salida limpia para los que NO tienen cohortes (SI-B036: un solo bloque de criterios).
_RE_SIN_COHORTES = re.compile(r"sin cohortes|criterios (únicos|unicos|comunes)|single set of criteria", re.I)
_RE_CITA_LITERAL = re.compile(r"[«\"“'‘][^«»\"”’']{15,}[»\"”'’]")


def gate_multicohorte(lead, veredicto):
    """(ok, faltan). Solo muerde en ensayos multicohorte, y ahi en todo veredicto que no sea un
    descarte claro: el de HER3-DXd no decia «encaja», decia «no exige tratamiento previo», que es
    la misma afirmacion. Un descarte («descartado», «no aplica», «excluye») pasa sin cita."""
    v = veredicto or ""
    if lead.get("tipo") in ("paper", "preprint", "patente"):
        return True, []  # solo ensayos: en un paper no hay cohorte en la que entrar
    if not _RE_MULTICOHORTE.search(lead.get("titulo") or ""):
        return True, []
    if _RE_DICE_NO.search(v) and not _RE_DICE_ENCAJA.search(v):
        return True, []
    faltan = []
    if not (_RE_NOMBRA_COHORTE.search(v) or _RE_SIN_COHORTES.search(v)):
        faltan.append("qué cohorte (la de mama HR+ u otra) se ha leído, o «sin cohortes» si los "
                      "criterios son únicos para todos los tumores")
    if not _RE_CITA_LITERAL.search(v):
        faltan.append("cita literal del criterio de esa cohorte (línea, quimio previa, HER2, muestra)")
    return (not faltan), faltan


def cmd_cerrar(a):
    """Cierra un lead SOLO con veredicto: sin haber abierto la fuente no hay nada que cerrar.
    Con --radar ademas lo marca como subido a Radar, y para lo chino pasa gate_radar_cn."""
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
    ok, faltan = gate_multicohorte(cerrado, a.veredicto)
    if not ok:
        print("RECHAZADO: el veredicto dice que encaja en un ensayo MULTICOHORTE y le falta:")
        for f in faltan:
            print(f"  - {f}")
        print("El lead sigue en la cola. (Nació del falso «encaja» de HER3-DXd, 20-sep-2026.)")
        return 4
    radar = bool(getattr(a, "radar", False))
    if radar:
        ok, faltan = gate_radar_cn(cerrado, a.veredicto)
        if not ok:
            print("RECHAZADO: un lead chino no sube a Radar sin, en el veredicto:")
            for f in faltan:
                print(f"  - {f}")
            print("El lead sigue en la cola. Sin --radar se cierra como lead abierto, no como hallazgo.")
            return 3
        cerrado["radar"] = True
    # `_limpia` recorta a MAX_TITULO=160 porque existe para TITULOS, asi que el `[:400]` de aqui
    # era decorativo: TODOS los veredictos se guardaban cortados a 160, justo por donde empieza la
    # cita literal que los justifica. Los 20 cierres del 20-sep-2026 quedaron asi («criterio
    # literal: 'Previously» y ahi se acababa), y el rastro de POR QUE se descarto un ensayo dejo
    # de ser auditable. Lo cazo el comite de verificacion el mismo dia. Es el MISMO fallo que el
    # del DOI y el de `_limpia_largo`: reusar el saneador de titulos para lo que no es un titulo.
    cerrado["veredicto"] = _limpia_largo(a.veredicto)[:2000]
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
                it["calidad"] = etiquetas_calidad(it, tema)
                it["modalidad"] = modalidad(it.get("titulo"))
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
         "🇨🇳 Capa china: Europe PMC por afiliación ampliada (China, HK, Macau, Taiwan y 9 ciudades), "
         "preprints sin filtro de país, revistas en chino por título, e ICTRP (espejo OMS de ChiCTR) "
         "por el VPS. Cada lead chino lleva etiqueta de calidad entre corchetes "
         "(registro sin resultados, RCT de población china única, IIT de un brazo, preprint, revista "
         "en chino, nota de prensa): **nada chino sube de lead a Radar sin ID de registro abierto, "
         "etiqueta de población y PMID/DOI cotejado** (`cerrar --radar`). ChinaXiv queda fuera a "
         "propósito (rendimiento oncológico ~0).", "",
         "⚠️ **No cubierto por este barrido** (necesita navegador): **CTIS** (registro de ensayos de "
         "la UE — ONA-255 solo vive ahí; el .sh lo intenta por el VPS), CDE de la NMPA, congresos "
         "(ASCO/ESMO/SABCS/AACR), notas de prensa de biotech y X.", ""]
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
            calidad = (" " + " ".join(h["calidad"])) if h.get("calidad") else ""
            mod = h.get("modalidad") or modalidad(h.get("titulo"))
            L.append(f"- **{h['tipo']}** · {h['fecha'] or 's/f'} · {h['titulo']} "
                     f"([{h['ref']}]({h['url']})) [{mod}]{calidad}{aviso}")
        L.append("")
    L += seccion_modalidades(res)
    L += ["---", "",
          "Siguiente paso humano/LLM: triar los 🔴, verificar contra fuente primaria (PMID/DOI "
          "abiertos), y mirar CTIS con navegador.", ""]
    return "\n".join(L)


def seccion_modalidades(res):
    """Cierre del digest: recuento por familia de terapia personalizada en 30 dias + cobertura.
    Solo si el barrido trae `recuento30` (lo pone cmd_run / el carril de navegador)."""
    r30 = res.get("recuento30")
    if not r30:
        return []
    hoy = recuento_dia(res)
    L = [f"## Terapia personalizada: recuento por modalidad ({DIAS_COBERTURA} dias)", "",
         "La familia entera, no solo la vacuna. Si una fila lleva semanas a cero, lo primero es "
         "sospechar de la consulta, no concluir que no hay nada.", "",
         "| Modalidad | Hoy | Ultimos %d dias |" % DIAS_COBERTURA, "|---|---:|---:|"]
    for m in FAMILIA + ["otro"]:
        L.append(f"| [{m}] | {hoy.get(m, 0)} | {r30.get(m, 0)} |")
    L.append("")
    for linea in res.get("cobertura") or []:
        L.append(f"- {linea}")
    if not res.get("cobertura"):
        L.append("- ✅ cobertura: toda la familia ha traido al menos un lead en la ventana.")
    L.append("")
    return L


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
    h = registra_historial(res, persistir=not a.dry)
    res["recuento30"] = recuento_30(h)
    res["cobertura"] = cobertura(h)
    hoy_m = recuento_dia(res)
    print("modalidades hoy / %dd: " % DIAS_COBERTURA
          + " · ".join(f"[{m}] {hoy_m.get(m, 0)}/{res['recuento30'].get(m, 0)}" for m in FAMILIA + ["otro"]))
    for linea in res["cobertura"]:
        print("  " + linea)
    if a.dry:
        print("--dry: no escribo digest, ni estado, ni historial, ni aviso.")
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
    p.add_argument("--radar", action="store_true",
                   help="cerrar: el lead SUBE a Radar (lo chino pasa gate_radar_cn)")
    p.add_argument("--umbral", type=int, help="corte de score en priorizar (def. %d)" % UMBRAL)
    p.add_argument("--todo", action="store_true", help="cola: lista los archivados por el filtro")
    a = p.parse_args()
    return {"run": cmd_run, "check": cmd_check, "status": cmd_status, "sellar": cmd_sellar,
            "cola": cmd_cola, "cerrar": cmd_cerrar, "priorizar": cmd_priorizar,
            "rescatar": cmd_rescatar}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
