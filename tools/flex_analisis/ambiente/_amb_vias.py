# Vias PROPIAS (REGLAS_VIAS_PROPIAS_v1): ADN genomico por grupo como etiqueta independiente, metacelulas y tabla
# comparativa entre vias (cierre, tipo DFCI v4, v4 sin linaje, metacelulas). Salidas: vias.json, vias_tabla.csv y
# vias_leiden_ADN.csv en la carpeta del ambiente. stdout: solo agregados.
import contextlib
import io
import json
import math
import os

import numpy as np
import pandas as pd
import scipy.sparse as sp

import _a
from _a import TC, _c
import _final as F
import _amb_final as AF

REF_TIPOS_H = ["hepatocito", "NK", "endotelio", "mieloide", "fibroblasto_estelar"]
DIANAS = ["ANKRD30A", "TPBG", "ERBB3"]


def katz(s1, o1, s2, o2):
    # razon de razones (s1/o1)/(s2/o2) con IC95 de Katz (log); +0,5 a todo si hay ceros
    if min(s1, o1, s2, o2) == 0:
        s1, o1, s2, o2 = s1 + 0.5, o1 + 0.5, s2 + 0.5, o2 + 0.5
    rr = (s1 / o1) / (s2 / o2)
    se = math.sqrt(1 / s1 + 1 / o1 + 1 / s2 + 1 / o2)
    return rr, rr * math.exp(-1.959964 * se), rr * math.exp(1.959964 * se)


def adn(L0, grupos):
    o = L0.o
    G = o["son_ven_counts"].astype(np.int64)
    ns = o["son_ven_nsondas"].astype(float)
    ch = o["son_ven_chr"].astype(str)
    ors = o["son_or_sum"].astype(np.int64)
    pool = grupos["pool_cierre"]
    tasa = G[pool].sum(axis=0) / (pool.sum() * np.maximum(ns, 1))
    ref = np.isin(L0.tipo, REF_TIPOS_H)
    out = {"n_OR_sondas": float(o["son_n_or"]), "OR_UMI": {"pool": int(ors[pool].sum()), "referencia": int(ors[ref].sum())}}
    for c, w_ in (("chr8", "8p11"), ("chr11", "{{LOCUS}}")):
        sil = (ch == c) & (tasa < 0.001) & (ns > 0)
        S = G[:, sil].sum(axis=1)
        sr, orr = int(S[ref].sum()), int(ors[ref].sum())
        info = {"genes_silentes": [str(x) for x in o["son_ven_sym"][sil]], "sondas_silentes": float(ns[sil].sum()), "ref": {"S": sr, "OR": orr}}
        for k, m in grupos.items():
            s_, o_ = int(S[m].sum()), int(ors[m].sum())
            rr, lo, hi = katz(s_, o_, sr, orr)
            info[k] = {"n": int(m.sum()), "S": s_, "OR": o_, "copias": 2 * rr, "ic95_copias": [2 * lo, 2 * hi],
                       "excluye_2": bool(lo > 1 or hi < 1), "garwood_S": list(_c.garwood(s_))}
        out[w_] = info
    return out


def contraste(info, a, b):
    x, y = info[a], info[b]
    rr, lo, hi = katz(x["S"], x["OR"], y["S"], y["OR"])
    return {"razon": rr, "ic95": [lo, hi], "excluye_1": bool(lo > 1 or hi < 1)}


def metacelulas(L, gsel_nl, mascaras):
    # k-means determinista por grupo en 50 componentes (genes sin linaje); suma de conteos por metacelula
    from sklearn.cluster import KMeans
    from sklearn.decomposition import TruncatedSVD
    Xg = L.X[:, gsel_nl].astype(np.float64).tocsr()
    Xn = sp.diags(1e4 / np.maximum(L.umi, 1)) @ Xg
    Xn.data = np.log1p(Xn.data)
    etiqueta, miembros = [], []
    for nombre, m in mascaras.items():
        idx = np.where(m)[0]
        k = max(2, len(idx) // 30)
        Z = TruncatedSVD(50, random_state=_a.SEED).fit_transform(Xn[idx])
        lab = KMeans(k, n_init=4, random_state=_a.SEED).fit_predict(Z)
        for j in range(k):
            mm = idx[lab == j]
            if len(mm) >= 15:
                miembros.append(mm)
                etiqueta.append(nombre)
    filas = np.concatenate([np.full(len(x), i) for i, x in enumerate(miembros)])
    A = sp.csr_matrix((np.ones(len(filas)), (filas, np.concatenate(miembros))), shape=(len(miembros), L.n))
    return (A @ L.X).tocsr(), np.array(etiqueta), miembros


class _Meta:
    def __init__(s, M, L, etiqueta):
        s.X = M
        s.umi = np.asarray(M.sum(axis=1)).ravel().astype(float)
        s.n = M.shape[0]
        s.o = L.o
        s.sym = L.sym
        s.tipo = etiqueta


def run(*args):
    L0, B0 = AF.obj("higado", "cruda"), AF.obj("hueso", "cruda")
    qc = AF.qc_v4(L0)
    grp = AF.grupos_v4(L0, qc)
    gsel = AF.genes_v4(L0)
    gnl = gsel & ~AF.linaje(L0, B0, L0)
    res = {"reglas_sha256": _c.sha256(os.path.join(_a.AQUI, "REGLAS_VIAS_PROPIAS_v1.json")), "commit": AF._commit(), "semilla": _a.SEED,
           "genes_sin_linaje": int(gnl.sum())}
    pool_c = np.isin(L0.tipo, ["epitelial", "no_asignado"]) & ~(L0.ndet(TC.HEP5) >= 2)
    z = np.load(os.path.join(_c.CIERRE, "higado_conjuntos_cierre.npz"))
    vias = {"cierre_identidad_y_CNV": z["identidad_y_CNV"].astype(bool)}
    with contextlib.redirect_stdout(io.StringIO()):
        r4, c4, _ = AF.v4(L0, grp, gsel)
        r4n, c4n, _ = AF.v4(L0, grp, gnl)
    vias["v4_tipo_DFCI"] = c4 & grp["no_ref"]
    vias["v4_sin_linaje"] = c4n & grp["no_ref"]
    res["celula"] = {k: {kk: r[kk] for kk in ("AUC_pool_vs_hepatocitos", "AUC_pool_vs_periportales", "metricas")}
                     for k, r in (("v4_tipo_DFCI", r4), ("v4_sin_linaje", r4n))}
    # POST HOC (red-team 17-oct): referencia de metacelulas SIN hepatocitos (antes los pericentrales estaban en ref y en hep)
    masc = {"pool": grp["pool"], "hepatocito": grp["hep"], "periportal": grp["pp"], "referencia": grp["ref"] & ~(L0.tipo == "hepatocito")}
    M, et, miembros = metacelulas(L0, gnl, masc)
    Lm = _Meta(M, L0, et)
    z0 = np.zeros(len(et), bool)
    gm = {"ref": et == "referencia", "h1": z0, "h2": z0, "pool": et == "pool", "hep": et == "hepatocito", "pp": et == "periportal",
          "no_ref": et != "referencia"}
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        C, _ = AF.infercnv_v4(Lm, gnl, gm)
    scm = AF.corr_con(C, C[gm["pool"]].mean(axis=0))
    thr = float(np.quantile(scm[gm["ref"]], 0.985))
    callm = scm > thr
    callc = np.zeros(L0.n, bool)
    for i, mm in enumerate(miembros):
        if callm[i] and et[i] == "pool":
            callc[mm] = True
    vias["metacelulas_sin_linaje"] = callc
    mal68 = B0.o["trib_maligna_T_limpio_T"].astype(bool)
    refB = np.isin(B0.tipo, TC.REF_TIPOS) & B0.o["trib_limpio_T"].astype(bool)
    nulo = np.isin(L0.tipo, AF.NULO)
    hp = L0.tipo == "hepatocito"
    gok, brazo, validos, _ = F.brazos_genes(L0, B0, mal68, refB, ~nulo & ~hp, nulo, hp)
    plantilla = F.centra(F.valores_brazo(B0, gok, brazo, validos, refB))[mal68].mean(axis=0)
    VM = F.centra(F.valores_brazo(Lm, gok, brazo, validos, gm["ref"]))
    sc_b = F.corr_filas(VM, plantilla)
    res["metacelulas"] = {"n": {k: int((et == k).sum()) for k in masc}, "umi_mediana": {k: float(np.median(Lm.umi[et == k])) for k in masc},
                          "AUC_infercnv_pool_vs_hep": F.auc(scm[gm["pool"]], scm[gm["hep"]]),
                          "AUC_brazos_cierre_pool_vs_hep": F.auc(sc_b[gm["pool"]], sc_b[gm["hep"]]),
                          "AUC_brazos_cierre_pool_vs_periportales": F.auc(sc_b[gm["pool"]], sc_b[gm["pp"]]),
                          "AUC_infercnv_pool_vs_periportales": F.auc(scm[gm["pool"]], scm[gm["pp"]]),
                          "llamadas_periportales": _c.w(int((callm & gm["pp"]).sum()), int(gm["pp"].sum())),
                          "POST_HOC": "referencia de metacelulas sin hepatocitos; periportales aparte (red-team 17-oct)",
                          "r_perfil_pool_con_hueso_sin_chr8_11": float(np.corrcoef(VM[gm["pool"]].mean(axis=0), plantilla)[0, 1]),
                          "r_perfil_hep_con_hueso": float(np.corrcoef(VM[gm["hep"]].mean(axis=0), plantilla)[0, 1]),
                          "r_perfil_ref_con_hueso": float(np.corrcoef(VM[gm["ref"]].mean(axis=0), plantilla)[0, 1]),
                          "brazos": len(validos), "genes_brazos": int(gok.sum()),
                          "llamadas_pool": _c.w(int((callm & gm["pool"]).sum()), int(gm["pool"].sum())),
                          "llamadas_hep": _c.w(int((callm & gm["hep"]).sum()), int(gm["hep"].sum())),
                          "celulas_llamadas": int(callc.sum())}
    print("METACELULAS:", json.dumps(_c._r(res["metacelulas"]), ensure_ascii=False))
    return parte_adn(L0, res, vias, pool_c, hp, gnl)


def parte_adn(L0, res, vias, pool_c, hp, gnl):
    grupos = {"pool_cierre": pool_c, "hepatocitos": hp, "inmune_endo_estroma": np.isin(L0.tipo, AF.NULO + ["fibroblasto_estelar"])}
    for v, m in vias.items():
        grupos[v + "|llamadas"] = m & pool_c
        grupos[v + "|no_llamadas_pool"] = pool_c & ~m
    import anndata as an
    import scanpy as sc
    idx = np.where(pool_c)[0]
    ad = an.AnnData(X=L0.X[idx][:, gnl].astype(np.float32))
    sc.pp.normalize_total(ad, target_sum=1e4)
    sc.pp.log1p(ad)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        sc.pp.pca(ad, n_comps=50, random_state=_a.SEED)
        sc.pp.neighbors(ad, n_neighbors=15, random_state=_a.SEED)
        sc.tl.leiden(ad, resolution=0.5, random_state=_a.SEED, flavor="igraph", n_iterations=2, directed=False)
    lab = ad.obs["leiden"].astype(str).values
    for c in sorted(set(lab), key=int):
        mm = np.zeros(L0.n, bool)
        mm[idx[lab == c]] = True
        if mm.sum() >= 300:
            grupos["leiden_" + c] = mm
    res["ADN"] = adn(L0, grupos)
    for w_ in ("8p11", "{{LOCUS}}"):
        res["ADN"][w_]["contrastes_llamadas_vs_no"] = {v: contraste(res["ADN"][w_], v + "|llamadas", v + "|no_llamadas_pool") for v in vias}
        res["ADN"][w_]["contraste_pool_vs_hepatocitos"] = contraste(res["ADN"][w_], "pool_cierre", "hepatocitos")
        res["ADN"][w_]["contraste_pool_vs_inmune_endo_estroma"] = contraste(res["ADN"][w_], "pool_cierre", "inmune_endo_estroma")
    filas_l = []
    for k in grupos:
        if k.startswith("leiden_"):
            a8 = res["ADN"]["8p11"][k]
            f = {"cluster": k, "n": int(grupos[k].sum()), "S": a8["S"], "OR": a8["OR"], "copias_8p11": a8["copias"],
                 "ic_lo": a8["ic95_copias"][0], "ic_hi": a8["ic95_copias"][1]}
            for g in ("ESR1", "ANKRD30A", "FGFR1", "ALB", "PTPRC"):
                f[g + "_det"] = float((L0.g(g)[grupos[k]] > 0).mean())
            for v, m in vias.items():
                f["frac_" + v] = float((m & grupos[k]).sum() / grupos[k].sum())
            filas_l.append(f)
    dl = pd.DataFrame(filas_l)
    res["leiden_spearman_llamadas_vs_ADN"] = ({v: float(dl[["frac_" + v, "copias_8p11"]].corr(method="spearman").iloc[0, 1]) for v in vias}
                                              if len(dl) > 3 else None)
    tabla = []
    for v, m in vias.items():
        mp = m & pool_c
        a8 = res["ADN"]["8p11"]
        fila = {"via": v, "llamadas_pool": int(mp.sum()), "frac_pool": float(mp.sum() / pool_c.sum()),
                "ADN8p11_copias_llamadas": a8[v + "|llamadas"]["copias"], "ADN8p11_ic_llamadas": a8[v + "|llamadas"]["ic95_copias"],
                "ADN8p11_copias_no_llamadas": a8[v + "|no_llamadas_pool"]["copias"],
                "ADN8p11_razon_ll_vs_no": a8["contrastes_llamadas_vs_no"][v]["razon"], "ADN8p11_ic_razon": a8["contrastes_llamadas_vs_no"][v]["ic95"]}
        for g in DIANAS:
            fila[g + "_det"] = float((L0.g(g)[mp] > 0).mean()) if mp.any() else None
        tabla.append(fila)
    dt = pd.DataFrame(tabla)
    res["estabilidad_dianas_puntos"] = {g: float(100 * (dt[g + "_det"].max() - dt[g + "_det"].min())) for g in DIANAS}
    os.environ["CIERRE_OUT"] = _a.out_dir()
    dt.to_csv(_a.outp("vias_tabla.csv"), index=False)
    dl.to_csv(_a.outp("vias_leiden_ADN.csv"), index=False)
    _c.guarda(res, "vias.json")
    pd.set_option("display.width", 300)
    pd.set_option("display.max_columns", 40)
    print(dt.drop(columns=["ADN8p11_ic_llamadas", "ADN8p11_ic_razon"]).round(3).to_string(index=False))
    print(dl.round(3).to_string(index=False))
    for w_ in ("8p11", "{{LOCUS}}"):
        a = res["ADN"][w_]
        print("ADN", w_, "silentes", a["sondas_silentes"], len(a["genes_silentes"]), "genes; ref", a["ref"],
              "; pool vs hep", _c._r(a["contraste_pool_vs_hepatocitos"]), "; pool vs inm/endo/estroma", _c._r(a["contraste_pool_vs_inmune_endo_estroma"]))
        print("   grupos:", {k: (v["S"], v["OR"], round(v["copias"], 1)) for k, v in a.items() if isinstance(v, dict) and "copias" in v and not k.startswith("leiden")})
    print("leiden spearman:", _c._r(res["leiden_spearman_llamadas_vs_ADN"]))
    print("estabilidad dianas (puntos):", _c._r(res["estabilidad_dianas_puntos"]))
    print("guardado vias.json")
