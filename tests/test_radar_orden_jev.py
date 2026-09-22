#!/usr/bin/env python3
"""test_radar_orden_jev.py — Jev solo ORDENA la cola del radar: nunca archiva ni toca lo cruzado.

21-sep-26: medido antes de enchufarlo, Jev separa bien lo que es de {{DIAGNOSTICO}} HR+ pero no
juzga el encaje, y dejándole decidir habría archivado DAREON-NEC-1. Este test fija las cuatro
garantías del plan: los carriles cruzados no se puntúan ni se adelantan, no se quita ni se
archiva nada, si el borde niega no se llama a Jev, y si Jev falla la cola queda intacta. Offline:
`preguntar` y `egress` son falsos; no sale nada a la red.
"""
import copy
import os
import sys

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


def cola():
    return [
        {"uid": "a", "tema": "adc-hr", "titulo": "ADC en HR+ metastásico", "encolado": "2026-09-20"},
        {"uid": "b", "tema": "ne-mama", "titulo": "{{DIANA2}} CAR-T", "encolado": "2026-09-19"},
        {"uid": "c", "tema": "adc-hr", "titulo": "HER2+ temprano", "encolado": "2026-09-18"},
        {"uid": "d", "tema": "til", "titulo": "TIL en melanoma", "encolado": "2026-09-21"},
    ]


def main():
    llamadas = []

    def jev(estado, clave, instr=None):
        llamadas.append(estado)
        return (0.9 if "HR+" in estado else 0.1), 1.0

    permite = lambda t, destino=None: (True, "ok")
    niega = lambda t, destino=None: (False, "HALT")

    pend = cola()
    antes = copy.deepcopy(pend)
    n, _ = r.orden_jev(pend, preguntar=jev, clave="x", egress=permite)
    check("puntúa solo los 2 de mama", n == 2)
    check("no puntúa los cruzados", all("p_jev" not in p for p in pend if p["tema"] in r.CRUZ))
    check("no quita ni reordena la lista", [p["uid"] for p in pend] == [p["uid"] for p in antes])
    check("ningún texto cruzado sale a Jev", not any("{{DIANA2}}" in e or "TIL" in e for e in llamadas))

    orden = [p["uid"] for p in sorted(pend, key=r.clave_orden)]
    check("mama por p_jev primero, cruzado detrás por llegada", orden == ["a", "c", "b", "d"])

    llamadas.clear()
    pend = cola()
    n, _ = r.orden_jev(pend, preguntar=jev, clave="x", egress=niega)
    check("si el borde niega, no se llama a Jev", n == 0 and not llamadas)

    def rota(estado, clave, instr=None):
        raise OSError("sin red")

    pend = cola()
    antes = copy.deepcopy(pend)
    n, motivo = r.orden_jev(pend, preguntar=rota, clave="x", egress=permite)
    check("si Jev falla, la cola queda intacta", n == 0 and pend == antes and "fallo" in motivo)

    pend = cola()
    pend[0]["p_jev"] = 0.5
    llamadas.clear()
    r.orden_jev(pend, preguntar=jev, clave="x", egress=permite)
    check("no repuntúa lo ya puntuado", len(llamadas) == 1)

    src = open(r.__file__, encoding="utf-8").read()
    cuerpo = src[src.index("def orden_jev"):src.index("def clave_orden")]
    check("orden_jev no toca descartados", "descartados" not in cuerpo)

    print("test_radar_orden_jev: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
