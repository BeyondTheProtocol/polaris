#!/usr/bin/env python3
"""Tests de tools/frescura_dosier.py — el núcleo determinista de frescura del Dosier.

Sin red, sin modelo, `ahora` inyectado (determinista). Demuestra el EFECTO: una pieza
caducada / sin confirmar / con gate abierto hace el dosier NO entregable (fail-closed),
y una línea JSONL malformada NO se ignora en silencio.
"""
import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import frescura_dosier as fd  # noqa: E402

AHORA = datetime(2026, 6, 26, tzinfo=timezone.utc)
fallos = 0
total = 0


def check(nombre, cond):
    global fallos, total
    total += 1
    print(("  ✓ " if cond else "  ✗ ") + nombre)
    if not cond:
        fallos += 1


def est(p):
    return fd.estado_pieza(p, AHORA)[0]


# ── estados por pieza ────────────────────────────────────────────────────────
check("FRESCO: vivo + confirmado dentro de ventana",
      est({"id": "a", "status": "vivo", "confirmado_en": "2026-06-20T00:00:00Z", "ttl_dias": 30}) == fd.FRESCO)
check("CADUCADO: vivo + confirmado vencido",
      est({"id": "b", "status": "vivo", "confirmado_en": "2026-05-01T00:00:00Z", "ttl_dias": 10}) == fd.CADUCADO)
check("NO_VERIFICABLE: gate humano abierto sin confirmar",
      est({"id": "c", "status": "vivo", "gate_humano": "G1", "confirmado_en": None, "ttl_dias": 14}) == fd.NO_VERIFICABLE)
check("SIN_CONFIRMAR: vivo, sin gate, sin fecha",
      est({"id": "d", "status": "vivo", "confirmado_en": None, "ttl_dias": 14}) == fd.SIN_CONFIRMAR)
check("NO_CUENTA: status superado no entra en completo",
      est({"id": "e", "status": "superado", "confirmado_en": "2020-01-01T00:00:00Z", "ttl_dias": 1}) == fd.NO_CUENTA)
check("NO_VERIFICABLE: fecha ilegible → fail-closed",
      est({"id": "f", "status": "vivo", "confirmado_en": "ayer", "ttl_dias": 14}) == fd.NO_VERIFICABLE)
check("NO_VERIFICABLE: ttl ilegible → fail-closed",
      est({"id": "g", "status": "vivo", "confirmado_en": "2026-06-20T00:00:00Z", "ttl_dias": "muchos"}) == fd.NO_VERIFICABLE)
check("NO_VERIFICABLE: confirmado_en en el FUTURO → fail-closed",
      est({"id": "h", "status": "vivo", "confirmado_en": "2027-01-01T00:00:00Z", "ttl_dias": 30}) == fd.NO_VERIFICABLE)
check("NO_VERIFICABLE: ttl_dias <= 0 (fuera de rango) → fail-closed",
      est({"id": "i", "status": "vivo", "confirmado_en": "2026-06-20T00:00:00Z", "ttl_dias": 0}) == fd.NO_VERIFICABLE)
check("NO_VERIFICABLE: ttl_dias disparatado (>36500 d) → fail-closed",
      est({"id": "j", "status": "vivo", "confirmado_en": "2026-06-20T00:00:00Z", "ttl_dias": 999999}) == fd.NO_VERIFICABLE)
check("NO_VERIFICABLE: ttl_dias BOOLEANO (true) → fail-closed (bool no es entero válido)",
      est({"id": "k", "status": "vivo", "confirmado_en": "2026-06-20T00:00:00Z", "ttl_dias": True}) == fd.NO_VERIFICABLE)


# ── agregado: entregable solo si todas las vivas están FRESCO ────────────────
todas_frescas = [
    {"id": "x", "status": "vivo", "confirmado_en": "2026-06-25T00:00:00Z", "ttl_dias": 30},
    {"id": "y", "status": "superado", "confirmado_en": None, "ttl_dias": 1},  # no cuenta
]
_, entregable, bloq = fd.evaluar_frescura(todas_frescas, AHORA)
check("dosier con todas las vivas frescas → entregable", entregable and not bloq)

con_caducada = todas_frescas + [{"id": "z", "status": "vivo", "confirmado_en": "2026-01-01T00:00:00Z", "ttl_dias": 7}]
_, entregable, bloq = fd.evaluar_frescura(con_caducada, AHORA)
check("una vivo caducada → NO entregable (fail-closed)", (not entregable) and any("z" in b for b in bloq))

# pieza no-dict (línea ilegible) → ERROR, fail-closed
res, entregable, bloq = fd.evaluar_frescura(["esto no es un objeto"], AHORA)
check("pieza no-objeto → ERROR fail-closed", (not entregable) and res[0]["estado"] == fd.ERROR)


# ── cargar_jsonl: una línea mala NO se ignora en silencio ────────────────────
tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
tmp.write('{"id":"ok","status":"vivo","confirmado_en":"2026-06-25T00:00:00Z","ttl_dias":30}\n')
tmp.write('{ esto no es json valido \n')
tmp.write('\n')  # línea en blanco se ignora (eso sí está bien)
tmp.close()
piezas, errores = fd.cargar_jsonl(tmp.name)
check("cargar_jsonl reporta la línea malformada (no la traga)", len(errores) == 1)
check("main con JSONL malo → exit 2 (fail-closed)", fd.main([tmp.name]) == 2)
os.unlink(tmp.name)


print("RESULTADO frescura_dosier: %d OK, %d fallos" % (total - fallos, fallos))
print("✅ FRESCURA DEL DOSIER EN VERDE" if not fallos else "❌ revisar fallos")
sys.exit(0 if not fallos else 1)
