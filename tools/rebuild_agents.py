#!/usr/bin/env python3
"""rebuild_agents.py — el núcleo regenera el borde.

Mantiene en los agentes un BLOQUE COMÚN (el "muro común") idéntico al del núcleo
(`tools/templates/agente_boilerplate.md`). Garantía de seguridad por DISEÑO: SOLO toca lo
que hay ENTRE los marcadores `<!-- BOILERPLATE:START … -->` y `<!-- BOILERPLATE:END -->`.
Todo lo de fuera (el cuerpo propio de cada agente: su rol, su voz, sus guardarraíles, y en
especial los prompts CLÍNICOS) queda intacto, byte a byte. El regenerador nunca reescribe
lo bespoke; solo sincroniza el trozo compartido que el núcleo posee.

Modos:
  (defecto)        sincroniza el bloque en los agentes que YA lo tienen marcado.
  --check          no escribe; lista los agentes desincronizados (rc 1 si hay drift).
  --list           lista qué agentes tienen el bloque.
  --init <slug…>   añade el bloque (al final) a los agentes nombrados que aún no lo tienen.

Opera sobre el checkout LOCAL (este repo, o `BTP_AGENTS_DIR` en tests): es una herramienta
de build; los cambios van por rama y se fusionan con el gate de {{TITULAR}}.
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS_DIR = os.environ.get("BTP_AGENTS_DIR") or os.path.join(ROOT, ".claude", "agents")
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "agente_boilerplate.md")

START = "<!-- BOILERPLATE:START"
END = "<!-- BOILERPLATE:END -->"
BLOCK_RE = re.compile(r"<!-- BOILERPLATE:START.*?<!-- BOILERPLATE:END -->", re.S)


def template():
    return open(TEMPLATE, encoding="utf-8").read().strip()


def agents():
    return sorted(glob.glob(os.path.join(AGENTS_DIR, "*.md")))


def slug(path):
    return os.path.basename(path)[:-3]


def sync_text(txt, block):
    """Reemplaza SOLO la región marcada por `block`. Devuelve (nuevo, cambió)."""
    if not BLOCK_RE.search(txt):
        return txt, False
    nuevo = BLOCK_RE.sub(lambda _m: block, txt)
    return nuevo, (nuevo != txt)


def append_block(txt, block):
    return txt.rstrip("\n") + "\n\n" + block + "\n"


def main(argv):
    mode = "sync"
    names = []
    if argv:
        if argv[0].startswith("--"):
            mode = argv[0]
            names = argv[1:]
        else:
            names = argv
    block = template()
    if START not in block or END not in block:
        print("❌ El template no está entre marcadores BOILERPLATE.")
        return 2

    paths = agents()
    if names:
        paths = [p for p in paths if slug(p) in set(names)]

    marcados, cambiados, inicializados = [], [], []
    for p in paths:
        txt = open(p, encoding="utf-8").read()
        tiene = bool(BLOCK_RE.search(txt))
        if tiene:
            marcados.append(slug(p))

        if mode == "--list":
            continue

        if mode == "--init":
            if not tiene:
                open(p, "w", encoding="utf-8").write(append_block(txt, block))
                inicializados.append(slug(p))
            continue

        # sync / --check: solo agentes que ya tienen el bloque
        if tiene:
            nuevo, cambio = sync_text(txt, block)
            if cambio:
                cambiados.append(slug(p))
                if mode != "--check":
                    open(p, "w", encoding="utf-8").write(nuevo)

    if mode == "--list":
        print("Agentes con muro común (%d): %s" % (len(marcados), ", ".join(marcados) or "—"))
        return 0
    if mode == "--init":
        print("Inicializados (%d): %s" % (len(inicializados), ", ".join(inicializados) or "—"))
        return 0
    if mode == "--check":
        if cambiados:
            print("⚠️  Desincronizados con el núcleo (%d): %s" % (len(cambiados), ", ".join(cambiados)))
            print("   → corre `python3 tools/rebuild_agents.py` para regenerarlos.")
            return 1
        print("✅ Todos los agentes con muro común están al día (%d marcados)." % len(marcados))
        return 0
    # sync
    print("🔧 Regenerados desde el núcleo (%d): %s" % (len(cambiados), ", ".join(cambiados) or "—"))
    print("   (%d agentes con muro común; el resto, intactos por diseño)" % len(marcados))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
