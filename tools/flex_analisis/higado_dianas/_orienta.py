"""Orientación ANTES de fijar REGLAS_HIGADO_v1: solo panel (nº de sondas), tipos, UMI y cachés.
No mira la expresión de ninguna diana en ningún conjunto tumoral. stdout: agregados."""
import os
import re
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "cierre"))
import numpy as np  # noqa: E402

import _c  # noqa: E402

print("cache:", sorted((f, round(os.path.getsize(os.path.join(_c.CACHE, f)) / 1e6, 1)) for f in os.listdir(_c.CACHE)))
print("cierre dir:", sorted(os.listdir(_c.CIERRE)))
DIANAS = ["ANKRD30A", "TPBG", "SLC39A6", "ERBB3", "ERBB2", "CD276", "NECTIN4", "TACSTD2", "MUC1", "ITGB6", "ROR1", "FOLR1",
          "CEACAM5", "VTCN1", "CDH17", "GPNMB", "MSLN", "CLDN6", "CLDN18", "{{DIANA2}}", "SEZ6", "{{DIANA}}", "SSTR5", "NCAM1", "CHGA",
          "SYP", "{{DIANA3}}", "FGFR1", "CCND1", "ESR1", "PGR", "AR", "PRAME", "CTAG2", "CTAG1B", "B2M", "TAP1", "TAP2", "NLRC5",
          "PSMB8", "PSMB9"]
OTROS = ["GATA3", "ESR1", "TFF1", "TFF3", "KRT8", "KRT18", "KRT19", "FOXA1", "SPDEF", "AGR2", "ALB", "APOA1", "SERPINA1",
         "HP", "TF", "PTPRC", "CD3E", "CD3D", "CD2", "NKG7", "GNLY", "LYZ", "CD68", "CD163", "C1QA", "MS4A1", "CD79A", "CSF1R",
         "PECAM1", "VWF", "CDH5", "KDR", "EMCN", "CLEC4G", "STAB2", "FLT1", "COL1A1", "COL1A2", "COL3A1", "DCN", "LUM",
         "ACTA2", "RGS5", "PDGFRB", "TAGLN"]
for t in ("higado", "hueso"):
    o = np.load(_c.cachep(f"obs_{t}.npz"), allow_pickle=True)
    sym = o["simbolo"]
    ns = o["nsondas"]
    umi = o["umi"]
    tipo = o["tipo"]
    print(f"== {t}: células {len(umi)}, genes {len(sym)}, en panel {(ns > 0).sum()}")
    u, c = np.unique(tipo, return_counts=True)
    print(" tipos:", dict(zip(u.tolist(), c.tolist())))
    print(" UMI cuantiles 5/10/25/50/75/90:", np.percentile(umi, [5, 10, 25, 50, 75, 90]).round(0).tolist())
    pool = np.isin(tipo, ["epitelial", "no_asignado"])
    if pool.any():
        print(" UMI pool cuantiles 5/10/25/50/75/90:", np.percentile(umi[pool], [5, 10, 25, 50, 75, 90]).round(0).tolist())
    if t == "hueso":
        m = o["trib_maligna_T_limpio_T"].astype(bool)
        print(" 68 limpias:", int(m.sum()), "UMI mediana", float(np.median(umi[m])))
    if t == "higado":
        d = {}
        for g in DIANAS:
            j = np.where(sym == g)[0]
            d[g] = int(ns[j[0]]) if len(j) else "no_en_matriz"
        print(" sondas dianas:", d)
        print(" CT:", [(s, int(n)) for s, n in zip(sym, ns) if re.match(r"^(MAGE[ABC]\d+|CTAG|PRAME)", s)])
        cyp = [(s, int(n)) for s, n in zip(sym, ns) if s.startswith("CYP") and n > 0]
        print(" CYP en panel:", len(cyp), cyp)
        print(" otros:", {g: (int(ns[np.where(sym == g)[0][0]]) if (sym == g).any() else "-") for g in OTROS})

# --- orientación 2: qué CYP son hepáticos (pseudobulk CP10k hepatocito frente a epitelial/no_asignado) y controles negativos
import scipy.sparse as sp  # noqa: E402
X = sp.load_npz(_c.cachep("X_higado.npz")).tocsr()
o = np.load(_c.cachep("obs_higado.npz"), allow_pickle=True)
sym, tipo = o["simbolo"], o["tipo"]


def pb(m):
    return np.asarray(X[m].sum(axis=0)).ravel() / X[m].sum() * 1e4


ph, pe, pn = pb(tipo == "hepatocito"), pb(tipo == "epitelial"), pb(tipo == "no_asignado")
cand = [s for s in sym if s.startswith("CYP")] + ["APOC3", "APOH", "FGB", "FGA", "AHSG", "RBP4", "APOA2", "TTR", "HPX", "ORM1"]
for s in cand:
    j = np.where(sym == s)[0][0]
    if ph[j] > 0.5:
        print(f"  {s}: hep {ph[j]:.2f} epi {pe[j]:.2f} no_asig {pn[j]:.2f} cociente hep/epi {ph[j] / max(pe[j], 1e-3):.1f}")
