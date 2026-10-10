#!/usr/bin/env python3
"""test_salida_cola_honesta.py — las dos colas de `salida` dicen la verdad (10-oct-26).

  1. `_aplazar` tragaba la excepción y `report_to_titular` contestaba «aplazado» (= «llegará con el parte») aunque no
     hubiera escrito nada. Ahora, si no se pudo guardar, el resultado es BLOQUEADO (y quien llama no lo da por
     en camino).
  2. `flush_silencio` borraba el buffer del silencio nocturno ANTES de enviar el resumen. Con HALT o un fallo de
     red el resumen de la mañana se perdía entero. Ahora el buffer se borra SOLO tras entregar, y solo lo enviado
     (lo añadido mientras tanto se conserva).
Hermético: STATE temporal, sin entrega real."""
import importlib
import json
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))
TMP = tempfile.mkdtemp(prefix="salida_cola_")
os.environ["BTP_STATE_DIR"] = TMP
os.environ["BTP_TEST_BATTERY"] = "1"
import salida  # noqa: E402
importlib.reload(salida)
salida.STATE = TMP

fallos = []


def check(cond, desc):
    print(("  ✅ " if cond else "  ❌ ") + desc)
    if not cond:
        fallos.append(desc)


# ── 1. _aplazar honesto ─────────────────────────────────────────────────────────────────────────
salida._presupuesto_agotado = lambda: True
salida._self_chatid = lambda: "123"
open(os.path.join(TMP, "aplazados"), "w").write("no soy un directorio")      # fuerza el fallo de escritura
r = salida.report_to_titular("aviso rutinario", fuente="t")
check(r["delivered"] is False and r["blocked"] is True and not r.get("aplazado"),
      "aplazar que NO escribe → bloqueado, NO «aplazado»: %r" % (r,))
os.remove(os.path.join(TMP, "aplazados"))
salida.send = lambda *a, **k: {"delivered": True}       # el mensaje «a partir de ahora agrupo» no sale de verdad
r = salida.report_to_titular("aviso rutinario", fuente="t")
check(r.get("aplazado") is True and r["blocked"] is False, "aplazar que SÍ escribe → aplazado: %r" % (r,))
lineas = open(os.path.join(TMP, "aplazados", os.listdir(os.path.join(TMP, "aplazados"))[0])).read().splitlines()
check(len(lineas) == 1 and json.loads(lineas[0])["texto"] == "aviso rutinario", "…y de verdad quedó guardado")

# ── 2. flush_silencio: borrar solo tras entregar ────────────────────────────────────────────────
d = os.path.join(TMP, "notif")
os.makedirs(d, exist_ok=True)
hold = os.path.join(d, "holding-2026-10-09.jsonl")


def sembrar():
    with open(hold, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "x", "texto": "aviso de la noche 1"}) + "\n")
        f.write(json.dumps({"ts": "x", "texto": "aviso de la noche 2"}) + "\n")


enviados = []
salida.send = lambda canal, accion, dest, texto, **k: (enviados.append(texto),
                                                       {"delivered": False, "blocked": True, "reason": "HALT activo"})[1]
sembrar()
res = salida.flush_silencio()
check(os.path.exists(hold) and len(open(hold).read().splitlines()) == 2,
      "HALT: el buffer del silencio NO se borra (se reintenta): %r" % (res,))
check(enviados and "aviso de la noche 1" in enviados[0], "…y el resumen sí se intentó con el contenido")

salida.send = lambda canal, accion, dest, texto, **k: (
    open(hold, "a").write(json.dumps({"ts": "y", "texto": "llegó mientras se enviaba"}) + "\n"),
    {"delivered": True, "blocked": False, "reason": "entregado"})[1]
res = salida.flush_silencio()
quedan = open(hold).read().splitlines() if os.path.exists(hold) else []
check(res["delivered"] and len(quedan) == 1 and "mientras se enviaba" in quedan[0],
      "entregado: se borra lo enviado y se CONSERVA lo que llegó mientras tanto")
salida.send = lambda *a, **k: {"delivered": True}
salida.flush_silencio()
check(not os.path.exists(hold), "entregado el resto: el fichero desaparece")
res = salida.flush_silencio()
check(res["delivered"] is False and not res["blocked"], "sin nada retenido: «nada retenido», sin enviar")

print("RESULTADO salida_cola_honesta: %d fallos" % len(fallos))
print("✅ SALIDA_COLA_HONESTA EN VERDE" if not fallos else "❌ revisar fallos")
sys.exit(1 if fallos else 0)
