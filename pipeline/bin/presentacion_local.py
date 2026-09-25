"""
presentacion_local.py — Etapa D (presentación pMHC) en LOCAL, egress CERO.

Motor: MHCflurry 2.x (Class1PresentationPredictor), open-source, corre 100 % en
local. Péptidos y alelos HLA NUNCA salen de esta máquina.

En PRODUCCIÓN, este paso lo orquesta pVACtools (Docker) con NetMHCpan como
predictor de referencia (ver docs/PIEZAS-GATED.md). MHCflurry es el motor abierto
que deja el pipeline FUNCIONANDO y verificable sin licencia académica. Cuando
NetMHCpan/pVACtools estén disponibles, se enchufan como predictor adicional y se
hace consenso; la INTERFAZ de esta función no cambia.
"""
from __future__ import annotations
from typing import Iterable
import pandas as pd

_PREDICTOR = None


def _cargar():
    global _PREDICTOR
    if _PREDICTOR is None:
        from mhcflurry import Class1PresentationPredictor  # import perezoso (tarda)
        _PREDICTOR = Class1PresentationPredictor.load()
    return _PREDICTOR


def predecir_presentacion(
    peptidos: Iterable[str],
    alelos_clase1: Iterable[str],
    umbral_rank: float = 2.0,
) -> pd.DataFrame:
    """
    Devuelve un DataFrame con la presentación predicha por péptido sobre los
    alelos clase I dados (ya filtrados de los perdidos por LOH).

    Columnas: peptide, best_allele, presentation_score, affinity, processing_score,
    presentation_percentile, presentado (bool según umbral de percentil).
    """
    peptidos = [p.strip().upper() for p in peptidos if p and p.strip()]
    alelos = [a.strip() for a in alelos_clase1 if a and a.strip()]
    if not peptidos or not alelos:
        return pd.DataFrame()

    pred = _cargar()
    df = pred.predict(peptides=peptidos, alleles=alelos, verbose=0)

    # presentation_percentile: cuanto MÁS BAJO, mejor (más presentable).
    col_pct = "presentation_percentile"
    if col_pct in df.columns:
        df["presentado"] = df[col_pct] <= umbral_rank
    else:
        # fallback por score si el percentil no está disponible
        df["presentado"] = df["presentation_score"] >= 0.5

    cols = [c for c in [
        "peptide", "best_allele", "presentation_score", "affinity",
        "processing_score", col_pct, "presentado",
    ] if c in df.columns]
    return df[cols].sort_values("presentation_score", ascending=False).reset_index(drop=True)
