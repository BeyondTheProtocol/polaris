#!/usr/bin/env python3
"""tools/usage_coste.py — ¿Dónde se van los tokens? Atribución por agente/MCP/skill.

Cruza los transcripts JSONL de ~/.claude/projects con el medidor de coste (coste.py)
para responder: qué agente/skill/MCP se come el presupuesto, y dónde hay fuga.

FUENTES DE DATOS (sin red, sin LLM, $0):
  1. Subagent JSONL: archivos ~/.claude/projects/<proj>/<session>/subagents/agent-*.jsonl
     Cada línea tiene 'attributionAgent' → nombre canónico del agente (git, tecnico, Explore…)
     y 'usage' con tokens exactos por turno.
  2. Session JSONL principales: atribuidos al «orquestador» o sesión directa (sin subagente).
  3. MCP tool_use: los bloques 'tool_use' en mensajes de assistant. Nombre formato
     'mcp__<servidor>__<tool>' → se extrae el servidor como dimensión de gasto.

NOTA sobre /usage de Claude Code:
  El comando /usage dentro de Claude Code muestra un resumen de tokens de las últimas
  24h/7d, pero NO expone una API ni fichero leíble por código. Los datos subyacentes
  son exactamente los JSONL de ~/.claude/projects/ — esta tool los lee directamente,
  así que tienes la misma información (y más: por agente, por MCP, por día) sin depender
  del comando interactivo. Cruce manual con /usage: ver sección al final del informe.

Uso:
  python3 usage_coste.py                # informe por agente, últimos 7 días (este proyecto)
  python3 usage_coste.py --days 30      # ventana más larga
  python3 usage_coste.py --all          # todos los proyectos de ~/.claude/projects
  python3 usage_coste.py --by-mcp       # desglose también por servidor MCP
  python3 usage_coste.py --fugas        # solo banderas de fuga (agentes caros sin retorno)
  python3 usage_coste.py --json         # salida JSON cruda (para pipelines)
  python3 usage_coste.py --today        # solo hoy

Precios: los mismos que coste.py (comparte tools/.precios.json).
"""
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HOME = os.path.expanduser("~")
PROJECTS_DIR = os.path.join(HOME, ".claude", "projects")
THIS_PROJECT = "-Users-polaris-claudecode"
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PRECIOS_FILE = os.path.join(TOOLS_DIR, ".precios.json")

# Precios $/millón de tokens: los de coste.py, que es la fuente de verdad. Aquí había un ESPEJO a
# mano que se quedó en Opus 4.8 al triple de su tarifa y sin los modelos 5 (13-sep-2026): dos copias
# divergen, así que ya no hay copia.
sys.path.insert(0, TOOLS_DIR)
import coste  # noqa: E402
from coste import DEFAULT_PRECIOS  # noqa: E402
NVIDIA_FREE = coste.NVIDIA_FREE_PREFIXES   # una sola lista, la de coste.py

# Mapeo de UUID internos de servidores MCP a nombres legibles.
# Cuando Claude Code asigna un UUID al servidor en vez de nombre textual, aquí lo resolvemos.
MCP_UUID_NAMES = {
    "1cc209a7-1f1f-42f3-90a5-0f6c1c9a777c": "notion",
    "b47695e8-1614-4b1d-81db-9d1d88117c68": "gmail",
    "b08b78ef-28ae-4b17-baf9-5cfd6efef4b0": "pubmed-articles",
    "6e48e780-9f62-48ae-8d52-8d8580968023": "google-drive",
    "ccd_session": "ccd_session",
    "ccd_session_mgmt": "ccd_session_mgmt",
}


def _resolve_mcp(name):
    """Resuelve el nombre del servidor MCP (UUID → nombre legible si está en el mapa)."""
    return MCP_UUID_NAMES.get(name, name)


# Umbrales para banderas de fuga (orientativos)
FUGA_AGENTE_TOK_K = 50_000   # agente con >50M tokens (histórico) = revisar
FUGA_CACHE_WRITE_RATIO = 0.5 # cache_write > 50% del total = mucho contexto sin reusar
FUGA_MCP_CALLS = 30          # servidor MCP con >30 llamadas = "impuesto MCP" alto


def _precios():
    p = {k: dict(v) for k, v in DEFAULT_PRECIOS.items()}
    if os.path.exists(PRECIOS_FILE):
        try:
            with open(PRECIOS_FILE) as f:
                overrides = json.load(f) or {}
            for k, v in overrides.items():
                p[k] = v
        except Exception:
            pass
    return p


# 24-sep-2026: aquí quedaban DOS matchers propios. La tabla ya venía de `coste.py`, pero el
# emparejamiento y la suma no, y divergían igual:
#   · `_price_for` casaba por PREFIJO contra TODA la tabla → `sonar-deep-research` cobraba la
#     tarifa de `sonar`. Un precio equivocado es peor que ninguno, porque no se nota.
#   · `_usd` no conocía `cache_write_1h`, así que tarifaba a 5 minutos la caché de 1 hora, que es
#     la que escriben casi siempre las sesiones de Claude Code (1,25× en vez de 2× el input).
# Compartir el diccionario no basta: se comparten `price_for` y `usd`, que son la decisión.
_price_for = coste.price_for
_usd = coste.usd


def _empty():
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
            "n_turns": 0, "models": defaultdict(int)}


def _add(dst, u, model):
    dst["input"] += u.get("input_tokens", 0) or 0
    dst["output"] += u.get("output_tokens", 0) or 0
    dst["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
    dst["cache_write"] += u.get("cache_creation_input_tokens", 0) or 0
    dst["n_turns"] += 1
    if model and model != "<synthetic>":
        dst["models"][model] += 1


def _total_tok(t):
    return t["input"] + t["output"] + t["cache_read"] + t["cache_write"]


def _parse_ts(ts_str):
    """ISO timestamp → date string YYYY-MM-DD o '????-??-??'."""
    if not ts_str:
        return "????-??-??"
    try:
        return ts_str[:10]
    except Exception:
        return "????-??-??"


def scan(project_dir, cutoff_date=None):
    """
    Escanea todos los JSONL (principales + subagentes) de project_dir.
    Devuelve:
      - by_agent: {agente → tokens+coste}
      - by_mcp:   {servidor_mcp → {calls, agents}}
      - by_day:   {fecha → tokens}
      - session_counts: {agente → n_sesiones}
    """
    by_agent = defaultdict(_empty)
    by_mcp = defaultdict(lambda: {"calls": 0, "agents": defaultdict(int)})
    by_day = defaultdict(_empty)

    # Archivos de subagentes: tienen attributionAgent = nombre del agente
    subagent_files = glob.glob(os.path.join(project_dir, "*", "subagents", "*.jsonl"))
    # Archivos de sesión principal: atribuidos al orquestador/sesión directa
    main_files = glob.glob(os.path.join(project_dir, "*.jsonl"))

    def process_file(fp, default_agent):
        current_agent = default_agent
        with open(fp, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue

                # El campo attributionAgent (en subagentes) da el agente real
                aa = r.get("attributionAgent")
                if aa:
                    current_agent = aa

                day = _parse_ts(r.get("timestamp", ""))

                # Filtro de fecha
                if cutoff_date and day != "????-??-??" and day < cutoff_date:
                    continue

                msg = r.get("message") or {}
                u = msg.get("usage")
                model = (msg.get("model") or r.get("model") or "?").strip()
                if model == "<synthetic>":
                    continue

                if isinstance(u, dict):
                    _add(by_agent[current_agent], u, model)
                    if day != "????-??-??":
                        _add(by_day[day], u, model)

                # MCP tool calls
                content = msg.get("content") or []
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_use":
                            tool_name = block.get("name", "")
                            if "mcp__" in tool_name:
                                parts = tool_name.split("__")
                                srv = _resolve_mcp(parts[1] if len(parts) > 1 else tool_name)
                                by_mcp[srv]["calls"] += 1
                                by_mcp[srv]["agents"][current_agent] += 1

    # Subagentes primero (tienen attribution explícita)
    for fp in subagent_files:
        try:
            process_file(fp, "subagente-sin-atribucion")
        except Exception:
            pass

    # Sesiones principales → atribuidas al «orquestador»
    for fp in main_files:
        try:
            process_file(fp, "orquestador")
        except Exception:
            pass

    return by_agent, by_mcp, by_day


def _fmt_tok(t):
    return (f"in {t['input']/1_000:,.0f}k · out {t['output']/1_000:,.0f}k · "
            f"cache_r {t['cache_read']/1_000:,.0f}k / cache_w {t['cache_write']/1_000:,.0f}k")


def _dominant_model(t):
    models = t.get("models", {})
    if not models:
        return "?"
    return max(models, key=models.get)


def _fugas(by_agent, by_mcp, tabla):
    """Detecta banderas de fuga."""
    flags = []
    for agent, t in by_agent.items():
        tot_k = _total_tok(t) // 1_000
        if tot_k > FUGA_AGENTE_TOK_K:
            dom = _dominant_model(t)
            pr = _price_for(dom, tabla)
            c = _usd(t, pr)
            flags.append(f"AGENTE CARO  {agent:25s} {tot_k:,}k tok (~${c:.2f}) — revisar si el volumen es justificado")

        # Cache write ratio alto = contextos grandes que no se reusan eficientemente
        tot = _total_tok(t)
        if tot > 500_000 and t["cache_write"] > tot * FUGA_CACHE_WRITE_RATIO:
            flags.append(f"CACHE WRITE ALTO  {agent:20s} cache_write={t['cache_write']/1_000:,.0f}k "
                         f"({100*t['cache_write']//tot}% del total) — prompt grande sin reusar")

    for srv, info in by_mcp.items():
        if info["calls"] > FUGA_MCP_CALLS:
            top_agent = max(info["agents"], key=info["agents"].get) if info["agents"] else "?"
            flags.append(f"MCP ACTIVO   {srv:25s} {info['calls']} llamadas — 'impuesto' de contexto; "
                         f"mayor uso: {top_agent}")

    return flags


def _cutoff_str(days):
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%d")


def main():
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    want_json = "--json" in args
    by_mcp_flag = "--by-mcp" in args
    fugas_only = "--fugas" in args
    scope_all = "--all" in args
    only_today = "--today" in args

    days = 7
    if "--days" in args:
        try:
            days = int(args[args.index("--days") + 1])
        except Exception:
            pass
    if only_today:
        days = 1

    cutoff = _cutoff_str(days)

    if scope_all:
        project_dirs = [
            d for d in glob.glob(os.path.join(PROJECTS_DIR, "*"))
            if os.path.isdir(d)
        ]
    else:
        project_dirs = [os.path.join(PROJECTS_DIR, THIS_PROJECT)]

    # Acumular sobre todos los proyectos pedidos
    all_agent = defaultdict(_empty)
    all_mcp = defaultdict(lambda: {"calls": 0, "agents": defaultdict(int)})
    all_day = defaultdict(_empty)

    for pd in project_dirs:
        if not os.path.isdir(pd):
            continue
        ba, bm, bd = scan(pd, cutoff_date=cutoff)
        for k, v in ba.items():
            for field in ("input", "output", "cache_read", "cache_write", "n_turns"):
                all_agent[k][field] += v[field]
            for m, c in v["models"].items():
                all_agent[k]["models"][m] += c
        for srv, info in bm.items():
            all_mcp[srv]["calls"] += info["calls"]
            for ag, cnt in info["agents"].items():
                all_mcp[srv]["agents"][ag] += cnt
        for d, v in bd.items():
            for field in ("input", "output", "cache_read", "cache_write", "n_turns"):
                all_day[d][field] += v[field]

    tabla = _precios()
    fugas = _fugas(all_agent, all_mcp, tabla)

    if want_json:
        out = {
            "ventana": {"dias": days, "desde": cutoff},
            "por_agente": {},
            "por_mcp": {},
            "por_dia": {},
            "fugas": fugas,
        }
        for ag, t in sorted(all_agent.items(), key=lambda kv: -_total_tok(kv[1])):
            dom = _dominant_model(t)
            pr = _price_for(dom, tabla)
            out["por_agente"][ag] = {
                **{k: t[k] for k in ("input", "output", "cache_read", "cache_write", "n_turns")},
                "total_tok": _total_tok(t),
                "modelo_dominante": dom,
                "usd_estimado": round(_usd(t, pr), 4),
                "modelos": dict(t["models"]),
            }
        for srv, info in sorted(all_mcp.items(), key=lambda kv: -kv[1]["calls"]):
            out["por_mcp"][srv] = {"calls": info["calls"], "agents": dict(info["agents"])}
        for d in sorted(all_day):
            t = all_day[d]
            out["por_dia"][d] = {k: t[k] for k in ("input", "output", "cache_read", "cache_write")}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    alcance = "TODOS los proyectos" if scope_all else "BTP (claudecode)"
    print(f"\nDONDE SE VAN LOS TOKENS — {alcance}")
    print(f"Ventana: {cutoff} → hoy  ({days} dias)")
    print("(€ ESTIMADO; en suscripcion el € marginal ~0 — mira el VOLUMEN de tokens)\n")

    if fugas_only:
        if fugas:
            print("BANDERAS DE FUGA:")
            for f in fugas:
                print(" ", f)
        else:
            print("Sin banderas de fuga en la ventana.")
        return

    # --- Por agente ---
    print("Por agente (ordenado por tokens):")
    print(f"  {'Agente':25s} {'Total':>10s}    {'in':>8s}  {'out':>7s}  {'cache_r':>9s}  {'cache_w':>9s}  {'USD est.':>10s}")
    print("  " + "-" * 95)
    total_tok_all = 0
    total_usd_all = 0.0
    for ag, t in sorted(all_agent.items(), key=lambda kv: -_total_tok(kv[1])):
        tot = _total_tok(t)
        total_tok_all += tot
        dom = _dominant_model(t)
        pr = _price_for(dom, tabla)
        c = _usd(t, pr)
        total_usd_all += c
        ce = f"~${c:,.2f}" if pr else "€?"
        flags = []
        if tot // 1_000 > FUGA_AGENTE_TOK_K:
            flags.append("!")
        if tot > 500_000 and t["cache_write"] > tot * FUGA_CACHE_WRITE_RATIO:
            flags.append("C")
        flag_str = " [" + ",".join(flags) + "]" if flags else ""
        print(f"  {ag:25s} {tot/1_000:>8,.0f}k  "
              f"{t['input']/1_000:>8,.0f}k  {t['output']/1_000:>7,.0f}k  "
              f"{t['cache_read']/1_000:>9,.0f}k  {t['cache_write']/1_000:>9,.0f}k  "
              f"{ce:>10s}{flag_str}")
    print(f"\n  TOTAL  {total_tok_all/1_000:,.0f}k tok  ~${total_usd_all:,.2f}")

    # --- Por día (últimos 7 o los días de la ventana) ---
    dias_ord = sorted(d for d in all_day if d != "????-??-??")
    if dias_ord:
        print(f"\nPor dia ({len(dias_ord)} dias con datos):")
        for d in dias_ord[-10:]:  # últimos 10 días máx para no saturar
            t = all_day[d]
            tot = _total_tok(t)
            print(f"  {d}  {tot/1_000:,.0f}k  ({_fmt_tok(t)})")

    # --- Por MCP ---
    if by_mcp_flag and all_mcp:
        print("\nPor servidor MCP (impuesto de contexto):")
        for srv, info in sorted(all_mcp.items(), key=lambda kv: -kv[1]["calls"]):
            top = sorted(info["agents"].items(), key=lambda x: -x[1])[:3]
            top_str = ", ".join(f"{a}({c})" for a, c in top)
            flag = " [!]" if info["calls"] > FUGA_MCP_CALLS else ""
            print(f"  {srv:30s} {info['calls']:>4d} llamadas   agentes: {top_str}{flag}")
        print()
        print("  Nota: cada servidor MCP ACTIVO añade su bloque de instrucciones al contexto")
        print("  en CADA turno de CADA sesion. Con 10+ servidores activos el overhead puede")
        print("  representar 5-15k tokens extras por turno (el 'impuesto MCP').")

    # --- Banderas de fuga ---
    if fugas:
        print("\nBANDERAS DE FUGA [!]=agente_caro [C]=cache_write_alto:")
        for f in fugas:
            print(" ", f)

    # --- Cruce manual con /usage ---
    print("\n" + "=" * 70)
    print("CRUCE MANUAL CON /usage DE CLAUDE CODE")
    print("=" * 70)
    print("""
El comando /usage dentro de Claude Code muestra un resumen interactivo
de tokens de las últimas 24h y 7d, pero no es programático.

Para cruzarlo con este informe:
  1. Ejecuta /usage dentro de una sesión de Claude Code.
  2. Verás: total de tokens de entrada/salida y el desglose por modelo.
  3. Compara el 'total input+output (7d)' de /usage con el 'TOTAL' de
     este informe para la misma ventana. Si difieren mucho:
       a. /usage puede incluir otras sesiones (proyectos fuera de BTP)
          → ejecuta: python3 usage_coste.py --all  para ver todos.
       b. Si sigue sin cuadrar, puede haber sesiones del Claude.app
          de escritorio (en ~/Library/Application Support/Claude/
          claude-code-sessions/) que este script no lee aún.
  4. El desglose por AGENTE no está en /usage — es exclusivo de este
     informe (lee attributionAgent de los subagent JSONL).
  5. El desglose por MCP tampoco está en /usage — solo en --by-mcp.
""")


if __name__ == "__main__":
    main()
