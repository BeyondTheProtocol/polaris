#!/usr/bin/env python3
"""tools/_oauth_refresh.py — renovar el token OAuth SIN humano (27/7/26).

POR QUÉ EXISTE. Los dos clientes MCP propios del carril de evidencia (Consensus y scite) guardan
`access_token` + `refresh_token`. El access token dura poco — Consensus 4 h, **scite 15 min** — y el
SDK de MCP, al encontrarlo caducado, arrancaba un OAuth NUEVO por navegador en vez de usar el
refresh token. En una máquina sin nadie delante eso no es "pedir login": es un cuelgue.

Resultado real: Consensus llevaba **28 días** sin responder y el panel decía 🟢 OK. Y scite, con 15
minutos de vida por token, no podía funcionar nunca fuera de la sesión en que {{TITULAR}} hacía el login.

Lo que faltaba era una línea de protocolo que el SDK no estaba dando: `grant_type=refresh_token`
contra el token endpoint. Verificado en vivo el 27/7/26 — el refresh token de Consensus, emitido el
29-jun, seguía siendo válido 28 días después. O sea: **nunca hizo falta un login nuevo.**

Con esto, el login humano es UNA VEZ y el carril se mantiene solo: cada renovación devuelve además
un refresh token nuevo (rotación), que se guarda.

Determinista, stdlib pura, sin dependencias (a propósito: lo importan scripts que sacan `tools/`
del sys.path antes de cargar `mcp`, así que no puede depender de nada del repo).

Uso:
    import _oauth_refresh
    estado, detalle = _oauth_refresh.renovar(state_dir, "https://mcp.consensus.app/mcp")
    # estado: "vigente" | "renovado" | "sin_refresh" | "sin_token" | "fallo"
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

MARGEN_SEG = 300          # se renueva 5 min ANTES de caducar (no se espera al 401)
TIMEOUT = 20


def _leer(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _guardar_0600(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def caduca_en(state_dir):
    """Segundos que le quedan al access token (negativo = caducado). None si no se puede saber.
    La marca de tiempo es el mtime del fichero: es cuando lo escribimos, que es cuando se emitió."""
    ruta = os.path.join(state_dir, "tokens.json")
    tok = _leer(ruta)
    if not tok or not tok.get("expires_in"):
        return None
    try:
        return (os.path.getmtime(ruta) + float(tok["expires_in"])) - time.time()
    except Exception:
        return None


def renovable(state_dir):
    """¿Hay refresh token guardado? (No garantiza que el servidor lo acepte, pero sin él la
    única salida es un login humano.)"""
    return bool(_leer(os.path.join(state_dir, "tokens.json")).get("refresh_token"))


def token_endpoint(server_url, state_dir):
    """El token endpoint del servidor. Se descubre por `.well-known` y se CACHEA en client.json:
    así la renovación no depende de un round-trip extra cada vez, ni de una URL a fuego aquí."""
    cli_path = os.path.join(state_dir, "client.json")
    cli = _leer(cli_path)
    if cli.get("_token_endpoint"):
        return cli["_token_endpoint"]
    p = urllib.parse.urlparse(server_url)
    well = "%s://%s/.well-known/oauth-authorization-server" % (p.scheme, p.netloc)
    try:
        with urllib.request.urlopen(well, timeout=TIMEOUT) as r:
            ep = (json.load(r) or {}).get("token_endpoint")
    except Exception:
        return None
    if ep and cli:
        cli["_token_endpoint"] = ep
        try:
            _guardar_0600(cli_path, cli)
        except Exception:
            pass
    return ep


def renovar(state_dir, server_url, *, forzar=False):
    """Renueva el access token si le queda poco. Devuelve (estado, detalle). NUNCA lanza:
    el llamante debe poder seguir e intentar la conexión igual (fail-soft)."""
    ruta = os.path.join(state_dir, "tokens.json")
    tok = _leer(ruta)
    if not tok:
        return "sin_token", "no hay token guardado: hace falta el login humano una vez"
    queda = caduca_en(state_dir)
    if not forzar and queda is not None and queda > MARGEN_SEG:
        return "vigente", "quedan %d min" % int(queda // 60)
    if not tok.get("refresh_token"):
        return "sin_refresh", "token caducado y sin refresh token: hace falta login humano"
    ep = token_endpoint(server_url, state_dir)
    if not ep:
        return "fallo", "no pude descubrir el token endpoint del servidor"
    datos = {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]}
    cid = _leer(os.path.join(state_dir, "client.json")).get("client_id")
    if cid:
        datos["client_id"] = cid
    req = urllib.request.Request(ep, data=urllib.parse.urlencode(datos).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            nuevo = json.load(r) or {}
    except urllib.error.HTTPError as e:
        cuerpo = ""
        try:
            cuerpo = e.read().decode()[:200]
        except Exception:
            pass
        # invalid_grant = el refresh token murió de verdad → ahí sí hace falta el humano.
        if e.code in (400, 401) and "invalid_grant" in cuerpo:
            return "sin_refresh", "el refresh token ya no vale: hace falta login humano una vez"
        return "fallo", "HTTP %s al renovar: %s" % (e.code, cuerpo)
    except Exception as e:
        return "fallo", "red: %s" % repr(e)[:120]
    if not nuevo.get("access_token"):
        return "fallo", "el servidor no devolvió access_token"
    # Rotación: si viene refresh nuevo se guarda; si no, se conserva el que teníamos.
    guardado = dict(tok)
    guardado.update({k: v for k, v in nuevo.items() if v is not None})
    if not nuevo.get("refresh_token"):
        guardado["refresh_token"] = tok["refresh_token"]
    _guardar_0600(ruta, guardado)
    return "renovado", "vale otras %d min" % int(float(guardado.get("expires_in") or 0) // 60)
