#!/usr/bin/env python3
"""tools/colgados_stdin.py — encuentra (y con --matar, termina) los tools colgados esperando stdin.

POR QUÉ (22-sep-2026, regla de {{TITULAR}}: «cuando haya procesos así hay que matarlos»). Dos
`archivar_nota.py --help` lanzados desde el Bash de Claude se quedaron 2 h y 7 h bloqueados en
`sys.stdin.read()`: su stdin era un socket unix que nunca se cerró. No hacían nada ni lo iban a
hacer. La causa se arregló en `archivar_nota.py`, pero la CLASE (un tool que lee stdin y nadie se
lo da) puede repetirse en cualquier otro.

«ASÍ» ES UNA HUELLA, NO UNA EDAD. La antigüedad sola no prueba nada: el 20-sep casi se mata un
`visor3d.py` sano por llevar 20 h vivo (feedback-no-declarar-colgado-por-antiguedad). Solo se
toca un proceso si cumple TODO esto:
  1. es `python … tools/<algo>.py` (un tool nuestro, no un servidor ni un daemon);
  2. su padre es el envoltorio del Bash de Claude (`shell-snapshots/snapshot-`);
  3. su stdin (fd 0) es un socket unix — lo que da ese Bash, no un pipe con datos ni un fichero;
  4. está dormido (estado S), con CPU acumulada casi nula y lleva al menos MIN_MIN minutos;
  5. no escucha en ningún puerto TCP.
Un proceso que falla una sola condición no se toca, y se dice cuál falló si se pide `--todo`.

Uso:
  python3 tools/colgados_stdin.py            # lista los que tienen la huella (no toca nada)
  python3 tools/colgados_stdin.py --matar    # SIGTERM a esos, y comprueba que murieron
  python3 tools/colgados_stdin.py --min 30   # umbral de minutos (defecto 10)
"""
import os
import re
import signal
import subprocess
import sys
import time

MIN_MIN = 10          # minutos mínimos dormido esperando stdin
CPU_MAX_S = 2.0       # CPU acumulada máxima: un tool que trabaja la supera enseguida
ENVOLTORIO = "shell-snapshots/snapshot-"
# El PROGRAMA es python y su primer argumento no-opción es tools/<x>.py. No casa con la línea de un
# `sh -c "… python3 tools/x.py"` (el envoltorio), que también contiene el texto.
RE_TOOL = re.compile(r"^\S*[Pp]ython[\d.]*\s+(-\S+\s+)*\S*\btools/[\w.-]+\.py(\s|$)")


def _segundos(t):
    """`[[dd-]hh:]mm:ss[.cc]` de ps → segundos."""
    dias = 0
    if "-" in t:
        d, t = t.split("-", 1)
        dias = int(d)
    partes = [float(x) for x in t.split(":")]
    while len(partes) < 3:
        partes.insert(0, 0.0)
    h, m, s = partes
    return dias * 86400 + h * 3600 + m * 60 + s


def procesos():
    out = subprocess.run(["ps", "-axo", "pid=,ppid=,etime=,time=,stat=,command="],
                         capture_output=True, text=True).stdout
    ps = {}
    for linea in out.splitlines():
        c = linea.split(None, 5)
        if len(c) < 6:
            continue
        try:
            ps[int(c[0])] = dict(pid=int(c[0]), ppid=int(c[1]), edad_s=_segundos(c[2]),
                                 cpu_s=_segundos(c[3]), stat=c[4], cmd=c[5])
        except ValueError:
            continue
    return ps


def _stdin_es_socket(pid):
    out = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "0", "-Ft"],
                         capture_output=True, text=True).stdout
    return "tunix" in out.split()


def _escucha(pid):
    out = subprocess.run(["lsof", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN", "-t"],
                         capture_output=True, text=True).stdout
    return bool(out.strip())


def motivo_para_no_tocar(p, padre_cmd, stdin_socket, escucha, min_min=None):
    """"" si tiene la huella completa; si no, la primera condición que falla. Pura: se testea."""
    if not RE_TOOL.search(p["cmd"]):
        return "no es un tool python de tools/"
    if ENVOLTORIO not in (padre_cmd or ""):
        return "su padre no es el Bash de Claude"
    if not stdin_socket:
        return "su stdin no es un socket unix"
    if not p["stat"].startswith("S"):
        return "no está dormido (stat %s)" % p["stat"]
    if p["cpu_s"] > CPU_MAX_S:
        return "ha trabajado (%.1f s de CPU)" % p["cpu_s"]
    min_min = MIN_MIN if min_min is None else min_min
    if p["edad_s"] < min_min * 60:
        return "lleva menos de %d min" % min_min
    if escucha:
        return "escucha en un puerto"
    return ""


def colgados(min_min=None, todo=False):
    min_min = MIN_MIN if min_min is None else min_min
    ps = procesos()
    res = []
    for p in ps.values():
        if p["pid"] == os.getpid() or not RE_TOOL.search(p["cmd"]):
            continue
        padre = ps.get(p["ppid"], {}).get("cmd", "")
        m = motivo_para_no_tocar(p, padre, _stdin_es_socket(p["pid"]), _escucha(p["pid"]), min_min)
        if not m or todo:
            res.append(dict(p, motivo=m))
    return res


def matar(pid, espera=3.0):
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    fin = time.time() + espera
    while time.time() < fin:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def main(argv):
    min_min = MIN_MIN
    if "--min" in argv:
        min_min = float(argv[argv.index("--min") + 1])
    todo = "--todo" in argv
    lista = colgados(min_min, todo)
    if not lista:
        print("✅ ningún tool colgado esperando stdin (≥ %g min)" % min_min)
        return 0
    rc = 0
    for p in lista:
        cmd = p["cmd"][:110]
        if p["motivo"]:
            print("· %d  %s  — no se toca: %s" % (p["pid"], cmd, p["motivo"]))
            continue
        linea = "🪦 %d  %.0f min dormido, %.1f s CPU  %s" % (p["pid"], p["edad_s"] / 60, p["cpu_s"], cmd)
        if "--matar" in argv:
            ok = matar(p["pid"])
            print(linea + ("  → terminado" if ok else "  → ‼️ SIGUE VIVO"))
            rc = rc or (0 if ok else 1)
        else:
            print(linea + "  (con --matar se termina)")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
