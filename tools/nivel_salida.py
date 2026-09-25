#!/usr/bin/env python3
"""tools/nivel_salida.py — el CÓDIGO calcula el nivel de cada salida (P3, fase F1: SOMBRA).

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.

POR QUÉ (25-sep-26). Hoy toda salida irreversible pide la misma firma, sea un merge a main o un
correo a su oncóloga. La propuesta P3 es que el código —nunca el modelo— ponga un nivel a cada
salida a partir de sus argumentos, y que cada nivel pida un gesto distinto:
  0 interno y reversible · 1 destinatario conocido + plantilla (NO ACTIVO: decide {{TITULAR}})
  2 texto libre, salud, plazo o lo que no se sabe leer · 3 pagar, publicar, merge/push a main.
Medición de 60 días y plan: `04 · IA/Notas/p3-firmas-por-niveles-medición-60-días-y-plan-2026-09-25.md`.

QUÉ HACE ESTA FASE: SOLO ANOTA. `salida_guard.py` sigue decidiendo exactamente igual; este módulo
le dice qué nivel HABRÍA tenido cada salida y lo deja en `nivel_salida.jsonl`. Tras 30 días de
sombra se comparan niveles y decisiones antes de que ningún nivel cambie un freno.

Reglas de diseño (comité: verificacion + consejero-arquitectura, 25-sep-26):
  · Una sola fuente de verdad: el QUÉ-sale lo sigue decidiendo `salida_guard._sale_fuera`; aquí
    solo se traduce su veredicto (+ el motivo de Bash) a un nivel. No se copia su clasificador.
  · Fail-closed hacia ARRIBA: lo que no se sabe leer es L2 como mínimo; un fallo del módulo, L3.
  · Tareas programadas: L2 mínimo (un sistema que crea sistemas). Terceros «propios» (Drive,
    Notion, scite): L2 salvo que se demuestre que el contenido pasó por `enruta.py` (hoy no).
  · Clic en host de pago → L3 desde el día 1; localhost y previews propias → L0.
  · No guarda contenido: solo un hash corto de lo que sale (para atar la firma al contenido, F2).
"""
import hashlib
import json
import os
import re
from urllib.parse import urlparse

L0, L1, L2, L3 = 0, 1, 2, 3

# ── Bash: el motivo que devuelve `salida_guard._bash_envia` ─────────────────────────────────────
_BASH_L3 = re.compile(r"^(git push que publica|gh pr merge|despliegue|gh (release|gist|issue) )")
_BASH_L1 = re.compile(r"^gh pr (create|edit|comment|ready|review)")  # repo propio; L1 no activo

# ── MCP por verbo ────────────────────────────────────────────────────────────────────────────────
_CORREO = re.compile(r"^(send_message|send_email|send_draft|reply|forward)$")
_PROGRAMA = re.compile(r"scheduled_task$|^create_trigger$")
_PUBLICA = re.compile(r"^(create_post|create_tweet|post_tweet|publish.*)$")
_TERCERO_PROPIO = re.compile(r"(^|_)(create_pages|update_page|create_comment|create_file|update_file|"
                             r"copy_file|create_collection|update_collection|add_dois_to_collection|"
                             r"create_database|create_view|move_pages|duplicate_page)$")

# ── Hosts para los clics ─────────────────────────────────────────────────────────────────────────
_HOST_PROPIO = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\]|.*\.local|"
                          r"(deploy-preview-\d+--|[0-9a-f]{24}--)?helptitular-web\.netlify\.app)$")
_HOST_PAGO = re.compile(r"(^|\.)(checkout\.|pay\.|payments?\.|secure\.)|"
                        r"(^|\.)(paypal|stripe|adyen|redsys|klarna|amazon|iherb|uber|renfe|"
                        r"booking|airbnb|ryanair|iberia|vueling)\.", re.I)
_NAVEGA = re.compile(r"(navigate|preview_start|tabs_create)", re.I)

# ── Paso de PAGO (P3 · F3, 26-sep-26, EN SOMBRA) ─────────────────────────────────────────────────
# El 25-sep un agente pulsó «Realizar pedido» en una tienda y solo lo paró el clasificador de Claude
# Code, no el muro. El hook no ve el DOM, así que se mira lo que SÍ pasa por él: la URL del paso
# final, un `find`/JS que busca o pulsa el botón de pedir, o pedir/rellenar una TARJETA. El verbo
# del botón solo cuenta si va con «pedido/compra/pago»: un «Confirmar» a secas (facturas de Renfe,
# 13-sep) no es pagar. Comité (verificacion + consejero-arquitectura): 7 días en sombra y luego se
# decide. Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.
_URL_PASO_PAGO = re.compile(
    r"/(checkout|pago|pagar|payment|pay|spc|buy|purchase|place-?order|order/confirm|"
    r"finalizar-?compra|tramitar|confirmar-?pedido)(?:/|\?|#|-|$)|placeOrder|/gp/buy/", re.I)
_TEXTO_PAGO = re.compile(
    r"(realizar|tramitar|confirmar|finalizar|completar)\s+(el\s+|la\s+|y\s+pagar\s+)?(pedido|compra|pago)|"
    r"place\s+(your\s+)?order|pay\s+now|buy\s+now|comprar\s+ahora|pagar\s+(ahora|con|\d)|"
    r"complete\s+(your\s+)?purchase|confirm\s+(and\s+)?pay|placeOrder", re.I)
_TARJETA = re.compile(r"card|tarjeta|payment|pago|cvv|cvc|visa|mastercard", re.I)


def senal_pago(tool, entrada):
    """Motivo si esta llamada dice que la sesión está en el paso de pagar, o ""."""
    t = (tool or "").lower()
    acts = [(t, entrada or {})]
    if "batch" in t and isinstance((entrada or {}).get("actions"), list):
        acts = [(str(a.get("name", "")).lower(), a.get("input") or {})
                for a in entrada["actions"] if isinstance(a, dict)]
    for nombre, ip in acts:
        if not isinstance(ip, dict):
            continue
        if _NAVEGA.search(nombre) and _URL_PASO_PAGO.search(str(ip.get("url") or "")):
            return "URL de paso final (%s)" % host_de_url(ip.get("url"))
        if nombre.endswith("find") and _TEXTO_PAGO.search(str(ip.get("query") or "")):
            return "busca el botón de pedir/pagar"
        if nombre.endswith("javascript_tool") and _TEXTO_PAGO.search(str(ip.get("text") or "")):
            return "JS sobre el botón de pedir/pagar"
        if ("credential" in nombre) and _TARJETA.search(json.dumps(ip, ensure_ascii=False)):
            return "pide o rellena una tarjeta"
    return ""


def host_de_url(url):
    if not isinstance(url, str) or not url.strip():
        return ""
    u = url.strip()
    try:
        return (urlparse(u if "://" in u else "https://" + u).hostname or "").lower()
    except Exception:
        return ""


def host_navegado(tool, entrada):
    """Host al que navega esta llamada (también dentro de un batch), o ""."""
    acts = [(tool or "", entrada or {})]
    if "batch" in (tool or "").lower() and isinstance((entrada or {}).get("actions"), list):
        acts = [(a.get("name", ""), a.get("input") or {}) for a in entrada["actions"] if isinstance(a, dict)]
    h = ""
    for nombre, ip in acts:
        if _NAVEGA.search(nombre or "") and isinstance(ip, dict):
            h = host_de_url(ip.get("url")) or h
    return h


def hash_contenido(entrada):
    """16 hex de lo que sale (canónico). No guarda el contenido: solo sirve para atarle la firma."""
    try:
        canon = json.dumps(entrada or {}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        canon = repr(entrada)
    return hashlib.sha256(canon.encode("utf-8", "replace")).hexdigest()[:16]


def nivel(tool, entrada, que, por_que="", host="", pago=""):
    """(nivel, etiqueta) de una salida ya reconocida por `salida_guard._sale_fuera`.

    `que`: "envia" | "clic" (lo que devolvió `_sale_fuera`); `por_que`: su motivo en Bash;
    `host`: último host navegado en la sesión (para los clics); `pago`: señal de paso de pago de
    la sesión (`senal_pago`). Nunca lanza: ante la duda, L3."""
    try:
        if que == "clic" and pago:
            return L3, "clic en PASO DE PAGO (%s)" % pago
        t = (tool or "").lower().replace("-", "_")
        verbo = t.rsplit("__", 1)[-1] if t.startswith("mcp__") else t
        if t == "bash":
            m = por_que or ""
            if _BASH_L3.search(m):
                return L3, "publica: " + m.split(" en /")[0][:60]
            if _BASH_L1.search(m):
                return L1, "gh pr en repo propio (L1 no activo)"
            return L2, "shell que lleva datos fuera: " + m[:60]
        if _CORREO.search(verbo) or verbo == "send_chat_message":
            return L2, "mensaje con texto libre"
        if _PUBLICA.search(verbo):
            return L3, "publica en una red"
        if _PROGRAMA.search(verbo):
            return L2, "tarea programada: cambia lo que el sistema hará solo"
        if verbo in ("share_file",) or "share" in verbo:
            return L2, "comparte con un tercero"
        if _TERCERO_PROPIO.search(verbo):
            return L2, "escribe en cuenta propia de un tercero (sin prueba de enruta)"
        if verbo in ("file_upload", "upload_image"):
            h = host or ""
            return (L3 if _HOST_PAGO.search(h) else L2), "sube un fichero a %s" % (h or "host desconocido")
        if que == "clic":
            h = host or ""
            if h and _HOST_PROPIO.match(h):
                return L0, "clic en host propio (%s)" % h
            if h and _HOST_PAGO.search(h):
                return L3, "clic en host de pago (%s)" % h
            return L2, "clic indeterminado (%s)" % (h or "host desconocido")
        return L2, "sale fuera sin regla de nivel: " + verbo[:40]
    except Exception as e:                       # fail-closed hacia arriba
        return L3, "fallo del calculador (%s)" % type(e).__name__


def nivel_salida_py(accion):
    """Nivel de una acción de `tools/salida.py` (lazo). report = sistema→{{TITULAR}}."""
    return {"report": (L0, "reporte a {{TITULAR}}"), "contact": (L2, "contacto con tercero"),
            "publish": (L3, "publica"), "pay": (L3, "paga")}.get(accion, (L3, "acción desconocida"))


# ── Memoria de host por sesión (para juzgar el clic que viene después de navegar) ────────────────
def _ruta_hosts(state):
    return os.path.join(state, "nivel_salida_hosts.json")


def _sesion_hash(session_id):
    return hashlib.sha256((session_id or "").encode()).hexdigest()[:12]


def _leer_sesiones(state):
    try:
        with open(_ruta_hosts(state), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _de_sesion(state, session_id):
    """{host, pago} de la sesión. Acepta el formato viejo (solo el host como texto)."""
    v = _leer_sesiones(state).get(_sesion_hash(session_id), {})
    return {"host": v, "pago": ""} if isinstance(v, str) else dict(v)


def recordar_host(state, session_id, tool, entrada):
    """Actualiza {host, pago} de la sesión. Navegar a OTRO host borra la señal de pago; la URL de
    paso final o un find/JS/tarjeta de pago la ponen. Devuelve el host nuevo o ""."""
    h = host_navegado(tool, entrada)
    s = senal_pago(tool, entrada)
    if not h and not s:
        return ""
    d = _leer_sesiones(state)
    k = _sesion_hash(session_id)
    v = d.get(k, {})
    v = {"host": v, "pago": ""} if isinstance(v, str) else dict(v)
    if h:
        if h != v.get("host"):
            v["pago"] = ""
        v["host"] = h
        if not s and _NAVEGA.search(tool or "") and not _URL_PASO_PAGO.search(
                str((entrada or {}).get("url") or "")):
            v["pago"] = ""                        # navegar a una URL que no es de pago la apaga
    if s:
        v["pago"] = s
    d[k] = v
    if len(d) > 500:                              # acotado: se queda con los últimos
        d = dict(list(d.items())[-300:])
    os.makedirs(state, exist_ok=True)
    ruta = _ruta_hosts(state)
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, ruta)
    return h


def host_de_sesion(state, session_id):
    return _de_sesion(state, session_id).get("host", "")


def anotar(state, registro):
    os.makedirs(state, exist_ok=True)
    with open(os.path.join(state, "nivel_salida.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


def sombra(state, datos, que, por_que, decision, ts):
    """Todo el trabajo de sombra de una llamada. El hook lo envuelve en try/except: NUNCA puede
    cambiar su decisión. Devuelve el registro anotado (o None si la llamada no sale fuera)."""
    if os.environ.get("BTP_NIVEL_SALIDA_FALLA"):  # solo para el test de «la sombra no manda»
        raise RuntimeError("fallo simulado")
    tool = datos.get("tool_name") or ""
    entrada = datos.get("tool_input") or {}
    sid = datos.get("session_id") or ""
    recordar_host(state, sid, tool, entrada)
    if not que:
        return None
    ses = _de_sesion(state, sid)
    host, pago = ses.get("host", ""), ses.get("pago", "")
    n, etiqueta = nivel(tool, entrada, que, por_que, host, pago)
    reg = {"ts": ts, "sesion": _sesion_hash(sid), "tool": tool, "que": que, "nivel": n,
           "etiqueta": etiqueta, "host": host, "hash_contenido": hash_contenido(entrada),
           "decision": decision}
    if pago:
        reg["paso_pago"] = pago
    anotar(state, reg)
    return reg
