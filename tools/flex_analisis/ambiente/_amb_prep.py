"""PREP del ambiente: exporta células (cachés del cierre) y gotas vacías (raw) a binario para R, corre SoupX y
DecontX (_amb_descont.R, entorno ambiente-r) y deja X_<tejido>_<metodo>.npz en ~/Clinico-PRIVADO/flex/ambiente/cache/.
No calcula ninguna cifra de resultado. Borra los ficheros de intercambio al terminar.
"""
import gc
import json
import os
import resource
import shutil
import subprocess
import time

import numpy as np
import scipy.sparse as sp

import _a
from _a import TC, _c, cachep

METODOS = ("soupx", "soupx_auto", "decontx")


def vacias(t):
    """Misma definición que tribunal/_suelo.ambiente: no llamados con 10 <= UMI <= 100."""
    bc, tt = TC.barcodes_raw(t)
    llam = set(TC.llamadas(t))
    sel = np.array([b for b, x in zip(bc, tt) if 10 <= x <= 100 and b not in llam])
    del bc, tt
    gc.collect()
    return sel


def _escribe_csr(d, nombre, M):
    M = sp.csr_matrix(M)
    M.sort_indices()
    M.indptr.astype(np.int64).tofile(os.path.join(d, nombre + "_p.bin"))
    M.indices.astype(np.int32).tofile(os.path.join(d, nombre + "_i.bin"))
    M.data.astype(np.float64).tofile(os.path.join(d, nombre + "_x.bin"))
    return M.shape


def _lee_csc_r(d, nombre, ng, nc):
    """R escribe genes × células en CSC (p sobre células): equivale a CSR células × genes."""
    p = np.fromfile(os.path.join(d, nombre + "_p.bin"), dtype=np.int64)
    i = np.fromfile(os.path.join(d, nombre + "_i.bin"), dtype=np.int32)
    x = np.fromfile(os.path.join(d, nombre + "_x.bin"), dtype=np.float64)
    return sp.csr_matrix((x, i, p), shape=(nc, ng))


def run(*args):
    tejidos = args or TC.TEJIDOS
    rbin = os.path.join(_a.ENVS, "ambiente-r", "bin", "Rscript")
    log = {}
    for t in tejidos:
        X = sp.load_npz(_c.cachep(f"X_{t}.npz")).tocsr()
        o = np.load(_c.cachep(f"obs_{t}.npz"), allow_pickle=True)
        gids, sym, tipo = o["gene_ids"].astype(str), o["simbolo"].astype(str), o["tipo"].astype(str)
        sel = vacias(t)
        E, ids, _ = TC.stream_h5(TC.ruta(t, "_raw_feature_barcode_matrix.h5"), sel)
        pos = {g: j for j, g in enumerate(ids)}
        jj = np.array([pos.get(g, -1) for g in gids])
        assert (jj >= 0).mean() > 0.99, "genes del raw y de la caché no casan"
        E = E.tocsc()[:, np.maximum(jj, 0)].tocsr()
        if (jj < 0).any():
            E = (E @ sp.diags((jj >= 0).astype(float))).tocsr()
        E.eliminate_zeros()
        assert abs(E.sum() - int(o["umi_vacios"])) / max(int(o["umi_vacios"]), 1) < 0.01, "vacíos distintos del cierre"
        d = cachep(f"intercambio_{t}")
        os.makedirs(d, exist_ok=True)
        _escribe_csr(d, "celulas", X)
        _escribe_csr(d, "vacias", E)
        with open(os.path.join(d, "meta.json"), "w") as f:
            json.dump({"genes": gids.tolist(), "simbolos": sym.tolist(), "tipo": tipo.tolist(),
                       "n_celulas": int(X.shape[0]), "n_vacias": int(E.shape[0]), "n_genes": int(X.shape[1]),
                       "semilla": _a.SEED, "genes_estima": json.load(open(_a.REGLAS))["GENES_ESTIMA"],
                       "nulo": ["NK", "endotelio", "mieloide"]}, f)
        print(f"PREP {t}: células {X.shape[0]}, vacías {E.shape[0]} (UMI {int(E.sum())}), genes {X.shape[1]}")
        del E
        gc.collect()
        t0 = time.time()
        r = subprocess.run([rbin, "--vanilla", os.path.join(_a.AQUI, "_amb_descont.R"), d], capture_output=True, text=True)
        dt = time.time() - t0
        print(r.stdout[-4000:])
        if r.returncode != 0:
            print(r.stderr[-4000:])
            raise SystemExit(f"R falló en {t}")
        info = json.load(open(os.path.join(d, "info_r.json")))
        info["segundos_R_total"] = dt
        info["umi_retenidas"] = {}
        for m in METODOS:
            Y = _lee_csc_r(d, m, X.shape[1], X.shape[0])
            Y.data = np.rint(Y.data)
            Y.eliminate_zeros()
            Y = Y.astype(np.int32)
            Y.sort_indices()
            sp.save_npz(cachep(f"X_{t}_{m}.npz"), Y, compressed=True)
            info["umi_retenidas"][m] = float(Y.sum() / X.sum())
        cpath = os.path.join(d, "contaminacion_decontx.bin")
        if os.path.exists(cpath):
            np.save(cachep(f"decontx_contaminacion_{t}.npy"), np.fromfile(cpath, dtype=np.float64))
        log[t] = info
        print(f"PREP {t}: R {dt:.0f} s; UMI retenidas", {k: round(v, 3) for k, v in info["umi_retenidas"].items()})
        shutil.rmtree(d)
    log["rss_pico_python_MB"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6
    prev = json.load(open(cachep("prep_log.json"))) if os.path.exists(cachep("prep_log.json")) else {}
    prev.update(log)
    with open(cachep("prep_log.json"), "w") as f:
        json.dump(prev, f, indent=1, sort_keys=True)
    print("prep_log.json actualizado")
