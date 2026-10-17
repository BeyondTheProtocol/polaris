#!/usr/bin/env python3
"""tools/secretos_broker.py — el Llavero, servido desde la sesión gráfica a las sesiones por SSH.

POR QUÉ EXISTE (17-oct-2026, deuda `llavero_bloqueado`, 7 detecciones). Las sesiones de Claude
Code corren por SSH en una sesión de seguridad «Background», y ahí el login.keychain sale
SIEMPRE bloqueado: `security find-generic-password -w` da rc=36 aunque la clave esté. El
desbloqueo va por sesión de auditoría: el login gráfico de {{TITULAR}} lo abre para la sesión Aqua,
no para SSH (verificado el 17-oct: en Terminal.app rc=0; por SSH rc=36; `launchctl asuser`
no puede saltar a la sesión 0x186b4 sin root). Resultado: semanas de tools que le devolvían
pasos manuales (adjuntar correos, subir a Drive, Umami a ciegas).

QUÉ HACE, Y SOLO ESO. Vive en gui/501 (LaunchAgent com.btp.secretos, LimitLoadToSessionType
Aqua), donde el Llavero se deja leer, y contesta por un socket Unix 0600 dentro de
`tools/state/_secrets/` (0700). Una línea JSON por conexión:

    {"v":1,"servicio":"btp-umami-api"}  →  {"v":1,"estado":"ok","valor":"…"}
    {"v":1,"ping":true}                 →  {"v":1,"estado":"ok"}

Estados: ok · no-existe (rc 44) · ilegible (cualquier otro fallo) · denegado (fuera de la lista)
· rechazado (petición mal formada o par de otro usuario).

LOS FRENOS:
  · Lista CERRADA por nombre (`PERMITIDAS`), no `btp-*` a secas: estar al alcance de una sesión
    es una capacidad nueva, y hay claves que protegen justo porque las sesiones no las leen.
    Nunca las RESERVADAS de `_secrets` (las que firman el permiso de salida) ni `NUNCA`.
  · El nombre casa con `^btp-[a-z0-9-]{1,60}$`: nada que empiece por `-` llega a argv.
  · El par tiene que ser un proceso del mismo usuario (LOCAL_PEERCRED).
  · Lee con `/usr/bin/security` (ruta absoluta), el mismo binario cuyo ACL ya funciona; nada de
    Security.framework, que podría sacar un diálogo en la pantalla de {{TITULAR}}.
  · El log lleva servicio, estado, pid y proceso. NUNCA el valor.
  · No hay subcomando que imprima un secreto: impreso queda quemado en el transcript.
  · La ruta del socket lleva `_secrets`: el muro ya deniega nombrarla desde Bash (`nc -U`,
    `python -c`). Test: tests/test_secretos_broker.py lo fija.

El cliente es `_secrets._keychain`: le pregunta primero y, si no contesta, cae a `security`.

uso:
  python3 tools/secretos_broker.py estado      # ¿socket?, ¿responde?, peticiones de las últimas 24 h
  secretos_broker.py servir                    # SOLO launchd (com.btp.secretos)
Activación, prueba por SSH y marcha atrás: tools/launchd/SECRETOS.md.
"""
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _secrets  # noqa: E402

LABEL = "com.btp.secretos"
SECURITY = _secrets.SECURITY
V_PROTO = 1
MAX_LINEA = 1024
T_CONEXION = 2.0
MAX_SOCKET = 100          # sun_path admite 104 bytes en macOS
MAX_LOG = 1_000_000       # por encima, se queda con la mitad más reciente
ESTADOS = ("ok", "no-existe", "ilegible", "denegado", "rechazado")
_RE_SERVICIO = re.compile(r"^btp-[a-z0-9-]{1,60}$")

# Tramo A del plan aprobado el 17-oct-26: lo que las sesiones necesitan para no devolverle a
# {{TITULAR}} pasos manuales. Leer la App Password de Gmail NO abre el envío: salida_guard deniega
# correo_smtp.py y smtplib desde una sesión (comprobado ejecutando el guard).
# Vive en `_secrets.BROKER_PERMITIDAS` para que el cliente ni pregunte por lo que no está (sin
# ruido de «denegado» en el log por cada lectura de un daemon gui/501).
PERMITIDAS = _secrets.BROKER_PERMITIDAS
# Tramo C: firman, autentican, descifran o publican. No entran aunque alguien las añada arriba
# (gana NUNCA; el test lo fija). El tramo B (API de pago y Telegram) queda fuera mientras {{TITULAR}}
# no diga otra cosa: no está en ninguna de las dos listas, y lo que no está, se deniega.
NUNCA = frozenset(_secrets.RESERVADOS | {
    "btp-restic-password", "btp-latido-externo", "btp-claude-oauth-token", "btp-consola-token",
    "btp-construir-token", "btp-x-mcp-client-id", "btp-x-mcp-client-secret",
    "btp-anatomia-panel-frase", "btp-anatomia-panel-key", "btp-anatomia-panel-pass",
    "btp-pass-app-contacto",
})


def socket_path():
    return _secrets.broker_socket()


def decidir(servicio):
    """None si se puede servir; si no, el estado con que se contesta."""
    if not isinstance(servicio, str) or not _RE_SERVICIO.match(servicio):
        return "rechazado"
    if servicio in NUNCA or servicio not in PERMITIDAS:
        return "denegado"
    return None


def leer_llavero(servicio):
    """(valor | None, estado). 44 es «no existe»; cualquier otro fallo, «ilegible»."""
    try:
        r = subprocess.run([SECURITY, "find-generic-password", "-s", servicio, "-w"],
                           capture_output=True, text=True, timeout=5)
    except Exception:
        return None, "ilegible"
    if r.returncode == 0:
        v = r.stdout.strip()
        return (v, "ok") if v else (None, "ilegible")
    return None, ("no-existe" if r.returncode == 44 else "ilegible")


def _proceso(pid):
    if not pid:
        return ""
    try:
        r = subprocess.run(["/bin/ps", "-p", str(int(pid)), "-o", "comm="],
                           capture_output=True, text=True, timeout=2)
        return os.path.basename(r.stdout.strip())[:60]
    except Exception:
        return ""


class Broker:
    def __init__(self, ruta=None, lector=None, log=None):
        self.ruta = ruta or socket_path()
        self.log = log or os.path.join(os.path.dirname(self.ruta), "broker_log.jsonl")
        self.lector = lector or leer_llavero
        self._sock = None
        self._parar = threading.Event()
        self._log_lock = threading.Lock()

    # ·· el log: nunca lleva el valor
    def _anotar(self, pid, servicio, estado):
        valido = isinstance(servicio, str) and bool(_RE_SERVICIO.match(servicio))
        linea = json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "pid": pid,
                            "proceso": _proceso(pid), "servicio": servicio if valido else "(invalido)",
                            "estado": estado}, ensure_ascii=False) + "\n"
        with self._log_lock:
            try:
                if os.path.exists(self.log) and os.path.getsize(self.log) > MAX_LOG:
                    with open(self.log, encoding="utf-8") as f:
                        lineas = f.readlines()
                    with open(self.log, "w", encoding="utf-8") as f:
                        f.writelines(lineas[len(lineas) // 2:])
                fd = os.open(self.log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as f:
                    f.write(linea)
            except Exception:
                pass

    def atender(self, peticion, pid=None):
        """La respuesta (dict) a una petición ya parseada. No escribe en el socket."""
        if not isinstance(peticion, dict) or peticion.get("v") != V_PROTO:
            self._anotar(pid, None, "rechazado")
            return {"v": V_PROTO, "estado": "rechazado"}
        if peticion.get("ping") is True and set(peticion) == {"v", "ping"}:
            return {"v": V_PROTO, "estado": "ok"}
        servicio = peticion.get("servicio")
        estado = "rechazado" if set(peticion) != {"v", "servicio"} else decidir(servicio)
        if estado:
            self._anotar(pid, servicio, estado)
            return {"v": V_PROTO, "estado": estado}
        valor, estado = self.lector(servicio)
        self._anotar(pid, servicio, estado)
        if estado == "ok" and valor:
            return {"v": V_PROTO, "estado": "ok", "valor": valor}
        return {"v": V_PROTO, "estado": estado if estado in ESTADOS[1:3] else "ilegible"}

    @staticmethod
    def par(conn):
        """(uid, pid) del proceso al otro lado. (None, None) si no se puede saber."""
        try:
            if sys.platform == "darwin":
                cred = conn.getsockopt(0, getattr(socket, "LOCAL_PEERCRED", 1), 76)   # struct xucred
                uid = struct.unpack("=II", cred[:8])[1]
                try:
                    pid = struct.unpack("=i", conn.getsockopt(0, 2, 4))[0]            # LOCAL_PEERPID
                except Exception:
                    pid = None
                return uid, pid
            pid, uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            return uid, pid
        except Exception:
            return None, None

    def _conexion(self, conn):
        try:
            conn.settimeout(T_CONEXION)
            uid, pid = self.par(conn)
            if uid != os.getuid():
                self._anotar(pid, None, "rechazado")
                r = {"v": V_PROTO, "estado": "rechazado"}
            else:
                datos = b""
                while b"\n" not in datos and len(datos) <= MAX_LINEA:
                    trozo = conn.recv(MAX_LINEA)
                    if not trozo:
                        break
                    datos += trozo
                try:
                    if b"\n" not in datos or len(datos) > MAX_LINEA:
                        raise ValueError("línea incompleta o demasiado larga")
                    peticion = json.loads(datos.split(b"\n", 1)[0].decode("utf-8"))
                except Exception:
                    peticion = None
                r = self.atender(peticion, pid)
            conn.sendall((json.dumps(r) + "\n").encode("utf-8"))
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def abrir(self):
        """Carpeta 0700 y socket 0600. Lanza si la ruta no cabe o si ya hay un broker vivo."""
        if len(self.ruta.encode("utf-8")) > MAX_SOCKET:
            raise RuntimeError("la ruta del socket no cabe en sun_path: %s" % self.ruta)
        carpeta = os.path.dirname(self.ruta)
        os.makedirs(carpeta, mode=0o700, exist_ok=True)
        os.chmod(carpeta, 0o700)
        if os.path.lexists(self.ruta):
            prueba = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            prueba.settimeout(0.5)
            try:
                prueba.connect(self.ruta)
                vivo = True
            except Exception:
                vivo = False
            finally:
                prueba.close()
            if vivo:
                raise RuntimeError("ya hay un broker escuchando en %s" % self.ruta)
            os.remove(self.ruta)
        viejo = os.umask(0o177)
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.bind(self.ruta)
        finally:
            os.umask(viejo)
        os.chmod(self.ruta, 0o600)
        s.listen(16)
        s.settimeout(0.5)
        self._sock = s
        return self

    def servir(self):
        while not self._parar.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except Exception:
                if self._parar.is_set():
                    break
                time.sleep(0.2)
                continue
            threading.Thread(target=self._conexion, args=(conn,), daemon=True).start()

    def cerrar(self):
        self._parar.set()
        try:
            if self._sock is not None:
                self._sock.close()
        finally:
            try:
                os.remove(self.ruta)
            except Exception:
                pass


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────
def _servir(ruta=None):
    import signal
    # launchd para con SIGTERM (bootout): sin esto, el `finally` no corre y el socket se queda.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    b = Broker(ruta=ruta).abrir()
    print("secretos: escuchando en %s · %d claves en la lista" % (b.ruta, len(PERMITIDAS)), flush=True)
    try:
        b.servir()
    finally:
        b.cerrar()
    return 0


def _estado(ruta=None):
    ruta = ruta or socket_path()
    hay = os.path.lexists(ruta)
    print("socket: %s (%s)" % (ruta, "existe" if hay else "NO existe: el broker no está activo"))
    r = _secrets._broker_pedir({"v": V_PROTO, "ping": True}, ruta=ruta) if hay else None
    vivo = bool(r and r.get("estado") == "ok")
    print("responde: %s" % ("sí" if vivo else "NO"))
    cuenta, desde = {}, time.time() - 86400
    try:
        with open(os.path.join(os.path.dirname(ruta), "broker_log.jsonl"), encoding="utf-8") as f:
            for linea in f:
                try:
                    d = json.loads(linea)
                    if datetime.fromisoformat(d["ts"]).timestamp() >= desde:
                        cuenta[d.get("estado")] = cuenta.get(d.get("estado"), 0) + 1
                except Exception:
                    continue
    except Exception:
        pass
    print("peticiones 24 h: %s" % (", ".join("%s %d" % kv for kv in sorted(cuenta.items())) or "ninguna"))
    return 0 if vivo else 1


def main(argv):
    orden = argv[1] if len(argv) > 1 else ""
    ruta = None
    if "--socket" in argv[2:-1]:        # solo para la prueba en vivo con un socket temporal
        ruta = argv[argv.index("--socket") + 1]
    if orden == "servir":
        return _servir(ruta)
    if orden == "estado":
        return _estado(ruta)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
