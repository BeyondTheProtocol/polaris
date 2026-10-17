"""T9 — módulo NO tumoral, EXPLORATORIO (pregunta añadida el 16-oct; POST HOC respecto a REGLAS_CIERRE_v3).
Solo presencia/fracción (k de n), sin scores compuestos ni p-valores. Frenos de verificacion, fijados ANTES de
mirar cifras y aplicados en el código:
 · k < 10 o n < 100 → «visto en k de n», no hallazgo;
 · hueso: solo GEM limpio (limpio_T, n 1.588); gen sin sondas spliced con detección < 5 % → «posible ADN», no se lee;
 · hígado: en no tumorales no se leen genes que el tumor expresa alto (FGFR1, CCND1, ESR1, KRT*, TFF*);
 · 1 sonda → solo detectado sí/no; 0 sondas → «sin sonda»;
 · la tabla de sondas (incluidas, spliced, unspliced) se imprime ANTES que cualquier cifra.
Tipos celulares: anotación de A (cachés del cierre, células del QC). Salida: cierre/t9_no_tumoral.json.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import _c  # noqa: E402
from _c import TC  # noqa: E402
from _final import T  # noqa: E402

L1 = ["ACP5", "CTSK", "MMP9", "TNFRSF11A", "S100A8", "S100A9", "FCN1", "FOXP3", "HAVCR2", "LAG3", "CXCL13"]
L2 = ["TCF7", "CXCL13", "PDCD1", "GZMK", "NCAM1", "KLRD1", "GNLY", "CD3E"]
L3M = ["TREM2", "SPP1", "APOE", "C1QA", "C1QB", "CSF1R", "CXCL9", "CXCL10"]
L3C = ["FAP", "MMP11", "POSTN", "CA9"]
L4 = ["HBB", "HBA1", "HBA2", "GYPA", "ALAS2", "CD34", "SPINK2", "PF4", "PPBP", "ITGA2B"]
NO_LEER_HIGADO = ("FGFR1", "CCND1", "ESR1")


def no_leer_higado(g):
    return g in NO_LEER_HIGADO or g.startswith("KRT") or g.startswith("TFF")


def sondas_tabla(genes):
    ps = TC.probe_set()
    out = {}
    for g in genes:
        s = ps[ps["gene_name"] == g]
        inc = s[s["included"]]
        out[g] = {"incluidas": int(len(inc)), "spliced": int((inc.region == "spliced").sum()), "unspliced": int((inc.region == "unspliced").sum())}
    return out


def lectura(T_, m, g, sond, tejido):
    n = int(m.sum())
    sd = sond[g]
    if sd["incluidas"] == 0:
        return {"lectura": "sin sonda", "n": n}
    if tejido == "higado" and no_leer_higado(g):
        return {"lectura": "no se lee (gen alto en el tumor: ambiente)", "n": n}
    v = T_.g(g)
    k = int((v[m] > 0).sum())
    if sd["incluidas"] == 1:
        r = {"lectura": "detectado" if k > 0 else "no detectado", "n": n, "k": k, "una_sonda": True}
    else:
        r = {"lectura": None, "n": n, "k": k, "frac": k / n if n else None, "ic95_wilson": list(_c.wilson(k, n)[1:]) if n else None}
    if tejido == "hueso" and sd["spliced"] == 0 and n and k / n < 0.05:
        r["lectura"] = "posible ADN (solo sondas unspliced y < 5 %)"
    elif k < 10 or n < 100:
        r["lectura"] = r["lectura"] or f"visto en {k} de {n}"
        if sd["incluidas"] > 1:
            r["lectura"] = f"visto en {k} de {n}"
    elif r["lectura"] is None:
        r["lectura"] = f"{k} de {n} ({100 * k / n:.1f} %)"
    return r


def run():
    genes = sorted(set(L1 + L2 + L3M + L3C + L4))
    sond = sondas_tabla(genes)
    print("SONDAS (incluidas/spliced/unspliced):", {g: (v["incluidas"], v["spliced"], v["unspliced"]) for g, v in sond.items()})
    res = {"aviso": "EXPLORATORIO, POST HOC; solo presencia k de n con los frenos del docstring", "sondas": sond}
    L, B = T("higado"), T("hueso")
    lim = B.o["trib_limpio_T"].astype(bool)
    res["tipos_A"] = {"higado": pd.Series(L.tipo).value_counts().to_dict(),
                      "hueso_GEM_limpio": pd.Series(B.tipo[lim]).value_counts().to_dict()}
    print("tipos hígado:", res["tipos_A"]["higado"])
    print("tipos hueso limpio:", res["tipos_A"]["hueso_GEM_limpio"])

    def bloque(T_, grupos, gl, tejido):
        return {gn: {g: lectura(T_, m, g, sond, tejido) for g in gl} for gn, m in grupos.items()}
    tb = B.tipo
    # (1) arquetipo inmune del hueso
    g1 = {t: (tb == t) & lim for t in ("mieloide", "osteoclasto", "neutrofilo", "plasmatica", "B", "T", "NK") if ((tb == t) & lim).any()}
    g1["inmunes_todas"] = np.isin(tb, TC.REF_TIPOS + ["osteoclasto"]) & lim & (tb != "endotelio")
    res["1_hueso_arquetipo_inmune"] = bloque(B, g1, L1, "hueso")
    # (2) T/NK
    tnk_l = np.isin(L.tipo, ["T", "NK"])
    tnk_b = np.isin(tb, ["T", "NK"]) & lim
    res["2_TNK"] = {"higado": bloque(L, {"T_NK": tnk_l}, L2, "higado"),
                    "hueso": bloque(B, {"T_NK": tnk_b}, L2, "hueso") if tnk_b.any() else "sin células T/NK anotadas en GEM limpio",
                    "UMI_por_celula": {"higado_T_NK_mediana": float(np.median(L.umi[tnk_l])) if tnk_l.any() else None,
                                       "higado_resto_mediana": float(np.median(L.umi[~tnk_l])),
                                       "hueso_T_NK_mediana": float(np.median(B.umi[tnk_b])) if tnk_b.any() else None,
                                       "hueso_resto_limpio_mediana": float(np.median(B.umi[lim & ~tnk_b]))}}
    # (3) hígado: macrófagos y CAF
    res["3_higado"] = {"macrofagos_mieloide": bloque(L, {"mieloide": L.tipo == "mieloide"}, L3M, "higado"),
                       "CAF_fibroblasto_estelar": bloque(L, {"fibroblasto_estelar": L.tipo == "fibroblasto_estelar"}, L3C, "higado")}
    # (4) hueso: eritroides, HSPC, megacariocitos (descriptivo)
    def pos(gs):
        return np.any(np.stack([(B.g(g) > 0) for g in gs if B.g(g) is not None]), axis=0)
    r4 = {"tipo_A_eritroide_limpio": int(((tb == "eritroide") & lim).sum()),
          "tipo_A_megacariocito_limpio": int(((tb == "megacariocito") & lim).sum()),
          "por_gen_en_GEM_limpio": {g: lectura(B, lim, g, sond, "hueso") for g in L4}}
    for nombre, gs in (("HSPC_CD34_y_SPINK2", ["CD34", "SPINK2"]), ("mega_PF4_y_PPBP", ["PF4", "PPBP"])):
        a, b = (B.g(gs[0]) > 0), (B.g(gs[1]) > 0)
        k = int((a & b & lim).sum())
        r4[nombre] = {"ambos": k, "n": int(lim.sum()), "lectura": f"visto en {k} de {int(lim.sum())}"}
    res["4_hueso_medula"] = r4
    # CD3E: fuente de 0,4 % y 1,3 % (B, 04_medidas: ≥1 UMI en toda la muestra post-QC) recontado aquí
    cd = {}
    for T_, nombre, m in ((L, "higado_QC", np.ones(L.n, bool)), (B, "hueso_QC", np.ones(B.n, bool)), (B, "hueso_GEM_limpio", lim)):
        v = T_.g("CD3E")
        cd[nombre] = {"k": int((v[m] > 0).sum()), "n": int(m.sum()), "frac": float((v[m] > 0).mean())}
    cd["fuente"] = "analisis_B/04_medidas(_EXPLORATORIO).json, «celulas_CD3E>=1_toda_la_muestra» (hígado n 69, hueso n 26)"
    res["CD3E"] = cd
    print("CD3E recontado:", cd)
    def hojas(x, pre=""):
        if isinstance(x, dict) and "lectura" in x:
            print(f"   {pre}: {x['lectura']}")
        elif isinstance(x, dict):
            for k, v in x.items():
                hojas(v, f"{pre}/{k}" if pre else k)
        else:
            print(f"   {pre}: {x}")
    for sec in ("1_hueso_arquetipo_inmune", "2_TNK", "3_higado"):
        print("==", sec)
        hojas(res[sec])
    print("== 4", {k: v for k, v in r4.items() if k != "por_gen_en_GEM_limpio"}, {g: r["lectura"] for g, r in r4["por_gen_en_GEM_limpio"].items()})
    _c.guarda(res, "t9_no_tumoral.json")
    print("guardado t9_no_tumoral.json")


if __name__ == "__main__":
    run()
