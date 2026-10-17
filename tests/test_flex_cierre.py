#!/usr/bin/env python3
"""test_flex_cierre.py — reproducibilidad del cierre técnico del Flex (tools/flex_analisis/cierre/).

Corre la etapa FINAL (todo el cálculo desde las cachés de PREP) DOS veces en directorios temporales y exige:
  1. sha256 idénticos de todas las salidas entre las dos ejecuciones;
  2. que coincidan con las salidas canónicas registradas en MANIFIESTO_CIERRE.json;
  3. que el stdout no lleve barcodes (ninguna racha de >= 16 nucleótidos) ni ids de bloque;
  4. unidades: Wilson con k=0 da cota superior > 0 y con k=n cota inferior < 1 (funciones puras, sin datos).
Sin el entorno nescore o sin cachés (otra máquina, CI) → SKIP visible (77), nunca PASS.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CIERRE = os.path.join(ROOT, "tools", "flex_analisis", "cierre")
PY = os.path.expanduser("~/.claude-science/conda/envs/nescore/bin/python")
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


def corre(paso, out=None):
    env = dict(os.environ, MURO_ALLOW_CLINICAL="1", NUMBA_NUM_THREADS="1", OMP_NUM_THREADS="1",
               MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", PYTHONHASHSEED="0")
    if out:
        env["CIERRE_OUT"] = out
    return subprocess.run([PY, "-I", os.path.join(CIERRE, "cierre.py"), paso], env=env, capture_output=True, text=True,
                          timeout=1800)


def main():
    # 4. funciones puras (no necesitan datos; sí el entorno)
    if not os.path.exists(PY):
        print("SKIP: falta el entorno nescore")
        return 77
    r = subprocess.run([PY, "-I", "-c", "import sys; sys.path.insert(0, %r); import _c; "
                        "print(_c.wilson(0, 68)[2], _c.wilson(68, 68)[1], _c.wilson(0, 13695)[2])" % CIERRE],
                       capture_output=True, text=True, env=dict(os.environ, MURO_ALLOW_CLINICAL="1"))
    vals = [float(x) for x in r.stdout.split()] if r.returncode == 0 else []
    ok(len(vals) == 3 and 0.04 < vals[0] < 0.06 and 0.94 < vals[1] < 0.96 and 0 < vals[2] < 0.001,
       "Wilson k=0/k=n da cotas no degeneradas (68: 0,053 / 0,947; 13.695: 0,0003)")
    c = corre("comprueba")
    if c.returncode == 77:
        print("SKIP: sin cachés del cierre en esta máquina (" + c.stdout.strip() + ")")
        return 77
    ok(c.returncode == 0, "comprueba: cachés presentes")
    hashes = []
    for i in (1, 2):
        d = tempfile.mkdtemp(prefix=f"cierre_rep{i}_")
        r = corre("final", d)
        ok(r.returncode == 0, f"ejecución {i} termina sin error" + ("" if r.returncode == 0 else ": " + r.stderr[-400:]))
        ok(not re.search(r"[ACGT]{16,}", r.stdout + r.stderr), f"ejecución {i}: stdout sin barcodes")
        ok(not re.search(r"B?\d{2}-\d{5}_M\d", r.stdout + r.stderr), f"ejecución {i}: stdout sin ids de bloque")
        hashes.append({f: sha(os.path.join(d, f)) for f in sorted(os.listdir(d))})
    ok(hashes[0] == hashes[1] and len(hashes[0]) >= 4, "dos ejecuciones → sha256 idénticos (%d salidas)" % len(hashes[0]))
    if hashes[0] != hashes[1]:
        for f in hashes[0]:
            if hashes[0].get(f) != hashes[1].get(f):
                print("   distinto:", f)
    man = corre("manifiesto_ruta")
    ruta = man.stdout.strip().splitlines()[-1] if man.returncode == 0 and man.stdout.strip() else ""
    if ruta and os.path.exists(ruta):
        canon = json.load(open(ruta))["salidas"]
        comunes = [f for f in hashes[0] if f in canon]
        ok(len(comunes) >= 4 and all(hashes[0][f] == canon[f] for f in comunes),
           "las salidas coinciden con MANIFIESTO_CIERRE.json (%d ficheros)" % len(comunes))
    else:
        ok(False, "MANIFIESTO_CIERRE.json canónico presente")
    print(f"\n{'ROJO' if fallos else 'VERDE'}: test_flex_cierre ({len(fallos)} fallos)")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
