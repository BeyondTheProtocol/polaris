"""Referencia EXTERNA pública (tercer contraste, REGLAS_HIGADO_v4): 10x 4-plex FFPE, Liver_BC1 (hígado sano, Flex,
probe set v1.0.1, CC BY 4.0). Subpasos: baja (descarga a un directorio propio y vacío, registra sha256) | analiza
(intersección de genes, control de lote, AUC con la referencia externa). Solo DESCARGA: nada sale.
"""
import json
import os
import urllib.request

import numpy as np
import scipy.sparse as sp

import _a
from _a import _c, cachep

URL = ("https://cf.10xgenomics.com/samples/cell-exp/7.1.0/4plex_human_liver_colorectal_ovarian_panc_scFFPE_multiplex_Liver_BC1/"
       "4plex_human_liver_colorectal_ovarian_panc_scFFPE_multiplex_Liver_BC1_count_sample_filtered_feature_bc_matrix.h5")
DIR = os.path.join(_a.AMB, "externa_liver_bc1")


def baja():
    os.makedirs(DIR, exist_ok=True)
    p = os.path.join(DIR, "liver_bc1_filtered.h5")
    if not os.path.exists(p):
        req = urllib.request.Request(URL, headers={"User-Agent": "curl/8.7.1"})
        with urllib.request.urlopen(req, timeout=300) as r, open(p, "wb") as f:
            f.write(r.read())
    info = {"url": URL, "bytes": os.path.getsize(p), "sha256": _c.sha256(p), "licencia": "CC BY 4.0 (10x Genomics; no leída en la página)"}
    with open(os.path.join(DIR, "procedencia.json"), "w") as f:
        json.dump(info, f, indent=1)
    print(info)


def lee():
    import scanpy as sc
    ad = sc.read_10x_h5(os.path.join(DIR, "liver_bc1_filtered.h5"), gex_only=True)
    ad.var["simbolo"] = ad.var_names.astype(str)
    ad.var_names = ad.var["gene_ids"].astype(str).values
    return ad


def analiza():
    """Tipado grueso de las células externas por marcadores, control de lote frente a nuestro nulo y AUC del eje 2 del
    cierre usando la media externa como referencia."""
    import _final as F
    import _amb_final as AF
    ext = lee()
    L = AF.obj("higado", "cruda")
    gids = L.o["gene_ids"].astype(str)
    comunes = np.isin(gids, ext.var_names)
    E = ext[:, gids[comunes]].X.tocsr().astype(np.float64)
    sym = L.sym[comunes]
    det = lambda gs: np.asarray((E[:, np.isin(sym, gs)] > 0).sum(axis=1)).ravel()  # noqa: E731
    imm = det(["PTPRC"]) >= 1
    hep = (det(["ALB", "APOA1", "SERPINA1", "HP", "TF"]) >= 2) & ~imm
    res = {"genes_ext": int(ext.n_vars), "genes_comunes": int(comunes.sum()), "celulas_ext": int(ext.n_obs),
           "inmunes_ext": int(imm.sum()), "hepatocitos_ext": int(hep.sum()),
           "UMI_mediana_ext": float(np.median(np.asarray(E.sum(axis=1)).ravel()))}
    # perfil de brazos del eje 2 (mismos genes que el cierre, restringidos a la intersección)
    B = AF.obj("hueso", "cruda")
    nulo = np.isin(L.tipo, AF.NULO)
    hp = L.tipo == "hepatocito"
    fib = L.tipo == "fibroblasto_estelar"
    cand = ~nulo & ~hp
    mal68 = B.o["trib_maligna_T_limpio_T"].astype(bool)
    refB = np.isin(B.tipo, F.TC.REF_TIPOS) & B.o["trib_limpio_T"].astype(bool)
    gok, brazo, validos, info = F.brazos_genes(L, B, mal68, refB, cand, nulo, hp)
    gok &= comunes
    out = {}
    for nombre, mref in (("inmunes_ext", imm), ("hepatocitos_ext", hep)):
        if mref.sum() < 30:
            out[nombre] = "menos de 30 células"
            continue
        # media externa por gen en log1p CP10k, en el espacio de genes de L
        Ee = E[mref]
        Ee = sp.diags(1e4 / np.maximum(np.asarray(Ee.sum(axis=1)).ravel(), 1)) @ Ee
        Ee.data = np.log1p(Ee.data)
        mu = np.zeros(len(gids))
        mu[comunes] = np.asarray(Ee.mean(axis=0)).ravel()
        Xg = L.X[:, gok].astype(np.float64).tocsr()
        Xg = sp.diags(1e4 / np.maximum(L.umi, 1)) @ Xg
        Xg.data = np.log1p(Xg.data)
        ab = brazo[gok]
        A = np.zeros((len(ab), len(validos)))
        for j, b in enumerate(validos):
            m = ab == b
            A[m, j] = 1.0 / max(m.sum(), 1)
        VL = np.asarray(Xg @ A) - mu[gok] @ A
        lote = VL[nulo].mean(axis=0)
        lote_c = lote - lote.mean()
        VB = F.valores_brazo(B, gok, brazo, validos, refB)
        plantilla = F.centra(VB)[mal68].mean(axis=0)
        score = F.corr_filas(F.centra(VL), plantilla)
        prim = np.isin(L.tipo, ["epitelial", "no_asignado"]) & ~(L.ndet(F.TC.HEP5) >= 2)
        plano = bool(np.abs(lote_c).max() <= 0.1 and lote_c.std() <= 0.05)
        out[nombre] = {"brazos": len(validos), "genes": int(gok.sum()), "lote_nulo_max_abs": float(np.abs(lote_c).max()),
                       "lote_nulo_DE": float(lote_c.std()), "PLANO": plano,
                       "AUC_pool_vs_hepatocitos": F.auc(score[prim], score[hp]), "AUC_pool_vs_nulo": F.auc(score[prim], score[nulo]),
                       "AUC_pool_vs_fibroblastos": F.auc(score[prim], score[fib]),
                       "uso": "contraste válido" if plano else "LOTE: no se usa para el veredicto"}
    res["eje2_con_referencia_externa"] = out
    os.environ["CIERRE_OUT"] = _a.out_dir()
    _c.guarda(res, "externa.json")
    print(json.dumps(_c._r(res), ensure_ascii=False, indent=1))


def run(sub="baja", *a):
    {"baja": baja, "analiza": analiza}[sub]()
