#!/usr/bin/env python3
"""test_evals.py — evals del ARNÉS de evals (tools/evals.py, F2b).

Verifica el arnés EN SECO (sin LLM real, $0):
  · Los casos deterministas (clasificar/egress/ingeniero/router) corren y puntúan; el borde es real.
  · `calidad` SIN juez → aplazado (deferred), nunca puntúa a ciegas.
  · `calidad` CON juez (ia.ask stubbeado): puntúa, y ANTI-GOODHART — si el juez cae en el mismo
    cerebro que el sujeto, o solo hay 1 cerebro, o el score no parsea → aplaza (no inventa nota).
  · Scorecard escrito; deriva alerta cuando un tipo cae > umbral; add-regression valida y deduplica.
Estilo test_ia/test_borde: cada caso suma OK/fallo; exit = nº de fallos.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("nombres", "identidad")
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="evals_test_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_HALT_FILES"] = os.path.join(_TMP, "nh_a") + ":" + os.path.join(_TMP, "nh_b")
os.environ["BTP_EVALS_DIR"] = os.path.join(_TMP, "evals")
os.environ["BTP_PERIPHERIES"] = os.path.join(_TMP, "peripheries.json")
os.makedirs(os.environ["BTP_EVALS_DIR"], exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "tools"))

# Golden set de prueba (uno por tipo determinista + uno de calidad)
GOLDEN = [
    {"id": "t-clasif-limpio", "tipo": "clasificar", "prompt": "redacta un saludo genérico",
     "espera": {"sensible": False}},
    {"id": "t-clasif-pii", "tipo": "clasificar", "prompt": "el caso de {{TITULAR}} {{APELLIDO}}",
     "espera": {"sensible": True, "motivo_contiene": "titular"}},
    {"id": "t-egress-block", "tipo": "egress", "prompt": "HLA-A*02:01 del paciente",
     "destino": "nvidia", "espera": {"permitido": False}},
    {"id": "t-cient-ok", "tipo": "ingeniero", "prompt": "vacunas de neoantígenos en {{DIAGNOSTICO}}",
     "espera": {"ok": True}},
    {"id": "t-router-free", "tipo": "router", "prompt": "resume esto", "clinico": False,
     "registro": [{"name": "free", "kind": "carril_gratis", "destino": "nvidia", "trusted": False,
                   "free": True, "orden": 10, "enabled": True},
                  {"name": "claude", "kind": "claude", "destino": "cleared:claude", "trusted": True,
                   "orden": 20, "enabled": True}],
     "stub": {"free": ["ok", "X"], "claude": ["ok", "Y"]},
     "espera": {"brain": "free", "degradado": False}},
    {"id": "t-calidad", "tipo": "calidad", "prompt": "Saluda en una frase.",
     "rubrica": "Debe ser un saludo breve.", "umbral": 0.7},
]
json.dump(GOLDEN, open(os.path.join(os.environ["BTP_EVALS_DIR"], "golden.json"), "w"))
# registro real (2 cerebros) para que _otro_cerebro encuentre un juez distinto del sujeto
json.dump({"cerebros": [
    {"name": "brainA", "kind": "carril_gratis", "destino": "nvidia", "free": True, "enabled": True},
    {"name": "brainB", "kind": "carril_gratis", "destino": "nvidia", "free": True, "enabled": True}]},
    open(os.environ["BTP_PERIPHERIES"], "w"))

import evals  # noqa: E402
import ia      # noqa: E402
# GATE DE SALUD: el arnes evalua ROUTING, no disponibilidad real. Sin clave NVIDIA en headless,
# _salud_disponibles filtra los carril_gratis sinteticos y el router gratis-primero falla (rojo
# AMBIENTAL). Neutralizarlo lo hermetiza. Ver reference-ia-ask-gate-salud-en-tests.
ia._salud_disponibles = lambda: {}

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _res_por_id(resultados):
    return {r["id"]: r for r in resultados}


def main():
    # ── corrida determinista SIN juez ────────────────────────────────────────────────────
    resumen, resultados = evals.correr(con_juez=False)
    R = _res_por_id(resultados)
    ok(R["t-clasif-limpio"]["paso"] is True, "clasificar limpio pasa")
    ok(R["t-clasif-pii"]["paso"] is True, "clasificar PII pasa")
    ok(R["t-egress-block"]["paso"] is True, "egress sensible→nube bloquea")
    ok(R["t-cient-ok"]["paso"] is True, "ingeniero genérico permite")
    ok(R["t-router-free"]["paso"] is True, "router gratis-primero")
    ok(R["t-calidad"]["deferred"] is True and R["t-calidad"]["paso"] is None,
       "calidad sin juez → aplazado (no puntúa)")
    ok(resumen["por_tipo"]["clasificar"]["pass_rate"] == 1.0, "pass_rate clasificar = 1.0")
    ok(os.path.exists(os.path.join(_TMP, "evals", "scorecard.json")), "scorecard escrito")

    # ── ruta del juez (ia.ask stubbeado; $0) ─────────────────────────────────────────────
    orig_ask = ia.ask

    def fake_ask(prompt, **kw):
        if "Eres un JUEZ" in prompt:                      # llamada del JUEZ
            return {"text": '{"score": 0.9, "motivo": "bien"}', "brain": "brainB",
                    "deferred": False, "motivo": "ok"}
        return {"text": "Hola.", "brain": "brainA", "deferred": False, "motivo": "ok"}
    ia.ask = fake_ask
    _, res = evals.correr(tipos=("calidad",), con_juez=True)
    c = _res_por_id(res)["t-calidad"]
    ok(c["paso"] is True and abs(c.get("score", 0) - 0.9) < 1e-6, "calidad con juez → puntúa 0.9")
    ok(c.get("subject_brain") == "brainA" and c.get("judge_brain") == "brainB",
       "juez ≠ sujeto (anti-Goodhart, cerebros distintos)")

    # ANTI-GOODHART: el juez cae en el MISMO cerebro que el sujeto → aplaza
    def fake_ask_mismo(prompt, **kw):
        return {"text": '{"score": 1.0, "motivo": "x"}', "brain": "brainA",
                "deferred": False, "motivo": "ok"}
    ia.ask = fake_ask_mismo
    _, res = evals.correr(tipos=("calidad",), con_juez=True)
    c = _res_por_id(res)["t-calidad"]
    ok(c["deferred"] is True and c["paso"] is None, "juez=sujeto → aplaza (anti-Goodhart)")

    # juez devuelve basura → no parsea → aplaza (no inventa nota)
    def fake_ask_basura(prompt, **kw):
        if "Eres un JUEZ" in prompt:
            return {"text": "me ha gustado mucho, un 10", "brain": "brainB", "deferred": False}
        return {"text": "Hola.", "brain": "brainA", "deferred": False}
    ia.ask = fake_ask_basura
    _, res = evals.correr(tipos=("calidad",), con_juez=True)
    ok(_res_por_id(res)["t-calidad"]["deferred"] is True, "juez sin score parseable → aplaza")
    ia.ask = orig_ask

    # solo 1 cerebro en el registro → el juez sería el sujeto → aplaza
    json.dump({"cerebros": [{"name": "brainA", "kind": "carril_gratis", "destino": "nvidia",
                             "free": True, "enabled": True}]},
              open(os.environ["BTP_PERIPHERIES"], "w"))

    def fake_ask_solo(prompt, **kw):
        return {"text": "Hola.", "brain": "brainA", "deferred": False}
    ia.ask = fake_ask_solo
    _, res = evals.correr(tipos=("calidad",), con_juez=True)
    ok(_res_por_id(res)["t-calidad"]["deferred"] is True, "1 solo cerebro → aplaza (no auto-juzga)")
    ia.ask = orig_ask

    # ── deriva ───────────────────────────────────────────────────────────────────────────
    out = os.path.join(_TMP, "evals")
    json.dump({"ts": "base", "resumen": {"por_tipo": {"router": {"pass_rate": 1.0}}}},
              open(os.path.join(out, "baseline.json"), "w"))
    json.dump({"ts": "now", "resumen": {"por_tipo": {"router": {"pass_rate": 0.5}}}},
              open(os.path.join(out, "scorecard.json"), "w"))
    d = evals.calcular_deriva()
    ok(d["ok"] and len(d["alertas"]) == 1 and d["alertas"][0]["tipo"] == "router",
       "deriva: caída 1.0→0.5 dispara alerta")
    json.dump({"ts": "now", "resumen": {"por_tipo": {"router": {"pass_rate": 0.95}}}},
              open(os.path.join(out, "scorecard.json"), "w"))
    ok(not evals.calcular_deriva()["alertas"], "deriva: caída pequeña no alerta")

    # ── add-regression ───────────────────────────────────────────────────────────────────
    okk, _ = evals.add_regresion({"id": "reg-1", "tipo": "clasificar", "prompt": "x",
                                  "espera": {"sensible": False}})
    ok(okk, "add-regression caso válido")
    okk2, _ = evals.add_regresion({"id": "reg-1", "tipo": "clasificar", "prompt": "x",
                                   "espera": {"sensible": False}})
    ok(not okk2, "add-regression dedup (mismo id → rechaza)")
    okk3, _ = evals.add_regresion({"id": "malo", "tipo": "inexistente"})
    ok(not okk3, "add-regression rechaza tipo inválido")

    print("RESULTADO evals (arnés): %d OK, %d fallos" % (_pass, _fail))
    print("✅ ARNÉS DE EVALS EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
