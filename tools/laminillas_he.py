#!/usr/bin/env python3
"""tools/laminillas_he.py — F3, parte B del plan «laminillas DFCI»: la H&E del primario (P-HE), el
hueso (B-HE-1, B-HE-2) y el exploratorio.

Lo lanza SOLO la ventanilla (`lector_clinico.py procesa laminillas_he -- <orden> …`), en
`analisis.sb`, venv `patologia`, cwd = SESION, entorno en lista blanca (HF_HUB_OFFLINE=1), con la
memoria de pesado y el cerrojo de la ventanilla. Órdenes (lista cerrada `ORDENES`):

  procesa laminillas_he -- primario [P-HE]            (pesado: HistoPLUS, NuLite, WSInfer)
  procesa laminillas_he -- hueso [B-HE-1] [B-HE-2]    (sin modelos; por defecto, las dos)
  procesa laminillas_he -- explora                    (pesado: UNI2-h, H-optimus-1, TITAN)
  procesa laminillas_he -- cellvitpp                  (sin píxeles: escribe el formato del enganche)

CÓDIGOS: 0 hecho; 2 uso; 3 sello ausente o inválido (NO MIDO); 4 no pasa; 5 falta un producto
previo (dice qué orden lo produce).

1. P-HE (`primario`). Necesita el sello (0) y los núcleos de InstanSeg con la configuración
   sellada (`procesa laminillas -- piloto-ibis P-HE`): InstanSeg SEGMENTA; HistoPLUS (variante
   40x, sobre L0 —0,2506 µm/px tomado como 40x, sin remuestrear—, por LazySlide, MPS, sin
   xformers) y NuLite (H, 40x) CLASIFICAN: cada uno detecta sus núcleos y su clase se pasa al
   núcleo de InstanSeg con el que casa (vecino más próximo MUTUO a ≤ 4 µm). Mini-puerta
   (`mini_puerta`): cobertura de los núcleos de InstanSeg, kappa sobre la taxonomía común y
   diferencia de la fracción neoplásica e inflamatoria por fragmento → manda HistoPLUS (principal
   del plan), manda NuLite (si HistoPLUS no está o no cubre), se da el RANGO de los dos, o no hay
   clasificación. Regiones sobre la rejilla del TorchScript de WSInfer
   `breast-tumor-resnet34.tcga-brca` (350 px a 0,25 µm/px): tumoral = densidad de núcleos
   neoplásicos ≥ umbral Y probabilidad WSInfer ≥ 0,5 (las teselas con una sola fuente van
   aparte); estroma (conectivo); grasa = huecos sin núcleos (sin anillo epitelial: una luz no es
   grasa); necrosis SIN CLASIFICAR. Infiltrado: «densidad de infiltrado inflamatorio en el
   estroma intratumoral (aproximación)», nunca el índice del TIL-WG. NINGÚN MODELO SEPARA
   INVASIVO DE IN SITU: va en la salida. CellViT++ NO corre aquí (necesita CUDA): `CELLVITPP`
   fija el enganche y el formato de entrada y salida; si su salida está en SESION/nube/cellvitpp/
   y pasa el esquema, entra en la mini-puerta como comparador. Sus clases se traducen a la
   taxonomía común AL LEERLAS: el nombre crudo del modelo (nucls_super tiene una clase que se llama
   como el índice estromal de TIL que esta salida veta) no entra nunca en la salida; Métodos nombra
   cada clase por índice y descripción saneada (`CELLVITPP_CLASES`).
2. Hueso (`hueso`): QC y composición de TEJIDO por área (trabécula, espacio medular, adipocitos)
   por color (desmezcla H&E de Ruifrok) y textura a 4 µm/px. SIN clasificador nuclear ni recuento
   celular. Rótulo: «reported negative for neoplasia by two independent pathology reads; not
   screened for tumour by Polaris». B-HE-2 es «no atribuible» y su salida lo dice. No exige el
   sello: no mide células ni usa T ni vectores de tanda (declarado).
3. Exploratorio (`explora`): FUERA DE LAS CIFRAS. Solo en fragmentos de P-HE que pasan (iii).
   Teselas tumorales de 256 µm (512 px a 0,5 µm/px); embeddings UNI2-h y H-optimus-1 (media de
   2×2 recortes de 224 px a 0,5 µm/px) y TITAN (CONCH v1.5 por tesela + codificador de TITAN
   sobre la vecindad 3×3); frente a la positividad registrada (% de la región de
   `piloto/metricas/<L>.json` en el marco de P-CK19) de RE, Ki67, SYN, CHGA e {{DIANA3}} (simetría NE;
   RP y RA si están). Ridge sobre PCA ajustada en el pliegue, validación dejando fuera un FRAGMENTO
   ENTERO, comparadores triviales (media del entrenamiento y densidad nuclear). Si no hay señal,
   lo dice (`ROTULOS["sin_senal"]`).

PESOS: todos del commit sellado en `laminillas_stack/pesos.json` (cuyo sha256 se coteja con su
`.sha256`), con bytes y sha256 de cada fichero comprobados ANTES de cargar; los envoltorios que
resuelven `refs/main` solo se usan si `refs/main` es el commit sellado.

INFERENCIAS MÍAS (el plan no da cifra; van a Métodos con `PARAMS`, `HUESO_P` y `EXPLORA_P`):
radio de emparejado 4 µm; cobertura ≥ 70 %; kappa ≥ 0,60; diferencias ≤ 5 puntos por fragmento;
densidad neoplásica ≥ 1.000/mm² (sensibilidad ×0,5 y ×2); estroma intratumoral = estroma dentro
de la envolvente convexa del tumor de su fragmento y a ≤ 2 teselas de él; grasa = huecos ≥ 1.000
µm² sin núcleo dentro ni anillo epitelial (≥ 3 núcleos por 100 µm de borde); umbrales de color y
textura del hueso (sensibilidad ×0,8 y ×1,25); criterio de señal del exploratorio (IC95 de ρ por
bootstrap de teselas > 0 y MAE menor que los dos triviales).
"""
import hashlib
import json
import math
import os
import re
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import laminillas_comun as C  # noqa: E402

_AQUI = os.path.dirname(os.path.abspath(__file__))
PESOS = os.path.join(_AQUI, "laminillas_stack", "pesos.json")

ORDENES = ("primario", "hueso", "explora", "cellvitpp")
DIR_HE = "he"
PRIMARIO = "P-HE"
HUESO = ("B-HE-1", "B-HE-2")
RC_USO, RC_SELLO, RC_NO_PASA, RC_FALTA = 2, 3, 4, 5

# ── taxonomía común de clases nucleares ───────────────────────────────────────────────────────
COMUN = ("neoplastic", "inflammatory", "connective", "dead", "epithelial", "other")
NEO, INF, CON, DEAD, EPI, OTRO = range(len(COMUN))
# Nombres EXACTOS de las clases de cada envoltorio (lazyslide-models 0.0.4; el test los coteja con
# `HistoPLUS.classes` y `NuLite.classes`). Mitosis → «other»: una mitosis no es por sí neoplásica;
# hematíe → «other»: no es un núcleo. Se cuentan aparte.
A_COMUN = {
    "histoplus": {"Cancer cell": "neoplastic", "Lymphocytes": "inflammatory",
                  "Plasmocytes": "inflammatory", "Eosinophils": "inflammatory",
                  "Neutrophils": "inflammatory", "Macrophages": "inflammatory",
                  "Fibroblasts": "connective", "Muscle Cell": "connective",
                  "Endothelial Cell": "connective", "Minor Stromal Cell": "connective",
                  "Apoptotic Body": "dead", "Epithelial": "epithelial",
                  "Mitotic Figures": "other", "Red blood cell": "other"},
    "nulite": {"Neoplastic": "neoplastic", "Inflammatory": "inflammatory",
               "Connective": "connective", "Dead": "dead", "Epithelial": "epithelial"},
}
CLASIFICADORES = {
    # tesela y paso en px de L0 (el modelo corre sobre L0: 0,2506 µm/px ≈ 40x, sin remuestreo)
    "histoplus": dict(entrada="histoplus", fichero="histoplus_cellvit_segmentor_40x.pt",
                      magnificacion="40x", tesela_px=448, paso_px=392,
                      clase="lazyslide_models.segmentation.cellvit_family.histoplus.HistoPLUS"),
    "nulite": dict(entrada="lazyslide_models", fichero="NuLite/NuLite_H_exported.pt2",
                   magnificacion="40x", tesela_px=512, paso_px=448,
                   clase="lazyslide_models.segmentation.cellvit_family.nulite.NuLite"),
}
PRINCIPAL = ("histoplus", "nulite")          # plan, actualización 1: HistoPLUS manda si pasa
WSINFER = dict(entrada="wsinfer_brca", fichero="torchscript_model.pt", config="config.json",
               lote=32)
FUNDACIONALES = {
    "uni2h": dict(entrada="uni2h", ficheros=("pytorch_model.bin",), mpp=0.5, lado=224),
    "hoptimus1": dict(entrada="hoptimus1", ficheros=("model.safetensors",), mpp=0.5, lado=224),
    "titan": dict(entrada="titan", ficheros=("model.safetensors", "conch_v1_5_pytorch_model.bin"),
                  mpp=0.5, lado=512),
}

PARAMS = dict(
    emparejado_um=4.0,                 # casar núcleo del clasificador con núcleo de InstanSeg
    cobertura_min=0.70,                # fracción de núcleos de InstanSeg con clase
    kappa_min=0.60,
    dif_neo_max_pts=5.0,               # |Δ fracción neoplásica| por fragmento, puntos
    dif_inf_max_pts=5.0,               # |Δ fracción inflamatoria| por fragmento, puntos
    min_nucleos_fragmento=200,         # fragmentos con menos núcleos casados por los dos: fuera
    ws_corte=0.5,                      # p(Tumor) de WSInfer
    ws_tejido_min=0.25,                # fracción de la tesela en la envolvente de tejido
    neo_densidad_min_mm2=1000.0,       # núcleos neoplásicos por mm² de tejido de la tesela
    neo_min_n=3,
    neo_factores=(0.5, 2.0),           # sensibilidad del umbral de densidad
    estroma_frac=0.5,                  # conectivo + inflamatorio ≥ 50 % de los clasificados
    estroma_min_clasificados=3,        # por debajo: estroma paucicelular
    estroma_dist_teselas=2,            # estroma intratumoral: a ≤ 2 teselas del tumor
    tumor_min_teselas_envolvente=3,
    tejido_mpp=2.0,                    # ×8 nativo
    tejido_sigma_um=4.0,
    tejido_odsum=0.08,
    hueco_odsum=0.05,
    hueco_sigma_px=0.5,
    envolvente_cierre_um=24.0,
    grasa_area_min_um2=1000.0,
    grasa_anillo_um=8.0,
    grasa_anillo_max_100um=3.0,        # núcleos por 100 µm de borde: más es una luz con epitelio
    grasa_frac=0.5,                    # tesela de grasa: grasa ≥ 50 % de su envolvente
    hueco_grande_um2=50000.0,          # se cuentan aparte (lobulillo graso o desgarro)
)
HUESO_P = dict(
    mpp=4.0, franja_px=1024, margen_px=32, sigma_um=8.0, color_sigma_um=4.0, textura_sigma_um=8.0,
    tejido_odsum=0.08, hueco_odsum=0.06, envolvente_cierre_um=40.0,
    adipocito_area_um2=(1000.0, 40000.0), hfrac_trabecula=0.35, textura_trabecula=0.06,
    trabecula_min_um2=2000.0, factores=(0.8, 1.25), foco_teselas=200, foco_lado_px=512,
    semilla=20261002, borde_um=8.0, fragmento_min_mm2=0.2, septo_um=8.0,
)
EXPLORA_P = dict(
    tesela_mpp=0.5, tesela_px=512, sub_px=224, frac_tumor_min=0.5, region_min_n=50,
    min_teselas=10, min_fragmentos=2, iqr_min_pts=5.0, pca_max=16, ridge_alfa=1.0,
    n_boot=2000, semilla=20261002, titan_vecindad=1,
)
MARCADORES_EXPLORA = ("P-RE", "P-KI67", "P-SYN", "P-CHGA", "P-{{DIANA3}}", "P-RP", "P-RA")
MARCADORES_NE = ("P-SYN", "P-CHGA", "P-{{DIANA3}}")
RUIFROK_H = (0.65, 0.70, 0.29)
RUIFROK_E = (0.07, 0.99, 0.11)

ROTULOS = {
    "alcance": "of the scanned region",
    "invasivo": "invasive, in-situ and benign are not separated by any model used",
    "necrosis": "necrosis: not classified",
    "fraccion": "fraction of neoplastic-classified nuclei (InstanSeg nuclei with a class)",
    "infiltrado_es": "densidad de infiltrado inflamatorio en el estroma intratumoral (aproximación)",
    "infiltrado_en": "density of inflammatory infiltrate in intratumoral stroma (approximation)",
    "infiltrado_no": "not the TIL Working Group stromal score",
    "infiltrado_caido": "not reported: no nuclear classifier passed the gate",
    "rango": "classifier-dependent: reported as the range of the classifiers that pass the gate",
    "solo_uno": "single classifier: the others are not available or do not pass coverage",
    "sin_clasificacion": "nuclear classification not reliable: no classifier passes coverage",
    "sin_histoplus": "HistoPLUS not available",
    "sin_wsinfer": "WSInfer not available: tumour region from nuclear classification only",
    "solo_wsinfer": "no nuclear classification: tumour region from WSInfer only",
    "grasa": "fat = enclosed empty spaces without nuclei and without an epithelial rim; a tear "
             "without nuclei is not separated from fat",
    "cellvitpp_no": "CellViT++ not run: it needs CUDA (>= 24 GB); cloud run only if {{TITULAR}} "
                    "approves a provider (plan step 7)",
    "hueso": "reported negative for neoplasia by two independent pathology reads; not screened "
             "for tumour by Polaris",
    "hueso_no_atribuible": "not attributable: no identity link between this slide and the bone "
                           "accession is established; not screened for tumour by Polaris",
    "hueso_sin_celulas": "tissue composition by area only: no nuclear classifier, no cell count",
    "seccion": "fraction of the section scanned: unknown, no macro image",
    "explora": "exploratory, outside the reported figures; descriptive, single block, n=1",
    "sin_senal": "no signal: the embeddings do not predict the registered IHC positivity better "
                 "than the trivial comparators",
    "explora_caido": "exploratory not run: no P-HE fragment passes the H&E registration (iii)",
    "no_estimable": "not estimable",
}
LICENCIAS = {
    "entregable": "non-commercial, research-only, non-clinical; no weights or code shipped",
    "HistoPLUS": "CC-BY-NC-ND 4.0; its outputs cannot be used commercially either",
    "NuLite": "weights CC BY-NC-SA 4.0 (README: Apache 2.0 with Commons Clause; LICENSE file: "
              "plain Apache-2.0; declared)",
    "InstanSeg": "Apache-2.0",
    "WSInfer breast-tumor-resnet34.tcga-brca": "not in the plan's licence review: to be checked "
                                                "before F4",
    "UNI2-h": "CC-BY-NC-ND 4.0", "H-optimus-1": "CC-BY-NC-ND 4.0",
    "TITAN + CONCH v1.5": "CC-BY-NC-ND 4.0",
    "CellViT++": "Apache-2.0 with Commons Clause (not run here)",
}
USO_DECLARADO = ("decision 5 (declared to MahmoodLab/Bioptimus): non-commercial academic research; "
                 "exploratory feature extraction to generate hypotheses; not used for diagnosis or "
                 "treatment decisions; H-optimus-1 use-case: Biomarker Discovery. This order is "
                 "exploratory and outside the reported figures, which matches that use")
# Formato del enganche de CellViT++ (plan, «Potencia y nube»). No corre aquí.
CELLVITPP = {
    "estado": ROTULOS["cellvitpp_no"],
    "entrada": {
        "lamina": PRIMARIO,
        "copia": "N1 copy of the full scanned P-HE file written by exporta-n1 (opaque name, "
                 "pixels not recompressed, whitelisted TIFF tags)",
        "mpp_l0": "the manifest mpp of P-HE",
        "modelo": "CellViT-SAM-H-x40 (CellViT++ 1.0.9; never the UNI/Virchow backbones)",
        "clasificadores": "nucls_* and panoptils (plan)",
        "via": "tools/nube_n1.py subir/lanzar/bajar, only after the provider audit and {{TITULAR}}'s "
               "trust-cloud (plan steps 5-bis, 6 and 7)",
    },
    "salida": {
        "carpeta": "nube/cellvitpp",
        "ficheros": "P-HE.<clasificador>.csv + P-HE.<clasificador>.json, one pair per classifier",
        "csv_columnas": ["x_l0", "y_l0", "clase", "prob"],
        "json_campos": {"modelo": "str", "version": "1.0.9", "clasificador": "[A-Za-z0-9_]+",
                        "lamina": PRIMARIO, "mpp_l0": "float (within 1 % of the manifest)",
                        "n": "int = CSV rows", "sha256_csv": "sha256 of the CSV",
                        "mapa_clases": "{original class: one of %s}" % ", ".join(COMUN)},
        "coordenadas": "nucleus centroid in L0 pixels of the same P-HE file",
    },
    "uso": "comparator in the H&E mini-gate, matched to InstanSeg nuclei like HistoPLUS/NuLite",
}
CELLVITPP_VERSION = "1.0.9"
CELLVITPP_MODELO = "CellViT-SAM-H-x40"
# Clases de los clasificadores de CellViT++ 1.0.9 (classifier.zip sha256 08a7e788…, `data.label_map`
# de cada checkpoint; nube_n1_cloudinit/config.json, mapa_origen): nombre CRUDO → (índice, descripción
# saneada). Es el ÚNICO sitio del que sale cómo se nombra una clase del modelo en la salida (Métodos):
# «<clasificador> class <índice>: <descripción>». Los nombres crudos son claves y nunca se copian; NO
# forma parte de `CELLVITPP` (que sí viaja en la salida). Una clase fuera de esta tabla: el par se
# rechaza. Descripciones mías (inferencia, revisables); los índices y nombres, del checkpoint.
CELLVITPP_CLASES = {
    "nucls_main": {"Tumor nonMitotic": (0, "tumour nucleus, non-mitotic"),
                   "Tumor Mitotic": (1, "tumour nucleus, mitotic"),
                   "nonTILnonMQ Stromal": (2, "stromal nucleus, neither lymphocyte nor macrophage"),
                   "Macrophage": (3, "macrophage"), "Lymphocyte": (4, "lymphocyte"),
                   "Plasma Cell": (5, "plasma cell"), "Other Nucleus": (6, "other nucleus")},
    "nucls_super": {"Tumor": (0, "tumour nucleus"), "nonTIL Stromal": (1, "stromal nucleus, not lymphocytic"),
                    "sTIL": (2, "stromal lymphocytes"), "Other": (3, "other nucleus")},
    "panoptils": {"Other Cells": (0, "other cells"), "Epithelial Cells": (1, "epithelial cells"),
                  "Stromal Cells": (2, "stromal cells"), "TILs": (3, "lymphocytes")},
}
_RE_STIL = re.compile(r"\bsTILs?\b")
_RE_CLASIF = re.compile(r"^[A-Za-z0-9_]{1,32}$")


class FaltaEntrada(RuntimeError):
    """Falta un producto previo (código 5)."""


class NoPasa(RuntimeError):
    """Un criterio no se cumple (código 4)."""


class CellvitppInvalido(ValueError):
    """La salida de CellViT++ no cumple el formato del enganche: no se usa."""


# ── utilidades ────────────────────────────────────────────────────────────────────────────────
def _np():
    import numpy as np
    return np


def _limpio(o):
    """JSON estricto: numpy → Python, NaN/inf → None, tuplas → listas, claves texto."""
    np = _np()
    if isinstance(o, dict):
        return {str(k): _limpio(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_limpio(v) for v in o]
    if isinstance(o, np.ndarray):
        return _limpio(o.tolist())
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        o = float(o)
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    return o


def _rel(*partes):
    return os.path.join(DIR_HE, *partes)


def _abs(base, rel):
    return os.path.join(base, rel)


def _escribe(base, rel, obj):
    os.makedirs(os.path.dirname(_abs(base, rel)), mode=0o700, exist_ok=True)
    C.escribe_json(_abs(base, rel), _limpio(obj))
    return rel


def _lee(base, rel):
    with open(_abs(base, rel), encoding="utf-8") as f:
        return json.load(f)


def _npz(base, rel, **arrays):
    np = _np()
    ruta = _abs(base, rel)
    os.makedirs(os.path.dirname(ruta), mode=0o700, exist_ok=True)
    tmp = ruta[:-len(".npz")] + ".parcial.npz"
    np.savez_compressed(tmp, **arrays)
    os.replace(tmp, ruta)
    os.chmod(ruta, 0o600)
    return rel


def _carga_npz(base, rel):
    np = _np()
    with np.load(_abs(base, rel), allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def _huella(base, rels):
    h = hashlib.sha256()
    for rel in sorted(set(rels)):
        p = _abs(base, rel)
        h.update(rel.encode("utf-8") + b"=")
        h.update((C.sha256_fichero(p) if os.path.isfile(p) else "-").encode("ascii") + b";")
    return h.hexdigest()[:16]


def _lector(lector):
    if lector is None:
        import laminillas_lector as lector
    return lector


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


def frases_prohibidas(obj):
    """«Nunca decir» del plan (`laminillas_metricas.NUNCA_DECIR`) más «sTIL»: el infiltrado es una
    aproximación y no se llama así. Vacío = limpio."""
    import laminillas_metricas as MET
    malas = list(MET.barre_nunca(obj))
    if any(_RE_STIL.search(s) for s in _cadenas(obj)):
        malas.append("sTIL")
    return malas


def _exige_limpio(obj):
    malas = frases_prohibidas(obj)
    if malas:
        raise RuntimeError("frase prohibida en la salida: %s" % ", ".join(malas))


def dispositivo():
    import torch
    return "mps" if torch.backends.mps.is_available() else "cpu"


def _vacia():
    import gc
    gc.collect()
    try:
        import torch
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:                                    # noqa: BLE001
        pass


# ── pesos sellados ────────────────────────────────────────────────────────────────────────────
def _pesos(pesos=PESOS):
    """pesos.json, verificado contra su `.sha256` (si no casa, nada carga)."""
    import laminillas_segmenta as S
    with open(pesos, "rb") as f:
        crudo = f.read()
    try:
        with open(pesos + ".sha256", encoding="utf-8") as f:
            sellado = f.read().split()[0]
    except (OSError, IndexError):
        raise S.PesoNoSellado("pesos.json sin su .sha256: no sé si es el sellado")
    if hashlib.sha256(crudo).hexdigest() != sellado:
        raise S.PesoNoSellado("pesos.json no casa con su .sha256 (¿editado?)")
    return json.loads(crudo.decode("utf-8"))["modelos"]


def _hf_home(hf_home=None):
    return hf_home or os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface")


def peso(entrada, fichero, pesos=PESOS, hf_home=None):
    """Ruta del `fichero` de la entrada `entrada` de pesos.json EN SU COMMIT SELLADO, con bytes y
    sha256 comprobados antes de cargar. Nunca baja nada ni sigue `refs/main`. (ruta, medido)."""
    import laminillas_segmenta as S
    ent = _pesos(pesos).get(entrada)
    if not ent:
        raise S.PesoNoSellado("%s no está en pesos.json" % entrada)
    meta = (ent.get("ficheros") or {}).get(fichero)
    commit, repo = ent.get("commit"), ent.get("repo")
    if not meta or not commit or not repo or not re.match(r"^[0-9a-f]{40}$", commit):
        raise S.PesoNoSellado("%s/%s no está sellado con commit en pesos.json" % (entrada, fichero))
    carpeta = "models--" + repo.replace("/", "--")
    ruta = os.path.join(_hf_home(hf_home), "hub", carpeta, "snapshots", commit, fichero)
    if not os.path.isfile(ruta):
        raise S.PesoNoSellado("%s no está en la caché en el commit sellado %s" % (fichero,
                                                                              commit[:12]))
    h, n = hashlib.sha256(), 0
    with open(ruta, "rb") as f:
        for trozo in iter(lambda: f.read(1 << 20), b""):
            h.update(trozo)
            n += len(trozo)
    medido = {"fichero": fichero, "repo": repo, "commit": commit, "bytes": n,
              "sha256": h.hexdigest()}
    if n != int(meta["bytes"]) or medido["sha256"] != meta["sha256"]:
        raise S.PesoNoSellado("%s: bytes o sha256 no son los sellados" % fichero)
    return ruta, medido


def refs_main_sellado(entrada, pesos=PESOS, hf_home=None):
    """Los envoltorios que cargan por `hf-hub:` siguen `refs/main` de la caché: solo se usan si
    apunta al commit sellado."""
    import laminillas_segmenta as S
    ent = _pesos(pesos)[entrada]
    carpeta = "models--" + ent["repo"].replace("/", "--")
    try:
        with open(os.path.join(_hf_home(hf_home), "hub", carpeta, "refs", "main"),
                  encoding="utf-8") as f:
            ref = f.read().strip()
    except OSError:
        raise S.PesoNoSellado("%s: la caché no tiene refs/main" % entrada)
    if ref != ent.get("commit"):
        raise S.PesoNoSellado("%s: refs/main (%s) no es el commit sellado (%s)" % (
            entrada, ref[:12], str(ent.get("commit"))[:12]))
    return ref


# ── carga de modelos (desde el fichero verificado) ────────────────────────────────────────────
def modelo_histoplus():
    """HistoPLUS 40x de LazySlide, con los pesos verificados (su constructor resuelve por
    `hf_hub_download`; aquí se salta y se carga la ruta sellada). Sin xformers: SwiGLU en PyTorch
    puro (se registra)."""
    import torch
    from lazyslide_models.segmentation.cellvit_family import histoplus as HP
    cfg = CLASIFICADORES["histoplus"]
    ruta, medido = peso(cfg["entrada"], cfg["fichero"])
    m = HP.HistoPLUS.__new__(HP.HistoPLUS)
    m.variant = cfg["magnificacion"]
    m.model = HP.HistoPLUSModel(backbone_tile_size=HP.HistoPLUS._backbone_tile_size["40x"])
    estado = torch.load(ruta, map_location="cpu")
    m.model.load_state_dict(HP.remap_state_dict(estado))
    m.model.eval()
    return m, dict(medido, xformers=bool(HP._xformers_available), clases=list(m.classes))


def modelo_nulite():
    import torch
    from lazyslide_models.segmentation.cellvit_family.nulite import NuLite
    cfg = CLASIFICADORES["nulite"]
    ruta, medido = peso(cfg["entrada"], cfg["fichero"])
    m = NuLite.__new__(NuLite)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="The given buffer is not writable")
        m.model = torch.export.load(ruta).module()
    m.magnification = cfg["magnificacion"]
    return m, dict(medido, clases=list(m.classes))


CARGA_CLASIFICADOR = {"histoplus": modelo_histoplus, "nulite": modelo_nulite}


def modelo_wsinfer():
    """TorchScript de WSInfer breast-tumor-resnet34.tcga-brca y su config, del commit sellado
    (sin `load_registry()`: offline intenta un cerrojo en ~/.wsinfer-zoo que la jaula no deja)."""
    import torch
    ruta, medido = peso(WSINFER["entrada"], WSINFER["fichero"])
    rc, medido_cfg = peso(WSINFER["entrada"], WSINFER["config"])
    with open(rc, encoding="utf-8") as f:
        cfg = json.load(f)
    red = torch.jit.load(ruta, map_location="cpu").eval()
    return red, cfg, {"modelo": medido, "config": medido_cfg}


def transforma_wsinfer(teselas, cfg):
    """(N, h, w, 3) uint8 → tensor (N, 3, s, s) con la transformación del config sellado
    (Resize, ToTensor, Normalize). Resize por interpolación bilineal con antialias sobre el tensor
    (equivalente a PIL salvo el redondeo: declarado). Otra transformación: error (no se adivina)."""
    import torch
    import torch.nn.functional as F
    x = torch.from_numpy(_np().ascontiguousarray(teselas)).permute(0, 3, 1, 2).float()
    escalado = False
    for paso in cfg.get("transform", []):
        nombre, arg = paso.get("name"), paso.get("arguments") or {}
        if nombre == "Resize":
            s = int(arg["size"])
            x = F.interpolate(x, size=(s, s), mode="bilinear", antialias=True,
                              align_corners=False)
        elif nombre == "ToTensor":
            x = x / 255.0
            escalado = True
        elif nombre == "Normalize":
            if not escalado:
                raise ValueError("Normalize antes de ToTensor: config inesperado")
            m = torch.tensor(arg["mean"]).view(1, 3, 1, 1)
            d = torch.tensor(arg["std"]).view(1, 3, 1, 1)
            x = (x - m) / d
        else:
            raise ValueError("transformación de WSInfer no soportada: %r" % nombre)
    return x


# ── P-HE: clasificación nuclear ───────────────────────────────────────────────────────────────
def codigos(clave, nombres, mapa=None):
    """Nombre de clase del clasificador → índice de COMUN (int8). Clase desconocida → error
    (fail-closed: un envoltorio que cambie sus clases no se mapea a ciegas)."""
    np = _np()
    mapa = mapa if mapa is not None else A_COMUN[clave]
    idx = {c: i for i, c in enumerate(COMUN)}
    out = np.empty(len(nombres), np.int8)
    for i, n in enumerate(nombres):
        n = str(n)
        if n not in mapa:
            raise ValueError("%s: clase %r fuera del mapa a la taxonomía común" % (clave, n))
        out[i] = idx[mapa[n]]
    return out


def asigna(xy_ref, xy_mod, radio_px):
    """Índice del núcleo del modelo casado con cada núcleo de referencia (InstanSeg), o −1:
    vecino más próximo MUTUO a ≤ `radio_px`."""
    np = _np()
    from scipy.spatial import cKDTree
    xy_ref = np.asarray(xy_ref, float).reshape(-1, 2)
    xy_mod = np.asarray(xy_mod, float).reshape(-1, 2)
    out = np.full(len(xy_ref), -1, np.int64)
    if not len(xy_ref) or not len(xy_mod):
        return out
    d1, i1 = cKDTree(xy_mod).query(xy_ref, distance_upper_bound=radio_px)
    d2, i2 = cKDTree(xy_ref).query(xy_mod, distance_upper_bound=radio_px)
    ok = np.isfinite(d1)
    r = np.flatnonzero(ok)
    j = i1[ok]
    mutuo = i2[j] == r
    out[r[mutuo]] = j[mutuo]
    return out


def clases_en_instanseg(xy_instanseg, res, clave, mpp_l0, mapa=None, p=PARAMS):
    """Clase común (int8, −1 sin casar) de cada núcleo de InstanSeg según un clasificador."""
    np = _np()
    cod = codigos(clave, res["clase"], mapa)
    j = asigna(xy_instanseg, res["centroide"], p["emparejado_um"] / mpp_l0)
    out = np.full(len(j), -1, np.int8)
    out[j >= 0] = cod[j[j >= 0]]
    return out


def kappa(a, b, k=len(COMUN)):
    """Kappa de Cohen entre dos vectores de códigos (mismos núcleos)."""
    np = _np()
    a, b = np.asarray(a, int), np.asarray(b, int)
    if not len(a):
        return None
    M = np.zeros((k, k))
    np.add.at(M, (a, b), 1)
    n = M.sum()
    po = np.trace(M) / n
    pe = float(M.sum(1) @ M.sum(0)) / n ** 2
    if pe >= 1.0:
        return 1.0 if po >= 1.0 else 0.0
    return float((po - pe) / (1.0 - pe))


def _fraccion(c, codigo):
    np = _np()
    casado = c >= 0
    n = int(casado.sum())
    return (float((c == codigo).sum()) / n) if n else float("nan"), n


def mini_puerta(comunes, fragmentos, p=PARAMS, principal=PRINCIPAL):
    """Mini-puerta HistoPLUS ↔ NuLite (y CellViT++ si está) sobre los núcleos de InstanSeg.

    `comunes`: {clave: int8 (n,) o None si el clasificador no está}. Elegible = cubre ≥
    `cobertura_min` de los núcleos. Manda el primero elegible de `principal` (o el primer elegible);
    con cada comparador elegible: kappa ≥ `kappa_min` y, por fragmento con ≥
    `min_nucleos_fragmento` núcleos casados por los dos, |Δ neoplásica| y |Δ inflamatoria| ≤ 5
    puntos. Todos de acuerdo → manda ese; alguno en desacuerdo → «rango» (todas las cifras como
    [mín, máx] de los elegibles). Ninguno elegible → «sin_clasificacion»."""
    np = _np()
    fragmentos = np.asarray(fragmentos)
    out = {"clasificadores": {}, "pares": {}, "criterio": {k: p[k] for k in (
        "cobertura_min", "kappa_min", "dif_neo_max_pts", "dif_inf_max_pts",
        "min_nucleos_fragmento", "emparejado_um")}, "rotulos": []}
    elegibles = []
    for k, c in comunes.items():
        if c is None:
            out["clasificadores"][k] = {"disponible": False, "elegible": False}
            if k == "histoplus":
                out["rotulos"].append(ROTULOS["sin_histoplus"])
            continue
        c = np.asarray(c)
        cob = float((c >= 0).mean()) if len(c) else 0.0
        por_f = {}
        for f in sorted(set(fragmentos[fragmentos >= 0].tolist())):
            m = fragmentos == f
            por_f["F%d" % f] = float((c[m] >= 0).mean()) if m.any() else None
        neo, n = _fraccion(c, NEO)
        el = cob >= p["cobertura_min"]
        out["clasificadores"][k] = {"disponible": True, "cobertura": cob,
                                    "cobertura_por_fragmento": por_f, "n_casados": n,
                                    "fraccion_neoplasica": neo, "elegible": el}
        if el:
            elegibles.append(k)
    orden = [k for k in principal if k in elegibles] + [k for k in elegibles
                                                        if k not in principal]
    if not orden:
        out.update(decision="sin_clasificacion", manda=None, usados=[],
                   motivo="no classifier covers >= %.0f %% of the InstanSeg nuclei"
                   % (100 * p["cobertura_min"]))
        out["rotulos"].append(ROTULOS["sin_clasificacion"])
        return out
    jefe = orden[0]
    if jefe != principal[0] and comunes.get(principal[0]) is not None:
        out["rotulos"].append("%s does not pass coverage: %s governs" % (principal[0], jefe))
    acuerdo_todos = True
    for k in orden[1:]:
        a, b = np.asarray(comunes[jefe]), np.asarray(comunes[k])
        ambos = (a >= 0) & (b >= 0)
        kp = kappa(a[ambos], b[ambos])
        difs = {}
        for f in sorted(set(fragmentos[fragmentos >= 0].tolist())):
            m = ambos & (fragmentos == f)
            if m.sum() < p["min_nucleos_fragmento"]:
                continue
            difs["F%d" % f] = {
                "neo_pts": 100.0 * abs(float((a[m] == NEO).mean()) - float((b[m] == NEO).mean())),
                "inf_pts": 100.0 * abs(float((a[m] == INF).mean()) - float((b[m] == INF).mean())),
                "n": int(m.sum())}
        max_neo = max([d["neo_pts"] for d in difs.values()], default=None)
        max_inf = max([d["inf_pts"] for d in difs.values()], default=None)
        ok = (kp is not None and kp >= p["kappa_min"] and difs and
              max_neo <= p["dif_neo_max_pts"] and max_inf <= p["dif_inf_max_pts"])
        out["pares"]["%s|%s" % (jefe, k)] = {
            "n_ambos": int(ambos.sum()), "kappa": kp, "por_fragmento": difs,
            "max_neo_pts": max_neo, "max_inf_pts": max_inf, "acuerdo": bool(ok),
            "motivo": None if ok else ("no fragment with >= %d nuclei matched by both" % p[
                "min_nucleos_fragmento"] if not difs else "kappa or per-fragment difference "
                                                           "outside the criterion")}
        acuerdo_todos &= bool(ok)
    if len(orden) == 1:
        out.update(decision=jefe, manda=jefe, usados=[jefe])
        out["rotulos"].append(ROTULOS["solo_uno"])
    elif acuerdo_todos:
        out.update(decision=jefe, manda=jefe, usados=[jefe], comparadores=orden[1:])
    else:
        out.update(decision="rango", manda=None, usados=orden)
        out["rotulos"].append(ROTULOS["rango"])
    return out


def clasifica_wsi(lector, nombre, clave, dir_trabajo, log=print, modelo=None):
    """HistoPLUS o NuLite por LazySlide sobre L0 (zona escaneada como `zona`, sin find_tissues),
    con los pesos verificados. MPS lote 4 → MPS lote 1 → CPU (y se dice cuál sirvió). Devuelve
    centroides (px L0), nombre de clase y probabilidad por núcleo DEL MODELO."""
    np = _np()
    import shutil

    import lazyslide as zs
    from wsidata import open_wsi
    from wsidata.io import add_tissues

    import laminillas_segmenta as S
    cfg = CLASIFICADORES[clave]
    lam = lector.abre(nombre)
    ruta = S.ruta_lamina(lector, lam, nombre)
    zona = lector.zona_escaneada(lam)
    mpp_l0 = float(lam.mpp_l0)
    os.makedirs(dir_trabajo, mode=0o700, exist_ok=True)
    store = os.path.join(dir_trabajo, "%s_%s.zarr" % (nombre, clave))
    if os.path.exists(store):
        shutil.rmtree(store)
    wsi = open_wsi(ruta, reader="openslide", store=store)
    add_tissues(wsi, "zona", [zona])
    key = "teselas_" + clave
    zs.pp.tile_tissues(wsi, cfg["tesela_px"], mpp=mpp_l0, slide_mpp=mpp_l0,
                       stride_px=cfg["paso_px"], edge=True, background_filter=False,
                       tissue_key="zona", key_added=key)
    spec = wsi.tile_spec(key)
    if modelo is None:
        modelo = CARGA_CLASIFICADOR[clave]()
    m, medido = modelo
    with warnings.catch_warnings(record=True) as avisos:
        warnings.simplefilter("always")
        m.check_input_tile(spec)                # LazySlide 0.12.0 no lo llama: se llama aquí
    intentos, usado = [], None
    for dev, lote in ((dispositivo(), 4), (dispositivo(), 1), ("cpu", 1)):
        try:
            with warnings.catch_warnings(record=True) as av2:
                warnings.simplefilter("always")
                zs.seg.cells(wsi, model=m, tile_key=key, magnification=cfg["magnificacion"],
                             overlap_ownership=True, device=dev, batch_size=lote,
                             num_workers=0, pbar=False, key_added=clave)
            avisos += av2
            usado = (dev, lote)
            break
        except Exception as e:                                     # noqa: BLE001
            intentos.append("%s x%d: %s" % (dev, lote, str(e).strip().splitlines()[-1][:120]
                                            if str(e).strip() else type(e).__name__))
            log("%s: %s falló en %s lote %d; sigo" % (nombre, clave, dev, lote))
    if usado is None:
        raise RuntimeError("%s no corre en ningún modo: %s" % (clave, " | ".join(intentos)))
    if clave in wsi.shapes:
        g = wsi[clave]
        cent = np.column_stack([g.geometry.centroid.x.to_numpy(),
                                g.geometry.centroid.y.to_numpy()]).astype(np.float32)
        cls = g["class"].to_numpy() if "class" in g else np.array(["?"] * len(g))
        nombres = [m.classes[int(c)] if isinstance(c, (int, np.integer)) else str(c)
                   for c in cls]
        prob = (g["prob"].to_numpy() if "prob" in g else np.ones(len(g))).astype(np.float32)
    else:
        cent, nombres, prob = np.zeros((0, 2), np.float32), [], np.zeros(0, np.float32)
    mensajes = sorted({str(a.message)[:160] for a in avisos})[:10]
    _vacia()
    return {"centroide": cent, "clase": np.array(nombres, dtype="<U32"), "prob": prob,
            "n": int(len(cent)), "dispositivo": usado[0], "lote": usado[1],
            "intentos_fallidos": intentos, "pesos": medido, "avisos": mensajes,
            "teselado": {"n": int(len(wsi[key])), "tesela_px": cfg["tesela_px"],
                         "paso_px": cfg["paso_px"], "mpp": float(spec.mpp),
                         "magnificacion": cfg["magnificacion"],
                         "nota": "L0 (%.4f um/px) used as 40x (0.25): no resampling" % mpp_l0}}


def forward_tesela(clave, rgb):
    """Forward de un clasificador sobre UNA tesela (humo / test): carga verificada, segmenta."""
    import torch
    m, medido = CARGA_CLASIFICADOR[clave]()
    dev = dispositivo()
    m.to(dev)
    x = m.get_transform()(torch.from_numpy(rgb).permute(2, 0, 1)).unsqueeze(0).to(dev)
    with torch.inference_mode():
        out = m.segment(x)
    forma = {"instancias": tuple(out.instance_map.shape),
             "probabilidad": tuple(out.probability_map.shape), "clases": list(out.classes)}
    del m, x, out
    _vacia()
    return dict(forma, dispositivo=dev, pesos=medido)


# ── P-HE: tejido, grasa, WSInfer y regiones ───────────────────────────────────────────────────
def rasteriza(geom, x0, y0, f, forma):
    """Máscara (h, w) de un (Multi)Polygon en px L0 sobre una rejilla de origen (x0, y0) L0 y
    `f` px L0 por píxel (centro de píxel dentro)."""
    np = _np()
    from skimage.draw import polygon as dpoly
    m = np.zeros(forma, bool)
    for g in getattr(geom, "geoms", [geom]):
        if g.is_empty:
            continue
        ext = np.asarray(g.exterior.coords)
        rr, cc = dpoly((ext[:, 1] - y0) / f - 0.5, (ext[:, 0] - x0) / f - 0.5, forma)
        m[rr, cc] = True
        for h in g.interiors:
            a = np.asarray(h.coords)
            rr, cc = dpoly((a[:, 1] - y0) / f - 0.5, (a[:, 0] - x0) / f - 0.5, forma)
            m[rr, cc] = False
    return m


def huecos_cerrados(claro, env):
    """Componentes claros ENCERRADOS en la envolvente: el vidrio que la toca por fuera (el borde
    que la suavización mete en la envolvente) no es un hueco. (etiquetas, n) renumeradas."""
    np = _np()
    from scipy import ndimage as ndi
    lab, n = ndi.label(claro)
    if not n:
        return lab, 0
    fuera = np.unique(lab[~env])
    ok = np.ones(n + 1, bool)
    ok[fuera] = False
    ok[0] = False
    nuevo = np.zeros(n + 1, np.int64)
    nuevo[ok] = np.arange(1, int(ok.sum()) + 1)
    return nuevo[lab], int(ok.sum())


def mascaras_tejido(rgb, i0, mpp, zona_mask, xy_px=None, p=PARAMS):
    """Tejido, envolvente y grasa de la H&E a `mpp` (≈2 µm/px).
    tejido = ODsum suavizada (σ 4 µm) > umbral, dentro de la zona; envolvente = tejido cerrado
    (24 µm) con los huecos rellenos; grasa = huecos claros de la envolvente ≥ 1.000 µm², sin
    núcleo de InstanSeg dentro y sin anillo epitelial (< 3 núcleos por 100 µm de borde en un
    anillo de 8 µm): una luz con su epitelio no es grasa. `xy_px`: centroides en px de la rejilla."""
    np = _np()
    from scipy import ndimage as ndi
    from skimage.morphology import disk

    import laminillas_color as CO
    od = CO.od(rgb, i0)
    s = od.sum(axis=-1)
    zona_mask = np.asarray(zona_mask, bool)
    tejido = (ndi.gaussian_filter(s, p["tejido_sigma_um"] / mpp) > p["tejido_odsum"]) & zona_mask
    # la envolvente se cierra también sobre lo FINO (membranas de adipocito, sin suavizar): un
    # campo de grasa en el borde del cilindro queda dentro aunque no lo rodee tejido denso
    fino_s = ndi.gaussian_filter(s, p["hueco_sigma_px"])
    fino = (fino_s > p["tejido_odsum"]) & zona_mask
    r = max(1, int(round(p["envolvente_cierre_um"] / mpp)))
    env = ndi.binary_fill_holes(CO.morfologia(tejido | fino, r, "cierre")) & zona_mask
    claro = fino_s < p["hueco_odsum"]
    lab, n = huecos_cerrados(claro & zona_mask, env)
    area = np.bincount(lab.ravel(), minlength=n + 1) * mpp ** 2
    ok = area >= p["grasa_area_min_um2"]
    ok[0] = False
    info = {"huecos": int(n), "con_nucleo": 0, "con_anillo": 0}
    if xy_px is not None and len(xy_px) and n:
        xy = np.asarray(xy_px, float)
        xi = np.floor(xy[:, 0]).astype(int)
        yi = np.floor(xy[:, 1]).astype(int)
        v = (xi >= 0) & (yi >= 0) & (xi < lab.shape[1]) & (yi < lab.shape[0])
        dentro = lab[yi[v], xi[v]]
        con = np.unique(dentro[dentro > 0])
        info["con_nucleo"] = int(ok[con].sum())
        ok[con] = False
        ra = max(1, int(round(p["grasa_anillo_um"] / mpp)))
        anillo = np.where(lab == 0, ndi.grey_dilation(lab, footprint=disk(ra)), 0)
        n_anillo = np.bincount(anillo[yi[v], xi[v]], minlength=n + 1)
        n_anillo[0] = 0
        borde_um = np.bincount(anillo.ravel(), minlength=n + 1) * mpp ** 2 / (ra * mpp)
        dens = 100.0 * n_anillo / np.maximum(borde_um, 1e-9)
        epitelial = dens >= p["grasa_anillo_max_100um"]
        epitelial[0] = False
        info["con_anillo"] = int((ok & epitelial).sum())
        ok &= ~epitelial
    grasa = ok[lab]
    info["grasa_grandes"] = int((ok & (area > p["hueco_grande_um2"])).sum())
    return {"tejido": tejido, "envolvente": env, "grasa": grasa, "info": info}


def mpp_entero(mpp_pedido, mpp_l0):
    """El mpp de un múltiplo ENTERO de L0 (el nivel nativo ×f si existe: sin remuestreo ni deriva
    entre rejillas). 2,0 µm/px sobre 0,2506 → ×8 = 2,005; 4,0 → ×16 = 4,010; 0,5 → ×2 = 0,501."""
    f = max(1, int(round(mpp_pedido / mpp_l0)))
    return f * mpp_l0, f


def tejido_he(lector, lam, zona, xy_l0, p=PARAMS):
    """Lee P-HE a ×8 (≈2 µm/px) por el lector único sobre la caja de la zona escaneada y calcula
    `mascaras_tejido`. Devuelve también la rejilla (x0, y0 L0; f px L0 por píxel)."""
    np = _np()
    import laminillas_color as CO
    mpp_l0 = float(lam.mpp_l0)
    mpp, f = mpp_entero(p["tejido_mpp"], mpp_l0)
    minx, miny, maxx, maxy = zona.bounds
    x0, y0 = int(math.floor(minx)), int(math.floor(miny))
    w, h = int(math.ceil((maxx - x0) / f)), int(math.ceil((maxy - y0) / f))
    rgb = lector.lee_region(lam, mpp, x0, y0, w, h)
    i0 = CO.i0_region(lector.i0_local(lam), x0, y0, w, h, mpp, mpp_l0)
    zm = rasteriza(zona, x0, y0, f, (h, w))
    xy_px = (np.asarray(xy_l0, float) - [x0, y0]) / f if len(xy_l0) else None
    out = mascaras_tejido(rgb, i0, mpp, zm, xy_px, p)
    out.update(x0=x0, y0=y0, f=f, mpp=mpp, forma=(h, w))
    return out


def rejilla_ws(zona, mpp_l0, cfg):
    """Rejilla de WSInfer sobre la caja de la zona: `patch_size_pixels` a `spacing_um_px`."""
    px, sp = int(cfg["patch_size_pixels"]), float(cfg["spacing_um_px"])
    if abs(sp - mpp_l0) / sp <= 0.01:          # el lector lee ese mpp en L0 nativo, sin remuestrear
        lado, mpp_lectura = px, float(mpp_l0)
    else:
        lado, mpp_lectura = int(round(px * sp / mpp_l0)), sp
    minx, miny, maxx, maxy = zona.bounds
    x0, y0 = int(math.floor(minx)), int(math.floor(miny))
    return {"x0": x0, "y0": y0, "lado_l0": lado, "nx": int(math.ceil((maxx - x0) / lado)),
            "ny": int(math.ceil((maxy - y0) / lado)), "px": px, "mpp_lectura": mpp_lectura,
            "mpp_entrenamiento": sp, "mpp_l0": float(mpp_l0)}


def fracciones_teselas(mascaras, tj, rej):
    """Fracción de cada máscara (rejilla ×8 de `tj`) en cada tesela de `rej` (ny, nx)."""
    np = _np()
    h, w = tj["forma"]
    xc = tj["x0"] + (np.arange(w) + 0.5) * tj["f"]
    yc = tj["y0"] + (np.arange(h) + 0.5) * tj["f"]
    ix = np.floor((xc - rej["x0"]) / rej["lado_l0"]).astype(np.int64)
    iy = np.floor((yc - rej["y0"]) / rej["lado_l0"]).astype(np.int64)
    vx = (ix >= 0) & (ix < rej["nx"])
    vy = (iy >= 0) & (iy < rej["ny"])
    idx = (iy[vy, None] * rej["nx"] + ix[None, vx]).ravel()
    n = rej["nx"] * rej["ny"]
    tot = np.bincount(idx, minlength=n).astype(float)
    out = {}
    for k, m in mascaras.items():
        c = np.bincount(idx, weights=np.asarray(m, bool)[np.ix_(vy, vx)].ravel(), minlength=n)
        out[k] = (c / np.maximum(tot, 1)).reshape(rej["ny"], rej["nx"])
    return out


def tumor_wsinfer(lector, lam, rej, f_env, p=PARAMS, modelo=None, log=print):
    """p(Tumor) de WSInfer en las teselas con tejido (`f_env` ≥ ws_tejido_min), leídas por el
    lector único a su mpp de entrenamiento. NaN donde no hay tejido. MPS; si falla, CPU."""
    np = _np()
    import torch
    red, cfg, medido = modelo if modelo is not None else modelo_wsinfer()
    i_t = list(cfg["class_names"]).index("Tumor")
    pr = np.full((rej["ny"], rej["nx"]), np.nan, np.float32)
    todas = [(i, j) for i in range(rej["ny"]) for j in range(rej["nx"])
             if f_env[i, j] >= p["ws_tejido_min"]]
    dev = dispositivo()
    try:
        red = red.to(dev)
    except Exception:                                              # noqa: BLE001
        dev = "cpu"
    for k in range(0, len(todas), WSINFER["lote"]):
        lote = todas[k:k + WSINFER["lote"]]
        x = np.stack([lector.lee_region(lam, rej["mpp_lectura"],
                                        rej["x0"] + j * rej["lado_l0"],
                                        rej["y0"] + i * rej["lado_l0"], rej["px"], rej["px"])
                      for i, j in lote])
        t = transforma_wsinfer(x, cfg)
        try:
            with torch.inference_mode():
                y = torch.softmax(red(t.to(dev)), dim=1)[:, i_t].float().cpu().numpy()
        except Exception as e:                                     # noqa: BLE001
            if dev == "cpu":
                raise
            log("WSInfer falló en %s (%s): sigo en CPU" % (dev, type(e).__name__))
            dev, red = "cpu", red.to("cpu")
            with torch.inference_mode():
                y = torch.softmax(red(t), dim=1)[:, i_t].float().numpy()
        for (i, j), v in zip(lote, y):
            pr[i, j] = v
    del red
    _vacia()
    return {"p": pr, "dispositivo": dev, "pesos": medido, "n_teselas": len(todas),
            "clases": cfg["class_names"], "tesela": {"px": rej["px"], "mpp": rej["mpp_lectura"]}}


def conteos_teselas(xy_l0, codigos_nuc, rej):
    """(len(COMUN), ny, nx): núcleos de InstanSeg con clase, por clase y tesela; y (ny, nx) de los
    núcleos sin clase."""
    np = _np()
    xy = np.asarray(xy_l0, float).reshape(-1, 2)
    ix = np.floor((xy[:, 0] - rej["x0"]) / rej["lado_l0"]).astype(int)
    iy = np.floor((xy[:, 1] - rej["y0"]) / rej["lado_l0"]).astype(int)
    v = (ix >= 0) & (iy >= 0) & (ix < rej["nx"]) & (iy < rej["ny"])
    n = rej["nx"] * rej["ny"]
    out = np.zeros((len(COMUN), rej["ny"], rej["nx"]), np.int32)
    c = np.asarray(codigos_nuc)
    for k in range(len(COMUN)):
        m = v & (c == k)
        out[k] = np.bincount(iy[m] * rej["nx"] + ix[m], minlength=n).reshape(rej["ny"], rej["nx"])
    m = v & (c < 0)
    sin = np.bincount(iy[m] * rej["nx"] + ix[m], minlength=n).reshape(rej["ny"], rej["nx"])
    return out, sin


REGION = {0: "no tissue", 1: "tumour (classifier and WSInfer)", 2: "tumour (WSInfer only)",
          3: "tumour (classifier only)", 4: "stroma", 5: "fat", 6: "other tissue"}


def regiones(rej, fr, p_ws, conteos, frag_tesela, p=PARAMS, neo_min=None, con_ws=True,
             con_clases=True):
    """Región por tesela (REGION) y estroma intratumoral. `fr`: fracciones por tesela
    (envolvente, tejido, grasa); `conteos`: de `conteos_teselas`."""
    np = _np()
    from scipy import ndimage as ndi
    from skimage.morphology import convex_hull_image
    neo_min = p["neo_densidad_min_mm2"] if neo_min is None else neo_min
    area_mm2 = (rej["lado_l0"] * rej["mpp_l0"]) ** 2 / 1e6
    hay = fr["envolvente"] >= p["ws_tejido_min"]
    grasa = hay & (fr["grasa"] >= p["grasa_frac"] * np.maximum(fr["envolvente"], 1e-9))
    tej_mm2 = fr["tejido"] * area_mm2
    n_neo = conteos[NEO]
    n_cla = conteos.sum(axis=0)
    t_cl = (con_clases & (n_neo >= p["neo_min_n"]) &
            (n_neo / np.maximum(tej_mm2, 1e-12) >= neo_min))
    t_ws = (np.nan_to_num(p_ws, nan=0.0) >= p["ws_corte"]) if con_ws else np.zeros_like(hay)
    base = hay & ~grasa
    reg = np.zeros(hay.shape, np.int8)
    if con_ws and con_clases:
        reg[base & t_cl & t_ws] = 1
        reg[base & t_ws & ~t_cl] = 2
        reg[base & t_cl & ~t_ws] = 3
    elif con_clases:
        reg[base & t_cl] = 1
    else:
        reg[base & t_ws] = 1
    resto = base & (reg == 0)
    if con_clases:
        est = ((conteos[CON] + conteos[INF]) >= p["estroma_frac"] * n_cla) | \
              (n_cla < p["estroma_min_clasificados"])
        reg[resto & est] = 4
        reg[resto & ~est] = 6
    else:
        reg[resto] = 4
    reg[grasa] = 5
    tum = reg == 1
    r = int(p["estroma_dist_teselas"])
    cerca = ndi.binary_dilation(tum, structure=np.ones((3, 3), bool), iterations=r) if r else tum
    casco = np.zeros_like(tum)
    for f in sorted(set(np.asarray(frag_tesela)[tum].tolist())):
        m = tum & (frag_tesela == f)
        if f >= 0 and m.sum() >= p["tumor_min_teselas_envolvente"]:
            try:
                h = convex_hull_image(m)
            except Exception:                                      # noqa: BLE001 (colineales)
                h = m
            casco |= h & (frag_tesela == f)
    intra = (reg == 4) & cerca & casco
    return reg, intra


def resumen_regiones(reg, intra, fr, conteos, rej, con_clases=True):
    np = _np()
    area_mm2 = (rej["lado_l0"] * rej["mpp_l0"]) ** 2 / 1e6
    env_mm2 = fr["envolvente"] * area_mm2
    total = float(env_mm2[reg > 0].sum())
    out = {"tejido_mm2": total, "alcance": ROTULOS["alcance"], "regiones": {}}
    for k, nombre in REGION.items():
        if k == 0:
            continue
        a = float(env_mm2[reg == k].sum())
        out["regiones"][nombre] = {"mm2": a, "pct_tejido": 100.0 * a / total if total else None,
                                   "teselas": int((reg == k).sum())}
    tum = reg == 1
    n_cla = conteos.sum(axis=0)
    out["fraccion_neoplasica_en_tumor"] = (
        float(conteos[NEO][tum].sum() / max(n_cla[tum].sum(), 1)) if con_clases and tum.any()
        else None)
    if con_clases and intra.any():
        tej = float((fr["tejido"] * area_mm2)[intra].sum())
        n_inf = int(conteos[INF][intra].sum())
        out["infiltrado"] = {"densidad_mm2": n_inf / tej if tej else None,
                             "fraccion_inflamatorios": n_inf / max(int(n_cla[intra].sum()), 1),
                             "area_estroma_intratumoral_mm2": tej, "n_inflamatorios": n_inf,
                             "teselas": int(intra.sum())}
    else:
        out["infiltrado"] = None
    return out


def acuerdo_fuentes(reg):
    """Acuerdo tumoral WSInfer ↔ clasificador (Jaccard sobre teselas)."""
    t_cl = (reg == 1) | (reg == 3)
    t_ws = (reg == 1) | (reg == 2)
    u = int((t_cl | t_ws).sum())
    return {"jaccard": (int((t_cl & t_ws).sum()) / u) if u else None,
            "teselas_ambos": int((reg == 1).sum()), "solo_wsinfer": int((reg == 2).sum()),
            "solo_clasificador": int((reg == 3).sum())}


def _valor(vals, rango):
    """Una cifra que depende del clasificador: la del que manda, o [mín, máx] si va en rango."""
    limpios = {k: v for k, v in vals.items() if v is not None}
    if not limpios:
        return {"valor": None, "por_clasificador": vals}
    if not rango:
        return {"valor": next(iter(limpios.values())), "por_clasificador": vals}
    return {"min": min(limpios.values()), "max": max(limpios.values()), "por_clasificador": vals,
            "rotulo": ROTULOS["rango"]}


# ── CellViT++: enganche (nube) ────────────────────────────────────────────────────────────────
def lee_cellvitpp(base, dims_l0, mpp_l0):
    """Salidas de CellViT++ en SESION/nube/cellvitpp/, validadas contra `CELLVITPP`. Devuelve
    ({clave: {centroide, clase (ya en la taxonomía común), prob, mapa, metodos, …}}, {clave: motivo
    de rechazo}). Sin carpeta: ({}, {}). El motivo es texto NUESTRO: nunca cita un valor del
    fichero (podría ser el nombre crudo de una clase)."""
    carpeta = _abs(base, CELLVITPP["salida"]["carpeta"])
    buenos, malos = {}, {}
    if not os.path.isdir(carpeta):
        return buenos, malos
    for fich in sorted(os.listdir(carpeta)):
        if not (fich.startswith(PRIMARIO + ".") and fich.endswith(".json")):
            continue
        clasif = fich[len(PRIMARIO) + 1:-len(".json")]
        clave = "cellvitpp_" + clasif
        try:
            if not _RE_CLASIF.match(clasif) or frases_prohibidas(clasif):
                raise CellvitppInvalido("nombre de clasificador no opaco")
            with open(os.path.join(carpeta, fich), encoding="utf-8") as f:
                meta = json.load(f)
            ruta_csv = os.path.join(carpeta, "%s.%s.csv" % (PRIMARIO, clasif))
            r = valida_cellvitpp(meta, ruta_csv, clasif, dims_l0, mpp_l0)
            if r["fuera_de_tabla"]:
                raise CellvitppInvalido("%d clase(s) del modelo fuera de CELLVITPP_CLASES[%s]: no se "
                                        "pueden nombrar en Métodos" % (r["fuera_de_tabla"], clasif))
            buenos[clave] = r
        except CellvitppInvalido as e:
            malos[clave] = "CellvitppInvalido: %s" % e
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            malos[clave] = "%s: unreadable file or value" % type(e).__name__
    return buenos, malos


def metodos_cellvitpp(clasif, mapa):
    """Cómo nombra Métodos las clases del modelo: «<clasif> class <i>: <descripción> -> <común>»,
    por índice. Una clase fuera de `CELLVITPP_CLASES` sale sin nombre («class ?») y se cuenta.
    Devuelve (lista, n fuera de tabla)."""
    tabla = CELLVITPP_CLASES.get(clasif, {})
    dentro = sorted((tabla[k][0], tabla[k][1], v) for k, v in mapa.items() if k in tabla)
    fuera = sorted(v for k, v in mapa.items() if k not in tabla)
    lineas = ["%s class %d: %s -> %s" % (clasif, i, d, v) for i, d, v in dentro]
    lineas += ["%s class ?: not in the sealed class table -> %s" % (clasif, v) for v in fuera]
    return lineas, len(fuera)


def valida_cellvitpp(meta, ruta_csv, clasif, dims_l0, mpp_l0):
    """Valida el par del enganche y TRADUCE sus clases a la taxonomía común: lo que devuelve ya no
    lleva el nombre crudo de ninguna clase del modelo (`clase` en COMUN, `mapa` la identidad de
    COMUN, `metodos` por índice y descripción saneada). Los mensajes de error no citan valores del
    fichero."""
    np = _np()
    import csv
    req = CELLVITPP["salida"]["json_campos"]
    faltan = [k for k in req if k not in meta]
    if faltan:
        raise CellvitppInvalido("faltan campos: %s" % ", ".join(faltan))
    if meta["lamina"] != PRIMARIO or meta["clasificador"] != clasif:
        raise CellvitppInvalido("lámina o clasificador no casan con el fichero")
    if str(meta["version"]) != CELLVITPP_VERSION:
        raise CellvitppInvalido("versión ≠ %s" % CELLVITPP_VERSION)
    if meta["modelo"] != CELLVITPP_MODELO:
        raise CellvitppInvalido("modelo ≠ %s" % CELLVITPP_MODELO)
    m = meta["mpp_l0"]
    if isinstance(m, bool) or not isinstance(m, (int, float)) or not math.isfinite(m) or \
            abs(float(m) - mpp_l0) / mpp_l0 > 0.01:
        raise CellvitppInvalido("mpp_l0 no es el del manifiesto (%.4f)" % mpp_l0)
    mapa = meta["mapa_clases"]
    if not isinstance(mapa, dict) or not mapa or any(not isinstance(k, str) or v not in COMUN
                                                     for k, v in mapa.items()):
        raise CellvitppInvalido("mapa_clases vacío o con destinos fuera de %s" % (COMUN,))
    if C.sha256_fichero(ruta_csv) != meta["sha256_csv"]:
        raise CellvitppInvalido("sha256 del CSV no casa")
    with open(ruta_csv, encoding="utf-8", newline="") as f:
        filas = list(csv.reader(f))
    if not filas or filas[0] != CELLVITPP["salida"]["csv_columnas"]:
        raise CellvitppInvalido("cabecera ≠ %s" % CELLVITPP["salida"]["csv_columnas"])
    filas = filas[1:]
    n = meta["n"]
    if isinstance(n, bool) or not isinstance(n, int) or n != len(filas):
        raise CellvitppInvalido("n no casa con las %d filas del CSV" % len(filas))
    if any(len(r) != 4 for r in filas):
        raise CellvitppInvalido("fila sin 4 campos")
    xy = np.array([[float(a), float(b)] for a, b, _, _ in filas], np.float64).reshape(-1, 2)
    prob = np.array([float(r[3]) for r in filas], np.float64)
    W, H = dims_l0
    if len(xy) and (not np.isfinite(xy).all() or xy.min() < 0 or (xy[:, 0] >= W).any() or
                    (xy[:, 1] >= H).any()):
        raise CellvitppInvalido("coordenadas fuera de la lámina")
    if len(prob) and (not np.isfinite(prob).all() or prob.min() < 0 or prob.max() > 1):
        raise CellvitppInvalido("prob fuera de [0, 1]")
    fuera = set(r[2] for r in filas) - set(mapa)
    if fuera:
        raise CellvitppInvalido("%d clase(s) del CSV sin mapa" % len(fuera))
    comun = np.array([mapa[r[2]] for r in filas], dtype="<U16")       # aquí muere el nombre crudo
    metodos, n_fuera = metodos_cellvitpp(clasif, mapa)
    return {"centroide": xy, "clase": comun, "prob": prob, "mapa": {c: c for c in COMUN},
            "clases_comunes": sorted(set(mapa.values())), "metodos": metodos, "fuera_de_tabla": n_fuera,
            "meta": {"modelo": CELLVITPP_MODELO, "version": CELLVITPP_VERSION, "clasificador": clasif,
                     "n": n}}


# ── orden `primario` ──────────────────────────────────────────────────────────────────────────
def _clasificacion(base, lector, clave, fn, log):
    """Resultado de un clasificador (de disco si su «hecho» vale; si no, lo corre). Si falla,
    {"disponible": False, "motivo"} y la siguiente corrida lo reintenta."""
    rel_npz, rel_json = _rel("clases_%s_%s.npz" % (clave, PRIMARIO)), \
        _rel("clases_%s_%s.json" % (clave, PRIMARIO))
    if C.esta_hecho(base, "he-clases", PRIMARIO, clave):
        d = _carga_npz(base, rel_npz)
        d.update(_lee(base, rel_json), disponible=True)
        return d
    try:
        res = fn(lector, PRIMARIO, os.path.join(base, DIR_HE, "trabajo"), log)
    except Exception as e:                                         # noqa: BLE001
        log("%s: %s no disponible (%s: %s)" % (PRIMARIO, clave, type(e).__name__, e))
        return {"disponible": False, "motivo": "%s: %s" % (type(e).__name__, str(e)[:300])}
    np = _np()
    _npz(base, rel_npz, centroide=np.asarray(res["centroide"], np.float32),
         clase=np.asarray(res["clase"], dtype="<U32"),
         prob=np.asarray(res["prob"], np.float32))
    meta = {k: v for k, v in res.items() if k not in ("centroide", "clase", "prob")}
    _escribe(base, rel_json, meta)
    C.marca_hecho(base, "he-clases", PRIMARIO, clave, productos=[rel_npz, rel_json])
    d = dict(res, disponible=True)
    return d


def _clasificador_defecto(clave):
    def fn(lector, nombre, dir_trabajo, log):
        return clasifica_wsi(lector, nombre, clave, dir_trabajo, log)
    return fn


def primario(base, lector=None, clasificadores=None, tumor=None, log=print):
    """La H&E del primario. `clasificadores` {clave: fn(lector, nombre, dir_trabajo, log)} y
    `tumor` fn(lector, lam, rej, f_env, log) se inyectan en los tests; por defecto, HistoPLUS y
    NuLite por LazySlide y el TorchScript de WSInfer, todos con pesos verificados."""
    np = _np()
    import laminillas_proc as P
    import laminillas_segmenta as S
    import laminillas_sello as SL
    lector = _lector(lector)
    sello = SL.exige(P.ruta_sello(base), [PRIMARIO])
    try:
        nuc = P.nucleos_lamina(base, PRIMARIO)
    except P.FaltaEntrada:
        raise FaltaEntrada("P-HE sin núcleos de InstanSeg: corre antes `procesa laminillas -- "
                           "piloto-ibis P-HE`")
    cent = np.asarray(nuc["centroide"], float).reshape(-1, 2)
    lam = lector.abre(PRIMARIO)
    mpp_l0 = float(lam.mpp_l0)
    dims = tuple(lam.dimensiones_l0)
    zona = lector.zona_escaneada(lam)
    prov = S.mascara_provisional(cent, mpp_l0, dims)
    frag = S.fragmento_de(prov, cent)
    # 1. clasificadores
    fns = clasificadores or {k: _clasificador_defecto(k) for k in PRINCIPAL}
    res = {k: _clasificacion(base, lector, k, fn, log) for k, fn in fns.items()}
    comunes, meta_cl = {}, {}
    for k, r in res.items():
        if not r.get("disponible"):
            comunes[k] = None
            meta_cl[k] = {"disponible": False, "motivo": r.get("motivo")}
            continue
        comunes[k] = clases_en_instanseg(cent, r, k, mpp_l0)
        meta_cl[k] = {kk: r.get(kk) for kk in ("n", "dispositivo", "lote", "teselado", "pesos",
                                                "avisos", "intentos_fallidos")}
        meta_cl[k]["disponible"] = True
    cvpp, cvpp_malos = lee_cellvitpp(base, dims, mpp_l0)
    for k, r in cvpp.items():
        comunes[k] = clases_en_instanseg(cent, r, k, mpp_l0, mapa=r["mapa"])
        # solo la taxonomía común y, para Métodos, índice + descripción saneada: nunca el nombre crudo
        meta_cl[k] = dict(r["meta"], disponible=True, clases_comunes=r["clases_comunes"],
                          metodos=r["metodos"])
    # artefacto: las marcas de GrandQC por núcleo de la segmentación sellada (si corrió); esos
    # núcleos quedan fuera de la puerta, los recuentos y las fracciones (en la H&E rige GrandQC)
    gq = nuc.get("grandqc")
    artef = (np.asarray(gq, bool).reshape(-1) if gq is not None and len(gq) == len(cent)
             else np.zeros(len(cent), bool))
    ok = ~artef
    puerta = mini_puerta({k: (c[ok] if c is not None else None) for k, c in comunes.items()},
                         frag[ok])
    usados = puerta["usados"]
    rango = puerta["decision"] == "rango"
    con_clases = bool(usados)
    # 2. tejido y grasa (×8), rejilla de WSInfer
    tj = tejido_he(lector, lam, zona, cent)
    cfg_ws = None
    rel_ws = _rel("wsinfer_%s.npz" % PRIMARIO)
    try:
        if tumor is None:
            red, cfg_ws, medido_ws = modelo_wsinfer()
        else:
            red, medido_ws = None, None
            cfg_ws = {"patch_size_pixels": 350, "spacing_um_px": 0.25}
        rej = rejilla_ws(zona, mpp_l0, cfg_ws)
    except Exception as e:                                         # noqa: BLE001
        red = None
        rej = rejilla_ws(zona, mpp_l0, {"patch_size_pixels": 350, "spacing_um_px": 0.25})
        ws_error = "%s: %s" % (type(e).__name__, str(e)[:300])
    else:
        ws_error = None
    fr = fracciones_teselas({"envolvente": tj["envolvente"], "tejido": tj["tejido"],
                             "grasa": tj["grasa"]}, tj, rej)
    ws = None
    if ws_error is None and C.esta_hecho(base, "he-wsinfer", PRIMARIO):
        d = _carga_npz(base, rel_ws)
        if d["p"].shape == (rej["ny"], rej["nx"]):
            ws = {"p": d["p"], **_lee(base, _rel("wsinfer_%s.json" % PRIMARIO))}
    if ws is None and ws_error is None:
        try:
            if tumor is not None:
                ws = tumor(lector, lam, rej, fr["envolvente"], log)
            else:
                ws = tumor_wsinfer(lector, lam, rej, fr["envolvente"],
                                   modelo=(red, cfg_ws, medido_ws), log=log)
            _npz(base, rel_ws, p=np.asarray(ws["p"], np.float32))
            _escribe(base, _rel("wsinfer_%s.json" % PRIMARIO),
                     {k: v for k, v in ws.items() if k != "p"})
            C.marca_hecho(base, "he-wsinfer", PRIMARIO,
                          productos=[rel_ws, _rel("wsinfer_%s.json" % PRIMARIO)])
        except Exception as e:                                     # noqa: BLE001
            ws_error = "%s: %s" % (type(e).__name__, str(e)[:300])
            log("WSInfer no disponible: %s" % ws_error)
            ws = None
    con_ws = ws is not None
    p_ws = ws["p"] if con_ws else np.full((rej["ny"], rej["nx"]), np.nan, np.float32)
    # 3. regiones por clasificador usado (o sin clases)
    gx, gy = np.meshgrid(rej["x0"] + (np.arange(rej["nx"]) + 0.5) * rej["lado_l0"],
                         rej["y0"] + (np.arange(rej["ny"]) + 0.5) * rej["lado_l0"])
    frag_t = S.fragmento_de(prov, np.column_stack([gx.ravel(), gy.ravel()])).reshape(gx.shape)
    por = {}
    arrays = {"p_ws": p_ws, "f_envolvente": fr["envolvente"], "f_tejido": fr["tejido"],
              "f_grasa": fr["grasa"], "fragmento": frag_t,
              "rejilla": np.array([rej["x0"], rej["y0"], rej["lado_l0"], rej["nx"], rej["ny"]])}
    claves_reg = usados if con_clases else [None]
    fr_ok = frag[ok]
    for k in claves_reg:
        cods = (comunes[k] if k else np.full(len(cent), -1, np.int8))[ok]
        cnt, sin = conteos_teselas(cent[ok], cods, rej)
        reg, intra = regiones(rej, fr, p_ws, cnt, frag_t, con_ws=con_ws, con_clases=bool(k))
        rs = resumen_regiones(reg, intra, fr, cnt, rej, con_clases=bool(k))
        sens = {}
        for fac in PARAMS["neo_factores"]:
            r2, _ = regiones(rej, fr, p_ws, cnt, frag_t, neo_min=PARAMS["neo_densidad_min_mm2"] *
                             fac, con_ws=con_ws, con_clases=bool(k))
            a = resumen_regiones(r2, _, fr, cnt, rej, con_clases=bool(k))
            sens["x%g" % fac] = a["regiones"][REGION[1]]["mm2"]
        rs["sensibilidad_umbral_densidad_mm2_tumor"] = sens
        rs["acuerdo_wsinfer_clasificador"] = acuerdo_fuentes(reg) if con_ws and k else None
        neo, n = _fraccion(cods, NEO)
        rs["fraccion_neoplasica_global"] = neo if k else None
        rs["fraccion_neoplasica_por_fragmento"] = {
            "F%d" % f: _fraccion(cods[fr_ok == f], NEO)[0]
            for f in sorted(set(fr_ok[fr_ok >= 0].tolist()))} if k else None
        nombre = k or "sin_clases"
        por[nombre] = rs
        arrays["region_%s" % nombre] = reg
        arrays["intratumoral_%s" % nombre] = intra
        arrays["conteos_%s" % nombre] = cnt
    for k, c in comunes.items():
        if c is not None:
            arrays["clase_instanseg_%s" % k] = c
    # 4. salida
    def cifra(f):
        return _valor({k: f(por[k]) for k in por}, rango)
    reg_nombres = [REGION[i] for i in range(1, 7)]
    out = {
        "tipo": "he-primario", "lamina": PRIMARIO, "sello": sello.sha256 if sello else None,
        "alcance": ROTULOS["alcance"], "invasivo_in_situ": ROTULOS["invasivo"],
        "necrosis": ROTULOS["necrosis"],
        "nucleos": {"segmentador": "InstanSeg, sealed configuration (laminillas segmenta)",
                    "n": int(len(cent)), "mpp_l0": mpp_l0,
                    "excluidos_artefacto_grandqc": int(artef.sum()),
                    "grandqc": ("GrandQC marks from the sealed segmentation: those nuclei are "
                                "outside the gate, counts and fractions" if gq is not None else
                                "GrandQC did not run in the sealed segmentation of P-HE: no "
                                "artefact exclusion"),
                    "fragmentos": {"F%d" % f: int((frag == f).sum())
                                   for f in sorted(set(frag[frag >= 0].tolist()))}},
        "clasificadores": meta_cl, "puerta": puerta, "manda": puerta["decision"],
        "cellvitpp": {"enganche": CELLVITPP, "presentes": sorted(cvpp),
                      "rechazados": cvpp_malos,
                      "estado": ("used as comparator" if cvpp else ROTULOS["cellvitpp_no"])},
        "wsinfer": ({"disponible": True, **{k: v for k, v in ws.items() if k != "p"}}
                    if con_ws else {"disponible": False, "motivo": ws_error,
                                    "rotulo": ROTULOS["sin_wsinfer"]}),
        "tejido": {"mpp": tj["mpp"], "huecos": tj["info"], "grasa": ROTULOS["grasa"]},
        "regiones": {n: {"mm2": cifra(lambda r, n=n: r["regiones"][n]["mm2"]),
                         "pct_tejido": cifra(lambda r, n=n: r["regiones"][n]["pct_tejido"])}
                     for n in reg_nombres},
        "tejido_mm2": next(iter(por.values()))["tejido_mm2"],
        "fraccion_neoplasica": {
            "rotulo": ROTULOS["fraccion"],
            "global": cifra(lambda r: r["fraccion_neoplasica_global"]),
            "en_region_tumoral": cifra(lambda r: r["fraccion_neoplasica_en_tumor"])},
        "infiltrado": {"rotulo_es": ROTULOS["infiltrado_es"], "rotulo_en": ROTULOS["infiltrado_en"],
                       "no_es": ROTULOS["infiltrado_no"]},
        "por_clasificador": por, "licencias": LICENCIAS, "parametros": PARAMS,
        "declaraciones": [ROTULOS["invasivo"], ROTULOS["necrosis"], ROTULOS["grasa"],
                          "nucleus classes from %s transferred to InstanSeg nuclei by mutual "
                          "nearest neighbour within %.0f um" % (", ".join(sorted(comunes)),
                                                                PARAMS["emparejado_um"]),
                          "mitotic figures and red blood cells -> 'other' (not neoplastic)",
                          "thresholds of this module are inferences (PARAMS), not from the plan"],
    }
    if not con_ws:
        out["declaraciones"].append(ROTULOS["sin_wsinfer"])
    if not con_clases:
        out["declaraciones"].append(ROTULOS["solo_wsinfer"])
        out["infiltrado"]["estado"] = ROTULOS["infiltrado_caido"]
    else:
        inf = {k: (por[k]["infiltrado"] or {}) for k in por}
        out["infiltrado"]["densidad_mm2"] = _valor(
            {k: v.get("densidad_mm2") for k, v in inf.items()}, rango)
        out["infiltrado"]["fraccion_inflamatorios"] = _valor(
            {k: v.get("fraccion_inflamatorios") for k, v in inf.items()}, rango)
        out["infiltrado"]["area_estroma_intratumoral_mm2"] = _valor(
            {k: v.get("area_estroma_intratumoral_mm2") for k, v in inf.items()}, rango)
    _exige_limpio(out)
    rel_reg = _npz(base, _rel("regiones_%s.npz" % PRIMARIO), **arrays)
    rel = _escribe(base, _rel("%s.json" % PRIMARIO), out)
    C.marca_hecho(base, "he-primario", PRIMARIO, productos=[rel, rel_reg])
    log("%s: manda %s · tumoral %s mm² · WSInfer %s" % (
        PRIMARIO, puerta["decision"], out["regiones"][REGION[1]]["mm2"],
        "sí" if con_ws else "no"))
    return out


# ── hueso ─────────────────────────────────────────────────────────────────────────────────────
def _matriz_he():
    import laminillas_color as CO
    return CO.matriz(RUIFROK_H, RUIFROK_E)


def rasgos_hueso(lector, lam, zona, p=HUESO_P):
    """Rasgos por píxel a `p["mpp"]` (4 µm/px) leídos por franjas con margen: ODsum suavizada y
    casi cruda, fracción de hematoxilina H/(H+E) (Ruifrok H&E) y textura (desviación local de la
    ODsum). float32; la caja es la de la zona escaneada."""
    np = _np()
    from scipy import ndimage as ndi

    import laminillas_color as CO
    mpp_l0 = float(lam.mpp_l0)
    mpp, f = mpp_entero(p["mpp"], mpp_l0)
    minx, miny, maxx, maxy = zona.bounds
    x0, y0 = int(math.floor(minx)), int(math.floor(miny))
    W, H = int(math.ceil((maxx - x0) / f)), int(math.ceil((maxy - y0) / f))
    M = _matriz_he()
    i0f = lector.i0_local(lam)
    s_suave = np.zeros((H, W), np.float32)
    s_cruda = np.zeros((H, W), np.float32)
    hfrac = np.zeros((H, W), np.float32)
    textura = np.zeros((H, W), np.float32)
    sig, sig_c, sig_t = (p["sigma_um"] / mpp, p["color_sigma_um"] / mpp,
                         p["textura_sigma_um"] / mpp)
    for r0 in range(0, H, p["franja_px"]):
        r1 = min(H, r0 + p["franja_px"])
        a0, a1 = max(0, r0 - p["margen_px"]), min(H, r1 + p["margen_px"])
        ya = y0 + a0 * f
        rgb = lector.lee_region(lam, mpp, x0, ya, W, a1 - a0)
        i0 = CO.i0_region(i0f, x0, ya, W, a1 - a0, mpp, mpp_l0)
        od = CO.od(rgb, i0)
        s = od.sum(axis=-1)
        c = np.clip(CO.desmezcla(od, M), 0, None)
        hs = ndi.gaussian_filter(c[..., 0], sig_c)
        es = ndi.gaussian_filter(c[..., 1], sig_c)
        m1 = ndi.gaussian_filter(s, sig_t)
        m2 = ndi.gaussian_filter(s * s, sig_t)
        k0, k1 = r0 - a0, r1 - a0
        s_suave[r0:r1] = ndi.gaussian_filter(s, sig)[k0:k1]
        s_cruda[r0:r1] = s[k0:k1]
        hfrac[r0:r1] = (hs / np.maximum(hs + es, 1e-6))[k0:k1]
        textura[r0:r1] = np.sqrt(np.maximum(m2 - m1 * m1, 0))[k0:k1]
    zm = rasteriza(zona, x0, y0, f, (H, W))
    return {"s_suave": s_suave, "s_cruda": s_cruda, "hfrac": hfrac, "textura": textura,
            "zona": zm, "mpp": mpp, "x0": x0, "y0": y0, "f": f, "mpp_l0": mpp_l0}


CLASES_HUESO = {1: "trabecular bone", 2: "medullary space (non-adipocyte)", 3: "adipocytes",
                4: "empty, unclassified (outside the adipocyte size range)"}


def clasifica_hueso(rs, p=HUESO_P, factores=None):
    """Etiqueta por píxel (uint8): 0 fuera/vidrio, 1 trabécula, 2 espacio medular (no adipocito),
    3 adipocitos, 4 vacío sin clasificar. `factores` multiplica umbrales (sensibilidad).
      · tejido = ODsum suavizada (σ 8 µm) > umbral; envolvente = tejido (y lo fino: septos, ODsum
        cruda) cerrado (40 µm), huecos rellenos; huecos = píxeles claros (ODsum cruda < umbral)
        de la envolvente;
      · adipocito = hueco de 1.000-40.000 µm² más sus septos (≤ 8 µm de él); otro hueco → 4;
      · trabécula = eosinófila (H/(H+E) ≤ umbral) Y lisa (textura ≤ umbral), apertura de 1 px y
        dilatación geodésica dentro de lo eosinófilo (radio 2σ de la textura: recupera el borde
        que la ventana de textura pierde junto a la médula granular); componentes < 2.000 µm²,
        fuera;
      · el resto de la envolvente, espacio medular."""
    np = _np()
    from scipy import ndimage as ndi

    import laminillas_color as CO
    fa = dict(tejido_odsum=1.0, hueco_odsum=1.0, hfrac_trabecula=1.0, textura_trabecula=1.0)
    fa.update(factores or {})
    mpp = rs["mpp"]
    zona = rs["zona"]
    tejido = (rs["s_suave"] > p["tejido_odsum"] * fa["tejido_odsum"]) & zona
    fino = (rs["s_cruda"] > p["tejido_odsum"] * fa["tejido_odsum"]) & zona   # septos de adipocito
    r = max(1, int(round(p["envolvente_cierre_um"] / mpp)))
    env = ndi.binary_fill_holes(CO.morfologia(tejido | fino, r, "cierre")) & zona
    lab, n = huecos_cerrados((rs["s_cruda"] < p["hueco_odsum"] * fa["hueco_odsum"]) & zona, env)
    hueco = lab > 0
    area = np.bincount(lab.ravel(), minlength=n + 1) * mpp ** 2
    amin, amax = p["adipocito_area_um2"]
    adip_ok = (area >= amin) & (area <= amax)
    adip_ok[0] = False
    otros_ok = ~adip_ok
    otros_ok[0] = False
    adip = adip_ok[lab]
    otros = otros_ok[lab]
    eos = tejido & ~hueco & (rs["hfrac"] <= p["hfrac_trabecula"] * fa["hfrac_trabecula"])
    liso = eos & (rs["textura"] <= p["textura_trabecula"] * fa["textura_trabecula"])
    nucleo = CO.morfologia(liso, 1, "apertura")
    rr = max(1, int(math.ceil(2 * p["textura_sigma_um"] / mpp)))
    trab = ndi.binary_dilation(nucleo, iterations=rr, mask=eos) if nucleo.any() else nucleo
    trab = CO.quita_pequenos(trab, max(1, int(p["trabecula_min_um2"] / mpp ** 2)))
    et = np.zeros(zona.shape, np.uint8)
    et[env] = 2
    rs_ = max(1, int(round(p["septo_um"] / mpp)))
    if adip.any():
        et[ndi.binary_dilation(adip, iterations=rs_) & env & ~hueco & ~trab] = 3
    et[trab] = 1
    et[adip] = 3
    et[otros] = 4
    return et, env


def composicion(et, mpp):
    np = _np()
    px = mpp ** 2 / 1e6
    areas = {k: float((et == k).sum() * px) for k in CLASES_HUESO}
    den = areas[1] + areas[2] + areas[3]
    pct = {CLASES_HUESO[k]: (100.0 * areas[k] / den if den else None) for k in (1, 2, 3)}
    return {"mm2": {CLASES_HUESO[k]: v for k, v in areas.items()}, "pct": pct,
            "denominador_mm2": den, "denominador": "trabecular bone + medullary space + "
                                                   "adipocytes"}


def qc_hueso(lector, lam, rs, env, p=HUESO_P):
    """QC del hueso: fragmentos, contacto con el borde escaneado, foco (cociente de energía del
    laplaciano L0 vs L0/2, teselas al azar con semilla fija; solo distribución dentro de la
    lámina, sin umbral absoluto)."""
    np = _np()
    from scipy import ndimage as ndi

    import laminillas_segmenta as S
    mpp = rs["mpp"]
    lab, n = ndi.label(env)
    area = np.bincount(lab.ravel(), minlength=n + 1) * mpp ** 2 / 1e6
    ok = area >= p["fragmento_min_mm2"]
    ok[0] = False
    b = max(1, int(math.ceil(p["borde_um"] / mpp)))
    banda = rs["zona"] & ~ndi.binary_erosion(rs["zona"], iterations=b, border_value=0)
    tocan = np.unique(lab[banda & env])
    tocan = [int(t) for t in tocan if t > 0 and ok[t]]
    ids = np.flatnonzero(ok)
    out = {"tejido_mm2": float(env.sum() * mpp ** 2 / 1e6),
           "fragmentos": int(len(ids)), "fragmentos_que_tocan_el_borde": len(tocan),
           "fraccion_area_en_fragmentos_que_tocan": (float(area[tocan].sum() / area[ids].sum())
                                                     if len(ids) else None),
           "seccion": ROTULOS["seccion"], "alcance": ROTULOS["alcance"]}
    yy, xx = np.nonzero(env)
    rng = np.random.default_rng(p["semilla"])
    k = min(p["foco_teselas"], len(yy))
    focos = []
    if k:
        sel = rng.choice(len(yy), size=k, replace=False)
        lado = p["foco_lado_px"]
        for i in sel:
            cx = rs["x0"] + (xx[i] + 0.5) * rs["f"]
            cy = rs["y0"] + (yy[i] + 0.5) * rs["f"]
            rgb = lector.lee_region(lam, rs["mpp_l0"], int(cx - lado / 2), int(cy - lado / 2),
                                    lado, lado)
            v = S.foco(np.asarray(rgb, float).mean(axis=-1))
            if math.isfinite(v):
                focos.append(v)
    f = np.asarray(focos)
    out["foco"] = {"metodo": "energy-of-Laplacian ratio, L0 vs L0 at half resolution; within-slide "
                             "distribution only, no absolute threshold",
                   "n_teselas": int(len(f)),
                   "p10": float(np.percentile(f, 10)) if len(f) else None,
                   "mediana": float(np.median(f)) if len(f) else None,
                   "p90": float(np.percentile(f, 90)) if len(f) else None}
    return out


def hueso(base, nombre, lector=None, log=print):
    """QC y composición de tejido del hueso (sin células). B-HE-2: «no atribuible»."""
    np = _np()
    if nombre not in HUESO:
        raise ValueError("hueso: solo %s (no %s)" % (", ".join(HUESO), nombre))
    lector = _lector(lector)
    lam = lector.abre(nombre)
    zona = lector.zona_escaneada(lam)
    rs = rasgos_hueso(lector, lam, zona)
    et, env = clasifica_hueso(rs)
    base_c = composicion(et, rs["mpp"])
    sens = {}
    for umbral in ("tejido_odsum", "hueco_odsum", "hfrac_trabecula", "textura_trabecula"):
        for fac in HUESO_P["factores"]:
            e2, _ = clasifica_hueso(rs, factores={umbral: fac})
            sens["%s x%g" % (umbral, fac)] = composicion(e2, rs["mpp"])["pct"]
    rango = {}
    for clase in base_c["pct"]:
        vals = [v[clase] for v in sens.values() if v.get(clase) is not None]
        if base_c["pct"][clase] is not None:
            vals.append(base_c["pct"][clase])
        rango[clase] = {"min": min(vals), "max": max(vals)} if vals else None
    vac = base_c["mm2"][CLASES_HUESO[4]]
    den = base_c["denominador_mm2"]
    adip = base_c["mm2"][CLASES_HUESO[3]]
    qc = qc_hueso(lector, lam, rs, env)
    atribucion = None
    try:
        atribucion = (lector.manifiesto().get("laminas", {}).get(nombre) or {}).get(
            "nivel_atribucion")
    except Exception:                                              # noqa: BLE001
        pass
    out = {
        "tipo": "he-hueso", "lamina": nombre, "alcance": ROTULOS["alcance"],
        "rotulo": ROTULOS["hueso"] if nombre == "B-HE-1" else ROTULOS["hueso_no_atribuible"],
        "atribucion": ("no atribuible" if nombre == "B-HE-2" else
                       "see manifest (nivel_atribucion)"),
        "atribucion_manifiesto": atribucion,
        "alcance_analisis": ROTULOS["hueso_sin_celulas"],
        "composicion": {"mpp": rs["mpp"], "metodo": "colour (Ruifrok H&E unmixing on local I0) "
                        "and texture (local SD of OD sum) at 4 um/px; fixed thresholds "
                        "(HUESO_P, inference)", "pct": base_c["pct"], "mm2": base_c["mm2"],
                        "denominador": base_c["denominador"], "rango_sensibilidad": rango,
                        "sensibilidad": sens,
                        "adipocitos_con_vacios_grandes_pct": (100.0 * (adip + vac) / (den + vac)
                                                              if den + vac else None),
                        "nota_vacios": "adipocytes separated by septa thinner than ~4 um can "
                                       "merge into one empty component above the size range: "
                                       "counted as 'empty, unclassified'; the upper bound "
                                       "counting them as adipocytes is given"},
        "qc": qc, "licencias": {"entregable": LICENCIAS["entregable"]},
        "parametros": HUESO_P,
    }
    _exige_limpio(out)
    rel = _escribe(base, _rel("hueso_%s.json" % nombre), out)
    rel_npz = _npz(base, _rel("hueso_%s.npz" % nombre), etiqueta=et,
                   rejilla=np.array([rs["x0"], rs["y0"], rs["f"], rs["mpp"]]))
    C.marca_hecho(base, "he-hueso", nombre, productos=[rel, rel_npz])
    # Sin cifras de resultado en el log (2-oct): el log sale de la zona clínica; viven en SESION.
    log("%s: composición escrita (%d clases; cifras solo en %s)" % (
        nombre, sum(1 for v in base_c["pct"].values() if v is not None), rel))
    return out


# ── exploratorio ──────────────────────────────────────────────────────────────────────────────
def _registro_he(base):
    """(matrices {fc: P-HE µm → P-CK19 µm} de los FC que pasan (iii), json de (iii))."""
    np = _np()
    import laminillas_proc as P
    rel = os.path.join(P.DIR_PILOTO, "registro", "%s.json" % PRIMARIO)
    if not (os.path.isfile(_abs(base, rel)) and
            C.esta_hecho(base, "registro-par", PRIMARIO, P.REFERENCIA)):
        raise FaltaEntrada("falta (iii): corre antes `procesa laminillas -- registro-par P-CK19 "
                           "P-HE`")
    d = _lee(base, rel)
    pasan = set(d.get("fcs_pasan") or [])
    mats = {}
    for f in (d.get("ck19_he") or {}).get("fragmentos", []):
        if f.get("id") in pasan and f.get("matriz_um") is not None:
            mats[f["id"]] = np.asarray(f["matriz_um"], float)
    return mats, d


def _aplica(M, xy):
    np = _np()
    xy = np.asarray(xy, float).reshape(-1, 2)
    return (np.c_[xy, np.ones(len(xy))] @ np.asarray(M).T)[:, :2]


def a_ck19(xy_he_um, mats, fcs):
    """Lleva puntos de P-HE (µm) a P-CK19 (µm) con la matriz del FC verificado en el que caen.
    Devuelve (xy_ck19, fc) con fc None si no cae en ninguno."""
    np = _np()
    import laminillas_proc as P
    xy = np.asarray(xy_he_um, float).reshape(-1, 2)
    out = np.full_like(xy, np.nan)
    ids = np.array([None] * len(xy), dtype=object)
    for fid, M in mats.items():
        q = _aplica(M, xy)
        dentro = (P.fc_de(fcs, q) == fid) & np.array([i is None for i in ids], bool)
        out[dentro] = q[dentro]
        ids[dentro] = fid
    return out, ids


def etiquetas_ihq(base, xy_ck19, fc_ids, p=EXPLORA_P, marcadores=MARCADORES_EXPLORA):
    """Positividad registrada de cada marcador en el punto: % de la región (fc, i, j) de lado L de
    `piloto/metricas/<L>.json` (rejilla anclada a P-CK19), si la región tiene ≥ region_min_n
    núcleos. Mismo tratamiento para RE, Ki67 y NE."""
    np = _np()
    import laminillas_proc as P
    out, meta = {}, {}
    for m in marcadores:
        rel = os.path.join(P.DIR_PILOTO, "metricas", "%s.json" % m)
        y = np.full(len(xy_ck19), np.nan)
        if not os.path.isfile(_abs(base, rel)):
            meta[m] = {"disponible": False, "motivo": "no metrics yet (laminillas metricas %s)" % m}
            out[m] = y
            continue
        d = _lee(base, rel)
        L = d.get("L_um")
        if d.get("estado") != "registrado" or not L:
            meta[m] = {"disponible": False, "motivo": "metrics not on the registered path "
                                                      "(estado %r)" % d.get("estado")}
            out[m] = y
            continue
        regs = d.get("regiones") or {}
        for i, (q, f) in enumerate(zip(xy_ck19, fc_ids)):
            if f is None or not np.isfinite(q).all():
                continue
            r = regs.get("%s:%d,%d" % (f, math.floor(q[0] / L), math.floor(q[1] / L)))
            if r and r.get("pct") is not None and int(r.get("n") or 0) >= p["region_min_n"]:
                y[i] = float(r["pct"])
        meta[m] = {"disponible": True, "L_um": L, "T": d.get("T"),
                   "denominador": d.get("denominador"), "n_etiquetadas": int(np.isfinite(y).sum()),
                   "ne": m in MARCADORES_NE}
        out[m] = y
    return out, meta


def _spearman(a, b):
    np = _np()
    from scipy.stats import spearmanr
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    return float(spearmanr(a, b).statistic)


def _ridge(A, y, alfa):
    np = _np()
    return np.linalg.solve(A.T @ A + alfa * np.eye(A.shape[1]), A.T @ y)


def analiza(X, y, grupos, trivial, p=EXPLORA_P):
    """Embeddings → positividad, validación DEJANDO FUERA UN FRAGMENTO ENTERO: estandarización,
    PCA (≤ pca_max) y ridge ajustados solo con el entrenamiento del pliegue. Comparadores
    triviales: la media del entrenamiento y un ridge de 1 variable sobre `trivial` (densidad
    nuclear). Señal = IC95 de ρ (bootstrap de teselas, optimista con n=1: declarado) > 0 Y MAE
    menor que los dos triviales. Si no, «no signal», dicho."""
    np = _np()
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    g = np.asarray(grupos)
    t = np.asarray(trivial, float)
    ok = np.isfinite(y) & np.isfinite(t) & np.isfinite(X).all(axis=1)
    X, y, g, t = X[ok], y[ok], g[ok], t[ok]
    frags = sorted(set(g.tolist()))
    base = {"n_teselas": int(len(y)), "n_fragmentos": len(frags)}
    if len(y) < p["min_teselas"] or len(frags) < p["min_fragmentos"]:
        return dict(base, estado=ROTULOS["no_estimable"],
                    motivo="fewer than %d labelled tiles or %d fragments" % (
                        p["min_teselas"], p["min_fragmentos"]))
    iqr = float(np.percentile(y, 75) - np.percentile(y, 25))
    base["iqr_pts"] = iqr
    if iqr < p["iqr_min_pts"]:
        return dict(base, estado=ROTULOS["no_estimable"],
                    motivo="near-uniform positivity (IQR %.1f < %.1f points)" % (
                        iqr, p["iqr_min_pts"]))
    pe, pm, pt = (np.full(len(y), np.nan) for _ in range(3))
    pliegues = []
    for f in frags:
        te, tr = g == f, g != f
        if tr.sum() < 3:
            continue
        mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-8
        Z = (X - mu) / sd
        k = int(min(p["pca_max"], tr.sum() - 1, X.shape[1]))
        _, _, Vt = np.linalg.svd(Z[tr], full_matrices=False)
        V = Vt[:k].T
        ym = y[tr].mean()
        w = _ridge(Z[tr] @ V, y[tr] - ym, p["ridge_alfa"])
        pe[te] = Z[te] @ V @ w + ym
        pm[te] = ym
        tm, ts = t[tr].mean(), t[tr].std() + 1e-8
        a = ((t[tr] - tm) / ts)[:, None]
        wt = _ridge(a, y[tr] - ym, p["ridge_alfa"])
        pt[te] = ((t[te] - tm) / ts)[:, None] @ wt + ym
        pliegues.append({"fuera": str(f), "n_prueba": int(te.sum()),
                         "fragmentos_entrenamiento": sorted(str(x) for x in set(g[tr].tolist()))})
    v = np.isfinite(pe)
    mae = {"embedding": float(np.mean(np.abs(pe[v] - y[v]))),
           "trivial_media": float(np.mean(np.abs(pm[v] - y[v]))),
           "trivial_densidad": float(np.mean(np.abs(pt[v] - y[v])))}
    rho = _spearman(pe[v], y[v])
    rng = np.random.default_rng(p["semilla"])
    boots = []
    idx = np.flatnonzero(v)
    for _ in range(p["n_boot"]):
        s = rng.choice(idx, size=len(idx), replace=True)
        r = _spearman(pe[s], y[s])
        if math.isfinite(r):
            boots.append(r)
    ic = ([float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]
          if boots else [None, None])
    senal = (ic[0] is not None and ic[0] > 0 and
             mae["embedding"] < min(mae["trivial_media"], mae["trivial_densidad"]))
    return dict(base, estado="signal" if senal else "no signal", rho_oof=rho, rho_ic95=ic,
                mae=mae, pliegues=pliegues,
                nota="CI by tile bootstrap: optimistic with a single block (n=1)",
                rotulo=None if senal else ROTULOS["sin_senal"])


def teselas_explora(base, lector, lam, mats, fcs, p=EXPLORA_P):
    """Teselas de 256 µm (512 px a 0,5 µm/px) de P-HE, tumorales (≥ 50 % de las teselas de región
    con tejido son «tumour (classifier and WSInfer)»), cuyo centro cae en un FC verificado de (iii)."""
    np = _np()
    he = _carga_npz(base, _rel("regiones_%s.npz" % PRIMARIO))
    d = _lee(base, _rel("%s.json" % PRIMARIO))
    clave = d["puerta"].get("manda") or (d["puerta"].get("usados") or [None])[0] or "sin_clases"
    reg = he["region_%s" % clave]
    x0r, y0r, lr, nxr, nyr = he["rejilla"].tolist()
    mpp_l0 = float(lam.mpp_l0)
    _, fx = mpp_entero(p["tesela_mpp"], mpp_l0)
    lado_l0 = p["tesela_px"] * fx
    zona = _lector(lector).zona_escaneada(lam)
    minx, miny, maxx, maxy = zona.bounds
    nx, ny = int(math.ceil((maxx - minx) / lado_l0)), int(math.ceil((maxy - miny) / lado_l0))
    gx, gy = np.meshgrid(x0r + (np.arange(int(nxr)) + 0.5) * lr,
                         y0r + (np.arange(int(nyr)) + 0.5) * lr)
    ix = np.floor((gx - minx) / lado_l0).astype(int)
    iy = np.floor((gy - miny) / lado_l0).astype(int)
    tum = np.zeros((ny, nx))
    tej = np.zeros((ny, nx))
    v = (ix >= 0) & (iy >= 0) & (ix < nx) & (iy < ny)
    np.add.at(tum, (iy[v], ix[v]), (reg[v] == 1))
    np.add.at(tej, (iy[v], ix[v]), (reg[v] > 0) & (reg[v] != 5))
    cand = np.argwhere((tej > 0) & (tum >= p["frac_tumor_min"] * np.maximum(tej, 1)))
    xy_l0 = np.array([[minx + (j + 0.5) * lado_l0, miny + (i + 0.5) * lado_l0]
                      for i, j in cand]).reshape(-1, 2)
    q, ids = a_ck19(xy_l0 * mpp_l0, mats, fcs)
    keep = np.array([i is not None for i in ids], bool)
    return {"ij": cand[keep], "centro_l0": xy_l0[keep], "ck19_um": q[keep], "fc": ids[keep],
            "lado_l0": lado_l0, "origen_l0": (minx, miny)}


def _vecinos(ij, fc, r):
    np = _np()
    out = []
    for k, (i, j) in enumerate(ij):
        m = (np.abs(ij[:, 0] - i) <= r) & (np.abs(ij[:, 1] - j) <= r) & (fc == fc[k])
        out.append(np.flatnonzero(m))
    return out


def _lee_teselas(lector, lam, centros_l0, p=EXPLORA_P):
    np = _np()
    mpp, fx = mpp_entero(p["tesela_mpp"], float(lam.mpp_l0))
    lado_l0 = p["tesela_px"] * fx
    return np.stack([lector.lee_region(lam, mpp, int(round(cx - lado_l0 / 2)),
                                       int(round(cy - lado_l0 / 2)), p["tesela_px"],
                                       p["tesela_px"]) for cx, cy in centros_l0])


def _embebe_vision(m, teselas, sub, lote=8):
    """Media de los embeddings de los 2×2 recortes de `sub` px del centro de cada tesela."""
    np = _np()
    import torch
    dev = dispositivo()
    m.to(dev)
    tf = m.get_transform()
    T = teselas.shape[1]
    o = (T - 2 * sub) // 2
    recortes = [(o + a * sub, o + b * sub) for a in (0, 1) for b in (0, 1)]
    out = []
    for k in range(0, len(teselas), lote):
        acum = None
        for (r, c) in recortes:
            x = torch.stack([tf(torch.from_numpy(np.ascontiguousarray(t[r:r + sub, c:c + sub]))
                                .permute(2, 0, 1)) for t in teselas[k:k + lote]]).to(dev)
            with torch.inference_mode():
                y = m.encode_image(x).float().cpu().numpy()
            acum = y if acum is None else acum + y
        out.append(acum / len(recortes))
    return np.concatenate(out), dev


def embebe_uni2h(teselas, coords, vecinos, patch_lv0, log=print):
    from lazyslide_models.vision.uni import UNI2
    ruta, medido = peso("uni2h", "pytorch_model.bin")
    m = UNI2(model_path=ruta)
    e, dev = _embebe_vision(m, teselas, EXPLORA_P["sub_px"])
    del m
    _vacia()
    return e, {"pesos": medido, "dispositivo": dev, "entrada": "mean of 2x2 224 px crops at 0.5 "
                                                              "um/px"}


def embebe_hoptimus1(teselas, coords, vecinos, patch_lv0, log=print):
    refs_main_sellado("hoptimus1")
    _, medido = peso("hoptimus1", "model.safetensors")
    from lazyslide_models.vision.h_optimus import HOptimus1
    m = HOptimus1()
    e, dev = _embebe_vision(m, teselas, EXPLORA_P["sub_px"])
    del m
    _vacia()
    return e, {"pesos": medido, "dispositivo": dev, "entrada": "mean of 2x2 224 px crops at 0.5 "
                                                              "um/px"}


def embebe_titan(teselas, coords, vecinos, patch_lv0, log=print):
    """CONCH v1.5 por tesela (512 px a 0,5 µm/px = 20x) y el codificador de TITAN sobre la
    vecindad 3×3 de cada tesela (misma FC): embedding de región, no de lámina."""
    np = _np()
    import torch
    from PIL import Image
    refs_main_sellado("titan")
    med = [peso("titan", f)[1] for f in FUNDACIONALES["titan"]["ficheros"]]
    from lazyslide_models.multimodal.titan import Titan
    m = Titan()
    dev = dispositivo()
    m.model.to(dev)
    m.conch.to(dev)
    feats = []
    for k in range(0, len(teselas), 8):
        x = torch.stack([m.conch_transform(Image.fromarray(t)) for t in teselas[k:k + 8]]).to(dev)
        with torch.inference_mode():
            f = m.conch(x)
            f = f[0] if isinstance(f, (tuple, list)) else f
        feats.append(f.float().cpu())
    feats = torch.cat(feats)
    out = []
    c = torch.as_tensor(np.asarray(coords), dtype=torch.long)
    for vs in vecinos:
        vs = torch.as_tensor(vs, dtype=torch.long)
        with torch.inference_mode():
            e = m.model.encode_slide_from_patch_features(feats[vs][None].to(dev),
                                                         c[vs][None].to(dev), int(patch_lv0))
        out.append(e.float().cpu().numpy().reshape(-1))
    del m, feats
    _vacia()
    return np.stack(out), {"pesos": med, "dispositivo": dev,
                           "entrada": "CONCH v1.5 per 512 px tile at 0.5 um/px; TITAN over the "
                                      "3x3 neighbourhood (region embedding)"}


EXTRACTORES = {"uni2h": embebe_uni2h, "hoptimus1": embebe_hoptimus1, "titan": embebe_titan}


def explora(base, lector=None, extractores=None, log=print):
    """El exploratorio, fuera de las cifras. Ver la cabecera."""
    np = _np()
    import laminillas_proc as P
    import laminillas_sello as SL
    lector = _lector(lector)
    sello = SL.exige(P.ruta_sello(base), [PRIMARIO])
    if not C.esta_hecho(base, "he-primario", PRIMARIO):
        raise FaltaEntrada("falta la H&E del primario: corre antes `procesa laminillas_he -- "
                           "primario`")
    mats, d3 = _registro_he(base)
    out = {"tipo": "he-explora", "rotulo": ROTULOS["explora"], "uso_declarado": USO_DECLARADO,
           "sello": sello.sha256 if sello else None, "licencias": LICENCIAS,
           "parametros": EXPLORA_P, "marcadores": list(MARCADORES_EXPLORA),
           "marcadores_ne": list(MARCADORES_NE)}
    if not mats:
        out.update(estado="caido", motivo=ROTULOS["explora_caido"])
        _exige_limpio(out)
        _escribe(base, _rel("explora.json"), out)
        log(ROTULOS["explora_caido"])
        return out
    fcs = P.carga_fcs(base)
    lam = lector.abre(PRIMARIO)
    ts = teselas_explora(base, lector, lam, mats, fcs)
    n = len(ts["centro_l0"])
    ys, meta_y = etiquetas_ihq(base, ts["ck19_um"], ts["fc"])
    nuc = P.nucleos_lamina(base, PRIMARIO)
    cent = np.asarray(nuc["centroide"], float)
    lado = ts["lado_l0"]
    dens = np.zeros(n)
    for k, (cx, cy) in enumerate(ts["centro_l0"]):
        dens[k] = np.sum((np.abs(cent[:, 0] - cx) < lado / 2) & (np.abs(cent[:, 1] - cy) <
                                                                  lado / 2))
    dens = dens / ((lado * float(lam.mpp_l0)) ** 2 / 1e6)
    out.update(n_teselas=n, fragmentos=sorted(set(str(f) for f in ts["fc"])),
               etiquetas=meta_y, comparadores_triviales=[
                   "training-fold mean", "1-variable ridge on nuclear count per mm2 of tile"])
    if n == 0:
        out.update(estado=ROTULOS["no_estimable"], motivo="no tumour tile inside a registered "
                                                          "P-HE fragment")
        _exige_limpio(out)
        _escribe(base, _rel("explora.json"), out)
        return out
    teselas = _lee_teselas(lector, lam, ts["centro_l0"])
    coords = np.round(ts["centro_l0"] - lado / 2).astype(np.int64)
    vec = _vecinos(ts["ij"], ts["fc"], EXPLORA_P["titan_vecindad"])
    patch_lv0 = int(round(lado))
    res, emb_meta = {}, {}
    for clave, fn in (extractores or EXTRACTORES).items():
        rel_e = _rel("embeddings_%s.npz" % clave)
        huella = hashlib.sha256(np.ascontiguousarray(coords).tobytes()).hexdigest()[:16]
        E = None
        if C.esta_hecho(base, "he-embeddings", clave, huella):
            E = _carga_npz(base, rel_e)["e"]
            emb_meta[clave] = _lee(base, _rel("embeddings_%s.json" % clave))
        else:
            try:
                E, meta = fn(teselas, coords, vec, patch_lv0, log)
            except Exception as e:                                 # noqa: BLE001
                emb_meta[clave] = {"disponible": False, "motivo": "%s: %s" % (
                    type(e).__name__, str(e)[:300])}
                log("%s no disponible: %s" % (clave, emb_meta[clave]["motivo"]))
                continue
            _npz(base, rel_e, e=np.asarray(E, np.float32), coords=coords)
            _escribe(base, _rel("embeddings_%s.json" % clave), meta)
            C.marca_hecho(base, "he-embeddings", clave, huella,
                          productos=[rel_e, _rel("embeddings_%s.json" % clave)])
            emb_meta[clave] = meta
        res[clave] = {m: analiza(E, ys[m], ts["fc"], dens) for m in MARCADORES_EXPLORA}
    conclusion = {}
    for m in MARCADORES_EXPLORA:
        estados = {k: r[m]["estado"] for k, r in res.items()}
        con = [k for k, e in estados.items() if e == "signal"]
        if con:
            conclusion[m] = "signal with: %s (exploratory)" % ", ".join(con)
        elif any(e == "no signal" for e in estados.values()):
            conclusion[m] = ROTULOS["sin_senal"]
        else:
            motivos = [r[m].get("motivo") for r in res.values() if r[m].get("motivo")]
            conclusion[m] = "%s: %s" % (ROTULOS["no_estimable"], meta_y[m].get("motivo") or
                                        (motivos[0] if motivos else "no model ran"))
    out.update(estado="hecho", embeddings=emb_meta, resultados=res, conclusion=conclusion)
    _exige_limpio(out)
    rel = _escribe(base, _rel("explora.json"), out)
    C.marca_hecho(base, "he-explora", PRIMARIO, productos=[rel])
    for m, c in conclusion.items():
        log("%s: %s" % (m, c))
    return out


def cellvitpp(base, log=print):
    """Escribe el formato del enganche de CellViT++ y el estado de lo que haya en SESION/nube/."""
    out = {"tipo": "he-cellvitpp", "enganche": CELLVITPP}
    try:
        man = C.lee_manifiesto(base)
        ent = (man.get("laminas") or {}).get(PRIMARIO) or {}
        mpp = float(ent.get("mpp")) if ent.get("mpp") else None
    except (OSError, ValueError, TypeError):
        mpp = None
    if mpp:
        buenos, malos = lee_cellvitpp(base, (10 ** 9, 10 ** 9), mpp)
        out["presentes"] = {k: v["meta"] for k, v in buenos.items()}
        out["rechazados"] = malos
    else:
        out["presentes"], out["rechazados"] = {}, {}
        out["nota"] = "no manifest mpp for P-HE yet: outputs not validated"
    _escribe(base, _rel("cellvitpp_formato.json"), out)
    log("CellViT++: %s; formato en %s" % (CELLVITPP["estado"], _rel("cellvitpp_formato.json")))
    return out


# ── despacho ──────────────────────────────────────────────────────────────────────────────────
def ejecuta(base, args, lector=None, log=print, **inyectado):
    """La orden (lista cerrada ORDENES). Devuelve el código de salida."""
    import laminillas_proc as P
    import laminillas_sello as SL
    if not args or args[0] not in ORDENES:
        print("orden desconocida; válidas: %s" % ", ".join(ORDENES), file=sys.stderr)
        return RC_USO
    orden, resto = args[0], args[1:]
    try:
        if any(a.startswith("--") for a in resto):
            raise ValueError("banderas no admitidas: %s" % " ".join(resto))
        if orden == "primario":
            if resto not in ([], [PRIMARIO]):
                raise ValueError("primario: solo %s" % PRIMARIO)
            primario(base, lector, inyectado.get("clasificadores"), inyectado.get("tumor"), log)
        elif orden == "hueso":
            for n in resto or list(HUESO):
                hueso(base, n, lector, log)
        elif orden == "explora":
            if resto:
                raise ValueError("explora: sin argumentos")
            explora(base, lector, inyectado.get("extractores"), log)
        elif orden == "cellvitpp":
            if resto:
                raise ValueError("cellvitpp: sin argumentos")
            cellvitpp(base, log)
    except (SL.SelloAusente, SL.SelloInvalido) as e:
        print("%s: NO MIDO · %s: %s" % (orden, type(e).__name__, e), file=sys.stderr)
        return RC_SELLO
    except (FaltaEntrada, P.FaltaEntrada) as e:
        print("%s: FALTA · %s" % (orden, e), file=sys.stderr)
        return RC_FALTA
    except (NoPasa, P.NoPasa) as e:
        print("%s: NO PASA · %s" % (orden, e), file=sys.stderr)
        return RC_NO_PASA
    except ValueError as e:
        print("%s: %s" % (orden, e), file=sys.stderr)
        return RC_USO
    return 0


def main(argv):
    base = C.sesion()
    for a in argv:
        C._clave(a)                     # opaco o ValueError (la ventanilla ya lo filtró)
    return ejecuta(base, list(argv))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
