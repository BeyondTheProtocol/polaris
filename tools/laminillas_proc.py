#!/usr/bin/env python3
"""tools/laminillas_proc.py — procesadores de laminillas con datos.

Lo lanza SOLO la ventanilla (`lector_clinico.py procesa laminillas_<modo>`), dentro de su jaula
(`analisis.sb`, `analisis.sb` de ingesta o `visor.sb`), con el cwd en SESION y el entorno en lista
blanca. El primer argumento (`modo`) lo pone la ventanilla, no quien llama.

Modos: `ingesta` y `qc` (F1.1-F1.3, en `laminillas_ingesta.py`), `registro prueba-vips` (prueba
(c) del lector), `segmenta` (punto 4 (i)/(i-bis): InstanSeg con la configuración sellada, por
lámina) y `analisis`: la PUERTA DEL PILOTO tras la congelación (0). En `analisis` el primer
argumento de quien llama es la ORDEN (lista cerrada `ORDENES`, que la ventanilla valida):

  lector_clinico.py procesa laminillas -- piloto-ibis [P-CK19] [P-HE]
  lector_clinico.py procesa laminillas -- registro-par P-KI67 P-CK19 [--valis]
  lector_clinico.py procesa laminillas -- registro-par P-HER2NEG P-CK19      (y P-HER2, P-HE…)
  lector_clinico.py procesa laminillas -- registro-par [MÓVIL P-CK19] --diagnostico   (solo números)
  lector_clinico.py procesa laminillas -- piloto-i [P-KI67]
  lector_clinico.py procesa laminillas -- metricas <LÁMINA>
  lector_clinico.py procesa laminillas -- geojson <LÁMINA>
  lector_clinico.py procesa laminillas -- lista-postcongelacion
  lector_clinico.py procesa laminillas -- tribunal-listo

ORDEN DE LAS DEPENDENCIAS (cada orden dice qué le falta, con código 5, y no mide nada a medias):
  1. `piloto-ibis`: InstanSeg sellado en P-CK19 y P-HE; en P-CK19, además, sus objetos (DAB por
     núcleo), la máscara CK19+ con la regla sellada y la regla morfométrica (ver abajo).
  2. `registro-par P-KI67 P-CK19` (ii): registro del módulo B (las dos quiralidades, puerta por
     FC, TRE (b)/(b')); segmenta P-KI67 si aún no lo está. ≥50 % del área de consenso verificada
     → se extiende; <50 % → ESCALERA SIN REGISTRO (`piloto/registro/escalera.json`): zona
     escaneada, % por lámina con denominador morfométrico, heterogeneidad por fragmento y
     GeoJSON; caen (a), (a-bis) y el denominador CK19, y el registro vuelve al tribunal una sola
     vez, con VALIS (`--valis`).
     (ii-bis) `registro-par P-HER2NEG P-CK19` y `P-HER2 P-CK19`: mismo método y puerta sobre el FC
     del piloto, triángulo de cierre KI67→CK19→HER2NEG y, en P-HER2, el control positivo del método
     HER2NEG→CK19→HER2 (fragmentos casi idénticos). (iii) `registro-par P-CK19 P-HE` (o `P-KI67
     P-HE`, la misma orden): CK19↔HE y KI67↔HE por fragmento (`registra_he`). Cualquier otra IHQ
     con tejido ↔ P-CK19 (extensión F3) se registra como (ii-bis), sobre el FC del piloto.
  3. `piloto-i` (P-KI67): QC, I0 local, InstanSeg sellado (con la rejilla desplazada y, si rige
     GrandQC, GrandQC), objetos, % global y hotspot, GeoJSON y capa del visor.
  4. `metricas L` y `geojson L`: CUALQUIER lámina con objetos; si no los tiene, los calcula (la
     segmentación la pide: `procesa laminillas_segmenta -- L`). Sirven sin cambios para la
     extensión (primera ola P-SYN, P-{{DIANA3}}, P-CHGA: simetría NE de `laminillas_metricas`).
  5. `lista-postcongelacion`: el punto 5 del plan, ítem a ítem en su orden y con su criterio de
     aceptación; al primer ítem que NO PASA se para (los siguientes, «no evaluado») y lo dice.
     Informe en `SESION/piloto/lista_postcongelacion.json`.
  6. `tribunal-listo`: requisito BLOQUEANTE 5-bis, por código (`tribunal_listo`).

CÓDIGOS: 0 hecho; 2 uso; 3 sello ausente o inválido (NO MIDO); 4 no pasa (lista post-congelación,
tribunal, regla de L, regla morfométrica sin clases, vuelta con VALIS ya gastada); 5 falta un
producto previo (dice cuál); 6 error de ejecución: `--valis` sin que VALIS corriera (no gasta la
vuelta).

PRODUCTOS (SESION/piloto/, todo 0600; «hecho» por lámina con el sha256 de lo producido: tras
98/99 se salta lo sellado; lo que depende de otros productos lleva la HUELLA de sus entradas y se
rehace si cambian):
  objetos_<L>.npz/.json   DAB por núcleo (núcleo y anillo, vectores sellados de la lámina),
                          hematoxilina, artefactos (foco, pliegue, saturados, GrandQC), fragmento
                          y borde de la máscara provisional, rasgos morfométricos
  qc_<L>.json             QC de F3 (foco, contacto con el borde escaneado, I0, teselado)
  ck19/mascara.npz/.json  máscara CK19+ (regla sellada), sensibilidad y galería de su borde
  regla_morfometrica.json la regla morfométrica (ajuste y validación por fragmento en P-CK19)
  registro/<MÓVIL>.json   el par contra P-CK19 (resumen de ResultadoRegistro, (ii), cierres)
  registro/fc_piloto.npz  el FC del piloto (KI67∩CK19), en la rejilla ×8 de P-CK19
  registro/P-HE.json      (iii); registro/escalera.json si (ii) <50 %
  L.json                  el valor de L (regla sellada en (0)), tras (ii), en P-KI67
  metricas/<L>.json       % global con IC y rango, H-score, heterogeneidad, señal, hotspot…
  geojson/<L>.geojson     GeoJSON para QuPath (laminillas_geojson, validado)
  visor/<L>.capa.json     capa del visor local (centroides por clase, px L0)
  regimen.json            régimen de artefactos que rige tras el chequeo de GrandQC (punto 5)
  deteccion.json          recall por clase (punto 5): si no pasa, % como rango
  lista_postcongelacion.json · tribunal_listo.json

INFERENCIAS MÍAS (el plan no da cifra; van a Métodos con `PARAMS` y `DECLARACIONES`):
  · Regla morfométrica: logística (L2) sobre log-área, log-ejes del elipse de mismos momentos,
    elongación, solidez y log-densidad local (núcleos a ≤25 µm), ajustada en P-CK19 con la verdad
    «anillo CK19+ de esa célula» (DAB medio del anillo > T_CK19 de la máscara) y validada dejando
    fuera un fragmento entero; invariante al DAB por construcción. Corte de prevalencia (la
    fracción predicha iguala la observada en el ajuste), no 0,5: con 3,8 % de CK19+ el 0,5 no
    marcaba ninguna célula (piloto real, 2-oct). Regla degenerada = no pasa.
  · Escalera: si ningún L sellado llega a la mediana de 100, rige el máximo con su rótulo.
  · Artefacto por núcleo: reglas clásicas = tercil bajo de foco | pliegue | saturados no H/DAB;
    GrandQC = marcado por GrandQC | saturados.
  · Erosión y dilatación de la máscara CK19 por 1× TRE (p90 del par), sobre una rejilla de 2 µm
    (error ≤2 µm).
  · Escalera y fragmentos no verificados: L por densidad sola (sin TRE, no hay registro).
  · Recall por clase: 40 teselas de 512 px a L0 al azar (semilla sellada) sobre núcleos de
    fragmento; estratos con <20 objetos de una clase, fuera del criterio (se dice).
  · Máscara de tejido: el umbral se mueve ×0,8 y ×1,25; vecindad del FC = 200 µm.
"""
import hashlib
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import laminillas_comun as C  # noqa: E402

MODOS = ("ingesta", "analisis", "registro", "qc", "visor", "segmenta")
ORDENES = ("piloto-i", "piloto-ibis", "registro-par", "lista-postcongelacion", "metricas",
           "geojson", "tribunal-listo")
DIR_SEGMENTA = "segmenta"
DIR_PILOTO = "piloto"
SUELO = ("P-HER2NEG", "P-HER2")
REFERENCIA = "P-CK19"
EXTENSION_IHQ = ("P-RE", "P-RP", "P-RA", "P-SYN", "P-CHGA", "P-{{DIANA3}}", "P-P63")
MARCADOR = {"P-KI67": "Ki67", "P-CK19": "CK19", "P-RE": "ER", "P-RP": "PR", "P-RA": "AR",
            "P-SYN": "synaptophysin", "P-CHGA": "chromogranin A", "P-{{DIANA3}}": "{{DIANA3}}",
            "P-P63": "p63", "P-HER2": "HER2", "P-HER2NEG": "HER2 (second HER2-labelled slide)"}
FUERA_DE_METRICAS = {"P-P63": "p63: its own gate (puerta_p63), not this order",
                     "P-AE1AE3": "AE1/AE3: outside the multimarker analysis",
                     "P-HE": "H&E: no DAB; nuclear classification is another track"}
RC_USO, RC_SELLO, RC_NO_PASA, RC_FALTA, RC_EJECUCION = 2, 3, 4, 5, 6

# Versión de la regla morfométrica: entra en la clave de «hecho» y en el JSON, así que un cambio
# de método obliga a reajustar (v1 = corte fijo 0,5, degenerado en el piloto real del 2-oct).
PASO_REGLA = "regla-morfometrica-v2"
# Versión del CÓDIGO del registro (no de sus parámetros sellados): va en cada producto
# `registro/<móvil>.json` y un «hecho» de otra versión se repite en vez de saltarse (2-oct: el
# arreglo de la traslación del eslabón 1, 995fb22, no se habría aplicado nunca a lo ya hecho).
VERSION_REGISTRO = "v1.1-init-max-iou"


def registro_de_esta_version(producto):
    """¿El producto de registro lo hizo ESTE código? Sin campo (antes del 2-oct) = v1 = no."""
    return (producto or {}).get("version_registro") == VERSION_REGISTRO
PARAMS = dict(densidad_local_radio_um=25.0, regla_ridge=1.0, regla_iter=50,
              regla_corte="prevalence-matching on the fitting data",
              regla_min_fragmento=50, regla_min_clase=10, recall_teselas=40, recall_lado_px=512,
              recall_min_objetos=20, tejido_factores=(0.8, 1.25), tejido_dif_cortes=0.15,
              tejido_dif_umbral=0.10, tejido_vecindad_um=200.0, galeria_ck19=20,
              galeria_ck19_lado_um=64.0, hema_tolerancia=0.20, grandqc_dif_puntos=5.0,
              erosion_rejilla_um=2.0, semilla_regla=11, semilla_recall=13, semilla_galeria=17,
              semilla_acuerdo_area=19)
DECLARACIONES = {
    "regla": ("morphometric rule: L2-regularised logistic model on log area, log axes of the "
              "equal-moment ellipse, elongation, solidity and log local density (nuclei within "
              "25 um), fitted on P-CK19 against the CK19+ ring of each cell, validated leaving one "
              "fragment out; DAB-invariant by construction"),
    "regla_corte": ("morphometric rule cut-off re-specified once after the pilot (a fixed 0.5 was "
                    "degenerate at 3.8 % CK19+ prevalence: no cell called positive) to the "
                    "prevalence-matching cut of the fitting data, refitted within each "
                    "leave-one-fragment-out fold; no target-cell measurement informed it"),
    "artefacto": ("artefact per nucleus: classical rules = low focus tercile, fold or saturated "
                  "non-H/DAB pixels; GrandQC regime = GrandQC-marked or saturated"),
    "erosion": "CK19 mask eroded/dilated by 1x the pair's TRE p90 on a 2 um grid (error <=2 um)",
    "escalera_L": "unregistered analysis: side L chosen by nuclear density only (no TRE)",
    "propio": ("fragments outside the verified registration: own analysis, morphometric "
               "denominator, outside the map"),
    "recall": ("class recall without human ground truth: 40 random 512 px L0 tiles over "
               "fragment nuclei (sealed seed); strata with fewer than 20 objects of a class are "
               "outside the criterion"),
    "tejido": "tissue-mask threshold moved x0.8 and x1.25; FC neighbourhood 200 um",
}


class FaltaEntrada(RuntimeError):
    """Falta un producto previo (código 5): el mensaje dice qué orden lo produce."""


class NoPasa(RuntimeError):
    """Un criterio del plan no se cumple (código 4)."""


class ValisNoCorrio(RuntimeError):
    """`registro-par … --valis` sin que VALIS devolviera una transformación (código 6): error de
    EJECUCIÓN, no resultado; la vuelta única con VALIS NO se gasta (`valis_fallos` en la
    escalera dice por qué)."""


# ── utilidades ────────────────────────────────────────────────────────────────────────────────
def ruta_sello(base):
    """El sello de la congelación (0) en su ruta canónica por la ventanilla: `SESION/congelacion.json`
    (`laminillas_sello._comprueba_ruta` rechaza cualquier otra; `laminillas_congela` sella ahí)."""
    import laminillas_sello as SL
    return os.path.join(base, SL.FICHERO)


def _rel(*partes):
    return os.path.join(DIR_PILOTO, *partes)


def _abs(base, rel):
    return os.path.join(base, rel)


def _limpio(o):
    """JSON estricto: numpy → Python, NaN/inf → None, tuplas → listas, claves texto."""
    try:
        import numpy as np
    except ImportError:                                        # pragma: no cover
        np = None
    if isinstance(o, dict):
        return {str(k): _limpio(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_limpio(v) for v in o]
    if np is not None:
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


def _escribe(base, rel, obj):
    os.makedirs(os.path.dirname(_abs(base, rel)), mode=0o700, exist_ok=True)
    C.escribe_json(_abs(base, rel), _limpio(obj))
    return rel


def _lee(base, rel):
    with open(_abs(base, rel), encoding="utf-8") as f:
        return json.load(f)


def _npz(base, rel, **arrays):
    import numpy as np
    ruta = _abs(base, rel)
    os.makedirs(os.path.dirname(ruta), mode=0o700, exist_ok=True)
    tmp = ruta[:-len(".npz")] + ".parcial.npz"
    np.savez_compressed(tmp, **arrays)
    os.replace(tmp, ruta)
    os.chmod(ruta, 0o600)
    return rel


def _carga_npz(base, rel):
    import numpy as np
    with np.load(_abs(base, rel), allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def _huella(base, rels):
    """Huella de las entradas: sha256 de cada fichero (ausente = «-»), en orden fijo."""
    h = hashlib.sha256()
    for rel in sorted(set(rels)):
        p = _abs(base, rel)
        h.update(rel.encode("utf-8") + b"=")
        h.update((C.sha256_fichero(p) if os.path.isfile(p) else "-").encode("ascii") + b";")
    return h.hexdigest()[:16]


def _sello(base, laminas, secciones=None):
    import laminillas_sello as SL
    return SL.exige(ruta_sello(base), list(laminas), secciones or SL.SECCIONES_BASE)


def _lector(lector):
    if lector is None:
        import laminillas_lector as lector
    return lector


def _semilla(sello, desplazamiento):
    return int((sello.d.get("semillas") or {}).get("maestra", 20261001)) + int(desplazamiento)


# ── segmentación (punto 4 (i)/(i-bis)) ───────────────────────────────────────────────────────
def _rige_sellado(sello):
    return None if sello is None else (sello.d.get("umbral") or {}).get("rige")


def acuerdo_area(lector, nombre, pol, semilla):
    """Puerta del área nuclear de la pre-congelación, la MISMA (`S.CRITERIO_AREA`, aplicada por
    `laminillas_congela.acuerdo_area_lamina`): mediana del cociente InstanSeg/detector B en µm² en
    los mismos núcleos (IoU > 0,5 a L0), en 20 ventanas de L0 con tejido; 0,70-1,30 con ≥ 200
    pares. La «mediana 25-80 µm²» del plan queda como referencia, no como puerta."""
    import laminillas_congela as K
    import laminillas_segmenta as S
    lam = lector.abre(nombre)
    return K.acuerdo_area_lamina(lector, lam, lector.zona_escaneada(lam), lector.i0_local(lam),
                                 pol, S.centroides(pol), semilla)


def _area_referencia(pol, mpp_l0):
    """Mediana del área nuclear frente a la «25-80 µm²» del plan: REFERENCIA (la puerta es
    `acuerdo_area`), como en la pre-congelación."""
    import laminillas_segmenta as S
    ref = S.mediana_area(pol, mpp_l0)
    return {"mediana_um2": ref["mediana_um2"], "n": ref["n"],
            "referencia_plan_um2": list(S.COMPROBACION["area_nuclear_um2"]),
            "en_referencia_plan": ref["pasa"], "puerta": "acuerdo_area"}


def segmenta(base, laminas, lector=None, segmentador=None, log=print, con_desplazada=None,
             con_grandqc=None, con_acuerdo_area=None):
    """Punto 4 (i)/(i-bis) del piloto: núcleos de InstanSeg con la configuración SELLADA.

    Pasa por `laminillas_congela.celulas_diana`, la guardia del módulo A: una lámina diana sin el
    sello verificado, completo y con los parámetros de este código no abre ni un píxel
    (SelloAusente/SelloInvalido), y cada lectura diana queda en `lecturas_diana.jsonl`. Por
    defecto, P-KI67 lleva la rejilla desplazada 128 px y la puerta del área nuclear
    (`acuerdo_area`, CRITERIO_AREA): la lista post-congelación repite en ella las comprobaciones
    del punto 2; si el sello hace regir GrandQC, toda lámina lleva GrandQC (sus marcas son el
    artefacto de ese régimen), y las H&E lo llevan SIEMPRE (`_pide_grandqc`); una H&E ya
    segmentada sin él se repite. Por lámina, en `SESION/segmenta/`:
      nucleos_<lámina>.npz   centroide (px L0), area_um2, pol_xy/pol_offs (`S.empaqueta`) y, si
                             corrieron, centroide_desplazada y grandqc (marcado por núcleo)
      nucleos_<lámina>.json  n, mediana de área (referencia), acuerdo_area (si corrió),
                             teselado, dispositivo, lote, aviso de check_input_tile, pesos,
                             mpp_l0, dimensiones, GrandQC y sha256 del sello con el que se midió
    «Hecho» por lámina con el sha256 de los dos: tras 98/99 se salta lo ya sellado."""
    import laminillas_congela as K
    import laminillas_segmenta as S
    import laminillas_sello as SL
    lector = _lector(lector)
    if not laminas:
        raise ValueError("segmenta: falta la lámina (nombre opaco, p. ej. P-KI67)")
    sello = ruta_sello(base)
    dir_out = os.path.join(base, DIR_SEGMENTA)
    os.makedirs(dir_out, mode=0o700, exist_ok=True)
    hechas = []
    for nombre in laminas:
        he = nombre in LAMINAS_HE
        if C.esta_hecho(base, "segmenta", nombre):
            if not (he and con_grandqc is None and not _segmentada_con_grandqc(base, nombre)):
                log("%s: ya segmentada (hecho sellado); salto" % nombre)
                continue
            log("%s: segmentada sin GrandQC; en la H&E rige siempre: se repite" % nombre)
        s = SL.exige(sello, [nombre])               # None: lámina de suelo sin sello
        desp = (nombre == "P-KI67") if con_desplazada is None else bool(con_desplazada)
        gq = _pide_grandqc(nombre, s, con_grandqc)
        acu = (nombre == "P-KI67") if con_acuerdo_area is None else bool(con_acuerdo_area)
        seg = K.celulas_diana(lector, nombre, sello, segmentador=segmentador,
                              con_desplazada=desp, con_grandqc=gq)
        pol = list(seg["celulas"])
        mpp_l0 = float(seg["mpp_l0"])
        cents = S.centroides(pol)
        xy, offs = S.empaqueta(pol)
        arrays = dict(centroide=cents, area_um2=S.areas_um2(pol, mpp_l0), pol_xy=xy,
                      pol_offs=offs)
        sin = []
        if desp and seg.get("celulas_desplazadas") is not None:
            arrays["centroide_desplazada"] = S.centroides(list(seg["celulas_desplazadas"]))
        else:
            sin.append("rejilla desplazada")
        info_gq = None
        if gq:
            g = seg.get("grandqc") or {"carga": False, "motivo": "el segmentador no lo devolvió"}
            meta = {k: g.get(k) for k in ("dispositivo", "intentos", "pesos")}
            if g.get("carga"):
                dims = getattr(lector.abre(nombre), "dimensiones_l0")
                prov = S.mascara_provisional(cents, mpp_l0, dims)
                fr = S.fracciones_grandqc(g["artefactos"], cents, prov)
                arrays["grandqc"] = fr.pop("marcado_nucleo")
                info_gq = dict(fr, carga=True, **meta)
            else:
                info_gq = dict({"carga": False, "motivo": g.get("motivo")}, **meta)
        else:
            sin.append("GrandQC")
        rel_npz = os.path.join(DIR_SEGMENTA, "nucleos_%s.npz" % nombre)
        rel_json = os.path.join(DIR_SEGMENTA, "nucleos_%s.json" % nombre)
        _npz(base, rel_npz, **arrays)
        resumen = {"lamina": nombre, "n": len(pol), "mpp_l0": mpp_l0,
                   "area_nuclear": _area_referencia(pol, mpp_l0),
                   "sello": None if s is None else s.sha256,
                   "parametros_instanseg": S.INSTANSEG, "grandqc": info_gq,
                   "con_desplazada": bool("centroide_desplazada" in arrays), "sin": sin}
        try:
            resumen["dimensiones_l0"] = list(lector.abre(nombre).dimensiones_l0)
        except Exception:                                          # noqa: BLE001
            resumen["dimensiones_l0"] = None
        for k in ("teselado", "dispositivo", "lote", "aviso_check_input_tile", "pesos"):
            resumen[k] = seg.get(k)
        if acu:
            sem = K.SEMILLA if s is None else _semilla(s, PARAMS["semilla_acuerdo_area"])
            resumen["acuerdo_area"] = acuerdo_area(lector, nombre, pol, sem)
            if nombre not in SUELO:
                K.registra_lectura_diana(sello, nombre, "acuerdo_area")
        else:
            sin.append("acuerdo de área")
        C.escribe_json(os.path.join(base, rel_json), K._limpio(resumen))
        C.marca_hecho(base, "segmenta", nombre, productos=[rel_npz, rel_json])
        log("%s: %d núcleos, mediana %.1f µm² (%s)" % (
            nombre, len(pol), resumen["area_nuclear"]["mediana_um2"], seg.get("dispositivo")))
        hechas.append(nombre)
    return hechas


LAMINAS_HE = ("P-HE", "B-HE-1", "B-HE-2")


def _pide_grandqc(nombre, sello, con_grandqc=None):
    """GrandQC: en las IHQ, según el régimen sellado; en las H&E, SIEMPRE (plan, punto 2: «En las
    H&E sigue como estaba»). Hasta el 2-oct, P-HE se segmentaba sin él con «clásicas»."""
    if con_grandqc is not None:
        return bool(con_grandqc)
    return nombre in LAMINAS_HE or _rige_sellado(sello) == "grandqc"


def _segmentada_con_grandqc(base, nombre):
    """¿La segmentación sellada de `nombre` pasó por GrandQC (aunque no cargara: se declara)?"""
    rel = os.path.join(DIR_SEGMENTA, "nucleos_%s.json" % nombre)
    if not os.path.isfile(_abs(base, rel)):
        return False
    return _lee(base, rel).get("grandqc") is not None


def _rels_nucleos(base, nombre):
    """Ficheros de los núcleos de una lámina: los de `segmenta` o, en las de suelo, los objetos
    de la pre-congelación (misma configuración, ya sellados en (0))."""
    seg = [os.path.join(DIR_SEGMENTA, "nucleos_%s.npz" % nombre),
           os.path.join(DIR_SEGMENTA, "nucleos_%s.json" % nombre)]
    if all(os.path.isfile(_abs(base, r)) for r in seg):
        return seg
    if nombre in SUELO and os.path.isfile(_abs(base, "objetos_%s.npz" % nombre)):
        return ["objetos_%s.npz" % nombre, "precongelacion.json"]
    return seg


def nucleos_lamina(base, nombre):
    """Núcleos de una lámina (px L0). FaltaEntrada si no se ha segmentado."""
    import laminillas_congela as K
    seg = [os.path.join(DIR_SEGMENTA, "nucleos_%s.npz" % nombre),
           os.path.join(DIR_SEGMENTA, "nucleos_%s.json" % nombre)]
    if all(os.path.isfile(_abs(base, r)) for r in seg) and C.esta_hecho(base, "segmenta", nombre):
        d = _carga_npz(base, seg[0])
        d["resumen"] = _lee(base, seg[1])
        d["mpp_l0"] = float(d["resumen"]["mpp_l0"])
        d["origen"] = "segmenta"
        return d
    if nombre in SUELO and os.path.isfile(_abs(base, "objetos_%s.npz" % nombre)):
        pre = _lee(base, "precongelacion.json")
        if pre.get("sha256") != K.sha256_de(pre):
            raise FaltaEntrada("precongelacion.json no casa con su sha256")
        ent = pre["laminas"][nombre]
        rel = ent["objetos"]["fichero"]
        if C.sha256_fichero(_abs(base, rel)) != ent["objetos"]["sha256"]:
            raise FaltaEntrada("%s: objetos de la pre-congelación cambiados" % nombre)
        z = _carga_npz(base, rel)
        d = {k: z[k] for k in ("centroide", "area_um2", "pol_xy", "pol_offs") if k in z}
        if "grandqc" in z:
            d["grandqc"] = z["grandqc"]
        d["mpp_l0"] = float(ent["mpp_l0"])
        d["resumen"] = {"lamina": nombre, "n": int(len(z["centroide"])),
                        "mpp_l0": float(ent["mpp_l0"]),
                        "dimensiones_l0": ent.get("dimensiones_l0"),
                        "teselado": (ent.get("segmentacion") or {}).get("teselado"),
                        "origen": "pre-congelación"}
        d["origen"] = "precongelacion"
        return d
    raise FaltaEntrada("%s sin núcleos: corre antes `procesa laminillas_segmenta -- %s` (o "
                       "piloto-i / piloto-ibis)" % (nombre, nombre))


# ── rasgos morfométricos y regla morfométrica ────────────────────────────────────────────────
RASGOS = ("log_area", "log_eje_mayor", "log_eje_menor", "log_elongacion", "solidez",
          "log_densidad_local")


def rasgos_morfologicos(pol_xy, pol_offs, mpp_l0, radio_um=PARAMS["densidad_local_radio_um"]):
    """Rasgos INVARIANTES AL DAB por núcleo: área y ejes del elipse de mismos segundos momentos
    (momentos exactos del polígono por Green), elongación, solidez (área / envolvente convexa) y
    densidad local (núcleos a ≤ radio_um, por mm²). Devuelve (N×6, centroides µm)."""
    import numpy as np
    import shapely
    from scipy.spatial import cKDTree
    xy = np.asarray(pol_xy, np.float64).reshape(-1, 2) * float(mpp_l0)
    offs = np.asarray(pol_offs, np.int64)
    n = len(offs) - 1
    if n <= 0:
        return np.zeros((0, len(RASGOS))), np.zeros((0, 2))
    lens = np.diff(offs)
    poli = np.repeat(np.arange(n), lens)
    i = np.arange(len(xy))
    ult = np.zeros(len(xy), bool)
    ult[offs[1:][lens > 0] - 1] = True                 # último vértice (= primero): sin segmento
    j = np.minimum(i + 1, len(xy) - 1)
    seg = ~ult
    x0, y0, x1, y1 = xy[i, 0], xy[i, 1], xy[j, 0], xy[j, 1]
    cr = np.where(seg, x0 * y1 - x1 * y0, 0.0)

    def suma(w):
        return np.bincount(poli, weights=w, minlength=n)
    A = 0.5 * suma(cr)
    As = np.where(np.abs(A) > 1e-12, A, np.nan)
    cx = suma((x0 + x1) * cr) / (6 * As)
    cy = suma((y0 + y1) * cr) / (6 * As)
    ixx = suma((x0 * x0 + x0 * x1 + x1 * x1) * cr) / 12.0
    iyy = suma((y0 * y0 + y0 * y1 + y1 * y1) * cr) / 12.0
    ixy = suma((x0 * y1 + 2 * x0 * y0 + 2 * x1 * y1 + x1 * y0) * cr) / 24.0
    mu20 = ixx / As - cx * cx
    mu02 = iyy / As - cy * cy
    mu11 = ixy / As - cx * cy
    med = (mu20 + mu02) / 2
    rad = np.sqrt(np.maximum(((mu20 - mu02) / 2) ** 2 + mu11 ** 2, 0))
    l1, l2 = np.maximum(med + rad, 0), np.maximum(med - rad, 0)
    mayor, menor = 4 * np.sqrt(l1), 4 * np.sqrt(l2)
    area = np.abs(A)
    import laminillas_segmenta as S
    pols = np.array(S.desempaqueta(np.asarray(pol_xy, np.float64), offs), dtype=object)
    casco = shapely.area(shapely.convex_hull(pols)) * float(mpp_l0) ** 2 if n else np.zeros(0)
    solidez = np.where(casco > 0, area / np.maximum(casco, 1e-12), 0.0)
    cent = np.column_stack([np.nan_to_num(cx), np.nan_to_num(cy)])
    cuenta = cKDTree(cent).query_ball_point(cent, float(radio_um), return_length=True) - 1
    dens = cuenta / (math.pi * float(radio_um) ** 2) * 1e6
    eps = 1e-3
    X = np.column_stack([np.log(np.maximum(area, eps)), np.log(np.maximum(mayor, eps)),
                         np.log(np.maximum(menor, eps)),
                         np.log(np.maximum(mayor, eps) / np.maximum(menor, eps)),
                         np.clip(np.nan_to_num(solidez), 0, 1), np.log1p(dens)])
    return np.nan_to_num(X), cent


def _sigmoide(z):
    import numpy as np
    return 1.0 / (1.0 + np.exp(-np.clip(z, -40, 40)))


def ajusta_regla(X, y, ridge=PARAMS["regla_ridge"], iteraciones=PARAMS["regla_iter"]):
    """Logística con L2 (sin penalizar el intercepto), Newton; rasgos estandarizados."""
    import numpy as np
    X = np.asarray(X, np.float64)
    y = np.asarray(y, np.float64)
    mu = X.mean(0)
    sd = X.std(0)
    sd[sd == 0] = 1.0
    Z = np.column_stack([np.ones(len(X)), (X - mu) / sd])
    w = np.zeros(Z.shape[1])
    P = np.eye(Z.shape[1]) * float(ridge)
    P[0, 0] = 0.0
    for _ in range(int(iteraciones)):
        p = _sigmoide(Z @ w)
        W = p * (1 - p)
        H = Z.T @ (Z * W[:, None]) + P
        g = Z.T @ (y - p) - P @ w
        paso = np.linalg.solve(H + 1e-9 * np.eye(len(w)), g)
        w = w + paso
        if np.max(np.abs(paso)) < 1e-8:
            break
    return {"media": mu.tolist(), "desv": sd.tolist(), "intercepto": float(w[0]),
            "coef": w[1:].tolist()}


def probabilidad_regla(regla, X):
    import numpy as np
    X = np.asarray(X, np.float64).reshape(-1, len(regla["coef"]))
    z = (X - np.asarray(regla["media"])) / np.asarray(regla["desv"])
    return _sigmoide(z @ np.asarray(regla["coef"]) + float(regla["intercepto"]))


def corte_prevalencia(p, y):
    """Corte que iguala la fracción predicha a la observada en los datos de ajuste (2-oct, piloto
    real: con 3,8 % de anillos CK19+, el corte fijo 0,5 no marcaba ninguna célula). El denominador
    CUENTA células: un corte que no respeta la prevalencia sesga el %. Con k = round(prev·n), el
    corte es la k-ésima probabilidad más alta (los empates entran)."""
    import numpy as np
    p = np.sort(np.asarray(p, np.float64))[::-1]
    k = int(round(float(np.mean(np.asarray(y, bool))) * len(p))) if len(p) else 0
    return float(p[min(max(k, 1), len(p)) - 1]) if len(p) else 1.0


def ajusta_regla_con_corte(X, y):
    r = ajusta_regla(X, y)
    r["corte"] = corte_prevalencia(probabilidad_regla(r, X), y)
    return r


def aplica_regla(regla, X):
    """El corte viaja en la regla (`corte_prevalencia` de su ajuste); sin corte no se aplica."""
    return probabilidad_regla(regla, X) >= float(regla["corte"])


def auc(puntuacion, verdad):
    """AUC por rangos (Mann-Whitney, empates a la media); None sin las dos clases."""
    import numpy as np
    from scipy.stats import rankdata
    v = np.asarray(verdad, bool)
    n1, n0 = int(v.sum()), int((~v).sum())
    if not n1 or not n0:
        return None
    r = rankdata(np.asarray(puntuacion, np.float64))
    return float((r[v].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def _sens_esp(pred, verdad):
    import numpy as np
    pred, verdad = np.asarray(pred, bool), np.asarray(verdad, bool)
    tp, fn = int(np.sum(pred & verdad)), int(np.sum(~pred & verdad))
    tn, fp = int(np.sum(~pred & ~verdad)), int(np.sum(pred & ~verdad))
    return {"sensibilidad": tp / (tp + fn) if tp + fn else None,
            "especificidad": tn / (tn + fp) if tn + fp else None,
            "tp": tp, "fn": fn, "tn": tn, "fp": fp}


def valida_regla(X, y, grupos, minimo=PARAMS["regla_min_fragmento"]):
    """Validación dejando fuera un fragmento entero (y en-muestra, aparte)."""
    import numpy as np
    grupos = np.asarray(grupos)
    out = {}
    for g in sorted(set(grupos.tolist())):
        fuera = grupos == g
        if fuera.sum() < minimo or len(set(y[~fuera].tolist())) < 2:
            out[str(g)] = {"n": int(fuera.sum()), "evaluado": False}
            continue
        r = ajusta_regla_con_corte(X[~fuera], y[~fuera])
        out[str(g)] = dict(_sens_esp(aplica_regla(r, X[fuera]), y[fuera]), n=int(fuera.sum()),
                           evaluado=True, corte=r["corte"],
                           auc=auc(probabilidad_regla(r, X[fuera]), y[fuera]))
    return out


def regla_degenerada(r):
    """Motivos por los que la regla no sirve de denominador (vacío = sirve). Degenerada = no marca
    ninguna célula CK19+ en muestra, o fuera de muestra en TODOS los fragmentos evaluados que
    tienen positivos. Es un error de ejecución, no un límite del dato: no pasa."""
    motivos = []
    em = r.get("en_muestra") or {}
    if not em.get("sensibilidad"):
        motivos.append("in-sample sensitivity %s" % em.get("sensibilidad"))
    lofo = [v for v in (r.get("validacion_por_fragmento") or {}).values()
            if v.get("evaluado") and (v.get("tp", 0) + v.get("fn", 0)) > 0]
    if lofo and not any(v.get("sensibilidad") for v in lofo):
        motivos.append("leave-one-fragment-out sensitivity 0 in all %d evaluated fragments"
                       % len(lofo))
    return motivos


def regla_morfometrica(base, log=print):
    """Ajuste en P-CK19 (su propio cristal): verdad = anillo CK19+ de la célula (DAB medio del
    anillo > T_CK19 de la máscara sellada); sin artefactos y dentro de fragmento."""
    import numpy as np
    import laminillas_congela as K
    rel = _rel("regla_morfometrica.json")
    entradas = [_rel("objetos_P-CK19.npz"), _rel("objetos_P-CK19.json"),
                _rel("ck19", "mascara.json")]
    hu = _huella(base, entradas)
    if C.esta_hecho(base, PASO_REGLA, REFERENCIA, hu):
        r = _lee(base, rel)
        if r.get("degenerada"):
            raise NoPasa("regla morfométrica degenerada (%s): no sirve de denominador"
                         % "; ".join(r["degenerada"]))
        return r
    ob = carga_objetos(base, REFERENCIA)
    mk = _lee(base, _rel("ck19", "mascara.json"))
    T = float(mk["umbral"]["T"])
    reg = _rige_efectivo(base, _sello(base, [REFERENCIA]))
    ok = (~_artefacto(ob, reg)) & (ob["fragmento"] >= 0) & np.isfinite(ob["dab_anillo"])
    X, y, g = ob["rasgos"][ok], ob["dab_anillo"][ok] > T, ob["fragmento"][ok]
    npos, nneg = int(y.sum()), int((~y).sum())
    if min(npos, nneg) < PARAMS["regla_min_clase"]:
        raise NoPasa("regla morfométrica: no se puede ajustar (anillo CK19+ %d, CK19− %d; mínimo "
                     "%d de cada)" % (npos, nneg, PARAMS["regla_min_clase"]))
    r = ajusta_regla_con_corte(X, y)
    r.update({"tipo": "regla-morfometrica", "version": PASO_REGLA, "rasgos": list(RASGOS),
              "criterio_corte": PARAMS["regla_corte"], "ridge": PARAMS["regla_ridge"],
              "verdad": "CK19+ ring of each cell on P-CK19 (ring DAB > T_CK19 of the sealed mask)",
              "T_ck19": T, "n": int(len(y)), "n_ck19_pos": npos,
              "en_muestra": _sens_esp(aplica_regla(r, X), y),
              "auc_en_muestra": auc(probabilidad_regla(r, X), y),
              "validacion_por_fragmento": valida_regla(X, y, g),
              "entradas": hu, "declaracion": DECLARACIONES["regla"],
              "declaracion_corte": DECLARACIONES["regla_corte"]})
    r["degenerada"] = regla_degenerada(r)
    r["sha256"] = K.sha256_de(_limpio(r))
    _escribe(base, rel, r)
    C.marca_hecho(base, PASO_REGLA, REFERENCIA, hu, productos=[rel])
    log("regla morfométrica: n=%d (CK19+ %d), corte %.3f, en muestra sens %.2f esp %.2f, AUC %.3f"
        % (len(y), npos, r["corte"], r["en_muestra"]["sensibilidad"] or 0,
           r["en_muestra"]["especificidad"] or 0, r["auc_en_muestra"] or float("nan")))
    for f, v in sorted(r["validacion_por_fragmento"].items()):
        if v.get("evaluado"):
            log("  fragmento %s fuera: n=%d sens %s esp %s AUC %s" % (
                f, v["n"], _f2(v.get("sensibilidad")), _f2(v.get("especificidad")),
                _f2(v.get("auc"))))
    if r["degenerada"]:
        raise NoPasa("regla morfométrica degenerada (%s): no sirve de denominador"
                     % "; ".join(r["degenerada"]))
    return r


def _f2(v):
    return "—" if v is None else "%.2f" % v


def carga_regla(base):
    import laminillas_congela as K
    rel = _rel("regla_morfometrica.json")
    if not os.path.isfile(_abs(base, rel)):
        raise FaltaEntrada("falta la regla morfométrica: corre antes `piloto-ibis P-CK19`")
    r = _lee(base, rel)
    sha = r.pop("sha256", None)
    if sha != K.sha256_de(r):
        raise FaltaEntrada("regla_morfometrica.json no casa con su sha256 (¿editada?)")
    if r.get("version") != PASO_REGLA:
        raise FaltaEntrada("regla morfométrica de otra versión (%s): corre de nuevo "
                           "`piloto-ibis P-CK19`" % r.get("version", "v1, corte fijo 0,5"))
    if r.get("degenerada"):
        raise NoPasa("regla morfométrica degenerada (%s): no sirve de denominador"
                     % "; ".join(r["degenerada"]))
    r["sha256"] = sha
    return r


# ── objetos: DAB por núcleo con los vectores sellados ────────────────────────────────────────
def _en_mapa(mapa, origen, f, xy):
    import numpy as np
    xy = np.asarray(xy, np.float64).reshape(-1, 2)
    xi = np.floor((xy[:, 0] - origen[0]) / f).astype(int)
    yi = np.floor((xy[:, 1] - origen[1]) / f).astype(int)
    ok = (xi >= 0) & (yi >= 0) & (xi < mapa.shape[1]) & (yi < mapa.shape[0])
    out = np.zeros(len(xy), bool)
    out[ok] = mapa[yi[ok], xi[ok]]
    return out


def objetos(base, nombre, lector=None, log=print):
    """Por núcleo, con los vectores SELLADOS de la lámina (`matriz_de_lamina`: los de tanda o su
    DAB propio): OD medio y DAB en núcleo y anillo de 3 µm, hematoxilina nuclear, saturados no
    H/DAB, tercil bajo de foco, pliegue (regla del plan), marcas de GrandQC si corrió, fragmento y
    borde de la máscara provisional y rasgos morfométricos. Mide células: guardia del módulo A."""
    import numpy as np
    import laminillas_color as Co
    import laminillas_congela as K
    import laminillas_segmenta as S
    rel_npz, rel_json = _rel("objetos_%s.npz" % nombre), _rel("objetos_%s.json" % nombre)
    hu = _huella(base, _rels_nucleos(base, nombre) + ["congelacion.json"])
    if C.esta_hecho(base, "objetos", nombre, hu):
        return carga_objetos(base, nombre)
    nuc = nucleos_lamina(base, nombre)
    lector = _lector(lector)
    ruta = ruta_sello(base)
    sello = K.verifica_sello(ruta)
    M, rot_vec = K.matriz_de_lamina(sello, nombre)
    lam = lector.abre(nombre)
    mpp_l0 = float(lam.mpp_l0)
    dims = tuple(int(v) for v in lam.dimensiones_l0)
    pol = S.desempaqueta(nuc["pol_xy"], nuc["pol_offs"])
    cents = np.asarray(nuc["centroide"], np.float64).reshape(-1, 2)
    prov = S.mascara_provisional(cents, mpp_l0, dims)
    i0 = lector.i0_local(lam)
    o = K.pasada_objetos(lector, lam, pol, i0, prov=prov, M=M, muestra_h=False,
                         semilla=_semilla(sello, 300), ruta_sello=ruta, nombre=nombre)
    if nombre not in SUELO:
        K.registra_lectura_diana(ruta, nombre, "objetos")
    corte = S.corte_tercil_foco(o["foco_tesela"], o["tejido_tesela"])
    foco_bajo = np.zeros(len(pol), bool)
    if corte is not None and len(pol):
        foco_bajo = ~(o["foco_tesela"][o["tesela"]] >= corte)
    pli = K.pasada_pliegues(lector, lam, lector.zona_escaneada(lam), i0, prov)
    pliegue = _en_mapa(pli["pliegue"], pli["origen"], pli["f"], cents)
    rasgos, _ = rasgos_morfologicos(nuc["pol_xy"], nuc["pol_offs"], mpp_l0)
    arrays = dict(centroide=cents, area_um2=np.asarray(nuc["area_um2"]),
                  od_nucleo=o["od_nucleo"], od_anillo=o["od_anillo"],
                  dab_nucleo=Co.dab_de(o["od_nucleo"], M), dab_anillo=Co.dab_de(o["od_anillo"], M),
                  hema=Co.desmezcla(o["od_nucleo"], M)[:, 0], saturado=o["saturado"],
                  frac_recorte_nucleo=o["frac_recorte_nucleo"],
                  frac_recorte_anillo=o["frac_recorte_anillo"],
                  foco_bajo=foco_bajo, pliegue=pliegue, fragmento=S.fragmento_de(prov, cents),
                  borde_um=S.distancia_borde(prov, cents), foco_tesela=o["foco_tesela"],
                  tejido_tesela=o["tejido_tesela"], origen_tesela=o["origen_tesela"],
                  tesela=o["tesela"], rasgos=rasgos, prov_fragmentos=prov["fragmentos"],
                  pol_xy=np.asarray(nuc["pol_xy"]), pol_offs=np.asarray(nuc["pol_offs"]))
    if "grandqc" in nuc:
        arrays["grandqc"] = np.asarray(nuc["grandqc"], bool)
    _npz(base, rel_npz, **arrays)
    info = {"lamina": nombre, "n": int(len(pol)), "mpp_l0": mpp_l0, "dimensiones_l0": list(dims),
            "prov_f": float(prov["f"]), "prov_mpp": float(prov["mpp"]),
            "prov_areas_mm2": prov["areas_mm2"], "corte_tercil_foco": corte,
            "p995_odsum": pli["p995"], "candidatos_pliegue": pli["candidatos"],
            "rotulos_vectores": rot_vec, "sello": sello.sha256, "con_grandqc": "grandqc" in nuc,
            "nombres_rasgos": list(RASGOS), "origen_nucleos": nuc["origen"], "entradas": hu,
            "declaraciones": [DECLARACIONES["artefacto"]]}
    _escribe(base, rel_json, info)
    C.marca_hecho(base, "objetos", nombre, hu, productos=[rel_npz, rel_json])
    log("%s: objetos de %d núcleos (corte de foco %s)" % (nombre, len(pol), corte))
    return carga_objetos(base, nombre)


def carga_objetos(base, nombre):
    rel_npz, rel_json = _rel("objetos_%s.npz" % nombre), _rel("objetos_%s.json" % nombre)
    if not (os.path.isfile(_abs(base, rel_npz)) and os.path.isfile(_abs(base, rel_json))):
        raise FaltaEntrada("%s sin objetos: corre antes piloto-i / piloto-ibis o `metricas %s`"
                           % (nombre, nombre))
    d = _lee(base, rel_json)
    d.update(_carga_npz(base, rel_npz))          # los arrays mandan sobre el JSON
    return d


def _regimen_vigente(base):
    """Régimen que rige tras el chequeo de GrandQC en P-KI67 (punto 5), o None (el sellado)."""
    rel = _rel("regimen.json")
    return _lee(base, rel).get("regimen") if os.path.isfile(_abs(base, rel)) else None


def _rige_efectivo(base, sello):
    return _regimen_vigente(base) or _rige_sellado(sello) or "clasicas"


def _artefacto(ob, regimen):
    """Artefacto por núcleo según el régimen (ver DECLARACIONES["artefacto"])."""
    import numpy as np
    sat = np.asarray(ob["saturado"], bool)
    if regimen == "grandqc":
        if "grandqc" not in ob:
            raise FaltaEntrada("%s: rige GrandQC y la lámina no tiene sus marcas (segmenta con "
                               "GrandQC)" % ob.get("lamina"))
        return np.asarray(ob["grandqc"], bool) | sat
    return np.asarray(ob["foco_bajo"], bool) | np.asarray(ob["pliegue"], bool) | sat


# ── QC (F3) ──────────────────────────────────────────────────────────────────────────────────
def _contacto_borde(zona, frag, f, dims):
    """Por fragmento de la máscara provisional: fracción de su borde que toca la zona NO
    escaneada (o el borde de la imagen)."""
    import numpy as np
    import shapely
    from scipy import ndimage as ndi
    H, W = frag.shape
    yy, xx = np.mgrid[0:H, 0:W]
    px, py = (xx.ravel() + 0.5) * f, (yy.ravel() + 0.5) * f
    dentro = shapely.contains_xy(zona, px, py).reshape(H, W)
    dentro &= ((xx + 1) * f <= dims[0]) & ((yy + 1) * f <= dims[1])
    fuera = ndi.binary_dilation(np.pad(~dentro, 1, constant_values=True))[1:-1, 1:-1]
    out = {}
    for k in range(int(frag.max()) + 1 if frag.size else 0):
        m = frag == k
        if not m.any():
            continue
        borde = m & ~ndi.binary_erosion(np.pad(m, 1))[1:-1, 1:-1]
        out[str(k)] = {"pixeles_borde": int(borde.sum()),
                       "fraccion_toca_borde_escaneado": float((borde & fuera).sum() /
                                                              max(1, borde.sum()))}
    return out


def qc_lamina(base, nombre, lector=None, log=print):
    """QC de F3: foco (cociente de energía del laplaciano por tesela, solo dentro de esta
    tinción), contacto del tejido con el borde escaneado, I0 local sin franja, teselado y avisos
    de InstanSeg. «fraction of the section scanned: unknown, no macro image»."""
    import numpy as np
    import laminillas_congela as K
    rel = _rel("qc_%s.json" % nombre)
    hu = _huella(base, _rels_nucleos(base, nombre) + [_rel("objetos_%s.json" % nombre),
                                                      _rel("objetos_%s.npz" % nombre)])
    if C.esta_hecho(base, "qc", nombre, hu):
        return _lee(base, rel)
    lector = _lector(lector)
    lam = lector.abre(nombre)
    ob = carga_objetos(base, nombre)
    nuc = nucleos_lamina(base, nombre)
    res = nuc["resumen"]
    i0 = K.revisa_i0(lector, lam, nombre, lector.i0_local(lam))
    ft, tt = np.asarray(ob["foco_tesela"], float), np.asarray(ob["tejido_tesela"], float)
    ok = (tt >= 0.5) & np.isfinite(ft)
    foco = {"corte_tercil_bajo": ob.get("corte_tercil_foco"), "n_teselas_tejido": int(ok.sum()),
            "cociente_p10_p50_p90": ([float(v) for v in np.percentile(ft[ok], [10, 50, 90])]
                                     if ok.any() else None),
            "nucleos_en_tercil_bajo": int(np.sum(ob["foco_bajo"]))}
    contacto = _contacto_borde(lector.zona_escaneada(lam), np.asarray(ob["prov_fragmentos"]),
                               float(ob["prov_f"]), tuple(ob["dimensiones_l0"]))
    out = {"lamina": nombre, "alcance": "of the scanned region",
           "seccion": "fraction of the section scanned: unknown, no macro image",
           "i0": i0, "foco": foco, "contacto_borde_escaneado": contacto,
           "teselado": res.get("teselado"), "aviso_check_input_tile": res.get("aviso_check_input_tile"),
           "area_nuclear": res.get("area_nuclear"), "dispositivo": res.get("dispositivo"),
           "pesos": res.get("pesos"), "entradas": hu}
    _escribe(base, rel, out)
    C.marca_hecho(base, "qc", nombre, hu, productos=[rel])
    log("%s: QC (I0 %s, %d teselas de tejido)" % (nombre, "pasa" if i0.get("pasa") else "FRANJA",
                                                   foco["n_teselas_tejido"]))
    return out


# ── máscara CK19 (regla sellada; el valor se mide tras el sello) ─────────────────────────────
def mascara_ck19(base, lector=None, log=print):
    import numpy as np
    from scipy import ndimage as ndi
    import laminillas_congela as K
    import laminillas_segmenta as S
    rel_npz, rel_json = _rel("ck19", "mascara.npz"), _rel("ck19", "mascara.json")
    hu = _huella(base, _rels_nucleos(base, REFERENCIA) + ["congelacion.json"])
    if C.esta_hecho(base, "mascara-ck19", REFERENCIA, hu):
        return _lee(base, rel_json)
    lector = _lector(lector)
    nuc = nucleos_lamina(base, REFERENCIA)
    m = K.mascara_ck19(lector, ruta_sello(base), S.desempaqueta(nuc["pol_xy"], nuc["pol_offs"]))
    mask = np.asarray(m["mascara"], bool)
    _npz(base, rel_npz, bits=np.packbits(mask, axis=None), forma=np.asarray(mask.shape))
    sello = _sello(base, [REFERENCIA])
    borde = mask & ~ndi.binary_erosion(np.pad(mask, 1))[1:-1, 1:-1]
    yy, xx = np.nonzero(borde)
    rng = np.random.default_rng(_semilla(sello, PARAMS["semilla_galeria"]))
    k = min(PARAMS["galeria_ck19"], len(yy))
    sel = rng.choice(len(yy), k, replace=False) if k else np.zeros(0, int)
    mpp_l0 = float(nuc["mpp_l0"])
    f = float(m["mpp"]) / mpp_l0
    ox, oy = m["origen_l0"]
    galeria = [{"xy_l0": [float(ox + (xx[i] + 0.5) * f), float(oy + (yy[i] + 0.5) * f)],
                "lado_um": PARAMS["galeria_ck19_lado_um"]} for i in sorted(sel.tolist())]
    info = {"lamina": REFERENCIA, "origen_l0": [float(ox), float(oy)], "mpp": float(m["mpp"]),
            "mpp_l0": mpp_l0, "forma": list(mask.shape), "umbral": m["umbral"],
            "sensibilidad": m["sensibilidad"], "rotulos": m["rotulos"],
            "fraccion_dentro": float(np.mean(m["dentro"])) if len(m["dentro"]) else None,
            "galeria_borde": galeria, "entradas": hu}
    _escribe(base, rel_json, info)
    C.marca_hecho(base, "mascara-ck19", REFERENCIA, hu, productos=[rel_npz, rel_json])
    log("P-CK19: máscara (regla %s, T %.3f)" % (m["umbral"].get("regla"), m["umbral"]["T"]))
    return info


class MascaraCK19:
    """La máscara CK19+ sellada, consultada en µm del marco de P-CK19 (la referencia)."""

    def __init__(self, base):
        import numpy as np
        rel_npz, rel_json = _rel("ck19", "mascara.npz"), _rel("ck19", "mascara.json")
        if not os.path.isfile(_abs(base, rel_json)):
            raise FaltaEntrada("falta la máscara CK19: corre antes `piloto-ibis P-CK19`")
        self.info = _lee(base, rel_json)
        z = _carga_npz(base, rel_npz)
        forma = tuple(int(v) for v in z["forma"])
        self.m = np.unpackbits(z["bits"], count=forma[0] * forma[1]).reshape(forma).astype(bool)
        self.mpp, self.mpp_l0 = float(self.info["mpp"]), float(self.info["mpp_l0"])
        self.origen = np.asarray(self.info["origen_l0"], float)
        self._din = self._dout = None

    def _px(self, xy_um):
        import numpy as np
        return (np.asarray(xy_um, float).reshape(-1, 2) / self.mpp_l0 - self.origen) * (
            self.mpp_l0 / self.mpp)

    def dentro(self, xy_um):
        import laminillas_color as Co
        return Co.dentro(self.m, self._px(xy_um))

    def _distancias(self):
        import numpy as np
        from scipy import ndimage as ndi
        if self._din is None:
            k = max(1, int(round(PARAMS["erosion_rejilla_um"] / self.mpp)))
            H, W = (self.m.shape[0] + k - 1) // k * k, (self.m.shape[1] + k - 1) // k * k
            p = np.zeros((H, W), bool)
            p[:self.m.shape[0], :self.m.shape[1]] = self.m
            b = p.reshape(H // k, k, W // k, k)
            todo, alguno = b.all(axis=(1, 3)), b.any(axis=(1, 3))
            paso = k * self.mpp
            self._k = k
            # el borde de la imagen no erosiona (como `laminillas_color.morfologia`)
            self._din = ndi.distance_transform_edt(np.pad(todo, 1, mode="edge"))[1:-1, 1:-1] * paso
            self._dout = ndi.distance_transform_edt(~alguno) * paso if alguno.any() else \
                np.full(alguno.shape, np.inf)
        return self._din, self._dout

    def _muestrea(self, mapa, xy_um, fuera):
        import numpy as np
        px = self._px(xy_um) / self._k
        xi, yi = np.floor(px[:, 0]).astype(int), np.floor(px[:, 1]).astype(int)
        ok = (xi >= 0) & (yi >= 0) & (xi < mapa.shape[1]) & (yi < mapa.shape[0])
        out = np.full(len(px), fuera, float)
        out[ok] = mapa[yi[ok], xi[ok]]
        return out

    def erosionada(self, xy_um, r_um):
        """Dentro de la máscara erosionada r µm (por centroide)."""
        din, _ = self._distancias()
        return self.dentro(xy_um) & (self._muestrea(din, xy_um, 0.0) > float(r_um or 0.0))

    def dilatada(self, xy_um, r_um):
        """Dentro de la máscara dilatada r µm."""
        _, dout = self._distancias()
        return self.dentro(xy_um) | (self._muestrea(dout, xy_um, float("inf")) <= float(r_um or 0))


# ── registro (puerta del piloto (ii), (ii-bis), (iii)) ──────────────────────────────────────
def valida_par(a, b):
    """(móvil, fija, paso) de un par de la lista cerrada; ValueError si no vale. La referencia es
    P-CK19 (nunca P-HE); (iii) acepta CK19↔HE y KI67↔HE (la misma orden: `registra_he`)."""
    par = {a, b}
    if len(par) != 2:
        raise ValueError("registro-par: dos láminas distintas")
    if "P-HE" in par:
        otra = (par - {"P-HE"}).pop()
        if otra not in (REFERENCIA, "P-KI67"):
            raise ValueError("(iii) solo CK19↔P-HE y KI67↔P-HE")
        return "P-HE", REFERENCIA, "iii"
    if REFERENCIA not in par:
        raise ValueError("registro-par: la referencia es P-CK19 (pares del piloto: KI67↔CK19, "
                         "HER2NEG↔CK19, HER2↔CK19, CK19↔HE, KI67↔HE)")
    movil = (par - {REFERENCIA}).pop()
    if movil == "P-KI67":
        return movil, REFERENCIA, "ii"
    if movil in SUELO:
        return movil, REFERENCIA, "ii-bis"
    if movil in EXTENSION_IHQ:
        return movil, REFERENCIA, "extension"
    raise ValueError("registro-par: %s no se registra (AE1/AE3: insufficient detectable tissue "
                     "on this scan for registration)" % movil if movil == "P-AE1AE3" else
                     "registro-par: lámina fuera de la serie IHQ: %s" % movil)


def _init_eslabon1(base, movil, fija):
    """Ángulo y quiralidad del eslabón 1 (`identidad/eslabon1.json`, sha256 del manifiesto)."""
    rel = os.path.join("identidad", "eslabon1.json")
    if not os.path.isfile(_abs(base, rel)):
        return None
    try:
        man = C.lee_manifiesto(base).get("eslabon1") or {}
        if man.get("sha256") and man["sha256"] != C.sha256_fichero(_abs(base, rel)):
            return None
        pares = _lee(base, rel).get("pares") or {}
    except (OSError, ValueError):
        return None
    e = pares.get("%s|%s" % (movil, fija)) or pares.get("%s|%s" % (fija, movil))
    if not e:
        return None
    esp = float(e["espejo"]["iou"]) > float(e["directo"]["iou"])
    r = e["espejo" if esp else "directo"]
    return {"angulo_grados": float(r["angulo"]), "espejo": bool(esp), "origen": "eslabon1",
            "iou_mascaras": float(r["iou"])}


REL_FC = _rel("registro", "fc_piloto.npz")


def _sha_fc(base):
    return C.sha256_fichero(_abs(base, REL_FC)) if os.path.isfile(_abs(base, REL_FC)) else None


def _guarda_fcs(base, fcs):
    import numpy as np
    _npz(base, REL_FC, ids=np.array([f["id"] for f in fcs]),
         mascaras=np.stack([np.asarray(f["mascara"], bool) for f in fcs]) if fcs else
         np.zeros((0, 1, 1), bool), mpp=np.array(float(fcs[0]["mpp"]) if fcs else 0.0),
         areas=np.array([float(f["area_um2"]) for f in fcs]))


def carga_fcs(base):
    """FC del piloto (KI67∩CK19) en la rejilla ×8 de P-CK19, con su polígono en µm."""
    import laminillas_registro as R
    if not os.path.isfile(_abs(base, REL_FC)):
        raise FaltaEntrada("falta el FC del piloto: corre antes `registro-par P-KI67 P-CK19`")
    z = _carga_npz(base, REL_FC)
    mpp = float(z["mpp"])
    out = []
    for i, fid in enumerate(z["ids"].tolist()):
        m = z["mascaras"][i]
        out.append({"id": str(fid), "mascara": m, "mpp": mpp, "area_um2": float(z["areas"][i]),
                    "poligono_um": R.mascara_a_poligono(m, mpp),
                    "rotulo": R.ROTULOS["consenso_piloto"]})
    return out


def _cotejo_fcs(base, fcs):
    """La serie recalculada en (ii-bis)/(iii) tiene que dar EL MISMO FC que (ii) (semillas
    selladas): si no, es un error de ejecución, no un resultado."""
    import numpy as np
    prev = carga_fcs(base)
    if [f["id"] for f in prev] != [f["id"] for f in fcs] or any(
            not np.array_equal(np.asarray(a["mascara"], bool), np.asarray(b["mascara"], bool))
            for a, b in zip(prev, fcs)):
        raise RuntimeError("el FC recalculado no es el de (ii): registro no determinista; no se "
                           "sigue (error de ejecución)")


def _p90_par(resumen):
    """TRE del par = el mayor p90 de los FC verificados (None si no hay)."""
    ps = [f.get("evaluacion", {}).get("p90_um") for f in resumen.get("fragmentos", [])
          if f.get("pasa")]
    ps = [p for p in ps if p is not None]
    return max(ps) if ps else None


def _intentos_valis(resumen):
    """Los intentos VALIS de los FC de un resumen de par: [(fc, intento)]."""
    return [(f.get("id"), i) for f in ((resumen or {}).get("fragmentos") or [])
            for i in (f.get("intentos") or []) if i.get("metodo") == "VALIS"]


def _valis_corrio_en(resumen):
    """¿VALIS devolvió una transformación en algún FC? Desde el 2-oct-26 el intento lo dice
    (`corrio`); antes, solo un intento con transformación llevaba `escala`."""
    return any(i.get("corrio") or "escala" in i for _fc, i in _intentos_valis(resumen))


def valis_gastado(base, esc=None):
    """(gastada, por qué): ¿se gastó ya la vuelta ÚNICA con VALIS (plan, (ii) <50 %)? Solo se
    gasta si VALIS CORRIÓ: devolvió una transformación en al menos un FC.

    · Escaleras escritas desde el 2-oct-26: llevan `valis_corrio` y manda ese campo
      (`valis_intentado` vale lo mismo).
    · LEGADO, antes del 2-oct-26: `valis_intentado` se ponía a True con solo pedir `--valis`,
      aunque VALIS no corriera. Es lo que dejó el piloto real del 2-oct: VALIS 1.2.0 caía en
      `cleanup()` y la salida no se podía serializar a JSON (rc 1), y los tres FC grandes
      quedaron con «sin resultado». Sin `valis_corrio`, se mira el producto
      `registro/P-KI67.json`: si NINGÚN intento VALIS trae transformación (todos «sin
      resultado», o ninguno), cuenta como NO intentado. Si ese producto no se puede leer, cuenta
      como gastado (lado seguro de la regla del plan).
    No edita ningún fichero de SESION: la escalera se reescribe en la siguiente corrida de (ii)."""
    rel_esc = _rel("registro", "escalera.json")
    if esc is None:
        if not os.path.isfile(_abs(base, rel_esc)):
            return False, "no ladder"
        esc = _lee(base, rel_esc)
    if "valis_corrio" in esc:
        return bool(esc["valis_corrio"]), "valis_corrio=%s" % bool(esc["valis_corrio"])
    if not esc.get("valis_intentado"):
        return False, "valis_intentado=false"
    try:
        resumen = _lee(base, _rel("registro", "P-KI67.json")).get("resumen")
    except (OSError, ValueError):
        return True, ("legacy valis_intentado=true and registro/P-KI67.json unreadable: counted "
                      "as spent")
    if _valis_corrio_en(resumen):
        return True, "legacy valis_intentado=true and VALIS returned a transform"
    return False, ("legacy valis_intentado=true but no VALIS attempt returned a transform "
                   "(VALIS did not run): not spent")


def registro_par(base, a, b, lector=None, valis=False, log=print, ejecutor_valis=None):
    """El registro del módulo B para un par del piloto (o de la extensión, sobre el FC del
    piloto). Ver la cabecera para los pasos y los productos.

    VUELTA ÚNICA CON VALIS (`valis`=True, solo (ii) con escalera): la escalera queda con
    `valis_intentado` = `valis_corrio` = True SOLO si VALIS devolvió una transformación en al menos
    un FC (corrió: la vuelta se gasta, pase o no la puerta TRE). Si no corrió (subproceso con
    rc ≠ 0, salida ilegible, o ningún FC llegó al paso 4), deja `valis_intentado` False, añade
    `valis_fallos` y lanza `ValisNoCorrio` (código 6) tras escribir los productos. Una escalera
    de antes del 2-oct-26 con `valis_intentado` de un VALIS que no corrió se reconoce
    (`valis_gastado`). Una corrida de (ii) sin `--valis` no borra una vuelta ya gastada."""
    import laminillas_registro as R
    import laminillas_sello as SL
    movil, fija, paso = valida_par(a, b)
    lector = _lector(lector)
    ruta = ruta_sello(base)
    SL.exige(ruta, [fija, movil], SL.SECCIONES_MODULO_B)
    rel = _rel("registro", "%s.json" % movil)
    rel_esc = _rel("registro", "escalera.json")
    gastada, por_que = valis_gastado(base) if paso == "ii" else (False, "")
    if valis:
        if paso != "ii":
            raise ValueError("--valis solo en (ii) KI67↔CK19: es la vuelta al tribunal con VALIS")
        if not os.path.isfile(_abs(base, rel_esc)):
            raise ValueError("--valis solo si (ii) quedó <50 % (escalera sin registro)")
        if gastada:
            raise NoPasa("el registro ya volvió una vez con VALIS (plan: una sola vez; %s)"
                         % por_que)
        if _lee(base, rel_esc).get("valis_intentado"):
            log("escalera de antes del 2-oct-26: %s" % por_que)
    elif C.esta_hecho(base, "registro-par", movil, fija) and (
            paso == "ii" or _lee(base, rel).get("fc_piloto_sha256") == _sha_fc(base)):
        if registro_de_esta_version(_lee(base, rel)):
            log("%s↔%s: ya registrado (hecho sellado); salto" % (movil, fija))
            return _lee(base, rel)
        log("%s↔%s: registro de otra versión del método (%s); se repite con %s" % (
            movil, fija, _lee(base, rel).get("version_registro") or "v1", VERSION_REGISTRO))
    if paso != "ii" and not C.esta_hecho(base, "registro-par", "P-KI67", REFERENCIA):
        raise FaltaEntrada("(%s) necesita (ii): corre antes `registro-par P-KI67 P-CK19`" % paso)
    if not C.esta_hecho(base, "segmenta", "P-KI67"):
        segmenta(base, ["P-KI67"], lector=lector, log=log)
    implicadas = {"ii": ["P-CK19", "P-KI67"], "iii": ["P-CK19", "P-KI67", "P-HE"]}.get(
        paso, ["P-CK19", "P-KI67", movil] + (["P-HER2NEG"] if movil == "P-HER2" else []))
    cent = {n: nucleos_lamina(base, n)["centroide"] for n in implicadas}
    inits = {n: _init_eslabon1(base, n, REFERENCIA) for n in implicadas if n not in
             (REFERENCIA, "P-HE")}
    sello_sha = _sello(base, [movil]).sha256
    out = {"tipo": "registro-par", "paso": paso, "fija": fija, "movil": movil,
           "sello": sello_sha, "inicializacion": inits, "version_registro": VERSION_REGISTRO}
    imagenes = {}
    corrio, fallos = False, []
    if paso == "ii":
        serie = R.registra_serie(lector, ruta, ["P-KI67"], centroides=cent, triangulos=(),
                                 log=log, ejecutor_valis=ejecutor_valis, intentar_valis=valis,
                                 imagenes=imagenes, inits=inits)
        if "P-KI67" not in serie.get("resultados", {}):
            out.update({"resumen": None, "ii": serie["ii"], "fcs": []})
        else:
            res = serie["resultados"]["P-KI67"]
            _guarda_fcs(base, res.fcs)
            out.update({"resumen": serie["pares"]["P-KI67"], "ii": serie["ii"],
                        "fcs": serie["fcs"], "orden": serie.get("orden"),
                        "tre_p90_um": _p90_par(serie["pares"]["P-KI67"]),
                        "fc_piloto_sha256": C.sha256_fichero(_abs(base, REL_FC))})
        esc = _lee(base, rel_esc) if os.path.isfile(_abs(base, rel_esc)) else None
        corrio = bool(valis) and _valis_corrio_en(out["resumen"])
        gastada = gastada or corrio
        fallos = []
        if valis and not corrio:
            fallos = [{"fc": fc, "motivo": i.get("motivo")}
                      for fc, i in _intentos_valis(out["resumen"])]
            if not fallos:
                fallos = [{"fc": None, "motivo": (
                    "VALIS not launched: no fragment reached the VALIS step"
                    if out["resumen"] is not None else
                    "VALIS not launched: P-KI67 without a global transform")}]
        if not out["ii"]["extiende"]:
            esc = {"tipo": "escalera-sin-registro",
                   "fraccion_area_verificada_ki67": out["ii"]["fraccion_area_verificada_ki67"],
                   "extiende": ["scanned region", "per-slide % with the morphometric denominator",
                                "heterogeneity by fragment", "GeoJSON"],
                   "caen": ["(a) neighbourhood map", "(a-bis) ER-poor regions",
                            "CK19 denominator"],
                   "nota": out["ii"]["nota"],
                   "vuelve_al_tribunal": ("done (VALIS ran)" if gastada else
                                          "once, with VALIS: registro-par P-KI67 P-CK19 --valis"),
                   "valis_intentado": gastada, "valis_corrio": gastada}
            if fallos:
                esc["valis_fallos"] = fallos
            _escribe(base, rel_esc, esc)
        elif esc is not None:
            esc.update({"superada_con_valis": corrio, "valis_intentado": gastada,
                        "valis_corrio": gastada})
            if fallos:
                esc["valis_fallos"] = fallos
            else:
                esc.pop("valis_fallos", None)
            _escribe(base, rel_esc, esc)
        out["escalera"] = esc
    elif paso == "iii":
        serie = R.registra_serie(lector, ruta, ["P-KI67"], centroides=cent, triangulos=(),
                                 log=log, intentar_valis=False, imagenes=imagenes, inits=inits)
        _cotejo_fcs(base, serie["resultados"]["P-KI67"].fcs)
        he = R.registra_he(ruta, lector=lector, serie=serie, centroides=cent, imagenes=imagenes,
                           log=log, intentar_valis=False)
        out.update({k: v for k, v in he.items() if k != "resultados"})
        out["fc_piloto_sha256"] = _sha_fc(base)
        e1 = None
        try:
            e1 = (_lee(base, os.path.join("identidad", "eslabon1.json")).get(
                "he_ihq_informativo") or {})
        except (OSError, ValueError):
            pass
        out["angulo_mascaras_eslabon1"] = ({n: e1.get(n) for n in ("P-CK19", "P-KI67")}
                                           if e1 else None)
        out["nota"] = "mask angle from link 1 recorded as data, not a gate"
    else:
        moviles = ["P-KI67", movil]
        tri = [("P-KI67", REFERENCIA, movil)]
        if movil == "P-HER2":
            moviles = ["P-KI67", "P-HER2NEG", "P-HER2"]
            tri = [("P-KI67", REFERENCIA, "P-HER2NEG"), ("P-HER2NEG", REFERENCIA, "P-HER2")]
        serie = R.registra_serie(lector, ruta, moviles, centroides=cent, triangulos=tuple(tri),
                                 log=log, intentar_valis=False, imagenes=imagenes, inits=inits)
        _cotejo_fcs(base, serie["resultados"]["P-KI67"].fcs)
        out.update({"resumen": serie["pares"][movil], "cierres": serie["cierres"],
                    "orden": serie.get("orden"), "tre_p90_um": _p90_par(serie["pares"][movil]),
                    "fc_piloto_sha256": C.sha256_fichero(_abs(base, REL_FC)),
                    "control_positivo": ("HER2NEG→CK19→HER2 closure (near-identical fragments)"
                                         if movil == "P-HER2" else None),
                    "rotulo_consenso": R.ROTULOS["consenso_piloto"]})
        if paso == "extension":
            out["declaraciones"] = ["pilot consensus fragments (KI67∩CK19); the F3 consensus "
                                    "(>=6 of 11 masks) needs the full series, not this order"]
    _escribe(base, rel, out)
    productos = [rel] + ([REL_FC] if paso == "ii" and os.path.isfile(_abs(base, REL_FC)) else [])
    C.marca_hecho(base, "registro-par", movil, fija, productos=productos)
    log("%s↔%s (%s): %s" % (movil, fija, paso, json.dumps(
        out.get("ii") or {"fcs_pasan": out.get("fcs_pasan")}, default=str)[:300]))
    # `--valis` sin VALIS que corra: error de ejecución (código 6), salvo que (ii) ya extienda sin
    # que ningún FC necesitara VALIS (nada falló: no hacía falta).
    if paso == "ii" and valis and not corrio and (
            not out["ii"]["extiende"] or any(f["fc"] is not None for f in fallos)):
        raise ValisNoCorrio(
            ("VALIS no corrió (%s); la vuelta única con VALIS NO se gasta (escalera: "
             "valis_intentado=false, valis_fallos)" % "; ".join(
                 "%s: %s" % (f["fc"] or "-", f["motivo"]) for f in fallos[:3]))[:1200])
    return out


PARES_DIAGNOSTICO = (("P-KI67", REFERENCIA), ("P-HER2NEG", REFERENCIA), ("P-HER2", REFERENCIA))
DIAG_MARCA = "DIAG_JSON "


def diagnostico_registro(base, laminas=None, lector=None, log=print, ejecutor_valis=None):
    """`registro-par [MÓVIL P-CK19] --diagnostico`: SOLO NÚMEROS, para separar error de
    ejecución (A) de límite del método (B) cuando (ii)/(ii-bis) fallan en láminas reales (2-oct:
    SIFT con 0-3 inliers en todos los pares contra P-CK19, mientras VALIS sí registró).

    Por par (KI67, HER2NEG y HER2 contra P-CK19, sobre el FC de (ii); más el control
    HER2NEG→HER2 con su propio FC) llama a `laminillas_registro.diagnostico_par`: lo que entra en
    SIFT, rasgos, putativas con ratio 0,8/0,9/1,0, inliers, coherencia con VALIS y con el eslabón
    1, variantes (sin CLAHE, hematoxilina, ×32), SIFT por FC, fase y TRE (b)/(b') con cada
    transformación, y la medida contra sí misma. VALIS corre aquí SOLO como referencia.

    NO escribe productos del registro, no marca «hecho», no toca la escalera ni la vuelta única
    con VALIS: deja `piloto/diagnostico_registro.json` (lo pisa cada corrida) y lo imprime en
    una línea `DIAG_JSON` (y cada par, al acabar, en una `DIAG_PAR`).

    MEMORIA: VALIS, como hijo, llega a ≈6 GB; con los tres pares en un proceso el árbol pasó de
    20 GB (techo 10, código 99). Se corre UN par por orden (`registro-par P-KI67 P-CK19
    --diagnostico`; P-HER2 añade el control HER2NEG→HER2, sin VALIS)."""
    import gc
    import resource
    import numpy as np
    import laminillas_registro as R
    import laminillas_sello as SL
    log_base = log

    def log(m):                       # al momento (la jaula guarda stdout con búfer) y con RSS
        div = 1.0 if sys.platform == "darwin" else 1024.0      # ru_maxrss: bytes en macOS
        yo = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / div / 2 ** 30
        hijos = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / div / 2 ** 30
        log_base("%s · pico RSS %.1f GB (hijos %.1f)" % (m, yo, hijos))
        sys.stdout.flush()
    if laminas:
        if len(laminas) != 2:
            raise ValueError("registro-par --diagnostico: sin láminas (todos los pares) o un par")
        movil, fija, paso = valida_par(laminas[0], laminas[1])
        if paso not in ("ii", "ii-bis"):
            raise ValueError("registro-par --diagnostico: solo (ii) y (ii-bis)")
        pares = [(movil, fija)]
    else:
        pares = list(PARES_DIAGNOSTICO)
    lector = _lector(lector)
    ruta = ruta_sello(base)
    moviles = [m for m, _ in pares]
    control = "P-HER2" in moviles
    implicadas = [REFERENCIA] + moviles + (["P-HER2NEG"] if control and "P-HER2NEG" not in
                                           moviles else [])
    for n in implicadas:
        SL.exige(ruta, [n], SL.SECCIONES_MODULO_B)
    fcs = carga_fcs(base)
    cent = {n: nucleos_lamina(base, n)["centroide"] for n in implicadas}
    p, _v, sello_v, _pre = R._params(ruta, REFERENCIA, moviles[0])
    out = {"tipo": "diagnostico-registro",
           "nota": ("diagnostic only: VALIS run here as a reference, not a registration attempt; "
                    "no registration product written; variants are not the sealed method"),
           "parametros_sellados": {k: p[k] for k in (
               "factor_nivel", "clahe_kernel_um", "clahe_clip", "odsum_max_8bits",
               "sift_upsampling", "ratio", "cross_check", "min_samples", "residual_px",
               "max_trials", "init_radio_um", "min_inliers", "min_inliers_global",
               "valis_escala_tol", "fase_angulos")},
           "fcs": [{"id": f["id"], "area_mm2": round(f["area_um2"] / 1e6, 3)} for f in fcs],
           "imagenes": {}, "pares": {}}
    imgs = {}

    def carga(n):
        if n not in imgs:
            log("diagnóstico: imagen ×8 de %s" % n)
            imgs[n] = R.ImagenRegistro(lector, n, p, R.vectores_de(n, sello_v), retiene=True)
            out["imagenes"][n] = R.diag_imagen(imgs[n])
            im = out["imagenes"][n]
            log("  %s: forma %s, mpp %s, tejido %s mm²" % (n, im["forma"], im["mpp"],
                                                         im["area_tejido_mm2"]))
        return imgs[n]

    def suelta(*nombres):
        for n in nombres:
            imgs.pop(n, None)
        gc.collect()

    ir_ref = carga(REFERENCIA)
    cent_ref_um = np.asarray(cent[REFERENCIA], float) * ir_ref.mpp_l0
    rasgos_fija = {}
    for movil, fija in pares:
        log("diagnóstico: %s→%s" % (movil, fija))
        _p, vect, _s, _pr = R._params(ruta, fija, movil)
        ir_m = carga(movil)
        out["pares"]["%s→%s" % (movil, fija)] = R.diagnostico_par(
            lector, ir_ref, ir_m, p, vect, fcs, init=_init_eslabon1(base, movil, REFERENCIA),
            cent_f=cent_ref_um, cent_m_l0=cent[movil], ejecutor_valis=ejecutor_valis,
            rasgos_fija=rasgos_fija, log=log)
        log_base("DIAG_PAR " + json.dumps(_limpio({"par": "%s→%s" % (movil, fija),
                                                   "d": out["pares"]["%s→%s" % (movil, fija)]}),
                                          sort_keys=True))
        if movil == "P-KI67":
            suelta(movil)
    if control:
        # sin VALIS: su referencia es la global SIFT del par (y VALIS como hijo suma ≈6 GB al
        # techo de 10 con tres láminas cargadas)
        log("diagnóstico: control P-HER2NEG→P-HER2 (sin eslabón 1 ni VALIS, FC propio)")
        _p, vect, _s, _pr = R._params(ruta, "P-HER2", "P-HER2NEG")
        ir_h, ir_n = carga("P-HER2"), carga("P-HER2NEG")
        out["control_her2neg_her2"] = R.diagnostico_par(
            lector, ir_h, ir_n, p, vect, None, init=None,
            cent_f=np.asarray(cent["P-HER2"], float) * ir_h.mpp_l0, cent_m_l0=cent["P-HER2NEG"],
            ejecutor_valis=ejecutor_valis, variantes=(), valis=False, log=log)
    suelta(*list(imgs))
    _escribe(base, _rel("diagnostico_registro.json"), out)
    log_base(DIAG_MARCA + json.dumps(_limpio(out), sort_keys=True))
    sys.stdout.flush()
    return out


def estado_ii(base):
    """(estado, json de (ii)): «sin_registro», «registrado» (≥50 %) o «escalera» (<50 %)."""
    rel = _rel("registro", "P-KI67.json")
    if not (os.path.isfile(_abs(base, rel)) and
            C.esta_hecho(base, "registro-par", "P-KI67", REFERENCIA)):
        return "sin_registro", None
    d = _lee(base, rel)
    return ("registrado" if (d.get("ii") or {}).get("extiende") else "escalera"), d


def resultado_desde_disco(base, movil):
    """`ResultadoRegistro` reconstruido de lo persistido (global, FC del piloto y matrices por
    FC), para `nucleos_a_referencia` y `matrices_verificadas`."""
    import numpy as np
    import laminillas_registro as R
    rel = _rel("registro", "%s.json" % movil)
    if not (os.path.isfile(_abs(base, rel)) and
            C.esta_hecho(base, "registro-par", movil, REFERENCIA)):
        raise FaltaEntrada("%s sin registro: corre antes `registro-par %s P-CK19`"
                           % (movil, movil))
    d = _lee(base, rel)
    if movil != "P-KI67" and d.get("fc_piloto_sha256") != _sha_fc(base):
        raise FaltaEntrada("%s: el FC del piloto cambió desde su registro: repite `registro-par %s "
                           "P-CK19`" % (movil, movil))
    rs = d.get("resumen")
    if not rs or rs.get("global", {}).get("matriz_um") is None:
        res = R.ResultadoRegistro(REFERENCIA, movil, {}, None, False)
        res.global_ = {"matriz_um": None}
        return res, d
    res = R.ResultadoRegistro(rs["fija"], movil, rs.get("parametros") or {}, None, False)
    res.global_ = dict(rs["global"], matriz_um=np.asarray(rs["global"]["matriz_um"], float))
    por = {f["id"]: f for f in rs.get("fragmentos", [])}
    for fc in carga_fcs(base):
        f = por.get(fc["id"])
        if f is None:
            continue
        res.fragmentos.append(dict(f, matriz_um=np.asarray(f["matriz_um"], float)))
        res.fcs.append(fc)
    return res, d


def fc_de(fcs, xy_um):
    """Id del FC (rejilla ×8 de la referencia) de cada punto en µm de P-CK19, o None."""
    import numpy as np
    xy = np.asarray(xy_um, float).reshape(-1, 2)
    ids = np.array([None] * len(xy), dtype=object)
    for fc in fcs:
        m, mpp = fc["mascara"], fc["mpp"]
        c = np.floor(xy[:, 0] / mpp).astype(int)
        r = np.floor(xy[:, 1] / mpp).astype(int)
        ok = (c >= 0) & (r >= 0) & (c < m.shape[1]) & (r < m.shape[0])
        dentro = np.zeros(len(xy), bool)
        dentro[ok] = m[r[ok], c[ok]]
        ids[dentro & np.array([i is None for i in ids], bool)] = fc["id"]
    return ids


# ── tabla de núcleos y métricas ──────────────────────────────────────────────────────────────
def _poligonos_prov(ob):
    """Polígonos (µm del marco de la lámina) de los fragmentos de su máscara provisional."""
    import numpy as np
    import laminillas_registro as R
    fr = np.asarray(ob["prov_fragmentos"])
    mpp = float(ob["prov_mpp"])
    return {"F%d" % k: R.mascara_a_poligono(fr == k, mpp)
            for k in range(int(fr.max()) + 1 if fr.size else 0) if (fr == k).any()}


def _L_densidad(nuc, fcs, ctx, den):
    """L de la escalera: el menor candidato con mediana ≥ L_mediana_min núcleos del denominador
    por región (celdas con ≥ L_frac_area_mediana en el fragmento). Sin TRE: no hay registro.
    Si ninguno llega, rige el MÁXIMO de la lista sellada con su rótulo: es un límite (b) y no
    bloquea; el plan solo bloquea por la detección nuclear en P-KI67 y por (ii) (2-oct, piloto
    real: con ~4 % de epitelio no llegaba ni L = 400 µm). Devuelve (L, tabla, rótulo o None)."""
    import numpy as np
    import laminillas_metricas as MET
    p = ctx.p
    tabla = []
    for L in p["L_candidatos_um"]:
        cnt = MET.cuenta_regiones(nuc, MET.rejilla(fcs, float(L)), ctx, den)
        ns = [r["n"] for r in cnt.values() if r["frac_area"] >= p["L_frac_area_mediana"]]
        med = float(np.median(ns)) if ns else 0.0
        tabla.append({"L_um": L, "mediana_nucleos": med})
        if med >= p["L_mediana_min"]:
            return float(L), tabla, None
    if not tabla:
        return None, tabla, None
    tope = tabla[-1]
    return float(tope["L_um"]), tabla, (
        "L at the top of the sealed list; median denominator nuclei per region = %.0f (<%d)"
        % (tope["mediana_nucleos"], p["L_mediana_min"]))


def _valor_L(base, nuc, fcs, ctx, tre, sha_ii):
    """L de la regla sellada en (0), fijado tras (ii) en el FC de P-KI67 (densidad y TRE, sin
    positividad). Las demás láminas lo leen. La caché vale con el MISMO registro y las mismas
    entradas del denominador (sello, objetos y regla de P-KI67, máscara CK19, régimen): antes solo
    miraba el registro, y una recongelación que cambia los artefactos lo dejaba viejo (2-oct)."""
    import laminillas_metricas as MET
    rel = _rel("L.json")
    hu_den = _huella(base, ["congelacion.json", _rel("objetos_P-KI67.npz"),
                            _rel("objetos_P-KI67.json"), _rel("regla_morfometrica.json"),
                            _rel("ck19", "mascara.json"), _rel("regimen.json")])
    if os.path.isfile(_abs(base, rel)):
        d = _lee(base, rel)
        if d.get("registro_sha256") == sha_ii and d.get("entradas_denominador") == hu_den:
            if d.get("L_um") is None:
                raise NoPasa("regla de L: %s" % d.get("motivo"))
            return float(d["L_um"]), d
    if nuc.lamina != "P-KI67":
        raise FaltaEntrada("L se fija tras (ii) en P-KI67: corre antes `metricas P-KI67`")
    r = MET.elige_L(nuc, fcs, tre, ctx, "principal")
    d = dict(r, tre_p90_um=tre, registro_sha256=sha_ii, entradas_denominador=hu_den,
             regla="smallest of {100,150,200,300,400} um with L >= 2x TRE p90 and median >= 100 "
                   "denominator nuclei per region in the P-KI67 consensus fragments")
    _escribe(base, rel, d)
    if r["L_um"] is None:
        raise NoPasa("regla de L: %s" % r["motivo"])
    return float(r["L_um"]), d


def _deteccion(base, lamina):
    rel = _rel("deteccion.json")
    if not os.path.isfile(_abs(base, rel)):
        return None
    return (_lee(base, rel).get("laminas") or {}).get(lamina)


def _tabla(MET, lamina, xy_um, dab, ids, den, ob, artef):
    """Tabla de núcleos de la lámina; `recorte` = fracción recortada en el compartimento de `dab`
    (regla v2; objetos de antes del 2-oct no la tienen: None)."""
    import laminillas_sello as SL
    comp = SL.COMPARTIMENTO.get(lamina, "nucleo")
    clave = "frac_recorte_anillo" if comp == "anillo" else "frac_recorte_nucleo"
    return MET.Nucleos(lamina, xy_um, dab, ids, den, artefacto=artef, hema=ob["hema"],
                       xy_l0=ob["centroide"], area_um2=ob["area_um2"],
                       dab_anillo=ob["dab_anillo"], recorte=ob.get(clave))


def _entradas_metricas(base, lamina):
    rels = ["congelacion.json", _rel("objetos_%s.npz" % lamina), _rel("objetos_%s.json" % lamina),
            _rel("regla_morfometrica.json"), _rel("ck19", "mascara.json"),
            _rel("registro", "P-KI67.json"), _rel("registro", "%s.json" % lamina), REL_FC,
            _rel("L.json"), _rel("regimen.json"), _rel("deteccion.json"),
            _rel("objetos_P-CK19.json"), "extension.json"]
    d = _abs(base, _rel("metricas"))
    if os.path.isdir(d):
        rels += [_rel("metricas", f) for f in sorted(os.listdir(d))
                 if f.endswith(".json") and f != "%s.json" % lamina]
    return rels


def prepara(base, lamina, lector=None, log=print):
    """Todo lo que necesitan `metricas` y `geojson` de UNA lámina: tabla de núcleos en el marco
    de la referencia, denominadores, contexto (sello, régimen, fondo), FC, rejilla y regiones. El
    camino depende de (ii): registrado (≥50 %), escalera (<50 %) o sin registro (falta (ii))."""
    import laminillas_sello as SL
    if lamina in FUERA_DE_METRICAS:
        raise ValueError(FUERA_DE_METRICAS[lamina])
    ruta = ruta_sello(base)
    sello = SL.exige(ruta, [lamina], SL.SECCIONES_MODULO_B)       # antes de nada pesado
    estado, dii = estado_ii(base)
    if estado == "sin_registro":
        raise FaltaEntrada("falta (ii): corre antes `registro-par P-KI67 P-CK19` (el % y el "
                           "hotspot necesitan su denominador)")
    import numpy as np
    import laminillas_metricas as MET
    ob = objetos(base, lamina, lector, log)
    regla = carga_regla(base)
    regimen = _regimen_vigente(base)
    reg_ef = regimen or _rige_sellado(sello) or "clasicas"
    artef = _artefacto(ob, reg_ef)
    morf = aplica_regla(regla, ob["rasgos"])
    comp = SL.COMPARTIMENTO.get(lamina, "nucleo")
    dab = np.asarray(ob["dab_anillo" if comp == "anillo" else "dab_nucleo"], float)
    cent = np.asarray(ob["centroide"], float)
    mpp = float(ob["mpp_l0"])
    ctx = MET.Contexto(ruta, lamina, regimen=regimen)
    P = {"lamina": lamina, "sello": sello, "estado": estado, "regimen": reg_ef, "ob": ob,
         "morf": morf, "artefacto": artef, "regla_sha256": regla["sha256"], "mpp_l0": mpp,
         "declaraciones": [DECLARACIONES["artefacto"], regla["declaracion"]], "propio": None,
         "supervivencia": None, "control_fondo": None, "ck19_caido": False, "solo_mapa": (),
         "densidad_por_fc": None, "error_regla_dab": None}
    if estado == "escalera":
        fcs = _poligonos_prov(ob)
        ids = np.array(["F%d" % k if k >= 0 else None for k in ob["fragmento"]], dtype=object)
        nuc = _tabla(MET, lamina, cent * mpp, dab, ids, {"morfometrico": morf}, ob, artef)
        L, tabla_L, rotulo_L = _L_densidad(nuc, fcs, ctx, "morfometrico")
        P.update(nuc=nuc, ctx=ctx, fcs=fcs, den="morfometrico", ck19_caido=True, L=L,
                 tabla_L=tabla_L, matrices=np.eye(3))
        P["declaraciones"].append(DECLARACIONES["escalera_L"])
        if rotulo_L:
            P["declaraciones"].append(rotulo_L)
        P["regiones"] = (MET.cuenta_regiones(nuc, MET.rejilla(fcs, L), ctx, "morfometrico")
                         if L else {})
        P["huella"] = _huella(base, _entradas_metricas(base, lamina))
        return P
    # registrado (≥50 % del área de consenso verificada en (ii))
    fcs_l = carga_fcs(base)
    fcs = {f["id"]: f["poligono_um"] for f in fcs_l if f["poligono_um"] is not None}
    ck = MascaraCK19(base)
    tre_ki = dii.get("tre_p90_um")
    if lamina == REFERENCIA:
        xy_um = cent * mpp
        ids = fc_de(fcs_l, xy_um)
        matrices, tre = np.eye(3), tre_ki
    else:
        res, dpar = resultado_desde_disco(base, lamina)
        if res.global_.get("matriz_um") is None:
            xy_um = np.full((len(cent), 2), np.nan)
            ids = np.array([None] * len(cent), dtype=object)
        else:
            xy_um, ids = res.nucleos_a_referencia(cent, mpp)
        matrices, tre = res, _p90_par(dpar.get("resumen") or {})
    den = {"ck19_erosionada": ck.erosionada(xy_um, tre), "ck19_sin_erosionar": ck.dentro(xy_um),
           "morfometrico": morf}
    nuc = _tabla(MET, lamina, xy_um, dab, ids, den, ob, artef)
    # supervivencia de la erosión, medida en P-CK19 sin registro con el TRE de ESTE par
    ob_ck = carga_objetos(base, REFERENCIA) if lamina != REFERENCIA else ob
    xy_ck = np.asarray(ob_ck["centroide"], float) * float(ob_ck["mpp_l0"])
    den_ck = {"ck19_erosionada": ck.erosionada(xy_ck, tre), "ck19_sin_erosionar": ck.dentro(xy_ck),
              "morfometrico": aplica_regla(regla, ob_ck["rasgos"])}
    nuc_ck = _tabla(MET, REFERENCIA, xy_ck, np.asarray(ob_ck["dab_anillo"], float),
                    fc_de(fcs_l, xy_ck), den_ck, ob_ck, _artefacto(ob_ck, reg_ef))
    superv = MET.supervivencia_erosion(nuc_ck, MET.Contexto(ruta, REFERENCIA, regimen=regimen))
    principal = MET.aplica_denominador_principal(nuc, superv)
    P.update(supervivencia=superv, principal_por_fc=principal, tre_p90_um=tre)
    P["declaraciones"].append(DECLARACIONES["erosion"])
    if lamina != REFERENCIA and lamina not in SUELO:
        # F3 «Umbral»: nucleares y citoplasmáticos; no P-CK19 ni las de suelo (su T sale de HER2NEG)
        control = MET.control_fondo(nuc, ctx, ~ck.dilatada(xy_um, tre), tre or 0.0)
        ctx = ctx.eleva_por_fondo(control)
        P["control_fondo"] = control
        # error de la regla morfométrica, aparte en DAB+ y DAB− (frente a CK19 registrada)
        ok = nuc.en_fc() & ~nuc.artefacto
        pos = nuc.dab > ctx.T
        P["error_regla_dab"] = {
            "dab_pos": _sens_esp(morf[ok & pos], den["ck19_sin_erosionar"][ok & pos]),
            "dab_neg": _sens_esp(morf[ok & ~pos], den["ck19_sin_erosionar"][ok & ~pos])}
    L, dL = _valor_L(base, nuc, fcs, ctx, tre_ki,
                     C.sha256_fichero(_abs(base, _rel("registro", "P-KI67.json"))))
    P.update(nuc=nuc, ctx=ctx, fcs=fcs, den="principal", L=L, tabla_L=dL.get("tabla"),
             matrices=matrices)
    P["regiones"] = MET.cuenta_regiones(nuc, MET.rejilla(fcs, L, (0.0, 0.0)), ctx, "principal")
    # parada dura de densidad: frente a la mediana de la serie medida hasta ahora
    dens = {lamina: MET.densidad_por_fc(nuc, fcs),
            REFERENCIA: MET.densidad_por_fc(nuc_ck, fcs)}
    dmet = _abs(base, _rel("metricas"))
    if os.path.isdir(dmet):
        for f in sorted(os.listdir(dmet)):
            if f.endswith(".json") and f[:-5] not in dens:
                dd = _lee(base, _rel("metricas", f)).get("densidad_por_fc")
                if isinstance(dd, dict):
                    dens[f[:-5]] = {k: float(v) for k, v in dd.items() if v is not None}
    parada = MET.parada_densidad(dens)
    P.update(densidad_por_fc=dens[lamina], parada=parada,
             solo_mapa=tuple(parada["solo_mapa"].get(lamina, ())))
    # fragmentos fuera del registro verificado: análisis propio (morfométrico, fuera del mapa)
    sin = np.array([i is None for i in ids], bool) & (np.asarray(ob["fragmento"]) >= 0)
    if lamina != REFERENCIA and sin.any():
        ids_p = np.array(["F%d" % k if s else None for k, s in zip(ob["fragmento"], sin)],
                         dtype=object)
        cero = np.zeros(len(cent), bool)
        den_p = {"ck19_erosionada": cero, "ck19_sin_erosionar": cero, "morfometrico": morf}
        nuc_p = _tabla(MET, lamina, cent * mpp, dab, ids_p, den_p, ob, artef)
        fcs_p = {k: v for k, v in _poligonos_prov(ob).items() if k in set(ids_p[sin])}
        ctx_p = MET.Contexto(ruta, lamina, regimen=regimen)
        reg_p = MET.cuenta_regiones(nuc_p, MET.rejilla(fcs_p, L), ctx_p, "morfometrico")
        P["propio"] = {"nuc": nuc_p, "ctx": ctx_p, "fcs": fcs_p, "regiones": reg_p,
                       "n_nucleos": int(sin.sum())}
        P["declaraciones"].append(DECLARACIONES["propio"])
    P["huella"] = _huella(base, _entradas_metricas(base, lamina))   # al final: L.json y objetos
    return P


def _referencias(base, P):
    """Regiones de RE y Ki67 (mismo L y FC) para el patrón de puntos de un NE «focal»."""
    import laminillas_metricas as MET
    out = {}
    for ref in ("P-RE", "P-KI67"):
        rel = _rel("metricas", "%s.json" % ref)
        if ref == P["lamina"] or not os.path.isfile(_abs(base, rel)):
            continue
        d = _lee(base, rel)
        if d.get("L_um") != P["L"] or d.get("estado") != P["estado"]:
            continue
        rej = MET.rejilla(P["fcs"], P["L"], (0.0, 0.0))
        cuentas = d.get("regiones") or {}
        regs = {}
        for c, r in rej.items():
            v = cuentas.get("%s:%d,%d" % c)
            if v is None:
                continue
            regs[c] = dict(r, n=v["n"], k=v["k"], pct=v["pct"] if v["pct"] is not None
                           else float("nan"), lamina=ref, T=d["T"], denominador=d["denominador"])
        out[ref] = regs
    return out or None


def _mide(base, P, lector):
    import laminillas_metricas as MET
    lamina, nuc, ctx = P["lamina"], P["nuc"], P["ctx"]
    out = {"tipo": "metricas", "lamina": lamina, "marcador": MARCADOR.get(lamina),
           "estado": P["estado"], "regimen": P["regimen"], "L_um": P["L"],
           "denominador": P["den"], "T": ctx.T, "umbral": ctx.cita(),
           "alcance": MET.ROTULOS["region_escaneada"], "declaraciones": list(P["declaraciones"]),
           "metodos": MET.metodos(), "regla_morfometrica_sha256": P["regla_sha256"],
           "supervivencia_erosion": P["supervivencia"], "control_fondo": P["control_fondo"],
           "densidad_por_fc": P["densidad_por_fc"], "error_regla_dab": P["error_regla_dab"],
           "solo_mapa": list(P["solo_mapa"]), "huella": P["huella"]}
    if P["estado"] == "escalera":
        out["escalera"] = _lee(base, _rel("registro", "escalera.json"))
    if P["L"] is None:
        raise NoPasa("%s: ningún L de la lista cumple la densidad (escalera)" % lamina)
    if lamina == REFERENCIA:
        mk = _lee(base, _rel("ck19", "mascara.json"))
        out["resultado"] = {"nota": MET.NO_MARCADOR[REFERENCIA], "umbral_mascara": mk["umbral"],
                            "sensibilidad_mascara": mk["sensibilidad"], "rotulos": mk["rotulos"]}
    elif lamina in SUELO:
        out["resultado"] = MET.her2_membrana(nuc, ctx, P["den"])
    else:
        det = _deteccion(base, lamina)
        ok_det = None if det is None else bool(det.get("deteccion_ok"))
        nuc_cl = None
        if ok_det is False:
            nuc_cl = nucleos_clasicos(base, P, lector)
            out["declaraciones"].append("detection-dependent: classical detector over the whole "
                                        "scanned region gives the other end of the range")
        out["resultado"] = MET.mide_marcador(
            nuc, P["regiones"], ctx, P["den"], fcs=P["fcs"], nuc_clasico=nuc_cl,
            deteccion_ok=ok_det, ck19_caido=P["ck19_caido"], solo_mapa=P["solo_mapa"],
            referencias=_referencias(base, P) if lamina in MET.LAMINAS_NE else None)
        if P["propio"]:
            pr = P["propio"]
            out["analisis_propio"] = {
                "n_nucleos": pr["n_nucleos"], "nota": DECLARACIONES["propio"],
                "porcentaje": MET.porcentaje_global(pr["nuc"], pr["regiones"], pr["ctx"],
                                                    "morfometrico")}
    out["regiones"] = {"%s:%d,%d" % c: {"k": r["k"], "n": r["n"], "pct": r["pct"]}
                       for c, r in sorted(P["regiones"].items(), key=lambda kv: kv[0])}
    return out


def metricas(base, lamina, lector=None, log=print):
    """% global con IC por bloques y rango de sensibilidad, H-score, heterogeneidad, puerta de
    señal, hotspot (Ki67) y patrón de puntos (NE «focal») de `laminillas_metricas`, con la tabla
    de `prepara`. Mismo código para cualquier marcador (extensión incluida)."""
    P = prepara(base, lamina, lector, log)
    import laminillas_metricas as MET
    rel = _rel("metricas", "%s.json" % lamina)
    if C.esta_hecho(base, "metricas", lamina, P["huella"]):
        log("%s: métricas ya hechas con estas entradas; salto" % lamina)
        return _lee(base, rel)
    out = _mide(base, P, lector)
    malas = MET.barre_nunca(out)
    if malas:
        raise RuntimeError("frase de «Nunca decir» en las métricas: %s" % ", ".join(malas))
    _escribe(base, rel, out)
    C.marca_hecho(base, "metricas", lamina, P["huella"], productos=[rel])
    # Ninguna cifra de diana al log (2-oct): el log sale de la zona clínica (stdout de la
    # ventanilla, logs del orquestador) y una cifra vista antes de tiempo contamina decisiones de
    # método. La cifra vive solo en SESION (metricas/<lámina>.json).
    log("%s: métricas escritas (%s, L %s µm; cifras solo en %s)" % (lamina, P["estado"], P["L"],
                                                                     rel))
    return out


def nucleos_clasicos(base, P, lector):
    """Detector clásico (watershed sobre ODsum a L0) en TODA la zona escaneada, con el DAB del
    compartimento de cada objeto y los mismos denominadores y marco que la tabla InstanSeg (el
    extremo del rango cuando el recall por clase no pasa). Artefacto: el del núcleo InstanSeg más
    próximo a ≤5 µm."""
    import numpy as np
    from scipy.spatial import cKDTree
    from skimage.segmentation import expand_labels
    import laminillas_color as Co
    import laminillas_congela as K
    import laminillas_metricas as MET
    import laminillas_segmenta as S
    lamina, nuc, ob = P["lamina"], P["nuc"], P["ob"]
    lector = _lector(lector)
    sello = P["sello"]
    M, _ = K.matriz_de_lamina(sello, lamina)
    lam = lector.abre(lamina)
    mpp = float(lam.mpp_l0)
    i0 = lector.i0_local(lam)
    zona = lector.zona_escaneada(lam)
    W, H = (int(v) for v in lam.dimensiones_l0)
    T_t = 1024
    anillo = int(math.ceil(Co.ANILLO_UM / mpp))
    marg = anillo + int(math.ceil(15.0 / mpp))
    comp = "anillo" if P["ctx"].compartimento == "anillo" else "nucleo"
    minx, miny, maxx, maxy = (int(v) for v in zona.bounds)
    cents, dabs, areas = [], [], []
    import shapely
    for y in range(miny, min(maxy, H), T_t):
        for x in range(minx, min(maxx, W), T_t):
            if not shapely.intersects(zona, shapely.box(x, y, x + T_t, y + T_t)):
                continue
            x0, y0 = max(0, x - marg), max(0, y - marg)
            x1, y1 = min(W, x + T_t + marg), min(H, y + T_t + marg)
            rgb = np.asarray(lector.lee_region(lam, mpp, x0, y0, x1 - x0, y1 - y0))[..., :3]
            od = Co.od(rgb, Co.i0_region(i0, x0, y0, x1 - x0, y1 - y0, mpp, mpp))
            lab, c, a, _ = S.detector_clasico(od.sum(-1), mpp)
            if not len(c):
                continue
            d = Co.desmezcla(od, M)[..., 1]
            et = expand_labels(lab, anillo) * (lab == 0) if comp == "anillo" else lab
            n = lab.max() + 1
            cnt = np.bincount(et.ravel(), minlength=n)
            s = np.bincount(et.ravel(), weights=d.ravel(), minlength=n)
            v = np.where(cnt[1:] > 0, s[1:] / np.maximum(cnt[1:], 1), np.nan)
            g = c + np.array([x0, y0])
            mio = (g[:, 0] >= x) & (g[:, 0] < x + T_t) & (g[:, 1] >= y) & (g[:, 1] < y + T_t)
            cents.append(g[mio])
            dabs.append(v[mio])
            areas.append(a[mio])
    cent = np.concatenate(cents) if cents else np.zeros((0, 2))
    dab = np.concatenate(dabs) if dabs else np.zeros(0)
    K.registra_lectura_diana(ruta_sello(base), lamina, "detector_clasico")
    # mismo marco y mismos denominadores: el del núcleo InstanSeg más próximo (≤5 µm)
    arbol = cKDTree(np.asarray(ob["centroide"], float) * mpp)
    dist, j = arbol.query(cent * mpp, k=1) if len(cent) else (np.zeros(0), np.zeros(0, int))
    cerca = dist <= 5.0
    j = np.where(cerca, j, 0)
    den = {k: np.asarray(v)[j] & cerca for k, v in nuc.den.items()}
    ids = np.where(cerca, nuc.fc[j], None)
    out = MET.Nucleos(lamina, nuc.xy[j], dab, ids, den, artefacto=nuc.artefacto[j] & cerca,
                      xy_l0=cent)
    return out


# ── GeoJSON y capa del visor ────────────────────────────────────────────────────────────────
def geojson(base, lamina, lector=None, log=print):
    """GeoJSON para QuPath (F4) con `laminillas_geojson`: núcleos clasificados con el T del
    contexto (sellado o elevado por el fondo), regiones con su % e IC en px L0 de ESTA lámina, y
    la capa editable del módulo (vacía: la zona p63 aún no existe)."""
    P = prepara(base, lamina, lector, log)
    import numpy as np
    import laminillas_geojson as G
    import laminillas_metricas as MET
    import laminillas_segmenta as S
    rel = _rel("geojson", "%s.geojson" % lamina)
    if C.esta_hecho(base, "geojson", lamina, P["huella"]):
        log("%s: GeoJSON ya hecho con estas entradas; salto" % lamina)
        return rel
    nuc, ctx, ob = P["nuc"], P["ctx"], P["ob"]
    pols = S.desempaqueta(np.asarray(ob["pol_xy"], float), np.asarray(ob["pol_offs"]))
    en_den = nuc.mascara(P["den"])
    feats = G.nucleos(pols, nuc.dab, ctx, en_den, area_um2=nuc.area,
                      extra={"Morphometric epithelial (rule)": P["morf"].astype(float),
                             "Artefact excluded": P["artefacto"].astype(float)})
    regs = P["regiones"] or {}
    reg_l0 = G.regiones_a_l0(regs, P["matrices"], P["mpp_l0"]) if regs else {}
    feats += G.regiones(regs, reg_l0, clopper=MET.clopper_pearson)
    feats += G.capa_editable(())
    fc = G.coleccion(feats, ctx, MARCADOR.get(lamina, lamina), n_capa_editable=0)
    os.makedirs(os.path.dirname(_abs(base, rel)), mode=0o700, exist_ok=True)
    G.escribe(_abs(base, rel), fc)
    os.chmod(_abs(base, rel), 0o600)
    C.marca_hecho(base, "geojson", lamina, P["huella"], productos=[rel])
    log("%s: GeoJSON (%d núcleos, %d regiones; capa editable «%s», vacía)" % (
        lamina, len(pols), len(reg_l0), G.CAPA_EDITABLE))
    return rel


def capa_visor(base, lamina, lector=None, log=print):
    """Capa del visor local (OpenSeadragon, instancia clínica): centroides en px L0 por clase
    (Positive / Negative según el T del contexto; Excluded = fuera del denominador o artefacto).
    No es N1: sale solo por `exporta-n1`."""
    P = prepara(base, lamina, lector, log)
    import numpy as np
    rel = _rel("visor", "%s.capa.json" % lamina)
    if C.esta_hecho(base, "visor", lamina, P["huella"]):
        return rel
    nuc, ctx = P["nuc"], P["ctx"]
    xy = np.rint(np.asarray(P["ob"]["centroide"], float)).astype(int)
    den = nuc.mascara(P["den"])
    pos = nuc.dab > ctx.T
    capa = {"lamina": lamina, "coordenadas": "pixels of level 0 of this slide",
            "T": ctx.T, "denominador": P["den"],
            "clases": {"Positive": xy[den & pos].tolist(), "Negative": xy[den & ~pos].tolist(),
                       "Excluded": xy[~den].tolist()},
            "nota": "local viewer layer (clinical instance); leaves only through exporta-n1"}
    _escribe(base, rel, capa)
    C.marca_hecho(base, "visor", lamina, P["huella"], productos=[rel])
    return rel


# ── (i) y (i-bis) ───────────────────────────────────────────────────────────────────────────
def piloto_i(base, lector=None, segmentador=None, log=print):
    """(i) P-KI67: QC, I0 local, InstanSeg sellado (rejilla desplazada; GrandQC si rige),
    objetos, % global y hotspot, GeoJSON y capa del visor. Sin (ii) o sin la regla morfométrica,
    deja hecho lo de la lámina y dice qué falta (código 5)."""
    nombre = "P-KI67"
    lector = _lector(lector)
    segmenta(base, [nombre], lector=lector, segmentador=segmentador, log=log)
    objetos(base, nombre, lector, log)
    qc = qc_lamina(base, nombre, lector, log)
    out = {"tipo": "piloto-i", "lamina": nombre, "qc": _rel("qc_%s.json" % nombre),
           "i0_pasa": (qc.get("i0") or {}).get("pasa"), "pendiente": None}
    try:
        m = metricas(base, nombre, lector, log)
        out["metricas"] = _rel("metricas", "%s.json" % nombre)
        res = m.get("resultado") or {}
        out["pct_global"] = (res.get("porcentaje") or {}).get("pct")
        out["hotspot_max_pct"] = (res.get("hotspot") or {}).get("maximo_pct")
        out["geojson"] = geojson(base, nombre, lector, log)
        out["visor"] = capa_visor(base, nombre, lector, log)
    except FaltaEntrada as e:
        out["pendiente"] = str(e)
        _escribe(base, _rel("piloto-i.json"), out)
        raise
    _escribe(base, _rel("piloto-i.json"), out)
    return out


def diagnostico_regla(base, log=print):
    """Solo NÚMEROS agregados de P-CK19 (denominador, no diana), para separar error de ejecución
    de dato antes del tribunal (2-oct, piloto real: 3,8 % de anillos CK19+ frente a 17 % de
    núcleos dentro de la máscara). Cruza pertenencia a la máscara × anillo > T_CK19, con y sin
    cada componente de artefacto, y cuantiles del DAB del anillo. No ajusta ni cambia nada."""
    import numpy as np
    ob = carga_objetos(base, REFERENCIA)
    mk = MascaraCK19(base)
    T = float(mk.info["umbral"]["T"])
    reg = _rige_efectivo(base, _sello(base, [REFERENCIA]))
    mpp = float(ob["mpp_l0"])
    cent = np.asarray(ob["centroide"], float).reshape(-1, 2)
    dentro = mk.dentro(cent * mpp)
    dab = np.asarray(ob["dab_anillo"], float)
    fin = np.isfinite(dab)
    anillo = fin & (dab > T)
    frag = np.asarray(ob["fragmento"]) >= 0
    comp = {"foco_bajo": np.asarray(ob["foco_bajo"], bool),
            "pliegue": np.asarray(ob["pliegue"], bool),
            "saturado": np.asarray(ob["saturado"], bool)}
    if "grandqc" in ob:
        comp["grandqc"] = np.asarray(ob["grandqc"], bool)
    art = _artefacto(ob, reg)

    def cruce(sel):
        n = int(sel.sum())
        return {"n": n, "dentro": int((sel & dentro).sum()), "anillo_pos": int((sel & anillo).sum()),
                "dentro_y_anillo": int((sel & dentro & anillo).sum()),
                "frac_dentro": float((sel & dentro).sum() / n) if n else None,
                "frac_anillo_pos": float((sel & anillo).sum() / n) if n else None}

    def q(v):
        v = v[np.isfinite(v)]
        return ({"n": int(len(v))} if not len(v) else
                dict(zip(("p10", "p25", "p50", "p75", "p90", "p99"),
                         [round(float(x), 4) for x in np.percentile(v, [10, 25, 50, 75, 90, 99])]),
                     n=int(len(v))))
    out = {"tipo": "diagnostico-regla", "lamina": REFERENCIA, "T_ck19": T, "regimen": reg,
           "todos": cruce(fin), "en_fragmento": cruce(fin & frag),
           "usados_por_la_regla": cruce(fin & frag & ~art),
           "artefacto_por_componente": {
               k: {"en_fragmento": int((fin & frag & v).sum()),
                   "de_ellos_dentro": int((fin & frag & v & dentro).sum()),
                   "de_ellos_anillo_pos": int((fin & frag & v & anillo).sum())}
               for k, v in comp.items()},
           "dab_anillo_dentro": q(dab[fin & frag & dentro]),
           "dab_anillo_fuera": q(dab[fin & frag & ~dentro]),
           "dab_nucleo_dentro": q(np.asarray(ob["dab_nucleo"], float)[fin & frag & dentro])}
    if "frac_recorte_anillo" in ob:                # regla v2: el recorte se anota, no excluye
        rec = np.nan_to_num(np.asarray(ob["frac_recorte_anillo"], float)) > 0
        out["recorte_anillo"] = {"en_fragmento": int((fin & frag & rec).sum()),
                                 "de_ellos_anillo_pos": int((fin & frag & rec & anillo).sum()),
                                 "de_ellos_dentro": int((fin & frag & rec & dentro).sum())}
    _escribe(base, _rel("diagnostico_regla.json"), out)
    log(json.dumps(out, indent=1, sort_keys=True))
    return out


def piloto_ibis(base, laminas=None, lector=None, segmentador=None, log=print):
    """(i-bis): InstanSeg sellado en P-CK19 y P-HE. En P-CK19, además, sus objetos, la máscara
    CK19+ (regla sellada) y la regla morfométrica: son el denominador de todo lo demás."""
    laminas = list(laminas or (REFERENCIA, "P-HE"))
    malas = sorted(set(laminas) - {REFERENCIA, "P-HE"})
    if malas:
        raise ValueError("piloto-ibis: solo P-CK19 y P-HE (no %s)" % ", ".join(malas))
    lector = _lector(lector)
    segmenta(base, laminas, lector=lector, segmentador=segmentador, log=log)
    out = {"tipo": "piloto-ibis", "laminas": laminas}
    if REFERENCIA in laminas:
        objetos(base, REFERENCIA, lector, log)
        mascara_ck19(base, lector, log)
        out["regla_morfometrica"] = {k: v for k, v in regla_morfometrica(base, log).items()
                                     if k in ("en_muestra", "validacion_por_fragmento", "n")}
    _escribe(base, _rel("piloto-ibis.json"), out)
    return out


# ── lista post-congelación (punto 5) ────────────────────────────────────────────────────────
PASA, RAMA, NO_PASA, NO_EVALUADO = "pasa", "rama", "no pasa", "no evaluado"


def _item_teselado(L):
    """Teselado e InstanSeg: las comprobaciones del punto 2, repetidas en P-KI67; fuera, se para."""
    import laminillas_segmenta as S
    nuc = nucleos_lamina(L["base"], "P-KI67")
    ob = carga_objetos(L["base"], "P-KI67")
    r = nuc["resumen"]
    t = r.get("teselado") or {}
    pesos = r.get("pesos") or {}
    motivos = []
    if not t.get("pasa"):
        motivos.append("teselado (nº, descartadas o tile_spec.mpp)")
    if r.get("aviso_check_input_tile"):
        motivos.append("aviso de check_input_tile")
    if pesos.get("sha256") != S.INSTANSEG["sha256"] or pesos.get("bytes") != S.INSTANSEG["bytes"]:
        motivos.append("pesos de InstanSeg sin verificar contra el sha256 sellado")
    if "centroide_desplazada" not in nuc:
        motivos.append("sin rejilla desplazada (segmenta P-KI67 la corre por defecto)")
        rej = None
    else:
        import numpy as np
        prov = S.mascara_provisional(nuc["centroide"], nuc["mpp_l0"], tuple(ob["dimensiones_l0"]))
        rej = S.cambio_rejilla(nuc["centroide"], np.asarray(nuc["centroide_desplazada"]), prov)
        if not rej["pasa"]:
            motivos.append("rejilla desplazada 128 px: cambio ≥ 1 % en algún fragmento")
    ac = r.get("acuerdo_area")
    if not ac:
        motivos.append("sin la puerta del área nuclear (acuerdo InstanSeg/detector B): "
                       "`segmenta P-KI67` la mide")
    elif not ac.get("pasa"):
        lo, hi = S.COMPROBACION["acuerdo_area"]
        motivos.append("área nuclear: mediana InstanSeg/B %s con %s pares (puerta %.2f-%.2f con "
                       "≥ %d)" % (ac.get("mediana"), ac.get("n_pares"), lo, hi,
                                  S.COMPROBACION["acuerdo_area_min_pares"]))
    return {"estado": NO_PASA if motivos else PASA, "motivos": motivos,
            "criterio": ("nº de teselas = esperado ±1 %, 0 descartadas, tile_spec.mpp 0,5 ±0,01; "
                         "sin aviso de check_input_tile; rejilla desplazada 128 px con cambio "
                         "<1 % por fragmento; " + S.CRITERIO_AREA),
            "teselado": t, "rejilla_desplazada": rej, "acuerdo_area": ac,
            "area_nuclear": r.get("area_nuclear")}


def recall_por_clase(base, lector, sello, nombre="P-KI67"):
    """(1) cobertura por InstanSeg de los objetos del detector clásico, DAB+ (T sobre DAB OD) y
    solo-hematoxilina, por fragmento y tercil de foco; (2) el detector como segmentador B: cociente
    y F1 a IoU 0,5 por clase. Teselas al azar (semilla sellada) a L0."""
    import numpy as np
    import laminillas_color as Co
    import laminillas_congela as K
    import laminillas_metricas as MET
    import laminillas_segmenta as S
    nuc = nucleos_lamina(base, nombre)
    ob = carga_objetos(base, nombre)
    lam = lector.abre(nombre)
    mpp = float(lam.mpp_l0)
    i0 = lector.i0_local(lam)
    M, _ = K.matriz_de_lamina(sello, nombre)
    T = MET.Contexto(ruta_sello(base), nombre, regimen=_regimen_vigente(base)).T
    pols = S.desempaqueta(nuc["pol_xy"], nuc["pol_offs"])
    import shapely
    arbol = shapely.STRtree(pols)
    cent = np.asarray(ob["centroide"], float)
    fr = np.asarray(ob["fragmento"])
    ft = np.asarray(ob["foco_tesela"], float)
    tt = np.asarray(ob["tejido_tesela"], float)
    ok = (tt >= 0.5) & np.isfinite(ft)
    cortes = np.percentile(ft[ok], [100 / 3, 200 / 3]) if ok.sum() >= 3 else None
    rng = np.random.default_rng(_semilla(sello, PARAMS["semilla_recall"]))
    cand = np.nonzero(fr >= 0)[0]
    lado = PARAMS["recall_lado_px"]
    W, H = (int(v) for v in lam.dimensiones_l0)
    elegidos = rng.choice(cand, min(PARAMS["recall_teselas"], len(cand)), replace=False) \
        if len(cand) else []
    acum, f1s = {}, {"dab": [], "h": []}
    for i in elegidos:
        x0 = int(min(max(0, cent[i, 0] - lado / 2), W - lado))
        y0 = int(min(max(0, cent[i, 1] - lado / 2), H - lado))
        rgb = np.asarray(lector.lee_region(lam, mpp, x0, y0, lado, lado))[..., :3]
        conc = Co.desmezcla(Co.od(rgb, Co.i0_region(i0, x0, y0, lado, lado, mpp, mpp)), M)
        lab_d, c_d, _, _ = S.detector_clasico(conc[..., 1], mpp, umbral=T)
        lab_h, c_h, _, _ = S.detector_clasico(conc[..., 0], mpp)
        if lab_h.max():
            dm = np.bincount(lab_h.ravel(), weights=conc[..., 1].ravel(), minlength=lab_h.max() + 1)
            cn = np.bincount(lab_h.ravel(), minlength=lab_h.max() + 1)
            solo_h = (dm[1:] / np.maximum(cn[1:], 1)) <= T
        else:
            solo_h = np.zeros(0, bool)
        lab_h_solo = np.where(np.isin(lab_h, np.nonzero(solo_h)[0] + 1), lab_h, 0)
        vec = np.asarray(arbol.query(shapely.box(x0, y0, x0 + lado, y0 + lado)), np.int64)
        lab_i = S.rasteriza([pols[j] for j in vec], x0, y0, (lado, lado))
        t_idx = int(ob["tesela"][i])
        foco = ft[t_idx] if 0 <= t_idx < len(ft) else np.nan
        tercil = ("?" if cortes is None or not np.isfinite(foco) else
                  "bajo" if foco < cortes[0] else "medio" if foco < cortes[1] else "alto")
        e = "F%d|foco %s" % (int(fr[i]), tercil)
        a = acum.setdefault(e, {"dab": [0, 0], "h": [0, 0]})
        for clase, cs in (("dab", c_d), ("h", c_h[solo_h] if len(c_h) else c_h)):
            if len(cs):
                cub = S.cobertura(cs, lab_i)
                a[clase][0] += int(round(cub * len(cs)))
                a[clase][1] += len(cs)
        f1s["dab"].append(S.f1_iou(lab_d, lab_i))
        f1s["h"].append(S.f1_iou(lab_h_solo, lab_i))
    cobertura, fuera = {}, []
    for e, a in acum.items():
        if min(a["dab"][1], a["h"][1]) < PARAMS["recall_min_objetos"]:
            fuera.append({"estrato": e, "n_dab": a["dab"][1], "n_h": a["h"][1]})
            continue
        cobertura[e] = {"dab": a["dab"][0] / a["dab"][1], "h": a["h"][0] / a["h"][1]}

    def agrega(lista):
        tp = sum(x["tp"] for x in lista)
        na = sum(x["n_a"] for x in lista)
        nb = sum(x["n_b"] for x in lista)
        return {"tp": tp, "n_clasico": na, "n_instanseg": nb,
                "f1": 2 * tp / (na + nb) if na + nb else None,
                "cociente_instanseg_clasico": nb / na if na else None}
    return {"cobertura": cobertura, "estratos_fuera": fuera, "T": T,
            "f1_iou05": {k: agrega(v) for k, v in f1s.items()},
            "n_teselas": len(elegidos), "declaracion": DECLARACIONES["recall"]}


def _densidades_fc(base):
    """(3) núcleos dentro del MISMO FC llevado a KI67, CK19 y HER2NEG / área del FC."""
    import numpy as np
    fcs = carga_fcs(base)
    area = {f["id"]: f["area_um2"] / 1e6 for f in fcs}
    out, decl = {}, []
    cuentas = {}
    ob_ck = carga_objetos(base, REFERENCIA)
    ids_ck = fc_de(fcs, np.asarray(ob_ck["centroide"], float) * float(ob_ck["mpp_l0"]))
    cuentas[REFERENCIA] = ids_ck
    for n in ("P-KI67", "P-HER2NEG"):
        try:
            res, _ = resultado_desde_disco(base, n)
        except FaltaEntrada:
            decl.append("%s: no pair registration" % n)
            continue
        nuc = nucleos_lamina(base, n)
        if res.global_.get("matriz_um") is None:
            continue
        _, ids = res.nucleos_a_referencia(nuc["centroide"], nuc["mpp_l0"])
        cuentas[n] = ids
    for fid, a in area.items():
        ds = {}
        for n, ids in cuentas.items():
            k = int(np.sum(ids == fid))
            if n != REFERENCIA and k == 0:
                continue                                  # FC no verificado en ese par
            ds[n] = k / a if a else float("nan")
        if "P-HER2NEG" not in ds:
            decl.append("%s: HER2NEG not verified in (ii-bis): KI67 vs CK19 only" % fid)
        if len(ds) >= 2:
            out[fid] = ds
    return out, decl


def _item_recall(L):
    import laminillas_segmenta as S
    base = L["base"]
    r = recall_por_clase(base, L["lector"], L["sello"])
    dens, decl = _densidades_fc(base)
    g = S.puerta_deteccion(r["cobertura"], {k: list(v.values()) for k, v in dens.items()})
    det = {"tipo": "deteccion", "laminas": {"P-KI67": {"deteccion_ok": g["pasa"],
                                                        "fallos": g["fallos"]}}}
    _escribe(base, _rel("deteccion.json"), det)
    return {"estado": PASA if g["pasa"] else RAMA,
            "criterio": ("(1) cobertura DAB+ vs solo-H por fragmento y tercil de foco, diferencia "
                         "<5 puntos; (3) densidad en el mismo FC entre KI67, CK19 y HER2NEG, "
                         "diferencia <15 %; si no: % como rango [InstanSeg; detector clásico], "
                         "«detection-dependent»"),
            "recall": r, "densidad_por_fc": dens, "declaraciones": decl, "puerta": g,
            "rama": None if g["pasa"] else "detection-dependent: % as a range in metricas"}


def _item_grandqc(L):
    import numpy as np
    import laminillas_metricas as MET
    base, sello = L["base"], L["sello"]
    criterio = ("si la fracción marcada por GrandQC difiere >5 puntos entre núcleos DAB+ y DAB− "
                "de P-KI67, rigen las reglas clásicas y su T sellado en todas las IHQ")
    if _rige_sellado(sello) != "grandqc":
        return {"estado": PASA, "criterio": criterio, "rige": "clasicas",
                "nota": "GrandQC not in use (sealed): classical rules"}
    nuc = nucleos_lamina(base, "P-KI67")
    info = (nuc["resumen"].get("grandqc") or {})
    ob = carga_objetos(base, "P-KI67")
    if not info.get("carga") or "grandqc" not in ob:
        cambia, motivo = True, "GrandQC did not load on P-KI67 (%s)" % info.get("motivo")
        detalle = None
    else:
        T = MET.Contexto(ruta_sello(base), "P-KI67", regimen="grandqc").T
        sel = np.asarray(ob["fragmento"]) >= 0
        pos = np.asarray(ob["dab_nucleo"]) > T
        g = np.asarray(ob["grandqc"], bool)
        fp = float(g[sel & pos].mean()) if (sel & pos).any() else 0.0
        fn = float(g[sel & ~pos].mean()) if (sel & ~pos).any() else 0.0
        dif = abs(fp - fn) * 100
        cambia = dif > PARAMS["grandqc_dif_puntos"]
        motivo = "difference %.1f points between DAB+ and DAB- nuclei" % dif
        detalle = {"marcado_dab_pos": fp, "marcado_dab_neg": fn, "diferencia_puntos": dif,
                   "T": T}
    rige = "clasicas" if cambia else "grandqc"
    _escribe(base, _rel("regimen.json"), {"regimen": rige, "motivo": motivo,
                                          "sello": sello.sha256})
    return {"estado": RAMA if cambia else PASA, "criterio": criterio, "rige": rige,
            "motivo": motivo, "detalle": detalle}


def _item_ck19(L):
    import laminillas_color as Co
    mk = _lee(L["base"], _rel("ck19", "mascara.json")) if os.path.isfile(
        _abs(L["base"], _rel("ck19", "mascara.json"))) else None
    if mk is None:
        raise FaltaEntrada("falta la máscara CK19: corre antes `piloto-ibis P-CK19`")
    dep = bool((mk.get("sensibilidad") or {}).get("dependiente"))
    return {"estado": RAMA if dep or mk["umbral"].get("regla") != "valle" else PASA,
            "criterio": ("bimodalidad (valle ≤0,5× el pico menor; si no, Otsu y «CK19 threshold "
                         "not bimodal») y sensibilidad ×0,75/×1,25 (>10 % en un fragmento: "
                         "«denominator-dependent»); galería N1 de 20 campos en el borde"),
            "umbral": mk["umbral"], "sensibilidad": mk["sensibilidad"],
            "rotulos": mk.get("rotulos"), "galeria_borde": mk.get("galeria_borde"),
            "metodos": Co.ROTULOS["ck19_sin_verdad"]}


def _item_regla(L):
    criterio = ("regla morfométrica frente al anillo CK19+ de esa célula, en P-CK19: sensibilidad, "
                "especificidad y AUC por fragmento. El plan no fija cifra: se informa y no "
                "bloquea, SALVO regla degenerada (no marca ninguna célula CK19+), que es error "
                "de ejecución (2-oct; inferencia mía)")
    try:
        r = carga_regla(L["base"])
    except NoPasa as e:
        return {"estado": NO_PASA, "criterio": criterio, "motivos": [str(e)]}
    return {"estado": PASA, "criterio": criterio,
            "validacion_por_fragmento": r["validacion_por_fragmento"],
            "en_muestra": r["en_muestra"], "auc_en_muestra": r.get("auc_en_muestra"),
            "corte": r.get("corte"), "criterio_corte": r.get("criterio_corte"),
            "declaracion": r["declaracion"], "declaracion_corte": r.get("declaracion_corte")}


def _item_fp(L):
    s = L["sello"]
    fp = {c: (s.d.get("fp_her2") or {}).get(c) for c in ("nucleo", "anillo")}
    u = s.d.get("umbral") or {}
    alto = any(float(v) > 0.25 for v in (u.get("T") or {}).values())
    hf = "high floor" in (u.get("rotulos") or [])
    ok = (not alto) or hf
    return {"estado": PASA if ok else NO_PASA,
            "criterio": ("falsos positivos en P-HER2 fuera de muestra (k, n, IC95); en HER2NEG "
                         "solo T ≤ 0,25 o «high floor»"),
            "fp_her2": fp, "T": u.get("T"), "high_floor": hf,
            "motivos": [] if ok else ["T > 0.25 sin «high floor»"]}


def _mascaras_tejido(base, nombre, factor=1.0):
    """{candidato: (mascara bool, f L0/px)} de una lámina: densidad nuclear InstanSeg
    (máscara provisional, umbral × factor) y textura (desviación local de ODsum a 8 µm, umbral de
    la ingesta × factor). GrandQC-tejido no tiene envoltorio en `laminillas_segmenta`."""
    import numpy as np
    import laminillas_segmenta as S
    nuc = nucleos_lamina(base, nombre)
    ob = carga_objetos(base, nombre)
    prov = S.mascara_provisional(nuc["centroide"], nuc["mpp_l0"], tuple(ob["dimensiones_l0"]),
                                 umbral=S.MASCARA_PROV["umbral_nucleos_mm2"] * factor)
    out = {"densidad_instanseg": (prov["mascara"], float(prov["f"]))}
    rel8 = os.path.join("mascaras", nombre + "-8um.npz")
    man = (C.lee_manifiesto(base).get("laminas") or {}).get(nombre) or {}
    tej = man.get("tejido") or {}
    if os.path.isfile(_abs(base, rel8)) and tej.get("umbral_textura") is not None:
        z = _carga_npz(base, rel8)
        tex = np.asarray(z["textura"], float) > float(tej["umbral_textura"]) * factor
        out["textura"] = (tex & np.asarray(z["dentro"], bool), float(z["escala_l0"]))
    return out


def _area_por_fc(base, nombre, mask, f, fcs):
    """Área (µm²) de `mask` (rejilla de f px L0) llevada a la referencia, en la vecindad de cada
    FC (FC dilatado PARAMS["tejido_vecindad_um"])."""
    import numpy as np
    from scipy import ndimage as ndi
    yy, xx = np.nonzero(mask)
    xy_l0 = np.column_stack([(xx + 0.5) * f, (yy + 0.5) * f])
    nuc = nucleos_lamina(base, nombre)
    mpp_l0 = float(nuc["mpp_l0"])
    if nombre == REFERENCIA:
        xy = xy_l0 * mpp_l0
    else:
        res, _ = resultado_desde_disco(base, nombre)
        if res.global_.get("matriz_um") is None:
            return {}
        Mg = np.asarray(res.global_["matriz_um"], float)
        xy = (xy_l0 * mpp_l0) @ Mg[:2, :2].T + Mg[:2, 2]
    area_px = (f * mpp_l0) ** 2
    out = {}
    for fc in fcs:
        r = int(math.ceil(PARAMS["tejido_vecindad_um"] / fc["mpp"]))
        vec = ndi.binary_dilation(fc["mascara"], iterations=r)
        c = np.floor(xy[:, 0] / fc["mpp"]).astype(int)
        q = np.floor(xy[:, 1] / fc["mpp"]).astype(int)
        ok = (c >= 0) & (q >= 0) & (c < vec.shape[1]) & (q < vec.shape[0])
        dentro = np.zeros(len(xy), bool)
        dentro[ok] = vec[q[ok], c[ok]]
        out[fc["id"]] = float(dentro.sum() * area_px)
    return out


def _item_tejido(L):
    base = L["base"]
    fcs = carga_fcs(base)
    areas = {}
    for n in ("P-KI67", REFERENCIA):
        for k_f, factor in (("base", 1.0),) + tuple(("x%g" % v, v) for v in PARAMS["tejido_factores"]):
            for cand, (m, f) in _mascaras_tejido(base, n, factor).items():
                areas.setdefault(cand, {}).setdefault(n, {})[k_f] = _area_por_fc(base, n, m, f, fcs)
    ganador, tabla = {}, {}
    for fc in fcs:
        fid = fc["id"]
        tabla[fid] = {}
        for cand, por in areas.items():
            try:
                a_k, a_c = por["P-KI67"]["base"][fid], por[REFERENCIA]["base"][fid]
            except KeyError:
                continue
            dif = abs(a_k - a_c) / max(a_k, a_c) if max(a_k, a_c) > 0 else float("inf")
            mov = max(abs(por[n][kf][fid] - por[n]["base"][fid]) / por[n]["base"][fid]
                      if por[n]["base"][fid] else float("inf")
                      for n in ("P-KI67", REFERENCIA) for kf in por[n] if kf != "base")
            ok = dif < PARAMS["tejido_dif_cortes"] and mov < PARAMS["tejido_dif_umbral"]
            tabla[fid][cand] = {"area_ki67_um2": a_k, "area_ck19_um2": a_c,
                                "diferencia_cortes": dif, "cambio_umbral_max": mov, "pasa": ok}
        buenos = sorted((c for c, v in tabla[fid].items() if v["pasa"]),
                        key=lambda c: tabla[fid][c]["diferencia_cortes"])
        ganador[fid] = buenos[0] if buenos else None
    sin = [k for k, v in ganador.items() if v is None]
    return {"estado": RAMA if sin else PASA,
            "criterio": ("por fragmento gana la máscara con diferencia de área <15 % entre los dos "
                         "cortes y <10 % al mover su umbral; si ninguna, áreas como rango y "
                         "fragmentos emparejados por centroides y forma"),
            "ganador": ganador, "tabla": tabla, "fragmentos_como_rango": sin,
            "declaraciones": ["GrandQC tissue: no wrapper in laminillas_segmenta (not compared)",
                              DECLARACIONES["tejido"],
                              "to be confirmed on SYN<->CHGA at the extension"]}


def _item_erosion(L):
    import numpy as np
    import laminillas_metricas as MET
    base = L["base"]
    _, dii = estado_ii(base)
    if dii is None:
        raise FaltaEntrada("falta (ii): registro-par P-KI67 P-CK19")
    tre = dii.get("tre_p90_um")
    ck = MascaraCK19(base)
    ob = carga_objetos(base, REFERENCIA)
    xy = np.asarray(ob["centroide"], float) * float(ob["mpp_l0"])
    regla = carga_regla(base)
    den = {"ck19_erosionada": ck.erosionada(xy, tre), "ck19_sin_erosionar": ck.dentro(xy),
           "morfometrico": aplica_regla(regla, ob["rasgos"])}
    reg = _rige_efectivo(base, L["sello"])
    nuc = MET.Nucleos(REFERENCIA, xy, np.asarray(ob["dab_anillo"], float), fc_de(carga_fcs(base), xy),
                      den, artefacto=_artefacto(ob, reg))
    sv = MET.supervivencia_erosion(nuc, MET.Contexto(ruta_sello(base), REFERENCIA,
                                                     regimen=_regimen_vigente(base)))
    morf = [f for f, v in sv.items() if v["principal"] == "morfometrico"]
    return {"estado": RAMA if morf else PASA,
            "criterio": ("supervivencia por fragmento de los núcleos CK19+ a la erosión de 1× TRE; "
                         "<70 %: la cifra principal de ese fragmento pasa a la regla morfométrica"),
            "tre_p90_um": tre, "supervivencia": sv, "fragmentos_morfometricos": morf}


def _item_tre(L):
    _, dii = estado_ii(L["base"])
    if dii is None:
        raise FaltaEntrada("falta (ii): registro-par P-KI67 P-CK19")
    malos, rige = [], {}
    for f in (dii.get("resumen") or {}).get("fragmentos", []):
        ev = f.get("evaluacion") or {}
        rige[f["id"]] = ev.get("rige")
        if f.get("pasa") and ev.get("rige") not in ("b", "b_prima"):
            malos.append(f["id"])
    return {"estado": NO_PASA if malos else PASA,
            "criterio": "si (b) no da pico válido en KI67↔CK19 rige (b'); nunca el residuo de inliers",
            "rige_por_fc": rige, "motivos": ["%s: TRE not from (b)/(b')" % m for m in malos]}


def _item_color(L):
    import numpy as np
    import laminillas_color as Co
    import laminillas_congela as K
    base, lector, s = L["base"], L["lector"], L["sello"]
    motivos, i0s = [], {}
    for n in ("P-KI67", REFERENCIA, "P-HE"):
        lam = lector.abre(n)
        r = K.revisa_i0(lector, lam, n, lector.i0_local(lam))
        i0s[n] = r
        if not r.get("pasa"):
            motivos.append("%s: I0 local con patrón por franja" % n)
    res = {}
    for n in K.TANDA:
        r = K.residuo_de(s, n)
        res[n] = {"supera": bool(r.get("supera")), "residuo": r.get("residuo"),
                  "umbral": (s.d.get("residuo") or {}).get("umbral")}
        if r.get("supera"):
            motivos.append("%s: residuo sobre el umbral sellado" % n)
    pre = nucleos_lamina(base, "P-HER2NEG")
    z = _carga_npz(base, "objetos_P-HER2NEG.npz") if pre["origen"] == "precongelacion" else None
    if z is not None:
        M, _ = K.matriz_de_lamina(s, "P-HER2NEG")
        od = np.asarray(z["od_nucleo"], float)
        od = od[np.isfinite(od).all(axis=1) & (np.asarray(z["fragmento"]) >= 0)]
        h_neg = float(np.median(Co.desmezcla(od, M)[:, 0]))
    else:
        ob_n = objetos(base, "P-HER2NEG", lector)
        h_neg = float(np.nanmedian(np.asarray(ob_n["hema"])[np.asarray(ob_n["fragmento"]) >= 0]))
    hema = {"P-HER2NEG": h_neg}
    # INFORMATIVO para el tribunal (2-oct, no cambia el criterio): la mediana de hematoxilina en
    # las dianas mezcla núcleos con DAB (Ki67 nuclear; CK19 que pisa el núcleo a L0), y la
    # desmezcla tiene diafonía H-DAB; restringida a núcleos DAB-negativos (DAB del núcleo ≤ T
    # nuclear sellado) separa «otra contratinción» de «diafonía del DAB».
    T_n = float(((s.d.get("umbral") or {}).get("T") or {}).get("nucleo") or 0.10)
    solo_neg = {}
    for n in ("P-KI67", REFERENCIA):
        ob = carga_objetos(base, n)
        hv = np.asarray(ob["hema"], float)
        fr = np.asarray(ob["fragmento"]) >= 0
        h = float(np.nanmedian(hv[fr]))
        hema[n] = h
        if not h or abs(h_neg / h - 1) > PARAMS["hema_tolerancia"]:
            motivos.append("hematoxilina nuclear de HER2NEG a %.0f %% de %s (tolerancia ±20 %%)"
                           % (100 * (h_neg / h - 1) if h else float("inf"), n))
        neg = fr & np.isfinite(hv) & (np.asarray(ob["dab_nucleo"], float) <= T_n)
        hn = float(np.median(hv[neg])) if neg.any() else None
        solo_neg[n] = {"n": int(neg.sum()), "mediana": hn,
                       "her2neg_relativo_pct": (100 * (h_neg / hn - 1)) if hn else None}
    return {"estado": NO_PASA if motivos else PASA,
            "criterio": ("I0 sin patrón por franja en KI67, CK19 y HE; residuo de las cinco de "
                         "tanda bajo el umbral sellado; hematoxilina nuclear de HER2NEG a ±20 % de "
                         "la diana (mediana, KI67 y CK19)"),
            "i0": i0s, "residuo": res, "hema_mediana": hema, "motivos": motivos,
            "informativo_hema_nucleos_dab_negativos": dict(
                solo_neg, T_nucleo=T_n,
                nota="does not change the criterion; separates counterstain difference from "
                     "H-DAB unmixing crosstalk in DAB-positive nuclei")}


def _item_solo_piloto(L):
    return {"estado": PASA, "criterio": "solo-piloto de esta ronda: ninguno recibido",
            "nota": "none received"}


ITEMS_LISTA = (("teselado_instanseg", _item_teselado), ("recall_por_clase", _item_recall),
               ("grandqc_ki67", _item_grandqc), ("mascara_ck19", _item_ck19),
               ("regla_morfometrica", _item_regla), ("falsos_positivos", _item_fp),
               ("mascara_tejido", _item_tejido), ("erosion_ck19", _item_erosion),
               ("tre", _item_tre), ("color", _item_color), ("solo_piloto", _item_solo_piloto))


def corre_lista(contexto, items=ITEMS_LISTA, log=print):
    """Ítem a ítem, en el orden del plan; al primer «no pasa» (o a la primera entrada que falta)
    se para: lo que queda, «no evaluado». Devuelve el informe."""
    filas, parado = [], None
    for nombre, fn in items:
        if parado:
            filas.append({"item": nombre, "estado": NO_EVALUADO,
                          "motivo": "la lista se paró en «%s»" % parado})
            continue
        try:
            r = fn(contexto)
        except FaltaEntrada as e:
            r = {"estado": NO_PASA, "motivos": ["falta: %s" % e]}
        r = dict(r, item=nombre)
        filas.append(r)
        log("  %-20s %s" % (nombre, r["estado"]))
        if r["estado"] == NO_PASA:
            parado = nombre
    return {"tipo": "lista-postcongelacion", "pasa": parado is None, "parada_en": parado,
            "items": filas,
            "ramas": [f["item"] for f in filas if f["estado"] == RAMA]}


def lista_postcongelacion(base, lector=None, log=print):
    rel = _rel("lista_postcongelacion.json")
    sello = _sello(base, ["P-KI67", REFERENCIA])
    lector = _lector(lector)
    inf = corre_lista({"base": base, "lector": lector, "sello": sello}, log=log)
    inf["sello"] = sello.sha256
    inf["fecha"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _escribe(base, rel, inf)
    return inf


# ── tribunal-listo (requisito BLOQUEANTE 5-bis) ─────────────────────────────────────────────
CALIB_SINTETICA = os.path.join("tools", "panel_vision", "calibracion_sintetica.json")
AUDITORIAS = os.path.join("tools", "panel_vision", "auditorias.json")
N1_PANEL = "panel_vision"
CALIB_N1 = "calibracion_n1.json"
CAPAS_PILOTO = "capas_piloto.json"
REVISIONES = "revisiones.jsonl"
GEMINI_NO = "Gemini not used: provider not yet trusted"
DESTINO_GEMINI = "vision-n1:gemini"


def _es_gemini(clave):
    return str(clave).lower().split(":", 1)[0].startswith("gemini")


def _confia_por_defecto(destino):
    """¿`destino` confiado con `trust-cloud` tecleado (via tty, cadena íntegra, sin HALT)? Lo mismo
    que exige `vision_n1` antes de enviar (`puerta_n1.exigir_confianza`). Cualquier fallo: no."""
    try:
        import puerta_n1
        puerta_n1.exigir_confianza(destino, "vision-n1:")
        return True
    except (Exception, SystemExit):                            # noqa: BLE001
        return False


def _carga_calibra():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel_vision"))
    import calibra
    return calibra


def _lee_json(ruta):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _calibracion_vale(calibra, d):
    """Motivo por el que una calibración no vale (None si vale): protocolo y preguntas vigentes
    y al menos un modelo puntuado."""
    if not isinstance(d, dict):
        return "no existe o no es JSON"
    if d.get("protocolo") != calibra.PROTOCOLO:
        return "protocolo %r, vigente %r" % (d.get("protocolo"), calibra.PROTOCOLO)
    if d.get("huella_preguntas") != calibra.huella_preguntas():
        return "preguntas de otra versión (huella distinta de la vigente)"
    if not d.get("modelos"):
        return "sin ningún modelo puntuado"
    return None


def _auditoria_apta(aud):
    """¿La auditoría deja usar el proveedor? «apto» a secas, o «apto con condiciones» solo si
    consta que las condiciones se cumplen (`condiciones_cumplidas: true`, que se escribe cuando
    {{TITULAR}} confirma las suyas: en Gemini, nivel de pago y logging apagado en AI Studio). Una
    auditoría con condiciones pendientes no habilita: el tribunal sigue sin ese proveedor y lo
    declara (2-oct-26)."""
    veredicto = str(aud.get("veredicto", "")).strip().lower()
    if veredicto == "apto":
        return True
    return veredicto == "apto con condiciones" and aud.get("condiciones_cumplidas") is True


def tribunal_listo(repo=None, n1=None, calibra=None, confia=None, log=print):
    """Requisito 5-bis del plan, por código, antes de lanzar a los jueces. BLOQUEA si falta:
      (i)  `tools/panel_vision/calibracion_sintetica.json` (formato `calibra.puntua_conjunto`,
           protocolo y preguntas vigentes) y, DESPUÉS, la calibración sobre capas N1 reales con
           errores sembrados: `~/Laminillas-N1/panel_vision/calibracion_n1.json` (mismo formato,
           otro conjunto, puntuada después);
      (ii) que cada modelo habilitado por la calibración N1 (`calibra.modelos_autorizados`, que
           recalcula desde los recuentos) haya revisado TODAS las capas N1 del piloto de cada
           tarea en la que demostró detectar errores: `panel_vision/capas_piloto.json`
           ({"tareas": {tarea: [fichero N1, …]}}, cada fichero con su sha256 en el manifiesto N1)
           y `panel_vision/revisiones.jsonl` (una línea por revisión: clave, tarea, fichero,
           sha256; con «error», no cuenta).
    (iii) Gemini entra solo con la auditoría «apto» (`tools/panel_vision/auditorias.json`,
          {"gemini": {"veredicto": "apto", …}}) y `trust-cloud vision-n1:gemini` tecleado por
          {{TITULAR}} (`confia`); si falta, NO bloquea: sale del panel y se declara «Gemini not used:
          provider not yet trusted».
    Devuelve el informe; `listo` es False si falta (i) o (ii)."""
    repo = repo or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    n1 = n1 or os.path.expanduser("~/Laminillas-N1")
    confia = confia or _confia_por_defecto
    inf = {"tipo": "tribunal-listo", "requisito": "5-bis", "faltan": [], "avisos": [],
           "declaraciones": [], "i": {}, "ii": {}, "iii": {}}
    if calibra is None:
        try:
            calibra = _carga_calibra()
        except Exception as e:                                  # noqa: BLE001
            inf["faltan"].append("(i) sin el arnés de calibración (tools/panel_vision/calibra.py): "
                                 "%s" % type(e).__name__)
            inf["listo"] = False
            return inf
    sint = _lee_json(os.path.join(repo, CALIB_SINTETICA))
    mal = _calibracion_vale(calibra, sint)
    inf["i"]["sintetica"] = {"fichero": CALIB_SINTETICA, "vale": mal is None, "motivo": mal}
    if mal:
        inf["faltan"].append("(i) calibración sintética (%s): %s" % (CALIB_SINTETICA, mal))
    real = _lee_json(os.path.join(n1, N1_PANEL, CALIB_N1))
    mal_r = _calibracion_vale(calibra, real)
    if mal_r is None and sint:
        if real.get("sha_conjunto") and real.get("sha_conjunto") == sint.get("sha_conjunto"):
            mal_r = "es el mismo conjunto que la sintética, no capas N1 reales"
        elif str(real.get("puntuado") or "") <= str(sint.get("puntuado") or ""):
            mal_r = "puntuada antes que la sintética (el plan: después)"
    inf["i"]["n1_real"] = {"fichero": os.path.join(N1_PANEL, CALIB_N1), "vale": mal_r is None,
                           "motivo": mal_r}
    if mal_r:
        inf["faltan"].append("(i) calibración sobre capas N1 reales con errores sembrados "
                             "(%s/%s): %s" % (N1_PANEL, CALIB_N1, mal_r))
    if inf["faltan"]:
        inf["ii"] = {"evaluado": False, "motivo": "(i) no se cumple"}
        inf["listo"] = False
        return inf
    habilitados = {t: list(calibra.modelos_autorizados(real, t)) for t in calibra.TAREAS}
    # (iii) Gemini
    con_gemini = sorted({m for ms in habilitados.values() for m in ms if _es_gemini(m)})
    aud = _lee_json(os.path.join(repo, AUDITORIAS)) or {}
    apto = _auditoria_apta(aud.get("gemini") or {})
    confiado = bool(confia(DESTINO_GEMINI)) if con_gemini or apto else False
    usa_gemini = bool(con_gemini) and apto and confiado
    inf["iii"] = {"gemini_habilitado_por_calibracion": con_gemini, "auditoria_apto": apto,
                  "trust_cloud_tty": confiado, "usa_gemini": usa_gemini}
    if not usa_gemini:
        habilitados = {t: [m for m in ms if not _es_gemini(m)] for t, ms in habilitados.items()}
        inf["iii"]["rotulo"] = GEMINI_NO
        inf["declaraciones"].append(GEMINI_NO)
    inf["ii"]["habilitados"] = habilitados
    # (ii) revisiones
    capas = (_lee_json(os.path.join(n1, N1_PANEL, CAPAS_PILOTO)) or {}).get("tareas") or {}
    man = (_lee_json(os.path.join(n1, "manifiesto.json")) or {}).get("ficheros") or {}
    hechas = set()
    try:
        with open(os.path.join(n1, N1_PANEL, REVISIONES), encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(r, dict) and not r.get("error"):
                    hechas.add((r.get("clave"), r.get("tarea"), r.get("sha256")))
    except OSError:
        pass
    falta_rev = []
    for tarea, mods in sorted(habilitados.items()):
        if not mods:
            inf["avisos"].append("%s: ningún modelo demostró detectar errores: va sin panel" % tarea)
            continue
        lista = capas.get(tarea) or []
        if not lista:
            inf["faltan"].append("(ii) %s: sin capas N1 del piloto declaradas (%s/%s)"
                                 % (tarea, N1_PANEL, CAPAS_PILOTO))
            continue
        for fich in lista:
            sha = (man.get(fich) or {}).get("sha256")
            if not sha:
                inf["faltan"].append("(ii) %s: %s no está en el manifiesto N1" % (tarea, fich))
                continue
            for m in mods:
                if (m, tarea, sha) not in hechas:
                    falta_rev.append({"modelo": m, "tarea": tarea, "fichero": fich})
    if falta_rev:
        inf["faltan"].append("(ii) %d revisiones de capas N1 del piloto sin hacer" % len(falta_rev))
    inf["ii"]["sin_revisar"] = falta_rev
    if not any(habilitados.values()):
        inf["avisos"].append("ningún modelo habilitado en ninguna tarea: el tribunal va sin panel "
                             "de visión (se declara)")
    inf["listo"] = not inf["faltan"]
    return inf


# ── despacho de `analisis` ──────────────────────────────────────────────────────────────────
def analisis(base, args, lector=None, segmentador=None, log=print, repo=None, n1=None):
    """La orden de la puerta del piloto (lista cerrada ORDENES). Devuelve el código de salida."""
    import laminillas_sello as SL
    if not args or args[0] not in ORDENES:
        print("orden desconocida; válidas: %s" % ", ".join(ORDENES), file=sys.stderr)
        return RC_USO
    orden, resto = args[0], args[1:]
    laminas = [a for a in resto if not a.startswith("--")]
    banderas = [a for a in resto if a.startswith("--")]
    try:
        if banderas and not ((orden == "registro-par" and banderas in (["--valis"],
                                                                       ["--diagnostico"])) or
                             (orden == "piloto-ibis" and banderas == ["--diagnostico"])):
            raise ValueError("bandera no admitida: %s" % " ".join(banderas))
        if orden == "registro-par" and banderas == ["--diagnostico"]:
            diagnostico_registro(base, laminas, lector, log)
            return 0
        if orden == "piloto-ibis" and banderas:
            if laminas not in ([], [REFERENCIA]):
                raise ValueError("piloto-ibis --diagnostico: solo P-CK19")
            diagnostico_regla(base, log)
            return 0
        if orden == "piloto-i":
            if laminas not in ([], ["P-KI67"]):
                raise ValueError("piloto-i: solo P-KI67")
            piloto_i(base, lector, segmentador, log)
        elif orden == "piloto-ibis":
            piloto_ibis(base, laminas, lector, segmentador, log)
        elif orden == "registro-par":
            if len(laminas) != 2:
                raise ValueError("registro-par: dos láminas (p. ej. P-KI67 P-CK19)")
            registro_par(base, laminas[0], laminas[1], lector, valis=bool(banderas), log=log)
        elif orden in ("metricas", "geojson"):
            if len(laminas) != 1:
                raise ValueError("%s: una lámina" % orden)
            (metricas if orden == "metricas" else geojson)(base, laminas[0], lector, log)
        elif orden == "lista-postcongelacion":
            if laminas:
                raise ValueError("lista-postcongelacion: sin argumentos")
            inf = lista_postcongelacion(base, lector, log)
            if not inf["pasa"]:
                print("lista post-congelación: NO PASA en «%s» (informe en %s)" % (
                    inf["parada_en"], _rel("lista_postcongelacion.json")), file=sys.stderr)
                return RC_NO_PASA
            print("lista post-congelación: pasa (ramas: %s)" % (", ".join(inf["ramas"]) or "—"))
        elif orden == "tribunal-listo":
            if laminas:
                raise ValueError("tribunal-listo: sin argumentos")
            inf = tribunal_listo(repo=repo, n1=n1, log=log)
            _escribe(base, _rel("tribunal_listo.json"), inf)
            for d in inf["declaraciones"]:
                print(d)
            if not inf["listo"]:
                print("tribunal NO listo (5-bis):\n  · " + "\n  · ".join(inf["faltan"]),
                      file=sys.stderr)
                return RC_NO_PASA
            print("tribunal listo (5-bis)")
    except (SL.SelloAusente, SL.SelloInvalido) as e:
        print("%s: NO MIDO · %s: %s" % (orden, type(e).__name__, e), file=sys.stderr)
        return RC_SELLO
    except FaltaEntrada as e:
        print("%s: FALTA · %s" % (orden, e), file=sys.stderr)
        return RC_FALTA
    except NoPasa as e:
        print("%s: NO PASA · %s" % (orden, e), file=sys.stderr)
        return RC_NO_PASA
    except ValisNoCorrio as e:
        print("%s: ERROR DE EJECUCIÓN · %s" % (orden, e), file=sys.stderr)
        return RC_EJECUCION
    except ValueError as e:
        print("%s: %s" % (orden, e), file=sys.stderr)
        return RC_USO
    return 0


def main(argv):
    if not argv or argv[0] not in MODOS:
        print("modo desconocido; válidos: %s" % ", ".join(MODOS), file=sys.stderr)
        return 2
    modo, args = argv[0], argv[1:]
    base = C.sesion()
    for a in args:
        C._clave(a)                        # opaco o ValueError (la ventanilla ya lo filtró)
    if modo == "ingesta":                  # F1.1-F1.3 (jaula analisis-ingesta: SESION + ORIGEN)
        import laminillas_ingesta
        return laminillas_ingesta.main(args)
    if modo == "qc":                       # por lámina, eslabón 1 (jaula analisis: solo SESION)
        import laminillas_ingesta
        return laminillas_ingesta.main_qc(args)
    if modo == "registro" and args[:1] == ["prueba-vips"]:   # prueba (c) del lector, venv valis
        import laminillas_ingesta
        return laminillas_ingesta.main_vips(args[1:])
    if modo == "segmenta":                 # punto 4 (i)/(i-bis), jaula analisis
        import laminillas_sello as SL
        try:
            segmenta(base, args)
        except (SL.SelloAusente, SL.SelloInvalido, ValueError) as e:
            print("segmenta: NO MIDO · %s: %s" % (type(e).__name__, e), file=sys.stderr)
            return 3
        return 0
    if modo == "analisis":                 # puerta del piloto tras (0), jaula analisis
        return analisis(base, args)
    constancia = os.path.join(base, "constancia")
    os.makedirs(constancia, mode=0o700, exist_ok=True)
    rel = os.path.join("constancia", "%s-%s.json" % (modo, time.strftime("%Y%m%dT%H%M%S")))
    with open(os.path.join(base, rel), "w", encoding="utf-8") as f:
        json.dump({"modo": modo, "args": args, "implementado": False,
                   "nota": "esqueleto F1-infra: valida y deja constancia; sin píxeles"}, f)
    C.marca_hecho(base, modo, "esqueleto", productos=[rel])
    print("%s: esqueleto, %d argumento(s) opacos; constancia sellada" % (modo, len(args)))
    return 0


# ── F3, parte A (`laminillas_f3`): órdenes añadidas sin tocar las del piloto ──────────────────
# Las de `ORDENES_F3` corren en la jaula `analisis` (procesador `laminillas`, lista cerrada de la
# ventanilla = `ORDENES`). `roi-carlos` lee el pptx de ORIGEN: va por el procesador
# `laminillas_roi` (jaula `analisis-ingesta`, la única que lee ORIGEN) y por eso NO entra en
# `ORDENES`. Este `analisis` envuelve al de la puerta del piloto: lo de F3 va a
# `laminillas_f3.orden`; el resto, sin cambios, a `_analisis_piloto`.
ORDENES_F3 = ("consenso", "puerta-p63", "regiones-pobres", "lectura-digital")
ORDENES_ORIGEN = ("roi-carlos",)
ORDENES = ORDENES + ORDENES_F3
_analisis_piloto = analisis


def analisis(base, args, lector=None, segmentador=None, log=print, repo=None, n1=None):  # noqa: F811
    """Despacho de `analisis`: órdenes de F3 (parte A) a `laminillas_f3`; el resto, al de la
    puerta del piloto. Devuelve el código de salida."""
    if args and args[0] in ORDENES_F3 + ORDENES_ORIGEN:
        import laminillas_f3
        return laminillas_f3.orden(base, args, lector=lector, log=log)
    return _analisis_piloto(base, args, lector, segmentador, log, repo, n1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
