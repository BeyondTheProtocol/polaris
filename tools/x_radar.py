#!/usr/bin/env python3
"""Radar de X hacia la vacuna (5 modos), sobre `xurl` (X API v2, solo lectura).

Fuente PRIMARIA: `tools/_xurl.py` → `search_recent(query)` — 1 lectura por modo
(~0,001 $ propia / ~0,005 $ general, Spend Cap de X). Sustituye a `tools/grok.py`
(más caro, sin API oficial detrás). Vigila lo PÚBLICO que acerca a {{TITULAR}} a un
tratamiento personalizado, y lo TRÍA en un log datado dentro de
00_FUENTE-DE-VERDAD/_PRIVADO_X/radar/ (gitignored). Solo LEE: nunca publica ni
contacta (ver _PRIVADO_X/POLITICA-CAPTURA.md).

Modos (dueño que lo explota):
  expertos      → oncólogos/inmunólogos/labs de vacunas personalizadas   [comite-medico]
  ensayos       → ensayos clínicos + congresos (#ASCO/#AACR/#ESMO/#SITC)  [comite-medico]
  prensa        → periodistas/medios de cáncer·IA·enfermedad rara         [prensa]
  financiacion  → becas/fundaciones/ayudas a pacientes                    [finanzas-transparencia]
  comunidad     → pacientes con casos parecidos, navegadores, voluntarios [comunidad]

Fallback: si `xurl` falla (no instalado / token caducado / sin crédito), degrada
a `tools/grok.py` (búsqueda en vivo por LLM) SIN romper — avisa por qué degradó.

Uso:
  python3 tools/x_radar.py                 # los 5 modos (búsqueda API v2)
  python3 tools/x_radar.py expertos        # un modo
  python3 tools/x_radar.py ensayos --max 30
  python3 tools/x_radar.py prensa --print  # imprime, no escribe fichero
  python3 tools/x_radar.py --grok          # fuerza el carril viejo (Grok), para comparar

Las WATCHLISTS de handles empiezan vacías a propósito: las pueblan comité-medico
(expertos/labs) y prensa (periodistas) a medida que identifican cuentas reales.
La búsqueda por temas/hashtags funciona aunque la watchlist esté vacía.
"""
import os, sys, subprocess, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _xurl  # noqa: E402

GROK = os.path.join(HERE, "grok.py")
OUTDIR = os.path.join(HERE, "..", "00_FUENTE-DE-VERDAD", "_PRIVADO_X", "radar")
MAX_RESULTS = 20  # nº de resultados a pedir por modo (frugal por defecto; API mín. 10)

# Cada modo: query de búsqueda X (operadores nativos) + prompt de fallback Grok.
MODES = {
    "expertos": {
        "owner": "comite-medico",
        "handles": [],   # POBLAR (comité-medico): @handles reales de oncólogos/inmunólogos/labs
        "query": '(oncólogo OR oncologist OR inmunólogo OR immunologist OR "vacuna personalizada" '
                 'OR "personalized vaccine" OR neoantígeno OR neoantigen) (cáncer OR cancer OR tumor) -is:retweet lang:es OR lang:en',
        "prompt": (
            "Eres el radar de EXPERTOS de X para el caso de {{TITULAR}} (ingeniera con "
            "{{DIAGNOSTICO}} que busca un tratamiento personalizado). "
            "Busca en X posts RECIENTES (últimas {hours}h) de oncólogos, inmunólogos, "
            "investigadores y labs sobre {campo}.{handles}\n"
            "Para CADA hallazgo relevante, una línea: autor (@handle + quién es si se "
            "sabe) · fecha · resumen en 1 frase · enlace · ETIQUETA "
            "[EXPERTO/LAB] / [PAPER] / [ENSAYO] / [LEAD].\n"
            "Prioriza a quien podría **diseñar o referir** un tratamiento personalizado. "
            "Ordena [EXPERTO/LAB] y [LEAD] primero. NO inventes; si no hay nada, dilo. "
            "No incluyas datos clínicos privados de {{TITULAR}}."
        ),
    },
    "ensayos": {
        "owner": "comite-medico",
        "handles": [],
        "query": '(ensayo clínico OR clinical trial OR #ASCO OR #AACR OR #ESMO OR #SITC) '
                  '(cáncer OR cancer OR tumor OR "breast cancer" OR "{{DIAGNOSTICO}}") -is:retweet',
        "prompt": (
            "Eres el radar de ENSAYOS y CONGRESOS de X. Busca en X (últimas {hours}h) "
            "anuncios de **ensayos clínicos** (reclutamiento o resultados) y posts de "
            "**congresos** (#ASCO #AACR #ESMO #SITC) sobre {campo}.{handles}\n"
            "Para CADA uno, una línea: autor · fecha · resumen · enlace (y código NCT si "
            "aparece) · ETIQUETA [ENSAYO] / [CONGRESO] / [EXPERTO].\n"
            "Marca los que parezcan cercanos al caso para **cruzar con BioMCP**. "
            "NO inventes; si no hay nada, dilo."
        ),
    },
    "prensa": {
        "owner": "prensa",
        "handles": [],   # POBLAR (prensa): @handles de periodistas/medios objetivo
        "query": '(periodista OR journalist OR reportero) (cáncer OR cancer OR "IA en salud" '
                  'OR "AI in health" OR "enfermedad rara" OR "rare disease") -is:retweet',
        "prompt": (
            "Eres el radar de PERIODISTAS de X. Busca en X (últimas {hours}h) "
            "periodistas y medios que cubran **cáncer, IA en salud, enfermedad rara o "
            "historias de pacientes-investigadores**, y qué ángulos están tocando.{handles}\n"
            "Para CADA uno, una línea: @handle (medio) · fecha · tema/ángulo · enlace · "
            "ETIQUETA [PERIODISTA] / [MEDIO] / [ANGULO].\n"
            "Objetivo: a quién podría interesar la historia de {{TITULAR}} (ingeniera + IA + "
            "caso único), priorizando medios que **leen oncólogos**. NO inventes."
        ),
    },
    "financiacion": {
        "owner": "finanzas-transparencia",
        "handles": [],
        "query": '(beca OR grant OR fundación OR foundation OR filantropía) '
                  '(cáncer OR cancer OR paciente OR patient OR investigación OR research) -is:retweet',
        "prompt": (
            "Eres el radar de FINANCIACIÓN de X. Busca en X (últimas {hours}h) "
            "**becas, fundaciones, filantropía y programas de ayuda** a investigación "
            "de cáncer o a pacientes (incl. enfermedad rara), y convocatorias abiertas "
            "que pudieran apoyar un tratamiento personalizado o el caso de {{TITULAR}}.{handles}\n"
            "Para CADA uno, una línea: autor (@handle/organización) · fecha · qué ofrece · "
            "enlace/convocatoria · ETIQUETA [BECA] / [FUNDACION] / [AYUDA-PACIENTE] / [LEAD].\n"
            "Prioriza lo que NO comprometa sus ayudas sociales (lo valida finanzas/legal). "
            "NO inventes; si no hay nada, dilo."
        ),
    },
    "comunidad": {
        "owner": "comunidad",
        "handles": [],
        "query": '(paciente OR patient) ({{DIAGNOSTICO}} OR breast cancer OR tumor raro OR rare tumor) '
                  '(vacuna OR vaccine OR ensayo OR trial OR navegador OR navigator) -is:retweet',
        "prompt": (
            "Eres el radar de COMUNIDAD de X. Busca en X (últimas {hours}h) **pacientes con "
            "casos parecidos** ({{DIAGNOSTICO}} / tumores raros) que hayan "
            "encontrado vía de **vacuna/ensayo**, **navegadores** de pacientes, y el "
            "**cluster de tecnólogos-pacientes** que ofrezca ayuda/herramientas útiles.{handles}\n"
            "Para CADA uno, una línea: @handle (quién es) · fecha · resumen · enlace · "
            "ETIQUETA [PACIENTE-LEAD] / [NAVEGADOR] / [AYUDA/VOLUNTARIO].\n"
            "Prioriza leads que puedan abrir puerta a un **lab/ensayo/médico**. "
            "NO inventes; si no hay nada, dilo. No expongas datos clínicos de {{TITULAR}}."
        ),
    },
}

CAMPO = ("vacunas personalizadas de cáncer / neoantígenos / inmunoterapia "
         "personalizada / {{DIAGNOSTICO}}")


def _build_query(mode, cfg):
    q = cfg["query"]
    handles = cfg["handles"]
    if handles:
        clause = " OR ".join("from:" + h.lstrip("@") for h in handles)
        q = "(%s) OR (%s)" % (q, clause)
    return q


def _via_xurl(mode, cfg, max_results):
    query = _build_query(mode, cfg)
    try:
        items = _xurl.search_recent(query, max_results=max_results)
    except _xurl.XurlError as e:
        return None, str(e)
    if not items:
        return "_(sin hallazgos en esta pasada)_\n", None
    lines = []
    for t in sorted(items, key=lambda x: x["created_at"], reverse=True):
        who = ("@" + t["author_username"]) if t["author_username"] else (t["author_name"] or t["author_id"])
        when = t["created_at"][:16].replace("T", " ") if t["created_at"] else ""
        resumen = (t["text"] or "").replace("\n", " ").strip()
        if len(resumen) > 280:
            resumen = resumen[:280] + "…"
        lines.append("- **%s** · %s\n  %s\n  %s" % (who, when, resumen, t["url"]))
    return "\n".join(lines) + "\n", None


def _via_grok(mode, cfg, hours):
    handles = cfg["handles"]
    hclause = (" Presta atención especial a estos handles: "
               + ", ".join("@" + h.lstrip("@") for h in handles) + ".") if handles else ""
    query = cfg["prompt"].format(hours=hours, campo=CAMPO, handles=hclause)
    try:
        out = subprocess.run([sys.executable, GROK, query], capture_output=True, text=True, timeout=180)
    except Exception as e:
        return None, "error al llamar a grok.py: %s" % e
    body = (out.stdout or "").strip()
    if not body:
        return None, "Grok no devolvió nada. " + (out.stderr or "").strip()[:300]
    return body, None


def run_mode(mode, max_results, do_print, force_grok):
    cfg = MODES[mode]
    fuente = "xurl (X API v2)"
    body, xerr = (None, "forzado --grok") if force_grok else _via_xurl(mode, cfg, max_results)
    if body is None:
        fuente = "Grok (fallback: %s)" % xerr
        body, gerr = _via_grok(mode, cfg, hours=72)
        if body is None:
            print(f"[{mode}] ✗ xurl falló (%s) y Grok también falló (%s)." % (xerr, gerr))
            return 1

    now = datetime.datetime.now()
    header = (f"# Radar X · {mode} — {now:%Y-%m-%d %H:%M}\n\n"
              f"> Fuente: {fuente} · solo lectura · coste aprox. ~{_xurl.COSTE_LECTURA_USD}$ propio "
              f"/ ~{_xurl.COSTE_LECTURA_GENERAL_USD}$ general por modo.\n"
              f"> Dueño: `{cfg['owner']}`. Derivar lo valioso ahí; contactos → Notion (borrador).\n\n")
    text = header + body
    if do_print:
        print(text); return 0
    os.makedirs(OUTDIR, exist_ok=True)
    path = os.path.join(OUTDIR, f"{mode}-{now:%Y-%m-%d}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"✅ {os.path.relpath(path, os.path.join(HERE, '..'))} ({len(body)} chars) · fuente: {fuente}")
    return 0


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
    modes = [a for a in args if not a.startswith("-")]
    if not modes or modes == ["all"]:
        modes = list(MODES)
    bad = [m for m in modes if m not in MODES]
    if bad:
        print("Modo(s) no válido(s):", ", ".join(bad), "→ usa:", ", ".join(MODES), "| all"); return 1
    rc = 0
    for m in modes:
        rc |= run_mode(m, max_results, do_print, force_grok)
    return rc


if __name__ == "__main__":
    sys.exit(main())
