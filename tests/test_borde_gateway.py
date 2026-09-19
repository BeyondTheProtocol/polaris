#!/usr/bin/env python3
"""test_borde_gateway.py — evals del GATEWAY del borde (tools/borde_gateway.py, F3 Fase A).

Verifica EN SECO (sin LLM real, $0; stub de ia._invocar como test_ia) que el gateway:
  · Enruta una petición OpenAI no-sensible por ia.ask (→ borde) y devuelve formato OpenAI (200).
  · NIEGA lo sensible: PII sin cerebro de confianza → 403 y el DATO NO sale en la respuesta.
  · Lo sensible hacia un destino de nube mal marcado 'trusted' → el borde lo bloquea (deferred→503)
    y queda una línea 'deny' en el ledger hash-chained del borde.
  · Cadena agotada (no-sensible, todos fallan) → 503 (aplazo honesto, sin inventar respuesta).
  · messages inválidos / prompt vacío → 400.
  · HTTP real (puerto efímero, loopback): sin token / token malo → 401; ruta desconocida → 404;
    GET /v1/models con token → 200; POST válido con token → 200.
Estilo test_ia/test_evals: cada caso suma OK/fallo; exit = nº de fallos.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("nombres", "identidad")
import glob
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="gw_test_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_HALT_FILES"] = os.path.join(_TMP, "nh_a") + ":" + os.path.join(_TMP, "nh_b")
os.environ["BTP_CANARIOS"] = "CANARIO-GW-4242"
os.environ["BTP_PERIPHERIES"] = os.path.join(_TMP, "peripheries.json")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import borde_gateway as gw   # noqa: E402
import ia                    # noqa: E402
# GATE DE SALUD: este test verifica el GATEWAY/MURO, no la disponibilidad REAL de cerebros.
# En headless sin clave NVIDIA, _salud_disponibles filtra el `nvidia-free` sintetico ANTES del
# stub de ia._invocar -> los caminos no-sensible/exito caerian por rojo AMBIENTAL. Neutralizarlo
# lo hermetiza (los caminos de muro 403/503 no dependen de esto). Ver reference-ia-ask-gate-salud-en-tests.
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


FREE = {"name": "nvidia-free", "kind": "carril_gratis", "destino": "nvidia",
        "trusted": False, "free": True, "orden": 10, "capability": 3, "enabled": True}
CLAUDE = {"name": "claude", "kind": "claude", "destino": "cleared:claude",
          "trusted": True, "orden": 20, "capability": 9, "enabled": True, "models": ["sonnet"]}
# 'trusted' mal configurado: dice trusted pero su destino es de NUBE. capability alta (9) para que
# el freno NO lo pre-salte: así el BORDE corre y SELLA su deny (defensa en profundidad, lo que prueba).
FALSO_TRUSTED = {"name": "nube-falsa", "kind": "carril_gratis", "destino": "nvidia",
                 "trusted": True, "orden": 5, "capability": 9, "enabled": True}


def set_registro(cerebros):
    json.dump({"cerebros": cerebros}, open(os.environ["BTP_PERIPHERIES"], "w"), ensure_ascii=False)


def stub(behavior):
    def fake(cerebro, prompt, system, clinico, interactivo=False, critico=False):
        b = behavior.get(cerebro.get("name"), ("fail", "no-config"))
        return (b[1], 0.0, None) if b[0] == "ok" else (None, 0.0, b[1])
    ia._invocar = fake


def msg(texto, role="user"):
    return {"messages": [{"role": role, "content": texto}]}


_avisos = []   # captura de salida.report_to_titular (envío real opt-in: no spamear en tests)


def main():
    # La salida REAL no debe enviar Telegram en tests: capturamos el auto-reporte de la puerta caída.
    import salida
    salida.report_to_titular = lambda text, **k: _avisos.append(text) or {"delivered": True}

    # ── núcleo responder_chat (sin socket) ───────────────────────────────────────────────
    set_registro([FREE, CLAUDE])
    stub({"nvidia-free": ("ok", "RESPUESTA-LIBRE"), "claude": ("ok", "X")})
    st, body = gw.responder_chat(msg("resume estos tres párrafos públicos"))
    ok(st == 200 and body["choices"][0]["message"]["content"] == "RESPUESTA-LIBRE",
       "no-sensible → 200 formato OpenAI")
    ok(body.get("model") == "nvidia-free", "200 reporta el cerebro que respondió")

    # sensible (PII) y ningún cerebro de confianza → 403, y el DATO no sale
    set_registro([FREE])
    stub({"nvidia-free": ("ok", "NO-DEBE-SALIR")})
    st, body = gw.responder_chat(msg("el caso clínico de {{TITULAR}} {{APELLIDO}}"))
    leak = json.dumps(body, ensure_ascii=False)
    ok(st == 403 and "muro" in body["error"]["type"], "PII sin confianza → 403 (muro)")
    ok("{{APELLIDO}}" not in leak and "NO-DEBE-SALIR" not in leak, "403 no filtra el dato ni respuesta")

    # sensible hacia 'trusted' mal configurado (destino nube) → borde bloquea → 503 + deny en ledger
    set_registro([FALSO_TRUSTED])
    stub({"nube-falsa": ("ok", "NO-DEBE-SALIR")})
    st, body = gw.responder_chat(msg("la variante R175H del tumor"))
    ok(st == 503, "sensible→trusted-de-nube → 503 (borde bloquea, defensa en profundidad)")
    deny = False
    for f in glob.glob(os.path.join(_TMP, "borde", "ledger-*.jsonl")):
        for ln in open(f, encoding="utf-8"):
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            if rec.get("evento") == "egress" and rec.get("permitido") is False:
                deny = True
    ok(deny, "el borde SELLÓ un deny en el ledger hash-chained")

    # cadena agotada (no-sensible) → 503 honesto
    set_registro([FREE, CLAUDE])
    stub({"nvidia-free": ("fail", "limite"), "claude": ("fail", "credito")})
    st, body = gw.responder_chat(msg("trabajo no sensible"))
    ok(st == 503 and "error" in body, "cadena agotada → 503 (aplazo honesto)")

    # ── AUTO-DETECCIÓN de modo (determinista, sin LLM) ───────────────────────────────────────
    # Solo corre si hay modos.json con los 4 modos (Vivir/Misión/Cuidar/Construir).
    modos = gw.cargar_modos()
    nombres = {m.get("name") for m in modos}
    if {"polaris-vivir", "polaris-mision", "polaris-cuidar", "polaris-construir"} <= nombres:
        def det(txt):
            m, auto = gw._detectar_modo(txt)
            return (m or {}).get("name"), auto
        ok(det("estoy agotada, no puedo más con todo") == ("polaris-cuidar", True),
           "auto: emoción/energía → Cuidar")
        ok(det("¿en qué ensayo clínico encaja mi biopsia?") == ("polaris-mision", True),
           "auto: caso/ensayo → Misión")
        ok(det("monta una tool nueva para el sistema")[0] == "polaris-construir",
           "auto: construir el sistema → Construir")
        ok(det("¿dónde quedó archivado el resumen de ayer?") == ("polaris-vivir", False),
           "auto: sin señal → Vivir (default, no se anuncia)")
        # override manual SIEMPRE gana (aunque el texto pida otro modo) y NO marca auto
        mov, autov = gw._resolver_modo("polaris-cuidar", texto="¿en qué ensayo encaja mi biopsia?")
        ok(mov.get("name") == "polaris-cuidar" and autov is False,
           "override manual gana sobre la auto-detección, y no se anuncia")
        # pasar el modo DEFAULT (Vivir) = "sin elección" → se auto-detecta
        mod, autod = gw._resolver_modo("polaris-vivir", texto="estoy agotada")
        ok(mod.get("name") == "polaris-cuidar" and autod is True,
           "Vivir/default = sin override → se auto-detecta")
        # el anuncio "→ modo X" aparece SOLO cuando lo eligió la auto-detección
        set_registro([FREE, CLAUDE])
        stub({"nvidia-free": ("ok", "CUERPO"), "claude": ("ok", "X")})
        st, body = gw.responder_chat(msg("estoy agotada y con ansiedad"))
        txt = body["choices"][0]["message"]["content"] if st == 200 else ""
        ok(st == 200 and txt.startswith("→ modo") and "CUERPO" in txt,
           "auto-detección anuncia '→ modo …' en 1 línea sobre la respuesta")
        st, body = gw.responder_chat(msg("¿dónde está el resumen?"))
        txt = body["choices"][0]["message"]["content"] if st == 200 else ""
        ok(st == 200 and not txt.startswith("→ modo"), "Vivir (default) no añade anuncio")

    # ── AUTO-REPORTE de la puerta caída (503 hacia {{TITULAR}} → avisa YA, anti-spam) ─────────────
    # Cierra el hueco de que un fallo de la puerta (el "tope gastado") tenga que notarlo ella.
    gw._AVISO_PUERTA_COOLDOWN_S = 3600
    try:
        os.remove(os.path.join(_TMP, "borde_gateway", "aviso_puerta.json"))
    except OSError:
        pass
    _avisos.clear()
    set_registro([FREE, CLAUDE])
    stub({"nvidia-free": ("fail", "limite"), "claude": ("fail", "tope_local")})
    st, _ = gw.responder_chat(msg("resume esto por favor"))
    ok(st == 503 and len(_avisos) == 1 and "sube" in _avisos[0].lower(),
       "503 hacia {{TITULAR}} → auto-reporte inmediato (sugiere «sube» si fue tope)")
    st, _ = gw.responder_chat(msg("resume esto otra vez"))
    ok(st == 503 and len(_avisos) == 1, "auto-reporte anti-spam: no repite dentro del cooldown")

    # body inválido / prompt vacío
    ok(gw.responder_chat({"messages": []})[0] == 400, "messages vacío → 400")
    ok(gw.responder_chat({"messages": [{"role": "system", "content": "x"}]})[0] == 400,
       "solo system (sin prompt) → 400")

    # mapeo de varios turnos → transcripción
    prompt, system = gw._mensajes_a_prompt([{"role": "system", "content": "sé breve"},
                                            {"role": "user", "content": "hola"},
                                            {"role": "assistant", "content": "qué tal"},
                                            {"role": "user", "content": "bien"}])
    ok(system == "sé breve" and "user: hola" in prompt and "assistant: qué tal" in prompt,
       "mensajes→prompt: system aparte + transcripción multi-turno")

    # ── modo solo-no-sensible (--solo-no-sensible / BORDE_GW_NO_SENSIBLE=1) ───────────────
    # Canario SINTÉTICO: HGVS + HLA, CERO PII (nada de {{TITULAR}}, ningún nombre real). Con Claude
    # ARRIBA y de confianza, en modo endurecido debe dar 403 ANTES de enrutar (ni siquiera a Claude).
    CANARIO = "anota la variante c.524G>A (p.R175H) en el alelo HLA-A*02:01"
    LIMPIO = "resume en una frase qué es un grafo dirigido acíclico"

    # baseline OFF: el canario sensible CON Claude de confianza disponible → 200 (va a Claude, permitido)
    os.environ.pop("BORDE_GW_NO_SENSIBLE", None)
    set_registro([CLAUDE])
    stub({"claude": ("ok", "RESP-CLAUDE")})
    st, body = gw.responder_chat(msg(CANARIO))
    ok(st == 200 and body["choices"][0]["message"]["content"] == "RESP-CLAUDE",
       "flag OFF: canario sensible con Claude de confianza → 200 (comportamiento de antes)")

    # ON: el MISMO canario, con el MISMO Claude arriba → 403 muro_denied, sin fuga, deny en ledger
    os.environ["BORDE_GW_NO_SENSIBLE"] = "1"
    set_registro([CLAUDE])
    stub({"claude": ("ok", "NO-DEBE-SALIR")})
    st, body = gw.responder_chat(msg(CANARIO))
    leak = json.dumps(body, ensure_ascii=False)
    ok(st == 403 and "muro" in body["error"]["type"],
       "flag ON: canario sensible → 403 AUNQUE Claude esté disponible")
    ok("R175H" not in leak and "HLA-A" not in leak and "NO-DEBE-SALIR" not in leak,
       "flag ON: el 403 NO filtra el contenido sensible ni la respuesta")
    deny_gw = False
    for f in glob.glob(os.path.join(_TMP, "borde", "ledger-*.jsonl")):
        for ln in open(f, encoding="utf-8"):
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            if rec.get("evento") == "egress" and rec.get("permitido") is False \
                    and "solo-no-sensible" in (rec.get("destino") or ""):
                deny_gw = True
    ok(deny_gw, "flag ON: el rechazo quedó sellado como deny en el ledger (solo metadatos)")

    # ON: una tarea LIMPIA → 200 (el modo endurecido no rompe lo no-sensible)
    set_registro([CLAUDE])
    stub({"claude": ("ok", "OK-LIMPIO")})
    st, body = gw.responder_chat(msg(LIMPIO))
    ok(st == 200 and body["choices"][0]["message"]["content"] == "OK-LIMPIO",
       "flag ON: tarea limpia → 200 (no-sensible pasa)")

    # vuelve a OFF para no contaminar el resto de la suite (default OFF, retrocompatible)
    os.environ.pop("BORDE_GW_NO_SENSIBLE", None)

    # ── HTTP real (loopback, puerto efímero) ─────────────────────────────────────────────
    set_registro([FREE, CLAUDE])
    stub({"nvidia-free": ("ok", "OK-HTTP"), "claude": ("ok", "X")})
    token = gw.cargar_token()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), gw.Handler)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        def http(method, path, tok=None, payload=None):
            url = "http://127.0.0.1:%d%s" % (port, path)
            data = json.dumps(payload).encode() if payload is not None else None
            req = urllib.request.Request(url, data=data, method=method)
            if tok:
                req.add_header("Authorization", "Bearer " + tok)
            if data is not None:
                req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return r.status, json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")

        ok(http("POST", "/v1/chat/completions", None, msg("hola"))[0] == 401, "sin token → 401")
        ok(http("POST", "/v1/chat/completions", "malo", msg("hola"))[0] == 401, "token malo → 401")
        ok(http("GET", "/v1/desconocido", token)[0] == 404, "ruta desconocida → 404")
        stc, b = http("GET", "/v1/models", token)
        # /v1/models lista los MODOS seleccionables (Vivir/Misión/Cuidar/Construir), no los cerebros crudos.
        ok(stc == 200 and any(m["id"] == "polaris-vivir" for m in b["data"]), "GET /v1/models → 200 lista (modos)")
        stc, b = http("POST", "/v1/chat/completions", token, msg("hola"))
        ok(stc == 200 and b["choices"][0]["message"]["content"] == "OK-HTTP", "POST con token → 200")

        # stream:true → SSE (lo que pide OpenCode/Goose); el texto YA pasó por el borde
        def http_raw(path, tok, payload):
            url = "http://127.0.0.1:%d%s" % (port, path)
            req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
            req.add_header("Authorization", "Bearer " + tok)
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8")
        p = msg("hola"); p["stream"] = True
        sst, sct, sbody = http_raw("/v1/chat/completions", token, p)
        deltas = "".join(json.loads(l[6:])["choices"][0]["delta"].get("content", "")
                         for l in sbody.splitlines()
                         if l.startswith("data: ") and l[6:].strip() != "[DONE]")
        ok(sst == 200 and "text/event-stream" in sct and deltas == "OK-HTTP"
           and "data: [DONE]" in sbody, "stream:true → SSE OpenAI + texto del borde + [DONE]")

        # stream:true PERO el muro rechaza (sin cerebro) → error JSON, NO SSE a medias
        stub({"nvidia-free": ("fail", "x"), "claude": ("fail", "y")})
        p2 = msg("hola"); p2["stream"] = True
        stc2, b2 = http("POST", "/v1/chat/completions", token, p2)
        ok(stc2 == 503 and "error" in b2, "stream:true + cadena agotada → 503 JSON (no SSE)")
        stub({"nvidia-free": ("ok", "OK-HTTP"), "claude": ("ok", "X")})
    finally:
        srv.shutdown()
        srv.server_close()

    # loopback-only: el guard rechaza bindear fuera de 127.0.0.1
    ok(gw.serve(host="0.0.0.0") == 2, "serve fuera de loopback → rechazado (muro)")

    print("RESULTADO borde_gateway: %d OK, %d fallos" % (_pass, _fail))
    print("✅ GATEWAY DEL BORDE EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
