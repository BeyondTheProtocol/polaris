#!/usr/bin/env python3
"""tools/laminillas_lector.py — el LECTOR ÚNICO de píxeles de las laminillas (plan, «Lector único»).

Toda lectura de píxeles de una lámina pasa por aquí, sobre OpenSlide. Prohibido `seek`+`decode`
manual y `page.decode` de tifffile (mueve el puntero: dos lecturas corruptas silenciosas, medido el
1-oct). Un descriptor OpenSlide por proceso (se reabre tras un `fork`). Niveles resueltos por
FACTOR o mpp, nunca por índice (`series[0]` agrupa mal). mpp del manifiesto si el lector no lo da.

Corre dentro de las jaulas (venv `patologia`): SESION es el cwd que fija la ventanilla
(`laminillas_comun.sesion()`); los tests la fijan con `configura(sesion=...)`.

API (fijada para el piloto, 1-oct-26):
  manifiesto()                              → dict del manifiesto de SESION
  abre(opaco)                               → Lamina (cacheada por proceso)
  Lamina.mpp_l0 · .dimensiones_l0 (w, h) · .niveles [(factor, mpp, (w, h))]
  lee_region(lamina, mpp, x_l0, y_l0, w, h) → np.ndarray uint8 (h, w, 3) RGB
      x, y en píxeles de L0; w, h en píxeles AL mpp pedido. Nivel nativo más cercano por debajo
      (el de mpp ≤ pedido más grueso; un mpp a ±1 % de un nivel nativo ES ese nivel, sin
      remuestreo); si hace falta, remuestreo por ÁREA. El relleno 255 se devuelve tal cual; lo que
      cae fuera de la imagen sale como 255 (relleno), nunca como negro.
  zona_escaneada(lamina)                    → shapely (Multi)Polygon en píxeles de L0
  i0_raster(lamina)                         → (array float32 (ny, nx, 3), {"paso_l0", "x0", "y0"})
  i0_local(lamina)                          → f(x_l0, y_l0) → RGB float32 (interpolación bilineal)

Los derivados (zona, I0) los produce la ingesta (`laminillas_ingesta.py`) y se registran en el
manifiesto con su sha256; aquí se verifica el sha256 al cargarlos.
"""
import hashlib
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TOL_NIVEL = 0.01            # un mpp a ±1 % de un nivel nativo se lee en ese nivel, sin remuestrear
RELLENO = 255

_SESION = None              # la fija `configura` (tests) o el cwd de la ventanilla
_ABIERTAS = {}              # opaco → Lamina, del proceso `_PID`
_PID = None


def configura(sesion=None):
    """Fija SESION (tests y herramientas fuera de la ventanilla). Cierra lo abierto."""
    global _SESION
    cierra_todo()
    _SESION = os.path.realpath(sesion) if sesion else None


def sesion():
    if _SESION:
        return _SESION
    import laminillas_comun as C
    return C.sesion()


def _sha256(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for trozo in iter(lambda: f.read(1 << 20), b""):
            h.update(trozo)
    return h.hexdigest()


def manifiesto():
    with open(os.path.join(sesion(), "manifiesto.json"), encoding="utf-8") as f:
        return json.load(f)


def _entrada(opaco, man=None):
    man = man or manifiesto()
    try:
        return man["laminas"][opaco]
    except KeyError:
        raise KeyError("%s no está en el manifiesto de SESION" % opaco)


def _ruta_en_sesion(rel):
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        raise ValueError("ruta del manifiesto no relativa a SESION")
    return os.path.join(sesion(), rel)


class Lamina:
    """Una lámina abierta con OpenSlide. No se construye a mano: `abre(opaco)`."""

    def __init__(self, opaco, ruta, mpp_manifiesto=None):
        import openslide
        self.opaco = opaco
        self.ruta = ruta
        self._os = openslide.OpenSlide(ruta)
        props = self._os.properties
        mpp = None
        try:
            mpp = float(props.get("openslide.mpp-x"))
        except (TypeError, ValueError):
            mpp = None
        if mpp_manifiesto:
            if mpp and abs(mpp - mpp_manifiesto) / mpp_manifiesto > 0.005:
                raise ValueError("%s: mpp del lector %.5f ≠ manifiesto %.5f"
                                 % (opaco, mpp, mpp_manifiesto))
            mpp = float(mpp_manifiesto)
        if not mpp:
            raise ValueError("%s: ni el lector ni el manifiesto dan el mpp" % opaco)
        self.mpp_l0 = mpp
        w0, h0 = self._os.level_dimensions[0]
        self.dimensiones_l0 = (int(w0), int(h0))
        niveles, self._idx = [], {}
        for k, (w, h) in enumerate(self._os.level_dimensions):
            real = w0 / float(w)
            factor = int(round(real))
            if factor < 1 or abs(real - factor) / factor > 0.01:
                continue                      # nivel no entero: no se usa (nunca por índice)
            if factor in self._idx:
                continue
            self._idx[factor] = k
            niveles.append((factor, mpp * real, (int(w), int(h))))
        niveles.sort()
        if not niveles or niveles[0][0] != 1:
            raise ValueError("%s: sin nivel L0 reconocible" % opaco)
        self.niveles = niveles

    def nivel(self, factor):
        """(factor, mpp, (w, h)) del nivel de ese factor, o KeyError."""
        for n in self.niveles:
            if n[0] == factor:
                return n
        raise KeyError("%s: sin nivel ×%s" % (self.opaco, factor))

    def nivel_para(self, mpp):
        """Nivel nativo para leer a `mpp`: el más grueso con mpp ≤ pedido (±1 %)."""
        if mpp <= 0:
            raise ValueError("mpp ≤ 0")
        if mpp < self.mpp_l0 * (1 - TOL_NIVEL):
            raise ValueError("%s: mpp %.4f más fino que L0 (%.4f): no se sobremuestrea"
                             % (self.opaco, mpp, self.mpp_l0))
        validos = [n for n in self.niveles if n[1] <= mpp * (1 + TOL_NIVEL)]
        return max(validos, key=lambda n: n[1])

    def _lee_nativo(self, factor, x_l0, y_l0, w, h):
        """RGB uint8 (h, w, 3) del nivel `factor`, origen en L0. Fuera de imagen → 255."""
        import numpy as np
        k = self._idx[factor]
        img = self._os.read_region((int(x_l0), int(y_l0)), k, (int(w), int(h)))
        a = np.asarray(img)
        rgb = np.ascontiguousarray(a[..., :3])
        transparente = a[..., 3] == 0
        if transparente.any():
            rgb[transparente] = RELLENO
        return rgb

    def cierra(self):
        try:
            self._os.close()
        except Exception:                         # noqa: BLE001
            pass

    def __repr__(self):
        return "Lamina(%s, mpp_l0=%.4f, %dx%d, niveles=%s)" % (
            self.opaco, self.mpp_l0, self.dimensiones_l0[0], self.dimensiones_l0[1],
            [n[0] for n in self.niveles])


def abre(opaco):
    """Lamina del manifiesto de SESION. Un descriptor por proceso: tras un fork se reabre."""
    global _PID
    if _PID != os.getpid():
        _ABIERTAS.clear()                         # no se cierran: son del padre
        _PID = os.getpid()
    if opaco in _ABIERTAS:
        return _ABIERTAS[opaco]
    ent = _entrada(opaco)
    fichero = ent["fichero"]
    if "/" in fichero:
        raise ValueError("fichero del manifiesto con ruta")
    lam = Lamina(opaco, _ruta_en_sesion(fichero), ent.get("mpp"))
    _ABIERTAS[opaco] = lam
    return lam


def cierra_todo():
    for lam in list(_ABIERTAS.values()):
        if _PID == os.getpid():
            lam.cierra()
    _ABIERTAS.clear()


def _como_lamina(lamina):
    return abre(lamina) if isinstance(lamina, str) else lamina


def lee_region(lamina, mpp, x_l0, y_l0, w, h):
    """RGB uint8 (h, w, 3) de la región que empieza en (x_l0, y_l0) de L0 y mide w×h píxeles al
    `mpp` pedido. Remuestreo por área si el mpp no es nativo."""
    import numpy as np
    lam = _como_lamina(lamina)
    w, h = int(w), int(h)
    if w <= 0 or h <= 0:
        raise ValueError("región vacía")
    factor, mpp_n, _ = lam.nivel_para(mpp)
    if abs(mpp_n - mpp) / mpp <= TOL_NIVEL:
        return lam._lee_nativo(factor, x_l0, y_l0, w, h)
    escala = mpp / mpp_n                          # > 1: píxeles nativos por píxel pedido
    wn, hn = int(math.ceil(w * escala)), int(math.ceil(h * escala))
    nativo = lam._lee_nativo(factor, x_l0, y_l0, wn, hn)
    # Área exacta: se recorta al múltiplo que corresponde y se promedia por bloques cuando el
    # cociente es entero; si no, INTER_AREA de OpenCV (mismo criterio, sub-píxel).
    ent = round(escala)
    if abs(escala - ent) < 1e-6 and ent >= 1:
        b = nativo[:h * ent, :w * ent].reshape(h, ent, w, ent, 3).astype(np.float32)
        return np.clip(np.rint(b.mean(axis=(1, 3))), 0, 255).astype(np.uint8)
    import cv2
    return cv2.resize(nativo, (w, h), interpolation=cv2.INTER_AREA)


def lee_nivel(lamina, factor, franja=2048):
    """Nivel nativo ENTERO (h, w, 3), leído por franjas para no duplicar el pico de memoria."""
    import numpy as np
    lam = _como_lamina(lamina)
    f, _, (w, h) = lam.nivel(factor)
    out = np.empty((h, w, 3), np.uint8)
    for y in range(0, h, franja):
        hh = min(franja, h - y)
        out[y:y + hh] = lam._lee_nativo(f, 0, y * f, w, hh)
    return out


# ── derivados de la ingesta ─────────────────────────────────────────────────────────────────
def _derivado(lamina, clave):
    lam = _como_lamina(lamina)
    ent = _entrada(lam.opaco).get(clave)
    if not ent:
        raise KeyError("%s: el manifiesto no tiene «%s» (¿falta la ingesta?)" % (lam.opaco, clave))
    ruta = _ruta_en_sesion(ent["fichero"])
    if _sha256(ruta) != ent["sha256"]:
        raise ValueError("%s: %s con sha256 distinto del manifiesto" % (lam.opaco, clave))
    return ruta, ent


def zona_escaneada(lamina):
    """Polígono de la zona escaneada (teselas L0 que no son relleno), en píxeles de L0."""
    from shapely.geometry import shape
    ruta, _ = _derivado(lamina, "zona_escaneada")
    with open(ruta, encoding="utf-8") as f:
        g = json.load(f)
    if g.get("type") == "Feature":
        g = g["geometry"]
    return shape(g)


def i0_raster(lamina):
    """(I0 float32 (ny, nx, 3), transform). La celda (i, j) tiene su centro en
    (x0 + (j + 0,5)·paso_l0, y0 + (i + 0,5)·paso_l0) de L0."""
    import numpy as np
    ruta, _ = _derivado(lamina, "i0")
    with np.load(ruta) as z:
        arr = z["i0"].astype(np.float32)
        paso = float(z["paso_l0"])
        x0 = float(z["x0"]) if "x0" in z else 0.0
        y0 = float(z["y0"]) if "y0" in z else 0.0
    return arr, {"paso_l0": paso, "x0": x0, "y0": y0}


def interpola_i0(arr, tf):
    """f(x_l0, y_l0) → RGB float32 con forma (..., 3); bilineal, con borde replicado."""
    import numpy as np
    ny, nx = arr.shape[:2]
    paso, x0, y0 = tf["paso_l0"], tf["x0"], tf["y0"]

    def f(x, y):
        x = np.asarray(x, np.float64)
        y = np.asarray(y, np.float64)
        u = np.clip((x - x0) / paso - 0.5, 0, nx - 1)
        v = np.clip((y - y0) / paso - 0.5, 0, ny - 1)
        j0 = np.floor(u).astype(int)
        i0 = np.floor(v).astype(int)
        j1 = np.minimum(j0 + 1, nx - 1)
        i1 = np.minimum(i0 + 1, ny - 1)
        du = (u - j0)[..., None]
        dv = (v - i0)[..., None]
        a = arr[i0, j0] * (1 - du) + arr[i0, j1] * du
        b = arr[i1, j0] * (1 - du) + arr[i1, j1] * du
        return (a * (1 - dv) + b * dv).astype(np.float32)
    return f


def i0_local(lamina):
    arr, tf = i0_raster(lamina)
    return interpola_i0(arr, tf)
