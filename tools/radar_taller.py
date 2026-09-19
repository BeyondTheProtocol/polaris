#!/usr/bin/env python3
"""radar_taller.py — radar DETERMINISTA de mejoras del TALLER (Polaris -> NED).

Recoge candidatos de mejora del SISTEMA (no clínico) de las fuentes ya disponibles,
los DEDUPLICA contra lo que ya está en el backlog / log de mejoras, y emite una lista
JSON de candidatos para que el agente `auto-mejora` los pase por el FILTRO de 6 (ver
`00_FUENTE-DE-VERDAD/04 · IA/Auto-mejora/Radar-Taller-Metodo.md`).

Filosofía (a propósito): esta tool NO juzga, NO verifica, NO instala, NO llama a ningún
LLM ni a la red. Solo RECOLECTA + DEDUPLICA. El juicio (filtro de 6 + sello de evidencia
+ veredicto adoptar/auditar/decisión-{{TITULAR}}/descartar) lo hace `auto-mejora`, porque eso
necesita criterio. Así es $0, local y reproducible. El carril CLÍNICO tiene su propio
radar (`radar_literatura.py`); aquí NO entran candidatos clínicos.

Fuentes que barre (las que existen hoy; se amplían sin romper el contrato de salida):
  1. Guardados de X de {{TITULAR}} (`_cajita/x_guardados/guardados.jsonl`), solo los de
     tema SISTEMA (tag `sistema` o sin tag); los `NED` puros se dejan al radar clínico.
  2. Fricción interna: nº de jobs en `failed/` de la cola (señal de algo que arreglar).

Salida (contrato estable): un objeto JSON
  {"generado": iso, "n_candidatos": N, "fricción": {...}, "candidatos": [ {...}, ... ]}
cada candidato: {"fuente","handle","url","texto","ts","clave"}. `clave` = id estable
para dedup aguas abajo.

Uso:
  python3 tools/radar_taller.py            # escribe a _cajita/radar_taller/candidatos.json
  python3 tools/radar_taller.py --dry      # NO escribe; imprime el JSON
  python3 tools/radar_taller.py --json     # imprime el JSON (además de escribir)
  python3 tools/radar_taller.py --todo     # incluye también los ya vistos (debug)
"""
import json, os, sys, argparse, datetime, re, glob

# Los DATOS (guardados, backlog, cola) viven en casa base, no en el worktree. Igual que el
# dispatcher (REPO=${BTP_REPO:-$HOME/claudecode}), resolvemos contra casa base por defecto.
# El fallback decía eso y hacía lo contrario: `dirname(dirname(__file__))` desde un worktree da
# el WORKTREE, donde no hay ni guardados ni backlog, así que el radar devolvía 0 candidatos en
# silencio y parecía que no había nada que traer (12-sep-2026). Misma convención que `kb.py`.
ROOT = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")

GUARDADOS = os.path.join(ROOT, "_cajita", "x_guardados", "guardados.jsonl")
AUTOMEJ = os.path.join(ROOT, "00_FUENTE-DE-VERDAD", "04 · IA", "Auto-mejora")
BACKLOG = os.path.join(AUTOMEJ, "Backlog-Mejoras.md")
MEJ_LOG = os.path.join(AUTOMEJ, "Mejoras-log.md")
OUT_DIR = os.path.join(ROOT, "_cajita", "radar_taller")
OUT_FILE = os.path.join(OUT_DIR, "candidatos.json")

# Cola: misma convención que cola.py (STATE = BTP_STATE_DIR or REPO/tools/state).
_STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(ROOT, "tools", "state")
FAILED_DIRS = [os.path.join(_STATE, "queue", "failed")]

# Un guardado entra al radar del TALLER para DETECCIÓN. Ojo: esto NO decide si es una mejora,
# solo si el humano (auto-mejora, filtro de 6) llega a verlo.
#
# 25-jul-2026: antes devolvía False para todo tag `NED` puro, así que una señal de SISTEMA mal
# etiquetada por el filtro de palabras clave de la captura quedaba INVISIBLE (así se perdieron
# los 15 del 23-jul y los leads clínicos escondidos en los guardados «solo-enlace»). Ahora pasa
# TODO a detección y el candidato viaja con `tag_clinico` para que el filtro de 6 lo cribe o lo
# redirija al carril clínico. No saca nada del radar clínico: son carriles distintos.
def _es_sistema(tags):
    return True


def _es_clinico(tags):
    """True si la captura lo etiquetó como NED puro (clínico): pista para el triaje, no un veto."""
    tags = tags or []
    return "NED" in tags and "sistema" not in tags


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _clave(handle, url, texto):
    """ID estable para dedup: id del tweet si está en la url, si no handle+inicio del texto."""
    m = re.search(r"/status/(\d+)", url or "")
    if m:
        return "x:" + m.group(1)
    return "t:" + _norm((handle or "") + " " + (texto or ""))[:80]


def _cargar_guardados():
    if not os.path.exists(GUARDADOS):
        return []
    out = []
    for line in open(GUARDADOS, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if not _es_sistema(d.get("tags")):
            continue
        url = d.get("url", "")
        texto = " ".join((d.get("text") or "").split())
        out.append({
            "fuente": "x_guardados",
            "handle": d.get("handle", ""),
            "url": url,
            "texto": texto[:400],
            "ts": d.get("iso", "") or d.get("ts", ""),
            "clave": _clave(d.get("handle"), url, texto),
            # pista para el triaje: la captura lo etiquetó como clínico. No es un veto; si el
            # filtro de 6 ve que es sistema mal etiquetado, entra igual.
            "tag_clinico": _es_clinico(d.get("tags")),
        })
    return out


_RE_NO_SILENCIAR = re.compile(
    r"<!--\s*RADAR:NO-SILENCIAR\s+inicio\s*-->.*?<!--\s*RADAR:NO-SILENCIAR\s+fin\s*-->",
    re.DOTALL | re.IGNORECASE)


def _texto_ya_registrado():
    """Concatena backlog + log de mejoras para dedup por presencia de url / id.

    OJO — «ya ANALIZADO» y «ya DECIDIDO» no son lo mismo, y el radar los confundía (12-sep-2026).
    El 20-jul-2026 un triaje «skim de texto, sin abrir enlaces» descartó ~176 ítems escribiendo
    sus ids en una sola línea del log *precisamente para que dedupasen*. Como el dedup busca el id
    literal en este blob, esos 176 quedaron silenciados PARA SIEMPRE sin que nadie hubiera abierto
    un solo enlace: OpenMed, Colibri, tencentDB, Fleet Deck y WANDR estaban ahí dentro. Por eso el
    radar lleva desde julio devolviendo casi nada, y esas herramientas tuvieron que reaparecer a
    mano en la revisión de guardados del 12-sep.

    Regla que impone esta función: un descarte SIN abrir el enlace no puede silenciar. Se envuelve
    entre `<!-- RADAR:NO-SILENCIAR inicio -->` y `<!-- RADAR:NO-SILENCIAR fin -->` y deja de contar
    para el dedup, sin borrar nada del historial. Un descarte CON veredicto se escribe fuera de los
    marcadores y sí silencia, como siempre.
    """
    blob = ""
    for f in (BACKLOG, MEJ_LOG):
        if os.path.exists(f):
            try:
                blob += "\n" + _RE_NO_SILENCIAR.sub(" ", open(f, encoding="utf-8").read()).lower()
            except Exception:
                pass
    return blob


def _ya_visto(cand, blob):
    """Visto si su id de tweet o su url aparece ya en backlog/log."""
    m = re.search(r"/status/(\d+)", cand["url"] or "")
    if m and m.group(1) in blob:
        return True
    if cand["url"] and cand["url"].lower() in blob:
        return True
    return False


def _friccion():
    failed = 0
    encontrado = None
    for d in FAILED_DIRS:
        if os.path.isdir(d):
            n = len([x for x in glob.glob(os.path.join(d, "*")) if os.path.isfile(x)])
            failed += n
            if n:
                encontrado = d
    return {"jobs_failed": failed, "failed_dir": encontrado}


def main():
    ap = argparse.ArgumentParser(description="Radar determinista de mejoras del taller.")
    ap.add_argument("--dry", action="store_true", help="no escribe; solo imprime el JSON")
    ap.add_argument("--json", action="store_true", help="imprime el JSON (machine)")
    ap.add_argument("--todo", action="store_true", help="incluye también los ya vistos")
    args = ap.parse_args()

    cands = _cargar_guardados()
    blob = _texto_ya_registrado()
    if not args.todo:
        cands = [c for c in cands if not _ya_visto(c, blob)]

    # dedup interno por clave
    vistos, unicos = set(), []
    for c in cands:
        if c["clave"] in vistos:
            continue
        vistos.add(c["clave"])
        unicos.append(c)

    salida = {
        "generado": datetime.datetime.now().isoformat(timespec="seconds"),
        "n_candidatos": len(unicos),
        "fricción": _friccion(),
        "candidatos": unicos,
    }

    if not args.dry:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            json.dump(salida, f, ensure_ascii=False, indent=2)

    if args.dry or args.json:
        print(json.dumps(salida, ensure_ascii=False, indent=2))
    else:
        fr = salida["fricción"]
        print("radar_taller: %d candidato(s) nuevo(s) de mejora del taller." % len(unicos))
        if fr["jobs_failed"]:
            print("  ⚠️ fricción: %d job(s) en failed/ (%s)" % (fr["jobs_failed"], fr["failed_dir"]))
        if not args.dry:
            print("  → escrito en %s" % OUT_FILE)
        for c in unicos[:12]:
            print("  • @%-18s %s" % (c["handle"][:18], (c["texto"][:90] or c["url"])))
        if len(unicos) > 12:
            print("  … y %d más" % (len(unicos) - 12))


if __name__ == "__main__":
    main()
