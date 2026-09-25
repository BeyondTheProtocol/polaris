#!/usr/bin/env python3
"""tests/test_seguimiento_hecho_cuando.py — el criterio de hecho se fija al abrir y se ve al cerrar.

POR QUÉ (24-sep-2026). Del post de Andrés Anaya («saber dónde quiero terminar antes de empezar»),
consejero-arquitectura concluyó que la Truth Table ya la cubren el Tablero y `deuda.py --test`, pero
que ninguna tarea declaraba al nacer CUÁNDO está hecha. `hecho_cuando` es ese criterio: opcional
(no se rechaza ni se avisa sin él), sobrevive a los updates parciales y vuelve en cada cierre para
comprobarlo. La cola no lo necesita (su `prueba` ya es eso) y la deuda tampoco (se cierra con test).
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OK, FALLOS = [], []


def ok(msg, cond, extra=""):
    (OK if cond else FALLOS).append(msg)
    print(("  ✅ " if cond else "  ❌ ") + msg + (("  " + extra) if extra and not cond else ""))


tmp = tempfile.mkdtemp(prefix="test-seg-hecho-cuando-")
os.environ["BTP_STATE_DIR"] = tmp
sys.path.insert(0, os.path.join(ROOT, "tools"))
import seguimiento as sg  # noqa: E402
import esquema_estado  # noqa: E402

# Orígenes que NO disparan el aviso de tarea nueva: el test no le manda nada a {{TITULAR}}.
if any(o in sg.ORIGENES_AUTONOMOS_AVISO for o in ("manual",)):
    print("  ❌ el origen del test avisa por Telegram: no se corre para no mandarle nada")
    sys.exit(1)

sg.SEG = os.path.join(tmp, "seguimiento.json")
ok("el test aísla el Tablero fuera del sistema vivo", sg.SEG.startswith(tmp))
json.dump({"hilos": []}, open(sg.SEG, "w"))


def hilo(hid):
    return next(h for h in sg.load_seguimiento()["hilos"] if h["id"] == hid)


# 1. Al crear, el criterio se guarda tal cual (recortado de espacios).
CRIT = "tests/test_x.py pasa dentro de test_all.sh"
a = sg.crear_tarea("Añadir freno X", origen="manual", objetivo_ned="taller",
                   hecho_cuando="  %s  " % CRIT)
ok("crear_tarea guarda hecho_cuando", hilo(a).get("hecho_cuando") == CRIT,
   "-> %r" % hilo(a).get("hecho_cuando"))

# 2. Opcional: sin él la tarea entra igual, con "" (ni se pierde ni se rellena con plantilla).
b = sg.crear_tarea("Tarea sin criterio", origen="manual", objetivo_ned="taller")
ok("sin criterio entra con hecho_cuando vacío", hilo(b).get("hecho_cuando") == "")

# 3. Un update PARCIAL (sin el campo) no lo borra: `{**h, **nuevo}` lo machacaría sin el freno.
sg.add_hilo({"id": a, "titulo": "Añadir freno X", "estado": "en_curso", "origen": "manual",
             "objetivo_ned": "taller"})
ok("un update sin hecho_cuando conserva el criterio", hilo(a).get("hecho_cuando") == CRIT,
   "-> %r" % hilo(a).get("hecho_cuando"))

# 4. Un update que SÍ lo trae lo cambia.
sg.add_hilo({"id": b, "titulo": "Tarea sin criterio", "estado": "esperando", "origen": "manual",
             "objetivo_ned": "taller", "hecho_cuando": "el PDF está en la bóveda"})
ok("un update con hecho_cuando lo cambia", hilo(b).get("hecho_cuando") == "el PDF está en la bóveda")

# 5. Entrada rara (de fuera): no-str → "", texto enorme → recortado. Nunca excepción.
c = sg.add_hilo({"titulo": "Hilo con basura", "origen": "manual", "objetivo_ned": "taller", "hecho_cuando": {"x": 1}})
ok("hecho_cuando no-str se normaliza a vacío", hilo(c).get("hecho_cuando") == "")
d = sg.add_hilo({"titulo": "Hilo largo", "origen": "manual", "objetivo_ned": "taller", "hecho_cuando": "z" * 5000})
ok("hecho_cuando largo se recorta al tope", len(hilo(d)["hecho_cuando"]) == sg.HECHO_CUANDO_MAX)

# 6. El cierre DEVUELVE el criterio, por cada camino de cierre.
h = sg.cerrar_tarea(a)
ok("cerrar_tarea devuelve el hilo con su criterio", bool(h) and h.get("hecho_cuando") == CRIT)
sg.reabrir_tarea(a)
ok("reabrir no borra el criterio", hilo(a).get("hecho_cuando") == CRIT)
r = sg.cerrar_por_texto("hecho lo del freno X")
ok("cerrar_por_texto devuelve el criterio",
   (r.get("cerrado") or {}).get("hecho_cuando") == CRIT, "-> %r" % r)
sg.reabrir_tarea(a)
sg.guardar_indice_hoy([a, b])
r = sg.cerrar_por_indice([1, 2])
crits = [x.get("hecho_cuando") for x in r["cerrados"]]
ok("cerrar_por_indice devuelve el criterio de cada cerrado",
   crits == [CRIT, "el PDF está en la bóveda"], "-> %r" % crits)

# 7. El esquema del Tablero sigue en regla con el campo nuevo.
rotos, _n = esquema_estado.revisar(sg.SEG)
ok("esquema_estado no ve hilos rotos por el campo nuevo", not rotos, "-> %r" % rotos)

# 8. Telegram: el cierre enseña el criterio, salvo si trae PII (entonces remite al Tablero).
try:
    import bot_telegram as bot  # noqa: E402
    suf = bot._criterio_al_cerrar([{"hecho_cuando": CRIT}, {"hecho_cuando": ""}])
    ok("Telegram muestra «comprueba: …» con el criterio", suf == " · comprueba: " + CRIT,
       "-> %r" % suf)
    suf = bot._criterio_al_cerrar([{"hecho_cuando": "responde a alguien@ejemplo.com"}])
    ok("un criterio con PII no se repite por Telegram",
       "@" not in suf and "Tablero" in suf, "-> %r" % suf)
    ok("sin criterio no añade nada", bot._criterio_al_cerrar([{"titulo": "x"}]) == "")
except Exception as e:  # noqa: BLE001
    ok("bot_telegram importable para probar el cierre", False, "-> %r" % e)

# 9. Vega (quien crea casi todas las tareas autónomas) sabe que tiene que escribirlo, y no inventarlo.
vega = open(os.path.join(ROOT, ".claude", "agents", "asistente.md"), encoding="utf-8").read()
ok("Vega tiene la instrucción de escribir hecho_cuando al crear", "`hecho_cuando`" in vega
   and "no lo inventes" in vega)

subprocess.run(["rm", "-rf", tmp], capture_output=True)
print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS
      else "test_seguimiento_hecho_cuando: %d/%d OK" % (len(OK), len(OK)))
sys.exit(1 if FALLOS else 0)
