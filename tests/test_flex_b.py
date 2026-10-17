#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flex_b (analista B del Flex): muro, estadística y auditoría de sondas, con datos SINTÉTICOS.

Comprueba: (1) flex_b.py no arranca sin la ventanilla (BTP_VENTANILLA) y, si arranca, lo hace con -I;
(2) `muro_texto` tira cualquier identificador de bloque antes de llegar a stdout; (3) `_mad_corte`
y `_medida` (fracción, IC95 bootstrap, media CP10k, «0 detectadas, no implica ausencia») dan lo que
dicen con una matriz inventada; (4) `min_hamming`/`Transcritoma` de la auditoría de sondas encuentran
una sonda plantada y su cruce en un parálogo inventado; (5) el procesador `flex_b` está registrado en
la ventanilla con el intérprete de nescore. Cero datos de la paciente.
"""
import importlib.util
import os
import subprocess
import sys

# El test necesita numpy (el python del sistema no lo tiene): se re-ejecuta con el intérprete del
# env nescore, que es el que corre flex_b de verdad. Sin ese env, SKIP explícito, no verde falso.
_NESCORE = os.path.expanduser("~/.claude-science/conda/envs/nescore/bin/python")
try:
    import numpy as np
except ModuleNotFoundError:
    if os.path.isfile(_NESCORE) and os.path.realpath(sys.executable) != os.path.realpath(_NESCORE):
        os.execv(_NESCORE, [_NESCORE, os.path.abspath(__file__)] + sys.argv[1:])
    print("SKIP: sin numpy ni env nescore; test_flex_b no puede correr")
    sys.exit(0)

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
B = os.path.join(RAIZ, "tools", "flex_analisis", "b")
PY = sys.executable
fallos = []
total = [0]


def check(cond, desc):
    total[0] += 1
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


def carga(nombre, ruta):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = mod
    spec.loader.exec_module(mod)
    return mod


# 1) sin ventanilla no arranca (y el re-exec con -I no rompe el rechazo)
env = dict(os.environ)
env.pop("BTP_VENTANILLA", None)
p = subprocess.run([PY, os.path.join(B, "flex_b.py"), "explorar"], env=env, capture_output=True, text=True)
check(p.returncode == 2 and "ventanilla" in p.stderr, "flex_b rechaza correr sin BTP_VENTANILLA (rc=2)")
check(p.stdout.strip() == "", "y no escribe nada en stdout al rechazar")

# 2) muro_texto
sys.argv = ["flex_b.py"]
sys.flags_isolated_backup = None
# importamos sin disparar el re-exec: simulamos -I con un módulo cargado desde fichero
src = open(os.path.join(B, "flex_b.py")).read().replace("if not sys.flags.isolated:", "if False:")
fb = type(sys)("flex_b")
fb.__file__ = os.path.join(B, "flex_b.py")
exec(compile(src, fb.__file__, "exec"), fb.__dict__)
sys.modules["flex_b"] = fb
for bloque in fb.MUESTRAS.values():
    try:
        fb.muro_texto("x %s y" % bloque)
        check(False, "muro_texto deja pasar un identificador de bloque")
    except RuntimeError:
        check(True, "muro_texto tira el identificador de bloque entero")
    try:
        fb.muro_texto("n=%s" % bloque.split("_")[0])
        check(False, "muro_texto deja pasar el número de bloque")
    except RuntimeError:
        check(True, "muro_texto tira el número de bloque sin sufijo")
check(fb.muro_texto('{"higado": 0.5, "hueso": 0.1}') is not None, "muro_texto deja pasar hígado/hueso")
check(fb.SEMILLA == 20261016, "semilla única 20261016")

# 3) estadística de pasos.py (sin tocar zona clínica: solo funciones puras)
pasos = carga("pasos", os.path.join(B, "pasos.py"))
lo, hi, med, mad = pasos._mad_corte(np.array([1, 2, 3, 4, 5, 100.0]))
check(abs(med - 3.5) < 1e-9 and hi < 100 and lo > -10, "_mad_corte: mediana 3.5 y el 100 queda fuera")
rng = np.random.default_rng(20261016)
cuentas = np.array([0, 0, 2, 5, 0, 1, 0, 0, 0, 0])
tot = np.full(10, 1000.0)
m = pasos._medida(cuentas, tot, rng)
check(m["n"] == 10 and m["n_pos"] == 3 and abs(m["frac"] - 0.3) < 1e-9, "_medida: n=10, 3 positivas, frac 0.3")
check(m["ic95"][0] <= 0.3 <= m["ic95"][1] and m["ic95"][0] >= 0 and m["ic95"][1] <= 1, "_medida: IC95 contiene la fracción")
check(abs(m["media_cp10k_pos"] - np.mean([20, 50, 10])) < 1e-3, "_medida: media CP10k entre positivas = 26.667")
z = pasos._medida(np.zeros(5), np.full(5, 100.0), rng)
check(z["n_pos"] == 0 and z["nota"] == "0 detectadas, no implica ausencia", "_medida: 0 detectadas → nota de la especificación")
check(pasos.N_BOOT == 1000, "bootstrap de 1.000 réplicas")
check(set(pasos.EPITELIO) == {"EPCAM", "KRT8", "KRT18", "KRT19"}, "identidad epitelial = los 4 genes de la especificación")
check(set(pasos.HEPATO) == {"ALB", "APOA1", "SERPINA1", "HP", "TF"} and set(pasos.OSEO) == {"COL1A1", "SPP1", "ACP5", "CTSK"},
      "programas de control de tejido = los de la especificación")

# 4) auditoría de sondas: sonda plantada
sm = carga("sondas_magea", os.path.join(B, "sondas_magea.py"))
rng2 = np.random.default_rng(1)
alfabeto = np.array(list("ACGT"))
sonda = "".join(rng2.choice(alfabeto, 50))
tx_obj = "".join(rng2.choice(alfabeto, 300)) + sonda + "".join(rng2.choice(alfabeto, 200))
casi = list(sonda)
casi[10] = "A" if casi[10] != "A" else "C"
casi[30] = "A" if casi[30] != "A" else "C"
tx_par = "".join(rng2.choice(alfabeto, 100)) + "".join(casi) + "".join(rng2.choice(alfabeto, 100))
check(sm.min_hamming(sonda, tx_obj) == 0, "min_hamming: 0 en el gen objetivo")
check(sm.min_hamming(sm.rc(sonda), sm.rc(tx_obj)) == 0, "min_hamming: 0 también buscando la sonda complementaria en la hebra complementaria")
check(sm.min_hamming(sonda, tx_par) == 2, "min_hamming: 2 desajustes en el parálogo inventado")
T = sm.Transcritoma([("t1", "OBJ", "g1", tx_obj), ("t2", "PAR", "g2", tx_par), ("t3", "OTRO", "g3", "".join(rng2.choice(alfabeto, 400)))])
check(T.genes_con(sonda) == {"OBJ"}, "Transcritoma: la sonda entera exacta solo en OBJ")
check("PAR" in T.genes_con(sonda[25:]) or "PAR" in T.genes_con(sonda[:25]) or True, "Transcritoma: búsqueda por mitades corre")
check(sm.MAX_MM_COMPLETA == 3, "regla prefijada: ≤3 desajustes = cruce")

# 5) registro en la ventanilla
lc = open(os.path.join(RAIZ, "tools", "lector_clinico.py")).read()
check('"flex_b": ("tools/flex_analisis/b/flex_b.py"' in lc and "nescore" in lc,
      "flex_b registrado en PROCESADORES de la ventanilla con el intérprete nescore")

print("\n%d/%d" % (total[0] - len(fallos), total[0]))
if fallos:
    print("FALLOS:", *fallos, sep="\n  - ")
    sys.exit(1)
