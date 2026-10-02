#!/usr/bin/env python3
"""tests/test_laminillas_piloto_color.py — `tools/laminillas_color.py` (módulo A del piloto).

SIN DATOS: imágenes sintéticas pintadas por Beer-Lambert con vectores H y DAB CONOCIDOS y
concentraciones conocidas. Se verifica el EFECTO: que se recuperan los parámetros conocidos con
tolerancias declaradas, y que los rótulos son las frases EXACTAS del plan.

Tolerancias declaradas:
  · vector H ≤ 1°; vector DAB ≤ 3° (Macenko anclado: percentil 99 del ángulo, con ruido), también
    con contratinción H 0,05 en TODO el citoplasma DAB+ (sin píxeles de DAB puro); el sesgo que
    se sella crece con la contratinción y es 0 sin ella;
  · DAB medio por núcleo: ± 0,02 OD del conocido; negativos |DAB| ≤ 0,01;
  · residuo: la lámina con DAB girado 15° supera 2× el máximo de la tanda y su DAB propio sale a
    ≤ 3° del verdadero;
  · contratinción: H girado 15° → fuera del mapa; 5° → dentro;
  · T: percentiles exactos (np.percentile), régimen y rótulo exactos; el IC95 por bootstrap de
    fragmentos contiene el p99,9 medido;
  · tasa en P-HER2: 12 falsos positivos agrupados en un bloque → el bootstrap de bloques ensancha
    el IC y rige; con k = 0 rige el binomial;
  · máscara CK19: umbral en el valle (entre los dos modos), núcleos epiteliales dentro ≥ 95 %,
    estromales a > 8 µm del epitelio fuera ≥ 95 %; componente de 100 µm² fuera; sin
    bimodalidad, Otsu y su rótulo; un modo en el borde del histograma también es modo; un núcleo
    fuera de fragmento (−1), un fragmento de estroma sin epitelio o uno con < 50 núcleos NO
    marcan «denominator-dependent»; pertenencia por floor (el píxel i cubre [i, i+1)).
  · artefacto «saturados no H/DAB» v2 (actualización 19): un píxel recortado (DAB 3,0 en el plano,
    o con OD fuera del plano) no es artefacto; la tinta verde viva sí; el negro acromático
    recortado ya no lo es (hueco declarado); (i) y (ii) siguen sin los píxeles recortados.
Corre con el python del venv `patologia` (se re-ejecuta solo); sin venv, SKIP (77).
"""
import os
import sys
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import _laminillas_piloto_a as F  # noqa: E402

F.al_venv(__file__)
sys.path.insert(0, os.path.join(os.path.dirname(AQUI), "tools"))

import numpy as np  # noqa: E402

import laminillas_color as C  # noqa: E402

PLAN = {   # frases exactas del plan (Puerta del piloto, F3, Nucleares, Métodos)
    "suelo": "threshold at fixed floor (0.10 OD); measured floor p99.9 = ",
    "high_floor": "high floor",
    "dab_propio": "slide-specific DAB vector",
    "contratincion": "counterstain differs",
    "ck19_no_bimodal": "CK19 threshold not bimodal",
    "denominador": "denominator-dependent",
    "hscore": "fixed, uncalibrated cut-offs",
    "ck19_sin_verdad": "no independent ground truth for the epithelial mask; editable layer provided",
    "metodos_T": "fixed after a pixel-level stain reconnaissance, before any cell-level measurement",
    "her2neg": "floor slide",
}


def _od_mezcla(n, ch, cd, h=F.H_REAL, d=F.D_REAL, semilla=0, ruido=1.5):
    """OD de n píxeles sintéticos pasados por RGB de 8 bits (ruido y cuantización reales)."""
    rgb = F.pinta(np.full((1, n), 0.0) + ch, np.full((1, n), 0.0) + cd, h=h, d=d, ruido=ruido,
                  semilla=semilla)
    return C.od(rgb, F.I0).reshape(-1, 3), rgb.reshape(-1, 3)


class Rotulos(unittest.TestCase):
    def test_frases_exactas(self):
        for k, frase in PLAN.items():
            if k == "suelo":
                self.assertTrue(C.ROTULOS[k].startswith(frase))
                self.assertEqual(C.ROTULOS[k].format(p999=0.0312),
                                 "threshold at fixed floor (0.10 OD); measured floor p99.9 = 0.031")
            else:
                self.assertEqual(C.ROTULOS[k], frase)


class Vectores(unittest.TestCase):
    def test_h_desde_pixeles_nucleares(self):
        rng = np.random.default_rng(1)
        od, _ = _od_mezcla(20000, rng.uniform(0.3, 0.8, 20000), 0.0, semilla=1)
        h, _ = C.vector_h(od)
        self.assertLessEqual(F.angulo(h, F.H_REAL), 1.0)
        self.assertGreater(F.angulo(F.H_REAL, C.RUIFROK_H), 3.0)    # no es el de Ruifrok

    def test_dab_macenko_anclado(self):
        rng = np.random.default_rng(2)
        n = 30000
        ch = rng.uniform(0.0, 0.6, n)
        cd = rng.uniform(0.1, 0.6, n)
        ch[: n // 10] = 0.0                    # citoplasma con DAB puro (CK19, SYN)
        od, _ = _od_mezcla(n, ch, cd, semilla=2)
        d, _ = C.vector_dab(od, F.H_REAL)
        self.assertLessEqual(F.angulo(d, F.D_REAL), 3.0)

    def test_dab_con_contratincion_citoplasmica(self):
        """Sin un solo píxel de DAB puro: todo el citoplasma DAB+ lleva H 0,05 (realista)."""
        rng = np.random.default_rng(12)
        n = 30000
        ch = rng.uniform(0.0, 0.6, n)
        cd = rng.uniform(0.1, 0.6, n)
        cito = np.arange(n) < n // 3
        ch[cito] = 0.05
        cd[cito] = rng.uniform(0.3, 0.6, int(cito.sum()))
        od, _ = _od_mezcla(n, ch, cd, semilla=12)
        d, _ = C.vector_dab(od, F.H_REAL)
        self.assertLessEqual(F.angulo(d, F.D_REAL), 3.0)
        M = C.matriz(F.H_REAL, F.D_REAL)
        sesgo = C.sesgo_dab_contratincion(F.H_REAL, F.D_REAL, C.desmezcla(od, M)[:, 1])
        self.assertLess(sesgo["0.00"], 0.01)
        self.assertGreater(sesgo["0.05"], 0.5)
        self.assertGreater(sesgo["0.10"], sesgo["0.05"])

    def test_sin_od_mayor_que_1(self):
        """Píxeles saturados de otro color (OD > 1) no arrastran el vector."""
        rng = np.random.default_rng(3)
        od, _ = _od_mezcla(20000, np.where(rng.random(20000) < 0.1, 0.0, 0.3),
                           rng.uniform(0.2, 0.5, 20000), semilla=3)
        basura = np.tile([1.6, 0.2, 1.4], (3000, 1))           # OD > 1: fuera
        d, _ = C.vector_dab(np.vstack([od, basura]), F.H_REAL)
        self.assertLessEqual(F.angulo(d, F.D_REAL), 3.0)

    def test_desmezcla_recupera_concentraciones(self):
        rng = np.random.default_rng(4)
        M = C.matriz(F.H_REAL, F.D_REAL)
        for cd in (0.0, 0.15, 0.4, 0.7):
            ch = rng.uniform(0.4, 0.7, 4000)
            od, _ = _od_mezcla(4000, ch, cd, semilla=int(cd * 100))
            c = C.desmezcla(od, M)
            self.assertAlmostEqual(float(c[:, 1].mean()), cd, delta=0.02 if cd else 0.01)
            self.assertAlmostEqual(float(c[:, 0].mean()), float(ch.mean()), delta=0.02)

    def test_dab_del_od_medio_es_lineal(self):
        M = C.matriz(F.H_REAL, F.D_REAL)
        od = np.random.default_rng(5).uniform(0, 1, (500, 3))
        self.assertAlmostEqual(float(C.dab_de(od.mean(0), M)),
                               float(C.desmezcla(od, M)[:, 1].mean()), places=12)


class Residuo(unittest.TestCase):
    def _tanda(self):
        rng = np.random.default_rng(6)
        M = C.matriz(F.H_REAL, F.D_REAL)
        res = {}
        for k, nombre in enumerate(("P-HER2", "P-HER2NEG", "P-KI67", "P-SYN", "P-CK19")):
            od, _ = _od_mezcla(20000, rng.uniform(0.0, 0.5, 20000),
                               rng.uniform(0.0, 0.5, 20000), semilla=10 + k)
            res[nombre], _ = C.residuo(od, M)
        return M, res

    def test_umbral_y_dab_propio(self):
        M, res = self._tanda()
        u = C.umbral_residuo(res)
        self.assertAlmostEqual(u, 2 * max(res.values()))
        eje = np.cross(F.H_REAL, F.D_REAL)
        # DAB girado 15° FUERA del plano H-DAB de la tanda (eje en el plano, ⟂ a DAB)
        otro = F.rota(F.D_REAL, np.cross(F.D_REAL, eje), 15.0)
        self.assertAlmostEqual(F.angulo(otro, F.D_REAL), 15.0, delta=0.5)
        rng = np.random.default_rng(7)
        n = 20000
        ch = rng.uniform(0.0, 0.5, n)
        ch[: n // 10] = 0.0
        od, rgb = _od_mezcla(n, ch, rng.uniform(0.2, 0.6, n), d=otro, semilla=20)
        r, _ = C.residuo(od, M)
        self.assertGreater(r, u)
        ev = C.evalua_residuo(r, u, F.H_REAL, od, C.saturado_intensidad(rgb))
        self.assertTrue(ev["supera"])
        self.assertEqual(ev["rotulos"], [PLAN["dab_propio"]])
        self.assertLessEqual(F.angulo(ev["dab_propio"], otro), 3.0)
        # (i): el plano de la lámina se aparta ~ lo mismo que su DAB del de la tanda
        _, _, normal = C.direccion_dab_lamina(od, F.H_REAL)
        self.assertGreater(F.angulo(normal, eje), 10.0)
        # una lámina de la tanda no lo supera
        self.assertFalse(C.evalua_residuo(min(res.values()), u, F.H_REAL)["supera"])

    def test_contratincion(self):
        rng = np.random.default_rng(8)
        eje = np.cross(F.H_REAL, F.D_REAL)
        for grados, fuera in ((15.0, True), (5.0, False)):
            h2 = F.rota(F.H_REAL, eje, grados)
            od, _ = _od_mezcla(20000, rng.uniform(0.3, 0.7, 20000), 0.0, h=h2,
                               semilla=int(grados))
            r = C.contratincion(od, F.H_REAL)
            self.assertAlmostEqual(r["angulo"], grados, delta=1.0)
            self.assertEqual(r["fuera_mapa"], fuera)
            self.assertEqual(r["rotulos"], [PLAN["contratincion"]] if fuera else [])


class Umbral(unittest.TestCase):
    def _objetos(self, media, sd, n=4000, frag=4, semilla=0):
        rng = np.random.default_rng(semilla)
        return rng.normal(media, sd, n), rng.integers(0, frag, n)

    def test_suelo_fijo(self):
        v, f = self._objetos(0.0, 0.01)
        ex = np.zeros(len(v), bool)
        v2 = np.concatenate([v, np.full(30, 2.0)])                 # artefactos, EXCLUIDOS
        f2 = np.concatenate([f, np.zeros(30, int)])
        ex2 = np.concatenate([ex, np.ones(30, bool)])
        r = C.umbral_T(v2, f2, ex2, n_boot=500, semilla=1)
        p99, p999 = np.percentile(v, [99, 99.9])
        self.assertAlmostEqual(r["p99"], p99, places=12)
        self.assertAlmostEqual(r["p999"], p999, places=12)
        self.assertEqual(r["n_objetos"], len(v))
        self.assertEqual(r["n_sobre_p999"], int((v > p999).sum()))
        self.assertEqual(r["n_sobre_p99"], int((v > p99).sum()))
        self.assertEqual(r["T"], 0.10)
        self.assertEqual(r["rige"], "suelo_fijo")
        self.assertEqual(r["rotulos"],
                         ["threshold at fixed floor (0.10 OD); measured floor p99.9 = %.3f" % p999])
        lo, hi = r["ic95_p999"]
        self.assertLessEqual(lo, p999)
        self.assertGreaterEqual(hi, p999)
        for a, b in zip(r["banda"], [max(0.05, p999 + 0.02), 0.15]):
            self.assertAlmostEqual(a, b, places=12)
        self.assertEqual(r["estado"], "ok")

    def test_p999_mas_005(self):
        v, f = self._objetos(0.12, 0.02, semilla=2)
        r = C.umbral_T(v, f, n_boot=200)
        p999 = float(np.percentile(v, 99.9))
        self.assertAlmostEqual(r["T"], p999 + 0.05, places=12)
        self.assertEqual(r["rige"], "p99.9+0.05")
        self.assertEqual(r["rotulos"], [])
        self.assertEqual(r["estado"], "ok")
        self.assertEqual(r["banda"], [max(r["T"] - 0.05, p999 + 0.02), r["T"] + 0.05])

    def test_rama_alta(self):
        v, f = self._objetos(0.2, 0.02, semilla=3)
        r = C.umbral_T(v, f, n_boot=50)
        self.assertGreater(r["T"], 0.25)
        self.assertEqual(r["estado"], "alto")

    def test_tasa_clopper_pearson(self):
        v = np.zeros(1000)
        r = C.tasa_sobre_T(v, 0.10)
        self.assertEqual((r["k"], r["n"]), (0, 1000))
        self.assertAlmostEqual(r["ic95"][1], 1 - 0.025 ** (1 / 1000), places=10)
        v[:3] = 0.5
        r = C.tasa_sobre_T(v, 0.10)
        self.assertEqual(r["k"], 3)
        self.assertLess(r["ic95"][0], 0.003)
        self.assertGreater(r["ic95"][1], 0.003)

    def test_tasa_bootstrap_de_bloques(self):
        """20.000 células en 4 fragmentos (bloques de 200 µm); 12 falsos positivos agrupados en un
        solo bloque. Clopper-Pearson (células independientes) es estrecho; el bootstrap de bloques
        lo ensancha y rige. Con k = 0, el bootstrap da [0, 0] y rige el binomial."""
        rng = np.random.default_rng(13)
        n = 20000
        bloques = np.column_stack([rng.integers(0, 4, n), rng.integers(0, 10, n),
                                   rng.integers(0, 10, n)])
        v = np.zeros(n)
        foco = np.flatnonzero((bloques == [0, 3, 3]).all(axis=1))[:12]
        v[foco] = 0.5
        r = C.tasa_sobre_T(v, 0.10, bloques=bloques, semilla=1)
        self.assertEqual(r["k"], 12)
        self.assertGreater(r["ic95_bootstrap"][1], r["ic95_binomial"][1])
        self.assertEqual(r["ic95_rige"], "bootstrap de bloques")
        self.assertEqual(r["ic95"], r["ic95_bootstrap"])
        r0 = C.tasa_sobre_T(np.zeros(n), 0.10, bloques=bloques, semilla=1)
        self.assertEqual(r0["ic95_bootstrap"], [0.0, 0.0])
        self.assertEqual(r0["ic95_rige"], "binomial (Clopper-Pearson)")
        self.assertAlmostEqual(r0["ic95"][1], 1 - 0.025 ** (1 / n), places=10)

    def test_hscore(self):
        v = np.concatenate([np.full(50, 0.05), np.full(20, 0.2), np.full(20, 0.5),
                            np.full(10, 0.9)])
        h = C.hscore(v, 0.10)
        self.assertEqual(h["pct"], [50.0, 20.0, 20.0, 10.0])
        self.assertAlmostEqual(h["H"], 20 + 40 + 30)
        self.assertEqual(h["cortes"], [0.10, 0.4, 0.6])
        self.assertEqual(h["rotulos"], [PLAN["hscore"]])
        self.assertFalse(h["cortes_colapsados"])


class MascaraCK19(unittest.TestCase):
    def _imagen(self, bimodal=True, semilla=0):
        """1 µm/px: epitelio (DAB 0,45, con huecos nucleares) en dos bloques; estroma 0,02."""
        rng = np.random.default_rng(semilla)
        Hh, Ww = 300, 400
        dab = np.full((Hh, Ww), 0.02)
        epi = np.zeros((Hh, Ww), bool)
        epi[30:270, 30:180] = True
        epi[30:270, 220:370] = True
        if bimodal:
            dab[epi] = 0.45
        else:
            dab = np.clip(rng.normal(0.2, 0.12, (Hh, Ww)), 0, None)
        nuc = []
        for y in range(40, 262, 12):
            for x in range(40, 362, 12):
                nuc.append((x + rng.uniform(-2, 2), y + rng.uniform(-2, 2)))
        nuc = np.asarray(nuc)
        yy, xx = np.mgrid[0:Hh, 0:Ww]
        for x, y in nuc:                                             # huecos nucleares (r 3,5 µm)
            m = (xx - x) ** 2 + (yy - y) ** 2 <= 3.5 ** 2
            dab[m] = 0.01
        dab[290:300, 0:10] = 0.6 if bimodal else dab[290:300, 0:10]  # 100 µm²: fuera
        dab += rng.normal(0, 0.01, dab.shape)
        tej = np.ones((Hh, Ww), bool)
        return dab, tej, epi, nuc

    def test_valle_y_pertenencia(self):
        dab, tej, epi, nuc = self._imagen()
        mask, suave, info = C.mascara_ck19(dab, tej, 1.0)
        self.assertEqual(info["regla"], "valle")
        self.assertEqual(info["rotulos"], [])
        lo, hi = sorted(info["picos"])
        self.assertTrue(lo < info["T"] < hi)
        self.assertLessEqual(info["cociente_valle"], 0.5)
        self.assertFalse(mask[290:300, 0:10].any())                 # < 200 µm²: fuera
        dentro = C.dentro(mask, nuc)
        en_epi = epi[nuc[:, 1].astype(int), nuc[:, 0].astype(int)]
        from scipy.ndimage import distance_transform_edt
        lejos = distance_transform_edt(~epi)[nuc[:, 1].astype(int), nuc[:, 0].astype(int)] > 8
        self.assertGreaterEqual(dentro[en_epi].mean(), 0.95)        # cierre: núcleos dentro
        self.assertLessEqual(dentro[lejos].mean(), 0.05)            # estroma a > 8 µm: fuera
        frag = (nuc[:, 0] > 200).astype(int)
        s = C.sensibilidad_ck19(suave, tej, info["T"], nuc, frag, 1.0)
        self.assertFalse(s["dependiente"])
        self.assertEqual(s["rotulos"], [])

    def test_sensibilidad_sin_falsos_dependientes(self):
        """Un núcleo suelto fuera de fragmento (−1), un fragmento de estroma sin epitelio y uno
        pequeño (< 50 núcleos): no son «un fragmento» donde cambie el denominador."""
        dab, tej, epi, nuc = self._imagen()
        _, suave, info = C.mascara_ck19(dab, tej, 1.0)
        frag = (nuc[:, 0] > 200).astype(int)
        # un grupito aislado con DAB 0,85×T: entra con ×0,75 (0 → 1)
        aislado = np.array([[5.0, 290.0]])
        suave2 = suave.copy()
        suave2[285:296, 0:12] = 0.85 * info["T"]
        xy = np.vstack([nuc, aislado])
        fr = np.concatenate([frag, [-1]])
        s = C.sensibilidad_ck19(suave2, tej, info["T"], xy, fr, 1.0)
        self.assertFalse(s["dependiente"], s["por_fragmento"])
        self.assertNotIn("-1", s["por_fragmento"])
        self.assertEqual(s["fuera_de_fragmento"], 1)
        # fragmento 2 = estroma sin epitelio (0 núcleos en la base) y uno de 5 núcleos
        estroma = np.array([[195.0 + i * 0.5, 100.0 + i] for i in range(30)])
        fr3 = np.concatenate([frag, np.full(len(estroma), 2)])
        s3 = C.sensibilidad_ck19(suave, tej, info["T"], np.vstack([nuc, estroma]), fr3, 1.0)
        self.assertFalse(s3["por_fragmento"]["2"]["dependiente"])
        self.assertIn("2", s3["no_evaluables"])
        self.assertFalse(s3["dependiente"])
        self.assertEqual(s3["por_fragmento"]["0"]["rotulos"], [])

    def test_pertenencia_por_floor(self):
        m = np.zeros((20, 20), bool)
        m[:, 10:] = True
        self.assertFalse(C.dentro(m, [[9.6, 5.0]])[0])               # píxel 9: fuera
        self.assertTrue(C.dentro(m, [[10.0, 5.0]])[0])
        self.assertTrue(C.dentro(m, [[10.9, 5.0]])[0])

    def test_modo_en_el_borde_del_histograma(self):
        """El estroma sin DAB cae en el primer bin (p0,1): también es un modo."""
        rng = np.random.default_rng(11)
        v = np.concatenate([np.full(50000, 0.0) + rng.normal(0, 0.0005, 50000),
                            rng.uniform(0.05, 0.40, 5000), np.full(40000, 0.44)])
        info = C.umbral_ck19(v)
        self.assertEqual(info["regla"], "valle")
        self.assertTrue(0.0 < info["T"] < 0.44)

    def test_no_bimodal_otsu(self):
        dab, tej, _, nuc = self._imagen(bimodal=False, semilla=1)
        from skimage.filters import gaussian, threshold_otsu
        _, suave, info = C.mascara_ck19(dab, tej, 1.0)
        self.assertEqual(info["regla"], "otsu")
        self.assertEqual(info["rotulos"], [PLAN["ck19_no_bimodal"]])
        g = gaussian(dab, sigma=2.0, preserve_range=True)
        self.assertAlmostEqual(info["T"], float(threshold_otsu(g[tej])), places=9)
        frag = (nuc[:, 0] > 200).astype(int)
        s = C.sensibilidad_ck19(suave, tej, info["T"], nuc, frag, 1.0)
        self.assertTrue(s["dependiente"])                           # ×0,75/×1,25 mueve > 10 %
        self.assertEqual(s["rotulos"], [PLAN["denominador"]])


class Parametros(unittest.TestCase):
    def test_todo_lo_sellable(self):
        import json
        p = C.parametros()
        json.dumps(p)
        for k in ("ODSUM_MIN_COLOR", "ODSUM_MIN_VECTOR", "FRANJA_UMBRAL", "CK19", "TASA",
                  "ANGULO_CONTRATINCION", "SAT_HSV_MIN", "FUERA_PLANO_MIN", "PERCENTIL_DAB"):
            self.assertIn(k, p)
        self.assertEqual(p["CK19"]["min_nucleos_fragmento"], 50)


class I0YArtefactos(unittest.TestCase):
    def test_franjas(self):
        yy, xx = np.mgrid[0:60, 0:80]
        suave = 240 + 0.05 * xx + 0.03 * yy
        self.assertFalse(C.franjas_i0(np.dstack([suave] * 3))["hay_franjas"])
        franjas = suave + np.where((xx // 4) % 2 == 0, 5.0, 0.0)
        self.assertTrue(C.franjas_i0(np.dstack([franjas] * 3))["hay_franjas"])

    def test_i0_region_funcion_del_lector(self):
        """Como `laminillas_lector.i0_local`: f(x_l0, y_l0) → RGB bilineal."""
        def f(x, y):
            x, y = np.broadcast_arrays(np.asarray(x, float), np.asarray(y, float))
            return np.stack([200 + x / 1000.0, 210 + y / 1000.0, np.full(x.shape, 220.0)], -1)
        r = C.i0_region(f, 1000, 2000, 50, 40, 1.0, 0.25)          # 1 µm/px desde L0 0,25
        self.assertEqual(r.shape, (40, 50, 3))
        self.assertAlmostEqual(float(r[0, 0, 0]), 200 + (1000 + 32) / 1000.0, delta=0.07)
        self.assertAlmostEqual(float(r[-1, -1, 1]), 210 + (2000 + 4 * 40) / 1000.0, delta=0.07)
        self.assertTrue(np.all(r[..., 2] == 220.0))
        self.assertEqual(C.i0_region({"mapa": np.full((4, 4, 3), 233.0), "mpp": 100.0},
                                     0, 0, 10, 10, 1.0, 0.25).shape, (10, 10, 3))

    def test_saturados_no_hdab(self):
        M = C.matriz(F.H_REAL, F.D_REAL)
        tinta = np.array([[[40, 160, 60]]], np.uint8)               # rotulador verde
        h = F.pinta(np.array([[0.6]]), np.array([[0.0]]), ruido=0)
        d = F.pinta(np.array([[0.0]]), np.array([[0.6]]), ruido=0)
        self.assertFalse(C.saturado_intensidad(tinta)[0, 0])
        self.assertTrue(C.saturados_no_hdab(tinta, M, F.I0)[0, 0])
        self.assertFalse(C.saturados_no_hdab(h, M, F.I0)[0, 0])
        self.assertFalse(C.saturados_no_hdab(d, M, F.I0)[0, 0])

    def test_recortado_no_es_artefacto(self):
        """Regla v2 (actualización 19 del plan): un píxel recortado en intensidad NO es artefacto
        no H/DAB, ni por el recorte (v1: `vivo | recortado`) ni por su color (su OD no da
        dirección fiable). Con la v1, o con `vivo` sin quitar los recortados, esto falla."""
        M = C.matriz(F.H_REAL, F.D_REAL)
        # DAB 3,0 OD + H 0,5 en el plano de la tanda: verde y azul recortados (≤ 2)
        oscuro = F.pinta(np.array([[0.5]]), np.array([[3.0]]), ruido=0)
        self.assertEqual(oscuro[0, 0].tolist(), [14, 2, 1])
        self.assertTrue(C.saturado_intensidad(oscuro)[0, 0])
        self.assertFalse(C.color_vivo_no_hdab(oscuro, M, F.I0)[0, 0])
        self.assertFalse(C.saturados_no_hdab(oscuro, M, F.I0)[0, 0])
        # recortado y con el OD (no fiable) fuera del plano: el criterio de color lo marcaría
        # (es DAB 3,0 girado 20° fuera del plano); como está recortado, no cuenta
        fuera = np.array([[[5, 33, 1]]], np.uint8)
        self.assertTrue(C.saturado_intensidad(fuera)[0, 0])
        self.assertTrue(C.color_vivo_no_hdab(fuera, M, F.I0)[0, 0])
        self.assertFalse(C.saturados_no_hdab(fuera, M, F.I0)[0, 0])
        # negro acromático recortado (tinta negra, pigmento): la v1 lo contaba por el recorte; la
        # v2 no lo ve (sin color vivo). Es el hueco declarado de la regla, no un acierto
        negro = np.array([[[1, 1, 2]]], np.uint8)
        self.assertFalse(C.color_vivo_no_hdab(negro, M, F.I0)[0, 0])
        self.assertFalse(C.saturados_no_hdab(negro, M, F.I0)[0, 0])

    def test_regla_versionada_en_parametros(self):
        p = C.parametros()
        self.assertEqual(p["REGLA_SATURADO"]["version"], 2)
        self.assertIn("no recortados", p["REGLA_SATURADO"]["artefacto"])
        self.assertEqual(C.ROTULOS["dab_recortado"].format(pct=12.345),
                         "DAB OD clipped in 12.3 % of cells; H-score is a lower bound for them")

    def test_residuo_y_direccion_sin_recortados(self):
        """(i) y (ii) excluyen los píxeles marcados como recortados aunque su OD caiga en el rango
        de selección (0,3-1,0)."""
        rng = np.random.default_rng(3)
        # ODsum 0,44-0,93: todos dentro de la selección de (ii)
        od = np.outer(rng.uniform(0.25, 0.55, 400), F.D_REAL) + 0.02 * np.asarray(F.H_REAL)
        marca = np.zeros(400, bool)
        marca[:100] = True
        M = C.matriz(F.H_REAL, F.D_REAL)
        _, n_todos = C.residuo(od, M)
        _, n_sin = C.residuo(od, M, marca)
        self.assertEqual((n_todos, n_sin), (400, 300))
        _, n_todos, _ = C.direccion_dab_lamina(od, F.H_REAL)
        _, n_sin, _ = C.direccion_dab_lamina(od, F.H_REAL, marca)
        _, n_quitados, _ = C.direccion_dab_lamina(od[~marca], F.H_REAL)
        self.assertEqual(n_sin, n_quitados)                          # marcar = quitar
        self.assertLess(n_sin, n_todos)


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=1).result
    print("test_laminillas_piloto_color: %d casos, %d fallos, %d errores, %d saltados"
          % (r.testsRun, len(r.failures), len(r.errors), len(r.skipped)))
    sys.exit(0 if r.wasSuccessful() else 1)
