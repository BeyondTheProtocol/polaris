"""tests/_laminillas_piloto_a.py — fixtures SINTÉTICAS del módulo A del piloto (color, segmentación,
congelación). Nada real: ni píxeles, ni nombres, ni accesiones.

Láminas de tanda inventadas, pintadas por Beer-Lambert con vectores H y DAB CONOCIDOS (distintos de
los de Ruifrok, para que el test compruebe que se estiman y no se suponen), núcleos con
concentraciones conocidas y un bloque de relleno 255 fuera de la zona escaneada. `LectorSint` imita
la API del lector único (`tools/laminillas_lector.py`): abre, lee_region (w, h AL mpp pedido),
zona_escaneada, i0_local (f(x, y) → RGB) y manifiesto.

Además, `al_venv()`: los tests del piloto corren con el intérprete del venv `patologia`; sin venv
(CI, BTP_PORTABLE) salen con SKIP (77).
"""
import math
import os
import sys

VENV = os.environ.get("BTP_VENV_PATOLOGIA") or os.path.expanduser("~/.polaris-venvs/patologia")


def al_venv(fichero):
    """Re-ejecuta el test con el python del venv; sin venv, SKIP explícito."""
    if os.path.realpath(sys.prefix) == os.path.realpath(VENV):
        return
    py = os.path.join(VENV, "bin", "python")
    if not os.path.exists(py):
        print("SKIP: sin venv patologia (~/.polaris-venvs/patologia): CI o BTP_PORTABLE")
        sys.exit(77)
    env = dict(os.environ)
    env.setdefault("HF_HOME", os.path.expanduser("~/.polaris-venvs/cache/hf"))
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
    os.execve(py, [py, os.path.abspath(fichero)] + sys.argv[1:], env)


# Importarlo desde un test fuera del venv ya re-ejecuta ese test en el venv (o SKIP 77).
if os.path.realpath(sys.prefix) != os.path.realpath(VENV) and \
        os.path.basename(sys.argv[0]).startswith("test_laminillas_piloto_"):
    al_venv(sys.argv[0])

import numpy as np  # noqa: E402


def unit(v):
    v = np.asarray(v, np.float64)
    return v / np.linalg.norm(v)


# Vectores VERDADEROS de la tanda sintética (≈5° y ≈3° de los de Ruifrok).
H_REAL = unit([0.58, 0.74, 0.34])
D_REAL = unit([0.32, 0.54, 0.78])
I0 = (240.0, 238.0, 242.0)
MPP = 0.5
W, H = 2300, 1200
FRAGMENTOS = [(100, 100, 1100, 1100), (1200, 100, 2200, 1100)]   # px L0 (500 µm de lado)
RELLENO = (2200, 0, 2300, 90)                                     # bloque 255 fuera de la zona


def rota(v, eje, grados):
    """Rodrigues: `v` girado `grados` alrededor de `eje`."""
    v, k = np.asarray(v, float), unit(eje)
    a = math.radians(grados)
    return unit(v * math.cos(a) + np.cross(k, v) * math.sin(a) + k * (k @ v) * (1 - math.cos(a)))


def angulo(a, b):
    a, b = unit(a), unit(b)
    return math.degrees(math.acos(min(1.0, abs(float(a @ b)))))


class LaminaSint:
    def __init__(self, opaco, img, mpp, zona, i0=I0, verdad=None):
        self.opaco = opaco
        self.img = img
        self.mpp_l0 = mpp
        self.dimensiones_l0 = (img.shape[1], img.shape[0])
        self.zona = zona
        self.i0 = i0
        self.verdad = verdad or {}
        self.ruta = None


def _recorte(img, x, y, w, h):
    Hh, Ww = img.shape[:2]
    out = np.full((h, w, 3), 255, np.uint8)
    x0, y0, x1, y1 = max(0, x), max(0, y), min(Ww, x + w), min(Hh, y + h)
    if x1 > x0 and y1 > y0:
        out[y0 - y:y1 - y, x0 - x:x1 - x] = img[y0:y1, x0:x1]
    return out


class LectorSint:
    """API del lector único sobre arrays en memoria."""

    def __init__(self, laminas, manifiesto=None):
        self.laminas = laminas
        self._man = manifiesto or {"laminas": {n: {
            "sha256": "sintetico-%s" % n,
            "prueba_lector": {"a_ok": True, "b_ok": True, "c_ok": True, "b_dif_max": 0,
                              "c_dif_max": 0},
            "i0": {"franja": False}} for n in laminas}}
        self.abiertas = []

    def manifiesto(self):
        return self._man

    def abre(self, opaco):
        self.abiertas.append(opaco)
        return self.laminas[opaco]

    def zona_escaneada(self, lam):
        return lam.zona

    def i0_local(self, lam):
        i0 = np.asarray(lam.i0, np.float32)

        def f(x, y):
            forma = np.broadcast(np.asarray(x), np.asarray(y)).shape
            return np.broadcast_to(i0, forma + (3,)).astype(np.float32)
        return f

    def lee_region(self, lam, mpp, x, y, w, h):
        f = mpp / lam.mpp_l0
        x, y, w, h = int(x), int(y), int(w), int(h)
        if abs(f - 1) < 0.01:
            return _recorte(lam.img, x, y, w, h)
        k = int(round(f))
        if abs(f - k) < 1e-6:
            r = _recorte(lam.img, x, y, w * k, h * k).reshape(h, k, w, k, 3)
            return np.clip(np.rint(r.astype(np.float32).mean(axis=(1, 3))), 0, 255).astype(
                np.uint8)
        from skimage.transform import resize
        r = _recorte(lam.img, x, y, int(math.ceil(w * f)), int(math.ceil(h * f)))
        return np.clip(np.rint(resize(r, (h, w), anti_aliasing=True, preserve_range=True)), 0,
                       255).astype(np.uint8)


def pinta(cH, cD, h=H_REAL, d=D_REAL, i0=I0, ruido=1.5, semilla=0, cX=None, x=None):
    """Beer-Lambert: RGB uint8 = I0·10^(−(cH·H + cD·DAB [+ cX·X])) + ruido gaussiano."""
    rng = np.random.default_rng(semilla)
    od = cH[..., None] * np.asarray(h) + cD[..., None] * np.asarray(d)
    if cX is not None:
        od = od + cX[..., None] * np.asarray(x)
    img = np.asarray(i0) * np.exp(-math.log(10.0) * od) + rng.normal(0, ruido, od.shape)
    return np.clip(np.rint(img), 0, 255).astype(np.uint8)


def nucleos(semilla, mpp=MPP, fragmentos=FRAGMENTOS, paso_um=18.0, r_um=(3.0, 4.0)):
    """Núcleos en rejilla con jitter, sin solaparse: (cx, cy, r) en px de L0 y fragmento."""
    rng = np.random.default_rng(semilla)
    paso = paso_um / mpp
    out = []
    for k, (x0, y0, x1, y1) in enumerate(fragmentos):
        ys = np.arange(y0 + paso / 2, y1 - paso / 2 + 1e-6, paso)
        xs = np.arange(x0 + paso / 2, x1 - paso / 2 + 1e-6, paso)
        for cy in ys:
            for cx in xs:
                j = rng.uniform(-2.5, 2.5, 2) / mpp
                r = rng.uniform(*r_um) / mpp
                out.append((cx + j[0], cy + j[1], r, k))
    return np.asarray(out)


def discos(nuc, forma, valores, campo=None, anillo_px=0.0):
    """Pinta `valores[i]` en el disco i (o en su anillo r..r+anillo_px si anillo_px > 0)."""
    campo = np.zeros(forma, np.float64) if campo is None else campo
    Hh, Ww = forma
    nuc = np.asarray(nuc, np.float64).reshape(-1, 4)
    valores = np.asarray(valores, np.float64).reshape(-1)
    if not len(nuc):
        return campo
    rmax = int(math.ceil(float((nuc[:, 2] + anillo_px).max()))) + 2
    off = np.arange(-rmax, rmax + 1)
    oy, ox = (a.ravel() for a in np.meshgrid(off, off, indexing="ij"))
    for a in range(0, len(nuc), 1000):              # vectorizado por bloques (el bucle tardaba)
        b, v = nuc[a:a + 1000], valores[a:a + 1000]
        cx, cy, r = b[:, :1], b[:, 1:2], b[:, 2:3]
        bx = np.floor(cx).astype(np.int64) + ox[None, :]
        by = np.floor(cy).astype(np.int64) + oy[None, :]
        d2 = (bx + 0.5 - cx) ** 2 + (by + 0.5 - cy) ** 2
        m = d2 <= (r + anillo_px) ** 2
        if anillo_px:
            m &= d2 > r * r
        m &= (bx >= 0) & (bx < Ww) & (by >= 0) & (by < Hh)
        quien = np.broadcast_to(np.arange(len(b))[:, None], m.shape)[m]
        campo[by[m], bx[m]] = v[quien]
    return campo


def mascara_nuclear(nuc, forma):
    return discos(nuc, forma, np.ones(len(nuc))) > 0


def poligonos(nuc):
    from shapely.geometry import Point
    return [Point(float(cx), float(cy)).buffer(float(r), 32) for cx, cy, r, _ in nuc]


def zona():
    from shapely.geometry import box
    return box(0, 0, W, H).difference(box(*RELLENO))


def tejido(forma=(H, W), fragmentos=FRAGMENTOS):
    t = np.zeros(forma, bool)
    for x0, y0, x1, y1 in fragmentos:
        t[y0:y1, x0:x1] = True
    return t


def lamina(opaco, semilla, dab_nucleo=None, dab_anillo=None, epitelio_dab=None,
           h=H_REAL, d=D_REAL, estroma_h=0.04, ruido=1.5, epitelio_h=0.05):
    """Una lámina de tanda: estroma con contratinción ligera, núcleos con H 0,45-0,70 y, según el
    marcador, DAB nuclear (`dab_nucleo(rng, n)`), DAB en un anillo de 3 µm (`dab_anillo`) o DAB
    citoplasmático en el epitelio (mitad izquierda de cada fragmento, `epitelio_dab`: un valor o
    (mín, máx) por píxel) CON contratinción H realista (`epitelio_h`, 0,05: el citoplasma no es DAB
    puro; con 0 el estimador de DAB no tiene sesgo y el test no vería el de verdad)."""
    rng = np.random.default_rng(semilla + 1000)
    nuc = nucleos(semilla)
    forma = (H, W)
    tej = tejido()
    cH = np.where(tej, estroma_h, 0.0)
    cD = np.zeros(forma)
    epi = np.zeros(forma, bool)
    if epitelio_dab is not None:
        for x0, y0, x1, y1 in FRAGMENTOS:
            epi[y0:y1, x0:(x0 + x1) // 2] = True
        if np.ndim(epitelio_dab):
            cD[epi] = rng.uniform(epitelio_dab[0], epitelio_dab[1], int(epi.sum()))
        else:
            cD[epi] = epitelio_dab
        cH[epi] = epitelio_h
    ch_n = rng.uniform(0.45, 0.70, len(nuc))
    cd_n = dab_nucleo(rng, len(nuc)) if dab_nucleo else np.zeros(len(nuc))
    nm = mascara_nuclear(nuc, forma)
    cH = np.where(nm, discos(nuc, forma, ch_n), cH)
    cD = np.where(nm, discos(nuc, forma, cd_n), cD)
    va = np.zeros(len(nuc))
    if dab_anillo is not None:
        va = dab_anillo(rng, len(nuc))
        ring = discos(nuc, forma, va, anillo_px=3.0 / MPP)
        cD = np.where(~nm & (ring > 0), ring, cD)
    img = pinta(cH, cD, h=h, d=d, ruido=ruido, semilla=semilla)
    img[RELLENO[1]:RELLENO[3], RELLENO[0]:RELLENO[2]] = 255
    epi_nuc = epi[np.clip(nuc[:, 1].astype(int), 0, H - 1), np.clip(nuc[:, 0].astype(int), 0,
                                                                     W - 1)]
    return LaminaSint(opaco, img, MPP, zona(), verdad={"nucleos": nuc, "c_h": ch_n,
                                                       "c_d": cd_n, "c_anillo": va,
                                                       "epitelio": epi,
                                                       "nucleo_en_epitelio": epi_nuc})


def _fraccion(p, v):
    """DAB nuclear: una fracción `p` de núcleos con DAB `v` (un valor o (mín, máx))."""
    def f(rng, n):
        pos = rng.random(n) < p
        out = np.zeros(n)
        out[pos] = rng.uniform(v[0], v[1], int(pos.sum())) if np.ndim(v) else v
        return out
    return f


KI67_POS = 0.30                 # fracción VERDADERA de núcleos DAB+ de P-KI67 (DAB 0,35-0,70)
SYN_POS = 0.50                  # fracción VERDADERA de anillos DAB+ de P-SYN (DAB 0,25)
OTRAS_IHQ = {                   # las otras 6 de las 11 IHQ con tejido, sin nada raro
    "P-RE": dict(dab_nucleo=_fraccion(0.6, 0.5)),
    "P-RP": dict(dab_nucleo=_fraccion(0.3, 0.4)),
    "P-RA": dict(dab_nucleo=_fraccion(0.1, 0.3)),
    "P-CHGA": dict(dab_anillo=_fraccion(0.1, 0.2)),
    "P-{{DIANA3}}": dict(dab_nucleo=_fraccion(0.05, 0.3)),
    "P-P63": dict(dab_nucleo=_fraccion(0.05, 0.4)),
}


_PINTADAS = {}                  # láminas estándar ya pintadas (solo lectura): (opaco, semilla)


def _estandar(opaco, semilla, **kw):
    clave = (opaco, semilla)
    if clave not in _PINTADAS:
        _PINTADAS[clave] = lamina(opaco, semilla, **kw)
    return _PINTADAS[clave]


def tanda(semilla=7, her2neg_alto=None, extra=None, otras=True):
    """Las cinco de tanda más las otras 6 IHQ con tejido (las 11 del plan; `extra` = {opaco:
    kwargs de `lamina`} las sustituye o añade). Las estándar se pintan una vez por proceso."""
    lam = {
        "P-HER2NEG": (lamina("P-HER2NEG", semilla + 1, dab_nucleo=her2neg_alto) if her2neg_alto
                      else _estandar("P-HER2NEG", semilla + 1)),
        "P-HER2": _estandar("P-HER2", semilla + 2),
        "P-KI67": _estandar("P-KI67", semilla + 3,
                            dab_nucleo=_fraccion(KI67_POS, (0.35, 0.70))),
        "P-SYN": _estandar("P-SYN", semilla + 4, dab_anillo=_fraccion(SYN_POS, 0.25)),
        "P-CK19": _estandar("P-CK19", semilla + 5, epitelio_dab=(0.30, 0.60)),
    }
    resto = dict(OTRAS_IHQ) if otras else {}
    for k, (opaco, kw) in enumerate(resto.items()):
        if opaco not in (extra or {}):
            lam[opaco] = _estandar(opaco, semilla + 10 + k, **kw)
    for k, (opaco, kw) in enumerate((extra or {}).items()):
        lam[opaco] = lamina(opaco, semilla + 30 + k, **kw)
    return lam


def traza_memoria(directorio, procesador, pico_gb, muestras=20):
    """Traza SINTÉTICA con el formato de `guarda_memoria.corre(traza=…)`: «s RSS huella sin-Metal
    swap» por línea; el pico de la columna sin Metal es `pico_gb`."""
    ruta = os.path.join(directorio, "traza-%s.tsv" % procesador)
    with open(ruta, "w", encoding="utf-8") as f:
        for i in range(muestras):
            neto = pico_gb if i == muestras // 2 else pico_gb * (0.4 + 0.5 * i / muestras)
            f.write("%.0f\t%.2f\t%.2f\t%.2f\t%.2f\n" % (3 * i, neto * 0.9, neto + 1.0, neto, 0.0))
    return ruta


def segmentador_stub(lector, lam, nombre, zona=None, **kw):
    """Hace de InstanSeg con la VERDAD del sintético (los tests de InstanSeg real están en
    test_laminillas_piloto_segmenta). Devuelve lo mismo que `segmentador_lazyslide`."""
    import laminillas_segmenta as S
    pol = poligonos(lam.verdad["nucleos"])
    esperadas, _ = S.rejilla(lam.zona, lam.mpp_l0)
    return {"celulas": pol, "celulas_desplazadas": list(pol),
            "teselado": S.comprueba_teselas(esperadas, esperadas, 0.5, lam.zona, lam.mpp_l0),
            "dispositivo": "stub", "lote": 1, "aviso_check_input_tile": False,
            "pesos": {"fichero": S.INSTANSEG["fichero"], "sha256": S.INSTANSEG["sha256"],
                      "bytes": S.INSTANSEG["bytes"], "fuente": "stub"},
            "grandqc": kw.get("grandqc") or {"carga": False, "artefactos": [],
                                             "motivo": "stub sin GrandQC"}}
