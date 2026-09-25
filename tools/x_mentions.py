#!/usr/bin/env python3
"""Minador de MENCIONES PÚBLICAS de X para {{TITULAR}} (@titular).

Fuente PRIMARIA: `xurl` (X API v2, OAuth cacheado, solo lectura) vía `tools/_xurl.py`
— endpoint `mentions` (~1 lectura/pasada, ~0,001 $ propia / ~0,005 $ general, Spend
Cap de X). Tría por relevancia hacia la vacuna (médico/lab/ensayo/prensa/lead) con
palabras clave deterministas y vuelca un log datado en
00_FUENTE-DE-VERDAD/_PRIVADO_X/mentions/ (gitignored).

Fallback: si `xurl` falla (no instalado, token caducado, sin crédito), degrada a
`tools/grok.py` (búsqueda en vivo, más cara) SIN romper — avisa por qué degradó.

Solo LEE y archiva. No responde, no publica, no contacta (ver POLITICA-CAPTURA.md).

Uso:
  python3 tools/x_mentions.py                 # menciones recientes (API v2, hasta 100)
  python3 tools/x_mentions.py --max 30        # nº de menciones a pedir (5-100)
  python3 tools/x_mentions.py --handle otra   # otra cuenta (def. titular; solo afecta encabezado)
  python3 tools/x_mentions.py --print         # solo imprime, no escribe fichero
  python3 tools/x_mentions.py --grok          # fuerza el carril viejo (Grok), para comparar
"""
import os, sys, subprocess, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _xurl  # noqa: E402

HANDLE = "titular"          # cuenta de {{TITULAR}} en X
MAX_RESULTS = 20                # nº de menciones a pedir por pasada (frugal por defecto)
GROK = os.path.join(HERE, "grok.py")
OUTDIR = os.path.join(HERE, "..", "00_FUENTE-DE-VERDAD", "_PRIVADO_X", "mentions")

# Palabras clave deterministas para el triaje (mismo espíritu que x_dms.py/x_guardados.py).
KW_MEDICO = ["oncolog", "inmunolog", "laborator", "lab ", " lab", "investiga", "research",
             "científic", "scientist", "doctor", "dr.", "dra.", "médic", "medic", "hospital",
             "clínic", "clinic", "universi", "biotech", "pharma", "farma", "ensayo", "trial",
             "vacuna", "vaccine", "neoantig", "neoantíg", "inmun", "immun", "genóm", "genom"]
KW_PRENSA = ["periodist", "journalist", "prensa", "press", "reporter", "entrevista", "interview"]
KW_LEAD = ["colabora", "collaborat", "fundación", "foundation", "donac", "grant", "beca", "ayuda"]

PROMPT_GROK = (
    "Eres el minador de menciones de X de la cuenta @{handle}. Busca en X las "
    "MENCIONES PÚBLICAS recientes a @{handle} de las últimas {hours} horas: "
    "respuestas, citas y posts que la nombren o hablen de su caso/proyecto.\n"
    "Para CADA mención relevante devuelve una línea con:\n"
    "  • autor (@handle) · fecha · resumen en 1 frase · enlace\n"
    "  • ETIQUETA: [MEDICO] si parece oncólogo/lab/genómica/gestor de ensayo; "
    "[PRENSA] si parece periodista/medio; [LEAD] si ofrece un contacto o recurso "
    "útil; [PERSONAL] si es apoyo/charla.\n"
    "Ordena primero [MEDICO] y [LEAD], luego [PRENSA], luego [PERSONAL].\n"
    "NO inventes: si no encuentras menciones, dilo claramente. No incluyas datos "
    "clínicos del titular de la cuenta en tu salida."
)


def _flag(text):
    t = (text or "").lower()
    tags = []
    if any(k in t for k in KW_MEDICO):
        tags.append("MEDICO")
    if any(k in t for k in KW_LEAD):
        tags.append("LEAD")
    if any(k in t for k in KW_PRENSA):
        tags.append("PRENSA")
    if not tags:
        tags.append("PERSONAL")
    return tags


def _via_xurl(max_results):
    """Devuelve (texto_markdown, None) o (None, motivo_error)."""
    try:
        items = _xurl.mentions(max_results=max_results)
    except _xurl.XurlError as e:
        return None, str(e)
    if not items:
        return "_(sin menciones nuevas en esta pasada)_\n", None

    def order(m):
        tags = m["_tags"]
        pri = 0 if ("MEDICO" in tags or "LEAD" in tags) else (1 if "PRENSA" in tags else 2)
        return (pri, -_ts(m["created_at"]))

    for m in items:
        m["_tags"] = _flag(m["text"])
    items.sort(key=order)

    lines = []
    for m in items:
        who = ("@" + m["author_username"]) if m["author_username"] else (m["author_name"] or m["author_id"])
        when = m["created_at"][:16].replace("T", " ") if m["created_at"] else ""
        tag = " ".join("[%s]" % t for t in m["_tags"])
        resumen = (m["text"] or "").replace("\n", " ").strip()
        if len(resumen) > 280:
            resumen = resumen[:280] + "…"
        lines.append("- %s **%s** · %s\n  %s\n  %s" % (tag, who, when, resumen, m["url"]))
    return "\n".join(lines) + "\n", None


def _ts(iso):
    if not iso:
        return 0
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0


def _via_grok(handle, hours):
    query = PROMPT_GROK.format(handle=handle, hours=hours)
    try:
        out = subprocess.run([sys.executable, GROK, query], capture_output=True, text=True, timeout=180)
    except Exception as e:
        return None, "Error al llamar a grok.py: %s" % e
    body = (out.stdout or "").strip()
    err = (out.stderr or "").strip()
    if not body:
        return None, "Grok no devolvió nada. " + err
    return body, None


def main():
    args = sys.argv[1:]
    handle, max_results, do_print, force_grok = HANDLE, MAX_RESULTS, False, False
    if "--print" in args:
        do_print = True; args.remove("--print")
    if "--grok" in args:
        force_grok = True; args.remove("--grok")
    if "--handle" in args:
        i = args.index("--handle"); handle = args[i + 1].lstrip("@"); args = args[:i] + args[i + 2:]
    if "--max" in args:
        i = args.index("--max"); max_results = int(args[i + 1]); args = args[:i] + args[i + 2:]
    if "--hours" in args:  # retro-compat: ya no filtra por horas (la API v2 no lo da igual), se ignora
        i = args.index("--hours"); args = args[:i] + args[i + 2:]

    fuente = "xurl (X API v2)"
    body, xerr = (None, "forzado --grok") if force_grok else _via_xurl(max_results)
    if body is None:
        fuente = "Grok (fallback: %s)" % xerr
        body, gerr = _via_grok(handle, hours=48)
        if body is None:
            print("✗ xurl falló (%s) y Grok también falló (%s)." % (xerr, gerr))
            return 1

    now = datetime.datetime.now()
    header = (f"# Menciones de X a @{handle} — {now:%Y-%m-%d %H:%M}\n\n"
              f"> Fuente: {fuente} · solo lectura · coste aprox. ~{_xurl.COSTE_LECTURA_USD}$ propio "
              f"/ ~{_xurl.COSTE_LECTURA_GENERAL_USD}$ general por pasada.\n"
              f"> Triaje: [MEDICO] / [LEAD] = revisar primero. Derivar a comite-medico / prensa.\n\n")
    text = header + body

    if do_print:
        print(text); return 0

    os.makedirs(OUTDIR, exist_ok=True)
    path = os.path.join(OUTDIR, f"mentions-{now:%Y-%m-%d}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"✅ {os.path.relpath(path, os.path.join(HERE, '..'))} ({len(body)} chars) · fuente: {fuente}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
