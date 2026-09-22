#!/usr/bin/env python3
"""tools/correo.py — reglas DURAS del gestor de correo de {{TITULAR}} (hacia NED).

El conector de Gmail lo maneja el AGENTE (un script no puede llamarlo). Pero las
SALVAGUARDAS no se confían al juicio del modelo leyendo correos (terreno de inyección):
viven AQUÍ, en código determinista, y el agente DEBE pasar por ellas. Espejo del patrón
allowlist fail-closed de seguimiento.construir_export.

Garantías (verificadas en tests/test_correo.py):
  · etiqueta_permitida(): allowlist de etiquetas aplicables — NUNCA TRASH/SPAM (no borrado).
  · destinatario_borrador_valido(): un borrador SOLO puede ir al remitente del propio hilo
    (cierra el reenvío-a-tercero como vector de exfiltración).
  · detecta_inyeccion(): patrones de orden al sistema / unicode oculto → cuarentena.
  · es_ned_critico()/puede_archivar(): nunca se archiva un hilo de un remitente NED-crítico.
  · es_urgente(): conservador (remitente NED-crítico o cita/plazo), no por "URGENTE" a secas.
  · log reversible de cada acción (deshacer en bloque).

Sin dependencias (stdlib).
"""
import fcntl
import hashlib
import json
import os
import re
import sys
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
CORREO_DIR = os.path.join(STATE, "correo")
LOG = os.path.join(CORREO_DIR, "acciones.jsonl")        # log reversible (append-only)
PROCESADOS = os.path.join(CORREO_DIR, "procesados.json")  # ids ya vistos (incremental)
AVISOS = os.path.join(CORREO_DIR, "avisos.json")        # ledger anti-dup de avisos urgentes
CUMBRE = os.path.join(STATE, "cumbre.json")

# ── Etiquetas que el agente PUEDE aplicar (allowlist; fail-closed) ──────────────
# Construido sobre la taxonomía que {{TITULAR}} YA tiene + las nuevas del esquema "fino +
# monitorizado + organizado" (2/7/26, PROPUESTA — pendiente de su OK, ver correo_triage.py
# categoria_de()). NUNCA TRASH/SPAM: aplicar TRASH = papelera = borrado diferido a 30 días,
# justo lo que el muro prohíbe.
ETIQUETAS_OK = {
    "HelpTitular", "HelpTitular/Ofrecimientos", "HelpTitular/Pacientes-Dudas",
    "HelpTitular/Onco-Experto", "HelpTitular/Prensa", "HelpTitular/Finanzas-Donaciones",
    "HelpTitular/Legal", "HelpTitular/Logística-Muestras",
    "Atender - tratamiento", "🗑 Limpiar", "Propuesta", "🛡 Revisar-inyección",
    # Esquema de categorización propuesto (esperando OK de {{TITULAR}}; correo_triage.py --dry
    # ya las usa para SIMULAR, aplicarlas en vivo requiere el conector Gmail en sesión):
    "NED/Médico", "Admin", "Personal", "Promos", "Polaris",
    # Ledger visible en la propia bandeja (11-jul-2026, alcance C): lo que sabe tools/pendientes.py
    # (qué espera SU respuesta / qué ya se cerró) también se ve en Gmail al primer vistazo, sin
    # abrir el bot. El agente las aplica desde el mismo hilo que persigue el ledger — nunca en
    # lote sobre correo que el ledger no haya calificado.
    "⏳ Pendiente", "✅ Respondido",
}
# Etiquetas de sistema cuyo USO está PROHIBIDO (borrado / mover a spam).
ETIQUETAS_VETADAS = {"TRASH", "SPAM"}
# Para "archivar" solo se permite QUITAR INBOX (reversible), nunca aplicar TRASH.
ARCHIVAR_QUITA = "INBOX"


def etiqueta_permitida(label):
    """True solo si el agente puede aplicar esa etiqueta. Fail-closed: lo no listado, NO."""
    if not label:
        return False
    if label.upper() in ETIQUETAS_VETADAS:
        return False
    return label in ETIQUETAS_OK


# ── Remitentes/temas NED-CRÍTICOS (nunca archivar; disparan aviso urgente) ──────
# Base + lo que se pueda derivar de cumbre.json. Substrings en minúscula sobre
# remitente/asunto. Mantener acotado (no inflar) para no volver "urgente" todo.
NED_CRITICOS = {
    # médicos / equipo
    "contacto", "contacto", "{{CONTACTO}}", "{{CENTRO}}", "{{CENTRO}}",
    "contacto", "contacto", "{{CENTRO}}", "dana farber", "dfci", "harvard",
    "fredhutch", "fred hutch", "fhcc", "moffitt", "stemvac", "veatch", "hunter",
    "soyano", "contacto", "{{CENTRO}}", "{{CENTRO}}", "contacto", "{{CONTACTO}}",
    "bassani", "lausanne", "chuv", "ludwig", "contacto", "contacto", "contacto",
    # ensayos
    "nct05098210", "nct07112053", "nct06691035", "nct07222267", "contacto",
    # logística de muestra
    "biomedical", "courier", "cold chain", "biopsia", "biopsy",
}
# Señales de urgencia "duras" (cita/plazo) — NO incluye "urgente" a secas (lo inflan spammers).
# `sign` con lookahead negativo para «sign-up» / «sign up» (20-sep-2026): un correo de marketing
# de Amazon («Earn $12 per eligible Prime sign-up») salía como «✍️ espera tu respuesta». De paso
# entran `signature`, `signed` y `sign-off`, que NO casaban con `\bsign\b` y sí son señal de firma.
# Comprobado que siguen casando: «please sign the consent form», «DocuSign: please sign».
RE_CITA = re.compile(r"\b(cita|appointment|schedul|reschedul|fecha|deadline|due\s+date|"
                     r"firma|sign(?![-\s]?up)(ature|ed|[-\s]?off)?|consent|washout|"
                     r"resultado|results)\b", re.I)


def _norm(*partes):
    return " ".join(p for p in partes if p).lower()


# Palabras NED que SOLO cuentan en el remitente (22-sep-2026). «harvard» en un asunto lo escribe
# cualquiera que presuma de benchmark: Glass Health («Glass ranks #1 on Harvard-Stanford ARISE
# SCT-Bench», updates@glass.health) salía como NED-crítico y urgente. El correo real de ese mundo
# llega DESDE *.harvard.edu (dfci, hms, mgh) o con «Harvard» en el nombre visible, y ahí sigue
# casando. Medido sobre el buzón (189 correos, 22-sep): ningún NED real dependía de «harvard» en
# el asunto; los NED por asunto que sí existen ({{CENTRO}}, DFCI, {{CENTRO}}…) no se tocan.
# Lista CORTA: solo lo que un falso positivo real ha demostrado ambiguo.
_NED_SOLO_REMITENTE = {"harvard"}


def es_ned_critico(sender="", subject=""):
    rem = _norm(sender)
    t = _norm(sender, subject)
    return any(k in (rem if k in _NED_SOLO_REMITENTE else t) for k in NED_CRITICOS)


def puede_archivar(sender="", subject=""):
    """Solo se archiva (quitar INBOX) lo que NO es NED-crítico. Fail-safe: ante duda, NO."""
    return not es_ned_critico(sender, subject)


# Dominios de CI/desarrollo. Un médico no escribe desde github.com: si una palabra clínica aparece
# en una notificación de estos, es el título de un PR, no correspondencia. Lista CORTA y explícita,
# comparada por ETIQUETAS de dominio (nunca substring), y con un uso deliberadamente estrecho.
_CI_DEV = ("github.com", "gitlab.com", "netlify.com", "netlify.app",
           "vercel.com", "circleci.com")


def es_ci_dev(sender=""):
    """¿El remitente es un bot de CI/desarrollo? (20-sep-2026)

    Se usa para DEGRADAR (quitar urgencia y sacar del parte rojo), NUNCA para desmarcar lo
    NED-crítico. `puede_archivar()` y `es_ruido()` no la ven a propósito: el `From` lo escribe el
    remitente, y una guarda de archivado fail-closed no puede depender de un campo que él controla.
    Medido el 20-sep: de 29 correos NED-críticos en el buzón, 10 eran de github.com."""
    m = re.search(r"([\w.+-]+)@([\w.-]+)", sender or "")
    if not m:
        return False
    dom = m.group(2).lower().strip(".")
    return any(_casa_dominio(dom, p) for p in _CI_DEV)


def es_urgente(sender="", subject=""):
    """Conservador: remitente NED-crítico, o señal dura de cita/plazo. No por 'urgente' suelto.

    Un bot de CI no es urgente aunque el título del PR lleve una palabra clínica; sigue siendo
    NED-crítico para `puede_archivar`, que es la guarda que no se toca."""
    if es_ned_critico(sender, subject):
        return not es_ci_dev(sender)
    return bool(RE_CITA.search(_norm(subject)))


# ── Lista de RUIDO (filtros de Gmail) — remitentes que NO suben al triaje ────────
# Config como DATO (tools/config/vega_ruido.json): dominios spam/newsletters/recibos
# que {{TITULAR}} ya filtra en Gmail. Coincidencia por DOMINIO del 'from', SUBDOMINIOS
# incluidos. FAIL-SAFE: si falta/ilegible/enabled=false → es_ruido() == False (no
# filtra nada, comportamiento idéntico a antes; nunca peta la tubería).
# egarante.com JAMÁS se trata como ruido.
_RUIDO_CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "vega_ruido.json")
_RUIDO_CATS = ("spam", "newsletters", "recibos")    # estas SÍ se filtran (NO 'evidencia_conservar')
_RE_DOMINIO = re.compile(r"@([\w.-]+)")


def _cargar_ruido():
    """Lee la config de ruido. Fail-safe TOTAL: cualquier problema (no existe, JSON roto,
    enabled!=true, estructura rara) → {} (= no hay ruido configurado). Nunca lanza."""
    try:
        with open(_RUIDO_CFG, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or d.get("enabled") is not True:
            return {}
        return d
    except Exception:
        return {}


def _dominios_remitente(sender=""):
    """Dominios (en minúscula) que aparecen en el 'from'. Soporta 'Nombre <a@b.com>' y 'a@b.com'."""
    return {m.group(1).lower().strip(".") for m in _RE_DOMINIO.finditer(sender or "")}


def _casa_dominio(dom, patron):
    """True si `dom` ES `patron` o es un SUBdominio suyo (events.holafly.com ⊂ holafly.com).
    Coincidencia por etiquetas de dominio, nunca substring (evita 'notholafly.com')."""
    dom, patron = dom.lower().strip("."), patron.lower().strip(".")
    return dom == patron or dom.endswith("." + patron)


def categoria_ruido(sender=""):
    """Categoría de ruido del remitente ('spam'/'newsletters'/'recibos') o None si no es ruido.
    egarante.com (evidencia_conservar) NUNCA cae aquí: esa categoría no se evalúa como filtro.
    Fail-safe: sin config → None (no filtra)."""
    cfg = _cargar_ruido()
    if not cfg:
        return None
    doms = _dominios_remitente(sender)
    if not doms:
        return None
    for cat in _RUIDO_CATS:
        for patron in (cfg.get(cat) or []):
            if isinstance(patron, str) and patron.strip():
                if any(_casa_dominio(dom, patron) for dom in doms):
                    return cat
    return None


def es_ruido(sender="", subject=""):
    """True si el remitente es ruido (spam/newsletter/recibo) y NO debe subir al triaje.
    NED-crítico SIEMPRE gana (un remitente que case ambas listas se conserva, fail-safe hacia
    no-perder). egarante.com nunca es ruido. Fail-safe: sin config → False."""
    if es_ned_critico(sender, subject):
        return False
    return categoria_ruido(sender) is not None


# ── Clasificación: persona real vs sistema automático ────────────────────────────
# Detecta si un correo viene de un humano vs una máquina (notificación, sistema,
# transaccional). Permite que Vega priorice correos de personas reales aunque no sean
# NED-críticos — diferencia la señal (periodistas, labs, contactos) del ruido de fondo.
#
# Lógica (conservadora, fail-safe hacia "persona"):
#   Si es NED-crítico -> persona (siempre, prioridad máxima).
#   Si el alias (parte antes del @) casa con patrones de robot -> robot.
#   Si el dominio es de plataforma de marketing conocida -> robot.
#   Si no casa ningun patron -> persona (fail-safe: mejor un falso "persona" que perder un humano).
_RE_ROBOT_ALIAS = re.compile(
    r"^(noreply|no[-_.]?reply|do[-_.]?not[-_.]?reply|donotreply|no[-_.]?responder|"
    r"newsletter|notif(ication(s)?)?|notification|updates?|alerts?|"
    r"mailer(-daemon)?|postmaster|daemon|bounce(s)?|unsubscribe|"
    r"billing|orders?|receipts?|invoices?|payments?|"
    r"automated?|robot|bot|service|services?|"
    r"news|digest|weekly|daily|monthly|promo(tion)?|marketing|deals?|"
    r"reply[-_.]to|replyto|replies?|"
    # ── Alias en ESPAÑOL (31-jul-26). La lista era solo inglesa, y los bancos y la
    # administración de aquí escriben desde `notificaciones@`, `avisos@`, `informacion@`.
    # Efecto real: el aviso mensual de MyInvestor (liquidación de intereses, 0,02 €, JavaMail)
    # pasaba como PERSONA REAL, y encima la fecha del asunto lo marcaba urgente, así que salía
    # como «✍️ espera tu respuesta» y se le preparaba borrador a un buzón que no lee nadie.
    # Este mismo fallo está escrito CUATRO veces en el libro de deuda por cuatro pasadas
    # distintas: detectarlo nunca fue el problema.
    r"notificacion(es)?|notificacao(es)?|aviso(s)?|informacion|información|info|"
    r"atencion[-_.]?cliente|atencionalcliente|clientes?|soporte|ayuda|contacto|"
    r"boletin|boletín|suscripcion(es)?|suscripción|comunicacion(es)?|comunicación|"
    r"facturacion|facturación|factura(s)?|pedidos?|envios?|envíos?|"
    r"automatico|automático|sistema|administracion|administración|admin)$", re.I
)
# Dominios de plataformas de marketing/transaccional (nunca son una persona real).
_ROBOT_DOMAINS = frozenset({
    "sendgrid.net", "mailchimp.com", "mailgun.org", "constantcontact.com",
    "klaviyo.com", "hubspot.com", "marketo.net",
    "amazonses.com", "bounce.twitter.com",
    "facebookmail.com", "email.linkedin.com", "notifications.google.com",
    "campaigns.google.com",
})


def es_persona_real(sender=""):
    """True si el correo viene probablemente de un humano (no de un sistema automatico).

    Fail-safe hacia "persona": si el alias no es claramente de robot, devuelve True.
    NED-critico siempre cuenta como persona (prioridad maxima).
    Sin sender identificable -> False (sin informacion suficiente).

    Util para que Vega distinga correos que merecen atencion humana del ruido de fondo,
    incluso cuando no son NED-criticos: un periodista, un lab, un colaborador nuevo.
    """
    if not sender:
        return False
    if es_ned_critico(sender):
        return True
    m = re.search(r"([\w.+-]+)@([\w.-]+)", sender)
    if not m:
        return False
    alias = m.group(1).lower().strip()
    dominio = m.group(2).lower().strip(".")
    if dominio in _ROBOT_DOMAINS:
        return False
    if _RE_ROBOT_ALIAS.match(alias):
        return False
    return True


# ── Categorización determinista (esquema "fino" — PROPUESTA, esperando OK de {{TITULAR}}) ────
# Motor de categoría por remitente/dominio/keywords, espejo del patrón de es_ruido()/
# categoria_ruido() (config como DATO, fail-safe: sin config → no rompe nada). Config en
# tools/config/vega_categorias.json. Devuelve una etiqueta de ETIQUETAS_OK o None (sin
# categoría clara → no se toca, mejor no-clasificar que mal-clasificar).
#
# Prioridad de reglas (de más a menos específica; NED-crítico SIEMPRE gana, igual que en
# es_ruido): NED/Médico > Prensa (HelpTitular/Prensa, ya existe) > Admin > Personal >
# Promos (ruido conocido) > Polaris (remitentes/asuntos del propio sistema) > None.
_CATEGORIAS_CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config",
                               "vega_categorias.json")
_RE_POLARIS = re.compile(r"\b(launchd|cron|daemon|healthcheck|dispatcher|com\.btp\.|"
                         r"polaris|codigo[- ]?rojo|c[oó]digo[- ]?rojo)\b", re.I)


def _cargar_categorias():
    """Config de categorización (dominios Admin/Personal + keywords). Fail-safe TOTAL:
    cualquier problema → {} (nada se categoriza fuera de NED/ruido, que ya tienen su propia
    fuente). Nunca lanza."""
    try:
        with open(_CATEGORIAS_CFG, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or d.get("enabled") is not True:
            return {}
        return d
    except Exception:
        return {}


def categoria_de(sender="", subject="", body=""):
    """Categoría DETERMINISTA propuesta para un correo, o None si no hay señal clara.
    Devuelve una de ETIQUETAS_OK (NED/Médico, HelpTitular/Prensa, Admin, Personal, Promos,
    Polaris) o None. Nunca sugiere TRASH/SPAM. Solo lectura de patrones — no decide nada
    por sí sola (el aplicar en vivo pasa por etiqueta_permitida() + el conector Gmail).
    """
    if es_ned_critico(sender, subject):
        return "NED/Médico"
    t = _norm(sender, subject)
    if _RE_POLARIS.search(t):
        return "Polaris"
    cat_ruido = categoria_ruido(sender)
    if cat_ruido in ("spam", "newsletters"):
        return "Promos"
    cfg = _cargar_categorias()
    doms = _dominios_remitente(sender)
    if cfg:
        for patron in (cfg.get("prensa") or []):
            if any(_casa_dominio(d, patron) for d in doms):
                return "HelpTitular/Prensa"
        for patron in (cfg.get("admin") or []):
            if any(_casa_dominio(d, patron) for d in doms):
                return "Admin"
        for kw in (cfg.get("admin_keywords") or []):
            if isinstance(kw, str) and kw.strip() and kw.lower() in t:
                return "Admin"
        for patron in (cfg.get("personal") or []):
            if any(_casa_dominio(d, patron) for d in doms):
                return "Personal"
    if cat_ruido == "recibos":
        return "Admin"
    if es_persona_real(sender) and not cfg:
        return None   # sin config de dominios, no adivinamos Personal por descarte
    return None


# ── Anti-inyección: el correo es DATO, no instrucciones ─────────────────────────
_RE_ZWSP = re.compile(r"[​‌‍⁠﻿]")            # zero-width / BOM
_RE_INJECT = re.compile(
    r"(ignora(r)?\s+(tus|las)\s+(reglas|instrucciones)|ignore\s+(your|all|previous)\s+"
    r"(instructions|rules)|soy\s+tu\s+admin|i\s*am\s+your\s+admin|system\s+prompt|"
    r"reenv[íi]a|forward\s+this|act\s+as|haz\s+caso\s+a\s+este|override|disregard\s+"
    r"(previous|prior)|env[íi]a\s+(a|to)\b|manda\s+esto\s+a)", re.I)


def detecta_inyeccion(text=""):
    """True si el texto trae patrones de orden al sistema o unicode oculto → cuarentena."""
    if not text:
        return False
    if _RE_ZWSP.search(text):
        return True
    return bool(_RE_INJECT.search(text))


# ── Borradores: SOLO respuesta al remitente del hilo (cierra exfiltración) ───────
def _emails(s):
    return set(m.group(0).lower() for m in re.finditer(r"[\w.+-]+@[\w-]+\.[\w.-]+", s or ""))


def destinatario_borrador_valido(remitente_hilo, destinatarios_propuestos):
    """Un borrador SOLO puede dirigirse al remitente del hilo original. Cualquier
    destinatario extra (cc/bcc/otro to) → INVÁLIDO (no se crea el borrador).
    `remitente_hilo`: el From del hilo. `destinatarios_propuestos`: lista/str.
    Devuelve (ok, motivo)."""
    origen = _emails(remitente_hilo)
    if not origen:
        return False, "sin remitente de hilo identificable → no se crea borrador"
    if isinstance(destinatarios_propuestos, str):
        destinatarios_propuestos = [destinatarios_propuestos]
    propuestos = set()
    for d in (destinatarios_propuestos or []):
        propuestos |= _emails(d)
    if not propuestos:
        return False, "sin destinatario"
    extra = propuestos - origen
    if extra:
        return False, "destinatario(s) no son el remitente del hilo: %s" % ", ".join(sorted(extra))
    return True, "ok (solo al remitente)"


# ── Unsubscribe (para la lista de bajas a-un-clic; NO se ejecuta) ───────────────
_RE_LISTUNSUB = re.compile(r"<(https?://[^>]+|mailto:[^>]+)>")


def extraer_unsubscribe(header_list_unsubscribe="", body=""):
    """Saca el/los enlaces de baja del header List-Unsubscribe (o del cuerpo). Solo los
    DEVUELVE para una lista a-un-clic; darse de baja lo confirma {{TITULAR}} (gate)."""
    out = []
    for m in _RE_LISTUNSUB.finditer(header_list_unsubscribe or ""):
        out.append(m.group(1))
    if not out and body:
        m = re.search(r"https?://\S*unsubscrib\S*", body, re.I)
        if m:
            out.append(m.group(0))
    return out


# ── Estado + log reversible ─────────────────────────────────────────────────────
def _ensure():
    os.makedirs(CORREO_DIR, mode=0o700, exist_ok=True)


def _write_atomic(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def registrar(accion, thread_id, *, etiqueta=None, antes=None, despues=None, motivo=""):
    """Append-only log de cada acción (para deshacer en bloque). NUNCA registra cuerpos."""
    _ensure()
    ev = {"ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "accion": accion,
          "thread": thread_id, "etiqueta": etiqueta, "antes": antes, "despues": despues,
          "motivo": motivo}
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev


def cargar_procesados():
    try:
        with open(PROCESADOS, encoding="utf-8") as f:
            return set(json.load(f) or [])
    except Exception:
        return set()


def marcar_procesado(thread_ids):
    _ensure()
    s = cargar_procesados() | set(thread_ids)
    _write_atomic(PROCESADOS, sorted(s))
    return len(s)


# ── Anti-duplicados de avisos urgentes (ledger compartido poller ↔ daemon LLM) ───
# Por qué: dos avisadores pueden pingar el MISMO correo — correo_imap.py (determinista,
# cada 120 s) y el daemon LLM correo-urgente (cada 30 min). Este ledger da un check-and-set
# con cooldown por (remitente, asunto), para que SOLO el primero avise y {{TITULAR}} no reciba el
# aviso dos veces (regla anti-spam). Determinista, sin red, sin IA. No guarda cuerpos: la clave
# es un hash de (remitente|asunto), el valor un timestamp. flock serializa el read-modify-write.
_AVISO_TTL_H = 72   # purga entradas más viejas que esto (mantiene el fichero pequeño)


def _aviso_key(remitente_email, asunto):
    return hashlib.sha256(_norm(remitente_email, asunto).encode("utf-8")).hexdigest()[:16]


def _parse_ts(s):
    try:
        return datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


def reclamar_aviso(remitente_email, asunto, horas=18):
    """Check-and-set con cooldown: devuelve True SOLO si nadie avisó de este correo en las
    últimas `horas` (y entonces lo marca como avisado AHORA); False si ya se avisó dentro de la
    ventana → no repetir. flock serializa el acceso entre el poller y el daemon LLM."""
    _ensure()
    now = datetime.now()
    with open(AVISOS + ".lock", "w") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX)
        except Exception:
            pass
        try:
            with open(AVISOS, encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            data = {}
        # Purga lo viejo (no deja crecer el ledger sin fin).
        data = {k: v for k, v in data.items()
                if _parse_ts(v) is None
                or (now - _parse_ts(v)).total_seconds() < _AVISO_TTL_H * 3600}
        key = _aviso_key(remitente_email, asunto)
        prev = _parse_ts(data.get(key))
        if prev is not None and (now - prev).total_seconds() < horas * 3600:
            _write_atomic(AVISOS, data)        # persiste la purga aunque no reclamemos
            return False
        data[key] = now.strftime("%Y-%m-%dT%H:%M:%S")
        _write_atomic(AVISOS, data)
        return True


# ── Anti-duplicados de BORRADORES (¿ya generé respuesta para ESTE hilo?) ─────────
# Distinto del ledger de avisos (que expira por TTL): un borrador ya dejado en Gmail no
# "caduca" — sigue ahí hasta que {{TITULAR}} lo envíe o lo borre. Por eso esto NO purga por
# tiempo, solo comprueba si YA hay un registro "borrador-respuesta" para la MISMA clave
# (remitente+asunto, mismo hash que _aviso_key: mismo patrón, un solo sitio que lo calcula).
# Barato: lee el log append-only (normalmente pequeño) en vez de mantener un fichero aparte.
def borrador_ya_generado(remitente_email, asunto):
    """True si el log YA tiene un 'borrador-respuesta' para (remitente_email, asunto).
    Fail-safe hacia 'no generado' si el log no existe o está corrupto (mejor un borrador
    de más revisable a mano que perder una respuesta real por un log ilegible)."""
    if not os.path.exists(LOG):
        return False
    key = _aviso_key(remitente_email, asunto)
    try:
        with open(LOG, encoding="utf-8") as f:
            for ln in f:
                try:
                    ev = json.loads(ln)
                except Exception:
                    continue
                if ev.get("accion") == "borrador-respuesta" and ev.get("thread") == key:
                    return True
    except Exception:
        return False
    return False


def auditar():
    """Conteos por acción/etiqueta + últimos movimientos (contra la deriva del clasificador)."""
    if not os.path.exists(LOG):
        return {"acciones": 0, "por_etiqueta": {}, "ultimos": []}
    evs = []
    for ln in open(LOG, encoding="utf-8"):
        try:
            evs.append(json.loads(ln))
        except Exception:
            pass
    poret = {}
    for e in evs:
        k = e.get("etiqueta") or e.get("accion")
        poret[k] = poret.get(k, 0) + 1
    return {"acciones": len(evs), "por_etiqueta": poret, "ultimos": evs[-15:]}


def main(argv):
    cmd = argv[0] if argv else "auditar"
    if cmd in ("-h", "--help"):
        print(__doc__)
        return 0
    if cmd == "auditar":
        print(json.dumps(auditar(), ensure_ascii=False, indent=2))
        return 0
    if cmd == "reglas":
        print(json.dumps({"etiquetas_ok": sorted(ETIQUETAS_OK),
                          "vetadas": sorted(ETIQUETAS_VETADAS),
                          "ned_criticos": sorted(NED_CRITICOS)}, ensure_ascii=False, indent=2))
        return 0
    if cmd == "aviso":
        # Anti-dup compartido. Imprime NUEVO (lo reclama → avisa) o YA (otro ya avisó → no repitas).
        # Uso (para el daemon LLM): correo.py aviso [--horas N] "<email>" "<asunto>"
        a = argv[1:]
        horas = 18.0
        if "--horas" in a:
            i = a.index("--horas")
            try:
                horas = float(a[i + 1])
            except Exception:
                horas = 18.0
            a = a[:i] + a[i + 2:]
        if not a:
            print('uso: correo.py aviso [--horas N] "<email>" "<asunto>"', file=sys.stderr)
            return 2
        email, asunto = a[0], " ".join(a[1:])
        print("NUEVO" if reclamar_aviso(email, asunto, horas=horas) else "YA")
        return 0
    print("uso: correo.py [auditar | reglas | aviso \"<email>\" \"<asunto>\"]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
