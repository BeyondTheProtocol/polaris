#!/usr/bin/env python3
"""tools/laminillas_humo.py — F2 «Humo sin datos» y «Criterio de salida» (sin un solo píxel real).

Lo lanza SOLO la ventanilla (`lector_clinico.py procesa laminillas_humo` / `laminillas_humo_valis`)
dentro de `analisis.sb` REAL: sin red, sin Llavero, sin zona clínica salvo SESION, entorno en lista
blanca y `HF_HUB_OFFLINE=1`. Escribe solo en `SESION/humo/`.

1. Fabrica un TIFF sintético con la estructura del Grundium (BigTIFF; niveles ×1/×4/×8; teselas de
   512 JPEG YCbCr 4:2:0; `NewSubfileType=1` en las reducidas; resolución en cm solo en L0, 0,2506
   µm/px; bloque de relleno 255) y una variante con texto rasterizado. Lleva parches de color
   conocido (`PARCHES`) que deben volver con ≤3 niveles por canal (`prueba_color`).
2. Lo leen OpenSlide (3 niveles, mpp 0,2506), wsidata/LazySlide (teselado a mpp 0,5) y WSInfer.
3. Cada modelo carga OFFLINE y hace un forward sobre una tesela sintética (MPS si hay; si no, CPU,
   y se dice). InstanSeg, además, por `zs.seg.cells` sobre el TIFF entero.
Modo `valis` (venv valis): VALIS lee el TIFF con VipsSlideReader, sin JVM, y no lo da por
«flattened»; DISK + LightGlue cargan offline y emparejan dos teselas.
Modo `registro-valis` (`procesa laminillas_humo -- registro-valis`, venv patologia): la ruta REAL
del respaldo (4) del registro, `laminillas_registro.respaldo_valis` → subproceso al venv valis,
dentro de `analisis`, sobre dos pares sintéticos con transformada conocida (directo y espejo):
VALIS corre y la matriz recupera la verdad con error < 1 px (2 µm). Ese camino no lo había
ejercitado nadie hasta el 2-oct-26 (el piloto real lo gastó sin que VALIS corriera).

Salida: una línea por prueba («OK»/«FALLO») y un JSON en `SESION/humo/humo-<modo>.json`.
"""
import json
import os
import sys
import time
import traceback

MPP = 0.2506
L0 = (4096, 3072)          # ancho, alto (pequeño: es humo)
TESELA = 512
RES = []
# Parches de color conocido, planos, fuera del tejido y del relleno, alineados a 64 px (bloques
# JPEG y píxeles enteros en ×4 y ×8): (x0, y0) en L0, lado, RGB. La prueba de color los relee.
PARCHES = {"hematoxilina": ((3328, 1280), 256, (80, 60, 140)),
           "eosina": ((3648, 1280), 256, (225, 160, 200)),
           "dab": ((3328, 1664), 256, (150, 100, 50)),
           "vidrio": ((3648, 1664), 256, (244, 244, 244))}
TOL_COLOR = 3              # niveles por canal (la conversión JFIF + JPEG 90 da 0-1 en parches planos)


def _nota(prueba, ok, detalle="", cae_declarado=False):
    RES.append({"prueba": prueba, "ok": bool(ok), "cae_declarado": bool(cae_declarado and not ok),
                "detalle": str(detalle)[:400]})
    etiqueta = "OK" if ok else ("CAE" if cae_declarado else "FALLO")
    print("%-5s %-38s %s" % (etiqueta, prueba, str(detalle)[:200]), flush=True)


def _prueba(nombre, cae_declarado=False):
    """`cae_declarado`: el plan admite que esta pieza caiga si se declara (F2, «Criterio de
    salida»: WSInfer cae a su TorchScript sobre teselas de LazySlide). Sale como «CAE», no cuenta
    como fallo del humo, y queda en el JSON con su motivo."""
    def deco(f):
        def envuelta(*a, **k):
            t = time.time()
            try:
                det = f(*a, **k)
                _nota(nombre, True, "%s (%.1f s)" % (det or "", time.time() - t))
            except Exception as e:                       # noqa: BLE001
                _nota(nombre, False, "%s: %s" % (type(e).__name__, e), cae_declarado)
                traceback.print_exc(file=sys.stderr)
            finally:
                _vacia()                 # un modelo a la vez: el siguiente no se suma al anterior
        return envuelta
    return deco


# ── 1. TIFF sintético ──────────────────────────────────────────────────────────────────────────
def imagen_sintetica(texto=False, semilla=0):
    import numpy as np
    rng = np.random.default_rng(semilla)
    w, h = L0
    img = np.full((h, w, 3), 244, np.uint8)                       # vidrio ≈244
    yy, xx = np.mgrid[0:h, 0:w]
    tejido = ((xx - w * 0.45) / (w * 0.35)) ** 2 + ((yy - h * 0.5) / (h * 0.38)) ** 2 < 1
    img[tejido] = (225, 160, 200)                                 # eosina
    # «núcleos»: discos de hematoxilina de ~7 µm (28 px a 0,25 µm/px)
    for _ in range(2500):
        cx, cy = int(rng.uniform(0, w)), int(rng.uniform(0, h))
        if not tejido[cy, cx]:
            continue
        r = int(rng.uniform(10, 16))
        y0, y1, x0, x1 = max(cy - r, 0), min(cy + r, h), max(cx - r, 0), min(cx + r, w)
        m = (yy[y0:y1, x0:x1] - cy) ** 2 + (xx[y0:y1, x0:x1] - cx) ** 2 < r * r
        img[y0:y1, x0:x1][m] = (80, 60, 140)
    img = np.clip(img.astype(np.int16) + rng.integers(-6, 7, img.shape), 0, 255).astype(np.uint8)
    img[: TESELA * 2, w - TESELA * 2:] = 255                       # relleno exacto (no escaneado)
    for (x0, y0), lado, rgb in PARCHES.values():                    # color conocido, sin ruido
        img[y0:y0 + lado, x0:x0 + lado] = rgb
    if texto:
        from PIL import Image, ImageDraw, ImageFont
        pil = Image.fromarray(img)
        d = ImageDraw.Draw(pil)
        try:
            fuente = ImageFont.load_default(size=200)
        except TypeError:
            fuente = ImageFont.load_default()
        d.text((200, h - 400), "SINT 12345", fill=(20, 20, 20), font=fuente)
        img = np.asarray(pil)
    return img


def rgb_a_ycbcr(img):
    """RGB → YCbCr de JFIF (rango completo, el que leen libjpeg/OpenSlide con
    ReferenceBlackWhite 0-255/128±127)."""
    import numpy as np
    f = img.astype(np.float64)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128.0 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128.0 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return np.clip(np.rint(np.stack([y, cb, cr], axis=-1)), 0, 255).astype(np.uint8)


def escribe_grundium(ruta, img):
    """`img` en RGB. tifffile con photometric='ycbcr' escribe los datos TAL CUAL como YCbCr (pasa
    colorspace='YCBCR' al codificador JPEG: no convierte); dárselos en RGB dejaba el vidrio en
    (255, 121, 255) para OpenSlide y tifffile (medido 1-oct-26 y 2-oct-26). Se convierte antes, por
    nivel, tras reducir en RGB. `prueba_color` lo vigila."""
    import numpy as np
    import tifffile
    from skimage.transform import downscale_local_mean
    px_cm = 1e4 / MPP
    with tifffile.TiffWriter(ruta, bigtiff=True) as tw:
        for i, f in enumerate((1, 4, 8)):
            nivel = img if f == 1 else downscale_local_mean(img, (f, f, 1)).astype(np.uint8)
            nivel = rgb_a_ycbcr(nivel)
            kw = dict(tile=(TESELA, TESELA), compression="jpeg", compressionargs={"level": 90},
                      photometric="ycbcr", subsampling=(2, 2), metadata=None)
            if f == 1:
                kw.update(resolution=(px_cm, px_cm), resolutionunit="CENTIMETER",
                          description="SINTETICO-L0")      # tag 270: 13 caracteres, solo L0
            else:
                kw.update(subfiletype=1)
            tw.write(nivel, **kw)


# ── 2. lectores ────────────────────────────────────────────────────────────────────────────────
def prueba_openslide(ruta):
    import openslide
    s = openslide.OpenSlide(ruta)
    n, mpp = s.level_count, float(s.properties.get("openslide.mpp-x", "nan"))
    region = s.read_region((0, 0), 0, (256, 256))
    s.close()
    assert n == 3, "niveles=%d" % n
    assert abs(mpp - MPP) < 1e-3, "mpp=%r" % mpp
    return "3 niveles, mpp %.4f, region %s" % (mpp, region.size)


def prueba_color(ruta, parches=None, tol=TOL_COLOR):
    """Cada parche de color conocido vuelve con ≤ `tol` niveles por canal (mediana del interior)
    leído por OpenSlide en ×1 y ×8 y por tifffile en ×1. Sin esto, un TIFF con los colores
    corruptos pasaba el humo: solo se miraban la estructura y que hubiera núcleos."""
    import numpy as np
    import openslide
    import tifffile
    parches = PARCHES if parches is None else parches
    s = openslide.OpenSlide(ruta)
    with tifffile.TiffFile(ruta) as t:
        l0 = t.pages[0].asarray()
    peor, malos = 0, []
    try:
        for nombre, ((x0, y0), lado, rgb) in parches.items():
            lecturas = {}
            m = lado // 8                                   # margen: fuera los bordes del bloque
            reg = s.read_region((x0 + m, y0 + m), 0, (lado - 2 * m, lado - 2 * m))
            lecturas["openslide x1"] = np.asarray(reg.convert("RGB"))
            k = s.get_best_level_for_downsample(8)
            f = s.level_downsamples[k]
            reg = s.read_region((x0 + m, y0 + m), k,
                                (int((lado - 2 * m) / f), int((lado - 2 * m) / f)))
            lecturas["openslide nivel %d (x%g)" % (k, f)] = np.asarray(reg.convert("RGB"))
            lecturas["tifffile x1"] = l0[y0 + m:y0 + lado - m, x0 + m:x0 + lado - m, :3]
            for lector, arr in lecturas.items():
                med = np.median(arr.reshape(-1, 3), axis=0)
                d = float(np.max(np.abs(med - np.asarray(rgb, float))))
                peor = max(peor, d)
                if d > tol:
                    malos.append("%s %s: %s ≠ %s" % (nombre, lector, med.astype(int).tolist(),
                                                     list(rgb)))
    finally:
        s.close()
    assert not malos, "colores corruptos (tolerancia %d): %s" % (tol, "; ".join(malos))
    return "%d parches × 3 lecturas, peor diferencia %.0f niveles (≤%d)" % (len(parches), peor,
                                                                            tol)


def prueba_wsidata(ruta):
    import lazyslide as zs
    from wsidata import open_wsi
    wsi = open_wsi(ruta, reader="openslide", store=os.path.join(os.path.dirname(ruta),
                                                                 "humo.zarr"))
    mpp = wsi.properties.mpp
    # Como en el plan: el polígono de ZONA ESCANEADA (aquí, la imagen menos el bloque de relleno)
    # entra como shapes `zona` y es el único límite del teselado; nada de find_tissues.
    from shapely.geometry import box
    from wsidata.io import add_tissues
    w, h = L0
    zona = box(0, 0, w, h).difference(box(w - TESELA * 2, 0, w, TESELA * 2))
    add_tissues(wsi, "zona", [zona])
    zs.pp.tile_tissues(wsi, TESELA, mpp=0.5, slide_mpp=MPP, stride_px=448, edge=True,
                       background_filter=False, tissue_key="zona")
    n = len(wsi["tiles"])
    spec = wsi.tile_spec("tiles")
    assert abs(spec.mpp - 0.5) <= 0.01, "tile_spec.mpp=%r" % spec.mpp
    return wsi, "mpp %.4f, %d teselas en `zona`, tile_spec.mpp %.3f" % (mpp, n, spec.mpp)


def prueba_wsinfer(ruta):
    from wsinfer.wsi import get_avg_mpp
    mpp = get_avg_mpp(ruta)
    assert abs(mpp - MPP) < 1e-3, "mpp=%r" % mpp
    return "get_avg_mpp %.4f" % mpp


# ── 3. modelos ─────────────────────────────────────────────────────────────────────────────────
def dispositivo():
    import torch
    return "mps" if torch.backends.mps.is_available() else "cpu"


def _tesela_uint8(img, lado):
    import numpy as np
    h, w = img.shape[:2]
    y, x = h // 2 - lado // 2, int(w * 0.45) - lado // 2
    return np.ascontiguousarray(img[y:y + lado, x:x + lado])


def _vacia():
    import gc
    gc.collect()
    try:
        import torch
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:                                    # noqa: BLE001 (venv sin torch)
        pass


def _libera(*objs):
    """Las referencias locales mueren al volver; la memoria la suelta `_vacia` tras cada prueba."""
    return None


def forward_vision(cls_ruta, img, lado=224):
    import importlib
    import torch
    mod, cls = cls_ruta.rsplit(".", 1)
    m = getattr(importlib.import_module(mod), cls)()
    dev = dispositivo()
    m.to(dev)
    tf = m.get_transform()
    x = tf(torch.from_numpy(_tesela_uint8(img, lado)).permute(2, 0, 1)).unsqueeze(0).to(dev)
    with torch.inference_mode():
        y = m.encode_image(x)
    forma = tuple(y.shape)
    _libera(m, x, y)
    return "%s en %s → %s" % (cls, dev, forma)


def forward_titan(img):
    import torch
    from lazyslide_models.multimodal.titan import Titan
    m = Titan()
    dev = dispositivo()
    m.model.to(dev)
    m.conch.to(dev)
    from PIL import Image
    x = m.conch_transform(Image.fromarray(_tesela_uint8(img, 448))).unsqueeze(0).to(dev)
    with torch.inference_mode():
        f = m.conch(x)
        f = f[0] if isinstance(f, (tuple, list)) else f
        feats = f.reshape(1, 1, -1).repeat(1, 4, 1)
        coords = torch.tensor([[[0, 0], [1024, 0], [0, 1024], [1024, 1024]]], device=dev)
        emb = m.model.encode_slide_from_patch_features(feats, coords, 1024)
    forma = (tuple(f.shape), tuple(emb.shape))
    _libera(m, x, f, emb)
    return "CONCH v1.5 %s + TITAN %s en %s" % (forma[0], forma[1], dev)


def forward_seg(cls_ruta, img, lado, **kw):
    import importlib
    import torch
    mod, cls = cls_ruta.rsplit(".", 1)
    m = getattr(importlib.import_module(mod), cls)(**kw)
    dev = dispositivo()
    m.to(dev)
    tf = m.get_transform()
    t = torch.from_numpy(_tesela_uint8(img, lado)).permute(2, 0, 1)
    x = (tf(t) if tf else t.float() / 255).unsqueeze(0).to(dev)
    with torch.inference_mode():
        out = m.segment(x)
    desc = {k: tuple(v.shape) for k, v in (out._asdict() if hasattr(out, "_asdict")
                                           else out).items() if hasattr(v, "shape")}
    _libera(m, x, out)
    return "%s en %s → %s" % (cls, dev, desc)


def forward_wsinfer(img):
    """El TorchScript de WSInfer sobre una tesela (la ruta de caída del plan). Sin pasar por
    `load_registry()`: offline intenta un cerrojo en ~/.wsinfer-zoo, que la jaula no deja escribir.
    El registro fija «main» para este modelo y la caché tiene `refs/main` → c5ec400 (pesos.json)."""
    import torch
    from wsinfer_zoo.client import load_torchscript_model_from_hf
    m = load_torchscript_model_from_hf("kaczmarj/breast-tumor-resnet34.tcga-brca")
    net = torch.jit.load(m.model_path, map_location="cpu").eval()
    lado = m.config.patch_size_pixels
    x = torch.from_numpy(_tesela_uint8(img, lado)).permute(2, 0, 1).float().unsqueeze(0) / 255
    with torch.inference_mode():
        y = net(x)
    return "TorchScript %s (CPU) → %s, mpp entrenamiento %s" % (lado, tuple(y.shape),
                                                              m.config.spacing_um_px)


def celulas_instanseg(wsi):
    """`zs.seg.cells` sobre el TIFF entero. InstanSeg NUNCA cae (plan): se prueban, en orden,
    MPS con lotes de 4 (lo de LazySlide por defecto), MPS lote a lote y CPU, y se dice cuál sirvió.
    Medido el 1-oct-26: en MPS con lote > 1 el TorchScript de InstanSeg mezcla un tensor de CPU
    (`torch.stack` de etiquetas, `instanseg_loss.py:1506`) en cuanto una tesela del lote no trae
    núcleos —y la zona escaneada siempre tiene vidrio—: «Passed CPU tensor to MPS op»."""
    import lazyslide as zs
    intentos = []
    for dev, lote in ((dispositivo(), 4), (dispositivo(), 1), ("cpu", 4)):
        try:
            zs.seg.cells(wsi, model="instanseg", overlap_ownership=True, device=dev,
                         batch_size=lote, pbar=False, num_workers=0)
        except Exception as e:                                # noqa: BLE001
            intentos.append("%s×%d: %s" % (dev, lote, str(e).strip().splitlines()[-1][:80]))
            continue
        n = len(wsi["cells"])
        assert n > 0, "0 núcleos sobre el sintético"
        previos = (" (antes falló: %s)" % "; ".join(intentos)) if intentos else ""
        return "%d núcleos en %s, lote %d%s" % (n, dev, lote, previos)
    raise RuntimeError("InstanSeg no corre en ningún modo: " + " | ".join(intentos))


# ── VALIS ──────────────────────────────────────────────────────────────────────────────────────
def humo_valis(base, ruta):
    @_prueba("valis: VipsSlideReader")
    def lector():
        from valis import slide_io
        plano = slide_io.check_flattened_pyramid_tiff(ruta, check_with_bf=False)[0]
        assert not plano, "VALIS lo detecta como «flattened»"
        cls = slide_io.get_slide_reader(ruta)          # el que elegiría VALIS (sin JVM si es vips)
        assert cls.__name__ == "VipsSlideReader", "VALIS elige %s" % cls.__name__
        r = slide_io.VipsSlideReader(ruta)
        md = r.metadata
        return "elige %s, flattened=%s, %d niveles, dims %s, mpp %.4f %s" % (
            cls.__name__, plano, len(md.slide_dimensions), md.slide_dimensions[0].tolist(),
            float(md.pixel_physical_size_xyu[0]), md.pixel_physical_size_xyu[2])
    lector()

    @_prueba("valis: DISK + LightGlue offline")
    def emparejar():
        import numpy as np
        import torch
        from valis import feature_detectors, feature_matcher
        img = imagen_sintetica(semilla=1)
        a = _tesela_uint8(img, 512)
        b = np.roll(a, (12, -7), axis=(0, 1))
        fd = feature_detectors.DiskFD(device=torch.device("cpu"))
        kp1, d1 = fd.detect_and_compute(a)
        kp2, d2 = fd.detect_and_compute(b)
        fm = feature_matcher.LightGlueMatcher(feature_detector=fd, device=torch.device("cpu"))
        res = fm.match_images(img1=a, img2=b, desc1=d1, kp1_xy=kp1, desc2=d2, kp2_xy=kp2)
        filtrado = res[1] if isinstance(res, (tuple, list)) else res
        n = getattr(filtrado, "n_matches", None)
        assert n, "0 pares tras filtrar"
        return "%d kp / %d kp, %s pares filtrados (CPU)" % (len(kp1), len(kp2), n)
    emparejar()


# ── respaldo VALIS del registro (venv patologia → subproceso al venv valis) ────────────────────
MPP_REGISTRO = 2.0                       # ≈ ×8 nativo (2,005 µm/px)
# Transformada conocida móvil→fija (µm): giro alrededor del centro de la imagen y traslación.
VALIS_VERDAD = {"angulo": 8.0, "desp_um": (70.0, -40.0)}
VALIS_TOL_PX = 1.0                       # criterio: error < 1 px a la escala del humo


def imagenes_registro(forma=(1100, 1500), mpp=MPP_REGISTRO, angulo=8.0, desp_um=(70.0, -40.0),
                      espejo=False, semilla=7):
    """Par sintético con la pinta de la imagen de registro (ODsum ecualizada, float en [0, 1]:
    tejido claro sobre fondo oscuro): tres «fragmentos» con textura y «núcleos». La móvil es la
    fija vista con la transformada conocida T (móvil→fija, µm); con `espejo`, además volteada en
    x. Devuelve (fija, móvil, T, máscara de tejido de la móvil)."""
    import numpy as np
    from scipy import ndimage as ndi
    from skimage.transform import AffineTransform, warp
    rng = np.random.default_rng(semilla)
    h, w = forma
    yy, xx = np.mgrid[0:h, 0:w]
    tejido = np.zeros((h, w), bool)
    for cy, cx, ry, rx, a in ((0.45, 0.33, 0.24, 0.17, 20), (0.56, 0.66, 0.2, 0.13, -35),
                              (0.24, 0.62, 0.09, 0.12, 0)):
        t = np.radians(a)
        u = (xx - cx * w) * np.cos(t) + (yy - cy * h) * np.sin(t)
        v = -(xx - cx * w) * np.sin(t) + (yy - cy * h) * np.cos(t)
        tejido |= (u / (rx * w)) ** 2 + (v / (ry * h)) ** 2 < 1
    textura = ndi.gaussian_filter(rng.random((h, w)), 4)
    textura = (textura - textura.min()) / (np.ptp(textura) or 1)
    nucleos = np.zeros((h, w))
    ys, xs = rng.integers(0, h, 4000), rng.integers(0, w, 4000)
    nucleos[ys, xs] = 1.0
    nucleos = ndi.gaussian_filter(nucleos, 1.5)
    nucleos /= nucleos.max() or 1
    fija = (0.05 + tejido * (0.30 + 0.35 * textura + 0.35 * nucleos)).astype(np.float32)
    fija = np.clip(fija, 0, 1)
    P = np.array([[mpp, 0, 0.5 * mpp], [0, mpp, 0.5 * mpp], [0, 0, 1.0]])
    c = np.array([w * mpp / 2, h * mpp / 2])
    t = np.radians(angulo)
    R = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
    T = np.eye(3)
    T[:2, :2] = R
    T[:2, 2] = c - R @ c + np.asarray(desp_um, float)
    # móvil(p) = fija(T·p): warp recibe el mapa inverso salida→entrada, en píxeles
    A = np.linalg.inv(P) @ T @ P
    movil = warp(fija, AffineTransform(matrix=A), order=1, cval=0.05).astype(np.float32)
    mask = warp(tejido.astype(float), AffineTransform(matrix=A), order=0, cval=0) > 0.5
    if espejo:
        movil = np.ascontiguousarray(movil[:, ::-1])
        mask = mask[:, ::-1]
        T = T @ np.array([[-1.0, 0, w * mpp], [0, 1.0, 0], [0, 0, 1.0]])
    return fija, movil, T, mask


def error_transformada_um(M, T, mask, mpp=MPP_REGISTRO, paso=25):
    """Error máximo y mediano (µm) entre M y la verdad T sobre una rejilla de puntos del tejido
    de la móvil."""
    import numpy as np
    ys, xs = np.nonzero(mask[::paso, ::paso])
    xy = np.stack([(xs * paso + 0.5) * mpp, (ys * paso + 0.5) * mpp], 1)
    h = np.c_[xy, np.ones(len(xy))]
    d = np.linalg.norm((h @ np.asarray(M).T)[:, :2] - (h @ np.asarray(T).T)[:, :2], axis=1)
    return float(d.max()), float(np.median(d))


def humo_registro_valis(base):
    """La ruta REAL del respaldo (4): este proceso (venv patologia, jaula analisis, entorno de la
    ventanilla) llama a `laminillas_registro.respaldo_valis`, que lanza el venv valis por
    subproceso. Dos pares sintéticos con transformada conocida (directo y en espejo): VALIS tiene
    que correr (rc 0) y la matriz recuperar la verdad con error < `VALIS_TOL_PX` px."""
    import laminillas_registro as R
    detalle = {}

    def caso(nombre, espejo):
        @_prueba("valis registro: %s" % nombre)
        def prueba():
            f, m, T, mask = imagenes_registro(espejo=espejo, **VALIS_VERDAD)
            M, info = R.respaldo_valis(f, m, MPP_REGISTRO, espejo=espejo,
                                       tmpdir=os.environ.get("TMPDIR"))
            detalle[nombre] = {"info": info}
            print("      rc=%s corrio=%s %.0f s · %s" % (info.get("rc"), info["corrio"],
                                                       info.get("segundos") or 0,
                                                       info.get("motivo") or ""), flush=True)
            assert info["corrio"], info["motivo"]
            emax, emed = error_transformada_um(M, T, mask)
            detalle[nombre].update(error_max_um=emax, error_mediana_um=emed,
                                   M=[[round(x, 4) for x in fila] for fila in M.tolist()])
            assert emax < VALIS_TOL_PX * MPP_REGISTRO, (
                "no recupera la transformada: error máx. %.2f µm (%.2f px) ≥ %g px" % (
                    emax, emax / MPP_REGISTRO, VALIS_TOL_PX))
            return "error máx. %.2f µm (%.2f px), mediana %.2f µm; %.0f s" % (
                emax, emax / MPP_REGISTRO, emed, info["segundos"])
        prueba()
    caso("directo", False)
    caso("espejo", True)
    with open(os.path.join(base, "humo-registro-valis-detalle.json"), "w", encoding="utf-8") as f:
        json.dump(detalle, f, indent=1, default=str)


def main(argv):
    if os.environ.get("BTP_VENTANILLA") != "1":
        print("solo por la ventanilla", file=sys.stderr)
        return 2
    modo = argv[0] if argv else "patologia"
    base = os.path.join(os.getcwd(), "humo")
    os.makedirs(base, mode=0o700, exist_ok=True)
    ruta = os.path.join(base, "sintetica-grundium.tiff")
    ruta_txt = os.path.join(base, "sintetica-grundium-texto.tiff")
    if modo == "registro-valis":
        humo_registro_valis(base)
    elif modo == "valis":
        if not os.path.isfile(ruta):
            _nota("valis: falta el TIFF sintético", False, "corre antes laminillas_humo")
        else:
            humo_valis(base, ruta)
    else:
        img = imagen_sintetica()

        @_prueba("TIFF sintético Grundium (+ texto)")
        def fabrica():
            for r in (ruta, ruta_txt):
                if os.path.exists(r):
                    os.unlink(r)
            escribe_grundium(ruta, img)
            escribe_grundium(ruta_txt, imagen_sintetica(texto=True))
            import tifffile
            with tifffile.TiffFile(ruta) as t:
                p = t.pages
                return "BigTIFF=%s, %d IFD, tile %s, compresión %s, subsampling %s, subfile %s" % (
                    t.is_bigtiff, len(p), p[0].tile, p[0].compression.name,
                    p[0].subsampling, [pg.subfiletype for pg in p])
        fabrica()
        _prueba("Color: parches RGB conocidos (±%d)" % TOL_COLOR)(prueba_color)(ruta)
        _prueba("OpenSlide lee niveles y mpp")(prueba_openslide)(ruta)
        _prueba("OpenSlide lee la variante con texto")(prueba_openslide)(ruta_txt)
        estado = {}

        @_prueba("wsidata/LazySlide abre y tesela a 0,5")
        def wsd():
            wsi, det = prueba_wsidata(ruta)
            estado["wsi"] = wsi
            return det
        wsd()
        _prueba("WSInfer (paquete) lee el mpp", cae_declarado=True)(prueba_wsinfer)(ruta)
        _prueba("InstanSeg zs.seg.cells (MPS)")(lambda: celulas_instanseg(estado["wsi"]))()
        _prueba("InstanSeg forward tesela")(forward_seg)(
            "lazyslide_models.segmentation.instanseg.Instanseg", img, 512)
        _prueba("UNI2-h forward")(forward_vision)("lazyslide_models.vision.uni.UNI2", img)
        _prueba("H-optimus-1 forward")(forward_vision)(
            "lazyslide_models.vision.h_optimus.HOptimus1", img)
        _prueba("TITAN + CONCH v1.5 forward")(forward_titan)(img)
        _prueba("HistoPLUS 40x forward")(forward_seg)(
            "lazyslide_models.segmentation.cellvit_family.histoplus.HistoPLUS", img, 448,
            magnification="40x")
        _prueba("NuLite forward")(forward_seg)(
            "lazyslide_models.segmentation.cellvit_family.nulite.NuLite", img, 512)
        _prueba("GrandQC tejido forward")(forward_seg)(
            "lazyslide_models.segmentation.grandqc.GrandQCTissue", img, 512)
        _prueba("GrandQC artefactos forward")(forward_seg)(
            "lazyslide_models.segmentation.grandqc.GrandQCArtifact", img, 512)
        _prueba("WSInfer breast-tumor forward")(forward_wsinfer)(img)
    salida = os.path.join(base, "humo-%s.json" % modo)
    with open(salida, "w", encoding="utf-8") as f:
        json.dump({"fecha": time.strftime("%Y-%m-%dT%H:%M:%S"), "resultados": RES}, f, indent=1)
    fallos = sum(not r["ok"] and not r["cae_declarado"] for r in RES)
    caen = sum(r["cae_declarado"] for r in RES)
    print("RESUMEN %s: %d OK, %d FALLO, %d CAE (declarado)" % (
        modo, len(RES) - fallos - caen, fallos, caen))
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
