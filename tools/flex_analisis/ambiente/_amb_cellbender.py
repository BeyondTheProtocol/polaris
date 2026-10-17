"""CellBender remove-background (GitHub master fijado, CPU, LOCAL) sobre el raw del hígado (REGLAS_HIGADO_v4).

Subpasos: prueba (2 y 3 épocas, extrapola a 150) | lanza (150 épocas; se llama en segundo plano) | importa (h5 de salida
→ X_higado_cellbender_fpr<x>.npz con las células y el orden del cierre). Nunca cómputo externo.
"""
import glob
import re
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np
import scipy.sparse as sp

import _a
from _a import TC, _c, cachep

CB = os.path.join(_a.ENVS, "cellbender", "bin", "cellbender")
FPR = ["0.0", "0.01", "0.05"]


def _cmd(salida, epocas, tejido="higado"):
    # Sin --seed: la CLI instalada no lo admite (la regla lo pedía). Declarado post hoc en
    # ADENDA_POST_HOC_2026-10-17.json (CELLBENDER_SIN_SEED_Y_PRUEBA_EPOCAS).
    p = {"higado": ("19000", "45000"), "hueso": ("2500", "20000")}[tejido]
    return [CB, "remove-background", "--input", TC.ruta(tejido, "_raw_feature_barcode_matrix.h5"), "--output", salida,
            "--cpu-threads", "10", "--expected-cells", p[0], "--total-droplets-included", p[1],
            "--fpr", *FPR, "--epochs", str(epocas), "--checkpoint-mins", "60"]  # enmienda operativa (no analítica): 600 -> 60 para no perderlo todo si se corta


def _env():
    """TMPDIR dentro de la caché clínica: nada derivado del dato en /var/folders."""
    t = cachep("tmp_cellbender")
    os.makedirs(t, exist_ok=True)
    return dict(os.environ, TMPDIR=t)


def limpia():
    for d in glob.glob(cachep("cb_prueba_*")) + [cachep("tmp_cellbender")]:
        if os.path.isdir(d):
            shutil.rmtree(d)
            print("borrado intermedio", os.path.basename(d))


def prueba():
    res = {}
    for ep in (2, 3):
        d = cachep(f"cb_prueba_{ep}")
        os.makedirs(d, exist_ok=True)
        t0 = time.time()
        r = subprocess.run(_cmd(os.path.join(d, "out.h5"), ep), cwd=d, capture_output=True, text=True, env=_env())
        res[ep] = time.time() - t0
        if r.returncode != 0:
            print(r.stderr[-3000:])
            raise SystemExit(f"CellBender falló con {ep} épocas")
        log = "".join(open(f).read() for f in glob.glob(os.path.join(d, "*.log")))
        res[f"log_{ep}_ultimas"] = "\n".join([x for x in log.splitlines() if "[epoch" in x][-3:])
        shutil.rmtree(d)
    por_epoca = res[3] - res[2]
    fijo = res[2] - 2 * por_epoca
    res["segundos_por_epoca"] = por_epoca
    res["segundos_fijos"] = fijo
    res["horas_150"] = (fijo + 150 * por_epoca) / 3600
    with open(cachep("cb_prueba.json"), "w") as f:
        json.dump(res, f, indent=1)
    print("CellBender prueba: 2 ép", round(res[2]), "s; 3 ép", round(res[3]), "s; por época", round(por_epoca), "s; 150 ép ≈",
          round(res["horas_150"], 1), "h")


def lanza():
    d = cachep("cellbender_higado")
    os.makedirs(d, exist_ok=True)
    t0 = time.time()
    with open(os.path.join(d, "stdout.txt"), "w") as fo:
        r = subprocess.run(_cmd(os.path.join(d, "out.h5"), 150), cwd=d, stdout=fo, stderr=subprocess.STDOUT, env=_env())
    with open(os.path.join(d, "tiempo.json"), "w") as f:
        json.dump({"segundos": time.time() - t0, "retorno": r.returncode}, f)
    print("CellBender terminado:", r.returncode, round((time.time() - t0) / 3600, 2), "h")


def importa():
    import h5py
    d = cachep("cellbender_higado")
    o = np.load(_c.cachep("obs_higado.npz"), allow_pickle=True)
    import scanpy as sc  # noqa: F401
    bcs = TC.carga("higado").obs_names.values.astype(str)
    for fpr in FPR:
        cand = [p for p in glob.glob(os.path.join(d, "out*.h5")) if f"FPR_{fpr}" in p and "filtered" not in p]
        if not cand:
            print("sin salida para FPR", fpr)
            continue
        M, ids, _ = TC.stream_h5(cand[0], bcs)
        pos = {g: j for j, g in enumerate(ids)}
        jj = np.array([pos[g] for g in o["gene_ids"]])
        Y = M.tocsc()[:, jj].tocsr().astype(np.int32)
        Y.sort_indices()
        sp.save_npz(cachep(f"X_higado_cellbender_fpr{fpr}.npz"), Y, compressed=True)
        X0 = sp.load_npz(_c.cachep("X_higado.npz"))
        print("importado FPR", fpr, "UMI retenidas", round(Y.sum() / X0.sum(), 3))
        with h5py.File(cand[0], "r") as f:
            print("  claves:", list(f.keys())[:8])


def estado():
    for d in sorted(glob.glob(cachep("cb_prueba_*")) + glob.glob(cachep("cellbender_higado"))):
        for f in glob.glob(os.path.join(d, "*.log")) + glob.glob(os.path.join(d, "stdout.txt")):
            ln = open(f).read().splitlines()
            print(os.path.basename(d), os.path.basename(f), len(ln), "líneas")
            print("\n".join(x for x in ln[-6:] if not re.search(r"[ACGT]{16,}", x)))


def run(sub="prueba", *a):
    {"prueba": prueba, "lanza": lanza, "importa": importa, "estado": estado, "limpia": limpia}[sub]()
