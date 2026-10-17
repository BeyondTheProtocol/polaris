"""P1 — Fuga entre muestras en el mismo GEM (REGLAS_TRIBUNAL_v2.json «gem» y «fuga»).

(a) Medida independiente: GEM = 16 nt; socio = barcode del hígado llamado como célula (filtered) en el
    mismo GEM; exposición = UMI raw de la otra muestra en el GEM. Tasa de base y prueba de
    co-encapsulación aleatoria (razón esperada ≈ células del hígado / células del hueso).
(b) Re-llamada de malignas del hueso SOLO con GEM limpio (inferCNV propio, referencia inmune+endotelio
    del hueso limpia, consenso tipo Puram, corte p99 de la referencia) + identidad epitelial; y con
    todas las células para comparar. Perfil CNV por cromosoma frente al hígado.
(c) Fuga cuantificada: alfa con HEP5; UMI atribuibles al socio por gen en las malignas del hueso.
Salidas: tribunal/fuga.json, tribunal/hueso_calls.npz (vectores por célula en el orden de obs_A del hueso).
"""
import gc

import numpy as np
import pandas as pd
import scanpy as sc

from _comun import (EPI4, HEP5, REF_TIPOS, SEED, barcodes_raw, boot_diff, carga, coords, gem, genes_ventana,
                    guarda, llamadas, n_detectados, obs_A, obs_B, out, score_ventana, suma, wilson)

GENES_ATRIB = ["FGFR1", "CCND1", "ZNF703", "ERBB2", "ESR1", "GATA3", "KRT8", "KRT18", "KRT19", "EPCAM",
               "TACSTD2", "CD276", "STAT1", "B2M"]


def _w(k, n):
    p, lo, hi = wilson(k, n)
    return {"k": int(k), "n": int(n), "frac": p, "ic95": [lo, hi]}


def infercnv_propio(ad, ref, cand_excl):
    """inferCNV (infercnvpy, ventana 100) + llamada por célula: correlación con el consenso del 10 %
    no-referencia con más señal; aberrante si r > p99 de r en la referencia."""
    import infercnvpy as cnv
    cv = coords()
    a2 = ad.copy()
    a2.var[["chromosome", "start", "end"]] = cv.reindex(a2.var_names)[["chromosome", "start", "end"]].values
    a2 = a2[:, a2.var["chromosome"].notna()].copy()
    a2.var["start"] = a2.var["start"].astype(int)
    a2.var["end"] = a2.var["end"].astype(int)
    sc.pp.normalize_total(a2, target_sum=1e4)
    sc.pp.log1p(a2)
    a2.obs["ref"] = np.where(ref, "ref", "otra")
    cnv.tl.infercnv(a2, reference_key="ref", reference_cat=["ref"], window_size=100, step=10, n_jobs=1)
    X = a2.obsm["X_cnv"]
    X = X.toarray() if hasattr(X, "toarray") else np.asarray(X)
    Xc = X - X.mean(axis=1, keepdims=True)
    senal = (Xc ** 2).mean(axis=1)
    cand = np.where(~ref & ~cand_excl)[0]
    top = cand[np.argsort(senal[cand])[::-1][: max(10, int(0.1 * len(cand)))]]
    cons = Xc[top].mean(axis=0)
    Z = (Xc - Xc.mean(axis=1, keepdims=True)) / (Xc.std(axis=1, keepdims=True) + 1e-12)
    cz = (cons - cons.mean()) / cons.std()
    r = Z @ cz / Xc.shape[1]
    lim = float(np.percentile(r[ref], 99))
    chrpos = a2.uns["cnv"]["chr_pos"]
    return r, r > lim, lim, X, chrpos


def perfil_cromosomas(X, chrpos, mask_t, mask_r):
    orden = sorted(chrpos.items(), key=lambda kv: kv[1])
    bordes = [p for _, p in orden] + [X.shape[1]]
    return {ch: float(X[mask_t, p:bordes[i + 1]].mean() - X[mask_r, p:bordes[i + 1]].mean())
            for i, (ch, p) in enumerate(orden)}


def run(*a):
    res = {"reglas": "REGLAS_TRIBUNAL_v2.json/gem,fuga"}
    oa = obs_A()
    ob = obs_B(cols=("maligna_B", "maligna_B_explor", "tipo"))
    calls = {t: llamadas(t) for t in ("higado", "hueso")}
    tot = {}
    for t in ("higado", "hueso"):
        bc, tt = barcodes_raw(t)
        tot[t] = dict(zip([gem(b) for b in bc], tt))
        del bc, tt
        gc.collect()
    gem_llam = {t: {gem(b): b for b in calls[t]} for t in calls}

    # (a) socios y exposición, en las dos direcciones
    o = {t: oa[oa["muestra"] == t].copy() for t in ("higado", "hueso")}
    for t, otro in (("hueso", "higado"), ("higado", "hueso")):
        g = o[t]["bc"].map(gem)
        o[t]["expo"] = g.map(lambda x: tot[otro].get(x, 0)).values
        o[t]["socio"] = g.isin(gem_llam[otro].keys()).values
        o[t]["limpio_T"] = ~o[t]["socio"].values
        o[t]["limpio_A"] = o[t]["expo"].values <= 25
    # tipo del socio del hígado para las células del hueso
    oh = o["higado"].copy()
    oh["gem"] = oh["bc"].map(gem)
    obh = ob[ob["bc"].isin(oh["bc"])].set_index("bc")
    oh["malB"] = obh.loc[oh["bc"], "maligna_B_explor"].values
    oh = oh.set_index("gem")
    gb = o["hueso"]["bc"].map(gem)

    def tipo_socio(x):
        if x not in gem_llam["higado"]:
            return "sin_socio"
        if x not in oh.index:
            return "socio_quitado_QC"
        r = oh.loc[x]
        if r["tipo"] == "hepatocito":
            return "socio_hepatocito"
        if bool(r["pob_usada"]):
            return "socio_maligna_A"
        return "socio_otro"
    o["hueso"]["tipo_socio"] = gb.map(tipo_socio).values

    nb, nh = len(o["hueso"]), len(o["higado"])
    sb, sh = int(o["hueso"]["socio"].sum()), int(o["higado"]["socio"].sum())
    hepb = (o["hueso"]["tipo"] == "hepatocito").values
    malA_b = o["hueso"]["pob_usada"].values.astype(bool)
    obb = ob[ob["bc"].isin(o["hueso"]["bc"])].set_index("bc")
    malB_b = obb.loc[o["hueso"]["bc"], "maligna_B_explor"].values.astype(bool)
    res["a_medida"] = {
        "hueso_con_socio_llamado": _w(sb, nb),
        "higado_con_socio_llamado": _w(sh, nh),
        "razon_observada_socio_hueso_vs_higado": (sb / nb) / (sh / nh),
        "razon_esperada_coencapsulacion_aleatoria": len(calls["higado"]) / len(calls["hueso"]),
        "hueso_limpio_A_umi_le_25": _w(int(o["hueso"]["limpio_A"].sum()), nb),
        "hueso_exposicion_p50_p90_p99": [float(np.percentile(o["hueso"]["expo"], q)) for q in (50, 90, 99)],
        "higado_exposicion_p50_p90_p99": [float(np.percentile(o["higado"]["expo"], q)) for q in (50, 90, 99)],
        "hepatocitos_hueso_con_socio": _w(int(o["hueso"]["socio"].values[hepb].sum()), int(hepb.sum())),
        "hepatocitos_hueso_tipo_socio": pd.Series(o["hueso"]["tipo_socio"].values[hepb]).value_counts().to_dict(),
        "resto_hueso_con_socio": _w(int(o["hueso"]["socio"].values[~hepb].sum()), int((~hepb).sum())),
        "malignas_A_hueso_con_socio": _w(int(o["hueso"]["socio"].values[malA_b].sum()), int(malA_b.sum())),
        "malignas_A_hueso_tipo_socio": pd.Series(o["hueso"]["tipo_socio"].values[malA_b]).value_counts().to_dict(),
        "malignas_B_hueso_con_socio": _w(int(o["hueso"]["socio"].values[malB_b].sum()), int(malB_b.sum())),
        "tipo_socio_todas_hueso": o["hueso"]["tipo_socio"].value_counts().to_dict(),
    }
    # P(maligna_A | tipo de socio)
    pm = {}
    for k in ["sin_socio", "socio_maligna_A", "socio_hepatocito", "socio_otro", "socio_quitado_QC"]:
        m = (o["hueso"]["tipo_socio"] == k).values
        pm[k] = {"A": _w(int(malA_b[m].sum()), int(m.sum())), "B": _w(int(malB_b[m].sum()), int(m.sum()))}
    m1 = (o["hueso"]["tipo_socio"] == "socio_maligna_A").values
    m0 = (o["hueso"]["tipo_socio"] == "sin_socio").values
    pm["dif_socio_maligno_menos_limpio_A"] = boot_diff(int(malA_b[m1].sum()), int(m1.sum()), int(malA_b[m0].sum()), int(m0.sum()))
    res["a_P_maligna_por_socio"] = pm
    print("(a)", {k: (v["k"], v["n"], round(v["frac"], 3)) if isinstance(v, dict) and "k" in v else v
                  for k, v in res["a_medida"].items()})
    print("    P(maligna|socio):", {k: (v["A"]["k"], v["A"]["n"]) if isinstance(v, dict) else v for k, v in pm.items()})

    # (c) alfa y atribución
    adb = carga("hueso")
    assert (adb.obs_names == o["hueso"]["bc"].values).all()
    adl = sc.read_10x_h5(__import__("_comun").ruta("higado", "_filtered_feature_barcode_matrix.h5"), gex_only=True)
    adl.var_names = adl.var["gene_ids"].astype(str).values
    assert (adl.var_names == adb.var_names).all()
    adl.X = adl.X.tocsr()
    pos_l = {b: i for i, b in enumerate(adl.obs_names)}
    soc_idx = np.array([pos_l[gem_llam["higado"][gem(b)]] if o["hueso"]["socio"].values[i] else -1
                        for i, b in enumerate(adb.obs_names)])
    tiene = soc_idx >= 0
    P = sp_rows(adl.X, soc_idx)  # cuentas del socio (0 si no hay)
    hep_b = suma(adb, HEP5)
    hep_s = np.asarray(P[:, np.isin(adb.var["simbolo"].values, HEP5)].sum(axis=1)).ravel()
    umi_b = np.asarray(adb.X.sum(axis=1)).ravel()
    umi_s = np.asarray(P.sum(axis=1)).ravel()
    par = tiene & (hep_s >= 20)
    alfa = hep_b[par] / hep_s[par]
    rng = np.random.default_rng(SEED)
    bmed = [np.median(rng.choice(alfa, len(alfa))) for _ in range(1000)]
    a_med = float(np.median(alfa))
    res["c_alfa"] = {"n_pares_socio_HEP5_ge20": int(par.sum()), "alfa_mediana": a_med,
                     "alfa_ic95_boot": [float(np.percentile(bmed, 2.5)), float(np.percentile(bmed, 97.5))],
                     "alfa_p25_p75": [float(np.percentile(alfa, 25)), float(np.percentile(alfa, 75))],
                     "alfa_media": float(np.mean(alfa)), "alfa_p90": float(np.percentile(alfa, 90)),
                     "UMI_hueso_sobre_socio_en_hepatocitos_hueso_mediana": float(np.median(umi_b[hepb & tiene] / np.maximum(umi_s[hepb & tiene], 1)))}
    print("(c) alfa:", res["c_alfa"])
    atrib = {}
    for nombre, msk in (("malignas_A", malA_b), ("malignas_B", malB_b)):
        d = {"n": int(msk.sum()), "n_con_socio": int((msk & tiene).sum())}
        for g in GENES_ATRIB + ["__UMI_total__"]:
            if g == "__UMI_total__":
                obs_g, soc_g = umi_b, umi_s
            else:
                j = np.where(adb.var["simbolo"].values == g)[0]
                if len(j) == 0:
                    continue
                obs_g = np.asarray(adb.X[:, j[0]].todense()).ravel()
                soc_g = np.asarray(P[:, j[0]].todense()).ravel()
            o_sum = obs_g[msk].sum()
            fr = lambda al: float(min(1.0, al * soc_g[msk].sum() / max(o_sum, 1)))
            d[g] = {"UMI_obs": int(o_sum), "frac_atribuible_fuga": fr(a_med),
                    "frac_atribuible_alfa_media": fr(res["c_alfa"]["alfa_media"]),
                    "cota_alfa_p90": fr(res["c_alfa"]["alfa_p90"]),
                    "rango_alfa_p25_p75": [fr(res["c_alfa"]["alfa_p25_p75"][0]), fr(res["c_alfa"]["alfa_p25_p75"][1])]}
        atrib[nombre] = d
    res["c_atribucion"] = atrib
    print("(c) atribuible (malignas A) mediana/media/p90:", {g: (round(v["frac_atribuible_fuga"], 4), round(v["frac_atribuible_alfa_media"], 4), round(v["cota_alfa_p90"], 4)) for g, v in atrib["malignas_A"].items() if isinstance(v, dict)})

    # comparación hueso con socio hepático/maligno frente a sin socio (epiteliales A)
    fg = np.asarray(adb.X[:, np.where(adb.var["simbolo"].values == "FGFR1")[0][0]].todense()).ravel()
    epi = n_detectados(adb, EPI4) >= 1
    comp = {}
    for k in ["sin_socio", "socio_maligna_A", "socio_hepatocito", "socio_otro", "socio_quitado_QC"]:
        m = (o["hueso"]["tipo_socio"] == k).values
        comp[k] = {"n": int(m.sum()), "FGFR1_UMI_por_celula": float(fg[m].mean()) if m.any() else None,
                   "FGFR1_det": _w(int((fg[m] > 0).sum()), int(m.sum())), "epitelial_ge1": _w(int(epi[m].sum()), int(m.sum())),
                   "HEP5_UMI_por_celula": float(hep_b[m].mean()) if m.any() else None,
                   "UMI_mediana": float(np.median(umi_b[m])) if m.any() else None}
    res["c_comparacion_por_socio"] = comp

    # (b) re-llamada de malignas en el hueso: inferCNV propio, limpio_T y todas
    ref = o["hueso"]["tipo"].isin(REF_TIPOS).values
    excl = (o["hueso"]["tipo"] == "hepatocito").values
    calls_b = {}
    cv = coords()
    w8 = genes_ventana(cv, "8p11")
    w11 = genes_ventana(cv, "{{LOCUS}}")
    for nombre, sub in (("todas", np.ones(nb, bool)), ("limpio_T", o["hueso"]["limpio_T"].values),
                        ("limpio_A", o["hueso"]["limpio_A"].values)):
        a2 = adb[sub].copy()
        r, ab, lim, X, chrpos = infercnv_propio(a2, ref[sub], excl[sub])
        epi_s = n_detectados(a2, EPI4) >= 1
        mal = ab & epi_s & ~ref[sub]
        s8, _, _ = score_ventana(a2, w8, ref[sub])
        s11, _, _ = score_ventana(a2, w11, ref[sub])
        c8 = s8 > np.percentile(s8[ref[sub]], 99)
        c11 = s11 > np.percentile(s11[ref[sub]], 99)
        prof = perfil_cromosomas(X, chrpos, mal, ref[sub])
        v = np.zeros(nb, bool); v[np.where(sub)[0][mal]] = True
        calls_b[f"maligna_T_{nombre}"] = v
        v8 = np.zeros(nb, bool); v8[np.where(sub)[0][c8 & epi_s & ~ref[sub]]] = True
        calls_b[f"ventana8p11_epi_{nombre}"] = v8
        calls_b[f"score8p11_{nombre}"] = np.where(sub, 0.0, np.nan); calls_b[f"score8p11_{nombre}"][sub] = s8
        res[f"b_{nombre}"] = {
            "n_celulas": int(sub.sum()), "n_referencia": int(ref[sub].sum()), "corte_p99_ref": lim,
            "n_aberrantes": int(ab.sum()), "n_epitelial_ge1": int(epi_s.sum()), "n_malignas_T": int(mal.sum()),
            "frac_malignas": _w(int(mal.sum()), int(sub.sum())),
            "concordancia_con_A_en_subconjunto": {"A": int(malA_b[sub].sum()), "ambos": int((malA_b[sub] & mal).sum())},
            "n_ventana8p11_epi": int((c8 & epi_s & ~ref[sub]).sum()),
            "malignas_T_con_8p11_alta": _w(int((mal & c8).sum()), int(mal.sum())),
            "malignas_T_con_11q13_alta": _w(int((mal & c11).sum()), int(mal.sum())),
            "referencia_con_8p11_alta": _w(int((ref[sub] & c8).sum()), int(ref[sub].sum())),
            "perfil_cromosomas_mal_menos_ref_x100": {k: round(v * 100, 2) for k, v in prof.items()}}
        print(f"(b) {nombre}: n {sub.sum()} malignas_T {mal.sum()} (A en subconj {malA_b[sub].sum()}, ambos {(malA_b[sub] & mal).sum()}); "
              f"8p11 alta en malignas {(mal & c8).sum()}/{mal.sum()}, en ref {(ref[sub] & c8).sum()}/{ref[sub].sum()}")
        del a2, X
        gc.collect()
    # malignas (todas) con socio frente a sin socio
    mt = calls_b["maligna_T_todas"]
    res["b_malignas_T_todas_por_socio"] = {k: _w(int(mt[(o["hueso"]["tipo_socio"] == k).values].sum()),
                                                 int((o["hueso"]["tipo_socio"] == k).sum()))
                                           for k in ["sin_socio", "socio_maligna_A", "socio_hepatocito", "socio_otro", "socio_quitado_QC"]}
    calls_b["limpio_T"] = o["hueso"]["limpio_T"].values
    calls_b["limpio_A"] = o["hueso"]["limpio_A"].values
    calls_b["socio"] = o["hueso"]["socio"].values
    calls_b["tipo_socio"] = o["hueso"]["tipo_socio"].values.astype(str)
    calls_b["expo"] = o["hueso"]["expo"].values
    np.savez_compressed(out("hueso_calls.npz"), **calls_b)
    np.savez_compressed(out("higado_gem.npz"), limpio_T=o["higado"]["limpio_T"].values,
                        limpio_A=o["higado"]["limpio_A"].values, expo=o["higado"]["expo"].values)
    guarda(res, "fuga.json")
    print("guardado fuga.json")


def sp_rows(X, idx):
    """Filas de X (CSR) según idx; -1 = fila de ceros."""
    import scipy.sparse as sp
    ok = idx >= 0
    sub = X[idx[ok]].tocsr()
    rows = np.where(ok)[0]
    perm = sp.csr_matrix((np.ones(len(rows)), (rows, np.arange(len(rows)))), shape=(len(idx), len(rows)))
    return (perm @ sub).tocsr()
