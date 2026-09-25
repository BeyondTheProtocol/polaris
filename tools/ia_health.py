#!/usr/bin/env python3
"""ia_health.py — sonda de SALUD de las IAs del gabinete (¿responde / caída?).

Para cada IA hace la comprobación MÁS BARATA posible (endpoint de auth/`models` por GET, o
alcanzabilidad), SIN gastar tokens de generación. Da un código por IA:
  OK         responde y autentica
  DEGRADED   responde pero hay un pero (auth/permiso/límite, o bot-wall en las de navegador)
  DOWN       no responde (timeout / conexión / 5xx)
  NA         no se sondea (sin clave, aislada por diseño, o vive en la sesión = MCP)

NO es consejo ni acción: solo diagnóstico. El muro manda: aquí no se manda PII a ningún
sitio, solo se toca un endpoint de estado. `probe()` la usa `healthcheck.py` para avisar a
{{TITULAR}} si una IA CRÍTICA cae (hermano operativo del código rojo).

Uso:
  python3 tools/ia_health.py            # tabla legible
  python3 tools/ia_health.py --json     # JSON (para healthcheck / El Observatorio)
"""
import sys, os, ssl, json, datetime, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _secrets import get as get_secret
except Exception:
    def get_secret(*a, **k):
        return None

TIMEOUT = 8
_CTX = ssl.create_default_context()

# código → (emoji+label legible)
SEMA = {"OK": "🟢 OK", "DEGRADED": "🟡 DEGRADADA", "DOWN": "🔴 CAÍDA", "NA": "⚪ no_sondeada"}


def _probe(url, headers=None, esperar_200=True):
    """GET ligero. Devuelve (code, detalle) interpretando el HTTP."""
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_CTX) as r:
            code = r.status
            cuerpo = r.read(2000).decode("utf-8", "replace").lower()
            if "cloudflare" in cuerpo or "checking your browser" in cuerpo or "are you human" in cuerpo:
                return "DEGRADED", "bot-wall (Cloudflare) — necesita Chrome real / MCP"
            return ("OK" if code == 200 else "DEGRADED"), "HTTP %d" % code
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return "DEGRADED", "HTTP %d (auth/permiso o bot-wall)" % e.code
        if e.code == 429:
            return "DEGRADED", "HTTP 429 (límite de tasa)"
        if e.code in (404, 405) and not esperar_200:
            return "OK", "HTTP %d (alcanzable)" % e.code
        if 500 <= e.code < 600:
            return "DOWN", "HTTP %d (servidor)" % e.code
        return "DEGRADED", "HTTP %d" % e.code
    except urllib.error.URLError as e:
        return "DOWN", "sin conexión (%s)" % (getattr(e, "reason", e),)
    except Exception as e:
        return "DOWN", "error (%s)" % (repr(e)[:60],)


def refrescar_credito_claude():
    """Refresca la SEÑAL DE CRÉDITO del prepago de Anthropic. Devuelve True/False/None.

    POR QUÉ EXISTE (25-jul-26). `cost_guard.credito_ok()` es la única fuente FIRME de si queda
    prepago, y la escribía solo `ia.ask`. Pero el lazo ejecuta por `run_agent.sh` (Claude Code
    CLI, que va por la cuota del plan y NO toca el prepago), así que nadie la refrescaba: el
    fichero llevaba 50,9 h parado con un TTL de 6 h, `credito_ok()` devolvía None en cada pasada
    y el sistema se quedó sin saber su propio saldo. Se arregló que no MINTIERA sobre ello; esto
    es lo que le devuelve la capacidad de saberlo.

    Por qué no basta el GET de `/v1/models` que ya hace la sonda de salud: ese GET responde 200
    con el prepago a CERO — prueba que la clave vale, no que quede saldo. Lo único que distingue
    «sin crédito» es el 400 `credit balance is too low`, y ese solo aparece pidiendo de verdad
    una respuesta a un modelo.

    Coste: `max_tokens: 1` sobre el modelo más barato, a la cadencia de la sonda de salud (~2 h).
    Fracciones de céntimo al día; y si NO hay crédito no cuesta nada, porque falla antes de
    generar. Contenido enviado: la palabra «hi». Nada de PII — es N0, y por eso cabe en esta
    sonda, que es la pieza que ya habla con los proveedores para diagnosticar.

    None = no se pudo determinar, y entonces NO se escribe nada: una señal fresca que dijera
    «no sé» es peor que una vieja, porque el TTL la daría por buena.

    La sonda vive en `ia.py`, no aquí: es quien sabe hablar con Claude (por el binario, con la
    clave del Llavero) y quien ya interpreta ese 400. Poner un cliente HTTP propio en esta sonda
    habría sido una segunda boca hacia Anthropic — y el muro tiene razón en que solo haya una.
    Aquí solo se dispara, porque esta es la pieza que corre sola cada ~2 h."""
    try:
        import ia
        return ia.sonda_credito()
    except Exception:
        return None


def _llm(nombre, key_name, url, auth, extra=None, esperar_200=True, gated=False, carril=""):
    key = (get_secret(key_name) or "").strip()
    if gated:
        return {"ia": nombre, "code": "NA", "detalle": "aislada por diseño (gate key+VPN)", "carril": "aislado"}
    if not key:
        return {"ia": nombre, "code": "NA", "detalle": "sin clave en Llavero (%s)" % key_name, "carril": carril}
    headers = dict(extra or {})
    u = url
    if auth == "bearer":
        headers["Authorization"] = "Bearer " + key
    elif auth == "x-api-key":
        headers["x-api-key"] = key
    elif auth == "querykey":
        u = url + key
    code, det = _probe(u, headers, esperar_200=esperar_200)
    return {"ia": nombre, "code": code, "detalle": det, "carril": carril}


def probe():
    """Sondea todas las IAs y devuelve el resultado estructurado (lo usa healthcheck)."""
    out = []
    # A · Cerebros LLM (endpoint de auth/models, $0)
    out.append(_llm("Claude", "btp-anthropic-api", "https://api.anthropic.com/v1/models",
                    "x-api-key", extra={"anthropic-version": "2023-06-01"}, carril="🔴 clínico"))
    # …y de paso se refresca la señal de crédito, que es la ÚNICA fuente firme del prepago y
    # llevaba dos días sin escribirse. Aquí porque es la pieza que ya habla con el proveedor y
    # corre sola cada ~2 h. Fail-soft y opt-out con BTP_SIN_SONDA_CREDITO=1 (tests).
    if os.environ.get("BTP_SIN_SONDA_CREDITO") != "1":
        try:
            refrescar_credito_claude()
        except Exception:
            pass
    out.append(_llm("Grok", "btp-grok-api", "https://api.x.ai/v1/models", "bearer", carril="no-clínico"))
    # OpenAI y GLM llevaban meses SIN vigilar (27/7/26): claves pagadas, cero señal. Las dos hablan
    # formato OpenAI, así que el /models de siempre vale.
    out.append(_llm("OpenAI", "btp-openai-api", "https://api.openai.com/v1/models", "bearer",
                    carril="no-clínico"))
    out.append(_llm("GLM (z.ai)", "btp-glm-api", "https://api.z.ai/api/paas/v4/models", "bearer",
                    carril="no-clínico"))
    out.append(_llm("Perplexity", "btp-perplexity-api", "https://api.perplexity.ai/", "bearer",
                    esperar_200=False, carril="no-clínico"))
    out.append(_llm("NVIDIA NIM", "btp-nvidia-api", "https://integrate.api.nvidia.com/v1/models",
                    "bearer", carril="no-clínico"))
    out.append(_llm("Gemini", "btp-gemini-api",
                    "https://generativelanguage.googleapis.com/v1beta/models?key=", "querykey", carril="no-clínico"))
    out.append(_llm("Fugu", "btp-fugu-api", "", "bearer", gated=True))
    # B · Evidencia médica DESACOPLADA: el estado REAL no es la web cruda (da 403 por auth/bot-wall
    #     aunque el cliente funcione — falso DEGRADADA, verificado en vivo el 29/6). Señal correcta =
    #     credencial presente: token OAuth cacheado para los MCP propios (Consensus/scite), clave en
    #     Llavero para Elicit. (Presencia ≠ token no-caducado, pero el cliente auto-refresca; mucho
    #     mejor que el 403 falso.) Undermind sigue por navegador → ahí sí vale la sonda web.
    _repo = os.environ.get("BTP_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _tok_estado(carpeta):
        """(code, detalle). PRESENCIA ≠ VALIDEZ (27/7/26): el fichero de token estaba ahí desde el
        29-jun y el panel decía 🟢 OK, mientras cada consulta real se colgaba 120 s pidiendo un
        OAuth nuevo. Un MUST del carril de evidencia llevaba un mes muerto y nadie se enteró.
        Ahora se mira la CADUCIDAD (mtime del fichero + expires_in), no si el fichero existe."""
        ruta = os.path.join(_repo, "tools", "state", carpeta, "tokens.json")
        if not os.path.exists(ruta):
            return "DEGRADED", "sin token — falta OAuth (evidencia.py %s --login)" % carpeta
        try:
            with open(ruta) as f:
                tok = json.load(f) or {}
            caduca = os.path.getmtime(ruta) + float(tok.get("expires_in") or 0)
        except Exception as e:
            return "DEGRADED", "token ilegible (%s)" % repr(e)[:60]
        restante = caduca - datetime.datetime.now().timestamp()
        if restante > 0:
            return "OK", "MCP propio · token vigente (%d min)" % int(restante // 60)
        # Caducado NO es lo mismo que muerto: con refresh token el cliente lo renueva SOLO antes de
        # conectar (tools/_oauth_refresh.py). Importa de verdad en scite, cuyo access token dura 15
        # min: sin este matiz el panel estaría en ámbar permanente por un carril que funciona.
        if tok.get("refresh_token"):
            return "OK", "MCP propio · caducado pero se renueva solo (refresh token)"
        return "DEGRADED", ("token caducado y SIN refresh — login humano (1 vez): "
                            "evidencia.py %s --login" % carpeta)

    for nombre, carpeta in (("Consensus", "consensus"), ("scite.ai", "scite")):
        code, detalle = _tok_estado(carpeta)
        out.append({"ia": nombre, "code": code, "detalle": detalle,
                    "carril": "evidencia (MCP propio)"})
    _elk = (get_secret("btp-elicit-api") or "").strip()
    out.append({"ia": "Elicit", "code": "OK" if _elk else "DEGRADED",
                "detalle": "API REST · clave en Llavero" if _elk else "sin clave btp-elicit-api",
                "carril": "evidencia (API REST)"})
    code, det = _probe("https://undermind.ai/", esperar_200=False)
    out.append({"ia": "Undermind", "code": code, "detalle": det, "carril": "evidencia (navegador)"})
    return {
        "ts": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "ias": out,
        "caidas": [x["ia"] for x in out if x["code"] == "DOWN"],
        "degradadas": [x["ia"] for x in out if x["code"] == "DEGRADED"],
    }


def main():
    salida = probe()
    if "--json" in sys.argv:
        import json
        print(json.dumps(salida, ensure_ascii=False, indent=2))
        return
    print("Salud de las IAs · %s" % salida["ts"])
    for x in salida["ias"]:
        print("  %-12s %-14s %s" % (x["ia"], SEMA.get(x["code"], x["code"]), x["detalle"]))
    print("\n🔴 CAÍDAS: " + ", ".join(salida["caidas"]) if salida["caidas"] else "\n✅ Ninguna IA caída.")


if __name__ == "__main__":
    main()
