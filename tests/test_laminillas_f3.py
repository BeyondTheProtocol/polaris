#!/usr/bin/env python3
"""tests/test_laminillas_f3.py — F3 parte A de las laminillas (`tools/laminillas_f3.py`): FC de
consenso, puerta de p63, (a-bis) y su gemela NE, (b) lectura digital y ROI de Carlos, como órdenes
de `laminillas_proc` por la ventanilla.

TODO SINTÉTICO: ni láminas reales, ni el pptx real, ni rutas clínicas. Tres montajes:
  · PRODUCTOS: una SESION de productos (sello, núcleos, objetos, máscara CK19, regla, L y consenso)
    con geometría y positividad CONOCIDAS: dos FC, tres estructuras CK19+ (A sin p63, B con anillo
    p63, C en el FC sin control), regiones pobres en RE sembradas y una región rica en SYN.
  · CONSENSO: 11 cortes seriados pintados (`_laminillas_sinteticas`) con giros, un espejo y dos
    fragmentos borrados en 5 y 6 cortes: la regla «≥6 de 11» decide qué FC existe.
  · ROI: láminas sintéticas distintas y un pptx mínimo con capturas reescaladas, una de ruido y una
    de dos láminas idénticas (la regla «≥2× la segunda»).
Corre con el intérprete del venv `patologia` (se re-ejecuta con él); sin venv, SKIP (rc 77).
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

VENV_DIR = os.environ.get("BTP_VENV_PATOLOGIA") or os.path.expanduser("~/.polaris-venvs/patologia")
VENV_PY = os.path.join(VENV_DIR, "bin", "python")
if os.path.realpath(sys.prefix) != os.path.realpath(VENV_DIR):
    if os.path.exists(VENV_PY) and not os.environ.get("BTP_F3_REEXEC"):
        os.environ["BTP_F3_REEXEC"] = "1"
        os.execv(VENV_PY, [VENV_PY, os.path.abspath(__file__)] + sys.argv[1:])
    print("SKIP: falta el venv patologia (%s)" % VENV_DIR)
    sys.exit(77)

import numpy as np  # noqa: E402
from shapely.geometry import LineString, Point, box  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _laminillas_sinteticas as S  # noqa: E402
import laminillas_comun as C  # noqa: E402
import laminillas_congela as K  # noqa: E402
import laminillas_f3 as F  # noqa: E402
import laminillas_metricas as MET  # noqa: E402
import laminillas_proc as P  # noqa: E402
import laminillas_registro as R  # noqa: E402
import laminillas_segmenta as SG  # noqa: E402
import laminillas_sello as SL  # noqa: E402

FECHA = "2026-10-02"
T = 0.10
Q = dict(log=lambda m: None)
NUNCA = ["solo mirasteis una roi", "qué hospital acertó", "perdió el re", "re-negative clone",
         "er-low", "er-negative", "negative-control slide", "polaris confirma",
         "compatibles con la heterogeneidad", "lámina entera", "whole slide", "tumour cells",
         "tumor cells", "celularidad", "pureza", "coexpresión", "co-expression",
         "% of invasive carcinoma"]


def _unit(v):
    v = np.asarray(v, float)
    return (v / np.linalg.norm(v)).tolist()


def sella(base, rige="clasicas"):
    """Sello sintético con el formato de `laminillas_congela` (el de la puerta del piloto)."""
    h, d = _unit([0.65, 0.70, 0.29]), _unit([0.27, 0.57, 0.78])
    reg = {"p999": 0.04, "rige": "suelo_fijo", "rotulos": [], "T": T, "banda": [0.06, 0.15],
           "estado": "ok"}
    fp = {"k": 1, "n": 2000, "fraccion": 0.0005, "ic95": [0.0, 0.002]}
    c = {"vectores": {"H": h, "DAB": d, "tercero": _unit(np.cross(h, d))},
         "residuo": {"por_lamina": {n: {"supera": False} for n in K.IHQ_CON_TEJIDO}},
         "precongelacion": {"sha256": "sintetico"}, "parametros": K.parametros(),
         "umbral": {"T": {"nucleo": T, "anillo": T},
                    "banda": {"nucleo": [0.06, 0.15], "anillo": [0.06, 0.15]},
                    "rige": rige, "rotulos": [],
                    "regimenes": {rige: {"nucleo": dict(reg), "anillo": dict(reg)}}},
         "fp_her2": {"nucleo": fp, "anillo": fp}, "registro": K.REGISTRO, "hotspot": K.HOTSPOT,
         "regla_L": K.REGLA_L, "modulo_b": MET.contenido_partida(),
         "semillas": {"maestra": 20261001, "bootstrap": 20261001, "pixeles": 20261101,
                      "galeria_focal": 20261201},
         "medida": {"hscore": {"cortes": ["T", 0.4, 0.6]}, "semilla_galeria_focal": 20261201}}
    return SL.sella(os.path.join(base, SL.FICHERO), c, FECHA)


def _sin_nunca(test, obj):
    test.assertEqual(MET.barre_nunca(obj), [])
    texto = " ".join(MET._cadenas(obj)).lower()
    for f in NUNCA:
        test.assertNotIn(f, texto, f)


def M_de(tx, ty):
    return np.array([[1, 0, tx], [0, 1, ty], [0, 0, 1.0]])


# ══ A. Órdenes, ventanilla y despacho (sin datos) ═════════════════════════════════════════════
class Ordenes(unittest.TestCase):

    def test_lista_cerrada_de_la_ventanilla_y_jaula_de_la_roi(self):
        import laminillas_ventanilla as V
        import lector_clinico as LC
        self.assertEqual(P.ORDENES_F3, ("consenso", "puerta-p63", "regiones-pobres",
                                        "lectura-digital"))
        for o in P.ORDENES_F3:
            self.assertIn(o, P.ORDENES)
        c = V.CONF["laminillas"]
        self.assertEqual(tuple(c["ordenes"]), P.ORDENES)
        r = V.CONF["laminillas_roi"]
        # la ROI lee el pptx de ORIGEN: SOLO en la jaula de la ingesta, nunca en la de análisis
        self.assertEqual((r["script"], r["modo"], r["perfil"]),
                         ("tools/laminillas_proc.py", "analisis", "analisis-ingesta"))
        self.assertEqual(tuple(r["ordenes"]), P.ORDENES_ORIGEN)
        self.assertNotIn("roi-carlos", P.ORDENES)
        self.assertIsNotNone(V.valida_orden(c, ["roi-carlos"]))
        self.assertIsNone(V.valida_orden(r, ["roi-carlos"]))
        self.assertIsNotNone(V.valida_orden(r, ["consenso"]))
        self.assertIn("laminillas_roi", LC.LAMINILLAS)
        self.assertEqual(LC.LAMINILLAS["laminillas_roi"][0], r["script"])
        # memoria: las de F3 piden la del registro (5); `consenso --valis`, la de VALIS (9)
        self.assertEqual(V.mem_de(c, ["consenso"]), 5)
        self.assertEqual(V.mem_de(c, ["consenso", "--valis"]), 9)
        for o in P.ORDENES_F3:
            self.assertIsNone(V.valida_args([o, "P-RE", "--valis"]), o)

    def test_despacho_rechaza_sin_tocar_nada(self):
        d = tempfile.mkdtemp()
        viejo = os.environ.pop("BTP_VENTANILLA", None)
        try:
            an = P.analisis
            self.assertEqual(an(d, ["consenso", "P-KI67"], **Q), 2)
            self.assertEqual(an(d, ["consenso", "--otra"], **Q), 2)
            self.assertEqual(an(d, ["puerta-p63", "P-HE"], **Q), 2)
            self.assertEqual(an(d, ["regiones-pobres"], **Q), 2)
            self.assertEqual(an(d, ["regiones-pobres", "P-KI67"], **Q), 2)
            self.assertEqual(an(d, ["lectura-digital", "P-HE"], **Q), 2)
            self.assertEqual(an(d, ["lectura-digital", "P-RE", "P-KI67"], **Q), 2)
            self.assertEqual(an(d, ["roi-carlos", "P-RE"], **Q), 2)
            # fuera de la ventanilla, roi-carlos no toca ORIGEN: se niega antes de mirar nada
            self.assertEqual(an(d, ["roi-carlos"], **Q), 2)
            self.assertEqual(an(d, ["consenso"], **Q), 3)                # sin sello: NO MIDO
            self.assertEqual(an(d, ["lectura-digital", "P-RE"], **Q), 3)
            # las del piloto siguen en su despacho, sin cambios
            self.assertEqual(an(d, ["piloto-i", "P-RE"], **Q), 2)
            self.assertEqual(an(d, ["metricas", "P-KI67"], **Q), 3)
            self.assertEqual(an(d, ["borra"], **Q), 2)
            self.assertEqual(os.listdir(d), [])                          # nada escrito
        finally:
            if viejo is not None:
                os.environ["BTP_VENTANILLA"] = viejo
            shutil.rmtree(d, ignore_errors=True)

    def test_parametros_inferidos_declarados(self):
        m = F.metodos()
        self.assertIn("ne_rica_pct", m["parametros_inferidos"])
        self.assertIn("p63_periferia_min_nucleos", m["parametros_inferidos"])
        self.assertNotIn("roi_min_inliers", m["parametros_inferidos"])     # del plan
        self.assertEqual(F.PARAMS_F3["roi_escala"], [0.25, 4.0])
        self.assertEqual((F.PARAMS_F3["roi_min_inliers"], F.PARAMS_F3["roi_margen"]), (30, 2.0))


# ══ B. Productos sintéticos: p63, (a-bis), gemela NE y (b) ════════════════════════════════════
MPP = 0.25
DIMS = (16800, 6400)                     # px L0 (4200 × 1600 µm)
FCS = {"FC1": box(100, 100, 1700, 1300), "FC2": box(1900, 100, 3500, 1300)}
ESTR = {"A": box(400, 400, 800, 800),     # FC1, sin p63: «invasivo»
        "B": box(1000, 400, 1400, 800),   # FC1, anillo p63: control positivo interno
        "C": box(2200, 400, 2600, 800)}   # FC2, sin control: «not assessable»
L_UM = 200.0
MOV = {"P-CK19": np.eye(3), "P-KI67": M_de(60.0, -40.0), "P-RE": M_de(100.0, -50.0),
       "P-SYN": M_de(-80.0, 30.0), "P-P63": M_de(40.0, 70.0)}
RE_POBRES = {(2, 2), (5, 2), (11, 2)}     # (i, j) de la rejilla L = 200 µm
SYN_RICA = (3, 3)


def _celda(xy):
    return np.floor(xy / L_UM).astype(int)


def _genera(rng):
    """Núcleos en µm de la referencia: fondo 3000/mm² en los FC y epitelio extra 6000/mm² en las
    estructuras CK19+. `epi` = dentro de una estructura."""
    pts = []
    for fc in FCS.values():
        x0, y0, x1, y1 = fc.bounds
        n = rng.poisson(fc.area / 1e6 * 3000)
        pts.append(np.c_[rng.uniform(x0, x1, n), rng.uniform(y0, y1, n)])
    for e in ESTR.values():
        x0, y0, x1, y1 = e.bounds
        n = rng.poisson(e.area / 1e6 * 6000)
        pts.append(np.c_[rng.uniform(x0, x1, n), rng.uniform(y0, y1, n)])
    xy = np.vstack(pts)
    epi = np.zeros(len(xy), bool)
    for e in ESTR.values():
        x0, y0, x1, y1 = e.bounds
        epi |= (xy[:, 0] > x0) & (xy[:, 0] < x1) & (xy[:, 1] > y0) & (xy[:, 1] < y1)
    return xy, epi


def _prob(nombre, xy, epi):
    c = _celda(xy)
    en = lambda s: np.array([tuple(v) in s for v in c], bool)  # noqa: E731
    if nombre == "P-RE":
        return np.where(epi, np.where(en(RE_POBRES), 0.03, 0.90), 0.0)
    if nombre == "P-KI67":
        return np.where(epi, 0.20, 0.05)
    if nombre == "P-SYN":
        return np.where(epi, np.where(en({SYN_RICA}), 0.95, 0.02), 0.0)
    return np.zeros(len(xy))


def escribe_lamina(base, nombre, rng):
    """Segmenta y objetos de UNA lámina (con su «hecho»), como los deja el piloto."""
    M = MOV[nombre]
    ref, epi = _genera(rng)
    pos = rng.random(len(ref)) < _prob(nombre, ref, epi)
    if nombre == "P-P63":                  # anillo mioepitelial alrededor de B (5 µm fuera)
        anillo = ESTR["B"].buffer(5).exterior
        d = np.linspace(0, anillo.length, 60, endpoint=False)
        extra = np.array([[anillo.interpolate(v).x, anillo.interpolate(v).y] for v in d])
        ref = np.vstack([ref, extra])
        epi = np.r_[epi, np.zeros(len(extra), bool)]
        pos = np.r_[pos, np.ones(len(extra), bool)]
    Minv = np.linalg.inv(M)
    lam = (ref @ Minv[:2, :2].T + Minv[:2, 2]) / MPP
    r = (np.where(epi, 4.2, 2.6) + rng.normal(0, 0.2, len(ref))) / MPP
    pols = [Point(x, y).buffer(rr, 8) for (x, y), rr in zip(lam, r)]
    cents = SG.centroides(pols)
    pxy, offs = SG.empaqueta(pols)
    os.makedirs(os.path.join(base, "segmenta"), exist_ok=True)
    np.savez_compressed(os.path.join(base, "segmenta", "nucleos_%s.npz" % nombre),
                        centroide=cents, area_um2=SG.areas_um2(pols, MPP), pol_xy=pxy,
                        pol_offs=offs)
    with open(os.path.join(base, "segmenta", "nucleos_%s.json" % nombre), "w") as f:
        json.dump({"lamina": nombre, "n": len(pols), "mpp_l0": MPP, "dimensiones_l0": list(DIMS),
                   "teselado": {"pasa": True}, "aviso_check_input_tile": False,
                   "area_nuclear": SG.mediana_area(pols, MPP)}, f)
    C.marca_hecho(base, "segmenta", nombre, productos=["segmenta/nucleos_%s.npz" % nombre,
                                                       "segmenta/nucleos_%s.json" % nombre])
    n = len(pols)
    senal = np.where(pos, 0.45, 0.02) + rng.normal(0, 0.01, n)
    fondo = 0.02 + rng.normal(0, 0.01, n)
    if nombre == "P-CK19":
        dab, dab_a = fondo, np.where(epi, 0.5, 0.02) + rng.normal(0, 0.01, n)
    elif SL.COMPARTIMENTO.get(nombre) == "anillo":
        dab, dab_a = fondo, senal
    else:
        dab, dab_a = senal, fondo
    prov = SG.mascara_provisional(cents, MPP, DIMS)
    rasgos, _ = P.rasgos_morfologicos(pxy, offs, MPP)
    z = np.zeros(n, bool)
    arr = dict(centroide=cents, area_um2=SG.areas_um2(pols, MPP), od_nucleo=np.zeros((n, 3)),
               od_anillo=np.zeros((n, 3)), dab_nucleo=dab, dab_anillo=dab_a,
               hema=np.full(n, 0.3), saturado=z, foco_bajo=z, pliegue=z,
               fragmento=SG.fragmento_de(prov, cents), borde_um=SG.distancia_borde(prov, cents),
               foco_tesela=np.ones(4), tejido_tesela=np.ones(4), origen_tesela=np.zeros((4, 2)),
               tesela=np.zeros(n, int), rasgos=rasgos, prov_fragmentos=prov["fragmentos"],
               pol_xy=pxy, pol_offs=offs)
    hu = P._huella(base, P._rels_nucleos(base, nombre) + ["congelacion.json"])
    P._npz(base, P._rel("objetos_%s.npz" % nombre), **arr)
    P._escribe(base, P._rel("objetos_%s.json" % nombre),
               {"lamina": nombre, "n": n, "mpp_l0": MPP, "dimensiones_l0": list(DIMS),
                "prov_f": prov["f"], "prov_mpp": prov["mpp"], "corte_tercil_foco": None})
    C.marca_hecho(base, "objetos", nombre, hu, productos=[P._rel("objetos_%s.npz" % nombre),
                                                         P._rel("objetos_%s.json" % nombre)])
    return {"ref": ref, "epi": epi, "pos": pos, "cent": cents}


def escribe_mascara_ck19(base):
    m = np.zeros((1600, 4200), bool)
    for e in ESTR.values():
        x0, y0, x1, y1 = (int(v) for v in e.bounds)
        m[y0:y1, x0:x1] = True
    P._npz(base, P._rel("ck19", "mascara.npz"), bits=np.packbits(m, axis=None),
           forma=np.asarray(m.shape))
    P._escribe(base, P._rel("ck19", "mascara.json"),
               {"lamina": "P-CK19", "origen_l0": [0.0, 0.0], "mpp": 1.0, "mpp_l0": MPP,
                "forma": list(m.shape), "umbral": {"regla": "valle", "T": 0.2, "rotulos": []},
                "sensibilidad": {"dependiente": False, "por_fragmento": {}}, "rotulos": [],
                "galeria_borde": []})


def fcs_sinteticos():
    out = []
    for fid, fc in FCS.items():
        mk = np.zeros((800, 2100), bool)
        x0, y0, x1, y1 = (int(v / 2.0) for v in fc.bounds)
        mk[y0:y1, x0:x1] = True
        out.append({"id": fid, "mascara": mk, "mpp": 2.0, "area_um2": float(mk.sum() * 4.0),
                    "poligono_um": R.mascara_a_poligono(mk, 2.0)})
    return out


def resultado(movil, M, fcs, pasa=None):
    r = R.ResultadoRegistro("P-CK19", movil, dict(R.PARAMS_PARTIDA), None, False)
    r.global_ = {"matriz_um": M, "espejo": False, "n_inliers": 120}
    for fc in fcs:
        ok = True if pasa is None else pasa.get(fc["id"], True)
        r.fragmentos.append({"id": fc["id"], "matriz_um": M, "pasa": ok,
                             "area_um2": fc["area_um2"], "metodo": "sift",
                             "evaluacion": {"p90_um": 12.0, "rige": "b", "pasa": ok,
                                            "rotulos": []}})
        r.fcs.append(fc)
    return r


class _LectorFalso:
    """Lector que no pinta: registra las lecturas (para la galería a L0)."""

    class _Lam:
        def __init__(self, n):
            self.nombre = self.opaco = n
            self.mpp_l0 = MPP
            self.dimensiones_l0 = DIMS
            self.niveles = [(1, MPP, DIMS)]

    def __init__(self):
        self.lecturas = []

    def abre(self, n):
        return self._Lam(n)

    def lee_region(self, lam, mpp, x, y, w, h):
        self.lecturas.append((lam.nombre, mpp, int(x), int(y), int(w), int(h)))
        return np.full((int(h), int(w), 3), 200, np.uint8)


def recall_falso(base, lector, sello, nombre):
    """Recall por clase «sin fallos», salvo en P-SYN dentro de FC2 (10 puntos): la mini-puerta
    de la gemela NE cae en FC2."""
    ob = P.carga_objetos(base, nombre)
    fr = np.asarray(ob["fragmento"])
    xy = np.asarray(ob["centroide"], float) * MPP
    M = MOV[nombre]
    ref = xy @ M[:2, :2].T + M[:2, 2]
    cob = {}
    for k in sorted(set(int(v) for v in fr if v >= 0)):
        mal = nombre == "P-SYN" and float(ref[fr == k, 0].mean()) > 1800
        cob["F%d|foco medio" % k] = {"dab": 0.85 if mal else 0.95, "h": 0.95}
    return {"cobertura": cob, "estratos_fuera": [], "f1_iou05": {}, "n_teselas": 40, "T": T,
            "declaracion": "sintético"}


class ProductosF3(unittest.TestCase):
    """p63, (a-bis), gemela NE y (b) sobre productos con verdad conocida."""

    @classmethod
    def setUpClass(cls):
        cls.base = os.path.realpath(tempfile.mkdtemp(prefix="lam-f3-prod-"))
        base = cls.base
        cls._recall = P.recall_por_clase
        P.recall_por_clase = recall_falso
        sella(base)
        escribe_mascara_ck19(base)
        rng = np.random.default_rng(23)
        cls.verdad = {n: escribe_lamina(base, n, rng) for n in MOV}
        P.regla_morfometrica(base, **Q)
        P._escribe(base, P._rel("L.json"), {"L_um": L_UM})
        fcs = fcs_sinteticos()
        res = {n: resultado(n, M, fcs) for n, M in MOV.items() if n != "P-CK19"}
        cent = {n: v["cent"] for n, v in cls.verdad.items()}
        cls.base_frag = F.persiste_consenso(
            base, res, {"sin_global": [], "origen_global": {}, "declaraciones": [],
                        "cierres": [], "orden": None, "valis_intentado": False},
            cent, {n: MPP for n in MOV}, L_UM, "fixturef3prod01", **Q)
        cls.lector = _LectorFalso()
        cls.p63 = F.puerta_p63(base, ["P-RE"], lector=cls.lector, **Q)
        cls.re = F.regiones_pobres(base, "P-RE", lector=cls.lector, **Q)
        cls.syn = F.regiones_pobres(base, "P-SYN", lector=cls.lector, **Q)
        P._escribe(base, F.REL_VISUAL, {
            "P-RE": {"pct": 85.0, "verificado_contra_original": True, "mismo_cristal": True,
                     "fuente": "lab B", "pagina": 1},
            "P-KI67": {"pct": 40.0, "verificado_contra_original": True, "mismo_cristal": False}})
        cls.lec = F.lectura_digital(base, "P-RE", lector=cls.lector, **Q)
        cls.lec_ki = F.lectura_digital(base, "P-KI67", lector=cls.lector, **Q)

    @classmethod
    def tearDownClass(cls):
        P.recall_por_clase = cls._recall
        shutil.rmtree(cls.base, ignore_errors=True)

    # ── base de consenso ────────────────────────────────────────────────────────────────────
    def test_base_de_fragmentos(self):
        b = self.base_frag
        self.assertEqual([f["id"] for f in b["fragmentos"]], ["FC1", "FC2"])
        f1 = b["fragmentos"][0]
        self.assertAlmostEqual(f1["area_mm2"], 1.92, delta=0.02)
        self.assertAlmostEqual(f1["area_interior_mm2"], 1.4 * 1.0, delta=0.03)   # −100 µm
        # rejilla de 200 µm anclada en (0, 0): FC1 = x 100-1700, y 100-1300 → 9 × 7 celdas
        self.assertEqual(f1["n_regiones"], 63)
        r = {x["region"]: x for x in f1["regiones"]}
        self.assertAlmostEqual(r["FC1:2,2"]["dist_borde_um"], 400.0, delta=1.0)
        self.assertTrue(r["FC1:2,2"]["interior"])
        self.assertFalse(r["FC1:0,0"]["interior"])
        self.assertEqual(f1["verificado_en"], sorted(MOV))
        self.assertIn("P-RA", f1["no_verificado_en"])
        dens = b["densidad_por_fc"]
        for n in MOV:                       # todas con densidad parecida: nadie «solo mapa»
            self.assertLess(abs(dens[n]["FC1"] / dens["P-CK19"]["FC1"] - 1), 0.15, n)
        self.assertEqual({k: v for k, v in b["parada"]["solo_mapa"].items() if v}, {})
        self.assertEqual(b["L_um"], L_UM)

    def test_consenso_hecho_y_cambio_detectado(self):
        c = F.carga_consenso(self.base)
        self.assertEqual([f["id"] for f in c["fcs"]], ["FC1", "FC2"])
        res, _ = F.resultado_consenso(self.base, "P-RE", c["fcs"])
        np.testing.assert_allclose(res.matriz("FC1"), MOV["P-RE"])

    # ── puerta de p63 ───────────────────────────────────────────────────────────────────────
    def test_puerta_p63_por_fragmento(self):
        inf = self.p63["puerta"]
        self.assertTrue(inf["puerta"]["FC1"]["pasa"])
        self.assertFalse(inf["puerta"]["FC2"]["pasa"])
        self.assertEqual(inf["puerta"]["FC2"]["rotulo"], "not assessable (no internal positive "
                                                         "control)")
        self.assertEqual(inf["fragmentos_que_pasan"], ["FC1"])
        self.assertAlmostEqual(inf["fraccion_area_cubierta"], 0.5, delta=0.01)
        clases = {}
        for e in inf["estructuras"]:
            cx = e["centro_um"][0]
            clases["A" if cx < 900 else "B" if cx < 1500 else "C"] = e["clase"]
        self.assertEqual(clases, {"A": "negativa", "B": "control_positivo",
                                  "C": "not assessable"})
        _sin_nunca(self, inf)

    def test_cifra_invasiva_solo_donde_hay_p63(self):
        """Verdad: RE sobre T en los núcleos de A (FC1, sin capa mioepitelial); B (con capa) y C
        (FC2, sin control) fuera. Tolerancia: ±4 puntos (erosión de 12 µm y azar)."""
        c = self.p63["P-RE"]
        v = self.verdad["P-RE"]
        enA = ((v["ref"][:, 0] > 412) & (v["ref"][:, 0] < 788) & (v["ref"][:, 1] > 412)
               & (v["ref"][:, 1] < 788))
        verdad = 100 * v["pos"][enA].mean()
        self.assertAlmostEqual(c["pct"], verdad, delta=4.0)
        self.assertEqual(c["fragmentos"], ["FC1"])
        self.assertIn("invasive-only where p63 available", c["rotulos"])
        self.assertAlmostEqual(c["fraccion_cubierta"], 2 / 3, delta=0.05)   # A+B de A+B+C
        _sin_nunca(self, c)

    # ── (a-bis) ─────────────────────────────────────────────────────────────────────────────
    def _cands(self, out):
        return {c["region"]: c for c in out["resultado"]["candidatas"]}

    def test_regiones_pobres_en_re_con_las_clases_del_plan(self):
        out = self.re
        self.assertTrue(out["resultado"]["evaluado"])
        c = self._cands(out)
        self.assertEqual(set(c), {"FC1:2,2", "FC1:5,2", "FC2:11,2"})
        a = c["FC1:2,2"]
        self.assertEqual(a["clase"], "ER-poor region within CK19+ epithelium, p63-negative")
        self.assertTrue(a["en_recuento"])
        self.assertEqual(a["rotulo"], "ER-poor region (<10 % of nuclei above T; k/n cells, 95 % "
                                      "CI; not an ASCO/CAP category)")
        self.assertGreaterEqual(a["n"], 50)
        self.assertLess(a["ic95"][0], a["pct"])
        self.assertGreater(a["ic95"][1], a["pct"])
        self.assertTrue(a["control_positivo_500um"])
        self.assertAlmostEqual(a["ki67_regional_pct"], 20.0, delta=6.0)
        mio = "CK19+ epithelium with myoepithelial layer or not assessable: benign or in-situ not " \
              "excluded"
        self.assertEqual((c["FC1:5,2"]["clase"], c["FC1:5,2"]["p63"]), (mio, "periferia_positiva"))
        self.assertEqual((c["FC2:11,2"]["clase"], c["FC2:11,2"]["p63"]), (mio, "not assessable"))
        self.assertFalse(c["FC1:5,2"]["en_recuento"] or c["FC2:11,2"]["en_recuento"])
        self.assertAlmostEqual(out["resultado"]["limite_deteccion"]["ic95_sup_k0_pct"], 7.1,
                               delta=0.05)
        self.assertEqual(out["minipuerta"]["FC1"]["pasa"], True)
        _sin_nunca(self, out)

    def test_galeria_a_l0_del_mismo_sitio(self):
        """La caja de FC1:2,2 (400-600 µm) ± 20 µm, llevada a P-RE (traslación 100, −50):
        x 280-520 µm, y 430-670 µm → px L0 1120-2080, 1720-2680."""
        g = self.re["galeria"]
        ind = P._lee(self.base, g["indice"])
        self.assertEqual(g["n"], 3)
        fila = {r["region"]: r for r in ind["regiones"]}["FC1:2,2"]
        self.assertEqual(set(fila["recortes"]), {"P-RE", "P-CK19", "P-P63", "P-KI67"})
        self.assertEqual(fila["recortes"]["P-RE"]["caja_l0"], [1120, 1720, 2080, 2680])
        self.assertEqual(fila["recortes"]["P-CK19"]["caja_l0"], [1520, 1520, 2480, 2480])
        self.assertIn(("P-RE", MPP, 1120, 1720, 960, 960), self.lector.lecturas)
        png = os.path.join(self.base, fila["recortes"]["P-RE"]["png"])
        self.assertTrue(os.path.isfile(png))
        self.assertEqual(oct(os.stat(png).st_mode & 0o777), "0o600")
        with open(os.path.join(self.base, K.LECTURAS_DIANA)) as f:
            self.assertIn("galeria-f3", f.read())

    def test_gemela_ne_pobres_y_ricas_con_los_mismos_criterios(self):
        out = self.syn
        res = out["resultado"]
        self.assertTrue(res["evaluado"])
        ricas = {c["region"]: c for c in res["ricas"]}
        self.assertEqual(set(ricas), {"FC1:3,3"})
        r = ricas["FC1:3,3"]
        self.assertEqual(r["clase"], "NE-marker-rich region within CK19+ epithelium, p63-negative")
        self.assertTrue(r["control_bajo_T_500um"])
        self.assertIsNotNone(r["re_regional_pct"])
        pobres = {c["region"] for c in res["pobres"]}
        self.assertEqual(pobres, {"FC1:2,2", "FC1:3,2", "FC1:2,3", "FC1:5,2", "FC1:6,2",
                                  "FC1:5,3", "FC1:6,3"})
        # FC2: la mini-puerta de SYN cae (recall 10 puntos) → fuera, con su motivo
        self.assertFalse(out["minipuerta"]["FC2"]["pasa"])
        fuera = {e["region"]: e["motivo"] for e in res["excluidas"]}
        self.assertEqual(fuera.get("FC2:11,2"), "mini-gate not passed in this fragment")
        self.assertEqual(res["fragmentos_evaluados"], ["FC1"])
        self.assertAlmostEqual(res["limite_deteccion"]["rica_ic95_inf_kn_pct"], 92.9, delta=0.05)
        ind = P._lee(self.base, out["galeria"]["indice"])
        self.assertEqual(ind["laminas"], ["P-SYN", "P-CK19", "P-P63", "P-KI67", "P-RE"])
        _sin_nunca(self, out)

    def test_gemela_ne_da_lo_mismo_que_re_en_las_pobres(self):
        """Simetría: misma tabla y regiones etiquetadas P-RE y P-SYN → mismas pobres (región, k,
        n, IC, control, p63, recuento) y mismas exclusiones."""
        rng = np.random.default_rng(5)
        xy = np.c_[rng.uniform(100, 1700, 9000), rng.uniform(100, 1300, 9000)]
        c = _celda(xy)
        pobre = np.isin(c[:, 0], [2, 4, 6]) & (c[:, 1] == 3)
        dab = np.where(rng.random(len(xy)) < np.where(pobre, 0.04, 0.6), 0.4, 0.02)
        art = (c[:, 0] == 4) & (c[:, 1] == 3) & (rng.random(len(xy)) < 0.5)   # región artefacto
        hema = np.where((c[:, 0] == 6) & (c[:, 1] == 3), 0.5, 0.3)          # hematoxilina +66 %
        fcs = {"FC1": FCS["FC1"]}
        ck = {("FC1", 2, 3): True}
        p63 = {("FC1", 2, 3): "negativo"}
        salida = {}
        for lam in ("P-RE", "P-SYN"):
            nuc = MET.Nucleos(lam, xy, dab, ["FC1"] * len(xy), {"d": np.ones(len(xy), bool)},
                              artefacto=art, hema=hema)
            ctx = MET.Contexto(P.ruta_sello(self.base), lam)
            regs = MET.cuenta_regiones(nuc, MET.rejilla(fcs, L_UM), ctx, "d")
            if lam == "P-RE":
                salida[lam] = MET.regiones_re_pobres(nuc, regs, ctx, "d", {"FC1": True}, ck19=ck,
                                                     p63=p63)
            else:
                salida[lam] = F.regiones_ne(nuc, regs, ctx, "d", {"FC1": True}, ck19=ck, p63=p63)
        re_, ne = salida["P-RE"], salida["P-SYN"]
        claves = ("region", "k", "n", "ic95", "control_positivo_500um", "p63", "en_recuento")
        self.assertEqual([{k: c[k] for k in claves} for c in re_["candidatas"]],
                         [{k: c[k] for k in claves} for c in ne["pobres"]])
        self.assertEqual(len(ne["pobres"]), 1)
        self.assertEqual(sorted((e["region"], e["motivo"]) for e in re_["excluidas"]),
                         sorted((e["region"], e["motivo"]) for e in ne["excluidas"]))
        self.assertEqual(len(re_["excluidas"]), 2)
        self.assertEqual(ne["pobres"][0]["clase"],
                         "NE-marker-poor region within CK19+ epithelium, p63-negative")

    def test_regiones_exige_la_puerta_de_p63(self):
        d = os.path.realpath(tempfile.mkdtemp(prefix="lam-f3-sinp63-"))
        try:
            for n in os.listdir(self.base):
                src = os.path.join(self.base, n)
                (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, os.path.join(d, n))
            os.remove(os.path.join(d, F.REL_P63))
            self.assertEqual(P.analisis(d, ["regiones-pobres", "P-RE"], lector=self.lector, **Q),
                             5)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # ── (b) ─────────────────────────────────────────────────────────────────────────────────
    def test_lectura_digital_tabla_por_fragmento_y_biopsias(self):
        o = self.lec
        filas = {f["fc"]: f for f in o["por_fragmento"]}
        v = self.verdad["P-RE"]
        for fid, cajas in (("FC1", ("A", "B")), ("FC2", ("C",))):
            m = np.zeros(len(v["ref"]), bool)
            for k in cajas:
                x0, y0, x1, y1 = ESTR[k].bounds
                m |= ((v["ref"][:, 0] > x0 + 12) & (v["ref"][:, 0] < x1 - 12)
                      & (v["ref"][:, 1] > y0 + 12) & (v["ref"][:, 1] < y1 - 12))
            self.assertAlmostEqual(filas[fid]["todo"]["pct"], 100 * v["pos"][m].mean(), delta=4.0)
            self.assertIsNotNone(filas[fid]["interior"]["pct"])
            self.assertEqual(set(filas[fid]["todo"]["pct_por_denominador"]) >=
                             {"ck19_erosionada", "ck19_sin_erosionar", "morfometrico"}, True)
            self.assertLess(filas[fid]["regiones"]["min"], 10.0)      # la región pobre sembrada
        b = o["biopsias_virtuales"]
        self.assertGreater(b["n_ventanas_validas"], 5)
        self.assertEqual(b["ventana"]["diametro_um"], 500.0)
        self.assertGreater(b["rango_p5_p95_puntos"], 10.0)          # cuánto cambia según el campo
        self.assertLessEqual(b["cuantiles"]["min"], o["global"]["pct"])
        self.assertGreaterEqual(b["cuantiles"]["max"], o["global"]["pct"])
        vis = o["lectura_visual"]
        self.assertEqual(vis["estado"], "same glass")
        dist = np.asarray(b["distribucion_pct"])
        self.assertAlmostEqual(vis["percentil_en_biopsias_virtuales"], 100 * np.mean(dist <= 85.0))
        self.assertEqual(self.lec_ki["lectura_visual"]["estado"],
                         "different staining run / material not identified: not testable with "
                         "these slides")
        _sin_nunca(self, [o, self.lec_ki])

    def test_hecho_salta_y_huella_rehace(self):
        self.assertEqual(F.lectura_digital(self.base, "P-RE", lector=self.lector, **Q)["huella"],
                         self.lec["huella"])
        vis = P._lee(self.base, F.REL_VISUAL)
        vis["P-RE"]["verificado_contra_original"] = False
        P._escribe(self.base, F.REL_VISUAL, vis)
        try:
            o = F.lectura_digital(self.base, "P-RE", lector=self.lector, **Q)
            self.assertNotEqual(o["huella"], self.lec["huella"])
            self.assertEqual(o["lectura_visual"]["estado"],
                             "not verified against the original report")
        finally:
            vis["P-RE"]["verificado_contra_original"] = True
            P._escribe(self.base, F.REL_VISUAL, vis)


# ══ C. FC de consenso con registro de verdad sobre 11 cortes sintéticos ════════════════════════
class CorteBorrado(S.Corte):
    """Un corte al que le falta tejido (`borra`, polígono del mundo): sin píxeles de tejido ni
    núcleos ahí, como un fragmento que no llegó a ese corte."""

    def __init__(self, *a, borra=None, **k):
        super().__init__(*a, **k)
        self.borra = borra
        if borra is not None:
            import shapely
            fuera = ~shapely.contains_xy(borra, self.nucleos_mundo[:, 0], self.nucleos_mundo[:, 1])
            for att in ("nucleos_mundo", "nucleos", "h_amp", "dab_amp", "en_epitelio"):
                setattr(self, att, getattr(self, att)[fuera])

    def pinta(self, mpp, x0_l0, y0_l0, w, h):
        out = super().pinta(mpp, x0_l0, y0_l0, w, h)
        if self.borra is None:
            return out
        import shapely
        xs = x0_l0 * S.MPP_L0 + (np.arange(w) + 0.5) * mpp
        ys = y0_l0 * S.MPP_L0 + (np.arange(h) + 0.5) * mpp
        XX, YY = np.meshgrid(xs, ys)
        mundo = S.aplica(self.T, np.c_[XX.ravel(), YY.ravel()])
        dentro = shapely.contains_xy(self.borra, mundo[:, 0], mundo[:, 1]).reshape(h, w)
        if dentro.any():
            vid = np.clip(np.round(self.vidrio(XX, YY)), 0, 255).astype(np.uint8)
            out[dentro] = vid[dentro]
        return out


class Consenso(unittest.TestCase):
    """11 cortes del mismo bloque (giros, un espejo); el fragmento B falta en 5 cortes y el C en
    6: con «≥6 de 11», B sigue siendo FC y C no."""

    @classmethod
    def setUpClass(cls):
        cls.base = os.path.realpath(tempfile.mkdtemp(prefix="lam-f3-cons-"))
        sella(cls.base)
        P._escribe(cls.base, P._rel("L.json"), {"L_um": 200.0})
        frags = [LineString([(300, 360), (950, 330), (1600, 380)]).buffer(220),
                 LineString([(300, 1060), (950, 1110), (1600, 1070)]).buffer(210),
                 Point(1840, 720).buffer(110)]
        cls.bloque = S.Bloque(7, ancho_um=2000, alto_um=1450, fragmentos=frags)
        b_mundo = cls.bloque.fragmentos[1].buffer(30)
        c_mundo = cls.bloque.fragmentos[2].buffer(30)
        # cortes contiguos (fase 0,02 y 90 % de núcleos que siguen): como 11 cortes seriados de
        # 3-4 µm; con fases de 0,09 y persistencia 0,6 los lejanos no casaban ni con 30 inliers
        spec = [dict(nombre=n, fase=0.02 * k, angulo=a, espejo=e, ck19=(n == "P-CK19"),
                     ki67=0.25 if n == "P-KI67" else 0.0, persistencia=0.9)
                for k, (n, a, e) in enumerate(zip(
                    F.SERIE_IHQ, (0, 12, -25, 40, 90, -60, 150, 5, -10, 30, 180),
                    (False, False, False, True, False, False, False, False, False, False, False)))]
        cls.sin_b = ("P-RP", "P-RA", "P-SYN", "P-CHGA", "P-{{DIANA3}}")
        cls.sin_c = ("P-HER2NEG", "P-HER2", "P-RE", "P-RP", "P-RA", "P-SYN")
        borra = {}
        for n in F.SERIE_IHQ:
            g = [x for x, lst in ((b_mundo, cls.sin_b), (c_mundo, cls.sin_c)) if n in lst]
            if g:
                from shapely.ops import unary_union
                borra[n] = unary_union(g)
        cortes, previo = {}, None
        for i in sorted(range(len(spec)), key=lambda k: spec[k]["fase"]):
            e = dict(spec[i])
            n = e.pop("nombre")
            cortes[n] = previo = CorteBorrado(cls.bloque, n, e.pop("fase"), 700 + i,
                                              previo=previo, borra=borra.get(n), **e)
        cls.cortes = cortes
        cls.L = S.LectorSintetico([cortes[n] for n in F.SERIE_IHQ])
        for n in F.SERIE_IHQ:
            cent = cls.L.centroides_l0(n)
            os.makedirs(os.path.join(cls.base, "segmenta"), exist_ok=True)
            np.savez_compressed(os.path.join(cls.base, "segmenta", "nucleos_%s.npz" % n),
                                centroide=cent, area_um2=np.full(len(cent), 30.0))
            with open(os.path.join(cls.base, "segmenta", "nucleos_%s.json" % n), "w") as f:
                json.dump({"lamina": n, "n": len(cent), "mpp_l0": S.MPP_L0}, f)
            C.marca_hecho(cls.base, "segmenta", n, productos=["segmenta/nucleos_%s.npz" % n,
                                                              "segmenta/nucleos_%s.json" % n])
        cls.rc = P.analisis(cls.base, ["consenso"], lector=cls.L, **Q)
        cls.bf = P._lee(cls.base, F.REL_BASE)
        cls.serie = P._lee(cls.base, F.REL_SERIE)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def _fc_de(self, geom_mundo):
        """FC cuyo polígono (µm de P-CK19) contiene el centroide del fragmento del mundo."""
        c = self.cortes["P-CK19"]
        x, y = S.aplica(c.A, [geom_mundo.centroid.coords[0]])[0]
        fcs = F.carga_consenso(self.base)["fcs"]
        for f in fcs:
            if f["poligono_um"].buffer(5).contains(Point(x, y)):
                return f["id"]
        return None

    def test_seis_de_once(self):
        self.assertEqual(self.rc, 0)
        fa = self._fc_de(self.bloque.fragmentos[0])
        fb = self._fc_de(self.bloque.fragmentos[1])
        fc = self._fc_de(self.bloque.fragmentos[2])
        self.assertIsNotNone(fa)
        self.assertIsNotNone(fb)                      # 6 de 11 lo tienen: FC
        self.assertIsNone(fc)                         # 5 de 11: no es FC
        self.assertEqual(len(self.bf["fragmentos"]), 2)

    def test_puerta_por_fc_en_cada_par(self):
        frs = {f["id"]: f for f in self.bf["fragmentos"]}
        fa = frs[self._fc_de(self.bloque.fragmentos[0])]
        fb = frs[self._fc_de(self.bloque.fragmentos[1])]
        self.assertGreaterEqual(len(fa["verificado_en"]), 10, fa)
        for n in self.cortes:
            if n in self.sin_b:                     # sin ese tejido, ese par no se verifica ahí
                self.assertNotIn(n, fb["verificado_en"], n)
        self.assertGreaterEqual(len(fb["verificado_en"]), 5)
        for f in frs.values():
            self.assertGreater(f["n_regiones"], 10)
            self.assertTrue(all("dist_borde_um" in r for r in f["regiones"]))
            for n, p90 in f["tre_p90_um"].items():
                self.assertLess(p90, 50.0, n)

    def test_recupera_las_transformadas_conocidas(self):
        """Tolerancia: ≤5 µm en el centroide de cada FC verificado (≈2,5 px a ×8)."""
        fcs = F.carga_consenso(self.base)["fcs"]
        for n in ("P-KI67", "P-HER2", "P-P63"):
            res, _ = F.resultado_consenso(self.base, n, fcs)
            esperada = self.cortes["P-CK19"].A @ self.cortes[n].T
            for f, fc in zip(res.fragmentos, res.fcs):
                if not f["pasa"]:
                    continue
                c = np.array(fc["poligono_um"].centroid.coords[0])
                M = np.asarray(f["matriz_um"])
                Me = np.linalg.inv(esperada)
                err = np.linalg.norm(S.aplica(M, S.aplica(Me, [c])) - c)
                self.assertLess(err, 5.0, (n, f["id"]))
        self.assertTrue(np.linalg.det(np.asarray(
            P._lee(self.base, F._rel_par("P-HER2"))["resumen"]["global"]["matriz_um"])[:2, :2]) < 0)

    def test_serie_y_hecho(self):
        self.assertIn(F.DECLARACIONES_F3["consenso"], self.serie["declaraciones"])
        self.assertEqual(self.serie["orden"]["rotulo"], "estimated section order; spacing unknown")
        self.assertEqual(P.analisis(self.base, ["consenso"], lector=self.L, **Q), 0)  # salta
        self.assertEqual(P._lee(self.base, F.REL_SERIE)["huella"], self.serie["huella"])

    def test_falta_una_lamina_segmentada(self):
        d = os.path.realpath(tempfile.mkdtemp(prefix="lam-f3-falta-"))
        try:
            sella(d)
            P._escribe(d, P._rel("L.json"), {"L_um": 200.0})
            self.assertEqual(P.analisis(d, ["consenso"], lector=self.L, **Q), 5)
            os.remove(os.path.join(d, P._rel("L.json")))
            self.assertEqual(P.analisis(d, ["consenso"], lector=self.L, **Q), 5)
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ══ D. ROI de Carlos sobre láminas y pptx sintéticos ════════════════════════════════════════
_NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _sp(texto, x, y, w=2000000, h=400000):
    return ('<p:sp><p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
            '</p:spPr><p:txBody><a:p><a:r><a:t>%s</a:t></a:r></a:p></p:txBody></p:sp>'
            % (x, y, w, h, texto))


def _pic(rid, x, y, w=4000000, h=3000000):
    return ('<p:pic><p:blipFill><a:blip r:embed="%s"/></p:blipFill><p:spPr><a:xfrm>'
            '<a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm></p:spPr></p:pic>'
            % (rid, x, y, w, h))


def escribe_pptx(ruta, diapositivas):
    """pptx mínimo: `diapositivas` = [(xml del spTree, {rId: (nombre del medio, bytes)})]."""
    with zipfile.ZipFile(ruta, "w") as z:
        ids = "".join('<p:sldId id="%d" r:id="rIdS%d"/>' % (256 + k, k)
                      for k in range(len(diapositivas)))
        z.writestr("ppt/presentation.xml",
                   '<p:presentation xmlns:p="%s" xmlns:r="%s"><p:sldIdLst>%s</p:sldIdLst>'
                   '</p:presentation>' % (_NS_P, _NS_R, ids))
        # el orden de la presentación es el inverso del de los nombres de fichero (se respeta)
        n = len(diapositivas)
        rels = "".join('<Relationship Id="rIdS%d" Target="slides/slide%d.xml"/>' % (k, n - k)
                       for k in range(n))
        z.writestr("ppt/_rels/presentation.xml.rels",
                   '<Relationships xmlns="%s">%s</Relationships>' % (_NS_REL, rels))
        for k, (arbol, medios) in enumerate(diapositivas):
            parte = "slide%d.xml" % (n - k)
            z.writestr("ppt/slides/" + parte,
                       '<p:sld xmlns:p="%s" xmlns:a="%s" xmlns:r="%s"><p:cSld><p:spTree>%s'
                       '</p:spTree></p:cSld></p:sld>' % (_NS_P, _NS_A, _NS_R, arbol))
            r = "".join('<Relationship Id="%s" Target="../media/%s"/>' % (rid, nom)
                        for rid, (nom, _) in medios.items())
            z.writestr("ppt/slides/_rels/%s.rels" % parte,
                       '<Relationships xmlns="%s">%s</Relationships>' % (_NS_REL, r))
            for nom, datos in medios.values():
                z.writestr("ppt/media/" + nom, datos)


def _png(rgb):
    from PIL import Image
    b = io.BytesIO()
    Image.fromarray(rgb).save(b, format="PNG")
    return b.getvalue()


class RoiCarlos(unittest.TestCase):
    """Láminas sintéticas DISTINTAS (un bloque cada una) y P-RP idéntica a P-RE."""

    @classmethod
    def setUpClass(cls):
        from PIL import Image
        cls.base = os.path.realpath(tempfile.mkdtemp(prefix="lam-f3-roi-"))
        sella(cls.base)
        cortes = []
        for k, n in enumerate(("P-CK19", "P-KI67", "P-RE", "P-HE")):
            b = S.Bloque(31 + k, ancho_um=1500, alto_um=1100,
                         fragmentos=[box(250, 250, 1250, 850).buffer(50)])
            cortes.append(S.Corte(b, n, 0.3 * k, 900 + k, ki67=0.3 if n == "P-KI67" else 0.0))
        b_re = cortes[2].bloque
        cortes.append(S.Corte(b_re, "P-RP", 0.6, 902))           # = P-RE píxel a píxel
        cls.laminas = ("P-CK19", "P-KI67", "P-RE", "P-RP", "P-HE")
        cls.L = S.LectorSintetico(cortes)
        lam = cls.L.abre("P-KI67")
        mpp4 = lam.niveles[1][1]
        crop = cls.L.lee_region(lam, mpp4, 4 * 300, 4 * 250, 500, 400)
        cap1 = np.asarray(Image.fromarray(crop).resize((800, 640), Image.BICUBIC))
        lam = cls.L.abre("P-HE")
        crop = cls.L.lee_region(lam, mpp4, 4 * 400, 4 * 300, 600, 450)
        cap2 = np.asarray(Image.fromarray(crop).resize((480, 360), Image.BICUBIC))
        lam = cls.L.abre("P-RE")
        cap3 = cls.L.lee_region(lam, mpp4, 4 * 350, 4 * 300, 500, 400)
        rng = np.random.default_rng(3)
        cap4 = rng.integers(0, 256, (400, 500, 3), dtype=np.uint8)
        diapos = [
            (_sp("ROI 20x", 100000, 100000) + _pic("rId1", 1000000, 1000000) +
             _sp("Ki67", 1500000, 4200000), {"rId1": ("image1.png", _png(cap1))}),
            (_sp("H&amp;E", 1000000, 4100000) + _pic("rId1", 1000000, 1000000),
             {"rId1": ("image2.png", _png(cap2))}),
            (_pic("rId1", 1000000, 1000000) + _sp("REC Andro", 1000000, 4100000),
             {"rId1": ("image3.png", _png(cap3))}),
            (_pic("rId1", 1000000, 1000000) + _sp("ruido", 1000000, 4100000) +
             _pic("rId2", 6000000, 1000000), {"rId1": ("image4.png", _png(cap4)),
                                               "rId2": ("image5.emf", b"\x01\x00\x00\x00")}),
            (_sp("AE1/AE3 Unable to identify same/similar ROI", 100000, 100000), {}),
        ]
        cls.pptx = os.path.join(cls.base, "deck.pptx")
        escribe_pptx(cls.pptx, diapos)
        cls.hoja = os.path.join(cls.base, "hoja.json")
        with open(cls.hoja, "w") as f:
            json.dump({"pptx": {"sha256": C.sha256_fichero(cls.pptx)}}, f)
        cls.out = F.roi_carlos(cls.base, cls.L, pptx=cls.pptx, hoja=cls.hoja,
                               laminas=cls.laminas, **Q)
        cls.cap = {(c["diapositiva"], c.get("imagen")): c for c in cls.out["capturas"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def test_orden_de_las_diapositivas_y_etiquetas(self):
        d = F.lee_pptx(self.pptx)
        self.assertEqual([x["diapositiva"] for x in d], [1, 2, 3, 4, 5])
        self.assertEqual(self.cap[(1, 1)]["label_in_deck"], "Ki67")      # la más cercana
        self.assertEqual(self.cap[(2, 1)]["label_in_deck"], "H&E")
        self.assertEqual(self.cap[(5, None)]["estado"], "sin imagen")
        self.assertIn("Unable to identify", self.cap[(5, None)]["label_in_deck"])

    def test_localiza_con_escala_y_posicion(self):
        """Verdad: captura = ×4 de P-KI67 desde (300, 250) px, ampliada ×1,6 (escala 0,625).
        Tolerancias: escala ±2 %, rotación ±0,5°, posición ±3 px del ×4."""
        c = self.cap[(1, 1)]
        self.assertEqual(c["estado"], "localizada")
        self.assertEqual(c["lamina"], "P-KI67")
        self.assertGreaterEqual(c["mejor_inliers"], 30)
        self.assertGreaterEqual(c["mejor_inliers"], 2 * c["segunda_inliers"])
        t = c["transformada"]
        self.assertAlmostEqual(t["escala_captura_a_x4"], 0.625, delta=0.0125)
        self.assertAlmostEqual(t["rotacion_grados"], 0.0, delta=0.5)
        self.assertAlmostEqual(t["traslacion_x4_px"][0], 300.0, delta=3.0)
        self.assertAlmostEqual(t["traslacion_x4_px"][1], 250.0, delta=3.0)
        self.assertAlmostEqual(c["huella_l0_px"][0][0], 1200.0, delta=12.0)
        self.assertEqual(c["informe"], "label in deck: Ki67; image matches: P-KI67 (Ki67)")
        c2 = self.cap[(2, 1)]
        self.assertEqual((c2["estado"], c2["lamina"]), ("localizada", "P-HE"))
        self.assertAlmostEqual(c2["transformada"]["escala_captura_a_x4"], 1.25, delta=0.025)

    def test_dos_laminas_iguales_no_localiza(self):
        c = self.cap[(3, 1)]
        self.assertEqual(c["estado"], "no localizada")
        self.assertGreaterEqual(c["mejor_inliers"], 30)                 # casa, pero con dos
        self.assertLess(c["mejor_inliers"], 2 * c["segunda_inliers"])
        self.assertEqual(c["informe"], "label in deck: REC Andro; image matches: not localized")

    def test_ruido_y_formato_no_raster(self):
        c = self.cap[(4, 1)]
        self.assertEqual(c["estado"], "no localizada")
        self.assertLess(c["mejor_inliers"], 30)
        e = self.cap[(4, 2)]
        self.assertEqual(e["estado"], "no evaluable")
        self.assertEqual(self.out["n_localizadas"], 2)
        _sin_nunca(self, self.out)

    def test_pptx_cambiado_no_se_usa_y_hecho_salta(self):
        otra = os.path.join(self.base, "hoja-mala.json")
        with open(otra, "w") as f:
            json.dump({"pptx": {"sha256": "0" * 64}}, f)
        with self.assertRaises(P.NoPasa):
            F.roi_carlos(self.base, self.L, pptx=self.pptx, hoja=otra, laminas=self.laminas, **Q)
        o = F.roi_carlos(self.base, self.L, pptx=self.pptx, hoja=self.hoja, laminas=self.laminas,
                         **Q)
        self.assertEqual(o["huella"], self.out["huella"])

    def test_asignacion_y_emparejado(self):
        self.assertEqual(F.asigna_roi({"A": 40, "B": 20}), ("A", 40, 20))
        self.assertEqual(F.asigna_roi({"A": 40, "B": 21})[0], None)
        self.assertEqual(F.asigna_roi({"A": 29})[0], None)
        self.assertEqual(F.asigna_roi({"A": 30, "B": 0})[0], "A")
        from skimage.feature import match_descriptors
        rng = np.random.default_rng(1)
        a = rng.integers(0, 255, (300, 128)).astype(np.uint8)
        b = np.vstack([a[:200] + rng.integers(0, 3, (200, 128)).astype(np.uint8),
                       rng.integers(0, 255, (500, 128)).astype(np.uint8)])
        ref = match_descriptors(a, b, max_ratio=0.8, cross_check=True)
        mio = F.empareja(a, b, 0.8, True, bloque=37)
        self.assertEqual(sorted(map(tuple, ref.tolist())), sorted(map(tuple, mio.tolist())))


class ConsensoValis(unittest.TestCase):
    """`consenso --valis` con el contrato de `registro_par` (2-oct): pedir VALIS no es haberlo
    intentado; un VALIS que no corre es error de ejecución (código 6), no una vuelta gastada."""

    NO_CORRE = {"fragmentos": [{"id": "FC1", "intentos": [
        {"metodo": "sift", "pasa": False},
        {"metodo": "VALIS", "pasa": False, "motivo": "VALIS did not run: rc=1"}]}]}
    CORRE = {"fragmentos": [{"id": "FC1", "intentos": [
        {"metodo": "VALIS", "pasa": False, "corrio": True, "escala": 1.0}]}]}
    SIN_VALIS = {"fragmentos": [{"id": "FC1", "intentos": [{"metodo": "sift", "pasa": True}]}]}

    def test_cuenta_valis(self):
        self.assertEqual(F.cuenta_valis({"P-RE": self.NO_CORRE}, True),
                         (False, [{"movil": "P-RE", "fc": "FC1",
                                   "motivo": "VALIS did not run: rc=1"}]))
        self.assertEqual(F.cuenta_valis({"P-RE": self.NO_CORRE, "P-RP": self.CORRE}, True),
                         (True, []))
        self.assertEqual(F.cuenta_valis({"P-RE": self.SIN_VALIS}, True), (False, []))
        self.assertEqual(F.cuenta_valis({"P-RE": self.NO_CORRE}, False), (False, []))

    def test_orden_con_valis_que_no_corre_da_6(self):
        original = F.consenso

        def falla(*a, **k):
            raise P.ValisNoCorrio("consenso: VALIS no corrió (P-RE FC1: rc=1)")
        F.consenso = falla
        try:
            self.assertEqual(F.orden(tempfile.gettempdir(), ["consenso", "--valis"], **Q),
                             P.RC_EJECUCION)
        finally:
            F.consenso = original


if __name__ == "__main__":
    unittest.main()
