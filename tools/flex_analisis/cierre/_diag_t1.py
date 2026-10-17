"""Diagnóstico de T1 (POST HOC, tras ver que FDR 1 % da 0): distribución del estadístico de identidad y de
marcadores de linaje no tumoral en candidatas frente al nulo, por decil de UMI. Solo agregados."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402

import _c  # noqa: E402
from _final import T  # noqa: E402

OTROS = {"inmune": ["PTPRC", "CD74", "LYZ", "CD68", "NKG7", "GNLY", "CD3E", "MS4A1", "IGKC", "C1QA", "C1QB", "TYROBP", "FCER1G"],
         "endotelio": ["PECAM1", "VWF", "CDH5", "FLT1", "STAB2", "CLEC4G", "KDR", "EMCN"],
         "estroma": ["COL1A1", "COL1A2", "COL3A1", "DCN", "LUM", "PDGFRB", "RGS5", "ACTA2"],
         "hepatocito": _c.HEP5 + ["APOC3", "FGB", "AHSG"]}

L = T("higado")
tipo = L.tipo
nulo = np.isin(tipo, _c.NULO_TIPOS)
hep = tipo == "hepatocito"
cand = ~nulo & ~hep
dec = _c.deciles(L.umi, base=L.umi[cand | nulo])
s = L.suma(_c.GL) / np.maximum(L.umi, 1)
o = {k: L.suma(v) / np.maximum(L.umi, 1) for k, v in OTROS.items()}
print("decil | n cand, n nulo | GL cand p10/p50/p90 | GL nulo p50/p90/p99 | otros-linaje (suma) cand p50/p90 | nulo p50/p10")
oth = sum(o.values())
for d in range(10):
    mc, mn = cand & (dec == d), nulo & (dec == d)
    print(d, int(mc.sum()), int(mn.sum()), np.round(np.percentile(s[mc], [10, 50, 90]), 4), np.round(np.percentile(s[mn], [50, 90, 99]), 4),
          np.round(np.percentile(oth[mc], [50, 90]), 4), np.round(np.percentile(oth[mn], [50, 10]), 4))
for k, v in o.items():
    print(k, "cand p50/p90", np.round(np.percentile(v[cand], [50, 90]), 4), "| nulo p50", round(float(np.median(v[nulo])), 4),
          "| hep p50", round(float(np.median(v[hep])), 4))
# ambiente: fracción de GL y de otros-linaje en el perfil de vacíos
pa = L.o["p_amb"]
print("ambiente: GL", round(float(pa[np.isin(L.sym, _c.GL)].sum()), 4), {k: round(float(pa[np.isin(L.sym, v)].sum()), 4) for k, v in OTROS.items()})
print("A tipos en candidatas:", {t: int((cand & (tipo == t)).sum()) for t in np.unique(tipo[cand])})
