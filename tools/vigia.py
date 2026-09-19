#!/usr/bin/env python3
"""tools/vigia.py — EL VIGÍA: caza cuando el sistema NO respondió bien y se arregla/escala solo.

Plan: ~/.claude/plans/polished-swimming-deer.md. Petición de {{TITULAR}} (24/6): "ni tú ni yo podemos
vigilar cada mensaje; hoy necesitamos que vayas arreglándote conforme detectas que no respondes bien".

QUÉ HACE (determinista, sin LLM, $0):
  · DETECTA firmas de "no respondí bien" que ya dejan huella en logs/cola/estado:
      1. respuesta FLOJA del respondedor (no-respuesta o tells "no aparece en los datos"…) — la marca
         el propio `responder_con_datos` en state/vigia/respuestas-*.jsonl.
      2. ACCIÓN en BUCLE: un job de Telegram aplazado una y otra vez (rc 75) que no avanza.
      3. BOT inestable: reinicios en bucle (muchos "daemon arriba" seguidos).
      4. ERRORES nuevos en los logs del bot/dispatcher (Traceback/ImportError/…).
  · CLASIFICA cada anomalía: auto_arreglable | necesita_codigo | informar.
  · AUTO-ARREGLA solo lo SEGURO y reversible (de momento: retirar un job trivial/obsoleto en bucle,
    p.ej. un "y" suelto). NUNCA toca código ni manda nada hacia fuera (el muro: solo lee + estado).
  · REGISTRA todo dedup en state/vigia/anomalias.jsonl → lo lee Claude en su bucle y arregla el código.
  · AVISA a {{TITULAR}} en llano por el choke-point (salida.report_to_titular, respeta HALT) cuando hay una
    anomalía que no auto-resolvió, con anti-spam de 12h (un problema nuevo rompe el silencio; el mismo
    se recuerda como mucho cada 12h). Sin esto el jsonl no lo leía nadie y el bot caído pasaba inadvertido.

NO hace: no juzga la calidad fina de una respuesta plausible (eso sería un juez-LLM, fase 2); no
parchea código solo (un daemon no debe). Cierra el hueco "nadie lo vio a tiempo".

CLI:
  python3 tools/vigia.py run         # un ciclo: detecta, auto-arregla lo seguro, registra
  python3 tools/vigia.py estado      # resumen de las anomalías abiertas
"""
import glob
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
VIGIA_DIR = os.path.join(STATE, "vigia")
LOGS = os.environ.get("BTP_LOGS_DIR") or os.path.join(REPO, "tools", "launchd", "logs")
QUEUE = os.path.join(STATE, "queue")
ANOMALIAS = os.path.join(VIGIA_DIR, "anomalias.jsonl")
SEEN = os.path.join(VIGIA_DIR, "seen.json")
ALERTA_STATE = os.path.join(VIGIA_DIR, "last_alert.json")
LAST_RUN = os.path.join(VIGIA_DIR, "last_run.json")   # B: latido de "el vigía corrió" (lo lee healthcheck)
HC_LAST_CHECK = os.path.join(STATE, "healthcheck", "last_check.json")  # B: lo escribe healthcheck cada ciclo
HC_STALE_MIN = int(os.environ.get("BTP_HC_STALE_MIN", "70"))   # healthcheck corre cada 30 min → stale > 70
ALERTA_COOLDOWN_H = 12   # un problema persistente se re-avisa como mucho cada 12h (anti-spam, igual que healthcheck)
AUTOFIX = os.path.join(VIGIA_DIR, "autofix.json")   # presupuesto de auto-arreglos con acción (kickstart), NO detección
# Auto-curación del healthcheck (pieza 2): intenta UN kickstart local por ventana y escala a {{TITULAR}} solo si no revive.
HC_KICK_MAX = int(os.environ.get("BTP_HC_KICK_MAX", "1"))       # nº de kickstarts por ventana antes de escalar a humano
HC_KICK_WINDOW_H = int(os.environ.get("BTP_HC_KICK_WINDOW_H", "6"))
HC_BOOT_GRACE_MIN = int(os.environ.get("BTP_HC_BOOT_GRACE_MIN", "35"))  # recién arrancado: healthcheck aún no tocó → no es fallo
HC_LABEL = "com.btp.healthcheck"

# Umbrales (env-ajustables; defaults conservadores para no dar falsos positivos).
WINDOW_MIN = int(os.environ.get("BTP_VIGIA_WINDOW_MIN", "45"))      # frescura de respuestas flojas
BUCLE_MIN = int(os.environ.get("BTP_VIGIA_BUCLE_MIN", "4"))         # aplazos del MISMO job = bucle
CRASH_MIN = int(os.environ.get("BTP_VIGIA_CRASH_MIN", "6"))         # "daemon arriba" en la cola = churn
TAIL_N = int(os.environ.get("BTP_VIGIA_TAIL", "200"))
SEEN_TTL_H = 24

_ERR_TELLS = ("Traceback", "ImportError", "ModuleNotFoundError", "error procesando un mensaje",
              "error en poll", "AttributeError", "KeyError", "TypeError")


def _now():
    return datetime.now(timezone.utc)


def _iso(dt=None):
    return (dt or _now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tail(path, n=TAIL_N):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.readlines()[-n:]
    except Exception:
        return []


def _parse_ts(s):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ── Detectores (puros sobre el estado/los logs; cada uno devuelve lista de anomalías) ──────────
def detectar_flojas(now=None):
    """Respuestas FLOJAS recientes que marcó el propio respondedor (state/vigia/respuestas-*.jsonl)."""
    now = now or _now()
    out = []
    for p in glob.glob(os.path.join(VIGIA_DIR, "respuestas-*.jsonl")):
        for ln in _tail(p, 400):
            try:
                d = json.loads(ln)
            except Exception:
                continue
            if not d.get("floja"):
                continue
            ts = _parse_ts(d.get("ts", ""))
            if ts and (now - ts).total_seconds() > WINDOW_MIN * 60:
                continue
            out.append({
                "tipo": "respuesta_floja",
                "clase": "necesita_codigo",   # una mala respuesta = candidata a bug de routing/datos → la reviso yo
                "key": "floja:%s:%s" % (d.get("sello", "?"), d.get("ts", "?")),
                "detalle": "respuesta floja (brain=%s, motivo=%s, sensible=%s)" % (
                    d.get("brain"), d.get("motivo"), d.get("sensible")),
            })
    return out


def _msg_de_job(job):
    """Extrae el mensaje original (dentro de <<< >>> del TRIAGE_FRAME, o la intención cruda)."""
    t = job.get("intencion", "") or ""
    m = re.search(r"<<<(.*?)>>>", t, re.S)
    return (m.group(1).strip() if m else t.strip())


def _es_trivial(msg):
    """True si el mensaje es trivial/obsoleto y se puede retirar sin riesgo (p.ej. 'y', 'ok')."""
    s = (msg or "").strip()
    if len(s) <= 3:
        return True
    palabras = re.sub(r"[¿¡!?.,;:…]+", " ", s.lower()).split()
    cortesia = {"hola", "ok", "okey", "vale", "gracias", "buenas", "y", "si", "sí", "no"}
    return 0 < len(palabras) <= 2 and all(w in cortesia for w in palabras)


def _job_por_id(job_id):
    for sub in ("pending", "processing"):
        for p in glob.glob(os.path.join(QUEUE, sub, "*%s.json" % job_id)):
            try:
                return p, json.load(open(p, encoding="utf-8"))
            except Exception:
                return p, None
    return None, None


def detectar_bucles():
    """Jobs aplazados (rc 75) una y otra vez = no avanzan. Lee dispatcher.out (tiene timestamps)."""
    lineas = _tail(os.path.join(LOGS, "dispatcher.out"), TAIL_N)
    cuenta = {}
    for ln in lineas:
        m = re.search(r"job (\w+) APLAZADO", ln)
        if m:
            cuenta[m.group(1)] = cuenta.get(m.group(1), 0) + 1
    out = []
    for jid, n in cuenta.items():
        if n < BUCLE_MIN:
            continue
        path, job = _job_por_id(jid)
        if job is None:
            continue   # el job YA no existe (resuelto/retirado): líneas históricas del log, no es anomalía VIVA
        trivial = _es_trivial(_msg_de_job(job))
        out.append({
            "tipo": "accion_en_bucle",
            "clase": "auto_arreglable" if trivial else "informar",
            "key": "bucle:%s" % jid,
            "detalle": "job %s aplazado %d veces seguidas%s" % (
                jid, n, " (trivial/obsoleto → retirar)" if trivial else " (acción real esperando al cerebro principal)"),
            "accion": ("retirar_job", jid) if trivial else None,
        })
    return out


def detectar_crash_loop():
    lineas = _tail(os.path.join(LOGS, "bot-telegram.err"), 80)
    arranques = sum(1 for ln in lineas if "daemon arriba" in ln)
    # Un reinicio LIMPIO (una recarga mía, o un blip de red transitorio) NO es un crash-loop. Solo es
    # crash si hay muchos reinicios Y errores REALES (Traceback/ImportError/…) en el tramo reciente —
    # así las recargas y los "aviso de red" no disparan falsos positivos (la lección de hoy).
    err_real = any(any(t in ln for t in _ERR_TELLS) for ln in lineas)
    if arranques >= CRASH_MIN and err_real:
        return [{
            "tipo": "bot_inestable",
            "clase": "necesita_codigo",
            "key": "crash:%s" % datetime.now().strftime("%Y-%m-%dT%H"),   # 1 por hora máx
            "detalle": "el bot se reinició %d veces CON errores reales en el log (posible crash-loop)" % arranques,
        }]
    return []


def detectar_errores():
    out = []
    for nombre in ("bot-telegram.err", "dispatcher.err"):
        for ln in _tail(os.path.join(LOGS, nombre), 60):
            if any(t in ln for t in _ERR_TELLS):
                ln = ln.strip()
                import hashlib
                h = hashlib.sha256(ln.encode("utf-8")).hexdigest()[:10]
                out.append({
                    "tipo": "error_log",
                    "clase": "necesita_codigo",
                    "key": "err:%s" % h,
                    "detalle": "%s: %s" % (nombre, ln[:160]),
                })
    return out


def detectar_healthcheck_parado():
    """B (lado vigía): ¿healthcheck dejó de correr? Lee su last_check.json (ts local 'YYYY-MM-DDTHH:MM:SS',
    que healthcheck escribe cada ciclo). Si lleva > HC_STALE_MIN sin actualizarse → anomalía (el chequeo
    SIMÉTRICO al que healthcheck hace del vigía). Sin baseline (ausente) → no dispara. Fail-soft.

    Gracia de arranque: si la máquina lleva despierta < HC_BOOT_GRACE_MIN, el healthcheck (RunAtLoad=false,
    StartInterval=1800) aún no ha tenido su primer tick → su last_check viejo NO es un fallo, es un arranque.
    Mata el falso positivo del 'portátil recién encendido' sin tocar nada."""
    try:
        ts = json.load(open(HC_LAST_CHECK, encoding="utf-8")).get("ts")
        edad_min = (datetime.now() - datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")).total_seconds() / 60.0
    except FileNotFoundError:
        return []                                  # sin baseline aún → no disparamos
    except Exception:
        return []                                  # ts ilegible → no inventamos un fallo
    up = _uptime_seg()
    if up is not None and up < HC_BOOT_GRACE_MIN * 60:
        return []                                  # recién arrancado → aún no ha corrido, no es fallo
    if edad_min > HC_STALE_MIN:
        return [{
            "tipo": "healthcheck_parado",
            "clase": "informar",
            "key": "healthcheck_parado:%s" % datetime.now().strftime("%Y-%m-%dT%H"),   # 1/hora máx
            "detalle": "healthcheck lleva %d min sin correr (su última comprobación es vieja)" % int(edad_min),
        }]
    return []


# ── Dedup (seen) + registro ────────────────────────────────────────────────────────────────────
def _cargar_seen():
    try:
        return json.load(open(SEEN, encoding="utf-8"))
    except Exception:
        return {}


def _guardar_seen(seen):
    os.makedirs(VIGIA_DIR, exist_ok=True)
    corte = time.time() - SEEN_TTL_H * 3600
    seen = {k: v for k, v in seen.items() if v.get("t", 0) >= corte}   # poda > TTL
    tmp = SEEN + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False)
    os.replace(tmp, SEEN)


def _registrar(anom):
    os.makedirs(VIGIA_DIR, exist_ok=True)
    with open(ANOMALIAS, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(anom, ts=_iso()), ensure_ascii=False) + "\n")


# ── Auto-arreglo SEGURO (solo estado reversible; nunca código ni egress) ───────────────────────
def _retirar_job(job_id, motivo):
    """Mueve un job obsoleto de pending/processing a failed/ (atómico). Reversible (queda en failed)."""
    path, job = _job_por_id(job_id)
    if not path or not os.path.exists(path):
        return False
    try:
        if job is not None:
            job["terminado"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            job["ultimo_error"] = "retirado por el vigía: " + motivo
            tmp = path + ".vig"
            json.dump(job, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
            os.replace(tmp, path)
        os.replace(path, os.path.join(QUEUE, "failed", os.path.basename(path)))
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def auto_arreglar(anom):
    """Ejecuta el arreglo seguro de una anomalía auto_arreglable. Devuelve (ok, descripción)."""
    acc = anom.get("accion")
    if not acc:
        return False, "sin acción"
    if acc[0] == "retirar_job":
        ok = _retirar_job(acc[1], anom.get("detalle", ""))
        return ok, ("retirado job %s" % acc[1]) if ok else ("no pude retirar job %s" % acc[1])
    return False, "acción desconocida: %s" % (acc[0],)


# ── Auto-curación del healthcheck (pieza 2): intentar-y-verificar, NUNCA callar en silencio ─────
def _uptime_seg():
    """Segundos que la máquina lleva encendida (macOS: kern.boottime). None si no se puede saber
    (→ el llamante no aplica la gracia de arranque). Fail-soft: nunca lanza."""
    try:
        import subprocess
        out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True,
                             text=True, timeout=3)
        if out.returncode != 0:
            return None
        m = re.search(r"sec\s*=\s*(\d+)", out.stdout)
        if not m:
            return None
        return max(0.0, time.time() - int(m.group(1)))
    except Exception:
        return None


def _cargar_autofix():
    try:
        return json.load(open(AUTOFIX, encoding="utf-8"))
    except Exception:
        return {}


def _guardar_autofix(st):
    os.makedirs(VIGIA_DIR, exist_ok=True)
    tmp = AUTOFIX + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, AUTOFIX)


def _kickstart_healthcheck():
    """Reanima el daemon local del healthcheck. Camino normal: `launchctl kickstart -k` (job cargado).
    Si el kickstart falla porque el job está DESCARGADO o `disabled` —lo que deja un CÓDIGO ROJO al hacer
    `unload -w` (ver incident-healthcheck-disabled-por-codigo-rojo)— kickstart NO puede revivirlo: hay que
    `enable` (quita el flag disabled persistente) + `bootstrap` (recarga el plist) y volver a kickstart.
    Todo local, reversible, sin egress → mismo perfil que _retirar_job, no toca el muro.
    Test/no-macOS: BTP_VIGIA_NO_KICKSTART=1 lo simula. Devuelve (ok, descripción)."""
    if os.environ.get("BTP_VIGIA_NO_KICKSTART") == "1":
        return True, "kickstart simulado (BTP_VIGIA_NO_KICKSTART)"
    try:
        import subprocess
        uid = os.getuid()
        svc = "gui/%d/%s" % (uid, HC_LABEL)

        def _lc(*args):
            return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=10)

        out = _lc("kickstart", "-k", svc)
        if out.returncode == 0:
            return True, "kickstart lanzado (%s)" % HC_LABEL
        # rc!=0 suele ser "Could not find service" → el job está descargado/disabled. Rescate acotado:
        plist = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % HC_LABEL)
        if not os.path.exists(plist):
            return False, "kickstart rc=%d y sin plist en %s" % (out.returncode, plist)
        _lc("enable", svc)                       # quita el flag `disabled` persistente (unload -w)
        _lc("bootstrap", "gui/%d" % uid, plist)  # recarga el job en launchd
        out2 = _lc("kickstart", "-k", svc)
        if out2.returncode == 0:
            return True, "revivido: enable+bootstrap+kickstart (%s)" % HC_LABEL
        return False, "revive falló rc=%d: %s" % (out2.returncode, (out2.stderr or "").strip()[:120])
    except Exception as e:
        return False, "kickstart no disponible: %s" % e


def _autoheal_healthcheck(anom):
    """Intenta reanimar el healthcheck ANTES de molestar a {{TITULAR}}, con presupuesto acotado.

    Camino: si aún hay presupuesto en la ventana → UN kickstart local y NO se escala este ciclo (se le
    da tiempo a revivir; queda auditado en anomalias.jsonl). Si el presupuesto ya se agotó y sigue
    parado → se escala a {{TITULAR}} con constancia de que ya se intentó ('ya lo reinicié N veces y no revive').
    Nunca se cura en silencio-para-siempre: agotado el presupuesto, el tercer testigo (ella) SIEMPRE entra."""
    st = _cargar_autofix()
    rec = st.get("healthcheck_parado") or {}
    ahora = time.time()
    if rec and (ahora - rec.get("t0", 0)) > HC_KICK_WINDOW_H * 3600:
        rec = {}                                   # ventana expirada → presupuesto fresco
    n = int(rec.get("n", 0))
    if n < HC_KICK_MAX:
        ok, desc = _kickstart_healthcheck()
        st["healthcheck_parado"] = {"t0": rec.get("t0", ahora), "n": n + 1}
        _guardar_autofix(st)
        # No escalar aún: darle un ciclo a que reviva. Auditado, pero sin push a {{TITULAR}} (evita el
        # spam del 'portátil dormido', que se auto-cura solo en el siguiente tick del healthcheck).
        return dict(anom, clase="auto_reintento", auto_resuelta=ok, auto_desc=desc,
                    escalar_humano=False)
    # Presupuesto agotado y sigue parado → esto NO es un blip: entra {{TITULAR}}.
    return dict(anom, clase="informar", escalar_humano=True,
                detalle=anom.get("detalle", "") + " · ya intenté reiniciarlo %d vez/veces y no revive" % n)


# ── Aviso a {{TITULAR}} (fail-LOUD con anti-spam) ───────────────────────────────────────────────────
# El vigía detecta el bot caído / respuestas flojas; sin esto solo lo escribía en un jsonl que nadie
# leía (hueco real del replanteo). Avisa en lenguaje llano por el choke-point (respeta HALT) con la
# misma ventana de 12h que healthcheck: un problema NUEVO rompe el silencio; el mismo se recuerda
# como mucho cada 12h. La clave de dedup es estable (sin texto volátil) para no flapear.
_TEXTO_LLANO = {
    "bot_inestable": "El bot de Telegram se está reiniciando en bucle con errores reales — puede que ahora mismo no te conteste.",
    "respuesta_floja": "Una respuesta del bot salió floja (no encontró datos o no respondió bien) — lo reviso.",
    "error_log": "Hay un error nuevo en los logs del bot/dispatcher — lo reviso.",
    "accion_en_bucle": "Un encargo lleva rato sin avanzar, esperando al cerebro principal.",
    "healthcheck_parado": "El chequeo de salud del lazo (healthcheck) sigue caído y YA intenté reiniciarlo "
                          "yo solo sin éxito — necesita que lo mires. A mano: "
                          "launchctl kickstart -k gui/$(id -u)/com.btp.healthcheck",
}


def _avisar_a_titular(nuevas):
    """Si hay anomalías que merecen aviso (no las que el vigía ya auto-resolvió), manda UN digest en
    llano. Anti-spam: solo si hay una condición nueva o ya pasó la ventana de 12h. Devuelve True si envió."""
    # No avisamos de lo ya auto-resuelto NI de lo que el vigía aún está reanimando (escalar_humano=False):
    # eso evita el spam del 'portátil dormido' (se auto-cura solo) y solo molesta a {{TITULAR}} si de verdad
    # no revive tras agotar el auto-arreglo.
    avisables = [a for a in nuevas
                 if not a.get("auto_resuelta") and a.get("escalar_humano") is not False]
    claves = sorted({a.get("key", "?") for a in avisables})
    if not claves:
        try:                                   # resuelto → resetea para que un problema FUTURO avise
            os.remove(ALERTA_STATE)
        except (FileNotFoundError, OSError):
            pass
        return False
    now = time.time()
    try:
        prev = json.load(open(ALERTA_STATE, encoding="utf-8"))
    except Exception:
        prev = {}
    prev_claves = set(prev.get("claves", []))
    hay_nueva = bool(set(claves) - prev_claves)
    elapsed_h = (now - prev.get("ts", 0)) / 3600.0
    if not hay_nueva and elapsed_h < ALERTA_COOLDOWN_H:
        return False                           # nada nuevo y dentro de la ventana → silencio
    # Texto en llano, sin jerga ("para_codigo"), una línea por tipo de problema (no por cada anomalía).
    tipos = []
    for a in avisables:
        t = a.get("tipo", "?")
        if t not in tipos:
            tipos.append(t)
    lineas = [_TEXTO_LLANO.get(t, "Anomalía detectada en el lazo.") for t in tipos]
    texto = "🩺 El vigía detectó algo en el lazo:\n- " + "\n- ".join(lineas)
    try:
        import salida
        # Los avisos del vigía son, por defecto, fontanería interna → log operativo, no al chat.
        # EXCEPCIÓN (B, watch-the-watcher): si el propio healthcheck está parado, nadie estaría
        # revisando ni curando el lazo → eso SÍ requiere a {{TITULAR}} (regla de oro: lo que no se
        # auto-arregla y necesita acción de ella escala a "humano"). El resto sigue operativo.
        cat = "humano" if any(a.get("escalar_humano") for a in avisables) else "operativo"
        salida.report_to_titular(texto, categoria=cat, fuente="vigia")
    except Exception:
        return False                           # fail-soft: no romper el ciclo si el choke-point no está
    try:                                       # guarda SOLO al enviar: referencia = último avisado
        with open(ALERTA_STATE, "w", encoding="utf-8") as f:
            json.dump({"ts": now, "claves": claves}, f, ensure_ascii=False)
    except Exception:
        pass
    return True


# ── Puente vigía → cerebro principal (Claude) ──────────────────────────────────────────────────
_TAREA_PARA_CODIGO = {
    "respuesta_floja": "El bot dio una respuesta floja (no encontró datos o no respondió bien). "
                       "Revisa por qué y arréglalo si aplica.",
    "bot_inestable": "El bot de Telegram se reinicia en bucle con errores reales. Diagnostica el "
                     "traceback en los logs y arréglalo.",
    "error_log": "Apareció un error nuevo en los logs del bot/dispatcher. Diagnostícalo y arréglalo "
                 "si es un bug real.",
}


def _encolar_para_codigo(nuevas):
    """Pieza 1: lo que necesita arreglo de CÓDIGO deja de morir en un jsonl que nadie lee — se encola
    para que el dispatcher (Polaris 24/7) lo recoja y lo arregle Claude, sin que {{TITULAR}} sea el puente.
    Pasa igual por run_agent.sh (el muro) y el cost_guard: no abre ninguna vía nueva. Fail-soft e
    idempotente por ciclo (solo anomalías NUEVAS llegan aquí; el dedup de `seen` ya filtró lo repetido).
    Se puede apagar con BTP_VIGIA_ENCOLAR=0."""
    if os.environ.get("BTP_VIGIA_ENCOLAR") == "0":
        return
    encolados = []
    for a in nuevas:
        if a.get("clase") != "necesita_codigo":
            continue
        tipo = a.get("tipo", "?")
        base = _TAREA_PARA_CODIGO.get(tipo, "Anomalía del vigía que puede necesitar arreglo de código.")
        intencion = "[vigía] %s\n\nDetalle: %s\n(clave: %s)" % (base, a.get("detalle", ""), a.get("key", ""))
        try:
            import cola
            jid = cola.enqueue(intencion, prioridad="normal", procedencia="vigia",
                               agente="tecnico", tipo="exec")
            encolados.append(jid)
        except Exception:
            pass   # fail-soft: si la cola no está disponible, la anomalía sigue registrada en anomalias.jsonl
    return encolados


# ── Ciclo ──────────────────────────────────────────────────────────────────────────────────────
def run(avisar=False):
    """Un ciclo del vigía: detecta, dedup, auto-arregla lo seguro, registra. Devuelve resumen.

    `avisar` es OPT-IN a propósito: solo el daemon/CLI (main) pone avisar=True para mandar el aviso
    a {{TITULAR}} por el choke-point. Llamar a run() a secas NO envía nada — así los tests y cualquier uso
    programático quedan mudos (no spamean el Telegram real) y la detección sigue siendo pura."""
    # Observabilidad (pieza 8): marca inicio para calcular duración al final.
    _obs_ts_ini = time.time()

    anomalias = (detectar_flojas() + detectar_bucles()
                 + detectar_crash_loop() + detectar_errores()
                 + detectar_healthcheck_parado())
    seen = _cargar_seen()
    nuevas, arreglos = [], []
    for a in anomalias:
        if a["key"] in seen:
            continue
        seen[a["key"]] = {"t": time.time(), "tipo": a["tipo"]}
        if a.get("clase") == "auto_arreglable":
            ok, desc = auto_arreglar(a)
            a = dict(a, auto_resuelta=ok, auto_desc=desc)
            arreglos.append(desc)
        elif a.get("tipo") == "healthcheck_parado":
            # Pieza 2: intenta reanimarlo (kickstart acotado) antes de escalar; solo molesta a
            # {{TITULAR}} si el presupuesto se agota y sigue caído (watch-the-watcher intacto).
            a = _autoheal_healthcheck(a)
            if a.get("auto_desc"):
                arreglos.append(a["auto_desc"])
        _registrar(a)
        nuevas.append(a)
    _guardar_seen(seen)
    _encolar_para_codigo(nuevas)               # Pieza 1: el puente vigía→cerebro principal (Claude)
    aviso = _avisar_a_titular(nuevas) if avisar else False

    # Bus de errores (Fase 1): feed silencioso al punto único de errores para cada anomalía nueva.
    # escalar=False: el vigía ya gestiona su propio aviso arriba; aquí solo dejamos traza en el bus.
    for a in nuevas:
        try:
            import errores as _err
            _err.registrar(
                origen="vigia",
                error=a.get("detalle", a.get("tipo", "anomalia")),
                severidad=_err.OPERATIVO,
                job="anomalia_vigia",
                escalar=False,
            )
        except Exception:
            pass   # fail-soft: nunca rompemos el ciclo del vigía

    # Observabilidad (pieza 8): registra traza del ciclo completo. Silencioso.
    try:
        import observabilidad as _obs
        _obs.registrar(
            agente="vigia",
            job="ciclo",
            ts_ini=datetime.fromtimestamp(_obs_ts_ini, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            resultado="ok",
        )
    except Exception:
        pass

    # B (watch-the-watcher): deja un latido de "el vigía corrió" para que healthcheck lo vigile.
    try:
        os.makedirs(VIGIA_DIR, exist_ok=True)
        tmp = LAST_RUN + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": _iso()}, f, ensure_ascii=False)   # ISO-Z, lo lee healthcheck._edad_horas_ts
        os.replace(tmp, LAST_RUN)
    except Exception:
        pass   # fail-soft: nunca rompemos el ciclo del vigía por no poder escribir el latido

    return {"nuevas": nuevas, "arreglos": arreglos,
            "aviso_enviado": aviso,
            "para_codigo": [a for a in nuevas if a.get("clase") == "necesita_codigo"]}


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv and argv[0] == "estado":
        for ln in _tail(ANOMALIAS, 30):
            print(ln.rstrip())
        return 0
    res = run(avisar=True)   # el daemon/CLI SÍ avisa a {{TITULAR}} (con anti-spam de 12h)
    print("vigía: %d anomalía(s) nueva(s); %d auto-arreglada(s); %d para revisar en código; aviso a {{TITULAR}}: %s." % (
        len(res["nuevas"]), len(res["arreglos"]), len(res["para_codigo"]),
        "sí" if res.get("aviso_enviado") else "no"))
    for a in res["nuevas"]:
        print("  · [%s] %s%s" % (a.get("clase"), a.get("detalle"),
                                 "  → " + a.get("auto_desc", "") if a.get("auto_resuelta") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
