"""tests/_laminillas_sinteticas.py — láminas SINTÉTICAS para los tests del piloto (módulo B).

Nada real: ni píxeles, ni nombres, ni accesiones. Un «bloque» de tejido inventado (fragmentos con
nidos epiteliales, luces y estroma) del que salen cortes seriados; cada corte se «escanea» con una
transformada euclídea CONOCIDA (rotación, espejo, traslación) y se pinta bajo demanda a cualquier
mpp por Beer-Lambert (hematoxilina + DAB + fondo) sobre un vidrio con I0 local y con un bloque de
relleno 255 fuera de la zona escaneada, como el Grundium.

`LectorSintetico` imita la API del lector único (`tools/laminillas_lector.py`): abre, lee_region,
zona_escaneada, i0_local, manifiesto. Los tests lo inyectan: el código de medida nunca lee píxeles
por su cuenta.

Convención: T_k (3×3, µm) lleva coordenadas de la lámina k a las del mundo = las de la referencia
(P-CK19 tiene T = identidad). El registro de la móvil k contra la referencia debe recuperar T_k.
"""
import math

import numpy as np
from scipy import ndimage as ndi
from shapely import affinity
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

MPP_L0 = 0.2506
FACTORES = (1, 4, 8)
RUIFROK_H = np.array([0.65, 0.70, 0.29])
RUIFROK_DAB = np.array([0.27, 0.57, 0.78])
RUIFROK_E = np.array([0.07, 0.99, 0.11])
TESELA_L0 = 512
PASO_CAMPO_UM = 2.0          # resolución de los campos suaves del mundo


def _unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


VH, VDAB, VE = _unit(RUIFROK_H), _unit(RUIFROK_DAB), _unit(RUIFROK_E)


def matriz(angulo_grados=0.0, tx=0.0, ty=0.0, espejo=False):
    """Matriz 3×3 que primero refleja (x → -x si espejo), luego rota y traslada."""
    a = math.radians(angulo_grados)
    c, s = math.cos(a), math.sin(a)
    R = np.array([[c, -s, tx], [s, c, ty], [0, 0, 1.0]])
    F = np.diag([-1.0, 1.0, 1.0]) if espejo else np.eye(3)
    return R @ F


def aplica(M, xy):
    xy = np.asarray(xy, float)
    h = np.c_[xy, np.ones(len(xy))]
    return (h @ M.T)[:, :2]


def _poligono_a(M, geom):
    a, b, tx = M[0]
    d, e, ty = M[1]
    return affinity.affine_transform(geom, [a, b, d, e, tx, ty])


class Bloque:
    """El bloque de tejido en coordenadas del mundo (µm)."""

    def __init__(self, semilla=11, ancho_um=3400.0, alto_um=2600.0, con_pequenos=True,
                 fragmentos=None):
        rng = np.random.default_rng(semilla)
        self.ancho, self.alto = ancho_um, alto_um
        frs = list(fragmentos) if fragmentos is not None else [
            LineString([(450, 500), (1200, 430), (1900, 600), (2400, 520)]).buffer(300),
            LineString([(450, 1400), (1300, 1540), (2250, 1480)]).buffer(280),
        ]
        if con_pequenos and fragmentos is None:
            # «pequeño verificable» (~0,55×0,75 mm) y «diminuto» (~0,25 mm): reglas de fragmento
            frs.append(box(2850, 1000, 3100, 1450).buffer(100))
            frs.append(Point(3000, 2150).buffer(120))
        lisos = []
        for f in frs:                      # bordes algo irregulares
            borde = np.asarray(f.exterior.coords)
            extra = [Point(*borde[int(i)]).buffer(rng.uniform(40, 110))
                     for i in rng.choice(len(borde) - 1, 6, replace=False)]
            lisos.append(unary_union([f] + extra).simplify(2.0))
        self.fragmentos = lisos
        nx, ny = int(ancho_um / PASO_CAMPO_UM), int(alto_um / PASO_CAMPO_UM)
        g1 = ndi.gaussian_filter(rng.standard_normal((ny, nx)), 30 / PASO_CAMPO_UM)
        g2 = ndi.gaussian_filter(rng.standard_normal((ny, nx)), 30 / PASO_CAMPO_UM)
        self.g1, self.g2 = g1 / g1.std(), g2 / g2.std()
        self.tejido = self._raster(self.fragmentos)
        # textura del estroma (colágeno), persistente entre cortes: rasgos a 5-20 µm para SIFT
        t = ndi.gaussian_filter(rng.standard_normal((ny, nx)), 5 / PASO_CAMPO_UM)
        self.textura = (t / t.std()).astype(np.float32)
        # luces (ductos): fijas a lo largo de la serie
        luces = []
        for _ in range(60):
            x, y = rng.uniform(0, ancho_um), rng.uniform(0, alto_um)
            r = rng.uniform(25, 70)
            if any(f.buffer(-r - 20).contains(Point(x, y)) for f in self.fragmentos):
                luces.append(Point(x, y).buffer(r))
        self.luces = luces
        self.luz = self._raster(luces) if luces else np.zeros_like(self.tejido)

    def _raster(self, geoms):
        from skimage.draw import polygon as dpoly
        ny, nx = self.g1.shape
        m = np.zeros((ny, nx), bool)
        for g in geoms:
            for p in getattr(g, "geoms", [g]):
                xs, ys = np.asarray(p.exterior.coords).T / PASO_CAMPO_UM
                rr, cc = dpoly(ys, xs, (ny, nx))
                m[rr, cc] = True
        return m

    def epitelio(self, fase):
        g = math.cos(fase) * self.g1 + math.sin(fase) * self.g2
        return (g > 0.35) & self.tejido & ~self.luz


class Corte:
    """Un corte del bloque, montado y escaneado con la transformada conocida `T` (lámina→mundo)."""

    def __init__(self, bloque, nombre, fase, semilla, angulo=0.0, espejo=False, ki67=0.0,
                 ck19=False, dab_nuclear=0.6, densidad_epi=0.008, densidad_estroma=0.0015,
                 margen_um=150.0, previo=None, persistencia=0.6):
        """`previo`: el corte anterior de la serie; cada núcleo suyo sigue con probabilidad
        `persistencia` (un núcleo de 6-10 µm cruza cortes de 3-4 µm) y el resto es nuevo."""
        self.bloque, self.nombre = bloque, nombre
        self.fase = fase
        rng = np.random.default_rng(semilla)
        epi = bloque.epitelio(fase)
        self.epi = epi
        tej = bloque.tejido & ~bloque.luz
        lam = np.where(epi, densidad_epi, np.where(tej, densidad_estroma, 0.0))
        if previo is not None:
            lam = lam * (1.0 - persistencia)
        cuenta = rng.poisson(lam * PASO_CAMPO_UM ** 2)
        iy, ix = np.nonzero(cuenta)
        rep = cuenta[iy, ix]
        iy, ix = np.repeat(iy, rep), np.repeat(ix, rep)
        x = (ix + rng.random(len(ix))) * PASO_CAMPO_UM
        y = (iy + rng.random(len(iy))) * PASO_CAMPO_UM
        nuevos = np.c_[x, y]
        if previo is not None:
            sigue = rng.random(len(previo.nucleos_mundo)) < persistencia
            viejos = previo.nucleos_mundo[sigue] + rng.normal(0, 1.0, (int(sigue.sum()), 2))
            nuevos = np.r_[viejos, nuevos]
        self.nucleos_mundo = nuevos
        ny, nx = epi.shape
        iy = np.clip((nuevos[:, 1] / PASO_CAMPO_UM).astype(int), 0, ny - 1)
        ix = np.clip((nuevos[:, 0] / PASO_CAMPO_UM).astype(int), 0, nx - 1)
        x = nuevos[:, 0]
        self.en_epitelio = epi[iy, ix]
        self.h_amp = rng.uniform(0.45, 0.75, len(x))
        pos = self.en_epitelio & (rng.random(len(x)) < ki67)
        self.dab_amp = np.where(pos, dab_nuclear, 0.0)
        self.ck19 = ck19
        # mundo → lámina: refleja/rota alrededor del centro y deja margen
        cx, cy = bloque.ancho / 2, bloque.alto / 2
        A = matriz(-angulo) @ (np.diag([-1.0, 1, 1]) if espejo else np.eye(3)) @ matriz(0, -cx, -cy)
        esquinas = aplica(A, [(0, 0), (bloque.ancho, 0), (0, bloque.alto), (bloque.ancho, bloque.alto)])
        mn = esquinas.min(0)
        A = matriz(0, margen_um - mn[0], margen_um - mn[1]) @ A
        self.A = A                         # mundo → lámina (µm)
        self.T = np.linalg.inv(A)          # lámina → mundo: lo que el registro debe recuperar
        mx = aplica(A, esquinas_mundo(bloque)).max(0) + margen_um
        self.dim_l0 = (int(math.ceil(mx[0] / MPP_L0)), int(math.ceil(mx[1] / MPP_L0)))
        self.nucleos = aplica(A, self.nucleos_mundo)          # µm en la lámina
        # zona escaneada: rectángulo menos un bloque de 4×4 teselas en una esquina (relleno 255)
        W, H = self.dim_l0
        ancho_rell = 4 * TESELA_L0
        self.zona = box(0, 0, W, H).difference(box(W - ancho_rell, 0, W, ancho_rell))

    # ── píxeles ────────────────────────────────────────────────────────────────────────────
    def vidrio(self, xs_um, ys_um):
        """I0 local: vidrio ≈244 con un gradiente suave (como el medido, 241-253)."""
        g = 2.0 * (xs_um / 3000.0) - 1.0 * (ys_um / 3000.0)
        base = np.array([244.0, 243.0, 246.0])
        return base[None, None, :] + g[..., None]

    def pinta(self, mpp, x0_l0, y0_l0, w, h):
        x0, y0 = x0_l0 * MPP_L0, y0_l0 * MPP_L0
        xs = x0 + (np.arange(w) + 0.5) * mpp
        ys = y0 + (np.arange(h) + 0.5) * mpp
        XX, YY = np.meshgrid(xs, ys)
        mundo = aplica(self.T, np.c_[XX.ravel(), YY.ravel()])
        coords = [mundo[:, 1] / PASO_CAMPO_UM - 0.5, mundo[:, 0] / PASO_CAMPO_UM - 0.5]
        tej = ndi.map_coordinates((self.bloque.tejido & ~self.bloque.luz).astype(np.float32),
                                  coords, order=1, cval=0).reshape(h, w)
        tex = ndi.map_coordinates(self.bloque.textura, coords, order=1, cval=0).reshape(h, w)
        E = (0.10 + 0.05 * np.clip(tex, -2, 2)) * tej
        D = np.zeros((h, w))
        if self.ck19:
            epi = ndi.map_coordinates(ndi.gaussian_filter(self.epi.astype(np.float32), 1.0),
                                      coords, order=1, cval=0).reshape(h, w)
            D += 0.45 * epi
        Hn = np.zeros((h, w))
        Dn = np.zeros((h, w))
        sig_um = 2.0
        sel = ((self.nucleos[:, 0] > x0 - 4 * sig_um) & (self.nucleos[:, 0] < x0 + w * mpp + 4 * sig_um)
               & (self.nucleos[:, 1] > y0 - 4 * sig_um) & (self.nucleos[:, 1] < y0 + h * mpp + 4 * sig_um))
        px = (self.nucleos[sel, 0] - x0) / mpp - 0.5
        py = (self.nucleos[sel, 1] - y0) / mpp - 0.5
        ix, iy = np.clip(np.round(px).astype(int), 0, w - 1), np.clip(np.round(py).astype(int), 0, h - 1)
        s_px = sig_um / mpp
        norm = 2 * math.pi * max(s_px, 0.7) ** 2
        np.add.at(Hn, (iy, ix), self.h_amp[sel] * norm)
        np.add.at(Dn, (iy, ix), self.dab_amp[sel] * norm)
        Hn = ndi.gaussian_filter(Hn, max(s_px, 0.7))
        Dn = ndi.gaussian_filter(Dn, max(s_px, 0.7))
        D = D + Dn
        od = (Hn[..., None] * VH + D[..., None] * VDAB + E[..., None] * VE)
        rgb = self.vidrio(XX, YY) * np.power(10.0, -od)
        rng = np.random.default_rng(int(x0_l0 * 7 + y0_l0 * 13 + w) % (2 ** 32))
        rgb = rgb + rng.normal(0, 1.0, rgb.shape)
        out = np.clip(np.round(rgb), 0, 255).astype(np.uint8)
        # relleno exacto 255 fuera de la zona escaneada (por teselas L0)
        W, H = self.dim_l0
        ancho_rell = 4 * TESELA_L0
        Xl0, Yl0 = XX / MPP_L0, YY / MPP_L0
        rell = (Xl0 >= W - ancho_rell) & (Yl0 < ancho_rell)
        out[rell] = 255
        return out


def esquinas_mundo(b):
    return [(0, 0), (b.ancho, 0), (0, b.alto), (b.ancho, b.alto)]


class LaminaSint:
    """Como `laminillas_lector.Lamina`: .opaco, .mpp_l0, .dimensiones_l0, .niveles
    [(factor, mpp, (w, h))]."""

    def __init__(self, corte):
        self.opaco = self.nombre = corte.nombre
        self.mpp_l0 = MPP_L0
        self.dimensiones_l0 = corte.dim_l0
        W, H = corte.dim_l0
        self.niveles = [(f, MPP_L0 * f, (W // f, H // f)) for f in FACTORES]
        self._c = corte


class LectorSintetico:
    """API del lector único, sobre cortes sintéticos. Cuenta lecturas (para los tests)."""

    def __init__(self, cortes, cache_niveles=(4, 8)):
        self._cortes = {c.nombre: c for c in cortes}
        self.lecturas = 0
        self._cache = {}
        self._cache_niveles = cache_niveles

    def abre(self, nombre):
        return LaminaSint(self._cortes[nombre])

    def lee_region(self, lamina, mpp, x_l0, y_l0, w, h):
        if not any(abs(mpp - n[1]) < 1e-9 for n in lamina.niveles):
            raise ValueError("mpp no nativo: %r" % mpp)
        self.lecturas += 1
        f = int(round(mpp / MPP_L0))
        if f not in self._cache_niveles:
            return lamina._c.pinta(mpp, x_l0, y_l0, int(w), int(h))
        # como OpenSlide en un nivel reducido: el origen L0 se redondea a la rejilla del nivel
        clave = (lamina.nombre, f)
        if clave not in self._cache:
            W, H = lamina.dimensiones_l0
            self._cache[clave] = lamina._c.pinta(mpp, 0, 0, W // f, H // f)
        full = self._cache[clave]
        c0, r0 = int(round(x_l0 / f)), int(round(y_l0 / f))
        out = np.full((int(h), int(w), 3), 255, np.uint8)
        rs, cs = max(0, r0), max(0, c0)
        re_, ce = min(full.shape[0], r0 + int(h)), min(full.shape[1], c0 + int(w))
        if re_ > rs and ce > cs:
            out[rs - r0:re_ - r0, cs - c0:ce - c0] = full[rs:re_, cs:ce]
        return out

    def zona_escaneada(self, lamina):
        return lamina._c.zona

    def i0_local(self, lamina):
        """Como el lector: f(x_l0, y_l0) → RGB float (..., 3)."""
        c = lamina._c

        def f(x, y):
            return c.vidrio(np.asarray(x, float) * MPP_L0, np.asarray(y, float) * MPP_L0)
        return f

    def manifiesto(self):
        return {"laminas": {n: {"mpp": MPP_L0} for n in self._cortes}}

    # para los tests: centroides nucleares «de InstanSeg» en px L0 de cada lámina
    def centroides_l0(self, nombre):
        return self._cortes[nombre].nucleos / MPP_L0


def serie(semilla=11, especificacion=None, con_pequenos=True, bloque=None):
    """Bloque + cortes. `especificacion`: lista de dicts (nombre, fase, angulo, espejo, ...)."""
    b = bloque or Bloque(semilla, con_pequenos=con_pequenos)
    hechos = {}
    previo = None
    # se generan en orden de fase (la serie física) y se devuelven en el orden pedido
    for i in sorted(range(len(especificacion)), key=lambda k: especificacion[k].get("fase", 0.0)):
        e = dict(especificacion[i])
        c = Corte(b, e.pop("nombre"), e.pop("fase", 0.0), semilla * 100 + i, previo=previo, **e)
        hechos[i] = previo = c
    cortes = [hechos[i] for i in range(len(especificacion))]
    return b, cortes, LectorSintetico(cortes)


def rgb_a_ycbcr(a):
    """JFIF (rango completo), como lo deshace libjpeg/libtiff al leer."""
    a = a.astype(np.float32)
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    Y = 0.299 * R + 0.587 * G + 0.114 * B
    Cb = 128 - 0.168736 * R - 0.331264 * G + 0.5 * B
    Cr = 128 + 0.5 * R - 0.418688 * G - 0.081312 * B
    return np.clip(np.round(np.stack([Y, Cb, Cr], -1)), 0, 255).astype(np.uint8)


# ── TIFF piramidal tipo Grundium + manifiesto de SESION, para el lector REAL ──────────────────
def escribe_sesion(sesion, cortes, tesela=512):
    """Escribe cada corte como BigTIFF ×1/×4/×8 (JPEG YCbCr 4:2:0, teselas 512, resolución en cm
    solo en L0) con sus derivados de ingesta (zona escaneada GeoJSON, I0 npz) y el manifiesto,
    en el formato que lee `laminillas_lector`. L0 = ×4 repetido (el registro no lee L0)."""
    import hashlib
    import json
    import os
    import tifffile
    from shapely.geometry import mapping

    def sha(r):
        return hashlib.sha256(open(r, "rb").read()).hexdigest()
    man = {"laminas": {}}
    for c in cortes:
        W8 = int(math.ceil(c.dim_l0[0] / 8.0))
        H8 = int(math.ceil(c.dim_l0[1] / 8.0))
        c.dim_l0 = (8 * W8, 8 * H8)
        ancho_rell = 4 * TESELA_L0
        W, H = c.dim_l0
        c.zona = box(0, 0, W, H).difference(box(W - ancho_rell, 0, W, ancho_rell))
        x8 = c.pinta(MPP_L0 * 8, 0, 0, W8, H8)
        x4 = c.pinta(MPP_L0 * 4, 0, 0, 2 * W8, 2 * H8)
        x1 = np.repeat(np.repeat(x4, 4, 0), 4, 1)
        ruta = os.path.join(sesion, c.nombre + ".tiff")
        px_cm = 1e4 / MPP_L0
        with tifffile.TiffWriter(ruta, bigtiff=True) as tw:
            for f, img in ((1, x1), (4, x4), (8, x8)):
                # tifffile con photometric='ycbcr' espera los datos YA en YCbCr: si se le dan en
                # RGB, OpenSlide y tifffile leen el vidrio como (255, 120, 255) (medido 1-oct)
                kw = dict(tile=(tesela, tesela), compression="jpeg", compressionargs={"level": 92},
                          photometric="ycbcr", subsampling=(2, 2), metadata=None)
                img = rgb_a_ycbcr(img)
                if f == 1:
                    kw.update(resolution=(px_cm, px_cm), resolutionunit="CENTIMETER",
                              description="SINTETICO-L0")
                else:
                    kw.update(subfiletype=1)
                tw.write(img, **kw)
        del x1
        zona = os.path.join(sesion, c.nombre + ".zona.json")
        with open(zona, "w") as f:
            json.dump(mapping(c.zona), f)
        paso = 512
        ny, nx = int(math.ceil(H / paso)), int(math.ceil(W / paso))
        jj, ii = np.meshgrid(np.arange(nx), np.arange(ny))
        i0 = c.vidrio((jj + 0.5) * paso * MPP_L0, (ii + 0.5) * paso * MPP_L0).astype(np.float32)
        i0r = os.path.join(sesion, c.nombre + ".i0.npz")
        np.savez(i0r, i0=i0, paso_l0=paso, x0=0.0, y0=0.0)
        man["laminas"][c.nombre] = {
            "fichero": c.nombre + ".tiff", "sha256": sha(ruta), "mpp": MPP_L0,
            "zona_escaneada": {"fichero": c.nombre + ".zona.json", "sha256": sha(zona)},
            "i0": {"fichero": c.nombre + ".i0.npz", "sha256": sha(i0r)}}
    with open(os.path.join(sesion, "manifiesto.json"), "w") as f:
        json.dump(man, f)
    return man
