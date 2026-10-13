#!/usr/bin/env python3
"""tools/pendientes.py — persigue lo que ESPERA RESPUESTA de {{TITULAR}} (hermano de correo.py).

Por qué existe (11-jul-2026): el gestor de correo solo avisa de urgente-*entrante*; nunca
persigue lo *pendiente de la respuesta de {{TITULAR}}* (ej. contestar a {{CONTACTO}} de breastcancer.org).
En 3 semanas `acciones.jsonl` solo tenía 14 `aviso-imap` — la mitad "que ordena" nunca corrió.

Determinista, SIN LLM, fail-closed. Opera sobre `tools/state/correo/buzon.json` (lo escribe
`correo_imap.py`, IMAP RO) — no lee cuerpos, no llama a ningún modelo, 0 tokens, 0 egress.
Reusa las salvaguardas de `correo.py` (es_ned_critico, es_urgente, `_aviso_key` como
generador de thread_id — MISMO hash que el ledger de avisos, así ambos ledgers hablan
el mismo idioma de claves).

Heurística "pendiente de respuesta de {{TITULAR}}" (sobre cada mensaje de `buzon.json`):
  · llegó a su bandeja (INBOX) de un tercero — por construcción, todo lo que hay en buzon.json.
  · sin `\\Answered` en las FLAGS IMAP (Gmail marca el original cuando ella responde DENTRO
    del hilo por Gmail/IMAP) → esa es la señal de "sin reply suyo posterior", sin leer cuerpos.
  · con fecha fiable y antigüedad > `ANTIGUEDAD_MIN_H` (6h) — cortesía: no perseguir algo
    recién llegado.
  · asunto con inyección detectada → SE IGNORA (no se persigue en automático; ya vive en
    "🛡 Revisar-inyección" vía correo.py, terreno de cuarentena, no de este ledger).

Ledger anti-duplicado `tools/state/correo/pendientes.json`, por thread_id (mismo hash que
`correo._aviso_key(remitente_email, asunto)` — MISMA clave que usa el ledger de avisos, para
que ambos ficheros describan el MISMO hilo con el MISMO id):
    {thread_id: {primer_visto, ultimo_nudge_ts, n_nudges, estado, prioridad, remitente, asunto}}

Insistencia (para no ser ruido): NED-crítico se recuerda 1×/día; prioridad media también
1×/día pero AGRUPADA en un solo mensaje; prioridad baja NUNCA dispara nudge individual — solo
aparece listada en el parte de la mañana (HOY.md, ver seguimiento.py). Cierre por:
  · reply detectado (`\\Answered` en una pasada posterior) — automático, en `sincronizar()`.
  · comando `ok <id>` / `ignora <id>` por Telegram (bot_telegram.py) — manual, `cerrar()`.

Modo SOLO-PARTE (11-jul-2026, gate nombrado de {{TITULAR}} — "modo suave" del daemon
com.btp.correo-pendientes): con la env var BTP_PENDIENTES_SOLO_PARTE=1, nudge() se
convierte en un no-op ANTES de tocar avisar()/salida.py — cero Telegram pase lo que
pase, aunque algún día el cron llame a mal a 'ciclo' en vez de 'sincroniza'. Defensa
en profundidad: el daemon solo-parte ya invoca 'sincroniza'/'baseline' (que nunca
llaman a nudge), este flag es el cinturón además del tirante. resumen_hoy() (lo que
lee HOY.md) es SIEMPRE de solo lectura y no depende de este flag.

Uso:
  python3 tools/pendientes.py sincroniza [--dry]   # relee buzon.json, actualiza el ledger
  python3 tools/pendientes.py baseline [--dry]     # arranque en silencio: marca el backlog
                                                    # como `silenciado` (NO avisa) pero lo deja
                                                    # ABIERTO y visible. Ya no cierra nada.
  python3 tools/pendientes.py nudge [--dry]        # avisa lo que toca (respeta cadencia)
  python3 tools/pendientes.py ciclo [--dry]        # sincroniza + nudge (lo que llama el cron)
  python3 tools/pendientes.py estado               # lo abierto, top-8 (para HOY.md)
  python3 tools/pendientes.py lista                # TODOS los abiertos, sin recortar
  python3 tools/pendientes.py reabrir-baseline     # rescata lo que la versión vieja cerró
  python3 tools/pendientes.py cerrar <id> [--motivo ok|ignora]

Sin dependencias (stdlib).
"""
import fcntl
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import correo  # noqa: E402  (salvaguardas duras + _aviso_key + es_ned_critico/es_urgente)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
CORREO_DIR = os.path.join(STATE, "correo")
LEDGER = os.path.join(CORREO_DIR, "pendientes.json")

ANTIGUEDAD_MIN_H = 6.0     # cortesía: no perseguir algo recién llegado
NUDGE_NED_H = 24.0         # NED-crítico: insiste 1×/día
NUDGE_MEDIA_H = 24.0       # media: 1×/día, pero AGRUPADA en un solo mensaje
# baja: nunca nudge individual — PERO SIEMPRE sale en la lista completa del parte (31-jul-26).

_ID_LEN = 10   # prefijo del thread_id que se muestra/acepta como "id corto" (ok/ignora <id>)

# Los ÚNICOS motivos por los que un pendiente puede pasar a cerrado (31-jul-26). Cerrar es
# un acto de {{TITULAR}}: o responde el hilo (\\Answered, lo detecta sincronizar) o lo cierra a
# mano por Telegram. Ninguna heurística, edad ni barrido puede cerrar nada más.
MOTIVOS_CIERRE_OK = ("ok", "ignora", "respondida")

# Remitentes de DOCUMENTACIÓN CLÍNICA / ADMINISTRACIÓN HOSPITALARIA: lo que mandan no suele
# pedir una respuesta, pide una ACCIÓN de {{TITULAR}} (descargar, firmar, recoger, presentarse) y
# suele venir con caducidad. Nunca son "baja". Nació del caso VH-Arxiu (ver módulo, 31-jul-26).
REMITENTES_ACCION = (
    "arxiu@", "archivo@", "arxiu.", "documentacio", "documentacion",
    "atenciousuari", "atencionalpaciente", "atencionpaciente", "admissions@",
    "secretaria@", "programacio", "programacion", "citacion", "citacio",
)

# Señales de CADUCIDAD en el asunto. Limitación honesta: `buzon.json` solo guarda cabeceras
# (uid/remitente/asunto/fecha/flags), NUNCA el cuerpo — así que esto solo caza lo que venga
# escrito EN EL ASUNTO. El caso VH-Arxiu del 10-jul NO lo habría cazado (asunto "Re:
# Documentacion", la caducidad estaba en el cuerpo). Quien cubre ese caso es REMITENTES_ACCION.
PATRONES_CADUCIDAD = (
    "caduca", "caducidad", "expira", "vence", "válido hasta", "valido hasta",
    "último aviso", "ultimo aviso", "fecha límite", "fecha limite", "plazo",
)


def _solo_parte():
    """True si el modo SOLO-PARTE está activo (ver docstring del módulo). Se lee en cada
    llamada (no se cachea) para que un test pueda mutar la env var entre casos."""
    return os.environ.get("BTP_PENDIENTES_SOLO_PARTE") == "1"


def es_accion_requerida(sender="", subject=""):
    """True si el correo pide una ACCIÓN de {{TITULAR}} (descargar, firmar, recoger) más que una
    respuesta: remitente de documentación/administración hospitalaria, o caducidad escrita en
    el asunto. Determinista, sobre cabeceras — no lee cuerpos (no los hay en buzon.json)."""
    s = (sender or "").lower()
    a = (subject or "").lower()
    if any(p in s for p in REMITENTES_ACCION):
        return True
    return any(p in a for p in PATRONES_CADUCIDAD)


def prioridad_de(sender="", subject=""):
    """ned > media > baja. Reusa exactamente las mismas reglas que correo.py (NED_CRITICOS +
    señal dura de cita/plazo) — un mismo remitente es NED-crítico aquí y en el aviso urgente.
    31-jul-26: lo que pide ACCIÓN (documentación clínica, caducidades) nunca cae a "baja"."""
    if correo.es_ned_critico(sender, subject) and not correo.es_ci_dev(sender):
        return "ned"   # un bot de CI no sube a «ned» por el título de un PR (20-sep-26)
    if correo.es_urgente(sender, subject):
        return "media"
    if es_accion_requerida(sender, subject):
        return "media"
    return "baja"


def _thread_id(sender_email, subject):
    """Id del hilo: MISMO hash que correo._aviso_key, sobre el asunto SIN «Re:»/«Fwd:»
    (13-oct-26): «X» y «Re: X» son un solo pendiente, no dos."""
    return correo._aviso_key(sender_email, correo.asunto_base(subject))  # noqa: SLF001


def _tid_en(ledger, sender_email, subject):
    """Clave de ESTE hilo dentro del ledger. Las entradas anteriores al 13-oct se guardaron
    con el hash del asunto tal cual (con su «Re:»): si existe esa y no la nueva, se sigue
    usando la vieja — así «ok <id>» por Telegram no se rompe y no nace un duplicado."""
    nuevo = _thread_id(sender_email, subject)
    if nuevo in ledger:
        return nuevo
    viejo = correo._aviso_key(sender_email, subject)  # noqa: SLF001
    return viejo if viejo in ledger else nuevo


def _parse_fecha_utc(s):
    """Parsea un header Date de correo a datetime UTC *naive* (para comparar sin líos de
    tz-aware/naive). None si no se puede parsear (fail-safe: ese mensaje no se persigue)."""
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except Exception:
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt   # sin tz en el header: se toma tal cual (mejor esto que descartar el mensaje)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _ahora_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


# ── Estado (ledger) ──────────────────────────────────────────────────────────────
def _ensure():
    os.makedirs(CORREO_DIR, mode=0o700, exist_ok=True)


def _cargar_raw():
    try:
        with open(LEDGER, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}   # sin ledger / corrupto → vacío (fail-safe: se reconstruye, no revienta)


def _guardar_raw(ledger):
    _ensure()
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, LEDGER)


def _con_lock(fn):
    """Read-modify-write del ledger bajo flock (mismo patrón que correo.reclamar_aviso):
    serializa el poller (sincroniza) contra el comando manual (cerrar) por Telegram."""
    _ensure()
    with open(LEDGER + ".lock", "w") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX)
        except Exception:
            pass
        ledger = _cargar_raw()
        resultado = fn(ledger)
        _guardar_raw(ledger)
        return resultado


def cargar():
    """Lectura suelta (sin lock) del ledger — para `estado`/lecturas informativas."""
    return _cargar_raw()


# ── El origen del mensaje y la reapertura (13-oct-26) ─────────────────────────────
def _anota_origen(entry, m, sender, fecha):
    """Guarda en la entrada el mensaje ENTRANTE más reciente del hilo (Message-ID, cuenta,
    dirección, fecha): es lo que necesita `revisar_hilos` para preguntarle a Gmail de quién
    es el último mensaje. Solo cabeceras; nunca cuerpo."""
    f_iso = _iso(fecha) if fecha else None
    if f_iso and (entry.get("ultimo_entrante_ts") or "") > f_iso:
        return
    entry["remitente_email"] = sender
    if m.get("message_id"):
        entry["message_id"] = m["message_id"]
    if m.get("cuenta"):
        entry["cuenta"] = m["cuenta"]
    if f_iso:
        entry["ultimo_entrante_ts"] = f_iso
        entry["fecha_rfc"] = m.get("fecha", "")


def _es_posterior(entry, fecha):
    """¿El hilo tiene ya un entrante MÁS NUEVO que este mensaje? (un «Re:» antiguo marcado
    \\Answered no puede cerrar un hilo donde luego llegó otra cosa)."""
    return bool(fecha and (entry.get("ultimo_entrante_ts") or "") > _iso(fecha))


def _reabre(entry, fecha):
    """Un hilo `cerrado` o `esperando` se reabre SOLO con un mensaje posterior al cierre (o
    al último mensaje de ella): el mismo correo viejo que sigue en la ventana no reabre nada."""
    if entry.get("estado") not in ("cerrado", "esperando") or fecha is None:
        return False
    corte = entry.get("cerrado_ts") if entry.get("estado") == "cerrado" else entry.get("esperando_desde")
    corte = _parse_iso(corte)
    return corte is not None and fecha > corte


# ── Núcleo determinista: ¿qué sigue pendiente, qué se acaba de cerrar? ──────────
def sincronizar(mensajes, *, ahora=None, dry=False):
    """Actualiza el ledger a partir del buzón fresco (la lista `mensajes` de buzon.json).
    Puro salvo la escritura del ledger (que se puede desactivar con dry=True, útil para
    pruebas o pulsos de solo-lectura). Devuelve {abiertos, nuevos, cerrados_ahora}."""
    ahora = ahora or _ahora_utc()

    def _mutar(ledger):
        nuevos, cerrados_ahora, reabiertos = [], [], []
        for m in mensajes or []:
            sender = (m.get("remitente_email") or "").strip()
            subject = (m.get("asunto") or "").strip()
            if not sender or not subject:
                continue
            if m.get("inyeccion"):
                continue   # cuarentena la trata correo.py; este ledger no persigue eso
            tid = _tid_en(ledger, sender, subject)
            answered = "\\Answered" in (m.get("flags") or "")
            entry = ledger.get(tid)
            if entry and entry.get("estado") == "fusionado" and entry.get("fusionado_en") in ledger:
                tid = entry["fusionado_en"]   # dos claves, un hilo: manda la entrada dueña
                entry = ledger[tid]
            fecha = _parse_fecha_utc(m.get("fecha", ""))
            if answered:
                if entry and entry.get("estado") == "abierto" and not _es_posterior(entry, fecha):
                    entry["estado"] = "cerrado"
                    entry["motivo_cierre"] = "respondida"
                    entry["cerrado_ts"] = _iso(ahora)
                    cerrados_ahora.append(tid)
                continue
            if fecha is None:
                continue   # fail-safe: sin fecha fiable, no perseguimos (evita falsos positivos)
            antiguedad_h = (ahora - fecha).total_seconds() / 3600.0
            if antiguedad_h < ANTIGUEDAD_MIN_H:
                continue
            if entry is None:
                entry = ledger[tid] = {
                    "primer_visto": _iso(ahora), "ultimo_nudge_ts": None, "n_nudges": 0,
                    "estado": "abierto", "prioridad": prioridad_de(sender, subject),
                    "remitente": m.get("remitente") or sender, "asunto": subject,
                }
                nuevos.append(tid)
            elif entry.get("estado") == "abierto":
                # refresca prioridad (la config de NED_CRITICOS/es_urgente puede haber cambiado)
                entry["prioridad"] = prioridad_de(sender, subject)
            elif _reabre(entry, fecha):
                # Un mensaje NUEVO en un hilo cerrado o en espera lo devuelve a «te toca»: la
                # segunda respuesta de un médico no puede quedarse muda (13-oct-26).
                entry.update(estado="abierto", reabierto_ts=_iso(ahora), n_nudges=0,
                             ultimo_nudge_ts=None, hilo_visto_ts=None, turno="titular",
                             prioridad=prioridad_de(sender, subject))
                for k in ("motivo_cierre", "cerrado_ts", "silenciado", "pospuesto_hasta"):
                    entry.pop(k, None)
                reabiertos.append(tid)
            _anota_origen(entry, m, sender, fecha)
        return {"nuevos": nuevos, "cerrados_ahora": cerrados_ahora, "reabiertos": reabiertos}

    if dry:
        ledger = _cargar_raw()
        res = _mutar(ledger)   # muta una copia en memoria; no se persiste
    else:
        res = _con_lock(_mutar)
        ledger = _cargar_raw()

    abiertos = [dict(id=tid, **e) for tid, e in ledger.items() if e.get("estado") == "abierto"]
    abiertos.sort(key=lambda e: (e["prioridad"] != "ned", e["prioridad"] != "media", e["primer_visto"]))
    return {"abiertos": abiertos, "nuevos": res["nuevos"], "cerrados_ahora": res["cerrados_ahora"],
            "reabiertos": res["reabiertos"]}


def baseline(mensajes, *, ahora=None, dry=False):
    """Arranque en silencio, versión que NO cierra nada (reescrito 31-jul-2026).

    Cómo era y por qué se cambió: la versión del 11-jul corría sincronizar() y acto seguido
    CERRABA cada entrada nueva con motivo "baseline-arranque". El 12-jul eso cerró 60 hilos de
    golpe. Dentro cayó "Re: Documentacion" de VH-Arxiu, que traía el enlace con contraseña
    CADUCABLE para descargar toda la historia clínica de {{TITULAR}}. Nadie volvió a verlo, la
    contraseña caducó y hubo que re-solicitarlo todo. {{TITULAR}}: "un fallo gordo".

    Ahora el backlog se marca `silenciado: True` y SIGUE ABIERTO: no dispara nudge (para no
    inundar el primer día, que era el objetivo legítimo) pero aparece entero en la lista del
    parte, que es donde ella decide. Cerrar sigue siendo acto suyo y solo suyo.
    """
    r = sincronizar(mensajes, ahora=ahora, dry=dry)
    if dry:
        return {"se_silenciarian": len(r["nuevos"]), "dry": True}
    nuevos = set(r["nuevos"])

    def _mutar(ledger):
        n = 0
        for tid in nuevos:
            e = ledger.get(tid)
            if e and e.get("estado") == "abierto" and not e.get("silenciado"):
                e["silenciado"] = True
                e["silenciado_ts"] = _iso(ahora or _ahora_utc())
                n += 1
        return n

    marcados = _con_lock(_mutar)
    abiertos_tras = sum(1 for e in cargar().values() if e.get("estado") == "abierto")
    return {"silenciados": marcados, "abiertos_tras_baseline": abiertos_tras}


def reabrir_baseline():
    """Rescate del destrozo del 12-jul-26: devuelve a `abierto` (silenciado) todo lo que la
    versión vieja cerró con motivo "baseline-arranque". No toca lo cerrado por ella
    (ok/ignora/respondida). Idempotente. Devuelve cuántos rescató."""
    def _mutar(ledger):
        n = 0
        for _tid, e in ledger.items():
            if e.get("estado") == "cerrado" and e.get("motivo_cierre") == "baseline-arranque":
                e["estado"] = "abierto"
                e["silenciado"] = True
                e["reabierto_ts"] = _iso(_ahora_utc())
                e.pop("motivo_cierre", None)
                e.pop("cerrado_ts", None)
                n += 1
        return n

    return _con_lock(_mutar)


# ── La verdad del hilo: ¿a quién le toca? (13-oct-26) ─────────────────────────────
# `sincronizar` solo ve la flag \\Answered de un mensaje que siga en la ventana del buzón. Aquí
# se le pregunta a Gmail, hilo a hilo y SOLO para NED/media, de quién es el último mensaje:
#   · último de un tercero → sigue `abierto` (te toca).
#   · último de {{TITULAR}}     → `esperando` (le toca al otro). No avisa; si pasan
#                            REPINCHAR_DIAS_HABILES sin respuesta, sale UNA vez como «re-pínchale».
#   · pide una ACCIÓN (descargar, firmar) → sigue abierto aunque ella haya contestado: eso
#                            solo lo cierra ella (lección VH-Arxiu, 31-jul).
#   · no se localiza        → no se toca nada. Nunca se da por contestado lo que no se ha visto.
REVISAR_CADA_H = 6.0            # no volver a preguntar por el mismo hilo antes de esto
NO_ENCONTRADO_MAX = 3           # intentos de localizar un hilo antes de dejarlo estar
ANTIGUO_DIAS = 30               # un hilo parado desde hace más no se le echa en cara cada día:
                                # sale contado en una línea (prueba real del 13-oct: «llevas 50
                                # días hábiles sin contestar» sobre cosas resueltas por teléfono)
REVISAR_MAX = 40                # hilos por pasada (cada uno son 2-3 búsquedas IMAP por cuenta)
REPINCHAR_DIAS_HABILES = 5
DESILENCIAR_DIAS = 30           # un NED silenciado del arranque vuelve a avisar solo si el
                                # hilo se movió en este plazo; más viejo, se queda en la lista


def _consulta_real(buzones):
    import correo_imap

    def _c(e):
        return correo_imap.estado_hilo(
            e.get("message_id", ""), remitente_email=e.get("remitente_email") or e.get("remitente", ""),
            asunto=e.get("asunto", ""), fecha=e.get("fecha_rfc", ""), buzones=buzones)
    return _c


def dias_habiles(desde, hasta):
    """Días laborables (lun-vie) enteros entre dos fechas. Un fin de semana no cuenta como
    «llevas dos días sin contestar»."""
    if desde is None or hasta is None or hasta <= desde:
        return 0
    n, d = 0, desde
    while (hasta - d).total_seconds() >= 86400:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def revisar_hilos(*, ahora=None, consulta=None, max_n=REVISAR_MAX, forzar=False, dry=False):
    """Pregunta por el estado real de los hilos NED/media abiertos y lo aplica al ledger.
    La red va FUERA del candado (si no, «ok <id>» por Telegram se quedaría esperando); el
    resultado se aplica después, bajo candado, y solo a entradas que sigan abiertas.
    `consulta(entry) -> dict de correo_imap.estado_hilo` se inyecta en tests."""
    ahora = ahora or _ahora_utc()
    ledger = cargar()
    cand = []
    for tid, e in ledger.items():
        if e.get("estado") != "abierto" or e.get("prioridad") not in ("ned", "media"):
            continue
        visto = _parse_iso(e.get("hilo_visto_ts"))
        if not forzar and visto is not None and (ahora - visto).total_seconds() < REVISAR_CADA_H * 3600:
            continue
        if not forzar and e.get("hilo_no_encontrado", 0) >= NO_ENCONTRADO_MAX:
            continue   # entradas viejas sin Message-ID: tres intentos y se deja de gastar IMAP
        cand.append((tid, e))
    cand.sort(key=lambda t: (t[1].get("hilo_visto_ts") or "", t[1].get("prioridad") != "ned",
                             t[1].get("primer_visto", "")))
    cand = cand[:max_n]
    res = {"revisados": 0, "te_toca": [], "esperando": [], "no_encontrados": [], "fusionados": [],
           "errores": [], "pendientes_de_revisar": 0}
    if not cand:
        return res
    buzones = None
    if consulta is None:
        import correo_imap
        buzones, errs = correo_imap.abrir_cuentas()
        res["errores"].extend(errs)
        consulta = _consulta_real(buzones)
    hallazgos = {}
    try:
        for tid, e in cand:
            try:
                hallazgos[tid] = consulta(e)
            except Exception as ex:   # un hilo que falla no tumba la pasada ni cambia nada
                res["errores"].append("%s: %s" % (tid[:_ID_LEN], type(ex).__name__))
    finally:
        if buzones is not None:
            import correo_imap
            correo_imap.cerrar_cuentas(buzones)

    def _mutar(led):
        por_hilo = {}
        for k, x in led.items():
            if x.get("thrid") and x.get("estado") in ("abierto", "esperando"):
                por_hilo.setdefault((x.get("cuenta_hilo"), x["thrid"]), k)
        for tid, h in hallazgos.items():
            e = led.get(tid)
            if not e or e.get("estado") != "abierto":
                continue   # ella lo cerró mientras preguntábamos: manda lo suyo
            e["hilo_visto_ts"] = _iso(ahora)
            res["revisados"] += 1
            res["errores"].extend(h.get("errores") or [])
            if not h.get("encontrado"):
                e["hilo_no_encontrado"] = e.get("hilo_no_encontrado", 0) + 1
                res["no_encontrados"].append(tid)
                continue
            e.pop("hilo_no_encontrado", None)
            ult = datetime.utcfromtimestamp(h["ultimo_ts"]) if h.get("ultimo_ts") else None
            e.update(thrid=h.get("thrid", ""), cuenta_hilo=h.get("cuenta", ""),
                     turno=h.get("turno"), via=h.get("via", "hilo"),
                     auto=bool(h.get("automatico")),
                     ultimo_ts_hilo=_iso(ult) if ult else None)
            clave = (e["cuenta_hilo"], e["thrid"])
            dueno = por_hilo.setdefault(clave, tid) if e["thrid"] else tid
            if dueno != tid:
                # dos entradas (la de «X» y la vieja de «Re: X») para un mismo hilo de Gmail
                e.update(estado="fusionado", fusionado_en=dueno, fusionado_ts=_iso(ahora))
                res["fusionados"].append(tid)
                continue
            accion = es_accion_requerida(e.get("remitente_email") or e.get("remitente", ""),
                                         e.get("asunto", ""))
            if h.get("turno") == "otro" and not accion:
                e.update(estado="esperando", esperando_desde=_iso(ult or ahora))
                e.pop("silenciado", None)
                res["esperando"].append(tid)
                continue
            if e.get("silenciado") and ult and (ahora - ult).days <= DESILENCIAR_DIAS:
                e.pop("silenciado", None)   # regla de salida del backlog de arranque
            res["te_toca"].append(tid)
        return None

    if dry:
        _mutar(ledger)
    else:
        _con_lock(_mutar)
        ledger = cargar()
    res["pendientes_de_revisar"] = sum(
        1 for e in ledger.values()
        if e.get("estado") == "abierto" and e.get("prioridad") in ("ned", "media")
        and not e.get("hilo_visto_ts"))
    res["errores"] = sorted(set(res["errores"]))
    return res


def esperando_vencidos(*, ahora=None):
    """Hilos donde lo último lo escribió ELLA y el otro lleva REPINCHAR_DIAS_HABILES sin
    contestar. Cada hilo sale UNA vez por mensaje suyo (`repinchado_de`): si ella vuelve a
    escribir y vuelven a callar, saldrá otra vez; si no, no se repite."""
    ahora = ahora or _ahora_utc()
    out = []
    for tid, e in cargar().items():
        if e.get("estado") != "esperando" or e.get("prioridad") not in ("ned", "media"):
            continue
        desde = _parse_iso(e.get("esperando_desde"))
        if desde is None or e.get("repinchado_de") == e.get("esperando_desde"):
            continue
        if (ahora - desde).days > ANTIGUO_DIAS:
            continue   # lo que lleva más de un mes callado ya no es un «re-pínchale» del día
        if dias_habiles(desde, ahora) >= REPINCHAR_DIAS_HABILES:
            out.append((tid, e))
    out.sort(key=lambda t: (t[1].get("prioridad") != "ned", t[1].get("esperando_desde", "")))
    return out


def posponer(identificador, *, modo="manana", ahora=None):
    """«mañana <id>»: ese hilo no vuelve a salir hasta mañana a las 8 (UTC+2 aprox: 06:00 UTC).
    «espero <id>»: ella dice que la pelota está en el otro tejado → `esperando` desde ahora.
    Mismo criterio fail-closed que `cerrar`: id ambiguo o inexistente no toca nada."""
    ahora = ahora or _ahora_utc()
    ident = (identificador or "").strip()
    if modo not in ("manana", "espero") or not ident:
        return False, "uso: mañana <id> | espero <id>"
    out = {}

    def _mutar(led):
        c = [k for k in led if k == ident] or [k for k in led if k.startswith(ident)]
        c = [k for k in c if led[k].get("estado") == "abierto"]
        if len(c) != 1:
            out["err"] = "ese id es ambiguo; dame más caracteres" if len(c) > 1 else \
                "no encuentro ese pendiente abierto (%s)" % ident
            return
        e = led[c[0]]
        if modo == "manana":
            e["pospuesto_hasta"] = _iso((ahora + timedelta(days=1)).replace(hour=6, minute=0, second=0))
        else:
            e.update(estado="esperando", esperando_desde=_iso(ahora), turno="otro", via="dicho")
        out["e"] = e

    _con_lock(_mutar)
    if out.get("err"):
        return False, out["err"]
    e = out["e"]
    return True, ("pospuesto hasta mañana: %s" if modo == "manana" else "esperando a %s") % e.get("remitente", ident)


# ── El mensaje diario: UNO por día hábil, agrupado (13-oct-26) ────────────────────
# Sustituye a la insistencia hilo a hilo. Repite cada día lo que sigue sin contestar, con los
# días hábiles que lleva; lo NED que pasa de CRITICO_DIAS_HABILES sube arriba, dicho sin
# rodeos. Solo entran hilos donde Gmail ha confirmado que el último mensaje NO es suyo (o que
# piden una acción): lo que no se ha podido comprobar no se le echa en cara.
CRITICO_DIAS_HABILES = 3
DIARIO_HORA_LOCAL = 9            # no antes de esta hora (local)
DIARIO_MAX_LINEAS = 8
DIARIO_ESTADO = os.path.join(CORREO_DIR, "pendientes_diario.json")
DIARIO_EN_SECO = os.path.join(CORREO_DIR, "diario_en_seco.txt")


def te_toca(*, ahora=None, antiguos=False):
    """Hilos NED/media confirmados como «te toca», sin los pospuestos, los silenciados ni lo
    que mandó una máquina (respuesta automática, boletín). Por defecto solo los movidos en
    los últimos ANTIGUO_DIAS; con `antiguos=True`, solo los más viejos.
    Devuelve [(tid, entry, dias_habiles)] — NED primero, y dentro, el que más lleva."""
    ahora = ahora or _ahora_utc()
    out = []
    for tid, e in cargar().items():
        if e.get("estado") != "abierto" or e.get("prioridad") not in ("ned", "media"):
            continue
        if e.get("silenciado") or not e.get("turno") or e.get("auto"):
            continue
        accion = es_accion_requerida(e.get("remitente_email") or e.get("remitente", ""), e.get("asunto", ""))
        if e.get("turno") != "titular" and not accion:
            continue
        hasta = _parse_iso(e.get("pospuesto_hasta"))
        if hasta is not None and ahora < hasta:
            continue
        desde = _parse_iso(e.get("ultimo_ts_hilo")) or _parse_iso(e.get("ultimo_entrante_ts")) \
            or _parse_iso(e.get("primer_visto"))
        viejo = desde is not None and (ahora - desde).days > ANTIGUO_DIAS
        if viejo != bool(antiguos):
            continue
        out.append((tid, e, dias_habiles(desde, ahora)))
    out.sort(key=lambda t: (t[1].get("prioridad") != "ned", -t[2]))
    return out


def texto_diario(*, ahora=None):
    """El texto del mensaje del día, o "" si no hay nada que decir (entonces no se manda)."""
    ahora = ahora or _ahora_utc()
    toca, espera = te_toca(ahora=ahora), esperando_vencidos(ahora=ahora)
    if not toca and not espera:
        return ""
    lineas = []
    if toca:
        lineas.append("✉️ Te toca contestar (%d):" % len(toca))
        for tid, e, d in toca[:DIARIO_MAX_LINEAS]:
            critico = e.get("prioridad") == "ned" and d >= CRITICO_DIAS_HABILES
            hecho = bool(e.get("borrador_ts")) or correo.borrador_ya_generado(
                e.get("remitente_email", ""), e.get("asunto", ""))
            lineas.append("%s %s: «%s». %s%s (id %s)" % (
                "🔴" if critico else "·", e.get("remitente", tid), e.get("asunto", ""),
                ("Llevas %d días hábiles sin contestar" % d) if critico else
                ("%d día%s" % (d, "" if d == 1 else "s") if d else "de hoy"),
                ", y el borrador está hecho en Gmail" if hecho else "", tid[:_ID_LEN]))
            if e.get("borrador_extracto"):
                lineas.append("    📝 %s«%s»" % (
                    "(ojo, el correo traía algo raro) " if e.get("borrador_sospechoso") else "",
                    e["borrador_extracto"]))
            elif e.get("borrador_pendiente") and not hecho:
                lineas.append("    sin borrador todavía: %s" % e["borrador_pendiente"])
        if len(toca) > DIARIO_MAX_LINEAS:
            lineas.append("· … y %d más (`pendientes.py lista`)" % (len(toca) - DIARIO_MAX_LINEAS))
    if espera:
        lineas.append("⏳ No te han contestado (%d):" % len(espera))
        for tid, e in espera[:DIARIO_MAX_LINEAS]:
            d = dias_habiles(_parse_iso(e.get("esperando_desde")), ahora)
            lineas.append("· %s: «%s». %d días hábiles desde tu último mensaje (id %s)" % (
                e.get("remitente", tid), e.get("asunto", ""), d, tid[:_ID_LEN]))
    viejos = len(te_toca(ahora=ahora, antiguos=True))
    if viejos:
        lineas.append("· Aparte, %d hilo%s de hace más de un mes sin cerrar (`pendientes.py antiguos`)"
                      % (viejos, "" if viejos == 1 else "s"))
    lineas.append('"ok <id>" hecho · "mañana <id>" lo dejo para mañana · "espero <id>" le toca al otro')
    return "\n".join(lineas)


def diario(*, ahora=None, ahora_local=None, dry=False, avisar=None):
    """Manda el mensaje del día UNA vez por día hábil. Con BTP_PENDIENTES_SOLO_PARTE=1 no
    manda nada: lo deja en `diario_en_seco.txt` para poder leer qué habría dicho (así se
    prueba unos días antes de encenderlo). Solo se sella «enviado hoy» si de verdad llegó."""
    ahora = ahora or _ahora_utc()
    local = ahora_local or datetime.now()
    hoy = local.strftime("%Y-%m-%d")
    if local.weekday() >= 5 or local.hour < DIARIO_HORA_LOCAL:
        return {"enviado": False, "motivo": "fuera de horario (día hábil, desde las %dh)" % DIARIO_HORA_LOCAL}
    try:
        with open(DIARIO_ESTADO, encoding="utf-8") as f:
            estado = json.load(f)
    except Exception:
        estado = {}
    seco = _solo_parte()
    if estado.get("seco" if seco else "enviado") == hoy:
        return {"enviado": False, "motivo": "ya salió hoy"}
    texto = texto_diario(ahora=ahora)
    if not texto:
        return {"enviado": False, "motivo": "nada que decir"}
    if dry:
        return {"enviado": False, "dry": True, "texto": texto}
    _ensure()
    if seco:
        with open(DIARIO_EN_SECO, "a", encoding="utf-8") as f:
            f.write("── %s (en seco: NO enviado) ──\n%s\n\n" % (local.strftime("%Y-%m-%d %H:%M"), texto))
        ok = False
    else:
        if avisar is None:
            import salida  # única boca al exterior (Telegram); respeta HALT/silencio/cupo
            avisar = lambda t: salida.report_to_titular(t, urgente=False, voz="calida", fuente="pendientes-diario")
        res = avisar(texto)
        ok = isinstance(res, dict) and bool(res.get("delivered"))
        if not ok:
            return {"enviado": False, "motivo": "no entregado; se reintenta en la próxima pasada"}
        marcados = {tid for tid, _e in esperando_vencidos(ahora=ahora)}

        def _mutar(led):
            for tid in marcados:   # cada «no te han contestado» sale una vez por mensaje suyo
                if tid in led:
                    led[tid]["repinchado_de"] = led[tid].get("esperando_desde")
        _con_lock(_mutar)
    estado["seco" if seco else "enviado"] = hoy
    tmp = DIARIO_ESTADO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(estado, f)
    os.replace(tmp, DIARIO_ESTADO)
    return {"enviado": ok, "en_seco": seco, "lineas": texto.count("\n") + 1}


def a_avisar(*, ahora=None):
    """¿Qué pendientes tocan nudge AHORA, respetando la cadencia por prioridad? Devuelve
    (ned, media) — listas de (thread_id, entry). 'baja' nunca aparece aquí (a propósito)."""
    ahora = ahora or _ahora_utc()
    ledger = cargar()
    ned, media = [], []
    for tid, e in ledger.items():
        if e.get("estado") != "abierto":
            continue
        if e.get("silenciado") or e.get("auto"):
            continue   # backlog de arranque / correo de máquina: no avisa, SIGUE en la lista
        hasta = _parse_iso(e.get("pospuesto_hasta"))
        if hasta is not None and ahora < hasta:
            continue   # «mañana <id>»: ella lo pospuso, este hilo calla hasta entonces
        prio = e.get("prioridad", "baja")
        if prio == "baja":
            continue
        cadencia_h = NUDGE_NED_H if prio == "ned" else NUDGE_MEDIA_H
        ult = _parse_iso(e.get("ultimo_nudge_ts"))
        if ult is not None and (ahora - ult).total_seconds() < cadencia_h * 3600:
            continue
        (ned if prio == "ned" else media).append((tid, e))
    # más antiguo primero dentro de cada grupo (lo que lleva más tiempo esperando, primero)
    ned.sort(key=lambda t: t[1].get("primer_visto", ""))
    media.sort(key=lambda t: t[1].get("primer_visto", ""))
    return ned, media


def _hace(entry, ahora=None):
    ahora = ahora or _ahora_utc()
    pv = _parse_iso(entry.get("primer_visto"))
    if pv is None:
        return "un tiempo"
    horas = (ahora - pv).total_seconds() / 3600.0
    if horas < 24:
        return "%dh" % round(horas)
    return "%dd" % round(horas / 24)


def _texto_nudge(ned, media, *, ahora=None):
    lineas = ["✉️ ESPERAN TU RESPUESTA:"]
    for tid, e in ned:
        lineas.append("· ⏳ %s: «%s» (%s, id %s)" % (
            e.get("remitente", tid), e.get("asunto", ""), _hace(e, ahora), tid[:_ID_LEN]))
    if media:
        agrupado = "; ".join("%s («%s», id %s)" % (e.get("remitente", tid), e.get("asunto", ""), tid[:_ID_LEN])
                             for tid, e in media)
        lineas.append("· También esperan (menos urgente): " + agrupado)
    lineas.append('Responde "ok <id>" cuando ya esté hecho, o "ignora <id>" si no toca contestar.')
    return "\n".join(lineas)


def nudge(*, dry=False, ahora=None, avisar=None):
    """Envía (o simula si dry=True) el nudge agrupado de lo que toca AHORA, y sella
    ultimo_nudge_ts/n_nudges SOLO de lo que de verdad se entregó (fail-safe: si `salida.py`
    lo bloquea por HALT/silencio nocturno, no se sella — se reintenta en el próximo pulso)."""
    ahora = ahora or _ahora_utc()
    if _solo_parte():
        # Modo suave: ni siquiera se calcula a_avisar()/avisar() — no hay boca a Telegram
        # que tocar. resumen_hoy() (el parte) sigue funcionando normal, es lectura aparte.
        return {"enviado": False, "motivo": "solo-parte (BTP_PENDIENTES_SOLO_PARTE=1)",
                "n_ned": 0, "n_media": 0}
    ned, media = a_avisar(ahora=ahora)
    if not ned and not media:
        return {"enviado": False, "motivo": "nada que avisar", "n_ned": 0, "n_media": 0}
    texto = _texto_nudge(ned, media, ahora=ahora)
    if dry:
        return {"enviado": False, "dry": True, "texto": texto, "n_ned": len(ned), "n_media": len(media)}
    if avisar is None:
        import salida  # única boca al exterior (Telegram); respeta HALT/silencio/cooldown
        avisar = lambda t: salida.report_to_titular(t, urgente=False, voz="calida", fuente="pendientes")
    res = avisar(texto)
    ok = isinstance(res, dict) and res.get("delivered")

    def _mutar(ledger):
        if ok:
            for tid, _e in ned + media:
                if tid in ledger and ledger[tid].get("estado") == "abierto":
                    ledger[tid]["ultimo_nudge_ts"] = _iso(ahora)
                    ledger[tid]["n_nudges"] = ledger[tid].get("n_nudges", 0) + 1
        return None

    _con_lock(_mutar)
    return {"enviado": bool(ok), "texto": texto, "n_ned": len(ned), "n_media": len(media), "raw": res}


def cerrar(identificador, *, motivo="ignora"):
    """Cierra un pendiente por su thread_id completo o por el prefijo corto que se muestra en
    el nudge/HOY.md («ok <id>» / «ignora <id>» desde Telegram). Fail-closed: si el prefijo es
    ambiguo (casa con más de uno) o no existe, NO cierra nada — mejor preguntar de nuevo que
    cerrar el hilo equivocado. Devuelve (ok, mensaje_humano)."""
    ident = (identificador or "").strip()
    if not ident:
        return False, "sin id"
    if motivo not in MOTIVOS_CIERRE_OK:
        # Fail-closed (31-jul-26): cerrar es acto de {{TITULAR}}. Ninguna rutina puede inventarse
        # un motivo nuevo para vaciar el ledger, que es justo lo que hizo "baseline-arranque".
        return False, "motivo de cierre no permitido: %r (solo %s)" % (
            motivo, ", ".join(MOTIVOS_CIERRE_OK))

    resultado = {}

    def _mutar(ledger):
        tid = ident if ident in ledger else None
        if tid is None:
            candidatos = [k for k in ledger if k.startswith(ident)]
            if len(candidatos) == 1:
                tid = candidatos[0]
            elif len(candidatos) > 1:
                resultado["ambiguo"] = True
                return
        if tid is None or tid not in ledger:
            resultado["no_encontrado"] = True
            return
        if ledger[tid].get("estado") != "abierto":
            resultado["ya_cerrado"] = ledger[tid]
            return
        ledger[tid]["estado"] = "cerrado"
        ledger[tid]["motivo_cierre"] = motivo
        ledger[tid]["cerrado_ts"] = _iso(_ahora_utc())
        resultado["cerrado"] = ledger[tid]

    _con_lock(_mutar)
    if resultado.get("ambiguo"):
        return False, "ese id es ambiguo (casa con varios pendientes); dame más caracteres"
    if resultado.get("no_encontrado"):
        return False, "no encuentro ese pendiente (%s)" % ident
    if resultado.get("ya_cerrado"):
        return False, "ese pendiente ya estaba cerrado"
    e = resultado["cerrado"]
    return True, "cerrado: %s («%s»)" % (e.get("remitente", ident), e.get("asunto", ""))


def abiertos_ordenados(*, ahora=None):
    """TODOS los pendientes abiertos, ned > media > baja y dentro de cada grupo el más viejo
    primero. Incluye los `silenciado` (no avisan, pero existen). Solo lectura."""
    ledger = cargar()
    abiertos = [dict(id=tid, **e) for tid, e in ledger.items() if e.get("estado") == "abierto"]
    orden = {"ned": 0, "media": 1, "baja": 2}
    abiertos.sort(key=lambda e: (orden.get(e.get("prioridad"), 3), e.get("primer_visto", "")))
    return abiertos


def _linea(e, ahora=None):
    return "· %s%s: «%s» (%s, id %s)" % (
        "⏳ " if e.get("prioridad") == "ned" else "",
        e.get("remitente", e["id"]), e.get("asunto", ""), _hace(e, ahora), e["id"][:_ID_LEN])


def resumen_hoy(*, ahora=None, limite=8):
    """Lista para HOY.md / el parte de la mañana: pendientes ABIERTOS, más urgentes primero.

    31-jul-26: `limite=None` devuelve la lista COMPLETA (es lo que pidió {{TITULAR}}: verlos todos
    y decidir ella cuáles no se contestan). Si hay límite, el resto NO se traga en silencio —
    se dice cuántos quedan y por dónde verlos. Un recorte callado se lee como "ya está todo".
    """
    ahora = ahora or _ahora_utc()
    abiertos = abiertos_ordenados(ahora=ahora)
    if limite is None or len(abiertos) <= limite:
        return [_linea(e, ahora) for e in abiertos]
    out = [_linea(e, ahora) for e in abiertos[:limite]]
    out.append("· … y %d más sin responder (todos: `pendientes.py lista`)" % (len(abiertos) - limite))
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────────
def _buzon_mensajes():
    import correo_imap
    return correo_imap.cargar_buzon().get("mensajes", [])


def _revisar_hilos_cli(*, dry=False, forzar=False):
    """La pasada de hilos, fail-soft: si no hay clave o Gmail no responde se DICE en la
    salida, pero la sincronización por cabeceras sigue valiendo."""
    try:
        r = revisar_hilos(dry=dry, forzar=forzar)
    except Exception as e:
        return {"ok": False, "motivo": "%s: %s" % (type(e).__name__, e)}
    return {"ok": True, "revisados": r["revisados"], "te_toca": len(r["te_toca"]),
            "esperando": len(r["esperando"]), "fusionados": len(r["fusionados"]),
            "no_encontrados": len(r["no_encontrados"]),
            "pendientes_de_revisar": r["pendientes_de_revisar"], "errores": r["errores"]}


def main(argv):
    cmd = argv[0] if argv else "estado"
    dry = "--dry" in argv[1:]
    if cmd in ("-h", "--help"):
        print(__doc__)
        return 0
    if cmd == "sincroniza":
        r = sincronizar(_buzon_mensajes(), dry=dry)
        out = {"nuevos": r["nuevos"], "cerrados_ahora": r["cerrados_ahora"],
               "reabiertos": r["reabiertos"], "abiertos": len(r["abiertos"])}
        if "--sin-red" not in argv[1:]:
            out["hilos"] = _revisar_hilos_cli(dry=dry)
            try:   # el borrador se prepara ANTES del mensaje del día, para que vaya dentro
                import correo_responder
                out["borradores"] = correo_responder.pasada(dry=dry)
            except Exception as e:
                out["borradores"] = {"ok": False, "motivo": "%s: %s" % (type(e).__name__, e)}
        d = diario(dry=dry)
        d.pop("texto", None)   # el texto lleva nombres: no al log del daemon
        out["diario"] = d
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if cmd == "diario":
        r = diario(dry=True, ahora_local=datetime.now().replace(hour=12)) if dry else diario()
        print(r.get("texto") or json.dumps(r, ensure_ascii=False))
        return 0
    if cmd == "antiguos":
        for tid, e, d in te_toca(antiguos=True):
            print("· %s: «%s» (%d días hábiles, id %s)" % (e.get("remitente", ""), e.get("asunto", ""), d, tid[:_ID_LEN]))
        return 0
    if cmd == "hilos":
        print(json.dumps(_revisar_hilos_cli(dry=dry, forzar="--forzar" in argv[1:]),
                         ensure_ascii=False, indent=2))
        return 0
    if cmd in ("manana", "mañana", "espero"):
        if len(argv) < 2:
            print("uso: pendientes.py mañana <id> | espero <id>", file=sys.stderr)
            return 2
        ok, texto = posponer(argv[1], modo="espero" if cmd == "espero" else "manana")
        print(("✅ " if ok else "⚠️  ") + texto)
        return 0 if ok else 1
    if cmd == "baseline":
        r = baseline(_buzon_mensajes(), dry=dry)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    if cmd == "nudge":
        r = nudge(dry=dry)
        print(json.dumps(r, ensure_ascii=False, default=str, indent=2))
        return 0
    if cmd == "ciclo":
        sincronizar(_buzon_mensajes(), dry=dry)
        r = nudge(dry=dry)
        print(json.dumps(r, ensure_ascii=False, default=str, indent=2))
        return 0
    if cmd == "estado":
        for linea in resumen_hoy():
            print(linea)
        if not cargar():
            print("(sin pendientes)")
        return 0
    if cmd == "lista":
        abiertos = abiertos_ordenados()
        for e in abiertos:
            print(_linea(e) + ("  [silenciado]" if e.get("silenciado") else ""))
        print("── %d sin responder. Cierra con: ok <id> / ignora <id>" % len(abiertos))
        return 0
    if cmd == "reabrir-baseline":
        n = reabrir_baseline()
        print("rescatados %d hilo(s) que la versión vieja cerró con 'baseline-arranque'" % n)
        return 0
    if cmd == "cerrar":
        if len(argv) < 2:
            print('uso: pendientes.py cerrar <id> [--motivo ok|ignora]', file=sys.stderr)
            return 2
        motivo = "ignora"
        a = argv[1:]
        if "--motivo" in a:
            i = a.index("--motivo")
            motivo = a[i + 1] if i + 1 < len(a) else "ignora"
            a = a[:i] + a[i + 2:]
        ok, texto = cerrar(a[0], motivo=motivo)
        print(("✅ " if ok else "⚠️  ") + texto)
        return 0 if ok else 1
    print("uso: pendientes.py [sincroniza [--dry] | baseline [--dry] | nudge [--dry] | "
          "ciclo [--dry] | estado | cerrar <id> [--motivo ok|ignora]]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
