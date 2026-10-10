#!/usr/bin/env python3
"""test_aviso_en_camino.py — la tabla de `errores.aviso_en_camino(res, categoria)`, la ÚNICA clasificación de «¿le llegó?»
que usan todos los llamantes de salida (10-oct-26). Carga el módulo por BTP_ERRORES_MODULO para que la campaña de
mutantes (tests/mutantes/aviso_en_camino.json) pueda apuntarlo a una versión rota."""
import importlib.util
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULO = os.environ.get("BTP_ERRORES_MODULO") or os.path.join(RAIZ, "tools", "errores.py")
sys.path.insert(0, os.path.join(RAIZ, "tools"))
spec = importlib.util.spec_from_file_location("errores_bajo_prueba", MODULO)
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)

fallos = []


def check(cond, desc):
    if not cond:
        fallos.append(desc)
        print("  ✗ " + desc)


ENTREGADO = {"delivered": True, "blocked": False, "reason": "entregado"}
APLAZADO = {"delivered": False, "blocked": False, "reason": "aplazado", "aplazado": True}
RETENIDO = {"delivered": False, "blocked": False, "reason": "retenido", "retenido": True}
OPERATIVO = {"delivered": False, "blocked": False, "reason": "operativo", "operativo": True}
DRY = {"delivered": False, "blocked": False, "reason": "dry", "dry": True}
BLOQUEADO = {"delivered": False, "blocked": True, "reason": "HALT"}
FALLO = {"delivered": False, "blocked": False, "reason": "algo raro"}

# (resultado, categoría, ¿en camino?, estado esperado)
TABLA = [
    (ENTREGADO, "humano", True, "entregado"),
    (APLAZADO, "humano", True, "aplazado"),
    (RETENIDO, "humano", True, "retenido"),
    (OPERATIVO, "humano", False, "operativo"),       # caer en el log NO es avisar a {{TITULAR}}
    (OPERATIVO, "operativo", True, "operativo"),     # …salvo que la categoría ES operativa
    (DRY, "humano", False, "dry"),
    (BLOQUEADO, "humano", False, "bloqueado"),
    (FALLO, "humano", False, "no_entregado"),
    (None, "humano", False, "desconocido"),
    ("texto", "humano", False, "desconocido"),
    (BLOQUEADO, "operativo", False, "bloqueado"),    # una categoría operativa bloqueada tampoco llegó
    (ENTREGADO, "operativo", True, "entregado"),
    (APLAZADO, "operativo", True, "aplazado"),
]
for res, cat, esperado, estado in TABLA:
    llega, est, _m = E.aviso_en_camino(res, cat)
    check(llega is esperado and est == estado, "aviso_en_camino(%r, %r) → (%s, %s), salió (%s, %s)"
          % (res, cat, esperado, estado, llega, est))
check(E.aviso_en_camino(ENTREGADO)[0] is True, "la categoría por defecto es humana y lo entregado llega")
check(E.aviso_en_camino(OPERATIVO)[0] is False, "por defecto (humana) un 'operativo' NO cuenta")
check(set(E.ESTADOS_EN_CAMINO) == {"entregado", "aplazado", "retenido"},
      "ESTADOS_EN_CAMINO es exactamente entregado/aplazado/retenido")

print("RESULTADO aviso_en_camino: %d fallos" % len(fallos))
print("✅ AVISO_EN_CAMINO EN VERDE" if not fallos else "❌ revisar fallos")
sys.exit(1 if fallos else 0)
