#!/usr/bin/env python3
"""test_continuidad_contexto.py — qué continuidad entra al arrancar (29-sep-2026, Fase 1.2).

Con la continuidad automática el INDEX se llena solo. Si el arranque siguiera mostrando «los 2
últimos bloques», los resúmenes automáticos echarían fuera a los humanos. Se comprueba:
  · entran los 2 últimos humanos, aunque haya automáticos más nuevos;
  · entran como mucho 2 automáticos, y solo de las últimas 24 h (lo viejo, rancio, no entra);
  · el automático se recorta a su tope (la dieta de contexto sigue viva).
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="cont_ctx_")
os.environ["BTP_STATE_DIR"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))
import continuity  # noqa: E402
import contexto_lazo  # noqa: E402

_fail = 0


def ok(cond, name):
    global _fail
    if not cond:
        _fail += 1
        print("  ✗ %s" % name)


def ts(horas_atras):
    return (datetime.now() - timedelta(hours=horas_atras)).strftime("%Y-%m-%dT%H:%M:%S")


bloques = [
    (60, "", "HUMANO-1"),
    (50, " (auto SessionEnd aaaa)", "AUTO-VIEJO"),
    (10, "", "HUMANO-2"),
    (5, " (auto SessionEnd bbbb)", "AUTO-A"),
    (4, "", "HUMANO-3"),
    (3, " (auto PreCompact cccc)", "AUTO-B"),
    (1, " (auto SessionEnd dddd)", "AUTO-C " + "x" * 2000),
]
os.makedirs(continuity.CONT, exist_ok=True)
with open(continuity.INDEX, "w", encoding="utf-8") as fh:
    fh.write("# Continuidad del lazo\n")
    for horas, fuente, texto in bloques:
        fh.write("\n## %s  [confiable]%s\n%s\n" % (ts(horas), fuente, texto))

salida = contexto_lazo.bloque(con_estilo_telegram=False)

ok("HUMANO-2" in salida and "HUMANO-3" in salida, "los 2 últimos humanos entran")
ok("HUMANO-1" not in salida, "el humano antiguo no entra (dieta: 2)")
ok("AUTO-B" in salida and "AUTO-C" in salida, "entran los 2 automáticos más recientes")
ok("AUTO-A" not in salida, "no más de 2 automáticos")
ok("AUTO-VIEJO" not in salida, "⭐ un automático de hace más de 24 h no entra (rancio)")
ok("x" * (contexto_lazo.CONTINUIDAD_AUTO_MAX_CHARS + 50) not in salida,
   "el automático largo se recorta a su tope")
ok(salida.index("HUMANO-3") < salida.index("AUTO-C"), "en orden cronológico")

if _fail:
    print("❌ test_continuidad_contexto: %d fallo(s)" % _fail)
    sys.exit(1)
print("✅ test_continuidad_contexto: humanos no desplazados, automáticos recientes y recortados")
