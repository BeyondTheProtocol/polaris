"""Prueba A/B de referencia purificada (REGLA_REF_PURIFICADA.json, fijada antes de correrla).

  purificada prepara -> inferCNV (hígado) con referencia purificada + entrada CopyKAT del trozo 0
                        en copykat_higado_pur/ (mismas células no-referencia, solo cambia la referencia)
  purificada evalua  -> aplica el criterio de éxito y escribe purificada.json
Solo agregados en stdout.
"""
import os

import numpy as np
import pandas as pd
import scanpy as sc
import scipy.io

from _comun import SEED, guarda_json, ids_de, out
from _malignas import REF_TIPOS, posiciones

PURGA = ["EPCAM", "KRT8", "KRT18", "KRT19", "ESR1", "FOXA1"]


def ref_pur(ad):
    ids = [g for g in ids_de(ad, PURGA).values() if g]
    c = ad.layers["counts"][:, [ad.var_names.get_loc(g) for g in ids]]
    limpio = np.asarray(c.sum(axis=1)).ravel() == 0
    return ad.obs["tipo"].isin(REF_TIPOS).values & limpio


def prepara():
    import infercnvpy as cnv
    ad = sc.read_h5ad(out("cel_higado.h5ad"))
    pos = posiciones()
    for k in ("chromosome", "start", "end"):
        ad.var[k] = pos[k].reindex(ad.var_names).values
    rp = ref_pur(ad)
    ad.obs["ref_pur"] = rp
    cnv.tl.infercnv(ad, reference_key="ref_pur", reference_cat=[True], window_size=100, step=10, n_jobs=1)
    Xc = ad.obsm["X_cnv"]
    Xc = Xc.toarray() if hasattr(Xc, "toarray") else Xc
    Xc = Xc - Xc.mean(axis=1, keepdims=True)
    senal = (Xc ** 2).mean(axis=1)
    esref = ad.obs["tipo"].isin(REF_TIPOS).values
    cand = ~esref & (ad.obs["tipo"] != "hepatocito").values
    top = np.where(cand)[0][np.argsort(senal[cand])[::-1][: int(0.1 * cand.sum())]]
    cons = Xc[top].mean(axis=0)
    Z = (Xc - Xc.mean(axis=1, keepdims=True)) / (Xc.std(axis=1, keepdims=True) + 1e-12)
    r = Z @ ((cons - cons.mean()) / cons.std()) / Xc.shape[1]
    p99 = float(np.percentile(r[rp], 99))
    p90 = float(np.percentile(r[rp], 90))
    med = pd.Series(r, index=ad.obs_names).groupby(ad.obs["leiden"].values, observed=True).median()
    cl_ab = sorted(map(str, med.index[med > p90]))
    np.save(out("cnv_corr_pur_higado.npy"), r)
    # entrada CopyKAT: mismas no-referencia del trozo 0 + referencia purificada (≤1000, semilla)
    d0 = out("copykat_higado")
    cells = pd.read_csv(os.path.join(d0, "celulas.csv")).iloc[:, 0].values
    ref0 = set(pd.read_csv(os.path.join(d0, "referencia.csv")).iloc[:, 0].values)
    noref = [c for c in cells if c not in ref0]
    trozo0 = noref[0::4]
    rng = np.random.default_rng(SEED)
    idx_rp = np.where(rp)[0]
    if len(idx_rp) > 1000:
        idx_rp = np.sort(rng.choice(idx_rp, 1000, replace=False))
    pos_ = {b: i for i, b in enumerate(ad.obs_names)}
    idx = np.array([pos_[c] for c in trozo0] + list(idx_rp))
    d = out("copykat_higado_pur")
    os.makedirs(d, exist_ok=True)
    scipy.io.mmwrite(os.path.join(d, "matriz.mtx"), ad.layers["counts"][idx].T.tocsc().astype(np.int32))
    ad.var["simbolo"].to_frame().to_csv(os.path.join(d, "genes.csv"), index=False)
    pd.Series(ad.obs_names[idx]).to_csv(os.path.join(d, "celulas.csv"), index=False)
    pd.Series(ad.obs_names[idx_rp]).to_csv(os.path.join(d, "referencia.csv"), index=False)
    res = {"n_ref_original": int(esref.sum()), "n_ref_purificada": int(rp.sum()),
           "p99_r_ref_original": None, "p99_r_ref_purificada": p99, "p90_r_ref_purificada": p90,
           "clusters_aberrantes_con_ref_purificada": cl_ab,
           "n_trozo0_noref": len(trozo0), "n_ref_copykat": int(len(idx_rp))}
    import json
    res["p99_r_ref_original"] = json.load(open(out("malignas_infercnv.json")))["higado"]["umbral_p99_ref"]
    guarda_json(res, "purificada_prepara.json")
    print(res)


def evalua():
    import json
    prep = json.load(open(out("purificada_prepara.json")))
    ad = sc.read_h5ad(out("cnv_higado.h5ad"))
    med = ad.obs.groupby("leiden", observed=True)["cnv_corr"].median()
    p90 = float(np.percentile(ad.obs.loc[ad.obs["tipo"].isin(REF_TIPOS), "cnv_corr"], 90))
    cl_orig = set(map(str, med.index[med > p90]))
    d = out("copykat_higado_pur")
    f = [x for x in os.listdir(d) if x.endswith("_copykat_prediction.txt")]
    pr = pd.read_csv(os.path.join(d, f[0]), sep="\t").set_index("cell.names")["copykat.pred"]
    pr.index = pr.index.astype(str).str.replace(".", "-", regex=False)
    ref = set(pd.read_csv(os.path.join(d, "referencia.csv")).iloc[:, 0].values)
    obs = ad.obs.reindex(pr.index)
    ev = pr.isin(["aneuploid", "diploid"])
    en_ab = obs["leiden"].astype(str).isin(cl_orig).values & ~pr.index.isin(list(ref))
    f_ab = float((pr[ev & en_ab] == "aneuploid").mean()) if (ev & en_ab).any() else float("nan")
    esr = pr.index.isin(list(ref))
    f_ref = float((pr[ev & esr] == "aneuploid").mean()) if (ev & esr).any() else float("nan")
    pasa = (f_ab >= 0.5) and (f_ref <= 0.10)
    res = {"prepara": prep, "conteos_copykat": pr.value_counts().to_dict(),
           "n_evaluables_en_clusters_aberrantes": int((ev & en_ab).sum()),
           "frac_aneuploide_en_clusters_aberrantes": f_ab,
           "n_ref_evaluables": int((ev & esr).sum()), "frac_aneuploide_en_ref_purificada": f_ref,
           "criterio": "≥0,50 y ≤0,10", "veredicto": "PASA" if pasa else "FALLA"}
    guarda_json(res, "purificada.json")
    print(res)


def run(sub="prepara", *a):
    prepara() if sub == "prepara" else evalua()
