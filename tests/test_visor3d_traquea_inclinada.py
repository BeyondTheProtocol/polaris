#!/usr/bin/env python3
"""La tráquea de la escena del reservorio se encuentra aunque baje INCLINADA. Datos sintéticos.

El 8-oct-2026 la escena del 24-mar salió sin tráquea: se buscaba el aire a menos de 12 mm del
(x, y) de la carina, pero en un corte 20 mm por encima del portal, a 8-10 cm de ella. Una
tráquea que baja hacia atrás ya no está ahí. Ahora se siembra junto a la carina.

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
    from scipy import ndimage
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


ESP = np.array([1.0, 1.0, 1.5])
xx, yy, zz = np.mgrid[0:120, 0:120, 0:90].astype(float)
CAR = (60.0, 40.0, 10.0)                       # carina abajo y atrás
# tráquea: 8 mm de radio, sube desde la carina yéndose 30 mm hacia delante en 100 mm de alto
cy = CAR[1] + (zz - CAR[2]) * ESP[2] * 0.30
traquea = (np.hypot(xx - CAR[0], yy - cy) <= 8.0) & (zz >= CAR[2])
pulmon = (np.hypot(xx - 100.0, yy - 50.0) <= 14.0)          # otra bolsa de aire, más grande
lab, n = ndimage.label(traquea | pulmon)
k_traquea = int(lab[60, int(CAR[1] + 3), int(CAR[2] + 4)])
k_pulmon = int(lab[100, 50, 40])
ZS = 80                                        # 105 mm por encima de la carina
REF = (CAR[0], CAR[1], ZS)                     # la referencia antigua: (x, y) de la carina, corte alto

check(n == 2 and k_traquea != k_pulmon, "el fantoma tiene tráquea y pulmón como componentes distintas")
check(abs(float(cy[0, 0, ZS]) - CAR[1]) > 20.0, "en el corte alto la tráquea se ha ido más de 20 mm de la referencia")
check(V._componente_traquea(lab, ESP, REF, None) == 0,
      "sin carina, la referencia antigua sola NO la encuentra (el fallo del 24-mar)")
check(V._componente_traquea(lab, ESP, REF, CAR) == k_traquea, "con la carina, la encuentra")
check(V._componente_traquea(lab, ESP, (60.0, float(cy[0, 0, ZS]), ZS), None) == k_traquea,
      "sin carina pero con una referencia bien puesta, sigue valiendo el camino antiguo")
check(V._componente_traquea(lab, ESP, REF, (100.0, 50.0, 30.0)) == k_pulmon,
      "siembra donde se le dice: gana la componente con más vóxeles junto al punto")
check(V._componente_traquea(np.zeros_like(lab), ESP, REF, CAR) == 0, "sin aire, 0")
check(V._componente_traquea(lab, ESP, (CAR[0], float(cy[0, 0, ZS]), ZS), (60.0, 40.0, 200.0)) == k_traquea,
      "una carina fuera del volumen no rompe: cae al camino antiguo")

print("\n%s" % ("TODO VERDE" if not fallos else "ROJO: %d fallo(s)" % len(fallos)))
sys.exit(1 if fallos else 0)
