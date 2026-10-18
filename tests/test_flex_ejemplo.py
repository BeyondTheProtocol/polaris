#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_flex_ejemplo.py — datos de EJEMPLO del explorador (tools/flex_analisis/explorador/ejemplo.py).

Comprueba:
  1. el ejemplo pasa el MISMO validador que el export real (valida_salidas), con JSON y Zarr;
  2. es determinista: dos ejecuciones dan los mismos JSON y el mismo árbol Zarr;
  3. no lleva nada del análisis real: grupos de interés con tamaño inventado (ni 993 ni 68), todos los
     veredictos «sin_veredicto», genes extra con nombre EJ…;
  4. la copia para la web lleva ejemplo: true en cada lesión y en el manifiesto, el sha de cada JSON y la
     huella de cada Zarr cuadran, y matriz_completa es «ejemplo».
Sin anndata se re-ejecuta con el env nescore; sin ese env, SKIP (77). No lee la zona clínica.
"""
import os
import sys

_NESCORE = os.path.expanduser("~/.claude-science/conda/envs/nescore/bin/python")
try:
    import anndata  # noqa: F401
    import numpy  # noqa: F401
except ModuleNotFoundError:
    if os.path.isfile(_NESCORE) and os.path.realpath(sys.executable) != os.path.realpath(_NESCORE):
        os.execv(_NESCORE, [_NESCORE, os.path.abspath(__file__)] + sys.argv[1:])
    print("SKIP: sin anndata ni env nescore; test_flex_ejemplo no puede correr")
    sys.exit(77)

import json  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools", "flex_analisis", "explorador"))
import _contrato as C  # noqa: E402
import ejemplo as E  # noqa: E402

N_REALES = dict(C.N_INTERES)
fallos, total = [], [0]


def check(cond, desc):
    total[0] += 1
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


def main():
    d1, d2, w1, w2 = (tempfile.mkdtemp(prefix="ej_") for _ in range(4))
    try:
        try:
            val = E.genera(d1)
            check(val["zarr/higado.zarr"]["n_genes"] == len(E.GENES) and val["web/higado.json"]["n_celulas"] == E.N_CELULAS["higado"],
                  "el ejemplo pasa valida_salidas entero (JSON + Zarr con todos los genes del ejemplo)")
        except C.ValidacionFallida as e:
            check(False, "el ejemplo NO pasa el validador del contrato: %s" % e)
            return
        E.genera(d2)
        m1 = E.copia_web(d1, w1)
        m2 = E.copia_web(d2, w2)
        check(m1 == m2, "determinista: mismo manifiesto (sha de cada JSON y huella de cada Zarr) en dos ejecuciones")
        check(E.N_INTERES_EJEMPLO["higado"] != N_REALES["higado"] and E.N_INTERES_EJEMPLO["hueso"] != N_REALES["hueso"],
              "tamaños del grupo de interés inventados (no los del análisis)")
        for t in C.LESIONES:
            o = json.load(open(os.path.join(w1, "%s.json" % t), encoding="utf-8"))
            interes = [x["n"] for x in o["grupos"] if x["rol"] == "interes"][0]
            check(o.get("ejemplo") is True and interes == E.N_INTERES_EJEMPLO[t] and interes != N_REALES[t],
                  "%s: ejemplo: true y grupo de interés de tamaño inventado" % t)
            check(set(o["veredicto"].values()) == {"sin_veredicto"}, "%s: ningún veredicto del acta" % t)
            genes_mk = {g for gs in o["marcadores"].values() for g in gs["gen"]}
            check(bool(genes_mk) and genes_mk <= set(E.GENES), "%s: marcadores calculados sobre los genes del ejemplo" % t)
        check(m1.get("ejemplo") is True and m1.get("matriz_completa") == "ejemplo" and m1["sello"] == "sin_verificar"
              and m1[C.CLAVE_OK_TITULAR] is False, "manifiesto de la web: ejemplo, sin verificar, sin OK, matriz_completa «ejemplo»")
        for f in m1["ficheros"]:
            check(E._sha(os.path.join(w1, f["ruta"])) == f["sha256"], "sha de %s cuadra con el manifiesto" % f["ruta"])
        for z in m1["zarr"]:
            check(E.sha_zarr(os.path.join(w1, z["ruta"])) == z["sha256_arbol"], "huella del árbol de %s cuadra" % z["ruta"])
        # la carpeta PUBLICADA, tal cual la sirve la web, pasa valida_publicado (con los tamaños inventados del ejemplo)
        C.N_INTERES = dict(E.N_INTERES_EJEMPLO)
        try:
            r = C.valida_publicado(w1)
            check(r["ejemplo"] is True and "zarr/higado" in r and "zarr/hueso" in r, "la carpeta publicada pasa valida_publicado (JSON + Zarr + huellas + agregados)")
        except C.ValidacionFallida as e:
            check(False, "la carpeta publicada NO pasa valida_publicado: %s" % e)
        man_p = os.path.join(w1, "manifiesto.json")
        original = open(man_p, encoding="utf-8").read()

        def con_manifiesto(cambios, quitar=(), real=False):
            m = json.loads(original)
            for k in quitar:
                m.pop(k, None)
            m.update(cambios)
            if real:                                    # las lesiones sin «ejemplo», con su sha recalculado
                for f in m["ficheros"]:
                    p_ = os.path.join(w1, f["ruta"])
                    o = json.load(open(p_, encoding="utf-8"))
                    o.pop("ejemplo", None)
                    json.dump(o, open(p_, "w", encoding="utf-8"), ensure_ascii=False)
                    f["sha256"] = E._sha(p_)
            open(man_p, "w", encoding="utf-8").write(json.dumps(m, ensure_ascii=False))

        def debe_fallar(desc, esperado):
            try:
                C.valida_publicado(w1)
                check(False, "MUTANTE no detectado: " + desc)
            except C.ValidacionFallida as e:
                check(esperado in str(e), "mutante tumbado: %s (%s)" % (desc, str(e)[:90]))

        con_manifiesto({"matriz_completa": {"fecha_decision": "2026-10-25"}})
        debe_fallar("un ejemplo que declara una decisión real de la matriz completa", "un ejemplo no puede declarar")
        con_manifiesto({"sello": "verificado", C.CLAVE_OK_TITULAR: True, "fecha_ok": "2026-10-20"}, quitar=("ejemplo", "matriz_completa"), real=True)
        debe_fallar("datos reales con Zarr en la carpeta y SIN la fecha de la decisión (fail-closed)", "Zarr real sin la fecha de la decisión")
        con_manifiesto({"sello": "verificado", C.CLAVE_OK_TITULAR: True, "fecha_ok": "2026-10-20",
                        "matriz_completa": {"fecha_decision": "2026-02-30"}}, quitar=("ejemplo",), real=True)
        debe_fallar("datos reales con una fecha de decisión que no es de calendario (cae ya como fecha suelta)", "fecha")
        con_manifiesto({"sello": "verificado", C.CLAVE_OK_TITULAR: True, "fecha_ok": "2026-10-20",
                        "matriz_completa": {"fecha_decision": "2026-10-25"}}, quitar=("ejemplo",), real=True)
        try:
            r = C.valida_publicado(w1)
            check(r["ejemplo"] is False, "datos reales CON la fecha de la decisión: pasa (la regla abre solo así)")
        except C.ValidacionFallida as e:
            check(False, "real con decisión debería pasar: %s" % e)
        open(man_p, "w", encoding="utf-8").write(original)
    finally:
        for d in (d1, d2, w1, w2):
            shutil.rmtree(d, ignore_errors=True)
        C.N_INTERES = N_REALES


if __name__ == "__main__":
    main()
    print("\n%d comprobaciones, %d fallos" % (total[0], len(fallos)))
    sys.exit(1 if fallos else 0)
