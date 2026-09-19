#!/usr/bin/env python3
"""Pregunta a Perplexity (Sonar) con búsqueda web EN VIVO y CITAS. Sin dependencias (stdlib).
La clave la pones TÚ en tools/.perplexity_secrets.json — yo nunca la veo.

Setup (1 vez):
  1. console.perplexity.ai → API → Generate API key. Copia la clave (pplx-...). Añade crédito
     (la API se factura aparte de tu suscripción Pro/Max, por uso).
  2. Crea/edita tools/.perplexity_secrets.json con:  {"api_key": "pplx-...", "model": "sonar"}

Uso:
  python3 perplexity.py "tu pregunta"                  # respuesta sintetizada CON fuentes citadas
  python3 perplexity.py --academic "pregunta clínica"  # busca solo en literatura ingeniera
  python3 perplexity.py --clinico "pregunta del caso"  # PRESET caso {{TITULAR}}: academic + sonar-pro (mejor para evidencia)
  python3 perplexity.py --pro "pregunta difícil"        # modelo sonar-pro (mejor, más caro)
  python3 perplexity.py --deep "informe a fondo"        # sonar-deep-research (informe exhaustivo)
  python3 perplexity.py --search "consulta"             # resultados web crudos (como un buscador)
  python3 perplexity.py --recency week "qué ha pasado"  # solo resultados recientes (hour/day/week/month/year)
  python3 perplexity.py --domains nih.gov,who.int "x"   # limita a esos dominios
  python3 perplexity.py --json "x"                       # devuelve el JSON crudo de la API

Modelos (de barato/rápido a potente): sonar · sonar-pro · sonar-reasoning-pro · sonar-deep-research
Por defecto usa "sonar" (el más económico) salvo que pongas otro en el secrets o con --pro/--deep/--reasoning.
"""
import json, os, sys, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secrets import get as get_secret
from _net import with_retries

SECRETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".perplexity_secrets.json")
CHAT_URL = "https://api.perplexity.ai/v1/sonar"     # LEGADO: se retira el 27-sep-2026
AGENT_URL = "https://api.perplexity.ai/v1/agent"     # su relevo: respuesta + citas
MODELOS_URL = "https://api.perplexity.ai/v1/models"  # catálogo vivo (46 modelos el 19-sep-26)
SEARCH_URL = "https://api.perplexity.ai/search"      # resultados web crudos
DEFAULT_MODEL = "perplexity/sonar"
RECENCY = {"hour", "day", "week", "month", "year"}


def load_key():
    """Devuelve (api_key, model) o imprime ayuda y devuelve (None, None).
    Clave: Llavero (btp-perplexity-api) → fallback a tools/.perplexity_secrets.json."""
    key = (get_secret("btp-perplexity-api", SECRETS, "api_key") or "").strip()
    if not key or not key.startswith("pplx-"):
        print("Falta la clave de Perplexity (debe empezar por 'pplx-'). "
              "Guárdala en el Llavero (btp-perplexity-api) o en tools/.perplexity_secrets.json.")
        return None, None
    model = DEFAULT_MODEL
    if os.path.exists(SECRETS):
        try:
            with open(SECRETS) as f:
                model = (json.load(f) or {}).get("model", DEFAULT_MODEL)
        except Exception:
            pass
    return key, model


def post(url, key, body, metodo="POST"):
    req = urllib.request.Request(
        url, method=metodo,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        return json.load(with_retries(lambda: urllib.request.urlopen(req, timeout=180))), None
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:600]
        hint = ""
        if e.code == 401:
            hint = "  → clave inválida o sin crédito. Revisa tools/.perplexity_secrets.json y el saldo en console.perplexity.ai."
        if e.code == 400 and "model" in detail.lower():
            hint = ("  → modelo no válido para el agente: va con proveedor, p.ej. "
                    "perplexity/sonar, anthropic/claude-opus-5, openai/gpt-5.3-codex. "
                    "El catálogo vivo: python3 tools/perplexity.py --modelos")
        return None, f"Perplexity API error {e.code}: {detail}{hint}"
    except Exception as e:
        return None, f"Error de red: {e}"


def fmt_sources(items):
    """items = lista de dicts {title,url,date} o lista de URLs (str)."""
    out = []
    for i, it in enumerate(items, 1):
        if isinstance(it, str):
            out.append(f"  [{i}] {it}")
        else:
            t = it.get("title") or it.get("url") or ""
            u = it.get("url") or ""
            d = it.get("date") or it.get("last_updated") or ""
            out.append(f"  [{i}] {t} — {u}" + (f"  ({d})" if d else ""))
    return "\n".join(out)


def modelos(key):
    """El catálogo vivo del Agent API. 19-sep-2026: 46 modelos, y no solo los propios —
    `anthropic/claude-opus-5`, `openai/gpt-5.3-codex`, `google/gemini-3.x`, `xai/grok-4.6`…
    Comprobado con una llamada real, no leído en una web."""
    resp, err = post(MODELOS_URL, key, None, metodo="GET")
    if err:
        return [], err
    return [m.get("id") for m in (resp or {}).get("data", []) if m.get("id")], None


def run_chat(key, model, q, opts):
    """Agent API (`/v1/agent`). Sustituye al `/v1/sonar` que Perplexity retira el 27-sep-2026.

    Dos diferencias que importan y por las que esto no es un cambio de URL: el cuerpo lleva
    `input` en vez de `messages`, y la respuesta viene en `output[]` —el texto en un item
    `message` y las fuentes en otro `search_results`— en vez de en `choices[0]`."""
    import borde  # 🔴 BORDE no-bypassable: Perplexity = tercero NO confiable
    if not borde.guard_cli(q, "perplexity"):
        return
    if "/" not in model:          # `sonar` a secas ya no vale: el agente exige proveedor/modelo
        model = "perplexity/" + model
    # `max_steps` > 1 es lo que hace que el agente BUSQUE. Con el default (1) contesta de
    # memoria y devuelve CERO fuentes: cambiar el carril de citas por uno sin citas sería una
    # degradación silenciosa, justo lo que el muro no permite.
    # `tools: [{"type": "web_search"}]` NO es opcional aquí: sin él el agente NO busca y
    # contesta de memoria. Probado el 19-sep-26 — sin la herramienta devolvió una descripción
    # INVENTADA de un ensayo y cero fuentes. Este carril existe para traer CITAS; uno sin citas
    # no es el mismo carril más barato, es otro peor disfrazado.
    body = {"input": q, "model": model, "max_output_tokens": opts.get("max_tokens", 2000),
            "max_steps": opts.get("pasos", 4), "tools": [{"type": "web_search"}]}
    # El agente NO acepta los filtros del endpoint viejo (`search_recency_filter` y compañía
    # dan 400 «unknown field»). Lo que sí entiende es la INSTRUCCIÓN: se traducen a texto, que
    # además queda visible en la petición en vez de escondido en un parámetro.
    guias = []
    if opts.get("academic"):
        guias.append("Prioriza fuentes académicas y revisadas por pares; cita DOI o PMID.")
    if opts.get("recency"):
        guias.append("Usa solo fuentes del último periodo: %s." % opts["recency"])
    if opts.get("domains"):
        guias.append("Limítate a estos dominios: %s." % ", ".join(opts["domains"]))
    if guias:
        body["instructions"] = " ".join(guias)
    resp, err = post(AGENT_URL, key, body)
    if err:
        print(err); return
    try:                                   # ledger de gasto (best-effort, no rompe la respuesta)
        import gasto
        _u = (resp or {}).get("usage") or {}
        gasto.registrar("perplexity", model,
                        _u.get("prompt_tokens") or _u.get("input_tokens") or 0,
                        _u.get("completion_tokens") or _u.get("output_tokens") or 0)
    except Exception:
        pass
    if opts.get("json"):
        print(json.dumps(resp, ensure_ascii=False, indent=2)); return
    text, sources = _leer_salida(resp)
    print(text)
    if sources:
        print("\nFuentes:")
        print(fmt_sources(sources))


def _leer_salida(resp):
    """(texto, fuentes) de una respuesta del agente. Tolera el formato viejo mientras viva."""
    texto, fuentes = "", []
    for item in (resp or {}).get("output", []) or []:
        if item.get("type") == "message":
            for bloque in item.get("content", []) or []:
                if bloque.get("type") == "output_text":
                    texto += bloque.get("text", "")
        elif item.get("type") == "search_results":
            fuentes.extend(item.get("results") or [])
    if not texto:                       # formato legado (`/v1/sonar`), por si aún responde
        try:
            texto = resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            texto = json.dumps(resp, ensure_ascii=False)[:1500]
        fuentes = fuentes or resp.get("search_results") or resp.get("citations") or []
    return texto, fuentes


def run_search(key, q, opts):
    import borde  # 🔴 BORDE no-bypassable: Perplexity = tercero NO confiable
    if not borde.guard_cli(q, "perplexity"):
        return
    body = {"query": q, "max_results": opts.get("max", 10)}
    if opts.get("recency"):
        body["search_recency_filter"] = opts["recency"]
    if opts.get("domains"):
        body["search_domain_filter"] = opts["domains"]
    resp, err = post(SEARCH_URL, key, body)
    if err:
        print(err); return
    if opts.get("json"):
        print(json.dumps(resp, ensure_ascii=False, indent=2)); return
    results = resp.get("results", [])
    if not results:
        print("(sin resultados)"); return
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        url = r.get("url", "")
        snip = (r.get("snippet") or "").strip().replace("\n", " ")
        date = r.get("date") or ""
        print(f"[{i}] {title}\n    {url}" + (f"  ({date})" if date else "") + (f"\n    {snip}" if snip else "") + "\n")


def main():
    args = sys.argv[1:]
    opts = {}
    mode = "chat"
    model_override = None

    # flags booleanos
    for flag, key in (("--academic", "academic"), ("--json", "json")):
        if flag in args:
            opts[key] = True; args.remove(flag)
    if "--modelos" in args:
        mode = "modelos"; args.remove("--modelos")
    if "--search" in args:
        mode = "search"; args.remove("--search")
    if "--pro" in args:
        model_override = "perplexity/sonar-pro"; args.remove("--pro")
    if "--reasoning" in args:
        model_override = "perplexity/sonar-reasoning-pro"; args.remove("--reasoning")
    if "--deep" in args:
        model_override = "perplexity/sonar-deep-research"; args.remove("--deep")
    if "--clinico" in args:  # preset caso {{TITULAR}}: literatura ingeniera + mejor modelo
        opts["academic"] = True
        if not model_override:
            model_override = "perplexity/sonar-pro"
        args.remove("--clinico")

    # Cualquiera de los 46 del catálogo, por su id (`--modelo anthropic/claude-opus-5`).
    # Es lo que convierte esta tool en un carril multi-casa con UNA sola clave.
    if "--modelo" in args:
        i = args.index("--modelo")
        if i + 1 < len(args):
            model_override = args[i + 1]; del args[i:i + 2]

    # flags con valor
    def take(flag):
        if flag in args:
            i = args.index(flag)
            if i + 1 >= len(args):
                print(f"{flag} necesita un valor."); sys.exit(2)
            val = args[i + 1]; del args[i:i + 2]; return val
        return None
    model_take = take("--model")
    if model_take:
        model_override = model_take
    rec = take("--recency")
    if rec:
        if rec not in RECENCY:
            print(f"--recency debe ser uno de: {', '.join(sorted(RECENCY))}"); return
        opts["recency"] = rec
    dom = take("--domains")
    if dom:
        opts["domains"] = [d.strip() for d in dom.split(",") if d.strip()]
    mx = take("--max")
    if mx:
        try:
            opts["max"] = max(1, min(20, int(mx)))
        except ValueError:
            print("--max debe ser un número (1-20)"); return

    q = " ".join(args).strip()
    if mode == "modelos":
        key, _ = load_key()
        if not key:
            return 1
        ids, err = modelos(key)
        print(err or "\n".join(ids))
        return 1 if err else 0

    if not q:
        print('Uso: python3 perplexity.py "tu pregunta"   (--academic / --pro / --deep / '
              '--search / --recency week / --modelos)')
        return

    key, model = load_key()
    if not key:
        return
    if model_override:
        model = model_override


    if mode == "search":
        run_search(key, q, opts)
    else:
        run_chat(key, model, q, opts)


if __name__ == "__main__":
    main()
