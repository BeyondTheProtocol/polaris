#!/usr/bin/env python3
"""El catéter que va a la web es un tubo continuo, sin huecos ni esquirlas. Datos sintéticos.

El 8-oct-2026 el tramo interpolado se veía como trozos sueltos: se recortaba la malla de
marching cubes por bandas y las caras que cruzaban el corte se tiraban. Ahora se barre un tubo
limpio y el tramo interpolado sale en dos piezas alternas que casan cara con cara.

Necesita numpy/scipy de `.venv-imagen`: se relanza con ella si hace falta.
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
        sys.exit(subprocess.call([VENV, os.path.abspath(__file__)], env=dict(os.environ, _VISOR3D_REEXEC="1")))
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


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


# un «catéter» de mentira: nube de puntos sobre un tubo de 1,4 mm alrededor de un arco de 150 mm
rng = np.random.default_rng(0)
LARGO, R = 150.0, 60.0
arco = rng.uniform(0.0, LARGO, 6000)
fi = arco / R
eje = np.stack([R * np.sin(fi), R * (1 - np.cos(fi)), 0.2 * arco], axis=1)
ruido = rng.normal(size=(6000, 3))
ruido = 1.4 * ruido / np.linalg.norm(ruido, axis=1, keepdims=True)
nube = eje + ruido
piezas = V._tubo_a_rayas(nube, arco, LARGO)

check(set(piezas) == {"medido", "interpolado", "interpolado_b"}, "salen las tres piezas")
todos_v = np.vstack([p[0] for p in piezas.values()])
caras_tot = sum(len(p[1]) for p in piezas.values())
for nombre, (pv, pf) in piezas.items():
    check(pf.min() == 0 and pf.max() == len(pv) - 1, "%s: índices compactos y válidos" % nombre)

# malla cerrada: uniendo las piezas, cada arista la comparten exactamente dos caras
clave = {}
tri = []
for pv, pf in piezas.values():
    for cara in pf:
        tri.append([clave.setdefault(tuple(np.round(pv[i], 4)), len(clave)) for i in cara])
tri = np.asarray(tri)
ar = np.sort(np.vstack([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]]), axis=1)
_, veces = np.unique(ar, axis=0, return_counts=True)
check((veces == 2).all(), "el tubo entero es cerrado: ninguna arista suelta, ningún hueco (%d caras)" % caras_tot)

# el tubo sigue el eje verdadero y tiene el radio nominal
def dist_al_eje(p):
    s = np.linspace(0, LARGO, 3001)
    e = np.stack([R * np.sin(s / R), R * (1 - np.cos(s / R)), 0.2 * s], axis=1)
    return np.min(np.linalg.norm(p[:, None, :] - e[None, ::10, :], axis=2), axis=1)

d = dist_al_eje(piezas["interpolado"][0])
check(abs(float(np.median(d)) - V.RADIO_CATETER_WEB_MM) < 0.35, "radio del tubo ≈ %.2f mm (mediana %.2f)" % (V.RADIO_CATETER_WEB_MM, float(np.median(d))))

# medido solo en los dos extremos; lo interpolado, nunca en ellos
def arco_de(p):
    s = np.linspace(0, LARGO, 3001)
    e = np.stack([R * np.sin(s / R), R * (1 - np.cos(s / R)), 0.2 * s], axis=1)
    return s[np.argmin(np.linalg.norm(p[:, None, :] - e[None, :, :], axis=2), axis=1)]

am = arco_de(piezas["medido"][0])
check(((am < V.MEDIDO_MM + 3) | (am > LARGO - V.MEDIDO_MM - 3)).all() and (am < 20).any() and (am > LARGO - 20).any(),
      "«medido» cubre los dos extremos y nada del medio")
ai = arco_de(np.vstack([piezas["interpolado"][0], piezas["interpolado_b"][0]]))
check((ai > V.MEDIDO_MM - 3).all() and (ai < LARGO - V.MEDIDO_MM + 3).all(), "lo interpolado no toca los extremos")
area = lambda p: float((np.linalg.norm(np.cross(p[0][p[1][:, 1]] - p[0][p[1][:, 0]], p[0][p[1][:, 2]] - p[0][p[1][:, 0]]), axis=1) / 2).sum())  # noqa: E731
prop = area(piezas["interpolado"]) / (area(piezas["interpolado"]) + area(piezas["interpolado_b"]))
esperado = V.DASH_ON_MM / (V.DASH_ON_MM + V.DASH_OFF_MM)
check(abs(prop - esperado) < 0.06, "las rayas guardan la proporción %.0f/%.0f mm (%.2f frente a %.2f)" % (V.DASH_ON_MM, V.DASH_OFF_MM, prop, esperado))

try:
    V._tubo_a_rayas(nube[:5], arco[:5] * 0 + 1.0, LARGO)
    check(False, "un catéter sin eje debería abortar")
except SystemExit as e:
    check("no da para sacar un eje" in str(e), "un catéter demasiado corto aborta")

print("\n%s" % ("TODO VERDE" if not fallos else "ROJO: %d fallo(s)" % len(fallos)))
sys.exit(1 if fallos else 0)
