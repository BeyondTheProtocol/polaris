"""Etapa PREP del cierre: lectura pesada (matrices de Cell Ranger, ambiente, sondas, socios de GEM) y
cachés deterministas en ~/Clinico-PRIVADO/flex/cierre/cache/. No calcula ninguna cifra de resultado.
"""
import gc
import os

import anndata as an
import numpy as np
import pandas as pd
import scipy.sparse as sp

import _c
from _c import TC, cachep


def _norm_nombre(s):
    s = str(s).split(":")[-1]
    return s.replace(".", "-")


def _copykat(path, bcs):
    d = pd.read_csv(path, sep=None, engine="python")
    nombres = [_norm_nombre(x) for x in d["cell.names"]]
    m = dict(zip(nombres, d["copykat.pred"].astype(str)))
    lab = np.array([m.get(b, "sin_prediccion") for b in bcs])
    return lab, int(sum(b in m for b in bcs)), int(len(d))


def _sondas(t, bcs, ps, cv):
    """Por célula: suma de UMI en sondas unspliced OR (nulas) y matriz células × genes de ventana (unspliced)."""
    nul = ps[ps["gene_name"].str.match(r"^OR\d") & (ps.region == "unspliced")]
    ven = set()
    for wv in TC.VENTANAS:
        ven |= set(TC.genes_ventana(cv, wv))
    vps = ps[ps["gene_id"].isin(ven) & (ps.region == "unspliced")]
    keep = set(nul["probe_id"]) | set(vps["probe_id"])
    path = TC.ruta(t, "_raw_probe_bc_matrix.h5")
    import h5py
    with h5py.File(path, "r") as f:
        fid = np.array([x.decode() for x in f["matrix/features/id"][:]])
    filas = np.where(np.isin(fid, sorted(keep)))[0]
    M, ids, _ = TC.stream_h5(path, bcs, filas=filas)
    M = M.tocsc()
    ids = np.asarray(ids)
    es_or = np.isin(ids, nul["probe_id"].values)
    or_sum = np.asarray(M[:, es_or].sum(axis=1)).ravel().astype(np.int64)
    gid_de = dict(zip(ps["probe_id"], ps["gene_id"]))
    genes = sorted(set(vps["gene_id"]))
    G = np.zeros((len(bcs), len(genes)), dtype=np.int32)
    nsond = np.zeros(len(genes), dtype=np.int32)
    for j, g in enumerate(genes):
        c = np.array([gid_de.get(x) == g for x in ids])
        nsond[j] = int(c.sum())
        G[:, j] = np.asarray(M[:, c].sum(axis=1)).ravel()
    sym = cv.reindex(genes)["symbol"].astype(str).values
    chrom = cv.reindex(genes)["chromosome"].astype(str).values
    return {"or_sum": or_sum, "n_or": int(es_or.sum()), "ven_genes": np.array(genes), "ven_sym": sym,
            "ven_chr": chrom, "ven_counts": G, "ven_nsondas": nsond}


def run(*a):
    ps = TC.probe_set()
    ps = ps[ps["included"]].copy()
    cv = TC.coords()
    nsond = ps.groupby("gene_id").size()
    nunsp = ps[ps.region == "unspliced"].groupby("gene_id").size()
    zl = np.load(os.path.join(_c.TRIB, "higado_calls.npz"))
    zb = np.load(os.path.join(_c.TRIB, "hueso_calls.npz"), allow_pickle=True)
    import _suelo
    for t in ("higado", "hueso"):
        ad = TC.carga(t)
        bcs = ad.obs_names.values.astype(str)
        X = ad.X.tocsr().astype(np.int32)
        X.sort_indices()
        sp.save_npz(cachep(f"X_{t}.npz"), X, compressed=True)
        vv = cv.reindex(ad.var_names)
        ob = TC.obs_B(cols=("maligna_B_explor",))
        ob = ob[ob["bc"].isin(bcs)].set_index("bc").reindex(bcs)
        p_amb, n_vac, umi_vac = _suelo.ambiente(t, ad)
        extra = {}
        if t == "higado":
            assert len(zl["lum"]) == len(bcs)
            extra.update({"trib_lum": zl["lum"], "trib_8p11_p99": zl["8p11_p99"]})
            labs = {}
            bcn = an.read_h5ad(os.path.join(TC.B_DIR, "higado_cnv.h5ad"), backed="r")
            ob2 = bcn.obs[["copykat"]].copy()
            bcn.file.close()
            ob2.index = [_norm_nombre(x) for x in ob2.index]
            extra["ck_B"] = ob2.reindex(bcs)["copykat"].astype(str).fillna("sin_prediccion").values
            labs["ck_B"] = (int(ob2.index.isin(bcs).sum()), len(ob2))
            for nombre, rel in (("ck_A_trozo0", "analisis_A/copykat_higado/trozo0/higado_t0_copykat_prediction.txt"),
                                ("ck_A_pur", "analisis_A/copykat_higado_pur/higado_pur_copykat_prediction.txt")):
                lab, nmatch, ntot = _copykat(os.path.join(_c.FLEX, rel), bcs)
                extra[nombre] = lab
                labs[nombre] = (nmatch, ntot)
            print("CopyKAT emparejadas (células del QC con predicción / filas del fichero):", labs)
            acn = an.read_h5ad(os.path.join(TC.A_DIR, "cnv_higado.h5ad"), backed="r")
            o = acn.obs[["infercnv_aberrante"]].copy()
            acn.file.close()
            o.index = [_norm_nombre(x) for x in o.index]
            o = o.reindex(bcs)
            print("A infercnv emparejadas:", int(o["infercnv_aberrante"].notna().sum()), "de", len(bcs))
            extra["A_infercnv_aberrante"] = o["infercnv_aberrante"].fillna(False).astype(bool).values
        else:
            assert len(zb["limpio_T"]) == len(bcs)
            for k in ("maligna_T_limpio_T", "maligna_T_todas", "limpio_T", "socio"):
                extra["trib_" + k] = zb[k].astype(bool)
            extra["trib_tipo_socio"] = zb["tipo_socio"].astype(str)
            # socios del hígado en el mismo GEM: HEP5 y UMI del socio (matriz filtrada completa del hígado)
            import scanpy as sc
            adl = sc.read_10x_h5(TC.ruta("higado", "_filtered_feature_barcode_matrix.h5"), gex_only=True)
            adl.var_names = adl.var["gene_ids"].astype(str).values
            assert (adl.var_names == ad.var_names).all()
            Xl = adl.X.tocsr()
            gem_l = {TC.gem(b): i for i, b in enumerate(adl.obs_names.astype(str))}
            idx = np.array([gem_l.get(TC.gem(b), -1) for b in bcs])
            h5 = np.isin(ad.var["simbolo"].values, HEP5_)
            hep_l = np.asarray(Xl[:, h5].sum(axis=1)).ravel()
            umi_l = np.asarray(Xl.sum(axis=1)).ravel()
            extra["socio_hep5"] = np.where(idx >= 0, hep_l[np.maximum(idx, 0)], 0).astype(np.int64)
            extra["socio_umi"] = np.where(idx >= 0, umi_l[np.maximum(idx, 0)], 0).astype(np.int64)
            extra["socio_idx_ok"] = idx >= 0
            assert (extra["socio_idx_ok"] == extra["trib_socio"]).all(), "socio distinto del tribunal"
            del adl, Xl
            gc.collect()
        son = _sondas(t, bcs, ps, cv)
        np.savez_compressed(
            cachep(f"obs_{t}.npz"),
            gene_ids=ad.var_names.values.astype(str), simbolo=ad.var["simbolo"].values.astype(str),
            chrom=vv["chromosome"].astype(str).values, start=vv["start"].fillna(-1).values.astype(np.int64),
            nsondas=nsond.reindex(ad.var_names).fillna(0).astype(int).values,
            nsondas_unsp=nunsp.reindex(ad.var_names).fillna(0).astype(int).values,
            umi=np.asarray(X.sum(axis=1)).ravel().astype(np.int64),
            tipo=ad.obs["tipo"].astype(str).values, pob_usada_A=ad.obs["pob_usada"].values.astype(bool),
            maligna_B=ob["maligna_B_explor"].fillna(False).values.astype(bool),
            p_amb=p_amb, n_vacios=n_vac, umi_vacios=umi_vac, **extra, **{"son_" + k: v for k, v in son.items()})
        print(f"PREP {t}: células {len(bcs)}, genes {ad.n_vars}, vacíos {n_vac}, sondas OR {son['n_or']}, genes ventana {len(son['ven_genes'])}")
        del ad, X
        gc.collect()


HEP5_ = HEP5 = TC.HEP5
