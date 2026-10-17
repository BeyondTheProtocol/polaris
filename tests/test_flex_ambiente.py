#!/usr/bin/env python3
"""test_flex_ambiente.py — descontaminación de ARN ambiental del Flex (tools/flex_analisis/ambiente/).

1. Funciones puras (sin datos): Jaccard, correlación por fila, umbral p98,5 en H1 y FP en H2.
2. SoupX + DecontX sobre datos SINTÉTICOS con verdad conocida (sin datos de la paciente): caída de luminales en
   inmunes >= 75 % y retención >= 80 %; dos ejecuciones → resultados idénticos (determinismo).
3. Muro: los scripts solo citan una URL externa (la referencia pública de 10x) y el stdout no lleva barcodes.
4. Si existe el MANIFIESTO del ambiente en esta máquina: las salidas registradas siguen teniendo su sha256.
Sin nescore, sin el entorno ambiente-r o sin celda → SKIP visible (77), nunca PASS.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AMB = os.path.join(ROOT, "tools", "flex_analisis", "ambiente")
PY = os.path.expanduser("~/.claude-science/conda/envs/nescore/bin/python")
RS = os.path.expanduser("~/.claude-science/conda/envs/ambiente-r/bin/Rscript")
fallos = []


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


ENV = dict(os.environ, MURO_ALLOW_CLINICAL="1", NUMBA_NUM_THREADS="1", OMP_NUM_THREADS="1",
           MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", PYTHONHASHSEED="0")

PURAS = r"""
import sys, numpy as np
sys.path.insert(0, %r)
import _a, _amb_final as F
a = np.array([1,1,0,0,1], bool); b = np.array([1,0,0,1,1], bool)
assert abs(F.jacc(a, b) - 0.5) < 1e-9
C = np.array([[1.,2,3],[3,2,1]]); v = np.array([1.,2,3])
r = F.corr_con(C, v); assert abs(r[0]-1) < 1e-9 and abs(r[1]+1) < 1e-9
n = 2000; rng = np.random.default_rng(1); s = rng.normal(size=n)
g = {"h1": np.arange(n) < 1000, "h2": np.arange(n) >= 1000, "pp": np.zeros(n, bool), "pool": np.zeros(n, bool),
     "no_ref": np.zeros(n, bool), "hep": np.zeros(n, bool)}
thr, call, m = F.llamada_v4(s, g)
assert abs((s[:1000] > thr).mean() - 0.015) < 0.002, "umbral p98,5 en H1"
assert m["FP_H2"]["n"] == 1000 and 0 < m["FP_H2"]["frac"] < 0.04
print("PURAS_OK")
"""


def main():
    if not os.path.exists(PY):
        print("SKIP: falta el entorno nescore")
        return 77
    r = subprocess.run([PY, "-I", "-c", PURAS % AMB], capture_output=True, text=True, env=ENV)
    ok(r.returncode == 0 and "PURAS_OK" in r.stdout, "funciones puras (Jaccard, correlación, umbral H1/FP H2)"
       + ("" if r.returncode == 0 else ": " + r.stderr[-300:]))
    # 3. muro: URLs en los scripts
    urls = set()
    for f in os.listdir(AMB):
        if f.endswith((".py", ".R")):
            urls |= set(re.findall(r"https?://[^\s\"')]+", open(os.path.join(AMB, f)).read()))
    ok(all(u.startswith("https://cf.10xgenomics.com/") for u in urls), "egress: solo la referencia pública de 10x (%d URL)" % len(urls))
    # 2. sintético
    if not os.path.exists(RS):
        print("SKIP parcial: falta el entorno ambiente-r")
        return 1 if fallos else 77
    c = subprocess.run([RS, "--vanilla", "-e", "cat(requireNamespace('celda', quietly=TRUE) && requireNamespace('SoupX', quietly=TRUE))"],
                       capture_output=True, text=True)
    if "TRUE" not in c.stdout:
        print("SKIP parcial: falta celda o SoupX en ambiente-r")
        return 1 if fallos else 77
    res = []
    for i in (1, 2):
        d = tempfile.mkdtemp(prefix=f"amb_sint{i}_")
        s = subprocess.run([PY, "-I", os.path.join(AMB, "ambiente.py"), "sintetico", d], capture_output=True, text=True,
                           env=ENV, timeout=1200)
        ok(s.returncode == 0, f"sintético {i}: SoupX y DecontX cumplen caída >= 75 % y retención >= 80 %"
           + ("" if s.returncode == 0 else ": " + (s.stdout + s.stderr)[-500:]))
        ok(not re.search(r"[ACGT]{16,}", s.stdout + s.stderr), f"sintético {i}: stdout sin barcodes")
        p = os.path.join(d, "sintetico.json")
        if os.path.exists(p):
            j = json.load(open(p))
            j.get("info_R", {}).pop("soupx_segundos", None)
            res.append({k: v for k, v in j.items() if k in ("soupx", "soupx_auto", "decontx")})
    ok(len(res) == 2 and res[0] == res[1], "sintético: dos ejecuciones → mismas métricas (determinismo con semilla)")
    # 4. manifiesto
    man = os.path.expanduser("~/Clinico-PRIVADO/flex/ambiente/MANIFIESTO_AMBIENTE.json")
    if os.path.exists(man):
        m = json.load(open(man))
        base = os.path.dirname(man)
        malos = [f for f, h in m["salidas"].items() if not os.path.exists(os.path.join(base, f)) or sha(os.path.join(base, f)) != h]
        ok(not malos, "MANIFIESTO_AMBIENTE: %d salidas con su sha256" % len(m["salidas"]))
    else:
        print("SKIP parcial: sin MANIFIESTO_AMBIENTE en esta máquina")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
