#!/usr/bin/env python3
"""test_secretos_largos_paralelo.py — dos test_all.sh a la vez no se pisan en el Llavero.

POR QUÉ EXISTE (25-sep-2026, deuda `test-secretos-largos-flake-concurrencia`, escalada a 3x).
`test_secretos_largos.py` usaba un servicio de nombre fijo. {{TITULAR}} trabaja con muchas sesiones en
paralelo y cada una corre la suite: cuando coincidían, una fallaba con `rc=45` al guardar (el
secreto ya existía) o su `limpia()` borraba el de la otra a medio test. Aquí se lanzan tres a la
vez y tienen que pasar las tres.
"""
import os
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST = os.path.join(RAIZ, "tests", "test_secretos_largos.py")

procs = [subprocess.Popen([sys.executable, TEST], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True) for _ in range(3)]
rcs = []
for p in procs:
    out, _ = p.communicate(timeout=120)
    rcs.append(p.returncode)
    if p.returncode:
        print(out[-800:])

if all(rc == 77 for rc in rcs):
    print("⏭️  SKIP: los tres test_secretos_largos saltan aquí (rc=77)")
    sys.exit(77)

ok = all(rc == 0 for rc in rcs)
print("  %s tres test_secretos_largos a la vez: rc=%s" % ("✅" if ok else "❌", rcs))
print("\nVEREDICTO: %s" % ("TODO CORRECTO" if ok else "FALLOS"))
sys.exit(0 if ok else 1)
