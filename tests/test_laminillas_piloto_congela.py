#!/usr/bin/env python3
"""tests/test_laminillas_piloto_congela.py — `tools/laminillas_congela.py` (módulo A del piloto).

SIN DATOS: las 11 IHQ con tejido son sintéticas (`_laminillas_piloto_a.tanda`): vectores H y DAB
conocidos y distintos de Ruifrok, núcleos con H 0,45-0,70, KI67 con 30 % de núcleos DAB+ (0,35-0,70),
SYN con 50 % de anillos DAB 0,25, CK19 con epitelio DAB 0,30-0,60 y contratinción 0,05 en el
citoplasma; HER2NEG y HER2 sin DAB (suelo). El segmentador es un sustituto con la VERDAD (InstanSeg
real se prueba en test_laminillas_piloto_segmenta). La memoria se sella desde trazas sintéticas con
el formato de guarda_memoria.

Se verifica el EFECTO, con tolerancias declaradas:
  · pre-congelación solo abre P-HER2NEG y P-HER2 y pasa; sin (b)(c) del lector no pasa; con sello
    no se pisa;
  · vectores de tanda: H ≤ 1°, DAB ≤ 3° de los verdaderos (con contratinción citoplasmática);
  · T del suelo = 0,10 con la frase exacta y el p99,9 medido, recalculable desde los objetos;
    tasa en P-HER2 0/n con IC binomial (rige con k = 0) y bootstrap de bloques; umbral de memoria
    = pico de la traza + 2 GB, y un número tecleado no vale;
  · % recuperado tras el sello: P-KI67 núcleos sobre T a ± 3 puntos de la verdad; P-SYN anillos
    sobre T a ± 3 puntos;
  · el sello verifica y el MÓDULO B lo lee (umbral, fp_her2, vectores, P-RE con DAB propio);
  · guardia: sin sello, ni células, ni pasada de objetos, ni segmentador, ni máscara CK19 de una
    diana; con sello tocado, incompleto o con parámetros cambiados tras sellar, tampoco;
  · (i)-(iii) en las 11 DENTRO del sello: P-RE con DAB girado 15° fuera del plano → «slide-specific
    DAB vector», DAB propio ≤ 3°, sus núcleos DAB+ a ± 0,02 de la verdad con él; (iv) P-RA con H
    girado 15° → «counterstain differs» anotado en el anexo sellado y en `fuera_del_mapa`;
  · rama > 0,25: pendiente sin sello; revisión solo con pendiente, una vez, no gastada por un
    fallo; «high floor» y banda obligatoria; con GrandQC en uso, un T clásico > 0,25 también abre
    la rama; recongelación una vez, también cuando pasa por el pendiente (el anterior vuelve a
    regir y el sello final guarda sha256 anterior y causa);
  · regla de artefacto v2 (actualización 19): por célula, el DAB recortado no es artefacto y se
    anota (`frac_recorte_*`), la tinta verde viva sí; el DAB de tanda y el residuo siguen sin
    píxeles recortados; un sello v1 no mide con el código v2 y se recongela UNA vez con
    `--causa regla_artefacto` reutilizando la pre-congelación (diferencias declaradas), con sha256
    anterior, causa y la frase de Métodos literal; sin arreglo, esa causa no recongela;
  · GrandQC: la galería sale aunque quede fuera (> 20 %);
  · ninguna frase de «Nunca decir» en el código del módulo A.
Corre con el python del venv `patologia`; sin venv, SKIP (77).
"""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import _laminillas_piloto_a as F  # noqa: E402

F.al_venv(__file__)
TOOLS = os.path.join(os.path.dirname(AQUI), "tools")
sys.path.insert(0, TOOLS)

import numpy as np  # noqa: E402

import laminillas_color as C  # noqa: E402
import laminillas_congela as K  # noqa: E402
import laminillas_segmenta as S  # noqa: E402
import laminillas_sello as SL  # noqa: E402

FECHA = "2026-10-01"
_TEMPORALES = []


def _tmp(prefijo):
    d = tempfile.mkdtemp(prefix=prefijo)
    _TEMPORALES.append(d)
    return d


def _trazas(d):
    """Trazas de guarda_memoria sintéticas: picos 6,1 y 3,0 GB."""
    return {"laminillas": F.traza_memoria(d, "laminillas", 6.1),
            "laminillas_qc": F.traza_memoria(d, "laminillas_qc", 3.0)}


PX = dict(n_teselas=20)
try:
    import laminillas_metricas as MET
    MODULO_B = None                                  # el de verdad (módulo B presente)
except Exception:                                    # noqa: BLE001
    MET = None
    MODULO_B = {"registro": {"stub": True}, "metricas": {"stub": True}}

D_RE = F.rota(F.D_REAL, np.cross(F.D_REAL, np.cross(F.H_REAL, F.D_REAL)), 15.0)
H_RA = F.rota(F.H_REAL, np.cross(F.H_REAL, F.D_REAL), 15.0)


class Segmentos:
    """El sustituto de InstanSeg, con registro de a qué láminas se le pidió segmentar."""

    def __init__(self, grandqc=None):
        self.pedidas = []
        self.grandqc = grandqc

    def __call__(self, lector, lam, nombre, **kw):
        self.pedidas.append(nombre)
        if self.grandqc is not None:
            kw["grandqc"] = self.grandqc(lam)
        return F.segmentador_stub(lector, lam, nombre, **kw)


def _congelado(extra=None, her2neg_alto=None, grandqc=None):
    d = _tmp("lam-congela-")
    lector = F.LectorSint(F.tanda(extra=extra, her2neg_alto=her2neg_alto))
    seg = Segmentos(grandqc)
    pre = K.precongela(lector, d, segmentador=seg)
    return d, lector, seg, pre


def _alto(rng, n):                                   # 3 % de núcleos con DAB 0,3-0,5
    return np.where(rng.random(n) < 0.03, rng.uniform(0.3, 0.5, n), 0.0)


class Precongelacion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, cls.lector, cls.seg, cls.pre = _congelado()

    def test_pasa_y_solo_abre_suelo(self):
        self.assertTrue(self.pre["pasa"], self.pre["motivos_no_pasa"])
        self.assertEqual(set(self.lector.abiertas), set(K.SUELO))
        self.assertEqual(sorted(self.seg.pedidas), sorted(K.SUELO))

    def test_comprobaciones(self):
        for n in K.SUELO:
            li = self.pre["laminas"][n]
            self.assertTrue(li["lector_a"]["ok"])
            self.assertTrue(li["lector_bc"]["corrida"])
            self.assertTrue(li["i0"]["pasa"])
            self.assertTrue(li["segmentacion"]["teselado"]["pasa"])
            self.assertIn("réplica de la rejilla", li["segmentacion"]["teselado"]["criterio"])
            self.assertIn("desvio_area", li["segmentacion"]["teselado"])
            self.assertEqual(li["segmentacion"]["pesos"]["sha256"], S.INSTANSEG["sha256"])
            ac = li["acuerdo_area"]                              # la puerta del área
            self.assertTrue(ac["pasa"], ac)
            self.assertEqual(ac["criterio"], S.CRITERIO_AREA)
            self.assertGreaterEqual(ac["n_pares"], S.COMPROBACION["acuerdo_area_min_pares"])
            self.assertAlmostEqual(ac["mediana"], 1.0, delta=0.15)   # verdad contra B
            self.assertTrue(li["area_nuclear"]["en_referencia_plan"])  # 25-80: referencia
            self.assertTrue(li["rejilla_desplazada"]["pasa"])
            # fragmentos de 500 µm a 50 µm: 2, con su área (± 8 %: el borde de una rejilla de
            # núcleos a 18 µm no es una línea)
            self.assertEqual(len(li["mascara_provisional"]["fragmentos_mm2"]), 2)
            for a in li["mascara_provisional"]["fragmentos_mm2"]:
                self.assertAlmostEqual(a, 0.25, delta=0.25 * 0.08)
        g = self.pre["grandqc"]
        self.assertFalse(g["en_uso"])
        self.assertEqual(g["rotulos"],
                         ["artifact exclusion by fixed classical rules; GrandQC not used on IHC"])
        self.assertEqual(self.pre["parametros"], K.parametros())

    def test_sin_bc_no_pasa_ni_congela(self):
        d = _tmp("lam-sinbc-")
        lam = {n: self.lector.laminas[n] for n in K.TANDA}
        man = {"laminas": {n: {"i0": {"franja": False}} for n in lam}}
        lector = F.LectorSint(lam, manifiesto=man)
        pre = K.precongela(lector, d, segmentador=F.segmentador_stub)
        self.assertFalse(pre["pasa"])
        self.assertIn("lector (b)(c) sin correr", " ".join(pre["motivos_no_pasa"]["P-HER2NEG"]))
        with self.assertRaises(RuntimeError):
            K.congela(lector, d, FECHA, _trazas(d), px_kw=PX, modulo_b=MODULO_B)
        self.assertFalse(os.path.exists(os.path.join(d, K.FICHERO_SELLO)))

    def test_pesos_sin_verificar_no_pasa(self):
        d = _tmp("lam-pesos-")

        def sin_pesos(lector, lam, nombre, **kw):
            out = F.segmentador_stub(lector, lam, nombre, **kw)
            out["pesos"] = None
            return out
        pre = K.precongela(self.lector, d, segmentador=sin_pesos)
        self.assertFalse(pre["pasa"])
        self.assertIn("pesos de InstanSeg sin verificar contra el sha256 sellado",
                      pre["motivos_no_pasa"]["P-HER2"])

    def test_area_nuclear_fuera_de_rango_no_pasa(self):
        d = _tmp("lam-area-")
        from shapely.geometry import Point

        def diminuto(lector, lam, nombre, **kw):
            out = F.segmentador_stub(lector, lam, nombre, **kw)
            out["celulas"] = [Point(p.centroid.x, p.centroid.y).buffer(1.5, 8)
                              for p in out["celulas"]]
            out["celulas_desplazadas"] = out["celulas"]
            return out
        pre = K.precongela(self.lector, d, segmentador=diminuto)
        self.assertFalse(pre["pasa"])
        self.assertIn("área nuclear: InstanSeg/detector B en µm² (mismos núcleos) fuera de "
                      "0.70-1.30", " ".join(pre["motivos_no_pasa"]["P-HER2"]))

    def test_error_de_unidades_no_pasa(self):
        """Los dos errores de unidades posibles, cada uno por su lado: (1) polígonos en px de
        0,5 µm/px en vez de L0 (aquí, a mitad de escala: L0 de 0,25) → no casan con los núcleos de
        B, 0 pares; (2) µm² mal convertidos (×¼) → los pares dan 0,25."""
        from unittest import mock
        from shapely import affinity

        def media_escala(lector, lam, nombre, **kw):
            out = F.segmentador_stub(lector, lam, nombre, **kw)
            out["celulas"] = [affinity.scale(p, 0.5, 0.5, origin=(0, 0))
                              for p in out["celulas"]]
            out["celulas_desplazadas"] = out["celulas"]
            return out
        pre = K.precongela(self.lector, _tmp("lam-unid1-"), segmentador=media_escala)
        self.assertFalse(pre["laminas"]["P-HER2"]["acuerdo_area"]["pasa"])
        self.assertLess(pre["laminas"]["P-HER2"]["acuerdo_area"]["n_pares"], 200)
        original = S.areas_um2
        with mock.patch.object(S, "areas_um2", lambda p, m: original(p, m) / 4.0):
            pre = K.precongela(self.lector, _tmp("lam-unid2-"), segmentador=F.segmentador_stub)
        ac = pre["laminas"]["P-HER2"]["acuerdo_area"]
        self.assertFalse(ac["pasa"])
        self.assertAlmostEqual(ac["mediana"], 0.25, delta=0.05)


class Congelacion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, cls.lector, cls.seg, cls.pre = _congelado(extra={
            # P-RE: epitelio de DAB puro (epitelio_h=0) para aislar (iii) del sesgo por
            # contratinción, que ya se prueba en la tanda (CK19 con H 0,05)
            "P-RE": dict(dab_nucleo=lambda rng, n: np.where(rng.random(n) < 0.6, 0.5, 0.0),
                         epitelio_dab=0.4, epitelio_h=0.0, d=D_RE),
            "P-RP": dict(dab_nucleo=lambda rng, n: np.where(rng.random(n) < 0.3, 0.4, 0.0),
                         epitelio_dab=0.3),
            "P-RA": dict(h=H_RA),
        })
        cls.ruta = os.path.join(cls.d, K.FICHERO_SELLO)
        ki67 = cls.lector.laminas["P-KI67"]
        pol_ki67 = F.poligonos(ki67.verdad["nucleos"])
        # ANTES del sello: la guardia (en el mismo escenario)
        cls.antes = {}
        for que, f in (("celulas", lambda: K.celulas_diana(cls.lector, "P-KI67", cls.ruta,
                                                            segmentador=cls.seg)),
                       ("ck19", lambda: K.mascara_ck19(cls.lector, cls.ruta, [])),
                       ("segmenta", lambda: S.celulas(cls.lector, "P-CK19", cls.ruta,
                                                      segmentador=cls.seg)),
                       ("pasada", lambda: K.pasada_objetos(cls.lector, ki67, pol_ki67,
                                                          cls.lector.i0_local(ki67))),
                       ("pasada_con_ruta", lambda: K.pasada_objetos(
                           cls.lector, ki67, pol_ki67, cls.lector.i0_local(ki67),
                           ruta_sello=cls.ruta)),
                       ("lazyslide", lambda: S.segmentador_lazyslide(cls.lector, ki67,
                                                                     "P-KI67")),
                       ("nombre_cambiado", lambda: K.pasada_objetos(
                           cls.lector, ki67, pol_ki67, cls.lector.i0_local(ki67),
                           nombre="P-HER2NEG"))):
            try:
                f()
                cls.antes[que] = "leyó"
            except SL.SelloAusente:
                cls.antes[que] = "SelloAusente"
            except ValueError:
                cls.antes[que] = "ValueError"
        cls.pedidas_antes = list(cls.seg.pedidas)
        cls.sello = K.congela(cls.lector, cls.d, FECHA, _trazas(cls.d), px_kw=PX,
                              modulo_b=MODULO_B)
        cls.pedidas_tras_congelar = list(cls.seg.pedidas)

    def test_guardia_antes_del_sello(self):
        self.assertEqual(self.antes, {"celulas": "SelloAusente", "ck19": "SelloAusente",
                                      "segmenta": "SelloAusente", "pasada": "SelloAusente",
                                      "pasada_con_ruta": "SelloAusente",
                                      "lazyslide": "SelloAusente",
                                      "nombre_cambiado": "ValueError"})
        # ninguna lámina diana se segmentó nunca: ni en la pre-congelación ni al congelar
        self.assertEqual(sorted(self.pedidas_tras_congelar), sorted(K.SUELO))

    def test_sello_verifica(self):
        s = SL.carga(self.ruta)
        self.assertEqual(s.sha256, self.sello.sha256)
        self.assertEqual(s.fecha, FECHA)
        self.assertEqual(s.d["tipo"], "congelacion-tanda")
        self.assertIsNone(s.anterior)
        self.assertEqual(s.d["precongelacion"]["sha256"], self.pre["sha256"])
        self.assertEqual(s.d["parametros"], K.parametros())

    def test_vectores_recuperados(self):
        v = self.sello.contenido["vectores"]
        self.assertLessEqual(F.angulo(v["H"], F.H_REAL), 1.0)
        self.assertLessEqual(F.angulo(v["DAB"], F.D_REAL), 3.0)
        self.assertAlmostEqual(F.angulo(np.cross(v["H"], v["DAB"]), v["tercero"]), 0.0,
                               places=6)
        sesgo = v["estimador_dab"]["sesgo_si_contratincion_citoplasmica_grados"]
        self.assertLess(sesgo["0.00"], 0.5)                       # sin contratinción, sin sesgo
        self.assertGreater(sesgo["0.10"], sesgo["0.05"])          # crece con la contratinción

    def test_T_del_suelo_y_recalculo(self):
        c = self.sello.contenido
        u = c["umbral"]
        self.assertEqual(u["rige"], "clasicas")
        self.assertEqual(u["alto"], [])
        M = C.matriz(c["vectores"]["H"], c["vectores"]["DAB"])
        neg = np.load(os.path.join(self.d, "objetos_P-HER2NEG.npz"))
        obj = {k: neg[k] for k in neg.files}
        pol = S.desempaqueta(obj["pol_xy"], obj["pol_offs"])
        lam = self.lector.laminas["P-HER2NEG"]
        obj["saturado"] = K.pasada_objetos(self.lector, lam, pol, self.lector.i0_local(lam),
                                           M=M, muestra_h=False)["saturado"]
        ex = K._exclusion(obj, "clasicas")
        for comp in ("nucleo", "anillo"):
            r = u["regimenes"]["clasicas"][comp]
            v = C.dab_de(obj["od_" + comp], M)[~ex]
            v = v[np.isfinite(v)]
            self.assertEqual(r["n_objetos"], len(v))
            self.assertAlmostEqual(r["p999"], float(np.percentile(v, 99.9)), places=12)
            self.assertLess(r["p999"], 0.02)                     # suelo sintético sin DAB
            self.assertEqual(u["T"][comp], 0.10)
            self.assertEqual(r["rotulos"], ["threshold at fixed floor (0.10 OD); measured floor "
                                            "p99.9 = %.3f" % r["p999"]])
            self.assertGreater(r["n_objetos"], 300)
            self.assertEqual(r["n_fragmentos"], 2)
            fp = c["fp_her2"][comp]
            self.assertEqual(fp["k"], 0)
            self.assertAlmostEqual(fp["ic95"][1], 1 - 0.025 ** (1 / fp["n"]), places=9)
            self.assertEqual(fp["ic95_rige"], "binomial (Clopper-Pearson)")
            self.assertEqual(fp["ic95_bootstrap"], [0.0, 0.0])    # k = 0: el bootstrap no sirve
            self.assertGreater(fp["n_bloques"], 10)
            self.assertIn("T_bajo", fp["banda"])
        self.assertIn("coincide con la propia medida de HER2",
                      c["fp_her2"]["anillo"]["declaracion"])
        self.assertFalse(u["banda_obligatoria"])
        self.assertEqual(len(c["galeria_her2neg"]["nucleo"]), 50)
        # el módulo B (fp_her2 por régimen) lo encuentra con la clave que lee
        self.assertIn("clasicas", c["fp_her2_regimenes"])

    def test_secciones_selladas(self):
        c = self.sello.contenido
        self.assertEqual(c["memoria"]["umbral_gb"], {"laminillas": 8.1, "laminillas_qc": 5.0})
        tr = c["memoria"]["trazas"]["laminillas"]
        self.assertEqual(tr["sha256"], K.sha256_fichero(os.path.join(self.d,
                                                                     "traza-laminillas.tsv")))
        self.assertEqual(tr["muestras"], 20)
        self.assertEqual(c["regla_L"]["candidatos_um"], [100, 150, 200, 300, 400])
        self.assertEqual(c["hotspot"]["diametro_mm"], 0.5)
        self.assertEqual(c["registro"]["ransac"]["residual_threshold_px"], 12)
        self.assertEqual(c["medida"]["hscore"]["cortes"], ["T", 0.4, 0.6])
        self.assertEqual(c["semillas"]["maestra"], K.SEMILLA)
        for clave in ("ODSUM_MIN_COLOR", "ODSUM_MIN_VECTOR", "FRANJA_UMBRAL", "CK19", "TASA"):
            self.assertIn(clave, c["parametros"]["color"])
        self.assertIn("CONTRATINCION", c["parametros"]["congela"])
        self.assertIn("MASCARA_PROV", c["parametros"]["segmenta"])
        for frase in ("fixed after a pixel-level stain reconnaissance, before any cell-level "
                      "measurement", "fixed, uncalibrated cut-offs",
                      "no independent ground truth for the epithelial mask; editable layer "
                      "provided", "not the IKWG method",
                      "artifact exclusion by fixed classical rules; GrandQC not used on IHC"):
            self.assertIn(frase, c["metodos"])
        self.assertTrue(any(m.endswith("floor slide") for m in c["metodos"]))
        r = c["residuo"]
        self.assertEqual(sorted(r["por_lamina"]), sorted(K.IHQ_CON_TEJIDO))
        self.assertAlmostEqual(r["umbral"], 2 * max(r["por_lamina"][n]["residuo"]
                                                    for n in K.TANDA))
        self.assertFalse(any(r["por_lamina"][n]["supera"] for n in K.TANDA))

    def test_el_modulo_b_lo_lee(self):
        s = SL.carga(self.ruta)
        u = s.umbral("P-KI67")
        self.assertEqual((u["T"], u["compartimento"]), (0.10, "nucleo"))
        self.assertIn("threshold at fixed floor (0.10 OD)", u["rotulos"][0])
        self.assertEqual(s.umbral("P-SYN")["compartimento"], "anillo")
        self.assertIn("ic95", s.fp_her2("nucleo"))
        self.assertEqual(s.vectores("P-KI67")["DAB"], s.d["vectores"]["DAB"])
        # (iii) dentro del sello: P-RE se mide con su DAB propio y su T, con el rótulo
        re_ = s.umbral("P-RE")
        self.assertTrue(re_["vector_propio"])
        self.assertIn("slide-specific DAB vector", re_["rotulos"])
        self.assertEqual(s.vectores("P-RE")["DAB"], s.d["residuo"]["por_lamina"]["P-RE"]
                         ["dab_propio"])
        if MET is not None:
            p = MET.parametros_metricas(s)                       # coteja hotspot y regla_L
            self.assertEqual(p["hotspot_diametro_um"], 500.0)
            import laminillas_registro as R
            pr, _, _, pre = R._params(s, "P-CK19", "P-KI67")   # coteja `registro`
            self.assertFalse(pre)
            self.assertEqual(pr["residual_px"], 12)

    def test_tras_el_sello_lee_y_deja_constancia(self):
        out = K.celulas_diana(self.lector, "P-KI67", self.ruta, segmentador=self.seg)
        self.assertGreater(len(out["celulas"]), 1000)
        with open(os.path.join(self.d, K.LECTURAS_DIANA)) as f:
            filas = [json.loads(x) for x in f]
        self.assertTrue(any(r["lamina"] == "P-KI67" and r["sello"] == self.sello.sha256
                            for r in filas))

    def test_porcentaje_recuperado(self):
        """El % por núcleo y por anillo sale de las imágenes con la verdad sintética (± 3 puntos):
        núcleo y anillo no están cruzados y el T sellado separa."""
        s = SL.carga(self.ruta)
        for nombre, comp, verdad in (("P-KI67", "nucleo", "c_d"), ("P-SYN", "anillo",
                                                                   "c_anillo")):
            lam = self.lector.laminas[nombre]
            M, _ = K.matriz_de_lamina(s, nombre)
            o = K.pasada_objetos(self.lector, lam, F.poligonos(lam.verdad["nucleos"]),
                                 self.lector.i0_local(lam), muestra_h=False,
                                 ruta_sello=self.ruta)
            T = s.umbral(nombre, comp)["T"]
            medido = 100 * float(np.mean(C.dab_de(o["od_" + comp], M) > T))
            real = 100 * float(np.mean(lam.verdad[verdad] > 0))
            self.assertAlmostEqual(medido, real, delta=3.0, msg="%s %s" % (nombre, comp))
            otro = "anillo" if comp == "nucleo" else "nucleo"
            cruzado = 100 * float(np.mean(C.dab_de(o["od_" + otro], M) > T))
            self.assertLess(cruzado, 5.0)                       # el otro compartimento, limpio

    def test_sello_tocado_se_niega(self):
        d = _tmp("lam-tocado-")
        ruta = os.path.join(d, K.FICHERO_SELLO)
        with open(self.ruta) as f:
            s = json.load(f)
        s["umbral"]["T"]["nucleo"] = 0.05
        with open(ruta, "w") as f:
            json.dump(s, f)
        with self.assertRaises(SL.SelloInvalido):
            S.celulas(self.lector, "P-KI67", ruta, segmentador=self.seg)
        with self.assertRaises(SL.SelloInvalido):
            K.mascara_ck19(self.lector, ruta, [])

    def test_sello_incompleto_se_niega(self):
        d = _tmp("lam-incompleto-")
        ruta = os.path.join(d, K.FICHERO_SELLO)
        SL.sella(ruta, {"vectores": {"H": [1, 0, 0], "DAB": [0, 1, 0]}}, FECHA)
        with self.assertRaises(SL.SelloInvalido):
            S.celulas(self.lector, "P-KI67", ruta, segmentador=self.seg)
        lam = self.lector.laminas["P-KI67"]
        with self.assertRaises(SL.SelloInvalido):
            K.pasada_objetos(self.lector, lam, F.poligonos(lam.verdad["nucleos"][:5]),
                             self.lector.i0_local(lam), ruta_sello=ruta)

    def test_parametros_cambiados_tras_el_sello_no_miden(self):
        lam = self.lector.laminas["P-KI67"]
        pol = F.poligonos(lam.verdad["nucleos"])
        for mod, nombre, nuevo in ((C, "ANGULO_CONTRATINCION", 20.0),
                                   (C, "ODSUM_MIN_COLOR", 0.2),
                                   (S, "ARTEFACTO", dict(S.ARTEFACTO, borde_um=10.0)),
                                   (K, "CONTRATINCION", dict(K.CONTRATINCION, decil=20.0))):
            viejo = getattr(mod, nombre)
            setattr(mod, nombre, nuevo)
            try:
                with self.assertRaises(SL.SelloInvalido, msg=nombre):
                    K.contratincion_lamina(self.lector, "P-KI67", self.ruta, pol)
                with self.assertRaises(SL.SelloInvalido, msg=nombre):
                    K.mascara_ck19(self.lector, self.ruta, [])
            finally:
                setattr(mod, nombre, viejo)

    def test_mascara_ck19(self):
        lam = self.lector.laminas["P-CK19"]
        nuc = lam.verdad["nucleos"]
        r = K.mascara_ck19(self.lector, self.ruta, F.poligonos(nuc))
        self.assertEqual(r["umbral"]["regla"], "valle")
        self.assertNotIn("CK19 threshold not bimodal", r["rotulos"])
        self.assertIn("no independent ground truth for the epithelial mask; editable layer "
                      "provided", r["rotulos"])
        self.assertFalse(r["sensibilidad"]["dependiente"])
        epi = lam.verdad["nucleo_en_epitelio"]
        from scipy.ndimage import distance_transform_edt
        dist = distance_transform_edt(~lam.verdad["epitelio"]) * F.MPP
        lejos = dist[nuc[:, 1].astype(int), nuc[:, 0].astype(int)] > 8
        dentro = r["dentro"]
        self.assertGreaterEqual(dentro[epi].mean(), 0.95)
        self.assertLessEqual(dentro[lejos & ~epi].mean(), 0.05)

    def test_residuo_de_las_11_en_el_sello(self):
        s = SL.carga(self.ruta)
        r = K.residuo_de(s, "P-RE")
        self.assertTrue(r["supera"])
        self.assertEqual(r["rotulos"], ["slide-specific DAB vector"])
        self.assertLessEqual(F.angulo(r["dab_propio"], D_RE), 3.0)
        self.assertGreater(r["angulo_plano"], 10.0)
        self.assertEqual(r["T_propio"]["nucleo"]["T"], 0.10)
        self.assertIn("clasicas", r["T_propio_por_regimen"])
        rp = K.residuo_de(s, "P-RP")
        self.assertFalse(rp["supera"])
        self.assertLess(rp["angulo_plano"], 3.0)
        with self.assertRaises(SL.SelloInvalido):               # fuera de las 11: no se mide
            K.matriz_de_lamina(s, "P-AE1AE3")
        # los núcleos DAB+ de P-RE (verdad 0,50) se miden bien con SU vector, peor con el de tanda
        lam = self.lector.laminas["P-RE"]
        o = K.pasada_objetos(self.lector, lam, F.poligonos(lam.verdad["nucleos"]),
                             self.lector.i0_local(lam), muestra_h=False, ruta_sello=self.ruta)
        pos = lam.verdad["c_d"] > 0
        Mp, rot = K.matriz_de_lamina(s, "P-RE")
        self.assertEqual(rot, ["slide-specific DAB vector"])
        propio = float(np.nanmean(C.dab_de(o["od_nucleo"][pos], Mp)))
        tanda = float(np.nanmean(C.dab_de(o["od_nucleo"][pos], C.matriz(s.d["vectores"]["H"],
                                                                         s.d["vectores"]["DAB"]))))
        self.assertAlmostEqual(propio, 0.50, delta=0.02)
        self.assertLess(abs(propio - 0.50), abs(tanda - 0.50))

    def test_contratincion_al_anexo_y_fuera_del_mapa(self):
        lam = self.lector.laminas["P-RA"]
        r = K.contratincion_lamina(self.lector, "P-RA", self.ruta,
                                   F.poligonos(lam.verdad["nucleos"]))
        self.assertAlmostEqual(r["angulo"], 15.0, delta=1.0)
        self.assertTrue(r["fuera_mapa"])
        self.assertEqual(r["rotulos"], ["counterstain differs"])
        self.assertIn("decil de menor DAB", r["seleccion"])     # todos sobre T: el respaldo
        lam2 = self.lector.laminas["P-KI67"]
        r2 = K.contratincion_lamina(self.lector, "P-KI67", self.ruta,
                                    F.poligonos(lam2.verdad["nucleos"]))
        self.assertFalse(r2["fuera_mapa"])
        self.assertEqual(r2["seleccion"], "DAB medio < T")
        ext = K._lee_verificado(os.path.join(self.d, K.EXTENSION), "extension-sello")
        self.assertEqual(ext["sello_sha256"], self.sello.sha256)
        self.assertTrue(ext["contratincion"]["P-RA"]["fuera_mapa"])
        fm = K.fuera_del_mapa(self.d)
        self.assertEqual(fm["fuera"], {"P-RA": ["counterstain differs"]})
        self.assertNotIn("P-KI67", fm["sin_evaluar"])
        self.assertNotIn("P-HER2NEG", fm["sin_evaluar"])         # suelo: del sello
        self.assertIn("P-CHGA", fm["sin_evaluar"])
        otra = K.contratincion_lamina(self.lector, "P-RA", self.ruta, [])   # ya anotada: fija
        self.assertTrue(otra["ya_anotado"])
        self.assertEqual(otra["angulo"], r["angulo"])


class Precongela_con_sello(unittest.TestCase):
    def test_no_pisa_la_sellada(self):
        d, lector, seg, pre = _congelado()
        s = K.congela(lector, d, FECHA, _trazas(d), px_kw=PX, modulo_b=MODULO_B)
        npz = os.path.join(d, "objetos_P-HER2NEG.npz")
        antes = K.sha256_fichero(npz)
        with self.assertRaises(RuntimeError):
            K.precongela(lector, d, segmentador=seg)
        self.assertEqual(K.sha256_fichero(npz), antes)
        # para recongelar sí, y la sellada queda archivada y verificable
        K.precongela(lector, d, segmentador=seg, para_recongelar=True)
        arch = os.path.join(d, "anterior-%s" % s.sha256[:12])
        self.assertEqual(K.sha256_fichero(os.path.join(arch, "objetos_P-HER2NEG.npz")), antes)
        self.assertEqual(K._lee_verificado(os.path.join(arch, K.PRECONGELACION),
                                           "precongelacion")["sha256"], pre["sha256"])


class Recongelacion(unittest.TestCase):
    def test_una_vez_por_error_de_ejecucion(self):
        d, lector, _, _ = _congelado()
        tr = _trazas(d)
        s1 = K.congela(lector, d, FECHA, tr, px_kw=PX, modulo_b=MODULO_B)
        with self.assertRaises(RuntimeError):                       # ya hay sello
            K.congela(lector, d, FECHA, tr, px_kw=PX, modulo_b=MODULO_B)
        with self.assertRaises(ValueError):                         # otra causa: incertidumbres
            K.recongela(lector, d, "2026-10-02", "resultado-feo", tr, px_kw=PX,
                        modulo_b=MODULO_B)
        self.assertEqual(SL.carga(os.path.join(d, K.FICHERO_SELLO)).sha256, s1.sha256)
        s2 = K.recongela(lector, d, "2026-10-02", "segmentador", tr, px_kw=PX,
                         modulo_b=MODULO_B)
        self.assertEqual(s2.anterior, {"sha256": s1.sha256, "causa": "segmentador"})
        self.assertTrue(s2.d["recongelado"])
        self.assertEqual(s2.fecha, "2026-10-02")
        self.assertIn("re-frozen once after a segmentation/reader fix; no target-cell "
                      "measurement informed it", s2.d["metodos"])
        self.assertTrue(os.path.exists(os.path.join(
            d, "congelacion.anterior-%s.json" % s1.sha256[:12])))
        with self.assertRaises(SL.SelloInvalido):
            K.recongela(lector, d, "2026-10-03", "lector", tr, px_kw=PX, modulo_b=MODULO_B)
        self.assertEqual(SL.carga(os.path.join(d, K.FICHERO_SELLO)).sha256, s2.sha256)

    def test_recongela_que_cae_en_la_rama_alta(self):
        """recongela → pendiente: el anterior SIGUE rigiendo; la revisión va por recongela y el
        sello final guarda sha256 anterior y causa; una tercera, no."""
        d, lector, seg, _ = _congelado()
        tr = _trazas(d)
        s1 = K.congela(lector, d, FECHA, tr, px_kw=PX, modulo_b=MODULO_B)
        # «arreglo del segmentador»: la pre-congelación nueva sale con el suelo alto
        lector2 = F.LectorSint(F.tanda(her2neg_alto=_alto))
        K.precongela(lector2, d, segmentador=Segmentos(), para_recongelar=True)
        r = K.recongela(lector2, d, "2026-10-02", "segmentador", tr, px_kw=PX,
                        modulo_b=MODULO_B)
        self.assertEqual(r["estado"], "revision_alto_pendiente")
        self.assertEqual(SL.carga(os.path.join(d, K.FICHERO_SELLO)).sha256, s1.sha256)
        self.assertEqual(K._huerfanos(d), [])
        with self.assertRaises(RuntimeError):                       # sin anterior, no
            K.congela(lector2, d, FECHA, tr, px_kw=PX, modulo_b=MODULO_B)
        rev = {"regla": "borde", "parametros": {"um": 100.0}, "vectores_e_i0_revisados": True}
        with self.assertRaises(ValueError):                         # el pendiente es de recongela
            os.replace(os.path.join(d, K.FICHERO_SELLO), os.path.join(d, "aparte.json"))
            try:
                K.congela(lector2, d, FECHA, tr, px_kw=PX, modulo_b=MODULO_B,
                          revision_alto=rev)
            finally:
                os.replace(os.path.join(d, "aparte.json"), os.path.join(d, K.FICHERO_SELLO))
        s2 = K.recongela(lector2, d, "2026-10-02", "segmentador", tr, px_kw=PX,
                         modulo_b=MODULO_B, revision_alto=rev)
        self.assertEqual(s2.anterior, {"sha256": s1.sha256, "causa": "segmentador"})
        self.assertIn("re-frozen once after a segmentation/reader fix; no target-cell "
                      "measurement informed it", s2.d["metodos"])
        self.assertEqual(s2.d["umbral"]["revision_alto"]["regla"], "borde")
        hecha = K._lee_verificado(os.path.join(d, K.REVISION_ALTO), "revision-alto")
        self.assertEqual(hecha["sello_sha256"], s2.sha256)
        with self.assertRaises(SL.SelloInvalido):
            K.recongela(lector2, d, "2026-10-03", "lector", tr, px_kw=PX, modulo_b=MODULO_B,
                        revision_alto=rev)

    def test_sin_fecha_o_sin_traza_no_sella(self):
        d, lector, _, _ = _congelado()
        with self.assertRaises(ValueError):
            K.congela(lector, d, None, _trazas(d), px_kw=PX, modulo_b=MODULO_B)
        with self.assertRaises(ValueError):
            K.congela(lector, d, FECHA, {}, px_kw=PX, modulo_b=MODULO_B)
        with self.assertRaises(ValueError):                         # número tecleado, no traza
            K.congela(lector, d, FECHA, {"laminillas": 6.1}, px_kw=PX, modulo_b=MODULO_B)
        mala = os.path.join(d, "mala.tsv")
        with open(mala, "w") as f:
            f.write("6.1\n")
        with self.assertRaises(ValueError):                         # no es una traza
            K.congela(lector, d, FECHA, {"laminillas": mala}, px_kw=PX, modulo_b=MODULO_B)
        self.assertFalse(os.path.exists(os.path.join(d, K.FICHERO_SELLO)))


class _CodigoV1:
    """El código de ANTES del arreglo de la regla de artefacto (actualización 19), para fabricar un
    sello «viejo»: sin `REGLA_SATURADO` en los parámetros, sin la causa regla_artefacto y con
    saturados = vivo | recortado (= v2 | recortado)."""

    def __enter__(self):
        self.par, self.causas, self.sat = C.parametros, K.CAUSAS_RECONGELA, C.saturados_no_hdab
        par, sat = self.par, self.sat

        def parametros_v1():
            p = par()
            p.pop("REGLA_SATURADO")
            return p
        C.parametros = parametros_v1
        K.CAUSAS_RECONGELA = ("lector", "I0", "segmentador")
        C.saturados_no_hdab = lambda rgb, M, i0=255.0: sat(rgb, M, i0) | C.saturado_intensidad(rgb)
        return self

    def __exit__(self, *exc):
        C.parametros, K.CAUSAS_RECONGELA, C.saturados_no_hdab = self.par, self.causas, self.sat
        return False


class SaturadoPorCelula(unittest.TestCase):
    """Regla v2 por célula (`pasada_objetos`): el DAB oscuro recortado no es artefacto y queda
    anotado como recortado; un píxel de tinta verde viva en el anillo sí es artefacto; un píxel
    recortado con color fuera del plano no cuenta. Con la regla v1 (`vivo | recortado`) o con
    `vivo` sin quitar los recortados, esto falla."""

    def test_dab_recortado_y_tinta(self):
        from shapely.geometry import box
        forma, r = (100, 280), 8.0
        nuc = np.array([[30, 40, r, 0], [80, 40, r, 0], [130, 40, r, 0], [180, 40, r, 0],
                        [230, 40, r, 0]], float)
        cH = F.discos(nuc, forma, [0.5, 0.6, 0.6, 0.6, 0.6])
        cD = F.discos(nuc[:1], forma, [3.0])                       # 0: DAB nuclear 3,0 OD
        cD = F.discos(nuc[3:4], forma, [3.0], campo=cD, anillo_px=C.ANILLO_UM / F.MPP)  # 3: anillo
        img = F.pinta(cH, cD, ruido=0)
        img[40, 140] = (40, 160, 60)          # 2: un píxel de rotulador verde en el anillo
        img[40, 240] = (5, 33, 1)             # 4: recortado con OD fuera del plano, en el anillo
        self.assertTrue(C.saturado_intensidad(img[40:41, 30:31])[0, 0])    # el DAB 3,0 recorta
        lam = F.LaminaSint("P-HER2NEG", img, F.MPP, box(0, 0, forma[1], forma[0]))
        lector = F.LectorSint({"P-HER2NEG": lam})
        M = C.matriz(F.H_REAL, F.D_REAL)
        o = K.pasada_objetos(lector, lam, F.poligonos(nuc), lector.i0_local(lam), M=M,
                             muestra_h=False)
        self.assertEqual(o["saturado"].tolist(), [False, False, True, False, False])
        fn, fa = o["frac_recorte_nucleo"], o["frac_recorte_anillo"]
        self.assertGreater(fn[0], 0.9)                                # anotado, no excluido
        self.assertEqual(fa[0], 0.0)
        self.assertEqual(fn[1:].tolist(), [0.0] * 4)
        self.assertEqual(fa[1:3].tolist(), [0.0, 0.0])
        self.assertGreater(fa[3], 0.8)
        self.assertAlmostEqual(fa[4], 1.0 / o["px_anillo"][4], places=12)
        # sin M: el recorte se anota igual (la pre-congelación no necesita vectores)
        o0 = K.pasada_objetos(lector, lam, F.poligonos(nuc), lector.i0_local(lam),
                              muestra_h=False)
        np.testing.assert_array_equal(o0["frac_recorte_nucleo"], fn)
        self.assertFalse(o0["saturado"].any())


class VectoresSinRecortados(unittest.TestCase):
    def test_dab_de_tanda_y_residuo_sin_recortados(self):
        """Los vectores de tanda y el residuo siguen sin píxeles recortados: P-KI67 con núcleos de
        DAB 3,0 (recortados) en la muestra; ninguno llega al estimador del DAB de tanda ni al
        residuo, y la máscara que reciben es el RECORTE en intensidad, no la regla de artefacto."""
        lector = F.LectorSint(F.tanda(extra={"P-KI67": dict(dab_nucleo=F._fraccion(0.6, 3.0))}))
        rng = np.random.default_rng(5)
        mh = np.outer(rng.uniform(0.4, 0.7, 2000), F.H_REAL)
        objs = {n: {"muestra_h": mh} for n in K.SUELO}
        vistos, a_dab, a_residuo = {}, [], []
        muestra, vdab, resid = K.muestra_pixeles, C.vector_dab, C.residuo

        def muestra_espia(lector_, lam, *a, **kw):
            o, s = muestra(lector_, lam, *a, **kw)
            vistos[lam.opaco] = (o, s)
            return o, s

        def vdab_espia(od_px, h, *a, **kw):
            a_dab.append(np.asarray(od_px))
            return vdab(od_px, h, *a, **kw)

        def residuo_espia(od_px, M, sin_saturar=None):
            a_residuo.append(sin_saturar)
            return resid(od_px, M, sin_saturar)
        K.muestra_pixeles, C.vector_dab, C.residuo = muestra_espia, vdab_espia, residuo_espia
        try:
            K._vectores_y_residuo(lector, objs, 11, list(K.TANDA), dict(n_teselas=12))
        finally:
            K.muestra_pixeles, C.vector_dab, C.residuo = muestra, vdab, resid
        recortado = {n: np.rint(np.asarray(F.I0) * 10.0 ** (-o)).min(axis=1)
                     <= C.SAT_INTENSIDAD for n, (o, s) in vistos.items()}
        for n, (o, s) in vistos.items():
            np.testing.assert_array_equal(s, recortado[n])         # máscara = recorte
        self.assertGreater(int(recortado["P-KI67"].sum()), 100)    # el caso no es vacío
        primero = a_dab[0]                                          # el DAB de TANDA
        self.assertEqual(len(primero), sum(int((~vistos[n][1]).sum()) for n in K.DIANA_TANDA))
        self.assertFalse((np.rint(np.asarray(F.I0) * 10.0 ** (-primero)).min(axis=1)
                          <= C.SAT_INTENSIDAD).any())
        self.assertEqual(len(a_residuo), len(K.TANDA))
        for s in a_residuo:
            self.assertIsNotNone(s)


class RecongelacionReglaArtefacto(unittest.TestCase):
    def test_sello_v1_recongelado_una_vez_con_la_regla_v2(self):
        """Actualización 19: sello v1 → recongela --causa regla_artefacto. Reutiliza la
        pre-congelación sellada (no re-segmenta), guarda sha256 anterior y causa, lleva la frase de
        Métodos literal y la regla v2 sellada; el sello v1 no mide con el código v2; una segunda
        recongelación se rechaza."""
        with _CodigoV1():
            d, lector, seg, pre = _congelado()
            tr = _trazas(d)
            s1 = K.congela(lector, d, FECHA, tr, px_kw=PX, modulo_b=MODULO_B)
        self.assertNotIn("REGLA_SATURADO", s1.d["parametros"]["color"])
        with self.assertRaises(SL.SelloInvalido):                   # el sello v1 ya no mide
            K._coteja_parametros(s1)
        pedidas = list(seg.pedidas)
        # una diferencia de parámetros que la pre-congelación SÍ usa: no se reutiliza, y el
        # sello v1 sigue rigiendo
        viejo = K.MEDIDA
        K.MEDIDA = dict(K.MEDIDA, tesela_l0=512)
        try:
            with self.assertRaisesRegex(RuntimeError, "MEDIDA"):
                K.recongela(lector, d, "2026-10-02", "regla_artefacto", tr, px_kw=PX,
                            modulo_b=MODULO_B)
        finally:
            K.MEDIDA = viejo
        self.assertEqual(SL.carga(os.path.join(d, K.FICHERO_SELLO)).sha256, s1.sha256)
        self.assertEqual(K._huerfanos(d), [])
        s2 = K.recongela(lector, d, "2026-10-02", "regla_artefacto", tr, px_kw=PX,
                         modulo_b=MODULO_B)
        self.assertEqual(s2.anterior, {"sha256": s1.sha256, "causa": "regla_artefacto"})
        self.assertTrue(s2.d["recongelado"])
        self.assertIn("re-frozen once after an artefact-rule implementation fix "
                      "(intensity-saturated DAB counted as non-H/DAB artefact); informed by "
                      "P-CK19 denominator aggregates only; no target-cell measurement informed "
                      "it", s2.d["metodos"])
        self.assertNotIn(K.METODOS["recongelado"], s2.d["metodos"])
        sat = s2.d["artefactos"]["saturados"]
        self.assertEqual(sat["version"], 2)
        self.assertFalse(sat["recorte"]["es_artefacto"])
        self.assertEqual(set(sat["recorte"]["suelo"]), set(K.SUELO))
        self.assertEqual(s2.d["parametros"]["color"]["REGLA_SATURADO"]["version"], 2)
        self.assertTrue(any("regla v2" in e for e in s2.d["umbral"]["excluidos"]))
        # la MISMA pre-congelación, con las diferencias declaradas; sin re-segmentar
        self.assertEqual(s2.d["precongelacion"]["sha256"], pre["sha256"])
        self.assertEqual(s2.d["precongelacion"]["parametros_distintos"],
                         ["color.REGLA_SATURADO", "congela.CAUSAS_RECONGELA"])
        self.assertEqual(seg.pedidas, pedidas)
        self.assertEqual(sorted(seg.pedidas), sorted(K.SUELO))
        self.assertFalse(os.path.exists(os.path.join(d, K.LECTURAS_DIANA)))
        # cadena válida para medir (anterior archivado, causa aceptada) y con el código v2
        self.assertEqual(SL.exige(os.path.join(d, K.FICHERO_SELLO), ["P-KI67"]).sha256,
                         s2.sha256)
        K._coteja_parametros(s2)
        self.assertEqual(SL.carga(os.path.join(
            d, "congelacion.anterior-%s.json" % s1.sha256[:12])).sha256, s1.sha256)
        # la segunda, no: ni por la misma causa ni por otra de ejecución (con «lector» la guarda
        # de «no hay arreglo» no actúa: lo que frena es la regla de una sola vez)
        for causa in ("lector", "regla_artefacto"):
            with self.assertRaises(SL.SelloInvalido, msg=causa):
                K.recongela(lector, d, "2026-10-03", causa, tr, px_kw=PX, modulo_b=MODULO_B)
            self.assertEqual(SL.carga(os.path.join(d, K.FICHERO_SELLO)).sha256, s2.sha256)
            self.assertEqual(K._huerfanos(d), ["congelacion.anterior-%s.json" % s1.sha256[:12]])

    def test_regla_artefacto_sin_arreglo_no_recongela(self):
        """Con la regla sellada igual a la del código, «regla_artefacto» no es la causa."""
        d, lector, _, _ = _congelado()
        tr = _trazas(d)
        s1 = K.congela(lector, d, FECHA, tr, px_kw=PX, modulo_b=MODULO_B)
        with self.assertRaisesRegex(ValueError, "no hay arreglo"):
            K.recongela(lector, d, "2026-10-02", "regla_artefacto", tr, px_kw=PX,
                        modulo_b=MODULO_B)
        self.assertEqual(SL.carga(os.path.join(d, K.FICHERO_SELLO)).sha256, s1.sha256)
        self.assertEqual(K._huerfanos(d), [])


class RamaAlta(unittest.TestCase):
    def test_suelo_alto_de_verdad(self):
        d, lector, _, _ = _congelado(her2neg_alto=_alto)
        tr = _trazas(d)
        r = K.congela(lector, d, FECHA, tr, px_kw=PX, modulo_b=MODULO_B)
        self.assertEqual(r["estado"], "revision_alto_pendiente")
        self.assertIn(["clasicas", "nucleo"], r["alto"])
        self.assertFalse(os.path.exists(os.path.join(d, K.FICHERO_SELLO)))
        self.assertTrue(os.path.exists(os.path.join(d, K.PENDIENTE_ALTO)))
        with self.assertRaises(ValueError):                 # sin revisar vectores e I0, no
            K.congela(lector, d, FECHA, tr, px_kw=PX, modulo_b=MODULO_B,
                      revision_alto={"regla": "borde", "parametros": {"um": 100.0}})
        # un fallo de ejecución NO gasta la revisión única
        with self.assertRaises(ValueError):                 # 400 µm: < 100 objetos para T
            K.congela(lector, d, FECHA, tr, px_kw=PX, modulo_b=MODULO_B,
                      revision_alto={"regla": "borde", "parametros": {"um": 400.0},
                                     "vectores_e_i0_revisados": True})
        self.assertFalse(os.path.exists(os.path.join(d, K.REVISION_ALTO)))
        rev = {"regla": "borde", "parametros": {"um": 100.0}, "vectores_e_i0_revisados": True}
        s = K.congela(lector, d, FECHA, tr, px_kw=PX, modulo_b=MODULO_B, revision_alto=rev)
        u = s.contenido["umbral"]
        self.assertGreater(u["T"]["nucleo"], 0.25)
        self.assertIn("high floor", u["rotulos"])
        self.assertTrue(u["banda_obligatoria"])
        self.assertTrue(u["por_regimen"]["clasicas"]["high_floor"])
        self.assertEqual(u["revision_alto"]["regla"], "borde")
        self.assertIsNotNone(u["revision_pendiente_sha256"])
        self.assertIn("high floor", SL.carga(os.path.join(d, K.FICHERO_SELLO))
                      .umbral("P-KI67")["rotulos"])
        # la galería se ve UNA vez: otra regla no entra ni por congela ni por recongela
        with self.assertRaises(RuntimeError):
            K.congela(lector, d, FECHA, tr, px_kw=PX, modulo_b=MODULO_B,
                      revision_alto={"regla": "pigmento", "vectores_e_i0_revisados": True})
        with self.assertRaises(RuntimeError):
            K.recongela(lector, d, "2026-10-02", "lector", tr, px_kw=PX, modulo_b=MODULO_B,
                        revision_alto={"regla": "pigmento", "vectores_e_i0_revisados": True})
        self.assertEqual(SL.carga(os.path.join(d, K.FICHERO_SELLO)).sha256, s.sha256)

    def test_revision_sin_rama_alta_se_rechaza(self):
        d, lector, _, _ = _congelado()
        with self.assertRaises(ValueError):
            K.congela(lector, d, FECHA, _trazas(d), px_kw=PX, modulo_b=MODULO_B,
                      revision_alto={"regla": "borde", "parametros": {"um": 100.0},
                                     "vectores_e_i0_revisados": True})
        self.assertFalse(os.path.exists(os.path.join(d, K.FICHERO_SELLO)))

    def test_grandqc_en_uso_y_T_clasico_alto(self):
        """GrandQC marca los núcleos altos del suelo y la regla clásica no: el T que rige (GrandQC)
        es 0,10, el clásico > 0,25. La rama se abre igual: si tras el sello rigen las clásicas,
        ese T no puede ir sin revisión, «high floor» ni banda."""
        from shapely.geometry import Point
        from shapely.ops import unary_union

        def marca_altos(lam):
            nuc = lam.verdad["nucleos"][lam.verdad["c_d"] > 0]
            geom = unary_union([Point(x, y).buffer(12) for x, y, _, _ in nuc]) if len(nuc) \
                else Point(5, 5).buffer(1)
            return {"carga": True, "artefactos": [(geom, "Darkspot & Foreign Object")],
                    "motivo": None}
        d, lector, _, pre = _congelado(her2neg_alto=_alto, grandqc=marca_altos)
        self.assertTrue(pre["grandqc"]["en_uso"])
        r = K.congela(lector, d, FECHA, _trazas(d), px_kw=PX, modulo_b=MODULO_B)
        self.assertEqual(r["estado"], "revision_alto_pendiente")
        self.assertIn(["clasicas", "nucleo"], r["alto"])
        self.assertNotIn(["grandqc", "nucleo"], r["alto"])
        self.assertLessEqual(r["T"]["grandqc"]["nucleo"]["T"], 0.25)
        self.assertGreater(r["T"]["clasicas"]["nucleo"]["T"], 0.25)

    def test_por_regimen(self):
        M = C.matriz(F.H_REAL, F.D_REAL)
        rng = np.random.default_rng(9)
        n = 3000
        od = np.outer(rng.uniform(0.4, 0.7, n), F.H_REAL) + rng.normal(0, 0.004, (n, 3))
        alto = rng.random(n) < 0.03
        od[alto] += 0.4 * M[1]
        obj = {"od_nucleo": od, "od_anillo": od * 0.1, "foco_bajo": np.zeros(n, bool),
               "borde_um": np.full(n, 200.0), "saturado": np.zeros(n, bool),
               "fragmento": rng.integers(0, 3, n), "pliegue": np.zeros(n, bool),
               "grandqc": alto, "centroide": rng.uniform(0, 4000, (n, 2))}
        t = K.calcula_T(obj, obj, M, True, 1, mpp_pos=0.5)
        self.assertEqual(t["rige"], "grandqc")
        self.assertEqual(t["alto"], [["clasicas", "nucleo"]])
        pr = K._por_regimen(t)
        self.assertTrue(pr["clasicas"]["high_floor"])
        self.assertTrue(pr["clasicas"]["banda_obligatoria"])
        self.assertIn("high floor", pr["clasicas"]["rotulos"])
        self.assertFalse(pr["grandqc"]["high_floor"])
        self.assertIn("grandqc", t["fp_her2_regimenes"])

    def test_regla_pigmento(self):
        M = C.matriz(F.H_REAL, F.D_REAL)
        rng = np.random.default_rng(9)
        n, k = 3000, 60
        od = np.outer(rng.uniform(0.4, 0.7, n), F.H_REAL) + rng.normal(0, 0.004, (n, 3))
        od[:k] = 0.35 * M[1] + 0.30 * M[2]                  # pigmento: fuera del plano H-DAB
        obj = {"od_nucleo": od, "od_anillo": od * 0.1, "foco_bajo": np.zeros(n, bool),
               "borde_um": np.full(n, 200.0), "saturado": np.zeros(n, bool),
               "fragmento": rng.integers(0, 3, n), "pliegue": np.zeros(n, bool),
               "grandqc": np.zeros(n, bool), "centroide": rng.uniform(0, 1000, (n, 2))}
        t = K.calcula_T(obj, obj, M, False, 1)
        self.assertEqual(t["alto"], [["clasicas", "nucleo"]])
        t2 = K.calcula_T(obj, obj, M, False, 1, revision={"regla": "pigmento"})
        self.assertEqual(t2["alto"], [])
        self.assertEqual(t2["regimenes"]["clasicas"]["nucleo"]["T"], 0.10)


class GaleriaGrandQC(unittest.TestCase):
    def test_galeria_aunque_quede_fuera(self):
        from shapely.geometry import box

        def marca_mucho(lam):                                # 40 % del fragmento 0
            return {"carga": True, "artefactos": [(box(100, 100, 500, 1100), "Fold")],
                    "motivo": None}
        d, lector, _, pre = _congelado(grandqc=marca_mucho)
        g = pre["grandqc"]
        self.assertFalse(g["en_uso"])
        self.assertIn("marca", g["motivo"])
        self.assertIn("galeria", g)
        for n in K.SUELO:
            self.assertEqual(len(g["galeria"][n]["marcadas"]), 10)
            self.assertEqual(len(g["galeria"][n]["no_marcadas"]), 10)


class DiagnosticoArea(unittest.TestCase):
    """`precongela --diagnostico-area`: InstanSeg frente al detector B en teselas de suelo, en
    µm², sin sellar ni escribir nada. Aquí el InstanSeg es un sustituto que devuelve la VERDAD (el
    real, contra discos de tamaño físico conocido, está en test_laminillas_piloto_segmenta)."""

    LADO = 512

    @classmethod
    def setUpClass(cls):
        cls.lam = F.lamina("P-HER2NEG", 21)
        nuc = cls.lam.verdad["nucleos"]
        cls.verdad_um2 = float(np.median(np.pi * (nuc[:, 2] * F.MPP) ** 2))   # r en px de L0

    def _stub(self, lector, lam, nombre, zona, elige, dir_trabajo):
        esq = np.array([(x, y) for y in range(100, F.H - self.LADO, 600)
                        for x in range(100, F.W - self.LADO, 600)])
        idx = elige(esq, self.LADO)
        nuc = lam.verdad["nucleos"]
        a = np.pi * (nuc[:, 2] * lam.mpp_l0) ** 2
        return {"celulas": F.poligonos(nuc), "esquinas": esq[idx], "lado_l0": self.LADO,
                "base_downsample": 0.5 / lam.mpp_l0, "tile_spec_mpp": 0.5, "teselado_pasa": True,
                "mapa": {"pixeles_um2": a, "contorno_um2": a}}

    def test_detector_b_e_instanseg_en_um2(self):
        lector = F.LectorSint({"P-HER2NEG": self.lam})
        r = K.diagnostico_area(lector, "P-HER2NEG", diagnostico=self._stub)
        self.assertEqual(r["teselas"], 3)
        v = self.verdad_um2
        self.assertAlmostEqual(r["detector_b_um2"]["mediana"] / v, 1.0, delta=0.15)
        self.assertAlmostEqual(r["instanseg_poligono_um2"]["mediana"] / v, 1.0, delta=0.03)
        self.assertGreater(r["pares_iou_05"]["n"], 0.9 * r["instanseg_poligono_um2"]["n"])
        self.assertAlmostEqual(r["pares_iou_05"]["cociente_instanseg_entre_b"]["mediana"], 1.0,
                               delta=0.15)
        self.assertTrue(r["puerta"]["pasa"], r["puerta"])
        self.assertLessEqual(r["fraccion_bajo_25_um2"]["instanseg"], 0.0)
        for clave in ("mediana", "p10", "p90"):                  # solo números agregados
            self.assertIsInstance(r["detector_b_um2"][clave], float)

    def test_diana_no_y_cli_sin_escribir(self):
        from unittest import mock
        lector = F.LectorSint({"P-HER2NEG": self.lam, "P-KI67": F.lamina("P-KI67", 22)})
        with self.assertRaises(ValueError):
            K.diagnostico_area(lector, "P-KI67", diagnostico=self._stub)
        self.assertEqual(lector.abiertas, [])                   # ni un píxel de la diana
        d = _tmp("lam-diag-")
        with mock.patch.object(K, "dir_sesion", return_value=d):
            rc = K.main(["precongela", "--diagnostico-area"], lector=lector,
                        diagnostico=self._stub)
            with self.assertRaises(SystemExit):
                K.main(["congela", "--diagnostico-area"], lector=lector)
        self.assertEqual(rc, K.CODIGO_DIAGNOSTICO)               # ≠ 0: no deja traza de memoria
        self.assertEqual(os.listdir(d), [])                      # ni sello ni pre-congelación


# ── regla física del mpp: hematíes sintéticos de diámetro conocido ─────────────────────────────
RUI_H, RUI_E = F.unit([0.65, 0.70, 0.29]), F.unit([0.07, 0.99, 0.11])
OD_HEMATIE = F.unit([0.10, 0.80, 0.60])          # deja pasar el rojo; absorbe verde y azul


def hueso_sintetico(d_um, mpp_pinta=0.2506, mpp_etiqueta=0.2506, lado=2048, semilla=0,
                    opaco="B-HE-1"):
    """H&E de médula sintética por Beer-Lambert (vectores de Ruifrok): estroma rosa pálido, una
    trabécula eosinófila intensa y grande, ~150 hematíes AISLADOS de cara de diámetro `d_um`
    (±6 %, palidez central) pintados a `mpp_pinta` y TRAMPAS que no deben contar: núcleos,
    «eosinófilos» (disco rojo de 12 µm con núcleo dentro), racimos de cuatro hematíes que se
    tocan, hematíes de canto (elipse 0,45) y parejas a 2 px (con el desenfoque, se
    tocan o se funden). Desenfoque σ 0,7 px y ruido. La
    etiqueta dice `mpp_etiqueta` (si ≠ `mpp_pinta`, el mpp está mal etiquetado)."""
    import math
    from scipy import ndimage as ndi
    from shapely.geometry import box
    rng = np.random.default_rng(semilla)
    od = np.zeros((lado, lado, 3)) + (0.12 * RUI_E + 0.03 * RUI_H)
    yy, xx = np.mgrid[:lado, :lado]
    ocupado = (((xx - 1700) / 260.0) ** 2 + ((yy - 340) / 220.0) ** 2) < 1
    od[ocupado] = 0.40 * RUI_E + 0.05 * RUI_H                   # trabécula (semillas, enorme)
    r_px = d_um / 2 / mpp_pinta
    trampas = dict.fromkeys(("nucleo", "eosinofilo", "racimo", "canto", "pareja"), 0)

    def sitio(r, margen=6):
        for _ in range(200):
            cx, cy = rng.uniform(r + 30, lado - r - 30, 2)
            y0, y1 = int(cy - r - margen), int(cy + r + margen + 1)
            x0, x1 = int(cx - r - margen), int(cx + r + margen + 1)
            if not ocupado[y0:y1, x0:x1].any():
                ocupado[y0:y1, x0:x1] = True
                return cx, cy
        return None

    def elipse(cx, cy, rx, ry, valor, ang=0.0, palidez=False):
        R = max(rx, ry)
        sy, sx = np.mgrid[int(cy - R - 2):int(cy + R + 3), int(cx - R - 2):int(cx + R + 3)]
        dx, dy = sx + 0.5 - cx, sy + 0.5 - cy
        u = (dx * math.cos(ang) + dy * math.sin(ang)) / rx
        v = (-dx * math.sin(ang) + dy * math.cos(ang)) / ry
        q = u * u + v * v
        m = q <= 1
        f = np.where(q < 0.35 ** 2, 0.75, 1.0)[m] if palidez else np.ones(int(m.sum()))
        od[sy[m], sx[m]] = f[:, None] * np.asarray(valor)[None, :]

    verdad = []
    while len(verdad) < 150:
        r = r_px * rng.normal(1, 0.06)
        s = sitio(r)
        if s is None:
            break
        elipse(s[0], s[1], r, r, rng.uniform(0.6, 0.9) * OD_HEMATIE, palidez=True)
        verdad.append(2 * r * mpp_pinta)
    for _ in range(80):
        r = rng.uniform(3, 4) / mpp_pinta
        s = sitio(r)
        if s:
            elipse(s[0], s[1], r, r, 0.6 * RUI_H + 0.05 * RUI_E)
            trampas["nucleo"] += 1
    for _ in range(40):
        r = 6 / mpp_pinta
        s = sitio(r)
        if s:
            elipse(s[0], s[1], r, r, 0.7 * OD_HEMATIE)
            elipse(s[0] + 0.2 * r, s[1], 0.45 * r, 0.45 * r, 0.6 * RUI_H)
            trampas["eosinofilo"] += 1
    for _ in range(40):
        s = sitio(3 * r_px)
        if s:
            for k in range(4):
                a = k * math.pi / 2
                elipse(s[0] + math.cos(a) * 0.95 * r_px, s[1] + math.sin(a) * 0.95 * r_px,
                       r_px, r_px, 0.75 * OD_HEMATIE)
            trampas["racimo"] += 1
    for _ in range(40):
        s = sitio(r_px)
        if s:
            elipse(s[0], s[1], r_px, 0.45 * r_px, 0.75 * OD_HEMATIE, ang=rng.uniform(0, math.pi))
            trampas["canto"] += 1
    for _ in range(30):
        s = sitio(2.2 * r_px)
        if s:
            for sg in (-1, 1):
                elipse(s[0] + sg * (r_px + 1.0), s[1], r_px, r_px, 0.75 * OD_HEMATIE)
            trampas["pareja"] += 1
    od = ndi.gaussian_filter(od, (0.7, 0.7, 0))
    img = np.asarray(F.I0) * np.power(10.0, -od) + rng.normal(0, 1.5, od.shape)
    img = np.clip(np.rint(img), 0, 255).astype(np.uint8)
    return F.LaminaSint(opaco, img, mpp_etiqueta, box(0, 0, lado, lado),
                        verdad={"diametros_um": np.asarray(verdad), "trampas": trampas})


class ReglaMpp(unittest.TestCase):
    """`precongela --diagnostico-mpp`: el diámetro de hematíes sintéticos de diámetro CONOCIDO se
    recupera con el mpp de la etiqueta (±0,25 µm en la mediana; medido: ±0,02), las trampas no
    cuentan, 9,5 µm o un mpp mal etiquetado (×2) salen de 6,0-9,0 µm y no pasan, y no se escribe
    ni se abre nada que no sea hueso."""

    @classmethod
    def setUpClass(cls):
        cls.r = {}
        for d in (6.5, 7.5, 9.5):
            lam = hueso_sintetico(d, semilla=int(d * 10))
            cls.r[d] = (lam, K.regla_mpp_lamina(F.LectorSint({"B-HE-1": lam}), "B-HE-1"))

    def test_diametro_recuperado(self):
        for d, (lam, r) in self.r.items():
            v = lam.verdad["diametros_um"]
            m = r["diametro_um"]
            self.assertAlmostEqual(m["mediana"], float(np.median(v)), delta=0.25, msg=d)
            self.assertAlmostEqual(m["p10"], float(np.percentile(v, 10)), delta=0.5, msg=d)
            self.assertAlmostEqual(m["p90"], float(np.percentile(v, 90)), delta=0.5, msg=d)
            # los que tocan el marco de la ventana se pierden; trampas que cuelan: ≤ 5 %
            self.assertGreaterEqual(m["n"], 0.75 * len(v), d)
            self.assertLessEqual(m["n"], 1.05 * len(v), d)
            self.assertTrue(r["mpp_igual_al_del_piloto"])
            for k, med in r["sensibilidad_mediana_um"].items():
                self.assertAlmostEqual(med, m["mediana"], delta=0.5, msg=(d, k))
        self.assertTrue(self.r[6.5][1]["diametro_um"]["pasa"])
        self.assertTrue(self.r[7.5][1]["diametro_um"]["pasa"])
        self.assertFalse(self.r[9.5][1]["diametro_um"]["pasa"])      # fuera de 6,0-9,0 µm

    def test_trampas_descartadas(self):
        lam, r = self.r[7.5]
        t, ds = lam.verdad["trampas"], r["descartes"]
        self.assertGreater(min(t.values()), 5, t)
        self.assertGreaterEqual(ds["forma"], 0.8 * (t["racimo"] + t["canto"]), (t, ds))
        self.assertGreaterEqual(ds["nucleo"], 0.8 * t["eosinofilo"], (t, ds))
        # parejas: o se funden (forma) o quedan a ≤ 3 px (no aisladas); ninguna cuenta
        self.assertGreaterEqual(ds["no_aislado"] + ds["forma"] - t["racimo"] - t["canto"],
                                0.8 * t["pareja"], (t, ds))
        self.assertGreater(ds["no_aislado"], 0, (t, ds))
        self.assertLessEqual(r["diametro_um"]["n"], len(lam.verdad["diametros_um"]))
        self.assertGreaterEqual(ds["tamano"], 1)                  # la trabécula
        self.assertEqual(sum(r["histograma_um"]["n"]), r["diametro_um"]["n"])
        rgb = r["rgb_mediano_aceptados"]
        self.assertGreater(rgb[0] - rgb[1], 80)                  # rojo: R ≫ G

    def test_mpp_mal_etiquetado_no_pasa(self):
        lam = hueso_sintetico(7.5, mpp_pinta=0.5012, semilla=3)
        lector = F.LectorSint({"B-HE-1": lam, "B-HE-2": hueso_sintetico(7.5, semilla=4,
                                                                         opaco="B-HE-2")})
        r = K.regla_mpp(lector)
        m = r["laminas"]["B-HE-1"]["diametro_um"]
        self.assertAlmostEqual(m["mediana"], float(np.median(lam.verdad["diametros_um"])) / 2,
                               delta=0.4)
        self.assertFalse(m["pasa"])
        self.assertTrue(r["laminas"]["B-HE-2"]["diametro_um"]["pasa"])
        self.assertFalse(r["pasa"])
        self.assertEqual(m["estado"], "fuera de rango")
        self.assertIn("fuera de rango (B-HE-1): no sellar", r["veredicto"])

    def test_pocos_hematies_no_pasa(self):
        lam, _ = self.r[7.5]
        lector = F.LectorSint({"B-HE-1": lam, "B-HE-2": lam})
        r = K.regla_mpp(lector, regla={"n_min": 10000})
        self.assertFalse(r["laminas"]["B-HE-1"]["diametro_um"]["pasa"])
        self.assertEqual(r["laminas"]["B-HE-1"]["diametro_um"]["estado"], "insuficiente")
        self.assertFalse(r["pasa"])
        self.assertIn("NO concluyente", r["veredicto"])               # no es «fuera de rango»

    def test_solo_hueso_y_cli_sin_escribir(self):
        import contextlib
        import io
        from unittest import mock
        lector = F.LectorSint({"B-HE-1": self.r[7.5][0], "B-HE-2": self.r[6.5][0],
                               "P-KI67": F.lamina("P-KI67", 22)})
        with self.assertRaises(ValueError):
            K.regla_mpp_lamina(lector, "P-KI67")
        self.assertEqual(lector.abiertas, [])                   # ni un píxel de otra lámina
        d = _tmp("lam-diag-mpp-")
        salida = io.StringIO()
        with mock.patch.object(K, "dir_sesion", return_value=d), \
                contextlib.redirect_stdout(salida):
            rc = K.main(["precongela", "--diagnostico-mpp"], lector=lector)
            with self.assertRaises(SystemExit):
                K.main(["congela", "--diagnostico-mpp"], lector=lector)
        self.assertEqual(rc, K.CODIGO_DIAGNOSTICO)               # ≠ 0: no deja traza de memoria
        self.assertEqual(os.listdir(d), [])                      # ni sello ni pre-congelación
        self.assertEqual(sorted(set(lector.abiertas)), ["B-HE-1", "B-HE-2"])
        texto = salida.getvalue()
        r = json.loads(texto[texto.index("{"):])
        self.assertTrue(r["pasa"], r["veredicto"])
        self.assertEqual(r["criterio"], K.CRITERIO_MPP)

        def listas(o):                                           # solo números agregados
            if isinstance(o, dict):
                return [x for v in o.values() for x in listas(v)]
            return [o] if isinstance(o, list) else []
        self.assertLessEqual(max(len(x) for x in listas(r)), 40)


class Memoria(unittest.TestCase):
    def test_traza_de_guarda_memoria(self):
        d = _tmp("lam-traza-")
        r = K.lee_traza_memoria(F.traza_memoria(d, "x", 7.3))
        self.assertAlmostEqual(r["pico_gb"], 7.3)
        self.assertEqual(r["muestras"], 20)
        with self.assertRaises(ValueError):
            K._trazas({"laminillas": 7.3})


class Reglas(unittest.TestCase):
    def test_elige_L(self):
        med = {100: 40, 150: 120, 200: 300, 300: 600, 400: 900}
        self.assertEqual(K.elige_L(40.0, med), 150)          # 100 no llega a 100 núcleos
        self.assertEqual(K.elige_L(90.0, med), 200)          # L ≥ 2× p90
        self.assertIsNone(K.elige_L(250.0, med))

    def test_nunca_decir(self):
        prohibidas = ["solo mirasteis una roi", "qué hospital acertó", "perdió el re",
                      "re-negative clone", "subclone", "er-low", "er-negative",
                      "negative-control slide", "negative control", "polaris confirma",
                      "las tres lecturas son compatibles", "lámina entera", "whole slide",
                      "tumour cells", "tumor cells", "celularidad", "pureza",
                      "sobre carcinoma invasivo", "coexpresión", "coexpression"]
        for nombre in ("laminillas_color.py", "laminillas_segmenta.py", "laminillas_congela.py"):
            with open(os.path.join(TOOLS, nombre), encoding="utf-8") as f:
                texto = f.read().lower()
            for p in prohibidas:
                self.assertNotIn(p, texto, "%s dice «%s»" % (nombre, p))
            self.assertIsNone(re.search(r"\b(ki-?67|er|re)[- ](low|negative)\b", texto))


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=1).result
    print("test_laminillas_piloto_congela: %d casos, %d fallos, %d errores, %d saltados"
          % (r.testsRun, len(r.failures), len(r.errors), len(r.skipped)))
    for d in _TEMPORALES:
        shutil.rmtree(d, ignore_errors=True)
    sys.exit(0 if r.wasSuccessful() else 1)
