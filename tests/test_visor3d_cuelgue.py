#!/usr/bin/env python3
"""Un visor3d colgado dentro de nnU-Net no puede quedarse colgado PARA SIEMPRE. Datos SINTÉTICOS.

El 24-sep-2026 `lector_clinico.py procesa visor3d -- suv …` se quedó en interbloqueo: el
principal esperando un lock (psynch_cvwait) y 7 hijos `multiprocessing.spawn` en sem_wait, todo
al 0 % de CPU más de 8 min. Se mató a mano; sin nadie mirando, habría seguido así indefinidamente
(deuda visor3d-nnunet-cuelgue-multiproceso).

Aquí se reproduce esa FORMA sin tocar un dato de la paciente: un visor3d de verdad, con
`totalsegmentator.python_api` sustituido por un doble (patrón de test_visor3d_procedencia.py)
que lanza dos hijos por `spawn` que esperan en un semáforo y deja al principal bloqueado en un
lock sin timeout. Se lanza por la VENTANILLA (`procesa visor3d`), que es por donde pasa todo.

  1. Colgado siempre → la ventanilla devuelve 98 en tiempo acotado, tras exactamente 2 intentos,
     sin dejar vivo ningún hijo spawn, y con la traza `sample` escrita.
  2. Colgado solo la primera vez → 0, con exactamente 1 reintento, y la caché queda sellada.
  3. Un árbol que TRABAJA (quema CPU más que la ventana) no se mata.
Cada caso lleva un timeout exterior: si vuelve el cuelgue sin límite, el test falla, no se cuelga.
"""
import os
import signal
import subprocess
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))
import guarda_memoria as G  # noqa: E402

VENV = os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"),
                    ".venv-imagen", "bin", "python")
if sys.platform != "darwin":
    print("SKIP: `ps -o time=` y `sample` son de macOS")
    sys.exit(77)
if not os.path.exists(VENV):
    if os.environ.get("BTP_PORTABLE"):
        print("SKIP: sin .venv-imagen (modo portátil)")
        sys.exit(77)
    print("ROJO: falta .venv-imagen en casa base; el vigilante de cuelgues no puede probarse")
    sys.exit(1)

VENTANA_S = 3.0
fallos = []


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


# El visor3d falso: se ejecuta con la venv de imagen, como el real por la ventanilla.
VISOR_FALSO = r'''
import importlib.util, multiprocessing, os, sys, threading, types

RAIZ, TMP = os.environ["T_RAIZ"], os.environ["T_TMP"]


def espera(sem):                      # hijo spawn: sem_wait para siempre, como el 24-sep
    sem.acquire()


def ts_falso(input, output, task, **kw):
    n = len(open(os.path.join(TMP, "intentos")).read()) if os.path.exists(
        os.path.join(TMP, "intentos")) else 0
    with open(os.path.join(TMP, "intentos"), "a") as f:
        f.write("x")
    if n < int(os.environ["T_CUELGA_VECES"]):
        ctx = multiprocessing.get_context("spawn")
        sem = ctx.Semaphore(0)
        for _ in range(2):
            p = ctx.Process(target=espera, args=(sem,))
            p.start()
            with open(os.path.join(TMP, "pids"), "a") as f:
                f.write("%d\n" % p.pid)
        candado = threading.Lock()
        candado.acquire()
        candado.acquire()             # el principal, bloqueado en un lock sin timeout
    import nibabel as nib, numpy as np
    nib.save(nib.Nifti1Image(np.ones((4, 4, 4), np.uint8), np.eye(4)), output)


if __name__ == "__main__":
    with open(os.path.join(TMP, "pgids"), "a") as f:   # para que el test limpie pase lo que pase
        f.write("%d\n" % os.getpgrp())
    import totalsegmentator  # noqa: F401  (el paquete real; solo se sustituye la API)
    pa = types.ModuleType("totalsegmentator.python_api")
    pa.totalsegmentator = ts_falso
    sys.modules["totalsegmentator.python_api"] = pa
    spec = importlib.util.spec_from_file_location("visor3d", os.path.join(RAIZ, "tools", "visor3d.py"))
    V = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(V)
    V.SALIDA_RAIZ = TMP
    V.exige_zona_clinica = lambda r: None
    V.exige_telemetria_apagada = lambda: None
    V.convierte = lambda raices, serie: ("/no/importa.nii.gz", {"modalidad": "CT"})
    hechas = V.segmenta([], "S1", tareas=["liver_lesions"], device="cpu")
    print("SEGMENTADO", hechas["liver_lesions"])
'''

# Los conductores: el caso corre en un proceso aparte con tope exterior, para que un vigilante
# roto haga el test ROJO en vez de colgarlo. Ni el log clínico real ni la carpeta real se tocan.
POR_VENTANILLA = r'''
import os, sys
sys.path.insert(0, os.path.join(os.environ["T_RAIZ"], "tools"))
import lector_clinico as L
TMP = os.environ["T_TMP"]
L.LOG = os.path.join(TMP, "acceso.log")
L.CUELGUES_DIR = os.path.join(TMP, "cuelgues")
L.PROCESADORES["visor3d"] = (os.environ["T_VISOR"], os.environ["T_VENV"])
if hasattr(L, "VIGILA_CUELGUE"):
    L.VIGILA_CUELGUE["visor3d"] = float(os.environ["T_VENTANA"]) / 60
sys.exit(L.procesa("test", ["visor3d", "--"]))
'''

POR_GUARDA = r'''
import os, sys
sys.path.insert(0, os.path.join(os.environ["T_RAIZ"], "tools"))
import guarda_memoria as G
codigo, _, intentos = G.corre_con_reintento(
    [sys.executable, "-c", os.environ["T_CMD"]], None, reintentos=0, intervalo=0.3,
    inactivo_s=float(os.environ["T_VENTANA"]), verbose=False)
sys.exit(codigo)
'''

# Lo que corre bajo la guarda en los casos 3: apunta su grupo y luego trabaja y/o se queda quieto.
APUNTA = ("import os,time\nopen(os.path.join(os.environ['T_TMP'],'pgids'),'a')"
          ".write('%d\\n' % os.getpgrp())\n")


def _vivo(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # un zombi responde a kill(0); `ps` lo marca Z
    estado = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True,
                            text=True).stdout.strip()
    return bool(estado) and not estado.startswith("Z")


def _lee(tmp, nombre, tipo=str):
    ruta = os.path.join(tmp, nombre)
    return open(ruta).read() if os.path.exists(ruta) else tipo()


def lanza(conductor, tope_s, **extra):
    tmp = tempfile.mkdtemp(prefix="visor3d_cuelgue_")
    visor = os.path.join(tmp, "visor_falso.py")
    open(visor, "w").write(VISOR_FALSO)
    env = dict(os.environ, T_RAIZ=RAIZ, T_TMP=tmp, T_VISOR=visor, T_VENV=VENV,
               T_VENTANA=str(VENTANA_S), **{k: str(v) for k, v in extra.items()})
    t0 = time.time()
    # a FICHERO, no a pipe: un hijo huérfano que herede el pipe dejaría esperando su EOF, y el
    # test mediría eso en vez del vigilante
    log = open(os.path.join(tmp, "salida.txt"), "w")
    p = subprocess.Popen([sys.executable, "-c", conductor], env=env, start_new_session=True,
                         stdout=log, stderr=subprocess.STDOUT)
    try:
        codigo = p.wait(timeout=tope_s)
    except subprocess.TimeoutExpired:
        codigo = None                          # el cuelgue sin límite: se da por ROJO
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            p.kill()
        p.wait()
    log.close()
    dur = time.time() - t0
    pids = [int(x) for x in _lee(tmp, "pids").split()]
    fin = time.time() + 10
    while any(_vivo(x) for x in pids) and time.time() < fin:
        time.sleep(0.3)
    vivos = [x for x in pids if _vivo(x)]
    # limpieza incondicional: la guarda lanza cada intento en SU grupo, que el tope de arriba no
    # alcanza. Con un vigilante roto esto es lo que impide dejar basura en la máquina.
    for g in {int(x) for x in _lee(tmp, "pgids").split()}:
        try:
            os.killpg(g, signal.SIGKILL)
        except OSError:
            pass
    for x in vivos:
        try:
            os.kill(x, signal.SIGKILL)
        except OSError:
            pass
    return dict(codigo=codigo, dur=dur, salida=_lee(tmp, "salida.txt"), tmp=tmp, pids=pids,
                vivos=vivos, intentos=len(_lee(tmp, "intentos")))


print("1) visor3d colgado siempre, por la ventanilla")
r = lanza(POR_VENTANILLA, 150, T_CUELGA_VECES=99)
check(r["codigo"] is not None, "termina solo, sin el timeout exterior (%.0f s)" % r["dur"])
check(r["codigo"] == G.CODIGO_CUELGUE, "sale con 98, fallo cerrado (salió %s)" % r["codigo"])
check(r["intentos"] == 2, "exactamente 2 intentos: el original y UN reintento (hubo %d)"
      % r["intentos"])
check(len(r["pids"]) == 4, "el doble lanzó sus hijos spawn (%d)" % len(r["pids"]))
check(not r["vivos"], "no queda vivo ningún hijo spawn (vivos: %s)" % r["vivos"])
cuelgues = os.path.join(r["tmp"], "cuelgues")
trazas = sorted(os.listdir(cuelgues)) if os.path.isdir(cuelgues) else []
check(len(trazas) == 2 and all("Call graph" in open(os.path.join(cuelgues, t)).read()
                               for t in trazas),
      "una traza `sample` por cuelgue, con pila de llamadas (%s)" % trazas)
log = _lee(r["tmp"], "acceso.log")
check("CUELGUE-visor3d-reintento" in log and "CUELGUE-visor3d-fallo-cerrado" in log,
      "el reintento y el fallo cerrado quedan en el log de la ventanilla")

print("2) colgado solo la primera vez")
r = lanza(POR_VENTANILLA, 150, T_CUELGA_VECES=1)
check(r["codigo"] == 0, "el reintento termina bien y sale 0 (salió %s)" % r["codigo"])
check(r["intentos"] == 2, "un solo reintento (hubo %d intentos)" % r["intentos"])
check("SEGMENTADO" in r["salida"], "la segmentación llegó al final")
seg = os.path.join(r["tmp"], "cache", "S1", "seg", "liver_lesions.nii.gz")
check(os.path.exists(seg + ".sello.json") and not os.path.exists(seg + ".parcial.nii.gz"),
      "la caché queda sellada y sin parcial del intento matado")
check(not r["vivos"], "los hijos del intento colgado no sobreviven (vivos: %s)" % r["vivos"])

print("3) un árbol que trabaja no se toca")
quema = APUNTA + "t=time.time()\nwhile time.time()-t<%f: sum(range(10000))\n" % (VENTANA_S * 3)
r = lanza(POR_GUARDA, 60, T_CMD=quema)
check(r["codigo"] == 0, "un proceso que quema CPU %.0f s con ventana de %.0f s acaba solo "
      "(código %s)" % (VENTANA_S * 3, VENTANA_S, r["codigo"]))

print("3b) un árbol que trabaja y DESPUÉS se cuelga sí se mata (el caso real: 38 min y cuelgue)")
trabaja = APUNTA + ("t=time.time()\nwhile time.time()-t<%f: sum(range(10000))\n"
                    "time.sleep(3600)\n" % (VENTANA_S * 2))
r = lanza(POR_GUARDA, 60, T_CMD=trabaja)
check(r["codigo"] == G.CODIGO_CUELGUE and r["dur"] < VENTANA_S * 6,
      "trabajó %.0f s, se quedó quieto y se mató con 98 en %.0f s (código %s)"
      % (VENTANA_S * 2, r["dur"], r["codigo"]))

print("4) el reloj de CPU se lee bien")
check(G._a_segundos("0:01.50") == 1.5 and G._a_segundos("1:02:03") == 3723
      and G._a_segundos("2-00:00:01") == 172801, "formatos M:SS.ss, H:MM:SS y D-HH:MM:SS")

if fallos:
    print("\n🔴 %d fallo(s):" % len(fallos))
    for f in fallos:
        print("   - " + f)
    sys.exit(1)
print("\n✅ vigilante de cuelgues de visor3d: los 5 casos en verde")
