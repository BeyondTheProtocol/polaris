#!/usr/bin/env python3
"""El recorte a la caja de la máscara en `_malla` (tools/visor3d.py). Datos SINTÉTICOS.

Qué fija, y por qué existe: el 20-sep-26 `visor3d.py --organo mama tumor` pidió 16 GB en un
Mac mini de 16 y dejó la casa base paginando a muerte hasta que hubo que reiniciarla. La causa
no era el tumor: `_malla` suavizaba y sobremuestreaba ×2 el VOLUMEN ENTERO del estudio para
dibujar una lesión que ocupa una millonésima de él (`zoom(order=3)` multiplica por 8 el número
de vóxeles y además se guarda una copia del spline).

El arreglo recorta a la caja de la máscara. Este test sostiene las dos mitades del claim:
  1. la malla sale IDÉNTICA a la del volumen completo (si no, el recorte estaría mintiendo
     sobre la geometría de un tumor, que es justo lo que no se puede hacer aquí);
  2. el pico de memoria se queda en megas, no en gigas.

Necesita numpy/scikit-image, que viven en `.venv-imagen`, no en el /usr/bin/python3 de
test_all.sh: si no están, se relanza con la venv. Sin venv: skip (rc 77) solo en modo portátil;
en casa base es ROJO, igual que test_visor3d.py.
"""
import os
import resource
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"),
                    ".venv-imagen", "bin", "python")

try:
    import numpy as np
    import skimage  # noqa: F401
    from scipy import ndimage
except ImportError:
    if os.path.exists(VENV) and os.environ.get("_RECORTE_REEXEC") != "1":
        env = dict(os.environ, _RECORTE_REEXEC="1")
        sys.exit(subprocess.call([VENV, os.path.abspath(__file__)], env=env))
    if os.environ.get("BTP_PORTABLE"):
        print("SKIP: sin .venv-imagen (modo portátil)")
        sys.exit(77)
    print("ROJO: falta .venv-imagen en casa base; el freno de memoria del visor no puede correr")
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


def _malla_sin_recorte(mask, afin, suavizado=1.0, sobremuestreo=1, min_componente=0, taubin=12):
    """El `_malla` de ANTES del arreglo, palabra por palabra: la referencia contra la que se
    compara. Si alguien cambia la receta de suavizado o de marching cubes, este test empieza a
    fallar y hay que actualizar las dos a la vez, que es exactamente lo que se quiere."""
    from skimage.measure import marching_cubes
    if mask.sum() < 4:
        return None
    if min_componente:
        comp, n = ndimage.label(mask)
        if n:
            tam = ndimage.sum(mask, comp, index=np.arange(1, n + 1))
            mask = np.isin(comp, 1 + np.nonzero(tam >= min_componente)[0])
            if not mask.any():
                return None
    campo = ndimage.gaussian_filter(np.pad(mask, 2).astype(np.float32), suavizado)
    if campo.max() <= 0.5:
        return None
    if sobremuestreo > 1:
        campo = ndimage.zoom(campo, sobremuestreo, order=3)
    v, f, _, _ = marching_cubes(campo, 0.5)
    v = V._taubin(v / sobremuestreo - 2, f, iteraciones=taubin)
    v = (afin @ np.c_[v, np.ones(len(v))].T).T[:, :3]
    return v, f


def bola(forma, centro, radio):
    ejes = np.ogrid[tuple(slice(0, n) for n in forma)]
    return sum((e - c) ** 2 for e, c in zip(ejes, centro)) <= radio ** 2


AFIN = np.diag([0.8, 0.8, 1.2, 1.0])        # vóxel anisótropo, como un estudio de verdad

print("== recorte a la caja: la malla no cambia ==")

# Bola descentrada en un volumen amplio: el caso real (una lesión perdida en el estudio).
casos = (
    ("bola simple",           (96, 96, 96), (30, 62, 41), 7,  {"suavizado": 1.0}),
    ("sobremuestreo x2",      (96, 96, 96), (30, 62, 41), 7,  {"suavizado": 0.9,
                                                               "sobremuestreo": 2}),
    ("sigma por eje (tupla)", (96, 96, 96), (48, 48, 48), 9,  {"suavizado": (1.2, 1.2, 1.9),
                                                               "taubin": 30}),
    ("pegada al borde",       (80, 80, 80), (4, 40, 40),  3,  {"suavizado": 0.8}),
    ("sigma grande",          (96, 96, 96), (30, 62, 41), 10, {"suavizado": 3.0}),
)
for nombre, forma, centro, radio, kw in casos:
    mask = bola(forma, centro, radio)
    nuevo = V._malla(mask, AFIN, **kw)
    viejo = _malla_sin_recorte(mask, AFIN, **kw)
    if nuevo is None or viejo is None:
        check(False, "%s: alguna de las dos no devolvió malla (%s / %s)"
              % (nombre, nuevo is None, viejo is None))
        continue
    vn, fn = nuevo
    vv, fv = viejo
    if kw.get("sobremuestreo", 1) > 1:
        # Con sobremuestreo no puede salir bit a bit igual, y es a propósito: el código viejo
        # dividía por `sobremuestreo` cuando la escala real de zoom() es (N-1)/(N'-1), o sea
        # que su malla dependía del tamaño del array. Lo que se exige aquí es que la geometría
        # no se mueva de forma clínicamente visible: sub-vóxel y por goleada.
        # Aquí la malla nueva NO coincide con la vieja, y está bien que no coincida: la vieja
        # dividía por `sobremuestreo` cuando la escala real de zoom() es (N-1)/(N'-1), así que
        # desplazaba la superficie tanto más cuanto más lejos del origen cayera la lesión. Se
        # corrige. Lo que este check fija es que la corrección es sub-milimétrica y que no
        # arrastra ninguna cifra: el diámetro sale de _diametro_axial_mayor(mask) y los
        # volúmenes de mask.sum()*vox_ml, los dos de la MÁSCARA; los .ply solo se renderizan.
        from scipy.spatial import cKDTree
        d = max(float(cKDTree(vv).query(vn)[0].max()), float(cKDTree(vn).query(vv)[0].max()))
        check(d < 1.0, "%s: corrige el desplazamiento del viejo y se queda sub-milimétrico "
                       "(Hausdorff %.3f mm, vóxel de %.1f mm)" % (nombre, d,
                                                                  min(np.diag(AFIN)[:3])))
    else:
        check(fn.shape == fv.shape and np.array_equal(fn, fv),
              "%s: mismas caras (%d)" % (nombre, len(fn)))
        check(vn.shape == vv.shape and np.allclose(vn, vv, atol=1e-6),
              "%s: mismos vértices (%d, desvío máx %.2e)"
              % (nombre, len(vn), np.abs(vn - vv).max() if vn.shape == vv.shape else
                 float("nan")))

print("== invarianza: el mismo objeto en volúmenes de distinto tamaño ==")
# Lo que de verdad hay que garantizar: cuánto volumen vacío sobre alrededor del tumor no puede
# cambiar su malla. Antes del arreglo esto fallaba justo con sobremuestreo, que es el caso de
# la lesión de mama.
CHICO, GRANDE = (64, 64, 64), (220, 190, 170)
CENTRO_C, CENTRO_G = (32, 32, 32), (150, 40, 120)


def _centrada(malla):
    return None if malla is None else (malla[0] - malla[0].mean(axis=0), malla[1])


for sm in (1, 2):
    a = _centrada(V._malla(bola(CHICO, CENTRO_C, 8), AFIN, 0.9, sobremuestreo=sm))
    b = _centrada(V._malla(bola(GRANDE, CENTRO_G, 8), AFIN, 0.9, sobremuestreo=sm))
    check(a is not None and b is not None and np.array_equal(a[1], b[1])
          and np.allclose(a[0], b[0], atol=1e-6),
          "sobremuestreo=%d: misma malla en 64³ que en 220x190x170" % sm)

# Y que quede escrito qué se arregló: con sobremuestreo, el código de antes SÍ dependía del
# tamaño del volumen. Si este check empieza a pasar es que alguien arregló el viejo también,
# y entonces sobra la copia de referencia de este fichero.
va = _centrada(_malla_sin_recorte(bola(CHICO, CENTRO_C, 8), AFIN, 0.9, sobremuestreo=2))
vb = _centrada(_malla_sin_recorte(bola(GRANDE, CENTRO_G, 8), AFIN, 0.9, sobremuestreo=2))
check(not (va[0].shape == vb[0].shape and np.allclose(va[0], vb[0], atol=1e-6)),
      "el código de antes NO era invariante al tamaño (por eso hacía falta la escala exacta)")

# Varios trozos sueltos: la caja tiene que abarcarlos todos, y min_componente debe seguir
# quitando los pequeños igual que antes (opera sobre el recorte, que los contiene enteros).
mask = bola((96, 96, 96), (28, 30, 30), 8) | bola((96, 96, 96), (70, 68, 66), 3)
for mc in (0, 200):
    nuevo, viejo = (V._malla(mask, AFIN, 0.9, min_componente=mc),
                    _malla_sin_recorte(mask, AFIN, 0.9, min_componente=mc))
    check(nuevo is not None and viejo is not None
          and np.array_equal(nuevo[1], viejo[1]) and np.allclose(nuevo[0], viejo[0], atol=1e-6),
          "dos componentes, min_componente=%d: malla idéntica" % mc)

print("== la caja ==")
mask = bola((200, 200, 200), (150, 40, 100), 6)
origen, caja = V._caja_de(mask, margen=7)
recorte = mask[caja]
check(recorte.sum() == mask.sum(), "la caja no se deja ningún vóxel de la máscara dentro")
check(recorte.size < mask.size / 100,
      "la caja es >100x más pequeña que el volumen (%d vs %d vóxeles, %.0fx)"
      % (recorte.size, mask.size, mask.size / float(recorte.size)))
check(tuple(origen) == (137.0, 27.0, 87.0),
      "el origen apunta al vértice de la caja (%s)" % (tuple(origen),))
borde = V._caja_de(bola((80, 80, 80), (2, 40, 40), 3), margen=9)[1]
check(borde[0].start == 0, "una máscara pegada al borde recorta el margen, no se sale del array")

print("== el pico de memoria ==")
# 256³ con sobremuestreo=2: sin recorte son >1 GB (64 MB de campo x8 por el zoom, más la copia
# del spline en float64). Con recorte, la caja de una bola de radio 6 son unos pocos MB.
# ru_maxrss es el máximo histórico del proceso, así que se mide el salto que provoca la llamada.
antes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
grande = bola((256, 256, 256), (190, 60, 130), 6)
r = V._malla(grande, AFIN, 0.8, sobremuestreo=2)
pico_mb = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - antes) / (1024.0 * 1024.0)
check(r is not None, "sale malla del volumen de 256³")
check(pico_mb < 400, "el pico se queda en megas: +%.0f MB (sin el recorte eran >1 GB)" % pico_mb)

print()
if fallos:
    print("❌ %d fallo(s):" % len(fallos))
    for f in fallos:
        print("   -", f)
    sys.exit(1)
print("✅ recorte a la caja: malla idéntica y memoria acotada")
