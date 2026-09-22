#!/usr/bin/env python3
"""Relay TCP PRIVADO para previsualizar un dev server local desde los dispositivos de {{TITULAR}}
por Tailscale (móvil, otro portátil…), desde cualquier sitio.

Escucha SOLO en la IP de Tailscale de Polaris (red privada de {{TITULAR}}, NO internet) y reenvía a
localhost. No expone nada al público. Reversible: Ctrl-C o `kill` y desaparece. No toca el dev
server (no lo reinicia). Sin dependencias (stdlib).

Uso:  python3 tools/preview_remoto.py [puerto_dev=3005] [puerto_escucha=3006]
Luego, en tu móvil (con Tailscale): http://100.114.113.73:<puerto_escucha>
"""
import os
import socket
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # para importar portguard (tool hermana)
import portguard  # noqa: E402

TS_IP = "100.114.113.73"   # IP de Polaris en tu Tailscale (solo tu tailnet la alcanza)


def _pipe(a, b):
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except Exception:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except Exception:
                pass


def _handle(client, dev_port):
    up = None
    for host in ("::1", "127.0.0.1"):
        try:
            up = socket.create_connection((host, dev_port), timeout=10)
            break
        except Exception:
            up = None
    if up is None:
        client.close()
        return
    threading.Thread(target=_pipe, args=(client, up), daemon=True).start()
    threading.Thread(target=_pipe, args=(up, client), daemon=True).start()


def main():
    dev_port = int(sys.argv[1]) if len(sys.argv) > 1 else 3005
    listen = int(sys.argv[2]) if len(sys.argv) > 2 else 3006
    # Arranque LIMPIO: si un relay huérfano nuestro sigue ocupando ESTE puerto, lo libera y reintenta
    # (port-scoped: NO toca la otra instancia de preview_remoto.py que escucha en otro puerto).
    try:
        srv = portguard.reusable_tcp_server(TS_IP, listen, markers=("preview_remoto.py",), backlog=50,
                                            log=lambda m: print(m, flush=True))
    except OSError as e:
        sys.exit("No pude escuchar en %s:%d (%s). ¿Tailscale activo? ¿IP correcta?" % (TS_IP, listen, e))
    print("preview PRIVADO (solo Tailscale): http://%s:%d  ->  localhost:%d" % (TS_IP, listen, dev_port), flush=True)
    while True:
        try:
            cli, _ = srv.accept()
        except KeyboardInterrupt:
            break
        threading.Thread(target=_handle, args=(cli, dev_port), daemon=True).start()


if __name__ == "__main__":
    main()
