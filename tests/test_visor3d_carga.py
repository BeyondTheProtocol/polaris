#!/usr/bin/env python3
"""Carga tumoral hepática del visor (tools/visor3d_web/carga.js) con números SINTÉTICOS.

Se congela: (1) es tumor/hígado en %; (2) sin volumen hepático válido NO se inventa un número
(devuelve null y el visor pinta «—»); (3) main.js la usa de verdad en el resumen.
"""
import json
import os
import shutil
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(RAIZ, "tools", "visor3d_web")
node = shutil.which("node")
if not node:
    print("SKIP: sin node")
    sys.exit(77)

js = """
import { fraccionHepaticaPct as f } from %s
console.log(JSON.stringify([f(15, 1500), f(0, 1500), f(15, 0), f(null, 1500), f(15, null),
                            f(-1, 1500), f('15', '1500'), f(NaN, 1500)]))
""" % json.dumps("file://" + os.path.join(WEB, "carga.js"))
r = subprocess.run([node, "--input-type=module", "-e", js], capture_output=True, text=True)
fallos = []
if r.returncode:
    fallos.append("node falló: " + r.stderr)
else:
    v = json.loads(r.stdout)
    esperado = [1.0, 0.0, None, None, None, None, 1.0, None]
    if v != esperado:
        fallos.append("fracción: %s, esperado %s" % (v, esperado))

main = open(os.path.join(WEB, "main.js")).read()
if "from './carga.js'" not in main or "fraccionHepaticaPct(e.volumen_tumoral_ml, e.volumen_higado_ml)" not in main:
    fallos.append("main.js no pinta la fracción en el resumen")
if "no pronóstico" not in main:
    fallos.append("la fracción debe llevar la etiqueta «exploratorio, no pronóstico»")

if fallos:
    print("ROJO:\n  " + "\n  ".join(fallos))
    sys.exit(1)
print("OK: carga tumoral hepática (fracción, nulos, cableado en el resumen)")
