#!/usr/bin/env python3
"""test_historial_indexado.py — el freno de «Polaris conoce mi historial entero, al día».

POR QUÉ EXISTE. El 23-ago-2026 el historial ordenado llevaba tres semanas siendo invisible
para el RAG y nadie se enteró: vivía en `informes/`, y `kb.py` solo indexa lo que cuelga de
`00_FUENTE-DE-VERDAD/`. Polaris conocía el vertedero de copias sueltas —nombres viejos,
cuatro copias del mismo informe— y no el archivo bueno. Eso no se arregla mudando la carpeta
una vez: se arregla con algo que se ponga rojo el día que vuelva a pasar.

Tres cosas que este test se niega a dejar pasar:
  1. Que un documento del historial no esté en el índice.
  2. Que el índice sea más viejo que el documento más nuevo (llegó un informe y nadie reindexó).
  3. Que `kb.py index` pueda correrse con un intérprete sin pypdf, que se comería los PDFs
     —o sea, el historial entero— sin decir una palabra.

SIN DATOS REALES: si no hay historial en esta máquina (un worktree limpio, CI), el test
PASA en vez de fallar. Lo que vigila es una máquina con datos, no la existencia de los datos.
"""
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import historial as h   # noqa: E402
import kb               # noqa: E402

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# ── 1 · el fail-loud de pypdf ────────────────────────────────────────────────
# Esto sí se comprueba siempre: es código, no datos.
check("kb sabe decir si este intérprete puede leer PDFs", callable(kb.hay_pypdf))
fuente_kb = open(os.path.join(ROOT, "tools", "kb.py"), encoding="utf-8").read()
check("un ImportError de pypdf NO se traga como si fuera un PDF roto",
      "except ImportError:" in fuente_kb)
check("build se planta antes de reemplazar el índice sin pypdf",
      "if not hay_pypdf()" in fuente_kb)
check("el historial ordenado es la copia canónica para el dedup del índice",
      "_historial" in kb.CANONICO and "_PRIVADO_CLINICO" in kb.CANONICO)

# ── 2 · el historial vive DENTRO de lo que el RAG indexa ─────────────────────
check("la raíz del historial cuelga de la fuente de verdad",
      os.path.abspath(h.RAIZ).startswith(os.path.abspath(kb.FV) + os.sep))

# ── 3 · con datos delante: todo documento indexado y el índice al día ────────
docs = []
for _clave, carpeta in h.CARPETAS:
    d = os.path.join(h.RAIZ, carpeta)
    if os.path.isdir(d):
        docs += [os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(".pdf")]

if not docs or not os.path.exists(kb.DB):
    print("test_historial_indexado: sin historial local, no hay nada que vigilar "
          "(%d docs, índice %s)" % (len(docs), "sí" if os.path.exists(kb.DB) else "no"))
    print("test_historial_indexado: %d ok, %d fallos" % (_pass, _fail))
    sys.exit(1 if _fail else 0)

con = sqlite3.connect(kb.DB)
indexados = {r[0] for r in con.execute(
    "SELECT DISTINCT path FROM chunks WHERE path LIKE ?", ("%" + os.path.basename(h.RAIZ) + "%",))}

# Un PDF escaneado no aporta texto y no genera pasajes: eso NO es un fallo del índice. Se
# exige que esté indexado el documento O su transcripción al lado.
def esta(p):
    rel = os.path.relpath(p, kb.FV)
    base = os.path.splitext(rel)[0]
    return any(x == rel or x.startswith(base) for x in indexados)

ausentes = [p for p in docs if not esta(p)]
check("todo documento del historial está en el índice del RAG (faltan %d de %d)"
      % (len(ausentes), len(docs)), not ausentes)
for p in ausentes[:10]:
    print("     falta: %s" % os.path.basename(p)[:88])

mas_nuevo = max(os.path.getmtime(p) for p in docs)
check("el índice no es más viejo que el documento más nuevo (reindexa: "
      "%s/.venv/bin/python3 tools/kb.py index)" % ROOT,
      os.path.getmtime(kb.DB) >= mas_nuevo - 60)
if os.path.getmtime(kb.DB) < mas_nuevo - 60:
    print("     índice: %s · documento más nuevo: %s"
          % (time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(kb.DB))),
             time.strftime("%Y-%m-%d %H:%M", time.localtime(mas_nuevo))))

print("test_historial_indexado: %d ok, %d fallos  (%d documentos vigilados)"
      % (_pass, _fail, len(docs)))
sys.exit(1 if _fail else 0)
