#!/usr/bin/env python3
"""tools/laminillas_registro.py — registro de los cortes seriados del piloto de laminillas.

Plan «laminillas DFCI», F3 «Registro» y Puerta del piloto (ii)/(ii-bis). Procesador
`laminillas_registro`, venv `patologia`. Referencia: P-CK19, nunca P-HE. Euclídeo con escala 1.

ENTRADA (F3): nivel ×8 NATIVO (≈2,005 µm/px, sin remuestreo); imagen = ODsum sobre el I0 local, en
8 bits, con ecualización local y el relleno (zona no escaneada) pintado del color del vidrio local.
Nunca RGB, gris ni canal DAB. Toda lectura de píxeles pasa por el lector único
(`laminillas_lector`: abre, lee_region, zona_escaneada, i0_local); este módulo no abre ficheros.

CADENA, por pareja móvil→referencia (la referencia nunca es P-HE):
  1. Inicialización por el eslabón 1 (si se da): con matriz completa, o con ángulo y quiralidad
     (lo que guarda la ingesta; la traslación se rehace por centroides y fase de las máscaras,
     probando los dos signos del ángulo). Con ella, la QUIRALIDAD la fija el eslabón 1 (la otra
     solo se ajusta como alternativa informativa), las putativas se filtran a `init_radio_um` de
     lo que predice y, si SIFT no da un global aceptable, la global ES la del eslabón 1.
  2. SIFT de scikit-image + match_descriptors(max_ratio=0,8, cross_check=True) +
     ransac(EuclideanTransform, min_samples=2, residual_threshold=12 px, max_trials=5000, rng
     sellado), en las DOS quiralidades (volteo horizontal de la móvil). Global primero; se acepta
     con ≥ `min_inliers_global` inliers y, sin eslabón 1, ≥ `margen_global_min`× la otra
     quiralidad [inferido: el plan no da cifra para la global].
  3. Fragmentos de consenso (FC): píxel presente en ≥6 de 11 máscaras llevadas a la referencia
     (la que no tiene global cuenta como 0; en el piloto, KI67∩CK19). Por FC: SIFT restringido al
     FC; con ≥30 inliers su modelo se juzga y no hay respaldo (plan: «Si un FC no llega a 30
     inliers: (2)… (3)… (4)»); con <30 → correlación de fase sobre la ODsum → mapa de densidad
     nuclear (2 µm/px, σ 8 µm) → VALIS por subproceso al venv `valis` (proyectado a euclídeo con
     escala 1; con |escala − 1| > `valis_escala_tol`, rechazado). VALIS registra las imágenes ×8
     ENTERAS: corre UNA vez por pareja (si algún FC llega a él) y todos los FC usan su resultado;
     si no corre, cada intento lo dice («VALIS did not run: rc=…; <cola de stderr>»). Gana el
     primer respaldo que pasa la puerta; TODOS los intentos quedan con su p90 junto a la cifra
     (`intentos`).
  4. Puerta por FC: contención en campo común y TRE < 50 µm. TRE:
       (a) informativo: 70/30 de las putativas (tras ratio test, antes de RANSAC);
       (b) correlación local de hematoxilina a ×4 (ventanas 256 µm, paso 128, ≥80 % dentro del FC,
           pico/segundo ≥1,5, desplazamiento <128 µm); rige su p90 con ≥15 picos válidos;
       (b') lo mismo sobre la densidad de centroides nucleares (independiente de la tinción).
     Si el mapa de densidad se usó para registrar, (b') deja de ser independiente y rige solo (b);
     donde (b) no llega a 15 picos, el FC queda PENDIENTE (`pasa` = None) de la comprobación a
     ciegas, que pasa de informativa a puerta (`ResultadoRegistro.resuelve_comprobacion_ciegas`).
  5. Fragmento cuya rejilla no da 15 ventanas candidatas: hereda la global y se comprueba con el
     máximo de sus picos válidos (<50 µm, mínimo 5): «small fragment: registration verified on n
     windows»; con <5, «small fragment: registration not independently verified»; con ≥5 que no
     pasan (TRE o contención), «registration not verified».
  6. Triángulo de cierre (≤3°, ≤50 µm) POR FC, con las transformadas refinadas de ese FC en su
     centroide, y orden estimado de los cortes (similitud solo en FC verificados en ambas).
     `registra_serie` hace (ii)/(ii-bis) del piloto de una vez (o F3 con las 11).
  7. `registra_he`: puerta (iii) CK19↔P-HE y KI67↔P-HE por fragmento (inliers ≥30 y ≥2× la mejor
     alternativa —espejo o rejilla de ángulos—, cierre KI67→CK19→HE vs KI67→HE ≤3° y ≤50 µm en el
     centroide del fragmento, y TRE de F3). Si no pasa ninguno, el rótulo de F1.3.

LÍMITES DECLARADOS: VALIS recibe las imágenes ×8 de registro (PNG), no el TIFF nativo (el lector
único manda), y las reduce a su `max_processed_image_dim_px` por defecto (850 px) para registrar.
El convenio de `Slide.M` de VALIS 1.2.0 está COMPROBADO con VALIS real (2-oct-26: humo
`registro-valis` por la ventanilla, en `analisis`, y `tests/test_laminillas_valis.py`): error
< 0,6 px con transformada conocida, directo y en espejo; con PNG su salida no menciona JVM
ni Bio-Formats (lee con pyvips). [inferido, sin medir en ×8 reales]: a 850 px, la precisión de
VALIS en una lámina entera ronda la decena de µm, cerca del umbral de 50 µm. La puerta TRE
juzga lo que devuelva igual que cualquier otro respaldo. La comprobación a ciegas por dos
subagentes (F3) no es código: este módulo deja la lista de recortes y recibe su resultado.
Máscara de tejido: ODsum suavizada O desviación local de hematoxilina, a ~4 µm/px (plan: 4-8),
histéresis, mismo umbral en todas; umbrales de la hematoxilina [inferido]; la decide el piloto.
En P-HE, la hematoxilina se desmezcla con H de tanda y eosina de Ruifrok [inferido].

SELLO: los parámetros se leen del sello de la congelación (0) (`laminillas_sello`, sección
`registro`; vectores de tanda, sección `vectores`). Sin sello, solo parejas de láminas de suelo
(P-HER2NEG, P-HER2) y con los parámetros de partida rotulados «pre-freeze».

Transformadas: matrices 3×3 en µm, de la lámina móvil (su L0 × mpp_l0) a la referencia.
"""
import math
import os
import re
import subprocess
import sys
import tempfile

import numpy as np
from scipy import ndimage as ndi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import laminillas_sello as SELLO  # noqa: E402

REFERENCIA = "P-CK19"

# Parámetros DE PARTIDA (plan F3: «ratio 0,8; 12 px, de partida [inferido], se sellan en (0)»).
# El código de medida NO los lee de aquí: los lee del sello. Esto es lo que se propone sellar.
PARAMS_PARTIDA = {
    "factor_nivel": 8,               # ×8 nativo
    "factor_tre": 4,                 # (b) a ×4 (≈1,00 µm/px)
    "odsum_max_8bits": 2.0,          # ODsum que satura los 8 bits
    "clahe_kernel_um": 128.0,        # ecualización local
    "clahe_clip": 0.01,
    "sift_upsampling": 1,            # 2 (defecto skimage) cuadruplica la memoria en ×8 reales
    "ratio": 0.8,
    "cross_check": True,
    "min_samples": 2,
    "residual_px": 12,               # ≈25 µm a ×8
    "max_trials": 5000,
    "semilla": 20261001,
    "init_radio_um": 500.0,
    "min_inliers": 30,
    "tre_max_um": 50.0,
    "ventana_um": 256.0,
    "paso_um": 128.0,
    "frac_dentro": 0.80,
    "pico_ratio": 1.5,
    "desp_max_um": 128.0,
    "exclusion_pico_um": 40.0,       # radio alrededor del pico para buscar el segundo
    "min_picos": 15,
    "fragmento_min_picos": 5,
    "densidad_mpp": 2.0,
    "densidad_sigma_um": 8.0,
    "fase_angulos": [-6.0, 6.0, 0.5],
    "cierre_grados": 3.0,
    "cierre_um": 50.0,
    "fc_n_mascaras": 11,
    "fc_min_votos": 6,
    "fc_min_area_um2": 20000.0,      # [inferido] FC < 0,02 mm²: fuera (también del ≥50 %)
    "fc_margen_um": 100.0,           # [inferido] margen del SIFT restringido al FC
    "contencion_min": 0.95,          # [inferido] el plan no da cifra para la contención
    "mascara_mpp": 4.0,              # plan: 4-8 µm/px
    "mascara_sigma_um": 4.0,         # [inferido]
    "mascara_bajo": 0.04,            # [inferido] histéresis sobre ODsum suavizada
    "mascara_alto": 0.10,            # [inferido]
    "mascara_h_sigma_um": 8.0,       # [inferido] ventana de la desviación local de hematoxilina
    "mascara_h_bajo": 0.02,          # [inferido] histéresis sobre esa desviación (OD)
    "mascara_h_alto": 0.04,          # [inferido]
    "min_inliers_global": 30,        # [inferido] = min_inliers por FC; el plan no lo da
    "margen_global_min": 2.0,        # [inferido] sin eslabón 1: ≥2× la otra quiralidad
    "valis_escala_tol": 0.01,        # [inferido] |escala − 1| máxima antes de proyectar
    "he_margen_alternativa": 2.0,    # plan (iii): ≥2 veces la mejor alternativa
    "he_angulo_excluido": 15.0,      # [inferido] «otro ángulo» = a >15° del elegido
    "he_paso_angulos": 5.0,          # [inferido] rejilla de ángulos de la alternativa
    "orden_mpp": 8.0,
    "particion_70": 0.7,
}

# Parámetros sin cifra en el plan (van a Métodos como inferencia).
INFERIDOS = ("sift_upsampling", "init_radio_um", "exclusion_pico_um", "fc_min_area_um2",
             "fc_margen_um", "contencion_min", "mascara_sigma_um", "mascara_bajo", "mascara_alto",
             "mascara_h_sigma_um", "mascara_h_bajo", "mascara_h_alto", "min_inliers_global",
             "margen_global_min", "valis_escala_tol", "he_angulo_excluido", "he_paso_angulos",
             "fase_angulos", "clahe_kernel_um", "clahe_clip", "odsum_max_8bits")

# Declaraciones para Métodos (no son rótulos: el plan dice «se declara»).
METODOS = {
    "densidad": ("nuclear-density map used to register this fragment: (b') is not independent; "
                 "(b) alone governs; where (b) has fewer than 15 valid peaks, the blind check is "
                 "the gate"),
    "eleccion": ("fallback methods tried only when the fragment's SIFT has <30 inliers; the first "
                 "that passes the TRE gate is kept; every attempt is listed with its p90"),
    "sift_upsampling": "SIFT upsampling 1 (scikit-image default 2 quadruples memory at x8)",
    "valis": "VALIS output projected to the nearest Euclidean transform (scale 1)",
    "mascara": ("tissue mask: smoothed ODsum or local haematoxylin deviation at ~4 um/px, "
                "hysteresis, same thresholds on every slide"),
    "he_vectores": "P-HE haematoxylin unmixed with the batch H vector and Ruifrok's eosin",
}

ROTULOS = {
    "no_verificado": "registration not verified",
    "pequeno_verificado": "small fragment: registration verified on {n} windows",
    "pequeno_no_verificado": "small fragment: registration not independently verified",
    "orden": "estimated section order; spacing unknown",
    "consenso_piloto": "pilot consensus (KI67∩CK19; HER2NEG and HER2 mapped onto it)",
    "comprobacion_inconclusa": "registration check inconclusive",
    "ae1ae3": ("insufficient detectable tissue on this scan for registration (cause not "
               "determined: pale counterstain, depleted section or focus)"),
    "he_no_registrada": ("same tissue block across the 11 IHC slides (fingerprint); H&E from a "
                         "different section level, positioned at core level only, not "
                         "co-registered; link to its accession by sender's file name"),
    "pre_congelacion": "pre-freeze parameters (floor slides only; not sealed)",
}

RUIFROK = {"H": [0.65, 0.70, 0.29], "DAB": [0.27, 0.57, 0.78]}
RUIFROK_EOSINA = [0.07, 0.99, 0.11]
LAMINAS_HE = ("P-HE",)
PENDIENTE_CIEGAS = "pending: blind check as gate"


class RegistroError(RuntimeError):
    pass


# ── lector único (inyectable) ──────────────────────────────────────────────────────────────
def _lector(lector):
    if lector is not None:
        return lector
    try:
        import laminillas_lector
    except ImportError as e:
        raise RegistroError("falta el lector único (tools/laminillas_lector.py): no leo píxeles "
                            "por mi cuenta (%s)" % e)
    return laminillas_lector


def mpp_nivel(lamina, factor):
    """mpp del nivel NATIVO de factor `factor` (sin remuestreo). `Lamina.niveles` del lector:
    [(factor, mpp, (w, h))]; también dict {factor: mpp}."""
    niv = getattr(lamina, "niveles", None)
    cand = None
    if isinstance(niv, dict):
        cand = niv.get(factor, niv.get(str(factor)))
    elif isinstance(niv, (list, tuple)):
        for n in niv:
            if int(n[0]) == int(factor):
                cand = n[1]
    if cand is None:
        raise RegistroError("la lámina no tiene nivel nativo ×%d (no remuestreo)" % factor)
    return float(cand)


def _lee(lector, lamina, mpp, x_l0, y_l0, w, h):
    rgb = np.asarray(lector.lee_region(lamina, mpp, int(x_l0), int(y_l0), int(w), int(h)))
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] < 3:
        raise RegistroError("lee_region devolvió algo que no es RGB uint8")
    return rgb[..., :3]


def campo_i0(lector, lamina, mpp, x_l0, y_l0, w, h, paso=16):
    """I0 local (H×W×3, float32) en los centros de píxel de una región. `i0_local(lamina)` del
    lector es f(x_l0, y_l0) → RGB (bilineal sobre el raster de la ingesta, ~1 mm); se evalúa cada
    `paso` píxeles y se interpola (el campo es suave), para no crear rejillas de 20 Mpx en ×8.
    Se admite también un RGB constante."""
    i0 = lector.i0_local(lamina)
    if not callable(i0):
        arr = np.asarray(i0, np.float32).reshape(1, 1, 3)
        return np.broadcast_to(arr, (h, w, 3)).astype(np.float32)
    f = mpp / float(lamina.mpp_l0)
    if h * w <= 1 << 20:
        X, Y = np.meshgrid(x_l0 + (np.arange(w) + 0.5) * f, y_l0 + (np.arange(h) + 0.5) * f)
        return np.broadcast_to(np.asarray(i0(X, Y), np.float32), (h, w, 3)).astype(np.float32)
    cw, ch = int(math.ceil(w / paso)) + 1, int(math.ceil(h / paso)) + 1
    X, Y = np.meshgrid(x_l0 + (np.arange(cw) * paso + 0.5) * f,
                       y_l0 + (np.arange(ch) * paso + 0.5) * f)
    gruesa = np.asarray(i0(X, Y), np.float32).reshape(ch, cw, 3)
    Ar, Ac = _interp_lineal(h, ch, paso), _interp_lineal(w, cw, paso)
    out = np.empty((h, w, 3), np.float32)
    for k in range(3):
        out[..., k] = Ar @ (Ac @ gruesa[..., k].T).T          # separable: filas y columnas
    return out


def _interp_lineal(n, m, paso):
    """Matriz dispersa n×m de interpolación lineal de una rejilla gruesa (nodo j en j·paso)."""
    from scipy.sparse import csr_matrix
    t = np.arange(n) / float(paso)
    j0 = np.clip(np.floor(t).astype(int), 0, m - 2)
    a = (t - j0).astype(np.float32)
    filas = np.r_[np.arange(n), np.arange(n)]
    return csr_matrix((np.r_[1 - a, a], (filas, np.r_[j0, j0 + 1])), shape=(n, m))


def _poligono_mascara(geom, shape, mpp, x0_um, y0_um, mpp_geom=1.0):
    """Rasteriza un polígono shapely (coordenadas × mpp_geom = µm) en una rejilla."""
    from skimage.draw import polygon as dpoly
    m = np.zeros(shape, bool)
    if geom is None or geom.is_empty:
        return m
    for p in getattr(geom, "geoms", [geom]):
        ext = np.asarray(p.exterior.coords) * mpp_geom
        rr, cc = dpoly((ext[:, 1] - y0_um) / mpp - 0.5, (ext[:, 0] - x0_um) / mpp - 0.5, shape)
        m[rr, cc] = True
        for hueco in p.interiors:
            hi = np.asarray(hueco.coords) * mpp_geom
            rr, cc = dpoly((hi[:, 1] - y0_um) / mpp - 0.5, (hi[:, 0] - x0_um) / mpp - 0.5, shape)
            m[rr, cc] = False
    return m


def od(rgb, i0):
    r = np.maximum(rgb.astype(np.float32), 1.0) / np.maximum(np.asarray(i0, np.float32), 1.0)
    return np.clip(-np.log10(r), 0, None)


def matriz_desmezcla(vectores):
    """Inversa de la matriz de tinción (H, DAB, tercero = producto vectorial)."""
    h = np.asarray(vectores["H"], float)
    d = np.asarray(vectores["DAB"], float)
    h, d = h / np.linalg.norm(h), d / np.linalg.norm(d)
    t = np.asarray(vectores.get("tercero") or np.cross(h, d), float)
    t = t / np.linalg.norm(t)
    return np.linalg.inv(np.stack([h, d, t]))


def hematoxilina(od_img, vectores):
    c = od_img.reshape(-1, 3) @ matriz_desmezcla(vectores)
    return np.clip(c[:, 0], 0, None).reshape(od_img.shape[:2])


# ── imagen de registro (×8) ────────────────────────────────────────────────────────────────
def vectores_de(nombre, sello=None):
    """Vectores de desmezcla de una lámina: los de tanda del sello (con su DAB propio si lo
    lleva); en P-HE, H de tanda y eosina de Ruifrok [inferido]; sin sello, Ruifrok."""
    v = sello.vectores(nombre) if sello is not None else dict(RUIFROK)
    if nombre in LAMINAS_HE:
        return {"H": v["H"], "DAB": list(RUIFROK_EOSINA), "tercero": None}
    return v


class ImagenRegistro:
    """ODsum de una lámina a ×8, en 8 bits ecualizada (float32), con su máscara de zona y de
    tejido. La ODsum y la hematoxilina a ×8 no se retienen (memoria: ≈4 bytes/px en vez de ≈14);
    queda `odsum_fuera_max` (|ODsum| máximo en el relleno pintado) para el QC."""

    def __init__(self, lector, nombre, p, vectores=None, franja=1024, retiene=False):
        """`retiene` (solo el diagnóstico): guarda además la ODsum en 8 bits ANTES de la
        ecualización (`b8`) y la hematoxilina en 8 bits (`hema8`, H OD recortada a 1,0). No cambia
        `img` ni `tejido`."""
        from skimage.exposure import equalize_adapthist
        lam = lector.abre(nombre)
        self.nombre, self.lamina = nombre, lam
        self.mpp_l0 = float(lam.mpp_l0)
        self.mpp = mpp_nivel(lam, int(p["factor_nivel"]))
        W0, H0 = lam.dimensiones_l0
        w = int(W0 * self.mpp_l0 / self.mpp)
        h = int(H0 * self.mpp_l0 / self.mpp)
        rgb = _lee(lector, lam, self.mpp, 0, 0, w, h)
        h, w = rgb.shape[:2]
        self.zona_geom = lector.zona_escaneada(lam)              # px L0
        self.zona = _poligono_mascara(self.zona_geom, (h, w), self.mpp, 0, 0, self.mpp_l0)
        Minv = matriz_desmezcla(vectores or vectores_de(nombre))
        odsum = np.empty((h, w), np.float32)
        hema = np.empty((h, w), np.float32)
        f = self.mpp / self.mpp_l0
        fuera_max = 0.0
        for y0 in range(0, h, franja):                            # por franjas: pico de memoria
            y1 = min(h, y0 + franja)
            i0 = campo_i0(lector, lam, self.mpp, 0, y0 * f, w, y1 - y0)
            tira = rgb[y0:y1].astype(np.float32)
            z = self.zona[y0:y1]
            tira[~z] = i0[~z]                                     # relleno pintado del vidrio local
            o = od(tira, i0)
            odsum[y0:y1] = o.sum(-1)
            hema[y0:y1] = np.clip(o.reshape(-1, 3) @ Minv, 0, None)[:, 0].reshape(y1 - y0, w)
            if (~z).any():
                fuera_max = max(fuera_max, float(np.abs(odsum[y0:y1][~z]).max()))
        del rgb
        self.odsum_fuera_max = fuera_max
        self.ancho_um, self.alto_um = w * self.mpp, h * self.mpp
        b8 = np.round(np.clip(odsum / p["odsum_max_8bits"], 0, 1) * 255).astype(np.uint8)
        k = max(8, int(round(p["clahe_kernel_um"] / self.mpp)))
        self.img = equalize_adapthist(b8, kernel_size=k, clip_limit=p["clahe_clip"]).astype(
            np.float32)
        self.tejido = mascara_tejido(odsum, self.mpp, p, hema) & self.zona
        if retiene:
            self.b8 = b8
            self.hema8 = np.round(np.clip(hema, 0, 1.0) * 255).astype(np.uint8)

    def px_a_um(self):
        return np.array([[self.mpp, 0, 0.5 * self.mpp], [0, self.mpp, 0.5 * self.mpp], [0, 0, 1.0]])


def _diezma(a, f):
    """Media por bloques f×f (recorta al múltiplo)."""
    if f <= 1:
        return a.astype(np.float32)
    h, w = (a.shape[0] // f) * f, (a.shape[1] // f) * f
    return a[:h, :w].reshape(h // f, f, w // f, f).mean(axis=(1, 3)).astype(np.float32)


def mascara_tejido(odsum, mpp, p, hema=None):
    """Candidata de F3 (decide el piloto): a ~`mascara_mpp` (4 µm/px), ODsum suavizada con
    histéresis O desviación local de hematoxilina con histéresis; mismo umbral en todas. Se
    devuelve en la rejilla de entrada."""
    from skimage.filters import apply_hysteresis_threshold
    from skimage.morphology import remove_small_objects
    f = max(1, int(round(float(p["mascara_mpp"]) / mpp)))
    mpp_d = mpp * f
    s = ndi.gaussian_filter(_diezma(odsum, f), p["mascara_sigma_um"] / mpp_d)
    m = apply_hysteresis_threshold(s, p["mascara_bajo"], p["mascara_alto"])
    if hema is not None:
        hd = _diezma(hema, f)
        sg = p["mascara_h_sigma_um"] / mpp_d
        mu = ndi.gaussian_filter(hd, sg)
        de = np.sqrt(np.clip(ndi.gaussian_filter(hd * hd, sg) - mu * mu, 0, None))
        m |= apply_hysteresis_threshold(de, p["mascara_h_bajo"], p["mascara_h_alto"])
    m = ndi.binary_fill_holes(m)
    m = remove_small_objects(m, max_size=max(0, int(p["fc_min_area_um2"] / mpp_d ** 2) - 1))
    if f > 1:
        m = np.repeat(np.repeat(m, f, 0), f, 1)
        out = np.zeros(odsum.shape, bool)
        hh, ww = min(m.shape[0], out.shape[0]), min(m.shape[1], out.shape[1])
        out[:hh, :ww] = m[:hh, :ww]
        if hh < out.shape[0]:
            out[hh:, :ww] = m[hh - 1:hh, :ww]
        if ww < out.shape[1]:
            out[:, ww:] = out[:, ww - 1:ww]
        m = out
    return m


# ── SIFT ───────────────────────────────────────────────────────────────────────────────────
class Rasgos:
    def __init__(self, xy_um, desc, n_bruto=None):
        self.xy = xy_um
        self.desc = desc
        self.n_bruto = len(xy_um) if n_bruto is None else int(n_bruto)   # antes de la máscara


def rasgos_sift(ir, p, espejo=False):
    """SIFT sobre el recorte de tejido; coordenadas devueltas en µm de la lámina (si `espejo`,
    ya reflejadas: x → −x, para ajustar un euclídeo a la quiralidad opuesta). Se calcula una vez
    por imagen, quiralidad y parámetros (determinista)."""
    clave = (bool(espejo), int(p["sift_upsampling"]))
    cache = ir.__dict__.setdefault("_rasgos", {})
    if clave not in cache:
        cache[clave] = _rasgos_sift(ir, p, espejo)
    return cache[clave]


def _rasgos_sift(ir, p, espejo):
    from skimage.feature import SIFT
    vacio = Rasgos(np.zeros((0, 2)), np.zeros((0, 128), np.uint8))
    zona = ndi.binary_dilation(ir.tejido, iterations=8)          # tejido + 16 µm
    filas, cols = np.nonzero(zona)
    if len(filas) == 0:
        return vacio
    r0, r1, c0, c1 = filas.min(), filas.max() + 1, cols.min(), cols.max() + 1
    rec = ir.img[r0:r1, c0:c1]
    if espejo:
        rec = rec[:, ::-1]
    s = SIFT(upsampling=int(p["sift_upsampling"]))
    try:
        s.detect_and_extract(rec)
    except RuntimeError:
        return vacio
    kp = s.keypoints.astype(float)                      # (fila, col) del recorte
    col = kp[:, 1]
    if espejo:
        col = (rec.shape[1] - 1) - col
    fila_img = np.clip((kp[:, 0] + r0).astype(int), 0, zona.shape[0] - 1)
    col_img = np.clip((col + c0).astype(int), 0, zona.shape[1] - 1)
    dentro = zona[fila_img, col_img]
    xy = np.c_[(col + c0 + 0.5) * ir.mpp, (kp[:, 0] + r0 + 0.5) * ir.mpp][dentro]
    if espejo:
        xy = xy * np.array([-1.0, 1.0])
    return Rasgos(xy, s.descriptors[dentro], n_bruto=len(kp))


F_ESPEJO = np.diag([-1.0, 1.0, 1.0])


def _aplica(M, xy):
    xy = np.asarray(xy, float).reshape(-1, 2)
    return xy @ M[:2, :2].T + M[:2, 2]


def angulo(M):
    E = M @ F_ESPEJO if np.linalg.det(M[:2, :2]) < 0 else M
    return math.degrees(math.atan2(E[1, 0], E[0, 0]))


def putativas(rf, rm, p, sel_f=None, sel_m=None):
    from skimage.feature import match_descriptors
    if sel_f is None:
        sel_f = np.ones(len(rf.xy), bool)
    if sel_m is None:
        sel_m = np.ones(len(rm.xy), bool)
    i_f, i_m = np.nonzero(sel_f)[0], np.nonzero(sel_m)[0]
    if len(i_f) < 2 or len(i_m) < 2:
        return np.zeros((0, 2)), np.zeros((0, 2))
    pares = match_descriptors(rf.desc[i_f], rm.desc[i_m], max_ratio=p["ratio"],
                              cross_check=bool(p["cross_check"]))
    return rm.xy[i_m[pares[:, 1]]], rf.xy[i_f[pares[:, 0]]]      # (origen móvil, destino fija)


def ajusta_ransac(src, dst, p, mpp, semilla_extra=0):
    """Euclídeo src→dst (µm) con los parámetros sellados; residual en px de ×8 → µm."""
    from skimage.measure import ransac
    from skimage.transform import EuclideanTransform
    if len(src) < max(int(p["min_samples"]), 3):
        return None, np.zeros(len(src), bool)
    rng = np.random.default_rng(int(p["semilla"]) + semilla_extra)
    modelo, inl = ransac((src, dst), EuclideanTransform, min_samples=int(p["min_samples"]),
                         residual_threshold=float(p["residual_px"]) * mpp,
                         max_trials=int(p["max_trials"]), rng=rng)
    # skimage ≥0.26 devuelve un `FailedEstimation` (falso al evaluarlo) en vez de None
    if modelo is None or not modelo or inl is None:
        return None, np.zeros(len(src), bool)
    return np.asarray(modelo.params, float), np.asarray(inl, bool)


def tre_particion(src, dst, p, mpp):
    """(a) informativo: ajuste con el 70 % de las putativas, fracción del 30 % a <50 µm."""
    n = len(src)
    if n < 10:
        return {"n_putativas": int(n), "fraccion_30_bajo_umbral": None}
    rng = np.random.default_rng(int(p["semilla"]) + 7)
    orden = rng.permutation(n)
    k = int(round(n * p["particion_70"]))
    a, b = orden[:k], orden[k:]
    M, _ = ajusta_ransac(src[a], dst[a], p, mpp, semilla_extra=11)
    if M is None:
        return {"n_putativas": int(n), "fraccion_30_bajo_umbral": None}
    err = np.linalg.norm(_aplica(M, src[b]) - dst[b], axis=1)
    return {"n_putativas": int(n), "n_prueba": int(len(b)),
            "fraccion_30_bajo_umbral": float(np.mean(err < p["tre_max_um"])),
            "nota": "informative only, not a gate"}


def _con_espejo(M_e, espejo):
    return M_e @ F_ESPEJO if espejo else M_e


def registro_global(ir_f, ir_m, p, init=None, rasgos=None):
    """SIFT + RANSAC en las dos quiralidades. Devuelve dict con la elegida y la alternativa.

    Con `init` (eslabón 1, ya con `matriz_um`), solo es elegible SU quiralidad (filtrada a
    `init_radio_um` de lo que predice); la otra se ajusta como alternativa informativa. La
    elegida se ACEPTA con ≥ `min_inliers_global` inliers y, sin eslabón 1, ≥ `margen_global_min`×
    la otra; si no, su `matriz_um` es None (`aceptada` False, con el motivo): un RANSAC de 2
    inliers no decide la quiralidad."""
    rf = rasgos_sift(ir_f, p) if rasgos is None else rasgos["f"]
    out = {}
    for espejo in (False, True):
        rm = (rasgos_sift(ir_m, p, espejo) if rasgos is None
              else rasgos["m_espejo" if espejo else "m"])
        src, dst = putativas(rf, rm, p)
        filtrado = False
        elegible = init is None or bool(init["espejo"]) == espejo
        if init is not None and elegible and len(src):
            Mi = np.asarray(init["matriz_um"], float)
            pred = _aplica(Mi @ (F_ESPEJO if espejo else np.eye(3)), src)
            ok = np.linalg.norm(pred - dst, axis=1) < p["init_radio_um"]
            src, dst, filtrado = src[ok], dst[ok], True
        M_e, inl = ajusta_ransac(src, dst, p, ir_f.mpp)
        out["espejo" if espejo else "directa"] = {
            "espejo": espejo, "matriz_um": None if M_e is None else _con_espejo(M_e, espejo),
            "n_putativas": int(len(src)), "n_inliers": int(inl.sum()), "filtrado_por_init": filtrado,
            "elegible": bool(elegible), "_src": src, "_dst": dst, "_inl": inl, "_rasgos_m": rm}
    d, e = out["directa"], out["espejo"]
    if init is not None:
        elegida = d if d["elegible"] else e
    else:
        elegida = d if d["n_inliers"] >= e["n_inliers"] else e
    otra = e if elegida is d else d
    margen = float(elegida["n_inliers"] / max(1, otra["n_inliers"]))
    motivo = None
    if elegida["matriz_um"] is None:
        motivo = "RANSAC without a model"
    elif elegida["n_inliers"] < int(p["min_inliers_global"]):
        motivo = "<%d inliers" % int(p["min_inliers_global"])
    elif init is None and margen < float(p["margen_global_min"]):
        motivo = "chirality margin <%gx" % float(p["margen_global_min"])
    if motivo is not None:
        elegida = dict(elegida, matriz_um=None, matriz_rechazada=elegida["matriz_um"])
    return {"elegida": elegida, "alternativa": otra, "margen_inliers": margen,
            "aceptada": motivo is None, "motivo": motivo, "rasgos_f": rf}


def _init_matriz(ir_f, ir_m, init):
    """Normaliza la inicialización: {matriz_um, espejo, …} o, desde el eslabón 1 (ángulo y
    quiralidad), la matriz por centroides y fase de las máscaras. None si no se puede."""
    if init is None:
        return None
    if init.get("matriz_um") is not None:
        M = np.asarray(init["matriz_um"], float)
        return {"matriz_um": M, "espejo": bool(np.linalg.det(M[:2, :2]) < 0),
                "origen": init.get("origen", "matriz"), "iou_mascaras": init.get("iou_mascaras")}
    Mi, iou = init_desde_eslabon1(ir_f, ir_m, init["angulo_grados"], bool(init["espejo"]))
    if Mi is None:
        return None
    return {"matriz_um": Mi, "espejo": bool(init["espejo"]), "iou_mascaras": iou,
            "origen": "eslabon1"}


def global_con_init(ir_f, ir_m, p, init=None):
    """Global de una pareja: SIFT (`registro_global`) y, si no da una aceptable y hay eslabón 1,
    la del eslabón 1. Devuelve (g, elegida, init normalizado)."""
    init = _init_matriz(ir_f, ir_m, init)
    g = registro_global(ir_f, ir_m, p, init)
    el = g["elegida"]
    if el["matriz_um"] is None and init is not None:
        el = dict(el, matriz_um=np.asarray(init["matriz_um"], float), espejo=init["espejo"],
                  _rasgos_m=rasgos_sift(ir_m, p, init["espejo"]), origen="eslabon1")
    return g, el, init


def traslacion_iou(A, B):
    """Desplazamiento entero d = (dy, dx) que maximiza la IoU entre A y B desplazada (el píxel q
    de B cae en el q + d de A), y esa IoU. Correlación cruzada por FFT con relleno de ceros (sin
    vuelta circular): el criterio del eslabón 1 de la ingesta (`busca_par`, IoU por FFT)."""
    from scipy import fft
    A = np.asarray(A, np.float32)
    B = np.asarray(B, np.float32)
    S = (fft.next_fast_len(A.shape[0] + B.shape[0]), fft.next_fast_len(A.shape[1] + B.shape[1]))
    inter = fft.irfft2(fft.rfft2(A, s=S, workers=-1) * np.conj(fft.rfft2(B, s=S, workers=-1)),
                       s=S, workers=-1)
    iou = inter / np.maximum(float(A.sum()) + float(B.sum()) - inter, 1e-9)
    del inter
    k = np.unravel_index(int(np.argmax(iou)), iou.shape)
    d = tuple(int(k[i]) if k[i] < A.shape[i] else int(k[i]) - S[i] for i in (0, 1))
    return d, float(iou[k])


def _recorta(m):
    ys, xs = np.nonzero(m)
    if not len(ys):
        return m[:1, :1], (0, 0)
    return m[ys.min():ys.max() + 1, xs.min():xs.max() + 1], (int(ys.min()), int(xs.min()))


def _mascara_llevada(ir_m, L, mpp):
    """La máscara de tejido de la móvil llevada con L (µm) a un lienzo propio que la contiene
    ENTERA (no se recorta al marco de la fija), a `mpp`. Devuelve (máscara, origen µm (x, y))."""
    from skimage.transform import AffineTransform, warp
    h, w = ir_m.tejido.shape
    esq = _aplica(L, [(0, 0), (w * ir_m.mpp, 0), (0, h * ir_m.mpp), (w * ir_m.mpp, h * ir_m.mpp)])
    o = esq.min(0)
    wc = int(math.ceil((esq[:, 0].max() - o[0]) / mpp)) + 1
    hc = int(math.ceil((esq[:, 1].max() - o[1]) / mpp)) + 1
    P_c = np.array([[mpp, 0, o[0] + 0.5 * mpp], [0, mpp, o[1] + 0.5 * mpp], [0, 0, 1.0]])
    inv = np.linalg.inv(ir_m.px_a_um()) @ np.linalg.inv(L) @ P_c
    B = warp(ir_m.tejido.astype(np.float32), AffineTransform(matrix=inv), output_shape=(hc, wc),
             order=0, cval=0, preserve_range=True) > 0.5
    return B, o


def init_por_iou(ir_f, ir_m, L):
    """Traslación que, tras la parte lineal L (giro y quiralidad, µm), maximiza la IoU de las
    máscaras de tejido a la rejilla de la fija. Devuelve (M, iou)."""
    mpp = ir_f.mpp
    B, o = _mascara_llevada(ir_m, L, mpp)
    A, (ay, ax) = _recorta(ir_f.tejido)
    B, (by, bx) = _recorta(B)
    (dy, dx), iou = traslacion_iou(A, B)
    t = np.array([mpp * (ax + dx - bx) - o[0], mpp * (ay + dy - by) - o[1]])
    return np.array([[1, 0, t[0]], [0, 1, t[1]], [0, 0, 1.0]]) @ L, iou


def init_desde_eslabon1(ir_f, ir_m, angulo_grados, espejo):
    """Matriz inicial (µm, móvil→fija) desde el eslabón 1 de la ingesta, que guarda ángulo y
    quiralidad pero no la traslación (su FFT es sobre máscaras recortadas a 32 µm/px). Se prueban
    los dos signos del ángulo (el convenio de `ndi.rotate` frente a x→derecha, y→abajo no se da
    por supuesto) y, con cada uno, la traslación de MÁXIMA IoU de las máscaras a ×8 (correlación
    cruzada por FFT con relleno de ceros: el criterio del propio eslabón 1); gana el de mayor IoU.
    Devuelve (matriz, iou).

    Hasta el 2-oct-26 se centraba el tejido por centroides y se afinaba con correlación de FASE
    de las máscaras. En el piloto real (KI67→CK19, diagnóstico por la ventanilla) esa fase BAJÓ
    la IoU de 0,565 (centroides) a 0,454 y dejó la global a 634-750 µm de VALIS, fuera del radio
    de 500 µm del filtro de putativas y del margen de 100 µm del SIFT por FC; con máxima IoU,
    0,608 y 17-120 µm. Con cilindros alargados parecidos, la fase salta un cilindro."""
    if not ir_m.tejido.any() or not ir_f.tejido.any():
        return None, 0.0
    mejor = (None, -1.0)
    for signo in (1.0, -1.0):
        a = math.radians(signo * float(angulo_grados))
        R = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1.0]])
        L = R @ (F_ESPEJO if espejo else np.eye(3))
        M, _iou_lienzo = init_por_iou(ir_f, ir_m, L)
        m_ref = warp_a_referencia(ir_m.tejido, ir_m, ir_f, M)
        inter = np.logical_and(m_ref, ir_f.tejido).sum()
        iou = inter / max(1, np.logical_or(m_ref, ir_f.tejido).sum())
        if iou > mejor[1]:
            mejor = (M, float(iou))
    return mejor


# ── llevar máscaras a la referencia y fragmentos de consenso ───────────────────────────────
def warp_a_referencia(arr, ir_origen, ir_ref, M, orden=0):
    """Lleva un raster de la rejilla ×8 de `ir_origen` a la de `ir_ref` con M (µm, origen→ref)."""
    from skimage.transform import AffineTransform, warp
    inv = np.linalg.inv(ir_origen.px_a_um()) @ np.linalg.inv(M) @ ir_ref.px_a_um()
    out = warp(arr.astype(float), AffineTransform(matrix=inv), output_shape=ir_ref.img.shape,
               order=orden, cval=0, preserve_range=True)
    return out > 0.5 if arr.dtype == bool else out


def fragmentos_consenso(mascaras_ref, mpp, p, min_votos=None):
    """FC = píxel presente en ≥ min_votos de las máscaras (ya en la referencia). Con 11 máscaras,
    6 (plan). Con otro número, `min_votos` explícito."""
    from skimage.measure import label
    n = len(mascaras_ref)
    if min_votos is None:
        if n != int(p["fc_n_mascaras"]):
            raise RegistroError("FC: el plan fija %d de %d máscaras; con %d, min_votos explícito"
                                % (p["fc_min_votos"], p["fc_n_mascaras"], n))
        min_votos = int(p["fc_min_votos"])
    votos = np.sum([m.astype(np.uint8) for m in mascaras_ref], axis=0)
    et = label(votos >= min_votos)
    fcs = []
    for i in range(1, et.max() + 1):
        m = et == i
        if m.sum() * mpp ** 2 < p["fc_min_area_um2"]:
            continue
        fcs.append(m)
    fcs.sort(key=lambda m: -m.sum())
    return [{"id": "FC%d" % (k + 1), "mascara": m, "mpp": float(mpp),
             "area_um2": float(m.sum() * mpp ** 2),
             "poligono_um": mascara_a_poligono(m, mpp)} for k, m in enumerate(fcs)]


def fc_piloto(mascara_ki67_ref, mascara_ck19, mpp, p):
    fcs = fragmentos_consenso([mascara_ki67_ref, mascara_ck19], mpp, p, min_votos=2)
    for f in fcs:
        f["rotulo"] = ROTULOS["consenso_piloto"]
    return fcs


def mascara_a_poligono(m, mpp):
    from shapely.geometry import Polygon
    from skimage.measure import find_contours
    pad = np.pad(m.astype(float), 1)
    mejor = None
    for c in find_contours(pad, 0.5):
        if len(c) < 4:
            continue
        pol = Polygon(np.c_[(c[:, 1] - 1 + 0.5) * mpp, (c[:, 0] - 1 + 0.5) * mpp]).buffer(0)
        if mejor is None or pol.area > mejor.area:
            mejor = pol
    return mejor.simplify(mpp) if mejor is not None else None


# ── TRE (b) y (b') ─────────────────────────────────────────────────────────────────────────
def rejilla_ventanas(fc_mask, mpp_mask, p):
    """Ventanas de 256 µm con paso de 128 µm sobre el FC (origen: esquina de su caja), con ≥80 %
    de su área dentro del FC. Devuelve [(x0_um, y0_um)] y el nº de candidatas."""
    filas, cols = np.nonzero(fc_mask)
    if len(filas) == 0:
        return []
    x_min, y_min = cols.min() * mpp_mask, filas.min() * mpp_mask
    x_max, y_max = (cols.max() + 1) * mpp_mask, (filas.max() + 1) * mpp_mask
    L, paso = p["ventana_um"], p["paso_um"]
    integ = np.pad(fc_mask.astype(np.int64).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    out = []
    y = y_min
    while y + L <= y_max + 1e-6:
        x = x_min
        while x + L <= x_max + 1e-6:
            r0, c0 = int(round(y / mpp_mask)), int(round(x / mpp_mask))
            r1, c1 = int(round((y + L) / mpp_mask)), int(round((x + L) / mpp_mask))
            r1, c1 = min(r1, fc_mask.shape[0]), min(c1, fc_mask.shape[1])
            tot = (r1 - r0) * (c1 - c0)
            dentro = integ[r1, c1] - integ[r0, c1] - integ[r1, c0] + integ[r0, c0]
            if tot > 0 and dentro / tot >= p["frac_dentro"]:
                out.append((x, y))
            x += paso
        y += paso
    return out


def _pico(superficie, m_px, mpp, p):
    """Pico, segundo pico (fuera de la exclusión) y desplazamiento en µm de una NCC."""
    i = np.unravel_index(np.argmax(superficie), superficie.shape)
    pico = float(superficie[i])
    rr, cc = np.ogrid[:superficie.shape[0], :superficie.shape[1]]
    excl = (rr - i[0]) ** 2 + (cc - i[1]) ** 2 <= (p["exclusion_pico_um"] / mpp) ** 2
    resto = np.where(excl, -np.inf, superficie)
    segundo = float(resto.max()) if np.isfinite(resto.max()) else -np.inf
    dy, dx = (i[0] - m_px) * mpp, (i[1] - m_px) * mpp
    desp = float(math.hypot(dx, dy))
    razon = float("inf") if segundo <= 0 else pico / segundo
    borde = i[0] in (0, superficie.shape[0] - 1) or i[1] in (0, superficie.shape[1] - 1)
    valido = (pico > 0 and razon >= p["pico_ratio"] and desp < p["desp_max_um"] and not borde)
    return {"desp_um": desp, "dx_um": float(dx), "dy_um": float(dy), "pico": pico,
            "razon": razon if math.isfinite(razon) else 1e9, "valido": bool(valido),
            "borde": bool(borde)}


def _ncc(grande, plantilla):
    from skimage.feature import match_template
    if plantilla.std() < 1e-9 or grande.std() < 1e-9:
        return None
    return match_template(grande, plantilla)


def tre_hematoxilina(lector, ir_f, ir_m, M, ventanas, p, vectores):
    """(b): por ventana, NCC de hematoxilina a ×4 entre la fija y la móvil llevada con M.
    `vectores`: {lámina: {H, DAB, tercero}} (cada una con su DAB propio si lo lleva)."""
    from skimage.transform import AffineTransform, warp
    lf, lm = ir_f.lamina, ir_m.lamina
    mpp4 = mpp_nivel(lf, int(p["factor_tre"]))
    mpp4m = mpp_nivel(lm, int(p["factor_tre"]))
    L = p["ventana_um"]
    m_um = p["desp_max_um"]
    n_px = int(round(L / mpp4))
    m_px = int(round(m_um / mpp4))
    Minv = np.linalg.inv(M)
    res = []
    for (x0, y0) in ventanas:
        xf_l0, yf_l0 = int(round(x0 / ir_f.mpp_l0)), int(round(y0 / ir_f.mpp_l0))
        rgb_f = _lee(lector, lf, mpp4, xf_l0, yf_l0, n_px, n_px)
        i0f = campo_i0(lector, lf, mpp4, xf_l0, yf_l0, n_px, n_px)
        hf = hematoxilina(od(rgb_f, i0f), vectores[ir_f.nombre])
        # región ampliada de la fija (±m) → su caja en la móvil
        X0, Y0 = xf_l0 * ir_f.mpp_l0 - m_px * mpp4, yf_l0 * ir_f.mpp_l0 - m_px * mpp4
        N = n_px + 2 * m_px
        esq = _aplica(Minv, [(X0, Y0), (X0 + N * mpp4, Y0), (X0, Y0 + N * mpp4),
                             (X0 + N * mpp4, Y0 + N * mpp4)])
        mn, mx = esq.min(0) - 4 * mpp4m, esq.max(0) + 4 * mpp4m
        xm_l0, ym_l0 = int(math.floor(mn[0] / ir_m.mpp_l0)), int(math.floor(mn[1] / ir_m.mpp_l0))
        xm_l0, ym_l0 = max(0, xm_l0), max(0, ym_l0)
        wm = int(math.ceil((mx[0] - xm_l0 * ir_m.mpp_l0) / mpp4m))
        hm = int(math.ceil((mx[1] - ym_l0 * ir_m.mpp_l0) / mpp4m))
        W0, H0 = lm.dimensiones_l0
        wm = max(1, min(wm, int((W0 - xm_l0) * ir_m.mpp_l0 / mpp4m)))
        hm = max(1, min(hm, int((H0 - ym_l0) * ir_m.mpp_l0 / mpp4m)))
        rgb_m = _lee(lector, lm, mpp4m, xm_l0, ym_l0, wm, hm)
        i0m = campo_i0(lector, lm, mpp4m, xm_l0, ym_l0, wm, hm)
        hm_img = hematoxilina(od(rgb_m, i0m), vectores[ir_m.nombre])
        P_out = np.array([[mpp4, 0, X0 + 0.5 * mpp4], [0, mpp4, Y0 + 0.5 * mpp4], [0, 0, 1]])
        P_in = np.array([[mpp4m, 0, xm_l0 * ir_m.mpp_l0 + 0.5 * mpp4m],
                         [0, mpp4m, ym_l0 * ir_m.mpp_l0 + 0.5 * mpp4m], [0, 0, 1]])
        A = np.linalg.inv(P_in) @ Minv @ P_out
        grande = warp(hm_img, AffineTransform(matrix=A), output_shape=(N, N), order=1, cval=0)
        sup = _ncc(grande, hf)
        if sup is None:
            res.append({"x_um": x0, "y_um": y0, "valido": False})
            continue
        r = _pico(sup, m_px, mpp4, p)
        r.update({"x_um": x0, "y_um": y0})
        res.append(r)
    return res


def mapa_densidad(xy_um, x0, y0, w, h, mpp, sigma_um):
    m = np.zeros((h, w))
    c = ((xy_um[:, 0] - x0) / mpp).astype(int)
    r = ((xy_um[:, 1] - y0) / mpp).astype(int)
    ok = (c >= 0) & (c < w) & (r >= 0) & (r < h)
    np.add.at(m, (r[ok], c[ok]), 1.0)
    return ndi.gaussian_filter(m, sigma_um / mpp)


def tre_densidad(cent_f_um, cent_m_ref_um, ventanas, p):
    """(b'): misma rejilla sobre el mapa de densidad de centroides (2 µm/px, σ 8 µm)."""
    if cent_f_um is None or cent_m_ref_um is None or not ventanas:
        return []
    mpp = p["densidad_mpp"]
    L, m_um = p["ventana_um"], p["desp_max_um"]
    n_px, m_px = int(round(L / mpp)), int(round(m_um / mpp))
    xs = np.array([v[0] for v in ventanas])
    ys = np.array([v[1] for v in ventanas])
    X0, Y0 = xs.min() - m_um - 4 * p["densidad_sigma_um"], ys.min() - m_um - 4 * p["densidad_sigma_um"]
    W = int(math.ceil((xs.max() + L + m_um + 4 * p["densidad_sigma_um"] - X0) / mpp))
    H = int(math.ceil((ys.max() + L + m_um + 4 * p["densidad_sigma_um"] - Y0) / mpp))
    df = mapa_densidad(cent_f_um, X0, Y0, W, H, mpp, p["densidad_sigma_um"])
    dm = mapa_densidad(cent_m_ref_um, X0, Y0, W, H, mpp, p["densidad_sigma_um"])
    res = []
    for (x0, y0) in ventanas:
        c0, r0 = int(round((x0 - X0) / mpp)), int(round((y0 - Y0) / mpp))
        plantilla = df[r0:r0 + n_px, c0:c0 + n_px]
        grande = dm[r0 - m_px:r0 + n_px + m_px, c0 - m_px:c0 + n_px + m_px]
        sup = _ncc(grande, plantilla)
        if sup is None:
            res.append({"x_um": x0, "y_um": y0, "valido": False})
            continue
        r = _pico(sup, m_px, mpp, p)
        r.update({"x_um": x0, "y_um": y0})
        res.append(r)
    return res


def _resumen_picos(res, p):
    val = [r["desp_um"] for r in res if r.get("valido")]
    return {"n_ventanas": len(res), "n_validas": len(val),
            "p90_um": float(np.percentile(val, 90)) if val else None,
            "max_um": float(max(val)) if val else None}


def contencion(fc_pol_um, ir_f, ir_m, M):
    """Fracción del FC dentro del campo común (zona escaneada de la fija ∩ la de la móvil
    llevada con M)."""
    from shapely import affinity
    if fc_pol_um is None or fc_pol_um.area == 0:
        return 0.0
    zf = affinity.scale(ir_f.zona_geom, ir_f.mpp_l0, ir_f.mpp_l0, origin=(0, 0))
    zm = affinity.scale(ir_m.zona_geom, ir_m.mpp_l0, ir_m.mpp_l0, origin=(0, 0))
    zm = affinity.affine_transform(zm, [M[0, 0], M[0, 1], M[1, 0], M[1, 1], M[0, 2], M[1, 2]])
    comun = zf.intersection(zm)
    return float(fc_pol_um.intersection(comun).area / fc_pol_um.area)


# ── respaldos: correlación de fase (ODsum y densidad) y VALIS ──────────────────────────────
def refina_correlacion(img_f, img_m_ref, mascara, mpp, p, centro_um):
    """Refinado euclídeo de una imagen YA llevada a la referencia con la global: busca un giro δ
    (rejilla sellada, alrededor del centroide del FC) y la traslación por correlación de fase
    (scikit-image). Devuelve la corrección D (3×3, µm, en la referencia) y su NCC."""
    from skimage.registration import phase_cross_correlation
    from skimage.transform import AffineTransform, warp
    filas, cols = np.nonzero(ndi.binary_dilation(mascara, iterations=int(50 / mpp) + 1))
    if len(filas) < 16:
        return None, None
    r0, r1, c0, c1 = filas.min(), filas.max() + 1, cols.min(), cols.max() + 1
    ref = img_f[r0:r1, c0:c1].astype(float)
    a0, a1, da = p["fase_angulos"]
    mejor = (None, -np.inf)
    cx, cy = centro_um
    for d in np.arange(a0, a1 + 1e-9, da):
        R = _rot_alrededor(d, cx, cy)
        P = np.array([[mpp, 0, (c0 + 0.5) * mpp], [0, mpp, (r0 + 0.5) * mpp], [0, 0, 1]])
        Pg = np.array([[mpp, 0, 0.5 * mpp], [0, mpp, 0.5 * mpp], [0, 0, 1]])
        A = np.linalg.inv(Pg) @ np.linalg.inv(R) @ P
        mov = warp(img_m_ref.astype(float), AffineTransform(matrix=A), output_shape=ref.shape,
                   order=1, cval=0)
        try:
            desp, _err, _f = phase_cross_correlation(ref, mov, upsample_factor=4)
        except Exception:
            continue
        mov2 = ndi.shift(mov, desp, order=1)
        a, b = ref - ref.mean(), mov2 - mov2.mean()
        den = math.sqrt((a * a).sum() * (b * b).sum())
        ncc = float((a * b).sum() / den) if den > 0 else -1
        if ncc > mejor[1]:
            T = np.array([[1, 0, desp[1] * mpp], [0, 1, desp[0] * mpp], [0, 0, 1.0]])
            mejor = (T @ R, ncc)
    return mejor


def _rot_alrededor(grados, cx, cy):
    a = math.radians(grados)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, cx - c * cx + s * cy], [s, c, cy - s * cx - c * cy], [0, 0, 1.0]])


VALIS_PY = os.path.expanduser("~/.polaris-venvs/valis/bin/python")
VALIS_TIMEOUT_S = 6 * 3600
VALIS_MARCA = "VALIS_JSON "          # la línea de resultado (VALIS escribe mucho más en stdout)
VALIS_SCRIPT = r"""
import json, sys
from valis import registration
src, dst, ref = sys.argv[1], sys.argv[2], sys.argv[3]
reg = registration.Valis(src, dst, reference_img_f=ref, align_to_reference=True,
                         non_rigid_registrar_cls=None)
# VALIS 1.2.0: con non_rigid_registrar_cls=None nunca crea `non_rigid_reg_kwargs`, y `cleanup()`
# (al final de register) lo toca: AttributeError, que register() se traga devolviendo None.
if not hasattr(reg, "non_rigid_reg_kwargs"):
    reg.non_rigid_reg_kwargs = {registration.NON_RIGID_REG_CLASS_KEY: None}
rigido = reg.register()[0]
if rigido is None:
    # register() se traga la excepcion (la imprime en stdout) y devuelve (None, None, None)
    sys.stderr.write("VALIS register() failed (traceback above, in stdout)\n")
    sys.exit(3)
ent = lambda v: [int(x) for x in v]          # numpy int64 no es serializable en JSON
out = {}
for nombre, sl in reg.slide_dict.items():
    out[nombre] = {"M": [[float(x) for x in fila] for fila in sl.M],
                   "processed_shape": ent(sl.processed_img_shape_rc),
                   "reg_shape": ent(sl.reg_img_shape_rc), "shape": ent(sl.slide_dimensions_wh[0])}
sys.stdout.write("\n" + "VALIS_JSON " + json.dumps(out) + "\n")
sys.stdout.flush()
"""
_RE_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _cola_saneada(texto, rutas=(), n_lineas=6, max_chars=600):
    """Cola de la salida de un subproceso, apta para un producto: sin códigos ANSI, sin rutas (la
    carpeta temporal pasa a «<tmp>» y HOME a «~»), solo imprimibles, ≤ `n_lineas` y `max_chars`."""
    t = _RE_ANSI.sub("", texto or "")
    for r in sorted({r for r in rutas if r}, key=len, reverse=True):
        t = t.replace(r, "<tmp>")
    t = t.replace(os.path.expanduser("~"), "~")
    lineas = [ln.strip() for ln in t.splitlines() if ln.strip()]
    cola = " | ".join(lineas[-n_lineas:])
    return "".join(c if c.isprintable() else "?" for c in cola)[-max_chars:]


def _ejecutor_valis(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=VALIS_TIMEOUT_S)


def _px_proc_a_um(mpp, esc):
    """Píxel PROCESADO de VALIS (centro en el entero, convenio de scikit-image) → µm de la imagen
    de registro: la imagen se reduce `esc` veces (esquina con esquina), así que el centro del
    píxel procesado p está en mpp·esc·(p + 0,5) µm (con esc = 1, el `px_a_um` de este módulo)."""
    return np.array([[mpp * esc[0], 0, 0.5 * mpp * esc[0]],
                     [0, mpp * esc[1], 0.5 * mpp * esc[1]], [0, 0, 1.0]])


def respaldo_valis(img_f, img_m, mpp, ejecutor=None, tmpdir=None, espejo=False, mpp_m=None):
    """(4) VALIS, último recurso, por subproceso al venv `valis` sobre las imágenes ×8 de
    registro. `mpp`: el de la fija; `mpp_m`: el de la móvil (None = el mismo; el ×8 nativo de
    cada lámina puede diferir en el redondeo de su pirámide). Devuelve (M, info): M (µm,
    móvil→ref) o None; info = {corrio, rc, motivo, espejo, segundos}. `corrio` = VALIS
    devolvió una transformación utilizable; si no, `motivo` dice por qué («VALIS did not run: rc=…; <cola saneada de stderr, o de stdout si stderr está vacío>»,
    o «VALIS returned no usable transform: …»). Nunca lanza: un VALIS que no corre no es un
    resultado del registro (el que llama decide; `registro_par` no gasta la vuelta).

    CONVENIO DE `Slide.M` (VALIS 1.2.0, leído en `valis/warp_tools.py::warp_xy_rigid`, que aplica
    `inv(M)` a los puntos, y comprobado con VALIS real en el humo `registro-valis`): M es el mapa
    INVERSO de scikit-image, del espacio registrado común a los píxeles PROCESADOS de su imagen.
    Por eso móvil→ref = P_f · M_ref · inv(M_móvil) · inv(P_m), con P = `_px_proc_a_um`. (Hasta el
    2-oct-26 se componía al revés —inv(M_ref) · M_móvil—: devolvía la inversa.)

    `espejo` (la quiralidad de la global): VALIS sin `check_for_reflections` solo busca
    rotaciones, así que si la global dice espejo se le da la móvil volteada en x y se compone con
    ese volteo. La puerta TRE juzga el resultado igual que cualquier otro."""
    from PIL import Image
    import json
    import time
    ejecutor = ejecutor or _ejecutor_valis
    mpp_m = mpp if mpp_m is None else float(mpp_m)
    info = {"corrio": False, "rc": None, "motivo": None, "espejo": bool(espejo)}
    t0 = time.time()

    def a_png(img, nombre, voltea=False):
        # un solo temporal float32 (como antes): en ×8 de lámina entera son cientos de MP
        b = np.clip(img, 0, 1).astype(np.float32, copy=False)
        b *= 255
        np.rint(b, out=b)
        u8 = b.astype(np.uint8)
        del b
        Image.fromarray(np.ascontiguousarray(u8[:, ::-1]) if voltea else u8).save(
            os.path.join(src, nombre + ".png"))
    with tempfile.TemporaryDirectory(dir=tmpdir) as d:
        rutas = (d, os.path.realpath(d))
        src = os.path.join(d, "src")
        os.makedirs(src)
        a_png(img_f, "fija")
        a_png(img_m, "movil", voltea=espejo)
        try:
            r = ejecutor([VALIS_PY, "-c", VALIS_SCRIPT, src, os.path.join(d, "dst"), "fija.png"])
        except Exception as e:                                     # noqa: BLE001
            info["motivo"] = "VALIS did not run: %s: %s" % (type(e).__name__,
                                                           _cola_saneada(str(e), rutas))
            info["segundos"] = round(time.time() - t0, 1)
            return None, info
        info["segundos"] = round(time.time() - t0, 1)
        info["rc"] = getattr(r, "returncode", None)
        sal, err = getattr(r, "stdout", "") or "", getattr(r, "stderr", "") or ""
        if info["rc"] != 0:
            info["motivo"] = "VALIS did not run: rc=%s; %s" % (
                info["rc"], _cola_saneada(err, rutas) or _cola_saneada(sal, rutas) or "no output")
            return None, info
        try:
            linea = [ln for ln in sal.splitlines() if ln.startswith(VALIS_MARCA)][-1]
            out = json.loads(linea[len(VALIS_MARCA):])
            Mp = np.asarray(out["movil"]["M"], float)
            Mf = np.asarray(out["fija"]["M"], float)
            esc_m = np.asarray(out["movil"]["shape"], float) / np.asarray(
                out["movil"]["processed_shape"][::-1], float)
            esc_f = np.asarray(out["fija"]["shape"], float) / np.asarray(
                out["fija"]["processed_shape"][::-1], float)
            M = (_px_proc_a_um(mpp, esc_f) @ Mf @ np.linalg.inv(Mp)
                 @ np.linalg.inv(_px_proc_a_um(mpp_m, esc_m)))
            if espejo:
                ancho_um = img_m.shape[1] * mpp_m
                M = M @ np.array([[-1.0, 0, ancho_um], [0, 1.0, 0], [0, 0, 1.0]])
            if M.shape != (3, 3) or not np.all(np.isfinite(M)) or abs(
                    np.linalg.det(M[:2, :2])) < 1e-9:
                raise ValueError("degenerate matrix")
        except Exception as e:                                     # noqa: BLE001
            info["motivo"] = "VALIS returned no usable transform: %s: %s; %s" % (
                type(e).__name__, _cola_saneada(str(e), rutas, 1, 200),
                _cola_saneada(err, rutas) or _cola_saneada(sal, rutas))
            return None, info
    info["corrio"] = True
    return M, info


def a_euclidea(M, centro_movil_um):
    """Proyecta una afín 2D a la euclídea más cercana (Procrustes por SVD: rotación con la
    quiralidad de M, escala 1), conservando la imagen de `centro_movil_um`. Devuelve (M_e,
    escala, anisotropía): escala = √|det|; anisotropía = cociente de valores singulares."""
    M = np.asarray(M, float)
    A = M[:2, :2]
    U, S, Vt = np.linalg.svd(A)
    R = U @ Vt
    c = np.asarray(centro_movil_um, float)
    t = A @ c + M[:2, 2] - R @ c
    Me = np.eye(3)
    Me[:2, :2], Me[:2, 2] = R, t
    return Me, float(math.sqrt(abs(np.linalg.det(A)))), float(S[0] / S[1]) if S[1] > 0 else math.inf


# ── el registro de una pareja ──────────────────────────────────────────────────────────────
_COTEJO_REGISTRO = (
    (("registro", "match", "max_ratio"), "ratio", None),
    (("registro", "match", "cross_check"), "cross_check", None),
    (("registro", "ransac", "min_samples"), "min_samples", None),
    (("registro", "ransac", "residual_threshold_px"), "residual_px", None),
    (("registro", "ransac", "max_trials"), "max_trials", None),
    (("registro", "min_inliers_fc"), "min_inliers", None),
    (("registro", "puerta", "tre_um_max"), "tre_max_um", None),
    (("registro", "puerta", "ventana_b_um"), "ventana_um", None),
    (("registro", "puerta", "paso_b_um"), "paso_um", None),
    (("registro", "puerta", "ventana_dentro_fc_min"), "frac_dentro", None),
    (("registro", "puerta", "pico_cociente_min"), "pico_ratio", None),
    (("registro", "puerta", "desplaz_max_um"), "desp_max_um", None),
    (("registro", "puerta", "min_picos"), "min_picos", None),
    (("registro", "puerta", "pequeno_min_picos"), "fragmento_min_picos", None),
    (("registro", "puerta", "b_prima", "mpp"), "densidad_mpp", None),
    (("registro", "puerta", "b_prima", "sigma_um"), "densidad_sigma_um", None),
)


def _params(sello, fija, movil):
    """(parámetros, {lámina: vectores}, sello, pre_congelación). Con sello, todo sale de él
    (`modulo_b.registro`, cotejado con su `registro`); sin sello, solo parejas de suelo."""
    if fija in LAMINAS_HE:
        raise RegistroError("referencia P-CK19 (o una IHQ), nunca P-HE: la H&E va de móvil "
                            "(registra_he)")
    sello = SELLO.exige(sello, [fija, movil], SELLO.SECCIONES_MODULO_B)
    if sello is None:
        return (dict(PARAMS_PARTIDA), {fija: vectores_de(fija), movil: vectores_de(movil)},
                None, True)
    import laminillas_metricas as MET
    p = dict(sello.modulo_b("registro"))
    faltan = sorted(set(PARAMS_PARTIDA) - set(p))
    if faltan:
        raise SELLO.SelloInvalido("al sello le faltan parámetros de registro: %s" % ", ".join(faltan))
    MET.coteja(sello.d, _COTEJO_REGISTRO, p, "registro")
    return p, {fija: vectores_de(fija, sello), movil: vectores_de(movil, sello)}, sello, False


def evalua_fc(lector, ir_f, ir_m, M, fc, p, vectores, cent_f_um=None, cent_m_l0=None,
              densidad_usada=False, ventanas=None):
    """Puerta de un FC con la transformada M: contención y TRE (b)/(b').

    `pasa`: True / False, o None si el FC se registró con el mapa de densidad y (b) no llega a 15
    picos válidos: entonces (b') no es independiente y la comprobación a ciegas es la puerta
    (`estado` = «pending: blind check as gate», con los recortes que necesita)."""
    ventanas = rejilla_ventanas(fc["mascara"], ir_f.mpp, p) if ventanas is None else ventanas
    b = tre_hematoxilina(lector, ir_f, ir_m, M, ventanas, p, vectores)
    cent_m_ref = None if cent_m_l0 is None else _aplica(M, np.asarray(cent_m_l0) * ir_m.mpp_l0)
    bp = tre_densidad(cent_f_um, cent_m_ref, ventanas, p)
    rb, rbp = _resumen_picos(b, p), _resumen_picos(bp, p)
    cont = contencion(fc["poligono_um"], ir_f, ir_m, M)
    out = {"n_candidatas": len(ventanas), "b": rb, "b_prima": rbp, "contencion": cont,
           "rotulos": [], "declaraciones": []}
    pequeno = len(ventanas) < int(p["min_picos"])
    pendiente = False
    if densidad_usada:
        out["declaraciones"].append(METODOS["densidad"])
    out["contencion_ok"] = bool(cont >= p["contencion_min"])
    if not pequeno:
        if rb["n_validas"] >= p["min_picos"]:
            out["rige"], out["p90_um"] = "b", rb["p90_um"]
        elif not densidad_usada and rbp["n_validas"] >= p["min_picos"]:
            out["rige"], out["p90_um"] = "b_prima", rbp["p90_um"]
        else:
            out["rige"], out["p90_um"] = None, None
            pendiente = densidad_usada
        out["pequeno"] = False
        tre_ok = out["p90_um"] is not None and out["p90_um"] < p["tre_max_um"]
    else:
        out["pequeno"] = True
        usa = rb if rb["n_validas"] >= p["fragmento_min_picos"] else (
            rbp if not densidad_usada else rb)
        n = usa["n_validas"]
        out["rige"] = ("b" if usa is rb else "b_prima") if n else None
        out["p90_um"] = usa["p90_um"]
        out["max_um"] = usa["max_um"]
        tre_ok = n >= p["fragmento_min_picos"] and usa["max_um"] < p["tre_max_um"]
        out["n_verificadas"] = n
    out["tre_ok"] = bool(tre_ok)
    if pendiente and out["contencion_ok"]:
        out["pasa"] = None
        out["estado"] = PENDIENTE_CIEGAS
        out["recortes_comprobacion_ciegas"] = [
            {"x_um": float(x), "y_um": float(y), "lado_um": float(p["ventana_um"])}
            for x, y in ventanas]
        return out
    out["pasa"] = bool(tre_ok and out["contencion_ok"])
    if out["pequeno"]:
        if n < p["fragmento_min_picos"]:
            out["rotulos"].append(ROTULOS["pequeno_no_verificado"])
        elif out["pasa"]:
            out["rotulos"].append(ROTULOS["pequeno_verificado"].format(n=n))
        else:                                     # se comprobó con ≥5 picos y no pasa
            out["rotulos"].append(ROTULOS["no_verificado"])
    elif not out["pasa"]:
        out["rotulos"].append(ROTULOS["no_verificado"])
    return out


def registra_par(fija, movil, sello, lector=None, init=None, centroides=None, fcs=None,
                 log=None, ejecutor_valis=None, imagenes=None, intentar_valis=True,
                 alternativas=False):
    """Registra `movil` contra `fija` (nunca P-HE de fija). `centroides`: {nombre: N×2 px L0}
    (InstanSeg). `fcs`: fragmentos de consenso ya calculados en la rejilla ×8 de la fija (si no,
    los de la pareja: intersección de las dos máscaras). `init`: eslabón 1 ({angulo_grados,
    espejo} o {matriz_um}). `alternativas`: por FC, inliers de la otra quiralidad y de otro ángulo
    (puerta (iii) de la H&E). Devuelve `ResultadoRegistro`."""
    log = log or (lambda m: None)
    p, vectores, sello_v, pre = _params(sello, fija, movil)
    lector = _lector(lector)
    imagenes = imagenes if imagenes is not None else {}
    for n in (fija, movil):
        if n not in imagenes:
            imagenes[n] = ImagenRegistro(lector, n, p, vectores[n])
    ir_f, ir_m = imagenes[fija], imagenes[movil]
    if abs(ir_f.mpp_l0 - ir_m.mpp_l0) > 0.005 * ir_f.mpp_l0:
        raise RegistroError("mpp L0 distinto entre láminas: el euclídeo con escala 1 no vale")
    g, el, init = global_con_init(ir_f, ir_m, p, init)
    res = ResultadoRegistro(fija, movil, p, sello_v, pre)
    res.global_ = {"matriz_um": el["matriz_um"], "espejo": el["espejo"],
                   "n_inliers": el["n_inliers"], "n_putativas": el["n_putativas"],
                   "alternativa_inliers": g["alternativa"]["n_inliers"],
                   "margen_inliers": g["margen_inliers"], "sift_aceptada": g["aceptada"],
                   "sift_motivo": g["motivo"],
                   "tre_a": tre_particion(el["_src"], el["_dst"], p, ir_f.mpp)}
    res.global_["origen"] = el.get("origen", "sift" if el["matriz_um"] is not None else None)
    if init is not None:
        res.global_["init"] = {"origen": init.get("origen", "matriz"),
                               "iou_mascaras": init.get("iou_mascaras"),
                               "espejo": init["espejo"],
                               "filtrado": bool(el.get("filtrado_por_init"))}
    if el["matriz_um"] is not None:
        res.global_["angulo_grados"] = angulo(el["matriz_um"])
    log("%s→%s global: %d inliers (alternativa %d)%s" % (
        movil, fija, el["n_inliers"], g["alternativa"]["n_inliers"],
        "" if g["aceptada"] else " · SIFT no aceptada: %s" % g["motivo"]))
    M_g = el["matriz_um"]
    if M_g is None:
        res.rotulos.append(ROTULOS["ae1ae3"] if movil == "P-AE1AE3" else ROTULOS["no_verificado"])
        return res
    if fcs is None:
        m_ref = warp_a_referencia(ir_m.tejido, ir_m, ir_f, M_g)
        fcs = fragmentos_consenso([m_ref, ir_f.tejido], ir_f.mpp, p, min_votos=2)
    cent_f = None
    cent_m = None
    if centroides:
        if fija in centroides:
            cent_f = np.asarray(centroides[fija], float) * ir_f.mpp_l0
        cent_m = centroides.get(movil)
    rm = el["_rasgos_m"]
    rf = g["rasgos_f"]
    valis = None
    if intentar_valis:
        # VALIS registra las imágenes ×8 ENTERAS (no depende del FC): UNA corrida por pareja,
        # perezosa (solo si algún FC llega al paso 4) y reutilizada por todos los FC.
        cache = []

        def valis():
            if not cache:
                log("%s→%s VALIS (una vez por pareja, imágenes ×8 enteras)" % (movil, fija))
                cache.append(respaldo_valis(ir_f.img, ir_m.img, ir_f.mpp, ejecutor_valis,
                                            espejo=el["espejo"], mpp_m=ir_m.mpp))
                res.valis = dict(cache[0][1])
                if cache[0][0] is not None:     # la matriz de VALIS queda en el producto
                    res.valis["matriz_um"] = np.asarray(cache[0][0], float)
                log("%s→%s VALIS: %s" % (movil, fija, "ran" if res.valis["corrio"]
                                         else res.valis["motivo"]))
            return cache[0]
    for fc in fcs:
        f = _registra_fc(lector, ir_f, ir_m, M_g, el["espejo"], fc, rf, rm, p, vectores, cent_f,
                         cent_m, log, valis)
        if alternativas:
            f["alternativas"] = alternativas_fc(ir_f, ir_m, fc, rf, p, el["espejo"], M_g)
        res.fragmentos.append(f)
        res.fcs.append(fc)
    return res


def _hough_angulos(src, dst, p, mpp, excluir=None):
    """Máximo de correspondencias compatibles con una rotación de la rejilla de ángulos (más una
    traslación): para cada ángulo, el mayor grupo de traslaciones dst − R·src a < el residual."""
    from scipy.spatial import cKDTree
    if len(src) < 2:
        return 0, None
    r = float(p["residual_px"]) * mpp
    mejor, ang = 0, None
    for a in np.arange(-180.0, 180.0, float(p["he_paso_angulos"])):
        if excluir is not None and abs((a - excluir + 180) % 360 - 180) <= p["he_angulo_excluido"]:
            continue
        t = math.radians(a)
        R = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
        tr = dst - src @ R.T
        cnt = max(len(x) for x in cKDTree(tr).query_ball_point(tr, r))
        if cnt > mejor:
            mejor, ang = int(cnt), float(a)
    return mejor, ang


def alternativas_fc(ir_f, ir_m, fc, rf, p, espejo, M_g):
    """Puerta (iii), por FC: inliers del modelo ELEGIDO (SIFT restringido al FC en las dos
    láminas, en la quiralidad de la global) y de la mejor ALTERNATIVA: la otra quiralidad (RANSAC
    y rejilla de ángulos) y otro ángulo (rejilla a > `he_angulo_excluido` del elegido). Las
    alternativas usan las rasgos fijas del FC contra TODA la móvil (más generoso con ellas)."""
    dil = ndi.binary_dilation(fc["mascara"], iterations=int(p["fc_margen_um"] / ir_f.mpp) + 1)

    def dentro(xy_um):
        c = np.clip((xy_um[:, 0] / ir_f.mpp).astype(int), 0, dil.shape[1] - 1)
        r = np.clip((xy_um[:, 1] / ir_f.mpp).astype(int), 0, dil.shape[0] - 1)
        return dil[r, c]
    sel_f = dentro(rf.xy)
    espejo = bool(espejo)
    rm_e = rasgos_sift(ir_m, p, espejo)
    xy_real = rm_e.xy * (np.array([-1.0, 1.0]) if espejo else 1.0)
    src, dst = putativas(rf, rm_e, p, sel_f, dentro(_aplica(M_g, xy_real)))
    M_e, inl = ajusta_ransac(src, dst, p, ir_f.mpp, semilla_extra=1)
    ang = angulo(M_e) if M_e is not None else angulo(_con_espejo(M_g, espejo))
    out = {"elegida": {"n_inliers": int(inl.sum()), "angulo_grados": ang, "espejo": espejo}}
    src, dst = putativas(rf, rm_e, p, sel_f, None)
    n, a = _hough_angulos(src, dst, p, ir_f.mpp, excluir=ang)
    out["otro_angulo"] = {"n_inliers": n, "angulo_grados": a}
    rm_o = rasgos_sift(ir_m, p, not espejo)
    src, dst = putativas(rf, rm_o, p, sel_f, None)
    _M, inl_o = ajusta_ransac(src, dst, p, ir_f.mpp, semilla_extra=3)
    n_h, a_h = _hough_angulos(src, dst, p, ir_f.mpp)
    out["espejo"] = {"n_inliers": max(int(inl_o.sum()), n_h), "angulo_grados": a_h}
    out["mejor"] = max(out["otro_angulo"]["n_inliers"], out["espejo"]["n_inliers"])
    return out


def sift_en_fc(ir_f, rf, rm, espejo, M_g, fc, p):
    """Paso (1) de un FC: putativas entre los rasgos de la fija dentro del FC (con margen
    `fc_margen_um`) y los de la móvil que la global `M_g` lleva dentro, y RANSAC sellado.
    `rm` en la quiralidad `espejo` (coordenadas reflejadas si lo es). Devuelve (src, dst, M_e,
    inliers); M_e en coordenadas reflejadas (`_con_espejo` la devuelve a las reales)."""
    dil = ndi.binary_dilation(fc["mascara"], iterations=int(p["fc_margen_um"] / ir_f.mpp) + 1)

    def dentro(xy_um):
        c = np.clip((xy_um[:, 0] / ir_f.mpp).astype(int), 0, dil.shape[1] - 1)
        r = np.clip((xy_um[:, 1] / ir_f.mpp).astype(int), 0, dil.shape[0] - 1)
        return dil[r, c]
    xy_m_real = rm.xy * (np.array([-1.0, 1.0]) if espejo else 1.0)
    sel_f = dentro(rf.xy)
    sel_m = dentro(_aplica(M_g, xy_m_real))
    src, dst = putativas(rf, rm, p, sel_f, sel_m)
    M_e, inl = ajusta_ransac(src, dst, p, ir_f.mpp, semilla_extra=1)
    return src, dst, M_e, inl


def fase_en_fc(ir_f, ir_m, M_g, fc, p, centro_um):
    """Paso (2) de un FC: la ODsum de la móvil llevada con M_g y `refina_correlacion` sobre el FC.
    Devuelve (D, ncc): la corrección en la referencia (D @ M_g es la transformada)."""
    img_m_ref = warp_a_referencia(ir_m.img, ir_m, ir_f, M_g, orden=1)
    D, ncc = refina_correlacion(ir_f.img, img_m_ref, fc["mascara"], ir_f.mpp, p, centro_um)
    del img_m_ref
    return D, ncc


def densidad_en_fc(ir_f, ir_m, M_g, fc, cent_f_um, cent_m_l0, p, centro_um):
    """Paso (3) de un FC: mapas de densidad de centroides nucleares (`densidad_mpp`, σ
    `densidad_sigma_um`), la móvil llevada con M_g, y `refina_correlacion` sobre el FC.
    Devuelve (D, ncc)."""
    mpp_d = p["densidad_mpp"]
    H, W = int(ir_f.alto_um / mpp_d), int(ir_f.ancho_um / mpp_d)
    df = mapa_densidad(cent_f_um, 0, 0, W, H, mpp_d, p["densidad_sigma_um"])
    dm = mapa_densidad(_aplica(M_g, np.asarray(cent_m_l0) * ir_m.mpp_l0), 0, 0, W, H, mpp_d,
                       p["densidad_sigma_um"])
    mask_d = ndi.zoom(fc["mascara"].astype(float), ir_f.mpp / mpp_d, order=0)[:H, :W] > 0.5
    return refina_correlacion(df / (df.max() or 1), dm / (dm.max() or 1), mask_d, mpp_d, p,
                              centro_um)


def _registra_fc(lector, ir_f, ir_m, M_g, espejo, fc, rf, rm, p, vectores, cent_f, cent_m, log,
                 valis=None):
    """Un FC: SIFT restringido y, con <30 inliers, los respaldos. `valis`: None (sin paso 4) o
    la función de la pareja que devuelve (M, info) de VALIS, calculada una vez."""
    ventanas = rejilla_ventanas(fc["mascara"], ir_f.mpp, p)
    intentos = []
    base = {"id": fc["id"], "area_um2": fc["area_um2"], "rotulo_fc": fc.get("rotulo"),
            "declaraciones": []}
    if len(ventanas) < int(p["min_picos"]):            # fragmento pequeño: hereda la global
        ev = evalua_fc(lector, ir_f, ir_m, M_g, fc, p, vectores, cent_f, cent_m,
                       ventanas=ventanas)
        base.update({"metodo": "global (small fragment)", "matriz_um": M_g, "evaluacion": ev,
                     "intentos": [{"metodo": "global", "pasa": ev["pasa"]}], "pasa": ev["pasa"]})
        return base
    # (1) SIFT restringido al FC (con margen), móvil llevada con la global
    src, dst, M_e, inl = sift_en_fc(ir_f, rf, rm, espejo, M_g, fc, p)
    n_sift = int(inl.sum())
    base["sift_inliers"] = n_sift
    base["angulo_sift"] = None if M_e is None else angulo(M_e)
    tre_a = tre_particion(src, dst, p, ir_f.mpp)
    cx, cy = fc["poligono_um"].centroid.coords[0] if fc["poligono_um"] is not None else (0, 0)

    def prueba(nombre, M, n_inl, densidad):
        ev = evalua_fc(lector, ir_f, ir_m, M, fc, p, vectores, cent_f, cent_m,
                       densidad_usada=densidad, ventanas=ventanas)
        intentos.append({"metodo": nombre, "n_inliers": n_inl, "pasa": ev["pasa"],
                         "p90_um": ev.get("p90_um")})
        log("  %s %s: pasa=%s p90=%s" % (fc["id"], nombre, ev["pasa"], ev.get("p90_um")))
        return ev

    def termina(nombre, M, ev, pasa):
        base.update({"metodo": nombre, "matriz_um": M, "evaluacion": ev, "intentos": intentos,
                     "pasa": pasa, "tre_a": tre_a})
        if pasa is not None and M is not None:
            base["angulo_grados"] = angulo(M)
        return base
    if M_e is not None and n_sift >= p["min_inliers"]:
        # plan: los respaldos solo entran con <30 inliers; el modelo SIFT se juzga y ya
        M = _con_espejo(M_e, espejo)
        ev = prueba("sift", M, n_sift, False)
        if not ev["pasa"] and ROTULOS["no_verificado"] not in ev["rotulos"]:
            ev["rotulos"].append(ROTULOS["no_verificado"])
        return termina("sift", M, ev, bool(ev["pasa"]))
    intentos.append({"metodo": "sift", "n_inliers": n_sift, "pasa": False,
                     "motivo": "<%d inliers" % p["min_inliers"]})
    base["declaraciones"].append(METODOS["eleccion"])
    pendiente = None
    D, ncc = fase_en_fc(ir_f, ir_m, M_g, fc, p, (cx, cy))               # (2) fase sobre ODsum
    if D is not None:
        ev = prueba("phase correlation (ODsum)", D @ M_g, None, False)
        if ev["pasa"]:
            return termina("phase correlation (ODsum)", D @ M_g, ev, True)
    if cent_f is not None and cent_m is not None:                     # (3) mapa de densidad
        D, ncc = densidad_en_fc(ir_f, ir_m, M_g, fc, cent_f, cent_m, p, (cx, cy))
        if D is not None:
            ev = prueba("nuclear density map", D @ M_g, None, True)
            base["declaraciones"].append(METODOS["densidad"])
            if ev["pasa"]:
                return termina("nuclear density map", D @ M_g, ev, True)
            if ev["pasa"] is None:
                pendiente = ("nuclear density map", D @ M_g, ev)
    if valis is not None:                                            # (4) VALIS
        Mv, info = valis()
        if Mv is None:
            intentos.append({"metodo": "VALIS", "pasa": False, "corrio": False,
                             "motivo": info["motivo"]})
        else:
            c_m = _aplica(np.linalg.inv(Mv), [(cx, cy)])[0]
            Me, escala, aniso = a_euclidea(Mv, c_m)
            if abs(escala - 1) > p["valis_escala_tol"] or abs(aniso - 1) > p["valis_escala_tol"]:
                intentos.append({"metodo": "VALIS", "pasa": False, "corrio": True,
                                 "escala": escala, "anisotropia": aniso,
                                 "motivo": "not Euclidean: |scale - 1| > %g" % p["valis_escala_tol"]})
            else:
                base["declaraciones"].append(METODOS["valis"])
                ev = prueba("VALIS", Me, None, False)
                intentos[-1].update({"corrio": True, "escala": escala, "anisotropia": aniso})
                if ev["pasa"]:
                    return termina("VALIS", Me, ev, True)
    if pendiente is not None:
        nombre, M, ev = pendiente
        return termina(nombre, M, ev, None)
    ev = evalua_fc(lector, ir_f, ir_m, M_g, fc, p, vectores, cent_f, cent_m, ventanas=ventanas)
    if ROTULOS["no_verificado"] not in ev["rotulos"]:
        ev["rotulos"].append(ROTULOS["no_verificado"])
    ev["pasa"] = False
    return termina(None, M_g, ev, False)


class ResultadoRegistro:
    def __init__(self, fija, movil, p, sello, pre):
        self.fija, self.movil, self.p = fija, movil, p
        self.sello = sello
        self.pre_congelacion = pre
        self.global_ = {}
        self.fragmentos = []
        self.fcs = []
        self.valis = None           # info de la corrida de VALIS de la pareja (None: no se lanzó)
        self.rotulos = [ROTULOS["pre_congelacion"]] if pre else []

    def matriz(self, fc_id=None):
        if fc_id is None:
            return self.global_.get("matriz_um")
        for f in self.fragmentos:
            if f["id"] == fc_id:
                return f["matriz_um"]
        raise KeyError(fc_id)

    def fragmento(self, fc_id):
        for f in self.fragmentos:
            if f["id"] == fc_id:
                return f
        raise KeyError(fc_id)

    def matrices_verificadas(self):
        """{fc_id: matriz} de los FC que pasan (para llevar regiones a px L0, GeoJSON)."""
        return {f["id"]: np.asarray(f["matriz_um"]) for f in self.fragmentos if f["pasa"]}

    def fraccion_area_verificada(self):
        tot = sum(f["area_um2"] for f in self.fragmentos)
        ok = sum(f["area_um2"] for f in self.fragmentos if f["pasa"])
        return ok / tot if tot else 0.0

    def pendientes(self):
        """FC registrados con el mapa de densidad cuya puerta es la comprobación a ciegas."""
        return [f["id"] for f in self.fragmentos if f["pasa"] is None]

    def resuelve_comprobacion_ciegas(self, fc_id, discrepancia_um):
        """Resultado de la comprobación a ciegas (dos subagentes, fuera de este módulo) para un FC
        pendiente: ≤50 µm del primario → pasa; si no, «registration check inconclusive» y fuera."""
        f = self.fragmento(fc_id)
        if f["pasa"] is not None:
            raise RegistroError("%s no está pendiente de la comprobación a ciegas" % fc_id)
        ev = f["evaluacion"]
        ev["comprobacion_ciegas_um"] = float(discrepancia_um)
        if float(discrepancia_um) <= float(self.p["tre_max_um"]):
            f["pasa"] = ev["pasa"] = True
        else:
            f["pasa"] = ev["pasa"] = False
            for r in (ROTULOS["comprobacion_inconclusa"], ROTULOS["no_verificado"]):
                if r not in ev["rotulos"]:
                    ev["rotulos"].append(r)
        ev["estado"] = "blind check as gate: %s" % ("passed" if f["pasa"] else "failed")
        return f

    def nucleos_a_referencia(self, xy_l0, mpp_l0):
        """Lleva núcleos (px L0 de la móvil) a la referencia (µm) con la transformada de su FC.
        Devuelve (xy_um_ref, id_fc) con id_fc = None si no cae en un FC verificado."""
        xy = np.asarray(xy_l0, float) * mpp_l0
        Mg = self.global_["matriz_um"]
        g = _aplica(Mg, xy)
        ids = np.array([None] * len(xy), dtype=object)
        out = g.copy()
        for f, fc in zip(self.fragmentos, self.fcs):
            m, mpp = fc["mascara"], fc["mpp"]
            c = np.clip((g[:, 0] / mpp).astype(int), 0, m.shape[1] - 1)
            r = np.clip((g[:, 1] / mpp).astype(int), 0, m.shape[0] - 1)
            dentro = m[r, c]
            if f["pasa"]:
                out[dentro] = _aplica(np.asarray(f["matriz_um"]), xy[dentro])
                ids[dentro] = f["id"]
        return out, ids

    def resumen(self):
        frs = []
        for f, fc in zip(self.fragmentos, self.fcs):
            d = {k: v for k, v in f.items()}
            d["poligono_um"] = (list(fc["poligono_um"].exterior.coords)
                                if fc["poligono_um"] is not None else None)
            frs.append(d)
        return _limpia({"fija": self.fija, "movil": self.movil, "global": self.global_,
                        "fragmentos": frs, "rotulos": self.rotulos,
                        "fraccion_area_verificada": self.fraccion_area_verificada(),
                        "pendientes_comprobacion_ciegas": self.pendientes(),
                        "valis": self.valis,
                        "sello": self.sello.cita() if self.sello else None,
                        "parametros": self.p,
                        "metodos": {"parametros_inferidos": {k: self.p.get(k) for k in INFERIDOS},
                                    "declaraciones": [METODOS["sift_upsampling"],
                                                      METODOS["mascara"]]}})


def _limpia(d):
    if isinstance(d, dict):
        return {k: _limpia(v) for k, v in d.items() if not str(k).startswith("_")}
    if isinstance(d, (list, tuple)):
        return [_limpia(v) for v in d]
    if isinstance(d, np.ndarray):
        return d.tolist()
    if isinstance(d, (np.floating, np.integer, np.bool_)):
        return d.item()
    return d


# ── triángulo de cierre y orden de los cortes ──────────────────────────────────────────────
def cierre(M_ab, M_bc, M_ac, punto_um, p):
    """Compara A→B→C con A→C directo en `punto_um` (de A): ángulo y distancia."""
    M_abc = np.asarray(M_bc) @ np.asarray(M_ab)
    da = (angulo(M_abc) - angulo(np.asarray(M_ac)) + 180) % 360 - 180
    dist = float(np.linalg.norm(_aplica(M_abc, [punto_um]) - _aplica(np.asarray(M_ac), [punto_um])))
    misma_quiralidad = (np.linalg.det(M_abc[:2, :2]) > 0) == (np.linalg.det(np.asarray(M_ac)[:2, :2]) > 0)
    ok = bool(misma_quiralidad and abs(da) <= p["cierre_grados"] and dist <= p["cierre_um"])
    return {"delta_grados": float(da), "delta_um": dist, "pasa": ok,
            "misma_quiralidad": bool(misma_quiralidad)}


def fc_en(fc, ir_ref, ir_dest, M_dest_a_ref):
    """Un FC (rejilla ×8 de la referencia) llevado a la rejilla ×8 de otra lámina con la inversa
    de su transformada (dest → ref)."""
    M = np.linalg.inv(np.asarray(M_dest_a_ref, float))           # ref → dest
    m = warp_a_referencia(fc["mascara"], ir_ref, ir_dest, M)
    return {"id": fc["id"], "mascara": m, "mpp": float(ir_dest.mpp),
            "area_um2": float(m.sum() * ir_dest.mpp ** 2),
            "poligono_um": mascara_a_poligono(m, ir_dest.mpp) if m.any() else None,
            "rotulo": fc.get("rotulo")}


def cierre_por_fc(res_ab, res_cb, res_ac, fcs, p):
    """Triángulo A→B→C frente a A→C por FC, con las transformadas REFINADAS de ese FC y en su
    centroide. `res_ab`: A→B (B = referencia, FC en su rejilla); `res_cb`: C→B; `res_ac`: A→C con
    los FC llevados a la rejilla de C (mismos id). Solo FC verificados en los tres."""
    out = []
    for fc in fcs:
        fid = fc["id"]
        try:
            fa, fc_b, fac = res_ab.fragmento(fid), res_cb.fragmento(fid), res_ac.fragmento(fid)
        except KeyError:
            out.append({"fc": fid, "pasa": False, "motivo": "fragment missing in a pair"})
            continue
        if not (fa["pasa"] and fc_b["pasa"] and fac["pasa"]) or fc["poligono_um"] is None:
            out.append({"fc": fid, "pasa": False, "motivo": "fragment not verified in a pair"})
            continue
        M_ab = np.asarray(fa["matriz_um"])
        M_bc = np.linalg.inv(np.asarray(fc_b["matriz_um"]))
        M_ac = np.asarray(fac["matriz_um"])
        punto = _aplica(np.linalg.inv(M_ab), [fc["poligono_um"].centroid.coords[0]])[0]
        out.append(dict(cierre(M_ab, M_bc, M_ac, punto, p), fc=fid))
    return out


def similitud_densidad(cent_ref_um, mascaras, mpp_mascara, p, verificados=None):
    """Correlación del mapa de densidad nuclear a 8 µm/px entre láminas ya en la referencia.
    `cent_ref_um`: {nombre: N×2 µm}. `mascaras`: una máscara (ndarray, rejilla de mpp_mascara) o
    la lista de FC ({id, mascara}). `verificados`: {nombre: ids de FC verificados} (None = todos):
    cada par se compara SOLO en los FC verificados en las dos. Par sin FC común o sin varianza:
    NaN."""
    mpp = p["orden_mpp"]
    if isinstance(mascaras, np.ndarray):
        fcl = [{"id": "_todo", "mascara": mascaras}]
        verificados = None
    else:
        fcl = list(mascaras)
    forma = fcl[0]["mascara"].shape
    H, W = int(forma[0] * mpp_mascara / mpp), int(forma[1] * mpp_mascara / mpp)
    sub = {f["id"]: ndi.zoom(f["mascara"].astype(float), mpp_mascara / mpp, order=0)[:H, :W] > 0.5
           for f in fcl}
    nombres = sorted(cent_ref_um)
    mapas = {}
    for n in nombres:
        d = np.zeros((H, W))
        xy = np.asarray(cent_ref_um[n]).reshape(-1, 2)
        c, r = (xy[:, 0] / mpp).astype(int), (xy[:, 1] / mpp).astype(int)
        ok = (c >= 0) & (c < W) & (r >= 0) & (r < H)
        np.add.at(d, (r[ok], c[ok]), 1.0)
        mapas[n] = ndi.gaussian_filter(d, 1.0)

    def ids_de(n):
        todos = set(sub)
        return todos if verificados is None or n not in verificados else set(verificados[n]) & todos
    S = np.eye(len(nombres))
    for i in range(len(nombres)):
        for j in range(i + 1, len(nombres)):
            comunes = ids_de(nombres[i]) & ids_de(nombres[j])
            if not comunes:
                S[i, j] = S[j, i] = np.nan
                continue
            m = np.any([sub[k] for k in comunes], axis=0)
            a, b = mapas[nombres[i]][m], mapas[nombres[j]][m]
            if a.std() == 0 or b.std() == 0:
                S[i, j] = S[j, i] = np.nan
                continue
            S[i, j] = S[j, i] = float(np.corrcoef(a, b)[0, 1])
    return nombres, S


def orden_cortes(nombres, S, referencia=REFERENCIA):
    """Seriación: el camino hamiltoniano que maximiza la similitud entre vecinos (Held-Karp,
    exacto hasta ~15 láminas). Sentido sin determinar. Las láminas sin ninguna similitud finita
    (sin núcleos en FC verificados) quedan fuera y se declaran; un par sin FC común vale −1 (no
    vecinos). Devuelve orden, distancias a la referencia y entre todos (compañeros del mapa) y el
    rótulo del plan."""
    S = np.asarray(S, float)
    nombres = list(nombres)
    fuera = [nm for i, nm in enumerate(nombres)
             if not np.any(np.isfinite(np.delete(S[i], i)))] if len(nombres) > 1 else []
    idx = [i for i, nm in enumerate(nombres) if nm not in fuera]
    nombres_in = [nombres[i] for i in idx]
    S = S[np.ix_(idx, idx)]
    nan_pares = int(np.sum(~np.isfinite(S)) // 2)
    S = np.where(np.isfinite(S), S, -1.0)
    n = len(nombres_in)
    if n > 15:
        raise RegistroError("seriación exacta hasta 15 láminas")
    INF = -1e18
    dp = np.full((1 << n, n), INF)
    padre = np.full((1 << n, n), -1, int)
    for i in range(n):
        dp[1 << i, i] = 0.0
    for mask in range(1 << n):
        for j in range(n):
            if dp[mask, j] == INF or not (mask >> j) & 1:
                continue
            for k in range(n):
                if (mask >> k) & 1:
                    continue
                nm = mask | (1 << k)
                v = dp[mask, j] + S[j, k]
                if v > dp[nm, k]:
                    dp[nm, k], padre[nm, k] = v, j
    full = (1 << n) - 1
    j = int(np.argmax(dp[full])) if n else -1
    camino, mask = [], full
    while j != -1:
        camino.append(j)
        pj = padre[mask, j]
        mask ^= 1 << j
        j = pj
    orden = [nombres_in[i] for i in camino[::-1]]
    if sorted(orden) != sorted(nombres_in):
        raise RegistroError("la seriación no cubre todas las láminas")
    pos = {nm: i for i, nm in enumerate(orden)}
    dist = {nm: (abs(pos[nm] - pos[referencia]) if referencia in pos else None) for nm in orden}
    entre = {a: {b: abs(pos[a] - pos[b]) for b in orden if b != a} for a in orden}
    out = {"orden": orden, "distancia_a_referencia": dist, "distancias_entre": entre,
           "rotulo": ROTULOS["orden"], "no_ordenadas": fuera, "declaraciones": []}
    if fuera:
        out["declaraciones"].append("not ordered: no verified nuclei (%s)" % ", ".join(fuera))
    if nan_pares:
        out["declaraciones"].append("%d pairs without common verified fragments: similarity "
                                    "set to -1 (not adjacent)" % nan_pares)
    return out


def fcs_serie(tejido_ref, mascaras, n_total, mpp, p, sin_global=()):
    """FC de F3: píxel en ≥6 de las 11 máscaras (`fc_min_votos` de `fc_n_mascaras`), contando
    como 0 la de una lámina sin global. Devuelve (fcs, declaraciones)."""
    if n_total != int(p["fc_n_mascaras"]):
        raise RegistroError("FC: el plan fija %d de %d máscaras; esta serie tiene %d"
                            % (p["fc_min_votos"], p["fc_n_mascaras"], n_total))
    lista = list(mascaras.values()) if isinstance(mascaras, dict) else list(mascaras)
    fcs = fragmentos_consenso([tejido_ref] + lista, mpp, p, min_votos=int(p["fc_min_votos"]))
    decl = []
    if len(lista) + 1 < n_total:
        decl.append("consensus votes: %d of %d masks available (no global: %s, counted as 0)"
                    % (len(lista) + 1, n_total, ", ".join(sin_global) or "?"))
    return fcs, decl


def cierres_serie(triangulos, pares, fcs, imagenes, referencia, sello, lector, centroides, log,
                  p):
    """Triángulos A→B(=referencia)→C frente a A→C, POR FC (`cierre_por_fc`). Una pareja sin
    global no rompe: «sin global»."""
    cierres = []
    for a, b, c in triangulos:
        if not all(x in pares or x == referencia for x in (a, b, c)) or b != referencia:
            continue
        if pares[a].matriz() is None or pares[c].matriz() is None:
            cierres.append({"triangulo": [a, b, c], "pasa": False, "motivo": "sin global",
                            "por_fc": []})
            continue
        ir_ref, ir_c = imagenes[referencia], imagenes[c]
        fcs_c = []
        for fc in fcs:
            try:
                f_c = pares[c].fragmento(fc["id"])
            except KeyError:
                continue
            if f_c["pasa"]:
                fcs_c.append(fc_en(fc, ir_ref, ir_c, f_c["matriz_um"]))
        # el lado directo A→C se registra SIN inicializar desde A→B→C: el cierre debe ser
        # independiente (solo la rejilla de los FC viene de C→B)
        directo = registra_par(c, a, sello, lector=lector, centroides=centroides, fcs=fcs_c,
                               imagenes=imagenes, intentar_valis=False, log=log)
        por_fc = cierre_por_fc(pares[a], pares[c], directo, fcs, p)
        evaluados = [x for x in por_fc if "delta_um" in x]
        cierres.append({"triangulo": [a, b, c], "por_fc": por_fc,
                        "pasa": bool(evaluados) and all(x["pasa"] for x in evaluados),
                        "n_fc_evaluados": len(evaluados)})
    return cierres


def registra_serie(lector, sello, moviles, referencia=REFERENCIA, centroides=None, piloto=True,
                   triangulos=(("P-KI67", "P-CK19", "P-HER2NEG"),), log=None, ejecutor_valis=None,
                   intentar_valis=True, imagenes=None, inits=None):
    """Puerta del piloto (ii)/(ii-bis), o F3 con las 11: global de cada móvil contra P-CK19
    (inicializada por el eslabón 1 si `inits` = {nombre: {angulo_grados, espejo} | {matriz_um}}),
    FC (piloto: KI67∩CK19; serie: ≥6 de 11, la que no tiene global cuenta como 0), refinado y
    puerta por FC en cada pareja, fracción de área verificada de KI67 (≥50 % para extender),
    triángulos de cierre por FC y orden de los cortes. P-HE no entra aquí (`registra_he`)."""
    log = log or (lambda m: None)
    inits = dict(inits or {})
    if referencia in LAMINAS_HE or any(n in LAMINAS_HE for n in moviles):
        raise RegistroError("P-HE no entra en la serie IHQ: referencia P-CK19 y (iii) aparte "
                            "(registra_he)")
    lector = _lector(lector)
    imagenes = imagenes if imagenes is not None else {}
    p, _, sello_v, _ = _params(sello, referencia, moviles[0])
    for n in [referencia] + list(moviles):
        SELLO.exige(sello, [n], SELLO.SECCIONES_MODULO_B)
        if n not in imagenes:
            imagenes[n] = ImagenRegistro(lector, n, p, vectores_de(n, sello_v))
    ir_ref = imagenes[referencia]
    globales, origen_global = {}, {}
    for n in moviles:
        g, el, _ini = global_con_init(ir_ref, imagenes[n], p, inits.get(n))
        globales[n] = el["matriz_um"]
        origen_global[n] = el.get("origen", "sift" if el["matriz_um"] is not None else None)
    mascaras = {n: warp_a_referencia(imagenes[n].tejido, imagenes[n], ir_ref, M)
                for n, M in globales.items() if M is not None}
    sin_global = sorted(n for n, M in globales.items() if M is None)
    out = {"referencia": referencia, "sin_global": sin_global, "origen_global": origen_global,
           "declaraciones": []}
    if piloto:
        if "P-KI67" not in mascaras:
            out.update({"fcs": [], "pares": {}, "resultados": {}, "cierres": [],
                        "ii": {"fraccion_area_verificada_ki67": 0.0, "extiende": False,
                               "nota": ("<50 %: unregistered ladder only; (a), (a-bis) and the "
                                        "CK19 denominator drop; registration back to the "
                                        "tribunal with VALIS"),
                               "motivo": "P-KI67 without a global transform"}})
            return out
        fcs = fc_piloto(mascaras["P-KI67"], ir_ref.tejido, ir_ref.mpp, p)
    else:
        fcs, decl = fcs_serie(ir_ref.tejido, mascaras, 1 + len(moviles), ir_ref.mpp, p,
                              sin_global)
        out["declaraciones"] += decl
    pares = {}
    for n in moviles:
        pares[n] = registra_par(referencia, n, sello, lector=lector, centroides=centroides,
                                fcs=fcs, log=log, ejecutor_valis=ejecutor_valis,
                                imagenes=imagenes, intentar_valis=intentar_valis,
                                init=inits.get(n))
    out.update({"fcs": [{"id": f["id"], "area_um2": f["area_um2"], "rotulo": f.get("rotulo")}
                        for f in fcs],
                "pares": {n: r.resumen() for n, r in pares.items()}, "resultados": pares})
    if "P-KI67" in pares:
        frac = pares["P-KI67"].fraccion_area_verificada()
        out["ii"] = {"fraccion_area_verificada_ki67": frac, "extiende": frac >= 0.5,
                     "pendientes_comprobacion_ciegas": pares["P-KI67"].pendientes(),
                     "nota": ("≥50 % of the consensus area verified" if frac >= 0.5 else
                              "<50 %: unregistered ladder only; (a), (a-bis) and the CK19 "
                              "denominator drop; registration back to the tribunal with VALIS")}
    out["cierres"] = cierres_serie(triangulos, pares, fcs, imagenes, referencia, sello, lector,
                                   centroides, log, p)
    if centroides and referencia in centroides and fcs:
        cent_ref = {referencia: np.asarray(centroides[referencia], float) * ir_ref.mpp_l0}
        verificados = {referencia: {f["id"] for f in fcs}}
        for n, r in pares.items():
            if n not in centroides or r.matriz() is None:
                continue
            xy, ids = r.nucleos_a_referencia(centroides[n], imagenes[n].mpp_l0)
            cent_ref[n] = xy[np.array([i is not None for i in ids], bool)]
            verificados[n] = {f["id"] for f in r.fragmentos if f["pasa"]}
        nombres, Smat = similitud_densidad(cent_ref, fcs, ir_ref.mpp, p, verificados)
        out["orden"] = orden_cortes(nombres, Smat, referencia)
    return out


def registra_he(sello, lector=None, serie=None, centroides=None, imagenes=None, init_ck19=None,
                log=None, ejecutor_valis=None, intentar_valis=False):
    """Puerta del piloto (iii): CK19↔P-HE y KI67↔P-HE POR FRAGMENTO (FC del piloto, de `serie` =
    salida de `registra_serie`). Un fragmento pasa con, en las dos parejas, inliers ≥30 y ≥2× la
    mejor alternativa (espejo u otro ángulo) y la TRE de F3; y cierre KI67→CK19→HE frente a
    KI67→HE ≤3° y ≤50 µm en el centroide de ESE fragmento con sus transformadas. Fragmento que no
    pasa: fuera de cruces; si no pasa ninguno, P-HE se analiza sola con el rótulo de F1.3. El
    ángulo de máscaras (eslabón 1) se anota como dato, no es puerta."""
    log = log or (lambda m: None)
    if serie is None or "resultados" not in serie or "P-KI67" not in serie["resultados"]:
        raise RegistroError("(iii) necesita la serie del piloto (registra_serie con P-KI67)")
    lector = _lector(lector)
    imagenes = imagenes if imagenes is not None else {}
    res_ki = serie["resultados"]["P-KI67"]
    fcs = res_ki.fcs
    p = res_ki.p
    r_ck = registra_par("P-CK19", "P-HE", sello, lector=lector, init=init_ck19,
                        centroides=centroides, fcs=fcs, log=log, ejecutor_valis=ejecutor_valis,
                        imagenes=imagenes, intentar_valis=intentar_valis, alternativas=True)
    out = {"ck19_he": r_ck.resumen(), "por_fc": [], "resultados": {"P-CK19": r_ck}}
    if r_ck.matriz() is None:
        out.update({"pasa_algun_fc": False, "rotulos": [ROTULOS["he_no_registrada"]],
                    "motivo": "no global CK19↔P-HE"})
        return out
    ir_ck, ir_ki = imagenes["P-CK19"], imagenes["P-KI67"]
    fcs_ki = []
    for fc in fcs:
        f_ki = res_ki.fragmento(fc["id"])
        if f_ki["pasa"]:
            fcs_ki.append(fc_en(fc, ir_ck, ir_ki, f_ki["matriz_um"]))
    # KI67↔HE directo, SIN inicializar desde KI67→CK19→HE (el cierre debe ser independiente)
    r_ki = registra_par("P-KI67", "P-HE", sello, lector=lector, centroides=centroides,
                        fcs=fcs_ki, log=log, ejecutor_valis=ejecutor_valis, imagenes=imagenes,
                        intentar_valis=intentar_valis, alternativas=True)
    out["ki67_he"] = r_ki.resumen()
    out["resultados"]["P-KI67"] = r_ki
    margen = float(p["he_margen_alternativa"])
    for fc in fcs:
        fid = fc["id"]
        fila = {"fc": fid, "pasa": False, "motivos": []}
        for etiqueta, r in (("ck19_he", r_ck), ("ki67_he", r_ki)):
            try:
                f = r.fragmento(fid)
            except KeyError:
                fila[etiqueta] = None
                fila["motivos"].append("%s: fragment not available" % etiqueta)
                continue
            alt = f.get("alternativas") or {}
            n_el = (alt.get("elegida") or {}).get("n_inliers", 0)
            mejor = alt.get("mejor", 0)
            ok_inl = n_el >= int(p["min_inliers"])
            ok_alt = n_el >= margen * max(1, mejor)
            fila[etiqueta] = {"inliers": n_el, "mejor_alternativa": mejor,
                              "alternativas": {k: alt.get(k) for k in ("espejo", "otro_angulo")},
                              "tre_pasa": f["pasa"], "metodo": f["metodo"],
                              "p90_um": f["evaluacion"].get("p90_um")}
            if not ok_inl:
                fila["motivos"].append("%s: <%d inliers" % (etiqueta, int(p["min_inliers"])))
            if not ok_alt:
                fila["motivos"].append("%s: inliers <%gx the best alternative" % (etiqueta, margen))
            if not f["pasa"]:
                fila["motivos"].append("%s: TRE gate not passed" % etiqueta)
        try:
            f_ki = res_ki.fragmento(fid)
            f_ck = r_ck.fragmento(fid)
            f_kh = r_ki.fragmento(fid)
        except KeyError:
            f_ki = f_ck = f_kh = None
        if f_ki and f_ck and f_kh and f_ki["pasa"] and f_ck["pasa"] and f_kh["pasa"]:
            M_ab = np.asarray(f_ki["matriz_um"])                      # KI67 → CK19
            M_bc = np.linalg.inv(np.asarray(f_ck["matriz_um"]))       # CK19 → HE
            M_ac = np.linalg.inv(np.asarray(f_kh["matriz_um"]))       # KI67 → HE
            punto = _aplica(np.linalg.inv(M_ab), [fc["poligono_um"].centroid.coords[0]])[0]
            fila["cierre"] = cierre(M_ab, M_bc, M_ac, punto, p)
            if not fila["cierre"]["pasa"]:
                fila["motivos"].append("closure KI67→CK19→HE vs KI67→HE >3° or >50 µm")
        else:
            fila["cierre"] = None
            fila["motivos"].append("closure not evaluable (a pair not verified)")
        fila["pasa"] = not fila["motivos"]
        out["por_fc"].append(fila)
    out["fcs_pasan"] = [x["fc"] for x in out["por_fc"] if x["pasa"]]
    out["pasa_algun_fc"] = bool(out["fcs_pasan"])
    out["rotulos"] = [] if out["pasa_algun_fc"] else [ROTULOS["he_no_registrada"]]
    out["declaraciones"] = [METODOS["he_vectores"]]
    if out["pasa_algun_fc"]:
        out["declaraciones"].append("H&E fragments that do not pass (iii): outside every "
                                    "H&E→IHC cross (%s)" % ", ".join(
                                        str(x["fc"]) for x in out["por_fc"] if not x["pasa"])
                                    if len(out["fcs_pasan"]) < len(out["por_fc"]) else
                                    "all fragments pass (iii)")
    return out


# ── diagnóstico (SOLO números; no cambia el método sellado ni escribe productos) ───────────────
# Separa (A) error de ejecución de (B) límite del método cuando el registro falla en láminas
# reales: mide lo que entra en SIFT, cuántas putativas hay y cuántas son coherentes con una
# transformación de referencia (VALIS, eslabón 1), y repite la cadena con VARIANTES que aíslan
# la causa (sin CLAHE, hematoxilina, ×32, sin el filtro del eslabón 1, la otra quiralidad). Las
# variantes son diagnóstico: el método sellado no las usa.
DIAG_UMBRALES_UM = (25.0, 50.0, 100.0)
DIAG_RATIOS = (0.8, 0.9, 1.0)
DIAG_VARIANTES = ("sin_clahe", "hematoxilina", "x32")
DIAG_MAX_MUESTRA = 2_000_000
DIAG_BLOQUE_BYTES = 256 * 2 ** 20      # bloque de la matriz de distancias de descriptores
DIAG_MAX_PARES = 1e10                  # nf·nm por encima: no se empareja (se dice)


def _r(x, k=4):
    if x is None:
        return None
    x = float(x)
    return round(x, k) if math.isfinite(x) else None


def _pcts(v):
    v = np.asarray(v, np.float32).ravel()
    if len(v) > DIAG_MAX_MUESTRA:
        v = v[::int(math.ceil(len(v) / DIAG_MAX_MUESTRA))]
    v = v[np.isfinite(v)]
    if not len(v):
        return {"n": 0}
    q = np.percentile(v, [1, 10, 50, 90, 99])
    out = {"n": int(len(v)), "media": _r(v.mean()), "de": _r(v.std())}
    out.update({k: _r(x) for k, x in zip(("p1", "p10", "p50", "p90", "p99"), q)})
    return out


class _Variante:
    """Imagen de registro con otro `img` (diagnóstico): misma máscara y mpp salvo que se den."""

    def __init__(self, ir, img, tejido=None, mpp=None):
        self.nombre, self.mpp_l0 = ir.nombre, ir.mpp_l0
        self.img = img
        self.tejido = ir.tejido if tejido is None else tejido
        self.mpp = ir.mpp if mpp is None else float(mpp)


def imagen_variante(ir, p, variante):
    """La imagen de una variante de diagnóstico (necesita `retiene=True` para las de 8 bits)."""
    if variante == "sin_clahe":
        return _Variante(ir, ir.b8.astype(np.float32) / 255.0)
    if variante == "hematoxilina":
        from skimage.exposure import equalize_adapthist
        k = max(8, int(round(p["clahe_kernel_um"] / ir.mpp)))
        return _Variante(ir, equalize_adapthist(ir.hema8, kernel_size=k,
                                                clip_limit=p["clahe_clip"]).astype(np.float32))
    if variante == "x32":
        return _Variante(ir, _diezma(ir.img, 4), tejido=_diezma(ir.tejido, 4) > 0.5,
                         mpp=ir.mpp * 4)
    raise ValueError("variante desconocida: %s" % variante)


def diag_imagen(ir):
    """Forma, mpp y estadísticos de lo que entra en SIFT (dentro y fuera del tejido)."""
    t, z = ir.tejido, ir.zona
    out = {"forma": [int(x) for x in ir.img.shape], "dtype": str(ir.img.dtype),
           "mpp": _r(ir.mpp, 6), "mpp_l0": _r(ir.mpp_l0, 6),
           "rango_img": [_r(ir.img.min()), _r(ir.img.max())],
           "frac_zona": _r(z.mean()), "frac_tejido": _r(t.mean()),
           "area_tejido_mm2": _r(t.sum() * ir.mpp ** 2 / 1e6, 3),
           "odsum_fuera_max": _r(getattr(ir, "odsum_fuera_max", None)),
           "img_tejido": _pcts(ir.img[t]), "img_vidrio": _pcts(ir.img[z & ~t])}
    gy, gx = np.gradient(ir.img)
    out["gradiente_tejido"] = _pcts(np.hypot(gx, gy)[t])
    del gy, gx
    if hasattr(ir, "b8"):
        out["odsum8_sin_clahe_tejido"] = _pcts(ir.b8[t])
        out["hema8_tejido"] = _pcts(ir.hema8[t])
    return out


def distancia_um(M1, M2, puntos_ref):
    """Distancia (µm) en la referencia entre M1 y M2 (móvil→ref) para los mismos puntos de la
    móvil que M2 lleva a `puntos_ref`. Lista, una por punto."""
    pm = _aplica(np.linalg.inv(np.asarray(M2, float)), puntos_ref)
    return [float(x) for x in np.linalg.norm(_aplica(np.asarray(M1, float), pm) - np.asarray(
        puntos_ref, float).reshape(-1, 2), axis=1)]


def putativas_ratios(rf, rm, p, ratios):
    """Lo mismo que `putativas` (match_descriptors de scikit-image: euclídea, cross_check
    sellado, ratio test sobre los descriptores de la móvil) para VARIOS ratios con UNA sola
    matriz de distancias. {ratio: (src, dst)}; ratio ≥ 1 = sin ratio test."""
    from scipy.spatial.distance import cdist
    vacio = (np.zeros((0, 2)), np.zeros((0, 2)))
    nf, nm = len(rf.xy), len(rm.xy)
    if nf < 2 or nm < 2:
        return {r: vacio for r in ratios}
    # por BLOQUES de filas (la matriz entera, nf×nm×8 bytes, reventó el techo de 10 GB con una
    # variante de muchos más rasgos): mismo resultado que la matriz entera, desempates incluidos
    # (argmin = primer índice; en columnas, el bloque anterior gana los empates)
    bloque = max(1, int(DIAG_BLOQUE_BYTES // (8 * nm)))
    i2 = np.empty(nf, np.int64)
    mejor = np.empty(nf)
    segundo = np.empty(nf)
    col_min = np.full(nm, np.inf)
    col_arg = np.zeros(nm, np.int64)
    for a in range(0, nf, bloque):
        d = cdist(rf.desc[a:a + bloque], rm.desc, metric="euclidean")
        filas = np.arange(d.shape[0])
        j = np.argmin(d, axis=1)
        i2[a:a + len(j)] = j
        mejor[a:a + len(j)] = d[filas, j]
        cm = np.argmin(d, axis=0)
        cv = d[cm, np.arange(nm)]
        nuevo = cv < col_min
        col_min[nuevo], col_arg[nuevo] = cv[nuevo], cm[nuevo] + a
        d[filas, j] = np.inf
        segundo[a:a + len(j)] = d.min(axis=1)
        del d
    i1 = np.arange(nf)
    if bool(p["cross_check"]):
        m = i1 == col_arg[i2]
        i1, i2, mejor, segundo = i1[m], i2[m], mejor[m], segundo[m]
    segundo[segundo == 0] = np.finfo(np.float64).eps
    razon = mejor / segundo
    out = {}
    for r in ratios:
        k = razon < r if r < 1.0 else np.ones(len(i1), bool)
        out[r] = (rm.xy[i2[k]], rf.xy[i1[k]])
    return out


def diag_emparejamiento(rf, rm, espejo, p, mpp, refs, puntos_ref, ratios=DIAG_RATIOS):
    """Putativas con varios ratios, coherencia con cada transformación de referencia (`refs`:
    {nombre: M real móvil→ref}) y RANSAC sellado. Solo cuentas y distancias."""
    out = {"n_rasgos_fija": int(len(rf.xy)), "n_rasgos_fija_bruto": int(rf.n_bruto),
           "n_rasgos_movil": int(len(rm.xy)), "n_rasgos_movil_bruto": int(rm.n_bruto)}
    S = np.array([-1.0, 1.0]) if espejo else np.ones(2)
    if float(len(rf.xy)) * len(rm.xy) > DIAG_MAX_PARES:
        out["omitido"] = "nf*nm > %g: matching skipped (time)" % DIAG_MAX_PARES
        return out
    por_ratio = putativas_ratios(rf, rm, p, ratios)
    for ratio in ratios:
        src, dst = por_ratio[ratio]
        d = {"n_putativas": int(len(src))}
        for nombre, M in refs.items():
            if M is None or not len(src):
                continue
            err = np.linalg.norm(_aplica(M, src * S) - dst, axis=1)
            d["coherentes_" + nombre] = {"%g" % u: int((err < u).sum()) for u in DIAG_UMBRALES_UM}
        M_e, inl = ajusta_ransac(src, dst, p, mpp)
        d["ransac_inliers"] = int(inl.sum())
        if M_e is not None:
            M = _con_espejo(M_e, espejo)
            d["ransac_angulo"] = _r(angulo(M), 2)
            for nombre, Mr in refs.items():
                if Mr is not None and len(puntos_ref):
                    d["ransac_vs_%s_um" % nombre] = [_r(x, 1) for x in distancia_um(M, Mr,
                                                                                    puntos_ref)]
        out["ratio_%g" % ratio] = d
    return out


def _motivos_picos(res, p):
    """Resumen de una lista de picos con el motivo de cada ventana no válida."""
    n = len(res)
    sin = [r for r in res if "pico" not in r]
    con = [r for r in res if "pico" in r]
    val = [r for r in con if r["valido"]]
    out = {"n_ventanas": n, "n_validas": len(val), "sin_ncc": len(sin),
           "pico_no_positivo": sum(1 for r in con if not r["pico"] > 0),
           "razon_baja": sum(1 for r in con if r["razon"] < p["pico_ratio"]),
           "desp_grande": sum(1 for r in con if r["desp_um"] >= p["desp_max_um"]),
           "en_borde": sum(1 for r in con if r.get("borde")),
           "pico": _pcts([r["pico"] for r in con]) if con else None,
           "razon": _pcts([min(r["razon"], 99.0) for r in con]) if con else None}
    if val:
        dv = np.array([r["desp_um"] for r in val])
        out.update({"p50_um": _r(np.median(dv), 1), "p90_um": _r(np.percentile(dv, 90), 1),
                    "max_um": _r(dv.max(), 1)})
    return out


def diag_tre(lector, ir_f, ir_m, M, fc, p, vectores, cent_f=None, cent_m_l0=None):
    """(b), (b') y contención de un FC con la transformación M, con el motivo de cada ventana."""
    ventanas = rejilla_ventanas(fc["mascara"], ir_f.mpp, p)
    b = tre_hematoxilina(lector, ir_f, ir_m, np.asarray(M, float), ventanas, p, vectores)
    out = {"b": _motivos_picos(b, p)}
    if cent_f is not None and cent_m_l0 is not None:
        cm = _aplica(np.asarray(M, float), np.asarray(cent_m_l0, float) * ir_m.mpp_l0)
        out["b_prima"] = _motivos_picos(tre_densidad(cent_f, cm, ventanas, p), p)
    out["contencion"] = _r(contencion(fc["poligono_um"], ir_f, ir_m, np.asarray(M, float)))
    return out


def iou_tejido(ir_f, ir_m, M):
    m = warp_a_referencia(ir_m.tejido, ir_m, ir_f, np.asarray(M, float))
    inter = np.logical_and(m, ir_f.tejido).sum()
    return _r(inter / max(1, np.logical_or(m, ir_f.tejido).sum()))


def _resumen_m(M, centro_movil_um):
    if M is None:
        return None
    M = np.asarray(M, float)
    _Me, esc, aniso = a_euclidea(M, centro_movil_um)
    return {"matriz_um": [[_r(x, 6) for x in fila] for fila in M], "angulo": _r(angulo(M), 3),
            "espejo": bool(np.linalg.det(M[:2, :2]) < 0), "escala": _r(esc, 5),
            "anisotropia": _r(aniso, 5)}


def diag_init(ir_f, ir_m, init, M_ref, puntos):
    """Desglose del eslabón 1, signo a signo: centroides solos, + fase de máscaras (lo que hace
    `init_desde_eslabon1`) y la traslación de máxima IoU (criterio del eslabón 1), con su IoU a
    ×8 y su distancia a `M_ref` (VALIS) en los centroides de los FC."""
    from skimage.registration import phase_cross_correlation
    if init is None or init.get("angulo_grados") is None:
        return None
    espejo = bool(init["espejo"])
    yy, xx = np.nonzero(ir_m.tejido)
    yf, xf = np.nonzero(ir_f.tejido)
    c_m = np.array([(xx.mean() + 0.5) * ir_m.mpp, (yy.mean() + 0.5) * ir_m.mpp])
    c_f = np.array([(xf.mean() + 0.5) * ir_f.mpp, (yf.mean() + 0.5) * ir_f.mpp])
    out = {}
    for signo in (1.0, -1.0):
        a = math.radians(signo * float(init["angulo_grados"]))
        Rm = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0],
                       [0, 0, 1.0]])
        L = Rm @ (F_ESPEJO if espejo else np.eye(3))
        t = c_f - _aplica(L, [c_m])[0]
        Mc = np.array([[1, 0, t[0]], [0, 1, t[1]], [0, 0, 1.0]]) @ L
        m_ref = warp_a_referencia(ir_m.tejido, ir_m, ir_f, Mc)
        d, _e, _f = phase_cross_correlation(ir_f.tejido.astype(float), m_ref.astype(float))
        Mp = np.array([[1, 0, d[1] * ir_f.mpp], [0, 1, d[0] * ir_f.mpp], [0, 0, 1.0]]) @ Mc
        Mi, iou_busqueda = init_por_iou(ir_f, ir_m, L)
        fila = {"desp_fase_um": _r(math.hypot(d[0], d[1]) * ir_f.mpp, 1),
                "iou_busqueda_max": _r(iou_busqueda)}
        for nombre, M in (("centroides", Mc), ("fase", Mp), ("max_iou", Mi)):
            fila["iou_" + nombre] = iou_tejido(ir_f, ir_m, M)
            if M_ref is not None and puntos:
                fila["vs_valis_um_" + nombre] = [_r(x, 1) for x in distancia_um(M, M_ref,
                                                                               puntos)]
        out["signo_%+d" % signo] = fila
    return out


def _centro_tejido(ir):
    yy, xx = np.nonzero(ir.tejido)
    return np.array([(xx.mean() + 0.5) * ir.mpp, (yy.mean() + 0.5) * ir.mpp]) if len(yy) else None


def diagnostico_par(lector, ir_f, ir_m, p, vectores, fcs, init=None, cent_f=None, cent_m_l0=None,
                    ejecutor_valis=None, valis=True, variantes=DIAG_VARIANTES, rasgos_fija=None,
                    log=None, valis_ambas=False):
    """Números de una pareja móvil→fija con la cadena SELLADA y con variantes de diagnóstico.

    `fcs`: FC en la rejilla ×8 de la fija (piloto: los de (ii)); None = los de la pareja con la
    global. `init`: eslabón 1 como lo recibe `registra_par`. `valis`: corre VALIS en las dos
    quiralidades SOLO como referencia (no es un intento del registro; no gasta ninguna vuelta).
    `rasgos_fija`: caché {variante: Rasgos} de la fija entre parejas. No escribe nada."""
    import time
    log = log or (lambda m: None)
    rasgos_fija = {} if rasgos_fija is None else rasgos_fija
    t0 = time.time()
    out = {"fija": ir_f.nombre, "movil": ir_m.nombre,
           "mpp_movil_entre_fija": _r(ir_m.mpp / ir_f.mpp, 6),
           "residual_um": _r(float(p["residual_px"]) * ir_f.mpp, 3)}
    c_f = _centro_tejido(ir_f)
    c_m = _centro_tejido(ir_m)
    # ── inicialización (eslabón 1) ──
    ini = _init_matriz(ir_f, ir_m, init)
    if ini is not None:
        out["init"] = {"entrada": {k: (v if not isinstance(v, float) else _r(v, 3))
                                   for k, v in (init or {}).items() if k != "matriz_um"},
                       "iou_mascaras_x8": _r(ini.get("iou_mascaras")),
                       "matriz": _resumen_m(ini["matriz_um"], c_m)}
    M_init = None if ini is None else np.asarray(ini["matriz_um"], float)
    espejo_ref = bool(ini["espejo"]) if ini is not None else None
    # ── VALIS como referencia: la quiralidad del eslabón 1 (y la otra si `valis_ambas`: en P-HER2
    # su VALIS en espejo llevó el árbol a 19,7 GB, por encima del techo de 10) ──
    Mv = {}
    if valis:
        for esp in ((False, True) if valis_ambas else (bool(espejo_ref),)):
            M, info = respaldo_valis(ir_f.img, ir_m.img, ir_f.mpp, ejecutor_valis, espejo=esp,
                                     mpp_m=ir_m.mpp)
            Mv[esp] = M
            out["valis_%s" % ("espejo" if esp else "directa")] = dict(
                {k: info.get(k) for k in ("corrio", "rc", "segundos")},
                motivo=(info.get("motivo") or "")[:300] or None, **(_resumen_m(M, c_m) or {}))
            log("  VALIS %s: %s" % ("espejo" if esp else "directa", "ran" if M is not None
                                    else info.get("motivo")))
    if espejo_ref is None:
        espejo_ref = False
    M_valis = Mv.get(espejo_ref)
    M_valis_e = None
    if M_valis is not None and c_m is not None:
        M_valis_e = a_euclidea(M_valis, c_m)[0]
    refs = {"valis": M_valis, "valis_euclidea": M_valis_e, "init": M_init}
    if Mv.get(not espejo_ref) is not None:
        refs["valis_otra_quiralidad"] = Mv[not espejo_ref]
    puntos = [np.asarray(f["poligono_um"].centroid.coords[0]) for f in (fcs or [])
              if f.get("poligono_um") is not None]
    if M_init is not None and M_valis is not None:
        out["init_vs_valis"] = {
            "angulo_init": _r(angulo(M_init), 2), "angulo_valis": _r(angulo(M_valis), 2),
            "angulo_inversa_init": _r(angulo(np.linalg.inv(M_init)), 2),
            "dist_en_centroides_fc_um": [_r(x, 1) for x in distancia_um(M_init, M_valis, puntos)]
            if puntos else None}
    out["iou_tejido_x8"] = {k: iou_tejido(ir_f, ir_m, M) for k, M in refs.items()
                            if M is not None}
    if init is not None and init.get("angulo_grados") is not None:
        out["init_desglose"] = diag_init(ir_f, ir_m, init, M_valis, puntos)
    # ── SIFT sellado: las dos quiralidades, sin filtro del eslabón 1 ──
    rf = rasgos_sift(ir_f, p)
    out["sellado"] = {}
    for esp in (False, True):
        rm = rasgos_sift(ir_m, p, esp)
        log("  SIFT sellado %s: rasgos fija %d, móvil %d" % (
            "espejo" if esp else "directa", len(rf.xy), len(rm.xy)))
        out["sellado"]["espejo" if esp else "directa"] = diag_emparejamiento(
            rf, rm, esp, p, ir_f.mpp, refs, puntos)
    log("  SIFT sellado: %.0f s" % (time.time() - t0))
    # la global EXACTA de la cadena (con el filtro del eslabón 1, si lo hay)
    g = registro_global(ir_f, ir_m, p, ini)
    out["global_cadena"] = {"aceptada": g["aceptada"], "motivo": g["motivo"],
                            "elegida_espejo": g["elegida"]["espejo"],
                            "elegida_putativas": g["elegida"]["n_putativas"],
                            "elegida_inliers": g["elegida"]["n_inliers"],
                            "filtrado_por_init": g["elegida"]["filtrado_por_init"],
                            "alternativa_putativas": g["alternativa"]["n_putativas"],
                            "alternativa_inliers": g["alternativa"]["n_inliers"]}
    if ini is not None and len(g["elegida"]["_src"]) and M_valis is not None:
        S = np.array([-1.0, 1.0]) if g["elegida"]["espejo"] else np.ones(2)
        err = np.linalg.norm(_aplica(M_valis, g["elegida"]["_src"] * S) - g["elegida"]["_dst"],
                             axis=1)
        out["global_cadena"]["filtradas_coherentes_valis"] = {
            "%g" % u: int((err < u).sum()) for u in DIAG_UMBRALES_UM}
    M_g = np.asarray(g["elegida"]["matriz_um"], float) if g["aceptada"] else M_init
    if fcs is None:
        if M_g is None:
            out["fcs"] = []
            return out
        m_ref = warp_a_referencia(ir_m.tejido, ir_m, ir_f, M_g)
        fcs = fragmentos_consenso([m_ref, ir_f.tejido], ir_f.mpp, p, min_votos=2)
        puntos = [np.asarray(f["poligono_um"].centroid.coords[0]) for f in fcs
                  if f.get("poligono_um") is not None]
    # ── variantes (en la quiralidad de referencia; ×32 en las dos) ──
    out["variantes"] = {}
    for v in variantes:
        t1 = time.time()
        if v not in rasgos_fija:
            vf = imagen_variante(ir_f, p, v)
            rasgos_fija[v] = (_rasgos_sift(vf, p, False), vf.mpp)
            del vf
        rf_v, mpp_v = rasgos_fija[v]
        vm = imagen_variante(ir_m, p, v)
        quir = (False, True) if v == "x32" else (espejo_ref,)
        d = {"mpp": _r(mpp_v, 4)}
        for esp in quir:
            rm_v = _rasgos_sift(vm, p, esp)
            log("  variante %s %s: rasgos fija %d, móvil %d" % (
                v, "espejo" if esp else "directa", len(rf_v.xy), len(rm_v.xy)))
            d["espejo" if esp else "directa"] = diag_emparejamiento(rf_v, rm_v, esp, p, ir_f.mpp,
                                                                    refs, puntos)
        del vm
        out["variantes"][v] = d
        log("  variante %s: %.0f s" % (v, time.time() - t1))
    # ── por FC: SIFT restringido, fase y TRE con cada transformación ──
    rm = rasgos_sift(ir_m, p, espejo_ref)
    out["por_fc"] = []
    for fc in fcs:
        t1 = time.time()
        c = np.asarray(fc["poligono_um"].centroid.coords[0])
        fila = {"id": fc["id"], "area_mm2": _r(fc["area_um2"] / 1e6, 3),
                "n_ventanas": len(rejilla_ventanas(fc["mascara"], ir_f.mpp, p))}
        Ms = {}
        if M_g is not None:
            Ms["global_cadena"] = M_g
        if M_valis is not None:
            Ms["valis_euclidea_fc"] = a_euclidea(M_valis, _aplica(np.linalg.inv(M_valis),
                                                                  [c])[0])[0]
            Ms["valis_similitud"] = M_valis
        fila["sift_fc"] = {}
        for nombre in ("global_cadena", "valis_euclidea_fc"):
            if nombre not in Ms:
                continue
            src, dst, M_e, inl = sift_en_fc(ir_f, rf, rm, espejo_ref, Ms[nombre], fc, p)
            d = {"n_putativas": int(len(src)), "ransac_inliers": int(inl.sum())}
            if M_valis is not None and len(src):
                S = np.array([-1.0, 1.0]) if espejo_ref else np.ones(2)
                err = np.linalg.norm(_aplica(M_valis, src * S) - dst, axis=1)
                d["coherentes_valis"] = {"%g" % u: int((err < u).sum())
                                         for u in DIAG_UMBRALES_UM}
            if M_e is not None:
                Msift = _con_espejo(M_e, espejo_ref)
                d["angulo"] = _r(angulo(Msift), 2)
                if M_valis is not None:
                    d["vs_valis_um"] = _r(distancia_um(Msift, M_valis, [c])[0], 1)
                if inl.sum() >= 3:
                    Ms["sift_fc_desde_" + nombre] = Msift
            fila["sift_fc"][nombre] = d
        # (2) fase sobre ODsum y (3) mapa de densidad, como la cadena, desde la global de la
        # cadena y desde VALIS euclídea en el FC
        for desde in ("global_cadena", "valis_euclidea_fc"):
            if desde not in Ms:
                continue
            M0 = Ms[desde]
            pasos = [("fase_odsum", lambda M0=M0: fase_en_fc(ir_f, ir_m, M0, fc, p, tuple(c)))]
            if cent_f is not None and cent_m_l0 is not None:
                pasos.append(("densidad", lambda M0=M0: densidad_en_fc(
                    ir_f, ir_m, M0, fc, cent_f, cent_m_l0, p, tuple(c))))
            for paso, f in pasos:
                D, ncc = f()
                if D is None:
                    continue
                nombre = "%s_desde_%s" % (paso, desde)
                Ms[nombre] = D @ M0
                dc = _aplica(D, [c])[0] - c          # lo que mueve la corrección en el FC
                fila[nombre] = {"ncc": _r(ncc), "giro": _r(angulo(D), 2),
                                "desp_en_centroide_um": _r(math.hypot(dc[0], dc[1]), 1)}
                if M_valis is not None:
                    fila[nombre]["vs_valis_um"] = _r(distancia_um(D @ M0, M_valis, [c])[0], 1)
        if M_valis is not None and M_g is not None:
            fila["global_vs_valis_um"] = _r(distancia_um(M_g, M_valis, [c])[0], 1)
        fila["tre"] = {nombre: diag_tre(lector, ir_f, ir_m, M, fc, p, vectores, cent_f,
                                        cent_m_l0) for nombre, M in Ms.items()}
        # comprobación de la propia medida: la fija contra sí misma con la identidad
        fila["tre"]["identidad_fija_contra_si_misma"] = diag_tre(
            lector, ir_f, ir_f, np.eye(3), fc, p, vectores, cent_f,
            None if cent_f is None else np.asarray(cent_f) / ir_f.mpp_l0)
        out["por_fc"].append(fila)
        log("  %s: %.0f s" % (fc["id"], time.time() - t1))
    out["segundos"] = round(time.time() - t0, 1)
    return out


def main(argv):
    print("laminillas_registro: módulo; lo llama el procesador `laminillas_registro` por la "
          "ventanilla. Parámetros de partida para el sello:")
    import json
    print(json.dumps(PARAMS_PARTIDA, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
