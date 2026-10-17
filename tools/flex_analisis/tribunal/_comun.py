"""Tribunal (fase 6) del Flex: constantes y utilidades. Implementación propia, independiente de A y B.

Muestras por probe barcode de config.csv (A-D10 = hígado, A-D11 = hueso, espec. v1). Ningún
identificador de bloque sale por stdout. Salidas en ~/Clinico-PRIVADO/flex/tribunal/.
"""
import hashlib
import json
import os

import numpy as np

SEED = 20261016
HOME = os.path.expanduser("~")
FLEX = os.path.join(HOME, "Clinico-PRIVADO", "flex")
BASE = os.path.join(FLEX, "931c64af777e")
A_DIR = os.path.join(FLEX, "analisis_A")
B_DIR = os.path.join(FLEX, "analisis_B")
OUT = os.path.join(FLEX, "tribunal")
PER_SAMPLE = os.path.join(BASE, "MP", "SC_MULTI_CS", "SC_MULTI_CORE",
                          "STRUCTIFY_PER_SAMPLE_OUTS", "fork0", "files")
PROBE_BC_TEJIDO = {"A-D10": "higado", "A-D11": "hueso"}
TEJIDOS = ("higado", "hueso")


def muestras():
    txt = open(os.path.join(BASE, "MP", "outs", "config.csv")).read()
    sec = txt.split("[samples]")[1].strip().splitlines()
    out = {}
    for line in sec[1:]:
        if not line.strip() or line.startswith("["):
            break
        sid, pbc = [x.strip() for x in line.split(",")[:2]]
        out[PROBE_BC_TEJIDO[pbc]] = sid
    assert set(out) == set(TEJIDOS)
    return out


def ruta(tejido, sufijo):
    return os.path.join(PER_SAMPLE, muestras()[tejido] + sufijo)


def out(*p):
    os.makedirs(OUT, exist_ok=True)
    return os.path.join(OUT, *p)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _js(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, np.bool_):
        return bool(o)
    return str(o)


def guarda(obj, nombre):
    p = out(nombre)
    with open(p, "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False, default=_js)
    return p


def wilson(k, n, z=1.959964):
    """IC95 de Wilson para una proporción (analítico; no depende de semilla)."""
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (float(p), float(max(0.0, c - h)), float(min(1.0, c + h)))


def boot(k, n, n_boot=1000, seed=SEED):
    """IC95 bootstrap percentil (1.000 réplicas, semilla de la espec.) = Binomial(n, k/n)."""
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    fr = rng.binomial(n, k / n, size=n_boot) / n
    return (float(k / n), float(np.percentile(fr, 2.5)), float(np.percentile(fr, 97.5)))


def boot_diff(k1, n1, k2, n2, n_boot=2000, seed=SEED):
    """IC95 bootstrap de p1 - p2 (muestras independientes)."""
    rng = np.random.default_rng(seed)
    a = rng.binomial(n1, k1 / n1, n_boot) / n1 if n1 else np.full(n_boot, np.nan)
    b = rng.binomial(n2, k2 / n2, n_boot) / n2 if n2 else np.full(n_boot, np.nan)
    d = a - b
    return (float(k1 / max(n1, 1) - k2 / max(n2, 1)), float(np.nanpercentile(d, 2.5)),
            float(np.nanpercentile(d, 97.5)))


def gem(b):
    """Los 16 primeros nt del barcode = GEM; el sufijo identifica la muestra."""
    return b[:16]


A_OBS_COLS = ["muestra", "tipo", "leiden", "pob_usada", "pob_epitelial", "epitelial_celula",
              "phase", "total_counts", "APM"]
REF_TIPOS = ["T", "NK", "B", "plasmatica", "mieloide", "neutrofilo", "mastocito", "endotelio"]


def obs_A():
    """obs del congelado de A (mismas 21.040 células que B), con el barcode original."""
    import anndata as an
    a = an.read_h5ad(os.path.join(A_DIR, "flex_A_congelado.h5ad"), backed="r")
    o = a.obs.copy()
    a.file.close()
    o["bc"] = [n.split(":", 1)[-1] for n in o.index]
    return o


def obs_B(cols=("maligna_B", "maligna_B_explor", "tipo", "phase", "S_score", "G2M_score")):
    import anndata as an
    b = an.read_h5ad(os.path.join(B_DIR, "flex_B_congelado.h5ad"), backed="r")
    o = b.obs[list(cols)].copy()
    b.file.close()
    o["bc"] = [n.split(":", 1)[-1] for n in o.index]
    return o


def carga(tejido, solo_qc=True):
    """Matriz filtrada de Cell Ranger (genes) de la muestra, CSR, con var símbolo/Ensembl.
    solo_qc: restringe a las células que pasaron el QC común de A y B, y añade obs de A."""
    import scanpy as sc
    ad = sc.read_10x_h5(ruta(tejido, "_filtered_feature_barcode_matrix.h5"), gex_only=True)
    ad.var["simbolo"] = ad.var_names.astype(str)
    ad.var_names = ad.var["gene_ids"].astype(str).values
    ad.var_names_make_unique()
    if solo_qc:
        oa = obs_A()
        oa = oa[oa["muestra"] == tejido].set_index("bc")
        ad = ad[ad.obs_names.isin(oa.index)].copy()
        for c in A_OBS_COLS:
            ad.obs[c] = oa.loc[ad.obs_names, c].values
    ad.X = ad.X.tocsr()
    return ad


def col(ad, simbolo):
    """Vector denso de cuentas de un símbolo (None si no está)."""
    hit = np.where(ad.var["simbolo"].values == simbolo)[0]
    if len(hit) == 0:
        return None
    return np.asarray(ad.X[:, hit[0]].todense()).ravel()


def suma(ad, simbolos):
    idx = [i for i, s in enumerate(ad.var["simbolo"].values) if s in set(simbolos)]
    return np.asarray(ad.X[:, idx].sum(axis=1)).ravel()


def barcodes_raw(tejido):
    import h5py
    with h5py.File(ruta(tejido, "_raw_feature_barcode_matrix.h5"), "r") as f:
        bc = np.array([b.decode() for b in f["matrix/barcodes"][:]])
        X = _csc_to_tot(f)
    return bc, X


def _csc_to_tot(f):
    """UMI totales por barcode desde el h5 raw (matriz guardada barcode-major, CSC de genes×bc)."""
    indptr = f["matrix/indptr"][:]
    cs = np.concatenate([[0], np.cumsum(f["matrix/data"][:], dtype=np.int64)])
    return cs[indptr[1:]] - cs[indptr[:-1]]


def stream_h5(path, bc_sel, filas=None, modo="matriz", bloque=4_000_000):
    """Lee un h5 10x (features × barcodes, CSC por barcode) en bloques, sin cargarlo entero.

    bc_sel: array de barcodes (orden de salida). filas: índices de features a conservar (None = todas).
    modo «matriz»: CSR (len(bc_sel) × n_filas_conservadas) en el orden de bc_sel.
    modo «suma»: vector de cuentas por feature sumado sobre bc_sel.
    Devuelve (resultado, ids_features, nombres_features).
    """
    import h5py
    import scipy.sparse as sp
    with h5py.File(path, "r") as f:
        g = f["matrix"]
        bcs = np.array([b.decode() for b in g["barcodes"][:]])
        nfeat = int(g["shape"][0])
        ids = np.array([x.decode() for x in g["features/id"][:]])
        nom = np.array([x.decode() for x in g["features/name"][:]])
        pos = {b: i for i, b in enumerate(bcs)}
        colsel = np.array([pos.get(b, -1) for b in bc_sel])
        assert (colsel >= 0).all(), "barcodes pedidos que no están en la matriz"
        destino = np.full(len(bcs), -1, dtype=np.int64)
        destino[colsel] = np.arange(len(colsel))
        keep_f = np.zeros(nfeat, bool)
        keep_f[filas if filas is not None else slice(None)] = True
        nuevo_f = np.cumsum(keep_f) - 1
        indptr = g["indptr"][:]
        n = int(indptr[-1])
        rs, cs, vs = [], [], []
        tot = np.zeros(nfeat, dtype=np.int64)
        for s in range(0, n, bloque):
            e = min(n, s + bloque)
            ind = g["indices"][s:e]
            dat = g["data"][s:e]
            colb = np.searchsorted(indptr, np.arange(s, e), side="right") - 1
            d = destino[colb]
            m = (d >= 0) & keep_f[ind]
            if modo == "suma":
                tot += np.bincount(ind[m], weights=dat[m], minlength=nfeat).astype(np.int64)
            else:
                rs.append(d[m].astype(np.int32))
                cs.append(nuevo_f[ind[m]].astype(np.int32))
                vs.append(dat[m].astype(np.int32))
        if modo == "suma":
            return tot[keep_f], ids[keep_f], nom[keep_f]
        M = sp.csr_matrix((np.concatenate(vs), (np.concatenate(rs), np.concatenate(cs))),
                          shape=(len(bc_sel), int(keep_f.sum())))
        return M, ids[keep_f], nom[keep_f]


VENTANAS = {"8p11": ("chr8", 37.0e6, 39.0e6), "{{LOCUS}}": ("chr11", 69.0e6, 70.5e6)}
EPI4 = ["EPCAM", "KRT8", "KRT18", "KRT19"]
LUMINAL = ["EPCAM", "KRT19", "KRT7", "GATA3", "FOXA1", "ESR1", "XBP1", "AGR2", "SPDEF", "TFF1", "TFF3", "CDH1"]
HEP5 = ["ALB", "APOA1", "SERPINA1", "HP", "TF"]


def coords():
    """Ensembl ID -> símbolo, cromosoma, inicio, fin (var del congelado de B, GRCh38)."""
    import anndata as an
    b = an.read_h5ad(os.path.join(B_DIR, "flex_B_congelado.h5ad"), backed="r")
    v = b.var[["symbol", "chromosome", "start", "end", "ensembl_id"]].copy()
    b.file.close()
    return v.set_index("ensembl_id")


def genes_ventana(cv, w, excluir=()):
    ch, a, b = VENTANAS[w]
    m = (cv["chromosome"].astype(str) == ch) & (cv["start"] > a) & (cv["end"] < b)
    return [g for g in cv.index[m] if cv.loc[g, "symbol"] not in set(excluir)]


def score_ventana(ad, genes_ens, ref_mask):
    """log2((obs+0,5)/(esp+0,5)); esp = UMI_i × fracción de la ventana en el pseudobulk de la referencia."""
    idx = np.where(np.isin(ad.var_names, genes_ens))[0]
    obs = np.asarray(ad.X[:, idx].sum(axis=1)).ravel()
    umi = np.asarray(ad.X.sum(axis=1)).ravel()
    f = obs[ref_mask].sum() / umi[ref_mask].sum()
    esp = umi * f
    return np.log2((obs + 0.5) / (esp + 0.5)), obs, esp


def n_detectados(ad, simbolos):
    idx = [i for i, s in enumerate(ad.var["simbolo"].values) if s in set(simbolos)]
    return np.asarray((ad.X[:, idx] > 0).sum(axis=1)).ravel()


def probe_set():
    import pandas as pd
    return pd.read_csv(os.path.join(BASE, "MP", "outs", "probe_set.csv"), comment="#")


def llamadas(tejido):
    """Barcodes llamados como célula por Cell Ranger (filtered) de la muestra."""
    import h5py
    with h5py.File(ruta(tejido, "_filtered_feature_barcode_matrix.h5"), "r") as f:
        return np.array([b.decode() for b in f["matrix/barcodes"][:]])
