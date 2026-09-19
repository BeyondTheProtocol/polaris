#!/usr/bin/env python3
"""test_ia.py — evals de la centralita `tools/ia.py` (F0/F1, plan expressive-plotting-flame).

Verifica el ROUTER (determinista) y los adaptadores, en estado AISLADO:
  · No-sensible → cerebro esperado (gratis primero por 'orden'); si falla → RELEVO al siguiente.
  · Clínico/sensible → SOLO cerebro de confianza; si no hay → se NIEGA (nunca a nube).
  · EL BORDE es la única puerta: aunque el registro marque un cerebro de nube como 'trusted' por
    error, el borde bloquea el contenido sensible hacia él (defensa en profundidad).
  · Cadena agotada → deferred=True (no excepción). · Clínico que releva → aviso RUIDOSO.
  · Adaptadores: _call_claude (parseo éxito/límite/crédito con bin falso) · _call_carril_gratis
    (centinela = fallo). · health() devuelve lista y escribe JSON. · prompt vacío.
Estilo test_borde/test_muro_fase0: cada caso suma OK/fallo; exit = nº de fallos.
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
_TMP = tempfile.mkdtemp(prefix="ia_test_")
os.environ["BTP_STATE_DIR"] = _TMP
# MURO DE LA BATERIA (15-jul-2026): este test llamaba a ia.ask CLINICO/critico -> _parar ->
# salida.report_to_titular SIN muro ni stub -> Telegram REAL a {{TITULAR}} cada ejecucion. Era la FUENTE
# del spam de 🔴 del 15-jul (dirs /var/folders/.../ia_test_*). Con BTP_TEST_BATTERY + el STATE ya
# aislado de arriba, salida.send() se niega a entregar aunque la boca sea la real. (El gate de
# origen de _parar tambien lo cubre; esto es la segunda linea de defensa, como en test_freno.)
os.environ["BTP_TEST_BATTERY"] = "1"
os.environ["BTP_HALT_FILES"] = os.path.join(_TMP, "nh_a") + ":" + os.path.join(_TMP, "nh_b")
os.environ["BTP_CANARIOS"] = "CANARIO-IA-9911"
os.environ["BTP_PERIPHERIES"] = os.path.join(_TMP, "peripheries.json")
# presupuesto holgado para que cost_guard no bloquee _call_claude
os.makedirs(os.path.join(_TMP, "cost"), exist_ok=True)
json.dump({"diario_usd": 30.0, "job_usd": 3.0},
          open(os.path.join(_TMP, "cost", "limits.json"), "w"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import ia      # noqa: E402
import borde   # noqa: E402

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


# Cerebros de prueba reutilizables. capability como en producción (Claude alto = pasa el freno
# de criticidad; el local modesto y los de nube, bajos).
FREE = {"name": "nvidia-free", "kind": "carril_gratis", "destino": "nvidia",
        "trusted": False, "free": True, "orden": 10, "capability": 3, "enabled": True}
CLAUDE = {"name": "claude", "kind": "claude", "destino": "cleared:claude",
          "trusted": True, "orden": 20, "capability": 9, "enabled": True, "models": ["sonnet"]}
# cerebro MAL configurado: el registro lo dice trusted, pero su destino es de NUBE
FALSO_TRUSTED = {"name": "nube-falsa", "kind": "carril_gratis", "destino": "nvidia",
                 "trusted": True, "orden": 5, "capability": 9, "enabled": True}


def _stub(behavior):
    """Sustituye ia._invocar por un stub determinista keyed por nombre de cerebro.
    behavior[name] = ('ok', texto) | ('fail', motivo)."""
    def fake(cerebro, prompt, system, clinico, interactivo=False, critico=False):
        b = behavior.get(cerebro.get("name"), ("fail", "no-config"))
        if b[0] == "ok":
            return b[1], 0.0, None
        return None, 0.0, b[1]
    ia._invocar = fake


def main():
    # ── ROUTER (con _invocar stubbeado; el borde sigue siendo REAL) ──────────────────────
    # El filtro de salud (caché TTL) se prueba aparte en _test_salud_filtro; aquí lo neutralizamos
    # (sin filtrar) para que el router/freno sean deterministas sin depender de claves/red reales.
    _invocar_real = ia._invocar   # guardado ANTES de cualquier _stub(), para los tests E2E reales
    _salud_real = ia._salud_disponibles
    ia._salud_disponibles = lambda: {}
    # T1 no-sensible: gratis primero
    set_registro([FREE, CLAUDE])
    _stub({"nvidia-free": ("ok", "DESDE-FREE"), "claude": ("ok", "DESDE-CLAUDE")})
    r = ia.ask("resume estos tres párrafos")
    ok(r["brain"] == "nvidia-free" and r["text"] == "DESDE-FREE" and not r["degradado"],
       "no-sensible → gratis primero")

    # T2 no-sensible: el gratis falla → RELEVO a claude (degradado)
    _stub({"nvidia-free": ("fail", "limite"), "claude": ("ok", "DESDE-CLAUDE")})
    r = ia.ask("resume esto")
    ok(r["brain"] == "claude" and r["degradado"] and not r["deferred"],
       "no-sensible → relevo gratis→claude")

    # T3 clínico → SOLO confianza (claude); el gratis ni se intenta
    _stub({"nvidia-free": ("ok", "NUNCA"), "claude": ("ok", "DESDE-CLAUDE")})
    r = ia.ask("pregunta clínica genérica", clinico=True)
    ok(r["brain"] == "claude" and r["text"] == "DESDE-CLAUDE", "clínico → solo claude (confianza)")

    # T3b sensible AUTO-detectado (sin flag) → también solo confianza
    r = ia.ask("el caso de {{TITULAR}} {{APELLIDO}}")
    ok(r["brain"] == "claude", "sensible auto-detectado → solo confianza")

    # T3c clínico con DOS de confianza (local cap4 orden5 + claude cap9 orden20) → gana CAPACIDAD
    # (Claude), NO el de orden menor. (muro→capacidad; un local modesto no opina de lo clínico.)
    set_registro([dict(CLAUDE, name="local", destino="local:x", trusted=True, orden=5, capability=4),
                  dict(CLAUDE, name="claude", orden=20, capability=9)])
    _stub({"local": ("ok", "LOCAL"), "claude": ("ok", "CLAUDE")})
    r = ia.ask("pregunta clínica genérica", clinico=True)
    ok(r["brain"] == "claude", "clínico: gana capacidad (Claude) sobre local de orden menor")

    # T4 sensible y NINGÚN cerebro de confianza → se NIEGA, NUNCA a nube
    set_registro([FREE])  # solo el de nube
    _stub({"nvidia-free": ("ok", "NO-DEBE-SALIR")})
    r = ia.ask("datos de {{TITULAR}}: KI-67 alto", clinico=True)
    ok(r["text"] is None and not r["deferred"] and "confianza" in r["motivo"].lower(),
       "sensible sin cerebro de confianza → se niega (no nube)")

    # T5 BORDE defensa-en-profundidad: registro dice trusted pero destino es de nube →
    #    el borde bloquea el contenido sensible aunque el router lo intente.
    set_registro([FALSO_TRUSTED])
    _stub({"nube-falsa": ("ok", "NO-DEBE-SALIR")})
    r = ia.ask("variante R175H del tumor", clinico=True)
    ok(r["text"] is None, "borde bloquea sensible hacia 'trusted' mal configurado (nube)")

    # T6 cadena agotada → deferred (no excepción)
    set_registro([FREE, CLAUDE])
    _stub({"nvidia-free": ("fail", "limite"), "claude": ("fail", "credito")})
    r = ia.ask("trabajo no sensible")
    ok(r["deferred"] and r["text"] is None, "todos fallan → deferred (no muere)")

    # T6b TOPE LOCAL: cuando claude falla con 'tope_local' (presupuesto nuestro agotado),
    # el motivo del ask dice "tope de gasto" (no "recargar saldo") — distingue del prepago real.
    set_registro([FREE, CLAUDE])
    _stub({"nvidia-free": ("fail", "limite"), "claude": ("fail", "tope_local")})
    r = ia.ask("trabajo no sensible")
    ok(r["deferred"] and "tope" in r["motivo"].lower(),
       "tope_local en todos → deferred con motivo 'tope de gasto' (no 'recargar')")
    ok("recargar" not in r["motivo"].lower() or "tope" in r["motivo"].lower(),
       "tope_local → el mensaje no confunde con prepago agotado")

    # T6c PREPAGO AGOTADO: cuando claude falla con 'credito' (400 API real), el motivo
    # del ask dice "prepago de Anthropic" — diferente del tope local.
    _stub({"nvidia-free": ("fail", "limite"), "claude": ("fail", "credito")})
    r = ia.ask("trabajo no sensible")
    ok(r["deferred"] and "prepago" in r["motivo"].lower(),
       "credito (400 API) en todos → deferred con motivo 'prepago agotado'")

    # T7 clínico que releva → aviso RUIDOSO (dos cerebros de confianza)
    avisos = []
    ia._aviso_clinico = lambda brain, motivo: avisos.append((brain, motivo))
    CLAUDE_A = dict(CLAUDE, name="claude-a", orden=20)
    CLAUDE_B = dict(CLAUDE, name="claude-b", orden=21)
    set_registro([CLAUDE_A, CLAUDE_B])
    _stub({"claude-a": ("fail", "limite"), "claude-b": ("ok", "OK-B")})
    r = ia.ask("consulta clínica", clinico=True)
    ok(r["brain"] == "claude-b" and len(avisos) >= 1, "clínico relevo → aviso ruidoso")

    # T8 prompt vacío
    ok(ia.ask("")["text"] is None, "prompt vacío → sin texto")

    # T9 RELEVO DE CORTESÍA (el bug del "tope gastado" en Vivir): charla SENSIBLE pero NO crítica;
    # el de pago (claude) topa NUESTRO presupuesto (tope_local) y el único de confianza por debajo
    # del suelo es el LOCAL → en vez de quedarse MUDA, releva al local de confianza. Muro intacto.
    LOCAL_MODESTO = {"name": "local", "kind": "openai_local", "destino": "local:x",
                     "trusted": True, "free": True, "orden": 5, "capability": 4, "enabled": True}
    set_registro([LOCAL_MODESTO, CLAUDE])
    _stub({"local": ("ok", "DESDE-LOCAL"), "claude": ("fail", "tope_local")})
    r = ia.ask("el caso de {{TITULAR}} {{APELLIDO}} hoy")   # sensible (auto-detectado), NO crítico
    ok(r["brain"] == "local" and r["text"] == "DESDE-LOCAL" and not r["deferred"] and r["degradado"],
       "sensible no-crítico + claude topa presupuesto → relevo de cortesía al local (Vivir no enmudece)")

    # T9b PERO el suelo CLÍNICO sigue intacto: en tarea clínica/crítica el local flojo NUNCA sirve
    # aunque claude tope — la tarea PARA (seguridad), no se degrada a un cerebro por debajo del suelo.
    set_registro([LOCAL_MODESTO, CLAUDE])
    _stub({"local": ("ok", "NO-DEBE-SERVIR"), "claude": ("fail", "tope_local")})
    r = ia.ask("el caso de {{TITULAR}} {{APELLIDO}}", clinico=True)
    ok(r["text"] is None and r["brain"] != "local",
       "clínico: el local flojo NUNCA sirve aunque claude tope (suelo clínico intacto)")

    # T9c END-TO-END (regla de {{TITULAR}}, 2/7/26): ask() con SENSIBLE auto-detectado + tope local
    # topado + saldo REAL sano → NO releva al local ni se aplaza: llega a Claude de verdad (salta
    # el tope). Aquí SÍ usamos _call_claude real (sin stub de _invocar) para probar el cableado
    # completo ask()→_invocar()→_call_claude() con el parámetro `critico` viajando de punta a punta.
    import cost_guard as cg_test_e2e
    _orig_check_e2e = cg_test_e2e.check_before_job
    cg_test_e2e.check_before_job = lambda **kw: (
        False, "[tope_local] tope DIARIO alcanzado para rutina "
        "(hoy gastado: $20.00 de $20.00 fijado por nosotros; "
        "el saldo de Anthropic sigue ahí — sube el tope con 1 clic)", 0.0)
    okj_e2e = '{"result": "RESPUESTA-REAL", "is_error": false, "total_cost_usd": 0.01}'
    os.environ["BTP_CLAUDE_BIN"] = _fake_claude_bin({"sonnet": okj_e2e})
    set_registro([LOCAL_MODESTO, CLAUDE])
    ia._invocar = _invocar_real   # restaura la implementación real (T9b la dejó stubbeada)
    r = ia.ask("el caso de {{TITULAR}} {{APELLIDO}} hoy")   # sensible (auto-detectado), NO crítico explícito
    ok(r["brain"] == "claude" and r["text"] == "RESPUESTA-REAL" and not r["deferred"],
       "E2E: sensible + tope local topado + saldo real sano → llega a Claude real (salta el tope)")
    del os.environ["BTP_CLAUDE_BIN"]
    cg_test_e2e.check_before_job = _orig_check_e2e

    # ── ADAPTADORES (reales) ─────────────────────────────────────────────────────────────
    _test_call_claude()
    _test_call_carril_gratis()
    # cerebro LOCAL sin servidor → falla rápido (relevo), no excepción
    t, usd, f = ia._call_local({"url": "http://127.0.0.1:1/x", "model": "x", "timeout": 1}, "hola", None)
    ok(t is None and f == "fallo", "_call_local sin servidor → fallo (relevo limpio)")

    # ── health() ─────────────────────────────────────────────────────────────────────────
    set_registro([FREE, CLAUDE])
    h = ia.health()
    ok(isinstance(h, list) and any(c["name"] == "claude" for c in h), "health() devuelve lista")
    ok(os.path.exists(os.path.join(_TMP, "ia", "health.json")), "health() escribe JSON")

    # ── FILTRO DE SALUD (caché TTL) — con la función real restaurada ──────────────────────
    ia._salud_disponibles = _salud_real
    _test_salud_filtro()

    print("RESULTADO ia (centralita): %d OK, %d fallos" % (_pass, _fail))
    print("✅ CENTRALITA EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


def _test_salud_filtro():
    """El caché de salud salta los cerebros marcados no-disponibles ANTES de invocarlos; si el
    caché miente (marca vivo uno que falla), el relevo reactivo sigue cubriendo."""
    import time
    os.environ["BTP_HEALTH_TTL"] = "300"
    ia.HEALTH_TTL_SEG = 300
    hpath = os.path.join(_TMP, "ia", "health.json")
    os.makedirs(os.path.dirname(hpath), exist_ok=True)
    set_registro([FREE, CLAUDE])

    # Caché FRESCO que dice: nvidia-free CAÍDO, claude vivo → ask salta nvidia sin invocarlo.
    json.dump({"ts": "x", "cerebros": [{"name": "nvidia-free", "disponible": False},
                                       {"name": "claude", "disponible": True}]}, open(hpath, "w"))
    os.utime(hpath, None)   # mtime = ahora → fresco
    invocados = []
    orig = ia._invocar
    def spy(c, p, s, cl, interactivo=False, critico=False):
        invocados.append(c.get("name"))
        return ("DESDE-%s" % c.get("name"), 0.0, None)
    ia._invocar = spy
    r = ia.ask("resume esto")
    ok(r["brain"] == "claude" and "nvidia-free" not in invocados,
       "salud caché: salta el caído (nvidia) sin invocarlo, sirve claude")

    # Caché que MIENTE: dice nvidia vivo pero al invocarlo falla → relevo reactivo a claude.
    json.dump({"ts": "x", "cerebros": [{"name": "nvidia-free", "disponible": True},
                                       {"name": "claude", "disponible": True}]}, open(hpath, "w"))
    os.utime(hpath, None)
    def spy2(c, p, s, cl, interactivo=False, critico=False):
        if c.get("name") == "nvidia-free":
            return (None, 0.0, "fallo")   # el caché mintió: estaba "vivo" pero falla
        return ("DESDE-claude", 0.0, None)
    ia._invocar = spy2
    r = ia.ask("resume esto")
    ok(r["brain"] == "claude" and r["degradado"],
       "salud caché miente (vivo pero falla) → red de seguridad = relevo reactivo")
    ia._invocar = orig


def _fake_claude_bin(behavior):
    path = os.path.join(_TMP, "fake_claude.sh")
    body = ("#!/bin/bash\nm=''\n"
            "while [ $# -gt 0 ]; do [ \"$1\" = --model ] && { m=\"$2\"; shift 2; continue; }; shift; done\n"
            "case \"$m\" in\n")
    for modelo, jsonout in behavior.items():
        body += "  %s) echo '%s' ;;\n" % (modelo, jsonout.replace("'", "'\\''"))
    body += "  *) echo '{\"is_error\":true}' ;;\nesac\n"
    open(path, "w").write(body)
    os.chmod(path, 0o755)
    return path


def _test_call_claude():
    import cost_guard as cg_test
    okj = '{\"result\": \"HOLA\", \"is_error\": false, \"total_cost_usd\": 0.01}'
    lim = '{\"api_error_status\": 429, \"is_error\": true}'
    cred = '{\"api_error_status\": 400, \"is_error\": true, \"error\": \"Credit balance is too low\"}'
    # éxito con el primer modelo
    os.environ["BTP_CLAUDE_BIN"] = _fake_claude_bin({"sonnet": okj})
    t, usd, fallo = ia._call_claude("hola", None, ["sonnet"])
    ok(t == "HOLA" and fallo is None, "_call_claude éxito")
    # límite en sonnet → degrada a haiku (éxito)
    os.environ["BTP_CLAUDE_BIN"] = _fake_claude_bin({"sonnet": lim, "haiku": okj})
    t, usd, fallo = ia._call_claude("hola", None, ["sonnet", "haiku"])
    ok(t == "HOLA" and fallo is None, "_call_claude degrada sonnet→haiku")
    # 400 API real (prepago Anthropic agotado) → fallo 'credito' (no insiste, hay que recargar)
    os.environ["BTP_CLAUDE_BIN"] = _fake_claude_bin({"sonnet": cred, "haiku": okj})
    t, usd, fallo = ia._call_claude("hola", None, ["sonnet", "haiku"])
    ok(t is None and fallo == "credito", "_call_claude 400 API → fallo credito (prepago agotado)")
    del os.environ["BTP_CLAUDE_BIN"]
    # TOPE LOCAL agotado → fallo 'tope_local' (DISTINTO de 'credito'; el dinero en Anthropic sigue ahí)
    # Simulamos: cost_guard dice ok=False con motivo [tope_local]
    _orig_check = cg_test.check_before_job
    cg_test.check_before_job = lambda **kw: (False, "[tope_local] tope DIARIO alcanzado para rutina "
                                             "(hoy gastado: $30.0000 de $30.00 fijado por nosotros; "
                                             "el saldo de Anthropic sigue ahí — sube el tope con 1 clic)", 0.0)
    os.environ["BTP_CLAUDE_BIN"] = _fake_claude_bin({"sonnet": okj})
    t, usd, fallo = ia._call_claude("hola", None, ["sonnet"])
    ok(t is None and fallo == "tope_local",
       "_call_claude tope local agotado → fallo tope_local (no credito)")
    cg_test.check_before_job = _orig_check
    del os.environ["BTP_CLAUDE_BIN"]

    # 🔴 Regla de {{TITULAR}} (2/7/26): CRÍTICO=True + tope LOCAL topado + saldo REAL sano → SALTA el
    # tope e igual llama a la API real (no se corta por nuestro cap interno).
    cg_test.check_before_job = lambda **kw: (False, "[tope_local] tope DIARIO alcanzado para rutina "
                                             "(hoy gastado: $20.00 de $20.00 fijado por nosotros; "
                                             "el saldo de Anthropic sigue ahí — sube el tope con 1 clic)", 0.0)
    os.environ["BTP_CLAUDE_BIN"] = _fake_claude_bin({"sonnet": okj})
    t, usd, fallo = ia._call_claude("hola clínica", None, ["sonnet"], critico=True)
    ok(t == "HOLA" and fallo is None,
       "_call_claude critico=True + tope_local + saldo sano → SALTA el tope, responde real")
    del os.environ["BTP_CLAUDE_BIN"]

    # PERO si el saldo REAL está agotado (400 de verdad), crítico NO insiste a ciegas: el 400 real
    # sigue siendo un bloqueo legítimo (no es el tope nuestro, es que no hay dinero).
    os.environ["BTP_CLAUDE_BIN"] = _fake_claude_bin({"sonnet": cred})
    t, usd, fallo = ia._call_claude("hola clínica", None, ["sonnet"], critico=True)
    ok(t is None and fallo == "credito",
       "_call_claude critico=True + tope_local + saldo REAL agotado (400) → SÍ bloquea (credito)")
    del os.environ["BTP_CLAUDE_BIN"]

    # Y una tarea NO crítica (critico=False, default) con el MISMO tope topado SIGUE respetando el
    # tope como siempre — el salto es EXCLUSIVO de lo crítico/clínico/sensible.
    os.environ["BTP_CLAUDE_BIN"] = _fake_claude_bin({"sonnet": okj})
    t, usd, fallo = ia._call_claude("hola rutina", None, ["sonnet"])
    ok(t is None and fallo == "tope_local",
       "_call_claude critico=False (default) + tope_local → SIGUE respetando el tope (no lo salta)")
    cg_test.check_before_job = _orig_check
    del os.environ["BTP_CLAUDE_BIN"]


def _test_call_carril_gratis():
    import carril_gratis
    orig = carril_gratis.responder
    carril_gratis.responder = lambda prompt, system=None, fallback="", model=None: "RESP-LIBRE"
    t, usd, fallo = ia._call_carril_gratis("hola", None)
    ok(t == "RESP-LIBRE" and fallo is None and usd == 0.0, "_call_carril_gratis éxito")
    carril_gratis.responder = lambda prompt, system=None, fallback="", model=None: fallback
    t, usd, fallo = ia._call_carril_gratis("hola", None)
    ok(t is None and fallo == "fallo", "_call_carril_gratis centinela → fallo")
    # El modelo del registro LLEGA al carril (así nvidia-deepseek no acaba sirviendo Nemotron).
    visto = {}
    def _espia(prompt, system=None, fallback="", model=None):
        visto["model"] = model
        return "RESP"
    carril_gratis.responder = _espia
    ia._call_carril_gratis("hola", None, model="deepseek-ai/deepseek-v4-pro")
    ok(visto.get("model") == "deepseek-ai/deepseek-v4-pro",
       "_call_carril_gratis pasa el modelo del registro al carril")
    carril_gratis.responder = orig


if __name__ == "__main__":
    sys.exit(main())
