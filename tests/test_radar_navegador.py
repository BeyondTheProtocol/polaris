#!/usr/bin/env python3
"""test_radar_navegador.py — el carril de registros sin API (CTIS, ChiCTR, CDE).

Lo que protege: que lo extraido a mano con el navegador NO se pierda (se ingiere, se deduplica y
entra en la cola con prioridad alta, porque no lo trae ninguna otra fuente) y que el recordatorio
de barrer siga funcionando. Sin red: la entrada son fixtures. $0.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="radar_nav_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_REPO"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))
import radar_navegador as rv   # noqa: E402

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


class A:
    def __init__(self, **kw):
        self.fuente = kw.get("fuente")
        self.fichero = kw.get("fichero")
        self.terminos = kw.get("terminos")
        self.dry = kw.get("dry", False)


def main():
    # 1. las tres fuentes estan declaradas con url, pasos y plantilla de ficha
    ok(set(rv.FUENTES) == {"ctis", "chictr", "cde"}, "las tres fuentes sin API estan declaradas")
    ok(all(f.get("url") and f.get("pasos") and "{id}" in f.get("ficha", "")
           for f in rv.FUENTES.values()), "cada fuente trae url, pasos y ficha parametrizada")

    # 2. nunca barrido => todas pendientes (es lo que la brujula saca en cada sesion)
    ok(len(rv.pendientes()) == 3, "sin barridos previos, las tres estan pendientes")
    ok(rv.dias_desde("ctis") is None, "sin marca, dias_desde es None")

    # 3. ingesta: normaliza, deduplica y sella la marca
    f = os.path.join(_TMP, "ctis.json")
    with open(f, "w") as fh:
        json.dump([{"id": "2025-522707-26-00", "fecha": "15/09/2026",
                    "titulo": "IEV407 en mama avanzada", "cond": "Advanced HR+/HER2- breast cancer",
                    "loc": "Germany/Italy"},
                   {"id": "2026-527524-27-00", "titulo": "ODEON"}], fh)
    ok(rv.cmd_ingerir(A(fuente="ctis", fichero=f, dry=True)) == 0, "ingesta en dry no falla")
    ok(rv.dias_desde("ctis") is None, "una ingesta en dry NO sella la marca")
    ok(rv.cmd_ingerir(A(fuente="ctis", fichero=f, terminos="breast cancer")) == 0, "ingesta real")
    ok(rv.dias_desde("ctis") == 0, "la ingesta real sella la marca de hoy")
    ok(len(rv.pendientes()) == 2, "la fuente barrida sale de pendientes")

    # 4. la segunda ingesta del mismo fichero no duplica nada
    import radar_ned_diario as rn
    n_antes = len(rn.lee_cola()["pendientes"])
    rv.cmd_ingerir(A(fuente="ctis", fichero=f, terminos="breast cancer"))
    ok(len(rn.lee_cola()["pendientes"]) == n_antes, "reingerir lo mismo no duplica la cola")

    # 5. lo del navegador entra con prioridad ALTA: no lo trae ninguna otra fuente, no puede
    #    quedarse fuera del cupo de la capa abierta
    c = rn.lee_cola()
    ok(any(p["uid"].startswith("ctis:") for p in c["pendientes"]), "el lead de CTIS esta en la cola")

    # 6. un JSON que no es lista se rechaza sin romper nada
    malo = os.path.join(_TMP, "malo.json")
    open(malo, "w").write('"no soy una lista"')
    ok(rv.cmd_ingerir(A(fuente="ctis", fichero=malo)) == 2, "un JSON con forma incorrecta se rechaza")
    ok(rv.cmd_ingerir(A(fuente="inventada", fichero=f)) == 2, "una fuente desconocida se rechaza")

    print("test_radar_navegador: %d ok, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
