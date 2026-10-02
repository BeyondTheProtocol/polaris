#!/usr/bin/env python3
"""tools/laminillas_congela.py — PRE-CONGELACIÓN y CONGELACIÓN (0) del piloto (plan «laminillas DFCI»,
Puerta del piloto, puntos 2 y 3).

  · Pre-congelación, SOLO en P-HER2NEG y P-HER2 (sin células diana): prueba del lector (a) aquí,
    (b)(c) las corre la ingesta y se leen del manifiesto; I0 local sin patrón por franja; teselado
    e InstanSeg (pesos verificados) con sus comprobaciones; máscara provisional; reglas clásicas de
    artefacto y GrandQC opcional (fuera de TODAS las IHQ si no carga o marca > 20 % en algún
    fragmento de cualquiera de las dos; su galería de 10 + 10 zonas se genera siempre que carga).
    Si algo falla no hay congelación: se corrige y se repite sin haber abierto ninguna lámina
    diana. Con sello ya escrito, la pre-congelación sellada no se pisa (solo `--para-recongelar`,
    que la archiva con el sha256 del sello).
  · (0) Congelación sobre las cinco de tanda (HER2, HER2NEG, KI67, SYN, CK19): a nivel de PÍXEL
    salvo T y su tasa, que usan núcleos de las dos láminas de suelo. Vectores de tanda; residuo
    (i)-(iii) de las 11 IHQ con tejido (nivel de píxel: el DAB propio y su T recalculado en HER2NEG
    quedan EN el sello) y (iv) de las dos de suelo; T de los dos regímenes (GrandQC y reglas
    clásicas) por compartimento, con la rama > 0,25 evaluada en TODOS; tasa de falsos positivos
    fuera de muestra en P-HER2 (IC binomial y por bootstrap de bloques); galería de los 50 objetos
    más altos; regla de la máscara CK19, de L, de registro y de hotspot; umbral de memoria por
    procesador desde la TRAZA de guarda_memoria; semillas; y TODOS los parámetros de regla de
    color, segmentación y congelación. Todo en UN sello (`laminillas_sello`: JSON canónico con
    sha256; la fecha la da quien congela).
  · Rama > 0,25: pendiente sin sello; la revisión (regla escrita: pigmento, pliegue, borde) solo
    se acepta con ese pendiente verificado, y queda registrada AL sellar (un fallo antes no la
    gasta). Una sola vez.
  · Recongelación: solo por error de ejecución (lector, I0, configuración del segmentador o, por la
    actualización 19 del plan, la implementación de la regla de artefacto «saturados no H/DAB»),
    UNA vez, con el sha256 del sello anterior y la causa; si acaba en pendiente, el anterior vuelve
    a regir hasta que la revisión vaya por `recongela`. Reutiliza la pre-congelación sellada si
    solo difiere en parámetros que la pre-congelación no usa (`PARAMS_SOLO_CONGELACION`; el sello
    lo declara); si no, antes `precongela --para-recongelar`.
  · Regla de artefacto «saturados no H/DAB» v2 (`laminillas_color.REGLA_SATURADO`): el recorte en
    intensidad NO es artefacto; `pasada_objetos` lo anota aparte por célula (`frac_recorte_nucleo`,
    `frac_recorte_anillo`). Los vectores de tanda y el residuo siguen sin píxeles recortados.

Guardia: nada que mida células de una lámina que no sea de suelo corre sin el sello verificado,
completo y con los parámetros de este código (`_sello_para_medir`): `pasada_objetos`,
`celulas_diana`, `contratincion_lamina`, `mascara_ck19` y, en `laminillas_segmenta`, `celulas` y
`segmentador_lazyslide`. Lo píxel a píxel de las IHQ que el plan permite antes del sello (vector
DAB, residuo) va por `muestra_pixeles`, que no segmenta nada.

Tras el sello: (iv) de las 9 que no son de suelo (núcleos DAB-negativos: necesita células) va a un
ANEXO sellado (`extension.json`, sha256 propio y el del sello padre); `fuera_del_mapa()` lo lee.

Uso (por la ventanilla, cwd = SESION; todo se escribe en SESION y el sello en la ruta canónica
única de `laminillas_sello`, `SESION/congelacion.json`):
  lector_clinico procesa laminillas_congela -- precongela [--para-recongelar]
  lector_clinico procesa laminillas_congela -- precongela --diagnostico-area [--lamina P-HER2]
                                  (no escribe ni sella; solo números agregados; sale con 4)
  lector_clinico procesa laminillas_congela -- precongela --diagnostico-mpp
                                  (regla física del mpp: diámetro de hematíes en B-HE-1 y B-HE-2;
                                  no escribe ni sella; solo números agregados; sale con 4)
  lector_clinico procesa laminillas_congela -- congela --fecha AAAA-MM-DD --traza <proc>[,<proc>…]
                                  [--revision-alto pigmento:0.25 --vectores-i0-revisados]
  lector_clinico procesa laminillas_congela -- recongela --fecha AAAA-MM-DD
                                  --causa lector|I0|segmentador|regla_artefacto --traza <proc>[,…]
  lector_clinico procesa laminillas_congela -- verifica
`--traza` lleva NOMBRES de procesador; la ventanilla los traduce a `<proc>=trazas/<proc>.tsv`
(relativas a SESION), que es lo que recibe este script.
"""
import hashlib
import json
import math
import os
import re
import sys

import numpy as np

_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _AQUI)
import laminillas_color as C  # noqa: E402
import laminillas_segmenta as S  # noqa: E402
import laminillas_sello as SL  # noqa: E402

SUELO = S.SUELO
TANDA = ("P-HER2", "P-HER2NEG", "P-KI67", "P-SYN", "P-CK19")
DIANA_TANDA = ("P-KI67", "P-SYN", "P-CK19")
# «Las 11 IHQ con tejido» (Hechos medidos): todas menos AE1/AE3. (i)-(iii) se calcula en las 11.
IHQ_CON_TEJIDO = ("P-RE", "P-RP", "P-RA", "P-HER2", "P-HER2NEG", "P-KI67", "P-CK19", "P-SYN",
                  "P-CHGA", "P-{{DIANA3}}", "P-P63")
COMPS = ("nucleo", "anillo")
SinSello = SL.SelloAusente

FICHERO_SELLO = SL.FICHERO              # ruta canónica única: SESION/congelacion.json
PRECONGELACION = "precongelacion.json"
REVISION_ALTO = "revision_alto.json"
PENDIENTE_ALTO = "pendiente_revision_alto.json"
LECTURAS_DIANA = "lecturas_diana.jsonl"
EXTENSION = "extension.json"

METODOS = {
    "fijado": C.ROTULOS["metodos_T"],
    "suelo": "'HER2, NEG' = " + C.ROTULOS["her2neg"],
    "recongelado": ("re-frozen once after a segmentation/reader fix; "
                    "no target-cell measurement informed it"),
    "hscore": C.ROTULOS["hscore"],
    "ck19": C.ROTULOS["ck19_sin_verdad"],
    "hotspot": "not the IKWG method",
}
# Métodos de la recongelación, por causa. La de la regla de artefacto es la frase de la
# actualización 19 del plan (2-oct), literal.
METODOS_RECONGELA = {
    "lector": METODOS["recongelado"], "I0": METODOS["recongelado"],
    "segmentador": METODOS["recongelado"],
    "regla_artefacto": ("re-frozen once after an artefact-rule implementation fix "
                        "(intensity-saturated DAB counted as non-H/DAB artefact); informed by "
                        "P-CK19 denominator aggregates only; no target-cell measurement "
                        "informed it"),
}
CAUSAS_RECONGELA = SL.CAUSAS_RECONGELA       # una sola lista: la que acepta `laminillas_sello`
# Parámetros sellados que la PRE-congelación no usa (solo los lee la congelación (0)): una
# pre-congelación hecha antes de que cambiaran sigue valiendo y el sello lo declara
# (`precongelacion.parametros_distintos`). Lista cerrada y mínima: lo que cambió el arreglo de la
# regla de artefacto (actualización 19). Cualquier otra diferencia obliga a repetirla.
PARAMS_SOLO_CONGELACION = ("color.REGLA_SATURADO", "congela.CAUSAS_RECONGELA")
REGLAS_ALTO = ("pigmento", "pliegue", "borde")
MEMORIA_MARGEN_GB = 2.0
SEMILLA = 20261001

# Lectura para medir (inferencia mía, sellada): objetos a L0 en teselas de 1024 px con margen
# para el anillo y el radio nuclear; muestras de píxeles a 1 µm/px (nivel ×4).
MEDIDA = dict(tesela_l0=1024, radio_max_um=15.0, muestra_h_tope=400_000,
              muestra_h_por_tesela=4000, px_mpp=1.0, px_lado=256, px_teselas=60,
              px_tope=400_000, galeria=50, galeria_grandqc=10)

# (iv) tras el sello: DAB-negativo = DAB medio del núcleo bajo el T de la lámina. Si la
# contratinción de la lámina se desvía hacia el DAB, con los vectores de tanda TODOS los núcleos
# salen sobre T (medido en el sintético) y no queda ninguno «negativo»: entonces se toma el decil
# de menor DAB, que ese desvío sesga por igual a todos los núcleos. Inferencia mía, sellada.
CONTRATINCION = dict(min_negativos=50, respaldo="decil de menor DAB", decil=10.0,
                     minimo_ajuste=20)

# Registro (F3, «Implementación (fijada)»), con los nombres del plan. Los parámetros finos los
# define `laminillas_registro.PARAMS_PARTIDA` y van en `modulo_b.registro`; el módulo B coteja
# estos contra aquellos y, si no casan, el sello no vale para medir.
REGISTRO = dict(
    referencia="P-CK19", nivel="x8 nativo (2.005 um/px, sin remuestreo)",
    imagen="ODsum sobre I0 local, 8 bits, ecualizacion local, relleno pintado",
    transformada="euclidea, escala 1; no rigido despues",
    sift="skimage.feature.SIFT", match=dict(max_ratio=0.8, cross_check=True),
    ransac=dict(modelo="EuclideanTransform", min_samples=2, residual_threshold_px=12,
                residual_threshold_um=25, max_trials=5000),
    quiralidades="volteo horizontal de la movil", min_inliers_fc=30,
    respaldos=["correlacion de fase sobre ODsum", "mapa de densidad nuclear 2 um/px sigma 8 um",
               "VALIS"],
    puerta=dict(tre_um_max=50.0, ventana_b_um=256, paso_b_um=128, nivel_b="x4 (1.00 um/px)",
                ventana_dentro_fc_min=0.80, pico_cociente_min=1.5, desplaz_max_um=128,
                min_picos=15, pequeno_min_picos=5, b_prima=dict(mpp=2.0, sigma_um=8.0),
                particion_a=[0.7, 0.3]),
)
HOTSPOT = dict(diametro_mm=0.5, min_nucleos=500, paso_um=50, interior=True,
               salida=["maximo con correccion por permutacion", "p95 de ventanas",
                       "distribucion completa"], rotulo=METODOS["hotspot"])
REGLA_L = dict(candidatos_um=[100, 150, 200, 300, 400], tre_factor=2.0, mediana_min_nucleos=100,
               region_min_nucleos=50, rejilla_anclada="P-CK19", fc_de="P-KI67",
               valor="tras (ii), solo con densidad nuclear y TRE, sin positividad")

# Declaraciones (internas, en español; NO son texto del informe): lo que Métodos tiene que decir
# y de dónde sale. Se sellan con lo demás.
DECL_FP_ANILLO = ("tasa de falsos positivos en el anillo de P-HER2: en el anillo coincide con la "
                  "propia medida de HER2 (plan: «se declara»)")
DECLARACIONES = [
    DECL_FP_ANILLO,
    "IC95 de la tasa de P-HER2: binomial (Clopper-Pearson) y bootstrap de bloques de 200 µm dentro "
    "de fragmento; rige el de límite superior más alto (`ic95_rige`)",
    "residuo (i)-(iii) calculado a nivel de píxel en las 11 IHQ con tejido dentro de (0); el DAB "
    "propio y su T recalculado en HER2NEG van en el sello",
    "(iv) de las láminas que no son de suelo: tras el sello, en el anexo sellado; si no hay "
    "≥ 50 núcleos bajo T se usa el decil de menor DAB (inferencia sellada)",
    "rama > 0,25 evaluada en todos los regímenes sellados; «high floor» y banda obligatoria por "
    "régimen y por lámina con DAB propio",
    "umbral de memoria = pico de la traza de guarda_memoria (columna sin Metal) + 2 GB",
    S.CRITERIO_TESELAS,
    S.CRITERIO_AREA,
    S.LIMITE_PLIEGUE,
    "máscara provisional: umbral fijo 250/mm² con borde corregido al real y valles entre "
    "fragmentos abiertos (ver MASCARA_PROV)",
]

_RE_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parametros_congela():
    """Todo parámetro de regla de este módulo, tal como se sella en (0)."""
    return {"MEDIDA": dict(MEDIDA), "REGISTRO": REGISTRO, "HOTSPOT": HOTSPOT, "REGLA_L": REGLA_L,
            "CONTRATINCION": dict(CONTRATINCION), "MEMORIA_MARGEN_GB": MEMORIA_MARGEN_GB,
            "IHQ_CON_TEJIDO": list(IHQ_CON_TEJIDO), "TANDA": list(TANDA), "SUELO": list(SUELO),
            "REGLAS_ALTO": list(REGLAS_ALTO), "CAUSAS_RECONGELA": list(CAUSAS_RECONGELA)}


def parametros():
    """Los tres módulos: lo que (0) sella en `parametros` y lo que se coteja tras el sello."""
    return S.canon({"color": C.parametros(), "segmenta": S.parametros(),
                    "congela": parametros_congela()})


# ── JSON verificado (pre-congelación, pendiente, revisión, anexo; el SELLO va por SL) ─────────
def _limpio(o):
    """JSON estricto: numpy → Python, NaN/inf → None, tuplas → listas."""
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
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return f if math.isfinite(f) else None
    return o


def sha256_de(obj):
    t = json.dumps({k: v for k, v in obj.items() if k != "sha256"}, sort_keys=True,
                   separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def _escribe_json(ruta, obj):
    obj = _limpio(obj)
    obj.pop("sha256", None)
    obj["sha256"] = sha256_de(obj)
    os.makedirs(os.path.dirname(os.path.abspath(ruta)), mode=0o700, exist_ok=True)
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True, allow_nan=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, ruta)
    return obj


def _lee_verificado(ruta, tipo):
    with open(ruta, encoding="utf-8") as f:
        obj = json.load(f)
    if obj.get("tipo") != tipo or obj.get("sha256") != sha256_de(obj):
        raise SL.SelloInvalido("%s: sha256 o tipo no casan (¿editado a mano?)"
                               % os.path.basename(ruta))
    return obj


def sha256_fichero(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for trozo in iter(lambda: f.read(1 << 20), b""):
            h.update(trozo)
    return h.hexdigest()


# ── sello ─────────────────────────────────────────────────────────────────────────────────────
def verifica_sello(ruta_sello):
    """`laminillas_sello.Sello` verificado; sin sello, `SelloAusente`; tocado, `SelloInvalido`."""
    if not ruta_sello:
        raise SinSello("no existe el sello de congelación: no mido células de láminas diana")
    return SL.carga(ruta_sello)


def _coteja_parametros(sello):
    """Tras el sello se mide con la regla SELLADA: si un parámetro de color, segmentación o
    congelación del código ya no es el sellado, no se mide (SelloInvalido)."""
    malos = S.diferencias(sello.d.get("parametros"), parametros())
    if malos:
        raise SL.SelloInvalido("los parámetros del código no son los sellados (%s): no mido"
                               % ", ".join(malos[:8]))


def _sello_para_medir(ruta_sello, nombre):
    """Guardia de toda medida de células: suelo sin sello pasa (pre-congelación); diana exige el
    sello verificado, completo y con los parámetros de este código."""
    s = S.exige_sello_celulas(nombre, ruta_sello)
    if s is not None:
        _coteja_parametros(s)
    return s


def registra_lectura_diana(ruta_sello, nombre, que):
    """Constancia de cada lectura de células diana (siempre tras el sello)."""
    import time
    s = verifica_sello(ruta_sello)
    r = os.path.join(os.path.dirname(os.path.abspath(ruta_sello)), LECTURAS_DIANA)
    with open(r, "a", encoding="utf-8") as f:
        f.write(json.dumps({"lamina": nombre, "que": que, "sello": s.sha256,
                            "cuando": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")


def celulas_diana(lector, nombre, ruta_sello, segmentador=None, **kw):
    """Núcleos de una lámina: diana solo con sello (completo y con estos parámetros), y con
    constancia."""
    _sello_para_medir(ruta_sello, nombre)
    out = S.celulas(lector, nombre, ruta_sello, segmentador=segmentador, **kw)
    if nombre not in SUELO:
        registra_lectura_diana(ruta_sello, nombre, "celulas")
    return out


def params_modulo_b():
    """Lo que el módulo B (registro y métricas) propone sellar: `laminillas_metricas.
    contenido_partida()` = {"registro": …, "metricas": …}. Se sella tal cual en `modulo_b`."""
    import laminillas_metricas as MET
    return MET.contenido_partida()


# ── memoria ───────────────────────────────────────────────────────────────────────────────────
def lee_traza_memoria(ruta):
    """Traza de `guarda_memoria.corre(traza=…)`: una línea por muestreo, «segundos RSS huella
    sin-Metal swap-nuevo» separados por tabuladores. Pico = máximo de la columna sin Metal (la
    que vigila el techo)."""
    filas = []
    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            if not linea.strip():
                continue
            partes = linea.rstrip("\n").split("\t")
            if len(partes) != 5:
                raise ValueError("%s no es una traza de guarda_memoria (5 columnas por línea)"
                                 % os.path.basename(ruta))
            filas.append([float(x) for x in partes])
    if not filas:
        raise ValueError("%s: traza de guarda_memoria vacía" % os.path.basename(ruta))
    return {"fichero": os.path.basename(ruta), "sha256": sha256_fichero(ruta),
            "muestras": len(filas), "duracion_s": filas[-1][0],
            "pico_gb": max(f[3] for f in filas)}


def _trazas(trazas_memoria):
    """{procesador: ruta de la traza} → lo que se sella. Un número tecleado no es una traza."""
    if not trazas_memoria:
        raise ValueError("sin traza de memoria de la primera corrida sobre P-HER2NEG: el umbral "
                         "por procesador (pico + 2 GB) se sella aquí, desde la traza de "
                         "guarda_memoria")
    out = {}
    for proc, ruta in trazas_memoria.items():
        if not isinstance(ruta, str) or not ruta:
            raise ValueError("%s: «%r» no es una traza; hace falta el fichero de guarda_memoria "
                             "(un número escrito a mano no vale)" % (proc, ruta))
        out[proc] = lee_traza_memoria(ruta)
    return out


# ── lectura ───────────────────────────────────────────────────────────────────────────────────
def _lee(lector, lamina, mpp, x, y, w, h):
    """lee_region(lamina, mpp, x_l0, y_l0, w, h) → uint8 RGB (h, w, 3); (w, h) a `mpp`."""
    rgb = np.asarray(lector.lee_region(lamina, mpp, int(x), int(y), int(w), int(h)))
    if rgb.shape[:2] != (int(h), int(w)):
        raise ValueError("lee_region devolvió %s, pedí %dx%d" % (rgb.shape, w, h))
    return rgb[..., :3]


def _dims(lamina):
    d = lamina.dimensiones_l0
    return int(d[0]), int(d[1])


def _entrada_manifiesto(lector, nombre):
    try:
        return (lector.manifiesto() or {}).get("laminas", {}).get(nombre, {}) or {}
    except Exception:                                              # noqa: BLE001
        return {}


def prueba_lector_a(lector, lamina, zona):
    """(a) dos lecturas de una tesela: sha256 idéntico."""
    p = zona.representative_point()
    W, H = _dims(lamina)
    x, y = max(0, min(int(p.x), W - 512)), max(0, min(int(p.y), H - 512))
    a = _lee(lector, lamina, float(lamina.mpp_l0), x, y, 512, 512)
    b = _lee(lector, lamina, float(lamina.mpp_l0), x, y, 512, 512)
    ha = hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()
    hb = hashlib.sha256(np.ascontiguousarray(b).tobytes()).hexdigest()
    return {"ok": ha == hb, "sha256": ha}


def prueba_lector_bc(lector, nombre):
    """(b) OpenSlide vs tifffile y (c) pyvips vs OpenSlide: las corre la ingesta y quedan en el
    manifiesto. Tienen que haberse CORRIDO; si fallan, no bloquean: «se declara el lector de
    referencia y VALIS solo aporta la transformación, nunca píxeles para colorimetría»."""
    pl = _entrada_manifiesto(lector, nombre).get("prueba_lector") or {}
    corrida = "b_ok" in pl and "c_ok" in pl
    ok = corrida and bool(pl.get("b_ok")) and bool(pl.get("c_ok"))
    decl = None
    if corrida and not ok:
        decl = ("lector de referencia: OpenSlide; VALIS solo aporta la transformación, nunca "
                "píxeles para colorimetría")
    return {"corrida": corrida, "ok": ok, "declaracion": decl,
            "detalle": {k: pl.get(k) for k in ("b_ok", "b_dif_max", "c_ok", "c_dif_max")}}


def revisa_i0(lector, lamina, nombre, i0):
    """I0 local sin patrón por franja. La ingesta lo mide sobre las medianas crudas por tesela y,
    si lo hay, aplica campo plano y lo declara; aquí, además, se busca franja en el campo que se
    va a usar (raster del lector o, si no lo expone, el campo evaluado a 64 µm/px)."""
    ent = _entrada_manifiesto(lector, nombre).get("i0") or {}
    mapa = None
    if callable(getattr(lector, "i0_raster", None)):
        try:
            mapa = np.asarray(lector.i0_raster(lamina)[0], np.float64)
        except Exception:                                          # noqa: BLE001
            mapa = None
    if mapa is None:
        W, H = _dims(lamina)
        mpp_l0, m = float(lamina.mpp_l0), 64.0
        w, h = max(1, int(W * mpp_l0 / m)), max(1, int(H * mpp_l0 / m))
        mapa = np.asarray(C.i0_region(i0, 0, 0, w, h, m, mpp_l0), np.float64)
        if mapa.ndim < 3:
            mapa = np.broadcast_to(mapa.reshape((1, 1, -1)) if mapa.ndim else mapa, (h, w, 3))
    mio = C.franjas_i0(mapa)
    out = {"campo": mio, "ingesta": {k: ent.get(k) for k in
                                     ("franja", "amplitud_franja_columnas",
                                      "amplitud_franja_filas")}}
    if ent.get("franja"):
        out.update(pasa=True, declaracion="patrón por franja en el vidrio: campo plano aplicado "
                                          "en la ingesta")
    else:
        out.update(pasa=not mio["hay_franjas"], declaracion=None)
    return out


def muestra_pixeles(lector, lamina, zona, i0, semilla, n_teselas=None, lado=None, mpp=None,
                    tope=None):
    """Píxeles (OD) de teselas al azar dentro de la zona escaneada, a 1 µm/px. NIVEL DE PÍXEL:
    no segmenta ni mide células (lo que el plan permite antes del sello en las IHQ diana).
    Devuelve (od N×3, saturado_intensidad N)."""
    import shapely
    n_teselas = n_teselas or MEDIDA["px_teselas"]
    lado = lado or MEDIDA["px_lado"]
    mpp = mpp or MEDIDA["px_mpp"]
    tope = tope or MEDIDA["px_tope"]
    mpp_l0 = float(lamina.mpp_l0)
    lado_l0 = lado * mpp / mpp_l0
    W, H = _dims(lamina)
    rng = np.random.default_rng(semilla)
    minx, miny, maxx, maxy = zona.bounds
    ods, sats = [], []
    intentos = 0
    while len(ods) < n_teselas and intentos < 50 * n_teselas:
        intentos += 1
        x = rng.uniform(minx, max(minx, maxx - lado_l0))
        y = rng.uniform(miny, max(miny, maxy - lado_l0))
        if x + lado_l0 > W or y + lado_l0 > H:
            continue
        if not shapely.contains_xy(zona, x + lado_l0 / 2, y + lado_l0 / 2):
            continue
        rgb = _lee(lector, lamina, mpp, x, y, lado, lado)
        o = C.od(rgb, C.i0_region(i0, x, y, lado, lado, mpp, mpp_l0)).reshape(-1, 3)
        s = C.saturado_intensidad(rgb).reshape(-1)
        util = o.sum(axis=1) > C.ODSUM_MIN_VECTOR
        ods.append(o[util])
        sats.append(s[util])
    if not ods:
        return np.zeros((0, 3)), np.zeros(0, bool)
    o, s = np.concatenate(ods), np.concatenate(sats)
    if len(o) > tope:
        k = rng.choice(len(o), tope, replace=False)
        o, s = o[k], s[k]
    return o, s


def _fraccion_tejido(prov, x0, y0, T):
    f = prov["f"]
    m = prov["mascara"]
    a, b = int(y0 / f), int(math.ceil((y0 + T) / f))
    c, d = int(x0 / f), int(math.ceil((x0 + T) / f))
    sub = m[max(0, a):max(0, b), max(0, c):max(0, d)]
    return float(sub.mean()) if sub.size else 0.0


def pasada_objetos(lector, lamina, poligonos, i0, prov=None, M=None, semilla=0, muestra_h=True,
                   ruta_sello=None, nombre=None):
    """Recorre los núcleos por teselas de L0: OD medio en el núcleo y en el anillo de 3 µm (que no
    pisa otros núcleos), foco por tesela, fracción de tejido de la tesela, muestra de píxeles
    nucleares para H y, si se da M, objetos con píxeles saturados no H/DAB (`saturado`, regla v2:
    color vivo en píxeles NO recortados). Siempre, la fracción de píxeles recortados en
    intensidad del núcleo y del anillo (`frac_recorte_nucleo`, `frac_recorte_anillo`; NaN sin
    píxeles): NO es artefacto, es la anotación «DAB OD clipped» (su DAB es cota inferior). MIDE
    CÉLULAS: una lámina que no sea de suelo solo con el sello (`ruta_sello`) verificado y
    vigente."""
    import shapely
    from skimage.segmentation import expand_labels
    nombre = S.nombre_de(lamina, nombre)
    _sello_para_medir(ruta_sello, nombre)
    mpp = float(lamina.mpp_l0)
    W, H = _dims(lamina)
    T = MEDIDA["tesela_l0"]
    anillo = int(math.ceil(C.ANILLO_UM / mpp))
    margen = anillo + int(math.ceil(MEDIDA["radio_max_um"] / mpp))
    cents = S.centroides(poligonos)
    n = len(cents)
    out = {"od_nucleo": np.full((n, 3), np.nan), "od_anillo": np.full((n, 3), np.nan),
           "px_nucleo": np.zeros(n, np.int64), "px_anillo": np.zeros(n, np.int64),
           "tesela": np.full(n, -1, np.int64), "saturado": np.zeros(n, bool),
           "frac_recorte_nucleo": np.full(n, np.nan), "frac_recorte_anillo": np.full(n, np.nan)}
    focos, fracs, origenes, muestras = [], [], [], []
    if not n:
        out.update(foco_tesela=np.zeros(0), tejido_tesela=np.zeros(0),
                   origen_tesela=np.zeros((0, 2)), muestra_h=np.zeros((0, 3)))
        return out
    rng = np.random.default_rng(semilla)
    arbol = shapely.STRtree(poligonos)
    gx = (cents[:, 0] // T).astype(np.int64)
    gy = (cents[:, 1] // T).astype(np.int64)
    claves, inv = np.unique(np.stack([gx, gy], 1), axis=0, return_inverse=True)
    inv = np.asarray(inv).ravel()
    for k, (ix, iy) in enumerate(claves):
        propios = np.flatnonzero(inv == k)
        cx0, cy0 = int(ix * T), int(iy * T)
        x0, y0 = max(0, cx0 - margen), max(0, cy0 - margen)
        x1, y1 = min(W, cx0 + T + margen), min(H, cy0 + T + margen)
        rgb = _lee(lector, lamina, mpp, x0, y0, x1 - x0, y1 - y0)
        i0r = C.i0_region(i0, x0, y0, x1 - x0, y1 - y0, mpp, mpp)
        o = C.od(rgb, i0r).reshape(-1, 3)
        vec = np.asarray(arbol.query(shapely.box(x0, y0, x1, y1)), np.int64)
        lab = S.rasteriza([poligonos[j] for j in vec], x0, y0, (y1 - y0, x1 - x0),
                          etiquetas=vec + 1)
        anil = expand_labels(lab, anillo)
        anil[lab > 0] = 0
        Lr, Ar = lab.ravel(), anil.ravel()
        rec = C.saturado_intensidad(rgb).ravel().astype(np.float64)    # recorte: se anota
        for campo, etq, cuenta, frac in (("od_nucleo", Lr, "px_nucleo", "frac_recorte_nucleo"),
                                         ("od_anillo", Ar, "px_anillo", "frac_recorte_anillo")):
            cnt = np.bincount(etq, minlength=n + 1)
            sums = np.stack([np.bincount(etq, weights=o[:, c], minlength=n + 1)
                             for c in range(3)], 1)
            nrec = np.bincount(etq, weights=rec, minlength=n + 1)
            c = cnt[propios + 1]
            out[cuenta][propios] = c
            with np.errstate(invalid="ignore", divide="ignore"):
                out[campo][propios] = sums[propios + 1] / c[:, None]
                out[frac][propios] = nrec[propios + 1] / c
        if M is not None:
            sp = C.saturados_no_hdab(rgb, M, i0r).ravel().astype(np.float64)
            sn = np.bincount(Lr, weights=sp, minlength=n + 1)
            sa = np.bincount(Ar, weights=sp, minlength=n + 1)
            out["saturado"][propios] = (sn[propios + 1] + sa[propios + 1]) > 0
        out["tesela"][propios] = k
        nx0, ny0 = cx0 - x0, cy0 - y0
        nucleo = rgb[ny0:ny0 + max(0, min(T, y1 - cy0)), nx0:nx0 + max(0, min(T, x1 - cx0))]
        focos.append(S.foco(nucleo.mean(axis=2)) if nucleo.size else float("nan"))
        fracs.append(_fraccion_tejido(prov, cx0, cy0, T) if prov is not None else 1.0)
        origenes.append((cx0, cy0))
        if muestra_h:
            px = o[np.isin(Lr, propios + 1)]
            if len(px) > MEDIDA["muestra_h_por_tesela"]:
                px = px[rng.choice(len(px), MEDIDA["muestra_h_por_tesela"], replace=False)]
            muestras.append(px)
    mh = np.concatenate(muestras) if muestras else np.zeros((0, 3))
    if len(mh) > MEDIDA["muestra_h_tope"]:
        mh = mh[rng.choice(len(mh), MEDIDA["muestra_h_tope"], replace=False)]
    out.update(foco_tesela=np.asarray(focos), tejido_tesela=np.asarray(fracs),
               origen_tesela=np.asarray(origenes).reshape(-1, 2), muestra_h=mh)
    return out


def _rejilla_a(prov, origen, f, forma):
    h_t, w_t = forma
    filas = np.clip(((origen[1] + (np.arange(h_t) + 0.5) * f) / prov["f"]).astype(int), 0,
                    prov["mascara"].shape[0] - 1)
    cols = np.clip(((origen[0] + (np.arange(w_t) + 0.5) * f) / prov["f"]).astype(int), 0,
                   prov["mascara"].shape[1] - 1)
    return prov["mascara"][np.ix_(filas, cols)]


def pasada_pliegues(lector, lamina, zona, i0, prov, bloque=2048):
    """ODsum de la zona a ~2 µm/px; pliegue = bandas ≥ 50 µm sobre el p99,5 del tejido (regla del
    plan); y los candidatos a pliegue grande que esa regla no ve, para la galería del tribunal."""
    m = S.ARTEFACTO["pliegue_mpp"]
    mpp_l0 = float(lamina.mpp_l0)
    f = m / mpp_l0
    minx, miny, maxx, maxy = zona.bounds
    W, H = _dims(lamina)
    maxx, maxy = min(maxx, W), min(maxy, H)
    w_t, h_t = max(1, int((maxx - minx) / f)), max(1, int((maxy - miny) / f))
    odsum = np.zeros((h_t, w_t), np.float32)
    for oy in range(0, h_t, bloque):
        for ox in range(0, w_t, bloque):
            w, h = min(bloque, w_t - ox), min(bloque, h_t - oy)
            x, y = minx + ox * f, miny + oy * f
            rgb = _lee(lector, lamina, m, x, y, w, h)
            odsum[oy:oy + h, ox:ox + w] = C.od(rgb, C.i0_region(i0, x, y, w, h, m, mpp_l0)).sum(-1)
    tejido = _rejilla_a(prov, (minx, miny), f, (h_t, w_t))
    pl, p995 = S.pliegues(odsum, tejido, m)
    cand = S.candidatos_pliegue(odsum, tejido, pl, m)
    for r in cand["regiones"]:
        r["xy_l0"] = [float(minx + r["centro_px"][0] * f), float(miny + r["centro_px"][1] * f)]
    return {"odsum": odsum, "tejido": tejido, "pliegue": pl, "p995": p995,
            "origen": (float(minx), float(miny)), "f": f, "candidatos": cand}


def _en(mapa, origen, f, xy):
    xy = np.asarray(xy, np.float64).reshape(-1, 2)
    xi = np.floor((xy[:, 0] - origen[0]) / f).astype(int)
    yi = np.floor((xy[:, 1] - origen[1]) / f).astype(int)
    ok = (xi >= 0) & (yi >= 0) & (xi < mapa.shape[1]) & (yi < mapa.shape[0])
    out = np.zeros(len(xy), bool)
    out[ok] = mapa[yi[ok], xi[ok]]
    return out


# ── pre-congelación ───────────────────────────────────────────────────────────────────────────
def _precongela_lamina(lector, nombre, segmentador, dir_salida, semilla):
    lamina = lector.abre(nombre)
    zona = lector.zona_escaneada(lamina)
    i0 = lector.i0_local(lamina)
    mpp_l0 = float(lamina.mpp_l0)
    inf = {"lamina": nombre, "mpp_l0": mpp_l0, "dimensiones_l0": list(_dims(lamina))}
    inf["lector_a"] = prueba_lector_a(lector, lamina, zona)
    inf["lector_bc"] = prueba_lector_bc(lector, nombre)
    inf["i0"] = revisa_i0(lector, lamina, nombre, i0)
    seg = segmentador(lector, lamina, nombre, zona=zona)
    pol = list(seg["celulas"])
    cents = S.centroides(pol)
    inf["segmentacion"] = {k: seg.get(k) for k in ("teselado", "dispositivo", "lote",
                                                     "aviso_check_input_tile", "pesos")}
    ref = S.mediana_area(pol, mpp_l0)
    inf["area_nuclear"] = {"mediana_um2": ref["mediana_um2"], "n": ref["n"],
                           "referencia_plan_um2": list(S.COMPROBACION["area_nuclear_um2"]),
                           "en_referencia_plan": ref["pasa"], "puerta": "acuerdo_area"}
    inf["acuerdo_area"] = acuerdo_area_lamina(lector, lamina, zona, i0, pol, cents, semilla)
    prov = S.mascara_provisional(cents, mpp_l0, _dims(lamina))
    inf["mascara_provisional"] = {"fragmentos_mm2": prov["areas_mm2"],
                                  "umbral_nucleos_mm2": prov["umbral"]}
    if seg.get("celulas_desplazadas") is not None:
        inf["rejilla_desplazada"] = S.cambio_rejilla(
            cents, S.centroides(seg["celulas_desplazadas"]), prov)
    else:
        inf["rejilla_desplazada"] = {"pasa": False, "detalle": "sin segmentación desplazada"}
    obj = pasada_objetos(lector, lamina, pol, i0, prov=prov, semilla=semilla, nombre=nombre)
    corte = S.corte_tercil_foco(obj["foco_tesela"], obj["tejido_tesela"])
    foco_bajo = np.zeros(len(pol), bool)
    if corte is not None and len(pol):
        foco_bajo = ~(obj["foco_tesela"][obj["tesela"]] >= corte)
    pli = pasada_pliegues(lector, lamina, zona, i0, prov)
    en_pliegue = _en(pli["pliegue"], pli["origen"], pli["f"], cents)
    gq = seg.get("grandqc") or {"carga": False, "artefactos": [], "motivo": "no ejecutado"}
    marcado_gq = np.zeros(len(pol), bool)
    gq_meta = {k: gq.get(k) for k in ("dispositivo", "intentos", "pesos")}
    if gq.get("carga"):
        fr = S.fracciones_grandqc(gq["artefactos"], cents, prov)
        marcado_gq = fr.pop("marcado_nucleo")
        inf["grandqc"] = dict(fr, carga=True, **gq_meta)
    else:
        inf["grandqc"] = dict({"carga": False, "motivo": gq.get("motivo")}, **gq_meta)
    inf["artefactos_clasicos"] = {"corte_tercil_foco": corte, "p995_odsum": pli["p995"],
                                  "objetos_pliegue": int(en_pliegue.sum()),
                                  "objetos_foco_bajo": int(foco_bajo.sum()),
                                  "candidatos_pliegue_grande": pli["candidatos"]}
    ruta_npz = os.path.join(dir_salida, "objetos_%s.npz" % nombre)
    pol_xy, pol_offs = S.empaqueta(pol)
    np.savez_compressed(
        ruta_npz, centroide=cents, area_um2=S.areas_um2(pol, mpp_l0),
        od_nucleo=obj["od_nucleo"], od_anillo=obj["od_anillo"], px_nucleo=obj["px_nucleo"],
        px_anillo=obj["px_anillo"], fragmento=S.fragmento_de(prov, cents),
        borde_um=S.distancia_borde(prov, cents), foco_bajo=foco_bajo, pliegue=en_pliegue,
        grandqc=marcado_gq, muestra_h=obj["muestra_h"], pol_xy=pol_xy, pol_offs=pol_offs,
        odsum_x8=pli["odsum"].astype(np.float16), tejido_x8=pli["tejido"],
        origen_x8=np.asarray(pli["origen"]), f_x8=np.asarray(pli["f"]))
    inf["objetos"] = {"fichero": os.path.basename(ruta_npz), "sha256": sha256_fichero(ruta_npz),
                      "n": int(len(pol))}
    return inf


def _motivos(inf):
    seg = inf["segmentacion"]
    t = (seg.get("teselado") or {})
    m = []
    if not inf["lector_a"]["ok"]:
        m.append("lector (a): dos lecturas distintas")
    if not inf["lector_bc"]["corrida"]:
        m.append("lector (b)(c) sin correr (ingesta: `lamina` y `prueba-vips`)")
    if not inf["i0"]["pasa"]:
        m.append("I0 local con patrón por franja")
    if not t.get("pasa"):
        m.append("teselado (nº, descartadas o tile_spec.mpp)")
    if seg.get("aviso_check_input_tile"):
        m.append("aviso de check_input_tile")
    pesos = seg.get("pesos") or {}
    if pesos.get("sha256") != S.INSTANSEG["sha256"] or pesos.get("bytes") != S.INSTANSEG["bytes"]:
        m.append("pesos de InstanSeg sin verificar contra el sha256 sellado")
    if not inf["rejilla_desplazada"].get("pasa"):
        m.append("rejilla desplazada 128 px: cambio ≥ 1 % en algún fragmento")
    if not inf["acuerdo_area"]["pasa"]:
        lo, hi = S.COMPROBACION["acuerdo_area"]
        m.append("área nuclear: InstanSeg/detector B en µm² (mismos núcleos) fuera de %.2f-%.2f "
                 "o con < %d pares" % (lo, hi, S.COMPROBACION["acuerdo_area_min_pares"]))
    if not inf["mascara_provisional"]["fragmentos_mm2"]:
        m.append("máscara provisional sin fragmentos ≥ 0,2 mm²")
    return m


def _archiva_precongelacion(dir_salida, sha_sello):
    """Antes de recongelar: la pre-congelación y los objetos que cita el sello vigente se mueven
    a `anterior-<sha12>/` (siguen verificables) en vez de pisarse."""
    destino = os.path.join(dir_salida, "anterior-%s" % sha_sello[:12])
    if os.path.exists(destino):
        raise RuntimeError("ya hay una pre-congelación archivada para el sello %s"
                           % sha_sello[:12])
    os.makedirs(destino, mode=0o700)
    ruta_pre = os.path.join(dir_salida, PRECONGELACION)
    if os.path.exists(ruta_pre):
        pre = _lee_verificado(ruta_pre, "precongelacion")
        for n in pre.get("laminas", {}):
            fichero = pre["laminas"][n]["objetos"]["fichero"]
            if os.path.exists(os.path.join(dir_salida, fichero)):
                os.replace(os.path.join(dir_salida, fichero), os.path.join(destino, fichero))
        os.replace(ruta_pre, os.path.join(destino, PRECONGELACION))
    return destino


def precongela(lector, dir_salida, segmentador=None, semilla=SEMILLA, para_recongelar=False):
    """Punto 2 del plan, SOLO P-HER2NEG y P-HER2. Escribe `precongelacion.json` (sha256) y los
    objetos de cada lámina; `pasa` decide si se puede congelar. Con sello, solo
    `para_recongelar` (una vez), que archiva antes la pre-congelación sellada."""
    os.makedirs(dir_salida, mode=0o700, exist_ok=True)
    ruta_sello = os.path.join(dir_salida, FICHERO_SELLO)
    archivada = None
    if os.path.exists(ruta_sello):
        if not para_recongelar:
            raise RuntimeError("ya hay sello: la pre-congelación sellada no se pisa (solo "
                               "`precongela --para-recongelar`, por error de ejecución, una vez)")
        previo = verifica_sello(ruta_sello)
        if previo.anterior is not None:
            raise SL.SelloInvalido("ya se recongeló una vez: no se recongela más")
        archivada = _archiva_precongelacion(dir_salida, previo.sha256)
    elif para_recongelar:
        raise RuntimeError("no hay sello que recongelar")
    segmentador = segmentador or S.segmentador_lazyslide
    laminas = {n: _precongela_lamina(lector, n, segmentador, dir_salida, semilla + i)
               for i, n in enumerate(SUELO)}
    gq = [laminas[n]["grandqc"] for n in SUELO]
    usa = all(g.get("carga") for g in gq) and not any(g.get("excede") for g in gq)
    motivo_gq = None
    if not usa:
        motivo_gq = "; ".join(
            "%s: %s" % (n, ("no carga (%s)" % g.get("motivo")) if not g.get("carga")
                        else "marca %.0f %% en un fragmento" % (100 * g["peor"]))
            for n, g in zip(SUELO, gq) if not g.get("carga") or g.get("excede"))
    decision = {"en_uso": usa, "rotulos": [] if usa else [S.GRANDQC["rotulo"]],
                "motivo": motivo_gq}
    if any(g.get("carga") for g in gq):
        # la galería es para JUZGAR GrandQC, también (sobre todo) cuando queda fuera
        decision["galeria"] = _galeria_grandqc(dir_salida, semilla)
    motivos = {n: _motivos(laminas[n]) for n in SUELO}
    declaraciones = [d for n in SUELO for d in (laminas[n]["lector_bc"]["declaracion"],
                                                laminas[n]["i0"]["declaracion"]) if d]
    inf = {"tipo": "precongelacion", "laminas": laminas, "grandqc": decision,
           "motivos_no_pasa": motivos, "pasa": not any(motivos.values()),
           "declaraciones": declaraciones, "semilla": semilla,
           "archivada_anterior": None if archivada is None else os.path.basename(archivada),
           "parametros": parametros()}
    return _escribe_json(os.path.join(dir_salida, PRECONGELACION), inf)


def _galeria_grandqc(dir_salida, semilla):
    """10 zonas marcadas y 10 no marcadas (posiciones L0 de núcleos), para el tribunal."""
    rng = np.random.default_rng(semilla + 7)
    k = MEDIDA["galeria_grandqc"]
    out = {}
    for n in SUELO:
        with np.load(os.path.join(dir_salida, "objetos_%s.npz" % n)) as z:
            c, m = z["centroide"], z["grandqc"].astype(bool)
        out[n] = {}
        for clave, sel in (("marcadas", np.flatnonzero(m)), ("no_marcadas", np.flatnonzero(~m))):
            out[n][clave] = (c[rng.choice(sel, min(k, len(sel)), replace=False)].tolist()
                             if len(sel) else [])
    return out


# ── área nuclear: acuerdo con el detector B (puerta) y diagnóstico ────────────────────────────
# Puerta (S.CRITERIO_AREA, declarado): en `acuerdo_area_teselas` ventanas de L0 al azar (semilla
# de la lámina), disjuntas, dentro de la imagen, tocando la zona y con ≥ `acuerdo_area_tejido_min`
# de píxeles de tejido a 2 µm/px (ODsum > el `odsum_tejido` del detector B: su misma definición
# de tejido; inferencia mía), el área de cada polígono de InstanSeg en µm² frente a la del MISMO
# núcleo según el detector B (watershed sobre ODsum a L0, píxeles × mpp_l0²).
# Diagnóstico (`precongela --diagnostico-area`): lo mismo sobre teselas de LazySlide y con el
# InstanSeg de producción, más el mapa del modelo (píxeles frente a contorno). Solo imprime
# números agregados y sale con CODIGO_DIAGNOSTICO (≠ 0) a propósito: la ventanilla solo toma como
# traza de memoria del procesador la de una corrida con rc 0, y la de un diagnóstico no es la de
# la pre-congelación.
DIAGNOSTICO_AREA = dict(teselas=20, mpp_tejido=2.0, semilla=SEMILLA + 300)
CODIGO_DIAGNOSTICO = 4


def _resumen(a):
    a = np.asarray(a, np.float64).ravel()
    a = a[np.isfinite(a)]
    if not len(a):
        return {"n": 0}
    return {"n": int(len(a)), "mediana": float(np.median(a)), "p10": float(np.percentile(a, 10)),
            "p90": float(np.percentile(a, 90))}


def _elige_teselas(lector, lamina, zona, i0, n, semilla):
    """`elige(esquinas, lado)` → índices: ventanas al azar (semilla fija), enteras dentro de la
    imagen, tocando la zona, sin solaparse (pueden compartir lado) y con tejido."""
    import shapely
    W, H = _dims(lamina)
    mpp_l0 = float(lamina.mpp_l0)
    m = DIAGNOSTICO_AREA["mpp_tejido"]
    tejido_min = S.COMPROBACION["acuerdo_area_tejido_min"]

    def elige(esquinas, lado):
        rng = np.random.default_rng(semilla)
        out, cajas = [], []
        for k in rng.permutation(len(esquinas)):
            x, y = (int(v) for v in esquinas[k])
            if x < 0 or y < 0 or x + lado > W or y + lado > H:
                continue
            caja = shapely.box(x, y, x + lado, y + lado)
            if not zona.intersects(caja) or any(caja.intersection(c).area > 0 for c in cajas):
                continue
            k_px = max(1, int(lado * mpp_l0 / m))
            rgb = _lee(lector, lamina, m, x, y, k_px, k_px)
            s = C.od(rgb, C.i0_region(i0, x, y, k_px, k_px, m, mpp_l0)).sum(-1)
            if float((s > S.DETECTOR_B["odsum_tejido"]).mean()) < tejido_min:
                continue
            out.append(int(k))
            cajas.append(caja)
            if len(out) >= n:
                break
        return out
    return elige


def _mide_area_teselas(lector, lamina, i0, poligonos, cents, esquinas, lado, margen):
    """Por ventana de L0 (x, y, lado): detector B sobre ODsum (etiquetas que tocan el marco,
    fuera), los polígonos de InstanSeg enteros dentro de la ventana y los pares del mismo núcleo
    (IoU > 0,5). Devuelve áreas en µm² (InstanSeg = polígono × mpp_l0², `S.areas_um2`; B =
    píxeles × mpp_l0²) y, por par, InstanSeg/B en µm² y en píxeles rasterizados."""
    mpp_l0 = float(lamina.mpp_l0)
    b0 = np.asarray([p.bounds for p in poligonos], np.float64).reshape(-1, 4)
    a_pol = S.areas_um2(poligonos, mpp_l0)
    inst, b, c_um2, c_px, umbrales = [], [], [], [], []
    for x, y in np.asarray(esquinas, np.int64).reshape(-1, 2):
        rgb = _lee(lector, lamina, mpp_l0, x, y, lado, lado)
        odsum = C.od(rgb, C.i0_region(i0, x, y, lado, lado, mpp_l0, mpp_l0)).sum(-1)
        lab_b, _, _, u = S.detector_clasico(odsum, mpp_l0)
        lab_b = S.quita_borde(lab_b, margen)
        b.append(np.unique(lab_b[lab_b > 0], return_counts=True)[1] * mpp_l0 * mpp_l0)
        umbrales.append(u)
        sel = np.flatnonzero((cents[:, 0] >= x) & (cents[:, 0] < x + lado)
                             & (cents[:, 1] >= y) & (cents[:, 1] < y + lado)
                             & (b0[:, 0] >= x + margen) & (b0[:, 1] >= y + margen)
                             & (b0[:, 2] <= x + lado - margen) & (b0[:, 3] <= y + lado - margen))
        inst.append(a_pol[sel])
        pr = S.pares_iou(S.rasteriza([poligonos[j] for j in sel], x, y, (lado, lado)), lab_b)
        c_um2.append(a_pol[sel[pr["a"] - 1]] / (np.maximum(pr["area_b"], 1) * mpp_l0 * mpp_l0))
        c_px.append(pr["area_a"] / np.maximum(pr["area_b"], 1))

    def cat(v):
        return np.concatenate(v) if v else np.zeros(0)
    return {"instanseg_um2": cat(inst), "b_um2": cat(b), "cociente_um2": cat(c_um2),
            "cociente_px": cat(c_px), "umbral_otsu": np.asarray(umbrales, np.float64)}


def acuerdo_area_lamina(lector, lamina, zona, i0, poligonos, cents, semilla):
    """Puerta del área nuclear de una lámina de suelo (S.CRITERIO_AREA): ventanas de L0 del
    tamaño de una tesela de 512 px a 0,5 µm/px, en rejilla anclada en la esquina de la zona."""
    mpp_l0 = float(lamina.mpp_l0)
    ds = S.TESELA["mpp"] / mpp_l0
    lado = int(S.TESELA["tile_px"] * ds)
    minx, miny, maxx, maxy = zona.bounds
    esq = np.array([(x, y) for y in np.arange(int(miny), int(maxy), lado)
                    for x in np.arange(int(minx), int(maxx), lado)], np.int64).reshape(-1, 2)
    elige = _elige_teselas(lector, lamina, zona, i0, S.COMPROBACION["acuerdo_area_teselas"],
                           semilla)
    esq = esq[np.asarray(elige(esq, lado), np.int64)]
    r = _mide_area_teselas(lector, lamina, i0, poligonos, cents, esq, lado,
                           int(math.ceil(2 * ds)))
    out = S.acuerdo_area(r["cociente_um2"])
    out.update(ventanas=int(len(esq)), lado_l0=lado, instanseg_um2=_resumen(r["instanseg_um2"]),
               detector_b_um2=_resumen(r["b_um2"]))
    return out


def diagnostico_area(lector, nombre="P-HER2NEG", n_teselas=None, semilla=None, diagnostico=None,
                     dir_trabajo=None):
    """Área nuclear en µm² (mediana, p10, p90) en teselas de una lámina de SUELO: InstanSeg
    (polígonos L0 de producción × mpp_l0², y el mapa del modelo: recuento de píxeles y contorno)
    frente al detector clásico B sobre ODsum a L0 (recuento de píxeles × mpp_l0²), más el cociente
    InstanSeg/B en los MISMOS núcleos (pares con IoU > 0,5 en L0) y la puerta (CRITERIO_AREA) sobre
    esos pares. Ni sello ni pre-congelación: solo temporales, que se borran. `diagnostico`
    (tests) sustituye a `S.diagnostico_instanseg`."""
    import shutil
    import tempfile
    if nombre not in SUELO:
        raise ValueError("el diagnóstico de área solo mide láminas de suelo (%s)" % ", ".join(SUELO))
    _sello_para_medir(None, nombre)
    p = DIAGNOSTICO_AREA
    n_teselas = n_teselas or p["teselas"]
    semilla = p["semilla"] if semilla is None else semilla
    lamina = lector.abre(nombre)
    zona = lector.zona_escaneada(lamina)
    i0 = lector.i0_local(lamina)
    mpp_l0 = float(lamina.mpp_l0)
    elige = _elige_teselas(lector, lamina, zona, i0, n_teselas, semilla)
    tmp = dir_trabajo or tempfile.mkdtemp(prefix="diagnostico-area-")
    try:
        d = (diagnostico or S.diagnostico_instanseg)(lector, lamina, nombre, zona, elige, tmp)
    finally:
        if dir_trabajo is None:
            shutil.rmtree(tmp, ignore_errors=True)
    pol = list(d["celulas"])
    r = _mide_area_teselas(lector, lamina, i0, pol, S.centroides(pol), d["esquinas"],
                           int(d["lado_l0"]), int(math.ceil(2 * d["base_downsample"])))
    inst, b = r["instanseg_um2"], r["b_um2"]
    lo, hi = S.DETECTOR_B["area_min_um2"], S.DETECTOR_B["area_max_um2"]
    mapa = d["mapa"]
    return {"lamina": nombre, "teselas": int(len(d["esquinas"])), "lado_l0": int(d["lado_l0"]),
            "mpp_l0": mpp_l0, "base_downsample": d["base_downsample"],
            "tile_spec_mpp": d["tile_spec_mpp"], "teselado_pasa": d.get("teselado_pasa"),
            "instanseg_poligono_um2": _resumen(inst),
            "instanseg_poligono_en_rango_b_um2": _resumen(inst[(inst >= lo) & (inst <= hi)]),
            "instanseg_mapa_pixeles_um2": _resumen(mapa["pixeles_um2"]),
            "instanseg_mapa_contorno_um2": _resumen(mapa["contorno_um2"]),
            "contorno_entre_pixeles": _resumen(mapa["contorno_um2"]
                                               / np.maximum(mapa["pixeles_um2"], 1e-12)),
            "detector_b_um2": _resumen(b), "detector_b_umbral_otsu": _resumen(r["umbral_otsu"]),
            "pares_iou_05": {"n": int(len(r["cociente_px"])),
                             "cociente_instanseg_entre_b": _resumen(r["cociente_px"]),
                             "cociente_instanseg_entre_b_um2": _resumen(r["cociente_um2"])},
            "puerta": S.acuerdo_area(r["cociente_um2"]),
            "fraccion_bajo_25_um2": {"instanseg": float((inst < 25).mean()) if len(inst) else None,
                                     "detector_b": float((b < 25).mean()) if len(b) else None},
            "referencia_plan_um2": list(S.COMPROBACION["area_nuclear_um2"]),
            "rango_detector_b_um2": [lo, hi]}


# ── regla física del mpp: diámetro de hematíes en el hueso (diagnóstico) ──────────────────────
# El mpp de L0 (0,2506 µm/px) sale de la etiqueta del TIFF Grundium y TODO lo medido en µm depende
# de él. Regla física: diámetro de hematíes aislados y de cara en las H&E de HUESO (B-HE-1, B-HE-2:
# médula ósea, muchos hematíes). No son láminas diana del piloto, no se mide ningún núcleo y nada de
# esto informa T, vectores ni umbrales. Referencia: el hematíe humano en fresco mide ≈ 7,5-8,7 µm de
# diámetro (Diez-Silva et al., MRS Bull 2010;35:382, PMID 21151848, PMC2998922); fijado y en
# parafina es algo menor (retracción: inferencia, sin fuente). Aceptación: mediana 6,0-9,0 µm en
# cada lámina con ≥ `n_min` hematíes; fuera, no se sella.
# Selección (en píxeles de L0; solo la compuerta de tamaño usa el mpp, y es ANCHA a propósito: con
# un mpp ×2 o ×½ un hematíe de 7,5 µm saldría a 3,75 o 15 µm y caería dentro, así que la compuerta
# no fabrica el resultado):
#  · OD suavizada (gaussiana de 1 px de L0): el croma del JPEG de L0 va submuestreado 2×2;
#  · color, eosinófilo intenso: «rojo» = OD_R ≤ 0,40·OD_G (el hematíe deja pasar el rojo; la
#    hematoxilina lo absorbe); semillas con OD_G ≥ 0,30; cada componente de semillas es un
#    candidato y su borde va a media altura entre SU fondo (mediana de OD_G de su recorte fuera de
#    él) y SU meseta (p90 de su OD_G);
#    huecos rellenos (palidez central); fuera lo que toca el marco de la ventana;
#  · de cara y discoide: ejes de la elipse de mismos momentos menor/mayor ≥ 0,80, solidez ≥ 0,90,
#    circularidad 4πA/P² (perímetro de Crofton) ≥ 0,80 y radio del círculo inscrito ≥ 0,85 del
#    equivalente (un racimo de hematíes que se tocan puede salir redondo y sólido, pero su círculo
#    inscrito es mucho menor que el de un disco de su área: 0,7 con cuatro en cruz);
#  · sin núcleo: < 5 % de píxeles «nucleares» (OD_R ≥ 0,60·OD_G y ODsum ≥ 0,30) en el objeto;
#  · aislado: ningún otro componente de semillas a ≤ 3 px y ≤ 10 % del suyo fuera de esos 3 px
#    (dos hematíes a 1-2 px pueden compartir componente);
#  · tamaño: diámetro equivalente 3-15 µm.
# Ventanas de 1024 px de L0 al azar (semilla fija), dentro de la imagen, tocando la zona y con
# ≥ 25 % de tejido. Sensibilidad: la mediana con la semilla de color ×0,8/×1,25 y el borde al
# 35 %/65 %. Solo imprime números agregados y sale con CODIGO_DIAGNOSTICO.
# Sintético (hematíes de 6,5/7,5/9,5 µm con trampas): mediana a ±0,02 µm de la verdad.
# Real, 2-oct-26 (400 ventanas por lámina): NO CONCLUYENTE. B-HE-1 n 27 (mediana 4,2 µm), B-HE-2
# n 5 (4,6 µm), RGB mediano de los aceptados magenta (≈180, 102, 182), no rojo; de ~13.100 y ~5.900
# candidatos rojos no diminutos, ~9.700 y ~4.500 fallan la forma (≥ 94 % de ellos por ejes
# < 0,80), en todas las clases de intensidad: los objetos rojos de estas láminas no son discos
# aislados para esta selección. El mpp de la etiqueta NO queda comprobado por esta regla.
HUESO_MPP = ("B-HE-1", "B-HE-2")
MPP_ETIQUETA = 0.2506
REGLA_MPP = dict(ventana_px=1024, ventanas_max=400, hematies_max=4000, semilla=SEMILLA + 400,
                 tejido_odsum=0.08, tejido_min=0.25, rojo_r_entre_g_max=0.40, semilla_g_min=0.30,
                 borde_media_altura=0.5, ejes_min=0.80, solidez_min=0.90,
                 circularidad_min=0.80, inscrito_min=0.85, nucleo_r_entre_g_min=0.60, nucleo_odsum_min=0.30,
                 nucleo_frac_max=0.05, aislado_px=3, propio_fuera_max=0.10, diminuto_px=10, diametro_um=(3.0, 15.0),
                 aceptacion_um=(6.0, 9.0), n_min=100, histograma_um=(1.0, 20.0, 0.5),
                 suavizado_px=1.0, tabla_meseta_bordes=(0.5, 0.8, 1.2),
                 color_cociente_bordes=(-10.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0,
                                        10.0),
                 color_g_bordes=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 10.0),
                 sensibilidad={"semilla_g_min x0.8": {"semilla_g_min": 0.24},
                               "semilla_g_min x1.25": {"semilla_g_min": 0.375},
                               "borde 35 %": {"borde_media_altura": 0.35},
                               "borde 65 %": {"borde_media_altura": 0.65}})
CRITERIO_MPP = ("mpp de L0: regla física con hematíes de las H&E de hueso (B-HE-1, B-HE-2), "
                "aislados, de cara, eosinófilos intensos y sin núcleo; diámetro equivalente con el "
                "mpp de la etiqueta; se acepta con mediana 6,0-9,0 µm y ≥ 100 hematíes en cada "
                "lámina (`precongela --diagnostico-mpp`)")
_MOTIVOS_MPP = ("diminuto", "toca_marco", "forma", "nucleo", "no_aislado", "tamano")


def _od_suave(rgb, i0, p):
    """OD con un suavizado gaussiano de `suavizado_px` (px de L0) por canal: el TIFF Grundium
    guarda L0 en JPEG YCbCr con el croma submuestreado 2×2, y el cociente de canales píxel a píxel
    es ruido de croma (en las láminas reales troceaba cada hematíe en decenas de motas)."""
    from scipy import ndimage as ndi
    s = float(p["suavizado_px"])
    od = C.od(rgb, i0)
    return ndi.gaussian_filter(od, (s, s, 0)) if s > 0 else od


def _color_tejido(rgb, i0, p):
    """Recuento 2D (OD_R/OD_G × OD_G, OD suavizada) de los píxeles de tejido de una ventana: la
    distribución de color agregada con la que se juzga la regla de color, sin mirar tamaños."""
    od = _od_suave(rgb, i0, p)
    g = od[..., 1]
    t = (od.sum(axis=-1) > p["tejido_odsum"]) & (g > 0)
    cociente = od[..., 0][t] / g[t]
    return np.histogram2d(cociente, g[t], bins=(np.asarray(p["color_cociente_bordes"]),
                                                np.asarray(p["color_g_bordes"])))[0]


def _hematies_ventana(rgb, i0, mpp_l0, p):
    """Diámetros equivalentes (µm) de los hematíes que pasan la selección en una ventana, el RGB
    medio de cada uno y el recuento de descartes por motivo (`_MOTIVOS_MPP`).
    Cada candidato nace de un componente de semillas (rojo e intenso) y su borde se pone a
    `borde_media_altura` entre SU fondo (mediana de OD_G de su recorte fuera de él) y SU meseta
    (p90 de OD_G en el componente): así el borde no depende del desenfoque, del estroma de al lado
    ni de la intensidad de cada hematíe."""
    from scipy import ndimage as ndi
    from skimage.measure import regionprops
    od = _od_suave(rgb, i0, p)
    r, g = od[..., 0], od[..., 1]
    descartes = dict.fromkeys(_MOTIVOS_MPP, 0)
    tabla = {}                                  # {clase de meseta: {motivo: n}} (diagnóstico)
    vacio = {"diametros_um": np.zeros(0), "rgb": np.zeros((0, 3)), "candidatos": 0,
             "descartes": descartes, "tabla": tabla}
    rojo = (g > 0) & (r <= p["rojo_r_entre_g_max"] * g)
    semillas = rojo & (g >= p["semilla_g_min"])
    if not semillas.any():
        return vacio
    ocho = np.ones((3, 3), bool)
    lab, n = ndi.label(semillas, structure=ocho)
    nucleo = (r >= p["nucleo_r_entre_g_min"] * g) & (od.sum(axis=-1) >= p["nucleo_odsum_min"])
    H, W = lab.shape
    lo, hi = p["diametro_um"]
    k = int(p["aislado_px"])
    diam, cols = [], []
    cortes_meseta = p["tabla_meseta_bordes"]

    def anota(clase, motivo, *formas):
        descartes[motivo] = descartes.get(motivo, 0) + 1
        t = tabla.setdefault(clase, {})
        for m in (motivo,) + formas:
            t[m] = t.get(m, 0) + 1

    for rp in regionprops(lab):
        if rp.area < p["diminuto_px"]:
            descartes["diminuto"] += 1
            continue
        y0, x0, y1, x1 = rp.bbox
        m = int(math.ceil(0.5 * max(y1 - y0, x1 - x0))) + k + 2
        a0, b0, a1, b1 = max(0, y0 - m), max(0, x0 - m), min(H, y1 + m), min(W, x1 + m)
        sg, sl = g[a0:a1, b0:b1], lab[a0:a1, b0:b1]
        nucleo_sub = sl == rp.label
        meseta = float(np.percentile(sg[nucleo_sub], 90))
        i = int(np.searchsorted(cortes_meseta, meseta, side="right"))
        clase = "OD_G %s" % ("<%g" % cortes_meseta[0] if i == 0 else
                             ">=%g" % cortes_meseta[-1] if i == len(cortes_meseta) else
                             "%g-%g" % (cortes_meseta[i - 1], cortes_meseta[i]))
        fuera = ~ndi.binary_dilation(nucleo_sub, iterations=k + 2)
        fondo = min(max(float(np.median(sg[fuera])), 0.0), meseta) if fuera.any() else 0.0
        cand = rojo[a0:a1, b0:b1] & (sg >= fondo + p["borde_media_altura"] * (meseta - fondo))
        lab2, _ = ndi.label(cand, structure=ocho)
        ids, cuenta = np.unique(lab2[nucleo_sub], return_counts=True)
        cuenta = cuenta[ids > 0]
        ids = ids[ids > 0]
        if not len(ids):
            anota(clase, "forma")
            continue
        yo = ndi.binary_fill_holes(lab2 == ids[np.argmax(cuenta)])
        ys, xs = np.nonzero(yo)
        if ys.min() == 0 or xs.min() == 0 or ys.max() == yo.shape[0] - 1 or \
                xs.max() == yo.shape[1] - 1:
            # llega al borde del recorte: al de la ventana (cortado) o se derrama (no aislado)
            marco = (a0 == 0 and ys.min() == 0) or (b0 == 0 and xs.min() == 0) or \
                (a1 == H and ys.max() == yo.shape[0] - 1) or (b1 == W and xs.max() == yo.shape[1] - 1)
            anota(clase, "toca_marco" if marco else "no_aislado")
            continue
        rq = regionprops(yo.astype(np.uint8))[0]
        A = float(rq.area)
        per = float(rq.perimeter_crofton)
        eje = float(rq.axis_major_length)
        ejes = float(rq.axis_minor_length) / eje if eje > 0 else 0.0
        circ = 4 * math.pi * A / (per * per) if per > 0 else 0.0
        r_eq = math.sqrt(A / math.pi)
        inscrito = float(ndi.distance_transform_edt(np.pad(yo, 1)).max()) / r_eq
        fallan = tuple(n for n, malo in (("forma:ejes", ejes < p["ejes_min"]),
                                         ("forma:solidez", rq.solidity < p["solidez_min"]),
                                         ("forma:circularidad", circ < p["circularidad_min"]),
                                         ("forma:inscrito", inscrito < p["inscrito_min"])) if malo)
        if fallan:
            anota(clase, "forma", *fallan)
            continue
        if float(nucleo[a0:a1, b0:b1][yo].mean()) >= p["nucleo_frac_max"]:
            anota(clase, "nucleo")
            continue
        # aislado: ningún otro componente de semillas a ≤ k px, y su propio componente no se
        # extiende más allá (dos hematíes a 1-2 px pueden compartir componente de semillas)
        cerca = ndi.binary_dilation(yo, iterations=k)
        propio_fuera = int((nucleo_sub & ~cerca).sum())
        if np.any(cerca & (sl > 0) & (sl != rp.label)) or \
                propio_fuera > p["propio_fuera_max"] * int(nucleo_sub.sum()):
            anota(clase, "no_aislado")
            continue
        d = 2.0 * r_eq * mpp_l0
        if not lo <= d <= hi:
            anota(clase, "tamano")
            continue
        t = tabla.setdefault(clase, {})
        t["aceptado"] = t.get("aceptado", 0) + 1
        diam.append(d)
        cols.append(rgb[a0:a1, b0:b1][yo].reshape(-1, 3).mean(axis=0))
    return {"diametros_um": np.asarray(diam, np.float64),
            "rgb": np.asarray(cols, np.float64).reshape(-1, 3), "candidatos": int(n),
            "descartes": descartes, "tabla": tabla}


def _ventanas_hueso(lamina, zona, p):
    """Esquinas (px de L0) de las ventanas candidatas: rejilla anclada en la caja de la zona, que
    tocan la zona y caben enteras en la imagen, en orden aleatorio con la semilla fija."""
    import shapely
    W, H = _dims(lamina)
    lado = int(p["ventana_px"])
    minx, miny, maxx, maxy = zona.bounds
    esq = [(x, y) for y in range(int(miny), int(maxy), lado)
           for x in range(int(minx), int(maxx), lado)
           if x >= 0 and y >= 0 and x + lado <= W and y + lado <= H
           and zona.intersects(shapely.box(x, y, x + lado, y + lado))]
    orden = np.random.default_rng(p["semilla"]).permutation(len(esq))
    return [esq[i] for i in orden]


def _medida_mpp(d, p):
    lo, hi = p["aceptacion_um"]
    r = _resumen(d)
    r["estado"] = ("insuficiente" if r["n"] < p["n_min"] else
                   "en rango" if lo <= r["mediana"] <= hi else "fuera de rango")
    r["pasa"] = r["estado"] == "en rango"
    return r


def regla_mpp_lamina(lector, nombre, regla=None):
    """Regla física del mpp en UNA lámina de hueso: números agregados (n, mediana, p10, p90 del
    diámetro equivalente en µm con el mpp de la etiqueta, histograma, descartes por motivo, RGB
    mediano de los aceptados y sensibilidad). Ni escribe ni sella."""
    if nombre not in HUESO_MPP:
        raise ValueError("la regla del mpp solo mide hematíes en el hueso (%s)"
                         % ", ".join(HUESO_MPP))
    p = dict(REGLA_MPP, **(regla or {}))
    lamina = lector.abre(nombre)
    zona = lector.zona_escaneada(lamina)
    i0f = lector.i0_local(lamina)
    mpp_l0 = float(lamina.mpp_l0)
    lado = int(p["ventana_px"])
    variantes = {"base": p}
    variantes.update({k: dict(p, **v) for k, v in p["sensibilidad"].items()})
    acum = {k: [] for k in variantes}
    rgbs, candidatos = [], 0
    color, tabla = 0.0, {}
    descartes = dict.fromkeys(_MOTIVOS_MPP, 0)
    leidas = con_tejido = 0
    for x, y in _ventanas_hueso(lamina, zona, p):
        if con_tejido >= p["ventanas_max"] or \
                sum(len(v) for v in acum["base"]) >= p["hematies_max"]:
            break
        rgb = _lee(lector, lamina, mpp_l0, x, y, lado, lado)
        i0 = C.i0_region(i0f, x, y, lado, lado, mpp_l0, mpp_l0)
        leidas += 1
        if float((C.od(rgb, i0).sum(axis=-1) > p["tejido_odsum"]).mean()) < p["tejido_min"]:
            continue
        con_tejido += 1
        color = color + _color_tejido(rgb, i0, p)
        for k, pv in variantes.items():
            h = _hematies_ventana(rgb, i0, mpp_l0, pv)
            acum[k].append(h["diametros_um"])
            if k == "base":
                rgbs.append(h["rgb"])
                candidatos += h["candidatos"]
                for m, v in h["descartes"].items():
                    descartes[m] += v
                for c, t in h["tabla"].items():
                    tc = tabla.setdefault(c, {})
                    for m, v in t.items():
                        tc[m] = tc.get(m, 0) + v
    d = {k: (np.concatenate(v) if v else np.zeros(0)) for k, v in acum.items()}
    a, b, paso = p["histograma_um"]
    bordes = np.arange(a, b + paso / 2, paso)
    rgb_ok = np.concatenate(rgbs) if rgbs else np.zeros((0, 3))
    base = _medida_mpp(d["base"], p)
    lo, hi = p["diametro_um"]
    return {"lamina": nombre, "mpp_l0": mpp_l0,
            "mpp_igual_al_del_piloto": bool(abs(mpp_l0 - MPP_ETIQUETA) / MPP_ETIQUETA <= 0.005),
            "ventanas_leidas": leidas, "ventanas_con_tejido": con_tejido,
            "lado_ventana_um": lado * mpp_l0, "candidatos": candidatos, "descartes": descartes,
            "diametro_um": base,
            "fraccion_en_extremos_compuerta": (float(((d["base"] < lo + 0.5)
                                                      | (d["base"] > hi - 0.5)).mean())
                                               if len(d["base"]) else None),
            "histograma_um": {"bordes": bordes.tolist(),
                              "n": np.histogram(d["base"], bordes)[0].tolist()},
            "rgb_mediano_aceptados": (np.median(rgb_ok, axis=0).round(1).tolist()
                                      if len(rgb_ok) else None),
            "sensibilidad_mediana_um": {k: _resumen(v).get("mediana")
                                        for k, v in d.items() if k != "base"},
            "sensibilidad_n": {k: int(len(v)) for k, v in d.items() if k != "base"},
            "candidatos_por_meseta": tabla,
            "color_tejido": {"filas_od_r_entre_od_g": list(p["color_cociente_bordes"]),
                             "columnas_od_g": list(p["color_g_bordes"]),
                             "fraccion": (np.round(color / max(float(np.sum(color)), 1.0), 5)
                                          .tolist() if np.ndim(color) else None)}}


def regla_mpp(lector, laminas=HUESO_MPP, regla=None):
    """La regla del mpp en las láminas de hueso y el veredicto: se acepta el mpp de la etiqueta si
    TODAS pasan (mediana 6,0-9,0 µm con ≥ n_min hematíes); si no, no se sella. «No concluyente»
    (alguna con < n_min) no es lo mismo que «fuera de rango»: lo primero dice que la selección no
    encontró hematíes bastantes; lo segundo, que el mpp no casa."""
    p = dict(REGLA_MPP, **(regla or {}))
    por = {n: regla_mpp_lamina(lector, n, regla) for n in laminas}
    pasa = bool(por) and all(r["diametro_um"]["pasa"] for r in por.values())
    fuera = [n for n, r in por.items() if r["diametro_um"]["estado"] == "fuera de rango"]
    pocos = [n for n, r in por.items() if r["diametro_um"]["estado"] == "insuficiente"]
    return {"criterio": CRITERIO_MPP, "aceptacion_um": list(p["aceptacion_um"]),
            "n_min": p["n_min"], "referencia": "Diez-Silva et al. 2010, PMID 21151848: "
            "7.5-8.7 um (fresh discocyte)", "laminas": por, "pasa": pasa,
            "veredicto": ("se acepta el mpp de la etiqueta" if pasa else
                          "NO se acepta, fuera de rango (%s): no sellar" % ", ".join(fuera)
                          if fuera else
                          "NO concluyente, < %d hematíes (%s): no sellar"
                          % (p["n_min"], ", ".join(pocos)))}


# ── congelación (0) ───────────────────────────────────────────────────────────────────────────
def _carga_objetos(dir_salida, pre, nombre):
    ent = pre["laminas"][nombre]["objetos"]
    ruta = os.path.join(dir_salida, ent["fichero"])
    if sha256_fichero(ruta) != ent["sha256"]:
        raise SL.SelloInvalido("%s: objetos cambiados desde la pre-congelación" % nombre)
    with np.load(ruta) as z:
        d = {k: z[k] for k in z.files}
    d.setdefault("saturado", np.zeros(len(d["centroide"]), bool))
    return d


def _exclusion(obj, regimen, revision=None, M=None):
    """Objetos fuera de T: artefacto (según régimen), tercil bajo de foco, 50 µm del borde de
    fragmento, saturados no H/DAB y núcleos fuera de los fragmentos ≥ 0,2 mm²."""
    ex = (obj["foco_bajo"].astype(bool) | (obj["borde_um"] < S.ARTEFACTO["borde_um"])
          | obj["saturado"].astype(bool) | (obj["fragmento"] < 0))
    ex = ex | (obj["pliegue"].astype(bool) if regimen == "clasicas"
               else obj["grandqc"].astype(bool))
    if revision:
        ex = ex | regla_alto(obj, revision, M)
    return ex


def regla_alto(obj, revision, M):
    """Las tres reglas ESCRITAS de la rama > 0,25 (pigmento, pliegue, borde). Se sellan y se
    aplican después a las 11."""
    regla, par = revision["regla"], revision.get("parametros") or {}
    if regla == "pigmento":
        c = np.abs(C.desmezcla(obj["od_nucleo"], M))
        fuera = c[:, 2] / np.maximum(c.sum(axis=1), 1e-9)
        return np.nan_to_num(fuera, nan=1.0) > float(par.get("fuera_plano_min",
                                                             C.FUERA_PLANO_MIN))
    if regla == "borde":
        return obj["borde_um"] < float(par.get("um", 100.0))
    if regla == "pliegue":
        s = obj["odsum_x8"].astype(np.float32)
        tej = obj["tejido_x8"].astype(bool)
        p = float(np.percentile(s[tej], float(par.get("percentil", 99.0)))) if tej.any() else 0
        r = max(1, int(round(float(par.get("ancho_um", S.ARTEFACTO["pliegue_ancho_um"]))
                             / 2.0 / S.ARTEFACTO["pliegue_mpp"])))
        pl = C.morfologia((s > p) & tej, r, "apertura")
        return _en(pl, tuple(obj["origen_x8"]), float(obj["f_x8"]), obj["centroide"])
    raise ValueError("regla de la rama alta desconocida: %r" % regla)


def _bloques(obj, mpp_l0, bloque_um=None):
    """Bloque de cada objeto para el bootstrap de la tasa: (fragmento, columna, fila) de una
    rejilla de `bloque_um` (200 µm) en L0."""
    b = (bloque_um or C.TASA["bloque_um"]) / float(mpp_l0)
    xy = np.asarray(obj["centroide"], np.float64)
    return np.column_stack([np.asarray(obj["fragmento"]), np.floor(xy[:, 0] / b),
                            np.floor(xy[:, 1] / b)]).astype(np.int64)


def _galeria(obj, M, ex):
    out = {}
    for comp in COMPS:
        v = C.dab_de(obj["od_" + comp], M)
        v = np.where(ex | ~np.isfinite(v), -np.inf, v)
        top = np.argsort(-v, kind="stable")[:MEDIDA["galeria"]]
        out[comp] = [{"xy_l0": obj["centroide"][i].tolist(), "dab": float(v[i])}
                     for i in top if np.isfinite(v[i])]
    return out


def calcula_T(neg, pos, M, grandqc_en_uso, semilla, revision=None, mpp_pos=None):
    """T por compartimento en los dos regímenes (sobre HER2NEG), qué régimen rige, rama > 0,25 en
    TODOS los regímenes (`alto` = [[régimen, compartimento], …]: el que no rige hoy puede regir
    tras la comprobación de GrandQC en P-KI67), galería de los 50 objetos más altos por régimen y
    tasa fuera de muestra en P-HER2 (núcleo y anillo) por régimen, con IC binomial y por bootstrap
    de bloques si se da `mpp_pos`."""
    regs = ["clasicas"] + (["grandqc"] if grandqc_en_uso else [])
    rige = "grandqc" if grandqc_en_uso else "clasicas"
    bloques = _bloques(pos, mpp_pos) if mpp_pos else None
    T, galeria, fp, ex_neg, alto = {}, {}, {}, {}, []
    for reg in regs:
        ex = _exclusion(neg, reg, revision, M)
        ex_neg[reg] = ex
        T[reg] = {comp: C.umbral_T(C.dab_de(neg["od_" + comp], M), neg["fragmento"], ex,
                                   semilla=semilla)
                  for comp in COMPS}
        alto += [[reg, c] for c in COMPS if T[reg][c]["estado"] == "alto"]
        galeria[reg] = _galeria(neg, M, ex)
        ex_pos = _exclusion(pos, reg, revision, M)
        fp[reg] = {}
        for comp in COMPS:
            v = C.dab_de(pos["od_" + comp], M)
            fp[reg][comp] = C.tasa_sobre_T(v, T[reg][comp]["T"], ex_pos, bloques=bloques,
                                           semilla=semilla)
            # la misma tasa en los extremos de la banda de T (la puerta de señal la mira)
            lo, hi = T[reg][comp]["banda"]
            fp[reg][comp]["banda"] = {
                "T_bajo": dict(C.tasa_sobre_T(v, lo, ex_pos, bloques=bloques, semilla=semilla),
                               T=float(lo)),
                "T_alto": dict(C.tasa_sobre_T(v, hi, ex_pos, bloques=bloques, semilla=semilla),
                               T=float(hi))}
        fp[reg]["anillo"]["declaracion"] = DECL_FP_ANILLO
    return {"regimenes": T, "rige": rige, "alto": alto, "galeria_her2neg": galeria[rige],
            "galeria_por_regimen": galeria, "fp_her2": fp[rige], "fp_her2_regimenes": fp,
            "exclusion_her2neg": ex_neg}


def _por_regimen(t):
    """Rótulos, «high floor» y banda obligatoria POR RÉGIMEN: si tras el sello rige el otro
    régimen, hereda los suyos."""
    out = {}
    for reg, comps in t["regimenes"].items():
        alto = [c for c in COMPS if comps[c]["estado"] == "alto"]
        rot = sorted({r for c in COMPS for r in comps[c]["rotulos"]})
        if alto:
            rot.append(C.ROTULOS["high_floor"])
        out[reg] = {"T": {c: comps[c]["T"] for c in COMPS},
                    "banda": {c: comps[c]["banda"] for c in COMPS}, "alto": alto,
                    "high_floor": bool(alto), "banda_obligatoria": bool(alto), "rotulos": rot}
    return out


def elige_L(tre_p90_um, mediana_nucleos):
    """Regla de L (se sella en (0); el valor, tras (ii)): el menor candidato con L ≥ 2× p90 del
    TRE del par y mediana ≥ 100 núcleos del denominador por región."""
    for L in REGLA_L["candidatos_um"]:
        med = mediana_nucleos.get(L, mediana_nucleos.get(str(L)))
        if med is None:
            continue
        if L >= REGLA_L["tre_factor"] * tre_p90_um and med >= REGLA_L["mediana_min_nucleos"]:
            return L
    return None


def _vectores_y_residuo(lector, objs_suelo, semilla, laminas_residuo, px_kw):
    """Vectores de tanda (H de los núcleos de suelo, DAB de KI67/SYN/CK19) y residuo (i)-(iii) de
    `laminas_residuo` (las 11), todo a nivel de píxel; umbral del residuo = 2× el máximo de las
    cinco de tanda."""
    h, n_h = C.vector_h(np.concatenate([objs_suelo[n]["muestra_h"] for n in SUELO]))
    px = {}
    for i, nombre in enumerate(laminas_residuo):
        lam = lector.abre(nombre)
        px[nombre] = muestra_pixeles(lector, lam, lector.zona_escaneada(lam),
                                     lector.i0_local(lam), semilla + 100 + i, **px_kw)
    od_dab = np.concatenate([px[n][0][~px[n][1]] for n in DIANA_TANDA])
    d, n_d = C.vector_dab(od_dab, h)
    M = C.matriz(h, d)
    res = {}
    for nombre in laminas_residuo:
        o, s = px[nombre]
        r, n_r = C.residuo(o, M, s)
        dl, n_dl, nl = C.direccion_dab_lamina(o, h, s)
        res[nombre] = {"residuo": r, "n_pixeles": n_r,
                       "direccion_dab_i": None if dl is None else dl.tolist(),
                       "angulo_al_congelado": None if dl is None else C.angulo(dl, d),
                       "angulo_plano": None if nl is None else C.angulo(nl, M[2]),
                       "n_pixeles_i": n_dl}
    umbral = C.umbral_residuo({n: res[n]["residuo"] for n in TANDA})
    for nombre in laminas_residuo:
        o, s = px[nombre]
        res[nombre].update(C.evalua_residuo(res[nombre]["residuo"], umbral, h, o, s))
    cd = C.desmezcla(C._filtra(od_dab, C.ODSUM_MIN_DAB, C.OD_MAX_DAB), M)[:, 1]
    vect = {"H": h.tolist(), "DAB": d.tolist(), "tercero": M[2].tolist(),
            "angulo_H_DAB": C.angulo(h, d), "n_pixeles_H": n_h, "n_pixeles_DAB": n_d,
            "origen": {"H": "pixeles nucleares (InstanSeg) de P-HER2NEG y P-HER2",
                       "DAB": "P-KI67, P-SYN y P-CK19 sin OD > 1 (Macenko anclado en H)",
                       "tercero": "H x DAB"},
            "estimador_dab": {"percentil": C.PERCENTIL_DAB, "odsum_min": C.ODSUM_MIN_DAB,
                              "od_max": C.OD_MAX_DAB,
                              "sesgo_si_contratincion_citoplasmica_grados":
                                  C.sesgo_dab_contratincion(h, d, cd, semilla=semilla),
                              "nota": ("sin píxeles de DAB puro el ángulo se sesga hacia H: "
                                       "ángulo que daría este estimador, sin ruido, si todo el "
                                       "citoplasma DAB+ llevara esa H constante")}}
    return M, vect, {"por_lamina": res, "umbral": umbral, "factor": C.RESIDUO_FACTOR,
                     "laminas_umbral": list(TANDA), "laminas": list(laminas_residuo)}


def _valida_revision(revision):
    if revision.get("regla") not in REGLAS_ALTO:
        raise ValueError("regla escrita obligatoria: %s" % ", ".join(REGLAS_ALTO))
    if not revision.get("vectores_e_i0_revisados"):
        raise ValueError("antes de la regla se revisan vectores e I0 (vectores_e_i0_revisados)")
    return _limpio(revision)


def _revision_para(dir_salida, pre, revision, anterior=None):
    """La rama > 0,25 se revisa UNA vez (el tribunal ve la galería una sola vez) y SOLO en esa
    rama: una revisión nueva exige el pendiente verificado de esta misma pre-congelación (y del
    mismo sello anterior, si es una recongelación). Devuelve (revisión, pendiente o None); no
    escribe nada: la revisión queda registrada al sellar."""
    previa = ((anterior.d.get("umbral") or {}).get("revision_alto")) if anterior else None
    if revision is None:
        return previa, None
    revision = _valida_revision(revision)
    if previa is not None:
        if previa != revision:
            raise RuntimeError("la rama > 0,25 ya se revisó una vez con otra regla: no se repite")
        return previa, None
    ruta = os.path.join(dir_salida, PENDIENTE_ALTO)
    if not os.path.exists(ruta):
        raise ValueError("revisión sin rama > 0,25: no hay %s (la exclusión por regla escrita "
                         "solo existe en esa rama)" % PENDIENTE_ALTO)
    pend = _lee_verificado(ruta, "revision-alto-pendiente")
    if pend.get("precongelacion_sha256") != pre["sha256"]:
        raise ValueError("el pendiente es de otra pre-congelación: congela otra vez sin revisión")
    if pend.get("anterior_sha256") != (anterior.sha256 if anterior is not None else None):
        raise ValueError("el pendiente es de otra congelación (sello anterior distinto)")
    if not pend.get("alto"):
        raise ValueError("el pendiente no tiene ningún T > 0,25")
    return revision, pend


def _huerfanos(dir_salida):
    return sorted(f for f in os.listdir(dir_salida)
                  if f.startswith("congelacion.anterior-") and f.endswith(".json"))


def congela(lector, dir_salida, fecha, trazas_memoria, semilla=SEMILLA, revision_alto=None,
            anterior=None, causa=None, px_kw=None, laminas_residuo=None, modulo_b=None):
    """(0). Devuelve el `Sello` escrito, o {'estado': 'revision_alto_pendiente', …} sin sello.
    `trazas_memoria` = {procesador: ruta de la traza de guarda_memoria}. `anterior` (Sello) y
    `causa`: solo desde `recongela`."""
    if not _RE_FECHA.match(str(fecha or "")):
        raise ValueError("fecha AAAA-MM-DD obligatoria (la da quien congela)")
    trazas = _trazas(trazas_memoria)
    ruta_sello = os.path.join(dir_salida, FICHERO_SELLO)
    if os.path.exists(ruta_sello):
        raise RuntimeError("ya hay sello: solo cabe `recongela` (una vez, por error de ejecución)")
    if anterior is None and _huerfanos(dir_salida):
        raise RuntimeError("hay un sello archivado sin sello vigente (%s): se restaura, no se "
                           "congela de cero encima" % ", ".join(_huerfanos(dir_salida)))
    pre = _lee_verificado(os.path.join(dir_salida, PRECONGELACION), "precongelacion")
    if not pre["pasa"]:
        raise RuntimeError("la pre-congelación no pasa: %s" % pre["motivos_no_pasa"])
    distintos = S.diferencias(pre.get("parametros"), parametros())
    ajenos = [p for p in distintos
              if not any(p == a or p.startswith(a + ".") for a in PARAMS_SOLO_CONGELACION)]
    if ajenos:
        raise RuntimeError("la pre-congelación se hizo con otros parámetros (%s): repetirla"
                           % ", ".join(ajenos[:8]))
    modulo_b = modulo_b if modulo_b is not None else params_modulo_b()
    revision, pendiente = _revision_para(dir_salida, pre, revision_alto, anterior)
    px_kw = px_kw or {}
    laminas_residuo = list(laminas_residuo or IHQ_CON_TEJIDO)
    if set(TANDA) - set(laminas_residuo):
        raise ValueError("el residuo necesita las cinco de tanda")
    objs = {n: _carga_objetos(dir_salida, pre, n) for n in SUELO}
    M, vect, residuo = _vectores_y_residuo(lector, objs, semilla, laminas_residuo, px_kw)
    # saturados no H/DAB (regla v2) con los vectores de tanda, y el recorte anotado: segunda
    # pasada SOLO sobre las de suelo
    recorte_suelo = {}
    for n in SUELO:
        lam = lector.abre(n)
        pol = S.desempaqueta(objs[n]["pol_xy"], objs[n]["pol_offs"])
        o2 = pasada_objetos(lector, lam, pol, lector.i0_local(lam), M=M, muestra_h=False,
                            nombre=n)
        objs[n]["saturado"] = o2["saturado"]
        recorte_suelo[n] = {"n_objetos": int(len(pol)),
                            "saturado_no_hdab": int(np.sum(o2["saturado"])),
                            "con_recorte_nucleo": int(np.sum(o2["frac_recorte_nucleo"] > 0)),
                            "con_recorte_anillo": int(np.sum(o2["frac_recorte_anillo"] > 0))}
    gq = bool(pre["grandqc"]["en_uso"])
    t = calcula_T(objs["P-HER2NEG"], objs["P-HER2"], M, gq, semilla, revision,
                  mpp_pos=pre["laminas"]["P-HER2"]["mpp_l0"])
    if t["alto"] and revision is None:
        pend = {"tipo": "revision-alto-pendiente", "T": t["regimenes"], "rige": t["rige"],
                "alto": t["alto"], "galeria_her2neg": t["galeria_por_regimen"],
                "precongelacion_sha256": pre["sha256"],
                "anterior_sha256": anterior.sha256 if anterior is not None else None,
                "causa": causa,
                "instrucciones": ("revisar vectores e I0; el tribunal ve esta galería UNA vez; "
                                  "regla escrita (pigmento, pliegue, borde) y otra vez «%s» con "
                                  "--revision-alto" % ("recongela" if anterior is not None
                                                       else "congela"))}
        _escribe_json(os.path.join(dir_salida, PENDIENTE_ALTO), pend)
        return {"estado": "revision_alto_pendiente", "alto": t["alto"], "T": t["regimenes"]}
    por_reg = _por_regimen(t)
    rige = por_reg[t["rige"]]
    neg = objs["P-HER2NEG"]
    for nombre, r in residuo["por_lamina"].items():
        if r.get("supera") and r.get("dab_propio"):
            # (iii) T recalculado en HER2NEG con (H de tanda, DAB propio), en cada régimen
            Mp = C.matriz(vect["H"], r["dab_propio"])
            pr = {}
            for reg, ex in t["exclusion_her2neg"].items():
                pr[reg] = {}
                for comp in COMPS:
                    u = C.umbral_T(C.dab_de(neg["od_" + comp], Mp), neg["fragmento"], ex,
                                   semilla=semilla)
                    u["banda_obligatoria"] = u["estado"] == "alto"
                    if u["banda_obligatoria"]:
                        u["rotulos"] = list(u["rotulos"]) + [C.ROTULOS["high_floor"]]
                    pr[reg][comp] = u
            r["T_propio"] = pr[t["rige"]]
            r["T_propio_por_regimen"] = pr
        if nombre in SUELO:
            mh = objs[nombre]["muestra_h"]
            r["contratincion"] = C.contratincion(mh[C.desmezcla(mh, M)[:, 1] < C.T_SUELO],
                                                 vect["H"])
        else:
            r["contratincion"] = "tras el sello: núcleos DAB-negativos de la lámina (iv), anexo"
    metodos = [METODOS["fijado"], METODOS["suelo"], METODOS["hscore"], METODOS["ck19"],
               METODOS["hotspot"]]
    if not gq:
        metodos.append(S.GRANDQC["rotulo"])
    if anterior is not None:
        metodos.append(METODOS_RECONGELA[causa])
    contenido = {
        "laminas_tanda": list(TANDA), "suelo": list(SUELO),
        "laminas_residuo": laminas_residuo,
        "procedencia": {n: _entrada_manifiesto(lector, n).get("sha256") for n in laminas_residuo},
        "precongelacion": {"sha256": pre["sha256"],
                           "objetos_sha256": {n: pre["laminas"][n]["objetos"]["sha256"]
                                              for n in SUELO},
                           "pesos_instanseg": {n: pre["laminas"][n]["segmentacion"].get("pesos")
                                               for n in SUELO},
                           "declaraciones": pre.get("declaraciones", []),
                           # parámetros que la pre-congelación no usa y difieren de los suyos
                           "parametros_distintos": distintos},
        "vectores": vect,
        "residuo": residuo,
        "umbral": {"regimenes": t["regimenes"], "rige": t["rige"],
                   "T": rige["T"], "banda": rige["banda"],
                   "banda_obligatoria": rige["banda_obligatoria"], "rotulos": rige["rotulos"],
                   "por_regimen": por_reg, "alto": t["alto"],
                   "formula": "max(p99.9 HER2NEG + 0.05; 0.10 OD)",
                   "compartimentos": {"nucleo": "DAB medio en la mascara nuclear",
                                      "anillo": "DAB medio en el anillo de %g um" % C.ANILLO_UM},
                   "excluidos": ["artefacto (regimen)", "tercil bajo de foco",
                                 "50 um del borde de fragmento (mascara provisional)",
                                 "saturados no H/DAB (regla v%d: %s)"
                                 % (C.REGLA_SATURADO["version"],
                                    C.REGLA_SATURADO["artefacto"]),
                                 "fuera de fragmentos >= 0.2 mm2"],
                   "revision_alto": revision,
                   "revision_pendiente_sha256": pendiente["sha256"] if pendiente else None},
        "fp_her2": t["fp_her2"],
        "fp_her2_regimenes": t["fp_her2_regimenes"],
        "galeria_her2neg": t["galeria_her2neg"],
        "galeria_her2neg_por_regimen": t["galeria_por_regimen"],
        "grandqc": pre["grandqc"],
        "artefactos": {"tercil_foco": "cociente de energia del laplaciano L0 vs L0/2",
                       "foco_tejido_min": S.ARTEFACTO["foco_tejido_min"],
                       "saturados": {"version": C.REGLA_SATURADO["version"],
                                     "regla": C.REGLA_SATURADO["artefacto"],
                                     "hsv_min": C.SAT_HSV_MIN,
                                     "fuera_plano_min": C.FUERA_PLANO_MIN,
                                     "odsum_min": C.ODSUM_MIN_COLOR,
                                     "recorte": {"regla": C.REGLA_SATURADO["recorte"],
                                                 "intensidad_max": C.SAT_INTENSIDAD,
                                                 "es_artefacto": False,
                                                 "rotulo": C.ROTULOS["dab_recortado"],
                                                 "suelo": recorte_suelo}},
                       "pliegue": {"percentil": S.ARTEFACTO["pliegue_percentil"],
                                   "ancho_um": S.ARTEFACTO["pliegue_ancho_um"],
                                   "mpp": S.ARTEFACTO["pliegue_mpp"],
                                   "limite": S.LIMITE_PLIEGUE,
                                   "candidatos_grandes": {
                                       n: pre["laminas"][n]["artefactos_clasicos"].get(
                                           "candidatos_pliegue_grande") for n in SUELO}},
                       "borde_um": S.ARTEFACTO["borde_um"]},
        "mascara_provisional": dict(S.MASCARA_PROV, fragmento_min_mm2=S.FRAGMENTO_MIN_MM2),
        "segmentacion": {"instanseg": S.INSTANSEG, "tesela": S.TESELA,
                         "criterio_teselas": S.CRITERIO_TESELAS, "detector_b": S.DETECTOR_B},
        "registro": REGISTRO,
        "hotspot": HOTSPOT,
        "regla_L": REGLA_L,
        "modulo_b": modulo_b,
        "medida": {
            "hscore": {"cortes": ["T", C.HSCORE_CORTES[0], C.HSCORE_CORTES[1]],
                       "rotulo": C.ROTULOS["hscore"]},
            "ck19": dict(C.CK19, regla="valle <= 0.5x pico menor; si no, Otsu en tejido",
                         rotulos_posibles=[C.ROTULOS["ck19_no_bimodal"],
                                           C.ROTULOS["denominador"]],
                         pertenencia="centroide nuclear", nunca="el T de HER2NEG",
                         cierre_interpretado="radio del disco"),
            "banda": {"formula": "[max(T - 0.05; p99.9 + 0.02); T + 0.05]"},
            "contratincion": dict(CONTRATINCION, angulo_max=C.ANGULO_CONTRATINCION),
            "semilla_galeria_focal": semilla + 200},
        "memoria": {"trazas": trazas,
                    "traza_pico_gb": {p: v["pico_gb"] for p, v in trazas.items()},
                    "umbral_gb": {p: float(v["pico_gb"]) + MEMORIA_MARGEN_GB
                                  for p, v in trazas.items()}},
        "semillas": {"maestra": semilla, "bootstrap": semilla, "pixeles": semilla + 100,
                     "galeria_focal": semilla + 200},
        "parametros_color": {"T_suelo": C.T_SUELO, "T_margen": C.T_MARGEN, "T_alto": C.T_ALTO,
                             "anillo_um": C.ANILLO_UM, "od_max_dab": C.OD_MAX_DAB,
                             "sel_residuo": C.SEL_RESIDUO,
                             "angulo_contratincion": C.ANGULO_CONTRATINCION,
                             "ruifrok_provisional": [C.RUIFROK_H, C.RUIFROK_DAB],
                             "medida": MEDIDA},
        "parametros": parametros(),
        "metodos": metodos,
        "declaraciones": DECLARACIONES,
    }
    s = SL.sella(ruta_sello, _limpio(contenido), fecha, anterior=anterior, causa=causa)
    if pendiente is not None:
        # la revisión queda gastada AL sellar (un error antes no la consume)
        _escribe_json(os.path.join(dir_salida, REVISION_ALTO),
                      {"tipo": "revision-alto", "revision": revision, "sello_sha256": s.sha256,
                       "pendiente_sha256": pendiente["sha256"]})
    return s


def recongela(lector, dir_salida, fecha, causa, trazas_memoria, **kw):
    """Solo por error de ejecución (lector, I0, configuración del segmentador o regla_artefacto:
    actualización 19 del plan), UNA vez, sobre las cinco de tanda y sin ninguna medida de célula
    diana (`congela` no lee ninguna). regla_artefacto exige que la regla de artefacto sellada no
    sea la del código (si no, no hay arreglo). Si acaba en la rama > 0,25 (pendiente), el sello
    anterior VUELVE a regir y la revisión va por `recongela`."""
    if causa not in CAUSAS_RECONGELA:
        raise ValueError("recongelación solo por error de ejecución (%s); otra causa va a "
                         "incertidumbres" % ", ".join(CAUSAS_RECONGELA))
    ruta = os.path.join(dir_salida, FICHERO_SELLO)
    previo = verifica_sello(ruta)
    if previo.anterior is not None:
        raise SL.SelloInvalido("ya se recongeló una vez: no se recongela más")
    if causa == "regla_artefacto":
        sellada = ((previo.d.get("parametros") or {}).get("color") or {}).get("REGLA_SATURADO")
        if sellada == C.parametros()["REGLA_SATURADO"]:
            raise ValueError("causa regla_artefacto, pero la regla de artefacto sellada es la del "
                             "código (v%s): no hay arreglo que recongelar"
                             % C.REGLA_SATURADO["version"])
    archivo = os.path.join(dir_salida, "congelacion.anterior-%s.json" % previo.sha256[:12])
    os.replace(ruta, archivo)
    try:
        r = congela(lector, dir_salida, fecha, trazas_memoria, anterior=previo, causa=causa,
                    **kw)
    except Exception:
        if not os.path.exists(ruta):
            os.replace(archivo, ruta)       # sin sello nuevo, el anterior sigue rigiendo
        raise
    if not isinstance(r, SL.Sello):
        os.replace(archivo, ruta)           # pendiente: el anterior sigue rigiendo
        return r
    ext = os.path.join(dir_salida, EXTENSION)
    if os.path.exists(ext):                 # el anexo era del sello anterior
        os.replace(ext, os.path.join(dir_salida, "extension.anterior-%s.json"
                                     % previo.sha256[:12]))
    return r


# ── tras el sello ─────────────────────────────────────────────────────────────────────────────
def residuo_de(sello, nombre):
    """(i)-(iii) SELLADOS de una lámina (las 11 se calculan en (0)). Una lámina fuera del sello no
    se mide con los vectores de tanda por defecto: SelloInvalido."""
    r = ((sello.contenido.get("residuo") or {}).get("por_lamina") or {}).get(nombre)
    if not isinstance(r, dict):
        raise SL.SelloInvalido("%s no tiene residuo (i)-(iii) en el sello %s…: no se mide"
                               % (nombre, sello.sha256[:12]))
    return r


def matriz_de_lamina(sello, nombre):
    """Vectores con los que se mide `nombre`: los de tanda o, si superó el residuo, H de tanda con
    su DAB propio («slide-specific DAB vector»)."""
    c = sello.contenido
    r = residuo_de(sello, nombre)
    if r.get("supera") and r.get("dab_propio"):
        return C.matriz(c["vectores"]["H"], r["dab_propio"]), list(r.get("rotulos", []))
    return C.matriz(c["vectores"]["H"], c["vectores"]["DAB"]), []


def _lee_extension(dir_salida, sello):
    ruta = os.path.join(dir_salida, EXTENSION)
    if not os.path.exists(ruta):
        return {"tipo": "extension-sello", "sello_sha256": sello.sha256, "contratincion": {}}
    d = _lee_verificado(ruta, "extension-sello")
    if d.get("sello_sha256") != sello.sha256:
        raise SL.SelloInvalido("el anexo de extensión es de otro sello (%s…)"
                               % str(d.get("sello_sha256"))[:12])
    return d


def _anota_extension(dir_salida, sello, seccion, nombre, valor):
    """Añade una entrada al anexo sellado; una vez anotada, fija (no se reescribe)."""
    import fcntl
    os.makedirs(dir_salida, mode=0o700, exist_ok=True)
    with open(os.path.join(dir_salida, EXTENSION + ".lock"), "w") as cerrojo:
        fcntl.flock(cerrojo, fcntl.LOCK_EX)
        d = _lee_extension(dir_salida, sello)
        if nombre in d.setdefault(seccion, {}):
            raise RuntimeError("%s ya está en el anexo (%s): una vez medido, fijo" % (nombre,
                                                                                  seccion))
        d[seccion][nombre] = _limpio(valor)
        return _escribe_json(os.path.join(dir_salida, EXTENSION), d)


def contratincion_lamina(lector, nombre, ruta_sello, poligonos):
    """(iv) tras el sello: dirección H en los núcleos DAB-negativos de la lámina frente a la de
    tanda; > 10° (el ángulo SELLADO) → «counterstain differs» y fuera del mapa. DAB-negativo =
    DAB medio del núcleo bajo el T de ESA lámina (el propio si lleva DAB propio), con sus
    vectores; con < 50 núcleos así, el decil de menor DAB (CONTRATINCION, sellado). El resultado
    queda en el anexo sellado; si ya estaba, se devuelve el anotado."""
    s = _sello_para_medir(ruta_sello, nombre)
    if s is None:
        raise SinSello("(iv) se mide tras el sello")
    if nombre in SUELO:
        return dict(residuo_de(s, nombre)["contratincion"], ya_anotado=True)
    dir_salida = os.path.dirname(os.path.abspath(ruta_sello))
    previo = _lee_extension(dir_salida, s).get("contratincion", {}).get(nombre)
    if previo is not None:
        return dict(previo, ya_anotado=True)
    registra_lectura_diana(ruta_sello, nombre, "contratincion")
    p = s.contenido["parametros"]
    pc = p["congela"]["CONTRATINCION"]
    M, _ = matriz_de_lamina(s, nombre)
    lam = lector.abre(nombre)
    o = pasada_objetos(lector, lam, list(poligonos), lector.i0_local(lam), muestra_h=False,
                       ruta_sello=ruta_sello, nombre=nombre)
    od = o["od_nucleo"][np.isfinite(o["od_nucleo"]).all(axis=1)]
    dab = C.desmezcla(od, M)[:, 1]
    T = float(s.umbral(nombre, "nucleo")["T"])
    sel, via = dab < T, "DAB medio < T"
    if sel.sum() < pc["min_negativos"]:
        sel = dab <= np.percentile(dab, pc["decil"])
        via = pc["respaldo"] + " (menos de %d núcleos bajo T)" % pc["min_negativos"]
    r = C.contratincion(od[sel], s.contenido["vectores"]["H"], minimo=pc["minimo_ajuste"],
                        angulo_max=p["color"]["ANGULO_CONTRATINCION"])
    r.update(n_nucleos=int(sel.sum()), seleccion=via, T=T, sello_sha256=s.sha256)
    _anota_extension(dir_salida, s, "contratincion", nombre, r)
    return r


def fuera_del_mapa(dir_salida):
    """Láminas que salen del mapa por (iv) «counterstain differs» (las de suelo, del sello; el
    resto, del anexo) y las que aún no se han evaluado. Lo consume el mapa de vecindad."""
    s = verifica_sello(os.path.join(dir_salida, FICHERO_SELLO))
    ext = _lee_extension(dir_salida, s).get("contratincion", {})
    fuera, pendientes = {}, []
    for n in s.contenido.get("laminas_residuo") or IHQ_CON_TEJIDO:
        r = (residuo_de(s, n).get("contratincion") if n in SUELO else ext.get(n))
        if not isinstance(r, dict):
            pendientes.append(n)
        elif r.get("fuera_mapa"):
            fuera[n] = list(r.get("rotulos") or [])
    return {"fuera": fuera, "sin_evaluar": pendientes, "sello_sha256": s.sha256}


def mascara_ck19(lector, ruta_sello, poligonos_ck19, nombre="P-CK19", bloque=2048):
    """Máscara CK19+ con la regla SELLADA (el VALOR se mide tras el sello): DAB OD a 1 µm/px con
    los vectores de tanda, σ 2 µm, umbral por valle u Otsu, cierre, < 200 µm² fuera; pertenencia
    por centroide; sensibilidad ×0,75/×1,25 por fragmento. Tejido = máscara provisional
    (núcleos)."""
    sello = _sello_para_medir(ruta_sello, nombre)
    if sello is None:
        raise SinSello("la máscara CK19 se mide tras el sello")
    registra_lectura_diana(ruta_sello, nombre, "mascara_ck19")
    p = dict(sello.contenido["parametros"]["color"]["CK19"])
    lam = lector.abre(nombre)
    zona = lector.zona_escaneada(lam)
    i0 = lector.i0_local(lam)
    M, rot = matriz_de_lamina(sello, nombre)
    mpp_l0 = float(lam.mpp_l0)
    m = p["mpp"]
    f = m / mpp_l0
    W, H = _dims(lam)
    minx, miny, maxx, maxy = zona.bounds
    w_t, h_t = int((min(maxx, W) - minx) / f), int((min(maxy, H) - miny) / f)
    dab = np.zeros((h_t, w_t), np.float32)
    for oy in range(0, h_t, bloque):
        for ox in range(0, w_t, bloque):
            w, h = min(bloque, w_t - ox), min(bloque, h_t - oy)
            x, y = minx + ox * f, miny + oy * f
            rgb = _lee(lector, lam, m, x, y, w, h)
            o = C.od(rgb, C.i0_region(i0, x, y, w, h, m, mpp_l0))
            dab[oy:oy + h, ox:ox + w] = C.desmezcla(o, M)[..., 1]
    cents = S.centroides(poligonos_ck19)
    prov = S.mascara_provisional(cents, mpp_l0, (W, H))
    tejido = _rejilla_a(prov, (minx, miny), f, (h_t, w_t))
    mask, suave, info = C.mascara_ck19(dab, tejido, m, p)
    xy = (cents - np.array([minx, miny])) / f
    sens = C.sensibilidad_ck19(suave, tejido, info["T"], xy, S.fragmento_de(prov, cents), m, p)
    return {"mascara": mask, "origen_l0": (minx, miny), "mpp": m, "umbral": info,
            "dentro": C.dentro(mask, xy), "sensibilidad": sens,
            "rotulos": rot + info["rotulos"] + sens["rotulos"] + [C.ROTULOS["ck19_sin_verdad"]]}


# ── CLI (solo por la ventanilla) ──────────────────────────────────────────────────────────────
def _traza(texto):
    """«proc=ruta,proc=ruta» → {proc: ruta}; las rutas, relativas a SESION (cwd)."""
    out = {}
    for par in (texto or "").split(","):
        if par.strip():
            k, v = par.split("=", 1)
            out[k.strip()] = os.path.abspath(v.strip())
    return out


def dir_sesion():
    """Carpeta de la congelación por la ventanilla: SESION misma (cwd). El sello vive en la ruta
    canónica ÚNICA de `laminillas_sello` (`SESION/congelacion.json`, la única que `exige` acepta
    por la ventanilla y la que lee `laminillas_proc.ruta_sello`); su pre-congelación, el anterior
    archivado y el anexo (iv) van a su lado, que es donde los buscan `laminillas_sello` y
    `fuera_del_mapa`."""
    import laminillas_comun as K
    return K.sesion()


def main(argv, lector=None, **kw):
    """CLI por la ventanilla. `lector` y `kw` (segmentador, px_kw, modulo_b, diagnostico,
    regla_mpp) solo para los tests:
    por defecto, el lector único y el segmentador de la pre-congelación."""
    import argparse
    dir_salida = dir_sesion()
    ap = argparse.ArgumentParser(prog="laminillas_congela")
    ap.add_argument("orden", choices=("precongela", "congela", "recongela", "verifica"))
    ap.add_argument("--fecha")
    ap.add_argument("--traza", default="")
    ap.add_argument("--causa")
    ap.add_argument("--revision-alto")
    ap.add_argument("--vectores-i0-revisados", action="store_true")
    ap.add_argument("--para-recongelar", action="store_true")
    ap.add_argument("--diagnostico-area", action="store_true")
    ap.add_argument("--diagnostico-mpp", action="store_true")
    ap.add_argument("--lamina", choices=SUELO, default="P-HER2NEG")
    a = ap.parse_args(argv)
    if (a.diagnostico_area or a.diagnostico_mpp) and a.orden != "precongela":
        ap.error("--diagnostico-area y --diagnostico-mpp van con `precongela`")
    if a.diagnostico_area and a.diagnostico_mpp:
        ap.error("un diagnóstico por corrida")
    if a.orden == "verifica":
        s = verifica_sello(os.path.join(dir_salida, FICHERO_SELLO))
        print("sello %s · fecha %s · T %s · recongelado %s" % (
            s.sha256[:16], s.fecha, s.contenido["umbral"]["T"], s.anterior is not None))
        return 0
    if lector is None:
        import laminillas_lector as lector                     # el lector único (ingesta)
    L = lector
    if a.diagnostico_area:
        r = diagnostico_area(L, nombre=a.lamina, diagnostico=kw.get("diagnostico"))
        print("diagnóstico de área " + json.dumps(_limpio(r), ensure_ascii=False, sort_keys=True))
        return CODIGO_DIAGNOSTICO
    if a.diagnostico_mpp:
        r = regla_mpp(L, regla=kw.get("regla_mpp"))
        print("regla del mpp " + json.dumps(_limpio(r), ensure_ascii=False, sort_keys=True))
        return CODIGO_DIAGNOSTICO
    if a.orden == "precongela":
        r = precongela(L, dir_salida, segmentador=kw.get("segmentador"),
                       para_recongelar=a.para_recongelar)
        print("precongelación %s · pasa=%s · %s" % (r["sha256"][:16], r["pasa"],
                                                     r["motivos_no_pasa"]))
        return 0 if r["pasa"] else 1
    rev = None
    if a.revision_alto:
        regla, _, par = a.revision_alto.partition(":")
        clave = {"pigmento": "fuera_plano_min", "borde": "um", "pliegue": "percentil"}[regla]
        rev = {"regla": regla, "parametros": {clave: float(par)} if par else {},
               "vectores_e_i0_revisados": a.vectores_i0_revisados}
    extra = {k: kw[k] for k in ("px_kw", "modulo_b") if kw.get(k) is not None}
    if a.orden == "congela":
        r = congela(L, dir_salida, a.fecha, _traza(a.traza), revision_alto=rev, **extra)
    else:
        r = recongela(L, dir_salida, a.fecha, a.causa, _traza(a.traza), revision_alto=rev,
                      **extra)
    if isinstance(r, dict):
        print(json.dumps(r, ensure_ascii=False, default=str)[:2000])
        return 3
    print("sello %s · fecha %s" % (r.sha256[:16], r.fecha))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
