#!/usr/bin/env python3
"""scite_mcp.py — cliente PROPIO del MCP de scite, DESACOPLADO de Claude Code.

Gemelo de `consensus_mcp.py`: habla directo con `https://api.scite.ai/mcp` por el SDK
oficial de MCP, hace su propio OAuth (la primera vez abre el navegador para que {{TITULAR}}
entre con su cuenta de scite — que YA paga) y CACHEA el token en local, así las siguientes
veces NO pide nada. NO depende del binario `claude` ni de su config — cumple "desacoplarse
siempre" ([[feedback-desacoplar-siempre-de-claude-code]]).

scite añade lo único que las demás no dan: **Smart Citations** (¿el paper fue apoyado /
mencionado / CONTRADICHO después?) — el anillo anti-refutación del carril de evidencia.

Reusa el venv de Consensus (mismo SDK `mcp`):
  .venv-consensus/bin/python tools/scite_mcp.py --login           # OAuth 1ª vez (acto de {{TITULAR}})
  .venv-consensus/bin/python tools/scite_mcp.py --tools           # lista las tools reales del MCP
  .venv-consensus/bin/python tools/scite_mcp.py "pregunta"        # llama la tool por defecto
  .venv-consensus/bin/python tools/scite_mcp.py --tool search "…" # elige la tool

MURO: consultas a NIVEL DE TEMA (gen/variante/paper/dianas PÚBLICAS) — NUNCA PII/HLA/VCF
crudo en el buscador (criterio #1 verificado 28/6: scite NO entrena con tus datos). El
resultado es DATO a cotejar contra fuente primaria (PMID/DOI), no verdad. Token en
`tools/state/scite/` (gitignored, 0600), nunca en el repo.
"""
import os
import sys, os, json, asyncio, webbrowser, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

# tools/ tiene un queue.py propio que ensombrece la stdlib `queue` que usan anyio/httpx.
# Sacamos este dir del sys.path ANTES de importar `mcp`, o los deps fallan (igual que consensus_mcp).
_here = os.path.dirname(os.path.abspath(__file__))
import _oauth_refresh   # stdlib pura, y se importa AQUÍ: la línea de abajo saca
                       # tools/ del sys.path y ya no se podría importar.
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _here]

from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientMetadata, OAuthToken, OAuthClientInformationFull

SERVER_URL = "https://api.scite.ai/mcp"
ROOT = os.environ.get("BTP_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ROOT, "tools", "state", "scite")
TOK_FILE = os.path.join(STATE_DIR, "tokens.json")
CLI_FILE = os.path.join(STATE_DIR, "client.json")
CALLBACK_PORT = 8766   # distinto del de consensus (8765) para no chocar si corren a la vez
REDIRECT_URI = "http://localhost:%d/callback" % CALLBACK_PORT
DEFAULT_TOOL = os.environ.get("BTP_SCITE_TOOL", "search_literature")   # tool real del MCP de scite (descubierto con --tools)


def _save(path, data):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)
    os.chmod(path, 0o600)


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


class FileTokenStorage(TokenStorage):
    """Cachea el token y el registro del cliente en local (0600)."""
    async def get_tokens(self):
        d = _load(TOK_FILE)
        return OAuthToken.model_validate(d) if d else None

    async def set_tokens(self, tokens: OAuthToken):
        _save(TOK_FILE, tokens.model_dump(mode="json", exclude_none=True))

    async def get_client_info(self):
        d = _load(CLI_FILE)
        return OAuthClientInformationFull.model_validate(d) if d else None

    async def set_client_info(self, info: OAuthClientInformationFull):
        _save(CLI_FILE, info.model_dump(mode="json", exclude_none=True))


# 🔐 El OAuth interactivo SOLO con --login (27/7/26). Antes, cualquier consulta cuyo token hubiera
# caducado abría el flujo del navegador y se quedaba BLOQUEADA para siempre esperando el callback:
# en el lazo, en un daemon o en una rutina no hay nadie para pulsar nada. Le pasó a Consensus (token
# caducado el 29-jun, 120 s de cuelgue por intento durante un mes) y scite tenía el MISMO patrón.
# Ahora: sin --login, si hace falta autorizar de nuevo se FALLA RÁPIDO y se dice qué hacer.
_PERMITE_LOGIN = False


def _sin_login(nombre, cli):
    return RuntimeError(
        "el OAuth de %s caducó y este proceso NO es interactivo: hace falta un login humano "
        "(una vez) → python3 tools/evidencia.py %s --login" % (nombre, cli))


def _wait_for_callback():
    holder = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.urlparse(self.path).query
            p = urllib.parse.parse_qs(q)
            holder["code"] = (p.get("code") or [None])[0]
            holder["state"] = (p.get("state") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>scite conectado ✅ — ya puedes cerrar esta pestaña.</h2>".encode())

        def log_message(self, *a):
            pass

    srv = HTTPServer(("localhost", CALLBACK_PORT), H)
    srv.timeout = 180             # ni con --login se cuelga para siempre
    srv.handle_request()          # bloquea hasta UNA petición (o hasta el timeout)
    srv.server_close()
    return holder.get("code"), holder.get("state")


async def _redirect_handler(url: str):
    if not _PERMITE_LOGIN:
        raise _sin_login("scite", "scite")
    print("\n🔐 Abre esta URL en tu navegador y entra con tu cuenta de scite:\n   %s\n" % url, file=sys.stderr)
    try:
        webbrowser.open(url)
    except Exception:
        pass


async def _callback_handler():
    code, state = await asyncio.to_thread(_wait_for_callback)
    if not code:
        raise RuntimeError("no llegó el code del OAuth (callback vacío)")
    return code, state


def _make_oauth():
    meta = OAuthClientMetadata(
        client_name="Polaris (Beyond the Protocol)",
        redirect_uris=[REDIRECT_URI],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )
    return OAuthClientProvider(
        server_url=SERVER_URL,
        client_metadata=meta,
        storage=FileTokenStorage(),
        redirect_handler=_redirect_handler,
        callback_handler=_callback_handler,
    )


async def _run(query, login_only=False, list_tools=False, tool=DEFAULT_TOOL):
    _key = _clave_estatica("btp-scite-api")
    if _key:                       # clave estática: ni OAuth ni navegador ni caducidad
        _ctx = streamablehttp_client(SERVER_URL, headers={"Authorization": "Bearer " + _key})
    else:
        _ctx = streamablehttp_client(SERVER_URL, auth=_make_oauth())
    async with _ctx as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if login_only:
                return {"_login": "ok"}
            if list_tools:
                tl = await session.list_tools()
                return {"tools": [{"name": t.name, "description": (t.description or "")[:200]} for t in tl.tools]}
            res = await session.call_tool(tool, {"term": query})   # search_literature usa `term`
            parts = []
            for c in (res.content or []):
                t = getattr(c, "text", None)
                if t:
                    parts.append(t)
            return {"text": "\n".join(parts)}


def _clave_estatica(servicio):
    """Clave ESTÁTICA del Llavero para `Authorization: Bearer` (27/7/26, idea de {{TITULAR}}: «lo
    apañamos para acceder sin login con su apikey»). La doc del MCP lo contempla —
    «Authorization: Bearer YOUR_API_KEY»— pero hoy esa vía es de acceso *enterprise* (por
    solicitud, con cuota a medida), así que no hay clave que poner todavía. El camino queda
    ENCHUFADO y en cuanto exista la clave se usa sola: cero OAuth, cero navegador, cero caducidad.
    Sin clave, se sigue por OAuth con renovación automática (que ya no necesita humano)."""
    try:
        _d = os.path.dirname(os.path.abspath(__file__))
        if _d not in sys.path:
            sys.path.insert(0, _d)
        from _secrets import get as _get
        return (_get(servicio) or "").strip() or None
    except Exception:
        return None


def _borde_check(query, destino):
    """MURO: ninguna consulta sale a un buscador externo sin pasar el borde.
    FAIL-CLOSED: si borde no carga, se bloquea (no se sale a ciegas)."""
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        import borde
        # Puerta ingeniera (no la genérica): una búsqueda de literatura puede llevar
        # nombre de gen / tipo de tumor (no identifica a {{TITULAR}}), pero NUNCA una huella
        # genómica del paciente (variante/coordenada/HLA/rsID/genotipo) ni PII.
        ok, motivo = borde.egress_cientifico(query, destino=destino)
        if not ok:
            sys.stderr.write("BORDE: no envio a %s - %s\n" % (destino, motivo))
        return ok
    except Exception:
        sys.stderr.write("[%s] ERROR: borde.py no disponible - llamada BLOQUEADA\n" % destino)
        return False


def main():
    args = sys.argv[1:]
    as_json = "--json" in args
    login = "--login" in args
    global _PERMITE_LOGIN
    _PERMITE_LOGIN = login        # solo el acto humano puede abrir el navegador
    list_tools = "--tools" in args
    tool = DEFAULT_TOOL
    if "--tool" in args:
        i = args.index("--tool")
        if i + 1 < len(args):
            tool = args[i + 1]
            args = args[:i] + args[i + 2:]
    args = [a for a in args if not a.startswith("--")]
    query = " ".join(args).strip()
    if not (login or list_tools) and not query:
        print("uso: scite_mcp.py [--json] [--tool NAME] \"pregunta\"  |  --login  |  --tools", file=sys.stderr)
        sys.exit(2)
    # MURO: fail-closed antes de salir a scite.
    if query and not _borde_check(query, "scite"):
        sys.exit(3)
    # 🔄 Renovar el access token ANTES de conectar (27/7/26). Aquí importa el doble que en Consensus:
    # el token de scite dura **15 minutos**, así que sin renovación automática el carril solo
    # funcionaba dentro de la misma sesión en la que {{TITULAR}} hacía el login. El SDK, al verlo caducado,
    # arrancaba un OAuth nuevo por navegador — un cuelgue en una máquina sin nadie delante.
    # Fail-soft: si no se puede renovar, se intenta la conexión igual y el camino de siempre decide.
    if not login and not _clave_estatica("btp-scite-api"):
        _est, _det = _oauth_refresh.renovar(STATE_DIR, SERVER_URL)
        if _est in ("renovado", "sin_refresh", "fallo"):
            print("[oauth] %s: %s" % (_est, _det), file=sys.stderr)
    try:
        out = asyncio.run(_run(query, login_only=login, list_tools=list_tools, tool=tool))
    except Exception as e:
        # El SDK de MCP envuelve el fallo en un ExceptionGroup: sin desenvolverlo, el motivo REAL
        # («el OAuth caducó, haz --login») queda sepultado bajo "unhandled errors in a TaskGroup".
        _causa = e
        while getattr(_causa, "exceptions", None):
            _causa = _causa.exceptions[0]
        msg = "scite_mcp ERROR: %s" % (str(_causa) or repr(_causa)[:300],)
        print(json.dumps({"error": msg}, ensure_ascii=False) if as_json else msg, file=sys.stderr)
        sys.exit(1)
    if login:
        print("✅ OAuth de scite completado y token cacheado.")
        return
    if list_tools:
        print(json.dumps(out, ensure_ascii=False, indent=2) if as_json
              else "\n".join("· %s — %s" % (t["name"], t["description"]) for t in out.get("tools", [])))
        return
    print(json.dumps(out, ensure_ascii=False) if as_json else out.get("text", "(sin resultados)"))


if __name__ == "__main__":
    main()
