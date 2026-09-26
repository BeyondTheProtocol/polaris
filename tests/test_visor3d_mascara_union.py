#!/usr/bin/env python3
"""La máscara del hígado del visor es la UNIÓN `total/liver ∪ liver_segments` (tools/visor3d.py,
`_mascara_higado`). Datos SINTÉTICOS.

Por qué existe (deuda `visor3d-mascara-higado-corta-puntas`, 26-sep-26): `total/liver` de
TotalSegmentator recorta la periferia del hígado (la punta del lóbulo izquierdo, 11,8 mm en el
corte 151 del TC del 8-sep), y la marca M17 del radiólogo, que está DENTRO del hígado según dos
revisiones independientes, salía 6,4 mm fuera de la malla pública. `liver_segments` sí cubre la
punta. Lo que fija este test:
  1. la máscara por defecto es la unión, en el código y en la línea de comandos;
  2. con un volumen sintético donde `total` recorta una punta, la punta queda dentro de la
     máscara Y dentro de la MALLA que sale de ella (que es lo que ve la web), y fuera de la
     malla de solo `total` (si esto dejara de fallar, la unión ya no haría falta);
  3. un islote de `liver_segments` que no toca `total` NO entra (no es hígado);
  4. `total` y `segmentos` siguen disponibles como opciones explícitas, y una máscara
     desconocida o la unión sin `liver_segments` abortan en vez de callar;
  5. `_punto_vs_malla` (lo que mide `marcas-dentro`) distingue dentro de fuera y mide la
     distancia a la superficie con error sub-vóxel.

Necesita numpy/scipy/scikit-image (`.venv-imagen`): si faltan se relanza con la venv; sin venv,
skip (77) solo en modo portátil, en casa base ROJO, igual que test_visor3d_malla_recorte.py.
"""
import inspect
import os
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"),
                    ".venv-imagen", "bin", "python")

try:
    import numpy as np
    import skimage  # noqa: F401
    from scipy import ndimage  # noqa: F401
except ImportError:
    if os.path.exists(VENV) and os.environ.get("_UNION_REEXEC") != "1":
        env = dict(os.environ, _UNION_REEXEC="1")
        sys.exit(subprocess.call([VENV, os.path.abspath(__file__)], env=env))
    if os.environ.get("BTP_PORTABLE"):
        print("SKIP: sin .venv-imagen (modo portátil)")
        sys.exit(77)
    print("ROJO: falta .venv-imagen en casa base; la máscara del hígado no se puede probar")
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


def bola(forma, centro, radio):
    ejes = np.ogrid[tuple(slice(0, n) for n in forma)]
    return sum((e - c) ** 2 for e, c in zip(ejes, centro)) <= radio ** 2


# ── el volumen sintético: un "hígado" con una punta que `total` no ve ────────────────────
FORMA = (90, 90, 90)
AFIN = np.diag([1.0, 1.0, 1.0, 1.0])       # 1 mm isótropo: los mm son vóxeles
CUERPO = bola(FORMA, (50, 45, 45), 22)      # lo que los dos modelos ven
PUNTA = np.zeros(FORMA, bool)               # la punta del "lóbulo izquierdo": un cilindro
ejes = np.ogrid[tuple(slice(0, n) for n in FORMA)]
PUNTA[((ejes[1] - 45) ** 2 + (ejes[2] - 45) ** 2 <= 6 ** 2) & (ejes[0] >= 16) & (ejes[0] <= 30)] = True
ISLOTE = bola(FORMA, (80, 80, 80), 4)       # un trozo suelto de `liver_segments`, lejos
TOTAL = CUERPO.copy()                        # `total/liver` recorta la punta
SEGM = np.zeros(FORMA, np.uint8)             # `liver_segments`: etiquetas 1-8, 0 = fuera
SEGM[CUERPO] = 4
SEGM[PUNTA] = 2                              # la punta es segmento II, como en el caso real
SEGM[ISLOTE] = 5
P_PUNTA = np.array([20.0, 45.0, 45.0])       # el "M17": dentro de la punta, a 8 mm del cuerpo

print("== 1. la unión es la máscara por defecto ==")
check(V.MASCARA_HIGADO == "union", "MASCARA_HIGADO == 'union'")
check(V.MASCARAS_HIGADO == ("union", "total", "segmentos"), "las tres opciones existen")
src_les, src_ass = inspect.getsource(V.lesiones), inspect.getsource(V.assets)
check("_mascara_higado(" in src_les and "_mascara_higado(" in src_ass,
      "lesiones() y assets() forman el hígado con _mascara_higado (no con total==liver a pelo)")
check("total == cm[\"liver\"]\n" not in src_les and "higado = total == cm[\"liver\"]" not in src_ass,
      "no queda ninguna asignación directa higado = total == liver")
src_main = inspect.getsource(V.main)
check("--mascara-higado" in src_main and "default=MASCARA_HIGADO" in src_main,
      "la CLI expone --mascara-higado con la unión por defecto")
check("marcas-dentro" in src_main, "la CLI expone marcas-dentro (medir dentro/fuera, no mover)")

print("== 2. la punta queda dentro de la máscara y de la malla ==")
union = V._mascara_higado(TOTAL, SEGM)
check(union.dtype == bool and union.shape == FORMA, "devuelve una máscara booleana en la rejilla")
check(union[tuple(P_PUNTA.astype(int))], "el punto de la punta está DENTRO de la máscara unión")
check(not TOTAL[tuple(P_PUNTA.astype(int))], "…y FUERA de `total` (si no, el test no prueba nada)")
check(bool((union & TOTAL).sum() == TOTAL.sum()), "la unión contiene a `total` entero")
check(bool(union[PUNTA].all()), "la punta entra entera")
esperado = int((CUERPO | PUNTA).sum())
check(int(union.sum()) == esperado, "volumen = cuerpo + punta (%d vóxeles = %d ml a 1 mm)"
      % (esperado, esperado // 1000))

# La malla con la MISMA receta que assets() a 2 mm de vóxel (suavizado 1.2, x1.6 en z, Taubin 30)
receta = dict(suavizado=(1.2, 1.2, 1.2 * 1.6), taubin=30)
malla_union = V._malla(union, AFIN, **receta)
malla_total = V._malla(TOTAL, AFIN, **receta)
check(malla_union is not None and malla_total is not None, "salen las dos mallas")
dentro_u, dist_u = V._punto_vs_malla(P_PUNTA, *malla_union)
dentro_t, dist_t = V._punto_vs_malla(P_PUNTA, *malla_total)
check(dentro_u, "la punta cae DENTRO de la malla unión (superficie a %.1f mm)" % dist_u)
check(not dentro_t and dist_t > 5,
      "la misma punta cae FUERA de la malla de solo `total`, a %.1f mm (el fallo de M17)" % dist_t)

print("== 3. un islote suelto de liver_segments no es hígado ==")
check(not union[80, 80, 80], "el islote que no toca `total` queda fuera de la unión")
check(bool(V._mascara_higado(TOTAL, SEGM, "segmentos")[80, 80, 80]),
      "…pero con --mascara-higado segmentos sí sale (es lo que se pidió, a sabiendas)")

print("== 4. opciones explícitas y abortos ==")
t = V._mascara_higado(TOTAL, SEGM, "total")
check(np.array_equal(t, TOTAL) and t is not TOTAL, "'total' devuelve total/liver tal cual (copia)")
check(np.array_equal(V._mascara_higado(TOTAL, SEGM, "segmentos"), SEGM > 0),
      "'segmentos' devuelve liver_segments > 0")
for modo, seg, motivo in (("rara", SEGM, "máscara desconocida"),
                          ("union", None, "unión sin liver_segments"),
                          ("union", SEGM[:-1], "rejillas distintas")):
    try:
        V._mascara_higado(TOTAL, seg, modo)
        check(False, "%s: NO abortó" % motivo)
    except SystemExit as e:
        check("ABORTA" in str(e), "%s: aborta (%s)" % (motivo, str(e)[:60]))
check(np.array_equal(V._mascara_higado(TOTAL, None, "total"), TOTAL),
      "'total' funciona aunque no haya liver_segments")

print("== 5. _punto_vs_malla: dentro/fuera y distancia ==")
esfera = V._malla(bola((64, 64, 64), (32, 32, 32), 15), AFIN, suavizado=1.0)
for p, esp_dentro, esp_dist, desc in (((32, 32, 32), True, 15.0, "el centro, a 15 mm"),
                                       ((32, 32, 42), True, 5.0, "a 5 mm por dentro"),
                                       ((32, 32, 52), False, 5.0, "a 5 mm por fuera"),
                                       ((60, 60, 60), False, None, "la esquina, fuera")):
    dentro, dist = V._punto_vs_malla(np.array(p, float), *esfera)
    ok = dentro == esp_dentro and (esp_dist is None or abs(dist - esp_dist) < 0.8)
    check(ok, "%s: %s, superficie a %.2f mm" % (desc, "dentro" if dentro else "fuera", dist))

print()
if fallos:
    print("❌ %d fallo(s):" % len(fallos))
    for f in fallos:
        print("   -", f)
    sys.exit(1)
print("✅ máscara del hígado: unión por defecto, la punta queda dentro, el islote fuera")
