#!/usr/bin/env python3
"""test_run_agent_reserva_max.py — Max por defecto, API solo de reserva (29-sep-2026).

Decisión de {{TITULAR}}: «tirar de la cuenta de Max en vez de la API». Desde entonces la caja
`claude-suscripcion` admite lo crítico (nivel_min=critico) y la API medida solo entra de RESERVA,
una vez, cuando Max da límite en una tarea crítica. Aquí se comprueba con un `claude` falso que
responde según con qué credencial lo llaman:
  A) clínico + Max sano        → responde Max; la API no se toca (el guardián ya no lo desvía).
  B) clínico + Max al límite   → reserva: mismo modelo por la API, y la tarea sale.
  C) rutina + Max al límite    → sin reserva de API (lo rutinario no gasta dinero).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")
import json
import os
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="ra_max_")
_fail = 0


def ok(cond, name):
    global _fail
    if not cond:
        _fail += 1
        print("  ✗ %s" % name)


# claude falso: apunta qué credencial usó; con el token de Max responde según MAX_ESTADO.
_LOG = os.path.join(_TMP, "via.log")
_BIN = os.path.join(_TMP, "claude_fake.sh")
open(_BIN, "w").write(
    '#!/bin/bash\n'
    'if [ -n "$CLAUDE_CODE_OAUTH_TOKEN" ]; then echo max >> "' + _LOG + '";\n'
    '  if [ "$MAX_ESTADO" = limite ]; then echo \'{"api_error_status": 429, "is_error": true}\'; exit 0; fi\n'
    '  echo \'{"subtype":"success","is_error":false,"total_cost_usd":0.01,"result":"ok-max"}\'; exit 0; fi\n'
    'echo api >> "' + _LOG + '"\n'
    'echo \'{"subtype":"success","is_error":false,"total_cost_usd":0.01,"result":"ok-api"}\'\n')
os.chmod(_BIN, 0o755)


def correr(agent, max_estado, criticidad=None):
    if os.path.exists(_LOG):
        os.remove(_LOG)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("BTP_") and k not in ("MURO_PROFILE", "ANTHROPIC_API_KEY",
                                                   "CLAUDE_CODE_OAUTH_TOKEN")}
    env.update(BTP_CLAUDE_BIN=_BIN, BTP_API_KEY_OVERRIDE="x", BTP_COST_GUARDED="1",
               BTP_STATE_DIR=_TMP, BTP_AGENT=agent, BTP_MODEL="sonnet", BTP_REPO=ROOT,
               BTP_ORQUESTADOR="claude-suscripcion", MAX_ESTADO=max_estado,
               BTP_IA_FAKE="DESDE-CENTRALITA",
               BTP_HALT_FILES=os.path.join(_TMP, "nh_a") + ":" + os.path.join(_TMP, "nh_b"))
    if criticidad:
        env["BTP_CRITICIDAD"] = criticidad
    p = subprocess.run(["bash", os.path.join(ROOT, "tools", "run_agent.sh"), "analiza esto"],
                       capture_output=True, text=True, env=env)
    vias = open(_LOG).read().split() if os.path.exists(_LOG) else []
    return p.stdout, p.stderr, vias


# A) crítico con Max sano: va por Max y no toca la API
out, err, vias = correr("comite-medico", "sano", "critico")
ok("ok-max" in out, "A: lo crítico responde por Max (%r)" % out[-200:])
ok("api" not in vias, "A: la API no se toca con Max sano (%s)" % vias)
ok("vuelvo a la API medida" not in err, "A: el guardián ya no desvía lo crítico a la API")

# B) crítico con Max al límite: reserva por la API, una vez, y la tarea sale
out, err, vias = correr("comite-medico", "limite", "critico")
ok("ok-api" in out, "B: la reserva de API saca la tarea (%r)" % out[-200:])
ok(vias[:1] == ["max"] and vias.count("api") == 1, "B: primero Max, luego UNA vez API (%s)" % vias)
ok("reserva" in err, "B: deja rastro de que usó la reserva")

# C) rutina con Max al límite: nunca gasta API
out, err, vias = correr("tecnico", "limite")
ok("api" not in vias, "C: lo rutinario no usa la reserva de API (%s)" % vias)

if _fail:
    print("❌ test_run_agent_reserva_max: %d fallo(s)" % _fail)
    _sys.exit(1)
print("✅ test_run_agent_reserva_max: Max por defecto, API solo de reserva en lo crítico")
