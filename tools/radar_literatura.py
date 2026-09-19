#!/usr/bin/env python3
"""Radar de literatura — barrido de papers/ensayos NUEVOS sobre las dianas del caso (vía Perplexity academic).
Solo reporta lo que no había visto antes (cache `tools/.radar_seen.json`) y escribe un digest datado en
`00_FUENTE-DE-VERDAD/04 · IA/Radar/`. Pensado para correr semanal. Coste: ~6 llamadas `sonar` académicas (céntimos).

Uso:
  python3 tools/radar_literatura.py                 # barre todos los temas, escribe digest, actualiza cache
  python3 tools/radar_literatura.py --dry           # no escribe nada (ni fichero ni cache); solo imprime
  python3 tools/radar_literatura.py --recency week  # ventana de frescura (hour/day/week/month/year; def: month)
  python3 tools/radar_literatura.py --limit 2       # solo los primeros N temas (para probar barato)

NOTA: tras un barrido real, reindexa la fuente de verdad: `python3 tools/kb.py index`.
"""
import json, os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from perplexity import load_key, post, CHAT_URL  # reutiliza la herramienta ya probada

SEEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".radar_seen.json")
RADAR_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "00_FUENTE-DE-VERDAD", "04 · IA", "Radar")

# Temas = dianas del caso (ver .claude/agents/comite-medico.md). Consultas en inglés para mejor cobertura.
TOPICS = [
    # Las dianas del caso viven en `tools/perfil.local.json`, no aquí: las etiquetas y las
    # consultas describen el perfil tumoral de una persona concreta. Estas dos son un
    # ejemplo de la FORMA — (etiqueta corta, consulta en inglés para mejor cobertura).
    ("Ejemplo · diana molecular",
     "targeted therapy for {{DIANA}} alteration clinical trial"),
    ("Ejemplo · vía terapéutica",
     "personalized cancer vaccine in {{SUBTIPO}} cancer, combined with checkpoint inhibitors"),
]


def load_seen():
    try:
        with open(SEEN) as f:
            return set(json.load(f))
    except (OSError, json.JSONDecodeError):
        return set()


def scan_topic(key, query, recency):
    """Devuelve (texto_resumen, lista_de_fuentes[{title,url,date}])."""
    content = (f"List the most relevant RECENT peer-reviewed papers or clinical-trial updates on: {query}. "
               "For each, give title and a one-line finding. Be concise and factual.")
    # 🔴 BORDE (política ingeniera): la consulta puede usar términos de ciencia genéricos, pero
    # NUNCA un identificador de paciente (nombre/contacto/huella genómica específica). fail-closed.
    import borde
    ok, motivo = borde.egress_cientifico(content, destino="perplexity-radar")
    if not ok:
        return f"[bloqueado por el borde: {motivo} — no se envió a Perplexity]", []
    body = {
        "model": "sonar",
        "messages": [{"role": "user", "content": content}],
        "search_mode": "academic",
        "search_recency_filter": recency,
    }
    resp, err = post(CHAT_URL, key, body)
    if err:
        return f"[error: {err}]", []
    try:
        text = resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        text = ""
    sources = resp.get("search_results") or []
    return text, sources


def main():
    args = sys.argv[1:]
    dry = "--dry" in args
    if dry:
        args.remove("--dry")
    recency = "month"
    if "--recency" in args:
        i = args.index("--recency"); recency = args[i + 1]; del args[i:i + 2]
    limit = None
    if "--limit" in args:
        i = args.index("--limit"); limit = int(args[i + 1]); del args[i:i + 2]

    key, _ = load_key()
    if not key:
        return

    seen = load_seen()
    new_urls = []
    topics = TOPICS[:limit] if limit else TOPICS
    today = datetime.date.today().isoformat()
    blocks = []

    for label, query in topics:
        text, sources = scan_topic(key, query, recency)
        fresh = [s for s in sources if s.get("url") and s["url"] not in seen]
        for s in fresh:
            new_urls.append(s["url"])
        print(f"\n### {label}  —  {len(fresh)} nuevas / {len(sources)} totales")
        lines = [f"### {label}"]
        if not fresh:
            print("  (sin novedades)"); lines.append("_(sin novedades esta vez)_")
        for s in fresh:
            t = (s.get("title") or s.get("url") or "").strip()
            u = s.get("url", "")
            d = s.get("date") or s.get("last_updated") or ""
            row = f"- [{t}]({u})" + (f" · {d}" if d else "")
            print("  " + row); lines.append(row)
        blocks.append("\n".join(lines))

    if dry:
        print("\n[--dry: no se escribe digest ni cache]")
        return

    # Escribe digest datado
    os.makedirs(RADAR_DIR, exist_ok=True)
    header = (f"# 🛰️ Radar de literatura — {today}\n\n"
              f"> Papers/ensayos **nuevos** (ventana: {recency}) sobre las dianas del caso, vía Perplexity academic. "
              f"Apoyo a la decisión, **no consejo médico**. Verifica antes de actuar.\n\n"
              f"**{len(new_urls)} novedades** en {len(topics)} temas.\n")
    path = os.path.join(RADAR_DIR, f"Radar-Literatura-{today}.md")
    with open(path, "w") as f:
        f.write(header + "\n" + "\n\n".join(blocks) + "\n")
    print(f"\n✅ Digest escrito: {path}")

    # Actualiza cache de vistos
    with open(SEEN, "w") as f:
        json.dump(sorted(seen | set(new_urls)), f, ensure_ascii=False, indent=0)
    print(f"✅ Cache actualizada (+{len(new_urls)} URLs). Recuerda: python3 tools/kb.py index")


if __name__ == "__main__":
    main()
