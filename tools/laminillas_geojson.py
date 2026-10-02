#!/usr/bin/env python3
"""tools/laminillas_geojson.py — GeoJSON para QuPath de cada lámina del piloto (F4).

Plan «laminillas DFCI», F4 «GeoJSON para QuPath»: en píxeles de L0 de ESA lámina, con núcleos
(clasificación y medidas), regiones (con su % e IC) y una capa editable «in situ / exclude», vacía
fuera de la zona p63: el patólogo la dibuja y `recalcula_con_exclusiones` rehace los %. Tabla nombre
opaco ↔ marcador en el miembro extra `laminillas` del FeatureCollection (RFC 7946 §6.1). En ninguna
cadena va una ruta ni un nombre de imagen (F5; la Puerta de N1 lo mira en todo el GeoJSON).

Formato de QuPath [inferido de su exportación 0.4+; QuPath no se ha abierto: «not opened in
QuPath»]: `properties.objectType` ("detection" | "annotation"), `classification` {name, color},
`measurements` {nombre: número}, `isLocked`, `name`.

Validación: esquema oficial de GeoJSON (`laminillas_geojson_esquema.json`, bajado de
https://geojson.org/schema/FeatureCollection.json el 1-oct-26, sha256 en ESQUEMA_SHA256) con
jsonschema, más lo que el esquema no mira: anillos cerrados, números finitos, sin NaN.

SELLO: la clasificación Positive/Negative de los núcleos sale del `Contexto` de
`laminillas_metricas` (T sellado, o elevado por el control de fondo), nunca de un T suelto: sin
sello no hay contexto de una lámina diana, así que tampoco GeoJSON. La cita del umbral es
obligatoria en la colección y cada detección lleva el T con el que se clasificó (se coteja).
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
ESQUEMA = os.path.join(_AQUI, "laminillas_geojson_esquema.json")
ESQUEMA_SHA256 = "83e781f1513e447be9287b2180c300621f8b4c65245d7822a22fcf39431e0471"
# La capa del plan es «in situ / exclude»; se escribe sin espacios junto a la barra porque la
# Puerta de N1 lee « /» como el principio de una ruta en CUALQUIER cadena de un GeoJSON.
# Desviación del texto del plan: declarada en METODOS (va a Métodos del informe).
CAPA_EDITABLE = "in situ/exclude"
METODOS = {
    "capa": ("editable layer named in situ/exclude: the plan writes it with spaces around the "
             "slash, and the N1 gate reads a space followed by a slash as the start of a path"),
    "recalculo": ("after the pathologist draws on the editable layer, the % is recomputed for the "
                  "three denominators, per region and at both ends of the T band"),
}
COLORES = {"Positive": [200, 40, 40], "Negative": [60, 60, 200], CAPA_EDITABLE: [250, 200, 0],
           "Region": [0, 160, 80]}
# Igual que la Puerta de N1 (puerta_n1.RE_RUTA): ni rutas ni nombres de imagen en properties.
RE_RUTA = re.compile(r"(?:^|[\s\"'])(?:/|~/|[A-Za-z]:\\)|\.(?:tiff?|svs|ndpi|mrxs|czi|scn|png|"
                     r"jpe?g|zip|pptx)\b", re.I)


class GeoJSONError(ValueError):
    pass


def _num(v):
    v = float(v)
    if not math.isfinite(v):
        return None
    return round(v, 4)


def _coords(seq, nd=2):
    return [[round(float(x), nd), round(float(y), nd)] for x, y in seq]


def solo_superficie(geom):
    """De una GeometryCollection (lo que deja la intersección de una celda o un FC con otro
    polígono cuando se tocan en un borde o un vértice), solo sus partes con área: las líneas y
    los puntos sueltos tienen área 0 y no cambian ninguna medida. Sin ninguna parte con área,
    GeoJSONError (2-oct, piloto real: `piloto-i` se paraba aquí)."""
    from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
    if not isinstance(geom, GeometryCollection) or isinstance(geom, MultiPolygon):
        return geom
    partes = []
    for g in geom.geoms:
        g = solo_superficie(g)
        if isinstance(g, Polygon) and not g.is_empty and g.area > 0:
            partes.append(g)
        elif isinstance(g, MultiPolygon):
            partes += [p for p in g.geoms if not p.is_empty and p.area > 0]
    if not partes:
        raise GeoJSONError("geometría sin superficie: %s" % geom.geom_type)
    return partes[0] if len(partes) == 1 else MultiPolygon(partes)


def geometria(geom):
    """shapely (px L0) → geometría GeoJSON; exterior antihorario (regla de la mano derecha).
    GeometryCollection: solo sus partes con área (`solo_superficie`)."""
    from shapely.geometry import MultiPolygon, Point, Polygon
    from shapely.geometry.polygon import orient
    geom = solo_superficie(geom)
    if isinstance(geom, Point):
        return {"type": "Point", "coordinates": [round(geom.x, 2), round(geom.y, 2)]}
    if isinstance(geom, Polygon):
        g = orient(geom, 1.0)
        return {"type": "Polygon", "coordinates": [_coords(g.exterior.coords)] +
                [_coords(r.coords) for r in g.interiors]}
    if isinstance(geom, MultiPolygon):
        return {"type": "MultiPolygon", "coordinates": [
            [_coords(orient(p, 1.0).exterior.coords)] + [_coords(r.coords) for r in orient(p, 1.0).interiors]
            for p in geom.geoms]}
    raise GeoJSONError("geometría no admitida: %s" % geom.geom_type)


def feature(geom, tipo, clase=None, medidas=None, nombre=None, bloqueado=False):
    props = {"objectType": tipo, "isLocked": bool(bloqueado)}
    if clase is not None:
        props["classification"] = {"name": clase, "color": COLORES.get(clase, [128, 128, 128])}
    if medidas:
        props["measurements"] = {k: v for k, v in ((k, _num(v)) for k, v in medidas.items())
                                 if v is not None}
    if nombre:
        props["name"] = nombre
    # Sin "id": un UUID es una tira hexadecimal que, con cientos de miles de núcleos, acaba
    # conteniendo nueve cifras seguidas (teléfono para la Puerta de N1). QuPath no lo exige.
    return {"type": "Feature", "geometry": geometria(geom), "properties": props}


def _es_contexto(ctx):
    import laminillas_metricas as MET
    if not isinstance(ctx, MET.Contexto):
        raise GeoJSONError("se necesita el Contexto de laminillas_metricas (sello y T), no un T "
                           "suelto")
    return ctx


def nucleos(geoms_l0, dab, ctx, en_denominador, area_um2=None, extra=None):
    """Un «detection» por núcleo, en px L0: clase Positive/Negative según el T del contexto
    (sellado o elevado por el fondo); medidas: DAB del compartimento, área, si entra en el
    denominador, el T usado y las extra."""
    ctx = _es_contexto(ctx)
    T = float(ctx.T)
    out = []
    for i, g in enumerate(geoms_l0):
        m = {"DAB OD mean (compartment)": dab[i], "In denominator": 1.0 if en_denominador[i] else 0.0,
             "Threshold T (OD)": T}
        if area_um2 is not None:
            m["Area um^2"] = area_um2[i]
        for k, v in (extra or {}).items():
            m[k] = v[i]
        out.append(feature(g, "detection", "Positive" if dab[i] > T else "Negative", m))
    return out


def _matriz_de(matrices, fc):
    if hasattr(matrices, "matrices_verificadas"):
        return matrices.matrices_verificadas().get(fc)
    if isinstance(matrices, dict):
        return matrices.get(fc)
    return matrices                       # una sola matriz (p. ej. la identidad de P-CK19)


def regiones_a_l0(regiones, matrices, mpp_l0):
    """Regiones de la rejilla (µm de la referencia) → px L0 de la lámina, cada una con la inversa
    de la transformada de SU FC (la misma con la que se contaron sus núcleos). `matrices`:
    {fc_id: M lámina→referencia}, un `ResultadoRegistro` (sus FC verificados) o una sola M (la
    referencia). Las regiones de un FC sin matriz no se devuelven."""
    from shapely import affinity
    out = {}
    for c, r in regiones.items():
        M = _matriz_de(matrices, r["fc"])
        if M is None:
            continue
        Minv = np.linalg.inv(np.asarray(M, float))
        S = np.diag([1 / mpp_l0, 1 / mpp_l0, 1.0]) @ Minv
        a = [S[0, 0], S[0, 1], S[1, 0], S[1, 1], S[0, 2], S[1, 2]]
        out[c] = affinity.affine_transform(r["geom"], a)
    return out


def regiones(regiones_ref, geoms_l0, clopper=None):
    """Una «annotation» bloqueada por región: k, n, %, IC 95 % y distancia al borde (solo las que
    tienen geometría en px L0)."""
    out = []
    for c, r in sorted(regiones_ref.items(), key=lambda kv: kv[0]):
        if c not in geoms_l0:
            continue
        m = {"k (above T)": r["k"], "n (denominator)": r["n"],
             "Percent above T": r.get("pct", float("nan")),
             "Distance to fragment border um": r["dist_borde_um"], "Side L um": r["L"]}
        if clopper is not None and r["n"]:
            lo, hi = clopper(r["k"], r["n"])
            m["CI95 low %"], m["CI95 high %"] = 100 * lo, 100 * hi
        out.append(feature(geoms_l0[c], "annotation", "Region", m, nombre="%s:%d,%d" % c,
                           bloqueado=True))
    return out


def capa_editable(geoms_l0=()):
    """Capa «in situ / exclude»: desbloqueada; vacía fuera de la zona p63 (solo lleva lo que se
    le pase, p. ej. estructuras con capa mioepitelial)."""
    return [feature(g, "annotation", CAPA_EDITABLE, nombre=CAPA_EDITABLE, bloqueado=False)
            for g in geoms_l0]


def coleccion(features, ctx, marcador, n_capa_editable=0):
    """FeatureCollection de la lámina del contexto, con la tabla nombre opaco ↔ marcador como
    miembro extra (fuera de properties) y la cita del umbral (obligatoria). Toda detección tiene
    que haberse clasificado con el T de ESTE contexto."""
    ctx = _es_contexto(ctx)
    for i, f in enumerate(features):
        pr = f.get("properties") or {}
        if pr.get("objectType") == "detection":
            t = (pr.get("measurements") or {}).get("Threshold T (OD)")
            if t is None or abs(float(t) - round(float(ctx.T), 4)) > 1e-4:
                raise GeoJSONError("detección %d clasificada con otro T (%r) que el del contexto "
                                   "(%r)" % (i, t, ctx.T))
    meta = {"tabla_opaco_marcador": [{"lamina": ctx.lamina, "marcador": marcador}],
            "coordenadas": "pixels of level 0 of this slide",
            "capas": ["detection: nuclei", "annotation: regions",
                      "annotation: %s (editable; %d drawn)" % (CAPA_EDITABLE, n_capa_editable)],
            "nota": "not opened in QuPath",
            "umbral": _cita_segura(ctx.cita()),
            "metodos": [METODOS["capa"]]}
    return {"type": "FeatureCollection", "features": list(features), "laminillas": meta}


def _cita_segura(cita):
    """Lo que del sello viaja en el GeoJSON (que puede ir a N1): números, compartimento y un
    prefijo del sha256. Nunca la fecha (la Puerta de N1 la para) ni un prefijo con una tira de
    cifras que parezca un teléfono."""
    out = {k: cita[k] for k in ("T", "T_sellado", "banda_T", "compartimento", "rige", "regimen",
                                 "pre_congelacion") if k in cita}
    sha = str(cita.get("sello_sha256") or "")[:12]
    if sha and not re.search(r"\d{7,}", sha):
        out["sello_sha256_prefijo"] = sha
    return out


# ── validación ─────────────────────────────────────────────────────────────────────────────
def _esquema():
    with open(ESQUEMA, "rb") as f:
        crudo = f.read()
    if hashlib.sha256(crudo).hexdigest() != ESQUEMA_SHA256:
        raise GeoJSONError("el esquema GeoJSON vendorizado no casa con su sha256")
    return json.loads(crudo)


def _anillos(geom):
    t = geom.get("type")
    c = geom.get("coordinates")
    if t == "Polygon":
        return c
    if t == "MultiPolygon":
        return [r for p in c for r in p]
    return []


def valida(fc):
    """Lista de errores (vacía = válido): esquema oficial + anillos cerrados + sin rutas en
    properties + números finitos."""
    import jsonschema
    errores = []
    v = jsonschema.Draft7Validator(_esquema())
    for e in v.iter_errors(fc):
        errores.append("esquema: %s en %s" % (e.message[:120], "/".join(map(str, e.path))))
    for i, f in enumerate(fc.get("features", [])):
        for r in _anillos(f.get("geometry") or {}):
            if len(r) < 4 or r[0] != r[-1]:
                errores.append("feature %d: anillo sin cerrar o con <4 posiciones" % i)
    errores += _sin_rutas({k: v for k, v in fc.items() if k != "features"}, "$")
    for i, f in enumerate(fc.get("features", [])):
        errores += ["feature %d: %s" % (i, m) for m in _sin_rutas(
            {k: v for k, v in f.items() if k != "geometry"}, "feature")]
    try:
        json.dumps(fc, allow_nan=False)
    except ValueError:
        errores.append("NaN o infinito en el JSON")
    return errores


def _sin_rutas(obj, ruta="properties"):
    out = []
    if isinstance(obj, str):
        if RE_RUTA.search(obj):
            out.append("ruta o nombre de imagen en %s" % ruta)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out += _sin_rutas(str(k), ruta + ".<clave>") + _sin_rutas(v, "%s.%s" % (ruta, k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += _sin_rutas(v, "%s[%d]" % (ruta, i))
    return out


def escribe(ruta, fc):
    errores = valida(fc)
    if errores:
        raise GeoJSONError("GeoJSON inválido: " + "; ".join(errores[:5]))
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    os.replace(tmp, ruta)
    return ruta


# ── el patólogo dibuja; el script recalcula ────────────────────────────────────────────────
def recalcula_con_exclusiones(fc_editada, nuc, ctx, denominador, regiones=None):
    """Rehace los % excluyendo los núcleos (centroides `nuc.xy_l0`, px L0 de esta lámina) que
    caen dentro de las anotaciones de la capa «in situ / exclude» del GeoJSON editado: % global
    antes/después con los TRES denominadores, en los dos extremos de la banda de T y, si se dan
    las `regiones` (rejilla de la referencia), por región con su IC binomial."""
    import shapely
    from shapely.geometry import shape
    from shapely.ops import unary_union
    import laminillas_metricas as MET
    ctx = _es_contexto(ctx)
    if nuc.lamina != ctx.lamina:
        raise GeoJSONError("los núcleos son de otra lámina que el contexto")
    if nuc.xy_l0 is None:
        raise GeoJSONError("los núcleos necesitan sus centroides en px L0 (xy_l0)")
    excl = [shape(f["geometry"]) for f in fc_editada.get("features", [])
            if (f.get("properties") or {}).get("classification", {}).get("name") == CAPA_EDITABLE
            and (f.get("properties") or {}).get("objectType") == "annotation"]
    zona = unary_union(excl) if excl else None
    xy = np.asarray(nuc.xy_l0, float)
    fuera = np.zeros(len(xy), bool)
    if zona is not None and not zona.is_empty:
        fuera = shapely.contains_xy(zona, xy[:, 0], xy[:, 1])
    lo, hi = ctx.banda()

    def pct(m, T):
        n = int(m.sum())
        return (100.0 * np.sum((nuc.dab > T) & m) / n if n else float("nan")), n
    porden = {}
    for d in sorted(nuc.den):
        m = nuc.mascara(d)
        a, na = pct(m, ctx.T)
        b, nb = pct(m & ~fuera, ctx.T)
        porden[d] = {"pct_antes": a, "n_antes": na, "pct_despues": b, "n_despues": nb,
                     "banda_T_despues": [pct(m & ~fuera, lo)[0], pct(m & ~fuera, hi)[0]]}
    principal = porden[denominador]
    out = {"pct_antes": principal["pct_antes"], "n_antes": principal["n_antes"],
           "pct_despues": principal["pct_despues"], "n_despues": principal["n_despues"],
           "n_excluidos": int((nuc.mascara(denominador) & fuera).sum()),
           "n_anotaciones": len(excl), "por_denominador": porden, "denominador": denominador,
           "umbral": ctx.cita(), "metodos": [METODOS["recalculo"]]}
    if regiones is not None:
        sub = MET._subconjunto(nuc, ~fuera)
        rr = MET.cuenta_regiones(sub, {c: dict(r) for c, r in regiones.items()}, ctx, denominador)
        out["por_region"] = {"%s:%d,%d" % c: {"k": r["k"], "n": r["n"], "pct": r["pct"],
                                              "ic95": [100 * x for x in
                                                       MET.clopper_pearson(r["k"], r["n"])]}
                             for c, r in sorted(rr.items(), key=lambda kv: kv[0])}
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "validar":
        with open(sys.argv[2], encoding="utf-8") as f:
            errs = valida(json.load(f))
        print("\n".join(errs) if errs else "GeoJSON válido")
        sys.exit(1 if errs else 0)
    print("uso: laminillas_geojson.py validar <fichero.geojson>")
