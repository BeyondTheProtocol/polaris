#!/usr/bin/env python3
"""test_errores_entrega.py — `errores.registrar` dice la VERDAD sobre si el aviso llegó.

POR QUÉ EXISTE (10-oct-26). `errores._avisar` devolvía True si `salida.report_to_titular(texto)` no lanzaba excepción, sin
mirar su resultado. Pero `report_to_titular` devuelve un dict que distingue «entregado» de «aplazado al parte» (presupuesto
diario agotado), «guardado en el log operativo», «retenido por el silencio nocturno», «simulado» o «frenado» (HALT…). El
rojo SEMBRADO de la nocturna lo cazó: `avisado: true`, auditoría en «aplazado», y a {{TITULAR}} no le llegó nada. Afectaba a
todo el sistema de errores. Se fija:
  1. `_estado_entrega` clasifica cada forma de resultado de `salida`;
  2. `registrar(...)["avisado"]` es True SOLO si se entregó; `["entrega"]` dice qué pasó (aplazado, operativo, retenido,
     bloqueado, error, desconocido…) y `["entrega_motivo"]` el porqué;
  3. el mismo aviso dentro de la ventana anti-spam no se reenvía y lo dice (`antispam`), sin fingir que llegó;
  4. la ruta crítica (`alerta_critica`) se mide igual; `_avisar` sigue devolviendo bool para quien solo mira eso.
"""
import importlib.util
import os
import sys
import tempfile
import types

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULO = os.environ.get("BTP_ERRORES_MODULO") or os.path.join(RAIZ, "tools", "errores.py")
fallos = []


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


os.environ["BTP_STATE_DIR"] = tempfile.mkdtemp(prefix="errores_entrega_")
sys.path.insert(0, os.path.join(RAIZ, "tools"))
resultado = {"report": None, "critica": None, "llamadas": []}


def _fake_salida():
    m = types.ModuleType("salida")

    def report_to_titular(texto, **kw):
        resultado["llamadas"].append(("report", texto, kw))
        r = resultado["report"]
        if isinstance(r, Exception):
            raise r
        return r

    def alerta_critica(texto):
        resultado["llamadas"].append(("critica", texto, {}))
        r = resultado["critica"]
        if isinstance(r, Exception):
            raise r
        return r
    m.report_to_titular, m.alerta_critica = report_to_titular, alerta_critica
    return m


sys.modules["salida"] = _fake_salida()
spec = importlib.util.spec_from_file_location("errores", MODULO)
E = importlib.util.module_from_spec(spec)
sys.modules["errores"] = E
spec.loader.exec_module(E)

print("1) _estado_entrega clasifica lo que devuelve salida")
casos = [
    ({"delivered": True, "blocked": False, "reason": "entregado (x)"}, "entregado"),
    ({"delivered": False, "blocked": False, "reason": "aplazado al parte diario (presupuesto de avisos agotado)", "aplazado": True}, "aplazado"),
    ({"delivered": False, "blocked": False, "reason": "operativo: guardado en log", "operativo": True}, "operativo"),
    ({"delivered": False, "blocked": False, "reason": "retenido (silencio nocturno) → resumen a las 08:00", "retenido": True}, "retenido"),
    ({"delivered": False, "blocked": False, "reason": "dry: no se envió", "dry": True}, "dry"),
    ({"delivered": False, "blocked": True, "reason": "HALT activo: salida en pausa total"}, "bloqueado"),
    ({"delivered": False, "blocked": False, "reason": "???"}, "no_entregado"),
    (None, "desconocido"),
    ("entregado", "desconocido"),
]
for res, esperado in casos:
    est, motivo = E._estado_entrega(res)
    check(est == esperado, "%s → %s (salió %s)" % (str(res)[:60], esperado, est))
check(E._estado_entrega({"delivered": True, "aplazado": True})[0] == "entregado", "delivered manda sobre cualquier otra marca")

print("2) registrar().avisado es True SOLO si se entregó")
n = [0]


def registra(res, critico=False):
    n[0] += 1
    resultado["report"] = res
    return E.registrar("origen_%d" % n[0], "fallo de prueba", E.OPERATIVO, job="t", detalle="detalle")


r = registra({"delivered": True, "blocked": False, "reason": "entregado (x)"})
check(r["avisado"] is True and r["entrega"] == "entregado", "entregado → avisado True, entrega «entregado»")
r = registra({"delivered": False, "blocked": False, "reason": "aplazado al parte diario (presupuesto de avisos agotado)", "aplazado": True})
check(r["avisado"] is False and r["entrega"] == "aplazado" and "presupuesto" in r["entrega_motivo"],
      "APLAZADO al parte → avisado False, entrega «aplazado» y el motivo (era el bug: salía True)")
r = registra({"delivered": False, "blocked": False, "reason": "operativo", "operativo": True})
check(r["avisado"] is False and r["entrega"] == "operativo", "log operativo → avisado False")
r = registra({"delivered": False, "blocked": False, "reason": "retenido (silencio nocturno)", "retenido": True})
check(r["avisado"] is False and r["entrega"] == "retenido", "silencio nocturno → avisado False, «retenido»")
r = registra({"delivered": False, "blocked": True, "reason": "HALT activo"})
check(r["avisado"] is False and r["entrega"] == "bloqueado", "HALT → avisado False, «bloqueado»")
r = registra(RuntimeError("telegram caído"))
check(r["avisado"] is False and r["entrega"] == "error" and "telegram caído" in r["entrega_motivo"], "salida lanza → avisado False, «error»")
r = registra(None)
check(r["avisado"] is False and r["entrega"] == "desconocido", "salida no devuelve nada → «desconocido», no entregado")

print("3) anti-spam: el mismo aviso no se reenvía y lo dice")
resultado["report"] = {"delivered": True, "blocked": False, "reason": "entregado (x)"}
a = E.registrar("antispam", "mismo fallo", E.OPERATIVO, job="t", detalle="d")
llamadas = len(resultado["llamadas"])
b = E.registrar("antispam", "mismo fallo", E.OPERATIVO, job="t", detalle="d")
check(a["avisado"] is True and b["avisado"] is False and b["entrega"] == "antispam" and len(resultado["llamadas"]) == llamadas,
      "el segundo no sale, avisado False, entrega «antispam»")

print("4) la ruta crítica y la compatibilidad con el bool")
resultado["critica"] = {"delivered": False, "blocked": True, "reason": "sin chat_id de {{TITULAR}}"}
d = E._avisar_detalle("urgente", critico=True)
check(d["entregado"] is False and d["estado"] == "bloqueado", "alerta_critica frenada → no entregado, «bloqueado»")
resultado["critica"] = {"delivered": True, "blocked": False, "reason": "alerta crítica entregada"}
check(E._avisar_detalle("urgente", critico=True)["entregado"] is True, "alerta_critica entregada → entregado")
resultado["report"] = {"delivered": False, "blocked": False, "reason": "aplazado", "aplazado": True}
check(E._avisar("x") is False, "_avisar (bool) es False si quedó aplazado")
resultado["report"] = {"delivered": True, "blocked": False, "reason": "entregado"}
check(E._avisar("x") is True, "_avisar (bool) es True si se entregó")

if fallos:
    print("\n❌ %d fallo(s)" % len(fallos))
    sys.exit(1)
print("\nOK")
