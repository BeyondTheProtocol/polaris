"""Cierre técnico del Flex (REGLAS_CIERRE_v3.json): constantes y utilidades comunes.

Reutiliza _comun del tribunal (copiado en tools/flex_analisis/tribunal/). Salidas en
~/Clinico-PRIVADO/flex/cierre/ (o en $CIERRE_OUT para el test de reproducibilidad). Cachés en
~/Clinico-PRIVADO/flex/cierre/cache/. stdout: solo agregados, ni barcodes ni ids de bloque.
"""
import hashlib
import json
import math
import os
import sys

for _v in ("NUMBA_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np  # noqa: E402

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "tribunal"))
import _comun as TC  # noqa: E402

SEED = 20261016
FLEX = TC.FLEX
TRIB = TC.OUT
CIERRE = os.path.join(FLEX, "cierre")
CACHE = os.path.join(CIERRE, "cache")
REGLAS = os.path.join(AQUI, "REGLAS_CIERRE_v3.json")
Z = 1.959964

# GRCh38, centrómeros aproximados (Mb). Aproximación declarada en REGLAS (±2 Mb).
CENTROMEROS_MB = {"chr1": 123.4, "chr2": 93.9, "chr3": 90.9, "chr4": 50.0, "chr5": 48.8, "chr6": 59.8, "chr7": 60.1,
                  "chr8": 45.2, "chr9": 43.0, "chr10": 39.8, "chr11": 53.4, "chr12": 35.5, "chr13": 17.7, "chr14": 17.2,
                  "chr15": 19.0, "chr16": 36.8, "chr17": 25.1, "chr18": 18.5, "chr19": 26.2, "chr20": 28.1, "chr21": 12.0,
                  "chr22": 15.0}
ACROCENTRICOS = {"chr13", "chr14", "chr15", "chr21", "chr22"}
EXCLUIR_CHR = {"chr8", "chr11", "chrX", "chrY", "chrM"}

GL = ["FOXA1", "ESR1", "ANKRD30A", "TRPS1", "SPDEF", "AGR2", "TFF1", "TFF3", "KRT19", "KRT7", "EPCAM", "CLDN4", "MUC1"]
NULO_TIPOS = ["NK", "endotelio", "mieloide"]
BILIAR = ["HNF1B", "CFTR", "SCTR"]
MESOTELIAL = ["UPK3B", "CALB2", "WT1", "MSLN"]
HEP5 = TC.HEP5
GENES_T6 = ["GRPR", "FAP", "ITGB6", "ROR1", "SLC39A6", "ERBB3", "ANKRD30A", "TPBG"]
LOO_IDENTIDAD = ["ANKRD30A", "MUC1"]


def out_dir():
    d = os.environ.get("CIERRE_OUT") or CIERRE
    os.makedirs(d, exist_ok=True)
    return d


def outp(nombre):
    return os.path.join(out_dir(), nombre)


def cachep(nombre):
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, nombre)


def sha256(path):
    return TC.sha256(path)


def _r(x):
    """Redondeo a 6 cifras significativas (determinismo de la serialización)."""
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        x = float(x)
        if math.isnan(x) or math.isinf(x):
            return None
        return float(f"{x:.6g}")
    if isinstance(x, dict):
        return {str(k): _r(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_r(v) for v in x]
    if isinstance(x, np.ndarray):
        return [_r(v) for v in x.tolist()]
    return x


def guarda(obj, nombre):
    p = outp(nombre)
    with open(p, "w") as f:
        json.dump(_r(obj), f, indent=1, ensure_ascii=False, sort_keys=True)
    return p


def guarda_npz(nombre, **arrays):
    """npz determinista: entradas en orden, fecha fija y sin compresión variable (np.savez_compressed pone la
    hora actual en el zip y rompe el hash entre ejecuciones)."""
    import io
    import zipfile
    p = outp(nombre)
    with zipfile.ZipFile(p, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for k in sorted(arrays):
            b = io.BytesIO()
            np.lib.format.write_array(b, np.asarray(arrays[k]), allow_pickle=False)
            zi = zipfile.ZipInfo(k + ".npy", date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, b.getvalue())
    return p


def wilson(k, n, z=Z):
    if n == 0:
        return (None, None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    lo, hi = max(0.0, c - h), min(1.0, c + h)
    if k == 0:
        lo = 0.0
    if k == n:
        hi = 1.0
    return (p, lo, hi)


def w(k, n):
    p, lo, hi = wilson(int(k), int(n))
    return {"k": int(k), "n": int(n), "frac": p, "ic95_wilson": [lo, hi]}


def garwood(k, conf=0.95):
    """IC exacto de Poisson (Garwood) para un recuento k."""
    from scipy.stats import chi2
    a = 1 - conf
    lo = 0.0 if k == 0 else chi2.ppf(a / 2, 2 * k) / 2
    hi = chi2.ppf(1 - a / 2, 2 * k + 2) / 2
    return float(lo), float(hi)


def clopper(k, n, conf=0.95):
    from scipy.stats import beta
    a = 1 - conf
    lo = 0.0 if k == 0 else beta.ppf(a / 2, k, n - k + 1)
    hi = 1.0 if k == n else beta.ppf(1 - a / 2, k + 1, n - k)
    return float(lo), float(hi)


def deciles(umi, base=None):
    """Asigna decil de UMI (0-9) con cortes calculados sobre `base` (por defecto, umi)."""
    b = umi if base is None else base
    q = np.quantile(b, np.linspace(0, 1, 11))[1:-1]
    return np.digitize(umi, q)


def pesos_umi(umi_obj, umi_ref):
    return TC_suelo().pesos_umi(umi_obj, umi_ref)


def TC_suelo():
    import _suelo
    return _suelo


def bh(p):
    """Benjamini-Hochberg: q-valores."""
    p = np.asarray(p, float)
    n = len(p)
    o = np.argsort(p, kind="mergesort")
    q = np.empty(n)
    acc = 1.0
    for r in range(n - 1, -1, -1):
        i = o[r]
        acc = min(acc, p[i] * n / (r + 1))
        q[i] = acc
    return q
