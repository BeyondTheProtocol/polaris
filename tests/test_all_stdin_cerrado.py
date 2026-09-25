#!/usr/bin/env python3
"""test_all_stdin_cerrado.py — la suite no le pasa a sus tests un stdin que nunca se cierra.

POR QUÉ EXISTE (25-sep-26, deuda `test-session-start-topologia-cuelgue-transitorio`, 4x, escalada).
`test_session_start_topologia.py` caía en `test_all.sh` con 4 TimeoutExpired de 30 s y suelto pasaba
siempre. Se atribuyó a la carga de la máquina. Era otra cosa: lanzada desde el Bash de un agente, la
suite tiene de stdin un pipe que no se cierra; el test lanzaba el hook sin `input`, el hook heredaba
ese stdin y su `cat` esperaba para siempre. Reproducido: con un pipe abierto, 0/4 a carga 13; con
/dev/null, 4/4 en 0,3 s. Misma clase que test_stdin_canalizado.py (22-sep), en otro sitio.

Fija: una batería que lee stdin hasta EOF termina al momento aunque la suite reciba un pipe abierto.
"""
import os
import re
import subprocess
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.join(RAIZ, "tests", "test_all.sh")
fallos = []


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


lineas = open(SUITE, encoding="utf-8").read().splitlines()
fin = next(i for i, l in enumerate(lineas) if re.match(r"^run\s+test_", l))
root = tempfile.mkdtemp(prefix="stdin_cerrado_")
os.makedirs(os.path.join(root, "tests"))
with open(os.path.join(root, "tests", "lee_stdin.py"), "w") as f:
    f.write("import sys\nsys.stdin.read()\nprint('leyó hasta EOF')\n")
script = "\n".join(lineas[:fin]) + '\nROOT=%s\nrunpy lee_stdin.py\necho "FAIL=$fail"\n' % root
env = dict(os.environ, BTP_ROJO_DIR=os.path.join(root, "rojos"))
env.pop("CI", None)

print("una batería que lee stdin, con la suite recibiendo un pipe que no se cierra")
t0 = time.monotonic()
p = subprocess.Popen(["bash", "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT, text=True, env=env)
try:
    # Nada de communicate(): CERRARÍA el stdin, que es justo lo que no hay que hacer.
    p.wait(timeout=20)
    out = p.stdout.read()
    dur = time.monotonic() - t0
except subprocess.TimeoutExpired:
    p.kill()
    p.wait()
    out, dur = "", None
finally:
    p.stdin.close()

check(dur is not None, "termina sin esperar al stdin (%s)" % ("%.1f s" % dur if dur else "colgado 20 s"))
check("leyó hasta EOF" in (out or "") and "FAIL=0" in (out or ""), "la batería ve EOF y sale en verde")
check(any(l.strip() == "exec </dev/null" for l in lineas[:fin]),
      "test_all.sh cierra stdin antes de la primera batería")

subprocess.run(["rm", "-r", root], capture_output=True)
if fallos:
    print("\n🔴 %d fallo(s)" % len(fallos))
    sys.exit(1)
print("\n✅ la suite corre con stdin cerrado: en verde")
