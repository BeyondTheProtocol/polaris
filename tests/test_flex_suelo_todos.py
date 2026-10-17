#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_flex_suelo_todos.py — suelo ambiental y referencia emparejada para TODOS los genes del explorador
(tools/flex_analisis/explorador/_suelo_todos.py), con un FIXTURE SINTÉTICO. Cero datos de la paciente.

Por qué: el plan «Loupe público» (17-oct-2026) abre los 18.132 genes; el comité médico exige que ninguno salga
sin suelo y referencia. La ampliación tiene que significar LO MISMO que lo que ya validó el acta para los 77
genes de la web, así que el test central es de EQUIVALENCIA:
  1. el oráculo es una copia literal del bucle de exporta.py; `calcula` lo reproduce bit a bit (==, no
     aproximado) en los genes «de la web» del fixture, en el hígado (sin término de ADN) y en el hueso (con él);
  2. anti-deriva: las tres líneas de la fórmula siguen en exporta.py tal cual; si la dueña cambia la
     fórmula, este test se pone rojo y obliga a actualizar el módulo;
  3. MUTANTES que deben caer: un gen sin suelo (valida_completo), un gen excluido con valor, un suelo
     fuera de [0, 1], un rho alterado en una millonésima (coincide_con_web lo detecta), longitudes
     incoherentes;
  4. grupo vacío → 0.0 como en exporta.py; genes sin sonda → NaN y listados.
Sin numpy/scipy se re-ejecuta con el env nescore; sin ese env, SKIP (77).
"""
import math
import os
import sys

_NESCORE = os.path.expanduser("~/.claude-science/conda/envs/nescore/bin/python")
try:
    import numpy as np
    import scipy.sparse as sp
except ModuleNotFoundError:
    if os.path.isfile(_NESCORE) and os.path.realpath(sys.executable) != os.path.realpath(_NESCORE):
        os.execv(_NESCORE, [_NESCORE, os.path.abspath(__file__)] + sys.argv[1:])
    print("SKIP: sin numpy/scipy ni env nescore; test_flex_suelo_todos no puede correr")
    sys.exit(77)

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXP = os.path.join(RAIZ, "tools", "flex_analisis", "explorador")
sys.path.insert(0, EXP)
import _suelo_todos as S  # noqa: E402

fallos = []
total = [0]

# Las tres líneas de exporta.py que definen el suelo y la referencia (anti-deriva, punto 2).
FORMULA = ("lam = p_amb[j] * rho * umi[m] + delta_adn[m] * nu[j]",
           "suelo[g][gid_] = _r(np.mean(1 - np.exp(-lam)), 3) if m.any() else 0.0",
           "refd[g] = _r(np.sum(w * (v[ref] > 0)) / max(w.sum(), 1e-12), 4)")


def check(cond, desc):
    total[0] += 1
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


def falla(f, desc):
    try:
        f()
    except ValueError:
        check(True, desc)
        return
    check(False, desc)


def _r(x, nd=4):
    """Copia literal de exporta._r (el oráculo no usa la del módulo, para que sean independientes)."""
    if x is None:
        return None
    x = float(x)
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, nd)


def oraculo(X, simb, genes, p_amb, rho, umi, delta_adn, nu, grupo, g_ids, ref, w):
    """Copia LITERAL del bucle de exporta.py (expr aparte): la referencia contra la que se mide `calcula`."""
    suelo, refd = {}, {}
    for g in genes:
        j = int(np.where(simb == g)[0][0])
        v = np.asarray(X[:, j].todense()).ravel().astype(float)
        suelo[g] = {}
        for gid_ in g_ids:
            m = grupo == gid_
            lam = p_amb[j] * rho * umi[m] + delta_adn[m] * nu[j]
            suelo[g][gid_] = _r(np.mean(1 - np.exp(-lam)), 3) if m.any() else 0.0
        refd[g] = _r(np.sum(w * (v[ref] > 0)) / max(w.sum(), 1e-12), 4)
    return suelo, refd


def fixture(semilla, con_adn):
    rng = np.random.default_rng(semilla)
    n_cel, n_gen = 700, 260
    X = sp.random(n_cel, n_gen, density=0.08, format="csr", random_state=semilla,
                  data_rvs=lambda k: rng.integers(1, 30, k)).astype(np.float32)
    umi = np.asarray(X.sum(axis=1)).ravel().astype(np.int64) + rng.integers(200, 4000, n_cel)
    p_amb = rng.dirichlet(np.ones(n_gen))
    rho = float(rng.uniform(0.2, 3.0))
    delta_adn = rng.uniform(0, 0.02, n_cel) if con_adn else np.zeros(n_cel)
    nu = rng.integers(0, 6, n_gen)
    vocab = np.array(["interes", "hepatocito", "NK", "mieloide", "otras"])
    grupo = vocab[rng.integers(0, len(vocab), n_cel)]
    g_ids = ["interes", "hepatocito", "NK", "mieloide", "endotelio", "otras"]  # «endotelio» vacío → 0.0
    ref = np.isin(grupo, ["hepatocito", "NK"])
    w = rng.uniform(0, 2, int(ref.sum()))
    excl = np.zeros(n_gen, bool)
    excl[rng.choice(n_gen, 12, replace=False)] = True
    simb = np.array(["G%03d" % i for i in range(n_gen)])
    web = [s for s, e in zip(simb, excl) if not e][:77]
    return dict(X=X, umi=umi, p_amb=p_amb, rho=rho, delta_adn=delta_adn, nu=nu, grupo=grupo, g_ids=g_ids,
                ref=ref, w=w, excl=excl, simb=simb, web=web)


def corre(F, rho=None):
    return S.calcula(F["X"], F["p_amb"], F["rho"] if rho is None else rho, F["umi"], F["delta_adn"], F["nu"],
                     F["grupo"], F["g_ids"], F["ref"], F["w"], F["excl"])


def main():
    print("1. equivalencia bit a bit con el bucle de exporta.py")
    for nombre, semilla, adn in (("hígado (sin ADN)", 1, False), ("hueso (con ADN)", 2, True)):
        F = fixture(semilla, adn)
        res = corre(F)
        sw, rw = oraculo(F["X"], F["simb"], F["web"], F["p_amb"], F["rho"], F["umi"], F["delta_adn"], F["nu"],
                         F["grupo"], F["g_ids"], F["ref"], F["w"])
        malos = S.coincide_con_web(res, list(F["simb"]), sw, rw)
        check(malos == [], "%s: los %d genes de la web, idénticos al oráculo (%s)" % (nombre, len(F["web"]), malos[:2]))
        check(S.valida_completo(res, F["X"].shape[1]), "%s: valida_completo acepta el cálculo completo" % nombre)
        k = res["grupos"].index("endotelio")
        no_excl = ~F["excl"]
        check(bool((res["suelo"][no_excl, k] == 0.0).all()), "%s: grupo vacío → suelo 0.0 como en exporta.py" % nombre)
        check(sorted(res["excluidos"]) == sorted(int(i) for i in np.where(F["excl"])[0])
              and bool(np.isnan(res["referencia"][F["excl"]]).all()),
              "%s: genes sin sonda → NaN y listados" % nombre)
        check(int((~np.isnan(res["referencia"])).sum()) == int(no_excl.sum()),
              "%s: referencia para TODOS los genes no excluidos (%d), no solo los de la web" % (nombre, int(no_excl.sum())))

    print("2. anti-deriva: la fórmula sigue igual en exporta.py")
    src = open(os.path.join(EXP, "exporta.py"), encoding="utf-8").read()
    for linea in FORMULA:
        check(linea in src, "exporta.py contiene: %s" % linea[:60])

    print("3. mutantes que deben caer")
    F = fixture(3, True)
    res = corre(F)
    sw, rw = oraculo(F["X"], F["simb"], F["web"], F["p_amb"], F["rho"], F["umi"], F["delta_adn"], F["nu"],
                     F["grupo"], F["g_ids"], F["ref"], F["w"])
    j = int(np.where(~F["excl"])[0][5])
    m1 = {**res, "suelo": res["suelo"].copy()}
    m1["suelo"][j, 0] = np.nan
    falla(lambda: S.valida_completo(m1, F["X"].shape[1]), "un gen sin suelo → valida_completo falla")
    m2 = {**res, "referencia": res["referencia"].copy()}
    m2["referencia"][j] = np.nan
    falla(lambda: S.valida_completo(m2, F["X"].shape[1]), "un gen sin referencia → valida_completo falla")
    m3 = {**res, "referencia": res["referencia"].copy()}
    m3["referencia"][res["excluidos"][0]] = 0.5
    falla(lambda: S.valida_completo(m3, F["X"].shape[1]), "un gen excluido con valor → valida_completo falla")
    m4 = {**res, "suelo": res["suelo"].copy()}
    m4["suelo"][j, 1] = 1.5
    falla(lambda: S.valida_completo(m4, F["X"].shape[1]), "un suelo fuera de [0, 1] → valida_completo falla")
    alterado = corre(F, rho=F["rho"] * 1.001)
    check(S.coincide_con_web(alterado, list(F["simb"]), sw, rw) != [],
          "rho alterado un 0,1 % → coincide_con_web lo detecta")
    falla(lambda: S.calcula(F["X"], F["p_amb"][:-1], F["rho"], F["umi"], F["delta_adn"], F["nu"], F["grupo"],
                            F["g_ids"], F["ref"], F["w"], F["excl"]), "p_amb con un gen de menos → ValueError")
    falla(lambda: S.calcula(F["X"], F["p_amb"], F["rho"], F["umi"], F["delta_adn"], F["nu"], F["grupo"],
                            F["g_ids"], F["ref"], F["w"][:-1], F["excl"]), "w sin un peso por célula de referencia → ValueError")


if __name__ == "__main__":
    main()
    print("\n%d comprobaciones, %d fallos" % (total[0], len(fallos)))
    sys.exit(1 if fallos else 0)
