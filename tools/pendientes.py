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
from datetime import datetime, timezone
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
    """MISMO hash que correo._aviso_key: el ledger de avisos y el de pendientes describen
    el mismo hilo con el mismo id (facilita cruzar los dos ficheros si hace falta)."""
    return correo._aviso_key(sender_email, subject)  # noqa: SLF001 (mismo módulo hermano)


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


# ── Núcleo determinista: ¿qué sigue pendiente, qué se acaba de cerrar? ──────────
def sincronizar(mensajes, *, ahora=None, dry=False):
    """Actualiza el ledger a partir del buzón fresco (la lista `mensajes` de buzon.json).
    Puro salvo la escritura del ledger (que se puede desactivar con dry=True, útil para
    pruebas o pulsos de solo-lectura). Devuelve {abiertos, nuevos, cerrados_ahora}."""
    ahora = ahora or _ahora_utc()

    def _mutar(ledger):
        nuevos, cerrados_ahora = [], []
        for m in mensajes or []:
            sender = (m.get("remitente_email") or "").strip()
            subject = (m.get("asunto") or "").strip()
            if not sender or not subject:
                continue
            if m.get("inyeccion"):
                continue   # cuarentena la trata correo.py; este ledger no persigue eso
            tid = _thread_id(sender, subject)
            answered = "\\Answered" in (m.get("flags") or "")
            entry = ledger.get(tid)
            if answered:
                if entry and entry.get("estado") == "abierto":
                    entry["estado"] = "cerrado"
                    entry["motivo_cierre"] = "respondida"
                    entry["cerrado_ts"] = _iso(ahora)
                    cerrados_ahora.append(tid)
                continue
            fecha = _parse_fecha_utc(m.get("fecha", ""))
            if fecha is None:
                continue   # fail-safe: sin fecha fiable, no perseguimos (evita falsos positivos)
            antiguedad_h = (ahora - fecha).total_seconds() / 3600.0
            if antiguedad_h < ANTIGUEDAD_MIN_H:
                continue
            if entry is None:
                ledger[tid] = {
                    "primer_visto": _iso(ahora), "ultimo_nudge_ts": None, "n_nudges": 0,
                    "estado": "abierto", "prioridad": prioridad_de(sender, subject),
                    "remitente": m.get("remitente") or sender, "asunto": subject,
                }
                nuevos.append(tid)
            elif entry.get("estado") == "abierto":
                # refresca prioridad (la config de NED_CRITICOS/es_urgente puede haber cambiado)
                entry["prioridad"] = prioridad_de(sender, subject)
        return {"nuevos": nuevos, "cerrados_ahora": cerrados_ahora}

    if dry:
        ledger = _cargar_raw()
        res = _mutar(ledger)   # muta una copia en memoria; no se persiste
    else:
        res = _con_lock(_mutar)
        ledger = _cargar_raw()

    abiertos = [dict(id=tid, **e) for tid, e in ledger.items() if e.get("estado") == "abierto"]
    abiertos.sort(key=lambda e: (e["prioridad"] != "ned", e["prioridad"] != "media", e["primer_visto"]))
    return {"abiertos": abiertos, "nuevos": res["nuevos"], "cerrados_ahora": res["cerrados_ahora"]}


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


def a_avisar(*, ahora=None):
    """¿Qué pendientes tocan nudge AHORA, respetando la cadencia por prioridad? Devuelve
    (ned, media) — listas de (thread_id, entry). 'baja' nunca aparece aquí (a propósito)."""
    ahora = ahora or _ahora_utc()
    ledger = cargar()
    ned, media = [], []
    for tid, e in ledger.items():
        if e.get("estado") != "abierto":
            continue
        if e.get("silenciado"):
            continue   # backlog de arranque: no avisa, pero SIGUE en la lista del parte
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


def main(argv):
    cmd = argv[0] if argv else "estado"
    dry = "--dry" in argv[1:]
    if cmd in ("-h", "--help"):
        print(__doc__)
        return 0
    if cmd == "sincroniza":
        r = sincronizar(_buzon_mensajes(), dry=dry)
        print(json.dumps({"nuevos": r["nuevos"], "cerrados_ahora": r["cerrados_ahora"],
                          "abiertos": len(r["abiertos"])}, ensure_ascii=False, indent=2))
        return 0
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
