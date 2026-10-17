"""Medidas primarias A-D (espec.) sobre células malignas por muestra, + sensibilidades EXPLORATORIAS:
(i) solo sondas «spliced» (raw_probe_bc_matrix; robustez a UMI de ADN genómico),
(ii) solo células en GEM limpio (UMBRAL_GEM_LIMPIO.json, fijado antes de estas tablas).
Por gen: fracción ≥1 UMI con IC95 bootstrap (1.000, semilla por gen), media CP10k entre positivas, n.
Salida: analisis_A/tablas.json y tablas.csv. stdout: agregados.
"""
import json
import os

import h5py
import numpy as np
import pandas as pd
import scanpy as sc
import scipy.sparse as sp

from _comun import BASE, SEED, TEJIDOS, bootstrap_frac, guarda_json, ids_de, out, ruta_muestra

A = ["B2M", "TAP1", "TAP2", "NLRC5", "PSMB8", "PSMB9", "IRF1", "STAT1", "HLA-A", "HLA-B", "HLA-C", "HLA-E"]
B = ["PRAME", "MAGEA4", "MAGEA1", "MAGEA6", "MAGEA10", "MAGEA11", "MAGEA12", "MAGEC1", "MAGEC2",
     "CTAG2", "ANKRD30A", "TPBG"]
C = ["TACSTD2", "ERBB2", "ERBB3", "CD276", "NECTIN4", "CEACAM5", "{{DIANA2}}", "SEZ6", "{{DIANA}}", "FOLR1",
     "SLC39A6", "VTCN1", "MSLN", "CLDN6", "MUC1", "GPNMB", "ROR1", "FGFR1"]
APM = ["B2M", "TAP1", "TAP2", "NLRC5", "PSMB8", "PSMB9", "IRF1", "STAT1"]
NE = ["CHGA", "SYP", "{{DIANA3}}", "{{DIANA4}}", "NEUROD1"]
S_GENES = ["MCM5", "PCNA", "TYMS", "FEN1", "MCM2", "MCM4", "RRM1", "UNG", "GINS2", "MCM6", "CDCA7",
           "DTL", "PRIM1", "UHRF1", "CENPU", "HELLS", "RFC2", "RPA2", "NASP", "RAD51AP1", "GMNN",
           "WDR76", "SLBP", "CCNE2", "UBR7", "POLD3", "MSH2", "ATAD2", "RAD51", "RRM2", "CDC45",
           "CDC6", "EXO1", "TIPIN", "DSCC1", "BLM", "CASP8AP2", "USP1", "CLSPN", "POLA1", "CHAF1B",
           "BRIP1", "E2F8"]
G2M_GENES = ["HMGB2", "CDK1", "NUSAP1", "UBE2C", "BIRC5", "TPX2", "TOP2A", "NDC80", "CKS2", "NUF2",
             "CKS1B", "MKI67", "TMPO", "CENPF", "TACC3", "PIMREG", "SMC4", "CCNB2", "CKAP2L", "CKAP2",
             "AURKB", "BUB1", "KIF11", "ANP32E", "TUBB4B", "GTSE1", "KIF20B", "HJURP", "CDCA3", "JPT1",
             "CDC20", "TTK", "CDC25C", "KIF2C", "RANGAP1", "NCAPD2", "DLGAP5", "CDCA2", "CDCA8", "ECT2",
             "KIF23", "HMMR", "AURKA", "PSRC1", "ANLN", "LBR", "CKAP5", "CENPE", "CTCF", "NEK2", "G2E3",
             "GAS2L3", "CBX5", "CENPA"]
MIN_MALIGNAS_D = 200
UMBRAL = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "UMBRAL_GEM_LIMPIO.json")))


def panel():
    ps = pd.read_csv(os.path.join(BASE, "MP", "outs", "probe_set.csv"), comment="#")
    return ps


def mide(ad, simbolo, gid, sub=None, etiqueta=""):
    m = np.ones(ad.n_obs, bool) if sub is None else sub
    n = int(m.sum())
    c = np.asarray(ad.layers["counts"][m][:, ad.var_names.get_loc(gid)].todense()).ravel()
    tot = np.asarray(ad.obs["total_counts"].values[m], float)
    k = int((c > 0).sum())
    f, lo, hi = bootstrap_frac(k, n, nombre=f"{etiqueta}|{simbolo}")
    cp = c / tot * 1e4
    media = float(cp[c > 0].mean()) if k else None
    return {"frac": f, "ic95": [lo, hi], "k": k, "n": n, "media_cp10k_positivas": media,
            "nota": ("0 detectadas, no implica ausencia" if k == 0 else "")}


def probe_matrix(t, ad):
    """Matriz por sonda (células malignas de ad × sondas) desde raw_probe_bc_matrix."""
    with h5py.File(ruta_muestra(t, "_raw_probe_bc_matrix.h5"), "r") as f:
        g = f["matrix"]
        bcs = np.array([b.decode() for b in g["barcodes"][:]])
        filt = g["filtered_barcodes"][:]
        shape = tuple(g["shape"][:])
        M = sp.csc_matrix((g["data"][:], g["indices"][:], g["indptr"][:]), shape=shape)
        pid = np.array([x.decode() for x in g["features/id"][:]])
        region = np.array([x.decode() for x in g["features/probe_region"][:]])
        gname = np.array([x.decode() for x in g["features/gene_name"][:]])
        fprobe = g["features/filtered_probes"][:]
    gem = {b[:16]: i for i, b in enumerate(bcs) if filt[i]}
    cols = [gem.get(b[:16], -1) for b in ad.obs_names]
    ok = int(sum(c >= 0 for c in cols))
    assert ok == ad.n_obs, f"join sonda-célula incompleto {ok}/{ad.n_obs}"
    Mm = M[:, cols].T.tocsr()
    return Mm, pid, region, gname, fprobe, int(filt.sum())


def run(*a):
    ps = panel()
    inc = set(ps.loc[ps["included"], "gene_name"])
    allp = set(ps["gene_name"])
    tej = json.load(open(out("celulas.json")))["control_tejido"]
    res, filas = {}, []
    for t in TEJIDOS:
        ad = sc.read_h5ad(out(f"mal_{t}.h5ad"))
        # Población principal = consenso de la espec. Si el consenso es 0 (CopyKAT no informativo,
        # ver purificada.json), se usa la de respaldo pre-declarada y TODO queda EXPLORATORIO.
        n_cons = int(ad.obs["maligna"].sum())
        pob = "maligna" if n_cons > 0 else "pob_respaldo"
        mal = ad.obs[pob].values.astype(bool)
        am = ad[mal].copy()
        expo = np.load(out(f"exposicion_{t}.npy"))[mal]
        limpio = expo <= UMBRAL["umi_otra_muestra_max"]
        Mm, pid, region, gname, fprobe, nfilt = probe_matrix(t, am)
        pobs = {k: ad.obs[k].values.astype(bool) for k in ("maligna", "pob_respaldo", "pob_p99", "pob_epitelial")}
        r = {"poblacion_usada": pob, "n_consenso_espec": n_cons,
             "n_por_definicion": {k: int(v.sum()) for k, v in pobs.items()},
             "n_malignas": int(mal.sum()), "n_malignas_GEM_limpio": int(limpio.sum()),
             "n_celulas_filtradas_raw_probe": nfilt}
        for tabla, genes in (("A", A), ("B", B), ("C", C), ("NE", NE)):
            for s in genes:
                gid = ids_de(am, [s])[s]
                if gid is None:
                    estado = "fuera del panel" if s not in allp else "fuera del panel (sonda no incluida)"
                    r[f"{tabla}:{s}"] = estado
                    filas.append({"muestra": t, "tabla": tabla, "gen": s, "estado": estado})
                    continue
                d = mide(am, s, gid, etiqueta=t)
                d["sensib_GEM_limpio"] = mide(am, s, gid, sub=limpio, etiqueta=t + "|limpio") if limpio.sum() else None
                # La matriz por gen suma TODAS las sondas del gen (verificado: ESR1 por gen = suma de sus
                # 5 sondas, aunque solo 3 tienen filtered_probes=True), así que aquí no se usa ese flag.
                sel = (gname == s)
                sel_inc = sel
                sp_sel = sel_inc & (region == "spliced")
                d["n_sondas_incluidas"] = int(sel_inc.sum())
                d["n_sondas_spliced"] = int(sp_sel.sum())
                if sp_sel.any():
                    kk = int((np.asarray(Mm[:, np.where(sp_sel)[0]].sum(axis=1)).ravel() > 0).sum())
                    f, lo, hi = bootstrap_frac(kk, am.n_obs, nombre=f"{t}|spliced|{s}")
                    d["sensib_solo_spliced"] = {"frac": f, "ic95": [lo, hi], "k": kk, "n": am.n_obs}
                est = {}
                for k, v in pobs.items():
                    if v.sum() == 0:
                        continue
                    cc = np.asarray(ad.layers["counts"][v][:, ad.var_names.get_loc(gid)].todense()).ravel()
                    est[k] = float((cc > 0).mean())
                d["estabilidad_frac_por_definicion"] = est
                d["estabilidad_rango"] = float(max(est.values()) - min(est.values())) if est else None
                r[f"{tabla}:{s}"] = d
                filas.append({"muestra": t, "tabla": tabla, "gen": s, "estado": "medido",
                              "frac": d["frac"], "ic95_lo": d["ic95"][0], "ic95_hi": d["ic95"][1],
                              "k": d["k"], "n": d["n"], "media_cp10k_pos": d["media_cp10k_positivas"],
                              "nota": d["nota"], "n_sondas": d["n_sondas_incluidas"],
                              "poblacion": pob, "estabilidad_rango": d["estabilidad_rango"],
                              "frac_solo_spliced": d.get("sensib_solo_spliced", {}).get("frac"),
                              "frac_GEM_limpio": (d["sensib_GEM_limpio"] or {}).get("frac"),
                              "n_GEM_limpio": (d["sensib_GEM_limpio"] or {}).get("n")})
        # MAGEA por sonda específica (auditoría de parálogos)
        par = pd.read_csv(out("paralogos_sondas.csv"))
        # Regla estricta (declarada tras ver SOLO las distancias de secuencia, antes de abrir la
        # expresión de tabla B): una sonda es específica si no casa con otro gen con ≤2 fallos Y su
        # parálogo más cercano está a ≥5 fallos con ≥2 en CADA mitad de 25 nt (la ligación exige que
        # hibriden las dos mitades; una mitad con 0-1 fallos puede capturar el parálogo).
        par["especifica_estricta"] = (par["especifica"] & (par["fallos_total"] >= 5)
                                      & (par[["fallos_mitad1", "fallos_mitad2"]].min(axis=1) >= 2))
        par.to_csv(out("paralogos_sondas.csv"), index=False)
        esp = {}
        for s in B:
            d = par[(par["gen"] == s) & par["included"] & par["especifica_estricta"]]
            if d.empty:
                cerc = par.loc[par["gen"] == s, "paralogo_mas_cercano"].unique().tolist()
                esp[s] = f"no resoluble a nivel de parálogo (sondas a <5 fallos o mitad con ≤1 de: {cerc})"
                continue
            cols = np.where(np.isin(pid, d["probe_id"].values))[0]
            kk = int((np.asarray(Mm[:, cols].sum(axis=1)).ravel() > 0).sum())
            f, lo, hi = bootstrap_frac(kk, am.n_obs, nombre=f"{t}|esp|{s}")
            esp[s] = {"n_sondas_especificas": int(len(cols)), "frac": f, "ic95": [lo, hi], "k": kk, "n": am.n_obs}
        r["B_sondas_especificas"] = esp
        # módulos (scores sobre malignas)
        if am.n_obs >= 20:
            ids = lambda L: [g for g in ids_de(am, L).values() if g]
            sc.tl.score_genes(am, ids(APM), score_name="APM", random_state=SEED)
            sc.tl.score_genes(am, ids(NE), score_name="NE", random_state=SEED)
            sc.tl.score_genes_cell_cycle(am, s_genes=ids(S_GENES), g2m_genes=ids(G2M_GENES), random_state=SEED)
            from scipy.stats import spearmanr
            rho, p = spearmanr(am.obs["APM"], am.obs["NE"])
            fase = am.obs.groupby("phase", observed=True)["APM"].agg(["mean", "count"]).to_dict()
            ne_alto = am.obs["NE"] > 0
            r["modulos"] = {"spearman_APM_NE": float(rho), "p": float(p),
                            "APM_por_fase": fase,
                            "frac_fase": am.obs["phase"].value_counts(normalize=True).to_dict(),
                            "APM_medio_NE_alto": float(am.obs.loc[ne_alto, "APM"].mean()) if ne_alto.any() else None,
                            "APM_medio_NE_bajo": float(am.obs.loc[~ne_alto, "APM"].mean()),
                            "frac_NE_score_pos": float(ne_alto.mean())}
            ad.obs["pob_usada"] = mal
            ad.uns["pob_usada_nombre"] = pob
            ad.obs.loc[am.obs_names, "APM"] = am.obs["APM"]
            ad.obs.loc[am.obs_names, "NE"] = am.obs["NE"]
            ad.obs.loc[am.obs_names, "phase"] = am.obs["phase"].astype(str)
            ad.write_h5ad(out(f"tab_{t}.h5ad"), compression="gzip")
        res[t] = r
        print(f"[{t}] malignas {r['n_malignas']} (GEM limpio {r['n_malignas_GEM_limpio']})")
    nb = res["hueso"]["n_malignas"]
    motivos = []
    if tej["veredicto"] != "PASA":
        motivos.append("control de tejido STOP (hepatocitos en hueso)")
    if nb < MIN_MALIGNAS_D:
        motivos.append(f"hueso con {nb} < {MIN_MALIGNAS_D} malignas")
    res["D_veredicto"] = "INDETERMINADA: " + "; ".join(motivos) if motivos else "EVALUABLE"
    print("D:", res["D_veredicto"])
    guarda_json(res, "tablas.json")
    pd.DataFrame(filas).to_csv(out("tablas.csv"), index=False)
