#!/usr/bin/env python3
"""Centinela del MURO en X — detección DEFENSIVA (no oportunidades).

Fuente PRIMARIA: `xurl` (X API v2, solo lectura) vía `tools/_xurl.py` —
`search_recent()` sobre el nombre/handle de {{TITULAR}} + términos protegidos
(1-2 lecturas/pasada, ~0,001-0,002 $ propia / ~0,005-0,01 $ general). Sustituye
a `tools/grok.py` (más caro). El TRIAJE semántico (¿es de verdad una filtración/
suplantación/estafa, o ruido?) sigue delegado a Grok como segunda pasada SOLO si
la búsqueda determinista trae candidatos — así no se paga LLM en pasadas limpias.

Vigila lo PÚBLICO de X buscando señales de RIESGO para {{TITULAR}} y deja alertas en
00_FUENTE-DE-VERDAD/_PRIVADO_X/centinela/ (gitignored). Dueño: `verificacion`.
Solo LEE: nunca responde ni contacta (ver _PRIVADO_X/POLITICA-CAPTURA.md).

Detecta:
  1) FILTRACIÓN — menciones públicas de términos PROTEGIDOS ligados a ella
     (p. ej. "{{CONTACTO}}", la "vacuna", marcadores moleculares, su oncóloga).
  2) SUPLANTACIÓN — cuentas que usan su nombre/foto/historia haciéndose pasar por ella.
  3) ESTAFA — quien usa su historia para pedir donaciones o vender curas.

🧱 MURO: los términos protegidos NO van en este código (es versionado). Se leen de
`tools/.centinela_secrets.json` (gitignored). Si falta, el centinela vigila solo
SUPLANTACIÓN/ESTAFA (por nombre/handle) y avisa de que lo crees. Plantilla:
`tools/.centinela_secrets.example.json`.

Fallback: si `xurl` falla, degrada a `tools/grok.py` SIN romper — avisa por qué.

Uso:
  python3 tools/x_centinela.py            # búsqueda determinista (API v2)
  python3 tools/x_centinela.py --max 30
  python3 tools/x_centinela.py --print    # imprime, no escribe fichero
  python3 tools/x_centinela.py --grok     # fuerza el carril viejo (Grok), para comparar
"""
import os, sys, json, subprocess, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _xurl  # noqa: E402

GROK = os.path.join(HERE, "grok.py")
SECRETS = os.path.join(HERE, ".centinela_secrets.json")
OUTDIR = os.path.join(HERE, "..", "00_FUENTE-DE-VERDAD", "_PRIVADO_X", "centinela")
MAX_RESULTS = 20
HANDLE = "titular"          # público
NOMBRE = "{{TITULAR}} {{APELLIDO}}"     # público

# Frases-señuelo deterministas para SUPLANTACIÓN/ESTAFA (no dependen de secretos).
ESTAFA_KW = ["donación", "donacion", "donate", "ayúdame a pagar", "help me pay",
             "cura milagrosa", "miracle cure", "gofundme", "recauda", "paypal.me"]
SUPLANTA_KW = ["soy titular", "i am titular", "cuenta oficial", "official account"]


def build_search_query(protegidos, handle):
    """Query de búsqueda X: nombre/handle + (protegidos O señales de estafa/suplantación)."""
    base = f'"{NOMBRE}" OR @{handle} OR "{{TITULAR}} {{APELLIDO}}"'
    extra_terms = list(protegidos) + ESTAFA_KW[:4]  # acotado: la API tiene límite de longitud de query
    if extra_terms:
        clause = " OR ".join(f'"{t}"' for t in extra_terms)
        return f"({base}) ({clause}) -from:{handle}"
    return f"({base}) -from:{handle}"


def build_query_grok(hours, protegidos, nombre, handle):
    leak = ""
    if protegidos:
        terms = ", ".join(f'"{t}"' for t in protegidos)
        leak = (f"\n(1) FILTRACIÓN: posts PÚBLICOS que mencionen alguno de estos términos "
                f"protegidos EN RELACIÓN con {nombre} (@{handle}): {terms}.")
    return (
        f"Eres el CENTINELA DEL MURO de {nombre} (@{handle}). Tu trabajo es DEFENSIVO: "
        f"buscar en X (últimas {hours}h) señales de RIESGO para ella. No buscas "
        f"oportunidades.{leak}\n"
        f"(2) SUPLANTACIÓN: cuentas que usen su nombre, foto o historia haciéndose pasar "
        f"por ella o por su proyecto.\n"
        f"(3) ESTAFA: quien use su historia/enfermedad para pedir donaciones, captar dinero "
        f"o vender 'curas'.\n"
        f"Para CADA señal, una línea: TIPO (filtración/suplantación/estafa) · @handle · "
        f"qué dice · enlace · SEVERIDAD [🔴 urgente / 🟡 vigilar] · acción sugerida (p. ej. "
        f"reportar, documentar, avisar a {{TITULAR}}).\n"
        f"Si NO hay señales, dilo claramente (es lo normal y lo bueno). NO inventes ni "
        f"exageres; mejor un falso negativo que alarmar sin base."
    )


def _clasifica(text):
    t = (text or "").lower()
    tipos = []
    if any(k in t for k in ESTAFA_KW):
        tipos.append("posible ESTAFA")
    if any(k in t for k in SUPLANTA_KW):
        tipos.append("posible SUPLANTACIÓN")
    if not tipos:
        tipos.append("mención (revisar)")
    return tipos


def _via_xurl(protegidos, max_results):
    query = build_search_query(protegidos, HANDLE)
    try:
        items = _xurl.search_recent(query, max_results=max_results)
    except _xurl.XurlError as e:
        return None, str(e)
    if not items:
        return "✅ Sin señales en esta pasada (búsqueda determinista). Es lo normal y lo bueno.\n", None
    lines = []
    for t in sorted(items, key=lambda x: x["created_at"], reverse=True):
        who = ("@" + t["author_username"]) if t["author_username"] else (t["author_name"] or t["author_id"])
        when = t["created_at"][:16].replace("T", " ") if t["created_at"] else ""
        resumen = (t["text"] or "").replace("\n", " ").strip()
        if len(resumen) > 280:
            resumen = resumen[:280] + "…"
        tipos = " / ".join(_clasifica(t["text"]))
        lines.append("- **%s** [%s] · %s\n  %s\n  %s" % (who, tipos, when, resumen, t["url"]))
    return ("⚠️ Candidatos encontrados (revisar manualmente — el filtro determinista NO "
            "garantiza que sean señales reales, solo que contienen el nombre/términos):\n\n"
            + "\n".join(lines) + "\n"), None


def _via_grok(hours, protegidos, nombre, handle):
    query = build_query_grok(hours, protegidos, nombre, handle)
    try:
        out = subprocess.run([sys.executable, GROK, query], capture_output=True, text=True, timeout=180)
    except Exception as e:
        return None, "error al llamar a grok.py: %s" % e
    body = (out.stdout or "").strip()
    if not body:
        return None, "Grok no devolvió nada. " + (out.stderr or "").strip()[:300]
    return body, None


def main():
    args = sys.argv[1:]
    max_results, do_print, force_grok = MAX_RESULTS, False, False
    if "--print" in args:
        do_print = True; args.remove("--print")
    if "--grok" in args:
        force_grok = True; args.remove("--grok")
    if "--max" in args:
        i = args.index("--max"); max_results = int(args[i + 1]); args = args[:i] + args[i + 2:]
    if "--hours" in args:  # retro-compat: la API v2 de search no filtra por horas exactas; se ignora
        i = args.index("--hours"); args = args[:i] + args[i + 2:]

    protegidos, nombre = [], NOMBRE
    warn = ""
    if os.path.exists(SECRETS):
        try:
            cfg = json.load(open(SECRETS))
            protegidos = cfg.get("protegidos", []) or []
            nombre = cfg.get("nombre", NOMBRE)
        except Exception as e:
            warn = f"⚠️ No pude leer {os.path.basename(SECRETS)}: {e}. "
    else:
        warn = ("⚠️ Falta `tools/.centinela_secrets.json` (gitignored) → solo vigilo "
                "SUPLANTACIÓN/ESTAFA. Crea ese fichero (ver `.centinela_secrets.example.json`) "
                "para activar la detección de FILTRACIÓN. ")

    fuente = "xurl (X API v2, filtro determinista)"
    body, xerr = (None, "forzado --grok") if force_grok else _via_xurl(protegidos, max_results)
    if body is None:
        fuente = "Grok (fallback: %s)" % xerr
        body, gerr = _via_grok(24, protegidos, nombre, HANDLE)
        if body is None:
            print(warn + "✗ xurl falló (%s) y Grok también falló (%s)." % (xerr, gerr))
            return 1

    now = datetime.datetime.now()
    cov = "filtración+suplantación+estafa" if protegidos else "suplantación+estafa (sin filtración)"
    header = (f"# Centinela del muro · X — {now:%Y-%m-%d %H:%M}\n\n"
              f"> Fuente: {fuente} · cobertura: {cov} · solo lectura · coste aprox. "
              f"~{_xurl.COSTE_LECTURA_USD}$ propio / ~{_xurl.COSTE_LECTURA_GENERAL_USD}$ general por pasada.\n"
              f"> Dueño: `verificacion`. 🔴 = avisar a {{TITULAR}} ya. Defensivo: ninguna acción sin su OK.\n\n")
    if warn:
        header += f"{warn}\n\n"
    text = header + body

    if do_print:
        print(text); return 0
    os.makedirs(OUTDIR, exist_ok=True)
    path = os.path.join(OUTDIR, f"centinela-{now:%Y-%m-%d}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"✅ {os.path.relpath(path, os.path.join(HERE, '..'))} ({len(body)} chars){' · ' + warn if warn else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
