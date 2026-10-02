#!/usr/bin/env python3
"""tests/test_laminillas_he.py — F3 parte B (`tools/laminillas_he.py`): H&E del primario, hueso y
exploratorio, y el procesador `laminillas_he` de la ventanilla.

TODO SINTÉTICO: una «P-HE» pintada por Beer-Lambert (dos nidos densos, una banda de estroma con
infiltrado conocido, un campo de adipocitos, una luz con su epitelio y un hueco con un núcleo
dentro), un «hueso» con trabéculas, médula granular y adipocitos de áreas conocidas, y
clasificadores, WSInfer y embeddings sustituidos por stubs con verdad conocida. Ninguna lámina
real, ninguna ruta clínica. Los modelos reales solo hacen un forward sobre una tesela sintética
(como el humo); el recorrido de LazySlide sobre un TIFF sintético y los fundacionales van con
BTP_HE_LENTO=1.

Corre con el intérprete del venv `patologia` (se re-ejecuta con él); sin venv, SKIP (rc 77).
"""
import csv
import hashlib
import io
import json
import math
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

VENV_DIR = os.environ.get("BTP_VENV_PATOLOGIA") or os.path.expanduser("~/.polaris-venvs/patologia")
VENV_PY = os.path.join(VENV_DIR, "bin", "python")
if os.path.realpath(sys.prefix) != os.path.realpath(VENV_DIR):
    if os.path.exists(VENV_PY) and not os.environ.get("BTP_HE_REEXEC"):
        env = dict(os.environ, BTP_HE_REEXEC="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                   PYTORCH_ENABLE_MPS_FALLBACK="1")
        env.setdefault("HF_HOME", os.path.expanduser("~/.polaris-venvs/cache/hf"))
        os.execve(VENV_PY, [VENV_PY, os.path.abspath(__file__)] + sys.argv[1:], env)
    print("SKIP: falta el venv patologia (%s)" % VENV_DIR)
    sys.exit(77)

import numpy as np  # noqa: E402
from shapely.geometry import box  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)
os.environ.pop("BTP_VENTANILLA", None)          # los tests no son la ventanilla

import laminillas_comun as C  # noqa: E402
import laminillas_he as H  # noqa: E402
import laminillas_sello as SL  # noqa: E402

MPP0 = 0.25                                       # L0 sintético: los niveles son enteros exactos
I0 = 244.0
VH = np.array(H.RUIFROK_H) / np.linalg.norm(H.RUIFROK_H)
VE = np.array(H.RUIFROK_E) / np.linalg.norm(H.RUIFROK_E)
HF = os.environ.get("HF_HOME") or os.path.expanduser("~/.polaris-venvs/cache/hf")


def pinta(cH, cE):
    od = cH[..., None] * VH + cE[..., None] * VE
    return np.clip(np.rint(I0 * 10.0 ** (-od)), 0, 255).astype(np.uint8)


class LaminaSint:
    def __init__(self, opaco, niveles, dims):
        self.opaco, self.mpp_l0, self.dimensiones_l0 = opaco, MPP0, dims
        self.img = niveles                                       # {factor: array}


class LectorSint:
    """API del lector único sobre arrays en memoria. Factores sin imagen → textura de ruido."""

    def __init__(self, laminas, manifiesto=None):
        self.laminas = laminas
        self._man = manifiesto or {"laminas": {n: {"mpp": MPP0} for n in laminas}}
        self.lecturas = []

    def abre(self, n):
        return self.laminas[n]

    def manifiesto(self):
        return self._man

    def zona_escaneada(self, lam):
        w, h = lam.dimensiones_l0
        return box(0, 0, w, h)

    def i0_local(self, lam):
        def f(x, y):
            forma = np.broadcast(np.asarray(x), np.asarray(y)).shape
            return np.full(forma + (3,), I0, np.float32)
        return f

    def lee_region(self, lam, mpp, x, y, w, h):
        f = int(round(mpp / lam.mpp_l0))
        assert abs(mpp - f * lam.mpp_l0) < 1e-9, "mpp no entero: %r" % mpp
        self.lecturas.append((lam.opaco, f, int(w), int(h)))
        img = lam.img.get(f)
        if img is None:
            rng = np.random.default_rng(abs(int(x) * 7 + int(y)))
            return pinta(rng.uniform(0.1, 0.8, (int(h), int(w))),
                         rng.uniform(0.2, 0.6, (int(h), int(w))))
        out = np.full((int(h), int(w), 3), 255, np.uint8)
        c0, r0 = int(x) // f, int(y) // f
        H_, W_ = img.shape[:2]
        rs, cs = max(0, r0), max(0, c0)
        re_, ce = min(H_, r0 + int(h)), min(W_, c0 + int(w))
        if re_ > rs and ce > cs:
            out[rs - r0:re_ - r0, cs - c0:ce - c0] = img[rs:re_, cs:ce]
        return out


# ── P-HE sintética ────────────────────────────────────────────────────────────────────────────
ANCHO_UM, ALTO_UM = 1200.0, 600.0
NIDO_A, NIDO_B = (100.0, 400.0), (750.0, 1100.0)
TEJ_Y = (50.0, 550.0)
BANDA_X, BANDA_Y = (400.0, 750.0), (50.0, 370.0)
GRASA = (420.0, 740.0, 380.0, 548.0)               # x0, x1, y0, y1 (µm)
LUZ = (250.0, 300.0, 20.0)                          # cx, cy, r (µm), dentro del nido A
HUECO_CON_NUCLEO = (600.0, 150.0, 20.0)


def _adipocitos(x0, x1, y0, y1, r, paso):
    cs = []
    fila = 0
    y = y0 + r
    while y + r <= y1:
        x = x0 + r + (paso / 2 if fila % 2 else 0)
        while x + r <= x1:
            cs.append((x, y))
            x += paso
        y += paso * math.sqrt(3) / 2
        fila += 1
    return cs


def he_sintetica():
    """(imagen ×8 a 2 µm/px, centroides L0, clase «verdadera» por núcleo)."""
    m8 = 2.0
    h, w = int(ALTO_UM / m8), int(ANCHO_UM / m8)
    yy, xx = (np.mgrid[0:h, 0:w] + 0.5) * m8
    tej = (xx >= 100) & (xx < 1100) & (yy >= TEJ_Y[0]) & (yy < TEJ_Y[1])
    cE = np.where(tej, 0.5, 0.0)
    cH = np.where(tej, 0.1, 0.0)
    blanco = np.zeros_like(tej)
    for cx, cy in _adipocitos(*GRASA, r=30.0, paso=62.0):
        blanco |= (xx - cx) ** 2 + (yy - cy) ** 2 < 30.0 ** 2
    for cx, cy, r in (LUZ, HUECO_CON_NUCLEO):
        blanco |= (xx - cx) ** 2 + (yy - cy) ** 2 < r ** 2
    cE[blanco] = 0.0
    cH[blanco] = 0.0
    img = pinta(cH, cE)
    xy, verdad = [], []
    for (a, b) in (NIDO_A, NIDO_B):
        for x in np.arange(a + 5, b, 10.0):
            for y in np.arange(TEJ_Y[0] + 5, TEJ_Y[1], 10.0):
                if math.hypot(x - LUZ[0], y - LUZ[1]) < LUZ[2] + 3:
                    continue
                xy.append((x, y))
                verdad.append("neo")
    for k in range(19):                                       # epitelio de la luz
        a = 2 * math.pi * k / 19
        xy.append((LUZ[0] + 24 * math.cos(a), LUZ[1] + 24 * math.sin(a)))
        verdad.append("neo")
    for i, x in enumerate(np.arange(BANDA_X[0] + 15, BANDA_X[1], 30.0)):
        for j, y in enumerate(np.arange(BANDA_Y[0] + 15, BANDA_Y[1], 30.0)):
            if math.hypot(x - HUECO_CON_NUCLEO[0], y - HUECO_CON_NUCLEO[1]) < 24:
                continue
            xy.append((x, y))
            verdad.append("inf" if (i + j) % 2 else "con")
    xy.append(HUECO_CON_NUCLEO[:2])                           # un núcleo dentro del hueco
    verdad.append("con")
    return img, np.asarray(xy) / MPP0, verdad


NOMBRES = {"histoplus": {"neo": "Cancer cell", "inf": "Lymphocytes", "con": "Fibroblasts"},
           "nulite": {"neo": "Neoplastic", "inf": "Inflammatory", "con": "Connective"}}


def stub_clasificador(clave, xy_l0, verdad, cobertura=0.95, cambia=None, semilla=0):
    """Detecciones del «modelo»: los núcleos verdaderos con ruido de 1 µm, el `cobertura`·100 %
    de ellos; `cambia(i, etiqueta) → etiqueta` para sembrar desacuerdo."""
    def fn(lector, nombre, dir_trabajo, log):
        rng = np.random.default_rng(semilla)
        keep = rng.random(len(xy_l0)) < cobertura
        et = [cambia(i, v) if cambia else v for i, v in enumerate(verdad)]
        cent = xy_l0[keep] + rng.normal(0, 1.0 / MPP0, (int(keep.sum()), 2))
        cls = np.array([NOMBRES[clave][e] for e, k in zip(et, keep) if k], dtype="<U32")
        return {"centroide": cent.astype(np.float32), "clase": cls,
                "prob": np.full(len(cls), 0.9, np.float32), "n": int(len(cls)),
                "dispositivo": "stub", "lote": 1, "teselado": None, "pesos": None,
                "avisos": [], "intentos_fallidos": []}
    return fn


def stub_falla(lector, nombre, dir_trabajo, log):
    raise RuntimeError("pesos no disponibles (stub)")


def stub_tumor(lector, lam, rej, f_env, log):
    xc = (rej["x0"] + (np.arange(rej["nx"]) + 0.5) * rej["lado_l0"]) * MPP0
    fila = np.where(((xc >= NIDO_A[0]) & (xc < NIDO_A[1])) | ((xc >= NIDO_B[0]) &
                                                              (xc < NIDO_B[1])), 0.9, 0.1)
    p = np.tile(fila, (rej["ny"], 1)).astype(np.float32)
    p[f_env < 0.25] = np.nan
    return {"p": p, "dispositivo": "stub", "pesos": None, "n_teselas": int(np.isfinite(p).sum()),
            "clases": ["Other", "Tumor"], "tesela": {"px": rej["px"], "mpp": rej["mpp_lectura"]}}


def sesion_he(base, con_sello=True, con_nucleos=True, grandqc=None):
    img, xy, verdad = he_sintetica()
    dims = (int(ANCHO_UM / MPP0), int(ALTO_UM / MPP0))
    lam = LaminaSint("P-HE", {8: img}, dims)
    if con_sello:
        SL.sella(os.path.join(base, SL.FICHERO), {k: {"sintetico": True}
                                                  for k in SL.SECCIONES_BASE}, "2026-10-02")
    if con_nucleos:
        os.makedirs(os.path.join(base, "segmenta"), exist_ok=True)
        rn = os.path.join("segmenta", "nucleos_P-HE.npz")
        rj = os.path.join("segmenta", "nucleos_P-HE.json")
        extra = {} if grandqc is None else {"grandqc": grandqc(xy)}
        np.savez_compressed(os.path.join(base, rn), centroide=xy.astype(np.float64),
                            area_um2=np.full(len(xy), 40.0), **extra)
        C.escribe_json(os.path.join(base, rj), {"lamina": "P-HE", "n": len(xy), "mpp_l0": MPP0})
        C.marca_hecho(base, "segmenta", "P-HE", productos=[rn, rj])
    return LectorSint({"P-HE": lam}), xy, verdad


def _silencio(m):
    return None


class TestClases(unittest.TestCase):
    def test_mapas_cubren_las_clases_de_los_envoltorios(self):
        from lazyslide_models.segmentation.cellvit_family.histoplus import HistoPLUS
        from lazyslide_models.segmentation.cellvit_family.nulite import NuLite
        self.assertEqual(set(H.A_COMUN["histoplus"]) | {"Background"}, set(HistoPLUS.classes))
        self.assertEqual(set(H.A_COMUN["nulite"]) | {"Background"}, set(NuLite.classes))
        for m in H.A_COMUN.values():
            self.assertTrue(set(m.values()) <= set(H.COMUN))

    def test_clase_desconocida_falla_cerrado(self):
        with self.assertRaises(ValueError):
            H.codigos("nulite", ["Neoplastic", "Tumor"])

    def test_asigna_mutuo(self):
        ref = np.array([[0.0, 0.0], [3.0, 0.0], [100.0, 100.0]])
        mod = np.array([[1.0, 0.0], [100.0, 105.0]])
        j = H.asigna(ref, mod, radio_px=4.0)
        # (1,0) está a 1 de ref0 y a 2 de ref1: solo el mutuo (ref0); ref2 está a 5 > 4
        self.assertEqual(j.tolist(), [0, -1, -1])

    def test_kappa(self):
        a = np.array([0, 0, 1, 1, 2, 2])
        self.assertAlmostEqual(H.kappa(a, a), 1.0)
        self.assertLess(H.kappa(a, np.array([1, 1, 0, 0, 2, 0])), 0.2)


class TestMiniPuerta(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(1)
        self.n = 2000
        self.frag = np.repeat([0, 1], self.n // 2)
        self.verdad = rng.choice([H.NEO, H.INF, H.CON], size=self.n, p=[0.5, 0.2, 0.3]).astype(
            np.int8)

    def _ruido(self, frac, semilla):
        rng = np.random.default_rng(semilla)
        c = self.verdad.copy()
        m = rng.random(self.n) < frac
        c[m] = rng.choice([H.NEO, H.INF, H.CON], size=int(m.sum())).astype(np.int8)
        return c

    def test_acuerdo_manda_histoplus(self):
        p = H.mini_puerta({"histoplus": self.verdad, "nulite": self._ruido(0.03, 2)}, self.frag)
        self.assertEqual((p["decision"], p["manda"]), ("histoplus", "histoplus"))
        self.assertGreaterEqual(p["pares"]["histoplus|nulite"]["kappa"], 0.6)

    def test_desacuerdo_da_rango(self):
        nl = self.verdad.copy()
        nl[(self.frag == 1) & (nl == H.NEO)] = H.CON          # NuLite pierde neoplásicos en F1
        p = H.mini_puerta({"histoplus": self.verdad, "nulite": nl}, self.frag)
        self.assertEqual(p["decision"], "rango")
        self.assertEqual(p["usados"], ["histoplus", "nulite"])
        self.assertIn(H.ROTULOS["rango"], p["rotulos"])
        self.assertGreater(p["pares"]["histoplus|nulite"]["por_fragmento"]["F1"]["neo_pts"], 5)

    def test_sin_histoplus_manda_nulite_y_se_dice(self):
        p = H.mini_puerta({"histoplus": None, "nulite": self.verdad}, self.frag)
        self.assertEqual(p["decision"], "nulite")
        self.assertIn(H.ROTULOS["sin_histoplus"], p["rotulos"])
        self.assertIn(H.ROTULOS["solo_uno"], p["rotulos"])

    def test_cobertura_baja_no_elegible(self):
        hp = self.verdad.copy()
        hp[: int(0.4 * self.n)] = -1                            # cubre el 60 %
        p = H.mini_puerta({"histoplus": hp, "nulite": self.verdad}, self.frag)
        self.assertEqual(p["decision"], "nulite")
        self.assertFalse(p["clasificadores"]["histoplus"]["elegible"])
        p = H.mini_puerta({"histoplus": hp, "nulite": None}, self.frag)
        self.assertEqual(p["decision"], "sin_clasificacion")
        self.assertIsNone(p["manda"])


class TestPrimario(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="he-")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _corre(self, clasificadores=None, cobertura=0.95, cambia=None):
        lector, xy, verdad = sesion_he(self.base)
        cl = clasificadores or {k: stub_clasificador(k, xy, verdad, cobertura,
                                                     cambia if k == "nulite" else None, i)
                                for i, k in enumerate(H.PRINCIPAL)}
        return H.primario(self.base, lector, cl, stub_tumor, log=_silencio), xy, verdad

    def test_regiones_infiltrado_y_rotulos(self):
        out, xy, verdad = self._corre()
        self.assertEqual(out["manda"], "histoplus")
        reg = out["regiones"]
        # tumoral = los dos nidos (2 × 300/350 µm × 500 µm = 0,325 mm²) ±25 % (rejilla de 87,5 µm)
        t = reg[H.REGION[1]]["mm2"]["valor"]
        self.assertAlmostEqual(t, 0.325, delta=0.08)
        self.assertGreater(reg[H.REGION[5]]["mm2"]["valor"], 0.02)       # campo de adipocitos
        self.assertGreater(reg[H.REGION[4]]["mm2"]["valor"], 0.05)       # banda de estroma
        # infiltrado: la mitad de los núcleos del estroma (rejilla de 30 µm) = 556/mm² ±30 %
        dens = out["infiltrado"]["densidad_mm2"]["valor"]
        self.assertAlmostEqual(dens, 0.5 / (0.03 ** 2), delta=0.3 * 556)
        self.assertEqual(out["infiltrado"]["rotulo_es"],
                         "densidad de infiltrado inflamatorio en el estroma intratumoral "
                         "(aproximación)")
        # frases obligatorias y prohibidas
        self.assertEqual(out["invasivo_in_situ"], H.ROTULOS["invasivo"])
        self.assertIn("not separated by any model", out["invasivo_in_situ"])
        self.assertEqual(out["necrosis"], "necrosis: not classified")
        self.assertEqual(H.frases_prohibidas(out), [])
        texto = json.dumps(out)
        self.assertNotRegex(texto, r"\bsTILs?\b")
        self.assertEqual(out["cellvitpp"]["estado"], H.ROTULOS["cellvitpp_no"])
        # grasa: la luz con epitelio y el hueco con núcleo NO son grasa
        hu = out["tejido"]["huecos"]
        self.assertEqual(hu["con_anillo"], 1)
        self.assertEqual(hu["con_nucleo"], 1)
        # productos y «hecho»
        self.assertTrue(C.esta_hecho(self.base, "he-primario", "P-HE"))
        z = np.load(os.path.join(self.base, "he", "regiones_P-HE.npz"))
        self.assertIn("region_histoplus", z.files)
        self.assertEqual(len(z["clase_instanseg_histoplus"]), len(xy))

    def test_fraccion_neoplasica_conocida(self):
        out, xy, verdad = self._corre()
        esperada = sum(v == "neo" for v in verdad) / len(verdad)
        self.assertAlmostEqual(out["fraccion_neoplasica"]["global"]["valor"], esperada, delta=0.02)
        self.assertGreater(out["fraccion_neoplasica"]["en_region_tumoral"]["valor"], 0.95)

    def test_desacuerdo_rango_en_las_cifras(self):
        def cambia(i, v):
            return "con" if v == "neo" and i % 3 == 0 else v
        out, _, _ = self._corre(cambia=cambia)
        self.assertEqual(out["manda"], "rango")
        f = out["fraccion_neoplasica"]["global"]
        self.assertLess(f["min"], f["max"])
        self.assertEqual(f["rotulo"], H.ROTULOS["rango"])
        self.assertIn("min", out["infiltrado"]["densidad_mm2"])

    def test_grandqc_excluye_nucleos(self):
        def marca(xy):                                  # el nido B entero, marcado como artefacto
            x = xy[:, 0] * MPP0
            return (x >= NIDO_B[0]) & (x < NIDO_B[1])
        lector, xy, verdad = sesion_he(self.base, grandqc=marca)
        cl = {k: stub_clasificador(k, xy, verdad, semilla=i) for i, k in enumerate(H.PRINCIPAL)}
        out = H.primario(self.base, lector, cl, stub_tumor, log=_silencio)
        n_b = int(marca(xy).sum())
        self.assertEqual(out["nucleos"]["excluidos_artefacto_grandqc"], n_b)
        self.assertLessEqual(out["puerta"]["clasificadores"]["histoplus"]["n_casados"],
                             len(xy) - n_b)
        # sin los núcleos del nido B, su área ya no es tumoral por el clasificador
        t = out["regiones"][H.REGION[1]]["mm2"]["valor"]
        self.assertAlmostEqual(t, 0.325 / 2, delta=0.05)

    def test_histoplus_cae_y_se_declara(self):
        lector, xy, verdad = sesion_he(self.base)
        cl = {"histoplus": stub_falla, "nulite": stub_clasificador("nulite", xy, verdad)}
        out = H.primario(self.base, lector, cl, stub_tumor, log=_silencio)
        self.assertEqual(out["manda"], "nulite")
        self.assertFalse(out["clasificadores"]["histoplus"]["disponible"])
        self.assertIn("pesos no disponibles", out["clasificadores"]["histoplus"]["motivo"])
        self.assertIn(H.ROTULOS["sin_histoplus"], out["puerta"]["rotulos"])
        # la caída NO se sella como hecha: la siguiente corrida lo reintenta
        self.assertFalse(C.esta_hecho(self.base, "he-clases", "P-HE", "histoplus"))

    def test_sin_clasificador_ni_infiltrado(self):
        lector, _, _ = sesion_he(self.base)
        out = H.primario(self.base, lector, {"histoplus": stub_falla, "nulite": stub_falla},
                         stub_tumor, log=_silencio)
        self.assertEqual(out["manda"], "sin_clasificacion")
        self.assertEqual(out["infiltrado"]["estado"], H.ROTULOS["infiltrado_caido"])
        self.assertIn(H.ROTULOS["solo_wsinfer"], out["declaraciones"])

    def test_cellvitpp_entra_como_comparador(self):
        lector, xy, verdad = sesion_he(self.base)
        d = os.path.join(self.base, "nube", "cellvitpp")
        os.makedirs(d)
        _escribe_cellvitpp(d, xy, verdad)
        cl = {k: stub_clasificador(k, xy, verdad, semilla=i) for i, k in enumerate(H.PRINCIPAL)}
        out = H.primario(self.base, lector, cl, stub_tumor, log=_silencio)
        self.assertEqual(out["cellvitpp"]["presentes"], ["cellvitpp_nucls_super"])
        self.assertIn("histoplus|cellvitpp_nucls_super", out["puerta"]["pares"])
        self.assertEqual(out["manda"], "histoplus")

    def test_cellvitpp_con_la_clase_vetada_sale_limpio(self):
        """El resultado REAL de nucls_super trae en su mapa la clase que se llama como el índice
        estromal de TIL: `primario` tiene que salir limpio (solo la taxonomía común; Métodos por
        índice y descripción saneada), también con un par rechazado que cita esa clase en su CSV;
        y la prohibición sigue disparando si alguien mete «sTIL» a mano en la salida."""
        lector, xy, verdad = sesion_he(self.base)
        d = os.path.join(self.base, "nube", "cellvitpp")
        os.makedirs(d)
        _escribe_cellvitpp(d, xy, verdad)                                  # nucls_super, con «sTIL»
        _escribe_cellvitpp(d, xy, verdad, clasif="panoptils",              # «sTIL» SIN mapa: rechazado
                           mapa={"Epithelial Cells": "epithelial", "Stromal Cells": "connective"},
                           clases={"neo": "Epithelial Cells", "inf": "sTIL", "con": "Stromal Cells"})
        with open(os.path.join(d, "P-HE.nucls_super.json"), encoding="utf-8") as f:
            mapa_crudo = json.load(f)["mapa_clases"]
        self.assertIn("sTIL", mapa_crudo)                                  # el enganche sí lo lleva
        cl = {k: stub_clasificador(k, xy, verdad, semilla=i) for i, k in enumerate(H.PRINCIPAL)}
        out = H.primario(self.base, lector, cl, stub_tumor, log=_silencio)
        self.assertEqual(out["cellvitpp"]["presentes"], ["cellvitpp_nucls_super"])
        self.assertIn("cellvitpp_panoptils", out["cellvitpp"]["rechazados"])
        self.assertEqual(H.frases_prohibidas(out), [])
        with open(os.path.join(self.base, "he", "P-HE.json"), encoding="utf-8") as f:
            escrito = f.read()
        for texto in (json.dumps(out), escrito):
            self.assertNotRegex(texto, r"\bsTILs?\b")
            for crudo in ("nonTIL Stromal", "Epithelial Cells", "Stromal Cells"):   # ningún nombre
                self.assertNotIn(crudo, texto)   # crudo («Tumor» no sirve: WSInfer lo usa en su salida)
        cv = out["clasificadores"]["cellvitpp_nucls_super"]
        self.assertNotIn("mapa_clases", cv)
        self.assertEqual(cv["clases_comunes"], ["connective", "inflammatory", "neoplastic", "other"])
        self.assertIn("nucls_super class 2: stromal lymphocytes -> inflammatory", cv["metodos"])
        self.assertEqual([m.split(":")[0] for m in cv["metodos"]],
                         ["nucls_super class %d" % i for i in range(4)])
        # la prohibición NO se ha relajado: a mano, en cualquier rincón de la salida, revienta
        for mete in (lambda o: o.update(nota="sTIL"),
                     lambda o: o["clasificadores"]["cellvitpp_nucls_super"]["metodos"].append(
                         "nucls_super class 2: sTIL"),
                     lambda o: o["clasificadores"]["cellvitpp_nucls_super"].update(
                         mapa_clases=mapa_crudo)):
            o = json.loads(json.dumps(out))
            mete(o)
            with self.assertRaises(RuntimeError):
                H._exige_limpio(o)


def _escribe_cellvitpp(d, xy, verdad, cabecera=None, version="1.0.9", clases=None,
                       clasif="nucls_super", mapa=None, modelo="CellViT-SAM-H-x40"):
    """El par que escribe _a_enganche.py en la máquina, con los nombres CRUDOS de las clases del
    modelo (por defecto nucls_super, el de la clase vetada)."""
    mapa = mapa or {"Tumor": "neoplastic", "nonTIL Stromal": "connective", "sTIL": "inflammatory",
                    "Other": "other"}
    nom = {"neo": "Tumor", "inf": "sTIL", "con": "nonTIL Stromal"}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cabecera or ["x_l0", "y_l0", "clase", "prob"])
    for (x, y), v in zip(xy, verdad):
        w.writerow(["%.2f" % x, "%.2f" % y, (clases or nom)[v], "0.8"])
    datos = buf.getvalue().encode("utf-8")
    with open(os.path.join(d, "P-HE.%s.csv" % clasif), "wb") as f:
        f.write(datos)
    meta = {"modelo": modelo, "version": version, "clasificador": clasif,
            "lamina": "P-HE", "mpp_l0": MPP0, "n": len(xy),
            "sha256_csv": hashlib.sha256(datos).hexdigest(), "mapa_clases": mapa}
    with open(os.path.join(d, "P-HE.%s.json" % clasif), "w") as f:
        json.dump(meta, f)


class TestCellvitpp(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="he-cv-")
        self.d = os.path.join(self.base, "nube", "cellvitpp")
        os.makedirs(self.d)
        self.xy = np.array([[100.0, 100.0], [200.0, 300.0]])
        self.v = ["neo", "inf"]

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_valido(self):
        _escribe_cellvitpp(self.d, self.xy, self.v)
        ok, malos = H.lee_cellvitpp(self.base, (1000, 1000), MPP0)
        self.assertEqual((list(ok), malos), (["cellvitpp_nucls_super"], {}))
        r = ok["cellvitpp_nucls_super"]
        self.assertEqual(H.codigos("x", r["clase"], r["mapa"]).tolist(), [H.NEO, H.INF])
        self.assertEqual(r["clase"].tolist(), ["neoplastic", "inflammatory"])   # ya traducidas
        self.assertEqual(H.frases_prohibidas({k: v for k, v in r.items() if k not in ("centroide", "clase",
                                                                                       "prob")}), [])

    def test_rechazos_fail_closed(self):
        casos = {"cabecera": dict(cabecera=["x", "y", "clase", "prob"]),
                 "version": dict(version="1.0.8"),
                 "modelo": dict(modelo="CellViT-256-x40"),
                 "clase sin mapa": dict(clases={"neo": "Tumor", "inf": "desconocida"}),
                 "clase fuera de la tabla": dict(mapa={"Tumor": "neoplastic", "sTIL": "inflammatory",
                                                       "inventada": "other"})}
        for nombre, kw in casos.items():
            _escribe_cellvitpp(self.d, self.xy, self.v, **kw)
            ok, malos = H.lee_cellvitpp(self.base, (1000, 1000), MPP0)
            self.assertEqual(ok, {}, nombre)
            self.assertIn("cellvitpp_nucls_super", malos, nombre)
            self.assertEqual(H.frases_prohibidas(malos), [], nombre)            # el motivo no cita valores
            self.assertNotIn("desconocida", malos["cellvitpp_nucls_super"], nombre)
        _escribe_cellvitpp(self.d, self.xy, self.v)
        with open(os.path.join(self.d, "P-HE.nucls_super.csv"), "a") as f:
            f.write("1,1,sTIL,0.5\n")                          # el sha256 ya no casa
        ok, malos = H.lee_cellvitpp(self.base, (1000, 1000), MPP0)
        self.assertIn("sha256", malos["cellvitpp_nucls_super"])
        _escribe_cellvitpp(self.d, self.xy, self.v)
        ok, malos = H.lee_cellvitpp(self.base, (150, 1000), MPP0)   # x=200 fuera de 150
        self.assertIn("coordenadas", malos["cellvitpp_nucls_super"])

    def test_tabla_de_clases_casa_con_el_checkpoint_sellado(self):
        """`CELLVITPP_CLASES` (índice → nombre crudo) es exactamente `mapa_origen.clases` del
        config.json sellado de nube_n1 (la config de cada checkpoint), y sus descripciones están
        limpias: si la receta cambia de clases, aquí se ve antes de pagar una máquina."""
        with open(os.path.join(TOOLS, "nube_n1_cloudinit", "config.json"), encoding="utf-8") as f:
            s = json.load(f)["scaleway"]
        self.assertEqual(sorted(H.CELLVITPP_CLASES), sorted(s["clasificadores"]))
        for c, origen in s["mapa_origen"]["clases"].items():
            self.assertEqual({str(i): crudo for crudo, (i, _d) in H.CELLVITPP_CLASES[c].items()}, origen, c)
            self.assertEqual(sorted(s["mapa_clases"][c]), sorted(H.CELLVITPP_CLASES[c]), c)
            descr = [d for _i, d in H.CELLVITPP_CLASES[c].values()]
            self.assertEqual(H.frases_prohibidas(descr), [], c)
            self.assertFalse(any(crudo in d for crudo in H.CELLVITPP_CLASES[c] for d in descr), c)
        self.assertNotIn("sTIL", json.dumps(H.CELLVITPP))

    def test_orden_escribe_el_formato(self):
        out = H.cellvitpp(self.base, log=_silencio)
        self.assertEqual(out["enganche"]["salida"]["csv_columnas"], ["x_l0", "y_l0", "clase",
                                                                     "prob"])
        self.assertTrue(os.path.isfile(os.path.join(self.base, "he", "cellvitpp_formato.json")))
        self.assertEqual(H.frases_prohibidas(H.CELLVITPP), [])


# ── hueso ─────────────────────────────────────────────────────────────────────────────────────
TRAB = ((100.0, 180.0), (600.0, 680.0))               # franjas trabeculares (y, µm)
ZONA_GRASA = (800.0, 1300.0, 260.0, 520.0)


def hueso_sintetico(semilla=3):
    m16 = 4.0
    h, w = 200, 400                                    # 0,8 × 1,6 mm
    yy, xx = (np.mgrid[0:h, 0:w] + 0.5) * m16
    rng = np.random.default_rng(semilla)
    tej = (xx >= 40) & (xx < 1560) & (yy >= 40) & (yy < 760)
    cH = np.where(tej, 0.3 + 0.9 * (rng.random((h, w)) < 0.5), 0.0)
    cE = np.where(tej, 0.2, 0.0)
    trab = np.zeros_like(tej)
    for a, b in TRAB:
        trab |= tej & (yy >= a) & (yy < b)
    cH[trab] = 0.05
    cE[trab] = 0.9 + rng.normal(0, 0.01, int(trab.sum()))
    x0, x1, y0, y1 = ZONA_GRASA
    zg = (xx >= x0) & (xx < x1) & (yy >= y0) & (yy < y1)
    cH[zg] = 0.0
    cE[zg] = 0.3                                       # septos
    disco = np.zeros_like(tej)
    for cx, cy in _adipocitos(x0, x1, y0, y1, r=32.0, paso=72.0):
        disco |= (xx - cx) ** 2 + (yy - cy) ** 2 < 32.0 ** 2
    cE[disco & zg] = 0.0
    img = pinta(cH, cE)
    verdad = {"trab": float(trab.sum()) * m16 ** 2 / 1e6, "grasa": float(zg.sum()) * m16 ** 2 / 1e6,
              "tejido": float(tej.sum()) * m16 ** 2 / 1e6}
    return img, verdad


class TestHueso(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="he-hueso-")
        img, self.verdad = hueso_sintetico()
        dims = (400 * 16, 200 * 16)
        man = {"laminas": {"B-HE-1": {"mpp": MPP0, "nivel_atribucion": ["sintética"]},
                           "B-HE-2": {"mpp": MPP0, "nivel_atribucion": ["no atribuible"]}}}
        self.lector = LectorSint({n: LaminaSint(n, {16: img}, dims) for n in H.HUESO}, man)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_composicion_conocida(self):
        out = H.hueso(self.base, "B-HE-1", self.lector, log=_silencio)
        v = self.verdad
        pct = out["composicion"]["pct"]
        esperado = {"trabecular bone": 100 * v["trab"] / v["tejido"],
                    "adipocytes": 100 * v["grasa"] / v["tejido"]}
        esperado["medullary space (non-adipocyte)"] = 100 - sum(esperado.values())
        for k, e in esperado.items():
            self.assertAlmostEqual(pct[k], e, delta=6.0, msg="%s: %.1f vs %.1f" % (k, pct[k], e))
        for k, r in out["composicion"]["rango_sensibilidad"].items():
            self.assertLessEqual(r["min"], pct[k])
            self.assertGreaterEqual(r["max"], pct[k])
        self.assertEqual(out["rotulo"], "reported negative for neoplasia by two independent "
                                        "pathology reads; not screened for tumour by Polaris")
        self.assertEqual(out["qc"]["seccion"], "fraction of the section scanned: unknown, no macro "
                                               "image")
        self.assertGreater(out["qc"]["foco"]["n_teselas"], 0)
        self.assertEqual(out["qc"]["fragmentos"], 1)

    def test_sin_celulas_y_b_he_2_no_atribuible(self):
        out = H.hueso(self.base, "B-HE-2", self.lector, log=_silencio)
        self.assertEqual(out["atribucion"], "no atribuible")
        self.assertIn("not attributable", out["rotulo"])
        self.assertIn("not screened for tumour by Polaris", out["rotulo"])
        self.assertEqual(out["alcance_analisis"], H.ROTULOS["hueso_sin_celulas"])

        def claves(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    yield k
                    yield from claves(v)
        for k in claves(out):
            self.assertNotRegex(k.lower(), r"nucle|celul|cell|recuento|count", k)
        # ningún modelo: el hueso solo lee píxeles al mpp de composición y teselas de foco en L0
        self.assertTrue({f for _, f, _, _ in self.lector.lecturas} <= {16, 1})
        self.assertEqual(H.frases_prohibidas(out), [])

    def test_lamina_no_osea_rechazada(self):
        with self.assertRaises(ValueError):
            H.hueso(self.base, "P-HE", self.lector)


# ── exploratorio ─────────────────────────────────────────────────────────────────────────────
def _fc_piloto(base):
    """Dos FC en el marco de P-CK19 (= el de P-HE, matriz identidad): mitad izquierda y derecha."""
    mpp = 8.0
    h, w = int(ALTO_UM / mpp), int(ANCHO_UM / mpp)
    xx = (np.arange(w) + 0.5) * mpp
    a = np.zeros((h, w), bool)
    a[:, xx < 575] = True
    b = np.zeros((h, w), bool)
    b[:, xx >= 575] = True
    d = os.path.join(base, "piloto", "registro")
    os.makedirs(d, exist_ok=True)
    np.savez_compressed(os.path.join(d, "fc_piloto.npz"), ids=np.array(["FCA", "FCB"]),
                        mascaras=np.stack([a, b]), mpp=np.array(mpp),
                        areas=np.array([a.sum(), b.sum()]) * mpp ** 2)
    return ["FCA", "FCB"]


def _registro_iii(base, pasan):
    rel = os.path.join("piloto", "registro", "P-HE.json")
    C.escribe_json(os.path.join(base, rel), {
        "fcs_pasan": pasan, "pasa_algun_fc": bool(pasan),
        "ck19_he": {"fragmentos": [{"id": f, "pasa": True, "matriz_um": np.eye(3).tolist()}
                                   for f in ("FCA", "FCB")]}})
    C.marca_hecho(base, "registro-par", "P-HE", "P-CK19", productos=[rel])


def _metricas(base, marcador, fn_pct, L=100.0):
    regs = {}
    for f, (x0, x1) in (("FCA", (0, 575)), ("FCB", (575, 1200))):
        for i in range(int(x0 // L), int(math.ceil(x1 / L))):
            for j in range(0, int(ALTO_UM // L)):
                regs["%s:%d,%d" % (f, i, j)] = {"k": 0, "n": 100,
                                                "pct": fn_pct((i + 0.5) * L, (j + 0.5) * L)}
    d = os.path.join(base, "piloto", "metricas")
    os.makedirs(d, exist_ok=True)
    C.escribe_json(os.path.join(d, "%s.json" % marcador),
                   {"estado": "registrado", "L_um": L, "T": 0.1, "denominador": "principal",
                    "regiones": regs})


def ext_senal(teselas, coords, vecinos, patch_lv0, log=print):
    rng = np.random.default_rng(5)
    y = (coords[:, 1] + patch_lv0 / 2) * MPP0 / ALTO_UM
    e = rng.normal(0, 1, (len(coords), 8))
    e[:, :4] = y[:, None] + rng.normal(0, 0.01, (len(coords), 4))
    return e, {"stub": "senal"}


def ext_ruido(teselas, coords, vecinos, patch_lv0, log=print):
    return np.random.default_rng(6).normal(0, 1, (len(coords), 8)), {"stub": "ruido"}


class TestExplora(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="he-explora-")
        self.lector, xy, verdad = sesion_he(self.base)
        cl = {k: stub_clasificador(k, xy, verdad, semilla=i) for i, k in enumerate(H.PRINCIPAL)}
        H.primario(self.base, self.lector, cl, stub_tumor, log=_silencio)
        _fc_piloto(self.base)
        self._orig = dict(H.EXPLORA_P)
        H.EXPLORA_P["tesela_px"] = 256                    # 128 µm: más teselas en la P-HE pequeña
        H.EXPLORA_P["n_boot"] = 400

    def tearDown(self):
        H.EXPLORA_P.clear()
        H.EXPLORA_P.update(self._orig)
        shutil.rmtree(self.base, ignore_errors=True)

    def test_senal_ruido_y_simetria_ne(self):
        _registro_iii(self.base, ["FCA", "FCB"])
        rng = np.random.default_rng(9)
        _metricas(self.base, "P-KI67", lambda x, y: 10 + 60 * y / ALTO_UM)
        _metricas(self.base, "P-SYN", lambda x, y: 70 - 60 * y / ALTO_UM)     # NE, con señal
        _metricas(self.base, "P-RE", lambda x, y: float(rng.uniform(0, 100)))
        _metricas(self.base, "P-CHGA", lambda x, y: 1.0)                       # NE, casi uniforme
        out = H.explora(self.base, self.lector, {"senal": ext_senal, "ruido": ext_ruido},
                        log=_silencio)
        self.assertEqual(out["estado"], "hecho")
        self.assertEqual(out["rotulo"], H.ROTULOS["explora"])
        self.assertEqual(sorted(out["fragmentos"]), ["FCA", "FCB"])
        res = out["resultados"]
        self.assertEqual(res["senal"]["P-KI67"]["estado"], "signal")
        self.assertEqual(res["senal"]["P-SYN"]["estado"], "signal")
        self.assertEqual(res["ruido"]["P-KI67"]["estado"], "no signal")
        self.assertEqual(res["ruido"]["P-KI67"]["rotulo"], H.ROTULOS["sin_senal"])
        self.assertEqual(out["conclusion"]["P-RE"], H.ROTULOS["sin_senal"])     # se dice
        self.assertIn("near-uniform", res["senal"]["P-CHGA"]["motivo"])
        self.assertIn("near-uniform", out["conclusion"]["P-CHGA"])
        self.assertFalse(out["etiquetas"]["P-{{DIANA3}}"]["disponible"])
        self.assertTrue(out["conclusion"]["P-{{DIANA3}}"].startswith(H.ROTULOS["no_estimable"]))
        # simetría NE: todos los marcadores (NE incluidos) pasan por el mismo análisis y salida
        for ext in ("senal", "ruido"):
            self.assertEqual(set(res[ext]), set(H.MARCADORES_EXPLORA))
        self.assertEqual(set(res["senal"]["P-SYN"]), set(res["senal"]["P-KI67"]))
        self.assertEqual(set(out["conclusion"]), set(H.MARCADORES_EXPLORA))
        for m in H.MARCADORES_NE:
            self.assertIn(m, out["etiquetas"])
            self.assertTrue(out["etiquetas"][m]["ne"] if out["etiquetas"][m]["disponible"]
                            else True)
        # validación dejando fuera un fragmento ENTERO
        for r in (res["senal"]["P-KI67"], res["ruido"]["P-RE"]):
            fuera = {p["fuera"] for p in r["pliegues"]}
            self.assertEqual(fuera, {"FCA", "FCB"})
            for p in r["pliegues"]:
                self.assertNotIn(p["fuera"], p["fragmentos_entrenamiento"])
        self.assertIn("trivial_media", res["senal"]["P-KI67"]["mae"])
        self.assertIn("trivial_densidad", res["senal"]["P-KI67"]["mae"])
        self.assertEqual(H.frases_prohibidas(out), [])

    def test_sin_fragmento_registrado_cae_y_lo_dice(self):
        _registro_iii(self.base, [])
        out = H.explora(self.base, self.lector, {"senal": ext_senal}, log=_silencio)
        self.assertEqual((out["estado"], out["motivo"]), ("caido", H.ROTULOS["explora_caido"]))

    def test_sin_iii_falta(self):
        with self.assertRaises(H.FaltaEntrada):
            H.explora(self.base, self.lector, {"senal": ext_senal}, log=_silencio)


# ── despacho, códigos y ventanilla ────────────────────────────────────────────────────────────
class TestDespacho(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="he-rc-")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _rc(self, args, lector=None, **kw):
        with redirect_stderr(io.StringIO()):
            return H.ejecuta(self.base, args, lector, log=_silencio, **kw)

    def test_codigos(self):
        self.assertEqual(self._rc([]), H.RC_USO)
        self.assertEqual(self._rc(["borra"]), H.RC_USO)
        self.assertEqual(self._rc(["primario", "--rapido"]), H.RC_USO)
        self.assertEqual(self._rc(["primario", "P-KI67"]), H.RC_USO)
        self.assertEqual(self._rc(["hueso", "P-HE"], LectorSint({})), H.RC_USO)
        lector, _, _ = sesion_he(self.base, con_sello=False)
        self.assertEqual(self._rc(["primario"], lector), H.RC_SELLO)        # sin sello no mide
        shutil.rmtree(self.base)
        os.makedirs(self.base)
        lector, _, _ = sesion_he(self.base, con_nucleos=False)
        self.assertEqual(self._rc(["primario"], lector), H.RC_FALTA)        # sin InstanSeg
        self.assertEqual(self._rc(["explora"], lector), H.RC_FALTA)         # sin primario

    def test_ventanilla_y_lector_clinico(self):
        import laminillas_ventanilla as V
        import lector_clinico as L
        c = V.CONF["laminillas_he"]
        self.assertEqual((c["script"], c["venv"], c["perfil"], c["modo"], c["datos"]),
                         ("tools/laminillas_he.py", "patologia", "analisis", None, True))
        self.assertEqual(tuple(c["ordenes"]), H.ORDENES)
        self.assertEqual(V.mem_de(c, ["primario"]), 9)
        self.assertEqual(V.mem_de(c, ["explora"]), 9)
        self.assertEqual(V.mem_de(c, ["hueso", "B-HE-1"]), 5)
        self.assertEqual(V.mem_de(c, ["cellvitpp"]), 0)
        for a in (["primario"], ["hueso", "B-HE-1", "B-HE-2"], ["explora"], ["cellvitpp"]):
            self.assertIsNone(V.valida_args(a), a)
            self.assertIsNone(V.valida_orden(c, a), a)
        for a in ([], ["borra"], ["P-HE"]):
            self.assertIsNotNone(V.valida_orden(c, a), a)
        self.assertEqual(L.LAMINILLAS["laminillas_he"][0], c["script"])
        self.assertIn("/patologia/", L.LAMINILLAS["laminillas_he"][1])

    def test_main_solo_por_la_ventanilla(self):
        with self.assertRaises(SystemExit):
            H.main(["cellvitpp"])


class TestPesos(unittest.TestCase):
    def test_pesos_json_manipulado_no_carga(self):
        import laminillas_segmenta as S
        d = tempfile.mkdtemp(prefix="he-pesos-")
        try:
            p = os.path.join(d, "pesos.json")
            shutil.copy(H.PESOS, p)
            shutil.copy(H.PESOS + ".sha256", p + ".sha256")
            with open(p, "a") as f:
                f.write(" ")
            with self.assertRaises(S.PesoNoSellado):
                H.peso("histoplus", "histoplus_cellvit_segmentor_40x.pt", pesos=p)
            # pesos.json íntegro, pero el fichero de la caché no es el sellado
            shutil.copy(H.PESOS, p)
            ent = H._pesos(p)["histoplus"]
            falso = os.path.join(d, "hub", "models--Owkin-Bioptimus--histoplus", "snapshots",
                                 ent["commit"], "config.json")
            os.makedirs(os.path.dirname(falso))
            with open(falso, "w") as f:
                f.write("{}")
            with self.assertRaises(S.PesoNoSellado):
                H.peso("histoplus", "config.json", pesos=p, hf_home=d)
            with self.assertRaises(S.PesoNoSellado):
                H.refs_main_sellado("histoplus", pesos=p, hf_home=d)    # sin refs/main
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_frases_prohibidas(self):
        self.assertEqual(H.frases_prohibidas({"a": "still fine"}), [])
        self.assertIn("sTIL", H.frases_prohibidas(["stromal sTIL score"]))
        self.assertIn("tumour cells", H.frases_prohibidas({"x": "count of tumour cells"}))


@unittest.skipUnless(os.path.isdir(os.path.join(HF, "hub")), "sin caché de pesos (CI/portable)")
class TestModelosReales(unittest.TestCase):
    """Forward de los modelos REALES sobre una tesela SINTÉTICA, con pesos verificados."""

    @classmethod
    def setUpClass(cls):
        import laminillas_humo as HU
        cls.img = HU.imagen_sintetica()
        cls.HU = HU

    def test_histoplus_y_nulite(self):
        for clave, lado, nclases in (("histoplus", 448, 15), ("nulite", 512, 6)):
            r = H.forward_tesela(clave, self.HU._tesela_uint8(self.img, lado))
            self.assertEqual(r["probabilidad"][1:], (nclases, lado, lado), clave)
            self.assertEqual(len(r["clases"]), nclases)
            self.assertEqual(set(r["clases"]) - {"Background"}, set(H.A_COMUN[clave]))
        self.assertFalse(H.modelo_histoplus()[1]["xformers"])           # sin xformers

    def test_wsinfer(self):
        import torch
        red, cfg, med = H.modelo_wsinfer()
        self.assertEqual((cfg["patch_size_pixels"], cfg["spacing_um_px"]), (350, 0.25))
        x = np.stack([self.HU._tesela_uint8(self.img, 350)] * 2)
        with torch.inference_mode():
            y = torch.softmax(red(H.transforma_wsinfer(x, cfg)), 1)
        self.assertEqual(tuple(y.shape), (2, 2))
        self.assertEqual(cfg["class_names"], ["Other", "Tumor"])

    @unittest.skipUnless(os.environ.get("BTP_HE_LENTO") == "1", "lento: BTP_HE_LENTO=1")
    def test_lazyslide_sobre_tiff_sintetico(self):
        """HistoPLUS y NuLite por LazySlide sobre L0 de un TIFF con la estructura del Grundium."""
        d = tempfile.mkdtemp(prefix="he-wsi-")
        try:
            ruta = os.path.join(d, "sint.tiff")
            self.HU.escribe_grundium(ruta, self.img)

            class Lam:
                opaco, ruta, mpp_l0, dimensiones_l0 = "P-HE", None, self.HU.MPP, self.HU.L0
            Lam.ruta = ruta

            class Lec:
                def abre(self, n):
                    return Lam

                def zona_escaneada(self, lam):
                    w, h = lam.dimensiones_l0
                    return box(0, 0, w, h).difference(box(w - 1024, 0, w, 1024))

                def manifiesto(self):
                    return {"laminas": {}}
            for clave in H.PRINCIPAL:
                r = H.clasifica_wsi(Lec(), "P-HE", clave, os.path.join(d, "trabajo"),
                                    log=_silencio)
                self.assertGreater(r["n"], 100, clave)
                self.assertEqual(r["teselado"]["magnificacion"], "40x")
                self.assertAlmostEqual(r["teselado"]["mpp"], self.HU.MPP, places=4)   # L0
                self.assertTrue(set(r["clase"].tolist()) <= set(H.A_COMUN[clave]), clave)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    @unittest.skipUnless(os.environ.get("BTP_HE_LENTO") == "1", "lento: BTP_HE_LENTO=1")
    def test_fundacionales(self):
        teselas = np.stack([self.img[y:y + 512, x:x + 512] for y, x in ((800, 800), (800, 1312))])
        coords = np.array([[0, 0], [1024, 0]], np.int64)
        vec = H._vecinos(np.array([[0, 0], [0, 1]]), np.array(["F", "F"], dtype=object), 1)
        for clave, dim in (("uni2h", 1536), ("hoptimus1", 1536), ("titan", 768)):
            e, meta = H.EXTRACTORES[clave](teselas, coords, vec, 1024)
            self.assertEqual(e.shape, (2, dim), clave)
            self.assertTrue(np.isfinite(e).all())


if __name__ == "__main__":
    unittest.main(verbosity=1)
