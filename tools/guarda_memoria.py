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
3. **24-sep-2026: ni RSS ni swap de la máquina.** Con la máquina apretada (12 % libre) un glotón de
   2,6 GB marcaba 0,2-0,7 GB de RSS: macOS le saca las páginas y el RSS no las ve. Y el swap que
   se vigilaba era el de TODA la máquina: mataba al árbol por el swap de otras sesiones (código 99
   con 0,9 GB). Ahora se mide la HUELLA del árbol (`proc_pid_rusage`, ve lo que está en swap o
   comprimido) y, antes de matar, se le RESTA lo que `vmmap` atribuye a «IOAccelerator», que es
   Metal: 2 GB liberados en MPS sin vaciar la caché dejaban 2,16 GB de huella y 0,09 de RSS.
   Metal no se ignora del todo: si la máquina YA está empujando swap y el árbol es grande
   contándolo, se mata igual (en memoria unificada la GPU también es RAM). Lo que ya no pasa es
   matar a un árbol pequeño por el swap de otros.

Avisa y mata; no negocia. Matar es irreversible y puede tirar media hora de cómputo, así que el
techo se pone a mano y con criterio, no por defecto alegre.

Uso:  python3 tools/guarda_memoria.py --tope-gb 8 -- <comando…>
"""
import argparse
import os
import re
import signal
import subprocess
import sys
import time


def _huella_una(pid):
    """Huella física (GB) de un pid: `ri_phys_footprint` de proc_pid_rusage. None si no se puede.
    Búfer amplio leído por offset: un struct corto hace que el kernel escriba fuera (segfault)."""
    import ctypes
    import struct
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.dylib")
        buf = ctypes.create_string_buffer(1024)
        if libc.proc_pid_rusage(int(pid), 2, buf) != 0:   # 2 = RUSAGE_INFO_V2
            return None
        # rusage_info_v2: uuid[16], y u64: user, system, pkg_idle, interrupt, pageins, wired,
        # resident (64), phys_footprint (72).
        return struct.unpack_from("<Q", buf.raw, 72)[0] / 1e9
    except Exception:            # noqa: BLE001
        return None


def _huella_gb(pids):
    """Suma de la huella física del árbol (GB). Incluye lo que está en swap o comprimido, que el
    RSS no ve; incluye también Metal (ver _metal_gb). Si no se puede leer, cae al RSS."""
    total, fallo = 0.0, []
    for p in pids:
        h = _huella_una(p)
        if h is None:
            fallo.append(p)
        else:
            total += h
    return total + (_rss_gb(fallo) if fallo else 0.0)


_TAM = {"K": 1 / 1048576, "M": 1 / 1024, "G": 1.0, "T": 1024.0}


def _a_gb(v):
    try:
        return float(v[:-1]) * _TAM[v[-1]] if v[-1] in _TAM else float(v) / 1073741824
    except (ValueError, IndexError):
        return 0.0


def _metal_gb(pids):
    """Lo que `vmmap -summary` atribuye a las regiones IOAccelerator* (Metal/GPU), sumado en el
    árbol: es la parte de la huella que el allocator de Metal reserva. Por región se toma el MAYOR
    de DIRTY y SWAPPED, no la suma: con el tensor vivo las dos columnas cuentan lo mismo (3,8 GB
    medidos para 2 GB reales, 24-sep). Tarda ~1,5 s por pid: solo se llama para CONFIRMAR.

    None si `vmmap` no pudo leer algún pid (proceso saliendo, máquina cargada: «can't suspend»).
    Un fallo NO es «0 GB de Metal»: eso convertía una lectura fallida en uso real y podía matar
    una corrida de Metal inocente, justo el fallo del 20-sep."""
    total = 0.0
    for p in pids:
        try:
            r = subprocess.run(["vmmap", "-summary", str(p)], capture_output=True, text=True,
                               timeout=30)
        except Exception:        # noqa: BLE001
            return None
        if r.returncode != 0 or "REGION TYPE" not in r.stdout:
            return None
        for linea in r.stdout.splitlines():
            m = re.match(r"^IOAccelerator[^\d]*?\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", linea)
            if m:                # VIRTUAL RESIDENT DIRTY SWAPPED
                total += max(_a_gb(m.group(3)), _a_gb(m.group(4)))
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
    """Devuelve (codigo, pico_gb). codigo 99 = lo mató la guarda. pico = uso del árbol sin Metal.

    Mata si (a) la huella del árbol SIN Metal pasa del techo dos muestreos seguidos, o (b) la
    máquina ya ha empujado `tope_swap_gb` al swap desde que esto empezó Y el árbol, contando
    Metal, pasa del techo (es de los que ahogan). El swap sin (b) solo avisa: puede ser de otros.

    Con `traza` se escribe una línea por muestreo (segundos, RSS, huella, sin-Metal, swap nuevo).
    Sirve para la pregunta que el techo NO responde: el techo dice CUÁNDO se pasó, nunca cuánto
    pedía de verdad ni quién.
    """
    swap0 = _swap_gb()
    proc = subprocess.Popen(comando, start_new_session=True)
    pico, t0, seguidas, avisado = 0.0, time.time(), 0, False
    reg = open(traza, "w", encoding="utf-8") if traza else None
    try:
        while proc.poll() is None:
            pids = _arbol(proc.pid)
            huella = _huella_gb(pids)
            swap = _swap_gb() - swap0
            neto = huella
            if huella >= tope_gb:            # la resta de Metal solo si hace falta (vmmap es caro)
                metal = _metal_gb(pids)
                # vmmap tarda ~1,5 s: la huella se relee DESPUÉS y se usa la menor. Si el proceso
                # suelta la GPU en medio (al salir), restar el Metal de ahora a la huella de antes
                # daba 2,2 GB de «uso real» que no existían (medido el 24-sep: 2,2 → 0,17).
                ahora = _huella_gb(pids)
                # Sin lectura de Metal, el RSS: tampoco cuenta Metal, y no se inventa un «0».
                neto = (max(0.0, min(huella, ahora) - metal) if metal is not None
                        else _rss_gb(pids))
            pico = max(pico, neto)
            if reg:
                reg.write("%.0f\t%.2f\t%.2f\t%.2f\t%.2f\n"
                          % (time.time() - t0, _rss_gb(pids), huella, neto, swap))
                reg.flush()
            seguidas = seguidas + 1 if neto >= tope_gb else 0
            motivo = None
            if seguidas >= 2:
                motivo = "el árbol ocupa %.1f GB sin contar Metal y el techo son %.1f" % (neto, tope_gb)
            elif swap >= tope_swap_gb and huella >= tope_gb:
                motivo = ("la máquina ha empujado %.1f GB al swap (tope %.1f) y este árbol ocupa "
                          "%.1f GB contando Metal" % (swap, tope_swap_gb, huella))
            elif swap >= tope_swap_gb and not avisado and verbose:
                print("GUARDA (aviso): la máquina ha empujado %.1f GB al swap, pero este árbol "
                      "ocupa %.1f GB: no es él, no lo mato" % (swap, huella),
                      file=sys.stderr, flush=True)
                avisado = True
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
    p.add_argument("--traza", help="fichero con segundos, RSS, huella, sin-Metal y swap por muestreo")
    p.add_argument("--tope-swap-gb", type=float, default=3.0,
                   help="swap NUEVO de la máquina a partir del cual se corta si el árbol, con Metal, pasa del techo")
    p.add_argument("comando", nargs=argparse.REMAINDER)
    a = p.parse_args(argv)
    cmd = a.comando[1:] if a.comando and a.comando[0] == "--" else a.comando
    if not cmd:
        raise SystemExit("falta el comando: guarda_memoria.py --tope-gb 8 -- <comando…>")
    codigo, pico = corre(cmd, a.tope_gb, a.intervalo, traza=a.traza,
                         tope_swap_gb=a.tope_swap_gb)
    print("guarda: pico %.1f GB sin Metal (techo %.1f)" % (pico, a.tope_gb), file=sys.stderr)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
