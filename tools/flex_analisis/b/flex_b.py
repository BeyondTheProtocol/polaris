#!/usr/bin/env python
"""Flex Polaris, analista B (fase 5, 16-oct-2026). Implementación independiente de la
especificación congelada v1 (`docs/flex/ESPEC_CONGELADA_v1.md`, sha256 6165b9ef…).

Corre SOLO por la ventanilla:
    python3 tools/lector_clinico.py procesa flex_b -- <orden> [opciones]

Órdenes (en el orden de la especificación):
    explorar     inventario de la zona clínica del Flex (solo nombres y tamaños)
    qc           paso 1 + 2 + 3: QC por MAD, Scrublet, control de tejido, ARN ambiental
    anotar       paso 4: Leiden + marcadores canónicos
    cnv          paso 5: inferCNV (infercnvpy) + segundo método, consenso, identidad epitelial
    medidas      tablas A-D (fracción ≥1 UMI, IC95 bootstrap, media CP10k entre positivas, n)
    secundarias  microambiente, eje ER/NE, RB1/CDKN2A/CCNE1/E2F1 en no ciclantes
    congelar     capa E: h5ad congelado con malignas etiquetadas + sha256

Muro: stdout solo agregados. Nada por célula, ningún barcode, ningún identificador de bloque
(las muestras se llaman «hígado» y «hueso»). Egress 0. Escritura solo en `analisis_B/`.
Semilla única: 20261016. NUMBA_NUM_THREADS=1.
"""
import os
import sys

# -I: sin site del usuario ni PYTHONPATH ni cwd en sys.path. Si no venimos así, re-exec.
if not sys.flags.isolated:
    os.execv(sys.executable, [sys.executable, "-I", os.path.abspath(__file__)] + sys.argv[1:])

os.environ.setdefault("NUMBA_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("MPLBACKEND", "Agg")

import argparse  # noqa: E402
import hashlib  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ_REPO = os.path.dirname(os.path.dirname(os.path.dirname(AQUI)))
HOME = os.path.expanduser("~")
SEMILLA = 20261016
FLEX_SHA12 = "931c64af777e"
RAW = os.path.join(HOME, "Clinico-PRIVADO", "flex", FLEX_SHA12)
OUT = os.path.join(HOME, "Clinico-PRIVADO", "flex", "analisis_B")
# Nombres de muestra que SÍ pueden salir por stdout; los identificadores de bloque, nunca.
MUESTRAS = {"higado": "26-28381_M1", "hueso": "B26-22813_M2"}


def _politica():
    for ruta in (os.path.join(RAIZ_REPO, ".claude", "hooks", "zonas_clinicas.py"),
                 os.path.join(HOME, "claudecode", ".claude", "hooks", "zonas_clinicas.py")):
        if os.path.exists(ruta):
            spec = importlib.util.spec_from_file_location("zonas_clinicas", ruta)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise RuntimeError("no encuentro zonas_clinicas.py")


def preparar():
    """Fail-closed: solo por ventanilla, y entrada y salida tienen que ser zona clínica."""
    if os.environ.get("BTP_VENTANILLA") != "1":
        print("RECHAZADO: flex_b solo corre por la ventanilla "
              "(python3 tools/lector_clinico.py procesa flex_b -- …).", file=sys.stderr)
        sys.exit(2)
    zc = _politica()
    if not zc.es_ruta_clinica(OUT) or not zc.es_ruta_clinica(RAW):
        print("RECHAZADO: la salida o la entrada no son zona clínica.", file=sys.stderr)
        sys.exit(2)
    os.makedirs(OUT, exist_ok=True)
    return zc


def sha256(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def muro_texto(s):
    """Última red: ningún identificador de bloque en lo que sale por stdout."""
    for bloque in MUESTRAS.values():
        for trozo in (bloque, bloque.split("_")[0], bloque.split("_")[0].lstrip("B")):
            if trozo and trozo in s:
                raise RuntimeError("MURO: un identificador de bloque iba a salir por stdout")
    return s


def imprime(obj):
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=1, default=str)
    print(muro_texto(s), flush=True)


def guardar_json(nombre, obj):
    ruta = os.path.join(OUT, nombre)
    with open(ruta, "w") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1, default=str)
    return ruta, sha256(ruta)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("orden", choices=["explorar", "qc", "anotar", "cnv", "medidas",
                                     "secundarias", "congelar", "resumen", "diagnostico", "diagcnv"])
    p.add_argument("--muestra", choices=sorted(MUESTRAS), default=None)
    p.add_argument("--extra", nargs="*", default=[])
    a = p.parse_args(argv)
    preparar()
    sys.path.insert(0, AQUI)
    import pasos  # noqa: E402  (módulo hermano, mismo directorio)
    fn = getattr(pasos, a.orden)
    return fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
