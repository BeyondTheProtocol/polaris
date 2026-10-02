#!/usr/bin/env python3
"""tools/laminillas_f3.py — F3 (parte A) de las laminillas DFCI: geometría de consenso, puerta de
p63, (a-bis) con su gemela NE, (b) lectura digital frente a visual y ROI de Carlos.

Lo lanza SOLO la ventanilla, como órdenes de `laminillas_proc` (que despacha aquí las de
`ORDENES_F3` y `ORDENES_ORIGEN`):

  lector_clinico.py procesa laminillas -- consenso [--valis]
  lector_clinico.py procesa laminillas -- puerta-p63 [P-KI67 P-RE …]
  lector_clinico.py procesa laminillas -- regiones-pobres P-RE        (gemela NE: P-SYN, P-CHGA, P-{{DIANA3}})
  lector_clinico.py procesa laminillas -- lectura-digital P-RE        (cualquier marcador medible)
  lector_clinico.py procesa laminillas_roi -- roi-carlos              (jaula analisis-ingesta: ORIGEN)

ORDEN DE LAS DEPENDENCIAS (cada orden dice qué le falta con código 5 y no mide nada a medias):
  0. Puerta del piloto: sello (0), `piloto-ibis` (máscara CK19 y regla morfométrica), `registro-par
     P-KI67 P-CK19` y `metricas P-KI67` (fija L con la regla sellada).
  1. `consenso`: las 11 IHQ con tejido segmentadas. Máscara de tejido por lámina (la del registro),
     transformada euclídea global de cada lámina contra P-CK19, FC = píxel presente en ≥6 de las
     11 máscaras llevadas a la referencia (la que no tiene global cuenta como 0), refinado y puerta
     por FC en cada par (`laminillas_registro.registra_serie`, rama F3; `--valis` añade el último
     respaldo) y la BASE: por FC, área, interior (>100 µm del borde), regiones de lado L (rejilla
     única anclada a P-CK19) con su distancia al borde, en qué láminas está verificado, densidad
     nuclear por lámina y parada dura de densidad.
  2. `puerta-p63 [L…]`: por FC, registro y ≥1 estructura CK19+ con ≥10 núcleos p63 sobre T en arco
     ≥50 % de su perímetro (`laminillas_metricas.puerta_p63`); sin ella, «not assessable (no
     internal positive control)». Con láminas, además su cifra aparte «invasive-only where p63
     available», con la fracción cubierta.
  3. `regiones-pobres L`: (a-bis) en P-RE con `laminillas_metricas.regiones_re_pobres` TAL CUAL
     (k/n, IC 95 %, ≥50 células, interior, control positivo a <500 µm, exclusiones, rótulos), solo
     en los FC que pasan la mini-puerta automática de la lámina (punto 7: densidad a ±20 % de CK19
     y KI67 y recall por clase), cruzada con CK19 y p63 registrados y con Ki67 regional. Gemela NE
     (actualización 11) en P-SYN, P-CHGA y P-{{DIANA3}}: regiones pobres (<10 %) y ricas (≥90 %) con los
     mismos criterios. Galería a L0 del mismo sitio (marcador, CK19, p63 y Ki67; RE en la gemela).
  4. `lectura-digital L`: (b) tabla por fragmento (todo e interior) y biopsias virtuales con la
     ventana sellada del hotspot en ESA lámina: cuánto cambia un % según el campo elegido. La
     lectura visual entra solo desde `SESION/f3/lectura_visual.json` (la escribe la verificación
     de F4 contra el informe original); si no está, no se inventa.
  5. `roi-carlos`: cada imagen del pptx (ORIGEN, sha256 cotejado con la hoja de claves) contra el
     nivel ×4 de las 13 láminas del primario: SIFT con los parámetros sellados del registro y
     RANSAC con SimilarityTransform de escala libre 0,25-4; se asigna a la de más inliers si son
     ≥30 y ≥2× los de la segunda; si no, «no localizada». Informe «label in deck: X; image matches:
     Y». Control de localización: no valida ningún %.

CÓDIGOS (los de `laminillas_proc`): 0 hecho; 2 uso; 3 sello ausente o inválido; 4 no pasa; 5 falta
un producto previo.

PRODUCTOS (SESION/f3/, 0600, «hecho» con la huella de sus entradas; tras 98/99 se salta lo sellado):
  consenso/fc.npz · serie.json · base.json · <MÓVIL>.json   FC, pares y base de fragmentos
  minipuerta/<L>.json                                      mini-puerta automática por FC
  p63/puerta.json · p63/invasiva_<L>.json                  puerta de p63 y cifra «invasive-only»
  regiones/<L>.json · galeria/<L>/indice.json + PNG a L0   (a-bis) y gemela NE
  lectura/<L>.json                                         (b)
  roi/roi.json                                             ROI de Carlos

INFERENCIAS MÍAS (el plan no da cifra; van a Métodos con `PARAMS_F3` y `DECLARACIONES_F3`): ver
cada clave de `PARAMS_F3` marcada [inferido].
"""
import hashlib
import json
import math
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DIR_F3 = "f3"
REFERENCIA = "P-CK19"
# Las 11 IHQ con tejido (el mismo bloque; AE1/AE3 no es registrable) y las 13 del primario.
SERIE_IHQ = ("P-CK19", "P-KI67", "P-HER2NEG", "P-HER2", "P-RE", "P-RP", "P-RA", "P-SYN", "P-CHGA",
             "P-{{DIANA3}}", "P-P63")
PRIMARIO = SERIE_IHQ + ("P-AE1AE3", "P-HE")
SUELO = ("P-HER2NEG", "P-HER2")
LAMINAS_NE = ("P-SYN", "P-CHGA", "P-{{DIANA3}}")
LAMINAS_REGIONES = ("P-RE",) + LAMINAS_NE
LAMINAS_LECTURA = ("P-KI67", "P-RE", "P-RP", "P-RA") + LAMINAS_NE
SIN_CONTROL_FONDO = (REFERENCIA, "P-P63") + SUELO      # plan F3 «Umbral»: no se les aplica
NOMBRE = {"P-HE": "H&E", "P-AE1AE3": "AE1/AE3"}         # el resto, `laminillas_proc.MARCADOR`

PARAMS_F3 = {
    "estructura_min_um2": 200.0,          # plan (máscara CK19): componentes <200 µm², fuera
    "p63_periferia_min_nucleos": 1,       # [inferido] «núcleos p63+ en la periferia»: con uno, la
                                          # estructura sale del recuento (lado conservador)
    "ck19_region_min_frac": 0.5,          # [inferido] región «CK19+»: ≥50 % de sus núcleos del
                                          # denominador dentro de la máscara CK19 registrada
    "ne_rica_pct": 90.0,                  # [inferido] gemela NE: espejo del «<10 %» de RE
    "control_negativo_min_nucleos": 10,   # [inferido] región rica: ≥10 células bajo T a <500 µm
    "galeria_max_regiones": 50,           # [inferido]
    "galeria_margen_um": 20.0,            # [inferido]
    "lectura_aparta_puntos": [5.0, 10.0], # [inferido] biopsias virtuales que se apartan del %
    "minipuerta_densidad_tol": 0.20,      # plan, punto 7: ±20 % de CK19 y KI67
    "minipuerta_recall_puntos": 5.0,      # plan, punto 5: diferencia DAB+ vs solo-H <5 puntos
    "roi_escala": [0.25, 4.0],            # plan
    "roi_min_inliers": 30,                # plan
    "roi_margen": 2.0,                    # plan: el doble que la segunda
    "roi_tesela_px": 2048,                # [inferido] SIFT del ×4 por teselas (memoria)
    "roi_solape_px": 256,                 # [inferido]
    "roi_bloque_desc": 4096,              # [inferido] emparejado por bloques (memoria)
    "roi_tejido_od": 0.15,                # [inferido] píxel con tejido en el ×4 (ODsum)
    "roi_tejido_min_frac": 0.02,          # [inferido] tesela sin tejido: fuera
    "roi_estira_percentil": 99.5,         # [inferido] estiramiento de la ODsum por imagen
    "roi_estira_min": 0.2,                # [inferido]
    "roi_i0_percentil": 99.5,             # [inferido] I0 de la captura (sin vidrio garantizado)
    "roi_i0_min": 200.0,                  # [inferido]
    "roi_max_lado_px": 4096,              # [inferido] captura mayor: se reduce (memoria)
    "roi_etiqueta_max": 80,               # [inferido] caracteres de la etiqueta guardada
}
INFERIDOS_F3 = tuple(k for k in PARAMS_F3 if k not in (
    "estructura_min_um2", "minipuerta_densidad_tol", "minipuerta_recall_puntos", "roi_escala",
    "roi_min_inliers", "roi_margen"))

ROTULOS_F3 = {
    "ne_pobre": ("NE-marker-poor region (<10 % of cells above T; k/n cells, 95 % CI; descriptive, "
                 "n=1)"),
    "ne_rica": ("NE-marker-rich region (>=90 % of cells above T; k/n cells, 95 % CI; descriptive, "
                "n=1)"),
    "ne_pobre_ck19": "NE-marker-poor region within CK19+ epithelium, p63-negative",
    "ne_rica_ck19": "NE-marker-rich region within CK19+ epithelium, p63-negative",
    "re_no_fiable": ("RE: nuclear detection not reliable in DAB-saturated areas; reported as "
                     "positive-pixel area fraction with range, no cell-level %"),
    "no_verificado_original": "not verified against the original report",
    "otra_tanda": ("different staining run / material not identified: not testable with these "
                   "slides"),
    "roi": "label in deck: {x}; image matches: {y}",
    "roi_no": "not localized",
    "roi_control": "localization control only; does not validate any %",
    "seccion": "fraction of the section scanned: unknown, no macro image",
}

DECLARACIONES_F3 = {
    "consenso": ("consensus fragments: pixel present in >=6 of the 11 tissue masks brought to "
                 "P-CK19 by each slide's global Euclidean transform (a slide without a global "
                 "counts as 0); refinement and gate per fragment in every pair; tissue mask = "
                 "the registration mask (smoothed ODsum or local haematoxylin deviation at ~4 "
                 "um/px, hysteresis, same thresholds on all 11 slides)"),
    "p63_periferia": ("a CK19+ structure with any p63+ nucleus within the periphery band counts "
                      "as having a myoepithelial layer (conservative: out of the count)"),
    "ck19_region": ("region 'CK19+': >=50 % of its denominator nuclei inside the registered CK19 "
                    "mask"),
    "ne_gemela": ("NE twin of (a-bis) (plan update 11): same criteria as the ER-poor search "
                  "(>=50 cells, interior, k/n with 95 % CI, exclusions, CK19 and p63 registered, "
                  "L0 gallery); poor = <10 %, rich = >=90 % (mirror of the ER cut; no plan "
                  "figure); rich regions carry a below-T control (>=10 cells at <500 um); labels "
                  "are not plan phrases"),
    "minipuerta": ("automatic mini-gate per consensus fragment (plan, point 7): nuclear density "
                   "within +-20 % of CK19 and KI67, and class recall (DAB+ vs haematoxylin-only "
                   "coverage, <5 points) in the strata mapped to the fragment; fragments without "
                   "an evaluable stratum are judged on density alone; the visual check gallery is "
                   "not produced here"),
    "solo_mapa": "fragments with the hard density stop (map only) are outside (a-bis) and (b)",
    "lectura": ("virtual biopsies: the sealed hotspot window (0.5 mm diameter, >=500 denominator "
                "nuclei, 50 um step, fragment interior) at every valid position of this slide"),
    "roi_imagen": ("ROI matching on ODsum (I0: local glass on the slide; the capture's own "
                   "bright percentile), each image stretched by its own tissue percentile, no "
                   "local equalisation (its kernel in um needs the unknown capture scale); slide "
                   "x4 level in tiles"),
    "roi_etiqueta": ("label in deck = text of the nearest text shape on the same slide (centre "
                     "distance; group transforms ignored)"),
}

ESTADO_LOCALIZADA, ESTADO_NO_LOCALIZADA = "localizada", "no localizada"


# ── utilidades ────────────────────────────────────────────────────────────────────────────────
def _P():
    """`laminillas_proc` (importado tarde: él despacha aquí)."""
    import laminillas_proc as P
    return P


def _rel(*partes):
    return os.path.join(DIR_F3, *partes)


REL_FC = _rel("consenso", "fc.npz")
REL_SERIE = _rel("consenso", "serie.json")
REL_BASE = _rel("consenso", "base.json")
REL_P63 = _rel("p63", "puerta.json")
REL_VISUAL = _rel("lectura_visual.json")


def _rel_par(movil):
    return _rel("consenso", "%s.json" % movil)


def clave_txt(c):
    """(fc, i, j) → «FC1:3,5» (como `laminillas_proc`)."""
    return "%s:%d,%d" % c


def _clave_fichero(c):
    return "%s_%d_%d" % c


def nombre_marcador(lamina):
    return _P().MARCADOR.get(lamina) or NOMBRE.get(lamina) or lamina


def metodos():
    return {"parametros_inferidos": {k: PARAMS_F3[k] for k in INFERIDOS_F3},
            "declaraciones": dict(DECLARACIONES_F3)}


def valor_L(base):
    """L de la regla sellada en (0), fijado tras (ii) en P-KI67 (`metricas P-KI67`)."""
    P = _P()
    rel = P._rel("L.json")
    if not os.path.isfile(P._abs(base, rel)):
        raise P.FaltaEntrada("L sin fijar: corre antes `metricas P-KI67` (regla de L sellada en "
                             "(0), valor tras (ii))")
    d = P._lee(base, rel)
    if d.get("L_um") is None:
        raise P.NoPasa("regla de L: %s" % d.get("motivo"))
    return float(d["L_um"]), rel


# ── 1. geometría de consenso ───────────────────────────────────────────────────────────────
def consenso(base, lector=None, valis=False, log=print, ejecutor_valis=None):
    """FC de consenso de las 11 IHQ, refinado y puerta por FC en cada par y base de fragmentos."""
    import numpy as np
    import laminillas_comun as C
    import laminillas_registro as R
    import laminillas_sello as SL
    P = _P()
    ruta = P.ruta_sello(base)
    SL.exige(ruta, list(SERIE_IHQ), SL.SECCIONES_MODULO_B)
    L, rel_L = valor_L(base)
    cent, mpp, faltan = {}, {}, []
    rels = ["congelacion.json", rel_L, os.path.join("identidad", "eslabon1.json")]
    for n in SERIE_IHQ:
        try:
            d = P.nucleos_lamina(base, n)
        except P.FaltaEntrada:
            faltan.append(n)
            continue
        cent[n] = np.asarray(d["centroide"], float).reshape(-1, 2)
        mpp[n] = float(d["mpp_l0"])
        rels += P._rels_nucleos(base, n)
    if faltan:
        raise P.FaltaEntrada("consenso: faltan los núcleos de %s (corre antes `procesa "
                             "laminillas_segmenta -- %s`)" % (", ".join(faltan), " ".join(faltan)))
    hu = P._huella(base, rels)
    if not valis and C.esta_hecho(base, "f3-consenso", "serie", hu):
        log("consenso: ya hecho con estas entradas; salto")
        bf = P._lee(base, REL_BASE)
        if not bf["fragmentos"]:
            raise P.NoPasa("consenso: ningún píxel en ≥6 de 11 máscaras (sin FC; ya medido)")
        return bf
    lector = P._lector(lector)
    moviles = list(SERIE_IHQ[1:])
    inits = {n: P._init_eslabon1(base, n, REFERENCIA) for n in moviles}
    serie = R.registra_serie(lector, ruta, moviles, centroides=cent, piloto=False, log=log,
                             ejecutor_valis=ejecutor_valis, intentar_valis=bool(valis),
                             imagenes={}, inits=inits)
    info = {k: serie.get(k) for k in ("sin_global", "origen_global", "declaraciones", "cierres",
                                      "orden")}
    # Mismo contrato que `laminillas_proc.registro_par` (2-oct): pedir `--valis` no es haberlo
    # intentado. Solo cuenta un VALIS que devolvió transformación en algún par; si se pidió y no
    # corrió en ninguno, es error de ejecución (código 6) y los fallos quedan en la serie.
    corrio, fallos = cuenta_valis({n: r.resumen() for n, r in serie["resultados"].items()},
                                  valis)
    info["valis_intentado"] = info["valis_corrio"] = corrio
    if fallos:
        info["valis_fallos"] = fallos[:20]
    info["sello"] = SL.exige(ruta, [REFERENCIA], SL.SECCIONES_MODULO_B).sha256
    bf = persiste_consenso(base, serie["resultados"], info, cent, mpp, L, hu, log)
    if fallos:
        raise P.ValisNoCorrio(("consenso: VALIS no corrió (%s)" % "; ".join(
            "%s %s: %s" % (f["movil"], f["fc"] or "-", f["motivo"]) for f in fallos[:3]))[:1200])
    return bf


def cuenta_valis(resumenes, valis):
    """(corrió, fallos) de VALIS en la serie: corrió si devolvió transformación en algún par;
    fallos = sus intentos sin transformación, solo si se pidió y no corrió en ninguno. Si nada
    necesitó VALIS (todos los FC pasaron antes), no hay intentos ni fallos: no es error."""
    P = _P()
    corrio = bool(valis) and any(P._valis_corrio_en(rs) for rs in resumenes.values())
    if not valis or corrio:
        return corrio, []
    return False, [{"movil": n, "fc": fc, "motivo": i.get("motivo")}
                   for n, rs in sorted(resumenes.items()) for fc, i in P._intentos_valis(rs)
                   if not (i.get("corrio") or "escala" in i)]


def persiste_consenso(base, resultados, info, cent, mpp, L, huella, log=print):
    """Escribe los productos del consenso a partir de los `ResultadoRegistro` de la serie (todas
    las móviles comparten los mismos FC). Devuelve la base. Sin ningún FC: base vacía y NoPasa."""
    import laminillas_comun as C
    import laminillas_metricas as MET
    P = _P()
    fcs = next((r.fcs for r in resultados.values() if r.fcs), [])
    _guarda_fcs(base, fcs)
    productos = [REL_FC]
    pares = {}
    for n, r in sorted(resultados.items()):
        rs = r.resumen()
        pares[n] = {"tipo": "consenso-par", "fija": REFERENCIA, "movil": n, "resumen": rs,
                    "tre_p90_um": P._p90_par(rs),
                    "fraccion_area_verificada": rs.get("fraccion_area_verificada"),
                    "pendientes_comprobacion_ciegas": rs.get("pendientes_comprobacion_ciegas")}
        productos.append(P._escribe(base, _rel_par(n), pares[n]))
    bf = base_fragmentos(fcs, resultados, pares, cent, mpp, L, MET.PARAMS_PARTIDA)
    serie = dict(info, tipo="consenso-serie", referencia=REFERENCIA, huella=huella, L_um=L,
                 fcs=[{"id": f["id"], "area_um2": f["area_um2"]} for f in fcs],
                 declaraciones=list(info.get("declaraciones") or []) +
                 [DECLARACIONES_F3["consenso"]])
    productos.append(P._escribe(base, REL_BASE, bf))
    productos.append(P._escribe(base, REL_SERIE, serie))
    C.marca_hecho(base, "f3-consenso", "serie", huella, productos=productos)
    log("consenso: %d FC (%s); área verificada por lámina: %s" % (
        len(fcs), ", ".join("%s %.2f mm²" % (f["id"], f["area_um2"] / 1e6) for f in fcs) or "—",
        ", ".join("%s %.0f %%" % (n, 100 * (d["fraccion_area_verificada"] or 0))
                  for n, d in sorted(pares.items()))))
    if not fcs:
        raise P.NoPasa("consenso: ningún píxel en ≥6 de 11 máscaras (sin FC); base vacía escrita")
    return bf


def _guarda_fcs(base, fcs):
    import numpy as np
    P = _P()
    return P._npz(base, REL_FC, ids=np.array([f["id"] for f in fcs]),
                  mascaras=(np.stack([np.asarray(f["mascara"], bool) for f in fcs]) if fcs else
                            np.zeros((0, 1, 1), bool)),
                  mpp=np.array(float(fcs[0]["mpp"]) if fcs else 0.0),
                  areas=np.array([float(f["area_um2"]) for f in fcs]))


def base_fragmentos(fcs, resultados, pares, cent, mpp, L, p_met):
    """Base de fragmentos: por FC, área, interior, regiones de lado L (rejilla única anclada a
    P-CK19) con su distancia al borde, en qué láminas está verificado (pendiente, no), densidad
    nuclear por lámina (solo donde está verificado) y la parada dura de densidad."""
    import numpy as np
    import laminillas_metricas as MET
    P = _P()
    interior = float(p_met["interior_um"])
    polys = {f["id"]: f["poligono_um"] for f in fcs if f.get("poligono_um") is not None}
    regiones = MET.rejilla(polys, float(L), (0.0, 0.0)) if polys else {}
    dens = {}
    estado = {REFERENCIA: {f["id"]: True for f in fcs}}
    if REFERENCIA in cent:
        ids = P.fc_de(fcs, cent[REFERENCIA] * mpp[REFERENCIA])
        dens[REFERENCIA] = {f["id"]: float(np.sum(ids == f["id"]) / (f["area_um2"] / 1e6))
                            for f in fcs}
    for n, r in sorted(resultados.items()):
        estado[n] = {f["id"]: f["pasa"] for f in r.fragmentos}
        if r.matriz() is None or n not in cent:
            dens[n] = {}
            continue
        _, ids = r.nucleos_a_referencia(cent[n], mpp[n])
        dens[n] = {f["id"]: float(np.sum(ids == f["id"]) / (f["area_um2"] / 1e6)) for f in fcs
                   if estado[n].get(f["id"]) is True}
    parada = MET.parada_densidad(dens) if fcs else {"mediana_serie": {}, "solo_mapa": {},
                                                    "frac_min": None}
    frs = []
    for f in fcs:
        fid = f["id"]
        pol = polys.get(fid)
        inte = pol.buffer(-interior) if pol is not None else None
        regs = [{"region": clave_txt(c), "area_um2": r["area_um2"], "frac_area": r["frac_area"],
                 "dist_borde_um": r["dist_borde_um"], "interior": r["dist_borde_um"] > interior}
                for c, r in sorted(regiones.items(), key=lambda kv: kv[0]) if r["fc"] == fid]
        ver = sorted(n for n, e in estado.items() if e.get(fid) is True)
        pen = sorted(n for n, e in estado.items() if fid in e and e[fid] is None)
        p90 = {n: ((resultados[n].fragmento(fid).get("evaluacion") or {}).get("p90_um")
                   if n in resultados and fid in estado.get(n, {}) else None) for n in ver
               if n != REFERENCIA}
        frs.append({"id": fid, "area_mm2": f["area_um2"] / 1e6,
                    "area_interior_mm2": (inte.area / 1e6 if inte is not None and not inte.is_empty
                                          else 0.0),
                    "n_regiones": len(regs), "n_regiones_interior": sum(r["interior"] for r in regs),
                    "verificado_en": ver, "pendiente_en": pen,
                    "no_verificado_en": sorted(n for n in SERIE_IHQ if n not in ver and n not in pen),
                    "tre_p90_um": p90,
                    "solo_mapa_en": sorted(lam for lam, fl in parada["solo_mapa"].items()
                                           if fid in map(str, fl)),
                    "regiones": regs})
    laminas = {n: {"fraccion_area_verificada": d.get("fraccion_area_verificada"),
                   "tre_p90_um": d.get("tre_p90_um"),
                   "sin_global": (d.get("resumen") or {}).get("global", {}).get("matriz_um") is None,
                   "pendientes_comprobacion_ciegas": d.get("pendientes_comprobacion_ciegas")}
              for n, d in pares.items()}
    return {"tipo": "consenso-base", "referencia": REFERENCIA, "L_um": float(L),
            "interior_um": interior, "alcance": "of the scanned region",
            "seccion": ROTULOS_F3["seccion"], "fragmentos": frs, "laminas": laminas,
            "densidad_por_fc": dens, "parada": parada,
            "ae1ae3": "insufficient detectable tissue on this scan for registration (cause not "
                      "determined: pale counterstain, depleted section or focus)",
            "area_total_mm2": sum(f["area_um2"] for f in fcs) / 1e6,
            "metodos": metodos()}


def carga_consenso(base):
    """FC (con polígono en µm de P-CK19), serie y base. FaltaEntrada si falta o no casa."""
    import laminillas_comun as C
    import laminillas_registro as R
    P = _P()
    if not os.path.isfile(P._abs(base, REL_SERIE)):
        raise P.FaltaEntrada("falta el consenso de F3: corre antes `consenso`")
    serie = P._lee(base, REL_SERIE)
    if not C.esta_hecho(base, "f3-consenso", "serie", serie.get("huella")):
        raise P.FaltaEntrada("el consenso de F3 está incompleto o cambiado: repite `consenso`")
    z = P._carga_npz(base, REL_FC)
    mpp = float(z["mpp"])
    fcs = []
    for i, fid in enumerate(z["ids"].tolist()):
        m = z["mascaras"][i]
        fcs.append({"id": str(fid), "mascara": m, "mpp": mpp, "area_um2": float(z["areas"][i]),
                    "poligono_um": R.mascara_a_poligono(m, mpp)})
    return {"fcs": fcs, "serie": serie, "base": P._lee(base, REL_BASE),
            "rels": [REL_FC, REL_SERIE, REL_BASE]}


def resultado_consenso(base, movil, fcs):
    """`ResultadoRegistro` de una móvil contra P-CK19 sobre los FC de consenso, desde disco."""
    import numpy as np
    import laminillas_registro as R
    P = _P()
    rel = _rel_par(movil)
    if not os.path.isfile(P._abs(base, rel)):
        raise P.FaltaEntrada("%s fuera del consenso (sin %s): repite `consenso`" % (movil, rel))
    d = P._lee(base, rel)
    rs = d.get("resumen") or {}
    res = R.ResultadoRegistro(rs.get("fija", REFERENCIA), movil, rs.get("parametros") or {},
                              None, False)
    g = rs.get("global") or {}
    res.global_ = dict(g, matriz_um=None if g.get("matriz_um") is None
                       else np.asarray(g["matriz_um"], float))
    por = {f["id"]: f for f in rs.get("fragmentos") or []}
    for fc in fcs:
        f = por.get(fc["id"])
        if f is None:
            continue
        res.fragmentos.append(dict(f, matriz_um=None if f.get("matriz_um") is None
                                   else np.asarray(f["matriz_um"], float)))
        res.fcs.append(fc)
    return res, d


# ── tabla de núcleos sobre la base de consenso ─────────────────────────────────────────────
def tabla(base, lamina, lector=None, log=print):
    """Lo que necesitan p63, (a-bis) y (b) de UNA lámina, sobre la base de consenso: núcleos en µm
    de P-CK19 con su FC de consenso (solo donde ESTA lámina está verificada), los tres
    denominadores y el principal (supervivencia de la erosión en P-CK19), el contexto sellado (con
    el control de fondo de F3 donde aplica) y las regiones de lado L."""
    import numpy as np
    import laminillas_metricas as MET
    import laminillas_sello as SL
    P = _P()
    ruta = P.ruta_sello(base)
    sello = SL.exige(ruta, [lamina], SL.SECCIONES_MODULO_B)
    cons = carga_consenso(base)
    L, rel_L = valor_L(base)
    if abs(L - float(cons["base"]["L_um"])) > 1e-9:
        raise P.FaltaEntrada("L cambió desde el consenso: repite `consenso`")
    ob = P.objetos(base, lamina, lector, log)
    regla = P.carga_regla(base)
    regimen = P._regimen_vigente(base)
    reg_ef = regimen or P._rige_sellado(sello) or "clasicas"
    artef = P._artefacto(ob, reg_ef)
    morf = P.aplica_regla(regla, ob["rasgos"])
    comp = SL.COMPARTIMENTO.get(lamina, "nucleo")
    dab = np.asarray(ob["dab_anillo" if comp == "anillo" else "dab_nucleo"], float)
    cent = np.asarray(ob["centroide"], float).reshape(-1, 2)
    mpp = float(ob["mpp_l0"])
    fcs_l = cons["fcs"]
    fcs = {f["id"]: f["poligono_um"] for f in fcs_l if f["poligono_um"] is not None}
    ck = P.MascaraCK19(base)
    rels = ["congelacion.json", P._rel("objetos_%s.npz" % lamina),
            P._rel("objetos_%s.json" % lamina), P._rel("regla_morfometrica.json"),
            P._rel("ck19", "mascara.json"), P._rel("ck19", "mascara.npz"), rel_L,
            P._rel("regimen.json"), P._rel("objetos_%s.json" % REFERENCIA),
            _rel_par("P-KI67")] + cons["rels"]
    tre_ki = (P._lee(base, _rel_par("P-KI67")).get("tre_p90_um")
              if os.path.isfile(P._abs(base, _rel_par("P-KI67"))) else None)
    res = None
    if lamina == REFERENCIA:
        xy_um = cent * mpp
        ids = P.fc_de(fcs_l, xy_um)
        tre = tre_ki
    else:
        res, dpar = resultado_consenso(base, lamina, fcs_l)
        rels.append(_rel_par(lamina))
        if res.global_.get("matriz_um") is None:
            xy_um = np.full((len(cent), 2), np.nan)
            ids = np.array([None] * len(cent), dtype=object)
        else:
            xy_um, ids = res.nucleos_a_referencia(cent, mpp)
        tre = dpar.get("tre_p90_um")
    den = {"ck19_erosionada": ck.erosionada(xy_um, tre), "ck19_sin_erosionar": ck.dentro(xy_um),
           "morfometrico": morf}
    foco = np.where(np.asarray(ob["foco_bajo"], bool), 1, 2)
    nuc = MET.Nucleos(lamina, xy_um, dab, ids, den, artefacto=artef, foco_tercil=foco,
                      hema=ob["hema"], xy_l0=cent, area_um2=ob["area_um2"],
                      dab_anillo=ob["dab_anillo"])
    ob_ck = ob if lamina == REFERENCIA else P.carga_objetos(base, REFERENCIA)
    xy_ck = np.asarray(ob_ck["centroide"], float).reshape(-1, 2) * float(ob_ck["mpp_l0"])
    den_ck = {"ck19_erosionada": ck.erosionada(xy_ck, tre), "ck19_sin_erosionar": ck.dentro(xy_ck),
              "morfometrico": P.aplica_regla(regla, ob_ck["rasgos"])}
    nuc_ck = MET.Nucleos(REFERENCIA, xy_ck, np.asarray(ob_ck["dab_anillo"], float),
                         P.fc_de(fcs_l, xy_ck), den_ck, artefacto=P._artefacto(ob_ck, reg_ef))
    superv = MET.supervivencia_erosion(nuc_ck, MET.Contexto(ruta, REFERENCIA, regimen=regimen))
    principal = MET.aplica_denominador_principal(nuc, superv)
    ctx = MET.Contexto(ruta, lamina, regimen=regimen)
    control = None
    if lamina not in SIN_CONTROL_FONDO:
        control = MET.control_fondo(nuc, ctx, ~ck.dilatada(xy_um, tre), tre or 0.0)
        ctx = ctx.eleva_por_fondo(control)
    regiones = MET.cuenta_regiones(nuc, MET.rejilla(fcs, L, (0.0, 0.0)), ctx, "principal")
    solo = tuple(str(f) for f in (cons["base"].get("parada") or {}).get("solo_mapa", {})
                 .get(lamina, ()))
    decl = [DECLARACIONES_F3["consenso"], P.DECLARACIONES["artefacto"], regla["declaracion"],
            P.DECLARACIONES["erosion"]]
    if solo:
        decl.append(DECLARACIONES_F3["solo_mapa"])
    return {"lamina": lamina, "nuc": nuc, "ctx": ctx, "fcs": fcs, "fcs_l": fcs_l, "L": L,
            "regiones": regiones, "den": "principal", "tre_p90_um": tre, "res": res,
            "supervivencia": superv, "principal_por_fc": principal, "control_fondo": control,
            "solo_mapa": solo, "ck": ck, "ob": ob, "mpp_l0": mpp, "rels": rels,
            "base": cons["base"], "declaraciones": decl, "regimen": reg_ef}


def _solo_fc(nuc, fid):
    """Copia de la tabla con el FC de los núcleos de otros fragmentos a None (fuera de toda cifra)."""
    import numpy as np
    import laminillas_metricas as MET
    fc = np.array([f if f == fid else None for f in nuc.fc], dtype=object)
    out = MET.Nucleos(nuc.lamina, nuc.xy, nuc.dab, fc, dict(nuc.den), artefacto=nuc.artefacto,
                      foco_tercil=nuc.foco, hema=nuc.hema, xy_l0=nuc.xy_l0, area_um2=nuc.area,
                      dab_anillo=nuc.dab_anillo)
    if hasattr(nuc, "principal_usa_ck19"):
        out.principal_usa_ck19 = nuc.principal_usa_ck19
    return out


def _hecho(base, paso, lamina, huella, rel):
    import laminillas_comun as C
    P = _P()
    if C.esta_hecho(base, paso, lamina, huella) and os.path.isfile(P._abs(base, rel)):
        return P._lee(base, rel)
    return None


def _barre(out):
    import laminillas_metricas as MET
    malas = MET.barre_nunca(out)
    if malas:
        raise RuntimeError("frase de «Nunca decir» en F3: %s" % ", ".join(malas))


# ── 2. puerta de p63 ─────────────────────────────────────────────────────────────────────────
def estructuras_ck19(base, fcs_l, ck=None):
    """Estructuras CK19+ = componentes conexas de la máscara CK19 sellada (≥200 µm²), como
    polígonos en µm de P-CK19, con el FC de consenso de su centroide (fuera de FC, fuera)."""
    from scipy import ndimage as ndi
    from shapely import affinity
    import laminillas_registro as R
    P = _P()
    ck = ck or P.MascaraCK19(base)
    et, _ = ndi.label(ck.m)
    dx, dy = (float(v) for v in ck.origen * ck.mpp_l0)
    out = []
    for k, sl in enumerate(ndi.find_objects(et), start=1):
        if sl is None:
            continue
        sub = et[sl] == k
        if sub.sum() * ck.mpp ** 2 < PARAMS_F3["estructura_min_um2"]:
            continue
        pol = R.mascara_a_poligono(sub, ck.mpp)
        if pol is None or pol.is_empty:
            continue
        pol = affinity.translate(pol, dx + sl[1].start * ck.mpp, dy + sl[0].start * ck.mpp)
        c = pol.centroid
        fc = P.fc_de(fcs_l, [[c.x, c.y]])[0]
        if fc is None:
            continue
        out.append({"id": "E%d" % k, "fc": str(fc), "geom": pol, "area_um2": float(pol.area),
                    "centro_um": [float(c.x), float(c.y)],
                    "caja_um": [float(v) for v in pol.bounds]})
    return out


MIOEPITELIO = ("control_positivo", "periferia_positiva")


def _clasifica_estructuras(estr, puerta, p_met):
    """Clase de cada estructura: «control_positivo» (≥10 núcleos p63 en arco ≥50 %),
    «periferia_positiva» (algún núcleo p63+ en su periferia), «negativa», o «not assessable» si
    su FC no pasa la puerta."""
    vistos = {}
    for e in estr:
        f = e["fc"]
        k = vistos.get(f, 0)
        vistos[f] = k + 1
        d = puerta.get(f) or {}
        r = (d.get("estructuras") or [None] * (k + 1))[k] if d else None
        e["n_p63_periferia"] = None if r is None else int(r["n_p63"])
        e["arco"] = None if r is None else float(r["arco"])
        if not d.get("pasa"):
            e["clase"] = "not assessable"
        elif r is not None and r["control"]:
            e["clase"] = "control_positivo"
        elif r is not None and r["n_p63"] >= PARAMS_F3["p63_periferia_min_nucleos"]:
            e["clase"] = "periferia_positiva"
        else:
            e["clase"] = "negativa"
    return estr


def puerta_p63(base, laminas=(), lector=None, log=print):
    """Puerta de p63 por FC de consenso y, por cada lámina pedida, la cifra aparte «invasive-only
    where p63 available» con la fracción cubierta."""
    import laminillas_comun as C
    import laminillas_metricas as MET
    P = _P()
    for lam in laminas:
        if lam not in LAMINAS_LECTURA:
            raise ValueError("puerta-p63: %s no es un marcador medible (%s)"
                             % (lam, ", ".join(LAMINAS_LECTURA)))
    t = tabla(base, "P-P63", lector, log)
    hu = P._huella(base, t["rels"])
    inf = _hecho(base, "f3-p63", "P-P63", hu, REL_P63)
    estr = None
    if inf is None:
        estr = estructuras_ck19(base, t["fcs_l"], t["ck"])
        bruta = MET.puerta_p63(t["nuc"], t["ctx"], [{"fc": e["fc"], "geom": e["geom"]}
                                                    for e in estr])
        puerta = {}
        for f in t["fcs"]:
            d = bruta.get(f)
            if d is None:
                registrado = any(x == f for x in t["nuc"].fc)
                d = {"n_estructuras": 0, "n_con_control": 0, "estructuras": [], "pasa": False,
                     "registrado": registrado, "rotulo": MET.ROTULOS["p63_no_evaluable"]}
            puerta[f] = d
        _clasifica_estructuras(estr, puerta, t["ctx"].p)
        area = {f["id"]: f["area_um2"] for f in t["fcs_l"]}
        tot = sum(area.values())
        inf = {"tipo": "puerta-p63", "lamina": "P-P63", "umbral": t["ctx"].cita(),
               "puerta": {f: {k: v for k, v in d.items() if k != "estructuras"}
                          for f, d in puerta.items()},
               "fraccion_area_cubierta": (sum(area[f] for f, d in puerta.items() if d["pasa"]) /
                                          tot if tot else 0.0),
               "fragmentos_que_pasan": sorted(f for f, d in puerta.items() if d["pasa"]),
               "estructuras": [{k: v for k, v in e.items() if k != "geom"} for e in estr],
               "criterio": ("registration and >=1 CK19+ structure with >=10 p63 nuclei above T "
                            "in an arc covering >=50 % of its perimeter (internal positive "
                            "control)"),
               "rotulo_no_evaluable": MET.ROTULOS["p63_no_evaluable"],
               "alcance": MET.ROTULOS["region_escaneada"],
               "declaraciones": t["declaraciones"] + [DECLARACIONES_F3["p63_periferia"]],
               "metodos": metodos(), "huella": hu}
        _barre(inf)
        P._escribe(base, REL_P63, inf)
        C.marca_hecho(base, "f3-p63", "P-P63", hu, productos=[REL_P63])
        log("puerta de p63: pasan %s de %d FC (%.0f %% del área de consenso)" % (
            ", ".join(inf["fragmentos_que_pasan"]) or "ninguno", len(puerta),
            100 * inf["fraccion_area_cubierta"]))
    out = {"puerta": inf}
    for lam in laminas:
        out[lam] = cifra_invasiva(base, lam, inf, estr, lector, log)
    return out


def carga_p63(base, fcs_l=None, ck=None):
    """La puerta de p63 de disco y sus estructuras con geometría (recalculada de la máscara
    sellada, determinista) y clase."""
    import laminillas_comun as C
    P = _P()
    if not os.path.isfile(P._abs(base, REL_P63)):
        raise P.FaltaEntrada("falta la puerta de p63: corre antes `puerta-p63`")
    inf = P._lee(base, REL_P63)
    if not C.esta_hecho(base, "f3-p63", "P-P63", inf.get("huella")):
        raise P.FaltaEntrada("la puerta de p63 está cambiada: repite `puerta-p63`")
    if fcs_l is None:
        fcs_l = carga_consenso(base)["fcs"]
    clase = {e["id"]: e for e in inf["estructuras"]}
    estr = []
    for e in estructuras_ck19(base, fcs_l, ck):
        if e["id"] not in clase:
            raise P.FaltaEntrada("estructuras CK19 distintas de las de la puerta de p63: repite "
                                 "`puerta-p63`")
        estr.append(dict(clase[e["id"]], geom=e["geom"]))
    return inf, estr


def en_mioepitelio(xy_um, estr, banda_um):
    """bool N: el núcleo cae en una estructura CK19+ con capa mioepitelial (dilatada la banda de
    la periferia)."""
    import numpy as np
    import shapely
    from shapely.ops import unary_union
    xy = np.asarray(xy_um, float).reshape(-1, 2)
    geoms = [e["geom"].buffer(banda_um) for e in estr if e.get("clase") in MIOEPITELIO]
    out = np.zeros(len(xy), bool)
    if not geoms or not len(xy):
        return out
    u = unary_union(geoms)
    ok = np.isfinite(xy).all(1)
    out[ok] = shapely.contains_xy(u, xy[ok, 0], xy[ok, 1])
    return out


def cifra_invasiva(base, lamina, inf=None, estr=None, lector=None, log=print):
    """«invasive-only where p63 available» de una lámina (con la fracción cubierta)."""
    import laminillas_comun as C
    import laminillas_metricas as MET
    P = _P()
    t = tabla(base, lamina, lector, log)
    rel = _rel("p63", "invasiva_%s.json" % lamina)
    hu = P._huella(base, t["rels"] + [REL_P63])
    hecho = _hecho(base, "f3-p63-invasiva", lamina, hu, rel)
    if hecho is not None:
        return hecho
    if inf is None or estr is None:
        inf, estr = carga_p63(base, t["fcs_l"], t["ck"])
    puerta = {f: dict(d) for f, d in inf["puerta"].items()}
    nuc = t["nuc"]
    if t["solo_mapa"]:
        for f in t["solo_mapa"]:
            if f in puerta:
                puerta[f]["pasa"] = False
    mio = en_mioepitelio(nuc.xy, estr, t["ctx"].p["p63_banda_um"])
    c = MET.cifra_invasiva_p63(nuc, t["ctx"], "principal", puerta, mio)
    out = dict(c, tipo="invasiva-p63", lamina=lamina, marcador=nombre_marcador(lamina),
               fraccion_area_p63=inf["fraccion_area_cubierta"],
               declaraciones=t["declaraciones"] + [DECLARACIONES_F3["p63_periferia"]],
               solo_mapa=list(t["solo_mapa"]), huella=hu)
    _barre(out)
    P._escribe(base, rel, out)
    C.marca_hecho(base, "f3-p63-invasiva", lamina, hu, productos=[rel])
    log("%s: invasive-only where p63 available %s %% (k/n %d/%d, FC %s)" % (
        lamina, "—" if c["n"] == 0 else "%.1f" % c["pct"], c["k"], c["n"],
        ", ".join(c["fragmentos"]) or "—"))
    return out


# ── 3. (a-bis) y gemela NE ───────────────────────────────────────────────────────────────────
def minipuerta(base, lamina, lector=None, log=print, t=None, recall=None):
    """Mini-puerta automática de la lámina por FC (plan, punto 7): densidad nuclear a ±20 % de la
    de CK19 y KI67 en ese FC (base de consenso) y recall por clase frente al detector clásico
    (`laminillas_proc.recall_por_clase`) en los estratos que caen en ese FC."""
    import collections
    import numpy as np
    import laminillas_comun as C
    P = _P()
    t = t or tabla(base, lamina, lector, log)
    rel = _rel("minipuerta", "%s.json" % lamina)
    hu = P._huella(base, t["rels"])
    hecho = _hecho(base, "f3-minipuerta", lamina, hu, rel)
    if hecho is not None:
        return hecho
    if recall is None:
        recall = P.recall_por_clase(base, P._lector(lector), P._sello(base, [lamina]), lamina)
    fr = np.asarray(t["ob"]["fragmento"])
    mapa = {}
    for k in sorted(set(int(x) for x in fr if x >= 0)):
        vals = [i for i in t["nuc"].fc[fr == k] if i is not None]
        mapa["F%d" % k] = collections.Counter(vals).most_common(1)[0][0] if vals else None
    dens = t["base"].get("densidad_por_fc") or {}
    tol = PARAMS_F3["minipuerta_densidad_tol"]
    max_p = PARAMS_F3["minipuerta_recall_puntos"]
    por_fc = {}
    for fid in sorted(t["fcs"]):
        d = {n: (dens.get(n) or {}).get(fid) for n in (lamina, REFERENCIA, "P-KI67")}
        ok_d = all(v is not None and v > 0 for v in d.values()) and all(
            abs(d[lamina] / d[n] - 1) <= tol for n in (REFERENCIA, "P-KI67"))
        estr = {e: c for e, c in (recall.get("cobertura") or {}).items()
                if mapa.get(e.split("|")[0]) == fid}
        malos = {e: 100 * abs(float(c["dab"]) - float(c["h"])) for e, c in estr.items()
                 if 100 * abs(float(c["dab"]) - float(c["h"])) >= max_p}
        rec = None if not estr else not malos
        pasa = bool(ok_d and rec is not False)
        por_fc[fid] = {"densidad_mm2": d, "densidad_pasa": bool(ok_d),
                       "recall_estratos": sorted(estr), "recall_fallos": malos,
                       "recall_pasa": rec, "pasa": pasa,
                       "rotulo": (ROTULOS_F3["re_no_fiable"] if lamina == "P-RE" and not pasa
                                  else None)}
    out = {"tipo": "minipuerta", "lamina": lamina, "por_fc": por_fc,
           "criterio": DECLARACIONES_F3["minipuerta"], "mapa_fragmentos": mapa,
           "recall": {k: recall.get(k) for k in ("cobertura", "estratos_fuera", "f1_iou05",
                                                 "n_teselas", "T", "declaracion")},
           "huella": hu}
    P._escribe(base, rel, out)
    C.marca_hecho(base, "f3-minipuerta", lamina, hu, productos=[rel])
    log("%s: mini-puerta por FC: %s" % (lamina, ", ".join(
        "%s %s" % (f, "pasa" if v["pasa"] else "NO") for f, v in por_fc.items()) or "—"))
    return out


def _ck19_por_region(t, regiones):
    """{clave: bool}: ≥50 % de los núcleos del denominador de la región dentro de CK19."""
    import numpy as np
    import laminillas_metricas as MET
    nuc = t["nuc"]
    m = nuc.mascara("principal")
    dentro = nuc.den["ck19_sin_erosionar"]
    claves = MET.claves_de(nuc, regiones)
    tot, si = {}, {}
    for i in np.nonzero(m)[0]:
        c = claves[i]
        if c in regiones:
            tot[c] = tot.get(c, 0) + 1
            si[c] = si.get(c, 0) + int(dentro[i])
    return {c: bool(tot.get(c) and si[c] / tot[c] >= PARAMS_F3["ck19_region_min_frac"])
            for c in regiones}


def _p63_por_region(regiones, estr, puerta):
    """{clave: «negativo» | «periferia_positiva» | «not assessable»} con las estructuras CK19+
    clasificadas por la puerta de p63."""
    import shapely
    geoms = [e["geom"] for e in estr]
    arbol = shapely.STRtree(geoms) if geoms else None
    out = {}
    for c, r in regiones.items():
        if not (puerta.get(r["fc"]) or {}).get("pasa"):
            out[c] = "not assessable"
            continue
        idx = arbol.query(r["geom"], predicate="intersects") if arbol is not None else []
        mio = any(estr[int(i)].get("clase") in MIOEPITELIO for i in idx)
        out[c] = "periferia_positiva" if mio else "negativo"
    return out


def _pct_regional(t):
    import laminillas_metricas as MET
    return {c: r["pct"] for c, r in MET.validas(t["regiones"], t["ctx"].p).items()}


def regiones_ne(nuc, regiones, ctx, denominador, pasa_minipuerta, ck19=None, p63=None,
                ki67_pct=None, p63_puerta=None, re_pct=None):
    """Gemela NE de (a-bis) (actualización 11): en P-SYN, P-CHGA o P-{{DIANA3}}, regiones POBRES (<10 %)
    y RICAS (≥90 %) con los MISMOS criterios que `laminillas_metricas.regiones_re_pobres`: ≥50
    células, interior del fragmento, k/n con IC 95 % (Clopper-Pearson), exclusión por artefacto,
    tercil bajo de foco o hematoxilina nuclear fuera de ±20 %, solo en los FC que pasan la
    mini-puerta, cruce con CK19 y p63 registrados. Pobre: control positivo a <500 µm (como RE);
    rica: control bajo T a <500 µm. Ninguna se llama tumoral."""
    import numpy as np
    from scipy.spatial import cKDTree
    from shapely.geometry import Point
    import laminillas_metricas as MET
    if nuc.lamina != ctx.lamina:
        raise MET.MetricaError("los núcleos son de %s y el contexto de %s: no se mide"
                               % (nuc.lamina, ctx.lamina))
    if ctx.lamina not in LAMINAS_NE:
        raise MET.MetricaError("la gemela NE se mide en P-SYN, P-CHGA o P-{{DIANA3}}")
    if regiones:
        MET._regiones_de(regiones, ctx, denominador)
    if isinstance(pasa_minipuerta, dict):
        pasan = {f for f, v in pasa_minipuerta.items() if v}
    else:
        pasan = None if pasa_minipuerta else set()
    if pasan is not None and not pasan:
        return {"evaluado": False, "motivo": "%s no pasa su mini-puerta en ningún FC: la gemela NE "
                                             "no se evalúa" % ctx.lamina}
    p = ctx.p
    T = ctx.T
    m = nuc.mascara(denominador)
    pos = (nuc.dab > T) & m
    neg = (nuc.dab <= T) & m
    claves = MET.claves_de(nuc, regiones)
    arboles = {"pobre": cKDTree(nuc.xy[pos]) if pos.any() else None,
               "rica": cKDTree(nuc.xy[neg]) if neg.any() else None}
    puntos = {"pobre": nuc.xy[pos], "rica": nuc.xy[neg]}
    minimo = {"pobre": p["control_positivo_min_nucleos"],
              "rica": PARAMS_F3["control_negativo_min_nucleos"]}
    hema_ref = float(np.median(nuc.hema[m])) if nuc.hema is not None and m.any() else None
    por_region = {}
    for i, c in enumerate(claves):
        if c in regiones:
            por_region.setdefault(c, []).append(i)
    cands, excluidas, n_intermedias = {"pobre": [], "rica": []}, [], 0
    for c, r in sorted(regiones.items(), key=lambda kv: kv[0]):
        if r["n"] < p["re_pobre_min_n"] or r["dist_borde_um"] <= p["interior_um"]:
            continue
        pct = 100.0 * r["k"] / r["n"]
        if pct < p["re_pobre_pct"]:
            tipo = "pobre"
        elif pct >= PARAMS_F3["ne_rica_pct"]:
            tipo = "rica"
        else:
            n_intermedias += 1
            continue
        if pasan is not None and r["fc"] not in pasan:
            excluidas.append({"region": clave_txt(c), "tipo": tipo,
                              "motivo": "mini-gate not passed in this fragment"})
            continue
        ids = np.array(por_region.get(c, []), int)
        motivo = None
        if len(ids) and nuc.artefacto[ids].mean() > p["artefacto_max_frac"]:
            motivo = "artifact"
        elif nuc.foco is not None and len(ids) and np.median(nuc.foco[ids]) <= 1:
            motivo = "lowest focus tertile"
        elif hema_ref and nuc.hema is not None and len(ids):
            hm = float(np.median(nuc.hema[ids]))
            if abs(hm / hema_ref - 1) > p["hema_tolerancia"]:
                motivo = "nuclear haematoxylin outside ±20 %"
        if motivo:
            excluidas.append({"region": clave_txt(c), "tipo": tipo, "motivo": motivo})
            continue
        lo, hi = MET.clopper_pearson(r["k"], r["n"])
        ctrl = False
        arbol = arboles[tipo]
        if arbol is not None:
            cx, cy = r["centro"]
            radio = p["control_positivo_dist_um"] + r["L"] * math.sqrt(2) / 2
            cerca = arbol.query_ball_point((cx, cy), radio)
            pts = puntos[tipo][cerca] if cerca else np.zeros((0, 2))
            g = r["geom"]
            nfuera = sum(1 for x, y in pts if not g.contains(Point(x, y))
                         and g.distance(Point(x, y)) < p["control_positivo_dist_um"])
            ctrl = nfuera >= minimo[tipo]
        c_ck = None if ck19 is None else ck19.get(c)
        c_p63 = None if p63 is None else p63.get(c)
        if p63_puerta is not None and not (p63_puerta.get(r["fc"]) or {}).get("pasa"):
            c_p63 = "not assessable"
        if not c_ck:
            clase, en_recuento = MET.ROTULOS["sin_ck19"], True
        elif c_p63 in ("periferia_positiva", "not assessable", None):
            clase, en_recuento = MET.ROTULOS["mioepitelio"], False
        else:
            clase, en_recuento = ROTULOS_F3["ne_%s_ck19" % tipo], True
        cands[tipo].append({
            "region": clave_txt(c), "fc": r["fc"], "tipo": tipo, "k": r["k"], "n": r["n"],
            "pct": pct, "ic95": [100 * lo, 100 * hi],
            ("control_positivo_500um" if tipo == "pobre" else "control_bajo_T_500um"): bool(ctrl),
            "rotulo": ROTULOS_F3["ne_%s" % tipo], "clase": clase, "en_recuento": en_recuento,
            "p63": c_p63, "caja_um": [float(v) for v in r["geom"].bounds],
            "ki67_regional_pct": None if ki67_pct is None else ki67_pct.get(c),
            "re_regional_pct": None if re_pct is None else re_pct.get(c)})
    nmin = p["re_pobre_min_n"]
    return {"evaluado": True, "candidatas": cands["pobre"] + cands["rica"],
            "pobres": cands["pobre"], "ricas": cands["rica"], "excluidas": excluidas,
            "n_intermedias": n_intermedias,
            "limite_deteccion": {"n_min": nmin,
                                 "pobre_ic95_sup_k0_pct": 100 * MET.clopper_pearson(0, nmin)[1],
                                 "rica_ic95_inf_kn_pct": 100 * MET.clopper_pearson(nmin, nmin)[0]},
            "fragmentos_evaluados": None if pasan is None else sorted(map(str, pasan)),
            "umbral": ctx.cita(), "declaraciones": [DECLARACIONES_F3["ne_gemela"]],
            "galeria": "L0 crops of the NE marker, CK19, p63, Ki67 and ER at caja_um"}


def regiones_pobres(base, lamina, lector=None, log=print, recall=None):
    """(a-bis) en P-RE o su gemela NE, con galería a L0 del mismo sitio."""
    import laminillas_comun as C
    import laminillas_metricas as MET
    P = _P()
    if lamina not in LAMINAS_REGIONES:
        raise ValueError("regiones-pobres: %s (válidas: %s)" % (lamina, ", ".join(LAMINAS_REGIONES)))
    t = tabla(base, lamina, lector, log)
    inf, estr = carga_p63(base, t["fcs_l"], t["ck"])
    tki = tabla(base, "P-KI67", lector, log)
    tre = tabla(base, "P-RE", lector, log) if lamina in LAMINAS_NE else None
    mini = minipuerta(base, lamina, lector, log, t=t, recall=recall)
    rel = _rel("regiones", "%s.json" % lamina)
    rels = t["rels"] + tki["rels"] + (tre["rels"] if tre else []) + [
        REL_P63, _rel("minipuerta", "%s.json" % lamina)]
    hu = P._huella(base, rels)
    hecho = _hecho(base, "f3-regiones", lamina, hu, rel)
    if hecho is not None:
        return hecho
    regiones = {c: r for c, r in t["regiones"].items() if r["fc"] not in set(t["solo_mapa"])}
    ck19 = _ck19_por_region(t, regiones)
    p63 = _p63_por_region(regiones, estr, inf["puerta"])
    ki = _pct_regional(tki)
    pasan = {f: bool(v["pasa"]) for f, v in mini["por_fc"].items()}
    if lamina == "P-RE":
        res = MET.regiones_re_pobres(t["nuc"], regiones, t["ctx"], "principal", pasan, ck19=ck19,
                                     p63=p63, ki67_pct=ki, p63_puerta=inf["puerta"])
        acompanan = ("P-RE", REFERENCIA, "P-P63", "P-KI67")
    else:
        res = regiones_ne(t["nuc"], regiones, t["ctx"], "principal", pasan, ck19=ck19, p63=p63,
                          ki67_pct=ki, p63_puerta=inf["puerta"], re_pct=_pct_regional(tre))
        acompanan = (lamina, REFERENCIA, "P-P63", "P-KI67", "P-RE")
    galeria = None
    if res.get("evaluado") and res.get("candidatas"):
        galeria = galeria_l0(base, lector, lamina, res["candidatas"], acompanan, t["fcs_l"])
    out = {"tipo": "regiones-pobres" if lamina == "P-RE" else "regiones-ne", "lamina": lamina,
           "marcador": nombre_marcador(lamina), "L_um": t["L"], "resultado": res,
           "minipuerta": {f: {"pasa": v["pasa"], "rotulo": v["rotulo"]}
                          for f, v in mini["por_fc"].items()},
           "p63_fragmentos_que_pasan": inf["fragmentos_que_pasan"], "galeria": galeria,
           "solo_mapa": list(t["solo_mapa"]), "alcance": MET.ROTULOS["region_escaneada"],
           "rotulos_umbral": t["ctx"].rotulos_umbral(), "control_fondo": t["control_fondo"],
           "declaraciones": t["declaraciones"] + [DECLARACIONES_F3["ck19_region"],
                                                  DECLARACIONES_F3["p63_periferia"],
                                                  DECLARACIONES_F3["minipuerta"]],
           "nota": "no region is called tumoural", "metodos": metodos(), "huella": hu}
    _barre(out)
    P._escribe(base, rel, out)
    C.marca_hecho(base, "f3-regiones", lamina, hu, productos=[rel])
    # Sin cifras de resultado en el log (2-oct): cuántas regiones pobres hay es un resultado de
    # diana; vive en SESION.
    log("%s: %s · regiones escritas en %s" % (
        lamina, "evaluado" if res.get("evaluado") else res.get("motivo"), rel))
    return out


def galeria_l0(base, lector, lamina, candidatas, laminas, fcs_l):
    """Recortes a L0 del MISMO sitio en cada lámina (la caja de la región, en µm de P-CK19, se
    lleva a cada lámina con la inversa de la transformada de SU FC; si esa lámina no está
    verificada en ese FC, no hay recorte y se dice). PNG en `f3/galeria/<lámina>/`."""
    import numpy as np
    from PIL import Image
    import laminillas_comun as C
    import laminillas_congela as K
    P = _P()
    lector = P._lector(lector)
    ruta = P.ruta_sello(base)
    margen = PARAMS_F3["galeria_margen_um"]
    orden = sorted(candidatas, key=lambda c: (not c["en_recuento"], c["pct"]
                                              if c.get("tipo", "pobre") == "pobre" else -c["pct"],
                                              c["region"]))
    elegidas = orden[:PARAMS_F3["galeria_max_regiones"]]
    matrices = {}
    for n in laminas:
        if n == REFERENCIA:
            matrices[n] = {f["id"]: np.eye(3) for f in fcs_l}
            continue
        res, _ = resultado_consenso(base, n, fcs_l)
        matrices[n] = {f["id"]: np.asarray(f["matriz_um"], float) for f in res.fragmentos
                       if f.get("pasa") and f.get("matriz_um") is not None}
    d_rel = _rel("galeria", lamina)
    os.makedirs(P._abs(base, d_rel), mode=0o700, exist_ok=True)
    indice, productos, leidas = [], [], set()
    for c in elegidas:
        x0, y0, x1, y1 = c["caja_um"]
        esq = np.array([[x0 - margen, y0 - margen], [x1 + margen, y0 - margen],
                        [x1 + margen, y1 + margen], [x0 - margen, y1 + margen]], float)
        fila = {"region": c["region"], "fc": c["fc"], "clase": c["clase"], "recortes": {}}
        for n in laminas:
            M = matrices[n].get(c["fc"])
            if M is None:
                fila["recortes"][n] = {"motivo": "not registered in this fragment"}
                continue
            lam = lector.abre(n)
            mpp = float(lam.mpp_l0)
            inv = np.linalg.inv(M)
            xy = (esq @ inv[:2, :2].T + inv[:2, 2]) / mpp
            px0, py0 = (int(math.floor(v)) for v in xy.min(0))
            px1, py1 = (int(math.ceil(v)) for v in xy.max(0))
            rgb = np.asarray(lector.lee_region(lam, mpp, px0, py0, px1 - px0, py1 - py0))[..., :3]
            if n not in SUELO and n not in leidas:
                K.registra_lectura_diana(ruta, n, "galeria-f3")
                leidas.add(n)
            rel = os.path.join(d_rel, "%s__%s.png" % (c["region"].replace(":", "_")
                                                      .replace(",", "_"), n))
            Image.fromarray(np.ascontiguousarray(rgb.astype(np.uint8))).save(P._abs(base, rel))
            os.chmod(P._abs(base, rel), 0o600)
            productos.append(rel)
            fila["recortes"][n] = {"png": rel, "caja_l0": [px0, py0, px1, py1], "mpp": mpp}
        indice.append(fila)
    rel_ind = os.path.join(d_rel, "indice.json")
    P._escribe(base, rel_ind, {"lamina": lamina, "laminas": list(laminas), "nivel": "L0",
                               "margen_um": margen, "regiones": indice,
                               "n_candidatas": len(candidatas), "n_en_galeria": len(elegidas),
                               "orden": "counted first, then by %%; capped at %d"
                                        % PARAMS_F3["galeria_max_regiones"]})
    C.marca_hecho(base, "f3-galeria", lamina, productos=[rel_ind] + productos)
    return {"indice": rel_ind, "n": len(elegidas), "n_candidatas": len(candidatas)}


# ── 4. (b) lectura digital frente a visual ───────────────────────────────────────────────────
def biopsias_virtuales(nuc, fcs, ctx, denominador, pct_global, excluir=()):
    """La ventana sellada del hotspot (0,5 mm, ≥500 núcleos del denominador, paso 50 µm, interior)
    en cada posición válida de ESTA lámina: distribución del % y cuánto se aparta del global."""
    import numpy as np
    import laminillas_metricas as MET
    p = ctx.p
    fcs_v = {f: pol for f, pol in fcs.items() if f not in set(excluir)}
    centros, ids = MET._ventanas_hotspot(fcs_v, p)
    m = nuc.mascara(denominador)
    W, idx_den = MET._incidencia(nuc, m, centros, ids, p["hotspot_diametro_um"] / 2)
    base = {"ventana": {"diametro_um": p["hotspot_diametro_um"], "paso_um": p["hotspot_paso_um"],
                        "min_nucleos": p["hotspot_min_nucleos"], "interior_um": p["interior_um"]},
            "nota": DECLARACIONES_F3["lectura"], "n_ventanas_total": int(len(centros))}
    if W is None:
        return dict(base, n_ventanas_validas=0)
    pos = (nuc.dab[idx_den] > ctx.T).astype(np.float32)
    n = np.asarray(W.sum(1)).ravel()
    k = W @ pos
    val = n >= p["hotspot_min_nucleos"]
    if not val.any():
        return dict(base, n_ventanas_validas=0)
    pct = 100.0 * k[val] / n[val]
    q = np.percentile(pct, [0, 5, 25, 50, 75, 95, 100])
    nombres = ("min", "p5", "p25", "p50", "p75", "p95", "max")
    por_fc = {}
    for f in sorted(set(ids[val]), key=str):
        v = pct[ids[val] == f]
        qf = np.percentile(v, [0, 5, 50, 95, 100])
        por_fc[str(f)] = dict(zip(("min", "p5", "p50", "p95", "max"), map(float, qf)),
                              n_ventanas=int(len(v)))
    dif = np.abs(pct - pct_global) if pct_global == pct_global else None
    aparta = ({"%g" % u: float(np.mean(dif > u)) for u in PARAMS_F3["lectura_aparta_puntos"]}
              if dif is not None else None)
    return dict(base, n_ventanas_validas=int(val.sum()),
                cuantiles=dict(zip(nombres, map(float, q))),
                rango_p5_p95_puntos=float(q[5] - q[1]), rango_total_puntos=float(q[6] - q[0]),
                fraccion_que_se_aparta_del_global=aparta, por_fc=por_fc,
                distribucion_pct=[round(float(v), 2) for v in np.sort(pct)])


def _lectura_visual(base, lamina, bio, filas):
    """La lectura visual solo desde el cotejo verificado de F4 (`f3/lectura_visual.json`)."""
    import numpy as np
    P = _P()
    if not os.path.isfile(P._abs(base, REL_VISUAL)):
        return {"estado": "not provided", "nota": "visual reading enters only from the F4 check "
                                                  "against the original report"}
    d = (P._lee(base, REL_VISUAL) or {}).get(lamina)
    if not d:
        return {"estado": "not provided", "nota": "no verified visual reading for this slide"}
    if not d.get("verificado_contra_original"):
        return {"estado": ROTULOS_F3["no_verificado_original"]}
    if not d.get("mismo_cristal"):
        return {"estado": ROTULOS_F3["otra_tanda"]}
    v = float(d["pct"])
    out = {"estado": "same glass", "pct_visual": v, "fuente": d.get("fuente"),
           "pagina": d.get("pagina")}
    dist = np.asarray(bio.get("distribucion_pct") or [], float)
    if len(dist):
        q = bio["cuantiles"]
        out.update({"percentil_en_biopsias_virtuales": float(100 * np.mean(dist <= v)),
                    "dentro_p5_p95": bool(q["p5"] <= v <= q["p95"]),
                    "dentro_min_max": bool(q["min"] <= v <= q["max"])})
    out["fragmentos_cuyo_rango_regional_lo_contiene"] = sorted(
        f["fc"] for f in filas if f["regiones"]["min"] is not None
        and f["regiones"]["min"] <= v <= f["regiones"]["max"])
    return out


def lectura_digital(base, lamina, lector=None, log=print):
    """(b): tabla por fragmento (todo e interior, tres denominadores, IC por bloques) y biopsias
    virtuales de tamaño prefijado en ESA lámina; la lectura visual, solo verificada."""
    import numpy as np
    import laminillas_comun as C
    import laminillas_metricas as MET
    P = _P()
    if lamina not in LAMINAS_LECTURA:
        raise ValueError("lectura-digital: %s (válidas: %s)" % (lamina, ", ".join(LAMINAS_LECTURA)))
    t = tabla(base, lamina, lector, log)
    rel = _rel("lectura", "%s.json" % lamina)
    hu = P._huella(base, t["rels"] + [REL_VISUAL])
    hecho = _hecho(base, "f3-lectura", lamina, hu, rel)
    if hecho is not None:
        return hecho
    nuc, ctx = t["nuc"], t["ctx"]
    p = ctx.p
    glob = MET.porcentaje_global(nuc, t["regiones"], ctx, "principal", solo_mapa=t["solo_mapa"])
    filas = []
    for fid in sorted(t["fcs"]):
        sub = _solo_fc(nuc, fid)
        regs = {c: r for c, r in t["regiones"].items() if r["fc"] == fid}
        inter = {c: r for c, r in regs.items() if r["dist_borde_um"] > p["interior_um"]}
        todo = MET.porcentaje_global(sub, regs, ctx, "principal")
        dentro = MET.porcentaje_global(sub, inter, ctx, "principal")
        v = np.array([r["pct"] for r in MET.validas(regs, p).values()], float)
        filas.append({
            "fc": fid, "solo_mapa": fid in t["solo_mapa"],
            "todo": {k: todo.get(k) for k in ("pct", "k", "n", "n_regiones", "mas_ancho",
                                              "pct_por_denominador", "rotulos")},
            "interior": {k: dentro.get(k) for k in ("pct", "k", "n", "n_regiones", "mas_ancho")},
            "regiones": {"n_validas": int(len(v)),
                         "min": float(v.min()) if len(v) else None,
                         "p25": float(np.percentile(v, 25)) if len(v) else None,
                         "p50": float(np.median(v)) if len(v) else None,
                         "p75": float(np.percentile(v, 75)) if len(v) else None,
                         "max": float(v.max()) if len(v) else None}})
    bio = biopsias_virtuales(nuc, t["fcs"], ctx, "principal", glob["pct"], excluir=t["solo_mapa"])
    out = {"tipo": "lectura-digital", "lamina": lamina, "marcador": nombre_marcador(lamina),
           "L_um": t["L"], "umbral": ctx.cita(), "alcance": MET.ROTULOS["region_escaneada"],
           "global": {k: glob.get(k) for k in ("pct", "k", "n", "n_regiones", "mas_ancho",
                                               "intervalos", "pct_por_denominador", "rotulos",
                                               "declaraciones")},
           "por_fragmento": filas, "biopsias_virtuales": bio,
           "lectura_visual": _lectura_visual(base, lamina, bio, filas),
           "control_fondo": t["control_fondo"], "solo_mapa": list(t["solo_mapa"]),
           "declaraciones": t["declaraciones"] + [DECLARACIONES_F3["lectura"]],
           "nota": ("same glass only: how much a % changes with the field chosen on this slide"),
           "metodos": metodos(), "huella": hu}
    _barre(out)
    P._escribe(base, rel, out)
    C.marca_hecho(base, "f3-lectura", lamina, hu, productos=[rel])
    log("%s: %% global %.1f; biopsias virtuales p5-p95 %s" % (
        lamina, glob["pct"] if glob["pct"] == glob["pct"] else float("nan"),
        "%.1f-%.1f" % (bio["cuantiles"]["p5"], bio["cuantiles"]["p95"])
        if bio.get("cuantiles") else "—"))
    return out


# ── 5. ROI de Carlos ─────────────────────────────────────────────────────────────────────────
_NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
       "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
_RE_SLIDE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
RASTER = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff")


def _rels(z, parte):
    """{rId: ruta en el zip} de una parte del paquete."""
    import posixpath
    from xml.etree import ElementTree as ET
    d, f = posixpath.split(parte)
    rp = posixpath.join(d, "_rels", f + ".rels")
    if rp not in z.namelist():
        return {}
    out = {}
    for r in ET.fromstring(z.read(rp)).findall("rel:Relationship", _NS):
        tgt = r.get("Target") or ""
        out[r.get("Id")] = (tgt.lstrip("/") if tgt.startswith("/")
                            else posixpath.normpath(posixpath.join(d, tgt)))
    return out


def _orden_diapositivas(z):
    """Partes de diapositiva en el orden de la presentación (sldIdLst); si no, por número."""
    from xml.etree import ElementTree as ET
    nombres = z.namelist()
    if "ppt/presentation.xml" in nombres:
        rels = _rels(z, "ppt/presentation.xml")
        raiz = ET.fromstring(z.read("ppt/presentation.xml"))
        orden = [rels.get(s.get("{%s}id" % _NS["r"]))
                 for s in raiz.iterfind("p:sldIdLst/p:sldId", _NS)]
        orden = [o for o in orden if o in nombres]
        if orden:
            return orden
    return sorted((n for n in nombres if _RE_SLIDE.match(n)),
                  key=lambda n: int(_RE_SLIDE.match(n).group(1)))


def _centro(el):
    off = el.find(".//a:xfrm/a:off", _NS)
    ext = el.find(".//a:xfrm/a:ext", _NS)
    if off is None:
        return None
    w = float(ext.get("cx", 0)) if ext is not None else 0.0
    h = float(ext.get("cy", 0)) if ext is not None else 0.0
    return (float(off.get("x", 0)) + w / 2, float(off.get("y", 0)) + h / 2)


def lee_pptx(ruta):
    """Diapositivas del pptx: imágenes (bytes, nombre del medio, posición) y textos. Solo lectura,
    en memoria."""
    from xml.etree import ElementTree as ET
    out = []
    with zipfile.ZipFile(ruta) as z:
        for k, parte in enumerate(_orden_diapositivas(z), start=1):
            raiz = ET.fromstring(z.read(parte))
            rels = _rels(z, parte)
            textos = []
            for sp in raiz.iter("{%s}sp" % _NS["p"]):
                txt = " ".join(t.text for t in sp.iter("{%s}t" % _NS["a"]) if t.text)
                txt = re.sub(r"\s+", " ", txt).strip()
                if txt:
                    textos.append({"texto": txt[:PARAMS_F3["roi_etiqueta_max"]],
                                   "centro": _centro(sp)})
            imagenes = []
            for pic in raiz.iter("{%s}pic" % _NS["p"]):
                blip = pic.find(".//a:blip", _NS)
                rid = None if blip is None else blip.get("{%s}embed" % _NS["r"])
                medio = rels.get(rid)
                if not medio or medio not in z.namelist():
                    continue
                imagenes.append({"medio": os.path.basename(medio), "bytes": z.read(medio),
                                 "centro": _centro(pic)})
            out.append({"diapositiva": k, "textos": textos, "imagenes": imagenes})
    return out


def etiqueta(imagen, textos):
    """Texto más cercano (distancia entre centros); sin posición, el primero de la diapositiva."""
    if not textos:
        return None
    c = imagen.get("centro")
    con = [t for t in textos if t.get("centro") is not None]
    if c is None or not con:
        return textos[0]["texto"]
    return min(con, key=lambda t: math.hypot(t["centro"][0] - c[0], t["centro"][1] - c[1]))["texto"]


def _estira(odsum, mascara=None):
    import numpy as np
    v = odsum[mascara] if mascara is not None and mascara.any() else odsum.ravel()
    s = max(float(np.percentile(v, PARAMS_F3["roi_estira_percentil"])) if v.size else 0.0,
            PARAMS_F3["roi_estira_min"])
    return s


def _sift(img, p):
    import numpy as np
    from skimage.feature import SIFT
    s = SIFT(upsampling=int(p["sift_upsampling"]))
    try:
        s.detect_and_extract(img)
    except RuntimeError:
        return np.zeros((0, 2)), np.zeros((0, 128), np.uint8)
    kp = np.asarray(s.keypoints, float)
    return np.c_[kp[:, 1] + 0.5, kp[:, 0] + 0.5], np.asarray(s.descriptors)


def rasgos_captura(rgb, p):
    """SIFT de una captura: ODsum con su I0 (percentil alto propio), estirada por su percentil."""
    import numpy as np
    import laminillas_registro as R
    rgb = np.asarray(rgb)[..., :3]
    f = 1.0
    lado = max(rgb.shape[:2])
    if lado > PARAMS_F3["roi_max_lado_px"]:
        from PIL import Image
        f = PARAMS_F3["roi_max_lado_px"] / float(lado)
        rgb = np.asarray(Image.fromarray(rgb.astype(np.uint8)).resize(
            (int(rgb.shape[1] * f), int(rgb.shape[0] * f)), Image.BILINEAR))
    i0 = np.maximum(np.percentile(rgb.reshape(-1, 3), PARAMS_F3["roi_i0_percentil"], axis=0),
                    PARAMS_F3["roi_i0_min"])
    od = R.od(rgb.astype(np.float32), i0[None, None, :]).sum(-1)
    s = _estira(od, od > PARAMS_F3["roi_tejido_od"])
    xy, desc = _sift(np.clip(od / s, 0, 1).astype(np.float32), p)
    return xy / f, desc


def rasgos_lamina_x4(lector, nombre, p, log=print):
    """SIFT del nivel ×4 nativo de una lámina, por teselas con solape (solo las que tienen
    tejido); coordenadas en px del ×4. Devuelve (xy, desc, mpp_x4, factor ×4/L0)."""
    import numpy as np
    from shapely.geometry import box
    import laminillas_registro as R
    lam = lector.abre(nombre)
    mpp4 = R.mpp_nivel(lam, 4)
    f = mpp4 / float(lam.mpp_l0)
    W0, H0 = lam.dimensiones_l0
    w4, h4 = int(W0 / f), int(H0 / f)
    T, S = int(PARAMS_F3["roi_tesela_px"]), int(PARAMS_F3["roi_solape_px"])
    zona = lector.zona_escaneada(lam)
    # estiramiento por lámina (no por tesela): percentil de la ODsum del tejido a ×8
    mpp8 = R.mpp_nivel(lam, 8)
    w8, h8 = int(W0 * lam.mpp_l0 / mpp8), int(H0 * lam.mpp_l0 / mpp8)
    od8 = R.od(R._lee(lector, lam, mpp8, 0, 0, w8, h8).astype(np.float32),
               R.campo_i0(lector, lam, mpp8, 0, 0, w8, h8)).sum(-1)
    s = _estira(od8, od8 > PARAMS_F3["roi_tejido_od"])
    del od8
    xs, ds = [], []
    for y0 in range(0, max(1, h4 - S), T - S):
        for x0 in range(0, max(1, w4 - S), T - S):
            w, h = min(T, w4 - x0), min(T, h4 - y0)
            if w < 32 or h < 32:
                continue
            if zona is not None and not zona.intersects(box(x0 * f, y0 * f, (x0 + w) * f,
                                                            (y0 + h) * f)):
                continue
            rgb = R._lee(lector, lam, mpp4, x0 * f, y0 * f, w, h).astype(np.float32)
            od = R.od(rgb, R.campo_i0(lector, lam, mpp4, x0 * f, y0 * f, w, h)).sum(-1)
            if np.mean(od > PARAMS_F3["roi_tejido_od"]) < PARAMS_F3["roi_tejido_min_frac"]:
                continue
            xy, desc = _sift(np.clip(od / s, 0, 1).astype(np.float32), p)
            if not len(xy):
                continue
            lo_x = S / 2 if x0 > 0 else 0
            hi_x = w - S / 2 if x0 + w < w4 else w + 1
            lo_y = S / 2 if y0 > 0 else 0
            hi_y = h - S / 2 if y0 + h < h4 else h + 1
            ok = (xy[:, 0] >= lo_x) & (xy[:, 0] < hi_x) & (xy[:, 1] >= lo_y) & (xy[:, 1] < hi_y)
            xs.append(xy[ok] + [x0, y0])
            ds.append(desc[ok])
    if not xs:
        return np.zeros((0, 2)), np.zeros((0, 128), np.uint8), mpp4, f
    return np.vstack(xs), np.vstack(ds), mpp4, f


def empareja(dc, ds, ratio, cruzado=True, bloque=None):
    """Como `skimage.feature.match_descriptors(metric euclídea, max_ratio, cross_check)`, por
    bloques (memoria). Devuelve pares (i_captura, j_lámina)."""
    import numpy as np
    bloque = int(bloque or PARAMS_F3["roi_bloque_desc"])
    a = np.asarray(dc, np.float32)
    b = np.asarray(ds, np.float32)
    nc, ns = len(a), len(b)
    if nc < 1 or ns < 2:
        return np.zeros((0, 2), int)
    aa = (a * a).sum(1)
    mejor = np.full(nc, np.inf, np.float64)
    segundo = np.full(nc, np.inf, np.float64)
    arg = np.full(nc, -1, int)
    inv = np.empty(ns, int)
    for s0 in range(0, ns, bloque):
        blk = b[s0:s0 + bloque]
        d2 = aa[:, None] + (blk * blk).sum(1)[None, :] - 2.0 * (a @ blk.T)
        np.maximum(d2, 0, out=d2)
        inv[s0:s0 + len(blk)] = np.argmin(d2, axis=0)
        if d2.shape[1] >= 2:
            dos = np.argpartition(d2, 1, axis=1)[:, :2]
            v = np.take_along_axis(d2, dos, 1)
            o = np.argsort(v, axis=1)
            dos, v = np.take_along_axis(dos, o, 1), np.take_along_axis(v, o, 1)
        else:
            dos = np.zeros((nc, 1), int)
            v = d2
        c1, c2 = v[:, 0], (v[:, 1] if v.shape[1] > 1 else np.full(nc, np.inf))
        nuevo_mejor = c1 < mejor
        # segundo = el menor de: (mejor viejo si lo supera el nuevo) / segundo viejo / segundo nuevo
        seg = np.where(nuevo_mejor, np.minimum(mejor, c2), np.minimum(segundo, c1))
        arg = np.where(nuevo_mejor, dos[:, 0] + s0, arg)
        mejor = np.where(nuevo_mejor, c1, mejor)
        segundo = seg
    d1, d2_ = np.sqrt(mejor), np.sqrt(segundo)
    d2_[d2_ == 0] = np.finfo(np.float64).eps
    ok = (arg >= 0) & (d1 / d2_ < ratio)
    if cruzado:
        ok &= inv[np.clip(arg, 0, ns - 1)] == np.arange(nc)
    i = np.nonzero(ok)[0]
    return np.c_[i, arg[i]]


def ajusta_similitud(src, dst, p):
    """RANSAC con SimilarityTransform (escala libre en [0,25; 4]); residual = el sellado, en px
    del ×4. Devuelve (modelo o None, inliers)."""
    import numpy as np
    from skimage.measure import ransac
    from skimage.transform import SimilarityTransform
    lo, hi = PARAMS_F3["roi_escala"]
    if len(src) < max(int(p["min_samples"]), 3):
        return None, np.zeros(len(src), bool)

    def valido(m, *_):
        return lo <= float(m.scale) <= hi

    modelo, inl = ransac((np.asarray(src, float), np.asarray(dst, float)), SimilarityTransform,
                         min_samples=int(p["min_samples"]),
                         residual_threshold=float(p["residual_px"]),
                         max_trials=int(p["max_trials"]), is_model_valid=valido,
                         rng=np.random.default_rng(int(p["semilla"])))
    if modelo is None or not modelo or inl is None:
        return None, np.zeros(len(src), bool)
    return modelo, np.asarray(inl, bool)


def asigna_roi(inliers):
    """{lámina: inliers} → (lámina o None, mejor, segunda). Más inliers, ≥30 y ≥2× la segunda."""
    orden = sorted(inliers.items(), key=lambda kv: (-kv[1], kv[0]))
    if not orden:
        return None, 0, 0
    (n1, k1), k2 = orden[0], (orden[1][1] if len(orden) > 1 else 0)
    ok = k1 >= PARAMS_F3["roi_min_inliers"] and k1 >= PARAMS_F3["roi_margen"] * k2
    return (n1 if ok else None), int(k1), int(k2)


def roi_carlos(base, lector=None, log=print, pptx=None, hoja=None, laminas=PRIMARIO):
    """ROI de Carlos: cada imagen del pptx contra el ×4 de las 13 láminas del primario."""
    import io
    import numpy as np
    from PIL import Image
    import laminillas_comun as C
    import laminillas_congela as K
    import laminillas_registro as R
    import laminillas_sello as SL
    P = _P()
    if pptx is None or hoja is None:
        import laminillas_ingesta as I
        pptx, hoja = pptx or I.PPTX_ORIGEN, hoja or I.HOJA_CLAVES
    if not os.path.isfile(pptx):
        raise P.FaltaEntrada("falta el pptx en ORIGEN: corre antes la ingesta (`extrae`)")
    if not os.path.isfile(hoja):
        raise P.FaltaEntrada("falta la hoja de claves en ORIGEN: corre antes la ingesta")
    with open(hoja, encoding="utf-8") as fh:
        esperado = (json.load(fh).get("pptx") or {}).get("sha256")
    sha = C.sha256_fichero(pptx)
    if not esperado or sha != esperado:
        raise P.NoPasa("el pptx de ORIGEN no casa con el sha256 de la hoja de claves: no lo uso")
    ruta = P.ruta_sello(base)
    SL.exige(ruta, [REFERENCIA], SL.SECCIONES_MODULO_B)
    p, _, _, _ = R._params(ruta, REFERENCIA, "P-KI67")
    rel = _rel("roi", "roi.json")
    hu = hashlib.sha256((sha + P._huella(base, ["congelacion.json", "manifiesto.json"]) +
                         ",".join(laminas)).encode()).hexdigest()[:16]
    hecho = _hecho(base, "f3-roi", "deck", hu, rel)
    if hecho is not None:
        log("roi-carlos: ya hecho con este pptx y estas láminas; salto")
        return hecho
    lector = P._lector(lector)
    diapos = lee_pptx(pptx)
    capturas, entradas = [], []
    for d in diapos:
        if not d["imagenes"]:
            lab = d["textos"][0]["texto"] if d["textos"] else None
            entradas.append({"diapositiva": d["diapositiva"], "label_in_deck": lab,
                             "estado": "sin imagen",
                             "informe": ROTULOS_F3["roi"].format(x=lab or "-", y="no image")})
            continue
        for k, im in enumerate(d["imagenes"]):
            lab = etiqueta(im, d["textos"])
            e = {"diapositiva": d["diapositiva"], "imagen": k + 1, "medio": im["medio"],
                 "label_in_deck": lab}
            ext = os.path.splitext(im["medio"])[1].lower()
            if ext not in RASTER:
                e.update(estado="no evaluable", motivo="not a raster image (%s)" % ext,
                         informe=ROTULOS_F3["roi"].format(x=lab or "-", y="not evaluable"))
                entradas.append(e)
                continue
            try:
                rgb = np.asarray(Image.open(io.BytesIO(im["bytes"])).convert("RGB"))
            except Exception as ex:                                  # noqa: BLE001
                e.update(estado="no evaluable", motivo="image not decodable (%s)" % type(ex).__name__,
                         informe=ROTULOS_F3["roi"].format(x=lab or "-", y="not evaluable"))
                entradas.append(e)
                continue
            xy, desc = rasgos_captura(rgb, p)
            e.update(dimensiones_px=[int(rgb.shape[1]), int(rgb.shape[0])],
                     n_rasgos=int(len(xy)), inliers={}, _xy=xy, _desc=desc,
                     _forma=rgb.shape[:2], _modelos={})
            capturas.append(e)
            entradas.append(e)
    info_lam = {}
    for n in laminas:
        if n not in SUELO:
            K.registra_lectura_diana(ruta, n, "roi-f3")
        xy4, d4, mpp4, f = rasgos_lamina_x4(lector, n, p, log)
        info_lam[n] = {"mpp_x4": mpp4, "factor_x4_l0": f, "n_rasgos": int(len(xy4))}
        for e in capturas:
            pares = empareja(e["_desc"], d4, float(p["ratio"]), bool(p["cross_check"]))
            if len(pares) < 2:
                e["inliers"][n] = 0
                continue
            modelo, inl = ajusta_similitud(e["_xy"][pares[:, 0]], xy4[pares[:, 1]], p)
            e["inliers"][n] = int(inl.sum()) if modelo is not None else 0
            if modelo is not None:
                e["_modelos"][n] = modelo
        log("roi-carlos: %s (%d rasgos a ×4)" % (n, len(xy4)))
    for e in capturas:
        n, k1, k2 = asigna_roi(e["inliers"])
        e.update(mejor_inliers=k1, segunda_inliers=k2)
        if n is None:
            e.update(estado=ESTADO_NO_LOCALIZADA, lamina=None,
                     informe=ROTULOS_F3["roi"].format(x=e["label_in_deck"] or "-",
                                                      y=ROTULOS_F3["roi_no"]))
        else:
            m = e["_modelos"][n]
            f = info_lam[n]["factor_x4_l0"]
            h, w = e["_forma"]
            esq = m(np.array([[0, 0], [w, 0], [w, h], [0, h]], float)) * f
            ang = math.degrees(float(m.rotation))
            e.update(estado=ESTADO_LOCALIZADA, lamina=n, marcador=nombre_marcador(n),
                     transformada={"escala_captura_a_x4": float(m.scale),
                                   "rotacion_grados": ang,
                                   "traslacion_x4_px": [float(v) for v in m.translation],
                                   "mpp_estimado_captura": float(m.scale) *
                                   info_lam[n]["mpp_x4"]},
                     huella_l0_px=[[float(x), float(y)] for x, y in esq],
                     centro_l0_px=[float(v) for v in esq.mean(0)],
                     informe=ROTULOS_F3["roi"].format(x=e["label_in_deck"] or "-",
                                                      y="%s (%s)" % (n, nombre_marcador(n))))
    for e in entradas:
        for k in [k for k in e if k.startswith("_")]:
            del e[k]
    out = {"tipo": "roi-carlos", "pptx_sha256": sha, "laminas": list(laminas),
           "nivel": "x4 (native)", "criterio": ("SIFT (sealed registration parameters) + RANSAC "
                                                "SimilarityTransform, free scale 0.25-4; assigned "
                                                "to the slide with most inliers if >=30 and >=2x "
                                                "the second; otherwise not localized"),
           "rotulo": ROTULOS_F3["roi_control"], "capturas": entradas, "laminas_info": info_lam,
           "n_localizadas": sum(1 for e in entradas if e.get("estado") == ESTADO_LOCALIZADA),
           "declaraciones": [DECLARACIONES_F3["roi_imagen"], DECLARACIONES_F3["roi_etiqueta"]],
           "metodos": metodos(), "huella": hu}
    _barre(out)
    P._escribe(base, rel, out)
    C.marca_hecho(base, "f3-roi", "deck", hu, productos=[rel])
    for e in entradas:
        log(e["informe"])
    return out


# ── despacho (lo llama `laminillas_proc.analisis`) ────────────────────────────────────────────
def orden(base, args, lector=None, log=print, ejecutor_valis=None):
    """Una orden de F3 (parte A). Devuelve el código de salida (los de `laminillas_proc`)."""
    import laminillas_sello as SL
    P = _P()
    validas = tuple(P.ORDENES_F3) + tuple(P.ORDENES_ORIGEN)
    if not args or args[0] not in validas:
        print("orden de F3 desconocida; válidas: %s" % ", ".join(validas), file=sys.stderr)
        return P.RC_USO
    nombre, resto = args[0], args[1:]
    laminas = [a for a in resto if not a.startswith("--")]
    banderas = [a for a in resto if a.startswith("--")]
    try:
        if banderas and (nombre != "consenso" or banderas != ["--valis"]):
            raise ValueError("bandera no admitida: %s" % " ".join(banderas))
        if nombre == "consenso":
            if laminas:
                raise ValueError("consenso: sin láminas (son las 11 IHQ con tejido)")
            bf = consenso(base, lector, valis=bool(banderas), log=log,
                          ejecutor_valis=ejecutor_valis)
            print("consenso: %d FC, %.2f mm²" % (len(bf["fragmentos"]), bf["area_total_mm2"]))
        elif nombre == "puerta-p63":
            out = puerta_p63(base, laminas, lector, log)
            print("puerta de p63: pasan %s" % (", ".join(out["puerta"]["fragmentos_que_pasan"])
                                               or "ninguno"))
        elif nombre == "regiones-pobres":
            if len(laminas) != 1:
                raise ValueError("regiones-pobres: una lámina (%s)" % ", ".join(LAMINAS_REGIONES))
            regiones_pobres(base, laminas[0], lector, log)
            print("%s: regiones pobres escritas (cifras solo en SESION)" % laminas[0])
        elif nombre == "lectura-digital":
            if len(laminas) != 1:
                raise ValueError("lectura-digital: una lámina (%s)" % ", ".join(LAMINAS_LECTURA))
            lectura_digital(base, laminas[0], lector, log)
            print("%s: lectura digital hecha" % laminas[0])
        elif nombre == "roi-carlos":
            if laminas:
                raise ValueError("roi-carlos: sin argumentos")
            if os.environ.get("BTP_VENTANILLA") != "1":
                raise ValueError("roi-carlos solo por la ventanilla (`procesa laminillas_roi`): "
                                 "lee ORIGEN dentro de su jaula")
            out = roi_carlos(base, lector, log)
            print("roi-carlos: %d de %d imagen(es) localizada(s)" % (
                out["n_localizadas"], sum(1 for e in out["capturas"] if "inliers" in e)))
    except (SL.SelloAusente, SL.SelloInvalido) as e:
        print("%s: NO MIDO · %s: %s" % (nombre, type(e).__name__, e), file=sys.stderr)
        return P.RC_SELLO
    except P.FaltaEntrada as e:
        print("%s: FALTA · %s" % (nombre, e), file=sys.stderr)
        return P.RC_FALTA
    except P.NoPasa as e:
        print("%s: NO PASA · %s" % (nombre, e), file=sys.stderr)
        return P.RC_NO_PASA
    except P.ValisNoCorrio as e:
        print("%s: ERROR DE EJECUCIÓN · %s" % (nombre, e), file=sys.stderr)
        return P.RC_EJECUCION
    except ValueError as e:
        print("%s: %s" % (nombre, e), file=sys.stderr)
        return P.RC_USO
    return 0
