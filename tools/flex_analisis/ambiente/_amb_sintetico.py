"""Prueba de SoupX y DecontX con datos SINTÉTICOS (sin datos de la paciente): 70 % «tumor», 20 % «inmune»,
10 % «hepatocito», ambiente = perfil del tumor (como en el hígado). Verdad conocida: los genes luminales no se
expresan en inmunes. Mide la caída de luminales en inmunes y la retención de genes de hepatocito y de tumor.

Uso: python -I ambiente.py sintetico [directorio_salida]   (por defecto un tmpdir que se borra)
Devuelve 0 si los dos métodos cumplen: caída luminal en inmunes >= 75 % y retención >= 80 %.
"""
import json
import os
import shutil
import subprocess
import tempfile

import numpy as np
import scipy.sparse as sp

import _a
import _amb_prep as P

G_LUM = ["ESR1", "ANKRD30A", "TFF1", "SCGB2A2", "AGR2", "SPDEF", "KRT19", "FOXA1"]
G_HEP = ["ALB", "APOA1", "SERPINA1", "HP", "TF"]
G_INM = ["PTPRC", "CD3E", "NKG7", "LYZ", "CD68"]


def simula(seed=_a.SEED, n=3000, ngen=600, rho=0.15):
    rng = np.random.default_rng(seed)
    sym = G_LUM + G_HEP + G_INM + [f"G{i}" for i in range(ngen - 18)]
    base = rng.gamma(0.3, 1.0, ngen)
    perfiles = {}
    for t, g, f in (("tumor", G_LUM, 40), ("inmune", G_INM, 40), ("hepatocito", G_HEP, 200)):
        p = base.copy()
        idx = [sym.index(x) for x in g]
        p[idx] = base.mean() * f
        if t != "tumor":
            p[[sym.index(x) for x in G_LUM]] = 0.0
        if t != "hepatocito":
            p[[sym.index(x) for x in G_HEP]] = 0.0
        if t != "inmune":
            p[[sym.index(x) for x in G_INM]] = 0.0
        perfiles[t] = p / p.sum()
    tipo = rng.choice(["tumor", "inmune", "hepatocito"], size=n, p=[0.7, 0.2, 0.1])
    umi = rng.lognormal(np.log(2000), 0.5, n).astype(int) + 200
    soup = perfiles["tumor"] * 0.9 + perfiles["hepatocito"] * 0.1
    filas, verdad = [], []
    for i in range(n):
        endo = rng.multinomial(int(umi[i] * (1 - rho)), perfiles[tipo[i]])
        amb = rng.multinomial(int(umi[i] * rho), soup)
        filas.append(endo + amb)
        verdad.append(endo)
    X = sp.csr_matrix(np.array(filas))
    V = sp.csr_matrix(np.array(verdad))
    E = sp.csr_matrix(rng.multinomial(50, soup, size=4000))
    tipo_lab = np.where(tipo == "inmune", "NK", tipo)
    return X, V, E, sym, tipo_lab


def mide(X, V, sym, tipo, Y):
    lum = np.isin(sym, ["ESR1", "ANKRD30A", "TFF1", "SCGB2A2", "KRT19"])
    hep = np.isin(sym, G_HEP)
    inm = tipo == "NK"
    det = lambda M: float((np.asarray(M[inm][:, lum].sum(axis=1)).ravel() > 0).mean())  # noqa: E731
    antes, despues = det(X), det(Y)
    ret_hep = float(Y[tipo == "hepatocito"][:, hep].sum() / max(V[tipo == "hepatocito"][:, hep].sum(), 1))
    ret_tum = float(Y[tipo == "tumor"][:, lum].sum() / max(V[tipo == "tumor"][:, lum].sum(), 1))
    return {"lum_en_inmunes_antes": antes, "lum_en_inmunes_despues": despues,
            "caida_relativa": 1 - despues / antes if antes else None, "retencion_hep_vs_verdad": ret_hep,
            "retencion_lum_tumor_vs_verdad": ret_tum}


def run(*args):
    X, V, E, sym, tipo = simula()
    d = args[0] if args else tempfile.mkdtemp(prefix="amb_sint_")
    os.makedirs(d, exist_ok=True)
    P._escribe_csr(d, "celulas", X)
    P._escribe_csr(d, "vacias", E)
    with open(os.path.join(d, "meta.json"), "w") as f:
        json.dump({"genes": sym, "simbolos": sym, "tipo": tipo.tolist(), "n_celulas": X.shape[0], "n_vacias": E.shape[0],
                   "n_genes": X.shape[1], "semilla": _a.SEED, "genes_estima": G_LUM, "nulo": ["NK"]}, f)
    rbin = os.path.join(_a.ENVS, "ambiente-r", "bin", "Rscript")
    r = subprocess.run([rbin, "--vanilla", os.path.join(_a.AQUI, "_amb_descont.R"), d], capture_output=True, text=True)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-3000:])
        return 1
    info = json.load(open(os.path.join(d, "info_r.json")))
    res = {"rho_verdad": 0.15, "info_R": info}
    ok = True
    for m in P.METODOS:
        Y = P._lee_csc_r(d, m, X.shape[1], X.shape[0])
        Y.data = np.rint(Y.data)
        res[m] = mide(X, V, np.array(sym), tipo, Y)
        if m != "soupx_auto":
            ok &= res[m]["caida_relativa"] >= 0.75 and res[m]["retencion_hep_vs_verdad"] >= 0.8 and res[m]["retencion_lum_tumor_vs_verdad"] >= 0.8
        print(m, {k: round(v, 3) for k, v in res[m].items() if v is not None})
    res["ok"] = bool(ok)
    print("SINTETICO", "OK" if ok else "FALLA")
    with open(os.path.join(d, "sintetico.json"), "w") as f:
        json.dump(res, f, indent=1, sort_keys=True, default=float)
    if not args:
        shutil.rmtree(d)
    return 0 if ok else 1
