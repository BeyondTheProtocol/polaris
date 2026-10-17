"""EXPLORATORIO: los mismos genes en células de referencia (inmune+endotelio) y estroma de la misma
muestra, como control de ARN ambiental/fuga para leer las fracciones malignas. Solo agregados."""
import json

import numpy as np
import scanpy as sc

from _comun import TEJIDOS, guarda_json, ids_de, out
from _malignas import REF_TIPOS

from _tablas import A, B, C, NE

GENES = [g for g in A + B + C + NE if not g.startswith("HLA")] + ["ZNF703", "CCND1", "ESR1", "GATA3", "ACTB", "PTPRC"]


def run(*a):
    res = {}
    for t in TEJIDOS:
        ad = sc.read_h5ad(out(f"tab_{t}.h5ad"))
        grupos = {"malignas_usadas": ad.obs["pob_usada"].values.astype(bool),
                  "referencia": ad.obs["tipo"].isin(REF_TIPOS).values,
                  "estroma": ad.obs["tipo"].isin(["fibroblasto_estelar"]).values,
                  "mieloide": ad.obs["tipo"].isin(["mieloide", "neutrofilo"]).values,
                  "linfoide_plasma": ad.obs["tipo"].isin(["T", "NK", "B", "plasmatica"]).values,
                  "hepatocito": (ad.obs["tipo"] == "hepatocito").values}
        tot = ad.obs["total_counts"].values
        r = {}
        for s, gid in ids_de(ad, GENES).items():
            if gid is None:
                r[s] = "fuera del panel"
                continue
            c = np.asarray(ad.layers["counts"][:, ad.var_names.get_loc(gid)].todense()).ravel()
            r[s] = {}
            for g, m in grupos.items():
                if m.sum() == 0:
                    continue
                cp = c[m] / tot[m] * 1e4
                r[s][g] = {"n": int(m.sum()), "frac": round(float((c[m] > 0).mean()), 3),
                           "cp10k_medio_todas": round(float(cp.mean()), 2)}
        res[t] = r
        print("==", t)
        for s, v in r.items():
            print(f"  {s:9s}", v if isinstance(v, str) else
                  " | ".join(f"{g}: {d['frac']*100:.0f}% {d['cp10k_medio_todas']}" for g, d in v.items()))
    guarda_json(res, "control_referencia.json")
