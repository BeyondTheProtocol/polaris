"""Hígado célula a célula, fase 2 (REGLAS_HIGADO_CELULA_v1.json): constantes, muro y estadística comunes.

Sin dependencias pesadas: lo importan el proceso nescore (higado_celula.py) y el de REFCON (refcon_run.py).
Salidas en ~/Clinico-PRIVADO/flex/higado_celula/ (o $HIGADO_CELULA_OUT). Cachés en cache/. stdout: solo agregados.
"""
import hashlib
import json
import math
import os
import sys

for _v in ("NUMBA_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

AQUI = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
FLEX = os.path.join(HOME, "Clinico-PRIVADO", "flex")
BASE = os.path.join(FLEX, "higado_celula")
CACHE = os.path.join(BASE, "cache")
CACHE_CIERRE = os.path.join(FLEX, "cierre", "cache")
REGLAS = os.path.join(AQUI, "REGLAS_HIGADO_CELULA_v1.json")
ENMIENDA = os.path.join(AQUI, "ENMIENDA_1_HIGADO_CELULA.json")
ENMIENDA2 = os.path.join(AQUI, "ENMIENDA_2_HIGADO_CELULA.json")
ENVS = os.path.join(HOME, ".claude-science", "conda", "envs")
RSCRIPT = os.path.join(ENVS, "dobletes", "bin", "Rscript")
REFCON_DIR = os.path.join(HOME, ".claude-science", "refcon", "04d9d24")
SEED = 20261016
Z = 1.959964
PERFIL_SIN_RED = "(version 1)(allow default)(deny network*)"
SANDBOX = "/usr/bin/sandbox-exec"


def out_dir():
    d = os.environ.get("HIGADO_CELULA_OUT") or BASE
    os.makedirs(d, exist_ok=True)
    return d


def outp(*n):
    p = os.path.join(out_dir(), *n)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def cachep(*n):
    p = os.path.join(CACHE, *n)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def cached(*n):
    """Directorio de caché (lo crea)."""
    d = os.path.join(CACHE, *n)
    os.makedirs(d, exist_ok=True)
    return d


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- muro
def _politica():
    for raiz in (os.path.dirname(os.path.dirname(os.path.dirname(AQUI))), os.path.join(HOME, "claudecode")):
        ruta = os.path.join(raiz, ".claude", "hooks", "zonas_clinicas.py")
        if os.path.exists(ruta):
            import importlib.util
            spec = importlib.util.spec_from_file_location("zonas_clinicas", ruta)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise RuntimeError("no encuentro zonas_clinicas.py")


def arranque(script, argv, nombre_proc):
    """Fail-closed: -I, solo por la ventanilla, salida en zona clínica y dentro de un sandbox sin red.

    Orden: 1) re-exec con -I; 2) sin BTP_VENTANILLA=1 no arranca; 3) la salida y las cachés tienen que ser zona
    clínica; 4) si no estamos ya en el sandbox, re-exec dentro de `sandbox-exec (deny network*)`."""
    if not sys.flags.isolated:
        os.execv(sys.executable, [sys.executable, "-I", os.path.abspath(script)] + argv)
    if os.environ.get("BTP_VENTANILLA") != "1":
        print("RECHAZADO: %s solo corre por la ventanilla (python3 tools/lector_clinico.py procesa %s -- …)."
              % (os.path.basename(script), nombre_proc), file=sys.stderr)
        sys.exit(2)
    zc = _politica()
    if not zc.es_ruta_clinica(out_dir()) or not zc.es_ruta_clinica(CACHE) or not zc.es_ruta_clinica(CACHE_CIERRE):
        print("RECHAZADO: la salida o las cachés no son zona clínica.", file=sys.stderr)
        sys.exit(2)
    if os.environ.get("HC_SANDBOX") != "1":
        if not os.path.exists(SANDBOX):
            print("RECHAZADO: sin sandbox-exec no se corre (egress 0 por construcción).", file=sys.stderr)
            sys.exit(2)
        env = dict(os.environ, HC_SANDBOX="1")
        os.execve(SANDBOX, [SANDBOX, "-p", PERFIL_SIN_RED, sys.executable, "-I", os.path.abspath(script)] + argv, env)


# ---------------------------------------------------------------- estadística
def wilson(k, n, z=Z):
    if n == 0:
        return (None, None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    lo, hi = max(0.0, c - h), min(1.0, c + h)
    if k == 0:
        lo = 0.0
    if k == n:
        hi = 1.0
    return (p, lo, hi)


def w(k, n):
    p, lo, hi = wilson(int(k), int(n))
    return {"k": int(k), "n": int(n), "frac": p, "ic95_wilson": [lo, hi]}


def hanley(a, n1, n2):
    """IC95 de un AUC por Hanley-McNeil (1982). n1 = positivos (pool), n2 = negativos."""
    if a is None or n1 == 0 or n2 == 0:
        return [None, None]
    q1 = a / (2 - a)
    q2 = 2 * a * a / (1 + a)
    se = math.sqrt(max(a * (1 - a) + (n1 - 1) * (q1 - a * a) + (n2 - 1) * (q2 - a * a), 0.0) / (n1 * n2))
    return [max(0.0, a - Z * se), min(1.0, a + Z * se)]


def katz(s1, o1, s2, o2):
    """Razón de odds/razones (s1/o1)/(s2/o2) con IC95 de Katz (log); +0,5 a todo si hay ceros."""
    if min(s1, o1, s2, o2) == 0:
        s1, o1, s2, o2 = s1 + 0.5, o1 + 0.5, s2 + 0.5, o2 + 0.5
    rr = (s1 / o1) / (s2 / o2)
    se = math.sqrt(1 / s1 + 1 / o1 + 1 / s2 + 1 / o2)
    return {"razon": rr, "ic95": [rr * math.exp(-Z * se), rr * math.exp(Z * se)],
            "excluye_1": bool(rr * math.exp(-Z * se) > 1 or rr * math.exp(Z * se) < 1)}


def r_(x):
    """Redondeo a 6 cifras significativas para que la serialización sea determinista."""
    import numpy as np
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        x = float(x)
        if math.isnan(x) or math.isinf(x):
            return None
        return float(f"{x:.6g}")
    if isinstance(x, dict):
        return {str(k): r_(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [r_(v) for v in x]
    if isinstance(x, np.ndarray):
        return [r_(v) for v in x.tolist()]
    return x


def guarda_json(obj, *nombre):
    p = outp(*nombre)
    with open(p, "w") as f:
        json.dump(r_(obj), f, indent=1, ensure_ascii=False, sort_keys=True)
    return p


def lee_json(p):
    with open(p) as f:
        return json.load(f)
