#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flex_analisis/tribunal: utilidades del tribunal del Flex con datos SINTÉTICOS (cero dato de la paciente).

Comprueba: IC de Wilson y bootstrap deterministas; GEM = 16 nt; lectura en bloques de un h5 10x
sintético (matriz y suma, orden de barcodes pedido, filtro de filas) igual a la lectura directa;
score de ventana (célula amplificada > referencia); pesos de profundidad que igualan deciles;
filas del socio con -1 = ceros; y que las reglas congeladas existen y declaran la enmienda.
Necesita numpy/scipy/h5py: si el python del sistema no los tiene, se relanza con el entorno
nescore; si tampoco existe, SKIP (rc 77).
"""
import json
import os
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRIB = os.path.join(RAIZ, "tools", "flex_analisis", "tribunal")
NESCORE = os.path.expanduser("~/.claude-science/conda/envs/nescore/bin/python")

try:
    import numpy as np  # noqa: F401
    import scipy  # noqa: F401
    import h5py  # noqa: F401
except ImportError:
    if os.environ.get("_TRIB_REEXEC") != "1" and os.path.exists(NESCORE):
        env = dict(os.environ, _TRIB_REEXEC="1")
        sys.exit(subprocess.call([NESCORE, "-I", os.path.abspath(__file__)], env=env))
    print("SKIP: sin numpy/scipy/h5py ni entorno nescore")
    sys.exit(77)

import numpy as np
import scipy.sparse as sp
import h5py

sys.path.insert(0, TRIB)
import _comun as C  # noqa: E402

fallos = []


def ok(cond, msg):
    print(("✅ " if cond else "❌ ") + msg)
    if not cond:
        fallos.append(msg)


# 1. IC
p, lo, hi = C.wilson(0, 100)
ok(p == 0 and lo == 0 and 0.03 < hi < 0.04, "Wilson 0/100: cota superior ~3,7 %")
p, lo, hi = C.wilson(50, 100)
ok(abs(p - 0.5) < 1e-12 and lo < 0.5 < hi, "Wilson 50/100 contiene 0,5")
ok(C.boot(30, 200) == C.boot(30, 200), "bootstrap determinista con la semilla")
ok(C.gem("A" * 16 + "C" * 10 + "-1") == "A" * 16, "GEM = 16 primeros nt")

# 2. stream_h5 sobre un h5 10x sintético
rng = np.random.default_rng(1)
nf, nb = 30, 50
M = sp.random(nf, nb, density=0.3, random_state=2, data_rvs=lambda n: rng.integers(1, 9, n)).tocsc().astype(np.int32)
bcs = np.array([f"{i:016d}SUFIJO0000-1" for i in range(nb)])
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "s.h5")
    with h5py.File(path, "w") as f:
        g = f.create_group("matrix")
        g["barcodes"] = bcs.astype("S")
        g["data"] = M.data
        g["indices"] = M.indices.astype(np.int64)
        g["indptr"] = M.indptr.astype(np.int64)
        g["shape"] = np.array([nf, nb], dtype=np.int32)
        fg = g.create_group("features")
        fg["id"] = np.array([f"G{i}" for i in range(nf)]).astype("S")
        fg["name"] = np.array([f"N{i}" for i in range(nf)]).astype("S")
    pedir = bcs[[7, 3, 40, 0]]
    filas = np.array([1, 5, 6, 29])
    R, ids, _ = C.stream_h5(path, pedir, filas=filas, bloque=17)
    esperado = M[filas][:, [7, 3, 40, 0]].T.toarray()
    ok(np.array_equal(R.toarray(), esperado), "stream_h5 matriz = lectura directa (orden y filas)")
    ok(list(ids) == [f"G{i}" for i in filas], "stream_h5 devuelve los ids de las filas pedidas")
    s, _, _ = C.stream_h5(path, pedir, modo="suma", bloque=11)
    ok(np.array_equal(s, np.asarray(M[:, [7, 3, 40, 0]].sum(axis=1)).ravel()), "stream_h5 suma = suma directa")
    try:
        C.stream_h5(path, np.array(["NOESTA"]))
        ok(False, "stream_h5 rechaza barcodes inexistentes")
    except AssertionError:
        ok(True, "stream_h5 rechaza barcodes inexistentes")


# 3. score de ventana
class _Ad:
    pass


ad = _Ad()
X = np.ones((4, 10)) * 10
X[0, :2] = 200  # célula amplificada en los genes 0-1
ad.X = sp.csr_matrix(X)
ad.var_names = np.array([f"E{i}" for i in range(10)])
s, obs, esp = C.score_ventana(ad, ["E0", "E1"], np.array([False, True, True, True]))
ok(s[0] > 2 and abs(s[1]) < 0.2, "score_ventana: la amplificada destaca y la referencia ~0")

# 4. pesos de profundidad
sys.path.insert(0, TRIB)
import _suelo as S  # noqa: E402
uo = rng.integers(1000, 3000, 500)
ur = rng.integers(200, 5000, 800)
w = S.pesos_umi(uo, ur)
q = np.quantile(uo, np.linspace(0, 1, 11))
med_pond = np.average(ur, weights=w)
ok(abs(med_pond - uo.mean()) / uo.mean() < 0.08, "pesos_umi: la referencia reponderada iguala la profundidad media")

# 5. filas del socio
import _fuga as F  # noqa: E402
Xs = sp.csr_matrix(np.arange(12).reshape(4, 3))
P = F.sp_rows(Xs, np.array([2, -1, 0]))
ok(np.array_equal(P.toarray(), np.array([[6, 7, 8], [0, 0, 0], [0, 1, 2]])), "sp_rows: -1 = fila de ceros, orden respetado")

# 6. reglas congeladas
r = json.load(open(os.path.join(TRIB, "REGLAS_TRIBUNAL_v2.json")))
ok("EXPLORATORIA" in r["estado"] and "ENMIENDA_1" in r["adn"], "reglas v2 declaran EXPLORATORIA y la enmienda del conjunto nulo")

print(f"\n{'❌ ' + str(len(fallos)) + ' fallos' if fallos else '✅ todo en verde'}")
sys.exit(1 if fallos else 0)
