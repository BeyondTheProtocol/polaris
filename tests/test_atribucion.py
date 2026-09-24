#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Todo lo que alguien aporta se atribuye con su nombre (regla de {{TITULAR}}, 24-sep-2026).

Nace de Marc Recio: su idea de las señales booleanas (tools/tier_evidencia.py) salía como
«idea recibida por DM de otro constructor». Él pidió su nombre y su GitHub, que para alguien
que empieza cuenta, y {{TITULAR}} lo convirtió en regla: «Todo lo que aporte hay que atribuirlo, y
en el README dar las gracias».

Lo que fija:
  1. Ninguna aportación se atribuye de forma anónima («otro constructor», «un seguidor»,
     «idea de un…», «por DM de alguien»): si se sabe de quién es, va su nombre.
  2. Cada «Idea de <Nombre> (<enlace>)» escrita en el código sale también en la sección
     «🙏 Gracias» del README, con el mismo enlace."""
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZONAS = ("tools", ".claude", "README.md", "CONTRIBUTING.md", "CHANGELOG.md", "AGENTS.md", "CLAUDE.md")
FUERA = {".git", "node_modules", ".venv", "state", "worktrees", "models", "__pycache__"}
ANONIMA = re.compile(
    r"otros? constructor(?:es|a|as)?\b|un(?:a)? seguidor(?:a)?\b|idea (?:recibida|de un|de una)\b"
    r"|por DM de (?:un|una|otro|otra|alguien)\b|alguien (?:en|por) (?:X|Twitter|LinkedIn|DM)\b",
    re.IGNORECASE)
CREDITO = re.compile(r"Idea de ([A-ZÁÉÍÓÚÑ][\wáéíóúñ]+(?: [A-ZÁÉÍÓÚÑ][\wáéíóúñ]+)*) \((https?://[^)\s]+)\)")

fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def _ficheros():
    for zona in ZONAS:
        ruta = os.path.join(RAIZ, zona)
        if os.path.isfile(ruta):
            yield ruta
            continue
        for base, dirs, nombres in os.walk(ruta):
            dirs[:] = [d for d in dirs if d not in FUERA]
            for n in nombres:
                if n.endswith((".py", ".md", ".sh", ".json")):
                    yield os.path.join(base, n)


def main():
    yo = os.path.abspath(__file__)
    anonimas, creditos = [], {}
    for f in _ficheros():
        if os.path.abspath(f) == yo:
            continue
        try:
            texto = open(f, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        rel = os.path.relpath(f, RAIZ)
        for n, linea in enumerate(texto.splitlines(), 1):
            if ANONIMA.search(linea):
                anonimas.append("%s:%d: %s" % (rel, n, linea.strip()[:120]))
        for m in CREDITO.finditer(texto.replace("\n", " ").replace("  ", " ")):
            creditos.setdefault(m.group(1), (m.group(2), rel))

    print("── 1. nada atribuido de forma anónima ──")
    for a in anonimas:
        print("     " + a)
    check(not anonimas, "ninguna aportación sin nombre (%d encontradas)" % len(anonimas))

    print("── 2. quien sale en el código sale en «🙏 Gracias» ──")
    readme = open(os.path.join(RAIZ, "README.md"), encoding="utf-8").read()
    m = re.search(r"^## 🙏 Gracias\n(.*?)(?=^## )", readme, re.S | re.M)
    gracias = m.group(1) if m else ""
    check(bool(gracias), "el README tiene la sección «🙏 Gracias»")
    check(bool(creditos), "hay al menos un crédito «Idea de <Nombre> (<enlace>)» en el código")
    for nombre, (enlace, rel) in sorted(creditos.items()):
        check(nombre in gracias and enlace in gracias,
              "%s (%s, en %s) sale en «Gracias» con su enlace" % (nombre, enlace, rel))

    print()
    if fallos:
        print("❌ %d fallo(s): atribuye con nombre y da las gracias en el README" % len(fallos))
        return 1
    print("✅ ATRIBUCIÓN EN VERDE (%d crédito(s), 0 anónimas)" % len(creditos))
    return 0


if __name__ == "__main__":
    sys.exit(main())
