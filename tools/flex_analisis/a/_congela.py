"""Capa E (espec.): h5ad congelado (ambas muestras, cuentas crudas, malignas etiquetadas, Ensembl ID
como var_names para el join con el WES) + MANIFIESTO_A.json con sha256 de salidas y scripts."""
import glob
import json
import os
import subprocess

import anndata as ad_
import numpy as np
import scanpy as sc

from _comun import OUT, SEED, TEJIDOS, out, sha256

OBS = ["muestra", "leiden", "tipo", "maligna", "pob_usada", "pob_respaldo", "pob_p99", "pob_epitelial", "epitelial_cluster", "epitelial_celula", "cnv_corr",
       "infercnv_aberrante", "infercnv_cluster_aberrante", "copykat", "APM", "NE", "phase",
       "total_counts", "n_genes_by_counts", "pct_counts_mt"]


def run(*a):
    partes = []
    for t in TEJIDOS:
        ad = sc.read_h5ad(out(f"tab_{t}.h5ad"))
        x = ad.layers["counts"].copy()
        e = np.load(out(f"exposicion_{t}.npy"))
        n = ad_.AnnData(X=x, obs=ad.obs[[c for c in OBS if c in ad.obs]].copy(),
                        var=ad.var[["gene_ids", "simbolo"]].copy())
        n.obs["umi_otra_muestra_mismo_GEM"] = e
        if t == "higado":
            # CopyKAT en hígado NO es informativo (purificada.json: FALLA; con referencia contaminada
            # llamó diploide a todo). La columna se vacía para que nadie la lea como biología.
            n.obs["copykat"] = "no_informativo_higado"
        n.obs_names = [f"{t}:{b}" for b in ad.obs_names]
        partes.append(n)
    c = ad_.concat(partes, join="outer", merge="same")
    c.uns["procedencia"] = {"espec": "docs/flex/ESPEC_CONGELADA_v1.md sha256 6165b9ef8a7b452cdce055d1542ca38a72eeb0c66c4453083278151f6a4030dc",
                            "tar": "931c64af777e", "semilla": SEED, "analista": "A",
                            "poblacion_maligna": "obs['pob_usada']: hueso = consenso de la espec (inferCNV ∧ CopyKAT ∧ epitelial); hígado = respaldo EXPLORATORIO (epitelial ∧ inferCNV por clúster), porque el consenso de la espec dio 0 y CopyKAT no es informativo en hígado (purificada.json, FALLA)",
                            "copykat_higado": "no informativo; ver purificada.json",
                            "commit": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                                     cwd=os.path.dirname(os.path.abspath(__file__))).stdout.strip()}
    for k in ("maligna", "pob_usada", "pob_respaldo", "pob_p99", "pob_epitelial", "epitelial_cluster", "epitelial_celula", "infercnv_aberrante", "infercnv_cluster_aberrante"):
        if k in c.obs:
            c.obs[k] = c.obs[k].astype(bool)
    for k in ("copykat", "phase", "tipo", "leiden", "muestra"):
        if k in c.obs:
            c.obs[k] = c.obs[k].astype(str)
    p = out("flex_A_congelado.h5ad")
    c.write_h5ad(p, compression="gzip")
    man = {"salidas": {}, "scripts": {}}
    for f in sorted(glob.glob(os.path.join(OUT, "*.json")) + glob.glob(os.path.join(OUT, "*.csv"))
                    + [p]):
        if os.path.basename(f) == "MANIFIESTO_A.json":
            continue
        man["salidas"][os.path.relpath(f, OUT)] = sha256(f)
    here = os.path.dirname(os.path.abspath(__file__))
    for f in sorted(glob.glob(os.path.join(here, "*.py")) + glob.glob(os.path.join(here, "*.R"))
                    + glob.glob(os.path.join(here, "*.json"))):
        man["scripts"][os.path.basename(f)] = sha256(f)
    with open(out("MANIFIESTO_A.json"), "w") as fh:
        json.dump(man, fh, indent=1)
    print("congelado:", c.shape, "consenso espec:", int(c.obs["maligna"].sum()), "población usada:", int(c.obs["pob_usada"].sum()))
    print("sha256 h5ad:", man["salidas"]["flex_A_congelado.h5ad"])
    print("sha256 manifiesto:", sha256(out("MANIFIESTO_A.json")))
