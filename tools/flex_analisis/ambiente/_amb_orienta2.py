"""Orientación 2: versión del probe set, sondas DEPRECATED, duplicados de gen en la matriz de features (solo agregados)."""
import collections
import os

import h5py
import numpy as np

import _a
from _a import TC


def run():
    p = os.path.join(TC.BASE, "MP", "outs", "probe_set.csv")
    with open(p) as f:
        cab = [next(f) for _ in range(8)]
    print("probe_set cabecera:", [c.strip() for c in cab if c.startswith("#")])
    ps = TC.probe_set()
    print("probe_set filas", len(ps), "columnas", list(ps.columns))
    print("included:", ps["included"].value_counts().to_dict())
    print("DEPRECATED en probe_id:", int(ps["probe_id"].str.contains("DEPRECATED").sum()),
          "incluidas así:", int((ps["probe_id"].str.contains("DEPRECATED") & ps["included"]).sum()))
    print("region:", ps["region"].value_counts().to_dict() if "region" in ps else "sin columna")
    for t in TC.TEJIDOS:
        with h5py.File(TC.ruta(t, "_raw_feature_barcode_matrix.h5"), "r") as f:
            ids = [x.decode() for x in f["matrix/features/id"][:]]
            nom = [x.decode() for x in f["matrix/features/name"][:]]
            ft = [x.decode() for x in f["matrix/features/feature_type"][:]]
            print(t, "features", len(ids), "tipos", dict(collections.Counter(ft)), "ids duplicados", len(ids) - len(set(ids)),
                  "nombres duplicados", len(nom) - len(set(nom)))
        with h5py.File(TC.ruta(t, "_raw_probe_bc_matrix.h5"), "r") as f:
            pid = [x.decode() for x in f["matrix/features/id"][:]]
            print(t, "probe matrix features", len(pid), "DEPRECATED", sum("DEPRECATED" in x for x in pid))
    import scanpy  # noqa: F401
    print("env nescore ok", np.__version__)
