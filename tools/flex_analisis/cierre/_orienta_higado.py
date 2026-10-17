"""Orientación (solo agregados, ANTES de fijar reglas): composición del hígado y marcadores biliares
frente a mamarios, para saber si existe una población epitelial NO tumoral (colangiocitos) utilizable
como nulo. No calcula ninguna cifra de resultado; solo cuenta tipos y detección de marcadores."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tribunal"))
from _comun import carga, out  # noqa: E402

BILIAR = ["HNF1B", "CFTR", "ONECUT1", "SOX9", "AQP1", "SLC4A2", "ANXA4", "SCTR", "MMP7", "SPP1", "CXCL1", "LCN2",
          "PROM1", "TACSTD2", "KRT7", "KRT19", "EPCAM", "FXYD2", "CLDN4", "ELF3", "HNF4A"]
MAMA = ["GATA3", "ESR1", "FOXA1", "TFF1", "ANKRD30A", "TRPS1", "SPDEF", "AGR2", "XBP1", "PGR", "MGP", "SCGB2A2",
        "PIP", "TFF3", "KRT8", "KRT18", "FGFR1", "CCND1", "AR", "MUC1"]


def run():
    ad = carga("higado")
    tipo = ad.obs["tipo"].astype(str).values
    print("tipos A (hígado):", pd.Series(tipo).value_counts().to_dict())
    z = np.load(out("higado_calls.npz"))
    lum = z["lum"]
    grupos = {"luminal_T": lum, "hepatocito": z["hep"], "ref": z["ref"]}
    for t in pd.Series(tipo).value_counts().index:
        grupos[f"A:{t}"] = tipo == t
    sym = ad.var["simbolo"].values
    filas = []
    for g in BILIAR + MAMA:
        j = np.where(sym == g)[0]
        if not len(j):
            filas.append({"gen": g, "panel": False})
            continue
        v = np.asarray(ad.X[:, j[0]].todense()).ravel()
        f = {"gen": g, "panel": True}
        for k, m in grupos.items():
            f[k] = round(float((v[m] > 0).mean()), 3) if m.sum() else None
        filas.append(f)
    pd.set_option("display.width", 300)
    pd.set_option("display.max_columns", 40)
    print(pd.DataFrame(filas).to_string(index=False))
    # dentro de las luminales: ¿hay células biliares sin programa mamario?
    def det(gs):
        idx = np.where(np.isin(sym, gs))[0]
        return np.asarray((ad.X[:, idx] > 0).sum(axis=1)).ravel()
    nb = det(["HNF1B", "CFTR", "ONECUT1", "SCTR", "AQP1", "SLC4A2"])
    nm = det(["GATA3", "ESR1", "ANKRD30A", "TRPS1", "TFF1", "PGR"])
    umi = np.asarray(ad.X.sum(axis=1)).ravel()
    for nm_max in (0,):
        for nb_min in (1, 2, 3):
            m = lum & (nb >= nb_min) & (nm <= nm_max)
            print(f"luminal con >= {nb_min} biliares y <= {nm_max} mamarios: {int(m.sum())} (UMI mediana {np.median(umi[m]) if m.any() else None})")
    for t in np.unique(tipo):
        m = tipo == t
        print(f"  {t}: n {m.sum()}, biliar>=2 {int((nb[m] >= 2).sum())}, mamario>=2 {int((nm[m] >= 2).sum())}, UMI med {np.median(umi[m]):.0f}")
    print("leiden x tipo (luminal_T): n clusters", len(np.unique(ad.obs['leiden'].astype(str).values[lum])))
    cl = ad.obs["leiden"].astype(str).values
    for c in sorted(np.unique(cl), key=lambda x: int(x) if x.isdigit() else 999):
        m = cl == c
        print(f"  leiden {c}: n {m.sum()}, lum {int((m & lum).sum())}, tipo {pd.Series(tipo[m]).value_counts().head(2).to_dict()}, "
              f"biliar>=2 {(nb[m] >= 2).mean():.2f}, mamario>=2 {(nm[m] >= 2).mean():.2f}, UMI {np.median(umi[m]):.0f}")


if __name__ == "__main__":
    run()
