#!/usr/bin/env python3
"""Ficha por lesión de tools/visor3d.py con datos SINTÉTICOS (nada de la paciente).

Lo que se congela: (1) la HU de una lesión es la de su NÚCLEO, no la del borde mezclado con el
parénquima; (2) una lesión con densidad de líquido se marca; (3) una lesión sin pareja en el TC
previo se llama «no vista en el TC previo», nunca «nueva» (el modelo pudo no verla).
"""
import os
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"),
                    ".venv-imagen", "bin", "python")
try:
    import numpy as np
    import scipy  # noqa: F401
except ImportError:
    if os.path.exists(VENV) and os.environ.get("_VISOR3D_REEXEC") != "1":
        sys.exit(subprocess.call([VENV, os.path.abspath(__file__)],
                                 env=dict(os.environ, _VISOR3D_REEXEC="1")))
    if os.environ.get("BTP_PORTABLE"):
        print("SKIP: sin .venv-imagen (modo portátil)")
        sys.exit(77)
    print("ROJO: falta .venv-imagen en casa base")
    sys.exit(1)

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("visor3d", os.path.join(RAIZ, "tools", "visor3d.py"))
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)
fallos = []


def ok(cond, msg):
    if not cond:
        fallos.append(msg)


# Hígado de 40³ a 100 HU; L1 sólida (cubo 8³ a 50 HU); L2 líquida (cubo 8³ a 5 HU);
# L3 diminuta (2³ a 40 HU): la erosión la vacía y debe marcarse como contaminada.
ct = np.full((40, 40, 40), 100.0)
higado = np.ones_like(ct, bool)
ids = np.zeros(ct.shape, np.uint8)
ct[4:12, 4:12, 4:12] = 50;  ids[4:12, 4:12, 4:12] = 1
ct[20:28, 20:28, 20:28] = 5; ids[20:28, 20:28, 20:28] = 2
ct[33:35, 33:35, 5:7] = 40;  ids[33:35, 33:35, 5:7] = 3
# Toda la corteza de L1 contaminada a 100 HU (volumen parcial): 296 de 512 vóxeles, así que
# la mediana de la lesión ENTERA sería 100; la del núcleo tiene que seguir dando 50.
ct[4:12, 4:12, 4:12] = 100
ct[5:11, 5:11, 5:11] = 50
d, par = V.densidad_lesiones(ct, ids, higado)
ok(par == 100, "parénquima debería ser 100, es %s" % par)
ok(d[1]["hu_mediana"] == 50, "L1: la HU es la del núcleo (50), no la del borde: %s" % d[1])
ok(not d[1]["densidad_liquido"] and d[2]["densidad_liquido"], "líquido mal marcado: %s" % d)
ok(d[2]["contraste_vs_higado_hu"] == -95, "contraste L2: %s" % d[2])
ok(d[3]["borde_contamina"] is True, "L3 diminuta debería marcarse contaminada: %s" % d[3])

pares = [{"antes": 1, "despues": 1, "distancia_mm": 3.0, "delta_diametro_pct": 25.0,
          "delta_volumen_pct": 90.0},
         {"antes": 2, "despues": 2, "distancia_mm": 2.0, "delta_diametro_pct": 5.0,
          "delta_volumen_pct": 10.0},
         {"antes": None, "despues": 3}]
e = V.evolucion(pares, [{"id": 1}, {"id": 2}, {"id": 3}])
ok(e[1]["estado"] == "crece" and e[2]["estado"] == "estable", "evolución: %s" % e)
ok(e[3]["estado"] == "no vista en el TC previo", "sin pareja NO es «nueva»: %s" % e[3])
ok(all("nueva" not in v["estado"] for v in e.values()), "la palabra «nueva» no debe salir")
ok(V._estado_pet(None, {"umbral_percist": 3}) == "no evaluable", "PET sin dato → no evaluable")
ok(V._estado_pet(3.5, {"umbral_percist": 3.2, "suvmean": 1.7, "suvsd": .3})
   == "sobre umbral tipo PERCIST", "estado PET: no se promete PERCIST de verdad")

# Vasos excluidos de la referencia: si no, un vaso a 200 HU sube el «parénquima».
ct2 = ct.copy()
vasos = np.zeros_like(higado)
ct2[:, 30:40, 30:40] = 200
vasos[:, 30:40, 30:40] = True
_, par_sin = V.densidad_lesiones(ct2, ids, higado, excluir=vasos)
ok(par_sin == 100, "vasos deben salir de la referencia: %s" % par_sin)

fs = V.focos_sin_lesion([{"organo": "liver", "distancia_mm": 18.3, "suvmax": 3.6},
                         {"organo": "liver", "distancia_mm": 5.7, "suvmax": 4.5},
                         {"organo": "kidney_right", "distancia_mm": 40, "suvmax": 35}])
ok([f["suvmax"] for f in fs] == [3.6], "foco hepático lejos de toda lesión debe listarse: %s" % fs)

if fallos:
    print("FALLA test_visor3d_ficha:\n  " + "\n  ".join(fallos))
    sys.exit(1)
print("OK test_visor3d_ficha")
