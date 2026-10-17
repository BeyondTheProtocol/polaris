"""P3 — Malignas del hígado calibradas con el ADN (espec. v2 EXPLORATORIA, REGLAS «calibra_higado»).

Score por célula de las ventanas 8p11 y {{LOCUS}} frente a la referencia (inmunes + endotelio de A), +
identidad luminal (>=2 genes de LUMINAL, sin KRT8/18; no referencia ni hepatocito). Corte p99 de la
referencia (sensibilidad p95). Conjuntos: 8p11 solo (para las tablas), 8p11 Y {{LOCUS}}, 8p11 O {{LOCUS}}.
Conjuntos LOO (ventana 8p11 sin FGFR1 / sin ZNF703 / sin NSD3) para la tabla C.
Comparación con A (pob_usada, 9.385) y B (maligna_B_explor, 1.967).
Salidas: tribunal/calibra.json, tribunal/higado_calls.npz (máscaras en el orden de obs_A del hígado).
"""
import numpy as np

from _comun import (HEP5, LUMINAL, REF_TIPOS, carga, coords, genes_ventana, guarda, n_detectados, obs_B, out,
                    score_ventana, wilson)


def _w(k, n):
    p, lo, hi = wilson(k, n)
    return {"k": int(k), "n": int(n), "frac": p, "ic95": [lo, hi]}


def run(*a):
    ad = carga("higado")
    ob = obs_B(cols=("maligna_B_explor", "maligna_B"))
    ob = ob[ob["bc"].isin(ad.obs_names)].set_index("bc").loc[ad.obs_names]
    ref = ad.obs["tipo"].isin(REF_TIPOS).values
    hep = (ad.obs["tipo"] == "hepatocito").values
    nlum = n_detectados(ad, LUMINAL)
    lum = (nlum >= 2) & ~ref & ~hep
    malA = ad.obs["pob_usada"].values.astype(bool)
    epiA = ad.obs["pob_epitelial"].values.astype(bool)
    malB = ob["maligna_B_explor"].values.astype(bool)
    cv = coords()
    res = {"reglas": "REGLAS_TRIBUNAL_v2.json/calibra_higado", "n": int(ad.n_obs), "n_ref": int(ref.sum()),
           "n_luminal_T": int(lum.sum()), "n_epitelial_A_pob_epitelial": int(epiA.sum())}
    sc = {}
    for w in ("8p11", "{{LOCUS}}"):
        g = genes_ventana(cv, w)
        s, obs, esp = score_ventana(ad, g, ref)
        sc[w] = s
        res[f"ventana_{w}"] = {"n_genes": len(g), "simbolos": sorted(cv.loc[g, "symbol"].astype(str)),
                               "p95_ref": float(np.percentile(s[ref], 95)), "p99_ref": float(np.percentile(s[ref], 99)),
                               "mediana_score": {"ref": float(np.median(s[ref])), "luminal": float(np.median(s[lum])),
                                                 "hepatocito": float(np.median(s[hep])), "malignas_A": float(np.median(s[malA])),
                                                 "malignas_B": float(np.median(s[malB]))},
                               "UMI_ventana_por_celula": {"ref": float(obs[ref].mean()), "luminal": float(obs[lum].mean()),
                                                          "hepatocito": float(obs[hep].mean())}}
    masks = {}
    for q in (99, 95):
        c8 = sc["8p11"] > np.percentile(sc["8p11"][ref], q)
        c11 = sc["{{LOCUS}}"] > np.percentile(sc["{{LOCUS}}"][ref], q)
        for nombre, m in (("8p11", c8), ("8p11_Y_11q13", c8 & c11), ("8p11_O_11q13", c8 | c11)):
            S = m & lum
            masks[f"{nombre}_p{q}"] = S
            res[f"conjunto_{nombre}_p{q}"] = {
                "n": int(S.sum()),
                "frac_de_luminales_T": _w(int(S.sum()), int(lum.sum())),
                "frac_de_pob_epitelial_A": _w(int((S & epiA).sum()), int(epiA.sum())),
                "solape_A": {"A": int(malA.sum()), "ambos": int((S & malA).sum()), "jaccard": float((S & malA).sum() / max((S | malA).sum(), 1)),
                             "frac_de_A_recuperada": float((S & malA).sum() / malA.sum())},
                "solape_B": {"B": int(malB.sum()), "ambos": int((S & malB).sum()), "jaccard": float((S & malB).sum() / max((S | malB).sum(), 1)),
                             "frac_de_B_recuperada": float((S & malB).sum() / malB.sum())},
                "ref_falsos_positivos_ventana": _w(int((m & ref).sum()), int(ref.sum())),
                "hepatocitos_con_ventana_alta": _w(int((m & hep).sum()), int(hep.sum()))}
    # POST HOC declarado (no estaba en REGLAS): la referencia está contaminada (cola alta), así que p99
    # no es un corte de 1 % FP sino un conjunto «casi seguro». Estimación poblacional de la fracción de
    # luminales con exceso de ventana: pi0 = P(score_lum < mediana_null) / 0,5 (estilo Storey), con
    # dos nulos (referencia, hepatocitos). IC por bootstrap de células (1.000, semilla).
    rng = np.random.default_rng(20261016)
    umi = np.asarray(ad.X.sum(axis=1)).ravel()
    res["umi_mediana"] = {"luminal": float(np.median(umi[lum])), "conjunto_8p11_p99": float(np.median(umi[masks["8p11_p99"]])),
                          "malignas_A": float(np.median(umi[malA])), "malignas_B": float(np.median(umi[malB]))}
    pi = {}
    for w in ("8p11", "{{LOCUS}}"):
        s = sc[w]
        for nnull, mn in (("referencia", ref), ("hepatocitos", hep)):
            med = np.median(s[mn])
            sl = s[lum]
            est = 1 - min(1.0, (sl < med).mean() / 0.5)
            bs = [1 - min(1.0, (rng.choice(sl, len(sl)) < np.median(rng.choice(s[mn], mn.sum()))).mean() / 0.5) for _ in range(1000)]
            pi[f"{w}_null_{nnull}"] = {"frac_luminal_con_exceso": float(est), "ic95": [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))],
                                       "n_luminal_estimado": float(est * lum.sum())}
    res["POSTHOC_fraccion_poblacional_con_exceso"] = pi
    print("POSTHOC pi:", {k: (round(v["frac_luminal_con_exceso"], 3), [round(x, 3) for x in v["ic95"]]) for k, v in pi.items()})
    print("UMI medianas:", res["umi_mediana"])
    # LOO para la tabla C (8p11 p99 sin el gen informado)
    for gex in ("FGFR1", "ZNF703", "NSD3"):
        g = genes_ventana(cv, "8p11", excluir=(gex,))
        s, _, _ = score_ventana(ad, g, ref)
        masks[f"8p11_p99_LOO_{gex}"] = (s > np.percentile(s[ref], 99)) & lum
        res[f"LOO_{gex}"] = {"n": int(masks[f"8p11_p99_LOO_{gex}"].sum()),
                             "solape_con_8p11_p99": int((masks[f"8p11_p99_LOO_{gex}"] & masks["8p11_p99"]).sum())}
    # dónde caen A y B: score 8p11
    res["A_fuera_de_8p11_p99_por_identidad"] = {"A_luminal_T": int((malA & lum).sum()), "A_no_luminal_T": int((malA & ~lum).sum())}
    hep5_l = n_detectados(ad, HEP5)
    res["control_hepato_en_conjunto"] = {"conjunto_8p11_p99_con_>=2_HEP5": int((masks["8p11_p99"] & (hep5_l >= 2)).sum())}
    np.savez_compressed(out("higado_calls.npz"), lum=lum, ref=ref, hep=hep, score8=sc["8p11"], score11=sc["{{LOCUS}}"], **masks)
    guarda(res, "calibra.json")
    for k, v in res.items():
        if k.startswith("conjunto_") and k.endswith("p99"):
            print(k, "n", v["n"], "frac lum", round(v["frac_de_luminales_T"]["frac"], 3), "A ambos", v["solape_A"]["ambos"],
                  "B ambos", v["solape_B"]["ambos"], "ref FP", v["ref_falsos_positivos_ventana"]["k"], "hep", v["hepatocitos_con_ventana_alta"]["k"])
    print("n luminal_T", res["n_luminal_T"], "| pob_epitelial A", res["n_epitelial_A_pob_epitelial"])
    print("8p11:", res["ventana_8p11"]["mediana_score"], res["ventana_8p11"]["UMI_ventana_por_celula"])
    print("{{LOCUS}}:", res["ventana_11q13"]["mediana_score"])
    print("p95 conjuntos:", {k: v["n"] for k, v in res.items() if k.startswith("conjunto_") and k.endswith("p95")})
    print("LOO:", {k: v for k, v in res.items() if k.startswith("LOO")}, res["A_fuera_de_8p11_p99_por_identidad"], res["control_hepato_en_conjunto"])
