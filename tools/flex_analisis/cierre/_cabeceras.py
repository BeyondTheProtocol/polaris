"""Solo estructura (nombres de columnas y nº de filas), sin conteos de resultado: predicciones CopyKAT de
A y B, obs/obsm de cnv_higado.h5ad de A, y panel de sondas de los genes nuevos (T6)."""
import os

import pandas as pd

F = os.path.expanduser("~/Clinico-PRIVADO/flex")
for p in ("analisis_B/copykat/higado/higado_copykat_prediction.txt", "analisis_A/copykat_higado/trozo0/higado_t0_copykat_prediction.txt",
          "analisis_A/copykat_higado_pur/higado_pur_copykat_prediction.txt", "analisis_A/copykat_higado/celulas.csv",
          "analisis_A/copykat_higado/referencia.csv", "analisis_B/copykat/higado/ref.txt"):
    d = pd.read_csv(os.path.join(F, p), sep=None, engine="python", nrows=3)
    n = sum(1 for _ in open(os.path.join(F, p)))
    print(p, "| filas", n, "| columnas", list(d.columns))
import anndata as an
a = an.read_h5ad(os.path.join(F, "analisis_A/cnv_higado.h5ad"), backed="r")
print("A cnv_higado:", a.shape, "obs:", list(a.obs.columns), "obsm:", list(a.obsm.keys()), "uns:", list(a.uns.keys()))
a.file.close()
ps = pd.read_csv(os.path.join(F, "931c64af777e/MP/outs/probe_set.csv"), comment="#")
print("probe_set columnas:", list(ps.columns))
for g in ["GRPR", "FAP", "ITGB6", "ROR1", "SLC39A6", "ERBB3", "ANKRD30A", "TPBG", "UPK3B", "CALB2", "WT1", "MSLN",
          "HNF1B", "CFTR", "SCTR"]:
    s = ps[ps["gene_name"] == g]
    print(f"  {g}: sondas {len(s)}, incluidas {int(s['included'].sum())}, unspliced incl {int(((s.region == 'unspliced') & s['included']).sum())}")

# formato (enmascarado: cada nucleótido -> N) de los nombres de CopyKAT de B y del cells.txt de B
import re
Bd = os.path.join(F, "analisis_B/copykat/higado")
d = pd.read_csv(os.path.join(Bd, "higado_copykat_prediction.txt"), sep=None, engine="python")
print("B pred nombre:", re.sub("[ACGT]", "N", str(d["cell.names"].iloc[0])), "| etiquetas:", sorted(d["copykat.pred"].astype(str).unique()))
print("B cells.txt:", re.sub("[ACGT]", "N", open(os.path.join(Bd, "cells.txt")).readline().strip()))
import anndata as an
b = an.read_h5ad(os.path.join(F, "analisis_B/flex_B_congelado.h5ad"), backed="r")
print("B obs index:", re.sub("[ACGT]", "N", str(b.obs_names[0])))
b.file.close()
