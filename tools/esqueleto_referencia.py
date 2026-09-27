#!/usr/bin/env python3
"""Esqueleto de REFERENCIA, de la cabeza a los pies, para /lesiones y /mapa-metastasis.

Por qué existe: su TC de cuerpo entero va de la cabeza al muslo con los brazos levantados, así
que de su dato nunca sale una figura completa (medido el 20-sep, `visor3d.py esqueleto`). {{TITULAR}}
pidió un esqueleto «como los de TotalSegmentator» (27-sep): entero, con los brazos abajo y la caja
cerrada por delante. Eso sale de un atlas anatómico público, BodyParts3D (CC BY 4.0, DBCLS), no
de ella. Lo suyo siguen siendo los FOCOS: la web los coloca en el centroide de su hueso con el
mismo `esqueleto.json`, y aquí solo cambia el dibujo de fondo.

Salida (mismo contrato que `visor3d.py esqueleto`, para que el componente no cambie):
  esqueleto-anterior.png   RGBA, fondo transparente, vista de frente
  esqueleto.json           {imagen, tamano, huesos: {nombre_TotalSegmentator: {u, v}}, fuente}

Uso (con un python que tenga numpy y PIL, p. ej. .venv/bin/python):
  .venv/bin/python tools/esqueleto_referencia.py --bp3d <carpeta con FJ*.obj> --salida <dir>
El zip se baja a mano (OK de {{TITULAR}}, 27-sep) de
  https://dbarchive.biosciencedbc.jp/data/bodyparts3d/LATEST/partof_BP3D_4.0_obj_99.zip
Atribución obligatoria donde se publique: ver ATRIBUCION.
"""
import argparse
import glob
import json
import os
import re

ATRIBUCION = ("BodyParts3D, © The Database Center for Life Science licensed under "
              "CC Attribution 4.0 International")

# Lo que NO es hueso aunque se llame parecido («ulnar artery», «iliotibial tract»...).
FUERA = re.compile(r"artery|vein|nerve|muscle|tendon|ligament|bursa|gland|eyeball|choroid|"
                   r"cornea|iris|lens|sclera|vitreous|retina|gingiva|tooth|teeth|tongue|cheek|"
                   r"skin|fascia|tract|capsule|membrane|meniscus|labrum|synovial|disk|symphysis|"
                   r"septal|alar|chamber|part of|joint|knee|nasal cartilage", re.I)
HUESO = re.compile(r"bone|vertebra|atlas|axis|sacrum|coccyx|rib\b|sternum|manubrium|xiphoid|"
                   r"clavicle|scapula|humerus|radius|ulna|femur|tibia|fibula|patella|calcaneus|"
                   r"talus|phalanx|mandible|maxilla|vomer|concha|basicranium|occipital|"
                   r"scaphoid|lunate|triquetrum|pisiform|trapezium|trapezoid|capitate|hamate|"
                   r"cuboid|cuneiform|navicular|sesamoid", re.I)
# El cartílago costal no es hueso, pero es lo que CIERRA la caja por delante, que era la queja.
CARTILAGO = re.compile(r"costal cartilage", re.I)

ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
       "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12}


def nombre_totalseg(n):
    """Nombre de BodyParts3D → nombre de TotalSegmentator (el que usa la web). None si no toca."""
    n = n.lower().strip()
    m = re.fullmatch(r"(\w+) (cervical|thoracic|lumbar) vertebra", n)
    if m and m.group(1) in ORD:
        return "vertebrae_%s%d" % (m.group(2)[0].upper(), ORD[m.group(1)])
    if n == "atlas":
        return "vertebrae_C1"
    if n == "axis":
        return "vertebrae_C2"
    m = re.fullmatch(r"(left|right) (\w+) rib", n)
    if m and m.group(2) in ORD:
        return "rib_%s_%d" % (m.group(1), ORD[m.group(2)])
    m = re.fullmatch(r"(left|right) (hip bone|femur|humerus|scapula|clavicle)", n)
    if m:
        base = {"hip bone": "hip", "clavicle": "clavicula"}.get(m.group(2), m.group(2))
        return "%s_%s" % (base, m.group(1))
    if n == "sacrum":
        return "sacrum"
    if n in ("manubrium", "body of sternum", "xiphoid process"):
        return "sternum"
    return None


def es_pieza(n):
    return bool(CARTILAGO.search(n)) or (bool(HUESO.search(n)) and not FUERA.search(n))


def lee_obj(ruta):
    import numpy as np
    nombre, vs, fs = None, [], []
    with open(ruta, encoding="utf-8", errors="replace") as f:
        for linea in f:
            if linea.startswith("v "):
                vs.append(linea.split()[1:4])
            elif linea.startswith("f "):
                fs.append([int(p.split("/")[0]) for p in linea.split()[1:4]])
            elif linea.startswith("# English name"):
                nombre = linea.split(":", 1)[1].strip()
    if not vs or not fs:
        return nombre, None, None
    return nombre, np.asarray(vs, float), np.asarray(fs, int) - 1


def carga(carpeta):
    piezas = []
    for ruta in sorted(glob.glob(os.path.join(carpeta, "*.obj"))):
        # la cabecera basta para decidir; así no se parsean 1.200 mallas que no son hueso
        with open(ruta, encoding="utf-8", errors="replace") as f:
            cab = "".join(next(f, "") for _ in range(14))
        m = re.search(r"# English name : (.*)", cab)
        n = m.group(1).strip() if m else ""
        if not es_pieza(n):
            continue
        nombre, v, f = lee_obj(ruta)
        if v is None:
            continue
        piezas.append({"nombre": nombre, "v": v, "f": f, "cartilago": bool(CARTILAGO.search(n))})
    if not piezas:
        raise SystemExit("ABORTA: ninguna malla de hueso en %s" % carpeta)
    return piezas


def orienta(piezas):
    """Ejes de la imagen desde la anatomía, no supuestos: arriba = cráneo, delante = mandíbula
    respecto a la columna, y la izquierda del cuerpo a la DERECHA de quien mira (vista anterior).
    Devuelve una función 3D → (x_img, y_img, profundidad hacia el observador)."""
    import numpy as np

    def centro(pat):
        vs = [p["v"] for p in piezas if re.fullmatch(pat, p["nombre"], re.I)]
        if not vs:
            raise SystemExit("ABORTA: no encuentro «%s» para orientar la figura" % pat)
        return np.concatenate(vs).mean(0)

    arriba = centro("frontal bone|basicranium|occipital bone") - centro("sacrum")
    delante = centro("mandible") - centro("(third|fourth) cervical vertebra")
    izq = centro("left femur") - centro("right femur")
    ez = arriba / np.linalg.norm(arriba)
    ey = delante - ez * (delante @ ez)
    ey /= np.linalg.norm(ey)
    ex = izq - ez * (izq @ ez) - ey * (izq @ ey)
    ex /= np.linalg.norm(ex)
    return lambda P: np.stack([P @ ex, -(P @ ez), P @ ey], -1)


def render(piezas, proy, ancho=880, margen=0.035, ss=2):
    """Rasteriza con z-buffer y sombreado Lambert + especular suave, a `ss`× y reduce (antialias).
    Devuelve (imagen, función 3D → (u, v) en fracción de la imagen final)."""
    import numpy as np
    from PIL import Image
    todos = np.concatenate([proy(p["v"]) for p in piezas])
    lo, hi = todos.min(0), todos.max(0)
    ext = hi - lo
    W = ancho * ss
    esc = W * (1 - 2 * margen) / ext[0]
    H = int(round(ext[1] * esc + 2 * margen * W))
    off = np.array([margen * W, margen * W]) - lo[:2] * esc
    zbuf = np.full((H, W), -np.inf)
    col = np.zeros((H, W), float)
    luz = np.array([-0.35, -0.55, 0.76])
    luz /= np.linalg.norm(luz)
    medio = luz + np.array([0, 0, 1.0])
    medio /= np.linalg.norm(medio)
    for p in piezas:
        Q = proy(p["v"])
        xy = Q[:, :2] * esc + off
        z = Q[:, 2]
        T = p["f"]
        a, b, c = Q[T[:, 0]], Q[T[:, 1]], Q[T[:, 2]]
        n = np.cross(b - a, c - a)
        n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
        # con y hacia abajo en la imagen, la cara que mira al observador tiene n·z < 0 o > 0
        # según el orden de los vértices; las mallas no traen orientación fiable, así que se
        # voltea toda normal que no mire al observador
        n[n[:, 2] < 0] *= -1
        lam = np.clip(n @ luz, 0, 1)
        spec = np.clip(n @ medio, 0, 1) ** 24
        base = 0.66 if p["cartilago"] else 1.0     # el cartílago, algo más apagado que el hueso
        tono = base * (0.12 + 0.80 * lam ** 1.4) + 0.18 * spec
        for k in range(len(T)):
            i0, i1, i2 = T[k]
            x0, y0 = xy[i0]
            x1, y1 = xy[i1]
            x2, y2 = xy[i2]
            xa, xb = int(max(0, np.floor(min(x0, x1, x2)))), int(min(W - 1, np.ceil(max(x0, x1, x2))))
            ya, yb = int(max(0, np.floor(min(y0, y1, y2)))), int(min(H - 1, np.ceil(max(y0, y1, y2))))
            if xa > xb or ya > yb:
                continue
            den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
            if abs(den) < 1e-9:
                continue
            gx, gy = np.meshgrid(np.arange(xa, xb + 1) + 0.5, np.arange(ya, yb + 1) + 0.5)
            w0 = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / den
            w1 = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / den
            w2 = 1 - w0 - w1
            dentro = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
            if not dentro.any():
                continue
            zz = w0 * z[i0] + w1 * z[i1] + w2 * z[i2]
            sub = zbuf[ya:yb + 1, xa:xb + 1]
            gana = dentro & (zz > sub)
            sub[gana] = zz[gana]
            col[ya:yb + 1, xa:xb + 1][gana] = tono[k]
    lleno = np.isfinite(zbuf)
    # lo que queda al fondo se apaga un poco: da volumen sin cambiar de color
    prof = np.where(lleno, (zbuf - lo[2]) / max(ext[2], 1e-9), 0)
    v = np.clip(col * (0.78 + 0.22 * prof), 0, 1)
    # silueta: borde oscuro fino donde el hueso toca el fondo, como el render anterior
    interior = (np.roll(lleno, 1, 0) & np.roll(lleno, -1, 0)
                & np.roll(lleno, 1, 1) & np.roll(lleno, -1, 1))
    v[lleno & ~interior] *= 0.55
    # paleta del hueso de la web (gris frío), nunca dorado: el dorado es «lesión»
    osc, cla = np.array([0x6f, 0x76, 0x82]), np.array([0xe6, 0xe9, 0xee])
    rgb = osc[None, None] + (cla - osc)[None, None] * v[..., None]
    rgba = np.dstack([rgb, lleno * 255]).astype(np.uint8)
    im = Image.fromarray(rgba, "RGBA").resize((ancho, int(round(H / ss))), Image.LANCZOS)
    return im, (lambda P: (proy(P)[:, :2] * esc + off) / np.array([W, H]))


def centroides(piezas, a_uv):
    import numpy as np
    grupos = {}
    for p in piezas:
        ts = nombre_totalseg(p["nombre"])
        if ts:
            grupos.setdefault(ts, []).append(p["v"])
    out = {}
    for ts, vs in sorted(grupos.items()):
        P = np.concatenate(vs)
        if ts.startswith("femur_"):
            # El fémur de su TC venía cortado por el muslo y su centroide caía en el extremo
            # proximal, que es donde está el foco («fémur proximal derecho»). Con el fémur
            # entero el centroide baja a media diáfisis: se toma el cuarto proximal.
            vv = a_uv(P)[:, 1]
            P = P[vv <= vv.min() + 0.25 * (vv.max() - vv.min())]
        u, v = a_uv(P.mean(0, keepdims=True))[0]
        out[ts] = {"u": round(float(u), 5), "v": round(float(v), 5)}
    return out


ORDEN_COLUMNA = (["vertebrae_C%d" % i for i in range(1, 8)]
                 + ["vertebrae_T%d" % i for i in range(1, 13)]
                 + ["vertebrae_L%d" % i for i in range(1, 6)] + ["sacrum"])


def frenos(huesos):
    """Lo que no se ve mirando la imagen: columna completa y en orden, y lateralidad."""
    faltan = [h for h in ORDEN_COLUMNA if h not in huesos]
    if faltan:
        raise SystemExit("ABORTA: faltan vértebras %s" % faltan)
    vs = [huesos[h]["v"] for h in ORDEN_COLUMNA]
    if any(b <= a for a, b in zip(vs, vs[1:])):
        raise SystemExit("ABORTA: la columna no baja en orden de C1 al sacro")
    if not huesos["femur_left"]["u"] > 0.5 > huesos["femur_right"]["u"]:
        raise SystemExit("ABORTA: vista en espejo (el fémur izquierdo debe caer a la derecha)")


def main():
    ap = argparse.ArgumentParser(description="Esqueleto de referencia (BodyParts3D) para la web")
    ap.add_argument("--bp3d", required=True, help="carpeta con los FJ*.obj de BodyParts3D")
    ap.add_argument("--salida", required=True)
    ap.add_argument("--ancho", type=int, default=880)
    a = ap.parse_args()
    piezas = carga(a.bp3d)
    proy = orienta(piezas)
    im, a_uv = render(piezas, proy, ancho=a.ancho)
    huesos = centroides(piezas, a_uv)
    frenos(huesos)
    os.makedirs(a.salida, exist_ok=True)
    im.save(os.path.join(a.salida, "esqueleto-anterior.png"), optimize=True)
    with open(os.path.join(a.salida, "esqueleto.json"), "w") as f:
        json.dump({"imagen": "esqueleto-anterior.png", "tamano": list(im.size), "huesos": huesos,
                   "fuente": ATRIBUCION}, f, ensure_ascii=False, indent=1)
    print("esqueleto de referencia: %d piezas · %d×%d px · %d huesos con centroide → %s"
          % (len(piezas), im.size[0], im.size[1], len(huesos), a.salida))


if __name__ == "__main__":
    main()
