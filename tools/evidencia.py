#!/usr/bin/env python3
"""tools/evidencia.py — carril de EVIDENCIA por navegador: Consensus · scite · Elicit · Undermind.

⭐ Consensus va por su **MCP oficial** vía cliente PROPIO desacoplado (`consensus_mcp.py`,
OAuth + token cacheado, sin navegador ni `claude`). Login una vez: `evidencia.py consensus --login`.
Consensus y scite van por su **MCP oficial** (cliente propio desacoplado), Elicit por su API REST
propia; solo **undermind** sigue por navegador hasta que tenga su propio carril.

Verificado por web 23/6/26 (estado de las APIs REST de pago; el MCP de Consensus lo CORRIGE):
  · Consensus — API REST de pago ~$0.10/llamada; **pero el MCP oficial va con el plan Pro** (OAuth). ✅ usado.
  · scite.ai  — API = plan Developer $250/mes (enterprise). Personal ($20/mes) = sin API.
  · Undermind — API solo Enterprise. Pro ($16/mes) = sin API.
  · Elicit    — API GRATIS pero SOLO en plan **Pro+** (Plus NO la trae). Si {{TITULAR}} tiene Pro →
                se monta un cliente REAL (clave `btp-elicit-api`, docs.elicit.com). Si Plus → navegador.
Así que el sistema usa lo que ella YA paga, por el navegador sobre su sesión logueada.

🛡️ Varias van detrás de Cloudflare → `agent-browser` a pelo NO pasa (reto anti-bot). Vía fiable =
Chrome REAL de {{TITULAR}} (Chrome MCP, logueada) o PUENTE MANUAL. Este helper construye la URL y
sonda; si topa con el muro anti-bot / login, te dice usar Chrome real o el puente manual.

🔒 MURO: consultas a NIVEL DE TEMA (nunca mutaciones/HLA/VCF/PII crudos en el buscador). El
resultado es DATO a verificar contra la fuente primaria (consenso/citas ≠ verdad).

Uso:
  python3 tools/evidencia.py consensus "research question"
  python3 tools/evidencia.py scite     "claim/paper a comprobar (apoyado vs contradicho)"
  python3 tools/evidencia.py elicit    "research question (revisión + extracción)"
  python3 tools/evidencia.py undermind "deep search topic (papers oscuros)"
  python3 tools/evidencia.py openevidence "pregunta clínica"   # puente manual: ida
  python3 tools/evidencia.py openevidence --leer <url /ask/...> # puente manual: vuelta
  python3 tools/evidencia.py <tool> --url-only "..."   # solo la URL (puente manual / Chrome)
"""
import sys, os, json, subprocess, urllib.parse, urllib.request, urllib.error

ROOT = os.environ.get("BTP_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # para _secrets
try:
    from _secrets import get as _get_secret
except Exception:
    def _get_secret(*a, **k):
        return None


def _cb(*parts):
    """Resuelve una ruta contra ROOT y, si no existe, contra casa base (~/claudecode).
    Así los carriles desacoplados (venv + scripts MCP) funcionan también desde un worktree,
    donde el venv gitignored no está pero sí vive en casa base."""
    p = os.path.join(ROOT, *parts)
    if os.path.exists(p):
        return p
    return os.path.join(os.path.expanduser("~/claudecode"), *parts)


AB = "agent-browser"

def _borde_check(query: str, destino: str) -> bool:
    """🛡️ MURO: ninguna consulta sale a un buscador externo sin pasar el borde.
    FAIL-CLOSED: si borde no carga, se bloquea (no se sale a ciegas)."""
    try:
        import os as _os
        import sys as _sys
        _here = _os.path.dirname(_os.path.abspath(__file__))
        if _here not in _sys.path:
            _sys.path.insert(0, _here)
        import borde
        # Puerta ingeniera (no la genérica): una búsqueda de literatura puede llevar
        # nombre de gen / tipo de tumor (no identifica a {{TITULAR}}), pero NUNCA una huella
        # genómica del paciente (variante/coordenada/HLA/rsID/genotipo) ni PII.
        ok, motivo = borde.egress_cientifico(query, destino=destino)
        if not ok:
            sys.stderr.write("BORDE: no envio a %s - %s\n" % (destino, motivo))
        return ok
    except Exception:
        print("[%s] ERROR: borde.py no disponible — llamada BLOQUEADA" % destino,
              file=sys.stderr)
        return False

TOOLS = {
    "consensus": "https://consensus.app/search/?q=",
    "scite":     "https://scite.ai/search?q=",
    "elicit":    "https://elicit.com/search?q=",
    "undermind": "https://app.undermind.ai/",   # búsqueda agéntica en la app (sin ?q= simple)
}
LOGIN_HINTS = ("log in", "sign in", "create a free account", "continue with google",
               "iniciar sesión", "create account")
BOT_WALL = ("verificación de seguridad", "security check", "are you a robot", "are you human",
            "cloudflare", "cloudfront", "ray id", "checking your browser", "bots maliciosos",
            "verify you are", "403 error", "request blocked", "could not be satisfied",
            "request id:", "access denied")


def _ab(*a, timeout=90):
    try:
        r = subprocess.run([AB, *a], capture_output=True, text=True, timeout=timeout)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except FileNotFoundError:
        return 127, "agent-browser no instalado"
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def _consensus(q, flags):
    """Carril Consensus DESACOPLADO: usa el cliente MCP propio (OAuth + token cacheado),
    sin navegador ni Cloudflare ni binario `claude`. Ver tools/consensus_mcp.py."""
    py = _cb(".venv-consensus", "bin", "python")
    script = _cb("tools", "consensus_mcp.py")
    if not os.path.exists(py):
        print("⚠️ falta el venv .venv-consensus. Créalo:")
        print("   python3 -m venv .venv-consensus && .venv-consensus/bin/pip install mcp")
        return 1
    if "--login" in flags:
        # interactivo: abre el navegador para el OAuth de {{TITULAR}} (su gate, una vez)
        return subprocess.run([py, script, "--login"]).returncode
    if not q:
        print('uso: evidencia.py consensus "pregunta"   |   evidencia.py consensus --login')
        return 2
    args = [py, script] + (["--json"] if "--json" in flags else []) + [q]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        # Antes salía como TRACEBACK crudo (visto 27/7 con el OAuth caducado): feo para
        # quien llama y, desde la centralita, indistinguible de un fallo cualquiera.
        print("⚠️ consensus_mcp no respondió en 90 s. Si el OAuth caducó: "
              "python3 tools/evidencia.py consensus --login")
        return 1
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        err = (r.stderr or out).strip()
        if "unauthorized" in err.lower() or "auth" in err.lower():
            print("🔐 Consensus necesita tu login (una vez): python3 tools/evidencia.py consensus --login")
        else:
            print("⚠️ consensus_mcp falló: " + err[:300])
        return 1
    if "--json" in flags:
        print(out)
        return 0
    print(f"# consensus · «{q}»\n")
    print(out[:6000])
    print("\n— DATO EXTERNO: verificar contra fuente primaria (PubMed/ensayos) antes de usar.")
    return 0


def _elicit(q, flags):
    """Carril Elicit DESACOPLADO: API REST propia (clave btp-elicit-api), sin navegador
    ni `claude`. Búsqueda de papers sobre 138M + PubMed. Plan Pro: 100 results/req, 100/día.
    (La revisión sistemática completa es solo Enterprise; aquí = búsqueda.)"""
    key = (_get_secret("btp-elicit-api") or "").strip()
    if not key:
        print("⚠️ falta la clave btp-elicit-api en el Llavero.")
        return 1
    if not q:
        print('uso: evidencia.py elicit "pregunta"')
        return 2
    body = json.dumps({"query": q, "corpus": "pubmed", "maxResults": 10}).encode()
    # UA real: Elicit está tras Cloudflare y bloquea el UA por defecto de urllib (error 1010).
    _UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")
    req = urllib.request.Request(
        "https://elicit.com/api/v1/search", data=body, method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                 "User-Agent": _UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        det = e.read().decode("utf-8", "replace")[:200] if hasattr(e, "read") else ""
        if e.code in (401, 403):
            print("🔐 Elicit: clave inválida o sin permiso (HTTP %d). ¿La key es `elk_live_…` de plan Pro+?" % e.code)
        else:
            print("⚠️ Elicit HTTP %d: %s" % (e.code, det))
        return 1
    except Exception as e:
        print("⚠️ Elicit error: %s" % (repr(e)[:200],))
        return 1
    papers = data.get("papers") or []
    if "--json" in flags:
        print(json.dumps(data, ensure_ascii=False))
        return 0
    print(f"# elicit · «{q}»  ({len(papers)} papers)\n")
    for i, p in enumerate(papers, 1):
        au = ", ".join((p.get("authors") or [])[:2]) + (" et al." if len(p.get("authors") or []) > 2 else "")
        print("[%d] %s (%s, %s)" % (i, p.get("title", "¿?"), au or "—", p.get("year", "—")))
        if p.get("doi"):
            print("    doi: %s" % p["doi"])
        ab = (p.get("abstract") or "").strip().replace("\n", " ")
        if ab:
            print("    " + ab[:240] + ("…" if len(ab) > 240 else ""))
        print()
    print("— DATO EXTERNO: verificar contra fuente primaria (PubMed/ensayos) antes de usar.")
    return 0


def _scite_mcp(q, flags):
    """Carril scite DESACOPLADO: cliente MCP propio (OAuth + token cacheado), sin navegador ni
    binario `claude`. Lo único que las demás no dan: Smart Citations (¿el paper fue apoyado /
    mencionado / CONTRADICHO después?). Ver tools/scite_mcp.py. Reusa el venv de Consensus."""
    py = _cb(".venv-consensus", "bin", "python")   # mismo SDK `mcp` que consensus
    script = _cb("tools", "scite_mcp.py")
    if not os.path.exists(py):
        print("⚠️ falta el venv .venv-consensus. Créalo:")
        print("   python3 -m venv .venv-consensus && .venv-consensus/bin/pip install mcp")
        return 1
    if "--login" in flags:
        return subprocess.run([py, script, "--login"]).returncode
    if "--tools" in flags:
        return subprocess.run([py, script, "--tools"]).returncode
    if not q:
        print('uso: evidencia.py scite "claim/paper"  |  scite --login  |  scite --tools')
        return 2
    args = [py, script] + (["--json"] if "--json" in flags else []) + [q]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        # Antes salía como TRACEBACK crudo (visto 27/7 con el OAuth caducado): feo para
        # quien llama y, desde la centralita, indistinguible de un fallo cualquiera.
        print("⚠️ scite_mcp no respondió en 90 s. Si el OAuth caducó: "
              "python3 tools/evidencia.py scite --login")
        return 1
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        err = (r.stderr or out).strip()
        if "unauthorized" in err.lower() or "auth" in err.lower():
            print("🔐 scite necesita tu login (una vez): python3 tools/evidencia.py scite --login")
        else:
            print("⚠️ scite_mcp falló: " + err[:300])
        return 1
    if "--json" in flags:
        print(out)
        return 0
    print(f"# scite · «{q}»\n")
    print(out[:6000])
    print("\n— DATO EXTERNO (Smart Citations): cotejar contra fuente primaria (PMID/DOI) antes de usar.")
    return 0


# ── OpenEvidence: puente manual de DOS TRAMOS ────────────────────────────────────────────────
# No tiene deep link de pregunta: la consulta va por POST (/api/article), así que NINGUNA URL la
# prellena — verificado el 12-sep-26 leyendo el cliente MCP no oficial y su robots.txt
# (Disallow: /api/). Y su ToS prohíbe explícitamente entrar con «any engine, software, tool,
# agent... (scripts, bots, spiders, scraper, crawlers)», sin excepción por tipo de cuenta: la
# cuenta clínica de {{TITULAR}} la hace ELEGIBLE, no le da permiso para automatizar.
# Por eso este carril NO toca su sesión:
#   IDA   — deja la pregunta en el portapapeles y da la URL. La escribe ella, en su Chrome.
#   VUELTA— ella pulsa Share y devuelve /ask/<uuid>, que es PÚBLICO: eso se lee con un fetch
#           normal, sin cookies ni sesión, y ahí no hay nada que infringir.
OE_ASK = "https://www.openevidence.com/ask"


def _openevidence(q, flags):
    if "--leer" in flags:
        # `flags` es un SET (así lo parsea main), así que el enlace llega en los posicionales,
        # es decir dentro de `q`. No se busca por índice.
        ref = q.strip()
        if not ref:
            print("uso: evidencia.py openevidence --leer <url /ask/<uuid> o el uuid>"); return 2
        url = ref if ref.startswith("http") else "https://www.openevidence.com/ask/" + ref
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            html = urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "replace")
        except Exception as e:
            print("No se pudo leer la respuesta compartida: %s" % e)
            print("¿Le diste a Share? Sin compartir, esa página pide sesión y no se puede leer.")
            return 1
        import re as _re
        txt = _re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=_re.S)
        txt = _re.sub(r"<[^>]+>", "\n", txt)
        txt = _re.sub(r"\n{2,}", "\n", _html_unescape(txt)).strip()
        low = txt.lower()
        # Una respuesta NO compartida (o un uuid que no existe) devuelve 200 con la portada de
        # registro, no un 404. Sin este filtro se entregaba «Sign Up» como si fuera evidencia:
        # se reusan los mismos detectores del resto del fichero, para cazar la clase entera.
        if any(h in low for h in BOT_WALL):
            print("🛡️ OpenEvidence bloqueó la lectura (anti-bot). Reintenta desde tu Chrome.")
            return 4
        if len(txt) < 600 or any(h in low for h in LOGIN_HINTS):
            print("🔒 Eso no es una respuesta compartida: vino la página de registro/login.")
            print("   Abre la respuesta en tu Chrome, pulsa **Share** y pásame ESE enlace.")
            return 3
        print("# openevidence · %s\n" % url)
        print(txt[:8000])
        print("\n— DATO EXTERNO: verificar contra fuente primaria antes de usar.")
        return 0

    if not q:
        print('uso: evidencia.py openevidence "pregunta"   ·   --leer <url> para la vuelta')
        return 2
    copiado = False
    try:
        pb = subprocess.run(["pbcopy"], input=q, text=True, timeout=5)
        copiado = pb.returncode == 0
    except Exception:
        pass
    print("# openevidence · puente manual (su ToS no deja automatizarlo)")
    print("\nPREGUNTA%s:" % ("  [copiada al portapapeles]" if copiado else ""))
    print("  %s" % q)
    print("\n1. Abre %s en TU Chrome (el que tiene la sesión clínica)." % OE_ASK)
    print("2. Pega la pregunta y envíala.")
    print("3. Dale a **Share** en la respuesta y copia el enlace /ask/<uuid>.")
    print("4. Devuélvemelo:  python3 tools/evidencia.py openevidence --leer <enlace>")
    print("\n   (El paso 3 es el que importa: ese enlace es público, así que lo leo sin tocar")
    print("    tu sesión ni saltarme nada.)")
    return 0


def _html_unescape(t):
    import html as _h
    return _h.unescape(t)


def main(argv):
    flags = {a for a in argv if a.startswith("--")}
    pos = [a for a in argv if not a.startswith("--")]
    # 🛡️ MURO: TODA consulta pasa el borde antes de salir (los 4 carriles).
    _dest = pos[0] if (pos and pos[0] in TOOLS) else "evidencia"
    _q0 = " ".join(pos[1:]).strip() if len(pos) > 1 else ""
    if _q0 and not _borde_check(_q0, _dest):
        return 3
    # consensus tiene su propio carril desacoplado (MCP), incluso para --login (sin query)
    if (pos and pos[0] == "consensus") or ("--login" in flags and (not pos or pos == ["consensus"])):
        return _consensus(" ".join(pos[1:]).strip(), flags)
    # elicit tiene su propio carril desacoplado (API REST propia)
    if pos and pos[0] == "elicit":
        return _elicit(" ".join(pos[1:]).strip(), flags)
    # scite tiene su propio carril desacoplado (MCP de scite — Smart Citations)
    if pos and pos[0] == "scite":
        return _scite_mcp(" ".join(pos[1:]).strip(), flags)
    # openevidence: puente manual de dos tramos (no tiene deep link y su ToS veta automatizar)
    if pos and pos[0] == "openevidence":
        return _openevidence(" ".join(pos[1:]).strip(), flags)
    if not pos or pos[0] not in TOOLS:
        print('uso: evidencia.py <consensus|scite|elicit|undermind|openevidence> "pregunta" [--url-only]')
        return 2
    tool = pos[0]
    q = " ".join(pos[1:]).strip()
    base = TOOLS[tool]
    url = base + (urllib.parse.quote(q) if "?q=" in base and q else "")

    if "--url-only" in flags or not q:
        print(url)
        return 0

    rc, out = _ab("open", url)
    if rc != 0:
        print(f"⚠️ no pude abrir el navegador (rc={rc}): {out[:160]}")
        return 1
    _ab("wait", "6000", timeout=25)
    rc, text = _ab("eval", "document.body.innerText")
    if rc != 0 or not text:
        rc, text = _ab("snapshot", "-q")
    text = (text or "").strip()
    low = text.lower()

    if any(h in low for h in BOT_WALL):
        print(f"🛡️ {tool} bloqueó al navegador-bot (anti-bot/Cloudflare). Usa el Chrome REAL de")
        print(f"   {{TITULAR}} (Chrome MCP, logueada) o el puente manual. URL:\n   {url}")
        return 4
    if (len(text) < 400 and any(h in low for h in LOGIN_HINTS)) or not text:
        print(f"🔒 {tool}: sin sesión iniciada (sale el login). {{TITULAR}} entra una vez y reintenta.")
        print(f"   URL: {url}")
        return 3

    print(f"# {tool} · «{q}»\n{url}\n")
    print(text[:6000])
    print("\n— DATO EXTERNO: verificar contra fuente primaria (PubMed/ensayos) antes de usar.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
