#!/usr/bin/env python3
"""tools/laminillas_metricas.py — lo que se mide sobre los núcleos ya detectados y registrados.

Plan «laminillas DFCI», F3 «Qué se mide» (Umbral y control de fondo, Puerta de señal, Denominador,
Puerta de p63, Nucleares, HER2, Heterogeneidad) y análisis estrella (a) mapa de vecindad y (a-bis)
regiones pobres en RE. Venv `patologia`.

ENTRADA: tablas de núcleos (`Nucleos`) de UNA lámina, ya en el marco de la referencia (µm de
P-CK19), con su valor de DAB por compartimento, su fragmento de consenso (FC) y sus tres
denominadores (CK19 erosionada, sin erosionar, morfométrico); polígonos de los FC; y el sello de la
congelación (0). Este módulo no lee píxeles ni detecta núcleos: eso es del lector único, de
InstanSeg y del detector clásico. Cada tabla lleva el nombre de SU lámina y toda medida exige que
case con el del contexto: un contexto de suelo no mide núcleos de una diana.

SELLO: T (y su régimen), la tasa de falsos positivos de P-HER2 y todos los parámetros de abajo
(semillas incluidas) se leen del sello (`laminillas_sello`). Sin sello no se mide ninguna lámina
diana; las de suelo, solo con T explícito (pre-congelación, rotulado en toda salida).

SIMETRÍA NE (plan, actualización 11): `mide_marcador` da a SYN, CHGA e {{DIANA3}} las MISMAS métricas
que a RE y Ki67 (% con IC por bloques y rango de sensibilidad, H-score, heterogeneidad); la puerta
de señal ya no esconde el % de un «focal»: solo decide si entra en el mapa y en el Moran. Un NE
«focal» añade `patron_puntos` («spatial pattern of NE-marker-positive cells (descriptive, n=1)»).
`mapa_multimarcador` lleva siempre los pares RE × SYN y Ki67 × SYN (con su motivo si no entran) y
saca del mapa lo que (iv) del módulo A marca (`laminillas_congela.fuera_del_mapa`).

RÓTULOS: los textos que salen hacia el informe son las frases EXACTAS del plan (`ROTULOS`). Ninguna
de «Nunca decir» (lo comprueba el test). Todo % es «of the scanned region». Lo que no es frase del
plan va en `declaraciones` (Métodos), nunca en `rotulos`.

DESVIACIONES DEL TEXTO DEL PLAN, DECLARADAS (van a Métodos con `metodos()`):
  · IC del % global: el plan dice «bootstrap por bloques (regiones dentro de fragmento)». Con
    dependencia espacial por encima de L, remuestrear regiones sueltas da IC2-4 veces estrechos
    (simulado: cobertura 0,36-0,61). Aquí los bloques son cuadrados CONTIGUOS de lado max(1 mm,
    2× el alcance del correlograma de los % regionales), sin fijar la composición por fragmento,
    y el intervalo es la envolvente de {percentil del bootstrap de bloques, 2.000 réplicas; t de
    Student con G−1 g.l. sobre los totales por bloque; t con la varianza de un modelo exponencial
    ajustado al semivariograma, con el alcance acotado al mayor desfase observado y fuera de la
    envolvente si no llega a su meseta (sin dependencia, la «meseta» es ruido y daba IC de ±20
    puntos)}. Cobertura simulada el 2-oct (3 FC de 6×1 mm, L = 200 µm, 150 réplicas por fila):
    sin dependencia espacial 0,98; campos gaussianos σ 150 µm 0,97; σ 300 µm 0,93. Se declara
    «approximate».
  · IC de Spearman del mapa: el bootstrap de bloques de ~1 mm sale estrecho con campos
    autocorrelados (exclusión de rho=0 en el 13-18 % con marcadores independientes). Se da la
    envolvente de ese bootstrap y del intervalo de Fisher con el tamaño efectivo de
    Clifford-Richardson-Hémon (1989) [cita de memoria, sin abrir en esta sesión: inferencia mía]
    con cuantil t; exclusión simulada 3-8 %.
  · Moran: centrado en la media de SU fragmento y permutación dentro de cada fragmento (una
    diferencia de nivel entre fragmentos no es autocorrelación); la diferencia entre fragmentos va
    en la tabla por fragmento.
  · H-score: con T > 0,4 los cortes colapsan a max(T; 0,4)/max(T; 0,6), como `laminillas_color`.
"""
import copy
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import laminillas_sello as SELLO  # noqa: E402

DENOMINADORES = ("ck19_erosionada", "ck19_sin_erosionar", "morfometrico")
LAMINAS_FUERA_DEL_MAPA = ("P-HER2", "P-HER2NEG", "P-AE1AE3")
# Eje neuroendocrino (plan, actualización 11): mismas métricas que RE y Ki67; si sale «focal»,
# además el patrón de puntos de sus positivos. Pares del mapa que no pueden faltar.
LAMINAS_NE = ("P-SYN", "P-CHGA", "P-{{DIANA3}}")
PARES_OBLIGATORIOS = (("P-RE", "P-SYN"), ("P-KI67", "P-SYN"))
# Ni marcador del mapa ni de `mide_marcador`: denominador, puerta propia o fuera por plan.
NO_MARCADOR = {"P-CK19": "CK19 is the denominator (not a target marker)",
               "P-P63": "p63 has its own gate (puerta_p63)",
               "P-HER2": "HER2: descriptive membrane ring (her2_membrana), outside the map",
               "P-HER2NEG": "HER2: descriptive membrane ring (her2_membrana), outside the map",
               "P-AE1AE3": "AE1/AE3: outside the multimarker analysis"}

PARAMS_PARTIDA = {
    "L_candidatos_um": [100, 150, 200, 300, 400],
    "L_mediana_min": 100,              # núcleos del denominador por región (FC de P-KI67)
    "L_frac_area_mediana": 0.5,        # [inferido] para la mediana solo celdas con ≥50 % en el FC
    "region_min_nucleos": 50,
    "bootstrap_replicas": 2000,
    # semillas con nombre: las dos primeras son las de `semillas` del sello (congela); las demás,
    # derivadas, se sellan aquí con su nombre [inferido: valores]
    "semilla_bootstrap": 20261001,
    "semilla_galeria_focal": 20261201,
    "semilla_moran": 20261004,
    "semilla_hotspot": 20261006,
    "semilla_mapa": 20261014,
    "interior_um": 100.0,
    "moran_min_regiones": 20,
    "moran_permutaciones": 999,
    "hotspot_diametro_um": 500.0,
    "hotspot_min_nucleos": 500,
    "hotspot_paso_um": 50.0,
    "hotspot_permutaciones": 200,      # [inferido]
    "varianza_iqr_min": 10.0,          # puntos de %
    "varianza_extremos_max": 0.5,      # fracción de regiones en ≤5 % o ≥95 %
    "varianza_extremo_bajo": 5.0,
    "varianza_extremo_alto": 95.0,
    "bloque_contiguo_um": 1000.0,
    "bloque_correlograma_corte": 0.1,  # [inferido] alcance = primer desfase con ρ < 0,1
    "bloque_factor_alcance": 2.0,      # [inferido] lado = max(1 mm, 2× alcance); simulado
    "correlograma_min_pares": 10,      # [inferido] clase con menos pares: arrastra la anterior
    "variograma_min_pares": 30,        # [inferido] clase del semivariograma con menos: fuera
    "mapa_min_regiones": 20,           # [inferido] = moran_min_regiones
    "banda_delta": 0.05,
    "banda_margen_suelo": 0.02,
    "umbral_dependiente_puntos": 10.0,
    "denominador_dependiente_puntos": 5.0,
    "senal_min_fraccion": 0.01,
    "senal_multiplo_fp": 3.0,
    "senal_regiones_frac": 0.10,
    "senal_region_min_pos": 5,
    "senal_region_min_pct": 5.0,
    "focal_galeria_max": 200,
    "focal_regiones_top": 10,
    "re_pobre_pct": 10.0,
    "re_pobre_min_n": 50,
    "control_positivo_dist_um": 500.0,
    "control_positivo_min_nucleos": 10,  # [inferido] «control positivo de RE a <500 µm»
    "hema_tolerancia": 0.20,
    "artefacto_max_frac": 0.10,          # [inferido] región «cae en artefacto»
    "hscore_cortes": [0.4, 0.6],
    "fondo_max_frac": 0.05,              # plan: «Si >5 % de esos anillos superan T»
    "fondo_percentil": 99.0,             # plan: «max(T, p99 de anillos)»
    "erosion_supervivencia_min": 0.70,   # plan, Denominador
    "densidad_parada_frac": 0.70,        # plan, Denominador: parada dura
    "p63_min_nucleos": 10,               # plan, Puerta de p63
    "p63_arco_min": 0.5,                 # plan: arco ≥50 % del perímetro
    "p63_banda_um": 15.0,                # [inferido] «periferia»: a ≤15 µm del borde
    "p63_arco_nucleo_um": 20.0,          # [inferido] arco que cubre cada núcleo p63
    # patrón de puntos de un marcador NE «focal» (actualización 11; el plan no da cifras)
    "patron_permutaciones": 999,         # [inferido] = Moran
    "semilla_patron": 20261008,          # [inferido]
    "patron_radios_um": [25.0, 50.0, 100.0, 200.0],  # [inferido] escalas del recuento de Ripley
    "patron_min_positivos": 5,           # [inferido] por debajo: recuento y posiciones
    "patron_cuantil_bajo": 100.0 / 3,    # [inferido] tercil inferior del % regional de referencia
    "patron_cuantil_alto": 200.0 / 3,    # [inferido] tercil superior
}

# Los parámetros añadidos sin cifra en el plan (van a Métodos como inferencia).
INFERIDOS = ("L_frac_area_mediana", "semilla_moran", "semilla_hotspot", "semilla_mapa",
             "hotspot_permutaciones", "bloque_correlograma_corte", "bloque_factor_alcance",
             "correlograma_min_pares", "variograma_min_pares", "mapa_min_regiones",
             "control_positivo_min_nucleos",
             "artefacto_max_frac", "p63_banda_um", "p63_arco_nucleo_um",
             "patron_permutaciones", "semilla_patron", "patron_radios_um",
             "patron_min_positivos", "patron_cuantil_bajo", "patron_cuantil_alto")

ROTULOS = {
    "region_escaneada": "of the scanned region",
    "suelo_fijo": "threshold at fixed floor (0.10 OD); measured floor p99.9 = {x}",
    "fondo_elevado": "slide background above floor; threshold raised",
    "deteccion": "detection-dependent",
    "denominador": "denominator-dependent",
    "umbral": "dependiente de umbral",
    "no_ikwg": "not the IKWG method",
    "casi_uniforme": "near-uniform at this scale: regional association not estimable",
    "descriptivo": "descriptive, single block, n=1",
    "techo": "section-to-section + registration ceiling",
    "colocalizacion": "co-localización regional en cortes seriados",
    "no_signal": "no signal",
    "quantifiable": "quantifiable",
    "focal": "focal",
    "borderline": "borderline: reported both ways",
    "er_pobre": ("ER-poor region (<10 % of nuclei above T; k/n cells, 95 % CI; not an ASCO/CAP "
                 "category)"),
    "sin_ck19": "candidate, epithelial nature not confirmed",
    "mioepitelio": ("CK19+ epithelium with myoepithelial layer or not assessable: benign or "
                    "in-situ not excluded"),
    "er_pobre_ck19": "ER-poor region within CK19+ epithelium, p63-negative",
    "hscore": "fixed, uncalibrated cut-offs",
    "re_saturado": "DAB saturated; intensity bins not informative",
    "dab_recortado": ("DAB OD clipped in {pct:.1f} % of positive cells; H-score is a lower bound "
                      "for them"),
    "fuera_ck19": ("DAB-positive nuclei outside CK19+ epithelium: {x} %; includes proliferating "
                   "lymphoid/stromal cells"),
    "denominador_ck19": ("CK19-positive epithelium on a serial section (regional prior); includes "
                         "in-situ carcinoma (~20 % reported) and any benign ducts/lobules; invasive, "
                         "in-situ and benign are not separated by any model used"),
    "her2_sin_senal": ("no membrane DAB signal above the floor measured on the second "
                       "HER2-labelled slide (‘HER2, NEG’; nature not determined: reagent control "
                       "or duplicate)"),
    "p63_no_evaluable": "not assessable (no internal positive control)",
    "p63_invasivo": "invasive-only where p63 available",
    "registro_no_verificado": "registration not verified",
    "patron_ne": "spatial pattern of NE-marker-positive cells (descriptive, n=1)",
    # No es frase del plan: solo marca salidas de láminas de suelo antes del sello (no van al informe).
    "pre_congelacion": "pre-freeze threshold (floor slides only; not sealed)",
}

# «Nunca decir» del plan (en minúsculas; el test barre todo lo que este módulo emite).
NUNCA_DECIR = ("solo mirasteis una roi", "qué hospital acertó", "perdió el re",
               "re-negative clone", "re-negative subclone", "er-low", "er-negative",
               "negative-control slide", "polaris confirma", "compatibles con la heterogeneidad",
               "lámina entera", "whole slide", "whole-slide", "tumour cells", "tumor cells",
               "celularidad", "pureza", "cellularity", "purity", "coexpresión", "co-expression",
               "% sobre carcinoma invasivo", "% of invasive carcinoma")


class MetricaError(RuntimeError):
    pass


def barre_nunca(obj):
    """Frases de «Nunca decir» que aparecen en cualquier cadena de `obj` (vacío = limpio)."""
    texto = " ".join(_cadenas(obj)).lower()
    return [f for f in NUNCA_DECIR if f in texto]


def _cadenas(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _cadenas(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _cadenas(v)


def _estado(senal):
    """Estado de la puerta de señal a partir de una cadena o de la salida de `puerta_senal`."""
    if isinstance(senal, dict):
        return senal.get("estado")
    return senal


# ── contexto: sello, T y parámetros ─────────────────────────────────────────────────────────
class Contexto:
    """Lo que necesita cualquier medida de UNA lámina: T (régimen sellado), su banda, la tasa de
    P-HER2 y los parámetros, todo del sello (compartimento según la lámina: núcleo o anillo). Sin
    sello: solo láminas de suelo y con T explícito (pre-congelación).

    `regimen`: el que rige si None; el otro régimen sellado (p. ej. «clasicas» tras el chequeo de
    GrandQC en P-KI67) sin recongelar. `eleva_por_fondo(control)` devuelve el contexto con el T de
    la lámina subido por el control de fondo (F3 «Umbral»): T_sellado se conserva y toda medida da
    las dos cifras."""

    def __init__(self, sello, lamina, T=None, compartimento=None, regimen=None):
        self.lamina = lamina
        self.sello = SELLO.exige(sello, [lamina], SELLO.SECCIONES_MODULO_B)
        self.compartimento = compartimento or SELLO.COMPARTIMENTO.get(lamina, "nucleo")
        self.control_fondo = None
        self.fp_banda = {"T_bajo": None, "T_alto": None}
        self.fp_motivo = None
        if self.sello is None:
            if T is None:
                raise MetricaError("lámina de suelo sin sello: T explícito (pre-congelación)")
            if regimen is not None:
                raise MetricaError("sin sello no hay régimen sellado")
            self.T = self.T_sellado = float(T)
            self.p999, self.rige, self.regimen = None, "explícito (pre-congelación)", None
            self._banda, self._rotulos = None, []
            self.banda_obligatoria = False
            self.p = dict(PARAMS_PARTIDA)
            self.fp = None
            self.fp_motivo = "pre-congelación: sin tasa de P-HER2 sellada"
            self.pre = True
            return
        if T is not None:
            raise MetricaError("con sello, T sale del sello: no se pasa a mano")
        u = self.sello.umbral(lamina, self.compartimento, regimen)
        self.T = self.T_sellado = u["T"]
        self._banda, self.p999 = u["banda"], u["p999"]
        self.rige, self.regimen, self._rotulos = u["rige_T"], u["regimen"], u["rotulos"]
        self.banda_obligatoria = u.get("banda_obligatoria", False)
        self.p = parametros_metricas(self.sello)
        self.pre = False
        try:
            fp = self.sello.fp_her2(self.compartimento, regimen=self.regimen)
        except SELLO.SelloInvalido as e:
            if self.regimen == self.sello.regimen_que_rige():
                raise
            self.fp, self.fp_motivo = None, str(e)
            return
        self.fp = {"k": fp.get("k"), "n": fp.get("n"), "ic95_sup": float(fp["ic95"][1])}
        for ext in ("T_bajo", "T_alto"):
            b = self.sello.fp_her2(self.compartimento, regimen=self.regimen, extremo=ext)
            if b is not None:
                self.fp_banda[ext] = {"k": b.get("k"), "n": b.get("n"),
                                      "ic95_sup": float(b["ic95"][1])}

    @property
    def elevado(self):
        return self.T > self.T_sellado

    def eleva_por_fondo(self, control):
        """Copia del contexto con el resultado de `control_fondo` aplicado (si eleva, T = T_lámina)."""
        if control.get("lamina") != self.lamina or control.get("T_sellado") != self.T_sellado:
            raise MetricaError("el control de fondo es de otra lámina o de otro T")
        c = copy.copy(self)
        c.control_fondo = dict(control)
        if control.get("eleva"):
            c.T = float(control["T_lamina"])
        return c

    def banda(self, T=None):
        """Banda de T = [max(T − 0,05; p99,9 + 0,02); T + 0,05]: la sellada para el T sellado;
        recalculada con la fórmula para un T elevado por el fondo."""
        T = self.T if T is None else T
        if self._banda and T == self.T_sellado:
            return (float(self._banda[0]), float(self._banda[1]))
        d = self.p["banda_delta"]
        lo = T - d
        if self.p999 is not None:
            lo = max(lo, float(self.p999) + self.p["banda_margen_suelo"])
        return (min(lo, T), T + d)

    def rotulos_umbral(self):
        out = list(self._rotulos)
        if self.rige == "suelo_fijo" and self.p999 is not None:
            r = ROTULOS["suelo_fijo"].format(x=_fmt(self.p999))
            if r not in out:
                out.append(r)
        if self.elevado:
            out.append(ROTULOS["fondo_elevado"])
        if self.pre:
            out.append(ROTULOS["pre_congelacion"])
        return out

    def cita(self):
        c = {"lamina": self.lamina, "T": self.T, "T_sellado": self.T_sellado,
             "banda_T": list(self.banda()), "rige": self.rige, "regimen": self.regimen,
             "compartimento": self.compartimento, "pre_congelacion": self.pre,
             "banda_obligatoria": bool(self.banda_obligatoria)}
        if self.control_fondo is not None:
            c["control_fondo"] = {k: self.control_fondo.get(k) for k in
                                  ("n", "fraccion_sobre_T", "p99", "eleva", "T_lamina", "control",
                                   "tre_dilatacion_um")}
        if self.sello is not None:
            c.update(self.sello.cita())
        return c


# Cotejo con lo que `laminillas_congela` sella con sus propios nombres: si no casa, no se mide.
_COTEJO_METRICAS = (
    (("hotspot", "diametro_mm"), "hotspot_diametro_um", 1000.0),
    (("hotspot", "min_nucleos"), "hotspot_min_nucleos", 1),
    (("hotspot", "paso_um"), "hotspot_paso_um", 1),
    (("regla_L", "candidatos_um"), "L_candidatos_um", None),
    (("regla_L", "mediana_min_nucleos"), "L_mediana_min", 1),
    (("regla_L", "region_min_nucleos"), "region_min_nucleos", 1),
    (("semillas", "bootstrap"), "semilla_bootstrap", 1),
    (("semillas", "galeria_focal"), "semilla_galeria_focal", 1),
    (("medida", "semilla_galeria_focal"), "semilla_galeria_focal", 1),
)


def coteja(d, cotejo, mios, que):
    for ruta, clave, factor in cotejo:
        v = d
        for k in ruta:
            v = v.get(k) if isinstance(v, dict) else None
        if v is None:
            continue
        esperado = mios[clave]
        if isinstance(v, list):
            ok = [float(x) for x in v] == [float(x) for x in esperado]
        elif isinstance(v, bool) or isinstance(esperado, bool):
            ok = bool(v) == bool(esperado)
        else:
            ok = abs(float(v) * (factor or 1) - float(esperado)) < 1e-9
        if not ok:
            raise SELLO.SelloInvalido("sello incoherente: %s.%s=%r frente a modulo_b.%s.%s=%r"
                                      % (ruta[0], ".".join(ruta[1:]), v, que, clave, esperado))


def parametros_metricas(sello):
    p = dict(sello.modulo_b("metricas"))
    faltan = sorted(set(PARAMS_PARTIDA) - set(p))
    if faltan:
        raise SELLO.SelloInvalido("al sello le faltan parámetros de métricas: %s" % ", ".join(faltan))
    coteja(sello.d, _COTEJO_METRICAS, p, "metricas")
    rl = (sello.d.get("regla_L") or {}).get("tre_factor")
    if rl is not None and float(rl) != 2.0:
        raise SELLO.SelloInvalido("regla_L.tre_factor sellado ≠ 2 (plan: L ≥ 2× p90)")
    cortes = (((sello.d.get("medida") or {}).get("hscore") or {}).get("cortes"))
    if cortes is not None and [float(x) for x in cortes[1:]] != [float(x) for x in
                                                                   p["hscore_cortes"]]:
        raise SELLO.SelloInvalido("sello incoherente: medida.hscore.cortes=%r frente a "
                                  "modulo_b.metricas.hscore_cortes=%r" % (cortes, p["hscore_cortes"]))
    return p


DESVIACIONES = (
    "Global % interval: contiguous square blocks of side max(1 mm, 2x the range of the regional "
    "correlogram), not single regions, no fixed per-fragment composition; envelope of the 2,000-"
    "replicate block-bootstrap percentile interval, a t interval (G-1 df) on block totals and a "
    "t interval with an exponential semivariogram-model variance (range bounded by the largest "
    "observed lag; left out when no sill is reached); approximate (simulated coverage 0.98 "
    "without spatial dependence, 0.97 and 0.93 with Gaussian fields of 150 and 300 um).",
    "Spearman interval of the neighbourhood map: envelope of the ~1 mm contiguous-block bootstrap "
    "and a Fisher interval with the Clifford-Richardson-Hemon effective sample size and t quantile "
    "(simulated exclusion of rho=0 with independent markers: 3-8 %).",
    "Moran's I centred on each fragment's mean with within-fragment permutation; between-fragment "
    "differences are reported in the per-fragment table.",
    "H-score cut-offs collapse to max(T; 0.4)/max(T; 0.6) when T > 0.4.",
    "Spatial pattern of NE-marker-positive cells: random-labelling null within fragment (positive "
    "status reassigned among all denominator cells); reference classes are the top and bottom "
    "terciles of the regional ER % and the top tercile of the regional Ki67 % (the plan does not "
    "define high and low).",
)


def metodos():
    """Declaraciones para Métodos (lo que el plan no fija o donde el código se aparta)."""
    return {"parametros_inferidos": {k: PARAMS_PARTIDA[k] for k in INFERIDOS},
            "desviaciones": list(DESVIACIONES)}


def _fmt(x, nd=3):
    return ("%%.%df" % nd) % float(x)


# ── núcleos ────────────────────────────────────────────────────────────────────────────────
class Nucleos:
    """Tabla de núcleos de UNA lámina (`lamina`, nombre opaco) en el marco de la referencia (µm).

    xy_um (N×2), dab (N; DAB medio del compartimento de la lámina), fc (N; id del FC verificado o
    None), denominadores {nombre: bool N}; opcionales: artefacto (bool N), foco_tercil (int N,
    1 = bajo), hema (N; hematoxilina nuclear), xy_l0 (N×2, px L0 de su lámina, para el GeoJSON),
    area_um2 (N), dab_anillo (N; DAB medio del anillo de 3 µm, para el control de fondo de los
    marcadores nucleares), recorte (N; fracción de píxeles recortados en intensidad en el
    compartimento de `dab`: regla de artefacto v2, se anota y no excluye; en `hscore`, cota
    inferior)."""

    def __init__(self, lamina, xy_um, dab, fc, denominadores, artefacto=None, foco_tercil=None,
                 hema=None, xy_l0=None, area_um2=None, dab_anillo=None, recorte=None):
        if not isinstance(lamina, str) or not lamina:
            raise MetricaError("Nucleos: el nombre de su lámina es obligatorio")
        self.lamina = lamina
        self.xy = np.asarray(xy_um, float).reshape(-1, 2)
        n = len(self.xy)
        self.dab = np.asarray(dab, float).reshape(n)
        self.fc = np.empty(n, dtype=object)
        self.fc[:] = list(fc) if n else []
        self.den = {k: np.asarray(v, bool).reshape(n) for k, v in denominadores.items()}
        self.artefacto = (np.zeros(n, bool) if artefacto is None
                          else np.asarray(artefacto, bool).reshape(n))
        self.foco = None if foco_tercil is None else np.asarray(foco_tercil).reshape(n)
        self.hema = None if hema is None else np.asarray(hema, float).reshape(n)
        self.xy_l0 = None if xy_l0 is None else np.asarray(xy_l0, float).reshape(n, 2)
        self.area = None if area_um2 is None else np.asarray(area_um2, float).reshape(n)
        self.dab_anillo = None if dab_anillo is None else np.asarray(dab_anillo, float).reshape(n)
        self.recorte = None if recorte is None else np.asarray(recorte, float).reshape(n)

    def __len__(self):
        return len(self.xy)

    def en_fc(self):
        return np.fromiter((f is not None for f in self.fc), bool, count=len(self.fc))

    def mascara(self, denominador):
        if denominador not in self.den:
            raise MetricaError("denominador desconocido: %s" % denominador)
        return self.den[denominador] & ~self.artefacto & self.en_fc()


def _comprueba(nuc, ctx):
    if not isinstance(nuc, Nucleos):
        raise MetricaError("se esperaba una tabla Nucleos")
    if nuc.lamina != ctx.lamina:
        raise MetricaError("los núcleos son de %s y el contexto (sello, T) de %s: no se mide"
                           % (nuc.lamina, ctx.lamina))


def _subconjunto(nuc, m):
    return Nucleos(nuc.lamina, nuc.xy[m], nuc.dab[m], nuc.fc[m],
                   {k: v[m] for k, v in nuc.den.items()}, artefacto=nuc.artefacto[m],
                   dab_anillo=None if nuc.dab_anillo is None else nuc.dab_anillo[m],
                   recorte=None if nuc.recorte is None else nuc.recorte[m])


# ── rejilla y regiones ─────────────────────────────────────────────────────────────────────
def rejilla(fcs, L, origen=(0.0, 0.0)):
    """Rejilla ÚNICA anclada a P-CK19 (`origen` = su (0, 0) en µm): celdas de lado L ∩ FC.
    `fcs`: {id: polígono shapely en µm}. Devuelve {(fc, i, j): región}."""
    from shapely.geometry import box
    ox, oy = origen
    out = {}
    for fid, pol in fcs.items():
        if pol is None or pol.is_empty:
            continue
        x0, y0, x1, y1 = pol.bounds
        for i in range(int(math.floor((x0 - ox) / L)), int(math.floor((x1 - ox) / L)) + 1):
            for j in range(int(math.floor((y0 - oy) / L)), int(math.floor((y1 - oy) / L)) + 1):
                celda = box(ox + i * L, oy + j * L, ox + (i + 1) * L, oy + (j + 1) * L)
                g = celda.intersection(pol)
                if g.is_empty or g.area <= 0:
                    continue
                c = g.centroid
                out[(fid, i, j)] = {"fc": fid, "i": i, "j": j, "L": L, "origen": (ox, oy),
                                    "geom": g,
                                    "area_um2": float(g.area), "frac_area": float(g.area / L ** 2),
                                    "centro": (float(c.x), float(c.y)),
                                    "dist_borde_um": float(pol.boundary.distance(c))}
    return out


def _L_origen(regiones):
    r = next(iter(regiones.values()))
    return r["L"], r.get("origen", (0.0, 0.0))


def claves_de(nuc, regiones):
    """Clave de región (fc, i, j) de cada núcleo según la rejilla de `regiones` (o None)."""
    if not regiones:
        return [None] * len(nuc)
    L, origen = _L_origen(regiones)
    return asigna(nuc, L, origen)


def asigna(nuc, L, origen=(0.0, 0.0)):
    ox, oy = origen
    i = np.floor((nuc.xy[:, 0] - ox) / L).astype(int)
    j = np.floor((nuc.xy[:, 1] - oy) / L).astype(int)
    return [(f, a, b) if f is not None else None for f, a, b in zip(nuc.fc, i, j)]


def _indices_region(nuc, regiones):
    """Índice (en el orden de `regiones`) de la región de cada núcleo; −1 si ninguna."""
    pos = {c: q for q, c in enumerate(regiones)}
    return np.fromiter((pos.get(c, -1) if c is not None else -1 for c in claves_de(nuc, regiones)),
                       int, count=len(nuc))


def _cuenta(nuc, regiones, T, denominador):
    idx = _indices_region(nuc, regiones)
    m = nuc.mascara(denominador)
    pos = nuc.dab > T
    R = len(regiones)
    ok = idx >= 0
    n_todos = np.bincount(idx[ok], minlength=R) if R else np.zeros(0, int)
    n = np.bincount(idx[ok & m], minlength=R) if R else np.zeros(0, int)
    k = np.bincount(idx[ok & m & pos], minlength=R) if R else np.zeros(0, int)
    out = {}
    for q, (c, r) in enumerate(regiones.items()):
        d = dict(r, n=int(n[q]), k=int(k[q]), n_todos=int(n_todos[q]), T=float(T),
                 denominador=denominador, lamina=nuc.lamina)
        d["pct"] = 100.0 * d["k"] / d["n"] if d["n"] else float("nan")
        d["densidad_mm2"] = d["n_todos"] / (d["area_um2"] / 1e6) if d["area_um2"] else 0.0
        out[c] = d
    return out


def cuenta_regiones(nuc, regiones, ctx, denominador):
    """k (sobre el T del contexto) y n (denominador) por región; además todos los núcleos (para la
    densidad). Los núcleos tienen que ser de la lámina del contexto."""
    _comprueba(nuc, ctx)
    return _cuenta(nuc, regiones, ctx.T, denominador)


def _regiones_de(regiones, ctx, denominador=None):
    """Comprueba que las regiones se contaron con ESTE contexto (lámina y T) y denominador."""
    for r in regiones.values():
        if r.get("lamina") != ctx.lamina or abs(float(r.get("T", np.nan)) - ctx.T) > 1e-12:
            raise MetricaError("regiones contadas con otra lámina u otro T: recontar con "
                               "cuenta_regiones(nuc, rejilla, ctx, denominador)")
        if denominador is not None and r.get("denominador") != denominador:
            raise MetricaError("regiones contadas con otro denominador (%s)" % r.get("denominador"))
        break


def validas(regiones, p):
    """Regiones con ≥ region_min_nucleos del denominador y % finito."""
    return {c: r for c, r in regiones.items()
            if r["n"] >= p["region_min_nucleos"] and math.isfinite(r["pct"])}


def excluye_pequenas(regiones, p):
    """Regiones con < region_min_nucleos del denominador: fuera, con su fracción informada."""
    minimo = p["region_min_nucleos"]
    dentro = {c: r for c, r in regiones.items() if r["n"] >= minimo}
    fuera = [r for r in regiones.values() if r["n"] < minimo]
    n_tot = sum(r["n"] for r in regiones.values())
    return dentro, {"regiones_excluidas": len(fuera), "regiones_total": len(regiones),
                    "fraccion_regiones_excluidas": len(fuera) / len(regiones) if regiones else 0.0,
                    "fraccion_nucleos_excluidos": (sum(r["n"] for r in fuera) / n_tot
                                                   if n_tot else 0.0),
                    "umbral_nucleos": minimo}


def elige_L(nuc_ki67, fcs, p90_tre_um, ctx, denominador, origen=(0.0, 0.0)):
    """Regla de L (sellada en (0)): el menor de {100, 150, 200, 300, 400} µm con L ≥ 2× p90 del
    TRE del par y mediana ≥100 núcleos del denominador por región en el FC de P-KI67. Solo usa
    densidad nuclear y TRE (nunca positividad)."""
    _comprueba(nuc_ki67, ctx)
    if ctx.lamina != "P-KI67":
        raise MetricaError("la regla de L se aplica en el FC de P-KI67")
    p = ctx.p
    tabla = []
    elegido = None
    for L in p["L_candidatos_um"]:
        reg = rejilla(fcs, float(L), origen)
        cnt = _cuenta(nuc_ki67, reg, np.inf, denominador)
        ns = [r["n"] for r in cnt.values() if r["frac_area"] >= p["L_frac_area_mediana"]]
        med = float(np.median(ns)) if ns else 0.0
        ok_tre = p90_tre_um is not None and L >= 2 * p90_tre_um
        ok_n = med >= p["L_mediana_min"]
        tabla.append({"L_um": L, "mediana_nucleos": med, "cumple_tre": bool(ok_tre),
                      "cumple_densidad": bool(ok_n)})
        if elegido is None and ok_tre and ok_n:
            elegido = float(L)
    return {"L_um": elegido, "tabla": tabla,
            "motivo": None if elegido else "ningún L de la lista cumple TRE y densidad"}


# ── estadística ────────────────────────────────────────────────────────────────────────────
def clopper_pearson(k, n, alfa=0.05):
    from scipy.stats import beta
    if n <= 0:
        return (float("nan"), float("nan"))
    lo = 0.0 if k == 0 else float(beta.ppf(alfa / 2, k, n - k + 1))
    hi = 1.0 if k == n else float(beta.ppf(1 - alfa / 2, k + 1, n - k))
    return (lo, hi)


def _spearman(a, b):
    from scipy.stats import spearmanr
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    return float(spearmanr(a, b)[0])


def _vecinos_reina(claves):
    """Pesos binarios de reina dentro del mismo FC (dispersa)."""
    from scipy.sparse import csr_matrix
    idx = {c: n for n, c in enumerate(claves)}
    filas, cols = [], []
    for c, n in idx.items():
        f, i, j = c
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if di == dj == 0:
                    continue
                m = idx.get((f, i + di, j + dj))
                if m is not None:
                    filas.append(n)
                    cols.append(m)
    return csr_matrix((np.ones(len(filas)), (filas, cols)), shape=(len(claves), len(claves)))


def moran(valores, W, permutaciones, rng, grupos=None):
    """I de Moran (pesos de reina dentro del mismo FC) y p por permutación. Con `grupos` (el FC de
    cada región), los valores se centran en la media de SU grupo y se permutan DENTRO de él: una
    diferencia de nivel entre fragmentos no cuenta como autocorrelación."""
    x = np.asarray(valores, float)
    n = len(x)
    S0 = float(W.sum())
    if n < 3 or S0 == 0 or not np.all(np.isfinite(x)):
        return {"I": float("nan"), "p_permutacion": float("nan")}
    g = np.zeros(n, int) if grupos is None else np.unique(np.asarray(grupos, str),
                                                          return_inverse=True)[1]
    z = x.copy()
    for k in np.unique(g):
        z[g == k] -= z[g == k].mean()
    zz = float(z @ z)
    if zz <= 1e-12:
        return {"I": float("nan"), "p_permutacion": float("nan")}
    I = (n / S0) * float(z @ (W @ z)) / zz
    Z = np.empty((n, permutaciones))
    for k in np.unique(g):
        miembros = np.nonzero(g == k)[0]
        orden = np.argsort(rng.random((permutaciones, len(miembros))), axis=1)
        Z[miembros, :] = z[miembros][orden].T
    Ip = (n / S0) * np.sum(Z * (W @ Z), 0) / zz
    p = (1 + np.sum(Ip >= I - 1e-12)) / (1 + permutaciones)
    return {"I": float(I), "p_permutacion": float(p), "esperado_nulo": float(-1 / (n - 1)),
            "permutaciones": int(permutaciones),
            "nulo": "within-fragment permutation; values centred on their fragment mean"}


def heterogeneidad(regiones, ctx, senal):
    """Tabla por fragmento; % por región con IC binomial (Clopper-Pearson) y distancia al borde;
    CV y Moran's I por permutación (≥20 regiones), dos veces: todo e interior (>100 µm). Moran solo
    si la puerta de señal da «quantifiable» (plan, actualización 11: la puerta decide el mapa y el
    Moran). Solo regiones con ≥50 núcleos y % finito."""
    p = ctx.p
    _regiones_de(regiones, ctx)
    regiones_ok = validas(regiones, p)
    estado = _estado(senal)
    rng = np.random.default_rng(int(p["semilla_moran"]))
    filas = []
    for c, r in sorted(regiones_ok.items(), key=lambda kv: kv[0]):
        lo, hi = clopper_pearson(r["k"], r["n"])
        filas.append({"region": "%s:%d,%d" % c, "fc": r["fc"], "k": r["k"], "n": r["n"],
                      "pct": r["pct"], "ic95": [100 * lo, 100 * hi],
                      "dist_borde_um": r["dist_borde_um"],
                      "interior": r["dist_borde_um"] > p["interior_um"]})
    out = {"regiones": filas, "regiones_descartadas": len(regiones) - len(regiones_ok),
           "senal": estado, "umbral": ctx.cita(), "rotulos": list(ctx.rotulos_umbral())}
    for nombre, filtro in (("todo", lambda r: True),
                           ("interior", lambda r: r["dist_borde_um"] > p["interior_um"])):
        sel = {c: r for c, r in regiones_ok.items() if filtro(r)}
        claves = sorted(sel)
        pct = np.array([sel[c]["pct"] for c in claves])
        bloque = {"n_regiones": len(claves)}
        bloque["cv"] = (float(pct.std(ddof=1) / pct.mean()) if len(claves) >= 2 and pct.mean() > 0
                        else float("nan"))

        def _moran_de(cl):
            if estado != ROTULOS["quantifiable"]:
                return {"I": None, "motivo": "signal gate: %s" % estado}
            if len(cl) < p["moran_min_regiones"]:
                return {"I": None, "motivo": "<%d regiones" % p["moran_min_regiones"]}
            return moran([sel[c]["pct"] for c in cl], _vecinos_reina(cl),
                         int(p["moran_permutaciones"]), rng, grupos=[sel[c]["fc"] for c in cl])
        bloque["moran"] = _moran_de(claves)
        porfc = {}
        for fid in sorted({sel[c]["fc"] for c in claves}, key=str):
            cl = [c for c in claves if sel[c]["fc"] == fid]
            k, n = sum(sel[c]["k"] for c in cl), sum(sel[c]["n"] for c in cl)
            v = np.array([sel[c]["pct"] for c in cl])
            porfc[fid] = {"n_regiones": len(cl), "k": k, "n": n,
                          "pct": 100.0 * k / n if n else float("nan"),
                          "cv": float(v.std(ddof=1) / v.mean()) if len(v) > 1 and v.mean() > 0
                          else float("nan"),
                          "moran": _moran_de(cl)}
        bloque["por_fragmento"] = porfc
        out[nombre] = bloque
    return out


# ── % global: bloques contiguos, IC y rango de sensibilidad ─────────────────────────────────
def correlograma(valores, centros, fcs, L, min_pares):
    """ρ(d) de los valores regionales (centrados en la media global) por clases de distancia de
    anchura L, SOLO entre regiones del mismo FC. Clase con < `min_pares` pares: arrastra la
    anterior. Devuelve (lista [(d_um, ρ)], matriz de clases n×n o None)."""
    from scipy.spatial.distance import pdist, squareform
    v = np.asarray(valores, float)
    n = len(v)
    if n < 3:
        return [], None
    z = v - v.mean()
    var = float(np.mean(z * z))
    if var <= 0:
        return [], None
    D = squareform(pdist(np.asarray(centros, float)))
    f = np.asarray([str(x) for x in fcs])
    mismo = f[:, None] == f[None, :]
    cls = np.floor(D / L + 0.5).astype(int)
    cls[~mismo] = -1
    np.fill_diagonal(cls, 0)
    prod = z[:, None] * z[None, :]
    out, rho = [], 0.0
    maxc = int(cls.max()) if n else 0
    tabla = np.zeros(maxc + 1)
    tabla[0] = 1.0
    for c in range(1, maxc + 1):
        m = cls == c
        if m.sum() / 2 >= min_pares:
            rho = float(prod[m].mean() / var)
        tabla[c] = rho
        out.append((c * L, rho))
    return out, (cls, tabla)


def _alcance(valores, centros, fcs, L, p):
    cg, _ = correlograma(valores, centros, fcs, L, p["correlograma_min_pares"])
    for d, r in cg:
        if r < p["bloque_correlograma_corte"]:
            return d, cg
    return (cg[-1][0] if cg else p["bloque_contiguo_um"]), cg


def bloques_contiguos(regiones, lado):
    """Bloque (fc, bi, bj) de cada región: cuadrados contiguos de `lado` µm en la rejilla
    anclada a P-CK19, dentro de su fragmento."""
    out = {}
    for c, r in regiones.items():
        ox, oy = r.get("origen", (0.0, 0.0))
        cx, cy = r["centro"]
        out[c] = (r["fc"], int(math.floor((cx - ox) / lado)), int(math.floor((cy - oy) / lado)))
    return out


def var_variograma(regiones, L, p):
    """Varianza del % global (en proporción²) con un modelo de covarianza espacial: semivariograma
    empírico de las proporciones regionales (pares del mismo FC, clases de anchura L, sin la parte
    binomial), ajuste exponencial γ(h) = c·(1 − e^(−h/a)) por mínimos cuadrados ponderados, y
    Var = nᵀ C n / (Σn)² con C = c·e^(−d/a) dentro del FC (0 entre FC) más la binomial en la
    diagonal. El semivariograma no necesita la media: capta la varianza del proceso que la
    covarianza centrada en la muestra pierde. Devuelve (var, {c, a}) o (None, motivo)."""
    from scipy.optimize import least_squares
    from scipy.spatial.distance import pdist, squareform
    cl = sorted(regiones, key=str)
    k = np.array([regiones[c]["k"] for c in cl], float)
    n = np.array([regiones[c]["n"] for c in cl], float)
    if len(cl) < 3:
        return None, "fewer than 3 regions"
    C = np.array([regiones[c]["centro"] for c in cl], float)
    f = np.array([str(regiones[c]["fc"]) for c in cl])
    pr = k / n
    R = k.sum() / n.sum()
    D = squareform(pdist(C))
    mismo = f[:, None] == f[None, :]
    iu = np.triu_indices(len(pr), 1)
    d, s = D[iu], mismo[iu]
    g2 = (0.5 * (pr[:, None] - pr[None, :]) ** 2)[iu]
    binom = (0.5 * R * (1 - R) * (1 / n[:, None] + 1 / n[None, :]))[iu]
    cls = np.floor(d / L + 0.5).astype(int)
    hs, gs, ws = [], [], []
    for c in range(1, int(cls[s].max()) + 1 if s.any() else 1):
        m = s & (cls == c)
        if m.sum() < p["variograma_min_pares"]:
            continue
        hs.append(c * L)
        gs.append(float(np.mean(g2[m] - binom[m])))
        ws.append(float(m.sum()))
    if len(hs) < 3:
        return None, "fewer than 3 semivariogram classes"
    hs, gs, ws = map(np.array, (hs, gs, ws))
    # El alcance no puede pasar del mayor desfase con datos: un semivariograma que no llega a su
    # meseta dentro de lo observado no la identifica, y con datos sin dependencia (γ ≈ 0) un
    # alcance libre extrapola una recta casi plana a una meseta arbitraria (varianza absurda).
    hmax = float(hs.max())
    ajuste = least_squares(lambda th: np.sqrt(ws) * (th[0] * (1 - np.exp(-hs / th[1])) - gs),
                           [max(1e-6, float(np.median(gs[-5:]))), min(2 * L, hmax)],
                           bounds=([0.0, L / 4], [1.0, hmax]))
    c0, a = (float(x) for x in ajuste.x)
    Cov = c0 * np.exp(-D / a) * mismo
    Cov[np.diag_indices_from(Cov)] = c0 + R * (1 - R) / n
    return float(n @ Cov @ n) / float(n.sum()) ** 2, {
        "meseta": c0, "alcance_um": a, "alcance_en_el_limite": bool(a >= 0.99 * hmax),
        "desfase_max_um": hmax}


def ic_bloques(regiones, p, rng):
    """IC del % global con dependencia espacial (ver «Desviaciones» en la cabecera): bloques
    contiguos de lado max(bloque_contiguo_um, factor × alcance del correlograma); intervalo =
    envolvente de {percentil del bootstrap de bloques sin estratificar; t con G−1 g.l. sobre los
    totales por bloque; t con G−1 g.l. y la varianza del modelo de semivariograma}."""
    from scipy.stats import t as tdist
    claves = sorted(regiones, key=str)
    k = np.array([regiones[c]["k"] for c in claves], float)
    n = np.array([regiones[c]["n"] for c in claves], float)
    pct = 100.0 * k / n
    L = regiones[claves[0]]["L"]
    alcance, cg = _alcance(pct, [regiones[c]["centro"] for c in claves],
                           [regiones[c]["fc"] for c in claves], L, p)
    lado = max(float(p["bloque_contiguo_um"]),
               math.ceil(p["bloque_factor_alcance"] * alcance / L) * L)
    bl = bloques_contiguos({c: regiones[c] for c in claves}, lado)
    ub = sorted(set(bl.values()), key=str)
    pos = {u: q for q, u in enumerate(ub)}
    g = np.array([pos[bl[c]] for c in claves])
    G = len(ub)
    kg, ng = np.bincount(g, k, G), np.bincount(g, n, G)
    est = 100.0 * k.sum() / n.sum()
    out = {"lado_bloque_um": lado, "alcance_um": float(alcance), "n_bloques": G,
           "correlograma": [[float(d), float(r)] for d, r in cg[:30]]}
    if G < 2:
        out.update({"bootstrap_bloques": [float("nan")] * 2, "t_bloques": [float("nan")] * 2,
                    "intervalo": [float("nan")] * 2,
                    "nota": "interval not estimable: fewer than 2 contiguous blocks"})
        return out
    B = int(p["bootstrap_replicas"])
    sel = rng.integers(0, G, (B, G))
    reps = 100.0 * kg[sel].sum(1) / ng[sel].sum(1)
    boot = [float(np.percentile(reps, 2.5)), float(np.percentile(reps, 97.5))]
    R = est / 100.0
    se = math.sqrt(G / (G - 1) * float(np.sum((kg - R * ng) ** 2))) / float(ng.sum())
    tq = float(tdist.ppf(0.975, G - 1))
    tint = [100 * (R - tq * se), 100 * (R + tq * se)]
    var_m, info_m = var_variograma({c: regiones[c] for c in claves}, L, p)
    cands = {"bootstrap_bloques": boot, "t_bloques": tint}
    if var_m is not None and not info_m["alcance_en_el_limite"]:
        sm = math.sqrt(max(var_m, 0.0))
        cands["t_variograma"] = [100 * (R - tq * sm), 100 * (R + tq * sm)]
        out["variograma"] = info_m
    elif var_m is not None:
        # sin meseta dentro de los desfases observados el modelo no identifica el alcance (con
        # datos sin dependencia, γ ≈ 0 y la «meseta» es ruido): no entra en la envolvente
        out["variograma"] = dict(info_m, motivo="no sill within the observed lags: range not "
                                 "identified; not used in the envelope")
    else:
        out["variograma"] = {"motivo": info_m}
    lo = min(cands.items(), key=lambda kv: kv[1][0])
    hi = max(cands.items(), key=lambda kv: kv[1][1])
    out.update(cands)
    out.update({"intervalo": [lo[1][0], hi[1][1]],
                "origen_intervalo": {"inferior": lo[0], "superior": hi[0]},
                "nota": ("approximate interval (spatial dependence): envelope of a %d-block "
                         "bootstrap (contiguous blocks of %g um), a t interval on block totals "
                         "and a semivariogram-model variance" % (G, lado))})
    return out


def _pct(nuc, T, den):
    m = nuc.mascara(den)
    n = int(m.sum())
    return (100.0 * np.sum(nuc.dab[m] > T) / n) if n else float("nan"), n


def _usa_ck19(denominador, nuc=None):
    if denominador.startswith("ck19"):
        return True
    if denominador == "principal" and nuc is not None:
        return bool(getattr(nuc, "principal_usa_ck19", True))
    return False


def porcentaje_global(nuc, regiones, ctx, denominador, nuc_clasico=None, deteccion_ok=None,
                      ck19_caido=False, solo_mapa=()):
    """% global sobre las regiones incluidas, con IC por bloques contiguos y, aparte, rango de
    sensibilidad: banda de T, denominador (SIEMPRE las tres cifras: CK19 erosionada, sin erosionar
    y morfométrica) e InstanSeg vs detector clásico. Se informa el intervalo más ancho y su origen.
    El IC binomial NO se usa aquí (solo por región).

    `ck19_caido` (registro <50 %): solo cabe el denominador morfométrico, y se declara.
    `solo_mapa`: FC con parada dura de densidad (`parada_densidad`): fuera de la cifra."""
    _comprueba(nuc, ctx)
    p = ctx.p
    T = ctx.T
    if ck19_caido:
        if denominador != "morfometrico":
            raise MetricaError("sin registro (CK19 caído) solo cabe el denominador morfométrico")
        requeridos = ("morfometrico",)
    else:
        requeridos = DENOMINADORES
    faltan = [d for d in requeridos if d not in nuc.den]
    if faltan:
        raise MetricaError("siempre tres cifras (CK19 erosionada, sin erosionar, morfométrica): "
                           "faltan %s" % ", ".join(faltan))
    if regiones:
        _regiones_de(regiones, ctx, denominador)
    declaraciones = []
    if ck19_caido:
        declaraciones.append("CK19 denominator dropped (registration <50 % of the consensus "
                             "area): morphometric denominator only")
    solo_mapa = set(solo_mapa or ())
    if solo_mapa:
        declaraciones.append("fragments with nuclear density <70 %% of the series median: map "
                             "only, outside this figure (%s)" % ", ".join(sorted(map(str, solo_mapa))))
    regiones = {c: r for c, r in validas(regiones, p).items() if r["fc"] not in solo_mapa}
    rotulos = list(ctx.rotulos_umbral())
    if _usa_ck19(denominador, nuc):
        rotulos.append(ROTULOS["denominador_ck19"])
    base = {"alcance": ROTULOS["region_escaneada"], "umbral": ctx.cita(), "rotulos": rotulos,
            "declaraciones": declaraciones, "denominador": denominador}
    if not regiones:
        base.update({"pct": float("nan"), "k": 0, "n": 0, "n_regiones": 0, "intervalos": {},
                     "pct_por_denominador": {}, "pct_detector_clasico": None, "mas_ancho": None,
                     "primaria": "pct"})
        base["declaraciones"].append("no region with >=%d nuclei of the denominator: no figure"
                                     % p["region_min_nucleos"])
        return base
    claves = sorted(regiones, key=str)
    k = np.array([regiones[c]["k"] for c in claves], float)
    n = np.array([regiones[c]["n"] for c in claves], float)
    est = 100.0 * k.sum() / n.sum()
    rng = np.random.default_rng(int(p["semilla_bootstrap"]))
    ic = ic_bloques(regiones, p, rng)
    # sensibilidad (sobre los mismos núcleos de las regiones incluidas)
    sub = _subconjunto(nuc, _en_regiones(nuc, regiones))
    lo_T, hi_T = ctx.banda()
    pct_lo, _ = _pct(sub, lo_T, denominador)
    pct_hi, _ = _pct(sub, hi_T, denominador)
    intervalos = {"bloques": ic["intervalo"], "banda_T": [min(pct_lo, pct_hi, est),
                                                          max(pct_lo, pct_hi, est)]}
    if abs(pct_hi - pct_lo) > p["umbral_dependiente_puntos"]:
        rotulos.append(ROTULOS["umbral"])
    dens = {}
    for d in requeridos:
        dens[d] = est if d == denominador else _pct(sub, T, d)[0]
    if denominador not in dens:
        dens[denominador] = est
    vals = [v for v in dens.values() if v == v]
    if len(vals) > 1:
        intervalos["denominador"] = [min(vals), max(vals)]
        if max(vals) - min(vals) > p["denominador_dependiente_puntos"]:
            rotulos.append(ROTULOS["denominador"])
    pct_clasico = None
    if nuc_clasico is not None:
        _comprueba(nuc_clasico, ctx)
        sub_c = _subconjunto(nuc_clasico, _en_regiones(nuc_clasico, regiones))
        pct_clasico = _pct(sub_c, T, denominador)[0]
        intervalos["detector"] = [min(est, pct_clasico), max(est, pct_clasico)]
    if deteccion_ok is False:
        rotulos.append(ROTULOS["deteccion"])
    finitos = {o: v for o, v in intervalos.items() if all(map(math.isfinite, v))}
    ancho = max(finitos.items(), key=lambda kv: kv[1][1] - kv[1][0]) if finitos else (None, None)
    base.update({"pct": est, "k": int(k.sum()), "n": int(n.sum()), "n_regiones": len(claves),
                 "intervalos": intervalos, "ic_bloques": ic, "pct_por_denominador": dens,
                 "pct_detector_clasico": pct_clasico,
                 "mas_ancho": {"origen": ancho[0], "intervalo": ancho[1]},
                 "primaria": ("rango [InstanSeg; detector clásico]" if deteccion_ok is False
                              else "pct")})
    base["declaraciones"].append(ic["nota"])
    if ctx.elevado:
        base["dos_cifras"] = {"T_sellado": _pct(sub, ctx.T_sellado, denominador)[0],
                              "T_lamina": est}
    return base


def _en_regiones(nuc, regiones):
    return _indices_region(nuc, regiones) >= 0


def fuera_ck19(nuc, ctx, denominador_amplio="ck19_sin_erosionar"):
    """Fracción de núcleos sobre T FUERA del epitelio CK19+: dato, nunca mueve T."""
    _comprueba(nuc, ctx)
    m = ~nuc.den[denominador_amplio] & ~nuc.artefacto & nuc.en_fc()
    n = int(m.sum())
    x = 100.0 * np.sum(nuc.dab[m] > ctx.T) / n if n else float("nan")
    return {"pct": x, "n": n, "rotulo": ROTULOS["fuera_ck19"].format(x=_fmt(x, 1)),
            "umbral": ctx.cita()}


# ── control de fondo por lámina (F3 «Umbral») ─────────────────────────────────────────────────
def control_fondo(nuc, ctx, fuera_ck19_dilatada, tre_dilatacion_um):
    """Control de fondo por lámina y compartimento, en fragmentos registrados; no en P-CK19.

    Nucleares (RE, RP, RA, Ki67, {{DIANA3}}): DAB medio del ANILLO de 3 µm (`nuc.dab_anillo`) de los
    núcleos fuera de la máscara CK19 dilatada 1× TRE. Citoplasmáticos (SYN, CHGA): DAB del
    compartimento de las células fuera de la máscara CK19 sin erosionar y dilatada 1× TRE. Si >5 %
    supera T: T de la lámina = max(T, p99 del control), «slide background above floor; threshold
    raised», y toda medida da las dos cifras (`Contexto.eleva_por_fondo`).
    `fuera_ck19_dilatada` (bool N) la calcula quien dilata la máscara CK19 registrada."""
    _comprueba(nuc, ctx)
    if ctx.lamina == "P-CK19":
        raise MetricaError("el control de fondo no se aplica a P-CK19 (es el denominador)")
    p = ctx.p
    fuera = np.asarray(fuera_ck19_dilatada, bool).reshape(len(nuc))
    sel = fuera & ~nuc.artefacto & nuc.en_fc()
    if ctx.compartimento == "nucleo":
        if nuc.dab_anillo is None:
            raise MetricaError("marcador nuclear: el control de fondo necesita el DAB del anillo")
        vals = nuc.dab_anillo[sel]
        control = "3 um ring of nuclei outside the CK19 mask dilated by 1x TRE"
    else:
        vals = nuc.dab[sel]
        control = "cells outside the non-eroded CK19 mask dilated by 1x TRE"
    vals = vals[np.isfinite(vals)]
    n = int(len(vals))
    out = {"lamina": ctx.lamina, "T_sellado": ctx.T_sellado, "n": n, "control": control,
           "tre_dilatacion_um": float(tre_dilatacion_um), "compartimento": ctx.compartimento}
    if not n:
        out.update({"fraccion_sobre_T": float("nan"), "p99": None, "eleva": False,
                    "T_lamina": ctx.T_sellado, "rotulo": None,
                    "motivo": "no control objects outside CK19"})
        return out
    frac = float(np.mean(vals > ctx.T_sellado))
    p99 = float(np.percentile(vals, p["fondo_percentil"]))
    eleva = frac > p["fondo_max_frac"]
    out.update({"fraccion_sobre_T": frac, "p99": p99, "eleva": bool(eleva),
                "T_lamina": max(ctx.T_sellado, p99) if eleva else ctx.T_sellado,
                "rotulo": ROTULOS["fondo_elevado"] if eleva else None})
    return out


# ── H-score ──────────────────────────────────────────────────────────────────────────────────
def hscore(nuc, ctx, denominador, marcador=None):
    """H-score digital con cortes T/0,4/0,6 («fixed, uncalibrated cut-offs»), con histograma que
    suma n (incluye DAB negativo). Con T > 0,4 los cortes colapsan a max(T; 0,4)/max(T; 0,6), como
    `laminillas_color.hscore`, y se dice. En RE, «DAB saturated; intensity bins not informative»."""
    _comprueba(nuc, ctx)
    m = nuc.mascara(denominador)
    v = nuc.dab[m]
    fin = np.isfinite(v)
    rec = None if nuc.recorte is None else np.nan_to_num(nuc.recorte[m][fin]) > 0
    v = v[fin]
    rot = [ROTULOS["hscore"]] + list(ctx.rotulos_umbral())
    # Regla de artefacto v2 (2-oct): el DAB recortado en intensidad ya no saca la célula; su OD
    # está acotado por debajo, así que el H-score de esas células es una cota inferior. Se dice
    # con el % de positivas con recorte (positividad: no cambia).
    frac_rec = None
    if rec is not None and len(v):
        pos = v > ctx.T
        frac_rec = float(np.mean(rec[pos])) if pos.any() else 0.0
        if frac_rec > 0:
            rot.append(ROTULOS["dab_recortado"].format(pct=100 * frac_rec))
    if ctx.lamina == "P-RE" or marcador in ("RE", "ER", "P-RE"):
        rot.append(ROTULOS["re_saturado"])
    n = len(v)
    if not n:
        return {"hscore": float("nan"), "n": 0, "rotulos": rot, "umbral": ctx.cita()}

    def _h(T):
        c0, c1, c2 = (float(x) for x in (T, *ctx.p["hscore_cortes"]))
        c1, c2 = max(T, c1), max(T, c2)
        f1 = float(np.mean((v > T) & (v <= c1)))
        f2 = float(np.mean((v > c1) & (v <= c2)))
        f3 = float(np.mean(v > c2))
        return 100 * (f1 + 2 * f2 + 3 * f3), [f1, f2, f3], [T, c1, c2]
    H, fr, cortes = _h(ctx.T)
    lo = min(0.0, float(v.min()))
    hi = max(1.5, float(v.max()))
    hist, bordes = np.histogram(v, bins=np.linspace(lo, hi, 31))
    if int(hist.sum()) != n:
        raise MetricaError("el histograma del H-score no suma n")
    out = {"hscore": H, "n": n, "fracciones": fr, "cortes": cortes,
           "cortes_colapsados": bool(ctx.T > float(ctx.p["hscore_cortes"][0])),
           "histograma": {"cuentas": hist.tolist(), "bordes": bordes.tolist()}, "rotulos": rot,
           "umbral": ctx.cita(), "frac_positivas_con_recorte": frac_rec}
    if ctx.elevado:
        out["dos_cifras"] = {"T_sellado": _h(ctx.T_sellado)[0], "T_lamina": H}
    return out


# ── HER2: anillo de membrana, descriptivo ───────────────────────────────────────────────────
def her2_membrana(nuc, ctx, denominador):
    """HER2 (F3): anillo de membrana, descriptivo, sin clasificar 0/ultralow, FUERA del mapa: % de
    células sobre el T de «HER2, NEG» (el T sellado del anillo se midió en P-HER2NEG); si ninguna,
    el rótulo exacto del plan."""
    _comprueba(nuc, ctx)
    if ctx.lamina not in ("P-HER2", "P-HER2NEG") or ctx.compartimento != "anillo":
        raise MetricaError("HER2: solo P-HER2 / P-HER2NEG, compartimento anillo")
    m = nuc.mascara(denominador)
    n = int(m.sum())
    k = int(np.sum(nuc.dab[m] > ctx.T))
    lo, hi = clopper_pearson(k, n)
    rot = list(ctx.rotulos_umbral())
    if k == 0:
        rot.append(ROTULOS["her2_sin_senal"])
    return {"k": k, "n": n, "pct": 100.0 * k / n if n else float("nan"),
            "ic95": [100 * lo, 100 * hi], "fuera_del_mapa": True,
            "clasificacion": None, "rotulos": rot, "alcance": ROTULOS["region_escaneada"],
            "umbral": ctx.cita()}


# ── hotspot de Ki67 y biopsias virtuales ───────────────────────────────────────────────────
def _ventanas_hotspot(fcs, p):
    from shapely.geometry import Point
    paso = p["hotspot_paso_um"]
    centros, ids = [], []
    for fid, pol in fcs.items():
        x0, y0, x1, y1 = pol.bounds
        for x in np.arange(math.floor(x0 / paso) * paso, x1 + paso, paso):
            for y in np.arange(math.floor(y0 / paso) * paso, y1 + paso, paso):
                pt = Point(x, y)
                if pol.contains(pt) and pol.boundary.distance(pt) > p["interior_um"]:
                    centros.append((x, y))
                    ids.append(fid)
    return np.array(centros, float).reshape(-1, 2), np.array(ids, dtype=object)


def _incidencia(nuc, m, centros, ids, r):
    """Matriz dispersa ventana × núcleo (núcleos del denominador del MISMO FC a < r)."""
    from scipy.sparse import csr_matrix
    from scipy.spatial import cKDTree
    idx_den = np.nonzero(m)[0]
    if not len(idx_den) or not len(centros):
        return None, idx_den
    arbol = cKDTree(nuc.xy[idx_den])
    filas, cols = [], []
    for w, lista in enumerate(arbol.query_ball_point(centros, r)):
        if not lista:
            continue
        lista = np.asarray(lista)
        lista = lista[nuc.fc[idx_den[lista]] == ids[w]]
        filas.append(np.full(len(lista), w))
        cols.append(lista)
    if not filas:
        return None, idx_den
    filas, cols = np.concatenate(filas), np.concatenate(cols)
    return csr_matrix((np.ones(len(filas), np.float32), (filas, cols)),
                      shape=(len(centros), len(idx_den))), idx_den


def hotspot(nuc, fcs, ctx, denominador):
    """Ki67 hotspot prefijado: ventana circular de 0,5 mm de diámetro con ≥500 núcleos del
    denominador, paso de 50 µm, interior del fragmento; máximo con corrección por permutación,
    p95 de ventanas y distribución completa. «not the IKWG method». Las biopsias virtuales usan
    la misma ventana."""
    _comprueba(nuc, ctx)
    p = ctx.p
    r = p["hotspot_diametro_um"] / 2
    centros, ids = _ventanas_hotspot(fcs, p)
    m = nuc.mascara(denominador)
    W, idx_den = _incidencia(nuc, m, centros, ids, r)
    rot = [ROTULOS["no_ikwg"]] + list(ctx.rotulos_umbral())
    if _usa_ck19(denominador, nuc):
        rot.append(ROTULOS["denominador_ck19"])
    base = {"rotulos": rot, "ventana": {"diametro_um": p["hotspot_diametro_um"],
            "paso_um": p["hotspot_paso_um"], "min_nucleos": p["hotspot_min_nucleos"]},
            "umbral": ctx.cita(), "alcance": ROTULOS["region_escaneada"]}
    if W is None:
        base.update({"n_ventanas_validas": 0, "maximo_pct": None})
        return base
    pos = (nuc.dab[idx_den] > ctx.T).astype(np.float32)
    n = np.asarray(W.sum(1)).ravel()
    k = W @ pos
    val = n >= p["hotspot_min_nucleos"]
    if not val.any():
        base.update({"n_ventanas_validas": 0, "maximo_pct": None})
        return base
    pct = 100.0 * k[val] / n[val]
    imax = int(np.argmax(pct))
    cval = centros[val]
    # corrección por permutación: etiquetas barajadas DENTRO de cada FC (azar espacial)
    rng = np.random.default_rng(int(p["semilla_hotspot"]))
    P = int(p["hotspot_permutaciones"])
    fc_den = nuc.fc[idx_den]
    grupos = [np.nonzero(fc_den == f)[0] for f in sorted(set(fc_den), key=str)]
    Pm = np.empty((len(pos), P), np.float32)
    for q in range(P):
        col = pos.copy()
        for g in grupos:
            col[g] = rng.permutation(col[g])
        Pm[:, q] = col
    kp = W[val] @ Pm
    maxp = (100.0 * kp / n[val][:, None]).max(0)
    pval = float((1 + np.sum(maxp >= pct[imax])) / (1 + P))
    cuantiles = np.percentile(pct, [0, 5, 25, 50, 75, 95, 100])
    base.update({
        "n_ventanas_validas": int(val.sum()), "n_ventanas_total": int(len(centros)),
        "maximo_pct": float(pct[imax]), "maximo_centro_um": [float(v) for v in cval[imax]],
        "maximo_fc": str(ids[val][imax]), "maximo_n": int(n[val][imax]),
        "p95_pct": float(np.percentile(pct, 95)),
        "permutacion": {"maximo_nulo_p50": float(np.median(maxp)),
                        "maximo_nulo_p95": float(np.percentile(maxp, 95)),
                        "p_maximo": pval,
                        "exceso_sobre_nulo_p95": float(pct[imax] - np.percentile(maxp, 95)),
                        "permutaciones": P},
        "distribucion_pct": [round(float(v), 2) for v in np.sort(pct)],
        "biopsias_virtuales": {"min": float(cuantiles[0]), "p5": float(cuantiles[1]),
                               "p25": float(cuantiles[2]), "p50": float(cuantiles[3]),
                               "p75": float(cuantiles[4]), "p95": float(cuantiles[5]),
                               "max": float(cuantiles[6]),
                               "nota": "same 0.5 mm window at every valid position of this slide"},
    })
    if ctx.elevado:
        pos_s = (nuc.dab[idx_den] > ctx.T_sellado).astype(np.float32)
        base["dos_cifras"] = {"T_sellado": float((100.0 * (W @ pos_s)[val] / n[val]).max()),
                              "T_lamina": float(pct[imax])}
    return base


# ── puerta de señal ────────────────────────────────────────────────────────────────────────
def _estado_senal(frac, regiones, kreg, fp_sup, p):
    if frac <= fp_sup:
        return ROTULOS["no_signal"]
    con = [c for c, r in regiones.items()
           if kreg.get(c, 0) >= p["senal_region_min_pos"]
           and r["n"] and 100.0 * kreg.get(c, 0) / r["n"] >= p["senal_region_min_pct"]]
    frac_reg = len(con) / len(regiones) if regiones else 0.0
    if (frac >= p["senal_min_fraccion"] and frac >= p["senal_multiplo_fp"] * fp_sup
            and frac_reg >= p["senal_regiones_frac"]):
        return ROTULOS["quantifiable"]
    return ROTULOS["focal"]


def puerta_senal(nuc, regiones, ctx, denominador):
    """«no signal» / «quantifiable» / «focal» por objeto. Cada estado se compara con la tasa de
    P-HER2 medida en SU umbral: en T, la sellada; en los extremos de la banda, solo si la
    congelación selló la tasa en esos umbrales. Si la banda cambia el estado, «borderline:
    reported both ways»; si las tasas de los extremos no están selladas, «borderline» no se evalúa
    y se declara. p63 no pasa por esta puerta. La puerta solo decide el mapa y el Moran
    (actualización 11 del plan): el % con IC se da igual para todo marcador."""
    _comprueba(nuc, ctx)
    if ctx.lamina in ("P-P63",):
        raise MetricaError("p63 no pasa por la puerta de señal (tiene la suya)")
    if ctx.fp is None:
        raise MetricaError("la puerta de señal necesita la tasa de P-HER2 sellada en este régimen: "
                           "%s" % ctx.fp_motivo)
    if regiones:
        _regiones_de(regiones, ctx, denominador)
    p = ctx.p
    regiones = validas(regiones, p)
    m = nuc.mascara(denominador)
    n = int(m.sum())
    idx = _indices_region(nuc, regiones)
    claves = list(regiones)
    out = {"umbral": ctx.cita(), "declaraciones": []}
    lo, hi = ctx.banda()
    # Con T elevado por el fondo, la tasa sellada (medida en T_sellado < T) es conservadora para
    # T; las de los extremos de la banda sellada ya no corresponden a la banda nueva: no se usan.
    umbrales = [("T", ctx.T, ctx.fp["ic95_sup"])]
    for nombre, T in (("T_bajo", lo), ("T_alto", hi)):
        b = None if ctx.elevado else ctx.fp_banda.get(nombre)
        umbrales.append((nombre, T, None if b is None else b["ic95_sup"]))
    if ctx.elevado:
        umbrales.append(("T_sellado", ctx.T_sellado, ctx.fp["ic95_sup"]))
    estados = {}
    for nombre, T, fp_sup in umbrales:
        pos = (nuc.dab > T) & m
        frac = float(pos.sum() / n) if n else 0.0
        if fp_sup is None:
            estados[nombre] = {"estado": None, "fraccion": frac, "umbral": T,
                               "motivo": "P-HER2 rate at this threshold not sealed"}
            continue
        sel = pos & (idx >= 0)
        kq = np.bincount(idx[sel], minlength=len(claves)) if claves else np.zeros(0, int)
        kreg = {claves[q]: int(kq[q]) for q in range(len(claves))}
        estados[nombre] = {"estado": _estado_senal(frac, regiones, kreg, fp_sup, p),
                           "fraccion": frac, "umbral": T, "fp_ic95_sup": fp_sup}
    out["estados"] = estados
    out["estado"] = estados["T"]["estado"]
    out["rotulos"] = list(ctx.rotulos_umbral())
    banda = [estados[x]["estado"] for x in ("T_bajo", "T_alto")]
    if None in banda:
        out["declaraciones"].append("borderline not evaluated: P-HER2 false-positive rate not "
                                    "sealed at the band extremes")
    elif len({estados["T"]["estado"], *banda}) > 1:
        out["rotulos"].append(ROTULOS["borderline"])
    out["entra_en_mapa_y_moran"] = out["estado"] == ROTULOS["quantifiable"]
    out["nota"] = ("the signal gate only decides entry into the neighbourhood map and Moran's I; "
                   "the % with its interval and sensitivity range is reported for every marker")
    if out["estado"] == ROTULOS["focal"]:
        out["focal"] = galeria_focal(nuc, regiones, ctx, denominador)
        # marcador NE «focal»: además, el patrón de puntos de sus positivos (`patron_puntos`,
        # necesita las regiones de RE y Ki67 registradas; lo lanza `mide_marcador`)
        out["patron_puntos_requerido"] = ctx.lamina in LAMINAS_NE
    return out


def galeria_focal(nuc, regiones, ctx, denominador):
    """Recuento, posiciones y galería a L0 de los positivos: todos si son ≤200; si no, 200 al azar
    (semilla sellada, `semilla_galeria_focal` = `semillas.galeria_focal` del sello) más las 10
    regiones con más. Fuera del mapa y del Moran; el % con IC lo da `porcentaje_global` igual que
    para cualquier marcador (actualización 11 del plan)."""
    _comprueba(nuc, ctx)
    p = ctx.p
    m = nuc.mascara(denominador) & (nuc.dab > ctx.T)
    idx = np.nonzero(m)[0]
    rng = np.random.default_rng(int(p["semilla_galeria_focal"]))
    if len(idx) > p["focal_galeria_max"]:
        gal = np.sort(rng.choice(idx, int(p["focal_galeria_max"]), replace=False))
    else:
        gal = idx
    claves = claves_de(nuc, regiones)
    cuenta = {}
    for i in idx:
        c = claves[i] if claves else None
        if c is not None and c in regiones:
            cuenta[c] = cuenta.get(c, 0) + 1
    top = sorted(cuenta.items(), key=lambda kv: (-kv[1], str(kv[0])))[:int(p["focal_regiones_top"])]
    return {"n_positivos": int(len(idx)),
            "posiciones_um": nuc.xy[idx].round(1).tolist(),
            "galeria_indices": gal.tolist(), "semilla": int(p["semilla_galeria_focal"]),
            "regiones_top": [{"region": "%s:%d,%d" % c, "positivos": v} for c, v in top],
            "nota": "focal: outside the neighbourhood map and Moran's I"}


# ── patrón de puntos de los positivos de un marcador NE «focal» ─────────────────────────────
# Clases de referencia del plan: «distancia a las regiones RE-altas y RE-bajas y a las
# Ki67-altas». Se nombran por tercil del % regional (nunca «ER-low», que es de «Nunca decir»).
CLASES_PATRON = (("P-RE", "alto", "regions in the top ER tercile (regional %)"),
                 ("P-RE", "bajo", "regions in the bottom ER tercile (regional %)"),
                 ("P-KI67", "alto", "regions in the top Ki67 tercile (regional %)"))


def _clases_referencia(referencias, p, declaraciones):
    """{nombre: {fc: unión de geometrías}, …} de las clases de CLASES_PATRON con su corte."""
    from shapely.ops import unary_union
    out = {}
    for ref, lado, nombre in CLASES_PATRON:
        regs = (referencias or {}).get(ref)
        if not regs:
            declaraciones.append("%s: reference slide %s not available" % (nombre, ref))
            continue
        val = validas(regs, p)
        lam = {r.get("lamina") for r in val.values()}
        if lam - {ref}:
            raise MetricaError("regiones de %s pasadas como %s" % (", ".join(map(str, lam)), ref))
        if len(val) < 3:
            declaraciones.append("%s: fewer than 3 regions of %s" % (nombre, ref))
            continue
        pct = np.array([r["pct"] for r in val.values()], float)
        corte = float(np.percentile(pct, p["patron_cuantil_alto" if lado == "alto"
                                         else "patron_cuantil_bajo"]))
        iqr = float(np.percentile(pct, 75) - np.percentile(pct, 25))
        if iqr < p["varianza_iqr_min"]:
            declaraciones.append("%s: %s regional %% near-uniform (IQR %.1f points): tercile "
                                 "classes barely differ" % (nombre, ref, iqr))
        sel = [r for r in val.values() if (r["pct"] >= corte if lado == "alto" else r["pct"] <= corte)]
        uniones = {}
        for f in sorted({r["fc"] for r in sel}, key=str):
            uniones[f] = unary_union([r["geom"] for r in sel if r["fc"] == f])
        out[nombre] = {"referencia": ref, "lado": lado, "corte_pct": corte, "iqr_referencia": iqr,
                       "n_regiones": len(sel), "uniones": uniones}
    return out


def patron_puntos(nuc, ctx, denominador, referencias=None):
    """Patrón espacial de las células positivas de un marcador NE «focal» (plan, actualización
    11): «spatial pattern of NE-marker-positive cells (descriptive, n=1)».

    Nulo: ETIQUETADO ALEATORIO — el mismo nº de positivos, reasignado al azar entre TODAS las
    células del denominador de su mismo fragmento (permutación dentro de cada FC, semilla sellada).
    Condiciona a las posiciones de todas las células, así que no necesita corrección de borde y una
    diferencia de nivel entre fragmentos no cuenta como agregación [inferencia mía: propiedad del
    etiquetado aleatorio, sin fuente abierta en esta sesión]. Estadísticos (descriptivos, p sin
    corregir entre ellos):
      · vecino más próximo entre positivos: media observada frente al nulo (p de agregación y de
        regularidad);
      · recuento tipo Ripley: pares ordenados de positivos a ≤ r (r sellados), cociente frente al
        nulo, p por radio y p global (máximo de los estandarizados, que corrige entre radios);
      · distancia de cada positivo a las regiones del tercil alto y bajo de RE y del tercil alto de
        Ki67 (`referencias` = {"P-RE": regiones, "P-KI67": regiones}, contadas en la MISMA rejilla
        anclada a P-CK19 sobre FC verificados), solo en los FC donde hay regiones de esa clase:
        media y fracción dentro (distancia 0) frente al nulo, p de «más cerca» y de «más lejos»."""
    from scipy.spatial import cKDTree
    import shapely
    _comprueba(nuc, ctx)
    if ctx.lamina not in LAMINAS_NE:
        raise MetricaError("el patrón de puntos del plan es de los marcadores NE (%s)"
                           % ", ".join(LAMINAS_NE))
    p = ctx.p
    todas = np.nonzero(nuc.mascara(denominador))[0]
    xy = nuc.xy[todas]
    fc = nuc.fc[todas]
    pos = nuc.dab[todas] > ctx.T
    k = int(pos.sum())
    P = int(p["patron_permutaciones"])
    out = {"rotulo": ROTULOS["patron_ne"], "lamina": ctx.lamina, "denominador": denominador,
           "n_celulas": int(len(todas)), "n_positivos": k,
           "posiciones_um": xy[pos].round(1).tolist(),
           "rotulos": [ROTULOS["patron_ne"]] + list(ctx.rotulos_umbral()),
           "nulo": ("random labelling: the same number of positive cells reassigned at random "
                    "among all denominator cells of the same fragment (%d permutations)" % P),
           "declaraciones": ["descriptive; permutation p-values are not corrected across the "
                             "statistics reported"],
           "umbral": ctx.cita(), "alcance": ROTULOS["region_escaneada"]}
    if k < int(p["patron_min_positivos"]):
        out["motivo"] = ("fewer than %d positive cells: count and positions only"
                         % int(p["patron_min_positivos"]))
        return out
    grupos = [np.nonzero(fc == f)[0] for f in sorted(set(fc), key=str)]
    k_g = [int(pos[g].sum()) for g in grupos]
    radios = np.asarray(p["patron_radios_um"], float)
    clases = _clases_referencia(referencias, p, out["declaraciones"])
    dist = {}
    for nombre, c in clases.items():
        d = np.full(len(todas), np.nan)
        for f, geom in c["uniones"].items():
            i = np.nonzero(fc == f)[0]
            if len(i):
                d[i] = shapely.distance(geom, shapely.points(xy[i]))
        dist[nombre] = d

    def estadisticos(sel):
        pts = xy[sel]
        arbol = cKDTree(pts)
        nn = float(np.mean(arbol.query(pts, k=2)[0][:, 1]))
        pares = np.asarray(arbol.count_neighbors(arbol, radios), float) - len(pts)
        dd = {}
        for nombre, d in dist.items():
            v = d[sel]
            v = v[np.isfinite(v)]
            dd[nombre] = (float(v.mean()), float(np.mean(v == 0))) if len(v) else (np.nan, np.nan)
        return nn, pares, dd
    nn_o, pares_o, dd_o = estadisticos(np.nonzero(pos)[0])
    rng = np.random.default_rng(int(p["semilla_patron"]))
    nn_n = np.empty(P)
    pares_n = np.empty((P, len(radios)))
    dd_n = {nombre: np.empty((P, 2)) for nombre in dist}
    for q in range(P):
        sel = np.concatenate([rng.choice(g, kg, replace=False) for g, kg in zip(grupos, k_g) if kg])
        nn_n[q], pares_n[q], dd = estadisticos(sel)
        for nombre in dist:
            dd_n[nombre][q] = dd[nombre]

    def pv(n):
        return float((1 + n) / (1 + P))
    media = pares_n.mean(0)
    sd = pares_n.std(0, ddof=1)
    sd = np.where(sd > 0, sd, np.inf)
    z_o = np.max((pares_o - media) / sd)
    z_n = np.max((pares_n - media) / sd, axis=1)
    out["vecino_mas_proximo"] = {
        "media_um": nn_o, "media_nulo_um": float(nn_n.mean()),
        "cociente": nn_o / float(nn_n.mean()) if nn_n.mean() > 0 else float("nan"),
        "p_agregacion": pv(np.sum(nn_n <= nn_o + 1e-12)),
        "p_regularidad": pv(np.sum(nn_n >= nn_o - 1e-12))}
    out["ripley"] = {
        "estadistico": "ordered pairs of positive cells within r (Ripley's K up to a constant "
                       "under random labelling)",
        "radios_um": radios.tolist(), "pares": pares_o.tolist(), "pares_nulo_media": media.tolist(),
        "cociente": [float(o / m) if m > 0 else float("nan") for o, m in zip(pares_o, media)],
        "p_agregacion_por_radio": [pv(np.sum(pares_n[:, j] >= pares_o[j] - 1e-12))
                                   for j in range(len(radios))],
        "p_agregacion_global": pv(np.sum(z_n >= z_o - 1e-12))}
    out["distancias"] = {}
    for nombre, c in clases.items():
        mo, fo = dd_o[nombre]
        dn = dd_n[nombre]
        fila = {"referencia": c["referencia"], "tercil": c["lado"], "corte_pct": c["corte_pct"],
                "n_regiones": c["n_regiones"],
                "n_positivos_evaluados": int(np.sum(np.isfinite(dist[nombre][pos])))}
        if math.isfinite(mo):
            fila.update({"media_um": mo, "media_nulo_um": float(np.nanmean(dn[:, 0])),
                         "fraccion_dentro": fo, "fraccion_dentro_nulo": float(np.nanmean(dn[:, 1])),
                         "p_mas_cerca": pv(np.sum(dn[:, 0] <= mo + 1e-12)),
                         "p_mas_lejos": pv(np.sum(dn[:, 0] >= mo - 1e-12))})
        else:
            fila["motivo"] = "no positive cell in a fragment with regions of this class"
        out["distancias"][nombre] = fila
    return out


# ── las MISMAS métricas para todo marcador (simetría NE) ────────────────────────────────────
def mide_marcador(nuc, regiones, ctx, denominador, fcs=None, nuc_clasico=None, deteccion_ok=None,
                  ck19_caido=False, solo_mapa=(), referencias=None):
    """Plan, actualización 11 («simetría hasta descartarlo»): SYN, CHGA e {{DIANA3}} reciben las MISMAS
    métricas que RE y Ki67 —% sobre el denominador con IC por bloques y rango de sensibilidad,
    H-score digital con histograma, heterogeneidad regional (tabla, IC binomial, CV) y puerta de
    señal—, y la puerta SOLO decide si entra en el mapa de vecindad y en el Moran, igual para RE que
    para NE. Ki67 añade su hotspot (`fcs`); un marcador NE «focal», el patrón de puntos de sus
    positivos (`referencias` = {"P-RE": regiones, "P-KI67": regiones})."""
    if ctx.lamina in NO_MARCADOR:
        raise MetricaError(NO_MARCADOR[ctx.lamina])
    senal = puerta_senal(nuc, regiones, ctx, denominador)
    out = {"lamina": ctx.lamina, "denominador": denominador,
           "porcentaje": porcentaje_global(nuc, regiones, ctx, denominador,
                                           nuc_clasico=nuc_clasico, deteccion_ok=deteccion_ok,
                                           ck19_caido=ck19_caido, solo_mapa=solo_mapa),
           "hscore": hscore(nuc, ctx, denominador),
           "heterogeneidad": heterogeneidad(regiones, ctx, senal),
           "senal": senal, "entra_en_mapa_y_moran": senal["entra_en_mapa_y_moran"]}
    if ctx.lamina == "P-KI67" and fcs:
        out["hotspot"] = hotspot(nuc, fcs, ctx, denominador)
    if senal.get("patron_puntos_requerido"):
        out["patron_puntos"] = patron_puntos(nuc, ctx, denominador, referencias)
    return out


# ── (a) mapa de vecindad ───────────────────────────────────────────────────────────────────
def puerta_varianza(regiones, ctx):
    p = ctx.p
    v = np.array([r["pct"] for r in validas(regiones, p).values()], float)
    if len(v) < 2:
        return {"pasa": False, "iqr": float("nan"), "frac_extremos": float("nan"),
                "n_regiones": int(len(v))}
    iqr = float(np.percentile(v, 75) - np.percentile(v, 25))
    ext = float(np.mean((v <= p["varianza_extremo_bajo"]) | (v >= p["varianza_extremo_alto"])))
    pasa = iqr >= p["varianza_iqr_min"] and ext < p["varianza_extremos_max"]
    return {"pasa": bool(pasa), "iqr": iqr, "frac_extremos": ext, "n_regiones": int(len(v))}


def _neff_crh(ra, rb, centros, fcs, L, p):
    """Tamaño efectivo de Clifford-Richardson-Hémon para la correlación de dos variables
    autocorrelativas: n_eff = 1 + n² / Σ_ij ρa(d_ij) ρb(d_ij), con los correlogramas de cada una
    por clases de distancia (mismo FC; entre fragmentos, 0)."""
    _, ca = correlograma(ra, centros, fcs, L, p["correlograma_min_pares"])
    _, cb = correlograma(rb, centros, fcs, L, p["correlograma_min_pares"])
    if ca is None or cb is None:
        return None
    cls, ta = ca
    _, tb = cb
    n = len(ra)
    m = cls >= 0
    Ra = np.zeros(cls.shape)
    Rb = np.zeros(cls.shape)
    Ra[m] = ta[cls[m]]
    Rb[m] = tb[cls[m]]
    s = float(np.sum(Ra * Rb))
    return 1.0 + n * n / s if s > 0 else None


def ic_spearman(a, b, centros, fcs, L, p, rng):
    """IC del Spearman entre % regionales: envolvente del bootstrap de bloques contiguos de ~1 mm
    (correlación de rangos con los rangos fijados en la muestra; réplicas por pesos) y del
    intervalo de Fisher con el n efectivo de CRH y cuantil t (ver cabecera)."""
    from scipy.stats import rankdata, t as tdist
    ra, rb = rankdata(a), rankdata(b)
    rho = _spearman(a, b)
    out = {"rho": rho}
    regs = {q: {"centro": tuple(centros[q]), "fc": fcs[q]} for q in range(len(a))}
    bl = bloques_contiguos(regs, float(p["bloque_contiguo_um"]))
    ub = sorted(set(bl.values()), key=str)
    pos = {u: q for q, u in enumerate(ub)}
    g = np.array([pos[bl[q]] for q in range(len(a))])
    G = len(ub)
    boot = [float("nan"), float("nan")]
    if G >= 2:
        B = int(p["bootstrap_replicas"])
        cuentas = np.zeros((B, G))
        sel = rng.integers(0, G, (B, G))
        np.add.at(cuentas, (np.repeat(np.arange(B), G), sel.ravel()), 1.0)
        w = cuentas[:, g]                                      # B × n, peso de cada región
        sw = w.sum(1)
        ma, mb = (w @ ra) / sw, (w @ rb) / sw
        sab = (w @ (ra * rb)) / sw - ma * mb
        saa = (w @ (ra * ra)) / sw - ma ** 2
        sbb = (w @ (rb * rb)) / sw - mb ** 2
        with np.errstate(invalid="ignore", divide="ignore"):
            r = sab / np.sqrt(saa * sbb)
        boot = [float(np.nanpercentile(r, 2.5)), float(np.nanpercentile(r, 97.5))]
    ne = _neff_crh(ra, rb, centros, fcs, L, p)
    fisher = [-1.0, 1.0]
    if ne is not None and ne > 4 and abs(rho) < 1:
        se = 1.0 / math.sqrt(ne - 3)
        tq = float(tdist.ppf(0.975, ne - 3))
        z = math.atanh(rho)
        fisher = [math.tanh(z - tq * se), math.tanh(z + tq * se)]
    finitos = [x for x in (boot, fisher) if all(map(math.isfinite, x))]
    env = [min(x[0] for x in finitos), max(x[1] for x in finitos)]
    out.update({"ic95": env, "ic95_bootstrap_bloques": boot, "ic95_n_efectivo": fisher,
                "n_efectivo": ne, "n_bloques": G})
    return out


def fuera_del_mapa_iv(ctx, fuera_mapa=None):
    """(iv) «counterstain differs» del módulo A: {"fuera": {lámina: rótulos}, "sin_evaluar": […],
    "sello_sha256"}, tal como lo devuelve `laminillas_congela.fuera_del_mapa` (las de suelo, del
    sello; el resto, de su anexo sellado). Sin `fuera_mapa`, se lee de la carpeta del sello del
    contexto; si se pasa, tiene que ser de ESE sello."""
    if ctx.sello is None:                       # pre-congelación: solo suelo, que ya está fuera
        return {"fuera": {}, "sin_evaluar": [], "sello_sha256": None}
    if fuera_mapa is None:
        import laminillas_congela as CG
        fuera_mapa = CG.fuera_del_mapa(os.path.dirname(os.path.abspath(ctx.sello.ruta)))
    if fuera_mapa.get("sello_sha256") != ctx.sello.sha256:
        raise MetricaError("(iv) «counterstain differs» de otro sello: no casa con el del contexto")
    return fuera_mapa


def mapa_vecindad(reg_a, reg_b, ctx, tre_p90_par_um, nombres, senal, fuera_mapa=None):
    """Spearman entre % regionales de dos marcadores registrados (regiones de la MISMA rejilla),
    con IC calibrado (ver `ic_spearman`); sin p-valor; techo = Spearman de la densidad nuclear
    regional entre los dos cortes. Entra a esta escala solo si 2× p90 ≤ L. Fuera: HER2, HER2NEG,
    AE1/AE3, CK19 (denominador), las láminas que (iv) saca del mapa o aún no ha evaluado
    (`fuera_del_mapa_iv`, leído del sello) y todo marcador cuya puerta de señal no sea
    «quantifiable» (`senal` = {nombre: estado o salida de `puerta_senal`}). Puerta de varianza y n
    mínimo sobre las regiones COMUNES."""
    p = ctx.p
    nombres = tuple(nombres)
    L = next(iter(reg_a.values()))["L"] if reg_a else None
    out = {"par": list(nombres), "L_um": L, "tre_p90_par_um": tre_p90_par_um,
           "rotulos": [ROTULOS["colocalizacion"], ROTULOS["descriptivo"]], "declaraciones": []}
    fuera = [nm for nm in nombres if nm in LAMINAS_FUERA_DEL_MAPA]
    if fuera:
        out.update({"entra": False, "motivo": "outside the map by plan: %s" % ", ".join(fuera)})
        return out
    if "P-CK19" in nombres:
        out.update({"entra": False, "motivo": NO_MARCADOR["P-CK19"]})
        return out
    estados = {nm: _estado((senal or {}).get(nm)) for nm in nombres}
    no_q = {nm: e for nm, e in estados.items() if e != ROTULOS["quantifiable"]}
    out["senal"] = estados
    if no_q:
        out.update({"entra": False, "motivo": "signal gate not «quantifiable»: %s"
                    % ", ".join("%s=%s" % kv for kv in sorted(no_q.items(), key=str))})
        return out
    for nm, reg in zip(nombres, (reg_a, reg_b)):
        if reg and next(iter(reg.values())).get("lamina") not in (None, nm):
            raise MetricaError("regiones de %s pasadas como %s" % (
                next(iter(reg.values())).get("lamina"), nm))
    iv = fuera_del_mapa_iv(ctx, fuera_mapa)
    contra = {nm: iv["fuera"][nm] for nm in nombres if nm in (iv.get("fuera") or {})}
    if contra:
        out["rotulos_iv"] = contra
        out.update({"entra": False, "motivo": "counterstain differs (iv): %s"
                    % ", ".join(sorted(contra))})
        return out
    pend = [nm for nm in nombres if nm in (iv.get("sin_evaluar") or [])]
    if pend:
        out.update({"entra": False, "motivo": "counterstain check (iv) not yet evaluated: %s"
                    % ", ".join(pend)})
        return out
    if L is None or tre_p90_par_um is None or 2 * tre_p90_par_um > L:
        out["entra"] = False
        out["motivo"] = "2× p90 TRE > L"
        return out
    va_, vb_ = validas(reg_a, p), validas(reg_b, p)
    comunes = sorted(set(va_) & set(vb_), key=str)
    ca = {c: va_[c] for c in comunes}
    cb = {c: vb_[c] for c in comunes}
    va, vb = puerta_varianza(ca, ctx), puerta_varianza(cb, ctx)
    out["varianza"] = {nombres[0]: va, nombres[1]: vb}
    out["n_regiones"] = len(comunes)
    out["entra"] = True
    if len(comunes) < p["mapa_min_regiones"]:
        out["rho"] = None
        out["declaraciones"].append("fewer than %d paired regions at this scale: rho not "
                                    "estimated" % p["mapa_min_regiones"])
        return out
    if not (va["pasa"] and vb["pasa"]):
        out["rho"] = None
        out["rotulos"].append(ROTULOS["casi_uniforme"])
        return out
    a = np.array([ca[c]["pct"] for c in comunes])
    b = np.array([cb[c]["pct"] for c in comunes])
    da = np.array([ca[c]["densidad_mm2"] for c in comunes])
    db = np.array([cb[c]["densidad_mm2"] for c in comunes])
    centros = np.array([ca[c]["centro"] for c in comunes], float)
    fcs = [ca[c]["fc"] for c in comunes]
    rng = np.random.default_rng(int(p["semilla_mapa"]))
    ic = ic_spearman(a, b, centros, fcs, L, p, rng)
    out.update({"rho": ic["rho"], "ic95": ic["ic95"],
                "ic95_bootstrap_bloques": ic["ic95_bootstrap_bloques"],
                "ic95_n_efectivo": ic["ic95_n_efectivo"], "n_efectivo": ic["n_efectivo"],
                "n_bloques": ic["n_bloques"], "techo_densidad_rho": _spearman(da, db)})
    out["rotulos"].append(ROTULOS["techo"])
    out["declaraciones"].append("interval = envelope of the ~1 mm contiguous-block bootstrap and "
                                "the effective-sample-size (spatial autocorrelation) interval")
    return out


def _mismo_sello(*ctxs):
    """Un solo sello (mismo sha256 y mismo fichero: su carpeta lleva el anexo de (iv))."""
    shas = {(c.sello.sha256, os.path.realpath(c.sello.ruta)) if c.sello is not None else None
            for c in ctxs}
    if len(shas) > 1:
        raise MetricaError("contextos de sellos distintos: el mapa se mide con UN sello")


def mapa_vecindad_escalas(nuc_a, ctx_a, nuc_b, ctx_b, fcs, L, tre_p90_par_um, denominador, senal,
                          origen=(0.0, 0.0), fuera_mapa=None):
    """El par a L y a 2L (plan: «a L y a 2L»), cada marcador con SU T sellado, misma rejilla
    anclada a P-CK19 y regiones con <50 núcleos fuera."""
    _mismo_sello(ctx_a, ctx_b)
    fuera_mapa = fuera_del_mapa_iv(ctx_a, fuera_mapa)
    out = {}
    for nombre, lado in (("L", float(L)), ("2L", 2.0 * float(L))):
        reg = rejilla(fcs, lado, origen)
        ra, _ = excluye_pequenas(cuenta_regiones(nuc_a, reg, ctx_a, denominador), ctx_a.p)
        rb, _ = excluye_pequenas(cuenta_regiones(nuc_b, reg, ctx_b, denominador), ctx_b.p)
        out[nombre] = mapa_vecindad(ra, rb, ctx_a, tre_p90_par_um, (ctx_a.lamina, ctx_b.lamina),
                                    senal, fuera_mapa=fuera_mapa)
    return out


def _clave_par(a, b):
    return "%s×%s" % (a, b)


def mapa_multimarcador(marcadores, fcs, L, tre_p90_pares, denominador, origen=(0.0, 0.0)):
    """(a) Mapa de vecindad entre TODOS los pares de los marcadores medidos (cada par por
    separado; basta con 2), a L y a 2L. `marcadores` = {lámina: (nuc, ctx, señal)} (señal = estado
    o salida de `puerta_senal`); `tre_p90_pares` = {(a, b): p90 del TRE del par compuesto, medido
    con (b)/(b') entre sus dos láminas} (orden indiferente).

    Plan, actualización 11: los pares RE × SYN y Ki67 × SYN son OBLIGATORIOS siempre que pasen
    registro y puerta de varianza; aquí salen SIEMPRE en la tabla, con su resultado o con el motivo
    por el que no entran (marcador no medido, sin TRE del par, (iv), puerta de señal o de
    varianza). Fuera del mapa: HER2, HER2NEG, AE1/AE3, CK19 (denominador) y lo que (iv) saca, leído
    UNA vez del sello común."""
    nombres = sorted(n for n in marcadores if n not in NO_MARCADOR)
    ctxs = [marcadores[n][1] for n in nombres]
    if not ctxs:
        raise MetricaError("mapa sin marcadores")
    _mismo_sello(*ctxs)
    fuera_mapa = fuera_del_mapa_iv(ctxs[0])
    senal = {n: marcadores[n][2] for n in nombres}
    pares = [(a, b) for i, a in enumerate(nombres) for b in nombres[i + 1:]]
    for a, b in PARES_OBLIGATORIOS:
        if (a, b) not in pares and (b, a) not in pares:
            pares.append((a, b))
    obligatorios = {frozenset(x) for x in PARES_OBLIGATORIOS}
    out = {"pares": {}, "obligatorios": [], "L_um": float(L),
           "fuera": {n: NO_MARCADOR[n] for n in marcadores if n in NO_MARCADOR},
           "rotulos": [ROTULOS["colocalizacion"], ROTULOS["descriptivo"]],
           "iv": {"fuera": fuera_mapa.get("fuera"), "sin_evaluar": fuera_mapa.get("sin_evaluar")}}
    for a, b in pares:
        clave = _clave_par(a, b)
        oblig = frozenset((a, b)) in obligatorios
        falta = [n for n in (a, b) if n not in marcadores]
        if falta:
            fila = {"L": {"entra": False, "motivo": "marker not measured: %s" % ", ".join(falta)}}
            fila["2L"] = dict(fila["L"])
        else:
            tre = tre_p90_pares.get((a, b), tre_p90_pares.get((b, a)))
            na, ca, _ = marcadores[a]
            nb, cb, _ = marcadores[b]
            fila = mapa_vecindad_escalas(na, ca, nb, cb, fcs, L, tre, denominador, senal, origen,
                                         fuera_mapa=fuera_mapa)
        fila["obligatorio"] = oblig
        out["pares"][clave] = fila
        if oblig:
            out["obligatorios"].append({
                "par": clave, "medido": not falta,
                "entra": {e: bool(fila[e].get("entra")) for e in ("L", "2L")},
                "rho": {e: fila[e].get("rho") for e in ("L", "2L")},
                "motivo": {e: fila[e].get("motivo") for e in ("L", "2L") if fila[e].get("motivo")}})
    return out


# ── Denominador: supervivencia de la erosión y parada dura de densidad ──────────────────────
def supervivencia_erosion(nuc_ck19, ctx_ck19):
    """En P-CK19, sin registro: fracción de núcleos CK19+ (sin erosionar) que sobrevive a la
    erosión, por fragmento. Con <70 %, la cifra principal de ESE fragmento pasa a la regla
    morfométrica y CK19 queda como prior regional sin erosión (plan, Denominador)."""
    _comprueba(nuc_ck19, ctx_ck19)
    if ctx_ck19.lamina != "P-CK19":
        raise MetricaError("la supervivencia de la erosión se mide en P-CK19")
    minimo = ctx_ck19.p["erosion_supervivencia_min"]
    ok = ~nuc_ck19.artefacto & nuc_ck19.en_fc()
    sin = nuc_ck19.den["ck19_sin_erosionar"] & ok
    ero = nuc_ck19.den["ck19_erosionada"] & sin
    out = {}
    for f in sorted({x for x in nuc_ck19.fc[ok]}, key=str):
        enf = nuc_ck19.fc == f
        n_sin, n_ero = int(np.sum(sin & enf)), int(np.sum(ero & enf))
        frac = n_ero / n_sin if n_sin else float("nan")
        out[f] = {"n_ck19_sin_erosionar": n_sin, "n_sobreviven": n_ero, "fraccion": frac,
                  "principal": ("ck19_erosionada" if n_sin and frac >= minimo else "morfometrico")}
    return out


def aplica_denominador_principal(nuc, supervivencia):
    """Añade a `nuc.den` el denominador «principal»: CK19 erosionada en los FC cuya erosión
    sobrevive ≥70 %, morfométrico en el resto."""
    for d in DENOMINADORES:
        if d not in nuc.den:
            raise MetricaError("siempre tres cifras: falta %s" % d)
    elige = np.array([(supervivencia.get(f) or {}).get("principal") == "ck19_erosionada"
                      for f in nuc.fc], bool)
    nuc.den["principal"] = np.where(elige, nuc.den["ck19_erosionada"], nuc.den["morfometrico"])
    nuc.principal_usa_ck19 = bool(elige.any())
    return {str(f): (supervivencia.get(f) or {}).get("principal", "morfometrico")
            for f in sorted({x for x in nuc.fc if x is not None}, key=str)}


def densidad_por_fc(nuc, fcs):
    """Núcleos (todos) por mm² en cada FC, para la parada dura."""
    out = {}
    for f, pol in fcs.items():
        n = int(np.sum(nuc.fc == f))
        out[f] = n / (pol.area / 1e6) if pol is not None and pol.area > 0 else float("nan")
    return out


def parada_densidad(densidades, frac_min=PARAMS_PARTIDA["densidad_parada_frac"]):
    """Parada dura (plan, Denominador): en el fragmento, densidad nuclear <70 % de la mediana de
    la serie → solo mapa. `densidades` = {lámina: {fc: núcleos/mm²}}."""
    fcs = sorted({f for d in densidades.values() for f in d}, key=str)
    mediana = {f: float(np.nanmedian([d[f] for d in densidades.values() if f in d])) for f in fcs}
    solo = {lam: sorted([f for f, v in d.items() if math.isfinite(v)
                         and v < frac_min * mediana[f]], key=str)
            for lam, d in densidades.items()}
    return {"mediana_serie": mediana, "solo_mapa": solo, "frac_min": frac_min}


# ── Puerta de p63, por fragmento ─────────────────────────────────────────────────────────────
def _cobertura_arco(s, perimetro, arco):
    """Fracción del perímetro cubierta por arcos de longitud `arco` centrados en `s`."""
    if not len(s) or perimetro <= 0:
        return 0.0
    iv = []
    for x in np.asarray(s, float):
        a, b = x - arco / 2, x + arco / 2
        if a < 0:
            iv += [(a + perimetro, perimetro), (0, b)]
        elif b > perimetro:
            iv += [(a, perimetro), (0, b - perimetro)]
        else:
            iv.append((a, b))
    iv.sort()
    tot, ca, cb = 0.0, None, None
    for a, b in iv:
        if ca is None or a > cb:
            if ca is not None:
                tot += cb - ca
            ca, cb = a, b
        else:
            cb = max(cb, b)
    tot += cb - ca
    return min(1.0, tot / perimetro)


def puerta_p63(nuc_p63, ctx_p63, estructuras):
    """Por fragmento (plan, «Puerta de p63»): registro y ≥1 estructura CK19+ con ≥10 núcleos p63
    sobre T en arco que cubra ≥50 % de su perímetro (control positivo interno). Sin ella, «not
    assessable (no internal positive control)». `estructuras`: [{"fc", "geom" (µm de la
    referencia)}] de la máscara CK19 registrada."""
    from shapely.geometry import Point
    _comprueba(nuc_p63, ctx_p63)
    if ctx_p63.lamina != "P-P63":
        raise MetricaError("la puerta de p63 se mide en P-P63")
    p = ctx_p63.p
    pos = (nuc_p63.dab > ctx_p63.T) & ~nuc_p63.artefacto & nuc_p63.en_fc()
    porfc = {}
    for e in estructuras:
        f, g = e["fc"], e["geom"]
        d = porfc.setdefault(f, {"n_estructuras": 0, "n_con_control": 0, "estructuras": []})
        d["n_estructuras"] += 1
        sel = np.nonzero(pos & (nuc_p63.fc == f))[0]
        borde = g.exterior if hasattr(g, "exterior") else g.boundary
        cerca = [i for i in sel if borde.distance(Point(*nuc_p63.xy[i])) <= p["p63_banda_um"]]
        s = [borde.project(Point(*nuc_p63.xy[i])) for i in cerca]
        cob = _cobertura_arco(s, borde.length, p["p63_arco_nucleo_um"])
        ok = len(cerca) >= p["p63_min_nucleos"] and cob >= p["p63_arco_min"]
        d["n_con_control"] += int(ok)
        d["estructuras"].append({"n_p63": len(cerca), "arco": cob, "control": bool(ok)})
    registrados = {x for x in nuc_p63.fc if x is not None}
    out = {}
    for f, d in porfc.items():
        pasa = f in registrados and d["n_con_control"] >= 1
        out[f] = dict(d, pasa=bool(pasa), registrado=f in registrados,
                      rotulo=None if pasa else ROTULOS["p63_no_evaluable"])
    return out


def cifra_invasiva_p63(nuc, ctx, denominador, puerta, en_mioepitelio):
    """Cifra aparte «invasive-only where p63 available» (con la fracción cubierta): % sobre los
    núcleos del denominador en FC que pasan la puerta de p63 y fuera de estructuras con capa
    mioepitelial (`en_mioepitelio`, bool N, de los cortes registrados)."""
    _comprueba(nuc, ctx)
    m = nuc.mascara(denominador)
    pasan = {f for f, d in puerta.items() if d.get("pasa")}
    en = np.fromiter((f in pasan for f in nuc.fc), bool, count=len(nuc))
    sel = m & en & ~np.asarray(en_mioepitelio, bool).reshape(len(nuc))
    n = int(sel.sum())
    k = int(np.sum(nuc.dab[sel] > ctx.T))
    lo, hi = clopper_pearson(k, n)
    return {"k": k, "n": n, "pct": 100.0 * k / n if n else float("nan"),
            "ic95_binomial": [100 * lo, 100 * hi],
            "fraccion_cubierta": float(np.sum(m & en) / max(1, int(m.sum()))),
            "fragmentos": sorted(map(str, pasan)),
            "rotulos": [ROTULOS["p63_invasivo"]] + list(ctx.rotulos_umbral()),
            "alcance": ROTULOS["region_escaneada"], "umbral": ctx.cita()}


# ── (a-bis) regiones pobres en RE ──────────────────────────────────────────────────────────
def regiones_re_pobres(nuc_re, regiones, ctx, denominador, re_pasa_minipuerta, ck19=None,
                       p63=None, ki67_pct=None, p63_puerta=None):
    """Candidatas en la lámina de RE: regiones con ≥50 células, interior del fragmento y <10 %
    de núcleos sobre T; cada una con k/n, IC 95 % (Clopper-Pearson) y control positivo de RE a
    <500 µm; fuera si cae en artefacto, tercil bajo de foco o hematoxilina nuclear fuera de
    ±20 %. `re_pasa_minipuerta`: bool o {fc: bool} (punto 7: (a-bis) se limita a los fragmentos
    que pasan). `ck19`/`p63`: {clave de región: bool} / {clave: "negativo" | "periferia_positiva"
    | "not assessable"} (de los cortes registrados); `p63_puerta` (`puerta_p63`): en los FC que no
    la pasan, p63 es «not assessable». Ninguna se llama tumoral."""
    _comprueba(nuc_re, ctx)
    if ctx.lamina != "P-RE":
        raise MetricaError("(a-bis) se mide en la lámina de RE")
    if regiones:
        _regiones_de(regiones, ctx, denominador)
    if isinstance(re_pasa_minipuerta, dict):
        pasan_re = {f for f, v in re_pasa_minipuerta.items() if v}
    else:
        pasan_re = None if re_pasa_minipuerta else set()
    if pasan_re is not None and not pasan_re:
        return {"evaluado": False, "motivo": "RE no pasa su mini-puerta: (a-bis) no se evalúa"}
    from scipy.spatial import cKDTree
    from shapely.geometry import Point
    p = ctx.p
    T = ctx.T
    m = nuc_re.mascara(denominador)
    pos = (nuc_re.dab > T) & m
    claves = claves_de(nuc_re, regiones)
    arbol_pos = cKDTree(nuc_re.xy[pos]) if pos.any() else None
    hema_ref = float(np.median(nuc_re.hema[m])) if nuc_re.hema is not None and m.any() else None
    por_region = {}
    for i, c in enumerate(claves):
        if c in regiones:
            por_region.setdefault(c, []).append(i)
    cands, excluidas = [], []
    for c, r in sorted(regiones.items(), key=lambda kv: kv[0]):
        if r["n"] < p["re_pobre_min_n"] or r["dist_borde_um"] <= p["interior_um"]:
            continue
        if 100.0 * r["k"] / r["n"] >= p["re_pobre_pct"]:
            continue
        if pasan_re is not None and r["fc"] not in pasan_re:
            excluidas.append({"region": "%s:%d,%d" % c, "motivo": "RE mini-gate not passed in "
                              "this fragment"})
            continue
        ids = np.array(por_region.get(c, []), int)
        motivo = None
        if len(ids) and nuc_re.artefacto[ids].mean() > p["artefacto_max_frac"]:
            motivo = "artifact"
        elif nuc_re.foco is not None and len(ids) and np.median(nuc_re.foco[ids]) <= 1:
            motivo = "lowest focus tertile"
        elif hema_ref and nuc_re.hema is not None and len(ids):
            hm = float(np.median(nuc_re.hema[ids]))
            if abs(hm / hema_ref - 1) > p["hema_tolerancia"]:
                motivo = "nuclear haematoxylin outside ±20 %"
        if motivo:
            excluidas.append({"region": "%s:%d,%d" % c, "motivo": motivo})
            continue
        lo, hi = clopper_pearson(r["k"], r["n"])
        # control positivo: ≥N núcleos RE sobre T a <500 µm del borde de la región, fuera de ella
        ctrl = False
        if arbol_pos is not None:
            cx, cy = r["centro"]
            radio = p["control_positivo_dist_um"] + r["L"] * math.sqrt(2) / 2
            cerca = arbol_pos.query_ball_point((cx, cy), radio)
            pts = nuc_re.xy[pos][cerca] if cerca else np.zeros((0, 2))
            g = r["geom"]
            nfuera = sum(1 for x, y in pts if not g.contains(Point(x, y))
                         and g.distance(Point(x, y)) < p["control_positivo_dist_um"])
            ctrl = nfuera >= p["control_positivo_min_nucleos"]
        c_ck = None if ck19 is None else ck19.get(c)
        c_p63 = None if p63 is None else p63.get(c)
        if p63_puerta is not None and not (p63_puerta.get(r["fc"]) or {}).get("pasa"):
            c_p63 = "not assessable"
        if not c_ck:
            clase, en_recuento = ROTULOS["sin_ck19"], True
        elif c_p63 in ("periferia_positiva", "not assessable", None):
            clase, en_recuento = ROTULOS["mioepitelio"], False
        else:
            clase, en_recuento = ROTULOS["er_pobre_ck19"], True
        cands.append({"region": "%s:%d,%d" % c, "fc": r["fc"], "k": r["k"], "n": r["n"],
                      "pct": 100.0 * r["k"] / r["n"], "ic95": [100 * lo, 100 * hi],
                      "control_positivo_500um": bool(ctrl), "rotulo": ROTULOS["er_pobre"],
                      "clase": clase, "en_recuento": en_recuento, "p63": c_p63,
                      "caja_um": [float(v) for v in r["geom"].bounds],
                      "ki67_regional_pct": None if ki67_pct is None else ki67_pct.get(c)})
    lim = clopper_pearson(0, p["re_pobre_min_n"])[1]
    return {"evaluado": True, "candidatas": cands, "excluidas": excluidas,
            "limite_deteccion": {"n_min": p["re_pobre_min_n"], "ic95_sup_k0_pct": 100 * lim},
            "fragmentos_evaluados": None if pasan_re is None else sorted(map(str, pasan_re)),
            "umbral": ctx.cita(), "galeria": "L0 crops of RE, CK19, p63 and Ki67 at caja_um"}


def contenido_partida():
    """Lo que el módulo B propone sellar (secciones registro y métricas). T, vectores y la tasa
    de P-HER2 los pone la congelación (0), no este módulo."""
    import laminillas_registro as R
    return {"registro": dict(R.PARAMS_PARTIDA), "metricas": dict(PARAMS_PARTIDA)}


if __name__ == "__main__":
    import json
    print(json.dumps(PARAMS_PARTIDA, indent=1))
