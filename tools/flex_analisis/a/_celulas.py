"""Pasos 2-4 (espec.): normalización, Leiden (semilla 20261016) + marcadores canónicos por regla,
control de tejido (STOP) y ARN ambiental (diagnóstico, no se corrige).
Anotación por regla declarada: cada clúster recibe el programa con mayor score medio (score_genes),
con PTPRC para separar inmunes. Entrada qc_<t>.h5ad; salida cel_<t>.h5ad y celulas.json.
"""
import numpy as np
import pandas as pd
import scanpy as sc

from _comun import SEED, TEJIDOS, guarda_json, ids_de, out

PROGRAMAS = {
    "epitelial": ["EPCAM", "KRT8", "KRT18", "KRT19"],
    "hepatocito": ["ALB", "APOA1", "SERPINA1", "HP", "TF", "APOB", "APOC3"],
    "colangiocito": ["KRT7", "SOX9", "CFTR", "SPP1"],
    "T": ["CD3D", "CD3E", "CD2", "CD247"],
    "NK": ["NKG7", "GNLY", "KLRD1", "KLRF1"],
    "B": ["MS4A1", "CD79A", "CD19", "BANK1"],
    "plasmatica": ["JCHAIN", "MZB1", "SDC1", "XBP1"],
    "mieloide": ["CD68", "CD14", "LYZ", "CSF1R", "C1QA", "CD163"],
    "neutrofilo": ["CSF3R", "FCGR3B", "S100A8", "S100A9"],
    "mastocito": ["TPSAB1", "CPA3", "KIT"],
    "endotelio": ["PECAM1", "VWF", "CDH5", "KDR"],
    "fibroblasto_estelar": ["COL1A2", "DCN", "LUM", "PDGFRB", "RGS5", "ACTA2"],
    "osteoblasto": ["RUNX2", "SP7", "ALPL", "IBSP", "BGLAP"],
    "osteoclasto": ["ACP5", "CTSK", "MMP9", "DCSTAMP"],
    "eritroide": ["HBB", "HBA1", "GYPA", "ALAS2"],
    "megacariocito": ["PF4", "PPBP", "ITGA2B"],
}
INMUNES = {"T", "NK", "B", "plasmatica", "mieloide", "neutrofilo", "mastocito"}
REFERENCIA_CNV = INMUNES | {"endotelio"}
SCORE_MIN = 0.10
HEP5 = ["ALB", "APOA1", "SERPINA1", "HP", "TF"]


def procesa(ad):
    ad.layers["counts"] = ad.X.copy()
    sc.pp.normalize_total(ad, target_sum=1e4)
    sc.pp.log1p(ad)
    sc.pp.highly_variable_genes(ad, n_top_genes=2000, flavor="seurat")
    adh = ad[:, ad.var["highly_variable"]].copy()
    sc.pp.scale(adh, max_value=10)
    sc.tl.pca(adh, n_comps=50, svd_solver="arpack", random_state=SEED)
    ad.obsm["X_pca"] = adh.obsm["X_pca"]
    sc.pp.neighbors(ad, n_neighbors=15, n_pcs=30, random_state=SEED)
    sc.tl.leiden(ad, resolution=1.0, flavor="igraph", n_iterations=2, random_state=SEED)
    sc.tl.umap(ad, random_state=SEED)
    return ad


def anota(ad):
    for prog, genes in PROGRAMAS.items():
        ids = [g for g in ids_de(ad, genes).values() if g is not None]
        sc.tl.score_genes(ad, ids, score_name=f"sc_{prog}", random_state=SEED)
    cols = [f"sc_{p}" for p in PROGRAMAS]
    medias = ad.obs.groupby("leiden", observed=True)[cols].mean()
    ptprc = ids_de(ad, ["PTPRC"])["PTPRC"]
    ad.obs["_ptprc"] = (ad[:, ptprc].layers["counts"].toarray().ravel() > 0)
    frac_ptprc = ad.obs.groupby("leiden", observed=True)["_ptprc"].mean()
    etiqueta = {}
    for cl, fila in medias.iterrows():
        orden = fila.sort_values(ascending=False)
        # Regla declarada: un clúster solo recibe etiqueta si su programa ganador supera
        # SCORE_MIN; si no, «no_asignado» (evita etiquetar por argmax de scores ~0).
        etiqueta[cl] = orden.index[0][3:] if orden.iloc[0] >= SCORE_MIN else "no_asignado"
    ad.obs["tipo"] = ad.obs["leiden"].map(etiqueta).astype(str)
    tabla = medias.copy()
    tabla.columns = [c[3:] for c in cols]
    tabla["n"] = ad.obs["leiden"].value_counts().reindex(tabla.index)
    tabla["frac_PTPRC"] = frac_ptprc
    tabla["tipo"] = pd.Series(etiqueta)
    return tabla


def ambiental(ad):
    """Fracción de cuentas de los 5 genes hepatocitarios sobre el total, por compartimento."""
    ids = [g for g in ids_de(ad, HEP5).values() if g is not None]
    c = ad.layers["counts"]
    hep = np.asarray(c[:, [ad.var_names.get_loc(i) for i in ids]].sum(axis=1)).ravel()
    tot = np.asarray(c.sum(axis=1)).ravel()
    res = {}
    grupos = {"inmune": ad.obs["tipo"].isin(INMUNES).values,
              "endotelio": (ad.obs["tipo"] == "endotelio").values,
              "hepatocito": (ad.obs["tipo"] == "hepatocito").values}
    for nombre, m in grupos.items():
        if m.sum() == 0:
            res[nombre] = {"n": 0}
            continue
        res[nombre] = {"n": int(m.sum()), "frac_cuentas_hep": float(hep[m].sum() / tot[m].sum()),
                       "frac_celulas_ALB_pos": float((c[m][:, ad.var_names.get_loc(ids_de(ad, ['ALB'])['ALB'])].toarray().ravel() > 0).mean())}
    return res


def run(*a):
    resumen = {}
    for t in TEJIDOS:
        ad = sc.read_h5ad(out(f"qc_{t}.h5ad"))
        ad = procesa(ad)
        tabla = anota(ad)
        tabla.to_csv(out(f"clusters_{t}.csv"))
        ad.obs.drop(columns=["_ptprc"], inplace=True)
        fr = ad.obs["tipo"].value_counts()
        amb = ambiental(ad)
        resumen[t] = {"n": ad.n_obs, "n_clusters": int(ad.obs["leiden"].nunique()),
                      "tipos_n": fr.to_dict(), "ambiental": amb}
        ad.write_h5ad(out(f"cel_{t}.h5ad"), compression="gzip")
        pd.set_option("display.width", 250)
        print(f"== {t}: n {ad.n_obs}, clústeres {resumen[t]['n_clusters']}")
        print(tabla.round(2).to_string())
        print("tipos:", fr.to_dict())
        print("ambiental:", amb)
    h = resumen["higado"]["tipos_n"].get("hepatocito", 0)
    o = resumen["hueso"]["tipos_n"].get("hepatocito", 0)
    resumen["control_tejido"] = {
        "hepatocitos_en_higado": int(h), "hepatocitos_en_hueso": int(o),
        "veredicto": "PASA" if (h > 0 and o == 0) else "STOP"}
    print("control de tejido:", resumen["control_tejido"])
    guarda_json(resumen, "celulas.json")
