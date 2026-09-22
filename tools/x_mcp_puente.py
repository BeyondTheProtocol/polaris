#!/usr/bin/env python3
"""tools/x_mcp_puente.py — envoltorio stdio del MCP de X que no se queda ciego (14-sep-2026).

POR QUÉ EXISTE. `.mcp.json` lanzaba `xurl mcp https://api.x.com/mcp` (xurl 1.2.2): un puente
stdio→HTTP que lee `~/.xurl` una sola vez al arrancar (store/tokens.go) y renueva con el refresh
token que guarda en memoria (auth/auth.go). Los daemons de X renuevan y reescriben `~/.xurl` cada
~2 h, y desde ese momento el puente viejo solo devuelve `-32001 could not reach the MCP server
(transport error)`. Verificado con un experimento controlado: el mismo puente respondió a las 08:48
y falló a las 09:38 con `~/.xurl` ya renovado; un puente recién arrancado funciona siempre. Deuda:
`x_mcp_transport_error`. Además `/opt/homebrew/bin/xurl` es un wrapper de Node (`execFileSync`) que
deja huérfano el binario Go cuando lo matan (xurl #93).

QUÉ HACE.
  · Lanza el binario Go directamente, en su propio grupo de procesos: no quedan huérfanos.
  · Reenvía JSON-RPC línea a línea con los bytes originales; parsea solo para llevar la cuenta.
  · PREVENTIVO: si `~/.xurl` se reescribió después de que el hijo empezara a servir y no hay nada en
    vuelo, relanza el hijo, que lee el token vigente, antes de que falle.
  · REACTIVO: ante -32001/-32002 comprueba con `xurl token` que hay token (nunca abre navegador),
    relanza y reintenta UNA vez, pero solo lecturas (ping, */list, tools/call get_* y search_*).
    Una escritura (marcadores, webhooks...) no se reenvía: X pudo haberla ejecutado ya. Se devuelve
    el error con data.relanzado=true y decide quien llamó.
  · Freno: como mucho X_PUENTE_MAX_RELANZ relanzamientos en 10 min.
  · Log a stderr solo con metadatos (evento, método, herramienta, código, pid). Nunca parámetros
    ni tokens.

Entorno (los tests lo usan para aislarse): X_PUENTE_MODO=passthrough (solo reenvía, sin lógica),
X_PUENTE_PREVENTIVO=0, X_PUENTE_HIJO y X_PUENTE_TOKEN_CMD (listas JSON), X_PUENTE_XURL_FILE,
X_PUENTE_VIGIA_S, X_PUENTE_INIT_TIMEOUT_S, X_PUENTE_TOKEN_TIMEOUT_S, X_PUENTE_CIERRE_S,
X_PUENTE_TARDIAS_S, X_PUENTE_MAX_RELANZ.

Solo biblioteca estándar, compatible con Python 3.9. Test: tests/test_x_mcp_puente.py
"""
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time

URL = "https://api.x.com/mcp"
XURL_CLI = "/opt/homebrew/bin/xurl"
CODIGOS_TOKEN = (-32001, -32002)
LECTURA = re.compile(r"^(get|search)_")
ERROR_GENERICO = {"code": -32001, "message": "xurl mcp: could not reach the MCP server (transport error)"}


def _num(nombre, defecto):
    try:
        return float(os.environ.get(nombre, defecto))
    except ValueError:
        return float(defecto)


MODO = os.environ.get("X_PUENTE_MODO", "normal")
PREVENTIVO = os.environ.get("X_PUENTE_PREVENTIVO", "1") != "0"
XURL_FILE = os.environ.get("X_PUENTE_XURL_FILE") or os.path.expanduser("~/.xurl")
VIGIA_S = _num("X_PUENTE_VIGIA_S", 60)
INIT_TIMEOUT_S = _num("X_PUENTE_INIT_TIMEOUT_S", 20)
TOKEN_TIMEOUT_S = _num("X_PUENTE_TOKEN_TIMEOUT_S", 15)
CIERRE_S = _num("X_PUENTE_CIERRE_S", 2)
TARDIAS_S = _num("X_PUENTE_TARDIAS_S", 2)
MAX_RELANZ = int(_num("X_PUENTE_MAX_RELANZ", 3))
VENTANA_RELANZ_S = 600


def log(evento, **kw):
    try:
        extra = " ".join("%s=%s" % (k, v) for k, v in kw.items())
        sys.stderr.write("[x_mcp_puente] %s %s\n" % (evento, extra))
        sys.stderr.flush()
    except Exception:
        pass


def comando_hijo():
    """El binario Go de xurl, sin el wrapper de Node. Si no se encuentra, el wrapper como reserva."""
    crudo = os.environ.get("X_PUENTE_HIJO")
    if crudo:
        return json.loads(crudo)
    try:
        binario = os.path.join(os.path.dirname(os.path.realpath(XURL_CLI)), "binary", "xurl")
        if os.path.isfile(binario) and os.access(binario, os.X_OK):
            return [binario, "mcp", URL]
    except OSError:
        pass
    return [XURL_CLI, "mcp", URL]


def comando_token():
    crudo = os.environ.get("X_PUENTE_TOKEN_CMD")
    if crudo:
        return json.loads(crudo)
    return [comando_hijo()[0], "token"]


def reintentable(msg):
    """¿Se puede repetir sin efectos? Solo lecturas: X pudo ejecutar una escritura antes del fallo."""
    metodo = msg.get("method") or ""
    if metodo == "ping" or metodo.endswith("/list"):
        return True
    if metodo == "tools/call":
        return bool(LECTURA.match(str((msg.get("params") or {}).get("name") or "")))
    return False


def _parse(raw):
    try:
        msg = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return msg if isinstance(msg, dict) else None


def _clave(id_):
    return json.dumps(id_, sort_keys=True)


class Hijo(object):
    """Un proceso `xurl mcp`. `gen` distingue al hijo actual de los retirados."""

    def __init__(self, gen, puente):
        self.gen = gen
        self.puente = puente
        self.referencia = None      # cuándo empezó a servir: el preventivo compara ~/.xurl con esto
        self.cerrado = False
        self._wlock = threading.Lock()
        self.proc = subprocess.Popen(comando_hijo(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=None, start_new_session=True)
        log("hijo_arranca", gen=gen, pid=self.proc.pid)
        threading.Thread(target=self._leer, daemon=True).start()

    def _leer(self):
        try:
            for raw in iter(self.proc.stdout.readline, b""):
                self.puente.desde_hijo(self, raw)
        except Exception as e:
            log("hijo_lectura_error", gen=self.gen, error=type(e).__name__)
        self.proc.wait()
        self.puente.hijo_termino(self)

    def escribir(self, raw):
        if not raw.endswith(b"\n"):
            raw += b"\n"
        with self._wlock:
            try:
                self.proc.stdin.write(raw)
                self.proc.stdin.flush()
                return True
            except (OSError, ValueError):
                return False

    def vivo(self):
        return self.proc.poll() is None

    def cerrar(self):
        """Cierre escalonado: EOF (el puente borra su sesión), SIGTERM al grupo, SIGKILL al grupo."""
        if self.cerrado:
            return
        self.cerrado = True
        try:
            with self._wlock:
                self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        for senal, espera in ((None, CIERRE_S), (signal.SIGTERM, 1.0), (signal.SIGKILL, 1.0)):
            if senal is not None:
                try:
                    os.killpg(self.proc.pid, senal)
                except OSError:
                    pass
            try:
                self.proc.wait(timeout=espera)
                break
            except subprocess.TimeoutExpired:
                continue
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)   # por si quedó algún proceso del grupo
        except OSError:
            pass
        log("hijo_cerrado", gen=self.gen, pid=self.proc.pid)


class Puente(object):
    def __init__(self):
        self.lock = threading.RLock()
        self.cambio = threading.Condition(self.lock)
        self._out = threading.Lock()
        self.gen = 0
        self.hijo = None
        self.relanzando = False
        self.cerrando = False
        self.init_msg = None
        self.initialized_raw = None
        self.en_vuelo = {}              # clave -> info de la petición del cliente
        self.cola = []                  # ("raw", bytes) o ("clave", clave) llegados durante un relanzamiento
        self.cancelados = set()
        self.peticiones_servidor = {}   # clave -> gen del hijo que la hizo
        self.relanzamientos = []
        self.init_interno = None
        self.init_interno_ok = False

    # ── hacia el cliente ─────────────────────────────────────────────────────────────────────
    def al_cliente(self, raw):
        if not raw.endswith(b"\n"):
            raw += b"\n"
        with self._out:
            try:
                sys.stdout.buffer.write(raw)
                sys.stdout.buffer.flush()
            except (OSError, ValueError):
                pass

    def error_al_cliente(self, id_, error, relanzado=False):
        err = dict(error) if isinstance(error, dict) else dict(ERROR_GENERICO)
        if relanzado:
            data = dict(err["data"]) if isinstance(err.get("data"), dict) else {}
            data["relanzado"] = True
            err["data"] = data
        self.al_cliente(json.dumps({"jsonrpc": "2.0", "id": id_, "error": err}).encode("utf-8"))

    # ── hacia el hijo ────────────────────────────────────────────────────────────────────────
    def arrancar(self):
        with self.lock:
            self.gen += 1
            self.hijo = Hijo(self.gen, self)

    def _enviar_crudo(self, raw):
        with self.lock:
            if self.relanzando:
                self.cola.append(("raw", raw))
                return
            hijo = self.hijo
        if hijo is None or not hijo.escribir(raw):
            log("escritura_fallida", tipo="crudo")

    def _enviar_peticion(self, clave):
        with self.lock:
            info = self.en_vuelo.get(clave)
            if info is None:
                return
            if self.relanzando:
                info["gen"] = "cola"
                self.cola.append(("clave", clave))
                return
            hijo = self.hijo
            info["gen"] = hijo.gen
        if not hijo.escribir(info["raw"]):
            log("escritura_fallida", tipo="peticion", metodo=info["metodo"])   # el lector verá la muerte

    # ── entrada del cliente ──────────────────────────────────────────────────────────────────
    def desde_cliente(self, raw):
        msg = None if MODO == "passthrough" else _parse(raw)
        if msg is None:
            self._enviar_crudo(raw)
            return
        metodo = msg.get("method")
        tiene_id = "id" in msg
        if metodo == "initialize" and tiene_id:
            self.init_msg = msg
        elif metodo == "notifications/initialized":
            self.initialized_raw = raw
        elif metodo == "notifications/cancelled":
            rid = (msg.get("params") or {}).get("requestId")
            if rid is not None:
                with self.lock:
                    self.en_vuelo.pop(_clave(rid), None)
                    self.cancelados.add(_clave(rid))
        if metodo and tiene_id:
            if metodo != "initialize":
                self.quizas_preventivo()
            clave = _clave(msg["id"])
            params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
            with self.lock:
                self.cancelados.discard(clave)
                self.en_vuelo[clave] = {"raw": raw, "id": msg["id"], "metodo": metodo,
                                        "herramienta": params.get("name") if metodo == "tools/call" else None,
                                        "gen": None, "reintentado": False,
                                        "lectura": reintentable(msg), "error": None}
            self._enviar_peticion(clave)
            return
        if not metodo and tiene_id:     # respuesta del cliente a una petición del servidor
            with self.lock:
                gen = self.peticiones_servidor.pop(_clave(msg["id"]), None)
                actual = self.gen
            if gen is not None and gen != actual:
                log("respuesta_cliente_descartada", motivo="hijo_relanzado")
                return
        self._enviar_crudo(raw)

    # ── salida del hijo ──────────────────────────────────────────────────────────────────────
    def desde_hijo(self, hijo, raw):
        if MODO == "passthrough":
            self.al_cliente(raw)
            return
        msg = _parse(raw)
        if msg is None:
            if hijo.gen == self.gen:
                self.al_cliente(raw)
            return
        es_respuesta = "id" in msg and "method" not in msg
        if es_respuesta and hijo.referencia is None:
            hijo.referencia = time.time()
        if "method" in msg:
            if hijo.gen != self.gen:
                return                  # peticiones o notificaciones de un hijo retirado
            if "id" in msg:
                with self.lock:
                    self.peticiones_servidor[_clave(msg["id"])] = hijo.gen
            self.al_cliente(raw)
            return
        if not es_respuesta:
            if hijo.gen == self.gen:
                self.al_cliente(raw)
            return

        clave = _clave(msg["id"])
        err = msg.get("error")
        codigo = err.get("code") if isinstance(err, dict) else None
        accion = None
        with self.lock:
            if self.init_interno is not None and clave == self.init_interno and hijo.gen == self.gen:
                self.init_interno_ok = "error" not in msg
                self.init_interno = None
                self.cambio.notify_all()
                return
            if clave in self.cancelados:
                self.cancelados.discard(clave)
                return
            info = self.en_vuelo.get(clave)
            if info is None or info["gen"] != hijo.gen:
                log("respuesta_descartada", gen=hijo.gen, motivo="no_en_vuelo" if info is None else "otra_generacion")
                return
            if codigo in CODIGOS_TOKEN and info["metodo"] != "initialize":
                info["error"] = err
                log("fallo_token", gen=hijo.gen, codigo=codigo, metodo=info["metodo"], herramienta=info["herramienta"])
                if info["lectura"] and not info["reintentado"]:
                    info["reintentado"] = True
                    info["gen"] = "reintento"
                    if self.relanzando:
                        accion = None           # el relanzamiento en curso la reenviará
                    elif hijo.gen != self.gen:
                        accion = "reenviar"     # ya hay un hijo nuevo sirviendo
                    else:
                        accion = "relanzar"
                else:
                    self.en_vuelo.pop(clave, None)
                    accion = "error_y_relanzar" if (hijo.gen == self.gen and not self.relanzando) else "error"
            else:
                self.en_vuelo.pop(clave, None)
                accion = "al_cliente"
        if accion == "al_cliente":
            self.al_cliente(raw)
        elif accion == "reenviar":
            self._enviar_peticion(clave)
        elif accion == "relanzar":
            self._relanzar_async("reactivo")
        elif accion in ("error", "error_y_relanzar"):
            self.error_al_cliente(msg["id"], err, relanzado=True)
            if accion == "error_y_relanzar":
                self._relanzar_async("reactivo")

    def hijo_termino(self, hijo):
        with self.lock:
            if self.cerrando or hijo.cerrado or hijo.gen != self.gen:
                return
            nunca_sirvio = hijo.referencia is None and hijo.gen == 1
        log("hijo_murio", gen=hijo.gen, rc=hijo.proc.returncode)
        if MODO == "passthrough" or nunca_sirvio:
            self._fallar_todo()
            self.salir(1)               # que /mcp lo marque fallido en vez de relanzar en bucle
            return
        self.relanzar("hijo_muerto")

    # ── relanzamiento ────────────────────────────────────────────────────────────────────────
    def quizas_preventivo(self):
        if MODO == "passthrough" or not PREVENTIVO:
            return
        with self.lock:
            hijo = self.hijo
            if (self.relanzando or self.cerrando or hijo is None or hijo.referencia is None
                    or self.en_vuelo or self.init_msg is None):
                return
            referencia = hijo.referencia
        try:
            mtime = os.path.getmtime(XURL_FILE)
        except OSError:
            return
        if mtime > referencia:
            self.relanzar("preventivo")

    def vigia(self):
        while not self.cerrando:
            time.sleep(VIGIA_S)
            try:
                self.quizas_preventivo()
            except Exception as e:
                log("vigia_error", error=type(e).__name__)

    def _relanzar_async(self, motivo):
        threading.Thread(target=self.relanzar, args=(motivo,), daemon=True).start()

    def _hay_token(self):
        try:
            r = subprocess.run(comando_token(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=TOKEN_TIMEOUT_S)
            return r.returncode == 0
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False

    def _fallar_reintentos(self):
        with self.lock:
            fallar = [self.en_vuelo.pop(c) for c, i in list(self.en_vuelo.items()) if i["gen"] == "reintento"]
        for info in fallar:
            self.error_al_cliente(info["id"], info["error"], relanzado=True)

    def _fallar_todo(self):
        with self.lock:
            pendientes = list(self.en_vuelo.values())
            self.en_vuelo.clear()
        for info in pendientes:
            self.error_al_cliente(info["id"], info["error"])

    def _vaciar_cola(self):
        with self.lock:
            cola, self.cola = self.cola, []
        for tipo, valor in cola:
            if tipo == "raw":
                self._enviar_crudo(valor)
            else:
                self._enviar_peticion(valor)

    def relanzar(self, motivo):
        with self.lock:
            if self.relanzando or self.cerrando:
                return
            ahora = time.time()
            self.relanzamientos = [t for t in self.relanzamientos if ahora - t < VENTANA_RELANZ_S]
            frenado = len(self.relanzamientos) >= MAX_RELANZ
            if not frenado:
                self.relanzamientos.append(ahora)
                self.relanzando = True
            viejo = self.hijo
        if frenado:
            log("relanzamiento_frenado", motivo=motivo, max_en_10_min=MAX_RELANZ)
            self._fallar_reintentos()
            if viejo is None or not viejo.vivo():
                self._fallar_todo()
                self.salir(1)
            return
        log("relanzando", motivo=motivo, gen_vieja=viejo.gen if viejo else None)
        if motivo != "preventivo" and not self._hay_token():
            log("sin_token_valido", motivo=motivo)
            with self.lock:
                self.relanzando = False
            self._fallar_reintentos()
            self._vaciar_cola()
            return

        with self.lock:
            self.gen += 1
            nuevo = Hijo(self.gen, self)
            self.hijo = nuevo
            init = self.init_msg
            initialized = self.initialized_raw
        ok = True
        if init is not None:
            interno = dict(init)
            interno["id"] = "puente-init-%d-%d" % (nuevo.gen, os.getpid())
            with self.lock:
                self.init_interno = _clave(interno["id"])
                self.init_interno_ok = False
            nuevo.escribir(json.dumps(interno).encode("utf-8"))
            fin = time.time() + INIT_TIMEOUT_S
            with self.lock:
                while self.init_interno is not None and time.time() < fin and not self.cerrando:
                    self.cambio.wait(max(0.05, fin - time.time()))
                ok = self.init_interno is None and self.init_interno_ok
                self.init_interno = None
            if ok and initialized is not None:
                nuevo.escribir(initialized)
            if not ok:
                log("init_interno_fallido", gen=nuevo.gen)

        if viejo is not None:           # margen para las respuestas que aún le pertenecen al viejo
            fin = time.time() + TARDIAS_S
            while time.time() < fin:
                with self.lock:
                    if not any(i["gen"] == viejo.gen for i in self.en_vuelo.values()):
                        break
                time.sleep(0.05)

        fallar, reenviar = [], []
        with self.lock:
            for clave, info in list(self.en_vuelo.items()):
                if viejo is not None and info["gen"] == viejo.gen:
                    if info["lectura"] and not info["reintentado"]:
                        info["reintentado"] = True
                        info["gen"] = "reintento"
                    else:
                        fallar.append(self.en_vuelo.pop(clave))
            for clave, info in list(self.en_vuelo.items()):
                if info["gen"] == "reintento":
                    if ok:
                        reenviar.append(clave)
                    else:
                        fallar.append(self.en_vuelo.pop(clave))
            self.relanzando = False
        for info in fallar:
            self.error_al_cliente(info["id"], info["error"], relanzado=True)
        for clave in reenviar:
            self._enviar_peticion(clave)
        self._vaciar_cola()
        if viejo is not None:
            threading.Thread(target=viejo.cerrar, daemon=True).start()
        log("relanzado", motivo=motivo, gen=nuevo.gen, pid=nuevo.proc.pid, init_ok=ok)

    # ── cierre ───────────────────────────────────────────────────────────────────────────────
    def salir(self, rc=0):
        with self.lock:
            ya = self.cerrando
            self.cerrando = True
            hijo = self.hijo
            self.cambio.notify_all()
        if not ya and hijo is not None:
            hijo.cerrar()
        os._exit(rc)


def main():
    puente = Puente()

    def _senal(signum, _frame):
        log("senal", sig=signum)
        puente.salir(0)

    signal.signal(signal.SIGTERM, _senal)
    signal.signal(signal.SIGHUP, _senal)
    puente.arrancar()
    if MODO != "passthrough":
        threading.Thread(target=puente.vigia, daemon=True).start()
    for raw in iter(sys.stdin.buffer.readline, b""):
        if raw.strip():
            puente.desde_cliente(raw)
    log("eof_cliente")
    puente.salir(0)


if __name__ == "__main__":
    main()
