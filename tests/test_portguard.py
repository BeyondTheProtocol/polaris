#!/usr/bin/env python3
"""test_portguard.py — batería del arranque limpio de puertos (tools/portguard.py).

Verifica, SIN tocar la red externa (solo loopback) y sin matar nada ajeno, que:
  · free_port NO toca un proceso cuya línea de comando no case con los markers (no es nuestro);
  · free_port NUNCA mata el PID actual (aunque el marker case con su propio comando);
  · free_port SÍ libera el puerto cuando lo ocupa una instancia NUESTRA (marker presente),
    y es port-scoped (matar al de un puerto no toca al que escucha en otro);
  · reusable_tcp_server entrega un socket escuchando y es idempotente (rebind tras cerrar);
  · http_server entrega un servidor que responde.

Si tocas la lógica de detección/terminación → añade el caso aquí (regresión permanente).
"""
import os
import socket
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import portguard as pg  # noqa: E402

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# Listener hijo: bind a un puerto efímero de loopback, imprime el puerto y duerme. El token en
# argv hace que `ps -o command=` contenga el marker que queramos (así _is_own da True/False).
_CHILD = (
    "import socket,time;"
    "s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
    "s.bind(('127.0.0.1',0));s.listen(8);"
    "print(s.getsockname()[1],flush=True);"
    "time.sleep(60)"
)


def _spawn_listener(token):
    import subprocess
    p = subprocess.Popen([sys.executable, "-c", _CHILD, token],
                         stdout=subprocess.PIPE, text=True)
    port = int(p.stdout.readline().strip())
    return p, port


# Comprobación previa: si no hay lsof utilizable, _pids_listening no ve nada y el test no aplica.
_probe = socket.socket()
_probe.bind(("127.0.0.1", 0))
_probe.listen(1)
_probe_port = _probe.getsockname()[1]
LSOF_OK = os.getpid() in pg._pids_listening(_probe_port)
_probe.close()
check("lsof detecta a quien escucha (entorno con lsof)", LSOF_OK)

if LSOF_OK:
    # 1) No mata un proceso ajeno (marker que NO casa con su comando).
    child, cport = _spawn_listener("MARKER-staging.py")
    try:
        killed = pg.free_port(cport, markers=("no-existe-este-marker",))
        check("no mata si el marker no casa (proceso ajeno a salvo)",
              killed == [] and child.poll() is None and child.pid in pg._pids_listening(cport))
        # 2) Sí libera cuando el marker casa (instancia nuestra) — y de verdad suelta el puerto.
        killed = pg.free_port(cport, markers=("staging.py",))
        check("libera el puerto si es instancia nuestra", child.pid in killed)
        check("el puerto queda libre tras liberar", pg._pids_listening(cport) == [])
        check("el proceso hijo murió", child.wait(timeout=5) is not None)
    finally:
        if child.poll() is None:
            child.kill()

    # 3) Port-scoped: matar al de un puerto NO toca al que escucha en otro.
    a, pa = _spawn_listener("MARKER-staging.py")
    b, pb = _spawn_listener("MARKER-staging.py")
    try:
        pg.free_port(pa, markers=("staging.py",))
        check("free_port es port-scoped (el otro puerto sigue vivo)",
              a.poll() is not None and b.poll() is None and b.pid in pg._pids_listening(pb))
    finally:
        for proc in (a, b):
            if proc.poll() is None:
                proc.kill()

    # 4) NUNCA mata el PID actual, aunque el marker case con su propio comando.
    self_sock = socket.socket()
    self_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    self_sock.bind(("127.0.0.1", 0))
    self_sock.listen(1)
    sport = self_sock.getsockname()[1]
    try:
        killed = pg.free_port(sport, markers=("test_portguard.py",))  # casa con MI propio comando
        check("nunca se mata a sí mismo", killed == [] and os.getpid() in pg._pids_listening(sport))
    finally:
        self_sock.close()

# 5) reusable_tcp_server entrega un socket escuchando, e idempotente al rebindear tras cerrar.
s1 = pg.reusable_tcp_server("127.0.0.1", 0, markers=("staging.py",), backlog=8)
p1 = s1.getsockname()[1]
check("reusable_tcp_server escucha en loopback", p1 > 0 and pg._pids_listening(p1) != [])
s1.close()
time.sleep(0.2)
# El puerto 0 da uno aleatorio cada vez, así que para el rebind usamos uno fijo y libre (p1, ya cerrado).
s2 = pg.reusable_tcp_server("127.0.0.1", p1, markers=("staging.py",), backlog=8)
check("reusable_tcp_server rebindea un puerto recién cerrado (idempotente)", s2.getsockname()[1] == p1)
s2.close()

# 6) http_server entrega un servidor que responde (en loopback, puerto efímero).
class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


httpd = pg.http_server(ThreadingHTTPServer, "127.0.0.1", 0, _H, markers=("staging.py",))
hport = httpd.server_address[1]
t = threading.Thread(target=httpd.serve_forever, daemon=True)
t.start()
try:
    with urllib.request.urlopen("http://127.0.0.1:%d/" % hport, timeout=10) as r:
        check("http_server responde 200", r.status == 200 and r.read() == b"ok")
finally:
    httpd.shutdown()

print("test_portguard: %d OK, %d fallos" % (_pass, _fail))
sys.exit(1 if _fail else 0)
