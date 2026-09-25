#!/usr/bin/env python3
"""tools/ia.py — LA CENTRALITA del motor: un adaptador único sobre los cerebros (F0/F1).

GLOSARIO (término estándar ↔ palabra de la casa; regla de {{TITULAR}} 27/7/26 — ella valida lo técnico,
así que el nombre de la industria manda y la metáfora va al lado):
  · centralita        = ROUTER de proveedores LLM (control-plane).
  · cerebro           = PROVEEDOR/BACKEND de inferencia registrado (Claude, Gemini, GLM, un modelo
                        local por HTTP…). No es un proceso vivo: es una entrada del registro.
  · registro          = SERVICE REGISTRY estático (tools/peripheries.json).
  · relevo / cadena   = FAILOVER sobre una FALLBACK CHAIN ordenada.
  · perfil de tarea   = CLASIFICADOR DE INTENCIÓN determinista (intent routing).
  · borde             = GATE DE EGRESS (política de salida de datos, fail-closed).
  · freno de capacidad= UMBRAL MÍNIMO DE CAPACIDAD por criticidad de la tarea.
  · carril gratis     = TIER gratuito (degradación de coste, no de garantías del muro).

Plan: `~/.claude/plans/expressive-plotting-flame.md` (y el plan-motor typed-swinging-wand).
Objetivo: que el trabajo DISCRETO del sistema deje de depender de un solo proveedor. Si a Claude
se le acaban los créditos o topa un límite, la centralita RELEVA a otro cerebro (gratis primero) en
vez de morir — fue lo que mató al comité de Zúrich. El control-plane DECIDE (determinista); los LLM
solo responden.

SEGURIDAD (respeta la intención del gate de Sid: estabilidad clínica antes del 8-jul):
  · Lo CLÍNICO/sensible va a un cerebro de CONFIANZA (Claude) SIEMPRE, o se NIEGA. Nunca a un cerebro
    de nube. Lo enforce el BORDE (tools/borde.py): cada salida pasa por `borde.egress_check`, y solo
    los destinos `cleared:`/`local:` reciben contenido sensible. Defensa en profundidad: aunque el
    router se equivoque, el borde bloquea.
  · NO toca el lazo clínico vivo (run_agent.sh/dispatcher/launchd). Es una biblioteca NUEVA para
    llamadas discretas; recablear las rutinas a `ia.ask` es F2 (post-biopsia).
  · Relevo RUIDOSO en clínico (nunca esconder una degradación de fiabilidad). Fail-closed.

Uso (biblioteca):
    import ia
    r = ia.ask("resume estos 3 párrafos", clinico=False)      # → {text, brain, coste_usd, ...}
    r = ia.ask("pregunta clínica genérica", clinico=True)      # → solo Claude o se niega
CLI:
    python3 tools/ia.py "tu pregunta"  [--clinico] [--nivel rutina|sustantivo|critico] [--system "..."] [--prefer claude]
    python3 tools/ia.py --por-que "..."  # a quién se lo daría y POR QUÉ, sin invocar ni gastar
    python3 tools/ia.py --para evidencia "..."   # fuerza el carril (el mapa lo elige solo si no)
    python3 tools/ia.py --health        # qué cerebros están vivos (JSON a state/ia/health.json)
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import borde                                  # noqa: E402 — la única puerta de egress
import cost_guard                             # noqa: E402
from _secrets import get as get_secret        # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
IA_DIR = os.path.join(STATE, "ia")
REGISTRO = os.environ.get("BTP_PERIPHERIES") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "peripheries.json")
_NOFREE = "\x00__sin_carril_gratis__\x00"     # centinela: el carril gratis no respondió

CLINICOS_DEFAULT = ["opus", "sonnet", "haiku"]

# 🔴 FRENO DE CRITICIDAD (lo nº1 — seguridad clínica). Una tarea CRÍTICA (clínica/sensible) NO se
# sirve con un cerebro flojo: si el mejor candidato DISPONIBLE no alcanza esta capacidad mínima,
# ia.ask devuelve {parado:True}, NO sirve. El local 3B (capability 4) está POR DEBAJO a propósito:
# para lo clínico, un 3B local NUNCA contesta (el borde no protege aquí porque local ES trusted —
# la barrera es ESTE freno). Claude (capability 9) sí pasa. Umbral configurable por env (solo SUBE
# la exigencia de forma segura; un valor inválido cae al default).
def _cap_minima_critica():
    try:
        v = int(os.environ.get("BTP_CAP_MINIMA_CRITICA", "7"))
        return v if v >= 0 else 7
    except Exception:
        return 7


# 🎯 NIVELES de tarea (regla de {{TITULAR}} 23/6: "no ir al máximo siempre; evaluar lo que más acerca a
# NED en cada momento"). El nivel fija el LISTÓN de capacidad (no el gasto) y CÓMO se ordenan los
# cerebros — el coste solo cede donde la fiabilidad importa:
#   · critico    → clínico / lo que ella o un médico accionarán / hacia fuera / irreversible. Listón
#                  ALTO (_cap_minima_critica) + orden por CAPACIDAD; si nadie llega → PARA + aviso.
#   · sustantivo → razonar/analizar/redactar algo que importa. Listón MEDIO; orden por COSTE (el
#                  3B/nvidia flojos NO bastan → entra Gemini/Claude), barato primero entre los capaces.
#   · rutina     → resumir/clasificar/formatear/volumen. SIN listón; gratis (local/nvidia) primero.
# Default sin pistas = "rutina" (barato y seguro; no se asume que algo importa; conserva el routing
# histórico). El caller que sabe que importa pasa nivel="sustantivo" (o critico_tarea/clinico).
NIVELES = ("critico", "sustantivo", "rutina")


def _cap_min_sustantiva():
    try:
        v = int(os.environ.get("BTP_CAP_MINIMA_SUSTANTIVA", "5"))
        return v if v >= 0 else 5
    except Exception:
        return 5


def _nivel_de(clinico, critico_tarea, nivel):
    """Nivel efectivo: el explícito si es válido; si no, 'critico' cuando es clínico/crítico y
    'rutina' en cualquier otro caso (default seguro que conserva el routing histórico)."""
    if nivel in NIVELES:
        return nivel
    return "critico" if (clinico or critico_tarea) else "rutina"


# 🧭 PERFIL DE TAREA — el mapa del stack, DENTRO del router (27/7/26, regla de {{TITULAR}}: «que Polaris
# tenga todos los LLMs y ya elija cuándo necesita el que necesite»).
#
# Hasta hoy el router solo sabía de PRECIO y de POTENCIA. Qué IA sirve para qué estaba escrito en
# `00_FUENTE-DE-VERDAD/04 · IA/Stack-IA-Recalibrado-2026-06-22.md` (10 filas revisadas por 3 comités
# el 28/6) y lo ejecutaba {{TITULAR}} a mano, abriendo la app que tocaba. Esto trae esas filas al código.
#
# DETERMINISTA a propósito: es el invariante que declara este fichero («el control-plane DECIDE; los
# LLM solo responden»). Se testea, no cuesta un token, y no puede alucinar su propia decisión.
#
# SEGURO por construcción, tres veces:
#   1. El perfil solo REORDENA candidatos. El borde, el freno de capacidad y el relevo corren
#      después, igual y en el mismo orden. Un perfil NO puede colar contenido sensible en una nube.
#   2. PROMOCIONA, no fuerza: si el especialista está caído o bloqueado, la cadena normal recoge.
#   3. Ante la duda, "general" = exactamente el comportamiento anterior a este cambio.
PERFILES = ("clinico", "evidencia", "descubrir", "osint-x", "web-frontend", "codigo",
            "segunda-opinion", "volumen", "general")

# Orden de PRIORIDAD (el primero que casa gana). Evidencia va antes que descubrir a propósito:
# «lo último con citas» exige papers reales (Consensus/scite), no un buscador LLM — es la regla de
# {{TITULAR}} en feedback-evidencia-grok-no-perplexity. X gana a descubrir: en X manda Grok.
_SENALES = (
    ("evidencia", (r"\bpmid\b", r"\bdoi\b", r"\bpapers?\b", r"\bpubmed\b", r"\bliteratura\b",
                   r"bibliograf", r"revision sistematica", r"met[a-]?analisis", r"\bcitas?\b",
                   r"\bcitar\b", r"\breferencias\b", r"\babstract\b", r"evidencia ingeniera",
                   r"que dice la (evidencia|literatura)")),
    ("osint-x", (r"\bx\.com\b", r"\btwitter\b", r"\btuits?\b", r"\btweets?\b",
                 r"(?<![\w.])@[a-z0-9_]{2,15}\b(?!\.[a-z])", r"\bhandle\b", r"\bseguidores\b")),
    ("descubrir", (r"lo ultimo", r"\bnovedades\b", r"que hay nuevo", r"\bpreprints?\b",
                   r"ultima hora", r"tiempo real", r"\bnoticias\b", r"recien publicad",
                   r"acaba de (salir|publicarse)")),
    # Pedir OTRO par de ojos es una intención explícita, no una corazonada: señales estrechas a
    # propósito (el uso normal es `--para segunda-opinion` desde un comité que quiere contraste).
    ("segunda-opinion", (r"segunda opinion", r"\botro modelo\b", r"contrast(a|ar) esto",
                         r"revision critica", r"que opina otr[ao]")),
    ("web-frontend", (r"\bhtml\b", r"\bcss\b", r"\blanding\b", r"\bfrontend\b", r"\btailwind\b",
                      r"\bresponsive\b", r"\bhero\b", r"\bcta\b", r"\bmaqueta\b",
                      r"pagina web", r"diseno web", r"\bcomponente\b")),
    ("codigo", (r"\.(py|js|ts|sh|json)\b", r"\btraceback\b", r"\bstacktrace\b", r"refactor",
                r"\bbugs?\b", r"\bexcepcion\b", r"\bcommit\b", r"\bdiff\b", r"\bpytest\b",
                r"\blinter\b", r"\bcompila")),
    ("volumen", (r"\bresume(me|n)?\b", r"\bclasifica\b", r"\bextrae\b", r"\bformatea\b",
                 r"\btraduce\b", r"en una linea", r"en bullets")),
)


def _norm(s):
    """Minúsculas sin acentos: 'Metaanálisis' y 'metaanalisis' casan igual."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                   if unicodedata.category(c) != "Mn")


def perfil_de_tarea(prompt, *, sensible=False, para=None):
    """(perfil, señales). `para` explícito manda (el que llama sabe más). Lo sensible es 'clinico'
    SIEMPRE (Claude o se niega, fila 1 del mapa). Si nada casa → 'general' = routing de siempre.
    Función PURA: sin red, sin estado, sin LLM."""
    if para in PERFILES:
        return para, ["explícito: %s" % para]
    if sensible:
        return "clinico", ["el borde lo marca sensible"]
    import re
    txt = _norm(prompt)
    for perfil, patrones in _SENALES:
        casan = [p for p in patrones if re.search(p, txt)]
        if casan:
            return perfil, casan[:4]
    return "general", []


# Caché de salud: si está fresco (TTL corto), ia.ask SALTA los cerebros marcados no-disponibles
# ANTES del borde y la invocación (evita el round-trip a un cerebro caído / sin saldo). Si está
# viejo, lo REFRESCA. RED de seguridad: aunque el caché mienta, el relevo reactivo del bucle sigue
# (un cerebro "vivo" que falla al invocarlo igual releva). TTL=0 desactiva el caché (siempre fresco).
HEALTH_TTL_SEG = int(os.environ.get("BTP_HEALTH_TTL", "300") or "300")  # ~5 min


# ── Registro de cerebros ─────────────────────────────────────────────────────────────────
def cargar_registro():
    try:
        cerebros = (json.load(open(REGISTRO, encoding="utf-8")) or {}).get("cerebros", [])
    except Exception:
        cerebros = []
    return [c for c in cerebros if isinstance(c, dict)]


def _candidatos(registro, sensible, prefer=None, solo_gratis=False, por_capacidad=False,
                perfil=None):
    """Cerebros aplicables, ordenados. Si `sensible`, SOLO los trusted (muro). Si `solo_gratis`,
    SOLO los gratis (para canales que no deben gastar de pago — p.ej. el bot por defecto).
    `por_capacidad`: ordena por CAPACIDAD desc (fiabilidad manda — crítico/sensible); si no, por
    coste ('orden' asc: gratis→barato→reservado, curado ahí). `perfil` promociona al ESPECIALISTA
    de esa tarea (campo `para` del registro) sin romper el resto del orden. `prefer` va primero."""
    cand = [c for c in registro if c.get("enabled")]
    if sensible:
        cand = [c for c in cand if c.get("trusted")]
    if solo_gratis:
        cand = [c for c in cand if c.get("free")]
    # `solo_perfil`: cerebros que NO entran en el relevo general (p.ej. Perplexity, que es un
    # buscador, no un razonador: colarlo en la cadena daría respuestas con forma de búsqueda a
    # preguntas que no lo son). Solo aparecen cuando el perfil los reclama.
    cand = [c for c in cand
            if not c.get("solo_perfil") or (perfil and perfil in (c.get("para") or []))]
    if por_capacidad:
        # fiabilidad manda (plan: muro→capacidad): Claude (cap alta) gana al local modesto; el local
        # solo sería último recurso de confianza. El freno de capacidad descarta a los flojos.
        cand.sort(key=lambda c: -c.get("capability", 0))
    else:
        cand.sort(key=lambda c: c.get("orden", 50))   # no-crítico: gratis/barato primero
    # ESPECIALISTA primero (sort estable → dentro de cada grupo se conserva el orden de arriba).
    # Va DESPUÉS del orden base y ANTES de `prefer`: quien pide un cerebro concreto manda sobre el
    # mapa, y el mapa manda sobre el precio. Nunca filtra: el resto sigue detrás como respaldo.
    if perfil and perfil not in ("general", "clinico"):
        cand.sort(key=lambda c: 0 if perfil in (c.get("para") or []) else 1)
    if prefer:
        cand.sort(key=lambda c: 0 if c.get("name") == prefer else 1)
    return cand


def _log(rec):
    try:
        os.makedirs(IA_DIR, exist_ok=True)
        rec = dict(rec, ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        with open(os.path.join(IA_DIR, "log-%s.jsonl" % datetime.now().strftime("%Y-%m-%d")),
                  "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _marcar_credito(ok):
    """Deja la SEÑAL PASIVA del saldo prepago de Anthropic para cost_guard.credito_ok() (que es una
    PUERTA y no debe llamar fuera). La escribe quien SÍ llama a los cerebros: éxito de un cerebro
    Claude → hay crédito (True); fallo 'credito' (400 'Credit balance is too low') → agotado (False).
    Así el sistema sabe el saldo SOLO, sin que {{TITULAR}} anote recargas. Fail-soft, atómico."""
    try:
        import time as _t
        os.makedirs(IA_DIR, exist_ok=True)
        tmp = os.path.join(IA_DIR, ".credito.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ok": bool(ok), "ts": _t.time()}, f)
        os.replace(tmp, os.path.join(IA_DIR, "credito.json"))
    except Exception:
        pass


def sonda_credito(timeout=45):
    """Pregunta a Claude lo mínimo posible SOLO para saber si queda prepago. True/False/None.

    POR QUÉ (25-jul-26). `_marcar_credito` la escribía únicamente `ask`, y `ask` casi no se usa:
    el lazo ejecuta por `run_agent.sh` (Claude Code CLI contra la cuota del plan, que NO toca el
    prepago). Resultado medido: `credito.json` llevaba 50,9 h parado con un TTL de 6 h, o sea que
    `cost_guard.credito_ok()` devolvía None en cada pasada y el sistema se quedó SIN SABER su
    propio saldo — y encima el estimador gritaba «bajo» con una cuenta imposible. Se arregló que
    no mintiera; esto le devuelve la capacidad de saberlo. La dispara `ia_health.probe()`, que ya
    corre sola cada ~2 h.

    Va por el MISMO camino que `_claude` (el binario `claude` con la clave del Llavero) en vez de
    abrir un cliente HTTP nuevo: una segunda boca hacia Anthropic sería justo lo que el
    choke-point del muro prohíbe, y de hecho el test de fuga la cazó al primer intento.

    Contenido enviado: «hi». Nada de PII (N0). Coste: una respuesta de una palabra cada 2 h.

    None cuando no se puede determinar (binario ausente, red, límite de tasa) y entonces NO se
    escribe nada: una señal FRESCA que en realidad dice «no sé» es peor que una vieja, porque el
    TTL la daría por buena."""
    key = os.environ.get("ANTHROPIC_API_KEY") or get_secret("btp-anthropic-api") or ""
    if not key:
        return None
    env = dict(os.environ, ANTHROPIC_API_KEY=key)
    binario = os.environ.get("BTP_CLAUDE_BIN", "claude")
    try:
        p = subprocess.run([binario, "-p", "hi", "--output-format", "json",
                            "--model", "claude-haiku-4-5-20251001"],
                           capture_output=True, text=True, env=env, timeout=timeout)
    except Exception:
        return None
    low = (p.stdout or "").lower()
    # Misma lectura del 400 que ya hace `_claude`: es LA señal de prepago agotado.
    if '"api_error_status": 400' in low.replace(" ", "") or "credit balance is too low" in low:
        ok = False
    elif p.returncode == 0 and low.strip():
        ok = True
    else:
        return None      # 429/529/overloaded/binario raro → no dice nada del saldo
    _log({"evento": "sonda_credito", "ok": ok})
    _marcar_credito(ok)
    return ok


def _aviso_clinico(brain, motivo):
    """Aviso RUIDOSO cuando el carril clínico releva/degrada. Best-effort (no rompe el flujo)."""
    try:
        import salida
        salida.report_to_titular(
            "Aviso: en una consulta clínica tuve que relevar a otro cerebro (%s) por %s. "
            "La fiabilidad puede bajar — conviene contrastarlo con tu equipo. 💜" % (brain, motivo),
            dry=False)
    except Exception:
        pass


# ── Adaptadores por cerebro (REUSAN los carriles; no los reescriben) ─────────────────────
def _call_claude(prompt, system, models, interactivo=False, critico=False):
    """Subproceso `claude -p` con cadena de modelos (Opus→Sonnet→Haiku). Devuelve
    (text, coste_usd, fallo) donde fallo ∈ {None, 'limite', 'credito', 'tope_local', 'fallo'}.

    `interactivo=True` (charla EN VIVO de {{TITULAR}} por el gateway) reserva para ella la última
    franja de gasto del día (ver cost_guard): el loop 24/7 no puede dejarla sin Claude.

    `critico=True` (clínico/crítico/sensible — regla de {{TITULAR}}, 2/7/26): el TOPE DIARIO/MENSUAL
    interno NUNCA corta esta tarea. `check_before_job` solo puede detectar el tope QUE NOSOTROS
    fijamos ([tope_local]); el prepago REAL agotado solo se sabe con el round-trip HTTP de abajo
    (el 400 "Credit balance is too low"). Por eso, si es crítico y el único motivo es tope_local,
    IGNORAMOS el check y llamamos a la API igualmente — si de verdad no hay saldo, el 400 real la
    parará más abajo (rama 'credito'), que sí es un bloqueo legítimo con aviso fuerte.

    DISTINCIÓN CRÍTICA (soluciona el falso caído de Vega):
      · 'tope_local'   → el tope diario/mensual QUE NOSOTROS fijamos se agotó. El dinero en
                         Anthropic sigue ahí. RECUPERABLE: {{TITULAR}} sube el tope con 1 clic
                         (aprobar_tope_hoy / «sube» por Telegram). No es un fallo de red ni
                         de saldo real — la centralita DEBE intentar el carril gratis si existe.
      · 'credito'      → la API devolvió 400 "Credit balance is too low". El prepago de Anthropic
                         está a cero. Hay que recargar en console.anthropic.com.
    """
    key = os.environ.get("ANTHROPIC_API_KEY") or get_secret("btp-anthropic-api") or ""
    env = dict(os.environ)
    if key:
        env["ANTHROPIC_API_KEY"] = key
    binario = os.environ.get("BTP_CLAUDE_BIN", "claude")
    sys_prompt = system or ("Asistente operativa de {{TITULAR}}; responde claro, en español llano, "
                            "sin inventar.")
    fallo = "fallo"
    for m in models:
        okb, motivo_cg, _ = cost_guard.check_before_job(interactivo=interactivo)
        if not okb:
            tipo = cost_guard.tipo_bloqueo(motivo_cg)
            if critico and tipo == "tope_local":
                # 🔴 Regla de {{TITULAR}} (2/7/26): lo clínico/crítico/sensible NO se corta por el tope
                # interno. Registramos que lo saltamos (trazabilidad) y seguimos a la API real —
                # NO gastamos por adelantado (add_cost sigue corriendo tras la respuesta real, como
                # siempre); solo dejamos de usar el tope como excusa para no intentarlo.
                _log({"evento": "claude", "modelo": m, "fallo": "tope_local_saltado_critico",
                      "motivo": motivo_cg})
            else:
                # Distingue tope local (nuestro presupuesto) de prepago real agotado.
                # El prepago real solo se puede saber con un round-trip a la API (400);
                # cost_guard nunca ve la respuesta HTTP → aquí SIEMPRE es tope_local.
                _log({"evento": "claude", "modelo": m, "fallo": "tope_local", "motivo": motivo_cg})
                fallo = "tope_local"; break     # tope nuestro → no gastar más hoy; el relevo puede usar gratis
        args = [binario, "-p", prompt, "--append-system-prompt", sys_prompt,
                "--output-format", "json", "--model", m]
        try:
            p = subprocess.run(args, capture_output=True, text=True, env=env, timeout=300)
            out = p.stdout or ""
        except (FileNotFoundError, PermissionError) as e:
            # AMBIENTAL (14-jul-2026): el binario no está en el PATH de ESTE proceso. Caso típico:
            # una sesión ssh, que no hereda /opt/homebrew/bin. NO significa que el servicio esté
            # caído ni que falte saldo — significa que nos están ejecutando en un entorno sin
            # cerebro. Antes caía en el mismo cajón que un 429/503 y `ask` acababa gritándole a
            # {{TITULAR}} un 🔴 CLÍNICO por un PATH roto. Reintentar los otros modelos es inútil (mismo
            # binario) → cortamos aquí.
            _log({"evento": "claude", "modelo": m, "fallo": "ambiental:binario_ausente",
                  "detalle": repr(e)[:160], "binario": binario})
            fallo = "ambiental:binario_ausente"; break
        except Exception as e:
            _log({"evento": "claude", "modelo": m, "fallo": "exc", "detalle": repr(e)[:160]})
            fallo = "fallo"; continue
        low = out.lower()
        if '"api_error_status": 400' in low.replace(" ", "") or "credit balance is too low" in low:
            # 400 desde la API = prepago Anthropic agotado (distinto del tope local).
            _log({"evento": "claude", "modelo": m, "fallo": "credito",
                  "detalle": "API 400 credit balance too low — recargar en console.anthropic.com"})
            fallo = "credito"; break
        if ('"api_error_status": 429' in out or '"api_error_status": 529' in out
                or "overloaded" in low or "rate limit" in low or "usage limit" in low):
            _log({"evento": "claude", "modelo": m, "fallo": "limite"})
            fallo = "limite"; continue          # degrada al siguiente modelo de la cadena
        try:
            d = json.loads(out)
        except Exception:
            fallo = "fallo"; continue
        if isinstance(d, dict) and not d.get("is_error") and isinstance(d.get("result"), str):
            usd = d.get("total_cost_usd") or 0.0
            try:
                cost_guard.add_cost(float(usd), job_id="ia-claude-%s" % m)
            except Exception:
                pass
            return d["result"], float(usd or 0.0), None
        fallo = "fallo"
    return None, 0.0, fallo


def _call_local(cerebro, prompt, system):
    """Cerebro LOCAL (servidor OpenAI-compatible, p.ej. Ollama en localhost). $0, egress-cero,
    destino `local:` = de confianza. Si el servidor no está levantado (no instalado / apagado) →
    falla rápido y la centralita releva. Es el suelo de independencia del plan ('open-weight LOCAL')."""
    import urllib.request
    url = cerebro.get("url") or "http://127.0.0.1:11434/v1/chat/completions"
    msgs = ([{"role": "system", "content": system}] if system else []) + \
           [{"role": "user", "content": prompt}]
    body = json.dumps({"model": cerebro.get("model", "llama3.2:3b"),
                       "messages": msgs, "stream": False}).encode()
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=cerebro.get("timeout", 60)) as r:
            d = json.load(r)
        txt = ((d.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "").strip()
        return (txt, 0.0, None) if txt else (None, 0.0, "fallo")
    except Exception:
        return None, 0.0, "fallo"   # servidor local caído/no instalado → relevo


def _call_carril_gratis(prompt, system, model=None):
    import carril_gratis
    txt = carril_gratis.responder(prompt, system=system, fallback=_NOFREE, model=model)
    if txt == _NOFREE or not txt.strip():
        return None, 0.0, "fallo"
    return txt, 0.0, None


def _call_fugu(prompt):
    import fugu
    r = fugu.consultar(prompt)
    if r.get("text"):
        return r["text"], float(r.get("coste_usd") or 0.0), None
    if r.get("blocked"):
        return None, 0.0, "bloqueado"
    return None, 0.0, "credito" if "presupuesto" in (r.get("error") or "") else "fallo"


def _call_cli(name, prompt):
    """Carriles con solo main() (gemini…): subproceso del CLI (conserva su guard del borde)."""
    try:
        p = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), name + ".py"),
                            prompt], capture_output=True, text=True, timeout=300)
        txt = (p.stdout or "").strip()
        if p.returncode == 0 and txt and "🛑 BORDE" not in (p.stderr or ""):
            return txt, 0.0, None
    except Exception:
        pass
    return None, 0.0, "fallo"


# Un error que el cliente imprime en stdout SALIENDO CON rc=0. Sin esto, la centralita sirve el
# texto del error como si fuera la respuesta del modelo (era el problema 6, visto en gemini.py y
# presente igual en chatgpt/glm/grok/perplexity: todos imprimen el fallo y devuelven 0).
_ERR_PREFIJOS = ("error:", "error de red", "falta la clave", "uso:", "usage:")
_ERR_CONTIENE = ("api error", "api_error", "insufficient_quota", "rate limit")


def _cuerpo_es_error(txt):
    """(es_error, code). Mira el CUERPO, no el código de salida."""
    import re
    low = (txt or "").strip().lower()
    if not low:
        return True, "?"
    m = re.search(r"api error (\d{3})", low)
    if m:
        return True, m.group(1)
    if low.startswith(_ERR_PREFIJOS) or any(s in low[:200] for s in _ERR_CONTIENE):
        return True, "?"
    return False, None


def _call_cli_chain(cerebro, prompt):
    """Cerebro servido por su CLIENTE CLI (gemini.py, chatgpt.py, glm.py, grok.py, perplexity.py),
    con CADENA de modelos: el más capaz primero y CAÍDA al estable si la punta falla.

    Es el adaptador GENÉRICO (antes era `_call_gemini`, solo para Gemini). Guiado por datos del
    registro: `bin` (por defecto <name>.py), `models` (cadena), `flags` (fijos del cliente),
    `flag_modelo` (por defecto --model), `timeout`.

    Dos cosas que hace y `_call_cli` no: prueba la cadena de modelos, y DETECTA el error dentro del
    cuerpo (rc=0 + 'API error 429' en stdout) para RELEVAR en vez de servir el error como respuesta.
    Cada cliente conserva su propio guard del borde (`borde.guard_cli`) — defensa en profundidad.
    El gasto lo apunta el propio cliente en `gasto.py` (ledger); aquí no se estima."""
    name = cerebro.get("name") or "?"
    binpath = os.path.join(os.path.dirname(__file__), cerebro.get("bin") or (name + ".py"))
    models = cerebro.get("models") or [None]     # [None] = el modelo por defecto del cliente
    flags = list(cerebro.get("flags") or [])
    flag_modelo = cerebro.get("flag_modelo", "--model")
    timeout = int(cerebro.get("timeout", 300))
    fallo = "fallo"
    for m in models:
        args = [sys.executable, binpath] + flags + ([flag_modelo, m] if m else []) + [prompt]
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        except Exception as e:
            _log({"evento": "cli_chain", "brain": name, "modelo": m, "fallo": "exc",
                  "detalle": repr(e)[:160]})
            fallo = "fallo"; continue
        if p.returncode != 0:
            fallo = "fallo"; continue           # rc≠0 = borde propio (sys.exit 2) / sin clave → releva
        txt = (p.stdout or "").strip()
        malo, code = _cuerpo_es_error(txt)
        if malo:
            _log({"evento": "cli_chain", "brain": name, "modelo": m, "fallo": "api", "code": code})
            fallo = "limite" if code in ("429", "503", "529") else "fallo"
            continue                            # la punta cayó → prueba el estable; si no, releva
        return txt, 0.0, None
    return None, 0.0, fallo


def _invocar(cerebro, prompt, system, clinico, interactivo=False, critico=False):
    # Gancho de test (como BTP_CLAUDE_BIN): finge la respuesta del cerebro SIN tocar el borde ni el
    # routing (que siguen reales) — para probar el cableado run_agent↔centralita de forma determinista.
    fake = os.environ.get("BTP_IA_FAKE")
    if fake is not None:
        return fake, 0.0, None

    kind = cerebro.get("kind")

    # AMBIENTAL, antes de gastar un round-trip (14-jul-2026). Si este cerebro necesita una clave y
    # NO podemos leerla en este proceso, el fallo es del ENTORNO, no del servicio. Sin esto, Gemini
    # (que se invoca por un script de Python — el binario SIEMPRE existe, así que nunca da
    # FileNotFoundError) fallaba con un genérico "fallo" y contaminaba la clasificación: bastaba UN
    # cerebro mal clasificado para que `ask` volviera a vestir de 🔴 CLÍNICO lo que era un PATH o un
    # Llavero rotos. Para el que llama, "no puedo preguntarle al Llavero" (ssh) y "la clave no está"
    # significan aquí lo mismo: este ENTORNO no puede usar este cerebro; no dice nada del servicio.
    _sec = cerebro.get("secret") or ""
    _tiene_env = bool(os.environ.get("ANTHROPIC_API_KEY")) if kind == "claude" else False
    if _sec and not _tiene_env:
        try:
            if not get_secret(_sec):
                _log({"evento": "invocar", "brain": cerebro.get("name"),
                      "fallo": "ambiental:sin_clave", "secret": _sec})
                return None, 0.0, "ambiental:sin_clave"
        except Exception:
            pass                       # ni preguntarlo pudimos → que lo intente y falle por su cuenta

    if kind == "claude":
        models = cerebro.get("models_clinico") if clinico else cerebro.get("models")
        return _call_claude(prompt, system, models or CLINICOS_DEFAULT, interactivo=interactivo,
                             critico=critico)
    if kind == "openai_local":
        return _call_local(cerebro, prompt, system)
    if kind == "carril_gratis":
        return _call_carril_gratis(prompt, system, model=cerebro.get("model"))
    if kind == "fugu":
        return _call_fugu(prompt)
    if kind in ("gemini", "cli_chain"):     # 'gemini' se conserva por compatibilidad del registro
        return _call_cli_chain(cerebro, prompt)
    if kind == "cli":
        return _call_cli(cerebro.get("name"), prompt)
    return None, 0.0, "kind desconocido"


def _salud_disponibles():
    """{name: disponible(bool)} desde el caché de salud. Si el caché falta o está más viejo que
    el TTL → lo REFRESCA (health() reescribe el JSON). Si nada se puede leer/calcular → {} (no
    sabemos = no filtramos; el relevo reactivo cubre). TTL<=0 fuerza refresco siempre."""
    path = os.path.join(IA_DIR, "health.json")
    fresco = False
    if HEALTH_TTL_SEG > 0:
        try:
            import time
            fresco = (time.time() - os.path.getmtime(path)) < HEALTH_TTL_SEG
        except Exception:
            fresco = False
    if not fresco:
        try:
            health()   # refresca el JSON (best-effort; si falla, intentamos leer el que haya)
        except Exception:
            pass
    try:
        data = json.load(open(path, encoding="utf-8"))
        return {c.get("name"): bool(c.get("disponible"))
                for c in (data.get("cerebros") or []) if c.get("name")}
    except Exception:
        return {}   # sin caché legible → no filtramos por salud (red = relevo reactivo)


# ── Quién es el CEREBRO PRINCIPAL de una tarea (generaliza, NO cablea 'claude') ────────────
def principal_para(nivel="critico", sensible=False, registro=None):
    """El CEREBRO PRINCIPAL de una tarea de este nivel/sensibilidad = el mejor cerebro de CONFIANZA
    con capacidad suficiente para su listón. NO se cablea a 'claude': se DERIVA del registro + el
    nivel (hoy resuelve a Claude para lo crítico; mañana podría ser un local potente o Gemini para lo
    no sensible). Es el cerebro al que una ACCIÓN espera cuando no está; un relevo de menor rango solo
    RESPONDE en solo-lectura mientras tanto. Devuelve {name, capability, trusted} o None si ninguno
    cumple. Función PURA (sin red, sin escribir estado)."""
    reg = registro if registro is not None else cargar_registro()
    cap_min = (_cap_minima_critica() if nivel == "critico"
               else _cap_min_sustantiva() if nivel == "sustantivo" else 0)
    if sensible:
        cap_min = max(cap_min, _cap_minima_critica())
    trusted_only = bool(sensible) or nivel == "critico"   # lo sensible/crítico, solo a confianza
    cand = [c for c in _candidatos(reg, trusted_only, por_capacidad=True)
            if int(c.get("capability", 0)) >= cap_min]
    if not cand:
        return None
    top = cand[0]
    return {"name": top.get("name"), "capability": int(top.get("capability", 0)),
            "trusted": bool(top.get("trusted"))}


def por_que(prompt, *, clinico=False, nivel=None, para=None, solo_gratis=False, sensible_forzado=False):
    """EXPLICA a quién le daría este prompt y por qué, SIN invocar a nadie y SIN gastar nada.
    Ni egress ni sello: solo el clasificador local del borde + el mismo orden de candidatos que
    usaría `ask`. Para auditar el mapa ('¿por qué esto fue a GLM?') y para los tests."""
    sensible = bool(clinico) or bool(sensible_forzado) or borde.clasificar(prompt)[0]
    nivel_ef = _nivel_de(clinico, False, nivel)
    perfil, senales = perfil_de_tarea(prompt, sensible=sensible, para=para)
    cand = _candidatos(cargar_registro(), sensible, solo_gratis=solo_gratis,
                       por_capacidad=(sensible or nivel_ef == "critico"), perfil=perfil)
    return {"perfil": perfil, "senales": senales, "sensible": sensible, "nivel": nivel_ef,
            "cadena": [c.get("name") for c in cand]}


# ── La puerta de entrada: ask() ──────────────────────────────────────────────────────────
def ask(prompt, *, clinico=False, system=None, prefer=None, sesion=None, solo_gratis=False,
        critico_tarea=False, nivel=None, interactivo=False, para=None, sensible_forzado=False):
    """Enruta `prompt` al mejor cerebro disponible y RELEVA si falla. Devuelve dict:
    {text, brain, coste_usd, degradado, deferred, motivo}. Lo sensible solo va a cerebro de
    confianza (o se niega); todo pasa por el borde. `solo_gratis=True` restringe a cerebros gratis
    (si no hay → deferred, sin gastar de pago) — para canales que no deben gastar Claude por defecto.

    🔴 FRENO DE CRITICIDAD (`critico_tarea=True`, o clínico, o sensible auto-detectado): la tarea es
    de SEGURIDAD CLÍNICA → NO se sirve con un cerebro por debajo del umbral de capacidad mínima
    (_cap_minima_critica). Si el mejor candidato disponible no llega (p.ej. el local 3B, cap 4), o
    todos quedan degradados/flojos, NO se sirve: devuelve {parado:True, motivo, desbloquea} y dispara
    un AVISO FUERTE por sí mismo. FAIL-SAFE: 'rutina' (no crítico) degrada-y-sirve como siempre."""
    if not isinstance(prompt, str) or not prompt.strip():
        return {"text": None, "brain": None, "deferred": False, "parado": False,
                "motivo": "prompt vacío"}
    # SENSIBLE POR PROCEDENCIA (24-sep-26, auditoría externa 3.3). `clasificar` es el mismo
    # detector que redacta en `deid`: un pasaje del caso que no reconoce (un nombre, un domicilio,
    # un diagnóstico sin marcador) salía como NO sensible y podía ir a un cerebro de nube. Quien
    # SABE de dónde viene el texto (`responder_con_datos`, con contexto de la KB del caso) lo dice
    # con `sensible_forzado=True`: solo cerebros de confianza, SIN la parada ruidosa de `clinico`.
    sensible = bool(clinico) or bool(sensible_forzado) or borde.clasificar(prompt)[0]
    nivel = _nivel_de(clinico, critico_tarea, nivel)
    # Dos ejes que no se solapan:
    #  · CRÍTICO EXPLÍCITO (critico_tarea, clínico o nivel="critico"): si no se sirve → PARADO + aviso
    #    FUERTE (seguridad clínica; ella tiene que enterarse). El auto-sensible SIN flag no es crítico
    #    explícito → si no se sirve, DEFERRED callado (un "ahora no puedo", no un grito).
    #  · LISTÓN de capacidad por NIVEL: critico = alto, sustantivo = medio, rutina = sin listón. Lo
    #    SENSIBLE (auto o por flag) exige SIEMPRE grado crítico (un 3B flojo NUNCA opina de lo sensible)
    #    — salvo el carril LOCAL-ONLY deliberado (solo_gratis), donde la garantía es el BORDE y el
    #    cerebro local SÍ puede contestar sobre el caso en la máquina.
    critico_explicito = bool(critico_tarea) or bool(clinico) or nivel == "critico"
    if nivel == "critico":
        cap_min = _cap_minima_critica()
    elif nivel == "sustantivo":
        cap_min = _cap_min_sustantiva()
    else:
        cap_min = 0
    if sensible and not solo_gratis:
        cap_min = max(cap_min, _cap_minima_critica())
    frena_capacidad = critico_explicito or nivel == "sustantivo" or (sensible and not solo_gratis)
    # Orden: por CAPACIDAD donde manda la fiabilidad (crítico o sensible); por COSTE si no.
    por_capacidad = sensible or nivel == "critico"
    # 🧭 El mapa del stack: qué clase de tarea es → qué cerebro la hace mejor. Solo REORDENA; el
    # borde y el freno de capacidad siguen mandando abajo. Ante la duda → "general" = lo de siempre.
    perfil, senales = perfil_de_tarea(prompt, sensible=sensible, para=para)
    registro = cargar_registro()
    candidatos = _candidatos(registro, sensible, prefer=prefer, solo_gratis=solo_gratis,
                             por_capacidad=por_capacidad, perfil=perfil)
    if not candidatos:
        motivo = ("no hay cerebro gratis para contenido sensible (lo lleva Claude)"
                  if sensible and solo_gratis else
                  "no hay cerebro de CONFIANZA habilitado para contenido sensible"
                  if sensible else "no hay ningún cerebro gratis habilitado"
                  if solo_gratis else "no hay ningún cerebro habilitado")
        _log({"evento": "ask", "sensible": sensible, "critico": critico_explicito,
              "resultado": "sin_candidatos", "motivo": motivo})
        if critico_explicito:
            return _parar(motivo, "habilita un cerebro de confianza con capacidad suficiente "
                          "(p.ej. recarga Claude)", sensible)
        return {"text": None, "brain": None, "deferred": False, "parado": False, "motivo": motivo}

    salud = _salud_disponibles()   # caché TTL: salta los caídos/sin-saldo antes del round-trip
    degradado = False
    intentos = []
    capaz_existe = False   # ¿algún candidato cumple el umbral crítico? (para distinguir parado real)
    for i, c in enumerate(candidatos):
        name, destino = c.get("name"), c.get("destino", c.get("name"))
        cap = int(c.get("capability", 0))
        # EL BORDE PRIMERO (única puerta de egress + SELLO de la traza): aunque luego el freno frene,
        # el borde tiene que CORRER y sellar su veredicto (defensa en profundidad: un cerebro de nube
        # mislabeled 'trusted' queda registrado como deny). Si bloquea → no se invoca, se prueba el siguiente.
        v = borde.egress_check(prompt, destino=destino, sesion=sesion, intencion="ia.ask")
        if not v.permitido:
            intentos.append({"brain": name, "fallo": "borde:" + v.motivo})
            continue
        # 🔴 FRENO (tras el borde): en tarea crítica/sensible, un cerebro POR DEBAJO del umbral NUNCA
        # sirve la respuesta. Se salta ANTES de invocarlo (no se le pide nada). Así el local 3B (cap 4)
        # jamás contesta lo clínico aunque sea el único disponible: la tarea PARA, no se degrada a un flojo.
        # MATIZ (relevo de cortesía): el suelo es una garantía CLÍNICA. Para una charla NO crítica
        # (Vivir) que solo resultó SENSIBLE, un cerebro de CONFIANZA y LOCAL por debajo del suelo SÍ
        # da un relevo de cortesía —en vez de dejar a {{TITULAR}} MUDA— cuando el de pago topa NUESTRO
        # presupuesto. El muro (egress) queda intacto: el local es de confianza y no saca nada de la
        # máquina. El listón estricto sigue mandando en lo crítico/clínico y para cerebros de nube.
        if frena_capacidad and cap < cap_min and (critico_explicito or not c.get("trusted")):
            intentos.append({"brain": name, "fallo": "capacidad %d < umbral crítico %d (no sirve)"
                             % (cap, cap_min)})
            continue
        capaz_existe = True
        # SALUD (caché TTL): salta el que el caché marca caído / sin saldo, antes de invocarlo. Si
        # el caché miente (lo marca vivo y falla), el relevo reactivo de abajo cubre.
        if salud.get(name) is False:
            intentos.append({"brain": name, "fallo": "salud: no disponible (caché)"})
            if i + 1 < len(candidatos):
                degradado = True
            continue
        # critico= aquí es la señal de "salta el tope interno si el saldo real está sano" (regla de
        # {{TITULAR}} 2/7/26): cubre clínico/crítico EXPLÍCITO y también lo auto-detectado SENSIBLE (el
        # muro ya exige cerebro de confianza para sensible; este flag además le exime del tope).
        text, usd, fallo = _invocar(c, prompt, system, clinico, interactivo=interactivo,
                                     critico=(critico_explicito or sensible))
        if text is not None:
            if str(c.get("kind", "")).startswith("claude"):
                _marcar_credito(True)          # Claude respondió → hay saldo prepago (señal para cost_guard)
            res = {"text": text, "brain": name, "coste_usd": usd, "degradado": degradado,
                   "deferred": False, "parado": False, "motivo": "ok", "nivel": nivel,
                   "perfil": perfil}
            _log({"evento": "ask", "sensible": sensible, "critico": critico_explicito, "brain": name,
                  "degradado": degradado, "coste_usd": usd, "resultado": "ok", "intentos": intentos,
                  "perfil": perfil, "senales": senales})
            return res
        intentos.append({"brain": name, "fallo": fallo})
        # ¿hay más candidatos CAPACES por delante? entonces esto es un RELEVO (degradación).
        if i + 1 < len(candidatos):
            degradado = True
            if clinico:
                _aviso_clinico(candidatos[i + 1].get("name"), fallo)

    # Nada se sirvió. Distinguir el motivo (solo-flojo vs capaces-fallaron) para el mensaje/desbloqueo.
    fallos_intentos = [i.get("fallo", "") for i in intentos]
    # AMBIENTAL: los cerebros que llegaron a intentarse fallaron TODOS por el ENTORNO de este
    # proceso (binario/clave ausentes), no por el servicio. Los saltados por capacidad no cuentan:
    # esos no se intentaron. Si es ambiental, el aviso NO puede vestirse de emergencia clínica.
    _intentados = [f for f in fallos_intentos if f and not f.startswith("capacidad ")]
    ambiental = bool(_intentados) and all(f.startswith("ambiental:") for f in _intentados)
    if ambiental:
        motivo = ("no pude EJECUTAR ningún cerebro en este entorno (el binario no está en el PATH "
                  "de este proceso) — no es que el servicio esté caído ni que falte saldo")
        desbloquea = ("ejecútalo dentro del lazo (launchd), no por ssh; o define BTP_CLAUDE_BIN con "
                      "la ruta real del binario")
    elif not capaz_existe and frena_capacidad:
        motivo = ("solo hay cerebros por debajo del umbral crítico (capacidad < %d): para una "
                  "tarea clínica/crítica/sensible NO se sirve con un cerebro flojo" % cap_min)
        desbloquea = ("recarga/levanta un cerebro de confianza con capacidad ≥ %d (p.ej. Claude)"
                      % cap_min)
    else:
        # ¿El bloqueo fue por tope local o por prepago real agotado?
        # Miramos los intentos para dar el mensaje/desbloquea correcto.
        tiene_tope_local = any("tope_local" in (f or "") for f in fallos_intentos)
        tiene_credito_agotado = any(f == "credito" for f in fallos_intentos)
        if tiene_credito_agotado:
            _marcar_credito(False)             # 400 'Credit balance too low' → prepago agotado (señal real)
        if tiene_tope_local and not tiene_credito_agotado:
            # Solo tope local: el dinero está, solo hay que subir nuestro presupuesto.
            motivo = ("tope de gasto diario/mensual alcanzado — el dinero en Anthropic sigue ahí, "
                      "solo se agotó el presupuesto que nosotros fijamos")
            desbloquea = ("sube el tope con 1 clic: responde «sube» por Telegram o ejecuta "
                          "python3 tools/cost_guard.py aprobar — el dinero en Anthropic no se toca")
        elif tiene_credito_agotado:
            motivo = "prepago de Anthropic agotado (API devolvió 400 'Credit balance is too low')"
            desbloquea = "recarga el saldo en console.anthropic.com y luego continúa normal"
        else:
            motivo = "todos los cerebros con límite de tasa o fallando (no es falta de saldo)"
            desbloquea = "espera unos minutos a que se recupere la disponibilidad o reintenta"
    _log({"evento": "ask", "sensible": sensible, "critico": critico_explicito,
          "resultado": "parado" if critico_explicito else "aplazado", "intentos": intentos,
          "perfil": perfil, "senales": senales})
    # 🔴 PARADO + aviso FUERTE SOLO si era crítico EXPLÍCITO (clínico/marcado). El auto-sensible sin
    # flag (p.ej. el gateway genérico) NO se sirvió con un flojo, pero se devuelve DEFERRED callado
    # (un "ahora no puedo", sin alarma): no es una tarea clínica declarada.
    if critico_explicito:
        return _parar(motivo, desbloquea, sensible, degradado=degradado, ambiental=ambiental)
    return {"text": None, "brain": None, "coste_usd": 0.0, "degradado": degradado,
            "deferred": True, "parado": False, "motivo": motivo, "nivel": nivel,
            "perfil": perfil}


_PARADO_COOLDOWN_H = 12   # un PARADO persistente avisa 1× y se recuerda cada 12h, NO en bucle


def _debe_avisar(motivo, *, ns="parado", cooldown_h=_PARADO_COOLDOWN_H):
    """Anti-spam GENÉRICO por hash de motivo + cooldown (regla: TODO aviso a {{TITULAR}} necesita dedup —
    feedback-todo-aviso-a-titular-necesita-anti-spam). Nace del aviso de PARADO (una tarea crítica
    bloqueada se REINTENTA en cada ciclo del dispatcher; sin esto salía EN BUCLE, lo vivió {{TITULAR}}
    25/6) y se generaliza (2/7/26) para que OTROS avisos con el mismo problema (p.ej. el bloqueo
    crítico de run_agent.sh) puedan REUSAR el mismo mecanismo en vez de reinventar uno por sitio.
    `ns` namespacea la clave (evita que dos avisos con motivo parecido de sitios distintos se pisen
    el cooldown entre sí). Avisa la 1ª vez de un (ns, motivo) y luego como mucho cada `cooldown_h`;
    un motivo NUEVO avisa al momento. FAIL-LOUD: ante un fallo de estado AVISA (la seguridad clínica
    pesa más que el anti-spam). Determinista/testeable; persistido en state/ia/aviso_parado.json."""
    import hashlib
    import json as _json
    import time as _time
    try:
        os.makedirs(IA_DIR, exist_ok=True)
        p = os.path.join(IA_DIR, "aviso_parado.json")
        clave = ns + ":" + hashlib.sha1((motivo or "").encode("utf-8")).hexdigest()[:12]
        try:
            st = _json.load(open(p, encoding="utf-8"))
            if not isinstance(st, dict):
                st = {}
        except Exception:
            st = {}
        ahora = _time.time()
        if ahora - st.get(clave, 0) < cooldown_h * 3600:
            return False                       # mismo bloqueo dentro de la ventana → silencio (mata el bucle)
        st[clave] = ahora
        st = {k: v for k, v in st.items() if ahora - v < 7 * 86400}   # poda claves viejas
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(st, f)
        os.replace(tmp, p)
        return True
    except Exception:
        return True   # ante la duda, AVISA (seguridad clínica > anti-spam)


def _debe_avisar_parado(motivo):
    """Alias retrocompatible: el aviso de PARADO de `_parar` (ns='parado', cooldown 12h)."""
    return _debe_avisar(motivo, ns="parado", cooldown_h=_PARADO_COOLDOWN_H)


def _parar(motivo, desbloquea, sensible, *, degradado=False, ambiental=False):
    """🔴 Resultado PARADO de una tarea crítica: NO se sirvió (la barrera de seguridad disparó).
    Dispara un AVISO FUERTE a {{TITULAR}} POR SÍ MISMO (no depende del caller — punto 4b del diseño):
    una tarea de seguridad clínica que no se puede atender bien tiene que enterarla ELLA, fuerte,
    con qué la desbloquea. El aviso pasa por el anti-spam (`_debe_avisar_parado`) para no salir en
    bucle; el FRENO no: SIEMPRE devuelve parado=True. Best-effort en la salida.

    `ambiental=True` (14-jul-2026) cambia el TEXTO, nunca la ENTREGA cuando SÍ viene del lazo.

    EL DISCRIMINADOR REAL ES EL ORIGEN, no el tipo de fallo interno (15-jul-2026). Un parón en una
    tarea CRÍTICA solo importa clínicamente si ocurrió DENTRO DEL LAZO (launchd) — eso es lo que
    {{TITULAR}} o un médico accionarían. Un parón en una sesión de DIAGNÓSTICO/test/manual NUNCA es su
    tarea clínica: el que corre el diagnóstico ve el resultado en el valor de retorno, y a {{TITULAR}} no
    hay que pingarla. Antes, cualquiera que probara `ia.ask` crítico —incluso otra sesión de Claude
    haciendo tests en el mini— le disparaba a su teléfono un 🔴 (o un ⚠️) cada pocos minutos.

    Por qué esto NO reabre el riesgo de «suprimir un aviso real»: el suelo era «nunca callar un aviso
    del LAZO». Aquí solo callamos lo que NO es del lazo. Y una caída REAL del lazo (binario perdido,
    saldo, Llavero) la caza el CANARIO independiente de healthcheck (`_check_cerebro_alcanzable`),
    que corre bajo launchd y no comparte este camino. El freno clínico es intocable: SIEMPRE
    devolvemos parado=True; lo único condicionado al origen es a quién se avisa y con qué severidad."""
    es_lazo = False
    try:
        import salida
        _origen, es_lazo = salida._origen()
    except Exception:
        salida = None
    _log({"evento": "parado", "ambiental": ambiental, "es_lazo": es_lazo, "motivo": motivo[:80]})

    # NO viene del lazo → diagnóstico/test/manual. No se pinga a {{TITULAR}} (el runner ve el retorno; el
    # canario de healthcheck cubre la caída real del lazo). Se registra y se devuelve parado.
    if es_lazo and salida is not None and _debe_avisar_parado(motivo):
        try:
            if ambiental:
                texto = ("⚠️ No he podido atender una tarea importante, pero NO es una alarma "
                         "clínica: es el ENTORNO del lazo. %s. Para desbloquearla: %s. La dejo "
                         "pendiente, no se pierde." % (motivo, desbloquea))
            else:
                texto = ("🔴 He PARADO una tarea importante (clínica/sensible) en vez de "
                         "contestarla con un cerebro flojo: %s. Para desbloquearla: %s. La dejo "
                         "pendiente, no se pierde." % (motivo, desbloquea))
            salida.report_to_titular(texto, urgente=True, voz="sobria",
                                    fuente="ia.parado" + (":ambiental" if ambiental else ""))
        except Exception:
            pass
    return {"text": None, "brain": None, "coste_usd": 0.0, "degradado": degradado, "deferred": False,
            "parado": True, "motivo": motivo, "desbloquea": desbloquea, "ambiental": ambiental,
            "es_lazo": es_lazo}


# ── Salud ────────────────────────────────────────────────────────────────────────────────
def _llavero_inaccesible(service):
    """True si NO PODEMOS SABER si la clave existe (el Llavero no es accesible en este contexto).

    Bug real (14-jul-2026): `security find-generic-password` devuelve **rc=36**
    (errSecInteractionNotAllowed) cuando el proceso no puede abrir el Llavero — el caso tipico es
    una sesion **ssh** (trabajar en el mini desde el portatil). `_secrets.get` aplasta ese rc y el
    "no encontrado" (rc=44) en el mismo `None`, asi que health() concluia "claude CAIDO", lo
    persistia en health.json (TTL 5 min) y **cualquier tarea clinica se BLOQUEABA** con "solo hay
    cerebros por debajo del umbral critico" — con Claude perfectamente vivo (capability 9).
    Distinguimos: rc=44 = la clave NO esta (caido de verdad). Cualquier otro fallo = NO SE.
    Ante la duda NO marcamos caido: el relevo reactivo cubre el fallo real en el round-trip. Es la
    misma filosofia que ya aplica el `except` del cost_guard mas abajo."""
    if not service:
        return False
    try:
        import subprocess as _sp
        r = _sp.run(["security", "find-generic-password", "-s", service, "-w"],
                    capture_output=True, timeout=5)
        return r.returncode not in (0, 44)   # 44 = errSecItemNotFound = ausencia REAL
    except Exception:
        return True                          # ni siquiera pudimos preguntar -> no se


def health():
    """Estado de cada cerebro (¿clave puesta? ¿habilitado?). Escribe state/ia/health.json."""
    out = []
    for c in cargar_registro():
        name = c.get("name")
        if c.get("kind") == "carril_gratis":
            try:
                import carril_gratis
                vivo = carril_gratis.disponible()
            except Exception:
                vivo = False
        elif c.get("kind") == "openai_local":
            import socket
            from urllib.parse import urlparse
            u = urlparse(c.get("url") or "http://127.0.0.1:11434")
            try:
                s = socket.create_connection((u.hostname or "127.0.0.1", u.port or 11434), 1.5)
                s.close(); vivo = True
            except Exception:
                vivo = False                      # servidor local no levantado
        elif c.get("kind") == "claude":
            # Claude está "disponible" si hay clave Y no hay bloqueo de PREPAGO REAL (400 API).
            # DISTINCIÓN IMPORTANTE: el tope local (cost_guard) NO marca Claude como "caído" en
            # el caché de salud, porque el caché se usa para saltarse el round-trip a cerebros
            # que no pueden responder — pero un tope local agotado no impide que Claude responda
            # (la API funciona bien), solo significa que no debemos gastar más hoy. Marcar Claude
            # "no disponible" por tope local propagaba un falso "sin saldo" durante 5 min (TTL)
            # y Vega se caía en falso creyendo que no había dinero real.
            # El tope_local lo maneja _call_claude con fallo='tope_local' → la centralita
            # releva al carril gratis; aquí no lo filtramos para no mentir en el caché.
            # "no se" != "caido": si el Llavero no es accesible (ssh), NO marcamos caido.
            _sec = c.get("secret", "")
            tiene_clave = (bool(get_secret(_sec)) or _llavero_inaccesible(_sec)) if _sec else True
            # Solo marcamos no-disponible si hay un bloqueo de PREPAGO conocido (estado persistido).
            # No hacemos round-trip aquí (health() es best-effort, sin gastar tokens).
            tope_local_agotado = False
            prepago_agotado = False
            try:
                okb, motivo_cg, _ = cost_guard.check_before_job()
                if not okb:
                    tipo = cost_guard.tipo_bloqueo(motivo_cg)
                    tope_local_agotado = (tipo == "tope_local")
                    prepago_agotado = (tipo == "prepago_agotado")
            except Exception:
                pass   # ante la duda, no marcar caído (el relevo reactivo lo cubre)
            # "vivo" = puede recibir la petición. Tope local no lo impide (la API responde);
            # prepago agotado sí (la API devolvería 400 en el siguiente intento).
            vivo = tiene_clave and not prepago_agotado
            # Añadimos nota diagnóstica al registro de salud cuando tope local está agotado
            # (para que Vega lo muestre correctamente, sin confundirlo con "caído").
            if tope_local_agotado:
                c = dict(c, _nota_salud="tope_local_agotado")
        elif c.get("salud_fichero"):
            # Cerebros que NO se autentican con una clave del Llavero sino con un token cacheado en
            # disco (el carril de evidencia: OAuth de Consensus/scite). Sin el fichero, el cliente
            # sale rc=1 y la centralita releva — pero conviene saberlo ANTES, no a base de fallar.
            # PRESENCIA ≠ VALIDEZ: el token de Consensus llevaba 28 días caducado con el fichero ahí
            # y el panel en verde (27/7/26). Si el JSON trae `expires_in`, se mira la CADUCIDAD.
            vivo = False
            _ruta = os.path.join(REPO, c["salud_fichero"])
            if os.path.exists(_ruta):
                vivo = True
                try:
                    _tok = json.load(open(_ruta, encoding="utf-8")) or {}
                    _exp = float(_tok.get("expires_in") or 0)
                    if _exp and (os.path.getmtime(_ruta) + _exp) < datetime.now().timestamp():
                        # Caducado ≠ muerto: con refresh token el cliente lo renueva solo antes de
                        # conectar (_oauth_refresh). Sin refresh sí hace falta un login humano.
                        if _tok.get("refresh_token"):
                            c = dict(c, _nota_salud="token_caducado_se_renueva_solo")
                        else:
                            vivo = False
                            c = dict(c, _nota_salud="token_caducado_sin_refresh")
                except Exception:
                    pass          # ilegible ≠ caducado: que lo intente y falle por su cuenta
        else:
            # Mismo criterio que la rama claude: "no puedo leer el Llavero" != "cerebro caido".
            # Sin esto, gemini/fugu se daban por muertos en cualquier sesion ssh (sin Llavero).
            _sec_g = c.get("secret", "")
            vivo = (bool(get_secret(_sec_g)) or _llavero_inaccesible(_sec_g)) if _sec_g else True
        entrada = {"name": name, "kind": c.get("kind"), "trusted": bool(c.get("trusted")),
                   "free": bool(c.get("free")), "enabled": bool(c.get("enabled")),
                   "disponible": bool(vivo)}
        # nota_salud: descripción legible del estado (solo cuando hay algo relevante que aclarar).
        if c.get("_nota_salud"):
            entrada["nota_salud"] = c["_nota_salud"]
        out.append(entrada)
    try:
        os.makedirs(IA_DIR, exist_ok=True)
        tmp = os.path.join(IA_DIR, "health.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "cerebros": out}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, os.path.join(IA_DIR, "health.json"))
    except Exception:
        pass
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────────────────
def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0
    if argv[0] == "--debe-avisar":
        # CLI para que OTROS sitios (p.ej. run_agent.sh, bash) reusen el mismo anti-spam por
        # hash+cooldown que _parar. Uso: ia.py --debe-avisar <ns> <cooldown_h> <motivo...>
        # exit 0 = SÍ avisar (y ya quedó registrado); exit 1 = silencio (dentro de la ventana).
        rest = argv[1:]
        if len(rest) < 3:
            sys.stderr.write("uso: ia.py --debe-avisar <ns> <cooldown_h> <motivo...>\n"); return 2
        ns, cooldown_h, motivo = rest[0], rest[1], " ".join(rest[2:])
        try:
            ch = float(cooldown_h)
        except Exception:
            ch = _PARADO_COOLDOWN_H
        return 0 if _debe_avisar(motivo, ns=ns, cooldown_h=ch) else 1
    if argv[0] == "--health":
        for c in health():
            print("%s %-14s %s%s" % ("✅" if c["disponible"] else "⚪", c["name"],
                                     "[confianza] " if c["trusted"] else "",
                                     "(habilitado)" if c["enabled"] else "(off)"))
        return 0
    clinico = "--clinico" in argv
    argv = [a for a in argv if a != "--clinico"]
    explicar = "--por-que" in argv
    argv = [a for a in argv if a != "--por-que"]
    system = prefer = nivel = para = None
    if "--system" in argv:
        i = argv.index("--system"); system = argv[i + 1]; argv = argv[:i] + argv[i + 2:]
    if "--prefer" in argv:
        i = argv.index("--prefer"); prefer = argv[i + 1]; argv = argv[:i] + argv[i + 2:]
    if "--nivel" in argv:
        i = argv.index("--nivel"); nivel = argv[i + 1]; argv = argv[:i] + argv[i + 2:]
    if "--para" in argv:
        i = argv.index("--para"); para = argv[i + 1]; argv = argv[:i] + argv[i + 2:]
    prompt = " ".join(argv).strip()
    if not prompt:
        print('uso: ia.py "tu pregunta" [--clinico] [--nivel rutina|sustantivo|critico] '
              '[--para evidencia|descubrir|osint-x|web-frontend|codigo|volumen] '
              '[--system "..."] [--prefer claude] | --por-que "..." | --health')
        return 2
    if explicar:
        d = por_que(prompt, clinico=clinico, nivel=nivel, para=para)
        print("perfil (intent):        %s%s"
              % (d["perfil"], ("  ← " + ", ".join(d["senales"])) if d["senales"] else ""))
        print("nivel (criticidad):     %s%s"
              % (d["nivel"], "  · SENSIBLE → solo proveedor de confianza" if d["sensible"] else ""))
        print("cadena de relevo:       %s"
              % (" → ".join(d["cadena"]) or "(ningún proveedor habilitado)"))
        return 0
    r = ask(prompt, clinico=clinico, system=system, prefer=prefer, nivel=nivel, para=para)
    if r.get("text"):
        print(r["text"])
        sys.stderr.write("— cerebro: %s%s  coste $%.4f\n" % (
            r["brain"], " (relevo)" if r.get("degradado") else "", r.get("coste_usd") or 0.0))
        return 0
    if r.get("deferred"):
        sys.stderr.write("⏸️ aplazado: %s\n" % r["motivo"]); return 4
    sys.stderr.write("🛑 %s\n" % r["motivo"]); return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
