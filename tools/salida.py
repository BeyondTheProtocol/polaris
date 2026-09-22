#!/usr/bin/env python3
"""tools/salida.py — ÚNICO punto de salida hacia fuera del lazo 24/7 (B3 del muro).

Principio rector (CLAUDE.md, el muro): «Nada hacia fuera sin OK explícito de {{TITULAR}}».
Aquí eso es CÓDIGO, no una instrucción al modelo: TODA entrega hacia el exterior pasa
por send(); el MÓDULO decide —no el agente— qué se entrega y qué se queda «a un clic».

Dos clases de salida:
  · REPORT  → mensaje del SISTEMA a la PROPIA {{TITULAR}} (su chat_id allowlistado). Es el
              canal de reporte del lazo. AUTÓNOMO: se entrega (si no hay HALT).
  · OUTWARD → publish / contact / pay: hacia el mundo o hacia terceros. GATE del muro:
              NUNCA se entrega en desatendido. Se deja como BORRADOR en el outbox
              («a un clic») y la entrega requiere el OK explícito de {{TITULAR}} mediante
              challenge-response con nonce del sistema (A4, fuera del alcance de B3).

Cortes (fail-closed):
  · ~/.btp.HALT  o  ~/claudecode/.HALT  presentes  → se bloquea TODO.
  · destino fuera de la allowlist del canal       → no se entrega (se draftea).
  · token / credencial ausente                    → no se entrega.
  · cualquier excepción inesperada                 → no se entrega (se draftea).

Auditoría (append-only): tools/state/outbox/audit-YYYY-MM-DD.jsonl. Cada intento deja
{ts, canal, accion, dest_hash, veredicto, motivo, n}. NO registra el cuerpo del OUTWARD
en claro (puede llevar PII); del REPORT guarda solo la longitud (n).

Outbox de borradores: tools/state/outbox/pending/<ts>-<canal>-<accion>-<id>.json
(escritura atómica tmp+os.replace). Lo revisa y entrega {{TITULAR}}.

INVARIANTE (test en tests/test_fuga.sh): este es el ÚNICO módulo del repo que habla con
telegram/gmail. Ningún otro tool importa urllib hacia api.telegram.org. El muro, además,
deniega `curl`/`wget` a esos hosts en bash, y deniega CREAR/SOBRESCRIBIR un .py (Write/Edit
por B1, y cp/mv/ln/tee/touch por B3) → el lazo no puede fabricar un emisor alternativo: la
única vía al exterior es este script, que el lazo tampoco puede editar.
"""
import hashlib
import json
import os
import random
import re
import socket
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secrets import get as get_secret
from _lexico_publico import revisar as _revisar_publico  # H3: avisa de fuga en OUTWARD

HOME = os.path.expanduser("~")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # este fichero vive en tools/
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
OUTBOX = os.path.join(STATE, "outbox")
PENDING = os.path.join(OUTBOX, "pending")
TG_OFFSET = os.path.join(STATE, "telegram", "offset.json")  # cursor de getUpdates (inbound)
SECRETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".telegram_secrets.json")
NOTIF_CFG = os.path.join(STATE, "notif", "config.json")  # avisos del lazo (silencio nocturno); OPT-IN

# Kill-switches: el de dentro del repo (lo conoce el muro) y el de fuera (A1: un agente
# `git` operando en el repo NO puede tocar ~/.btp.HALT). Cualquiera presente → paramos.
#
# `BTP_HALT_FILES` (14-jul-2026): igual que ya hacen borde.py, cost_guard.py, mcp_server.py y
# cosecha_whatsapp.py. Sin esto, salida.py era el ÚNICO que no se podía aislar del HALT REAL de la
# máquina — y en el Air hay uno permanente y deliberado ("mini primario, Air = cliente remoto"), así
# que cualquier test que quisiera comprobar que la boca NO está muda se topaba con un HALT global y
# no podía afirmar nada. Un test que no puede fallar tampoco puede proteger.
HALT_FILES = tuple(
    p for p in (os.environ.get("BTP_HALT_FILES") or "").split(":") if p
) or (os.path.join(HOME, ".btp.HALT"), os.path.join(REPO, ".HALT"))

# Acciones. REPORT = sistema→{{TITULAR}} (autónomo). OUTWARD = hacia el mundo (gate del muro).
REPORT = "report"
OUTWARD = ("publish", "contact", "pay")
ACTIONS = (REPORT,) + OUTWARD

CHANNELS = ("telegram", "instagram")  # gmail/x se añadirán aquí cuando exista una vía de ENVÍO real.


# ─────────────────────────────────────────────────────────────────────────────
# CASA DE ESTILO (capa determinista de FORMATO sobre TODO REPORT a {{TITULAR}}).
#
# Una sola función, _casa_estilo, colgada en el embudo send() (action == REPORT) y en el
# caption de report_file_to_titular, normaliza SIEMPRE el formato de lo que llega al chat de
# {{TITULAR}}, lo escriba quien lo escriba: fuera markdown crudo, guion largo de muletilla a
# puntuación, viñetas a «•» (solo a inicio de línea), jerga/IDs internos a lenguaje llano,
# y partido si pasa del tope de Telegram (sin perder texto). CONSERVA emojis y el 💜.
#
# Diseño (SPEC-SALIDA.md + correcciones de VERIF.md, 22/6/26):
#   · FAIL-OPEN ABSOLUTO: ante cualquier excepción, devuelve el texto crudo. Nunca bloquea.
#   · IDEMPOTENTE: aplicarla dos veces da el mismo resultado.
#   · DETERMINISTA y SIN RED: solo stdlib (re, unicodedata), como _lexico_publico.
#   · El TONO no se garantiza aquí (eso es la guía que adoptan los agentes); esta capa
#     garantiza el FORMATO. Honestidad de la SPEC §7.
#   · INTOCABLE: alerta_critica (CÓDIGO ROJO) NO pasa por aquí (VERIF AGUJERO 1). Las
#     reacciones, el HALT, el silencio nocturno y el scrubber OUTWARD tampoco se tocan.
#
# `voz` (decisión de {{TITULAR}}): "calida" (defecto) asegura un 💜 de cierre donde encaje;
# "sobria" = sin 💜, escueto (para avisos secos: healthcheck, termómetro, etc.).
# ─────────────────────────────────────────────────────────────────────────────

# Topes de Telegram. 3900 deja margen bajo el 4096 real (sitio para el pie "(1/3)").
_TG_TOPE_TEXTO = 3900
# El caption se trunca (no se parte). 997 + "…" = 1000 → no lo re-recorta el deliverer.
_TG_TOPE_CAPTION = 1000
_TG_CAPTION_UTIL = _TG_TOPE_CAPTION - 1   # 999; con "…" cabe holgado bajo 1000

# Viñeta canónica (U+2022). Hoy flush_silencio usa "·" (punto medio): se unifica a "•".
_VINETA = "•"

# Jerga interna → lenguaje llano. UNA sola fuente (como _lexico_publico.LEXICO). Conservador:
# solo patrones con bajo riesgo de falso positivo. Genes/identificadores legítimos
# (MET, KIT, chat_id, kb.py) NO entran. Ante la duda, no tocar (SPEC §3.3.1).
# (patrón regex con límites de palabra, reemplazo). Orden: primero los que borran ruido.
_JERGA = [
    # IDs / refs / hashes entre paréntesis → se borran (ruido de logs).
    (re.compile(r"\s*\(ref[:\s]+[0-9a-fA-F]{6,}\)", re.I), ""),
    (re.compile(r"\s*\bref[:\s]+[0-9a-fA-F]{6,}\b", re.I), ""),
    (re.compile(r"\s*\bid=[0-9a-fA-F]{6,}\b", re.I), ""),
    (re.compile(r"\s*\bjob\s+[0-9a-fA-F]{6,}\b", re.I), ""),
    # Códigos de retorno / HTTP sueltos al final de línea → se borran.
    (re.compile(r"\s*\brc=[0-9]+\b"), ""),
    (re.compile(r"\s*\bHTTP\s+[0-9]{3}\b"), ""),
    # Jerga de ingeniería → su versión llana (límites de palabra). Mapeamos a la palabra
    # SIN artículo, así "El dispatcher"→"El motor" y "el dispatcher"→"el motor" conservan
    # la mayúscula sin dejar un doble artículo ("el el motor").
    (re.compile(r"\bdispatcher\b", re.I), "motor"),
    (re.compile(r"\bchoke-point\b", re.I), "única salida"),
    (re.compile(r"\bcost_guard\b", re.I), "control de gasto"),
    (re.compile(r"\bdead-man\b", re.I), "vigía"),
]


# ─────────────────────────────────────────────────────────────────────────────
# META-FUGA (bug real 3/7/26): un sub-agente (p. ej. Vega en la pasada de
# recordatorio) que no pudo ejecutar una tool en su sesión (Bash no disponible)
# a veces NARRA el problema técnico ("el sandbox bloquea…", "presento el
# recordatorio que habría enviado…") en vez de limitarse a escribir el mensaje
# que {{TITULAR}} debe leer. El prompt YA lo prohíbe (asistente.md + el plist del
# recordatorio) pero el modelo se lo salta a veces → esta es la barrera
# DETERMINISTA, cinturón y tirantes sobre el prompt.
#
# Estrategia: LÍNEA a línea (no fragmentos de oración con regex frágiles, que
# arriesgan cortes raros a mitad de frase). Si una línea completa contiene un
# patrón de meta-fuga (_META_LINEA) se DESCARTA esa línea entera. Si una línea
# es la cabecera de un bloque de "resumen técnico" (_META_CABECERA_BLOQUE), se
# descarta ella y TODO el bloque que la sigue (tabla cruda incluida) hasta la
# próxima línea en blanco que no enlace con otra fila de tabla — es jerga de
# proceso que {{TITULAR}} no necesita ver, nunca la decisión/recordatorio en sí.
# Se aplica ANTES de _quitar_markdown (§_casa_estilo) para descartar la tabla
# cruda como bloque, en vez de dejarla sobrevivir aplanada en una línea suelta.
# Lista CERRADA y conservadora, igual que _JERGA: ante la duda, no tocar.
# ─────────────────────────────────────────────────────────────────────────────
_META_LINEA = [
    re.compile(r"\bsandbox\b", re.I),
    re.compile(r"\bentorno\s+de\s+ejecuci[oó]n\b", re.I),
    re.compile(r"\bbloquea\s+la\s+ejecuci[oó]n\b", re.I),
    re.compile(r"\bno\s+teng[oa]\s+acceso\s+a\s+Bash\b", re.I),
    re.compile(r"\bhabr[ií]a\s+enviado\b", re.I),
    re.compile(r"\bpresento\s+el\s+recordatorio\b", re.I),
    re.compile(r"\bsalida\.report(_file)?_to_titular\b", re.I),
    re.compile(r"(?<![\w.])salida\.py\b"),
    re.compile(r"\btools/[A-Za-z0-9_./-]+\.(py|sh|json)\b"),
    re.compile(r"\b_PRIVADO_[A-Za-z0-9_]*\b"),
    re.compile(r"\bGestion/[A-Za-z0-9_./-]+\b"),
]

# Cabecera de un bloque "resumen técnico" (tabla/lista de estado interno para
# referencia, no para {{TITULAR}}): se descarta la línea y el bloque que la sigue
# hasta la próxima línea en blanco.
_META_CABECERA_BLOQUE = re.compile(
    r"resumen\s+del\s+barrido|para\s+referencia\)?\s*:?\s*$", re.I)

# Enums crudos de tools/seguimiento.py que a veces se cuelan tal cual en la
# prosa → su versión llana. Límites de palabra; lista cerrada y conservadora.
_ESTADOS_CRUDOS = [
    (re.compile(r"\ben_curso\b", re.I), "en marcha"),
    (re.compile(r"\bpor_confirmar\b", re.I), "por confirmar"),
    (re.compile(r"\bquien_espera\b", re.I), "quién lo espera"),
]


def _es_fila_tabla_cruda(ln):
    """True si la línea es una fila de tabla markdown "| a | b |" o su separador
    "|---|---|" (crudos, ANTES de que _quitar_markdown los aplane)."""
    return bool(re.match(r"^[ \t]*\|.*\|[ \t]*$", ln))


def _quitar_meta_fuga(t):
    """Borra líneas de meta-comentario sobre el propio sistema (sandbox, nombres
    de herramientas, rutas de fichero) y bloques de "resumen técnico" (cabecera +
    la tabla/lista que la sigue); convierte enums de estado crudos a lenguaje
    llano. Conservador: opera LÍNEA a línea (nunca a mitad de frase) para no
    dejar cabos sueltos raros. NUNCA toca listas con viñetas/emojis normales ni
    negritas de énfasis. Se aplica ANTES de _quitar_markdown (así el bloque de
    tabla cruda que sigue a la cabecera "resumen técnico" se descarta como
    unidad, en vez de sobrevivir aplanado en una línea suelta)."""
    lineas = t.split("\n")
    n = len(lineas)
    out = []
    saltando_bloque = False
    i = 0
    while i < n:
        ln = lineas[i]
        if saltando_bloque:
            # El bloque sigue mientras la línea tenga contenido, O sea una tabla
            # cruda, O sea una línea en blanco que enlaza con una fila de tabla
            # más adelante (cabecera de tabla separada por un blanco del título).
            if ln.strip() == "":
                siguiente = lineas[i + 1] if i + 1 < n else ""
                if _es_fila_tabla_cruda(siguiente):
                    i += 1
                    continue
                saltando_bloque = False
                i += 1
                continue
            i += 1
            continue
        if _META_CABECERA_BLOQUE.search(ln):
            saltando_bloque = True
            i += 1
            continue
        if any(rx.search(ln) for rx in _META_LINEA):
            i += 1
            continue
        out.append(ln)
        i += 1
    t = "\n".join(out)
    for rx, repl in _ESTADOS_CRUDOS:
        t = rx.sub(repl, t)
    return t


def _quitar_markdown(t):
    """Quita la sintaxis markdown que Telegram NO renderiza (se vería en crudo)."""
    # Bloques de código ```...``` → el contenido, sin los backticks ni la etiqueta de lenguaje.
    t = re.sub(r"```[^\n`]*\n?(.*?)```", lambda m: m.group(1), t, flags=re.S)
    # Código en línea `x` → x.
    t = re.sub(r"`([^`\n]+)`", r"\1", t)
    # Enlaces [texto](url) → "texto (url)" (por defecto CONSERVA la url; VERIF §enlaces).
    t = re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)", r"\1 (\2)", t)
    # Cabeceras de markdown a inicio de línea: "# Título" / "## Sub" → "Título" (sin #).
    t = re.sub(r"^[ \t]*#{1,6}[ \t]+", "", t, flags=re.M)
    # Cita "> algo" a inicio de línea → "algo".
    t = re.sub(r"^[ \t]*>[ \t]?", "", t, flags=re.M)
    # Negrita/cursiva: pares **x** / __x__ / *x* / _x_. Solo PARES envolventes, con
    # límites razonables, para no comerse un "_" interno (chat_id) ni un "*" suelto.
    t = re.sub(r"\*\*(\S(?:.*?\S)?)\*\*", r"\1", t)
    t = re.sub(r"__(\S(?:.*?\S)?)__", r"\1", t)
    t = re.sub(r"(?<![\w*])\*(\S(?:[^*\n]*?\S)?)\*(?![\w*])", r"\1", t)
    t = re.sub(r"(?<![\w_])_(\S(?:[^_\n]*?\S)?)_(?![\w_])", r"\1", t)
    # Reglas horizontales (--- / *** / ___) en su propia línea → fuera.
    t = re.sub(r"^[ \t]*([-*_])\1{2,}[ \t]*$", "", t, flags=re.M)
    # Tablas: una fila "| a | b |" → "• a — b" aplanada legible; la fila separadora |---| fuera.
    def _fila_tabla(m):
        celdas = [c.strip() for c in m.group(0).strip().strip("|").split("|")]
        celdas = [c for c in celdas if c and not re.fullmatch(r":?-{2,}:?", c)]
        if not celdas:
            return ""
        return _VINETA + " " + ", ".join(celdas)
    t = re.sub(r"^[ \t]*\|.*\|[ \t]*$", _fila_tabla, t, flags=re.M)
    # Limpia líneas que quedaron vacías tras tirar una regla/separador de tabla.
    t = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", t)
    return t


# Firma de cierre: ÚLTIMA línea no vacía del texto «— Nombre» (guion largo/medio + UNA sola
# palabra, con un 💜 opcional detrás — para que sobreviva idéntica a una 2ª pasada, VERIF
# idempotencia) es quien manda el mensaje ("— Vega"), NO una viñeta de lista ni un inciso.
# Se extrae ANTES de _normaliza_vinetas/_guion_largo (que la tratarían como tal) y se
# reintegra intacta al final del pipeline (antes del 💜 de cierre).
_FIRMA_RX = re.compile(r"^[ \t]*[—–][ \t]+\S+[ \t]*(?:💜[ \t]*)?$")


def _extrae_firma(t):
    """Si la ÚLTIMA línea no vacía es una firma «— Nombre», la separa. Devuelve
    (cuerpo_sin_firma, firma_o_None). No toca nada si no hay firma reconocible.

    Distingue firma de «último ítem de una lista con guion»: una firma real cierra el
    mensaje SOLA, precedida de una línea en blanco (o es la única línea del texto). Si la
    línea anterior no vacía es OTRO guion de lista (sin blanco entre medias), es una lista
    consecutiva, no una firma → se deja intacta para _normaliza_vinetas."""
    lineas = t.split("\n")
    idx = next((i for i in range(len(lineas) - 1, -1, -1) if lineas[i].strip()), None)
    if idx is None or not _FIRMA_RX.match(lineas[idx]):
        return t, None
    if idx > 0 and lineas[idx - 1].strip() != "":
        return t, None   # línea anterior no vacía y no-blanco → parte de una lista, no firma
    firma = lineas[idx].strip()
    resto = lineas[:idx] + lineas[idx + 1:]
    return "\n".join(resto).rstrip("\n"), firma


def _normaliza_vinetas(t):
    """Convierte marcadores de viñeta a «•» SOLO a inicio de línea (re.MULTILINE).
    NUNCA un replace global: un «·»/«-» a media frase NO se toca (VERIF AGUJERO 2)."""
    # "— ítem" / "– ítem" / "- ítem" / "* ítem" / "· ítem" / "• ítem" a inicio de línea → "• ítem".
    t = re.sub(r"^([ \t]*)[-*•·–—][ \t]+", r"\1" + _VINETA + " ", t, flags=re.M)
    return t


def _guion_largo(t):
    """El guion largo (—) y el medio (–) como INCISO dentro de la frase → coma.
    (Las viñetas a inicio de línea ya las trató _normaliza_vinetas antes que esto.)"""
    # " — " (rodeado de espacios) → ", " (coma + espacio).
    t = re.sub(r"[ \t]+[—–][ \t]+", ", ", t)
    # Un "—"/"–" pegado raro que quede suelto → coma.
    t = t.replace("—", ", ").replace("–", ", ")
    return t


def _quitar_jerga(t):
    """Borra/suaviza la jerga interna (lista cerrada _JERGA). Conservador."""
    for rx, repl in _JERGA:
        t = rx.sub(repl, t)
    # Emoji-semáforo huérfano al inicio de línea (🟢/🟡/⚙️) que quedó sin texto-código
    # detrás tras limpiar: solo si va pegado a inicio y le sigue espacio+texto, se deja el
    # texto. Conservador: el 🔴 NUNCA se toca (es señal de socorro, no decoración).
    t = re.sub(r"^([ \t]*)[🟢🟡⚙️][ \t]+", r"\1", t, flags=re.M)
    return t


def _muletillas_en(t):
    """Sustituye muletillas/fragmentos en inglés de mensajes de sistema. Lista cerrada.
    NO traduce prosa (eso es responsabilidad del tono, SPEC §3.4)."""
    t = re.sub(r"^[ \t]*Done[.!]?[ \t]*$", "", t, flags=re.M)
    t = re.sub(r"\bWarning:", "Aviso:", t)
    t = re.sub(r"\bFailed\b", "Falló", t)
    return t


def _limpia_espacios(t):
    """Recorta espacios sobrantes que dejan los pasos anteriores, sin tocar el contenido."""
    # Espacios dobles dentro de línea (no toca saltos de línea).
    t = re.sub(r"[ \t]{2,}", " ", t)
    # Espacios antes de signos de puntuación de cierre.
    t = re.sub(r"[ \t]+([,.;:!?])", r"\1", t)
    # {{CONTACTO}} duplicadas que dejan los reemplazos (",,", ", ,").
    t = re.sub(r"(,[ \t]*){2,}", ", ", t)
    # Coma pegada a otro signo de puntuación final (",.", ", .", ",;") → solo el signo:
    # lo deja la limpieza de jerga ("…, rc=0." → "…,."). Nos quedamos con el de cierre.
    t = re.sub(r",[ \t]*([.;:!?])", r"\1", t)
    # Espacio/coma al final de cada línea.
    t = re.sub(r"[ \t,]+$", "", t, flags=re.M)
    # Más de dos saltos de línea seguidos → dos.
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _tiene_corazon(t):
    return "💜" in t


def _asegura_corazon(t):
    """Voz cálida: garantiza un 💜 de cierre donde encaje. Idempotente: si ya hay uno,
    no añade otro. No lo pone en mensajes que son una alerta seca (los que llevan 🔴)."""
    if _tiene_corazon(t) or "🔴" in t:
        return t
    if not t.strip():
        return t
    return t.rstrip() + " 💜"


def _partir_telegram(t, tope=_TG_TOPE_TEXTO):
    """Parte un texto largo en trozos que caben en Telegram, SIN perder contenido y sin
    cortar a mitad de palabra/viñeta. Corta por límites naturales (línea en blanco, salto
    de línea, espacio). Devuelve lista de trozos; si hay más de uno, cada uno lleva «(i/n)».
    Idempotente para textos que ya caben (devuelve [t])."""
    t = t.rstrip()
    if len(t) <= tope:
        return [t] if t else []
    # Reservamos sitio para el pie "(99/99)\n" (peor caso ~8 chars).
    util = tope - 10
    trozos, resto = [], t
    while len(resto) > tope:
        corte = resto.rfind("\n\n", 0, util)
        if corte <= 0:
            corte = resto.rfind("\n", 0, util)
        if corte <= 0:
            corte = resto.rfind(" ", 0, util)
        if corte <= 0:
            corte = util            # sin límite natural: corte duro (texto sin espacios)
        trozos.append(resto[:corte].rstrip())
        resto = resto[corte:].lstrip()
    if resto:
        trozos.append(resto)
    n = len(trozos)
    if n > 1:
        trozos = ["%s\n(%d/%d)" % (tr, i + 1, n) for i, tr in enumerate(trozos)]
    return trozos


def _ya_partido(t):
    """True si el texto ya trae un pie de partición «(i/n)» (idempotencia del partido)."""
    return bool(re.search(r"\(\d+/\d+\)\s*$", t))


def _casa_estilo(texto, voz="calida", tipo="report"):
    """Normaliza el FORMATO de un mensaje a {{TITULAR}}. Determinista, sin red, FAIL-OPEN.

    Parámetros:
      · texto: lo que el llamante quiere que lea {{TITULAR}}.
      · voz:  "calida" (defecto) asegura 💜 de cierre donde encaje; "sobria" = sin 💜, seco.
      · tipo: "report" (cuerpo normal; lo parte send() si excede) o "caption" (pie de
              fichero: se trunca a ≤1000 con «…», NUNCA se parte).

    Devuelve SIEMPRE un str ya en casa de estilo. Ante CUALQUIER excepción, devuelve el
    texto crudo (la casa de estilo jamás puede ser el motivo de que un mensaje no salga).
    Idempotente: _casa_estilo(_casa_estilo(t)) == _casa_estilo(t).
    """
    try:
        if texto is None:
            return ""
        t = str(texto)
        # 1. Codificación: normaliza formas raras/zero-width/homoglifos accidentales SIN
        #    perder acentos (NFC conserva á, ñ; solo quita zero-width y compone).
        t = t.replace("​", "").replace("‌", "").replace("﻿", "")
        t = unicodedata.normalize("NFC", t)
        # 1b. Meta-fuga: líneas de meta-comentario sobre el propio sistema (sandbox,
        #     "habría enviado", nombres de herramientas, rutas) y bloques de "resumen
        #     técnico" (cabecera + su tabla) fuera; enums de estado crudos → llano.
        #     ANTES de aplanar markdown: así el bloque de tabla CRUDA que sigue a una
        #     cabecera de "resumen técnico" se descarta como unidad (si se aplicara
        #     después de aplanar, sobreviviría como una línea suelta "• Hilo, Plazo…").
        t = _quitar_meta_fuga(t)
        # 2. Markdown crudo fuera.
        t = _quitar_markdown(t)
        # 2b. Firma de cierre («— Nombre» como última línea) se aparta ANTES de que
        #     viñetas/guion-largo la confundan con una lista o un inciso; se reintegra
        #     intacta más abajo (paso 7b).
        t, _firma = _extrae_firma(t)
        # 3. Viñetas → «•» SOLO a inicio de línea (antes que el guion largo, para no
        #    confundir una viñeta "—" de línea con un inciso).
        t = _normaliza_vinetas(t)
        # 4. Guion largo de inciso → coma.
        t = _guion_largo(t)
        # 5. Jerga / IDs internos → llano.
        t = _quitar_jerga(t)
        # 6. Muletillas en inglés de sistema.
        t = _muletillas_en(t)
        # 7. Limpieza de espacios sobrantes que dejan los pasos.
        t = _limpia_espacios(t)
        # 7b. Reintegrar la firma de cierre, intacta.
        if _firma:
            t = (t + "\n\n" + _firma) if t else _firma
        # 8. Voz cálida: asegurar 💜 de cierre (idempotente; nunca un segundo).
        if voz == "calida":
            t = _asegura_corazon(t)
        # 9. Caption: tope duro, se trunca (no se parte). Cuida no recortar dos veces:
        #    deja sitio para "…" bajo el 1000 del deliverer (VERIF AGUJERO 3).
        if tipo == "caption" and len(t) > _TG_CAPTION_UTIL:
            t = t[:_TG_CAPTION_UTIL].rstrip() + "…"
        return t
    except Exception:
        # FAIL-OPEN: el texto crudo SIEMPRE sale.
        return "" if texto is None else str(texto)


# ─────────────────────────────────────────────────────────────────────────────
# Veredicto
# ─────────────────────────────────────────────────────────────────────────────
def _bajo_bateria_test():
    """True SOLO si estamos dentro de la BATERIA DE TESTS. Exige DOS senales independientes.

    Por que dos y no una (14-jul-2026): una env var pelada (BTP_TESTING=1) seria la MORDAZA en
    bandeja — heredable, adivinable, y hay decenas de plists inyectando BTP_*. Un `export` olvidado
    dejaria a Vega MUDA para siempre y en silencio, y callar un aviso REAL (tope de verdad, HALT,
    algo clinico) es MUCHO peor que dejar pasar un fantasma. Por eso exigimos ADEMAS que el STATE
    este desviado del canonico: TODO test aisla su estado en un tmp (tempfile.mkdtemp) y la
    produccion real (launchd/daemons) JAMAS lo hace.

    FAIL-OPEN hacia el ENVIO: si falta cualquiera de las dos senales, se entrega como siempre.

    NO cubre `alerta_critica()` A PROPOSITO: esa es la via del codigo rojo, no pasa por send() y
    debe atravesar SIEMPRE (incluso el HALT). Es una red de seguridad, no un descuido."""
    if os.environ.get("BTP_TEST_BATTERY") != "1":
        return False
    try:
        canonico = os.path.abspath(os.path.join(REPO, "tools", "state"))
        return os.path.abspath(STATE) != canonico
    except Exception:
        return False


def _origen():
    """De dónde sale ESTE proceso. Devuelve (etiqueta, es_lazo).

    Bajo launchd, macOS inyecta `XPC_SERVICE_NAME` con la etiqueta del job (com.btp.*). En una
    sesión ssh la variable NO existe; en una app de escritorio vale "0". Por eso el criterio es el
    PREFIJO `com.btp.`, no "está poblada" — comprobado en la máquina el 14-jul-2026.

    OJO: esto NO decide si se entrega. Solo ETIQUETA. Un pestillo que filtre por entorno dejaría a
    Vega MUDA el día que un plist se olvide de una variable, y callar un aviso REAL es mucho peor
    que dejar pasar uno falso."""
    xpc = os.environ.get("XPC_SERVICE_NAME") or ""
    agente = os.environ.get("BTP_AGENT") or ""
    if xpc.startswith("com.btp."):
        return (xpc + (" · " + agente if agente else ""), True)
    if agente:
        return (agente + " (fuera de launchd)", False)
    return ("sesión ssh manual" if os.environ.get("SSH_CONNECTION") else "sesión local", False)


def _result(delivered, blocked, reason, draft=None, extra=None):
    r = {"delivered": bool(delivered), "blocked": bool(blocked), "reason": reason}
    if draft:
        r["draft"] = draft
    if extra:
        r.update(extra)
    return r


def halted():
    """True si cualquiera de los kill-switches está presente (corta toda salida)."""
    return any(os.path.exists(p) for p in HALT_FILES)


def _no_llego_seguro(exc):
    """¿Es SEGURO que este fallo dejó el mensaje sin salir? Solo entonces se puede reintentar.

    POR QUÉ (12-sep-2026). El daemon `enviar-hoy` acumuló 1266 detecciones de fallo, y el mismo
    día otros dos (`calendar-sync`, `anatomia-push`) cayeron en la MISMA ventana horaria, todos
    por resolución DNS: no eran tres bugs, era la red yéndose y volviendo (la caja corre Tailscale
    y NordVPN a la vez). Sin reintento, un aviso que ella necesita se perdía por un hipo de DNS de
    dos minutos.

    El único riesgo de reintentar es DUPLICAR un aviso, así que la regla es estrecha a propósito:
    se reintenta **solo cuando la conexión ni siquiera llegó a establecerse**. Un timeout después
    de conectar NO entra (el mensaje pudo llegar y perderse la respuesta), y un `HTTPError`
    tampoco (el servidor contestó: eso es una decisión suya, no un fallo de red).

    No abre ninguna boca nueva de egress: el destino se construye igual, en la misma llamada.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return False                      # el servidor respondió: no es un fallo de transporte
    causa = getattr(exc, "reason", exc)
    if isinstance(causa, socket.gaierror):
        return True                       # el nombre no se resolvió → nunca se conectó
    if isinstance(causa, ConnectionRefusedError):
        return True                       # puerto cerrado → nunca se conectó
    if isinstance(causa, OSError) and getattr(causa, "errno", None) in (50, 51, 65, 101):
        return True                       # red caída / host inalcanzable (ENETDOWN/EHOSTUNREACH…)
    return False


def _self_chatid():
    """El chat_id allowlistado de la PROPIA {{TITULAR}} (único destino REPORT)."""
    cid = get_secret("btp-telegram-chatid", SECRETS, "chat_id")
    return str(cid).strip() if cid else None


def _dest_hash(dest):
    if not dest:
        return None
    return hashlib.sha256(str(dest).encode("utf-8")).hexdigest()[:12]


def _now_iso():
    # Hora local legible para el nombre del borrador y el log.
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ─────────────────────────────────────────────────────────────────────────────
# Auditoría + outbox
# ─────────────────────────────────────────────────────────────────────────────
def _audit(channel, action, dest, veredicto, motivo, n):
    """Apunta el intento en el log append-only del día. Nunca falla hacia el caller."""
    try:
        os.makedirs(OUTBOX, exist_ok=True)
        rec = {"ts": _now_iso(), "canal": channel, "accion": action,
               "dest_hash": _dest_hash(dest), "veredicto": veredicto,
               "motivo": motivo, "n": n}
        line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
        path = os.path.join(OUTBOX, "audit-%s.jsonl" % time.strftime("%Y-%m-%d"))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except Exception as e:
        sys.stderr.write("salida: aviso, no pude auditar: %r\n" % (e,))


def _draft(channel, action, dest, text, reason, avisos=None):
    """Escribe el borrador en el outbox (atómico) y devuelve su id. «A un clic».
    Si hay `avisos` de posible fuga (léxico/PII), se guardan en el payload para que
    {{TITULAR}} los VEA al revisar antes de aprobar."""
    os.makedirs(PENDING, exist_ok=True)
    ts = _now_iso().replace(":", "")
    sufx = _dest_hash("%s|%s|%s" % (channel, dest, text)) or "x"
    name = "%s-%s-%s-%s.json" % (ts, channel, action, sufx)
    path = os.path.join(PENDING, name)
    # Nonce del sistema (A4): lo genera el CÓDIGO, el agente nunca lo ve → un job no puede
    # fabricar su propia aprobación. La entrega real exige re-teclear este nonce.
    payload = {"creado": _now_iso(), "canal": channel, "accion": action,
               "dest": dest, "texto": text, "motivo": reason, "estado": "pendiente_OK",
               "nonce": os.urandom(5).hex()}
    if avisos:
        payload["avisos_fuga"] = list(avisos)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return name


def approve_and_deliver(draft_name, nonce):
    """Challenge-response (A4): entrega un borrador del outbox SOLO si el nonce coincide
    con el que el sistema generó, no hay HALT, y existe un canal de entrega real para esa
    acción. REPORT retenido → se entrega; OUTWARD (publish/contact/pay) → hoy NO hay canal
    de entrega → queda bloqueado (nada sale al mundo). Fail-closed en cada rama."""
    path = os.path.join(PENDING, os.path.basename(draft_name))
    if not os.path.exists(path):
        return _result(False, True, "borrador no encontrado")
    if halted():
        return _result(False, True, "HALT activo: no se entrega nada")
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        return _result(False, True, "borrador ilegible (%r)" % e)
    if not nonce or str(nonce) != str(d.get("nonce")):
        _audit(d.get("canal"), d.get("accion"), d.get("dest"), "rechazado", "nonce no coincide", len(d.get("texto") or ""))
        return _result(False, True, "nonce no coincide: no se entrega")
    canal, accion, dest, texto = d.get("canal"), d.get("accion"), d.get("dest"), d.get("texto")
    # Solo REPORT a {{TITULAR}} tiene canal de entrega hoy; OUTWARD a terceros no.
    if accion == REPORT and canal in _DELIVERERS and str(dest) == str(_self_chatid()):
        ok, info = _DELIVERERS[canal](dest, texto or "")
        if ok:
            os.makedirs(os.path.join(OUTBOX, "sent"), exist_ok=True)
            os.replace(path, os.path.join(OUTBOX, "sent", os.path.basename(path)))
            _audit(canal, accion, dest, "entregado-aprobado", info, len(texto or ""))
            return _result(True, False, "entregado (%s)" % info)
        return _result(False, True, "entrega falló (%s)" % info)
    # OUTWARD contact por Instagram: el gate del muro YA está pasado (nonce de {{TITULAR}}, arriba).
    # Validar destinatario y entregar por la única boca. Si la entrega falla (hoy: sin
    # instagram_manage_messages), el borrador NO se mueve a sent → se queda «a un clic».
    if accion == "contact" and canal == "instagram":
        if not _ig_dest_ok(dest):
            _audit(canal, accion, dest, "bloqueado", "destinatario IG no permitido", len(texto or ""))
            return _result(False, True, "destinatario de Instagram no permitido (allowlist) — no se envía")
        ok, info = _DELIVERERS[canal](dest, texto or "")
        if ok:
            os.makedirs(os.path.join(OUTBOX, "sent"), exist_ok=True)
            os.replace(path, os.path.join(OUTBOX, "sent", os.path.basename(path)))
            _audit(canal, accion, dest, "entregado-aprobado", info, len(texto or ""))
            return _result(True, False, "DM de Instagram entregado (%s)" % info)
        _audit(canal, accion, dest, "bloqueado", "entrega IG falló: %s" % info, len(texto or ""))
        return _result(False, True, "no se pudo enviar el DM (%s); sigue como borrador" % info)
    _audit(canal, accion, dest, "bloqueado", "OUTWARD sin canal de entrega", len(texto or ""))
    return _result(False, True, "no hay canal de entrega para %r (sigue como borrador)" % accion)


def _safe_draft(channel, action, dest, text, reason, avisos=None):
    """_draft que NUNCA lanza: devuelve (name|None, err|None). Mantiene el fail-closed
    (si no se puede ni draftear, el caller bloquea igual; jamás entrega)."""
    try:
        return _draft(channel, action, dest, text, reason, avisos), None
    except Exception as e:
        return None, "%r" % (e,)


# ─────────────────────────────────────────────────────────────────────────────
# Canal: Telegram (la ÚNICA boca hacia fuera, y solo para REPORT a {{TITULAR}})
# ─────────────────────────────────────────────────────────────────────────────
def _deliver_telegram(chat_id, text, reply_to=None):
    """POST real a Telegram. Devuelve (ok, info). Único socket saliente del repo.
    `reply_to` (message_id): ancla este mensaje como RESPUESTA a uno de {{TITULAR}}, para no
    perder el hilo. allow_sending_without_reply → si el original ya no existe, se entrega
    igual (no se pierde el mensaje por anclar)."""
    token = get_secret("btp-telegram-token", SECRETS, "token")
    if not token:
        return False, "falta btp-telegram-token (Llavero/fichero)"
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    payload = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": "true"}
    if reply_to:
        payload["reply_to_message_id"] = str(reply_to)
        payload["allow_sending_without_reply"] = "true"
    data = urllib.parse.urlencode(payload).encode()
    ultimo = None
    for intento, espera in ((1, 1.0), (2, 4.0), (3, None)):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=20)
            return (r.status == 200), ("HTTP %s" % r.status + (" (intento %d)" % intento
                                                              if intento > 1 else ""))
        except Exception as e:
            ultimo = e
            if espera is None or not _no_llego_seguro(e):
                break
            time.sleep(espera)
    return False, "error de red: %r" % (ultimo,)


# ─────────────────────────────────────────────────────────────────────────────
# Canal: Instagram DM (Graph API oficial de Meta). 2ª boca OUTWARD, SIEMPRE con gate.
#   · A _deliver_instagram_dm SOLO se llega desde approve_and_deliver, tras el nonce de
#     {{TITULAR}} (challenge-response). Nunca en desatendido. El POST vive AQUÍ (no en
#     tools/instagram.py) para no abrir una 2ª boca de egress (invariante de test_fuga.sh).
#   · GATED por App Review de Meta: enviar DMs exige el permiso instagram_manage_messages.
#     Sin ese permiso (hoy), Meta rechaza el POST → fail-closed: el borrador se queda «a un
#     clic» y se devuelve el motivo. La lectura de DMs sigue en ig_inbox.py / instagram.py.
#   · El token vive en el Llavero (btp-instagram-api), igual que tools/instagram.py.
#     Aquí NUNCA se imprime ni se loguea (el audit guarda solo dest_hash + longitud).
# ─────────────────────────────────────────────────────────────────────────────
IG_GRAPH = "https://graph.facebook.com/v21.0"
IG_SECRETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".instagram_secrets.json")
IG_ALLOWLIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".instagram_allowlist.json")


def _ig_token():
    """Token de Instagram desde el Llavero (o .instagram_secrets.json). None si falta."""
    return (get_secret("btp-instagram-api", IG_SECRETS, "access_token") or "").strip() or None


def _ig_user_id(token):
    """IG Business/Creator user-id: del config si está fijado, si no se resuelve (1 GET)
    reusando tools/instagram.py. Import perezoso: no carga nada en Fase 1 (dry)."""
    try:
        cfg = (json.load(open(IG_SECRETS, encoding="utf-8")) or {}).get("ig_user_id")
        if cfg:
            return str(cfg)
    except Exception:
        pass
    try:
        from instagram import resolve_ig_user_id
        igid, _err = resolve_ig_user_id(token)
        return igid
    except Exception:
        return None


def _ig_dest_ok(dest):
    """Destinatario IG válido. Si existe .instagram_allowlist.json ({"ids": [...]}), se EXIGE
    pertenencia (fail-closed: ilegible → deniega). Sin allowlist, el gate es la aprobación por
    nonce de {{TITULAR}} (igual que el correo no allowlista por persona). dest = IGSID del hilo."""
    if not dest or not str(dest).strip():
        return False
    try:
        if os.path.exists(IG_ALLOWLIST):
            ids = {str(x) for x in ((json.load(open(IG_ALLOWLIST, encoding="utf-8")) or {}).get("ids") or [])}
            return str(dest) in ids
    except Exception:
        return False  # allowlist presente pero ilegible → fail-closed
    return True


def _deliver_instagram_dm(dest, text):
    """POST real de un DM de Instagram (Graph API). Devuelve (ok, info). Única boca de egress
    para IG. Fail-closed: sin token o sin instagram_manage_messages, Meta rechaza y devolvemos
    (False, motivo) → el borrador se queda «a un clic» (no se pierde, no se reintenta solo)."""
    token = _ig_token()
    if not token:
        return False, "falta token de Instagram (Llavero btp-instagram-api)"
    igid = _ig_user_id(token)
    if not igid:
        return False, "no pude resolver el IG user-id (¿cuenta Profesional vinculada a Página?)"
    url = "%s/%s/messages" % (IG_GRAPH, igid)
    data = urllib.parse.urlencode({
        "recipient": json.dumps({"id": str(dest)}),
        "message": json.dumps({"text": (text or "")[:1000]}),
        "access_token": token,
    }).encode()
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30)
        return (r.status == 200), ("HTTP %s" % r.status)
    except urllib.error.HTTPError as e:
        hint = ""
        try:
            err = (json.load(e) or {}).get("error", {})
            code, msg = err.get("code"), (err.get("message") or "")
            if code in (10, 200) or "permission" in msg.lower():
                hint = " → falta instagram_manage_messages (App Review de Meta pendiente)"
            elif code == 551 or "24" in msg:
                hint = " → fuera de la ventana de 24h (el contacto debe escribir primero)"
            else:
                hint = " → %s" % msg if msg else ""
        except Exception:
            pass
        return False, "Meta rechazó (HTTP %s)%s" % (e.code, hint)
    except Exception as e:
        return False, "error de red: %r" % (e,)


_DELIVERERS = {"telegram": _deliver_telegram, "instagram": _deliver_instagram_dm}


def _deliver_telegram_action(chat_id, action="typing"):
    """POST sendChatAction (p.ej. 'typing' = "escribiendo…"). Mismo socket único; best-effort."""
    token = get_secret("btp-telegram-token", SECRETS, "token")
    if not token:
        return False, "falta btp-telegram-token"
    url = "https://api.telegram.org/bot%s/sendChatAction" % token
    data = urllib.parse.urlencode({"chat_id": chat_id, "action": action}).encode()
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
        return (r.status == 200), ("HTTP %s" % r.status)
    except Exception as e:
        return False, "error de red: %r" % (e,)


def typing_to_titular(*, dry=False):
    """Muestra "escribiendo…" en el chat de {{TITULAR}} para no dejarla en silencio mientras se prepara
    una respuesta. BEST-EFFORT: nunca rompe el flujo (si falla, se ignora). Respeta HALT (en pausa,
    nada). No es boca OUTWARD: solo su self chat_id, sin texto."""
    if halted() or dry:
        return False
    cid = _self_chatid()
    if not cid:
        return False
    try:
        ok, _ = _deliver_telegram_action(cid, "typing")
        return ok
    except Exception:
        return False


def _deliver_telegram_document(chat_id, path, caption=""):
    """POST real a Telegram sendDocument (fichero). Mismo socket único que el texto."""
    token = get_secret("btp-telegram-token", SECRETS, "token")
    if not token:
        return False, "falta btp-telegram-token (Llavero/fichero)"
    try:
        with open(path, "rb") as f:
            blob = f.read()
    except Exception as e:
        return False, "no pude leer el fichero: %r" % (e,)
    boundary = "----btpSalidaDoc7f3a1c9e"
    fn = os.path.basename(path)
    chunks = []

    def _field(name, val):
        chunks.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                       % (boundary, name, val)).encode("utf-8"))
    _field("chat_id", str(chat_id))
    if caption:
        _field("caption", caption[:1000])
    chunks.append(("--%s\r\nContent-Disposition: form-data; name=\"document\"; filename=\"%s\"\r\n"
                   "Content-Type: application/octet-stream\r\n\r\n" % (boundary, fn)).encode("utf-8"))
    chunks.append(blob)
    chunks.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/sendDocument" % token, data=b"".join(chunks),
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return (r.status == 200), ("HTTP %s" % r.status)
    except Exception as e:
        return False, "error de red: %r" % (e,)


def report_file_to_titular(path, *, caption="", dry=False, voz="calida"):
    """REPORT de un FICHERO al chat de la PROPIA {{TITULAR}} (autónomo, como report_to_titular,
    pero documento). Solo a su self chat_id (NUNCA a terceros). HALT + auditoría. No es
    OUTWARD: no acepta destino, así que no puede usarse como boca hacia fuera.
    El `caption` (pie que {{TITULAR}} lee) pasa por la casa de estilo (tipo "caption": se
    trunca a ≤1000 con «…», no se parte). FAIL-OPEN: si la capa fallara, va el caption crudo."""
    caption = _casa_estilo(caption, voz=voz, tipo="caption") if caption else caption
    path = os.path.expanduser(str(path))
    n = os.path.getsize(path) if os.path.exists(path) else 0
    if not os.path.isfile(path):
        _audit("telegram", REPORT, None, "bloqueado", "fichero inexistente", 0)
        return _result(False, True, "no existe el fichero: %s" % path)
    if halted():
        _audit("telegram", REPORT, None, "bloqueado", "HALT activo", n)
        return _result(False, True, "HALT activo: salida en pausa total")
    cid = _self_chatid()
    if not cid:
        _audit("telegram", REPORT, None, "bloqueado", "sin self chat_id", n)
        return _result(False, True, "no hay chat_id de {{TITULAR}} configurado")
    if dry:
        _audit("telegram", REPORT, None, "dry", "fichero %s" % os.path.basename(path), n)
        return _result(False, False, "dry: no se envió", extra={"dry": True, "fichero": os.path.basename(path)})
    ok, info = _deliver_telegram_document(cid, path, caption)
    _audit("telegram", REPORT, None, "entregado" if ok else "fallo",
           "fichero %s (%s)" % (os.path.basename(path), info), n)
    return _result(ok, not ok, ("fichero entregado (%s)" % info) if ok else
                   "entrega de fichero falló (%s)" % info)


def _deliver_telegram_reaction(chat_id, message_id, emoji):
    """POST real a Telegram setMessageReaction (acuse LIGERO, sin texto). Mismo socket único."""
    token = get_secret("btp-telegram-token", SECRETS, "token")
    if not token:
        return False, "falta btp-telegram-token (Llavero/fichero)"
    data = urllib.parse.urlencode({
        "chat_id": chat_id, "message_id": message_id,
        "reaction": json.dumps([{"type": "emoji", "emoji": emoji}]),
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/setMessageReaction" % token, data=data)
    try:
        r = urllib.request.urlopen(req, timeout=20)
        return (r.status == 200), ("HTTP %s" % r.status)
    except Exception as e:
        return False, "error de red: %r" % (e,)


def react_to_titular(message_id, emoji="👍", *, dry=False):
    """Acuse LIGERO: reacciona (emoji) a un mensaje de la PROPIA {{TITULAR}} en su chat, sin
    mandar texto (preferencia suya). Solo a su self chat_id (NUNCA terceros); no acepta
    destino → no es boca OUTWARD. Respeta HALT y audita. No pasa por el silencio (es un
    acuse a algo que ella acaba de mandar)."""
    if not message_id:
        return _result(False, True, "sin message_id para reaccionar")
    if halted():
        _audit("telegram", REPORT, None, "bloqueado", "HALT activo (reacción)", 0)
        return _result(False, True, "HALT activo: salida en pausa total")
    cid = _self_chatid()
    if not cid:
        _audit("telegram", REPORT, None, "bloqueado", "sin self chat_id", 0)
        return _result(False, True, "no hay chat_id de {{TITULAR}} configurado")
    if dry:
        _audit("telegram", REPORT, None, "dry", "reacción %s" % emoji, 0)
        return _result(False, False, "dry: no se reaccionó", extra={"dry": True})
    ok, info = _deliver_telegram_reaction(cid, message_id, emoji)
    _audit("telegram", REPORT, None, "reaccion" if ok else "fallo",
           "reacción %s (%s)" % (emoji, info), 0)
    return _result(ok, not ok, ("reacción %s (%s)" % (emoji, info)) if ok else
                   "reacción falló (%s)" % info)


# ─────────────────────────────────────────────────────────────────────────────
# Inbound de Telegram (lo usa bot_telegram.py) — la ÚNICA boca de ENTRADA también
# vive aquí, así el invariante "solo salida.py habla con Telegram" cubre in y out.
# OJO: el texto que devuelve es NO CONFIABLE (aunque venga del chat de {{TITULAR}}) →
# quien lo consuma DEBE triarlo en CUARENTENA, nunca ejecutarlo como instrucción.
# ─────────────────────────────────────────────────────────────────────────────
def _read_offset():
    try:
        return int(json.load(open(TG_OFFSET, encoding="utf-8")).get("offset"))
    except Exception:
        return None


def _write_offset(offset):
    os.makedirs(os.path.dirname(TG_OFFSET), exist_ok=True)
    tmp = TG_OFFSET + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"offset": int(offset)}, f)
    os.replace(tmp, TG_OFFSET)


def poll_updates(*, timeout=0, limit=20, estado_out=None):
    """Lee mensajes nuevos del chat allowlistado de {{TITULAR}} vía getUpdates. Devuelve una
    lista de dicts {update_id, chat_id, text, date, message_id, kind, voice}; SOLO del
    self chat_id (allowlist dura). Persiste el offset para no re-leer. [] si falta
    token/chat_id o ante cualquier error de red (fail-soft: no es salida hacia fuera).

    `estado_out` (opcional): dict que esta función RELLENA con {"ok": bool, "motivo": str|None}
    para que el llamador distinga "sin mensajes nuevos" (ok=True) de "fallo de red" (ok=False) —
    ambos devuelven []. Lo usa bot_telegram para su heartbeat de salud (2/7/26: un poll que solo
    ve excepciones de red en bucle NO es lo mismo que un poll sano sin novedad). No cambia el
    comportamiento para llamadores que no lo pasan (compatibilidad hacia atrás)."""
    def _marcar(ok, motivo=None):
        if isinstance(estado_out, dict):
            estado_out["ok"] = ok
            estado_out["motivo"] = motivo

    token = get_secret("btp-telegram-token", SECRETS, "token")
    self_cid = _self_chatid()
    if not token or not self_cid:
        _marcar(False, "sin token/chat_id")
        return []
    offset = _read_offset()
    params = {"timeout": int(timeout), "limit": int(limit)}
    if offset is not None:
        params["offset"] = offset
    url = "https://api.telegram.org/bot%s/getUpdates?%s" % (token, urllib.parse.urlencode(params))
    try:
        r = urllib.request.urlopen(urllib.request.Request(url), timeout=int(timeout) + 15)
        data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        sys.stderr.write("salida.poll_updates: aviso de red: %r\n" % (e,))
        _marcar(False, "%r" % (e,))
        return []
    if not isinstance(data, dict) or not data.get("ok"):
        _marcar(False, "respuesta no-ok de Telegram")
        return []
    _marcar(True)
    out, max_uid = [], offset
    for upd in data.get("result", []):
        uid = upd.get("update_id")
        if isinstance(uid, int):
            max_uid = uid + 1 if max_uid is None else max(max_uid, uid + 1)
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        if chat != self_cid:           # allowlist: solo el chat de {{TITULAR}}
            continue
        voice = msg.get("voice") or msg.get("audio")
        out.append({"update_id": uid, "chat_id": chat, "text": msg.get("text"),
                    "date": msg.get("date"), "message_id": msg.get("message_id"),
                    "kind": "voice" if voice else "text", "voice": voice})
    if max_uid is not None:
        _write_offset(max_uid)
    return out


def download_voice(voice, dest_dir=None):
    """Descarga una nota de voz de Telegram (el objeto `voice` de poll_updates) a un fichero
    local temporal y devuelve su ruta, o None si falla. INBOUND: lee la voz de {{TITULAR}} de su
    PROPIO chat para transcribirla en LOCAL; no es salida hacia fuera. Vive aquí porque salida
    es el ÚNICO que habla con api.telegram.org (choke-point del muro)."""
    fid = (voice or {}).get("file_id")
    if not fid:
        return None
    token = get_secret("btp-telegram-token", SECRETS, "token")
    if not token:
        return None
    try:
        u = "https://api.telegram.org/bot%s/getFile?file_id=%s" % (token, urllib.parse.quote(str(fid)))
        meta = json.loads(urllib.request.urlopen(urllib.request.Request(u), timeout=20).read().decode("utf-8"))
        fp = (meta.get("result") or {}).get("file_path") if meta.get("ok") else None
        if not fp:
            return None
        durl = "https://api.telegram.org/file/bot%s/%s" % (token, fp)
        raw = urllib.request.urlopen(urllib.request.Request(durl), timeout=40).read()
    except Exception as e:
        sys.stderr.write("salida.download_voice: aviso de red: %r\n" % (e,))
        return None
    ext = os.path.splitext(fp)[1] or ".oga"
    safe = re.sub(r"[^A-Za-z0-9]", "", str(fid))[:24] or "voz"
    path = os.path.join(dest_dir or tempfile.gettempdir(), "tg_voz_%s%s" % (safe, ext))
    try:
        with open(path, "wb") as f:
            f.write(raw)
    except Exception:
        return None
    return path


# ─────────────────────────────────────────────────────────────────────────────
# Política de avisos: SILENCIO NOCTURNO (decisión de {{TITULAR}}, 21/6/26).
# OPT-IN: si STATE/notif/config.json NO existe (o activo!=true) no hay silencio —los
# tests, con estado aislado, nunca lo ven → comportamiento intacto. Solo afecta a REPORT
# no-urgente. El CÓDIGO ROJO (alerta_critica) NO pasa por send() → siempre atraviesa.
# ─────────────────────────────────────────────────────────────────────────────
def _notif_cfg():
    try:
        with open(NOTIF_CFG, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _en_silencio(cfg=None):
    """True si la config está activa y la hora local cae en la ventana de silencio."""
    cfg = _notif_cfg() if cfg is None else cfg
    if not cfg.get("activo"):
        return False
    try:
        ini = int(cfg.get("silencio_inicio", 23))
        fin = int(cfg.get("silencio_fin", 8))
    except Exception:
        return False
    if ini == fin:
        return False
    h = time.localtime().tm_hour
    # ventana que cruza medianoche (ini>fin, p.ej. 23→8) o normal (ini<fin)
    return (h >= ini or h < fin) if ini > fin else (ini <= h < fin)


def _hold_silencio(text):
    """Retiene un REPORT no-urgente durante el silencio: lo guarda para el resumen de la
    mañana y lo espeja en la bandeja. Devuelve un veredicto 'retenido' (no es un fallo)."""
    try:
        d = os.path.join(STATE, "notif")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "holding-%s.jsonl" % time.strftime("%Y-%m-%d"))
        rec = json.dumps({"ts": _now_iso(), "texto": text}, ensure_ascii=False) + "\n"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, rec.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception as e:
        sys.stderr.write("salida: no pude retener aviso nocturno: %r\n" % (e,))
    try:
        import bandeja
        bandeja.anota("🌙 (silencio nocturno) " + str(text)[:200])
    except Exception:
        pass
    return _result(False, False, "retenido (silencio nocturno) → resumen a las 08:00",
                   extra={"retenido": True})


def flush_silencio():
    """Envía un resumen de los avisos retenidos por la noche y limpia el buffer. Lo llama
    un launchd a las 08:00. urgente=True para no re-retenerse. Si no hay nada, no envía."""
    d = os.path.join(STATE, "notif")
    try:
        files = [f for f in os.listdir(d) if f.startswith("holding-") and f.endswith(".jsonl")]
    except Exception:
        files = []
    items = []
    for fn in sorted(files):
        p = os.path.join(d, fn)
        try:
            with open(p, encoding="utf-8") as fh:
                for ln in fh:
                    try:
                        items.append(json.loads(ln))
                    except Exception:
                        pass
            os.remove(p)
        except Exception:
            pass
    if not items:
        return _result(False, False, "nada retenido")
    _n = len(items)
    _cosa = "cosa" if _n == 1 else "cosas"
    cab = random.choice([
        "💤 Mientras dormías me ocupé de %d %s:\n" % (_n, _cosa),
        "Buenos días 💜 Anoche avancé %d %s:\n" % (_n, _cosa),
        "💤 Esta noche saqué %d %s, te las dejo aquí:\n" % (_n, _cosa),
    ])
    cuerpo = "\n".join("· %s" % (str(it.get("texto") or "")[:200]) for it in items[:30])
    if len(items) > 30:
        cuerpo += "\n… y %d más (las tienes en la bandeja)." % (len(items) - 30)
    return send("telegram", REPORT, None, cab + cuerpo, urgente=True)


# ─────────────────────────────────────────────────────────────────────────────
# Choke-point
# ─────────────────────────────────────────────────────────────────────────────
def send(channel, action, dest, text, *, dry=False, urgente=False, voz="calida", reply_to=None, fuente=""):
    """Único punto de salida. Devuelve un veredicto dict; NUNCA lanza por una vía de
    entrega (fail-closed → draft). Reglas, en orden:
      0. validación de forma          → bloqueo (no se draftea basura).
      1. HALT                          → bloqueo total.
      2. acción OUTWARD                → SIEMPRE draft (gate del muro), nunca entrega.
      3. REPORT a destino no-allowlist → draft (no se entrega a terceros «como report»).
      4. REPORT a {{TITULAR}}               → casa de estilo + entrega por el canal (o draft).

    `voz`: solo aplica a REPORT-a-{{TITULAR}}. "calida" (defecto) asegura 💜 de cierre donde
    encaje; "sobria" = sin 💜, escueto (avisos secos). OUTWARD NUNCA se maquilla (podría
    alterar lo que el scrubber de fuga analiza).
    """
    text = "" if text is None else str(text)   # nunca asumir que viene un str
    n = len(text)
    # 0. Forma.
    if channel not in CHANNELS:
        _audit(channel, action, dest, "bloqueado", "canal desconocido", n)
        return _result(False, True, "canal desconocido: %r" % channel)
    if action not in ACTIONS:
        _audit(channel, action, dest, "bloqueado", "accion desconocida", n)
        return _result(False, True, "accion desconocida: %r" % action)
    if not text.strip():
        _audit(channel, action, dest, "bloqueado", "texto vacio", n)
        return _result(False, True, "texto vacio")

    # 1. Kill-switch.
    if halted():
        _audit(channel, action, dest, "bloqueado", "HALT activo", n)
        return _result(False, True, "HALT activo: salida en pausa total")

    # 2. OUTWARD: gate del muro. Jamás entrega en desatendido → borrador «a un clic».
    #    H3: scrubber de léxico/PII público → no bloquea (ya está gated) pero hace
    #    VISIBLE la posible fuga en el borrador, el log y el veredicto, para que {{TITULAR}}
    #    no apruebe a ciegas algo que filtre "vacuna"/clínica/teléfono.
    if action in OUTWARD:
        avisos = _revisar_publico(text)
        motivo = "gate del muro: requiere OK de {{TITULAR}}"
        if avisos:
            motivo += " | ⚠️ posible fuga: " + "; ".join(avisos)
        draft, err = _safe_draft(channel, action, dest, text, motivo, avisos)
        _audit(channel, action, dest, "borrador" if draft else "bloqueado",
               ("OUTWARD: gate del muro%s" % (" +%d aviso(s)" % len(avisos) if avisos else ""))
               if draft else "draft fallo: %s" % err, n)
        res = _result(False, True,
                      "OUTWARD requiere OK de {{TITULAR}} → borrador en outbox" if draft
                      else "OUTWARD bloqueado; no pude draftear (%s); NO se envió" % err,
                      draft=draft)
        if avisos:
            res["avisos_fuga"] = avisos
        return res

    # CASA DE ESTILO: solo REPORT (a {{TITULAR}}). Normaliza el formato del texto antes de
    # cualquier decisión de entrega, así dry/silencio/borrador llevan ya el texto en
    # casa de estilo. FAIL-OPEN dentro de _casa_estilo: nunca tira el mensaje.
    text = _casa_estilo(text, voz=voz, tipo="report")

    # SELLO DE ORIGEN (14-jul-2026). Si el aviso NO sale del lazo, se le antepone una línea que lo
    # dice. Incidente real: un diagnóstico por ssh llamó a ia.ask y le disparó a {{TITULAR}} un 🔴
    # "tarea clínica PARADA" que era mentira (solo faltaba el binario en el PATH de esa sesión).
    # NO se suprime nada: suprimir es exactamente cómo se pierden las alarmas de verdad. Se ETIQUETA
    # — una falsa se descarta de un vistazo y no erosiona la confianza en las reales.
    origen, es_lazo = _origen()
    if not es_lazo:
        text = ("⚠️ (origen: %s — esto NO viene del lazo; probablemente un diagnóstico manual)\n\n%s"
                % (origen, text))
        n = len(text)

    # 3-4. REPORT: solo al chat_id allowlistado de {{TITULAR}}.
    self_cid = _self_chatid()
    if not self_cid:
        # Sin allowlist no hay a quién reportar con garantías → draft, no se inventa destino.
        draft, err = _safe_draft(channel, action, dest, text, "sin chat_id de {{TITULAR}} configurado")
        _audit(channel, action, dest, "borrador" if draft else "bloqueado", "REPORT sin self chat_id", n)
        return _result(False, True, "no hay chat_id de {{TITULAR}} configurado → %s"
                       % ("borrador" if draft else "bloqueado (%s)" % err), draft=draft)

    target = str(dest).strip() if dest else self_cid
    if target != self_cid:
        # «report» a alguien que no es {{TITULAR}} = salida hacia fuera disfrazada → gate.
        draft, err = _safe_draft(channel, action, target, text, "REPORT a destino no-allowlistado")
        _audit(channel, action, target, "borrador" if draft else "bloqueado", "REPORT a no-self", n)
        return _result(False, True, "REPORT solo a {{TITULAR}}; destino no allowlistado → %s"
                       % ("borrador" if draft else "bloqueado (%s)" % err), draft=draft)

    # MURO DE LA BATERIA (14-jul-2026): bajo tests NUNCA se alcanza el telefono de {{TITULAR}}. El texto
    # NO se pierde (se draftea) y el mutis es RUIDOSO (audit + stderr): un mutis silencioso seria
    # exactamente el fallo que intentamos evitar. `alerta_critica()` no pasa por aqui: sigue saliendo.
    if _bajo_bateria_test():
        draft, err = _safe_draft(channel, action, target, text,
                                 "bateria de tests: no se envia de verdad")
        _audit(channel, action, target, "mutis-test-battery",
               "bateria de tests (BTP_TEST_BATTERY + STATE aislado)", n)
        try:
            sys.stderr.write("salida: MUTIS por bateria de tests (no se envio a {{TITULAR}})\n")
        except Exception:
            pass
        return _result(False, True, "bateria de tests: no se envia de verdad", draft=draft)

    if dry:
        _audit(channel, action, target, "dry", "simulacro (no se envió)", n)
        return _result(False, False, "dry: no se envió", extra={"dry": True, "texto": text})

    # Silencio nocturno: REPORT no-urgente → se retiene y sale en el resumen de la mañana.
    if not urgente and _en_silencio():
        _audit(channel, action, target, "retenido-silencio", "silencio nocturno", n)
        return _hold_silencio(text)

    # Partir si pasa del tope de Telegram, SIN perder contenido (casa de estilo §3.6).
    # Un solo trozo si cabe; varios (cada uno con su pie «(i/n)») si no. Cada trozo se
    # entrega por el mismo canal; el veredicto es "entregado" solo si TODOS salen.
    try:
        trozos = _partir_telegram(text)
    except Exception:
        trozos = [text]                       # FAIL-OPEN: no perder el mensaje por partir
    if not trozos:
        trozos = [text]
    info = ""
    for i, tr in enumerate(trozos):
        # Solo el PRIMER trozo se ancla (reply); los de continuación van sueltos.
        ok, info = _DELIVERERS[channel](target, tr, reply_to=(reply_to if i == 0 else None))
        if not ok:
            # Entrega falló → no perder el resto: lo no entregado va a outbox + log.
            no_enviado = "\n".join(trozos[i:])
            draft, err = _safe_draft(channel, action, target, no_enviado, "entrega fallida: %s" % info)
            _audit(channel, action, target, "fallo", info, n)
            return _result(False, True, "entrega fallida (%s) → %s"
                           % (info, "borrador" if draft else "sin borrador (%s)" % err), draft=draft)
    _audit(channel, action, target, "entregado", info, n)
    # copia local legible de lo que SÍ le llegó, con el origen para poder auditar quién habló
    _log_enviado(text, urgente=urgente, fuente=fuente, origen=origen)
    extra = {"trozos": len(trozos)} if len(trozos) > 1 else None
    return _result(True, False, "entregado (%s)" % info, extra=extra)


# ── Presupuesto de avisos (27-jul-26) ─────────────────────────────────────────────
# Cuántos mensajes NO urgentes puede recibir {{TITULAR}} al día antes de que el resto se
# agrupe en el parte. Tres, no treinta y tres. Se sube con BTP_TOPE_AVISOS si algún día
# hace falta, pero subirlo es volver al problema: el tope es la funcionalidad.
TOPE_AVISOS_DIA = int(os.environ.get("BTP_TOPE_AVISOS", "3"))


def _entregados_hoy():
    """Cuántos mensajes se le han entregado ya hoy. Lee el propio audit: la única
    fuente que sabe lo que de verdad salió, no lo que alguien pensaba enviar."""
    try:
        path = os.path.join(OUTBOX, "audit-%s.jsonl" % time.strftime("%Y-%m-%d"))
        if not os.path.exists(path):
            return 0
        n = 0
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for linea in fh:
                if '"veredicto": "entregado"' in linea:
                    n += 1
        return n
    except Exception:
        return 0   # fail-open: ante la duda, que el mensaje SALGA


def _presupuesto_agotado():
    return _entregados_hoy() >= TOPE_AVISOS_DIA


def _aplazar(text, fuente=""):
    """Guarda el aviso para el parte diario. NO se pierde: se agrupa.

    La primera vez de cada día manda UN mensaje corto avisando de que a partir de ahí
    agrupa, para que el silencio no se lea como que el sistema se ha muerto."""
    try:
        dir_apl = os.path.join(STATE, "aplazados")
        os.makedirs(dir_apl, exist_ok=True)
        path = os.path.join(dir_apl, "%s.jsonl" % time.strftime("%Y-%m-%d"))
        primero = not os.path.exists(path)
        rec = {"ts": _now_iso(), "fuente": str(fuente), "texto": str(text)}
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if primero:
            send(TELEGRAM, REPORT, None,
                 "Te he mandado ya %d avisos hoy, así que el resto lo agrupo en el parte "
                 "para no ser pesada. Si algo urge de verdad, te escribo igual."
                 % TOPE_AVISOS_DIA,
                 urgente=True, voz="calida", fuente="portero-de-ruido")
    except Exception:
        pass


def aplazados_de_hoy():
    """Los avisos agrupados de hoy, para que el parte diario los incluya."""
    try:
        path = os.path.join(STATE, "aplazados", "%s.jsonl" % time.strftime("%Y-%m-%d"))
        if not os.path.exists(path):
            return []
        out = []
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for linea in fh:
                try:
                    out.append(json.loads(linea))
                except Exception:
                    continue
        return out
    except Exception:
        return []


def _log_operativo(text, fuente=""):
    """Append a tools/state/operativo/operativo-YYYY-MM-DD.jsonl. Nunca lanza.
    Usa STATE dinámicamente (igual que _audit usa OUTBOX) para que los tests que
    sobreescriben salida.STATE queden aislados."""
    try:
        operativo_dir = os.path.join(STATE, "operativo")
        os.makedirs(operativo_dir, exist_ok=True)
        rec = {"ts": _now_iso(), "fuente": str(fuente), "texto": str(text), "n": len(str(text))}
        line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
        path = os.path.join(operativo_dir, "operativo-%s.jsonl" % time.strftime("%Y-%m-%d"))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except Exception as e:
        sys.stderr.write("salida: aviso, no pude loguear operativo: %r\n" % (e,))


def _log_enviado(text, *, urgente=False, fuente="", origen=""):
    """Copia LOCAL y legible de cada mensaje que de verdad se ENTREGÓ al chat de {{TITULAR}}
    (bot→ella). El audit de _audit solo guarda metadatos (hash/hora/nº); esto guarda el TEXTO
    para que cualquier sesión pueda responder «¿qué te ha dicho Vega ahora?» y cazar duplicados
    ANTES de que la molesten. Fichero: tools/state/salida/enviados-YYYY-MM-DD.jsonl (gitignored,
    0600, solo su Mac → 0 egress, respeta el muro; es su propio mensaje a sí misma). Nunca lanza."""
    try:
        d = os.path.join(STATE, "salida")
        os.makedirs(d, exist_ok=True)
        rec = {"ts": _now_iso(), "fuente": str(fuente or ""), "urgente": bool(urgente),
               "origen": str(origen or ""), "texto": str(text), "n": len(str(text))}
        line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
        path = os.path.join(d, "enviados-%s.jsonl" % time.strftime("%Y-%m-%d"))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except Exception as e:
        sys.stderr.write("salida: aviso, no pude loguear enviado: %r\n" % (e,))


def report_to_titular(text, *, channel="telegram", dry=False, urgente=False, voz="calida",
                     reply_to=None, categoria="humano", fuente=""):
    """Atajo del canal de reporte del lazo: REPORT al chat de la propia {{TITULAR}}.

    categoria="humano" (DEFECTO) → comportamiento actual: entrega al chat de {{TITULAR}}.
      FAIL-SAFE: si un emisor no marca categoria, sigue llegándole. Nunca escondemos
      por omisión.
    categoria="operativo" → fontanería del sistema. NO entrega a Telegram; en su lugar
      hace append a tools/state/operativo/operativo-YYYY-MM-DD.jsonl y audita con
      veredicto "operativo-log". El chat de {{TITULAR}} queda limpio.
    categoria="parte" → el PARTE DIARIO de HOY (tools/enviar_hoy.py). EXENTO del
      presupuesto de avisos: ver nota "Portero de ruido" más abajo — es el único
      llamante legítimo, porque el parte es el mecanismo de vaciado de lo aplazado.

    INVARIANTE: urgente=True siempre es "humano" (nunca operativo); si alguien pasa
    urgente=True + categoria="operativo", prevalece "humano" (regla de oro: nunca
    esconder lo urgente).

    urgente=True salta el silencio nocturno (p.ej. el acuse a un mensaje que ella acaba
    de mandar, o una urgencia). El silencio solo afecta a los avisos no-urgentes.
    voz="sobria" para avisos secos (sin 💜); "calida" (defecto) para el trato diario.
    reply_to (message_id): ancla la respuesta al mensaje de {{TITULAR}}, para no perder el
    hilo (el lazo pasa el message_id del mensaje que ella acaba de mandar)."""
    # INVARIANTE: urgente = siempre humano (regla de oro).
    if urgente and categoria == "operativo":
        categoria = "humano"

    if categoria == "operativo":
        n = len(str(text) if text is not None else "")
        _log_operativo(text, fuente=fuente)
        _audit(channel, REPORT, None, "operativo-log", "categoria=operativo (no entregado a Telegram)", n)
        return _result(False, False, "operativo: guardado en log, no entregado a Telegram",
                       extra={"operativo": True})

    # ── Portero de ruido (27-jul-26) ──────────────────────────────────────────────
    # {{TITULAR}}: «Vega habla tanto que ya no le hago caso». Medido en el propio outbox:
    # 33 mensajes/día de media, pico de 71. Uno cada 20 minutos. Con ese volumen dejar
    # de mirar es la respuesta racional, y entonces la señal se pierde: el 26-jul saltó
    # un CÓDIGO ROJO, se entregaron 10 alertas críticas, y el lazo estuvo 37 horas
    # parado sin que ella lo supiera. Un muro cuya alarma no se lee es decoración.
    #
    # Regla: lo urgente SIEMPRE pasa. Lo rutinario se APLAZA al parte, no se tira.
    #
    # BUG (29-jul-26, detectado en vivo: el parte de HOY salía con exit 1 cada mañana):
    # `_aplazar()` guarda los avisos "para el parte diario" — el parte ES el mecanismo
    # de vaciado. Si el propio parte cae bajo el MISMO cupo (como pasaba: enviar_hoy.py
    # llamaba a report_to_titular sin marcar nada especial), lo aplazado no sale NUNCA y
    # el cupo se convierte en un bloqueo permanente en vez de un filtro de ruido. Colar
    # urgente=True no vale: urgente cambia también silencio nocturno/tono, y el parte
    # NO es una urgencia. El parte necesita su propia vía, exenta SOLO del cupo.
    es_parte = (categoria == "parte")
    if not urgente and not es_parte and not dry and _presupuesto_agotado():
        _aplazar(text, fuente=fuente)
        n = len(str(text) if text is not None else "")
        _audit(channel, REPORT, None, "aplazado", "presupuesto diario agotado -> va al parte", n)
        return _result(False, False, "aplazado al parte diario (presupuesto de avisos agotado)",
                       extra={"aplazado": True})

    return send(channel, REPORT, None, text, dry=dry, urgente=urgente, voz=voz, reply_to=reply_to,
                fuente=fuente)


def vaciar_aplazados_de_hoy():
    """Borra el fichero de aplazados de HOY. Llamar SOLO tras confirmar que el parte
    diario que los incorpora se entregó de verdad (delivered=True) — si se borra antes
    y la entrega falla, esos avisos se pierden. Fail-soft: nunca lanza."""
    try:
        path = os.path.join(STATE, "aplazados", "%s.jsonl" % time.strftime("%Y-%m-%d"))
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        sys.stderr.write("salida: aviso, no pude vaciar aplazados: %r\n" % (e,))


def alerta_critica(text):
    """Señal de SOCORRO del sistema (CÓDIGO ROJO): entrega a {{TITULAR}} AUNQUE haya HALT.
    Es el único mensaje que atraviesa el muro, y por una razón: si algo va MUY mal y se
    han parado las máquinas, ella TIENE que enterarse. Es REPORT-a-self por CÓDIGO (solo
    a su propio chat, solo este texto; no es salida hacia fuera ni la decide el modelo).

    LLAMANTES LEGÍTIMOS: `codigo_rojo.trigger` y `errores` (severidad GOAL). NADA MÁS. Este canal
    salta el HALT, el silencio nocturno y la casa de estilo; usarlo para un aviso normal —y no
    digamos para una CELEBRACIÓN— es abusar del canal de socorro y desgastarlo. Lo vigila
    tests/test_alerta_critica.py con una lista de llamantes cerrada."""
    self_cid = _self_chatid()
    # Sello de origen también aquí: alerta_critica NO pasa por send(), así que se lo estampamos
    # nosotros. Un código rojo disparado desde un diagnóstico manual tiene que ser reconocible.
    origen, es_lazo = _origen()
    if not es_lazo:
        text = ("⚠️ (origen: %s — esto NO viene del lazo; probablemente un diagnóstico manual)\n\n%s"
                % (origen, text))
    n = len(text or "")
    if not self_cid:
        _audit("telegram", REPORT, None, "bloqueado", "alerta critica sin chat_id", n)
        return _result(False, True, "sin chat_id de {{TITULAR}}")
    ok, info = _DELIVERERS["telegram"](self_cid, text)
    _audit("telegram", REPORT, self_cid, "alerta-critica" if ok else "fallo-alerta", info, n)
    return _result(ok, not ok, "alerta crítica %s (%s)" % ("entregada" if ok else "FALLÓ", info))


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def _cli_status():
    cid = _self_chatid()
    n_pend = len(os.listdir(PENDING)) if os.path.isdir(PENDING) else 0
    print("salida.py — choke-point único de salida (B3)")
    print("  HALT activo : %s" % ("SÍ ⛔ (toda salida bloqueada)" if halted() else "no"))
    for p in HALT_FILES:
        print("    · %s : %s" % (p, "existe" if os.path.exists(p) else "—"))
    print("  chat de {{TITULAR}} configurado : %s" % ("sí" if cid else "NO (reportes → borrador)"))
    print("  borradores en outbox : %d  (%s)" % (n_pend, PENDING))


def main(argv):
    if not argv or argv[0] in ("status", "-h", "--help", "help"):
        _cli_status()
        return 0
    cmd = argv[0]
    if cmd in ("report", "report-dry", "report-urgente"):
        text = argv[1] if len(argv) > 1 else ""
        if not text:
            print("uso: salida.py report \"<texto>\"")
            return 2
        res = report_to_titular(text, dry=(cmd == "report-dry"), urgente=(cmd == "report-urgente"))
        print(json.dumps(res, ensure_ascii=False))
        return 0 if (res["delivered"] or res.get("dry") or res.get("retenido")) else 1
    if cmd == "flush":
        res = flush_silencio()
        print(json.dumps(res, ensure_ascii=False))
        return 0
    if cmd == "enviados":
        n = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 10
        d = os.path.join(STATE, "salida")
        hoy = os.path.join(d, "enviados-%s.jsonl" % time.strftime("%Y-%m-%d"))
        if not os.path.exists(hoy):
            print("(sin mensajes entregados hoy)")
            return 0
        with open(hoy, encoding="utf-8") as f:
            lineas = f.readlines()[-n:]
        for ln in lineas:
            try:
                r = json.loads(ln)
            except Exception:
                continue
            fu = (" [%s]" % r["fuente"]) if r.get("fuente") else ""
            urg = " 🔴" if r.get("urgente") else ""
            print("· %s%s%s\n  %s\n" % (r.get("ts", "")[:19], fu, urg,
                                        (r.get("texto") or "").replace("\n", " ")))
        return 0
    print("uso: salida.py [status | report \"<texto>\" | report-urgente \"<texto>\" | report-dry \"<texto>\" | flush | enviados [N]]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
