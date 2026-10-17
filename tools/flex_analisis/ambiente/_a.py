"""Ambiente del Flex (REGLAS_AMBIENTE_v1.json): constantes y utilidades comunes.

Reutiliza el cierre (_c, _final) y el tribunal (_comun). Salidas en ~/Clinico-PRIVADO/flex/ambiente/
(o $AMBIENTE_OUT). Cachés de entrada: las del cierre (mismas células, mismo orden). stdout: solo agregados.
"""
import os
import sys

for _v in ("NUMBA_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

AQUI = os.path.dirname(os.path.abspath(__file__))
for _d in ("cierre", "tribunal"):
    _p = os.path.join(AQUI, "..", _d)
    if _p not in sys.path:
        sys.path.append(_p)
import _c  # noqa: E402
from _c import TC  # noqa: E402

SEED = _c.SEED
FLEX = TC.FLEX
AMB = os.path.join(FLEX, "ambiente")
CACHE = os.path.join(AMB, "cache")
REGLAS = os.path.join(AQUI, "REGLAS_AMBIENTE_v1.json")
ENVS = os.path.expanduser("~/.claude-science/conda/envs")


def out_dir():
    d = os.environ.get("AMBIENTE_OUT") or AMB
    os.makedirs(d, exist_ok=True)
    return d


def outp(n):
    return os.path.join(out_dir(), n)


def cachep(n):
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, n)
