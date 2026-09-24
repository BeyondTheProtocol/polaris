#!/usr/bin/env python3
"""`visor3d cateter` sobre un FANTOMA sintético (nada de la paciente).

Qué se comprueba, con geometría conocida de antemano:
  · la carina se encuentra en el corte donde la tráquea de mentira se parte en dos;
  · el catéter (tubo de 2,8 mm y 900 HU) se sigue desde el portal metálico hasta la punta,
    sin llevarse la barra de hueso que le pasa al lado ni el propio portal;
  · la punta cae donde se dibujó (±2,5 mm) y la distancia a la carina sale en mm con signo;
  · la longitud del trayecto se acerca a la polilínea dibujada;
  · muro: con la raíz de salida fuera de zona clínica NO se escribe nada (fail-closed).

Necesita numpy/scipy/scikit-image de `.venv-imagen`: se relanza con ella si hace falta.
"""
import os
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"),
                    ".venv-imagen", "bin", "python")

try:
    import numpy as np
    import scipy  # noqa: F401
    import skimage  # noqa: F401
except ImportError:
    if os.path.exists(VENV) and os.environ.get("_VISOR3D_REEXEC") != "1":
        env = dict(os.environ, _VISOR3D_REEXEC="1")
        sys.exit(subprocess.call([VENV, os.path.abspath(__file__)], env=env))
    if os.environ.get("BTP_PORTABLE"):
        print("SKIP: sin .venv-imagen (modo portátil)")
        sys.exit(77)
    print("ROJO: falta .venv-imagen en casa base; el test del catéter no puede correr")
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


# ─── fantoma: 1×1×2 mm, RAS = índice × espaciado ─────────────────────────────────────────
ESP = np.array([1.0, 1.0, 2.0])
NX, NY, NZ = 120, 100, 80
ct = np.full((NX, NY, NZ), -1000.0, np.float32)
xx, yy, zz = np.mgrid[0:NX, 0:NY, 0:NZ].astype(float)
cuerpo = ((xx - 60) / 55.0) ** 2 + ((yy - 50) / 45.0) ** 2 <= 1.0
ct[cuerpo] = 40.0
# tráquea: un tubo hasta z=50 incluido, dos bronquios por debajo
traquea = (np.hypot(xx - 60, yy - 55) <= 8.0) & (zz >= 50)
bronq = ((np.hypot(xx - 48, yy - 55) <= 6.0) | (np.hypot(xx - 72, yy - 55) <= 6.0)) & (zz < 50) & (zz >= 30)
ct[traquea | bronq] = -950.0
Z_CARINA = 50
# barra de hueso (clavícula de mentira) que cruza a ~10 mm del trayecto, sin tocarlo
hueso = (np.hypot(yy - 62, (zz - 68) * ESP[2]) <= 5.0) & (xx >= 20) & (xx <= 70)
ct[hueso] = 1200.0
# portal: cubo de titanio en la mitad anterior, lado izquierdo del paciente (x bajo en RAS)
ct[25:36, 80:89, 40:47] = 3100.0
# catéter: polilínea de radio 1,4 mm y 900 HU, del portal a la punta
POLI = np.array([[30, 84, 47], [40, 84, 60], [55, 70, 62], [64, 60, 55], [66, 58, 35]], float)
mm = np.stack([xx * ESP[0], yy * ESP[1], zz * ESP[2]], -1)
cat = np.zeros(ct.shape, bool)
for a, b in zip(POLI[:-1], POLI[1:]):
    a_mm, b_mm = a * ESP, b * ESP
    ab = b_mm - a_mm
    t = np.clip(((mm - a_mm) @ ab) / (ab @ ab), 0, 1)
    d = np.linalg.norm(mm - (a_mm + t[..., None] * ab), axis=-1)
    cat |= d <= 1.4
cat &= ct < 3000            # no pisa el portal
ct[cat] = 900.0
LONG_POLI = float(np.linalg.norm(np.diff(POLI * ESP, axis=0), axis=1).sum())
PUNTA_MM = POLI[-1] * ESP
afin = np.diag([ESP[0], ESP[1], ESP[2], 1.0])

# 1) carina
car = V.carina(ct, ESP)
check(car is not None, "carina encontrada")
if car is not None:
    check(car[0] == Z_CARINA, "carina en el corte %d (esperado %d)" % (car[0], Z_CARINA))
    check(abs(car[1] - 60) <= 1.0, "carina centrada en x (%.1f)" % car[1])

# 2) catéter completo sin salir del disco
res = V.cateter_volumen(ct, ESP, afin, guardar=False)
check("error" not in res, "portal detectado (%s)" % res.get("error", "ok"))
m = res.get("medidas", {})
check(res.get("n_puntos_camino", 0) > 20, "camino con puntos (%d)" % res.get("n_puntos_camino", 0))
punta = np.array(m.get("punta_mm", [0, 0, 0]))
check(np.linalg.norm(punta - PUNTA_MM) <= 2.5,
      "punta en %s (dibujada en %s, error %.1f mm)" % (punta.tolist(), PUNTA_MM.tolist(),
                                                       np.linalg.norm(punta - PUNTA_MM)))
check(m.get("punta_vs_carina_mm") is not None
      and abs(m["punta_vs_carina_mm"] - (35 - Z_CARINA) * ESP[2]) <= 2.5,
      "punta vs carina %s mm (esperado %.0f)" % (m.get("punta_vs_carina_mm"), (35 - Z_CARINA) * ESP[2]))
check(m.get("punta_vs_linea_media_mm") is not None and abs(m["punta_vs_linea_media_mm"] - 6.0) <= 2.0,
      "punta vs línea media %s mm (esperado +6)" % m.get("punta_vs_linea_media_mm"))
L = m.get("longitud_cateter_mm", 0)
check(abs(L - LONG_POLI) <= 0.15 * LONG_POLI,
      "longitud %.0f mm (polilínea %.0f mm)" % (L, LONG_POLI))
check(2.0 <= m.get("hueso_mas_cerca_mm", 0) <= 20.0,
      "hueso más cerca a %s mm: el trayecto no se lleva la barra" % m.get("hueso_mas_cerca_mm"))
check(res.get("volumen_cateter_mm3", 1e9) < 1500.0,
      "volumen del catéter %s mm3 (< 1500: sin hueso ni portal dentro)" % res.get("volumen_cateter_mm3"))
check(m.get("doblez_max_grados", 0) < 90.0,
      "doblez máximo %s° (< 90 en una polilínea suave)" % m.get("doblez_max_grados"))

# 3) muro: la raíz de salida fuera de zona clínica → no se escribe nada
with tempfile.TemporaryDirectory() as tmp:
    V.SALIDA_RAIZ = tmp
    try:
        V.cateter_volumen(ct, ESP, afin, guardar=True)
        check(False, "escribir fuera de zona clínica aborta")
    except SystemExit:
        check(True, "escribir fuera de zona clínica aborta")
    check(not os.listdir(tmp), "y no deja ningún fichero fuera")

# 4) sin metal → error explícito, no excepción
sin = ct.copy()
sin[sin > 3000] = 40.0
r2 = V.cateter_volumen(sin, ESP, afin, guardar=False)
check("error" in r2, "sin portal: %s" % r2.get("error"))

if fallos:
    print("❌ %d fallo(s): %s" % (len(fallos), "; ".join(fallos)))
    sys.exit(1)
print("✅ visor3d cateter: fantoma OK")
