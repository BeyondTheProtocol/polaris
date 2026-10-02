#!/usr/bin/env python3
"""tests/test_laminillas_piloto_segmenta.py — `tools/laminillas_segmenta.py` (módulo A del piloto).

SIN DATOS: núcleos sintéticos de posición, radio y tinción conocidos; un TIFF sintético con la
estructura del Grundium (BigTIFF, ×1/×4/×8, teselas 512 JPEG YCbCr 4:2:0 — convertido a YCbCr
ANTES de escribir: tifffile no convierte, y sin eso los colores salen corruptos —, mpp 0,2506,
bloque de relleno 255 fuera de la zona) para el camino real LazySlide + InstanSeg (MPS, lote 1).

Tolerancias declaradas:
  · detector clásico B: recuento ± 5 %, mediana de área ± 15 %, F1 a IoU 0,5 ≥ 0,9;
  · InstanSeg real: recuento ± 5 % de la verdad, cobertura de centroides verdaderos ≥ 0,95,
    mediana de área en 25-80 µm² y a ± 25 % de la verdad, nº de teselas = esperado, 0
    descartadas, tile_spec.mpp 0,5, sin aviso de check_input_tile, rejilla desplazada < 1 %;
  · máscara provisional: área de fragmento ± 8 % a 2.000/mm² (Poisson) y ± 5 % a 7.000-10.000/mm²
    (rejilla y Poisson) con fragmentos a 50 µm: 2 fragmentos y ningún núcleo retenido (≥ 50 µm de
    la máscara) a < 45 µm REALES del borde; estroma laxo (1.000/mm²) junto a tumor denso
    (8.000/mm²): área ≥ 90 % de la verdad; grupo de 3 núcleos fuera de fragmentos (< 0,2 mm²);
  · pliegue: banda de 60 µm (0,36 % del tejido) detectada, línea de 16 µm no; bandas del 1 % y del
    3 % (que la regla del plan no ve) salen como candidatos para el tribunal, área ± 10 %;
  · rejilla del respaldo (tissue_key=None): = tiles_from_bbox de toda la imagen ∩ zona;
  · pesos: sha256 y bytes del fichero sellado; pesos.json tocado o fichero cambiado, no carga;
  · GrandQC: un fallo en el primer dispositivo se reintenta en CPU y se dice.
Corre con el python del venv `patologia`; sin venv, SKIP (77). Sin los pesos de InstanSeg en la
caché, solo se salta el caso de InstanSeg real (y se dice). Los temporales se borran.
"""
import glob
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import _laminillas_piloto_a as F  # noqa: E402

F.al_venv(__file__)
sys.path.insert(0, os.path.join(os.path.dirname(AQUI), "tools"))

import numpy as np  # noqa: E402
from shapely.geometry import box  # noqa: E402

import laminillas_sello as SL  # noqa: E402
import laminillas_segmenta as S  # noqa: E402

FECHA = "2026-10-01"


def sello_completo(ruta, segmenta=None):
    """Un sello con TODAS las secciones que exige la guardia (contenido mínimo, sintético)."""
    return SL.sella(ruta, {
        "vectores": {"H": [1, 0, 0], "DAB": [0, 1, 0]}, "residuo": {"por_lamina": {}},
        "umbral": {"T": {"nucleo": 0.1, "anillo": 0.1}}, "precongelacion": {"sha256": "x"},
        "parametros": {"segmenta": segmenta if segmenta is not None else S.canon(S.parametros())}},
        FECHA)


HF = os.environ.get("HF_HOME", os.path.expanduser("~/.polaris-venvs/cache/hf"))
PESOS_INSTANSEG = glob.glob(os.path.join(HF, "hub", "models--RendeiroLab--LazySlide-models",
                                         "snapshots", "*", "instanseg", "instanseg_v0_1_0.pt"))


def _nucleos_l0(mpp, lado_px, paso_um=10.0, r_um=3.5, jitter_um=1.0, semilla=0, margen=40):
    rng = np.random.default_rng(semilla)
    paso = paso_um / mpp
    out = []
    for cy in np.arange(margen, lado_px - margen, paso):
        for cx in np.arange(margen, lado_px - margen, paso):
            j = rng.uniform(-jitter_um, jitter_um, 2) / mpp
            out.append((cx + j[0], cy + j[1], r_um / mpp, 0))
    return np.asarray(out)


def _ycbcr(rgb):
    f = rgb.astype(np.float64)
    Y = 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]
    Cb = 128 - 0.168736 * f[..., 0] - 0.331264 * f[..., 1] + 0.5 * f[..., 2]
    Cr = 128 + 0.5 * f[..., 0] - 0.418688 * f[..., 1] - 0.081312 * f[..., 2]
    return np.clip(np.rint(np.dstack([Y, Cb, Cr])), 0, 255).astype(np.uint8)


def escribe_grundium(ruta, img, mpp=0.2506):
    """BigTIFF ×1/×4/×8, teselas 512 JPEG YCbCr 4:2:0, NewSubfileType=1 en las reducidas,
    resolución en cm solo en L0."""
    import tifffile
    from skimage.transform import downscale_local_mean
    with tifffile.TiffWriter(ruta, bigtiff=True) as tw:
        for f in (1, 4, 8):
            nivel = img if f == 1 else downscale_local_mean(img, (f, f, 1)).astype(np.uint8)
            kw = dict(tile=(512, 512), compression="jpeg", compressionargs={"level": 90},
                      photometric="ycbcr", subsampling=(2, 2), metadata=None)
            if f == 1:
                kw.update(resolution=(1e4 / mpp, 1e4 / mpp), resolutionunit="CENTIMETER")
            else:
                kw.update(subfiletype=1)
            tw.write(_ycbcr(nivel), **kw)


class DetectorB(unittest.TestCase):
    def test_watershed_recupera_nucleos(self):
        mpp = 0.25
        nuc = _nucleos_l0(mpp, 1024, semilla=1)
        forma = (1024, 1024)
        cH = np.where(F.mascara_nuclear(nuc, forma), F.discos(nuc, forma, np.full(len(nuc), 0.6)),
                      0.04)
        rgb = F.pinta(cH, np.zeros(forma), semilla=1)
        import laminillas_color as C
        odsum = C.od(rgb, F.I0).sum(-1)
        lab, cents, areas, _ = S.detector_clasico(odsum, mpp)
        self.assertAlmostEqual(len(cents) / len(nuc), 1.0, delta=0.05)
        verdad = np.pi * 3.5 ** 2
        self.assertAlmostEqual(float(np.median(areas)) / verdad, 1.0, delta=0.15)
        lab_v = S.rasteriza(F.poligonos(nuc), 0, 0, forma)
        r = S.f1_iou(lab_v, lab)
        self.assertGreaterEqual(r["f1"], 0.9)
        self.assertGreaterEqual(S.cobertura(nuc[:, :2], lab), 0.95)
        self.assertAlmostEqual(r["cociente"], len(cents) / len(nuc), places=9)


class PuertaDeteccion(unittest.TestCase):
    def test_rotulo_exacto(self):
        ok = S.puerta_deteccion({("0", 1): {"dab": 0.97, "h": 0.95}},
                                {"0": [3000, 3200, 2900]})
        self.assertTrue(ok["pasa"])
        self.assertEqual(ok["rotulos"], [])
        mal = S.puerta_deteccion({("0", 1): {"dab": 0.80, "h": 0.95}})      # 15 puntos
        self.assertFalse(mal["pasa"])
        self.assertEqual(mal["rotulos"], ["detection-dependent"])
        dens = S.puerta_deteccion({}, {"1": [3000, 2400]})                   # 20 %
        self.assertEqual(dens["rotulos"], ["detection-dependent"])


class Geometria(unittest.TestCase):
    def test_empaqueta_ida_y_vuelta(self):
        pol = F.poligonos(_nucleos_l0(0.25, 300, semilla=2))
        xy, offs = S.empaqueta(pol)
        vuelta = S.desempaqueta(xy, offs)
        self.assertEqual(len(vuelta), len(pol))
        self.assertTrue(all(abs(a.area - b.area) < 1e-6 for a, b in zip(pol, vuelta)))

    def test_mediana_area(self):
        pol = F.poligonos(_nucleos_l0(0.25, 600, r_um=3.5, semilla=3))
        m = S.mediana_area(pol, 0.25)
        self.assertAlmostEqual(m["mediana_um2"], np.pi * 3.5 ** 2, delta=0.5)
        self.assertTrue(m["pasa"])
        self.assertFalse(S.mediana_area(F.poligonos(_nucleos_l0(0.25, 600, r_um=1.5)),
                                        0.25)["pasa"])

    def test_acuerdo_area(self):
        """Puerta del área (CRITERIO_AREA): 0,90 (lo del sintético con verdad física) pasa; un
        error ×2 de escala lineal (0,25 o 4) y la falta de pares, no."""
        rng = np.random.default_rng(9)
        ok = S.acuerdo_area(0.90 + rng.normal(0, 0.1, 500))
        self.assertTrue(ok["pasa"])
        self.assertEqual(ok["criterio"], S.CRITERIO_AREA)
        self.assertFalse(S.acuerdo_area(0.25 + rng.normal(0, 0.03, 500))["pasa"])
        self.assertFalse(S.acuerdo_area(4.0 + rng.normal(0, 0.3, 500))["pasa"])
        self.assertFalse(S.acuerdo_area(np.full(150, 0.9))["pasa"])          # < 200 pares
        self.assertFalse(S.acuerdo_area([])["pasa"])
        self.assertIn("CRITERIO_AREA", S.parametros())

    def test_rejilla_replica_lazyslide(self):
        from lazyslide.preprocess._tiles import tiles_from_bbox
        mpp = 0.2506
        zona = box(0, 0, 9000, 6000).difference(box(8000, 0, 9000, 1500))
        mia, _ = S.rejilla(zona, mpp)
        ds = 0.5 / mpp
        ls = tiles_from_bbox(0, 0, 9000, 6000, int(512 * ds), int(512 * ds),
                             stride_w=int(448 * ds), stride_h=int(448 * ds), edge=True, mask=zona)
        b = ls.geometry.bounds
        self.assertEqual({tuple(p) for p in mia.tolist()},
                         {(int(x), int(y)) for x, y in zip(b.minx, b.miny)})
        ok = S.comprueba_teselas(mia, mia, 0.5, zona, mpp)
        self.assertTrue(ok["pasa"])
        self.assertEqual(ok["descartadas"], 0)
        self.assertEqual(ok["criterio"], S.CRITERIO_TESELAS)         # el cambio, declarado
        self.assertGreater(ok["desvio_area"], 0.01)                  # el ±1 % por área no cabe
        uno_menos = S.comprueba_teselas(mia[1:], mia, 0.5)
        self.assertEqual(uno_menos["descartadas"], 1)
        self.assertFalse(uno_menos["pasa"])
        self.assertFalse(S.comprueba_teselas(mia, mia, 0.52)["pasa"])

    def test_rejilla_del_respaldo(self):
        """tissue_key=None: LazySlide tesela la caja de TODA la imagen y luego se descarta lo que
        no toca la zona; la réplica, con esa misma caja (no la de la zona), da las mismas
        teselas, columna de borde incluida."""
        import shapely
        from lazyslide.preprocess._tiles import tiles_from_bbox
        mpp = 0.2506
        zona = box(3000, 3000, 20555, 12000)
        caja = (0, 0, 24000, 15000)
        ds = 0.5 / mpp
        ls = tiles_from_bbox(*caja, int(512 * ds), int(512 * ds), stride_w=int(448 * ds),
                             stride_h=int(448 * ds), edge=True)
        g = ls.geometry.to_numpy()
        ls = ls.iloc[np.flatnonzero(shapely.intersects(g, zona))]
        b = ls.geometry.bounds
        esperado = {(int(x), int(y)) for x, y in zip(b.minx, b.miny)}
        mia, _ = S.rejilla(zona, mpp, caja=caja)
        self.assertEqual({tuple(p) for p in mia.tolist()}, esperado)
        self.assertTrue(S.comprueba_teselas(np.asarray(sorted(esperado)), mia, 0.5)["pasa"])

    def test_mascara_provisional_y_bordes(self):
        mpp = 0.25
        rng = np.random.default_rng(4)
        rects = [(0.2, 0.2, 0.8, 0.8), (1.0, 0.3, 1.5, 1.1)]      # mm
        xy = []
        for x0, y0, x1, y1 in rects:
            n = int(2000 * (x1 - x0) * (y1 - y0))
            xy.append(np.column_stack([rng.uniform(x0, x1, n), rng.uniform(y0, y1, n)]))
        xy.append(np.array([[1.8, 1.8], [1.802, 1.8], [1.8, 1.802]]))   # grupo aislado
        xy = np.vstack(xy) * 1000 / mpp
        prov = S.mascara_provisional(xy, mpp, (int(2000 / mpp), int(1600 / mpp)))
        verdad = sorted((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in rects)
        self.assertEqual(len(prov["areas_mm2"]), 2)
        for a, v in zip(sorted(prov["areas_mm2"]), verdad):
            self.assertAlmostEqual(a / v, 1.0, delta=0.08)
        fr = S.fragmento_de(prov, xy)
        self.assertTrue((fr[-3:] == -1).all())                   # < 0,2 mm²: no es fragmento
        centro = np.array([[0.5, 0.5]]) * 1000 / mpp
        cerca = np.array([[0.21, 0.5]]) * 1000 / mpp
        self.assertGreater(S.distancia_borde(prov, centro)[0], 250)
        self.assertLess(S.distancia_borde(prov, cerca)[0], S.ARTEFACTO["borde_um"])

    def test_mascara_provisional_densa(self):
        """7.000-10.000 núcleos/mm², fragmentos de 500 µm a 50 µm: con el umbral fijo solo, el
        borde caía 25-31 µm fuera y los dos fragmentos se fundían. Ahora: 2 fragmentos, área
        ± 5 %, y ningún núcleo retenido (≥ 50 µm del borde según la máscara) a < 45 µm REALES."""
        mpp = 0.5
        rects = [(50, 50, 550, 550), (600, 50, 1100, 550)]                # µm
        for dens in (7000, 10000):
            for aleatorio in (False, True):
                rng = np.random.default_rng(1)
                pts = []
                for x0, y0, x1, y1 in rects:
                    if aleatorio:
                        n = rng.poisson(dens * (x1 - x0) * (y1 - y0) / 1e6)
                        pts.append(np.column_stack([rng.uniform(x0, x1, n),
                                                    rng.uniform(y0, y1, n)]))
                    else:
                        paso = 1000 / np.sqrt(dens)
                        g = np.array([(x, y) for y in np.arange(y0 + paso / 2, y1, paso)
                                      for x in np.arange(x0 + paso / 2, x1, paso)])
                        pts.append(g + rng.uniform(-paso * 0.14, paso * 0.14, g.shape))
                xy_um = np.vstack(pts)
                prov = S.mascara_provisional(xy_um / mpp, mpp, (int(1200 / mpp), int(650 / mpp)))
                caso = "%d/mm² %s" % (dens, "Poisson" if aleatorio else "rejilla")
                self.assertEqual(len(prov["areas_mm2"]), 2, caso)
                for a in prov["areas_mm2"]:
                    self.assertAlmostEqual(a / 0.25, 1.0, delta=0.05, msg=caso)
                real = np.full(len(xy_um), np.inf)
                for x0, y0, x1, y1 in rects:
                    d = np.minimum.reduce([xy_um[:, 0] - x0, x1 - xy_um[:, 0],
                                           xy_um[:, 1] - y0, y1 - xy_um[:, 1]])
                    real = np.where(d >= 0, np.minimum(real, d), real)
                retenido = S.distancia_borde(prov, xy_um / mpp) >= S.ARTEFACTO["borde_um"]
                self.assertGreaterEqual(float(real[retenido].min()), 45.0, caso)
                self.assertGreater(retenido.mean(), 0.5, caso)          # y no se lo come todo

    def test_mascara_provisional_estroma_laxo(self):
        """Estroma laxo (1.000/mm²) junto a tumor denso (8.000/mm²) en un mismo fragmento: no es
        un valle (tejido a un solo lado) y se conserva."""
        mpp = 0.5
        rng = np.random.default_rng(3)
        pts = []
        for x0, y0, x1, y1, d in ((100, 100, 600, 700, 8000), (600, 100, 1100, 700, 1000)):
            n = rng.poisson(d * (x1 - x0) * (y1 - y0) / 1e6)
            pts.append(np.column_stack([rng.uniform(x0, x1, n), rng.uniform(y0, y1, n)]))
        prov = S.mascara_provisional(np.vstack(pts) / mpp, mpp, (int(1300 / mpp), int(800 / mpp)))
        self.assertEqual(len(prov["areas_mm2"]), 1)
        self.assertGreaterEqual(prov["areas_mm2"][0] / 0.60, 0.90)
        self.assertFalse(prov["valle"].any())

    def test_cobertura_y_centroides_continuos(self):
        """Convención única: el píxel i cubre [i, i+1). Un centroide en 9,6 cae en el píxel 9."""
        lab = np.zeros((20, 20), np.int32)
        lab[:, 10:] = 1
        self.assertEqual(S.cobertura(np.array([[9.6, 5.0]]), lab), 0.0)
        self.assertEqual(S.cobertura(np.array([[10.0, 5.0]]), lab), 1.0)
        img = np.zeros((40, 40))
        img[10:16, 20:26] = 1.0                                    # centro continuo (23, 13)
        _, cents, _, _ = S.detector_clasico(img, 1.0, umbral=0.5, p=dict(
            S.DETECTOR_B, sigma_um=0.01, area_min_um2=1.0))
        self.assertAlmostEqual(cents[0, 0], 23.0, places=6)
        self.assertAlmostEqual(cents[0, 1], 13.0, places=6)

    def test_cambio_rejilla(self):
        mpp = 0.25
        rng = np.random.default_rng(5)
        xy = rng.uniform(0.2, 0.8, (800, 2)) * 1000 / mpp
        prov = S.mascara_provisional(xy, mpp, (4000, 4000))
        self.assertTrue(S.cambio_rejilla(xy, xy, prov)["pasa"])
        r = S.cambio_rejilla(xy, xy[:-20], prov)                 # 2,5 % menos
        self.assertFalse(r["pasa"])
        self.assertAlmostEqual(r["peor"], 20 / 800, delta=0.002)


class Artefactos(unittest.TestCase):
    def test_foco_y_tercil(self):
        from scipy.ndimage import gaussian_filter
        rng = np.random.default_rng(6)
        cocientes = []
        for i in range(9):
            g = gaussian_filter(rng.uniform(0, 255, (256, 256)), 1.0)
            if i < 3:
                g = gaussian_filter(g, 2.5)                      # fuera de foco
            cocientes.append(S.foco(g))
        corte = S.corte_tercil_foco(cocientes, np.ones(9))
        bajos = np.flatnonzero(np.asarray(cocientes) < corte)
        self.assertEqual(sorted(bajos.tolist()), [0, 1, 2])

    def test_pliegue_banda_si_linea_no(self):
        mpp = 2.0
        rng = np.random.default_rng(7)
        s = 0.3 + rng.normal(0, 0.03, (1000, 1000))
        s[100:220, 300:330] = 2.0 + rng.normal(0, 0.03, (120, 30))   # banda de 60 µm
        s[500:620, 600:608] = 2.0                                    # línea de 16 µm
        pl, p995 = S.pliegues(s, np.ones_like(s, bool), mpp)
        self.assertLess(p995, 2.0)
        self.assertGreater(pl[100:220, 300:330].mean(), 0.9)
        self.assertFalse(pl[500:620, 600:608].any())
        c = S.candidatos_pliegue(s, np.ones_like(s, bool), pl, mpp)
        self.assertEqual(c["regiones"], [])                          # nada que la regla no viera
        self.assertEqual(c["limite"], S.LIMITE_PLIEGUE)

    def test_pliegue_grande_va_a_candidatos(self):
        """Un pliegue que ocupa > 0,5 % del tejido sube el p99,5 dentro de sí mismo: la regla del
        plan no lo marca (límite declarado) y sale como candidato, con su área, para el tribunal.
        Un nido denso de la misma ODsum saldría igual: por eso no se excluye solo."""
        mpp = 2.0
        for frac, ancho, alto in ((0.01, 30, 333), (0.03, 30, 1000)):
            rng = np.random.default_rng(11)
            s = 0.3 + rng.normal(0, 0.03, (1000, 1000))
            s[0:alto, 400:400 + ancho] = 0.7 + rng.normal(0, 0.03, (alto, ancho))
            tej = np.ones_like(s, bool)
            pl, _ = S.pliegues(s, tej, mpp)
            self.assertLess(pl[0:alto, 400:400 + ancho].mean(), 0.5, frac)   # la regla no lo ve
            c = S.candidatos_pliegue(s, tej, pl, mpp)
            verdad = ancho * alto * mpp * mpp
            self.assertTrue(c["regiones"], frac)
            self.assertAlmostEqual(c["regiones"][0]["area_um2"] / verdad, 1.0, delta=0.10,
                                   msg=frac)
            self.assertAlmostEqual(c["fraccion_tejido"], frac, delta=frac * 0.15)

    def test_fracciones_grandqc(self):
        mpp = 0.25
        rng = np.random.default_rng(8)
        xy = np.vstack([rng.uniform(0.1, 0.7, (900, 2)), rng.uniform([0.9, 0.1], [1.5, 0.7],
                                                                     (900, 2))]) * 1000 / mpp
        prov = S.mascara_provisional(xy, mpp, (8000, 4000))
        x0, y0 = 100 / mpp, 100 / mpp
        grande = box(x0, y0, x0 + 600 / mpp * 0.35, y0 + 600 / mpp)   # ~35 % del fragmento 0
        chico = box(x0, y0, x0 + 600 / mpp * 0.05, y0 + 600 / mpp)
        r = S.fracciones_grandqc([(grande, "Fold")], xy, prov)
        self.assertTrue(r["excede"])
        self.assertAlmostEqual(r["por_fragmento"]["0"]["union_nucleos"], 0.35, delta=0.05)
        self.assertEqual(len(r["marcado_nucleo"]), len(xy))
        r2 = S.fracciones_grandqc([(chico, "Out of Focus")], xy, prov)
        self.assertFalse(r2["excede"])


class Guardia(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lector = F.LectorSint({n: F.lamina(n, k) for k, n in
                                   enumerate(("P-HER2NEG", "P-KI67"))})

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="lam-guardia-")
        self.llamadas = []

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _seg(self, lector, lam, nombre, **kw):
        self.llamadas.append(nombre)
        return {"celulas": []}

    def test_diana_sin_sello_se_niega(self):
        ruta = os.path.join(self.d, "congelacion.json")
        with self.assertRaises(SL.SelloAusente):
            S.celulas(self.lector, "P-KI67", ruta, segmentador=self._seg)
        with self.assertRaises(SL.SelloAusente):
            S.celulas(self.lector, "P-KI67", None, segmentador=self._seg)
        self.assertEqual(self.llamadas, [])                     # ni un píxel segmentado
        S.celulas(self.lector, "P-HER2NEG", ruta, segmentador=self._seg)   # suelo: sí
        self.assertEqual(self.llamadas, ["P-HER2NEG"])

    def test_segmentador_real_tambien_se_niega(self):
        lam = self.lector.laminas["P-KI67"]
        with self.assertRaises(SL.SelloAusente):                # antes de abrir nada
            S.segmentador_lazyslide(self.lector, lam, "P-KI67")
        with self.assertRaises(ValueError):                     # nombre que no casa
            S.segmentador_lazyslide(self.lector, lam, "P-HER2NEG")

    def test_sello_incompleto_o_con_otros_parametros(self):
        ruta = os.path.join(self.d, "congelacion.json")
        SL.sella(ruta, {"vectores": {"H": [1, 0, 0], "DAB": [0, 1, 0]}}, FECHA)
        with self.assertRaises(SL.SelloInvalido):
            S.celulas(self.lector, "P-KI67", ruta, segmentador=self._seg)
        otro = os.path.join(self.d, "b")
        os.makedirs(otro)
        ruta2 = os.path.join(otro, "congelacion.json")
        p = S.canon(S.parametros())
        p["MASCARA_PROV"]["umbral_nucleos_mm2"] = 100.0
        sello_completo(ruta2, segmenta=p)
        with self.assertRaises(SL.SelloInvalido):
            S.celulas(self.lector, "P-KI67", ruta2, segmentador=self._seg)
        self.assertEqual(self.llamadas, [])

    def test_con_sello_y_sello_tocado(self):
        ruta = os.path.join(self.d, "congelacion.json")
        sello_completo(ruta)
        S.celulas(self.lector, "P-KI67", ruta, segmentador=self._seg)
        self.assertEqual(self.llamadas, ["P-KI67"])
        with open(ruta) as f:
            d = json.load(f)
        d["vectores"]["DAB"] = [0, 0, 1]
        with open(ruta, "w") as f:
            json.dump(d, f)
        with self.assertRaises(SL.SelloInvalido):
            S.celulas(self.lector, "P-KI67", ruta, segmentador=self._seg)


class Pesos(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="lam-pesos-")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _pesos(self, contenido, sello=None):
        ruta = os.path.join(self.d, "pesos.json")
        crudo = json.dumps(contenido).encode()
        with open(ruta, "wb") as f:
            f.write(crudo)
        with open(ruta + ".sha256", "w") as f:
            f.write((sello or hashlib.sha256(crudo).hexdigest()) + "  pesos.json\n")
        return ruta

    def test_fichero_cambiado_o_pesos_tocados_no_cargan(self):
        hf = os.path.join(self.d, "hf")
        fich = "instanseg/instanseg_v0_1_0.pt"
        carpeta = os.path.join(hf, "hub", "models--RendeiroLab--LazySlide-models", "snapshots",
                               "abc123", "instanseg")
        os.makedirs(carpeta)
        with open(os.path.join(carpeta, "instanseg_v0_1_0.pt"), "wb") as f:
            f.write(b"no son los pesos")
        bueno = hashlib.sha256(b"no son los pesos").hexdigest()
        ent = {"modelos": {S.REPO_PESOS: {"repo": "RendeiroLab/LazySlide-models",
                                          "commit": "abc123",
                                          "ficheros": {fich: {"bytes": 16, "sha256": bueno}}}}}
        ruta, medido = S.peso_verificado(fich, self._pesos(ent), hf_home=hf)
        self.assertEqual(medido["sha256"], bueno)
        ent["modelos"][S.REPO_PESOS]["ficheros"][fich]["sha256"] = "0" * 64
        with self.assertRaises(S.PesoNoSellado):                 # el fichero no es el sellado
            S.peso_verificado(fich, self._pesos(ent), hf_home=hf)
        with self.assertRaises(S.PesoNoSellado):                 # pesos.json tocado
            S.peso_verificado(fich, self._pesos(ent, sello="1" * 64), hf_home=hf)
        ent["modelos"][S.REPO_PESOS]["ficheros"][fich]["sha256"] = bueno
        ent["modelos"][S.REPO_PESOS]["commit"] = "otro"
        with self.assertRaises(S.PesoNoSellado):                 # otro commit: no está
            S.peso_verificado(fich, self._pesos(ent), hf_home=hf)

    @unittest.skipUnless(PESOS_INSTANSEG, "sin los pesos de InstanSeg en la caché HF (F1.0)")
    def test_instanseg_sellado(self):
        ruta, medido = S.peso_verificado(S.INSTANSEG["fichero"], hf_home=HF)
        self.assertEqual((medido["sha256"], medido["bytes"]),
                         (S.INSTANSEG["sha256"], S.INSTANSEG["bytes"]))


@unittest.skipUnless(PESOS_INSTANSEG, "sin los pesos de InstanSeg en la caché HF (F1.0)")
class InstanSegReal(unittest.TestCase):
    """El camino del plan, de punta a punta, sobre un Grundium sintético."""

    @classmethod
    def setUpClass(cls):
        mpp = 0.2506
        W, H = 3072, 2560
        rng = np.random.default_rng(3)
        paso = 18.0 / mpp
        # La verdad es FÍSICA: radios en µm (3,0-3,5: 28-38 µm²), pintados a mpp 0,2506 en L0.
        cls.r_um = rng.uniform(3.0, 3.5, 10_000)
        nuc = [(cx + rng.uniform(-8, 8), cy + rng.uniform(-8, 8), 0.0, 0)
               for cy in np.arange(250, H - 250, paso) for cx in np.arange(250, W - 700, paso)]
        nuc = np.asarray(nuc)
        cls.r_um = cls.r_um[:len(nuc)]
        nuc[:, 2] = cls.r_um / mpp
        tej = np.zeros((H, W), bool)
        tej[200:H - 200, 200:W - 650] = True
        cH = np.where(tej, 0.05, 0.0)
        nm = F.mascara_nuclear(nuc, (H, W))
        cH = np.where(nm, F.discos(nuc, (H, W), rng.uniform(0.45, 0.7, len(nuc))), cH)
        img = F.pinta(cH, np.zeros((H, W)), semilla=4)
        img[:512, W - 512:] = 255                               # relleno: fuera de la zona
        cls.img = img
        cls.d = tempfile.mkdtemp(prefix="lam-instanseg-")
        ruta = os.path.join(cls.d, "sintetica.tiff")
        escribe_grundium(ruta, img, mpp)
        cls.zona = box(0, 0, W, H).difference(box(W - 512, 0, W, 512))
        cls.nuc, cls.mpp, cls.W, cls.H = nuc, mpp, W, H

        class Lam:
            mpp_l0 = mpp
            dimensiones_l0 = (W, H)
        Lam.ruta = ruta

        class Lec:
            def manifiesto(self):
                return {}
        Lam.opaco = "P-HER2NEG"
        cls.ruta = ruta
        cls.out = S.segmentador_lazyslide(Lec(), Lam(), "P-HER2NEG", zona=cls.zona,
                                          dir_trabajo=cls.d, con_grandqc=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_pesos_sellados(self):
        with open(PESOS_INSTANSEG[0], "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(h, S.INSTANSEG["sha256"])
        self.assertEqual(self.out["pesos"]["sha256"], S.INSTANSEG["sha256"])   # el CARGADO

    def test_teselado_del_plan(self):
        t = self.out["teselado"]
        self.assertEqual(t["via"], "zona")
        self.assertEqual(t["n"], t["esperado"])
        self.assertEqual(t["descartadas"], 0)
        self.assertAlmostEqual(t["tile_spec_mpp"], 0.5, delta=0.01)
        self.assertTrue(t["pasa"])
        self.assertFalse(self.out["aviso_check_input_tile"])
        self.assertEqual(self.out["lote"], 1)
        import torch
        self.assertEqual(self.out["dispositivo"],
                         "mps" if torch.backends.mps.is_available() else "cpu")

    def test_nucleos_recuperados(self):
        pol = self.out["celulas"]
        self.assertAlmostEqual(len(pol) / len(self.nuc), 1.0, delta=0.05)
        lab = S.rasteriza(pol, 0, 0, (self.H, self.W))
        self.assertGreaterEqual(S.cobertura(self.nuc[:, :2], lab), 0.95)
        b = np.asarray([p.bounds for p in pol])
        self.assertTrue((b[:, 0] >= 0).all() and (b[:, 2] <= self.W).all())

    def test_poligonos_en_px_de_l0_y_area_fisica(self):
        """Contra la verdad FÍSICA (radios en µm), sin pasar por el código que se prueba: (1) el
        diámetro de los polígonos, en px de L0, es 2·r/mpp_l0 (si vinieran en px de 0,5 µm/px
        saldría la mitad); (2) su área en µm² cae en [0,80; 1,00] de π·r²: el contorno de
        LazySlide pasa por los centros de los píxeles del borde y pierde ~medio píxel (0,25 µm)
        de radio (medido: 0,90 con r = 3,5 µm); un error de unidades daría 0,25."""
        pol = self.out["celulas"]
        b = np.asarray([p.bounds for p in pol])
        diam_l0 = float(np.median(b[:, 2] - b[:, 0]))
        self.assertAlmostEqual(diam_l0 / (2 * float(np.median(self.r_um)) / self.mpp), 1.0,
                               delta=0.12)
        m = S.mediana_area(pol, self.mpp)
        cociente = m["mediana_um2"] / float(np.median(np.pi * self.r_um ** 2))
        self.assertGreaterEqual(cociente, 0.80)
        self.assertLessEqual(cociente, 1.00)

    def test_diagnostico_area_contra_verdad_fisica(self):
        """`laminillas_congela.diagnostico_area` (el modo de diagnóstico de la pre-congelación) con
        el InstanSeg real: detector B y el mapa del modelo (píxeles) a ± 10 % de π·r²; los
        polígonos de producción, el contorno del mapa (mismas unidades); cociente contorno /
        píxeles < 1 (lo que pierde la poligonización)."""
        import laminillas_congela as K
        lam = F.LaminaSint("P-HER2NEG", self.img, self.mpp, self.zona)
        lam.ruta = self.ruta
        r = K.diagnostico_area(F.LectorSint({"P-HER2NEG": lam}), "P-HER2NEG", n_teselas=4)
        verdad = float(np.median(np.pi * self.r_um ** 2))
        self.assertGreaterEqual(r["teselas"], 1)
        self.assertAlmostEqual(r["detector_b_um2"]["mediana"] / verdad, 1.0, delta=0.10)
        self.assertAlmostEqual(r["instanseg_mapa_pixeles_um2"]["mediana"] / verdad, 1.0,
                               delta=0.10)
        self.assertAlmostEqual(r["instanseg_poligono_um2"]["mediana"]
                               / r["instanseg_mapa_contorno_um2"]["mediana"], 1.0, delta=0.03)
        c = r["contorno_entre_pixeles"]["mediana"]
        self.assertTrue(0.80 <= c < 0.95, c)
        self.assertAlmostEqual(r["base_downsample"], 0.5 / self.mpp, places=6)

    def test_rejilla_desplazada(self):
        c1 = S.centroides(self.out["celulas"])
        c2 = S.centroides(self.out["celulas_desplazadas"])
        prov = S.mascara_provisional(c1, self.mpp, (self.W, self.H))
        r = S.cambio_rejilla(c1, c2, prov)
        self.assertTrue(r["pasa"], r)

    def test_grandqc_carga(self):
        g = self.out["grandqc"]
        self.assertTrue(g["carga"], g.get("motivo"))
        self.assertIsInstance(g["artefactos"], list)
        self.assertEqual(g["pesos"]["fichero"], S.GRANDQC["fichero"])

    def test_grandqc_reintenta_en_cpu(self):
        from wsidata import open_wsi
        from wsidata.io import add_tissues
        wsi = open_wsi(self.ruta, reader="openslide",
                       store=os.path.join(self.d, "reintento.zarr"))
        add_tissues(wsi, "zona", [self.zona])
        g = S._grandqc(wsi, self.zona, self.mpp, dispositivos=["dispositivo-inexistente", "cpu"])
        self.assertTrue(g["carga"], g.get("motivo"))
        self.assertEqual(g["dispositivo"], "cpu")
        self.assertEqual(len(g["intentos"]), 2)
        self.assertIsNotNone(g["intentos"][0]["error"])


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=1).result
    print("test_laminillas_piloto_segmenta: %d casos, %d fallos, %d errores, %d saltados"
          % (r.testsRun, len(r.failures), len(r.errors), len(r.skipped)))
    sys.exit(0 if r.wasSuccessful() else 1)
