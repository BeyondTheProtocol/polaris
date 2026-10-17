"""Diagnóstico agregado: perfil CNV medio por cromosoma y clúster Leiden (×100), y detección epitelial
(≥1 UMI en cualquiera de EPCAM/KRT8/KRT18/KRT19) por clúster. Solo agregados."""
import numpy as np
import pandas as pd
import scanpy as sc

from _comun import TEJIDOS, ids_de, out


def run(*a):
    pd.set_option("display.width", 300)
    for t in TEJIDOS:
        ad = sc.read_h5ad(out(f"cnv_{t}.h5ad"))
        X = ad.obsm["X_cnv"]
        X = X.toarray() if hasattr(X, "toarray") else X
        chrpos = ad.uns["cnv"]["chr_pos"]
        orden = sorted(chrpos.items(), key=lambda kv: kv[1])
        bordes = [p for _, p in orden] + [X.shape[1]]
        cols = {}
        for i, (ch, p) in enumerate(orden):
            cols[ch.replace("chr", "")] = X[:, p:bordes[i + 1]].mean(axis=1)
        df = pd.DataFrame(cols, index=ad.obs_names)
        m = (df.groupby(ad.obs["leiden"].values, observed=True).mean() * 100).round(1)
        m.insert(0, "tipo", ad.obs.groupby("leiden", observed=True)["tipo"].first())
        ids = [g for g in ids_de(ad, ["EPCAM", "KRT8", "KRT18", "KRT19"]).values() if g]
        c = ad.layers["counts"][:, [ad.var_names.get_loc(g) for g in ids]]
        any1 = np.asarray((c > 0).sum(axis=1)).ravel() >= 1
        m.insert(1, "epi_any", pd.Series(any1, index=ad.obs_names).groupby(ad.obs["leiden"].values, observed=True).mean().round(2))
        print("==", t)
        print(m.to_string())
