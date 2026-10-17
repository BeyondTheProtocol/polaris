"""Constantes y utilidades comunes del analista A (Flex). Sin identificadores de la paciente aquí.

El mapeo muestra→tejido se deriva en tiempo de ejecución de config.csv por probe barcode
(A-D10 = hígado, A-D11 = hueso, según la especificación congelada v1), para que ningún
identificador de bloque quede en el repo ni salga por stdout.
"""
import configparser
import hashlib
import json
import os

import numpy as np

SEED = 20261016
HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, "Clinico-PRIVADO", "flex", "931c64af777e")
OUT = os.path.join(HOME, "Clinico-PRIVADO", "flex", "analisis_A")
PER_SAMPLE = os.path.join(BASE, "MP", "SC_MULTI_CS", "SC_MULTI_CORE",
                          "STRUCTIFY_PER_SAMPLE_OUTS", "fork0", "files")
REF = os.path.join(HOME, ".claude-science", "ref", "ensembl110")
PROBE_BC_TEJIDO = {"A-D10": "higado", "A-D11": "hueso"}
TEJIDOS = ("higado", "hueso")


def _muestras():
    """{tejido: id_bloque} leído de config.csv. El id nunca se imprime."""
    txt = open(os.path.join(BASE, "MP", "outs", "config.csv")).read()
    sec = txt.split("[samples]")[1].strip().splitlines()
    out = {}
    for line in sec[1:]:
        if not line.strip() or line.startswith("["):
            break
        sid, pbc = [x.strip() for x in line.split(",")[:2]]
        out[PROBE_BC_TEJIDO[pbc]] = sid
    assert set(out) == set(TEJIDOS), "config.csv no tiene las dos muestras esperadas"
    return out


def ruta_muestra(tejido, sufijo):
    return os.path.join(PER_SAMPLE, _muestras()[tejido] + sufijo)


def out(*p):
    os.makedirs(OUT, exist_ok=True)
    return os.path.join(OUT, *p)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def guarda_json(obj, nombre):
    p = out(nombre)
    with open(p, "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False, default=_js)
    return p


def _js(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def semilla_gen(nombre):
    """Semilla determinista por gen: SEED + hash estable del nombre."""
    return SEED + int(hashlib.sha256(nombre.encode()).hexdigest()[:8], 16) % 100000


def bootstrap_frac(k, n, nombre="", n_boot=1000):
    """Fracción k/n con IC95 percentil por bootstrap no paramétrico.

    Remuestrear n células con reemplazo y contar positivas es exactamente Binomial(n, k/n),
    así que se muestrea la binomial (misma distribución, sin matriz n_boot × n).
    """
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    p = k / n
    rng = np.random.default_rng(semilla_gen(nombre))
    fr = rng.binomial(n, p, size=n_boot) / n
    return (float(p), float(np.percentile(fr, 2.5)), float(np.percentile(fr, 97.5)))


def carga(tejido):
    """AnnData de la matriz filtrada por muestra; var_names = Ensembl ID, var['simbolo']."""
    import scanpy as sc
    ad = sc.read_10x_h5(ruta_muestra(tejido, "_filtered_feature_barcode_matrix.h5"), gex_only=True)
    ad.var["simbolo"] = ad.var_names.astype(str)
    ad.var_names = ad.var["gene_ids"].astype(str).values
    ad.var_names_make_unique()
    ad.obs["muestra"] = tejido
    return ad


def ids_de(ad, simbolos):
    """{simbolo: ensembl_id o None} usando var['simbolo']."""
    m = {}
    for s in simbolos:
        hit = ad.var.index[ad.var["simbolo"] == s]
        m[s] = hit[0] if len(hit) else None
    return m
