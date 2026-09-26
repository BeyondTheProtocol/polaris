#!/usr/bin/env python3
"""test_staging_loopback.py — la Antesala no escucha fuera de loopback.

26-sep-2026, continuación del punto S14. Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.
Hasta ese día tools/staging.py levantaba relays Python en la IP de Tailscale (3010/3011). Ahora todo
escucha en 127.0.0.1 y `tailscale serve` (9091 → 4000, 9094 → 4011) lo saca a la tailnet.

Qué protege:
  · Ningún servidor ni relay de staging.py se ata a una IP que no sea loopback (lectura del código:
    cualquier llamada a reusable_tcp_server/http_server con un host distinto de "127.0.0.1" → rojo).
  · Los enlaces que se enseñan son los de serve, por NOMBRE (por IP, serve da 404).
  · El relay de la app reenvía al puerto dinámico: socket real en 127.0.0.1, sin red externa.
No arranca el daemon ni toca launchd ni Tailscale.
"""
import os
import re
import socket
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("BTP_TEST_BATTERY", "1")

import staging as st  # noqa: E402

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# ── 1. nada se ata fuera de loopback ─────────────────────────────────────────────────────
src = open(os.path.join(ROOT, "tools", "staging.py"), encoding="utf-8").read()
binds = re.findall(r"(?:reusable_tcp_server|http_server)\(\s*(?:ThreadingHTTPServer,\s*)?([^,]+),", src)
check("hay binds que revisar (el regex sigue casando)", len(binds) >= 2)
for b in binds:
    check("bind en loopback: %s" % b.strip(), b.strip() == '"127.0.0.1"')
check("TS_IP ya no existe en staging.py", not hasattr(st, "TS_IP"))
check("ninguna IP de la tailnet en el código ejecutable",
      not re.search(r'^[^#\n]*"100\.\d+\.\d+\.\d+"', src, re.M))

# ── 2. los enlaces ───────────────────────────────────────────────────────────────────────
check("índice por serve 9091", st.INDEX_TS == 9091)
check("app por serve 9094", st.APP_TS == 9094)
check("relay de la app en 4011", st.APP_PROXY == 4011)
_tmp = tempfile.mkdtemp(prefix="staging-t-")
st.CURRENT = os.path.join(_tmp, "current.json")
st.STATE_DIR = _tmp
check("app_url por nombre y 9094", st._estado_payload()["app_url"] == "http://%s:9094/" % st.TS_HOST)
check("el nombre es el de la tailnet", st.TS_HOST.endswith(".ts.net"))

# ── 3. el relay de loopback reenvía al puerto dinámico ──────────────────────────────────
destino = socket.socket()
destino.bind(("127.0.0.1", 0))
destino.listen(1)
puerto_destino = destino.getsockname()[1]


def _eco():
    c, _ = destino.accept()
    c.sendall(c.recv(64).upper())
    c.close()


threading.Thread(target=_eco, daemon=True).start()
libre = socket.socket()
libre.bind(("127.0.0.1", 0))
puerto_relay = libre.getsockname()[1]
libre.close()
st._log = lambda m: None
threading.Thread(target=st._relay, args=(puerto_relay, lambda: puerto_destino), daemon=True).start()
resp = b""
for _ in range(50):
    try:
        with socket.create_connection(("127.0.0.1", puerto_relay), timeout=2) as s:
            s.sendall(b"hola")
            resp = s.recv(64)
        break
    except OSError:
        time.sleep(0.1)
check("el relay reenvía al destino dinámico", resp == b"HOLA")

print("%s staging_loopback: %d ok, %d fallos" % ("✅" if _fail == 0 else "❌", _pass, _fail))
sys.exit(1 if _fail else 0)
