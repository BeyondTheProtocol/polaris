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

4. **25-sep-2026: también vigila que el árbol NO SE QUEDE QUIETO** (`inactivo_s`). El 24-sep un
   `visor3d suv` se quedó en interbloqueo dentro de nnU-Net: el principal esperando un lock y 7
   hijos `multiprocessing.spawn` en `sem_wait`, todo al 0 % de CPU más de 8 min, y habría seguido
   así para siempre. Se mide el tiempo de CPU ACUMULADO del árbol (no el %cpu instantáneo, que un
   proceso sano a ratos también marca 0): si en `inactivo_s` no avanza más de `_umbral_cpu()` (2 s, o el 2 % de una ventana corta), se
   vuelca un `sample` de cada proceso (para cazar la causa, que aún no sabemos) y se mata el
   grupo con código 98. `corre_con_reintento` lo relanza UNA vez; si se repite, falla cerrado.

Avisa y mata; no negocia. Matar es irreversible y puede tirar media hora de cómputo, así que el
techo se pone a mano y con criterio, no por defecto alegre.

Uso:  python3 tools/guarda_memoria.py --tope-gb 8 -- <comando…>
      python3 tools/guarda_memoria.py --inactivo-min 10 --reintentos 1 -- <comando…>
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


CODIGO_MEMORIA = 99
CODIGO_CUELGUE = 98
# CPU que el árbol entero tiene que gastar en la ventana para contar como vivo. Un árbol colgado
# gasta centésimas (los hilos de fondo de Python y torch); uno trabajando, segundos por segundo.
INACTIVO_CPU_S = 2.0
# ...y como mucho esta FRACCIÓN de la ventana (25-sep-26, deuda visor3d-cuelgue-inestable-con-carga).
# 2 s fijos en una ventana corta pedían ~67 % de un núcleo: con la máquina a carga 51 sobre 10
# núcleos, un proceso trabajando al 100 % recibía 0,3 s en 3 s (medido con `ps`) y se mataba como
# colgado. Con la ventana de producción (10 min) el umbral sigue siendo 2 s.
INACTIVO_CPU_FRAC = 0.02


def _umbral_cpu(inactivo_s):
    return min(INACTIVO_CPU_S, INACTIVO_CPU_FRAC * inactivo_s)


def _a_segundos(t):
    """`ps -o time=` de macOS: «M:SS.ss», «H:MM:SS» o «D-HH:MM:SS» → segundos."""
    dias = 0
    if "-" in t:
        d, t = t.split("-", 1)
        dias = int(d)
    s = 0.0
    for parte in t.split(":"):
        s = s * 60 + float(parte)
    return dias * 86400 + s


def _cpu_por_pid(pids):
    """{pid: segundos de CPU acumulados} de los que siguen vivos. {} si `ps` falla."""
    try:
        salida = subprocess.run(["ps", "-o", "pid=,time=", "-p", ",".join(str(p) for p in pids)],
                                capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return {}
    fuera = {}
    for linea in salida.split("\n"):
        p = linea.split()
        if len(p) == 2 and p[0].isdigit():
            try:
                fuera[int(p[0])] = _a_segundos(p[1])
            except ValueError:
                pass
    return fuera


def _vuelca_sample(pids, carpeta, verbose=True):
    """`sample <pid> 1` de cada proceso del árbol a un fichero. Es la traza que faltó el 24-sep
    para saber en QUÉ lock estaba el interbloqueo. Local; son pilas de llamadas, no datos."""
    if not carpeta:
        return None
    try:
        os.makedirs(carpeta, exist_ok=True)
        ruta = os.path.join(carpeta, "cuelgue-%s-%d.txt" % (time.strftime("%Y%m%d-%H%M%S"), pids[0]))
        with open(ruta, "w", encoding="utf-8") as f:
            for p in pids[:12]:
                f.write("===== pid %d =====\n" % p)
                f.flush()
                try:
                    r = subprocess.run(["sample", str(p), "1"], capture_output=True, text=True,
                                       timeout=30)
                    f.write(r.stdout or r.stderr)
                except Exception as e:     # noqa: BLE001
                    f.write("sample falló: %s\n" % e)
        if verbose:
            print("GUARDA: traza del cuelgue en %s" % ruta, file=sys.stderr, flush=True)
        return ruta
    except OSError:
        return None


def _mata_grupo(proc, pids):
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        for p in pids:
            try:
                os.kill(p, signal.SIGKILL)
            except Exception:
                pass
    proc.wait(timeout=30)


def corre(comando, tope_gb, intervalo=3.0, verbose=True, traza=None, tope_swap_gb=3.0,
          inactivo_s=None, volcado_dir=None, env=None):
    """Devuelve (codigo, pico_gb). codigo 99 = lo mató la guarda. pico = uso del árbol sin Metal.
    codigo 98 = colgado: el árbol pasó `inactivo_s` sin gastar CPU. `tope_gb=None` = sin techo.

    Mata si (a) la huella del árbol SIN Metal pasa del techo dos muestreos seguidos, o (b) la
    máquina ya ha empujado `tope_swap_gb` al swap desde que esto empezó Y el árbol, contando
    Metal, pasa del techo (es de los que ahogan). El swap sin (b) solo avisa: puede ser de otros.

    Con `traza` se escribe una línea por muestreo (segundos, RSS, huella, sin-Metal, swap nuevo).
    Sirve para la pregunta que el techo NO responde: el techo dice CUÁNDO se pasó, nunca cuánto
    pedía de verdad ni quién.
    """
    swap0 = _swap_gb() if tope_gb is not None else 0.0
    proc = subprocess.Popen(comando, start_new_session=True, env=env)
    pico, t0, seguidas, avisado = 0.0, time.time(), 0, False
    # CPU por pid, el MÁXIMO visto: un hijo que termina no puede restar a la suma y fingir quietud
    cpu_visto, cpu_ref, t_ref = {}, 0.0, time.time()
    reg = open(traza, "w", encoding="utf-8") if traza else None
    try:
        while proc.poll() is None:
            pids = _arbol(proc.pid)
            if inactivo_s:
                for p, c in _cpu_por_pid(pids).items():
                    cpu_visto[p] = max(c, cpu_visto.get(p, 0.0))
                cpu = sum(cpu_visto.values())
                if cpu - cpu_ref > _umbral_cpu(inactivo_s):
                    cpu_ref, t_ref = cpu, time.time()
                elif time.time() - t_ref >= inactivo_s and proc.poll() is None:
                    if verbose:
                        print("GUARDA: el árbol (%d procesos) lleva %.0f s sin gastar CPU "
                              "(%.2f s en la ventana): colgado, lo mato"
                              % (len(pids), time.time() - t_ref, cpu - cpu_ref),
                              file=sys.stderr, flush=True)
                    _vuelca_sample(pids, volcado_dir, verbose)
                    _mata_grupo(proc, _arbol(proc.pid))
                    return CODIGO_CUELGUE, pico
            if tope_gb is None:
                time.sleep(intervalo)
                continue
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
                _mata_grupo(proc, pids)
                return CODIGO_MEMORIA, pico
            time.sleep(intervalo)
    except KeyboardInterrupt:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            pass
        raise
    return proc.returncode, pico


def corre_con_reintento(comando, tope_gb=None, reintentos=1, al_reintentar=None, **kw):
    """`corre` y, si acaba COLGADO (98), lo relanza hasta `reintentos` veces. Solo el cuelgue se
    reintenta: un 99 de memoria volvería a pasar igual, y un error del programa también.
    Es seguro porque visor3d escribe cada caché en `.parcial` y la sella con `os.replace`: lo
    que deja a medias un intento matado no pasa por bueno en el siguiente.
    Devuelve (codigo, pico_gb, intentos). Si el último intento también se cuelga: 98 (cerrado)."""
    intentos = 0
    while True:
        intentos += 1
        codigo, pico = corre(comando, tope_gb, **kw)
        if codigo != CODIGO_CUELGUE or intentos > reintentos:
            return codigo, pico, intentos
        if al_reintentar:
            al_reintentar(intentos)
        print("GUARDA: reintento %d de %d tras el cuelgue" % (intentos, reintentos),
              file=sys.stderr, flush=True)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--tope-gb", type=float, default=None)
    p.add_argument("--inactivo-min", type=float, default=None,
                   help="mata el árbol (código 98) si pasa estos minutos sin gastar CPU")
    p.add_argument("--reintentos", type=int, default=0, help="relanzamientos tras un cuelgue")
    p.add_argument("--volcado", help="carpeta para la traza `sample` del cuelgue")
    p.add_argument("--intervalo", type=float, default=3.0)
    p.add_argument("--traza", help="fichero con segundos, RSS, huella, sin-Metal y swap por muestreo")
    p.add_argument("--tope-swap-gb", type=float, default=3.0,
                   help="swap NUEVO de la máquina a partir del cual se corta si el árbol, con Metal, pasa del techo")
    p.add_argument("comando", nargs=argparse.REMAINDER)
    a = p.parse_args(argv)
    cmd = a.comando[1:] if a.comando and a.comando[0] == "--" else a.comando
    if not cmd:
        raise SystemExit("falta el comando: guarda_memoria.py --tope-gb 8 -- <comando…>")
    if a.tope_gb is None and a.inactivo_min is None:
        raise SystemExit("falta qué vigilar: --tope-gb y/o --inactivo-min")
    codigo, pico, _ = corre_con_reintento(
        cmd, a.tope_gb, reintentos=a.reintentos, intervalo=a.intervalo, traza=a.traza,
        tope_swap_gb=a.tope_swap_gb, volcado_dir=a.volcado,
        inactivo_s=a.inactivo_min * 60 if a.inactivo_min else None)
    if a.tope_gb is not None:
        print("guarda: pico %.1f GB sin Metal (techo %.1f)" % (pico, a.tope_gb), file=sys.stderr)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
