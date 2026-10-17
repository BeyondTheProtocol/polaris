"""Paso «dobletes»: exporta las cuentas crudas (caché del cierre) a binario y lanza dobletes.R (scDblFinder + scCDC).

Todo dentro del sandbox sin red heredado del proceso padre. Las células van por índice de fila de la caché del cierre:
ni barcodes ni identificadores de bloque salen a disco ni a stdout. Lectores de los resultados para la etapa final.
"""
import os
import subprocess

import numpy as np
import pandas as pd
import scipy.sparse as sp

import _hc

DIR = "dobletes"


def _exporta(t):
    import _final as F
    T_ = F.T(t)
    X = T_.X.tocsr().astype(np.int32)
    X.sort_indices()
    d = _hc.cached(DIR)
    np.asarray(X.indptr, dtype="<i4").tofile(os.path.join(d, f"{t}_p.bin"))
    np.asarray(X.indices, dtype="<i4").tofile(os.path.join(d, f"{t}_i.bin"))
    np.asarray(X.data, dtype="<i4").tofile(os.path.join(d, f"{t}_x.bin"))
    with open(os.path.join(d, f"{t}_dims.txt"), "w") as f:
        f.write(f"{X.shape[1]}\n{X.shape[0]}\n{X.nnz}\n")
    with open(os.path.join(d, f"{t}_genes.txt"), "w") as f:
        f.write("\n".join(T_.o["gene_ids"].astype(str)) + "\n")
    with open(os.path.join(d, f"{t}_tipo.txt"), "w") as f:
        f.write("\n".join(T_.tipo.astype(str)) + "\n")
    return X.shape


def run(*args):
    modos = list(args) or ["scdbl", "sccdc"]
    for t in ("higado", "hueso"):
        print("exportada", t, _exporta(t))
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    env["PATH"] = os.path.join(_hc.ENVS, "dobletes", "bin") + os.pathsep + env.get("PATH", "")
    for m in modos:
        r = subprocess.run([_hc.RSCRIPT, "--vanilla", os.path.join(_hc.AQUI, "dobletes.R"), _hc.cached(DIR), m],
                           env=env, capture_output=True, text=True)
        # el R solo imprime agregados; los avisos de R van a un log en caché, no a stdout
        with open(_hc.cachep(DIR, f"log_{m}.txt"), "w") as f:
            f.write(r.stdout + "\n--- stderr ---\n" + r.stderr)
        print(r.stdout.strip())
        if r.returncode != 0:
            print(f"FALLO dobletes.R {m} (rc {r.returncode}); ver log en caché")
            return 1
    return 0


# ---------------------------------------------------------------- lectores
def scdbl(nombre="dbr006"):
    p = _hc.cachep(DIR, f"scdbl_{nombre}.csv")
    d = pd.read_csv(p, keep_default_na=False)
    assert (d["idx"].values == np.arange(len(d))).all()
    return {"doblete": d["clase"].values == "doublet", "score": d["score"].values.astype(float),
            "origen": d["origen"].astype(str).values, "ambiguo": d["ambiguo"].astype(str).values == "TRUE", "sha256": _hc.sha256(p)}


def sccdc(t):
    """(matriz corregida células x genes CSR o None, GCG en ids, mensaje de error o None)."""
    d = _hc.cached(DIR)
    err = os.path.join(d, f"sccdc_{t}_error.txt")
    if os.path.exists(err):
        return None, [], open(err).read().strip()
    dims = [int(x) for x in open(os.path.join(d, f"sccdc_{t}_dims.txt")).read().split()]
    p = np.fromfile(os.path.join(d, f"sccdc_{t}_p.bin"), dtype="<i4")
    i = np.fromfile(os.path.join(d, f"sccdc_{t}_i.bin"), dtype="<i4")
    x = np.fromfile(os.path.join(d, f"sccdc_{t}_x.bin"), dtype="<i4")
    M = sp.csc_matrix((x, i, p), shape=(dims[0], dims[1])).T.tocsr().astype(np.int32)
    M.eliminate_zeros()
    M.sort_indices()
    g = pd.read_csv(os.path.join(d, f"sccdc_{t}_gcg.csv"), index_col=0)
    return M, [str(s) for s in g.index], None
