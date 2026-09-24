#!/usr/bin/env python3
"""Marcas de un radiólogo en el visor (`visor3d.marcas_radiologo`), con datos SINTÉTICOS.

Lo que frena: que una marca sin posición fiable acabe dibujada en el 3D (inventar dónde está),
que dos marcas se queden la misma lesión automática, y que la procedencia o la frase sellada
se pierdan. La esfera tiene que medir el diámetro que midió él.
"""
import json
import os
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"),
                    ".venv-imagen", "bin", "python")
try:
    import numpy as np
except ImportError:
    if os.path.exists(VENV) and os.environ.get("_VISOR3D_REEXEC") != "1":
        sys.exit(subprocess.call([VENV, os.path.abspath(__file__)],
                                 env=dict(os.environ, _VISOR3D_REEXEC="1")))
    print("SKIP: sin numpy")
    sys.exit(77)

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("visor3d", os.path.join(RAIZ, "tools", "visor3d.py"))
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)

fallos = []


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


tmp = tempfile.mkdtemp()
V._dir = lambda *p: os.path.join(tmp, *p)
V._cache = lambda s: tmp
V.exige_zona_clinica = lambda r: None
V._carga = lambda r: (None, np.diag([1.0, 1.0, 2.0, 1.0]))       # vóxel 1×1×2 mm
os.makedirs(os.path.join(tmp, "assets", "f"))
json.dump({"lesiones": [{"id": 1, "diametro_mm": 10.0}, {"id": 2, "diametro_mm": 5.0},
                        {"id": 3, "diametro_mm": 8.0}]},
          open(os.path.join(tmp, "assets", "f", "estudio.json"), "w"))


def m(i, mm, xyz, lp=None):
    x, y, z = xyz if xyz else (None, None, None)
    return {"id": i, "corte": 100, "mm": mm, "x": x, "y": y, "z": z, "segmento": 4,
            "lesion_polaris": lp, "origen": "", "posicion": ""}


rev = {"tc": "sintético", "metodo": "test", "lesiones": [
    m(1, 10.2, (10, 10, 10), 1),
    m(2, 5.1, (40, 40, 40), 2),
    m(3, 9.0, (40, 40, 40), 2),        # misma lesión y mismas coords que la 2: copia
    m(4, 6.0, (70, 70, 70)),           # solo radiólogo, con posición
    m(5, 4.0, (90, 90, 90)),           # gemela de la 6
    m(6, 3.0, (90, 90, 90)),
    m(7, 3.5, None),                   # sin posición
    m(8, 8.1, (5, 5, 5)),              # se empareja a mano con L3
]}
ruta = os.path.join(tmp, "rev.json")
json.dump(rev, open(ruta, "w"))

r = V.marcas_radiologo(ruta, "s", "f", empareja={8: 3}, fuente_emparejado="a ojo")
n = r["recuento"]
check(n == {"total": 8, "polaris_revisadas": 3, "solo_radiologo": 5, "en_3d": 1,
            "sin_posicion": 4}, "recuento 3 + 5 (1 en 3D, 4 sin posición): %s" % n)
check(r["automaticas"]["2"]["marca"] == 2, "L2 se la queda la marca de diámetro más cercano")
check(r["automaticas"]["3"]["marca"] == 8 and r["_emparejado_forzado"]["fuente"] == "a ojo",
      "el emparejado forzado se aplica y deja su fuente")
sin = {s["id"] for s in r["sin_posicion"]}
check(sin == {3, 5, 6, 7}, "copia, gemelas y sin coords quedan FUERA del 3D: %s" % sorted(sin))
check(not any(os.path.exists(os.path.join(tmp, "assets", "f", "marca%02d.ply" % i))
              for i in sin), "ninguna marca sin posición tiene esfera")
check(all(a["origen"] == V.ORIGEN_POLARIS for a in r["automaticas"].values())
      and all(x["origen"] == V.ORIGEN_RADIOLOGO for x in r["marcas"] + r["sin_posicion"]),
      "cada marca lleva su procedencia")
check("sin informe firmado" in r["frase"] and "55 metástasis" not in r["frase"]
      and "M1 múltiples" in r["frase"], "frase sellada, sin «55 metástasis»")
M = r["marcas"][0]
check(M["centro_mm"] == [70.0, 70.0, 140.0], "centro en mm con la afín: %s" % M["centro_mm"])
ply = open(os.path.join(tmp, "assets", "f", M["malla"]), "rb").read()
cab = ply[:ply.index(b"end_header\n") + 11]
nv = int(cab.split(b"element vertex ")[1].split(b"\n")[0])
v = np.frombuffer(ply[len(cab):len(cab) + nv * 12], dtype="<f4").reshape(-1, 3)
d = np.linalg.norm(v - np.array(M["centro_mm"]), axis=1)
check(abs(d.max() - 3.0) < 1e-3 and abs(d.min() - 3.0) < 1e-3,
      "esfera de radio = su diámetro / 2 (%.3f mm)" % d.max())

print("\n%s" % ("VERDE" if not fallos else "ROJO: %d fallos" % len(fallos)))
sys.exit(1 if fallos else 0)
