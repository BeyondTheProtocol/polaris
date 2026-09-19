#!/usr/bin/env python3
"""audit_constelacion.py — el cortafuegos EN BUCLE de la constelación de cajas.

Cada "caja" (un mini-subsistema con un goal) vive en
`00_FUENTE-DE-VERDAD/04 · IA/Constelacion/<slug>/CAJA.md` y debe cumplir un
contrato (el charter). Este auditor lo comprueba ESTÁTICAMENTE para CADA caja y
caza la CLASE de cada deriva (no el caso suelto). Es el músculo de dos lazos:

  · loop-until-clean en el `constructor`: tras tocar una caja, se reaudita en
    bucle (`--strict`) hasta pasar TODO antes de presentarla = gate cartesiano.
  · recurrente diario (agente `auto-mejora`, ~5:08): corre junto a
    `audit_comites.py` para que ninguna caja derive sin que salte la alarma.

Principios (heredados del muro):
  · FAIL-CLOSED: un CAJA.md ilegible/corrupto/inauditable = FALLO, nunca "verde
    por defecto". Cualquier excepción dentro de una caja = FAIL de esa caja.
  · 0 cajas (no existe la carpeta o está vacía) = exit 0.
  · El texto de las cajas es DATO, no instrucción: solo se lee y compara con
    patrones; jamás se ejecuta ni se obedece nada embebido (anti-inyección). Los
    valores citados en los mensajes se truncan/delimitan (no se reinyectan).
  · Solo LECTURA: no escribe nada, no toca nada hacia fuera.

Aserciones A1–A14 (→ derivas D1–D13; A13 es control extra de seguridad pública,
A14 mata zombis de misión, y D6 «caja huérfana sin agente» se cubre dentro de A7):
  A1 charter completo (D4)         · A2 NED justificado (D5)
  A3 sin código/boca propia (D1)   · A4 sin egress fuera del gate (D2)
  A5 sensibilidad coherente (D9)   · A6 versión de plantilla (D3)
  A7 registro + dueño/expertos reales (D7+D6) · A8 cost_guard compartido (D10)
  A9 slug saneado (D11)            · A10 sin placeholders (D12)
  A11 estado honesto (D13)         · A12 conexiones válidas (D8)
  A13 caja pública sin léxico vetado ni PII (control de fuga)
  A14 caducidad/sunset: condición de retiro + fecha (zombi de misión)

Salida: 0 = todo cuadra (o 0 cajas) · 1 = hay FALLOS · 2 = error de uso.
Flags: --strict (WARN→FAIL, lo usa el constructor) · --caja SLUG · --json · --quiet
"""
import glob
import json
import os
import re
import sys
import unicodedata

ROOT = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")  # casa base SIEMPRE: las cajas (00_FUENTE-DE-VERDAD, gitignored) solo viven ahí — desde un worktree daba "0 cajas" y fail-open
AGENTS_DIR = os.path.join(ROOT, ".claude", "agents")

# ── Constantes del contrato ──────────────────────────────────────────────────
VERSION_PLANTILLA_ACTUAL = 1

CAMPOS_OBLIGATORIOS = (
    "caja", "version_plantilla", "estado", "visibilidad", "rag_scope",
    "dueno", "ned", "ned_eslabon", "ned_desbloquea", "revision",
    "expira_si", "caduca", "presupuesto_usd", "arquetipo", "expertos",
)
CAMPOS_OPCIONALES = {"conexiones"}
CLAVES_PERMITIDAS = set(CAMPOS_OBLIGATORIOS) | CAMPOS_OPCIONALES

ESTADOS = {"propuesta", "activa", "en-pausa", "archivada"}
VISIBILIDADES = {"publica", "interna"}
RAG_SCOPES = {"public", "internal", "private"}
NED_VALORES = {"directo", "indirecto"}
NED_ESLABONES = {"idea", "producto", "web", "expertos"}
ARQUETIPOS = {"solo-lectura", "redactor-borrador", "productor"}

# Tope global de gasto (espejo de tools/cost_guard.TOPE_DIARIO_USD). NO se importa
# cost_guard a propósito: mantiene el auditor como lector puro y desacoplado.
TOPE_GLOBAL_USD = 30.0

# A3 — "boca propia": una caja NUNCA reimplementa la salida ni abre red/procesos.
# Regex (no substrings): caza variantes ofuscadas. La única boca es tools/salida.py.
RE_BOCA_PROPIA = re.compile(
    r"\bapi\.telegram\.org\b|\bt\.me/|sendmessage|\bsmtplib\b|/dev/tcp"
    r"|\bos\.system\b|\bsubprocess\b|\bpopen\b|\beval\(|\bexec\("
    r"|\b(?:requests|httpx|aiohttp|urllib\w*|http\.client|socket|telebot|telegram)\b"
    r"|\b(?:curl|wget|nc|netcat|telnet|scp|ftp|ssh)\b"
    r"|\bimport\s+(?:socket|requests|http|smtplib|telegram|telebot|urllib)\b"
    r"|\bfrom\s+urllib\b|\bwebhook\b|discord\.com/api|api\.openai\.com",
    re.I)
# Una caja es DATOS: ni ficheros de código, ni fences de código dentro del .md.
RE_FENCE_CODIGO = re.compile(r"```[^\n]*\n.*?(?:\bimport \b|\bdef \b|\bfunction\b"
                             r"|#!/|\bclass \b|=>|\brequire\().*?```", re.S)
# Allowlist de extensiones DENTRO de una caja (datos, no ejecutables).
EXT_PERMITIDAS_EN_CAJA = (".md", ".txt", ".png", ".jpg", ".jpeg", ".pdf", ".csv")

# A4 — verbos de salida por FAMILIA semántica (no lista de lemas: un sinónimo no
# debe colar egress). Stems elegidos para NO pillar palabras comunes: 'publica\b'
# (no 'publicacion'), 'mand[ao]\w*' con \b (no 'demanda'), sin 'pag\w*' a secas (no
# 'pagina'). Permitidos SOLO dentro del bloque "gate de salida".
RE_EGRESS = re.compile(
    r"\b(?:"
    r"envi[ao]\w*|enviar|reenvi\w*|remit[eio]\w*|remitir|mand[ao]\w*|mandar"      # enviar/remitir/mandar
    r"|publicar|publica\b|publicad\w*|publicand\w*|publiqu\w*|publicit\w*"          # publicar
    r"|difund\w*|divulg\w*|propag\w*|compart\w*|cuelg\w*|colgar"                    # difundir/divulgar/compartir/colgar
    r"|poste[ao]\w*|postear|tuit\w*|twitt\w*"                                       # postear/tuitear
    r"|contact\w*|comunic\w*|notific\w*|respond\w*|contest\w*"                      # contactar/notificar/responder
    r"|pagar|paga\b|pagad\w*|pagand\w*|pagu\w*|abon[ao]\w*|abonar|liquid\w*"
    r"|transf[ei]\w*|reembols\w*"  # pagar/abonar/transferir (transfer- y transfi-)/reembolsar
    r"|gira\b|giras\b|girar\b|girand\w*|ingresa\w*|ingresar|domicilia\w*|domiciliar"
    r"|bizum\w*|retira\w*|retirar"  # dinero cotidiano: girar/ingresar/domiciliar/bizum/retirar
    r"|despleg\w*|desplie\w*|deploy\w*|merge\w*|export\w*|emit[ei]\w*|emitir"       # desplegar/deploy/exportar/emitir
    r")\b", re.I)
# Negación de PROXIMIDAD (junto al verbo) para A3/A4: palabras que niegan, y el
# idioma afirmativo "no dudes/tardes/esperes" que NO niega. "sin" se trata aparte:
# solo niega pegado al verbo ("sin enviar"), no detrás ("envía sin ok" = autónomo).
NEG_PALABRAS = ("no", "nunca", "jamas", "ni", "prohibido", "prohibida",
                "prohibe", "vetado", "vetada", "veta")
TRAMPA_NO = ("dudes", "tardes", "esperes")
# Conjunciones subordinantes = frontera de cláusula: una negación en una subordinada
# ("aunque no parezca, publica…") no debe 'contagiar' al verbo principal de otra.
_SUBORD = r"\b(?:aunque|cuando|mientras|si|porque|pese|a pesar|ya que|aun)\b"
# A4 — el bloque gate LISTA lo que requiere OK; no puede contener órdenes autónomas.
# Un egress dentro del gate es legítimo solo si la cláusula lo marca como gateado
# (aprobación) y no lleva marca de autonomía.
GATE_APROBACION = ("requiere", "necesita", "previa", "previo", "a un clic",
                   "borrador", "pide ok", "pedir ok", "con ok", "con el ok",
                   "firma de titular", "ok de titular", "autoriza", "pendiente de ok",
                   "espera ok", "hasta ok", "tras el ok")
GATE_AUTONOMIA = ("cada dia", "cada hora", "cada semana", "automatic", "por su cuenta",
                  "sin pedir", "sin ok", "sin permiso", "sin esperar", "sin avisar",
                  "sin consultar", "sin revision", "sin confirmar", "sin intervencion",
                  "sin supervision", "autonom", "diariamente", "cada manana",
                  "proactiv", "en bucle", "por defecto", "al instante", "recurrente",
                  "periodic")

# A10 — marcadores de plantilla a medio rellenar.
PLACEHOLDERS = ("todo:", "tbd", "xxx", "rellenar", "placeholder", "lorem ipsum",
                "<slug>", "<goal>", "<...>", "completar", "pendiente de rellenar")

# Anti-inyección: frases de payload típicas plantadas en el charter.
RE_INYECCION = re.compile(
    r"ignora\s+(?:tus|las)\s+(?:reglas|instrucciones)|olvida\s+tus\s+(?:reglas|instrucciones)"
    r"|ignore\s+(?:your|previous|all)|disregard\s+(?:your|previous)|you\s+are\s+now"
    r"|haz\s+push|desactiva\s+el\s+muro|skip[- ]permissions", re.I)

# A13 — léxico/genes vetados en PÚBLICO. FUENTE ÚNICA: tools/_lexico_publico.py
# (la misma que usa el scrubber de salida.py) para que las listas NO deriven entre sí.
from _lexico_publico import LEXICO as LEXICO_PUBLICO_PROHIBIDO, GENES as GENES_ONCO  # noqa: E402
RE_GEN = re.compile(r"\b(?:%s)\b" % "|".join(GENES_ONCO))
# Gen PEGADO a su cambio aminoacídico ({{GEN}}{{VARIANTE}}, EGFRT790M): evade \bGEN\b.
RE_GEN_PEGADO = re.compile(r"\b(?:%s)\d*[A-Za-z]\d{1,4}[A-Za-z*]" % "|".join(GENES_ONCO), re.I)
# Teléfono: con separadores (600 12 34 56 / +34 600 123 456) SIEMPRE; 9 dígitos
# seguidos SOLO si hay contexto telefónico cerca (si no, es un importe/contador).
RE_TEL_SEP = re.compile(r"(?<!\d)(?:\+?34[\s.\-]?)?[6-9]\d{0,2}(?:[\s.\-]\d{2,3}){2,3}(?!\d)")
RE_TEL_PLANO = re.compile(r"(?<!\d)[6-9]\d{8}(?!\d)")
RE_TEL_CTX = re.compile(r"tel[eé]fono|\btel\b|\btlf\b|tfno|whatsapp|wasap|m[oó]vil|"
                        r"ll[aá]ma|ll[aá]mame|\bfijo\b|\+34", re.I)
# Variante genómica: HGVS (c./p.Val600Glu), cambio aminoacídico tipo {{VARIANTE}} (≥2
# dígitos de posición, evita "B2B"), o gen + cambio pegado/espaciado.
RE_HGVS = re.compile(r"\b[cp]\.\d|\bp\.[A-Za-z]{3}\d+", re.I)
# Cambio aminoacídico ({{VARIANTE}}) con SOLO letras de aminoácido (excluye B/J/O/U/X/Z →
# descarta códigos tipo B12A); además exige contexto clínico cercano (RE_CLIN_CTX)
# para no falsear con un modelo/lote "A350K" en copy público.
RE_AA_CHANGE = re.compile(r"\b[ACDEFGHIKLMNPQRSTVWY]\d{2,4}[ACDEFGHIKLMNPQRSTVWY*]\b")
RE_CLIN_CTX = re.compile(r"mutaci|biopsia|al[eé]lic|\bvaf\b|gen[oé]?tic|\bexon\b"
                         r"|exón|oncolog|tumor|metasta|\bc\.\d|\bp\.", re.I)

# Homoglifos cirílicos/griegos → latino, y zero-width a eliminar.
_ZW = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
_HOMO = {ord(a): b for a, b in {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "к": "k", "м": "m", "т": "t", "в": "b", "н": "h", "і": "i", "ѕ": "s",
    "ј": "j", "ο": "o", "α": "a", "ε": "e", "ρ": "p", "ν": "v", "κ": "k",
    "ι": "i", "τ": "t", "0": "o",
}.items()}


def _norm(t):
    """Normaliza para comparar: minúsculas PRIMERO (colapsa mayúsculas homoglifas
    Α/В→α/в antes de traducir), homoglifos→latino, sin zero-width, NFKD sin
    diacríticos. Caza vacúna / vакuna / VΑCUNA / va<zwsp>cuna / 0lune."""
    t = t.lower()
    t = t.translate(_ZW).translate(_HOMO)
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if not unicodedata.combining(c))


def _norm_nospace(t):
    return re.sub(r"\s+", "", _norm(t))


def _scalar(v):
    """Coacciona a string seguro para .lower()/regex. Una lista/dict donde se
    esperaba escalar NO crashea: se trata como cadena vacía (lo pilla A1)."""
    if v is None or isinstance(v, (list, dict)):
        return ""
    return str(v)


def _q(v, n=80):
    """Cita un valor del charter truncado y delimitado (anti-reinyección)."""
    s = repr(str(v))
    return s if len(s) <= n else s[:n] + "…'"


# ── Modelo de hallazgo ───────────────────────────────────────────────────────
class Hallazgo:
    __slots__ = ("caja", "codigo", "nivel", "msg")

    def __init__(self, caja, codigo, nivel, msg):
        self.caja, self.codigo, self.nivel, self.msg = caja, codigo, nivel, msg

    def as_dict(self):
        return {"caja": self.caja, "codigo": self.codigo,
                "nivel": self.nivel, "msg": self.msg}


# ── Parsing del charter ──────────────────────────────────────────────────────
def _parse_frontmatter(texto):
    """(dict, cuerpo, error). Frontmatter YAML-lite entre dos `---`. Claves planas
    y listas `[a, b]`. Fail-closed: sin frontmatter cerrado → error."""
    if not texto.startswith("---"):
        return None, "", "sin frontmatter (no empieza por '---')"
    partes = texto.split("---", 2)
    if len(partes) < 3:
        return None, "", "frontmatter no cerrado (faltan los '---')"
    fm = {}
    for ln in partes[1].splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or ":" not in s:
            continue
        k, v = s.split(":", 1)
        k, v = k.strip(), v.strip()
        if v.startswith("[") and v.endswith("]"):
            fm[k] = [x.strip().strip('"').strip("'") for x in v[1:-1].split(",") if x.strip()]
        else:
            fm[k] = v.strip('"').strip("'")
    return fm, partes[2], None


def _clausulas(texto):
    """Trocea en cláusulas NORMALIZADAS: puntuación fuerte + comas + conjunciones
    subordinantes, para que la negación de una subordinada no contagie a otra."""
    return re.split(r"[.\n;:,]|" + _SUBORD, _norm(texto))


def _matches_no_negados(clausula, regex):
    """Coincidencias de `regex` NO negadas en UNA cláusula normalizada (negación de
    proximidad: ~3 palabras antes del match; 'no dudes' es afirmativo; 'sin' niega
    solo pegado al verbo)."""
    out = set()
    for m in regex.finditer(clausula):
        prev = clausula[:m.start()].split()[-3:]
        negado = bool(prev) and prev[-1] == "sin"
        for i, w in enumerate(prev):
            if w in NEG_PALABRAS and not (
                    w == "no" and i + 1 < len(prev) and prev[i + 1] in TRAMPA_NO):
                negado = True
        if not negado:
            out.add(m.group(0))
    return out


def _hits_no_negados(texto, regex):
    hits = set()
    for clausula in _clausulas(texto):
        hits |= _matches_no_negados(clausula, regex)
    return hits


def _split_gate(cuerpo):
    """Separa el cuerpo en (texto-del-gate, texto-fuera-del-gate) POR LÍNEA (no por
    substring: evita que un `replace` borre egress que coincida en ambos sitios).
    Heading de gate = contiene la PALABRA 'gate' (\\b, no 'aggregate') o 'puerta de
    salida'."""
    gate, fuera, in_gate = [], [], False
    for ln in cuerpo.splitlines():
        m = re.match(r"^#{1,6}\s+(.*)$", ln)
        if m:
            head = m.group(1).lower()
            in_gate = bool(re.search(r"\bgate\b", head)) or "puerta de salida" in head
        (gate if in_gate else fuera).append(ln)
    return "\n".join(gate), "\n".join(fuera)


def _gate_egress_malo(clausula_norm):
    """True si una cláusula del gate (ya normalizada, con egress NO negado) lleva
    marca de autonomía o NO lleva ningún marcador de aprobación = orden camuflada."""
    if any(a in clausula_norm for a in GATE_AUTONOMIA):
        return True
    return not any(a in clausula_norm for a in GATE_APROBACION)


def _es_placeholder(v):
    s = _scalar(v).strip().lower()
    return (not s) or any(p in s for p in PLACEHOLDERS)


# ── Carga de cajas ───────────────────────────────────────────────────────────
def _constelacion_dir():
    hits = glob.glob(os.path.join(ROOT, "00_FUENTE-DE-VERDAD", "04*IA", "Constelacion"))
    return hits[0] if hits else None


def cargar_cajas(solo=None):
    base = _constelacion_dir()
    if not base or not os.path.isdir(base):
        return []
    cajas = []
    for caja_md in sorted(glob.glob(os.path.join(base, "*", "CAJA.md"))):
        carpeta = os.path.dirname(caja_md)
        slug = os.path.basename(carpeta)
        if solo and slug != solo:
            continue
        cajas.append({"slug": slug, "carpeta": carpeta, "md": caja_md})
    return cajas


def _find_registry_text():
    txt = ""
    for pat in ("Comites-Registro.md", os.path.join("Constelacion", "INDICE.md")):
        for h in glob.glob(os.path.join(ROOT, "00_FUENTE-DE-VERDAD", "04*IA", pat)):
            try:
                txt += open(h, encoding="utf-8").read()
            except Exception:
                pass
    return txt


def _agentes_en_disco():
    return {os.path.basename(p)[:-3] for p in glob.glob(os.path.join(AGENTS_DIR, "*.md"))}


def _registrado(nombre, agentes, reg_text):
    return nombre in agentes or ("`%s`" % nombre) in reg_text


# ── Auditoría de UNA caja ────────────────────────────────────────────────────
def auditar_caja(caja, reg_text, agentes, slugs_validos):
    slug = caja["slug"]
    H = []
    def fail(c, m): H.append(Hallazgo(slug, c, "FAIL", m))
    def warn(c, m): H.append(Hallazgo(slug, c, "WARN", m))

    try:
        texto = open(caja["md"], encoding="utf-8").read()
    except Exception as e:
        fail("A1", "CAJA.md ilegible (%r) — fail-closed" % e)
        return H

    fm, cuerpo, err = _parse_frontmatter(texto)
    if err:
        fail("A1", "charter sin contrato: %s" % err)
        return H

    # Todo el resto va en try/except: cualquier excepción = FAIL (fail-closed real).
    try:
        bajo = _norm(texto)

        # A1 — charter completo.
        for campo in CAMPOS_OBLIGATORIOS:
            val = fm.get(campo)
            if campo not in fm or (isinstance(val, str) and not val.strip()) \
               or (isinstance(val, list) and not val):
                fail("A1", "falta el campo obligatorio '%s'" % campo)
        # Frontmatter cerrado: claves desconocidas = superficie de fuga.
        for k in fm:
            if k not in CLAVES_PERMITIDAS:
                warn("A1", "campo desconocido en el charter: %s (formulario cerrado)" % _q(k))
        # Anti-inyección en el propio charter.
        if RE_INYECCION.search(texto):
            fail("A1", "posible payload de inyección en el charter (frase de override)")

        visibilidad = _scalar(fm.get("visibilidad")).lower()
        rag_scope = _scalar(fm.get("rag_scope")).lower()
        estado = _scalar(fm.get("estado")).lower()
        dueno = _scalar(fm.get("dueno"))

        # A9 — slug saneado y coherente con la carpeta (anti path-traversal).
        if not re.match(r"^[a-z0-9-]+$", slug):
            fail("A9", "slug de carpeta inválido %s (solo [a-z0-9-])" % _q(slug))
        if fm.get("caja") and _scalar(fm.get("caja")) != slug:
            fail("A9", "el campo caja=%s no casa con la carpeta %s" % (_q(fm.get("caja")), _q(slug)))

        # A2 — NED justificado.
        if _scalar(fm.get("ned")).lower() not in NED_VALORES:
            fail("A2", "ned=%s no es directo|indirecto" % _q(fm.get("ned")))
        if _scalar(fm.get("ned_eslabon")).lower() not in NED_ESLABONES:
            fail("A2", "ned_eslabon=%s fuera de {idea,producto,web,expertos}" % _q(fm.get("ned_eslabon")))
        if _es_placeholder(fm.get("ned_desbloquea")):
            fail("A2", "ned_desbloquea vacío/placeholder: di QUÉ decisión humana hacia la vacuna desbloquea")

        # A5 — sensibilidad declarada y coherente.
        if visibilidad not in VISIBILIDADES:
            fail("A5", "visibilidad=%s no es publica|interna" % _q(fm.get("visibilidad")))
        if rag_scope not in RAG_SCOPES:
            fail("A5", "rag_scope=%s no es public|internal|private" % _q(fm.get("rag_scope")))
        if visibilidad == "publica" and rag_scope != "public":
            fail("A5", "caja PÚBLICA con rag_scope=%s: una pública solo lee el RAG público (fuga clínica)" % _q(rag_scope))

        # A6 — versión de plantilla vigente.
        try:
            vp = int(_scalar(fm.get("version_plantilla")))
            if vp < VERSION_PLANTILLA_ACTUAL:
                warn("A6", "plantilla v%d obsoleta (actual v%d): regenerar" % (vp, VERSION_PLANTILLA_ACTUAL))
            elif vp > VERSION_PLANTILLA_ACTUAL:
                fail("A6", "version_plantilla v%d > actual v%d" % (vp, VERSION_PLANTILLA_ACTUAL))
        except (TypeError, ValueError):
            fail("A6", "version_plantilla no numérica: %s" % _q(fm.get("version_plantilla")))

        # A3 — sin boca propia ni código (caja = DATOS). Negación-consciente en
        # prosa; los bloques de código y ficheros se vetan SIN excepción debajo.
        for pat in _hits_no_negados(texto, RE_BOCA_PROPIA):
            fail("A3", "patrón de boca propia/código '%s': la salida es SOLO tools/salida.py" % pat)
        if RE_FENCE_CODIGO.search(texto):
            fail("A3", "bloque de código en el charter: una caja es DATOS, no lleva implementación")
        for f in glob.glob(os.path.join(caja["carpeta"], "**", "*"), recursive=True):
            if not os.path.isfile(f):
                continue
            base = os.path.basename(f)
            if not base.lower().endswith(EXT_PERMITIDAS_EN_CAJA):
                fail("A3", "fichero no permitido en la caja: %s (solo datos: %s)" % (base, ", ".join(EXT_PERMITIDAS_EN_CAJA)))
            elif os.access(f, os.X_OK):
                fail("A3", "fichero con bit de ejecución en la caja: %s" % base)

        # A4 — sin egress fuera del bloque gate; arquetipo del enum.
        arquetipo = _scalar(fm.get("arquetipo")).lower()
        if arquetipo not in ARQUETIPOS:
            fail("A4", "arquetipo=%s fuera del enum {solo-lectura,redactor-borrador}" % _q(fm.get("arquetipo")))
        gate_txt, fuera_gate = _split_gate(cuerpo)
        for verbo in _hits_no_negados(fuera_gate, RE_EGRESS):
            fail("A4", "verbo de salida '%s' FUERA del bloque gate (egress autónomo)" % verbo)
        for clausula in _clausulas(gate_txt):
            if _matches_no_negados(clausula, RE_EGRESS) and _gate_egress_malo(clausula):
                fail("A4", "egress dentro del gate sin marcador de OK/firma o con marca de autonomía (orden camuflada)")
                break

        # A8 — cost_guard compartido. Número SIMPLE (regex): rechaza nan/inf (que
        # burlaban las comparaciones) y el separador '_' (2_0→20 no es lo escrito).
        praw = _scalar(fm.get("presupuesto_usd")).strip()
        if not re.match(r"^\d+(?:\.\d+)?$", praw):
            fail("A8", "presupuesto_usd no es un número simple (¿nan/inf/_/texto?): %s" % _q(fm.get("presupuesto_usd")))
        else:
            pres = float(praw)
            if pres > TOPE_GLOBAL_USD:
                fail("A8", "presupuesto_usd=%.2f > tope global %.2f (el tope es GLOBAL, no por caja)" % (pres, TOPE_GLOBAL_USD))
            elif pres == 0:
                warn("A8", "presupuesto_usd=0 (¿caja sin presupuesto?)")
        if os.path.exists(os.path.join(caja["carpeta"], "limits.json")):
            fail("A8", "la caja tiene su propio limits.json: el freno de gasto es COMPARTIDO")

        # A7 — registro + dueño/expertos reales (tokens delimitados, no substring).
        if slug not in reg_text:
            warn("A7", "la caja no aparece en el Registro/INDICE de la constelación")
        if dueno and not _registrado(dueno, agentes, reg_text):
            fail("A7", "dueño %s no es un agente en disco ni está registrado (caja huérfana)" % _q(dueno))
        for ex in (fm.get("expertos") or []):
            if not _registrado(ex, agentes, reg_text):
                warn("A7", "experto %s no es un agente conocido ni está registrado" % _q(ex))

        # A10 — sin placeholders.
        for ph in PLACEHOLDERS:
            if ph in bajo:
                warn("A10", "placeholder sin rellenar en el charter: %s" % _q(ph))
                break

        # A11 — estado honesto.
        if estado not in ESTADOS:
            fail("A11", "estado=%s fuera de {propuesta,activa,en-pausa,archivada}" % _q(fm.get("estado")))
        if estado == "activa":
            if not dueno:
                fail("A11", "caja ACTIVA sin dueño")
            rev = _scalar(fm.get("revision"))
            if re.match(r"^\d{4}-\d{2}-\d{2}$", rev):
                from datetime import date
                try:
                    y, mo, d = (int(x) for x in rev.split("-"))
                    if date(y, mo, d) < date.today():
                        warn("A11", "caja ACTIVA con revisión caducada (%s): revisar o pasar a en-pausa (zombi)" % rev)
                except ValueError:
                    fail("A11", "fecha de revisión inválida: %s" % _q(rev))
            else:
                warn("A11", "revision sin fecha ISO (YYYY-MM-DD): %s" % _q(fm.get("revision")))

        # A14 — caducidad / sunset: cada caja declara CÓMO termina (`expira_si`, la
        # condición de retiro) y CUÁNDO caduca su misión (`caduca`: fecha ISO o
        # 'nunca' para capacidad permanente). Mata zombis de misión: el auditor avisa
        # de cajas vencidas sin esperar a que alguien note que la condición se cumplió.
        if _es_placeholder(fm.get("expira_si")):
            fail("A14", "expira_si vacío/placeholder: di la CONDICIÓN que retira esta caja")
        caduca = _scalar(fm.get("caduca")).strip()
        if caduca.lower() == "nunca":
            pass  # caja de capacidad permanente: sin sunset mecánico
        elif re.match(r"^\d{4}-\d{2}-\d{2}$", caduca):
            from datetime import date
            try:
                y, mo, d = (int(x) for x in caduca.split("-"))
                if date(y, mo, d) < date.today() and estado != "archivada":
                    warn("A14", "caja vencida (caducó el %s): archivar o renovar `caduca` (zombi de misión)" % caduca)
            except ValueError:
                fail("A14", "fecha de caduca inválida: %s" % _q(fm.get("caduca")))
        else:
            fail("A14", "caduca=%s no es fecha ISO (YYYY-MM-DD) ni 'nunca'" % _q(fm.get("caduca")))

        # A12 — conexiones válidas.
        for con in (fm.get("conexiones") or []):
            if con and con not in slugs_validos:
                fail("A12", "conexión a una caja inexistente: %s" % _q(con))

        # A13 — léxico/PII en caja pública (normalizado + sin espacios).
        if visibilidad == "publica":
            sin_esp = _norm_nospace(texto)
            for lex in LEXICO_PUBLICO_PROHIBIDO:
                if lex in bajo or lex in sin_esp:
                    fail("A13", "léxico prohibido en público '%s' en una caja PÚBLICA" % lex)
            if RE_GEN.search(bajo) or RE_GEN_PEGADO.search(texto):
                fail("A13", "gen/biomarcador oncológico nombrado en una caja PÚBLICA (PII clínica)")
            variante = bool(RE_HGVS.search(texto))
            if not variante:  # cambio aminoacídico solo con contexto clínico en la MISMA frase
                for frase in re.split(r"[.\n;:]", texto):
                    if RE_AA_CHANGE.search(frase) and RE_CLIN_CTX.search(frase):
                        variante = True
                        break
            if variante:
                fail("A13", "posible variante genómica (HGVS/cambio aminoacídico) en una caja PÚBLICA")
            if RE_TEL_SEP.search(texto) or (RE_TEL_PLANO.search(texto) and RE_TEL_CTX.search(texto)):
                fail("A13", "posible teléfono (PII) en una caja PÚBLICA")
    except Exception as e:
        fail("A1", "charter inauditable (%r) — fail-closed" % e)

    return H


# ── Orquestación ─────────────────────────────────────────────────────────────
def auditar(solo=None):
    cajas = cargar_cajas(solo)
    reg_text = _find_registry_text()
    agentes = _agentes_en_disco()
    slugs_validos = {c["slug"] for c in cargar_cajas()}
    todos = []
    for caja in cajas:
        todos.extend(auditar_caja(caja, reg_text, agentes, slugs_validos))
    return cajas, todos


def main(argv):
    strict = "--strict" in argv
    quiet = "--quiet" in argv
    as_json = "--json" in argv
    solo = None
    if "--caja" in argv:
        i = argv.index("--caja")
        if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
            print("uso: --caja SLUG"); return 2
        solo = argv[i + 1]

    cajas, hallazgos = auditar(solo)
    fails = [h for h in hallazgos if h.nivel == "FAIL"]
    warns = [h for h in hallazgos if h.nivel == "WARN"]
    rojo = fails + (warns if strict else [])

    if as_json:
        print(json.dumps({
            "n_cajas": len(cajas), "ok": not rojo, "strict": strict,
            "fails": [h.as_dict() for h in fails],
            "warns": [h.as_dict() for h in warns],
        }, ensure_ascii=False, indent=2))
        return 0 if not rojo else 1

    if not quiet:
        print("🌌 Cajas en la constelación: %d%s" % (len(cajas), " (filtro: %s)" % solo if solo else ""))
        if not cajas:
            print("✅ Sin cajas que auditar (la fábrica aún no ha creado ninguna).")
            return 0
        if not rojo:
            print("✅ TODAS las cajas pasan los 14 cortafuegos%s." % (" (modo estricto)" if strict else ""))
        for h in fails:
            print("  ⛔ [%s] %s · %s" % (h.codigo, h.caja, h.msg))
        for h in warns:
            print("  %s [%s] %s · %s" % ("⛔" if strict else "⚠️ ", h.codigo, h.caja, h.msg))
        if rojo:
            print("\n❌ %d FALLO(S)%s — la(s) caja(s) NO se presenta(n) hasta pasar TODO." %
                  (len(rojo), " (WARN=FAIL en estricto)" if strict and warns else ""))

    return 0 if not rojo else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
