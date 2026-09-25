#!/usr/bin/env python3
"""tools/cola.py — cola persistente del lazo autónomo 24/7 de Polaris (P1).

Un job = un fichero JSON. Su ESTADO es el subdir donde vive; cada transición es un
`os.replace` atómico (rename en el mismo filesystem). El despachador toma UN job a la
vez (lo serializa su lock «un cerebro»). Sin daemon: esto es librería + CLI.

Seguridad (el lazo solo procesa intenciones ESTRUCTURADAS):
  · El job tiene campos en ALLOWLIST cerrada; cualquier extra/tipo inesperado → el job
    va directo a failed/, NUNCA se ejecuta. `intencion` es SIEMPRE un dato/prompt para
    run_agent.sh; aquí nunca se evalúa.
  · Dos motivos de rechazo, ambos fail-closed (a failed/, sin ejecutar): `schema-invalido`
    (tipo malo, valor de enum fuera de dominio, falta obligatorio, intención vacía =
    malformado/plantado, rechazo duro) y `schema-desconocido` (campo top-level no en la
    allowlist = skew de versión: casa base con cola.py vieja; recuperable re-encolando
    tras fusionar). La allowlist es CONSUMER-FIRST: añadir un campo/valor de enum nuevo se
    fusiona a casa base y se recarga el daemon ANTES de que ningún productor lo emita.
  · Carriles de prioridad por prefijo del nombre → alta antes que normal/baja, FIFO
    dentro del carril, sin parsear todos los ficheros.
  · Caducidad: un job datado-perecedero caducado → failed/ (no se ejecuta, no cuenta
    como intento). Dead-letter: a `max_intentos` fallos → failed/ (no re-encola).

Escritura atómica = tmp + os.replace (mismo patrón que tools/salida.py:129, kb.py:77).
Sin dependencias (stdlib).
"""
import json
import os
import sys
import time
import uuid
from datetime import datetime

# La cola viva (pending/processing/done/failed) vive SOLO en casa base (tools/state está gitignored:
# NO viaja al worktree) y SOLO la consume el dispatcher de casa base. Un productor que emita un job
# desde su worktree con el árbol relativo al fichero escribiría en una cola desechable que ningún
# dispatcher lee → el job se PIERDE en silencio. Resolvemos casa base (BTP_REPO o ~/claudecode),
# igual que el dispatcher (REPO=${BTP_REPO:-$HOME/claudecode}) y que seguimiento.py (commit 2fcfad93).
# Esto NO relaja la allowlist consumer-first (CAMPOS/enums/OBLIGATORIOS intactos): el job aterriza en
# la cola de casa base y, si su schema fuese más nuevo que el del consumidor, cae a failed/ como
# `schema-desconocido` (recuperable) en vez de evaporarse. BTP_STATE_DIR aísla tests / se inyecta en el daemon.
REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")  # este fichero vive en tools/
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
QUEUE = os.path.join(STATE, "queue")
SUBDIRS = ("pending", "processing", "done", "failed")

# Cuánto puede envejecer el latido del dispatcher y seguir contando como "vivo" para `_job_en_curso`.
# El refrescador de btp_dispatcher.sh late cada 60 s durante el job, así que 180 s = tres refrescos
# perdidos: holgura para un pico de carga, sin blindar un proceso que ya murió.
LATIDO_VIVO_SEG = 180

RANGO = {"alta": 0, "normal": 1, "baja": 2}
PRIORIDADES = tuple(RANGO.keys())
PERFILES = ("privileged", "quarantine")

# Allowlist CERRADA de campos del job. Nada fuera de aquí es válido (anti-inyección).
CAMPOS = {
    "id": str, "prioridad": str, "intencion": str, "agente": (str, type(None)),
    "modelo": (str, type(None)), "perfil": str, "intentos": int, "max_intentos": int,
    "creado": str, "expira": (str, type(None)), "procedencia": str,
    "tope_job_usd": (int, float, type(None)), "ultimo_error": (str, type(None)),
    "terminado": (str, type(None)), "coste_usd": (int, float, type(None)),
    "tipo": str,   # "exec" (ejecutar la intención) | "triage" (cuarentena → estructura)
    # Freno de criticidad (seguridad clínica): un job 'critico' (clínico/sensible) NO se sirve con
    # un cerebro flojo — PARA. Opcional, default 'rutina' (jobs viejos sin el campo = rutina, igual
    # que hoy). 'rutina' degrada-y-sirve como siempre; SOLO 'critico' puede bloquear el servicio.
    "criticidad": str,
    # Fase 2 (constelación): trazabilidad por caja + anti fork-bomb. Opcionales (jobs
    # viejos sin estos campos siguen válidos); enqueue los rellena.
    "caja_id": (str, type(None)),   # qué caja originó el job (auditoría de gasto por caja)
    "linaje": (str, type(None)),    # cadena de ancestros "id>id>id" (tope por linaje)
    "profundidad": int,             # nº de ancestros: un job que engendra jobs sin fin = bomba
    # Diario entre vueltas (25-jul-26): qué se intentó y con qué error, para que el reintento NO
    # repita el camino que ya falló. Lo escribe SOLO `mark_failed` (ningún productor externo), así
    # que no rompe el contrato consumer-first: hasta que esta versión esté en casa base, nadie lo
    # emite. Lista de {"intento","error","ts"}, tope HISTORIAL_TOPE.
    "historial": (list, type(None)),
    # Presupuesto de turnos del job (31-jul-26). Nace SIEMPRE ausente: ningún productor lo emite,
    # igual que `historial`, así que no rompe el contrato consumer-first. Lo escribe SOLO
    # `mark_failed` cuando el job muere por `error_max_turns`: en vez de reintentar tres veces el
    # mismo presupuesto (garantizado a agotarse otra vez), sube de marcha UNA vez y lo reintenta
    # con el doble. Si con el tope tampoco cabe, entonces sí es dead-letter y se cuenta con su
    # motivo. Regla suya: el coste no corta el camino, se baja de marcha o se pide aprobación.
    "turnos": (int, type(None)),
    # Qué tiene que EXISTIR para que el job se pueda cerrar (20-sep-2026). Nace ausente: hasta que
    # esta versión esté en casa base ningún productor lo emite (contrato consumer-first). El
    # dispatcher cerraba mirando solo el código de salida, así que un job que chocó contra el muro
    # y respondió en prosa se marcaba hecho: 6,03 USD por un arreglo de `tools/seguimiento.py` que
    # nunca existió. Vocabulario CERRADO, dos formas: {"tipo":"ruta","ruta":X} y
    # {"tipo":"deuda","clave":X}. Lo evalúa `tools/prueba_entregable.py`.
    "prueba": (dict, type(None)),
}
PRUEBA_TIPOS = ("ruta", "deuda")
HISTORIAL_TOPE = 5    # el diario guarda los últimos N intentos (basta para no repetirse)
TURNOS_TOPE = 60      # el mismo techo que un job crítico: más que eso se decide a mano

TIPOS = ("exec", "triage")
CRITICIDADES = ("rutina", "critico")
CRITICIDAD_DEFAULT = "rutina"

# Procedencias que el sistema se AUTO-adjudica: nadie las aprobó una por una. Llevan techo de
# gasto por defecto (31-jul-26, tras un job de radar-taller que quemó 2,53 USD en dos intentos
# fallidos sin producir un commit, con tope_job_usd=null). Un tope explícito en la llamada manda
# siempre sobre esto — subir el techo es una decisión, no un descuido.
AUTO_PROCEDENCIAS = ("radar-taller", "healthcheck:", "auto-mejora")
TOPE_AUTO_USD = 1.00
OBLIGATORIOS = ("id", "prioridad", "intencion", "perfil", "intentos", "max_intentos",
                "creado", "procedencia", "tipo")
MAX_INTENTOS_TOPE = 10

# Marcador del motivo cuando el rechazo es por un campo TOP-LEVEL desconocido. Eso es skew
# de versión (casa base con cola.py vieja que no conoce un campo nuevo), NO un job
# malformado/plantado. dequeue() lo etiqueta como `schema-desconocido` (recuperable:
# re-encolar tras fusionar) en vez de `schema-invalido`. Sigue siendo fail-closed: el job
# va a failed/ y NUNCA se ejecuta — un consumidor que no entiende un campo podría estar
# ignorando un FRENO, así que rechazar es lo correcto, solo que de forma diagnosticable.
CAMPO_DESCONOCIDO = "campo-desconocido"
MAX_PROFUNDIDAD = 5   # un job descendiente de >5 ancestros → failed (corta la bomba de fork)


def _now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _ensure_dirs():
    for d in SUBDIRS:
        os.makedirs(os.path.join(QUEUE, d), mode=0o700, exist_ok=True)


def _write_atomic(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _validate(job):
    """Devuelve None si el job cumple la allowlist; si no, el motivo (string)."""
    if not isinstance(job, dict):
        return "no es un objeto"
    for k in job:
        if k not in CAMPOS:
            return "%s: %s" % (CAMPO_DESCONOCIDO, k)   # skew de versión, no malformado
        if not isinstance(job[k], CAMPOS[k]):
            return "tipo invalido en %s" % k
    for k in OBLIGATORIOS:
        if k not in job:
            return "falta campo obligatorio: %s" % k
    if job["prioridad"] not in RANGO:
        return "prioridad invalida: %r" % job["prioridad"]
    if job["perfil"] not in PERFILES:
        return "perfil invalido: %r" % job["perfil"]
    if job["tipo"] not in TIPOS:
        return "tipo invalido: %r" % job["tipo"]   # un .json plantado con tipo basura no corre como exec
    # criticidad: opcional, pero si está debe ser válida (un .json plantado no cuela un valor raro).
    if "criticidad" in job and job["criticidad"] not in CRITICIDADES:
        return "criticidad invalida: %r" % job["criticidad"]
    if not (0 <= int(job["max_intentos"]) <= MAX_INTENTOS_TOPE):
        return "max_intentos fuera de rango: %r" % job["max_intentos"]
    if not str(job["intencion"]).strip():
        return "intencion vacia"
    if job.get("prueba") is not None:
        motivo = _validar_prueba(job["prueba"])
        if motivo:
            return motivo
    return None


# Extensiones que el muro NO deja escribir al lazo, en NINGÚN perfil
# (`.claude/hooks/muro_guard.py:888`, constante WRITE_EXEC_EXT). Prometer un entregable con una de
# estas extensiones es prometer lo imposible: el job no puede entregarlo por construcción, así que
# se rechaza AL ENCOLAR y no después de pagarlo. `tests/test_cola.py` compara las dos listas para
# que no se desincronicen en silencio.
PRUEBA_EXT_PROHIBIDAS = (".py", ".sh", ".bash", ".zsh", ".command", ".pl", ".rb", ".js",
                         ".mjs", ".cjs", ".ts", ".php", ".rs", ".go", ".c", ".cc", ".cpp",
                         ".m", ".swift", ".applescript", ".scpt", ".plist", ".bat", ".ps1",
                         ".so", ".dylib", ".dll", ".pyc", ".pth")


def _validar_prueba(p):
    """None si la prueba es válida; si no, el motivo. Fail-closed: lo que no entiendo, se rechaza."""
    if not isinstance(p, dict):
        return "prueba invalida: no es un objeto"
    tipo = p.get("tipo")
    if tipo not in PRUEBA_TIPOS:
        return "prueba invalida: tipo %r" % tipo
    if tipo == "ruta":
        ruta = p.get("ruta")
        if not isinstance(ruta, str) or not ruta.strip():
            return "prueba invalida: ruta vacia"
        if ruta.startswith("/") or ".." in ruta.split("/"):
            return "prueba invalida: la ruta va relativa al repo (%r)" % ruta
        if ruta.lower().endswith(PRUEBA_EXT_PROHIBIDAS):
            return "prueba imposible: el lazo no puede escribir %r (muro)" % ruta
    elif tipo == "deuda":
        clave = p.get("clave")
        if not isinstance(clave, str) or not clave.strip():
            return "prueba invalida: clave de deuda vacia"
    sobra = set(p) - {"tipo", "ruta", "clave"}
    if sobra:
        return "prueba invalida: campos desconocidos %s" % sorted(sobra)
    return None


def _fname(job, ts_key):
    # ts_key con microsegundos → FIFO real dentro del carril aunque haya varios enqueue
    # en el mismo segundo (a resolución de segundos empataban y desempataba el id azar).
    return "%d-%s-%s.json" % (RANGO[job["prioridad"]], ts_key, job["id"])


def _expired(job):
    """¿El job ya no vale? Fail-closed: fecha ILEGIBLE = caducado.

    Acepta las dos formas de escribir una caducidad, y esto NO es cosmético (30-jul-26): hasta hoy
    solo se parseaba `%Y-%m-%dT%H:%M:%S`, así que un `expira` con fecha suelta (`2026-07-31`) caía
    al `except` y se leía como CADUCADO en el acto. `healthcheck.py` emitía justo eso al
    auto-encolarse un arreglo, con lo que **todos** sus jobs de auto-reparación morían con
    `intentos=0` en el mismo instante de nacer, aunque les quedaran dos días. Y el detector de
    «jobs caídos» encolaba otro job igual para investigarlo, que moría igual: la auto-reparación
    fabricando su propia deuda en bucle. 6 encargos muertos y 146 re-detecciones en 3 días.

    Una fecha suelta se lee como el FINAL de ese día (`2026-07-31` = vale todo el 31), que es lo que
    significa en castellano y lo que asumía quien la escribió.
    """
    exp = job.get("expira")
    if not exp:
        return False
    for fmt, fin_de_dia in (("%Y-%m-%dT%H:%M:%S", False), ("%Y-%m-%d", True)):
        try:
            t = datetime.strptime(exp, fmt)
        except (ValueError, TypeError):
            continue
        if fin_de_dia:
            t = t.replace(hour=23, minute=59, second=59)
        return t < datetime.now()
    return True  # fecha ilegible de verdad = caducado (fail-closed)


def _find_by_id(subdir, job_id):
    d = os.path.join(QUEUE, subdir)
    if not os.path.isdir(d):
        return None
    for f in os.listdir(d):
        if f.endswith("-%s.json" % job_id):
            return os.path.join(d, f)
    return None


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _to_failed(path, job, motivo):
    """Mueve un fichero a failed/ marcando el motivo (best-effort, atómico)."""
    _ensure_dirs()
    try:
        if isinstance(job, dict):
            job = dict(job)
            job["ultimo_error"] = motivo
            job["terminado"] = _now_iso()
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(job, f, ensure_ascii=False, indent=2)
            os.chmod(tmp, 0o600)   # mismo permiso que _write_atomic; no dejar 0644 por defecto
            os.replace(tmp, path)
    except Exception:
        pass
    dest = os.path.join(QUEUE, "failed", os.path.basename(path))
    try:
        os.replace(path, dest)
    except FileNotFoundError:
        pass
    return dest


# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────
def enqueue(intencion, *, prioridad="normal", agente=None, modelo=None,
            perfil="privileged", procedencia="desconocida", expira=None,
            max_intentos=3, tope_job_usd=None, tipo="exec",
            caja_id=None, profundidad=0, linaje=None, criticidad=CRITICIDAD_DEFAULT,
            prueba=None):
    """Crea un job válido y lo deja en pending/. Devuelve el id."""
    _ensure_dirs()
    if tipo not in TIPOS:
        raise ValueError("tipo invalido: %r" % tipo)
    if criticidad not in CRITICIDADES:
        raise ValueError("criticidad invalida: %r" % criticidad)
    # La intención NO puede ser el valor de un flag (20-sep-26). Cuando el CLI cogía args[-1],
    # una llamada acabada en `--tipo exec` encolaba la palabra «exec» y el lazo la ejecutaba: dos
    # jobs reales, 0,60 USD por hacer nada. El parseo ya está arreglado (`_posicional`); esto es
    # la red de abajo, para el que llame a enqueue() desde código. No se filtra por longitud: un
    # mensaje corto de Telegram («¿cómo va?») es una intención legítima.
    _txt = str(intencion).strip()
    if not _txt:
        raise ValueError("intencion vacia")
    if _txt.lower() in set(TIPOS) | set(PERFILES) | set(PRIORIDADES) | set(CRITICIDADES):
        raise ValueError(
            "la intencion es el valor de un flag (%r): se perdio el texto al encolar" % _txt)
    # Techo por defecto para lo que se AUTO-encola sin que {{TITULAR}} lo vea (31-jul-26). Un job de
    # `radar-taller` (mejoras del taller que el bucle se auto-adjudica por ser de bajo riesgo)
    # llevaba gastados 2,53 USD en dos intentos fallidos sin producir un solo commit, con
    # `tope_job_usd=null`, o sea sin techo. Lo que nadie aprobó no puede gastar sin límite: si el
    # trabajo de verdad vale más, que lo pida. Lo CLÍNICO y lo que pide una persona no pasan por
    # aquí (llevan su propia procedencia y su tope explícito manda siempre).
    if tope_job_usd is None and str(procedencia).startswith(tuple(AUTO_PROCEDENCIAS)):
        tope_job_usd = TOPE_AUTO_USD
    job = {
        "id": uuid.uuid4().hex[:10], "prioridad": prioridad,
        "intencion": str(intencion), "agente": agente, "modelo": modelo,
        "perfil": perfil, "intentos": 0, "max_intentos": int(max_intentos),
        "creado": _now_iso(), "expira": expira, "procedencia": str(procedencia),
        "tope_job_usd": tope_job_usd, "ultimo_error": None,
        "terminado": None, "coste_usd": None, "tipo": tipo,
        "criticidad": criticidad,
        "caja_id": caja_id, "linaje": linaje, "profundidad": int(profundidad),
    }
    if prueba is not None:
        job["prueba"] = prueba
    motivo = _validate(job)
    if motivo:
        raise ValueError("job invalido: %s" % motivo)
    ts_key = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    _write_atomic(os.path.join(QUEUE, "pending", _fname(job, ts_key)), job)
    return job["id"]


def dequeue():
    """Toma el job de mayor prioridad NO caducado, lo mueve pending→processing de forma
    atómica, y lo devuelve (con la clave interna '_path'). None si no hay ninguno.
    Los caducados o con schema inválido se apartan a failed/ por el camino."""
    _ensure_dirs()
    pend = os.path.join(QUEUE, "pending")
    for f in sorted(os.listdir(pend)):
        if not f.endswith(".json"):
            continue
        src = os.path.join(pend, f)
        try:
            job = _load(src)
        except Exception:
            _to_failed(src, None, "json-ilegible")
            continue
        motivo = _validate(job)
        if motivo:
            # Campo top-level desconocido = skew de versión (recuperable: re-encolar tras
            # fusionar+recargar casa base). Todo lo demás (tipo malo, valor de enum fuera de
            # dominio, falta obligatorio, intención vacía) = malformado/plantado → duro.
            if motivo.startswith(CAMPO_DESCONOCIDO + ":"):
                campo = motivo.split(":", 1)[1].strip()
                _to_failed(src, job, "schema-desconocido: %s (¿casa base sin fusionar/"
                           "recargar? re-encolar tras fusionar)" % campo)
            else:
                _to_failed(src, job, "schema-invalido: %s" % motivo)
            continue
        if _expired(job):
            _to_failed(src, job, "caducado")
            continue
        if int(job.get("profundidad", 0)) > MAX_PROFUNDIDAD:
            _to_failed(src, job, "profundidad-excedida (corta la bomba de fork)")
            continue
        dest = os.path.join(QUEUE, "processing", f)
        try:
            os.replace(src, dest)  # claim atómico: si otro lo tomó, FileNotFound → siguiente
        except FileNotFoundError:
            continue
        job["_path"] = dest
        return job
    return None


def mark_done(job, coste_usd=None):
    path = job.get("_path") or _find_by_id("processing", job["id"])
    if not path or not os.path.exists(path):
        return False
    j = _load(path)
    j["terminado"] = _now_iso()
    j["coste_usd"] = coste_usd
    j["ultimo_error"] = None
    dest = os.path.join(QUEUE, "done", os.path.basename(path))
    _write_atomic(path, j)
    os.replace(path, dest)
    return True


def mark_failed(job, error):
    """Incrementa intentos; si llega a max_intentos → dead-letter (failed/), si no →
    vuelve a pending/ para que el despachador aplique back-off.

    Fase 3b — reintentos por severidad: si el error se clasifica como CONFIG o DEGRADADO
    (permanente: schema inválido, import error, saldo agotado…), no malgastamos intentos —
    va directo a dead-letter aunque queden intentos. TRANSITORIO sigue reintentando normal.
    La clasificación es fail-soft: si errores.py no está disponible, se comporta como antes.
    """
    path = job.get("_path") or _find_by_id("processing", job["id"])
    if not path or not os.path.exists(path):
        return "perdido"
    j = _load(path)
    j["intentos"] = int(j.get("intentos", 0)) + 1
    error_str = str(error)[:500]
    j["ultimo_error"] = error_str
    # Diario entre vueltas: el reintento tiene que SABER qué se probó y cómo falló, o repite el
    # mismo camino hasta el dead-letter (idea de x:2069713940989231590, 25-jul-26).
    hist = j.get("historial") or []
    if not isinstance(hist, list):
        hist = []
    hist.append({"intento": j["intentos"], "error": error_str[:300], "ts": _now_iso()})
    j["historial"] = hist[-HISTORIAL_TOPE:]

    # Turnos agotados: SUBIR de marcha una vez, no repetir el mismo presupuesto (31-jul-26).
    # `error_max_turns` no es que el trabajo esté mal: es que no cabía. Reintentarlo idéntico
    # vuelve a agotarlo — medido: 3 intentos, 2,34 USD, mismo final. Se dobla el presupuesto (25
    # por defecto → 50, tope TURNOS_TOPE) y NO se gasta intento, porque la vuelta anterior no
    # fracasó por el trabajo sino por el techo. Una sola escalada: si con el tope tampoco cabe,
    # sigue el camino normal y acaba en dead-letter con su motivo escrito.
    if "error_max_turns" in error_str and int(j.get("turnos") or 0) < TURNOS_TOPE:
        j["turnos"] = min(int(j.get("turnos") or 25) * 2, TURNOS_TOPE)
        j["intentos"] = int(j["intentos"]) - 1          # el techo no cuenta como intento fallido
        _write_atomic(path, j)
        os.replace(path, os.path.join(QUEUE, "pending", os.path.basename(path)))
        return "reencolado con %d turnos" % j["turnos"]

    # Severidad del error: CONFIG/DEGRADADO → no reintentar (ya sabemos que no sirve).
    _no_reintentar = False
    try:
        import errores as _err
        sev = _err.clasificar(error if isinstance(error, BaseException) else Exception(error_str))
        if sev in (_err.CONFIG, _err.DEGRADADO):
            _no_reintentar = True
    except Exception:
        pass   # fail-soft: si errores.py no está, comportamiento original intacto

    if _no_reintentar or j["intentos"] >= int(j.get("max_intentos", 3)):
        j["terminado"] = _now_iso()
        _write_atomic(path, j)
        os.replace(path, os.path.join(QUEUE, "failed", os.path.basename(path)))
        return "dead-letter"
    _write_atomic(path, j)
    os.replace(path, os.path.join(QUEUE, "pending", os.path.basename(path)))
    return "reencolado"


def diario(job):
    """El diario del job en texto, para METERLO en el prompt del reintento. "" si es el 1er intento.

    Por qué (25-jul-2026, idea de `x:2069713940989231590`): un loop sin memoria entre vueltas
    reintenta el mismo arreglo que ya falló y quema intentos hasta el dead-letter. `mark_failed`
    ya guardaba el error, pero NADIE se lo contaba al reintento: el dispatcher montaba el prompt
    con contexto + intención y nada más. Esto es la mitad que faltaba.
    """
    hist = (job or {}).get("historial") or []
    if not isinstance(hist, list) or not hist:
        return ""
    lineas = ["⚠️ ESTE TRABAJO YA FALLÓ %d vez/veces. Lo que se intentó y cómo falló:" % len(hist)]
    for h in hist:
        if not isinstance(h, dict):
            continue
        lineas.append("  · intento %s (%s): %s" % (h.get("intento", "?"),
                                                   (h.get("ts") or "")[:16],
                                                   (h.get("error") or "")[:300]))
    lineas.append("NO repitas el mismo camino: si el error apunta a algo permanente (config, "
                  "permiso, falta un dato), dilo y para en vez de volver a intentarlo igual.")
    return "\n".join(lineas)


def requeue(job_id):
    """Devuelve un job de processing/ a pending/ sin contar intento (p.ej. abort por HALT)."""
    path = _find_by_id("processing", job_id)
    if not path:
        return False
    os.replace(path, os.path.join(QUEUE, "pending", os.path.basename(path)))
    return True


def _job_en_curso(job_id):
    """True si el dispatcher dice estar ejecutando AHORA ese job: su latido lo nombra en `last_job`,
    es fresco (< LATIDO_VIVO_SEG) y su PID sigue vivo. Las tres condiciones, no una.

    Existe porque `reap_stuck` rescataba por mtime a secas: un job legítimamente largo se declaraba
    atascado mientras el agente seguía corriendo, y el rescate abría la puerta a ejecutarlo dos veces
    (y a cobrarlo dos veces). El latido pasó a refrescarse DURANTE el job (btp_dispatcher.sh), así
    que ya hay una señal fiable que distinguir «tarda» de «murió» — esto la usa.

    Fail-CLOSED hacia el rescate: si no hay latido, o no se puede leer, o el PID está muerto,
    devuelve False y el job SÍ se rescata. Un crash real no puede quedarse colgado por una lectura
    que falla."""
    if not job_id:
        return False
    hb = os.path.join(STATE, "dispatcher", "heartbeat.json")
    try:
        with open(hb, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("last_job") != job_id:
            return False
        if time.time() - os.path.getmtime(hb) > LATIDO_VIVO_SEG:
            return False   # latido rancio: el dispatcher no está refrescando → murió a mitad
        os.kill(int(d["pid"]), 0)   # señal 0 = ¿existe el proceso? No lo toca
        return True
    except Exception:
        return False


def reap_stuck(max_edad_seg=3600):
    """Jobs en processing/ más viejos que max_edad_seg (crash a mitad) → mark_failed.

    Salvo que el dispatcher lo siga ejecutando de verdad (`_job_en_curso`): el mtime por sí solo no
    distingue «murió a mitad» de «tarda mucho», y confundirlos cuesta una doble ejecución."""
    _ensure_dirs()
    proc = os.path.join(QUEUE, "processing")
    n = 0
    for f in list(os.listdir(proc)):
        p = os.path.join(proc, f)
        try:
            if time.time() - os.path.getmtime(p) > max_edad_seg:
                j = _load(p)
                if _job_en_curso(j.get("id")):
                    continue
                j["_path"] = p
                mark_failed(j, "atascado en processing > %ds" % max_edad_seg)
                n += 1
        except Exception:
            continue
    return n


def get_status():
    _ensure_dirs()
    st = {}
    for d in SUBDIRS:
        st[d] = sum(1 for f in os.listdir(os.path.join(QUEUE, d)) if f.endswith(".json"))
    by = {p: 0 for p in PRIORIDADES}
    for f in os.listdir(os.path.join(QUEUE, "pending")):
        if f and f[0].isdigit():
            for p, r in RANGO.items():
                if f.startswith("%d-" % r):
                    by[p] += 1
    st["pending_por_prioridad"] = by
    return st


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def _arg(args, name, default=None):
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else default


# Flags de `enqueue` que llevan valor detrás. Hace falta para saber qué argumento es la INTENCIÓN
# y cuál es el valor de un flag: sin esto, `enqueue "…" --tipo exec` encolaba la palabra "exec".
FLAGS_CON_VALOR = ("--prioridad", "--perfil", "--agente", "--modelo", "--procedencia",
                   "--expira", "--tipo", "--criticidad", "--max-intentos", "--prueba")


def _posicional(args):
    """El primer argumento que no es ni un flag ni el valor de un flag.

    20-sep-2026: `enqueue` cogía `args[-1]`, así que cualquier llamada que terminara en flag
    —justo como la documenta `.claude/agents/auto-mejora.md`— perdía la intención y encolaba el
    valor del último flag. Dos jobs reales se ejecutaron con la intención literal «exec» y se
    pagaron: 0,24 y 0,36 USD por hacer nada."""
    saltar = False
    for x in args:
        if saltar:
            saltar = False
            continue
        if x.startswith("--"):
            saltar = x in FLAGS_CON_VALOR and "=" not in x
            continue
        return x
    return ""


def recover(*, patron=None, desde=None, max_jobs=50, dry_run=False):
    """Recupera jobs de failed/ re-encolándolos en pending/, de forma determinista e idempotente.

    Operaciones soportadas (las dos se pueden combinar):
      1. Re-encolar failed/ por patrón de texto en `ultimo_error` y/o por fecha mínima.
      2. Rescatar jobs colgados en processing/ usando reap_stuck (con la edad mínima indicada).

    Parámetros:
      patron   — str (substring) en `ultimo_error` del job que lo hace candidato. None = todos
                 EXCEPTO los de schema-invalido (permanentes, no recuperables).
      desde    — ISO date "YYYY-MM-DD" de corte: solo jobs creados a partir de esa fecha.
      max_jobs — tope de jobs a re-encolar en una sola llamada (para no inundar la cola).
      dry_run  — True → solo lista lo recuperable sin mover nada.

    Devuelve {"reencolados": [...ids], "rescatados": int, "ignorados": int, "dry_run": bool}.
    Determinista e idempotente: re-encolar un job que ya está en pending/ es un no-op (el
    fichero no existe en failed/ dos veces).
    """
    _ensure_dirs()
    failed_dir = os.path.join(QUEUE, "failed")
    pending_dir = os.path.join(QUEUE, "pending")
    reencolados, ignorados = [], 0

    # Candidatos: todos los .json de failed/, ordenados por nombre (FIFO natural del carril).
    candidatos = sorted(f for f in os.listdir(failed_dir) if f.endswith(".json"))
    for fname in candidatos:
        if len(reencolados) >= max_jobs:
            break
        src = os.path.join(failed_dir, fname)
        try:
            job = _load(src)
        except Exception:
            ignorados += 1
            continue

        ultimo = str(job.get("ultimo_error") or "")

        # NUNCA re-encolar schema-invalido (malformado/plantado — permanente).
        if "schema-invalido" in ultimo:
            ignorados += 1
            continue

        # Filtro por patrón de error (si se especificó).
        if patron and patron not in ultimo:
            ignorados += 1
            continue

        # Filtro por fecha mínima de creación (si se especificó).
        if desde:
            creado = str(job.get("creado") or "")[:10]
            if creado and creado < desde:
                ignorados += 1
                continue

        if dry_run:
            reencolados.append(job.get("id", fname))
            continue

        # Limpia la marca de terminado/error para que el consumidor no lo trate como muerto.
        j2 = dict(job)
        j2.pop("terminado", None)
        j2["ultimo_error"] = None
        dest = os.path.join(pending_dir, fname)
        try:
            _write_atomic(src, j2)   # actualiza el fichero in-place (mismo path aún en failed/)
            os.replace(src, dest)    # mueve atómicamente a pending/
            reencolados.append(j2.get("id", fname))
        except Exception:
            ignorados += 1

    # Además: rescatar processing/ colgado (reap_stuck con la edad mínima por defecto).
    rescatados = 0 if dry_run else reap_stuck()

    return {
        "reencolados": reencolados,
        "rescatados": rescatados,
        "ignorados": ignorados,
        "dry_run": dry_run,
    }


def list_recoverable(*, patron=None, desde=None):
    """Versión conveniente de recover(dry_run=True): devuelve la lista de jobs recuperables.
    Útil para el Observatorio y el subcomando 'recover --list'."""
    return recover(patron=patron, desde=desde, dry_run=True)


def main(argv):
    if not argv:
        print("uso: cola.py [enqueue|dequeue|status|list <subdir>|reap|requeue|mark-done|mark-failed|recover]")
        return 2
    cmd = argv[0]
    a = argv[1:]
    if cmd == "enqueue":
        texto = _posicional(a)
        if not texto:
            print("uso: cola.py enqueue [--prioridad alta] [--procedencia x] [--perfil quarantine] \"<intencion>\"")
            return 2
        prueba_cli = _arg(a, "--prueba")
        if prueba_cli:
            try:
                prueba_cli = json.loads(prueba_cli)
            except Exception:
                print("--prueba tiene que ser JSON: '{\"tipo\":\"ruta\",\"ruta\":\"…\"}'")
                return 2
        jid = enqueue(texto, prueba=prueba_cli or None,
                      prioridad=_arg(a, "--prioridad", "normal"),
                      perfil=_arg(a, "--perfil", "privileged"),
                      agente=_arg(a, "--agente"), modelo=_arg(a, "--modelo"),
                      procedencia=_arg(a, "--procedencia", "cli"),
                      expira=_arg(a, "--expira"), tipo=_arg(a, "--tipo", "exec"),
                      criticidad=_arg(a, "--criticidad", CRITICIDAD_DEFAULT),
                      max_intentos=int(_arg(a, "--max-intentos", "3")))
        print(jid)
        return 0
    if cmd == "dequeue":
        job = dequeue()
        if not job:
            return 1
        print(json.dumps({k: v for k, v in job.items() if k != "_path"}, ensure_ascii=False))
        return 0
    if cmd == "diario":
        # El job entra por stdin (el dispatcher ya lo tiene del dequeue). Sale el texto del
        # diario de intentos, o nada si es el primer intento.
        try:
            job = json.loads(sys.stdin.read() or "{}")
        except Exception:
            return 1
        txt = diario(job if isinstance(job, dict) else {})
        if txt:
            print(txt)
        return 0
    if cmd == "status":
        print(json.dumps(get_status(), ensure_ascii=False, indent=2))
        return 0
    if cmd == "list":
        sub = a[0] if a else "pending"
        _ensure_dirs()
        for f in sorted(os.listdir(os.path.join(QUEUE, sub))):
            print(f)
        return 0
    if cmd == "reap":
        print("reaped:", reap_stuck(int(_arg(a, "--max-edad", "3600"))))
        return 0
    if cmd == "requeue":
        print("ok" if requeue(_arg(a, "--id")) else "no encontrado")
        return 0
    if cmd in ("mark-done", "mark-failed"):
        jid = _arg(a, "--id")
        path = _find_by_id("processing", jid)
        if not path:
            print("no encontrado en processing:", jid)
            return 1
        job = _load(path)
        job["_path"] = path
        if cmd == "mark-done":
            mark_done(job, coste_usd=float(_arg(a, "--coste", "0") or 0))
        else:
            mark_failed(job, _arg(a, "--error", "?"))
        return 0
    if cmd == "recover":
        # cola.py recover [--patron <texto>] [--desde YYYY-MM-DD] [--max N] [--dry-run]
        patron = _arg(a, "--patron")
        desde = _arg(a, "--desde")
        max_j = int(_arg(a, "--max", "50"))
        dry = "--dry-run" in a or "--dry" in a
        listar = "--list" in a
        if listar:
            res = list_recoverable(patron=patron, desde=desde)
            print("recuperables: %d" % len(res["reencolados"]))
            for jid in res["reencolados"]:
                print("  " + jid)
            return 0
        res = recover(patron=patron, desde=desde, max_jobs=max_j, dry_run=dry)
        if dry:
            print("dry-run: %d recuperables, %d colgados en processing/, %d ignorados" % (
                len(res["reencolados"]), res.get("rescatados", 0), res["ignorados"]))
            for jid in res["reencolados"]:
                print("  " + jid)
        else:
            print("reencolados: %d · rescatados: %d · ignorados: %d" % (
                len(res["reencolados"]), res["rescatados"], res["ignorados"]))
        return 0
    print("comando desconocido:", cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
