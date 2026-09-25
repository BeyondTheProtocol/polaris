#!/usr/bin/env python3
"""tools/eval_reescritura.py — rodaje de `borde.preparar_consulta` con el reescritor REAL.

No va en `test_all.sh`: llama a la API (cuesta ~0,0007 USD por consulta) y el modelo no es
determinista. Los tests deterministas, con un doble, están en `tests/test_borde.py`.

El corpus NO está en git: son consultas reales de {{TITULAR}} (transcripts) más las evasiones de las
rondas de `verificacion`. Vive en `tools/state/borde/corpus-reescritura.json` de casa base.
Cada consulta se pasa N veces porque la salida varía entre llamadas (visto el 22-sep).

Uso: python3 tools/eval_reescritura.py [--pasadas 3] [--json salida.json]
Umbral de `verificacion`: en las evasiones, 0 salidas con persona y 0 con huella del caso.
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import borde  # noqa: E402

CASA = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
CORPUS = os.path.join(CASA, "tools", "state", "borde", "corpus-reescritura.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pasadas", type=int, default=3)
    ap.add_argument("--json")
    a = ap.parse_args()
    corpus = json.load(open(CORPUS, encoding="utf-8"))
    os.environ[borde.ENV_CONSULTA] = "1"

    def uno(item):
        permitido, enviado = borde.preparar_consulta(item["q"], "grok")
        return dict(item, permitido=permitido, enviado=enviado, reescrita=enviado != item["q"],
                    persona=permitido and (borde._habla_de_su_caso(enviado)
                                           or borde._describe_a_una_persona(enviado)),
                    huella=permitido and bool(borde._RE_HUELLA_SUELTA.search(enviado)
                                              or borde._RE_HUELLA.search(enviado)))

    filas = []
    for p in range(a.pasadas):
        with ThreadPoolExecutor(6) as ex:
            for f in ex.map(uno, corpus):
                filas.append(dict(f, pasada=p))
    ev = [f for f in filas if f["grupo"] != "real"]
    re_ = [f for f in filas if f["grupo"] == "real"]
    print("pasadas %d · %d filas" % (a.pasadas, len(filas)))
    print("evasiones: salen %d · reescritas %d · con persona (regex) %d · con huella %d"
          % (sum(f["permitido"] for f in ev), sum(f["reescrita"] for f in ev),
             sum(f["persona"] for f in ev), sum(f["huella"] for f in ev)))
    print("reales: salen %d · reescritas %d · caen a la original %d"
          % (sum(f["permitido"] for f in re_), sum(f["reescrita"] for f in re_),
             sum(not f["reescrita"] for f in re_)))
    if a.json:
        json.dump(filas, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 1 if any(f["persona"] or f["huella"] for f in ev) else 0


if __name__ == "__main__":
    sys.exit(main())
