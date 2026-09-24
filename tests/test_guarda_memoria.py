#!/usr/bin/env python3
"""La guarda de memoria tiene que matar al NIETO, que es donde falló la anterior.

El 20-sep-2026 lancé una segmentación con un techo de 9 GB y llegó a 20 GB igualmente: la
guarda vigilaba el proceso que lanzaba, y la ventanilla clínica arranca el Python de la venv
como SUBPROCESO. Miraba al padre, que no consume nada, mientras el nieto se hinchaba.

Esto lo comprueba con un glotón de verdad, a dos niveles de profundidad: un shell que lanza un
python que reserva memoria. Si la guarda solo mirase al hijo directo, el test fallaría.

Y el error simétrico, del mismo día: por evitar lo anterior me fui al footprint de `top`, que
cuenta el heap que el allocator de Metal RESERVA y nunca devuelve. Con el trabajo real ocupando
1,7 GB, el footprint marcaba 8,2 GB y la guarda mató cuatro corridas seguidas por una reserva
que no le dolía a nadie. El caso 4 es ese freno: reservar sin tocar NO puede matar a nadie.
"""
import os
import subprocess
import sys
import time
import types

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))
import guarda_memoria as G  # noqa: E402

fallos = []


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


if sys.platform != "darwin":
    print("SKIP: la medida de footprint es de `top` de macOS")
    sys.exit(77)

# 1) un comando tranquilo termina solo y la guarda no se mete
t0 = time.time()
codigo, pico = G.corre([sys.executable, "-c", "print('hola')"], tope_gb=8, intervalo=0.4,
                       verbose=False)
check(codigo == 0, "un comando normal sale con 0 y la guarda no lo toca")
check(time.time() - t0 < 30, "  y no se queda colgada esperando")

# 2) el glotón está DOS niveles abajo: shell → python que reserva ~2,5 GB y espera
import tempfile  # noqa: E402
glot = tempfile.NamedTemporaryFile("w", suffix="_glotonazo.py", delete=False)
# Bytes ALEATORIOS: una página casi vacía la comprime macOS. Con la huella (no el RSS) ya no
# importa si acaban en swap, pero así el caso mide memoria de verdad y no un truco del compresor.
glot.write("import os, time\n"
           "b = bytearray(os.urandom(64_000_000)) * 41\n"   # ~2,6 GB incompresibles, ya tocados
           "time.sleep(120)\n")
glot.close()
# dos niveles: shell → python. Si la guarda solo mirase al hijo directo (el shell), no vería nada.
cmd = ["/bin/sh", "-c", "exec %s %s" % (sys.executable, glot.name)]
t0 = time.time()
codigo, pico = G.corre(cmd, tope_gb=1.2, intervalo=0.5, verbose=False)
tardó = time.time() - t0
check(codigo == 99, "mata al NIETO cuando el árbol pasa del techo (código %s)" % codigo)
check(pico >= 1.2, "  y midió el pico de verdad (%.1f GB ≥ 1,2)" % pico)
check(tardó < 90, "  en un tiempo razonable (%.0f s)" % tardó)

# 3) no queda nadie vivo del árbol
resto = subprocess.run(["pgrep", "-f", os.path.basename(glot.name)], capture_output=True, text=True).stdout.strip()
check(not resto, "no queda ningún proceso del árbol vivo")

# 4) reservar SIN tocar no es consumir: una reserva grande y virgen no debe matar a nadie.
#    Es el caso que me costó cuatro corridas tiradas (el heap de Metal, reservado y sin usar).
virgen = tempfile.NamedTemporaryFile("w", suffix="_reservon.py", delete=False)
virgen.write("import mmap, time\n"
             "m = mmap.mmap(-1, 6_000_000_000)\n"      # 6 GB de espacio, ni una página tocada
             "time.sleep(8)\n")
virgen.close()
codigo, pico = G.corre([sys.executable, virgen.name], tope_gb=2.0, intervalo=0.5, verbose=False)
check(codigo == 0, "reservar 6 GB sin TOCARLOS no dispara la guarda con techo de 2 (código %s)" % codigo)
check(pico < 2.0, "  y el pico medido es el uso real, no la reserva (%.1f GB < 2)" % pico)
os.unlink(virgen.name)

os.unlink(glot.name)

# 5-6) Dobles de la medida (24-sep-2026): lo que la máquina real solo enseña bajo presión o con GPU.
#      Árbol real y tranquilo (`sleep`); lo que se falsea es lo que la guarda LEE de él.
_orig = (G._huella_gb, G._metal_gb, G._swap_gb, G._rss_gb)


def con_medida(huella, metal, swap_nuevo=0.0, rss=0.05, tope_swap=3.0):
    base = [None]

    def swap():
        if base[0] is None:
            base[0] = 10.0
            return 10.0
        return 10.0 + swap_nuevo
    G._huella_gb = huella if callable(huella) else (lambda pids: huella)
    G._metal_gb = lambda pids: metal
    G._swap_gb = swap
    G._rss_gb = lambda pids: rss
    try:
        return G.corre(["/bin/sleep", "3"], tope_gb=1.0, intervalo=0.2, verbose=False,
                       tope_swap_gb=tope_swap)
    finally:
        G._huella_gb, G._metal_gb, G._swap_gb, G._rss_gb = _orig


codigo, pico = con_medida(huella=3.0, metal=2.8)
check(codigo == 0, "5) huella alta pero casi toda Metal y la máquina tranquila: NO mata (código %s)" % codigo)
check(pico < 1.0, "   y el pico es el uso sin Metal (%.1f GB)" % pico)
codigo, _p = con_medida(huella=3.0, metal=2.8, swap_nuevo=5.0)
check(codigo == 99, "5b) la misma huella con la máquina YA empujando swap: mata (código %s)" % codigo)
codigo, _p = con_medida(huella=0.3, metal=0.0, swap_nuevo=5.0)
check(codigo == 0, "5c) swap de OTROS con un árbol pequeño: no lo mata (código %s)" % codigo)
# 5f) el proceso suelta la GPU MIENTRAS vmmap lee: huella 3 antes, 0,2 después, Metal ya a 0.
#     Restar el Metal de ahora a la huella de antes inventaba 3 GB de uso (visto: 2,2 → 0,17).
_lecturas = iter([3.0, 0.2] * 50)
codigo, pico = con_medida(huella=lambda pids: next(_lecturas), metal=0.0, rss=0.05)
check(codigo == 0, "5f) suelta la GPU mientras vmmap lee: no mata (código %s)" % codigo)
check(pico < 1.0, "    y el pico usa la huella de después, no la de antes (%.1f GB)" % pico)
codigo, pico = con_medida(huella=3.0, metal=None, rss=0.3)
check(codigo == 0, "5d) vmmap no pudo leer (None): no se toma como «0 GB de Metal», no mata (código %s)" % codigo)
check(pico < 1.0, "    y el pico cae al RSS, no a la huella entera (%.1f GB)" % pico)
codigo, _p = con_medida(huella=3.0, metal=None, rss=2.0)
check(codigo == 99, "5e) sin lectura de Metal pero con RSS sobre el techo: mata (código %s)" % codigo)
codigo, _p = con_medida(huella=3.0, metal=0.0, rss=0.05)
check(codigo == 99, "6) huella alta, sin Metal, con RSS mínimo (proceso en swap): mata (código %s)" % codigo)

# 6b) Lectura de vmmap (salida real recortada del 24-sep): por región, el MAYOR de DIRTY y SWAPPED
#     (con el tensor vivo cuentan lo mismo; sumarlas daba 3,8 GB para 2 reales), y un fallo = None.
_VMMAP = """REGION TYPE                        VIRTUAL RESIDENT    DIRTY  SWAPPED VOLATILE   NONVOL    EMPTY   REGION
IOAccelerator                       64K      64K      64K       0K       0K       0K       0K        2
IOAccelerator (graphics)           1.9G     1.9G     1.9G     1.9G       0K     1.9G     240K       16
MALLOC_SMALL                     111.9M     976K     976K    87.2M       0K       0K       0K       31
"""
_run_orig = G.subprocess.run
G.subprocess.run = lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=_VMMAP, stderr="")
m = G._metal_gb([1])
check(abs(m - (1.9 + 64 / 1048576)) < 0.01, "6b) Metal = mayor de DIRTY y SWAPPED por región, no la suma (%.2f GB)" % m)
G.subprocess.run = lambda *a, **k: types.SimpleNamespace(returncode=255, stdout="", stderr="can't suspend")
check(G._metal_gb([1]) is None, "    y si vmmap falla, None (no «0 GB de Metal»)")
G.subprocess.run = _run_orig

# 7) Metal DE VERDAD: 2 GB en MPS, liberados sin vaciar la caché. Es lo que mató cinco corridas el
#    20-sep por la huella de `top`. Techo de 1 GB y swap aislado: no puede matar.
VENV = os.path.join(RAIZ, ".venv-imagen", "bin", "python")
metal_py = tempfile.NamedTemporaryFile("w", suffix="_metal.py", delete=False)
metal_py.write("import sys, time\n"
               "try:\n    import torch\nexcept Exception:\n    sys.exit(77)\n"
               "if not torch.backends.mps.is_available():\n    sys.exit(77)\n"
               "x = torch.ones(500_000_000, device='mps'); torch.mps.synchronize()\n"
               "del x; torch.mps.synchronize(); time.sleep(8)\n")
metal_py.close()
if not os.path.exists(VENV):
    print("  ⏭️  7) sin .venv-imagen: el caso Metal real no se puede probar aquí")
else:
    codigo, pico = G.corre([VENV, metal_py.name], tope_gb=1.0, intervalo=0.5, verbose=False,
                           tope_swap_gb=10_000)
    if codigo == 77:
        print("  ⏭️  7) sin torch/MPS en .venv-imagen: el caso Metal real no se puede probar aquí")
    else:
        check(codigo == 0, "7) 2 GB de MPS liberados (reserva de Metal) con techo de 1: NO mata (código %s)" % codigo)
        check(pico < 1.0, "   y el pico es el uso sin Metal (%.1f GB < 1)" % pico)
os.unlink(metal_py.name)

print("\nVEREDICTO: %s" % ("TODO CORRECTO" if not fallos else "%d FALLOS" % len(fallos)))
sys.exit(1 if fallos else 0)
