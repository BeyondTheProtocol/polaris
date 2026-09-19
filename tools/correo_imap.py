#!/usr/bin/env python3
"""tools/correo_imap.py — lector de correo en TIEMPO REAL por IMAP (solo lectura).

Por qué existe: el conector de Gmail (MCP) trabaja sobre una copia que sincroniza cada
cierto tiempo y puede ir HORAS por detrás. Eso es un punto ciego para lo recién llegado
(un médico/logística que escribe hoy, "¿ya respondió X?"). Este watcher cierra ese hueco
con una capa DETERMINISTA y barata (sin IA, sin tokens) que lee la propia cuenta de {{TITULAR}}
y deja un buzón fresco en local + avisa al momento de lo urgente.

Además es el PRODUCTOR de tools/state/correo/buzon.json, la señal que hace "morder" al
semáforo de Vega (tools/vega_gate.py con BTP_GATE_SOURCES=buzon): así el daemon LLM
correo-urgente solo se enciende cuando entra correo NUEVO de verdad, no por el ruido de
seguimiento.json. Frugalidad = este fichero al día.

Garantías del muro (verificadas en tests/test_correo_imap.py):
  · SOLO LECTURA: IMAP4_SSL + select(readonly=True). No etiqueta, no borra, no mueve.
    NUNCA importa smtplib → estructuralmente no puede enviar correo.
  · Egress fail-closed: la ÚNICA conexión permitida es imap.gmail.com:993 (_assert_host).
  · NO es egress de datos: lee la cuenta de la PROPIA {{TITULAR}} (Gmail ya tiene su correo);
    nada nuevo sale a terceros. La única boca de salida sigue siendo tools/salida.py.
  · El secreto vive en el Llavero (btp-gmail-app-password*); si falta, no hace nada (fail-closed).
  · Anti-inyección: el asunto pasa por correo.detecta_inyeccion antes de mostrarse; el cuerpo
    NO se descarga aquí (headers-only) → no se ingiere texto no confiable.
  · Solo avisa por salida.report_to_titular (REPORT a su propio chat, gated). No contacta a nadie.
  · Anti-duplicados: cada aviso urgente pasa por correo.reclamar_aviso (ledger compartido), para
    que el daemon LLM correo-urgente no repita el mismo correo ({{TITULAR}} pidió ping sin doblar).

Reusa las salvaguardas de tools/correo.py (es_urgente, es_ned_critico, detecta_inyeccion,
registrar, reclamar_aviso) y el estado de tools/state/correo/. Sin dependencias (stdlib).

Punto ciego tapado (11-jul-2026): el pulso 24/7 (`once`, sin `user=`) solo vigilaba
`titular.mgp@` — lo que entraba a `titular@` (la cuenta del conector Gmail) era invisible,
así que una invitación real (Bernardo/Kernis) llegó sin avisar. `once_todas()`/`todas` hace el
MISMO pulso IMAP RO también sobre `titular@` (ya estaba en CUENTAS, solo faltaba barrerla en
el ciclo periódico), cada cuenta con su propio marcador incremental (`path_por_cuenta`, no pisa
el de la cuenta por defecto). GATE de {{TITULAR}}: requiere el App Password de `titular@` en el
Llavero como `btp-gmail-app-password-2` (mismo patrón que `btp-gmail-app-password` de
`titular.mgp@`) — sin él, esa cuenta falla fail-closed (silencioso, no rompe el pulso de la otra).

Uso:
  python3 tools/correo_imap.py once          # un pulso: refresca buzón + avisa urgentes
  python3 tools/correo_imap.py once --dry     # un pulso de prueba: ni avisa ni persiste
  python3 tools/correo_imap.py todas          # un pulso para TODAS las cuentas del daemon
  python3 tools/correo_imap.py todas --dry    # ídem, de prueba (ni avisa ni persiste)
  python3 tools/correo_imap.py buzon          # imprime el buzón fresco (sin red)
  python3 tools/correo_imap.py buscar "from:x" # búsqueda dirigida (¿ya respondió X?), todo el correo

La cuenta a vigilar se elige con BTP_GMAIL_USER (su clave se resuelve sola desde CUENTAS).
"""
import email
import email.header
import email.utils
import imaplib
import json
import os
import re
import socket
import ssl
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import correo  # noqa: E402  (salvaguardas duras + estado compartido + ledger anti-dup)

# ── Constantes de conexión (egress fail-closed: SOLO este host/puerto) ───────────
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
DEFAULT_USER = "titular.mgp@gmail.com"
SECRET_SERVICE = "btp-gmail-app-password"   # App Password de Gmail (cuenta por defecto), en el Llavero
# Cuentas de {{TITULAR}} (cada una con su App Password en el Llavero). El daemon elige cuál con
# BTP_GMAIL_USER y la clave se resuelve sola con _secret_for (no hay que tocar el código para cambiarla).
CUENTAS = [("titular.mgp@gmail.com", "btp-gmail-app-password"),
           ("titular@gmail.com", "btp-gmail-app-password-2"),
           ("titular@gmail.com", "btp-gmail-app-password-3")]
# Cuentas que vigila el PULSO 24/7 (once_todas/`todas`) — a propósito NO todo CUENTAS: el punto
# ciego verificado el 11-jul era solo titular@ (conector Gmail). titular@ no tiene
# el mismo tráfico NED y sumarla al pulso periódico multiplicaría el consumo de red/IMAP sin
# necesidad real; si hiciera falta, se añade aquí explícitamente (no por defecto).
CUENTAS_DAEMON = [DEFAULT_USER, "titular@gmail.com"]

SEEN = os.path.join(correo.CORREO_DIR, "imap_seen.json")   # marcador incremental propio
BUZON = os.path.join(correo.CORREO_DIR, "buzon.json")      # snapshot fresco (lo lee HOY/agente/gate)
BUZON_MAX = 80                                             # ventana MÍNIMA de cabeceras que
                                                             # guardamos; en catch-up puede crecer
                                                             # más (nunca se recorta lo nuevo, ver
                                                             # procesar())

_RE_CTRL = re.compile(r"[\x00-\x1f\x7f​‌‍⁠﻿]")  # control + zero-width/BOM


class FaltaClave(Exception):
    """No hay App Password en el Llavero → el watcher no hace nada (fail-closed)."""


def _assert_host(host, port):
    """Cortafuegos de egress: aborta si se intenta conectar a algo que no sea el IMAP de Gmail."""
    if (host, int(port)) != (IMAP_HOST, IMAP_PORT):
        raise RuntimeError("egress bloqueado: solo se permite %s:%d (pedido %r:%r)"
                           % (IMAP_HOST, IMAP_PORT, host, port))


def _secret_for(user):
    """Resuelve la App Password (servicio del Llavero) de una cuenta desde CUENTAS, para que
    BTP_GMAIL_USER baste para elegir cuenta Y clave. Cae a SECRET_SERVICE si la cuenta no aparece."""
    if user:
        for u, s in CUENTAS:
            if u == user:
                return s
    return SECRET_SERVICE


# ── Helpers de parseo de cabeceras (RFC 2047) ───────────────────────────────────
def _decode(raw):
    if not raw:
        return ""
    try:
        return str(email.header.make_header(email.header.decode_header(raw)))
    except Exception:
        return str(raw)


def _limpia(s):
    """Quita saltos, controles y unicode oculto del texto a mostrar."""
    return _RE_CTRL.sub("", (s or "").replace("\n", " ").replace("\r", " ")).strip()


def _nombre_de(remitente):
    nombre, addr = email.utils.parseaddr(remitente or "")
    nombre = _limpia(_decode(nombre))
    return nombre or (addr or "alguien")


# ── Abstracción del buzón (real o simulada en tests) ─────────────────────────────
class ImapMailbox:
    """Envuelve una conexión imaplib YA seleccionada en INBOX (readonly)."""

    def __init__(self, M):
        self._M = M
        typ, data = M.response("UIDVALIDITY")
        self.uidvalidity = int(data[0]) if data and data[0] else 0

    def all_uids(self):
        """TODOS los UID de la carpeta seleccionada (sin recortar). Necesario para el
        catch-up: recent_uids() por sí solo recorta a una ventana y por eso podía perder
        mensajes cuando el hueco desde el último pulso era mayor que la ventana."""
        typ, data = self._M.uid("search", None, "ALL")
        if typ != "OK" or not data or not data[0]:
            return []
        return sorted(int(x) for x in data[0].split())

    def recent_uids(self, limit):
        return self.all_uids()[-limit:]

    def fetch_header(self, uid):
        typ, data = self._M.uid(
            "fetch", str(uid),
            "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
        flags, raw = "", b""
        for part in data or []:
            if isinstance(part, tuple) and len(part) == 2:
                raw = part[1] or b""
                m = re.search(rb"FLAGS \(([^)]*)\)", part[0] or b"")
                if m:
                    flags = m.group(1).decode("ascii", "replace")
        msg = email.message_from_bytes(raw)
        return {"from": msg.get("From", ""), "subject": msg.get("Subject", ""),
                "date": msg.get("Date", ""), "message_id": msg.get("Message-ID", ""),
                "flags": flags}

    def close(self):
        try:
            self._M.logout()
        except Exception:
            pass


def _login(user=None, secret=None):
    """Conecta y autentica contra el IMAP de Gmail (sin seleccionar buzón). Fail-closed si
    falta la clave del Llavero. SOLO LECTURA siempre se aplica al seleccionar el buzón.
    Si no se pasa `secret`, se resuelve desde la cuenta (BTP_GMAIL_USER) vía _secret_for."""
    import _secrets
    user = user or os.environ.get("BTP_GMAIL_USER") or DEFAULT_USER
    secret = secret or _secret_for(user)
    pw = _secrets.get(secret)
    if not pw:
        raise FaltaClave("falta '%s' en el Llavero (genera una App Password de Gmail y "
                         "guárdala con setup_keychain.sh)" % secret)
    _assert_host(IMAP_HOST, IMAP_PORT)
    M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ssl.create_default_context())
    M.login(user, pw)
    return M


def _conectar(user=None, secret=None):
    """Abre INBOX en SOLO LECTURA para el pulso del watcher."""
    M = _login(user, secret)
    M.select("INBOX", readonly=True)   # readonly: no puede marcar visto, etiquetar ni borrar
    return ImapMailbox(M)


def login_ok(user=None, secret=None):
    """Test de credencial: ¿la App Password de esta cuenta autentica de verdad contra IMAP?
    Para el monitor de salud (healthcheck): NO selecciona buzón, solo LOGIN+LOGOUT (barato,
    de solo lectura). Devuelve (True, None) si autentica; (False, motivo_str) si no — nunca
    lanza (fail-soft: el llamador decide qué hacer con el fallo)."""
    try:
        M = _login(user, secret)
    except FaltaClave as e:
        return False, str(e)
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)
    try:
        M.logout()
    except Exception:
        pass
    return True, None


_RE_MAILER_DAEMON = re.compile(
    r"(mailer-daemon|mail delivery (subsystem|failure)|postmaster@|delivery status notif|"
    r"undeliverable|no se pudo entregar|entrega fallida|returned mail|failure notice)", re.I)


def rebotes_recientes(user=None, secret=None, limit=BUZON_MAX):
    """¿Hay señales de REBOTE (mailer-daemon/undeliverable) entre los mensajes recientes de
    INBOX? Barato: reusa la misma conexión de solo lectura que el poller normal, solo mira
    cabeceras (From/Subject) ya descargadas — no abre una ruta nueva. Para el healthcheck:
    si Vega mandó un borrador que rebotó, esto lo detecta sin que {{TITULAR}} tenga que notarlo
    ella. Devuelve la lista de rebotes (remitente/asunto, ambos limpios de control/zero-width
    vía _entry) o [] si no hay ninguno. Fail-soft: nunca lanza (¡solo lectura, no hay envío
    aquí — cero smtplib importado en este fichero!)."""
    try:
        mb = _conectar(user, secret)
    except FaltaClave:
        return []
    try:
        uids = mb.recent_uids(limit)
        out = []
        for uid in uids:
            e = _entry(uid, mb.fetch_header(uid))
            t = (e.get("remitente_email", "") + " " + e.get("asunto", "")).lower()
            if _RE_MAILER_DAEMON.search(t):
                out.append(e)
        return out
    except Exception:
        return []
    finally:
        mb.close()


def _select_todo_el_correo(M):
    """Selecciona el buzón 'Todo el correo' de Gmail por su atributo special-use \\All
    (robusto al idioma; su nombre fijo se localiza). Cae a INBOX si no aparece. Readonly."""
    objetivo = None
    try:
        typ, lines = M.list()
        if typ == "OK":
            for ln in lines or []:
                s = ln.decode("utf-8", "replace") if isinstance(ln, bytes) else str(ln)
                if "\\All" in s:
                    mm = re.search(r'"([^"]*)"\s*$', s)
                    objetivo = mm.group(1) if mm else s.split()[-1]
                    break
    except Exception:
        objetivo = None
    if objetivo:
        try:
            typ, _ = M.select('"%s"' % objetivo, readonly=True)
            if typ == "OK":
                return objetivo
        except Exception:
            pass
    M.select("INBOX", readonly=True)
    return "INBOX"


def _solo_ascii(s):
    """Quita diacríticos: Gmail ignora acentos y así X-GM-RAW no revienta con no-ASCII."""
    import unicodedata
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")


def _ts(e):
    try:
        return email.utils.parsedate_to_datetime(e.get("fecha", "")).timestamp()
    except Exception:
        return 0.0


def buscar(query, *, limit=20, cuenta="ambas"):
    """Búsqueda DIRIGIDA estilo Gmail (X-GM-RAW) sobre TODO el correo (no solo la bandeja) y en
    AMBAS cuentas de {{TITULAR}}, para responder '¿ya respondió X?' aunque el hilo esté archivado o
    en la otra cuenta. SOLO LECTURA; no avisa ni persiste. Más nuevo primero.
    `cuenta`: 'ambas' (def) | substring de la cuenta ('titular.mgp' / 'titular')."""
    objetivo = CUENTAS if cuenta in (None, "ambas", "all", "todas") else \
        [(u, s) for (u, s) in CUENTAS if cuenta in u] or [(cuenta, _secret_for(cuenta))]
    q = _solo_ascii(str(query)).replace('"', " ").strip()
    out, sin_clave = [], 0
    for user, secret in objetivo:
        try:
            M = _login(user, secret)
        except FaltaClave:
            sin_clave += 1
            continue
        try:
            _select_todo_el_correo(M)
            mb = ImapMailbox(M)
            typ, data = M.uid("search", "X-GM-RAW", '"%s"' % q)
            raw = data[0].split() if (typ == "OK" and data and data[0]) else []
            for uid in sorted(int(x) for x in raw)[-limit:]:
                e = _entry(uid, mb.fetch_header(uid))
                e["cuenta"] = user
                out.append(e)
        except Exception:
            pass
        finally:
            try:
                M.logout()
            except Exception:
                pass
    if sin_clave == len(objetivo) and not out:
        raise FaltaClave("ninguna cuenta tiene App Password en el Llavero")
    out.sort(key=_ts, reverse=True)
    return out[:limit]


# ── Estado propio (no toca procesados.json del triaje del agente) ────────────────
def _load_seen(path=None):
    try:
        with open(path or SEEN, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def path_por_cuenta(user):
    """Rutas (seen, buzon) PROPIAS de una cuenta, para no pisar el estado del poller
    principal (que usa SEEN/BUZON a secas para BTP_GMAIL_USER). Determinista: un slug simple
    del email basta (no hay colisiones entre las 3 cuentas de CUENTAS)."""
    slug = re.sub(r"[^a-z0-9]+", "-", (user or "").lower()).strip("-") or "default"
    return (os.path.join(correo.CORREO_DIR, "imap_seen-%s.json" % slug),
            os.path.join(correo.CORREO_DIR, "buzon-%s.json" % slug))


def cargar_buzon(path=None):
    try:
        with open(path or BUZON, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"actualizado": None, "uidvalidity": 0, "last_uid": 0, "mensajes": []}


def _avisar_default(text, urgente):
    import salida
    return salida.report_to_titular(text, urgente=urgente, fuente="correo-imap")


def _entry(uid, h):
    frm = h.get("from", "")
    subj_raw = _decode(h.get("subject", ""))
    inj = correo.detecta_inyeccion(subj_raw) or correo.detecta_inyeccion(frm)
    subj = "[asunto retenido por revisión]" if inj else _limpia(subj_raw)
    return {
        "uid": int(uid),
        "message_id": _limpia(h.get("message_id", "")),
        "remitente": _nombre_de(frm),
        "remitente_email": (email.utils.parseaddr(frm)[1] or "").lower(),
        "asunto": subj,
        "fecha": _limpia(h.get("date", "")),
        "flags": h.get("flags", ""),
        "ned_critico": correo.es_ned_critico(frm, subj_raw),
        "urgente": correo.es_urgente(frm, subj_raw),
        "inyeccion": bool(inj),
    }


def procesar(mb, *, dry=False, alertar=True, avisar=None, seen_path=None, buzon_path=None):
    """Núcleo determinista. `mb` es un buzón (real o simulado). Devuelve un resumen.
    No persiste ni avisa si dry=True; no avisa si alertar=False.
    Cada aviso urgente pasa por correo.reclamar_aviso (ledger compartido) → no se repite si el
    daemon LLM ya avisó del mismo correo (anti-duplicados).
    `seen_path`/`buzon_path`: rutas de persistencia alternativas (por defecto SEEN/BUZON, las
    de la cuenta del daemon). correo_triage.py las usa para barrer OTRA cuenta sin pisar el
    estado del poller principal — cada cuenta lleva su propio marcador incremental."""
    seen_path = seen_path or SEEN
    buzon_path = buzon_path or BUZON
    avisar = avisar or _avisar_default
    seen = _load_seen(seen_path)
    baseline = (seen is None) or (seen.get("uidvalidity") != mb.uidvalidity)
    last_uid = 0 if baseline else int(seen.get("last_uid", 0))

    if baseline:
        # Primera vez (o uidvalidity nuevo tras un cambio de buzón): no hay marcador fiable
        # del que partir, así que cogemos solo la ventana reciente de siempre — "recuperar
        # todo el historial" no tiene sentido en un re-baseline.
        recientes = mb.recent_uids(BUZON_MAX)
    else:
        # CATCH-UP (bug real 2→7-sep-26: +131 msgs con ventana=80 → 51 sin procesar, sin
        # rastro en ningún artefacto local — tools/state/deuda.json,
        # "buzon-ventana-80-pierde-mensajes-en-catchup"). mb.recent_uids(BUZON_MAX) recorta
        # SIEMPRE a los últimos BUZON_MAX UID de la carpeta entera, sin mirar last_uid: si
        # entre pulsos llegan más mensajes que la ventana, los más viejos del hueco quedan
        # fuera del fetch y nunca pasan por triaje/etiquetado/avisos.
        # Fix: en catch-up NUNCA se recorta lo nuevo — se traen TODOS los UID > last_uid
        # (por grande que sea el hueco), unidos a la ventana reciente de siempre (para que
        # el buzón no encoja de un pulso a otro cuando no hay hueco).
        todos = mb.all_uids()
        nuevos = [u for u in todos if u > last_uid]
        ventana = todos[-BUZON_MAX:]
        recientes = sorted(set(nuevos) | set(ventana))
    entradas = [_entry(uid, mb.fetch_header(uid)) for uid in recientes]
    entradas.sort(key=lambda e: e["uid"], reverse=True)   # más nuevo primero

    avisos = 0
    if alertar and not baseline:
        nuevas = [e for e in entradas if e["uid"] > last_uid]
        for e in sorted(nuevas, key=lambda e: e["uid"]):   # en orden de llegada
            if not e["urgente"]:
                continue
            # Anti-dup: solo avisa si nadie (poller o daemon LLM) avisó ya de este correo.
            if not (dry or correo.reclamar_aviso(e["remitente_email"], e["asunto"])):
                continue
            texto = "📬 Correo nuevo de %s: «%s». Está en tu bandeja." % (e["remitente"], e["asunto"])
            try:
                avisar(texto, e["ned_critico"])   # rompe silencio nocturno solo si NED-crítico
                correo.registrar("aviso-imap", e["message_id"] or str(e["uid"]),
                                 motivo="urgente:%s" % ("ned" if e["ned_critico"] else "cita"))
                avisos += 1
            except Exception:
                pass

    nuevo_last = max([e["uid"] for e in entradas] + [last_uid]) if entradas else last_uid
    buzon = {"actualizado": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
             "uidvalidity": mb.uidvalidity, "last_uid": nuevo_last, "mensajes": entradas}

    if not dry:
        correo._ensure()
        correo._write_atomic(seen_path, {"uidvalidity": mb.uidvalidity, "last_uid": nuevo_last,
                                         "actualizado": buzon["actualizado"]})
        correo._write_atomic(buzon_path, buzon)

    return {"nuevos": len([e for e in entradas if e["uid"] > last_uid]) if not baseline else 0,
            "avisos": avisos, "baseline": baseline, "buzon": buzon}


# Errores de red/servidor RECUPERABLES con reintento: el IMAP de Gmail corta la conexión
# (socket EOF, abort) cada cierto tiempo; sin reconexión el poller moría y el buzón se
# quedaba congelado (bug observado 30-jun: `imaplib.abort: ... socket error: EOF`).
_TRANSITORIOS = (imaplib.IMAP4.abort, imaplib.IMAP4.error, ssl.SSLError, socket.error,
                 OSError, EOFError, TimeoutError)


def _heartbeat(estado):
    """Late para que el dead-man (seguimiento._heartbeats_problema) SEPA si el poller se murió.
    El poller se invoca directo (no vía run_agent.sh), así que sin esto su caída era INVISIBLE —
    justo lo que dejó pasar desapercibida la muerte del correo/calendario el 30-jun. Tolerante:
    nunca rompe el pulso. Usa mtime del fichero como reloj (igual que calendar_sync.write_heartbeat)."""
    try:
        hb_dir = os.path.join(os.path.dirname(correo.CORREO_DIR), "heartbeat")
        os.makedirs(hb_dir, exist_ok=True)
        correo._write_atomic(os.path.join(hb_dir, "correo-imap.json"),
                             {"agente": "correo-imap", "estado": estado,
                              "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")})
    except Exception:
        pass


def once(dry=False, user=None, alertar=None, reintentos=3, backoff=2.0):
    """Un pulso del watcher con RECONEXIÓN ROBUSTA (reintento+backoff ante caídas transitorias
    de red/IMAP: socket EOF, abort, timeout — bug 30-jun) y HEARTBEAT para el dead-man.

    `user=None` = el pulso del poller 24/7 (com.btp.correo-imap): persiste en SEEN/BUZON por
    defecto y LATE el heartbeat. Pasar `user` explícito barre OTRA cuenta (p.ej. correo_triage.py,
    que mira AMBAS): cada una persiste en su propio marcador (path_por_cuenta) y NO toca el
    heartbeat del poller. Fail-closed silencioso si falta la clave o si agota los reintentos
    (no rompe launchd)."""
    es_poller = user is None            # solo el pulso principal late para el dead-man
    ultimo = None
    for intento in range(1, max(1, reintentos) + 1):
        mb = None
        seen_path = buzon_path = None
        try:
            if user:
                mb = _conectar(user=user)
                seen_path, buzon_path = path_por_cuenta(user)
            else:
                mb = _conectar()
            r = procesar(mb, dry=dry,
                         alertar=((not dry) if alertar is None else alertar),
                         seen_path=seen_path, buzon_path=buzon_path)
            if es_poller and not dry:
                _heartbeat("ok")
            return r
        except FaltaClave as e:
            return {"error": str(e), "fail_closed": True}   # sin clave = fail-closed, no es caída del poller
        except Exception as e:                     # noqa: BLE001 — filtramos por _TRANSITORIOS
            if not isinstance(e, _TRANSITORIOS):
                raise                              # error NO recuperable → sube tal cual
            ultimo = e
            if intento < reintentos:
                time.sleep(backoff * intento)      # backoff creciente (2s, 4s, …) antes de reconectar
        finally:
            if mb is not None:
                mb.close()                         # cierra la conexión muerta antes de reintentar
    if es_poller and not dry:
        _heartbeat("fallo")                        # agotó reintentos: el dead-man debe verlo
    return {"error": "IMAP no disponible tras %d intentos: %r" % (reintentos, ultimo),
            "fail_closed": True, "transitorio": True}


def once_todas(dry=False, cuentas=None):
    """Un pulso para VARIAS cuentas (por defecto CUENTAS_DAEMON): tapa el punto ciego de
    titular@ sin tocar el comportamiento de siempre de `once()` para la cuenta por defecto
    (titular.mgp@ sigue usando SEEN/BUZON a secas → mismo fichero que ya lee vega_gate/HOY/el
    agente). Cada cuenta EXTRA persiste en su propio marcador (path_por_cuenta), así que no se
    pisan entre sí. Fail-soft POR CUENTA: si una falla (sin App Password, IMAP caído), las
    demás siguen — el resultado por cuenta dice qué pasó, nunca revienta el pulso completo.
    Devuelve {email_cuenta: resultado_de_once(...)}."""
    cuentas = cuentas or CUENTAS_DAEMON
    out = {}
    for user in cuentas:
        u = None if user == DEFAULT_USER else user   # None → comportamiento de siempre (SEEN/BUZON)
        try:
            out[user] = once(dry=dry, user=u)
        except Exception as e:
            out[user] = {"error": "%s: %s" % (type(e).__name__, e), "fail_closed": True}
    return out


def main(argv):
    cmd = argv[0] if argv else "buzon"
    if cmd in ("-h", "--help"):
        print(__doc__)
        return 0
    if cmd == "buzon":
        print(json.dumps(cargar_buzon(), ensure_ascii=False, indent=2))
        return 0
    if cmd == "once":
        dry = "--dry" in argv[1:]
        r = once(dry=dry)
        if r.get("error"):
            print("⚠️  " + r["error"], file=sys.stderr)
            return 0   # fail-closed silencioso: no rompe launchd
        print("nuevos=%d avisos=%d baseline=%s mensajes_en_buzon=%d%s"
              % (r["nuevos"], r["avisos"], r["baseline"], len(r["buzon"]["mensajes"]),
                 " (DRY: no se avisó ni persistió)" if dry else ""))
        return 0
    if cmd == "todas":
        dry = "--dry" in argv[1:]
        resultados = once_todas(dry=dry)
        for user, r in resultados.items():
            if r.get("error"):
                print("⚠️  %s: %s" % (user, r["error"]), file=sys.stderr)
                continue
            print("%s: nuevos=%d avisos=%d baseline=%s mensajes_en_buzon=%d%s"
                  % (user, r["nuevos"], r["avisos"], r["baseline"], len(r["buzon"]["mensajes"]),
                     " (DRY)" if dry else ""))
        return 0   # fail-closed silencioso por cuenta: no rompe launchd aunque una falle
    if cmd == "buscar":
        args2 = argv[1:]
        cuenta = "ambas"
        if "--cuenta" in args2:
            i = args2.index("--cuenta")
            cuenta = args2[i + 1] if i + 1 < len(args2) else "ambas"
            args2 = args2[:i] + args2[i + 2:]
        q = " ".join(args2).strip()
        if not q:
            print('uso: correo_imap.py buscar [--cuenta titular.mgp|titular] "<consulta>"', file=sys.stderr)
            return 2
        try:
            res = buscar(q, cuenta=cuenta)
        except FaltaClave as e:
            print("⚠️  " + str(e), file=sys.stderr)
            return 0
        print(json.dumps({"query": q, "n": len(res), "resultados": res}, ensure_ascii=False, indent=2))
        return 0
    print('uso: correo_imap.py [once [--dry] | buzon | buscar "<consulta>"]', file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
