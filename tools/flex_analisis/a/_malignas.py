"""Paso 5 (espec.): malignas = consenso inferCNV (infercnvpy) ∧ CopyKAT ∧ identidad epitelial.

Subpasos:
  malignas infercnv   -> posiciones Ensembl 110 por gene_id, inferCNV con referencia inmune+endotelio,
                         score por célula = media |X_cnv|; aberrante si > p99 de la referencia.
                         Exporta la entrada de CopyKAT (candidatas epiteliales + referencia ≤1000).
  malignas consenso   -> lee la predicción de CopyKAT y escribe el consenso y la concordancia.
Identidad epitelial por célula (regla declarada): ≥1 UMI en ≥2 de EPCAM, KRT8, KRT18, KRT19.
"""
import os

import numpy as np
import pandas as pd
import scanpy as sc
import scipy.io
import scipy.sparse as sp

from _comun import REF, SEED, TEJIDOS, guarda_json, ids_de, out

EPI = ["EPCAM", "KRT8", "KRT18", "KRT19"]
REF_TIPOS = ["T", "NK", "B", "plasmatica", "mieloide", "neutrofilo", "mastocito", "endotelio"]
GTF = os.path.join(REF, "Homo_sapiens.GRCh38.110.gtf.gz")
MAX_REF_COPYKAT = 1000


def posiciones():
    """gene_id -> chr, start, end de las líneas «gene» del GTF Ensembl 110 (cromosomas 1-22, X, Y)."""
    import gzip
    validos = {str(i) for i in range(1, 23)} | {"X", "Y"}
    filas = []
    with gzip.open(GTF, "rt") as f:
        for line in f:
            if line.startswith("#"):
                continue
            c = line.split("\t", 9)
            if c[2] != "gene" or c[0] not in validos:
                continue
            gid = c[8].split('gene_id "', 1)[1].split('"', 1)[0]
            filas.append((gid, "chr" + c[0], int(c[3]), int(c[4])))
    return pd.DataFrame(filas, columns=["gene_id", "chromosome", "start", "end"]).set_index("gene_id")


NO_EPITELIALES = {"hepatocito", "fibroblasto_estelar", "endotelio", "osteoblasto", "osteoclasto",
                  "eritroide", "megacariocito", "T", "NK", "B", "plasmatica", "mieloide",
                  "neutrofilo", "mastocito"}


def _epi_any(ad):
    ids = [g for g in ids_de(ad, EPI).values() if g]
    c = ad.layers["counts"][:, [ad.var_names.get_loc(g) for g in ids]]
    return np.asarray((c > 0).sum(axis=1)).ravel() >= 1


def epitelial_cluster(ad, refcats):
    """Identidad epitelial a nivel de clúster (regla declarada, fijada viendo solo la detección de
    EPCAM/KRT8/KRT18/KRT19 por clúster, antes de abrir ningún gen diana): fracción de células con
    ≥1 UMI en cualquiera de los 4 ≥ 1,5 × la de la referencia (inmune+endotelio) de esa muestra, y
    el clúster no tiene etiqueta de otro linaje (hepatocito, estroma, inmune, hueso...)."""
    e = _epi_any(ad)
    esref = ad.obs["tipo"].isin(refcats).values
    base = e[esref].mean()
    fr = pd.Series(e, index=ad.obs_names).groupby(ad.obs["leiden"].values, observed=True).mean()
    tipo = ad.obs.groupby("leiden", observed=True)["tipo"].first()
    ok = {cl for cl in fr.index if fr[cl] >= 1.5 * base and tipo[cl] not in NO_EPITELIALES}
    ad.uns["epi_cluster_regla"] = {"base_referencia": float(base), "clusters": sorted(map(str, ok))}
    return ad.obs["leiden"].isin(ok).values


def consenso_cromosomas(ad, cons):
    chrpos = ad.uns["cnv"]["chr_pos"]
    orden = sorted(chrpos.items(), key=lambda kv: kv[1])
    bordes = [p for _, p in orden] + [len(cons)]
    return {ch: float(cons[p:bordes[i + 1]].mean()) for i, (ch, p) in enumerate(orden)}


def epitelial(ad):
    ids = [g for g in ids_de(ad, EPI).values() if g]
    c = ad.layers["counts"][:, [ad.var_names.get_loc(g) for g in ids]]
    return np.asarray((c > 0).sum(axis=1)).ravel() >= 2


def infer(t):
    import infercnvpy as cnv
    ad = sc.read_h5ad(out(f"cel_{t}.h5ad"))
    pos = posiciones()
    ad.var["chromosome"] = pos["chromosome"].reindex(ad.var_names).values
    ad.var["start"] = pos["start"].reindex(ad.var_names).values
    ad.var["end"] = pos["end"].reindex(ad.var_names).values
    n_pos = int(ad.var["chromosome"].notna().sum())
    refcats = [c for c in REF_TIPOS if c in set(ad.obs["tipo"])]
    cnv.tl.infercnv(ad, reference_key="tipo", reference_cat=refcats, window_size=100, step=10,
                    n_jobs=1)
    Xc = ad.obsm["X_cnv"]
    Xc = Xc.toarray() if sp.issparse(Xc) else Xc
    # Llamada inferCNV (regla declarada, estilo Puram 2017): perfil centrado por célula; consenso
    # tumoral = media del 10% de células no-referencia, no-hepatocito, con más señal; r = correlación
    # de cada célula con ese consenso; aberrante si r > p99 de r en la referencia (≤1% FP en referencia).
    Xc = Xc - Xc.mean(axis=1, keepdims=True)
    senal = (Xc ** 2).mean(axis=1)
    esref = ad.obs["tipo"].isin(refcats).values
    cand = ~esref & (ad.obs["tipo"] != "hepatocito").values
    top = np.where(cand)[0][np.argsort(senal[cand])[::-1][: max(10, int(0.1 * cand.sum()))]]
    cons = Xc[top].mean(axis=0)
    Z = (Xc - Xc.mean(axis=1, keepdims=True)) / (Xc.std(axis=1, keepdims=True) + 1e-12)
    cz = (cons - cons.mean()) / cons.std()
    r = Z @ cz / Xc.shape[1]
    ad.obs["cnv_senal"] = senal
    ad.obs["cnv_corr"] = r
    ad.obs["cnv_score_celula"] = senal
    lim = float(np.percentile(r[esref], 99))
    ad.obs["infercnv_aberrante"] = r > lim
    ad.obs["epitelial_celula"] = epitelial(ad)
    ad.obs["epitelial_cluster"] = epitelial_cluster(ad, refcats)
    ad.uns["cnv_consenso_por_cromosoma"] = consenso_cromosomas(ad, cons)
    cnv.tl.pca(ad, random_state=SEED) if "random_state" in cnv.tl.pca.__code__.co_varnames else cnv.tl.pca(ad)
    cnv.pp.neighbors(ad, random_state=SEED)
    cnv.tl.leiden(ad, random_state=SEED, flavor="igraph", n_iterations=2)
    ad.write_h5ad(out(f"cnv_{t}.h5ad"), compression="gzip")
    # entrada CopyKAT: candidatas epiteliales (todas) + referencia submuestreada
    rng = np.random.default_rng(SEED)
    idx_ref = np.where(esref)[0]
    if len(idx_ref) > MAX_REF_COPYKAT:
        idx_ref = np.sort(rng.choice(idx_ref, MAX_REF_COPYKAT, replace=False))
    idx_epi = np.where(ad.obs["epitelial_cluster"].values & ~esref)[0]
    idx = np.concatenate([idx_epi, idx_ref])
    d = out(f"copykat_{t}")
    os.makedirs(d, exist_ok=True)
    M = ad.layers["counts"][idx].T.tocsc().astype(np.int32)
    scipy.io.mmwrite(os.path.join(d, "matriz.mtx"), M)
    ad.var["simbolo"].to_frame().to_csv(os.path.join(d, "genes.csv"), index=False)
    pd.Series(ad.obs_names[idx]).to_csv(os.path.join(d, "celulas.csv"), index=False)
    pd.Series(ad.obs_names[idx_ref]).to_csv(os.path.join(d, "referencia.csv"), index=False)
    r = {"genes_con_posicion": n_pos, "referencia_tipos": refcats, "n_referencia": int(esref.sum()),
         "umbral_p99_ref": lim, "n_aberrantes_infercnv": int(ad.obs["infercnv_aberrante"].sum()),
         "n_epitelial_celula": int(ad.obs["epitelial_celula"].sum()),
         "n_copykat_entrada_epi": int(len(idx_epi)), "n_copykat_entrada_ref": int(len(idx_ref))}
    print(t, r)
    return r


def consenso(t):
    ad = sc.read_h5ad(out(f"cnv_{t}.h5ad"))
    d = out(f"copykat_{t}")
    f = [os.path.join(r_, x) for r_, _, fs in os.walk(d) for x in fs if x.endswith("_copykat_prediction.txt")]
    if not f:
        print(t, "sin predicción de CopyKAT")
        return None
    trozos = [x for x in f if "trozo" in x]
    f = trozos if trozos else f
    prs = [pd.read_csv(x, sep="\t") for x in f]
    ref_names = set(pd.read_csv(os.path.join(d, "referencia.csv")).iloc[:, 0].str.replace("-", ".", regex=False))
    # la referencia aparece en cada trozo: se toma su predicción del primer trozo
    pr = pd.concat([prs[0]] + [x[~x["cell.names"].isin(ref_names)] for x in prs[1:]])
    pr = pr.drop_duplicates("cell.names").set_index("cell.names")["copykat.pred"]
    pr.index = pr.index.astype(str).str.replace(".", "-", regex=False)  # R make.names
    assert pr.index.isin(ad.obs_names).mean() > 0.99, "nombres de CopyKAT no casan"
    ad.obs["copykat"] = pr.reindex(ad.obs_names).fillna("no_evaluada").astype(str).values
    epi = ad.obs["epitelial_cluster"].values
    epi_cel = ad.obs["epitelial_celula"].values
    esref = ad.obs["tipo"].isin(REF_TIPOS).values
    # inferCNV, llamada primaria a nivel de clúster Leiden (regla declarada tras ver que la
    # referencia del hígado está contaminada de señal tumoral: p90 de r en referencia ≈ 0,3):
    # el clúster es aberrante si la MEDIANA de r de sus células supera el p90 de r en referencia.
    # La llamada por célula (r > p99 de la referencia) se informa como sensibilidad.
    r_ref = ad.obs.loc[esref, "cnv_corr"].values
    p90 = float(np.percentile(r_ref, 90))
    med = ad.obs.groupby("leiden", observed=True)["cnv_corr"].median()
    cl_ab = set(med.index[med > p90])
    ad.obs["infercnv_cluster_aberrante"] = ad.obs["leiden"].isin(cl_ab).values
    inf = ad.obs["infercnv_cluster_aberrante"].values
    inf_cel = ad.obs["infercnv_aberrante"].values
    ck = (ad.obs["copykat"] == "aneuploid").values
    evaluada = ad.obs["copykat"].isin(["aneuploid", "diploid"]).values
    ad.obs["maligna"] = epi & inf & ck & ~esref
    # poblaciones alternativas (estabilidad; REGLA_REF_PURIFICADA.json)
    ad.obs["pob_respaldo"] = epi & inf & ~esref
    ad.obs["pob_p99"] = epi & inf_cel & ~esref
    ad.obs["pob_epitelial"] = epi & ~esref
    m = epi & ~esref & evaluada
    a, b = inf[m], ck[m]
    a2 = inf_cel[m]
    acuerdo_cel = float((a2 == b).mean())
    pe2 = a2.mean() * b.mean() + (1 - a2.mean()) * (1 - b.mean())
    kappa_cel = float((acuerdo_cel - pe2) / (1 - pe2)) if pe2 < 1 else float("nan")
    acuerdo = float((a == b).mean())
    po = acuerdo
    pe = a.mean() * b.mean() + (1 - a.mean()) * (1 - b.mean())
    kappa = float((po - pe) / (1 - pe)) if pe < 1 else float("nan")
    tab = pd.crosstab(pd.Series(a, name="inferCNV_aberrante"), pd.Series(b, name="CopyKAT_aneuploide"))
    no_epi_aberr = int((inf_cel & ~epi & ~esref).sum())
    tipo_no_epi_aberr = ad.obs.loc[inf_cel & ~epi & ~esref, "tipo"].value_counts().to_dict()
    epi_diploides = int((epi & ~esref & evaluada & ~inf & ~ck).sum())
    epi_dipl_tipo = ad.obs.loc[epi & ~esref & evaluada & ~inf & ~ck, "tipo"].value_counts().to_dict()
    ref_ck = ad.obs.loc[esref & evaluada, "copykat"].value_counts().to_dict()
    r = {"n_total": ad.n_obs, "n_malignas_consenso": int(ad.obs["maligna"].sum()),
         "fraccion_malignas_sobre_total": float(ad.obs["maligna"].mean()),
         "n_evaluadas_ambos_epiteliales": int(m.sum()), "acuerdo": acuerdo, "kappa_cohen": kappa,
         "tabla_2x2": tab.to_dict(),
         "infercnv_p90_ref": p90, "infercnv_clusters_aberrantes": sorted(map(str, cl_ab)),
         "sensib_infercnv_por_celula_acuerdo": acuerdo_cel, "sensib_infercnv_por_celula_kappa": kappa_cel,
         "sensib_malignas_infercnv_por_celula": int((epi & inf_cel & ck & ~esref).sum()),
         "copykat_estado_en_epiteliales": ad.obs.loc[epi & ~esref, "copykat"].value_counts().to_dict(),
         "malignas_sin_exigir_copykat_definida": int((epi & inf & ~esref & ~(ad.obs["copykat"] == "diploid").values).sum()), "copykat_en_referencia": ref_ck,
         "EXPLORATORIO_aberrantes_infercnv_no_epiteliales": no_epi_aberr,
         "EXPLORATORIO_tipo_de_esas": tipo_no_epi_aberr,
         "epiteliales_diploides_por_ambos": epi_diploides, "tipo_epiteliales_diploides": epi_dipl_tipo,
         "sensibilidad_gate_por_celula_2de4": int((epi_cel & inf & ck & ~esref).sum()),
         "regla_epitelial_cluster": ad.uns.get("epi_cluster_regla"),
         "malignas_por_tipo_cluster": ad.obs.loc[ad.obs["maligna"], "tipo"].value_counts().to_dict()}
    ad.write_h5ad(out(f"mal_{t}.h5ad"), compression="gzip")
    print(t, r)
    return r


def run(sub="infercnv", *a):
    res = {}
    for t in TEJIDOS:
        res[t] = infer(t) if sub == "infercnv" else consenso(t)
    guarda_json(res, f"malignas_{sub}.json")
