#!/usr/bin/env python3
"""tools/laminillas_segmenta.py — segmentación nuclear y reglas de artefacto (plan «laminillas DFCI»,
F2 «Segmentación nuclear» y Puerta del piloto, punto 2).

  · Teselado LazySlide con el polígono de ZONA ESCANEADA como shapes `zona`:
      zs.pp.tile_tissues(wsi, 512, mpp=0.5, slide_mpp=<mpp del manifiesto>, stride_px=448,
                         edge=True, background_filter=False, tissue_key='zona')
    respaldo: tissue_key=None y descarte posterior de las teselas fuera del polígono.
    Prohibido find_tissues.
  · zs.seg.cells(model='instanseg', overlap_ownership=True), LOTE 1 en MPS (decisión fijada: con
    lote > 1 el TorchScript mezcla un tensor de CPU en cuanto una tesela del lote viene vacía);
    polígonos en px de L0. La máscara de tejido solo se aplica DESPUÉS, sobre los núcleos.
  · Pesos de InstanSeg y GrandQC cargados desde el fichero del commit sellado en
    `laminillas_stack/pesos.json`, con bytes y sha256 comprobados antes (nunca `refs/main`);
    GrandQC reintenta en CPU si falla en MPS.
  · Comprobaciones de la pre-congelación: nº de teselas = el esperado ±1 % (CRITERIO_TESELAS,
    declarado), 0 descartadas, tile_spec.mpp 0,5 ±0,01, sin el aviso de `check_input_tile`,
    rejilla desplazada 128 px con cambio de recuento < 1 % por fragmento y acuerdo del área
    nuclear con el detector B en µm² (CRITERIO_AREA, declarado; la «mediana 25-80 µm²» del plan
    queda como referencia).
  · Detector clásico B (watershed sobre ODsum a L0) y sus medidas de acuerdo (cobertura, F1 a
    IoU 0,5).
  · Máscara provisional independiente de la tinción: centroides a 8 µm/px, gaussiana σ 16 µm,
    umbral fijo sellado, con el borde corregido al real y los valles entre fragmentos abiertos
    (MASCARA_PROV) → borde de fragmento y área.
  · Reglas clásicas de artefacto: tercil bajo de foco (cociente de energía del laplaciano L0 vs
    L0 a la mitad), saturados no H/DAB (en `laminillas_color`), pliegue = bandas ≥ 50 µm con ODsum
    sobre el p99,5 de la lámina (y los candidatos a pliegue grande que esa regla no ve, para la
    galería del tribunal: LIMITE_PLIEGUE). GrandQC opcional, con sus fracciones.

Guardia: `celulas()` y `segmentador_lazyslide()` se NIEGAN a segmentar una lámina que no sea de
suelo (P-HER2NEG, P-HER2) sin un sello de congelación verificado, COMPLETO (umbral, vectores,
residuo, pre-congelación y parámetros) y cuyos parámetros de segmentación sellados casen con los
de este código (`exige_sello_celulas`). Lo que opera sobre un `wsi` ya abierto es privado (`_`).
"""
import hashlib
import json
import math
import os
import sys
import warnings

import numpy as np

SUELO = ("P-HER2NEG", "P-HER2")

TESELA = dict(tile_px=512, mpp=0.5, stride_px=448, edge=True, background_filter=False,
              tissue_key="zona")
COMPROBACION = dict(tol_teselas=0.01, tol_mpp=0.01, desplaza_px=128, cambio_rejilla_max=0.01,
                    area_nuclear_um2=(25.0, 80.0), acuerdo_area=(0.70, 1.30),
                    acuerdo_area_min_pares=200, acuerdo_area_teselas=20,
                    acuerdo_area_tejido_min=0.25)
# Criterio del área nuclear, DECLARADO (2-oct-26). El plan pedía «mediana 25-80 µm²» [inferido];
# en las dos láminas de suelo sale 13,75 µm² y NO es un error de unidades (los polígonos de
# LazySlide están en px de L0: verificado con discos de 7 µm a mpp 0,2506). Medido en P-HER2NEG
# con un detector independiente (B: watershed sobre ODsum a L0, recuento de píxeles × mpp_l0²):
# mediana 19,9 µm², y en los MISMOS núcleos (IoU > 0,5) InstanSeg/B = 0,91, lo mismo que en el
# sintético con verdad física (0,90; el contorno de LazySlide pasa por los centros de los píxeles
# del borde y pierde ~0,25 µm de radio). Los núcleos de estas láminas miden ~15-20 µm² con los dos
# métodos; lo que el criterio tiene que cazar es un fallo de escala o de segmentación, y eso lo da
# el cociente: un error ×2 en la escala lineal da 0,25 o 4; ×√2, 0,5 o 2.
CRITERIO_AREA = ("área nuclear: mediana del cociente InstanSeg/detector B, en µm² (polígono × "
                 "mpp_l0² frente a píxeles × mpp_l0²), en los mismos núcleos (IoU > 0,5 a L0), en "
                 "20 ventanas de L0 con tejido de cada lámina de suelo: 0,70-1,30 con ≥ 200 pares "
                 "(sintético con verdad física: 0,90); la «mediana 25-80 µm²» del plan era "
                 "inferencia y estas láminas miden ~15-20 µm² con los dos métodos: se guarda como "
                 "referencia, no como puerta")
# Criterio del nº de teselas, DECLARADO: el plan pide «el esperado por el área de la zona (±1 %)»,
# que no es alcanzable (las teselas de borde que solo tocan la zona cuentan enteras: +4-5 % en
# zonas de prueba). El esperado es una réplica independiente de la rejilla de LazySlide
# intersecada con la zona (mismo conjunto de esquinas, ±1 %); la referencia por área y su desvío
# se guardan al lado.
CRITERIO_TESELAS = ("esperado = réplica de la rejilla de LazySlide (tiles_from_bbox) intersecada "
                    "con la zona escaneada, por esquinas, ±1 %; el «esperado por el área ±1 %» del "
                    "plan no es alcanzable por las teselas de borde: se guarda como referencia "
                    "con su desvío")
INSTANSEG = dict(modelo="instanseg", fichero="instanseg/instanseg_v0_1_0.pt",
                 sha256="6dee75ac3d09c7e44d4549a61fe7a3ee058ca489d141c1f9a94bf0c6f039b382",
                 bytes=15799131, lote=1, dispositivo="mps", overlap_ownership=True,
                 aviso="To optimize the performance of Instanseg")
# Máscara provisional (independiente de la tinción). Densidad de centroides a 8 µm/px con
# gaussiana σ 16 µm y umbral fijo de 250 núcleos/mm² (plan; la cifra es inferencia mía: un núcleo
# aislado da un pico de 622/mm²). Con SOLO eso, el borde cae fuera del tejido real y tanto más
# cuanto más denso (σ·Φ⁻¹(250/D): 18 µm a 2.000/mm², 31 µm a 10.000/mm²): los «50 µm del borde»
# se quedaban en 19-28 µm reales, y dos fragmentos a 50 µm se fundían (2,5 µm reales en sus caras
# enfrentadas). Correcciones, selladas (inferencia mía), que NO quitan tejido lejos de un valle:
#  · borde con el vidrio: la distancia y el área se miden al borde REAL, δ = −σ·Φ⁻¹(250/D) dentro
#    del de la máscara, con D = media local (σ_L 48 µm) de la densidad en el interior (> 2σ);
#  · valles: densidad < 25 % del tejido que hay a los DOS lados (a ±40 µm, 8 direcciones), con
#    ≥ 1.200/mm² a ambos lados y ≥ 100 µm de largo (un hueco entre fragmentos es una línea larga;
#    los valles cortos son ruido del estroma laxo): se abren, y a ≤ 40 µm de ellos el borde va a
#    la mitad del escalón (densidad < ½ del tejido de al lado);
#  · huecos < 3.000 µm² (ruido del estroma laxo, no vidrio) se rellenan.
# Sintético (núcleos en rejilla o Poisson, 2.000-10.000/mm², fragmentos de 500 µm a 50 µm): 2
# fragmentos, área −5/+2 %, ningún núcleo retenido a < 45 µm reales del borde. Tumor 8.000 junto a
# estroma 1.000/mm²: el estroma se conserva (área −7 %; con un umbral relativo del 50 % se perdía
# la mitad).
MASCARA_PROV = dict(mpp=8.0, sigma_um=16.0, umbral_nucleos_mm2=250.0, fraccion_valle=0.25,
                    radio_valle_um=40.0, densidad_min_valle=1200.0, valle_min_largo_um=100.0,
                    sigma_meseta_um=48.0, hueco_max_um2=3000.0)
FRAGMENTO_MIN_MM2 = 0.2           # «fragmentos > 0,2 mm²» (Hechos medidos)
# Pliegue (regla del plan): ODsum sobre el p99,5 del tejido, bandas ≥ 50 µm. Por construcción no
# marca más del 0,5 % del tejido: un pliegue mayor sube el p99,5 dentro de sí mismo y desaparece.
# Bajar el umbral (k× la mediana, p99,5 por bloques) marcaría también los nidos densos, que en
# ODsum son indistinguibles de un doble grosor y son justo lo que se mide. Así que la regla del
# plan se queda y los CANDIDATOS a pliegue grande (bandas ≥ 50 µm con ODsum ≥ 2× la mediana del
# tejido que la regla no marcó) van a la galería del tribunal, sin excluirse solos.
ARTEFACTO = dict(foco_tejido_min=0.5, pliegue_percentil=99.5, pliegue_ancho_um=50.0,
                 pliegue_mpp=2.0, borde_um=50.0, pliegue_candidato_factor_mediana=2.0,
                 pliegue_candidatos_max=20)
LIMITE_PLIEGUE = ("la regla de pliegue del plan (ODsum sobre el p99,5 de la lámina, bandas "
                  "≥ 50 µm) no marca pliegues que ocupen más del 0,5 % del tejido; los candidatos "
                  "(ODsum ≥ 2× la mediana, bandas ≥ 50 µm) van a la galería del tribunal, no se "
                  "excluyen solos")
GRANDQC = dict(variante="7x", mpp=1.5, umbral=0.8, max_fraccion=0.20,
               fichero="GrandQC/GrandQC_MPP15_exported.pt2",
               rotulo="artifact exclusion by fixed classical rules; GrandQC not used on IHC")
DETECTOR_B = dict(sigma_um=0.5, area_min_um2=10.0, area_max_um2=400.0, dist_min_um=2.5,
                  odsum_tejido=0.05)
ROTULOS = {"deteccion": "detection-dependent", "grandqc_fuera": GRANDQC["rotulo"]}
SECCIONES_SELLO = ("umbral", "vectores", "residuo", "precongelacion", "parametros")

_AQUI = os.path.dirname(os.path.abspath(__file__))
PESOS = os.path.join(_AQUI, "laminillas_stack", "pesos.json")
REPO_PESOS = "lazyslide_models"           # entrada de pesos.json (RendeiroLab/LazySlide-models)


def parametros():
    """Todo parámetro de regla de este módulo, tal como se sella en (0)."""
    return {"TESELA": dict(TESELA), "COMPROBACION": dict(COMPROBACION, area_nuclear_um2=list(
        COMPROBACION["area_nuclear_um2"]), acuerdo_area=list(COMPROBACION["acuerdo_area"])),
            "CRITERIO_TESELAS": CRITERIO_TESELAS, "CRITERIO_AREA": CRITERIO_AREA,
            "INSTANSEG": dict(INSTANSEG), "MASCARA_PROV": dict(MASCARA_PROV),
            "FRAGMENTO_MIN_MM2": FRAGMENTO_MIN_MM2, "ARTEFACTO": dict(ARTEFACTO),
            "GRANDQC": dict(GRANDQC), "DETECTOR_B": dict(DETECTOR_B)}


def canon(x):
    """Forma JSON canónica (tuplas → listas, claves texto) para cotejar con lo sellado."""
    return json.loads(json.dumps(x, sort_keys=True, default=float))


def diferencias(sellado, actual, ruta=""):
    """Rutas de las claves que no casan entre dos estructuras JSON."""
    if isinstance(sellado, dict) and isinstance(actual, dict):
        out = []
        for k in sorted(set(sellado) | set(actual)):
            out += diferencias(sellado.get(k), actual.get(k), "%s.%s" % (ruta, k) if ruta else k)
        return out
    return [] if sellado == actual else [ruta or "(raíz)"]


# El sello es el del piloto entero (`laminillas_sello`, formato compartido con el registro).
sys.path.insert(0, _AQUI)
import laminillas_sello as SL  # noqa: E402

SinSello = SL.SelloAusente


def exige_sello_celulas(nombre, ruta_sello):
    """Ninguna célula de una lámina que no sea de suelo se lee antes del sello: sin sello,
    `SelloAusente`; con un sello tocado después de sellar, incompleto (le falta alguna de
    `SECCIONES_SELLO`) o con parámetros de segmentación que ya no son los de este código,
    `SelloInvalido`. Devuelve el Sello (o None: lámina de suelo sin sello)."""
    s = SL.exige(ruta_sello, [nombre])
    if s is None:
        return None
    faltan = [k for k in SECCIONES_SELLO if not isinstance(s.d.get(k), dict)]
    if faltan:
        raise SL.SelloInvalido("sello %s… incompleto (faltan %s): no es una congelación (0)"
                               % (s.sha256[:12], ", ".join(faltan)))
    malos = diferencias(s.d["parametros"].get("segmenta"), canon(parametros()))
    if malos:
        raise SL.SelloInvalido("los parámetros de segmentación del código no son los sellados "
                               "(%s): no mido" % ", ".join(malos[:6]))
    return s


def nombre_de(lamina, nombre=None):
    """Nombre opaco de la lámina: el que se pasa y el de la Lámina tienen que casar."""
    propio = getattr(lamina, "opaco", None)
    if nombre and propio and nombre != propio:
        raise ValueError("la lámina abierta es %s y se pidió medir %s" % (propio, nombre))
    nombre = nombre or propio
    if not nombre:
        raise ValueError("lámina sin nombre opaco: no sé si es de suelo o diana, no la mido")
    return nombre


def celulas(lector, nombre, ruta_sello, segmentador=None, **kw):
    """ÚNICA puerta para obtener núcleos de una lámina. Diana sin sello → SinSello."""
    exige_sello_celulas(nombre, ruta_sello)
    if segmentador is None:
        segmentador = segmentador_lazyslide
        kw["ruta_sello"] = ruta_sello
    return segmentador(lector, lector.abre(nombre), nombre, **kw)


# ── rejilla (réplica independiente para el «esperado») ────────────────────────────────────────
def _arranques(origen, largo, tesela, paso, edge):
    if largo < tesela:
        return np.array([origen], np.int64)
    n = (largo - tesela) // paso + 1
    a = origen + np.arange(n, dtype=np.int64) * paso
    if edge and a[-1] + tesela < origen + largo:
        a = np.append(a, a[-1] + paso)
    return a


def rejilla(zona, mpp_l0, origen=None, desplaza_px=0, tile_px=TESELA["tile_px"],
            stride_px=TESELA["stride_px"], mpp=TESELA["mpp"], edge=TESELA["edge"], caja=None):
    """Esquinas (x0, y0) en L0 de las teselas que tocan la zona. Anclaje como LazySlide: esquina
    de la caja de la zona (o `origen`), desplazada `desplaza_px` px a 0,5 µm/px. `caja` = (x, y,
    w, h): la caja que tesela LazySlide en el respaldo (tissue_key=None: `wsi.properties.bounds`,
    toda la imagen), para que la réplica tenga las mismas columnas y filas de borde."""
    import shapely
    ds = mpp / mpp_l0
    bw, bs, sh = int(tile_px * ds), int(stride_px * ds), int(desplaza_px * ds)
    if caja is not None:
        x, y = int(caja[0]) - sh, int(caja[1]) - sh
        w, h = int(caja[2]) + sh, int(caja[3]) + sh
    else:
        minx, miny, maxx, maxy = zona.bounds
        if origen is not None:
            minx, miny = origen
        x, y = int(minx) - sh, int(miny) - sh
        w, h = int(maxx - minx) + sh, int(maxy - miny) + sh
    xs = _arranques(x, w, bw, bs, edge)
    ys = _arranques(y, h, bw, bs, edge)
    gx, gy = np.meshgrid(xs, ys, indexing="ij")
    cajas = shapely.box(gx.ravel(), gy.ravel(), gx.ravel() + bw, gy.ravel() + bw)
    shapely.prepare(zona)
    ok = shapely.intersects(cajas, zona)
    return np.column_stack([gx.ravel()[ok], gy.ravel()[ok]]), bw


def comprueba_teselas(esquinas_lazyslide, esquinas_esperadas, spec_mpp, zona=None, mpp_l0=None):
    """Nº de teselas frente al esperado (CRITERIO_TESELAS, declarado), 0 descartadas y
    tile_spec.mpp 0,5 ±0,01; con la zona, además la referencia por área y su desvío."""
    a = {tuple(map(int, p)) for p in np.asarray(esquinas_lazyslide).reshape(-1, 2)}
    e = {tuple(map(int, p)) for p in np.asarray(esquinas_esperadas).reshape(-1, 2)}
    n, ne = len(a), len(e)
    r = {"n": n, "esperado": ne, "descartadas": len(e - a), "sobrantes": len(a - e),
         "tile_spec_mpp": float(spec_mpp), "criterio": CRITERIO_TESELAS}
    if zona is not None and mpp_l0:
        paso_mm = TESELA["stride_px"] * TESELA["mpp"] / 1000.0
        ref = float(zona.area * (mpp_l0 / 1000.0) ** 2 / paso_mm ** 2)
        r["referencia_area"] = ref
        r["desvio_area"] = (n - ref) / ref if ref else None
    r["pasa"] = (ne > 0 and abs(n - ne) <= COMPROBACION["tol_teselas"] * ne
                 and r["descartadas"] == 0
                 and abs(spec_mpp - TESELA["mpp"]) <= COMPROBACION["tol_mpp"])
    return r


# ── pesos sellados (F1.0) ─────────────────────────────────────────────────────────────────────
class PesoNoSellado(RuntimeError):
    """El fichero de pesos que se iba a cargar no es el sellado en pesos.json."""


def peso_verificado(fichero, pesos=PESOS, hf_home=None):
    """Ruta en la caché HF del `fichero` de RendeiroLab/LazySlide-models EN EL COMMIT SELLADO de
    `pesos.json` (cuyo propio sha256 se coteja con `pesos.json.sha256`), con bytes y sha256
    comprobados antes de cargar. Nunca baja nada ni sigue `refs/main`. Devuelve (ruta, medido)."""
    with open(pesos, "rb") as f:
        crudo = f.read()
    try:
        with open(pesos + ".sha256", encoding="utf-8") as f:
            sellado = f.read().split()[0]
    except (OSError, IndexError):
        raise PesoNoSellado("pesos.json sin su .sha256: no sé si es el sellado")
    if hashlib.sha256(crudo).hexdigest() != sellado:
        raise PesoNoSellado("pesos.json no casa con su .sha256 (¿editado?)")
    ent = json.loads(crudo.decode("utf-8"))["modelos"][REPO_PESOS]
    meta = (ent.get("ficheros") or {}).get(fichero)
    if not meta or not ent.get("commit") or not ent.get("repo"):
        raise PesoNoSellado("%s no está sellado en pesos.json (%s)" % (fichero, REPO_PESOS))
    hf_home = hf_home or os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface")
    carpeta = "models--" + ent["repo"].replace("/", "--")
    ruta = os.path.join(hf_home, "hub", carpeta, "snapshots", ent["commit"], fichero)
    if not os.path.isfile(ruta):
        raise PesoNoSellado("%s no está en la caché en el commit sellado %s" % (fichero,
                                                                              ent["commit"][:12]))
    h = hashlib.sha256()
    n = 0
    with open(ruta, "rb") as f:
        for trozo in iter(lambda: f.read(1 << 20), b""):
            h.update(trozo)
            n += len(trozo)
    medido = {"fichero": fichero, "repo": ent["repo"], "commit": ent["commit"], "bytes": n,
              "sha256": h.hexdigest()}
    if n != int(meta["bytes"]) or medido["sha256"] != meta["sha256"]:
        raise PesoNoSellado("%s: bytes o sha256 no son los sellados" % fichero)
    return ruta, medido


def _modelo_instanseg():
    """InstanSeg de LazySlide cargado DESDE el fichero verificado (su constructor ignora
    `model_file` y resuelve `refs/main`; aquí se salta y se carga la ruta sellada)."""
    import torch
    from lazyslide_models.segmentation.instanseg import Instanseg
    ruta, medido = peso_verificado(INSTANSEG["fichero"])
    if medido["sha256"] != INSTANSEG["sha256"] or medido["bytes"] != INSTANSEG["bytes"]:
        raise PesoNoSellado("el InstanSeg de pesos.json no es el que cita el plan")
    m = Instanseg.__new__(Instanseg)
    m.model = torch.jit.load(ruta, map_location="cpu")
    m.model.eval()
    return m, medido


def _modelo_grandqc():
    """GrandQC-artefactos (7x) DESDE el fichero verificado."""
    import torch
    from lazyslide_models.segmentation.grandqc import GrandQCArtifact
    ruta, medido = peso_verificado(GRANDQC["fichero"])
    m = GrandQCArtifact.__new__(GrandQCArtifact)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="The given buffer is not writable")
        m.model = torch.export.load(ruta).module()
    return m, medido


# ── LazySlide + InstanSeg ─────────────────────────────────────────────────────────────────────
def _esquinas(gdf):
    b = gdf.geometry.bounds
    return np.column_stack([b["minx"].to_numpy(), b["miny"].to_numpy()]).astype(np.int64)


def _tesela_lazyslide(wsi, zona, mpp_l0):
    """Teselado del plan sobre `zona`; si falla, el respaldo con tissue_key=None y descarte."""
    import lazyslide as zs
    from wsidata.io import add_tissues, subset_tiles
    add_tissues(wsi, "zona", [zona])
    via = "zona"
    caja = None
    try:
        zs.pp.tile_tissues(wsi, TESELA["tile_px"], mpp=TESELA["mpp"], slide_mpp=mpp_l0,
                           stride_px=TESELA["stride_px"], edge=TESELA["edge"],
                           background_filter=TESELA["background_filter"], tissue_key="zona")
    except Exception as e:                                         # noqa: BLE001
        via = "respaldo tissue_key=None (%s)" % type(e).__name__
        zs.pp.tile_tissues(wsi, TESELA["tile_px"], mpp=TESELA["mpp"], slide_mpp=mpp_l0,
                           stride_px=TESELA["stride_px"], edge=TESELA["edge"],
                           background_filter=False, tissue_key=None)
        import shapely
        t = wsi["tiles"]
        dentro = np.flatnonzero(shapely.intersects(t.geometry.to_numpy(), zona))
        subset_tiles(wsi, "tiles", dentro)
        caja = tuple(wsi.properties.bounds)          # (x, y, w, h): LazySlide tesela esto
    spec = wsi.tile_spec("tiles")
    esperadas, _ = rejilla(zona, mpp_l0, caja=caja)
    r = comprueba_teselas(_esquinas(wsi["tiles"]), esperadas, spec.mpp, zona, mpp_l0)
    r["via"] = via
    return r


def _tesela_desplazada(wsi, zona, desplaza_px=COMPROBACION["desplaza_px"],
                       key="teselas_desplazadas"):
    """Misma especificación y misma caja, rejilla desplazada `desplaza_px` px (a 0,5 µm/px):
    desplazamiento PURO, origen = esquina de la zona + 128 px − paso, aunque quede en x < 0 (el
    lector de wsidata rellena fuera de la imagen con negro, igual que en las teselas de borde de
    la rejilla original; verificado el 1-oct-26). Devuelve la clave de las teselas."""
    import shapely
    from wsidata.io import add_tiles
    spec = wsi.tile_spec("tiles")
    sh = int(desplaza_px * spec.base_downsample)
    minx, miny, maxx, maxy = zona.bounds
    ox = int(minx) + sh - spec.base_stride_width
    oy = int(miny) + sh - spec.base_stride_height
    xs = _arranques(ox, int(maxx) - ox, spec.base_width, spec.base_stride_width, True)
    ys = _arranques(oy, int(maxy) - oy, spec.base_height, spec.base_stride_height, True)
    gx, gy = np.meshgrid(xs, ys, indexing="ij")
    xy = np.column_stack([gx.ravel(), gy.ravel()])
    cajas = shapely.box(xy[:, 0], xy[:, 1], xy[:, 0] + spec.base_width,
                        xy[:, 1] + spec.base_height)
    xy = xy[shapely.intersects(cajas, zona)]
    add_tiles(wsi, key, xy, spec, np.zeros(len(xy), np.int64))
    return key


def dispositivo():
    import torch
    return "mps" if torch.backends.mps.is_available() else "cpu"


def _instanseg(wsi, tile_key="tiles", key_added="cells", modelo=None):
    """InstanSeg por LazySlide, lote 1 en MPS (CPU solo si no hay MPS, y se dice), con los pesos
    VERIFICADOS. Devuelve la lista de polígonos L0, el dispositivo, si saltó el aviso de
    `check_input_tile` (LazySlide 0.12.0 no lo llama: se llama aquí, con la especificación real
    de las teselas) y el fichero de pesos medido."""
    import lazyslide as zs
    dev = dispositivo()
    with warnings.catch_warnings(record=True) as avisos:
        warnings.simplefilter("always")
        if modelo is None:
            modelo, medido = _modelo_instanseg()
        else:
            modelo, medido = modelo
        modelo.check_input_tile(wsi.tile_spec(tile_key))
        zs.seg.cells(wsi, model=modelo, tile_key=tile_key, overlap_ownership=True, device=dev,
                     batch_size=INSTANSEG["lote"], num_workers=0, pbar=False,
                     key_added=key_added)
    aviso = any(INSTANSEG["aviso"] in str(a.message) for a in avisos)
    pol = list(wsi[key_added].geometry) if key_added in wsi.shapes else []
    return pol, dev, aviso, medido


def ruta_lamina(lector, lamina, nombre):
    """LazySlide abre el fichero con OpenSlide (el plan lo pide: `open_wsi(..., reader=
    'openslide')`); la ruta sale de la Lámina o del manifiesto, relativa a SESION (cwd)."""
    for at in ("ruta", "path", "fichero"):
        r = getattr(lamina, at, None)
        if r:
            return r if os.path.isabs(r) else os.path.join(os.getcwd(), r)
    man = lector.manifiesto()
    ent = man.get("laminas", {}).get(nombre, {})
    if not ent.get("fichero"):
        raise ValueError("%s: ni la Lámina ni el manifiesto dan el fichero" % nombre)
    return os.path.join(os.getcwd(), ent["fichero"])


def segmentador_lazyslide(lector, lamina, nombre, zona=None, dir_trabajo=None,
                          con_desplazada=True, con_grandqc=True, ruta_sello=None):
    """Lo que la pre-congelación (o, tras el sello, la medida) pide a la segmentación de UNA
    lámina. Guardia propia: diana sin sello completo y vigente → no abre ni un píxel."""
    from wsidata import open_wsi
    nombre = nombre_de(lamina, nombre)
    exige_sello_celulas(nombre, ruta_sello)
    ruta = ruta_lamina(lector, lamina, nombre)
    zona = zona if zona is not None else lector.zona_escaneada(lamina)
    dir_trabajo = dir_trabajo or os.path.join(os.getcwd(), "segmenta")
    os.makedirs(dir_trabajo, mode=0o700, exist_ok=True)
    wsi = open_wsi(ruta, reader="openslide", store=os.path.join(dir_trabajo, nombre + ".zarr"))
    mpp_l0 = float(lamina.mpp_l0)
    teselas = _tesela_lazyslide(wsi, zona, mpp_l0)
    modelo = _modelo_instanseg()
    pol, dev, aviso, pesos = _instanseg(wsi, modelo=modelo)
    out = {"celulas": pol, "teselado": teselas, "dispositivo": dev, "lote": INSTANSEG["lote"],
           "aviso_check_input_tile": aviso, "mpp_l0": mpp_l0, "pesos": pesos}
    if con_desplazada:
        key = _tesela_desplazada(wsi, zona)
        out["celulas_desplazadas"] = _instanseg(wsi, tile_key=key, modelo=modelo,
                                                key_added="celulas_desplazadas")[0]
    if con_grandqc:
        out["grandqc"] = _grandqc(wsi, zona, mpp_l0)
    return out


def _areas_mapa_instanseg(wsi, tile_key, modelo, dev, mpp_l0):
    """Sobre la MISMA entrada que ve el modelo en `zs.seg.cells` (lectura, remuestreo y
    transformación de LazySlide), área de cada instancia del mapa de InstanSeg (lejos del borde de
    la tesela) de dos formas, en µm²: recuento de píxeles del mapa y área del contorno que
    LazySlide convierte en polígono (cv2.findContours, que pasa por los CENTROS de los píxeles del
    borde y pierde ~medio píxel de radio)."""
    import cv2
    import torch
    from lazyslide.cv.mask import binary_mask_to_polygons
    from skimage.measure import regionprops
    spec = wsi.tile_spec(tile_key)
    px_um2 = (spec.base_downsample * mpp_l0) ** 2
    ds = wsi.ds.tile_images(tile_key=tile_key, transform=modelo.get_transform())
    modelo.to(dev)
    pix, cont = [], []
    with torch.inference_mode():
        for i in range(len(ds)):
            mapa = modelo.segment(ds[i]["image"][None].to(dev)).instance_map
            mapa = quita_borde(np.asarray(mapa[0].detach().cpu().numpy(), np.int64), 3)
            for r in regionprops(mapa):
                p = binary_mask_to_polygons(np.asarray(r.image, np.uint8), detect_holes=False,
                                            contour_method=cv2.CHAIN_APPROX_SIMPLE)
                if p:
                    pix.append(float(r.area))
                    cont.append(float(p[0].area))
    return {"pixeles_um2": np.asarray(pix) * px_um2, "contorno_um2": np.asarray(cont) * px_um2,
            "px_um2": px_um2}


def diagnostico_instanseg(lector, lamina, nombre, zona, elige, dir_trabajo):
    """DIAGNÓSTICO del área nuclear (no sella; solo escribe en `dir_trabajo`): el teselado y el
    InstanSeg de la pre-congelación, solo en las teselas que `elige(esquinas_l0, lado_l0)` devuelve
    (índices). Devuelve los polígonos L0 de producción (`zs.seg.cells`), las esquinas y el lado
    L0 de esas teselas, la especificación de tesela y las áreas del mapa del modelo
    (`_areas_mapa_instanseg`). SOLO láminas de suelo."""
    from wsidata import open_wsi
    from wsidata.io import subset_tiles
    nombre = nombre_de(lamina, nombre)
    if nombre not in SUELO:
        raise ValueError("el diagnóstico de área solo mide láminas de suelo (%s)" % ", ".join(SUELO))
    ruta = ruta_lamina(lector, lamina, nombre)
    wsi = open_wsi(ruta, reader="openslide",
                   store=os.path.join(dir_trabajo, nombre + "-diagnostico.zarr"))
    mpp_l0 = float(lamina.mpp_l0)
    teselado = _tesela_lazyslide(wsi, zona, mpp_l0)
    spec = wsi.tile_spec("tiles")
    esquinas = _esquinas(wsi["tiles"])
    idx = np.asarray(elige(esquinas, spec.base_width), np.int64)
    if not len(idx):
        raise ValueError("ninguna tesela elegida para el diagnóstico")
    subset_tiles(wsi, "tiles", idx, new_key="teselas_diagnostico")
    modelo = _modelo_instanseg()
    pol, dev, aviso, pesos = _instanseg(wsi, tile_key="teselas_diagnostico",
                                        key_added="celulas_diagnostico", modelo=modelo)
    mapa = _areas_mapa_instanseg(wsi, "teselas_diagnostico", modelo[0], dev, mpp_l0)
    return {"celulas": pol, "esquinas": esquinas[idx], "lado_l0": int(spec.base_width),
            "base_downsample": float(spec.base_downsample), "tile_spec_mpp": float(spec.mpp),
            "teselado_pasa": bool(teselado.get("pasa")), "dispositivo": dev,
            "aviso_check_input_tile": aviso, "pesos": pesos, "mapa": mapa}


# ── geometría de núcleos ──────────────────────────────────────────────────────────────────────
def centroides(poligonos):
    return np.array([[p.centroid.x, p.centroid.y] for p in poligonos], np.float64).reshape(-1, 2)


def areas_um2(poligonos, mpp_l0):
    return np.array([p.area for p in poligonos], np.float64) * mpp_l0 * mpp_l0


def empaqueta(poligonos):
    """Contornos exteriores (de un MultiPolygon, su parte mayor) como (coords, offsets)."""
    ext = []
    for p in poligonos:
        if p.geom_type == "MultiPolygon":
            p = max(p.geoms, key=lambda g: g.area)
        ext.append(np.asarray(p.exterior.coords, np.float64))
    offs = np.cumsum([0] + [len(e) for e in ext]).astype(np.int64)
    return (np.concatenate(ext) if ext else np.zeros((0, 2))), offs


def desempaqueta(coords, offs):
    from shapely.geometry import Polygon
    return [Polygon(coords[offs[i]:offs[i + 1]]) for i in range(len(offs) - 1)]


def mediana_area(poligonos, mpp_l0):
    a = areas_um2(poligonos, mpp_l0)
    m = float(np.median(a)) if len(a) else float("nan")
    lo, hi = COMPROBACION["area_nuclear_um2"]
    return {"mediana_um2": m, "n": int(len(a)), "pasa": bool(lo <= m <= hi)}


def acuerdo_area(cocientes_um2):
    """Puerta del área nuclear (CRITERIO_AREA): mediana del cociente InstanSeg/B en µm² sobre
    pares del mismo núcleo, en [0,70; 1,30] y con ≥ 200 pares."""
    c = np.asarray(cocientes_um2, np.float64).ravel()
    c = c[np.isfinite(c)]
    lo, hi = COMPROBACION["acuerdo_area"]
    if not len(c):
        return {"n_pares": 0, "mediana": None, "p10": None, "p90": None, "pasa": False,
                "criterio": CRITERIO_AREA}
    m = float(np.median(c))
    return {"n_pares": int(len(c)), "mediana": m, "p10": float(np.percentile(c, 10)),
            "p90": float(np.percentile(c, 90)), "criterio": CRITERIO_AREA,
            "pasa": bool(len(c) >= COMPROBACION["acuerdo_area_min_pares"] and lo <= m <= hi)}


def rasteriza(poligonos, x0, y0, forma, escala=1.0, etiquetas=None):
    """Etiquetas (1..n) de los polígonos (coordenadas L0) en una rejilla con origen (x0, y0) en
    L0 y `escala` px de L0 por px de salida."""
    from skimage.draw import polygon as dibuja
    lab = np.zeros(forma, np.int32)
    for i, p in enumerate(poligonos):
        partes = getattr(p, "geoms", [p])
        for q in partes:
            xy = np.asarray(q.exterior.coords)
            rr, cc = dibuja((xy[:, 1] - y0) / escala - 0.5, (xy[:, 0] - x0) / escala - 0.5,
                            forma)
            lab[rr, cc] = (etiquetas[i] if etiquetas is not None else i + 1)
    return lab


# ── máscara provisional (independiente de la tinción) ─────────────────────────────────────────
def _anillo(mapa, r_px, n_dir=8):
    """Para cada píxel y cada una de `n_dir` direcciones, el valor de `mapa` a ±r píxeles
    (bilineal, 0 fuera): devuelve (máximo a un lado cualquiera, máximo de los mínimos a los dos
    lados opuestos). Lo primero es «hay tejido a un lado»; lo segundo, «a los dos»."""
    from scipy.ndimage import map_coordinates
    H, W = mapa.shape
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    uno = np.zeros_like(mapa)
    dos = np.zeros_like(mapa)
    for t in np.arange(n_dir) * np.pi / n_dir:
        dx, dy = r_px * math.cos(t), r_px * math.sin(t)
        a = map_coordinates(mapa, [yy + dy, xx + dx], order=1, mode="constant", cval=0.0)
        b = map_coordinates(mapa, [yy - dy, xx - dx], order=1, mode="constant", cval=0.0)
        uno = np.maximum(uno, np.maximum(a, b))
        dos = np.maximum(dos, np.minimum(a, b))
    return uno, dos


def mascara_provisional(xy_l0, mpp_l0, dims_l0, mpp=MASCARA_PROV["mpp"],
                        sigma_um=MASCARA_PROV["sigma_um"],
                        umbral=MASCARA_PROV["umbral_nucleos_mm2"], p=MASCARA_PROV):
    """Raster de centroides a 8 µm/px, gaussiana σ 16 µm; tejido = densidad sobre el umbral fijo
    (núcleos/mm²), con dos correcciones selladas (MASCARA_PROV): el borde con el vidrio se
    corrige en δ = −σ·Φ⁻¹(umbral/D) (la distancia y el área son las del borde REAL, no las de la
    máscara ensanchada), y los valles entre dos tejidos densos se abren (fragmentos que el umbral
    fijo fundía). Fragmentos = componentes ≥ 0,2 mm² (id 0..k-1; el resto, −1). La distancia
    cuenta el borde escaneado como borde (relleno de ceros)."""
    from scipy.ndimage import (binary_dilation, binary_fill_holes, distance_transform_edt,
                               gaussian_filter, label)
    from scipy.special import ndtri
    from skimage.morphology import disk
    f = mpp / mpp_l0
    W, H = int(math.ceil(dims_l0[0] / f)), int(math.ceil(dims_l0[1] / f))
    cuenta = np.zeros((H, W), np.float64)
    xy = np.asarray(xy_l0, np.float64).reshape(-1, 2)
    # Reparto bilineal entre los 4 centros de píxel vecinos: sin él, cada núcleo se corre hasta
    # medio píxel (4 µm) hacia su esquina y el borde lo hereda.
    u, v = xy[:, 0] / f - 0.5, xy[:, 1] / f - 0.5
    u0, v0 = np.floor(u).astype(int), np.floor(v).astype(int)
    fu, fv = u - u0, v - v0
    for du, dv, w in ((0, 0, (1 - fu) * (1 - fv)), (1, 0, fu * (1 - fv)),
                      (0, 1, (1 - fu) * fv), (1, 1, fu * fv)):
        np.add.at(cuenta, (np.clip(v0 + dv, 0, H - 1), np.clip(u0 + du, 0, W - 1)), w)
    dens = gaussian_filter(cuenta, sigma_um / mpp, mode="constant") / (mpp * mpp) * 1e6
    m0 = dens > umbral
    # Huecos pequeños (< `hueco_max_um2`): huecos de ruido del estroma laxo, no vidrio; dentro.
    huecos = binary_fill_holes(m0) & ~m0
    if huecos.any():
        lab_h, nh = label(huecos)
        tam = np.bincount(lab_h.ravel(), minlength=nh + 1) * mpp * mpp
        chico = tam < p["hueco_max_um2"]
        chico[0] = False
        m0 = m0 | chico[lab_h]

    def edt_a(fondo):
        # distancia (µm) de cada centro de píxel al BORDE de `fondo` (medio píxel antes del
        # centro del píxel de fondo más cercano); el exterior del raster cuenta como fondo
        return (distance_transform_edt(np.pad(~fondo, 1, constant_values=False))[1:-1, 1:-1]
                - 0.5) * mpp

    # Valles: densidad bajo `fraccion_valle` × el tejido que hay A LOS DOS LADOS (a ±r): el hueco
    # entre dos fragmentos próximos. Junto a ellos, el borde se pone donde la densidad cae a la
    # mitad del tejido de al lado (el borde de un escalón suavizado), sin tocar nada lejos de un
    # valle: el estroma laxo junto a tumor denso NO es un valle (solo tiene tejido a un lado).
    r_px = p["radio_valle_um"] / mpp
    uno, dos = _anillo(dens, r_px)
    valle = m0 & (dos >= p["densidad_min_valle"]) & (dens < p["fraccion_valle"] * dos)
    # Un hueco entre fragmentos es una LÍNEA larga; los valles cortos son ruido del estroma laxo.
    if valle.any():
        from scipy.ndimage import find_objects
        lab_v, nv = label(valle, structure=np.ones((3, 3)))
        largo = np.zeros(nv + 1, bool)
        for i, sl in enumerate(find_objects(lab_v), start=1):
            ext = max(sl[0].stop - sl[0].start, sl[1].stop - sl[1].start) * mpp
            largo[i] = ext >= p["valle_min_largo_um"]
        valle = largo[lab_v]
    quitar = valle.copy()
    if valle.any():
        cerca = binary_dilation(valle, structure=disk(max(1, int(round(r_px)))))
        quitar |= m0 & cerca & (dens < 0.5 * uno)
    m = m0 & ~quitar
    # Meseta D (para δ): media local (σ_L) de la densidad en el interior de la máscara (> 2σ del
    # borde); donde no hay interior, sobre toda la máscara.
    s_l = p["sigma_meseta_um"] / mpp

    def media_local(valores, donde):
        num = gaussian_filter(np.where(donde, valores, 0.0), s_l, mode="constant")
        den = gaussian_filter(donde.astype(np.float64), s_l, mode="constant")
        return np.where(den > 1e-6, num / np.maximum(den, 1e-12), np.nan)

    interior = m & (edt_a(~m) > 2.0 * sigma_um)
    D = media_local(dens, interior)
    D = np.where(np.isfinite(D), D, media_local(dens, m))
    D = np.nan_to_num(D, nan=0.0)
    delta = np.clip(-sigma_um * ndtri(np.clip(umbral / np.maximum(D, 1e-9), 1e-6, 0.5)), 0.0,
                    3.0 * sigma_um)
    # Distancia al borde REAL: al vidrio (borde del umbral fijo, corregido δ) o a lo quitado junto
    # a un valle (borde ya puesto en la mitad del escalón, sin corrección).
    d_vidrio = edt_a(~m0) - delta
    d_valle = edt_a(quitar) if quitar.any() else np.full(m.shape, np.inf)
    dist = np.where(m, np.minimum(d_vidrio, d_valle), 0.0)
    cubre = np.where(m, np.clip((dist + mpp / 2.0) / mpp, 0.0, 1.0), 0.0)
    lab, n = label(m)
    area_px_mm2 = (mpp / 1000.0) ** 2
    a_comp = np.bincount(lab.ravel(), weights=cubre.ravel(), minlength=n + 1) * area_px_mm2
    frag = np.full(m.shape, -1, np.int32)
    areas = []
    k = 0
    for i in range(1, n + 1):
        if a_comp[i] >= FRAGMENTO_MIN_MM2:
            frag[lab == i] = k
            areas.append(float(a_comp[i]))
            k += 1
    return {"mascara": m & (dist >= 0), "mascara_umbral_fijo": m0, "valle": quitar,
            "densidad": dens, "meseta": D, "delta_um": delta, "fragmentos": frag,
            "areas_mm2": areas, "distancia_um": np.maximum(dist, 0.0), "mpp": mpp, "f": f,
            "umbral": umbral, "sigma_um": sigma_um}


def _muestrea(prov, clave, xy_l0, fuera):
    xy = np.asarray(xy_l0, np.float64).reshape(-1, 2)
    mapa = prov[clave]
    H, W = mapa.shape
    xi = (xy[:, 0] / prov["f"]).astype(int)
    yi = (xy[:, 1] / prov["f"]).astype(int)
    ok = (xi >= 0) & (yi >= 0) & (xi < W) & (yi < H)
    out = np.full(len(xy), fuera, dtype=mapa.dtype)
    out[ok] = mapa[yi[ok], xi[ok]]
    return out


def fragmento_de(prov, xy_l0):
    return _muestrea(prov, "fragmentos", xy_l0, -1)


def distancia_borde(prov, xy_l0):
    """Distancia (µm) al borde de fragmento, interpolada (bilineal) entre centros de píxel en la
    posición continua del centroide; fuera de la máscara, 0."""
    from scipy.ndimage import map_coordinates
    xy = np.asarray(xy_l0, np.float64).reshape(-1, 2)
    if not len(xy):
        return np.zeros(0)
    d = map_coordinates(prov["distancia_um"], [xy[:, 1] / prov["f"] - 0.5,
                                               xy[:, 0] / prov["f"] - 0.5],
                        order=1, mode="constant", cval=0.0)
    dentro = _muestrea(prov, "mascara", xy, False)
    return np.where(dentro, np.maximum(d, 0.0), 0.0)


def cambio_rejilla(xy_a, xy_b, prov):
    """Recuento por fragmento con la rejilla original y la desplazada: cambio relativo."""
    fa, fb = fragmento_de(prov, xy_a), fragmento_de(prov, xy_b)
    por = {}
    peor = 0.0
    for fr in range(len(prov["areas_mm2"])):
        na, nb = int((fa == fr).sum()), int((fb == fr).sum())
        c = abs(nb - na) / na if na else (0.0 if nb == 0 else float("inf"))
        por[str(fr)] = {"n": na, "n_desplazada": nb, "cambio": c}
        peor = max(peor, c)
    return {"por_fragmento": por, "peor": peor,
            "pasa": bool(por) and peor < COMPROBACION["cambio_rejilla_max"]}


# ── detector clásico B ────────────────────────────────────────────────────────────────────────
def detector_clasico(img, mpp, umbral=None, p=DETECTOR_B):
    """Watershed sobre una imagen de OD (ODsum para núcleos; DAB OD con T para DAB+), a L0.
    Umbral: Otsu sobre los píxeles de tejido (OD > odsum_tejido) si no se da. Devuelve
    (etiquetas, centroides xy en px, áreas µm², umbral)."""
    from scipy.ndimage import binary_fill_holes, distance_transform_edt
    from skimage.feature import peak_local_max
    from skimage.filters import gaussian, threshold_otsu
    from skimage.measure import label, regionprops
    from skimage.segmentation import watershed
    s = gaussian(np.asarray(img, np.float64), sigma=p["sigma_um"] / mpp, preserve_range=True)
    if umbral is None:
        t = s[s > p["odsum_tejido"]]
        umbral = float(threshold_otsu(t)) if t.size > 16 else float("inf")
    fg = binary_fill_holes(s > umbral)
    dist = distance_transform_edt(fg)
    comp = label(fg)
    picos = peak_local_max(dist, min_distance=max(1, int(round(p["dist_min_um"] / mpp))),
                           labels=comp, exclude_border=False)
    marcas = np.zeros(fg.shape, np.int32)
    marcas[tuple(picos.T)] = np.arange(1, len(picos) + 1)
    lab = watershed(-dist, marcas, mask=fg)
    a_px = mpp * mpp
    out = np.zeros_like(lab)
    cents, areas = [], []
    k = 0
    for r in regionprops(lab):
        a = r.area * a_px
        if p["area_min_um2"] <= a <= p["area_max_um2"]:
            k += 1
            out[lab == r.label] = k
            # regionprops da el centroide en índices de píxel (centro del píxel i = i); en la
            # convención continua del módulo (píxel i = [i, i+1)) es +0,5.
            cents.append((r.centroid[1] + 0.5, r.centroid[0] + 0.5))
            areas.append(a)
    return out, np.asarray(cents, np.float64).reshape(-1, 2), np.asarray(areas), umbral


def cobertura(xy_px, etiquetas_a):
    """Fracción de objetos (centroides en coordenadas CONTINUAS: el píxel i cubre [i, i+1), la
    convención de `rasteriza`) cubiertos por un núcleo de la segmentación A."""
    xy = np.asarray(xy_px, np.float64).reshape(-1, 2)
    if not len(xy):
        return None
    xi = np.clip(np.floor(xy[:, 0]).astype(int), 0, etiquetas_a.shape[1] - 1)
    yi = np.clip(np.floor(xy[:, 1]).astype(int), 0, etiquetas_a.shape[0] - 1)
    return float((etiquetas_a[yi, xi] > 0).mean())


def pares_iou(lab_a, lab_b, iou_min=0.5):
    """Pares (etiqueta de A, etiqueta de B) con IoU > `iou_min` (únicos por construcción con
    0,5), con su IoU y el área en píxeles de cada uno: dict de arrays."""
    a, b = np.asarray(lab_a).ravel(), np.asarray(lab_b).ravel()
    vacio = {k: np.zeros(0, np.int64) for k in ("a", "b", "area_a", "area_b")}
    sel = (a > 0) & (b > 0)
    if not sel.any():
        return dict(vacio, iou=np.zeros(0))
    pares, inter = np.unique(np.stack([a[sel], b[sel]]), axis=1, return_counts=True)
    area_a = np.bincount(a)
    area_b = np.bincount(b)
    iou = inter / (area_a[pares[0]] + area_b[pares[1]] - inter)
    ok = iou > iou_min
    return {"a": pares[0][ok], "b": pares[1][ok], "iou": iou[ok],
            "area_a": area_a[pares[0][ok]], "area_b": area_b[pares[1][ok]]}


def f1_iou(lab_a, lab_b, iou_min=0.5):
    """Emparejamiento por IoU > 0,5 (único por construcción) entre dos segmentaciones."""
    a, b = lab_a.ravel(), lab_b.ravel()
    na, nb = int(len(np.unique(a[a > 0]))), int(len(np.unique(b[b > 0])))
    if not ((a > 0) & (b > 0)).any() or not na or not nb:
        return {"tp": 0, "n_a": na, "n_b": nb, "f1": 0.0, "cociente": (nb / na) if na else None}
    tp = int(len(pares_iou(lab_a, lab_b, iou_min)["a"]))
    return {"tp": tp, "n_a": na, "n_b": nb, "f1": 2 * tp / (na + nb), "cociente": nb / na,
            "precision": tp / nb, "recall": tp / na}


def quita_borde(lab, margen):
    """Etiquetas que tocan el marco (a < `margen` px del borde) fuera: un núcleo cortado por el
    borde de la tesela no mide su área (LazySlide hace lo mismo con los suyos)."""
    lab = np.asarray(lab).copy()
    m = int(max(1, margen))
    borde = np.unique(np.concatenate([lab[:m].ravel(), lab[-m:].ravel(), lab[:, :m].ravel(),
                                      lab[:, -m:].ravel()]))
    lab[np.isin(lab, borde[borde > 0])] = 0
    return lab


def puerta_deteccion(cobertura, densidad=None, max_puntos=5.0, max_densidad=0.15):
    """Recall por clase sin verdad terreno (lista post-congelación, punto 5): (1) fracción de los
    objetos del detector clásico de cada clase (`dab`, `h`) cubierta por un núcleo InstanSeg, por
    estrato (fragmento, tercil de foco): diferencia entre clases < 5 puntos; (3) densidad nuclear
    en el MISMO polígono FC entre láminas: diferencia < 15 %. Si no pasa: «detection-dependent» y
    el % se da como rango [InstanSeg; detector clásico].
    `cobertura` = {estrato: {"dab": f, "h": f}}; `densidad` = {estrato: [d1, d2, …]}."""
    malos = []
    for e, c in cobertura.items():
        dif = abs(float(c["dab"]) - float(c["h"])) * 100
        if dif >= max_puntos:
            malos.append({"estrato": e, "diferencia_puntos": dif})
    for e, ds in (densidad or {}).items():
        ds = [float(x) for x in ds]
        rel = (max(ds) - min(ds)) / max(ds) if max(ds) > 0 else 0.0
        if rel >= max_densidad:
            malos.append({"estrato": e, "diferencia_densidad": rel})
    return {"pasa": not malos, "fallos": malos,
            "rotulos": [] if not malos else [ROTULOS["deteccion"]]}


# ── reglas clásicas de artefacto ──────────────────────────────────────────────────────────────
def foco(gris_l0):
    """Cociente de energía del laplaciano: L0 frente a L0 a la mitad (media 2×2)."""
    from scipy.ndimage import laplace
    g = np.asarray(gris_l0, np.float64)
    h, w = (g.shape[0] // 2) * 2, (g.shape[1] // 2) * 2
    g = g[:h, :w]
    mitad = g.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))
    e0 = float(np.var(laplace(g)))
    e1 = float(np.var(laplace(mitad)))
    return e0 / e1 if e1 > 0 else float("nan")


def corte_tercil_foco(cocientes, fraccion_tejido):
    """Corte del tercil bajo, solo con teselas de tejido (≥ 50 % en la máscara provisional) y
    dentro de una sola tinción (lo garantiza quien llama: una lámina a la vez)."""
    c = np.asarray(cocientes, np.float64)
    t = np.asarray(fraccion_tejido, np.float64) >= ARTEFACTO["foco_tejido_min"]
    ok = t & np.isfinite(c)
    if ok.sum() < 3:
        return None
    return float(np.percentile(c[ok], 100.0 / 3.0))


def pliegues(odsum, tejido, mpp=ARTEFACTO["pliegue_mpp"]):
    """Pliegue = bandas ≥ 50 µm de ancho con ODsum sobre el p99,5 del tejido de la lámina
    (apertura con un disco de 50 µm de diámetro). Devuelve (máscara, p99,5)."""
    import laminillas_color as C
    s = np.asarray(odsum, np.float64)
    tej = np.asarray(tejido, bool)
    if not tej.any():
        return np.zeros_like(tej), None
    p = float(np.percentile(s[tej], ARTEFACTO["pliegue_percentil"]))
    m = (s > p) & tej
    r = max(1, int(round(ARTEFACTO["pliegue_ancho_um"] / 2.0 / mpp)))
    return C.morfologia(m, r, "apertura"), p


def candidatos_pliegue(odsum, tejido, pliegue, mpp=ARTEFACTO["pliegue_mpp"]):
    """Pliegues que la regla del plan no ve (LIMITE_PLIEGUE): bandas ≥ 50 µm con ODsum ≥ 2× la
    mediana del tejido que la regla dejó sin marcar en su mayor parte (< 50 % marcado). NO se
    excluyen: van a la galería del tribunal (un nido denso da la misma ODsum que un doble
    grosor). Devuelve la fracción de tejido y las regiones (área ENTERA en µm², fracción que la
    regla sí marcó, centro en px de este raster), de mayor a menor."""
    import laminillas_color as C
    from scipy.ndimage import center_of_mass, label
    s = np.asarray(odsum, np.float64)
    tej = np.asarray(tejido, bool)
    pl = np.asarray(pliegue, bool)
    vacio = {"fraccion_tejido": 0.0, "regiones": [], "umbral_odsum": None,
             "factor_mediana": ARTEFACTO["pliegue_candidato_factor_mediana"],
             "limite": LIMITE_PLIEGUE}
    if not tej.any():
        return vacio
    u = ARTEFACTO["pliegue_candidato_factor_mediana"] * float(np.median(s[tej]))
    r = max(1, int(round(ARTEFACTO["pliegue_ancho_um"] / 2.0 / mpp)))
    # un cierre pequeño (4 µm) antes: un píxel de ruido bajo el umbral dentro de la banda no
    # debe partirla en dos al abrir con el disco de 50 µm
    m = C.morfologia(C.morfologia((s >= u) & tej, 2, "cierre") & tej, r, "apertura")
    lab, n = label(m)
    regiones = []
    total = 0
    if n:
        tam = np.bincount(lab.ravel(), minlength=n + 1)
        marcado = np.bincount(lab.ravel(), weights=pl.ravel().astype(np.float64),
                              minlength=n + 1)
        centros = center_of_mass(m, lab, range(1, n + 1))
        ids = [i for i in range(1, n + 1) if marcado[i] < 0.5 * tam[i]]
        total = int(sum(tam[i] for i in ids))
        ids = sorted(ids, key=lambda i: -tam[i])[:ARTEFACTO["pliegue_candidatos_max"]]
        regiones = [{"area_um2": float(tam[i] * mpp * mpp),
                     "marcado_por_la_regla": float(marcado[i] / tam[i]),
                     "centro_px": [float(centros[i - 1][1]) + 0.5,
                                   float(centros[i - 1][0]) + 0.5]}
                    for i in ids]
    return dict(vacio, fraccion_tejido=float(total / tej.sum()), regiones=regiones,
                umbral_odsum=u)


# ── GrandQC (opcional) ────────────────────────────────────────────────────────────────────────
def _grandqc(wsi, zona, mpp_l0, dispositivos=None):
    """Artefactos de GrandQC (variante 7x, 1,5 µm/px), offline, con los pesos VERIFICADOS. Plan:
    «MPS; si no, CPU»: un fallo en MPS (op no soportada, memoria de Metal) se reintenta en CPU y
    se dice. Devuelve {'carga': bool, 'artefactos': [(geom, clase)], 'motivo', 'dispositivo',
    'intentos', 'pesos'}; si no carga en ninguno, el motivo (rige la regla clásica)."""
    import lazyslide as zs
    intentos = []
    try:
        modelo, pesos = _modelo_grandqc()
        zs.pp.tile_tissues(wsi, 512, mpp=GRANDQC["mpp"], slide_mpp=mpp_l0,
                           stride_px=TESELA["stride_px"], edge=True, background_filter=False,
                           tissue_key="zona", key_added="teselas_grandqc")
    except Exception as e:                                         # noqa: BLE001
        return {"carga": False, "artefactos": [], "motivo": "%s: %s" % (type(e).__name__, e),
                "dispositivo": None, "intentos": intentos, "pesos": None}
    if dispositivos is None:
        dispositivos = [dispositivo()] + (["cpu"] if dispositivo() != "cpu" else [])
    for dev in dispositivos:
        try:
            zs.seg.artifact(wsi, tile_key="teselas_grandqc", model=modelo,
                            variant=GRANDQC["variante"], threshold=GRANDQC["umbral"],
                            device=dev, batch_size=1, key_added="artefactos_grandqc",
                            pbar=False)
        except Exception as e:                                     # noqa: BLE001
            intentos.append({"dispositivo": dev, "error": "%s: %s" % (type(e).__name__, e)})
            continue
        intentos.append({"dispositivo": dev, "error": None})
        art = []
        if "artefactos_grandqc" in wsi.shapes:
            g = wsi["artefactos_grandqc"]
            art = list(zip(g.geometry, g["class"].astype(str)))
        return {"carga": True, "motivo": None, "artefactos": art, "dispositivo": dev,
                "intentos": intentos, "pesos": pesos}
    return {"carga": False, "artefactos": [], "dispositivo": None, "intentos": intentos,
            "pesos": pesos, "motivo": "; ".join("%s: %s" % (i["dispositivo"], i["error"])
                                               for i in intentos)}


def fracciones_grandqc(artefactos, xy_l0, prov):
    """Fracción marcada por clase, sobre núcleos y sobre área con núcleos (máscara provisional),
    por fragmento; y la unión de clases, que es la que decide (> 20 % en algún fragmento)."""
    import shapely
    xy = np.asarray(xy_l0, np.float64).reshape(-1, 2)
    fr = fragmento_de(prov, xy)
    H, W = prov["mascara"].shape
    yy, xx = np.mgrid[0:H, 0:W]
    px = (xx.ravel() + 0.5) * prov["f"]
    py = (yy.ravel() + 0.5) * prov["f"]
    fmap = prov["fragmentos"].ravel()
    clases = sorted({c for _, c in artefactos})
    marca_n = {c: np.zeros(len(xy), bool) for c in clases}
    marca_a = {c: np.zeros(len(px), bool) for c in clases}
    for geom, c in artefactos:
        shapely.prepare(geom)
        marca_n[c] |= shapely.contains_xy(geom, xy[:, 0], xy[:, 1])
        marca_a[c] |= shapely.contains_xy(geom, px, py)
    union_n = np.zeros(len(xy), bool)
    union_a = np.zeros(len(px), bool)
    for c in clases:
        union_n |= marca_n[c]
        union_a |= marca_a[c]
    por = {}
    peor = 0.0
    for k in range(len(prov["areas_mm2"])):
        sn, sa = fr == k, fmap == k
        d = {"nucleos": {c: float(marca_n[c][sn].mean()) if sn.any() else 0.0 for c in clases},
             "area": {c: float(marca_a[c][sa].mean()) if sa.any() else 0.0 for c in clases}}
        d["union_nucleos"] = float(union_n[sn].mean()) if sn.any() else 0.0
        d["union_area"] = float(union_a[sa].mean()) if sa.any() else 0.0
        peor = max(peor, d["union_nucleos"], d["union_area"])
        por[str(k)] = d
    return {"por_fragmento": por, "peor": peor, "excede": peor > GRANDQC["max_fraccion"],
            "marcado_nucleo": union_n}
