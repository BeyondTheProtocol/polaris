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


# ── Estado del HILO: ¿de quién es el último mensaje? (13-oct-2026) ───────────────
# Por qué existe: `pendientes.py` decidía «¿contestó {{TITULAR}}?» solo con la flag \Answered del
# mensaje, y solo mientras ese mensaje siguiera en la ventana de buzon.json. Resultado medido
# el 13-oct: 1557 pendientes abiertos y 38 cerrados, con el aviso amordazado porque insistir
# sobre eso era spam. La verdad está en el hilo: si el último mensaje es suyo, la pelota está
# en el tejado del otro. Esto lo mira en Todo el correo (recibidos y enviados juntos), en
# TODAS sus cuentas, SOLO LECTURA y sin leer cuerpos.
MIS_DIRECCIONES = frozenset(u.lower() for u, _ in CUENTAS)
HILO_MAX = 25   # mensajes de un hilo que se miran como mucho (los más recientes)

asunto_base = correo.asunto_base   # «X» y «Re: X» son el mismo hilo


def es_mia(direccion):
    """¿Esta dirección es una de las cuentas de {{TITULAR}}? (quién escribió el último mensaje)."""
    return (email.utils.parseaddr(direccion or "")[1] or "").lower() in MIS_DIRECCIONES


class TodoElCorreo(ImapMailbox):
    """Una cuenta abierta en «Todo el correo» (readonly): búsquedas por hilo de Gmail."""

    def __init__(self, M, cuenta):
        _select_todo_el_correo(M)
        super().__init__(M)
        self.cuenta = cuenta

    def buscar_raw(self, query):
        q = _solo_ascii(str(query)).replace('"', " ").replace("\\", " ").strip()
        typ, data = self._M.uid("search", "X-GM-RAW", '"%s"' % q)
        return sorted(int(x) for x in data[0].split()) if (typ == "OK" and data and data[0]) else []

    def thrid(self, uid):
        typ, data = self._M.uid("fetch", str(uid), "(X-GM-THRID)")
        for part in data or []:
            raw = part[0] if isinstance(part, tuple) else part
            m = re.search(rb"X-GM-THRID (\d+)", raw or b"")
            if m:
                return m.group(1).decode("ascii")
        return ""

    def uids_de_hilo(self, thrid):
        if not str(thrid).isdigit():
            return []
        typ, data = self._M.uid("search", "X-GM-THRID", str(thrid))
        return sorted(int(x) for x in data[0].split()) if (typ == "OK" and data and data[0]) else []


    def es_automatico(self, uid):
        """¿Este mensaje lo mandó una máquina? (respuesta automática, boletín, lista). Por las
        cabeceras que pone quien envía en masa, no por el texto. Un «fuera de la oficina» o
        un boletín de un hospital NO es algo que {{TITULAR}} tenga que contestar."""
        typ, data = self._M.uid(
            "fetch", str(uid),
            "(BODY.PEEK[HEADER.FIELDS (AUTO-SUBMITTED PRECEDENCE LIST-UNSUBSCRIBE LIST-ID X-AUTOREPLY)])")
        raw = b""
        for part in data or []:
            if isinstance(part, tuple) and len(part) == 2:
                raw = part[1] or b""
        return cabeceras_de_maquina(email.message_from_bytes(raw))

    def fetch_cuerpo(self, uid, max_bytes=None):
        """Bytes crudos del mensaje, con PEEK (no lo marca como leído) y con tope."""
        typ, data = self._M.uid("fetch", str(uid),
                                "(BODY.PEEK[]<0.%d>)" % int(max_bytes or CUERPO_MAX_BYTES))
        for part in data or []:
            if isinstance(part, tuple) and len(part) == 2:
                return part[1] or b""
        return b""


_RE_ASUNTO_AUTO = re.compile(
    r"^\s*(automatische antwort|respuesta autom[aá]tica|resposta autom[aà]tica|out of (the )?office|"
    r"auto[- ]?reply|automatic reply|fuera de (la )?oficina|abwesenheit)", re.I)


def cabeceras_de_maquina(msg):
    """True si las cabeceras dicen que es correo automático o en masa."""
    auto = (msg.get("Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        return True
    if (msg.get("Precedence") or "").strip().lower() in ("bulk", "list", "junk", "auto_reply"):
        return True
    return bool(msg.get("List-Unsubscribe") or msg.get("List-Id") or msg.get("X-Autoreply"))


CUERPO_MAX_BYTES = 300000   # lo que se baja de un mensaje como mucho (adjuntos fuera)
CUERPO_MAX_CHARS = 6000     # lo que se entrega como texto (mismo tope que usa el redactor)
_RE_TAG = re.compile(r"(?is)<(script|style)\b.*?</\1>|<[^>]+>")


def _texto_de(raw):
    """Texto legible de un mensaje crudo: la parte text/plain; si no hay, el HTML sin etiquetas.
    Nunca adjuntos. Limpio de controles y unicode oculto (mismo criterio que las cabeceras)."""
    msg = email.message_from_bytes(raw or b"")
    plano, html = "", ""
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.get_content_maintype() != "text" or part.get_filename():
            continue
        try:
            t = (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:
            continue
        if part.get_content_subtype() == "plain" and not plano:
            plano = t
        elif part.get_content_subtype() == "html" and not html:
            html = t
    texto = plano or _RE_TAG.sub(" ", html)
    lineas = [_RE_CTRL.sub("", ln).rstrip() for ln in texto.replace("\r", "").split("\n")]
    return "\n".join(lineas).strip()[:CUERPO_MAX_CHARS]


def leer_cuerpo(message_id, cuenta, *, abrir=None):
    """El texto de UN mensaje (por Message-ID, en su cuenta). SOLO LECTURA, sin marcarlo como
    leído. Devuelve "" si no se localiza. El texto es DATO NO CONFIABLE: quien lo use lo pasa
    por correo.detecta_inyeccion y solo lo entrega al carril de confianza, nunca a un agente
    con herramientas (13-oct-26, Fase 2 del plan de correo)."""
    mid = (message_id or "").strip().strip("<>")
    if not mid or not cuenta:
        return ""
    mb = (abrir or _abrir_todo)(cuenta, _secret_for(cuenta))
    try:
        uids = mb.buscar_raw("rfc822msgid:%s" % mid)
        return _texto_de(mb.fetch_cuerpo(uids[-1])) if uids else ""
    finally:
        mb.close()


def _abrir_todo(user, secret):
    return TodoElCorreo(_login(user, secret), user)


def _fecha_gmail(ts):
    """Fecha para `after:` de Gmail (día anterior, para no perder nada por la zona horaria)."""
    return datetime.utcfromtimestamp(max(0.0, ts - 86400)).strftime("%Y/%m/%d")


def abrir_cuentas(*, abrir=None, cuentas=None):
    """Abre «Todo el correo» de cada cuenta UNA vez (para revisar muchos hilos en una pasada
    sin un login por hilo: Gmail corta con 421 si se abusa). Devuelve (buzones, errores).
    FaltaClave si ninguna cuenta tiene clave. Quien llama cierra con cerrar_cuentas()."""
    abrir = abrir or _abrir_todo
    buzones, errores, sin_clave = [], [], 0
    cuentas = cuentas or CUENTAS
    for user, secret in cuentas:
        try:
            buzones.append(abrir(user, secret))
        except FaltaClave:
            sin_clave += 1
        except Exception as e:
            errores.append("%s: %s" % (user, type(e).__name__))
    if not buzones and sin_clave == len(cuentas):
        raise FaltaClave("ninguna cuenta tiene App Password en el Llavero")
    return buzones, errores


def cerrar_cuentas(buzones):
    for mb in buzones or []:
        mb.close()


def estado_hilo(message_id="", *, remitente_email="", asunto="", fecha="", abrir=None,
                cuentas=None, buzones=None):
    """¿De quién es el ÚLTIMO mensaje del hilo? Devuelve
        {encontrado, turno: "titular"|"otro", thrid, cuenta, n, ultimo_fecha, ultimo_ts,
         ultimo_remitente, via: "hilo"|"enviado-aparte", errores}
    `turno` dice a quién le toca: "titular" si lo último es de un tercero, "otro" si lo último
    lo escribió ella. `encontrado=False` = no se pudo localizar (fail-safe: quien llama NO
    cambia nada; nunca se da por contestado lo que no se ha visto).

    Se localiza por Message-ID (rfc822msgid:) y, si falta, por remitente + asunto. Además se
    busca en cada cuenta un correo SUYO a ese remitente posterior al último de él: cubre el
    caso de contestar desde la otra cuenta o en un hilo nuevo (`via="enviado-aparte"`).
    `abrir(user, secret)` se inyecta en tests; por defecto abre IMAP real, readonly."""
    mid = (message_id or "").strip().strip("<>")
    rem = (remitente_email or "").strip().lower()
    base = asunto_base(asunto)
    propios = buzones is None
    errores = []
    if propios:
        buzones, errores = abrir_cuentas(abrir=abrir, cuentas=cuentas)
    mejor, aparte = None, None
    try:
        for mb in buzones:
            user = getattr(mb, "cuenta", "")
            try:
                uids = mb.buscar_raw("rfc822msgid:%s" % mid) if mid else []
                if not uids and rem and base:
                    uids = mb.buscar_raw("from:%s subject:(%s)" % (rem, base))
                if uids:
                    thrid = mb.thrid(uids[-1])
                    hilo = (mb.uids_de_hilo(thrid) or uids)[-HILO_MAX:]
                    ents = [_entry(u, mb.fetch_header(u), cuenta=user) for u in hilo]
                    ents = [e for e in ents if _ts(e) > 0] or ents
                    ult = max(ents, key=_ts)
                    cand = {"encontrado": True, "thrid": thrid, "cuenta": user, "n": len(ents),
                            "ultimo_ts": _ts(ult), "ultimo_fecha": ult.get("fecha", ""),
                            "ultimo_remitente": ult.get("remitente_email", ""),
                            "turno": "otro" if es_mia(ult.get("remitente_email")) else "titular",
                            "via": "hilo"}
                    if cand["turno"] == "titular":
                        auto = bool(_RE_ASUNTO_AUTO.match(ult.get("asunto", "")))
                        if not auto and hasattr(mb, "es_automatico"):
                            auto = bool(mb.es_automatico(ult["uid"]))
                        cand["automatico"] = auto
                    if mejor is None or cand["ultimo_ts"] > mejor["ultimo_ts"]:
                        mejor = cand
                if rem and "@" in rem:
                    ref = _ts({"fecha": fecha}) or 0.0
                    env = mb.buscar_raw("in:sent to:%s%s" % (
                        rem, (" after:%s" % _fecha_gmail(ref)) if ref else ""))
                    if env:
                        e = _entry(env[-1], mb.fetch_header(env[-1]), cuenta=user)
                        if aparte is None or _ts(e) > _ts(aparte):
                            aparte = e
            except Exception as e:
                errores.append("%s: %s" % (user, type(e).__name__))
    finally:
        if propios:
            cerrar_cuentas(buzones)
    if mejor is None:
        return {"encontrado": False, "errores": errores}
    if (mejor["turno"] == "titular" and aparte is not None
            and _ts(aparte) > mejor["ultimo_ts"]):
        mejor.update(turno="otro", via="enviado-aparte", ultimo_ts=_ts(aparte),
                     ultimo_fecha=aparte.get("fecha", ""),
                     ultimo_remitente=aparte.get("remitente_email", ""))
    mejor["errores"] = errores
    return mejor


# ── Estado propio (no toca procesados.json del triaje del agente) ────────────────
def _load_seen(path=None):
    try:
        with open(path or SEEN, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _slug_cuenta(user):
    """Slug determinista de un email. Mismo que usan los ficheros por cuenta, para que el
    `id` de un mensaje y su buzón hablen del mismo sitio."""
    return re.sub(r"[^a-z0-9]+", "-", (user or "").lower()).strip("-") or "default"


def path_por_cuenta(user):
    """Rutas (seen, buzon) PROPIAS de una cuenta, para no pisar el estado del poller
    principal (que usa SEEN/BUZON a secas para BTP_GMAIL_USER). Determinista: un slug simple
    del email basta (no hay colisiones entre las 3 cuentas de CUENTAS)."""
    slug = _slug_cuenta(user)
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


def _entry(uid, h, cuenta=None):
    """Una cabecera, normalizada. `cuenta` es CRÍTICA desde que buzon.json es multi-cuenta:
    los UID solo son únicos DENTRO de un buzón, así que sin ella dos mensajes distintos de
    cuentas distintas pueden compartir uid y uno desaparece del set de ids que llevan
    vega_gate y centinela_ned (ambos resuelven `m["id"] or m["uid"] or m["message_id"]`).
    Por eso el `id` lleva la cuenta delante: es lo que leen ellos primero, sin tocarlos."""
    frm = h.get("from", "")
    subj_raw = _decode(h.get("subject", ""))
    inj = correo.detecta_inyeccion(subj_raw) or correo.detecta_inyeccion(frm)
    subj = "[asunto retenido por revisión]" if inj else _limpia(subj_raw)
    return {
        "uid": int(uid),
        "id": "%s:%s" % (_slug_cuenta(cuenta), uid) if cuenta else str(uid),
        "cuenta": cuenta or "",
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


# ── Cola de avisos urgentes PENDIENTES (10-oct-26) ─────────────────────────────────────────────
# Antes: `procesar` reclamaba el aviso en el ledger, llamaba a `avisar` DESCARTANDO el resultado y avanzaba
# `last_uid`. Con HALT (20 avisos `urgente:ned`, 13-jul → 17-sep) o un fallo de red, el correo urgente no se
# avisaba NUNCA: el uid ya estaba pasado y la clave del ledger ya reclamada. Ahora un aviso que no llega
# (a) libera su reclamo y (b) se apunta aquí, en un fichero propio por cuenta; cada pasada reintenta SIN
# reprocesar el buzón, con espera entre intentos y como UN solo aviso agrupado (el cupo diario es 3: N correos
# acumulados no pueden ser N avisos al levantar el HALT).
PEND_ESPERA_S = int(os.environ.get("BTP_AVISOS_PEND_ESPERA_S", "300"))   # entre intentos de la cola
PEND_MAX = 200          # tope de lo guardado (el más viejo cede el sitio; se dice por stderr, no en silencio)
PEND_LISTA = 8          # cuántos correos se nombran en el aviso agrupado (el resto, «y N más»)


def _pend_path(seen_path):
    base, _ext = os.path.splitext(seen_path)
    return base + "-avisos-pendientes.json"


def _pend_cargar(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("items"), list):
            return d
    except Exception:
        pass
    return {"items": [], "ultimo_intento": 0}


def _pend_guardar(path, d):
    correo._ensure()
    correo._write_atomic(path, d)


def _llega(res):
    import errores
    return errores.aviso_en_camino(res)[0]


def _pend_anotar(path, e):
    """Apunta un correo urgente cuyo aviso no llegó (idempotente por uid/message_id)."""
    d = _pend_cargar(path)
    tid = e["message_id"] or str(e["uid"])
    if any(i.get("tid") == tid for i in d["items"]):
        return
    d["items"].append({"tid": tid, "uid": e["uid"], "remitente": e["remitente"],
                       "remitente_email": e["remitente_email"], "asunto": e["asunto"],
                       "ned": bool(e["ned_critico"]), "desde": time.time()})
    if len(d["items"]) > PEND_MAX:
        sys.stderr.write("correo_imap: cola de avisos pendientes llena (%d); cede el más viejo\n" % PEND_MAX)
        d["items"] = d["items"][-PEND_MAX:]
    d["ultimo_intento"] = max(d.get("ultimo_intento", 0), time.time())
    _pend_guardar(path, d)


def _texto_pendientes(items):
    if len(items) == 1:
        i = items[0]
        return "📬 Correo nuevo de %s: «%s». Está en tu bandeja." % (i["remitente"], i["asunto"])
    lista = "\n".join("· %s: «%s»" % (i["remitente"], i["asunto"]) for i in items[:PEND_LISTA])
    if len(items) > PEND_LISTA:
        lista += "\n… y %d más (están en tu bandeja)." % (len(items) - PEND_LISTA)
    return "📬 Mientras no pude avisarte llegaron %d correos urgentes:\n%s" % (len(items), lista)


def _reintentar_pendientes(path, avisar, ahora=None):
    """Reintenta la cola como UN aviso. Devuelve cuántos correos quedaron avisados. Respeta la espera entre
    intentos. Re-reclama cada correo en el ledger: si otro avisador (centinela, daemon LLM) ya lo avisó, se
    descarta de la cola sin duplicar."""
    d = _pend_cargar(path)
    if not d["items"]:
        return 0
    ahora = ahora if ahora is not None else time.time()
    if ahora - float(d.get("ultimo_intento", 0)) < PEND_ESPERA_S:
        return 0
    reclamados, ya_avisados = [], []
    for i in d["items"]:
        try:
            ok = correo.reclamar_aviso(i["remitente_email"], i["asunto"])
        except Exception:
            ok = True
        (reclamados if ok else ya_avisados).append(i)
    llego = False
    if reclamados:
        try:
            llego = _llega(avisar(_texto_pendientes(reclamados), any(i["ned"] for i in reclamados)))
        except Exception:
            llego = False
    if reclamados and not llego:
        for i in reclamados:                      # no llegó: se libera el reclamo y se reintenta más tarde
            try:
                correo.liberar_aviso(i["remitente_email"], i["asunto"])
            except Exception:
                pass
        d["items"] = reclamados
        d["ultimo_intento"] = ahora
        d["intentos"] = int(d.get("intentos", 0)) + 1
        _pend_guardar(path, d)
        return 0
    for i in reclamados:
        correo.registrar("aviso-imap", i["tid"], motivo="urgente:%s:cola" % ("ned" if i["ned"] else "cita"))
    _pend_guardar(path, {"items": [], "ultimo_intento": ahora})
    return len(reclamados)


def procesar(mb, *, dry=False, alertar=True, avisar=None, seen_path=None, buzon_path=None,
             cuenta=None):
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
    entradas = [_entry(uid, mb.fetch_header(uid), cuenta) for uid in recientes]
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
            llego = False
            try:
                # rompe silencio nocturno solo si NED-crítico. El resultado SE LEE: `salida` no lanza cuando
                # bloquea (HALT) o aplaza, devuelve un dict (ver errores.aviso_en_camino).
                llego = _llega(avisar(texto, e["ned_critico"]))
                if llego:
                    correo.registrar("aviso-imap", e["message_id"] or str(e["uid"]),
                                     motivo="urgente:%s" % ("ned" if e["ned_critico"] else "cita"))
                    avisos += 1
            except Exception:
                llego = False
            if not llego and not dry:
                # No llegó: se libera el reclamo (si no, nadie volvería a avisar de este correo) y se apunta
                # para reintentarlo sin reprocesar el buzón, aunque `last_uid` avance.
                try:
                    correo.liberar_aviso(e["remitente_email"], e["asunto"])
                except Exception:
                    pass
                _pend_anotar(_pend_path(seen_path), e)
        if not dry:
            avisos += _reintentar_pendientes(_pend_path(seen_path), avisar)

    nuevo_last = max([e["uid"] for e in entradas] + [last_uid]) if entradas else last_uid
    buzon = {"actualizado": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
             "uidvalidity": mb.uidvalidity, "last_uid": nuevo_last,
             "cuenta": cuenta or "", "mensajes": entradas}

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
                         seen_path=seen_path, buzon_path=buzon_path,
                         cuenta=(user or os.environ.get("BTP_GMAIL_USER") or DEFAULT_USER))
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
    """Un pulso para las cuentas del daemon (por defecto CUENTAS_DAEMON), y `buzon.json` como
    UNIÓN de todas. Devuelve {email: resultado_de_once(...)}.

    POR QUÉ ASÍ (20-sep-2026). Antes esta función llamaba a `once(user=None)` para
    DEFAULT_USER, y `_login` resuelve `user = BTP_GMAIL_USER or DEFAULT_USER`: con la variable
    puesta en el plist del daemon, esa iteración barría OTRA vez la cuenta del entorno y
    `titular.mgp@` no se barría NUNCA. Un pulso «para todas» que dependía de una variable de
    entorno para saber cuáles. Ahora cada cuenta va con `user` EXPLÍCITO, así que el entorno
    no puede cambiar qué se barre.

    Y `buzon.json` —lo que leen vega_gate, centinela_ned, pendientes y reconciliar_estado—
    pasa a ser la unión de las cuentas, no el buzón de una de ellas: mientras fue de una sola,
    el correo de la otra era invisible para todos ellos. Cada cuenta conserva además su propio
    marcador incremental y su propio fichero (`path_por_cuenta`), que es lo que hace el
    catch-up correcto por cuenta.

    Fail-soft POR CUENTA: si una falla (sin App Password, IMAP caído), las demás siguen y la
    unión se compone con lo que haya — el resultado por cuenta dice qué pasó.
    """
    cuentas = cuentas or CUENTAS_DAEMON
    _siembra_marcador_por_cuenta(cuentas)
    out, latio = {}, False
    for user in cuentas:
        try:
            out[user] = once(dry=dry, user=user)
        except Exception as e:                     # noqa: BLE001
            out[user] = {"error": "%s: %s" % (type(e).__name__, e), "fail_closed": True}
        if not dry and not latio and not out[user].get("error"):
            _heartbeat("ok")                       # el dead-man late una vez por pulso
            latio = True
    if not dry:
        _componer_buzon_union(cuentas)
    return out


def _siembra_marcador_por_cuenta(cuentas):
    """Migración idempotente: pasa el frente de `imap_seen.json` al marcador POR CUENTA.

    Hasta ahora una de las cuentas se barría como `once(user=None)` y avanzaba el marcador
    GLOBAL (`imap_seen.json`); su marcador por cuenta se quedó atrás. Al pasar todas las
    cuentas a `user` explícito, esa cuenta partiría de un `last_uid` viejo y trataría como
    NUEVOS los correos ya procesados: una avalancha de avisos de correo que {{TITULAR}} ya leyó.
    (Medido el 20-sep-2026: 91849 en el global contra 91693 en el de la cuenta, 156 UID.)

    Solo se adopta el frente global cuando es LA MISMA carpeta (`uidvalidity` idéntico) y va
    por DELANTE. Si no coinciden, no se toca nada: mejor un catch-up que pisar un marcador
    bueno. No borra nada ni retrocede ningún frente.
    """
    glob_seen = _load_seen(SEEN)
    if not glob_seen:
        return
    for user in cuentas:
        seen_path, _b = path_por_cuenta(user)
        propio = _load_seen(seen_path) or {}
        if propio.get("uidvalidity") != glob_seen.get("uidvalidity"):
            continue                       # otra carpeta: el frente global no dice nada de ella
        if int(propio.get("last_uid", 0)) >= int(glob_seen.get("last_uid", 0)):
            continue                       # ya está al día o por delante
        correo._ensure()
        correo._write_atomic(seen_path, {"uidvalidity": glob_seen.get("uidvalidity"),
                                         "last_uid": glob_seen.get("last_uid"),
                                         "actualizado": glob_seen.get("actualizado")})


def _componer_buzon_union(cuentas):
    """Escribe BUZON (buzon.json) con los mensajes de TODAS las cuentas, más nuevo primero.

    No vuelve a la red: relee lo que cada cuenta acaba de persistir en su propio fichero. El
    recorte a BUZON_MAX es POR CUENTA, no sobre el total: si se recortara la mezcla, una cuenta
    ruidosa dejaría fuera los mensajes de la otra — el mismo tipo de pérdida silenciosa que el
    catch-up de `procesar()` existe para evitar.

    `last_uid`/`uidvalidity` no aparecen en la raíz a propósito: no significan nada para un
    fichero multi-cuenta, y el marcador incremental de verdad vive en los `imap_seen-*.json`.
    Comprobado (20-sep-2026) que ningún consumidor los lee de aquí.
    """
    mensajes, estado = [], {}
    for user in cuentas:
        _seen, buzon_path = path_por_cuenta(user)
        b = cargar_buzon(buzon_path)
        msgs = [m for m in (b.get("mensajes") or []) if isinstance(m, dict)]
        msgs.sort(key=lambda m: m.get("uid", 0), reverse=True)
        mensajes += msgs[:BUZON_MAX]
        estado[user] = {"actualizado": b.get("actualizado"), "last_uid": b.get("last_uid"),
                        "uidvalidity": b.get("uidvalidity"), "mensajes": len(msgs)}
    mensajes.sort(key=lambda m: (m.get("cuenta", ""), m.get("uid", 0)), reverse=True)
    correo._ensure()
    correo._write_atomic(BUZON, {
        "actualizado": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "cuentas": estado, "mensajes": mensajes})


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
