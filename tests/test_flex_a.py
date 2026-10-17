#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flex_a (analista A del Flex): pruebas con datos SINTÉTICOS (cero dato de la paciente).

1. bootstrap_frac: respuesta conocida (k=0 → [0,0]; 500/1000 → IC que contiene 0,5 y ancho ~0,06),
   determinista por nombre de gen.
2. Auditoría de parálogos: un transcrito con la sonda exacta, otro con 2 fallos y otro con 3;
   `busca` debe devolver los dos primeros con sus fallos y NO el tercero; rc correcto.
3. MURO: ningún fichero de tools/flex_analisis/a/ contiene un identificador de bloque
   (patrón NN-NNNNN_MN / XNN-NNNNN_MN); el mapeo muestra→tejido se deriva en tiempo de ejecución.
Necesita numpy/pandas: se re-ejecuta con el python de nescore si el del sistema no los tiene.
"""
import os
import re
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(RAIZ, "tools", "flex_analisis", "a")
NESCORE = os.path.expanduser("~/.claude-science/conda/envs/nescore/bin/python")

try:
    import numpy  # noqa: F401
    import pandas  # noqa: F401
except ImportError:
    if not os.path.exists(NESCORE):
        print("FALLO: sin numpy/pandas y sin el entorno nescore")
        sys.exit(1)
    sys.exit(subprocess.call([NESCORE, "-I", os.path.abspath(__file__)]))

sys.path.insert(0, DIR)
fallos = []


def ok(cond, msg):
    print(("ok   " if cond else "FALLO ") + msg)
    if not cond:
        fallos.append(msg)


from _comun import bootstrap_frac  # noqa: E402

f, lo, hi = bootstrap_frac(0, 100, nombre="X")
ok((f, lo, hi) == (0.0, 0.0, 0.0), "k=0 da [0,0]")
f, lo, hi = bootstrap_frac(500, 1000, nombre="Y")
ok(f == 0.5 and lo < 0.5 < hi and 0.04 < hi - lo < 0.08, f"500/1000 IC {lo:.3f}-{hi:.3f}")
ok(bootstrap_frac(500, 1000, nombre="Y") == (f, lo, hi), "determinista por nombre")

import _paralogos as P  # noqa: E402

probe = "ACGTTGCAAGCTTAGGCATCGATCGGATCCTAGCTAGCTTGACCAGTCAT"[:50]
v2 = list(probe); v2[5] = "T" if v2[5] != "T" else "A"; v2[40] = "G" if v2[40] != "G" else "C"
v3 = list(v2); v3[20] = "A" if v3[20] != "A" else "C"
txs = ["GGGG" + probe + "CCCC", "TTTT" + "".join(v2) + "AAAA", "CCCC" + "".join(v3) + "GGGG"]
genes = ["G1", "G2", "G3"]
starts, pos = [], 0
for s in txs:
    starts.append(pos); pos += len(s) + 1
big = "#".join(txs)
h = P.busca(probe, big, starts, genes)
ok(h == {"G1": 0, "G2": 2}, f"busca ≤2 fallos: {h}")
ok(P.rc("AACGT") == "ACGTT", "rc")
ok(P.busca(P.rc(probe), big, starts, genes) == {}, "la sonda en antisentido no casa en sentido")

patron = re.compile(r"\b[A-Z]?\d{2}-\d{5}_M\d\b")
for fn in sorted(os.listdir(DIR)):
    p = os.path.join(DIR, fn)
    if os.path.isfile(p) and fn.endswith((".py", ".R", ".json", ".md")):
        ok(not patron.search(open(p, encoding="utf-8").read()), f"sin identificador de bloque en {fn}")

print(f"\n{'✅' if not fallos else '❌'} test_flex_a: {len(fallos)} fallo(s)")
sys.exit(1 if fallos else 0)
