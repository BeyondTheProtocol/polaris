#!/usr/bin/env python3
"""tests/test_laminillas_piloto_b.py — módulo B del piloto de laminillas: registro, métricas y
GeoJSON (plan «laminillas DFCI», F3 y F4).

TODO SINTÉTICO: cortes seriados de un bloque inventado (`_laminillas_sinteticas`) con rotación,
espejo y traslación CONOCIDOS, y tablas de núcleos con densidad y positividad CONOCIDAS. Ninguna
lámina real, ninguna ruta clínica. Verifica el EFECTO: que se recuperan los parámetros conocidos
con tolerancias declaradas (DECLARADAS abajo, junto a cada aserción).

Corre con el intérprete del venv `patologia` (se re-ejecuta con él); sin venv, SKIP (rc 77).
"""
import copy
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

VENV_DIR = os.environ.get("BTP_VENV_PATOLOGIA") or os.path.expanduser("~/.polaris-venvs/patologia")
VENV_PY = os.path.join(VENV_DIR, "bin", "python")
if os.path.realpath(sys.prefix) != os.path.realpath(VENV_DIR):
    if os.path.exists(VENV_PY) and not os.environ.get("BTP_PILOTO_B_REEXEC"):
        os.environ["BTP_PILOTO_B_REEXEC"] = "1"
        os.execv(VENV_PY, [VENV_PY, os.path.abspath(__file__)] + sys.argv[1:])
    print("SKIP: falta el venv patologia (%s)%s" % (
        VENV_DIR, " · BTP_PORTABLE" if os.environ.get("BTP_PORTABLE") else ""))
    sys.exit(77)

import numpy as np  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _laminillas_sinteticas as S  # noqa: E402
import laminillas_geojson as G  # noqa: E402
import laminillas_metricas as MET  # noqa: E402
import laminillas_registro as R  # noqa: E402
import laminillas_sello as SL  # noqa: E402

FECHA = "2026-10-01"

# Frases EXACTAS del plan (copiadas del plan, no del código): el código debe emitir estas.
FRASES_PLAN = [
    "near-uniform at this scale: regional association not estimable",
    "threshold at fixed floor (0.10 OD); measured floor p99.9 = ",
    "slide background above floor; threshold raised",
    "detection-dependent", "denominator-dependent", "registration not verified",
    "small fragment: registration verified on ", "small fragment: registration not independently verified",
    "estimated section order; spacing unknown", "not the IKWG method",
    "descriptive, single block, n=1", "section-to-section + registration ceiling",
    "borderline: reported both ways", "no signal", "quantifiable", "focal",
    "ER-poor region (<10 % of nuclei above T; k/n cells, 95 % CI; not an ASCO/CAP category)",
    "candidate, epithelial nature not confirmed",
    "CK19+ epithelium with myoepithelial layer or not assessable: benign or in-situ not excluded",
    "ER-poor region within CK19+ epithelium, p63-negative",
    "pilot consensus (KI67∩CK19; HER2NEG and HER2 mapped onto it)",
    "dependiente de umbral", "DAB saturated; intensity bins not informative",
    "fixed, uncalibrated cut-offs", "registration check inconclusive",
    "CK19-positive epithelium on a serial section (regional prior); includes in-situ carcinoma "
    "(~20 % reported) and any benign ducts/lobules; invasive, in-situ and benign are not "
    "separated by any model used",
    "no membrane DAB signal above the floor measured on the second HER2-labelled slide (‘HER2, "
    "NEG’; nature not determined: reagent control or duplicate)",
    "not assessable (no internal positive control)", "invasive-only where p63 available",
    "same tissue block across the 11 IHC slides (fingerprint); H&E from a different section "
    "level, positioned at core level only, not co-registered; link to its accession by sender's "
    "file name",
    "insufficient detectable tissue on this scan for registration (cause not determined: pale "
    "counterstain, depleted section or focus)",
    "spatial pattern of NE-marker-positive cells (descriptive, n=1)",
]
NUNCA = ["solo mirasteis una roi", "qué hospital acertó", "perdió el re", "re-negative clone",
         "re-negative subclone", "er-low", "er-negative", "negative-control slide",
         "polaris confirma", "compatibles con la heterogeneidad", "lámina entera", "whole slide",
         "whole-slide", "tumour cells", "tumor cells", "celularidad", "pureza", "coexpresión",
         "co-expression", "% sobre carcinoma invasivo", "% of invasive carcinoma"]
SEMILLAS = {"maestra": 20261001, "bootstrap": 20261001, "pixeles": 20261101,
            "galeria_focal": 20261201}


def _unit(v):
    v = np.asarray(v, float)
    return (v / np.linalg.norm(v)).tolist()


def _constantes_congela():
    """REGISTRO, HOTSPOT y REGLA_L de `laminillas_congela` (módulo A) si importa: el cotejo del
    sello se prueba contra lo que de verdad sella la congelación. Si no, copia literal del plan."""
    try:
        import laminillas_congela as CG
        return CG.REGISTRO, CG.HOTSPOT, CG.REGLA_L, "laminillas_congela"
    except Exception:                                  # noqa: BLE001
        reg = {"match": {"max_ratio": 0.8, "cross_check": True},
               "ransac": {"min_samples": 2, "residual_threshold_px": 12, "max_trials": 5000},
               "min_inliers_fc": 30,
               "puerta": {"tre_um_max": 50.0, "ventana_b_um": 256, "paso_b_um": 128,
                          "ventana_dentro_fc_min": 0.8, "pico_cociente_min": 1.5,
                          "desplaz_max_um": 128, "min_picos": 15, "pequeno_min_picos": 5,
                          "b_prima": {"mpp": 2.0, "sigma_um": 8.0}}}
        return (reg, {"diametro_mm": 0.5, "min_nucleos": 500, "paso_um": 50},
                {"candidatos_um": [100, 150, 200, 300, 400], "tre_factor": 2.0,
                 "mediana_min_nucleos": 100, "region_min_nucleos": 50}, "copia del plan")


def _regimen(T, p999):
    banda = [max(T - 0.05, p999 + 0.02), T + 0.05]
    rige = "suelo_fijo" if p999 + 0.05 < 0.10 else "p99.9+0.05"
    rot = (["threshold at fixed floor (0.10 OD); measured floor p99.9 = %.3f" % p999]
           if rige == "suelo_fijo" else [])
    comp = {"p999": p999, "rige": rige, "rotulos": rot, "T": T, "banda": banda,
            "estado": "alto" if T > 0.25 else "ok"}
    return {"nucleo": dict(comp), "anillo": dict(comp)}, banda, rot


def contenido_sello(T=0.10, p999=0.04, fp_sup=0.002, modulo_b=None, registro=None,
                    fp_banda=None, otros_regimenes=None, rige="clasicas", fp_regimenes=None,
                    por_lamina=None, rotulos_umbral=None, semillas=None):
    """Sello sintético con el formato de `laminillas_congela`. `otros_regimenes`: {nombre: (T,
    p999)}; `fp_banda`: {"T_bajo": ic95_sup, "T_alto": ic95_sup} (tasas en los extremos)."""
    REG, HOT, RL, _ = _constantes_congela()
    h, d = _unit(S.RUIFROK_H), _unit(S.RUIFROK_DAB)
    regs = {rige: _regimen(T, p999)[0]}
    for nombre, (Tx, px) in (otros_regimenes or {}).items():
        regs[nombre] = _regimen(Tx, px)[0]
    _, banda, rot = _regimen(T, p999)

    def fp(sup):
        out = {"k": 1, "n": 2000, "fraccion": 0.0005, "ic95": [0.0, sup]}
        if fp_banda:
            out["banda"] = {e: {"k": 3, "n": 2000, "fraccion": 0.0015, "ic95": [0.0, v]}
                            for e, v in fp_banda.items()}
        return out
    # (iv) de las de suelo va EN el sello (congela); el resto, al anexo tras el sello
    pl = {n: {"contratincion": {"angulo": 1.0, "fuera_mapa": False, "rotulos": []}}
          for n in ("P-HER2NEG", "P-HER2")}
    pl.update(por_lamina or {})
    c = {
        "vectores": {"H": h, "DAB": d, "tercero": _unit(np.cross(h, d))},
        "residuo": {"por_lamina": pl},
        "precongelacion": {"sha256": "sintetico", "declaraciones": []},
        "parametros": {"nota": "sintético: los parámetros de A los coteja su propia guardia"},
        "laminas_residuo": ["P-RE", "P-HER2", "P-HER2NEG", "P-KI67", "P-CK19", "P-SYN",
                            "P-CHGA", "P-{{DIANA3}}"],
        "umbral": {"T": {"nucleo": T, "anillo": T}, "banda": {"nucleo": banda, "anillo": banda},
                   "rige": rige, "rotulos": list(rot) + list(rotulos_umbral or []),
                   "regimenes": regs},
        "fp_her2": {cc: fp(fp_sup) for cc in ("nucleo", "anillo")},
        "registro": registro if registro is not None else REG, "hotspot": HOT, "regla_L": RL,
        "modulo_b": modulo_b if modulo_b is not None else MET.contenido_partida(),
        "semillas": dict(semillas or SEMILLAS),
        "medida": {"hscore": {"cortes": ["T", 0.4, 0.6]},
                   "semilla_galeria_focal": (semillas or SEMILLAS)["galeria_focal"]},
    }
    if fp_regimenes:
        c["fp_her2_regimenes"] = {r: {cc: {"k": 1, "n": 2000, "ic95": [0.0, v]}
                                      for cc in ("nucleo", "anillo")}
                                  for r, v in fp_regimenes.items()}
    return c


def sella(dirn, nombre="sello", **kw):
    """Cada sello en su carpeta (la ruta canónica es `<carpeta>/congelacion.json`)."""
    return SL.sella(os.path.join(dirn, nombre, SL.FICHERO), contenido_sello(**kw), FECHA)


def anota_iv(sello, laminas, fuera=()):
    """(iv) del módulo A tras el sello, en su anexo sellado, como lo deja
    `laminillas_congela.contratincion_lamina`: `fuera` sale del mapa («counterstain differs»)."""
    import laminillas_congela as CG
    for n in laminas:
        sale = n in fuera
        CG._anota_extension(os.path.dirname(sello.ruta), sello, "contratincion", n,
                            {"angulo": 15.0 if sale else 1.0, "fuera_mapa": sale,
                             "rotulos": [CG.C.ROTULOS["contratincion"]] if sale else []})


def _carga(ruta):
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def _guarda(obj, ruta):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def _sin_nunca(test, obj):
    test.assertEqual(MET.barre_nunca(obj), [], "frase de «Nunca decir»")
    texto = " ".join(MET._cadenas(obj)).lower()
    for f in NUNCA:
        test.assertNotIn(f, texto, "frase de «Nunca decir»: %s" % f)


# ══ A. Sello ══════════════════════════════════════════════════════════════════════════════
class Sello(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="piloto-b-sello-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sin_sello_no_mide_diana(self):
        with self.assertRaises(SL.SelloAusente):
            MET.Contexto(None, "P-KI67")
        with self.assertRaises(SL.SelloAusente):
            MET.Contexto(os.path.join(self.tmp, "nada", SL.FICHERO), "P-CK19")
        with self.assertRaises(SL.SelloAusente):
            R.registra_par("P-CK19", "P-KI67", None, lector=object())
        ctx = MET.Contexto(None, "P-HER2NEG", T=0.12)           # suelo: pre-congelación
        self.assertEqual(ctx.T, 0.12)
        self.assertIn(MET.ROTULOS["pre_congelacion"], ctx.rotulos_umbral())

    def test_sello_tocado_o_incoherente_no_vale(self):
        s = sella(self.tmp)
        self.assertEqual(MET.Contexto(s.ruta, "P-KI67").T, 0.10)
        d = _carga(s.ruta)
        d["umbral"]["T"]["nucleo"] = 0.05                       # tocado después de sellar
        ruta2 = os.path.join(self.tmp, "tocado", SL.FICHERO)
        _guarda(d, ruta2)
        with self.assertRaises(SL.SelloInvalido):
            MET.Contexto(ruta2, "P-KI67")
        mb = MET.contenido_partida()
        mb["registro"]["ratio"] = 0.7                           # no casa con registro.match
        s3 = sella(self.tmp, "incoherente", modulo_b=mb)
        with self.assertRaises(SL.SelloInvalido):
            R._params(s3, "P-CK19", "P-KI67")
        c = contenido_sello()
        del c["modulo_b"]
        s4 = SL.sella(os.path.join(self.tmp, "sin_b", SL.FICHERO), c, FECHA)
        with self.assertRaises(SL.SelloInvalido):
            MET.Contexto(s4, "P-KI67")
        with self.assertRaises(MET.MetricaError):               # con sello, T no se pasa a mano
            MET.Contexto(s.ruta, "P-KI67", T=0.2)

    def test_semillas_y_cortes_cotejados(self):
        """Hallazgo «semillas»: la semilla de la galería focal y la del bootstrap son las de
        `semillas` del sello (y `medida.semilla_galeria_focal`); si no casan, el sello no vale.
        Igual con los cortes del H-score."""
        s = sella(self.tmp)
        p = MET.Contexto(s, "P-KI67").p
        self.assertEqual(p["semilla_galeria_focal"], SEMILLAS["galeria_focal"])
        self.assertEqual(p["semilla_bootstrap"], SEMILLAS["bootstrap"])
        otra = dict(SEMILLAS, galeria_focal=20261010)
        s2 = sella(self.tmp, "otra_semilla", semillas=otra)
        with self.assertRaises(SL.SelloInvalido):
            MET.Contexto(s2, "P-KI67")
        c = contenido_sello()
        c["medida"]["hscore"]["cortes"] = ["T", 0.3, 0.6]
        s3 = SL.sella(os.path.join(self.tmp, "cortes", SL.FICHERO), c, FECHA)
        with self.assertRaises(SL.SelloInvalido):
            MET.Contexto(s3, "P-KI67")

    def test_nunca_pisa_y_recongela_una_vez_por_ejecucion(self):
        """Hallazgo «recongelación»: causa solo lector/I0/segmentador; ruta canónica única; un
        segundo sello «primero» junto a un anterior archivado no se escribe; la cadena se exige."""
        s = sella(self.tmp)
        carpeta = os.path.dirname(s.ruta)
        with self.assertRaises(FileExistsError):
            sella(self.tmp)
        with self.assertRaises(SL.SelloInvalido):
            SL.sella(os.path.join(self.tmp, "x", SL.FICHERO), contenido_sello(), "1-oct-2026")
        with self.assertRaises(SL.SelloInvalido):               # nombre no canónico
            SL.sella(os.path.join(self.tmp, "x", "otro.json"), contenido_sello(), FECHA)
        # recongelación como la hace congela.recongela: el anterior se archiva y se sella encima
        archivo = os.path.join(carpeta, SL.ARCHIVO_ANTERIOR % s.sha256[:12])
        os.replace(s.ruta, archivo)
        with self.assertRaises(SL.SelloInvalido):               # causa que no es de ejecución
            SL.sella(s.ruta, contenido_sello(T=0.2), FECHA, anterior=s,
                     causa="preferia otro T tras ver Ki67")
        with self.assertRaises(SL.SelloInvalido):               # «primero» nuevo junto al archivado
            SL.sella(s.ruta, contenido_sello(T=0.2), "2026-09-01")
        r1 = SL.sella(s.ruta, contenido_sello(), FECHA, anterior=s, causa="lector")
        self.assertEqual(SL.carga(r1.ruta).anterior["sha256"], s.sha256)
        self.assertTrue(SL.exige(r1, ["P-KI67"]).cita()["recongelado"])
        os.replace(archivo, archivo + ".perdido")               # sin su anterior, no vale
        with self.assertRaises(SL.SelloInvalido):
            SL.exige(r1.ruta, ["P-KI67"])
        os.replace(archivo + ".perdido", archivo)
        os.replace(r1.ruta, os.path.join(carpeta, SL.ARCHIVO_ANTERIOR % r1.sha256[:12]))
        with self.assertRaises(SL.SelloInvalido):               # una sola vez
            SL.sella(s.ruta, contenido_sello(), FECHA, anterior=r1, causa="lector")

    def test_solo_el_sello_de_sesion_por_la_ventanilla(self):
        """Por la ventanilla (cwd = SESION), un sello de otra carpeta no vale; un Sello en memoria
        que no es el del fichero, tampoco."""
        s = sella(self.tmp, "sesion")
        otro = sella(self.tmp, "otra", T=0.20)
        previo, env = os.getcwd(), os.environ.get("BTP_VENTANILLA")
        try:
            os.chdir(os.path.dirname(s.ruta))
            os.environ["BTP_VENTANILLA"] = "1"
            self.assertEqual(MET.Contexto(SL.FICHERO, "P-KI67").T, 0.10)
            with self.assertRaises(SL.SelloInvalido):
                MET.Contexto(otro.ruta, "P-KI67")
        finally:
            os.chdir(previo)
            if env is None:
                os.environ.pop("BTP_VENTANILLA", None)
            else:
                os.environ["BTP_VENTANILLA"] = env
        falso = SL.Sello(dict(s.d, sha256="0" * 64), s.ruta)
        with self.assertRaises(SL.SelloInvalido):
            MET.Contexto(falso, "P-KI67")

    def test_regimen_sellado_sin_recongelar(self):
        """Hallazgo «régimen»: con GrandQC y reglas clásicas sellados, el contexto puede pasar a
        clásicas (punto 5) sin recongelar; la tasa de P-HER2 de ese régimen solo si está sellada."""
        s = sella(self.tmp, rige="grandqc", T=0.13, p999=0.08,
                  otros_regimenes={"clasicas": (0.10, 0.04)})
        self.assertEqual(MET.Contexto(s, "P-KI67").T, 0.13)
        c = MET.Contexto(s, "P-KI67", regimen="clasicas")
        self.assertEqual((c.T, c.cita()["regimen"]), (0.10, "clasicas"))
        self.assertIsNone(c.fp)                                  # tasa del otro régimen: no sellada
        with self.assertRaises(MET.MetricaError):
            MET.puerta_senal(_nucleos(1, lambda x, y: np.full(len(x), 0.2)), {}, c,
                             "ck19_erosionada")
        with self.assertRaises(SL.SelloInvalido):
            MET.Contexto(s, "P-KI67", regimen="inventado")
        s2 = sella(self.tmp, "con_fp", rige="grandqc", T=0.13, p999=0.08,
                   otros_regimenes={"clasicas": (0.10, 0.04)}, fp_regimenes={"clasicas": 0.003})
        self.assertEqual(MET.Contexto(s2, "P-KI67", regimen="clasicas").fp["ic95_sup"], 0.003)

    def test_high_floor_no_se_pierde_con_vector_propio(self):
        """Hallazgo «high floor»: con vector DAB propio la lámina conserva «high floor» (y lo
        gana si su T propio > 0,25); la banda pasa a obligatoria."""
        propio = {"supera": True, "dab_propio": _unit([0.3, 0.6, 0.75]),
                  "T_propio": {c: {"T": 0.30, "banda": [0.25, 0.35], "p999": 0.25,
                                   "rige": "p99.9+0.05", "rotulos": [], "estado": "alto"}
                               for c in ("nucleo", "anillo")}}
        s = sella(self.tmp, "hf", rotulos_umbral=["high floor"], por_lamina={"P-RE": propio})
        u = s.umbral("P-RE")
        self.assertIn("slide-specific DAB vector", u["rotulos"])
        self.assertIn("high floor", u["rotulos"])
        self.assertTrue(u["banda_obligatoria"])
        s2 = sella(self.tmp, "hf2", por_lamina={"P-RE": propio})          # sin high floor de tanda
        self.assertIn("high floor", s2.umbral("P-RE")["rotulos"])
        self.assertNotIn("high floor", s2.umbral("P-KI67")["rotulos"])

    def test_exige_todas_las_secciones(self):
        """Pedido del módulo A: `laminillas_sello.exige` comprueba que el sello esté completo. Un
        sello con solo las secciones de la guardia de A vale para A (`SECCIONES_BASE`) pero no
        para el registro ni las métricas; sin `fp_her2` o sin `modulo_b`, tampoco."""
        for falta in ("precongelacion", "parametros", "fp_her2", "modulo_b", "semillas"):
            c = contenido_sello()
            del c[falta]
            s = SL.sella(os.path.join(self.tmp, "sin_" + falta, SL.FICHERO), c, FECHA)
            with self.assertRaises(SL.SelloInvalido, msg=falta):
                MET.Contexto(s, "P-KI67")
            with self.assertRaises(SL.SelloInvalido, msg=falta):
                R._params(s, "P-CK19", "P-KI67")
        c = {k: v for k, v in contenido_sello().items() if k in SL.SECCIONES_BASE}
        base = SL.sella(os.path.join(self.tmp, "base", SL.FICHERO), c, FECHA)
        self.assertEqual(SL.exige(base, ["P-KI67"]).sha256, base.sha256)   # guardia de A: pasa
        with self.assertRaises(SL.SelloInvalido):
            SL.exige(base, ["P-KI67"], SL.SECCIONES_MODULO_B)
        with self.assertRaises(SL.SelloInvalido):                # ni la base completa
            SL.exige(SL.sella(os.path.join(self.tmp, "vacio", SL.FICHERO),
                              {"vectores": {"H": [1, 0, 0], "DAB": [0, 1, 0]}}, FECHA), ["P-KI67"])

    def test_regimen_lee_por_regimen_y_t_propio_por_regimen(self):
        """Pedido del módulo A: si tras el chequeo de GrandQC rige el otro régimen, se leen SU
        «high floor» (`umbral.por_regimen`) y SU T del vector propio (`T_propio_por_regimen`); si
        la lámina lleva vector propio y falta su T de ese régimen, no se mide."""
        propio = {"supera": True, "dab_propio": _unit([0.3, 0.6, 0.75]),
                  "T_propio": {c: {"T": 0.14, "banda": [0.10, 0.19], "p999": 0.09,
                                   "rige": "p99.9+0.05", "rotulos": [], "estado": "ok"}
                               for c in ("nucleo", "anillo")},
                  "T_propio_por_regimen": {"clasicas": {c: {
                      "T": 0.12, "banda": [0.10, 0.17], "p999": 0.07, "rige": "p99.9+0.05",
                      "rotulos": [], "estado": "ok"} for c in ("nucleo", "anillo")}}}
        # clásicas: el núcleo a 0,20 (no > 0,25), pero el régimen lleva «high floor» (su anillo
        # pasó de 0,25): lo dice `por_regimen`, no el T del compartimento
        c = contenido_sello(rige="grandqc", T=0.13, p999=0.08,
                            otros_regimenes={"clasicas": (0.20, 0.15)}, por_lamina={"P-RE": propio})
        c["umbral"]["por_regimen"] = {
            "grandqc": {"rotulos": [], "high_floor": False},
            "clasicas": {"rotulos": ["high floor"], "high_floor": True}}
        s = SL.sella(os.path.join(self.tmp, "reg", SL.FICHERO), c, FECHA)
        self.assertNotIn("high floor", s.umbral("P-KI67")["rotulos"])
        u = s.umbral("P-KI67", regimen="clasicas")
        self.assertEqual(u["T"], 0.20)
        self.assertIn("high floor", u["rotulos"])
        self.assertTrue(u["banda_obligatoria"])
        self.assertEqual(s.umbral("P-RE")["T"], 0.14)                     # el propio del que rige
        self.assertEqual(s.umbral("P-RE", regimen="clasicas")["T"], 0.12)  # el propio de clásicas
        del c["residuo"]["por_lamina"]["P-RE"]["T_propio_por_regimen"]
        s2 = SL.sella(os.path.join(self.tmp, "reg2", SL.FICHERO), c, FECHA)
        with self.assertRaises(SL.SelloInvalido):
            s2.umbral("P-RE", regimen="clasicas")

    def test_ventanilla_precongela_congela_verifica_y_guardia(self):
        """Ruta canónica ÚNICA (petición del coordinador, 2-oct): `laminillas_congela.main` por
        la ventanilla (BTP_VENTANILLA=1, cwd = SESION) escribe el sello en `SESION/congelacion.json`
        (antes en SESION/congelacion/, que `laminillas_sello` rechazaba). Extremo a extremo:
        precongela → congela (traza ya traducida por la ventanilla) → verifica → la guardia
        `celulas_diana` (segmenta) lo encuentra; el registro y las métricas lo aceptan y
        `laminillas_proc.ruta_sello` apunta al mismo fichero. Lector y segmentador sintéticos del
        módulo A (sin InstanSeg ni láminas reales)."""
        try:
            import _laminillas_piloto_a as F
            import laminillas_congela as K
        except Exception as e:                                  # noqa: BLE001
            self.skipTest("módulo A no importa aquí (%s)" % e)
        ses = os.path.realpath(tempfile.mkdtemp(prefix="piloto-b-ventanilla-"))
        previo, env = os.getcwd(), os.environ.get("BTP_VENTANILLA")
        try:
            os.chdir(ses)
            os.environ["BTP_VENTANILLA"] = "1"
            lector = F.LectorSint(F.tanda())
            self.assertEqual(K.main(["precongela"], lector=lector,
                                    segmentador=F.segmentador_stub), 0)
            self.assertTrue(os.path.isfile(os.path.join(ses, K.PRECONGELACION)))
            os.makedirs("trazas")
            os.replace(F.traza_memoria("trazas", "laminillas_congela", 6.1),
                       os.path.join("trazas", "laminillas_congela.tsv"))
            rc = K.main(["congela", "--fecha", FECHA, "--traza",
                         "laminillas_congela=trazas/laminillas_congela.tsv"], lector=lector,
                        px_kw=dict(n_teselas=20))
            self.assertEqual(rc, 0)
            ruta = os.path.join(ses, SL.FICHERO)
            self.assertTrue(os.path.isfile(ruta))
            self.assertFalse(os.path.exists(os.path.join(ses, "congelacion")))
            self.assertEqual(K.main(["verifica"]), 0)
            s = SL.exige(SL.FICHERO, ["P-KI67"], SL.SECCIONES_MODULO_B)   # relativa a SESION
            self.assertEqual(s.sha256, SL.carga(ruta).sha256)
            out = K.celulas_diana(lector, "P-KI67", ruta, segmentador=F.segmentador_stub)
            self.assertGreater(len(out["celulas"]), 100)
            self.assertEqual(MET.Contexto(SL.FICHERO, "P-KI67").cita()["sello_sha256"], s.sha256)
            self.assertEqual(R._params(ruta, "P-CK19", "P-KI67")[2].sha256, s.sha256)
            self.assertEqual(K.fuera_del_mapa(ses)["sello_sha256"], s.sha256)
            try:
                import laminillas_proc as P
            except Exception:                                   # noqa: BLE001
                P = None
            if P is not None and hasattr(P, "ruta_sello"):
                self.assertEqual(os.path.realpath(P.ruta_sello(ses)), ruta)
        finally:
            os.chdir(previo)
            if env is None:
                os.environ.pop("BTP_VENTANILLA", None)
            else:
                os.environ["BTP_VENTANILLA"] = env
            shutil.rmtree(ses, ignore_errors=True)

    def test_formato_igual_que_congela(self):
        s = sella(self.tmp)
        try:
            import laminillas_congela as CG
        except Exception as e:                                  # noqa: BLE001
            self.skipTest("laminillas_congela no importa aquí (%s)" % e)
        d = _carga(s.ruta)
        self.assertEqual(CG.sha256_de(d), d["sha256"])
        v = CG.verifica_sello(s.ruta)
        self.assertEqual(v["sha256"] if isinstance(v, dict) else v.sha256, s.sha256)
        self.assertEqual(set(SL.CAUSAS_RECONGELA), set(CG.CAUSAS_RECONGELA))


# ══ B. Registro ═══════════════════════════════════════════════════════════════════════════
def _err_um(M, esperada, punto_ref):
    """Error (µm) en `punto_ref` (coordenadas de la referencia) entre M y la verdadera."""
    pm = R._aplica(np.linalg.inv(esperada), [punto_ref])
    return float(np.linalg.norm(R._aplica(np.asarray(M), pm) - punto_ref))


class _LectorDoble:
    """Dos lectores sintéticos detrás de una sola API: `a` sirve sus láminas, `b` el resto."""

    def __init__(self, a, b):
        self.a, self.b = a, b

    def _de(self, nombre):
        return self.a if nombre in self.a._cortes else self.b

    def abre(self, nombre):
        return self._de(nombre).abre(nombre)

    def lee_region(self, lam, *a):
        return self._de(lam.nombre).lee_region(lam, *a)

    def zona_escaneada(self, lam):
        return self._de(lam.nombre).zona_escaneada(lam)

    def i0_local(self, lam):
        return self._de(lam.nombre).i0_local(lam)


class Registro(unittest.TestCase):
    """Serie: P-CK19 (referencia), P-KI67 girada 37°, P-HER2NEG en ESPEJO y girada −120°, P-HE
    (otro nivel del mismo bloque) girada 60°."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="piloto-b-reg-")
        cls.sello = sella(cls.tmp)
        cls.bloque, cortes, cls.L = S.serie(11, [
            dict(nombre="P-CK19", fase=0.0, ck19=True),
            dict(nombre="P-KI67", fase=0.25, angulo=37.0, ki67=0.25),
            dict(nombre="P-HER2NEG", fase=0.5, angulo=-120.0, espejo=True),
            dict(nombre="P-HE", fase=0.75, angulo=60.0)])
        cls.cortes = {c.nombre: c for c in cortes}
        cls.cent = {n: cls.L.centroides_l0(n) for n in cls.cortes}
        cls.imgs = {}
        # (ii)/(ii-bis) del piloto de una vez: KI67 y HER2NEG contra P-CK19 sobre el FC del piloto
        cls.serie = R.registra_serie(cls.L, cls.sello, ["P-KI67", "P-HER2NEG"],
                                     centroides=cls.cent, imagenes=cls.imgs, intentar_valis=False)
        cls.res_ki = cls.serie["resultados"]["P-KI67"]
        cls._res_neg = cls.serie["resultados"]["P-HER2NEG"]
        # (iii) P-HE por fragmento
        cls.he = R.registra_he(cls.sello, lector=cls.L, serie=cls.serie, centroides=cls.cent,
                               imagenes=cls.imgs)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def res_neg(cls):
        return cls._res_neg

    def esperada(self, movil, fija="P-CK19"):
        return self.cortes[fija].A @ self.cortes[movil].T

    def _fc(self, k=None):
        if k is None:
            k = max(range(len(self.res_ki.fcs)), key=lambda i: self.res_ki.fcs[i]["area_um2"])
        return self.res_ki.fcs[k]

    def _fc_mayor(self):
        return self._fc()

    def test_recupera_giro_y_traslacion(self):
        """Tolerancias: |Δángulo| ≤ 0,3°; error ≤ 5 µm en el centroide de cada FC (≈2,5 px a ×8);
        p90 del TRE (b) ≤ 5 µm. Verdad: 37°, sin espejo."""
        r = self.res_ki
        esp = self.esperada("P-KI67")
        self.assertFalse(r.global_["espejo"])
        self.assertTrue(r.global_["sift_aceptada"])
        self.assertAlmostEqual(r.global_["angulo_grados"], 37.0, delta=0.3)
        self.assertGreaterEqual(r.global_["n_inliers"], 30)
        self.assertGreater(r.global_["margen_inliers"], 5)     # la otra quiralidad, muy por detrás
        grandes = [f for f in r.fragmentos if not f["evaluacion"]["pequeno"]]
        self.assertGreaterEqual(len(grandes), 2)
        for f, fc in zip(r.fragmentos, r.fcs):
            c = np.array(fc["poligono_um"].centroid.coords[0])
            self.assertLess(_err_um(f["matriz_um"], esp, c), 5.0, f["id"])
        for f in grandes:
            self.assertTrue(f["pasa"], f["id"])
            self.assertEqual(f["metodo"], "sift")
            self.assertEqual(f["evaluacion"]["rige"], "b")
            self.assertLess(f["evaluacion"]["p90_um"], 5.0)
            self.assertAlmostEqual(R.angulo(np.asarray(f["matriz_um"])), 37.0, delta=0.3)
        _sin_nunca(self, r.resumen())
        json.dumps(r.resumen())                                  # serializable

    def test_quiralidad_espejo(self):
        """Verdad: espejo + −120°. Tolerancia: error ≤ 5 µm en el centroide del tejido."""
        r = self.res_neg()
        esp = self.esperada("P-HER2NEG")
        self.assertTrue(r.global_["espejo"])
        self.assertLess(np.linalg.det(np.asarray(r.global_["matriz_um"])[:2, :2]), 0)
        self.assertGreater(r.global_["n_inliers"], 5 * max(1, r.global_["alternativa_inliers"]))
        c = np.array(self.res_ki.fcs[0]["poligono_um"].centroid.coords[0])
        self.assertLess(_err_um(r.global_["matriz_um"], esp, c), 5.0)

    def test_serie_del_piloto(self):
        """(ii)/(ii-bis): FC del piloto (KI67∩CK19, rótulo exacto), KI67 y HER2NEG llevadas a
        él, ≥50 % del área verificada, triángulo KI67→CK19→HER2NEG cerrado POR FC (≤3°, ≤50 µm;
        con la verdad sintética, ≤0,3° y ≤5 µm) y orden estimado con distancias entre todos."""
        out = self.serie
        self.assertTrue(out["fcs"])
        self.assertTrue(all(f["rotulo"] == "pilot consensus (KI67∩CK19; HER2NEG and HER2 mapped "
                                          "onto it)" for f in out["fcs"]))
        self.assertTrue(out["ii"]["extiende"])
        self.assertGreater(out["ii"]["fraccion_area_verificada_ki67"], 0.8)
        ci = out["cierres"][0]
        self.assertTrue(ci["pasa"], ci)
        self.assertGreaterEqual(ci["n_fc_evaluados"], 2)        # por FC, no un punto global
        for x in ci["por_fc"]:
            if "delta_um" in x:
                self.assertLess(abs(x["delta_grados"]), 0.3)
                self.assertLess(x["delta_um"], 5.0)
        o = out["orden"]
        self.assertEqual(o["rotulo"], "estimated section order; spacing unknown")
        self.assertIn(o["orden"], (["P-CK19", "P-KI67", "P-HER2NEG"],
                                   ["P-HER2NEG", "P-KI67", "P-CK19"]))
        self.assertEqual(o["distancias_entre"]["P-KI67"]["P-HER2NEG"], 1)
        self.assertEqual(o["distancias_entre"]["P-HER2NEG"]["P-CK19"], 2)
        json.dumps(out["pares"])
        _sin_nunca(self, out["pares"])

    def test_inicializacion_por_eslabon1(self):
        """El eslabón 1 da ángulo y quiralidad (sin traslación): la matriz inicial cae a ≤50 µm
        de la verdad con cualquiera de los dos convenios de signo del ángulo, y filtra las
        putativas."""
        esp = self.esperada("P-KI67")
        c = np.array(self._fc().get("poligono_um").centroid.coords[0])
        ir_f, ir_m = self.imgs["P-CK19"], self.imgs["P-KI67"]
        for ang in (37.0, -37.0):
            Mi, iou = R.init_desde_eslabon1(ir_f, ir_m, ang, False)
            self.assertLess(_err_um(Mi, esp, c), 50.0)
            self.assertGreater(iou, 0.7)
        r = R.registra_par("P-CK19", "P-KI67", self.sello, lector=self.L, fcs=[self._fc()],
                           imagenes=self.imgs, init={"angulo_grados": 37.0, "espejo": False},
                           intentar_valis=False)
        self.assertTrue(r.global_["init"]["filtrado"])
        self.assertAlmostEqual(r.global_["angulo_grados"], 37.0, delta=0.3)

    def test_eslabon1_manda_sobre_un_sift_espurio(self):
        """Hallazgo «eslabón 1 anulado»: SIFT con 8 putativas ESPURIAS por quiralidad (sin
        monkeypatch de registro_global). El eslabón 1 dice «directa»: la espejo no es elegible y
        un RANSAC de 2 inliers no se acepta; la global es la del eslabón 1 y el FC se registra por
        la cadena de respaldo. Tolerancia: ≤10 µm en el centroide del FC. Sin eslabón 1, no hay
        global («registration not verified»)."""
        fc = self._fc()
        up = int(R.PARAMS_PARTIDA["sift_upsampling"])
        rng = np.random.default_rng(5)
        desc = rng.integers(0, 255, (8, 128)).astype(np.uint8)
        ir_f, ir_m = copy.copy(self.imgs["P-CK19"]), copy.copy(self.imgs["P-KI67"])
        ir_f.__dict__["_rasgos"] = {(False, up): R.Rasgos(rng.uniform(0, 3000, (8, 2)), desc)}
        ir_m.__dict__["_rasgos"] = {
            (False, up): R.Rasgos(rng.uniform(0, 3000, (8, 2)), desc.copy()),
            (True, up): R.Rasgos(rng.uniform(-3000, 0, (8, 2)), desc.copy())}
        imgs = {"P-CK19": ir_f, "P-KI67": ir_m}
        r = R.registra_par("P-CK19", "P-KI67", self.sello, lector=self.L, fcs=[fc],
                           imagenes=imgs, init={"angulo_grados": 37.0, "espejo": False},
                           intentar_valis=False)
        self.assertEqual(r.global_["origen"], "eslabon1")
        self.assertFalse(r.global_["espejo"])
        self.assertFalse(r.global_["sift_aceptada"])
        f = r.fragmentos[0]
        self.assertTrue(f["pasa"], f["intentos"])
        self.assertNotEqual(f["metodo"], "sift")
        self.assertLess(_err_um(f["matriz_um"], self.esperada("P-KI67"),
                                np.array(fc["poligono_um"].centroid.coords[0])), 10.0)
        sin = R.registra_par("P-CK19", "P-KI67", self.sello, lector=self.L, fcs=[fc],
                             imagenes=dict(imgs), intentar_valis=False)
        self.assertIsNone(sin.matriz())
        self.assertIn("registration not verified", sin.rotulos)

    def test_serie_acepta_init_y_no_aborta_sin_global(self):
        """Hallazgo «registra_serie»: acepta `inits` y, si P-KI67 no tiene global, devuelve la
        rama «<50 %» en vez de lanzar."""
        import inspect
        self.assertIn("inits", inspect.signature(R.registra_serie).parameters)
        real = R.global_con_init

        def sin_ki67(ir_f, ir_m, p, init=None):
            g, el, ini = real(ir_f, ir_m, p, init)
            if ir_m.nombre == "P-KI67":
                el = dict(el, matriz_um=None)
            return g, el, ini
        R.global_con_init = sin_ki67
        try:
            out = R.registra_serie(self.L, self.sello, ["P-KI67", "P-HER2NEG"],
                                   imagenes=self.imgs, intentar_valis=False,
                                   inits={"P-KI67": {"angulo_grados": 37.0, "espejo": False}})
        finally:
            R.global_con_init = real
        self.assertFalse(out["ii"]["extiende"])
        self.assertIn("<50 %", out["ii"]["nota"])
        self.assertEqual(out["sin_global"], ["P-KI67"])

    def test_fc_de_f3_con_una_global_que_falta(self):
        """F3: ≥6 de 11 máscaras; la lámina sin global cuenta como 0 (no aborta)."""
        p = dict(R.PARAMS_PARTIDA)
        ms = []
        for k in range(8):                                      # 3 de las 11 sin global
            m = np.zeros((200, 300), bool)
            m[20:120, 10 + 5 * k:110 + 5 * k] = True
            ms.append(m)
        fcs, decl = R.fcs_serie(ms[0], ms[1:], 11, 2.0, p, ["P-A", "P-B", "P-C"])
        # columna c con ≥6 votos de los 8 desplazamientos (k de 0..7): 35 ≤ c ≤ 119
        self.assertEqual(int(fcs[0]["mascara"].sum()), 85 * 100)
        self.assertTrue(decl and "counted as 0" in decl[0])
        with self.assertRaises(R.RegistroError):
            R.fcs_serie(ms[0], ms[1:], 9, 2.0, p)

    def test_cierre_sin_global_no_rompe(self):
        """Hallazgo «cierre»: una pareja sin global da «sin global», no LinAlgError."""
        class Falso:
            def matriz(self, fc_id=None):
                return None
        ki = self.res_ki
        out = R.cierres_serie((("P-KI67", "P-CK19", "P-HER2NEG"),),
                              {"P-KI67": ki, "P-HER2NEG": Falso()}, ki.fcs, self.imgs, "P-CK19",
                              self.sello, self.L, None, None, ki.p)
        self.assertEqual(out[0]["motivo"], "sin global")
        self.assertFalse(out[0]["pasa"])

    def test_tre_mide_desplazamientos_conocidos(self):
        """Se desplaza la transformada buena 30 y 80 µm (el 0 lo mira el test principal): el p90
        de (b) debe dar ese número (±3 µm) y la puerta (TRE < 50 µm) abrir con 30 y cerrar con 80."""
        r = self.res_ki
        ir_f, ir_m = self.imgs["P-CK19"], self.imgs["P-KI67"]
        k = max(range(len(r.fcs)), key=lambda i: r.fcs[i]["area_um2"])
        fc, M = r.fcs[k], np.asarray(r.fragmentos[k]["matriz_um"])
        p, vect, _, _ = R._params(self.sello, "P-CK19", "P-KI67")
        for d, pasa in ((30.0, True), (80.0, False)):
            Md = np.array([[1, 0, d * 0.6], [0, 1, d * 0.8], [0, 0, 1.0]]) @ M
            ev = R.evalua_fc(self.L, ir_f, ir_m, Md, fc, p, vect)
            self.assertEqual(ev["rige"], "b")
            self.assertAlmostEqual(ev["p90_um"], d, delta=3.0, msg="desplazamiento %g" % d)
            self.assertEqual(ev["pasa"], pasa, "desplazamiento %g" % d)
            if not pasa:
                self.assertIn("registration not verified", ev["rotulos"])

    def test_fragmentos_pequenos(self):
        """El de ~0,55×0,75 mm no da 15 ventanas: hereda la global y se verifica con sus picos;
        el de ~0,25 mm no llega a 5: «not independently verified». Hallazgo «pequeño que falla»:
        el pequeño COMPROBADO (≥5 picos) con la transformada desplazada 80 µm lleva «registration
        not verified», no «not independently verified»."""
        peq = [(f, fc) for f, fc in zip(self.res_ki.fragmentos, self.res_ki.fcs)
               if f["evaluacion"]["pequeno"]]
        self.assertGreaterEqual(len(peq), 2)
        rot = [f["evaluacion"]["rotulos"] for f, _ in peq]
        verif = [x for x in rot if any(s.startswith("small fragment: registration verified on ")
                                        for s in x)]
        nover = [x for x in rot if "small fragment: registration not independently verified" in x]
        self.assertTrue(verif and nover, rot)
        for f, fc in peq:
            self.assertEqual(f["metodo"], "global (small fragment)")
            ev = f["evaluacion"]
            if ev["pasa"]:
                self.assertGreaterEqual(ev["n_verificadas"], 5)
                self.assertLess(ev["max_um"], 50)
                self.assertIn("small fragment: registration verified on %d windows"
                              % ev["n_verificadas"], ev["rotulos"])
        f, fc = next((f, fc) for f, fc in peq if f["pasa"])
        p, vect, _, _ = R._params(self.sello, "P-CK19", "P-KI67")
        Md = np.array([[1, 0, 48.0], [0, 1, 64.0], [0, 0, 1.0]]) @ np.asarray(f["matriz_um"])
        ev = R.evalua_fc(self.L, self.imgs["P-CK19"], self.imgs["P-KI67"], Md, fc, p, vect)
        self.assertGreaterEqual(ev["n_verificadas"], 5)
        self.assertAlmostEqual(ev["max_um"], 80.0, delta=5.0)
        self.assertFalse(ev["pasa"])
        self.assertIn("registration not verified", ev["rotulos"])
        self.assertNotIn("small fragment: registration not independently verified", ev["rotulos"])

    def test_cierre_triangulo(self):
        """KI67→CK19→HER2NEG frente a KI67→HER2NEG directo: ≤3° y ≤50 µm (plan); con la verdad
        sintética, ≤0,3° y ≤5 µm. Una transformada girada 5° no cierra."""
        directo = R.registra_par("P-HER2NEG", "P-KI67", self.sello, lector=self.L, fcs=[],
                                 imagenes=self.imgs, intentar_valis=False)
        p, _, _, _ = R._params(self.sello, "P-CK19", "P-KI67")
        M_ab = np.asarray(self.res_ki.global_["matriz_um"])            # KI67 → CK19
        M_bc = np.linalg.inv(np.asarray(self.res_neg().global_["matriz_um"]))  # CK19 → HER2NEG
        M_ac = np.asarray(directo.global_["matriz_um"])                # KI67 → HER2NEG
        punto = R._aplica(np.linalg.inv(M_ab), [self.res_ki.fcs[0]["poligono_um"].centroid.coords[0]])[0]
        c = R.cierre(M_ab, M_bc, M_ac, punto, p)
        self.assertTrue(c["pasa"], c)
        self.assertLess(abs(c["delta_grados"]), 0.3)
        self.assertLess(c["delta_um"], 5.0)
        malo = R._rot_alrededor(5.0, *R._aplica(M_ac, [punto])[0]) @ M_ac
        self.assertFalse(R.cierre(M_ab, M_bc, malo, punto, p)["pasa"])

    def _sello_sin_sift(self):
        """Mismo sello pero con min_inliers inalcanzable (coherente en las dos secciones):
        obliga a la cadena de respaldo."""
        mb = MET.contenido_partida()
        mb["registro"]["min_inliers"] = 10 ** 6
        REG, _, _, _ = _constantes_congela()
        reg = json.loads(json.dumps(REG))
        reg["min_inliers_fc"] = 10 ** 6
        return sella(self.tmp, "sin_sift_%d" % np.random.randint(1 << 30), modulo_b=mb,
                     registro=reg)

    def test_respaldo_correlacion_de_fase(self):
        """Sin SIFT por FC: correlación de fase sobre la ODsum. Tolerancia: ≤10 µm, ≤0,5°."""
        s = self._sello_sin_sift()
        fc = self._fc_mayor()
        r = R.registra_par("P-CK19", "P-KI67", s, lector=self.L, centroides=self.cent, fcs=[fc],
                           imagenes=self.imgs, intentar_valis=False)
        f = r.fragmentos[0]
        self.assertEqual(f["metodo"], "phase correlation (ODsum)")
        self.assertTrue(f["pasa"])
        self.assertIn(R.METODOS["eleccion"], f["declaraciones"])
        self.assertEqual(f["intentos"][0]["metodo"], "sift")
        c = np.array(fc["poligono_um"].centroid.coords[0])
        self.assertLess(_err_um(f["matriz_um"], self.esperada("P-KI67"), c), 10.0)
        self.assertAlmostEqual(R.angulo(np.asarray(f["matriz_um"])), 37.0, delta=0.5)

    def test_sift_con_30_inliers_no_lanza_respaldos(self):
        """Hallazgo «respaldos con ≥30 inliers»: si el SIFT del FC tiene ≥30 inliers y su TRE no
        pasa, no se prueban fase, densidad ni VALIS (plan: solo con <30)."""
        real = R.tre_hematoxilina
        R.tre_hematoxilina = lambda *a, **k: []                 # (b) sin picos: no pasa
        try:
            r = R.registra_par("P-CK19", "P-KI67", self.sello, lector=self.L, fcs=[self._fc()],
                               imagenes=self.imgs, intentar_valis=True,
                               ejecutor_valis=lambda cmd: self.fail("VALIS no debía lanzarse"))
        finally:
            R.tre_hematoxilina = real
        f = r.fragmentos[0]
        self.assertEqual([i["metodo"] for i in f["intentos"]], ["sift"])
        self.assertFalse(f["pasa"])
        self.assertIn("registration not verified", f["evaluacion"]["rotulos"])

    def test_respaldo_mapa_de_densidad(self):
        """Si la fase sobre ODsum no da nada, el mapa de densidad nuclear; entonces (b') deja de
        ser independiente: rige solo (b) y se DECLARA (Métodos, no rótulo)."""
        s = self._sello_sin_sift()
        fc = self._fc_mayor()
        real = R.refina_correlacion
        llamadas = []

        def falla_la_primera(*a, **k):
            llamadas.append(1)
            return (None, None) if len(llamadas) == 1 else real(*a, **k)
        R.refina_correlacion = falla_la_primera
        try:
            r = R.registra_par("P-CK19", "P-KI67", s, lector=self.L, centroides=self.cent,
                               fcs=[fc], imagenes=self.imgs, intentar_valis=False)
        finally:
            R.refina_correlacion = real
        f = r.fragmentos[0]
        self.assertEqual(f["metodo"], "nuclear density map")
        self.assertTrue(f["pasa"])
        self.assertEqual(f["evaluacion"]["rige"], "b")
        self.assertIn(R.METODOS["densidad"], f["evaluacion"]["declaraciones"])
        self.assertFalse(any("nuclear-density" in x for x in f["evaluacion"]["rotulos"]))
        c = np.array(fc["poligono_um"].centroid.coords[0])
        self.assertLess(_err_um(f["matriz_um"], self.esperada("P-KI67"), c), 10.0)

    def test_densidad_sin_b_queda_pendiente_de_la_comprobacion_a_ciegas(self):
        """Hallazgo «densidad y (b) cae»: el FC no se da por no verificado, queda PENDIENTE de la
        comprobación a ciegas (puerta), con sus recortes; ≤50 µm la pasa, >50 µm no
        («registration check inconclusive»)."""
        s = self._sello_sin_sift()
        fc = self._fc_mayor()
        real_c, real_b = R.refina_correlacion, R.tre_hematoxilina
        llamadas = []

        def falla_la_primera(*a, **k):
            llamadas.append(1)
            return (None, None) if len(llamadas) == 1 else real_c(*a, **k)
        R.refina_correlacion = falla_la_primera
        R.tre_hematoxilina = lambda *a, **k: [{"valido": False}] * 20   # (b) cae: DAB tapa la H
        try:
            r = R.registra_par("P-CK19", "P-KI67", s, lector=self.L, centroides=self.cent,
                               fcs=[fc], imagenes=self.imgs, intentar_valis=False)
        finally:
            R.refina_correlacion, R.tre_hematoxilina = real_c, real_b
        f = r.fragmentos[0]
        self.assertIsNone(f["pasa"])
        self.assertEqual(f["evaluacion"]["estado"], "pending: blind check as gate")
        self.assertTrue(f["evaluacion"]["recortes_comprobacion_ciegas"])
        self.assertEqual(r.pendientes(), [fc["id"]])
        self.assertEqual(r.fraccion_area_verificada(), 0.0)
        bueno, malo = copy.deepcopy(r), copy.deepcopy(r)
        self.assertTrue(bueno.resuelve_comprobacion_ciegas(fc["id"], 20.0)["pasa"])
        fm = malo.resuelve_comprobacion_ciegas(fc["id"], 80.0)
        self.assertFalse(fm["pasa"])
        self.assertIn("registration check inconclusive", fm["evaluacion"]["rotulos"])

    def _ejecutor_valis(self, M_um, llamadas=None):
        """Ejecutor falso con el convenio REAL de `Slide.M` en VALIS 1.2.0 (comprobado con VALIS
        de verdad en `test_laminillas_valis`): M = mapa INVERSO, del espacio registrado a los
        píxeles procesados de su imagen. Con la referencia = el espacio registrado (M_ref = I),
        M_móvil = inv(M en px). La línea de resultado va tras la marca, entre el ruido de VALIS."""
        def corre(cmd):
            self.assertEqual(cmd[0], R.VALIS_PY)
            if llamadas is not None:
                llamadas.append(cmd)
            P = self.imgs["P-CK19"].px_a_um()
            shp_f = list(self.imgs["P-CK19"].img.shape)
            shp_m = list(self.imgs["P-KI67"].img.shape)
            Mp = np.linalg.inv(np.linalg.inv(P) @ M_um @ P)
            out = {"fija": {"M": np.eye(3).tolist(), "processed_shape": shp_f,
                            "shape": shp_f[::-1]},
                   "movil": {"M": Mp.tolist(), "processed_shape": shp_m, "shape": shp_m[::-1]}}

            class Rr:
                returncode = 0
                stdout = "Converting images: 100%\n" + R.VALIS_MARCA + json.dumps(out) + "\nfin\n"
                stderr = ""
            return Rr()
        return corre

    def test_diagnostico_par_sintetico(self):
        """`registro-par --diagnostico` (solo números) sobre la serie sintética con verdad
        conocida (P-KI67 girada 37°): las putativas de ratio 0,8 son EXACTAMENTE las de la cadena
        (`putativas`); sin filtro, RANSAC sellado da ≥30 inliers y casi todas son coherentes (<25
        µm) con la verdad (aquí el VALIS falso devuelve la verdad); la TRE (b) con la verdad da
        ≥15 picos válidos y p90 ≤5 µm; la fija contra sí misma, p90 0 (máximo 1 px de ×4: la
        ventana de la móvil empieza en un píxel L0 entero, con desfase sub-píxel); y el resultado
        se serializa a JSON."""
        p, vect, sv, _pre = R._params(self.sello, "P-CK19", "P-KI67")
        ir_f = R.ImagenRegistro(self.L, "P-CK19", p, R.vectores_de("P-CK19", sv), retiene=True)
        ir_m = R.ImagenRegistro(self.L, "P-KI67", p, R.vectores_de("P-KI67", sv), retiene=True)
        self.assertTrue(np.array_equal(ir_f.img, self.imgs["P-CK19"].img))   # retiene no cambia
        esp = self.esperada("P-KI67")
        rf, rm = R.rasgos_sift(ir_f, p), R.rasgos_sift(ir_m, p, False)
        a = R.putativas(rf, rm, p)
        a9 = R.putativas(rf, rm, dict(p, ratio=0.9))
        bloque = R.DIAG_BLOQUE_BYTES
        try:          # por bloques de 7 filas: lo mismo que la matriz entera (desempates incluidos)
            R.DIAG_BLOQUE_BYTES = 8 * len(rm.xy) * 7
            b = R.putativas_ratios(rf, rm, p, (0.8, 0.9, 1.0))
        finally:
            R.DIAG_BLOQUE_BYTES = bloque
        self.assertGreater(len(rf.xy), 7 * 3)
        for x, y in ((a, b[0.8]), (a9, b[0.9])):
            self.assertTrue(np.array_equal(x[0], y[0]) and np.array_equal(x[1], y[1]))
        self.assertGreaterEqual(len(b[0.9][0]), len(b[0.8][0]))
        # desempates: descriptores repetidos en la fija y en la móvil
        rep_f = R.Rasgos(np.r_[rf.xy, rf.xy[:5]], np.r_[rf.desc, rf.desc[:5]])
        rep_m = R.Rasgos(np.r_[rm.xy, rm.xy[:5]], np.r_[rm.desc, rm.desc[:5]])
        try:
            R.DIAG_BLOQUE_BYTES = 8 * len(rep_m.xy) * 3
            c = R.putativas_ratios(rep_f, rep_m, p, (0.8, 1.0))
        finally:
            R.DIAG_BLOQUE_BYTES = bloque
        for r in (0.8, 1.0):
            x = R.putativas(rep_f, rep_m, dict(p, ratio=r))
            self.assertTrue(np.array_equal(x[0], c[r][0]) and np.array_equal(x[1], c[r][1]), r)
        fc = self._fc_mayor()
        cent_f = self.cent["P-CK19"] * ir_f.mpp_l0
        logs = []
        d = R.diagnostico_par(self.L, ir_f, ir_m, p, vect, [fc], init=None, cent_f=cent_f,
                              cent_m_l0=self.cent["P-KI67"],
                              ejecutor_valis=self._ejecutor_valis(esp), log=logs.append)
        json.dumps(d)
        s = d["sellado"]["directa"]["ratio_0.8"]
        self.assertGreaterEqual(s["ransac_inliers"], 30)
        self.assertGreaterEqual(s["coherentes_valis"]["25"], 0.8 * s["n_putativas"])
        self.assertLess(max(s["ransac_vs_valis_um"]), 5.0)
        self.assertTrue(d["global_cadena"]["aceptada"])
        self.assertTrue(logs)
        self.assertAlmostEqual(d["valis_directa"]["escala"], 1.0, places=6)
        fila = d["por_fc"][0]
        tre_v = fila["tre"]["valis_euclidea_fc"]["b"]
        self.assertGreaterEqual(tre_v["n_validas"], 15)
        self.assertLess(tre_v["p90_um"], 5.0)
        idem = fila["tre"]["identidad_fija_contra_si_misma"]
        self.assertEqual(idem["b"]["n_validas"], idem["b"]["n_ventanas"])
        self.assertEqual(idem["b"]["p90_um"], 0.0)
        self.assertLessEqual(idem["b"]["max_um"], 1.0)      # 1 px de ×4: el origen L0 entero
        self.assertEqual(idem["b_prima"]["max_um"], 0.0)
        for v in R.DIAG_VARIANTES:
            self.assertIn(v, d["variantes"])
        self.assertGreater(d["variantes"]["x32"]["directa"]["n_rasgos_movil"], 0)

    def test_valis_una_vez_por_pareja(self):
        """VALIS registra las imágenes ×8 ENTERAS: con dos FC que llegan al paso 4, el
        subproceso se lanza UNA vez y los dos FC usan su transformada (antes, una por FC: horas
        de RAM con las mismas imágenes)."""
        s = self._sello_sin_sift()
        fcs = sorted(self.res_ki.fcs, key=lambda f: -f["area_um2"])[:2]
        self.assertEqual(len(fcs), 2)
        llamadas = []
        real = R.refina_correlacion
        R.refina_correlacion = lambda *a, **k: (None, None)
        try:
            r = R.registra_par("P-CK19", "P-KI67", s, lector=self.L, fcs=fcs, imagenes=self.imgs,
                               ejecutor_valis=self._ejecutor_valis(self.esperada("P-KI67"),
                                                                   llamadas))
        finally:
            R.refina_correlacion = real
        self.assertEqual(len(llamadas), 1)
        for f in r.fragmentos:
            v = [i for i in f["intentos"] if i["metodo"] == "VALIS"]
            self.assertEqual(len(v), 1, f["id"])
            self.assertTrue(v[0]["corrio"])
        self.assertTrue(r.valis["corrio"])
        self.assertTrue(r.resumen()["valis"]["corrio"])

    def test_valis_que_no_corre_queda_con_su_motivo(self):
        """Un subproceso VALIS con rc ≠ 0 no es «sin resultado» a secas: el intento dice
        «VALIS did not run: rc=…» con la cola de stderr SANEADA (sin la carpeta temporal ni HOME,
        sin códigos ANSI), `corrio` False, y el par lo guarda en `valis`."""
        s = self._sello_sin_sift()
        fc = self._fc_mayor()
        casa = os.path.expanduser("~")

        def falla(cmd):
            tmp = os.path.dirname(cmd[3])                      # .../tmpXXXX/src → .../tmpXXXX

            class Rr:
                returncode = 1
                stdout = "\x1b[31mprogress\x1b[0m\n"
                stderr = ('Traceback (most recent call last):\n  File "%s/dst/x.py", line 1\n'
                          '  File "%s/.polaris-venvs/valis/json/encoder.py"\n'
                          "TypeError: Object of type int64 is not JSON serializable\n"
                          % (tmp, casa))
            return Rr()
        real = R.refina_correlacion
        R.refina_correlacion = lambda *a, **k: (None, None)
        try:
            r = R.registra_par("P-CK19", "P-KI67", s, lector=self.L, fcs=[fc], imagenes=self.imgs,
                               ejecutor_valis=falla)
        finally:
            R.refina_correlacion = real
        v = [i for i in r.fragmentos[0]["intentos"] if i["metodo"] == "VALIS"][0]
        self.assertFalse(v["pasa"])
        self.assertFalse(v["corrio"])
        self.assertTrue(v["motivo"].startswith("VALIS did not run: rc=1; "), v["motivo"])
        self.assertIn("int64 is not JSON serializable", v["motivo"])
        self.assertNotIn(casa, v["motivo"])
        self.assertNotIn("\x1b", v["motivo"])
        self.assertIn("<tmp>/dst/x.py", v["motivo"])
        self.assertFalse(r.valis["corrio"])
        self.assertEqual(r.valis["rc"], 1)
        self.assertFalse(r.fragmentos[0]["pasa"])

    def test_valis_ultimo_recurso_y_puerta(self):
        """VALIS solo entra si fallan los anteriores, por subproceso al venv `valis` (aquí un
        ejecutor falso: el convenio real de VALIS no se ejercita). Lo que devuelva pasa la MISMA
        puerta TRE: una matriz buena pasa, una mala no."""
        s = self._sello_sin_sift()
        fc = self._fc_mayor()
        esp = self.esperada("P-KI67")
        real = R.refina_correlacion
        R.refina_correlacion = lambda *a, **k: (None, None)
        try:
            r = R.registra_par("P-CK19", "P-KI67", s, lector=self.L, fcs=[fc], imagenes=self.imgs,
                               ejecutor_valis=self._ejecutor_valis(esp))
            mal = R.registra_par("P-CK19", "P-KI67", s, lector=self.L, fcs=[fc],
                                 imagenes=self.imgs,
                                 ejecutor_valis=self._ejecutor_valis(
                                     R._rot_alrededor(8.0, 1000, 1000) @ esp))
        finally:
            R.refina_correlacion = real
        self.assertEqual(r.fragmentos[0]["metodo"], "VALIS")
        self.assertTrue(r.fragmentos[0]["pasa"])
        self.assertFalse(mal.fragmentos[0]["pasa"])
        self.assertIn("registration not verified", mal.fragmentos[0]["evaluacion"]["rotulos"])

    def test_valis_no_euclideo_se_rechaza(self):
        """Hallazgo «VALIS con escala ≠ 1»: una similitud de escala 1,03 o 1,015 (alrededor del
        centroide del FC: pasaría la TRE) se rechaza por no euclídea; una de 1,005 se PROYECTA a
        euclídea (det = +1 exacto) y pasa."""
        s = self._sello_sin_sift()
        fc = self._fc_mayor()
        esp = self.esperada("P-KI67")
        cx, cy = fc["poligono_um"].centroid.coords[0]
        real = R.refina_correlacion
        R.refina_correlacion = lambda *a, **k: (None, None)
        res = {}
        try:
            for e in (1.03, 1.015, 1.005):
                Sm = np.array([[e, 0, cx * (1 - e)], [0, e, cy * (1 - e)], [0, 0, 1.0]])
                res[e] = R.registra_par("P-CK19", "P-KI67", s, lector=self.L, fcs=[fc],
                                        imagenes=self.imgs,
                                        ejecutor_valis=self._ejecutor_valis(Sm @ esp))
        finally:
            R.refina_correlacion = real
        for e in (1.03, 1.015):
            f = res[e].fragmentos[0]
            self.assertFalse(f["pasa"], e)
            v = [i for i in f["intentos"] if i["metodo"] == "VALIS"][0]
            self.assertIn("not Euclidean", v["motivo"])
            self.assertAlmostEqual(v["escala"], e, delta=1e-6)
        f = res[1.005].fragmentos[0]
        self.assertTrue(f["pasa"])
        self.assertAlmostEqual(abs(np.linalg.det(np.asarray(f["matriz_um"])[:2, :2])), 1.0,
                               places=9)
        self.assertLess(_err_um(f["matriz_um"], esp, np.array([cx, cy])), 1.0)

    def test_he_puerta_iii_por_fragmento(self):
        """(iii) P-HE del mismo bloque (otro nivel, 60°): pasa al menos un FC; cada uno que pasa
        tiene, en CK19↔HE y KI67↔HE, ≥30 inliers y ≥2× la mejor alternativa (espejo u otro
        ángulo), TRE de F3 y cierre KI67→CK19→HE vs KI67→HE ≤3°/≤50 µm (verdad: ≤0,5°, ≤5 µm).
        Tolerancia: transformada CK19↔HE del FC a ≤5 µm de la verdad en su centroide."""
        he = self.he
        self.assertTrue(he["pasa_algun_fc"], he["por_fc"])
        self.assertEqual(he["rotulos"], [])
        for x in he["por_fc"]:
            if not x["pasa"]:
                self.assertTrue(x["motivos"])
                continue
            for lado in ("ck19_he", "ki67_he"):
                self.assertGreaterEqual(x[lado]["inliers"], 30)
                self.assertGreaterEqual(x[lado]["inliers"], 2 * max(1, x[lado]["mejor_alternativa"]))
                self.assertTrue(x[lado]["tre_pasa"])
            self.assertLess(abs(x["cierre"]["delta_grados"]), 0.5)
            self.assertLess(x["cierre"]["delta_um"], 5.0)
            fc = [f for f in self.res_ki.fcs if f["id"] == x["fc"]][0]
            M = he["resultados"]["P-CK19"].matriz(x["fc"])
            self.assertLess(_err_um(M, self.esperada("P-HE"),
                                    np.array(fc["poligono_um"].centroid.coords[0])), 5.0)
        _sin_nunca(self, {k: v for k, v in he.items() if k != "resultados"})

    def test_he_de_otro_bloque_no_pasa_y_he_nunca_es_referencia(self):
        """Una P-HE de OTRO bloque: ningún fragmento pasa (iii) y sale el rótulo de F1.3. P-HE
        como referencia: error."""
        _, otros, L2 = S.serie(99, [dict(nombre="P-HE", fase=0.0, angulo=15.0)])
        lector = _LectorDoble(L2, self.L)
        imgs = {k: v for k, v in self.imgs.items() if k != "P-HE"}
        he = R.registra_he(self.sello, lector=lector, serie=self.serie, imagenes=imgs)
        self.assertFalse(he["pasa_algun_fc"])
        self.assertEqual(he["rotulos"], [R.ROTULOS["he_no_registrada"]])
        with self.assertRaises(R.RegistroError):
            R._params(self.sello, "P-HE", "P-CK19")
        with self.assertRaises(R.RegistroError):
            R.registra_serie(self.L, self.sello, ["P-KI67", "P-HE"], imagenes=self.imgs)

    def test_tejido_ajeno_no_se_verifica(self):
        """Un corte de OTRO bloque: ningún FC pasa la puerta; «registration not verified»."""
        _, otros, L2 = S.serie(99, [dict(nombre="P-SYN", fase=0.0, angulo=15.0)])
        lector = _LectorDoble(self.L, L2)
        r = R.registra_par("P-CK19", "P-SYN", self.sello, lector=lector, imagenes=self.imgs,
                           fcs=[self._fc_mayor()], intentar_valis=False)
        if r.global_.get("matriz_um") is None:
            self.assertIn("registration not verified", r.rotulos)
            return
        self.assertTrue(r.fragmentos)
        for f in r.fragmentos:
            self.assertFalse(f["pasa"], f["id"])
        self.assertEqual(r.fraccion_area_verificada(), 0.0)

    def test_relleno_pintado_y_memoria(self):
        """La zona no escaneada (relleno 255) se pinta del vidrio local: |ODsum| < 0,01 ahí y fuera
        de la máscara de tejido. Hallazgo «memoria»: la imagen se guarda en float32 y la ODsum no
        se retiene."""
        ir = self.imgs["P-KI67"]
        fuera = ~ir.zona
        self.assertGreater(fuera.sum(), 1000)
        self.assertLess(ir.odsum_fuera_max, 0.01)
        self.assertFalse(ir.tejido[fuera].any())
        self.assertEqual(ir.img.dtype, np.float32)
        self.assertFalse(hasattr(ir, "odsum"))

    def test_mascara_tejido_con_varianza_de_hematoxilina(self):
        """Hallazgo «máscara»: a ~4 µm/px, un tejido PÁLIDO (ODsum 0,02, bajo la histéresis) con
        textura nuclear de hematoxilina (desviación ≈0,06 OD) entra por el término de
        hematoxilina; el vidrio (ruido 0,002) no. Sin ese término, el pálido no entraba."""
        p = dict(R.PARAMS_PARTIDA)
        rng = np.random.default_rng(3)
        h, w = 400, 600
        odsum = np.abs(rng.normal(0, 0.002, (h, w))).astype(np.float32)
        hema = np.abs(rng.normal(0, 0.002, (h, w))).astype(np.float32)
        odsum[100:300, 100:300] = 0.02                          # pálido
        nuc = np.zeros((h, w), np.float32)
        nuc[100:300:5, 100:300:5] = 1.0
        hema[100:300, 100:300] += 0.6 * nuc[100:300, 100:300]
        m = R.mascara_tejido(odsum, 2.0, p, hema)
        self.assertEqual(m.shape, (h, w))
        self.assertGreater(m[110:290, 110:290].mean(), 0.95)
        self.assertLess(m[:, 400:].mean(), 0.01)
        self.assertLess(R.mascara_tejido(odsum, 2.0, p)[110:290, 110:290].mean(), 0.05)

    def test_fragmentos_de_consenso(self):
        """FC = píxel en ≥6 de 11 máscaras (área conocida); con otro nº de máscaras, explícito."""
        p = dict(R.PARAMS_PARTIDA)
        ms = []
        for k in range(11):
            m = np.zeros((200, 300), bool)
            m[20:120, 10 + 5 * k:110 + 5 * k] = True            # franjas desplazadas 5 px
            ms.append(m)
        fcs = R.fragmentos_consenso(ms, 2.0, p)
        self.assertEqual(len(fcs), 1)
        # columna c con ≥6 votos ⇔ 35 ≤ c ≤ 134 (k de 0-5 a 5-10): 100 columnas × 100 filas
        self.assertEqual(int(fcs[0]["mascara"].sum()), 100 * 100)
        with self.assertRaises(R.RegistroError):
            R.fragmentos_consenso(ms[:5], 2.0, p)
        piloto = R.fc_piloto(ms[0], ms[1], 2.0, p)
        self.assertEqual(piloto[0]["rotulo"],
                         "pilot consensus (KI67∩CK19; HER2NEG and HER2 mapped onto it)")

    def test_orden_de_los_cortes(self):
        """Cinco cortes de fases 0..1 (la serie física), con nombres barajados: la seriación de
        la similitud de densidad nuclear recupera el orden (o su inverso)."""
        nombres = ["P-RE", "P-CK19", "P-SYN", "P-KI67", "P-RA"]
        fases = [0.75, 0.0, 1.0, 0.25, 0.5]
        _, cortes, _ = S.serie(21, [dict(nombre=n, fase=f) for n, f in zip(nombres, fases)])
        ref = [c for c in cortes if c.nombre == "P-CK19"][0]
        cent = {c.nombre: R._aplica(ref.A @ c.T, c.nucleos) for c in cortes}
        mask = np.ones((int(3700 / 2), int(3000 / 2)), bool)
        p = dict(R.PARAMS_PARTIDA)
        nom, Smat = R.similitud_densidad(cent, mask, 2.0, p)
        o = R.orden_cortes(nom, Smat)
        verdad = [n for _, n in sorted(zip(fases, nombres))]
        self.assertIn(o["orden"], (verdad, verdad[::-1]))
        self.assertEqual(o["rotulo"], "estimated section order; spacing unknown")
        self.assertEqual(o["distancia_a_referencia"]["P-KI67"], 1)
        self.assertEqual(o["distancias_entre"]["P-RE"]["P-SYN"], 1)

    def test_orden_con_una_lamina_sin_nucleos_verificados(self):
        """Hallazgo «orden con NaN»: una lámina sin núcleos en FC verificados sale de la seriación
        y se declara; el resto se ordena completo (antes salía ['P-CK19'] solo)."""
        nombres = ["P-CK19", "P-HER2NEG", "P-KI67", "P-RE"]
        S_ = np.array([[1.0, np.nan, 0.8, 0.6], [np.nan, 1.0, np.nan, np.nan],
                       [0.8, np.nan, 1.0, 0.7], [0.6, np.nan, 0.7, 1.0]])
        o = R.orden_cortes(nombres, S_)
        self.assertEqual(sorted(o["orden"]), ["P-CK19", "P-KI67", "P-RE"])
        self.assertEqual(o["no_ordenadas"], ["P-HER2NEG"])
        self.assertTrue(any("not ordered: no verified nuclei" in d for d in o["declaraciones"]))
        self.assertIn(o["orden"], (["P-CK19", "P-KI67", "P-RE"], ["P-RE", "P-KI67", "P-CK19"]))

    def test_parametros_inferidos_declarados(self):
        """Hallazgo «parámetros inventados»: los que el plan no fija van marcados y a Métodos."""
        for k in ("contencion_min", "fc_min_area_um2", "exclusion_pico_um", "init_radio_um",
                  "fc_margen_um", "mascara_bajo", "mascara_alto", "sift_upsampling"):
            self.assertIn(k, R.INFERIDOS)
        self.assertIn("L_frac_area_mediana", MET.INFERIDOS)
        res = self.res_ki.resumen()
        self.assertIn("contencion_min", res["metodos"]["parametros_inferidos"])
        self.assertTrue(MET.metodos()["desviaciones"])

    def test_sin_lectura_propia_de_pixeles(self):
        """El código de medida no abre TIFF por su cuenta (lector único)."""
        for f in ("laminillas_registro.py", "laminillas_metricas.py", "laminillas_geojson.py"):
            with open(os.path.join(TOOLS, f), encoding="utf-8") as fh:
                src = fh.read()
            for prohibido in ("tifffile", "openslide", ".decode(", ".seek("):
                self.assertNotIn(prohibido, src, "%s usa %s" % (f, prohibido))


class InitEslabon1Cilindros(unittest.TestCase):
    """Hallazgo del 2-oct (piloto real, `registro-par P-KI67 P-CK19 --diagnostico`): la
    traslación del eslabón 1 se afinaba con correlación de FASE de las máscaras, y eso BAJÓ la IoU
    de los centroides (0,565 → 0,454) y dejó la global a 634-750 µm de VALIS: fuera del radio de
    500 µm del filtro de putativas y del margen de 100 µm del SIFT por FC. Reproducción sintética
    del mecanismo: cuatro cilindros de biopsia paralelos y parecidos (separación 220 µm), bordes
    distintos entre cortes y un trozo que falta en la móvil; la fase salta un cilindro. Con la
    traslación de MÁXIMA IoU (criterio del eslabón 1): error ≤ 25 µm (≈12 px a ×8) en los
    centroides de los cilindros con la verdad conocida, en las seis semillas."""

    MPP = 2.0

    class _IR:
        def __init__(self, tejido, mpp):
            self.tejido, self.mpp = tejido, mpp
            self.img = np.zeros(tejido.shape, np.float32)

        def px_a_um(self):
            return np.array([[self.mpp, 0, 0.5 * self.mpp], [0, self.mpp, 0.5 * self.mpp],
                             [0, 0, 1.0]])

    @staticmethod
    def _cilindros(shape, specs, rng):
        from scipy import ndimage as ndi
        yy, xx = np.mgrid[:shape[0], :shape[1]]
        m = np.zeros(shape, bool)
        for (cy, cx, largo, ancho, ang) in specs:
            a = np.radians(ang)
            u = (xx - cx) * np.cos(a) + (yy - cy) * np.sin(a)
            v = -(xx - cx) * np.sin(a) + (yy - cy) * np.cos(a)
            m |= (np.abs(u) <= largo / 2) & (np.abs(v) <= ancho / 2)
        n = ndi.gaussian_filter(rng.standard_normal(shape), 10)
        d = ndi.distance_transform_edt(m) - ndi.distance_transform_edt(~m)
        return d + 6.0 * n / n.std() > 0

    def _caso(self, semilla, sep=110, quita=0.35):
        rng = np.random.default_rng(semilla)
        H, W = 1000, 1400
        specs = [(300 + k * sep, 700 + rng.uniform(-60, 60), rng.uniform(700, 1000), 60, 3)
                 for k in range(4)]
        A = self._cilindros((H, W), specs, rng)
        specs_m = list(specs)
        cy, cx, lg, an, ag = specs_m[0]
        specs_m[0] = (cy, cx + lg * quita / 2, lg * (1 - quita), an, ag)
        B0 = self._cilindros((H, W), specs_m, rng)
        verdad = R._rot_alrededor(17.0, 700 * self.MPP, 500 * self.MPP) @ np.array(
            [[1, 0, 160.0], [0, 1, -120.0], [0, 0, 1]])
        ir_f = self._IR(A, self.MPP)
        ir_m = self._IR(R.warp_a_referencia(B0, self._IR(B0, self.MPP), ir_f,
                                            np.linalg.inv(verdad)), self.MPP)
        puntos = [np.array([700 * self.MPP, (300 + k * sep) * self.MPP]) for k in range(4)]
        return ir_f, ir_m, verdad, puntos

    def test_traslacion_de_maxima_iou(self):
        for semilla in range(6):
            ir_f, ir_m, verdad, puntos = self._caso(semilla)
            for ang in (17.0, -17.0):                      # cualquiera de los dos convenios
                M, iou = R.init_desde_eslabon1(ir_f, ir_m, ang, False)
                err = max(R.distancia_um(M, verdad, puntos))
                self.assertLess(err, 25.0, (semilla, ang, err))
                self.assertGreater(iou, 0.65, (semilla, ang))

    def test_traslacion_iou_signo_y_desempate(self):
        """`traslacion_iou`: el píxel q de B cae en q + d de A (d con signo, sin vuelta)."""
        A = np.zeros((40, 50), bool)
        A[10:20, 30:45] = True
        for d in ((-7, 12), (5, -20), (0, 0)):
            B = np.zeros((30, 70), bool)                  # cabe entero
            B[10 - d[0]:20 - d[0], 30 - d[1]:45 - d[1]] = True
            got, iou = R.traslacion_iou(A, B)
            self.assertEqual(got, d)
            self.assertAlmostEqual(iou, 1.0, places=5)


class ValisMppMovil(unittest.TestCase):
    """`respaldo_valis` convertía los píxeles de la MÓVIL a µm con el mpp de la FIJA. El ×8
    nativo de cada lámina sale de su pirámide (`mpp_l0 · W0/w`) y puede diferir: entonces la
    transformada tenía una escala espuria = mpp_móvil/mpp_fija. VALIS falso que dice «el píxel
    (i, j) de la móvil es el (i, j) de la fija» (identidad en píxeles): la transformada en µm
    tiene que ser la escala mpp_fija/mpp_móvil (directa y en espejo); con el código anterior
    salía la identidad."""

    def _ej(self, shp_f, shp_m):
        def corre(cmd):
            out = {"fija": {"M": np.eye(3).tolist(), "processed_shape": list(shp_f),
                            "shape": list(shp_f)[::-1]},
                   "movil": {"M": np.eye(3).tolist(), "processed_shape": list(shp_m),
                             "shape": list(shp_m)[::-1]}}

            class Rr:
                returncode = 0
                stdout = R.VALIS_MARCA + json.dumps(out) + "\n"
                stderr = ""
            return Rr()
        return corre

    def test_mpp_de_la_movil(self):
        mpp_f, mpp_m = 2.0044, 2.0300
        f = np.zeros((40, 60), np.float32)
        m = np.zeros((40, 60), np.float32)
        s = mpp_f / mpp_m
        for espejo in (False, True):
            M, info = R.respaldo_valis(f, m, mpp_f, self._ej(f.shape, m.shape), espejo=espejo,
                                       mpp_m=mpp_m)
            self.assertTrue(info["corrio"], info)
            esc = math.sqrt(abs(np.linalg.det(M[:2, :2])))
            self.assertAlmostEqual(esc, s, places=9)
            # un punto de la móvil (µm) cae donde dice VALIS: su mismo píxel en la fija
            px = np.array([17.0, 23.0])                       # (col, fila) del píxel de la móvil
            xm = (np.array([60 - 1 - px[0], px[1]]) if espejo else px) + 0.5
            esperado = (px + 0.5) * mpp_f
            self.assertLess(np.abs(R._aplica(M, [xm * mpp_m])[0] - esperado).max(), 1e-9)
        # sin mpp_m (mismo mpp), la identidad como siempre
        M, _ = R.respaldo_valis(f, m, mpp_f, self._ej(f.shape, m.shape))
        self.assertTrue(np.allclose(M, np.eye(3)))


class LectorReal(unittest.TestCase):
    """El registro a través del LECTOR ÚNICO real (OpenSlide sobre un BigTIFF sintético tipo
    Grundium, con zona escaneada e I0 de la ingesta en el manifiesto). Verdad: 23°, sin espejo.
    Tolerancias: |Δángulo| ≤ 0,3°; ≤ 5 µm en el centroide del fragmento; TRE p90 ≤ 5 µm."""

    def test_registro_con_el_lector_real(self):
        try:
            import openslide  # noqa: F401
            import tifffile  # noqa: F401
            import laminillas_lector as LEC
        except Exception as e:                              # noqa: BLE001
            self.skipTest("sin OpenSlide/tifffile/lector (%s)" % e)
        from shapely.geometry import LineString
        tmp = tempfile.mkdtemp(prefix="piloto-b-lector-")
        try:
            b = S.Bloque(5, ancho_um=1500, alto_um=1100, fragmentos=[
                LineString([(250, 450), (1250, 650)]).buffer(260)])
            _, cortes, Lsint = S.serie(5, [dict(nombre="P-CK19", fase=0.0, ck19=True),
                                           dict(nombre="P-KI67", fase=0.25, angulo=23.0, ki67=0.3)],
                                       bloque=b)
            S.escribe_sesion(tmp, cortes)
            LEC.configura(sesion=tmp)
            sello = sella(tmp)
            cent = {c.nombre: Lsint.centroides_l0(c.nombre) for c in cortes}
            r = R.registra_par("P-CK19", "P-KI67", sello, lector=LEC, centroides=cent,
                               intentar_valis=False)
            ref, mov = cortes
            esp = ref.A @ mov.T
            self.assertFalse(r.global_["espejo"])
            self.assertAlmostEqual(r.global_["angulo_grados"], 23.0, delta=0.3)
            self.assertTrue(r.fragmentos)
            f, fc = r.fragmentos[0], r.fcs[0]
            c = np.array(fc["poligono_um"].centroid.coords[0])
            self.assertLess(_err_um(f["matriz_um"], esp, c), 5.0)
            self.assertTrue(f["pasa"], f["evaluacion"])
            ev = f["evaluacion"]
            self.assertLess(ev["p90_um"] if ev["p90_um"] is not None else ev["max_um"], 5.0)
        finally:
            try:
                LEC.configura(None)
            except Exception:                               # noqa: BLE001
                pass
            shutil.rmtree(tmp, ignore_errors=True)


# ══ C. Métricas ═══════════════════════════════════════════════════════════════════════════
def _fcs():
    from shapely.geometry import box
    return {"FC1": box(0, 0, 3000, 1000), "FC2": box(0, 1400, 3000, 2400)}


def _nucleos(semilla, prob, densidad_mm2=3000.0, fcs=None, dab_pos=0.5, dab_neg=0.02,
             den_frac=1.0, densidad_fn=None, dab_sd=0.05, lamina="P-KI67"):
    """Núcleos Poisson en los FC; positivo con probabilidad prob(x, y). Devuelve Nucleos."""
    import shapely
    rng = np.random.default_rng(semilla)
    fcs = fcs or _fcs()
    xs, ys, ids = [], [], []
    for fid, pol in fcs.items():
        x0, y0, x1, y1 = pol.bounds
        n = rng.poisson(densidad_mm2 * (x1 - x0) * (y1 - y0) / 1e6)
        x, y = rng.uniform(x0, x1, n), rng.uniform(y0, y1, n)
        if densidad_fn is not None:
            keep = rng.random(n) < densidad_fn(x, y)
            x, y = x[keep], y[keep]
        ok = shapely.contains_xy(pol, x, y)
        xs.append(x[ok]); ys.append(y[ok]); ids += [fid] * int(ok.sum())
    x, y = np.concatenate(xs), np.concatenate(ys)
    pos = rng.random(len(x)) < prob(x, y)
    dab = np.where(pos, rng.normal(dab_pos, dab_sd, len(x)), rng.normal(dab_neg, 0.01, len(x)))
    den = rng.random(len(x)) < den_frac
    return MET.Nucleos(lamina, np.c_[x, y], dab, ids, {"ck19_erosionada": den,
                                                       "ck19_sin_erosionar": np.ones(len(x), bool),
                                                       "morfometrico": den})


def _expit(x):
    return 1 / (1 + np.exp(-x))


def _campo(rng, W, H, sigma, res=10.0):
    """Campo gaussiano estacionario de varianza marginal 1 (ruido blanco suavizado y
    normalizado por su varianza teórica, no por la realizada)."""
    from scipy import ndimage as ndi
    nx, ny = int(W / res) + 2, int(H / res) + 2
    s = sigma / res
    f = ndi.gaussian_filter(rng.standard_normal((ny, nx)), s, mode="wrap")
    return f / math.sqrt(1 / (4 * math.pi * s * s))


def _regiones_sinteticas(rng, reg, lamina, campo, a, s, dens=3000.0, res=10.0, T=0.10):
    """Regiones con n ~ Poisson(densidad·área) y k ~ Binomial(n, expit(a + s·campo(centro)))."""
    out = {}
    for c, r in reg.items():
        n = int(rng.poisson(dens * r["area_um2"] / 1e6))
        cx, cy = r["centro"]
        pr = _expit(a + s * campo[int(cy / res), int(cx / res)])
        k = int(rng.binomial(n, pr)) if n else 0
        out[c] = dict(r, n=n, k=k, n_todos=n, pct=100.0 * k / n if n else float("nan"),
                      densidad_mm2=dens, lamina=lamina, T=T, denominador="ck19_erosionada")
    return out


class Metricas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="piloto-b-met-")
        cls.sello = sella(cls.tmp)
        anota_iv(cls.sello, ["P-RE", "P-KI67", "P-SYN", "P-CHGA", "P-{{DIANA3}}"])
        cls.sello_banda = sella(cls.tmp, "banda", fp_banda={"T_bajo": 0.006, "T_alto": 0.002})
        cls.ctx = MET.Contexto(cls.sello, "P-KI67")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _regiones(self, nuc, L=200.0, ctx=None):
        reg = MET.rejilla(_fcs(), L)
        return MET.cuenta_regiones(nuc, reg, ctx or self.ctx, "ck19_erosionada")

    def test_clopper_pearson_del_plan(self):
        """Plan: con 0/50, el límite superior del 95 % es 7,1 %."""
        lo, hi = MET.clopper_pearson(0, 50)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.0711, delta=0.0005)

    def test_regla_de_L(self):
        """3000 núcleos/mm²: L=150 → ~67/región, L=200 → ~120. Con p90 = 20 µm, L = 200; con
        p90 = 120 µm (2×p90 = 240), L = 300; sin TRE, ninguno."""
        nuc = _nucleos(1, lambda x, y: np.full(len(x), 0.3))
        r = MET.elige_L(nuc, _fcs(), 20.0, self.ctx, "ck19_erosionada")
        self.assertEqual(r["L_um"], 200.0)
        self.assertAlmostEqual([t for t in r["tabla"] if t["L_um"] == 200][0]["mediana_nucleos"],
                               120, delta=12)
        self.assertEqual(MET.elige_L(nuc, _fcs(), 120.0, self.ctx, "ck19_erosionada")["L_um"], 300.0)
        self.assertIsNone(MET.elige_L(nuc, _fcs(), None, self.ctx, "ck19_erosionada")["L_um"])

    def test_porcentaje_global_bootstrap_y_sensibilidad(self):
        """Verdad 30 %. Tolerancia: |% − 30| ≤ 1,5 (n≈18.000, ET≈0,35) y el IC de bloques lo
        contiene. Rangos de sensibilidad con su origen y rótulos; SIEMPRE las tres cifras de
        denominador y el rótulo de CK19."""
        nuc = _nucleos(2, lambda x, y: np.full(len(x), 0.30))
        reg, info = MET.excluye_pequenas(self._regiones(nuc), self.ctx.p)
        g = MET.porcentaje_global(nuc, reg, self.ctx, "ck19_erosionada")
        self.assertAlmostEqual(g["pct"], 30.0, delta=1.5)
        lo, hi = g["intervalos"]["bloques"]
        self.assertTrue(lo < 30.0 < hi or abs(g["pct"] - 30) < 0.5, (lo, hi))
        self.assertLess(hi - lo, 4.0)
        self.assertGreaterEqual(g["ic_bloques"]["lado_bloque_um"], 1000.0)
        self.assertEqual(g["alcance"], "of the scanned region")
        self.assertEqual(set(g["pct_por_denominador"]), set(MET.DENOMINADORES))
        self.assertIn(MET.ROTULOS["denominador_ck19"], g["rotulos"])
        self.assertNotIn("dependiente de umbral", g["rotulos"])
        self.assertIn("threshold at fixed floor (0.10 OD); measured floor p99.9 = 0.040",
                      g["rotulos"])
        # DAB de los positivos pegado a T: la banda de T mueve el % > 10 puntos
        nuc2 = _nucleos(3, lambda x, y: np.full(len(x), 0.30), dab_pos=0.11)
        reg2, _ = MET.excluye_pequenas(self._regiones(nuc2), self.ctx.p)
        g2 = MET.porcentaje_global(nuc2, reg2, self.ctx, "ck19_erosionada")
        self.assertIn("dependiente de umbral", g2["rotulos"])
        self.assertEqual(g2["mas_ancho"]["origen"], "banda_T")
        # denominadores que difieren > 5 puntos
        nuc3 = _nucleos(4, lambda x, y: np.where(x < 1500, 0.6, 0.1))
        nuc3.den["morfometrico"] = nuc3.xy[:, 0] < 1500
        reg3, _ = MET.excluye_pequenas(self._regiones(nuc3), self.ctx.p)
        g3 = MET.porcentaje_global(nuc3, reg3, self.ctx, "ck19_erosionada",
                                   deteccion_ok=False, nuc_clasico=nuc)
        self.assertIn("denominator-dependent", g3["rotulos"])
        self.assertIn("detection-dependent", g3["rotulos"])
        self.assertIn("detector", g3["intervalos"])
        self.assertTrue(g3["primaria"].startswith("rango"))
        _sin_nunca(self, [g, g2, g3])

    def test_ic_global_cubre_con_dependencia_espacial(self):
        """Hallazgo «IC del % global»: positividad con un campo gaussiano (σ = 150 µm, varianza
        marginal 1, logit p = logit 0,3 + 0,8·campo) sobre 3 FC de 6×1 mm, L = 200 µm. Verdad =
        E[p] del proceso (Gauss-Hermite). 150 réplicas: cobertura del IC ≥ 0,90 (nominal 0,95;
        2 ET binomiales con 150 réplicas ≈ 0,036). Con remuestreo de regiones sueltas la
        cobertura simulada era 0,58."""
        from shapely.geometry import box
        fcs = {"FC%d" % i: box(0, 1400 * i, 6000, 1400 * i + 1000) for i in range(3)}
        reg0 = MET.rejilla(fcs, 200.0)
        a, s = math.log(0.3 / 0.7), 0.8
        x, w = np.polynomial.hermite_e.hermegauss(80)
        verdad = 100 * float(np.sum(w * _expit(a + s * x)) / np.sum(w))
        rng = np.random.default_rng(20261019)
        cubre, anchos = 0, []
        p = self.ctx.p
        N = 150
        for _ in range(N):
            campo = _campo(rng, 6100, 4000, 150.0)
            reg = MET.validas(_regiones_sinteticas(rng, reg0, "P-KI67", campo, a, s), p)
            ic = MET.ic_bloques(reg, p, np.random.default_rng(int(p["semilla_bootstrap"])))
            lo, hi = ic["intervalo"]
            cubre += lo <= verdad <= hi
            anchos.append(hi - lo)
        self.assertGreaterEqual(cubre / N, 0.90, "cobertura %.3f" % (cubre / N))
        self.assertLess(float(np.median(anchos)), 15.0)          # informativo, no infinito

    def test_exclusion_de_regiones_pobres_en_nucleos(self):
        """Mitad izquierda de FC1 a 600/mm² (≈24 núcleos por región de 200 µm): fuera; la
        fracción se informa. Verdad: 7 columnas × 5 filas de FC1 = 35 de 150 (la columna 7
        queda mitad densa: ~72 núcleos, dentro)."""
        dens = lambda x, y: np.where((x < 1500) & (y < 1000), 0.2, 1.0)  # noqa: E731
        nuc = _nucleos(5, lambda x, y: np.full(len(x), 0.3), densidad_fn=dens)
        reg, info = MET.excluye_pequenas(self._regiones(nuc), self.ctx.p)
        self.assertEqual(info["regiones_total"], 150)
        self.assertAlmostEqual(info["regiones_excluidas"], 35, delta=2)
        self.assertGreater(info["fraccion_nucleos_excluidos"], 0.03)
        self.assertTrue(all(r["n"] >= 50 for r in reg.values()))

    def test_sin_regiones_ni_nucleos_no_revienta(self):
        """Hallazgo «tabla vacía»: con todas las regiones excluidas, % NaN, n = 0 y declaración;
        una tabla de núcleos vacía no da TypeError."""
        nuc = _nucleos(30, lambda x, y: np.full(len(x), 0.3), densidad_mm2=300)
        reg, info = MET.excluye_pequenas(self._regiones(nuc), self.ctx.p)
        self.assertEqual(reg, {})
        g = MET.porcentaje_global(nuc, reg, self.ctx, "ck19_erosionada")
        self.assertTrue(math.isnan(g["pct"]))
        self.assertEqual(g["n"], 0)
        self.assertTrue(any("no region with" in d for d in g["declaraciones"]))
        vacio = MET.Nucleos("P-KI67", np.zeros((0, 2)), [], [],
                            {d: np.zeros(0, bool) for d in MET.DENOMINADORES})
        self.assertEqual(int(vacio.mascara("ck19_erosionada").sum()), 0)
        self.assertEqual(MET.fuera_ck19(vacio, self.ctx)["n"], 0)
        self.assertEqual(MET.hscore(vacio, self.ctx, "ck19_erosionada")["n"], 0)

    def test_contexto_de_suelo_no_mide_diana(self):
        """Hallazgo «puerta del sello esquivada»: núcleos de P-KI67 con un contexto de suelo y T a
        mano: toda medida se niega. Con núcleos de suelo, sí, y rotulado «pre-freeze»."""
        neg = MET.Contexto(None, "P-HER2NEG", T=0.10)
        nuc = _nucleos(31, lambda x, y: np.full(len(x), 0.3))
        reg0 = MET.rejilla(_fcs(), 200.0)
        for f in (lambda: MET.cuenta_regiones(nuc, reg0, neg, "ck19_erosionada"),
                  lambda: MET.porcentaje_global(nuc, {}, neg, "ck19_erosionada"),
                  lambda: MET.hotspot(nuc, _fcs(), neg, "ck19_erosionada")):
            with self.assertRaises(MET.MetricaError):
                f()
        suelo = _nucleos(32, lambda x, y: np.full(len(x), 0.0), lamina="P-HER2NEG")
        r = MET.cuenta_regiones(suelo, reg0, neg, "ck19_erosionada")
        g = MET.porcentaje_global(suelo, MET.excluye_pequenas(r, neg.p)[0], neg, "ck19_erosionada")
        self.assertIn(MET.ROTULOS["pre_congelacion"], g["rotulos"])
        self.assertTrue(g["umbral"]["pre_congelacion"])
        reg_ki = self._regiones(nuc)
        with self.assertRaises(MET.MetricaError):                # regiones contadas con otro T
            MET.porcentaje_global(nuc, reg_ki, MET.Contexto(self.sello_banda, "P-KI67")
                                  .eleva_por_fondo({"lamina": "P-KI67", "T_sellado": 0.10,
                                                    "eleva": True, "T_lamina": 0.2}),
                                  "ck19_erosionada")

    def test_control_de_fondo_sube_T_y_da_dos_cifras(self):
        """Hallazgo «control de fondo»: Ki67 (nuclear) con el 10 % de los ANILLOS de los núcleos
        fuera de CK19 dilatada a DAB 0,25 (>5 % sobre T = 0,10): T de la lámina = p99 de los
        anillos (0,25 ± 0,01), rótulo exacto y dos cifras en el %, el hotspot y el H-score. Con
        el 2 %, no sube. En P-CK19 no se aplica; en SYN (citoplasmático) el control es el DAB del
        compartimento fuera de CK19."""
        nuc = _nucleos(33, lambda x, y: np.full(len(x), 0.3))
        rng = np.random.default_rng(33)
        fuera = nuc.xy[:, 1] > 1900                              # «fuera de CK19 dilatada»
        anillo = np.where(fuera & (rng.random(len(nuc)) < 0.10), 0.25, 0.03)
        nuc.dab_anillo = anillo + rng.normal(0, 0.002, len(nuc))
        nuc.dab[fuera & (rng.random(len(nuc)) < 0.5)] = 0.18    # positivos «de fondo» 0,10-0,25
        ctl = MET.control_fondo(nuc, self.ctx, fuera, tre_dilatacion_um=12.0)
        self.assertTrue(ctl["eleva"])
        self.assertAlmostEqual(ctl["fraccion_sobre_T"], 0.10, delta=0.02)
        self.assertAlmostEqual(ctl["T_lamina"], 0.25, delta=0.01)
        self.assertEqual(ctl["rotulo"], "slide background above floor; threshold raised")
        c2 = self.ctx.eleva_por_fondo(ctl)
        self.assertEqual((c2.T_sellado, c2.T), (0.10, ctl["T_lamina"]))
        self.assertEqual(c2.cita()["control_fondo"]["tre_dilatacion_um"], 12.0)
        reg, _ = MET.excluye_pequenas(self._regiones(nuc, ctx=c2), c2.p)
        g = MET.porcentaje_global(nuc, reg, c2, "ck19_erosionada")
        self.assertIn("slide background above floor; threshold raised", g["rotulos"])
        self.assertGreater(g["dos_cifras"]["T_sellado"], g["dos_cifras"]["T_lamina"])
        self.assertIn("dos_cifras", MET.hscore(nuc, c2, "ck19_erosionada"))
        self.assertIn("dos_cifras", MET.hotspot(nuc, _fcs(), c2, "ck19_erosionada"))
        poco = np.where(fuera & (rng.random(len(nuc)) < 0.02), 0.25, 0.03)
        nuc.dab_anillo = poco
        self.assertFalse(MET.control_fondo(nuc, self.ctx, fuera, 12.0)["eleva"])
        ck = _nucleos(34, lambda x, y: np.full(len(x), 0.3), lamina="P-CK19")
        with self.assertRaises(MET.MetricaError):
            MET.control_fondo(ck, MET.Contexto(self.sello, "P-CK19"), fuera[:len(ck)], 12.0)
        syn = _nucleos(35, lambda x, y: np.full(len(x), 0.3), lamina="P-SYN")
        f_syn = syn.xy[:, 1] > 1900
        syn.dab[f_syn & (np.random.default_rng(1).random(len(syn)) < 0.2)] = 0.3
        ctx_syn = MET.Contexto(self.sello, "P-SYN")
        self.assertEqual(ctx_syn.compartimento, "anillo")
        c_syn = MET.control_fondo(syn, ctx_syn, f_syn, 12.0)
        self.assertTrue(c_syn["eleva"])
        self.assertIn("non-eroded CK19", c_syn["control"])

    def test_denominador_tres_cifras_erosion_y_parada(self):
        """Hallazgo «denominador»: sin las tres cifras, error; supervivencia de la erosión por
        FC en P-CK19 (FC1 90 %, FC2 50 % → principal morfométrico en FC2); parada dura de
        densidad (<70 % de la mediana de la serie → solo mapa, fuera de la cifra)."""
        nuc = _nucleos(36, lambda x, y: np.full(len(x), 0.3))
        sin = MET.Nucleos("P-KI67", nuc.xy, nuc.dab, nuc.fc,
                          {"ck19_erosionada": nuc.den["ck19_erosionada"]})
        reg, _ = MET.excluye_pequenas(self._regiones(nuc), self.ctx.p)
        with self.assertRaises(MET.MetricaError):
            MET.porcentaje_global(sin, reg, self.ctx, "ck19_erosionada")
        ck = _nucleos(37, lambda x, y: np.full(len(x), 0.3), lamina="P-CK19")
        rng = np.random.default_rng(37)
        sobrevive = np.where(ck.fc == "FC1", rng.random(len(ck)) < 0.9, rng.random(len(ck)) < 0.5)
        ck.den["ck19_erosionada"] = ck.den["ck19_sin_erosionar"] & sobrevive
        sup = MET.supervivencia_erosion(ck, MET.Contexto(self.sello, "P-CK19"))
        self.assertAlmostEqual(sup["FC1"]["fraccion"], 0.9, delta=0.02)
        self.assertEqual(sup["FC1"]["principal"], "ck19_erosionada")
        self.assertEqual(sup["FC2"]["principal"], "morfometrico")
        elec = MET.aplica_denominador_principal(nuc, sup)
        self.assertEqual(elec, {"FC1": "ck19_erosionada", "FC2": "morfometrico"})
        rp, _ = MET.excluye_pequenas(MET.cuenta_regiones(nuc, MET.rejilla(_fcs(), 200.0),
                                                         self.ctx, "principal"), self.ctx.p)
        gp = MET.porcentaje_global(nuc, rp, self.ctx, "principal")
        self.assertIn(MET.ROTULOS["denominador_ck19"], gp["rotulos"])
        self.assertEqual(set(MET.DENOMINADORES) | {"principal"}, set(gp["pct_por_denominador"]))
        dens = {"P-KI67": {"FC1": 3000.0, "FC2": 1500.0}, "P-CK19": {"FC1": 3100.0, "FC2": 2900.0},
                "P-RE": {"FC1": 2900.0, "FC2": 3000.0}}
        par = MET.parada_densidad(dens)
        self.assertEqual(par["solo_mapa"]["P-KI67"], ["FC2"])
        self.assertEqual(par["solo_mapa"]["P-CK19"], [])
        g = MET.porcentaje_global(nuc, reg, self.ctx, "ck19_erosionada",
                                  solo_mapa=par["solo_mapa"]["P-KI67"])
        self.assertEqual(g["n_regiones"], len([r for r in reg.values() if r["fc"] == "FC1"]))
        self.assertTrue(any("map only" in d for d in g["declaraciones"]))
        gm = MET.porcentaje_global(nuc, MET.excluye_pequenas(MET.cuenta_regiones(
            nuc, MET.rejilla(_fcs(), 200.0), self.ctx, "morfometrico"), self.ctx.p)[0],
            self.ctx, "morfometrico", ck19_caido=True)
        self.assertNotIn(MET.ROTULOS["denominador_ck19"], gm["rotulos"])
        self.assertEqual(list(gm["pct_por_denominador"]), ["morfometrico"])

    def test_heterogeneidad_cv_y_moran(self):
        """Agrupado (60 % | 10 % en x dentro de cada FC): Moran I > 0,5 con p < 0,01. Al azar
        (30 %): p > 0,05. Todo e interior (>100 µm) por separado. Sin «quantifiable», no hay
        Moran."""
        nuc = _nucleos(6, lambda x, y: np.where(x < 1500, 0.6, 0.1))
        reg, _ = MET.excluye_pequenas(self._regiones(nuc), self.ctx.p)
        h = MET.heterogeneidad(reg, self.ctx, "quantifiable")
        self.assertGreater(h["todo"]["moran"]["I"], 0.5)
        self.assertLess(h["todo"]["moran"]["p_permutacion"], 0.01)
        self.assertLess(h["interior"]["n_regiones"], h["todo"]["n_regiones"])
        self.assertGreater(h["todo"]["cv"], 0.5)
        self.assertLess(h["todo"]["por_fragmento"]["FC1"]["moran"]["p_permutacion"], 0.01)
        fila = h["regiones"][0]
        self.assertLessEqual(fila["ic95"][0], fila["pct"])
        self.assertGreaterEqual(fila["ic95"][1], fila["pct"])
        azar = MET.heterogeneidad(MET.excluye_pequenas(self._regiones(
            _nucleos(7, lambda x, y: np.full(len(x), 0.3))), self.ctx.p)[0], self.ctx,
            "quantifiable")
        self.assertGreater(azar["todo"]["moran"]["p_permutacion"], 0.05)
        pocas = {k: v for k, v in list(reg.items())[:10]}
        self.assertIsNone(MET.heterogeneidad(pocas, self.ctx, "quantifiable")["todo"]["moran"]["I"])
        foc = MET.heterogeneidad(reg, self.ctx, "focal")
        self.assertIsNone(foc["todo"]["moran"]["I"])
        self.assertIn("signal gate", foc["todo"]["moran"]["motivo"])

    def test_moran_no_confunde_nivel_entre_fragmentos(self):
        """Hallazgo «Moran entre fragmentos»: FC1 uniforme al 60 % y FC2 uniforme al 10 %, sin
        estructura dentro de cada uno: p > 0,05 (antes, I ≈ 0,97 con p = 0,001). La diferencia
        de nivel queda en la tabla por fragmento."""
        nuc = _nucleos(40, lambda x, y: np.where(y < 1200, 0.6, 0.1))
        reg, _ = MET.excluye_pequenas(self._regiones(nuc), self.ctx.p)
        h = MET.heterogeneidad(reg, self.ctx, "quantifiable")
        self.assertGreater(h["todo"]["moran"]["p_permutacion"], 0.05)
        self.assertLess(abs(h["todo"]["moran"]["I"]), 0.2)
        pf = h["todo"]["por_fragmento"]
        self.assertAlmostEqual(pf["FC1"]["pct"], 60.0, delta=2.0)
        self.assertAlmostEqual(pf["FC2"]["pct"], 10.0, delta=2.0)

    def test_heterogeneidad_descarta_regiones_vacias(self):
        """Hallazgo «Moran con NaN»: regiones con n = 0 (sin excluir) no dan I NaN con p 0,001."""
        nuc = _nucleos(41, lambda x, y: np.where(x < 1500, 0.6, 0.1),
                       densidad_fn=lambda x, y: np.where(x > 2600, 0.0, 1.0))
        reg = self._regiones(nuc)                                 # SIN excluye_pequenas
        h = MET.heterogeneidad(reg, self.ctx, "quantifiable")
        self.assertGreater(h["regiones_descartadas"], 0)
        self.assertTrue(math.isfinite(h["todo"]["moran"]["I"]))
        self.assertTrue(all(math.isfinite(f["pct"]) for f in h["regiones"]))

    def test_hotspot(self):
        """Fondo 10 %, disco de 250 µm de radio en (1500, 500) al 60 %. Tolerancias: máximo
        60 ± 6 %; centro a ≤ 75 µm; p de la permutación < 0,01. Sin foco: p > 0,05."""
        cx, cy = 1500.0, 500.0
        prob = lambda x, y: np.where((x - cx) ** 2 + (y - cy) ** 2 < 250 ** 2, 0.6, 0.1)  # noqa: E731
        nuc = _nucleos(8, prob, densidad_mm2=4000)
        h = MET.hotspot(nuc, _fcs(), self.ctx, "ck19_erosionada")
        self.assertAlmostEqual(h["maximo_pct"], 60.0, delta=6.0)
        self.assertLess(math.dist(h["maximo_centro_um"], (cx, cy)), 75.0)
        self.assertLess(h["permutacion"]["p_maximo"], 0.01)
        self.assertIn("not the IKWG method", h["rotulos"])
        self.assertIn(MET.ROTULOS["denominador_ck19"], h["rotulos"])
        self.assertGreater(h["n_ventanas_validas"], 100)
        self.assertEqual(len(h["distribucion_pct"]), h["n_ventanas_validas"])
        bv = h["biopsias_virtuales"]
        self.assertLess(bv["p50"], 20.0)
        self.assertGreater(bv["max"], 50.0)
        plano = MET.hotspot(_nucleos(9, lambda x, y: np.full(len(x), 0.1), densidad_mm2=4000),
                            _fcs(), self.ctx, "ck19_erosionada")
        self.assertGreater(plano["permutacion"]["p_maximo"], 0.05)

    def test_puerta_de_senal(self):
        """fp_her2 IC95 sup = 0,2 % (sellado). 0,1 % → no signal; 20 % → quantifiable; 40
        positivos en un rincón → focal (galería completa, semilla sellada)."""
        ctx = self.ctx
        casos = {
            "no signal": _nucleos(10, lambda x, y: np.full(len(x), 0.001)),
            "quantifiable": _nucleos(11, lambda x, y: np.full(len(x), 0.20)),
        }
        for esperado, nuc in casos.items():
            reg, _ = MET.excluye_pequenas(self._regiones(nuc), ctx.p)
            self.assertEqual(MET.puerta_senal(nuc, reg, ctx, "ck19_erosionada")["estado"], esperado)
        foco = _nucleos(12, lambda x, y: np.zeros(len(x)))
        dentro = np.nonzero((foco.xy[:, 0] < 200) & (foco.xy[:, 1] < 200))[0][:40]
        foco.dab[dentro] = 0.6
        reg, _ = MET.excluye_pequenas(self._regiones(foco), ctx.p)
        ps = MET.puerta_senal(foco, reg, ctx, "ck19_erosionada")
        self.assertEqual(ps["estado"], "focal")
        self.assertFalse(ps["entra_en_mapa_y_moran"])
        self.assertEqual(ps["focal"]["n_positivos"], 40)
        self.assertEqual(len(ps["focal"]["galeria_indices"]), 40)
        # > 200 positivos: 200 al azar con la semilla SELLADA (semillas.galeria_focal)
        muchos = _nucleos(14, lambda x, y: np.where((x < 600) & (y < 600), 0.3, 0.0))
        reg, _ = MET.excluye_pequenas(self._regiones(muchos), ctx.p)
        gf = MET.galeria_focal(muchos, reg, ctx, "ck19_erosionada")
        idx = np.nonzero(muchos.mascara("ck19_erosionada") & (muchos.dab > ctx.T))[0]
        self.assertGreater(len(idx), 200)
        esperado = np.sort(np.random.default_rng(SEMILLAS["galeria_focal"]).choice(idx, 200,
                                                                                 replace=False))
        self.assertEqual(gf["galeria_indices"], esperado.tolist())
        self.assertEqual(gf["semilla"], SEMILLAS["galeria_focal"])

    def test_borderline_solo_con_las_tasas_de_su_umbral(self):
        """Hallazgo «puerta de señal en la banda»: (1) sin las tasas de P-HER2 selladas en los
        extremos de la banda, «borderline» no se evalúa y se declara; (2) con ellas, una diana con
        el DAB pegado a T (20 % a 0,12) cambia de estado → «borderline: reported both ways»; (3)
        una diana con la cola de P-HER2 (0,25 % sobre T_bajo, nada sobre T) da «no signal» en los
        tres umbrales: no hay borderline espurio (antes, sí)."""
        borde = _nucleos(13, lambda x, y: np.full(len(x), 0.20), dab_pos=0.12, dab_sd=0.005)
        reg, _ = MET.excluye_pequenas(self._regiones(borde), self.ctx.p)
        pb = MET.puerta_senal(borde, reg, self.ctx, "ck19_erosionada")
        self.assertNotIn("borderline: reported both ways", pb["rotulos"])
        self.assertTrue(any("borderline not evaluated" in d for d in pb["declaraciones"]))
        cb = MET.Contexto(self.sello_banda, "P-KI67")
        reg_b, _ = MET.excluye_pequenas(self._regiones(borde, ctx=cb), cb.p)
        pb2 = MET.puerta_senal(borde, reg_b, cb, "ck19_erosionada")
        self.assertIn("borderline: reported both ways", pb2["rotulos"])
        self.assertNotEqual(pb2["estados"]["T"]["estado"], pb2["estados"]["T_alto"]["estado"])
        cola = _nucleos(15, lambda x, y: np.zeros(len(x)))
        rng = np.random.default_rng(15)
        cola.dab[rng.random(len(cola)) < 0.0025] = 0.08         # entre T_bajo (0,06) y T (0,10)
        reg_c, _ = MET.excluye_pequenas(self._regiones(cola, ctx=cb), cb.p)
        pc = MET.puerta_senal(cola, reg_c, cb, "ck19_erosionada")
        self.assertEqual({e["estado"] for e in pc["estados"].values()}, {"no signal"})
        self.assertNotIn("borderline: reported both ways", pc["rotulos"])

    def test_her2_membrana_descriptiva(self):
        """Hallazgo «HER2»: % de células sobre el T de «HER2, NEG», sin clasificar; si ninguna, el
        rótulo exacto del plan; fuera del mapa."""
        her2 = _nucleos(16, lambda x, y: np.zeros(len(x)), lamina="P-HER2")
        h = MET.her2_membrana(her2, MET.Contexto(self.sello, "P-HER2"), "ck19_erosionada")
        self.assertEqual(h["k"], 0)
        self.assertIsNone(h["clasificacion"])
        self.assertTrue(h["fuera_del_mapa"])
        self.assertIn("no membrane DAB signal above the floor measured on the second "
                      "HER2-labelled slide (‘HER2, NEG’; nature not determined: reagent control "
                      "or duplicate)", h["rotulos"])
        algo = _nucleos(17, lambda x, y: np.full(len(x), 0.01), lamina="P-HER2")
        h2 = MET.her2_membrana(algo, MET.Contexto(self.sello, "P-HER2"), "ck19_erosionada")
        self.assertAlmostEqual(h2["pct"], 1.0, delta=0.3)
        self.assertNotIn(MET.ROTULOS["her2_sin_senal"], h2["rotulos"])
        with self.assertRaises(MET.MetricaError):
            MET.her2_membrana(_nucleos(18, lambda x, y: np.zeros(len(x))), self.ctx,
                              "ck19_erosionada")

    def test_mapa_de_vecindad(self):
        """Dos marcadores con un campo COMPARTIDO de alcance corto (σ = 100 µm; 80 % común): rho
        > 0,6 e IC inferior > 0,3 (IC calibrado para autocorrelación); sin p-valor; techo
        (densidad con gradiente en y compartido) > 0,5. Uno uniforme: puerta de varianza →
        «near-uniform…». TRE: p90 150 µm no entra a L=200, sí a 2L=400."""
        from shapely.geometry import box
        fcs = {"FC%d" % i: box(0, 1400 * i, 6000, 1400 * i + 1000) for i in range(3)}
        rng = np.random.default_rng(42)
        comun = _campo(rng, 6100, 4000, 100.0)
        fa = math.sqrt(0.2) * _campo(rng, 6100, 4000, 100.0) + math.sqrt(0.8) * comun
        fb = math.sqrt(0.2) * _campo(rng, 6100, 4000, 100.0) + math.sqrt(0.8) * comun
        dens = lambda x, y: 0.5 + 0.5 * (y % 1000) / 1000  # noqa: E731

        def prob(f):
            return lambda x, y: _expit(-0.85 + 1.2 * f[(y / 10).astype(int), (x / 10).astype(int)])
        ctx_re = MET.Contexto(self.sello, "P-RE")
        a = _nucleos(14, prob(fa), densidad_fn=dens, fcs=fcs, lamina="P-RE")
        b = _nucleos(15, prob(fb), densidad_fn=dens, fcs=fcs)
        reg0 = MET.rejilla(fcs, 200.0)
        ra, _ = MET.excluye_pequenas(MET.cuenta_regiones(a, reg0, ctx_re, "ck19_erosionada"), ctx_re.p)
        rb, _ = MET.excluye_pequenas(MET.cuenta_regiones(b, reg0, self.ctx, "ck19_erosionada"),
                                     self.ctx.p)
        q = {"P-RE": "quantifiable", "P-KI67": "quantifiable"}
        m = MET.mapa_vecindad(ra, rb, self.ctx, 30.0, ("P-RE", "P-KI67"), q)
        self.assertTrue(m["entra"])
        self.assertGreater(m["rho"], 0.6)
        self.assertGreater(m["ic95"][0], 0.3)
        self.assertLessEqual(m["ic95"][0], m["ic95_bootstrap_bloques"][0])     # envolvente
        self.assertGreater(m["techo_densidad_rho"], 0.5)
        for r in ("descriptive, single block, n=1", "section-to-section + registration ceiling",
                  "co-localización regional en cortes seriados"):
            self.assertIn(r, m["rotulos"])
        self.assertFalse(any("p_val" in k or "p_value" in k for k in m))
        u = _nucleos(16, lambda x, y: np.full(len(x), 0.5), fcs=fcs)
        ru, _ = MET.excluye_pequenas(MET.cuenta_regiones(u, reg0, self.ctx, "ck19_erosionada"),
                                     self.ctx.p)
        mu = MET.mapa_vecindad(ra, ru, self.ctx, 30.0, ("P-RE", "P-KI67"), q)
        self.assertIsNone(mu["rho"])
        self.assertIn("near-uniform at this scale: regional association not estimable", mu["rotulos"])
        self.assertFalse(MET.mapa_vecindad(ra, rb, self.ctx, 150.0, ("P-RE", "P-KI67"), q)["entra"])
        esc = MET.mapa_vecindad_escalas(a, ctx_re, b, self.ctx, fcs, 200.0, 150.0,
                                        "ck19_erosionada", q)
        self.assertFalse(esc["L"]["entra"])
        self.assertTrue(esc["2L"]["entra"])
        self.assertEqual(esc["2L"]["L_um"], 400.0)
        self.assertGreater(esc["2L"]["rho"], 0.5)
        _sin_nunca(self, [m, mu, esc])

    def test_mapa_excluye_her2_ae1ae3_y_no_cuantificables(self):
        """Hallazgo «HER2 en el mapa»: HER2, HER2NEG y AE1/AE3 nunca entran; ni un marcador cuya
        puerta de señal no sea «quantifiable»; ni regiones de una lámina pasadas como otra."""
        a = _nucleos(19, lambda x, y: 0.05 + 0.6 * x / 3000, lamina="P-HER2NEG")
        ra, _ = MET.excluye_pequenas(MET.cuenta_regiones(
            a, MET.rejilla(_fcs(), 200.0), MET.Contexto(self.sello, "P-HER2NEG"),
            "ck19_erosionada"), self.ctx.p)
        b = _nucleos(20, lambda x, y: 0.10 + 0.5 * x / 3000)
        rb, _ = MET.excluye_pequenas(self._regiones(b), self.ctx.p)
        q = {"P-HER2NEG": "quantifiable", "P-KI67": "quantifiable"}
        m = MET.mapa_vecindad(ra, rb, self.ctx, 30.0, ("P-HER2NEG", "P-KI67"), q)
        self.assertFalse(m["entra"])
        self.assertIn("outside the map", m["motivo"])
        m2 = MET.mapa_vecindad(rb, rb, self.ctx, 30.0, ("P-KI67", "P-KI67"),
                               {"P-KI67": "focal"})
        self.assertFalse(m2["entra"])
        self.assertIn("signal gate", m2["motivo"])
        with self.assertRaises(MET.MetricaError):
            MET.mapa_vecindad(rb, rb, self.ctx, 30.0, ("P-RE", "P-KI67"),
                              {"P-RE": "quantifiable", "P-KI67": "quantifiable"})

    def test_mapa_sobre_regiones_comunes(self):
        """Hallazgo «mapa con 4 regiones comunes»: A densa a la izquierda y B a la derecha (40 y
        39 regiones, casi ninguna común): rho no se estima y se declara; la puerta de varianza
        mira las COMUNES."""
        ctx_re = MET.Contexto(self.sello, "P-RE")
        a = _nucleos(21, lambda x, y: 0.05 + 0.6 * x / 3000, lamina="P-RE",
                     densidad_fn=lambda x, y: np.where(x < 1600, 1.0, 0.05))
        b = _nucleos(22, lambda x, y: 0.05 + 0.6 * x / 3000,
                     densidad_fn=lambda x, y: np.where(x > 1400, 1.0, 0.05))
        reg0 = MET.rejilla(_fcs(), 200.0)
        ra, _ = MET.excluye_pequenas(MET.cuenta_regiones(a, reg0, ctx_re, "ck19_erosionada"), ctx_re.p)
        rb, _ = MET.excluye_pequenas(MET.cuenta_regiones(b, reg0, self.ctx, "ck19_erosionada"),
                                     self.ctx.p)
        m = MET.mapa_vecindad(ra, rb, self.ctx, 30.0, ("P-RE", "P-KI67"),
                              {"P-RE": "quantifiable", "P-KI67": "quantifiable"})
        self.assertLess(m["n_regiones"], 20)
        self.assertIsNone(m["rho"])
        self.assertTrue(any("paired regions" in d for d in m["declaraciones"]))

    def test_ic_de_spearman_calibrado_con_marcadores_independientes(self):
        """Hallazgo «IC de Spearman estrecho»: dos marcadores con campos espaciales INDEPENDIENTES
        (σ = 300 µm) en 3 FC de 6×1 mm, L = 200 µm (rho verdadera = 0). 300 réplicas: el IC excluye
        0 en ≤ 5 % + 2 ET binomiales (≤ 7,5 %). Con el bootstrap de bloques solo, 13-18 %."""
        from shapely.geometry import box
        fcs = {"FC%d" % i: box(0, 1400 * i, 6000, 1400 * i + 1000) for i in range(3)}
        reg0 = MET.rejilla(fcs, 200.0)
        p = self.ctx.p
        a0, s = math.log(0.3 / 0.7), 0.8
        rng = np.random.default_rng(20261023)
        N, excl, solo_boot = 300, 0, 0
        for _ in range(N):
            ra = MET.validas(_regiones_sinteticas(rng, reg0, "P-RE", _campo(rng, 6100, 4000, 300.0),
                                                  a0, s), p)
            rb = MET.validas(_regiones_sinteticas(rng, reg0, "P-KI67",
                                                  _campo(rng, 6100, 4000, 300.0), a0, s), p)
            com = sorted(set(ra) & set(rb), key=str)
            ic = MET.ic_spearman(np.array([ra[c]["pct"] for c in com]),
                                 np.array([rb[c]["pct"] for c in com]),
                                 np.array([ra[c]["centro"] for c in com]),
                                 [ra[c]["fc"] for c in com], 200.0, p,
                                 np.random.default_rng(int(p["semilla_mapa"])))
            excl += not (ic["ic95"][0] <= 0 <= ic["ic95"][1])
            bb = ic["ic95_bootstrap_bloques"]
            solo_boot += not (bb[0] <= 0 <= bb[1])
        tol = 0.05 + 2 * math.sqrt(0.05 * 0.95 / N)
        self.assertLessEqual(excl / N, tol, "exclusión %.3f (solo bootstrap %.3f)"
                             % (excl / N, solo_boot / N))

    def test_regiones_pobres_en_re(self):
        """RE al 90 % con una región interior a 0 %: candidata con k/n, IC (Clopper-Pearson) y
        control positivo a <500 µm; su clase según CK19/p63; una con artefacto, fuera; sin
        mini-puerta, no se evalúa."""
        ctxre = MET.Contexto(self.sello, "P-RE")
        hueco = lambda x, y: (x >= 1400) & (x < 1600) & (y >= 400) & (y < 600)  # noqa: E731
        nuc = _nucleos(17, lambda x, y: np.where(hueco(x, y), 0.0, 0.9), lamina="P-RE")
        reg = MET.cuenta_regiones(nuc, MET.rejilla(_fcs(), 200.0), ctxre, "ck19_erosionada")
        reg, _ = MET.excluye_pequenas(reg, ctxre.p)
        clave = ("FC1", 7, 2)
        self.assertEqual(reg[clave]["k"], 0)
        ck = {clave: True}
        for p63, clase, en in (("negativo", "ER-poor region within CK19+ epithelium, p63-negative", True),
                               ("periferia_positiva", "CK19+ epithelium with myoepithelial layer or "
                                "not assessable: benign or in-situ not excluded", False),
                               ("not assessable", "CK19+ epithelium with myoepithelial layer or "
                                "not assessable: benign or in-situ not excluded", False)):
            out = MET.regiones_re_pobres(nuc, reg, ctxre, "ck19_erosionada", True, ck19=ck,
                                         p63={clave: p63})
            self.assertEqual(len(out["candidatas"]), 1)
            c = out["candidatas"][0]
            self.assertEqual(c["region"], "FC1:7,2")
            self.assertEqual(c["clase"], clase)
            self.assertEqual(c["en_recuento"], en)
            self.assertEqual(c["rotulo"], "ER-poor region (<10 % of nuclei above T; k/n cells, "
                             "95 % CI; not an ASCO/CAP category)")
            self.assertAlmostEqual(c["ic95"][1], 100 * MET.clopper_pearson(0, c["n"])[1], places=6)
            self.assertTrue(c["control_positivo_500um"])
        sin = MET.regiones_re_pobres(nuc, reg, ctxre, "ck19_erosionada", True)
        self.assertEqual(sin["candidatas"][0]["clase"], "candidate, epithelial nature not confirmed")
        self.assertAlmostEqual(sin["limite_deteccion"]["ic95_sup_k0_pct"], 7.11, delta=0.05)
        # mini-puerta de RE POR FRAGMENTO: FC1 no la pasa → su candidata, fuera con su motivo
        por_fc = MET.regiones_re_pobres(nuc, reg, ctxre, "ck19_erosionada",
                                        {"FC1": False, "FC2": True})
        self.assertEqual(por_fc["candidatas"], [])
        self.assertIn("RE mini-gate", por_fc["excluidas"][0]["motivo"])
        nuc.artefacto[:] = hueco(nuc.xy[:, 0], nuc.xy[:, 1])
        reg_a = MET.cuenta_regiones(nuc, MET.rejilla(_fcs(), 200.0), ctxre, "ck19_erosionada")
        # con artefacto la región pierde su denominador: ya no es candidata
        out = MET.regiones_re_pobres(nuc, MET.excluye_pequenas(reg_a, ctxre.p)[0], ctxre,
                                     "ck19_erosionada", True)
        self.assertEqual(out["candidatas"], [])
        self.assertFalse(MET.regiones_re_pobres(nuc, reg, ctxre, "ck19_erosionada", False)["evaluado"])
        _sin_nunca(self, [out, sin])

    def test_puerta_de_p63_por_fragmento(self):
        """Hallazgo «p63»: estructura CK19+ de FC1 con 20 núcleos p63 sobre T repartidos por su
        perímetro (arco ≈ 64 %) → pasa; la de FC2 con 12 en un lado (arco ≈ 38 %) → «not
        assessable (no internal positive control)». En (a-bis), la región de FC2 pasa a «not
        assessable» aunque el dict dijera «negativo». Cifra aparte «invasive-only where p63
        available» con la fracción cubierta."""
        from shapely.geometry import Point
        ctx63 = MET.Contexto(self.sello, "P-P63")
        e1, e2 = Point(500, 500).buffer(100), Point(500, 1900).buffer(100)
        ang1 = np.linspace(0, 2 * np.pi, 20, endpoint=False)
        ang2 = np.linspace(0, 0.6 * np.pi, 12)
        xy = np.r_[np.c_[500 + 103 * np.cos(ang1), 500 + 103 * np.sin(ang1)],
                   np.c_[500 + 103 * np.cos(ang2), 1900 + 103 * np.sin(ang2)],
                   np.random.default_rng(3).uniform([0, 0], [3000, 1000], (300, 2))]
        fc = ["FC1"] * 20 + ["FC2"] * 12 + ["FC1"] * 300
        dab = np.r_[np.full(32, 0.5), np.full(300, 0.02)]
        n = len(xy)
        p63 = MET.Nucleos("P-P63", xy, dab, fc, {d: np.ones(n, bool) for d in MET.DENOMINADORES})
        pu = MET.puerta_p63(p63, ctx63, [{"fc": "FC1", "geom": e1}, {"fc": "FC2", "geom": e2}])
        self.assertTrue(pu["FC1"]["pasa"])
        self.assertGreater(pu["FC1"]["estructuras"][0]["arco"], 0.5)
        self.assertFalse(pu["FC2"]["pasa"])
        self.assertEqual(pu["FC2"]["rotulo"], "not assessable (no internal positive control)")
        ctxre = MET.Contexto(self.sello, "P-RE")
        hueco = lambda x, y: (x >= 1400) & (x < 1600) & (y >= 1800) & (y < 2000)  # noqa: E731
        nuc = _nucleos(23, lambda x, y: np.where(hueco(x, y), 0.0, 0.9), lamina="P-RE")
        reg, _ = MET.excluye_pequenas(MET.cuenta_regiones(nuc, MET.rejilla(_fcs(), 200.0), ctxre,
                                                          "ck19_erosionada"), ctxre.p)
        clave = ("FC2", 7, 9)
        out = MET.regiones_re_pobres(nuc, reg, ctxre, "ck19_erosionada", True, ck19={clave: True},
                                     p63={clave: "negativo"}, p63_puerta=pu)
        self.assertEqual(out["candidatas"][0]["p63"], "not assessable")
        self.assertFalse(out["candidatas"][0]["en_recuento"])
        ki = _nucleos(24, lambda x, y: np.full(len(x), 0.3))
        inv = MET.cifra_invasiva_p63(ki, self.ctx, "ck19_erosionada", pu, np.zeros(len(ki), bool))
        self.assertIn("invasive-only where p63 available", inv["rotulos"])
        self.assertAlmostEqual(inv["fraccion_cubierta"], 0.5, delta=0.03)   # solo FC1 (mitad)
        self.assertAlmostEqual(inv["pct"], 30.0, delta=2.0)

    def test_hscore_y_fuera_de_ck19(self):
        """Valores conocidos: 10 % en (T,0,4], 20 % en (0,4;0,6], 30 % > 0,6 → H = 10+40+90 = 140.
        Hallazgo «rótulo de RE»: sale por la lámina del contexto, sin pasar `marcador`."""
        n = 1000
        dab = np.r_[np.full(400, 0.02), np.full(100, 0.3), np.full(200, 0.5), np.full(300, 0.9)]
        dens = {"ck19_erosionada": np.ones(n, bool),
                "ck19_sin_erosionar": np.r_[np.ones(900, bool), np.zeros(100, bool)],
                "morfometrico": np.ones(n, bool)}
        nuc_re = MET.Nucleos("P-RE", np.zeros((n, 2)), dab, ["FC1"] * n, dens)
        h = MET.hscore(nuc_re, MET.Contexto(self.sello, "P-RE"), "ck19_erosionada")
        self.assertAlmostEqual(h["hscore"], 140.0, places=6)
        self.assertIn("fixed, uncalibrated cut-offs", h["rotulos"])
        self.assertIn("DAB saturated; intensity bins not informative", h["rotulos"])
        nuc_ki = MET.Nucleos("P-KI67", np.zeros((n, 2)), dab, ["FC1"] * n, dens)
        self.assertNotIn("DAB saturated; intensity bins not informative",
                         MET.hscore(nuc_ki, self.ctx, "ck19_erosionada")["rotulos"])
        f = MET.fuera_ck19(nuc_ki, self.ctx)
        self.assertEqual(f["n"], 100)
        self.assertEqual(f["pct"], 100.0)
        self.assertEqual(f["rotulo"], "DAB-positive nuclei outside CK19+ epithelium: 100.0 %; "
                         "includes proliferating lymphoid/stromal cells")

    def test_hscore_con_dab_recortado_es_cota_inferior(self):
        """Regla de artefacto v2 (2-oct): el DAB recortado ya no saca la célula; el H-score lo dice
        con el % de POSITIVAS con recorte, y la cifra no cambia (la positividad no depende del
        recorte). Sin el array (objetos de antes), sin rótulo."""
        n = 1000
        dab = np.r_[np.full(400, 0.02), np.full(100, 0.3), np.full(200, 0.5), np.full(300, 0.9)]
        rec = np.r_[np.zeros(400), np.zeros(100), np.zeros(200), np.full(150, 0.3), np.zeros(150)]
        rec[0] = 0.5                                   # una negativa recortada: no cuenta
        dens = {"ck19_erosionada": np.ones(n, bool)}
        sin = MET.hscore(MET.Nucleos("P-KI67", np.zeros((n, 2)), dab, ["FC1"] * n, dens),
                         self.ctx, "ck19_erosionada")
        con = MET.hscore(MET.Nucleos("P-KI67", np.zeros((n, 2)), dab, ["FC1"] * n, dens,
                                     recorte=rec), self.ctx, "ck19_erosionada")
        self.assertEqual(con["hscore"], sin["hscore"])
        self.assertIsNone(sin["frac_positivas_con_recorte"])
        self.assertAlmostEqual(con["frac_positivas_con_recorte"], 150 / 600)
        self.assertIn("DAB OD clipped in 25.0 % of positive cells; H-score is a lower bound for them",
                      con["rotulos"])
        self.assertFalse([r for r in sin["rotulos"] if "clipped" in r])
        sub = MET._subconjunto(MET.Nucleos("P-KI67", np.zeros((n, 2)), dab, ["FC1"] * n, dens,
                                           recorte=rec), np.arange(n) >= 700)
        self.assertEqual(len(sub.recorte), 300)

    def test_hscore_con_T_alto_e_histograma_completo(self):
        """Hallazgo «H-score con T > 0,4»: T = 0,45 («high floor»): 500 núcleos a 0,42 (bajo T) y
        500 a 0,02 → H = 0 (antes 100), cortes colapsados, igual que `laminillas_color.hscore`.
        Hallazgo «histograma»: con DAB negativo, las cuentas suman n."""
        s = sella(self.tmp, "alto", T=0.45, p999=0.40)
        ctx = MET.Contexto(s, "P-KI67")
        self.assertIn("high floor", ctx.rotulos_umbral())
        n = 1000
        dens = {d: np.ones(n, bool) for d in MET.DENOMINADORES}
        nuc = MET.Nucleos("P-KI67", np.zeros((n, 2)), np.r_[np.full(500, 0.42), np.full(500, 0.02)],
                          ["FC1"] * n, dens)
        h = MET.hscore(nuc, ctx, "ck19_erosionada")
        self.assertEqual(h["hscore"], 0.0)
        self.assertTrue(h["cortes_colapsados"])
        self.assertEqual(h["cortes"], [0.45, 0.45, 0.6])
        v = np.r_[np.full(300, 0.5), np.full(300, 0.7), np.full(400, 0.02)]
        nuc2 = MET.Nucleos("P-KI67", np.zeros((n, 2)), v, ["FC1"] * n, dens)
        h2 = MET.hscore(nuc2, ctx, "ck19_erosionada")
        try:
            import laminillas_color as C
            self.assertAlmostEqual(h2["hscore"], C.hscore(v, 0.45)["H"], places=6)
        except ImportError:
            pass
        self.assertAlmostEqual(h2["hscore"], 2 * 30 + 3 * 30, places=6)
        neg = MET.Nucleos("P-KI67", np.zeros((n, 2)), np.r_[np.full(300, -0.03), np.full(700, 0.2)],
                          ["FC1"] * n, dens)
        hn = MET.hscore(neg, self.ctx, "ck19_erosionada")
        self.assertEqual(sum(hn["histograma"]["cuentas"]), n)
        self.assertLessEqual(hn["histograma"]["bordes"][0], -0.03)

    # ── simetría NE (plan, actualización 11) ─────────────────────────────────────────────────
    def _ne_escena(self, semilla, positivos):
        """SYN con todas las células negativas salvo `positivos(nuc)` (bool N, DAB 0,5); RE con
        gradiente creciente en x (tercil alto a la derecha) y Ki67 decreciente (tercil alto a la
        izquierda), contadas en la MISMA rejilla de 200 µm sobre los dos FC."""
        syn = _nucleos(semilla, lambda x, y: np.zeros(len(x)), lamina="P-SYN")
        syn.dab[positivos(syn)] = 0.5
        ctx_syn = MET.Contexto(self.sello, "P-SYN")
        reg0 = MET.rejilla(_fcs(), 200.0)
        re = _nucleos(semilla + 1, lambda x, y: 0.05 + 0.9 * x / 3000, lamina="P-RE")
        ki = _nucleos(semilla + 2, lambda x, y: 0.6 - 0.5 * x / 3000)
        ctx_re = MET.Contexto(self.sello, "P-RE")
        ref = {"P-RE": MET.excluye_pequenas(MET.cuenta_regiones(re, reg0, ctx_re, "ck19_erosionada"),
                                            ctx_re.p)[0],
               "P-KI67": MET.excluye_pequenas(MET.cuenta_regiones(ki, reg0, self.ctx,
                                                                  "ck19_erosionada"), self.ctx.p)[0]}
        reg_syn, _ = MET.excluye_pequenas(MET.cuenta_regiones(syn, reg0, ctx_syn, "ck19_erosionada"),
                                          ctx_syn.p)
        return syn, ctx_syn, reg_syn, ref, (re, ki, ctx_re)

    def test_simetria_ne_focal_da_pct_con_ic_y_patron(self):
        """Actualización 11: SYN «focal» (90 positivos en 6 racimos de 40 µm de radio, todos en
        el tercil alto de RE) recibe las MISMAS métricas que RE y Ki67 —% con IC por bloques y
        rango de banda, H-score, heterogeneidad con CV— y la puerta solo le quita el mapa y el
        Moran. Su patrón de puntos: agregado (vecino más próximo < 0,5× el nulo, p < 0,01; Ripley
        global p < 0,01), más cerca del tercil alto de RE y más lejos del bajo y del tercil alto de
        Ki67 (p < 0,01 con 999 permutaciones; mínimo posible 0,001)."""
        centros = np.array([[2300, 300], [2500, 700], [2700, 400], [2400, 1700], [2600, 2100],
                            [2800, 1900]], float)

        def racimos(nuc):
            return np.min(np.linalg.norm(nuc.xy[:, None, :] - centros[None], axis=2), axis=1) < 40
        syn, ctx_syn, reg_syn, ref, (re, ki, ctx_re) = self._ne_escena(50, racimos)
        r = MET.mide_marcador(syn, reg_syn, ctx_syn, "ck19_erosionada", referencias=ref)
        self.assertEqual(r["senal"]["estado"], "focal")
        self.assertFalse(r["entra_en_mapa_y_moran"])
        g = r["porcentaje"]
        verdad = 100.0 * g["k"] / g["n"]
        self.assertGreater(g["k"], 50)
        self.assertAlmostEqual(g["pct"], verdad, places=9)
        lo, hi = g["intervalos"]["bloques"]
        self.assertTrue(lo <= g["pct"] <= hi, (lo, g["pct"], hi))
        self.assertIn("banda_T", g["intervalos"])
        self.assertIn(MET.ROTULOS["denominador_ck19"], g["rotulos"])
        self.assertTrue(math.isfinite(r["hscore"]["hscore"]))
        self.assertTrue(math.isfinite(r["heterogeneidad"]["todo"]["cv"]))
        self.assertIsNone(r["heterogeneidad"]["todo"]["moran"]["I"])
        pp = r["patron_puntos"]
        self.assertEqual(pp["rotulo"], "spatial pattern of NE-marker-positive cells (descriptive, n=1)")
        self.assertEqual(pp["n_positivos"], int(np.sum(syn.mascara("ck19_erosionada") &
                                                       (syn.dab > ctx_syn.T))))
        self.assertLess(pp["vecino_mas_proximo"]["cociente"], 0.5)
        self.assertLess(pp["vecino_mas_proximo"]["p_agregacion"], 0.01)
        self.assertLess(pp["ripley"]["p_agregacion_global"], 0.01)
        self.assertGreater(pp["ripley"]["cociente"][1], 2.0)               # r = 50 µm
        d = pp["distancias"]
        alto_re = d["regions in the top ER tercile (regional %)"]
        self.assertLess(alto_re["p_mas_cerca"], 0.01)
        self.assertGreater(alto_re["fraccion_dentro"], 0.9)
        self.assertLess(alto_re["fraccion_dentro_nulo"], 0.5)
        self.assertLess(d["regions in the bottom ER tercile (regional %)"]["p_mas_lejos"], 0.01)
        self.assertLess(d["regions in the top Ki67 tercile (regional %)"]["p_mas_lejos"], 0.01)
        # RE y Ki67 («quantifiable»): las MISMAS claves; Ki67 añade el hotspot, el NE focal el patrón
        rre = MET.mide_marcador(re, ref["P-RE"], ctx_re, "ck19_erosionada")
        self.assertEqual(rre["senal"]["estado"], "quantifiable")
        self.assertTrue(rre["entra_en_mapa_y_moran"])
        self.assertTrue(math.isfinite(rre["heterogeneidad"]["todo"]["moran"]["I"]))
        self.assertEqual(set(r) - {"patron_puntos"}, set(rre))
        rki = MET.mide_marcador(ki, ref["P-KI67"], self.ctx, "ck19_erosionada", fcs=_fcs())
        self.assertEqual(set(rki) - {"hotspot"}, set(rre))
        for lam in ("P-CK19", "P-HER2NEG"):
            with self.assertRaises(MET.MetricaError):
                n = _nucleos(53, lambda x, y: np.zeros(len(x)), lamina=lam)
                MET.mide_marcador(n, {}, MET.Contexto(self.sello, lam), "ck19_erosionada")
        with self.assertRaises(MET.MetricaError):                  # el patrón es de los NE
            MET.patron_puntos(re, ctx_re, "ck19_erosionada", ref)
        _sin_nunca(self, [r, rre])

    def test_patron_de_puntos_nulo_por_fragmento_y_minimos(self):
        """Positivos al azar al 1 % en FC1 y ninguno en FC2: con el etiquetado aleatorio DENTRO
        de cada fragmento no hay agregación (p > 0,01 en vecino más próximo y Ripley global;
        cociente 0,85-1,15), aunque todos estén en un fragmento. Con < 5 positivos: recuento y
        posiciones, sin estadísticos."""
        rng = np.random.default_rng(51)
        syn, ctx_syn, reg_syn, ref, _ = self._ne_escena(
            52, lambda nuc: (nuc.fc == "FC1") & (rng.random(len(nuc)) < 0.01))
        pp = MET.patron_puntos(syn, ctx_syn, "ck19_erosionada", ref)
        self.assertGreater(pp["n_positivos"], 50)
        self.assertGreater(pp["vecino_mas_proximo"]["p_agregacion"], 0.01)
        self.assertGreater(pp["ripley"]["p_agregacion_global"], 0.01)
        self.assertAlmostEqual(pp["vecino_mas_proximo"]["cociente"], 1.0, delta=0.15)
        pocos, ctx2, _, _, _ = self._ne_escena(54, lambda nuc: np.arange(len(nuc)) < 3)
        p3 = MET.patron_puntos(pocos, ctx2, "ck19_erosionada", ref)
        self.assertEqual(p3["n_positivos"], 3)
        self.assertEqual(len(p3["posiciones_um"]), 3)
        self.assertIn("fewer than 5 positive cells", p3["motivo"])
        self.assertNotIn("vecino_mas_proximo", p3)

    def test_mapa_saca_lo_que_iv_marca(self):
        """Pedido del módulo A: las láminas que `laminillas_congela.fuera_del_mapa` da por
        «counterstain differs» no entran en el mapa, ni las que (iv) aún no ha evaluado; se lee
        del sello del contexto. Contextos de sellos distintos, o un (iv) de otro sello: error."""
        s = sella(self.tmp, "iv")
        anota_iv(s, ["P-RE", "P-KI67", "P-SYN"], fuera=("P-SYN",))
        ctx = {n: MET.Contexto(s, n) for n in ("P-RE", "P-KI67", "P-SYN", "P-CHGA")}
        nuc = {n: _nucleos(60 + i, lambda x, y: 0.05 + 0.6 * x / 3000, lamina=n)
               for i, n in enumerate(ctx)}
        q = {n: "quantifiable" for n in ctx}

        def par(a, b):
            return MET.mapa_vecindad_escalas(nuc[a], ctx[a], nuc[b], ctx[b], _fcs(), 200.0, 30.0,
                                             "ck19_erosionada", q)["L"]
        m = par("P-RE", "P-SYN")
        self.assertFalse(m["entra"])
        self.assertIn("counterstain differs (iv)", m["motivo"])
        self.assertEqual(m["rotulos_iv"]["P-SYN"], ["counterstain differs"])
        self.assertTrue(par("P-RE", "P-KI67")["entra"])
        sin = par("P-RE", "P-CHGA")
        self.assertFalse(sin["entra"])
        self.assertIn("not yet evaluated", sin["motivo"])
        with self.assertRaises(MET.MetricaError):
            MET.mapa_vecindad_escalas(nuc["P-RE"], ctx["P-RE"], nuc["P-KI67"], self.ctx, _fcs(),
                                      200.0, 30.0, "ck19_erosionada", q)
        reg0 = MET.rejilla(_fcs(), 200.0)
        ra = MET.cuenta_regiones(nuc["P-RE"], reg0, ctx["P-RE"], "ck19_erosionada")
        rk = MET.cuenta_regiones(nuc["P-KI67"], reg0, ctx["P-KI67"], "ck19_erosionada")
        with self.assertRaises(MET.MetricaError):
            MET.mapa_vecindad(ra, rk, ctx["P-RE"], 30.0, ("P-RE", "P-KI67"), q,
                              fuera_mapa={"fuera": {}, "sin_evaluar": [], "sello_sha256": "0" * 64})

    def test_mapa_multimarcador_pares_obligatorios(self):
        """Actualización 11: RE × SYN y Ki67 × SYN son obligatorios. Con los tres marcadores sobre
        un campo compartido (80 %), salen los tres pares con rho > 0,5 a L; HER2NEG y CK19, fuera.
        Con SYN «focal», o sin SYN medida, los dos obligatorios SIGUEN en la tabla con su motivo."""
        from shapely.geometry import box
        fcs = {"FC%d" % i: box(0, 1400 * i, 6000, 1400 * i + 1000) for i in range(3)}
        rng = np.random.default_rng(43)
        comun = _campo(rng, 6100, 4000, 100.0)

        def prob():
            f = math.sqrt(0.2) * _campo(rng, 6100, 4000, 100.0) + math.sqrt(0.8) * comun
            return lambda x, y: _expit(-0.85 + 1.2 * f[(y / 10).astype(int), (x / 10).astype(int)])
        marc = {}
        for i, n in enumerate(("P-RE", "P-KI67", "P-SYN")):
            marc[n] = (_nucleos(70 + i, prob(), fcs=fcs, lamina=n), MET.Contexto(self.sello, n),
                       "quantifiable")
        for i, n in enumerate(("P-HER2NEG", "P-CK19")):
            marc[n] = (_nucleos(80 + i, lambda x, y: np.zeros(len(x)), fcs=fcs, lamina=n),
                       MET.Contexto(self.sello, n), "no signal")
        tre = {("P-RE", "P-KI67"): 30.0, ("P-SYN", "P-RE"): 30.0, ("P-KI67", "P-SYN"): 30.0}
        m = MET.mapa_multimarcador(marc, fcs, 200.0, tre, "ck19_erosionada")
        self.assertEqual(set(m["pares"]), {"P-KI67×P-RE", "P-KI67×P-SYN", "P-RE×P-SYN"})
        self.assertEqual(set(m["fuera"]), {"P-HER2NEG", "P-CK19"})
        for clave in ("P-RE×P-SYN", "P-KI67×P-SYN"):
            f = m["pares"][clave]
            self.assertTrue(f["obligatorio"])
            self.assertTrue(f["L"]["entra"], f["L"])
            self.assertGreater(f["L"]["rho"], 0.5)
        self.assertFalse(m["pares"]["P-KI67×P-RE"]["obligatorio"])
        self.assertEqual({o["par"] for o in m["obligatorios"]}, {"P-RE×P-SYN", "P-KI67×P-SYN"})
        foc = dict(marc)
        foc["P-SYN"] = marc["P-SYN"][:2] + ("focal",)
        m2 = MET.mapa_multimarcador(foc, fcs, 200.0, tre, "ck19_erosionada")
        for o in m2["obligatorios"]:
            self.assertFalse(o["entra"]["L"])
            self.assertIn("signal gate", o["motivo"]["L"])
        sin = {k: v for k, v in marc.items() if k != "P-SYN"}
        m3 = MET.mapa_multimarcador(sin, fcs, 200.0, tre, "ck19_erosionada")
        self.assertEqual({o["par"] for o in m3["obligatorios"]}, {"P-RE×P-SYN", "P-KI67×P-SYN"})
        for o in m3["obligatorios"]:
            self.assertFalse(o["medido"])
            self.assertIn("marker not measured", o["motivo"]["L"])
        _sin_nunca(self, [{k: v for k, v in x.items()} for x in (m, m2, m3)])

    def test_rotulos_exactos(self):
        """Toda frase del plan está en los rótulos tal cual; ninguna de «Nunca decir»; la lista
        de «Nunca decir» del código incluye las que faltaban."""
        emitidos = " | ".join(list(MET.ROTULOS.values()) + list(R.ROTULOS.values()))
        for frase in FRASES_PLAN:
            self.assertIn(frase.split("{")[0], emitidos.replace("{n}", "").replace("{x}", ""), frase)
        _sin_nunca(self, [MET.ROTULOS, R.ROTULOS, R.METODOS, G.METODOS, MET.DESVIACIONES])
        for f in ("tumour cells", "tumor cells", "whole slide", "whole-slide",
                  "% of invasive carcinoma"):
            self.assertIn(f, MET.NUNCA_DECIR)
        self.assertEqual(MET.barre_nunca({"x": "Whole slide of tumour cells"}),
                         ["whole slide", "tumour cells"])


# ══ D. GeoJSON ════════════════════════════════════════════════════════════════════════════
class GeoJSON(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="piloto-b-geo-")
        self.sello = sella(self.tmp)
        self.ctx = MET.Contexto(self.sello, "P-KI67")

    def test_coleccion_de_interseccion_queda_en_su_superficie(self):
        """Piloto real (2-oct): una celda ∩ FC que se tocan en un borde da GeometryCollection
        (polígono + línea) y `geometria` la rechazaba. Ahora: solo las partes con área; sin
        ninguna, GeoJSONError."""
        from shapely.geometry import GeometryCollection, LineString, Point, box
        a, b = box(0, 0, 10, 10), box(10, 0, 20, 10)
        col = box(0, 0, 10, 10).intersection(box(5, 0, 20, 10)).union(LineString([(30, 0), (40, 0)]))
        g = G.geometria(GeometryCollection([a, LineString([(30, 0), (40, 0)]), Point(50, 50)]))
        self.assertEqual(g["type"], "Polygon")
        g2 = G.geometria(GeometryCollection([a, b.buffer(-1)]))
        self.assertEqual(g2["type"], "MultiPolygon")
        self.assertEqual(len(g2["coordinates"]), 2)
        self.assertIn(G.geometria(col)["type"], ("Polygon", "MultiPolygon"))
        with self.assertRaises(G.GeoJSONError):
            G.geometria(GeometryCollection([LineString([(0, 0), (1, 1)]), Point(3, 3)]))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _coleccion(self, extra_props=None):
        from shapely.geometry import Point
        mpp = S.MPP_L0
        M = S.matriz(30.0, 500.0, -200.0)                       # lámina → referencia (µm)
        rng = np.random.default_rng(3)
        xy_ref = rng.uniform([100, 100], [2900, 900], (300, 2))
        xy_l0 = R._aplica(np.linalg.inv(M), xy_ref) / mpp
        dab = np.where(rng.random(300) < 0.3, 0.5, 0.02)
        dens = {"ck19_erosionada": np.ones(300, bool), "ck19_sin_erosionar": np.ones(300, bool),
                "morfometrico": rng.random(300) < 0.8}
        nuc = MET.Nucleos("P-KI67", xy_ref, dab, ["FC1"] * 300, dens, xy_l0=xy_l0)
        reg = MET.cuenta_regiones(nuc, MET.rejilla({"FC1": _fcs()["FC1"]}, 400.0), self.ctx,
                                  "ck19_erosionada")
        geoms = [Point(x, y).buffer(12) for x, y in xy_l0]
        feats = G.nucleos(geoms, dab, self.ctx, np.ones(300, bool), area_um2=np.full(300, 30.0))
        feats += G.regiones(reg, G.regiones_a_l0(reg, {"FC1": M}, mpp), MET.clopper_pearson)
        if extra_props:
            feats[0]["properties"].update(extra_props)
        feats += G.capa_editable([])
        return G.coleccion(feats, self.ctx, "Ki67"), xy_l0, dab, reg, M, nuc

    def test_valido_en_px_l0_y_con_capas(self):
        fc, xy_l0, dab, reg, M, _ = self._coleccion()
        self.assertEqual(G.valida(fc), [])
        tipos = {f["properties"]["objectType"] for f in fc["features"]}
        self.assertEqual(tipos, {"detection", "annotation"})
        n0 = fc["features"][0]
        from shapely.geometry import shape
        c = shape(n0["geometry"]).centroid
        self.assertLess(math.dist((c.x, c.y), xy_l0[0]), 0.5)   # px L0, tolerancia 0,5 px
        self.assertEqual(n0["properties"]["classification"]["name"],
                         "Positive" if dab[0] > self.ctx.T else "Negative")
        self.assertIn("DAB OD mean (compartment)", n0["properties"]["measurements"])
        # la región vuelve a px L0 de la lámina con la inversa de su transformada
        r0 = [f for f in fc["features"] if f["properties"]["objectType"] == "annotation"][0]
        cr = shape(r0["geometry"]).centroid
        clave = sorted(reg)[0]
        esperado = R._aplica(np.linalg.inv(M), [reg[clave]["centro"]])[0] / S.MPP_L0
        self.assertLess(math.dist((cr.x, cr.y), esperado), 1.0)
        self.assertFalse(any(f["properties"].get("classification", {}).get("name") == G.CAPA_EDITABLE
                             for f in fc["features"]))         # capa vacía (fuera de zona p63)
        self.assertIn("in situ/exclude", " ".join(fc["laminillas"]["capas"]))
        self.assertEqual(fc["laminillas"]["tabla_opaco_marcador"],
                         [{"lamina": "P-KI67", "marcador": "Ki67"}])
        self.assertEqual(fc["laminillas"]["umbral"]["T"], self.ctx.T)
        self.assertIn(G.METODOS["capa"], fc["laminillas"]["metodos"])
        ruta = G.escribe(os.path.join(self.tmp, "ki67.geojson"), fc)
        self.assertEqual(G.valida(_carga(ruta)), [])

    def test_sin_sello_ni_T_suelto(self):
        """Hallazgo «GeoJSON sin sello»: no se clasifica con un T suelto; una colección con
        detecciones de otro T no se arma; una lámina diana sin sello no tiene contexto."""
        from shapely.geometry import Point
        geoms = [Point(10, 10).buffer(5)]
        with self.assertRaises(G.GeoJSONError):
            G.nucleos(geoms, [0.06], 0.05, [True])
        with self.assertRaises(SL.SelloAusente):
            MET.Contexto(None, "P-KI67", T=0.05)
        neg = MET.Contexto(None, "P-HER2NEG", T=0.05)
        feats = G.nucleos(geoms, [0.06], neg, [True])
        with self.assertRaises(G.GeoJSONError):
            G.coleccion(feats, self.ctx, "Ki67")
        with self.assertRaises(G.GeoJSONError):                 # sin Contexto no hay cita
            G.coleccion(feats, "P-KI67", "Ki67")

    def test_regiones_con_la_matriz_de_su_fc(self):
        """Hallazgo «regiones con una sola matriz»: dos FC con transformadas distintas (Δ 40 µm):
        cada región vuelve a px L0 con la de SU FC (≤1 px de la verdad); sin matriz, no sale."""
        from shapely.geometry import box
        mpp = S.MPP_L0
        fcs = {"FC1": box(0, 0, 1000, 600), "FC2": box(0, 1000, 1000, 1600)}
        reg = MET.rejilla(fcs, 200.0)
        M1 = S.matriz(20.0, 300.0, -100.0)
        M2 = np.array([[1, 0, 24.0], [0, 1, 32.0], [0, 0, 1.0]]) @ M1
        geo = G.regiones_a_l0(reg, {"FC1": M1, "FC2": M2}, mpp)
        for c, r in reg.items():
            M = M1 if r["fc"] == "FC1" else M2
            esperado = R._aplica(np.linalg.inv(M), [r["geom"].centroid.coords[0]])[0] / mpp
            got = geo[c].centroid
            self.assertLess(math.dist((got.x, got.y), esperado), 1.0, c)
        self.assertEqual({r["fc"] for c, r in reg.items() if c in G.regiones_a_l0(
            reg, {"FC1": M1}, mpp)}, {"FC1"})

    def test_rechaza_rutas_y_no_geojson(self):
        fc, *_ = self._coleccion({"name": "/Users/alguien/P-KI67.tiff"})
        self.assertTrue(G.valida(fc))
        with self.assertRaises(G.GeoJSONError):
            G.escribe(os.path.join(self.tmp, "x.geojson"), fc)
        roto, *_ = self._coleccion()
        roto["features"][0]["geometry"]["coordinates"][0] = roto["features"][0]["geometry"][
            "coordinates"][0][:-1]                             # anillo abierto
        self.assertTrue(G.valida(roto))
        self.assertTrue(G.valida({"type": "FeatureCollection"}))   # esquema: sin features

    def test_patologo_dibuja_y_se_recalcula(self):
        """Una anotación de la capa «in situ / exclude» que tapa una franja: los % se rehacen sin
        esos núcleos (verdad calculada a mano) con los TRES denominadores, en la banda de T y por
        región. Hallazgo «recálculo parcial»."""
        from shapely.geometry import box
        fc, xy_l0, dab, reg, M, nuc = self._coleccion()
        x0, x1 = np.percentile(xy_l0[:, 0], [20, 50])
        y0, y1 = xy_l0[:, 1].min() - 1, xy_l0[:, 1].max() + 1
        fc["features"] += G.capa_editable([box(x0, y0, x1, y1)])
        r = G.recalcula_con_exclusiones(fc, nuc, self.ctx, "ck19_erosionada", regiones=reg)
        pos = dab > self.ctx.T
        dentro = (xy_l0[:, 0] > x0) & (xy_l0[:, 0] < x1)
        self.assertEqual(r["n_excluidos"], int(dentro.sum()))
        self.assertAlmostEqual(r["pct_despues"], 100 * pos[~dentro].mean(), places=6)
        self.assertAlmostEqual(r["pct_antes"], 100 * pos.mean(), places=6)
        self.assertEqual(set(r["por_denominador"]), set(MET.DENOMINADORES))
        mo = nuc.den["morfometrico"]
        self.assertAlmostEqual(r["por_denominador"]["morfometrico"]["pct_despues"],
                               100 * pos[~dentro & mo].mean(), places=6)
        self.assertEqual(len(r["por_denominador"]["ck19_erosionada"]["banda_T_despues"]), 2)
        self.assertEqual(sum(v["n"] for v in r["por_region"].values()), int((~dentro).sum()))
        self.assertIn(G.METODOS["recalculo"], r["metodos"])

    def test_pasa_la_puerta_de_n1(self):
        """Puerta de N1 real (`tools/puerta_n1.py revisar`) sobre una casa base falsa con
        overlays sintéticos: el GeoJSON pasa; con una ruta en properties, no."""
        casa = os.path.join(self.tmp, "casa")
        copia = os.path.join(self.tmp, "arbol", "tools")
        os.makedirs(os.path.join(casa, "tools", "state", "borde"))
        os.makedirs(copia)
        for f in os.listdir(TOOLS):
            if f.endswith(".py"):
                shutil.copy(os.path.join(TOOLS, f), copia)
        for nombre, d in (("perfil.local.json", {"titular": {"nombre": "Leocadia Rosa",
                                                             "apellidos": "Quintanar Tallon"}}),
                          ("nombres.local.json", {"nombres": ["eustaquio", "fulgencio"],
                                                  "lugares_ruta": ["{{CENTRO}}", "DFCI"]}),
                          ("identidad.local.json", {"ids": ["X9988776Q"]})):
            _guarda(d, os.path.join(casa, "tools", nombre))
        _guarda(["CANARIO-7f3a9c11"], os.path.join(casa, "tools", "state", "borde",
                                                         "canarios.json"))
        env = {k: v for k, v in os.environ.items() if not k.startswith("BTP_")}
        env.update(BTP_REPO=casa, BTP_HALT_FILES=os.path.join(self.tmp, "no-halt"))
        from shapely.geometry import box
        fc, *_ = self._coleccion()
        fc["features"] += G.capa_editable([box(100, 100, 400, 400)])   # capa con un dibujo
        bueno = G.escribe(os.path.join(self.tmp, "bueno.geojson"), fc)
        malo = os.path.join(self.tmp, "malo.geojson")
        fcm, *_ = self._coleccion()
        fcm["features"][0]["properties"]["name"] = "/Users/x/P-KI67.tiff"
        _guarda(fcm, malo)
        py = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable

        def corre(f):
            return subprocess.run([py, os.path.join(copia, "puerta_n1.py"), "revisar", f],
                                  capture_output=True, text=True, timeout=300, env=env,
                                  cwd=self.tmp, stdin=subprocess.DEVNULL)
        r = corre(bueno)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(corre(malo).returncode, 3)

    def test_esquema_vendorizado_intacto(self):
        import hashlib
        with open(G.ESQUEMA, "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), G.ESQUEMA_SHA256)


if __name__ == "__main__":
    unittest.main(verbosity=1)
