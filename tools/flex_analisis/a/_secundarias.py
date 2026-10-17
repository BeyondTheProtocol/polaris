"""Secundarias (descriptivas, espec.): microambiente, eje ER/NE por célula, RB1/CDKN2A/CCNE1/E2F1 en
malignas no ciclantes (fase G1). Salida: analisis_A/secundarias.json. stdout: agregados."""
import numpy as np
import pandas as pd
import scanpy as sc

from _comun import SEED, TEJIDOS, bootstrap_frac, guarda_json, ids_de, out

MICRO = ["CD8A", "CD8B", "CD4", "FOXP3", "PDCD1", "HAVCR2", "LAG3", "TOX", "CD68", "CD163", "TREM2",
         "SPP1", "FAP", "IFNG", "GZMB", "CXCL9", "CXCL10", "CXCL13"]
ER = ["ESR1", "PGR", "GREB1", "TFF1"]
NE = ["CHGA", "SYP", "{{DIANA3}}", "{{DIANA4}}", "NEUROD1"]
CICLO = ["RB1", "CDKN2A", "CCNE1", "E2F1"]
COMPART = {"linfoide": {"T", "NK", "B", "plasmatica"}, "mieloide": {"mieloide", "neutrofilo", "mastocito", "osteoclasto"},
           "estroma": {"fibroblasto_estelar", "endotelio", "osteoblasto"}}


def frac(ad, s, m, et):
    gid = ids_de(ad, [s])[s]
    if gid is None:
        return "fuera del panel"
    n = int(m.sum())
    if n == 0:
        return {"n": 0}
    c = np.asarray(ad.layers["counts"][m][:, ad.var_names.get_loc(gid)].todense()).ravel()
    k = int((c > 0).sum())
    f, lo, hi = bootstrap_frac(k, n, nombre=et + "|" + s)
    return {"frac": f, "ic95": [lo, hi], "k": k, "n": n,
            "nota": "0 detectadas, no implica ausencia" if k == 0 else ""}


def det(ad, genes):
    ids = [g for g in ids_de(ad, genes).values() if g]
    c = ad.layers["counts"][:, [ad.var_names.get_loc(g) for g in ids]]
    return np.asarray((c > 0).todense())


def run(*a):
    res = {}
    for t in TEJIDOS:
        ad = sc.read_h5ad(out(f"tab_{t}.h5ad"))
        mal = ad.obs["pob_usada"].values.astype(bool)
        r_pob = str(ad.uns.get("pob_usada_nombre"))
        r = {"poblacion_maligna_usada": r_pob}
        tipo = ad.obs["tipo"].astype(str).where(~mal, "MALIGNA")
        n = ad.n_obs
        r["fracciones_tipo"] = {k: dict(zip(("frac", "ic_lo", "ic_hi"), bootstrap_frac(int(v), n, nombre=f"{t}|tipo|{k}")), n=n, k=int(v))
                                for k, v in tipo.value_counts().items()}
        nm = ~mal
        r["micro"] = {}
        for s in MICRO:
            r["micro"][s] = {"no_malignas": frac(ad, s, nm, f"{t}|nm")}
            for comp, tipos in COMPART.items():
                r["micro"][s][comp] = frac(ad, s, nm & ad.obs["tipo"].isin(tipos).values, f"{t}|{comp}")
        am = ad[mal].copy()
        if am.n_obs >= 20:
            ids = lambda L: [g for g in ids_de(am, L).values() if g]
            sc.tl.score_genes(am, ids(ER), score_name="ER", random_state=SEED)
            er_d = det(am, ["ESR1"]).ravel()
            ne_d = det(am, NE).sum(axis=1) >= 1
            ne_2 = det(am, NE).sum(axis=1) >= 2
            tab = pd.crosstab(pd.Series(er_d, name="ESR1_det"), pd.Series(ne_d, name="NE_det_1o_mas"))
            a_, b_, c_, d_ = [tab.loc[i, j] if (i in tab.index and j in tab.columns) else 0
                              for i, j in ((True, True), (True, False), (False, True), (False, False))]
            orr = ((a_ + .5) * (d_ + .5)) / ((b_ + .5) * (c_ + .5))
            from scipy.stats import spearmanr
            rho, p = spearmanr(am.obs["ER"], am.obs["NE"])
            sub = am.obs.groupby("leiden", observed=True)
            por_cl = pd.DataFrame({"n": sub.size(),
                                   "frac_ESR1_det": pd.Series(er_d, index=am.obs_names).groupby(am.obs["leiden"].values, observed=True).mean(),
                                   "frac_NE_det": pd.Series(ne_d, index=am.obs_names).groupby(am.obs["leiden"].values, observed=True).mean(),
                                   "frac_NE_2genes": pd.Series(ne_2, index=am.obs_names).groupby(am.obs["leiden"].values, observed=True).mean()})
            r["ER_NE"] = {"n": am.n_obs, "frac_ESR1_det": float(er_d.mean()), "frac_NE_det_1o_mas": float(ne_d.mean()),
                          "frac_NE_det_2o_mas": float(ne_2.mean()),
                          "frac_ESR1_y_NE": float((er_d & ne_d).mean()), "esperado_si_independientes": float(er_d.mean() * ne_d.mean()),
                          "odds_ratio_ESR1_NE_haldane": float(orr), "spearman_ER_NE_scores": float(rho), "p": float(p),
                          "por_cluster_expresion": por_cl.round(3).to_dict(orient="index"),
                          "por_gen_NE": {s: frac(am, s, np.ones(am.n_obs, bool), f"{t}|mal") for s in NE}}
            g1 = (am.obs["phase"].astype(str) == "G1").values
            r["ciclo_G1"] = {s: frac(am, s, g1, f"{t}|G1") for s in CICLO}
            r["ciclo_todas"] = {s: frac(am, s, np.ones(am.n_obs, bool), f"{t}|malall") for s in CICLO}
        res[t] = r
        print(f"[{t}] tipos:", {k: (v["k"], round(v["frac"], 3)) for k, v in r["fracciones_tipo"].items()})
        if "ER_NE" in r:
            e = r["ER_NE"]
            print(f"   ER/NE: ESR1 {e['frac_ESR1_det']:.3f} NE≥1 {e['frac_NE_det_1o_mas']:.3f} ambos {e['frac_ESR1_y_NE']:.3f} "
                  f"(indep {e['esperado_si_independientes']:.3f}) OR {e['odds_ratio_ESR1_NE_haldane']:.2f} rho {e['spearman_ER_NE_scores']:.2f}")
    guarda_json(res, "secundarias.json")
