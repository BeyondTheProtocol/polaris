"""tests/_laminillas_capas_mundo.py — una SESION y un lector SINTÉTICOS para `laminillas_capas`.

NADA REAL: tejido inventado por `panel_vision/sinteticos` (H&E e IHQ de Beer-Lambert con núcleos
sintéticos), en el formato de los productos del piloto que escribe `laminillas_proc` (núcleos de
`segmenta`, objetos y máscara de P-CK19, FC del piloto y registro de (ii)/(ii-bis), con sus
«hecho»), un sello de congelación sintético y un lector que sirve las láminas desde arrays (o desde
.npy, para el subproceso de la Puerta de verdad). Láminas a 0,5 µm/px en L0 (la vista del panel se
lee sin remuestreo) con un nivel ×8 por media de bloques.

  P-HE      2048×2048 px (1024 µm), tejido continuo; GrandQC marca uno de cada 7 núcleos.
  P-CK19    3072×3072 px (1536 µm), 9 fragmentos elípticos (≈440×400 µm) sobre vidrio; máscara =
            epitelio de la IHQ sintética (a 1 µm/px), anillo DAB 0,6 en epitelio y 0,02 fuera, T 0,3.
  P-KI67    P-CK19 trasladada (KI67 µm → CK19 µm: +20, −12) con «DAB de Ki67» en núcleos de la mitad
            izquierda (variante A) o derecha (B): la selección de campos no puede depender de eso.
  P-HER2NEG P-CK19 trasladada (−16, +8), sin DAB de más.
FC: los 9 fragmentos en la rejilla ×8 de P-CK19; verificados todos menos F8 en los dos pares.
"""
import json
import os
import sys

import numpy as np
from scipy import ndimage as ndi

AQUI = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(AQUI), "tools")
for _p in (TOOLS, os.path.join(TOOLS, "panel_vision")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MPP = 0.5
FECHA = "2026-10-02"
T_CK19 = 0.3
TRASLADO = {"P-KI67": (20.0, -12.0), "P-HER2NEG": (-16.0, 8.0)}
VIDRIO = 242
I0 = 245.0


# ── lector falso (la API de laminillas_lector que usa laminillas_capas) ─────────────────────
class LamFalsa:
    def __init__(self, op, arr, mpp=MPP):
        self.opaco, self.mpp_l0, self.arr = op, float(mpp), arr
        H, W = arr.shape[:2]
        self.dimensiones_l0 = (W, H)
        h8, w8 = H // 8, W // 8
        self.n8 = np.rint(arr[:h8 * 8, :w8 * 8].reshape(h8, 8, w8, 8, 3).astype(np.float32)
                          .mean(axis=(1, 3))).astype(np.uint8)
        self.niveles = [(1, self.mpp_l0, (W, H)), (8, self.mpp_l0 * 8, (w8, h8))]

    def nivel(self, factor):
        for n in self.niveles:
            if n[0] == factor:
                return n
        raise KeyError(factor)


class LectorFalso:
    """abre / lee_region / i0_local de `laminillas_lector`, sobre arrays. Anota cada lectura."""

    def __init__(self, laminas):
        self.lams = {op: (a if isinstance(a, LamFalsa) else LamFalsa(op, a)) for op, a in laminas.items()}
        self.lecturas = []

    @classmethod
    def desde_dir(cls, d):
        return cls({f[:-4]: np.load(os.path.join(d, f)) for f in sorted(os.listdir(d)) if f.endswith(".npy")})

    def abre(self, op):
        return self.lams[op]

    def lee_region(self, lam, mpp, x_l0, y_l0, w, h):
        lam = self.abre(lam) if isinstance(lam, str) else lam
        self.lecturas.append((lam.opaco, float(mpp), int(x_l0), int(y_l0), int(w), int(h)))
        if abs(mpp - lam.mpp_l0) <= 0.01 * mpp:
            src, f = lam.arr, 1
        elif abs(mpp - lam.niveles[1][1]) <= 0.01 * mpp:
            src, f = lam.n8, 8
        else:
            raise ValueError("lector falso: solo L0 y ×8 (mpp %r)" % mpp)
        x, y = int(x_l0) // f, int(y_l0) // f
        out = np.full((int(h), int(w), 3), 255, np.uint8)
        H, W = src.shape[:2]
        x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + int(w)), min(H, y + int(h))
        if x1 > x0 and y1 > y0:
            out[y0 - y:y1 - y, x0 - x:x1 - x] = src[y0:y1, x0:x1]
        return out

    def i0_local(self, lam):
        return lambda X, Y: np.full(np.shape(X) + (3,), I0, np.float32)


# ── tejido ──────────────────────────────────────────────────────────────────────────────────
def _poligonos(lab):
    """{id: contorno exterior cerrado (px, coordenadas continuas)} de cada etiqueta."""
    import cv2
    out = {}
    for i, sl in enumerate(ndi.find_objects(lab), 1):
        if sl is None:
            continue
        m = (lab[sl] == i).astype(np.uint8)
        cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not cs:
            continue
        c = max(cs, key=len).reshape(-1, 2).astype(np.float64) + 0.5
        c[:, 0] += sl[1].start
        c[:, 1] += sl[0].start
        if len(c) >= 3:
            out[i] = np.vstack([c, c[:1]])
    return out


def _mosaico(base, n, extra=None):
    """La tesela `base` (dict de sinteticos) repetida n×n: rgb, núcleos [(cx, cy, polígono, clase)]."""
    import sinteticos as S
    lado = base["od"].shape[0]
    rgb1 = S._od_a_rgb(base["od"], S._rng(9, 9))
    polys = _poligonos(base["lab"])
    rgb = np.tile(rgb1, (n, n, 1))
    nuc = []
    for by in range(n):
        for bx in range(n):
            for d in base["nucleos"]:
                p = polys.get(d["id"])
                if p is None:
                    continue
                nuc.append((d["cx"] + bx * lado, d["cy"] + by * lado, p + (bx * lado, by * lado),
                            d.get("clase")))
    out = {"rgb": rgb, "nucleos": nuc, "lado": lado * n}
    if extra:
        out[extra] = np.tile(base[extra], (n, n))
    return out


def _empaqueta(nuc):
    xy = np.concatenate([p for _x, _y, p, _c in nuc]) if nuc else np.zeros((0, 2))
    offs = np.cumsum([0] + [len(p) for _x, _y, p, _c in nuc]).astype(np.int64)
    cent = np.array([(x, y) for x, y, _p, _c in nuc], np.float64).reshape(-1, 2)
    return cent, xy, offs


def mundo_tejido():
    """{op: array} y lo que el pintado tiene que reconocer. Determinista."""
    import sinteticos as S
    he = _mosaico(S.fondo_he(7, 512), 4)
    ck = _mosaico(S.fondo_ihc(5, 512), 6, extra="epitelio")
    L = ck["lado"]
    yy, xx = np.mgrid[0:L, 0:L]
    frag = np.zeros((L, L), np.int32)
    for k in range(9):
        cx, cy = 512 + 1024 * (k % 3), 512 + 1024 * (k // 3)
        frag[((xx - cx) / 440.0) ** 2 + ((yy - cy) / 400.0) ** 2 <= 1.0] = k + 1
    tejido = frag > 0
    ck["rgb"][~tejido] = VIDRIO
    ck["epitelio"] &= tejido
    ck["nucleos"] = [n for n in ck["nucleos"] if tejido[min(L - 1, int(n[1])), min(L - 1, int(n[0]))]]
    ck["fragmentos"] = frag
    return he, ck


def _trasladada(rgb, t_um):
    """La lámina móvil: su píxel (x, y) muestra la referencia en (x + tx, y + ty) (µm/MPP)."""
    dx, dy = int(round(t_um[0] / MPP)), int(round(t_um[1] / MPP))
    H, W = rgb.shape[:2]
    out = np.full_like(rgb, VIDRIO)
    xs0, xs1 = max(0, dx), min(W, W + dx)
    ys0, ys1 = max(0, dy), min(H, H + dy)
    out[ys0 - dy:ys1 - dy, xs0 - dx:xs1 - dx] = rgb[ys0:ys1, xs0:xs1]
    return out


def laminas(he, ck, variante="A"):
    """{op: array RGB L0}. `variante` mueve el «DAB de Ki67» de mitad (A izquierda, B derecha)."""
    ki = _trasladada(ck["rgb"], TRASLADO["P-KI67"])
    L = ck["lado"]
    tx, ty = TRASLADO["P-KI67"]
    for x, y, _p, _c in ck["nucleos"]:
        kx, ky = x - tx / MPP, y - ty / MPP                     # el núcleo, en el marco de P-KI67
        if (kx < L / 2) == (variante == "A") and 4 <= kx < L - 4 and 4 <= ky < L - 4:
            ki[int(ky) - 3:int(ky) + 4, int(kx) - 3:int(kx) + 4] = (110, 70, 40)
    return {"P-HE": he["rgb"], "P-CK19": ck["rgb"], "P-KI67": ki,
            "P-HER2NEG": _trasladada(ck["rgb"], TRASLADO["P-HER2NEG"])}


# ── SESION en el formato de laminillas_proc ─────────────────────────────────────────────────
def _escribe(ruta, obj):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1, sort_keys=True)


def _npz(ruta, **arrays):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    np.savez_compressed(ruta, **arrays)


def sesion(ses, he, ck, lams, con_grandqc=True, sin=()):
    """Escribe en `ses` los productos del piloto. `sin`: productos a omitir («registro», «mascara»…)."""
    import laminillas_comun as C
    import laminillas_proc as P
    import laminillas_sello as SL
    os.makedirs(ses, exist_ok=True)
    cont = {k: {"sintetico": True} for k in SL.SECCIONES_BASE}
    cont["semillas"] = {"maestra": 20261001}
    sello = SL.sella(os.path.join(ses, SL.FICHERO), cont, FECHA)
    _escribe(os.path.join(ses, "manifiesto.json"),
             {"version": 1, "laminas": {op: {"fichero": "%s.tif" % op, "sha256": "%064x" % (i + 1)}
                                        for i, op in enumerate(sorted(lams))}})
    for op, mundo in (("P-HE", he), ("P-CK19", ck)):
        cent, xy, offs = _empaqueta(mundo["nucleos"])
        arrays = dict(centroide=cent, area_um2=np.full(len(cent), 30.0), pol_xy=xy, pol_offs=offs)
        if op == "P-HE" and con_grandqc:
            arrays["grandqc"] = (np.arange(len(cent)) % 7) == 0
        rel_npz, rel_json = "segmenta/nucleos_%s.npz" % op, "segmenta/nucleos_%s.json" % op
        _npz(os.path.join(ses, rel_npz), **arrays)
        _escribe(os.path.join(ses, rel_json), {"lamina": op, "n": len(cent), "mpp_l0": MPP,
                                               "sello": sello.sha256})
        C.marca_hecho(ses, "segmenta", op, productos=[rel_npz, rel_json])
    hu = P._huella(ses, P._rels_nucleos(ses, "P-CK19") + ["congelacion.json"])
    cent, _xy, _offs = _empaqueta(ck["nucleos"])
    dab = np.array([0.6 if c == "epitelial" else 0.02 for _x, _y, _p, c in ck["nucleos"]])
    _npz(os.path.join(ses, "piloto", "objetos_P-CK19.npz"), centroide=cent, dab_anillo=dab)
    _escribe(os.path.join(ses, "piloto", "objetos_P-CK19.json"),
             {"lamina": "P-CK19", "n": len(cent), "mpp_l0": MPP, "sello": sello.sha256, "entradas": hu})
    # una «diana» con objetos que la selección NO debe leer nunca (si los leyera, A ≠ B)
    _npz(os.path.join(ses, "piloto", "objetos_P-KI67.npz"), centroide=cent,
         dab_nucleo=np.random.default_rng(len(lams)).random(len(cent)))
    if "mascara" not in sin:
        m = ck["epitelio"]
        m1 = m.reshape(m.shape[0] // 2, 2, m.shape[1] // 2, 2).any(axis=(1, 3))      # a 1 µm/px
        _npz(os.path.join(ses, "piloto", "ck19", "mascara.npz"), bits=np.packbits(m1, axis=None),
             forma=np.asarray(m1.shape))
        _escribe(os.path.join(ses, "piloto", "ck19", "mascara.json"),
                 {"lamina": "P-CK19", "origen_l0": [0.0, 0.0], "mpp": 1.0, "mpp_l0": MPP,
                  "forma": list(m1.shape), "umbral": {"T": T_CK19}, "entradas": hu})
    if "registro" not in sin:
        frag = ck["fragmentos"]
        f8 = frag[::8, ::8]
        ids = ["F%d" % k for k in range(9)]
        _npz(os.path.join(ses, "piloto", "registro", "fc_piloto.npz"), ids=np.array(ids),
             mascaras=np.stack([f8 == k + 1 for k in range(9)]), mpp=np.array(MPP * 8),
             areas=np.array([float((f8 == k + 1).sum()) * 16.0 for k in range(9)]))
        sha_fc = C.sha256_fichero(os.path.join(ses, "piloto", "registro", "fc_piloto.npz"))
        for movil, (tx, ty) in TRASLADO.items():
            M = [[1.0, 0.0, tx], [0.0, 1.0, ty], [0.0, 0.0, 1.0]]
            rel = os.path.join("piloto", "registro", "%s.json" % movil)
            _escribe(os.path.join(ses, rel), {
                "tipo": "registro-par", "fija": "P-CK19", "movil": movil, "sello": sello.sha256,
                "fc_piloto_sha256": sha_fc,
                "resumen": {"fija": "P-CK19", "movil": movil, "global": {"matriz_um": M},
                            "fragmentos": [{"id": i, "matriz_um": M, "pasa": i != "F8"} for i in ids]}})
            C.marca_hecho(ses, "registro-par", movil, "P-CK19", productos=[rel])
    return sello


def guarda_laminas(d, lams):
    os.makedirs(d, exist_ok=True)
    for op, a in lams.items():
        np.save(os.path.join(d, op + ".npy"), a)
