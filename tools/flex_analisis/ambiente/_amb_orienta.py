"""Orientación: tamaños y curva de UMI del raw (solo agregados)."""
import collections
import os

import numpy as np

import _a
from _a import TC, _c


def run():
    for f in sorted(os.listdir(_c.CACHE)):
        print("cache cierre:", f, os.path.getsize(os.path.join(_c.CACHE, f)) // 1_000_000, "MB")
    for t in TC.TEJIDOS:
        for suf in ("_raw_feature_barcode_matrix.h5", "_filtered_feature_barcode_matrix.h5", "_raw_probe_bc_matrix.h5"):
            p = TC.ruta(t, suf)
            print(t, suf, os.path.exists(p), os.path.getsize(p) // 1_000_000 if os.path.exists(p) else -1, "MB")
        bc, tot = TC.barcodes_raw(t)
        tot = np.sort(tot)[::-1]
        print(t, "raw barcodes", len(bc), "UMI>0", int((tot > 0).sum()), "UMI>=10", int((tot >= 10).sum()),
              "UMI>=100", int((tot >= 100).sum()), "UMI>=500", int((tot >= 500).sum()))
        print(t, "UMI por rango:", {r: int(tot[r]) for r in (1000, 5000, 10000, 15000, 20000, 25000, 30000, 40000, 60000, 100000, 200000) if r < len(tot)})
        o = np.load(_c.cachep(f"obs_{t}.npz"), allow_pickle=True)
        print(t, "células cierre", len(o["umi"]), "UMI mediana", float(np.median(o["umi"])), "vacíos", int(o["n_vacios"]), "UMI vacíos", int(o["umi_vacios"]))
        print(t, "tipos", dict(collections.Counter(o["tipo"].tolist())))
        print(t, "llamadas CR:", len(TC_llamadas(t)))


def TC_llamadas(t):
    import _suelo
    return _suelo.llamadas(t)
