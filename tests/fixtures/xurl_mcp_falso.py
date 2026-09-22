#!/usr/bin/env python3
"""Hijo falso que imita `xurl mcp` para tests/test_x_mcp_puente.py. Nunca toca la red ni ~/.xurl.

Todo se controla por entorno. FALSO_DIR (obligatorio) guarda:
  · arranques       nº de veces que ha arrancado (el envoltorio relanza = nuevo arranque)
  · pid-<n>         pid del arranque n
  · recibido.jsonl  todo lo que llega por stdin, con su nº de arranque

FALSO_FALLA_TRAS=N      en el arranque 1, tras N tools/call correctas, las demás dan error
FALSO_FALLA_SIEMPRE=1   toda tools/call da error, en cualquier arranque
FALSO_FALLA_NOMBRES=a,b en el arranque 1, esas herramientas dan error
FALSO_CODIGO            código del error (por defecto -32001, el del puente real)
FALSO_MUERE_TRAS=N      en el arranque 1, muere al recibir la tools/call nº N+1
FALSO_RETARDO           JSON {herramienta: ms} de espera antes de responder
FALSO_IGNORA_EOF=1      ignora el EOF y SIGTERM (para probar la escalada a SIGKILL)
FALSO_CUELGA_INIT=1     desde el arranque 2 no responde al initialize
Herramientas especiales: emite_notif (2 notificaciones antes de responder) y emite_basura
(una línea que no es JSON antes de responder).
"""
import json
import os
import signal
import sys
import threading
import time

D = os.environ["FALSO_DIR"]
_cont = os.path.join(D, "arranques")
N = (int(open(_cont).read()) if os.path.exists(_cont) else 0) + 1
open(_cont, "w").write(str(N))
open(os.path.join(D, "pid-%d" % N), "w").write(str(os.getpid()))
LOG = os.path.join(D, "recibido.jsonl")

FALLA_TRAS = int(os.environ.get("FALSO_FALLA_TRAS", "-1"))
FALLA_SIEMPRE = os.environ.get("FALSO_FALLA_SIEMPRE") == "1"
FALLA_NOMBRES = set(filter(None, os.environ.get("FALSO_FALLA_NOMBRES", "").split(",")))
CODIGO = int(os.environ.get("FALSO_CODIGO", "-32001"))
MUERE_TRAS = int(os.environ.get("FALSO_MUERE_TRAS", "-1"))
RETARDO = json.loads(os.environ.get("FALSO_RETARDO", "{}"))
IGNORA_EOF = os.environ.get("FALSO_IGNORA_EOF") == "1"
CUELGA_INIT = os.environ.get("FALSO_CUELGA_INIT") == "1"

if IGNORA_EOF:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

_out = threading.Lock()
_log = threading.Lock()


def escribir(obj):
    raw = obj if isinstance(obj, bytes) else json.dumps(obj).encode("utf-8")
    with _out:
        sys.stdout.buffer.write(raw + b"\n")
        sys.stdout.buffer.flush()


def apuntar(entrada):
    entrada = dict(entrada)
    entrada["arranque"] = N
    with _log:
        with open(LOG, "a") as f:
            f.write(json.dumps(entrada) + "\n")


def responder(msg, falla):
    metodo, id_ = msg.get("method"), msg.get("id")
    if metodo == "tools/call":
        nombre = (msg.get("params") or {}).get("name")
        if nombre in RETARDO:
            time.sleep(RETARDO[nombre] / 1000.0)
        if falla:
            escribir({"jsonrpc": "2.0", "id": id_,
                      "error": {"code": CODIGO, "message": "xurl mcp: could not reach the MCP server (transport error)"}})
            return
        if nombre == "emite_notif":
            escribir({"jsonrpc": "2.0", "method": "notifications/message", "params": {"n": 1}})
            escribir({"jsonrpc": "2.0", "method": "notifications/message", "params": {"n": 2}})
        if nombre == "emite_basura":
            escribir(b"BASURA-NO-JSON")
        escribir({"jsonrpc": "2.0", "id": id_,
                  "result": {"content": [{"type": "text", "text": "%s arranque %d" % (nombre, N)}]}})


llamadas = 0
for linea in iter(sys.stdin.buffer.readline, b""):
    try:
        msg = json.loads(linea)
    except ValueError:
        apuntar({"crudo": linea.decode("utf-8", "replace").rstrip("\n")})
        continue
    apuntar({"msg": msg})
    if not isinstance(msg, dict) or "id" not in msg or "method" not in msg:
        continue
    metodo, id_ = msg["method"], msg["id"]
    if metodo == "initialize":
        if not (CUELGA_INIT and N >= 2):
            escribir({"jsonrpc": "2.0", "id": id_, "result": {
                "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                "serverInfo": {"name": "falso", "version": str(N)}}})
    elif metodo == "ping":
        escribir({"jsonrpc": "2.0", "id": id_, "result": {}})
    elif metodo == "tools/list":
        escribir({"jsonrpc": "2.0", "id": id_, "result": {"tools": [
            {"name": "get_users_me"}, {"name": "search_posts_all"}, {"name": "create_users_bookmark"}]}})
    elif metodo == "tools/call":
        llamadas += 1
        if N == 1 and MUERE_TRAS >= 0 and llamadas > MUERE_TRAS:
            os._exit(3)
        nombre = (msg.get("params") or {}).get("name")
        falla = FALLA_SIEMPRE or (N == 1 and ((FALLA_TRAS >= 0 and llamadas > FALLA_TRAS) or nombre in FALLA_NOMBRES))
        threading.Thread(target=responder, args=(msg, falla), daemon=True).start()
    else:
        escribir({"jsonrpc": "2.0", "id": id_, "error": {"code": -32601, "message": "método no soportado"}})

if IGNORA_EOF:
    while True:
        time.sleep(1)
time.sleep(0.1)
os._exit(0)
