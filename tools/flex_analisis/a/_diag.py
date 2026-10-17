"""Diagnóstico agregado por clúster Leiden (n, tipo, fracción epitelial-célula, score CNV medio,
fracción aberrante inferCNV, genes marcadores principales). Solo agregados."""
import numpy as np
import pandas as pd
import scanpy as sc

from _comun import TEJIDOS, ids_de, out

GENES = ["EPCAM", "KRT8", "KRT18", "KRT19", "KRT7", "ESR1", "GATA3", "FOXA1", "TFF1", "CHGA", "SYP",
         "{{DIANA3}}", "{{DIANA4}}", "NEUROD1", "MKI67", "VIM", "PTPRC", "ALB"]


def run(*a):
    pd.set_option("display.width", 250)
    for t in TEJIDOS:
        ad = sc.read_h5ad(out(f"cnv_{t}.h5ad"))
        g = ad.obs.groupby("leiden", observed=True)
        df = pd.DataFrame({"n": g.size(), "tipo": g["tipo"].first(),
                           "frac_epi": g["epitelial_celula"].mean(),
                           "cnv_media": g["cnv_score_celula"].mean(),
                           "frac_aberr": g["infercnv_aberrante"].mean(),
                           "UMI_med": g["total_counts"].median()})
        ids = ids_de(ad, GENES)
        c = ad.layers["counts"]
        for s, gid in ids.items():
            if gid is None:
                continue
            pos = np.asarray((c[:, ad.var_names.get_loc(gid)] > 0).todense()).ravel()
            df[s] = pd.Series(pos, index=ad.obs_names).groupby(ad.obs["leiden"].values, observed=True).mean()
        print("==", t)
        print(df.round(2).to_string())
        sc.tl.rank_genes_groups(ad, "leiden", method="wilcoxon", n_genes=8)
        top = pd.DataFrame(ad.uns["rank_genes_groups"]["names"])
        sym = dict(zip(ad.var_names, ad.var["simbolo"]))
        for col in top.columns:
            print(f"  {col}: " + ", ".join(sym.get(x, x) for x in top[col]))
        # cnv leiden
        g2 = ad.obs.groupby("cnv_leiden", observed=True)
        d2 = pd.DataFrame({"n": g2.size(), "frac_ref": g2["tipo"].apply(lambda s: s.isin(
            ["T", "NK", "B", "plasmatica", "mieloide", "neutrofilo", "mastocito", "endotelio"]).mean()),
            "frac_hep": g2["tipo"].apply(lambda s: (s == "hepatocito").mean()),
            "frac_epi": g2["epitelial_celula"].mean(), "cnv_media": g2["cnv_score_celula"].mean()})
        print(" cnv_leiden:")
        print(d2.round(3).sort_values("cnv_media").to_string())
