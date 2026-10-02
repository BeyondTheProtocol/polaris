#!/usr/bin/env python3
"""tools/laminillas_color.py — color de la tanda (plan «laminillas DFCI», Puerta del piloto, (0)).

Matemática pura (numpy, scipy, scikit-image): no lee láminas ni células. Quien lee es
`laminillas_congela` (a través del lector único) y quien decide si se puede leer, su sello.

Contenido, en el orden del plan:
  · Desmezcla de Ruifrok con VECTORES DE TANDA, no por lámina:
      H   = primera dirección principal de los píxeles nucleares de HER2NEG+HER2 (máscara de
            InstanSeg sobre RGB, independiente de los vectores);
      DAB = de KI67/SYN/CK19 sin OD > 1 (Macenko anclado en H: ver `vector_dab`);
      tercero = producto vectorial.
  · Residuo de desmezcla (i)-(iv); umbral = 2× el máximo de las cinco; «slide-specific DAB vector»;
    «counterstain differs».
  · T por compartimento (núcleo y anillo de 3 µm) = max(p99,9 de HER2NEG + 0,05; 0,10 OD), con IC
    por bootstrap de fragmentos; p99, p99,9 y nº de objetos sobre cada uno; rama > 0,25; banda de T.
  · Tasa de falsos positivos de P-HER2 con IC binomial y por bootstrap de bloques (rige el más
    ancho por arriba); sesgo del estimador de DAB frente a contratinción citoplasmática.
  · Cortes del H-score T/0,4/0,6.
  · Máscara CK19+ por la regla del plan (valle bimodal ≤ 0,5× el pico menor; si no, Otsu restringido
    a tejido y «CK19 threshold not bimodal»; cierre de 6 µm; componentes < 200 µm² fuera;
    sensibilidad ×0,75/×1,25 → «denominator-dependent» si el denominador cambia > 10 %).
  · I0 local sin patrón por franja; píxeles saturados no H/DAB (`REGLA_SATURADO` v2: el recorte
    en intensidad no es artefacto, se anota aparte).

Toda cadena que va al informe sale de `ROTULOS` (frases EXACTAS del plan). Todo parámetro de regla
está en `parametros()`: (0) lo sella y el código posterior al sello lo coteja antes de medir.
Coordenadas de píxel: el píxel i cubre [i, i+1) (`a_pixel`: floor, nunca round).
"""
import math

import numpy as np

# ── Rótulos: frases EXACTAS del plan. Ninguna otra cadena de este módulo va al informe. ─────────
ROTULOS = {
    "suelo": "threshold at fixed floor (0.10 OD); measured floor p99.9 = {p999:.3f}",
    "high_floor": "high floor",
    "dab_propio": "slide-specific DAB vector",
    "contratincion": "counterstain differs",
    "ck19_no_bimodal": "CK19 threshold not bimodal",
    "denominador": "denominator-dependent",
    "hscore": "fixed, uncalibrated cut-offs",
    "ck19_sin_verdad": "no independent ground truth for the epithelial mask; editable layer provided",
    "metodos_T": "fixed after a pixel-level stain reconnaissance, before any cell-level measurement",
    "her2neg": "floor slide",
    # actualización 19 (2-oct): el recorte en intensidad se anota, no es artefacto
    "dab_recortado": "DAB OD clipped in {pct:.1f} % of cells; H-score is a lower bound for them",
}

# ── Parámetros del plan (se copian al sello; cambiarlos después del sello no cambia lo sellado) ─
T_SUELO = 0.10              # OD
T_MARGEN = 0.05             # p99,9 + 0,05
T_ALTO = 0.25               # rama > 0,25
BANDA_ANCHO = 0.05          # banda de T = [max(T − 0,05; p99,9 + 0,02); T + 0,05]
BANDA_P999 = 0.02
HSCORE_CORTES = (0.4, 0.6)  # con T delante: T/0,4/0,6
ANILLO_UM = 3.0
OD_MAX_DAB = 1.0            # «sin OD > 1» (por canal)
SEL_RESIDUO = (0.3, 1.0)    # (i) DAB 0,3-1,0 OD (Ruifrok provisional); (ii) ODsum 0,3-1,0
RESIDUO_FACTOR = 2.0        # umbral = 2× el máximo de las cinco
ANGULO_CONTRATINCION = 10.0  # (iv) grados
# `min_nucleos_fragmento`: con menos núcleos en el denominador base, el cambio relativo de un
# fragmento no se evalúa (el mínimo de 50 de las regiones del plan; inferencia mía, sellada).
CK19 = dict(mpp=1.0, sigma_um=2.0, valle_max=0.5, cierre_um=6.0, area_min_um2=200.0,
            sensibilidad=(0.75, 1.25), cambio_max=0.10, bins=256, suavizado_bins=2.0,
            min_nucleos_fragmento=50)

# Parámetros sin cifra en el plan (inferencia mía): TODOS van en `parametros()`, que (0) sella y
# que el código posterior al sello coteja antes de medir (si no casan, no mide).
ODSUM_MIN_VECTOR = 0.15     # píxeles casi transparentes fuera de la estimación de H
ODSUM_MIN_DAB = 0.30        # ídem para DAB: ángulos estables (como la selección de (i))
PERCENTIL_DAB = 99.0        # Macenko: extremo robusto del ángulo
SAT_INTENSIDAD = 2          # canal RGB ≤ 2 → OD no fiable («recortado en intensidad»)
SAT_HSV_MIN = 0.5           # «saturado no H/DAB»: color vivo …
FUERA_PLANO_MIN = 0.25      # … y fuera del plano H-DAB (|x| / Σ|c|)
ODSUM_MIN_COLOR = 0.15      # … y con tinta de verdad (regla de artefacto, excluye objetos de T)
# Regla del artefacto «píxeles saturados no H/DAB» (tinta, pigmento), VERSIONADA (actualización 19
# del plan, 2-oct). La v1 contaba además todo píxel recortado en intensidad (un canal ≤
# SAT_INTENSIDAD) y así el DAB fuerte salía como artefacto (P-CK19: 63 % de las CK19+ fuera). v2:
# solo color vivo fuera del plano H-DAB, evaluado en píxeles NO recortados (un píxel recortado no
# da dirección de OD fiable y no puede contar como «no H/DAB»); el recorte se anota aparte por
# célula y no es artefacto. Va en `parametros()`: un sello v1 no casa con este código (no mide) y
# la v2 queda sellada.
REGLA_SATURADO = dict(
    version=2,
    artefacto="color vivo no explicado por H/DAB, solo en pixeles no recortados en intensidad",
    recorte=("fraccion de pixeles con algun canal RGB <= SAT_INTENSIDAD en nucleo y anillo, por "
             "celula; no es artefacto; su DAB es cota inferior"))
FRANJA_UMBRAL = 3.0         # niveles de gris (el FRANJA_MAX de la ingesta)
# Tasa de falsos positivos en P-HER2: es un % GLOBAL de lámina, así que su IC va por bootstrap de
# bloques (regiones de 200 µm, el L típico del plan, dentro de fragmento; 2.000 réplicas), como
# el resto de % globales; el Clopper-Pearson queda de referencia. Rige el de límite superior más
# alto (la puerta de señal usa ese límite): con k = 0 el bootstrap da [0, 0] y rige el binomial.
TASA = dict(bloque_um=200.0, n_boot=2000, alfa=0.05)
# Sesgo del estimador de DAB frente a contratinción citoplasmática (se mide en (0) y se sella).
SESGO_DAB_CH = (0.0, 0.05, 0.10)

# Ruifrok & Johnston (2001), los de `skimage.color.rgb_from_hdx`: solo para la selección
# PROVISIONAL de (i); nunca para medir.
RUIFROK_H = (0.650, 0.704, 0.286)
RUIFROK_DAB = (0.268, 0.570, 0.776)


def parametros():
    """Todo parámetro de regla de este módulo, tal como se sella en (0)."""
    return {"T_SUELO": T_SUELO, "T_MARGEN": T_MARGEN, "T_ALTO": T_ALTO,
            "BANDA_ANCHO": BANDA_ANCHO, "BANDA_P999": BANDA_P999,
            "HSCORE_CORTES": list(HSCORE_CORTES), "ANILLO_UM": ANILLO_UM,
            "OD_MAX_DAB": OD_MAX_DAB, "SEL_RESIDUO": list(SEL_RESIDUO),
            "RESIDUO_FACTOR": RESIDUO_FACTOR, "ANGULO_CONTRATINCION": ANGULO_CONTRATINCION,
            "CK19": dict(CK19, sensibilidad=list(CK19["sensibilidad"])),
            "ODSUM_MIN_VECTOR": ODSUM_MIN_VECTOR, "ODSUM_MIN_DAB": ODSUM_MIN_DAB,
            "PERCENTIL_DAB": PERCENTIL_DAB, "SAT_INTENSIDAD": SAT_INTENSIDAD,
            "REGLA_SATURADO": dict(REGLA_SATURADO),
            "SAT_HSV_MIN": SAT_HSV_MIN, "FUERA_PLANO_MIN": FUERA_PLANO_MIN,
            "ODSUM_MIN_COLOR": ODSUM_MIN_COLOR, "FRANJA_UMBRAL": FRANJA_UMBRAL,
            "TASA": dict(TASA), "SESGO_DAB_CH": list(SESGO_DAB_CH),
            "RUIFROK_H": list(RUIFROK_H), "RUIFROK_DAB": list(RUIFROK_DAB),
            "I0_PASO_UM": I0_PASO_UM}


# ── básicos ───────────────────────────────────────────────────────────────────────────────────
def unitario(v):
    v = np.asarray(v, np.float64)
    n = float(np.linalg.norm(v))
    if not n or not math.isfinite(n):
        raise ValueError("vector nulo")
    v = v / n
    return -v if v.sum() < 0 else v


def angulo(a, b):
    """Ángulo en grados entre dos direcciones (sin signo)."""
    a, b = unitario(a), unitario(b)
    return float(np.degrees(np.arccos(np.clip(abs(float(a @ b)), -1.0, 1.0))))


def od(rgb, i0=255.0):
    """OD = −log10(I / I0) por canal, con I ≥ 1. SIN recortar a 0: el ruido del vidrio da OD
    negativa y recortarla sesgaría hacia arriba el suelo que se mide en HER2NEG (el
    reconocimiento midió DAB p50 −0,02/−0,03 en las dos láminas de suelo)."""
    i = np.maximum(np.asarray(rgb, np.float64), 1.0)
    return -np.log10(i / np.asarray(i0, np.float64))


def matriz(h, d):
    """Filas H, DAB y tercero = H × DAB (normalizado). od = C @ M."""
    h, d = unitario(h), unitario(d)
    x = np.cross(h, d)
    x = x / np.linalg.norm(x)
    return np.vstack([h, d, x])


def desmezcla(od_px, M):
    """Concentraciones (…, 3): H, DAB, tercero. Ruifrok: C = OD · M⁻¹."""
    od_px = np.asarray(od_px, np.float64)
    return od_px @ np.linalg.inv(M)


def dab_de(od_medio, M):
    """DAB de un OD MEDIO por objeto: la desmezcla es lineal, así que la media del DAB por píxel
    es el DAB del OD medio. Por eso los objetos guardan su OD medio (3 canales) y T se puede
    recalcular con otro par de vectores sin volver a leer píxeles."""
    return desmezcla(od_medio, M)[..., 1]


def saturado_intensidad(rgb):
    """Píxeles RECORTADOS en intensidad (algún canal ≤ SAT_INTENSIDAD): su OD no es fiable ni en
    magnitud ni en dirección. No son artefacto (`REGLA_SATURADO` v2): se anotan aparte por célula
    y se excluyen de lo que necesita la dirección del OD (vectores de tanda y residuo)."""
    return np.asarray(rgb).min(axis=-1) <= SAT_INTENSIDAD


def color_vivo_no_hdab(rgb, M, i0=255.0):
    """Criterio de color, SIN mirar el recorte: color vivo (HSV ≥ SAT_HSV_MIN), fuera del plano
    H-DAB (|tercero| / Σ|c| > FUERA_PLANO_MIN) y con tinta de verdad (ODsum > ODSUM_MIN_COLOR).
    En un píxel recortado la dirección del OD no es fiable: por eso el artefacto es
    `saturados_no_hdab`, no esto."""
    rgb = np.asarray(rgb)
    o = od(rgb, i0)
    s = o.sum(axis=-1)
    c = np.abs(desmezcla(o, M))
    fuera = c[..., 2] / np.maximum(c.sum(axis=-1), 1e-9)
    f = rgb.astype(np.float64)
    mx, mn = f.max(axis=-1), f.min(axis=-1)
    sat_hsv = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0.0)
    return (sat_hsv >= SAT_HSV_MIN) & (fuera > FUERA_PLANO_MIN) & (s > ODSUM_MIN_COLOR)


def saturados_no_hdab(rgb, M, i0=255.0):
    """Regla clásica de artefacto del plan, «píxeles saturados no H/DAB» (tinta, pigmento),
    `REGLA_SATURADO` v2: color vivo que H y DAB no explican, SOLO en píxeles no recortados. Un
    píxel recortado (DAB fuerte, sobre todo) no cuenta: no da dirección de OD fiable. La v1
    devolvía además todo píxel recortado (`vivo | recortado`) y sacaba el DAB fuerte como
    artefacto. Los umbrales se sellan."""
    return color_vivo_no_hdab(rgb, M, i0) & ~saturado_intensidad(rgb)


# ── vectores de tanda ─────────────────────────────────────────────────────────────────────────
def _filtra(od_px, odsum_min, od_max=None, sin_saturar=None):
    p = np.asarray(od_px, np.float64).reshape(-1, 3)
    ok = np.isfinite(p).all(axis=1) & (p.sum(axis=1) > odsum_min)
    if od_max is not None:
        ok &= p.max(axis=1) <= od_max
    if sin_saturar is not None:
        ok &= ~np.asarray(sin_saturar).reshape(-1)
    return p[ok]


def _dir_principal(p):
    """Primera dirección principal SIN centrar: la ley de Beer-Lambert pasa por el origen."""
    w, v = np.linalg.eigh(p.T @ p)
    return unitario(v[:, int(np.argmax(w))])


def vector_h(od_nuclear, odsum_min=ODSUM_MIN_VECTOR, minimo=100):
    """H de tanda: primera dirección principal de los píxeles nucleares (HER2NEG + HER2)."""
    p = _filtra(od_nuclear, odsum_min)
    if len(p) < minimo:
        raise ValueError("menos de %d OD nucleares útiles para H (%d)" % (minimo, len(p)))
    return _dir_principal(p), int(len(p))


def vector_dab(od_px, h, od_max=OD_MAX_DAB, odsum_min=ODSUM_MIN_DAB, percentil=PERCENTIL_DAB):
    """DAB de tanda con H fijo («Macenko anclado»): plano = H y la dirección principal del resto
    ortogonal a H; DAB = el ángulo del percentil `percentil` en ese plano. El plan solo dice «de
    KI67, SYN y CK19 sin OD > 1»; el estimador es inferencia mía y se sella con su percentil."""
    h = unitario(h)
    p = _filtra(od_px, odsum_min, od_max)
    if len(p) < 100:
        raise ValueError("menos de 100 píxeles útiles para DAB (%d)" % len(p))
    r = p - np.outer(p @ h, h)
    w, v = np.linalg.eigh(r.T @ r)
    e = v[:, int(np.argmax(w))]
    e = e - (e @ h) * h
    e = e / np.linalg.norm(e)
    if np.mean(r @ e) < 0:
        e = -e
    phi = np.arctan2(p @ e, p @ h)
    a = float(np.percentile(phi, percentil))
    return unitario(math.cos(a) * h + math.sin(a) * e), int(len(p))


def normal_plano(od_px, h):
    """Normal del plano H-DAB de unos píxeles con H conocido: el resto ortogonal a H de cada píxel
    (a·H + b·DAB) es paralelo a la parte de DAB ortogonal a H, sea cual sea a. A diferencia del
    ángulo de DAB DENTRO del plano (que sin píxeles de DAB puro no se identifica), la orientación
    del plano sí se identifica con mezclas."""
    h = unitario(h)
    p = np.asarray(od_px, np.float64).reshape(-1, 3)
    r = p - np.outer(p @ h, h)
    w, v = np.linalg.eigh(r.T @ r)
    e = v[:, int(np.argmax(w))]
    x = np.cross(h, e)
    return x / np.linalg.norm(x)


def direccion_dab_lamina(od_px, h_tanda, sin_saturar=None):
    """(i) Dirección DAB de UNA lámina solo con píxeles de DAB 0,3-1,0 OD según Ruifrok
    provisional, nunca saturados; mismo estimador que el vector de tanda. Devuelve (dab, n,
    normal del plano): con DAB solo nuclear (sin píxeles de DAB puro) el ángulo de Macenko sale
    sesgado hacia H; el del PLANO no."""
    p = np.asarray(od_px, np.float64).reshape(-1, 3)
    Mp = matriz(RUIFROK_H, RUIFROK_DAB)
    dab = desmezcla(p, Mp)[:, 1]
    sel = (dab >= SEL_RESIDUO[0]) & (dab <= SEL_RESIDUO[1]) & np.isfinite(p).all(axis=1)
    if sin_saturar is not None:
        sel &= ~np.asarray(sin_saturar).reshape(-1)
    if sel.sum() < 100:
        return None, int(sel.sum()), None
    d, n = vector_dab(p[sel], h_tanda, od_max=np.inf, odsum_min=0.0)
    return d, n, normal_plano(p[sel], h_tanda)


def residuo(od_px, M, sin_saturar=None):
    """(ii) mediana de |tercer canal| / ODsum en píxeles con ODsum 0,3-1,0, nunca saturados."""
    p = np.asarray(od_px, np.float64).reshape(-1, 3)
    s = p.sum(axis=1)
    sel = (s >= SEL_RESIDUO[0]) & (s <= SEL_RESIDUO[1]) & np.isfinite(p).all(axis=1)
    if sin_saturar is not None:
        sel &= ~np.asarray(sin_saturar).reshape(-1)
    if sel.sum() < 100:
        return None, int(sel.sum())
    x = desmezcla(p[sel], M)[:, 2]
    return float(np.median(np.abs(x) / s[sel])), int(sel.sum())


def umbral_residuo(residuos_tanda):
    vals = [v for v in residuos_tanda.values() if v is not None]
    if len(vals) != len(residuos_tanda):
        raise ValueError("residuo no calculable en alguna lámina de tanda: %r" % residuos_tanda)
    return RESIDUO_FACTOR * max(vals)


def evalua_residuo(res_lamina, umbral, h_tanda, od_px=None, sin_saturar=None):
    """(iii) superar el umbral NO expulsa: la lámina usa su DAB propio con el H de tanda (T se
    recalcula en HER2NEG con ese par: lo hace quien tiene los objetos de HER2NEG)."""
    if res_lamina is None:
        return {"supera": None, "rotulos": [], "dab_propio": None}
    if res_lamina <= umbral:
        return {"supera": False, "rotulos": [], "dab_propio": None}
    d = None
    if od_px is not None:
        d, _ = vector_dab(_filtra(od_px, 0.0, None, sin_saturar), h_tanda)
    return {"supera": True, "rotulos": [ROTULOS["dab_propio"]],
            "dab_propio": None if d is None else [float(t) for t in d]}


def sesgo_dab_contratincion(h, d, cd, ch=SESGO_DAB_CH, ruido_od=0.0, tope=50000, semilla=0):
    """Sesgo del estimador de DAB (Macenko anclado) si TODO el citoplasma DAB+ llevara una
    contratinción H constante `ch`: sin píxeles de DAB puro el ángulo del percentil se desplaza
    hacia H y ningún estimador lo identifica con los datos solos. Píxeles sintéticos
    od = cd·DAB + ch·H con las concentraciones de DAB `cd` observadas en la tanda (sin ruido por
    defecto: así aísla el efecto de la contratinción del sobredisparo del percentil con ruido);
    devuelve {ch: ángulo (°) del DAB estimado al de partida}. Es una COTA bajo ese supuesto: con
    píxeles de DAB puro en los datos el sesgo real es menor."""
    h, d = unitario(h), unitario(d)
    rng = np.random.default_rng(semilla)
    cd = np.asarray(cd, np.float64).ravel()
    cd = cd[np.isfinite(cd) & (cd > 0.1)]
    if len(cd) < 100:
        return None
    if len(cd) > tope:
        cd = cd[rng.choice(len(cd), tope, replace=False)]
    out = {}
    for c in ch:
        od_px = np.outer(cd, d) + c * h + rng.normal(0, ruido_od, (len(cd), 3))
        try:
            e, _ = vector_dab(od_px, h)
        except ValueError:
            out["%.2f" % c] = None
            continue
        out["%.2f" % c] = angulo(e, d)
    return out


def contratincion(od_nucleos_dab_neg, h_tanda, minimo=100, angulo_max=ANGULO_CONTRATINCION):
    """(iv) solo sale del mapa si la dirección H en núcleos DAB-negativos se aparta > 10° de la
    de tanda. Recibe OD de núcleos DAB-negativos (píxeles, o el OD medio de cada núcleo: el de
    un núcleo sin DAB es c·H de la lámina). Tras el sello, `angulo_max` es el sellado."""
    h, n = vector_h(od_nucleos_dab_neg, minimo=minimo)
    a = angulo(h, h_tanda)
    return {"angulo": a, "n_pixeles": n, "fuera_mapa": a > angulo_max,
            "rotulos": [ROTULOS["contratincion"]] if a > angulo_max else []}


# ── T por compartimento ───────────────────────────────────────────────────────────────────────
def banda(T, p999):
    return [max(T - BANDA_ANCHO, p999 + BANDA_P999), T + BANDA_ANCHO]


def umbral_T(valores, fragmentos, excluir=None, n_boot=2000, semilla=0):
    """T = max(p99,9 + 0,05; 0,10 OD) sobre los objetos NO excluidos (artefacto, tercil bajo de
    foco, 50 µm del borde de fragmento, saturados no H/DAB: lo decide quien llama), con IC95 por
    bootstrap de FRAGMENTOS. Devuelve lo que se sella: p99, p99,9, nº de objetos sobre cada uno,
    T, qué término rige, rótulo, banda y estado («alto» si T > 0,25)."""
    v = np.asarray(valores, np.float64)
    f = np.asarray(fragmentos)
    ok = np.isfinite(v)
    if excluir is not None:
        ok &= ~np.asarray(excluir, bool)
    v, f = v[ok], f[ok]
    if len(v) < 100:
        raise ValueError("menos de 100 objetos para T (%d)" % len(v))
    p99, p999 = (float(x) for x in np.percentile(v, [99.0, 99.9]))
    medido = p999 + T_MARGEN
    T = max(medido, T_SUELO)
    rige = "suelo_fijo" if medido < T_SUELO else "p99.9+0.05"
    rotulos = [ROTULOS["suelo"].format(p999=p999)] if rige == "suelo_fijo" else []
    # bootstrap de fragmentos: se remuestrean fragmentos enteros (no objetos)
    rng = np.random.default_rng(semilla)
    ids = np.unique(f)
    grupos = [v[f == i] for i in ids]
    bt = []
    if len(ids) >= 2:
        for _ in range(n_boot):
            m = np.concatenate([grupos[j] for j in rng.integers(0, len(ids), len(ids))])
            bt.append(np.percentile(m, 99.9))
    bt = np.asarray(bt)
    ic999 = [float(np.percentile(bt, 2.5)), float(np.percentile(bt, 97.5))] if len(bt) else None
    icT = ([max(x + T_MARGEN, T_SUELO) for x in ic999] if ic999 else None)
    return {
        "n_objetos": int(len(v)), "n_fragmentos": int(len(ids)),
        "p99": p99, "p999": p999,
        "n_sobre_p99": int((v > p99).sum()), "n_sobre_p999": int((v > p999).sum()),
        "T": float(T), "rige": rige, "terminos": {"p999+0.05": medido, "suelo": T_SUELO},
        "ic95_p999": ic999, "ic95_T": icT, "n_boot": int(len(bt)), "metodo_percentil": "linear",
        "banda": banda(T, p999), "estado": "alto" if T > T_ALTO else "ok", "rotulos": rotulos,
    }


def tasa_sobre_T(valores, T, excluir=None, alfa=TASA["alfa"], bloques=None,
                 n_boot=TASA["n_boot"], semilla=0):
    """Fracción de objetos sobre T (falsos positivos en P-HER2) con dos IC95: Clopper-Pearson
    (células independientes) y bootstrap de BLOQUES (`bloques`: id de región dentro de fragmento
    por objeto; se remuestrean bloques enteros, así un foco de falsos positivos agrupados ensancha
    el intervalo). `ic95` = el de límite superior más alto, que es el que usa la puerta de señal;
    `ic95_rige` dice cuál fue."""
    from scipy.stats import beta
    v = np.asarray(valores, np.float64)
    ok = np.isfinite(v)
    if excluir is not None:
        ok &= ~np.asarray(excluir, bool)
    n = int(ok.sum())
    sobre = v[ok] > T
    k = int(sobre.sum())
    lo = 0.0 if k == 0 else float(beta.ppf(alfa / 2, k, n - k + 1))
    hi = 1.0 if k == n else float(beta.ppf(1 - alfa / 2, k + 1, n - k))
    out = {"k": k, "n": n, "fraccion": k / n if n else None, "ic95_binomial": [lo, hi],
           "ic95_bootstrap": None, "n_bloques": None}
    if bloques is not None and n:
        b = np.asarray(bloques)[ok]
        _, inv = np.unique(b, return_inverse=True, axis=0)
        inv = np.asarray(inv).ravel()
        kb = np.bincount(inv, weights=sobre.astype(np.float64))
        nb = np.bincount(inv).astype(np.float64)
        out["n_bloques"] = int(len(nb))
        if len(nb) >= 2:
            rng = np.random.default_rng(semilla)
            idx = rng.integers(0, len(nb), (n_boot, len(nb)))
            fr = kb[idx].sum(axis=1) / nb[idx].sum(axis=1)
            out["ic95_bootstrap"] = [float(np.percentile(fr, 100 * alfa / 2)),
                                     float(np.percentile(fr, 100 * (1 - alfa / 2)))]
            out["n_boot"] = int(n_boot)
    bs = out["ic95_bootstrap"]
    if bs is not None and bs[1] > hi:
        out.update(ic95=bs, ic95_rige="bootstrap de bloques")
    else:
        out.update(ic95=[lo, hi], ic95_rige="binomial (Clopper-Pearson)")
    return out


def hscore(valores, T):
    """H-score digital con cortes T/0,4/0,6 («fixed, uncalibrated cut-offs»). Con T ≥ 0,4 (solo
    con «high floor») los cortes colapsan hacia arriba y se dice."""
    v = np.asarray(valores, np.float64)
    v = v[np.isfinite(v)]
    c1, c2, c3 = T, max(T, HSCORE_CORTES[0]), max(T, HSCORE_CORTES[1])
    n = len(v)
    cuenta = [int((v < c1).sum()), int(((v >= c1) & (v < c2)).sum()),
              int(((v >= c2) & (v < c3)).sum()), int((v >= c3).sum())]
    pct = [100.0 * c / n if n else float("nan") for c in cuenta]
    return {"H": pct[1] + 2 * pct[2] + 3 * pct[3], "pct": pct, "n": n, "cortes": [c1, c2, c3],
            "cortes_colapsados": T >= HSCORE_CORTES[0], "rotulos": [ROTULOS["hscore"]]}


# ── máscara CK19+ (regla sellada; el VALOR se mide tras el sello) ─────────────────────────────
def umbral_ck19(valores, bins=CK19["bins"], suavizado=CK19["suavizado_bins"],
                valle_max=CK19["valle_max"]):
    """Valle entre los dos modos del histograma dentro del tejido, válido solo si el valle ≤
    0,5× el pico menor; si no, Otsu restringido a tejido y «CK19 threshold not bimodal»."""
    from scipy.ndimage import gaussian_filter1d
    from skimage.filters import threshold_otsu
    v = np.asarray(valores, np.float64)
    v = v[np.isfinite(v)]
    lo, hi = np.percentile(v, [0.1, 99.9])
    hist, bordes = np.histogram(v, bins=bins, range=(lo, hi))
    hs = gaussian_filter1d(hist.astype(np.float64), suavizado, mode="constant")
    centros = (bordes[:-1] + bordes[1:]) / 2
    # Un modo puede caer en el primer o el último bin (el estroma, en el suelo): con ceros a los
    # lados, también cuenta como pico.
    hp = np.pad(hs, 1)
    picos = [i - 1 for i in range(1, len(hp) - 1) if hp[i] >= hp[i - 1] and hp[i] > hp[i + 1]]
    info = {"bins": bins, "suavizado_bins": suavizado, "rango": [float(lo), float(hi)]}
    if len(picos) >= 2:
        p1 = max(picos, key=lambda i: hs[i])
        p2 = max((i for i in picos if i != p1), key=lambda i: hs[i])
        a, b = sorted((p1, p2))
        valle = a + int(np.argmin(hs[a:b + 1]))
        menor = min(hs[p1], hs[p2])
        info.update(picos=[float(centros[a]), float(centros[b])], valle=float(centros[valle]),
                    cociente_valle=float(hs[valle] / menor) if menor else None)
        if menor and hs[valle] <= valle_max * menor:
            info.update(regla="valle", T=float(centros[valle]), rotulos=[])
            return info
    info.update(regla="otsu", T=float(threshold_otsu(v)), rotulos=[ROTULOS["ck19_no_bimodal"]])
    return info


def morfologia(m, r, op):
    """Cierre u apertura binaria con un disco de radio `r` px, con el borde replicado (el borde
    de la imagen no erosiona). scipy: `binary_*` de scikit-image está obsoleto desde 0.26."""
    from scipy import ndimage as ndi
    from skimage.morphology import disk
    m = np.pad(np.asarray(m, bool), r, mode="edge")
    f = ndi.binary_closing if op == "cierre" else ndi.binary_opening
    return f(m, structure=disk(r))[r:-r, r:-r]


def quita_pequenos(m, min_px):
    """Componentes de menos de `min_px` píxeles, fuera."""
    from scipy import ndimage as ndi
    lab, n = ndi.label(m)
    if not n:
        return np.asarray(m, bool)
    tam = np.bincount(lab.ravel())
    ok = tam >= min_px
    ok[0] = False
    return ok[lab]


def mascara_umbral(dab_suave, tejido, T, mpp=CK19["mpp"], p=CK19):
    """Umbral + cierre de 6 µm (radio del disco: inferencia mía, sellada) + componentes < 200 µm²
    fuera, dentro del tejido. Tras el sello, `p` es la regla SELLADA."""
    m = (dab_suave > T) & tejido
    r = max(1, int(round(p["cierre_um"] / mpp)))
    m = morfologia(m, r, "cierre") & tejido
    return quita_pequenos(m, max(1, int(math.ceil(p["area_min_um2"] / (mpp * mpp)))))


def mascara_ck19(dab, tejido, mpp=CK19["mpp"], p=CK19):
    """DAB OD con vectores de tanda a 1 µm/px, gaussiana σ 2 µm, umbral por la regla.
    Devuelve (máscara, dab suavizado, info del umbral). Tras el sello, `p` es la regla SELLADA."""
    from skimage.filters import gaussian
    g = gaussian(np.asarray(dab, np.float64), sigma=p["sigma_um"] / mpp, preserve_range=True)
    info = umbral_ck19(g[tejido], bins=int(p["bins"]), suavizado=p["suavizado_bins"],
                       valle_max=p["valle_max"])
    return mascara_umbral(g, tejido, info["T"], mpp, p), g, info


def a_pixel(xy_px):
    """Índices de píxel de coordenadas CONTINUAS. Convención única del módulo A (la de `rasteriza`
    y la de una lectura `lee_region` con origen en la esquina): el píxel i cubre [i, i+1), así
    que el índice es floor(x), nunca round(x) (round desplaza medio píxel hacia +x/+y)."""
    xy = np.asarray(xy_px, np.float64).reshape(-1, 2)
    return np.floor(xy[:, 0]).astype(int), np.floor(xy[:, 1]).astype(int)


def dentro(mascara, xy_px):
    """Pertenencia por CENTROIDE (coordenadas continuas en píxeles de la máscara: `a_pixel`)."""
    xi, yi = a_pixel(xy_px)
    h, w = mascara.shape
    ok = (xi >= 0) & (yi >= 0) & (xi < w) & (yi < h)
    out = np.zeros(len(xi), bool)
    out[ok] = mascara[yi[ok], xi[ok]]
    return out


def sensibilidad_ck19(dab_suave, tejido, T, xy_px, fragmento, mpp=CK19["mpp"], p=CK19):
    """T_CK19 ×0,75 y ×1,25: si el nº de núcleos del denominador cambia > 10 % en un fragmento,
    «denominator-dependent». Solo cuentan los FRAGMENTOS (id ≥ 0: los núcleos fuera de los
    fragmentos ≥ 0,2 mm², id −1, no forman un fragmento) con ≥ `min_nucleos_fragmento` núcleos en
    el denominador base: con menos, el cambio relativo no se evalúa (0 → 1 sería infinito) y el
    fragmento queda en `no_evaluables`. El rótulo va POR FRAGMENTO; la lámina lo lleva si algún
    fragmento evaluable lo lleva."""
    fragmento = np.asarray(fragmento)
    base = dentro(mascara_umbral(dab_suave, tejido, T, mpp, p), xy_px)
    alts = {k: dentro(mascara_umbral(dab_suave, tejido, T * k, mpp, p), xy_px)
            for k in p["sensibilidad"]}
    por = {}
    for fr in np.unique(fragmento):
        if fr < 0:
            continue
        sel = fragmento == fr
        n0 = int(base[sel].sum())
        evaluable = n0 >= int(p["min_nucleos_fragmento"])
        d = {"n_base": n0, "evaluable": evaluable, "por_factor": {}}
        dep = False
        for k, alt in alts.items():
            n1 = int(alt[sel].sum())
            cambio = abs(n1 - n0) / n0 if n0 else None
            d["por_factor"][str(k)] = {"n_alt": n1, "cambio": cambio}
            dep |= bool(evaluable and cambio > p["cambio_max"])
        d.update(dependiente=dep, rotulos=[ROTULOS["denominador"]] if dep else [])
        por[str(int(fr))] = d
    dependiente = any(d["dependiente"] for d in por.values())
    return {"por_fragmento": por, "dependiente": bool(dependiente),
            "no_evaluables": sorted(f for f, d in por.items() if not d["evaluable"]),
            "fuera_de_fragmento": int((fragmento < 0).sum()),
            "rotulos": [ROTULOS["denominador"]] if dependiente else []}


# ── I0 local ──────────────────────────────────────────────────────────────────────────────────
def franjas_i0(mapa, umbral=FRANJA_UMBRAL):
    """Patrón por franja en el I0 local: perfiles de filas y columnas (mediana), sin la tendencia
    lenta (gaussiana de σ = longitud/4); amplitud p99 del resto en niveles de gris."""
    from scipy.ndimage import gaussian_filter1d
    m = np.asarray(mapa, np.float64)
    if m.ndim == 3:
        m = m.mean(axis=2)
    pts = {}
    for eje, nombre in ((1, "filas"), (0, "columnas")):
        perfil = np.median(m, axis=eje)
        if len(perfil) < 8:
            pts[nombre] = 0.0
            continue
        lento = gaussian_filter1d(perfil, max(2.0, len(perfil) / 4.0), mode="nearest")
        pts[nombre] = float(np.percentile(np.abs(perfil - lento), 99))
    peor = max(pts.values())
    return {"hay_franjas": peor > umbral, "amplitud": pts, "umbral": umbral}


I0_PASO_UM = 16.0            # el I0 local está suavizado a ~1 mm: se evalúa cada 16 µm


def i0_region(i0, x_l0, y_l0, ancho, alto, mpp, mpp_l0):
    """I0 (alto, ancho, 3) para una región leída a `mpp` con origen (x_l0, y_l0) en L0. Acepta lo
    que devuelve `laminillas_lector.i0_local` — f(x_l0, y_l0) → RGB, bilineal, difundible —, un
    número, (3,), o {'mapa': (H,W[,3]), 'mpp': µm/px}. La función se evalúa en los centros de una
    rejilla de 16 µm y se repite por vecino (error despreciable con un campo de FWHM 1 mm)."""
    if callable(i0):
        k = max(1, int(I0_PASO_UM / mpp))
        f = mpp / mpp_l0
        nx, ny = int(math.ceil(ancho / k)), int(math.ceil(alto / k))
        xs = x_l0 + (np.arange(nx) * k + k / 2.0) * f
        ys = y_l0 + (np.arange(ny) * k + k / 2.0) * f
        g = np.asarray(i0(xs[None, :], ys[:, None]), np.float64)
        if g.ndim == 2:
            g = g[..., None]
        g = np.repeat(np.repeat(g, k, axis=0), k, axis=1)
        return g[:alto, :ancho]
    if isinstance(i0, dict) or hasattr(i0, "mapa"):
        mapa = np.asarray(i0["mapa"] if isinstance(i0, dict) else i0.mapa, np.float64)
        mpp_m = float(i0["mpp"] if isinstance(i0, dict) else i0.mpp)
        f = mpp / mpp_m
        x0 = x_l0 * mpp_l0 / mpp_m
        y0 = y_l0 * mpp_l0 / mpp_m
        xs = np.clip((x0 + (np.arange(ancho) + 0.5) * f).astype(int), 0, mapa.shape[1] - 1)
        ys = np.clip((y0 + (np.arange(alto) + 0.5) * f).astype(int), 0, mapa.shape[0] - 1)
        r = mapa[ys][:, xs]
        return r if r.ndim == 3 else r[..., None]
    return np.asarray(i0, np.float64)
