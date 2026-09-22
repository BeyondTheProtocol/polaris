#!/usr/bin/env python3
"""test_pipeline_alelos_muestra.py — engancha a test_all.sh los arreglos de la auditoría
externa del 22-sep-26 (hallazgos 2.1, 2.2, 2.3) sobre el pipeline de neoantígenos:
alelo a alelo sin CSQ cruzada, muestra tumoral fail-closed y config validada.

Shim: el test de verdad vive en `pipeline/bin/test_alelos_muestra.py` y necesita el
intérprete del pipeline (pysam, PyYAML). Todo con VCF SINTÉTICOS, nada de datos reales.
SALTA (rc=77) si falta el venv del pipeline.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = "/Users/polaris/claudecode/.venv-pipeline/bin/python"
TEST = os.path.join(ROOT, "pipeline", "bin", "test_alelos_muestra.py")

if not os.path.exists(PY):
    print("SALTADO: falta .venv-pipeline (pysam)")
    sys.exit(77)

r = subprocess.run([PY, TEST], capture_output=True, text=True)
salida = (r.stdout or "") + (r.stderr or "")
print("\n".join(ln for ln in salida.splitlines() if "warning" not in ln.lower()))
sys.exit(r.returncode)
