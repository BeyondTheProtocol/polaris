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

# 2b) el fallo del 26-sep-2026 (PR #216 de la web): con S1 segmentada, el tramo del reparto
#     llegaba hasta el FONDO de S1, toda la cadena salía estirada hacia abajo y L5 caía encima
#     de S1 (en su TC, S1 medida v=0,7345 por encima de L5 estimada v=0,7556). Aquí el cuerpo
#     más bajo es S1: L5 tiene que caer en el cuerpo de encima, no en S1.
print("\n— S1 segmentada no estira el reparto —")
nombres_s1 = {1: "vertebrae_C1", 2: "skull", 3: "sacrum", 4: "vertebrae_S1"}
# orientación del TC real: z bajo = craneal (C1 sale en `lo`), así que S1 es el cuerpo de z alto
nz = vol.shape[2]
lab3 = np.zeros(vol.shape, np.int16)
lab3[vol] = 1
s1 = np.zeros(vol.shape, bool)
s1[:, :, nz - 12:] = vol[:, :, nz - 12:]  # el último cuerpo (z 276..285) es S1
lab3[s1] = 4
lab3[16:24, 16:24, :6] = 2               # "cráneo" arriba (z bajo)
lab3[18:22, 18:22, -6:] = 3              # "sacro" abajo (z alto)
c3 = V.niveles_por_reparto(lab3, nombres_s1, afin)
z_l5 = c3.get("vertebrae_L5", [0, 0, -1])[2]
z_s1 = float(np.nonzero(s1.any(axis=(0, 1)))[0].mean())
check(264 <= z_l5 <= 273, "L5 cae en el cuerpo de encima de S1 (z=%.1f, ese cuerpo = 264..273)" % z_l5)
check(z_l5 < z_s1, "L5 estimada queda por encima de S1 medida (%.1f < %.1f)" % (z_l5, z_s1))
check(len(c3) == len(V.CADENA) - 1, "y siguen saliendo los 24 niveles de C1 a L5 (%d)" % len(c3))

# 2c) el hueco de T8 (26-sep-2026): un nivel cuyo corte cae en un hueco de la segmentación no
#     tumba el reparto entero si hay hueso DENTRO de su propio tramo; se usa el corte con hueso
#     más cercano. Si el hueco es mayor que medio tramo, el freno sigue parando todo.
print("\n— un hueco pequeño no tumba el reparto —")
lab4 = lab.copy()
lo4, hi4 = 0, int(np.nonzero(vol.any(axis=(0, 1)))[0].max())
z10 = int(round(lo4 + (hi4 - lo4) * 10.5 / 24))       # el nivel k=10 (T4)
lab4[:, :, z10 - 4:z10 + 5][lab4[:, :, z10 - 4:z10 + 5] == 1] = 0
c4 = V.niveles_por_reparto(lab4, nombres, afin)
check(len(c4) == len(V.CADENA) - 1, "un hueco de 9 cortes deja los 24 niveles (%d)" % len(c4))
zt4 = c4.get("vertebrae_T4", [0, 0, -99])[2]
medio = (hi4 - lo4) / 24 / 2
check(abs(zt4 - z10) <= medio and (lab4[:, :, int(round(zt4))] == 1).any(),
      "T4 va al hueso más cercano, dentro de su tramo (z=%.1f, ideal %d ± %.1f)" % (zt4, z10, medio))
z4 = [c4[n][2] for n in V.CADENA[:-1] if n in c4]
check(all(a < b for a, b in zip(z4, z4[1:])), "y la cadena sigue en orden")
lab5 = lab.copy()
lab5[:, :, z10 - 20:z10 + 21][lab5[:, :, z10 - 20:z10 + 21] == 1] = 0
check(V.niveles_por_reparto(lab5, nombres, afin) == {},
      "un hueco mayor que medio tramo SIGUE parando el reparto")

# 3) sin sacro no se reparte nada: mejor ningún nivel que niveles corridos
print("\n— sin puntos de anclaje —")
lab2 = np.zeros(vol.shape, np.int16)
lab2[vol] = 1
check(V.niveles_por_reparto(lab2, {1: "vertebrae_C1"}, afin) == {},
      "sin cráneo ni sacro NO devuelve niveles")

print("\nVEREDICTO: %s" % ("TODO CORRECTO" if not fallos else "%d FALLOS" % len(fallos)))
sys.exit(1 if fallos else 0)
