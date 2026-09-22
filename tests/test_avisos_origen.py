#!/usr/bin/env python3
"""test_avisos_origen.py — que los avisos NO MIENTAN y NO CALLEN (incidente del 14-jul-2026).

Un diagnóstico por ssh llamó a `ia.ask(critico_tarea=True)`; el binario `claude` no estaba en el
PATH de esa sesión → todos los cerebros "fallaron" → `_parar()` le disparó a {{TITULAR}} un 🔴 "tarea
clínica PARADA" que era MENTIRA. Una alarma clínica falsa erosiona lo único que no puede
erosionarse: que cuando Vega dice 🔴, ella se lo crea.

La decisión de diseño que se prueba aquí: NO se suprime NADA (suprimir es como se pierden las
alarmas de verdad — un mal deploy que se lleve el binario se ve EXACTAMENTE igual que un ssh sin
PATH). Se dice la VERDAD y se ESTAMPA el ORIGEN.

Casos:
  ⭐ ESTRELLA  binario ausente + tarea crítica → PARADO, pero ⚠️ ambiental, NO 🔴 clínico
     ...y aun así SE ENTREGA (no se calla: si el lazo real pierde el binario, hay que enterarse)
     sello de origen: fuera de launchd → el texto lleva "(origen: ...)"
     sello de origen: DENTRO del lazo (XPC_SERVICE_NAME=com.btp.*) → el texto va LIMPIO
     la boca NO está muda: un aviso del lazo LLEGA de verdad a _DELIVERERS
     alerta_critica: lista de llamantes CERRADA (ni celebraciones ni avisos normales)
"""
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="avisos_origen_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_HALT_FILES"] = os.path.join(_TMP, "nh_a") + ":" + os.path.join(_TMP, "nh_b")
os.environ["BTP_PERIPHERIES"] = os.path.join(_TMP, "peripheries.json")
os.environ["BTP_HEALTH_TTL"] = "0"
# OJO: aquí NO ponemos BTP_TEST_BATTERY. Este test necesita que salida.send() llegue hasta la boca
# para comprobar que NO está muda — y la neutraliza sustituyendo _DELIVERERS (la costura de verdad).
os.makedirs(os.path.join(_TMP, "cost"), exist_ok=True)
json.dump({"tope_diario_usd": 30.0, "tope_job_usd": 3.0},
          open(os.path.join(_TMP, "cost", "limits.json"), "w"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import ia          # noqa: E402
import salida      # noqa: E402

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# ── Arnés: la boca es un doble; nada sale de esta máquina ────────────────────────────────────
ENTREGADOS = []


def _fake_deliver(chat_id, text, reply_to=None):
    ENTREGADOS.append(text)
    return True, "fake"


salida._DELIVERERS = {"telegram": _fake_deliver}
salida._self_chatid = lambda: "123456"

CLAUDE = {"name": "claude", "kind": "claude", "destino": "cleared:claude", "trusted": True,
          "free": False, "enabled": True, "capability": 9}
json.dump({"cerebros": [CLAUDE]}, open(os.environ["BTP_PERIPHERIES"], "w"))


def _lanzar_critico():
    """Corre ia.ask crítico con el binario del cerebro APUNTANDO A LA NADA (reproduce el incidente:
    FileNotFoundError al ejecutar el subproceso)."""
    ENTREGADOS.clear()
    try:                                   # el anti-spam es por motivo+cooldown: limpiarlo entre casos
        os.remove(os.path.join(_TMP, "ia", "aviso_parado.json"))
    except OSError:
        pass
    os.environ["BTP_CLAUDE_BIN"] = os.path.join(_TMP, "no-existe-este-binario")
    return ia.ask("da igual", critico_tarea=True)


# ── ⭐ 1. Binario ausente → PARADO, pero AMBIENTAL, no clínico ───────────────────────────────
os.environ.pop("XPC_SERVICE_NAME", None)          # simula: NO estamos en el lazo (ssh/manual)
r = _lanzar_critico()

ok(r.get("parado") is True, "el freno clínico SIGUE parando (no se sirve con un flojo)")
ok(r.get("ambiental") is True, "el fallo se clasifica como AMBIENTAL, no como avería del servicio")
ok("no es que el servicio esté caído" in (r.get("motivo") or ""),
   "el motivo dice la verdad (no culpa al servicio ni al saldo)")

# ⭐ NO viene del lazo → NO se pinga a {{TITULAR}} (el runner ve el retorno; el canario cubre el lazo).
# Antes, cualquier diagnóstico le disparaba un 🔴/⚠️ al teléfono cada pocos minutos.
ok(len(ENTREGADOS) == 0, "⭐ un parón FUERA del lazo NO pinga a {{TITULAR}} (era el spam del 15-jul)")
ok(r.get("es_lazo") is False, "el retorno marca es_lazo=False")

# ── 2. DENTRO del lazo + fallo ambiental → SÍ avisa, pero ⚠️ (no 🔴) ──────────────────────────
ENTREGADOS.clear()
os.remove(os.path.join(_TMP, "ia", "aviso_parado.json")) if os.path.exists(
    os.path.join(_TMP, "ia", "aviso_parado.json")) else None
os.environ["XPC_SERVICE_NAME"] = "com.btp.asistente"       # así lo inyecta launchd de verdad
os.environ["BTP_CLAUDE_BIN"] = os.path.join(_TMP, "no-existe-este-binario")
r2 = ia.ask("da igual", critico_tarea=True)
ok(r2.get("es_lazo") is True, "dentro del lazo el retorno marca es_lazo=True")
ok(len(ENTREGADOS) == 1, "dentro del lazo SÍ se avisa (la boca no está muda)")
av2 = ENTREGADOS[0] if ENTREGADOS else ""
ok("⚠️" in av2 and "🔴" not in av2, "lazo + ambiental → ⚠️, no 🔴")

# ── 3. DENTRO del lazo + fallo REAL (solo cerebros flojos) → 🔴 clínico de verdad ─────────────
ENTREGADOS.clear()
os.remove(os.path.join(_TMP, "ia", "aviso_parado.json")) if os.path.exists(
    os.path.join(_TMP, "ia", "aviso_parado.json")) else None
LOCAL_3B = {"name": "local-3b", "kind": "openai_local", "destino": "local:ollama",
            "trusted": True, "free": True, "enabled": True, "capability": 4}
json.dump({"cerebros": [LOCAL_3B]}, open(os.environ["BTP_PERIPHERIES"], "w"))
r3 = ia.ask("da igual", critico_tarea=True)
ok(r3.get("parado") is True and r3.get("ambiental") is False, "lazo + solo-flojo = parado real, no ambiental")
av3 = ENTREGADOS[0] if ENTREGADOS else ""
ok("🔴" in av3, "⭐ un parón REAL del lazo SÍ es 🔴 (nunca se suprime la alarma de verdad)")
# restaura el registro con claude para lo que sigue
json.dump({"cerebros": [CLAUDE]}, open(os.environ["BTP_PERIPHERIES"], "w"))

# ── 4. Sello de origen en un report normal: fuera del lazo estampa, dentro no ─────────────────
ENTREGADOS.clear()
os.environ.pop("XPC_SERVICE_NAME", None)
salida.report_to_titular("hola fuera del lazo", fuente="test")
fuera = ENTREGADOS[0] if ENTREGADOS else ""
ok("origen:" in fuera, "un report normal fuera del lazo lleva SELLO DE ORIGEN")
ENTREGADOS.clear()
os.environ["XPC_SERVICE_NAME"] = "com.btp.asistente"
salida.report_to_titular("hola desde el lazo", fuente="test")
limpio = ENTREGADOS[0] if ENTREGADOS else ""
ok("origen:" not in limpio and "hola desde el lazo" in limpio,
   "dentro del lazo el report normal va LIMPIO e íntegro (la boca no está muda)")

# ── 5. alerta_critica: lista de llamantes CERRADA ────────────────────────────────────────────
# Atraviesa el HALT, el silencio nocturno y la casa de estilo. Es el canal de SOCORRO: si se usa
# para avisos normales (o para celebraciones 🎉, como se hacía) se desgasta y deja de significar nada.
PERMITIDOS = {"codigo_rojo.py", "errores.py", "salida.py", "healthcheck.py"}
intrusos = []
for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "tools")):
    for fn in files:
        if not fn.endswith(".py") or fn in PERMITIDOS:
            continue
        p = os.path.join(dirpath, fn)
        try:
            src = open(p, encoding="utf-8").read()
        except Exception:
            continue
        if re.search(r"\balerta_critica\s*\(", src):
            intrusos.append(fn)
ok(not intrusos, "solo los llamantes legítimos usan alerta_critica (intrusos: %s)" % (intrusos,))

print("test_avisos_origen: %d ok, %d fallos" % (_pass, _fail))
sys.exit(1 if _fail else 0)
