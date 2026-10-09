#!/usr/bin/env python3
"""lote_envio.py — enviar N DMs con UNA sola orden de {{TITULAR}} (9-oct-26, plan aprobado por ella).

QUÉ RESUELVE. El permiso de envío (`permiso_envio.py`) es de un solo uso: con 8 respuestas leídas en
el chat hacían falta 8 órdenes. Aquí ella lee N respuestas, escribe UNA orden («envíalos») y salen
esas N, y solo esas. Nada automático: sin su orden no se abre nada (el «nivel A» de plantillas quedó
descartado).

LA ORDEN. Solo abre un lote un mensaje suyo que sea ENTERO una orden corta de una gramática cerrada
(afirmación opcional + «envíalos»/«mándalos»/«envía»/«manda»/«lánzalos» + «todos»/«ya»/«ahora»/«por
favor»). Es lista blanca porque una lista negra de frases que no deben abrir no converge. Lo que no casa
no abre nada, y se nota porque no sale nada. Ojo: el emisor solo mira mensajes que además son una orden
de envío (`permiso_envio.ORDEN`), que no incluye «lánzalos»: esa forma está en la gramática pero hoy
no llega a abrir nada.

CÓMO SE ATA A LO QUE ELLA LEYÓ (misma filosofía que el permiso: la verdad está en el transcript, no
en un fichero).
  1. MANIFIESTO. La sesión escribe en su MENSAJE (texto de asistente, lo que ella ve) un bloque con
     el formato de `render()`: por ítem, quién (hilo de LinkedIn) y el texto EXACTO. `python3
     tools/lote_envio.py render items.json` lo genera y valida. El bloque que vale es el que estaba
     delante de ella en la vuelta a la que contesta su orden: se vuelve a buscar en el transcript
     (que escribe el harness) cada vez. No existe un fichero de ítems que alguien pueda escribir a mano.
  2. SU ORDEN abre `ok_envio/<sesión>.lote`: puntero FIRMADO (HMAC, la misma clave que el permiso) a
     su mensaje + qué ítems lleva ya gastados. 15 minutos. Los ítems no están en el fichero.
  3. EL GUARD (`salida_guard`) deja pasar un `browser_batch` si es EXACTAMENTE: navegar al hilo de un
     ítem pendiente, teclear el texto exacto de ESE ítem y pulsar Enter (SIN ningún clic: uno en la
     lista de hilos cambiaría de conversación), todo en la misma pestaña, con tabId en cada acción y en una sola llamada. Gasta el ítem (un solo uso). Cualquier
     otra cosa que teclee, rellene o suba algo mientras el lote está vivo se DENIEGA.

FUERA DE LOTE SIEMPRE (de uno en uno, como antes). Se distingue por ESTRUCTURA, no por intención:
  · solo entra un hilo YA existente de LinkedIn (`/messaging/thread/<id>`): perfil, redactar mensaje
    nuevo o InMail no casan, y por tanto un primer contacto no puede ir en lote;
  · texto con dato clínico (heurística, ver `RE_CLINICO`: puede fallar, en la dirección de rechazar);
  · adjuntos y subidas: el lote no los contempla y se deniegan mientras vive;
  · correo, Bash, `gh`: este permiso no vale para nada de eso.

LÍMITES QUE NO SE ESCONDEN (ver también el límite de `permiso_envio`: mismo uid de macOS).
  · El nombre que acompaña a cada ítem es una ETIQUETA: el guard no ve la pantalla, solo ata la URL
    del hilo y el texto. Que la etiqueta sea el dueño real de esa URL lo comprueba ella al leerla
    (y `verificacion` al revisar), no el código.
  · Solo cubre LinkedIn por navegador (Claude in Chrome). Otros envíos siguen de uno en uno.
  · Un ítem se gasta al autorizar la llamada, no al confirmarse el envío: si el navegador falla a
    mitad, ese ítem no se reintenta con este lote.
  · El filtro de líneas laterales exige `isSidechain: false` en las entradas de asistente. Dato de
    `verificacion` (9-oct-26): en 15 transcripts reales recientes, 860 de 860 entradas de asistente
    del fichero principal la llevan a false; las `true` solo viven en ficheros `subagents/`. Es una
    MUESTRA, no una garantía del formato: si algún día no la llevan, el lote no se abre.
  · SIN CLIC y sin saber si LinkedIn enfoca solo la caja al cargar el hilo: si no lo hace, el
    lote no funcionará hasta decidirlo con una prueba real (no se relaja por defecto).
  · Cualquier mensaje suyo posterior a la orden cancela el lote («espera» debe frenar).
"""
import hashlib
import unicodedata
import json
import os
import re
import secrets
import sys
from datetime import datetime
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import permiso_envio as P  # noqa: E402

MAX_ITEMS = 10
VIDA_S = 900
MAX_TEXTO = 1500
EXT = ".lote"

# Hilo existente de LinkedIn: es lo único que se puede enviar en lote.
_RE_HILO = re.compile(r"^https://www\.linkedin\.com/messaging/thread/[A-Za-z0-9_=%.-]+$")

# Dato clínico: heurística DELIBERADAMENTE ancha. Si casa, el ítem no entra en el lote y va de uno
# en uno. Un falso positivo cuesta un clic más; un falso negativo, un dato clínico saliendo en lote.
# No sustituye al criterio de quien redacta, ni es un detector de identificadores (eso es `deid`).
RE_CLINICO = re.compile(
    r"(met[áa]stasis|metast|quimio|radioterapia|inmunoterapia|bio?psia|tumor|oncolog|onc[óo]log|"
    r"diagn[óo]stic|pron[óo]stic|estadio|\bhers?-?2\b|\bbrca\s*\d?|\bki-?67\b|pd-?l1|"
    r"receptores?\s+(?:hormonal|de\s+estr)|triple\s+negativ|mutaci|gen[óo]mic|secuenci|\btac\b|"
    r"resonancia|pet-?tac|anal[ií]tica|marcadores?|\bca\s*15|dosis|\bmg\b|tratamiento|medicaci|"
    r"f[áa]rmaco|efectos?\s+secundarios|\bned\b|vacuna\s+personalizada|neoant[ií]geno|"
    r"informe\s+(?:m[ée]dico|cl[ií]nico)|historial|hospital|ingres|cirug[ií]a|mastectom|ganglio|"
    r"lesi[óo]n|ensayo\s+cl[ií]nico)", re.I)

_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

# Si su orden suena a elegir, cambiar o esperar, NO es «envía todo el lote leído».
_RE_PARCIAL = re.compile(
    r"[?¿]|\bsi\b|\ba(?:l)?\s+[a-záéíóúñ]+|"      # pregunta, condicional («sí» afirmativo no), «a <alguien>»
    r"\d|\b(?:sin|excepto|menos|salvo|solo|s[óo]lo|nada\s+m[áa]s|pero|cambia\w*|modifica\w*|corrige\w*|"
    r"quita\w*|elimina\w*|borra\w*|espera\w*|antes|primero|despu[ée]s|luego|ma[ñn]ana|"
    r"cuando|todav[ií]a|a[uú]n|(?:el|la|los|las)\s+de|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|primer\w*|segund\w*|tercer\w*|"
    r"cuart\w*|quint\w*|[úu]ltim\w*|ese|esa|este|esta|aquel\w*)\b", re.I)

NEUTRAS = {"screenshot", "wait", "zoom", "scroll", "scroll_to", "hover"}
_CAMPOS_TECLA = {"action", "text", "tabId", "action_summary"}
_RE_ENVIAR_TECLA = re.compile(r"^(?:(?:ctrl|cmd|control|meta|super|command)\+)?(?:enter|return)$", re.I)

_JS_ACTUA = re.compile(r"(\.click\s*\(|\.submit\s*\(|requestSubmit|dispatchEvent\s*\(\s*new\s+"
                       r"(Mouse|Pointer|Submit|Keyboard|Input)Event|XMLHttpRequest|\bfetch\s*\(|"
                       r"navigator\.sendBeacon|\.value\s*=|execCommand|\.innerText\s*=|\.textContent\s*=)",
                       re.I)


# ── texto, URL, huella ───────────────────────────────────────────────────────────────────────
def norm_texto(t):
    t = unicodedata.normalize("NFC", t or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(l.rstrip() for l in t.strip().split("\n"))


def norm_url(u):
    """Sin query ni fragmento ni barra final; host en minúsculas. Vacío si no es una URL."""
    try:
        p = urlparse((u or "").strip())
    except Exception:
        return ""
    if not p.scheme or not p.netloc:
        return ""
    return "%s://%s%s" % (p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"))


def huella(url, texto):
    return hashlib.sha256((norm_url(url) + "\n" + norm_texto(texto)).encode("utf-8")).hexdigest()[:16]


def motivo_item(url, texto):
    """"" si el ítem puede ir en lote; si no, por qué no."""
    if not _RE_HILO.match(norm_url(url) or ""):
        return "la URL no es un hilo existente de LinkedIn (https://www.linkedin.com/messaging/thread/<id>)"
    t = norm_texto(texto)
    if not t:
        return "texto vacío"
    if len(t) > MAX_TEXTO:
        return "texto de más de %d caracteres" % MAX_TEXTO
    if "```" in t:
        return "el texto contiene ``` (rompe el formato del manifiesto)"
    if RE_CLINICO.search(t):
        return "el texto parece llevar contenido clínico: va de uno en uno"
    if _RE_EMAIL.search(t):
        return "el texto lleva una dirección de correo: va de uno en uno"
    return ""


# ── el manifiesto (lo que ella lee) ──────────────────────────────────────────────────────────
_INI = re.compile(r"^LOTE DE ENVÍO \((\d+)\)[ \t]*$", re.M)
_FIN = "FIN DEL LOTE"
_ITEM = re.compile(r"\[(\d+)\] Para: (.+?) \| (\S+) \| huella ([0-9a-f]{8})\n```texto\n(.*?)\n```\n?", re.S)


def render(items):
    """Bloque de manifiesto para pegar en el mensaje. `items`: [{para, url, texto}]. Lanza
    ValueError con el motivo si el lote no es válido."""
    if not items or len(items) > MAX_ITEMS:
        raise ValueError("un lote lleva de 1 a %d ítems (hay %d)" % (MAX_ITEMS, len(items or [])))
    vistos, out = set(), ["LOTE DE ENVÍO (%d)" % len(items)]
    for i, it in enumerate(items, 1):
        para = re.sub(r"\s+", " ", str(it.get("para") or "")).strip().replace("|", "/")
        if not para:
            raise ValueError("ítem %d sin destinatario (campo `para`)" % i)
        m = motivo_item(it.get("url"), it.get("texto"))
        if m:
            raise ValueError("ítem %d: %s" % (i, m))
        h = huella(it["url"], it["texto"])
        if h in vistos:
            raise ValueError("ítem %d repetido (misma URL y mismo texto)" % i)
        vistos.add(h)
        out.append("[%d] Para: %s | %s | huella %s\n```texto\n%s\n```"
                   % (i, para, norm_url(it["url"]), h[:8], norm_texto(it["texto"])))
    out.append(_FIN)
    return "\n".join(out)


def _bloques(textos):
    res = []
    for t in textos:
        pos = 0
        while True:
            m = _INI.search(t, pos)
            if not m:
                break
            fin = t.find("\n" + _FIN, m.end())
            if fin < 0:
                res.append((int(m.group(1)), None))      # abierto y sin cerrar: inválido
                break
            res.append((int(m.group(1)), t[m.end():fin + 1]))
            pos = fin + len(_FIN)
    return res


def parsear(textos):
    """(items|None, motivo). Exactamente UN bloque en lo que ella tenía delante, y entero válido:
    ningún ítem se descarta en silencio (un lote a medias sería otro lote que el que leyó)."""
    bl = _bloques(textos or [])
    if not bl:
        return None, "no hay manifiesto de lote en lo que ella tenía delante"
    if len(bl) > 1:
        return None, "hay %d manifiestos en lo que ella tenía delante: no sé cuál leyó" % len(bl)
    n, cuerpo = bl[0]
    if cuerpo is None:
        return None, "el manifiesto no está cerrado (falta «%s»)" % _FIN
    if n < 1 or n > MAX_ITEMS:
        return None, "lote de %d ítems: el máximo es %d" % (n, MAX_ITEMS)
    items, vistos, pos = [], set(), 0
    for m in _ITEM.finditer(cuerpo):
        if cuerpo[pos:m.start()].strip():
            return None, "texto suelto dentro del manifiesto"
        pos = m.end()
        if int(m.group(1)) != len(items) + 1:
            return None, "los ítems no van numerados 1..N"
        url, texto = m.group(3), m.group(5)
        mot = motivo_item(url, texto)
        if mot:
            return None, "ítem %s: %s" % (m.group(1), mot)
        h = huella(url, texto)
        if h[:8] != m.group(4):
            return None, "ítem %s: la huella no casa con el texto (copiado a medias o retocado)" % m.group(1)
        if h in vistos:
            return None, "ítem %s repetido" % m.group(1)
        vistos.add(h)
        items.append({"n": len(items) + 1, "para": m.group(2), "url": norm_url(url),
                      "texto": norm_texto(texto), "huella": h})
    if cuerpo[pos:].strip():
        return None, "texto suelto dentro del manifiesto"
    if len(items) != n:
        return None, "el manifiesto dice %d ítems y trae %d" % (n, len(items))
    return items, ""


# ── el permiso de lote (puntero firmado) ─────────────────────────────────────────────────────
def ruta(sesion):
    return os.path.join(P.dir_permisos(), P._sid(sesion) + EXT)


def _guardar(d, k):
    r = ruta(d.get("session_id"))
    os.makedirs(os.path.dirname(r), mode=0o700, exist_ok=True)
    tmp = r + ".%d.tmp" % os.getpid()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(P.firmar(d, k), f, ensure_ascii=False)
    os.replace(tmp, r)


def _quitar(sesion):
    try:
        os.remove(ruta(sesion))
    except Exception:
        pass


def hay(sesion):
    return os.path.exists(ruta(sesion))


_STOP_ETIQUETA = {"del", "los", "las", "con", "por", "para", "dra", "dr", "sra", "sr"}


def _sin_acentos(t):
    return "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn").lower()


def _nombra_a_alguien(texto, etiquetas):
    """¿Su orden nombra a algún destinatario del manifiesto («envíalos a Ana y Berta»)? Nombrar es
    elegir. Basta una palabra de 3+ letras de la etiqueta `para`."""
    t = _sin_acentos(texto)
    for et in etiquetas or ():
        for tok in re.findall(r"[a-z0-9]+", _sin_acentos(et)):
            if len(tok) >= 3 and tok not in _STOP_ETIQUETA and re.search(r"\b%s\b" % re.escape(tok), t):
                return True
    return False


# LA ORDEN QUE ABRE UN LOTE ES UNA GRAMÁTICA CERRADA (lista BLANCA, 9-oct-26). Una lista negra de
# frases que no deben abrir no converge: `verificacion` encontró una frase nueva en cuatro revisiones
# seguidas («el resto no», «y para», «y cierra sesión»…). Ahora solo abre el mensaje ENTERO que casa con
#   [afirmación] + verbo de envío en imperativo (clítico plural, o sin objeto) + [coletilla]{0,2}
# y nada más. Cualquier otra palabra → no abre, y se nota porque no sale nada. Si ella escribe algo más
# largo, que lo diga en un mensaje aparte o use la orden corta.
_AFIRM = r"(?:s[ií]|vale|ok|okay|venga|perfecto|adelante|dale|de acuerdo)"
_VERBO = (r"(?:(?:env[ií]a|m[aá]nda|l[aá]nza)(?:los|las)|env[ií]a|manda)")
_COLET = r"(?:todos|todas|ya|ahora|por favor|porfa)"
_RE_GRAMATICA = re.compile(r"(?:%s,?\s+)?%s(?:,?\s+%s){0,2}" % (_AFIRM, _VERBO, _COLET))
FRASES_VALIDAS = "«envíalos», «vale, envíalos», «ok, envíalos todos», «sí, mándalos», «venga, envíalos ya»"


def _normaliza_orden(t):
    t = unicodedata.normalize("NFC", t or "").lower()
    t = re.sub(r"\s+", " ", t).strip()
    return t.strip("¡!. ")


def _gramatica_de_lote(texto):
    """¿El mensaje ENTERO casa con la gramática cerrada? (`fullmatch`: nada antes ni después)."""
    return _RE_GRAMATICA.fullmatch(_normaliza_orden(P.solo_suyo(texto))) is not None


def es_orden_de_lote(texto, etiquetas=()):
    """True SOLO si el mensaje entero es la orden corta de la gramática. Los rechazos explícitos
    (elegir, cambiar, esperar, nombrar a alguien) quedan como segunda barrera, no como criterio."""
    if "envio" not in P.alcance(texto):
        return False
    suyo = P.solo_suyo(texto)
    if not _gramatica_de_lote(suyo):
        return False
    return not _RE_PARCIAL.search(suyo) and not _nombra_a_alguien(suyo, etiquetas)


def ventana_abierta(transcript_path, prompt_id="", prompt=""):
    """Textos míos desde su último mensaje humano ANTERIOR a esta orden (su mensaje nuevo puede estar
    ya en el transcript o no: si está, se salta). Es solo una vista previa para avisar: el guard
    vuelve a decidirlo con `contexto`."""
    textos = []
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for linea in f:
                try:
                    e = json.loads(linea)
                except Exception:
                    continue
                if not isinstance(e, dict):
                    continue
                if P._es_humano(e) or P._es_humano_encolado(e):
                    if prompt_id and e.get("promptId") == prompt_id:
                        continue
                    a = e.get("attachment")
                    if isinstance(a, dict) and prompt and P._hash_texto(a.get("prompt")) == P._hash_texto(prompt):
                        continue
                    textos = []
                elif e.get("type") == "assistant" and e.get("isSidechain") is False:
                    # Solo la conversación PRINCIPAL, y ESTRICTO (a diferencia de `contexto`): sin la
                    # clave `isSidechain` no cuenta. Código nuevo; un lote que no se abre es el lado seguro.
                    for b in (e.get("message") or {}).get("content") or []:
                        if isinstance(b, dict) and b.get("type") == "text":
                            textos.append(b.get("text") or "")
    except Exception:
        return None
    return textos


def intentar_abrir(prompt, session_id, prompt_id, transcript_path, k):
    """(abierto, aviso|None). `aviso` None = no había manifiesto delante (flujo de siempre, sin
    ruido). Si hay manifiesto y su orden es de lote, abre el permiso de lote (y el llamador NO abre
    el de un solo uso: este permiso no vale para correo, Bash ni gh)."""
    textos = ventana_abierta(transcript_path, prompt_id, prompt) if transcript_path else None
    if textos is None or not any(_INI.search(t) for t in textos):
        return False, None
    items, motivo = parsear(textos)
    if not items:
        return False, "El lote que tenía delante no es válido (%s): no se abre ningún lote." % motivo
    if not es_orden_de_lote(prompt, [it["para"] for it in items]):
        return False, ("Hay un lote de envío delante, pero su mensaje elige, nombra a alguien, cambia, espera "
                       "o no manda claramente enviar TODO el lote: no se abre ningún lote. Solo abre un mensaje "
                       "que sea SOLO la orden corta: " + FRASES_VALIDAS + ". Enséñale el lote tal como "
                       "quedaría y que la dé así.")
    d = {"tipo": "lote", "ts": datetime.now().replace(microsecond=0).isoformat(),
         "session_id": session_id or "", "prompt_id": prompt_id or "",
         "hash_prompt": P._hash_texto(prompt), "transcript_path": transcript_path or "",
         "nonce": secrets.token_hex(8), "n": len(items), "gastados": []}
    _guardar(d, k)
    return True, ("🔓 {{TITULAR}} ha dado la orden para el LOTE de %d envíos que tenía delante. Salen SOLO "
                  "esos: por cada uno, UN `browser_batch` con navigate al hilo, "
                  "`type` con el texto exacto y Enter (sin ningún clic), todo con el mismo tabId. 15 "
                  "minutos, cada ítem un solo uso. Mientras dure, teclear o rellenar cualquier otra "
                  "cosa se deniega. Si escribe otro mensaje, el lote se cancela." % len(items))


def _leer(k, sesion):
    """dict|None. Firma, sesión y caducidad; un lote que no vale se borra."""
    try:
        with open(ruta(sesion), encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        _quitar(sesion)
        return None
    if not isinstance(d, dict) or not P._mac_ok(d, k):
        _quitar(sesion)
        return None
    try:
        edad = (datetime.now() - datetime.fromisoformat(d["ts"])).total_seconds()
    except Exception:
        _quitar(sesion)
        return None
    if d.get("tipo") != "lote" or d.get("session_id") != sesion or edad > VIDA_S or edad < -60:
        _quitar(sesion)
        return None
    return d


# ── lo que hace cada llamada ─────────────────────────────────────────────────────────────────
def _norm_tool(t):
    return (t or "").lower().replace("-", "_")


def _acciones(tool, entrada):
    """[(nombre, input)] de una llamada de navegador, venga suelta o dentro de un batch. None si el
    batch es ilegible."""
    t = _norm_tool(tool)
    entrada = entrada if isinstance(entrada, dict) else {}
    if t.endswith("browser_batch"):
        acts = entrada.get("actions")
        if not isinstance(acts, list) or not acts:
            return None
        out = []
        for a in acts:
            if not isinstance(a, dict) or not isinstance(a.get("input"), dict):
                return None
            out.append((str(a.get("name") or "").lower(), a["input"]))
        return out
    return [(t.rsplit("__", 1)[-1], entrada)]


def _js_dudoso(txt):
    return not isinstance(txt, str) or not txt or bool(_JS_ACTUA.search(txt))


def _toca_texto(tool, entrada):
    """¿La llamada teclea, rellena o sube algo (o no se puede saber)? Es lo que el lote vigila."""
    acts = _acciones(tool, entrada)
    if acts is None:                       # batch ilegible: ante la duda, se vigila
        return True
    for nombre, ip in acts:
        if nombre == "computer" and ip.get("action") == "type":
            return True
        if nombre in ("form_input", "file_upload", "upload_image", "autofill_credential",
                      "enter_verification_code"):
            return True
        if nombre == "javascript_tool" and _js_dudoso(ip.get("text")):
            return True
    return False


def _casa_con_item(tool, entrada, pendientes):
    """(item|None, motivo). El batch tiene que ser exactamente la forma de un envío de lote."""
    if not _norm_tool(tool).endswith("browser_batch"):
        return None, "un envío de lote va en UN `browser_batch` (navigate + type + enviar), no suelto"
    acts = _acciones(tool, entrada)
    if not acts:
        return None, "batch ilegible"
    tabs, nav, tipo, enviado = set(), None, None, False
    for nombre, ip in acts:
        es_neutra = nombre == "computer" and ip.get("action") in NEUTRAS
        if es_neutra:
            if "tabId" in ip:            # una captura en OTRA pestaña tampoco vale
                tabs.add(ip.get("tabId"))
        else:
            # Toda acción que no sea mirar lleva el MISMO tabId que el navigate (si falta, deny).
            if not isinstance(ip.get("tabId"), int) or isinstance(ip.get("tabId"), bool):
                return None, "toda acción del envío tiene que llevar `tabId` (falta en %s)" % nombre
            tabs.add(ip["tabId"])
        if nombre == "navigate":
            if nav is not None:
                return None, "el batch navega más de una vez"
            nav = norm_url(str(ip.get("url") or ""))
            continue
        if nombre != "computer":
            return None, "acción no prevista en un envío de lote (%s)" % nombre
        a = ip.get("action")
        if es_neutra:
            continue
        if nav is None:
            return None, "hay que navegar primero al hilo dentro del mismo batch"
        if enviado:
            return None, "acciones después de enviar"
        if a == "left_click":
            # Ningún clic (verificacion, 9-oct-26): uno en la lista de hilos cambia de conversación
            # y el texto aprobado para A saldría a B.
            return None, "un envío de lote no admite clics: navigate + type + Enter"
        elif a == "type":
            if tipo is not None:
                return None, "el batch teclea más de una vez"
            tipo = norm_texto(str(ip.get("text") or ""))
        elif a == "key" and tipo is not None and _RE_ENVIAR_TECLA.match(str(ip.get("text") or "").strip()):
            if set(ip) - _CAMPOS_TECLA:      # `repeat`, `modifiers`…: no es un Enter simple
                return None, "el Enter lleva campos extra (%s)" % ", ".join(sorted(set(ip) - _CAMPOS_TECLA))
            enviado = True
        else:
            return None, "acción no prevista en un envío de lote (%s)" % a
    if nav is None or tipo is None:
        return None, "falta navegar al hilo o teclear el texto"
    if not enviado:
        return None, "el batch no incluye el envío (Enter): un envío de lote va en una sola llamada"
    if len(tabs) != 1:
        return None, "navegar y teclear tienen que ser en la MISMA pestaña (un solo tabId)"
    for it in pendientes:
        if it["url"] == nav and it["texto"] == tipo:
            return it, ""
    if any(it["texto"] == tipo for it in pendientes):
        return None, "ese texto es de un ítem del lote, pero no para ese hilo"
    if any(it["url"] == nav for it in pendientes):
        return None, "ese hilo es del lote, pero el texto no es el que ella leyó para él"
    return None, "ni el hilo ni el texto son de un ítem pendiente del lote"


def decidir(datos):
    """None = esto no lo gobierna el lote (se sigue por el camino de siempre).
    ("permitir", detalle) = un ítem del lote; se ha gastado.
    ("denegar", motivo) = el lote está vivo y esto teclea o sube algo que no es un ítem pendiente."""
    sesion = datos.get("session_id") or ""
    if not sesion or not hay(sesion):
        return None
    tool, entrada = datos.get("tool_name") or "", datos.get("tool_input")
    try:
        if not _toca_texto(tool, entrada):
            return None
        k = P.clave(permitir_env=True)
        if not k:
            return "denegar", "hay un lote abierto pero no puedo leer la clave que lo firma"
        d = _leer(k, sesion)
        if not d:
            return None          # caducó o no valía: se borra y se vuelve a lo de siempre
        if P._usado(d):
            _quitar(sesion)
            return None
        ok, _motivo, ctx = P.contexto(d)
        if not ok:
            _quitar(sesion)
            return None          # escribió otra cosa después, o el transcript no lo confirma
        items, motivo = parsear(ctx.get("visto_textos") or [])
        if not items or len(items) != d.get("n"):
            _quitar(sesion)
            return "denegar", "el manifiesto que ella tenía delante ya no es el del lote (%s)" % (motivo or "distinto")
        gastados = set(d.get("gastados") or [])
        pend = [it for it in items if it["huella"] not in gastados]
        if not pend:
            return "denegar", "el lote ya está entero enviado"
        it, motivo = _casa_con_item(tool, entrada, pend)
        if not it:
            return "denegar", "no casa con ningún ítem pendiente del lote: " + motivo
        d["gastados"] = sorted(gastados | {it["huella"]})
        if len(d["gastados"]) >= len(items):
            P.marcar_usado(d, "lote")        # agotado: queda en el libro
            _quitar(sesion)
        else:
            _guardar(d, k)
        return "permitir", "ítem %d de %d (%s)" % (it["n"], len(items), it["para"])
    except Exception as e:                  # fail-closed: con el lote vivo, ante un error no se teclea
        return "denegar", "no he podido comprobar el lote (%s)" % type(e).__name__


def main(argv):
    if len(argv) >= 3 and argv[1] == "render":
        try:
            with open(argv[2], encoding="utf-8") as f:
                print(render(json.load(f)))
        except (ValueError, OSError, KeyError, TypeError) as e:
            print("lote no válido: %s" % e, file=sys.stderr)
            return 1
        return 0
    if len(argv) >= 2 and argv[1] == "validar":
        items, motivo = parsear([sys.stdin.read()])
        print("OK %d ítems" % len(items) if items else "NO: " + motivo)
        return 0 if items else 1
    print("uso: lote_envio.py render <items.json> | validar < bloque.txt", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
