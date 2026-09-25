#!/usr/bin/env python3
"""tools/observabilidad.py — Pieza 8 del arnés agéntico: observabilidad por ejecución.

Registra una traza estructurada por ejecución de job/agente en tools/state/observabilidad/
en JSONL append-only (una línea por ejecución):

  {ts_ini, ts_fin, agente, job, duracion_ms, tokens_in, tokens_out, eur_estimado,
   resultado (ok|fail), error_type, stack_trace}

Principios:
  · DETERMINISTA, sin LLM. Coste = 0 tokens.
  · APPEND-ONLY (sin borrado/sobreescritura): la traza es inmutable para auditoría.
  · Un fichero por día: observabilidad-YYYY-MM-DD.jsonl (rotación natural, sin cron).
  · Integración con coste.py: si la herramienta que se instrumenta conoce sus tokens,
    puede pasarlos; si no, los campos quedan como None y se puede rellenar a posteriori.

API pública (usa la mínima que necesites):

    # 1. Context manager (recomendado — captura excepciones y mide duración automáticamente)
    from tools.observabilidad import traza
    with traza("orquestador", "procesar_intencion") as t:
        resultado = hacer_algo()
        t.tokens(input=1200, output=340)   # opcional

    # 2. Función directa (para código que no puede usar with)
    from tools.observabilidad import registrar
    registrar("dispatcher", "despachar_job", duracion_ms=450, resultado="ok")

    # 3. Decorador
    from tools.observabilidad import observar
    @observar("mi_tool", "ejecutar")
    def mi_funcion(...): ...

CLI:
    python3 tools/observabilidad.py               # resumen de hoy
    python3 tools/observabilidad.py --tail 20     # últimas 20 trazas
    python3 tools/observabilidad.py --agente X    # filtra por agente
    python3 tools/observabilidad.py --fallos      # solo fallos
    python3 tools/observabilidad.py --dias 7      # ventana de N días

--- Cómo instrumentar una tool existente (3 líneas) ---
# En cualquier tool del repo:
#   import observabilidad
#   with observabilidad.traza("nombre_tool", "nombre_operacion") as t:
#       resultado = la_logica_existente(args)
#       t.tokens(input=in_tok, output=out_tok)   # si la tool conoce sus tokens
# El context manager captura excepciones, las re-lanza, y deja la traza (resultado="fail").
# Para el lazo: el dispatcher (tools/dispatcher*.py) o btp_run.sh son el mejor punto de
# inyección (cubren TODAS las tools de un golpe; mínima invasión de código).
"""

import contextlib
import functools
import json
import os
import sys
import time
import traceback

# ─── resolución de casa base (patrón canónico del repo) ───────────────────────
# BTP_REPO o ~/claudecode: el estado vivo vive ahí, no en el worktree del agente.
# BTP_STATE_DIR permite aislamiento en tests (igual que seguimiento.py, vigia.py, etc.).
REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
OBS_DIR = os.path.join(STATE, "observabilidad")

# ─── integración con coste.py (opcional, no rompe si no está disponible) ──────
# TODO: cuando el dispatcher conozca los tokens por ejecución, importar coste.precios()
# aquí y calcular eur_estimado = coste.usd(tok, pr). Por ahora el campo queda en None
# y se puede enriquecer con un post-proceso sobre el JSONL leyendo coste.scan().
def _eur_estimado(modelo, tokens_in, tokens_out):
    """Estima el coste en USD dado modelo y tokens. Devuelve None si no se puede calcular.
    Sin LLM, sin red: solo usa las tablas de coste.py (stdlib)."""
    if not modelo or (tokens_in is None and tokens_out is None):
        return None
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import coste
        tabla = coste.precios()
        tok = {"input": tokens_in or 0, "output": tokens_out or 0,
               "cache_read": 0, "cache_write": 0}
        pr = coste.price_for(modelo, tabla)
        return round(coste.usd(tok, pr), 6) if pr else None
    except Exception:
        return None


# ─── escritura (append-only, atómica por línea) ───────────────────────────────
def _ruta_hoy():
    os.makedirs(OBS_DIR, exist_ok=True)
    return os.path.join(OBS_DIR, "observabilidad-%s.jsonl" % time.strftime("%Y-%m-%d"))


def _escribir(rec):
    """Escribe una línea JSONL de forma atómica (O_APPEND). Nunca lanza: si falla, stderr."""
    try:
        line = (json.dumps(rec, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        path = _ruta_hoy()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except Exception as exc:
        sys.stderr.write("observabilidad: no pude escribir traza: %r\n" % (exc,))


# ─── API pública ──────────────────────────────────────────────────────────────
def registrar(agente, job, *, duracion_ms=None, tokens_in=None, tokens_out=None,
              modelo=None, resultado="ok", error_type=None, stack_trace=None,
              ts_ini=None, severidad=None):
    """Registra una traza ya terminada. Úsalo cuando el contexto manager no encaja.

    Parámetros:
      agente      — nombre del agente o tool (string corto, p.ej. "dispatcher")
      job         — operación dentro del agente (p.ej. "despachar_job_X")
      duracion_ms — duración en milisegundos (int o float; None si desconocida)
      tokens_in   — tokens de entrada consumidos por el LLM (int; None si no aplica)
      tokens_out  — tokens de salida generados (int; None si no aplica)
      modelo      — nombre del modelo LLM usado (para estimar coste; None si no aplica)
      resultado   — "ok" o "fail"
      error_type  — clase de la excepción (p.ej. "TimeoutError")
      stack_trace — traceback completo como string (solo en resultado="fail")
      ts_ini      — timestamp ISO de inicio (se usa time.strftime si None)
    """
    # Timestamps consistentes (local, sin Z — igual que la rotación de ficheros y el
    # filtrado por día). Los daemons pasan ts_ini en UTC-Z; lo parseamos y lo
    # convertimos a local, y de paso CALCULAMOS duracion_ms si no vino dada (un
    # registrar() directo no podía medirla solo). El context manager ya pasa la
    # duración medida por monotonic, así que ahí no se recalcula.
    import datetime as _dt
    now_dt = _dt.datetime.now()
    now = now_dt.strftime("%Y-%m-%dT%H:%M:%S")
    ts_ini_norm = now
    if ts_ini:
        try:
            _ini = _dt.datetime.fromisoformat(str(ts_ini).replace("Z", "+00:00"))
            if _ini.tzinfo is not None:
                _ini = _ini.astimezone().replace(tzinfo=None)  # UTC → local naive
            ts_ini_norm = _ini.strftime("%Y-%m-%dT%H:%M:%S")
            if duracion_ms is None:
                _d = (now_dt - _ini).total_seconds() * 1000
                if _d >= 0:
                    duracion_ms = int(_d)
        except Exception:
            ts_ini_norm = str(ts_ini)  # formato raro: guárdalo tal cual, no rompas
    rec = {
        "ts_ini": ts_ini_norm,
        "ts_fin": now,
        "agente": str(agente),
        "job": str(job),
        "duracion_ms": int(duracion_ms) if duracion_ms is not None else None,
        "tokens_in": int(tokens_in) if tokens_in is not None else None,
        "tokens_out": int(tokens_out) if tokens_out is not None else None,
        "modelo": modelo,
        "eur_estimado": _eur_estimado(modelo, tokens_in, tokens_out),
        "resultado": resultado,
        "error_type": error_type,
        "stack_trace": stack_trace,
        "severidad": severidad,   # taxonomía de errores.py (None en trazas de ejecución normales)
    }
    _escribir(rec)
    return rec


class _TrazaCtx:
    """Objeto devuelto por el context manager `traza()`.
    Permite enriquecer la traza con tokens/modelo durante la ejecución."""

    def __init__(self, agente, job):
        self._agente = agente
        self._job = job
        self._t0 = time.monotonic()
        self._ts_ini = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._tokens_in = None
        self._tokens_out = None
        self._modelo = None

    def tokens(self, *, input=None, output=None, modelo=None):
        """Registra tokens consumidos. Llámalo desde dentro del bloque `with`."""
        if input is not None:
            self._tokens_in = int(input)
        if output is not None:
            self._tokens_out = int(output)
        if modelo is not None:
            self._modelo = str(modelo)

    def _cerrar(self, resultado, error_type=None, stack_trace=None):
        dur_ms = int((time.monotonic() - self._t0) * 1000)
        registrar(
            self._agente, self._job,
            duracion_ms=dur_ms,
            tokens_in=self._tokens_in,
            tokens_out=self._tokens_out,
            modelo=self._modelo,
            resultado=resultado,
            error_type=error_type,
            stack_trace=stack_trace,
            ts_ini=self._ts_ini,
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._cerrar("ok")
        else:
            tb = traceback.format_exc()
            self._cerrar("fail", error_type=exc_type.__name__, stack_trace=tb)
        return False  # re-lanza la excepción


@contextlib.contextmanager
def traza(agente, job):
    """Context manager que mide duración y captura excepciones.

    Uso:
        with traza("mi_agente", "mi_operacion") as t:
            resultado = hacer_algo()
            t.tokens(input=1500, output=200, modelo="claude-sonnet-4-6")
    """
    ctx = _TrazaCtx(agente, job)
    with ctx:
        yield ctx


def observar(agente, job=None):
    """Decorador que envuelve una función con una traza de observabilidad.

    Uso:
        @observar("mi_tool", "ejecutar")
        def mi_funcion(x, y): ...
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            nombre_job = job or fn.__name__
            with traza(agente, nombre_job):
                return fn(*args, **kwargs)
        return wrapper
    return decorator


# ─── lectura (CLI) ────────────────────────────────────────────────────────────
def _leer_dias(n_dias):
    """Lee todas las trazas de los últimos n_dias. Devuelve lista de dicts."""
    from datetime import datetime, timedelta
    hoy = datetime.now().date()
    registros = []
    for i in range(n_dias):
        dia = hoy - timedelta(days=i)
        path = os.path.join(OBS_DIR, "observabilidad-%s.jsonl" % dia.isoformat())
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        try:
                            registros.append(json.loads(ln))
                        except Exception:
                            pass
        except Exception:
            pass
    return registros


def _resumen(registros):
    """Devuelve un dict de resumen estadístico sobre una lista de trazas."""
    if not registros:
        return {"total": 0}
    total = len(registros)
    fallos = [r for r in registros if r.get("resultado") == "fail"]
    ok = [r for r in registros if r.get("resultado") == "ok"]
    durs = [r["duracion_ms"] for r in registros if r.get("duracion_ms") is not None]
    por_agente = {}
    for r in registros:
        ag = r.get("agente", "?")
        s = por_agente.setdefault(ag, {"total": 0, "fallos": 0})
        s["total"] += 1
        if r.get("resultado") == "fail":
            s["fallos"] += 1
    return {
        "total": total,
        "ok": len(ok),
        "fallos": len(fallos),
        "tasa_fallo_pct": round(100 * len(fallos) / total, 1),
        "dur_media_ms": round(sum(durs) / len(durs)) if durs else None,
        "dur_max_ms": max(durs) if durs else None,
        "por_agente": por_agente,
    }


# ─── supersede (3/7/26): ¿hubo una pasada OK más reciente que tape un heartbeat malo? ────────────
# El mismo patrón que ya usaba RUTINAS_NED de healthcheck.py (comparación de recencia), pero contra
# la fuente MÁS FINA que tenemos: la traza append-only de observabilidad, no solo el heartbeat (que
# algunos daemons —correo-triaje, bot-telegram— NUNCA sobreescriben porque comparten BTP_AGENT con
# otro heartbeat, p.ej. 'asistente'). Bug real (3/7): el heartbeat 'correo-triaje' quedó clavado en
# 'fallo' de las 06:26 aunque el triaje se recuperase de verdad a las 09:11 — nadie comprobó si una
# pasada POSTERIOR había ido bien. Fail-soft: cualquier excepción → False (nunca enmascara por error).
def hubo_ok_desde(agente, ts_iso, dias=2):
    """True si existe una traza de `agente` con resultado='ok' y ts_fin ESTRICTAMENTE posterior a
    `ts_iso` (str ISO 'YYYY-MM-DDTHH:MM:SS', con o sin 'Z'/tz). `dias` acota cuántos ficheros diarios
    se leen (2 por defecto: hoy + ayer, suficiente para un heartbeat que no lleva más de un día
    rancio). Si `ts_iso` no se puede interpretar, o no hay trazas, devuelve False (conservador: sin
    prueba de recuperación, NO se tapa la alerta — más vale un aviso de más que silenciar un fallo
    real)."""
    import datetime as _dt
    if not ts_iso:
        return False
    try:
        s = str(ts_iso).strip()
        ref = _dt.datetime.strptime(s[:19].replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return False
    try:
        registros = _leer_dias(max(1, int(dias)))
    except Exception:
        return False
    for r in registros:
        if r.get("agente") != agente or r.get("resultado") != "ok":
            continue
        fin = r.get("ts_fin") or r.get("ts_ini")
        try:
            fin_dt = _dt.datetime.strptime(str(fin)[:19].replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
        except Exception:
            continue
        if fin_dt > ref:
            return True
    return False


def ultimo_resultado(agente, dias=2):
    """El resultado ('ok'/'fail'/...) de la traza MÁS RECIENTE de `agente`, o None si no hay
    ninguna en la ventana de `dias`. Hermano de hubo_ok_desde() para el caso en que NO hay un
    ts de referencia fiable que comparar (p.ej. el exit-code de `launchctl list`, que no trae
    marca de tiempo del momento en que falló) — aquí el criterio es más simple: ¿la ÚLTIMA
    pasada conocida de este agente fue 'ok' o 'fail'? Si la más reciente es 'ok', se considera
    recuperado (tapa); si es 'fail' y no hay ninguna 'ok' más nueva, sigue siendo problema.
    Fail-soft: cualquier excepción → None (conservador, no tapa nada)."""
    try:
        registros = _leer_dias(max(1, int(dias)))
    except Exception:
        return None
    de_agente = [r for r in registros if r.get("agente") == agente]
    if not de_agente:
        return None
    de_agente.sort(key=lambda r: str(r.get("ts_fin") or r.get("ts_ini") or ""))
    return de_agente[-1].get("resultado")


# ─── tarjeta para el Observatorio ────────────────────────────────────────────
def estado_observabilidad():
    """Lee las trazas de hoy y ayer y devuelve el estado para el Observatorio.
    Solo-lectura, sin LLM. Encaja el patrón _safe(fn) del Observatorio."""
    registros = _leer_dias(2)
    from datetime import datetime
    hoy_str = datetime.now().date().isoformat()
    hoy = [r for r in registros if (r.get("ts_ini") or "")[:10] == hoy_str]
    fallos_recientes = sorted(
        [r for r in registros if r.get("resultado") == "fail"],
        key=lambda r: r.get("ts_ini", ""),
        reverse=True,
    )[:5]
    res_hoy = _resumen(hoy)
    ultimas = sorted(registros, key=lambda r: r.get("ts_ini", ""), reverse=True)[:8]
    return {
        "hoy": res_hoy,
        "fallos_recientes": [
            {
                "ts": r.get("ts_ini", ""),
                "agente": r.get("agente", "?"),
                "job": r.get("job", "?"),
                "error_type": r.get("error_type"),
                "dur_ms": r.get("duracion_ms"),
            }
            for r in fallos_recientes
        ],
        "ultimas": [
            {
                "ts": r.get("ts_ini", ""),
                "agente": r.get("agente", "?"),
                "job": r.get("job", "?"),
                "resultado": r.get("resultado"),
                "dur_ms": r.get("duracion_ms"),
                "eur": r.get("eur_estimado"),
            }
            for r in ultimas
        ],
    }


# ─── CLI ─────────────────────────────────────────────────────────────────────
def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]

    # opciones
    tail = None
    agente_filtro = None
    solo_fallos = "--fallos" in argv
    dias = 1
    want_json = "--json" in argv

    if "--tail" in argv:
        i = argv.index("--tail")
        try:
            tail = int(argv[i + 1])
        except (IndexError, ValueError):
            tail = 20

    if "--agente" in argv:
        i = argv.index("--agente")
        try:
            agente_filtro = argv[i + 1]
        except IndexError:
            pass

    if "--dias" in argv:
        i = argv.index("--dias")
        try:
            dias = int(argv[i + 1])
        except (IndexError, ValueError):
            dias = 7

    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0

    registros = _leer_dias(dias)

    if agente_filtro:
        registros = [r for r in registros if r.get("agente") == agente_filtro]
    if solo_fallos:
        registros = [r for r in registros if r.get("resultado") == "fail"]

    if tail is not None:
        registros_mostrados = sorted(registros, key=lambda r: r.get("ts_ini", ""), reverse=True)[:tail]
        registros_mostrados = list(reversed(registros_mostrados))
    else:
        registros_mostrados = sorted(registros, key=lambda r: r.get("ts_ini", ""))

    if want_json:
        print(json.dumps({
            "resumen": _resumen(registros),
            "registros": registros_mostrados,
        }, ensure_ascii=False, indent=2))
        return 0

    # salida legible
    res = _resumen(registros)
    ventana = "hoy" if dias == 1 else ("últimos %d días" % dias)
    print("OBSERVABILIDAD · %s — %d ejecuciones (%d OK · %d fallos · %.1f%% fallo)" % (
        ventana, res.get("total", 0), res.get("ok", 0), res.get("fallos", 0),
        res.get("tasa_fallo_pct", 0.0)))
    if res.get("dur_media_ms") is not None:
        print("  Duración media: %d ms  |  máx: %d ms" % (res["dur_media_ms"], res["dur_max_ms"]))

    if not registros_mostrados:
        print("  (sin trazas en esta ventana)")
        return 0

    print()
    for r in registros_mostrados:
        ico = "OK" if r.get("resultado") == "ok" else "FAIL"
        ts = (r.get("ts_ini") or "")[-8:] or "?"   # solo la hora HH:MM:SS
        ag = r.get("agente", "?")
        job = r.get("job", "?")
        dur = ("%d ms" % r["duracion_ms"]) if r.get("duracion_ms") is not None else "?"
        coste_str = (" ~$%.5f" % r["eur_estimado"]) if r.get("eur_estimado") else ""
        print("  %s  [%s]  %s/%s  %s%s" % (ts, ico, ag, job, dur, coste_str))
        if r.get("resultado") == "fail" and r.get("error_type"):
            print("       Fallo: %s" % r["error_type"])
        if r.get("resultado") == "fail" and r.get("stack_trace") and "--verbose" in argv:
            for ln in (r["stack_trace"] or "").splitlines()[-4:]:
                print("         | %s" % ln)

    print()
    if res.get("por_agente"):
        print("Por agente:")
        for ag, s in sorted(res["por_agente"].items(), key=lambda kv: -kv[1]["total"]):
            print("  %-22s  %d ejecuciones  %d fallos" % (ag, s["total"], s["fallos"]))

    return 0


if __name__ == "__main__":
    sys.exit(main())
