#!/usr/bin/env python3
"""test_secretos_broker.py — el broker del Llavero sirve lo que debe, a quien debe, y nada más.

POR QUÉ EXISTE (17-oct-2026, deuda `llavero_bloqueado`, 7 detecciones). Por SSH el login.keychain
sale bloqueado (rc=36) y las sesiones le devolvían a {{TITULAR}} pasos manuales. El arreglo es
`tools/secretos_broker.py`, que vive en la sesión gráfica, y el cliente en `_secrets._keychain`.
Esto fija:
  A. La lista es CERRADA: lo de fuera se deniega; las RESERVADAS y `NUNCA` no entran jamás.
  B. Los nombres raros no llegan a argv (`-w`, `;`, mayúsculas, demasiado largos).
  C. El log nunca lleva el valor.
  D. El cliente cae a `security` si el broker no está, dice que no o se cuelga; y
     BTP_SECRETOS_BROKER=0 lo apaga del todo (lo exporta test_all.sh).
  E. El plist: Aqua, rutas de casa base, registrado en REGISTRO.json.
  F. El muro deniega nombrar el socket desde Bash (si alguien lo saca de `_secrets/`, rojo).

HERMÉTICO: broker sobre un socket temporal con un lector falso. Nunca toca el Llavero real ni el
broker real (BTP_STATE_DIR temporal en todo el fichero).
"""
import json
import os
import plistlib
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest import mock

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))
_TMP = tempfile.mkdtemp(prefix="sb-", dir="/tmp")         # corto: sun_path tiene 104 bytes
os.environ["BTP_STATE_DIR"] = _TMP
import _secrets  # noqa: E402
import secretos_broker as SB  # noqa: E402

CENTINELA = "VALOR-CENTINELA-%s" % os.urandom(6).hex()
fallos = []


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


def lector_falso(servicio):
    if servicio == "btp-umami-api":
        return CENTINELA, "ok"
    if servicio == "btp-hf-token":
        return None, "no-existe"
    return None, "ilegible"


def arrancar(ruta, lector=lector_falso):
    b = SB.Broker(ruta=ruta, lector=lector).abrir()
    threading.Thread(target=b.servir, daemon=True).start()
    return b


def crudo(ruta, datos):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(3)
    s.connect(ruta)
    s.sendall(datos)
    r = b""
    while b"\n" not in r:
        t = s.recv(4096)
        if not t:
            break
        r += t
    s.close()
    return json.loads(r.split(b"\n", 1)[0])


print("A · la lista es cerrada")
check(not (SB.PERMITIDAS & _secrets.RESERVADOS), "PERMITIDAS no toca las RESERVADAS")
check(not (SB.PERMITIDAS & SB.NUNCA), "PERMITIDAS no toca NUNCA")
check(_secrets.RESERVADOS <= SB.NUNCA, "las RESERVADAS están en NUNCA")
for svc in ("btp-restic-password", "btp-latido-externo", "btp-claude-oauth-token",
            "btp-x-mcp-client-secret", "btp-anthropic-api", "btp-telegram-token"):
    check(SB.decidir(svc) == "denegado", "%s → denegado" % svc)
for svc in ("btp-gmail-app-password-2", "btp-umami-api", "btp-gdrive-refresh-token"):
    check(SB.decidir(svc) is None, "%s → se sirve" % svc)
with mock.patch.object(SB, "PERMITIDAS", SB.PERMITIDAS | {"btp-firmante-ed25519"}):
    check(SB.decidir("btp-firmante-ed25519") == "denegado", "aunque alguien la añada, NUNCA gana")

print("B · nombres raros")
for raro in ("-w", "btp-x;rm", "BTP-UMAMI-API", "btp-" + "a" * 61, "", None, 7, "umami"):
    check(SB.decidir(raro) == "rechazado", "%r → rechazado" % (raro,))

print("C/D · por el socket, y el cliente")
ruta = _secrets.broker_socket()
check(ruta.startswith(_TMP) and "/_secrets/" in ruta, "el socket sigue a BTP_STATE_DIR y vive en _secrets/")
b = arrancar(ruta)
try:
    check(oct(os.stat(ruta).st_mode & 0o777) == "0o600", "socket 0600")
    check(oct(os.stat(os.path.dirname(ruta)).st_mode & 0o777) == "0o700", "carpeta 0700")
    r = crudo(ruta, b'{"v":1,"servicio":"btp-umami-api"}\n')
    check(r.get("estado") == "ok" and r.get("valor") == CENTINELA, "en la lista → ok con valor")
    check(crudo(ruta, b'{"v":1,"servicio":"btp-hf-token"}\n') == {"v": 1, "estado": "no-existe"}, "no-existe se dice")
    check(crudo(ruta, b'{"v":1,"servicio":"btp-gmail-app-password"}\n') == {"v": 1, "estado": "ilegible"}, "ilegible se dice")
    check(crudo(ruta, b'{"v":1,"servicio":"btp-ok-envio-mac"}\n') == {"v": 1, "estado": "denegado"}, "RESERVADA → denegado")
    check(crudo(ruta, b'{"v":1,"servicio":"btp-umami-api","x":1}\n').get("estado") == "rechazado", "campos de más → rechazado")
    check(crudo(ruta, b'{"v":2,"servicio":"btp-umami-api"}\n').get("estado") == "rechazado", "otra versión → rechazado")
    check(crudo(ruta, b"x" * 2000 + b"\n").get("estado") == "rechazado", "línea demasiado larga → rechazado")
    check(crudo(ruta, b'{"v":1,"ping":true}\n') == {"v": 1, "estado": "ok"}, "ping sin leer clave")

    os.environ["BTP_SECRETOS_BROKER"] = "1"
    with mock.patch.object(_secrets.subprocess, "run", side_effect=AssertionError("no debía llamar a security")):
        check(_secrets.get("btp-umami-api") == CENTINELA, "cliente: el valor llega por el broker sin tocar security")
    falso = mock.Mock(return_value=subprocess.CompletedProcess([], 0, stdout="del-security\n", stderr=""))
    with mock.patch.object(_secrets.subprocess, "run", falso):
        n_log = open(b.log, encoding="utf-8").read().count("\n")
        check(_secrets.get("btp-restic-password") == "del-security", "cliente: fuera de la lista → security directo")
        check(open(b.log, encoding="utf-8").read().count("\n") == n_log,
              "cliente: fuera de la lista ni pregunta al broker (sin ruido en el log)")
        check(_secrets.get("btp-gmail-app-password") == "del-security", "cliente: el broker dice ilegible → cae a security")
        check(_secrets._ULTIMO_BROKER.get("btp-gmail-app-password") == "ilegible", "cliente: recuerda el estado (sin valor)")
    os.environ["BTP_SECRETOS_BROKER"] = "0"
    with mock.patch.object(_secrets.subprocess, "run", falso):
        check(_secrets.get("btp-umami-api") == "del-security", "BTP_SECRETOS_BROKER=0: ni lo intenta")
    os.environ.pop("BTP_SECRETOS_BROKER")
    with mock.patch.object(_secrets.subprocess, "run", falso):
        check(_secrets.get("btp-umami-api") == "del-security",
              "un test lanzado a mano (sin la variable) tampoco habla con el broker")
    with mock.patch.object(sys, "argv", ["/x/tools/umami.py"]), \
            mock.patch.object(_secrets.subprocess, "run", side_effect=AssertionError("no debía llamar a security")):
        check(_secrets.get("btp-umami-api") == CENTINELA, "una tool normal (sin la variable) sí lo usa")
    os.environ["BTP_SECRETOS_BROKER"] = "1"
    check(_secrets.get("btp-ok-envio-mac") is None, "cliente: RESERVADA → None sin preguntar a nadie")

    with open(b.log, encoding="utf-8") as f:
        log = f.read()
    check(CENTINELA not in log and log.count("\n") >= 5, "C · el log tiene las peticiones y NUNCA el valor")
    check(oct(os.stat(b.log).st_mode & 0o777) == "0o600", "log 0600")
finally:
    b.cerrar()

with mock.patch.object(_secrets.subprocess, "run", falso):
    check(_secrets.get("btp-umami-api") == "del-security", "sin socket → cae a security")

# Un broker que acepta y no contesta: el cliente no se puede quedar colgado.
mudo = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
mudo.bind(ruta)
mudo.listen(4)
try:
    t0 = time.time()
    with mock.patch.object(_secrets.subprocess, "run", falso):
        v = _secrets.get("btp-umami-api")
    check(v == "del-security" and time.time() - t0 < 2.5, "broker colgado → cae a security en < 2,5 s")
finally:
    mudo.close()
    os.remove(ruta)

print("E · el plist y el registro")
with open(os.path.join(RAIZ, "tools", "launchd", "com.btp.secretos.plist"), "rb") as f:
    pl = plistlib.load(f)
check(pl.get("Label") == SB.LABEL, "Label com.btp.secretos")
check(pl.get("LimitLoadToSessionType") == "Aqua", "LimitLoadToSessionType = Aqua")
check(not any(".claude/worktrees" in str(v) for v in [pl.get("WorkingDirectory")] + pl.get("ProgramArguments", [])),
      "sin rutas de worktree")
check(pl.get("ProgramArguments", [])[1:] == ["/Users/polaris/claudecode/tools/secretos_broker.py", "servir"],
      "lanza secretos_broker.py servir desde casa base")
with open(os.path.join(RAIZ, "tools", "launchd", "REGISTRO.json"), encoding="utf-8") as f:
    reg = json.load(f)["daemons"].get(SB.LABEL) or {}
check(reg.get("concern") == "broker-secretos-llavero", "registrado con su concern")

print("F · el muro deniega hablarle al socket desde Bash")
real = os.path.join(os.path.expanduser("~/claudecode"), "tools", "state", "_secrets", "broker.sock")
hook = os.path.join(RAIZ, ".claude", "hooks", "muro_guard.py")
env = dict(os.environ, BTP_STATE_DIR=tempfile.mkdtemp(prefix="sb-guard-"))
env.pop("MURO_PROFILE", None)
for cmd in ("nc -U %s" % real,
            "python3 -c \"import socket;s=socket.socket(socket.AF_UNIX);s.connect('%s')\"" % real,
            "socat - UNIX-CONNECT:%s" % real):
    carga = dict(hook_event_name="PreToolUse", tool_name="Bash", tool_input=dict(command=cmd),
                 session_id="s", cwd=RAIZ, permission_mode="bypassPermissions")
    r = subprocess.run([sys.executable, hook], input=json.dumps(carga), capture_output=True,
                       text=True, timeout=60, env=env)
    check(r.returncode == 2 or '"deny"' in r.stdout, "muro_guard deniega: %s" % cmd[:60])

# El freno de arriba que de verdad depende de la RUTA es SECRET_HINTS («_secrets»): nc y python -c
# caen también por otras reglas. Si alguien saca el socket de `_secrets/`, esto se pone rojo.
sys.path.insert(0, os.path.dirname(hook))
import muro_guard  # noqa: E402
check("_secrets" in muro_guard.SECRET_HINTS, "SECRET_HINTS del muro sigue cubriendo «_secrets»")
_sin = {k: v for k, v in os.environ.items() if k not in ("BTP_STATE_DIR", "BTP_REPO")}
with mock.patch.dict(os.environ, _sin, clear=True):
    check(_secrets.broker_socket() == real, "la ruta real del socket es la de casa base, dentro de _secrets/")

if fallos:
    print("\n%d FALLO(S)" % len(fallos))
    sys.exit(1)
print("\nOK test_secretos_broker")
