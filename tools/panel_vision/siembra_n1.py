"""tools/panel_vision/siembra_n1.py — errores SEMBRADOS sobre las capas N1 REALES del piloto, para la
calibración del panel de visión sobre capas reales (plan «laminillas DFCI», requisito 5-bis (i),
segunda parte: «la calibración sobre capas N1 reales con errores sembrados»).

POR QUÉ. `sinteticos.py` mide si un modelo ve los errores en tejido INVENTADO; el tribunal exige
además `~/Laminillas-N1/panel_vision/calibracion_n1.json`, medida sobre las capas que el panel va a
revisar de verdad. Esto toma esas capas (las que lista `<n1>/panel_vision/capas_piloto.json`), saca
de ellas casos con UN error de tipo, cuadrante y magnitud conocidos y controles, y escribe un
conjunto con el MISMO formato que `calibra.genera_conjunto` (manifiesto, preguntas, verdad, casos):
`calibra.py correr` lo pregunta y `calibra.py exportar --salida <n1>/panel_vision/calibracion_n1.json`
lo puntúa. Las mismas 5 tareas, los mismos tipos y la misma pregunta (sin una palabra cambiada: la
huella de las preguntas es la del protocolo).

DÓNDE ESCRIBE: SOLO dentro de `<n1>/panel_vision/` (por defecto `<n1>/panel_vision/conjunto_n1/`);
cualquier otro destino es ValueError antes de leer una capa. No toca la raíz de N1 ni su manifiesto.

LA PUERTA (fail-closed). Cada capa de origen entra por `exporta_n1.revalidar_n1` (nombre N1, sha256
del manifiesto, OCR + Puerta + trazos) y cada imagen que se escribe sale por
`exporta_n1.png_canonico` + `exporta_n1.verificar_png_n1` (forma PNG de N1, ≤1024 px, OCR + Puerta
de N1 + trazos de rotulador); el texto de cada pregunta, por `puerta_n1.revisar_texto`. Todo
importado, nada editado. Antes de nada, `puerta_n1.exigir_diccionario()`. Una imagen rechazada
PARA la generación entera (no se salta: saltarla sesgaría el conjunto) y no deja manifiesto.

CONTRATO DE LAS CAPAS (lo que la pregunta del protocolo ya da por hecho; la siembra lo usa para
encontrar la capa dentro de la imagen):
  nucleos    H&E a 0,5 µm/px con un contorno ámbar (255,190,0) por núcleo.
  ck19       IHQ CK19 a 0,5 µm/px con la máscara epitelial en contorno magenta (255,0,255).
  artefacto  H&E a 0,5 µm/px; lo que detectó el detector, contorneado en naranja (255,110,0).
  registro   superposición pintada con `sinteticos.pinta_registro` (A amarillo, B violeta, por
             pesos) y la barra gris (50,50,50) de 100 µm abajo en el centro; `contexto.mpp`.
  figura     figura del informe con su especificación en `contexto` (marcador, mpp, escala_um,
             escala_px) y, en `siembra.<fichero>.cajas`, dónde está cada elemento:
             {"etiqueta"|"escala"|"leyenda": [x0, y0, x1, y1]} (quien pinta la figura lo sabe).
  El mpp de nucleos/ck19/artefacto sale del nombre N1 («__mpp0.5000.png») o de `contexto.mpp`; si
  no es 0,5 ±10 %, ValueError: la pregunta dice 0,5 µm/px y otra escala la haría mentir.

CÓMO SE SIEMBRA (por caso: una VISTA de una capa —recorte cuadrado de `lado` px en una posición al
azar y una de las 8 orientaciones— para nucleos, ck19 y artefacto; la capa entera para registro y
figura, que llevan barra y rótulos en su sitio):
  nucleos    omitido: se borran 4-10 contornos vecinos (relleno desde los píxeles de alrededor);
             fusionado: 2-4 pares de núcleos casi en contacto pasan a un solo contorno;
             fuera_de_nucleo: 3-6 contornos del tamaño mediano en zonas sin hematoxilina.
  ck19       epitelio_excluido: se borra el contorno de un nido marrón (área en µm²);
             estroma_incluido: un contorno nuevo encierra estroma no marrón (área en µm²).
  registro   desalineacion: un fragmento de B (componente de tejido o, si no cabe ninguno, un
             disco) se desplaza 75-160 µm dentro de su cuadrante; los controles desplazan uno
             ≤20 µm (lo aceptable). B se separa de A invirtiendo `pinta_registro` (tabla 129×129).
  figura     etiqueta: otro marcador rotulado en su caja; escala: la barra ×0,5-1,8; leyenda:
             los colores de pos y neg intercambiados. El cuadrante es el de la caja.
  artefacto  pliegue, desenfoque o burbuja (las fórmulas de `sinteticos`) sin contornear; el
             control «marcado» lleva el mismo artefacto contorneado en naranja y el «limpio», nada.
Todo cambio queda DENTRO del cuadrante declarado (el test lo mide contra la vista sin error).

LÍMITES DECLARADOS:
  · Los casos salen de pocas capas: no son independientes, y el IC de Wilson supone que lo son
    (sobreestima la precisión). El manifiesto guarda cuántas capas e imágenes distintas hay.
  · El control es la capa del piloto tal cual (o con un desplazamiento ≤20 µm en registro): si la
    capa ya trae un error de verdad, el control lo hereda y cuenta como falso positivo.
  · Figura: sin recorte ni orientación, los controles de una figura son la misma imagen; con k
    figuras hay k controles distintos.
  · La Puerta mira los píxeles por OCR y trazos (sus límites declarados son los de `exporta_n1`);
    los JSON del conjunto (verdad, manifiesto) no salen nunca de N1 y no pasan por ella.

Requiere numpy, scipy y Pillow (venv `patologia`).
"""
import io
import json
import math
import os
import re
import sys

import numpy as np
from scipy import ndimage as ndi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calibra as C  # noqa: E402
import sinteticos as S  # noqa: E402

SEMILLA_N1 = 20261002
LADO_DEFECTO = 512
MPP_PREGUNTA = 0.5              # nucleos, ck19 y artefacto: lo dice la pregunta
TOL_MPP = 0.10
TOL_COLOR = 12                  # |canal - color de la capa| para reconocer el contorno pintado
INTENTOS = 40
DIR_CONJUNTO = "conjunto_n1"
RE_MPP_NOMBRE = re.compile(r"__mpp(\d{1,3}\.\d{4})\.png$")
VISTA = ("nucleos", "ck19", "artefacto")       # se recortan y orientan; registro y figura, no
_K8 = np.ones((3, 3), bool)


class SinSitio(RuntimeError):
    """Esta vista no deja sembrar ese error ahí (se prueba otra vista u otra capa)."""


# ── la Puerta de N1 (importada, no editada) ───────────────────────────────────────────────────
class PuertaN1:
    """`puerta_n1` + `exporta_n1`, tal cual. `fuente` re-valida una capa N1 y devuelve sus bytes
    canónicos; `imagen` devuelve el PNG que se escribe, solo si la Puerta lo deja; `texto`, la
    Puerta sobre una pregunta. Cualquier rechazo lanza PuertaCerrada."""
    nombre = "puerta_n1 + exporta_n1 (revalidar_n1, verificar_png_n1, revisar_texto)"

    def __init__(self):
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import puerta_n1
        import exporta_n1
        self.p, self.e = puerta_n1, exporta_n1
        puerta_n1.exigir_diccionario()

    def fuente(self, ruta, sha256):
        return self.e.revalidar_n1(ruta, sha256)

    def imagen(self, rgb):
        from PIL import Image
        datos, _limpia = self.e.png_canonico(Image.fromarray(np.ascontiguousarray(rgb), "RGB"))
        self.e.verificar_png_n1(datos)
        return datos

    def texto(self, texto, donde):
        self.p.revisar_texto(texto, donde)


# ── utilidades ──────────────────────────────────────────────────────────────────────────────
def _cerca(rgb, color, tol=TOL_COLOR):
    return (np.abs(rgb.astype(np.int16) - np.asarray(color, np.int16)) <= tol).all(axis=2)


def _q(q, H, W):
    """(y0, y1, x0, x1) del cuadrante `q` en una imagen H×W."""
    izq, sup = S._izq_sup(q)
    y0, y1 = (0, H // 2) if sup else (H // 2, H)
    x0, x1 = (0, W // 2) if izq else (W // 2, W)
    return y0, y1, x0, x1


def _caja_q(q, H, W, margen=None):
    """(x0, y0, x1, y1): el interior del cuadrante a `margen` px de bordes y medianas."""
    y0, y1, x0, x1 = _q(q, H, W)
    m = margen if margen is not None else max(16, int(round(0.06 * min(H, W))))
    return x0 + m, y0 + m, x1 - m, y1 - m


def _bb_en_q(bb, q, H, W, holgura=3):
    if bb is None:
        return False
    y0, y1, x0, x1 = bb
    qy0, qy1, qx0, qx1 = _q(q, H, W)
    return x0 >= qx0 + holgura and x1 <= qx1 - holgura and y0 >= qy0 + holgura and y1 <= qy1 - holgura


def cuadrante_de_caja(x0, y0, x1, y1, H, W, holgura=2):
    """El cuadrante que contiene entera la caja (x1, y1 exclusivos), o None."""
    for q in C.CUADRANTES:
        if _bb_en_q((y0, y1, x0, x1), q, H, W, holgura):
            return q
    return None


def mascara_q(q, H, W):
    y0, y1, x0, x1 = _q(q, H, W)
    m = np.zeros((H, W), bool)
    m[y0:y1, x0:x1] = True
    return m


def _rellena(rgb, quitar, validos):
    """`rgb` con los píxeles de `quitar` rellenos desde los `validos` de alrededor (media en 5×5,
    de fuera hacia dentro). Así desaparece un contorno pintado sin dejar hueco."""
    out = rgb.astype(float)
    conocido = validos & ~quitar
    falta = quitar.copy()
    for _ in range(40):
        if not falta.any():
            break
        k = conocido.astype(float)
        n = ndi.uniform_filter(k, 5, mode="nearest")
        nuevos = falta & (n > 1e-6)
        if not nuevos.any():
            break
        for c in range(3):
            s = ndi.uniform_filter(out[..., c] * k, 5, mode="nearest")
            out[..., c][nuevos] = s[nuevos] / n[nuevos]
        conocido |= nuevos
        falta &= ~nuevos
    if falta.any():
        raise SinSitio("no hay píxeles de alrededor para rellenar")
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def _od(rgb):
    return -np.log10(np.maximum(rgb.astype(float), 1.0) / 255.0)


def _concentraciones(rgb, v1, v2):
    """(c1, c2) por deconvolución de color (Ruifrok) con los vectores v1, v2 y su perpendicular."""
    v3 = np.cross(v1, v2)
    v3 = v3 / np.linalg.norm(v3)
    od = _od(rgb)
    c = od.reshape(-1, 3) @ np.linalg.inv(np.stack([v1, v2, v3]))
    H, W = rgb.shape[:2]
    return c[:, 0].reshape(H, W), c[:, 1].reshape(H, W)


def _orienta(a, k):
    a = np.rot90(a, k % 4)
    return np.ascontiguousarray(a[:, ::-1] if k >= 4 else a)


def _vista(capa, lado, rng):
    H, W = capa.shape[:2]
    L = min(lado, H, W) // 2 * 2
    y0, x0 = int(rng.integers(0, H - L + 1)), int(rng.integers(0, W - L + 1))
    k = int(rng.integers(8))
    return _orienta(capa[y0:y0 + L, x0:x0 + L], k), {"recorte": [x0, y0, L], "orientacion": k}


def _bbox(m):
    return S._bbox(m)


# ── (a) núcleos ─────────────────────────────────────────────────────────────────────────────
def siembra_nucleos(rgb, tipo, q, rng, mpp):
    H, W = rgb.shape[:2]
    amb = _cerca(rgb, S.COLOR_NUCLEOS)
    lab, n = ndi.label(amb, structure=_K8)
    if n == 0:
        raise SinSitio("sin contornos ámbar en la vista")
    objs = ndi.find_objects(lab)
    comps = []
    for i, sl in enumerate(objs, 1):
        bb = (sl[0].start, sl[0].stop, sl[1].start, sl[1].stop)
        comps.append({"id": i, "bb": bb, "cy": (bb[0] + bb[1]) / 2.0, "cx": (bb[2] + bb[3]) / 2.0})
    dentro = [c for c in comps if _bb_en_q(c["bb"], q, H, W, 4)]
    x0, y0, x1, y1 = _caja_q(q, H, W)
    out = rgb.copy()
    if tipo == "omitido":
        k = int(rng.integers(4, 11))
        ax, ay = rng.uniform(x0, x1), rng.uniform(y0, y1)
        dentro.sort(key=lambda c: (c["cx"] - ax) ** 2 + (c["cy"] - ay) ** 2)
        if len(dentro) < k:
            raise SinSitio("%d contornos en %s, hacen falta %d" % (len(dentro), q, k))
        quitar = np.isin(lab, [c["id"] for c in dentro[:k]])
        return _rellena(rgb, quitar, ~amb), k, "nucleos", {}
    if tipo == "fusionado":
        k = int(rng.integers(2, 5))
        relleno = np.zeros((H, W), np.int32)
        for c in comps:
            y0_, y1_, x0_, x1_ = c["bb"]
            loc = ndi.binary_fill_holes(lab[y0_:y1_, x0_:x1_] == c["id"])
            relleno[y0_:y1_, x0_:x1_][loc] = c["id"]
        ids_dentro = {c["id"] for c in dentro}
        pares = []
        for c in dentro:
            y0_, y1_, x0_, x1_ = c["bb"]
            sy, sx = slice(max(0, y0_ - 4), y1_ + 4), slice(max(0, x0_ - 4), x1_ + 4)
            dil = ndi.binary_dilation(relleno[sy, sx] == c["id"], structure=_K8, iterations=3)
            for j in np.unique(relleno[sy, sx][dil]):
                if j > c["id"] and j in ids_dentro:
                    pares.append((c["id"], int(j)))
        rng.shuffle(pares)
        usados, elegidos, borde_nuevo = set(), [], np.zeros((H, W), bool)
        todo = relleno > 0
        for i, j in pares:
            if i in usados or j in usados:
                continue
            par = (relleno == i) | (relleno == j)
            unido = ndi.binary_fill_holes(ndi.binary_closing(par, structure=S._disco(3.0)) | par)
            otros = todo & ~par
            if (unido & ndi.binary_dilation(otros, structure=_K8, iterations=2)).any():
                continue
            if not _bb_en_q(_bbox(unido), q, H, W, 4):
                continue
            usados |= {i, j}
            elegidos.append((i, j))
            borde_nuevo |= S._borde(unido)
            if len(elegidos) == k:
                break
        if len(elegidos) < k:
            raise SinSitio("%d pares en contacto en %s, hacen falta %d" % (len(elegidos), q, k))
        quitar = np.isin(lab, sorted(usados))
        out = _rellena(rgb, quitar, ~amb)
        out[borde_nuevo] = S.COLOR_NUCLEOS
        return out, k, "pares", {}
    # fuera_de_nucleo
    k = int(rng.integers(3, 7))
    ejes = sorted(max(c["bb"][1] - c["bb"][0], c["bb"][3] - c["bb"][2]) / 2.0 for c in comps)
    a_med = max(3.0, ejes[len(ejes) // 2])
    cH, _cE = _concentraciones(rgb, S.VH_HE, S.VE_HE)
    tejido = ndi.uniform_filter(_od(rgb).sum(axis=2), 5) > 0.15
    if tejido.sum() < 100:
        raise SinSitio("sin tejido")
    oscuro = cH > np.percentile(cH[tejido], 70)
    relleno = ndi.binary_fill_holes(amb)
    ocupado = ndi.binary_dilation(relleno, iterations=3) | ndi.binary_dilation(oscuro, iterations=2) | ~tejido
    falsos = np.zeros((H, W), bool)
    puestos = intentos = 0
    while puestos < k and intentos < 3000:
        intentos += 1
        a = a_med * rng.uniform(0.9, 1.1)
        v = S._elipse_ventana(rng.uniform(x0, x1), rng.uniform(y0, y1), a, a * rng.uniform(0.7, 0.9),
                              rng.uniform(0, math.pi), (H, W), extra=4)
        if v is None:
            continue
        vy0, vy1, vx0, vx1, m = v
        bb = _bbox(m)
        if not _bb_en_q((bb[0] + vy0, bb[1] + vy0, bb[2] + vx0, bb[3] + vx0), q, H, W, 4):
            continue
        if (ocupado[vy0:vy1, vx0:vx1] & ndi.binary_dilation(m, iterations=2)).any():
            continue
        falsos[vy0:vy1, vx0:vx1] |= m
        ocupado[vy0:vy1, vx0:vx1] |= ndi.binary_dilation(m, iterations=3)
        puestos += 1
    if puestos < k:
        raise SinSitio("sin sitio para %d contornos falsos en %s" % (k, q))
    out[S._borde(falsos)] = S.COLOR_NUCLEOS
    return out, k, "contornos", {}


# ── (b) máscara CK19 ────────────────────────────────────────────────────────────────────────
def _marron(rgb):
    _cH, cD = _concentraciones(rgb, S.VH, S.VDAB)
    return ndi.gaussian_filter(cD, 1.5)


def siembra_ck19(rgb, tipo, q, rng, mpp):
    H, W = rgb.shape[:2]
    mag = _cerca(rgb, S.COLOR_CK19)
    relleno = ndi.binary_fill_holes(mag)
    dab = _marron(rgb)
    out = rgb.copy()
    if tipo == "epitelio_excluido":
        lab, n = ndi.label(relleno, structure=_K8)
        cand = []
        for i, sl in enumerate(ndi.find_objects(lab), 1):
            bb = (sl[0].start, sl[0].stop, sl[1].start, sl[1].stop)
            if not _bb_en_q(bb, q, H, W, 4):
                continue
            region = lab[sl] == i
            interior = region & ~mag[sl]
            if interior.sum() < 30 or (dab[sl][interior] > 0.15).mean() < 0.5:
                continue
            cand.append(i)
        if not cand:
            raise SinSitio("ningún nido marrón contorneado entero en %s" % q)
        i = cand[int(rng.integers(len(cand)))]
        region = lab == i
        out = _rellena(rgb, mag & region, ~mag)
        return out, round(float(region.sum()) * mpp * mpp, 1), "um2", {}
    # estroma_incluido
    tejido = ndi.uniform_filter(_od(rgb).sum(axis=2), 7) > 0.10
    lejos = ndi.distance_transform_edt(~relleno)
    x0, y0, x1, y1 = _caja_q(q, H, W)
    lado = min(H, W)
    for _ in range(400):
        R = rng.uniform(0.03, 0.05) * lado
        cx, cy = rng.uniform(x0, x1), rng.uniform(y0, y1)
        if lejos[int(cy), int(cx)] < R + 8:
            continue
        blob = np.zeros((H, W), bool)
        S._pinta_elipse(blob, cx, cy, R, R * rng.uniform(0.6, 0.9), rng.uniform(0, math.pi))
        S._pinta_elipse(blob, cx + rng.normal(0, 0.3 * R), cy + rng.normal(0, 0.3 * R), 0.7 * R, 0.5 * R,
                        rng.uniform(0, math.pi))
        blob = ndi.binary_fill_holes(ndi.gaussian_filter(blob.astype(float), 1.5) > 0.5)
        if not _bb_en_q(_bbox(blob), q, H, W, 6):
            continue
        if (blob & ndi.binary_dilation(relleno, iterations=6)).any():
            continue
        if tejido[blob].mean() < 0.8 or (dab[blob] > 0.15).mean() > 0.1:
            continue
        out[S._trazo(blob, 2)] = S.COLOR_CK19
        return out, round(float(blob.sum()) * mpp * mpp, 1), "um2", {}
    raise SinSitio("sin estroma libre para encerrar en %s" % q)


# ── (c) registro ────────────────────────────────────────────────────────────────────────────
_TABLA = None


def _tabla():
    global _TABLA
    if _TABLA is None:
        from scipy.spatial import cKDTree
        g = np.linspace(0.0, 1.0, 129)
        wa, wb = np.meshgrid(g, g, indexing="ij")
        _TABLA = (cKDTree(S.pinta_registro(wa, wb).reshape(-1, 3)), wa.ravel(), wb.ravel())
    return _TABLA


def separa_registro(rgb):
    """(wA, wB, residuo): los pesos de A y B que `sinteticos.pinta_registro` convertiría en cada
    píxel (vecino más próximo en una tabla de 129×129) y la distancia RGB a lo que pintaría."""
    arbol, wa, wb = _tabla()
    H, W = rgb.shape[:2]
    d, idx = arbol.query(rgb.reshape(-1, 3).astype(float))
    return wa[idx].reshape(H, W), wb[idx].reshape(H, W), d.reshape(H, W)


def siembra_registro(rgb, tipo, q, rng, mpp, control=False):
    """Desplaza un fragmento de B: 75-160 µm dentro de `q` (error) o ≤ JITTER (control)."""
    H, W = rgb.shape[:2]
    barra = _cerca(rgb, S.COLOR_BARRA, 6)
    wA, wB, resid = separa_registro(rgb)
    ajeno = ndi.binary_dilation(barra | (resid > 20), iterations=2)
    tejido = ndi.binary_closing(((wA > 0.05) | (wB > 0.05)) & ~ajeno, iterations=2)
    lab, n = ndi.label(tejido, structure=_K8)
    u = 1.0 / mpp
    rango = (0.0, S.JITTER_REGISTRO_UM) if control else S.ERROR_REGISTRO_UM
    cuadrantes = list(C.CUADRANTES) if control else [q]
    frags = []
    for i, sl in enumerate(ndi.find_objects(lab), 1):
        bb = (sl[0].start, sl[0].stop, sl[1].start, sl[1].stop)
        qq = next((c for c in cuadrantes if _bb_en_q(bb, c, H, W, 6)), None)
        if qq and (lab[sl] == i).sum() >= 50:
            frags.append((i, qq))
    prohibido = ndi.binary_dilation(barra, iterations=4)
    yy, xx = np.mgrid[0:H, 0:W]
    for intento in range(600):
        if frags and intento < 400:
            i, qq = frags[int(rng.integers(len(frags)))]
            F = lab == i
            forma = "fragmento"
        else:                                           # sin fragmento entero: un disco de tejido
            qq = cuadrantes[int(rng.integers(len(cuadrantes)))]
            x0, y0, x1, y1 = _caja_q(qq, H, W)
            R = rng.uniform(0.06, 0.1) * min(H, W)
            F = (np.hypot(xx - rng.uniform(x0, x1), yy - rng.uniform(y0, y1)) <= R) & tejido
            forma = "disco"
            if F.sum() < 50:
                continue
        d_um, ang = rng.uniform(*rango), rng.uniform(0, 2 * math.pi)
        dx, dy = d_um * u * math.cos(ang), d_um * u * math.sin(ang)
        Fs = ndi.shift(F.astype(float), (dy, dx), order=1, cval=0.0) > 0.01
        zona = ndi.binary_dilation(F | Fs, iterations=2)
        if not _bb_en_q(_bbox(zona), qq, H, W, 4) or (zona & prohibido).any():
            continue
        BF = np.where(F, wB, 0.0)
        wB2 = np.clip(np.where(F, 0.0, wB) + ndi.shift(BF, (dy, dx), order=1, cval=0.0), 0.0, 1.0)
        nuevo = np.clip(np.rint(S.pinta_registro(wA, wB2)), 0, 255).astype(np.uint8)
        out = rgb.copy()
        cambia = zona & ~barra
        out[cambia] = nuevo[cambia]
        detalle = {"forma": forma, "desplazamiento_um": round(d_um, 1), "cuadrante_desplazado": qq}
        return out, round(d_um, 1), "um", detalle
    raise SinSitio("ningún fragmento de B cabe desplazado en %s" % ("ningún cuadrante" if control else q))


# ── (d) figura ──────────────────────────────────────────────────────────────────────────────
def _fondo_caja(sub):
    borde = np.concatenate([sub[0], sub[-1], sub[:, 0], sub[:, -1]])
    return tuple(int(v) for v in np.median(borde, axis=0))


def siembra_figura(rgb, tipo, rng, contexto, cajas):
    """El error `tipo` en la caja de su elemento; el cuadrante es el de esa caja."""
    from PIL import Image, ImageDraw
    H, W = rgb.shape[:2]
    if tipo not in (cajas or {}):
        raise SinSitio("la figura no declara la caja de «%s»" % tipo)
    x0, y0, x1, y1 = (int(v) for v in cajas[tipo])
    if not (0 <= x0 < x1 <= W and 0 <= y0 < y1 <= H):
        raise ValueError("caja de %s fuera de la imagen: %r" % (tipo, cajas[tipo]))
    q = cuadrante_de_caja(x0, y0, x1, y1, H, W)
    if q is None:
        raise SinSitio("la caja de «%s» no cae entera en un cuadrante" % tipo)
    out = rgb.copy()
    sub = out[y0:y1, x0:x1]
    fondo = _fondo_caja(sub)
    if tipo == "leyenda":
        s = sub.astype(int)
        rojo = (s[..., 0] - s[..., 2] > 60) & (s[..., 0] - s[..., 1] > 60)
        azul = (s[..., 2] - s[..., 0] > 60)
        if rojo.sum() < 16 or azul.sum() < 16:
            raise SinSitio("la leyenda no tiene un rojo y un azul reconocibles")
        c_rojo = np.median(sub[rojo], axis=0).astype(np.uint8)
        c_azul = np.median(sub[azul], axis=0).astype(np.uint8)
        sub[rojo], sub[azul] = c_azul, c_rojo
        return out, q, 1, "elemento", {}
    if tipo == "escala":
        barra = _cerca(sub, S.COLOR_BARRA, 6)
        lab, n = ndi.label(barra)
        if n == 0:
            raise SinSitio("sin barra gris en la caja de escala")
        tam = ndi.sum(barra, lab, index=np.arange(1, n + 1))
        b = lab == int(np.argmax(tam)) + 1
        ys, xs = np.nonzero(b)
        largo, by0, by1, bx0 = int(xs.max() - xs.min() + 1), int(ys.min()), int(ys.max()) + 1, int(xs.min())
        if abs(largo - int(contexto["escala_px"])) > 3:
            raise ValueError("la barra de la figura mide %d px y la especificación dice %d"
                             % (largo, int(contexto["escala_px"])))
        qy0, qy1, qx0, qx1 = _q(q, H, W)
        factores = list(S.FACTORES_ESCALA)
        rng.shuffle(factores)
        for f in factores:
            nuevo = int(round(largo * f))
            if x0 + bx0 + nuevo <= qx1 - 3:
                break
        else:
            raise SinSitio("ninguna barra cambiada cabe en el cuadrante")
        color = np.median(sub[b], axis=0).astype(np.uint8)
        sub[b] = fondo
        out[y0 + by0:y0 + by1, x0 + bx0:x0 + bx0 + nuevo] = color
        return out, q, f, "factor", {"largo_px": nuevo}
    # etiqueta
    marcador = str(contexto["marcador"])
    otros = [m for m in S.MARCADORES if m != marcador]
    nueva = otros[int(rng.integers(len(otros)))]
    gris = sub.astype(float).mean(axis=2)
    texto = gris < float(np.mean(fondo)) - 40
    if texto.sum() < 5:
        raise SinSitio("la caja de etiqueta no tiene texto reconocible")
    ys, xs = np.nonzero(texto)
    color = tuple(int(v) for v in np.median(sub[texto], axis=0))
    alto = int(ys.max() - ys.min() + 1)
    img = Image.fromarray(out, "RGB")
    d = ImageDraw.Draw(img)
    d.rectangle((x0, y0, x1 - 1, y1 - 1), fill=fondo)
    fs = max(8, int(round(alto / 0.72)))
    while fs >= 8:
        fuente = S._fuente(fs)
        bb = d.textbbox((0, 0), nueva, font=fuente)
        tx, ty = x0 + int(xs.min()) - bb[0], y0 + int(ys.min()) - bb[1]
        if tx + bb[2] <= x1 and ty + bb[3] <= y1 and tx + bb[0] >= x0 and ty + bb[1] >= y0:
            break
        fs -= 1
    else:
        raise SinSitio("el marcador nuevo no cabe en la caja de etiqueta")
    d.text((tx, ty), nueva, fill=color, font=fuente)
    return np.array(img), q, 1, "elemento", {"etiqueta_dibujada": nueva}


# ── (e) artefacto ───────────────────────────────────────────────────────────────────────────
def siembra_artefacto(rgb, tipo, q, rng, mpp, marcado=False):
    """pliegue, desenfoque o burbuja en `q` (las fórmulas de `sinteticos.caso_artefacto` sobre la
    densidad óptica de la vista real); contorneado en naranja si `marcado`."""
    H, W = rgb.shape[:2]
    lado = min(H, W)
    u = 1.0 / mpp
    qc = ndi.binary_dilation(_cerca(rgb, S.COLOR_QC), iterations=20)
    od0 = _od(rgb)
    x0, y0, x1, y1 = _caja_q(q, H, W)
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
            ix = np.clip(np.rint(pts[:, 0]).astype(int), 0, W - 1)
            iy = np.clip(np.rint(pts[:, 1]).astype(int), 0, H - 1)
            linea[iy, ix] = True
            dist = ndi.distance_transform_edt(~linea)
            zona, holg = dist <= w / 2, 8
        elif tipo == "desenfoque":
            ra = rng.uniform(0.07, 0.11) * lado
            zona = np.zeros((H, W), bool)
            S._pinta_elipse(zona, rng.uniform(x0, x1), rng.uniform(y0, y1), ra, ra * rng.uniform(0.6, 1.0),
                            rng.uniform(0, math.pi))
            holg = 20
        else:
            r = rng.uniform(0.05, 0.09) * lado
            cx, cy = rng.uniform(x0, x1), rng.uniform(y0, y1)
            dist = np.hypot(xx - cx, yy - cy)
            zona, holg = dist <= r + 2, 14
        if _bb_en_q(_bbox(zona), q, H, W, holg) and not (zona & qc).any():
            break
    else:
        raise SinSitio("el %s no cabe en %s sin pisar un contorno naranja" % (tipo, q))
    od = od0.copy()
    if tipo == "pliegue":
        capa = ndi.shift(od, (nrm[1] * w, nrm[0] * w, 0), order=1, mode="nearest")
        od = np.where(zona[..., None], od + 0.9 * capa, od)
        od[zona & (dist > w / 2 - 1.5)] += 0.12
        afectado = zona
        mag, uni = round(w * mpp, 1), "um_ancho"
    elif tipo == "desenfoque":
        sigma = rng.uniform(2.5, 4.5)
        alfa = ndi.gaussian_filter(zona.astype(float), 4.0) * ndi.binary_dilation(zona, iterations=14)
        od = od * (1 - alfa[..., None]) + ndi.gaussian_filter(od, (sigma, sigma, 0)) * alfa[..., None]
        afectado = alfa > 0
        mag, uni = round(sigma * mpp, 2), "um_sigma"
    else:
        dentro = dist < r
        od = np.where(dentro[..., None], 0.75 * ndi.gaussian_filter(od, (1.2, 1.2, 0)), od)
        anillo = np.exp(-((dist - r) / 1.8) ** 2) * (np.abs(dist - r) < 9)
        luz = rng.uniform(0, 2 * math.pi)
        lado_luz = np.cos(np.arctan2(yy - cy, xx - cx) - luz) > 0.3
        brillo = np.exp(-((dist - 0.82 * r) / 2.5) ** 2) * dentro * lado_luz
        od = np.clip(od + 0.5 * anillo[..., None] - 0.08 * brillo[..., None], 0.0, None)
        afectado = dentro | (anillo > 0)
        mag, uni = round(r * mpp, 1), "um_radio"
    od = np.minimum(od, 1.6)
    out = rgb.copy()
    nuevo = S._od_a_rgb(od)
    out[afectado] = nuevo[afectado]
    if marcado:
        out[S._trazo(ndi.binary_dilation(zona, iterations=4), 2)] = S.COLOR_QC
    return out, mag, uni, {"artefacto": tipo}


# ── un caso ─────────────────────────────────────────────────────────────────────────────────
def siembra_caso(capa, tarea, semilla, tipo=None, cuadrante=None, variante=None, mpp=None, contexto=None,
                 cajas=None, lado=LADO_DEFECTO):
    """{"rgb", "base", "verdad", "contexto", "vista"} de UNA capa real. `base` es la vista sin
    error (el gemelo: la diferencia con `rgb` queda en el cuadrante de la verdad). SinSitio si
    esta semilla no deja sembrar ahí."""
    C._tarea(tarea)
    if tipo is not None and tipo not in C.TIPOS[tarea]:
        raise ValueError("tipo %r no es de %s" % (tipo, tarea))
    rng = np.random.default_rng([int(semilla) % (2 ** 32), 7])
    contexto = dict(contexto or {})
    if tarea in VISTA:
        base, vista = _vista(capa, lado, rng)
        if min(base.shape[:2]) < S.LADO_MIN:
            raise ValueError("capa de %dx%d: la vista necesita ≥%d px" % (capa.shape[1], capa.shape[0], S.LADO_MIN))
    else:
        base, vista = np.ascontiguousarray(capa), {"recorte": None, "orientacion": 0}
    H, W = base.shape[:2]
    hay_error = tipo is not None and variante != "marcado"
    detalle, mag, uni = {}, None, None
    rgb = base
    if tarea == "nucleos" and tipo:
        rgb, mag, uni, detalle = siembra_nucleos(base, tipo, cuadrante, rng, mpp)
    elif tarea == "ck19" and tipo:
        rgb, mag, uni, detalle = siembra_ck19(base, tipo, cuadrante, rng, mpp)
    elif tarea == "artefacto" and tipo:
        rgb, mag, uni, detalle = siembra_artefacto(base, tipo, cuadrante, rng, mpp, marcado=variante == "marcado")
    elif tarea == "registro":
        rgb, mag, uni, detalle = siembra_registro(base, tipo, cuadrante, rng, mpp, control=not tipo)
        if not tipo:
            detalle = dict(detalle, jitter_um=mag)
            mag = uni = None
    elif tarea == "figura" and tipo:
        rgb, cuadrante, mag, uni, detalle = siembra_figura(base, tipo, rng, contexto, cajas)
    if variante == "marcado":
        detalle = dict(detalle, cuadrante_artefacto=cuadrante, magnitud_artefacto=mag, unidad_artefacto=uni)
    verdad = {"tarea": tarea, "hay_error": hay_error,
              "tipo": tipo if hay_error else None, "cuadrante": cuadrante if hay_error else None,
              "magnitud": mag if hay_error else None, "unidad": uni if hay_error else None,
              "variante": variante or ("error" if hay_error else "correcto"), "detalle": detalle}
    assert rgb.dtype == np.uint8 and rgb.shape == (H, W, 3)
    return {"rgb": rgb, "base": base, "verdad": verdad, "contexto": contexto, "vista": vista}


# ── el conjunto ─────────────────────────────────────────────────────────────────────────────
def _mpp_de(fichero, contexto):
    if contexto.get("mpp") is not None:
        return float(contexto["mpp"])
    m = RE_MPP_NOMBRE.search(fichero)
    return float(m.group(1)) if m else None


def _carga_capas(n1, puerta, tareas=None):
    """{tarea: [{"fichero", "sha256", "rgb", "mpp", "contexto", "cajas"}]} de capas_piloto.json,
    cada capa re-validada por la Puerta."""
    from PIL import Image
    panel = os.path.join(n1, C.N1_PANEL)
    with open(os.path.join(panel, C.CAPAS_PILOTO), encoding="utf-8") as fh:
        capas = json.load(fh)
    with open(os.path.join(n1, "manifiesto.json"), encoding="utf-8") as fh:
        man = json.load(fh).get("ficheros") or {}
    lista = capas.get("tareas") or {}
    tareas = [C._tarea(t) for t in (tareas or [t for t in C.TAREAS if lista.get(t)])]
    out = {}
    for t in tareas:
        if not lista.get(t):
            raise ValueError("%s no lista capas de %s" % (C.CAPAS_PILOTO, t))
        for fich in lista[t]:
            if not isinstance(fich, str) or os.path.basename(fich) != fich or not (man.get(fich) or {}).get("sha256"):
                raise ValueError("%s (%s): no es un fichero N1 con sha256 en el manifiesto" % (fich, t))
            sha = man[fich]["sha256"]
            datos = puerta.fuente(os.path.join(n1, fich), sha)
            rgb = np.array(Image.open(io.BytesIO(datos)).convert("RGB"))
            ctx = dict((capas.get("contexto") or {}).get(fich) or {})
            mpp = _mpp_de(fich, ctx)
            if t in VISTA:
                if mpp is None or abs(mpp - MPP_PREGUNTA) > TOL_MPP * MPP_PREGUNTA:
                    raise ValueError("%s (%s): la pregunta dice %.1f µm/px y la capa está a %s"
                                     % (fich, t, MPP_PREGUNTA, "?" if mpp is None else "%g" % mpp))
                ctx = {}
            else:
                falta = sorted(set(C.CONTEXTO[t]) - set(ctx))
                if falta:
                    raise ValueError("%s (%s): falta contexto %s en %s" % (fich, t, falta, C.CAPAS_PILOTO))
                ctx = {k: ctx[k] for k in C.CONTEXTO[t]}
                mpp = float(ctx["mpp"])
            cajas = ((capas.get("siembra") or {}).get(fich) or {}).get("cajas")
            out.setdefault(t, []).append({"fichero": fich, "sha256": sha, "rgb": rgb, "mpp": mpp,
                                          "contexto": ctx, "cajas": cajas})
    return out


def genera_conjunto_n1(n1, dir_=None, n=30, semilla=SEMILLA_N1, lado=LADO_DEFECTO, tareas=None, puerta=None,
                       avisa=None):
    """Escribe en `dir_` (dentro de `<n1>/panel_vision/`) el conjunto sembrado sobre las capas del
    piloto, con el formato de `calibra.genera_conjunto`. Devuelve el manifiesto."""
    S.valida_lado(lado)
    if n < 1:
        raise ValueError("n tiene que ser ≥1")
    n1 = os.path.realpath(n1)
    panel = os.path.join(n1, C.N1_PANEL)
    dir_ = os.path.realpath(dir_ or os.path.join(panel, DIR_CONJUNTO))
    if not dir_.startswith(panel + os.sep):
        raise ValueError("el conjunto N1 solo se escribe dentro de %s/" % panel)
    if os.path.exists(os.path.join(dir_, "manifiesto.json")):
        raise FileExistsError("%s ya tiene un conjunto: usa otro directorio" % dir_)
    puerta = puerta if puerta is not None else PuertaN1()
    fuentes = _carga_capas(n1, puerta, tareas)
    tareas = tuple(t for t in C.TAREAS if t in fuentes)
    os.makedirs(os.path.join(dir_, "casos"), exist_ok=True)
    plan = C.plan_conjunto(n, semilla, tareas)
    cuenta = {t: 0 for t in tareas}
    casos = []
    for k, p in enumerate(plan, 1):
        t = p["tarea"]
        lista = fuentes[t]
        base_i = cuenta[t]
        cuenta[t] += 1
        for intento in range(INTENTOS):
            f = lista[(base_i + intento) % len(lista)]
            semilla_c = (p["semilla"] + intento * 7919) % (2 ** 32)
            try:
                c = siembra_caso(f["rgb"], t, semilla_c, p["tipo"], p["cuadrante"], p["variante"], f["mpp"],
                                 f["contexto"], f["cajas"], lado)
                break
            except SinSitio:
                continue
        else:
            raise RuntimeError("caso %s (%s %s): ninguna de %d vistas deja sembrar el error en estas capas"
                               % (p["id"], t, p["tipo"] or "control", INTENTOS))
        prompt = C.pregunta(t, c["contexto"])
        puerta.texto(prompt, "pregunta %s" % t)
        datos = puerta.imagen(c["rgb"])
        ruta = os.path.join("casos", p["id"] + ".png")
        with open(os.path.join(dir_, ruta), "wb") as fh:
            fh.write(datos)
        verdad = dict(c["verdad"], semilla=semilla_c,
                      origen={"capa": f["fichero"], "sha256": f["sha256"], **c["vista"]})
        casos.append({"id": p["id"], "tarea": t, "png": ruta, "sha256": C._sha(datos), "prompt": prompt,
                      "verdad": verdad})
        if avisa and (k % 25 == 0 or k == len(plan)):
            avisa("  %d/%d casos" % (k, len(plan)))
    with open(os.path.join(dir_, "preguntas.jsonl"), "w", encoding="utf-8") as fh:
        for c in casos:
            fh.write(C._canon({"id": c["id"], "tarea": c["tarea"], "png": c["png"], "prompt": c["prompt"]}) + "\n")
    with open(os.path.join(dir_, "verdad.json"), "w", encoding="utf-8") as fh:
        json.dump({c["id"]: c["verdad"] for c in casos}, fh, ensure_ascii=False, indent=1, sort_keys=True)
    distintas = {}
    for t in tareas:
        for clase in (True, False):
            shas = {c["sha256"] for c in casos if c["tarea"] == t and c["verdad"]["hay_error"] is clase}
            distintas.setdefault(t, {})["errores" if clase else "controles"] = len(shas)
    man = {"protocolo": C.PROTOCOLO, "huella_preguntas": C.huella_preguntas(), "semilla": semilla, "lado": lado,
           "n_por_clase": n, "tareas": list(tareas), "casos": {c["id"]: c["sha256"] for c in casos},
           "sha_conjunto": C._huella_conjunto(casos), "creado": C._ahora(),
           "origen": {"tipo": "capas-n1-sembradas", "puerta": getattr(puerta, "nombre", type(puerta).__name__),
                      "capas": {t: {f["fichero"]: f["sha256"] for f in fuentes[t]} for t in tareas},
                      "imagenes_distintas": distintas},
           "regenerar": "calibra.py sembrar-n1 --n %d --lado %d --semilla %d (las mismas capas: sha256 en origen)"
                        % (n, lado, semilla)}
    with open(os.path.join(dir_, "manifiesto.json"), "w", encoding="utf-8") as fh:
        json.dump(man, fh, ensure_ascii=False, indent=1, sort_keys=True)
    return man
