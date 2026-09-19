#!/usr/bin/env python3
"""tools/errores.py — el sistema nervioso de errores de Polaris (pieza de unificación).

Punto ÚNICO al que todo error del sistema reporta. NO reemplaza a vigia/healthcheck/
codigo_rojo/cost_guard/borde: les da un VOCABULARIO COMÚN de severidad y un único punto
de escalado, para que ningún error se pierda y cada uno se trate según su gravedad.

Qué hace `registrar(...)`:
  1. CLASIFICA la severidad (si no se le da) por el tipo/patrón del error.
  2. DEJA TRAZA reusando observabilidad.py (mismo almacén JSONL, no se duplica).
  3. ESCALA de forma graduada según severidad, con anti-spam de 12h (igual que vigia):
       TRANSITORIO → solo log (aviso si se vuelve recurrente)
       CONFIG      → aviso si recurrente (no se reintenta)
       OPERATIVO   → aviso a {{TITULAR}} (el autofix lo hacen vigia/healthcheck)
       DEGRADADO   → aviso a {{TITULAR}} (el carril barato lo hacen cost_guard/run_agent)
       GOAL        → codigo_rojo.trigger (PARA TODO). SOLO explícito, nunca inferido.

Principios: DETERMINISTA, $0, sin LLM. FAIL-SAFE absoluto — registrar un error JAMÁS
puede romper al que llama (todo envuelto; si algo peta dentro, va a stderr y sigue).
El muro manda: el aviso sale por el choke-point (salida.py), respeta HALT.

CLI:
  python3 tools/errores.py --resumen [--dias N]   # cuántos errores, por severidad/origen
  python3 tools/errores.py --tail [N]             # últimos errores (vía observabilidad)
"""
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
ERR_DIR = os.path.join(STATE, "errores")
ALERTA_STATE = os.path.join(ERR_DIR, "last_alert.json")
ALERTA_COOLDOWN_H = 12   # anti-spam, igual que vigia.py / healthcheck.py
RECURRENTE_N = 3         # nº de veces hoy a partir del cual un CONFIG/TRANSITORIO avisa

# ─── Taxonomía única de severidad (una sola fuente de "gravedad → acción") ─────
TRANSITORIO = "transitorio"   # 429/timeout/red/overloaded → reintentar con backoff
CONFIG      = "config"        # schema/import/fichero → no reintentar, arreglar
OPERATIVO   = "operativo"     # daemon caído, bucle, disco, heartbeat → autofix+aviso
DEGRADADO   = "degradado"     # saldo/crédito/capacidad → carril barato o bloquear+avisar
GOAL        = "goal"          # amenaza NED/vacuna → codigo_rojo (PARA TODO)

SEVERIDADES = (TRANSITORIO, CONFIG, OPERATIVO, DEGRADADO, GOAL)

# Política por severidad: ¿se reintenta?, ¿avisa siempre / solo si recurrente / código rojo?
POLITICA = {
    TRANSITORIO: {"reintentar": True,  "aviso": "recurrente"},
    CONFIG:      {"reintentar": False, "aviso": "recurrente"},
    OPERATIVO:   {"reintentar": False, "aviso": "siempre"},
    DEGRADADO:   {"reintentar": False, "aviso": "siempre"},
    GOAL:        {"reintentar": False, "aviso": "codigo_rojo"},
}

# Patrones de inferencia (NUNCA infieren GOAL: parar el sistema exige decisión explícita).
_PAT_TRANSITORIO = ("timeout", "timed out", "429", "529", "overloaded", "rate limit",
                    "temporarily", "connection", "connreset", "read timed", "503", "502",
                    "temporarily unavailable")
_PAT_DEGRADADO = ("credit balance", "sin saldo", "insufficient", "tope", "prepago",
                  "quota", "billing")
_PAT_CONFIG = ("schema-invalido", "schema-desconocido", "no such file", "not found",
               "modulenotfound", "importerror", "no module named",
               # 31-jul-26: turnos agotados. El CLI sale con rc=1 y `subtype=error_max_turns`
               # cuando el agente se queda sin turnos. Reintentar el MISMO job con el MISMO
               # presupuesto vuelve a agotarlos: es permanente, no transitorio. Medido: el job
               # c4fb4d06fc lo intentó 3 veces (30-jul 19:24/19:28/19:32) y gastó 1.35 + 0.99 USD
               # para acabar igual. Va a dead-letter al primer intento y se cuenta con su motivo.
               "error_max_turns", "max_turns")
_TIPOS_TRANSITORIO = ("TimeoutError", "ConnectionError", "ConnectionResetError",
                      "ConnectionAbortedError", "BrokenPipeError")
_TIPOS_CONFIG = ("ImportError", "ModuleNotFoundError", "FileNotFoundError",
                 "KeyError", "AttributeError", "NameError", "TypeError")


def clasificar(error):
    """Infiere la severidad por el tipo y el texto del error. NUNCA devuelve GOAL
    (parar el sistema es decisión explícita). Default = OPERATIVO."""
    tipo = type(error).__name__ if isinstance(error, BaseException) else ""
    msg = str(error).lower()
    if tipo in _TIPOS_TRANSITORIO or any(p in msg for p in _PAT_TRANSITORIO):
        return TRANSITORIO
    if any(p in msg for p in _PAT_DEGRADADO):
        return DEGRADADO
    if tipo in _TIPOS_CONFIG or any(p in msg for p in _PAT_CONFIG):
        return CONFIG
    return OPERATIVO


# ─── recurrencia (lee el almacén de observabilidad, no duplica store) ──────────
def _veces_hoy(origen, error_type):
    """Cuántos fallos del mismo (origen, error_type) hay hoy en observabilidad."""
    try:
        import observabilidad
        regs = [r for r in observabilidad._leer_dias(1)
                if r.get("resultado") == "fail"
                and r.get("agente") == origen
                and (r.get("error_type") or "") == (error_type or "")]
        return len(regs)
    except Exception:
        return 0


# ─── anti-spam (mismo patrón que vigia.py) ────────────────────────────────────
def _debe_avisar(clave):
    """True si la clave es nueva o ya pasó la ventana de 12h. Persiste al avisar."""
    now = time.time()
    try:
        prev = json.load(open(ALERTA_STATE, encoding="utf-8"))
    except Exception:
        prev = {}
    claves_prev = set(prev.get("claves", []))
    elapsed_h = (now - prev.get("ts", 0)) / 3600.0
    if clave in claves_prev and elapsed_h < ALERTA_COOLDOWN_H:
        return False
    try:
        os.makedirs(ERR_DIR, exist_ok=True)
        nuevas = (claves_prev | {clave}) if elapsed_h < ALERTA_COOLDOWN_H else {clave}
        with open(ALERTA_STATE, "w", encoding="utf-8") as f:
            json.dump({"ts": now, "claves": sorted(nuevas)}, f, ensure_ascii=False)
    except Exception:
        pass
    return True


def _avisar(texto, *, critico=False):
    """Manda el aviso por el choke-point (respeta HALT). Fail-soft."""
    try:
        import salida
        if critico:
            salida.alerta_critica(texto)
        else:
            salida.report_to_titular(texto)
        return True
    except Exception as exc:
        sys.stderr.write("errores: no pude avisar: %r\n" % (exc,))
        return False


_TEXTO = {
    OPERATIVO: "Un componente del sistema está fallando",
    DEGRADADO: "Capacidad degradada (saldo/crédito/límite)",
    CONFIG: "Un fallo de configuración se repite",
    TRANSITORIO: "Un fallo transitorio se está repitiendo mucho",
}


# ─── API pública ──────────────────────────────────────────────────────────────
def registrar(origen, error, severidad=None, *, job="error", detalle="",
              escalar=True, **ctx):
    """Registra un error en el punto único, lo clasifica, deja traza y escala.

    origen     — agente/tool donde ocurrió (p.ej. "grok", "dispatcher")
    error      — la excepción (preferido) o un str con el mensaje
    severidad  — una de SEVERIDADES; si None se infiere (nunca GOAL automático)
    escalar    — si False, solo clasifica+traza (útil en tests/uso programático)

    Devuelve un dict {severidad, origen, error_type, recurrente, avisado, accion}.
    NUNCA lanza: registrar un error no puede romper al que llama.
    """
    try:
        es_exc = isinstance(error, BaseException)
        error_type = type(error).__name__ if es_exc else "error"
        stack = traceback.format_exc() if es_exc and sys.exc_info()[0] else None
        sev = severidad if severidad in SEVERIDADES else clasificar(error)

        # 1) traza en el almacén común (observabilidad), con la severidad.
        try:
            import observabilidad
            observabilidad.registrar(origen, job, resultado="fail",
                                     error_type=error_type, stack_trace=stack,
                                     severidad=sev)
        except Exception as exc:
            sys.stderr.write("errores: no pude trazar: %r\n" % (exc,))

        veces = _veces_hoy(origen, error_type)
        recurrente = veces >= RECURRENTE_N
        pol = POLITICA[sev]
        avisado = False

        if escalar:
            if pol["aviso"] == "codigo_rojo":          # GOAL: PARA TODO
                try:
                    import codigo_rojo
                    codigo_rojo.trigger("Error GOAL en %s" % origen,
                                        detalle or str(error))
                    avisado = True
                except Exception as exc:
                    sys.stderr.write("errores: fallo al disparar codigo_rojo: %r\n" % (exc,))
            elif pol["aviso"] == "siempre" or (pol["aviso"] == "recurrente" and recurrente):
                clave = "%s:%s:%s" % (sev, origen, error_type)
                if _debe_avisar(clave):
                    extra = " (×%d hoy)" % veces if recurrente else ""
                    texto = "🩺 %s — %s%s.\n%s" % (
                        _TEXTO.get(sev, "Error"), origen, extra,
                        (detalle or str(error))[:300])
                    avisado = _avisar(texto)

        return {"severidad": sev, "origen": origen, "error_type": error_type,
                "recurrente": recurrente, "veces_hoy": veces,
                "avisado": avisado, "reintentar": pol["reintentar"]}
    except Exception as exc:   # blindaje total: ni el propio errores.py rompe al llamante
        sys.stderr.write("errores: fallo interno al registrar: %r\n" % (exc,))
        return {"severidad": "desconocida", "origen": origen, "avisado": False,
                "reintentar": False}


class capturar:
    """Context manager: captura una excepción, la registra por el bus y la RE-LANZA.
    Uso para instrumentar un bloque:
        with errores.capturar("grok", severidad=errores.TRANSITORIO):
            ... código que puede fallar ...
    """
    def __init__(self, origen, severidad=None, *, job="error", escalar=True, **ctx):
        self.origen, self.severidad = origen, severidad
        self.job, self.escalar, self.ctx = job, escalar, ctx

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            registrar(self.origen, exc_val, self.severidad,
                      job=self.job, escalar=self.escalar, **self.ctx)
        return False   # re-lanza


# ─── CLI (resumen rápido; el detalle vive en observabilidad) ──────────────────
def _resumen(dias=1):
    try:
        import observabilidad
        regs = [r for r in observabilidad._leer_dias(dias) if r.get("resultado") == "fail"]
    except Exception:
        regs = []
    por_sev, por_origen = {}, {}
    for r in regs:
        por_sev[r.get("severidad", "?")] = por_sev.get(r.get("severidad", "?"), 0) + 1
        por_origen[r.get("agente", "?")] = por_origen.get(r.get("agente", "?"), 0) + 1
    print("ERRORES — %d en %s" % (len(regs), "hoy" if dias == 1 else "%d días" % dias))
    if por_sev:
        print("  por severidad: " + ", ".join("%s=%d" % kv for kv in sorted(por_sev.items())))
    if por_origen:
        print("  por origen:    " + ", ".join("%s=%d" % kv for kv in
                                              sorted(por_origen.items(), key=lambda x: -x[1])[:8]))
    return 0


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    dias = 1
    if "--dias" in argv:
        try:
            dias = int(argv[argv.index("--dias") + 1])
        except (IndexError, ValueError):
            dias = 7
    if "--tail" in argv:   # delega en observabilidad --fallos (mismo almacén)
        import observabilidad
        return observabilidad.main(["--fallos", "--dias", str(dias)])
    return _resumen(dias)


if __name__ == "__main__":
    sys.exit(main())
