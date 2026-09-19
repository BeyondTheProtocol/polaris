#!/usr/bin/env python3
"""tools/borde.py — EL BORDE de confianza no-bypassable (F0-mínima del motor agnóstico).

Plan: `~/.claude/plans/typed-swinging-wand.md`. Veredicto unánime de la auditoría
(Grok·NVIDIA·DeepSeek·GLM·GPT·Albia·Fugu): el diseño completo solo es defendible si existe
**UN solo borde no-bypassable que medie TODO el egress** hacia un cerebro EXTERNO, con unos
pocos invariantes ROCA, simples y bien testeados, ANTES de la pila avanzada (ZK/GBAC/etc.).
Esto es ese borde. NO es la centralita (eso es F0/F1, post-biopsia): es la PARED por la que
todo carril externo tiene que pasar.

Antes la pared estaba dispersa: solo `fugu.py` comprobaba la sensibilidad antes de salir; el
resto de carriles (nvidia/carril_gratis, glm, grok, perplexity, chatgpt, gemini) mandaban texto
a un tercero SIN filtro. "Un solo bypass lateral convierte el kernel en decoración" (GPT). Ahora
se consolida en una sola fuente: TODOS los carriles externos pasan por `borde.guard_cli` /
`borde.egress_check` / `borde.permitido` (fugu, carril_gratis/nvidia, glm, grok, perplexity,
chatgpt, gemini). Cualquier carril externo NUEVO debe llamar al borde — es la única puerta.

Invariantes ROCA (F0 — fijos, simples, testeados en tests/test_borde.py):
  1. FAIL-CLOSED por sensibilidad: ante la duda, NO sale. Lo clínico/genómico/PII/términos
     vetados del muro solo va a destinos *trusted* (local/cleared). Reusa los deny-lists de
     seguimiento.py (una sola fuente del muro, no copias).
  2. VÁLVULA DE DECLASSIFICACIÓN (Fugu, palabra final): el bypass real no es "salir por otro
     socket", es que el sistema convierta un secreto en una salida AUTORIZADA (resumen, score,
     decisión). Por eso `declasificar()` es deny-by-default + salida TIPADA por campo +
     presupuesto de revelación por sesión + test "¿el dato sigue dentro del valor permitido?".
  3. AIR-GAP de embeddings (Albia, blindaje nº1): `embedding_egress_check()` NIEGA vectorizar
     por API externa — los vectores se pueden invertir y reconstruir el texto. Solo modelo
     LOCAL (BGE-M3) a destino local.
  4. TRAZA append-only HASH-CHAINED: cada decisión se sella encadenada; un borrado/alteración
     rompe la cadena (`verificar_cadena()` lo detecta por salto de secuencia o sello roto). La
     traza guarda METADATOS, NUNCA el contenido (logs minimizados; solo un sello sha256).
  5. REVOCABILIDAD + anti-replay: una sesión/intención revocada no vuelve a pasar (persistente).
  6. CANARIOS: si un canario sembrado aparece en una salida → exfiltración → DENY + ALARMA
     (opcional: dispara código rojo).

Lo que F0 NO hace (a propósito — anti-over-engineering, son fases posteriores): GBAC/ZK,
attestation TEE/HSM, router causal, cifrado homomórfico, génesis JIT de tools. El cifrado en
reposo del dato CRUDO es la BASE FÍSICA (LUKS/TPM/YubiKey, hardware) del plan, proporcional al
ingest de la biopsia; aquí el borde garantiza que NO se persiste contenido sensible en claro.

CLI:
  python3 tools/borde.py check "<texto>" [--destino local:consejero-arneses] [--sesion s1]
  python3 tools/borde.py verify           # integridad de la cadena de traza
  python3 tools/borde.py revocar <sesion>
  python3 tools/borde.py trust-cloud <destino> [--para sensible]   # ACTO HUMANO (confirma tecleando)
  python3 tools/borde.py revoke-cloud <destino>                    # revoca una nube confiada
  python3 tools/borde.py status | selftest
"""
import fcntl
import hashlib
import json
import os
import re
import sys
from collections import namedtuple
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seguimiento as seg  # noqa: E402 — ÚNICA fuente de los deny-lists del muro

HOME = os.path.expanduser("~")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
BORDE_DIR = os.path.join(STATE, "borde")
HEAD_FILE = os.path.join(BORDE_DIR, "head.txt")
LOCK_FILE = os.path.join(BORDE_DIR, ".lock")
REVOCADAS = os.path.join(BORDE_DIR, "revocadas.json")
CANARIO_FILE = os.path.join(BORDE_DIR, "canarios.json")
# Nubes confiadas POR UN HUMANO para contenido sensible (gate trazable). NUNCA se escribe por
# código de flujo ni por contenido externo: solo el CLI `borde.py trust-cloud` (confirmación
# tecleada). Es un acto humano, registrado en la cadena y REVOCABLE. El suelo (TRUSTED_PREFIXES)
# manda igual; esto solo AÑADE destinos de nube concretos, bajo responsabilidad de {{TITULAR}}.
CLOUD_CONFIADOS = os.path.join(BORDE_DIR, "cloud_confiados.json")

# Kill-switch del muro (mismo contrato que muro_guard.py / run_agent.sh). Override por test.
HALT_FILES = (tuple(os.environ["BTP_HALT_FILES"].split(":"))
              if os.environ.get("BTP_HALT_FILES")
              else (os.path.join(HOME, ".btp.HALT"), os.path.join(REPO, ".HALT")))

# ── Registro de confianza (ESTÁTICO — rutas estáticas para PHI; deny-by-default) ─────────
# Trusted = puede RECIBIR contenido sensible. Solo lo LOCAL y lo CLEARED. Cualquier endpoint
# de un modelo en la nube (fugu/nvidia/glm/grok/perplexity/chatgpt/openrouter/gemini) es NO
# confiable POR DEFECTO, aunque la marca lo llame "Alby": el relay de Alby es un proceso
# humano cleared, no el endpoint del modelo. Fail-closed: lo que no esté aquí, no es trusted.
TRUSTED_PREFIXES = ("local:", "cleared:")


def _trusted_exactos():
    """Allowlist exacta desde BTP_BORDE_TRUSTED (separada por COMAS — los valores llevan ':').
    SOLO se admite lo que empiece por local:/cleared: → una env var no puede colar un endpoint
    de nube como trusted (cierra M1)."""
    raw = (os.environ.get("BTP_BORDE_TRUSTED", "")).split(",")
    return frozenset(s.strip() for s in raw
                     if s.strip() and any(s.strip().startswith(p) for p in TRUSTED_PREFIXES))

# Embeddings: SOLO modelos locales (air-gap). Inversión de vectores reconstruye el texto.
# multilingual-e5-small: el que usa kb.py para la capa vectorial híbrida (ONNX, egress-0,
# ver tools/kb_embed.py) — 384-dim, corre local vía onnxruntime, nunca sale de la máquina.
EMBEDDINGS_LOCALES_OK = frozenset({"bge-m3", "bge-large", "e5-large", "nomic-embed-text",
                                   "all-minilm", "gte-large",
                                   "multilingual-e5-small", "e5-small"})

Veredicto = namedtuple("Veredicto", "permitido motivo sensibilidad alarma")


# ── Clasificación de sensibilidad (consolida lo que estaba en fugu.py) ───────────────────
# NOTA HONESTA (auditoría verificacion 23/6): una deny-list de patrones SIEMPRE será porosa
# para datos clínicos (notación corta, espaciados, homoglifos). Por eso esto es un TRIPWIRE de
# defensa-en-profundidad, NO la garantía. La garantía real es ARQUITECTÓNICA: el carril clínico
# va a Claude / destinos *trusted* SIEMPRE (el muro), y lo profundamente específico del caso solo
# a periférico cleared + local. El camino correcto para egress clínico de verdad es ALLOW-LIST
# (de-identificar y verificar lo que sale), no cazar lo malo — eso es F1. Aquí se cazan las
# CLASES de evasión conocidas para que el tripwire no dé falsa confianza.
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
_RE_HLA = re.compile(r"HLA[\s\-]*[A-DRQP]\b", re.I)               # HLA-A / HLA A / HLA A*02
_RE_RS = re.compile(r"\brs\d{3,}\b", re.I)
_RE_CHR = re.compile(r"\bchr[\dxy]+\s*[:\- ]\s*(?:[gcpmnr]\.)?\d", re.I)  # chr17:123 y chr17:g.123
_RE_CHR_W = re.compile(r"\b(?:cromosoma|chromosome)\s*\d+\b", re.I)
_RE_CITO = re.compile(r"\b(?:delec|amplific|monosom|trisom|ganancia|p[eé]rdida|loss|gain|"
                      r"del|amp)\w*\s+(?:de[l]?\s+)?\d{1,2}[pq]\d{0,2}\b", re.I)  # del 17p, amp 8p11
_RE_EXON = re.compile(r"\bex[oó]n\s*\d+\b", re.I)
# p.Arg175His / c.524G>A / g.7676154G>A / c.68_69delAG / c.5266dupC / p.(Arg175His) / c.1234+1G>A.
# Sin `\b` al final: el `_` de un rango HGVS (c.68_69del) es carácter de palabra y el `\b` hacía
# que la variante entera no casara (11-sep-26). Ante la duda, casa: un falso positivo solo retiene.
_VAR_CUERPO = r"[A-Za-z]*\d+(?:[_+\-]\d+)*(?:[A-Za-z>*=?]+)?"
_RE_VAR = re.compile(r"\b[gcpmnr]\.(?:\(%s\)|%s)" % (_VAR_CUERPO, _VAR_CUERPO))  # el ")" solo si abrió
_RE_VAR_CORTO = re.compile(r"\b[A-Z]\d{2,4}[A-Z]\b")             # V600E / R175H / H1047R / G12D
_RE_GT = re.compile(r"\b[01][/|][01]\b")                          # genotipo VCF 0/1, 1|1
_RE_CLIN = re.compile(r"\b(ki-?67|tmb|hrd|msi|her-?2|braf|pik3ca|esr1|tp53|ccne1|rb1|brca[12]"
                      r"|fgfr[1-4]|egfr|kras|alk)\b|neuroendocrin", re.I)
_RE_CLIN_PCT = re.compile(r"\b(er|pr|re|rp|ki-?67)\b[^\n]{0,12}\d{1,3}\s?%", re.I)
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_RE_PHONE = re.compile(r"(?:\+\d[\d\s().-]{7,}\d)|(?:\b\d{3}[\s.-]\d{2,3}[\s.-]\d{2,3}\b)"
                       r"|(?:\b[67]\d{8}\b)")  # móvil español a pelo (9 díg., empieza 6/7); fail-closed
_RE_DNI = re.compile(r"\b\d{8}[A-Za-z]\b|\b[XYZ]\d{7}[A-Za-z]\b")
# NHC / numero de historia clinica. Faltaba, y era una fuga CON sello de aprobacion: deid.py
# se autoverifica contra clasificar(), asi que un "NHC <numero>" salia intacto y ademas marcado
# LIMPIO. Es un identificador directo de hospital: ata el texto a una persona concreta para
# cualquiera con acceso al sistema del centro. Verificado el 2-sep-2026.
# Se EXIGE la etiqueta delante (NHC, n.º historia, historia clinica...): un numero suelto de
# 6-12 digitos es cualquier cosa (un recuento, un pedido, un anio-mes) y marcarlo sensible
# llenaria el sistema de falsos positivos hasta volver inutil al juez.
_RE_NHC = re.compile(
    r"\b(?:n\.?\s?h\.?\s?c\.?|n[uu]m(?:ero)?\.?\s+(?:de\s+)?historia(?:\s+cl[ii]nica)?"
    r"|historia\s+cl[ii]nica|n[oo]\s*historia)\s*[:.\-]?\s*\d{4,12}\b", re.I)
# El SEGUNDO apellido faltaba (2-sep-2026). En Espana nadie la llama «Sra. Perez», la
# llaman «Sra. {{APELLIDO}}» — pero fuera de Espana se toma el ULTIMO apellido como EL apellido,
# asi que un informe del USZ de {{CIUDAD}} o un correo de {{CENTRO}} que diga «patient Perez»
# pasaba el juez como LIMPIO. Medido antes de anadirlo: «Perez» aparece 9.287 veces en la
# fuente de verdad frente a las 86.847 de «{{APELLIDO}}», que ya estaba aqui sin dar problemas.
# Una decima parte del ruido que el sistema ya tolera, y el coste es asimetrico: un falso
# positivo redacta una palabra de mas, un falso negativo deja salir su identidad.
_NOMBRE_TITULAR = re.compile(r"\btitular\b|\bgonz[aá]lez\b|\bp[eé]rez\b", re.I)


def _normalizar(texto):
    """NFKD + quita marcas diacríticas combinantes + zero-width, y lower. Cierra la evasión por
    homoglifo/acentos falsos ('vacüna'). Devuelve (texto_norm, low_norm)."""
    import unicodedata
    t = unicodedata.normalize("NFKD", texto).translate(_ZERO_WIDTH)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t, t.lower()


# Consulta que habla de SU caso («mi vacuna», «para mí», «paciente de 41 años»). El embargo de
# palabras hacía de cazatodo accidental de esto; al quitarlo para las búsquedas (11-sep-26), la
# frase saldría a un tercero desde su cuenta, que ya la identifica. `verificacion` lo cazó.
_RE_PRIMERA_PERSONA = re.compile(
    # «mi/mis/nuestra + algo de su caso» y los suyos (un familiar también la señala)
    r"\b(?:mi|mis|nuestr[ao]s?)\s+(?:caso|vacuna|tumor|c[aá]ncer|onc|m[eé]dic|doctor|mutaci|biops|"
    r"tratamiento|diagn|informe|met[aá]stas|prueba|resultado|panel|hla|diana|enfermedad|terapia|"
    r"ensayo|mujer|marido|pareja|madre|padre|hermana|hij)"
    # formas verbales en primera persona sobre la enfermedad
    r"|\b(?:tengo|padezco|me\s+(?:han|van|hacen|ponen|dieron|diagnostic)|soy\s+(?:paciente|"
    r"enferma|superviviente)|como\s+yo)\b|\bpara\s+m[ií]\b"
    r"|\bmy\s+(?:case|vaccine|tumou?r|cancer|oncolog|doctor|mutation|biops|treatment|diagnos|"
    r"report|metasta|results?|panel|therapy|trial|wife|mother|sister)|\bi\s+(?:have|was\s+diagnosed)\b"
    # edad o nacimiento de UNA persona (no «supervivencia a 5 años» ni «últimos 10 años»)
    r"|\b(?:paciente|mujer|chica|ingeniera|tengo|edad)\b[^.\n]{0,40}?\b\d{2}\s*a[nñ]os"
    r"|\b\d{2}\s*a[nñ]os\s+de\s+edad|\b\d{2}-year-old|\baged\s+\d{2}\b|\bnacid[ao]\s+en\s+\d{4}",
    re.I)
# Letras sueltas separadas por espacios («m i c a s o»): el patrón de arriba no las ve.
_RE_LETRAS_SUELTAS = re.compile(r"(?:\b\w\s+){3,}\w\b")
_COMPACTOS_PRIMERA = ("micaso", "mivacuna", "mitumor", "micancer", "parami", "tengocancer",
                      "mioncolog", "misresultados", "midiagnostico")
# La marca pública + un término aún embargado = combinación que la re-identifica (memoria 29-6).
_RE_MARCA = re.compile(r"beyond\s*the\s*protocol|helptitular", re.I)


def _habla_de_su_caso(texto):
    norm, _ = _normalizar(texto)
    if _RE_PRIMERA_PERSONA.search(norm) or _RE_PRIMERA_PERSONA.search(texto):
        return True
    for run in _RE_LETRAS_SUELTAS.findall(norm):
        junto = re.sub(r"\s+", "", run).lower()
        if any(c in junto for c in _COMPACTOS_PRIMERA):
            return True
    if _RE_MARCA.search(norm):
        canon = seg._canon(texto)
        if any(seg._canon(t) in canon for t in seg._VETO_EMBARGO):
            return True
    return False


def clasificar_consulta(texto):
    """(es_sensible, motivo) para una CONSULTA de búsqueda que sale tal cual a un tercero.
    Igual que `clasificar` pero sin el embargo de palabras públicas (vacuna, neoantígenos,
    nombres-ruta), que rige lo que se publica y no una búsqueda. A cambio, cualquier rastro de
    que la consulta habla de SU caso la devuelve al borde estricto. FAIL-CLOSED."""
    if not isinstance(texto, str) or not texto.strip():
        return False, "vacío"
    if _habla_de_su_caso(texto):
        return True, "la consulta habla de su caso (primera persona, edad o marca + embargo)"
    return clasificar(texto, veto_publico=False)


def clasificar(texto, *, veto_publico=True):
    """(es_sensible: bool, motivo). FAIL-CLOSED: marca sensible ante cualquier indicio de
    clínico / genómico / PII / término vetado del muro. Reusa los deny-lists de seguimiento.
    Tripwire de defensa-en-profundidad (ver NOTA arriba), no garantía única.

    `veto_publico=False` quita SOLO el embargo de palabras («vacuna», neoantígenos, nombres-ruta),
    que es una regla de lo que se PUBLICA; el durable («{{CONTACTO}}») y todos los detectores de clínico,
    genómico y PII siguen igual. Lo usa el enrutador para una consulta de búsqueda (11-sep-26)."""
    if not isinstance(texto, str) or not texto.strip():
        return False, "vacío"
    norm, low = _normalizar(texto)
    # términos vetados: match sobre la forma CANÓNICA (desofusca + sin separadores) — una sola fuente
    # con el muro (seg._canon), que caza 'v a c u n a', 'neo-antigeno', homoglifos ('Оlune') y términos
    # con separador propio ('{{VACUNA2}}' vs 'mrna 4157'). Antes era un compacto local más débil.
    canon = seg._canon(texto)
    for term in (seg._TERMINOS_VETADOS if veto_publico else seg._VETO_DURABLE):
        if seg._canon(term) in canon:
            return True, "término vetado del muro (%s)" % term
    if _NOMBRE_TITULAR.search(norm):
        return True, "PII (nombre de {{TITULAR}})"
    if set(re.findall(r"[a-záéíóúñ]+", low)) & seg._NOMBRES_DENY:
        return True, "nombre propio en deny-list"
    es_clin, etq_clin = _clinico_o_genomico(norm)
    if es_clin:
        return True, "dato clínico/genómico (%s)" % etq_clin
    for rx, etq in _ID_DURO:
        if rx.search(norm):
            return True, "PII (%s)" % etq
    return False, "limpio"


# Identificadores DUROS: por sí solos, sin ningún nombre alrededor, ya atan el texto a una
# persona concreta (un email o un DNI no necesitan contexto para identificar). Única fuente:
# la reusan `clasificar()` (arriba) e `identificador_directo()` (abajo), sin duplicar regex.
_ID_DURO = ((_RE_EMAIL, "email"), (_RE_DNI, "DNI/NIE"), (_RE_PHONE, "teléfono"),
            (_RE_NHC, "NHC / historia clínica"))

# Detectores de clínico/genómico AISLADO. Única fuente: la reusan `clasificar()` (vía
# `_clinico_o_genomico()`) e `identificador_directo()`, para que nunca diverjan en silencio.
_CLINICO_GENOMICO = ((_RE_HLA, "HLA"), (_RE_RS, "rsID"), (_RE_CHR, "coordenada genómica"),
                     (_RE_CHR_W, "cromosoma"), (_RE_CITO, "citobanda"), (_RE_EXON, "exón"),
                     (_RE_VAR, "variante"), (_RE_VAR_CORTO, "variante (HGVS corto)"),
                     (_RE_GT, "genotipo VCF"), (_RE_CLIN, "marcador clínico"),
                     (_RE_CLIN_PCT, "cifra clínica"))


def _clinico_o_genomico(norm):
    """(bool, etiqueta|None) sobre texto YA normalizado (`_normalizar()`). Extraído de
    `clasificar()` (13-sep-26) para que `identificador_directo()` reuse la MISMA lista de
    detectores sin copiar regex — si una diverge de la otra, un caso deja de tratarse igual en
    las dos funciones sin que nadie lo note. No cambia el comportamiento de `clasificar()`:
    mismas regex, mismo orden, mismo resultado."""
    for rx, etq in _CLINICO_GENOMICO:
        if rx.search(norm):
            return True, etq
    return False, None


def identificador_directo(texto):
    """(bool, motivo). Aditiva: NO toca `clasificar()` ni a sus ~10 llamadores actuales.

    Corrección de {{TITULAR}} 13-sep-26 sobre la primera versión de esta función: leyendo
    `.claude/rules/clinico.md` L21-23, N2 es «relato o informe crudo: nombre + edad + fecha +
    hospital + historia», NO un nombre suelto. Un nombre sin nada clínico alrededor (p. ej.
    monitorizar su nombre público en redes) es legítimo y no era el bug de @MrHydeDev, que
    llevaba PII dura o un relato clínico completo. Regla aplicada aquí:

      1. Identificador DURO (email, DNI/NIE, teléfono, NHC) → crudo SIEMPRE, con o sin nombre:
         por sí solo ya ata el texto a una persona.
      2. Nombre (de {{TITULAR}} o deny-list) + contenido clínico/genómico → crudo: eso SÍ es el
         relato identificable que describe N2.
      3. Nombre SOLO, sin identificador duro ni clínico → NO crudo (sigue siendo `sensible` vía
         `clasificar()`, así que la caída seguirá a `claude` para ese caso, como antes).

    NO mira los términos vetados del muro («vacuna», «{{CONTACTO}}»...): es una regla de qué se
    PUBLICA, no de quién es el paciente.

    Usa `enruta.elegir()` para decidir que el contenido crudo identificable (N2) solo puede ir
    a `local`, nunca a `claude` — plan `plan-enrutado-crudo-solo-local-y-gate-por-check`,
    13-sep-26.

    FAIL-CLOSED igual que `clasificar()`: texto vacío/no-string → False (nada que evaluar, no
    hay excepción posible más abajo); una excepción real de las regex ya compiladas no debería
    pasar, pero por si acaso se asume crudo (la rama conservadora)."""
    if not isinstance(texto, str) or not texto.strip():
        return False, "vacío"
    try:
        norm, low = _normalizar(texto)
        for rx, etq in _ID_DURO:
            if rx.search(norm):
                return True, "PII (%s)" % etq
        tiene_nombre = bool(_NOMBRE_TITULAR.search(norm)) or bool(
            set(re.findall(r"[a-záéíóúñ]+", low)) & seg._NOMBRES_DENY)
        if tiene_nombre:
            es_clin, etq_clin = _clinico_o_genomico(norm)
            if es_clin:
                return True, "nombre + dato clínico/genómico (%s)" % etq_clin
        return False, "sin identificador directo (nombre solo o sin PII: puede seguir siendo " \
                      "sensible vía clasificar())"
    except Exception as e:
        return True, "identificador_directo reventó (%r): asumo crudo" % e


def egress_cientifico(texto, *, destino="buscador-ingeniero"):
    """(ok, motivo) para CONSULTAS de descubrimiento ingeniero a un buscador externo (radar de
    literatura). Política DISTINTA del muro público: una búsqueda de literatura PUEDE usar términos
    científicos genéricos (clase de tratamiento, nombre de gen, tipo de tumor) — eso NO identifica a
    {{TITULAR}} — pero NUNCA un IDENTIFICADOR de paciente: su nombre, contactos de terceros, o una HUELLA
    genómica específica (variante/coordenada/genotipo/HLA/rsID/citobanda). Reusa los detectores del
    borde; NO aplica el veto público (vacuna/neoantíg) porque la ciencia genérica es legítima. fail-closed."""
    def fin(ok, motivo):
        _sellar({"evento": "egress_cientifico", "destino": destino, "permitido": ok,
                 "motivo": motivo, "nivel": "allow" if ok else "deny",
                 "sello": hashlib.sha256((texto if isinstance(texto, str) else "").encode("utf-8"))
                 .hexdigest()[:16]})
        return ok, motivo
    if not isinstance(texto, str) or not texto.strip():
        return fin(False, "vacío")
    if halted():
        return fin(False, "HALT activo")
    norm, low = _normalizar(texto)
    if _NOMBRE_TITULAR.search(norm):
        return fin(False, "PII (nombre de {{TITULAR}})")
    if set(re.findall(r"[a-záéíóúñ]+", low)) & seg._NOMBRES_DENY:
        return fin(False, "nombre propio en deny-list")
    for rx, etq in ((_RE_EMAIL, "email"), (_RE_DNI, "DNI/NIE"), (_RE_PHONE, "teléfono"),
                    (_RE_NHC, "NHC / historia clínica")):
        if rx.search(norm):
            return fin(False, "PII (%s)" % etq)
    for rx, etq in ((_RE_HLA, "HLA"), (_RE_RS, "rsID"), (_RE_CHR, "coordenada genómica"),
                    (_RE_CHR_W, "cromosoma"), (_RE_CITO, "citobanda"), (_RE_VAR, "variante"),
                    (_RE_VAR_CORTO, "variante (HGVS corto)"), (_RE_GT, "genotipo VCF"),
                    (_RE_CLIN_PCT, "cifra clínica")):
        if rx.search(norm):
            return fin(False, "huella genómica específica del paciente (%s)" % etq)
    return fin(True, "ok (ingeniero genérico, sin identificador de paciente)")


def de_identificar(texto):
    """(texto_redactado, n). Sustituye los tramos sensibles por marcadores. Utilidad para la
    'consulta protegida' (preguntar de forma genérica). NO se usa para auto-enviar a ciegas:
    el borde sigue fail-closed; un carril que quiera de-identificar y enviar debe hacerlo
    explícito y volver a pasar `clasificar`. (F0 mínimo; el tokenizado reversible es F1.)"""
    if not texto:
        return texto, 0
    n = 0
    out = texto
    for rx in (_RE_EMAIL, _RE_DNI, _RE_PHONE, _RE_HLA, _RE_RS, _RE_CHR, _RE_VAR,
               _RE_CLIN_PCT, _RE_CLIN, _NOMBRE_TITULAR):
        out, k = rx.subn("[REDACTADO]", out)
        n += k
    return out, n


# ── Confianza del destino ────────────────────────────────────────────────────────────────
def _cloud_confiados():
    """Conjunto de destinos de NUBE que un humano confió a mano (gate trazable) y NO ha revocado.
    Lee el fichero persistente. Fail-closed: ilegible → vacío (no confía nada)."""
    try:
        data = json.load(open(CLOUD_CONFIADOS, encoding="utf-8"))
    except Exception:
        return set()
    if not isinstance(data, dict):
        return set()
    return {d for d, meta in data.items()
            if isinstance(meta, dict) and not meta.get("revocado")}


def es_trusted(destino):
    """True solo si el destino es local/cleared, está en la allowlist explícita, o fue confiado a
    mano por un humano vía el gate `trust-cloud` (registrado y revocable). Fail-closed."""
    if not destino:
        return False
    return (destino in _trusted_exactos()
            or any(destino.startswith(p) for p in TRUSTED_PREFIXES)
            or destino in _cloud_confiados())


def trust_cloud(destino, *, para="sensible", quien="titular", nota=None):
    """Confía a mano un destino de NUBE para contenido sensible. ACTO HUMANO: el CLI exige
    confirmación tecleada ANTES de llamar aquí (nunca lo dispara el flujo ni contenido externo).
    Lo persiste, lo sella en la cadena hash-chained y queda revocable. Devuelve (ok, motivo).
    No admite destinos local:/cleared: (ya son trusted por suelo) ni vacíos."""
    if not destino or not isinstance(destino, str):
        return False, "destino vacío"
    if any(destino.startswith(p) for p in TRUSTED_PREFIXES):
        return False, "ese destino ya es trusted por suelo (local:/cleared:); no necesita gate"
    os.makedirs(BORDE_DIR, exist_ok=True)
    with _Lock():
        try:
            data = json.load(open(CLOUD_CONFIADOS, encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        data[destino] = {"para": str(para), "quien": str(quien),
                         "nota": (str(nota)[:200] if nota else None),
                         "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "revocado": False}
        tmp = CLOUD_CONFIADOS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CLOUD_CONFIADOS)
    _sellar({"evento": "trust_cloud", "destino": destino, "para": str(para),
             "quien": str(quien), "nivel": "info"})
    return True, "nube confiada (acto humano, trazado y revocable): %s" % destino


def revoke_cloud(destino):
    """Revoca una nube confiada a mano: vuelve a fail-closed para ese destino. Acto humano,
    sellado en la cadena. (Re-confiarla luego = nuevo `trust_cloud` deliberado.)"""
    if not destino:
        return False, "destino vacío"
    with _Lock():
        try:
            data = json.load(open(CLOUD_CONFIADOS, encoding="utf-8"))
        except Exception:
            data = {}
        if not isinstance(data, dict) or destino not in data:
            return False, "esa nube no estaba confiada"
        data[destino]["revocado"] = True
        data[destino]["ts_revocado"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        tmp = CLOUD_CONFIADOS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CLOUD_CONFIADOS)
    _sellar({"evento": "revoke_cloud", "destino": destino, "nivel": "info"})
    return True, "nube revocada (vuelve a fail-closed): %s" % destino


# ── HALT / revocación ────────────────────────────────────────────────────────────────────
def halted():
    return any(os.path.exists(h) for h in HALT_FILES)


def _load_revocadas():
    try:
        return set(json.load(open(REVOCADAS, encoding="utf-8")))
    except Exception:
        return set()


def revocada(sesion):
    return bool(sesion) and sesion in _load_revocadas()


def revocar(sesion):
    """Revoca una sesión/intención (persistente, anti-replay: no se puede des-revocar aquí)."""
    if not sesion:
        return False
    os.makedirs(BORDE_DIR, exist_ok=True)
    r = _load_revocadas()
    r.add(sesion)
    tmp = REVOCADAS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(r), f, ensure_ascii=False)
    os.replace(tmp, REVOCADAS)
    _sellar({"evento": "revocacion", "sesion": sesion, "nivel": "info"})
    return True


# ── Canarios ─────────────────────────────────────────────────────────────────────────────
def _canarios():
    env = os.environ.get("BTP_CANARIOS", "")
    vals = set(x for x in env.split(":") if x)
    try:
        vals |= set(json.load(open(CANARIO_FILE, encoding="utf-8")))
    except Exception:
        pass
    return vals


def hay_canario(texto):
    """Devuelve el canario que aparece en `texto`, o None. Un canario en una salida = el
    secreto se está fugando (aunque 'transformado'): exfiltración."""
    if not texto:
        return None
    for c in _canarios():
        if c and c in texto:
            return c
    return None


# ── Traza append-only hash-chained (metadatos, NUNCA el contenido) ───────────────────────
class _Lock:
    def __enter__(self):
        os.makedirs(BORDE_DIR, exist_ok=True)
        self._f = open(LOCK_FILE, "w")
        fcntl.flock(self._f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        try:
            fcntl.flock(self._f, fcntl.LOCK_UN)
            self._f.close()
        except Exception:
            pass


def _ledger_path(d=None):
    d = d or datetime.now().strftime("%Y-%m-%d")
    return os.path.join(BORDE_DIR, "ledger-%s.jsonl" % d)


def _read_head():
    try:
        seq, h = open(HEAD_FILE, encoding="utf-8").read().strip().split(" ", 1)
        return int(seq), h
    except Exception:
        return 0, "GENESIS"


def _hash_rec(rec):
    return hashlib.sha256(json.dumps(rec, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def _sellar(evento):
    """Sella `evento` (dict de METADATOS) en la cadena. Devuelve el hash. Best-effort: un
    fallo de E/S no debe tumbar una decisión de seguridad, pero deja rastro en stderr."""
    try:
        with _Lock():
            seq, prev = _read_head()
            seq += 1
            rec = dict(evento)
            rec["seq"] = seq
            rec["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            rec["prev"] = prev
            rec["hash"] = _hash_rec({k: rec[k] for k in rec if k != "hash"})
            with open(_ledger_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            tmp = HEAD_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("%d %s" % (seq, rec["hash"]))
            os.replace(tmp, HEAD_FILE)
            return rec["hash"]
    except Exception as e:
        sys.stderr.write("borde: no pude sellar la traza (%r)\n" % e)
        return None


def verificar_cadena():
    """(ok, detalle). Re-camina TODAS las trazas en orden y comprueba: secuencia contigua
    (sin saltos = sin borrados), enlace prev correcto, y hash recalculado. Detecta borrado,
    reordenado y alteración. Devuelve (False, motivo) al primer fallo."""
    files = sorted(f for f in os.listdir(BORDE_DIR) if f.startswith("ledger-")) \
        if os.path.isdir(BORDE_DIR) else []
    prev, esperado = "GENESIS", 1
    total = 0
    for fn in files:
        for ln in open(os.path.join(BORDE_DIR, fn), encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            rec = json.loads(ln)
            if rec.get("seq") != esperado:
                return False, "salto de secuencia en seq=%s (esperaba %d) — posible borrado" % (
                    rec.get("seq"), esperado)
            if rec.get("prev") != prev:
                return False, "enlace roto en seq=%d (prev no coincide)" % esperado
            h = _hash_rec({k: rec[k] for k in rec if k != "hash"})
            if h != rec.get("hash"):
                return False, "sello alterado en seq=%d" % esperado
            prev, esperado, total = rec["hash"], esperado + 1, total + 1
    return True, "cadena íntegra (%d eventos)" % total


# ── Puerta central de EGRESS ─────────────────────────────────────────────────────────────
def egress_check(texto, *, destino="externo", sesion=None, intencion=None, escalar=False,
                 consulta=False):
    """Veredicto(permitido, motivo, sensibilidad, alarma). La ÚNICA puerta por la que el
    contenido sale hacia un cerebro externo. Orden fail-closed:
      0) HALT activo → DENY.   1) vacío → DENY.   2) sesión revocada → DENY.
      3) canario en el texto → DENY + ALARMA (exfiltración).
      4) sensible y destino NO trusted → DENY.   5) en otro caso → ALLOW.
    Cada resultado se sella en la traza (metadatos + sello del contenido, nunca el contenido).
    `escalar=True` dispara código rojo si salta un canario (producción; los tests lo dejan en
    False). `consulta=True`: el texto es una búsqueda que sale tal cual (lo pide el enrutador),
    y se clasifica con `clasificar_consulta`; todo lo demás, igual."""
    def fin(permitido, motivo, sens, alarma=False):
        _sellar({"evento": "egress", "destino": destino, "sesion": sesion,
                 "intencion": intencion, "permitido": permitido, "motivo": motivo,
                 "sensibilidad": sens, "alarma": alarma,
                 "sello": hashlib.sha256(
                     (texto if isinstance(texto, str) else "").encode("utf-8")).hexdigest()[:16],
                 "nivel": "ALARMA" if alarma else ("deny" if not permitido else "allow")})
        if alarma and escalar:
            try:
                import codigo_rojo
                codigo_rojo.trigger("Canario detectado en egress",
                                    "El borde bloqueó una salida hacia %s que contenía un "
                                    "canario sembrado: posible exfiltración." % destino)
            except Exception as e:
                sys.stderr.write("borde: no pude escalar a código rojo (%r)\n" % e)
        return Veredicto(permitido, motivo, sens, alarma)

    if not isinstance(texto, str):          # fail-closed en el BORDE, no en el llamante (m1)
        return fin(False, "tipo inválido (se esperaba texto)", "desconocida")
    if halted():
        return fin(False, "HALT activo", "desconocida")
    if not texto.strip():
        return fin(False, "vacío (nada que enviar)", "vacío")
    if revocada(sesion):
        return fin(False, "sesión revocada", "desconocida")
    can = hay_canario(texto)
    if can:
        return fin(False, "canario detectado (exfiltración)", "sensible", alarma=True)
    sensible, motivo = clasificar_consulta(texto) if consulta else clasificar(texto)
    sens = "sensible" if sensible else "limpio"
    if sensible and not es_trusted(destino):
        return fin(False, "dato sensible a destino no confiable: %s" % motivo, sens)
    return fin(True, "ok", sens)


def permitido(texto, *, destino="externo", **kw):
    """Atajo booleano sobre egress_check (para carriles que solo quieren un sí/no)."""
    return egress_check(texto, destino=destino, **kw).permitido


# El enrutador (`enruta.ejecutar`) marca así la llamada cuando lo que sale es una BÚSQUEDA.
# Solo quita el embargo de palabras: PII, clínico, genómico, «{{CONTACTO}}» y canarios siguen igual.
ENV_CONSULTA = "BTP_EGRESS_CONSULTA"
# Solo los carriles de BÚSQUEDA heredan el modo consulta; drive, chatgpt, elicit… nunca.
DESTINOS_CONSULTA = ("grok", "perplexity")


def guard_cli(texto, destino, *, etiqueta=None):
    """Para los CLIs de carriles externos (grok/glm/perplexity/chatgpt/gemini…): devuelve True
    si el contenido puede salir; si no, escribe el bloqueo a STDERR (no contamina stdout, que
    los consumidores parsean) y devuelve False. Uso: `if not borde.guard_cli(q, "grok"): return`.

    escalar=True: un canario en la salida de un carril de producción = exfiltración EN VIVO
    (token sembrado que solo puede estar ahí si el secreto se está fugando) → dispara CÓDIGO
    ROJO, no solo un DENY silencioso. Falso positivo ≈ cero (match exacto de token único)."""
    consulta = os.environ.get(ENV_CONSULTA) == "1" and destino in DESTINOS_CONSULTA
    v = egress_check(texto, destino=destino, intencion=destino + (":consulta" if consulta else ""),
                     escalar=True, consulta=consulta)
    if not v.permitido:
        sys.stderr.write("🛑 BORDE: no envío a %s — %s\n" % (etiqueta or destino, v.motivo))
    return v.permitido


# ── Air-gap de embeddings ────────────────────────────────────────────────────────────────
def embedding_egress_check(modelo, destino="externo"):
    """Veredicto. SOLO permite vectorizar con modelo LOCAL en destino local. Cualquier API de
    embeddings externa se NIEGA: los vectores son invertibles (reconstrucción del texto), así
    que la de-identificación NO los protege (Albia, blindaje nº1)."""
    m = (modelo or "").lower().split("/")[-1]
    if not es_trusted(destino):
        v = Veredicto(False, "embeddings solo en destino local (air-gap): %s no es trusted"
                      % destino, "n/a", False)
    elif m not in EMBEDDINGS_LOCALES_OK:
        v = Veredicto(False, "modelo de embedding '%s' no está en la allowlist local" % modelo,
                      "n/a", False)
    else:
        v = Veredicto(True, "ok (local)", "n/a", False)
    _sellar({"evento": "embedding", "modelo": modelo, "destino": destino,
             "permitido": v.permitido, "motivo": v.motivo,
             "nivel": "allow" if v.permitido else "deny"})
    return v


# ── Válvula de declassificación (deny-by-default + tipado + presupuesto) ─────────────────
# Campos que PUEDEN llevar información derivada hacia fuera, cada uno con su validador de forma.
# deny-by-default: lo que no esté aquí, no sale. El valor además debe seguir siendo LIMPIO
# (la transformación tiene que haber lavado de verdad el dato) y sin canarios.
def _es_enum(opciones):
    return lambda v: isinstance(v, str) and v in opciones


def _es_score(v):
    return isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.0


def _es_texto_corto(v):
    return isinstance(v, str) and 0 < len(v) <= 500


CAMPOS_PERMITIDOS = {
    "veredicto": _es_enum({"si", "no", "revisar", "aprobado", "rechazado"}),
    "score": _es_score,
    "estado": _es_enum({"en_curso", "hecho", "esperando_ok", "bloqueado"}),
    "resumen_publico": _es_texto_corto,
}
PRESUPUESTO_REVELACION = int(os.environ.get("BTP_BORDE_PRESUPUESTO", "20"))  # por sesión
_revelaciones = {}  # sesión -> nº (en proceso; el presupuesto duro vive en este runtime)


def declasificar(campo, valor, *, sesion="default"):
    """Veredicto. Deja salir un valor derivado SOLO si: (a) el campo está en la allowlist,
    (b) el valor cumple su tipo/forma, (c) sigue siendo LIMPIO y sin canario (no relava un
    secreto), (d) no se agotó el presupuesto de revelación de la sesión."""
    def fin(ok, motivo, alarma=False):
        _sellar({"evento": "declasificar", "campo": campo, "sesion": sesion,
                 "permitido": ok, "motivo": motivo,
                 "nivel": "ALARMA" if alarma else ("allow" if ok else "deny")})
        return Veredicto(ok, motivo, "n/a", alarma)

    validador = CAMPOS_PERMITIDOS.get(campo)
    if validador is None:
        return fin(False, "campo no permitido (deny-by-default): %s" % campo)
    if not validador(valor):
        return fin(False, "valor no cumple el tipo/forma de '%s'" % campo)
    if isinstance(valor, str):
        if hay_canario(valor):
            return fin(False, "canario en el valor declasificado (exfiltración)", alarma=True)
        sensible, motivo = clasificar(valor)
        if sensible:
            return fin(False, "el valor sigue siendo sensible (%s): la transformación no lo "
                              "lavó" % motivo)
    if _revelaciones.get(sesion, 0) >= PRESUPUESTO_REVELACION:
        return fin(False, "presupuesto de revelación agotado para la sesión")
    _revelaciones[sesion] = _revelaciones.get(sesion, 0) + 1
    return fin(True, "ok")


# ── CLI ──────────────────────────────────────────────────────────────────────────────────
def _selftest():
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(REPO, "tests", "test_borde.py")])
    return r.returncode


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "check":
        destino, sesion = "externo", None
        rest = argv[1:]
        if "--destino" in rest:
            i = rest.index("--destino"); destino = rest[i + 1]; rest = rest[:i] + rest[i + 2:]
        if "--sesion" in rest:
            i = rest.index("--sesion"); sesion = rest[i + 1]; rest = rest[:i] + rest[i + 2:]
        v = egress_check(" ".join(rest), destino=destino, sesion=sesion)
        print(("✅ ALLOW" if v.permitido else "🛑 DENY") + " — %s [%s]" % (v.motivo, v.sensibilidad))
        return 0 if v.permitido else 3
    if cmd == "verify":
        ok, det = verificar_cadena()
        print(("✅ " if ok else "❌ ") + det)
        return 0 if ok else 1
    if cmd == "revocar":
        if len(argv) < 2:
            print("uso: borde.py revocar <sesion>"); return 2
        print("revocada:", argv[1] if revocar(argv[1]) else "(error)")
        return 0
    if cmd == "trust-cloud":
        # ACTO HUMANO: confiar una nube para contenido sensible. Exige confirmación TECLEADA
        # (anti-inyección: jamás lo dispara un flujo ni contenido externo). --para = uso, --yes
        # salta la confirmación SOLO para un humano en script consciente (no para el lazo).
        rest = argv[1:]
        para = "sensible"
        if "--para" in rest:
            i = rest.index("--para"); para = rest[i + 1]; rest = rest[:i] + rest[i + 2:]
        auto = "--yes" in rest
        rest = [a for a in rest if a != "--yes"]
        destino = rest[0] if rest else ""
        if not destino:
            print("uso: borde.py trust-cloud <destino> [--para sensible] [--yes]"); return 2
        if any(destino.startswith(p) for p in TRUSTED_PREFIXES):
            print("🛑 '%s' ya es trusted por suelo (local:/cleared:): no necesita gate." % destino)
            return 3
        if not auto:
            print("⚠️  Vas a CONFIAR la nube '%s' para contenido %s." % (destino, para))
            print("    Esto deja que datos SENSIBLES salgan hacia ese destino externo. Es un acto")
            print("    tuyo, queda registrado y es revocable. Escribe SÍ para confirmar:")
            try:
                resp = input("> ").strip().lower()
            except EOFError:
                resp = ""
            if resp not in ("sí", "si", "s", "yes", "y"):
                print("cancelado (no se confió nada)."); return 1
        ok, motivo = trust_cloud(destino, para=para)
        print(("✅ " if ok else "🛑 ") + motivo)
        return 0 if ok else 3
    if cmd == "revoke-cloud":
        if len(argv) < 2:
            print("uso: borde.py revoke-cloud <destino>"); return 2
        ok, motivo = revoke_cloud(argv[1])
        print(("✅ " if ok else "🛑 ") + motivo)
        return 0 if ok else 1
    if cmd == "status":
        seq, h = _read_head()
        ok, det = verificar_cadena()
        print("HALT: %s | eventos: %d | cadena: %s | trusted-exactos: %d | nubes-confiadas: %d "
              "| canarios: %d" % (
                  halted(), seq, "OK" if ok else "ROTA(%s)" % det,
                  len(_trusted_exactos()), len(_cloud_confiados()), len(_canarios())))
        return 0
    if cmd == "selftest":
        return _selftest()
    print("uso: borde.py check|verify|revocar|status|trust-cloud|revoke-cloud|selftest")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
