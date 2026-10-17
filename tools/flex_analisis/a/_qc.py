"""Paso 1 (espec.): QC por muestra. 3 MAD en escala log (UMI y genes, ambas colas), % MT (cola alta,
si hay sondas MT), Scrublet (semilla 20261016) sobre las células que pasan MAD.
Además (paso 2, parte pseudobulk): CP10k pseudobulk de los programas hepatocitario y óseo.
Salida: analisis_A/qc_<tejido>.h5ad (cuentas crudas en X) y analisis_A/qc.json. stdout: agregados.
"""
import numpy as np
import scanpy as sc
import scipy.sparse as sp

from _comun import SEED, TEJIDOS, carga, guarda_json, ids_de, out

HEP = ["ALB", "APOA1", "SERPINA1", "HP", "TF"]
OSEO = ["COL1A1", "SPP1", "ACP5", "CTSK"]


def _mad_lims(x, colas=("baja", "alta"), k=3.0):
    med = np.median(x)
    mad = np.median(np.abs(x - med)) * 1.4826
    lo = med - k * mad if "baja" in colas else -np.inf
    hi = med + k * mad if "alta" in colas else np.inf
    return float(lo), float(hi), float(med), float(mad)


def pseudobulk_cp10k(ad, simbolos):
    ids = ids_de(ad, simbolos)
    tot = float(ad.X.sum())
    res = {}
    for s, g in ids.items():
        res[s] = None if g is None else float(ad[:, g].X.sum()) / tot * 1e4
    return res


def run(*a):
    resumen = {}
    for t in TEJIDOS:
        ad = carga(t)
        n0 = ad.n_obs
        mt = ad.var["simbolo"].str.upper().str.startswith("MT-")
        ad.var["mt"] = mt.values
        sc.pp.calculate_qc_metrics(ad, qc_vars=["mt"], inplace=True, percent_top=None, log1p=True)
        r = {"n_inicial": n0, "n_sondas_MT": int(mt.sum())}
        lo_u, hi_u, med_u, mad_u = _mad_lims(ad.obs["log1p_total_counts"].values)
        lo_g, hi_g, med_g, mad_g = _mad_lims(ad.obs["log1p_n_genes_by_counts"].values)
        keep = ((ad.obs["log1p_total_counts"] >= lo_u) & (ad.obs["log1p_total_counts"] <= hi_u)
                & (ad.obs["log1p_n_genes_by_counts"] >= lo_g) & (ad.obs["log1p_n_genes_by_counts"] <= hi_g))
        r["umbral_UMI"] = [float(np.expm1(lo_u)), float(np.expm1(hi_u))]
        r["umbral_genes"] = [float(np.expm1(lo_g)), float(np.expm1(hi_g))]
        r["mediana_UMI_inicial"] = float(np.median(ad.obs["total_counts"]))
        r["mediana_genes_inicial"] = float(np.median(ad.obs["n_genes_by_counts"]))
        r["excluidas_UMI_genes"] = int((~keep).sum())
        if mt.sum() > 0:
            lo_m, hi_m, _, _ = _mad_lims(ad.obs["pct_counts_mt"].values, colas=("alta",))
            km = ad.obs["pct_counts_mt"] <= hi_m
            r["umbral_pct_MT"] = hi_m
            r["excluidas_MT_adicionales"] = int((keep & ~km).sum())
            keep &= km
        else:
            r["umbral_pct_MT"] = "sin sondas MT en el panel: no aplicable"
        ad = ad[keep.values].copy()
        sc.pp.scrublet(ad, random_state=SEED)
        nd = int(ad.obs["predicted_doublet"].sum())
        r["scrublet_dobletes"] = nd
        r["scrublet_umbral"] = float(ad.uns["scrublet"]["threshold"]) if "threshold" in ad.uns.get("scrublet", {}) else None
        ad = ad[~ad.obs["predicted_doublet"].values].copy()
        r["n_final"] = ad.n_obs
        r["mediana_UMI_final"] = float(np.median(ad.obs["total_counts"]))
        r["mediana_genes_final"] = float(np.median(ad.obs["n_genes_by_counts"]))
        r["pseudobulk_CP10k_hepatocitario"] = pseudobulk_cp10k(ad, HEP)
        r["pseudobulk_CP10k_oseo"] = pseudobulk_cp10k(ad, OSEO)
        if not sp.issparse(ad.X):
            ad.X = sp.csr_matrix(ad.X)
        ad.write_h5ad(out(f"qc_{t}.h5ad"), compression="gzip")
        resumen[t] = r
        print(f"[{t}] n {n0} -> {r['n_final']} | UMI umbral {r['umbral_UMI'][0]:.0f}-{r['umbral_UMI'][1]:.0f} "
              f"| genes {r['umbral_genes'][0]:.0f}-{r['umbral_genes'][1]:.0f} | MT sondas {r['n_sondas_MT']} "
              f"| excl UMI/genes {r['excluidas_UMI_genes']} | dobletes {nd} "
              f"| mediana UMI final {r['mediana_UMI_final']:.0f}, genes {r['mediana_genes_final']:.0f}")
        print(f"   pseudobulk CP10k hep {r['pseudobulk_CP10k_hepatocitario']}")
        print(f"   pseudobulk CP10k oseo {r['pseudobulk_CP10k_oseo']}")
    p = guarda_json(resumen, "qc.json")
    print("qc.json escrito")
