"""
muestras.py — UNA sola regla para decidir cuál es la muestra TUMORAL de un VCF.

Nace el 22-sep-26 (auditoría externa, hallazgo 2.2): sin cabecera `##tumor_sample`,
preparar_reales tomaba la ÚLTIMA muestra y leer_entradas la PRIMERA. Dos reglas
incompatibles sobre el mismo fichero, y el orden de columnas decidiendo la biología
(el VAF de la normal, ~0, presentado como VAF del tumor).

La regla, fail-closed:
  1. Si quien llama la nombra (--muestra-tumor), manda; tiene que existir en el VCF.
  2. Si hay `##tumor_sample=X`, X tiene que existir en el VCF. Si no existe, se PARA:
     una cabecera que apunta a una muestra que no está es un fichero incoherente.
  3. Si hay una sola muestra, es esa.
  4. Si hay varias y nada las distingue, se PARA. Nunca se adivina por posición.

Devuelve también el MOTIVO de la elección, para dejarlo escrito en el manifiesto.
"""
from __future__ import annotations


class MuestraAmbigua(RuntimeError):
    """No se puede saber con certeza cuál es la muestra tumoral. Se aborta a propósito."""


def _cabecera(header, clave: str) -> str | None:
    valor = None
    for rec in header.records:
        if rec.key == clave:
            valor = rec.value
    return valor


def muestra_tumoral(header, explicita: str | None = None) -> tuple[str | None, str]:
    """(muestra, motivo). `header` es un pysam.VariantHeader."""
    muestras = list(header.samples)
    normal = _cabecera(header, "normal_sample")

    if explicita:
        if explicita not in muestras:
            raise MuestraAmbigua(
                f"La muestra tumoral indicada ({explicita!r}) no existe en el VCF. "
                f"Muestras: {muestras}")
        if normal and explicita == normal:
            raise MuestraAmbigua(
                f"La muestra indicada como tumor ({explicita!r}) es la que la cabecera "
                "marca como ##normal_sample.")
        return explicita, "indicada por quien corre el pipeline (--muestra-tumor)"

    tumor = _cabecera(header, "tumor_sample")
    if tumor is not None:
        if tumor not in muestras:
            raise MuestraAmbigua(
                f"La cabecera dice ##tumor_sample={tumor} pero esa muestra no está en el "
                f"VCF (muestras: {muestras}). Fichero incoherente: no se sigue.")
        if normal and tumor == normal:
            raise MuestraAmbigua(
                f"##tumor_sample y ##normal_sample son la misma muestra ({tumor!r}).")
        return tumor, "cabecera ##tumor_sample"

    if not muestras:
        return None, "el VCF no trae columnas de muestra (sin VAF)"
    if len(muestras) == 1:
        return muestras[0], "única muestra del VCF"
    raise MuestraAmbigua(
        f"El VCF trae {len(muestras)} muestras ({muestras}) y ninguna cabecera "
        "##tumor_sample dice cuál es el tumor. NO se adivina por el orden de las columnas "
        "(en Mutect2 la primera suele ser la normal, pero no siempre). Indícala con "
        "--muestra-tumor <nombre> o añade ##tumor_sample al VCF.")
