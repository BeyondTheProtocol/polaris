"""tools/panel_vision/sinteticos.py — imágenes SINTÉTICAS con errores SEMBRADOS para calibrar el
panel de visión (plan «laminillas DFCI», actualización 3: «se calibran con capas que llevan errores
sembrados a propósito y cada modelo se usa solo para lo que demuestre detectar»).

NADA REAL: ni píxeles, ni nombres, ni accesiones. Tejido inventado por Beer-Lambert (H&E con el par
de referencia habitual de Macenko; IHQ con hematoxilina y DAB de Ruifrok) con núcleos sintéticos, y
encima la CAPA que el panel revisa. LÍMITE DECLARADO: esto mide si un modelo ve ESTOS errores en ESTE
tejido sintético; no sustituye a sembrar errores en capas reales N1 del piloto (eso lo hace
`siembra_n1.py` sobre la capa pintada, con los mismos colores y las mismas fórmulas; de aquí toma
`pinta_registro`, que es también el contrato de pintado de la capa de registro).

Cinco tareas (F4 y tribunal del plan), cada una con tipos de error de posición y magnitud CONOCIDAS:
  nucleos    máscara de segmentación nuclear (contorno ámbar, 1 px): omitido · fusionado ·
             fuera_de_nucleo. Magnitud: nº de núcleos / pares / contornos afectados.
  ck19       máscara epitelial sobre IHQ CK19 (contorno magenta, 2 px): estroma_incluido ·
             epitelio_excluido. Magnitud: área cambiada, µm².
  registro   superposición de dos cortes seriados (A amarillo, B violeta; campo de 1536 µm):
             desalineacion de un fragmento, 75-160 µm. Los controles llevan ≤20 µm por fragmento.
  figura     figura de informe contra su especificación: etiqueta · escala (factor 0,5-1,8) ·
             leyenda (colores cambiados).
  artefacto  campo H&E con la salida de un detector de artefactos (contorno naranja): pliegue ·
             desenfoque · burbuja SIN marcar. Controles: «limpio» (sin artefacto) y «marcado»
             (artefacto presente y contorneado, que NO es error).

UN error como mucho por imagen, y entero dentro de UN cuadrante (con holgura): el cuadrante es la
verdad de localización. La base (tejido, ruido) sale de flujos de azar propios de la semilla y el
error de otro flujo: la misma semilla sin error da el «gemelo» exacto, y la diferencia entre ambos
queda confinada al cuadrante declarado (lo verifica el test).

COMPATIBLES CON `exporta_n1.verificar_png_n1` (para que el día que `vision_n1` exponga proveedores
externos estas mismas imágenes puedan pasar por N1): ningún color de capa cae en las bandas del
detector de «trazos de rotulador» (verde-cian saturado, azul muy vivo, negro neutro y, desde el
2-oct, rojo muy saturado: por eso ámbar, magenta, naranja, amarillo y violeta, un rojo de S<200 y
gris 50 en vez de negro) y ningún texto lleva ≥4 alfanuméricos seguidos
(«Ki-67», «HER-2», «um», «pos», «neg»; nada de «Ki67» ni «µm»: la fuente por defecto no tiene µ).

Requiere numpy, scipy y Pillow (venv `patologia`). El protocolo (preguntas, puntuación, criterio)
vive en `calibra.py`, que es solo stdlib.
"""
import io
import math
import os
import sys

import numpy as np
from scipy import ndimage as ndi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from calibra import CUADRANTES, TIPOS  # noqa: E402 — calibra es solo stdlib

LADO_MIN, LADO_MAX = 256, 1024
MPP_HE = 0.5                    # µm/px de los campos H&E e IHQ (≈ ×20)
CAMPO_REGISTRO_UM = 1536.0      # el campo de registro mide siempre esto; mpp = campo / lado
MPP_FIGURA = 0.5
ESCALAS_FIGURA_UM = (20, 25, 50, 100)
MARCADORES = ("Ki-67", "RE", "RP", "RA", "HER-2", "CK-19", "p63", "SYP")

COLOR_NUCLEOS = (255, 190, 0)   # ámbar
COLOR_CK19 = (255, 0, 255)      # magenta
COLOR_QC = (255, 110, 0)        # naranja
COLOR_BARRA = (50, 50, 50)      # gris oscuro, nunca negro (negro neutro = «rotulador»)
COLOR_TEXTO = (90, 90, 90)      # su antialias nunca llega a 50: la barra se mide por color
COLOR_POS = (205, 65, 60)       # rojo con saturación <200/255: fuera de la banda «rojo rotulador»
COLOR_NEG = (60, 100, 200)      # azul con saturación <192/255: fuera de la banda «azul vivo»
COLOR_REG_A = np.array([255.0, 200.0, 40.0])   # corte A: amarillo (tono ≈45°, banda libre 15-58°)
COLOR_REG_B = np.array([150.0, 60.0, 210.0])   # corte B: violeta (tono ≈276°, banda libre 260-345°)
# Mezcla por PESOS, no por transmitancias: con transmitancias el solape de dos colores saturados
# sale rojo saturado (S≥200), que el detector de trazos de exporta_n1 cuenta como rotulador rojo
# (5354 px en un 384×384 contra un umbral de 73). Con pesos, el solape es la media de A y B, malva
# pardo con S≈0,38, oscurecida un 25 % como mucho.
PESO_REG = 1.6
OSCURECE_SOLAPE = 0.25

ERROR_REGISTRO_UM = (75.0, 160.0)
JITTER_REGISTRO_UM = 20.0
FACTORES_ESCALA = (0.5, 0.6, 1.5, 1.8)


def _unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


VH = _unit([0.65, 0.70, 0.29])            # Ruifrok: IHQ (hematoxilina + DAB)
VDAB = _unit([0.27, 0.57, 0.78])
VH_HE = _unit([0.5626, 0.7201, 0.4062])   # par H&E de referencia habitual en Macenko: rosa, no magenta
VE_HE = _unit([0.2159, 0.8012, 0.5581])
_K8 = np.ones((3, 3), bool)


# ── utilidades ──────────────────────────────────────────────────────────────────────────────
def _rng(semilla, *flujo):
    return np.random.default_rng([int(semilla) % (2 ** 32)] + [int(f) for f in flujo])


def valida_lado(lado):
    if not isinstance(lado, int) or isinstance(lado, bool) or lado % 2 or not LADO_MIN <= lado <= LADO_MAX:
        raise ValueError("lado %r: tiene que ser un entero par entre %d y %d px" % (lado, LADO_MIN, LADO_MAX))


def _izq_sup(q):
    if q not in CUADRANTES:
        raise ValueError("cuadrante desconocido: %r" % (q,))
    return q.endswith("izquierdo"), q.startswith("superior")


def caja_cuadrante(q, lado, margen=None):
    """(x0, y0, x1, y1): el interior del cuadrante, a `margen` px de los bordes y de las medianas."""
    izq, sup = _izq_sup(q)
    m = margen if margen is not None else max(16, int(round(0.06 * lado)))
    h = lado // 2
    x0, x1 = (m, h - m) if izq else (h + m, lado - m)
    y0, y1 = (m, h - m) if sup else (h + m, lado - m)
    return x0, y0, x1, y1


def mascara_cuadrante(q, lado):
    izq, sup = _izq_sup(q)
    m = np.zeros((lado, lado), bool)
    h = lado // 2
    m[(slice(0, h) if sup else slice(h, lado)), (slice(0, h) if izq else slice(h, lado))] = True
    return m


def cuadrante_de(x, y, lado):
    h = lado / 2.0
    return ("superior_" if y < h else "inferior_") + ("izquierdo" if x < h else "derecho")


def _bbox(m):
    ys, xs = np.nonzero(m)
    if not len(ys):
        return None
    return int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1


def _bbox_en_cuadrante(bb, q, lado, holgura=3, abajo_libre=0):
    """¿La caja (y0, y1, x0, x1) cabe en el cuadrante con `holgura` px a cada lado?"""
    if bb is None:
        return False
    izq, sup = _izq_sup(q)
    h = lado // 2
    y0, y1, x0, x1 = bb
    qx0, qx1 = (0, h) if izq else (h, lado)
    qy0, qy1 = (0, h) if sup else (h, lado - abajo_libre)
    return x0 >= qx0 + holgura and x1 <= qx1 - holgura and y0 >= qy0 + holgura and y1 <= qy1 - holgura


def _suave(rng, forma, sigma):
    r = ndi.gaussian_filter(rng.standard_normal(forma), sigma, mode="wrap")
    s = r.std()
    return (r - r.mean()) / (s if s > 0 else 1.0)


def _fibras(rng, forma, angulo, largo, corto):
    """Ruido estirado (fibras de colágeno). Con `ndi.rotate`, una fibra horizontal acaba con ángulo
    -`angulo` en coordenadas de imagen (y hacia abajo): medido, no supuesto."""
    n = int(max(forma) * 1.5)
    r = ndi.gaussian_filter(rng.standard_normal((n, n)), (corto, largo), mode="wrap")
    r = ndi.rotate(r, angulo, reshape=False, order=1, mode="wrap")
    y0, x0 = (n - forma[0]) // 2, (n - forma[1]) // 2
    r = r[y0:y0 + forma[0], x0:x0 + forma[1]]
    return (r - r.mean()) / (r.std() or 1.0)


def _od_a_rgb(od, rng=None, sigma=1.5):
    rgb = 255.0 * np.power(10.0, -od)
    if rng is not None:
        rgb = rgb + rng.normal(0.0, sigma, rgb.shape)
    return np.clip(np.rint(rgb), 0, 255).astype(np.uint8)


def _borde(m):
    """Borde interior de 1 px (8-vecindad); el borde de la imagen cuenta como fuera."""
    return m & ~ndi.binary_erosion(m, structure=_K8, border_value=0)


def _trazo(m, grosor=2):
    b = _borde(m)
    if grosor >= 2:
        b |= ndi.binary_dilation(m, structure=_K8) & ~m
    return b


def _disco(r):
    k = int(math.ceil(r))
    yy, xx = np.mgrid[-k:k + 1, -k:k + 1]
    return (xx * xx + yy * yy) <= r * r


def _elipse_ventana(cx, cy, a, b, th, forma, extra=2):
    """(y0, y1, x0, x1, máscara local) de la elipse; None si se sale de la imagen."""
    H, W = forma
    r = int(math.ceil(max(a, b))) + extra
    x0, x1, y0, y1 = int(cx) - r, int(cx) + r + 1, int(cy) - r, int(cy) + r + 1
    if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
        return None
    yy, xx = np.mgrid[y0:y1, x0:x1]
    dx, dy = xx - cx, yy - cy
    c, s = math.cos(th), math.sin(th)
    uu = (dx * c + dy * s) / a
    vv = (-dx * s + dy * c) / b
    return y0, y1, x0, x1, (uu * uu + vv * vv) <= 1.0


def _pinta_elipse(m, cx, cy, a, b, th):
    v = _elipse_ventana(cx, cy, a, b, th, m.shape, extra=1)
    if v is None:
        H, W = m.shape
        yy, xx = np.mgrid[0:H, 0:W]
        c, s = math.cos(th), math.sin(th)
        uu = ((xx - cx) * c + (yy - cy) * s) / a
        vv = (-(xx - cx) * s + (yy - cy) * c) / b
        m |= (uu * uu + vv * vv) <= 1.0
        return
    y0, y1, x0, x1, loc = v
    m[y0:y1, x0:x1] |= loc


def _mayor_componente(m):
    lab, n = ndi.label(m)
    if n <= 1:
        return m
    tam = ndi.sum(m, lab, index=np.arange(1, n + 1))
    return lab == (int(np.argmax(tam)) + 1)


class _Nucleos:
    """Etiquetas de núcleos SIN contacto entre ellos (un píxel de hueco en 8-vecindad como mínimo):
    así cada contorno es una componente y los recuentos del test son exactos."""

    def __init__(self, forma, prohibido=None):
        self.lab = np.zeros(forma, np.int32)
        self.lista = [None]
        self.prohibido = prohibido

    def nuevo(self, cx, cy, a, b, th, clase, base):
        v = _elipse_ventana(cx, cy, a, b, th, self.lab.shape)
        if v is None:
            return None
        y0, y1, x0, x1, m = v
        if m.sum() < 8:
            return None
        win = self.lab[y0:y1, x0:x1]
        if (win[ndi.binary_dilation(m, structure=_K8)] != 0).any():
            return None
        if self.prohibido is not None and self.prohibido[y0:y1, x0:x1][m].mean() > 0.1:
            return None
        nid = len(self.lista)
        win[m] = nid
        bb = _bbox(m)
        self.lista.append({"id": nid, "cx": float(cx), "cy": float(cy), "clase": clase, "base": float(base),
                           "bbox": (bb[0] + y0, bb[1] + y0, bb[2] + x0, bb[3] + x0)})
        return nid

    def quita(self, nid):
        n = self.lista[nid]
        y0, y1, x0, x1 = n["bbox"]
        win = self.lab[y0:y1, x0:x1]
        win[win == nid] = 0
        self.lista[nid] = None

    def vivos(self):
        return [n for n in self.lista if n]

    def od_hematoxilina(self, rng, granos_sigma):
        """Concentración de H en los núcleos: base por núcleo, cromatina granular y borde algo más
        oscuro (membrana nuclear)."""
        base = np.zeros(len(self.lista))
        for n in self.vivos():
            base[n["id"]] = n["base"]
        dentro = self.lab > 0
        c = base[self.lab] * (1.0 + 0.2 * _suave(rng, self.lab.shape, granos_sigma))
        c[_borde(dentro)] *= 1.15
        return np.where(dentro, c, 0.0), dentro


def _rellena_nucleos(nuc, rng, lado, en_nido, huecos, ang_fibra, p_estroma, u, base_ep, base_st):
    """Núcleos epiteliales (redondos, densos) en `en_nido` y estromales (fusiformes, dispersos)."""
    K = int(lado * lado * 0.004)
    xs, ys, us = rng.uniform(0, lado, K), rng.uniform(0, lado, K), rng.random(K)
    a_ep, r_ep, t_ep = rng.uniform(3.5, 5.5, K) * u, rng.uniform(0.72, 0.95, K), rng.uniform(0, math.pi, K)
    a_st, b_st, d_st = rng.uniform(5.0, 8.0, K) * u, rng.uniform(1.4, 2.2, K) * u, rng.normal(0, 0.2, K)
    b_e, b_s = rng.uniform(*base_ep, K), rng.uniform(*base_st, K)
    for i in range(K):
        iy, ix = int(ys[i]), int(xs[i])
        if huecos is not None and huecos[iy, ix]:
            continue
        if en_nido[iy, ix]:
            nuc.nuevo(xs[i], ys[i], a_ep[i], a_ep[i] * r_ep[i], t_ep[i], "epitelial", b_e[i])
        elif us[i] < p_estroma:
            nuc.nuevo(xs[i], ys[i], a_st[i], b_st[i], ang_fibra + d_st[i], "estromal", b_s[i])


# ── tejidos de fondo ───────────────────────────────────────────────────────────────────────
def fondo_he(semilla, lado, n_pares=4):
    """Campo H&E a MPP_HE: estroma con fibras, luces/adipocitos, nidos epiteliales y núcleos. En
    cada cuadrante, `n_pares` pares de núcleos casi en contacto (1-3 px de hueco), en errores Y en
    controles: sin ellos, «hay pares pegados» delataría el error «fusionado»."""
    rng = _rng(semilla, 1)
    H = W = lado
    u = 1.0 / MPP_HE
    ang = float(rng.uniform(0, 180))
    ang_fibra = -math.radians(ang)
    fib = 0.65 * _fibras(rng, (H, W), ang, largo=9 * u, corto=1.0 * u) + \
        0.45 * _fibras(rng, (H, W), ang + 22, largo=5 * u, corto=1.2 * u)
    suave = _suave(rng, (H, W), 20 * u)
    huecos = ndi.binary_opening(_suave(rng, (H, W), 9 * u) > 1.7, iterations=2)
    nidos = (_suave(rng, (H, W), 22 * u) > 0.75) & ~ndi.binary_dilation(huecos, iterations=3)
    fino = _suave(rng, (H, W), 1.5 * u)
    cE = np.where(nidos, 0.21 + 0.03 * fino, 0.19 + 0.035 * fib + 0.03 * suave)
    cH = np.where(nidos, 0.10 + 0.02 * fino, 0.045 + 0.012 * suave)

    nuc = _Nucleos((H, W), prohibido=huecos)
    pares = []
    for q in CUADRANTES:
        x0, y0, x1, y1 = caja_cuadrante(q, lado)
        hechos = intentos = 0
        while hechos < n_pares and intentos < 400:
            intentos += 1
            cx, cy = rng.uniform(x0, x1), rng.uniform(y0, y1)
            phi = rng.uniform(0, math.pi)
            ux, uy = math.cos(phi), math.sin(phi)
            a1, a2 = rng.uniform(4.0, 5.5) * u, rng.uniform(4.0, 5.5) * u
            b1, b2 = a1 * rng.uniform(0.7, 0.85), a2 * rng.uniform(0.7, 0.85)
            base1, base2 = rng.uniform(0.6, 0.95), rng.uniform(0.6, 0.95)
            th = phi + math.pi / 2          # se tocan por el lado ancho
            n1 = nuc.nuevo(cx - ux * (b1 + 1.0), cy - uy * (b1 + 1.0), a1, b1, th, "par", base1)
            if n1 is None:
                continue
            n2 = None
            for g in (1.0, 1.5, 2.0, 2.5, 3.0):
                n2 = nuc.nuevo(cx + ux * (b2 + g), cy + uy * (b2 + g), a2, b2, th, "par", base2)
                if n2:
                    break
            if n2 is None or not all(_bbox_en_cuadrante(nuc.lista[i]["bbox"], q, lado, 4) for i in (n1, n2)):
                nuc.quita(n1)
                if n2:
                    nuc.quita(n2)
                continue
            pares.append({"ids": (n1, n2), "cuadrante": q})
            hechos += 1
    _rellena_nucleos(nuc, rng, lado, nidos, huecos, ang_fibra, 0.10, u, (0.55, 0.95), (0.7, 1.0))
    cHn, dentro = nuc.od_hematoxilina(rng, 0.7 * u)
    cH = np.where(dentro, cHn + 0.05, cH)
    cE = np.where(dentro, 0.07, cE)
    cH[huecos] = 0.0
    cE[huecos] = 0.0
    od = cH[..., None] * VH_HE + cE[..., None] * VE_HE + 0.015
    od = ndi.gaussian_filter(od, sigma=(0.6, 0.6, 0))
    return {"od": od, "lab": nuc.lab, "nucleos": nuc.vivos(), "pares": pares, "huecos": huecos,
            "nidos": nidos, "mpp": MPP_HE}


def fondo_ihc(semilla, lado, mpp=MPP_HE, nidos_por_cuadrante=(2, 4)):
    """Campo de IHQ (DAB + hematoxilina) a `mpp`: estroma azul pálido y nidos epiteliales DAB+,
    2-3 por cuadrante, cada uno entero dentro de su cuadrante y separados ≥10 px entre sí."""
    rng = _rng(semilla, 1)
    H = W = lado
    u = 1.0 / mpp
    ang = float(rng.uniform(0, 180))
    fib = _fibras(rng, (H, W), ang, largo=10 * u, corto=1.0 * u)
    suave = _suave(rng, (H, W), 20 * u)
    fino = _suave(rng, (H, W), 1.2 * u)
    cH = 0.035 + 0.012 * fib + 0.008 * suave
    nidos, ocup = [], np.zeros((H, W), bool)
    for q in CUADRANTES:
        x0, y0, x1, y1 = caja_cuadrante(q, lado)
        n_obj = int(rng.integers(*nidos_por_cuadrante))
        hechos = intentos = 0
        while hechos < n_obj and intentos < 200:
            intentos += 1
            R = rng.uniform(0.035, 0.06) * lado
            cx, cy = rng.uniform(x0, x1), rng.uniform(y0, y1)
            m = np.zeros((H, W), bool)
            for _ in range(int(rng.integers(2, 5))):
                a = R * rng.uniform(0.5, 1.0)
                _pinta_elipse(m, cx + rng.normal(0, 0.45 * R), cy + rng.normal(0, 0.45 * R), a,
                              a * rng.uniform(0.5, 0.9), rng.uniform(0, math.pi))
            m = ndi.binary_fill_holes((ndi.gaussian_filter(m.astype(float), 2.0) + 0.12 * fino) > 0.5)
            m = _mayor_componente(m)
            if m.sum() < 0.3 * math.pi * R * R * 0.3:
                continue
            if not _bbox_en_cuadrante(_bbox(m), q, lado, holgura=6):
                continue
            if (ndi.binary_dilation(m, iterations=10) & ocup).any():
                continue
            ocup |= m
            nidos.append({"cuadrante": q, "mascara": m, "area_px": int(m.sum())})
            hechos += 1
    epitelio = ocup
    cD = np.where(epitelio, np.clip(0.60 + 0.12 * _suave(rng, (H, W), 2.5 * u) + 0.06 * fino, 0.15, None), 0.0)
    cH = np.where(epitelio, 0.06, cH)
    nuc = _Nucleos((H, W))
    _rellena_nucleos(nuc, rng, lado, epitelio, None, -math.radians(ang), 0.08, u, (0.45, 0.75), (0.55, 0.8))
    cHn, dentro = nuc.od_hematoxilina(rng, 0.7 * u)
    cH = np.where(dentro, cHn + 0.04, cH)
    cD = np.where(dentro, 0.04, cD)
    od = cH[..., None] * VH + cD[..., None] * VDAB + 0.012
    od = ndi.gaussian_filter(od, sigma=(0.6, 0.6, 0))
    return {"od": od, "lab": nuc.lab, "nucleos": nuc.vivos(), "nidos": nidos, "epitelio": epitelio, "mpp": mpp}


# ── (a) máscara nuclear ────────────────────────────────────────────────────────────────────
def caso_nucleos(semilla, lado, tipo=None, cuadrante=None):
    b = fondo_he(semilla, lado)
    lab, nuc = b["lab"], b["nucleos"]
    u = 1.0 / MPP_HE
    sin_contorno, detalle = set(), {}
    extra = np.zeros(lab.shape, bool)
    falsos = np.zeros(lab.shape, bool)
    magnitud = unidad = None
    if tipo:
        rng = _rng(semilla, 2)
        x0, y0, x1, y1 = caja_cuadrante(cuadrante, lado)
        if tipo == "omitido":
            k = int(rng.integers(4, 11))
            ax, ay = rng.uniform(x0, x1), rng.uniform(y0, y1)
            cand = [n for n in nuc if _bbox_en_cuadrante(n["bbox"], cuadrante, lado, 4)]
            cand.sort(key=lambda n: (n["cx"] - ax) ** 2 + (n["cy"] - ay) ** 2)
            if len(cand) < k:
                raise RuntimeError("caso_nucleos(%d): %d núcleos en %s, hacen falta %d" % (semilla, len(cand), cuadrante, k))
            sin_contorno = {n["id"] for n in cand[:k]}
            magnitud, unidad = k, "nucleos"
        elif tipo == "fusionado":
            k = int(rng.integers(2, 5))
            pq = [p for p in b["pares"] if p["cuadrante"] == cuadrante]
            if len(pq) < k:
                raise RuntimeError("caso_nucleos(%d): %d pares en %s, hacen falta %d" % (semilla, len(pq), cuadrante, k))
            todos = lab > 0
            for i in sorted(int(j) for j in rng.choice(len(pq), size=k, replace=False)):
                i1, i2 = pq[i]["ids"]
                par = (lab == i1) | (lab == i2)
                cerrado = ndi.binary_closing(par, structure=_disco(3.0)) | par
                vecinos = ndi.binary_dilation(todos & ~par, structure=_K8)
                extra |= ndi.binary_fill_holes(cerrado) & ~vecinos
                sin_contorno |= {i1, i2}
            magnitud, unidad = k, "pares"
        elif tipo == "fuera_de_nucleo":
            k = int(rng.integers(3, 7))
            ocupado = ndi.binary_dilation(lab > 0, iterations=3) | b["huecos"]
            puestos = intentos = 0
            while puestos < k and intentos < 3000:
                intentos += 1
                a = rng.uniform(4.0, 5.5) * u
                v = _elipse_ventana(rng.uniform(x0, x1), rng.uniform(y0, y1), a, a * rng.uniform(0.7, 0.9),
                                    rng.uniform(0, math.pi), lab.shape, extra=4)
                if v is None:
                    continue
                vy0, vy1, vx0, vx1, m = v
                bb = _bbox(m)
                if not _bbox_en_cuadrante((bb[0] + vy0, bb[1] + vy0, bb[2] + vx0, bb[3] + vx0), cuadrante, lado, 4):
                    continue
                if (ocupado[vy0:vy1, vx0:vx1] & ndi.binary_dilation(m, iterations=2)).any():
                    continue
                falsos[vy0:vy1, vx0:vx1] |= m
                ocupado[vy0:vy1, vx0:vx1] |= ndi.binary_dilation(m, iterations=3)
                puestos += 1
            if puestos < k:
                raise RuntimeError("caso_nucleos(%d): sin sitio para %d contornos falsos en %s" % (semilla, k, cuadrante))
            magnitud, unidad = k, "contornos"
    dibujados = (lab > 0) & ~np.isin(lab, sorted(sin_contorno))
    contorno = _borde(dibujados) | _borde(extra) | _borde(falsos)
    rgb = _od_a_rgb(b["od"], _rng(semilla, 9))
    rgb[contorno] = COLOR_NUCLEOS
    detalle["n_nucleos"] = len(nuc)
    capas = {"lab": lab, "contorno": contorno, "extra": extra, "falsos": falsos,
             "sin_contorno": sorted(sin_contorno), "pares": b["pares"]}
    return rgb, magnitud, unidad, detalle, {}, capas


# ── (b) máscara epitelial CK19 ─────────────────────────────────────────────────────────────
def caso_ck19(semilla, lado, tipo=None, cuadrante=None):
    b = fondo_ihc(semilla, lado)
    nidos, epitelio = b["nidos"], b["epitelio"]
    rj = _rng(semilla, 3)
    partes = []
    for n in nidos:                 # borde imperfecto ±1 px también en los controles
        op = int(rj.integers(0, 3))
        m = n["mascara"]
        partes.append(ndi.binary_dilation(m) if op == 1 else ndi.binary_erosion(m) if op == 2 else m)
    control = np.zeros_like(epitelio)
    for m in partes:
        control |= m
    mascara = control.copy()
    magnitud = unidad = None
    detalle = {"n_nidos": len(nidos)}
    if tipo:
        rng = _rng(semilla, 2)
        cand = [i for i, n in enumerate(nidos) if n["cuadrante"] == cuadrante]
        if tipo == "epitelio_excluido":
            if not cand:
                raise RuntimeError("caso_ck19(%d): ningún nido en %s" % (semilla, cuadrante))
            i = cand[int(rng.integers(len(cand)))]
            mascara &= ~partes[i]
        else:
            x0, y0, x1, y1 = caja_cuadrante(cuadrante, lado)
            lejos = ndi.distance_transform_edt(~epitelio)
            fino = _suave(rng, epitelio.shape, 2.0)
            for _ in range(400):
                R = rng.uniform(0.03, 0.05) * lado
                adyacente = bool(cand) and rng.random() < 0.5
                if adyacente:
                    i = cand[int(rng.integers(len(cand)))]
                    bordes = np.argwhere(_borde(nidos[i]["mascara"]))
                    py, px = bordes[int(rng.integers(len(bordes)))]
                    cy_n, cx_n = ndi.center_of_mass(nidos[i]["mascara"])
                    d = math.hypot(px - cx_n, py - cy_n) or 1.0
                    cx, cy = px + (px - cx_n) / d * 0.55 * R, py + (py - cy_n) / d * 0.55 * R
                    otros = epitelio & ~nidos[i]["mascara"]
                else:
                    cx, cy = rng.uniform(x0, x1), rng.uniform(y0, y1)
                    if lejos[int(cy), int(cx)] < R + 8:
                        continue
                    otros = epitelio
                blob = np.zeros(epitelio.shape, bool)
                _pinta_elipse(blob, cx, cy, R, R * rng.uniform(0.6, 0.9), rng.uniform(0, math.pi))
                _pinta_elipse(blob, cx + rng.normal(0, 0.3 * R), cy + rng.normal(0, 0.3 * R), 0.7 * R,
                              0.5 * R, rng.uniform(0, math.pi))
                blob = (ndi.gaussian_filter(blob.astype(float), 1.5) + 0.1 * fino) > 0.5
                if not _bbox_en_cuadrante(_bbox(blob), cuadrante, lado, 6):
                    continue
                if (blob & ndi.binary_dilation(otros, iterations=6)).any():
                    continue
                if (blob & ~control).sum() < 0.5 * blob.sum():
                    continue
                detalle["variante_estroma"] = "adyacente" if adyacente else "aislado"
                break
            else:
                raise RuntimeError("caso_ck19(%d): sin sitio para estroma en %s" % (semilla, cuadrante))
            mascara |= blob
        cambio = mascara ^ control
        magnitud, unidad = round(float(cambio.sum()) * MPP_HE * MPP_HE, 1), "um2"
    contorno = _trazo(mascara, 2)
    rgb = _od_a_rgb(b["od"], _rng(semilla, 9))
    rgb[contorno] = COLOR_CK19
    capas = {"mascara": mascara, "control": control, "epitelio": epitelio, "contorno": contorno}
    return rgb, magnitud, unidad, detalle, {}, capas


# ── (c) registro entre dos cortes ──────────────────────────────────────────────────────────
def pinta_registro(wA, wB):
    """RGB (float, sin ruido ni redondeo) de la superposición con pesos `wA` (corte A, amarillo) y
    `wB` (corte B, violeta) en [0, 1]: lo que describe la pregunta de registro. Es también el
    CONTRATO de pintado de la capa de registro del piloto: `siembra_n1` la invierte con esta misma
    fórmula para desplazar un fragmento de B."""
    wA, wB = np.asarray(wA, float), np.asarray(wB, float)
    suma = wA + wB
    norma = np.maximum(suma, 1.0)
    rgb = (255.0 * np.clip(1.0 - suma, 0, None)[..., None] + wA[..., None] * COLOR_REG_A
           + wB[..., None] * COLOR_REG_B) / norma[..., None]
    rgb *= (1.0 - OSCURECE_SOLAPE * np.minimum(wA, wB))[..., None]
    return rgb


def caso_registro(semilla, lado, tipo=None, cuadrante=None):
    mpp = CAMPO_REGISTRO_UM / lado
    u = 1.0 / mpp
    H = W = lado
    rng = _rng(semilla, 1)
    yy, xx = np.mgrid[0:H, 0:W].astype(float)
    rugo = _suave(rng, (H, W), 0.035 * lado)
    interior = _suave(rng, (H, W), 0.02 * lado)
    luces = _suave(rng, (H, W), 0.012 * lado)
    barra_y = lado - int(round(0.03 * lado))
    libre = lado - barra_y + 8                     # las cajas de abajo no pisan la barra
    frags = []
    for q in CUADRANTES:
        izq, sup = _izq_sup(q)
        qx, qy = lado * (0.25 if izq else 0.75), lado * (0.25 if sup else 0.75)
        for _ in range(100):
            cx, cy = qx + rng.uniform(-0.02, 0.02) * lado, qy + rng.uniform(-0.02, 0.02) * lado
            R = rng.uniform(0.07, 0.09) * lado
            m = ndi.binary_fill_holes(_mayor_componente(np.hypot(xx - cx, yy - cy) / R + 0.22 * rugo < 1.0))
            if _bbox_en_cuadrante(_bbox(m), q, lado, 8, abajo_libre=0 if sup else libre):
                break
        else:
            raise RuntimeError("caso_registro(%d): el fragmento de %s no cabe" % (semilla, q))
        dens = np.where(m, 0.45 + 0.12 * interior, 0.0)
        dens[m & (interior > 0.8)] += 0.3
        dens[m & (luces > 1.5)] = 0.08
        frags.append({"cuadrante": q, "dens": ndi.gaussian_filter(dens, 0.8), "mascara": m})
    rb = _rng(semilla, 2)
    offs = []
    for _f in frags:
        r_um, a = rb.uniform(0, JITTER_REGISTRO_UM), rb.uniform(0, 2 * math.pi)
        offs.append((r_um * u * math.cos(a), r_um * u * math.sin(a)))
    magnitud = unidad = None
    if tipo:
        re_ = _rng(semilla, 3)
        i = CUADRANTES.index(cuadrante)
        _izq, sup = _izq_sup(cuadrante)
        y0, y1, x0, x1 = _bbox(frags[i]["mascara"])
        for _ in range(500):
            d_um, a = re_.uniform(*ERROR_REGISTRO_UM), re_.uniform(0, 2 * math.pi)
            dx, dy = d_um * u * math.cos(a), d_um * u * math.sin(a)
            bb = (int(math.floor(y0 + dy)) - 2, int(math.ceil(y1 + dy)) + 2,
                  int(math.floor(x0 + dx)) - 2, int(math.ceil(x1 + dx)) + 2)
            if _bbox_en_cuadrante(bb, cuadrante, lado, 6, abajo_libre=0 if sup else libre):
                break
        else:
            raise RuntimeError("caso_registro(%d): el fragmento de %s no cabe desplazado" % (semilla, cuadrante))
        offs[i] = (dx, dy)
        magnitud, unidad = round(d_um, 1), "um"
    A = sum(f["dens"] for f in frags)
    B = sum(ndi.shift(f["dens"], (dy, dx), order=1, mode="constant", cval=0.0)
            for f, (dx, dy) in zip(frags, offs))
    rw = _rng(semilla, 4)                          # deformación elástica pequeña (≤1,5 µm)
    amp = 1.5 * u
    B = ndi.map_coordinates(B, [yy + amp * np.tanh(_suave(rw, (H, W), 0.05 * lado) / 2),
                                xx + amp * np.tanh(_suave(rw, (H, W), 0.05 * lado) / 2)],
                            order=1, mode="constant", cval=0.0)
    A = np.clip(A * (1 + 0.12 * _suave(_rng(semilla, 5), (H, W), 1.0)), 0, 1)
    B = np.clip(B * (1 + 0.12 * _suave(_rng(semilla, 6), (H, W), 1.0)), 0, 1)
    rgb = pinta_registro(np.clip(PESO_REG * A, 0, 1), np.clip(PESO_REG * B, 0, 1))
    rgb = np.clip(np.rint(rgb + _rng(semilla, 9).normal(0, 1.0, rgb.shape)), 0, 255).astype(np.uint8)
    largo = int(round(100.0 / mpp))
    grueso = max(3, lado // 160)
    rgb[barra_y - grueso:barra_y, lado // 2 - largo // 2:lado // 2 - largo // 2 + largo] = COLOR_BARRA
    offs_um = {q: round(math.hypot(dx, dy) * mpp, 2) for q, (dx, dy) in zip(CUADRANTES, offs)}
    detalle = {"mpp": round(mpp, 4), "desplazamiento_um": offs_um}
    capas = {"A": A, "B": B, "offsets_px": offs, "mascaras": [f["mascara"] for f in frags], "barra_px": largo}
    return rgb, magnitud, unidad, detalle, {"mpp": round(mpp, 4)}, capas


# ── (d) figura del informe ─────────────────────────────────────────────────────────────────
def _fuente(tam):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=tam)
    except TypeError:                                   # Pillow < 10.1: sin tamaño
        return ImageFont.load_default()


def _caja_esquina(q, lado):
    izq, sup = _izq_sup(q)
    x0, x1 = (0.04, 0.46) if izq else (0.54, 0.96)
    y0, y1 = (0.03, 0.22) if sup else (0.78, 0.97)
    return tuple(int(round(v * lado)) for v in (x0, y0, x1, y1))


def caso_figura(semilla, lado, tipo=None, cuadrante=None, disposicion=None):
    from PIL import Image, ImageDraw
    rng = _rng(semilla, 1)
    P = (lado // 2) // 2 * 2
    base = fondo_ihc(semilla, max(P, 128), nidos_por_cuadrante=(1, 3))
    panel = _od_a_rgb(base["od"], _rng(semilla, 9))[:P, :P]
    img = Image.new("RGB", (lado, lado), (255, 255, 255))
    img.paste(Image.fromarray(panel, "RGB"), (lado // 4, lado // 4))
    d = ImageDraw.Draw(img)
    rpt = max(2, lado // 200)
    for n in base["nucleos"]:
        if n["cx"] >= P or n["cy"] >= P:
            continue
        pos = rng.random() < (0.8 if n["clase"] == "epitelial" else 0.1)
        x, y = lado // 4 + n["cx"], lado // 4 + n["cy"]
        d.ellipse((x - rpt, y - rpt, x + rpt, y + rpt), fill=COLOR_POS if pos else COLOR_NEG)
    marcador = MARCADORES[int(rng.integers(len(MARCADORES)))]
    validas = [L for L in ESCALAS_FIGURA_UM if 0.08 * lado <= L / MPP_FIGURA <= 0.2 * lado] or [ESCALAS_FIGURA_UM[0]]
    L = validas[int(rng.integers(len(validas)))]
    elementos = ("etiqueta", "escala", "leyenda")
    if disposicion is None:
        perm = [CUADRANTES[int(i)] for i in rng.permutation(4)]
        disposicion = dict(zip(elementos, perm[:3]))
        if tipo:                                       # el elemento erróneo va al cuadrante pedido
            otro = next((e for e, q in disposicion.items() if q == cuadrante), None)
            if otro:
                disposicion[otro] = disposicion[tipo]
            disposicion[tipo] = cuadrante
    else:
        rng.permutation(4)                             # mismo consumo de azar que sin disposición
    etiqueta, factor, colores = marcador, 1.0, (COLOR_POS, COLOR_NEG)
    magnitud = unidad = None
    detalle = {"disposicion": dict(disposicion)}
    if tipo:
        re_ = _rng(semilla, 2)
        if tipo == "etiqueta":
            otros = [m for m in MARCADORES if m != marcador]
            etiqueta = otros[int(re_.integers(len(otros)))]
            magnitud, unidad = 1, "elemento"
            detalle["etiqueta_dibujada"] = etiqueta
        elif tipo == "escala":
            factor = FACTORES_ESCALA[int(re_.integers(len(FACTORES_ESCALA)))]
            magnitud, unidad = factor, "factor"
        else:
            colores = (COLOR_NEG, COLOR_POS)
            magnitud, unidad = 1, "elemento"
    fs = max(12, int(round(0.045 * lado)))
    fuente = _fuente(fs)
    cajas = {}
    barra = None
    for elem, q in disposicion.items():
        x0, y0, x1, y1 = _caja_esquina(q, lado)
        cajas[elem] = (x0, y0, x1, y1)
        if elem == "etiqueta":
            d.text((x0, y0), etiqueta, fill=COLOR_TEXTO, font=fuente)
        elif elem == "escala":
            largo = int(round(L / MPP_FIGURA * factor))
            grueso = max(3, lado // 128)
            by = y0 + fs // 2
            d.rectangle((x0, by, x0 + largo - 1, by + grueso - 1), fill=COLOR_BARRA)
            d.text((x0, by + grueso + 4), "%d um" % L, fill=COLOR_TEXTO, font=fuente)
            barra = {"x0": x0, "largo_px": largo, "y": by, "grueso": grueso}
        else:
            for k, (col, txt) in enumerate(zip(colores, ("pos", "neg"))):
                ry = y0 + k * (fs + 6)
                d.rectangle((x0, ry, x0 + fs - 1, ry + fs - 1), fill=col)
                d.text((x0 + fs + 6, ry), txt, fill=COLOR_TEXTO, font=fuente)
    rgb = np.array(img)
    contexto = {"marcador": marcador, "mpp": MPP_FIGURA, "escala_um": L,
                "escala_px": int(round(L / MPP_FIGURA))}
    capas = {"cajas": cajas, "barra": barra, "factor": factor}
    return rgb, magnitud, unidad, detalle, contexto, capas


# ── (e) artefacto sin marcar ───────────────────────────────────────────────────────────────
def caso_artefacto(semilla, lado, tipo=None, cuadrante=None, marcado=False):
    b = fondo_he(semilla, lado)
    od = b["od"].copy()
    H = W = lado
    zona = np.zeros((H, W), bool)
    magnitud = unidad = None
    detalle = {}
    if tipo:
        rng = _rng(semilla, 2)
        u = 1.0 / MPP_HE
        x0, y0, x1, y1 = caja_cuadrante(cuadrante, lado)
        yy, xx = np.mgrid[0:H, 0:W].astype(float)
        for _ in range(500):
            if tipo == "pliegue":
                w = rng.uniform(15, 35) * u
                p0 = np.array([rng.uniform(x0, x1), rng.uniform(y0, y1)])
                p1 = np.array([rng.uniform(x0, x1), rng.uniform(y0, y1)])
                lon = float(np.linalg.norm(p1 - p0))
                if lon < 0.6 * (x1 - x0):
                    continue
                nrm = np.array([-(p1 - p0)[1], (p1 - p0)[0]]) / lon
                t = np.linspace(0, 1, int(4 * lon))
                pts = p0[None] + t[:, None] * (p1 - p0)[None] + nrm[None] * (rng.uniform(-0.15, 0.15) * lon
                                                                          * np.sin(math.pi * t))[:, None]
                linea = np.zeros((H, W), bool)
                ix, iy = np.clip(np.rint(pts[:, 0]).astype(int), 0, W - 1), np.clip(np.rint(pts[:, 1]).astype(int), 0, H - 1)
                linea[iy, ix] = True
                dist = ndi.distance_transform_edt(~linea)
                cand = dist <= w / 2
                holg = 8
            elif tipo == "desenfoque":
                ra = rng.uniform(0.07, 0.11) * lado
                cand = np.zeros((H, W), bool)
                _pinta_elipse(cand, rng.uniform(x0, x1), rng.uniform(y0, y1), ra, ra * rng.uniform(0.6, 1.0),
                              rng.uniform(0, math.pi))
                holg = 20
            else:
                r = rng.uniform(0.05, 0.09) * lado
                cx, cy = rng.uniform(x0, x1), rng.uniform(y0, y1)
                dist = np.hypot(xx - cx, yy - cy)
                cand = dist <= r + 2
                holg = 14
            if _bbox_en_cuadrante(_bbox(cand), cuadrante, lado, holg):
                zona = cand
                break
        else:
            raise RuntimeError("caso_artefacto(%d): el %s no cabe en %s" % (semilla, tipo, cuadrante))
        if tipo == "pliegue":
            capa = ndi.shift(od, (nrm[1] * w, nrm[0] * w, 0), order=1, mode="nearest")
            od = np.where(zona[..., None], od + 0.9 * capa, od)
            od[zona & (dist > w / 2 - 1.5)] += 0.12          # el canto del pliegue
            magnitud, unidad = round(w * MPP_HE, 1), "um_ancho"
        elif tipo == "desenfoque":
            sigma = rng.uniform(2.5, 4.5)
            alfa = ndi.gaussian_filter(zona.astype(float), 4.0) * ndi.binary_dilation(zona, iterations=14)
            od = od * (1 - alfa[..., None]) + ndi.gaussian_filter(od, (sigma, sigma, 0)) * alfa[..., None]
            magnitud, unidad = round(sigma * MPP_HE, 2), "um_sigma"
        else:
            dentro = dist < r
            od = np.where(dentro[..., None], 0.75 * ndi.gaussian_filter(od, (1.2, 1.2, 0)), od)
            anillo = np.exp(-((dist - r) / 1.8) ** 2) * (np.abs(dist - r) < 9)
            luz = rng.uniform(0, 2 * math.pi)
            lado_luz = np.cos(np.arctan2(yy - cy, xx - cx) - luz) > 0.3
            brillo = np.exp(-((dist - 0.82 * r) / 2.5) ** 2) * dentro * lado_luz
            od = np.clip(od + 0.5 * anillo[..., None] - 0.08 * brillo[..., None], 0.0, None)
            magnitud, unidad = round(r * MPP_HE, 1), "um_radio"
        od = np.minimum(od, 1.6)
        detalle["artefacto"] = tipo
    rgb = _od_a_rgb(od, _rng(semilla, 9))
    contorno = np.zeros((H, W), bool)
    if tipo and marcado:
        contorno = _trazo(ndi.binary_dilation(zona, iterations=4), 2)
        rgb[contorno] = COLOR_QC
    capas = {"zona": zona, "contorno": contorno}
    return rgb, magnitud, unidad, detalle, {}, capas


_CASOS = {"nucleos": caso_nucleos, "ck19": caso_ck19, "registro": caso_registro, "figura": caso_figura,
          "artefacto": caso_artefacto}


def genera_caso(tarea, semilla, lado=768, tipo=None, cuadrante=None, variante=None, **extra):
    """Un caso: {"rgb", "verdad", "contexto", "capas"}.

    `tipo`+`cuadrante` siembran el error; sin ellos es un control. `variante="marcado"` (solo
    artefacto) pone el artefacto `tipo` en `cuadrante` CONTORNEADO: control, no error.
    `verdad` nunca va al modelo; `contexto` sí (lo que la pregunta necesita: mpp, especificación)."""
    valida_lado(lado)
    if tarea not in _CASOS:
        raise ValueError("tarea desconocida: %r" % (tarea,))
    if tipo is not None and tipo not in TIPOS[tarea]:
        raise ValueError("tipo %r no es de %s" % (tipo, tarea))
    if (tipo is None) != (cuadrante is None):
        raise ValueError("tipo y cuadrante van juntos")
    if cuadrante is not None:
        _izq_sup(cuadrante)
    if variante not in (None, "limpio", "marcado", "correcto"):
        raise ValueError("variante desconocida: %r" % (variante,))
    if variante == "marcado" and (tarea != "artefacto" or tipo is None):
        raise ValueError("«marcado» es un control de artefacto con su tipo y cuadrante")
    if tarea == "artefacto":
        rgb, mag, uni, det, ctx, capas = caso_artefacto(semilla, lado, tipo, cuadrante, marcado=variante == "marcado")
    else:
        rgb, mag, uni, det, ctx, capas = _CASOS[tarea](semilla, lado, tipo, cuadrante, **extra)
    hay_error = tipo is not None and variante != "marcado"
    if variante == "marcado":
        det = dict(det, cuadrante_artefacto=cuadrante, magnitud_artefacto=mag, unidad_artefacto=uni)
    verdad = {"tarea": tarea, "hay_error": hay_error,
              "tipo": tipo if hay_error else None, "cuadrante": cuadrante if hay_error else None,
              "magnitud": mag if hay_error else None, "unidad": uni if hay_error else None,
              "variante": variante or ("error" if hay_error else "correcto"), "detalle": det}
    assert rgb.dtype == np.uint8 and rgb.shape == (lado, lado, 3)
    return {"rgb": rgb, "verdad": verdad, "contexto": ctx, "capas": capas}


def png_bytes(rgb):
    """PNG RGB sin metadatos (solo IHDR/IDAT/IEND, como exige N1). Lado ≤1024 px."""
    from PIL import Image
    if max(rgb.shape[:2]) > LADO_MAX:
        raise ValueError("imagen de %dx%d: pasa de %d px" % (rgb.shape[1], rgb.shape[0], LADO_MAX))
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgb), "RGB").save(buf, format="PNG", compress_level=6)
    return buf.getvalue()
