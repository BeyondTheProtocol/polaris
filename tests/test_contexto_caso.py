#!/usr/bin/env python3
"""test_contexto_caso.py — evals de la OPCIÓN A de F3b (tools/contexto_caso.py).

Insight de A: el cerebro local (Ollama) es egress-cero y de CONFIANZA (destino `local:`); el caso
nunca sale de la máquina para él. `ia.ask` ya pasa por el BORDE, que PERMITE sensible→destino de
confianza y DENIEGA sensible→no-confiable. Por eso el pre-bloqueo estricto de `contexto_caso` (vaciar
la respuesta si quedan identificadores residuales en TÍTULOS/rutas/fechas) era REDUNDANTE para el
local y era lo que lo rompía sobre el caso real.

INVARIANTE DURA (sintética — no se procesa clínico real):
  1. Con contexto de caso sintético que tiene identificadores RESIDUALES (título con nombre+fecha),
     `preguntar_local` (→ local de CONFIANZA) AHORA devuelve respuesta (no vacío): el carril local ya
     no se pre-bloquea a sí mismo. Y SIGUE redactando (de-id) los pasajes.
  2. El MISMO contenido, enrutado a un destino NO de confianza (cerebro de nube), sigue BLOQUEADO
     (el BORDE deniega). Demuestra que el guard de egress aguanta — A no abre ninguna vía de fuga.

Estilo test_ia/test_borde: cada caso suma OK/fallo; exit = nº de fallos. Estado AISLADO (tmp).
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
_TMP = tempfile.mkdtemp(prefix="contexto_test_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_HALT_FILES"] = os.path.join(_TMP, "nh_a") + ":" + os.path.join(_TMP, "nh_b")
os.environ["BTP_CANARIOS"] = "CANARIO-CTX-7733"
os.environ["BTP_PERIPHERIES"] = os.path.join(_TMP, "peripheries.json")
os.makedirs(os.path.join(_TMP, "cost"), exist_ok=True)
json.dump({"diario_usd": 30.0, "job_usd": 3.0},
          open(os.path.join(_TMP, "cost", "limits.json"), "w"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import contexto_caso as cc   # noqa: E402
import borde                 # noqa: E402
import deid                  # noqa: E402
import ia                    # noqa: E402

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


# ── Índice kb SINTÉTICO ────────────────────────────────────────────────────────────────────
# Un pasaje cuyo CUERPO tiene huella clínica (que la de-id redacta) y cuyo TÍTULO/ruta llevan
# nombre+fecha (identificadores RESIDUALES que la de-id del cuerpo NO toca → el viejo pre-bloqueo
# descartaba TODO el contexto por ellos). Texto 100% sintético, no es el caso real.
_BODY = ("La paciente presenta el marcador TP53 elevado y un valor de ER 80%. "
         "La variante p.R175H aparece en el informe. Cita prevista el 8 de julio de 2026.")
_TITLE_RESIDUAL = "Informe de {{TITULAR}} {{APELLIDO}} 2026-07-08"   # nombre + fecha en el TÍTULO
_PATH_RESIDUAL = "00_FUENTE-DE-VERDAD/{{TITULAR}}-{{APELLIDO}}/informe-2026-07-08.md"


def _escribir_indice():
    toks = cc._cargar_kb().toks
    text = _BODY
    chunk = {"path": _PATH_RESIDUAL, "title": _TITLE_RESIDUAL, "text": text,
             "sensitivity": "private"}
    tk = toks(text)
    df = {}
    post = {}
    for i, w in enumerate(set(tk)):
        df[w] = 1
        post[w] = [[0, tk.count(w)]]
    idx = {"N": 1, "avgdl": float(len(tk)), "lengths": [len(tk)],
           "df": df, "postings": post, "chunks": [chunk]}
    p = os.path.join(_TMP, "kb_index.json")
    json.dump(idx, open(p, "w"), ensure_ascii=False)
    return p


IDX = _escribir_indice()
PREGUNTA = "marcador TP53 ER variante"   # tokens que casan con el cuerpo


def main():
    # El filtro de SALUD (caché TTL dentro de ia.ask) se añadió DESPUÉS de escribir estos evals
    # (commit 59891338, "freno de criticidad") y sondea el Ollama REAL por socket (127.0.0.1:11434):
    # sin el servidor local levantado marca el cerebro local "caído" y lo SALTA entre el borde y el
    # fake, dejando INV1 sin respuesta. Estos evals prueban el MURO (pre-bloqueo/egress), NO si Ollama
    # corre → neutralizamos el filtro (mismo idiom que test_ia.py: `= lambda: {}` = "no filtramos por
    # salud; deja actuar al borde"). El BORDE sigue REAL: INV2 demuestra que el muro bloquea lo no-fiable.
    ia._salud_disponibles = lambda: {}
    # ── Sanidad: el contexto se RECUPERA y se REDACTA (de-id activa) ─────────────────────────
    ctx = cc.construir_contexto(PREGUNTA, index_path=IDX, pre_bloqueo=False)
    ok(ctx["contexto"] != "", "carril local: recupera contexto (no vacío)")
    ok("[REDACTADO]" in ctx["contexto"], "sigue redactando: hay marcadores [REDACTADO] en el cuerpo")
    ok("TP53" not in ctx["contexto"] and "R175H" not in ctx["contexto"].upper(),
       "de-id enmascara la huella clínica del cuerpo")
    # El residuo vive en el TÍTULO/ruta de la fuente (nombre+fecha), que se inserta verbatim en el
    # ensamblado: por eso el contexto ENSAMBLADO no queda 'limpio' aunque el CUERPO sí. Eso es lo que
    # el viejo `preguntar_local` usaba para VACIAR la respuesta sobre el caso real.
    ok(ctx["limpio"] is False, "el ensamblado NO queda 'limpio' por el residuo de título/fecha")
    ok(ctx["descartados"] == [], "carril local: NO descarta pasajes (cuerpo limpio; residuo en título)")

    # ── INVARIANTE 1: preguntar_local → local de CONFIANZA → AHORA responde (no vacío) ───────
    # Registro: el único gratis es el LOCAL de confianza (destino local:). BTP_IA_FAKE finge la
    # respuesta del cerebro SIN tocar el borde ni el routing (que siguen reales) → el borde corre
    # ANTES del fake: como el destino es de confianza, PERMITE el contenido sensible y el fake responde.
    # (El filtro de salud, que iría ENTRE el borde y el fake, se neutraliza en main() — ver nota allí:
    # estos evals prueban el MURO, no la disponibilidad de Ollama.)
    set_registro([{"name": "local-ollama", "kind": "openai_local", "destino": "local:ollama",
                   "trusted": True, "free": True, "orden": 5, "enabled": True}])
    os.environ["BTP_IA_FAKE"] = "RESPUESTA-LOCAL"
    r = cc.preguntar_local(PREGUNTA, index_path=IDX)
    del os.environ["BTP_IA_FAKE"]
    ok(r["respuesta"] == "RESPUESTA-LOCAL", "INV1: local de confianza AHORA responde (no se pre-bloquea)")
    ok(r["brain"] == "local-ollama", "INV1: respondió el cerebro LOCAL")
    ok(r["enviado_limpio"] is True, "INV1: se envió (hay respuesta)")
    ok("[REDACTADO]" in r["contexto"], "INV1: el contexto enviado iba REDACTADO (defensa en profundidad)")

    # ── INVARIANTE 2: el MISMO contenido → destino NO de confianza → BLOQUEADO ───────────────
    # 2a) directo: el borde sobre el contexto redactado hacia un destino de NUBE → DENY.
    v = borde.egress_check(ctx["contexto"], destino="nvidia", intencion="test")
    ok(not v.permitido, "INV2a: borde DENIEGA el contexto sensible hacia destino de nube (nvidia)")
    ok(borde.es_trusted("local:ollama") and not borde.es_trusted("nvidia"),
       "INV2a: local:ollama es trusted; nvidia NO")

    # 2b) end-to-end: si el único gratis es un cerebro de NUBE no-confiable, ia.ask (dentro de
    #     preguntar_local) NO obtiene texto: el borde bloquea sensible→no-confiable. No abre fuga.
    set_registro([{"name": "nvidia-free", "kind": "carril_gratis", "destino": "nvidia",
                   "trusted": False, "free": True, "orden": 5, "enabled": True}])
    os.environ["BTP_IA_FAKE"] = "NO-DEBE-SALIR"   # aunque el cerebro 'respondería', el borde corta antes
    r2 = cc.preguntar_local(PREGUNTA, index_path=IDX)
    del os.environ["BTP_IA_FAKE"]
    ok(r2["respuesta"] is None, "INV2b: destino no-confiable (nube) → preguntar_local NO obtiene texto")
    ok(r2["enviado_limpio"] is False, "INV2b: enviado_limpio=False (el borde bloqueó la salida)")

    # 2c) el muro manda aunque el registro marque trusted por error y el destino sea de nube
    set_registro([{"name": "nube-falsa", "kind": "carril_gratis", "destino": "nvidia",
                   "trusted": True, "free": True, "orden": 5, "enabled": True}])
    os.environ["BTP_IA_FAKE"] = "NO-DEBE-SALIR"
    r3 = cc.preguntar_local(PREGUNTA, index_path=IDX)
    del os.environ["BTP_IA_FAKE"]
    ok(r3["respuesta"] is None,
       "INV2c: borde bloquea sensible hacia 'trusted' mal configurado (nube) — defensa en profundidad")

    print("RESULTADO contexto_caso (F3b opción A): %d OK, %d fallos" % (_pass, _fail))
    print("✅ OPCIÓN A EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
