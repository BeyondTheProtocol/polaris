"""Diagnóstico agregado: distribución de la correlación CNV por clúster (percentiles). Solo agregados."""
import pandas as pd
import scanpy as sc

from _comun import TEJIDOS, out


def run(*a):
    pd.set_option("display.width", 250)
    for t in TEJIDOS:
        ad = sc.read_h5ad(out(f"cnv_{t}.h5ad"))
        gb = ad.obs.groupby("leiden", observed=True)
        g = gb["cnv_corr"]
        df = pd.DataFrame({"n": g.size(), "tipo": gb["tipo"].first(),
                           "p10": g.quantile(.1), "p50": g.median(), "p90": g.quantile(.9),
                           "frac_aberr": gb["infercnv_aberrante"].mean(),
                           "UMI_med": gb["total_counts"].median()})
        print("==", t, ad.uns.get("epi_cluster_regla"))
        print(df.round(2).to_string())
        print("consenso x100:", {k: round(v * 100, 1) for k, v in ad.uns["cnv_consenso_por_cromosoma"].items()})
