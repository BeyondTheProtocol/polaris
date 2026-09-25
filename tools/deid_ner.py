#!/usr/bin/env python3
"""tools/deid_ner.py — Capa NER de la de-identificación: modelo del BSC entrenado con historias reales.

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.

POR QUÉ (P4, 25-sep-26). `deid.py` redacta con regex y listas del borde y revalida con los MISMOS
patrones: caza restos, no demuestra anonimato. Esta capa añade un detector DISTINTO, entrenado con
historias clínicas reales en castellano y catalán: `BSC-NLP4BIA/bsc-bio-ehr-es-contacto-anon`
(Lima-López et al., Sci Data 2025;12:1088, doi:10.1038/s41597-025-05320-1; recall micro 0,962 en el
test de CONTACTO-I, cotejado en el paper el 25-sep-26).

LO QUE NO HACE (cotejado en su config.json): sus 18 etiquetas NO incluyen el nombre del paciente ni
el email. En CONTACTO-I esas entidades ya venían tapadas, así que ni se entrenaron ni se midieron. En
prueba etiquetó un nombre de paciente como SEXO y los apellidos como personal sanitario: tapa el
tramo, pero por casualidad. El nombre de {{TITULAR}} lo garantizan el regex y el diccionario, no esto.

EGRESS 0. Corre en `.venv-deid` (torch + transformers, fuera del Python del sistema) con
HF_HUB_OFFLINE: el modelo ya está en disco, revisión fijada y sha256 comprobado. Si faltara, falla;
nunca descarga en caliente con un texto clínico esperando.

Uso (lo llama deid.py por subproceso; a mano, solo con texto inventado):
  echo "texto" | .venv-deid/bin/python tools/deid_ner.py      # → JSON [{ini, fin, etiqueta, score}]
  echo '["t1","t2"]' | .venv-deid/bin/python tools/deid_ner.py --lote   # → una lista por texto
"""
import json
import os
import sys

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

MODELO = "BSC-NLP4BIA/bsc-bio-ehr-es-contacto-anon"
REVISION = "83db1112c37c7ef527a9ba6d6b4d1be18b4bca9b"
SHA256_PESOS = "bce8c79ede4559c7f75b9e505824f9700ac15ddbc5929434be768a4ae621a995"

# Umbral bajo a propósito: aquí un falso positivo tapa una palabra de más y un falso negativo deja
# salir un identificador. Se ajusta con el banco de medida, no a ojo.
UMBRAL = 0.30


def _pipeline():
    from transformers import pipeline
    from transformers.utils import logging as tlog
    tlog.disable_progress_bar()
    return pipeline("token-classification", model=MODELO, revision=REVISION,
                    aggregation_strategy="simple", stride=64)


def spans(texto, ner=None):
    """[(ini, fin, etiqueta, score)] sobre `texto` tal cual (offsets de carácter)."""
    if not texto.strip():
        return []
    ner = ner or _pipeline()
    out = []
    for e in ner(texto):
        s = float(e["score"])
        if s >= UMBRAL and e.get("start") is not None:
            out.append((int(e["start"]), int(e["end"]), e["entity_group"], round(s, 3)))
    return out


def main():
    if "--lote" in sys.argv:
        textos = json.load(sys.stdin)
        ner = _pipeline()
        json.dump([[{"ini": a, "fin": b, "etiqueta": et, "score": s} for a, b, et, s in spans(t, ner)]
                   for t in textos], sys.stdout, ensure_ascii=False)
        return 0
    texto = sys.stdin.read()
    json.dump([{"ini": a, "fin": b, "etiqueta": et, "score": s} for a, b, et, s in spans(texto)],
              sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
