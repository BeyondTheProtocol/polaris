#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prueba `tools/mutantes.py` — el que comprueba que los tests protegen lo que dicen.

Un comprobador de tests que estuviera mal es peor que no tenerlo: diría «todo cazado» sobre
defensas sin cobertura. Así que aquí se monta un objetivo de juguete con DOS líneas —una que
el test de juguete sí vigila y otra que no— y se comprueba que la herramienta distingue.

Cuatro cosas que tiene que hacer bien, y las cuatro nacen de un fallo real del 20-sep-2026:
  1. cazar el mutante que mata una defensa vigilada;
  2. DECIR que sobrevive el que toca algo sin cobertura (no callárselo);
  3. plantarse si el trozo a mutar no aparece exactamente una vez (mutación no-op silenciosa);
  4. plantarse si el test ya estaba rojo antes de mutar nada (entonces no mide nada).

Todo con ficheros de juguete en un tmp. Cero repo, cero clínico.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import shutil

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERR = os.path.join(RAIZ, "tools", "mutantes.py")

fallos = []
total = [0]


def check(cond, desc):
    total[0] += 1
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


# ── el objetivo de juguete: dos «defensas», una vigilada y otra no ────────────────────
OBJETIVO = '''# -*- coding: utf-8 -*-
import io, os, sys

def sirve(ruta, salida):
    if not os.path.basename(ruta).startswith("ok-"):
        return 1                      # DEFENSA VIGILADA por el test de juguete
    if len(ruta) > 4096:
        return 1                      # defensa SIN cobertura: el test nunca prueba esto
    salida.write(io.open(ruta, encoding="utf-8").read())
    return 0

if __name__ == "__main__":
    sys.exit(sirve(sys.argv[1], sys.stdout))
'''

# El test de juguete carga por BTP_JUGUETE lo que le digan (como BTP_LECTOR en el de verdad).
TEST = '''# -*- coding: utf-8 -*-
import os, subprocess, sys, tempfile, io
OBJ = os.environ["BTP_JUGUETE"]
d = tempfile.mkdtemp()
bueno = os.path.join(d, "ok-si.txt"); io.open(bueno, "w").write("hola")
malo = os.path.join(d, "no-vale.txt"); io.open(malo, "w").write("secreto")
def corre(p):
    r = subprocess.run([sys.executable, OBJ, p], capture_output=True, text=True)
    return r.returncode, r.stdout
rc1, out1 = corre(bueno)
rc2, out2 = corre(malo)
ok = (rc1 == 0 and "hola" in out1) and (rc2 != 0 and "secreto" not in out2)
print("verde" if ok else "ROJO")
sys.exit(0 if ok else 1)
'''

tmp = tempfile.mkdtemp(prefix="mutantes-test-")
try:
    os.makedirs(os.path.join(tmp, "tools"))
    os.makedirs(os.path.join(tmp, "tests"))
    obj_rel = os.path.join("tools", "juguete.py")
    with io.open(os.path.join(tmp, obj_rel), "w", encoding="utf-8") as f:
        f.write(OBJETIVO)
    test_rel = os.path.join("tests", "test_juguete.py")
    with io.open(os.path.join(tmp, test_rel), "w", encoding="utf-8") as f:
        f.write(TEST)

    def campana(mutaciones, nombre="c.json"):
        ruta = os.path.join(tmp, "tests", nombre)
        with io.open(ruta, "w", encoding="utf-8") as f:
            json.dump({"objetivo": obj_rel,
                       "test": [sys.executable, test_rel],
                       "env": "BTP_JUGUETE",
                       "mutaciones": mutaciones}, f)
        return ruta

    def corre(ruta_campana):
        p = subprocess.run([sys.executable, HERR, ruta_campana], capture_output=True,
                           text=True, cwd=tmp, timeout=300,
                           env=dict(os.environ, BTP_MUTANTES_RAIZ=tmp))
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    VIGILADA = '    if not os.path.basename(ruta).startswith("ok-"):\n        return 1'
    SIN_COBERTURA = "    if len(ruta) > 4096:\n        return 1"

    # 1) La defensa vigilada: al quitarla el test cae → el mutante se caza.
    rc, out = corre(campana([{"etiqueta": "quita la defensa vigilada",
                              "viejo": VIGILADA, "nuevo": "    if False:\n        return 1"}]))
    check(rc == 0 and "1/1 mutantes cazados" in out,
          "caza el mutante que mata una defensa que el test sí vigila")

    # 2) Lo que NADIE prueba tiene que salir como SUPERVIVIENTE, no colarse en verde.
    rc, out = corre(campana([{"etiqueta": "quita lo que nadie prueba",
                              "viejo": SIN_COBERTURA, "nuevo": "    if False:\n        return 1"}],
                            "c2.json"))
    check(rc != 0 and "SOBREVIVEN" in out and "nadie prueba" in out,
          "dice que SOBREVIVE el mutante sin cobertura, y lo nombra")

    # 3) Un trozo que no aparece: la mutación sería un no-op silencioso.
    rc, out = corre(campana([{"etiqueta": "trozo inexistente",
                              "viejo": "esto no está en el fichero", "nuevo": ""}], "c3.json"))
    check(rc != 0 and "aparece 0 veces" in out,
          "se planta si el trozo a mutar no aparece exactamente una vez")

    # 4) Con el test ya roto, la campaña no mide nada y tiene que decirlo.
    #    Se rompe el objetivo POR DENTRO (deja de escribir la salida), no añadiendo una línea
    #    al final: lo de añadir al final no rompía nada, porque el `sys.exit()` del bloque
    #    `__main__` ya había salido antes de llegar. El primer intento de este test pasaba
    #    por eso, y era otro check vacuo — el tercero del día.
    with io.open(os.path.join(tmp, obj_rel), "w", encoding="utf-8") as f:
        f.write(OBJETIVO.replace('salida.write(io.open(ruta, encoding="utf-8").read())',
                                 "pass  # roto a propósito"))
    rc, out = corre(campana([{"etiqueta": "da igual", "viejo": VIGILADA, "nuevo": VIGILADA}],
                            "c4.json"))
    check(rc != 0 and "ya está ROJO sin mutar" in out,
          "aborta si el test estaba rojo ANTES de mutar (si no, no mide nada)")

    # 5) No deja mutantes tirados al lado del original.
    sobras = [x for x in os.listdir(os.path.join(tmp, "tools")) if x.startswith("_mutante_")]
    check(not sobras, "no deja ficheros de mutante sin borrar (%s)" % (sobras or "ninguno"))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("%s %d/%d" % ("❌ FALLOS:" if fallos else "✅ MUTANTES: la herramienta distingue",
                    total[0] - len(fallos), total[0]))
sys.exit(1 if fallos else 0)
