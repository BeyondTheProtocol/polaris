"""P1(b) — ¿Hígado y hueso son el mismo clon? Perfil de expresión relativa por bins genómicos de 10 Mb
(pseudobulk tumor / referencia de su propia muestra, log2, mediana por bin, centrado), comparado
entre hígado y hueso con GEM limpio, hueso con socio, y controles no tumorales (expresión, no CNV).
Más las ventanas focales 8p11 y {{LOCUS}} y la lectura de copias por ADN del hueso (adn.json).
EXPLORATORIO (espec. v2). Salida: tribunal/clon.json.
"""
import json

import numpy as np

from _comun import REF_TIPOS, carga, coords, guarda, out

BIN = 10e6


def perfil(ad, mt, mr, cv, genes_ok):
    X = ad.X
    umi_t = X[mt].sum()
    umi_r = X[mr].sum()
    ct = np.asarray(X[mt].sum(axis=0)).ravel() / umi_t * 1e4
    cr = np.asarray(X[mr].sum(axis=0)).ravel() / umi_r * 1e4
    lr = np.log2((ct + 0.05) / (cr + 0.05))
    v = cv.reindex(ad.var_names)
    ok = genes_ok & v["chromosome"].notna().values
    df = {"chr": v["chromosome"].values[ok], "bin": (v["start"].values[ok] // BIN).astype(int), "lr": lr[ok]}
    import pandas as pd
    d = pd.DataFrame(df)
    d = d[~d["chr"].isin(["chrX", "chrY", "chrM"])]
    p = d.groupby(["chr", "bin"])["lr"].agg(["median", "size"])
    p = p[p["size"] >= 5]["median"]
    return p - p.median()


def run(*a):
    cv = coords()
    adl = carga("higado")
    zl = np.load(out("higado_calls.npz"))
    adb = carga("hueso")
    zb = np.load(out("hueso_calls.npz"), allow_pickle=True)
    assert (adl.var_names == adb.var_names).all()
    # genes con expresión suficiente en ambas muestras (pseudobulk >= 0,5 CP10k en alguna población)
    cpl = np.asarray(adl.X.sum(axis=0)).ravel() / adl.X.sum() * 1e4
    cpb = np.asarray(adb.X.sum(axis=0)).ravel() / adb.X.sum() * 1e4
    ok = (cpl >= 0.5) & (cpb >= 0.5)
    refb = adb.obs["tipo"].isin(REF_TIPOS).values
    lim = zb["limpio_T"].astype(bool)
    malb = zb["maligna_T_todas"].astype(bool)
    mal_lim = zb["maligna_T_limpio_T"].astype(bool)  # re-llamada SOLO con GEM limpio (la del encargo)
    soc = zb["socio"].astype(bool)
    perf = {
        "higado_luminal": perfil(adl, zl["lum"], zl["ref"], cv, ok),
        "higado_8p11_p99": perfil(adl, zl["8p11_p99"], zl["ref"], cv, ok),
        "hueso_malignas_limpio": perfil(adb, mal_lim, refb & lim, cv, ok),
        "hueso_malignas_con_socio": perfil(adb, malb & soc, refb & lim, cv, ok),
        "CTRL_higado_hepatocitos": perfil(adl, zl["hep"], zl["ref"], cv, ok),
        "CTRL_hueso_estroma_limpio": perfil(adb, (adb.obs["tipo"] == "fibroblasto_estelar").values & lim, refb & lim, cv, ok),
        "CTRL_hueso_osteoclasto": perfil(adb, (adb.obs["tipo"] == "osteoclasto").values, refb & lim, cv, ok),
    }
    import pandas as pd
    P = pd.DataFrame(perf).dropna()
    rng = np.random.default_rng(20261016)
    cor = {}
    base = "higado_luminal"
    for k in P.columns:
        if k == base:
            continue
        r = float(np.corrcoef(P[base], P[k])[0, 1])
        bs = []
        for _ in range(1000):
            i = rng.integers(0, len(P), len(P))
            bs.append(np.corrcoef(P[base].values[i], P[k].values[i])[0, 1])
        cor[k] = {"r_pearson_bins": r, "ic95_boot_bins": [float(np.nanpercentile(bs, 2.5)), float(np.nanpercentile(bs, 97.5))]}
    # sin chr8 ni chr11: si r sigue alto, la correlación es de linaje/programa, no de los amplicones
    sin = P[~P.index.get_level_values(0).isin(["chr8", "chr11"])]
    cor_sin = {k: float(np.corrcoef(sin[base], sin[k])[0, 1]) for k in P.columns if k != base}
    # bins focales
    foc = {}
    for nombre, (ch, pos) in {"8p11": ("chr8", 38.4e6), "{{LOCUS}}": ("chr11", 69.7e6)}.items():
        key = (ch, int(pos // BIN))
        foc[nombre] = {k: (float(P.loc[key, k]) if key in P.index else None) for k in P.columns}
        foc[nombre]["rango_percentil_en_higado_luminal"] = float((P[base] < P.loc[key, base]).mean()) if key in P.index else None
    n_cel = {"hueso_malignas_limpio": int(mal_lim.sum()), "hueso_malignas_con_socio": int((malb & soc).sum()),
             "higado_luminal": int(zl["lum"].sum()), "higado_8p11_p99": int(zl["8p11_p99"].sum())}
    adn = json.load(open(out("adn.json")))["hueso"]["lectura_copias_ADN_ventanas"]
    res = {"aviso": "EXPLORATORIO; correlación de expresión relativa por bins: mezcla CNV y programa de linaje; los CTRL dan la línea base de programa",
           "n_bins": int(len(P)), "n_genes_usados": int(ok.sum()), "n_celulas": n_cel, "correlaciones_con_higado_luminal": cor,
           "correlaciones_sin_chr8_chr11": cor_sin,
           "focales_log2_rel": foc, "copias_ADN_hueso": {w: adn[w]["malignas_A"] for w in adn}}
    guarda(res, "clon.json")
    print("bins", len(P), "| n", n_cel)
    print({k: (round(v["r_pearson_bins"], 2), [round(x, 2) for x in v["ic95_boot_bins"]]) for k, v in cor.items()})
    print("sin chr8/chr11:", {k: round(v, 2) for k, v in cor_sin.items()})
    print("focales:", {w: {k: (round(x, 2) if isinstance(x, float) else x) for k, x in d.items()} for w, d in foc.items()})
