#!/usr/bin/env python3
"""test_flex_higado_dianas.py — reproducibilidad de las dianas del hígado (tools/flex_analisis/higado_dianas/).

1. Funciones puras (sin datos clínicos): la deconvolución recupera pesos y expresión tumoral de una mezcla sintética
   con respuesta conocida; Wilson con k=0 da cota superior > 0.
2. Con cachés: corre `dianas.py run` DOS veces en directorios temporales y exige sha256 idénticos de todas las
   salidas, que coincidan con el MANIFIESTO canónico si existe, y stdout sin barcodes ni ids de bloque.
Sin el entorno nescore o sin cachés (otra máquina, CI) → SKIP visible (77), nunca PASS.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "tools", "flex_analisis", "higado_dianas")
PY = os.path.expanduser("~/.claude-science/conda/envs/nescore/bin/python")
fallos = []

SINTETICO = r"""
import sys; sys.path.insert(0, %r)
import numpy as np, _h, _c
rng = np.random.default_rng(1)
G = 60
perf = rng.gamma(1.0, 5.0, size=(3, G))
marc = np.arange(0, 30)
tum = rng.gamma(1.0, 5.0, size=G); tum[marc] = 0
amb = rng.random(G); amb /= amb.sum()
w = np.array([0.1, 0.05, 0.0]); rho = 0.08
Pst = np.clip((perf - rho * amb * 1e4) / (1 - rho), 0, None)
pool = rho * amb * 1e4 + w @ Pst + (1 - rho - w.sum()) * tum
we, wT, T, _ = _h.deconvoluciona(pool, perf, amb, rho, marc)
print(float(np.abs(we - w).max()), float(np.abs(T - tum)[30:].max()), float(_c.wilson(0, 993)[2]))
"""


def ok(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg)
    if not cond:
        fallos.append(msg)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def env(out=None):
    e = dict(os.environ, MURO_ALLOW_CLINICAL="1", NUMBA_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1",
             OPENBLAS_NUM_THREADS="1", PYTHONHASHSEED="0")
    if out:
        e["HIGADO_OUT"] = out
    return e


def corre(paso, out=None):
    return subprocess.run([PY, "-I", os.path.join(DIR, "dianas.py"), paso], env=env(out), capture_output=True, text=True, timeout=1800)


def main():
    if not os.path.exists(PY):
        print("SKIP: falta el entorno nescore")
        return 77
    r = subprocess.run([PY, "-I", "-c", SINTETICO % DIR], capture_output=True, text=True, env=env())
    vals = [float(x) for x in r.stdout.split()] if r.returncode == 0 else []
    ok(len(vals) == 3 and vals[0] < 1e-6 and vals[1] < 1e-6, "deconvolución recupera pesos y expresión tumoral de una mezcla sintética"
       + ("" if r.returncode == 0 else ": " + r.stderr[-300:]))
    ok(len(vals) == 3 and 0 < vals[2] < 0.005, "Wilson k=0 de 993 da cota superior > 0 y < 0,5 %")
    c = corre("comprueba")
    if c.returncode == 77:
        print("SKIP: sin cachés del cierre en esta máquina (" + c.stdout.strip() + ")")
        return 77 if not fallos else 1
    ok(c.returncode == 0, "comprueba: cachés presentes")
    hashes = []
    for i in (1, 2):
        d = tempfile.mkdtemp(prefix=f"higado_dianas_rep{i}_")
        r = corre("run", d)
        ok(r.returncode == 0, f"ejecución {i} termina sin error" + ("" if r.returncode == 0 else ": " + r.stderr[-400:]))
        ok(not re.search(r"[ACGT]{16,}", r.stdout + r.stderr), f"ejecución {i}: stdout sin barcodes")
        ok(not re.search(r"B?\d{2}-\d{5}_M\d", r.stdout + r.stderr), f"ejecución {i}: stdout sin ids de bloque")
        hashes.append({f: sha(os.path.join(d, f)) for f in sorted(os.listdir(d))})
        shutil.rmtree(d, ignore_errors=True)  # derivados clínicos: no se dejan fuera de la zona clínica
    ok(hashes[0] == hashes[1] and len(hashes[0]) >= 4, "dos ejecuciones → sha256 idénticos (%d salidas)" % len(hashes[0]))
    man = corre("manifiesto_ruta")
    ruta = man.stdout.strip().splitlines()[-1] if man.returncode == 0 and man.stdout.strip() else ""
    if ruta and os.path.exists(ruta):
        canon = json.load(open(ruta))["salidas"]
        ok(all(hashes[0].get(f) == h for f, h in canon.items()), "las salidas coinciden con el MANIFIESTO canónico")
    else:
        print("aviso: sin MANIFIESTO canónico todavía")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
