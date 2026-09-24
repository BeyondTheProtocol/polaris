#!/usr/bin/env python3
"""Los niveles vertebrales del esqueleto: que salgan los 24, en orden, y dentro del hueso.

Por qué este test y no otro. El 20-sep-2026 TotalSegmentator nombró la columna de {{TITULAR}} al
revés y a trozos: de arriba abajo salía C7 → S1 → C6 → C5 → C4 → C3 → C2 → T11… y la misma
etiqueta aparecía en dos vértebras distintas. El render se veía PERFECTO igualmente. Ese es
todo el problema: un foco que dice «D11» colocado en la vértebra de al lado no se detecta
mirando, solo comprobando que la cadena salga monótona.

Así que aquí se prueban los dos frenos, no el resultado bonito:
  · `_orden_columna` tiene que ABORTAR si la cadena se desordena;
  · `niveles_por_reparto` tiene que dar los 24 niveles, todos dentro del hueso, y no dar
    ninguno si algo no cuadra.
"""
import os
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"),
                    ".venv-imagen", "bin", "python")

# el mismo arranque que test_visor3d.py: numpy y scipy viven en la venv de imagen, no en el
# python del sistema con el que la batería lanza los tests
try:
    import numpy as np
except ImportError:
    if os.path.exists(VENV) and os.environ.get("_ESQ_REEXEC") != "1":
        sys.exit(subprocess.call([VENV, os.path.abspath(__file__)],
                                 env=dict(os.environ, _ESQ_REEXEC="1")))
    if os.environ.get("BTP_PORTABLE"):
        print("SKIP: sin .venv-imagen (modo portátil)")
        sys.exit(77)
    print("ROJO: falta .venv-imagen; el freno de los niveles vertebrales no puede correr")
    sys.exit(1)

sys.path.insert(0, os.path.join(RAIZ, "tools"))
os.environ.setdefault("BTP_REPO", RAIZ)
import visor3d as V  # noqa: E402

fallos = []


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


def columna_falsa(n_cuerpos=24, alto=10, lado=12):
    """Una columna de juguete: n cuerpos apilados, con disco entre medias."""
    nz = n_cuerpos * (alto + 2)
    vol = np.zeros((40, 40, nz), bool)
    for k in range(n_cuerpos):
        z0 = k * (alto + 2)
        vol[20 - lado // 2:20 + lado // 2, 20 - lado // 2:20 + lado // 2, z0:z0 + alto] = True
    return vol


# 1) el freno del orden: una cadena monótona pasa, una barajada aborta
print("\n— el orden de la columna —")
buena = {n: [0.0, 0.0, 1000.0 - i * 10] for i, n in enumerate(V.CADENA)}
try:
    V._orden_columna(buena)
    check(True, "una columna en orden no aborta")
except SystemExit as e:
    check(False, "una columna en orden no aborta (abortó: %s)" % e)

mala = dict(buena)
mala["vertebrae_T5"], mala["vertebrae_T6"] = mala["vertebrae_T6"], mala["vertebrae_T5"]
try:
    V._orden_columna(mala)
    check(False, "dos niveles intercambiados ABORTAN")
except SystemExit as e:
    check("no sale en orden" in str(e), "dos niveles intercambiados ABORTAN")

# el caso real que se coló: cada grupo ordenado por dentro pero los grupos barajados
barajada = {}
for i, n in enumerate(V.CADENA):
    grupo = 0 if n.startswith("vertebrae_C") else (1 if "_T" in n else 2)
    barajada[n] = [0.0, 0.0, [1000, 3000, 2000][grupo] - i * 10]
try:
    V._orden_columna(barajada)
    check(False, "la columna por grupos barajados (el fallo real) ABORTA")
except SystemExit:
    check(True, "la columna por grupos barajados (el fallo real) ABORTA")

# 2) el reparto: 24 niveles, todos dentro del hueso y en orden
print("\n— el reparto de los 24 niveles —")
vol = columna_falsa()
nombres = {1: "vertebrae_C1", 2: "skull", 3: "sacrum"}
lab = np.zeros(vol.shape, np.int16)
lab[vol] = 1
lab[18:22, 18:22, :6] = 3        # un "sacro" abajo
lab[16:24, 16:24, -6:] = 2       # un "cráneo" arriba
afin = np.eye(4)
centros = V.niveles_por_reparto(lab, nombres, afin)
check(len(centros) == len(V.CADENA) - 1,
      "salen los 24 niveles de C1 a L5 (%d)" % len(centros))
z = [centros[n][2] for n in V.CADENA[:-1] if n in centros]
check(all(a < b for a, b in zip(z, z[1:])) or all(a > b for a, b in zip(z, z[1:])),
      "y en orden monótono")
dentro = sum(1 for n, c in centros.items() if vol[:, :, int(round(c[2]))].any())
check(dentro == len(centros), "todos caen en un corte CON hueso (%d de %d)" % (dentro, len(centros)))

# 3) sin sacro no se reparte nada: mejor ningún nivel que niveles corridos
print("\n— sin puntos de anclaje —")
lab2 = np.zeros(vol.shape, np.int16)
lab2[vol] = 1
check(V.niveles_por_reparto(lab2, {1: "vertebrae_C1"}, afin) == {},
      "sin cráneo ni sacro NO devuelve niveles")

print("\nVEREDICTO: %s" % ("TODO CORRECTO" if not fallos else "%d FALLOS" % len(fallos)))
sys.exit(1 if fallos else 0)
