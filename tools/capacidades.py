#!/usr/bin/env python3
"""capacidades.py — «reusar antes de crear» (anti-sprawl).

Antes de montar un agente/comité/caja nuevo, el `constructor` (o el orquestador) pregunta
aquí: *¿ya existe algo que cubra esto?* Devuelve los agentes existentes más parecidos a la
capacidad propuesta, ordenados por relevancia (solapamiento de términos, ponderado por
rareza). Si el mejor encaje es fuerte → REUSA; si no hay nada cercano → hueco real, y
entonces se crea (con caducidad). Así crear es la excepción, no el reflejo.

Determinista, local, sin red: solo lee las descripciones de `.claude/agents/*.md`.

Con `--tools` busca lo mismo entre las HERRAMIENTAS de `tools/`, leyendo sus fichas
(`tools/fichas/*.json`: pieza, para, cuándo sí). Es el «busca antes de crear» que da el hook
`ficha_guard.py` al denegar una herramienta sin ficha (25-sep-2026, ver `tools/fichas.py`).

USO:
  python3 capacidades.py "organizar un viaje a una cita en otra ciudad"
  python3 capacidades.py -n 8 "responder comentarios en redes"
  python3 capacidades.py --tools "barrer ensayos clínicos chinos"
"""
import glob
import json
import math
import os
import re
import sys
import unicodedata
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS_DIR = os.environ.get("BTP_AGENTS_DIR") or os.path.join(ROOT, ".claude", "agents")
UMBRAL_REUSA = 6.0  # score del mejor encaje a partir del cual conviene reusar (heurístico)

_WORD = re.compile(r"[a-z0-9]+")
# Palabras vacías (ES/EN): no aportan señal y, si no se filtran, hacen que cualquier
# texto "parezca" parecido a todo. Fuera.
_STOP = {
    "de", "la", "el", "los", "las", "un", "una", "unos", "unas", "y", "o", "a", "e",
    "en", "para", "por", "con", "sin", "que", "del", "al", "lo", "su", "sus", "se",
    "es", "son", "como", "mas", "muy", "ya", "le", "les", "te", "me", "nos", "si",
    "no", "ni", "the", "of", "to", "and", "or", "in", "on", "for", "with", "is",
}


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _toks(s):
    return [w for w in _WORD.findall(_norm(s)) if len(w) > 2 and w not in _STOP]


def _desc(path):
    """name + description del frontmatter (lo que define para qué sirve el agente)."""
    name = desc = ""
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return name, desc
    if not lines or lines[0].strip() != "---":
        return name, desc
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        m = re.match(r"^(name|description):\s*(.*)$", ln)
        if m:
            if m.group(1) == "name":
                name = m.group(2).strip()
            else:
                desc = m.group(2).strip()
    return name, desc


def _cargar():
    items = []
    for p in sorted(glob.glob(os.path.join(AGENTS_DIR, "*.md"))):
        name, desc = _desc(p)
        slug = os.path.basename(p)[:-3]
        toks = set(_toks(slug + " " + name + " " + desc))
        items.append((slug, desc, toks))
    return items


def _cargar_herramientas(raiz=ROOT):
    """(pieza, para, tokens) de cada ficha de tools/fichas/. Sin fichas → lista vacía."""
    items = []
    for p in sorted(glob.glob(os.path.join(raiz, "tools", "fichas", "*.json"))):
        try:
            with open(p, encoding="utf-8") as fh:
                fi = json.load(fh)
        except (OSError, ValueError):
            continue
        if fi.get("estado_ficha") == "retirada" or not fi.get("pieza"):
            continue
        pieza = fi["pieza"]
        nombre = os.path.splitext(pieza)[0].replace("/", " ").replace("_", " ")
        texto = " ".join((nombre, fi.get("para") or "", fi.get("cuando_si") or ""))
        items.append((pieza, fi.get("para") or "", set(_toks(texto))))
    return items


def buscar_herramientas(query, n=5, raiz=ROOT):
    return _puntuar(_cargar_herramientas(raiz), query, n)


def buscar(query, n=5):
    return _puntuar(_cargar(), query, n)


def _puntuar(items, query, n):
    """Solapamiento de términos ponderado por rareza (IDF): un término que casi nadie usa pesa
    más que uno que sale en todas partes."""
    N = max(1, len(items))
    df = Counter()
    for _slug, _desc, toks in items:
        for t in toks:
            df[t] += 1
    qt = set(_toks(query))
    scored = []
    for slug, desc, toks in items:
        comunes = qt & toks
        score = sum(math.log((N + 1) / df[t]) for t in comunes)
        if score > 0:
            scored.append((round(score, 2), slug, desc))
    scored.sort(reverse=True)
    return scored[:n]


def main(argv):
    n = 5
    if "-n" in argv:
        i = argv.index("-n")
        n = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    tools = "--tools" in argv
    argv = [a for a in argv if a != "--tools"]
    query = " ".join(argv).strip()
    if not query:
        print('uso: capacidades.py [-n N] [--tools] "<capacidad que necesitas>"')
        return 2
    res = buscar_herramientas(query, n) if tools else buscar(query, n)
    if not res:
        print("🆕 No hay nada parecido en el gabinete → parece un HUECO real.")
        print("   Si lo creas, dale objetivo-NED y caducidad (no dejes zombies).")
        return 0
    top = res[0][0]
    segundo = res[1][0] if len(res) > 1 else 0.0
    print("🔎 Capacidades existentes más parecidas a: «%s»\n" % query)
    for score, slug, desc in res:
        print("  [%5.2f] %s — %s" % (score, slug, (desc[:90] + "…") if len(desc) > 90 else desc))
    print()
    if top >= UMBRAL_REUSA or (segundo and top >= 2 * segundo):
        print("✅ REUSA «%s» (encaje claro). Crear uno nuevo solaparía; mejor extiende el que hay." % res[0][1])
    elif top >= 3.0:
        print("🟠 Hay parecidos (p. ej. «%s»). Antes de crear, comprueba si reusas/coordinas uno." % res[0][1])
    else:
        print("🟡 Encaje flojo → probablemente un hueco real; si creas, con objetivo-NED y caducidad.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
