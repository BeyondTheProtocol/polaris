"""T8 — PRAME/MAGEA4 «vistos en Loupe» en el hueso: ¿ARN tumoral real, ADN genómico, baja calidad o fuga?

EXPLORATORIO, pregunta añadida por la coordinación el 16-oct-2026 DESPUÉS de congelar REGLAS_CIERRE_v3:
todo lo de este paso es POST HOC respecto a esas reglas (las reglas propias de T8 se fijan en el
docstring ANTES de ejecutarlo por primera vez):
 · universo = las 2.469 células llamadas por Cell Ranger en el hueso (las del .cloupe), sin nuestro QC;
 · ADN por célula = delta = UMI por sonda unspliced de receptores olfativos (OR); esperado de ADN de un gen
   en una célula = delta × nº de sondas unspliced del gen × copias/2 (copias 2); spliced = solo ARN;
 · «señal de ARN real» de PRAME/MAGEA4 en un subgrupo de malignas si (1) hay cuentas en sondas spliced, o
   (2) las cuentas en unspliced superan lo esperado por ADN con Poisson p < 0,001 en ese subgrupo, y
   (3) no se explican por células con ADN alto (delta > p90 de la muestra) ni por fuga del hígado;
 · lóbulos: embedding propio (scanpy, HVG 2.000, PCA 30, leiden, semilla) sobre las células tumorales
   (malignas del tribunal ∪ A ∪ clúster propio con EPI alto); los dos grupos mayores dentro del tumor se
   comparan (Wilcoxon, CP10k log1p) y se mapean al UMAP de 10x solo para nombrarlos (inferior/superior);
 · coexpresión: tabla 2×2 en malignas + nulo por permutación dentro de deciles de UMI (10.000, semilla);
 · B2M/TAP1: detección y CP10k en inmunes (GEM limpio) frente a malignas, con suelo de ambiente;
 · «neuron» de 10x: se cruzan con malignas (tribunal, A), marcadores epiteliales, NE y ADN.
Salida: cierre/t8_prame_hueso.json. stdout: solo agregados.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import pandas as pd
import scipy.sparse as sp

import _c
import _cloupe_sonda as CL
from _c import TC, w

SEED = _c.SEED
CTA = ["PRAME", "MAGEA4", "MAGEA1", "MAGEA3", "MAGEA6", "MAGEA10", "MAGEA11", "MAGEA12", "MAGEC1", "MAGEC2", "CTAG2"]
EXTRA = ["{{DIANA}}", "ESR1", "CHGA", "SYP", "{{DIANA3}}", "B2M", "TAP1"]
NE = ["CHGA", "SYP", "{{DIANA3}}", "{{DIANA4}}", "NEUROD1", "CHGB", "ENO2", "NCAM1"]
EPI = ["EPCAM", "KRT8", "KRT18", "KRT19", "KRT7", "GATA3", "FOXA1", "ESR1", "AGR2", "TFF3", "ANKRD30A"]
NEURONA = ["SNAP25", "STMN2", "GAP43", "TUBB3", "ELAVL4", "NEFL", "SYT1", "RBFOX3"]


def leer_cloupe():
    p = CL.fichero("hueso")
    ixs = CL.indice(p, todos=True)
    ix = ixs[0]
    with open(p, "rb") as f:
        m = ix["Matrices"][0]
        bcs = CL.arr(f, m["Barcodes"])
        umap = CL.arr(f, ix["Projections"][0]["Matrix"]).reshape(2, -1).T
        gb = ix["Clusterings"][0]
        graf = CL.arr(f, gb["Assignments"]).astype(int)
        tracks = {}
        for sub in ixs[1:]:
            for t in sub["CellTracks"]:
                import json
                meta = json.loads(CL.bloque(f, t["Metadata"]))
                v = CL.arr(f, t["Values"]).astype(int)
                grupos = np.array(meta["groups"] + ["<sin>"])
                tracks[t["Name"]] = grupos[np.minimum(v, len(meta["groups"]))]
    return bcs, umap, graf, tracks


def sondas(bcs, ps, genes):
    nul = ps[ps["gene_name"].str.match(r"^OR\d") & (ps.region == "unspliced")]
    sel = ps[ps["gene_name"].isin(genes)]
    keep = sorted(set(nul["probe_id"]) | set(sel["probe_id"]))
    path = TC.ruta("hueso", "_raw_probe_bc_matrix.h5")
    import h5py
    with h5py.File(path, "r") as f:
        fid = np.array([x.decode() for x in f["matrix/features/id"][:]])
    filas = np.where(np.isin(fid, keep))[0]
    M, ids, _ = TC.stream_h5(path, bcs, filas=filas)
    M = M.tocsc()
    ids = np.asarray(ids)
    es_or = np.isin(ids, nul["probe_id"].values)
    delta = np.asarray(M[:, es_or].sum(axis=1)).ravel() / es_or.sum()
    meta = ps.set_index("probe_id").reindex(ids)
    return M, ids, meta, delta, int(es_or.sum())


def perm_coexp(a, b, dec, n=10000, seed=SEED):
    """Nulo de independencia condicionado a la profundidad: permuta b dentro de cada decil de UMI."""
    rng = np.random.default_rng(seed)
    obs = int((a & b).sum())
    idx = [np.where(dec == d)[0] for d in np.unique(dec)]
    sims = np.empty(n)
    bb = b.copy()
    for i in range(n):
        for ix in idx:
            bb[ix] = b[ix][rng.permutation(len(ix))]
        sims[i] = (a & bb).sum()
    return {"observado_ambos": obs, "esperado_nulo_media": float(sims.mean()),
            "p_una_cola_mayor": float((1 + (sims >= obs).sum()) / (n + 1)),
            "razon_obs_esp": float(obs / sims.mean()) if sims.mean() > 0 else None}


def run(*a):
    import scanpy as sc
    res = {"aviso": "EXPLORATORIO; POST HOC respecto a REGLAS_CIERRE_v3 (pregunta añadida el 16-oct). Reglas propias en el docstring de _t8.py."}
    bcs_cl, umap, graf, tracks = leer_cloupe()
    adf = sc.read_10x_h5(TC.ruta("hueso", "_filtered_feature_barcode_matrix.h5"), gex_only=True)
    adf.var["simbolo"] = adf.var_names.astype(str)
    adf.var_names = adf.var["gene_ids"].astype(str).values
    bh5 = adf.obs_names.astype(str).values
    pos = {b: i for i, b in enumerate(bcs_cl)}
    orden = np.array([pos.get(b, -1) for b in bh5])
    res["emparejamiento_cloupe_h5"] = {"cloupe": len(bcs_cl), "h5_filtrado": len(bh5), "en_ambos": int((orden >= 0).sum())}
    print("cloupe/h5:", res["emparejamiento_cloupe_h5"])
    assert (orden >= 0).all()
    umap, graf = umap[orden], graf[orden]
    tracks = {k: v[orden] for k, v in tracks.items()}
    X = adf.X.tocsr()
    sym = adf.var["simbolo"].values
    umi = np.asarray(X.sum(axis=1)).ravel().astype(float)
    ngen = np.asarray((X > 0).sum(axis=1)).ravel()

    def g(s):
        j = np.where(sym == s)[0]
        return np.asarray(X[:, j[0]].todense()).ravel() if len(j) else np.zeros(X.shape[0])

    # QC, tipos y llamadas previas
    ob = np.load(_c.cachep("obs_hueso.npz"), allow_pickle=True)
    adq = TC.carga("hueso")
    qc_bcs = adq.obs_names.astype(str).values
    qpos = {b: i for i, b in enumerate(qc_bcs)}
    qi = np.array([qpos.get(b, -1) for b in bh5])
    enqc = qi >= 0

    def deqc(v, relleno):
        out = np.full(len(bh5), relleno, dtype=object if isinstance(relleno, str) else type(relleno))
        out[enqc] = np.asarray(v)[qi[enqc]]
        return out
    tipoA = deqc(ob["tipo"], "fuera_QC")
    malA = deqc(ob["pob_usada_A"], False).astype(bool)
    mal87 = deqc(ob["trib_maligna_T_todas"], False).astype(bool)
    mal68 = deqc(ob["trib_maligna_T_limpio_T"], False).astype(bool)
    malB = deqc(ob["maligna_B"], False).astype(bool)
    import anndata as an
    acn = an.read_h5ad(os.path.join(TC.A_DIR, "cnv_hueso.h5ad"), backed="r")
    oa = acn.obs[["doublet_score", "cnv_score_celula", "infercnv_aberrante"]].copy()
    acn.file.close()
    oa.index = [x.split(":")[-1] for x in oa.index]
    oa = oa.reindex(bh5)
    # fuga: UMI raw del hígado en el mismo GEM
    bcl, ttl = TC.barcodes_raw("higado")
    totl = dict(zip([TC.gem(b) for b in bcl], ttl))
    del bcl, ttl
    expo = np.array([totl.get(TC.gem(b), 0) for b in bh5])
    llam_l = set(TC.gem(b) for b in TC.llamadas("higado"))
    socio = np.array([TC.gem(b) in llam_l for b in bh5])
    # sondas
    ps = TC.probe_set()
    ps = ps[ps["included"]].copy()
    import json as _json
    lect = _json.load(open(os.path.join(_c.TRIB, "adn.json")))["hueso"]["lectura_copias_ADN_ventanas"]
    SILENT = {wv: lect[wv]["genes_silentes"] for wv in ("8p11", "{{LOCUS}}")}
    M, ids, meta, delta, n_or = sondas(bh5, ps, CTA + EXTRA + SILENT["8p11"] + SILENT["{{LOCUS}}"])
    res["n_sondas_OR"] = n_or
    p90 = float(np.percentile(delta, 90))
    adn_alto = delta > p90
    frac_adn = delta * int((ps.region == "unspliced").sum()) / np.maximum(umi, 1)

    # (b) diseño de sondas y cuentas por sonda
    tab_s = {}
    for gen in CTA + ["{{DIANA}}", "ESR1", "CHGA", "B2M", "TAP1"]:
        mm = (meta["gene_name"] == gen).values
        if not mm.any():
            tab_s[gen] = "sin sondas incluidas"
            continue
        d = {"n_spliced": int((meta["region"].values[mm] == "spliced").sum()),
             "n_unspliced": int((meta["region"].values[mm] == "unspliced").sum()), "sondas": {}}
        for j in np.where(mm)[0]:
            v = np.asarray(M[:, j].todense()).ravel()
            d["sondas"][f"{meta['region'].values[j]}_{ids[j][-6:]}"] = {"UMI_total_hueso": int(v.sum()), "celulas_con_UMI": int((v > 0).sum())}
        nu = d["n_unspliced"]
        tot = sum(x["UMI_total_hueso"] for x in d["sondas"].values())
        tot_u = sum(x["UMI_total_hueso"] for k, x in d["sondas"].items() if k.startswith("unspliced"))
        esp = float(delta.sum() * nu)
        from scipy.stats import poisson
        d.update({"UMI_total": tot, "UMI_unspliced": tot_u, "UMI_spliced": tot - tot_u,
                  "esperado_ADN_2copias_todas_celulas": esp,
                  "p_poisson_exceso_sobre_ADN": float(poisson.sf(tot_u - 1, esp)) if esp > 0 else None})
        tab_s[gen] = d
    res["b_sondas"] = tab_s
    print("(b) sondas:", {k: (v["n_spliced"], v["n_unspliced"], v["UMI_spliced"], v["UMI_unspliced"], round(v["esperado_ADN_2copias_todas_celulas"], 1))
                          for k, v in tab_s.items() if isinstance(v, dict)})

    # (a) las células PRAME+/MAGEA4+ (matriz de genes = lo que ve Loupe)
    perfil = {}
    grupos = {"PRAME+": g("PRAME") > 0, "MAGEA4+": g("MAGEA4") > 0, "{{DIANA}}+": g("{{DIANA}}") > 0,
              "todas": np.ones(len(bh5), bool), "malignas_87": mal87, "malignas_A_200": malA}
    for k, m in grupos.items():
        n = int(m.sum())
        if n == 0:
            perfil[k] = {"n": 0}
            continue
        perfil[k] = {
            "n": n, "en_QC": w(int((m & enqc).sum()), n), "malignas_tribunal_87": w(int((m & mal87).sum()), n),
            "malignas_tribunal_68_limpias": w(int((m & mal68).sum()), n), "malignas_A": w(int((m & malA).sum()), n),
            "malignas_B": w(int((m & malB).sum()), n),
            "infercnv_aberrante_A": w(int((m & oa["infercnv_aberrante"].fillna(False).astype(bool).values).sum()), n),
            "UMI_mediana": float(np.median(umi[m])), "genes_mediana": float(np.median(ngen[m])),
            "delta_ADN_mediana": float(np.median(delta[m])), "frac_UMI_ADN_mediana": float(np.median(frac_adn[m])),
            "ADN_alto_gt_p90": w(int((m & adn_alto).sum()), n),
            "doublet_score_mediana_A": float(np.nanmedian(oa["doublet_score"].values[m].astype(float))) if (m & enqc).any() else None,
            "con_socio_higado": w(int((m & socio).sum()), n), "exposicion_higado_mediana": float(np.median(expo[m])),
            "tipo_A": pd.Series(tipoA[m]).value_counts().to_dict(),
            "tipo_10x_Cloud": pd.Series(tracks.get("10x_Cloud Cell Types (beta)", np.array(["?"] * len(m)))[m]).value_counts().head(6).to_dict(),
            "tipo_Azimuth": pd.Series(tracks.get("Azimuth Cell Types (beta)", np.array(["?"] * len(m)))[m]).value_counts().head(6).to_dict(),
            "cluster_grafo_10x": pd.Series(graf[m]).value_counts().head(6).to_dict()}
    res["a_perfil"] = perfil
    for k in ("PRAME+", "MAGEA4+", "{{DIANA}}+", "malignas_87", "todas"):
        p = perfil[k]
        print(f"(a) {k}: n {p['n']}, QC {p['en_QC']['k']}, mal87 {p['malignas_tribunal_87']['k']}, malA {p['malignas_A']['k']}, "
              f"UMI {p['UMI_mediana']:.0f}, delta {p['delta_ADN_mediana']:.4f}, fracADN {p['frac_UMI_ADN_mediana']:.3f}, ADNalto {p['ADN_alto_gt_p90']['k']}, "
              f"socio {p['con_socio_higado']['k']}, tipoA {p['tipo_A']}, 10x {p['tipo_10x_Cloud']}")

    # P(PRAME>0) por decil de delta, y ADN esperado frente a observado en las PRAME+/MAGEA4+
    dd = _c.deciles(delta)
    res["b_deteccion_por_decil_ADN"] = {gen: [w(int(((g(gen) > 0) & (dd == d)).sum()), int((dd == d).sum())) for d in range(10)]
                                        for gen in ("PRAME", "MAGEA4", "{{DIANA}}", "ESR1")}
    res["b_delta_por_decil"] = [float(np.median(delta[dd == d])) for d in range(10)]
    print("(b) P(det) por decil de ADN:", {gen: [(round(x["frac"], 3) if x["frac"] is not None else None, x["n"]) for x in v] for gen, v in res["b_deteccion_por_decil_ADN"].items()})
    print("(b) delta mediana por decil:", [round(x, 4) for x in res["b_delta_por_decil"]])
    from scipy.stats import poisson
    sub = {}
    for gen in ("PRAME", "MAGEA4", "{{DIANA}}"):
        nu = tab_s[gen]["n_unspliced"]
        v = g(gen)
        for nombre, m in (("malignas_87", mal87), ("malignas_A_200", malA), ("malignas_87_ADN_bajo", mal87 & ~adn_alto),
                          ("no_malignas", ~mal87 & ~malA), ("todas", np.ones(len(v), bool))):
            esp = float(delta[m].sum() * nu)
            o = int(v[m].sum())
            sub[f"{gen}|{nombre}"] = {"n": int(m.sum()), "UMI_obs": o, "UMI_esperadas_ADN": esp,
                                      "obs_sobre_esp": o / esp if esp > 0 else None,
                                      "p_poisson_exceso": float(poisson.sf(o - 1, esp)) if esp > 0 else None,
                                      "celulas_det": w(int((v[m] > 0).sum()), int(m.sum()))}
    res["b_obs_vs_ADN_por_conjunto"] = sub
    print("(b) obs vs ADN:", {k: (v["UMI_obs"], round(v["UMI_esperadas_ADN"], 1), (round(v["p_poisson_exceso"], 4) if v["p_poisson_exceso"] is not None else None)) for k, v in sub.items()})

    # (c) embedding propio y lóbulos del tumor
    a2 = adf.copy()
    a2.var_names = a2.var["simbolo"].astype(str).values
    a2.var_names_make_unique()
    sc.pp.normalize_total(a2, target_sum=1e4)
    sc.pp.log1p(a2)
    sc.pp.highly_variable_genes(a2, n_top_genes=2000, flavor="seurat")
    a3 = a2[:, a2.var["highly_variable"]].copy()
    sc.pp.scale(a3, max_value=10)
    sc.tl.pca(a3, n_comps=30, random_state=SEED)
    sc.pp.neighbors(a3, n_neighbors=15, random_state=SEED)
    sc.tl.leiden(a3, resolution=0.6, random_state=SEED, flavor="igraph", n_iterations=2, directed=False)
    sc.tl.umap(a3, random_state=SEED)
    cl = a3.obs["leiden"].astype(int).values
    epi = np.asarray((X[:, np.isin(sym, EPI)] > 0).sum(axis=1)).ravel()
    tab = []
    for c in np.unique(cl):
        m = cl == c
        tab.append({"cl": int(c), "n": int(m.sum()), "frac_mal87_o_A": float((mal87 | malA)[m].mean()), "epi_ge3": float((epi[m] >= 3).mean()),
                    "PRAME+": int((grupos["PRAME+"] & m).sum()), "MAGEA4+": int((grupos["MAGEA4+"] & m).sum()),
                    "{{DIANA}}+": int((grupos["{{DIANA}}+"] & m).sum()), "UMI_med": float(np.median(umi[m])), "delta_med": float(np.median(delta[m])),
                    "umap10x_y_med": float(np.median(umap[m, 1])), "umap10x_x_med": float(np.median(umap[m, 0])),
                    "tipo_10x": pd.Series(tracks.get("10x_Cloud Cell Types (beta)", np.array(["?"] * len(m)))[m]).value_counts().head(2).to_dict()})
    tab = pd.DataFrame(tab)
    res["c_clusters_propios"] = tab.to_dict("records")
    pd.set_option("display.width", 250)
    print("(c) clústeres propios:\n" + tab.round(3).to_string(index=False))
    tumor_cl = tab[(tab["frac_mal87_o_A"] >= 0.3) | (tab["epi_ge3"] >= 0.5)]["cl"].tolist()
    res["c_clusters_tumor"] = tumor_cl
    tum = np.isin(cl, tumor_cl)
    res["c_n_tumor"] = int(tum.sum())
    # dos grupos dentro del tumor: los dos clústeres tumorales mayores, o subclustering si solo hay uno
    if len(tumor_cl) >= 2:
        lob = np.full(len(cl), -1)
        for i, c in enumerate(tumor_cl):
            lob[cl == c] = c
    else:
        lob = np.full(len(cl), -1)
    res["c_lobulos_definicion"] = "clústeres leiden propios con ≥30 % malignas (tribunal ∪ A) o ≥50 % con ≥3 genes epiteliales"
    # DE de cada clúster tumoral frente al resto del tumor
    de = {}
    a2t = a2[tum].copy()
    a2t.obs["cl"] = pd.Categorical(cl[tum].astype(str))
    if len(tumor_cl) >= 2:
        sc.tl.rank_genes_groups(a2t, "cl", method="wilcoxon")
        for c in tumor_cl:
            r = sc.get.rank_genes_groups_df(a2t, str(c))
            r = r[(r["pvals_adj"] < 0.01)]
            de[str(c)] = {"arriba": r.sort_values("scores", ascending=False).head(25)[["names", "logfoldchanges", "pvals_adj"]].round(4).values.tolist(),
                          "abajo": r.sort_values("scores").head(15)[["names", "logfoldchanges", "pvals_adj"]].round(4).values.tolist()}
    res["c_DE_por_cluster_tumoral"] = de
    for c, d in de.items():
        print(f"(c) cl {c} ARRIBA:", [x[0] for x in d["arriba"]][:20])
        print(f"    cl {c} ABAJO:", [x[0] for x in d["abajo"]][:12])

    # (a2) lectura de copias de ADN en los barcodes ricos en ADN: genes de ventana silentes en ARN (lista del
    # tribunal), calibrada en las no malignas de la propia muestra (2 copias). Si los barcodes PRAME+/lóbulo
    # inferior son núcleos TUMORALES, 8p11/{{LOCUS}} deben salir muy por encima de 2 copias.
    cop = {}
    nm = ~mal87 & ~malA
    for wv, gl in SILENT.items():
        mm = (meta["gene_name"].isin(gl) & (meta["region"] == "unspliced")).values
        cw = np.asarray(M[:, mm].sum(axis=1)).ravel()
        ns = int(mm.sum())
        base = (cw[nm].sum() / ns) / max(delta[nm].sum(), 1e-12)
        for nombre, m in (("PRAME+", grupos["PRAME+"]), ("MAGEA4+", grupos["MAGEA4+"]), ("{{DIANA}}+", grupos["{{DIANA}}+"]),
                          ("cl7", cl == 7), ("cl9", cl == 9), ("cl12_tumor", cl == 12), ("neuron_10x", tracks["10x_Cloud Cell Types (beta)"] == "neuron"),
                          ("no_malignas_ADN_alto", nm & adn_alto), ("malignas_87", mal87)):
            o = int(cw[m].sum())
            e2 = float(delta[m].sum() * ns * base)
            lo, hi = _c.garwood(o)
            cop[f"{wv}|{nombre}"] = {"n": int(m.sum()), "UMI_silentes": o, "esperado_2copias": e2,
                                     "copias": 2 * o / e2 if e2 > 0 else None,
                                     "copias_ic95_garwood": [2 * lo / e2, 2 * hi / e2] if e2 > 0 else None}
        cop[f"{wv}|n_sondas"] = ns
        cop[f"{wv}|eficiencia_relativa_no_malignas"] = float(base)
    res["a2_copias_ADN_ventanas"] = cop
    print("(a2) copias por ADN:", {k: (v["UMI_silentes"], round(v["esperado_2copias"], 1), (round(v["copias"], 1) if v["copias"] else None),
                                        [round(x, 1) for x in v["copias_ic95_garwood"]] if v["copias_ic95_garwood"] else None)
                                    for k, v in cop.items() if isinstance(v, dict)})
    # (c2) lóbulo inferior (cl 7 ∪ 9) frente al superior (cl 12): DE y calidad
    mtg = np.array([s_.startswith("MT-") for s_ in sym])
    mt_frac = np.asarray(X[:, mtg].sum(axis=1)).ravel() / np.maximum(umi, 1)
    a2l = a2[np.isin(cl, [7, 9, 12])].copy()
    a2l.obs["lob"] = pd.Categorical(np.where(cl[np.isin(cl, [7, 9, 12])] == 12, "superior", "inferior"))
    sc.tl.rank_genes_groups(a2l, "lob", groups=["inferior"], reference="superior", method="wilcoxon")
    r = sc.get.rank_genes_groups_df(a2l, "inferior")
    r = r[r["pvals_adj"] < 0.01]
    res["c2_inferior_vs_superior"] = {
        "arriba": r.sort_values("scores", ascending=False).head(30)[["names", "logfoldchanges", "pvals_adj"]].round(4).values.tolist(),
        "abajo": r.sort_values("scores").head(30)[["names", "logfoldchanges", "pvals_adj"]].round(4).values.tolist(),
        "calidad": {k: {"n": int(m.sum()), "UMI_mediana": float(np.median(umi[m])), "genes_mediana": float(np.median(ngen[m])),
                        "MT_frac_mediana": float(np.median(mt_frac[m])), "frac_UMI_ADN_mediana": float(np.median(frac_adn[m])),
                        "malignas_87": int((m & mal87).sum()), "malignas_A": int((m & malA).sum()), "fuera_QC": int((m & ~enqc).sum())}
                    for k, m in (("inferior_cl7", cl == 7), ("inferior_cl9", cl == 9), ("superior_cl12", cl == 12))}}
    print("(c2) inferior ARRIBA:", [x[0] for x in res["c2_inferior_vs_superior"]["arriba"]])
    print("(c2) inferior ABAJO:", [x[0] for x in res["c2_inferior_vs_superior"]["abajo"]])
    print("(c2) calidad:", res["c2_inferior_vs_superior"]["calidad"])

    # (d) coexpresión en malignas (tribunal 87 ∪ A 200)
    malu = mal87 | malA
    dec = _c.deciles(umi[malu])
    co = {}
    for g1, g2 in (("ESR1", "CHGA"), ("ESR1", "{{DIANA}}"), ("CHGA", "{{DIANA}}"), ("ESR1", "SYP")):
        x1, x2 = g(g1)[malu] > 0, g(g2)[malu] > 0
        r = perm_coexp(x1, x2, dec)
        r.update({"n": int(malu.sum()), f"{g1}+": int(x1.sum()), f"{g2}+": int(x2.sum()),
                  f"frac_{g1}+_en_{g2}+": w(int((x1 & x2).sum()), int(x2.sum())),
                  f"frac_{g1}+_en_{g2}-": w(int((x1 & ~x2).sum()), int((~x2).sum()))})
        co[f"{g1}~{g2}"] = r
    res["d_coexpresion_malignas"] = co
    print("(d) coexp:", {k: (v["observado_ambos"], round(v["esperado_nulo_media"], 1), round(v["p_una_cola_mayor"], 4)) for k, v in co.items()})

    # (e) B2M/TAP1/ACTB inmunes (GEM limpio, QC) frente a malignas, con suelo de ambiente
    p_amb = ob["p_amb"]
    gid_qc = ob["gene_ids"]
    inm = np.isin(tipoA, TC.REF_TIPOS) & ~socio
    e = {}
    for gen in ("B2M", "TAP1", "TAP2", "HLA-A", "ACTB", "PTPRC"):
        v = g(gen)
        jj = np.where(ob["simbolo"] == gen)[0]
        if not len(jj):
            e[gen] = "fuera del panel"
            continue
        pa = float(p_amb[jj[0]])
        fila = {"n_sondas": int(ob["nsondas"][jj[0]])}
        for nombre, m in (("inmunes_limpias", inm), ("malignas_87", mal87), ("malignas_A", malA)):
            fila[nombre] = {"det": w(int((v[m] > 0).sum()), int(m.sum())),
                            "cp10k_pseudobulk": float(v[m].sum() / umi[m].sum() * 1e4) if m.any() else None,
                            "UMI_mediana": float(np.median(umi[m])) if m.any() else None}
        fila["p_ambiente_cp10k"] = pa * 1e4
        e[gen] = fila
    res["e_presentacion"] = e
    print("(e)", {k: (v["inmunes_limpias"]["det"]["frac"], v["malignas_87"]["det"]["frac"], round(v["inmunes_limpias"]["cp10k_pseudobulk"], 2),
                      round(v["malignas_87"]["cp10k_pseudobulk"], 2), v["n_sondas"]) for k, v in e.items() if isinstance(v, dict)})

    # (f) «neuron» de 10x
    f_ = {}
    for nombre_track, t in tracks.items():
        vals = pd.Series(t).value_counts()
        f_[nombre_track] = {"conteos": vals.to_dict()}
        cand = [x for x in vals.index if "neur" in x.lower()]
        for cn in cand:
            m = t == cn
            n = int(m.sum())
            ne = np.asarray((X[:, np.isin(sym, NE)] > 0).sum(axis=1)).ravel()
            neu = np.asarray((X[:, np.isin(sym, NEURONA)] > 0).sum(axis=1)).ravel()
            f_[nombre_track][cn] = {
                "n": n, "malignas_87": int((m & mal87).sum()), "malignas_A": int((m & malA).sum()), "malignas_B": int((m & malB).sum()),
                "union_malignas": w(int((m & (mal87 | malA | malB)).sum()), n), "en_QC": int((m & enqc).sum()),
                "epi_ge3": w(int((m & (epi >= 3)).sum()), n), "NE_ge2": w(int((m & (ne >= 2)).sum()), n),
                "neurona_ge2": w(int((m & (neu >= 2)).sum()), n), "ADN_alto": w(int((m & adn_alto).sum()), n),
                "UMI_mediana": float(np.median(umi[m])), "delta_mediana": float(np.median(delta[m])),
                "tipo_A": pd.Series(tipoA[m]).value_counts().head(6).to_dict(),
                "clusters_propios": pd.Series(cl[m]).value_counts().head(6).to_dict()}
            print(f"(f) {nombre_track} / {cn}:", {k: v for k, v in f_[nombre_track][cn].items() if k not in ("tipo_A",)}, f_[nombre_track][cn]["tipo_A"])
    res["f_neuron_10x"] = f_
    print("(f) tracks:", {k: dict(list(v["conteos"].items())[:8]) for k, v in f_.items()})
    _c.guarda(res, "t8_prame_hueso.json")
    print("guardado t8_prame_hueso.json")


if __name__ == "__main__":
    run()
