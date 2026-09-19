#!/usr/bin/env python3
"""portguard.py — que los servicios locales (antesala, Observatorio, relays) arranquen LIMPIO.

Problema real (23/6/26): al reiniciar un daemon (`launchctl kickstart -k`), un proceso huérfano
de la ejecución anterior puede seguir ESCUCHANDO en el puerto. El bind nuevo revienta con
`OSError: [Errno 48] Address already in use`; con KeepAlive, launchd lo marca como crash (exit 1)
y entra en bucle de reinicio hasta que alguien hace `pkill` a mano. Pasó con com.btp.staging.

`SO_REUSEADDR` NO basta: solo deja reusar un socket en TIME_WAIT, no uno que está LISTEN activo.
La cura determinista es liberar el puerto antes de bindear — pero SOLO si quien lo ocupa es una
instancia NUESTRA (mismo script). Nunca matamos un proceso ajeno: si el puerto lo ocupa otra cosa,
lo dejamos y dejamos que el bind falle con un mensaje claro (no robamos puertos de nadie).

Idempotente: si el puerto ya está libre, no hace nada. Sin dependencias de terceros (usa lsof/ps
del sistema, por ruta absoluta porque launchd da un PATH mínimo). El muro intacto: es fontanería
local de arranque — no toca red externa, ni datos, ni nada clínico.
"""
import os
import signal
import socket
import subprocess
import time

# Rutas absolutas: bajo launchd el PATH es mínimo y "lsof" pelado no se encuentra.
_LSOF = next((p for p in ("/usr/sbin/lsof", "/usr/bin/lsof", "lsof") if os.path.exists(p)), "lsof")
_PS = next((p for p in ("/bin/ps", "/usr/bin/ps", "ps") if os.path.exists(p)), "ps")

# Firmas por defecto de NUESTROS servidores. Cada llamante pasa la suya (más estrecha) para que,
# p. ej., la antesala solo pueda matar a la antesala.
OWN_MARKERS = ("staging.py", "observatorio.py", "preview_remoto.py")


def _pids_listening(port):
    """PIDs que ESCUCHAN en `port` (cualquier interfaz). [] si lsof no está o nadie escucha.

    Filtra a estado LISTEN: un relay que solo CONECTA al puerto (cliente saliente) no cuenta,
    así free_port(8787) ve al servidor del Observatorio pero no al relay que reenvía hacia él.
    """
    try:
        out = subprocess.run(
            [_LSOF, "-nP", "-iTCP:%d" % int(port), "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return []
    pids = []
    for tok in out.split():
        try:
            pids.append(int(tok))
        except ValueError:
            pass
    return pids


def _cmdline(pid):
    try:
        return subprocess.run(
            [_PS, "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception:
        return ""


def _is_own(pid, markers):
    cmd = _cmdline(pid)
    return any(m in cmd for m in markers)


def free_port(port, markers=OWN_MARKERS, wait=3.0, log=None):
    """Si una instancia NUESTRA escucha en `port`, la termina (SIGTERM, luego SIGKILL) y espera a
    que suelte el puerto. Devuelve la lista de PIDs terminados.

    Salvaguardas: nunca toca el PID actual ni un proceso cuya línea de comando no case con
    `markers` (= no es nuestro). Idempotente: sin ocupantes nuestros → no hace nada y devuelve [].
    """
    me = os.getpid()
    killed = []
    for pid in _pids_listening(port):
        if pid == me or not _is_own(pid, markers):
            continue
        for sig, budget in ((signal.SIGTERM, wait), (signal.SIGKILL, 1.0)):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                break  # ya no está
            except Exception:
                break
            deadline = time.time() + budget
            while time.time() < deadline:
                if pid not in _pids_listening(port):
                    break
                time.sleep(0.1)
            if pid not in _pids_listening(port):
                break
        killed.append(pid)
        if log:
            log("portguard: puerto %d liberado (terminé la instancia previa pid %d)" % (port, pid))
    return killed


def reusable_tcp_server(host, port, markers=OWN_MARKERS, backlog=128, log=None, attempts=3):
    """Socket TCP escuchando en (host, port), arrancado LIMPIO: libera al ocupante propio,
    pone SO_REUSEADDR y reintenta el bind un par de veces (por la breve ventana tras matar).
    Lanza OSError si tras los reintentos sigue ocupado (p. ej. lo tiene un proceso ajeno)."""
    free_port(port, markers, log=log)
    last = None
    for i in range(attempts):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((host, port))
            srv.listen(backlog)
            return srv
        except OSError as e:
            last = e
            srv.close()
            if i < attempts - 1:
                time.sleep(0.4)
                free_port(port, markers, log=log)
    raise last


def http_server(server_cls, host, port, handler, markers=OWN_MARKERS, log=None, attempts=3):
    """Construye un http.server.*HTTPServer arrancado LIMPIO (mismo patrón que reusable_tcp_server).
    `server_cls` es la clase (p. ej. ThreadingHTTPServer). Lanza OSError si sigue ocupado."""
    free_port(port, markers, log=log)
    last = None
    for i in range(attempts):
        try:
            return server_cls((host, port), handler)
        except OSError as e:
            last = e
            if i < attempts - 1:
                time.sleep(0.4)
                free_port(port, markers, log=log)
    raise last
