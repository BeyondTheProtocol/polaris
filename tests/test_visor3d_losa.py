#!/usr/bin/env python3
"""`visor3d losa` sobre un FANTOMA sintético: congela el sesgo que tumbó `verificacion` el
24-sep-2026.

  · La losa se RECORTA EN Y. Sin ese recorte, la MIP coronal proyecta el hueso de detrás
    (columna, ~1200 HU) sobre el catéter tenue (~500 HU) que va por delante, y el catéter
    desaparece; el sesgo solo puede ACORTAR el catéter visible. Aquí se dibuja un «catéter»
    de 500 HU delante de la tráquea y una «vértebra» de 1200 HU detrás, en la misma
    columna (x, z): el bloque que devuelve `losa_volumen` NO puede contener la vértebra.
  · La LÍNEA MEDIA es la x de la tráquea, no x=0 (el isocentro del escáner): el fantoma
    lleva la tráquea desplazada 15 mm del centro y `linea_media_x_mm` tiene que darla.
  · La carina NO acepta una «hija» mayor que la madre.
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
    import PIL  # noqa: F401
except ImportError:
    if os.path.exists(VENV) and os.environ.get("_VISOR3D_REEXEC") != "1":
        env = dict(os.environ, _VISOR3D_REEXEC="1")
        sys.exit(subprocess.call([VENV, os.path.abspath(__file__)], env=env))
    if os.environ.get("BTP_PORTABLE"):
        print("SKIP: sin .venv-imagen (modo portátil)")
        sys.exit(77)
    print("ROJO: falta .venv-imagen en casa base; el test de la losa no puede correr")
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


ESP = np.array([1.0, 1.0, 2.0])
NX, NY, NZ = 140, 120, 90
ct = np.full((NX, NY, NZ), -1000.0, np.float32)
xx, yy, zz = np.mgrid[0:NX, 0:NY, 0:NZ].astype(float)
ct[((xx - 70) / 65.0) ** 2 + ((yy - 60) / 55.0) ** 2 <= 1.0] = 40.0
TX, TY = 85, 60                               # tráquea DESPLAZADA del centro (x=70)
traquea = (np.hypot(xx - TX, yy - TY) <= 8.0) & (zz >= 55)
bronq = ((np.hypot(xx - TX - 12, yy - TY) <= 5.5) | (np.hypot(xx - TX + 12, yy - TY) <= 5.5)) & (zz < 55) & (zz >= 35)
ct[traquea | bronq] = -950.0
# vértebra brillante DETRÁS de la tráquea (y bajo), misma x y z que el catéter
ct[(np.hypot(xx - 100, yy - 18) <= 12.0) & (zz >= 20) & (zz <= 80)] = 1200.0
# portal metálico anterior, lado izquierdo del paciente (x bajo)
ct[30:41, 100:109, 60:67] = 3100.0
# «catéter» tenue por DELANTE de la tráquea: de (35,104,66) baja oblicuo hasta (100,80,40)
POLI = np.array([[35, 104, 66], [60, 96, 70], [100, 80, 40]], float)
mm = np.stack([xx * ESP[0], yy * ESP[1], zz * ESP[2]], -1)
cat = np.zeros(ct.shape, bool)
for a, b in zip(POLI[:-1], POLI[1:]):
    a_mm, b_mm = a * ESP, b * ESP
    ab = b_mm - a_mm
    t = np.clip(((mm - a_mm) @ ab) / (ab @ ab), 0, 1)
    cat |= np.linalg.norm(mm - (a_mm + t[..., None] * ab), axis=-1) <= 1.4
cat &= ct < 3000
ct[cat] = 500.0
afin = np.diag([ESP[0], ESP[1], ESP[2], 1.0])

res, lam, sub = V.losa_volumen(ct, ESP, afin, guardar=False) if "guardar" in V.losa_volumen.__code__.co_varnames \
    else V.losa_volumen(ct, ESP, afin)
check("error" not in res, "losa sale (%s)" % res.get("error", "ok"))
check(lam is not None and sub is not None, "devuelve lámina y bloque")
if sub is not None:
    y0, y1 = res["caja_indices"]["y"]
    check(y0 > 30, "la losa empieza por delante de la vértebra (y0=%d > 30)" % y0)
    check(float(sub.max()) < 1000.0 or bool((sub > 3000).any()),
          "en el bloque no hay hueso de 1200 HU (máx sin metal = %.0f)" % float(np.where(sub > 3000, 0, sub).max()))
    # en la columna (x, z) de la punta del catéter, la MIP coronal ve ~500 HU, no 1200
    x0, _ = res["caja_indices"]["x"]
    z0, _ = res["caja_indices"]["z"]
    i, k = int(POLI[-1][0]) - x0, int(POLI[-1][2]) - z0
    mip = sub.max(axis=1)
    v = float(mip[i, k]) if 0 <= i < mip.shape[0] and 0 <= k < mip.shape[1] else -1
    check(400.0 <= v <= 700.0, "MIP coronal en la punta del catéter = %.0f HU (esperado ~500, no 1200)" % v)
check(abs(res.get("linea_media_x_mm", -99) - TX * ESP[0]) <= 2.0,
      "línea media = x de la tráquea (%s mm; esperado %.0f, NO el centro 70)" % (res.get("linea_media_x_mm"), TX * ESP[0]))
check(res.get("carina_detalle", {}).get("fiable") is True, "carina fiable en el fantoma: %s" % res.get("carina_detalle"))

# carina: una «hija» mayor que la madre no es carina
ct2 = ct.copy()
ct2[traquea | bronq] = 40.0
tr2 = (np.hypot(xx - TX, yy - TY) <= 8.0) & (zz >= 55)
gorda = (np.hypot(xx - TX - 14, yy - TY) <= 11.0) & (zz < 55) & (zz >= 45)      # 380 mm² > 200 mm²
fina = (np.hypot(xx - TX + 12, yy - TY) <= 5.5) & (zz < 55) & (zz >= 45)
ct2[tr2 | gorda | fina] = -950.0
car2 = V.carina(ct2, ESP)
check(car2 is None or car2[0] != 55, "una hija de 380 mm² con madre de 200 no se acepta como carina en z=55 (sale %s)"
      % (None if car2 is None else car2[0]))

if fallos:
    print("❌ %d fallo(s): %s" % (len(fallos), "; ".join(fallos)))
    sys.exit(1)
print("✅ visor3d losa: fantoma OK")
