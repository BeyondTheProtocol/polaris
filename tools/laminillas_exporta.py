#!/usr/bin/env python3
"""tools/laminillas_exporta.py — `exporta_n1` por la ventanilla (plan «laminillas DFCI», F1.4).

Lo lanza SOLO `lector_clinico.py procesa laminillas_exporta -- …`, dentro de `exporta.sb` (lee
SESION, escribe solo `~/Laminillas-N1/`, sin red). La ventanilla no deja pasar rutas: este
envoltorio resuelve los ficheros en SESION (el cwd) por el manifiesto de la ingesta y llama a
`exporta_n1`, que hace toda la revisión (formato, teselas, tags, OCR, trazos, Puerta de N1).

Órdenes:
  <OPACO> tiff                  copia N1 de la lámina entera (`exporta_n1.exporta_tiff`). Si no
                                sale por formato o teselas, imprime `diagnostico`.
  diagnostico [relleno|coherencia] <OPACO…>
                                sin escribir nada: tags de la lista blanca por IFD con sus valores
                                (el 270, solo su longitud; los de fuera, solo su número), lo que
                                dicen las comprobaciones de tags y pirámide de exporta_n1, por
                                nivel cuántas teselas no pasan la forma canónica o la decodificación
                                estricta (índice de la primera y aviso de libjpeg; nunca bytes), y
                                el RELLENO de las teselas del borde (`diagnostico_relleno`: cuántas,
                                tamaño de su JPEG y estadísticos de sus píxeles de relleno). Con
                                `relleno`, solo eso último; con `coherencia`, solo la coherencia de
                                la Puerta por nivel y cuántas ventanas pasan el umbral (las que
                                enseñaría la hoja del 1-bis).
  <OPACO> png [thumbnail|x8]    los dos PNG de revisión, o solo uno.
  verifica [OPACO…]             cada fichero N1 (de esos opacos, o todos) por `revalidar_n1`, la
                                Puerta del envío; y un PNG de revisión, si sus píxeles son los del
                                PNG vigente en SESION. No escribe nada.
  retira <OPACO> <thumbnail|x8|tiff> <motivo>
                                mueve ese fichero N1 a `_retirados/` con su `.retirada.txt` (fecha
                                y motivo) y lo saca del manifiesto, bajo el cerrojo y por la Puerta
                                de exporta_n1 (2-oct-26). `motivo`: una clave de MOTIVOS_RETIRADA.
  capas-piloto                  las capas del piloto para el panel de visión (5-bis, actualización
                                15): pinta núcleos, CK19, artefacto, registro y figura desde los
                                productos del piloto en SESION, cada una a N1 por `exporta_png`, y
                                escribe `panel_vision/capas_piloto.json` (`laminillas_capas`).

Una lámina cuyo OCR o detector de trazos saltó en la revisión local (`n1_apta` distinto de True en
el manifiesto) NO entra: eso es el paso 1-bis, de {{TITULAR}}, y este envoltorio no lo salta. Y la
revisión tiene que ser la vigente (2-oct-26): la r5 de `laminillas_ingesta.cristal`, hecha con el
MISMO exporta_n1 (sha256) que va a exportar; si exporta_n1 cambia, hay que repetirla.

HOJA DEL PASO 1-BIS (`hoja_1bis`, 2-oct-26). La lanza `procesa laminillas_qc -- hoja-1bis [OPACO…]`
(jaula analisis: lee y escribe SESION, nunca N1), no `laminillas_exporta`: exporta.sb solo escribe
en N1 y el PDF es zona clínica (sus recortes son imágenes de la lámina: se quedan en SESION). Escribe
`SESION/revision/hoja-1bis.pdf`: por cada lámina que pide el paso 1-bis (`n1_apta` = no, o un TIFF
que pregunta aunque sea solo por la coherencia), su miniatura con un recuadro rojo numerado en cada
lectura del OCR y, por sitio y nivel, el recorte ampliado ×4 de ESE nivel (sus teselas decodificadas
como las lee exporta_n1), con la confianza y el nivel; y CADA ventana de la coherencia que saltó
(recuadro azul en la miniatura y una página «Coherencia»: el recorte del nivel fino, L0 o ×4, junto
al mismo campo del ×8, a ~384 px, con su diferencia y su umbral). Con más de TOPE_VENTANAS_1BIS
ventanas: «TODAVÍA NO: demasiadas ventanas para revisarlas a mano» y la lámina no lleva orden. Al
final, qué buscar y la orden exacta de cada lámina para Terminal.app (`comando_1bis`: exporta_n1
tiff … --revisado-en-tty --hoja <huella>, que pide VISTO-N1 por `borde.revisar_cristal_en_tty`; la
huella es la de lo que enseña la hoja, y exporta_n1 se para ANTES de preguntar si lo que ve al
exportar es otra cosa). Las cajas salen del manifiesto (r5, en mm de L0), que no guarda el texto
leído: la hoja no lo puede escribir. Marca «TODAVÍA NO» la orden de una lámina si exporta_n1 se
pararía hoy antes de preguntar (la estructura del TIFF por sus propias funciones, relleno incluido,
sin decodificar una tesela; el ×4 que la r5 anotó que rehúsa; un error que la r5 anotó en la
coherencia), si los recuadros son de una revisión hecha con otro exporta_n1 (el que pregunte podría
ver sitios que la hoja no enseña) o si la r5 no guarda el sha256 del TIFF.
"""
import collections
import fcntl
import hashlib
import json
import math
import os
import re
import shlex
import sys
import textwrap
import time

_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _AQUI)
import exporta_n1 as E  # noqa: E402 — importa puerta_n1 (fija el estado) antes que borde
from puerta_n1 import PuertaCerrada  # noqa: E402

REVISION_CRISTAL = "r5"                 # = laminillas_ingesta.REVISION_CRISTAL (lo fija un test)
CARPETA_RETIRADOS = "_retirados"
MOTIVOS_RETIRADA = {
    "rayas": "PNG de revisión con rayas de relleno: exportado antes del arreglo ad73a9d "
             "(1-oct-26, píxeles mezcla de relleno y vidrio pintados con el I0 local)",
    "puerta": "la Puerta de N1 vigente (exporta_n1.revalidar_n1) ya no lo acepta",
    "desfasado": "sus píxeles ya no son los del PNG de revisión vigente en SESION",
}
_CLAVE_PNG = {"thumbnail": "miniatura", "x8": "campo_x8"}


def _ruta(base, rel):
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        raise PuertaCerrada("ruta del manifiesto no relativa a SESION")
    return os.path.join(base, rel)


def _sha256(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as fh:
        for trozo in iter(lambda: fh.read(1 << 20), b""):
            h.update(trozo)
    return h.hexdigest()


def _en_ventanilla():
    if os.environ.get("BTP_VENTANILLA") != "1":
        raise PuertaCerrada("solo corre por la ventanilla")


def _laminas(base):
    with open(os.path.join(base, "manifiesto.json"), encoding="utf-8") as fh:
        return json.load(fh).get("laminas", {})


def revision_vigente(opaco, ent):
    """Lanza salvo que la revisión local del cristal esté limpia (`n1_apta` True) y sea la vigente:
    versión REVISION_CRISTAL y hecha con este mismo exporta_n1 (su sha256)."""
    if ent.get("n1_apta") is not True:
        raise PuertaCerrada("%s no tiene la revisión local del cristal limpia (n1_apta=%s): no "
                            "entra en N1 hasta el paso 1-bis" % (opaco, ent.get("n1_apta")))
    cr = ent.get("cristal") or {}
    if (cr.get("version") != REVISION_CRISTAL
            or cr.get("exporta_n1_sha256") != _sha256(os.path.join(_AQUI, "exporta_n1.py"))):
        raise PuertaCerrada("%s: su revisión del cristal no es la vigente (versión %s; hecha con otro "
                            "exporta_n1): repite `procesa laminillas_qc -- cristal %s` antes de exportar"
                            % (opaco, cr.get("version"), opaco))


def exporta_sesion(opaco, que, base=None, nivel=None):
    """Lista de (destino, entrada) exportados. Lanza PuertaCerrada ante cualquier duda."""
    _en_ventanilla()
    E._opaco(opaco)
    base = base or os.getcwd()
    ent = _laminas(base).get(opaco)
    if not ent:
        raise PuertaCerrada("%s no está en el manifiesto de SESION" % opaco)
    revision_vigente(opaco, ent)
    if que == "tiff":
        p = _ruta(base, ent["fichero"])
        if _sha256(p) != ent["sha256"]:
            raise PuertaCerrada("%s: sha256 distinto del manifiesto" % opaco)
        return [E.exporta_tiff(p, opaco)]
    if que == "png":
        if nivel not in (None,) + tuple(_CLAVE_PNG):
            raise PuertaCerrada("nivel del PNG: thumbnail o x8")
        rev = ent.get("revision") or {}
        out = []
        for niv, clave in _CLAVE_PNG.items():
            if nivel and niv != nivel:
                continue
            if clave not in rev:
                raise PuertaCerrada("%s: falta el PNG de revisión «%s»" % (opaco, clave))
            out.append(E.exporta_png(_ruta(base, rev[clave]["fichero"]), opaco, niv,
                                     None if niv == "thumbnail" else rev[clave]["mpp"]))
        return out
    raise PuertaCerrada("qué exportar: tiff o png")


# ── Diagnóstico de teselas (solo índices, recuentos y avisos; nunca bytes) ─────────────────────
def diagnostico_teselas(ruta):
    """Por IFD: teselas, cuántas no pasan la lectura canónica o la decodificación estricta de
    exporta_n1, la primera (índice, columna, fila) y los avisos distintos con su recuento."""
    t = E.Tiff(ruta)
    try:
        out = []
        for k, ifd in enumerate(t.ifds):
            w, h = t.enteros(ifd, 256)[0], t.enteros(ifd, 257)[0]
            tw, th = t.enteros(ifd, 322)[0], t.enteros(ifd, 323)[0]
            porfila = (w + tw - 1) // tw
            avisos, primera, n = collections.Counter(), None, 0
            for i, (o, c) in enumerate(zip(t.enteros(ifd, 324), t.enteros(ifd, 325))):
                if not c:
                    continue
                n += 1
                try:
                    E.decodifica_jpeg(E.tesela_canonica(t.lee(o, c), tw, th), 8)
                except PuertaCerrada as e:
                    avisos[str(e)[:200]] += 1
                    primera = primera or (i, i % porfila, i // porfila)
            out.append({"ifd": k, "ancho": w, "alto": h, "teselas": n, "fallan": sum(avisos.values()),
                        "primera": primera, "avisos": dict(avisos)})
        return out
    finally:
        t.f.close()


MCU_RELLENO = 16         # lado del MCU de un JPEG 4:2:0 (el del Grundium): ver diagnostico_relleno
MARGEN_RELLENO = 2       # px de la interpolación del croma (libjpeg «fancy upsampling») tras el MCU


def _stats_relleno(px):
    """Solo números de un array (n, 3) de píxeles de relleno: mín, máx, media y desviación por
    canal, el valor RGB más frecuente, la fracción EXACTAMENTE igual a él y la desviación máxima
    respecto a él (cualquier canal). None si no hay píxeles."""
    import numpy as np
    if not len(px):
        return None
    vals, cuentas = np.unique(px, axis=0, return_counts=True)
    moda = vals[int(cuentas.argmax())]
    return {"px": int(len(px)), "min": px.min(axis=0).tolist(), "max": px.max(axis=0).tolist(),
            "media": [round(float(v), 2) for v in px.mean(axis=0)],
            "desv": [round(float(v), 2) for v in px.std(axis=0)],
            "moda": moda.tolist(), "frac_moda": round(float(cuentas.max()) / len(px), 6),
            "dev_max_moda": int(np.abs(px.astype(np.int16) - moda.astype(np.int16)).max()),
            "valores_distintos": int(len(vals))}


def _dims_sof0(t, o, c):
    """(ancho, alto) que declara el SOF0 de la tesela en `o` (`c` bytes), leyendo solo su cabecera
    (los segmentos hasta el SOF0); None si no lo encuentra. Solo números."""
    import struct
    b = t.lee(o, min(c, 4096))
    i = 2
    while i + 4 <= len(b) and b[i] == 0xFF:
        m = b[i + 1]
        ln = struct.unpack(">H", b[i + 2:i + 4])[0]
        if m == 0xC0 and i + 9 <= len(b):
            alto, ancho = struct.unpack(">HH", b[i + 5:i + 9])
            return ancho, alto
        if m == 0xDA:
            return None
        i += 2 + ln
    return None


def diagnostico_relleno(ruta):
    """Por IFD, solo números: el RELLENO de las teselas del borde (los píxeles decodificados más
    allá del ancho y alto declarados, w y h), que nada de lo que mira la Puerta recorta dentro.
    Cada tesela del borde se decodifica entera con libjpeg-turbo en estricto (como exporta_n1).

    Zonas del relleno de una tesela (vw×vh = lo visible en ella):
      · «banda»: x < MCU(vw) + MARGEN o y < MCU(vh) + MARGEN — el MCU que comparte con lo visible
        (su contenido lo fijan los mismos coeficientes) y el halo de croma tras él;
      · «lejos»: el resto, MCUs enteros de relleno.
    Devuelve [{ifd, ancho, alto, tesela, resto (w % tw, h % th), teselas, sof0 (cuántas teselas
    declaran en su SOF0 el tamaño exacto de la tesela y cuáles otro: solo su cabecera, de TODAS),
    teselas_borde, jpeg (tamaño del JPEG del borde frente a la tesela y a lo visible),
    relleno/banda/lejos (`_stats_relleno`), teselas con «lejos» constante, la peor tesela (máx −
    mín en «lejos»)}]. Nunca bytes ni píxeles visibles."""
    import numpy as np
    t = E.Tiff(ruta)
    try:
        out = []
        for k, ifd in enumerate(t.ifds):
            w, h = t.enteros(ifd, 256)[0], t.enteros(ifd, 257)[0]
            tw, th = t.enteros(ifd, 322)[0], t.enteros(ifd, 323)[0]
            porfila, filas = (w + tw - 1) // tw, (h + th - 1) // th
            offs, cnts = t.enteros(ifd, 324), t.enteros(ifd, 325)
            jpeg = collections.Counter()
            todas, bandas, lejos = [], [], []
            n_borde = constantes = con_lejos = 0
            peor = 0
            sof = collections.Counter()
            for i, (o, c) in enumerate(zip(offs, cnts)):
                fx, fy = i % porfila, i // porfila
                vw, vh = min(tw, w - fx * tw), min(th, h - fy * th)
                if c:                                   # el SOF0 de TODAS las teselas
                    dims = _dims_sof0(t, o, c)
                    sof["= tesela" if dims == (tw, th) else "%s" % (list(dims) if dims else "sin SOF0")] += 1
                else:
                    sof["vacía"] += 1
                if vw == tw and vh == th:
                    continue
                n_borde += 1
                if not c:
                    jpeg["vacía"] += 1
                    continue
                sw, sh, rgb = E.decodifica_jpeg(E.tesela_canonica(t.lee(o, c), tw, th), 1)
                jpeg["tesela entera" if (sw, sh) == (tw, th) else
                     "solo lo visible" if (sw, sh) == (vw, vh) else "otro tamaño"] += 1
                a = np.frombuffer(rgb, np.uint8).reshape(sh, sw, 3)
                yy, xx = np.mgrid[0:sh, 0:sw]
                rell = (xx >= vw) | (yy >= vh)
                if not rell.any():
                    continue
                bx = -(-vw // MCU_RELLENO) * MCU_RELLENO + MARGEN_RELLENO
                by = -(-vh // MCU_RELLENO) * MCU_RELLENO + MARGEN_RELLENO
                lej = (xx >= bx) | (yy >= by)
                todas.append(a[rell])
                bandas.append(a[rell & ~lej])
                if lej.any():
                    con_lejos += 1
                    pl = a[lej]
                    lejos.append(pl)
                    rango = int((pl.max(axis=0).astype(int) - pl.min(axis=0)).max())
                    constantes += rango == 0
                    peor = max(peor, rango)

            def junta(xs):
                return _stats_relleno(np.concatenate(xs) if xs else np.zeros((0, 3), np.uint8))
            out.append({"ifd": k, "ancho": w, "alto": h, "tesela": [tw, th],
                        "resto": [w % tw, h % th], "teselas": len(cnts), "sof0": dict(sof),
                        "teselas_borde": n_borde, "jpeg": dict(jpeg),
                        "relleno": junta(todas), "banda": junta(bandas), "lejos": junta(lejos),
                        "teselas_con_lejos": con_lejos, "lejos_constante": constantes,
                        "lejos_peor_rango": peor})
        return out
    finally:
        t.f.close()


def diagnostico_coherencia(ruta):
    """Por cada nivel mayor que el ×8, solo números: la coherencia de la Puerta con el ×8
    (`exporta_n1._coherencia_nivel_detalle`), cuántas ventanas pasan el umbral (lo que enseñaría la
    hoja del 1-bis, frente a TOPE_VENTANAS_1BIS) y las 5 mayores diferencias; o el error."""
    t = E.Tiff(ruta)
    try:
        k8 = E._nivel_x8(t)
        factores = E._comprueba_piramide(t)
        img8 = E._imagen_de_nivel(t, k8)
        out = []
        for k in range(k8):
            try:
                d, malas = E._coherencia_nivel_detalle(t, k, factores[k8] // factores[k], img8)
                out.append({"ifd": k, "factor": factores[k], "diferencia": d, "umbral": E.UMBRAL_COHERENCIA,
                            "ventanas_sobre_umbral": len(malas), "tope_1bis": E.TOPE_VENTANAS_1BIS,
                            "mayores": sorted((m[4] for m in malas), reverse=True)[:5]})
            except PuertaCerrada as e:
                out.append({"ifd": k, "factor": factores[k], "error": str(e)[:200]})
        return out
    finally:
        t.f.close()


def diagnostico_formato(ruta):
    """([por IFD], pirámide), solo números: cada tag de la lista blanca con su tipo, nº y valores
    (el 270, solo su longitud; 324/325, solo cuántos), el NÚMERO de los tags fuera de la lista
    (su valor puede ser texto: nunca se lee), y lo que dicen `_comprueba_tags` y
    `_comprueba_piramide` de exporta_n1."""
    import struct
    t = E.Tiff(ruta)
    try:
        out = []
        for k, ifd in enumerate(t.ifds):
            tags = {}
            for tag, (tipo, cnt, val) in sorted(ifd.items()):
                if tag not in E.TAGS_FORMATO:
                    continue
                if tag == 270:
                    tags[tag] = {"tipo": tipo, "n": cnt, "longitud": len(val)}
                elif tag in (324, 325):
                    tags[tag] = {"tipo": tipo, "n": cnt}
                else:
                    fmt = E._FMT_VAL.get(tipo)
                    tags[tag] = {"tipo": tipo, "n": cnt, "valores": list(struct.unpack(
                        t.bo + fmt * cnt, val)) if fmt and cnt <= 16 else None}
            try:
                E._comprueba_tags(t, k, ifd)
                veredicto = "pasa"
            except PuertaCerrada as e:
                veredicto = str(e)
            out.append({"ifd": k, "tags": tags, "comprueba_tags": veredicto,
                        "fuera_de_lista": sorted(x for x in ifd if x not in E.TAGS_FORMATO)})
        try:
            E._comprueba_piramide(t)
            piramide = "pasa"
        except PuertaCerrada as e:
            piramide = str(e)
        return out, piramide
    finally:
        t.f.close()


# ── Verificación de lo que hay en N1 ─────────────────────────────────────────────────────────
def _opaco_de(nombre):
    m = E.RE_NOMBRE_TIFF.match(nombre) or E.RE_NOMBRE_PNG.match(nombre)
    return m.group(1) if m else None


def _pixeles_distintos(a, b):
    """Nº de píxeles distintos entre dos imágenes RGB, o None si no tienen el mismo tamaño."""
    from PIL import ImageChops
    if a.size != b.size:
        return None
    return sum(ImageChops.difference(a.convert("RGB"), b.convert("RGB")).convert("L").histogram()[1:])


def verifica_n1(opacos=(), base=None):
    """[(nombre, veredicto, píxeles distintos de SESION o None)] de cada fichero del manifiesto N1:
    veredicto «pasa» o el motivo de `revalidar_n1` (la Puerta del envío; nunca lleva texto leído)."""
    from PIL import Image
    _en_ventanilla()
    E.puerta.exigir_diccionario()
    base = base or os.getcwd()
    laminas = _laminas(base)
    n1 = E.puerta.n1_dir()
    out = []
    for nombre, ent in sorted(E.puerta.cargar_manifiesto()["ficheros"].items()):
        op = _opaco_de(nombre)
        if opacos and op not in opacos:
            continue
        try:
            E.revalidar_n1(os.path.join(n1, nombre), ent.get("sha256"))
            veredicto = "pasa"
        except PuertaCerrada as e:
            veredicto = "🛑 " + str(e)
        dif = None
        nivel = ent.get("nivel")
        if nombre.endswith(".png") and op in laminas and nivel in _CLAVE_PNG:
            rev = (laminas[op].get("revision") or {}).get(_CLAVE_PNG[nivel])
            if rev:
                with Image.open(os.path.join(n1, nombre)) as a, \
                        Image.open(_ruta(base, rev["fichero"])) as b:
                    dif = _pixeles_distintos(a, b)
                    dif = -1 if dif is None else dif
        out.append((nombre, veredicto, dif))
    return out


# ── Retirada (por la vía de exporta_n1: su cerrojo, su manifiesto y su Puerta) ────────────────
def _nombre_n1(opaco, que, ficheros):
    if que == "tiff":
        return "%s.n1.tif" % opaco
    if que == "thumbnail":
        return "%s__thumbnail.png" % opaco
    if que == "x8":
        cand = [n for n in ficheros if n.startswith("%s__x8__mpp" % opaco) and E.RE_NOMBRE_PNG.match(n)]
        if len(cand) != 1:
            raise PuertaCerrada("%s: %d PNG ×8 en el manifiesto N1 (espero 1)" % (opaco, len(cand)))
        return cand[0]
    raise PuertaCerrada("qué retirar: thumbnail, x8 o tiff")


def retira_n1(opaco, que, motivo):
    """Mueve un fichero N1 a `_retirados/` (no borra: se deshace moviéndolo de vuelta) con su
    `.retirada.txt`, y lo saca de `ficheros` del manifiesto anotándolo en `retirados` (sha256 y
    clave del motivo), bajo el mismo cerrojo que `exporta_n1._escribe_n1` y con el manifiesto
    nuevo por `puerta.revisar_manifiesto` ANTES de mover nada. Así exporta_n1 puede volver a
    escribir ese nombre, y vision_n1/nube_n1 ya no lo ven (solo sirven la raíz de N1 con sha256 en
    el manifiesto). Devuelve la ruta del retirado."""
    _en_ventanilla()
    E._opaco(opaco)
    if motivo not in MOTIVOS_RETIRADA:
        raise PuertaCerrada("motivo de retirada: uno de %s" % ", ".join(sorted(MOTIVOS_RETIRADA)))
    E.puerta.exigir_diccionario()
    n1 = E.puerta.n1_dir()
    with open(os.path.join(n1, ".manifiesto.lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        man = E.puerta.cargar_manifiesto()
        nombre = _nombre_n1(opaco, que, man["ficheros"])
        ent = man["ficheros"].get(nombre)
        origen = os.path.join(n1, nombre)
        if not ent:
            raise PuertaCerrada("%s no está en el manifiesto N1: nada que retirar" % nombre)
        if os.path.islink(origen) or not os.path.isfile(origen) or _sha256(origen) != ent.get("sha256"):
            raise PuertaCerrada("%s: el fichero no es el del manifiesto (sha256): no retiro a ciegas"
                                % nombre)
        dret = os.path.join(n1, CARPETA_RETIRADOS)
        destino = os.path.join(dret, nombre)
        if os.path.lexists(destino) or os.path.lexists(destino + ".retirada.txt"):
            raise PuertaCerrada("ya hay un %s retirado: míralo antes de retirar otro igual" % nombre)
        del man["ficheros"][nombre]
        man.setdefault("retirados", {}).setdefault(nombre, []).append(
            {"sha256": ent["sha256"], "motivo": motivo})
        texto_man = json.dumps(man, ensure_ascii=False, indent=1, sort_keys=True)
        E.puerta.revisar_manifiesto(texto_man)
        os.makedirs(dret, mode=0o700, exist_ok=True)
        fd = os.open(destino + ".retirada.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("fecha: %s\nmotivo: %s (%s)\nsha256: %s\nvia: laminillas_exporta retira "
                     "(ventanilla, exporta.sb)\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"),
                                                     MOTIVOS_RETIRADA[motivo], motivo, ent["sha256"]))
        os.rename(origen, destino)
        tmpm = E.puerta.manifiesto_path() + ".tmp"
        with open(tmpm, "w", encoding="utf-8") as fh:
            fh.write(texto_man)
        os.replace(tmpm, E.puerta.manifiesto_path())
    return destino


def _imprime_relleno(op, ruta):
    """El relleno de las teselas del borde de `op` (`diagnostico_relleno`): una línea JSON por
    IFD, solo números."""
    for d in diagnostico_relleno(ruta):
        print("   %s IFD %d relleno: %s" % (op, d["ifd"], json.dumps(d, sort_keys=True)))


def _imprime_diagnostico(op, base, solo=None):
    """Formato (tags por IFD y pirámide), teselas y relleno de las teselas del borde de la lámina
    `op` de SESION: solo números, índices y los avisos de exporta_n1/libjpeg. `solo`: "relleno"
    o "coherencia" (`diagnostico_coherencia`), solo eso."""
    ruta = _ruta(base, _laminas(base)[op]["fichero"])
    if solo == "relleno":
        _imprime_relleno(op, ruta)
        return
    if solo == "coherencia":
        for d in diagnostico_coherencia(ruta):
            print("   %s IFD %d coherencia: %s" % (op, d["ifd"], json.dumps(d, sort_keys=True)))
        return
    ifds, piramide = diagnostico_formato(ruta)
    print("%s · pirámide: %s" % (op, piramide))
    for d in ifds:
        print("   IFD %d · tags: %s · fuera de la lista: %s · %s" % (
            d["ifd"], json.dumps(d["tags"], sort_keys=True), d["fuera_de_lista"], d["comprueba_tags"]))
    for d in diagnostico_teselas(ruta):
        print("   IFD %d (%dx%d): %d teselas, %d no pasan la forma canónica o la decodificación "
              "estricta%s%s" % (d["ifd"], d["ancho"], d["alto"], d["teselas"], d["fallan"],
                                "; primera nº %d (columna %d, fila %d)" % d["primera"]
                                if d["primera"] else "",
                                "".join("\n      · %d× %s" % (n, a) for a, n in d["avisos"].items())))
    _imprime_relleno(op, ruta)


# ── Hoja del paso 1-bis (PDF en SESION; la lanza `procesa laminillas_qc -- hoja-1bis`) ─────────
HOJA_1BIS = "revision/hoja-1bis.pdf"
AMPLIA = 4
LADO_MAX_RECORTE = 1600                 # px del recorte ya ampliado: si a ×4 no cabe, menos
PIDE_VISTO = "Escribe %s para confirmar:" % E.PALABRA_VISTO     # lo que pregunta borde en /dev/tty
_RE_FUENTE_TIFF = re.compile(r"^IFD(\d+)_x(\d+)(?:_entero)?$")
_FUENTE_PNG = {"png_miniatura": ("miniatura", "PNG miniatura"),
               "png_campo_x8": ("campo_x8", "PNG campo ×8")}
_ROJO = (230, 0, 0)
_AZUL = (0, 70, 230)
LADO_PAR = 384                          # px de cada recorte de una ventana de la coherencia en la hoja


def _en_shell(ruta):
    """La ruta como se teclea en zsh: `~/…` si cuelga de HOME (zsh expande la tilde)."""
    home = os.path.expanduser("~").rstrip("/")
    if home and ruta.startswith(home + "/"):
        return "~/" + shlex.quote(ruta[len(home) + 1:])
    return shlex.quote(ruta)


def comando_1bis(opaco, fichero, sesion, tools=None, py=None, huella=None):
    """La línea EXACTA que {{TITULAR}} teclea en Terminal.app para el paso 1-bis de `opaco`: exporta_n1
    tiff sobre su copia de SESION con --revisado-en-tty y, si el TIFF pregunta, `--hoja <huella>`
    (`exporta_n1.huella_1bis` de lo que enseña la hoja: exporta_n1 se para ANTES de preguntar si lo
    que ve al exportar no es eso). exporta_n1 revisa la lámina entera y, si salta algo,
    `borde.revisar_cristal_en_tty` pide VISTO-N1 en /dev/tty (rechaza si la lanza un proceso de
    Claude Code o si stdin no es el terminal). Intérprete del venv patologia y el exporta_n1 de ESTE
    árbol, el mismo cuyo sha256 exige la revisión r5."""
    E._opaco(opaco)
    if huella is not None and not E.RE_HUELLA.match(huella):
        raise PuertaCerrada("huella del paso 1-bis no válida")
    py = py or os.path.join(os.path.expanduser("~"), ".polaris-venvs", "patologia", "bin", "python")
    return " ".join([_en_shell(py), _en_shell(os.path.join(tools or _AQUI, "exporta_n1.py")), "tiff",
                     _en_shell(_ruta(sesion, fichero)), "--opaco", opaco, "--revisado-en-tty"]
                    + (["--hoja", huella] if huella else []))


def _nivel_de(nombre, rev, mpp0):
    """De una fuente de la revisión r5: {etiqueta, tiff=k o png=fichero, a=(X,Y L0 → px)}; None si
    no es una fuente con imagen conocida."""
    m = _RE_FUENTE_TIFF.match(nombre)
    if m:
        k, f = int(m.group(1)), int(m.group(2))
        return {"etiqueta": "×%d" % f, "tiff": k, "a": lambda X, Y, f=f: (X / float(f), Y / float(f))}
    if nombre in _FUENTE_PNG and _FUENTE_PNG[nombre][0] in rev:
        r = rev[_FUENTE_PNG[nombre][0]]
        ox, oy, paso = float(r.get("x_l0", 0)), float(r.get("y_l0", 0)), float(r["mpp"]) / mpp0
        return {"etiqueta": _FUENTE_PNG[nombre][1], "png": r["fichero"],
                "a": lambda X, Y: ((X - ox) / paso, (Y - oy) / paso)}
    return None


def _sitios(dets, mpp0):
    """Agrupa las lecturas cuyas cajas (px de L0) se tocan con 50 µm de margen: la misma palabra
    leída en varios niveles, escalas o psm es UN sitio. De arriba abajo y de izquierda a derecha."""
    pad = 50.0 / mpp0
    padre = list(range(len(dets)))

    def raiz(i):
        while padre[i] != i:
            padre[i] = padre[padre[i]]
            i = padre[i]
        return i
    for i, a in enumerate(dets):
        for j in range(i + 1, len(dets)):
            b = dets[j]["l0"]
            if (a["l0"][0] - pad <= b[2] and b[0] - pad <= a["l0"][2]
                    and a["l0"][1] - pad <= b[3] and b[1] - pad <= a["l0"][3]):
                padre[raiz(i)] = raiz(j)
    grupos = collections.OrderedDict()
    for i, d in enumerate(dets):
        grupos.setdefault(raiz(i), []).append(d)
    return sorted(grupos.values(), key=lambda g: (min(d["l0"][1] for d in g), min(d["l0"][0] for d in g)))


def _region_tiff(t, k, x0, y0, x1, y1):
    """Píxeles de [x0,x1)×[y0,y1) del nivel `k`, decodificados como `exporta_n1._imagen_de_nivel`
    (Pillow, tesela a tesela; sin tesela, blanco), sin cargar el nivel entero."""
    import io
    from PIL import Image
    ifd = t.ifds[k]
    w = t.enteros(ifd, 256)[0]
    tw, th = t.enteros(ifd, 322)[0], t.enteros(ifd, 323)[0]
    offs, cnts = t.enteros(ifd, 324), t.enteros(ifd, 325)
    porfila = (w + tw - 1) // tw
    lienzo = Image.new("RGB", (x1 - x0, y1 - y0), (255, 255, 255))
    for ty in range(y0 // th, (y1 - 1) // th + 1):
        for tx in range(x0 // tw, (x1 - 1) // tw + 1):
            i = ty * porfila + tx
            if i >= len(offs) or not cnts[i]:
                continue
            tesela = Image.open(io.BytesIO(t.lee(offs[i], cnts[i]))).convert("RGB")
            lienzo.paste(tesela, (tx * tw - x0, ty * th - y0))
    return lienzo


def _recorte(base, t, niv, caja, pngs):
    """(imagen ampliada, factor) del sitio `caja` (px del nivel) con margen; ×AMPLIA con vecino más
    cercano (los píxeles tal cual), o menos si no cabe en LADO_MAX_RECORTE."""
    from PIL import Image
    x0, y0, x1, y1 = caja
    m = max(6.0, 0.35 * max(x1 - x0, y1 - y0))
    if "tiff" in niv:
        ifd = t.ifds[niv["tiff"]]
        w, h = t.enteros(ifd, 256)[0], t.enteros(ifd, 257)[0]
    else:
        if niv["png"] not in pngs:
            with Image.open(_ruta(base, niv["png"])) as im:
                pngs[niv["png"]] = im.convert("RGB")
        w, h = pngs[niv["png"]].size
    c = (max(0, int(math.floor(x0 - m))), max(0, int(math.floor(y0 - m))),
         min(w, int(math.ceil(x1 + m))), min(h, int(math.ceil(y1 + m))))
    if c[2] <= c[0] or c[3] <= c[1]:
        return None, 0
    img = _region_tiff(t, niv["tiff"], *c) if "tiff" in niv else pngs[niv["png"]].crop(c)
    lado = max(img.size)
    if lado > LADO_MAX_RECORTE:
        f = LADO_MAX_RECORTE / float(lado)
        return img.resize((max(1, int(img.width * f)), max(1, int(img.height * f))), Image.LANCZOS), f
    f = AMPLIA if lado * AMPLIA <= LADO_MAX_RECORTE else max(1, LADO_MAX_RECORTE // lado)
    return img.resize((img.width * f, img.height * f), Image.NEAREST), f


def llega_a_preguntar(base, ent):
    """None si `exporta_n1 tiff` pasaría hoy la estructura del TIFF que mira antes que nada
    (`copia_tiff_n1`: teselado, tags, relleno, pirámide, el 297 si esta versión lo mira aparte, y
    resoluciones), con SUS funciones, sin decodificar una tesela (del SOF0 de cada una, solo su
    cabecera); y si la revisión r5 no anotó que exporta_n1 rehúse el ×4. Si no, el motivo (solo
    tags y números)."""
    t = E.Tiff(_ruta(base, ent["fichero"]))
    try:
        for k, ifd in enumerate(t.ifds):
            if any(tag not in ifd for tag in (322, 323, 324, 325)):
                return "formato del TIFF: IFD %d no teselado" % k
            E._comprueba_tags(t, k, ifd)
        if hasattr(E, "_comprueba_sin_relleno"):      # en el orden de copia_tiff_n1 (2-oct-26)
            E._comprueba_sin_relleno(t, sof0=False)
        factores = E._comprueba_piramide(t)
        if hasattr(E, "_comprueba_297"):
            E._comprueba_297(t, factores)
        E._resoluciones(t, factores)
        if hasattr(E, "_comprueba_sin_relleno"):      # el SOF0 de cada tesela: solo su cabecera
            E._comprueba_sin_relleno(t)
    except PuertaCerrada as e:
        return "formato del TIFF: %s" % e
    finally:
        t.f.close()
    # La r5 anota el ×4 «rehusado» solo por su tamaño (laminillas_ingesta._imagen_sin_tope); desde
    # 239b28a exporta_n1 lo lee por teselas (`lecturas_ocr_nivel`) y ya no lo rehúsa.
    if (ent.get("cristal") or {}).get("tiff_exporta_n1_rehusa") and not hasattr(E, "lecturas_ocr_nivel"):
        return "el ×4 pasa el tope de Pillow con que lo lee exporta_n1 (anotado en la revisión r5)"
    return None


def _etiqueta_nivel(f):
    return "L0" if f == 1 else "×%d" % f


def _ventanas_r5(cr):
    """(ventanas [[k, x0, y0, x1, y1, dif]] en px del ×8, cuántas pasan el umbral en total, errores)
    de la coherencia de la revisión r5: lo que la hoja enseña y con lo que calcula la huella."""
    coh = cr.get("coherencia") or {}
    ventanas = sorted([int(x) for x in v] for c in coh.values() for v in (c.get("ventanas") or ()))
    total = sum(int(c.get("n_ventanas") or 0) for c in coh.values())
    errores = ["%s: %s" % (n, c["error"]) for n, c in coh.items() if c.get("error")]
    if any(c.get("salta") and "n_ventanas" not in c for c in coh.values()):
        errores.append("la coherencia saltó y la revisión no guarda sus ventanas (es de antes del 2-oct-26)")
    return ventanas, total, errores


def _par_ventana(t, k, k8, den, v, n, total, fk):
    """El recorte del nivel fino `k` (a ×1 si es L0; ampliado si no) y el MISMO campo del ×8
    (ampliado al mismo lado), con la ventana `v` (px del ×8) recuadrada en rojo y una ventana de
    contexto alrededor. Píxeles decodificados como `exporta_n1._imagen_de_nivel` (Pillow)."""
    from PIL import Image, ImageDraw
    _k, x0, y0, x1, y1, dif = v
    w8, h8 = t.enteros(t.ifds[k8], 256)[0], t.enteros(t.ifds[k8], 257)[0]
    wk, hk = t.enteros(t.ifds[k], 256)[0], t.enteros(t.ifds[k], 257)[0]
    m = E.VENTANA_COHERENCIA
    c = (max(0, x0 - m), max(0, y0 - m), min(w8, x1 + m), min(h8, y1 + m))
    fino = _region_tiff(t, k, c[0] * den, c[1] * den, min(wk, c[2] * den), min(hk, c[3] * den))
    x8 = _region_tiff(t, k8, *c)
    out = []
    for img, escala in ((fino, den), (x8, 1)):
        f = max(1, LADO_PAR // max(img.size))
        img = img.resize((img.width * f, img.height * f), Image.NEAREST)
        e = escala * f
        ImageDraw.Draw(img).rectangle(((x0 - c[0]) * e, (y0 - c[1]) * e, (x1 - c[0]) * e - 1,
                                       (y1 - c[1]) * e - 1), outline=_ROJO, width=max(2, img.width // 160))
        out.append((img, f))
    (img_f, ff), (img_8, f8x) = out
    pie = ("Ventana %d de %d · %s frente al ×8 · diferencia %d (umbral %d)\nizquierda: %s %s; derecha: "
           "×8 %s · rojo: la ventana" % (n, total, _etiqueta_nivel(fk), dif, E.UMBRAL_COHERENCIA,
                                        _etiqueta_nivel(fk), "a ×1" if ff == 1 else "ampliado ×%d" % ff,
                                        "ampliado ×%d" % f8x))
    return {"n": n, "nivel": _etiqueta_nivel(fk), "dif": dif, "fino": img_f, "x8": img_8, "pie": pie}


def _ficha_1bis(base, op, ent, sha_e, tools, py):
    """Lo que la hoja enseña de una lámina: sitios del OCR con sus recortes, miniatura con
    recuadros, motivos sin caja, CADA ventana de la coherencia que saltó (el nivel fino junto al
    mismo campo del ×8, 2-oct-26), la orden con la huella de todo eso y si hoy llegaría a
    preguntar. Nunca el texto leído."""
    from PIL import Image, ImageDraw, ImageFont
    cr = ent["cristal"]
    mpp0 = float(ent["mpp"])
    rev = ent.get("revision") or {}
    dets, sin_caja = [], []
    for nombre, d in (cr.get("fuentes") or {}).items():
        niv = _nivel_de(nombre, rev, mpp0)
        etiqueta = niv["etiqueta"] if niv else nombre
        for p in d.get("palabras") or ():
            if niv is None:
                continue
            X0, Y0, X1, Y1 = (v * 1000.0 / mpp0 for v in p["caja_mm"])
            dets.append({"fuente": nombre, "niv": niv, "conf": p["confianza"], "l0": (X0, Y0, X1, Y1)})
        tz = d.get("trazos") or {}
        if tz.get("salta"):
            sin_caja.append("el detector de trazos de rotulador saltó en %s (%d px; umbral %d): no "
                            "tiene recuadro, mira la imagen entera" % (etiqueta, tz["px"], tz["umbral"]))
        if d.get("capas_puerta") and not d.get("palabras"):
            sin_caja.append("la Puerta de N1 saltó con lo leído en %s sin lectura de confianza ≥%d: "
                            "no tiene recuadro, mira la imagen entera" % (etiqueta, E.CONF_OCR))
    sitios = _sitios(dets, mpp0)
    ventanas, n_ventanas, errores_coh = _ventanas_r5(cr)
    demasiadas = n_ventanas > E.TOPE_VENTANAS_1BIS
    recortes, pares, f8 = [], [], None
    t = E.Tiff(_ruta(base, ent["fichero"]))
    pngs = {}
    try:
        for n, sitio in enumerate(sitios, 1):
            por_fuente = collections.OrderedDict()           # en el orden de la r5: ×8, menores, ×4, PNG
            for d in sitio:
                por_fuente.setdefault(d["fuente"], []).append(d)
            for nombre, ds in por_fuente.items():
                niv = ds[0]["niv"]
                esquinas = [niv["a"](d["l0"][0], d["l0"][1]) for d in ds] + \
                           [niv["a"](d["l0"][2], d["l0"][3]) for d in ds]
                caja = (min(x for x, _ in esquinas), min(y for _, y in esquinas),
                        max(x for x, _ in esquinas), max(y for _, y in esquinas))
                img, f = _recorte(base, t, niv, caja, pngs)
                if img is None:
                    continue
                confs = sorted((d["conf"] for d in ds), reverse=True)
                recortes.append({"sitio": n, "nivel": niv["etiqueta"], "confianzas": confs, "factor": f,
                                 "img": img,
                                 "pie": "Sitio %d · nivel %s\nconfianza %s · %s" % (
                                     n, niv["etiqueta"], ", ".join("%g" % c for c in confs),
                                     "ampliado ×%d" % f if f >= 1 else "reducido a %d %%" % round(100 * f))})
        if ventanas and not demasiadas:
            k8 = E._nivel_x8(t)
            factores = E._comprueba_piramide(t)
            f8 = factores[k8]
            for n, v in enumerate(ventanas, 1):
                pares.append(_par_ventana(t, v[0], k8, f8 // factores[v[0]], v, n, len(ventanas),
                                          factores[v[0]]))
    finally:
        t.f.close()
    mini = None
    if "miniatura" in rev:
        r = rev["miniatura"]
        with Image.open(_ruta(base, r["fichero"])) as im:
            mini = im.convert("RGB")
        a = _nivel_de("png_miniatura", rev, mpp0)["a"]
        dib = ImageDraw.Draw(mini)
        grosor = max(2, mini.width // 350)
        try:
            fuente = ImageFont.load_default(size=max(14, mini.width // 45))
        except TypeError:
            fuente = ImageFont.load_default()
        for n, sitio in enumerate(sitios, 1):
            for d in sitio:
                (x0, y0), (x1, y1) = a(d["l0"][0], d["l0"][1]), a(d["l0"][2], d["l0"][3])
                cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0            # mínimo 14 px: que se vea
                hx, hy = max(7.0, (x1 - x0) / 2.0), max(7.0, (y1 - y0) / 2.0)
                dib.rectangle((cx - hx, cy - hy, cx + hx, cy + hy), outline=_ROJO, width=grosor)
            xs = [a(d["l0"][2], d["l0"][1]) for d in sitio]
            dib.text((max(x for x, _ in xs) + 3 * grosor, min(y for _, y in xs)), str(n), fill=_ROJO,
                     font=fuente, stroke_width=2, stroke_fill=(255, 255, 255))
        for p in pares if f8 else ():                       # ventanas de la coherencia, en azul
            _k, x0, y0, x1, y1, _d = ventanas[p["n"] - 1]
            (u0, v0), (u1, v1) = a(x0 * f8, y0 * f8), a(x1 * f8, y1 * f8)
            cx, cy = (u0 + u1) / 2.0, (v0 + v1) / 2.0
            hx, hy = max(7.0, (u1 - u0) / 2.0), max(7.0, (v1 - v0) / 2.0)
            dib.rectangle((cx - hx, cy - hy, cx + hx, cy + hy), outline=_AZUL, width=grosor)
            dib.text((cx + hx + 3 * grosor, cy - hy), "C%d" % p["n"], fill=_AZUL, font=fuente,
                     stroke_width=2, stroke_fill=(255, 255, 255))
    falla = llega_a_preguntar(base, ent)
    vigente = cr.get("exporta_n1_sha256") == sha_e
    motivos_tiff = cr.get("motivos_tiff")
    pregunta = isinstance(motivos_tiff, list) and bool(motivos_tiff)
    # TODAVÍA NO: si se pararía antes de preguntar, si los recuadros son de otro exporta_n1 (el que
    # pregunte podría ver sitios que la hoja no enseña y VISTO-N1 los daría por vistos), si la r5
    # anotó que no puede revisar el TIFF, o si hay más ventanas de las que se revisan a mano.
    todavia_no = ([falla] if falla else []) + ([] if vigente else [
        "la revisión del cristal es de otra versión de exporta_n1; antes, `procesa laminillas_qc -- "
        "cristal %s` y otra hoja" % op])
    todavia_no += ["la revisión r5 anotó que exporta_n1 no puede revisar este TIFF (%s)" % e for e in errores_coh]
    if demasiadas:
        todavia_no.append("demasiadas ventanas para revisarlas a mano (%d; tope %d)"
                          % (n_ventanas, E.TOPE_VENTANAS_1BIS))
    huella = None
    if pregunta and not demasiadas:
        if not cr.get("tiff_sha256"):
            todavia_no.append("la revisión r5 no guarda el sha256 del TIFF (es de antes del 2-oct-26): "
                              "repite `procesa laminillas_qc -- cristal %s` y la hoja" % op)
        else:
            huella = E.huella_1bis(cr.get("exporta_n1_sha256"), cr["tiff_sha256"], motivos_tiff, ventanas)
    return {"op": op, "sitios": len(sitios), "lecturas": len(dets), "recortes": recortes, "mini": mini,
            "sin_caja": sin_caja, "no_pregunta_hoy": falla, "todavia_no": todavia_no,
            "tiff_limpio": motivos_tiff == "limpio" and not cr.get("coherencia_salta"),
            "coherencia_salta": bool(cr.get("coherencia_salta")),
            "ventanas": n_ventanas, "pares": pares, "demasiadas": demasiadas, "huella": huella,
            "vigente": vigente,
            "minutos": int(math.ceil(float(cr.get("segundos") or 0) / 60.0)),
            "comando": None if demasiadas else comando_1bis(op, ent["fichero"], base, tools, py, huella)}


def _paginas_1bis(fichas):
    """Páginas de la hoja (estructura, sin dibujar): por lámina, la primera con miniatura y hasta
    6 recortes del OCR, las siguientes con 12, y las de «Coherencia» con 6 pares (nivel fino y ×8)
    cada una; al final, las instrucciones."""
    paginas = []
    for f in fichas:
        lineas = ["Recuadros rojos: dónde el OCR creyó leer algo (%d sitio(s), %d lectura(s)). Debajo, "
                  "cada sitio ampliado en el nivel donde se leyó." % (f["sitios"], f["lecturas"])
                  if f["sitios"] else "El OCR no creyó leer nada en esta lámina."]
        if f["ventanas"] and not f["demasiadas"]:
            lineas.append("Recuadros azules (C1, C2…): las %d ventana(s) donde un nivel fino no se parece al "
                          "×8; en las páginas «Coherencia», cada una con el nivel fino a la izquierda y el "
                          "mismo campo del ×8 a la derecha." % f["ventanas"])
        elif f["demasiadas"]:
            lineas.append("La coherencia de niveles saltó en %d ventanas: demasiadas para revisarlas a mano; "
                          "esta lámina no lleva orden (TODAVÍA NO)." % f["ventanas"])
        lineas += ["Además, " + s + "." for s in f["sin_caja"]]
        if not f["vigente"]:
            lineas.append("OJO: estos recuadros son de la revisión hecha con otra versión de exporta_n1: "
                          "no teclees el comando de esta lámina con esta hoja.")
        trozos = [f["recortes"][:6]] + [f["recortes"][i:i + 12] for i in range(6, len(f["recortes"]), 12)]
        pares = [f["pares"][i:i + 6] for i in range(0, len(f["pares"]), 6)]
        de = len(trozos) + len(pares)
        for i, rs in enumerate(trozos):
            paginas.append({"tipo": "lamina", "op": f["op"], "parte": i + 1, "de": de,
                            "mini": f["mini"] if i == 0 else None, "lineas": lineas if i == 0 else [],
                            "recortes": rs})
        for j, ps in enumerate(pares):
            paginas.append({"tipo": "ventanas", "op": f["op"], "parte": len(trozos) + j + 1, "de": de,
                            "lineas": [] if j else [
                                "Coherencia: la Puerta reduce cada nivel fino al tamaño del ×8 y compara la "
                                "media de cada ventana de 16×16 px del ×8. Aquí, las ventanas que se apartan "
                                "más del umbral: busca letras, números, una firma o un trazo que esté en un "
                                "lado y no en el otro."],
                            "pares": ps})
    no_hoy = [f for f in fichas if f["todavia_no"]]
    cab = [("Paso 1-bis: qué mirar y qué teclear", "titulo")]
    if no_hoy:
        cab.append(("Hoy %d de %d comandos llevan «TODAVÍA NO»: no los teclees. No es por ti: es el programa "
                    "(abajo, el motivo de cada uno)." % (len(no_hoy), len(fichas)), "aviso"))
    # Bloques: una página nunca parte uno (el nombre de una lámina, sus avisos y su orden, juntos).
    bloques = [cab, [
        ("QUÉ MIRAR (páginas anteriores: una lámina, su miniatura, sus recortes y su coherencia)", "seccion"),
        ("1. En la miniatura, cada recuadro rojo numerado es un sitio donde el OCR creyó leer algo. Debajo va "
         "cada sitio ampliado en el nivel donde se leyó, con la confianza del OCR (60-100) y el nivel.", "texto"),
        ("2. Busca letras, números, una firma o un trazo de rotulador. Si en el recuadro solo ves tejido "
         "(núcleos, fibras, bordes, manchas de tinción), es textura.", "texto"),
        ("3. En las páginas «Coherencia», cada par es una ventana (recuadro rojo) donde el nivel fino no se "
         "parece al ×8. Si en un lado hay letras, números o un trazo que en el otro no están, esa lámina NO. "
         "Si solo cambian el color, el brillo o el foco, es la pirámide.", "texto"),
        ("4. Si en una lámina ves algo escrito, aunque sea un trozo, o dudas: esa lámina NO. No teclees su "
         "comando y apunta su nombre.", "texto")], [
        ("QUÉ TECLEAR (solo en las láminas en las que todo es textura)", "seccion"),
        ("5. Abre Terminal.app en el mini. No uses el panel de terminal de Claude: desde ahí se rechaza.", "texto"),
        ("6. Teclea o pega el comando de la lámina (es UNA línea) y pulsa Intro. Antes de preguntar revisa la "
         "lámina entera otra vez: tarda minutos (al lado, lo que tardó la revisión anterior). Si ve algo que "
         "esta hoja no te enseñó, se para sin preguntar.", "texto"),
        ("7. Cuando salga «%s», teclea %s y pulsa Intro. Cualquier otra cosa cancela y no sella nada."
         % (PIDE_VISTO, E.PALABRA_VISTO), "texto"),
        ("8. Si termina con un error sin preguntarte, no se ha sellado nada: deja esa lámina y díselo a Claude.",
         "texto")]]
    for i, f in enumerate(fichas, 1):
        b = [("COMANDOS, UNO POR LÁMINA, EN EL ORDEN DE ESTA HOJA", "seccion")] if i == 1 else []
        b.append(("%d. %s · %d sitio(s) · %d ventana(s) de coherencia · revisión anterior: unos %d min"
                  % (i, f["op"], f["sitios"], f["ventanas"], max(1, f["minutos"])), "lamina"))
        for motivo in f["todavia_no"]:
            if f["demasiadas"] and motivo.startswith("demasiadas ventanas"):
                b.append(("TODAVÍA NO: demasiadas ventanas para revisarlas a mano (%d; tope %d). Esta lámina "
                          "no lleva orden." % (f["ventanas"], E.TOPE_VENTANAS_1BIS), "aviso"))
            elif motivo == f["no_pregunta_hoy"]:
                b.append(("TODAVÍA NO: hoy se pararía antes de preguntarte (%s)." % motivo, "aviso"))
            else:
                b.append(("TODAVÍA NO: %s." % motivo, "aviso"))
        if f["tiff_limpio"] and not f["todavia_no"]:
            b.append(("No te preguntará: lo que saltó fue en los PNG de revisión, no en el TIFF, y el TIFF "
                      "saldría sin tu VISTO-N1.", "aviso"))
        if f["ventanas"] and not f["todavia_no"]:
            b.append(("La terminal te listará también «no se parece al ×8»: son las %d ventana(s) de las "
                      "páginas «Coherencia» de esta lámina." % f["ventanas"], "texto"))
        if f["comando"]:
            b.append((f["comando"], "comando"))
        bloques.append(b)
    paginas.append({"tipo": "instrucciones", "bloques": bloques})
    return paginas


_ESTILO = {"titulo": (16, "bold", "sans", 0.045), "seccion": (11, "bold", "sans", 0.034),
           "texto": (10, "normal", "sans", 0.024), "lamina": (10.5, "bold", "sans", 0.026),
           "aviso": (10, "bold", "sans", 0.024), "comando": (8.5, "normal", "mono", 0.032)}


def _escribe_pdf(paginas, ruta):
    """Dibuja las páginas (matplotlib, texto como texto: los comandos se pueden copiar) y devuelve
    las páginas que tiene el fichero escrito, contadas en él."""
    import matplotlib
    matplotlib.use("Agg")
    import numpy as np
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.figure import Figure
    tmp = ruta + ".tmp"
    with matplotlib.rc_context({"pdf.fonttype": 42, "font.family": "DejaVu Sans"}):
        with PdfPages(tmp, metadata={"Title": "Paso 1-bis", "Creator": "laminillas_exporta"}) as pdf:
            for p in paginas:
                if p["tipo"] in ("lamina", "ventanas"):
                    fig = Figure(figsize=(8.27, 11.69))
                    fig.text(0.05, 0.975, "%s · paso 1-bis%s%s" % (
                        p["op"], " · Coherencia" if p["tipo"] == "ventanas" else "",
                        "" if p["de"] == 1 else " · %d/%d" % (p["parte"], p["de"])),
                        fontsize=14, fontweight="bold", va="top")
                    y = 0.948
                    for ln in p["lineas"]:
                        for trozo in textwrap.wrap(ln, 118):
                            fig.text(0.05, y, trozo, fontsize=8.5, va="top")
                            y -= 0.016
                    if p["tipo"] == "ventanas":
                        for i, r in enumerate(p["pares"]):
                            fila, col = divmod(i, 2)
                            top = y - 0.01 - fila * 0.295
                            x = 0.05 + col * 0.47
                            for j, img in enumerate((r["fino"], r["x8"])):
                                ax = fig.add_axes([x + j * 0.215, top - 0.205, 0.205, 0.205])
                                ax.imshow(np.asarray(img), interpolation="none")
                                ax.set_xticks([])
                                ax.set_yticks([])
                            fig.text(x, top - 0.21, r["pie"], fontsize=7, va="top")
                        pdf.savefig(fig)
                        continue
                    if p["mini"] is not None:
                        ax = fig.add_axes([0.05, y - 0.375, 0.90, 0.365])
                        ax.imshow(np.asarray(p["mini"]), interpolation="none")
                        ax.set_axis_off()
                        y -= 0.39
                    for i, r in enumerate(p["recortes"]):
                        fila, col = divmod(i, 3)
                        top = y - fila * 0.215
                        ax = fig.add_axes([0.05 + col * 0.31, top - 0.17, 0.28, 0.165])
                        ax.imshow(np.asarray(r["img"]), interpolation="none")
                        ax.set_xticks([])
                        ax.set_yticks([])
                        fig.text(0.05 + col * 0.31, top - 0.175, r["pie"], fontsize=7.5, va="top")
                else:
                    fig, y = None, 0
                    ancho = max([len(t) for b in p["bloques"] for t, e in b if e == "comando"] + [1])
                    mono = min(11.0, 0.92 * 17 * 72 / (0.61 * ancho))
                    for bloque in p["bloques"]:
                        lineas = [(trozo, estilo) for texto, estilo in bloque for trozo in
                                  ([texto] if estilo == "comando" else textwrap.wrap(texto, 190))]
                        if fig is None or y - sum(_ESTILO[e][3] for _t, e in lineas) < 0.03:
                            if fig is not None:
                                pdf.savefig(fig)
                                lineas = [("Paso 1-bis (sigue)", "seccion")] + lineas
                            fig, y = Figure(figsize=(17, 11)), 0.96
                        for trozo, estilo in lineas:
                            tam, peso, fam, salto = _ESTILO[estilo]
                            fig.text(0.04, y, trozo, va="top", fontweight=peso,
                                     fontsize=mono if estilo == "comando" else tam,
                                     family="DejaVu Sans Mono" if fam == "mono" else "DejaVu Sans",
                                     color="#b00000" if estilo == "aviso" else "black")
                            y -= salto
                pdf.savefig(fig)
    os.chmod(tmp, 0o600)
    os.replace(tmp, ruta)
    with open(ruta, "rb") as fh:
        return len(re.findall(rb"/Type\s*/Page(?![A-Za-z])", fh.read()))


def _pide_1bis(ent):
    """¿Necesita esta lámina el paso 1-bis? Si su revisión r5 dejó `n1_apta` = no (texto o trazos)
    o si el TIFF pregunta (`motivos_tiff`, también solo por la coherencia: n1_apta no la cuenta)."""
    mt = (ent.get("cristal") or {}).get("motivos_tiff")
    return ent.get("n1_apta") is False or (isinstance(mt, list) and bool(mt))


def hoja_1bis(base, ops, tools=None, py=None):
    """Escribe SESION/HOJA_1BIS con las láminas de `ops` (en ese orden) que necesitan el paso 1-bis
    según su revisión r5 (`_pide_1bis`). Devuelve {pdf, paginas (contadas en el fichero), laminas,
    omitidas}; sin ninguna lámina, no escribe nada (pdf None)."""
    laminas = _laminas(base)
    sha_e = _sha256(os.path.join(_AQUI, "exporta_n1.py"))
    fichas, omitidas = [], []
    for op in ops:
        ent = laminas.get(op)
        if not ent:
            omitidas.append((op, "no está en el manifiesto"))
        elif not _pide_1bis(ent):
            omitidas.append((op, "n1_apta %s" % ent.get("n1_apta")))
        elif (ent.get("cristal") or {}).get("version") != REVISION_CRISTAL:
            omitidas.append((op, "revisión del cristal %s, no %s" % (
                (ent.get("cristal") or {}).get("version"), REVISION_CRISTAL)))
        else:
            fichas.append(_ficha_1bis(base, op, ent, sha_e, tools, py))
    if not fichas:
        return {"pdf": None, "paginas": 0, "laminas": [], "omitidas": omitidas}
    ruta = _ruta(base, HOJA_1BIS)
    os.makedirs(os.path.dirname(ruta), mode=0o700, exist_ok=True)
    n = _escribe_pdf(_paginas_1bis(fichas), ruta)
    return {"pdf": ruta, "paginas": n, "laminas": fichas, "omitidas": omitidas}


def imprime_hoja_1bis(base, ops):
    """`procesa laminillas_qc -- hoja-1bis [OPACO…]`: la hoja y, por lámina, solo números, si
    exporta_n1 llegaría hoy a preguntar y la orden que lleva el PDF."""
    r = hoja_1bis(base, ops)
    for op, motivo in r["omitidas"]:
        print("   %-10s fuera de la hoja: %s" % (op, motivo))
    if not r["pdf"]:
        print("hoja-1bis: ninguna lámina pide el paso 1-bis en la revisión %s; no escribo PDF" % REVISION_CRISTAL)
        return 0
    for f in r["laminas"]:
        print("%-10s sitios %d · lecturas %d · recortes %d · ventanas %d%s · revisión vigente %s · "
              "¿pregunta VISTO-N1 hoy? %s"
              % (f["op"], f["sitios"], f["lecturas"], len(f["recortes"]), f["ventanas"],
                 " (demasiadas: sin orden)" if f["demasiadas"] else "", "sí" if f["vigente"] else "NO",
                 "no: " + f["no_pregunta_hoy"] if f["no_pregunta_hoy"] else
                 ("no: TIFF limpio" if f["tiff_limpio"] else "sí"))
              + (" · TODAVÍA NO en la hoja" if f["todavia_no"] else ""))
    print("→ SESION/%s · %d páginas (contadas en el fichero escrito)" % (HOJA_1BIS, r["paginas"]))
    for f in r["laminas"]:
        print("   %s: %s" % (f["op"], f["comando"] or "sin orden (TODAVÍA NO)"))
    return 0


def main(argv):
    uso = ("uso: procesa laminillas_exporta -- <OPACO> tiff | <OPACO> png [thumbnail|x8] | "
           "verifica [OPACO…] | diagnostico [relleno|coherencia] <OPACO…> | retira <OPACO> <thumbnail|x8|tiff> <%s> | "
           "capas-piloto" % "|".join(sorted(MOTIVOS_RETIRADA)))
    if argv[:1] == ["hoja-1bis"]:
        print("🛑 la hoja del paso 1-bis va por `procesa laminillas_qc -- hoja-1bis [OPACO…]`: es un PDF "
              "de zona clínica en SESION, y exporta.sb solo escribe en N1", file=sys.stderr)
        return 2
    try:
        E.puerta.exigir_diccionario()
        if argv[:1] == ["verifica"]:
            mal = 0
            for nombre, veredicto, dif in verifica_n1(argv[1:]):
                mal += veredicto != "pasa" or bool(dif)
                print("%s %s · %s" % ("✅" if veredicto == "pasa" else "🛑", nombre, veredicto)
                      + ("" if dif is None else " · píxeles = SESION" if dif == 0 else
                         " · tamaño distinto del PNG de SESION" if dif < 0 else
                         " · %d píxeles distintos del PNG de SESION" % dif))
            return 4 if mal else 0
        if argv[:1] == ["diagnostico"] and len(argv) >= 2:
            solo = argv[1] if argv[1] in ("relleno", "coherencia") else None
            ops = argv[2:] if solo else argv[1:]
            if not ops:
                print(uso, file=sys.stderr)
                return 2
            for op in ops:
                E._opaco(op)
                _imprime_diagnostico(op, os.getcwd(), solo)
            return 0
        if argv[:1] == ["retira"]:
            if len(argv) != 4:
                print(uso, file=sys.stderr)
                return 2
            destino = retira_n1(argv[1], argv[2], argv[3])
            print("↩ N1: %s → %s/ (motivo %s)" % (os.path.basename(destino), CARPETA_RETIRADOS, argv[3]))
            return 0
        if argv[:1] == ["capas-piloto"]:
            if len(argv) != 1:
                print(uso, file=sys.stderr)
                return 2
            _en_ventanilla()
            import laminillas_capas
            return laminillas_capas.main_ventanilla()
        if len(argv) not in (2, 3) or argv[1] not in ("tiff", "png") or (len(argv) == 3 and argv[1] != "png"):
            print(uso, file=sys.stderr)
            return 2
        t0 = time.time()
        try:
            hechos = exporta_sesion(argv[0], argv[1], nivel=argv[2] if len(argv) == 3 else None)
        except PuertaCerrada as e:
            print("🛑 %s · %.0f s" % (e, time.time() - t0))
            if argv[1] == "tiff" and not str(e).startswith(("texto en el cristal", argv[0])):
                _imprime_diagnostico(argv[0], os.getcwd())      # formato o teselas: dónde
            return 3
        for destino, ent in hechos:
            print("✅ N1: %s (sha256 %s…, %.1f MB)" % (os.path.basename(destino), ent["sha256"][:12],
                                                     ent["bytes"] / 1e6))
        print("   %.0f s" % (time.time() - t0))
    except PuertaCerrada as e:
        print("🛑 %s" % e)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
