#!/usr/bin/env python3
"""test_colgados_stdin.py — se mata el tool colgado esperando stdin, y SOLO ese.

POR QUÉ (22-sep-2026, regla de {{TITULAR}}: «cuando haya procesos así hay que matarlos»). Dos
`archivar_nota.py --help` quedaron 2 h y 7 h bloqueados leyendo un stdin que el Bash de Claude no
cierra. La huella se fija aquí con procesos de verdad: uno que la cumple (se detecta y muere) y
otro idéntico salvo el stdin (no se toca). Y cada condición por separado, porque la lección del
20-sep es que la edad sola NO basta para matar nada.
"""
import os
import socket
import subprocess
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))
import colgados_stdin as C      # noqa: E402

fallos = 0


def ok(cond, desc, detalle=""):
    global fallos
    if not cond:
        fallos += 1
    print("  %s %s%s" % ("✅" if cond else "❌ MAL", desc, ("  — " + detalle) if not cond else ""))


print("── la huella, condición a condición ──")
BASE = dict(pid=1, ppid=0, edad_s=3600, cpu_s=0.05, stat="S",
            cmd="/opt/homebrew/bin/python3 tools/archivar_nota.py --help")
PADRE = "/bin/zsh -c source /Users/x/.claude/shell-snapshots/snapshot-zsh-1.sh && eval '…'"
ok(C.motivo_para_no_tocar(BASE, PADRE, True, False) == "", "el caso del 22-sep tiene la huella")
for cambio, padre, sock, esc, etiqueta in (
        (dict(cmd="/usr/bin/python3 -m http.server 8000"), PADRE, True, False, "no es un tool de tools/"),
        ({}, "/usr/sbin/launchd", True, False, "padre que no es el Bash de Claude (daemon)"),
        ({}, PADRE, False, False, "stdin que no es socket (pipe o fichero)"),
        (dict(stat="R"), PADRE, True, False, "proceso trabajando (R)"),
        (dict(cpu_s=40.0), PADRE, True, False, "proceso con CPU acumulada"),
        (dict(edad_s=120), PADRE, True, False, "proceso joven"),
        ({}, PADRE, True, True, "proceso que escucha en un puerto")):
    p = dict(BASE, **cambio)
    ok(C.motivo_para_no_tocar(p, padre, sock, esc) != "", "NO se toca: " + etiqueta)
ok(not C.RE_TOOL.search("/bin/sh -c : x; /usr/bin/python3 tools/a.py; true") and
   C.RE_TOOL.search("/opt/homebrew/Cellar/python@3.14/3.14.6/Frameworks/Python.framework/Versions/3.14/"
                    "Resources/Python.app/Contents/MacOS/Python tools/archivar_nota.py --help"),
   "reconoce al python del tool, no al shell que lo envuelve")
ok(C._segundos("1-02:03:04") == 93784 and C._segundos("07:23:44") == 26624 and
   C._segundos("0:00.05") == 0.05, "lee los tiempos de ps (con días, h:m:s y m:s.cc)")

print("── de verdad: un tool esperando stdin bajo un Bash como el de Claude ──")
tmp = tempfile.mkdtemp(prefix="colgados-")
os.makedirs(os.path.join(tmp, "tools"))
tool = os.path.join(tmp, "tools", "espera_stdin.py")
with open(tool, "w") as fh:
    fh.write("import sys\nsys.stdin.read()\n")
envoltorio = ": shell-snapshots/snapshot-test; %s %s; true" % (sys.executable, tool)

a, b = socket.socketpair()
colgado = subprocess.Popen(["/bin/sh", "-c", envoltorio], stdin=b)
r, w = os.pipe()
con_pipe = subprocess.Popen(["/bin/sh", "-c", envoltorio], stdin=r)
time.sleep(1.5)
try:
    hallados = {p["pid"]: p for p in C.colgados(min_min=0)}
    nuestros = [p for p in hallados.values() if tool in p["cmd"]]
    ok(len(nuestros) == 1, "detecta el del socket y no el del pipe", repr([p["cmd"] for p in nuestros]))
    if nuestros:
        pid = nuestros[0]["pid"]
        ok(C.matar(pid), "--matar lo termina y comprueba que murió")
        colgado.wait(timeout=5)
        ok(colgado.returncode is not None, "el Bash que lo lanzó sigue su curso (no se queda colgado)")
    ok(con_pipe.poll() is None, "el del pipe sigue vivo: no se ha tocado")
finally:
    for pr in (colgado, con_pipe):
        if pr.poll() is None:
            pr.kill()
    subprocess.run(["pkill", "-f", tool])
    a.close(); b.close(); os.close(r); os.close(w)

print("── el vigía (launchd, cada 5 min) lo detecta y lo termina solo ──")
import vigia as V      # noqa: E402
a, b = socket.socketpair()
colgado = subprocess.Popen(["/bin/sh", "-c", envoltorio], stdin=b)
time.sleep(1.5)
guardado, C.MIN_MIN = C.MIN_MIN, 0
try:
    anoms = [x for x in V.detectar_colgados_stdin() if tool in x["detalle"]]
    ok(len(anoms) == 1 and anoms[0]["clase"] == "auto_arreglable",
       "el vigía lo ve como anomalía auto-arreglable", repr(anoms))
    if anoms:
        hecho, desc = V.auto_arreglar(anoms[0])
        ok(hecho, "el auto-arreglo lo termina", desc)
        colgado.wait(timeout=5)
        hecho2, desc2 = V.auto_arreglar(anoms[0])
        ok(not hecho2 and "no se toca" in desc2, "con el pid ya muerto no mata nada (re-comprueba)", desc2)
finally:
    C.MIN_MIN = guardado
    if colgado.poll() is None:
        colgado.kill()
    subprocess.run(["pkill", "-f", tool])
    a.close(); b.close()

print("── la causa: archivar_nota.py ya no se cuelga ──")
an = os.path.join(RAIZ, "tools", "archivar_nota.py")
a, b = socket.socketpair()
try:
    t0 = time.time()
    r = subprocess.run([sys.executable, an, "--help"], stdin=b, capture_output=True, text=True, timeout=20)
    ok(r.returncode == 0 and "archivar_nota" in r.stdout and time.time() - t0 < 10,
       "--help responde y sale, aunque stdin sea un socket abierto")
    t0 = time.time()
    r = subprocess.run([sys.executable, an, "Título"], stdin=b, capture_output=True, text=True, timeout=20,
                       env=dict(os.environ, BTP_ARCHIVAR_ESPERA_STDIN="1",
                                BTP_FV_DIR=tempfile.mkdtemp(prefix="fv-")))
    ok(r.returncode == 2 and time.time() - t0 < 10,
       "con un título y el socket sin datos, sale con error en vez de esperar para siempre",
       "rc=%s %.1fs" % (r.returncode, time.time() - t0))
except subprocess.TimeoutExpired:
    ok(False, "archivar_nota.py sale solo", "SE HA COLGADO (timeout)")
finally:
    a.close(); b.close()

print("\ntest_colgados_stdin: %d fallos" % fallos)
sys.exit(1 if fallos else 0)
