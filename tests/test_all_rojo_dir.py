#!/usr/bin/env python3
"""test_all_rojo_dir.py — el log de un rojo es de SU ejecución, no del último que pasó por /tmp.

POR QUÉ EXISTE (25-sep-26, deuda `test-all-log-rojo-tmp-compartido`, 3 detecciones). `test_all.sh`
guardaba cada rojo en `/tmp/rojo-<test>.log`, una ruta fija que comparten todas las sesiones en
paralelo. Ese día el log de `test_atribucion` de una rama enseñaba el fallo de OTRA sesión
(gate_salida.py en vez de tools/fichas.py), y antes ya había llevado a pedir un revert por un rojo
que no era (feedback-verificar-el-rojo-antes-de-revertir).

Fija:
  1. Dos ejecuciones con el mismo test en rojo dejan su log en carpetas DISTINTAS, cada una con
     su propio fallo.
  2. La ruta que imprime la línea «🔴 ROJO» es la del log de verdad.
  3. En el CI (`CI` puesto: un runner por ejecución) sigue en /tmp, que es donde lo buscan el
     workflow público y `healthcheck` (`##[group]rojo-<batería>.log`).
  4. Ningún `/tmp/rojo-` fijo queda escrito en test_all.sh fuera de ese valor por defecto del CI.
"""
import os
import re
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.join(RAIZ, "tests", "test_all.sh")
fallos = []


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


def cabecera():
    """Lo que test_all.sh define antes de la primera batería: variables y las funciones run/runpy."""
    lineas = open(SUITE, encoding="utf-8").read().splitlines()
    fin = next(i for i, l in enumerate(lineas) if re.match(r"^run\s+test_", l))
    return "\n".join(lineas[:fin])


def corre(marca, env_extra):
    """Ejecuta la cabecera real con un ROOT falso cuyo único test falla imprimiendo `marca`."""
    root = tempfile.mkdtemp(prefix="rojo_dir_root_")
    os.makedirs(os.path.join(root, "tests"))
    with open(os.path.join(root, "tests", "falla.py"), "w") as f:
        f.write("import sys\nprint(%r)\nsys.exit(1)\n" % marca)
    script = cabecera() + '\nROOT=%s\nrunpy falla.py\necho "DIR=$ROJO_DIR"\n' % root
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "BTP_ROJO_DIR")}
    env.update(env_extra)
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60)
    m = re.search(r"log: (\S+)\)", r.stdout)
    d = re.search(r"^DIR=(.*)$", r.stdout, re.M)
    return (m.group(1) if m else None), (d.group(1) if d else None), r.stdout


print("1-2) dos ejecuciones, cada una con su log")
log_a, dir_a, out_a = corre("fallo-de-A", {})
log_b, dir_b, out_b = corre("fallo-de-B", {})
check(log_a and log_b and log_a != log_b, "rutas distintas (%s · %s)" % (log_a, log_b))
check(log_a and os.path.exists(log_a) and "fallo-de-A" in open(log_a).read(),
      "el log de A existe y trae SU fallo")
check(log_b and os.path.exists(log_b) and "fallo-de-B" in open(log_b).read(),
      "el log de B existe y trae SU fallo")
check(log_a and not log_a.startswith("/tmp/rojo-"), "fuera del /tmp/rojo-* compartido")
check(dir_a and log_a and log_a.startswith(dir_a + "/"), "el log vive en la carpeta de su ejecución")

print("3) en el CI sigue en /tmp")
log_ci, dir_ci, _ = corre("fallo-ci", {"CI": "true"})
check(log_ci == "/tmp/rojo-falla.py.log", "CI=true → /tmp/rojo-falla.py.log (salió %s)" % log_ci)

print("4) ninguna ruta fija compartida")
texto = open(SUITE, encoding="utf-8").read()
fijas = [l.strip() for l in texto.splitlines()
         if "/tmp/rojo-" in l and not l.lstrip().startswith("#")]
check(not fijas, "test_all.sh no escribe en /tmp/rojo-* (quedan: %s)" % fijas)

# Limpieza: solo las carpetas por ejecución que creó este test, y el log del caso CI.
for d in (dir_a, dir_b):
    if d and os.path.basename(d).startswith("rojo."):
        subprocess.run(["rm", "-r", d], capture_output=True)
if log_ci == "/tmp/rojo-falla.py.log" and os.path.exists(log_ci):
    os.remove(log_ci)

if fallos:
    print("\n🔴 %d fallo(s)" % len(fallos))
    sys.exit(1)
print("\n✅ logs de rojo por ejecución: en verde")
