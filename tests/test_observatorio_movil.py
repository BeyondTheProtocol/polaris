#!/usr/bin/env python3
"""test_observatorio_movil.py — el Observatorio llega al móvil por `tailscale serve` 9090, no por un relay.

25-sep-2026, punto S14 de la revisión externa. Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.
El relay Python 8788 (com.btp.observatorio-remoto) se retiró: hacía lo mismo que `tailscale serve`
9090 → 127.0.0.1:8787 y era un proceso nuestro más escuchando fuera de loopback.

Qué protege:
  · El enlace que se anuncia (tarjeta `urls`, parte de Telegram) es el de 9090 por NOMBRE: por IP,
    `tailscale serve` da 404.
  · El guard de escritura acepta el Origin del nombre de Tailscale (si no, apuntar un síntoma desde
    el portátil daría 403) y sigue rechazando webs ajenas y peticiones sin token.
  · El vigilante de salud mira 9090 y, si solo falla el camino móvil, avisa SIN reiniciar nada
    (ya no hay daemon nuestro en medio).
Sin red: `_http_ok` y `_kickstart_daemon` se sustituyen. No importa `salida` ni manda nada.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("BTP_TEST_BATTERY", "1")
os.environ.setdefault("BTP_STATE_DIR", tempfile.mkdtemp(prefix="obsmovil-"))

import healthcheck as hc   # noqa: E402
import observatorio as ob  # noqa: E402

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# ── el enlace ─────────────────────────────────────────────────────────────────────────────
check("puerto móvil = 9090", ob.MOVIL_PORT == 9090)
check("el enlace lleva el nombre, no la IP", ob.TS_HOST.endswith(".ts.net"))
urls = {"movil": "http://%s:%d" % (ob.TS_HOST, ob.MOVIL_PORT)}
check("ni rastro del 8788 en el enlace", "8788" not in urls["movil"])
check("la rutina del relay ya no se describe", "com.btp.observatorio-remoto" not in ob._RUTINA_HUMANO)


# ── el guard de escritura ─────────────────────────────────────────────────────────────────
class _FakeHeaders(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


def guard(origin, token=True):
    h = _FakeHeaders()
    if token:
        h["X-Tablero-Token"] = ob.TOKEN
    if origin:
        h["Origin"] = origin
    fake = type("F", (), {})()
    fake.headers = h
    return ob.Handler._post_guard(fake)


check("Origin http://polaris:9090 → acepta", guard("http://polaris:9090"))
check("Origin nombre largo .ts.net → acepta", guard("http://%s:9090" % ob.TS_HOST))
check("Origin loopback → acepta", guard("http://127.0.0.1:8787"))
check("Origin ajeno → rechaza", not guard("http://evil.example.com"))
check("Origin que solo EMPIEZA por polaris → rechaza", not guard("http://polaris.evil.com"))
check("sin token → rechaza", not guard("http://polaris:9090", token=False))


# ── el vigilante de salud ─────────────────────────────────────────────────────────────────
check("healthcheck mira 9090 por nombre", hc.OBS_MOVIL_URL == "http://%s:9090/" % ob.TS_HOST)
check("el relay ya no es un daemon a reiniciar", "observatorio-remoto" not in hc.KEEPALIVE_DAEMONS)
check("su ausencia no alerta (aparcado)", "com.btp.observatorio-remoto" in hc._PARKED_HC)

_orig_http, _orig_kick, _orig_sleep = hc._http_ok, hc._kickstart_daemon, hc.time.sleep
kicks = []
hc._kickstart_daemon = lambda label: kicks.append(label) or True
hc.time.sleep = lambda s: None
try:
    hc._http_ok = lambda url, timeout=None: (True, 200)
    alertas, _ = hc._check_servicios_vivos()
    check("todo responde → sin avisos", alertas == [] and kicks == [])

    hc._http_ok = lambda url, timeout=None: (url == hc.OBS_LOCAL_URL, 200 if url == hc.OBS_LOCAL_URL else "URLError")
    alertas, _ = hc._check_servicios_vivos()
    check("local sí, móvil no → un aviso", len(alertas) == 1 and "Tailscale" in alertas[0])
    check("local sí, móvil no → NO reinicia nada", kicks == [])

    kicks.clear()
    hc._http_ok = lambda url, timeout=None: (False, "URLError")
    alertas, _ = hc._check_servicios_vivos()
    check("local caído → reinicia el Observatorio", kicks == ["com.btp.observatorio"])
    check("local caído y sigue → avisa", len(alertas) == 1)
finally:
    hc._http_ok, hc._kickstart_daemon, hc.time.sleep = _orig_http, _orig_kick, _orig_sleep

print("%s observatorio_movil: %d ok, %d fallos" % ("✅" if _fail == 0 else "❌", _pass, _fail))
sys.exit(1 if _fail else 0)
