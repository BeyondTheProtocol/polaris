#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
biomarcadores.py — Tracker longitudinal de biomarcadores de sangre de {{TITULAR}}.

Junta TODAS sus analíticas en una línea temporal para que su regla de ingeniera
del pilar integrativo ("control, y si algo falla, se corta") sea por DATO y no a ojo.

Diseño (fail-closed, el muro manda):
  - Lee los .md de analíticas de la fuente de verdad en CASA BASE (~/claudecode),
    que NO viaja a los worktrees. Por eso resolvemos BTP_REPO, nunca el árbol del worktree.
  - Extracción con DICCIONARIO CURADO de analitos: solo se extrae lo que se reconoce,
    nada se inventa. Lo dudoso se marca confianza 'baja' → subcomando `por-confirmar`
    para que {{TITULAR}} (que conoce su caso) lo valide. La verificación final la hace `verificacion`.
  - NO interpreta clínicamente. Guarda valor + rango de referencia + si estaba fuera de rango.
    La lectura es para {{TITULAR}} y sus médicas (cuarentena clínica pendiente {{CONTACTO}}).
  - Salida a tools/state/biomarcadores/ (gitignored): clínico crudo NUNCA al historial git.

Uso:
  python3 tools/biomarcadores.py build            # parsea y reconstruye el timeline
  python3 tools/biomarcadores.py por-confirmar     # lista los valores de baja confianza
  python3 tools/biomarcadores.py resumen           # resumen de lo extraído
"""
import os
import re
import sys
import json
import glob
import datetime
from collections import Counter

# ── Casa base y estado (igual criterio que tools/seguimiento.py) ───────────────
REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
BIO_DIR = os.path.join(STATE, "biomarcadores")
TIMELINE = os.path.join(BIO_DIR, "timeline.json")
INTERV = os.path.join(BIO_DIR, "intervenciones.json")
RAG_MD = os.path.join(REPO, "00_FUENTE-DE-VERDAD", "00 · Bandeja de entrada", "RAG_md")


def _now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def _write_atomic(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # atómico en POSIX


# ── Diccionario curado de analitos ─────────────────────────────────────────────
# Cada entrada: key, nombre visible, grupo, aliases (prefijo en minúsculas), unidad
# esperada (familia normalizada) y unidad de display. unit_fam=None → sin unidad (índices).
# El orden importa para los alias que comparten prefijo: se elige el alias MÁS LARGO.
GRUPOS = {
    "hematologia": "Hematología",
    "renal_hepatico": "Renal · hepático · metabólico",
    "electrolitos": "Electrolitos · calcio",
    "marcadores": "Marcadores tumorales",
    "hormonas": "Tiroides · hormonas",
}

ANALITOS = [
    # — Hematología —
    dict(key="leucocitos", nombre="Leucocitos", grupo="hematologia",
         aliases=["leucocitos"], unit_fam="x10e3", unidad="x10³/µL"),
    dict(key="neutrofilos_abs", nombre="Neutrófilos (abs.)", grupo="hematologia",
         aliases=["neutrófilos", "neutrofilos"], unit_fam="x10e3", unidad="x10³/µL"),
    dict(key="neutrofilos_pct", nombre="Neutrófilos (%)", grupo="hematologia",
         aliases=["neutrófilos", "neutrofilos"], unit_fam="pct", unidad="%"),
    dict(key="linfocitos_abs", nombre="Linfocitos (abs.)", grupo="hematologia",
         aliases=["linfocitos"], unit_fam="x10e3", unidad="x10³/µL"),
    dict(key="linfocitos_pct", nombre="Linfocitos (%)", grupo="hematologia",
         aliases=["linfocitos"], unit_fam="pct", unidad="%"),
    dict(key="nlr", nombre="Cociente neutrófilos/linfocitos (NLR)", grupo="hematologia",
         aliases=["cociente recuento neutrófilos", "cociente neutrófilos",
                  "cociente recuento neutrofilos"], unit_fam=None, unidad=""),
    dict(key="hematies", nombre="Hematíes", grupo="hematologia",
         aliases=["hematíes", "hematies"], unit_fam="x10e6", unidad="x10⁶/µL"),
    dict(key="chcm", nombre="CHCM", grupo="hematologia",
         aliases=["concentración hemoglobina", "concentracion hemoglobina"],
         unit_fam="g/dl", unidad="g/dL"),
    dict(key="hcm", nombre="Hemoglobina corpuscular media (HCM)", grupo="hematologia",
         aliases=["hemoglobina corpuscular media"], unit_fam="pg", unidad="pg"),
    dict(key="hemoglobina", nombre="Hemoglobina", grupo="hematologia",
         aliases=["hemoglobina"], unit_fam="g/dl", unidad="g/dL"),
    dict(key="hematocrito", nombre="Hematocrito", grupo="hematologia",
         aliases=["hematocrito"], unit_fam="pct", unidad="%"),
    dict(key="vcm", nombre="Volumen corpuscular medio (VCM)", grupo="hematologia",
         aliases=["volumen corpuscular"], unit_fam="fl", unidad="fL"),
    dict(key="plaquetas", nombre="Plaquetas", grupo="hematologia",
         aliases=["plaquetas"], unit_fam="x10e3", unidad="x10³/µL"),
    # — Renal · hepático · metabólico —
    dict(key="glucosa", nombre="Glucosa", grupo="renal_hepatico",
         aliases=["glucosa"], unit_fam="mg/dl", unidad="mg/dL"),
    dict(key="urea", nombre="Urea", grupo="renal_hepatico",
         aliases=["urea"], unit_fam="mg/dl", unidad="mg/dL"),
    dict(key="creatinina", nombre="Creatinina", grupo="renal_hepatico",
         aliases=["creatinina"], unit_fam="mg/dl", unidad="mg/dL"),
    dict(key="proteinas_totales", nombre="Proteínas totales", grupo="renal_hepatico",
         aliases=["proteínas totales", "proteinas totales"], unit_fam="g/dl", unidad="g/dL"),
    dict(key="albumina", nombre="Albúmina", grupo="renal_hepatico",
         aliases=["albúmina", "albumina"], unit_fam="g/dl", unidad="g/dL"),
    dict(key="got", nombre="GOT (AST)", grupo="renal_hepatico",
         aliases=["got (ast)", "got", "ast"], unit_fam="u/l", unidad="U/L"),
    dict(key="gpt", nombre="GPT (ALT)", grupo="renal_hepatico",
         aliases=["gpt (alt)", "gpt", "alt"], unit_fam="u/l", unidad="U/L"),
    dict(key="ggt", nombre="GGT", grupo="renal_hepatico",
         aliases=["ggt", "gamma-glutamil", "gamma glutamil", "gamma gt"], unit_fam="u/l", unidad="U/L"),
    dict(key="fosfatasa_alcalina", nombre="Fosfatasa alcalina", grupo="renal_hepatico",
         aliases=["fosfatasa alcalina"], unit_fam="u/l", unidad="U/L"),
    dict(key="bilirrubina_total", nombre="Bilirrubina total", grupo="renal_hepatico",
         aliases=["bilirrubina total"], unit_fam="mg/dl", unidad="mg/dL"),
    dict(key="bilirrubina_directa", nombre="Bilirrubina directa", grupo="renal_hepatico",
         aliases=["bilirrubina directa"], unit_fam="mg/dl", unidad="mg/dL"),
    dict(key="ldh", nombre="LDH", grupo="renal_hepatico",
         aliases=["ldh", "lactato deshidrogenasa"], unit_fam="u/l", unidad="U/L"),
    dict(key="pcr", nombre="Proteína C reactiva (PCR)", grupo="renal_hepatico",
         aliases=["proteína c reactiva", "proteina c reactiva"], unit_fam="mg/dl", unidad="mg/dL"),
    # — Electrolitos · calcio —
    dict(key="sodio", nombre="Sodio", grupo="electrolitos",
         aliases=["sodio"], unit_fam="meq/l", unidad="mEq/L"),
    dict(key="potasio", nombre="Potasio", grupo="electrolitos",
         aliases=["potasio"], unit_fam="meq/l", unidad="mEq/L"),
    dict(key="cloro", nombre="Cloro", grupo="electrolitos",
         aliases=["cloro", "cloruro"], unit_fam="meq/l", unidad="mEq/L"),
    dict(key="calcio", nombre="Calcio", grupo="electrolitos",
         aliases=["calcio"], unit_fam="mg/dl", unidad="mg/dL"),
    dict(key="magnesio", nombre="Magnesio", grupo="electrolitos",
         aliases=["magnesio"], unit_fam="mg/dl", unidad="mg/dL"),
    dict(key="fosforo", nombre="Fósforo", grupo="electrolitos",
         aliases=["fósforo", "fosforo"], unit_fam="mg/dl", unidad="mg/dL"),
    # — Marcadores tumorales —
    dict(key="ca153", nombre="CA 15.3", grupo="marcadores",
         aliases=["ca 15.3", "ca 15,3", "ca15.3", "ca 15 3"], unit_fam="ui/ml", unidad="UI/mL"),
    dict(key="cea", nombre="CEA", grupo="marcadores",
         aliases=["cea", "antígeno carcinoembrionario"], unit_fam="ng/ml", unidad="ng/mL"),
    dict(key="ca125", nombre="CA 125", grupo="marcadores",
         aliases=["ca 125", "ca125"], unit_fam="ui/ml", unidad="UI/mL"),
    # 26-sep-2026 ({{TITULAR}}, panel /datos «más analítica»): estaban en los informes y no se extraían.
    dict(key="ca199", nombre="CA 19-9", grupo="marcadores",
         aliases=["ca 19.9", "ca 19-9", "ca 19,9", "ca19.9", "ca19-9"], unit_fam="ui/ml", unidad="UI/mL"),
    dict(key="ca2729", nombre="CA 27.29", grupo="marcadores",
         aliases=["ca 27.29", "ca 27-29", "ca 27,29", "ca27.29"], unit_fam="ui/ml", unidad="UI/mL"),
    dict(key="b2m", nombre="Beta-2-microglobulina", grupo="marcadores",
         aliases=["beta 2-microglobulina", "beta-2-microglobulina", "beta 2 microglobulina",
                  "beta2-microglobulina", "beta2 microglobulina"], unit_fam="mg/l", unidad="mg/L"),
    dict(key="cga", nombre="Cromogranina A", grupo="marcadores",
         aliases=["cromogranina a"], unit_fam="ug/l", unidad="µg/L"),
    dict(key="nse", nombre="Enolasa neuronal específica (NSE)", grupo="marcadores",
         aliases=["enolasa específica neuronal", "enolasa especifica neuronal",
                  "enolasa neuronal específica", "enolasa neuronal especifica"], unit_fam="ng/ml", unidad="ng/mL"),
    # — Tiroides · hormonas — (alias con espacio final: «lh» a secas casaría cualquier línea que empiece así)
    dict(key="tsh", nombre="TSH", grupo="hormonas",
         aliases=["tsh "], unit_fam="uui/ml", unidad="µUI/mL", solo_valor=True),
    dict(key="t4l", nombre="T4 libre", grupo="hormonas",
         aliases=["t4 libre"], unit_fam="ng/dl", unidad="ng/dL", solo_valor=True),
    dict(key="estradiol", nombre="Estradiol", grupo="hormonas",
         aliases=["estradiol "], unit_fam="pg/ml", unidad="pg/mL", solo_valor=True),
    dict(key="fsh", nombre="FSH", grupo="hormonas",
         aliases=["fsh "], unit_fam="mui/ml", unidad="mUI/mL", solo_valor=True),
    dict(key="lh", nombre="LH", grupo="hormonas",
         aliases=["lh "], unit_fam="mui/ml", unidad="mUI/mL", solo_valor=True),
]

# índice alias → entradas (varias si comparten prefijo, p.ej. neutrófilos abs/%)
_ALIAS_IDX = {}
for a in ANALITOS:
    for al in a["aliases"]:
        _ALIAS_IDX.setdefault(al, []).append(a)
# alias ordenados por longitud desc para preferir el match más específico
_ALIASES_SORTED = sorted(_ALIAS_IDX.keys(), key=len, reverse=True)


def unit_family(u):
    """Normaliza una unidad OCR a una familia para comparar/desambiguar."""
    if not u:
        return None
    s = u.lower().replace(" ", "").replace("μ", "u").replace("µ", "u")
    if "10^3" in s or "10e3" in s or "10³" in s or "x103" in s:
        return "x10e3"
    if "10^6" in s or "10e6" in s or "10⁶" in s or "x106" in s:
        return "x10e6"
    if s.startswith("%") or s == "%":
        return "pct"
    if s.startswith("mg/dl"):
        return "mg/dl"
    # µUI/mL = mUI/L = µIU/mL: la misma magnitud (TSH), la escriba como la escriba cada laboratorio
    if s.startswith(("uui/ml", "uiu/ml", "mu/l", "mui/l", "miu/l")):
        return "uui/ml"
    if s.startswith(("mui/ml", "mu/ml", "miu/ml")):
        return "mui/ml"
    if s.startswith("ng/dl"):
        return "ng/dl"
    if s.startswith("pg/ml"):
        return "pg/ml"
    # mg/L = µg/mL (beta-2-microglobulina: Murcia en mg/L, MD Anderson en mcg/mL)
    if s.startswith(("mg/l", "mcg/ml", "ug/ml")):
        return "mg/l"
    if s.startswith(("mcg/l", "ug/l")):
        return "ug/l"
    if s.startswith("g/dl"):
        return "g/dl"
    if s.startswith("meq/l") or s.startswith("mmol/l"):
        return "meq/l"
    if s.startswith("ng/ml"):
        return "ng/ml"
    if s.startswith("ui/ml") or s.startswith("u/ml"):
        return "ui/ml"
    if s.startswith("ui/l") or s.startswith("u/l") or s.startswith("u/i"):
        return "u/l"
    if s.startswith("ml/min"):
        return "ml/min"
    if s.startswith("fl"):
        return "fl"
    if s.startswith("pg"):
        return "pg"
    return "?"


def _num(s):
    try:
        return float(s.replace(",", "."))
    except (ValueError, AttributeError):
        return None


# valor + unidad + rango opcional, tras el alias (analitos CON unidad). El valor puede venir
# CENSURADO («<15», «<9»): el laboratorio solo dice que está por debajo de su límite. Antes la regex
# saltaba el «<» y el panel público enseñaba «<9 U/L» como 9 (ALT/AST abr-2024, PCR may-2024:
# cazado el 26-sep-2026). Ahora el comparador viaja con el punto (`cmp`) y el valor es el límite.
_RE_CON_UNIDAD = re.compile(
    r"(\*{0,2})\s*([<>]?)\s*(-?\d+(?:[.,]\d+)?)\s+([^\s]+)"
    r"(?:\s+(\d+(?:[.,]\d+)?)\s*-\s*(\d+(?:[.,]\d+)?))?"
)
# valor + rango opcional, sin unidad (índices tipo NLR)
_RE_SIN_UNIDAD = re.compile(
    r"(\*{0,2})\s*([<>]?)\s*(-?\d+(?:[.,]\d+)?)"
    r"(?:\s+(\d+(?:[.,]\d+)?)\s*-\s*(\d+(?:[.,]\d+)?))?"
)
# rango escrito como «< 34 U/mL» (MD Anderson): límite superior, sin inferior impreso
_RE_RANGO_MENOR = re.compile(r"^\s*<\s*(\d+(?:[.,]\d+)?)")


# Muestras que NO son sangre: el mismo analito (LDH, glucosa, albúmina, CEA…) medido en otro líquido
# no es la serie de sangre. Detectado el 25-sep-2026: el 14-mar-2024 entraban LDH 840, glucosa <4,
# albúmina 1,8 y CEA 1,3 de LÍQUIDO PLEURAL como si fueran analítica de sangre en el panel /datos.
_RE_NO_SANGRE = re.compile(
    r"\b(l[ií]quido|pleural|asc[ií]tic[oa]|peritoneal|pericárdic[oa]|pericardic[oa]|sinovial|"
    r"cefalorraqu[ií]deo|lcr|orina|urinari[oa]|heces|lavado|broncoaspirado|drenaje)\b", re.I)


def parse_line(line):
    """Devuelve (entrada_analito, dict_punto) o None. dict_punto: valor, unidad,
    ref_low, ref_high, fuera, confianza."""
    compact = re.sub(r"\s+", " ", line).strip()
    compact = re.sub(r"^\(i\)\s*", "", compact)  # MD Anderson marca con «(i)» algunas pruebas
    if not compact or compact.startswith("-") or compact.startswith("."):
        return None
    if _RE_NO_SANGRE.search(compact):
        return None  # otra muestra (pleural, orina, LCR…): no es la serie de sangre
    low = compact.lower()
    matched_alias = None
    for al in _ALIASES_SORTED:
        if low.startswith(al):
            matched_alias = al
            break
    if not matched_alias:
        return None
    candidatos = _ALIAS_IDX[matched_alias]
    tail = compact[len(matched_alias):].strip()
    # `solo_valor`: tras el nombre viene el valor, no otra palabra («Estradiol libre», «TSH receptor»
    # son OTRA prueba con el mismo prefijo)
    if all(c.get("solo_valor") for c in candidatos) and not re.match(r"[*<>:\d-]", tail):
        return None

    # ¿hay candidato con unidad o es índice sin unidad?
    tiene_unidad = any(c["unit_fam"] is not None for c in candidatos)

    if tiene_unidad:
        m = _RE_CON_UNIDAD.search(tail)
        if not m:
            return None
        flag, cmp, val, unidad_raw, rlo, rhi = m.groups()
        if rlo is None:
            mm = _RE_RANGO_MENOR.match(tail[m.end():])
            if mm:
                rlo, rhi = "0", mm.group(1)
        valor = _num(val)
        if valor is None:
            return None
        fam = unit_family(unidad_raw)
        # elegir candidato cuya familia de unidad coincide; si ninguna, el primero con unidad
        elegido = next((c for c in candidatos if c["unit_fam"] == fam), None)
        unidad_ok = elegido is not None
        if elegido is None:
            elegido = next((c for c in candidatos if c["unit_fam"] is not None), candidatos[0])
    else:
        m = _RE_SIN_UNIDAD.search(tail)
        if not m:
            return None
        flag, cmp, val, rlo, rhi = m.groups()
        valor = _num(val)
        if valor is None:
            return None
        elegido = candidatos[0]
        unidad_ok = True  # los índices no llevan unidad

    ref_low, ref_high = _num(rlo), _num(rhi)
    # «fuera de rango» no puede fiarse SOLO del asterisco del OCR: un marcador tumoral
    # alto sin «*» (el OCR se lo comió) se mostraría en-rango. Si hay rango numérico,
    # comparamos valor vs [ref_low, ref_high] además de mirar el flag.
    fuera = bool(flag and "*" in flag)
    if not fuera and valor is not None and ref_low is not None and ref_high is not None:
        # con «<X» solo se sabe que está por debajo de X: fuera si X ya es menor que el mínimo
        # (o mayor que el máximo con «>X»); «<9» con rango 7-35 NO se puede dar por fuera
        if cmp == "<":
            fuera = valor <= ref_low
        elif cmp == ">":
            fuera = valor >= ref_high
        else:
            fuera = valor < ref_low or valor > ref_high

    # confianza
    if unidad_ok and ref_low is not None and ref_high is not None:
        conf = "alta"
    elif unidad_ok:
        conf = "media"
    else:
        conf = "baja"  # la unidad no casa con la esperada → que lo mire {{TITULAR}}

    punto = dict(valor=valor, unidad=elegido["unidad"], ref_low=ref_low,
                 ref_high=ref_high, fuera=fuera, confianza=conf)
    if cmp:
        punto["cmp"] = cmp
    # MD Anderson imprime un rango POR FASE del ciclo y el primero es el folicular: que viaje dicho
    if ref_high is not None and re.search(r"\bF\.?\s*Folicular", tail, re.I):
        punto["ref_fase"] = "folicular"
    return elegido, punto


# ── Fecha del informe ──────────────────────────────────────────────────────────
_RE_FRONT_DATE = re.compile(r'^date:\s*"?(\d{4}-\d{2}-\d{2})', re.M)
_RE_FNAME_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


# La fecha de la ANALÍTICA es la de la extracción, no la de emisión del informe. El nombre del
# fichero y el `date:` del frontmatter llevan la «Fecha Envío», y dos informes enviados el mismo
# día se pisaban en el acumulador (un punto por fecha): la analítica del 18-mar-2024 desaparecía
# y la del 15-mar salía como del 18 (lo cazó `verificacion` cotejando /datos, 24-sep-2026; 10 de
# 75 informes cambian 1-4 días). Recepción en el laboratorio = día de la extracción.
_RE_RECEPCION = re.compile(
    r"Fecha\s+(?:Recepci[oó]n|de\s+obtenci[oó]n\s+de\s+las\s+muestras)\s*:\s*(\d{1,2})/(\d{1,2})/(\d{2,4})",
    re.I)


# MD Anderson escribe el mes en letra: «Fecha toma muestra: 12/Jul/2024».
_MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
          "sep": 9, "oct": 10, "nov": 11, "dic": 12}
_RE_TOMA_MUESTRA = re.compile(r"Fecha\s+toma\s+muestra\s*:\s*(\d{1,2})/([A-Za-z]{3})[a-z]*/(\d{4})", re.I)


def fecha_de(path, texto):
    m = _RE_TOMA_MUESTRA.search(texto[:6000])
    if m and m.group(2).lower() in _MESES:
        try:
            return datetime.date(int(m.group(3)), _MESES[m.group(2).lower()], int(m.group(1))).isoformat()
        except ValueError:
            pass
    m = _RE_RECEPCION.search(texto[:6000])
    if m:
        d, mes, a = (int(x) for x in m.groups())
        a += 2000 if a < 100 else 0
        try:
            return datetime.date(a, mes, d).isoformat()
        except ValueError:
            pass  # fecha imposible en el OCR: se cae a la del informe, no se inventa
    m = _RE_FRONT_DATE.search(texto[:1500])
    if m:
        return m.group(1)
    m = _RE_FNAME_DATE.search(os.path.basename(path))
    return m.group(1) if m else None


# Informes de sangre que no se llaman «Lab - Analítica». De ellos se extraen SOLO las claves listadas:
# el resto de su hemograma/bioquímica viene en otras unidades y formatos (MD Anderson: «10 /µL» partido
# en dos líneas) y no se ha cotejado, así que no entra en las series existentes.
FUENTES_EXTRA = {
    "bioquímica (md anderson)": {"ca199", "ca2729", "ca125", "b2m", "cga", "nse",
                                 "tsh", "t4l", "estradiol", "fsh", "lh"},
}


def claves_permitidas(path):
    """None = todas (analítica normal); un set = solo esas (fuente extra)."""
    name = os.path.basename(path).lower()
    for patron, claves in FUENTES_EXTRA.items():
        if patron in name:
            return claves
    return None


def listar_analiticas():
    """Solo ficheros de Analítica (excluye Microbiología, Anatomía, etc.) y las FUENTES_EXTRA."""
    if not os.path.isdir(RAG_MD):
        return []
    out = []
    for p in glob.glob(os.path.join(RAG_MD, "*.md")):
        name = os.path.basename(p).lower()
        if " - lab - analítica" in name or " - lab - analitica" in name \
                or any(pat in name for pat in FUENTES_EXTRA):
            out.append(p)
    return sorted(out)


def build():
    archivos = listar_analiticas()
    # acumulador: key → fecha → mejor punto (por confianza)
    rank = {"alta": 3, "media": 2, "baja": 1}
    datos = {}          # key → {fecha: punto(+fuente)}
    por_confirmar = []
    n_ok = 0
    for path in archivos:
        with open(path, encoding="utf-8", errors="replace") as f:
            texto = f.read()
        fecha = fecha_de(path, texto)
        if not fecha:
            continue
        fuente = os.path.basename(path)
        permitidas = claves_permitidas(path)
        visto_en_archivo = 0
        for line in texto.splitlines():
            r = parse_line(line)
            if not r:
                continue
            ent, punto = r
            if permitidas is not None and ent["key"] not in permitidas:
                continue
            punto["fecha"] = fecha
            punto["fuente"] = fuente
            visto_en_archivo += 1
            d = datos.setdefault(ent["key"], {})
            prev = d.get(fecha)
            if prev is None or rank[punto["confianza"]] > rank[prev["confianza"]]:
                d[fecha] = punto
        if visto_en_archivo:
            n_ok += 1

    # construir estructura por grupos + derivado BUN/Cr
    _añadir_bun_cr(datos)

    grupos_out = {}
    fechas_all = set()
    pc_n = 0
    for g_key, g_nombre in GRUPOS.items():
        analitos_out = []
        for ent in ANALITOS:
            if ent["grupo"] != g_key or ent["key"] not in datos:
                continue
            puntos = sorted(datos[ent["key"]].values(), key=lambda x: x["fecha"])
            # los de baja confianza NO se grafican (no se ocultan: van a por_confirmar
            # para que {{TITULAR}} los valide; así un OCR raro no distorsiona la tendencia)
            puntos_ok = [p for p in puntos if p["confianza"] != "baja"]
            for p in puntos:
                if p["confianza"] == "baja":
                    pc_n += 1
                    por_confirmar.append(dict(analito=ent["nombre"], **{
                        k: p[k] for k in ("fecha", "valor", "unidad", "confianza", "fuente")}))
            if not puntos_ok:
                continue
            # banda de referencia = el rango MÁS FRECUENTE entre los informes (no el último).
            # Los labs varían el rango (p.ej. urea 13–43 vs 17–43); el flag `fuera` de cada
            # punto honra el rango de SU informe, así que la banda es solo orientativa.
            pares = [(p["ref_low"], p["ref_high"]) for p in puntos_ok
                     if p.get("ref_low") is not None and p.get("ref_high") is not None]
            ref = None
            if pares:
                (lo, hi), _ = Counter(pares).most_common(1)[0]
                ref = {"low": lo, "high": hi}
            for p in puntos_ok:
                fechas_all.add(p["fecha"])
            analitos_out.append(dict(
                key=ent["key"], nombre=ent["nombre"], unidad=ent["unidad"], ref=ref,
                # ref_low/ref_high de SU informe: el panel /datos normaliza a ×LSN punto a
                # punto; con la banda «más frecuente» un lab con otro rango saldría desplazado.
                puntos=[dict({k: p[k] for k in ("fecha", "valor", "fuera", "confianza", "fuente")},
                             ref_low=p.get("ref_low"), ref_high=p.get("ref_high"),
                             **{k: p[k] for k in ("cmp", "ref_fase") if p.get(k)})
                        for p in puntos_ok]))
        if analitos_out:
            grupos_out[g_key] = {"nombre": g_nombre, "analitos": analitos_out}

    fechas_ord = sorted(fechas_all)
    payload = dict(
        generado=_now(),
        n_analiticas=n_ok,
        n_analiticas_total=len(archivos),
        rango_fechas=[fechas_ord[0], fechas_ord[-1]] if fechas_ord else None,
        por_confirmar_n=pc_n,
        grupos=grupos_out,
        por_confirmar=por_confirmar,
        fuente_dir="00_FUENTE-DE-VERDAD/00 · Bandeja de entrada/RAG_md (casa base)",
        nota="Apoyo a la decisión, no consejo médico. Valor + rango; interpretación = {{TITULAR}} + sus médicas.",
    )
    _write_atomic(TIMELINE, payload)
    _seed_intervenciones()
    return payload


def _añadir_bun_cr(datos):
    """Deriva el ratio BUN/Creatinina (BUN = urea/2.14). Lo que pidió el 2º contacto,
    como dato con su banda estándar (~10–20), SIN interpretarlo."""
    urea = datos.get("urea", {})
    crea = datos.get("creatinina", {})
    if not urea or not crea:
        return
    out = {}
    for fecha, pu in urea.items():
        pc = crea.get(fecha)
        if not pc or not pc.get("valor"):
            continue
        bun = pu["valor"] / 2.14
        ratio = round(bun / pc["valor"], 1)
        conf = "alta" if pu["confianza"] != "baja" and pc["confianza"] != "baja" else "media"
        out[fecha] = dict(fecha=fecha, valor=ratio, unidad="", ref_low=10.0, ref_high=20.0,
                          fuera=not (10.0 <= ratio <= 20.0), confianza=conf,
                          fuente="derivado urea/2.14 ÷ creatinina")
    if out:
        datos["bun_cr"] = out
        ANALITOS.append(dict(key="bun_cr", nombre="Ratio BUN/Creatinina (derivado)",
                             grupo="renal_hepatico", aliases=[], unit_fam=None, unidad=""))


# ── Intervenciones (línea de tiempo del pilar) ─────────────────────────────────
# Semilla desde la fuente de verdad (CASE_SUMMARY). Todo lo no cotejado al 100% va
# con por_confirmar=true para que {{TITULAR}} lo valide. NO es interpretación clínica:
# son marcas temporales para leer las gráficas.
# `publico`: si esta marca puede salir a la capa web (fase 2).
# (Hay una parte del pilar integrativo que, por decisión de {{TITULAR}}, NO se nombra en
#  NINGÚN sitio del sistema —ni aquí, ni en la web, ni en outputs—; no se modela como marca.)
_INTERV_SEED = [
    dict(fecha="2024-01-17", nombre="Radioterapia L3", tipo="radioterapia", por_confirmar=False, publico=True),
    dict(fecha="2024-02-01", nombre="1ª línea: HT + ribociclib (CDK4/6)", tipo="sistemico", por_confirmar=True, publico=True),
    dict(fecha="2025-01-01", nombre="2ª línea", tipo="sistemico", por_confirmar=True, publico=True),
    dict(fecha="2026-04-16", nombre="Pilar metabólico (Dra. Lola Martín)", tipo="integrativo", por_confirmar=False, publico=True),
    dict(fecha="2026-06-15", nombre="SBRT espinal (15–20 jun)", tipo="radioterapia", por_confirmar=False, publico=True),
]


def _seed_intervenciones():
    if os.path.exists(INTERV):
        return  # ya existe: no piso lo que {{TITULAR}} haya confirmado/editado
    _write_atomic(INTERV, dict(actualizado=_now(), intervenciones=_INTERV_SEED,
                               nota="Fechas marcadas por_confirmar=true: validar con {{TITULAR}}. Marcas de lectura, no interpretación clínica."))


# ── CLI ─────────────────────────────────────────────────────────────────────────
def _cmd_resumen():
    if not os.path.exists(TIMELINE):
        print("No hay timeline. Corre primero: python3 tools/biomarcadores.py build")
        return
    d = json.load(open(TIMELINE, encoding="utf-8"))
    print(f"Biomarcadores · generado {d['generado']}")
    print(f"Analíticas con datos: {d['n_analiticas']}/{d['n_analiticas_total']}  ·  rango {d['rango_fechas']}")
    print(f"Valores por confirmar (baja confianza): {d['por_confirmar_n']}\n")
    for gk, g in d["grupos"].items():
        print(f"▸ {g['nombre']}")
        for a in g["analitos"]:
            pts = a["puntos"]
            ult = pts[-1]
            ref = a.get("ref")
            refs = f"  [ref {ref['low']}–{ref['high']}]" if ref else ""
            print(f"    {a['nombre']:<42} {len(pts):>3} pts  ·  último {ult['valor']} {a['unidad']} ({ult['fecha']}){refs}")
    print()


def _cmd_por_confirmar():
    if not os.path.exists(TIMELINE):
        print("No hay timeline. Corre primero: build")
        return
    d = json.load(open(TIMELINE, encoding="utf-8"))
    pc = d.get("por_confirmar", [])
    if not pc:
        print("✓ Nada de baja confianza. Todo lo extraído casó unidad esperada.")
        return
    print(f"⚠ {len(pc)} valores de baja confianza (unidad no esperada) — que los mire {{TITULAR}}:\n")
    for x in pc:
        print(f"  {x['fecha']}  {x['analito']:<30} {x['valor']} {x['unidad']}  ←  {x['fuente']}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "resumen"
    if cmd == "build":
        d = build()
        print(f"✓ Timeline reconstruido: {TIMELINE}")
        print(f"  {d['n_analiticas']}/{d['n_analiticas_total']} analíticas con datos · "
              f"{sum(len(a['puntos']) for g in d['grupos'].values() for a in g['analitos'])} puntos · "
              f"{d['por_confirmar_n']} por confirmar")
    elif cmd in ("por-confirmar", "--por-confirmar", "pc"):
        _cmd_por_confirmar()
    elif cmd in ("resumen", "summary"):
        _cmd_resumen()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
