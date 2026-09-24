#!/usr/bin/env python3
"""tools/seguimiento.py — vigía DETERMINISTA de hilos abiertos ("que no se te escape nada").

Lee las fuentes de estado y calcula QUÉ se está cayendo, por SEVERIDAD y por IMPACTO-NED:
  · cumbre.json        — cadena clínica a NED (dueño; aquí NO se duplica, se referencia).
  · seguimiento.json   — hilos operativo/admin (legal, finanzas, infra, voz, prensa…).
  · outbox/pending     — borradores esperando el OK de {{TITULAR}}.
  · queue/failed       — jobs del lazo que se cayeron.
  · HOY.md + heartbeat — frescura de fuentes (dead-man: la ausencia de señal NO es buena noticia).

Reglas duras:
  · La SEVERIDAD sale SOLO de fechas ISO y enums de estado — NUNCA de texto libre. Así una
    orden plantada en prosa ("URGENTE, máxima prioridad") no puede subir la urgencia: cierra
    el vector de inyección. La prosa (digests) se lee fuera, propone, y un humano/gate confirma.
  · Prioridad por IMPACTO-NED: lo que toca el cuello de botella de cumbre.json (hoy: la
    biopsia) pesa más que lo urgente-pero-periférico. La vara es "¿nos acerca a NED?".
  · Privacidad: por Telegram los hilos `privado` (un TEMA entero, p.ej. el caso legal) NO salen —
    solo se cuentan; se ven en local. NOMBRES: ningún nombre de PERSONA (terceros NI colaboradores)
    sale por Telegram — se REDACTA en el render (`_redactar_personas`), no se oculta el ítem, para
    que el recordatorio (incl. la cadena clínica, la misión nº1) se VEA sin nombre. Es backstop más
    robusto que ocultar-por-nombre: caza el nombre en título/acción/bloqueo. El muro/HALT mandan.

Sin dependencias (stdlib). Patrón de cumbre.py.
"""
import contextlib
import functools
import io
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, date

# El estado VIVO y la fuente de verdad viven SOLO en casa base (tools/state/ está gitignored:
# NO viaja a los worktrees). Resolvemos casa base (BTP_REPO o ~/claudecode), nunca el árbol
# relativo al fichero: una sesión en su worktree que cree/mueva tareas (crear_tarea/set_estado)
# debe escribir en la ÚNICA libreta viva que leen El Observatorio / El Tablero, no en una copia
# desechable. Mismo criterio que tools/leer_contacto.py. BTP_STATE_DIR sigue aislando el estado
# en tests; BTP_REPO permite override del raíz.
REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
SEG = os.path.join(STATE, "seguimiento.json")
CUMBRE = os.path.join(STATE, "cumbre.json")
# Espejo de INVESTIGACIÓN (literatura) → Notion: fuente OPT-IN local + interruptor del embargo público.
# El radar de literatura NO alimenta esto (es huella del caso, queda local); ver construir_export("investigacion").
INVESTIGACION = os.path.join(STATE, "investigacion.json")
EMBARGO_FLAG = os.path.join(STATE, "embargo_publico.json")
OUTBOX_PENDING = os.path.join(STATE, "outbox", "pending")
# Entrega reclamada y sin cerrar: entregándose ahora o con resultado INCIERTO (24-sep-26, 3.5).
OUTBOX_SENDING = os.path.join(STATE, "outbox", "sending")
QUEUE_FAILED = os.path.join(STATE, "queue", "failed")
HEARTBEAT_DIR = os.path.join(STATE, "heartbeat")
HOY = os.path.join(REPO, "00_FUENTE-DE-VERDAD", "Gestion", "HOY.md")
# Buffer de coalescencia para avisos NO-urgentes de "tarea nueva" (§3, tools/avisos_flush.py lo
# vacía). Cada línea: {"id","titulo","etiqueta","icono"}. Vive en tools/state → gitignored, VIVO.
AVISOS_DIR = os.path.join(STATE, "avisos")
AVISOS_PENDIENTES = os.path.join(AVISOS_DIR, "pendientes.jsonl")

# Modelo ({{TITULAR}}, 2/7/26): cada vía de entrada AUTÓNOMA (no una sesión interactiva conmigo) captura
# en silencio y, cuando registra una tarea NUEVA, avisa — urgente al momento, el resto agrupado.
# Allowlist FAIL-CLOSED del hook de aviso en add_hilo(): un origen fuera de esta lista (incl.
# "manual"/interactivo, que {{TITULAR}} ya ve en el chat) NO dispara aviso — evita que una tarea creada
# EN SESIÓN conmigo se duplique por Telegram. Añadir un origen nuevo = tocar esta lista a propósito.
ORIGENES_AUTONOMOS_AVISO = frozenset({
    "whatsapp", "correo", "email", "dm", "dm-inbox", "instagram", "linkedin",
    "x-menciones", "x-radar", "x-dms", "x-centinela", "prensa", "cosecha", "vega",
})

# Umbrales (días) — la severidad sale SOLO de aquí + enums, nunca de prosa.
VENCE_PRONTO = 7
ESPERA_LARGA = 10
ESPERA_MEDIA = 5
HOY_STALE_DIAS = 2        # HOY.md (por su fecha DECLARADA) más viejo que esto = dead-man
HEARTBEAT_STALE_H = 26    # un agente diario que no late en >26 h = mudo

SEV_ORDEN = {"roja": 0, "ambar": 1, "amarilla": 2, "info": 3}
SEV_EMOJI = {"roja": "🔴", "ambar": "🟠", "amarilla": "🟡", "info": "🟢"}
SEV_ASC = ["info", "amarilla", "ambar", "roja"]   # de menos a más urgente

# Enums CERRADOS de etiqueta/prioridad. Existían como literales sueltos (crear_tarea los
# saneaba, add_hilo NO: copiaba crudo lo que llegara). Cerrarlos aquí es lo que permite que
# `severidad()` los lea sin romper su invariante: solo mira enums, nunca prosa. Un valor
# desconocido NO revienta (rompería calendar_sync/cosecha_checklists/triage_tareas): cae al
# valor seguro, que es el que menos empuja.
ETIQUETAS = ("NED", "Polaris", "Gestión", "contacto", "marca", "admin", "infra", "")
PRIORIDADES = ("alta", "normal", "baja")
_ETIQUETA_ALIAS = {"gestion": "Gestión", "ned": "NED", "polaris": "Polaris"}
_PRIORIDAD_ALIAS = {"media": "normal"}   # valor vivo en 7 hilos, sin sentido en el orden del kanban


def _norm_etiqueta(v):
    e = str(v or "").strip()
    e = _ETIQUETA_ALIAS.get(e.lower(), e)
    return e if e in ETIQUETAS else ""


def _norm_prioridad(v):
    p = str(v or "").strip().lower()
    p = _PRIORIDAD_ALIAS.get(p, p)
    return p if p in PRIORIDADES else "normal"


# Orígenes cuya palabra vale: los canales por los que habla {{TITULAR}} o el propio sistema, no lo
# derivado de fuera. Vive aquí arriba (y no junto a _resolver_dueno, donde estaba) porque ahora
# también lo usa `severidad()`: es la puerta que impide que un correo se escale a sí mismo.
# `decision-titular` y `peticion-titular` son canal suyo directo; `sesion` NO (es dictado en bruto).
ORIGENES_CONFIABLES = {"manual", "plan-aprobado", "tablero", "chat",
                       "decision-titular", "peticion-titular", "titular"}

# Estados que CIERRAN un eslabón de la cadena clínica. La fuente es cumbre.CERRADOS; aquí solo
# se toma prestada. El import es defensivo a propósito: seguimiento.py corre dentro de 4 daemons
# y no puede caerse porque un sibling no esté en el sys.path del que lo invoque. El fallback es
# una COPIA literal, y `test_seguimiento` asserta que ambas coinciden — si alguien toca una y no
# la otra, lo caza el test, no una fase clínica que se queda colgada en el parte para siempre.
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cumbre import CERRADOS as _CUMBRE_CERRADOS
except Exception:
    _CUMBRE_CERRADOS = ("resuelto", "hecho", "aparcado", "fallido")
MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
         "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}


def _today():
    return date.today()


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _peor(a, b):
    """Devuelve la severidad MÁS urgente (roja=0 es la peor)."""
    return a if SEV_ORDEN[a] <= SEV_ORDEN[b] else b


def _subir(sev):
    i = SEV_ASC.index(sev) if sev in SEV_ASC else 0
    return SEV_ASC[min(i + 1, len(SEV_ASC) - 1)]


def load_seguimiento():
    try:
        with open(SEG, encoding="utf-8") as f:
            d = json.load(f) or {}
    except FileNotFoundError:
        d = {"hilos": []}
    except Exception as e:
        # corrupto: NO asumir vacío (ocultaría hilos) → avisar fuerte.
        return {"hilos": [], "_error": "seguimiento.json ilegible: %r" % e}
    d.setdefault("hilos", [])
    return d


def load_cumbre():
    try:
        with open(CUMBRE, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def load_investigacion():
    """Fuente OPT-IN del espejo de investigación. Ilegible/ausente → [] (fail-closed: el espejo queda
    vacío, NUNCA adivina). Cada ítem: {id,titulo,autores,enlace,relevancia,publico:bool}. Se alimenta
    sobre todo por `investigacion_sync` (volcado del agente desde la base Notion «Biblioteca de papers»,
    filas `Publicar en web ✓` = visto bueno curado); también admite alta manual con `investigacion-add`
    (entra publico:false). El radar de literatura NO la toca (es huella del caso → local)."""
    try:
        with open(INVESTIGACION, encoding="utf-8") as f:
            d = json.load(f) or {}
    except Exception:
        return []
    items = d.get("items")
    return items if isinstance(items, list) else []


# «abandonado» = tarea que se deja caer a propósito (ni hecha ni bloqueada-para-siempre).
# Es una SEÑAL de aprendizaje (¿por qué se abandonó?), no un zombie en «bloqueado».
ESTADOS = ("en_curso", "esperando", "bloqueado", "por_confirmar", "hecho", "abandonado")


# ── Candado de escritura ────────────────────────────────────────────────────────────────
# `_write_atomic` es atómico para la ESCRITURA, no para la secuencia leer→modificar→escribir.
# Eso dejaba una carrera real y silenciosa: tú mueves una tarjeta en el Tablero (el handler
# HTTP del Observatorio llama a set_estado/crear_tarea) mientras el poller de correo registra
# un hilo con add_hilo → los dos leen el mismo JSON y el segundo `os.replace` machaca al
# primero. Sin excepción, sin log, sin reintento: la tarjeta simplemente vuelve a su sitio.
# `tools/_lock.py` ya existía y no se usaba aquí; el único candado del fichero (`.perseguir.lock`)
# lo respeta solo perseguir, y protege contra sí mismo, no contra los demás escritores.
_SEG_LOCK_DEPTH = 0


@contextlib.contextmanager
def _seg_lock(timeout=20.0):
    """Serializa el read-modify-write ENTRE PROCESOS. Reentrante dentro del mismo proceso:
    el candado de _lock.py es un mkdir, así que un segundo acquire se bloquearía contra sí
    mismo (y add_hilo/cerrar_tarea sí se llaman desde dentro del módulo).

    Fail-OPEN ruidoso si no se puede tomar: perder una escritura es malo, pero dejar mudo un
    daemon del lazo es peor, y sin candado es exactamente lo que había hasta hoy.
    """
    global _SEG_LOCK_DEPTH
    if _SEG_LOCK_DEPTH > 0:
        _SEG_LOCK_DEPTH += 1
        try:
            yield
        finally:
            _SEG_LOCK_DEPTH -= 1
        return
    try:
        import _lock
    except Exception:
        yield
        return
    try:
        with _lock.lock("seguimiento", timeout=timeout):
            _SEG_LOCK_DEPTH = 1
            try:
                yield
            finally:
                _SEG_LOCK_DEPTH = 0
    except TimeoutError:
        sys.stderr.write("seguimiento: no pude tomar el candado en %ss — escribo sin él "
                         "(riesgo de pisar otra escritura)\n" % timeout)
        yield


def _serializado(fn):
    """Envuelve un escritor de seguimiento.json con el candado. Una línea por función y sin
    reindentar el cuerpo: menos superficie para meter un bug en un fichero que corre en 4 daemons."""
    @functools.wraps(fn)
    def _w(*a, **kw):
        with _seg_lock():
            return fn(*a, **kw)
    return _w


def _write_atomic(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# Columna del Tablero → estado de seguimiento (write-back: mover/cerrar una tarjeta en El Tablero
# actualiza el hilo de Vega, así board y parte diario son la misma fuente).
_COL_A_ESTADO = {
    "hecho": "hecho", "en_curso": "en_curso", "esperando_ok": "por_confirmar",
    "por_hacer": "esperando", "pausa": "bloqueado",
    "abandonado": "abandonado", "descartado": "abandonado",
}


@_serializado
def set_estado(hid, columna):
    """Write-back desde El Tablero: cambia el estado de un hilo OPERATIVO (seguimiento.json) según
    la columna a la que se le movió. NO toca los hilos clínicos de cumbre (id 'cumbre:…', son fases,
    no tareas sueltas). Cambio LOCAL: no envía/publica/contacta. Devuelve el hilo actualizado."""
    import time as _t
    estado = _COL_A_ESTADO.get(columna)
    if not estado:
        raise ValueError("columna inválida: %s (válidas: %s)" % (columna, ", ".join(_COL_A_ESTADO)))
    if str(hid).startswith("cumbre:"):
        raise ValueError("las fases clínicas no se mueven desde el tablero (id %s)" % hid)
    seg = load_seguimiento()
    if seg.get("_error"):
        raise RuntimeError(seg["_error"])
    for h in seg.get("hilos", []):
        if h.get("id") == hid:
            h["estado"] = estado
            if estado == "hecho":
                h["hecho_el"] = _t.strftime("%Y-%m-%d")   # sella para que cuente como "hecho hoy"
                h["ultimo_aviso"] = None                  # cerrado → deja de perseguirse
                h["aviso_estado"] = None
            else:
                h["hecho_el"] = None                      # reabierto desde el Tablero → limpia el sello
            seg["actualizado"] = _t.strftime("%Y-%m-%d")
            _write_atomic(SEG, seg)
            return h
    raise KeyError("no existe el hilo operativo: %s" % hid)


def _slug(s):
    out = "".join(c if c.isalnum() else "-" for c in (s or "").lower()).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:40] or "hilo"


PLAZO_URGENTE_DIAS = 2   # una tarea nueva con plazo a ≤ este nº de días avisa AL MOMENTO


def _es_urgente_nueva(hilo, cumbre):
    """Clasificador de urgencia del hook «tarea nueva → aviso», DETERMINISTA (solo enums/fechas,
    igual que `severidad()`: nunca la prosa del título/siguiente_accion). Urgente si:
      · etiqueta NED con prioridad alta, o
      · toca el cuello de botella actual (ref_cumbre == cumbre.aqui_estamos), o
      · trae plazo a PLAZO_URGENTE_DIAS días o menos (incl. vencido).
    Lo demás es no-urgente → va al buffer de coalescencia (§3)."""
    # OJO: aquí NO va la puerta de origen que sí lleva `severidad()`. Esta función se ejecuta
    # SOLO para orígenes autónomos (ORIGENES_AUTONOMOS_AVISO), así que exigir un origen de
    # confianza la dejaría en código muerto — y ese es justo el camino por el que a {{TITULAR}} le
    # entra una tarea urgente de verdad («confirmar biopsia» por WhatsApp), que debe pingar al
    # momento y no dormir en el buffer. Lo probé el 25-jul y test_avisos_nueva_tarea lo cazó.
    # La superficie de inyección la cierra el enum: `etiqueta` ya no admite texto libre.
    if (hilo.get("etiqueta") or "") == "NED" and (hilo.get("prioridad") or "") == "alta":
        return True
    if hilo.get("ref_cumbre") and hilo["ref_cumbre"] == (cumbre.get("aqui_estamos") or ""):
        return True
    plazo = _parse_iso(hilo.get("plazo"))
    if plazo and (plazo - _today()).days <= PLAZO_URGENTE_DIAS:
        return True
    return False


def _avisar_tarea_nueva(hilo):
    """Hook del choke-point (§2): una tarea NUEVA de un origen AUTÓNOMO dispara un aviso.
    Urgente → `salida.report_to_titular` inmediato (respeta HALT/silencio-noche/anti-spam, vía el
    choke-point único). No-urgente → append al buffer de coalescencia; lo vacía `avisos_flush.py`
    en UN mensaje agrupado. En ambos casos sella `ultimo_aviso` para no re-avisar (dedup). Fail-soft:
    un error aquí NUNCA debe tirar `add_hilo` (la tarea ya está guardada; el aviso es secundario)."""
    ahora = datetime.now().isoformat(timespec="seconds")
    try:
        cumbre = load_cumbre()
        urgente = _es_urgente_nueva(hilo, cumbre)
        if urgente:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import salida
            icono = hilo.get("icono") or CATEGORIA_ICONO.get((hilo.get("categoria") or "").lower(), "📌")
            texto = "%s Tarea nueva: %s" % (icono, hilo.get("titulo", "?"))
            salida.report_to_titular(texto, urgente=True, voz="sobria")
        else:
            os.makedirs(AVISOS_DIR, exist_ok=True)
            rec = {"id": hilo.get("id"), "titulo": hilo.get("titulo", "?"),
                   "etiqueta": hilo.get("etiqueta", ""), "icono": hilo.get("icono", "")}
            with open(AVISOS_PENDIENTES, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _sellar_ultimo_aviso(hilo.get("id"), ahora)
    except Exception as e:
        sys.stderr.write("seguimiento: aviso, no pude avisar de tarea nueva: %r\n" % (e,))


@_serializado
def _sellar_ultimo_aviso(hid, ahora):
    """Sella `ultimo_aviso` en el hilo YA guardado (dedup: si un aviso posterior — p.ej. un canal
    que también avisa por su cuenta, correo-urgente/dm-inbox — ve el sello, no re-avisa). Read-modify-
    write directo (no pasa por add_hilo, para no re-disparar el hook)."""
    if not hid:
        return
    seg = load_seguimiento()
    if seg.get("_error"):
        return
    for h in seg.get("hilos", []):
        if h.get("id") == hid:
            h["ultimo_aviso"] = ahora
            _write_atomic(SEG, seg)
            return


HECHO_CUANDO_MAX = 300


def _hecho_cuando(v):
    """Criterio verificable de parada («está hecho cuando…»), fijado al ABRIR la tarea y mostrado
    al CERRARLA para comprobarlo (24-sep-2026). Opcional y texto libre: no-str → "", recortado."""
    if not isinstance(v, str):
        return ""
    return v.strip()[:HECHO_CUANDO_MAX]


@_serializado
def add_hilo(obj):
    """Sumidero VALIDADO para entradas nuevas (ingesta de Gmail por el agente, captura verbal).
    Valida enum de estado y fechas ISO; lo derivado de fuera entra como `por_confirmar` y con
    `origen` (dato no confiable, anti-inyección) — la severidad nunca sale de su prosa. Si el id
    ya existe, ACTUALIZA (no duplica). Devuelve el id."""
    if not isinstance(obj, dict) or not obj.get("titulo"):
        raise ValueError("hace falta al menos 'titulo'")
    est = obj.get("estado", "por_confirmar")
    if est not in ESTADOS:
        raise ValueError("estado inválido %r (usa %s)" % (est, "/".join(ESTADOS)))
    for k in ("plazo", "esperando_desde"):
        if obj.get(k) and _parse_iso(obj[k]) is None:
            raise ValueError("%s debe ser ISO YYYY-MM-DD, no %r" % (k, obj[k]))
    # «¿Acerca a NED?» sin declarar (21-sep-2026). NO se rechaza: perder una tarea es peor que
    # tenerla sin etiqueta, y rechazar rompería el Observatorio (donde {{TITULAR}} crea tareas a mano),
    # cascada_clinica y triage_tareas. Pero tampoco se calla: el KPI de etiquetado cayó del 92 %
    # al 0 % entre el 13 y el 21-sep sin que nadie lo viera, porque todo hilo nacía con la cadena
    # vacía. Tampoco se rellena con un genérico por categoría: inflaría el indicador con texto de
    # plantilla, y lo que mide es si ALGUIEN pensó el porqué. Lo completa quien tiene criterio
    # (Vega al triar; `seguimiento.py sin-ned` los lista).
    if not (obj.get("objetivo_ned") or "").strip() and not obj.get("id"):
        try:
            sys.stderr.write("⚠️ seguimiento: hilo nuevo SIN objetivo_ned (origen=%s): «%s». "
                             "Complétalo; `seguimiento.py sin-ned` lista los que faltan.\n"
                             % (obj.get("origen", "manual"), str(obj.get("titulo"))[:70]))
        except Exception:        # noqa: BLE001 — avisar nunca puede tumbar el alta
            pass
    seg = load_seguimiento()
    if seg.get("_error"):
        raise RuntimeError(seg["_error"])
    hid = obj.get("id") or _slug(obj["titulo"])
    if not obj.get("id"):
        # Colisión de slug con título DISTINTO → id nuevo (no machacar el sello de otro hilo).
        for h in seg.get("hilos", []):
            if h.get("id") == hid and (h.get("titulo") or "") != obj["titulo"]:
                import hashlib
                hid = hid[:34] + "-" + hashlib.sha1(obj["titulo"].encode("utf-8")).hexdigest()[:5]
                break
    nuevo = {
        "id": hid, "titulo": obj["titulo"], "categoria": obj.get("categoria", "otros"),
        "estado": est, "quien_espera": obj.get("quien_espera", ""),
        "siguiente_accion": obj.get("siguiente_accion", ""),
        "plazo": obj.get("plazo"), "esperando_desde": obj.get("esperando_desde"),
        "severidad": None, "gate": obj.get("gate"),
        "privado": bool(obj.get("privado", False)),
        "ref_cumbre": obj.get("ref_cumbre"),
        # Enums CERRADOS (ver _norm_*): `severidad()` los lee, así que un valor plantado desde
        # fuera no puede auto-escalar un hilo. Desconocido → valor seguro, nunca excepción.
        "etiqueta": _norm_etiqueta(obj.get("etiqueta")),   # grupo del kanban (NED/Polaris/Gestión/…)
        "prioridad": _norm_prioridad(obj.get("prioridad")),  # alta/normal/baja (orden del kanban)
        "origen": obj.get("origen", "manual"),
        "fuente": obj.get("fuente", ""),
        "hecho_el": obj.get("hecho_el"),          # sello "se hizo el ..." (para el CHECK "hecho hoy")
        "dueno": obj.get("dueno", ""),            # QUIÉN ejecuta (comité/agente/tú) — antes se perdía
        "objetivo_ned": obj.get("objetivo_ned", ""),  # el "¿acerca a NED?" — antes se perdía
        "icono": obj.get("icono", ""),            # emoji por ítem para el parte (opcional; hay fallback)
        "por_que": obj.get("por_que", ""),        # "🔓 por qué importa" en 1 frase (opcional)
        "rama": obj.get("rama", ""),              # rama/worktree de trabajo enlazada (puente ramas↔tareas)
        "ref_reserva": obj.get("ref_reserva"),    # id de reservas.json enlazado (puente reservas↔tareas)
        "hecho_cuando": _hecho_cuando(obj.get("hecho_cuando")),  # criterio de parada; se enseña al cerrar
    }
    hoy_iso = datetime.now().strftime("%Y-%m-%d")
    hilos = seg.get("hilos", [])
    es_tarea_nueva = False    # el hook de aviso (§2) solo dispara si NO había id previo
    for i, h in enumerate(hilos):
        if h.get("id") == hid:
            was_hecho = (h.get("estado") == "hecho")
            merged = {**h, **nuevo}
            # Sello de empuje (Vega): se PRESERVA en un update normal (no re-aflorar lo ya
            # avisado); `nuevo` no lo trae, así que `{**h,**nuevo}` lo conserva tal cual.
            merged.setdefault("ultimo_aviso", h.get("ultimo_aviso"))
            merged.setdefault("aviso_estado", h.get("aviso_estado"))
            merged.setdefault("avisos_fallidos", h.get("avisos_fallidos", 0))
            if est == "hecho":
                merged["hecho_el"] = merged.get("hecho_el") or hoy_iso   # conserva si ya estaba
            else:
                merged["hecho_el"] = None                                # reabierto → limpia el sello
                if was_hecho:                # REAPERTURA real → persíguelo fresco (resetea empuje)
                    merged["ultimo_aviso"] = None
                    merged["aviso_estado"] = None
            if not obj.get("rama"):          # no machacar el enlace de rama si el update no lo trae
                merged["rama"] = h.get("rama", "")
            if "hecho_cuando" not in obj:    # ídem el criterio de hecho: un update parcial no lo borra
                merged["hecho_cuando"] = h.get("hecho_cuando", "")
            hilos[i] = merged
            break
    else:
        nuevo.setdefault("ultimo_aviso", obj.get("ultimo_aviso"))   # sello de empuje (Vega)
        nuevo.setdefault("aviso_estado", obj.get("aviso_estado"))
        nuevo.setdefault("avisos_fallidos", obj.get("avisos_fallidos", 0))
        if est == "hecho" and not nuevo.get("hecho_el"):
            nuevo["hecho_el"] = hoy_iso
        hilos.append(nuevo)
        es_tarea_nueva = True
    seg["hilos"] = hilos
    seg["actualizado"] = datetime.now().strftime("%Y-%m-%d")
    _write_atomic(SEG, seg)
    # Hook «tarea nueva → aviso» (§2, modelo 2/7/26): SOLO en la rama de creación (el `for` no hizo
    # `break`) y SOLO para orígenes AUTÓNOMOS (allowlist fail-closed) — una tarea creada EN SESIÓN
    # conmigo ("manual") ya la ve {{TITULAR}} en el chat, no se duplica por Telegram. Dedup: si el hilo YA
    # trae `ultimo_aviso` (un agente de canal ya avisó, p.ej. correo-urgente/dm-inbox), no se re-avisa.
    if es_tarea_nueva and nuevo.get("origen") in ORIGENES_AUTONOMOS_AVISO and not nuevo.get("ultimo_aviso"):
        _avisar_tarea_nueva(nuevo)
    return hid


def sin_objetivo_ned():
    """Hilos ABIERTOS que no declaran su objetivo_ned. Lo que falta completar a mano o por Vega."""
    seg = load_seguimiento()
    return [h for h in (seg.get("hilos") or [])
            if h.get("estado") not in ("hecho", "abandonado")
            and not (h.get("objetivo_ned") or "").strip()]


@_serializado
def crear_tarea(titulo, etiqueta="NED", vence="", prioridad="normal", origen="manual",
                quien_espera="tú", ref_cumbre=None, categoria="otros", estado="esperando",
                dueno="", objetivo_ned="", icono="", por_que="", rama="", hecho_cuando=""):
    """FUENTE ÚNICA de creación de tareas, para CUALQUIER canal (tablero, Vega, chat, Telegram, correo…).
    Toda tarea es un hilo de seguimiento → una sola lista, idéntica en el kanban y en el parte diario.
    Devuelve el id. Cambio LOCAL: no envía/publica/contacta (lo de fuera entra como dato no confiable)."""
    titulo = (titulo or "").strip()
    if not titulo:
        raise ValueError("la tarea necesita un título")
    return add_hilo({
        "titulo": titulo, "estado": estado,
        "etiqueta": _norm_etiqueta(etiqueta or "NED"),
        "prioridad": _norm_prioridad(prioridad),
        "plazo": (vence or None), "quien_espera": quien_espera,
        "ref_cumbre": ref_cumbre, "categoria": categoria, "origen": origen,
        "dueno": dueno, "objetivo_ned": objetivo_ned, "icono": icono, "por_que": por_que,
        "rama": rama, "hecho_cuando": hecho_cuando,
    })


def upsert_evento(entidad, tipo, fecha_iso, titulo, estado="por_confirmar", **kwargs):
    """Crea-O-actualiza un hilo por IDENTIDAD DE EVENTO (entidad + tipo + fecha_iso).

    Principio: un evento = un hilo. Mencionar el mismo evento dos veces actualiza el hilo
    existente en lugar de crear uno nuevo (dedup-en-escritura). Idempotente: llamarlo N
    veces con los mismos argumentos deja exactamente 1 hilo.

    Parámetros:
        entidad   -- persona/org protagonista del evento, p. ej. "bernardo-cordovez"
                     (sin mayúsculas ni espacios; se limpia internamente).
        tipo      -- naturaleza del evento: "reunion", "llamada", "cita", "tarea", etc.
        fecha_iso -- fecha del evento en formato YYYY-MM-DD (o None si no se conoce).
        titulo    -- texto legible del hilo (puede actualizarse en llamadas posteriores).
        estado    -- estado inicial; si el hilo ya existe y se llama con el mismo estado
                     que ya tiene, no cambia. Default: "por_confirmar" (lo que viene de
                     fuera es dato no confiable hasta que Vega/{{TITULAR}} confirmen).
        **kwargs  -- pasan directamente a add_hilo: etiqueta, categoria, plazo, quien_espera,
                     siguiente_accion, prioridad, ref_cumbre, origen, dueno, objetivo_ned, etc.

    Devuelve el id del hilo (estable).

    Garantias:
        · DETERMINISTA: el id = slug de "tipo-entidad-fecha" (o "tipo-entidad" sin fecha),
          sin LLM, sin red.
        · FAIL-SOFT: si add_hilo lanza (JSON corrupto), la excepcion sube al llamador.
        · NO toca hilos de eventos distintos (identidades distintas = ids distintos).
        · Anti-inyeccion: entidad/tipo/fecha se normalizan; la prosa del titulo NO
          determina el id ni eleva la severidad (eso lo hace solo la fecha/enum).
    """
    ent_clean = _slug((entidad or "").strip())
    tipo_clean = _slug((tipo or "evento").strip())
    if fecha_iso:
        # Validar formato ISO; si no parsea, descartar la fecha del id (fail-soft).
        if _parse_iso(fecha_iso) is not None:
            fecha_part = str(fecha_iso)[:10]
        else:
            fecha_part = None
    else:
        fecha_part = None

    id_base = "-".join(p for p in (tipo_clean, ent_clean, fecha_part) if p)
    # Truncar a 50 chars para mantener ids legibles.
    hilo_id = (id_base[:50]).strip("-")

    obj = {
        "id": hilo_id,
        "titulo": (titulo or "").strip() or id_base,
        "estado": estado,
        "origen": kwargs.pop("origen", "upsert_evento"),
        **kwargs,
    }
    # Propagar fecha del evento como plazo si no se da uno explicito.
    if fecha_part and not obj.get("plazo"):
        obj["plazo"] = fecha_part

    return add_hilo(obj)


@_serializado
def enlazar_rama(hid, rama):
    """Puente ramas↔tareas: engancha (o desengancha con rama="") una rama de trabajo a un hilo YA
    existente, sin tocar el resto de sus campos. Lo llama el flujo cuando una sesión abre un worktree
    para una tarea NED/Gestión. Cambio LOCAL y de SOLO lectura sobre git: aquí no se fusiona ni borra
    nada (eso es singleton + gate de {{TITULAR}}). Devuelve el hilo actualizado."""
    import time as _t
    if str(hid).startswith("cumbre:"):
        raise ValueError("las fases clínicas no llevan rama (id %s)" % hid)
    seg = load_seguimiento()
    if seg.get("_error"):
        raise RuntimeError(seg["_error"])
    for h in seg.get("hilos", []):
        if h.get("id") == hid:
            h["rama"] = (rama or "").strip()
            seg["actualizado"] = _t.strftime("%Y-%m-%d")
            _write_atomic(SEG, seg)
            return h
    raise KeyError("no existe el hilo operativo: %s" % hid)


@_serializado
def enlazar_reserva(hid, eid):
    """Puente reservas↔tareas: engancha (o desengancha con eid="") un encargo de reservas.json a un
    hilo YA existente, sin tocar el resto de sus campos. Lo usa el reconciliador para poder cerrar
    el hilo gemelo con seguridad (1:1). Cambio LOCAL. Devuelve el hilo actualizado."""
    import time as _t
    if str(hid).startswith("cumbre:"):
        raise ValueError("las fases clínicas no llevan reserva (id %s)" % hid)
    seg = load_seguimiento()
    if seg.get("_error"):
        raise RuntimeError(seg["_error"])
    for h in seg.get("hilos", []):
        if h.get("id") == hid:
            h["ref_reserva"] = (eid or "").strip() or None
            seg["actualizado"] = _t.strftime("%Y-%m-%d")
            _write_atomic(SEG, seg)
            return h
    raise KeyError("no existe el hilo operativo: %s" % hid)


def severidad(hilo, cumbre):
    """SOLO fechas ISO + enums. Nunca texto libre."""
    if hilo.get("estado") == "hecho":
        return "info"
    hoy = _today()
    sev = "info"
    plazo = _parse_iso(hilo.get("plazo"))
    if plazo:
        d = (plazo - hoy).days
        if d <= 0:
            sev = "roja"
        elif d <= VENCE_PRONTO:
            sev = "ambar"
        else:
            sev = "amarilla"
    esp = _parse_iso(hilo.get("esperando_desde"))
    if esp:
        d = (hoy - esp).days
        if d >= ESPERA_LARGA:
            sev = _peor(sev, "ambar")
        elif d >= ESPERA_MEDIA:
            sev = _peor(sev, "amarilla")
    if hilo.get("estado") == "bloqueado":
        sev = _peor(sev, "ambar")
    # Impacto-NED (1): un hilo marcado NED+alta no puede quedarse en «info» y desaparecer del
    # parte por no tener fecha — pasaba con 58 hilos, incluido el courier de las muestras a
    # {{CENTRO}}. Es un SUELO, no un escalón: subir un nivel a todos metía 14 en ámbar y
    # disparaba las caídas de 46 a 60 (medido), o sea inundarle el parte.
    # La PUERTA DE ORIGEN es la que sostiene el invariante: un hilo nacido de un correo o de
    # un dictado no puede promoverse a sí mismo poniéndose la etiqueta.
    if (hilo.get("etiqueta") == "NED" and hilo.get("prioridad") == "alta"
            and (hilo.get("origen") or "manual").lower() in ORIGENES_CONFIABLES):
        sev = _peor(sev, "amarilla")
    # Impacto-NED (2): si el hilo toca el cuello de botella actual, sube un nivel.
    if hilo.get("ref_cumbre") and hilo["ref_cumbre"] == (cumbre.get("aqui_estamos") or ""):
        sev = _subir(sev)
    return sev


def _con_dias_esperando(bloqueo, desde):
    """Añade «lleva N días» al bloqueo, SOLO si de verdad sabemos desde cuándo. Sin fecha no
    se inventa una cifra: es el mismo criterio de sello de evidencia que el resto del sistema."""
    d = _parse_iso(desde)
    if not d:
        return bloqueo or ""
    dias = (_today() - d).days
    if dias < 1:
        return bloqueo or ""
    return "%s · lleva %d día%s esperando" % (bloqueo or "", dias, "" if dias == 1 else "s")


def hilos_clinicos(cumbre):
    """La cadena clínica de cumbre.json como hilos del digest (sin duplicar: cumbre manda).
    El 'aqui_estamos' (cuello de botella) pesa lo máximo.

    Cierra sobre los MISMOS estados que cumbre.CERRADOS: antes filtraba solo «hecho», y
    «resuelto» —lo único que escribe el trinquete avanzar()— se colaba, así que cada eslabón
    conquistado se quedaba en el parte para siempre en ámbar.
    """
    out = []
    aqui = cumbre.get("aqui_estamos") or ""
    for s in cumbre.get("salientes", []):
        es_cuello = s.get("id") == aqui
        tiene_bloqueo = bool(s.get("bloqueo"))
        if s.get("estado") in _CUMBRE_CERRADOS:
            continue  # fase cerrada: ya no "se cae" (bomba latente si no se salta)
        if not es_cuello and s.get("estado") == "pendiente" and not tiene_bloqueo:
            continue  # pendiente futuro sin bloqueo: no "se cae hoy"
        if es_cuello and tiene_bloqueo:
            sev = "roja"
        elif tiene_bloqueo:
            sev = "ambar"
        else:
            sev = "amarilla"
        out.append({
            "id": "cumbre:" + s.get("id", ""),
            "titulo": s.get("titulo", ""),
            "categoria": "clinico",
            "estado": s.get("estado"),
            "siguiente_accion": s.get("siguiente_accion", ""),
            "quien_espera": _con_dias_esperando(s.get("bloqueo", ""), s.get("esperando_desde")),
            # La cadena clínica no envejecía: sin fecha, un eslabón bloqueado se lee igual el
            # día 1 que el día 17. Se propaga para que el parte pueda decir cuánto llevas.
            "esperando_desde": s.get("esperando_desde"),
            "_sev": sev,
            "privado": False,
            "es_cuello": es_cuello,
            "fuente": s.get("fuente", ""),
        })
    for t in cumbre.get("transversal", []):
        if t.get("estado") in ("riesgo", "bloqueado"):
            out.append({
                "id": "cumbre:" + t.get("id", ""),
                "titulo": t.get("titulo", ""),
                "categoria": "clinico",
                "estado": t.get("estado"),
                "siguiente_accion": t.get("siguiente_accion", ""),
                "quien_espera": t.get("bloqueo", ""),
                "_sev": "ambar",
                "privado": False,
                "es_cuello": False,
                "fuente": t.get("fuente", ""),
            })
    return out


def _fecha_declarada_hoy():
    """Fecha que el propio parte HOY.md declara (no su mtime: un job puede tocar el fichero
    sin regenerar el cuerpo → mtime 'fresco' engañoso). Falla SEGURO: si no la encuentro,
    devuelvo None y el llamador lo trata como NO fresco. (Esto es el dead-man, no severidad.)"""
    try:
        with open(HOY, encoding="utf-8") as f:
            cab = f.read(600).lower()
    except Exception:
        return None
    # Acepta los formatos que REALMENTE se escriben: "23-jun" / "23-jun-2026" (legado),
    # "mar 23 jun" (parte determinista) y "martes 23 junio" (daemon/orquestador). El separador
    # es guion O espacio; el mes vale abreviado o entero (se mapea por sus 3 primeras letras).
    # finditer + validación = se queda con la PRIMERA coincidencia que es un mes de verdad.
    for m in re.finditer(r"(\d{1,2})[-\s]+([a-zñ]{3,})(?:[-\s]+(\d{4}))?", cab):
        dd, palabra, yyyy = m.group(1), m.group(2), m.group(3)
        mes = MESES.get(palabra[:3])
        if not mes:
            continue
        try:
            return date(int(yyyy) if yyyy else _today().year, mes, int(dd))
        except Exception:
            continue
    return None


# Estados que NO son una caída: un agente que se aplazó a sí mismo (sin saldo de PAGO, sin
# presupuesto, o por límite/crédito agotado tras la cadena de modelos) y acaba de latir está SANO —
# difirió a propósito, no se rompió. DEBE incluir TODOS los estados de aplazo que escribe
# run_agent.sh (`credito_agotado`, `aplazado_limite`, `aplazado_tope_local`); un contrato en
# test_seguimiento lo verifica, porque el bug real (23/6) fue justo ese: un aplazo por crédito que
# el vigía leía como "fallo" y gritaba en falso. Un latido recién escrito jamás es una caída.
# `aplazado_sin_saldo` = nombre VIEJO y engañoso del aplazo por TOPE LOCAL (el prepago tenía saldo);
# renombrado a `aplazado_tope_local` (29/6). Se mantiene como ALIAS aquí por compat: un heartbeat
# pegajoso escrito por un run_agent.sh pre-deploy sigue siendo benigno hasta que se sobreescribe.
_HB_BENIGNOS = ("sin_presupuesto", "aplazado_tope_local", "aplazado_sin_saldo", "aplazado_limite",
                "credito_agotado", "respaldo_gratis", "estado_nuevo_desconocido")
# Marcador(es) de ERROR genuino. run_agent.sh escribe "fallo" cuando algo petó de verdad (NO por
# saldo/límite, que son aplazos benignos de arriba) y "fallo_max_turns" cuando el run agotó su
# presupuesto de turnos (estructural: en un daemon gateado ya se selló el gate para cortar la
# sangría, pero SIGUE siendo un fallo visible que el vigía debe gritar). El vigía grita por estos.
_HB_FALLOS = ("fallo", "fallo_max_turns")

# Daemons que CORREN EN HORARIO: su silencio prolongado SÍ es señal (dead-man). Un agente
# ON-DEMAND (comite-medico, orquestador, git…) solo late cuando se le invoca, así que tarde o
# temprano envejece — marcarlo "parado" por eso es un FALSO POSITIVO que inunda el digest. Por eso
# el "parado" (latido viejo) se limita a esta lista; el "fallo" FRESCO se reporta para cualquiera
# (un fallo recién escrito siempre importa, lo lance quien lo lance). Espejo de VEGA_DAEMONS de
# healthcheck.py; al añadir un daemon NUEVO con horario propio, anótalo aquí.
#
# El valor = HORAS de silencio a partir de las cuales se da por parado. None = el default diario
# (HEARTBEAT_STALE_H). No todos corren a diario: auto-mejora pasó a lun/mié/vie/dom el 17-jul-26
# (commit 2ff543b) y con el umbral diario gritaba "parado" en falso cada martes, jueves y sábado
# (visto el 25-jul: heartbeat ok de 35 h = hueco NORMAL entre viernes y domingo). El umbral tiene
# que salir de la CADENCIA REAL del plist, no de asumir "diaria". Si cambias su
# StartCalendarInterval, cambia también estas horas.
_DAEMONS_PROGRAMADOS = {
    "asistente":      None,   # barrido diario 7:55
    "auto-mejora":    56,     # lun/mié/vie/dom ~5:08 → hueco máximo real 48 h + margen de pasada
    "calendar-sync":  None,   # diaria 8:05
    "centinela-ned":  None,   # cada ~2-3 min
    "dm-inbox":       None,   # diaria
    "prensa":         None,   # diaria
    "correo-urgente": None,   # cada 30 min (heartbeat propio)
    "correo-triaje":  None,   # 8:20 / 14:00 (heartbeat propio)
    "correo-imap":    None,   # poller IMAP cada 120s (heartbeat propio, escrito por correo_imap._heartbeat)
}


def _max_silencio_h(nombre):
    """Horas de silencio toleradas a este heartbeat antes de darlo por parado. Los que no declaran
    una cadencia propia usan el default diario. Fail-soft: nombre desconocido → default."""
    try:
        h = _DAEMONS_PROGRAMADOS.get(nombre)
    except Exception:
        h = None
    return HEARTBEAT_STALE_H if h is None else h

# SUPERSEDE (3/7/26): algunos heartbeats comparten BTP_AGENT con otro (p.ej. correo-triaje y
# correo-urgente corren bajo BTP_AGENT=asistente vía run_agent.sh, ver tools/launchd/com.btp.correo*.
# plist) — su heartbeat propio (BTP_HEARTBEAT_NAME) puede quedarse CLAVADO en 'fallo' de una pasada
# vieja aunque una pasada POSTERIOR de ese mismo BTP_AGENT ya saliera 'ok' en observabilidad (bug
# real 3/7: 'correo-triaje' fallido a las 06:26, recuperado a las 09:11, pero el heartbeat seguía
# gritando la vieja). Mapea heartbeat → agente que observabilidad.registrar() usa de verdad
# (BTP_AGENT). Los que faltan del mapa usan su propio nombre (agente == heartbeat, caso normal).
_OBS_AGENTE_DE = {
    "correo-triaje": "asistente",
    "correo-urgente": "asistente",
}


def _heartbeats_problema():
    """Agentes con un problema REAL: falló su última ejecución, o (si es un daemon CON HORARIO)
    lleva tanto sin latir que damos por hecho que está parado. Devuelve (nombre, clase, edad_h)
    donde clase es 'fallo' (la última vez petó) o 'parado' (no da señales). Lo benigno y fresco,
    y el silencio de un agente on-demand, se ignoran. SUPERSEDE: un 'fallo' se tapa si una pasada
    POSTERIOR del mismo BTP_AGENT ya salió 'ok' en observabilidad (ver _OBS_AGENTE_DE) — no
    enmascara un fallo que se repita (si no hay ok más nuevo, se reporta igual)."""
    out = []
    try:
        files = [f for f in os.listdir(HEARTBEAT_DIR) if f.endswith(".json")]
    except Exception:
        return out
    for fn in files:
        p = os.path.join(HEARTBEAT_DIR, fn)
        try:
            mtime = os.path.getmtime(p)
            edad_h = (datetime.now().timestamp() - mtime) / 3600.0
            est = json.load(open(p, encoding="utf-8")).get("estado", "?")
        except Exception:
            continue
        nombre = fn[:-5]
        # Ventana de silencio de ESTE daemon (su cadencia real), no un 26 h para todos.
        stale_h = _max_silencio_h(nombre)
        # Un FALLO genuino y FRESCO se reporta para CUALQUIER agente (acaba de petar de verdad) —
        # SALVO que una pasada más nueva del mismo BTP_AGENT ya haya salido OK (supersede).
        if est in _HB_FALLOS and edad_h <= stale_h:
            if _tapado_por_ok_posterior(nombre, mtime):
                continue
            out.append((nombre, "fallo", round(edad_h, 1)))
        # Un latido VIEJO solo es "parado" para un daemon CON HORARIO (el silencio de un on-demand
        # es esperable, no una caída). Esto incluye el caso de un daemon que petó y ya no vuelve.
        elif edad_h > stale_h and nombre in _DAEMONS_PROGRAMADOS:
            out.append((nombre, "parado", round(edad_h, 1)))
        # estados benignos frescos, o silencio de un on-demand → silencio: no es una caída
    return out


def _tapado_por_ok_posterior(nombre_heartbeat, mtime_heartbeat):
    """¿Hay una pasada OK en observabilidad, del BTP_AGENT real detrás de este heartbeat,
    estrictamente posterior al mtime del heartbeat malo? Fail-soft: cualquier excepción → False
    (conservador — sin prueba de recuperación, no se tapa la alerta)."""
    try:
        import datetime as _dt
        agente_obs = _OBS_AGENTE_DE.get(nombre_heartbeat, nombre_heartbeat)
        ts_iso = _dt.datetime.fromtimestamp(mtime_heartbeat).strftime("%Y-%m-%dT%H:%M:%S")
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import observabilidad as _obs
        return bool(_obs.hubo_ok_desde(agente_obs, ts_iso))
    except Exception:
        return False


DIAS_FALLO_VIVO = 7


def _fallos_por_edad(nombres, dias=DIAS_FALLO_VIVO, ahora=None, base=None):
    """(vivos, cementerio) por fecha de caída. Un job que cayó por una causa ya arreglada y que
    nadie barrió no es trabajo pendiente: es historia. Contarlos juntos convierte el aviso en
    ruido —el 22-sep el Tablero decía «44 caídos» y solo 2 eran de esta semana; 39 venían de dos
    clases muertas desde agosto y el 12-sep— y un aviso que siempre dice lo mismo se deja de leer.
    """
    import time as _t
    ahora = ahora or _t.time()
    base = QUEUE_FAILED if base is None else base
    vivos, viejos = [], []
    for n in nombres:
        ruta = n if os.path.isabs(n) else os.path.join(base, n)
        try:
            edad_d = (ahora - os.path.getmtime(ruta)) / 86400.0
        except OSError:
            edad_d = 0.0          # si no se puede leer, cuenta como VIVO: no se esconde nada
        (vivos if edad_d <= dias else viejos).append(n)
    return vivos, viejos


def _listdir_json(d):
    try:
        return [f for f in os.listdir(d) if f.endswith(".json")]
    except Exception:
        return []


WT_COLGADO_DIAS = 1   # un worktree con commits sin fusionar y sin sesión > este nº de días = "colgado"


def worktrees_colgados():
    """Cabos sueltos de trabajo: worktrees (ramas) con commits ÚTILES SIN fusionar y SIN sesión de
    Claude viva — trabajo que se quedó en el aire al cerrar un hilo sin fusionarlo. Reusa la detección
    determinista de `ramas.huerfanas()` (no la reimplementa). NO auto-fusiona: solo MARCA, para que
    Vega lo saque en su aviso; fusionar es el cierre con gate (`cerrar_sesion.py` / comité git).
    Devuelve [{rama, ahead, edad_dias, sin_commitear}]. Lista vacía si ramas.py no está o no hay nada."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import ramas  # noqa: E402 — solo-lectura de git (worktrees/huérfanas)
        return ramas.huerfanas()
    except Exception:
        return []


def frescura(cumbre):
    """Avisos del dead-man: nunca reportar la ausencia de señal como buena noticia.

    EXCEPCION: el HALT (20-sep-26). Durante una pausa TOTAL los agentes no corren, asi que sus
    latidos envejecen y HOY.md no se regenera. Eso no es un dead-man: es el kill-switch haciendo
    su trabajo, y avisar de ello convierte cada pausa en una tanda de avisos falsos. Medido en el
    libro de deuda: `frescura_hoy_desactualizado` 727 detecciones y
    `frescura_agente_parado:correo-triaje` 679, las dos remitidas TRES veces por sesiones
    distintas -cada una verificando que "ya no pasa"- sin que nadie buscara por que volvia.
    Volvia en cada HALT.

    Se suprime SOLO lo que depende de que algo este vivo. El aviso de ramas sin fusionar NO se
    toca: es estado de git, y un cabo suelto sigue colgando este el sistema parado o no.
    """
    try:
        import salida as _sal
        _en_pausa = _sal.halted()
    except Exception:
        _en_pausa = False   # fail-soft: ante la duda, el dead-man habla
    avisos = []
    fd = _fecha_declarada_hoy()
    if not _en_pausa:
        if fd is None:
            avisos.append("⚠️ No pude leer la fecha del parte HOY.md → trátalo como NO fresco.")
        else:
            d = (_today() - fd).days
            if d >= HOY_STALE_DIAS:
                avisos.append("⚠️ Tu parte HOY.md declara %s (%d días) → puede estar desactualizado." % (fd.isoformat(), d))
        for nombre, clase, _edad in _heartbeats_problema():
            if clase == "fallo":
                avisos.append("⚠️ Falló la última ejecución de '%s' → conviene revisar qué pasó." % nombre)
            else:  # parado
                avisos.append("⚠️ '%s' lleva un buen rato y no da señales → puede estar parado." % nombre)
    # Cabos sueltos de trabajo: ramas (worktrees) con commits ÚTILES SIN fusionar y sin sesión viva.
    # Trabajo que se quedó en el aire al cerrar un hilo sin fusionarlo. Vega lo saca para que {{TITULAR}}
    # pueda cerrar y no se pierda nada; el cierre con gate (`cerrar_sesion.py`) lo recoge.
    colgados = [w for w in worktrees_colgados() if (w.get("edad_dias", 0) < 0 or w["edad_dias"] >= WT_COLGADO_DIAS)]
    if colgados:
        tot = sum(w.get("ahead", 0) for w in colgados)
        avisos.append("🌿 %d rama(s) con trabajo SIN fusionar (%d commit[s]) y sin sesión → cabos sueltos: %s. "
                      "Ciérralas con `python3 tools/cerrar_sesion.py --apply` desde su worktree."
                      % (len(colgados), tot, ", ".join(w["rama"] for w in colgados)))
    return avisos


def recopilar():
    cumbre = load_cumbre()
    seg = load_seguimiento()
    items = []
    for h in seg.get("hilos", []):
        h2 = dict(h)
        h2["_sev"] = severidad(h, cumbre)
        h2.setdefault("es_cuello", False)
        items.append(h2)
    items += hilos_clinicos(cumbre)
    pend = _listdir_json(OUTBOX_PENDING)
    en_curso = _listdir_json(OUTBOX_SENDING)
    fail = _listdir_json(QUEUE_FAILED)
    avisos = frescura(cumbre)
    if seg.get("_error"):
        avisos.insert(0, "⚠️ " + seg["_error"])
    return {"cumbre": cumbre, "items": items, "pendientes_ok": pend, "entrega_incierta": en_curso,
            "fallos": fail, "avisos": avisos}


def _prueba_de_vida(data):
    fuentes = []
    fuentes.append("cumbre" + ("✓" if data["cumbre"] else "✗"))
    fuentes.append("seguimiento" + ("✓" if os.path.exists(SEG) else "✗"))
    fuentes.append("HOY" + ("✓" if _fecha_declarada_hoy() is not None else "✗"))
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    return "última revisión: %s · fuentes: %s · avisos: %d" % (ts, " ".join(fuentes), len(data["avisos"]))


def construir_digest(canal="local"):
    data = recopilar()
    items = data["items"]
    # Privacidad: por Telegram, los hilos `privado` (un TEMA entero) NO salen; se cuentan. Los
    # NOMBRES de persona no ocultan el ítem: se redactan en el render final (`_redactar_personas`),
    # así el recordatorio se ve sin nombre — el muro: ningún nombre de tercero por Telegram.
    privados_ocultos = 0
    visibles = []
    for i in items:
        if canal == "telegram" and _oculto_en_telegram(i):
            privados_ocultos += 1
            continue
        visibles.append(i)
    visibles.sort(key=lambda i: (SEV_ORDEN.get(i.get("_sev", "info"), 3), not i.get("es_cuello")))

    lineas = ["🗂️ SEGUIMIENTO — lo que no se te puede escapar"]
    activos = [i for i in visibles if i.get("_sev") != "info"]
    if not activos:
        lineas.append("✅ 0 vencimientos a la vista.")
    for sev in ("roja", "ambar", "amarilla"):
        grupo = [i for i in visibles if i.get("_sev") == sev]
        if not grupo:
            continue
        for i in grupo:
            cuello = " ⭐NED" if i.get("es_cuello") else ""
            quien = (" · espera: " + i["quien_espera"]) if i.get("quien_espera") else ""
            plazo = (" · plazo " + i["plazo"]) if i.get("plazo") else ""
            acc = ("\n   → " + i["siguiente_accion"]) if i.get("siguiente_accion") else ""
            lineas.append("%s %s%s%s%s%s" % (SEV_EMOJI[sev], i.get("titulo", "?"), cuello, plazo, quien, acc))

    if privados_ocultos:
        lineas.append("🔒 %d hilo(s) privado(s) (terceros) — no salen por aquí; míralos en local." % privados_ocultos)
    if data["pendientes_ok"]:
        lineas.append("✉️ %d borrador(es) esperando tu OK (outbox/pending)." % len(data["pendientes_ok"]))
    if data.get("entrega_incierta"):
        lineas.append("⚠️ %d envío(s) con resultado INCIERTO (outbox/sending): puede que te llegaran. "
                      "Dime «reconciliar <nombre> entregado» o «reintentar»." % len(data["entrega_incierta"]))
    if data["fallos"]:
        vivos, viejos = _fallos_por_edad(data["fallos"], base=QUEUE_FAILED)
        if vivos:
            lineas.append("🛠️ %d job(s) del lazo caídos esta semana (queue/failed)%s."
                          % (len(vivos),
                             " · y %d más, viejos, ya sin clase viva" % len(viejos) if viejos else ""))
        else:
            lineas.append("🛠️ 0 caídos esta semana · quedan %d viejos en queue/failed, para archivar."
                          % len(viejos))
    for a in data["avisos"]:
        lineas.append(a)
    lineas.append("— " + _prueba_de_vida(data))
    lineas.append("No cubre aún: WhatsApp, lo verbal no apuntado, y lo que esté solo en tu cabeza.")
    salida = "\n".join(lineas)
    # Privacidad-en-render: por Telegram, ningún nombre de persona (incluido el de la cadena
    # clínica, que SÍ se ve pero con el nombre redactado). En local va íntegro ({{TITULAR}} sí los ve).
    return _redactar_personas(salida) if canal == "telegram" else salida


# ── PARTE HOY (formato rico de 4 bloques) — determinista, gratis, en la voz de {{TITULAR}} ──
FRANJA_EMOJI = {"mañana": "🌅", "mediodía": "🌤️", "tarde": "🌆", "noche": "🌙"}
# Icono por ítem cuando el hilo no trae `icono` propio (fallback determinista por categoría).
CATEGORIA_ICONO = {"clinico": "🩻", "clínico": "🩻", "legal": "🛡️", "finanzas": "💰",
                   "voz": "🎙️", "prensa": "📢", "seguridad": "🔐", "infra": "🛠️",
                   "redes": "📱", "personal": "🏠", "logistica": "📦", "otros": "📌"}
_NUMS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
_MESES = ["", "ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
_DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def _icono(h):
    return h.get("icono") or CATEGORIA_ICONO.get((h.get("categoria") or "").lower(), "📌")


def _por_que(h):
    """El '🔓 por qué importa', corto. Campo propio o, si falta, derivado (nunca inventado)."""
    if h.get("por_que"):
        return h["por_que"]
    if h.get("es_cuello"):
        return "cuello hacia NED"
    if h.get("ref_cumbre"):
        return "toca %s" % h["ref_cumbre"]
    return ""


def _accion_corta(s, n=90):
    """Primera cláusula accionable; lo largo no va a Telegram."""
    s = (s or "").strip()
    for sep in (". ", ";", "\n"):
        if sep in s:
            s = s.split(sep, 1)[0].strip()
            break
    return (s[:n].rstrip() + "…") if len(s) > n else s


def _manos(h):
    """Determinista: ¿de quién es el siguiente paso? 'tu' | 'agente' | 'tercero'. Nunca de prosa libre."""
    if h.get("gate"):                                   # gate = acto humano de {{TITULAR}} (firma/decisión/envío)
        return "tu"
    q = (h.get("quien_espera") or "").strip().lower()
    if q.startswith(("tú", "tu", "titular", "ella")):
        return "tu"
    d = (h.get("dueno") or "").strip().lower()
    if d and d not in ("tú", "tu", "titular", "ella"):
        return "agente"
    if q:
        return "tercero"
    return "tu"


def _es_borrador_listo(h):
    g = (h.get("gate") or "").lower()
    t = ((h.get("siguiente_accion") or "") + " " + (h.get("titulo") or "")).lower()
    return g in ("enviar", "revisar", "enviar_ok", "revisar_ok") or \
        any(k in t for k in ("borrador", "listo", "a un clic", "redactad"))


def construir_hoy(franja="mediodía", canal="telegram"):
    """PARTE de HOY en formato rico de 4 bloques (🔴 TÚ AHORA / 🤖 YO ME OCUPO / ✍️ A UN CLIC /
    😴 NO APRIETA) + ✅ HECHO HOY + cierre de noche. DETERMINISTA (sin IA → gratis, nunca bloqueado
    por saldo). Routeo a bloque SOLO por estado/severidad/gate/quien_espera (nunca por prosa →
    anti-inyección intacta). Telegram oculta los hilos `privado` (tema entero) y REDACTA todo nombre
    de persona del texto final (terceros/colaboradores → [contacto]); la cadena clínica de cumbre se
    ve siempre, con el nombre redactado. En local va íntegro. NO es el 'chorizo'."""
    data = recopilar()
    items = data["items"]
    if canal == "telegram":
        items = [i for i in items if not _oculto_en_telegram(i)]
    hoy = _today()
    hechos_hoy = [i for i in items if i.get("estado") == "hecho" and _parse_iso(i.get("hecho_el")) == hoy]
    pend = [i for i in items if i.get("estado") != "hecho"]
    # ✉️ ESPERAN TU RESPUESTA (11-jul-2026): ledger APARTE de tools/pendientes.py (correo de
    # terceros sin contestar, no el registro de hilos de arriba) — import perezoso + fail-soft
    # TOTAL (mismo patrón que codigo_rojo en cost_guard.py): si pendientes.py no está disponible
    # o revienta por lo que sea, el parte de HOY sigue construyéndose igual, solo sin esta sección.
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import pendientes as _pend
        pend_lineas = _pend.resumen_hoy()
    except Exception:
        pend_lineas = []

    tu, yo, clic, luego = [], [], [], []
    for h in pend:
        sev, manos = h.get("_sev"), _manos(h)
        if h.get("es_cuello"):                # el cuello hacia NED SIEMPRE es titular → 🔴 nº1
            tu.append(h)
            continue
        if manos in ("agente", "tercero") and sev != "info":
            yo.append(h)
        elif manos == "tu" and _es_borrador_listo(h) and sev != "roja":
            clic.append(h)
        elif manos == "tu" and sev in ("roja", "ambar"):
            tu.append(h)
        else:
            luego.append(h)
    tu.sort(key=lambda h: (not h.get("es_cuello"), SEV_ORDEN.get(h.get("_sev"), 3)))
    if canal == "telegram":
        # Índice numerado del 🔴 TÚ → para cerrar por número ("hecho 2"). Mismo orden que se numera.
        guardar_indice_hoy([h.get("id") for h in tu[:8]])

    hdr_dia = "%s %d %s" % (_DIAS[hoy.weekday()], hoy.day, _MESES[hoy.month])
    L = ["%s HOY · %s" % (FRANJA_EMOJI.get(franja, "🗓️"), hdr_dia)]

    if hechos_hoy:
        titulos = " · ".join(i.get("titulo", "?") for i in hechos_hoy[:5])
        extra = " …(+%d)" % (len(hechos_hoy) - 5) if len(hechos_hoy) > 5 else ""
        L.append("✅ HECHO HOY (%d): %s%s" % (len(hechos_hoy), titulos, extra))

    if tu:
        L.append("")
        L.append("🔴 TÚ, AHORA")
        for n, h in enumerate(tu[:8]):
            num = _NUMS[n] if n < len(_NUMS) else "•"
            acc = _accion_corta(h.get("siguiente_accion"))
            linea = "%s %s %s" % (num, _icono(h), h.get("titulo", "?"))
            if acc:
                linea += " → " + acc
            L.append(linea)
            pq = _por_que(h)
            if pq:
                L.append("      🔓 " + pq)
        if len(tu) > 8:
            L.append("• …y %d más urgentes" % (len(tu) - 8))

    if yo:
        L.append("")
        L.append("🤖 YO ME OCUPO · tú nada")
        for h in yo[:5]:
            acc = _accion_corta(h.get("siguiente_accion")) or h.get("titulo", "?")
            L.append("   → %s %s" % (_icono(h), acc))
        if len(yo) > 5:
            L.append("   → …y %d más en marcha" % (len(yo) - 5))

    n_clic = len(clic) + len(data["pendientes_ok"])
    if n_clic:
        L.append("")
        L.append("✍️ A UN CLIC · sin prisa")
        for h in clic[:4]:
            L.append("   → %s %s" % (_icono(h), h.get("titulo", "?")))
        sueltos = len(data["pendientes_ok"]) + max(0, len(clic) - 4)
        if sueltos:
            L.append("   → %d borrador(es) más listos, tú decides" % sueltos)

    if luego:
        temas = []
        for h in luego:
            t = (h.get("categoria") or "otros").capitalize()
            if t not in temas:
                temas.append(t)
        L.append("")
        L.append("😴 NO APRIETA HOY → " + " · ".join(temas[:6]))

    if pend_lineas:
        L.append("")
        L.append("✉️ ESPERAN TU RESPUESTA")
        L.extend(pend_lineas)

    if not (tu or yo or clic or hechos_hoy or pend_lineas):
        L.append("✅ Todo al día, nada urgente.")

    if franja == "noche":
        quedan = len(tu)
        L.append("")
        L.append(("— Lo que queda pasa a mañana (%d). Descansa 💜" % quedan) if quedan
                 else "— Día cerrado, nada pendiente. Descansa 💜")

    for a in data["avisos"]:                  # dead-man: una fuente caída sí se dice
        L.append(a)
    salida = "\n".join(L)
    # Privacidad-en-render: por Telegram, NINGÚN nombre de persona (terceros/colaboradores). La
    # cadena clínica (cumbre) SÍ se ve, pero con el nombre redactado. En local va íntegro ({{TITULAR}}
    # sí los ve en El Tablero). DURABLE: no depende del flag `privado`, que un daemon puede pisar.
    return _redactar_personas(salida) if canal == "telegram" else salida


# Alias de compatibilidad: el subcomando `check` y las rutinas siguen llamando a esto.
construir_check = construir_hoy


# ── ESPEJO A NOTION (nube de terceros) — ALLOWLIST FAIL-CLOSED ──────────────────
# El veredicto del comité (21/6): Telegram es self-endpoint, Notion NO. El gate de
# Telegram es BLOCKLIST (falla abierto). Aquí el modelo es ALLOWLIST: ante la duda, NO
# sale. Tres puertas independientes (categoría, contenido del item, saneado del título) +
# recompute en cada sync, sin fiarse del flag `privado` congelado. Lo clínico/cumbre y
# cualquier PII de terceros NUNCA suben.
NOTION_CATEGORIAS_OK = {"voz", "seguridad", "finanzas", "prensa", "infra"}
NOTION_CAMPOS = ("id", "titulo", "categoria", "estado", "plazo", "gate")
# Nombres de TERCEROS que jamás deben aparecer en un título que va a la nube.
# Nombres de personas reales (contactos, colaboradores, médicos). Estaban aquí en claro:
# una lista de terceros identificables, versionada, que ninguno de ellos consintió publicar.
# Viven en `nombres.local.json` (gitignored, junto a este fichero).
# Fail-open consciente: sin overlay se redacta MENOS, no más. No se puede hacer fail-closed
# sin volver a meter en git lo que se quiere sacar.
_NOMBRES_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nombres.local.json")
# …y el de CASA BASE. El overlay es gitignored, así que en un worktree no existe y la deny-list
# se quedaba VACÍA sin avisar: cada sesión que editaba en rama corría con el muro más flojo que
# la casa base, justo donde más código nuevo se prueba. El fail-open de abajo se pensó para un
# clon sin overlay, no para esto: aquí el fichero está al lado, solo había que ir a buscarlo.
# Detectado 20-sep-26 construyendo el set dorado del triage.
_NOMBRES_BASE = os.path.join(
    os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"), "tools", "nombres.local.json")


def _cargar_overlay(clave="nombres"):
    """Une la lista `clave` del overlay del repo actual con la de casa base. Unir y no elegir: si
    algún día un worktree tiene su propia lista, suma en vez de pisar — en una deny-list, de más
    es seguro."""
    nombres = set()
    vistos = set()
    for ruta in (_NOMBRES_LOCAL, _NOMBRES_BASE):
        real = os.path.realpath(ruta)
        if real in vistos:
            continue
        vistos.add(real)
        try:
            with io.open(ruta, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        nombres |= {str(x).lower() for x in d.get(clave, []) if str(x).strip()}
    return frozenset(nombres)


def _cargar_nombres_deny():
    return _cargar_overlay("nombres")


_NOMBRES_DENY = _cargar_nombres_deny()
# Lugares e instituciones de SU ruta (22-sep-26). Una búsqueda que junta uno de ellos con un término
# del embargo («vacuna … <su hospital>») la re-identifica aunque no diga su nombre: lo cazó `verificacion`
# sobre b9d6c29. Viven en el mismo overlay gitignored (clave `lugares_ruta`): la lista ya cuenta su
# recorrido. Sin overlay, `borde.clasificar_consulta` vuelve al borde estricto (fail-closed).
_LUGARES_RUTA = _cargar_overlay("lugares_ruta")
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_RE_HANDLE = re.compile(r"@\w{2,}")
_RE_DINERO = re.compile(r"[$€£]\s?\d|\b\d[\d.,]*\s?(k|mil|chf|usd|eur|euros|dólares|dolares)\b", re.I)
_RE_PRIVADO = re.compile(r"_privado", re.I)
# Términos que JAMÁS salen a la nube (regla del muro). Se chequean por SUBSTRING (caza "vacunas",
# "{{CONTACTO}}", "neoantígenos"…), no por palabra — por eso van aquí y no en _NOMBRES_DENY (los nombres sí
# son por palabra, para no sobre-bloquear: "sid" no debe vetar "considera"). DOS NIVELES:
#  · DURABLE — "{{CONTACTO}}" no se nombra en público NUNCA (regla permanente del muro).
#  · EMBARGO — la "vacuna"/neoantígenos y los nombres-ruta no se nombran TODAVÍA (hasta el reveal de
#    Carlos Roca, en días). Es TEMPORAL: se levanta SOLO por acto humano de {{TITULAR}} (flip del flag
#    embargo_publico.json), NUNCA auto por fecha. Ver _embargo_activo() / _terminos_vetados_ahora().
_VETO_DURABLE = ("contacto",)
_VETO_EMBARGO = ("vacuna", "neoantíg", "neoantig", "{{VACUNA}}", "{{VACUNA2}}", "v940")
# Compat: el nombre histórico = la veda con el embargo ACTIVO (comportamiento de hoy). La fuente VIVA
# es _terminos_vetados_ahora() — no uses esta constante para decidir, que no ve el flag.
_TERMINOS_VETADOS = _VETO_DURABLE + _VETO_EMBARGO
# Términos del embargo que {{TITULAR}} YA levantó para lo PÚBLICO, uno a uno. El flag reveal:true
# levanta el embargo ENTERO (neoantígenos y nombres-ruta incluidos) y ella no lo ha hecho; lo que sí
# hizo fue levantar la palabra «vacuna» el 29-7-26 (.claude/rules/marca-copy.md, commits 177a669 y
# 532f753). Solo afecta a _terminos_vetados_ahora() (espejos y copy público). NO toca el borde de
# egress ni deid, que siguen usando _TERMINOS_VETADOS entero: que «vacuna» se pueda PUBLICAR no
# significa que un texto con «vacuna» + su caso pueda salir a un LLM de terceros. Añadir aquí un
# término es acto de {{TITULAR}}, como el flag; esta tupla solo deja constancia en código versionado.
_EMBARGO_LEVANTADO_PUBLICO = ("vacuna",)

# Dianas moleculares del caso (FGFR1/{{LOCUS}}/CDK2/{{DIANA}}/TMB…): **PÚBLICAS por directiva de {{TITULAR}}
# (25/6): "mis dianas son públicas; la curiosidad también acerca a NED"** — comparte su biología
# abiertamente para atraer a los científicos correctos. Por eso NO se vetan en el espejo (marketing
# puede de-enfatizarlas en su copy, pero eso es ÉNFASIS, no muro). La lista queda como VÁLVULA: VACÍA
# por defecto; si algún término concreto llegara a BLOQUEAR NED ("si no me bloquea NED son públicas"),
# se añade aquí y vuelve a taparse. El mecanismo (normalización anti-ofuscación + chequeo) sigue vivo
# y testeado. Comprobado por _texto_publicable sobre el texto NORMALIZADO ("FGFR-1"→"fgfr1").
_DIANAS_VETADAS = ()


def _embargo_activo():
    """True si el embargo público sigue VIGENTE (la 'vacuna'/neoantígenos no se nombran aún).
    Fail-SAFE: si el flag no existe o es ilegible, el embargo está ACTIVO (nada se cuela). Se levanta
    SOLO por acto humano de {{TITULAR}}: poner {"reveal": true} en tools/state/embargo_publico.json (como
    levantar el código rojo). NUNCA auto por fecha: cambiar la política de egress sin un humano en el
    bucle es justo lo que el muro prohíbe."""
    try:
        with open(EMBARGO_FLAG, encoding="utf-8") as f:
            return (json.load(f) or {}).get("reveal") is not True
    except Exception:
        return True


def _terminos_vetados_ahora():
    """Términos vetados EFECTIVOS ahora mismo = durables + (embargo si sigue vigente). Fuente VIVA de
    la veda por-substring; la usan el espejo de tareas y el de investigación. Con el embargo vigente
    se descuentan los términos que {{TITULAR}} levantó uno a uno (_EMBARGO_LEVANTADO_PUBLICO)."""
    if not _embargo_activo():
        return _VETO_DURABLE
    return _VETO_DURABLE + tuple(t for t in _VETO_EMBARGO if t not in _EMBARGO_LEVANTADO_PUBLICO)


# ── Desofuscación anti-evasión (misma técnica que borde._normalizar, reforzada con homoglifos) ──
# Un gate que casa por substring sobre texto CRUDO es trivial de evadir ("O l u n e", "vacüna",
# zero-width, homoglifos cirílicos). Por eso TODO match de término/nombre/PII (de los espejos Y de
# borde, vía seg._canon) se hace sobre la forma DESOFUSCADA. Reusamos la pared, no reimplementamos una
# más débil — fue justo el fallo que cazó `verificacion` (25/6) antes de fusionar este egress.
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
# Homoglifos cirílico/griego → latín, curado a las letras de los términos del muro (cierra "Оlune"
# con О cirílica). Curado para minimizar falsos positivos en prosa legítima.
_HOMOGLYPH = str.maketrans({
    "а": "a", "е": "e", "о": "o", "с": "c", "р": "p", "у": "y", "х": "x", "к": "k", "м": "m",
    "т": "t", "н": "n", "в": "b", "і": "i", "ѕ": "s", "ј": "j", "ԁ": "d", "л": "l",
    "ο": "o", "α": "a", "ε": "e", "ρ": "p", "ν": "v", "τ": "t", "κ": "k", "ι": "i", "χ": "x",
    "υ": "u", "η": "n",
})


def _s(v):
    """Coacciona a str (no-str → '') para no crashear el gate con datos malformados (fail-closed)."""
    return v if isinstance(v, str) else ""


def _desofusca(t):
    """Forma comparable: NFKD + quita diacríticos combinantes + zero-width + pliega homoglifos, en
    minúsculas. Cierra evasión por acentos falsos ('vacüna'), invisibles y homoglifos ('Оlune')."""
    if not isinstance(t, str):
        return ""
    t = unicodedata.normalize("NFKD", t).translate(_ZERO_WIDTH)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.lower().translate(_HOMOGLYPH)


def _canon(t):
    """Forma canónica para match de TÉRMINOS/dianas: desofusca + quita TODO lo no-alfanumérico. Caza
    el separador metido a mano ('O l u n e'→'contacto', '{{VACUNA2}}'→'mrna4157'); se normaliza ASÍ el
    término Y el texto, así un veto con separador propio también casa (cierra la clase de la fuga #2)."""
    return re.sub(r"[^0-9a-z]", "", _desofusca(t))


def _texto_publicable(t):
    """Gate de un texto que va al espejo de INVESTIGACIÓN: PII/términos vetados/terceros/{{CONTACTO}}/embargo
    (vía _titulo_seguro, robusto a ofuscación) + la VÁLVULA de dianas `_DIANAS_VETADAS` (VACÍA por
    defecto: las dianas son públicas por directiva de {{TITULAR}} 25/6). Fail-closed (no-str/vacío → False)."""
    if not _titulo_seguro(t):
        return False
    canon = _canon(t)
    return not any(_canon(d) in canon for d in _DIANAS_VETADAS)


# Deny-list de terceros en forma desofuscada (así "contacto"→"contacto" casa con o sin acento/homoglifo).
# Derivada de _NOMBRES_DENY (única fuente); no la dupliques a mano.
_NOMBRES_DENY_CANON = frozenset(_desofusca(n) for n in _NOMBRES_DENY)


def _nombra_tercero(t):
    """True si el texto nombra a un TERCERO de la denylist (match por PALABRA sobre la forma
    DESOFUSCADA, no substring: 'sid' no veta 'considera'; pero 'Yаnnick' con homoglifo SÍ casa). Lo usa
    `_titulo_seguro` (allowlist a la nube): un nombre de tercero excluye el registro entero."""
    palabras = set(re.findall(r"[a-z]+", _desofusca(t)))
    return bool(palabras & _NOMBRES_DENY_CANON)


def _oculto_en_telegram(i):
    """Gate de privacidad del carril Telegram (self-endpoint privado de {{TITULAR}}). Oculta el ítem SOLO
    si está marcado `privado` (= ocultar un TEMA entero, p.ej. el caso legal). El NOMBRE de un tercero
    ya NO oculta el ítem: se REDACTA en el render (`_redactar_personas`) — regla de {{TITULAR}} (22/6): los
    recordatorios (incl. la cadena clínica de cumbre, su misión nº1) se VEN, pero sin ningún nombre de
    persona. Redactar es backstop superior a ocultar-por-nombre: caza el nombre en título, acción o
    bloqueo, no solo en el título, y no esconde el recordatorio. (El saneado por-nombre
    `_nombra_tercero` sigue VIVO, pero para el espejo a Notion: allowlist a la nube, donde un nombre
    de tercero NUNCA puede llegar — ahí sí se excluye el registro entero.)"""
    return bool(i.get("privado"))


def _titulo_seguro(t):
    """True solo si el texto no contiene términos vetados/PII/importes/rutas/handles/terceros, ROBUSTO
    a ofuscación (acentos falsos, zero-width, homoglifos, separadores). Fail-closed (no-str/vacío → False)."""
    if not isinstance(t, str) or not t:
        return False
    low = _desofusca(t)
    canon = _canon(t)
    if any(_canon(term) in canon for term in _terminos_vetados_ahora()):
        return False
    if _RE_EMAIL.search(low) or _RE_HANDLE.search(low) or _RE_DINERO.search(low) or _RE_PRIVADO.search(low):
        return False
    return not _nombra_tercero(t)


# ── REDACCIÓN DE NOMBRES EN EL RENDER DE TELEGRAM ───────────────────────────────────
# Regla de {{TITULAR}} (22/6): por Telegram NO aparece NINGÚN nombre de PERSONA (terceros NI
# colaboradores) — "tapa a todo el mundo". A diferencia de ocultar el ítem (que escondería la
# biopsia, su misión nº1), aquí se REDACTA el nombre en el texto final y el estado/recordatorio con
# fecha se MANTIENE. Es la capa DURABLE: actúa en el render, así que aguanta aunque un daemon
# reescriba `seguimiento.json` y pise el flag `privado`. SOLO nombres de persona: lugares e
# instituciones (Zúrich, {{CENTRO}}, Fred Hutch, {{CENTRO}}, Moffitt, BioNTech, {{CENTRO}}, {{CENTRO}},
# Morales…) se conservan porque son señal clínica, no PII de un tercero. Curado y ampliable.
_PERSONAS_REDACT = frozenset({
    # personas de la denylist (se excluye "contacto" = empresa; "titular" JAMÁS se redacta: es ella).
    # OJO: "contacto" NO va — es palabra común ("contacto y pestañas"); a {{CONTACTO}} {{CONTACTO}} la cubre "contacto".
    "contacto", "contacto", "contacto", "contacto", "contacto", "contacto", "contacto",
    "contacto", "contacto", "contacto", "contacto", "contacto", "contacto", "contacto", "contacto", "contacto",
    "contacto", "contacto", "contacto", "contacto", "contacto", "contacto", "contacto", "contacto",
    "contacto", "contacto", "contacto", "contacto", "contacto", "contacto", "contacto", "contacto",
    "esperanza", "sid", "contacto", "gil",
    # médicos / labs / contactos vistos en el registro
    "bernardo", "cordovez", "contacto", "contacto", "cárdenas", "cardenas", "ruth", "gumbau",
    "domínguez", "dominguez", "bassani", "sternberg", "contacto", "navares", "rocío", "rocio",
    "{{CONTACTO}}", "veatch", "okada", "soyano", "hideho", "aixa", "michal", "catherine", "wu",
    "olmos", "elizabeth", "yarmarkovich", "carlos", "débora", "debora",
})
# A propósito FUERA (ambiguos / no-persona): "vega" (la asistente), "titular" (la usuaria),
# "ana"/"rosa"/"roca"/"morales" (palabra común o institución). Falso positivo = solo estética (es
# su feed privado); un nombre que se escape = fallo real → ante la duda, se incluye en el set.
_HONORIFICOS = ("dr", "dra", "dres", "prof", "profa", "sr", "sra", "srta")
_re_nom_pers = "|".join(sorted((re.escape(n) for n in _PERSONAS_REDACT), key=len, reverse=True))
_re_honor = "|".join(_HONORIFICOS)
# Un "átomo" redactable = honorífico opcional pegado (Dr./Dra./Prof.) + un nombre, por PALABRA (\b).
_RE_ATOMO = r"(?:(?:%s)\.?\s+)?(?:%s)\b" % (_re_honor, _re_nom_pers)
# Secuencia de átomos consecutivos unidos por espacio o guion (NO barra: "viaje/{{CONTACTO}}/lab" deja
# "viaje" y "lab"). Colapsa "Dr. {{CONTACTO}} {{CONTACTO}}" y "Bassani-Sternberg" a UN solo marcador.
_RE_PERSONAS = re.compile(r"\b%s(?:(?:[ \t]+|[ \t]*-[ \t]*)%s)*" % (_RE_ATOMO, _RE_ATOMO), re.IGNORECASE)


def _redactar_personas(text):
    """Sustituye todo nombre de PERSONA (con su honorífico pegado) por '[contacto]', por límite de
    palabra (case-insensitive). SOLO para el carril Telegram; el local va íntegro. No toca
    lugares/instituciones/importes/fechas — solo el nombre del tercero/colaborador."""
    if not text:
        return text
    return _RE_PERSONAS.sub("[contacto]", text)


INVEST_CAMPOS = ("id", "titulo", "autores", "enlace", "relevancia")
# Campos que deben pasar el gate de contenido antes de salir. El `enlace` (DOI/URL) lo atesta el humano
# con `publico:true`, PERO además se le pasa el gate como defensa-en-profundidad (verificacion 25/6: el
# enlace era el único punto que descansaba SOLO en el juicio humano) — así un slug/param con PII,
# término vetado o nombre de tercero excluye el ítem aunque el humano se despiste. Las dianas NO se
# vetan (válvula vacía), así que un DOI sobre FGFR1 sale igual; un slug con "contacto" o un email, no.
_INVEST_CAMPOS_TEXTO = ("autores", "relevancia", "enlace")


def _campo_publicable(v):
    """Un campo de texto libre OPCIONAL es publicable si está VACÍO (no hay nada que filtrar) o pasa
    el gate. (El título NO usa esto: es obligatorio y se exige publicable.) Tolerante a no-str (→ vacío)."""
    v = _s(v).strip()
    return v == "" or _texto_publicable(v)


def _construir_export_investigacion():
    """Proyección DERIVADA y segura de la lista de literatura para la nube (Notion). ALLOWLIST
    fail-closed POR-ÍTEM: nada sale si no está marcado `publico:true` Y todos sus campos de texto
    pasan el gate (que veta PII/términos vetados/terceros/dianas). Devuelve solo INVEST_CAMPOS."""
    out = []
    for it in load_investigacion():
        if not isinstance(it, dict):
            continue                                  # basura → fuera
        # Puerta 1 — opt-in explícito (default excluido). El radar nunca marca esto.
        if it.get("publico") is not True:
            continue
        # Puerta 2 — texto saneado: título obligatorio y publicable; autores/relevancia, si los hay.
        titulo = _s(it.get("titulo")).strip()
        if not titulo or not _texto_publicable(titulo):
            continue
        if not all(_campo_publicable(it.get(c)) for c in _INVEST_CAMPOS_TEXTO):
            continue
        # Puerta 3 — solo los campos de la allowlist (jamás flags internos ni notas privadas).
        out.append({k: it.get(k) for k in INVEST_CAMPOS})
    return out


def construir_export(canal="notion"):
    """Proyección DERIVADA y segura para un canal de nube (Notion). ALLOWLIST fail-closed.
    NUNCA toca cumbre.json (clínico). Devuelve solo registros con campos de NOTION_CAMPOS.
    La privacidad se RECOMPUTA aquí en cada llamada — no se confía en el flag guardado."""
    if canal == "investigacion":
        return _construir_export_investigacion()
    seg = load_seguimiento()
    if seg.get("_error"):
        return []          # registro ilegible → no exportamos nada (fail-closed)
    out = []
    for h in seg.get("hilos", []):
        # Puerta 1 — recompute privacidad/elegibilidad (no fiarse del flag congelado).
        if h.get("privado") is True:
            continue
        if h.get("categoria") not in NOTION_CATEGORIAS_OK:
            continue        # categoría no listada → fuera por defecto
        if h.get("ref_cumbre"):
            continue        # referencia la cadena clínica → fuera
        if h.get("categoria") == "clinico":
            continue        # cinturón y tirantes
        if h.get("estado") not in ESTADOS:
            continue        # estado raro/ausente → fuera
        # Puerta 2 — título saneado (caza PII aunque privado fuera False por error).
        if not _titulo_seguro(h.get("titulo", "")):
            continue
        # Puerta 3 — solo los campos de la allowlist (jamás fuente/quien_espera/siguiente_accion/bloqueo).
        rec = {k: h.get(k) for k in NOTION_CAMPOS}
        out.append(rec)
    return out


_ESTADO_VIS = {"en_curso": "🟢 En curso", "esperando": "⏳ Esperando",
               "bloqueado": "🚨 Bloqueado", "por_confirmar": "❓ Por confirmar", "hecho": "✅ Hecho"}


def render_notion_md():
    """Markdown del ESPEJO de solo-lectura para Notion. Se regenera entero en cada sync
    (idempotente: el escritor hace replace_content sobre UNA página). Solo datos del export
    seguro (allowlist). Lleva cabecera 'solo lectura' + sello de sync."""
    recs = construir_export("notion")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lin = []
    lin.append("> 🔄 **Espejo de la asistente · SOLO LECTURA.** Lo escribe el sistema; "
               "**edita en el origen, no aquí.** Última sync: %s." % ts)
    lin.append("> Lo privado (terceros), lo clínico y las credenciales **no salen aquí** "
               "(se ven en local). Esto es una vista de coordinación, no la fuente de verdad.")
    lin.append("")
    if not recs:
        lin.append("_Sin hilos que mostrar ahora mismo._")
    else:
        lin.append("| Hilo | Estado | Plazo | Espera |")
        lin.append("| --- | --- | --- | --- |")
        for r in recs:
            lin.append("| %s | %s | %s | %s |" % (
                r.get("titulo", "?"), _ESTADO_VIS.get(r.get("estado"), r.get("estado") or "—"),
                r.get("plazo") or "—", r.get("gate") or "—"))
    return "\n".join(lin)


def _celda(s):
    """Sanea una celda de tabla markdown: sin '|' (rompe la columna) ni saltos de línea. Tolera no-str."""
    return re.sub(r"\s+", " ", _s(s).replace("|", "/")).strip()


def render_investigacion_md():
    """Markdown del ESPEJO de investigación (solo-lectura) para Notion. Se regenera entero en cada
    sync (idempotente: replace_content sobre UNA página fija). Solo papers del export seguro (allowlist
    por-ítem). Cabecera 'solo lectura' + el aviso HONESTO de que es una lista CURADA, no toda la
    investigación (el radar es local), para que nadie confunda 'escaso' con 'completo' + sello."""
    recs = construir_export("investigacion")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lin = []
    lin.append("> 🔬 **Espejo de investigación · SOLO LECTURA.** Lo escribe el sistema; "
               "**edita en el origen, no aquí.** Última sync: %s." % ts)
    lin.append("> Lista CURADA de papers marcados como públicos — **no es toda la investigación** "
               "(el radar de literatura se queda en local). No salen aquí: datos de terceros (PII), "
               "credenciales, ni lo que siga bajo embargo. (Las dianas del caso son públicas.)")
    lin.append("")
    if not recs:
        lin.append("_Sin papers marcados como públicos todavía._")
    else:
        # Lista (no tabla): los resúmenes son párrafos y el enlace debe ser CLICABLE — los enlaces
        # markdown NO se renderizan dentro de celdas de tabla en Notion, sí en texto normal.
        for r in recs:
            tit = _celda(r.get("titulo")) or "?"
            enl = _celda(r.get("enlace"))
            cab = "**[%s](%s)**" % (tit, enl) if enl else "**%s**" % tit
            aut = _celda(r.get("autores"))
            if aut:
                cab += " — _%s_" % aut
            lin.append(cab)
            rel = _celda(r.get("relevancia"))
            if rel:
                lin.append(rel)
            lin.append("")
    return "\n".join(lin).rstrip() + "\n"


def investigacion_add(obj):
    """Añade/actualiza un paper en la lista de investigación. SIEMPRE entra como `publico:false` (nada
    es público sin un flip explícito posterior — el greenlight de egress es un acto humano deliberado,
    a mano sobre investigacion.json). Si el id ya existe, ACTUALIZA preservando su `publico`. id."""
    if not isinstance(obj, dict) or not _s(obj.get("titulo")).strip():
        raise ValueError("hace falta al menos 'titulo'")
    try:
        with open(INVESTIGACION, encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception:
        data = {}
    items = data.get("items")
    if not isinstance(items, list):
        items = []
    titulo = _s(obj.get("titulo")).strip()
    iid = _s(obj.get("id")).strip() or _slug(titulo)
    rec = {
        "id": iid, "titulo": titulo,
        "autores": _s(obj.get("autores")).strip(),
        "enlace": _s(obj.get("enlace")).strip(),
        "relevancia": _s(obj.get("relevancia")).strip(),
        "publico": False,                      # SIEMPRE false al añadir (opt-in explícito aparte)
    }
    for i, it in enumerate(items):
        if isinstance(it, dict) and it.get("id") == iid:
            items[i] = {**it, **rec, "publico": bool(it.get("publico", False))}   # preserva su publico
            break
    else:
        items.append(rec)
    data["items"] = items
    _write_atomic(INVESTIGACION, data)
    return iid


def investigacion_sync(items):
    """Reemplaza ENTERA la fuente del espejo (idempotente, como regenera el render) desde una lista YA
    CURADA de papers públicos. CASO DE USO: el volcado que hace el agente desde la base Notion
    «Biblioteca de papers» con las filas `Publicar en web ✓` (ESE checkbox ES el visto bueno humano),
    mapeando SOLO columnas públicas (Título→titulo, Autores→autores, URL/DOI→enlace, «Resumen público
    (web)»→relevancia). NUNCA se mapea `Notas internas` ni columnas clínicas. Cada ítem entra
    `publico:true` (su origen es el flag curado), PERO el gate de `construir_export` los RE-FILTRA igual
    (backstop fail-closed: embargo/{{CONTACTO}}/PII/dato clínico que se hubiera colado → fuera). Lista vacía →
    espejo vacío (idempotente). Devuelve cuántos ítems quedaron en la fuente."""
    if not isinstance(items, list):
        raise ValueError("se esperaba una lista de papers")
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        titulo = _s(it.get("titulo")).strip()
        if not titulo:
            continue                     # sin título → se ignora (no rompe el lote)
        out.append({
            "id": _s(it.get("id")).strip() or _slug(titulo),
            "titulo": titulo,
            "autores": _s(it.get("autores")).strip(),
            "enlace": _s(it.get("enlace")).strip(),
            "relevancia": _s(it.get("relevancia")).strip(),
            "publico": True,             # origen = flag `Publicar en web` curado; el gate re-filtra
        })
    _write_atomic(INVESTIGACION, {"items": out})
    return len(out)


DUENOS_CUMBRE = {"biopsia": "oncologo-virtual", "dianas": "comite-medico", "ensayo": "consejero-acceso",
                 "acceso": "legal-burocracia", "medible": "comite-medico"}


def render_tracks():
    """Vista 'un plan por tema hacia NED' (spin-off estructural): DERIVADA de cumbre.json
    (cadena clínica) + seguimiento.json (operativo). NO es una 4ª fuente de verdad — solo
    presenta, por tema, objetivo-NED · estado · siguiente paso (🟢/🛑) · dueño."""
    cumbre = load_cumbre()
    seg = load_seguimiento()
    aqui = cumbre.get("aqui_estamos") or ""
    lin = ["🎯 TRACKS HACIA NED — un plan por tema (vista derivada, no fuente de verdad)",
           "Meta: %s" % (cumbre.get("meta") or "NED — sin evidencia de enfermedad"), ""]
    lin.append("══ Cadena clínica a NED ══")
    for sal in cumbre.get("salientes", []):
        cuello = " ⭐CUELLO DE HOY" if sal.get("id") == aqui else ""
        lin.append("• [%s]%s — %s" % (sal.get("id"), cuello, sal.get("titulo", "")))
        lin.append("    estado: %s · dueño: %s" % (sal.get("estado", "?"), DUENOS_CUMBRE.get(sal.get("id"), "—")))
        if sal.get("siguiente_accion"):
            lin.append("    → %s" % sal["siguiente_accion"])
    for t in cumbre.get("transversal", []):
        lin.append("• [%s] (gate transversal) %s — estado: %s · dueño: %s" % (
            t.get("id"), t.get("titulo", ""), t.get("estado", "?"), DUENOS_CUMBRE.get(t.get("id"), "—")))
        if t.get("siguiente_accion"):
            lin.append("    → %s" % t["siguiente_accion"])
    lin += ["", "══ Tracks operativos (soporte a NED) ══"]
    porcat = {}
    for h in seg.get("hilos", []):
        porcat.setdefault(h.get("categoria", "otros"), []).append(h)
    for cat in sorted(porcat):
        lin.append("• categoría: %s" % cat)
        for h in porcat[cat]:
            marca = "🛑gate" if h.get("gate") else "🟢"
            lin.append("    %s %s" % (marca, h.get("titulo", "?")))
            if h.get("objetivo_ned"):
                lin.append("        NED: %s" % h["objetivo_ned"])
            if h.get("siguiente_accion"):
                lin.append("        → %s [dueño: %s]" % (h["siguiente_accion"], h.get("dueno", "—")))
    return "\n".join(lin)


# ── CIERRE FÁCIL (Fase 3): "di HECHO y ya" — una puerta, reversible, local ──────
# Cerrar una tarea es lo ÚNICO que {{TITULAR}} hace al final; debe ser facilísimo y desde
# donde le salga (nº en Telegram / palabras / voz / Tablero). Todo pasa por aquí:
# estado LOCAL, reversible, nada hacia fuera, y a {{TITULAR}} NUNCA se le bloquea ni se le juzga.
HOY_INDICE = os.path.join(STATE, "hoy_indice.json")   # mapa nº→hilo_id del último parte de Telegram


@_serializado
def cerrar_tarea(hilo_id, por="titular"):
    """Cierra un hilo (estado=hecho, sella hecho_el, deja de perseguirse). LOCAL y reversible;
    no envía/publica nada. Nunca lanza (camino de {{TITULAR}} a prueba de balas). Devuelve el hilo
    cerrado o None. Las fases clínicas (cumbre:*) no se cierran desde aquí."""
    try:
        if not hilo_id or str(hilo_id).startswith("cumbre:"):
            return None
        seg = load_seguimiento()
        if seg.get("_error"):
            return None
        hoy_iso = datetime.now().strftime("%Y-%m-%d")
        for h in seg.get("hilos", []):
            if h.get("id") == hilo_id:
                h["estado"] = "hecho"
                h["hecho_el"] = hoy_iso
                h["ultimo_aviso"] = None
                h["aviso_estado"] = None
                seg["actualizado"] = hoy_iso
                _write_atomic(SEG, seg)
                return h
    except Exception:
        return None
    return None


@_serializado
def reabrir_tarea(hilo_id):
    """Deshacer un cierre: vuelve a 'esperando' y limpia el sello de hecho. Reversible, local."""
    try:
        seg = load_seguimiento()
        if seg.get("_error"):
            return None
        for h in seg.get("hilos", []):
            if h.get("id") == hilo_id:
                h["estado"] = "esperando"
                h["hecho_el"] = None
                h["ultimo_aviso"] = None      # que vuelva a perseguirse fresco
                h["aviso_estado"] = None
                seg["actualizado"] = datetime.now().strftime("%Y-%m-%d")
                _write_atomic(SEG, seg)
                return h
    except Exception:
        return None
    return None


def guardar_indice_hoy(ids):
    """Persiste el mapa nº→hilo_id de los ítems numerados del parte (🔴 TÚ), para 'hecho N'.
    Se reescribe en cada parte de Telegram → siempre refleja el último que vio {{TITULAR}}."""
    try:
        mapa = {str(i + 1): hid for i, hid in enumerate(ids) if hid}
        _write_atomic(HOY_INDICE, {"actualizado": datetime.now().isoformat(timespec="seconds"),
                                   "mapa": mapa})
    except Exception:
        pass


def _cargar_indice_hoy():
    try:
        with open(HOY_INDICE, encoding="utf-8") as f:
            return (json.load(f) or {}).get("mapa", {}) or {}
    except Exception:
        return {}


def cerrar_por_indice(nums):
    """Cierra por número(s) del último parte: 'hecho 2', '1 3'. Un número fuera de rango se
    ignora sin romper. Devuelve {cerrados:[{id,titulo}], no_encontrados:[n]}."""
    mapa = _cargar_indice_hoy()
    cerrados, no = [], []
    for n in nums:
        hid = mapa.get(str(n))
        h = cerrar_tarea(hid, por="titular") if hid else None
        if h:
            cerrados.append({"id": h["id"], "titulo": h.get("titulo", ""),
                             "hecho_cuando": h.get("hecho_cuando", "")})
        else:
            no.append(n)
    return {"cerrados": cerrados, "no_encontrados": no}


_CIERRE_STOP = {"hecho", "hice", "termine", "terminar", "terminado", "listo", "lista", "acabe",
                "acabo", "acabado", "ya", "del", "los", "las", "una", "uno", "ese", "esa", "esto",
                "eso", "que", "para", "con", "por", "lo", "la", "el", "de", "mi", "ya"}


def _norm(s):
    import unicodedata
    s = (s or "").lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", s)


def cerrar_por_texto(frase):
    """Match difuso de la frase ('ya hice lo del CD') contra los hilos ABIERTOS por solapamiento
    de palabras. Si hay UN match claro → lo cierra. Si hay empate → devuelve candidatos (no
    adivina; Vega pregunta 1 línea). Determinista. Devuelve {cerrado, candidatos}."""
    seg = load_seguimiento()
    if seg.get("_error"):
        return {"cerrado": None, "candidatos": []}
    palabras = {p for p in _norm(frase).split() if len(p) >= 2} - _CIERRE_STOP
    if not palabras:
        return {"cerrado": None, "candidatos": []}
    puntuados = []
    for h in seg.get("hilos", []):
        if h.get("estado") == "hecho":
            continue
        tit = set(_norm(h.get("titulo", "")).split())
        score = len(palabras & tit)
        if score:
            puntuados.append((score, h))
    if not puntuados:
        return {"cerrado": None, "candidatos": []}
    puntuados.sort(key=lambda x: -x[0])
    mejor = puntuados[0][0]
    top = [h for sc, h in puntuados if sc == mejor]
    if len(top) == 1:
        h = cerrar_tarea(top[0].get("id"), por="titular")
        if h:
            return {"cerrado": {"id": h["id"], "titulo": h.get("titulo", ""),
                                "hecho_cuando": h.get("hecho_cuando", "")}, "candidatos": []}
    return {"cerrado": None,
            "candidatos": [{"id": h.get("id"), "titulo": h.get("titulo", "")} for sc, h in puntuados[:4]]}


def buscar_similar(frase, minimo=2):
    """Hilos ABIERTOS que solapan >= `minimo` palabras significativas (len>=3) con la frase.
    Para AVISAR 'ya tienes esto' antes de crear un duplicado (dedup conservador, anti-falsos
    positivos). NO cierra ni crea nada. Devuelve [{id,titulo,score}] por score desc, [] si nada
    fuerte. Reusa _norm/_CIERRE_STOP como cerrar_por_texto."""
    seg = load_seguimiento()
    if seg.get("_error"):
        return []
    palabras = {p for p in _norm(frase).split() if len(p) >= 3} - _CIERRE_STOP
    if len(palabras) < minimo:
        return []
    out = []
    for h in seg.get("hilos", []):
        if h.get("estado") == "hecho":
            continue
        tit = set(_norm(h.get("titulo", "")).split())
        score = len(palabras & tit)
        if score >= minimo:
            out.append({"id": h.get("id"), "titulo": h.get("titulo", ""), "score": score})
    out.sort(key=lambda x: -x["score"])
    return out[:4]


# ── EMPUJE (Fase 2): Vega SUPERVISA y AVISA — decide, NO ejecuta ─────────────────
# Por cada hilo que se cae, `perseguir` decide UNA salida determinista que el AGENTE
# consume (fundir en el parte de HOY, disparar código rojo con criterio, preparar un
# borrador "a un clic"). NO encola jobs, NO manda nada, NO toca a terceros. El ÚNICO
# efecto de --ejecutar es escribir el sello de idempotencia en el hilo (interno, local),
# para perseguir sin spamear. La severidad y el routing salen SOLO de enums/fechas: un
# texto plantado no decide a quién se delega (anti-inyección intacta).
COOLDOWN_AVISO_H = 24          # no re-aflorar el MISMO hilo ámbar antes de esto
CAP_AVISOS_PASADA = 8          # tope de salidas frescas por pasada (anti-avalancha; rotan)

# Categoría operativa → comité dueño. Enum CERRADO y SOLO agentes reales y seguros de
# entregar autónomo. clinico/personal/seguridad NO están a propósito → siempre AVISO a {{TITULAR}}.
DUENOS_CATEGORIA = {
    "legal": "legal-burocracia", "finanzas": "finanzas-transparencia",
    "voz": "voz-titular", "prensa": "prensa", "redes": "redes-contenido",
    "infra": "tecnico", "logistica": "agencia-viajes",
}
CATEGORIAS_SOLO_TITULAR = {"clinico", "clínico", "personal", "seguridad"}


def _agentes_reales():
    """Nombres de agente que EXISTEN de verdad (.claude/agents/*.md)."""
    try:
        d = os.path.join(REPO, ".claude", "agents")
        return {f[:-3] for f in os.listdir(d) if f.endswith(".md")}
    except Exception:
        return set()


def _es_clinico(i):
    """Cualquier cosa que toque la cadena clínica → NUNCA se delega ni se escribe; va a {{TITULAR}}."""
    return (str(i.get("id", "")).startswith("cumbre:")
            or (i.get("categoria") or "").lower().startswith(("clinic", "clínic"))
            or bool(i.get("ref_cumbre")))


def _resolver_dueno(h, agentes):
    """A quién le toca, SOLO por diccionario cerrado + agente real. El `dueno` plantado en el
    hilo solo cuenta si el origen es de confianza (un email no puede nombrar a su ejecutor)."""
    if _es_clinico(h):
        return None
    if (h.get("categoria") or "").lower() in CATEGORIAS_SOLO_TITULAR:
        return None
    cand = ""
    d = (h.get("dueno") or "").strip()
    if d and (h.get("origen") or "manual").lower() in ORIGENES_CONFIABLES \
            and d.lower() not in ("tú", "tu", "titular", "ella"):
        cand = d
    if not cand:
        cand = DUENOS_CATEGORIA.get((h.get("categoria") or "").lower(), "")
    return cand if cand in agentes else None


def _horas_desde(iso):
    s = str(iso or "")
    d = None
    try:
        d = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
    except Exception:
        try:
            d = datetime.strptime(s[:10], "%Y-%m-%d")
        except Exception:
            return None
    return (datetime.now() - d).total_seconds() / 3600.0


def _tomar_lock(lock, stale_s=120):
    """Lock de productor (mkdir atómico, NO bloqueante): si otra pasada lo tiene, devuelve
    False y salimos sin pisar. Reclama un lock huérfano viejo. Patrón de btp_dispatcher/cost_guard."""
    try:
        os.mkdir(lock)
        return True
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(lock) > stale_s:
                os.rmdir(lock)
                os.mkdir(lock)
                return True
        except Exception:
            pass
        return False
    except Exception:
        return False


def _liberar_lock(lock):
    try:
        os.rmdir(lock)
    except Exception:
        pass


@_serializado
def _sellar_avisos(salidas, ahora):
    """ÚNICO efecto de --ejecutar: sella ultimo_aviso/aviso_estado en los hilos avisados, en UN
    read-modify-write atómico bajo lock (cierra la carrera entre las 4 pasadas). Interno, local;
    nada hacia fuera. Nunca toca hilos clínicos (id cumbre:*, que no viven en seguimiento.json)."""
    por_id = {s["hilo_id"]: s for s in salidas
              if s.get("hilo_id") and not str(s["hilo_id"]).startswith("cumbre:")}
    if not por_id:
        return
    lock = os.path.join(STATE, ".perseguir.lock")
    if not _tomar_lock(lock):
        return
    try:
        seg = load_seguimiento()
        if seg.get("_error"):
            return
        for h in seg.get("hilos", []):
            s = por_id.get(h.get("id"))
            if s:
                h["ultimo_aviso"] = ahora
                h["aviso_estado"] = s["salida"]
        _write_atomic(SEG, seg)
    finally:
        _liberar_lock(lock)


def perseguir(ejecutar=False):
    """SUPERVISA Y AVISA (no ejecuta). Devuelve, por cada hilo que se cae, UNA salida:
      · codigo_rojo — cuello clínico en rojo/bloqueado → el AGENTE lo dispara con criterio.
      · aviso       — es de {{TITULAR}} (gate / la espera ella / sin dueño / clínico).
      · entrega     — hilo NO-clínico con comité dueño real → handoff "a un clic".
      · desbloquea  — espera larga a un tercero → follow-up (borrador).
    Determinista (estado/severidad/enums, NUNCA prosa). --ejecutar solo sella idempotencia."""
    data = recopilar()
    if any("ilegible" in a for a in data.get("avisos", [])):   # fail-closed: no decir "0 caídas"
        return {"error": "registro ilegible — no puedo vigilar",
                "salidas": [{"hilo_id": None, "salida": "aviso", "destino": "titular",
                             "motivo": "seguimiento.json roto: revísalo, no me fío de '0 caídas'"}],
                "en_cooldown": [], "resto_capado": 0, "total_caidos": 0}
    agentes = _agentes_reales()
    ahora = datetime.now().isoformat(timespec="seconds")
    salidas = []
    for i in data["items"]:
        if i.get("estado") == "hecho":
            continue
        sev = i.get("_sev", "info")
        es_cuello = bool(i.get("es_cuello"))
        if sev not in ("roja", "ambar") and not es_cuello:
            continue
        ult = i.get("ultimo_aviso")
        en_cooldown = (sev != "roja" and not es_cuello and ult is not None
                       and (_horas_desde(ult) or 1e9) < COOLDOWN_AVISO_H)
        base = {"hilo_id": i.get("id", ""), "sev": sev, "es_cuello": es_cuello,
                "_ult": ult or "", "en_cooldown": en_cooldown}
        # 1) CÓDIGO ROJO (marca) — cuello clínico en rojo/bloqueado. Nunca en cooldown.
        if es_cuello and (sev == "roja" or i.get("estado") == "bloqueado"):
            base.update(salida="codigo_rojo", destino="agente", en_cooldown=False,
                        motivo="el cuello hacia NED está en rojo/bloqueado")
            salidas.append(base)
            continue
        # 2) Clínico (incl. cuello no-rojo): solo se AVISA, jamás se delega ni escribe.
        if _es_clinico(i):
            base.update(salida="aviso", destino="titular",
                        motivo="toca lo clínico: lo decidís tú y tu equipo")
            salidas.append(base)
            continue
        manos = _manos(i)
        dueno = _resolver_dueno(i, agentes)
        esp = _parse_iso(i.get("esperando_desde"))
        # 3) AVISO a {{TITULAR}} — es suyo (gate / privado-tercero / la espera ella / sin dueño claro).
        if i.get("gate") or i.get("privado") or manos == "tu" or not dueno:
            base.update(salida="aviso", destino="titular",
                        motivo=("lo ves tú (privado, tercero)" if i.get("privado")
                                else "espera tu firma o decisión" if (i.get("gate") or manos == "tu")
                                else "se cae y no tiene dueño claro — lo decides tú"))
        # 4) DESBLOQUEA — lleva mucho esperando a un tercero → follow-up.
        elif manos == "tercero" and esp and (_today() - esp).days >= ESPERA_LARGA:
            base.update(salida="desbloquea", destino=dueno,
                        motivo="lleva %d+ días esperando a un tercero — toca follow-up" % ESPERA_LARGA)
        # 5) ENTREGA a un clic — hilo de un comité que se está cayendo.
        else:
            base.update(salida="entrega", destino=dueno,
                        motivo="es de %s y se está cayendo" % dueno)
        salidas.append(base)

    salidas.sort(key=lambda s: (0 if s["salida"] == "codigo_rojo" else 1,
                                SEV_ORDEN.get(s.get("sev", "info"), 3), s.get("_ult", "")))
    frescas = [s for s in salidas if not s.get("en_cooldown")]
    capadas = frescas[:CAP_AVISOS_PASADA]
    if ejecutar:
        _sellar_avisos(capadas, ahora)
    for s in salidas:
        s.pop("_ult", None)
    return {"salidas": capadas, "en_cooldown": [s for s in salidas if s.get("en_cooldown")],
            "resto_capado": max(0, len(frescas) - len(capadas)), "total_caidos": len(salidas)}


def _render_perseguir(res):
    if res.get("error"):
        return "⚠️ EMPUJE: %s" % res["error"]
    et = {"codigo_rojo": "🔴 CÓDIGO ROJO (el agente lo valora y dispara)",
          "aviso": "🟠 AVISO a ti", "entrega": "🤖 ENTREGA a un clic",
          "desbloquea": "✍️ DESBLOQUEA (follow-up)"}
    L = ["🧭 EMPUJE — Vega supervisa y avisa (no ejecuta) · %d caídas" % res["total_caidos"]]
    for s in res["salidas"]:
        dest = s.get("destino", "")
        suf = (" → " + dest) if dest and dest not in ("titular", "agente") else ""
        L.append("• %s — %s%s\n    %s" % (et.get(s["salida"], s["salida"]),
                                          s.get("hilo_id", "?"), suf, s.get("motivo", "")))
    if res.get("resto_capado"):
        L.append("… +%d frescas capadas esta pasada (rotan por antigüedad)" % res["resto_capado"])
    if res.get("en_cooldown"):
        L.append("(%d en cooldown: ya avisadas hace <%dh)" % (len(res["en_cooldown"]), COOLDOWN_AVISO_H))
    if not res["salidas"] and not res.get("resto_capado"):
        L.append("✅ nada que perseguir ahora mismo.")
    return "\n".join(L)


def main(argv):
    cmd = argv[0] if argv else "revisar"
    if cmd in ("-h", "--help"):
        print(__doc__)
        return 0
    if cmd == "revisar":
        if "--json" in argv:
            print(json.dumps(recopilar(), ensure_ascii=False, indent=2, default=str))
        else:
            print(construir_digest("local"))
        return 0
    if cmd == "add":
        # add --json '<obj>'  o  add  (lee JSON de stdin). Sumidero validado.
        raw = None
        if "--json" in argv:
            raw = argv[argv.index("--json") + 1]
        else:
            raw = sys.stdin.read()
        try:
            obj = json.loads(raw)
        except Exception as e:
            print("JSON inválido: %r" % e, file=sys.stderr)
            return 2
        try:
            hid = add_hilo(obj)
        except Exception as e:
            print("rechazado: %r" % e, file=sys.stderr)
            return 1
        print("ok id=%s" % hid)
        return 0
    if cmd == "tracks":
        print(render_tracks())
        return 0
    if cmd == "sin-ned":
        faltan = sin_objetivo_ned()
        if not faltan:
            print("✅ todos los hilos abiertos declaran su objetivo_ned")
            return 0
        print("%d hilo(s) abierto(s) sin declarar cómo acercan a NED:" % len(faltan))
        for h in faltan:
            print("  · %-40s [%s · origen %s]" % (h.get("id", "?")[:40], h.get("etiqueta", "?"),
                                                 h.get("origen", "?")))
            print("      %s" % (h.get("titulo") or "")[:100])
        return 1
    if cmd == "perseguir":
        # EMPUJE: Vega supervisa y avisa (no ejecuta). --ejecutar solo sella idempotencia.
        res = perseguir(ejecutar=("--ejecutar" in argv))
        if "--json" in argv:
            print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
        else:
            print(_render_perseguir(res))
        return 0
    if cmd == "check":
        # CHECK vivo (mediodía/tarde/noche). Franja explícita o deducida de la hora.
        franjas = {"manana": "mañana", "mañana": "mañana", "mediodia": "mediodía",
                   "mediodía": "mediodía", "tarde": "tarde", "noche": "noche"}
        franja = next((franjas[a] for a in argv[1:] if a in franjas), None)
        if not franja:
            h = datetime.now().hour
            franja = "mañana" if h < 12 else "mediodía" if h < 16 else "tarde" if h < 21 else "noche"
        enviar = "--send" in argv
        texto = construir_check(franja, "telegram" if enviar else "local")
        if enviar:
            try:
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                import salida
            except Exception as e:
                print("no pude importar salida: %r" % e, file=sys.stderr)
                return 1
            res = salida.report_to_titular(texto, dry=("--dry" in argv))
            print(json.dumps(res, ensure_ascii=False))
            return 0
        print(texto)
        return 0
    if cmd == "export":
        # export [--md] → registros DERIVADOS y seguros (allowlist) para el espejo de nube.
        if "--md" in argv:
            print(render_notion_md())
        else:
            print(json.dumps(construir_export("notion"), ensure_ascii=False, indent=2, default=str))
        return 0
    if cmd == "export-investigacion":
        # export-investigacion [--md] → espejo de literatura (allowlist por-ítem, fail-closed).
        if "--md" in argv:
            print(render_investigacion_md())
        else:
            print(json.dumps(construir_export("investigacion"), ensure_ascii=False, indent=2, default=str))
        return 0
    if cmd == "investigacion-add":
        # investigacion-add --json '<obj>'  o  (lee JSON de stdin). Entra SIEMPRE publico:false.
        raw = argv[argv.index("--json") + 1] if "--json" in argv else sys.stdin.read()
        try:
            obj = json.loads(raw)
        except Exception as e:
            print("JSON inválido: %r" % e, file=sys.stderr)
            return 2
        try:
            iid = investigacion_add(obj)
        except Exception as e:
            print("rechazado: %r" % e, file=sys.stderr)
            return 1
        print("ok id=%s (publico:false — marca publico:true a mano para que salga al espejo)" % iid)
        return 0
    if cmd == "investigacion-sync":
        # investigacion-sync --json '[{...}]'  o  (lee array JSON de stdin). Volcado IDEMPOTENTE desde
        # la base curada (filas `Publicar en web`). Reemplaza la fuente entera; el gate de export filtra.
        raw = argv[argv.index("--json") + 1] if "--json" in argv else sys.stdin.read()
        try:
            items = json.loads(raw)
        except Exception as e:
            print("JSON inválido: %r" % e, file=sys.stderr)
            return 2
        try:
            n = investigacion_sync(items)
        except Exception as e:
            print("rechazado: %r" % e, file=sys.stderr)
            return 1
        print("ok: %d paper(s) en la fuente (el gate los re-filtra al exportar)" % n)
        return 0
    if cmd == "--send" or cmd == "send":
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import salida
        except Exception as e:
            print("no pude importar salida: %r" % e, file=sys.stderr)
            return 1
        dry = "--dry" in argv
        texto = construir_digest("telegram")
        res = salida.report_to_titular(texto, dry=dry)
        print(json.dumps(res, ensure_ascii=False))
        return 0
    print("uso: seguimiento.py [revisar [--json] | --send [--dry]]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
