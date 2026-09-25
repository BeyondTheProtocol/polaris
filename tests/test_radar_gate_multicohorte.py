#!/usr/bin/env python3
"""test_radar_gate_multicohorte.py — un «encaja» en un ensayo multicohorte exige citar SU cohorte.

Nació del 20-sep-26: NCT06172478 (HER3-DXd, pan-tumor) se cerró como «el único que encaja» porque
«no exige tratamiento previo específico», y su cohorte de mama exige una línea de quimio. El test
fija que ese veredicto real se rechaza, que uno con cohorte + cita literal pasa, que los descartes
y los papers no se frenan, y que `cmd_cerrar` de verdad devuelve 4 y deja el lead en la cola.
"""
import json
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import radar_ned_diario as r  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


HER3 = {"uid": "ctgov:NCT06172478", "ref": "NCT06172478", "tipo": "ensayo",
        "titulo": "[RECRUITING] A Study of HER3-DXd in Subjects With Locally Advanced or Metastatic Solid Tumors"}
VIEJO = ("abierto: HER3-DXd en solidos avanzados, fase 2, RECLUTANDO. Incluye mama HR+ entre las "
         "cohortes y no exige tratamiento previo especifico.")
BUENO = ("ENCAJA. Cohorte de mama: «must have progression on or after CDK 4/6 inhibitor combined with "
         "endocrine therapy»")


def main():
    g = r.gate_multicohorte
    check("el veredicto real de HER3-DXd se rechaza", not g(HER3, VIEJO)[0])
    check("con cohorte de mama + cita literal pasa", g(HER3, BUENO)[0])
    check("un descarte claro pasa sin cita", g(HER3, "DESCARTADO: la cohorte de mama exige quimio")[0])
    check("«entre las cohortes» no cuenta como citar la cohorte",
          not g(HER3, "Encaja: incluye mama entre las cohortes, «texto literal de un criterio cualquiera»")[0])
    check("sin cohortes + cita literal pasa",
          g(HER3, "Encaja, sin cohortes: «Locally advanced or metastatic breast cancer and other solid tumors»")[0])
    check("un paper no se frena", g(dict(HER3, tipo="paper"), VIEJO)[0])
    check("un ensayo de una sola histología no se frena",
          g(dict(HER3, titulo="T-DXd in HER2 IHC 0 breast cancer"), VIEJO)[0])

    # Integración: cmd_cerrar devuelve 4 y el lead sigue pendiente.
    d = tempfile.mkdtemp()
    cola = os.path.join(d, "cola.json")
    json.dump({"pendientes": [dict(HER3)], "cerrados": []}, open(cola, "w"))
    orig = (r.COLA, r.DIR_ESTADO)
    r.COLA, r.DIR_ESTADO = cola, d
    try:
        rc = r.cmd_cerrar(types.SimpleNamespace(ref="NCT06172478", veredicto=VIEJO, radar=False))
        c = json.load(open(cola))
        check("cmd_cerrar rechaza con 4", rc == 4)
        check("el lead sigue en la cola", len(c["pendientes"]) == 1 and not c["cerrados"])
        rc = r.cmd_cerrar(types.SimpleNamespace(ref="NCT06172478", veredicto=BUENO, radar=False))
        c = json.load(open(cola))
        check("con cita correcta se cierra", rc == 0 and len(c["cerrados"]) == 1)
    finally:
        r.COLA, r.DIR_ESTADO = orig
    print("test_radar_gate_multicohorte: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
