#!/usr/bin/env python3
"""x_guardados.py — cosechador SOLO-LECTURA de los GUARDADOS (bookmarks) de X de {{TITULAR}}.

Fuente PRIMARIA: `xurl` (X API v2, OAuth cacheado en ~/.xurl) vía `tools/_xurl.py` —
endpoint `bookmarks` (1 lectura, hasta 100/pasada; ~0,001 $ propia / ~0,005 $
general). Sustituye al scraping de cookies web (`~/.agent-browser/x_state.json` +
GraphQL `Bookmarks`, que X rota cada pocas semanas). Mismo aterrizaje que antes
(`_cajita/x_guardados/`) para no romper lo que consume `auto-mejora`.

Fallback: NO hay carril alternativo para bookmarks (son privados; Grok no los ve).
Si `xurl` falla (no instalado / token caducado / sin crédito), avisa claro y NO
inventa datos — reintenta en la próxima pasada.

Para qué: {{TITULAR}} guarda en X cosas que le interesan (papers, hilos, cuentas, ideas).
Este tool las cosecha para la MEJORA CONTINUA — las deja triadas para que el agente
`auto-mejora` las mine como fuente (¿qué de esto acerca a NED / mejora el sistema?).

NUNCA responde, da like, publica ni toca la cuenta. Los guardados crudos se quedan en
`_cajita/x_guardados/` (gitignored, sin PII en el repo). Dedup por id de tweet: cada
pasada solo captura los NUEVOS desde la última vez.

Uso:
  python3 tools/x_guardados.py fetch            # captura los guardados NUEVOS a local
  python3 tools/x_guardados.py fetch --notify    # además avisa a {{TITULAR}} por Telegram
  python3 tools/x_guardados.py fetch --all       # ignora el estado: vuelca todo (debug)
  python3 tools/x_guardados.py fetch --max 100   # nº de guardados a pedir (1-100; def. 40)
  python3 tools/x_guardados.py listar            # digest de los nuevos (para auto-mejora)
  python3 tools/x_guardados.py listar --since 7  # guardados de los últimos 7 días
  python3 tools/x_guardados.py --digest          # alias de `listar` (para auto-mejora)
"""
import json, os, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)  # para importar salida y _xurl
import _xurl  # noqa: E402

OUT_DIR = os.path.join(ROOT, "_cajita", "x_guardados")
SEEN_FILE = os.path.join(OUT_DIR, "seen.json")
STORE_FILE = os.path.join(OUT_DIR, "guardados.jsonl")  # acumulado, dedup por id
MAX_RESULTS = 40

# Etiquetado determinista hacia la mejora continua (NED + sistema). El triaje semántico
# fino lo hace `auto-mejora` sobre el jsonl; aquí solo flags baratos por palabra clave.
KW_NED = [
    "oncolog", "cancer", "cáncer", "tumor", "mama", "breast", "metast",
    "ensayo", "trial", "vacuna", "vaccine", "neoantig", "neoantíg", "inmun",
    "immun", "laborator", "investiga", "research", "científic", "scientist",
    "biopsia", "biopsy", "genóm", "genom", "secuenc", "sequenc",
    "immunotherap", "inmunoterap", "ned ", " ned", "terapia", "therapy",
    "protocolo", "clinical", "clínic",
]
KW_SISTEMA = [
    "agent", "agente", "llm", "claude", "anthropic", "prompt", "rag",
    "mcp", "tool use", "tooling", "pipeline", "automat", "workflow",
    "open-weight", "open weight", "fine-tun", "embedding", "vector",
    "deepseek", "nvidia", "nim", "context window", "evals", "benchmark",
    "framework", "python", "open source", "open-source", "self-host",
]


def _flag(text):
    t = (text or "").lower()
    tags = []
    if any(k in t for k in KW_NED):
        tags.append("NED")
    if any(k in t for k in KW_SISTEMA):
        tags.append("sistema")
    return tags


def _load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            return set(json.load(open(SEEN_FILE)).get("ids", []))
        except Exception:
            pass
    return set()


def _texto_completo(rec):
    """Todo lo que se sabe del guardado en un solo texto: lo visible + el título y cuerpo del
    artículo + el post citado + los títulos de los enlaces. Para etiquetar y para el triaje."""
    partes = [rec.get("text") or "", rec.get("articulo_titulo") or "",
              rec.get("cuerpo") or "", rec.get("cita_texto") or ""]
    partes += [(e or {}).get("titulo") or "" for e in (rec.get("enlaces") or [])]
    return "\n".join(p for p in partes if p)


def _enriquecer(nuevos):
    """Rellena cada guardado nuevo con su contenido REAL (artículo largo, texto sin truncar,
    enlaces expandidos, post citado) vía `_xurl.posts_ricos`.

    Por qué (25-jul-2026): un guardado sin texto NO es un guardado sin contenido. Los 25 que
    llegaban como «(solo link)» tenían dentro artículos enteros, hilos completos y hasta un
    informe clínico, y llevaban dos meses descartados como «ruido» porque el triaje solo veía
    el `t.co`. Arreglar la CAPTURA mata la categoría de fallo; arreglar los 25 a mano, no.

    FAIL-SOFT a propósito: si la lectura de enriquecido falla (API caída, límite, token), la
    cosecha NO se cae — guarda lo básico y deja `enriquecido: False` para que se pueda reintentar
    y para que nadie confunda «no había contenido» con «no pude leerlo».
    """
    if not nuevos:
        return
    try:
        ricos = _xurl.posts_ricos([t["id"] for t in nuevos])
    except _xurl.XurlError as e:
        print("⚠️  no pude leer el contenido completo de los nuevos (%s). Guardo lo básico y "
              "marco enriquecido=False: es reintentable, NO es «sin contenido»." % e,
              file=sys.stderr)
        for t in nuevos:
            t["enriquecido"] = False
        return
    for t in nuevos:
        r = ricos.get(t["id"])
        if not r:
            t["enriquecido"] = False
            continue
        t.update({k: v for k, v in r.items() if v})
        t["enriquecido"] = True


def _load_store():
    """Lee el acumulado jsonl -> dict id->record (dedup por id)."""
    store = {}
    if os.path.exists(STORE_FILE):
        for ln in open(STORE_FILE, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
                store[r["id"]] = r
            except Exception:
                continue
    return store


def cmd_fetch(notify, show_all, max_results):
    try:
        raw = _xurl.bookmarks(max_results=max_results)
    except _xurl.XurlError as e:
        msg = ("⚠️ No pude leer tus GUARDADOS de X (%s). Revisa `xurl auth` / crédito. "
               "No invento datos; reintento en la próxima pasada." % e)
        print(msg)
        if notify:
            try:
                import salida; salida.report_to_titular(msg)
            except Exception:
                pass
        return 3

    tweets = []
    for t in raw:
        tweets.append({
            "id": t["id"], "handle": t["author_username"], "name": t["author_name"],
            "text": t["text"], "url": t["url"],
            "ts": int(datetime.datetime.fromisoformat(t["created_at"].replace("Z", "+00:00")).timestamp())
                  if t["created_at"] else 0,
            "iso": t["created_at"],
        })

    # dedup por id dentro de esta cosecha
    uniq = {}
    for t in tweets:
        uniq[t["id"]] = t
    tweets = list(uniq.values())

    os.makedirs(OUT_DIR, exist_ok=True)
    seen = set() if show_all else _load_seen()
    first_run = not os.path.exists(SEEN_FILE)
    store = _load_store()

    new = [t for t in tweets if t["id"] not in seen]
    _enriquecer(new)
    for t in new:
        t["fetched"] = datetime.date.today().isoformat()
        # Clasifica con TODO lo que se sabe del guardado, no con los 280 caracteres: un tuit
        # que solo trae un t.co no tiene palabras que etiquetar, y así entraba como "(sin tag)".
        t["tags"] = _flag(_texto_completo(t))

    # acumula al store (dedup por id) y reescribe el jsonl ordenado por captura
    for t in new:
        store[t["id"]] = t
    with open(STORE_FILE, "w", encoding="utf-8") as f:
        for r in sorted(store.values(), key=lambda x: x.get("ts", 0)):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # estado: todo lo visto en esta pasada queda marcado
    json.dump({"ids": sorted(set(seen) | {t["id"] for t in tweets}), "updated": int(datetime.datetime.now().timestamp())},
              open(SEEN_FILE, "w"))

    n_ned = sum(1 for t in new if "NED" in t["tags"])
    n_sis = sum(1 for t in new if "sistema" in t["tags"])
    if first_run and not show_all:
        print("👁️  primera pasada: línea base de %d guardados (a partir de ahora solo los nuevos)."
              % len(tweets))
    print("✅ guardados leídos: %d · nuevos: %d (NED: %d · sistema: %d)"
          % (len(tweets), len(new), n_ned, n_sis))
    print("   acumulado: %s (%d total)" % (os.path.relpath(STORE_FILE, ROOT), len(store)))

    if notify and new and not first_run:
        try:
            import salida
            body = "🔖 X guardados: %d nuevos" % len(new)
            if n_ned or n_sis:
                body += " (NED: %d · sistema: %d)" % (n_ned, n_sis)
            body += "\n\n" + "\n".join(
                "• @%s: %s" % (t["handle"] or "?", (t["text"][:120] or "(sin texto)").replace("\n", " "))
                for t in sorted(new, key=lambda x: -x.get("ts", 0))[:8])
            body += "\n\nLos cosecha auto-mejora: python3 tools/x_guardados.py listar"
            salida.report_to_titular(body)
            print("[avisado por Telegram]")
        except Exception as e:
            print("[notify] no se pudo enviar: %s" % e, file=sys.stderr)
    return 0


def _dias_desde_ultima_cosecha(store):
    """Antigüedad (en días) del guardado más reciente que se llegó a COSECHAR.
    None si el store está vacío o ninguna entrada trae fecha de cosecha."""
    fechas = [r.get("fetched") for r in store.values() if r.get("fetched")]
    if not fechas:
        return None
    ultima = datetime.date.fromisoformat(max(fechas))
    return (datetime.date.today() - ultima).days


# Umbral de rancidez: `fetch` corre a diario (com.btp.x-guardados). Si el guardado más
# reciente tiene más de esto, el fetch no está corriendo aunque haya sesión.
DIAS_RANCIO = 2


def cmd_listar(since_days):
    """Digest de los guardados para que `auto-mejora` los ingiera. Por defecto, los
    capturados en la última pasada (fetched == hoy); con --since N, los de N días.

    FAIL-CLOSED (8-jul-2026): «no pude mirarlo» y «sin novedad» NO son lo mismo. Sin vía de
    lectura de los bookmarks este comando NO puede decir «sin guardados nuevos» —llevan sin
    leerse desde la última cosecha—, así que aborta con exit 2 igual que `cmd_fetch`. Antes leía
    el caché rancio y anunciaba «sin novedad»: 10 días diciendo que no había nada cuando lo que
    pasaba es que nadie estaba mirando.

    ARREGLADO 25-jul-2026: la puerta fail-closed comprobaba cookies de navegador con
    `_load_cookies()` / `STATE_FILE`, dos nombres que ya NO existen en este fichero (quedaron
    de la época de agent-browser) → `--digest` rompía con NameError y el digest de los guardados
    llevaba caído. La fuente primaria es `xurl` (OAuth cacheado en ~/.xurl, ver `_xurl.py`), la
    misma que usa `cmd_fetch`, así que la puerta se comprueba contra ella.
    """
    store = _load_store()
    if not _xurl.disponible():
        dias = _dias_desde_ultima_cosecha(store)
        antig = ("la última cosecha es de hace %d día(s)" % dias) if dias is not None \
            else "no hay ninguna cosecha previa"
        print("⚠️  NO PUDE MIRARLO — falta `xurl`, la vía de lectura de tus GUARDADOS.\n"
              "    Esto NO es «sin novedad»: los GUARDADOS de {{TITULAR}} llevan sin leerse y %s.\n"
              "    Su señal ya pre-filtrada se está perdiendo. Hace falta `brew install xurl` y\n"
              "    `xurl auth` (gate suyo) — escálalo como «⏸️ NECESITO DE TI»." % antig)
        return 2

    if not store:
        print("(sin guardados cosechados todavía: corre `python3 tools/x_guardados.py fetch`)")
        return 0

    dias = _dias_desde_ultima_cosecha(store)
    if dias is not None and dias > DIAS_RANCIO:
        print("⚠️  Se puede leer X, pero la última cosecha es de hace %d día(s): `fetch` no está\n"
              "    corriendo. Lo que sigue es CACHÉ RANCIO, no la foto de hoy.\n" % dias)

    recs = list(store.values())
    if since_days is not None:
        cutoff = (datetime.date.today() - datetime.timedelta(days=since_days)).isoformat()
        recs = [r for r in recs if r.get("fetched", "") >= cutoff]
    else:
        today = datetime.date.today().isoformat()
        recs = [r for r in recs if r.get("fetched", "") == today]
        if not recs:  # nada hoy: cae a los 7 últimos días para no salir vacío
            cutoff = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
            recs = [r for r in store.values() if r.get("fetched", "") >= cutoff]

    recs.sort(key=lambda r: (("NED" not in r.get("tags", [])), -r.get("ts", 0)))
    ventana = "últimos %d días" % since_days if since_days is not None else "última cosecha"
    print("# Guardados de X de {{TITULAR}} — cosecha para auto-mejora (%s)\n" % ventana)
    print("> Fuente: bookmarks privados de @titular (solo lectura). Etiqueta = filtro "
          "determinista; el triaje fino lo haces tú (auto-mejora): ¿esto acerca a NED o "
          "mejora el sistema? Lo que no, se descarta.\n")
    if not recs:
        print("_(sin guardados nuevos en la ventana)_")
        return 0
    for r in recs:
        tag = " ".join("[%s]" % t for t in r.get("tags", [])) or "[otro]"
        when = r.get("iso", "")[:10]
        who = "@" + r["handle"] if r["handle"] else (r.get("name") or "?")
        txt = (r.get("text", "") or "").replace("\n", " ").strip()
        if len(txt) > 400:
            txt = txt[:400] + "…"
        print("- %s **%s** · %s\n  %s\n  %s" % (tag, who, when, txt, r["url"]))
        # Lo que hay DETRÁS del enlace, que es donde vivía la señal que se perdía.
        if r.get("articulo_titulo"):
            print("  📄 «%s»" % r["articulo_titulo"])
        cuerpo = (r.get("cuerpo") or "").replace("\n", " ").strip()
        if cuerpo:
            print("  ↳ %s%s" % (cuerpo[:700], "…" if len(cuerpo) > 700 else ""))
        cita = (r.get("cita_texto") or "").replace("\n", " ").strip()
        if cita:
            print("  ↳ cita: %s%s" % (cita[:400], "…" if len(cita) > 400 else ""))
        for e in (r.get("enlaces") or [])[:4]:
            print("  🔗 %s%s" % (e.get("url", ""), (" — " + e["titulo"]) if e.get("titulo") else ""))
        if r.get("enriquecido") is False:
            print("  ⚠️  contenido NO leído (fallo de lectura, reintentable) — no es «sin contenido»")
    print("\n_%d guardados en la ventana._" % len(recs))
    return 0


FLAGS_CONOCIDOS = {"--notify", "--all", "--max", "--pages", "--since", "--digest"}


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    # 19-sep-2026: `--help` caía al fetch por defecto y gastaba una lectura de la API de X.
    # Ayuda o flag desconocido → se responde SIN tocar la API.
    if "-h" in args or "--help" in args:
        print(__doc__)
        return 0
    raros = [a for a in args if a.startswith("-") and a not in FLAGS_CONOCIDOS]
    if raros:
        print("Flag no reconocido: %s → usa --help" % " ".join(raros))
        return 1
    notify = "--notify" in args
    show_all = "--all" in args
    max_results = MAX_RESULTS
    if "--max" in args:
        i = args.index("--max"); max_results = int(args[i + 1]); args = args[:i] + args[i + 2:]
    if "--pages" in args:  # retro-compat: la API v2 pagina distinto; se traduce a --max si no venía
        i = args.index("--pages"); pages = int(args[i + 1]); args = args[:i] + args[i + 2:]
        max_results = min(100, max(max_results, pages * 20))
    since = None
    if "--since" in args:
        i = args.index("--since"); since = int(args[i + 1]); args = args[:i] + args[i + 2:]

    cmd = "fetch"
    if "--digest" in args or "listar" in args:
        cmd = "listar"
    elif "fetch" in args:
        cmd = "fetch"
    elif args and not args[0].startswith("-"):
        print("Comando no reconocido: %s → usa: fetch | listar (--digest)" % args[0])
        return 1

    if cmd == "listar":
        return cmd_listar(since)
    return cmd_fetch(notify, show_all, max_results)


if __name__ == "__main__":
    sys.exit(main())
