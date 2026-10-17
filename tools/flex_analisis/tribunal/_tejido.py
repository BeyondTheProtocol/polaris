"""P6 — Control de tejido. v1 literal (ESPEC_CONGELADA_v1, paso 2) y v2 EXPLORATORIA (hueso solo GEM limpio).
Pseudobulk CP10k de HEP5 y del programa óseo; células etiquetadas hepatocito (A); esperado por ambiente.
Salida: tribunal/tejido.json.
"""
import json
import os

import numpy as np

from _comun import A_DIR, B_DIR, HEP5, barcodes_raw, carga, guarda, llamadas, out, ruta, stream_h5, wilson

OSEO = ["COL1A1", "SPP1", "ACP5", "CTSK"]


def pb(ad, m, genes):
    X = ad.X[m]
    tot = X.sum()
    return {g: float(X[:, np.where(ad.var["simbolo"].values == g)[0][0]].sum() / tot * 1e4) for g in genes}


def run(*a):
    zb = np.load(out("hueso_calls.npz"), allow_pickle=True)
    suelo = json.load(open(out("suelo.json")))
    res = {"v1_texto_literal": "Control de tejido: pseudobulk de programa hepatocitario (ALB, APOA1, SERPINA1, HP, TF) y óseo/osteoclasto (COL1A1, SPP1, ACP5, CTSK). Si el hígado no muestra hepatocitos o el hueso sí, STOP.",
           "v1_umbral_numerico": "ninguno en el texto",
           "A_regla_y_veredicto": json.load(open(os.path.join(A_DIR, "celulas.json")))["control_tejido"],
           "B_regla_y_veredicto": json.load(open(os.path.join(B_DIR, "01_qc.json")))["control_tejido_decision"]}
    for t in ("higado", "hueso"):
        ad = carga(t)
        todas = np.ones(ad.n_obs, bool)
        hep = (ad.obs["tipo"] == "hepatocito").values
        r = {"todas": {"n": int(ad.n_obs), "HEP5_cp10k": pb(ad, todas, HEP5), "oseo_cp10k": pb(ad, todas, OSEO),
                       "hepatocitos_A": int(hep.sum())}}
        if t == "hueso":
            lim = zb["limpio_T"].astype(bool)
            lA = zb["limpio_A"].astype(bool)
            r["limpio_T"] = {"n": int(lim.sum()), "HEP5_cp10k": pb(ad, lim, HEP5), "oseo_cp10k": pb(ad, lim, OSEO),
                             "hepatocitos_A": int((hep & lim).sum()), "frac_hepatocitos": wilson(int((hep & lim).sum()), int(lim.sum()))}
            r["limpio_A"] = {"n": int(lA.sum()), "HEP5_cp10k": pb(ad, lA, HEP5), "hepatocitos_A": int((hep & lA).sum())}
            r["con_socio"] = {"n": int((~lim).sum()), "HEP5_cp10k": pb(ad, ~lim, HEP5), "hepatocitos_A": int((hep & ~lim).sum())}
            # esperado por ambiente del propio hueso (vacíos 10-100 UMI) × rho del hueso
            bc, tt = barcodes_raw(t)
            ll = set(llamadas(t))
            sel = np.array([b for b, x in zip(bc, tt) if 10 <= x <= 100 and b not in ll])
            v, ids, _ = stream_h5(ruta(t, "_raw_feature_barcode_matrix.h5"), sel, modo="suma")
            import pandas as pd
            s = pd.Series(v, index=ids).reindex(ad.var_names).fillna(0).values
            p = s / s.sum()
            rho = suelo["hueso"]["rho"]
            r["esperado_ambiente_HEP5_cp10k"] = {g: float(p[ad.var["simbolo"].values == g].sum() * rho * 1e4) for g in HEP5}
        res[t] = r
        print(t, json.dumps({k: v for k, v in r.items()}, ensure_ascii=False, default=str)[:900])
    h = res["higado"]["todas"]["HEP5_cp10k"]
    bl = res["hueso"]["limpio_T"]["HEP5_cp10k"]
    res["v2_razon_HEP5_hueso_limpio_sobre_higado"] = sum(bl.values()) / sum(h.values())
    res["v2_razon_HEP5_hueso_limpio_sobre_ambiente"] = sum(bl.values()) / max(sum(res["hueso"]["esperado_ambiente_HEP5_cp10k"].values()), 1e-9)
    guarda(res, "tejido.json")
    print("v2 razones:", res["v2_razon_HEP5_hueso_limpio_sobre_higado"], res["v2_razon_HEP5_hueso_limpio_sobre_ambiente"])
