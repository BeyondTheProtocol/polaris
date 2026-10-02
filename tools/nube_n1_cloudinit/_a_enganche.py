#!/usr/bin/env python3
"""_a_enganche.py — corre EN LA MÁQUINA GPU (dentro del contenedor de CellViT++), no en el Mac.

Convierte el `cells.json` de CellViT++ 1.0.9 al formato del ENGANCHE de `tools/laminillas_he.py`
(`CELLVITPP["salida"]`, commit dc16108): un par por clasificador,
    P-HE.<clasificador>.csv   columnas x_l0, y_l0, clase, prob (centroide en píxeles de L0)
    P-HE.<clasificador>.json  modelo, version, clasificador, lamina, mpp_l0, n, sha256_csv, mapa_clases
`nube_n1.bajar` lo vuelve a validar en el Mac antes de escribir nada en SESION/nube/.

Lo que leo del `cells.json` (código de CellViT++ 1.0.9, `inference/inference.py`, leído el 2-oct-26):
{"wsi_metadata", "type_map": {id: nombre}, "cells": [{"centroid": [x, y], "type": id,
"type_prob": p, …}]}. El centroide es [x, y]: el GeoJSON de CellViT++ lo usa tal cual como
coordenada. Que esté en píxeles de L0 es inferencia (con 0,2506 µm/px y destino 0,25 no reescala);
la mini-puerta de laminillas_he lo comprueba casando con InstanSeg.

FALLA (código 3, y el trabajo escribe FAILED) si una clase no está en el mapa, si el mapa lleva un
destino fuera de la taxonomía común, o si una coordenada o probabilidad no es un número válido.
Solo stdlib: corre con el python del contenedor.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import sys

COLUMNAS = ["x_l0", "y_l0", "clase", "prob"]
COMUN = ("neoplastic", "inflammatory", "connective", "dead", "epithelial", "other")
LAMINA = "P-HE"
RE_CLASIF = re.compile(r"^[A-Za-z0-9_]{1,32}$")


class Invalido(ValueError):
    pass


def convierte(cells, clasificador, mpp, mapa, modelo, version, lamina=LAMINA):
    """(bytes del CSV, dict del JSON)."""
    if not RE_CLASIF.match(clasificador or ""):
        raise Invalido("clasificador no opaco")
    if not isinstance(mapa, dict) or not mapa or any(v not in COMUN for v in mapa.values()):
        raise Invalido("mapa de clases vacío o con destinos fuera de la taxonomía común")
    tipos = {str(k): str(v) for k, v in (cells.get("type_map") or {}).items()}
    filas = []
    for c in cells.get("cells") or []:
        cen = c.get("centroid")
        if not isinstance(cen, (list, tuple)) or len(cen) < 2:
            raise Invalido("célula sin centroide")
        x, y, p = float(cen[0]), float(cen[1]), float(c.get("type_prob"))
        if not all(math.isfinite(v) for v in (x, y, p)) or x < 0 or y < 0 or not 0 <= p <= 1:
            raise Invalido("coordenada o probabilidad no válida")
        clase = tipos.get(str(c.get("type")))
        if clase is None or clase not in mapa:
            raise Invalido("clase sin nombre o sin mapa")
        filas.append(["%.2f" % x, "%.2f" % y, clase, "%.6f" % p])
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNAS)
    w.writerows(filas)
    datos = buf.getvalue().encode("utf-8")
    meta = {"modelo": modelo, "version": version, "clasificador": clasificador, "lamina": lamina,
            "mpp_l0": float(mpp), "n": len(filas), "sha256_csv": hashlib.sha256(datos).hexdigest(),
            "mapa_clases": {str(k): str(v) for k, v in mapa.items()}}
    return datos, meta


def main(argv=None):
    ap = argparse.ArgumentParser(prog="a_enganche.py")
    ap.add_argument("--cells", required=True)
    ap.add_argument("--clasificador", required=True)
    ap.add_argument("--mpp", type=float, required=True)
    ap.add_argument("--mapa", required=True)
    ap.add_argument("--modelo", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--salida", required=True)
    a = ap.parse_args(argv)
    try:
        with open(a.cells, encoding="utf-8") as fh:
            cells = json.load(fh)
        with open(a.mapa, encoding="utf-8") as fh:
            mapa = (json.load(fh) or {}).get(a.clasificador)
        datos, meta = convierte(cells, a.clasificador, a.mpp, mapa, a.modelo, a.version)
    except (OSError, ValueError, TypeError) as e:
        print("enganche: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 3
    os.makedirs(a.salida, exist_ok=True)
    base = os.path.join(a.salida, "%s.%s" % (LAMINA, a.clasificador))
    with open(base + ".csv", "wb") as fh:
        fh.write(datos)
    with open(base + ".json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, sort_keys=True)
    print("enganche: %s · %d núcleos" % (os.path.basename(base), meta["n"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
