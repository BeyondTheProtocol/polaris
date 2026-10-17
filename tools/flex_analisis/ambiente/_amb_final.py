"""FINAL del ambiente (REGLAS_AMBIENTE_v1 + REGLAS_HIGADO_v4): todo desde cachés.

Por método (cruda, soupx, soupx_auto, decontx, cellbender_*):
  A. éxito de la descontaminación y retención (AMBIENTE_v1)
  B. eje 2 del cierre SIN CAMBIOS (AUC frente a hepatocitos >= 0,6)
  C. protocolo v4: QC tipo DFCI, zonación, referencia con hold-out, infercnvpy, correlación con consenso, umbral
     por FPR en H1, éxito en H2 y periportales, AUC
  D. dosis 8p11/{{LOCUS}}
  E. dianas crudo y corregido con la regla del cierre
Hueso: ADN por célula, expresión solo con sondas spliced. Salidas en ~/Clinico-PRIVADO/flex/ambiente/ (ambiente.json,
tablas_<metodo>.csv, discordancia.csv). stdout: solo agregados.
"""
import contextlib
import io
import json
import os
import time

import numpy as np
import pandas as pd
import scipy.sparse as sp

import _a
from _a import TC, _c, cachep

import _final as F  # cierre (mismo código)

R1 = json.load(open(_a.REGLAS))
R4 = json.load(open(os.path.join(_a.AQUI, "REGLAS_HIGADO_v4.json")))
POST_HOC = []
METODOS = ["cruda", "soupx", "soupx_auto", "decontx", "cellbender_fpr0.01", "cellbender_fpr0.0", "cellbender_fpr0.05"]
NULO = ["NK", "endotelio", "mieloide"]


def obj(t, metodo):
    T_ = F.T(t)
    if metodo != "cruda":
        p = cachep(f"X_{t}_{metodo}.npz")
        if not os.path.exists(p) and t == "hueso" and metodo.startswith("cellbender"):
            # POST HOC: CellBender solo se corrió en el hígado; el hueso (plantilla y retención) va crudo
            POST_HOC.append("CELLBENDER_HUESO_CRUDO: CellBender solo en el hígado; hueso con la matriz cruda")
            return T_
        if not os.path.exists(p):
            return None
        T_.X = sp.load_npz(p).tocsr()
        T_.umi = np.asarray(T_.X.sum(axis=1)).ravel().astype(float)
    return T_


def det_frac(X, mask, cols):
    return float((np.asarray(X[mask][:, cols].sum(axis=1)).ravel() > 0).mean())


# ---------------------------------------------------------------- A. éxito y retención
def exito(L0, L, B0, B):
    tes = np.isin(L.sym, R1["exito_descontaminacion"]["testigos"])
    nulo = np.isin(L.tipo, NULO)
    hep = L.tipo == "hepatocito"
    pool = np.isin(L.tipo, ["epitelial", "no_asignado"]) & ~(L0.ndet(TC.HEP5) >= 2)
    antes, despues = det_frac(L0.X, nulo, tes), det_frac(L.X, nulo, tes)
    h5 = np.isin(L.sym, TC.HEP5)
    fg = L.sym == "FGFR1"
    mal68 = B.o["trib_maligna_T_limpio_T"].astype(bool)
    refB = np.isin(B.tipo, TC.REF_TIPOS) & B.o["trib_limpio_T"].astype(bool)
    fgB = B.sym == "FGFR1"
    cv = TC.coords()
    g8 = TC.genes_ventana(cv, "8p11")

    class _Ad:  # adaptador para TC.score_ventana
        def __init__(s, T_):
            s.X, s.var_names = T_.X, T_.o["gene_ids"]
    s8 = TC.score_ventana(_Ad(B), g8, refB)[0][mal68]
    r = {"testigos_en_nulo": {"antes": antes, "despues": despues, "caida_rel": 1 - despues / antes if antes else None},
         "FGFR1_en_nulo": {"antes": det_frac(L0.X, nulo, fg), "despues": det_frac(L.X, nulo, fg)},
         "GATA3_en_nulo": {"antes": det_frac(L0.X, nulo, L.sym == "GATA3"), "despues": det_frac(L.X, nulo, L.sym == "GATA3")},
         "HEP5_retenidas_hepatocitos": float(L.X[hep][:, h5].sum() / max(L0.X[hep][:, h5].sum(), 1)),
         "FGFR1_retenidas_pool": float(L.X[pool][:, fg].sum() / max(L0.X[pool][:, fg].sum(), 1)),
         "FGFR1_det_hueso68": {"antes": det_frac(B0.X, mal68, fgB), "despues": det_frac(B.X, mal68, fgB)},
         "score_8p11_hueso68_mediana": float(np.median(s8)),
         "UMI_retenidas_higado": float(L.X.sum() / L0.X.sum()), "UMI_retenidas_hueso": float(B.X.sum() / B0.X.sum())}
    r["EXITO"] = bool(r["testigos_en_nulo"]["caida_rel"] is not None and r["testigos_en_nulo"]["caida_rel"] >= 0.75)
    rf = r["FGFR1_det_hueso68"]
    r["RETENCION"] = bool(r["HEP5_retenidas_hepatocitos"] >= 0.8 and r["FGFR1_retenidas_pool"] >= 0.7
                          and rf["despues"] >= 0.8 * rf["antes"] and r["score_8p11_hueso68_mediana"] > 0)
    return r


# ---------------------------------------------------------------- B. eje 2 del cierre
def eje2_cierre(L0, B0, L, B, metodo):
    """t1 del cierre sobre la matriz del método. La PERTENENCIA a conjuntos (>= 2 HEP5, programa biliar/mesotelial)
    se calcula con la matriz cruda para que pool y controles sean las mismas células en todos los métodos."""
    os.environ["CIERRE_OUT"] = _a.outp(os.path.join("cierre_eje2", metodo))
    F.POST_HOC.clear()
    L.ndet = L0.ndet
    B.ndet = B0.ndet
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            res, conj = F.t1(L, B)
    except ValueError as e:
        # el filtro de linaje del cierre puede dejar 0 brazos con >= 20 genes: se informa, no se fuerza
        nulo = np.isin(L.tipo, F.NULO_TIPOS)
        hep = L.tipo == "hepatocito"
        mal68 = B.o["trib_maligna_T_limpio_T"].astype(bool)
        refB = np.isin(B.tipo, TC.REF_TIPOS) & B.o["trib_limpio_T"].astype(bool)
        _, _, validos, info = F.brazos_genes(L, B, mal68, refB, ~nulo & ~hep, nulo, hep)
        POST_HOC.append(f"EJE2_SIN_BRAZOS ({metodo}): t1 del cierre no se puede calcular ({e}); brazos válidos {len(validos)}; "
                        f"genes {info}")
        pool = np.isin(L.tipo, ["epitelial", "no_asignado"]) & ~(L0.ndet(TC.HEP5) >= 2)
        return {"no_calculable": str(e), "brazos_validos": len(validos), "genes_eje2": info, "RESUELTO_criterio_cierre": False,
                "AUC_pool_vs_hepatocitos": None, "AUC_pool_vs_nulo": None, "llamada_celula": {"k": None, "n": int(pool.sum())}}, \
            {"identidad_y_CNV": np.zeros(L.n, bool)}
    assert res["pool_por_exclusion"]["n"] == 13577, "el pool del eje 2 no es el del cierre"
    e = res["eje2_cnv"]
    return {"AUC_pool_vs_hepatocitos": e["AUC_primario_vs_hepatocitos"], "AUC_pool_vs_nulo": e["AUC_primario_vs_nulo"],
            "AUC_pool_vs_fibroblastos": e["AUC_primario_vs_fibroblastos"], "llamada_celula": e["llamada_celula"]["en_primario"],
            "FP_controles": e["llamada_celula"]["falsos_positivos_controles"], "storey": e["fraccion_poblacional_storey"],
            "signo_brazos": e["validacion_cruzada_signo"]["concordancia_llamadas"],
            "signo_hepatocitos": e["validacion_cruzada_signo"]["concordancia_hepatocitos_baseline"],
            "r_pseudobulk_plantilla": e["r_pseudobulk_con_plantilla_hueso"],
            "genes_eje2": e["genes"], "n_pool": res["pool_por_exclusion"]["n"],
            "RESUELTO_criterio_cierre": bool(e["AUC_primario_vs_hepatocitos"] >= 0.6)}, conj


# ---------------------------------------------------------------- C. protocolo v4
def qc_v4(L0):
    if os.path.exists(cachep("qc_v4_higado.npz")):
        z = np.load(cachep("qc_v4_higado.npz"))
        return {k: z[k] for k in z.files}
    import scrublet as scr
    ngen = np.asarray((L0.X > 0).sum(axis=1)).ravel()
    s = scr.Scrublet(L0.X.astype(np.float32), expected_doublet_rate=0.06, random_state=_a.SEED)
    with contextlib.redirect_stdout(io.StringIO()):
        dbl, _ = s.scrub_doublets(verbose=False, use_approx_neighbors=False)  # annoy de scrublet 0.2.3 rompe con numpy 2
    cont = np.load(cachep("decontx_contaminacion_higado.npy"))
    out = {"ngen": ngen, "scrublet": dbl.astype(np.float32), "decontx_cont": cont}
    np.savez(cachep("qc_v4_higado.npz"), **out)
    return out


def zonas(L0):
    s = lambda g: L0.suma(g)  # noqa: E731
    pc, pp = s(["CYP2E1", "CYP3A4"]), s(["HAL", "SDS", "ARG1"])
    reac = L0.ndet(["KRT7", "SOX9"]) >= 1
    hep = L0.tipo == "hepatocito"
    peri_c = hep & (pc > pp) & ~reac
    peri_p = hep & ((pp > pc) | reac)
    return peri_c, peri_p, hep & ~peri_c & ~peri_p


def grupos_v4(L0, qc, sin_qc=False):
    keep = (qc["ngen"] >= 500) & (qc["scrublet"] <= 0.25) & (qc["decontx_cont"] <= 0.7)
    if sin_qc:
        keep = np.ones(L0.n, bool)
    pc, pp, sz = zonas(L0)
    noh = np.isin(L0.tipo, NULO + ["fibroblasto_estelar"])
    rng = np.random.default_rng(_a.SEED)
    hold = np.zeros(L0.n, bool)
    for t in NULO + ["fibroblasto_estelar"]:
        idx = np.where(noh & keep & (L0.tipo == t))[0]
        hold[rng.choice(idx, int(round(0.2 * len(idx))), replace=False)] = True
    hidx = np.where(hold)[0]
    h1 = np.zeros(L0.n, bool)
    h1[rng.choice(hidx, len(hidx) // 2, replace=False)] = True
    h2 = hold & ~h1
    ref = keep & (pc | noh) & ~hold
    pool = np.isin(L0.tipo, ["epitelial", "no_asignado"]) & ~(L0.ndet(TC.HEP5) >= 2) & keep
    return {"keep": keep, "pc": pc & keep, "pp": pp & keep, "sin_zona": sz, "ref": ref, "h1": h1, "h2": h2, "pool": pool,
            "hep": (L0.tipo == "hepatocito") & keep, "no_ref": keep & ~ref & ~hold}


def genes_v4(L0):
    o = L0.o
    cp = np.asarray(L0.X.sum(axis=0)).ravel() / L0.X.sum() * 1e4
    auto = np.array([c.startswith("chr") and c[3:].isdigit() for c in o["chrom"].astype(str)])
    return auto & (o["start"] >= 0) & (cp >= 0.1)


def infercnv_v4(L, gsel, grp):
    import anndata as an
    import infercnvpy as cnv
    X = L.X[:, gsel].astype(np.float32).tocsr()
    X = sp.diags(1e4 / np.maximum(L.umi, 1)).astype(np.float32) @ X
    X.data = np.log1p(X.data)
    var = pd.DataFrame({"chromosome": L.o["chrom"][gsel].astype(str), "start": L.o["start"][gsel],
                        "end": L.o["start"][gsel] + 1}, index=L.o["gene_ids"][gsel].astype(str))
    lab = np.where(grp["ref"], "ref", "otro")
    ad = an.AnnData(X=X, var=var, obs=pd.DataFrame({"g": lab}, index=[f"c{i}" for i in range(L.n)]))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        cnv.tl.infercnv(ad, reference_key="g", reference_cat=["ref"], window_size=100, step=10,
                        exclude_chromosomes=("chrX", "chrY"), n_jobs=1)
    C = ad.obsm["X_cnv"]
    C = C.toarray() if sp.issparse(C) else np.asarray(C)
    pos = ad.uns["cnv"]["chr_pos"]
    return C, pos


def corr_con(C, v):
    Cc = C - C.mean(axis=1, keepdims=True)
    vc = v - v.mean()
    return (Cc @ vc) / (np.sqrt((Cc ** 2).sum(axis=1)) * np.sqrt((vc ** 2).sum()) + 1e-12)


def llamada_v4(score, grp):
    thr = float(np.quantile(score[grp["h1"]], 0.985))
    call = score > thr
    return thr, call, {"FP_H2": _c.w(int((call & grp["h2"]).sum()), int(grp["h2"].sum())),
                       "FP_periportales": _c.w(int((call & grp["pp"]).sum()), int(grp["pp"].sum())),
                       "en_pool": _c.w(int((call & grp["pool"]).sum()), int(grp["pool"].sum())),
                       "en_no_referencia": _c.w(int((call & grp["no_ref"]).sum()), int(grp["no_ref"].sum())),
                       "en_hepatocitos_todos": _c.w(int((call & grp["hep"]).sum()), int(grp["hep"].sum()))}


def v4(L, grp, gsel):
    C, pos = infercnv_v4(L, gsel, grp)
    cons = C[grp["pool"]].mean(axis=0)
    sc = corr_con(C, cons)
    thr, call, m = llamada_v4(sc, grp)
    ok = m["FP_H2"]["frac"] <= 0.02 and m["FP_periportales"]["frac"] <= 0.01
    # sensibilidades: consenso sin chr8/chr11; carga
    cols = np.ones(C.shape[1], bool)
    chs = sorted(pos, key=lambda k: pos[k])
    for i, ch in enumerate(chs):
        if ch in ("chr8", "chr11"):
            fin = pos[chs[i + 1]] if i + 1 < len(chs) else C.shape[1]
            cols[pos[ch]:fin] = False
    sc_s = corr_con(C[:, cols], C[grp["pool"]][:, cols].mean(axis=0))
    thr_s, call_s, m_s = llamada_v4(sc_s, grp)
    carga = np.abs(C).mean(axis=1)
    thr_c, call_c, m_c = llamada_v4(carga, grp)
    r = {"umbral_corr": thr, "metricas": m, "EXITO_v4": bool(ok),
         "AUC_pool_vs_hepatocitos": F.auc(sc[grp["pool"]], sc[grp["hep"]]),
         "AUC_pool_vs_periportales": F.auc(sc[grp["pool"]], sc[grp["pp"]]),
         "corr_mediana": {k: float(np.median(sc[grp[k]])) for k in ("pool", "hep", "pp", "pc", "ref", "h2")},
         "sens_sin_chr8_11": {"umbral": thr_s, "metricas": m_s, "AUC_pool_vs_hepatocitos": F.auc(sc_s[grp["pool"]], sc_s[grp["hep"]]),
                              "jaccard_con_primaria": jacc(call & grp["pool"], call_s & grp["pool"])},
         "sens_carga": {"umbral": thr_c, "metricas": m_c, "AUC_pool_vs_hepatocitos": F.auc(carga[grp["pool"]], carga[grp["hep"]])},
         "n_ventanas": int(C.shape[1])}
    r["RESUELTO_v4"] = bool(ok and r["AUC_pool_vs_hepatocitos"] >= 0.6)
    return r, call, sc


def linaje(L, B, L0):
    """Genes de programa de linaje con la MISMA regla del cierre (|log2FC| > 1 en: malignas 68 del hueso frente a su
    referencia; candidatas frente a nulo; hepatocitos frente a nulo), sin restricción de cromosoma ni de brazo.
    Diagnóstico POST HOC: ¿la separación de v4 es CNV o linaje?"""
    nulo = np.isin(L.tipo, NULO)
    hep = L.tipo == "hepatocito"
    cand = ~nulo & ~hep
    mal68 = B.o["trib_maligna_T_limpio_T"].astype(bool)
    refB = np.isin(B.tipo, TC.REF_TIPOS) & B.o["trib_limpio_T"].astype(bool)

    def pb(T_, m):
        return np.asarray(T_.X[m].sum(axis=0)).ravel() / max(T_.X[m].sum(), 1) * 1e4

    def lfc(a, b):
        return np.abs(np.log2((a + 0.05) / (b + 0.05)))
    return (lfc(pb(B, mal68), pb(B, refB)) > 1) | (lfc(pb(L, cand), pb(L, nulo)) > 1) | (lfc(pb(L, hep), pb(L, nulo)) > 1)


def jacc(a, b):
    u = (a | b).sum()
    return float((a & b).sum() / u) if u else None


# ---------------------------------------------------------------- D. dosis
def dosis(L, grp, sets):
    o = L.o
    cp = np.asarray(L.X.sum(axis=0)).ravel() / L.X.sum() * 1e4
    Xn = sp.diags(1e4 / np.maximum(L.umi, 1)) @ L.X.astype(np.float64)
    Xn.data = np.log1p(Xn.data)
    Xn = Xn.tocsc()
    D = R4["dosis_8p11_11q13"]
    res = {}
    amp8 = [g for g in D["8p11_amplicon"] if g in set(L.sym)]
    st = o["start"][np.isin(L.sym, amp8)]
    lo, hi = st.min(), st.max()
    fl8 = (o["chrom"] == "chr8") & (cp >= 0.5) & (((o["start"] >= lo - 3e6) & (o["start"] < lo)) | ((o["start"] > hi) & (o["start"] <= hi + 3e6))) & ~np.isin(L.sym, amp8)
    a11 = np.isin(L.sym, D["11q13_amplicon"])
    st11 = o["start"][a11]
    fl11d = ((o["chrom"] == "chr11") & (cp >= 0.5) & ~a11 & ~np.isin(L.sym, D["11q13_flancos"])
             & (((o["start"] >= st11.min() - 3e6) & (o["start"] < st11.min())) | ((o["start"] > st11.max()) & (o["start"] <= st11.max() + 3e6))))
    defs = {"8p11": (np.isin(L.sym, amp8), fl8), "{{LOCUS}}": (a11, np.isin(L.sym, D["11q13_flancos"])),
            "11q13_sens_flancos_por_distancia_POSTHOC": (a11, fl11d)}
    for w_, (a, f) in defs.items():
        sa = np.asarray(Xn[:, a].mean(axis=1)).ravel()
        sf = np.asarray(Xn[:, f].mean(axis=1)).ravel()
        s = sa - sf
        s = s - np.median(s[grp["ref"]])
        p95 = np.quantile(s[grp["ref"]], 0.95)
        res[w_] = {"genes_amplicon": list(L.sym[a]), "genes_flanco": list(L.sym[f]),
                   "por_conjunto": {k: {"mediana": float(np.median(s[m])) if m.any() else None,
                                        "sobre_p95_ref": _c.w(int((s[m] > p95).sum()), int(m.sum()))} for k, m in sets.items()}}
    return res


def ventanas_v1(L, cnv_mas, grp):
    """REGLAS_AMBIENTE_v1 T3_ventanas: TC.score_ventana con referencia = nulo (matriz del método), con y sin el gen
    motor, en CNV+ (cierre), pool, hepatocitos y nulo. Fracción > 1 (log2) con Wilson."""
    cv = TC.coords()
    nulo = np.isin(L.tipo, NULO)
    pool = np.isin(L.tipo, ["epitelial", "no_asignado"]) & ~(L.ndet(TC.HEP5) >= 2)

    class _Ad:
        X, var_names = L.X, L.o["gene_ids"]
    sets = {"CNV_mas_cierre": cnv_mas, "pool": pool, "hepatocitos": L.tipo == "hepatocito", "nulo": nulo}
    out = {}
    for w_, motor in (("8p11", "FGFR1"), ("{{LOCUS}}", "CCND1")):
        for etiqueta, excl in (("con_motor", ()), ("sin_motor", (motor,))):
            g = TC.genes_ventana(cv, w_, excluir=excl)
            s_ = TC.score_ventana(_Ad(), g, nulo)[0]
            out[f"{w_}|{etiqueta}"] = {"n_genes": len(g), **{k: {"mediana": float(np.median(s_[m])) if m.any() else None,
                                                                 "frac_gt1": _c.w(int((s_[m] > 1).sum()), int(m.sum()))}
                                                             for k, m in sets.items()}}
    return out


# ---------------------------------------------------------------- E. dianas
def dianas(L, B, conj, metodo, etiqueta):
    F.TABLAS["NE_ER"] = ["CHGA", "SYP", "{{DIANA3}}", "{{DIANA4}}", "NCAM1", "ESR1"]  # declarado: el encargo pide eje NE; F.tablas solo recorre TABLAS
    os.environ["CIERRE_OUT"] = _a.outp(os.path.join("dianas", f"{metodo}_{etiqueta}"))
    with contextlib.redirect_stdout(io.StringIO()):
        meta, df = F.tablas(L, B, conj)
    genes = R4["dianas"].split(":")[0].replace(" ", "").split(",")
    df = df[df["gen"].isin(genes) & ~df["conjunto"].isin(["estroma_FAP"])].drop_duplicates(["muestra", "conjunto", "gen"])
    df.insert(0, "matriz", etiqueta)
    df.insert(0, "metodo", metodo)
    return df


def _si(v):
    return v is True or str(v) == "True"


def compara_dianas(dfa):
    """«Sostenida» = por encima del suelo Y supera la referencia emparejada (regla del cierre), en malignas_v4
    (corregido y crudo), frente al pool del cierre y al veredicto de higado_dianas. Escribe dianas_cambios.csv."""
    genes = R4["dianas"].split(":")[0].replace(" ", "").split(",")
    pc = os.path.join(_c.CIERRE, "tablas_cierre.csv")
    ph = os.path.join(_c.FLEX, "higado_dianas", "tabla_dianas_higado.csv")
    dc = pd.read_csv(pc) if os.path.exists(pc) else None
    dh = pd.read_csv(ph) if os.path.exists(ph) else None
    filas = []
    for g in genes:
        f = {"gen": g}
        if dc is not None:
            x = dc[(dc["muestra"] == "higado") & (dc["conjunto"] == "primario") & (dc["gen"] == g)].drop_duplicates("gen")
            if len(x):
                f["cierre_pool_frac"] = x["frac"].iloc[0]
                f["cierre_pool_sostenida"] = _si(x["por_encima_suelo"].iloc[0]) and _si(x["supera_ref"].iloc[0])
        if dh is not None and "gen" in dh and "veredicto" in dh:
            x = dh[dh["gen"] == g]
            if len(x):
                f["higado_dianas_veredicto"] = str(x["veredicto"].iloc[0])
        sel = dfa[(dfa["muestra"] == "higado") & (dfa["conjunto"] == "malignas_v4") & (dfa["gen"] == g)]
        for (m, mat), d in sel.groupby(["metodo", "matriz"]):
            r_ = d.iloc[0]
            f[f"{m}|{mat}|frac"] = r_.get("frac")
            f[f"{m}|{mat}|sostenida"] = _si(r_.get("por_encima_suelo")) and _si(r_.get("supera_ref"))
        filas.append(f)
    df = pd.DataFrame(filas)
    df.to_csv(_a.outp("dianas_cambios.csv"), index=False)
    return {"filas": len(df), "fuentes": {"cierre": dc is not None, "higado_dianas": dh is not None}}


def _commit():
    import subprocess
    try:
        return subprocess.check_output(["git", "-C", _a.AQUI, "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "desconocido"


def manifiesto():
    d = _a.out_dir()
    sal = {}
    for raiz, _, fs in os.walk(d):
        if os.path.relpath(raiz, d).split(os.sep)[0] in ("cache", "externa_liver_bc1"):
            continue
        for f in fs:
            if not f.startswith("MANIFIESTO"):
                rel = os.path.relpath(os.path.join(raiz, f), d)
                sal[rel] = _c.sha256(os.path.join(raiz, f))
    scr = {f: _c.sha256(os.path.join(_a.AQUI, f)) for f in sorted(os.listdir(_a.AQUI)) if f.endswith((".py", ".json", ".R"))}
    os.environ["CIERRE_OUT"] = d
    _c.guarda({"salidas": dict(sorted(sal.items())), "scripts": scr, "commit_al_generar": _commit(), "semilla": _a.SEED},
              "MANIFIESTO_AMBIENTE.json")
    print("MANIFIESTO_AMBIENTE:", len(sal), "salidas")


# ---------------------------------------------------------------- hueso: ADN y spliced
def hueso_spliced(B0):
    p = cachep("hueso_spliced.npz")
    ps = TC.probe_set()
    ps = ps[ps["included"]]
    if not os.path.exists(p):
        ad = TC.carga("hueso")
        bcs = ad.obs_names.values.astype(str)
        import h5py
        with h5py.File(TC.ruta("hueso", "_raw_probe_bc_matrix.h5"), "r") as f:
            fid = np.array([x.decode() for x in f["matrix/features/id"][:]])
        spl = set(ps.loc[ps.region == "spliced", "probe_id"])
        filas = np.where(np.isin(fid, sorted(spl)))[0]
        M, ids, _ = TC.stream_h5(TC.ruta("hueso", "_raw_probe_bc_matrix.h5"), bcs, filas=filas)
        gid = ps.set_index("probe_id").loc[ids, "gene_id"].values
        gpos = {g: j for j, g in enumerate(B0.o["gene_ids"])}
        jj = np.array([gpos.get(g, -1) for g in gid])
        ok_ = jj >= 0  # sondas sin gen en la matriz: fuera (no al gen 0)
        A = sp.csr_matrix((np.ones(int(ok_.sum())), (np.where(ok_)[0], jj[ok_])), shape=(len(ids), B0.X.shape[1]))
        S = (M.tocsr().astype(np.float64) @ A).tocsr().astype(np.int32)
        sp.save_npz(p, S)
    S = sp.load_npz(p).tocsr()
    nspl = ps[ps.region == "spliced"].groupby("gene_id").size().reindex(B0.o["gene_ids"]).fillna(0).astype(int).values
    n_unsp = int((ps.region == "unspliced").sum())
    adn = B0.o["son_or_sum"] / float(B0.o["son_n_or"]) * n_unsp
    frac = np.clip(adn / np.maximum(B0.umi, 1), 0, None)
    return S, nspl, frac


def hueso_dianas(B0, L, conj_vacio):
    S, nspl, frac = hueso_spliced(B0)
    Bs = F.T("hueso")
    Bs.X = S
    Bs.umi = np.asarray(S.sum(axis=1)).ravel().astype(float)
    Bs.o = dict(Bs.o)
    Bs.o["nsondas"] = nspl
    Bs.o["nsondas_unsp"] = np.zeros_like(nspl)
    ok = frac <= 0.5
    for k in ("trib_maligna_T_limpio_T", "trib_maligna_T_todas", "pob_usada_A", "trib_limpio_T"):
        Bs.o[k] = Bs.o[k].astype(bool) & ok
    os.environ["CIERRE_OUT"] = _a.outp(os.path.join("dianas", "hueso_spliced"))
    with contextlib.redirect_stdout(io.StringIO()):
        _, df = F.tablas(L, Bs, conj_vacio)
    genes = R4["dianas"].split(":")[0].replace(" ", "").split(",")
    df = df[(df["muestra"] == "hueso") & df["gen"].isin(genes)].drop_duplicates(["conjunto", "gen"])
    df.loc[df["gen"].isin(B0.sym[nspl == 0]), "nota"] = "sin sonda de unión"
    mal68 = B0.o["trib_maligna_T_limpio_T"].astype(bool)
    info = {"ADN_frac_mediana_todas": float(np.median(frac)), "ADN_frac_mediana_68": float(np.median(frac[mal68])),
            "excluidas_ADN_gt_0.5": _c.w(int((frac > 0.5).sum()), len(frac)),
            "68_que_quedan": int((mal68 & ok).sum()), "genes_con_sonda_spliced": int((nspl > 0).sum())}
    return info, df


# ---------------------------------------------------------------- orquestación
def run(*args):
    if args and args[0] == "manifiesto":
        return manifiesto()
    t0 = time.time()
    metodos = list(args) if args else METODOS
    L0, B0 = obj("higado", "cruda"), obj("hueso", "cruda")
    qc = qc_v4(L0)
    grp = grupos_v4(L0, qc)
    gsel = genes_v4(L0)
    res = {"reglas": {"AMBIENTE_v1": _c.sha256(_a.REGLAS), "HIGADO_v4": _c.sha256(os.path.join(_a.AQUI, "REGLAS_HIGADO_v4.json"))},
           "commit": _commit(), "semilla": _a.SEED, "prep_log": json.load(open(cachep("prep_log.json"))) if os.path.exists(cachep("prep_log.json")) else None,
           "QC_v4": {"n": L0.n, "fuera_genes_lt500": int((qc["ngen"] < 500).sum()), "fuera_scrublet_gt0.25": int((qc["scrublet"] > 0.25).sum()),
                     "fuera_decontx_gt0.7": int((qc["decontx_cont"] > 0.7).sum()), "quedan": int(grp["keep"].sum()),
                     "genes_MT_en_panel": int(np.sum(np.char.startswith(L0.sym.astype(str), "MT-")))},
           "grupos_v4": {k: int(v.sum()) for k, v in grp.items()}, "genes_infercnv": int(gsel.sum()), "metodos": {}}
    res["QC_v4_por_tipo"] = {t: {"n": int((L0.tipo == t).sum()), "genes_lt500": int(((qc["ngen"] < 500) & (L0.tipo == t)).sum()),
                                 "scrublet_gt0.25": int(((qc["scrublet"] > 0.25) & (L0.tipo == t)).sum()),
                                 "decontx_gt0.7": int(((qc["decontx_cont"] > 0.7) & (L0.tipo == t)).sum()),
                                 "quedan": int((grp["keep"] & (L0.tipo == t)).sum())} for t in sorted(set(L0.tipo))}
    grp_sq = grupos_v4(L0, qc, sin_qc=True)
    res["grupos_v4_sin_QC"] = {k: int(v.sum()) for k, v in grp_sq.items()}
    print("QC v4:", res["QC_v4"], "| grupos:", res["grupos_v4"])
    print("QC por tipo:", res["QC_v4_por_tipo"])
    llam, tabs, cierre_calls = {}, [], {}
    for m in metodos:
        L, B = obj("higado", m), obj("hueso", m)
        if L is None or B is None:
            res["metodos"][m] = "sin matriz (no se corrió)"
            print(m, ": sin matriz")
            continue
        r = {}
        tm = time.time()
        r["A_exito_retencion"] = exito(L0, L, B0, B) if m != "cruda" else None
        r["B_eje2_cierre"], conj = eje2_cierre(L0, B0, L, B, m)
        cierre_calls[m] = conj["identidad_y_CNV"]
        r["C_v4"], call, sc = v4(L, grp, gsel)
        llam[m] = call & grp["no_ref"]
        lin = linaje(L, B, L0)
        r["C_v4_sin_genes_de_linaje_POSTHOC"], call_nl, _ = v4(L, grp, gsel & ~lin)
        r["C_v4_sin_genes_de_linaje_POSTHOC"].update({"genes_usados": int((gsel & ~lin).sum()), "genes_linaje_quitados": int((gsel & lin).sum()),
                                                      "jaccard_con_v4_en_pool": jacc(call & grp["pool"], call_nl & grp["pool"])})
        r["C_v4_sin_QC"], call_sq, _ = v4(L, grp_sq, gsel)
        r["C_v4_sin_QC"]["jaccard_con_QC_en_pool"] = jacc(call & grp["pool"], call_sq & grp["pool"])
        r["E_ventanas_v1"] = ventanas_v1(L, conj["identidad_y_CNV"], grp)
        valido = (m == "cruda") or (r["A_exito_retencion"]["EXITO"] and r["A_exito_retencion"]["RETENCION"])
        r["metodo_valido"] = bool(valido)
        sets = {"malignas_v4": call & grp["no_ref"], "pool": grp["pool"], "periportales": grp["pp"], "pericentrales_ref": grp["pc"] & grp["ref"],
                "referencia": grp["ref"]}
        r["D_dosis"] = dosis(L, grp, sets)
        cj = {"malignas_v4": call & grp["no_ref"], "pool_primario": grp["pool"]}
        tabs.append(dianas(L, B, cj, m, "corregido"))
        if m != "cruda":
            tabs.append(dianas(L0, B0, cj, m, "crudo"))
        r["segundos"] = time.time() - tm
        res["metodos"][m] = r
        a, b, c = r["A_exito_retencion"], r["B_eje2_cierre"], r["C_v4"]
        print(f"== {m}: valido {valido}" + (f" | éxito {a['EXITO']} (testigos nulo {a['testigos_en_nulo']['antes']:.3f}->{a['testigos_en_nulo']['despues']:.3f}) retención {a['RETENCION']}" if a else ""))
        f3 = lambda x: "NA" if x is None else f"{x:.3f}"  # noqa: E731
        print(f"   cierre: AUC hep {f3(b['AUC_pool_vs_hepatocitos'])} nulo {f3(b['AUC_pool_vs_nulo'])} | llamadas {b['llamada_celula']['k']}/{b['llamada_celula']['n']}"
              + (f" | {b['no_calculable']}; brazos {b['brazos_validos']}" if "no_calculable" in b else ""))
        nl = r["C_v4_sin_genes_de_linaje_POSTHOC"]
        print(f"   v4 sin linaje (POSTHOC): genes {nl['genes_usados']} (quitados {nl['genes_linaje_quitados']}) AUC hep {f3(nl['AUC_pool_vs_hepatocitos'])}"
              f" pp {f3(nl['AUC_pool_vs_periportales'])} | pool {nl['metricas']['en_pool']['k']} | FP H2 {nl['metricas']['FP_H2']['k']} pp {nl['metricas']['FP_periportales']['k']}")
        print(f"   v4: AUC hep {c['AUC_pool_vs_hepatocitos']:.3f} pp {c['AUC_pool_vs_periportales']:.3f} | FP H2 {c['metricas']['FP_H2']['k']}/{c['metricas']['FP_H2']['n']}"
              f" pp {c['metricas']['FP_periportales']['k']}/{c['metricas']['FP_periportales']['n']} | pool {c['metricas']['en_pool']['k']}/{c['metricas']['en_pool']['n']}"
              f" | EXITO {c['EXITO_v4']} RESUELTO {c['RESUELTO_v4']}")
    # concordancia y discordancia
    ms = list(llam)
    res["jaccard_v4"] = {f"{a}|{b}": jacc(llam[a], llam[b]) for i, a in enumerate(ms) for b in ms[i + 1:]}
    res["jaccard_cierre"] = {f"{a}|{b}": jacc(cierre_calls[a], cierre_calls[b]) for i, a in enumerate(ms) for b in ms[i + 1:]}
    pat = pd.DataFrame({m: llam[m][grp["no_ref"]].astype(int) for m in ms})
    disc = pat.value_counts().reset_index(name="n_celulas")
    disc.to_csv(_a.outp("discordancia_v4.csv"), index=False)
    validos = [m for m in ms if m != "cruda" and res["metodos"][m]["metodo_valido"] and res["metodos"][m]["C_v4"]["RESUELTO_v4"]]
    jv = [res["jaccard_v4"].get(f"{a}|{b}") or res["jaccard_v4"].get(f"{b}|{a}") for i, a in enumerate(validos) for b in validos[i + 1:]]
    res["veredicto_v4"] = ("RESUELTO" if len(validos) >= 2 and min(jv) >= 0.5 else "RESUELTO FRÁGIL" if validos else "NO RESUELTO")
    res["metodos_que_resuelven_v4"] = validos
    vc = [m for m in ms if m != "cruda" and res["metodos"][m]["metodo_valido"] and res["metodos"][m]["B_eje2_cierre"]["RESUELTO_criterio_cierre"]]
    jc = [res["jaccard_cierre"].get(f"{a}|{b}") or res["jaccard_cierre"].get(f"{b}|{a}") or 0 for i, a in enumerate(vc) for b in vc[i + 1:]]
    res["veredicto_v1_criterio_cierre"] = ("RESUELTO" if len(vc) >= 2 and min(jc) >= 0.5 else "RESUELTO FRÁGIL" if vc else "NO RESUELTO")
    res["metodos_que_resuelven_cierre"] = vc
    print("VEREDICTO v1 (criterio del cierre):", res["veredicto_v1_criterio_cierre"], vc)
    print("Jaccard v4:", {k: round(v, 3) if v is not None else None for k, v in res["jaccard_v4"].items()})
    print("VEREDICTO v4:", res["veredicto_v4"], validos)
    # hueso spliced
    res["hueso_ADN_spliced"], dfh = hueso_dianas(B0, L0, {})
    tabs.append(dfh.assign(metodo="cruda", matriz="hueso_spliced_ADN<=0.5"))
    print("hueso:", res["hueso_ADN_spliced"])
    dfa = pd.concat(tabs).round(6)
    dfa.to_csv(_a.outp("dianas_ambiente.csv"), index=False)
    res["dianas_cambios"] = compara_dianas(dfa)
    res["POST_HOC"] = sorted(set(POST_HOC))
    res["segundos_total"] = time.time() - t0
    os.environ["CIERRE_OUT"] = _a.out_dir()  # _c.guarda escribe en ambiente/
    p = _c.guarda(res, "ambiente.json")
    print("guardado", p)
