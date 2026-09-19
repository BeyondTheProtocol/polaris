#!/usr/bin/env python3
"""Tests de tools/guardian_evals.py — L9, el banco que verifica al verificador.

Prueba el EFECTO en los dos sentidos: (a) con el Guardián real (L1), el banco da SANO;
(b) con un checker AGUJEREADO (siempre dice entregable), el banco lo DETECTA (exit 1).
Sin esto, «el banco corre» no significa «el banco sirve».
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import guardian_evals as ge  # noqa: E402

AHORA = datetime(2026, 6, 27, tzinfo=timezone.utc)
fallos = 0
total = 0


def check(nombre, cond):
    global fallos, total
    total += 1
    print(("  ✓ " if cond else "  ✗ ") + nombre)
    if not cond:
        fallos += 1


# 1) con el Guardián REAL (L1) → SANO, exit 0, todas cazadas
resultados, sano, exit_code, resumen = ge.correr(AHORA)
check("con el Guardián real → SANO (exit 0)", sano and exit_code == 0)
check("caza TODAS las mutaciones + el canario", all(r["cazada"] for r in resultados))


# 2) con un checker AGUJEREADO (siempre entregable) → el banco lo DETECTA (exit 1)
def checker_roto(piezas, ahora):
    return ([], True, [])  # miente: dice que TODO es entregable


resultados_r, sano_r, exit_r, _ = ge.correr(AHORA, checker=checker_roto)
check("checker agujereado → banco NO lo da por sano", not sano_r)
check("checker agujereado → exit 1 (agujero detectado)", exit_r == 1)
check("checker agujereado → ninguna mutación 'cazada'", not any(r["cazada"] for r in resultados_r))


# 3) con un checker que NO valida la base (siempre NO entregable) → no se puede medir, exit 2
def checker_paranoico(piezas, ahora):
    return ([], False, ["todo bloqueado"])  # ni la base pasa


_, sano_p, exit_p, _ = ge.correr(AHORA, checker=checker_paranoico)
check("checker que ni valida la base → exit 2 (no se puede medir, fail-closed)",
      (not sano_p) and exit_p == 2)


print("RESULTADO guardian_evals (L9): %d OK, %d fallos" % (total - fallos, fallos))
print("✅ L9 EN VERDE" if not fallos else "❌ revisar fallos")
sys.exit(0 if not fallos else 1)
