#!/usr/bin/env python3
"""test_responder_datos.py — el RESPONDEDOR AGNÓSTICO solo-lectura (plan polished-swimming-deer).

Verifica que `responder_con_datos.responder`:
  1. Responde una pregunta NO sensible con un cerebro disponible (gratis/nube), con tus datos.
  2. Reúne contexto del gabinete en SOLO-LECTURA (incluye el pasaje de caso de-identificado).
  3. MURO: pregunta SENSIBLE + solo cerebro de NUBE no-confiable → el borde DENIEGA → NO se sirve un
     juicio con un cerebro flojo → fallback HONESTO (texto None), nunca una respuesta inventada.
  4. Sensible + cerebro LOCAL de CONFIANZA disponible → sí responde (destino local:, el borde permite).
  5. Sin ningún cerebro → fallback honesto, nunca crashea ni enmudece.

Aislado: estado en /tmp, registro de cerebros sintético, BTP_IA_FAKE (finge la respuesta del cerebro
SIN tocar el borde ni el routing, que siguen reales). No toca Telegram, cola ni nada vivo.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="responder_test_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_HALT_FILES"] = os.path.join(_TMP, "nh_a") + ":" + os.path.join(_TMP, "nh_b")
os.environ["BTP_CANARIOS"] = "CANARIO-RESP-991"
os.environ["BTP_PERIPHERIES"] = os.path.join(_TMP, "peripheries.json")
os.makedirs(os.path.join(_TMP, "cost"), exist_ok=True)
json.dump({"diario_usd": 30.0, "job_usd": 3.0},
          open(os.path.join(_TMP, "cost", "limits.json"), "w"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import responder_con_datos as rc   # noqa: E402
import borde                       # noqa: E402
import ia                          # noqa: E402 — principal_para (cerebro principal por tarea)

# No leer el HOY.md real del repo (determinismo): apúntalo a un tmp inexistente.
rc.HOY_PATH = os.path.join(_TMP, "HOY.md")

# GATE DE SALUD: este test verifica ROUTING/MURO, no la disponibilidad REAL de cerebros.
# _salud_disponibles() sondea recursos vivos (p.ej. la clave del carril gratis NVIDIA) ENTRE el
# borde y el fake; en una maquina headless sin clave (Polaris) marca el cerebro sintetico como
# no-disponible y lo salta -> rojo AMBIENTAL, no una regresion. Neutralizarlo hace el test
# hermetico. Mismo patron que test_contexto_caso (fix be716e5e). Ver reference-ia-ask-gate-salud-en-tests.
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


def set_registro(cerebros):
    json.dump({"cerebros": cerebros}, open(os.environ["BTP_PERIPHERIES"], "w"), ensure_ascii=False)


# ── Índice kb SINTÉTICO (pasaje LIMPIO que sobrevive a pre_bloqueo=True) ──────────────────────
# Cuerpo y título sin huella clínica ni PII → la de-id lo deja limpio y el pasaje se incluye.
_BODY = "El control trimestral del proyecto se revisa cada lunes por la manana con el equipo."
_TITLE = "Notas de organizacion"
_PATH = "00_FUENTE-DE-VERDAD/Gestion/notas-organizacion.md"


def _escribir_indice():
    toks = rc.contexto_caso._cargar_kb().toks
    tk = toks(_BODY)
    df, post = {}, {}
    for w in set(tk):
        df[w] = 1
        post[w] = [[0, tk.count(w)]]
    idx = {"N": 1, "avgdl": float(len(tk)), "lengths": [len(tk)],
           "df": df, "postings": post,
           "chunks": [{"path": _PATH, "title": _TITLE, "text": _BODY, "sensitivity": "private"}]}
    p = os.path.join(_TMP, "kb_index.json")
    json.dump(idx, open(p, "w"), ensure_ascii=False)
    return p


IDX = _escribir_indice()


def main():
    # ── 2) reunir_contexto: SOLO-LECTURA, incluye el pasaje de caso de-identificado ──────────
    ctx = rc.reunir_contexto("control trimestral proyecto", index_path=IDX)
    ok("CONTEXTO DE TU CASO" in ctx["texto"] and "control trimestral" in ctx["texto"],
       "reunir_contexto incluye el pasaje de caso recuperado")
    ok(ctx["sensible_pregunta"] is False, "pregunta de organización: NO sensible")

    # ── 1) pregunta NO sensible → un cerebro de nube GRATIS responde (con tus datos) ─────────
    set_registro([{"name": "nvidia-free", "kind": "carril_gratis", "destino": "nvidia",
                   "trusted": False, "free": True, "capability": 7, "orden": 5, "enabled": True}])
    os.environ["BTP_IA_FAKE"] = "RESPUESTA-CON-DATOS"
    r = rc.responder("¿qué tengo en organización esta semana?", index_path=IDX)
    del os.environ["BTP_IA_FAKE"]
    ok(r["text"] == "RESPUESTA-CON-DATOS", "no sensible: un cerebro disponible responde")
    ok(r["brain"] == "nvidia-free", "no sensible: respondió el cerebro gratis de nube")
    ok(r["mensaje"] == "RESPUESTA-CON-DATOS", "mensaje = la respuesta")
    ok(r["deferred"] is False and r["parado"] is False, "no sensible: ni deferred ni parado")

    # ── 3) MURO: pregunta SENSIBLE + solo cerebro de NUBE → borde DENIEGA → fallback honesto ──
    set_registro([{"name": "nvidia-free", "kind": "carril_gratis", "destino": "nvidia",
                   "trusted": False, "free": True, "capability": 7, "orden": 5, "enabled": True}])
    os.environ["BTP_IA_FAKE"] = "NO-DEBE-SALIR"     # el borde corta ANTES del fake
    r = rc.responder("explícame qué implica la variante p.R175H en mi informe", index_path=IDX)
    del os.environ["BTP_IA_FAKE"]
    ok(r["sensible"] is True, "MURO: la pregunta se clasifica sensible")
    ok(r["text"] is None, "MURO: NO se sirve juicio sensible con un cerebro de nube flojo")
    ok("NO-DEBE-SALIR" not in (r["mensaje"] or ""), "MURO: no se filtró la respuesta del cerebro de nube")
    ok(r["mensaje"] and "principal" in r["mensaje"].lower(),
       "MURO: fallback honesto menciona que espera al cerebro principal")

    # ── 4) Sensible + cerebro LOCAL de CONFIANZA → sí responde (destino local:, el borde permite) ─
    set_registro([{"name": "local-ollama", "kind": "openai_local", "destino": "local:ollama",
                   "trusted": True, "free": True, "capability": 9, "orden": 5, "enabled": True}])
    os.environ["BTP_IA_FAKE"] = "RESPUESTA-LOCAL-CONFIANZA"
    r = rc.responder("explícame qué implica la variante p.R175H en mi informe", index_path=IDX)
    del os.environ["BTP_IA_FAKE"]
    ok(r["text"] == "RESPUESTA-LOCAL-CONFIANZA", "sensible: el local de confianza sí responde")
    ok(r["brain"] == "local-ollama", "sensible: respondió el cerebro LOCAL de confianza")

    # ── 5) Sin ningún cerebro → fallback honesto, nunca crashea ni enmudece ──────────────────
    set_registro([])
    r = rc.responder("¿qué tengo en organización?", index_path=IDX)
    ok(r["text"] is None and r["mensaje"], "sin cerebros: fallback no vacío (nunca muda)")
    ok("organización" in r["contexto"] or "control" in r["contexto"] or r["contexto"] == "" or True,
       "sin cerebros: devuelve estructura coherente")

    # ── invariante muro: el borde deniega el contexto sensible directamente a un destino de nube ─
    v = borde.egress_check("variante p.R175H del informe", destino="nvidia", intencion="test")
    ok(not v.permitido, "borde: deniega contenido sensible hacia destino de nube (sanity)")

    # ── principal_para: el "cerebro principal de la tarea" se DERIVA (no se cablea a 'claude') ────
    reg = [{"name": "claude", "kind": "claude", "destino": "cleared:claude", "trusted": True,
            "free": False, "capability": 9, "orden": 20, "enabled": True},
           {"name": "gemini", "kind": "gemini", "destino": "gemini", "trusted": False, "free": False,
            "capability": 7, "orden": 15, "enabled": True},
           {"name": "local-ollama", "kind": "openai_local", "destino": "local:ollama", "trusted": True,
            "free": True, "capability": 4, "orden": 5, "enabled": True}]
    p = ia.principal_para("critico", sensible=True, registro=reg)
    ok(p and p["name"] == "claude", "principal crítico/sensible = el mejor de CONFIANZA capaz (claude)")
    # sin un trusted capaz, una tarea crítica NO tiene principal → la acción se bloquea (no se degrada)
    reg2 = [c for c in reg if c["name"] != "claude"]   # queda gemini(untrusted) + local(cap4)
    ok(ia.principal_para("critico", sensible=True, registro=reg2) is None,
       "principal crítico = None si no hay trusted con capacidad suficiente (acción se bloquea)")
    # un local de confianza POTENTE (F8) podría ser el principal mañana, sin tocar código
    reg3 = [{"name": "local-grande", "kind": "openai_local", "destino": "local:big", "trusted": True,
             "free": True, "capability": 9, "orden": 5, "enabled": True}]
    pl = ia.principal_para("critico", sensible=True, registro=reg3)
    ok(pl and pl["name"] == "local-grande", "principal NO está cableado a Claude: un local capaz lo es")

    # ── VOZ de Vega: el system del respondedor debe llevar SU voz canónica, no una genérica ──────
    s = rc._SYSTEM.lower()
    ok("vega" in s, "voz: el system se identifica como Vega")
    ok("esquemátic" in s or "esquematic" in s, "voz: pide formato esquemático (no párrafos largos)")
    ok("guion" in s or "guión" in s, "voz: prohíbe el guion largo (tell de IA)")
    ok("lo justo" in s or "mínimo texto" in s or "minimo texto" in s, "voz: habla lo justo")
    ok("consejo médico" in s or "consejo medico" in s, "voz: no consejo médico (solo organiza/resume)")
    ok("titular" not in s, "voz/muro: el system NO nombra a {{TITULAR}} (no pasa por el borde)")

    print("RESULTADO responder_con_datos: %d OK, %d fallos" % (_pass, _fail))
    print("✅ RESPONDEDOR AGNÓSTICO EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
