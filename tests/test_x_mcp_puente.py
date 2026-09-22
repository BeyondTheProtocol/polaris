#!/usr/bin/env python3
"""test_x_mcp_puente.py — el envoltorio del MCP de X relanza el puente en vez de quedarse ciego.

POR QUÉ EXISTE (14-sep-2026). `xurl mcp` lee `~/.xurl` una sola vez al arrancar. Cuando otro
proceso renueva el token, el puente de una sesión larga solo devuelve `-32001 could not reach the
MCP server (transport error)`: verificado con un experimento controlado (OK a las 08:48, fallo a las
09:38 con `~/.xurl` ya renovado). Deuda `x_mcp_transport_error`. `tools/x_mcp_puente.py` lo evita
relanzando el hijo, y este test fija las dos guardas (preventiva y reactiva) y lo que no deben
romper: una respuesta por id, escrituras sin repetir, notificaciones, cancelaciones y cero huérfanos.

Aislado: hijo falso (tests/fixtures/xurl_mcp_falso.py) y `~/.xurl` falso en tmp. Sin red ni token.
"""
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUENTE = os.path.join(ROOT, "tools", "x_mcp_puente.py")
FALSO = os.path.join(ROOT, "tests", "fixtures", "xurl_mcp_falso.py")
sys.path.insert(0, os.path.join(ROOT, "tools"))

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def vivo(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def esperar(cond, timeout=4.0, paso=0.05):
    fin = time.time() + timeout
    while time.time() < fin:
        if cond():
            return True
        time.sleep(paso)
    return cond()


class Cliente(object):
    def __init__(self, extra=None):
        self.dir = tempfile.mkdtemp(prefix="xpuente_")
        self.xurl = os.path.join(self.dir, "xurl")
        with open(self.xurl, "w") as f:
            f.write("falso\n")
        antes = time.time() - 60
        os.utime(self.xurl, (antes, antes))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("FALSO_", "X_PUENTE_"))}
        env.update({
            "FALSO_DIR": self.dir,
            "X_PUENTE_HIJO": json.dumps([sys.executable, FALSO]),
            "X_PUENTE_TOKEN_CMD": json.dumps([sys.executable, "-c", "pass"]),
            "X_PUENTE_XURL_FILE": self.xurl,
            "X_PUENTE_VIGIA_S": "0.2",
            "X_PUENTE_INIT_TIMEOUT_S": "1.5",
            "X_PUENTE_CIERRE_S": "0.5",
            "X_PUENTE_TARDIAS_S": "0.4",
            "X_PUENTE_TOKEN_TIMEOUT_S": "5",
        })
        env.update(extra or {})
        self.err = open(os.path.join(self.dir, "stderr.log"), "wb")
        self.p = subprocess.Popen([sys.executable, PUENTE], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=self.err, env=env)
        self.q = queue.Queue()
        self.todos = []
        self.buffer = []
        self._n = 100
        threading.Thread(target=self._leer, daemon=True).start()

    def _leer(self):
        for raw in iter(self.p.stdout.readline, b""):
            try:
                msg = json.loads(raw)
            except ValueError:
                msg = {"__crudo__": raw.decode("utf-8", "replace").rstrip("\n")}
            self.todos.append(msg)
            self.q.put(msg)

    def enviar(self, obj):
        raw = obj if isinstance(obj, bytes) else json.dumps(obj).encode("utf-8")
        self.p.stdin.write(raw + b"\n")
        self.p.stdin.flush()

    def pedir(self, metodo, params=None, id_=None):
        if id_ is None:
            self._n += 1
            id_ = self._n
        self.enviar({"jsonrpc": "2.0", "id": id_, "method": metodo, "params": params or {}})
        return id_

    def llamar(self, nombre, id_=None):
        return self.pedir("tools/call", {"name": nombre, "arguments": {}}, id_)

    def esperar_id(self, id_, timeout=6.0):
        for m in list(self.buffer):
            if m.get("id") == id_ and "method" not in m:
                self.buffer.remove(m)
                return m
        fin = time.time() + timeout
        while time.time() < fin:
            try:
                m = self.q.get(timeout=max(0.01, fin - time.time()))
            except queue.Empty:
                break
            if m.get("id") == id_ and "method" not in m:
                return m
            self.buffer.append(m)
        return None

    def recoger(self, segundos):
        fin = time.time() + segundos
        while time.time() < fin:
            try:
                self.buffer.append(self.q.get(timeout=max(0.01, fin - time.time())))
            except queue.Empty:
                break

    def iniciar(self):
        r = self.esperar_id(self.pedir("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                      "clientInfo": {"name": "test", "version": "0"}}))
        self.enviar({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return r

    def arranques(self):
        try:
            return int(open(os.path.join(self.dir, "arranques")).read())
        except (OSError, ValueError):
            return 0

    def pid_hijo(self, n):
        return int(open(os.path.join(self.dir, "pid-%d" % n)).read())

    def recibido(self):
        try:
            return [json.loads(l) for l in open(os.path.join(self.dir, "recibido.jsonl"))]
        except OSError:
            return []

    def stderr(self):
        self.err.flush()
        return open(os.path.join(self.dir, "stderr.log"), encoding="utf-8", errors="replace").read()

    def cerrar(self):
        try:
            self.p.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait()
        self.err.close()


def texto(r):
    try:
        return r["result"]["content"][0]["text"]
    except (TypeError, KeyError, IndexError):
        return None


def main():
    import x_mcp_puente as xp

    # 0. Piezas puras: qué se reintenta y qué hijo se lanza.
    ok(xp.reintentable({"method": "tools/call", "params": {"name": "get_users_me"}}), "get_* es lectura")
    ok(xp.reintentable({"method": "tools/call", "params": {"name": "search_posts_all"}}), "search_* es lectura")
    ok(not xp.reintentable({"method": "tools/call", "params": {"name": "create_users_bookmark"}}),
       "create_* NO se reintenta (X pudo ejecutarlo ya)")
    ok(not xp.reintentable({"method": "tools/call", "params": {"name": "delete_users_bookmark"}}), "delete_* NO se reintenta")
    ok(xp.reintentable({"method": "tools/list"}) and xp.reintentable({"method": "ping"}), "tools/list y ping se reintentan")
    ok(not xp.reintentable({"method": "initialize"}), "initialize no pasa por el reintento del cliente")
    cmd = xp.comando_hijo() if not os.environ.get("X_PUENTE_HIJO") else None
    ok(cmd is None or (cmd[1:] == ["mcp", xp.URL] and "cli.js" not in cmd[0]),
       "el hijo por defecto es el binario (o el wrapper de reserva), nunca cli.js: %r" % (cmd,))

    # 1. Paso limpio.
    c = Cliente()
    r = c.iniciar()
    ok(r is not None and r.get("result", {}).get("serverInfo", {}).get("name") == "falso", "initialize llega al hijo y vuelve")
    ok("result" in (c.esperar_id(c.pedir("tools/list")) or {}), "tools/list pasa limpio")
    ok(texto(c.esperar_id(c.llamar("get_users_me"))) == "get_users_me arranque 1", "tools/call pasa limpio")
    ok(c.arranques() == 1, "sin fallos no se relanza nada")
    c.cerrar()

    # 2. REACTIVO: -32001 en una lectura → relanza, reintenta y el cliente recibe OK.
    c = Cliente({"FALSO_FALLA_TRAS": "1"})
    c.iniciar()
    ok(texto(c.esperar_id(c.llamar("get_users_me"))) == "get_users_me arranque 1", "reactivo: 1ª llamada OK en el hijo 1")
    r2 = c.esperar_id(c.llamar("get_users_me"))
    ok(texto(r2) == "get_users_me arranque 2", "reactivo: la llamada que falló vuelve OK desde el hijo 2: %r" % (r2,))
    ok(c.arranques() == 2, "reactivo: un solo relanzamiento (%d arranques)" % c.arranques())
    ok(not any(str(m.get("id", "")).startswith("puente-init") for m in c.todos),
       "el initialize interno del relanzamiento no llega al cliente")
    orden = [e["msg"].get("method") for e in c.recibido() if e["arranque"] == 2 and "msg" in e]
    ok(orden == ["initialize", "notifications/initialized", "tools/call"],
       "hijo 2 recibe initialize → initialized → la petición, en ese orden: %r" % orden)
    err = c.stderr()
    ok("relanzando motivo=reactivo" in err, "el log dice que relanzó por reactivo")
    ok("arguments" not in err and "get_users_me arranque" not in err, "el log no lleva parámetros ni respuestas")
    c.cerrar()

    # 3. Fallo doble: el error llega al cliente, sin bucle, y el freno corta.
    c = Cliente({"FALSO_FALLA_SIEMPRE": "1", "X_PUENTE_MAX_RELANZ": "2"})
    c.iniciar()
    r = c.esperar_id(c.llamar("get_users_me"))
    ok(r is not None and r.get("error", {}).get("code") == -32001 and r["error"].get("data", {}).get("relanzado") is True,
       "fallo doble: el cliente recibe el -32001 con data.relanzado: %r" % (r,))
    ok(c.arranques() == 2, "fallo doble: un relanzamiento por petición (%d)" % c.arranques())
    c.esperar_id(c.llamar("get_users_me"))
    r3 = c.esperar_id(c.llamar("get_users_me"))
    ok(r3 is not None and "error" in r3, "con el freno agotado la petición recibe error, no se cuelga")
    ok(c.arranques() == 3, "freno: como mucho X_PUENTE_MAX_RELANZ relanzamientos (%d arranques)" % c.arranques())
    ok("relanzamiento_frenado" in c.stderr(), "el log registra el freno")
    c.cerrar()

    # 4. Escritura: nunca se reenvía.
    c = Cliente({"FALSO_FALLA_TRAS": "0"})
    c.iniciar()
    r = c.esperar_id(c.llamar("create_users_bookmark"))
    ok(r is not None and r.get("error", {}).get("data", {}).get("relanzado") is True,
       "escritura con -32001: error al cliente con relanzado: %r" % (r,))
    esperar(lambda: c.arranques() == 2)
    veces = sum(1 for e in c.recibido() if e.get("msg", {}).get("params", {}).get("name") == "create_users_bookmark")
    ok(veces == 1, "la escritura llegó al hijo UNA sola vez (%d)" % veces)
    ok(texto(c.esperar_id(c.llamar("get_users_me"))) == "get_users_me arranque 2", "tras relanzar, lo siguiente va al hijo nuevo")
    c.cerrar()

    # 5. En paralelo: una OK tardía del hijo viejo y dos que fallan → exactamente 3 respuestas únicas.
    c = Cliente({"FALSO_FALLA_NOMBRES": "search_posts_all,search_users", "FALSO_RETARDO": json.dumps({"get_users_me": 250})})
    c.iniciar()
    a, b, d = c.llamar("get_users_me"), c.llamar("search_posts_all"), c.llamar("search_users")
    c.recoger(3.0)
    resp = [m for m in c.buffer if m.get("id") in (a, b, d) and "method" not in m]
    ids = sorted(m["id"] for m in resp)
    ok(ids == sorted([a, b, d]), "paralelo: una respuesta por id, sin repetidos: %r" % ids)
    por_id = {m["id"]: texto(m) for m in resp}
    ok(por_id.get(a) == "get_users_me arranque 1", "paralelo: la OK tardía del hijo viejo sí llega: %r" % por_id.get(a))
    ok(por_id.get(b) == "search_posts_all arranque 2" and por_id.get(d) == "search_users arranque 2",
       "paralelo: las dos que fallaron vuelven OK desde el hijo 2: %r" % por_id)
    c.cerrar()

    # 6. Notificaciones del servidor: pasan y en orden.
    c = Cliente()
    c.iniciar()
    i = c.llamar("emite_notif")
    r = c.esperar_id(i)
    notifs = [m.get("params", {}).get("n") for m in c.buffer if m.get("method") == "notifications/message"]
    ok(r is not None and notifs == [1, 2], "notificaciones del servidor llegan en orden antes de la respuesta: %r" % notifs)
    c.cerrar()

    # 7. Cancelación: llega al hijo y la respuesta posterior se descarta.
    c = Cliente({"FALSO_RETARDO": json.dumps({"get_lento": 700})})
    c.iniciar()
    i = c.llamar("get_lento")
    c.enviar({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": i}})
    ok(c.esperar_id(i, timeout=1.5) is None, "petición cancelada: su respuesta no llega al cliente")
    ok(any(e.get("msg", {}).get("method") == "notifications/cancelled" for e in c.recibido()), "la cancelación llega al hijo")
    c.cerrar()

    # 8. El hijo muere solo: relanza y la lectura en vuelo vuelve OK.
    c = Cliente({"FALSO_MUERE_TRAS": "0"})
    c.iniciar()
    r = c.esperar_id(c.llamar("get_users_me"))
    ok(texto(r) == "get_users_me arranque 2", "hijo muerto: se relanza y la lectura vuelve OK: %r" % (r,))
    c.cerrar()

    # 9. PREVENTIVO: ~/.xurl reescrito sin nada en vuelo → relanza antes de que falle.
    c = Cliente()
    c.iniciar()
    ok(texto(c.esperar_id(c.llamar("get_users_me"))) == "get_users_me arranque 1", "preventivo: arranca sirviendo el hijo 1")
    os.utime(c.xurl, None)
    ok(esperar(lambda: c.arranques() == 2, timeout=3), "preventivo: ~/.xurl renovado → relanza sin esperar a un fallo")
    ok(texto(c.esperar_id(c.llamar("get_users_me"))) == "get_users_me arranque 2", "preventivo: la siguiente llamada ya va al hijo nuevo")
    ok(not any("error" in m for m in c.todos), "preventivo: el cliente no ve ningún error")
    ok("relanzando motivo=preventivo" in c.stderr(), "el log dice que relanzó por preventivo")
    esperar(lambda: not vivo(c.pid_hijo(1)), timeout=3)
    ok(not vivo(c.pid_hijo(1)), "preventivo: el hijo viejo se cierra")
    c.cerrar()

    # 10. EOF del cliente: el envoltorio sale y no deja huérfanos.
    c = Cliente()
    c.iniciar()
    pid = c.pid_hijo(1)
    c.p.stdin.close()
    try:
        c.p.wait(timeout=4)
        salio = True
    except subprocess.TimeoutExpired:
        salio = False
    ok(salio, "EOF del cliente: el envoltorio sale")
    ok(esperar(lambda: not vivo(pid), timeout=3), "EOF del cliente: el hijo no queda huérfano")
    c.cerrar()

    # 11. Hijo que ignora EOF y SIGTERM: escalada a SIGKILL.
    c = Cliente({"FALSO_IGNORA_EOF": "1"})
    c.iniciar()
    pid = c.pid_hijo(1)
    c.p.stdin.close()
    try:
        c.p.wait(timeout=6)
        salio = True
    except subprocess.TimeoutExpired:
        salio = False
    ok(salio and esperar(lambda: not vivo(pid), timeout=3), "hijo terco: SIGKILL al grupo y sin huérfano")
    c.cerrar()

    # 12. Líneas que no son JSON: pasan intactas en los dos sentidos.
    c = Cliente()
    c.iniciar()
    c.enviar(b"hola-no-json")
    r = c.esperar_id(c.llamar("emite_basura"))
    ok(r is not None and any(m.get("__crudo__") == "BASURA-NO-JSON" for m in c.buffer + c.todos),
       "línea no JSON del hijo llega intacta al cliente")
    ok(esperar(lambda: any(e.get("crudo") == "hola-no-json" for e in c.recibido())), "línea no JSON del cliente llega intacta al hijo")
    c.cerrar()

    # 13. Un id del cliente con forma de id interno no se confunde.
    c = Cliente({"FALSO_FALLA_TRAS": "1"})
    c.iniciar()
    ok(texto(c.esperar_id(c.llamar("get_users_me", id_="puente-init-2"))) == "get_users_me arranque 1",
       "id 'puente-init-2' del cliente se responde normal")
    ok(texto(c.esperar_id(c.llamar("get_users_me"))) == "get_users_me arranque 2", "y el relanzamiento posterior funciona")
    c.cerrar()

    # 14. kill -9 al envoltorio: el hijo sale por EOF.
    c = Cliente()
    c.iniciar()
    pid = c.pid_hijo(1)
    c.p.kill()
    ok(esperar(lambda: not vivo(pid), timeout=4), "kill -9 al envoltorio: el hijo sale solo")
    c.cerrar()

    # 15. El hijo nuevo no responde al initialize: el cliente recibe error, no se cuelga.
    c = Cliente({"FALSO_FALLA_TRAS": "1", "FALSO_CUELGA_INIT": "1"})
    c.iniciar()
    c.esperar_id(c.llamar("get_users_me"))
    t0 = time.time()
    r = c.esperar_id(c.llamar("get_users_me"), timeout=8)
    ok(r is not None and "error" in r and time.time() - t0 < 6, "initialize colgado: error en %.1fs" % (time.time() - t0))
    c.cerrar()

    # 16. passthrough: solo reenvía.
    c = Cliente({"X_PUENTE_MODO": "passthrough", "FALSO_FALLA_TRAS": "0"})
    c.iniciar()
    r = c.esperar_id(c.llamar("get_users_me"))
    ok(r is not None and r.get("error", {}).get("code") == -32001 and "data" not in r["error"],
       "passthrough: el -32001 llega tal cual: %r" % (r,))
    time.sleep(0.5)
    ok(c.arranques() == 1, "passthrough: nunca relanza")
    c.cerrar()

    print("RESULTADO x_mcp_puente: %d OK, %d fallos" % (_pass, _fail))
    print("✅ X_MCP_PUENTE EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
