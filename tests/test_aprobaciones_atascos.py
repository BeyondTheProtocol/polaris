#!/usr/bin/env python3
"""test_aprobaciones_atascos.py — Vega aprueba con registro (F4) y resumen diario de atascos (F5).
1-oct-2026, plan «Vega al mando». Estado temporal, datos falsos.

Aprobaciones:
  · la política real clasifica: bug → A, clínico → B, enviar/pagar/levantar código rojo → C
  · un tipo desconocido es B (ante la duda, se pregunta)
  · A sin prueba o sin cómo deshacer → rechazo; «hecho» no es prueba
  · B sin OK de {{TITULAR}} → rechazo; con OK, se registra
  · C → rechazo siempre
  · el buzón de propuestas se lee y se resuelve, y queda en el registro
  · la muestra semanal lleva siempre las B
Atascos:
  · lista lo que espera por {{TITULAR}} con sus días, y no lo que espera por un tercero
  · propone cambio de proceso con 3+ esperas del mismo tipo, nunca para lo clínico
  · sin atascos, el bloque está vacío (el parte no crece)
  · una fuente rota no tumba el resumen
"""
import json
import os
import sys
import tempfile
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="aprob_")
os.environ["BTP_STATE_DIR"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))
import aprobaciones as ap  # noqa: E402
import atascos  # noqa: E402

_fail = 0


def ok(cond, name):
    global _fail
    if not cond:
        _fail += 1
        print("  ✗ %s" % name)


def rechaza(fn):
    try:
        fn()
    except ap.Rechazo:
        return True
    return False


# ── política ──
ok(ap.nivel("bug") == "A" and ap.nivel("rutina") == "A", "fontanería = A")
ok(ap.nivel("clinico") == "B" and ap.nivel("informe_que_falta") == "B", "clínico = B")
ok(all(ap.nivel(t) == "C" for t in ("enviar", "pagar", "levantar_codigo_rojo", "publicar")), "salidas = C")
ok(ap.nivel("algo_nuevo") == "B", "desconocido = B")

# ── registrar ──
ok(rechaza(lambda: ap.registrar("bug", "x", "y", "", "git revert a")), "A sin prueba → rechazo")
ok(rechaza(lambda: ap.registrar("bug", "x", "y", "commit a", "")), "A sin deshacer → rechazo")
ok(rechaza(lambda: ap.registrar("clinico", "x", "y", "p", "d")), "B sin OK de {{TITULAR}} → rechazo")
ok(rechaza(lambda: ap.registrar("enviar", "x", "y", "p", "d", ok_titular="sí")), "C → rechazo siempre")
f = ap.registrar("bug", "arreglo del parte", "fallaba", "commit abc + test verde", "git revert abc")
ok(f["nivel"] == "A" and f["quien"] == "vega", "A con prueba → registrada")
ap.registrar("clinico", "cerrar espera del informe", "llegó", "informe en historial", "reabrir hilo",
             ok_titular="«sí, ciérralo» 1-oct")
ok(len(ap.listar()) == 2, "dos en el registro")
ok(ap.main(["registrar", "--tipo", "pagar", "--que", "x", "--prueba", "p", "--deshacer", "d"]) == 2,
   "CLI: rc 2 en rechazo")
for i in range(6):
    ap.registrar("tarea", "t%d" % i, "-", "hilo cerrado en seguimiento.json", "reabrir")
m = ap.muestra(n=3, semilla=1)
ok(len(m) == 3 and any(x["nivel"] == "B" for x in m), "muestra semanal con las B dentro")

# ── buzón de propuestas ──
os.makedirs(os.path.join(_TMP, "vega"), exist_ok=True)
with open(os.path.join(_TMP, "vega", "propuestas_hilos.jsonl"), "w", encoding="utf-8") as fh:
    fh.write(json.dumps({"titulo": "registrar hilo X", "origen": "archivar_nota"}) + "\n")
    fh.write(json.dumps({"titulo": "Esperando: informe", "origen": "promesas_caso", "clinico": True}) + "\n")
ok(len(ap.propuestas_abiertas()) == 2, "buzón: dos abiertas")
ap.resolver(0, "aprobada", "es un entregable real")
ok([i for i, _ in ap.propuestas_abiertas()] == [1], "resuelta la 0, queda la 1")
ok(rechaza(lambda: ap.resolver(1, "aprobada", "x")), "la clínica no la resuelve Vega sin OK")
ok(ap.listar()[-1]["que"].startswith("aprobada propuesta"), "la resolución queda en el registro")

# ── atascos ──
hoy = date(2026, 10, 1)
hilos = [
    {"titulo": "Firmar consentimiento", "estado": "por_confirmar", "gate": True,
     "esperando_desde": (hoy - timedelta(days=9)).isoformat(), "etiqueta": "Admin"},
    {"titulo": "Respuesta del laboratorio", "estado": "esperando", "quien_espera": "laboratorio",
     "esperando_desde": (hoy - timedelta(days=20)).isoformat(), "etiqueta": "Admin"},
    {"titulo": "Decidir cosa reciente", "estado": "por_confirmar", "gate": True,
     "esperando_desde": (hoy - timedelta(days=1)).isoformat()},
] + [{"titulo": "Revisar web %d" % i, "estado": "por_confirmar", "gate": True, "etiqueta": "Web",
      "esperando_desde": (hoy - timedelta(days=5)).isoformat()} for i in range(3)] + \
    [{"titulo": "Pregunta clínica %d" % i, "estado": "por_confirmar", "gate": True, "etiqueta": "Clinico",
      "esperando_desde": (hoy - timedelta(days=5)).isoformat()} for i in range(3)]
esp = atascos.esperan_por_titular(hilos, hoy)
tit = [h["titulo"] for h, _ in esp]
ok(tit[0] == "Firmar consentimiento" and dict((h["titulo"], d) for h, d in esp)["Firmar consentimiento"] == 9,
   "espera por ella, con sus días, la más vieja primero")
ok("Respuesta del laboratorio" not in tit, "lo que espera por un tercero no")
ok("Decidir cosa reciente" not in tit, "lo de ayer todavía no")
prop = atascos.propuestas_de_proceso(esp)
ok(len(prop) == 1 and "«Web»" in prop[0], "propone delegar lo repetido no clínico: %r" % prop)
vacio = {"incongruencias": [], "esperas": [], "proceso": [], "propuestas": 0, "promesas_vencidas": [], "sistema": [], "fallos": []}
ok(atascos.bloque(vacio) == "", "sin atascos, bloque vacío")
d = atascos.recopilar()      # estado temporal sin healthcheck: una fuente rota no tumba nada
ok(isinstance(atascos.bloque(d), str) and any("healthcheck" in x for x in d["fallos"]),
   "fuente rota → se dice y sigue")

print("test_aprobaciones_atascos: %s" % ("OK" if not _fail else "%d FALLOS" % _fail))
sys.exit(1 if _fail else 0)
