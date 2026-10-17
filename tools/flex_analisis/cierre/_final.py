"""Etapa FINAL del cierre (REGLAS_CIERRE_v3.json + ENMIENDA_1): todo el cálculo desde las cachés de PREP.

T1 malignas del hígado (identidad frente a nulo de ambiente; CNV de brazo sin chr8/chr11 con plantilla del
hueso limpio; techo de epitelio no tumoral) · T2 fuga α por cociente de sumas · T3 Wilson en las tablas del
tribunal · T4 lectura de ADN calibrada en no malignas · T5 tablas A-C · T6 genes nuevos.
Salidas (en $CIERRE_OUT o ~/Clinico-PRIVADO/flex/cierre/): cierre.json, tablas_cierre.csv,
tablas_tribunal_wilson.csv, higado_conjuntos_cierre.npz. stdout: solo agregados.
"""
import json
import os
import subprocess

import numpy as np
import pandas as pd
import scipy.sparse as sp

import _c
from _c import (BILIAR, CENTROMEROS_MB, ACROCENTRICOS, EXCLUIR_CHR, GL, GENES_T6, HEP5, LOO_IDENTIDAD, MESOTELIAL,
                NULO_TIPOS, SEED, TC, cachep, w, wilson)

TABLAS = {"A": ["B2M", "TAP1", "TAP2", "NLRC5", "PSMB8", "PSMB9", "IRF1", "STAT1"],
          "B": ["PRAME", "MAGEA4", "MAGEA1", "MAGEA6", "MAGEA10", "MAGEA11", "MAGEA12", "MAGEC1", "MAGEC2", "CTAG2",
                "ANKRD30A", "TPBG"],
          "C": ["TACSTD2", "ERBB2", "ERBB3", "CD276", "NECTIN4", "CEACAM5", "{{DIANA2}}", "SEZ6", "{{DIANA}}", "FOLR1", "SLC39A6",
                "VTCN1", "MSLN", "CLDN6", "MUC1", "GPNMB", "ROR1", "FGFR1"],
          "T6": GENES_T6}
NO_RESOLUBLE = {"MAGEA6"}
TRAZ = {"higado": HEP5, "hueso": ["COL1A1", "COL1A2", "SPP1"]}
INMUNES = ["T", "NK", "B", "plasmatica", "mieloide", "neutrofilo", "mastocito"]
TRAZ_TUMOR = ["FGFR1", "ANKRD30A", "MUC1", "ESR1"]
POST_HOC = []


class T:
    """Una muestra desde caché."""

    def __init__(self, t):
        self.t = t
        self.X = sp.load_npz(cachep(f"X_{t}.npz")).tocsr()
        o = np.load(cachep(f"obs_{t}.npz"), allow_pickle=True)
        self.o = {k: o[k] for k in o.files}
        self.sym = self.o["simbolo"]
        self.umi = self.o["umi"].astype(float)
        self.tipo = self.o["tipo"]
        self.n = self.X.shape[0]

    def g(self, s):
        j = np.where(self.sym == s)[0]
        return np.asarray(self.X[:, j[0]].todense()).ravel() if len(j) else None

    def suma(self, gs):
        return np.asarray(self.X[:, np.isin(self.sym, gs)].sum(axis=1)).ravel()

    def ndet(self, gs):
        return np.asarray((self.X[:, np.isin(self.sym, gs)] > 0).sum(axis=1)).ravel()


# ---------------------------------------------------------------- utilidades de deciles
def grupos_decil(dec, ctrl, minimo=20, etiqueta=""):
    """Para cada decil d, índices de control a usar: los del decil; si < minimo, se añaden los de los
    deciles contiguos más cercanos (ENMIENDA_1) y se registra."""
    out = {}
    for d in range(10):
        usados = [d]
        idx = np.where(ctrl & (dec == d))[0]
        k = 1
        while len(idx) < minimo and k < 10:
            for dd in (d - k, d + k):
                if 0 <= dd <= 9:
                    usados.append(dd)
            idx = np.where(ctrl & np.isin(dec, usados))[0]
            k += 1
        if len(usados) > 1:
            POST_HOC.append(f"ENMIENDA_1 aplicada ({etiqueta}): decil {d} con < {minimo} controles, se usan los deciles {sorted(usados)}")
        out[d] = idx
    return out


# ---------------------------------------------------------------- T1 eje 1: identidad
def identidad(L, genes, cand, nulo, dec, etiqueta):
    s = L.suma(genes) / np.maximum(L.umi, 1)
    gd = grupos_decil(dec, nulo, etiqueta=etiqueta)
    p = np.ones(L.n)
    thr99 = np.full(L.n, np.inf)
    for d in range(10):
        sn = np.sort(s[gd[d]])
        m = cand & (dec == d)
        ge = len(sn) - np.searchsorted(sn, s[m], side="left")
        p[m] = (1 + ge) / (1 + len(sn))
        thr99[dec == d] = np.quantile(sn, 0.99)
    q = np.ones(L.n)
    q[cand] = _c.bh(p[cand])
    return {"s": s, "p": p, "q": q, "fdr1": cand & (q <= 0.01), "fdr5": cand & (q <= 0.05), "p99": cand & (s > thr99)}


# ---------------------------------------------------------------- T1 eje 2: CNV de brazo
def brazos_genes(L, B, mal68, refB, cand, nulo, hep):
    chrom, start = L.o["chrom"], L.o["start"]
    ok = (start >= 0) & np.isin(chrom, list(CENTROMEROS_MB)) & ~np.isin(chrom, list(EXCLUIR_CHR))
    cpL = np.asarray(L.X.sum(axis=0)).ravel() / L.X.sum() * 1e4
    cpB = np.asarray(B.X.sum(axis=0)).ravel() / B.X.sum() * 1e4
    ok &= (cpL >= 0.5) & (cpB >= 0.5)

    def pb(T_, m):
        return np.asarray(T_.X[m].sum(axis=0)).ravel() / max(T_.X[m].sum(), 1) * 1e4

    def lfc(a, b):
        return np.abs(np.log2((a + 0.05) / (b + 0.05)))
    linaje = (lfc(pb(B, mal68), pb(B, refB)) > 1) | (lfc(pb(L, cand), pb(L, nulo)) > 1) | (lfc(pb(L, hep), pb(L, nulo)) > 1)
    n_ok_previo = int(ok.sum())
    quitados = int((ok & linaje).sum())
    ok &= ~linaje
    brazo = np.array([(c + ("q" if (c in ACROCENTRICOS or s_ >= CENTROMEROS_MB.get(c, 0) * 1e6) else "p")) if o_ else ""
                      for c, s_, o_ in zip(chrom, start, ok)])
    cuenta = pd.Series(brazo[ok]).value_counts()
    validos = sorted([b for b, n in cuenta.items() if n >= 20],
                     key=lambda x: (int(x[3:-1]), x[-1]))
    ok &= np.isin(brazo, validos)
    return ok, brazo, validos, {"genes_expresados_fuera_chr8_11": n_ok_previo, "genes_quitados_por_linaje": quitados,
                                "genes_usados": int(ok.sum()), "brazos": {b: int(cuenta[b]) for b in validos}}


def valores_brazo(T_, genes_ok, brazo, validos, ref):
    Xg = T_.X[:, genes_ok].astype(np.float64).tocsr()
    Xg = sp.diags(1e4 / np.maximum(T_.umi, 1)) @ Xg
    Xg.data = np.log1p(Xg.data)
    ab = brazo[genes_ok]
    A = np.zeros((len(ab), len(validos)))
    for j, b in enumerate(validos):
        m = ab == b
        A[m, j] = 1.0 / m.sum()
    V = np.asarray((Xg @ A))
    mu = np.asarray(Xg[ref].mean(axis=0)).ravel()
    return V - mu @ A  # sin centrar por célula


def centra(V, cols=None):
    if cols is not None:
        V = V[:, cols]
    return V - V.mean(axis=1, keepdims=True)


def corr_filas(V, t):
    Vc = V - V.mean(axis=1, keepdims=True)
    tc = t - t.mean()
    return (Vc @ tc) / (np.sqrt((Vc ** 2).sum(axis=1)) * np.sqrt((tc ** 2).sum()) + 1e-12)


def llamadas_cnv(score, dec, ctrl):
    gd = grupos_decil(dec, ctrl, etiqueta="CNV p99 controles")
    thr = np.zeros(len(score))
    for d in range(10):
        thr[dec == d] = np.quantile(score[gd[d]], 0.99)
    return score > thr


def storey(score, dec, cand, ctrl, rng=None, etiqueta=""):
    """pi = 1 - P(score_cand < mediana_control)/0,5 por decil, ponderado por nº de candidatas."""
    gd = grupos_decil(dec, ctrl, etiqueta=etiqueta)
    num, den = 0.0, 0
    for d in range(10):
        ic = np.where(cand & (dec == d))[0]
        if len(ic) == 0:
            continue
        ctl = gd[d]
        if rng is not None:
            ic = rng.choice(ic, len(ic))
            ctl = rng.choice(ctl, len(ctl))
        med = np.median(score[ctl])
        pi = 1 - min(1.0, (score[ic] < med).mean() / 0.5)
        num += pi * len(ic)
        den += len(ic)
    return num / den


def auc(a, b):
    from scipy.stats import mannwhitneyu
    return float(mannwhitneyu(a, b).statistic / (len(a) * len(b)))


def t1(L, B):
    res = {}
    tipo = L.tipo
    nulo = np.isin(tipo, NULO_TIPOS)
    hep = tipo == "hepatocito"
    fib = tipo == "fibroblasto_estelar"
    cand = ~nulo & ~hep
    dec = _c.deciles(L.umi, base=L.umi[cand | nulo])
    res["n"] = {"celulas": L.n, "nulo_ambiente": int(nulo.sum()), "hepatocitos": int(hep.sum()), "candidatas": int(cand.sum()),
                "fibroblasto_estelar": int(fib.sum())}
    # eje 1 (según REGLAS): identidad frente al nulo de ambiente
    idn = identidad(L, GL, cand, nulo, dec, "identidad")
    hep2 = L.ndet(HEP5) >= 2
    res["eje1_identidad"] = {
        "FDR1": w(int(idn["fdr1"].sum()), int(cand.sum())), "FDR5": w(int(idn["fdr5"].sum()), int(cand.sum())),
        "p99_decil": w(int(idn["p99"].sum()), int(cand.sum())),
        "fp_esperados_p99": 0.01 * int(cand.sum()),
        "GL_mediana_por_decil": {"candidatas": [float(np.median(idn["s"][cand & (dec == d)])) for d in range(10)],
                                 "nulo": [float(np.median(idn["s"][nulo & (dec == d)])) for d in range(10)]},
        "GL_fraccion_en_ambiente": float(L.o["p_amb"][np.isin(L.sym, GL)].sum())}
    print("T1 eje1: FDR1", int(idn["fdr1"].sum()), "FDR5", int(idn["fdr5"].sum()), "p99", int(idn["p99"].sum()), "de", int(cand.sum()))
    # POST HOC (16-oct, tras ver el eje 1): el «nulo de ambiente» no es nulo. Se activa la rama prevista en
    # REGLAS («si_no_se_resuelve»): no resoluble célula a célula; cota poblacional sobre el pool por exclusión.
    POST_HOC.append(
        "EJE1_FALLA: FDR1 = %d y FDR5 = %d de %d candidatas; p99 por decil = %d (≈%d FP esperados). El nulo NK+endotelio+mieloide "
        "lleva GL en mediana %.4f-%.4f frente a %.4f-%.4f en las candidatas, y el ambiente de los vacíos tiene GL %.4f: con rho_HEP5 "
        "≈0,085 el ambiente explicaría ~0,0003 en el nulo; en los deciles altos el nulo lleva ~8 veces eso, y su p90 alcanza la mediana de las candidatas. El nulo no es un nulo de ambiente "
        "(multipletes o ARN tumoral fuera del modelo de vacíos). Se activa la rama «no resoluble célula a célula» de REGLAS."
        % (int(idn["fdr1"].sum()), int(idn["fdr5"].sum()), int(cand.sum()), int(idn["p99"].sum()), int(0.01 * cand.sum()),
           min(res["eje1_identidad"]["GL_mediana_por_decil"]["nulo"]), max(res["eje1_identidad"]["GL_mediana_por_decil"]["nulo"]),
           min(res["eje1_identidad"]["GL_mediana_por_decil"]["candidatas"]), max(res["eje1_identidad"]["GL_mediana_por_decil"]["candidatas"]),
           res["eje1_identidad"]["GL_fraccion_en_ambiente"]))
    POST_HOC.append("CONJUNTO_HIGADO: pool por exclusión = tipos de A «epitelial» + «no_asignado» (no inmune, no endotelio, no "
                    "estroma, no hepatocito; no depende de ningún modelo de malignidad), sin las de >= 2 genes HEP5 como manda REGLAS. Es el conjunto primario de T1 y de las "
                    "tablas del hígado, fijado antes de calcular T1-eje2, T5 y T6. Es una cota SUPERIOR de malignas, no un recuento.")
    pool0 = np.isin(tipo, ["epitelial", "no_asignado"])
    prim = pool0 & ~hep2  # REGLAS «conjunto_para_tablas»: fuera las de >= 2 genes HEP5 (regla congelada, no post hoc)
    res["pool_por_exclusion"] = {"n": int(prim.sum()), "n_antes_de_quitar_HEP5_ge2": int(pool0.sum()), "con_HEP5_ge2_quitadas": int((pool0 & hep2).sum()),
                                 "solape_A_pob_epitelial": int((prim & L.o["pob_usada_A"].astype(bool)).sum())}
    loo = {}

    # techo de epitelio no tumoral (biliar/mesotelial) dentro del primario, frente al nulo emparejado
    bm = (L.ndet(BILIAR + MESOTELIAL) >= 1)
    k_pos = int((bm & prim).sum())
    wts = _c.TC_suelo().pesos_umi(L.umi[prim], L.umi[nulo])
    tasa_nulo = float(np.sum(wts * bm[nulo]) / max(wts.sum(), 1e-12))
    esperado = tasa_nulo * prim.sum()
    exceso = max(0, int(round(k_pos - esperado)))
    p_ex, _, hi_ex = wilson(exceso, int(prim.sum()))
    _, _, hi_raw = wilson(k_pos, int(prim.sum()))
    res["techo_no_tumor"] = {"genes": BILIAR + MESOTELIAL, "k_con_programa": k_pos, "n": int(prim.sum()),
                             "tasa_nulo_emparejada": tasa_nulo, "esperado_por_nulo": esperado, "exceso": exceso,
                             "frac_exceso": p_ex, "techo_wilson_sup_exceso": hi_ex, "techo_wilson_sup_bruto": hi_raw,
                             "por_programa": {"biliar": int(((L.ndet(BILIAR) >= 1) & prim).sum()), "mesotelial": int(((L.ndet(MESOTELIAL) >= 1) & prim).sum())}}
    print("T1 techo no-tumor:", {k: v for k, v in res["techo_no_tumor"].items() if k != "genes"})

    # eje 2: CNV de brazo
    bo = B.o
    mal68 = bo["trib_maligna_T_limpio_T"].astype(bool)
    refB = np.isin(B.tipo, TC.REF_TIPOS) & bo["trib_limpio_T"].astype(bool)
    gok, brazo, validos, info = brazos_genes(L, B, mal68, refB, cand, nulo, hep)
    res["eje2_cnv"] = {"genes": info}
    VL = valores_brazo(L, gok, brazo, validos, nulo)
    VB = valores_brazo(B, gok, brazo, validos, refB)
    plantilla = centra(VB)[mal68].mean(axis=0)
    res["eje2_cnv"]["plantilla_hueso_68"] = dict(zip(validos, plantilla.tolist()))
    score = corr_filas(centra(VL), plantilla)
    ctrl = hep | fib | nulo
    decA = _c.deciles(L.umi)
    cnv = llamadas_cnv(score, decA, ctrl)
    rng = np.random.default_rng(SEED)
    pis = {}
    for nombre_c, cset in (("primario", prim), ("candidatas", cand)):
        for nombre_n, cn in (("hepatocitos", hep), ("todos_controles", ctrl)):
            est = storey(score, decA, cset, cn, etiqueta=f"Storey {nombre_c}/{nombre_n}")
            bs = [storey(score, decA, cset, cn, rng=rng) for _ in range(1000)]
            pis[f"{nombre_c}|nulo_{nombre_n}"] = {"pi": est, "ic95_boot": [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))]}
    res["eje2_cnv"]["fraccion_poblacional_storey"] = pis
    res["eje2_cnv"]["AUC_primario_vs_hepatocitos"] = auc(score[prim], score[hep])
    res["eje2_cnv"]["AUC_primario_vs_fibroblastos"] = auc(score[prim], score[fib]) if fib.any() else None
    res["eje2_cnv"]["AUC_primario_vs_nulo"] = auc(score[prim], score[nulo])
    res["eje2_cnv"]["score_mediana"] = {"primario": float(np.median(score[prim])), "hepatocitos": float(np.median(score[hep])),
                                        "fibroblastos": float(np.median(score[fib])), "nulo": float(np.median(score[nulo]))}
    res["eje2_cnv"]["llamada_celula"] = {"en_primario": w(int((cnv & prim).sum()), int(prim.sum())),
                                         "falsos_positivos_controles": w(int((cnv & ctrl).sum()), int(ctrl.sum())),
                                         "en_hepatocitos": w(int((cnv & hep).sum()), int(hep.sum())),
                                         "por_decil_UMI_en_primario": [w(int((cnv & prim & (decA == d)).sum()), int((prim & (decA == d)).sum())) for d in range(10)]}
    # concordancia de perfil (pseudobulk) con el hueso: el primario NO se definió con la plantilla
    perf = {}
    for nombre, m in (("primario", prim), ("hepatocitos", hep), ("fibroblastos", fib), ("nulo", nulo)):
        v = VL[m].mean(axis=0)
        v = v - v.mean()
        perf[nombre] = float(np.corrcoef(v, plantilla)[0, 1])
    res["eje2_cnv"]["r_pseudobulk_con_plantilla_hueso"] = perf
    # LOO por brazo y validación cruzada de signo
    loo_n, loo_j, signos = [], [], []
    for i, b in enumerate(validos):
        cols = [j for j in range(len(validos)) if j != i]
        Vc = centra(VL, cols)
        pl = centra(VB, cols)[mal68].mean(axis=0)
        sc_ = corr_filas(Vc, pl)
        cl = llamadas_cnv(sc_, decA, ctrl)
        loo_n.append(int((cl & prim).sum()))
        inter = (cl & prim & cnv).sum()
        uni = ((cl & prim) | (cnv & prim)).sum()
        loo_j.append(float(inter / max(uni, 1)))
        full_b = centra(VB)[mal68].mean(axis=0)[i]
        if abs(full_b) > 0.02:
            otros = VL[:, cols].mean(axis=1)
            va = VL[:, i] - otros
            otrosB = VB[:, cols].mean(axis=1)
            vb = (VB[:, i] - otrosB)[mal68].mean() - (VB[:, i] - otrosB)[refB].mean()
            sh = va[cl & prim].mean() - va[nulo].mean() if (cl & prim).any() else np.nan
            sh_hep = va[hep].mean() - va[nulo].mean()
            signos.append({"brazo": b, "plantilla": float(vb), "desplazamiento_llamadas": float(sh), "desplazamiento_hepatocitos": float(sh_hep),
                           "concuerda": bool(np.sign(sh) == np.sign(vb)), "concuerda_hepatocitos": bool(np.sign(sh_hep) == np.sign(vb))})
    kc = sum(s_["concuerda"] for s_ in signos)
    kh = sum(s_["concuerda_hepatocitos"] for s_ in signos)
    res["eje2_cnv"]["LOO_brazo"] = {"n_llamadas_en_primario_rango": [min(loo_n), max(loo_n)], "jaccard_min": min(loo_j)}
    res["eje2_cnv"]["validacion_cruzada_signo"] = {"brazos": signos, "concordancia_llamadas": w(kc, len(signos)),
                                                   "concordancia_hepatocitos_baseline": w(kh, len(signos))}
    print("T1 eje2: genes", info["genes_usados"], "brazos", len(validos), "| AUC prim vs hep", round(res["eje2_cnv"]["AUC_primario_vs_hepatocitos"], 3),
          "vs fib", round(res["eje2_cnv"]["AUC_primario_vs_fibroblastos"], 3), "vs nulo", round(res["eje2_cnv"]["AUC_primario_vs_nulo"], 3))
    print("   score mediana:", {k: round(v, 3) for k, v in res["eje2_cnv"]["score_mediana"].items()})
    print("   Storey:", {k: (round(v["pi"], 3), [round(x, 3) for x in v["ic95_boot"]]) for k, v in pis.items()})
    print("   llamada por célula en primario:", res["eje2_cnv"]["llamada_celula"]["en_primario"]["k"], "| FP controles",
          res["eje2_cnv"]["llamada_celula"]["falsos_positivos_controles"]["k"], "/", int(ctrl.sum()))
    print("   r pseudobulk con plantilla:", {k: round(v, 3) for k, v in perf.items()})
    print("   LOO brazo:", res["eje2_cnv"]["LOO_brazo"], "| signo:", kc, "/", len(signos), "(hepatocitos", kh, ")")

    POST_HOC.append("CNV_NO_ESPECIFICA: AUC pool frente a hepatocitos %.3f (< 0,6, umbral de REGLAS); r del pseudobulk con la plantilla del "
                    "hueso %.3f en el pool frente a %.3f en el nulo inmune/endotelial; concordancia de signo por brazo %d/%d (hepatocitos %d/%d). "
                    "La pi de Storey no se puede leer como fracción tumoral: se da como cifra descriptiva, no como corroboración."
                    % (res["eje2_cnv"]["AUC_primario_vs_hepatocitos"], perf["primario"], perf["nulo"], kc, len(signos), kh, len(signos)))
    POST_HOC.append("TABLAS_k_menor_10: columna añadida; con k < 10 en n grande el suelo ~0 hace que «por encima del suelo» sea trivial (p. ej., 1 célula de 14.553). No se lee.")
    # número final y sensibilidad al método
    n_p = int(prim.sum())
    ph = pis["primario|nulo_hepatocitos"]
    pc = pis["primario|nulo_todos_controles"]
    res["numero_final"] = {
        "veredicto": "NO RESOLUBLE CÉLULA A CÉLULA. Cota poblacional.",
        "cota_superior_pool": n_p, "cota_superior_pool_por_techo_no_tumor": n_p * (1 - (p_ex or 0)),
        "frac_pool_de_celulas_higado": w(n_p, L.n),
        "CNV_pi_nulo_hepatocitos": {"pi": ph["pi"], "ic95": ph["ic95_boot"], "celulas": [n_p * ph["ic95_boot"][0], n_p * ph["pi"], n_p * ph["ic95_boot"][1]]},
        "CNV_pi_nulo_todos_controles": {"pi": pc["pi"], "ic95": pc["ic95_boot"], "celulas": [n_p * pc["ic95_boot"][0], n_p * pc["pi"], n_p * pc["ic95_boot"][1]]},
        "sesgos": "Controles contaminados de ARN tumoral empujan pi hacia 0 (conservador); el linaje residual de la plantilla (luminal frente a inmune) lo empuja hacia arriba frente a los hepatocitos."}
    o = L.o
    sets = {"identidad_FDR1": idn["fdr1"], "identidad_FDR5": idn["fdr5"], "identidad_p99_decil": idn["p99"],
            "CNV_llamada_celula": cnv & ~ctrl, "identidad_y_CNV": prim & cnv,
            "CopyKAT_B_aneuploide": o["ck_B"] == "aneuploid", "CopyKAT_A_trozo0_aneuploide": o["ck_A_trozo0"] == "aneuploid",
            "CopyKAT_A_pur_aneuploide": o["ck_A_pur"] == "aneuploid", "inferCNV_A_aberrante": o["A_infercnv_aberrante"].astype(bool),
            "A_9385": o["pob_usada_A"].astype(bool), "B_1967": o["maligna_B"].astype(bool),
            "tribunal_luminal_T": o["trib_lum"].astype(bool), "tribunal_8p11_p99_732": o["trib_8p11_p99"].astype(bool)}
    sens = {}
    for k, m in sets.items():
        sens[k] = {"n": int(m.sum()), "en_primario": int((m & prim).sum()), "frac_del_conjunto_en_primario": float((m & prim).sum() / max(m.sum(), 1)),
                   "UMI_mediana": float(np.median(L.umi[m])) if m.any() else None,
                   "CNV_score_mediana": float(np.median(score[m])) if m.any() else None}
    sens["CopyKAT_etiquetas"] = {k: pd.Series(o[k]).value_counts().to_dict() for k in ("ck_B", "ck_A_trozo0", "ck_A_pur")}
    res["sensibilidad_metodo"] = sens
    print("T1 número final:", {k: v for k, v in res["numero_final"].items() if k not in ("sesgos",)})
    print("T1 sensibilidad:", {k: (v["n"], v["en_primario"]) for k, v in sens.items() if "n" in v})
    conj = {"primario": prim, "identidad_y_CNV": prim & cnv, "A_9385": sets["A_9385"], "tribunal_luminal_T": sets["tribunal_luminal_T"]}
    _c.guarda_npz("higado_conjuntos_cierre.npz", cnv_score=score.astype(np.float32), identidad_q=idn["q"].astype(np.float32),
                  **{k: v for k, v in conj.items()})
    return res, conj


# ---------------------------------------------------------------- T2 fuga
def t2(B):
    o = B.o
    ts = o["trib_tipo_socio"].astype(str)
    hb = B.suma(HEP5)
    hs = o["socio_hep5"].astype(float)
    us = o["socio_umi"].astype(float)
    par = ts == "socio_hepatocito"
    rng = np.random.default_rng(SEED)

    def alfa(m, boot=True):
        a = hb[m].sum() / max(hs[m].sum(), 1)
        if not boot:
            return a, None
        ix = np.where(m)[0]
        bs = []
        for _ in range(1000):
            j = rng.choice(ix, len(ix))
            bs.append(hb[j].sum() / max(hs[j].sum(), 1))
        return a, [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))]
    a, ic = alfa(par)
    hepb = B.tipo == "hepatocito"
    a2, ic2 = alfa(par & ~hepb)
    a3, ic3 = alfa(par & (hs >= 20))
    con_socio = o["socio_idx_ok"].astype(bool)
    cota = float(np.median(B.umi[hepb & con_socio] / np.maximum(us[hepb & con_socio], 1)))
    res = {"alfa_cociente_sumas_socios_hepatocito": {"alfa": a, "ic95_boot_pares": ic, "n_pares": int(par.sum()),
                                                     "HEP5_hueso": int(hb[par].sum()), "HEP5_socio": int(hs[par].sum())},
           "sens_sin_hepatocitos_del_hueso": {"alfa": a2, "ic95_boot_pares": ic2, "n_pares": int((par & ~hepb).sum())},
           "sens_HEP5_socio_ge20": {"alfa": a3, "ic95_boot_pares": ic3, "n_pares": int((par & (hs >= 20)).sum())},
           "fuera_por_regla_socio_quitado_QC": {"n": int((ts == "socio_quitado_QC").sum()), "de_ellos_hepatocitos_del_hueso": int(((ts == "socio_quitado_QC") & hepb).sum())},
           "cota_0046_recalculada": cota}
    fr = {}
    for nombre, m in (("malignas_87", o["trib_maligna_T_todas"].astype(bool)), ("malignas_68_limpias", o["trib_maligna_T_limpio_T"].astype(bool)),
                      ("malignas_A_200", o["pob_usada_A"].astype(bool))):
        ob = B.umi[m].sum()
        fr[nombre] = {"n": int(m.sum()), "n_con_socio": int((m & con_socio).sum()), "UMI_obs": int(ob),
                      "frac_UMI_fugada_alfa": float(a * us[m].sum() / ob), "frac_UMI_fugada_alfa_ic95": [float(ic[0] * us[m].sum() / ob), float(ic[1] * us[m].sum() / ob)],
                      "frac_UMI_fugada_cota_0046": float(0.046 * us[m].sum() / ob), "frac_UMI_fugada_cota_recalculada": float(cota * us[m].sum() / ob)}
    res["fraccion_UMI"] = fr
    print("T2 alfa:", round(a, 4), [round(x, 4) for x in ic], "pares", int(par.sum()), "| sin hep hueso", round(a2, 4), "| socio>=20", round(a3, 4),
          int((par & (hs >= 20)).sum()), "| cota", round(cota, 4))
    print("T2 fracción UMI:", {k: (round(v["frac_UMI_fugada_alfa"], 4), round(v["frac_UMI_fugada_cota_0046"], 4)) for k, v in fr.items()})
    return res


# ---------------------------------------------------------------- T3 Wilson en las tablas del tribunal
def t3():
    p = os.path.join(_c.TRIB, "tablas_tribunal.csv")
    d = pd.read_csv(p)
    lo, hi = [], []
    for k, n in zip(d["k"], d["n"]):
        if pd.isna(k) or pd.isna(n):
            lo.append(np.nan)
            hi.append(np.nan)
            continue
        _, a, b = wilson(int(k), int(n))
        lo.append(a)
        hi.append(b)
    d["wilson_lo"] = lo
    d["wilson_hi"] = hi
    d["wilson_lo_menos_suelo"] = d["wilson_lo"] - d["suelo_ambiente"]
    d = d.round(6)
    d.to_csv(_c.outp("tablas_tribunal_wilson.csv"), index=False)
    z = d[(d["k"] == 0) | (d["k"] == d["n"])]
    print("T3: filas", len(d), "| con k=0 o k=n", len(z))
    return {"entrada_sha256": _c.sha256(p), "filas": int(len(d)), "filas_k0_o_kn": int(len(z)),
            "ejemplos_k0": z[z["k"] == 0][["muestra", "conjunto", "gen", "n", "wilson_hi"]].head(12).values.tolist()}


# ---------------------------------------------------------------- T4 lectura de ADN
def t4(L, B, conj):
    res = {}
    vg_l, vg_b = L.o["son_ven_genes"], B.o["son_ven_genes"]
    assert (vg_l == vg_b).all()
    ns = B.o["son_ven_nsondas"].astype(float)
    sym = B.o["son_ven_sym"]
    chrom = B.o["son_ven_chr"]
    n_or = int(B.o["son_n_or"])
    malA = B.o["pob_usada_A"].astype(bool)
    m87 = B.o["trib_maligna_T_todas"].astype(bool)
    m68 = B.o["trib_maligna_T_limpio_T"].astype(bool)
    nm = ~m87 & ~malA
    for nombre_sel, msel in (("silentes_en_primario_cierre", conj["primario"]), ("silentes_en_A_9385", conj["A_9385"])):
        tasa = L.o["son_ven_counts"][msel].sum(axis=0) / (msel.sum() * np.maximum(ns, 1))
        r = {}
        for wv, ch in (("8p11", "chr8"), ("{{LOCUS}}", "chr11")):
            sil = (tasa < 1e-3) & (chrom == ch) & (ns > 0)
            cw = B.o["son_ven_counts"][:, sil].sum(axis=1)
            nsil = int(ns[sil].sum())
            OR = B.o["son_or_sum"].astype(float)
            e_nm = (cw[nm].sum() / nsil) / (OR[nm].sum() / n_or)
            d = {"genes_silentes": sorted(sym[sil].tolist()), "n_sondas": nsil, "eficiencia_relativa_no_malignas": float(e_nm),
                 "no_malignas": {"n": int(nm.sum()), "UMI_silentes": int(cw[nm].sum()), "UMI_OR": int(OR[nm].sum())}}
            # POST HOC: ARN residual de los genes «silentes» (tasa del propio tumor en el hígado, donde el ADN es ~0,2 %),
            # escalado por la profundidad: lo que quede por encima es ADN.
            tasa_pool = L.o["son_ven_counts"][conj["primario"]][:, sil].sum() / max(conj["primario"].sum(), 1)
            umi_pool = L.umi[conj["primario"]].mean()
            d["ARN_residual_por_celula_en_pool_higado"] = float(tasa_pool)
            for nombre, m in (("malignas_68", m68), ("malignas_87", m87), ("malignas_A_200", malA)):
                S, Om = int(cw[m].sum()), float(OR[m].sum())
                Snm, Onm = int(cw[nm].sum()), float(OR[nm].sum())
                esp2 = Om / n_or * nsil * e_nm
                lo, hi = _c.garwood(S)
                cp_lo, cp_hi = _c.clopper(S, S + Snm)
                f = Onm / Om if Om > 0 else np.nan

                def rr(p):
                    return 2 * (p / (1 - p)) * f if p < 1 else float("inf")
                cop = 2 * S / esp2 if esp2 > 0 else None
                ic_g = [2 * lo / esp2, 2 * hi / esp2] if esp2 > 0 else None
                ic_b = [rr(cp_lo), rr(cp_hi)]
                informativo = bool(ic_b[0] > 2) if esp2 > 0 else False
                arn = float(tasa_pool * m.sum() * B.umi[m].mean() / umi_pool)
                exc = S - arn
                ic_c = [2 * max(lo - arn, 0) / esp2, 2 * max(hi - arn, 0) / esp2] if esp2 > 0 else None
                d[nombre + "_corregido_ARN"] = {"ARN_esperado": arn, "exceso_ADN": exc, "ic95_garwood_copias_corregido": ic_c,
                                                "informativo_excluye_2": bool(ic_c is not None and ic_c[0] > 2),
                                                "copias_puntual": (2 * exc / esp2) if (ic_c is not None and ic_c[0] > 2) else None}
                d[nombre] = {"n": int(m.sum()), "UMI_silentes": S, "UMI_OR": int(Om), "esperado_2copias": esp2,
                             "copias": cop if informativo else None, "copias_puntual_no_informativa": None if informativo else cop,
                             "ic95_garwood": ic_g, "ic95_binomial_condicional": ic_b, "informativo_excluye_2": informativo}
            r[wv] = d
        res[nombre_sel] = r
    # POST HOC (rama «no resoluble»): ADN del hígado a nivel poblacional en el pool, con selección de genes
    # silentes en una mitad aleatoria de las células y lectura en la otra (selección independiente de lo leído).
    POST_HOC.append("T4_HIGADO: lectura poblacional de copias por ADN en el pool del hígado (split-half: selección de silentes en "
                    "una mitad, lectura en la otra; eficiencia calibrada en las no malignas del hueso). Añadida tras el fallo del eje 1.")
    rng = np.random.default_rng(SEED)
    pool = conj["primario"]
    ip = np.where(pool)[0]
    perm = rng.permutation(len(ip))
    hA = np.zeros(L.n, bool)
    hA[ip[perm[: len(ip) // 2]]] = True
    hB = pool & ~hA
    hep = L.tipo == "hepatocito"
    tasaA = L.o["son_ven_counts"][hA].sum(axis=0) / (hA.sum() * np.maximum(ns, 1))
    ORl = L.o["son_or_sum"].astype(float)
    ORb = B.o["son_or_sum"].astype(float)
    rh = {}
    for wv, ch in (("8p11", "chr8"), ("{{LOCUS}}", "chr11")):
        sil = (tasaA < 1e-3) & (chrom == ch) & (ns > 0)
        nsil = int(ns[sil].sum())
        cwb = B.o["son_ven_counts"][:, sil].sum(axis=1)
        e_nm = (cwb[nm].sum() / max(nsil, 1)) / (ORb[nm].sum() / n_or)
        cwl = L.o["son_ven_counts"][:, sil].sum(axis=1)
        d = {"genes_silentes_mitad_A": sorted(sym[sil].tolist()), "n_sondas": nsil, "eficiencia_hueso_no_malignas": float(e_nm)}
        for nombre, m in (("pool_mitad_B", hB), ("hepatocitos_control", hep)):
            S = int(cwl[m].sum())
            esp2 = ORl[m].sum() / n_or * nsil * e_nm
            lo, hi = _c.garwood(S)
            ic = [2 * lo / esp2, 2 * hi / esp2] if esp2 > 0 else None
            d[nombre] = {"n": int(m.sum()), "UMI_silentes": S, "esperado_2copias": float(esp2), "ic95_garwood_copias": ic,
                         "con_potencia": bool(esp2 >= 3), "informativo_excluye_2": bool(ic is not None and ic[0] > 2),
                         "copias_puntual": (2 * S / esp2) if (ic is not None and ic[0] > 2) else None}
        rh[wv] = d
    res["higado_poblacional_split_half"] = rh
    POST_HOC.append("T4_HIGADO_NO_INTERPRETABLE: en el hígado el ADN es ~0,2 % de las UMI (delta ~6e-5 por sonda), por debajo del ARN "
                    "residual que dejan pasar los genes «silentes» (corte < 1e-3 por sonda y célula). El control de hepatocitos en {{LOCUS}} "
                    "da un exceso enorme (ARN de un gen silente en el tumor y expresado en hepatocitos), lo que demuestra la fuga de ARN. "
                    "La lectura de copias por ADN en el hígado no separa ADN de ARN: no se usa como prueba.")
    print("T4 hígado (split-half):", {wv: {k: (v["UMI_silentes"], round(v["esperado_2copias"], 2), [round(x, 1) for x in v["ic95_garwood_copias"]] if v["ic95_garwood_copias"] else None)
                                           for k, v in d.items() if isinstance(v, dict)} for wv, d in rh.items()})
    for s_, r in res.items():
        if not s_.startswith("silentes"):
            continue
        for wv, d in r.items():
            print("T4", s_, wv, "genes", d["genes_silentes"], "| ARN/célula pool", round(d["ARN_residual_por_celula_en_pool_higado"], 4))
            for k, v in d.items():
                if k.startswith("malignas") and not k.endswith("ARN"):
                    c = d[k + "_corregido_ARN"]
                    print("   ", k, "obs", v["UMI_silentes"], "esp2", round(v["esperado_2copias"], 2), "IC", [round(x, 1) for x in v["ic95_garwood"]],
                          "| ARN esp", round(c["ARN_esperado"], 2), "IC corr", [round(x, 1) for x in c["ic95_garwood_copias_corregido"]], c["informativo_excluye_2"])
    return res


# ---------------------------------------------------------------- T5/T6 tablas
def curva(X, mask, ns, umi):
    Xm = X[mask]
    det = np.asarray((Xm > 0).mean(axis=0)).ravel()
    cp = np.asarray(Xm.sum(axis=0)).ravel() / max(Xm.sum(), 1) * 1e4
    out_ = {}
    for k in (1, 2, 3, 4):
        m = ((ns == k) if k < 4 else (ns >= 4)) & (cp > 0)
        if m.sum() < 30:
            continue
        lc = np.log10(cp[m])
        dig = np.digitize(lc, np.linspace(-3, 3, 25))
        tab = pd.DataFrame({"b": dig, "d": det[m], "lc": lc}).groupby("b").agg(lc=("lc", "median"), d=("d", "mean"), n=("d", "size"))
        tab = tab[tab["n"] >= 5].sort_values("lc")
        cruce = None
        dd, ll = tab["d"].values, tab["lc"].values
        for i in range(1, len(tab)):
            if dd[i - 1] < 0.05 <= dd[i]:
                cruce = float(10 ** np.interp(0.05, [dd[i - 1], dd[i]], [ll[i - 1], ll[i]]))
                break
        out_[str(k) if k < 4 else ">=4"] = cruce
    return out_


def tablas(L, B, conj):
    filas = []
    meta = {}
    for T_ in (L, B):
        t = T_.t
        o = T_.o
        tipo = T_.tipo
        umi = T_.umi
        ns, nu = o["nsondas"], o["nsondas_unsp"]
        p_amb = o["p_amb"]
        if t == "higado":
            limpio = np.ones(T_.n, bool)
            delta = np.zeros(T_.n)
            sets = {k: v for k, v in conj.items()}
        else:
            limpio = o["trib_limpio_T"].astype(bool)
            delta = o["son_or_sum"] / float(o["son_n_or"])
            sets = {"primario_68": o["trib_maligna_T_limpio_T"].astype(bool), "sens_87": o["trib_maligna_T_todas"].astype(bool),
                    "sens_A_200_limpio": o["pob_usada_A"].astype(bool) & limpio}
        inm = np.isin(tipo, INMUNES) & limpio
        traz = np.isin(T_.sym, TRAZ[t])
        rho = float(T_.X[inm][:, traz].sum() / umi[inm].sum() / max(p_amb[traz].sum(), 1e-12))
        tnk = np.isin(tipo, ["T", "NK"]) & limpio
        if tnk.sum() < 30:
            tnk = inm
        trt = np.isin(T_.sym, TRAZ_TUMOR)
        rho_t = float(T_.X[tnk][:, trt].sum() / umi[tnk].sum() / max(p_amb[trt].sum(), 1e-12))
        rho_hi = max(rho, rho_t)
        refm = np.isin(tipo, TC.REF_TIPOS) & limpio
        estroma = (tipo == "fibroblasto_estelar") & limpio
        meta[t] = {"rho_no_tumoral": rho, "rho_tumoral": rho_t, "n_inmunes_rho": int(inm.sum()), "n_ref": int(refm.sum()),
                   "n_estroma": int(estroma.sum()), "n_conjuntos": {k: int(v.sum()) for k, v in sets.items()}}
        print(f"T5 {t}: rho {rho:.3f}, rho tumoral {rho_t:.3f}, conjuntos", meta[t]["n_conjuntos"])
        sets_ext = dict(sets)
        sets_ext["estroma_FAP"] = estroma
        for cname, mal in sets_ext.items():
            if cname.startswith("LOO_"):
                gen_loo = cname[4:]
                genes_tab = [("B" if gen_loo == "ANKRD30A" else "C", gen_loo), ("T6", gen_loo)] if gen_loo == "ANKRD30A" else [("C", gen_loo)]
            elif cname == "estroma_FAP":
                genes_tab = [("T6", "FAP")]
            else:
                genes_tab = [(tb, g) for tb, gs in TABLAS.items() for g in gs]
            if mal.sum() == 0:
                continue
            cur = curva(T_.X, mal, ns, umi[mal])
            wts = _c.TC_suelo().pesos_umi(umi[mal], umi[refm])
            for tab, g in genes_tab:
                j = np.where(T_.sym == g)[0]
                fila = {"muestra": t, "conjunto": cname, "tabla": tab, "gen": g}
                if len(j) == 0 or ns[j[0]] == 0:
                    fila["nota"] = "fuera del panel"
                    filas.append(fila)
                    continue
                j = j[0]
                v = np.asarray(T_.X[:, j].todense()).ravel()
                k, n = int((v[mal] > 0).sum()), int(mal.sum())
                f, lo, hi = wilson(k, n)
                lam = p_amb[j] * rho * umi[mal] + delta[mal] * nu[j]
                suelo = float(np.mean(1 - np.exp(-lam)))
                lam_hi = p_amb[j] * rho_hi * umi[mal] + delta[mal] * nu[j]
                suelo_hi = float(np.mean(1 - np.exp(-lam_hi)))
                dref = float(np.sum(wts * (v[refm] > 0)) / max(wts.sum(), 1e-12))
                fila.update({"n": n, "k": k, "frac": f, "wilson_lo": lo, "wilson_hi": hi,
                             "media_cp10k_pos": float(np.mean(v[mal][v[mal] > 0] / umi[mal][v[mal] > 0] * 1e4)) if k else None,
                             "n_sondas": int(ns[j]), "n_sondas_unspliced": int(nu[j]),
                             "suelo": suelo, "por_encima_suelo": bool(lo - suelo > 0), "wilson_lo_menos_suelo": lo - suelo,
                             "frac_menos_suelo": f - suelo,
                             "suelo_rho_tumoral": suelo_hi, "por_encima_suelo_rho_tumoral": bool(lo - suelo_hi > 0),
                             "ref_emparejada": dref, "frac_sobre_ref": (f / dref) if dref > 0 else None,
                             "supera_ref": bool(lo > dref), "k_menor_10": bool(k < 10)})
                if g in NO_RESOLUBLE:
                    fila["nota"] = "no resoluble a nivel de parálogo"
                if g == "FAP" and cname != "estroma_FAP":
                    fila["nota"] = (fila.get("nota", "") + " FAP es diana de estroma (CAF); no se lee como diana tumoral").strip()
                if k == 0:
                    cota = 3.0 / n / np.mean(umi[mal]) * 1e4
                    c5 = cur.get(str(int(ns[j])) if int(ns[j]) < 4 else ">=4")
                    fila.update({"cota_sup_cp10k_regla3": cota, "cp10k_5pct_curva": c5,
                                 "cero_informativo": bool(c5 is not None and cota < c5)})
                    fila["nota"] = (fila.get("nota", "") + " 0 detectadas: cota superior de Wilson, no ausencia").strip()
                filas.append(fila)
    df = pd.DataFrame(filas).round(6)
    df.to_csv(_c.outp("tablas_cierre.csv"), index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 500)
    for t, prim in (("higado", "primario"), ("hueso", "primario_68")):
        s = df[(df["muestra"] == t) & (df["conjunto"] == prim)][["tabla", "gen", "n", "k", "frac", "wilson_lo", "wilson_hi", "suelo", "por_encima_suelo",
                                                                   "suelo_rho_tumoral", "por_encima_suelo_rho_tumoral", "ref_emparejada", "supera_ref"]]
        print(f"== T5/T6 {t} {prim}\n" + s.round(3).to_string(index=False))
    s = df[df["conjunto"].str.startswith("LOO_") | (df["conjunto"] == "estroma_FAP")][["muestra", "conjunto", "gen", "n", "k", "frac", "wilson_lo", "suelo", "por_encima_suelo", "ref_emparejada"]]
    print("== LOO y estroma\n" + s.round(3).to_string(index=False))
    return meta, df


# ---------------------------------------------------------------- orquestación
def run():
    POST_HOC.clear()
    L, B = T("higado"), T("hueso")
    res = {"reglas": "REGLAS_CIERRE_v3.json (+ ENMIENDA_1)", "reglas_sha256": _c.sha256(_c.REGLAS), "semilla": SEED,
           "cache_sha256": {f: _c.sha256(cachep(f)) for f in sorted(os.listdir(_c.CACHE))}}
    res["T1"], conj = t1(L, B)
    res["T2"] = t2(B)
    res["T3"] = t3()
    res["T4"] = t4(L, B, conj)
    res["T5_T6_meta"], _ = tablas(L, B, conj)
    res["POST_HOC"] = sorted(set(POST_HOC))
    _c.guarda(res, "cierre.json")
    print("POST_HOC:", res["POST_HOC"])
    print("guardado cierre.json")


def manifiesto():
    d = _c.out_dir()
    sal = {f: _c.sha256(os.path.join(d, f)) for f in sorted(os.listdir(d))
           if os.path.isfile(os.path.join(d, f)) and not f.startswith("MANIFIESTO")}
    scr = {f: _c.sha256(os.path.join(_c.AQUI, f)) for f in sorted(os.listdir(_c.AQUI)) if f.endswith((".py", ".json"))}
    cache = {f: _c.sha256(cachep(f)) for f in sorted(os.listdir(_c.CACHE))}
    try:
        commit = subprocess.check_output(["git", "-C", _c.AQUI, "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        commit = "desconocido"
    _c.guarda({"salidas": sal, "scripts": scr, "caches": cache, "commit_al_generar": commit, "semilla": SEED,
               "entradas_tribunal": {f: _c.sha256(os.path.join(_c.TRIB, f)) for f in ("tablas_tribunal.csv", "higado_calls.npz", "hueso_calls.npz", "adn.json")}},
              "MANIFIESTO_CIERRE.json")
    for f, h in sal.items():
        print(f, h)
