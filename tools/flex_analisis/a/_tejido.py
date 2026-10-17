"""EXPLORATORIO tras el STOP del control de tejido (no cambia la espec.; diagnostica la causa).

1. Estructura del barcode: GEM (16 nt) + sufijo de muestra (10 nt); ¿un sufijo por muestra?
2. Socios de los «hepatocitos» del hueso en el hígado PRE-QC: ¿qué filtro los quitó?
   Y cuántas células hepatocito-altas quitó cada corte del QC del hígado.
3. Mecanismo: UMI con barcode de hueso / UMI del socio con barcode de hígado en el mismo GEM, y
   correlación del perfil con su socio frente a hepatocitos del hígado al azar.
4. Exposición continua: para cada célula, UMI de la OTRA muestra en el mismo GEM (matriz raw).
   El umbral de «GEM limpio» está fijado en UMBRAL_GEM_LIMPIO (commit previo a las tablas).
Solo agregados en stdout.
"""
import json
import os

import h5py
import numpy as np
import scanpy as sc
import scipy.sparse as sp

from _comun import SEED, TEJIDOS, guarda_json, ids_de, out, ruta_muestra

HEP5 = ["ALB", "APOA1", "SERPINA1", "HP", "TF"]
UMBRAL = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "UMBRAL_GEM_LIMPIO.json")))


def _raw(t):
    ad = sc.read_10x_h5(ruta_muestra(t, "_raw_feature_barcode_matrix.h5"), gex_only=True)
    ad.var["simbolo"] = ad.var_names.astype(str)
    ad.var_names = ad.var["gene_ids"].astype(str).values
    return ad


def exposicion(t_celula, obs_names, raw_otro_tot):
    """UMI de la otra muestra en el mismo GEM para cada célula de t_celula."""
    return np.array([raw_otro_tot.get(b[:16], 0) for b in obs_names])


def run(*a):
    res = {}
    # 1. estructura
    for t in TEJIDOS:
        with h5py.File(ruta_muestra(t, "_raw_feature_barcode_matrix.h5"), "r") as f:
            bc = [b.decode() for b in f["matrix/barcodes"][:]]
        sufs = {b[16:] for b in bc}
        res[f"sufijos_distintos_raw_{t}"] = len(sufs)
        res[f"n_barcodes_raw_{t}"] = len(bc)
    print("1. sufijos distintos por muestra (raw):", {k: v for k, v in res.items() if k.startswith("sufijos")})

    raw = {t: _raw(t) for t in TEJIDOS}
    tot = {t: dict(zip([b[:16] for b in raw[t].obs_names], np.asarray(raw[t].X.sum(axis=1)).ravel()))
           for t in TEJIDOS}
    hepids = {t: [g for g in ids_de(raw[t], HEP5).values() if g] for t in TEJIDOS}

    # 2. socios en hígado pre-QC
    adh = sc.read_h5ad(out("cel_hueso.h5ad"))
    adl = sc.read_h5ad(out("cel_higado.h5ad"))
    qcl = sc.read_h5ad(out("qc_higado.h5ad"))
    from _comun import carga
    pre = carga("higado")
    pre_g = {b[:16]: i for i, b in enumerate(pre.obs_names)}
    post_g = {b[:16] for b in qcl.obs_names}
    qcj = json.load(open(out("qc.json")))["higado"]
    mt = pre.var["simbolo"].str.upper().str.startswith("MT-").values
    X = pre.X.tocsr()
    totc = np.asarray(X.sum(axis=1)).ravel()
    pct_mt = np.asarray(X[:, mt].sum(axis=1)).ravel() / np.maximum(totc, 1) * 100
    hepc = np.asarray(X[:, [pre.var_names.get_loc(g) for g in hepids["higado"]]].sum(axis=1)).ravel()
    frac_hep = hepc / np.maximum(totc, 1)
    ngen = np.asarray((X > 0).sum(axis=1)).ravel()

    def motivo(i):
        m = []
        if totc[i] > qcj["umbral_UMI"][1] or ngen[i] > qcj["umbral_genes"][1]:
            m.append("cola_alta_UMI_genes")
        if totc[i] < qcj["umbral_UMI"][0] or ngen[i] < qcj["umbral_genes"][0]:
            m.append("cola_baja")
        if isinstance(qcj["umbral_pct_MT"], float) and pct_mt[i] > qcj["umbral_pct_MT"]:
            m.append("MT")
        return "+".join(m) or "scrublet_u_otro"

    hep_b = adh.obs["tipo"] == "hepatocito"
    socios = {}
    for b in adh.obs_names[hep_b]:
        g = b[:16]
        if g in post_g:
            k = "en_higado_post_QC"
        elif g in pre_g:
            k = "llamada_pero_quitada_QC:" + motivo(pre_g[g])
        else:
            k = "no_llamada_como_celula_en_higado"
        socios[k] = socios.get(k, 0) + 1
    res["socios_hep_hueso"] = socios
    print("2. socios en hígado de los hepatocitos del hueso:", socios)
    alto = frac_hep >= 0.005
    cortes = {}
    for i in np.where(alto)[0]:
        if pre.obs_names[i][:16] in post_g:
            continue
        k = motivo(i)
        cortes[k] = cortes.get(k, 0) + 1
    res["hepatocito_altas_higado_pre_QC"] = int(alto.sum())
    res["hepatocito_altas_quitadas_por_corte"] = cortes
    print("   células hígado pre-QC con ≥0,5% de cuentas HEP5:", int(alto.sum()), "quitadas por corte:", cortes)

    # 3. mecanismo
    rl = raw["higado"]
    rl_idx = {b[:16]: i for i, b in enumerate(rl.obs_names)}
    Xl = rl.X.tocsr()
    Xb = adh.layers["counts"].tocsr()
    comunes = np.intersect1d(adh.var_names, rl.var_names)
    ib = [adh.var_names.get_loc(g) for g in comunes]
    il = [rl.var_names.get_loc(g) for g in comunes]

    def perfil(v):
        v = np.asarray(v.todense()).ravel().astype(float)
        return np.log1p(v / max(v.sum(), 1) * 1e4)

    ratios, c_socio = [], []
    for j in np.where(hep_b.values)[0]:
        g = adh.obs_names[j][:16]
        if g not in rl_idx:
            continue
        ub = Xb[j].sum()
        ul = Xl[rl_idx[g]].sum()
        ratios.append(ub / max(ul, 1))
        c_socio.append(np.corrcoef(perfil(Xb[j][:, ib]), perfil(Xl[rl_idx[g]][:, il]))[0, 1])
    hepL = np.where(adl.obs["tipo"] == "hepatocito")[0]
    rng = np.random.default_rng(SEED)
    Xll = adl.layers["counts"].tocsr()
    il2 = [adl.var_names.get_loc(g) for g in comunes]
    c_azar = []
    for j in np.where(hep_b.values)[0]:
        for k in rng.choice(hepL, 5, replace=False):
            c_azar.append(np.corrcoef(perfil(Xb[j][:, ib]), perfil(Xll[k][:, il2]))[0, 1])
    res["mecanismo"] = {"n_con_socio_raw": len(ratios),
                        "mediana_ratio_UMI_hueso_sobre_higado_mismo_GEM": float(np.median(ratios)),
                        "rango_ratio": [float(np.min(ratios)), float(np.max(ratios))],
                        "mediana_corr_con_su_socio": float(np.median(c_socio)),
                        "mediana_corr_con_hepatocitos_al_azar": float(np.median(c_azar))}
    print("3. mecanismo:", res["mecanismo"])

    # 4. exposición continua
    expo = {}
    for t, otro in (("hueso", "higado"), ("higado", "hueso")):
        ad = adh if t == "hueso" else adl
        e = exposicion(t, ad.obs_names, tot[otro])
        np.save(out(f"exposicion_{t}.npy"), e)
        lim = UMBRAL["umi_otra_muestra_max"]
        expo[t] = {"n": int(len(e)), "frac_GEM_limpio": float((e <= lim).mean()),
                   "n_GEM_limpio": int((e <= lim).sum()),
                   "percentiles_UMI_otra_muestra_p50_p90_p99": [float(np.percentile(e, q)) for q in (50, 90, 99)],
                   "frac_hepatocito_hueso_limpio": (float((e[hep_b.values] <= lim).mean()) if t == "hueso" else None)}
    res["exposicion"] = expo
    res["umbral_gem_limpio"] = UMBRAL
    print("4. exposición:", expo)
    guarda_json(res, "tejido_exploratorio.json")
