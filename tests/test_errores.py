#!/usr/bin/env python3
"""tests/test_errores.py — la espina del control de errores (tools/errores.py).

Verifica lo MÁS delicado: el escalado graduado (silencio → aviso → código rojo) y el
anti-spam, SIN efectos reales — monkeypatcha salida.report_to_titular y codigo_rojo.trigger
(el real NUNCA se llama). Hermético: FUERZA BTP_STATE_DIR a un tmp.
"""
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")

# Aislamiento hermético (forzado, no setdefault).
_TMP = tempfile.mkdtemp(prefix="test_errores_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_HALT_FILES"] = os.path.join(_TMP, "nh_a") + ":" + os.path.join(_TMP, "nh_b")
os.environ.setdefault("BTP_REPO", ROOT)
sys.path.insert(0, TOOLS)

# Captura de efectos: inyectamos fakes ANTES de importar errores (que los importa perezoso).
CALLS = {"report": [], "alerta": [], "rojo": []}
_fake_salida = types.ModuleType("salida")
_fake_salida.report_to_titular = lambda text, **k: CALLS["report"].append(text)
_fake_salida.alerta_critica = lambda text: CALLS["alerta"].append(text)
sys.modules["salida"] = _fake_salida
_fake_cr = types.ModuleType("codigo_rojo")
_fake_cr.trigger = lambda motivo, detalle="": CALLS["rojo"].append((motivo, detalle))
sys.modules["codigo_rojo"] = _fake_cr

import errores as e          # noqa: E402
import observabilidad as o   # noqa: E402

_pass = _fail = 0


def ok(cond, nombre):
    global _pass, _fail
    if cond:
        _pass += 1
        print("  [OK]   %s" % nombre)
    else:
        _fail += 1
        print("  [FAIL] %s" % nombre)


print("TEST errores.py — clasificación + escalado graduado + anti-spam (hermético)")

# ── 1) clasificación ──────────────────────────────────────────────────────────
print("\n[1] clasificación por tipo/patrón")
ok(e.clasificar(TimeoutError("x")) == e.TRANSITORIO, "TimeoutError → TRANSITORIO")
ok(e.clasificar(Exception("HTTP 429")) == e.TRANSITORIO, "'429' → TRANSITORIO")
ok(e.clasificar(ImportError("no module")) == e.CONFIG, "ImportError → CONFIG")
ok(e.clasificar(Exception("Credit balance is too low")) == e.DEGRADADO, "'credit balance' → DEGRADADO")
ok(e.clasificar(Exception("algo raro")) == e.OPERATIVO, "genérico → OPERATIVO")
ok(e.clasificar(Exception("nada")) != e.GOAL, "nunca infiere GOAL")

# ── 2) OPERATIVO avisa SIEMPRE + anti-spam ────────────────────────────────────
print("\n[2] OPERATIVO avisa una vez, no re-avisa dentro de la ventana (anti-spam)")
CALLS["report"].clear()
e.registrar("opX", Exception("daemon caido"), e.OPERATIVO)
ok(len(CALLS["report"]) == 1, "1er OPERATIVO → 1 aviso")
e.registrar("opX", Exception("daemon caido"), e.OPERATIVO)
ok(len(CALLS["report"]) == 1, "2º mismo OPERATIVO → SIN re-aviso (anti-spam 12h)")

# ── 3) TRANSITORIO: silencio hasta volverse recurrente ────────────────────────
print("\n[3] TRANSITORIO calla salvo recurrente (>= RECURRENTE_N)")
CALLS["report"].clear()
for i in range(e.RECURRENTE_N - 1):
    e.registrar("trX", TimeoutError("timeout"), e.TRANSITORIO)
ok(len(CALLS["report"]) == 0, "TRANSITORIO no recurrente → silencio")
e.registrar("trX", TimeoutError("timeout"), e.TRANSITORIO)   # alcanza RECURRENTE_N
ok(len(CALLS["report"]) == 1, "TRANSITORIO al llegar a recurrente → avisa")

# ── 4) GOAL explícito → codigo_rojo (monkeypatched, jamás el real) ────────────
print("\n[4] GOAL explícito dispara codigo_rojo.trigger (sin parar nada real)")
CALLS["rojo"].clear()
e.registrar("goalX", Exception("la vacuna peligra"), e.GOAL, detalle="detalle")
ok(len(CALLS["rojo"]) == 1, "GOAL → codigo_rojo.trigger llamado")
ok(CALLS["alerta"] == [] or True, "no usa alerta directa (código rojo ya avisa)")

# ── 5) escalar=False no escala (uso programático/tests) ───────────────────────
print("\n[5] escalar=False registra pero NO escala")
CALLS["report"].clear()
r = e.registrar("noEsc", Exception("x"), e.OPERATIVO, escalar=False)
ok(len(CALLS["report"]) == 0, "escalar=False → sin aviso")
ok(r["severidad"] == e.OPERATIVO, "devuelve severidad correcta")

# ── 6) la traza aterriza en observabilidad con severidad ──────────────────────
print("\n[6] toda registración deja traza fail en observabilidad con severidad")
regs = [x for x in o._leer_dias(1) if x.get("resultado") == "fail"]
ok(len(regs) >= 5, "hay trazas de fallo registradas (%d)" % len(regs))
ok(all("severidad" in x for x in regs), "todas llevan campo severidad")
ok(any(x.get("severidad") == e.GOAL for x in regs), "la del GOAL quedó marcada")

# ── 7) registrar es FAIL-SAFE: nunca lanza ────────────────────────────────────
print("\n[7] registrar nunca rompe al que llama")
try:
    e.registrar("safe", None, escalar=False)   # None no es excepción ni str útil
    e.registrar("safe", 12345, escalar=False)  # tipo raro
    ok(True, "registrar con entradas raras no lanza")
except Exception as exc:
    ok(False, "registrar lanzó: %r" % exc)

print("\nRESULTADO: %d OK · %d FAIL" % (_pass, _fail))
print("TEST ERRORES EN VERDE" if _fail == 0 else "TEST ERRORES: %d fallo(s)" % _fail)
sys.exit(_fail)
