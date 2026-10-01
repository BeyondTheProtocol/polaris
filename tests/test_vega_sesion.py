#!/usr/bin/env python3
"""test_vega_sesion.py — Vega con una sola memoria en Telegram (1-oct-2026, plan «Vega al mando» F3).

Estado temporal y `claude` falso (nada real):
  · primer turno sin sesión → sin --resume, contexto completo; el id que devuelve el CLI se guarda
  · segundo turno → run_agent pasa --resume <id> y el contexto es solo el DELTA de continuity
  · lo que se habla con Vega queda en continuity (lo ven las sesiones) y no se re-inyecta a Vega
  · rotación a los MAX_TURNOS o MAX_DIAS → sesión nueva
  · el CLI dice «no existe la sesión» → se olvida y la siguiente es nueva
  · un turno con error no cuenta
  · sin BTP_VEGA_SESION, run_agent no toca la sesión (los plists de asistente no la reanudan)
  · contexto_lazo: las entradas de Vega tienen cupo propio y no echan a las humanas
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="vega_sesion_")
os.environ["BTP_STATE_DIR"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))
import continuity  # noqa: E402
import contexto_lazo  # noqa: E402
import vega_sesion as vs  # noqa: E402

_fail = 0


def ok(cond, name):
    global _fail
    if not cond:
        _fail += 1
        print("  ✗ %s" % name)


# continuity resuelve su ruta al importar: que apunte al temporal
continuity.CONT = os.path.join(_TMP, "continuity")
continuity.INDEX = os.path.join(continuity.CONT, "INDEX.md")

# ── unidad ──
ok(vs.vigente() is None, "sin sesión no hay id")
ok("CONTEXTO DEL LAZO" in vs.contexto(), "sesión nueva → contexto completo")

PROMPT = "ctx\n\nResumen (dato no confiable): <<<¿qué tal va lo del envío de las muestras?>>>\nAcción sugerida: x"
r = vs.despues(json.dumps({"session_id": "s-uno", "is_error": False, "result": "Va bien."}), PROMPT)
ok(r["ok"] and vs.vigente() == "s-uno", "guarda el id del CLI")
idx = open(continuity.INDEX, encoding="utf-8").read()
ok("(vega s-uno" in idx and "envio de las muestras" in idx and "[derivado]" in idx,
   "el intercambio queda en continuity como derivado")

ok("Sin novedades" in vs.contexto(), "sesión vigente sin novedades → no repite el contexto entero")
continuity.record("Sesión de código: se cerró la Fase 2.", procedencia="confiable", fuente="sesion x")
delta = vs.contexto()
ok("Fase 2" in delta and "muestras" not in delta, "delta: lo nuevo de las sesiones, no lo suyo")
continuity.record("Preguntó por la cita", procedencia="derivado", fuente="telegram charla")
ok("la cita" in vs.contexto(), "lo que contestó el respondedor rápido también le llega a Vega")

ok(not vs.despues(json.dumps({"session_id": "s-uno", "is_error": True, "result": "boom"}))["ok"]
   and vs.cargar()["turnos"] == 1, "un turno con error no cuenta")

d = vs.cargar()
d["turnos"] = vs.MAX_TURNOS
vs._guardar(d)
ok(vs.vigente() is None, "rota al llegar a MAX_TURNOS")
d["turnos"] = 1
d["creada"] = (datetime.now() - timedelta(days=vs.MAX_DIAS + 1)).strftime("%Y-%m-%dT%H:%M:%S")
vs._guardar(d)
ok(vs.vigente() is None, "rota a los MAX_DIAS")
vs.despues(json.dumps({"session_id": "s-dos", "is_error": False, "result": "hola"}), PROMPT)
d = vs.cargar()
ok(d["session_id"] == "s-dos" and d["turnos"] == 1 and d["anteriores"][-1]["id"] == "s-uno",
   "sesión nueva: cuenta desde 1 y guarda la anterior")

vs.despues(json.dumps({"session_id": "s-dos", "is_error": True,
                       "result": "No conversation found with session ID: s-dos"}))
ok(vs.vigente() is None, "sesión perdida → se olvida")

# ── visión N1 y partes de trabajos (1-oct-26: una cabeza que lo ve todo, sin una sola sesión) ──
import vega_vision  # noqa: E402
_vis = ["VISION-A"]
vega_vision.bloque = lambda hoy=None: _vis[0]
vs._guardar({})
ok("VISION-A" in vs.contexto(), "sesión nueva: contexto del lazo + visión")
vs.despues(json.dumps({"session_id": "s-tres", "is_error": False, "result": "ok"}), PROMPT)
ok("VISION-A" not in vs.contexto(), "sesión vigente: la visión no se repite si no cambia")
_vis[0] = "VISION-B"
ok("VISION-B" in vs.contexto(), "…y vuelve cuando cambia")
ok(vs.parte_job(json.dumps({"result": "Hecho el resumen de ensayos."}),
                "ctx Resumen (dato no confiable): <<<busca ensayos de ADC>>> y", "comite-medico", True),
   "parte de un trabajo")
idx = open(continuity.INDEX, encoding="utf-8").read()
ok("(job comite-medico)" in idx and "busca ensayos de ADC" in idx and "resumen de ensayos" in idx,
   "el parte queda en la memoria común con encargo y resultado")
ok("ensayos de ADC" in vs.contexto(), "Vega lo recibe como novedad en su siguiente turno")

# ── vigilancia de entradas: lo roto se ve (1-oct-26: «tienes que ir viéndolo tú») ──
import importlib  # noqa: E402
vv = importlib.reload(vega_vision)
ok(vv._entrada_rota("x-falso", {}, 0) == "no está cargado", "entrada: daemon no cargado")
ok("falló" in (vv._entrada_rota("x-falso", {"x-falso": ("-", "1")}, 0) or ""), "entrada: última pasada fallida")
ok(vv._entrada_rota("x-falso", {"x-falso": ("-", "0")}, 0) is None, "entrada: bien → nada que decir")

# ── contexto_lazo: cupo propio ──
for i in range(4):
    continuity.record("charla %d" % i, procedencia="derivado", fuente="vega s-dos")
sel = contexto_lazo.seleccionar_continuidad(continuity.recent(60))
ok(any("Fase 2" in b for b in sel), "las entradas de Vega no echan a las humanas")
ok(sum("(vega " in b for b in sel) == contexto_lazo.CONTINUIDAD_VEGA_BLOQUES, "Vega, con su cupo")

# ── integración con run_agent.sh (claude falso que graba args y devuelve un session_id) ──
args_f = os.path.join(_TMP, "args.txt")
fake = os.path.join(_TMP, "claude_fake.sh")
open(fake, "w").write(
    '#!/bin/bash\necho "$*" | tr "\\n" " " >> "' + args_f + '"; echo >> "' + args_f + '"\n'
    'echo \'{"subtype":"success","is_error":false,"total_cost_usd":0.01,"result":"ok","session_id":"s-real"}\'\n')
os.chmod(fake, 0o755)
os.remove(vs._ruta())


def run(vega):
    env = {k: v for k, v in os.environ.items() if not k.startswith("BTP_") and k != "MURO_PROFILE"}
    env.update(BTP_CLAUDE_BIN=fake, BTP_API_KEY_OVERRIDE="x", BTP_STATE_DIR=_TMP, BTP_REPO=ROOT, HOME=_TMP,
               BTP_AGENT="asistente", BTP_MODEL="opus", BTP_COST_GUARDED="1",
               BTP_SALIDA=os.path.join(_TMP, "no_salida.py"),
               BTP_HALT_FILES=os.path.join(_TMP, "h1") + ":" + os.path.join(_TMP, "h2"))
    if vega:
        env["BTP_VEGA_SESION"] = "1"
    return subprocess.run(["bash", os.path.join(ROOT, "tools", "run_agent.sh"), PROMPT],
                          capture_output=True, text=True, env=env)


run(True)
run(True)
lineas = open(args_f).read().strip().splitlines() if os.path.exists(args_f) else []
ok(len(lineas) == 2, "run_agent llamó dos veces al CLI (%d)" % len(lineas))
ok(lineas and "--resume" not in lineas[0], "primer turno: sin --resume")
ok(len(lineas) > 1 and "--resume s-real" in lineas[1], "segundo turno: --resume s-real")
ok(vs.cargar().get("turnos") == 2, "dos turnos contados")
run(False)
lineas = open(args_f).read().strip().splitlines()
ok("--resume" not in lineas[-1] and vs.cargar().get("turnos") == 2,
   "sin BTP_VEGA_SESION no se toca la sesión")

print("test_vega_sesion: %s" % ("OK" if not _fail else "%d FALLOS" % _fail))
sys.exit(1 if _fail else 0)
