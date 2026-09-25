#!/usr/bin/env python3
"""test_evals_honestidad.py — el sello de evidencia ya tiene vara que lo mida.

Hallazgo medio nº13 de la auditoría del 25-jul-26: el golden set no tenía NI UN caso sobre sellos de
evidencia. Enumerado entonces: muro 17 casos, router 7, calidad 3, honestidad 0. De las dos reglas
que CLAUDE.md llama inquebrantables, una tenía diecisiete casos gratis y la otra ninguno — y la que
no tenía vara es justo la que ya falló en producción (el «Baifo = caja negra» del 28-jun, relayado
como hecho sin verificar).

Este test guarda dos cosas: que las categorías `sello` y `citas` sigan existiendo y en verde, y que
el golden set no se vacíe (un fichero de casos vaciado devolvería «100% pass» sobre nada, que es la
forma más limpia de fingir cobertura).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import evals  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def main():
    check("la categoría `sello` está registrada", "sello" in evals.TIPOS)
    check("la categoría `citas` está registrada", "citas" in evals.TIPOS)
    check("ambas tienen evaluador determinista (no dependen del juez LLM)",
          "sello" in evals._EVALUADORES and "citas" in evals._EVALUADORES)

    casos = evals.cargar_casos(tipos=("sello", "citas"))
    sellos = [c for c in casos if c["tipo"] == "sello"]
    citas = [c for c in casos if c["tipo"] == "citas"]
    check("hay casos de sello en el golden set (el bug era que hubiera 0)", len(sellos) >= 5)
    check("hay casos de citas en el golden set", len(citas) >= 3)
    check("los ids son únicos", len({c["id"] for c in casos}) == len(casos))

    # El incidente que originó el tool tiene que estar cubierto por su nombre, no por casualidad.
    ids = {c["id"] for c in casos}
    check("está el caso del relay «según el red-team» (el incidente del 28-jun)",
          "sello-relay-redteam-baifo" in ids)
    check("está el DOI dentro de una bibliografía (el agujero de verifica_citas)",
          "citas-doi-en-bibliografia" in ids)

    fallos = []
    for c in casos:
        r = evals.correr_caso(c)
        if not r.get("paso"):
            fallos.append("%s: %s" % (c["id"], r.get("detalle")))
    check("todos los casos de sello/citas pasan" + ("" if not fallos else " → " + "; ".join(fallos)),
          not fallos)

    # Falsos negativos: si el linter dejara de marcar, estos casos tienen que ponerse ROJOS.
    # Se comprueba invirtiendo la expectativa, no tocando el tool.
    trampa = {"id": "trampa", "tipo": "sello",
              "texto": "El arnes es una caja negra con telemetria sin documentar.",
              "espera": {"flags": 0}}
    check("un caso con la expectativa MAL puesta falla (la vara mide de verdad)",
          not evals.correr_caso(trampa).get("paso"))

    trampa2 = {"id": "trampa2", "tipo": "citas",
               "cadena": "Perez J. Lancet Oncol. 2025. doi:10.1016/S1470-2045(25)00123-4",
               "espera": {"clase": "desconocido"}}
    check("y lo mismo en citas", not evals.correr_caso(trampa2).get("paso"))

    # La categoría `citas` es OFFLINE: un caso que pidiera `estado` con un id resoluble saldría a la
    # red y volvería el rojo dependiente del wifi, que es la forma de enseñar a ignorar los rojos.
    mal_disenado = {"id": "red", "tipo": "citas", "cadena": "NCT05098210",
                    "espera": {"clase": "nct", "estado": "existe"}}
    r = evals.correr_caso(mal_disenado)
    check("un caso que pediría red se rechaza con un motivo claro",
          not r.get("paso") and "offline" in (r.get("detalle") or ""))

    print("test_evals_honestidad: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
