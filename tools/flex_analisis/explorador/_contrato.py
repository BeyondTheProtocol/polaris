"""Contrato del explorador single-cell (export curado del Flex): vocabularios CERRADOS, allow-lists,
constructores de obs/uns y el validador fail-closed de las salidas.

Sin datos ni rutas clínicas: lo importa `exporta.py` (que sí corre por la ventanilla) y
`tests/test_flex_explorador.py` con un fixture sintético. Todo lo que aquí es «prohibido» se busca en
CADA cadena de CADA salida (árbol HDF5 entero, JSON, CSV, markdown) y además pasa por `puerta_n1`.
Ningún identificador real (bloque, pool, barcode) vive en este fichero: las formas de los ids de
muestra se leen de config.csv en memoria y se pasan como `prohibidas`.
"""
import json
import os
import re
import sys

import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ_REPO = os.path.dirname(os.path.dirname(os.path.dirname(AQUI)))
TOOLS = os.path.join(RAIZ_REPO, "tools")

LESIONES = ("higado", "hueso")
OBS_COLS = ["lesion", "grupo", "tipo_celular", "umap_1", "umap_2"]
VAR_COLS = ["ensembl"]
UNS_KEYS = ("suelo_ambiental", "referencia_emparejada", "veredicto_acta", "cnv_hueso",
            "cnv_higado_pseudobulk", "version", "avisos")
VERSION = "explorador-v1 · semilla del cierre (_c.SEED)"
MODOS_SONDAS = ("todas", "spliced")


def version_contrato(modo="todas"):
    assert modo in MODOS_SONDAS
    return "%s · sondas %s" % (VERSION, modo)

# Grupos: vocabulario cerrado por lesión. El de interés lleva la llamada; «tumor»/«malign» no
# aparecen en ningún valor del hígado (no resuelto célula a célula: acta, ESTADO FINAL).
GRUPOS = {
    "higado": {"interes": "luminal_alta_confianza",
               "referencia": ["hepatocito", "NK", "mieloide", "endotelio", "fibroblasto_estelar"],
               "otras": "otras"},
    "hueso": {"interes": "malignas_limpias",
              "referencia": ["T", "NK", "B", "plasmatica", "mieloide", "neutrofilo", "mastocito", "endotelio"],
              "otras": "otras"},
}
N_INTERES = {"higado": 993, "hueso": 68}
PROHIBIDAS_HIGADO = ("tumor", "malign")
RE_TIPO = re.compile(r"^[a-z_]+$")
# `tipo` de las cachés (anotación de A: PROGRAMAS + no_asignado) → vocabulario ^[a-z_]+$. Fail-closed:
# un tipo que no esté aquí rompe el export.
TIPOS = {"epitelial": "epitelial", "hepatocito": "hepatocito", "colangiocito": "colangiocito",
         "T": "linfocito_t", "NK": "nk", "B": "linfocito_b", "plasmatica": "plasmatica",
         "mieloide": "mieloide", "neutrofilo": "neutrofilo", "mastocito": "mastocito",
         "endotelio": "endotelio", "fibroblasto_estelar": "fibroblasto_estelar",
         "osteoblasto": "osteoblasto", "osteoclasto": "osteoclasto", "eritroide": "eritroide",
         "megacariocito": "megacariocito", "no_asignado": "no_asignado"}

# «sostenido_con_matiz» y «con_matiz» son DOS lecturas distintas del acta (red-team de cifras del 17-oct): HER3 del
# hígado es «SOSTENIDO con matiz» (l.123, una de las tres que aguantan según la lectura de l.135: NY-BR-1, 5T4 y
# HER3); ESR1, HER2, Nectin-4 y LIV-1 son solo «Con matiz» (l.124-127), sin «se sostiene». Fundirlas hacía que
# la web rotulara HER2 (IHQ 0) como «se sostiene».
VEREDICTOS = ("sostenido", "sostenido_con_matiz", "con_matiz", "en_el_limite", "no_sostenido", "no_resoluble",
              "referencia_igual_o_mas", "solo_nivel", "marcador_ne", "no", "cerrado_por_arn", "sin_veredicto")
# Veredicto del acta por gen y lesión, con la línea de la que sale. Es la LECTURA DE POLARIS del acta
# (borrador, pendiente de la lectura humana del laboratorio), no palabra del laboratorio.
# Hígado: tabla «Dianas del HÍGADO» (columna Veredicto). Hueso, por orden de prevalencia: la fila «Dianas
# en el tumor del hueso (68 limpias)» del ESTADO FINAL (l.102, PREVALECE); la fila «PRAME y MAGE-A4» del
# ESTADO FINAL (l.99: cerrados por ARN, los positivos del hueso son ADN de núcleos no tumorales); y para
# lo que esas dos no valoran, las filas de la tabla «Dianas del HÍGADO» que llevan columna Hueso (68):
# CHGA/SYP/{{DIANA3}} (l.131), FGFR1/CCND1 (l.132) y la fila «No» (l.133, hueso ≤6 %).
# Lo que no está en esas fuentes: sin_veredicto («no valorado en esa fila»), puesto al construir.
# ESR1 del hueso NO hereda el «Con matiz (selección por programa ER)» de l.124: ese matiz es del subconjunto del
# hígado (seleccionado por programa ER); las 68 malignas del hueso se eligieron por EPI4 + inferCNV, así que en el
# hueso esa fila no valora ESR1 → sin_veredicto (VEREDICTO_NO_TRASLADADO lo declara en el informe).
ACTA = "acta del tribunal, fase 6 (flex-acta-del-tribunal-fase-6-lo-que-se-sostiene)"
_FILA_NO = ["ROR1", "FOLR1", "CEACAM5", "VTCN1", "CDH17", "GPNMB", "MSLN", "CLDN6", "CLDN18", "{{DIANA2}}", "{{DIANA}}",
            "NCAM1", "PGR", "AR", "PRAME", "MAGEA1", "MAGEA4", "MAGEA10", "MAGEA11", "MAGEA12"]
VEREDICTO_ACTA = {
    "higado": {
        "ANKRD30A": ("sostenido", 121), "TPBG": ("sostenido", 122), "ERBB3": ("sostenido_con_matiz", 123),
        "ESR1": ("con_matiz", 124), "ERBB2": ("con_matiz", 125), "NECTIN4": ("con_matiz", 126),
        "SLC39A6": ("con_matiz", 127), "MUC1": ("no_resoluble", 128), "TACSTD2": ("no_sostenido", 129),
        "CD276": ("no_sostenido", 130), "CHGA": ("marcador_ne", 131), "SYP": ("marcador_ne", 131),
        "{{DIANA3}}": ("marcador_ne", 131), "FGFR1": ("solo_nivel", 132), "CCND1": ("solo_nivel", 132),
        **{g: ("no", 133) for g in _FILA_NO},
    },
    "hueso": {
        # l.133 primero: lo que sigue (l.131-132, l.99 y l.102, que PREVALECE) lo pisa
        **{g: ("no", 133) for g in _FILA_NO},
        "CHGA": ("marcador_ne", 131), "SYP": ("marcador_ne", 131),
        "{{DIANA3}}": ("marcador_ne", 131), "FGFR1": ("solo_nivel", 132), "CCND1": ("solo_nivel", 132),
        "PRAME": ("cerrado_por_arn", 99), "MAGEA4": ("cerrado_por_arn", 99),
        "ANKRD30A": ("sostenido", 102), "TPBG": ("sostenido", 102), "SLC39A6": ("en_el_limite", 102),
        "ERBB3": ("en_el_limite", 102), "ROR1": ("no", 102),
        **{g: ("referencia_igual_o_mas", 102) for g in ["CD276", "NECTIN4", "TACSTD2", "ERBB2", "MUC1"]},
    },
}
# Genes con veredicto en OTRA fila del acta que el contrato no mapea (se declaran en el informe). Vacío
# desde el red-team del 17-oct (B4): PRAME/MAGEA4 (l.99) y FGFR1 (l.132) ya se mapean.
VEREDICTO_FUERA_DE_CONTRATO = {"hueso": {}, "higado": {}}
# Filas del acta que tienen cifra en una lesión pero cuyo veredicto NO se traslada a ella (se declara en el informe).
VEREDICTO_NO_TRASLADADO = {
    "hueso": {"ESR1": "l.124 «Con matiz (selección por programa ER)»: el matiz es del subconjunto del hígado, seleccionado "
                      "por programa ER; las 68 malignas del hueso se eligieron por EPI4 + inferCNV. En el hueso: sin_veredicto."},
    "higado": {},
}
ETIQUETA_VEREDICTO = "lectura de Polaris, borrador, pendiente de la lectura humana del laboratorio"

PANEL_10 = ["ANKRD30A", "TPBG", "ERBB3", "SLC39A6", "ERBB2", "TACSTD2", "CD276", "NECTIN4", "MUC1", "FGFR1"]
# Cifras del acta (fracción detectada en el grupo de interés: tabla «Dianas del HÍGADO», columnas
# «Hígado (993)» y «Hueso (68)»): CHECK DE REPRODUCCIÓN fail-closed del export (reproduce_acta) y delta
# solo-unión. «Saturados» (FGFR1, sin cifra en la tabla): None. {{DIANA3}} del hueso va con un decimal (1,5 %).
ACTA_FRAC = {
    "higado": {"ANKRD30A": 0.58, "TPBG": 0.25, "ERBB3": 0.32, "SLC39A6": 0.39, "ERBB2": 0.41, "TACSTD2": 0.13,
               "CD276": 0.25, "NECTIN4": 0.16, "MUC1": 0.54, "FGFR1": None,
               "ESR1": 0.58, "CHGA": 0.22, "SYP": 0.14, "{{DIANA3}}": 0.08},
    "hueso": {"ANKRD30A": 0.54, "TPBG": 0.26, "ERBB3": 0.28, "SLC39A6": 0.34, "ERBB2": 0.10, "TACSTD2": 0.09,
              "CD276": 0.22, "NECTIN4": 0.07, "MUC1": 0.16, "FGFR1": None,
              "ESR1": 0.68, "CHGA": 0.27, "SYP": 0.21, "{{DIANA3}}": 0.015},
}
# Cifras del acta medidas FUERA de la circularidad (LOO de higado_dianas: el gen participa en la selección
# del grupo y se mide con un conjunto construido sin él). Para esos genes el check compara contra el LOO,
# no contra el grupo de 993, y la página enseña el LOO con su rótulo. Valor: línea del acta.
ACTA_LOO = {"higado": {"ESR1": 124}, "hueso": {}}


def tolerancia_acta(acta):
    """El acta da % enteros (±0,5 pt de redondeo) salvo alguno con un decimal (±0,05 pt); +0,1/+0,01 de holgura."""
    pct = round(acta * 100, 6)
    return 0.006 if abs(pct - round(pct)) < 1e-6 else 0.0006


def reproduce_acta(lesion, frac_grupo, loo=None):
    """CHECK DE REPRODUCCIÓN: cada cifra de ACTA_FRAC[lesion] frente a lo que enseña el export. frac_grupo:
    {gen: fracción detectada en el grupo de interés}; loo: {gen: {"frac": …}} (higado_dianas, vía 1).
    Devuelve (filas, genes_que_no_cuadran). Un gen del acta que falte en el export no cuadra."""
    loo = loo or {}
    filas, malos = {}, []
    for g, acta in ACTA_FRAC[lesion].items():
        if acta is None:
            filas[g] = {"acta": None, "nota": "saturado en el acta: solo nivel"}
            continue
        tol = tolerancia_acta(acta)
        if g in ACTA_LOO[lesion]:
            v = (loo.get(g) or {}).get("frac")
            fuente = "loo_higado_dianas (linea %d del acta)" % ACTA_LOO[lesion][g]
        else:
            v = frac_grupo.get(g)
            fuente = "grupo_de_interes"
        pasa = v is not None and abs(float(v) - acta) <= tol
        filas[g] = {"acta": acta, "export": None if v is None else round(float(v), 4), "fuente": fuente,
                    "tolerancia_pts": round(100 * tol, 2), "pasa": bool(pasa)}
        if g in ACTA_LOO[lesion] and g in frac_grupo:
            filas[g]["circular_en_el_grupo"] = round(float(frac_grupo[g]), 4)
        if not pasa:
            malos.append(g)
    return filas, malos
GENES_WEB = PANEL_10 + [
    "CCND1", "ESR1", "PGR", "AR", "CHGA", "SYP", "{{DIANA3}}", "PRAME", "MAGEA1", "MAGEA4", "MAGEA10", "MAGEA11",
    "MAGEA12", "MAGEC1", "MAGEC2", "CTAG2", "B2M", "TAP1", "TAP2", "ROR1", "FOLR1", "CEACAM5", "VTCN1", "MSLN",
    "CLDN6", "CLDN18", "{{DIANA2}}", "{{DIANA}}", "NCAM1", "GPNMB", "CDH17", "ITGB6", "GRPR", "FAP", "ZNF703", "NSD3",
    "FGF3", "FGF19", "MKI67", "TOP2A",
    # marcadores de tipo celular
    "ALB", "APOA1", "PTPRC", "CD3E", "NKG7", "CD68", "C1QA", "CSF1R", "PECAM1", "VWF", "COL1A1", "DCN", "ACTA2",
    "KRT19", "KRT7", "EPCAM", "FOXA1", "GATA3", "TRPS1", "ACP5", "CTSK", "MMP9", "S100A8", "S100A9", "HBB",
    # controles
    "ACTB", "GAPDH",
]
CNV_VENTANAS = ["8p11", "{{LOCUS}}"]
# Método del score de CNV según la matriz de la que sale (B3 del red-team: en «todas» no lleva «_spliced»).
# Es una INFERENCIA desde la expresión (ARN) frente a la referencia, no una lectura de ADN.
CNV_METODOS = {"todas": "tribunal_score_ventana_log2_obs_esp", "spliced": "tribunal_score_ventana_log2_obs_esp_spliced"}
WEB_KEYS = ("version", "lesion", "n_celulas", "grupos", "tipos", "celulas", "genes", "expr", "suelo",
            "referencia", "veredicto", "sondas", "seleccion", "loo", "adn", "cnv", "qc",
            "marcadores", "perfil_bins", "adn_por_grupo")   # agregados (18-oct): los mismos que el Zarr, cotejados
# QC agregado de la lesión («Loupe público», 18-oct): el del analista A, que es el que fija las células del
# explorador (tribunal/_comun.carga: las del congelado de A, mismas que B). Allow-list cerrada; sin ids.
QC_KEYS = ("fuente", "n_inicial", "n_final", "umbral_UMI", "umbral_genes", "umbral_pct_MT", "excluidas_UMI_genes",
           "excluidas_MT_adicionales", "scrublet_dobletes", "scrublet_umbral", "mediana_UMI_inicial",
           "mediana_UMI_final", "mediana_genes_inicial", "mediana_genes_final", "n_sondas_MT")
QC_OBLIGATORIAS = ("fuente", "n_inicial", "n_final", "umbral_UMI", "umbral_genes", "scrublet_dobletes")
QC_FUENTE = "analista A: QC común (MAD sobre UMI y genes, % MT si hay sondas, Scrublet); mismas células en A y B"


def construye_qc(qc_a):
    """QC agregado de una lesión desde el qc.json de A: solo las claves de la allow-list, en su orden."""
    out = {"fuente": QC_FUENTE}
    for k in QC_KEYS[1:]:
        if k in qc_a:
            out[k] = qc_a[k]
    return out


def _valida_qc(qc, n, b):
    if not isinstance(qc, dict) or not set(QC_OBLIGATORIAS) <= set(qc) or not set(qc) <= set(QC_KEYS):
        raise ValidacionFallida("%s: qc fuera del contrato" % b)
    if qc["fuente"] != QC_FUENTE or qc["n_final"] != n or not _es_ent(qc["n_inicial"], n):
        raise ValidacionFallida("%s: qc no cuadra con las células del explorador" % b)
    for k in ("umbral_UMI", "umbral_genes"):
        u = qc[k]
        if not (isinstance(u, list) and len(u) == 2 and all(isinstance(x, (int, float)) and x >= 0 for x in u) and u[0] <= u[1]):
            raise ValidacionFallida("%s: qc.%s fuera del contrato" % (b, k))
    for k in set(qc) - {"fuente", "umbral_UMI", "umbral_genes", "umbral_pct_MT", "scrublet_umbral"}:
        if not _es_ent(qc[k], 0) and not (isinstance(qc[k], float) and qc[k] >= 0):
            raise ValidacionFallida("%s: qc.%s no es un número >= 0" % (b, k))
# Genes que participan en la SELECCIÓN del grupo de interés (su cifra en ese grupo es circular): papel en
# la regla. Hígado (REGLAS_HIGADO_v1, vía 1): panel de identidad, FGFR1_alto, marcadores de exclusión y
# genes ligados al programa ER con el que se selecciona (favorecidos por seleccionar con ESR1/TFF1/FOXA1):
# LIGADOS_ER de las reglas (PGR, AR, CCND1) más CIRCULAR_PROGRAMA_HIGADO. Hueso (tribunal): EPI4 >= 1.
SELECCION_ROLES = ("identidad", "fgfr1_alto", "exclusion", "ligado_er")
# Circularidad de PROGRAMA que el acta declara y las reglas no listan como LIGADOS_ER (red-team de cifras del
# 17-oct): acta l.117, «ESR1 y NY-BR-1 se miden en un conjunto seleccionado por dianas de ER (circularidad de
# programa)»; REGLAS_HIGADO_v1 via1_alta_confianza.fuera_del_panel_y_por_que, «ANKRD30A es de diferenciación
# mamaria: circular». ESR1 ya va como «identidad»; NY-BR-1 (ANKRD30A) entra aquí con papel «ligado_er». Sin
# LOO: higado_dianas no lo midió sin esa circularidad (via1.ANKRD30A.LOO = false).
CIRCULAR_PROGRAMA_HIGADO = ("ANKRD30A",)
# Las fracciones de ADN son de UMI (moléculas contadas: «Estimated UMIs from genomic DNA» de Cell Ranger y
# frac_UMI_ADN_estimada de tribunal/adn.json), no de lecturas. Las claves conservan «lecturas» para no mover el
# contrato que comparte con la web (no se pintan); el texto de la página dice UMI.
ADN_KEYS = ("sondas_dentro_de_exon", "sondas_panel", "frac_lecturas_estimada", "frac_lecturas_cell_ranger",
            "frac_lecturas_interes")
LOO_KEYS = ("n", "frac", "ref", "cp10k_pseudobulk")
MANIFIESTO_KEYS = ("version", "sello", "ok_titular", "lesiones", "ficheros", "modo_sondas")
# Estados del manifiesto que el validador admite (A4 del red-team, choque fecha_ok/validador). El export
# escribe SIEMPRE el primero; el segundo lo pone una persona al publicar, y entonces se vuelve a validar.
ESTADO_EXPORT = {"sello": "sin_verificar", "ok_titular": False}
ESTADO_PUBLICADO = {"sello": "verificado", "ok_titular": True}
RE_FECHA_OK = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Etiqueta sin ambigüedad de cada versión del export (manifiesto e informe). La decisión entre las dos
# es de {{TITULAR}} con verificacion («solo spliced» venía dentro del muro del encargo).
ETIQUETA_MODO = {"todas": "todas_incluidas", "spliced": "solo_spliced"}
# El h5ad no puede llevar el nombre de la titular en ninguna clave ni cadena (ni como parte de una clave).
RE_TITULAR_H5 = re.compile("titular", re.I)

# ── Patrones prohibidos en CUALQUIER cadena de salida ─────────────────────────────────────────
RE_BARCODE = re.compile(r"[ACGTN]{12,}(?:-\d+)?")
RE_UNSPLICED = re.compile(r"unspliced", re.I)
RE_PROBE_BC = re.compile(r"(?<![A-Za-z0-9])A[-_ ]?D\d{1,2}(?![0-9])")       # A-D10 / A-D11 y vecinos
RES_FECHA = [re.compile(r"(?<!\d)(?:19|20)\d\d[-/._](?:0?[1-9]|1[0-2])[-/._](?:0?[1-9]|[12]\d|3[01])(?!\d)"),
             re.compile(r"(?<!\d)(?:0?[1-9]|[12]\d|3[01])[-/.](?:0?[1-9]|1[0-2])[-/.](?:19|20)?\d\d(?!\d)"),
             re.compile(r"(?<!\d)(?:19|20)\d\d(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?!\d)"),
             # mes en letra + separador + año («17-oct-26», «oct 2026»); el separador es obligatorio para
             # que un símbolo de gen («MARCH10», «SEPTIN14») no cuente como fecha
             re.compile(r"(?<![A-Za-z])(?:\d{1,2}[-/ ]?)?(?:ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic|jan|apr|aug|dec)"
                        r"[a-z]*\.?[-/ ](?:19|20)?\d\d(?![0-9])", re.I)]
HOSPITALES = ("dfci", "{{CENTRO}}", "dana farber", "farber", "usz", "{{CIUDAD}}", "{{CIUDAD}}", "universitätsspital",
              "universitatsspital", "brigham", "hospital", "clínica universidad", "vall d'hebron")
RE_HEX64 = re.compile(r"^[0-9a-f]{64}$")
RE_HEX_LARGO = re.compile(r"(?<![0-9A-Za-z])(?=[0-9a-fA-F]*[a-fA-F])(?=[0-9a-fA-F]*[0-9])[0-9a-fA-F]{16,}(?![0-9A-Za-z])")
RE_INDICE = re.compile(r"^c\d{5}$")


def formas_laxas(identificador):
    """Formas en las que un id de muestra podría colarse: entero, en minúsculas, sin separadores, cada
    parte con ≥5 caracteres y la tira de cifras larga (≥5) con o sin la letra que la precede."""
    s = str(identificador).strip()
    if not s:
        return set()
    out = {s, s.lower(), re.sub(r"[-_. /:]", "", s).lower()}
    for parte in re.split(r"[-_. /:]+", s):
        if len(parte) >= 5:
            out.add(parte.lower())
            out.add(parte.lower().lstrip("b"))
    for m in re.finditer(r"\d{5,}", s):
        out.add(m.group(0))
    return {x for x in out if len(x) >= 5}


def _normal(s):
    return re.sub(r"[-_. /:]", "", s.lower())


def cadena_prohibida(s, prohibidas=(), hospitales=HOSPITALES):
    """Motivo (str) si la cadena lleva algo que no puede salir; None si pasa. Nunca devuelve la cadena.
    `prohibidas`: formas laxas de los ids de muestra, probe barcodes y nombre del pool (en memoria)."""
    if not isinstance(s, str) or not s:
        return None
    if RE_HEX64.match(s):
        return None                      # sha256 de un fichero de salida: opaco
    # Tiras hexadecimales largas (sha256 dentro de una línea del informe): opacas para los detectores de
    # forma, como hace puerta_n1 (_tapa_hex); si no, una accesión o una fecha «aparecen» dentro del hash.
    s = RE_HEX_LARGO.sub(lambda m: " " * len(m.group(0)), s)
    if RE_BARCODE.search(s):
        return "barcode_10x"
    if RE_UNSPLICED.search(s):
        return "unspliced"
    if RE_PROBE_BC.search(s):
        return "probe_barcode"
    for rx in RES_FECHA:
        if rx.search(s):
            return "fecha"
    low = s.lower()
    for h in hospitales:
        if h in low:
            return "hospital"
    n = _normal(s)
    es_indice = bool(RE_INDICE.match(s))
    for p in prohibidas:
        if not p:
            continue
        if p.isdigit():
            # Tira de cifras de un id: solo con frontera de cifra («x 28381», no «ENSG00000028381»). El
            # índice secuencial c00000…cNNNNN contiene POR CONSTRUCCIÓN toda cifra de 5 dígitos menor que
            # n: queda exento de las tiras de cifras (excepción declarada; las demás formas sí le aplican).
            if not es_indice and re.search(r"(?<!\d)%s(?!\d)" % re.escape(p), s):
                return "identificador_de_muestra_o_pool"
        elif p.isalpha() or len(_normal(p)) < 7:
            # Trozo corto (nombre del pool, carpeta de una ruta, probe barcode): palabra entera, con
            # separadores opcionales entre sus caracteres («A-D10», «ad10»), no subcadena: un símbolo
            # de gen que lo contenga por azar no cuenta.
            pat = r"[-_. /:]?".join(re.escape(ch) for ch in _normal(p))
            if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % pat, low):
                return "identificador_de_muestra_o_pool"
        elif p in low or _normal(p) in n:
            return "identificador_de_muestra_o_pool"
    cp = _caso_publico()
    if cp is not None and cp._RE_CODIGO_AP.search(s):
        return "codigo_ap"
    pn = _puerta()
    if pn is not None and pn.RE_CODIGO_AP_N1.search(s):
        return "codigo_ap_laxo"
    return None


_CP = None
_PN = None


def _caso_publico():
    global _CP
    if _CP is None:
        if TOOLS not in sys.path:
            sys.path.insert(0, TOOLS)
        import caso_publico
        _CP = caso_publico
    return _CP


def _puerta():
    global _PN
    if _PN is None:
        if TOOLS not in sys.path:
            sys.path.insert(0, TOOLS)
        import puerta_n1
        _PN = puerta_n1
    return _PN


# ── Constructores ─────────────────────────────────────────────────────────────────────────────
def indice_secuencial(n, desde=0):
    return ["c%05d" % i for i in range(desde, desde + n)]


def tipo_canonico(tipos):
    """`tipo` de la caché → vocabulario cerrado. Fail-closed con un tipo desconocido."""
    out = []
    for t in tipos:
        t = str(t)
        if t not in TIPOS:
            raise ValueError("tipo de la caché fuera del vocabulario (n=%d distinto)" % len(set(tipos)))
        out.append(TIPOS[t])
    return np.array(out, dtype=object)


def grupos_lesion(lesion, tipos, interes, limpio=None):
    """Vector de grupo. interes: máscara del grupo de interés (993 / 68). limpio: en el hueso, GEM
    limpio (una referencia solo lo es con GEM limpio). Asserts del encargo."""
    tipos = np.asarray(tipos).astype(str)
    interes = np.asarray(interes, bool)
    g = GRUPOS[lesion]
    assert len(tipos) == len(interes), "tipos e interés de distinta longitud"
    assert int(interes.sum()) == N_INTERES[lesion], "n del grupo de interés de %s: %d" % (lesion, int(interes.sum()))
    limpio = np.ones(len(tipos), bool) if limpio is None else np.asarray(limpio, bool)
    es_ref = np.isin(tipos, g["referencia"]) & limpio & ~interes
    assert not (np.isin(tipos, g["referencia"]) & interes).any(), "una célula de interés tipada como referencia"
    out = np.where(interes, g["interes"], np.where(es_ref, tipos, g["otras"]))
    return out.astype(object)


def construye_obs(lesiones, grupos, tipos_celular, umap):
    import pandas as pd
    lesiones = np.asarray(lesiones).astype(str)
    grupos = np.asarray(grupos).astype(str)
    tipos_celular = np.asarray(tipos_celular).astype(str)
    umap = np.asarray(umap, float)
    n = len(lesiones)
    assert umap.shape == (n, 2) and len(grupos) == n and len(tipos_celular) == n
    obs = pd.DataFrame({"lesion": lesiones, "grupo": grupos, "tipo_celular": tipos_celular,
                        "umap_1": umap[:, 0], "umap_2": umap[:, 1]}, index=indice_secuencial(n))
    comprueba_vocabulario(obs["lesion"].values, obs["grupo"].values, obs["tipo_celular"].values)
    return obs[OBS_COLS]


def comprueba_vocabulario(lesion, grupo, tipo):
    lesion, grupo, tipo = (np.asarray(x).astype(str) for x in (lesion, grupo, tipo))
    fallos = []
    if not set(lesion) <= set(LESIONES):
        fallos.append("lesion fuera de vocabulario")
    for t in set(tipo):
        if not RE_TIPO.match(t) or t not in TIPOS.values():
            fallos.append("tipo_celular fuera de vocabulario")
        if any(p in t.lower() for p in PROHIBIDAS_HIGADO):
            fallos.append("tipo_celular con tumor/malign")
    for les in set(lesion):
        if les not in GRUPOS:
            continue
        g = GRUPOS[les]
        voc = {g["interes"], g["otras"], *g["referencia"]}
        vals = set(grupo[lesion == les])
        if not vals <= voc:
            fallos.append("grupo de %s fuera de vocabulario" % les)
        if les == "higado" and any(p in v.lower() for v in vals for p in PROHIBIDAS_HIGADO):
            fallos.append("grupo del hígado con tumor/malign")
        k = int((grupo[lesion == les] == g["interes"]).sum())
        if les in N_INTERES and k != N_INTERES[les]:
            fallos.append("n del grupo de interés de %s = %d" % (les, k))
    if fallos:
        raise ValueError("; ".join(sorted(set(fallos))))


def construye_uns(suelo, referencia, veredicto, cnv_hueso, cnv_higado_pseudobulk, avisos, version=VERSION):
    """uns SOLO con las claves de la allow-list. cnv_hueso: DataFrame indexado por índice secuencial."""
    for les, d in veredicto.items():
        for g, v in d.items():
            assert v in VEREDICTOS, "veredicto fuera de vocabulario"
    uns = {"suelo_ambiental": suelo, "referencia_emparejada": referencia, "veredicto_acta": veredicto,
           "cnv_hueso": cnv_hueso, "cnv_higado_pseudobulk": cnv_higado_pseudobulk, "version": str(version),
           "avisos": [str(a) for a in avisos]}
    assert tuple(uns) == UNS_KEYS
    return uns


def veredictos_lesion(lesion, genes):
    """Veredicto cerrado para cada gen de la lista: lo del acta o sin_veredicto."""
    tabla = VEREDICTO_ACTA[lesion]
    return {g: (tabla[g][0] if g in tabla else "sin_veredicto") for g in genes}


def seleccion_lesion(genes, roles):
    """{gen: papel} solo para los genes de la lista que participan en la selección del grupo de interés.
    roles: {papel: [símbolos]} (de las reglas congeladas); un gen con dos papeles se queda con el primero."""
    out = {}
    for papel in SELECCION_ROLES:
        for g in roles.get(papel, ()):
            if g in genes and g not in out:
                out[g] = papel
    return {g: out[g] for g in genes if g in out}


# ── Validador fail-closed ─────────────────────────────────────────────────────────────────────
class ValidacionFallida(RuntimeError):
    """Nunca lleva la cadena que disparó: solo dónde y por qué."""


def _str_de(x):
    if isinstance(x, bytes):
        return x.decode("utf-8", "replace")
    return str(x)


def _cadenas_h5(path):
    """(dónde, cadena) de TODO el árbol: nombres, attrs, datasets de texto (string-array, categorías)."""
    import h5py
    out = []

    def attrs(nombre, obj):
        for k, v in obj.attrs.items():
            out.append(("%s@%s" % (nombre, k), _str_de(k)))
            if isinstance(v, (bytes, str)):
                out.append(("%s@%s" % (nombre, k), _str_de(v)))
            elif isinstance(v, np.ndarray) and v.dtype.kind in ("O", "S", "U"):
                for e in v.ravel():
                    out.append(("%s@%s" % (nombre, k), _str_de(e)))

    with h5py.File(path, "r") as f:
        attrs("/", f)

        def visita(nombre, obj):
            out.append((nombre, nombre.rsplit("/", 1)[-1]))
            attrs(nombre, obj)
            if isinstance(obj, h5py.Dataset) and obj.dtype.kind in ("O", "S", "U"):
                vals = obj.asstr()[()] if obj.dtype.kind == "O" else obj[()]
                vals = np.asarray(vals).ravel()
                for v in vals:
                    out.append((nombre, _str_de(v)))
        f.visititems(visita)
    return out


RE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*$")     # un símbolo, un id Ensembl, una clave: sin espacios


def _puerta_lote(cadenas, donde, ignorar_capas=()):
    """Todas las cadenas por puerta_n1 (únicas, en lotes separados por « | »); si un lote cierra, se
    repasa una a una para localizar la posición sin revelar la cadena. `ignorar_capas` (excepción
    declarada del h5ad): capas de la puerta que NO cierran cuando la cadena es un token sin espacios
    (símbolo de gen, Ensembl, clave); en texto libre cierran todas. Devuelve (n únicas, n toleradas)."""
    pn = _puerta()
    unicas = sorted({c for c in cadenas if isinstance(c, str) and c})
    toleradas = 0
    for i in range(0, len(unicas), 400):
        lote = unicas[i:i + 400]
        try:
            pn.revisar_texto(" | ".join(lote), donde)
        except pn.PuertaCerrada:
            for j, c in enumerate(lote):
                todos = pn.motivos(c)
                if not todos:
                    continue
                graves = [m for m in todos if not (m[0] in ignorar_capas and RE_TOKEN.match(c))]
                if graves:
                    raise ValidacionFallida("%s: puerta_n1 cierra la cadena única nº %d (%s)"
                                            % (donde, i + j, ", ".join(sorted({m[0] for m in graves}))))
                toleradas += 1
    return len(unicas), toleradas


def valida_h5ad(path, prohibidas=()):
    """Recorre TODO el árbol HDF5. Devuelve un resumen (n cadenas revisadas, n células, etc.)."""
    import h5py
    with h5py.File(path, "r") as f:
        if _str_de(f.attrs.get("encoding-type", "")) != "anndata":
            raise ValidacionFallida("h5ad: raíz sin encoding anndata")
        if "raw" in f:
            raise ValidacionFallida("h5ad: lleva raw")
        for g in ("layers", "obsm", "obsp", "varm", "varp"):
            if g in f and len(f[g].keys()) > 0:
                raise ValidacionFallida("h5ad: %s no está vacío" % g)
        obs = f["obs"]
        cols = sorted(k for k in obs.keys() if k != "_index")
        if cols != sorted(OBS_COLS):
            raise ValidacionFallida("h5ad: columnas de obs fuera de la allow-list (%d columnas)" % len(cols))
        orden = [_str_de(x) for x in np.asarray(obs.attrs.get("column-order", [])).ravel()]
        if orden != OBS_COLS:
            raise ValidacionFallida("h5ad: column-order de obs distinto del contrato")
        var = f["var"]
        vcols = sorted(k for k in var.keys() if k != "_index")
        if vcols != sorted(VAR_COLS):
            raise ValidacionFallida("h5ad: columnas de var fuera de la allow-list")
        uns = f["uns"] if "uns" in f else {}
        extra = set(uns.keys()) - set(UNS_KEYS)
        if extra:
            raise ValidacionFallida("h5ad: %d claves de uns fuera de la allow-list" % len(extra))
        X = f["X"]
        dt = X["data"].dtype if isinstance(X, h5py.Group) else X.dtype
        if dt.kind not in ("i", "u"):
            raise ValidacionFallida("h5ad: X no es de cuentas enteras")
        if isinstance(X, h5py.Group) and X["data"].shape[0] and int(X["data"][:1000].min()) < 0:
            raise ValidacionFallida("h5ad: X con negativos")
        idx = obs["_index"].asstr()[()]
        if list(idx) != indice_secuencial(len(idx)):
            raise ValidacionFallida("h5ad: el índice de obs no es el secuencial c00000…")

        def columna(nombre):
            d = obs[nombre]
            if isinstance(d, h5py.Group):           # categorical
                cats = d["categories"].asstr()[()]
                return np.asarray(cats)[d["codes"][()]]
            return d.asstr()[()]
        lesion, grupo, tipo = columna("lesion"), columna("grupo"), columna("tipo_celular")
        try:
            comprueba_vocabulario(lesion, grupo, tipo)
        except ValueError as e:
            raise ValidacionFallida("h5ad: %s" % e)
        if "cnv_hueso" in uns:
            ci = uns["cnv_hueso"]["_index"].asstr()[()]
            if not set(ci) <= set(idx[np.asarray(lesion) == "hueso"]):
                raise ValidacionFallida("h5ad: cnv_hueso indexa células que no son del hueso")
            if set(uns["cnv_hueso"].keys()) - {"_index", *CNV_VENTANAS}:
                raise ValidacionFallida("h5ad: cnv_hueso con columnas fuera del contrato")
        n_cel = len(idx)
    cadenas = _cadenas_h5(path)
    for donde, c in cadenas:
        m = cadena_prohibida(c, prohibidas)
        if m:
            raise ValidacionFallida("h5ad %s: %s" % (donde, m))
        if RE_TITULAR_H5.search(c):
            raise ValidacionFallida("h5ad %s: nombre de la titular" % donde)
    # Excepción declarada: un símbolo de gen (vocabulario público, HGNC) a distancia <= 2 de una parte del
    # nombre de la titular no es su nombre. Solo la capa difusa `casi_titular`, solo en tokens sin espacios;
    # el diccionario (nombre exacto), canarios, fechas y accesiones cierran igual.
    n_unicas, toleradas = _puerta_lote([c for _, c in cadenas], "h5ad", ignorar_capas=("casi_titular",))
    return {"n_celulas": int(n_cel), "n_cadenas": len(cadenas), "n_cadenas_unicas_puerta": n_unicas,
            "tokens_casi_titular_tolerados_como_vocabulario": toleradas}


def _cadenas_json(obj, ruta="$"):
    if isinstance(obj, str):
        yield ruta, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield ruta + ".<clave>", str(k)
            yield from _cadenas_json(v, "%s.%s" % (ruta, k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _cadenas_json(v, "%s[%d]" % (ruta, i))


def fecha_ok_valida(v):
    """AAAA-MM-DD y fecha de calendario real (ni «2026-13-45» ni texto pegado)."""
    import datetime
    if not isinstance(v, str) or not RE_FECHA_OK.match(v):
        return False
    try:
        datetime.date.fromisoformat(v)
    except ValueError:
        return False
    return True


def valida_json_generico(path, prohibidas=(), exento_fecha_ok=False, exento_fecha_decision=False):
    """Patrones propios sobre TODAS las claves y cadenas, y después puerta_n1 sobre el texto. Sin
    excepciones salvo una, declarada y acotada (`exento_fecha_ok`, solo el manifiesto): el VALOR de la
    clave de primer nivel `fecha_ok`, y solo si es exactamente AAAA-MM-DD de calendario, no cuenta como
    fecha prohibida y se enmascara en la copia que pasa por puerta_n1. Cualquier otra fecha, en cualquier
    otra clave o con cualquier otra forma, cierra."""
    with open(path, encoding="utf-8") as fh:
        texto = fh.read()
    obj = json.loads(texto)
    exento = bool(exento_fecha_ok and isinstance(obj, dict) and "fecha_ok" in obj and fecha_ok_valida(obj["fecha_ok"]))
    # segunda excepción, igual de acotada (manifiesto PUBLICADO de la web, 18-oct): el valor de
    # matriz_completa.fecha_decision, solo si es AAAA-MM-DD de calendario.
    mc = obj.get("matriz_completa") if isinstance(obj, dict) else None
    exento_dec = bool(exento_fecha_decision and isinstance(mc, dict) and fecha_ok_valida(mc.get("fecha_decision")))
    cadenas = list(_cadenas_json(obj))
    for donde, c in cadenas:
        if (exento and donde == "$.fecha_ok") or (exento_dec and donde == "$.matriz_completa.fecha_decision"):
            continue
        m = cadena_prohibida(c, prohibidas)
        if m:
            raise ValidacionFallida("%s %s: %s" % (os.path.basename(path), donde, m))
    pn = _puerta()
    if exento or exento_dec:
        copia = dict(obj)
        if exento:
            copia["fecha_ok"] = "fecha_ok_enmascarada"
        if exento_dec:
            copia["matriz_completa"] = {**mc, "fecha_decision": "fecha_decision_enmascarada"}
        texto_puerta = json.dumps(copia, ensure_ascii=False)
    else:
        texto_puerta = texto
    try:
        pn.revisar_json(texto_puerta, os.path.basename(path))
    except pn.PuertaCerrada as e:
        raise ValidacionFallida("%s: puerta_n1 (%s)" % (os.path.basename(path), str(e).split(":")[-1].strip()))
    return obj, len(cadenas)


def _es_frac(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x <= 1


def _es_ent(x, minimo=0):
    return isinstance(x, int) and not isinstance(x, bool) and x >= minimo


def valida_web_lesion(path, prohibidas=(), modo="todas"):
    obj, n_cad = valida_json_generico(path, prohibidas)
    b = os.path.basename(path)
    if tuple(obj.keys()) != WEB_KEYS:
        raise ValidacionFallida("%s: claves distintas del contrato" % b)
    les = obj["lesion"]
    if les not in GRUPOS or obj["version"] != 1:
        raise ValidacionFallida("%s: lesión o versión fuera del contrato" % b)
    g = GRUPOS[les]
    ids = [x["id"] for x in obj["grupos"]]
    roles = {x["id"]: x["rol"] for x in obj["grupos"]}
    voc = [g["interes"], *g["referencia"], g["otras"]]
    if not set(ids) <= set(voc) or len(set(ids)) != len(ids):
        raise ValidacionFallida("%s: grupos fuera de vocabulario o repetidos" % b)
    if roles.get(g["interes"]) != "interes" or roles.get(g["otras"], "otras") != "otras" \
            or any(roles[i] != "referencia" for i in ids if i in g["referencia"]):
        raise ValidacionFallida("%s: rol de grupo fuera del contrato" % b)
    n = obj["n_celulas"]
    if sum(x["n"] for x in obj["grupos"]) != n:
        raise ValidacionFallida("%s: la suma de n por grupo no es n_celulas" % b)
    _valida_qc(obj["qc"], n, b)
    agregados = {"marcadores": _valida_marcadores(obj["marcadores"], ids, b),
                 "perfil_bins": _valida_perfil(obj["perfil_bins"], b),
                 "adn_por_grupo": _valida_adn_grupos(obj["adn_por_grupo"], [x["id"] for x in obj["grupos"] if x["n"] > 0], modo, b)}
    k = [x["n"] for x in obj["grupos"] if x["id"] == g["interes"]]
    if not k or k[0] != N_INTERES[les]:
        raise ValidacionFallida("%s: n del grupo de interés distinto de %d" % (b, N_INTERES[les]))
    for t in obj["tipos"]:
        if not RE_TIPO.match(t) or t not in TIPOS.values() or any(p in t.lower() for p in PROHIBIDAS_HIGADO):
            raise ValidacionFallida("%s: tipo fuera de vocabulario" % b)
    if len(set(obj["tipos"])) != len(obj["tipos"]):
        raise ValidacionFallida("%s: tipos repetidos" % b)
    if les == "higado" and any(p in i.lower() for i in ids for p in PROHIBIDAS_HIGADO):
        raise ValidacionFallida("%s: grupo del hígado con tumor/malign" % b)
    c = obj["celulas"]
    if len(c["umap"]) != 2 * n or len(c["grupo"]) != n or len(c["tipo"]) != n:
        raise ValidacionFallida("%s: celulas con longitudes distintas de n" % b)
    if min(c["grupo"]) < 0 or max(c["grupo"]) >= len(ids) or min(c["tipo"]) < 0 or max(c["tipo"]) >= len(obj["tipos"]):
        raise ValidacionFallida("%s: índice de grupo o tipo fuera de rango" % b)
    conteo = np.bincount(np.asarray(c["grupo"]), minlength=len(ids))
    if any(int(conteo[i]) != x["n"] for i, x in enumerate(obj["grupos"])):
        raise ValidacionFallida("%s: n por grupo no cuadra con celulas.grupo" % b)
    if max(abs(v) for v in c["umap"]) > 10.0001:
        raise ValidacionFallida("%s: umap fuera de [-10, 10]" % b)
    genes = obj["genes"]
    if len(set(genes)) != len(genes):
        raise ValidacionFallida("%s: genes repetidos" % b)
    for clave in ("expr", "suelo", "referencia", "veredicto"):
        if list(obj[clave].keys()) != genes:
            raise ValidacionFallida("%s: %s no cubre exactamente la lista de genes" % (b, clave))
    for gname, e in obj["expr"].items():
        i, v = e["i"], e["v"]
        if len(i) != len(v) or (i and (i[0] < 0 or i[-1] >= n)) or any(b_ <= a for a, b_ in zip(i, i[1:])) \
                or any(x <= 0 for x in v):
            raise ValidacionFallida("%s: expr de un gen mal formada" % b)
    for gname, d in obj["suelo"].items():
        if set(d.keys()) != set(ids) or any(not (0 <= x <= 1) for x in d.values()):
            raise ValidacionFallida("%s: suelo sin todos los grupos o fuera de [0,1]" % b)
    if any(not (0 <= x <= 1) for x in obj["referencia"].values()):
        raise ValidacionFallida("%s: referencia fuera de [0,1]" % b)
    if not set(obj["veredicto"].values()) <= set(VEREDICTOS):
        raise ValidacionFallida("%s: veredicto fuera de vocabulario" % b)
    # sondas por gen: [unión exón-exón, total] (B2: la tabla de genes las enseña)
    sondas = obj["sondas"]
    if list(sondas.keys()) != genes or any(
            not (isinstance(v, list) and len(v) == 2 and _es_ent(v[0]) and _es_ent(v[1], 1) and v[0] <= v[1])
            for v in sondas.values()):
        raise ValidacionFallida("%s: sondas fuera del contrato ([unión, total] por gen, unión <= total)" % b)
    # selección del grupo de interés (circularidad, B1): solo genes de la lista y papeles del vocabulario
    sel = obj["seleccion"]
    if not isinstance(sel, dict) or not set(sel) <= set(genes) or not set(sel.values()) <= set(SELECCION_ROLES):
        raise ValidacionFallida("%s: seleccion fuera del contrato" % b)
    if les == "hueso" and set(sel.values()) - {"identidad"}:
        raise ValidacionFallida("%s: el hueso solo lleva papel «identidad» en la selección" % b)
    # LOO (cifra sin la circularidad): solo de genes que participan en la selección, y solo en el hígado
    loo = obj["loo"]
    if not isinstance(loo, dict) or not set(loo) <= set(sel) or (les == "hueso" and loo):
        raise ValidacionFallida("%s: loo fuera del contrato (solo hígado, solo genes de la selección)" % b)
    for v in loo.values():
        if not isinstance(v, dict) or tuple(v.keys()) != LOO_KEYS or not _es_ent(v["n"], 1) \
                or not all(v[k] is None or _es_frac(v[k]) for k in ("frac", "ref")) \
                or not (v["cp10k_pseudobulk"] is None or (isinstance(v["cp10k_pseudobulk"], (int, float)) and v["cp10k_pseudobulk"] >= 0)):
            raise ValidacionFallida("%s: una entrada de loo fuera del contrato" % b)
    # ADN genómico (B2): números de la muestra; en el modo spliced no aplica (null)
    adn = obj["adn"]
    if modo == "spliced":
        if adn is not None:
            raise ValidacionFallida("%s: adn debe ser null en el modo spliced" % b)
    elif not isinstance(adn, dict) or tuple(adn.keys()) != ADN_KEYS or not _es_ent(adn["sondas_dentro_de_exon"], 1) \
            or not _es_ent(adn["sondas_panel"], 1) or adn["sondas_dentro_de_exon"] > adn["sondas_panel"] \
            or not all(_es_frac(adn[k]) for k in ADN_KEYS[2:]):
        raise ValidacionFallida("%s: adn fuera del contrato" % b)
    cnv = obj["cnv"]
    if les == "hueso":
        if cnv.get("ventanas") != CNV_VENTANAS or cnv.get("metodo") != CNV_METODOS[modo] \
                or len(cnv.get("valores", [])) != 2 or any(len(v) != n for v in cnv["valores"]):
            raise ValidacionFallida("%s: cnv del hueso fuera del contrato" % b)
    else:
        pb = cnv.get("pseudobulk", {})
        if set(cnv.keys()) != {"pseudobulk"} or pb.get("ventana") != "8p11" \
                or not isinstance(pb.get("percentil_genoma"), int) or not (0 <= pb["percentil_genoma"] <= 100):
            raise ValidacionFallida("%s: cnv del hígado fuera del contrato" % b)
    return {"n_celulas": n, "n_genes": len(genes), "n_cadenas": n_cad, "kb": round(os.path.getsize(path) / 1024, 1),
            "agregados": agregados}


def sha256_fichero(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


CLAVE_OK_TITULAR = "ok_titular"      # contrato de la web pública (C1: sin el nombre, sin excepción en puerta_n1)


def valida_manifiesto(path, prohibidas=()):
    """Dos estados coherentes y nada más (A4): el del export (sin_verificar, ok_titular false, SIN fecha_ok)
    y el publicado (verificado, ok_titular true, fecha_ok AAAA-MM-DD de calendario). Mezclas: cierra."""
    obj, _ = valida_json_generico(path, prohibidas, exento_fecha_ok=True)
    claves = set(obj.keys())
    if claves not in (set(MANIFIESTO_KEYS), set(MANIFIESTO_KEYS) | {"fecha_ok"}) or obj["version"] != 1 \
            or obj["lesiones"] != list(LESIONES):
        raise ValidacionFallida("manifiesto: cabecera fuera del contrato")
    estado = {"sello": obj["sello"], CLAVE_OK_TITULAR: obj[CLAVE_OK_TITULAR]}
    if estado == ESTADO_EXPORT and obj[CLAVE_OK_TITULAR] is False and "fecha_ok" not in obj:
        fase = "export"
    elif estado == ESTADO_PUBLICADO and obj[CLAVE_OK_TITULAR] is True and fecha_ok_valida(obj.get("fecha_ok")):
        fase = "publicado"
    else:
        raise ValidacionFallida("manifiesto: sello, %s y fecha_ok no forman un estado admitido" % CLAVE_OK_TITULAR)
    if obj["modo_sondas"] not in ETIQUETA_MODO.values():
        raise ValidacionFallida("manifiesto: modo_sondas fuera de vocabulario")
    rutas = [f["ruta"] for f in obj["ficheros"]]
    if rutas != ["%s.json" % l for l in LESIONES]:
        raise ValidacionFallida("manifiesto: ficheros distintos del contrato")
    for f in obj["ficheros"]:
        real = os.path.join(os.path.dirname(path), f["ruta"])
        if not os.path.isfile(real) or sha256_fichero(real) != f["sha256"]:
            raise ValidacionFallida("manifiesto: sha256 de %s no cuadra" % f["ruta"])
    return {"n_ficheros": len(rutas), "modo_sondas": obj["modo_sondas"], "estado": fase,
            "excepcion_puerta_n1": "ninguna en el estado del export; en el publicado, solo el valor de fecha_ok "
                                   "(AAAA-MM-DD de calendario) se enmascara en la copia que pasa por puerta_n1"}


def valida_texto(path, prohibidas=()):
    with open(path, encoding="utf-8") as fh:
        texto = fh.read()
    for i, linea in enumerate(texto.splitlines()):
        m = cadena_prohibida(linea, prohibidas)
        if m:
            raise ValidacionFallida("%s línea %d: %s" % (os.path.basename(path), i + 1, m))
    pn = _puerta()
    try:
        pn.revisar_texto(texto, os.path.basename(path))
    except pn.PuertaCerrada as e:
        raise ValidacionFallida("%s: puerta_n1 (%s)" % (os.path.basename(path), str(e).split(":")[-1].strip()))
    return {"lineas": texto.count("\n") + 1}


def valida_csv(path, prohibidas=()):
    with open(path, encoding="utf-8") as fh:
        texto = fh.read()
    for i, linea in enumerate(texto.splitlines()):
        m = cadena_prohibida(linea, prohibidas)
        if m:
            raise ValidacionFallida("%s línea %d: %s" % (os.path.basename(path), i + 1, m))
    pn = _puerta()
    try:
        pn.revisar_csv(texto, os.path.basename(path))
    except pn.PuertaCerrada as e:
        raise ValidacionFallida("%s: puerta_n1 (%s)" % (os.path.basename(path), str(e).split(":")[-1].strip()))
    return {"lineas": texto.count("\n") + 1}


# ── Zarr por lesión («Loupe público», plan aprobado por la titular el 17-oct-2026) ──────────────────────
# TODOS los genes, legibles gen a gen desde la web. Vive FUERA de web/: publicarlo es un paso consciente aparte
# (condiciones del laboratorio + decisión de la titular), no algo que viaje con la carpeta de la página.
# U1 (verificado en ObsFeatureMatrixAnndataLoader.js de @vitessce/zarr 4.1.0): Vitessce lee una csc_matrix
# columna a columna por indptr; con csr_matrix recorre `indices` y `data` enteros en cada selección.
ZARR_DIR = "zarr"
# 18-oct (lo que un ingeniero necesita para no malinterpretar, inventario del comité médico): UMI y nº de genes
# por célula, fracción de ADN genómico POR GRUPO (fórmula del tribunal, _adn.py, que es un cociente de medias:
# célula a célula da >= 1 en células con pocas UMI; vacía en el modo spliced: no aplica), sondas por gen,
# marcadores por grupo del contrato y el perfil por bins de 10 Mb.
ZARR_OBS_COLS = ["grupo", "tipo_celular", "umi", "n_genes"]
ZARR_OBSM = ["X_umap"]
ZARR_UNS_KEYS = ("version", "lesion", "modo_sondas", "genes_sin_sonda", "marcadores", "perfil_bins", "adn_por_grupo")
ZARR_VAR_FIJAS = ["ensembl", "n_sondas", "n_sondas_union", "referencia_emparejada"]
MARCADORES_KEYS = ("gen", "logfc", "fdr", "frac_grupo", "frac_resto", "suelo")
MARCADORES_N = 50                   # por grupo, ordenados por el estadístico de Wilcoxon
PERFIL_KEYS = ("cromosoma", "inicio_mb", "log2_rel")
RE_CROMOSOMA = re.compile(r"^chr([1-9]|1\d|2[0-2])$")
PREFIJO_SUELO = "suelo__"
ZARR_CHUNK = 65536      # elementos por trozo de X/indices y X/data: un gen cae en 1-2 trozos


def _suelo_todos():
    if AQUI not in sys.path:
        sys.path.insert(0, AQUI)
    import _suelo_todos as ST
    return ST


def grupos_zarr(lesion, grupo):
    """Columnas de suelo, en el orden de g_ids de exporta.py: interés, referencias presentes, otras."""
    g = GRUPOS[lesion]
    presentes = set(np.asarray(grupo).astype(str))
    return [g["interes"]] + [r for r in g["referencia"] if r in presentes] + [g["otras"]]


def sig3(x):
    """«%.3g» vectorizado (las cifras de expresión de la web van a 3 cifras significativas)."""
    x = np.asarray(x, float)
    out = np.zeros_like(x)
    nz = x != 0
    f = 10.0 ** (2 - np.floor(np.log10(np.abs(x[nz]))))
    out[nz] = np.round(x[nz] * f) / f
    return out


def escribe_zarr_lesion(path, lesion, X, umi, simbolos, ensembl, grupo, tipo_c, umap, indices, suelo_todos,
                        version, modo, adn_por_grupo, n_sondas, n_sondas_union, marcadores, perfil):
    """AnnData-Zarr de UNA lesión: X = CP10k a 3 cifras (como `expr` de la web) en csc_matrix con trozos de
    ZARR_CHUNK; obs = grupo, tipo_celular, umi, n_genes con el índice secuencial del h5ad;
    obsm = X_umap; var = ensembl, sondas, referencia emparejada y suelo por grupo de TODOS los genes
    (`suelo_todos` = salida de _suelo_todos.calcula); uns = version, lesion, modo_sondas, genes_sin_sonda,
    marcadores (grupo → MARCADORES_KEYS) y perfil_bins (PERFIL_KEYS)."""
    import anndata as an
    import pandas as pd
    import scipy.sparse as sp
    n_genes = np.asarray((sp.csr_matrix(X) > 0).sum(axis=1)).ravel().astype(np.int32)
    X = sp.csr_matrix(X, dtype=np.float64)
    X = (sp.diags(1e4 / np.asarray(umi, float)) @ X).tocsr()
    X.data = sig3(X.data)
    X = X.astype(np.float32).tocsc()
    X.eliminate_zeros()
    X.sort_indices()
    grupos = grupos_zarr(lesion, grupo)
    if list(suelo_todos["grupos"]) != grupos:
        raise ValueError("los grupos del suelo no siguen el orden del contrato")
    cols = {"ensembl": np.asarray(ensembl).astype(str), "n_sondas": np.asarray(n_sondas, np.int32),
            "n_sondas_union": np.asarray(n_sondas_union, np.int32),
            "referencia_emparejada": np.asarray(suelo_todos["referencia"], float)}
    for k, gid in enumerate(grupos):
        cols[PREFIJO_SUELO + gid] = np.asarray(suelo_todos["suelo"], float)[:, k]
    var = pd.DataFrame(cols, index=pd.Index(np.asarray(simbolos).astype(str)))
    var.index = an.utils.make_index_unique(var.index)
    obs = pd.DataFrame({"grupo": pd.Categorical(np.asarray(grupo).astype(str)),
                        "tipo_celular": pd.Categorical(np.asarray(tipo_c).astype(str)),
                        "umi": np.asarray(umi, np.int64), "n_genes": n_genes},
                       index=pd.Index([str(i) for i in indices]))
    uns = {"version": str(version), "lesion": lesion, "modo_sondas": ETIQUETA_MODO[modo],
           "genes_sin_sonda": np.array([str(var.index[i]) for i in suelo_todos["excluidos"]], dtype=object),
           "marcadores": {g: {"gen": np.array(m["gen"], dtype=object),
                              **{k: np.asarray(m[k], np.float64) for k in MARCADORES_KEYS[1:]}}
                          for g, m in marcadores.items()},
           "perfil_bins": {"cromosoma": np.array(perfil["cromosoma"], dtype=object),
                           "inicio_mb": np.asarray(perfil["inicio_mb"], np.float64),
                           "log2_rel": np.asarray(perfil["log2_rel"], np.float64)},
           "adn_por_grupo": {g: float(x) for g, x in adn_por_grupo.items()}}
    a = an.AnnData(X=X, obs=obs, var=var, uns=uns, obsm={"X_umap": np.asarray(umap, np.float32)})
    with an.settings.override(zarr_write_format=2):   # v2 fijado: anndata pasará a v3 por defecto; U1 se verificó con v2
        a.write_zarr(path)
    fija_X_zarr(path, X)
    return {"n_celulas": int(a.n_obs), "n_genes": int(a.n_vars), "nnz_millones": round(X.nnz / 1e6, 3)}


def fija_X_zarr(path, X):
    """Reescribe X del Zarr con trozos de ZARR_CHUNK (write_zarr no los deja elegir en una matriz dispersa) y
    vuelve a consolidar los metadatos (write_zarr los consolida y write_elem no edita un almacén consolidado)."""
    import zarr
    from anndata.io import write_elem
    zm = os.path.join(path, ".zmetadata")
    if os.path.exists(zm):
        os.remove(zm)
    g = zarr.open_group(path, mode="r+", use_consolidated=False)
    del g["X"]
    write_elem(g, "X", X, dataset_kwargs={"chunks": (ZARR_CHUNK,)})
    zarr.consolidate_metadata(path)


def _cadenas_zarr(path):
    """(dónde, cadena) de TODO el árbol Zarr: nombres de fichero y carpeta, todo el JSON de metadatos
    (.zattrs/.zarray/.zgroup/zarr.json) y los arrays de texto."""
    import zarr
    out = []
    for d, subdirs, files in os.walk(path):
        rel = os.path.relpath(d, path)
        for nombre in subdirs + files:
            out.append((os.path.join(rel, nombre), nombre))
        for f in files:
            if f in (".zattrs", ".zarray", ".zgroup", ".zmetadata", "zarr.json"):
                with open(os.path.join(d, f), encoding="utf-8") as fh:
                    out.extend(_cadenas_json(json.load(fh), os.path.join(rel, f)))
    for nombre, obj in zarr.open_group(path, mode="r").members(max_depth=None):
        if isinstance(obj, zarr.Array) and obj.dtype.kind in ("O", "S", "U", "T"):
            out.extend((nombre, _str_de(v)) for v in np.asarray(obj[...]).ravel())
    return out


def valida_zarr_lesion(path, lesion, prohibidas=()):
    """Fail-closed sobre el Zarr de una lesión: estructura, X en csc, vocabulario, suelo y referencia en TODO
    gen con sonda, y todas las cadenas del árbol por los patrones propios y puerta_n1."""
    import anndata as an
    import zarr
    q = "zarr %s" % lesion
    if not os.path.isdir(path):
        raise ValidacionFallida("%s: no existe" % q)
    if not os.path.isfile(os.path.join(path, ".zgroup")) or os.path.exists(os.path.join(path, "zarr.json")):
        raise ValidacionFallida("%s: no es Zarr v2 (el formato con el que se verificó la lectura gen a gen)" % q)
    g = zarr.open_group(path, mode="r")
    if _str_de(g.attrs.get("encoding-type", "")) != "anndata":
        raise ValidacionFallida("%s: raíz sin encoding anndata" % q)
    # anndata 0.12 escribe raw como un array «null» y los demás como dicts vacíos: eso y nada más
    if "raw" in g and not (isinstance(g["raw"], zarr.Array) and _str_de(g["raw"].attrs.get("encoding-type", "")) == "null"):
        raise ValidacionFallida("%s: lleva raw" % q)
    for k in ("layers", "obsp", "varm", "varp"):
        if k in g and (not isinstance(g[k], zarr.Group) or len(list(g[k].keys())) > 0):
            raise ValidacionFallida("%s: %s no está vacío" % (q, k))
    if "obsm" not in g or sorted(g["obsm"].keys()) != ZARR_OBSM:
        raise ValidacionFallida("%s: obsm fuera del contrato" % q)
    X = g["X"]
    if not isinstance(X, zarr.Group) or _str_de(X.attrs.get("encoding-type", "")) != "csc_matrix":
        raise ValidacionFallida("%s: X no es csc_matrix (la web tendría que recorrer la matriz entera por gen)" % q)
    for k in ("indices", "data"):
        if X[k].chunks[0] > ZARR_CHUNK:
            raise ValidacionFallida("%s: trozos de X/%s mayores que %d" % (q, k, ZARR_CHUNK))
    data = np.asarray(X["data"][...])
    if data.dtype.kind != "f" or (data.size and (not np.isfinite(data).all() or data.min() < 0)):
        raise ValidacionFallida("%s: X con valores no finitos o negativos" % q)
    # Privacidad primero: si hay un barcode, un id o el nombre, ese es el motivo que sale, no otro posterior.
    cadenas = _cadenas_zarr(path)
    for donde, c in cadenas:
        m = cadena_prohibida(c, prohibidas)
        if m:
            raise ValidacionFallida("%s %s: %s" % (q, donde, m))
        if RE_TITULAR_H5.search(c):
            raise ValidacionFallida("%s %s: nombre de la titular" % (q, donde))
    n_unicas, toleradas = _puerta_lote([c for _, c in cadenas], q, ignorar_capas=("casi_titular",))
    a = an.read_zarr(path)
    if list(a.obs.columns) != ZARR_OBS_COLS:
        raise ValidacionFallida("%s: columnas de obs fuera del contrato" % q)
    idx = [str(i) for i in a.obs_names]
    if not all(RE_INDICE.match(i) for i in idx) or len(set(idx)) != len(idx) or idx != sorted(idx):
        raise ValidacionFallida("%s: el índice de obs no es el secuencial c00000…" % q)
    if set(a.uns.keys()) != set(ZARR_UNS_KEYS) or a.uns["lesion"] != lesion \
            or a.uns["modo_sondas"] not in ETIQUETA_MODO.values():
        raise ValidacionFallida("%s: uns fuera del contrato" % q)
    grupo, tipo = a.obs["grupo"].astype(str).values, a.obs["tipo_celular"].astype(str).values
    try:
        comprueba_vocabulario(np.full(len(idx), lesion), grupo, tipo)
    except ValueError as e:
        raise ValidacionFallida("%s: %s" % (q, e))
    grupos = grupos_zarr(lesion, grupo)
    if list(a.var.columns) != ZARR_VAR_FIJAS + [PREFIJO_SUELO + x for x in grupos]:
        raise ValidacionFallida("%s: columnas de var fuera del contrato" % q)
    if a.var_names.duplicated().any():
        raise ValidacionFallida("%s: símbolos repetidos en var" % q)
    sin = {str(s) for s in np.asarray(a.uns["genes_sin_sonda"]).ravel()}
    excl = [int(i) for i in np.where(np.isin(a.var_names.astype(str), list(sin)))[0]]
    if len(excl) != len(sin):
        raise ValidacionFallida("%s: genes_sin_sonda que no están en var" % q)
    res = {"grupos": grupos, "suelo": a.var[[PREFIJO_SUELO + x for x in grupos]].to_numpy(float),
           "referencia": a.var["referencia_emparejada"].to_numpy(float), "excluidos": excl}
    try:
        _suelo_todos().valida_completo(res, a.n_vars)
    except ValueError as e:
        raise ValidacionFallida("%s: %s" % (q, e))
    agregados = _valida_extra_zarr(a, q, grupos, sin)
    um = np.asarray(a.obsm["X_umap"])
    if um.shape != (a.n_obs, 2) or not np.isfinite(um).all():
        raise ValidacionFallida("%s: X_umap fuera del contrato" % q)
    adn_int = a.uns["adn_por_grupo"].get(grupos[0])
    return {"n_celulas": int(a.n_obs), "n_genes": int(a.n_vars), "n_genes_sin_sonda": len(excl), "grupos": grupos,
            "adn_interes": None if adn_int is None else float(adn_int), "agregados": agregados,
            "indices": idx, "n_cadenas": len(cadenas), "n_cadenas_unicas_puerta": n_unicas,
            "tokens_casi_titular_tolerados_como_vocabulario": toleradas}


def _valida_extra_zarr(a, q, grupos, sin):
    """Campos del 18-oct: UMI, nº de genes y ADN por célula; sondas por gen; marcadores; perfil por bins."""
    import scipy.sparse as sp
    modo = {v: k for k, v in ETIQUETA_MODO.items()}[a.uns["modo_sondas"]]
    o, v = a.obs, a.var
    if o["umi"].dtype.kind not in ("i", "u") or (o["umi"].values < 1).any():
        raise ValidacionFallida("%s: umi por célula fuera del contrato" % q)
    if o["n_genes"].dtype.kind not in ("i", "u") or \
            not np.array_equal(o["n_genes"].values, np.asarray((sp.csr_matrix(a.X) > 0).sum(axis=1)).ravel()):
        raise ValidacionFallida("%s: n_genes no es el nº de genes detectados de X" % q)
    con_celulas = [g for g in grupos if (o["grupo"].astype(str).values == g).any()]
    adn = _valida_adn_grupos(a.uns["adn_por_grupo"], con_celulas, modo, q)
    ns, nu = v["n_sondas"].values, v["n_sondas_union"].values
    if ns.dtype.kind not in ("i", "u") or nu.dtype.kind not in ("i", "u") or (nu < 0).any() or (nu > ns).any():
        raise ValidacionFallida("%s: sondas por gen fuera del contrato" % q)
    sin_sonda = set(v.index[(ns if modo == "todas" else nu) == 0].astype(str))
    if sin_sonda != sin:
        raise ValidacionFallida("%s: genes_sin_sonda no son los genes con 0 sondas del modo" % q)
    mk = _valida_marcadores(a.uns["marcadores"], grupos, q, validos=set(v.index.astype(str)) - sin)
    for gid, (genes, cols) in mk.items():
        if genes and not np.array_equal(cols["suelo"], v.loc[genes, PREFIJO_SUELO + gid].to_numpy(float)):
            raise ValidacionFallida("%s: marcadores de %s: el suelo no es el de var" % (q, gid))
    pb = _valida_perfil(a.uns["perfil_bins"], q)
    return {"marcadores": mk, "perfil_bins": pb, "adn_por_grupo": adn}


def _valida_adn_grupos(adn, con_celulas, modo, q):
    """Fracción de ADN por grupo: en el modo todas, un valor en [0, 1) por grupo con células; en spliced, vacío."""
    if not hasattr(adn, "items"):
        raise ValidacionFallida("%s: adn_por_grupo fuera del contrato" % q)
    adn = {str(k): float(x) for k, x in adn.items()}
    if modo == "todas" and (sorted(adn) != sorted(con_celulas) or not all(np.isfinite(x) and 0 <= x < 1 for x in adn.values())):
        raise ValidacionFallida("%s: adn_por_grupo fuera del contrato (un valor en [0, 1) por grupo con células)" % q)
    if modo == "spliced" and adn:
        raise ValidacionFallida("%s: adn_por_grupo debe ir vacío en el modo spliced (no aplica)" % q)
    return adn


def _valida_marcadores(mk, grupos, q, validos=None):
    """Marcadores por grupo del contrato: {grupo: (genes, {columna: array})}. `validos`: genes admitidos."""
    if not hasattr(mk, "items") or not set(mk) <= set(grupos):
        raise ValidacionFallida("%s: marcadores de un grupo fuera del contrato" % q)
    out = {}
    for gid, m in mk.items():
        if not hasattr(m, "keys") or set(m) != set(MARCADORES_KEYS):
            raise ValidacionFallida("%s: marcadores de %s fuera del contrato" % (q, gid))
        genes = [str(x) for x in np.asarray(m["gen"], dtype=object).ravel()]
        cols = {k: np.asarray(m[k], float).ravel() for k in MARCADORES_KEYS[1:]}
        if len(genes) > MARCADORES_N or len(set(genes)) != len(genes) or not all(RE_TOKEN.match(g) for g in genes) \
                or (validos is not None and not set(genes) <= validos) or any(len(c) != len(genes) for c in cols.values()):
            raise ValidacionFallida("%s: marcadores de %s: genes o longitudes fuera del contrato" % (q, gid))
        if not (np.isfinite(cols["logfc"]).all() and all(((c >= 0) & (c <= 1)).all() for k, c in cols.items() if k != "logfc")):
            raise ValidacionFallida("%s: marcadores de %s: valores fuera de rango" % (q, gid))
        out[str(gid)] = (genes, cols)
    return out


def _valida_perfil(pb, q):
    """Perfil por bins de 10 Mb: listas paralelas, solo chr1-chr22, valores finitos."""
    if not hasattr(pb, "keys") or set(pb) != set(PERFIL_KEYS):
        raise ValidacionFallida("%s: perfil_bins fuera del contrato" % q)
    cr = [str(x) for x in np.asarray(pb["cromosoma"], dtype=object).ravel()]
    ini, lr = np.asarray(pb["inicio_mb"], float).ravel(), np.asarray(pb["log2_rel"], float).ravel()
    if not cr or len(ini) != len(cr) or len(lr) != len(cr) or not all(RE_CROMOSOMA.match(c) for c in cr) \
            or not (np.isfinite(ini).all() and (ini >= 0).all() and np.isfinite(lr).all()):
        raise ValidacionFallida("%s: perfil_bins fuera del contrato" % q)
    return cr, ini, lr


def _agregados_iguales(w, z):
    """¿Los agregados de la web (JSON) son exactamente los del Zarr? (mismos genes, mismos valores)."""
    if sorted(w["marcadores"]) != sorted(z["marcadores"]) or w["adn_por_grupo"] != z["adn_por_grupo"]:
        return False
    for gid, (genes, cols) in w["marcadores"].items():
        zg, zc = z["marcadores"][gid]
        if genes != zg or any(not np.array_equal(cols[k], zc[k]) for k in cols):
            return False
    (c1, i1, l1), (c2, i2, l2) = w["perfil_bins"], z["perfil_bins"]
    return c1 == c2 and np.array_equal(i1, i2) and np.array_equal(l1, l2)


def _indices_h5ad(path):
    """{lesión: índices de obs} del h5ad, para cotejar los Zarr con él."""
    import h5py
    with h5py.File(path, "r") as f:
        idx = f["obs"]["_index"].asstr()[()]
        d = f["obs"]["lesion"]
        les = np.asarray(d["categories"].asstr()[()])[d["codes"][()]] if isinstance(d, h5py.Group) else d.asstr()[()]
    return {l: [str(i) for i in np.asarray(idx)[np.asarray(les) == l]] for l in LESIONES}


# ── La carpeta PUBLICADA de la web (public/celula/explorador), 18-oct ───────────────────────────────────
# Manifiesto de la web = el de Polaris + ejemplo, zarr (huella del árbol) y matriz_completa. FAIL-CLOSED (revisión de
# verificación): la URL de un Zarr es pública aunque la página lo esconda, así que con datos reales NINGÚN Zarr sin la
# fecha de la decisión de la titular de publicar la matriz completa.
MANIFIESTO_WEB_KEYS = MANIFIESTO_KEYS + ("fecha_ok", "ejemplo", "zarr", "matriz_completa")


def sha_arbol(path):
    """sha256 del listado «ruta sha256» de todos los ficheros del árbol, ordenado (la misma huella que el test web)."""
    import hashlib
    filas = []
    for d, _, fs in os.walk(path):
        for f in fs:
            p = os.path.join(d, f)
            filas.append("%s %s" % (os.path.relpath(p, path).replace(os.sep, "/"), sha256_fichero(p)))
    return hashlib.sha256("\n".join(sorted(filas)).encode()).hexdigest()


def valida_publicado(dirp, prohibidas=()):
    """Valida la carpeta pública tal cual la sirve la web. Devuelve un resumen; lanza ValidacionFallida."""
    import shutil
    import tempfile
    man, _ = valida_json_generico(os.path.join(dirp, "manifiesto.json"), prohibidas, exento_fecha_ok=True,
                                  exento_fecha_decision=True)
    if not set(MANIFIESTO_KEYS) <= set(man) or not set(man) <= set(MANIFIESTO_WEB_KEYS) or man["version"] != 1 \
            or man["lesiones"] != list(LESIONES) or man["modo_sondas"] not in ETIQUETA_MODO.values():
        raise ValidacionFallida("publicado: cabecera del manifiesto fuera del contrato")
    ejemplo = man.get("ejemplo") is True
    if "ejemplo" in man and not ejemplo:
        raise ValidacionFallida("publicado: «ejemplo» solo puede ser true o no estar")
    estado = {"sello": man["sello"], CLAVE_OK_TITULAR: man[CLAVE_OK_TITULAR]}
    if ejemplo and not (estado == ESTADO_EXPORT and "fecha_ok" not in man):
        raise ValidacionFallida("publicado: un ejemplo no puede llevar sello, OK ni fecha de OK")
    if not ejemplo and not (estado == ESTADO_PUBLICADO and man[CLAVE_OK_TITULAR] is True and fecha_ok_valida(man.get("fecha_ok"))):
        raise ValidacionFallida("publicado: datos reales sin sello verificado, OK de la titular y su fecha")
    if [f["ruta"] for f in man["ficheros"]] != ["%s.json" % l for l in LESIONES]:
        raise ValidacionFallida("publicado: ficheros distintos del contrato")
    for f in man["ficheros"]:
        if sha256_fichero(os.path.join(dirp, f["ruta"])) != f["sha256"]:
            raise ValidacionFallida("publicado: sha256 de %s no cuadra" % f["ruta"])
    en_disco = sorted("%s/%s" % (ZARR_DIR, z) for z in os.listdir(os.path.join(dirp, ZARR_DIR))) \
        if os.path.isdir(os.path.join(dirp, ZARR_DIR)) else []
    declarados = [z["ruta"] for z in man.get("zarr", [])]
    if sorted(declarados) != en_disco:
        raise ValidacionFallida("publicado: los Zarr de la carpeta no son los del manifiesto")
    mc = man.get("matriz_completa")
    if ejemplo and mc not in (None, "ejemplo"):
        raise ValidacionFallida("publicado: un ejemplo no puede declarar una decisión real de la matriz completa")
    if en_disco:
        if declarados != ["%s/%s.zarr" % (ZARR_DIR, l) for l in LESIONES]:
            raise ValidacionFallida("publicado: Zarr fuera del contrato (uno por lesión)")
        if ejemplo and mc != "ejemplo":
            raise ValidacionFallida("publicado: Zarr de ejemplo sin matriz_completa «ejemplo»")
        if not ejemplo and not (isinstance(mc, dict) and set(mc) == {"fecha_decision"} and fecha_ok_valida(mc["fecha_decision"])):
            raise ValidacionFallida("publicado: Zarr real sin la fecha de la decisión de publicar la matriz completa")
    modo = {v: k for k, v in ETIQUETA_MODO.items()}[man["modo_sondas"]]
    res, tmp = {"ejemplo": ejemplo}, tempfile.mkdtemp(prefix="publicado_")
    try:
        for les in LESIONES:
            obj = json.load(open(os.path.join(dirp, "%s.json" % les), encoding="utf-8"))
            if (obj.get("ejemplo") is True) != ejemplo or ("ejemplo" in obj and obj["ejemplo"] is not True):
                raise ValidacionFallida("publicado: %s.json mezcla ejemplo y real" % les)
            obj.pop("ejemplo", None)
            pt = os.path.join(tmp, "%s.json" % les)
            with open(pt, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, ensure_ascii=False)
            w = valida_web_lesion(pt, prohibidas, modo)
            if en_disco:
                zp = os.path.join(dirp, ZARR_DIR, "%s.zarr" % les)
                zd = [z for z in man["zarr"] if z["ruta"] == "%s/%s.zarr" % (ZARR_DIR, les)][0]
                if sha_arbol(zp) != zd.get("sha256_arbol"):
                    raise ValidacionFallida("publicado: la huella del árbol de %s.zarr no cuadra" % les)
                z = valida_zarr_lesion(zp, les, prohibidas)
                z.pop("indices")
                z.pop("adn_interes")
                if z["n_celulas"] != w["n_celulas"] or not _agregados_iguales(w.pop("agregados"), z.pop("agregados")):
                    raise ValidacionFallida("publicado: %s.zarr no dice lo mismo que %s.json" % (les, les))
                res["zarr/%s" % les] = z
            w.pop("agregados", None)
            res[les] = w
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return res


def valida_salidas(out_dir, prohibidas=(), con_informe=True):
    """Todas las salidas del export. Lanza ValidacionFallida en el primer fallo; devuelve el resumen."""
    res = {}
    res["explorador.h5ad"] = valida_h5ad(os.path.join(out_dir, "explorador.h5ad"), prohibidas)
    res["web/manifiesto.json"] = valida_manifiesto(os.path.join(out_dir, "web", "manifiesto.json"), prohibidas)
    modo = {v: k for k, v in ETIQUETA_MODO.items()}[res["web/manifiesto.json"]["modo_sondas"]]
    for les in LESIONES:
        res["web/%s.json" % les] = valida_web_lesion(os.path.join(out_dir, "web", "%s.json" % les), prohibidas, modo)
    por_lesion = _indices_h5ad(os.path.join(out_dir, "explorador.h5ad"))
    for les in LESIONES:
        z = valida_zarr_lesion(os.path.join(out_dir, ZARR_DIR, "%s.zarr" % les), les, prohibidas)
        if z.pop("indices") != por_lesion[les]:
            raise ValidacionFallida("zarr %s: sus células no son las de esa lesión en el h5ad" % les)
        with open(os.path.join(out_dir, "web", "%s.json" % les), encoding="utf-8") as fh:
            adn_web = json.load(fh)["adn"]
        za = z.pop("adn_interes")
        if not _agregados_iguales(res["web/%s.json" % les].pop("agregados"), z.pop("agregados")):
            raise ValidacionFallida("zarr %s: los agregados de la web no son los del Zarr" % les)
        if (adn_web is None) != (za is None) or \
                (za is not None and abs(round(za, 5) - adn_web["frac_lecturas_interes"]) > 1e-5):
            raise ValidacionFallida("zarr %s: el ADN del grupo de interés no cuadra con el de la web" % les)
        res["%s/%s.zarr" % (ZARR_DIR, les)] = z
    if con_informe:
        res["INFORME_LIMPIEZA.json"] = {"n_cadenas": valida_json_generico(os.path.join(out_dir, "INFORME_LIMPIEZA.json"), prohibidas)[1]}
        res["INFORME_LIMPIEZA.md"] = valida_texto(os.path.join(out_dir, "INFORME_LIMPIEZA.md"), prohibidas)
    for nombre in sorted(os.listdir(out_dir)):
        if nombre.endswith(".csv"):
            res[nombre] = valida_csv(os.path.join(out_dir, nombre), prohibidas)
    return res
