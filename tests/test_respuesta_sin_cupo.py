#!/usr/bin/env python3
"""test_respuesta_sin_cupo.py — contestarle a {{TITULAR}} no gasta el cupo de avisos (8-oct-2026).

El fallo real: le escribió a Vega por Telegram, Vega contestó, y la respuesta se aplazó al parte
porque ya se habían entregado 3 avisos ese día. Ella vio silencio.
  1. con el cupo agotado, un aviso normal se aplaza (el portero de ruido sigue funcionando)
  2. con el cupo agotado, una respuesta SÍ se entrega
  3. el dispatcher usa report-respuesta cuando el encargo vino de Telegram, y report en el resto
  4. el bot contesta con categoria="respuesta"
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="resp_cupo_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_TEST_BATTERY"] = "1"   # mutis de salida.py: este test no puede escribirle a {{TITULAR}}
sys.path.insert(0, os.path.join(ROOT, "tools"))
import salida  # noqa: E402

_fail = 0


def ok(cond, name):
    global _fail
    if not cond:
        _fail += 1
        print("  ✗ %s" % name)


enviados = []
salida.send = lambda *a, **k: (enviados.append((a, k)) or salida._result(True, False, "entregado (falso)"))
salida._presupuesto_agotado = lambda: True
salida._en_silencio = lambda cfg=None: False
salida._aplazar = lambda text, fuente="": None

r = salida.report_to_titular("aviso de rutina")
ok(r.get("aplazado") and not enviados, "1: con el cupo agotado, el aviso normal se aplaza (%s)" % r)

r = salida.report_to_titular("esto es lo que me preguntabas", categoria="respuesta")
ok(not r.get("aplazado") and len(enviados) == 1,
   "2: ⭐ la respuesta a su mensaje se entrega aunque el cupo esté agotado (%s)" % r)

disp = open(os.path.join(ROOT, "tools", "btp_dispatcher.sh")).read()
ok('telegram*) cmd_rep="report-respuesta"' in disp and '*) cmd_rep="report"' in disp,
   "3: ⭐ el dispatcher entrega como respuesta lo que vino de Telegram, y como aviso lo demás")
src = open(os.path.join(ROOT, "tools", "salida.py")).read()
ok('"report-respuesta"' in src, "3: salida.py acepta report-respuesta")
bot = open(os.path.join(ROOT, "tools", "bot_telegram.py")).read()
ok('kw.setdefault("categoria", "respuesta")' in bot, "4: ⭐ el bot contesta como respuesta")

if _fail:
    print("❌ test_respuesta_sin_cupo: %d fallo(s)" % _fail)
    sys.exit(1)
print("✅ test_respuesta_sin_cupo: contestarle no gasta el cupo de avisos, y el portero de ruido sigue en pie")
