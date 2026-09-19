#!/usr/bin/env python3
"""test_pipeline_datos_reales.py — engancha a test_all.sh la validación del pipeline de
neoantígenos contra un caso humano REAL y PÚBLICO (dataset abierto de Sid {{CONTACTO}},
osteosarcoma, CC0). Nada de {{TITULAR}}.

Es un shim: el test de verdad vive en `pipeline/bin/test_datos_reales.py` y necesita el
intérprete del pipeline (pysam + MHCflurry). Aquí solo se decide si se puede correr.

SALTA (rc=77, la convención de skip de test_all.sh) si falta el venv del pipeline o los
datos en disco — no obliga a nadie a descargar 180 MB para tener el tablero en verde.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = "/Users/polaris/claudecode/.venv-pipeline/bin/python"
TEST = os.path.join(ROOT, "pipeline", "bin", "test_datos_reales.py")
DATOS = "/Users/polaris/claudecode/_cajita/datos_publicos/sid_osteosarc/preparado"

if not os.path.exists(PY) or not os.path.isdir(DATOS):
    print("SALTADO: falta .venv-pipeline o los datos públicos de Sid "
          "(_cajita/datos_publicos/sid_osteosarc/README.md)")
    sys.exit(77)

r = subprocess.run([PY, TEST], capture_output=True, text=True)
salida = (r.stdout or "") + (r.stderr or "")
print("\n".join(ln for ln in salida.splitlines()
                if "warning" not in ln.lower() and "tensorflow" not in ln.lower()))
sys.exit(r.returncode)
