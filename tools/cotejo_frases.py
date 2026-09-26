#!/usr/bin/env python3
"""tools/cotejo_frases.py — ¿dijo {{TITULAR}} de verdad la frase que un borrador en su voz le atribuye?

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026: punto
de rotura 08, «capa 3» del plan de normas (el juez de las normas de salida). Paso 2 del plan
aprobado el 26-sep-2026 (`04 · IA/Notas/plan-el-juez-de-las-normas-de-salida-capa-3-2026-09-26.md`).

POR QUÉ. `feedback-no-inventarle-frases-en-primera-persona` (20-sep-26): un copy en su voz le
fabricó una intuición que ella nunca dijo, entre comillas. Una regex ve las comillas, no si ella
dijo la frase. Esto hace el cotejo SIN LLM: busca cada frase entrecomillada de un borrador en su
voz en SUS mensajes reales:
  · los mensajes de {{TITULAR}} en el transcript de esa sesión (los pasa quien llama);
  · sus mensajes de WhatsApp (`00_FUENTE-DE-VERDAD/_PRIVADO_WHATSAPP/*.md`, líneas `yo:` que
    escribe `wa_tracker.py`), en casa base.

MURO. Lectura LOCAL y solo lectura. Nada sale de la máquina, nada va a ningún modelo. El corpus no
se persiste en ningún sitio (vive en memoria del proceso); lo único que devuelve es la frase del
BORRADOR (texto mío, no suyo) y de qué fuente salió el respaldo.

QUÉ NO HACE. No sabe si una paráfrasis es fiel, ni traduce: un borrador en inglés de algo que ella
dijo en español sale «sin respaldo». Por eso nunca bloquea.
"""
import glob
import os
import re
import sys
import unicodedata
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _casa  # noqa: E402

_REL_WA = os.path.join("00_FUENTE-DE-VERDAD", "_PRIVADO_WHATSAPP")
# Casa base; si BTP_REPO apunta a un worktree (rodaje con replay_gate), el WhatsApp sigue en ~/claudecode.
WA_DIR = os.environ.get("BTP_WA_DIR") or next(
    (d for d in (os.path.join(_casa.casa_base(), _REL_WA),
                 os.path.join(os.path.expanduser("~/claudecode"), _REL_WA)) if os.path.isdir(d)),
    os.path.join(_casa.casa_base(), _REL_WA))
_RE_WA = re.compile(r"^\*\*\[\d{2}/\d{2}/\d{2} \d{2}:\d{2}\]\s+yo:\*\*\s+(.*)$")

# Frase entre comillas: «…», “…” o "…". Mínimo 3 palabras: «vacuna» o «ingeniera» son términos,
# no frases que se le atribuyan.
_COMILLAS = re.compile(r"«([^«»\n]{6,400})»|“([^“”\n]{6,400})”|\"([^\"\n]{6,400})\"")
MIN_PALABRAS = 3
# Primera persona dentro del bloque: es un texto en su voz (misma señal que `borrador_sin_voz`).
_PRIMERA = re.compile(r"\b(yo|mi|mis|me|estoy|llevo|tengo|siento|pens[ée]|dije|creo|I'm|I've|my)\b",
                      re.I)
# Un prompt para otra sesión, o un bloque que ES una cita de un tercero, no es su voz.
_NO_SUYO = re.compile(r"claude/|casa base|worktree|\brama\b|\bgit\b")
UMBRAL_DIFUSO = 0.85


def norm(s):
    """Minúsculas, sin tildes, sin puntuación, espacios colapsados."""
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def es_su_voz(bloque):
    return (bool(_PRIMERA.search(bloque)) and not _NO_SUYO.search(bloque)
            and not re.match(r"\s*\*?\s*[«\"“]", bloque))


# Qué la presenta como algo que ELLA dijo, pensó o siente: la frase misma en primera persona, o un
# verbo suyo justo antes («me dije: «…»», «I told myself "…"»). El replay de 30 días (26-sep-26)
# enseñó que sin esto el prefiltro cogía citas de un informe o de un criterio de ensayo dentro de
# un correo suyo («múltiples imágenes nodulares…», «any chemotherapy in the metastatic setting»).
_ATRIBUYE = re.compile(
    r"\b(dije|digo|dec[ií]a|pens[ée]|pienso|pensaba|sent[ií]|siento|sent[ií]a|me (dije|digo|"
    r"pregunt[ée]|repito|repet[ií])|mi (frase|intuici[oó]n|mantra|lema)|escrib[ií]|"
    r"I (said|say|thought|think|felt|feel|told|wrote|asked)|my (words|mantra|motto))\b", re.I)
# La frase misma dice lo que ella siente, piensa o dijo. Sin esto, un titular de copy en primera
# persona («Mi hígado, en seis semanas», «Esto que gira soy yo») contaba como cita suya, y eso es
# imitar su registro, que es el trabajo (replay de 30 días, 26-sep-26: 2 de 3 disparos).
_SIENTE = re.compile(
    r"\b(me dio la sensaci[oó]n|sensaci[oó]n de|siento|sent[ií]a?|pens[ée]|pienso|pensaba|"
    r"creo que|cre[ií]a que|me pareci[oó]|me parece|intu[ií]|not[ée]|tengo miedo|me asust\w*|"
    r"me dije|dije|me pregunt[ée]|I (felt|feel|thought|think|believed?|said|told myself|wondered|"
    r"was afraid))\b", re.I)


def frases_citadas(bloque, solo_suyas=False):
    """Frases entrecomilladas (≥ MIN_PALABRAS) de un bloque. Con `solo_suyas`, solo las que el
    bloque presenta como algo que ella dijo, pensó o siente."""
    out = []
    for m in _COMILLAS.finditer(bloque):
        f = next(g for g in m.groups() if g is not None).strip()
        if len(norm(f).split()) < MIN_PALABRAS:
            continue
        if re.search(r"[\\{}<>=;|]|\(\?|\w+\.\w+\(", f):   # código o regex, no una frase
            continue
        if solo_suyas:
            antes = bloque[max(0, m.start() - 80):m.start()]
            if not _ATRIBUYE.search(antes) and not _SIENTE.search(f):
                continue
        out.append(f)
    return out


def frases_en_su_voz(borradores):
    """Frases entrecomilladas que un bloque en su voz presenta como suyas."""
    out = []
    for b in borradores or []:
        if es_su_voz(b):
            out.extend(frases_citadas(b, solo_suyas=True))
    return out


_WA_CACHE = None


def corpus_whatsapp(wa_dir=None):
    """Sus mensajes de WhatsApp (solo `yo:`), normalizados. Cacheado por proceso. Fail-soft: sin
    carpeta (worktree sin fuente de verdad, test) devuelve lista vacía."""
    global _WA_CACHE
    if _WA_CACHE is not None and wa_dir is None:
        return _WA_CACHE
    out = []
    for ruta in sorted(glob.glob(os.path.join(wa_dir or WA_DIR, "*.md"))):
        try:
            with open(ruta, encoding="utf-8", errors="replace") as f:
                for raw in f:
                    m = _RE_WA.match(raw.rstrip("\n"))
                    if m:
                        t = norm(m.group(1).replace("🎙️", "").replace("🖼️", ""))
                        if t:
                            out.append(t)
        except OSError:
            continue
    if wa_dir is None:
        _WA_CACHE = out
    return out


def _difusa(fn, msg):
    """¿Está la frase casi literal en el mensaje? Solo si comparten la mayoría de palabras (filtro
    barato), se pasa una ventana del largo de la frase con SequenceMatcher."""
    pal = set(fn.split())
    if len(pal & set(msg.split())) < 0.7 * len(pal):
        return False
    w = msg.split()
    n = len(fn.split())
    for i in range(0, max(1, len(w) - n + 1)):
        if SequenceMatcher(None, fn, " ".join(w[i:i + n + 1])).ratio() >= UMBRAL_DIFUSO:
            return True
    return False


def respaldo(frase, suyos, wa=None):
    """'sesion' | 'whatsapp' | None. `suyos` = sus mensajes de la sesión (crudos); `wa` = corpus
    ya normalizado (`corpus_whatsapp()`)."""
    fn = norm(frase)
    if not fn:
        return "sesion"
    fuentes = (("sesion", [norm(x) for x in (suyos or [])]), ("whatsapp", wa or []))
    for fuente, msgs in fuentes:
        if any(fn in m for m in msgs):
            return fuente
    for fuente, msgs in fuentes:
        if any(_difusa(fn, m) for m in msgs):
            return fuente
    return None


def sin_respaldo(borradores, suyos, wa=None):
    """Frases en su voz que no aparecen en sus mensajes."""
    return [f for f in frases_en_su_voz(borradores) if respaldo(f, suyos, wa) is None]
