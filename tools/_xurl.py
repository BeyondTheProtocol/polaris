#!/usr/bin/env python3
"""_xurl.py — envoltorio fino y FRUGAL sobre la CLI `xurl` (X API v2), SOLO LECTURA.

Sustituye a Grok/cookies-web para los daemons deterministas de X
(`x_mentions.py`, `x_dms.py`, `x_radar.py`, `x_centinela.py`). `xurl` ya trae su
propio OAuth cacheado en `~/.xurl` (nunca lo tocamos ni lo copiamos: el binario
es la única puerta). Aquí solo invocamos el binario, parseamos su JSON nativo y
normalizamos usuarios vía `includes.users` (gratis, viene en la misma llamada).

Fail-soft por diseño: si `xurl` no está instalado, si el token caducó/falta
crédito, o si la respuesta no es JSON válido, cada función devuelve
`XurlError` (nunca lanza una excepción cruda) para que el daemon llamante
pueda degradar (a Grok) o avisar sin romperse.

Coste aprox. (Spend Cap de X, lectura pura, según lo documentado por {{TITULAR}}):
  ~0,001 $ por lectura propia · ~0,005 $ de coste general por lectura.
  Cada función de este módulo = 1 sola llamada a `xurl` = 1 lectura.
"""
import json
import os
import shutil
import subprocess

XURL_BIN = shutil.which("xurl") or "/opt/homebrew/bin/xurl"
TIMEOUT = 25


def _env_con_node():
    """`xurl` es un script `#!/usr/bin/env node` → necesita `node` en el PATH. Los daemons launchd
    arrancan con un PATH mínimo (sin /opt/homebrew/bin), así que garantizamos aquí que el directorio
    de node esté en el PATH del subproceso. Fix 3/7: 'env: node: No such file or directory' en
    x-dms-watch tras migrar a xurl."""
    env = dict(os.environ)
    node = shutil.which("node") or "/opt/homebrew/bin/node"
    node_dir = os.path.dirname(node)
    partes = env.get("PATH", "").split(os.pathsep)
    if node_dir and node_dir not in partes:
        env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")
    return env
COSTE_LECTURA_USD = 0.001   # aprox., propia (Spend Cap X) — documentar en cada llamada
COSTE_LECTURA_GENERAL_USD = 0.005  # aprox., coste general asociado


class XurlError(Exception):
    """Fallo al invocar xurl o al parsear su respuesta. Mensaje = motivo humano."""


def disponible():
    """True si el binario xurl existe en PATH (no implica que el token sea válido)."""
    return bool(shutil.which("xurl")) or os.path.exists(XURL_BIN)


def _run(args):
    if not disponible():
        raise XurlError("xurl no está instalado (brew install xurl) — degradar o avisar.")
    try:
        out = subprocess.run([XURL_BIN] + args, capture_output=True, text=True, timeout=TIMEOUT,
                             env=_env_con_node())
    except FileNotFoundError:
        raise XurlError("xurl no está instalado (brew install xurl) — degradar o avisar.")
    except subprocess.TimeoutExpired:
        raise XurlError("xurl no respondió a tiempo (timeout %ds)." % TIMEOUT)
    except Exception as e:
        raise XurlError("Error al invocar xurl: %s" % e)

    body = (out.stdout or "").strip()
    err = (out.stderr or "").strip()
    if not body:
        # xurl a veces sale con rc!=0 pero ya escribió el error a stderr/stdout
        raise XurlError(err or "xurl no devolvió nada (¿token caducado o sin crédito?).")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise XurlError("xurl devolvió una respuesta no-JSON: %s" % body[:200])

    if isinstance(data, dict) and "errors" in data and "data" not in data:
        msgs = "; ".join(str(e.get("message", e)) for e in data.get("errors", []))
        raise XurlError("X API devolvió error: %s" % (msgs or "desconocido"))
    # Formato problem+json (p.ej. 402 «credits depleted»): no trae `errors` y sin este freno
    # pasaba como respuesta vacía -> «✅ 0 nuevos» con la API caída (24-sep-2026).
    if isinstance(data, dict) and "data" not in data and (
            isinstance(data.get("status"), int) and data["status"] >= 400
            or ("title" in data and "type" in data)):
        raise XurlError("X API devolvió error %s: %s" % (
            data.get("status", "?"), data.get("detail") or data.get("title") or "desconocido"))
    return data


def _users_by_id(includes):
    """dict id -> {'username', 'name'} desde includes.users de la respuesta."""
    users = {}
    for u in (includes or {}).get("users", []) or []:
        uid = str(u.get("id", ""))
        if uid:
            users[uid] = {"username": u.get("username", ""), "name": u.get("name", "")}
    return users


def whoami():
    """id/username de la cuenta autenticada (@titular). 1 lectura barata."""
    d = _run(["/2/users/me"])
    return (d.get("data") or {})


def mentions(max_results=10):
    """Menciones públicas recientes a la cuenta autenticada. 1 lectura.
    Devuelve lista de dicts: {id, author_id, author_username, author_name, text, created_at, url}."""
    max_results = max(5, min(100, int(max_results)))
    d = _run(["mentions", "-n", str(max_results)])
    users = _users_by_id(d.get("includes"))
    out = []
    for t in d.get("data", []) or []:
        aid = str(t.get("author_id", ""))
        u = users.get(aid, {})
        out.append({
            "id": str(t.get("id", "")),
            "author_id": aid,
            "author_username": u.get("username", ""),
            "author_name": u.get("name", ""),
            "text": t.get("text", "") or "",
            "created_at": t.get("created_at", ""),
            "url": ("https://x.com/%s/status/%s" % (u.get("username") or "i", t.get("id", ""))),
        })
    return out


def search_recent(query, max_results=10):
    """Búsqueda de posts recientes por query. 1 lectura por llamada."""
    max_results = max(10, min(100, int(max_results)))
    d = _run(["search", query, "-n", str(max_results)])
    users = _users_by_id(d.get("includes"))
    out = []
    for t in d.get("data", []) or []:
        aid = str(t.get("author_id", ""))
        u = users.get(aid, {})
        out.append({
            "id": str(t.get("id", "")),
            "author_id": aid,
            "author_username": u.get("username", ""),
            "author_name": u.get("name", ""),
            "text": t.get("text", "") or "",
            "created_at": t.get("created_at", ""),
            "url": ("https://x.com/%s/status/%s" % (u.get("username") or "i", t.get("id", ""))),
        })
    return out


def dms(max_results=10):
    """Eventos de DM recientes (todas las conversaciones). 1 lectura.
    OJO: la API v2 de DMs no incluye `includes.users` — solo sender_id/conversation_id;
    resolver @handle exige 1 lectura extra por autor nuevo (ver `resolve_users`)."""
    max_results = max(1, min(100, int(max_results)))
    d = _run(["dms", "-n", str(max_results)])
    out = []
    for m in d.get("data", []) or []:
        out.append({
            "id": str(m.get("id", "")),
            "conversation_id": m.get("dm_conversation_id", ""),
            "sender_id": str(m.get("sender_id", "")),
            "text": m.get("text", "") or "",
            "created_at": m.get("created_at", ""),
            "event_type": m.get("event_type", ""),
        })
    return out


def bookmarks(max_results=10):
    """Guardados (bookmarks) de la cuenta autenticada. 1 lectura."""
    max_results = max(1, min(100, int(max_results)))
    d = _run(["bookmarks", "-n", str(max_results)])
    users = _users_by_id(d.get("includes"))
    out = []
    for t in d.get("data", []) or []:
        aid = str(t.get("author_id", ""))
        u = users.get(aid, {})
        out.append({
            "id": str(t.get("id", "")),
            "author_id": aid,
            "author_username": u.get("username", ""),
            "author_name": u.get("name", ""),
            "text": t.get("text", "") or "",
            "created_at": t.get("created_at", ""),
            "url": ("https://x.com/%s/status/%s" % (u.get("username") or "i", t.get("id", ""))),
        })
    return out


# Campos que hacen falta para que un guardado NO llegue ciego: el cuerpo del artículo largo,
# el texto sin truncar (note_tweet), los enlaces expandidos y el post citado.
_CAMPOS_RICOS = ("article,note_tweet,entities,referenced_tweets,created_at,text,author_id")


def posts_ricos(ids, tope_cuerpo=4000):
    """Contenido COMPLETO de unos posts por id: cuerpo del artículo largo, texto sin truncar,
    enlaces expandidos y el post citado. UNA lectura por cada 100 ids.

    Por qué existe (25-jul-2026): `xurl bookmarks` devuelve campos fijos (`text` truncado a 280,
    sin `article` ni `note_tweet`), así que un guardado cuyo contenido vive detrás de un `t.co`
    llegaba a la cosecha como «(solo link)» y el triaje por texto era estructuralmente ciego —
    dentro había artículos de miles de palabras, hilos completos e incluso un informe entero.
    Esto NO es una fuente nueva: es la misma API y el mismo OAuth, un campo más.

    Devuelve {id: {"cuerpo","articulo_titulo","enlaces","cita_id","cita_texto"}}. `cuerpo` es el
    texto largo (artículo o note_tweet) recortado a `tope_cuerpo` para no inflar el jsonl.
    """
    ids = [str(i) for i in dict.fromkeys(ids) if i]
    if not ids:
        return {}
    out = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        d = _run(["/2/tweets?ids=%s&tweet.fields=%s&expansions=referenced_tweets.id"
                  % (",".join(chunk), _CAMPOS_RICOS)])
        citados = {str(p.get("id")): p for p in (d.get("includes") or {}).get("tweets", []) or []}
        for p in d.get("data", []) or []:
            art = p.get("article") or {}
            largo = (art.get("plain_text") or "").strip()
            if len(largo) < 3:                      # artículos vacíos (p. ej. solo un GIF)
                largo = ""
            nota = ((p.get("note_tweet") or {}).get("text") or "").strip()
            cuerpo = largo or nota
            enlaces = []
            for u in ((p.get("entities") or {}).get("urls") or []):
                exp = u.get("expanded_url") or ""
                if not exp or "/photo/" in exp or "/video/" in exp:
                    continue
                enlaces.append({"url": exp, "titulo": (u.get("title") or "").strip()})
            cita_id, cita_texto = "", ""
            for rt in (p.get("referenced_tweets") or []):
                q = citados.get(str(rt.get("id")))
                if not q:
                    continue
                cita_id = str(rt.get("id"))
                cita_texto = (((q.get("note_tweet") or {}).get("text")) or q.get("text") or "").strip()
                break
            out[str(p.get("id"))] = {
                "cuerpo": cuerpo[:tope_cuerpo],
                "articulo_titulo": (art.get("title") or "").strip(),
                "enlaces": enlaces,
                "cita_id": cita_id,
                "cita_texto": cita_texto[:tope_cuerpo],
            }
    return out


def resolve_users(ids):
    """Resuelve una lista de author_id -> {'username','name'} en UNA sola lectura
    batched (hasta 100 ids por llamada, límite de /2/users). Frugal: solo se llama
    con los ids que de verdad faltan (dedup por el caller)."""
    ids = [str(i) for i in dict.fromkeys(ids) if i]  # dedup preservando orden
    if not ids:
        return {}
    out = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        d = _run(["/2/users?ids=%s" % ",".join(chunk)])
        for u in d.get("data", []) or []:
            out[str(u.get("id", ""))] = {"username": u.get("username", ""), "name": u.get("name", "")}
    return out
