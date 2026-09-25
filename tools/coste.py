#!/usr/bin/env python3
"""Medidor de gasto (tokens + € + APIs) de Beyond the Protocol. Determinista, SIN LLM (no gasta nada).

Idea ({{TITULAR}}, 21/6/26): que cada vez que se gastan tokens haya una DECISIÓN de nivel de precisión/modelo.
Este tool NO es un agente que corre todo el rato (eso quemaría el recurso que vigila): es un MEDIDOR que
miras cuando quieres + un resumen diario que ya cosecha la rutina de auto-mejora. La ESTRATEGIA de qué
modelo/precisión usar vive en CLAUDE.md (memoria feedback-estrategia-coste-precision).

Qué mide:
  1) Tokens de Claude Code por modelo y por día, leyendo los transcripts JSONL de ~/.claude/projects/.
     OJO suscripción: si vas por plan (Max/Pro) el € marginal por token puede ser ~0 — lo que importa
     entonces es el VOLUMEN de tokens (consume tu cuota / rate-limit). El € es ESTIMADO y orientativo.
  2) APIs de pago por uso (Grok/Perplexity/NVIDIA…) si las tools registran su uso en
     tools/.gasto_ledger.jsonl (líneas {"ts","tool","model","input_tokens","output_tokens","usd"}).

Uso:
  python3 coste.py                 # resumen: hoy + últimos 7 días (este proyecto)
  python3 coste.py --days 30       # ventana de N días
  python3 coste.py --today         # solo hoy
  python3 coste.py --by-session    # desglose por sesión
  python3 coste.py --all           # todos los proyectos de ~/.claude/projects, no solo este
  python3 coste.py --json          # salida JSON cruda

Precios: ESTIMADOS, ajústalos en tools/.precios.json ($/millón de tokens):
  {"claude-opus-4-8": {"input":15,"output":75,"cache_read":1.5,"cache_write":18.75}, ...}
"""
import glob, json, os, re, sys
from collections import defaultdict

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
THIS_PROJECT = "-Users-polaris-claudecode"
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))


def proyectos_del_repo():
    """Directorios de transcript que pertenecen a ESTE repo: casa base MÁS sus worktrees.

    Claude Code nombra cada carpeta por la ruta del cwd con los `/` vueltos `-`, así que un worktree
    de casa base se llama `-Users-polaris-claudecode--claude-worktrees-<rama>`: todos comparten el
    prefijo de casa base. Medir solo `THIS_PROJECT` (lo que hacía el Observatorio) dejaba fuera 89 de
    los 95 directorios — y como CLAUDE.md ordena aislar cada sesión en su worktree, lo invisible era
    justamente el trabajo de construcción. Bajo Max el recurso escaso es la CUOTA: se podía llegar a
    un rate-limit en mitad de algo clínico con el panel en verde.

    Devuelve rutas absolutas, ordenadas y con casa base primero. Excluye `-private-tmp-…` (cwds de
    scratchpad): son de sesión, no del repo."""
    if not os.path.isdir(PROJECTS):
        return []
    base = os.path.join(PROJECTS, THIS_PROJECT)
    otros = sorted(
        os.path.join(PROJECTS, d) for d in os.listdir(PROJECTS)
        if d.startswith(THIS_PROJECT) and d != THIS_PROJECT
        and os.path.isdir(os.path.join(PROJECTS, d))
    )
    return ([base] if os.path.isdir(base) else []) + otros


def es_casa_base(ruta):
    """True si el directorio de transcripts es el de casa base (no un worktree)."""
    return os.path.basename(ruta.rstrip(os.sep)) == THIS_PROJECT


# El ledger de APIs de pago vive en CASA BASE, no en el worktree desde el que se lee: lo escribe
# `gasto.py` con esta misma regla (está en .gitignore, así que un ledger de worktree no se fusiona
# nunca). Sin esto, `coste.py` corrido desde un worktree enseñaba un ledger vacío.
def _ledger_path():
    base = os.environ.get("BTP_GASTO_LEDGER")
    if base:
        return base
    repo = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
    casa = os.path.join(repo, "tools")
    return os.path.join(casa if os.path.isdir(casa) else TOOLS_DIR, ".gasto_ledger.jsonl")


LEDGER = _ledger_path()
PRECIOS_FILE = os.path.join(TOOLS_DIR, ".precios.json")

# $/millón de tokens. En suscripción Max el recurso real es CUOTA, no €/token
# (feedback-no-quemar-planificando-el-ahorro): este $ es un coste API-EQUIVALENTE, la señal de «cuánto
# quemaría por token», no un desembolso cuando el lazo va por Claude Code CLI.
#
# Tarifa API OFICIAL, cotejada el 13-sep-2026 contra platform.claude.com/docs/en/about-claude/pricing.
# Hasta ese día la tabla mezclaba escalas (Opus 4.8 al triple de su tarifa, Sonnet y Haiku a la suya)
# y NO tenía `claude-opus-5`, `claude-sonnet-5` ni `claude-fable-5-1`, que eran el grueso del gasto:
# `price_for` es de coincidencia exacta, así que todo eso se tarifaba a CERO y el medidor parecía
# barato justo donde más se gastaba. `tests/test_coste_modelos.py` exige ahora que todo modelo
# `claude-*` visto en los transcripts de los últimos 30 días tenga fila aquí.
#
# `cache_write` es la escritura de 5 minutos (1,25× input) y `cache_write_1h` la de 1 hora (2×). Las
# sesiones de Claude Code escriben casi siempre a 1 hora (57.608 registros frente a 22.919 de 5 min,
# medido el 13-sep), así que tarifarlo todo como 5 min infravaloraba la caché.
DEFAULT_PRECIOS = {
    "claude-fable-5-1":  {"input": 10.0, "output": 50.0, "cache_read": 0.25, "cache_write": 12.5,
                          "cache_write_1h": 20.0},
    "claude-fable-5":    {"input": 10.0, "output": 50.0, "cache_read": 1.0,  "cache_write": 12.5,
                          "cache_write_1h": 20.0},
    # Opus 5.5 (24-sep-2026): apareció en los transcripts sin fila aquí y `price_for` es de
    # coincidencia EXACTA, así que se estaba tarifando a cero. input/output/cache_read salen de la
    # tabla de precios de la referencia de la API (skill `claude-api`, caché de 24-jun-2026);
    # las dos escrituras de caché son la regla de siempre (1,25× y 2× el input), NO una cifra leída.
    "claude-opus-5-5":   {"input": 4.0,  "output": 20.0, "cache_read": 0.2,  "cache_write": 5.0,
                          "cache_write_1h": 8.0},
    "claude-opus-5":     {"input": 5.0,  "output": 25.0, "cache_read": 0.5,  "cache_write": 6.25,
                          "cache_write_1h": 10.0},
    "claude-opus-4-8":   {"input": 5.0,  "output": 25.0, "cache_read": 0.5,  "cache_write": 6.25,
                          "cache_write_1h": 10.0},
    "claude-sonnet-5":   {"input": 2.0,  "output": 10.0, "cache_read": 0.2,  "cache_write": 2.5,
                          "cache_write_1h": 4.0},
    "claude-sonnet-4-6": {"input": 3.0,  "output": 15.0, "cache_read": 0.3,  "cache_write": 3.75,
                          "cache_write_1h": 6.0},
    "claude-haiku-4-5":  {"input": 1.0,  "output": 5.0,  "cache_read": 0.1,  "cache_write": 1.25,
                          "cache_write_1h": 2.0},
    "grok-4.3":          {"input": 3.0,  "output": 15.0, "cache_read": 0.75, "cache_write": 3.0},
    "sonar":             {"input": 1.0,  "output": 1.0,  "cache_read": 0.0,  "cache_write": 0.0},
    "sonar-pro":         {"input": 3.0,  "output": 15.0, "cache_read": 0.0,  "cache_write": 0.0},
    # 24-sep-2026: estas cuatro filas vivían SOLO en la tabla paralela de `gasto.py`, que se ha
    # borrado (dos tablas que divergen son la causa raíz del agujero de Opus 5.5). Son las cifras
    # que ya estaban allí, etiquetadas «ESTIMADO»: se mudan tal cual, NO se han cotejado contra la
    # tarifa del proveedor en ninguna sesión → siguen siendo INFERIDAS. `gasto.py` solo apunta
    # input/output, así que la caché de estos cuatro va a 0 por no tener cifra, no por ser gratis.
    # `perplexity.py` apunta el modelo como `perplexity/sonar` (así lo pide su API): 466 líneas del
    # ledger de casa base iban con `usd: null` por no casar con la fila `sonar` (contado hoy). Fila
    # explícita y no un stripper genérico de `proveedor/modelo`: un `openai/gpt-4o` de OpenRouter NO
    # cuesta necesariamente lo que el gpt-4o de OpenAI, y adivinarlo sería inventar la cifra.
    "perplexity/sonar":     {"input": 1.0, "output": 1.0,  "cache_read": 0.0, "cache_write": 0.0},
    "perplexity/sonar-pro": {"input": 3.0, "output": 15.0, "cache_read": 0.0, "cache_write": 0.0},
    "gpt-5":             {"input": 1.25, "output": 10.0, "cache_read": 0.0,  "cache_write": 0.0},
    "gemini-2.5-pro":    {"input": 1.25, "output": 10.0, "cache_read": 0.0,  "cache_write": 0.0},
    "gemini-3":          {"input": 1.25, "output": 10.0, "cache_read": 0.0,  "cache_write": 0.0},
    "glm-5.2":           {"input": 0.6,  "output": 2.2,  "cache_read": 0.0,  "cache_write": 0.0},
}
# Modelos abiertos de NVIDIA NIM = tier gratis → 0 (ajusta si pasas a pago).
NVIDIA_FREE_PREFIXES = ("meta/", "deepseek-ai/", "qwen/", "nvidia/", "mistralai/", "google/")


def precios():
    p = {k: dict(v) for k, v in DEFAULT_PRECIOS.items()}
    if os.path.exists(PRECIOS_FILE):
        try:
            with open(PRECIOS_FILE) as f:
                for k, v in (json.load(f) or {}).items():
                    p[k] = v
        except Exception:
            pass
    return p


_SUFIJO_FECHA = re.compile(r"-\d{8}$")


_SUFIJO_VENTANA = re.compile(r"\[\d+[kKmM]\]$")


def price_for(model, tabla, tool=None):
    if model in tabla:
        return tabla[model]
    # `claude-haiku-4-5-20251001` es `claude-haiku-4-5` con fecha: sin esto se tarifaba a cero.
    base = _SUFIJO_FECHA.sub("", model or "")
    if base != model and base in tabla:
        return tabla[base]
    # `claude-opus-5[1m]` es el MISMO modelo con la ventana de contexto anotada. Toda la
    # generación actual trae 1M de serie y sin tarifa aparte (cotejado 17-sep-2026 contra la
    # tabla de precios de la API), así que el sufijo no cambia el precio — pero al ser
    # `price_for` de coincidencia exacta, sin esto se tarifaba a CERO, que es justo el
    # agujero que abrió esta tabla en septiembre. Si algún día una ventana se cobra aparte,
    # la fila explícita (`claude-x[1m]`) gana, porque se busca antes.
    sin_ventana = _SUFIJO_VENTANA.sub("", base)
    if sin_ventana != base and sin_ventana in tabla:
        return tabla[sin_ventana]
    # El tier gratis de NVIDIA NIM es un juicio sobre el PROVEEDOR, y estaba aplicado al NOMBRE del
    # modelo. Comprobado el 24-sep-2026: eso tarifaba a CERO los `google/…`, `mistralai/…` y
    # `qwen/…` que sirve OpenRouter, que se pagan — tres líneas del ledger pasaban de `usd: null`
    # (honesto) a 0,0 (mentira). Ahora hace falta decir de qué tool viene la llamada.
    if tool == "nvidia" and model and model.startswith(NVIDIA_FREE_PREFIXES):
        return {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0}
    return None  # desconocido → tokens sí, € no


def usd(tok, pr):
    """$ de un dict de tokens. `cache_write` es el TOTAL escrito en caché y `cache_write_1h` la parte
    de ese total escrita a 1 hora: esa parte va a su tarifa (2× input) y el resto a la de 5 minutos."""
    if not pr:
        return 0.0
    cw = tok.get("cache_write", 0) or 0
    cw_1h = min(tok.get("cache_write_1h", 0) or 0, cw)
    return (tok.get("input", 0) * pr.get("input", 0) + tok.get("output", 0) * pr.get("output", 0)
            + tok.get("cache_read", 0) * pr.get("cache_read", 0)
            + (cw - cw_1h) * pr.get("cache_write", 0)
            + cw_1h * pr.get("cache_write_1h", pr.get("cache_write", 0))) / 1_000_000.0


def empty():
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cache_write_1h": 0}


def tokens_total(t):
    """Tokens de un dict de `scan`. NO sumes `t.values()`: `cache_write_1h` es una PARTE de
    `cache_write` y contarías esos tokens dos veces."""
    return (t.get("input", 0) + t.get("output", 0) + t.get("cache_read", 0)
            + t.get("cache_write", 0))


def add(dst, u):
    dst["input"] += u.get("input_tokens", 0) or 0
    dst["output"] += u.get("output_tokens", 0) or 0
    dst["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
    dst["cache_write"] += u.get("cache_creation_input_tokens", 0) or 0
    desglose = u.get("cache_creation")
    if isinstance(desglose, dict):
        dst["cache_write_1h"] = dst.get("cache_write_1h", 0) + (
            desglose.get("ephemeral_1h_input_tokens", 0) or 0)


def transcripts(proyecto):
    """Todos los transcripts JSONL de un directorio de proyecto, no solo las sesiones principales.

    Claude Code guarda cada subagente en `<sesión>/subagents/agent-*.jsonl` y cada agente de un
    Workflow en `<sesión>/subagents/workflows/wf_*/agent-*.jsonl`. Hasta el 13-sep-2026 aquí solo se
    miraba `<proyecto>/*.jsonl`: 225 de 942 transcripts del último mes quedaban fuera, y con ellos todo
    el gasto de los workflows. No se duplica: comprobado ese día que el uso de un subagente no aparece
    también en el transcript de su sesión madre. `journal.jsonl` es el diario del workflow, sin uso."""
    fuera = glob.glob(os.path.join(proyecto, "*.jsonl"))
    fuera += glob.glob(os.path.join(proyecto, "*", "subagents", "*.jsonl"))
    fuera += glob.glob(os.path.join(proyecto, "*", "subagents", "workflows", "*", "agent-*.jsonl"))
    return sorted(set(fuera))


def scan(files):
    """Devuelve por_modelo, por_dia, por_sesion (cada uno dict→tokens dict)."""
    por_modelo = defaultdict(empty)
    por_dia = defaultdict(empty)
    por_sesion = defaultdict(empty)
    for f in files:
        sid = os.path.splitext(os.path.basename(f))[0][:8]
        try:
            fh = open(f)
        except Exception:
            continue
        with fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                msg = r.get("message") or {}
                u = msg.get("usage") or r.get("usage")
                if not isinstance(u, dict):
                    continue
                model = msg.get("model") or r.get("model") or "?"
                if model == "<synthetic>":
                    continue
                day = (r.get("timestamp") or "")[:10] or "????-??-??"
                add(por_modelo[model], u)
                add(por_dia[day], u)
                add(por_sesion[f"{day} {sid}"], u)
    return por_modelo, por_dia, por_sesion


def total(tokmap):
    t = empty()
    for v in tokmap.values():
        for k in t:
            t[k] += v[k]
    return t


def read_ledger():
    rows = []
    if os.path.exists(LEDGER):
        with open(LEDGER) as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def ledger_usd(rows, tabla=None):
    """Suma el ledger diciendo QUÉ no pudo sumar. Devuelve (usd, n_con_usd, n_retarifadas, sin_tarifa).

    24-sep-2026: aquí se hacía `float(r.get("usd", 0) or 0)`, que aplasta a 0 una línea sin tarifa.
    Contado ese día en el ledger de casa base: 466 de 1380 líneas (34%) iban con `usd: null` —
    todo `perplexity/sonar`, que no casaba con la fila `sonar`— y el informe decía «~$0.01» tan
    tranquilo. Cero significa «no costó»; null significa «no lo sé», y no son lo mismo.

    `n_retarifadas` son las que venían sin `usd` y AHORA tienen fila: se calculan al vuelo desde los
    tokens, que el ledger sí guardó exactos, así que el histórico se sana sin reescribir el fichero.
    `sin_tarifa` es la lista de (tool, modelo) que siguen sin poderse tarifar: esas NO se suman."""
    tabla = tabla if tabla is not None else precios()
    total, n_usd, n_retarifadas = 0.0, 0, 0
    sin_tarifa = defaultdict(int)
    for r in rows:
        v = r.get("usd")
        if isinstance(v, (int, float)):
            total += float(v); n_usd += 1
            continue
        pr = price_for(r.get("model") or "", tabla, r.get("tool"))
        if pr:
            total += ((r.get("input_tokens", 0) or 0) * pr.get("input", 0)
                      + (r.get("output_tokens", 0) or 0) * pr.get("output", 0)) / 1_000_000.0
            n_retarifadas += 1
        else:
            sin_tarifa[(r.get("tool"), r.get("model"))] += 1
    return total, n_usd, n_retarifadas, dict(sin_tarifa)


def fmt_tok(t):
    tot = t["input"] + t["output"] + t["cache_read"] + t["cache_write"]
    return (f"{tot/1000:,.0f}k tok  (in {t['input']/1000:,.0f}k · out {t['output']/1000:,.0f}k · "
            f"cache r {t['cache_read']/1000:,.0f}k / w {t['cache_write']/1000:,.0f}k)")


def main():
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__); return
    want_json = "--json" in args
    by_session = "--by-session" in args
    scope_all = "--all" in args
    only_today = "--today" in args
    days = 7
    if "--days" in args:
        try:
            days = int(args[args.index("--days") + 1])
        except Exception:
            pass

    # `transcripts` también mira subagentes y agentes de Workflow, no solo las sesiones principales.
    # Y el alcance por defecto es TODO el repo (casa base + sus worktrees), no solo casa base:
    # `proyectos_del_repo` existía desde el 25-jul-2026 y la usaban anatomía, el Observatorio y
    # lentes, pero ESTE CLI se había quedado midiendo `THIS_PROJECT` a secas. Medido el 24-sep-2026:
    # `claude-opus-5-5` no aparecía en `python3 coste.py` porque sus 3 sesiones eran worktrees — o
    # sea, la herramienta con la que se mide el agujero no podía verlo.
    if scope_all:
        files = [f for p in glob.glob(os.path.join(PROJECTS, "*")) if os.path.isdir(p)
                 for f in transcripts(p)]
    else:
        files = [f for p in proyectos_del_repo() for f in transcripts(p)]
    if not files:
        print("No encuentro transcripts en", PROJECTS); return

    por_modelo, por_dia, por_sesion = scan(files)
    tabla = precios()

    # ventana temporal sobre días con datos
    dias_ord = sorted(d for d in por_dia if d != "????-??-??")
    if only_today and dias_ord:
        ventana = dias_ord[-1:]
    else:
        ventana = dias_ord[-days:]
    vset = set(ventana)

    # recomputar modelo/total restringido a la ventana → re-escaneo barato por día no guardado; usamos por_dia para totales
    tot_ventana = empty()
    for d in ventana:
        for k in tot_ventana:
            tot_ventana[k] += por_dia[d][k]

    ledger = read_ledger()
    ledger_v = [r for r in ledger if (r.get("ts") or "")[:10] in vset] if vset else ledger
    l_usd, l_n, l_retar, l_sin = ledger_usd(ledger_v, tabla)

    if want_json:
        out = {
            "ventana_dias": ventana,
            "por_modelo": {m: {**t, "usd_estimado": round(usd(t, price_for(m, tabla)), 4)}
                           for m, t in por_modelo.items()},
            "por_dia": {d: dict(t) for d, t in sorted(por_dia.items())},
            "total_ventana": tot_ventana,
            "apis_pago_ledger_usd": round(l_usd, 4),
            "apis_pago_retarifadas": l_retar,
            "apis_pago_sin_tarifa": {"%s/%s" % k: v for k, v in l_sin.items()},
        }
        print(json.dumps(out, ensure_ascii=False, indent=2)); return

    alcance = "TODOS los proyectos" if scope_all else "este repo (casa base + worktrees)"
    print(f"💸 GASTO — {alcance} · ventana: {ventana[0] if ventana else '—'} → {ventana[-1] if ventana else '—'}")
    print("   (€ ESTIMADO; en suscripción el € marginal ≈ 0 → mira el VOLUMEN de tokens)\n")

    print("Por día:")
    for d in ventana:
        print(f"  {d}  {fmt_tok(por_dia[d])}")

    print("\nPor modelo (histórico completo):")
    tot_usd = 0.0
    for m, t in sorted(por_modelo.items(), key=lambda kv: -(kv[1]['input'] + kv[1]['output'])):
        pr = price_for(m, tabla)
        c = usd(t, pr)
        tot_usd += c
        ce = f"~${c:,.2f}" if pr else "€? (modelo sin precio)"
        print(f"  {m:22s} {fmt_tok(t)}  {ce}")
    print(f"\n  TOTAL € estimado (histórico, modelos con precio): ~${tot_usd:,.2f}")

    if ledger:
        print(f"\nAPIs de pago por uso (ledger, ventana): ~${l_usd:,.2f}  ({len(ledger_v)} llamadas)")
        if l_retar:
            print(f"  · {l_retar} re-tarifadas ahora desde sus tokens (se apuntaron sin tarifa)")
        if l_sin:
            n = sum(l_sin.values())
            print(f"  ⚠️  {n} llamada(s) SIN TARIFA, NO sumadas (su gasto existió y no se sabe cuánto):")
            for (tool, modelo), c in sorted(l_sin.items(), key=lambda kv: -kv[1]):
                print(f"       {c:>5}x  {tool}  {modelo}")
    else:
        print("\nAPIs de pago por uso: sin ledger todavía (tools/.gasto_ledger.jsonl). "
              "Grok/Perplexity/NVIDIA aún no registran su uso aquí.")

    if by_session:
        print("\nPor sesión (ventana):")
        for s in sorted(por_sesion):
            if s[:10] in vset:
                print(f"  {s}  {fmt_tok(por_sesion[s])}")


if __name__ == "__main__":
    main()
