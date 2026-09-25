#!/usr/bin/env python3
"""test_freno_criticidad.py — el FRENO DE CRITICIDAD (lo nº1, SEGURIDAD CLÍNICA).

Prueba el EFECTO, no que "corre": una tarea CRÍTICA/clínica NUNCA se sirve con un cerebro flojo
(el local 3B), aunque sea el único disponible — PARA. La barrera es ESTE freno (el borde no
protege aquí porque el local ES trusted). Aislado en tmp; el borde y el cost_guard son REALES, el
cerebro se finge con un stub determinista de _invocar.

Casos:
  ⭐ ESTRELLA  crítico + solo-local-3B disponible          → PARADO, NO servido (nada de 3B clínico)
     clínico + Claude caído                                → PARA, el 3B no contesta
     rutina + Claude caído (solo 3B vivo)                  → degrada y SIRVE (no sobre-bloqueado)
     crítico con Claude capaz disponible                   → SIRVE (no sobre-bloquea de más)
     parado dispara aviso FUERTE por sí mismo (4b)         → _parar llama a salida (mockeado)
     mensual agotado → deferred/parado y mensual=suma de diarios (sin doble conteo)
     borde.py status corre sin crashear (el NameError arreglado)
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="freno_test_")
os.environ["BTP_STATE_DIR"] = _TMP
# MURO DE LA BATERIA (14-jul-2026): este fichero RESTAURA salida.report_to_titular a la boca
# REAL a mitad de camino y luego sigue con TOPE_DIARIO_USD=30 — estaba a UNA palabra
# (esencial=True) de llamar al telefono de {{TITULAR}} de verdad. Con esta senal + el STATE
# aislado de arriba, salida.send() se niega a entregar aunque la boca sea la real.
os.environ["BTP_TEST_BATTERY"] = "1"
# Simula que corremos DENTRO del lazo (launchd). Desde 15-jul _parar solo avisa si el paron viene
# del lazo (salida._origen); un paron en un diagnostico/test NO pinga a {{TITULAR}}. Este test verifica
# el comportamiento del FRENO en produccion (el lazo), asi que se declara lazo.
os.environ["XPC_SERVICE_NAME"] = "com.btp.test-freno"
os.environ["BTP_HALT_FILES"] = os.path.join(_TMP, "nh_a") + ":" + os.path.join(_TMP, "nh_b")
os.environ["BTP_PERIPHERIES"] = os.path.join(_TMP, "peripheries.json")
os.environ["BTP_HEALTH_TTL"] = "0"   # caché de salud desactivado → siempre fresco (determinista)
os.makedirs(os.path.join(_TMP, "cost"), exist_ok=True)
json.dump({"tope_diario_usd": 30.0, "tope_job_usd": 3.0, "tope_mensual_usd": 200.0},
          open(os.path.join(_TMP, "cost", "limits.json"), "w"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import ia          # noqa: E402
import cost_guard  # noqa: E402

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


# El cerebro LOCAL 3B = trusted (local:) pero capability 4 (POR DEBAJO del umbral crítico 7).
LOCAL_3B = {"name": "local-3b", "kind": "openai_local", "destino": "local:ollama",
            "trusted": True, "free": True, "orden": 5, "capability": 4, "enabled": True}
CLAUDE = {"name": "claude", "kind": "claude", "destino": "cleared:claude",
          "trusted": True, "free": False, "orden": 20, "capability": 9, "enabled": True,
          "models": ["sonnet"]}


def _stub(behavior):
    def fake(c, prompt, system, clinico, interactivo=False, critico=False):
        b = behavior.get(c.get("name"), ("fail", "no-config"))
        return (b[1], 0.0, None) if b[0] == "ok" else (None, 0.0, b[1])
    ia._invocar = fake


# Mockear el aviso fuerte: capturarlo sin tocar Telegram (no hay notif config en tmp igualmente).
_avisos = []
ia._salud_disponibles = lambda: {}   # neutralizado salvo donde se pruebe (no toca el efecto del freno)


def main():
    import salida
    orig_report = salida.report_to_titular
    salida.report_to_titular = lambda *a, **k: _avisos.append((a, k))

    # ⭐ TEST ESTRELLA: crítico + SOLO el local 3B disponible (Claude no está en el registro) →
    #    PARADO, NO servido. El 3B (cap 4) NUNCA contesta lo crítico aunque sea el único.
    set_registro([LOCAL_3B])
    _stub({"local-3b": ("ok", "RESPUESTA-3B-NO-DEBE-SALIR")})
    r = ia.ask("evalúa este caso clínico", critico_tarea=True)
    ok(r.get("parado") is True and r.get("text") is None
       and "RESPUESTA-3B-NO-DEBE-SALIR" != r.get("text"),
       "⭐ ESTRELLA: crítico + solo-3B → PARADO, el 3B NO sirve")
    ok(r.get("desbloquea") and "capacidad" in r.get("motivo", "").lower(),
       "⭐ ESTRELLA: el parado dice motivo + cómo desbloquearlo")

    # 4b: el parado disparó un aviso FUERTE por SÍ MISMO (no depende del caller).
    ok(len(_avisos) >= 1 and any(k.get("urgente") for (_a, k) in _avisos),
       "4b: el deferral crítico dispara aviso fuerte por sí mismo (urgente)")

    # CLÍNICO + Claude caído: el local 3B existe y está vivo, pero NO contesta lo clínico → PARA.
    set_registro([LOCAL_3B, CLAUDE])
    _stub({"local-3b": ("ok", "3B-CLINICO-NO"), "claude": ("fail", "credito")})  # Claude sin saldo
    r = ia.ask("interpretación clínica de la biopsia", clinico=True)
    ok(r.get("parado") is True and r.get("text") is None,
       "clínico + Claude caído → PARA (el 3B no contesta lo clínico)")

    # RUTINA + Claude caído (solo 3B vivo) → degrada y SIRVE (NO sobre-bloqueado). El 3B es trusted
    #   pero para RUTINA el umbral no aplica: se sirve. Fail-safe hacia seguir.
    set_registro([LOCAL_3B, CLAUDE])
    _stub({"local-3b": ("ok", "RESPUESTA-RUTINA-OK"), "claude": ("fail", "credito")})
    r = ia.ask("resume estos tres párrafos")   # no crítico, no clínico, contenido limpio
    ok(r.get("text") == "RESPUESTA-RUTINA-OK" and not r.get("parado"),
       "rutina + Claude caído → degrada y SIRVE (no sobre-bloqueado)")

    # CRÍTICO con Claude CAPAZ disponible → SIRVE con Claude (el freno no sobre-bloquea de más).
    set_registro([LOCAL_3B, CLAUDE])
    _stub({"local-3b": ("ok", "3B-NO"), "claude": ("ok", "DESDE-CLAUDE")})
    r = ia.ask("evalúa este caso clínico", critico_tarea=True)
    ok(r.get("text") == "DESDE-CLAUDE" and not r.get("parado"),
       "crítico + Claude capaz → SIRVE con Claude (no sobre-bloquea)")

    # CRÍTICO: el local 3B (cap 4) se SALTA aunque vaya PRIMERO por orden; Claude (cap 9) sirve.
    set_registro([LOCAL_3B, CLAUDE])
    invocados = []
    def spy(c, p, s, cl, interactivo=False, critico=False):
        invocados.append(c.get("name"))
        return ("DESDE-%s" % c.get("name"), 0.0, None)
    ia._invocar = spy
    r = ia.ask("variante del tumor a interpretar", critico_tarea=True)
    ok("local-3b" not in invocados and r.get("brain") == "claude",
       "crítico: el 3B ni se invoca (se salta por capacidad); sirve Claude")

    salida.report_to_titular = orig_report
    _test_mensual()
    _test_borde_status()
    _test_antispam_parado()

    print("RESULTADO freno de criticidad: %d OK, %d fallos" % (_pass, _fail))
    print("✅ FRENO EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


def _test_mensual():
    """Mensual agotado → check_before_job False por 'MENSUAL'; y el mes = suma EXACTA de los días
    (derivado al leer, sin ledger paralelo → sin doble conteo)."""
    tmp = tempfile.mkdtemp(prefix="freno_mes_")
    cost_guard.STATE = tmp
    cost_guard.COST = os.path.join(tmp, "cost")
    cost_guard.LOCKDIR = os.path.join(cost_guard.COST, "lock")
    cost_guard.LIMITS = os.path.join(cost_guard.COST, "limits.json")
    cost_guard.DEGRADED = os.path.join(tmp, "hc", "deg.flag")
    cost_guard.HALT_FILES = (os.path.join(tmp, ".a"), os.path.join(tmp, ".b"))
    cost_guard.TOPE_DIARIO_USD = 30.0
    cost_guard.TOPE_JOB_USD = 3.0
    cost_guard.TOPE_MENSUAL_USD = 10.0
    cost_guard.TOPE_MENSUAL_MAX_ABSOLUTO = 10.0
    cost_guard._ensure_dirs()
    mes = cost_guard.datetime.now().strftime("%Y-%m")
    for dia, g in [("01", 4.0), ("02", 4.0)]:   # días anteriores del mes
        json.dump({"fecha": "%s-%s" % (mes, dia), "gastado_usd": g, "n_jobs": 1, "eventos": []},
                  open(os.path.join(cost_guard.COST, "%s-%s.json" % (mes, dia)), "w"))
    cost_guard.add_cost(1.0, job_id="hoy")       # hoy → mes = 9
    ok(abs(cost_guard.month_spent() - 9.0) < 1e-9, "mensual = suma de diarios (9 = 4+4+1)")
    # no doble conteo: el mes coincide EXACTO con la suma directa de los ficheros-día
    suma = sum(json.load(open(os.path.join(cost_guard.COST, f)))["gastado_usd"]
               for f in os.listdir(cost_guard.COST)
               if len(f) == 15 and f.endswith(".json"))
    ok(abs(suma - cost_guard.month_spent()) < 1e-9, "mensual sin doble conteo (= suma directa)")
    # mes 9 / tope 10 → restante 1 < job_cap 3 → BLOQUEA por MENSUAL (aunque el diario sobre)
    okc, motivo, _ = cost_guard.check_before_job()
    ok(okc is False and "MENSUAL" in motivo, "mensual agotado → check False y motivo distinguible")
    # con tope mensual holgado, el mismo gasto NO bloquea por mes (sí distingue de 'diario')
    cost_guard.TOPE_MENSUAL_USD = 200.0
    cost_guard.TOPE_MENSUAL_MAX_ABSOLUTO = 200.0
    okc2, motivo2, _ = cost_guard.check_before_job()
    ok(okc2 is True, "con mensual holgado el mismo gasto pasa (no es el diario el que frena)")


def _test_antispam_parado():
    """ANTI-SPAM del PARADO (bug del bucle, 25/6): una tarea crítica bloqueada se reintenta cada ciclo;
    el aviso FUERTE NO debe salir en bucle. Avisa 1× por motivo, recuerda cada 12h, y un motivo NUEVO
    avisa al momento. INVARIANTE: el freno (parado=True) nunca depende del aviso."""
    import shutil
    import json as _json
    import time as _time
    import hashlib
    import salida
    iadir = os.path.join(_TMP, "ia")
    if os.path.isdir(iadir):
        shutil.rmtree(iadir)
    sent = []
    orig = salida.report_to_titular
    salida.report_to_titular = lambda *a, **k: sent.append(a[0] if a else "")
    try:
        r1 = ia._parar("motivo-A", "recarga Claude", True)
        r2 = ia._parar("motivo-A", "recarga Claude", True)          # mismo motivo, en bucle
        ok(len(sent) == 1, "PARADO en bucle (mismo motivo) → avisa 1 sola vez: %d" % len(sent))
        ok(r1.get("parado") is True and r2.get("parado") is True,
           "PARADO: el FRENO no depende del aviso (parado=True siempre)")
        ia._parar("motivo-B", "recarga Claude", True)               # motivo NUEVO → avisa al momento
        ok(len(sent) == 2, "PARADO: un motivo nuevo avisa al momento")
        # simula que pasó la ventana de 12h para motivo-A → recuerda
        # (clave namespaced 'parado:<hash>' — _debe_avisar_parado usa ns='parado' de _debe_avisar,
        # generalizado 2/7/26 para que run_agent.sh pueda reusar el mismo mecanismo con otro ns).
        p = os.path.join(iadir, "aviso_parado.json")
        st = _json.load(open(p))
        clave = "parado:" + hashlib.sha1(b"motivo-A").hexdigest()[:12]
        st[clave] = _time.time() - (ia._PARADO_COOLDOWN_H + 1) * 3600
        _json.dump(st, open(p, "w"))
        ia._parar("motivo-A", "recarga Claude", True)
        ok(len(sent) == 3, "PARADO: pasada la ventana de 12h → recuerda")
    finally:
        salida.report_to_titular = orig


def _test_borde_status():
    """borde.py status corre sin crashear (el NameError TRUSTED_EXACTOS → _trusted_exactos)."""
    env = dict(os.environ, BTP_STATE_DIR=tempfile.mkdtemp(prefix="freno_borde_"))
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "borde.py"), "status"],
                       capture_output=True, text=True, env=env)
    ok(p.returncode == 0 and "trusted-exactos" in p.stdout and "Traceback" not in p.stderr,
       "borde.py status corre sin crashear")


if __name__ == "__main__":
    sys.exit(main())
