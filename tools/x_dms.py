#!/usr/bin/env python3
"""x_dms.py — lector SUAVE de DMs de X (solo lectura).

Fuente PRIMARIA: `xurl` (X API v2, OAuth cacheado en ~/.xurl) vía `tools/_xurl.py`
— endpoint `dms` (1 lectura) + `resolve_users` (1 lectura extra SOLO para los
remitentes nuevos que aún no tengamos en caché — dedup agresivo, ver `_cache_handle`).
Coste aprox. por pasada: ~0,001-0,002 $ propio / ~0,005-0,01 $ general (1-2 lecturas).

Reemplaza al viejo scraping de cookies web (`~/.agent-browser/x_state.json` +
`dm/inbox_initial_state.json`), más frágil y sin API oficial detrás. Mismo
aterrizaje que antes (`_cajita/x_dms/`, mismos ficheros `seen.json`/`digest-*.md`/
`raw-*.json`/`watch-*.json`) para no romper lo que ya consume Vega/auto-mejora.

Fallback: si `xurl` falla (no instalado / token caducado / sin crédito), avisa
claro y NO rompe — no hay carril Grok para DMs (son privados, Grok no los ve).

NUNCA responde ni escribe nada en X. Los DMs crudos se quedan en `_cajita/x_dms/`
(gitignored, sin PII en el repo). El triaje SEMÁNTICO profundo lo hace el agente
`x-inbox` sobre el `raw-*.json` que deja este tool; aquí solo flag determinista.

Uso:
  python3 tools/x_dms.py            # lee, tría, deja digest en _cajita/x_dms/
  python3 tools/x_dms.py --notify   # además avisa a {{TITULAR}} por Telegram si hay algo nuevo
  python3 tools/x_dms.py --all      # ignora el estado y vuelca todo lo reciente (debug)
  python3 tools/x_dms.py --watch alguien --notify   # vigilancia focalizada de una persona
  python3 tools/x_dms.py --max 50   # nº de eventos a pedir (1-100, def. 30)
"""
import json, os, sys, time, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)  # para importar salida y _xurl
import _xurl  # noqa: E402

OUT_DIR = os.path.join(ROOT, "_cajita", "x_dms")
SEEN_FILE = os.path.join(OUT_DIR, "seen.json")
HANDLE_CACHE_FILE = os.path.join(OUT_DIR, "handle_cache.json")
MAX_RESULTS = 30

# Red de palabras clave para marcar posibles leads hacia NED/la vacuna (ES+EN).
LEAD_KW = [
    "oncolog", "cancer", "cáncer", "tumor", "mama", "breast", "metast",
    "ensayo", "trial", "vacuna", "vaccine", "neoantig", "neoantíg", "inmun",
    "immun", "laborator", "lab ", " lab", "investiga", "research", "científic",
    "scientist", "doctor", "dr.", "dra.", "médic", "medic", "hospital",
    "clínic", "clinic", "universi", "biotech", "pharma", "farma", "periodist",
    "journalist", "prensa", "press", "reporter", "entrevista", "interview",
    "colabora", "collaborat", "fundación", "foundation", "donac", "grant",
    "beca", "ned", "terapia", "therapy", "protocolo", "biopsia", "biopsy",
    "genóm", "genom", "secuenc", "sequenc", "immunotherap", "inmunoterap",
]


def _flag(text):
    t = (text or "").lower()
    return sorted({k.strip() for k in LEAD_KW if k in t})


def _load_handle_cache():
    if os.path.exists(HANDLE_CACHE_FILE):
        try:
            return json.load(open(HANDLE_CACHE_FILE))
        except Exception:
            pass
    return {}


def _save_handle_cache(cache):
    os.makedirs(OUT_DIR, exist_ok=True)
    json.dump(cache, open(HANDLE_CACHE_FILE, "w"))


def _resolve_handles(sender_ids, my_id):
    """Resuelve sender_id -> {'username','name'}, cacheado en disco. Solo pide a la
    API los ids que faltan en caché (frugal: 1 lectura batched como mucho)."""
    cache = _load_handle_cache()
    missing = [i for i in dict.fromkeys(sender_ids) if i and i != my_id and i not in cache]
    if missing:
        try:
            fresh = _xurl.resolve_users(missing)
            cache.update(fresh)
            _save_handle_cache(cache)
        except _xurl.XurlError:
            pass  # sin handle, seguimos con from_id crudo
    return cache


def _ts_ms(iso):
    if not iso:
        return 0
    try:
        return int(datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return 0


def _alert_fallo(notify, motivo):
    msg = ("⚠️ El vigía de DMs de X no pudo leer (%s). Revisa `xurl auth` / crédito. "
           "No invento datos; reintento en la próxima pasada." % motivo)
    print(msg)
    if notify:
        try:
            import salida
            salida.report_to_titular(msg)
        except Exception:
            pass


def _fetch(max_results):
    """1 lectura a xurl dms + resolución de handles. Devuelve lista de mensajes
    normalizados {conv,id,from_id,handle,name,text,ts,mine} o levanta XurlError."""
    who = _xurl.whoami()
    my_id = str(who.get("id", ""))
    raw = _xurl.dms(max_results=max_results)
    cache = _resolve_handles([m["sender_id"] for m in raw], my_id)
    out = []
    for m in raw:
        sid = m["sender_id"]
        u = cache.get(sid, {})
        out.append({
            "conv": m["conversation_id"],
            "id": m["id"],
            "from_id": sid,
            "handle": u.get("username", ""),
            "name": u.get("name", ""),
            "text": m["text"],
            "ts": _ts_ms(m["created_at"]),
            "mine": (sid == my_id),
        })
    return out


def _do_watch(handle, notify, max_results):
    """Vigilancia FOCALIZADA de una persona: avisa si te escribe (dentro de la
    ventana pedida — la API v2 de DMs no soporta filtrar por conversación, así que
    miramos los últimos N eventos y filtramos por handle)."""
    if 2 <= datetime.datetime.now().hour < 8:
        print("(horas tranquilas, no consulto)")
        return 0
    try:
        msgs = _fetch(max_results)
    except _xurl.XurlError as e:
        _alert_fallo(notify, str(e))
        return 3
    theirs = [m for m in msgs if m["handle"].lower() == handle and not m["mine"]]

    os.makedirs(OUT_DIR, exist_ok=True)
    wf = os.path.join(OUT_DIR, "watch-%s.json" % handle)
    seen = set()
    if os.path.exists(wf):
        try:
            seen = set(json.load(open(wf)).get("ids", []))
        except Exception:
            seen = set()
    first = not os.path.exists(wf)
    new = [m for m in theirs if m["id"] not in seen]
    json.dump({"ids": sorted(m["id"] for m in theirs), "updated": int(time.time())}, open(wf, "w"))

    if first:
        print("👁️  vigía @%s armado (línea base: %d mensajes suyos previos)" % (handle, len(theirs)))
        return 0
    if not new:
        print("@%s: sin mensajes nuevos" % handle)
        return 0

    for m in sorted(new, key=lambda x: x["ts"]):
        when = datetime.datetime.fromtimestamp(m["ts"] / 1000).strftime("%d/%m %H:%M") if m["ts"] else ""
        print("🔔 NUEVO de @%s %s: %s" % (handle, when, m["text"][:300]))
    if notify:
        try:
            import salida
            body = "🔔🔔 ¡@%s te ha escrito por DM en X!\n\n" % handle
            for m in sorted(new, key=lambda x: x["ts"]):
                body += "«%s»\n\n" % (m["text"][:600] or "(sin texto)")
            body += "(respondes TÚ desde X; yo solo te aviso)"
            salida.report_to_titular(body)
            print("[avisado por Telegram]")
        except Exception as e:
            print("[notify] no se pudo enviar: %s" % e, file=sys.stderr)
    return 0


def main():
    args = sys.argv[1:]
    notify = "--notify" in args
    show_all = "--all" in args
    max_results = MAX_RESULTS
    if "--max" in args:
        i = args.index("--max"); max_results = int(args[i + 1]); args = args[:i] + args[i + 2:]
    watch = None
    for i, a in enumerate(args):
        if a == "--watch" and i + 1 < len(args):
            watch = args[i + 1].lstrip("@").lower()

    if watch:
        return _do_watch(watch, notify, max_results)

    try:
        msgs = _fetch(max_results)
    except _xurl.XurlError as e:
        _alert_fallo(notify, str(e))
        return 3 if "caduc" in str(e).lower() or "credential" in str(e).lower() else 2

    # dedup por id
    uniq = {}
    for m in msgs:
        if m["id"]:
            uniq[m["id"]] = m
    msgs = sorted(uniq.values(), key=lambda x: x["ts"])

    os.makedirs(OUT_DIR, exist_ok=True)
    seen = set()
    if os.path.exists(SEEN_FILE) and not show_all:
        try:
            seen = set(json.load(open(SEEN_FILE)).get("ids", []))
        except Exception:
            seen = set()
    first_run = not os.path.exists(SEEN_FILE)

    # mensajes nuevos = no vistos y NO míos (los míos son lo que mandé yo)
    new = [m for m in msgs if m["id"] not in seen and not m["mine"]]
    leads = [(m, _flag(m["text"])) for m in new]
    leads = [(m, f) for m, f in leads if f]

    # actualiza estado (marca TODO lo visto como visto, incl. lo mío)
    json.dump({"ids": sorted(uniq.keys()), "updated": int(time.time())}, open(SEEN_FILE, "w"))

    today = datetime.date.today().isoformat()
    # raw para el agente x-inbox (deep-triage semántico)
    json.dump({"fetched": today, "new": new}, open(os.path.join(OUT_DIR, "raw-%s.json" % today), "w"),
              ensure_ascii=False, indent=1)

    def line(m):
        who = ("@" + m["handle"]) if m["handle"] else m["name"] or m["from_id"]
        when = datetime.datetime.fromtimestamp(m["ts"] / 1000).strftime("%d/%m %H:%M") if m["ts"] else ""
        return "- **%s** %s\n  %s" % (who, when, (m["text"][:280] or "(sin texto)").replace("\n", " "))

    md = ["# DMs de X — %s" % today, ""]
    if first_run:
        md.append("_(primera pasada: establezco la línea base; a partir de ahora solo te muestro lo nuevo)_\n")
    md.append("**mensajes nuevos:** %d · **posibles leads:** %d\n" % (len(new), len(leads)))
    if leads:
        md.append("## 🎯 Posibles leads hacia la vacuna/NED")
        for m, f in sorted(leads, key=lambda x: -x[0]["ts"]):
            md.append(line(m) + "\n  _señales: %s_" % ", ".join(f))
        md.append("")
    if new:
        md.append("## 📨 Resto de DMs nuevos")
        for m in sorted(new, key=lambda x: -x["ts"]):
            if any(m["id"] == lm["id"] for lm, _ in leads):
                continue
            md.append(line(m))
    if not new and not first_run:
        md.append("_Sin DMs nuevos desde la última vez._")
    digest = "\n".join(md)
    open(os.path.join(OUT_DIR, "digest-%s.md" % today), "w").write(digest)

    print(digest[:1500])
    print("\n[guardado en %s]" % OUT_DIR)

    if notify and (leads or (new and not first_run)):
        try:
            import salida
            head = "🐦 DMs de X: %d nuevos" % len(new)
            if leads:
                head += " · %d posibles leads 🎯" % len(leads)
            body = head + "\n\n" + "\n".join(
                "• %s%s" % (("@" + m["handle"]) if m["handle"] else m["name"],
                           " 🎯" if any(m["id"] == lm["id"] for lm, _ in leads) else "")
                for m in sorted(new, key=lambda x: -x["ts"])[:12])
            body += "\n\nDigest completo: _cajita/x_dms/digest-%s.md" % today
            salida.report_to_titular(body)
            print("[avisado por Telegram]")
        except Exception as e:
            print("[notify] no se pudo enviar: %s" % e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
