#!/usr/bin/env python3
"""tools/cost_guard.py — freno de gasto DURO del lazo 24/7 (P1, MF-9 / A2). FAIL-CLOSED.

Contador de gasto del día persistido en tools/state/cost/YYYY-MM-DD.json (escritura
atómica bajo lock propio). El despachador lo consulta ANTES de cada job y le suma el
coste DESPUÉS. Reglas a prueba de fallos:

  · Tope diario DURO (def $30) + tope por-job. Un job puede pedir un tope ≤ el global,
    NUNCA subirlo.
  · `add_cost` suma el `total_cost_usd` del JSON de run_agent.sh. Si falta o no es
    numérico → cuenta un coste PESIMISTA (= tope del job), nunca 0: un run que ocurrió
    y no reportó coste se contabiliza como caro, no como gratis.
  · Si el estado del día existe pero NO se puede leer/parsear → `_panic`: crea `.HALT`
    (dentro y fuera del repo) y aborta. No asume 0 sobre un fichero corrupto.
  · Modo conservador: si existe state/healthcheck/degraded.flag (dead-man A3), el tope
    efectivo se recorta a un % — frena nuevos jobs, pero NO bloquea la salida (eso es
    solo el muro/HALT).

Independiente de tools/coste.py (que mide a posteriori los transcripts y queda como
auditoría cruzada). Sin dependencias (stdlib).
"""
import json
import math
import os
import sys
import time
from datetime import datetime

HOME = os.path.expanduser("~")
# Casa base SIEMPRE, no el árbol de al lado (`_casa.casa_base()`, migrado el 24-sep-2026 y borrado
# de la lista de `tests/test_raices_casa_base.py`). Esto resolvía la raíz con `__file__`, así que
# un `cost_guard` importado desde un worktree llevaba su PROPIA contabilidad, y los topes de casa
# base no veían nada de lo que pasara ahí. Con {{TITULAR}} en varias sesiones a la vez, y cada una en
# su worktree, ese caso es el NORMAL. De paso, `$REPO/.HALT` pasa a ser el de casa base: un HALT
# puesto ahí ahora sí frena a una sesión de worktree.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _casa import casa_base, es_proceso_de_test  # noqa: E402

REPO = casa_base()
# BTP_STATE_DIR aísla el estado (tests / reubicación); por defecto tools/state.
# Y en batería (`BTP_TEST_BATTERY=1`) sin `BTP_STATE_DIR`, a un tmp y NUNCA a la contabilidad real
# — mismo freno y misma historia que `panel.py`. Lo que lo destapó: mientras se migraba esto se
# encontraron 2.263 eventos `borde-reescritura` de 6 segundos ($2,31) en el estado de coste de un
# worktree. Seis segundos no son 2.263 llamadas de API: era un test sin aislar, dinero SIMULADO.
# Mientras la raíz era el worktree, eso solo ensuciaba un árbol que se borra; apuntando a casa
# base habría entrado en el dinero de verdad. Por eso la migración trae su freno en el mismo sitio.
# Tercer freno (25-sep-2026): un proceso que ES un test (`tests/test_*.py`) tampoco llega al dinero
# real aunque se le olvide aislarse. Lo destapó el registro completo el mismo día que nació:
# `test_gasto_tarifa` apuntaba $4 (grok $3 + perplexity $1) en el contador REAL en cada pasada de
# `test_all.sh`, que no exporta BTP_TEST_BATTERY, y `test_perplexity_agent` otros céntimos. Ese
# dinero simulado inflaba el gasto del día y gasta el tope de verdad. Freno de CLASE, no test a test.
_ES_TEST = es_proceso_de_test()
if os.environ.get("BTP_STATE_DIR"):
    STATE = os.environ["BTP_STATE_DIR"]
elif os.environ.get("BTP_TEST_BATTERY") == "1" or _ES_TEST:
    import tempfile
    STATE = os.path.join(tempfile.gettempdir(), "btp-test-cost-%d" % os.getuid())
else:
    STATE = os.path.join(REPO, "tools", "state")
COST = os.path.join(STATE, "cost")
LOCKDIR = os.path.join(COST, "lock")
LIMITS = os.path.join(COST, "limits.json")
# Registro de RECARGAS del saldo PREPAGO de Anthropic (distinto del tope local): cada línea
# {ts, fecha, monto_usd}. Anthropic NO expone el saldo por API, así que la única forma de avisar
# ANTES de que llegue a 0 (el susto del 17-jul) es: {{TITULAR}} anota lo que recarga y comparamos con
# el gasto desde entonces. Ver registrar_recarga / saldo_prepago.
RECARGAS = os.path.join(COST, "recargas.jsonl")
DEGRADED = os.path.join(STATE, "healthcheck", "degraded.flag")
# HALT: por defecto ~/.btp.HALT (A1, fuera del repo) y $REPO/.HALT. BTP_HALT_FILES (":"
# separado) permite aislarlos en tests para no tocar los reales.
HALT_FILES = tuple(os.environ["BTP_HALT_FILES"].split(":")) if os.environ.get("BTP_HALT_FILES") \
    else (os.path.join(HOME, ".btp.HALT"), os.path.join(REPO, ".HALT"))

# Decisión de {{TITULAR}} (27/6/26, «hoy trabajamos a tope»): tope alto para no frenar el trabajo,
# con un CORTACIRCUITOS de gasto-de-golpe como única barrera dura (ver TOPE_GOLPE_USD). El medidor
# va en USD; 540 USD ≈ 500 € (los 500 € exactos que pidió {{TITULAR}}).
TOPE_DIARIO_USD = 540.0     # antes 30; override en cost/limits.json (solo puede BAJARLO)
TOPE_JOB_USD = 3.0          # coste pesimista por defecto si un job no reporta
# H4 (Fase 0): techo ABSOLUTO en código. limits.json puede BAJAR el tope, NUNCA
# subirlo. El lazo PUEDE escribir limits.json (es .json, no lo frena el muro), así que
# sin este clamp podría poner 99999 y desactivar el freno. El código manda sobre el fichero.
TOPE_MAX_ABSOLUTO = 540.0
# Tope MENSUAL: segunda barrera por si el gasto diario, día tras día, suma demasiado en el mes.
# Mismo patrón que el diario: limits.json SOLO puede BAJARLO, el techo absoluto vive en código. El
# acumulador del mes se DERIVA sumando los JSON diarios al LEER (no hay ledger mensual paralelo).
TOPE_MENSUAL_USD = 1500.0       # antes 200; ~3 días a tope sin re-bloquear, sigue acotado
TOPE_MENSUAL_MAX_ABSOLUTO = 1500.0
# CORTACIRCUITOS «de golpe» ({{TITULAR}} 27/6/26): si UN solo cargo (un job) gasta esto o más de una
# tacada, es un runaway → se dispara CÓDIGO ROJO (para todo + avisa fuerte). Es la barrera dura que
# sustituye al viejo tope bajo: el día puede subir solo, pero un pico de 500 lo frena en seco.
TOPE_GOLPE_USD = 540.0   # ≈ 500 € (el «500 de golpe» que pidió {{TITULAR}})
FACTOR_CONSERVADOR = 0.30   # en modo degradado, tope efectivo = 30% del diario
# Reserva para lo ESENCIAL (Vega/vigía, healthcheck): la cháchara rutinaria no puede
# vaciar la hucha por debajo de esta fracción; lo esencial SÍ puede tirar de ella. No
# sube el techo (el dinero total sigue acotado), solo decide QUIÉN usa la última porción.
RESERVA_ESENCIAL_FRAC = 0.20
# Franja SOLO para la charla interactiva de {{TITULAR}} (la puerta del gateway: ella delante, en vivo).
# Ni la cháchara rutinaria NI el trabajo de fondo "esencial" (Vega/vigía) pueden tocar este último
# tramo → el loop 24/7 nunca la deja sin Claude estando ella presente. Decisión de {{TITULAR}} (27/6/26:
# «reserva para ti»). No sube el techo total (el dinero sigue acotado), solo reserva QUIÉN usa la
# última porción del día. Es el carril de MÁS privilegio: interactivo > esencial > rutina.
RESERVA_INTERACTIVA_FRAC = 0.15
MAX_EVENTOS = 50            # cola corta de eventos guardada en el JSON del día
# No-mudo (11-jul-2026): antes, si un job ESENCIAL (Vega/vigía) topaba el tope y había novedad
# real, check_before_job simplemente devolvía ok=False y CALLABA — el bloqueo era invisible hasta
# que {{TITULAR}} auditaba a mano. Ahora avisa por tools/salida.py (única boca), con este cooldown para
# no ser ruido en cada reintento del daemon (el tope sigue agotado durante horas, no hay que
# repetirlo cada 15 min).
NUDGE_ESENCIAL_COOLDOWN_S = 6 * 3600


def _ensure_dirs():
    os.makedirs(COST, mode=0o700, exist_ok=True)


def _today_path():
    return os.path.join(COST, datetime.now().strftime("%Y-%m-%d") + ".json")


def _limits():
    """(tope_diario, tope_job, tope_mensual). Todos clampados a su techo ABSOLUTO de código:
    limits.json (y el override del día/mes) solo pueden BAJAR, nunca subir."""
    d = TOPE_DIARIO_USD
    j = TOPE_JOB_USD
    m = TOPE_MENSUAL_USD
    try:
        if os.path.exists(LIMITS):
            cfg = json.load(open(LIMITS, encoding="utf-8")) or {}
            rd = float(cfg.get("tope_diario_usd", d))
            rj = float(cfg.get("tope_job_usd", j))
            rm = float(cfg.get("tope_mensual_usd", m))
            # H4: limits.json SOLO puede bajar el tope; el techo absoluto vive en código.
            # nan/inf (que burlaban las comparaciones >/<=) → se ignora el override.
            d = min(rd, TOPE_MAX_ABSOLUTO) if math.isfinite(rd) else TOPE_DIARIO_USD
            j = min(rj, TOPE_MAX_ABSOLUTO) if math.isfinite(rj) else TOPE_JOB_USD
            m = min(rm, TOPE_MENSUAL_MAX_ABSOLUTO) if math.isfinite(rm) else TOPE_MENSUAL_USD
    except Exception:
        _panic("limits.json ilegible")
    # Subida puntual SOLO-HOY (acto de {{TITULAR}}): cost/override-YYYY-MM-DD.json. Se AUTO-EXPIRA
    # (mañana el fichero ya no es el de hoy) → el tope vuelve solo al baseline, sin que nadie
    # tenga que revertir nada. El clamp absoluto del código sigue mandando (nunca > el techo).
    try:
        ovr = os.path.join(COST, "override-" + datetime.now().strftime("%Y-%m-%d") + ".json")
        if os.path.exists(ovr):
            od = float((json.load(open(ovr, encoding="utf-8")) or {}).get("tope_diario_usd", d))
            if math.isfinite(od):
                d = min(od, TOPE_MAX_ABSOLUTO)
    except Exception:
        pass   # override OPCIONAL: si es ilegible, se ignora y manda el baseline (no _panic)
    # Subida puntual SOLO-ESTE-MES (acto de {{TITULAR}}): cost/override-mes-YYYY-MM.json. AUTO-EXPIRA
    # igual que el del día (el mes que viene el fichero ya no es el del mes en curso). Clamp absoluto.
    try:
        ovm = os.path.join(COST, "override-mes-" + datetime.now().strftime("%Y-%m") + ".json")
        if os.path.exists(ovm):
            om = float((json.load(open(ovm, encoding="utf-8")) or {}).get("tope_mensual_usd", m))
            if math.isfinite(om):
                m = min(om, TOPE_MENSUAL_MAX_ABSOLUTO)
    except Exception:
        pass   # override OPCIONAL: ilegible → se ignora, manda el baseline (no _panic)
    return d, j, m


def _panic(motivo):
    msg = "cost_guard PANIC: %s @ %s\n" % (motivo, datetime.now().isoformat())
    for h in HALT_FILES:
        try:
            with open(h, "a", encoding="utf-8") as f:
                f.write(msg)
        except Exception:
            pass
    sys.stderr.write("⛔ " + msg)
    raise SystemExit(3)


def _lock(timeout=10.0):
    _ensure_dirs()
    t0 = time.time()
    while True:
        try:
            os.mkdir(LOCKDIR)
            return
        except FileExistsError:
            if time.time() - t0 > timeout:
                # lock viejo/huérfano: si lleva demasiado, reclamar (fail-closed: si ni
                # eso se puede, panic — no podemos garantizar suma correcta del gasto).
                try:
                    if time.time() - os.path.getmtime(LOCKDIR) > timeout:
                        os.rmdir(LOCKDIR)
                        continue
                except Exception:
                    pass
                _panic("no pude tomar el lock de cost")
            time.sleep(0.05)


def _unlock():
    try:
        os.rmdir(LOCKDIR)
    except Exception:
        pass


def _read_today():
    """Devuelve el dict del día. Fichero ausente = 0 legítimo; fichero ilegible = PANIC."""
    p = _today_path()
    if not os.path.exists(p):
        return {"fecha": datetime.now().strftime("%Y-%m-%d"), "gastado_usd": 0.0,
                "n_jobs": 0, "eventos": []}
    try:
        d = json.load(open(p, encoding="utf-8"))
        if not isinstance(d, dict) or not isinstance(d.get("gastado_usd"), (int, float)):
            _panic("estado de coste con forma inesperada: %s" % p)
        return d
    except SystemExit:
        raise
    except Exception as e:
        _panic("estado de coste ilegible (%r): %s" % (e, p))


def _write_today(d):
    p = _today_path()
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except Exception as e:
        _panic("no pude escribir el estado de coste (%r)" % e)


def today_spent():
    return float(_read_today().get("gastado_usd", 0.0))


def month_spent(prefijo=None):
    """Gasto del MES en curso, DERIVADO sumando los JSON diarios (sin ledger paralelo → cero
    doble conteo: el mes es exactamente la suma de los días, una sola fuente de verdad). Un día
    ILEGIBLE → _panic (igual que today_spent: no asumimos 0 sobre un fichero corrupto)."""
    prefijo = prefijo or datetime.now().strftime("%Y-%m") + "-"   # YYYY-MM- → ficheros del mes
    if not os.path.isdir(COST):
        return 0.0
    total = 0.0
    for fn in os.listdir(COST):
        # SOLO ficheros-día YYYY-MM-DD.json del mes pedido. Los override-* / limits.json no
        # empiezan por la fecha, así que quedan fuera por construcción (no se doble-cuentan).
        if not (fn.startswith(prefijo) and fn.endswith(".json") and len(fn) == 15):
            continue
        try:
            d = json.load(open(os.path.join(COST, fn), encoding="utf-8"))
            g = d.get("gastado_usd") if isinstance(d, dict) else None
            if not isinstance(g, (int, float)):
                _panic("estado de coste con forma inesperada: %s" % fn)
            total += float(g)
        except SystemExit:
            raise
        except Exception as e:
            _panic("estado de coste ilegible (%r): %s" % (e, fn))
    return round(total, 6)


def _recargas_path():
    # Lee COST del MÓDULO en el momento de la llamada (mismo patrón que _nudge_mark_path) para que
    # los tests que reasignan cg.COST aíslen también el registro de recargas, sin tocar el real.
    return os.path.join(COST, "recargas.jsonl")


def registrar_recarga(monto_usd):
    """Anota una recarga del saldo prepago de Anthropic (acto de {{TITULAR}} tras recargar). Append-only:
    la ÚLTIMA línea manda. Devuelve el dict registrado."""
    _ensure_dirs()
    rec = {"ts": time.time(), "fecha": datetime.now().strftime("%Y-%m-%d"),
           "monto_usd": round(float(monto_usd), 2)}
    with open(_recargas_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def ultima_recarga():
    """Última recarga registrada {ts, fecha, monto_usd} o None si no hay ninguna."""
    p = _recargas_path()
    if not os.path.exists(p):
        return None
    ult = None
    try:
        with open(p, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = json.loads(ln)
                    if isinstance(r, dict) and "monto_usd" in r:
                        ult = r
                except Exception:
                    pass
    except Exception:
        return None
    return ult


def spent_since(fecha_iso):
    """Gasto acumulado (USD) desde una fecha YYYY-MM-DD inclusive, sumando los JSON diarios.
    NOTA: cuenta TODO el gasto del CLI, tanto API medida como el coste-sombra de la suscripción
    (el fichero-día no distingue orquestador). Sobrestima algo el consumo del PREPAGO → avisa un
    poco ANTES (conservador: mejor recargar de sobra que quedarse a 0)."""
    return spent_since_detallado(fecha_iso)["total"]


def spent_since_detallado(fecha_iso):
    """Como `spent_since`, pero separando por CANAL: {total, api, suscripcion, sin_etiquetar}.

    Por qué importa (25-jul-26): el prepago de Anthropic solo lo consume la API MEDIDA. El gasto
    que va por la cuota del plan Max no sale de ahí, y sumarlo hace que el estimador declare el
    prepago «bajo» a los dos días de cualquier recarga — pase lo que pase. Medido ese día: recarga
    de 20 $, «gastado» 49,72 $, restante −29,72 $. Un número imposible que llevaba tres ciclos
    disparando el mismo aviso, hasta poner `test_all.sh` en rojo por escalada de deuda.

    El campo `via` de cada evento (api|suscripcion) se añadió ESE mismo día, así que el histórico
    viene sin etiquetar. En vez de adivinar, se cuenta aparte: quien decide (`saldo_prepago`) sabe
    entonces qué parte de su cifra es firme y qué parte no, y puede decir «no lo sé» en vez de
    afirmar. Los días sin desglose de eventos caen enteros a `sin_etiquetar`, que es la verdad."""
    out = {"total": 0.0, "api": 0.0, "suscripcion": 0.0, "sin_etiquetar": 0.0,
           "otros_proveedores": 0.0}
    if not os.path.isdir(COST):
        return out
    for fn in sorted(os.listdir(COST)):
        if not (fn.endswith(".json") and len(fn) == 15):
            continue
        dia = fn[:-5]                                  # YYYY-MM-DD
        if dia < fecha_iso:
            continue
        try:
            d = json.load(open(os.path.join(COST, fn), encoding="utf-8"))
            if not isinstance(d, dict):
                continue
            g = d.get("gastado_usd")
            if isinstance(g, (int, float)):
                out["total"] += float(g)
            eventos = d.get("eventos")
            if not isinstance(eventos, list) or not eventos:
                # Día sin desglose: su total entero es de canal desconocido. No se reparte a ojo.
                if isinstance(g, (int, float)):
                    out["sin_etiquetar"] += float(g)
                continue
            visto = 0.0
            for e in eventos:
                if not isinstance(e, dict):
                    continue
                usd = e.get("usd")
                if not isinstance(usd, (int, float)):
                    continue
                usd = float(usd)
                visto += usd
                via = e.get("via")
                if via == "api" and str(e.get("job", "")).startswith("api-"):
                    # api-grok, api-perplexity, api-chatgpt…: dinero de OTRO proveedor (26-sep-26).
                    # Cuenta en el total, pero no sale del prepago de Anthropic.
                    out["otros_proveedores"] += usd
                    continue
                out["suscripcion" if via == "suscripcion" else
                    "api" if via == "api" else "sin_etiquetar"] += usd
            # El agregado del día puede ser mayor que la suma de eventos (eventos podados, o
            # gasto anotado antes de que existieran). Ese resto tampoco se sabe de qué canal es.
            if isinstance(g, (int, float)) and float(g) - visto > 1e-9:
                out["sin_etiquetar"] += float(g) - visto
        except Exception:
            pass                                       # día ilegible → no cuenta (fail-soft, no _panic)
    return {k: round(v, 6) for k, v in out.items()}


def saldo_prepago():
    """Estado del saldo PREPAGO estimado frente a la última recarga, o None si no hay recarga
    registrada. {monto, gastado, restante, frac} — frac ∈ [0, >1] (puede pasar de 1 si ya lo topó).

    `frac_firme` (25-jul-26) es la misma fracción contando SOLO lo que se sabe que salió de la API
    medida. La diferencia entre las dos dice cuánta de la alarma se sostiene en datos y cuánta en
    gasto de canal desconocido: sin eso, el estimador afirmaba «bajo» con una cifra que incluía la
    suscripción entera. `fiable` es False mientras la mayor parte del gasto no esté etiquetada."""
    ur = ultima_recarga()
    if not ur:
        return None
    monto = float(ur.get("monto_usd", 0.0))
    if monto <= 0:
        return None
    det = spent_since_detallado(ur.get("fecha", datetime.now().strftime("%Y-%m-%d")))
    gastado = det["total"]
    out = {"monto": round(monto, 2), "gastado": round(gastado, 2),
           "restante": round(monto - gastado, 2), "frac": gastado / monto,
           "frac_firme": det["api"] / monto,
           "sin_etiquetar": det["sin_etiquetar"],
           "fiable": det["sin_etiquetar"] <= 0.25 * gastado if gastado else True,
           "fecha_recarga": ur.get("fecha")}
    # Previsión (26-sep-26): a este ritmo de API de Anthropic, cuántas horas quedan. Solo con una
    # baseline de hora conocida y al menos 1 h de historia; si no, no se inventa.
    try:
        horas = (time.time() - float(ur.get("ts", 0))) / 3600.0
        if ur.get("ts") and horas >= 1 and det["api"] > 0:
            ritmo = det["api"] / horas
            out["ritmo_usd_h"] = round(ritmo, 3)
            out["horas_restantes"] = round(max(monto - det["api"], 0) / ritmo, 1)
    except Exception:
        pass
    return out


_CREDITO_TTL_S = 6 * 3600   # 6 h: la señal la refresca cualquier llamada a Claude del lazo (frecuente)


def marcar_credito(ok):
    """Escribe la señal de crédito (mismo fichero y formato que `ia._marcar_credito`). La llama
    run_agent.sh (26-sep-26): el lazo gasta el prepago por ahí y hasta hoy no la escribía, así que
    a las 00:21 del 26-sep la señal seguía diciendo «hay crédito» con la API ya rechazando. Es
    estado local, no una llamada fuera. Fail-soft."""
    try:
        d = os.path.join(STATE, "ia")
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, ".credito.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ok": bool(ok), "ts": time.time()}, f)
        os.replace(tmp, os.path.join(d, "credito.json"))
        return True
    except Exception:
        return False


def credito_ok(ttl=_CREDITO_TTL_S):
    """SEÑAL REAL y AUTÓNOMA del saldo prepago de Anthropic, SIN que {{TITULAR}} anote nada y SIN que
    cost_guard llame fuera (es una PUERTA; el choke-point B3 dice que solo salida.py habla con el
    exterior). La ESCRIBE quien SÍ llama a los cerebros — ia.ask, en state/ia/credito.json: éxito de
    un cerebro Claude → hay crédito (True); fallo 'credito' (400 'Credit balance is too low') →
    agotado (False). Aquí solo la LEEMOS. Devuelve True/False si es FRESCA (< ttl), None si falta o
    es vieja (no afirmamos → el caller decide). Fail-soft. Fix 23/7 — el sistema sabe el saldo solo."""
    try:
        p = os.path.join(STATE, "ia", "credito.json")
        c = json.load(open(p, encoding="utf-8"))
        if isinstance(c, dict) and "ok" in c and (time.time() - float(c.get("ts", 0))) < ttl:
            return bool(c["ok"])
    except Exception:
        pass
    return None


def resync_baseline_auto():
    """La API CONFIRMA crédito pero el estimador lo daba por gastado → el importe que suponíamos se
    quedó corto. Se SUBE el importe (el doble de lo gastado) SIN mover la baseline.

    Por qué así (26-sep-26): antes se ponía la baseline a HOY en cada pasada. Como el estimador
    cuenta por DÍA, el gasto de hoy seguía dentro y la condición volvía a cumplirse: 189 marcadores
    `auto` en recargas.jsonl y el estimador reiniciado cada 30 min, así que nunca podía avisar ANTES
    de quedarse a cero (se agotó 5 veces en septiembre sin aviso previo). Subir el importe mantiene
    la cuenta y solo se repite si el gasto vuelve a pasar del umbral. Devuelve el marcador o None."""
    try:
        ur = ultima_recarga()
        if not ur:
            return None
        sp = saldo_prepago()
        gastado = float((sp or {}).get("frac_firme", 0)) * float(ur.get("monto_usd", 0) or 0)
        monto = round(max(float(ur.get("monto_usd", 0) or 0), 2 * gastado), 2)
        if monto <= float(ur.get("monto_usd", 0) or 0):
            return None
        rec = dict(ur, monto_usd=monto, auto="ajuste", ajustado_ts=time.time())
        _append_recarga(rec)
        return rec
    except Exception:
        return None


def _append_recarga(rec):
    os.makedirs(COST, exist_ok=True)
    with open(_recargas_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _ultimo_marcador():
    """Última línea de recargas.jsonl, sea recarga o marcador de agotado."""
    ult = None
    try:
        with open(_recargas_path(), encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                    if isinstance(r, dict):
                        ult = r
                except Exception:
                    pass
    except Exception:
        pass
    return ult


def observar_credito(ok, ahora=None):
    """Aprende el importe real del prepago sin que {{TITULAR}} lo anote (26-sep-26).

    Se llama en cada pasada de salud con la señal real (`credito_ok`). Dos transiciones:
      · hay crédito → SIN crédito: se anota `{"tipo": "agotado", "gastado": X}`. X es lo que salió
        de la API de Anthropic desde la última recarga, o sea lo que de verdad se cargó.
      · SIN crédito → hay crédito: {{TITULAR}} recargó. Nueva baseline con hora exacta y, como importe,
        lo aprendido en el corte anterior (si fue creíble, > 1 $); si no, el último conocido.
    Supone que recarga cantidades parecidas; si no, el siguiente corte lo corrige solo. None si no
    hay transición. Fail-soft."""
    if ok is None:
        return None
    ahora = ahora or time.time()
    try:
        ult = _ultimo_marcador()
        agotado = bool(ult and ult.get("tipo") == "agotado")
        if ok is False and not agotado:
            sp = saldo_prepago() or {}
            gastado = round(float(sp.get("frac_firme", 0)) * float(sp.get("monto", 0)), 2)
            rec = {"tipo": "agotado", "ts": ahora, "gastado": gastado}
            _append_recarga(rec)
            return rec
        if ok is True and agotado:
            previa = ultima_recarga() or {}
            monto = float(ult.get("gastado", 0) or 0)
            if monto <= 1:
                monto = float(previa.get("monto_usd", 20.0) or 20.0)
            rec = {"ts": ahora, "fecha": datetime.fromtimestamp(ahora).strftime("%Y-%m-%d"),
                   "monto_usd": round(monto, 2), "auto": "recarga_detectada"}
            _append_recarga(rec)
            return rec
    except Exception:
        pass
    return None


def _tope_efectivo():
    d, _, _ = _limits()
    if os.path.exists(DEGRADED):
        return d * FACTOR_CONSERVADOR
    return d


def _nudge_mark_path():
    # OJO: lee COST del MÓDULO en el momento de la llamada (no la baquea en un constante de
    # import) para que los tests que reasignan cg.COST (mismo patrón que el resto del fichero)
    # también aíslen este marcador, sin tocar el estado real entre pruebas.
    return os.path.join(COST, "nudge_bloqueo_esencial.json")


def _en_cooldown_nudge_esencial():
    try:
        with open(_nudge_mark_path(), encoding="utf-8") as f:
            ts = float(json.load(f).get("ts", 0))
        return (time.time() - ts) < NUDGE_ESENCIAL_COOLDOWN_S
    except Exception:
        return False   # sin marcador / ilegible → no hay cooldown (avisa)


def _sella_nudge_esencial():
    try:
        _ensure_dirs()
        tmp = _nudge_mark_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time()}, f)
        os.replace(tmp, _nudge_mark_path())
    except Exception as e:
        sys.stderr.write("cost_guard: no pude sellar el cooldown del nudge esencial: %r\n" % (e,))


# Boca al exterior, INYECTABLE. None = usar la REAL (`import salida`). Un test pone aqui su doble y
# ninguna ruta —ni un subproceso— puede alcanzar el telefono de {{TITULAR}}. El `import salida` perezoso
# que habia aqui era un cable directo que ningun stub de sys.modules podia interceptar con fiabilidad.
NOTIFICADOR = None


def _nudge_bloqueo_esencial(motivo):
    """Job ESENCIAL (Vega/vigía) bloqueado por [tope_local]: antes check_before_job callaba del
    todo (ok=False y ya) — el bloqueo era invisible hasta que {{TITULAR}} auditaba a mano. Avisa por
    tools/salida.py (única boca al exterior; respeta HALT/silencio nocturno/cooldown propios),
    con SU PROPIO cooldown para no repetir cada 15 min mientras el tope siga agotado. Fail-soft
    TOTAL: si `salida` no carga o el envío falla, nunca rompe check_before_job — el ok=False que
    ya devuelve la función sigue siendo la señal real; esto es solo el aviso extra."""
    if _en_cooldown_nudge_esencial():
        return
    try:
        if NOTIFICADOR is not None:
            salida = NOTIFICADOR
        else:
            import salida  # única boca al exterior (Telegram); import perezoso (mismo patrón que
                            # el codigo_rojo.trigger de add_cost, evita el ciclo de import en frío)
        texto = ("🔒 No pude hacer una tarea esencial (Vega/vigía) porque el tope de gasto de "
                 "hoy está agotado. %s\nSube el tope con \"sube\" (1 clic) o mira si puede "
                 "esperar a mañana." % motivo)
        res = salida.report_to_titular(texto, urgente=False, voz="calida", fuente="cost_guard")
        if isinstance(res, dict) and res.get("delivered"):
            _sella_nudge_esencial()
    except Exception as e:
        sys.stderr.write("cost_guard: no pude avisar del bloqueo esencial (ignorado): %r\n" % (e,))


def check_before_job(tope_job_usd=None, esencial=False, interactivo=False, via="api"):
    """(ok, motivo, restante). ok=False si no queda presupuesto de PAGO.

    `via="suscripcion"` (25-jul-26): el job NO va por la API medida, va por la cuota del plan Max
    (token OAuth, ver tools/orquestadores.json). Ahí el € marginal es CERO, así que los topes de
    DINERO no pintan nada: gatearlo con ellos es frenar trabajo gratis con la baranda del gasto.
    Bug real de ese día: una pasada de auto-mejora (que corre por suscripción desde el 17-jul) se
    aplazó porque el contador de dólares iba por 13,20 de 20 — dólares que en su mayoría NO eran
    dinero. Lo que sigue acotando un runaway de CUOTA: --max-turns, el cortacircuitos de golpe de
    add_cost, el HALT y la propia cadencia del plist. Fail-closed: cualquier via desconocida (o
    ausente) cuenta como "api", o sea como dinero real.
    """
    if via == "suscripcion":
        return (True, "ok %s (por SUSCRIPCIÓN: no consume el presupuesto de la API medida)" % (
            "interactivo" if interactivo else ("esencial" if esencial else "rutina")), float("inf"))
    return _check_api(tope_job_usd, esencial, interactivo)


def _check_api(tope_job_usd=None, esencial=False, interactivo=False):
    """El chequeo de siempre, contra el presupuesto de DINERO real (API medida).

    `ok=False` NUNCA significa "para el trabajo": significa "no gastes dinero" — el que
    llama debe BAJAR de marcha (carril gratis / suelo determinista), no quedarse mudo.

    TRES CARRILES de prioridad sobre la última porción del día (de más a menos privilegio):
      · `interactivo=True` ({{TITULAR}} EN VIVO en la puerta del gateway) → llega hasta el último
        dólar: solo necesita un job_cap libre. Nadie más puede comerse su franja.
      · `esencial=True`    (Vega/vigía, healthcheck) → tira de la reserva esencial, PERO deja
        intacta la franja SOLO-interactiva (así el loop de fondo no la deja sin Claude).
      · rutina (cháchara de fondo, por defecto) → deja AMBAS franjas (esencial + interactiva).
    El techo de dinero total no cambia: solo cambia quién usa lo último.

    ESTADOS DE BLOQUEO (distinguibles por el caller):
      · motivo contiene "[tope_local]"  → el tope diario/mensual QUE NOSOTROS PUSIMOS se agotó.
        RECUPERABLE con 1 clic (aprobar_tope_hoy). El dinero en Anthropic sigue ahí.
      · motivo contiene "[prepago_agotado]" → la API devolvió 400 "Credit balance is too low".
        NO lo detecta esta función (eso lo ve _call_claude al leer la respuesta HTTP); aquí no
        podemos saberlo sin hacer un round-trip. Este marcador se usa en ia.py al recibir ese 400.
    Usar tipo_bloqueo(motivo) para obtener "tope_local" | "prepago_agotado" | None.
    """
    gastado = today_spent()
    tope = _tope_efectivo()
    restante = tope - gastado
    degradado = os.path.exists(DEGRADED)
    _, tj, tope_mes = _limits()
    job_cap = min(float(tope_job_usd), tj) if tope_job_usd is not None else tj
    # Exigir presupuesto de un job ENTERO para arrancar → el rebase máximo queda acotado a
    # un job_cap (H4), en vez de permitir arrancar con casi nada y dispararse. Lo NO esencial
    # debe además dejar intacta la reserva (job_cap + reserva); lo esencial solo el job_cap.
    reserva_ese = tope * RESERVA_ESENCIAL_FRAC
    reserva_int = tope * RESERVA_INTERACTIVA_FRAC
    if interactivo:
        necesario = job_cap                              # {{TITULAR}} en vivo: hasta el último dólar
        etiqueta = "interactivo"
    elif esencial:
        necesario = job_cap + reserva_int                # esencial deja la franja SOLO-interactiva
        etiqueta = "esencial"
    else:
        necesario = job_cap + reserva_int + reserva_ese  # la rutina deja ambas franjas
        etiqueta = "rutina"
    etiqueta += " conservador" if degradado else ""
    # Tope MENSUAL: segunda barrera. El restante del mes = tope_mes − suma de los días (derivada).
    # Se exige solo el job_cap (no la reserva diaria) — la reserva protege la última porción del DÍA.
    restante_mes = tope_mes - month_spent()
    if restante_mes < job_cap:
        # [tope_local] = tope que NOSOTROS pusimos; el prepago de Anthropic sigue intacto.
        # Recuperable con 1 clic (aprobar_tope_hoy / «sube» por Telegram).
        motivo = ("[tope_local] tope MENSUAL alcanzado para %s "
                 "(mes gastado: $%.4f de $%.2f fijado por nosotros; "
                 "el saldo de Anthropic sigue ahí — sube el tope con 1 clic)" % (
                     etiqueta, tope_mes - restante_mes, tope_mes))
        if esencial:
            _nudge_bloqueo_esencial(motivo)   # no-mudo (11-jul): antes callaba del todo
        return (False, motivo, min(restante, restante_mes))
    if restante < necesario:
        # [tope_local] = tope diario que NOSOTROS pusimos; el prepago de Anthropic sigue intacto.
        motivo = ("[tope_local] tope DIARIO alcanzado para %s "
                 "(hoy gastado: $%.4f de $%.2f fijado por nosotros; "
                 "el saldo de Anthropic sigue ahí — sube el tope con 1 clic)" % (
                     etiqueta, gastado, tope))
        if esencial:
            _nudge_bloqueo_esencial(motivo)   # no-mudo (11-jul): antes callaba del todo
        return (False, motivo, restante)
    return (True, "ok %s (restante diario %.4f de %.2f; mes %.4f de %.2f)" % (
        etiqueta, restante, tope, restante_mes, tope_mes), restante)


def tipo_bloqueo(motivo):
    """Clasifica el motivo de un ok=False de check_before_job.
    Devuelve: "tope_local" (tope nuestro, recuperable con 1 clic) |
              "prepago_agotado" (crédito Anthropic a 0, hay que recargar) | None (otro/ok).
    Diseñado para que ia.py y los callers distingan los dos casos sin parsear texto frágil."""
    if not motivo:
        return None
    if "[tope_local]" in motivo:
        return "tope_local"
    if "[prepago_agotado]" in motivo:
        return "prepago_agotado"
    return None


def _coste_de(entrada, tope_job_usd):
    """Extrae un USD del dict de run_agent (--output-format json) o de un número.
    Si no hay coste fiable → PESIMISTA (= tope del job), jamás 0."""
    return _coste_detalle(entrada, tope_job_usd)[0]


def _modelos_de(obj):
    """{modelo: usd} de `modelUsage` del JSON del CLI de Claude, o None si no lo trae.
    Solo nombres y cifras: nada del contenido del trabajo."""
    mu = obj.get("modelUsage") if isinstance(obj, dict) else None
    if not isinstance(mu, dict):
        return None
    out = {}
    for m, u in mu.items():
        c = u.get("costUSD") if isinstance(u, dict) else None
        if isinstance(c, (int, float)) and math.isfinite(c):
            out[str(m)] = round(float(c), 6)
    return out or None


def _coste_detalle(entrada, tope_job_usd):
    """(usd, pesimista, modelos). `pesimista` = el trabajo NO informó de su coste y se apunta el
    de por defecto; `modelos` = coste por modelo si el JSON lo trae. Misma regla que _coste_de."""
    pesimista = min(float(tope_job_usd), _limits()[1]) if tope_job_usd is not None else _limits()[1]
    if isinstance(entrada, (int, float)):
        return float(entrada), False, None
    if isinstance(entrada, dict):
        v = entrada.get("total_cost_usd", entrada.get("cost_usd"))
        if isinstance(v, (int, float)):
            return float(v), False, _modelos_de(entrada)
        return pesimista, True, None
    if isinstance(entrada, str):
        s = entrada.strip()
        if not s:
            return pesimista, True, None
        try:
            obj = json.loads(s)
            return _coste_detalle(obj, tope_job_usd)
        except Exception:
            try:
                return float(s), False, None
            except Exception:
                return pesimista, True, None
    return pesimista, True, None


def aprobar_tope_hoy(monto=None):
    """Acto de {{TITULAR}} (1 clic): sube el tope de gasto SOLO-HOY escribiendo override-YYYY-MM-DD.json,
    que _limits() ya lee y AUTO-EXPIRA mañana. Sin `monto` → al techo absoluto del código ($30): el
    máximo margen para que las tareas hacia NED terminen el día. El clamp absoluto sigue mandando
    (nunca por encima de TOPE_MAX_ABSOLUTO). Determinista, sin red, sin LLM — por eso lo puede
    disparar bot_telegram al recibir «sube» aunque Claude esté sin saldo (si necesitara a Claude para
    aprobar el gasto que desbloquea a Claude, sería un deadlock). Devuelve el tope efectivo resultante."""
    _ensure_dirs()
    target = TOPE_MAX_ABSOLUTO if monto is None else float(monto)
    if not math.isfinite(target):
        target = TOPE_MAX_ABSOLUTO
    target = min(target, TOPE_MAX_ABSOLUTO)
    ovr = os.path.join(COST, "override-" + datetime.now().strftime("%Y-%m-%d") + ".json")
    tmp = ovr + ".tmp"
    payload = {"tope_diario_usd": target, "motivo": "aprobado por {{TITULAR}} (sube)",
               "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, ovr)
    except Exception as e:
        _panic("no pude escribir el override de aprobación (%r)" % e)
    return _limits()[0]


def add_cost(entrada, *, job_id=None, tope_job_usd=None, via="api"):
    """Suma el coste del job al ledger del día (atómico, bajo lock). Devuelve el USD sumado.

    DOS CUBOS (25-jul-26): `gastado_usd` es DINERO REAL (API medida) y es el único que gatean los
    topes; `cuota_suscripcion_usd` es lo que habría costado por API lo que corrió con el plan Max
    — se guarda para ver el volumen, pero no frena nada. Cada evento lleva su `via`. Los eventos
    viejos no la llevan: se leen como "api" (conservador, cuentan como dinero)."""
    usd, pesimista, modelos = _coste_detalle(entrada, tope_job_usd)
    usd = max(0.0, usd)   # suelo: un coste negativo no resta (H7)
    # Cortacircuitos «de golpe» ({{TITULAR}} 27/6/26): un único cargo >= TOPE_GOLPE_USD es un runaway →
    # CÓDIGO ROJO (para todo + avisa fuerte). Fail-safe: si codigo_rojo no carga, no rompe el conteo.
    if usd >= TOPE_GOLPE_USD:
        try:
            import codigo_rojo
            codigo_rojo.trigger(
                "Gasto de golpe: un job gastó $%.2f (≥ tope de golpe $%.0f)" % (usd, TOPE_GOLPE_USD),
                "job_id=%s. Freno automático del cost_guard: posible bucle/runaway de gasto." % job_id)
        except Exception as _e:
            sys.stderr.write("cost_guard: no pude disparar codigo_rojo ante gasto de golpe: %r\n" % (_e,))
    _lock()
    try:
        d = _read_today()
        if via == "suscripcion":
            d["cuota_suscripcion_usd"] = round(
                float(d.get("cuota_suscripcion_usd", 0.0)) + usd, 6)
        else:
            d["gastado_usd"] = round(float(d.get("gastado_usd", 0.0)) + usd, 6)
        d["n_jobs"] = int(d.get("n_jobs", 0)) + 1
        d["actualizado"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        ev = d.get("eventos", [])
        ev.append({"ts": d["actualizado"], "job": job_id, "usd": round(usd, 6), "via": via})
        d["eventos"] = ev[-MAX_EVENTOS:]
        d["tope_diario_usd"] = _limits()[0]
        _write_today(d)
        # Registro COMPLETO (25-sep-26): el JSON del día recorta a MAX_EVENTOS y así no había forma
        # de saber en qué se fue el dinero (el 24-sep, 25 apuntes a la vista de $407). Aquí va cada
        # apunte, sin recorte. Después del contador y sin tumbarlo: el dinero manda, el detalle
        # informa; si no se pudo escribir, `desglose` lo declara como gasto fuera del registro.
        linea = {"ts": d["actualizado"], "job": job_id, "usd": round(usd, 6), "via": via,
                 "pesimista": pesimista}
        if modelos:
            linea["modelos"] = modelos
        _append_detalle(d.get("fecha") or d["actualizado"][:10], linea)
        return usd
    finally:
        _unlock()


def _detalle_path(dia):
    return os.path.join(COST, "detalle-%s.jsonl" % dia)


def _append_detalle(dia, linea):
    """Añade un apunte al registro completo del día. Nunca lanza (lo llama add_cost bajo el lock)."""
    try:
        p = _detalle_path(dia)
        fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(linea, ensure_ascii=False) + "\n")
    except Exception as e:
        sys.stderr.write("cost_guard: no pude escribir el registro de detalle (%r)\n" % (e,))


def _agente_de_cola(job_id, _cache={}):
    """Agente de un job de la cola, sacado de su JSON en tools/state/queue/*/…-<id>.json.
    None si no se encuentra; "" si el job no tiene agente."""
    if job_id in _cache:
        return _cache[job_id]
    res = None
    q = os.path.join(STATE, "queue")
    try:
        for sub in sorted(os.listdir(q)):
            dd = os.path.join(q, sub)
            if not os.path.isdir(dd):
                continue
            for fn in os.listdir(dd):
                if fn.endswith("-%s.json" % job_id):
                    try:
                        res = json.load(open(os.path.join(dd, fn), encoding="utf-8")).get("agente") or ""
                    except Exception:
                        continue
                    break
            if res is not None:
                break
    except Exception:
        pass
    _cache[job_id] = res
    return res


def origen_de(job_id):
    """A quién se le carga un apunte. `agente-AAAAMMDDTHHMMSS` → el agente (run_agent);
    `api-*` → esa API; id de la cola → `cola:<agente>`; lo demás, el id tal cual."""
    j = str(job_id or "?")
    if j.startswith("api-"):
        return j
    base, _, sello = j.rpartition("-")
    if _ and len(sello) == 15 and sello[8] == "T" and (sello[:8] + sello[9:]).isdigit():
        return base or "(agente sin nombre)"
    if j.startswith("ia-claude-"):
        return "ia-claude"
    if len(j) == 10 and all(c in "0123456789abcdef" for c in j):
        ag = _agente_de_cola(j)
        if ag is None:
            return "cola:(job no encontrado)"
        return "cola:%s" % (ag or "(sin agente)")
    return j


def desglose(dia=None):
    """En qué se fue el dinero de un día, desde el registro completo (detalle-AAAA-MM-DD.jsonl).
    Agrupa por origen y por modelo, separa lo pesimista y declara lo que NO está en el registro
    (días anteriores al 25-sep-26, o apuntes que no se pudieron escribir) en vez de callárselo."""
    dia = dia or datetime.now().strftime("%Y-%m-%d")
    total_api = total_sus = 0.0
    p_dia = os.path.join(COST, dia + ".json")
    if os.path.exists(p_dia):
        try:
            dd = json.load(open(p_dia, encoding="utf-8"))
            total_api = float(dd.get("gastado_usd", 0.0))
            total_sus = float(dd.get("cuota_suscripcion_usd", 0.0))
        except Exception:
            pass
    origenes, modelos = {}, {}
    reg = {"api": 0.0, "suscripcion": 0.0}
    pes = {"usd": 0.0, "n": 0}
    n = malas = 0
    p = _detalle_path(dia)
    if os.path.exists(p):
        for ln in open(p, encoding="utf-8"):
            try:
                e = json.loads(ln)
                usd = float(e.get("usd", 0.0))
            except Exception:
                malas += 1
                continue
            n += 1
            via = "suscripcion" if e.get("via") == "suscripcion" else "api"
            reg[via] += usd
            o = origenes.setdefault(origen_de(e.get("job")),
                                    {"usd": 0.0, "n": 0, "pesimista_usd": 0.0, "suscripcion_usd": 0.0})
            o["n"] += 1
            if via == "suscripcion":
                o["suscripcion_usd"] += usd
            else:
                o["usd"] += usd
            if e.get("pesimista"):
                o["pesimista_usd"] += usd
                pes["usd"] += usd
                pes["n"] += 1
            if via == "api":
                ms = e.get("modelos")
                if isinstance(ms, dict) and ms:
                    for m, c in ms.items():
                        modelos[m] = modelos.get(m, 0.0) + float(c)
                else:
                    k = "(pesimista, sin dato)" if e.get("pesimista") else "(sin desglose por modelo)"
                    modelos[k] = modelos.get(k, 0.0) + usd
    r6 = lambda x: round(x, 6)
    return {
        "dia": dia, "apuntes": n, "lineas_ilegibles": malas,
        "gastado_usd": r6(total_api), "en_registro_usd": r6(reg["api"]),
        "fuera_del_registro_usd": r6(max(0.0, total_api - reg["api"])),
        "cuota_suscripcion_usd": r6(total_sus), "suscripcion_en_registro_usd": r6(reg["suscripcion"]),
        "pesimista_usd": r6(pes["usd"]), "pesimista_n": pes["n"],
        "por_origen": {k: {kk: (r6(vv) if isinstance(vv, float) else vv) for kk, vv in v.items()}
                       for k, v in sorted(origenes.items(), key=lambda kv: -(kv[1]["usd"] + kv[1]["suscripcion_usd"]))},
        "por_modelo": {k: r6(v) for k, v in sorted(modelos.items(), key=lambda kv: -kv[1])},
    }


def _texto_desglose(r):
    L = ["Gasto del %s: $%.2f de API" % (r["dia"], r["gastado_usd"])
         + (" (+ $%.2f de cuota de suscripción, no es dinero)" % r["cuota_suscripcion_usd"]
            if r["cuota_suscripcion_usd"] else "")]
    if r["fuera_del_registro_usd"] > 0.005:
        L.append("⚠️  $%.2f NO están en el registro (día anterior al registro completo o apuntes "
                 "perdidos): de eso no se sabe en qué se fue." % r["fuera_del_registro_usd"])
    L.append("En el registro: $%.2f en %d apuntes · pesimista (el trabajo no informó y se "
             "apuntó el de por defecto): $%.2f en %d" % (r["en_registro_usd"], r["apuntes"],
                                                        r["pesimista_usd"], r["pesimista_n"]))
    if r["lineas_ilegibles"]:
        L.append("⚠️  %d líneas del registro ilegibles (no suman arriba)" % r["lineas_ilegibles"])
    L.append("")
    L.append("%-34s %9s %5s %11s" % ("Origen", "USD", "n", "pesimista"))
    for k, v in r["por_origen"].items():
        extra = "  (+$%.2f suscr.)" % v["suscripcion_usd"] if v["suscripcion_usd"] else ""
        L.append("%-34s %9.2f %5d %11.2f%s" % (k[:34], v["usd"], v["n"], v["pesimista_usd"], extra))
    L.append("")
    L.append("%-34s %9s" % ("Modelo (solo API)", "USD"))
    for k, v in r["por_modelo"].items():
        L.append("%-34s %9.2f" % (k[:34], v))
    return "\n".join(L)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def _arg(a, name, default=None):
    return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else default


def _via(a):
    """--via api|suscripcion. FAIL-CLOSED: cualquier cosa que no sea exactamente 'suscripcion'
    (incl. ausente o mal escrita) cuenta como dinero real. Un typo nunca abre la mano."""
    return "suscripcion" if _arg(a, "--via") == "suscripcion" else "api"


def main(argv):
    cmd = argv[0] if argv else "today"
    a = argv[1:]
    if cmd == "check":
        tj = _arg(a, "--tope-job")
        ok, motivo, restante = check_before_job(float(tj) if tj else None,
                                                esencial="--esencial" in a,
                                                interactivo="--interactivo" in a,
                                                via=_via(a))
        print(motivo)
        return 0 if ok else 1
    if cmd == "add":
        tj = _arg(a, "--tope-job")
        job_id = _arg(a, "--job")
        if "--stdin" in a:
            entrada = sys.stdin.read()
        elif _arg(a, "--usd"):
            entrada = float(_arg(a, "--usd"))
        else:
            entrada = _arg(a, "--json", "")
        usd = add_cost(entrada, job_id=job_id, tope_job_usd=float(tj) if tj else None,
                       via=_via(a))
        print("%.6f" % usd)
        return 0
    if cmd == "today":
        print(json.dumps(_read_today(), ensure_ascii=False, indent=2))
        return 0
    if cmd == "month":
        d, j, m = _limits()
        gm = month_spent()
        print(json.dumps({"mes": datetime.now().strftime("%Y-%m"), "gastado_usd": gm,
                          "tope_mensual_usd": m, "restante_usd": round(m - gm, 6),
                          "tope_diario_usd": d}, ensure_ascii=False, indent=2))
        return 0
    if cmd == "aprobar":
        m = _arg(a, "--monto")
        nuevo = aprobar_tope_hoy(float(m) if m else None)
        print("%.2f" % nuevo)
        return 0
    if cmd == "recarga":
        # {{TITULAR}} acaba de recargar el saldo prepago de Anthropic: lo anotamos para poder avisar
        # ANTES de que llegue a 0. Uso: cost_guard.py recarga 50
        monto = a[0] if a else _arg(a, "--monto")
        if not monto:
            print("uso: cost_guard.py recarga <monto_usd>   (p. ej. recarga 50)")
            return 2
        rec = registrar_recarga(float(monto))
        print("Recarga anotada: $%.2f el %s. Avisaré al 75%% y al 90%% del gasto." % (rec["monto_usd"], rec["fecha"]))
        return 0
    if cmd == "desglose":
        dia = _arg(a, "--dia")
        if dia and not (len(dia) == 10 and dia[4] == "-" and dia[7] == "-"
                        and (dia[:4] + dia[5:7] + dia[8:]).isdigit()):
            print("uso: cost_guard.py desglose [--dia AAAA-MM-DD] [--json]")
            return 2
        r = desglose(dia)
        print(json.dumps(r, ensure_ascii=False, indent=2) if "--json" in a else _texto_desglose(r))
        return 0
    if cmd == "credito" and a and a[0] in ("ok", "agotado"):
        marcar_credito(a[0] == "ok")
        return 0
    if cmd == "saldo":
        sp = saldo_prepago()
        if sp is None:
            print("Sin recarga registrada. Anota una con: cost_guard.py recarga <monto>")
            return 0
        print(json.dumps(sp, ensure_ascii=False, indent=2))
        return 0
    print("uso: cost_guard.py [check [--tope-job N] [--esencial] [--interactivo] [--via api|suscripcion] | add (--stdin|--usd N|--json '..') [--job id] [--via api|suscripcion] | today | month | aprobar [--monto N] | recarga <monto> | saldo | desglose [--dia AAAA-MM-DD] [--json]]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
