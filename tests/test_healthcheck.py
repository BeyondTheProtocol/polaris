#!/usr/bin/env python3
"""test_healthcheck.py — anti-spam del aviso de salud (healthcheck._emitir_si_cambia).

Bug (24/6): el healthcheck enviaba "🩺 Revisión de salud del lazo" en CADA ciclo (~30 min) aunque la
alerta fuera idéntica → toda la noche, 16 copias del mismo "comité-médico caído" (que era por el saldo
de Claude agotado). Fix: solo avisa cuando el CONJUNTO de alertas CAMBIA. Aislado (tmp, salida mock).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("estado")
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="hc_test_")
os.environ["BTP_STATE_DIR"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))
import healthcheck as hc   # noqa: E402
import salida              # noqa: E402

hc.HC = os.path.join(_TMP, "hc")
os.makedirs(hc.HC, exist_ok=True)
_sent = []
salida.report_to_titular = lambda *a, **k: _sent.append(a[0] if a else "")
# 18-sep-26 (deuda `test-healthcheck-poluciona-cola-real`, detectada 3 veces): redirigir
# BTP_STATE_DIR y hc.HC NO basta. healthcheck calcula `STATE = REPO/tools/state` al importarse y
# `_encolar_investigacion` escribe por el modulo `cola`, asi que cada pasada de tests metia jobs
# de investigacion en la COLA REAL del lazo 24/7. Se neutraliza aqui, igual que la boca: el test
# comprueba el anti-spam del AVISO, no el encolado, y no tiene por que ensuciar produccion.
_encolados = []
hc._encolar_investigacion = lambda clave, texto: (_encolados.append(clave), None)[1]
# Sin red en los tests: la sonda DNS de _salud_daemons se da por buena salvo en red_sin_dns_tests,
# que restaura la real y simula getaddrinfo. Así ningún test depende de la conexión del Mac.
_HAY_DNS_REAL = hc._hay_dns
hc._hay_dns = lambda *a, **k: True

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def salud_tests():
    """Watchdog de Vega: gate de saldo vs error, frescura, auto-heal del corrupto, cadenas estables."""
    import json
    import datetime
    import cost_guard

    hb_dir = os.path.join(_TMP, "hb")
    os.makedirs(hb_dir, exist_ok=True)
    hc.HB_DIR = hb_dir

    def saldo(hay):
        cost_guard.check_before_job = lambda *a, **k: (hay, "test", 0.0)

    def clear():
        for f in os.listdir(hb_dir):
            os.remove(os.path.join(hb_dir, f))
        hb_iso("centinela-ned", "ok", 0.1)  # 3er daemon (cadencia 0.5h) que estos casos no ejercitan → fresco/sano
        hb_iso("bot-telegram", "ok", 0.01)  # 4º daemon (cadencia 4min) idem → fresco/sano por defecto

    def hb_iso(agente, est, edad_h=1.0):   # formato run_agent (ISO-Z, UTC)
        ts = (datetime.datetime.utcnow() - datetime.timedelta(hours=edad_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
        json.dump({"agente": agente, "ts": ts, "estado": est, "modelo": "sonnet"},
                  open(os.path.join(hb_dir, agente + ".json"), "w"))

    def hb_local(agente, est, edad_h=1.0):  # formato calendar_sync (local 'YYYY-MM-DD HH:MM')
        ts = (datetime.datetime.now() - datetime.timedelta(hours=edad_h)).strftime("%Y-%m-%d %H:%M")
        json.dump({"estado": est, "citas": 4, "ts": ts},
                  open(os.path.join(hb_dir, agente + ".json"), "w"))

    def txt(a):
        """_salud_daemons devuelve (clave, texto); extrae el texto para buscar substrings."""
        return a[1] if isinstance(a, tuple) else a

    # 1. con saldo, todo sano y fresco (y calendar en su formato local) → sin alertas
    saldo(True); clear(); hb_iso("asistente", "ok", 1); hb_local("calendar-sync", "ok", 1)
    al, _ = hc._salud_daemons()
    ok(al == [], "todo sano con saldo → sin alertas (parsea formato local del calendario)")

    # 2. tope LOCAL alcanzado (cost_guard False) → aviso de tope, no de crédito; y NO duplica el fallo
    saldo(False); clear(); hb_iso("asistente", "fallo", 1); hb_iso("calendar-sync", "ok", 1)
    al, _ = hc._salud_daemons()
    ok(any("tope de gasto" in txt(a) for a in al), "tope local alcanzado → aviso de tope (no de crédito)")
    ok(sum("falló" in txt(a) for a in al) == 0, "tope local NO duplica el fallo del daemon (lo explica el aviso)")

    # 3. con saldo, asistente en fallo reciente → alerta de fallo, y ESTABLE entre pasadas (anti-flapping)
    saldo(True); clear(); hb_iso("asistente", "fallo", 1); hb_iso("calendar-sync", "ok", 1)
    al1, _ = hc._salud_daemons(); al2, _ = hc._salud_daemons()
    ok(any("barrido diario de Vega" in txt(a) and "falló" in txt(a) for a in al1), "fallo con saldo → alerta de fallo")
    ok(al1 == al2, "cadenas de alerta estables entre pasadas (no rompe el anti-flapping)")

    # 4. con saldo, calendar viejísimo (> cadencia) → 'lleva demasiado tiempo sin correr'
    saldo(True); clear(); hb_iso("asistente", "ok", 1); hb_iso("calendar-sync", "ok", 100)
    al, _ = hc._salud_daemons()
    ok(any("calendario" in txt(a) and "sin correr" in txt(a) for a in al), "daemon atrasado → alerta de stale")

    # 5. heartbeat corrupto → auto-heal: se aparta a .corrupto y avisa del reset
    saldo(True); clear(); hb_iso("asistente", "ok", 1)
    open(os.path.join(hb_dir, "calendar-sync.json"), "w").write("{ esto no es json")
    al, _ = hc._salud_daemons()
    ok(not os.path.exists(os.path.join(hb_dir, "calendar-sync.json")), "heartbeat corrupto se aparta")
    ok(any(f.startswith("calendar-sync.json.corrupto-") for f in os.listdir(hb_dir)), "queda copia .corrupto")
    ok(any("ilegible" in txt(a) for a in al), "avisa del reset del estado corrupto")

    # 6. estado frugal 'gate_sin_novedad' (la centralita saltó por no haber novedad) → NO es alerta
    saldo(True); clear(); hb_iso("asistente", "gate_sin_novedad", 1); hb_iso("calendar-sync", "ok", 1)
    al, _ = hc._salud_daemons()
    ok(al == [], "gate_sin_novedad (salto frugal, no gasta) NO se reporta como problema")

    # 7. estado 'critico_bloqueado' con saldo → alerta fuerte específica
    saldo(True); clear(); hb_iso("asistente", "critico_bloqueado", 1); hb_iso("calendar-sync", "ok", 1)
    al, _ = hc._salud_daemons()
    ok(any("bloqueado" in txt(a) for a in al), "critico_bloqueado → alerta específica de bloqueo")

    # 8. CRÉDITO de la cuenta agotado: el heartbeat del daemon trae estado 'credito_agotado' (lo escribe
    #    run_agent cuando la API dio 'Credit balance is too low'), con tope local OK → aviso ESPECÍFICO de
    #    crédito. Caso real del 25/6: cost_guard ve margen, pero la cuenta Anthropic no tiene crédito.
    #    Señal FRESCA del heartbeat (no el .out viejo, que era el bug que gritaba 'sin crédito' de más).
    saldo(True); clear(); hb_iso("asistente", "credito_agotado", 1); hb_iso("calendar-sync", "ok", 1)
    al, info = hc._salud_daemons()
    ok(any("CRÉDITO de la cuenta" in txt(a) and "Recárgalo" in txt(a) for a in al), "crédito de cuenta agotado (heartbeat) → aviso específico")
    ok(sum("falló" in txt(a) for a in al) == 0, "crédito agotado NO duplica el 'falló' genérico del daemon")
    ok(info.get("credito_cuenta_ok") is False, "info marca credito_cuenta_ok=False (para el Observatorio)")

    # 8b. 'aplazado_tope_local' = TOPE LOCAL, NO crédito de cuenta → NO debe gritar 'crédito agotado'
    #     (esto era justo el bug: confundir mi tope del día con la cuenta sin saldo).
    saldo(False); clear(); hb_iso("asistente", "aplazado_tope_local", 1); hb_iso("calendar-sync", "ok", 1)
    al, info = hc._salud_daemons()
    ok(info.get("credito_cuenta_ok") is True, "aplazado_tope_local NO se confunde con crédito de cuenta")
    ok(any("tope de gasto" in txt(a) for a in al) and not any("CRÉDITO de la cuenta" in txt(a) for a in al),
       "tope local → aviso de tope, NO de crédito")

    clear()  # deja el sandbox limpio para no contaminar otros asserts


def rutinas_ned_tests():
    """WATCHDOG de rutinas NED (C, 2/7/26): auto-mejora/git/comite-medico ≥2 días clavadas en un
    estado NO sano → alerta; recientes o sanas → silencio; comite-medico (mensual) con heartbeat
    VIEJO pero estado 'ok' → silencio (no exige frescura, a diferencia de _salud_daemons)."""
    import json
    import datetime

    hb_dir = os.path.join(_TMP, "hb_ned")
    os.makedirs(hb_dir, exist_ok=True)
    hc.HB_DIR = hb_dir

    def clear():
        for f in os.listdir(hb_dir):
            os.remove(os.path.join(hb_dir, f))

    def hb(agente, est, dias_atras):
        ts = (datetime.datetime.utcnow() - datetime.timedelta(days=dias_atras)).strftime("%Y-%m-%dT%H:%M:%SZ")
        json.dump({"agente": agente, "ts": ts, "estado": est, "modelo": "sonnet"},
                  open(os.path.join(hb_dir, agente + ".json"), "w"))

    def claves():
        return [a[0] for a in hc._check_rutinas_ned()[0]]

    # 1. sin heartbeat aún (nunca corrió) → sin baseline, no se alarma
    clear()
    ok(claves() == [], "sin heartbeat → silencio (sin baseline, igual que el dead-man)")

    # 2. todas sanas y recientes → silencio
    clear(); hb("auto-mejora", "ok", 0.2); hb("git", "ok", 0.5); hb("comite-medico", "ok", 1)
    ok(claves() == [], "las 3 rutinas sanas → silencio")

    # 3. auto-mejora en 'fallo' pero RECIENTE (<2 días) → todavía no alarma (puede autocorregirse mañana)
    clear(); hb("auto-mejora", "fallo", 0.5); hb("git", "ok", 0.2); hb("comite-medico", "ok", 1)
    ok(claves() == [], "fallo reciente (<2 días) → silencio, aún dentro del margen")

    # 4. auto-mejora en 'fallo' y YA lleva ≥2 días clavada → alerta específica
    clear(); hb("auto-mejora", "fallo", 2.5); hb("git", "ok", 0.2); hb("comite-medico", "ok", 1)
    al = hc._check_rutinas_ned()[0]
    ok("rutina_ned_auto-mejora_atascada" in [a[0] for a in al], "fallo ≥2 días → alerta")
    ok(any("auto-mejora" in a[1] and "fallo" in a[1] for a in al), "el texto nombra la rutina y el estado")

    # 5. git en 'critico_bloqueado' ≥2 días → alerta
    clear(); hb("auto-mejora", "ok", 0.1); hb("git", "critico_bloqueado", 3); hb("comite-medico", "ok", 1)
    ok("rutina_ned_git_atascada" in claves(), "git critico_bloqueado ≥2 días → alerta")

    # 6. comite-medico respondido solo por 'centralita' ≥2 días → alerta (aquí SÍ cuenta como malo,
    #    a diferencia del watchdog de Vega donde centralita es un salto frugal sano)
    clear(); hb("auto-mejora", "ok", 0.1); hb("git", "ok", 0.1); hb("comite-medico", "centralita", 4)
    ok("rutina_ned_comite-medico_atascada" in claves(), "comite-medico solo por centralita ≥2 días → alerta")

    # 7. comite-medico (rutina MENSUAL) con heartbeat 'ok' de hace 20 días → SIGUE sano, no exige
    #    frescura (a diferencia de _salud_daemons/VEGA_DAEMONS)
    clear(); hb("auto-mejora", "ok", 0.1); hb("git", "ok", 0.1); hb("comite-medico", "ok", 20)
    ok(claves() == [], "comite-medico ok de hace 20 días → silencio (mensual, no exige frescura)")

    # 8. heartbeat corrupto → auto-heal (se aparta, se avisa del reset, categoría luego se filtra a operativo)
    clear(); hb("auto-mejora", "ok", 0.1); hb("git", "ok", 0.1)
    open(os.path.join(hb_dir, "comite-medico.json"), "w").write("{ esto no es json")
    al = hc._check_rutinas_ned()[0]
    ok(not os.path.exists(os.path.join(hb_dir, "comite-medico.json")), "heartbeat corrupto se aparta")
    ok(any(f.startswith("comite-medico.json.corrupto-") for f in os.listdir(hb_dir)), "queda copia .corrupto")
    ok(any(a[0] == "rutina_ned_comite-medico_corrupto" for a in al), "avisa del reset del estado corrupto")

    clear()  # deja el sandbox limpio


def bot_telegram_autofix_tests():
    """Incidente 2/7/26: el bot-telegram estaba VIVO (launchctl status 0) pero su heartbeat se quedó
    rancio (long-poll atascado en bucle de resets de red). healthcheck debe distinguir "vivo pero
    degradado" de "nunca corrió" y solo intentar el autofix (kickstart×1) en el primer caso; si el
    kickstart revive el heartbeat → traza operativa, sin alertar; si no revive → alerta en llano.
    Aísla _kickstart_daemon (sin launchctl real) y pone KICKSTART_WAIT=0 para no dormir."""
    import json
    import datetime
    import cost_guard

    hb_dir = os.path.join(_TMP, "hb2")
    os.makedirs(hb_dir, exist_ok=True)
    hc.HB_DIR = hb_dir
    hc.KICKSTART_WAIT = 0
    cost_guard.check_before_job = lambda *a, **k: (True, "test con saldo", 0.0)

    def hb_iso(agente, est, edad_h):
        ts = (datetime.datetime.utcnow() - datetime.timedelta(hours=edad_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
        json.dump({"agente": agente, "ts": ts, "estado": est},
                  open(os.path.join(hb_dir, agente + ".json"), "w"))

    def clear():
        for f in os.listdir(hb_dir):
            os.remove(os.path.join(hb_dir, f))
        hb_iso("asistente", "ok", 1); hb_iso("calendar-sync", "ok", 1); hb_iso("centinela-ned", "ok", 0.1)

    def txt(a):
        return a[1] if isinstance(a, tuple) else a

    orig_kick = hc._kickstart_daemon

    # 1. heartbeat FRESCO (< 4 min) → sin autofix, sin alerta (caso normal)
    clear(); hb_iso("bot-telegram", "ok", 0.01)
    hc._kickstart_daemon = lambda label: (_ for _ in ()).throw(AssertionError("no debería kickstartear"))
    al, info = hc._salud_daemons()
    ok(not any("bot-telegram" in txt(a) or "bot de Telegram" in txt(a) for a in al),
       "heartbeat fresco de bot-telegram → sin alerta, sin tocar kickstart")

    # 2. heartbeat RANCIO (bucle de red) → kickstart revive el heartbeat → autofix silencioso
    clear(); hb_iso("bot-telegram", "ok", 1.0)   # 1h > 4min de cadencia → rancio

    def revive(label):
        ok(label == "com.btp.bot-telegram", "kickstart apunta al Label correcto")
        hb_iso("bot-telegram", "ok", 0.001)      # simula que el proceso relanzado ya volvió a latir
        return True
    hc._kickstart_daemon = revive
    al, info = hc._salud_daemons()
    claves = [a[0] for a in al]
    ok("daemon_bot-telegram_autofix" in claves, "heartbeat rancio + kickstart revive → autofix registrado")
    ok(not any(c.endswith("_parado") or c.endswith("_sin_senal") for c in claves if "bot-telegram" in c),
       "tras el autofix NO se duplica con una alerta de 'parado'")
    ok(info.get("bot-telegram", {}).get("autofix") is True, "info marca autofix=True")

    # 3. heartbeat RANCIO y el kickstart NO lo revive → alerta en llano (nunca silencio)
    clear(); hb_iso("bot-telegram", "ok", 1.0)
    hc._kickstart_daemon = lambda label: False    # kickstart falla (o revive pero el poll sigue sin hablar)
    al, info = hc._salud_daemons()
    claves = [a[0] for a in al]
    ok(any(c.startswith("daemon_bot-telegram_") and not c.endswith("_autofix") for c in claves),
       "kickstart no revive el heartbeat → SÍ alerta (nunca se traga el problema)")
    ok(info.get("bot-telegram", {}).get("autofix") is False, "info marca autofix=False")

    # 4. NUNCA hubo heartbeat (recién desplegado) → NO se fuerza kickstart (edad_h is None), solo
    #    la alerta genérica de "sin señal" (igual que los demás VEGA_DAEMONS sin latido previo).
    clear()   # sin fichero bot-telegram.json
    hc._kickstart_daemon = lambda label: (_ for _ in ()).throw(AssertionError("no debería kickstartear"))
    al, info = hc._salud_daemons()
    claves = [a[0] for a in al]
    ok("daemon_bot-telegram_sin_senal" in claves, "nunca corrió → alerta de sin señal, sin forzar kickstart")

    # 5. MÁXIMO 1 kickstart por ciclo: aunque el mock se llame más de una vez en el ciclo entero,
    #    _autofix_daemon_por_heartbeat solo se invoca UNA vez por pasada de _salud_daemons (no hay bucle).
    clear(); hb_iso("bot-telegram", "ok", 1.0)
    llamadas = []
    def contar(label):
        llamadas.append(label)
        hb_iso("bot-telegram", "ok", 0.001)
        return True
    hc._kickstart_daemon = contar
    hc._salud_daemons()
    ok(len(llamadas) == 1, "como mucho 1 kickstart por ciclo (sin bucle de reinicios): %d" % len(llamadas))

    # 6. El latido llega TARDE, como en la vida real (13-sep-26). El proceso relanzado no late hasta
    #    que vuelve su primer long-poll. Los mocks de arriba reviven el latido al instante, y eso tapó
    #    durante semanas que con la espera fija de 8 s el autofix no salía bien nunca (0 de 1.370).
    import re
    import threading
    fuente_bot = open(os.path.join(ROOT, "tools", "bot_telegram.py")).read()
    m = re.search(r"poll_updates\(timeout=(\d+)", fuente_bot)
    ok(m is not None, "encuentro el timeout del long-poll en bot_telegram.py")
    if m:
        ok(hc.LATIDO_TRAS_KICKSTART_MAX_S > int(m.group(1)),
           "el tope de espera tras kickstart (%ss) cubre el long-poll del bot (%ss)"
           % (hc.LATIDO_TRAS_KICKSTART_MAX_S, m.group(1)))
    max_real, paso_real = hc.LATIDO_TRAS_KICKSTART_MAX_S, hc.LATIDO_TRAS_KICKSTART_PASO_S
    hc.LATIDO_TRAS_KICKSTART_MAX_S, hc.LATIDO_TRAS_KICKSTART_PASO_S = 3, 0.05
    clear(); hb_iso("bot-telegram", "ok", 1.0)

    def revive_tarde(label):
        threading.Timer(0.4, lambda: hb_iso("bot-telegram", "ok", 0.0)).start()
        return True
    hc._kickstart_daemon = revive_tarde
    al, info = hc._salud_daemons()
    claves = [a[0] for a in al]
    ok("daemon_bot-telegram_autofix" in claves, "latido que llega tarde tras el kickstart → autofix")
    ok("daemon_bot-telegram_parado" not in claves, "latido que llega tarde → NO se cuenta como parado")

    # 7. kickstart OK pero el latido no llega antes del tope → alerta en llano (el sondeo no lo traga)
    hc.LATIDO_TRAS_KICKSTART_MAX_S = 0.3
    clear(); hb_iso("bot-telegram", "ok", 1.0)
    hc._kickstart_daemon = lambda label: True
    al, info = hc._salud_daemons()
    ok("daemon_bot-telegram_parado" in [a[0] for a in al], "sin latido antes del tope → SÍ alerta de parado")
    hc.LATIDO_TRAS_KICKSTART_MAX_S, hc.LATIDO_TRAS_KICKSTART_PASO_S = max_real, paso_real

    hc._kickstart_daemon = orig_kick
    clear()


def gateway_tests():
    """Cable de la PUERTA (gateway del borde): puerto vivo → sin alerta; caído pero revive con
    kickstart → sin alerta; caído y NO revive → alerta. Aísla _puerto_vivo/_kickstart_daemon
    (sin red ni launchctl) y pone KICKSTART_WAIT=0 para no dormir."""
    hc.KICKSTART_WAIT = 0
    orig_puerto, orig_kick = hc._puerto_vivo, hc._kickstart_daemon

    hc._puerto_vivo = lambda *a, **k: True
    al, _ = hc._check_gateway_vivo()
    ok(al == [], "gateway vivo → sin alerta")

    estados = iter([False, True])              # caído al sondear, vivo tras el kickstart
    hc._puerto_vivo = lambda *a, **k: next(estados)
    hc._kickstart_daemon = lambda label: True
    al, _ = hc._check_gateway_vivo()
    ok(al == [], "gateway caído pero revive con kickstart → sin alerta")

    hc._puerto_vivo = lambda *a, **k: False    # caído y sigue caído tras el kickstart
    hc._kickstart_daemon = lambda label: True
    al, _ = hc._check_gateway_vivo()
    ok(len(al) == 1 and "puerta de Polaris" in al[0], "gateway caído y no revive → alerta en llano")

    hc._puerto_vivo, hc._kickstart_daemon = orig_puerto, orig_kick


def red_sin_dns_tests():
    """Caída de RED ≠ bot parado (noche 11→12-sep-2026: 21 pasadas de `daemon_bot-telegram_parado`
    con el bot sano y 11.645 gaierror de DNS en su .err). Aislado: getaddrinfo simulado, kickstart
    simulado, latidos en tmp. Nada toca la red ni launchctl."""
    import datetime
    import json
    import socket
    import time as _t
    import cost_guard

    hb_dir = os.path.join(_TMP, "hb_dns")
    os.makedirs(hb_dir, exist_ok=True)
    hc.HB_DIR = hb_dir
    cost_guard.check_before_job = lambda *a, **k: (True, "test", 0.0)

    def hb_iso(agente, est, edad_h):
        ts = (datetime.datetime.utcnow() - datetime.timedelta(hours=edad_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
        json.dump({"agente": agente, "ts": ts, "estado": est},
                  open(os.path.join(hb_dir, agente + ".json"), "w"))

    def escena(edad_bot_h):
        for f in os.listdir(hb_dir):
            os.remove(os.path.join(hb_dir, f))
        hb_iso("asistente", "ok", 1); hb_iso("calendar-sync", "ok", 1); hb_iso("centinela-ned", "ok", 0.1)
        hb_iso("bot-telegram", "ok", edad_bot_h)

    llamadas_dns = []

    def sin_dns(host, port, *a, **k):
        llamadas_dns.append(host)
        raise socket.gaierror(8, "nodename nor servname provided, or not known")

    def con_dns(host, port, *a, **k):
        llamadas_dns.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("149.154.167.220", 443))]

    def no_kickstart(label):
        raise AssertionError("sin red no se kickstartea")

    orig_gai, orig_kick, orig_dns = socket.getaddrinfo, hc._kickstart_daemon, hc._hay_dns
    hc._hay_dns = _HAY_DNS_REAL
    try:
        # 1. SIN DNS + latido rancio → avisa de la red, NO culpa al bot y NO gasta el kickstart
        escena(10.76); socket.getaddrinfo = sin_dns; hc._kickstart_daemon = no_kickstart
        del llamadas_dns[:]
        al, info = hc._salud_daemons()
        claves = [a[0] for a in al]
        ok("red_sin_dns" in claves, "sin DNS y latido rancio → alerta red_sin_dns")
        ok("daemon_bot-telegram_parado" not in claves, "sin DNS → NO se emite daemon_bot-telegram_parado")
        ok(not any(c.startswith("daemon_bot-telegram_") for c in claves),
           "sin DNS → ninguna clave del bot entra al libro de deuda")
        ok(info.get("bot-telegram", {}).get("sin_dns") is True, "info marca sin_dns=True")
        ok(llamadas_dns == ["api.telegram.org"], "una sola sonda DNS por pasada: %r" % llamadas_dns)
        ok(any("no tiene red" in a[1] for a in al if a[0] == "red_sin_dns"), "el texto dice en llano que es la red")

        # 2. CON DNS + latido rancio + kickstart que no lo revive → SÍ es el bot: daemon_bot-telegram_parado
        escena(10.76); socket.getaddrinfo = con_dns; hc._kickstart_daemon = lambda label: False
        al, info = hc._salud_daemons()
        claves = [a[0] for a in al]
        ok("daemon_bot-telegram_parado" in claves, "con DNS y latido rancio → SÍ alerta de parado")
        ok("red_sin_dns" not in claves, "con DNS → no se inventa una caída de red")

        # 3. Latido FRESCO → ni se sondea el DNS (una pasada normal no toca la red)
        escena(0.01); socket.getaddrinfo = sin_dns; hc._kickstart_daemon = no_kickstart
        del llamadas_dns[:]
        al, info = hc._salud_daemons()
        ok(llamadas_dns == [], "latido fresco → no se sondea el DNS")
        ok("red_sin_dns" not in [a[0] for a in al], "latido fresco → sin alerta de red aunque no haya DNS")

        # 4. Resolver COLGADO → False dentro del tope, sin bloquear el healthcheck
        socket.getaddrinfo = lambda *a, **k: _t.sleep(5)
        t0 = _t.time()
        r = hc._hay_dns(timeout=0.2)
        ok(r is False and _t.time() - t0 < 2, "resolver colgado → False en el tope (%.2fs)" % (_t.time() - t0))
    finally:
        socket.getaddrinfo, hc._kickstart_daemon, hc._hay_dns = orig_gai, orig_kick, orig_dns


def red_sin_dns_frescura_tests():
    """Frescura sin red ≠ agente caído (noche 11→12-sep-2026: `frescura_agente_fallo:correo-imap`
    entró al libro con el correo sano y ya iba por 2 remisiones). Aislado: getaddrinfo simulado."""
    import socket

    FALLO_IMAP = "⚠️ Falló la última ejecución de 'correo-imap' → conviene revisar qué pasó."
    PARADO_ASIS = "⚠️ 'asistente' lleva un buen rato y no da señales → puede estar parado."
    HOY = "⚠️ HOY.md está desactualizado (fecha declarada de hace 2 días)."
    llamadas = []

    def sin_dns(host, port, *a, **k):
        llamadas.append(host)
        raise socket.gaierror(8, "nodename nor servname provided, or not known")

    def con_dns(host, port, *a, **k):
        llamadas.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("149.154.167.220", 443))]

    orig_gai, orig_dns = socket.getaddrinfo, hc._hay_dns
    hc._hay_dns = _HAY_DNS_REAL
    try:
        # 1. SIN DNS → los avisos de agente se funden en UN red_sin_dns; HOY.md se mantiene
        socket.getaddrinfo = sin_dns; del llamadas[:]
        al = hc._alertas_frescura([FALLO_IMAP, PARADO_ASIS, HOY])
        claves = [a[0] for a in al]
        ok(claves.count("red_sin_dns") == 1, "sin DNS → un solo red_sin_dns: %r" % claves)
        ok(not any(c.startswith("frescura_agente_") for c in claves),
           "sin DNS → ni frescura_agente_fallo:correo-imap ni _parado entran al libro")
        ok("frescura_hoy_desactualizado" in claves, "sin DNS → el aviso de HOY.md NO se tapa")
        ok(len(llamadas) == 1, "una sola sonda DNS por lote de avisos: %d" % len(llamadas))

        # 2. CON DNS → las claves de siempre, sin inventar caída de red
        socket.getaddrinfo = con_dns
        claves = [a[0] for a in hc._alertas_frescura([FALLO_IMAP, PARADO_ASIS, HOY])]
        ok("frescura_agente_fallo:correo-imap" in claves, "con DNS → frescura_agente_fallo:correo-imap")
        ok("frescura_agente_parado:asistente" in claves, "con DNS → frescura_agente_parado:asistente")
        ok("red_sin_dns" not in claves, "con DNS → sin red_sin_dns")

        # 3. Sin avisos de agente → ni se sondea el DNS
        socket.getaddrinfo = sin_dns; del llamadas[:]
        claves = [a[0] for a in hc._alertas_frescura([HOY])]
        ok(llamadas == [] and claves == ["frescura_hoy_desactualizado"],
           "solo HOY.md → no se sondea el DNS y sale igual")

        # 4. Frescura y daemons en la misma pasada → red_sin_dns una sola vez, conservando el resto
        mezcla = [("red_sin_dns", "a"), ("disco_bajo", "b"), ("red_sin_dns", "c"), "suelta"]
        res = hc._una_red_sin_dns(mezcla)
        ok(res == [("red_sin_dns", "a"), ("disco_bajo", "b"), "suelta"],
           "red_sin_dns duplicada se queda en la primera: %r" % res)
    finally:
        socket.getaddrinfo, hc._hay_dns = orig_gai, orig_dns


def red_sin_dns_correo_roster_tests():
    """Monitor de correo y roster sin red ≠ correo o daemon rotos (noche 11→12-sep-2026:
    correo_smtp_inalcanzable, correo_imap_login:* y daemon_fallando:com.btp.correo con el correo sano).
    Aislado: getaddrinfo simulado, correo_imap/correo_smtp como stubs, launchctl y roster simulados."""
    import socket
    import types
    import cost_guard

    llamadas = []

    def sin_dns(host, port, *a, **k):
        llamadas.append(host)
        raise socket.gaierror(8, "nodename nor servname provided, or not known")

    def con_dns(host, port, *a, **k):
        llamadas.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.0.1", 443))]

    m1, m2 = "titular.mgp@gmail.com", "titular@gmail.com"
    state_p = os.path.join(hc.HC, "correo_salud.json")

    def correo(imap_login, smtp_alcanzable, rebotes=None):
        try:
            os.remove(state_p)                   # sin throttle: cada caso sondea
        except OSError:
            pass
        cimap = types.SimpleNamespace(
            login_ok=lambda user=None, secret=None: imap_login.get(user, (True, None)),
            rebotes_recientes=lambda user=None, secret=None, limit=40: (rebotes or {}).get(user, []))
        csmtp = types.SimpleNamespace(
            smtp_alcanzable=lambda timeout=8: smtp_alcanzable,
            login_ok=lambda account=None, timeout=8: (True, None))
        orig = (sys.modules.get("correo_imap"), sys.modules.get("correo_smtp"))
        sys.modules["correo_imap"], sys.modules["correo_smtp"] = cimap, csmtp
        try:
            al, info = hc._check_correo_salud()
        finally:
            for nombre, mod in zip(("correo_imap", "correo_smtp"), orig):
                if mod is not None:
                    sys.modules[nombre] = mod
                else:
                    sys.modules.pop(nombre, None)
        return [a[0] for a in al], info

    NO_RED = {m1: (False, "gaierror(8, 'nodename nor servname')"), m2: (False, "gaierror(8)")}
    orig_gai, orig_dns = socket.getaddrinfo, hc._hay_dns
    orig_roster = (hc._launchctl_estado, hc._daemons_keepalive, hc._daemons_roster, hc._parked,
                   hc._kickstart_daemon, cost_guard.check_before_job, hc._roster_meta, hc._obs_agente_de_label)
    hc._hay_dns = _HAY_DNS_REAL
    try:
        # --- MONITOR DE CORREO ---
        # 1. SIN DNS + SMTP e IMAP caídos → solo red_sin_dns, ninguna clave correo_* al libro
        socket.getaddrinfo = sin_dns; del llamadas[:]
        c, info = correo(NO_RED, (False, "gaierror(8)"))
        ok(c == ["red_sin_dns"], "correo sin DNS → solo red_sin_dns: %r" % c)
        ok(info.get("sin_dns") is True, "correo sin DNS → detalle marca sin_dns")
        ok(len(llamadas) == 1, "correo: una sola sonda DNS: %d" % len(llamadas))

        # 2. CON DNS + los mismos fallos → las claves de siempre (el fallo es de Gmail o de la clave)
        socket.getaddrinfo = con_dns
        c, info = correo(NO_RED, (False, "TimeoutError"))
        ok("correo_smtp_inalcanzable" in c and "correo_imap_login:titular_mgp" in c
           and "correo_imap_login:titular" in c, "correo con DNS → claves de siempre: %r" % c)
        ok("red_sin_dns" not in c and not info.get("sin_dns"), "correo con DNS → sin red_sin_dns")

        # 3. Correo sano con rebotes → ni se sondea el DNS y el rebote sigue saliendo
        socket.getaddrinfo = sin_dns; del llamadas[:]
        c, _ = correo({m1: (True, None), m2: (True, None)}, (True, None),
                      rebotes={m1: [{"asunto": "Undeliverable"}]})
        ok(llamadas == [] and c == ["correo_rebotes:titular_mgp"],
           "correo sano con rebote → sin sonda DNS y el rebote sale: %r / %r" % (c, llamadas))

        # --- ROSTER: daemon_fallando ---
        cost_guard.check_before_job = lambda *a, **k: (True, "test con saldo", 0.0)
        hc._parked = lambda: set()
        hc._daemons_keepalive = lambda: set()
        hc._daemons_roster = lambda: {"com.btp.correo"}
        hc._launchctl_estado = lambda: {"com.btp.correo": ("-", "1")}
        hc._roster_meta = lambda: {}
        hc._obs_agente_de_label = lambda: {}
        kicks = []

        # 4. SIN DNS + correo con exit≠0 → red_sin_dns, sin daemon_fallando y sin kickstart
        socket.getaddrinfo = sin_dns
        hc._kickstart_daemon = lambda label: kicks.append(label) or True
        al, info = hc._check_roster_daemons()
        claves = [a[0] for a in al]
        ok("red_sin_dns" in claves and "daemon_fallando:com.btp.correo" not in claves,
           "roster sin DNS → red_sin_dns y no daemon_fallando: %r" % claves)
        ok(kicks == [], "roster sin DNS → no se kickstartea: %r" % kicks)
        ok(info.get("fallando_sin_dns") == ["com.btp.correo"], "info guarda qué daemons se taparon por la red")

        # 5. CON DNS + mismo exit≠0 → daemon_fallando y kickstart × 1, como siempre
        socket.getaddrinfo = con_dns
        al, info = hc._check_roster_daemons()
        claves = [a[0] for a in al]
        ok("daemon_fallando:com.btp.correo" in claves and "red_sin_dns" not in claves,
           "roster con DNS → daemon_fallando como siempre: %r" % claves)
        ok(kicks == ["com.btp.correo"], "roster con DNS → kickstart × 1: %r" % kicks)

        # 6. Roster sin ningún fallando → ni se sondea el DNS
        hc._launchctl_estado = lambda: {"com.btp.correo": ("-", "0")}
        socket.getaddrinfo = sin_dns; del llamadas[:]
        al, _ = hc._check_roster_daemons()
        ok(llamadas == [] and not any(a[0] == "red_sin_dns" for a in al),
           "roster sin fallando → sin sonda DNS: %r" % llamadas)
    finally:
        socket.getaddrinfo, hc._hay_dns = orig_gai, orig_dns
        (hc._launchctl_estado, hc._daemons_keepalive, hc._daemons_roster, hc._parked,
         hc._kickstart_daemon, cost_guard.check_before_job, hc._roster_meta, hc._obs_agente_de_label) = orig_roster


def roster_tests():
    """B1 — vigía de roster, las 3 clases: KeepAlive caído (alerta si no revive), interval DESCARGADO
    (bootout → alerta, NO se auto-carga), y cargado-pero-FALLANDO (exit>0 con PID '-'). Respeta lo
    aparcado y no marca fallando a un daemon que está corriendo (PID vivo). Aísla launchctl + roster."""
    import cost_guard
    hc.KICKSTART_WAIT = 0
    orig = (hc._launchctl_estado, hc._daemons_keepalive, hc._daemons_roster, hc._parked,
            hc._kickstart_daemon, cost_guard.check_before_job, hc._roster_meta)
    cost_guard.check_before_job = lambda *a, **k: (True, "test con saldo", 0.0)   # hay saldo → no suprime fallando
    hc._parked = lambda: {"com.btp.instagram"}
    hc._daemons_keepalive = lambda: {"com.btp.ka", "com.btp.instagram"}
    hc._daemons_roster = lambda: {"com.btp.ka", "com.btp.iv", "com.btp.run", "com.btp.fail", "com.btp.instagram"}
    hc._kickstart_daemon = lambda label: False           # el KeepAlive caído NO revive

    # ka KeepAlive ausente (caído); iv interval ausente (bootout); run cargado ok; fail cargado exit 75.
    hc._launchctl_estado = lambda: {"com.btp.run": ("321", "0"), "com.btp.fail": ("-", "75")}
    al, _ = hc._check_roster_daemons()
    claves = [a[0] for a in al]
    ok("daemon_roster_caido:com.btp.ka" in claves, "KeepAlive caído y no revive → alerta humana")
    ok("daemon_descargado:com.btp.iv" in claves, "interval bootout → descargado (no se auto-carga: encender = gate)")
    ok("daemon_fallando:com.btp.fail" in claves, "cargado con exit>0 y sin PID → fallando")
    ok(not any("com.btp.run" in c for c in claves), "cargado y exit 0 → sin alerta")
    ok(not any("instagram" in c for c in claves), "aparcado (_parked) → nunca alerta aunque falte")

    # PID VIVO con status histórico ≠0 → NO fallando (está corriendo ahora mismo)
    hc._daemons_keepalive = lambda: set()
    hc._daemons_roster = lambda: {"com.btp.run", "com.btp.fail"}
    hc._launchctl_estado = lambda: {"com.btp.run": ("999", "-15"), "com.btp.fail": ("888", "1")}
    al, _ = hc._check_roster_daemons()
    ok(not any(a[0].startswith("daemon_fallando") for a in al),
       "con PID vivo no se marca fallando aunque el status sea ≠0 (está corriendo)")

    # A — "CARGADO PERO SIN TRABAJAR": interval cargado, exit 0, pero su log lleva > K×cadencia sin
    # tocarse → inactivo. Con log fresco, no. (Mock de _roster_meta + os.path.getmtime.)
    import os as _os, time as _t
    hc._daemons_keepalive = lambda: set()
    hc._daemons_roster = lambda: {"com.btp.iv2"}
    hc._launchctl_estado = lambda: {"com.btp.iv2": ("-", "0")}          # cargado, exit 0
    hc._roster_meta = lambda: {"com.btp.iv2": (300, "/tmp/btp_iv2_test.log")}   # cadencia 5 min
    orig_getmtime = _os.path.getmtime
    _os.path.getmtime = lambda p: (_t.time() - 3600) if p == "/tmp/btp_iv2_test.log" else orig_getmtime(p)
    al, info = hc._check_roster_daemons()           # log de hace 1h >> 3×300s = 15 min → inactivo
    ok("daemon_inactivo:com.btp.iv2" in [a[0] for a in al], "interval cargado con log viejo > K×cadencia → inactivo")

    # A-bis (fix 17-jul): log VIEJO pero HEARTBEAT FRESCO → NO inactivo. El latido manda sobre el
    # mtime del log: un daemon puede correr con ÉXITO sin escribir en stdout (seguridad-sweep: diario
    # 06:00 exit 0 pero .out sin tocar días → falso "77h sin señal"). getmtime sigue viejo aquí.
    _orig_hb = hc._leer_heartbeat
    hc._leer_heartbeat = lambda ag: (0.1, "ok", False) if ag == "iv2" else _orig_hb(ag)
    al, _ = hc._check_roster_daemons()              # log viejo PERO latido fresco → NO inactivo
    ok(not any(a[0].startswith("daemon_inactivo") for a in al),
       "log viejo pero heartbeat fresco → NO inactivo (el latido manda; fix seguridad-sweep)")
    hc._leer_heartbeat = _orig_hb

    _os.path.getmtime = lambda p: _t.time() if p == "/tmp/btp_iv2_test.log" else orig_getmtime(p)
    al, _ = hc._check_roster_daemons()              # log fresco → no inactivo
    ok(not any(a[0].startswith("daemon_inactivo") for a in al), "log fresco → no inactivo")
    # SUELO del umbral: daemon MUY frecuente (120s → 3×120 = 6 min) con un hueco de 12 min NO alerta
    # (bajo el suelo de 30 min): un hipo del entorno que se auto-recupera no debe avisar a {{TITULAR}}.
    # Fix 3/7 (correo-imap avisó por un hueco único de 12 min).
    hc._roster_meta = lambda: {"com.btp.iv2": (120, "/tmp/btp_iv2_test.log")}   # cadencia 2 min
    _os.path.getmtime = lambda p: (_t.time() - 12 * 60) if p == "/tmp/btp_iv2_test.log" else orig_getmtime(p)
    al, _ = hc._check_roster_daemons()              # 12 min < suelo 30 min → NO inactivo
    ok(not any(a[0].startswith("daemon_inactivo") for a in al),
       "daemon frecuente, hueco 12 min < suelo → NO alerta (anti-falsa-alarma)")
    _os.path.getmtime = lambda p: (_t.time() - 40 * 60) if p == "/tmp/btp_iv2_test.log" else orig_getmtime(p)
    al, _ = hc._check_roster_daemons()              # 40 min > suelo 30 min → SÍ inactivo (problema real)
    ok("daemon_inactivo:com.btp.iv2" in [a[0] for a in al],
       "daemon frecuente, hueco 40 min > suelo → SÍ alerta (stall real)")
    _os.path.getmtime = orig_getmtime

    # BLOQUEO POR DINERO (tope local) → un exit≠0 es 'aplazado por saldo', NO se marca fallando
    # (lo explica el aviso de saldo de _salud_daemons; no se duplica). El descargado SÍ sigue saliendo.
    cost_guard.check_before_job = lambda *a, **k: (False, "[tope_local] tope diario", 0.0)
    hc._daemons_roster = lambda: {"com.btp.fail", "com.btp.iv"}
    hc._daemons_keepalive = lambda: set()
    hc._launchctl_estado = lambda: {"com.btp.fail": ("-", "75")}
    al, info = hc._check_roster_daemons()
    ok(not any(a[0].startswith("daemon_fallando") for a in al), "bloqueo por dinero → NO marca fallando (no duplica el aviso de saldo)")
    ok(info.get("fallando_suprimido_por_saldo") is True, "info marca que se suprimió fallando por saldo")
    ok(any(a[0] == "daemon_descargado:com.btp.iv" for a in al), "el descargado SÍ alerta aunque no haya saldo (no es problema de dinero)")

    (hc._launchctl_estado, hc._daemons_keepalive, hc._daemons_roster, hc._parked,
     hc._kickstart_daemon, cost_guard.check_before_job, hc._roster_meta) = orig


def recover_tests():
    """B2 — auto-recover schema-desconocido con guarda ANTI-BUCLE: re-encola lo recuperable y, si un
    job rebota RECOVER_ESCALA veces seguidas, ESCALA a {{TITULAR}} (humano) en vez de re-encolar para
    siempre; y se auto-cura (purga el ledger) cuando el job sale de failed/."""
    import json
    import cola as q
    hc.RECOVER_LEDGER = os.path.join(hc.HC, "recover_ledger.json")
    failed = os.path.join(q.QUEUE, "failed")
    os.makedirs(failed, exist_ok=True)
    jobf = os.path.join(failed, "9-x-jobB2.json")

    def plantar():   # simula el rebote del dispatcher: el job vuelve a failed/ con schema-desconocido
        open(jobf, "w").write(json.dumps({"id": "jobB2", "ultimo_error": "schema-desconocido: campo X"}))

    try:
        os.remove(hc.RECOVER_LEDGER)
    except OSError:
        pass

    plantar(); al1, i1 = hc._auto_recover_schema()
    ok(i1.get("reencolados") == ["jobB2"] and "cola_recover_schema" in [a[0] for a in al1],
       "1er rebote → re-encola (operativo, barato)")
    plantar(); hc._auto_recover_schema()                       # 2º rebote
    plantar(); al3, i3 = hc._auto_recover_schema()             # 3º → escala
    ok(i3.get("persistentes") == ["jobB2"], "rebota RECOVER_ESCALA veces → marcado persistente")
    ok("cola_recover_atascado" in [a[0] for a in al3], "persistente → escala a {{TITULAR}} (humano)")
    ok(i3.get("reencolados") == [], "al escalar deja de re-encolar (ROMPE el bucle)")

    try:
        os.remove(jobf)                                        # el job se recupera de verdad (tras fusionar)
    except OSError:
        pass
    al4, i4 = hc._auto_recover_schema()
    ok(i4.get("candidatos") == [] and al4 == [], "sin candidatos → sin alertas")
    ok(not os.path.exists(hc.RECOVER_LEDGER), "ledger purgado tras curarse (no queda marcado)")


def presupuesto_tests():
    """C — burn-rate del presupuesto: holgado → silencio; mensual ≥80% → alerta; diario ≥90% → alerta;
    y deja la proyección en info. Mock de los accesores de cost_guard."""
    import cost_guard
    orig = (cost_guard._limits, cost_guard.today_spent, cost_guard.month_spent)
    _orig_saldo = cost_guard.saldo_prepago
    cost_guard.saldo_prepago = lambda: None      # por defecto: sin recarga → no contamina los casos de tope

    # La proyección de fin de mes solo se calcula con datos suficientes (hoy.day >= 3). Si el test
    # corre los días 1-2 del mes, datetime.now().day es < 3 y no habría proyección → falso fallo.
    # Fijamos el "hoy" a día 15 (determinista): probamos la lógica, no el calendario.
    import datetime as _dtmod
    _orig_dt = hc.datetime

    class _FakeDT:
        @staticmethod
        def now():
            return _dtmod.datetime.now().replace(day=15)
    hc.datetime = _FakeDT

    def setm(d, gd, m, gm):
        cost_guard._limits = lambda: (d, 1.5, m)
        cost_guard.today_spent = lambda: gd
        cost_guard.month_spent = lambda: gm

    setm(540, 13, 1500, 107)        # holgado (lo de hoy: $107/$1500, $13/$540)
    al, info = hc._check_presupuesto()
    ok([a[0] for a in al] == [], "presupuesto holgado → sin alertas (el caso real de hoy)")
    # La proyección solo se calcula desde el día ≥3 del mes (guarda intencional del 28/6: con 1-2
    # días de gasto la proyección es ruido). Este test corre cualquier día real → si toca ejecutarlo
    # el día 1 o 2, "proyeccion_fin_mes" NO estaría en info aunque el código sea correcto. Fijamos
    # hc.datetime.now() a un día 15 fijo para probar la lógica de forma determinista, no dependiente
    # de en qué fecha se lance el test (bug de test detectado 2-jul-2026, no bug de producción).
    import datetime as _dt

    class _FakeDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime(2026, 7, 15, 12, 0, 0)

    _orig_datetime = hc.datetime
    hc.datetime = _FakeDatetime
    try:
        al, info = hc._check_presupuesto()
        ok([a[0] for a in al] == [], "presupuesto holgado (día≥3) → sin alertas")
        ok("proyeccion_fin_mes" in info, "deja la proyección de fin de mes en info (día≥3)")
    finally:
        hc.datetime = _orig_datetime

    setm(540, 13, 1500, 1300)       # mensual al ~87%
    ok(any(a[0] == "presupuesto_mensual_alto" for a in hc._check_presupuesto()[0]),
       "mensual ≥80% → alerta (el hueco del 28/6)")

    setm(540, 520, 1500, 100)       # diario al ~96%
    ok(any(a[0] == "presupuesto_diario_alto" for a in hc._check_presupuesto()[0]),
       "diario ≥90% → alerta")

    # SALDO PREPAGO (rehecho 23/7 — AUTONOMÍA): el ledger es solo un ESTIMADOR que {{TITULAR}} tendría que
    # rellenar; la ALARMA se guía por la SONDA REAL de crédito (cost_guard.credito_ok), no por él.
    # Mockeamos sonda + resync para no tocar la API ni el ledger real.
    setm(540, 13, 1500, 107)        # tope local holgado
    _orig_cok, _orig_resync = cost_guard.credito_ok, cost_guard.resync_baseline_auto
    _resyncs = []
    cost_guard.resync_baseline_auto = lambda: _resyncs.append(1)
    # ledger cree que está bajo, PERO la API tiene crédito → resync y CERO alarma (recarga sin anotar)
    cost_guard.saldo_prepago = lambda: {"monto": 20, "gastado": 18, "restante": 2, "frac": 0.92, "fecha_recarga": "2026-07-17"}
    cost_guard.credito_ok = lambda *a, **k: True
    al_sp = [a[0] for a in hc._check_presupuesto()[0]]
    ok(not any(a.startswith("saldo_prepago") for a in al_sp) and _resyncs,
       "ledger bajo + API CON crédito → resync, sin alarma (autónomo)")
    # la API rechaza por falta de crédito (señal REAL) → aviso URGENTE
    cost_guard.credito_ok = lambda *a, **k: False
    ok(any(a[0] == "saldo_prepago_urgente" for a in hc._check_presupuesto()[0]),
       "API SIN crédito de verdad → aviso URGENTE")
    # indeterminado (no pude confirmar) + ledger bajo → aviso SUAVE, no urgente
    cost_guard.credito_ok = lambda *a, **k: None
    al_sp = [a[0] for a in hc._check_presupuesto()[0]]
    ok("saldo_prepago_aviso" in al_sp and "saldo_prepago_urgente" not in al_sp,
       "ledger bajo + sonda indeterminada → aviso suave (sin cifras caducas)")
    # ledger NO bajo (<75%) → NI se sonda (barato), silencio
    cost_guard.credito_ok = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debe sondar si frac<0.75"))
    cost_guard.saldo_prepago = lambda: {"monto": 20, "gastado": 8, "restante": 12, "frac": 0.40, "fecha_recarga": "2026-07-17"}
    ok(not any(a[0].startswith("saldo_prepago") for a in hc._check_presupuesto()[0]),
       "saldo <75% → silencio (ni sonda a la API)")
    cost_guard.saldo_prepago = lambda: None
    ok(not any(a[0].startswith("saldo_prepago") for a in hc._check_presupuesto()[0]),
       "sin recarga registrada → silencio (sin baseline no inventamos)")
    cost_guard.credito_ok, cost_guard.resync_baseline_auto = _orig_cok, _orig_resync

    cost_guard._limits, cost_guard.today_spent, cost_guard.month_spent = orig
    cost_guard.saldo_prepago = _orig_saldo
    hc.datetime = _orig_dt


def vigilantes_tests():
    """B (lado healthcheck) — watch-the-watcher: vigía fresco → silencio; viejo → alerta; sin baseline
    → silencio (igual que el dead-man)."""
    import json
    import datetime
    orig_state = hc.STATE
    hc.STATE = _TMP
    vdir = os.path.join(_TMP, "vigia")
    os.makedirs(vdir, exist_ok=True)
    lr = os.path.join(vdir, "last_run.json")

    def put(min_atras):
        ts = (datetime.datetime.utcnow() - datetime.timedelta(minutes=min_atras)).strftime("%Y-%m-%dT%H:%M:%SZ")
        json.dump({"ts": ts}, open(lr, "w"))

    try:
        os.remove(lr)
    except OSError:
        pass
    ok([a[0] for a in hc._check_vigilantes()[0]] == [], "vigía sin baseline → silencio (no falso positivo)")
    put(2)
    ok([a[0] for a in hc._check_vigilantes()[0]] == [], "vigía fresco → silencio")
    put(40)
    ok("vigia_parado" in [a[0] for a in hc._check_vigilantes()[0]], "vigía sin correr > umbral → alerta humana")
    hc.STATE = orig_state


def docker_expuesto_tests():
    """SEGURIDAD — Docker/Colima: apagado → silencio; contenedor a 0.0.0.0/:: → alerta; ligado a
    127.0.0.1 o 100.x (Tailscale) → silencio; solo-expuesto sin binding → silencio. Sin tocar Docker
    real: se monkeypatchean _docker_arriba y la salida cruda de `docker ps`."""
    orig_arriba = hc._docker_arriba

    class _R:                      # mini-stub del CompletedProcess de subprocess.run
        def __init__(self, out): self.returncode = 0; self.stdout = out

    def feed(ps_output):
        hc._docker_arriba = lambda: True
        hc.subprocess.run = lambda *a, **k: _R(ps_output)

    def claves():
        return [a[0] for a in hc._check_docker_expuesto()[0]]

    orig_run = hc.subprocess.run
    try:
        # Colima/Docker apagado → ni mira los puertos (no es un fallo).
        hc._docker_arriba = lambda: False
        ok(claves() == [], "docker apagado → silencio")

        # Contenedor publicando a 0.0.0.0 → alerta con clave estable por nombre.
        feed("web\t0.0.0.0:8080->80/tcp\n")
        ok("docker_expuesto:web" in claves(), "puerto a 0.0.0.0 → alerta")

        # Wildcard IPv6 (::) también es exposición.
        feed("v6\t[::]:9000->9000/tcp\n")
        ok("docker_expuesto:v6" in claves(), "puerto a :: (IPv6 wildcard) → alerta")

        # Atado a loopback o a la tailnet 100.x → seguro, sin alerta.
        feed("obs\t127.0.0.1:8787->8787/tcp\nrelay\t100.114.113.73:8788->8788/tcp\n")
        ok(claves() == [], "127.0.0.1 y 100.x (Tailscale) → silencio")

        # Puerto solo EXPUESTO (sin binding de host) → no es exposición de red.
        feed("db\t5432/tcp\n")
        ok(claves() == [], "puerto expuesto sin publicar → silencio")

        # Mezcla: un contenedor seguro + uno a 0.0.0.0 → solo alerta el malo, una vez.
        feed("safe\t127.0.0.1:1->1/tcp\nbad\t0.0.0.0:2->2/tcp, 0.0.0.0:3->3/tcp\n")
        c = claves()
        ok(c == ["docker_expuesto:bad"], "mezcla → solo el expuesto, 1 clave por contenedor: %s" % c)
    finally:
        hc._docker_arriba = orig_arriba
        hc.subprocess.run = orig_run


def correo_salud_tests():
    """MONITOR DE CORREO (2/7/26): SMTP/IMAP alcanzables, App Passwords válidas (login por
    cuenta) y rebotes recientes. Se sustituyen correo_imap/correo_smtp por stubs en sys.modules
    (mismo patrón que docker_expuesto_tests con subprocess) para no tocar red real. Throttle
    ~1h: se resetea el fichero de estado entre casos para forzar cada sonda."""
    import types
    import json as _json

    state_p = os.path.join(hc.HC, "correo_salud.json")

    def _reset():
        try:
            os.remove(state_p)
        except OSError:
            pass

    def _claves(imap_login, imap_rebotes, smtp_alcanzable, smtp_login):
        _reset()
        cimap_stub = types.SimpleNamespace(
            login_ok=lambda user=None, secret=None: imap_login.get(user, (True, None)),
            rebotes_recientes=lambda user=None, secret=None, limit=40: imap_rebotes.get(user, []),
        )
        csmtp_stub = types.SimpleNamespace(
            smtp_alcanzable=lambda timeout=8: smtp_alcanzable,
            login_ok=lambda account=None, timeout=8: smtp_login.get(account, (True, None)),
        )
        orig_cimap = sys.modules.get("correo_imap")
        orig_csmtp = sys.modules.get("correo_smtp")
        sys.modules["correo_imap"] = cimap_stub
        sys.modules["correo_smtp"] = csmtp_stub
        try:
            alertas, info = hc._check_correo_salud()
        finally:
            if orig_cimap is not None:
                sys.modules["correo_imap"] = orig_cimap
            if orig_csmtp is not None:
                sys.modules["correo_smtp"] = orig_csmtp
        return [a[0] for a in alertas], info

    m1, m2 = "titular.mgp@gmail.com", "titular@gmail.com"

    # 1. Todo sano → silencio total.
    c, info = _claves({m1: (True, None), m2: (True, None)}, {}, (True, None),
                      {m1: (True, None), m2: (True, None)})
    ok(c == [], "correo sano (SMTP+IMAP+login ok, sin rebotes) → silencio: %s" % c)
    ok(info.get("cuentas", {}).get(m1, {}).get("imap_ok") is True, "detalle guarda imap_ok por cuenta")

    # 2. SMTP inalcanzable → alerta, y NO intenta el login-test de SMTP (ya sabemos que no va).
    c, _ = _claves({m1: (True, None), m2: (True, None)}, {}, (False, "TimeoutError: x"),
                   {m1: (True, None), m2: (True, None)})
    ok("correo_smtp_inalcanzable" in c, "SMTP inalcanzable → alerta: %s" % c)

    # 3. App Password de IMAP revocada en UNA cuenta → alerta con clave estable por cuenta.
    c, _ = _claves({m1: (False, "falta btp-gmail-app-password"), m2: (True, None)}, {},
                   (True, None), {m1: (True, None), m2: (True, None)})
    ok("correo_imap_login:titular_mgp" in c, "IMAP sin credencial válida → alerta por cuenta: %s" % c)
    ok(not any("titular" in k for k in c), "la cuenta SANA no genera alerta (no contamina)")

    # 4. Rebotes recientes (mailer-daemon) → alerta con clave estable, no revienta el resto.
    c, info = _claves({m1: (True, None), m2: (True, None)},
                      {m1: [{"remitente_email": "mailer-daemon@googlemail.com", "asunto": "Undeliverable"}]},
                      (True, None), {m1: (True, None), m2: (True, None)})
    ok("correo_rebotes:titular_mgp" in c, "rebote detectado → alerta: %s" % c)
    ok(info["cuentas"][m1]["rebotes"] == 1, "detalle cuenta el nº de rebotes")

    # 5. Login SMTP (para enviar) falla aunque IMAP vaya bien → alerta separada (credenciales
    #    IMAP/SMTP no comparten bug necesariamente, aunque usen el mismo secreto normalmente).
    c, _ = _claves({m1: (True, None), m2: (True, None)}, {}, (True, None),
                   {m1: (False, "auth failed"), m2: (True, None)})
    ok("correo_smtp_login:titular_mgp" in c, "login SMTP roto → alerta: %s" % c)

    # 6. Throttle: segunda llamada inmediata (sin _reset) no vuelve a sondear (usa el caché).
    _reset()
    cimap_stub = types.SimpleNamespace(
        login_ok=lambda user=None, secret=None: (True, None),
        rebotes_recientes=lambda user=None, secret=None, limit=40: [],
    )
    csmtp_stub = types.SimpleNamespace(
        smtp_alcanzable=lambda timeout=8: (True, None),
        login_ok=lambda account=None, timeout=8: (True, None),
    )
    orig_cimap, orig_csmtp = sys.modules.get("correo_imap"), sys.modules.get("correo_smtp")
    sys.modules["correo_imap"], sys.modules["correo_smtp"] = cimap_stub, csmtp_stub
    try:
        hc._check_correo_salud()
        _, info2 = hc._check_correo_salud()
    finally:
        if orig_cimap is not None:
            sys.modules["correo_imap"] = orig_cimap
        if orig_csmtp is not None:
            sys.modules["correo_smtp"] = orig_csmtp
    ok(info2.get("throttled") is True, "2ª sonda inmediata → throttled (no martillea Gmail)")


def drift_rutas_tests():
    # item #2 - detector de plists INSTALADOS con rutas rotas (home inexistente -> exit 78 al disparar)
    # o XML ilegible. Aisla en un tmpdir de plists fabricados; no toca ~/Library/LaunchAgents real.
    import plistlib, tempfile, os as _os
    d = tempfile.mkdtemp(prefix="hc_drift_")
    orig_parked = hc._parked
    hc._parked = lambda: {"com.btp.aparcado"}

    def wplist(label, prog, wd=None, out=None, err=None):
        body = {"Label": label, "ProgramArguments": prog}
        if wd is not None: body["WorkingDirectory"] = wd
        if out is not None: body["StandardOutPath"] = out
        if err is not None: body["StandardErrorPath"] = err
        with open(_os.path.join(d, label + ".plist"), "wb") as fh:
            plistlib.dump(body, fh)

    ausente = "/Users/nadie_xyz_no_existe/claudecode"
    wplist("com.btp.sano", ["/bin/echo", "--run"], wd=d,
           out=_os.path.join(d, "a.log"), err=_os.path.join(d, "b.log"))
    wplist("com.btp.wdroto", ["/bin/echo"], wd=ausente)
    wplist("com.btp.progroto", [ausente + "/.venv/bin/python", ausente + "/tools/x.py", "--run"])
    wplist("com.btp.logok", ["/bin/echo"], wd=d, out=_os.path.join(d, "no_existe_aun.log"))
    wplist("com.btp.flags", ["/usr/bin/caffeinate", "-dimsu"], wd=d)
    wplist("com.btp.aparcado", ["/bin/echo"], wd=ausente)
    with open(_os.path.join(d, "com.btp.malo.plist"), "wb") as fh:
        fh.write(b'<?xml version="1.0"?>\n<plist version="1.0"><dict><key>Label</key><string>com.btp.malo</string>')

    al, rotas, ileg = hc._drift_rutas_rotas(d)
    claves = [a[0] for a in al]
    ok(not any("sano" in c for c in claves) and "com.btp.sano" not in rotas, "rutas existentes -> sin alerta")
    ok("drift_rutas_com.btp.wdroto" in claves, "WorkingDirectory inexistente -> drift_rutas")
    ok("drift_rutas_com.btp.progroto" in claves, "home ajeno en ProgramArguments -> drift_rutas")
    ok(not any("logok" in c for c in claves), "log FILE ausente pero DIR presente -> SIN alerta")
    ok(not any("flags" in c for c in claves), "ProgramArguments solo-flags -> sin falso positivo")
    ok(not any("aparcado" in c for c in claves), "aparcado (_parked) -> nunca alerta aunque falte la ruta")
    ok("drift_ilegible_com.btp.malo" in claves and "com.btp.malo" in ileg, "plist malformado -> drift_ilegible y sigue el loop")
    ok(len([c for c in claves if c.startswith("drift_rutas_")]) == 2, "exactamente 2 rutas_rotas: el malformado no rompio el loop")

    hc._parked = orig_parked

    al2, rotas2, ileg2 = hc._drift_rutas_rotas(_os.path.join(d, "no_hay_nada"))
    ok(al2 == [] and rotas2 == {} and ileg2 == [], "dir sin plists -> nada (no lee el repo)")

    # wiring: _check_drift_daemons incorpora las alertas del helper y llena info
    orig = (hc._launchctl_estado, hc._registro_labels, hc._labels_de_plists, hc._drift_rutas_rotas)
    hc._launchctl_estado = lambda: {}
    hc._registro_labels = lambda: set()
    hc._labels_de_plists = lambda dd: set()
    hc._drift_rutas_rotas = lambda dd: ([("drift_rutas_com.btp.z", "roto")], {"com.btp.z": ["wd"]}, ["com.btp.q"])
    al3, info3 = hc._check_drift_daemons()
    ok("drift_rutas_com.btp.z" in [a[0] for a in al3], "wiring: la alerta del helper llega a _check_drift_daemons")
    ok(info3.get("rutas_rotas") == {"com.btp.z": ["wd"]} and info3.get("ilegibles") == ["com.btp.q"], "wiring: info lleva rutas_rotas + ilegibles")
    (hc._launchctl_estado, hc._registro_labels, hc._labels_de_plists, hc._drift_rutas_rotas) = orig
    print("-- drift-rutas (item #2): casos ejecutados --")


def deuda_dedup_tests():
    """Regresión 11-sep-26 (triaje de deuda escalada): un ÚNICO incidente persistente NO puede
    inflar tools/deuda.py cientos de veces. Antes del fix, el bloque del libro de deuda dentro de
    `_emitir_si_cambia` iteraba sobre `claves` (el conjunto CRUDO de la pasada actual) y llamaba a
    `deuda.visto()` por cada clave presente, en CADA pasada de healthcheck (cada 30 min) mientras la
    condición siguiera activa. Un solo apagón largo —el código rojo del 6-ago-26, que paró el lazo
    27 días— escaló `dead_man_ausencia` a 1241x y varios `frescura_agente_parado:*` a 700+x: no eran
    700 detecciones distintas, era UN incidente contado una vez por ciclo de sondeo. Al cruzar en
    horas tanto el umbral de escalada (3x) como REMISIONES_MAX (3), el hallazgo quedaba
    'intermitente' PARA SIEMPRE (R4: ya no se puede volver a callar, solo cerrar con test) aunque la
    condición llevara días resuelta. El fix usa `nuevas` (claves - prev_claves, ya calculado para el
    dedup de Telegram) en su lugar: solo toca el libro cuando la condición NO estaba en el último
    conjunto REALMENTE avisado — que es justo la semántica de R2 ("alguien lo volvió a DETECTAR"),
    no "sigue pasando este ciclo"."""
    import deuda as _deuda

    # Aislado del contador global `_sent` (lo comprueba, por cuenta exacta, el bloque de _emitir_si_
    # cambia más abajo en main()): swap temporal del mock de salida, restaurado al salir.
    _orig_report = salida.report_to_titular
    salida.report_to_titular = lambda *a, **k: None

    clave = "zz_persistente_demo_dedup"
    A = [clave]

    ok(hc._emitir_si_cambia(A, categoria="operativo") is True, "deuda-dedup: 1er ciclo con el problema -> avisa")
    it = _deuda._cargar()[clave]
    ok(it["veces"] == 1 and it["estado"] == "abierto", "deuda-dedup: 1er ciclo abre el hallazgo en 1x")

    # el MISMO problema sigue activo varios ciclos MÁS seguidos (persistente, nunca se resuelve) ->
    # el libro NO debe escalar: es UN incidente, no repetidas detecciones nuevas.
    for _ in range(5):
        hc._emitir_si_cambia(A, categoria="operativo")
    it = _deuda._cargar()[clave]
    ok(it["veces"] == 1,
       "deuda-dedup: 5 ciclos MÁS del mismo problema persistente -> sigue en 1x (antes del fix: escalaba a 6x)")
    ok(it["estado"] == "abierto", "deuda-dedup: sigue 'abierto', no escala por repetirse DENTRO del mismo incidente")

    # el recordatorio (pasada la ventana de 12h) tampoco debe tocar el contador: sigue siendo el
    # MISMO incidente, solo se re-notifica a {{TITULAR}}.
    import json as _json
    import time as _time
    sp = os.path.join(hc.HC, "last_alert_state-operativo.json")
    st = _json.load(open(sp)); st["ts"] = _time.time() - (hc.ALERTA_COOLDOWN_H + 1) * 3600
    _json.dump(st, open(sp, "w"))
    ok(hc._emitir_si_cambia(A, categoria="operativo") is True, "deuda-dedup: recordatorio pasada la ventana -> SÍ avisa de nuevo")
    it = _deuda._cargar()[clave]
    ok(it["veces"] == 1, "deuda-dedup: el recordatorio tampoco escala el libro (sigue siendo el mismo incidente)")

    # si el problema de verdad se RESUELVE (2 ciclos seguidos ausente, confirma "cerradas") y luego
    # VUELVE, eso sí es una repetición real y debe contar como una detección más.
    hc._emitir_si_cambia([], categoria="operativo")   # 1er ciclo ausente: pendiente de confirmar
    hc._emitir_si_cambia([], categoria="operativo")   # 2º ciclo ausente: confirma -> deuda.remitir()
    it = _deuda._cargar()[clave]
    ok(it["estado"] == "remitido", "deuda-dedup: 2 ciclos seguidos sin el problema -> se remite (deja de gritar)")
    hc._emitir_si_cambia(A, categoria="operativo")    # reaparece: repetición GENUINA
    it = _deuda._cargar()[clave]
    ok(it["veces"] == 2, "deuda-dedup: tras resolverse y VOLVER, sí cuenta como 2ª detección real")
    ok(it["estado"] == "abierto", "deuda-dedup: al volver tras remitido, despierta (R4)")

    salida.report_to_titular = _orig_report
    print("-- deuda-dedup (regresión 11-sep, dead_man_ausencia/frescura_agente_parado): casos ejecutados --")


def main():
    import json
    import time as _time
    salud_tests()
    rutinas_ned_tests()
    bot_telegram_autofix_tests()
    gateway_tests()
    red_sin_dns_tests()
    red_sin_dns_frescura_tests()
    red_sin_dns_correo_roster_tests()
    roster_tests()
    recover_tests()
    presupuesto_tests()
    vigilantes_tests()
    docker_expuesto_tests()
    correo_salud_tests()
    drift_rutas_tests()
    deuda_dedup_tests()
    A = ["agente X caído (prueba)", "disco bajo (prueba)"]
    ok(hc._emitir_si_cambia(A) is True, "1ª alerta → avisa")
    ok(hc._emitir_si_cambia(A) is False, "misma alerta → NO repite (anti-spam)")
    ok(hc._emitir_si_cambia(["b", "a"]) is True, "alerta NUEVA (aparece un problema que no estaba) → avisa")
    # mismo conjunto, distinto ORDEN → NO repite
    ok(hc._emitir_si_cambia(["a", "b"]) is False, "mismo conjunto en otro orden → NO repite")

    # CIERRE ("✅ Resuelto", 3/7/26): igual que una alerta NUEVA, el cierre respeta el anti-flapping —
    # una desaparición de UN solo ciclo no se anuncia todavía (podría ser un parpadeo); se anuncia
    # solo si SIGUE ausente en la llamada siguiente (2 lecturas seguidas sin volver).
    ok(hc._emitir_si_cambia([]) is False, "desaparece (1er ciclo) → aún NO anuncia resuelto (posible parpadeo)")
    ok(hc._emitir_si_cambia(["a", "b"]) is False,
       "reaparece antes de confirmarse → fue un parpadeo, no se avisó de nada (ni resuelto ni nuevo)")

    # ANTI-FLAPPING (bug 24/6): un agente que OSCILA dentro/fuera del conjunto ya avisado NO re-spamea.
    # Referencia tras lo anterior = {a, b} (lo último realmente ENVIADO, en el paso 3 "b, a").
    ok(hc._emitir_si_cambia(["a"]) is False, "uno se cae del conjunto avisado → NO re-avisa (nada nuevo, y el cierre de 'b' aún no se confirma)")
    ok(hc._emitir_si_cambia(["a", "b"]) is False, "y VUELVE el que osciló → NO re-avisa (flapping callado, tampoco hubo 'resuelto')")
    # un problema GENUINAMENTE nuevo sí dispara, al momento
    ok(hc._emitir_si_cambia(["a", "b", "c"]) is True, "aparece un agente NUEVO (c) → avisa al momento")

    # RECORDATORIO: un problema persistente se re-avisa al pasar la ventana (ALERTA_COOLDOWN_H).
    sp = os.path.join(hc.HC, "last_alert_state-humano.json")
    st = json.load(open(sp)); st["ts"] = _time.time() - (hc.ALERTA_COOLDOWN_H + 1) * 3600
    json.dump(st, open(sp, "w"))
    ok(hc._emitir_si_cambia(["a", "b", "c"]) is True, "mismo problema pasada la ventana de 12h → recuerda")

    # CIERRE CONFIRMADO: dos ciclos SEGUIDOS sin "a" ni "c" (dejamos solo "b") → tras el 2º, se
    # anuncia "✅ Resuelto" para las que de verdad no volvieron (y, como "b" queda fuera de la
    # ventana de recordatorio tras el reset de arriba, ese mismo ciclo TAMBIÉN entrega su estado
    # normal — son dos mensajes reales y distintos: el cierre y lo que sigue activo).
    ok(hc._emitir_si_cambia(["b"]) is False, "primer ciclo sin a/c → aún pendiente de confirmar")
    n_antes = len(_sent)
    r_cierre = hc._emitir_si_cambia(["b"])
    ok(r_cierre is True, "segundo ciclo seguido sin a/c → confirma y avisa '✅ Resuelto'")
    ok(len(_sent) - n_antes == 2, "el cierre confirmado entrega 2 mensajes (el '✅ Resuelto' + el estado de 'b' que sigue activo)")
    ok(any(m.startswith("✅ Resuelto") for m in _sent), "el mensaje de cierre lleva el prefijo '✅ Resuelto'")

    # nº total de envíos reales: 1ª, nueva(b,a), agente-nuevo(c), recordatorio, resuelto + estado-b = 6
    ok(len(_sent) == 6, "exactamente 6 envíos reales (flapping y parpadeos de cierre NO cuentan): %d" % len(_sent))

    # GUARDIÁN de frescura: un fallo TRANSITORIO (import de seguimiento que coincide con git) NO
    # avisa a {{TITULAR}}; solo tras ≥ UMBRAL ciclos seguidos. El éxito resetea. Fix 3/7.
    import tempfile as _tmp
    hc.STATE = _tmp.mkdtemp()
    hc._FRESCURA_FALLOS = os.path.join(hc.STATE, "healthcheck", "frescura_fallos.json")
    n1 = hc._frescura_fallos_inc(); n2 = hc._frescura_fallos_inc()
    ok(n1 == 1 and n2 == 2 and n2 < hc.FRESCURA_FALLOS_UMBRAL, "1-2 fallos < umbral → aún NO avisa")
    n3 = hc._frescura_fallos_inc()
    ok(n3 >= hc.FRESCURA_FALLOS_UMBRAL, "al 3er fallo consecutivo → SÍ avisa (umbral %d)" % hc.FRESCURA_FALLOS_UMBRAL)
    hc._frescura_fallos_reset()
    ok(hc._frescura_fallos_inc() == 1, "un ciclo con éxito resetea el contador (vuelve a 1)")

    print("RESULTADO healthcheck (anti-spam + watchdog de Vega): %d OK, %d fallos" % (_pass, _fail))
    print("✅ HEALTHCHECK EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
