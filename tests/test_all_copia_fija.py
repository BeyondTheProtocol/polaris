#!/usr/bin/env python3
"""test_all_copia_fija.py — reescribir `test_all.sh` a mitad de una pasada NO la corrompe.

POR QUÉ EXISTE (10-oct-26). bash lee un script POR TROZOS, a medida que lo ejecuta. Si otro proceso
reescribe el fichero con la pasada en marcha (una fusión que toca `tests/test_all.sh` mientras corre la
pasada nocturna, o mientras otra sesión pasa la suite), bash sigue leyendo desde un desplazamiento que ya
no corresponde: se ejecuta dos veces un trozo (la pasada en paralelo salió con el bloque de resultados y
10 rojos DUPLICADOS) o se salta otro. Se vio al editar el script mientras corría una pasada de validación.

Arreglo: al arrancar, el script se copia a un temporal propio de la ejecución y se re-ejecuta desde ahí
(`exec`), conservando ROOT; los trabajadores del modo paralelo usan la MISMA copia. Se fija:
  1. con el script REESCRITO a mitad (en serie y en paralelo) la pasada acaba con el resultado del script
     ORIGINAL: las mismas baterías, cada una UNA vez, en su orden, y la batería añadida después no corre;
  2. el temporal se borra al acabar (y no queda ninguno de esta ejecución);
  3. las variables de la copia no bajan a las baterías (una batería que lanza otra suite no la confunde);
  4. ROOT sigue siendo el de verdad (las baterías se buscan en `tests/` de la raíz, no del temporal).
"""
import glob
import os
import re
import subprocess
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.environ.get("BTP_SUITE") or os.path.join(RAIZ, "tests", "test_all.sh")
fallos = []


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


lineas = open(SUITE, encoding="utf-8").read().splitlines()
ini = next(i for i, l in enumerate(lineas) if re.match(r"^run\s+test_", l))
fin = next(i for i, l in enumerate(lineas) if l.startswith("_vacia_cola   #"))
CAB = re.sub(r'LARGAS="[^"]*"', 'LARGAS=""', "\n".join(lineas[:ini]), count=1)
FIN = "\n".join(lineas[fin:])


def monta(tmpdir):
    root = tempfile.mkdtemp(prefix="copia_fija_", dir=tmpdir)
    os.makedirs(os.path.join(root, "tests"))
    marca = os.path.join(root, "marcas")
    os.makedirs(marca)
    cuerpos = {
        "test_a.py": "print('ok a')\n",
        # la batería B es la que da tiempo a reescribir el script: avisa de que ha empezado y espera
        "test_b.py": "import os, time\nopen(%r, 'w').write('b-en-marcha')\nfor _ in range(100):\n    if os.path.exists(%r): break\n    time.sleep(0.1)\nprint('ok b')\n" % (marca + "/b_empezo", marca + "/script_reescrito"),
        "test_c.py": "print('ok c')\n",
        "test_d.py": "print('ok d')\n",
    }
    for n, c in cuerpos.items():
        open(os.path.join(root, "tests", n), "w").write(c)
    toy = "runpy test_a.py\nrunpy test_b.py\nrunpy test_c.py\nrunpy test_d.py\n"
    ruta = os.path.join(root, "tests", "test_all.sh")
    open(ruta, "w").write(CAB + "\n" + toy + FIN + "\n")
    return root, ruta, marca


def pasada(jobs):
    tmpdir = tempfile.mkdtemp(prefix="copia_fija_tmp_")
    root, ruta, marca = monta(tmpdir)
    original = open(ruta).read()
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "BTP_ROJO_DIR", "BTP_JOBS", "BTP_PORTABLE",
                                                            "BTP_TEST_ALL_ROOT", "BTP_TEST_ALL_COPIA", "BTP_TEST_ALL_DUENA")}
    env.update(BTP_ROJO_DIR=os.path.join(root, "rojos"), TMPDIR=tmpdir)
    if jobs:
        env["BTP_JOBS"] = str(jobs)
    p = subprocess.Popen(["bash", ruta], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                         stdin=subprocess.DEVNULL, cwd=root)
    # espera a que B esté en marcha y entonces REESCRIBE el script: un bloque grande al principio (corre todos los
    # desplazamientos) y una batería nueva al final de la lista
    for _ in range(300):
        if os.path.exists(os.path.join(marca, "b_empezo")):
            break
        time.sleep(0.1)
    nuevo = "#!/bin/bash\n" + ("# relleno que desplaza todo lo que viene después\n" * 40) + original.split("\n", 1)[1]
    nuevo = nuevo.replace("runpy test_d.py\n", "runpy test_d.py\nrunpy test_e_nueva.py\n")
    # y cambia lo que dice cada trabajador: si un trabajador leyera el fichero VIVO (no la copia), su salida cambiaría
    nuevo = nuevo.replace('echo "── $_n ──"', 'echo "── $_n REESCRITO ──"')
    with open(ruta, "w") as f:         # EN SITIO (mismo inodo, truncar y escribir): un editor, `cat >`, un script.
        f.write(nuevo)                 # Un rename atómico (lo que hace git) no daña a bash, que tiene abierto el inodo viejo.
    open(os.path.join(marca, "script_reescrito"), "w").write("x")
    try:
        out, _ = p.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        p.kill()
        out = p.communicate()[0] + "\n[TIMEOUT]"
    return p.returncode, out, tmpdir


for etiqueta, jobs in (("en serie", None), ("en paralelo (3 trabajos)", 3)):
    print("pasada %s, con el script reescrito a mitad" % etiqueta)
    rc, out, tmpdir = pasada(jobs)
    ejecutadas = re.findall(r"^── (test_\S+) ──$", out, re.M)
    check(ejecutadas == ["test_a.py", "test_b.py", "test_c.py", "test_d.py"],
          "corren las 4 del script original, una vez cada una y en su orden (%s)" % ejecutadas)
    check("test_e_nueva.py" not in out, "la batería que SOLO lista el script reescrito no corre (ni existe: si corriera, fallaría)")
    check(rc == 0 and "TODO EN VERDE" in out, "termina con el resultado del original: rc=0 y «TODO EN VERDE» (rc=%s)" % rc)
    check(out.count("TODO EN VERDE") == 1 and out.count("las 10 baterías más lentas") == 1,
          "el resumen sale UNA vez (antes salía duplicado)")
    restos = glob.glob(os.path.join(tmpdir, "test_all.*"))
    check(not restos, "el temporal de la ejecución se borra al acabar (quedan: %s)" % restos)

print("las variables de la copia no bajan a las baterías, y ROOT es el de verdad")
tmpdir = tempfile.mkdtemp(prefix="copia_fija_tmp_")
root, ruta, marca = monta(tmpdir)
open(os.path.join(root, "tests", "test_a.py"), "w").write(
    "import os, sys\nvars_ = [k for k in os.environ if k.startswith('BTP_TEST_ALL_')]\n"
    "print('FUGA' if vars_ else 'limpio')\nsys.exit(1 if vars_ else 0)\n")
open(os.path.join(marca, "script_reescrito"), "w").write("x")          # B no espera
env = {k: v for k, v in os.environ.items() if k not in ("CI", "BTP_ROJO_DIR", "BTP_JOBS", "BTP_PORTABLE")}
env.update(BTP_ROJO_DIR=os.path.join(root, "rojos"), TMPDIR=tmpdir)
for jobs in (None, "3"):
    e = dict(env)
    if jobs:
        e["BTP_JOBS"] = jobs
    r = subprocess.run(["bash", "tests/test_all.sh"], cwd=root, env=e, capture_output=True, text=True, timeout=120,
                       stdin=subprocess.DEVNULL)
    check(r.returncode == 0 and "limpio" in r.stdout, "ninguna BTP_TEST_ALL_* llega a la batería (%s)" % ("paralelo" if jobs else "serie"))
    check(re.findall(r"^── (test_\S+) ──$", r.stdout, re.M) == ["test_a.py", "test_b.py", "test_c.py", "test_d.py"],
          "las baterías se buscan en tests/ de la raíz real (%s)" % ("paralelo" if jobs else "serie"))

if fallos:
    print("\n❌ %d fallo(s)" % len(fallos))
    sys.exit(1)
print("\nOK")
