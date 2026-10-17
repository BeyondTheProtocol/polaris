"""Dianas del hígado (REGLAS_HIGADO_v1.json): constantes, rutas y utilidades.

Reutiliza las cachés y las funciones del cierre (tools/flex_analisis/cierre/). Salidas en
~/Clinico-PRIVADO/flex/higado_dianas/ (o $HIGADO_OUT para el test). stdout: solo agregados.
"""
import io
import json
import os
import sys
import zipfile

for _v in ("NUMBA_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np  # noqa: E402

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "cierre"))
import _c  # noqa: E402

REGLAS = os.path.join(AQUI, "REGLAS_HIGADO_v1.json")
R = json.load(open(REGLAS))
SEED = R["semilla"]
SALIDA = os.path.join(_c.FLEX, "higado_dianas")

DIANAS = R["dianas"]["dianas"]
CT = R["dianas"]["control_CT"]
APM = R["dianas"]["presentacion"]
NEG_HEP = R["dianas"]["controles_negativos_hepatocito"]
PANEL = R["via1_alta_confianza"]["panel_identidad"]
HEP = ["ALB", "APOA1", "SERPINA1", "HP", "TF", "CYP2E1", "CYP2C9", "CYP2C8", "CYP3A4", "CYP1A2", "CYP4A11", "CYP2A6",
       "CYP2B6", "CYP8B1", "CYP2C18", "CYP4F2"]
INMUNE = ["PTPRC", "NKG7", "GNLY", "CD2", "CD3E", "CD3D", "LYZ", "CD68", "CD163", "C1QA", "CSF1R", "MS4A1", "CD79A"]
INMUNE_HUESO_EXTRA = ["S100A8", "S100A9", "MZB1", "JCHAIN", "HBB", "HBA1", "HBA2", "ACP5", "CTSK"]
ENDO = ["PECAM1", "VWF", "CDH5", "KDR", "EMCN", "CLEC4G", "STAB2", "FLT1"]
ESTROMA = ["COL1A1", "COL1A2", "COL3A1", "DCN", "LUM", "RGS5", "PDGFRB", "TAGLN", "ACTA2"]
DOBLETE = ["NKG7", "GNLY", "PTPRC"]
TIPOS_FUERA = ["hepatocito", "NK", "mieloide", "endotelio", "fibroblasto_estelar"]
REF1 = ["NK", "mieloide", "endotelio"]
COMPARTIMENTOS = ["hepatocito", "NK", "mieloide", "endotelio", "fibroblasto_estelar"]
MARCADORES = {"hepatocito": HEP + NEG_HEP, "NK": ["NKG7", "GNLY", "CD2", "PTPRC"],
              "mieloide": ["LYZ", "CD68", "CD163", "C1QA", "CSF1R"], "endotelio": ENDO, "fibroblasto_estelar": ESTROMA}
LIGADOS_ER = {"PGR", "AR", "CCND1"}
NO_RESOLUBLE = {"MAGEA6"}
Z = _c.Z


def out_dir():
    d = os.environ.get("HIGADO_OUT") or SALIDA
    os.makedirs(d, exist_ok=True)
    return d


def outp(nombre):
    return os.path.join(out_dir(), nombre)


def guarda(obj, nombre):
    p = outp(nombre)
    with open(p, "w") as f:
        json.dump(_c._r(obj), f, indent=1, ensure_ascii=False, sort_keys=True)
    return p


def guarda_npz(nombre, **arrays):
    p = outp(nombre)
    with zipfile.ZipFile(p, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for k in sorted(arrays):
            b = io.BytesIO()
            np.lib.format.write_array(b, np.asarray(arrays[k]), allow_pickle=False)
            zi = zipfile.ZipInfo(k + ".npy", date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, b.getvalue())
    return p


def deconvoluciona(pool, perfiles, amb, rho, marcadores_idx):
    """Vía 2 sobre vectores CP10k (genes). pool: (G,), perfiles: (C, G), amb: (G,) fracción de ambiente
    (suma 1 sobre el transcriptoma). Devuelve (pesos (C,), w_T, T (G,)). Función pura (testeable)."""
    from scipy.optimize import nnls
    A = amb * 1e4
    Pst = np.clip((perfiles - rho * A) / (1 - rho), 0, None)
    y = pool - rho * A
    w, _ = nnls(Pst[:, marcadores_idx].T, y[marcadores_idx])
    wT = 1 - rho - w.sum()
    T = (y - w @ Pst) / wT if wT > 1e-6 else np.full(len(pool), np.nan)
    return w, wT, T, Pst
