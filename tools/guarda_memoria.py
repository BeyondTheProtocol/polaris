#!/usr/bin/env python3
"""tools/guarda_memoria.py — lanza un comando con TECHO de memoria y lo mata si lo pasa.

Por qué existe: el 20-sep-2026 dos procesos distintos ahogaron el mini de 16 GB (la inferencia
de MAMA-MIA en CPU, 16 GB; la segmentación del esqueleto, 20 GB). El segundo iba con una guarda
puesta… que no sirvió: vigilaba el proceso que lanzaba, y la ventanilla clínica arranca el
Python de la venv como SUBPROCESO. Estaba mirando al padre, que no consume nada, mientras el
nieto se hinchaba.

Dos cosas que este script hace y la guarda anterior no:

1. **Vigila el ÁRBOL entero**, no el hijo directo. El comando arranca en su propio grupo de
   procesos y se suma el consumo de todos sus descendientes; al matar, se mata el grupo.
2. **Mide la PRESIÓN, no la reserva.** Aquí me equivoqué dos veces el mismo día y las dos
   costaron cómputo tirado:
   · primero miré el RSS de `ps`, que se desploma en cuanto el proceso cae al swap (42 MB de RSS
     con 16 GB de footprint);
   · luego me fui al footprint de `top`, y ese cuenta el heap que el allocator de Metal RESERVA
     y no devuelve. Medido: con el trabajo real ocupando 1,7 GB de RSS, el footprint marcaba
     8,2 GB y esta guarda mató cuatro corridas seguidas por una reserva que no dolía a nadie.
   Lo que de verdad ahoga la máquina es el RSS del árbol MÁS lo que el sistema ya ha tenido que
   empujar al swap. Así que se vigilan las dos cosas, y el footprint queda como aviso.

Avisa y mata; no negocia. Matar es irreversible y puede tirar media hora de cómputo, así que el
techo se pone a mano y con criterio, no por defecto alegre.

Uso:  python3 tools/guarda_memoria.py --tope-gb 8 -- <comando…>
"""
import argparse
import os
import signal
import subprocess
import sys
import time


def _footprint_gb(pids):
    """Suma el footprint (GB) de esos pids, según `top`. 0.0 si no hay ninguno vivo."""
    if not pids:
        return 0.0
    cmd = ["top", "-l", "1", "-stats", "pid,mem"]
    for p in pids:
        cmd += ["-pid", str(p)]
    try:
        salida = subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return 0.0
    total = 0.0
    for linea in salida.splitlines():
        partes = linea.split()
        if len(partes) == 2 and partes[0].isdigit():
            v = partes[1]
            try:
                n = float(v[:-1])
            except ValueError:
                continue
            total += n * {"K": 1 / 1048576, "M": 1 / 1024, "G": 1.0, "B": 1 / 1073741824}.get(v[-1], 0)
    return total


def _rss_gb(pids):
    """Suma el RSS (GB) de esos pids, según `ps`. Es memoria física de verdad ocupada."""
    if not pids:
        return 0.0
    try:
        salida = subprocess.run(["ps", "-o", "rss=", "-p", ",".join(str(p) for p in pids)],
                                capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return 0.0
    total = 0
    for linea in salida.split("\n"):
        linea = linea.strip()
        if linea.isdigit():
            total += int(linea)
    return total / 1048576.0


def _swap_gb():
    """Swap usado por TODO el sistema, en GB. Si esto sube, la máquina ya está sufriendo."""
    try:
        salida = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True,
                                timeout=10).stdout
        for trozo in salida.split():
            if trozo.endswith("M") and "=" not in trozo:
                pass
        partes = salida.replace("=", " ").split()
        i = partes.index("used")
        v = partes[i + 1]
        return float(v[:-1]) / {"M": 1024.0, "G": 1.0, "K": 1048576.0}.get(v[-1], 1024.0)
    except Exception:
        return 0.0


def _arbol(raiz):
    """El pid raíz y todos sus descendientes, vivos ahora."""
    try:
        salida = subprocess.run(["ps", "-eo", "pid=,ppid="], capture_output=True, text=True,
                                timeout=20).stdout
    except Exception:
        return [raiz]
    hijos = {}
    for linea in salida.split("\n"):
        p = linea.split()
        if len(p) == 2 and p[0].isdigit() and p[1].isdigit():
            hijos.setdefault(int(p[1]), []).append(int(p[0]))
    fuera, pila = [], [raiz]
    while pila:
        n = pila.pop()
        if n in fuera:
            continue
        fuera.append(n)
        pila.extend(hijos.get(n, []))
    return fuera


def corre(comando, tope_gb, intervalo=3.0, verbose=True, traza=None, tope_swap_gb=3.0):
    """Devuelve (codigo, pico_gb). codigo 99 = lo mató la guarda.

    Con `traza` se escribe una línea por muestreo (segundos, GB del árbol, GB del proceso que
    más pide). Sirve para la pregunta que el techo NO responde: el techo dice CUÁNDO se pasó,
    nunca cuánto pedía de verdad ni quién.
    """
    swap0 = _swap_gb()
    proc = subprocess.Popen(comando, start_new_session=True)
    pico, t0 = 0.0, time.time()
    reg = open(traza, "w", encoding="utf-8") if traza else None
    try:
        while proc.poll() is None:
            pids = _arbol(proc.pid)
            gb = _rss_gb(pids)
            huella = _footprint_gb(pids) if traza or verbose else 0.0
            swap = _swap_gb() - swap0
            pico = max(pico, gb)
            if reg:
                reg.write("%.0f\t%.2f\t%.2f\t%.2f\n" % (time.time() - t0, gb, huella, swap))
                reg.flush()
            motivo = None
            if gb >= tope_gb:
                motivo = "el árbol ocupa %.1f GB de RSS y el techo son %.1f" % (gb, tope_gb)
            elif swap >= tope_swap_gb:
                motivo = ("la máquina ha empujado %.1f GB al swap desde que esto empezó (tope %.1f)"
                          % (swap, tope_swap_gb))
            if motivo:
                if verbose:
                    print("GUARDA: %s — matando %d proceso(s)" % (motivo, len(pids)),
                          file=sys.stderr, flush=True)
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    for p in pids:
                        try:
                            os.kill(p, signal.SIGKILL)
                        except Exception:
                            pass
                proc.wait(timeout=30)
                return 99, pico
            time.sleep(intervalo)
    except KeyboardInterrupt:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            pass
        raise
    return proc.returncode, pico


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--tope-gb", type=float, required=True)
    p.add_argument("--intervalo", type=float, default=3.0)
    p.add_argument("--traza", help="fichero con segundos, RSS, footprint y swap por muestreo")
    p.add_argument("--tope-swap-gb", type=float, default=3.0,
                   help="cuánto swap NUEVO se tolera antes de cortar")
    p.add_argument("comando", nargs=argparse.REMAINDER)
    a = p.parse_args(argv)
    cmd = a.comando[1:] if a.comando and a.comando[0] == "--" else a.comando
    if not cmd:
        raise SystemExit("falta el comando: guarda_memoria.py --tope-gb 8 -- <comando…>")
    codigo, pico = corre(cmd, a.tope_gb, a.intervalo, traza=a.traza,
                         tope_swap_gb=a.tope_swap_gb)
    print("guarda: pico %.1f GB de RSS (techo %.1f)" % (pico, a.tope_gb), file=sys.stderr)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
