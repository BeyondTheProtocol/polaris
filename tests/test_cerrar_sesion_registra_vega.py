#!/usr/bin/env python3
"""test_cerrar_sesion_registra_vega.py — cada fusión de sesión queda en el registro de Vega.

POR QUÉ (1-oct-2026, {{TITULAR}}: «quiero que Vega sea la orquestadora de todo»). Las sesiones fusionaban
a casa base y Vega no se enteraba: ese mismo día una sesión fusionó `edc49f6` y su registro de
aprobaciones no lo vio. Con una casa base de usar y tirar (BTP_REPO) y estado aislado
(BTP_STATE_DIR) se fija:
  1. tras fusionar, `vega/aprobaciones.jsonl` gana una fila `codigo_fusion`, nivel A,
     quien = sesion:<rama>, con el asunto del commit, la prueba (merge + baterías) y el revert;
  2. el resumen del cierre lo dice;
  3. si no hay nada que fusionar, no se apunta nada.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tempfile  # noqa: E402

import test_cerrar_sesion_verifica_base as base_t  # noqa: E402  (reusa su casa base de usar y tirar)

fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def filas(tmp):
    p = os.path.join(tmp, "state", "vega", "aprobaciones.jsonl")
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def main():
    print("── fusión de sesión → registro de Vega ──")
    tmp = tempfile.mkdtemp(prefix="cierre_vega_")
    base, wt = base_t.casa(tmp, rojo=False)
    salida = base_t.cerrar(base, wt, tmp)
    fs = filas(tmp)
    check(len(fs) == 1, "una fila en el registro (hay %d)" % len(fs))
    if fs:
        f = fs[0]
        check(f["tipo"] == "codigo_fusion" and f["nivel"] == "A", "tipo codigo_fusion, nivel A")
        check(f["quien"] == "sesion:claude/x", "quien = sesion:<rama> (%r)" % f["quien"])
        check(f["que"] == "trabajo", "qué = asunto del commit fusionado (%r)" % f["que"])
        check(f["prueba"].startswith("merge ") and "en verde" in f["prueba"],
              "prueba = merge + baterías (%r)" % f["prueba"])
        check("revert -m 1" in f["deshacer"], "deshacer = revert del merge (%r)" % f["deshacer"])
    check("vega: fusión apuntada" in salida, "el resumen del cierre lo dice")

    print("── nada que fusionar → nada que apuntar ──")
    salida2 = base_t.cerrar(base, wt, tmp)
    check(len(filas(tmp)) == 1, "un segundo cierre sin commits nuevos no añade filas")

    print("\ntest_cerrar_sesion_registra_vega: %d fallos" % len(fallos))
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
