"""P4 — Tablas A-C con suelo de ambiente y sensibilidad (REGLAS_TRIBUNAL_v2.json «suelo»).

Conjuntos: hígado = 8p11 p99 (calibrado por ADN, congelado) [+ sensibilidad: luminal_T, A, B];
hueso = malignas del tribunal SOLO con GEM limpio (primaria T) [+ sensibilidad: limpio_A, A limpio_T].
Por gen: fracción ≥1 UMI (IC95 bootstrap 1.000, semilla), media CP10k en positivas, suelo de ambiente
(modelo p_g·rho·UMI_i, + ADN en el hueso para sondas unspliced), fracción − suelo, referencia con
profundidad emparejada (comparador), nº de sondas, y para los 0: cota superior (regla del tres) en
CP10k frente al CP10k con 5 % de detección de la curva por nº de sondas.
Salidas: tribunal/suelo.json, tribunal/tablas_tribunal.csv.
"""
import gc
import json
import os

import numpy as np
import pandas as pd

from _comun import (A_DIR, B_DIR, HEP5, REF_TIPOS, barcodes_raw, boot, carga, guarda, llamadas, out, probe_set,
                    ruta, stream_h5)

TABLAS = {
    "A": ["B2M", "TAP1", "TAP2", "NLRC5", "PSMB8", "PSMB9", "IRF1", "STAT1"],
    "B": ["PRAME", "MAGEA4", "MAGEA1", "MAGEA6", "MAGEA10", "MAGEA11", "MAGEA12", "MAGEC1", "MAGEC2", "CTAG2",
          "ANKRD30A", "TPBG"],
    "C": ["TACSTD2", "ERBB2", "ERBB3", "CD276", "NECTIN4", "CEACAM5", "{{DIANA2}}", "SEZ6", "{{DIANA}}", "FOLR1", "SLC39A6",
          "VTCN1", "MSLN", "CLDN6", "MUC1", "GPNMB", "ROR1", "FGFR1"],
}
NO_RESOLUBLE = {"MAGEA6"}  # auditoría de parálogos de A y B coinciden
HK = ["ACTB", "B2M", "PGK1", "PPIA", "RPLP0", "TBP", "HPRT1", "GUSB", "YWHAZ", "SDHA", "TFRC", "UBC", "HMBS",
      "POLR2A", "PSMB2", "PSMB4", "RAB7A", "REEP5", "VCP", "VPS29", "EMC7", "CHMP2A", "GPI", "SNRPD3", "C1orf43",
      "EEF2", "RPL13A", "RPS18", "HSP90AB1", "PFN1", "CFL1", "TPT1", "EIF4A2", "GAPDH", "LDHA", "ALDOA", "TUBB",
      "PSMA1", "PSMC4", "SF3B1", "CTCF", "ATP5F1B", "NONO", "HNRNPA1", "SRSF3", "CALM2", "ARF1", "RHOA", "CDC42"]
TRAZ = {"higado": HEP5, "hueso": ["COL1A1", "COL1A2", "SPP1"]}
INMUNES = ["T", "NK", "B", "plasmatica", "mieloide", "neutrofilo", "mastocito"]
# POST HOC (tribunal, tras ver rho_HEP5): trazador tumoral en T/NK, genes que no expresan los linfocitos.
TRAZ_TUMOR = ["FGFR1", "ANKRD30A", "MUC1", "ESR1"]


def ambiente(t, ad):
    """Perfil de ambiente: barcodes raw NO llamados con 10 <= UMI <= 100."""
    bc, tt = barcodes_raw(t)
    llam = set(llamadas(t))
    sel = np.array([b for b, x in zip(bc, tt) if 10 <= x <= 100 and b not in llam])
    del bc, tt
    gc.collect()
    v, ids, _ = stream_h5(ruta(t, "_raw_feature_barcode_matrix.h5"), sel, modo="suma")
    s = pd.Series(v, index=ids).reindex(ad.var_names).fillna(0).values
    return s / s.sum(), len(sel), int(s.sum())


def pesos_umi(umi_obj, umi_ref):
    q = np.quantile(umi_obj, np.linspace(0, 1, 11))
    q[0], q[-1] = -np.inf, np.inf
    bo = np.digitize(umi_obj, q[1:-1])
    br = np.digitize(umi_ref, q[1:-1])
    po = np.bincount(bo, minlength=10) / len(bo)
    pr = np.bincount(br, minlength=10) / max(len(br), 1)
    w = np.where(pr[br] > 0, po[br] / np.maximum(pr[br], 1e-12), 0)
    return w


def curva(ad, mask, nsondas, umi):
    """Detección media frente a CP10k pseudobulk por clase de nº de sondas (1, 2, 3, >=4), y CP10k al
    que la detección cruza el 5 % (interpolado en log10)."""
    X = ad.X[mask]
    det = np.asarray((X > 0).mean(axis=0)).ravel()
    cp = np.asarray(X.sum(axis=0)).ravel() / X.sum() * 1e4
    out_ = {}
    for k in (1, 2, 3, 4):
        m = (nsondas == k) if k < 4 else (nsondas >= 4)
        m &= cp > 0
        if m.sum() < 30:
            continue
        lc = np.log10(cp[m])
        bins = np.linspace(-3, 3, 25)
        dig = np.digitize(lc, bins)
        tab = pd.DataFrame({"b": dig, "d": det[m], "lc": lc}).groupby("b").agg(lc=("lc", "median"), d=("d", "mean"), n=("d", "size"))
        tab = tab[tab["n"] >= 5].sort_values("lc")
        cruce = None
        dd = tab["d"].values
        ll = tab["lc"].values
        for i in range(1, len(tab)):
            if dd[i - 1] < 0.05 <= dd[i]:
                cruce = float(10 ** np.interp(0.05, [dd[i - 1], dd[i]], [ll[i - 1], ll[i]]))
                break
        out_[str(k) if k < 4 else ">=4"] = {"n_genes": int(m.sum()), "cp10k_al_5pct_deteccion": cruce,
                                            "curva_lc_det": [[round(float(a), 2), round(float(b), 4)] for a, b in zip(ll, dd)]}
    # cruce teórico Poisson con la profundidad real: 1 - mean(exp(-c·UMI/1e4)) = 0,05
    from scipy.optimize import brentq
    f = lambda c: 1 - np.mean(np.exp(-c * umi / 1e4)) - 0.05
    out_["poisson_teorico_cp10k_5pct"] = float(brentq(f, 1e-6, 100))
    return out_, det, cp


def ref_A_B():
    A = json.load(open(os.path.join(A_DIR, "tablas.json")))
    B = json.load(open(os.path.join(B_DIR, "04_medidas_EXPLORATORIO.json")))
    secB = {"A": "A_presentacion_antigenica", "B": "B_antigenos_CT_linaje", "C": "C_dianas_superficie"}
    fa, fb = {}, {}
    for t in ("higado", "hueso"):
        for tab, gs in TABLAS.items():
            for g in gs:
                x = A[t].get(f"{tab}:{g}")
                fa[(t, g)] = (x["frac"], x["n"], (x.get("sensib_GEM_limpio") or {}).get("frac")) if isinstance(x, dict) else None
                y = B["muestras"][t].get(secB[tab], {}).get(g)
                fb[(t, g)] = (y.get("frac"), y.get("n")) if isinstance(y, dict) else None
    return fa, fb


def run(*a):
    ps = probe_set()
    ps = ps[ps["included"]]
    nsond = ps.groupby("gene_id").size()
    nunsp = ps[ps.region == "unspliced"].groupby("gene_id").size()
    fa, fb = ref_A_B()
    res = {"reglas": "REGLAS_TRIBUNAL_v2.json/suelo"}
    filas = []
    zl = np.load(out("higado_calls.npz"))
    zb = np.load(out("hueso_calls.npz"), allow_pickle=True)
    zadn = np.load(out("adn_celulas.npz"))
    for t in ("higado", "hueso"):
        ad = carga(t)
        umi = np.asarray(ad.X.sum(axis=1)).ravel().astype(float)
        tipo = ad.obs["tipo"].values
        ref = np.isin(tipo, REF_TIPOS)
        inm = np.isin(tipo, INMUNES)
        if t == "higado":
            conjuntos = {"primario_8p11_p99": zl["8p11_p99"], "sens_luminal_T": zl["lum"],
                         "sens_A_9385": ad.obs["pob_usada"].values.astype(bool)}
            from _comun import obs_B
            ob = obs_B(cols=("maligna_B_explor",))
            ob = ob[ob["bc"].isin(ad.obs_names)].set_index("bc").loc[ad.obs_names]
            conjuntos["sens_B_1967"] = ob["maligna_B_explor"].values.astype(bool)
            limpio = np.ones(ad.n_obs, bool)
            delta = np.zeros(ad.n_obs)
        else:
            lim = zb["limpio_T"].astype(bool)
            conjuntos = {"primario_T_limpio": zb["maligna_T_limpio_T"].astype(bool),
                         "sens_T_limpio_A": zb["maligna_T_limpio_A"].astype(bool),
                         "sens_A_200_limpio_T": ad.obs["pob_usada"].values.astype(bool) & lim,
                         "sens_T_todas": zb["maligna_T_todas"].astype(bool)}
            limpio = lim
            delta = zadn["hueso_delta"]
        p_amb, n_vac, umi_vac = ambiente(t, ad)
        traz = np.isin(ad.var["simbolo"].values, TRAZ[t])
        mi = inm & limpio
        rho = float(ad.X[mi][:, traz].sum() / umi[mi].sum() / max(p_amb[traz].sum(), 1e-12))
        tnk = np.isin(tipo, ["T", "NK"]) & limpio
        if tnk.sum() < 30:  # el hueso no tiene T/NK anotadas: todas las inmunes de GEM limpio
            tnk = inm & limpio
        trt = np.isin(ad.var["simbolo"].values, TRAZ_TUMOR)
        rho_t = float(ad.X[tnk][:, trt].sum() / umi[tnk].sum() / max(p_amb[trt].sum(), 1e-12))
        rho_hi = max(rho, rho_t)
        res[t] = {"rho_trazador_tumoral_TNK": rho_t, "n_TNK": int(tnk.sum()), "rho_alto_usado": rho_hi,
                  "ambiente": {"n_barcodes_vacios": n_vac, "UMI_vacios": umi_vac,
                               "p_FGFR1_ambiente": float(p_amb[ad.var["simbolo"].values == "FGFR1"].sum()),
                               "p_trazador_ambiente": float(p_amb[traz].sum())},
                  "rho": rho, "n_inmunes_rho": int(mi.sum()),
                  "n_conjuntos": {k: int(v.sum()) for k, v in conjuntos.items()}}
        print(f"== {t}: vacíos {n_vac}, rho {rho:.3f}, rho_tumoral_TNK {rho_t:.3f} (n {tnk.sum()}), p_FGFR1_amb {res[t]['ambiente']['p_FGFR1_ambiente']:.4f}, conjuntos",
              res[t]["n_conjuntos"])
        ns = nsond.reindex(ad.var_names).fillna(0).astype(int).values
        nu = nunsp.reindex(ad.var_names).fillna(0).astype(int).values
        for cname, mal in conjuntos.items():
            if mal.sum() == 0:
                continue
            cur, det_all, cp_all = curva(ad, mal, ns, umi[mal])
            res[t][f"curva_{cname}"] = cur
            refm = ref & limpio
            w = pesos_umi(umi[mal], umi[refm])
            # housekeeping por nº de sondas
            hk = {}
            for g in HK:
                j = np.where(ad.var["simbolo"].values == g)[0]
                if len(j):
                    hk[g] = {"n_sondas": int(ns[j[0]]), "det": float(det_all[j[0]]), "cp10k": float(cp_all[j[0]])}
            res[t][f"housekeeping_{cname}"] = hk
            for tab, gs in TABLAS.items():
                for g in gs:
                    j = np.where(ad.var["simbolo"].values == g)[0]
                    fila = {"muestra": t, "conjunto": cname, "tabla": tab, "gen": g}
                    if len(j) == 0 or ns[j[0]] == 0:
                        fila["nota"] = "fuera del panel"
                        filas.append(fila)
                        continue
                    j = j[0]
                    v = np.asarray(ad.X[:, j].todense()).ravel()
                    k, n = int((v[mal] > 0).sum()), int(mal.sum())
                    f, lo, hi = boot(k, n)
                    lam = p_amb[j] * rho * umi[mal] + delta[mal] * nu[j]
                    suelo = float(np.mean(1 - np.exp(-lam)))
                    lam_hi = p_amb[j] * rho_hi * umi[mal] + delta[mal] * nu[j]
                    suelo_hi = float(np.mean(1 - np.exp(-lam_hi)))
                    dref = float(np.sum(w * (v[refm] > 0)) / max(w.sum(), 1e-12))
                    cpos = float(np.mean(v[mal][v[mal] > 0] / umi[mal][v[mal] > 0] * 1e4)) if k else None
                    fila["UMI_obs"] = int(v[mal].sum())
                    fila["frac_UMI_atribuible_ambiente_ADN"] = float(min(1.0, lam.sum() / max(v[mal].sum(), 1)))
                    fila.update({"n": n, "k": k, "frac": f, "ic95": [lo, hi], "media_cp10k_pos": cpos,
                                 "n_sondas": int(ns[j]), "n_sondas_unspliced": int(nu[j]),
                                 "suelo_ambiente": suelo, "frac_menos_suelo": f - suelo,
                                 "suelo_alto_rho_tumoral": suelo_hi, "frac_menos_suelo_alto": f - suelo_hi,
                                 "frac_UMI_atribuible_ambiente_alto": float(min(1.0, lam_hi.sum() / max(v[mal].sum(), 1))),
                                 "ic95_menos_suelo": [lo - suelo, hi - suelo],
                                 "ref_emparejada_UMI": dref, "cociente_vs_ref": (f / dref) if dref > 0 else None})
                    if g in NO_RESOLUBLE:
                        fila["nota"] = "no resoluble a nivel de parálogo"
                    if g in ("FGFR1",) and t == "higado" and cname == "primario_8p11_p99":
                        fila["nota"] = "por construcción (el conjunto se selecciona por la ventana 8p11)"
                    if k == 0:
                        cota = 3.0 / n / np.mean(umi[mal]) * 1e4
                        clave = str(int(ns[j])) if int(ns[j]) < 4 else ">=4"
                        ent = cur.get(clave)
                        c5 = ent.get("cp10k_al_5pct_deteccion") if isinstance(ent, dict) else None
                        fila.update({"cota_sup_cp10k_regla3": cota, "cp10k_5pct_curva": c5,
                                     "cp10k_5pct_poisson": cur["poisson_teorico_cp10k_5pct"],
                                     "cero_informativo": bool(c5 is not None and cota < c5),
                                     "detectado_en_otras_celulas_de_la_muestra": int((v[~mal] > 0).sum()),
                                     "UMI_otras_celulas": int(v[~mal].sum()),
                                     "UMI_esperadas_ADN_otras_celulas": float(delta[~mal].sum() * nu[j]),
                                     "frac_HK_mismo_n_sondas_sobre_cota": float(np.mean([h["cp10k"] > cota for h in hk.values()
                                                                                          if min(h["n_sondas"], 4) == min(int(ns[j]), 4)] or [np.nan]))})
                        fila["nota"] = (fila.get("nota", "") + " 0 detectadas, no implica ausencia").strip()
                    if cname.startswith("primario"):
                        fa_ = fa.get((t, g))
                        fb_ = fb.get((t, g))
                        fila["A_frac"] = fa_[0] if fa_ else None
                        fila["A_n"] = fa_[1] if fa_ else None
                        fila["A_frac_GEM_limpio"] = fa_[2] if fa_ else None
                        fila["B_frac"] = fb_[0] if fb_ else None
                        fila["B_n"] = fb_[1] if fb_ else None
                    filas.append(fila)
        # LOO de FGFR1 en el hígado
        if t == "higado":
            j = np.where(ad.var["simbolo"].values == "FGFR1")[0][0]
            v = np.asarray(ad.X[:, j].todense()).ravel()
            for gex in ("FGFR1", "ZNF703", "NSD3"):
                m = zl[f"8p11_p99_LOO_{gex}"]
                if gex == "FGFR1":
                    k, n = int((v[m] > 0).sum()), int(m.sum())
                    res[t]["FGFR1_en_conjunto_LOO_sin_FGFR1"] = {"k": k, "n": n, "frac_ic": boot(k, n),
                                                                  "UMI_por_celula": float(v[m].mean())}
                    print("FGFR1 en conjunto LOO sin FGFR1:", res[t]["FGFR1_en_conjunto_LOO_sin_FGFR1"])
        del ad
        gc.collect()
    df = pd.DataFrame(filas)
    df.to_csv(out("tablas_tribunal.csv"), index=False)
    res["filas"] = filas
    guarda(res, "suelo.json")
    pd.set_option("display.width", 250)
    pr = df[df["conjunto"].str.startswith("primario")][["muestra", "tabla", "gen", "n", "k", "frac", "suelo_ambiente",
                                                         "frac_menos_suelo", "suelo_alto_rho_tumoral", "frac_menos_suelo_alto", "ref_emparejada_UMI", "A_frac", "B_frac", "nota"]]
    print(pr.round(3).to_string(index=False))
