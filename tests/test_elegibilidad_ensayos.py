#!/usr/bin/env python3
"""Tests de tools/elegibilidad_ensayos.py — L3, «CT.gov nunca confirma, solo descarta».

Sin red (comprobador CT.gov inyectado), `ahora` fijo. El caso central es el CANARIO PNV21:
CT.gov dice RECRUITING pero, sin confirmación del centro, NO es elegible (el falso negativo
que descalificó a {{TITULAR}} sin que cambiara ningún campo de CT.gov).
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import elegibilidad_ensayos as el  # noqa: E402

AHORA = datetime(2026, 6, 27, tzinfo=timezone.utc)
fallos = 0
total = 0


def check(nombre, cond):
    global fallos, total
    total += 1
    print(("  ✓ " if cond else "  ✗ ") + nombre)
    if not cond:
        fallos += 1


def abierto(ncts):   # CT.gov: todos RECRUITING/existen
    return {n: "abierto" for n in ncts}


def cerrado(ncts):   # CT.gov: inexistente/cerrado
    return {n: "inexistente" for n in ncts}


def caido(ncts):     # CT.gov no responde
    return {n: "no_resoluble" for n in ncts}


def ev(piezas, comp):
    return el.evaluar_elegibilidad(piezas, AHORA, comprobador_ct=comp)


P_PNV21 = {"id": "B5-PNV21", "bloque": "B5",
           "afirmacion": "elegibilidad para PNV21", "fuentes": ["NCT05098210", "Tablero"],
           "confianza": "por-confirmar", "status": "vivo", "gate_humano": "G5",
           "confirmado_en": None, "ttl_dias": 7}


# 1) CANARIO PNV21: CT.gov RECRUITING, sin confirmación del centro → NO elegible
bloq = ev([P_PNV21], abierto)
check("PNV21 RECRUITING en CT.gov pero sin centro → flag NO confirmada (regla PNV21)",
      any("no confirmada por el centro" in b.lower() or "NO confirmada por el centro" in b for b in bloq))

# 2) CT.gov cerrado/inexistente → DESCARTE
bloq = ev([P_PNV21], cerrado)
check("ensayo cerrado/inexistente en CT.gov → DESCARTE", any("DESCARTE" in b for b in bloq))

# 3) CT.gov no resoluble → fail-closed
bloq = ev([P_PNV21], caido)
check("CT.gov no resoluble → fail-closed", any("fail-closed" in b for b in bloq))

# 4) abierto + confirmación FRESCA del centro → elegible (sin bloqueo)
p_ok = dict(P_PNV21, confirmado_en="2026-06-25T00:00:00Z", confianza="alta")
bloq = ev([p_ok], abierto)
check("abierto + confirmación fresca del centro → elegible (sin bloqueo)", not bloq)

# 5) confirmación del centro CADUCADA → vuelve a no confirmada
p_caduca = dict(P_PNV21, confirmado_en="2026-01-01T00:00:00Z", ttl_dias=7)
bloq = ev([p_caduca], abierto)
check("confirmación del centro caducada → no confirmada", any("no confirmada por el centro" in b.lower() for b in bloq))

# 6) confianza ALTA sin centro → flag (CT.gov no la justifica)
p_alta = dict(P_PNV21, confianza="alta")
bloq = ev([p_alta], abierto)
check("alta sin confirmación del centro → flag (CT.gov no la justifica)",
      any("ALTA en elegibilidad" in b for b in bloq))

# 7) pieza que NO es de ensayo → L3 no la toca
p_noens = {"id": "B1-x", "bloque": "B1", "afirmacion": "dato no clínico de ensayo",
           "confianza": "media", "status": "vivo", "confirmado_en": "2026-06-25T00:00:00Z", "ttl_dias": 30}
bloq = ev([p_noens], abierto)
check("pieza no-ensayo → L3 no la afecta", not bloq)

# 8) pieza de ensayo SUPERADA → no se evalúa (histórico)
p_sup = dict(P_PNV21, status="superado")
bloq = ev([p_sup], abierto)
check("ensayo superado (histórico) → L3 no lo evalúa", not bloq)


print("RESULTADO elegibilidad_ensayos (L3): %d OK, %d fallos" % (total - fallos, fallos))
print("✅ L3 EN VERDE" if not fallos else "❌ revisar fallos")
sys.exit(0 if not fallos else 1)
