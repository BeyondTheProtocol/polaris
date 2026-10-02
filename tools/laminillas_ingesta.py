#!/usr/bin/env python3
"""tools/laminillas_ingesta.py — F1.1 Ingesta, F1.2 Manifiesto y F1.3 Identidad (eslabón 1 y
H&E) de las laminillas DFCI. Lo lanza SOLO la ventanilla (`lector_clinico.py procesa …`).

LOS NOMBRES DEL ZIP LLEVAN NOMBRE Y ACCESIÓN DE LA PACIENTE. Este código los mapea a opacos sin
imprimirlos NUNCA: la salida solo lleva opacos, recuentos, veredictos y esqueletos (`esqueleto()`:
cada palabra fuera de la lista de marcadores sale como su forma, «<Aa6>», y cada número largo como
«<D7>»). El nombre original vive solo en la hoja de claves de ORIGEN (0600).

Jaulas y pasos (cada paso sella su «hecho» y se salta si ya está y su producto casa):
  procesa laminillas_ingesta -- <paso>   (jaula analisis-ingesta: lee y escribe SESION y ORIGEN)
    inventario   esqueletos del zip de Descargas, sin copiar nada
    copia        zip de Descargas → ORIGEN; sha256 de Descargas (dos lecturas) y de la copia
    extrae       de la COPIA: solo «2. Pyramid Tiff/» → SESION/<opaco>.tif, nombre opaco desde el
                 primer byte; el pptx → ORIGEN; hoja de claves (0600) y manifiesto inicial
    identidad    H&E: accesión del nombre de fichero contra los informes AP copiados a
                 ORIGEN/informes/ con `lector_clinico.py --a`; identidad por `identidad_paciente`
    atribucion   nivel de atribución y tanda en el manifiesto (tras eslabón 1 e identidad)
  procesa laminillas_qc -- <paso> [OPACO…]  (jaula analisis: solo SESION; venv patologia)
    lamina OP…   metadatos, zona escaneada, I0 local, tejido y máscaras, prueba del lector (a)(b),
                 recortes ×8 para (c), PNG de revisión (miniatura y campo ×8)
    cristal OP…  r5: la decisión de la Puerta de N1 (exporta_n1) sobre el TIFF —×8 y menores con
                 OCR + Puerta + trazos (rojo incluido), ×4 entero con OCR + Puerta, coherencia de
                 L0 y ×4 con el ×8— y sobre los dos PNG; con dónde cae cada palabra. Lee las
                 teselas como la Puerta (exporta_n1.Tiff), no por OpenSlide: es su réplica.
                 n1_apta = sin texto ni trazos; la coherencia va aparte (la exige el TIFF)
    coherencia OP…  diagnóstico de la coherencia de niveles (solo números, no toca n1_apta)
    hoja-1bis [OP…]  PDF del paso 1-bis en SESION/revision/ (`laminillas_exporta.hoja_1bis`): aquí
                 y no en exporta.sb, que solo escribe en N1
    rojo OP…     diagnóstico: píxeles en la ventana de tinta roja (solo números, no escribe nada)
    eslabon1     F1.3 eslabón 1: IHQ↔IHQ por máscaras a 32 µm/px (rotación, espejo, FFT); además
                 B-HE-2↔B-HE-1 y, informativo, P-HE↔IHQ
  procesa laminillas_registro -- prueba-vips [OPACO…]  (venv valis)
    prueba (c): pyvips frente a OpenSlide en ×8 sobre los recortes que dejó `lamina`
Toda lectura de PÍXELES de una lámina va por `laminillas_lector` (OpenSlide), salvo la revisión
del cristal (r5), que replica a la Puerta y lee como ella. Los bytes comprimidos de las teselas
(para reconocer el relleno, que es byte a byte idéntico) y los tags no son píxeles; tifffile solo
entra como lector de referencia de la prueba (b).
"""
import collections
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
import zipfile

_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _AQUI)
import laminillas_comun as C  # noqa: E402

HOME = os.path.expanduser("~")
ZIP_DESCARGAS = os.path.join(HOME, "Downloads", "Microscopy scans.zip")
ORIGEN = os.path.join(HOME, "Clinico-PRIVADO", "laminillas-DFCI", "origen")
ZIP_ORIGEN = os.path.join(ORIGEN, "Microscopy scans.zip")
ZIP_SELLO = os.path.join(ORIGEN, "zip-sha256.json")
HOJA_CLAVES = os.path.join(ORIGEN, "hoja-de-claves.json")
PPTX_ORIGEN = os.path.join(ORIGEN, "pptx-origen.pptx")
INFORMES = os.path.join(ORIGEN, "informes")
IDENTIDAD_HE = os.path.join(ORIGEN, "identidad-he.json")
CARPETA_PIRAMIDES = "2. Pyramid Tiff/"

OPACOS = ("P-HE", "P-RE", "P-RP", "P-RA", "P-HER2", "P-HER2NEG", "P-KI67", "P-CK19", "P-AE1AE3",
          "P-P63", "P-SYN", "P-CHGA", "P-{{DIANA3}}", "B-HE-1", "B-HE-2")
IHQ = tuple(o for o in OPACOS if o.startswith("P-") and o != "P-HE")
TINCION = {
    "P-HE": "H&E", "P-RE": "RE (receptor de estrógenos)", "P-RP": "RP (receptor de progesterona)",
    "P-RA": "RA (receptor de andrógenos)", "P-HER2": "HER2 (rotulado «MAMA»)",
    "P-HER2NEG": "HER2 (rotulado «NEG»)", "P-KI67": "Ki67", "P-CK19": "CK19",
    "P-AE1AE3": "AE1/AE3", "P-P63": "p63", "P-SYN": "sinaptofisina", "P-CHGA": "cromogranina",
    "P-{{DIANA3}}": "{{DIANA3}}", "B-HE-1": "H&E", "B-HE-2": "H&E"}
GRUPO = {o: ("hueso" if o.startswith("B-") else "primario") for o in OPACOS}

# Sellos de método (se escriben en el manifiesto con cada resultado).
SEMILLA = 20261001                  # prueba del lector: 200 teselas L0 al azar
N_PRUEBA = 200
MPP_HUELLA = 32.0                   # µm/px del eslabón 1 (plan)
UMBRALES_OD = (0.02, 0.05, 0.08, 0.15)   # media de ODsum sobre I0 local (sensibilidad)
TEJIDO_BAJO = 0.02                  # suelo del umbral bajo de la histéresis
TEJIDO_ALTO = 0.08                  # semilla de la histéresis
TEXTURA_MIN = 0.01                  # suelo del umbral de textura (desv. típica de ODsum a 2 µm)
TEXTURA_DEFECTO = 0.02
FRAG_MIN_MM2 = 0.2                  # fragmentos > 0,2 mm² (como «Hechos medidos»)
I0_FWHM_UM = 1000.0                 # suavizado del I0 local: gaussiana de FWHM ~1 mm
FRANJA_MAX = 3.0                    # niveles de gris: por encima, «patrón por franja» → campo plano
# Eslabón 1 (plan F1.3), fijado tras el reconocimiento y declarado.
E1_IOU = 0.45
E1_MARGEN = 0.10
E1_MARGEN_ESTRECHO = 0.15
E1_CIERRE = 5.0
E1_PASO_GRUESO = 2.0
E1_PASO_FINO = 0.25


def _imprime(msg):
    print(msg, flush=True)


# ── esqueleto: lo único que se imprime de un nombre ─────────────────────────────────────────
_LISTA_LETRAS = {
    "HE", "H", "E", "RE", "RP", "RA", "ER", "PR", "AR", "HER", "NEG", "MAMA", "KI", "CK", "AE",
    "P", "INSM", "SYN", "SINAPTOFISINA", "SINAPTOF", "SYNAPTOPHYSIN", "CHGA", "CROMOGRANINA",
    "CHROMOGRANIN", "CROMO", "CROMOG", "BONE", "PYRAMID", "TIFF", "TIF", "RAW", "MICROSCOPY",
    "SCANS", "PPTX", "SVS", "ROI", "A", "B", "REC", "ANDRO", "IHC", "IHQ", "STAIN", "SLIDE",
    "SCAN", "IMAGES", "DATA", "MACOSX", "DS", "STORE", "JPG", "PNG", "OME", "NDPI", "SYNAP", "CGA",
    "{{DIANA3}}", "P63", "KI67", "CK19", "HER2", "AE1", "AE3", "SINAP", "RECEPTOR", "ESTROGEN",
    "PROGESTERONE", "ANDROGEN", "CHROMO", "SYNAPTO", "PDF", "ZIP", "NEGATIVE", "NEGATIVO",
    "POSITIVE", "CONTROL", "AE1AE3",
}
_RE_TOK = re.compile(r"[A-Za-z]+|\d+|[^A-Za-z\d]+")


def _forma_letras(t):
    if t.isupper():
        return "<A%d>" % len(t)
    if t.islower():
        return "<a%d>" % len(t)
    if t[0].isupper() and t[1:].islower():
        return "<Aa%d>" % len(t)
    return "<x%d>" % len(t)


def esqueleto(nombre):
    """El nombre sin nada identificable: letras fuera de la lista → su forma; números de >2
    cifras → <Dn>; de ≤2 cifras, solo pegados a una palabra de la lista (Ki67, CK19, A2-1);
    letras no ASCII → <u>."""
    toks = _RE_TOK.findall(nombre)
    out = []
    for i, t in enumerate(toks):
        if t.isalpha() and t.isascii():
            out.append(t if t.upper() in _LISTA_LETRAS else _forma_letras(t))
        elif t.isdigit():
            prev = toks[i - 1] if i else ""
            pegado = prev.isalpha() and prev.isascii() and prev.upper() in _LISTA_LETRAS
            tras_guion = (i >= 3 and prev == "-" and toks[i - 2].isdigit()
                          and len(toks[i - 2]) <= 2 and toks[i - 3].upper() in _LISTA_LETRAS)
            out.append(t if len(t) <= 2 and (pegado or tras_guion) else "<D%d>" % len(t))
        else:
            out.append("".join(c if c.isascii() else "<u>" for c in t))
    return "".join(out)


# ── clasificación: nombre del zip → opaco ───────────────────────────────────────────────────
_MARCADOR = {"RE": "P-RE", "RP": "P-RP", "REC": "P-RA", "CK19": "P-CK19", "KI67": "P-KI67",
             "{{DIANA3}}": "P-{{DIANA3}}", "SINAPTOF": "P-SYN", "P63": "P-P63", "AE1AE3": "P-AE1AE3"}
RE_ACCESION = re.compile(r"(?<![A-Z0-9])(\d{2})B(\d{5,9})(?![0-9])")


def clasifica(ruta_zip):
    """Opaco de una entrada del zip, o None si no es una pirámide. Lanza ValueError (con el
    esqueleto, nunca el nombre) si es una pirámide que no sé clasificar."""
    if not ruta_zip.startswith(CARPETA_PIRAMIDES) or ruta_zip.endswith("/"):
        return None
    partes = ruta_zip[len(CARPETA_PIRAMIDES):].split("/")
    if len(partes) != 2 or not partes[1].lower().endswith((".tif", ".tiff")):
        if partes[-1] and not partes[-1].startswith("."):
            raise ValueError("entrada inesperada en pirámides: %s" % esqueleto(ruta_zip))
        return None
    carpeta, base = partes
    stem = base.rsplit(".", 1)[0]
    up = stem.upper()
    hueso = re.search(r"BONE[_ ]?(\d)", up)
    if hueso:
        n = hueso.group(1)
        if n not in ("1", "2"):
            raise ValueError("hueso con número inesperado: %s" % esqueleto(ruta_zip))
        return "B-HE-%s" % n
    if "H&E" in carpeta.upper() or "H&E" in up:
        return "P-HE"
    m = re.match(r"[A-Z0-9]+", up)
    primero = m.group(0) if m else ""
    if primero == "HER2":
        antes_parentesis = up.split("(")[0]
        return "P-HER2NEG" if re.search(r"\bNEG\b", antes_parentesis) else "P-HER2"
    if primero in _MARCADOR:
        return _MARCADOR[primero]
    if primero.startswith(("CROM", "CHROM", "CHG", "CGA")):
        return "P-CHGA"
    raise ValueError("pirámide sin marcador reconocible: %s" % esqueleto(ruta_zip))


# ── señales de PII en el nombre (solo booleanos y distancias; nunca el texto) ────────────────
def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _lev(a, b):
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _palabras(texto):
    """Palabras de letras, partiendo también las pegadas en CamelCase («NombreApellido»)."""
    out = []
    for t in re.findall(r"[^\W\d_]+", texto):
        partes = re.findall(r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+|[a-záéíóúñ]+|[A-ZÁÉÍÓÚÑ]+", t) or [t]
        out.extend(partes)
        if len(partes) > 1:
            out.append(t)
    return [_norm(p) for p in out]


def senales_pii(nombre, titular=None):
    """{'nombre': 'exacto'|'casi'|'no', 'distancia': d, 'accesion': bool, 'cifras_accesion': n,
    'iniciales': bool}. Casi = ≥5 letras a distancia ≤2 o 4 letras a ≤1 (umbral de la Puerta)."""
    if titular is None:
        import identidad_paciente
        titular = identidad_paciente.titular()
    piezas = []
    if titular:
        piezas = [_norm(titular.get("nombre", ""))]
        for ap in titular.get("apellidos") or []:
            piezas += [_norm(p) for p in str(ap).split()]
    piezas = [p for p in piezas if len(p) >= 4]
    # Por cada pieza del nombre (nombre, cada apellido), su mejor distancia a una palabra del
    # fichero. «exacto» si alguna pieza está tal cual; errata si alguna pieza solo aparece a
    # distancia 1-2 (una pieza bien escrita no tapa la errata de otra).
    por_pieza = {}
    palabras = [w for w in _palabras(nombre) if len(w) >= 4]
    for p in piezas:
        for w in palabras:
            d = _lev(w, p)
            tope = 2 if min(len(w), len(p)) >= 5 else 1
            if d <= tope and (por_pieza.get(p) is None or d < por_pieza[p]):
                por_pieza[p] = d
    mejor = min(por_pieza.values()) if por_pieza else None
    errata = max((d for d in por_pieza.values() if d), default=None)
    up = nombre.upper()
    acc = RE_ACCESION.search(up)
    iniciales = False
    if titular and titular.get("nombre") and titular.get("apellidos"):
        ini = (_norm(titular["nombre"])[:1] + _norm(str(titular["apellidos"][0]))[:1]).upper()
        iniciales = bool(re.search(r"(?<![A-Z])%s(?![A-Z])" % re.escape(ini), up)) if ini else False
    return {"nombre": "no" if mejor is None else ("exacto" if mejor == 0 else "casi"),
            "distancia": mejor, "piezas_encontradas": len(por_pieza), "piezas": len(piezas),
            "errata_distancia": errata, "accesion": bool(acc),
            "cifras_accesion": len(acc.group(2)) if acc else None, "iniciales": iniciales}


def accesion_de(nombre):
    """(prefijo, cifras) de la accesión del nombre, o None. Solo para comparar en la jaula."""
    m = RE_ACCESION.search(nombre.upper())
    return (m.group(1), m.group(2)) if m else None


# ── copia ───────────────────────────────────────────────────────────────────────────────────
def _sha256(ruta):
    return C.sha256_fichero(ruta)


def copia():
    os.makedirs(ORIGEN, mode=0o700, exist_ok=True)
    os.makedirs(INFORMES, mode=0o700, exist_ok=True)
    if os.path.isfile(ZIP_ORIGEN) and os.path.isfile(ZIP_SELLO):
        with open(ZIP_SELLO, encoding="utf-8") as f:
            sello = json.load(f)
        sha = _sha256(ZIP_ORIGEN)
        if sha == sello.get("sha256"):
            _imprime("copia: ya estaba; sha256 de la copia en ORIGEN %s… = sellado" % sha[:16])
            return 0
        _imprime("ABORTO: la copia de ORIGEN no casa con su sello; no la toco")
        return 1
    if not os.path.isfile(ZIP_DESCARGAS):
        _imprime("ABORTO: no está el zip en Descargas")
        return 1
    st = os.stat(ZIP_DESCARGAS)
    tmp = ZIP_ORIGEN + ".tmp"
    h = hashlib.sha256()
    n = 0
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(ZIP_DESCARGAS, "rb") as src, os.fdopen(fd, "wb") as dst:
        for trozo in iter(lambda: src.read(8 << 20), b""):
            h.update(trozo)
            dst.write(trozo)
            n += len(trozo)
        dst.flush()
        os.fsync(dst.fileno())
    sha_lectura1 = h.hexdigest()
    sha_copia = _sha256(tmp)
    sha_lectura2 = _sha256(ZIP_DESCARGAS)
    if not (sha_lectura1 == sha_copia == sha_lectura2) or n != st.st_size:
        os.unlink(tmp)
        _imprime("ABORTO: sha256 distinto entre Descargas y la copia (no se deja nada)")
        return 1
    os.replace(tmp, ZIP_ORIGEN)
    os.utime(ZIP_ORIGEN, (st.st_atime, st.st_mtime))
    C.escribe_json(ZIP_SELLO, {"sha256": sha_copia, "bytes": n, "copiado": time.strftime(
        "%Y-%m-%dT%H:%M:%S"), "comprobacion": "sha256 de Descargas (lectura de la copia y "
        "relectura independiente) = sha256 de la copia releída de ORIGEN"})
    _imprime("copia: %d bytes; sha256 %s… igual en Descargas (2 lecturas) y en ORIGEN (releída)"
             % (n, sha_copia[:16]))
    return 0


# ── extrae ──────────────────────────────────────────────────────────────────────────────────
def _copia_entrada(z, info, destino):
    """Stream de la entrada del zip a `destino` (vía .tmp-<opaco>, 0600). zipfile comprueba el CRC
    al terminar. Devuelve (sha256, bytes)."""
    d = os.path.dirname(destino)
    tmp = os.path.join(d, ".tmp-" + os.path.basename(destino))
    h = hashlib.sha256()
    n = 0
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with z.open(info) as src, os.fdopen(fd, "wb") as dst:
            for trozo in iter(lambda: src.read(8 << 20), b""):
                h.update(trozo)
                dst.write(trozo)
                n += len(trozo)
    except Exception:
        os.unlink(tmp)
        raise
    if n != info.file_size:
        os.unlink(tmp)
        raise ValueError("tamaño distinto al del índice del zip")
    os.replace(tmp, destino)
    os.chmod(destino, 0o600)
    return h.hexdigest(), n


def extrae(base):
    with open(ZIP_SELLO, encoding="utf-8") as f:
        sello = json.load(f)
    if _sha256(ZIP_ORIGEN) != sello["sha256"]:
        _imprime("ABORTO: la copia de ORIGEN no casa con su sello")
        return 1
    import identidad_paciente
    titular = identidad_paciente.titular()
    if not titular:
        _imprime("ABORTO: sin titular (perfil.local.json) no puedo marcar PII en los nombres")
        return 1
    hoja = {}
    if os.path.isfile(HOJA_CLAVES):
        with open(HOJA_CLAVES, encoding="utf-8") as f:
            hoja = json.load(f)
    with zipfile.ZipFile(ZIP_ORIGEN) as z:
        mapa, pptx = {}, []
        for info in z.infolist():
            try:
                op = clasifica(info.filename)
            except ValueError as e:
                _imprime("ABORTO: %s" % e)
                return 1
            if op is None:
                if (info.filename.lower().endswith(".pptx") and "/" not in info.filename):
                    pptx.append(info)
                continue
            if op in mapa:
                _imprime("ABORTO: dos pirámides para %s" % op)
                return 1
            mapa[op] = info
        faltan = [o for o in OPACOS if o not in mapa]
        if faltan:
            _imprime("ABORTO: faltan pirámides para %s" % ", ".join(faltan))
            return 1
        if len(pptx) != 1:
            _imprime("ABORTO: esperaba 1 pptx en la raíz del zip y hay %d" % len(pptx))
            return 1
        for op in OPACOS:
            info = mapa[op]
            destino = os.path.join(base, op + ".tif")
            previo = hoja.get(op, {})
            if (os.path.isfile(destino) and previo.get("sha256")
                    and _sha256(destino) == previo["sha256"]):
                _imprime("%-10s ya extraída (sha256 %s…)" % (op, previo["sha256"][:12]))
                continue
            sha, n = _copia_entrada(z, info, destino)
            hoja[op] = {"nombre_original": info.filename, "bytes": n, "crc32": info.CRC,
                        "sha256": sha, "senales": senales_pii(os.path.basename(info.filename),
                                                              titular)}
            _imprime("%-10s extraída: %.1f MB, sha256 %s…, CRC del zip ok" % (op, n / 1e6, sha[:12]))
        if not (os.path.isfile(PPTX_ORIGEN) and hoja.get("pptx", {}).get("sha256")
                and _sha256(PPTX_ORIGEN) == hoja["pptx"]["sha256"]):
            sha, n = _copia_entrada(z, pptx[0], PPTX_ORIGEN)
            hoja["pptx"] = {"nombre_original": pptx[0].filename, "bytes": n, "sha256": sha,
                            "destino": os.path.basename(PPTX_ORIGEN)}
            _imprime("pptx → ORIGEN: %.1f MB, sha256 %s…" % (n / 1e6, sha[:12]))
    for op in OPACOS:                    # señales siempre recalculadas (la regla puede cambiar)
        hoja[op]["senales"] = senales_pii(os.path.basename(hoja[op]["nombre_original"]), titular)
    hoja["zip"] = {"sha256": sello["sha256"], "bytes": sello["bytes"]}
    hoja["nota"] = ("Hoja de claves nombre original ↔ opaco. SOLO en ORIGEN (0600). Sale solo con "
                    "la firma de {{TITULAR}}.")
    C.escribe_json(HOJA_CLAVES, hoja)
    # Erratas: la accesión con más cifras que las demás, el nombre a distancia 1-2 del titular.
    cifras = [hoja[o]["senales"]["cifras_accesion"] for o in OPACOS
              if hoja[o]["senales"]["cifras_accesion"]]
    tipico = collections.Counter(cifras).most_common(1)[0][0] if cifras else None

    def fn(man):
        man["zip_sha256"] = sello["sha256"]
        man["fuente"] = "zip del laboratorio receptor (29-sep-26); solo la carpeta de pirámides"
        for op in OPACOS:
            s = hoja[op]["senales"]
            erratas = []
            if s.get("errata_distancia"):
                erratas.append("nombre de la titular con errata (una pieza a distancia de "
                               "edición %d)" % s["errata_distancia"])
            if s["cifras_accesion"] and tipico and s["cifras_accesion"] != tipico:
                erratas.append("accesión con %d cifras tras la letra (las demás, %d)"
                               % (s["cifras_accesion"], tipico))
            ent = man["laminas"].setdefault(op, {})
            ent.update({
                "opaco": op, "fichero": op + ".tif", "sha256": hoja[op]["sha256"],
                "bytes": hoja[op]["bytes"], "tincion": TINCION[op], "grupo": GRUPO[op],
                "nombre_original_con_pii": {
                    "nombre_titular": s["nombre"] != "no", "accesion": s["accesion"],
                    "iniciales": s["iniciales"]},
                "erratas": erratas})
    C.actualiza_manifiesto(base, fn)
    _imprime("hoja de claves → ORIGEN (0600); manifiesto con %d láminas" % len(OPACOS))
    for op in OPACOS:
        s = hoja[op]["senales"]
        _imprime("%-10s nombre_titular=%s (%d/%d piezas%s) accesión=%s iniciales=%s" % (
            op, s["nombre"], s["piezas_encontradas"], s["piezas"],
            ", errata d=%d" % s["errata_distancia"] if s["errata_distancia"] else "",
            "sí(%d cifras)" % s["cifras_accesion"] if s["accesion"] else "no", s["iniciales"]))
    return 0


# ── identidad H&E (jaula de ingesta: ORIGEN) ────────────────────────────────────────────────
def _texto_pdf(ruta, tmpdir, ocr=False):
    """Capa de texto (pdftotext -layout); si no hay o se pide `ocr`, OCR de la página renderizada
    (pdftoppm 300 ppp + tesseract spa+eng): la cabecera de filiación a veces es imagen."""
    exe = "/opt/homebrew/bin/pdftotext"
    if not ocr:
        r = subprocess.run([exe, "-q", "-layout", ruta, "-"], capture_output=True, timeout=180)
        t = r.stdout.decode("utf-8", "ignore")
        if t.strip():
            return t, "pdftotext"
    os.makedirs(tmpdir, mode=0o700, exist_ok=True)
    pref = os.path.join(tmpdir, "ocr-%d" % os.getpid())
    subprocess.run(["/opt/homebrew/bin/pdftoppm", "-r", "300", "-png", ruta, pref],
                   capture_output=True, timeout=600)
    textos = []
    for fn in sorted(os.listdir(tmpdir)):
        if fn.startswith(os.path.basename(pref)) and fn.endswith(".png"):
            p = os.path.join(tmpdir, fn)
            o = subprocess.run(["/opt/homebrew/bin/tesseract", p, "stdout", "-l", "spa+eng"],
                               capture_output=True, timeout=600)
            textos.append(o.stdout.decode("utf-8", "ignore"))
            os.unlink(p)
    return "\n".join(textos), "ocr-tesseract"


def _accesiones_texto(texto):
    t = re.sub(r"[\s\-./]", "", texto.upper())
    return {(m.group(1), m.group(2)) for m in RE_ACCESION.finditer(t)}


def identidad(base):
    """P-HE, B-HE-1 y B-HE-2: accesión del nombre de fichero contra los informes AP originales
    (copiados a ORIGEN/informes/ por `lector_clinico.py --a`). Imprime solo veredictos."""
    import identidad_paciente
    with open(HOJA_CLAVES, encoding="utf-8") as f:
        hoja = json.load(f)
    if not os.path.isdir(INFORMES):
        _imprime("ABORTO: no hay ORIGEN/informes (cópialos con lector_clinico.py --a)")
        return 1
    informes = {}
    for fn in sorted(os.listdir(INFORMES)):
        if not fn.lower().endswith(".pdf"):
            continue
        ruta = os.path.join(INFORMES, fn)
        texto, via = _texto_pdf(ruta, os.path.join(base, "tmp"))
        # Sin `ruta`: lo que se verifica es la capa de texto ya extraída (con la ruta .pdf,
        # `es_textual` lo daría por binario y el veredicto sería «no_textual»).
        ver, _ = identidad_paciente.verificar(texto)
        ver_ocr = None
        if ver != "coincide":                    # segunda lectura: OCR de la página renderizada
            t_ocr, _ = _texto_pdf(ruta, os.path.join(base, "tmp"), ocr=True)
            ver_ocr, _ = identidad_paciente.verificar(t_ocr)
            texto = texto + "\n" + t_ocr
            if ver_ocr == "coincide":
                ver, via = "coincide", via + "+ocr"
        norm = re.sub(r"\s+", "", texto.upper())
        informes[fn] = {"sha256": _sha256(ruta), "via_texto": via, "identidad": ver,
                        "identidad_ocr": ver_ocr,
                        "accesiones": sorted("".join(a) for a in _accesiones_texto(texto)),
                        "cortes_A2": sorted(set(re.findall(r"A2-[12]", norm)))}
        _imprime("informe %s: texto por %s; identidad=%s (OCR: %s); %d accesión(es) en el "
                 "texto; cortes A2-x: %s" % (fn, via, ver, ver_ocr, len(informes[fn]["accesiones"]),
                                ",".join(informes[fn]["cortes_A2"]) or "ninguno"))
    res = {}
    for op in ("P-HE", "B-HE-1", "B-HE-2"):
        acc = accesion_de(os.path.basename(hoja[op]["nombre_original"]))
        if not acc:
            res[op] = {"accesion_en_nombre": False}
            _imprime("%-7s sin accesión en el nombre" % op)
            continue
        s = "".join(acc)
        exactos = [fn for fn, i in informes.items() if s in i["accesiones"]
                   and identidad_paciente.SIRVE.get(i["identidad"]) and i["identidad"] == "coincide"]
        exactos_sin_id = [fn for fn, i in informes.items() if s in i["accesiones"]]
        mejor = None
        for fn, i in informes.items():
            for a in i["accesiones"]:
                if a[:2] != acc[0]:
                    continue
                d = _lev(s, a)
                if mejor is None or d < mejor[0]:
                    mejor = (d, fn)
        cortes = re.findall(r"A2-([12])", hoja[op]["nombre_original"].upper())
        res[op] = {"accesion_en_nombre": True, "casa_exacta_con_identidad": exactos,
                   "casa_exacta": exactos_sin_id,
                   "identidad_de_esos_informes": sorted({informes[f]["identidad"]
                                                         for f in exactos_sin_id}),
                   "distancia_minima": mejor[0] if mejor else None,
                   "informe_mas_cercano": mejor[1] if mejor else None,
                   "corte_en_nombre": ("A2-" + cortes[0]) if cortes else None}
        _imprime("%-7s accesión del nombre: casa exacta con %d informe(s) con identidad "
                 "«coincide» (%s); distancia mínima a una accesión de informe = %s (%s)" % (
                     op, len(exactos), ", ".join(exactos) or "ninguno",
                     mejor[0] if mejor else "—", mejor[1] if mejor else "—"))
    C.escribe_json(IDENTIDAD_HE, {"fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                  "informes": informes, "laminas": res})

    def fn(man):
        for op, r in res.items():
            man["laminas"][op]["identidad_he"] = {
                "accesion_en_nombre": r.get("accesion_en_nombre"),
                "accesion_casa_con_informe_AP_original": bool(r.get("casa_exacta")),
                "identidad_del_informe_verificada": bool(r.get("casa_exacta_con_identidad")),
                "veredicto_identidad_paciente": r.get("identidad_de_esos_informes"),
                "distancia_minima": r.get("distancia_minima"),
                "corte_en_nombre": r.get("corte_en_nombre"),
                "metodo": ("accesión del nombre de fichero asignado por el laboratorio receptor "
                           "contra el informe AP original (copiado intacto por la ventanilla); "
                           "identidad del informe por identidad_paciente (nombre + fecha de "
                           "nacimiento)")}
    C.actualiza_manifiesto(base, fn)
    return 0


# ══ Por lámina (jaula analisis, venv patologia) ═════════════════════════════════════════════
def _l0_page(tf):
    """IFD de L0 = el de mayor anchura (nunca por índice)."""
    return max(tf.pages, key=lambda p: p.imagewidth)


def metadatos(base, op):
    import tifffile
    import laminillas_lector as L
    lam = L.abre(op)
    ruta = lam.ruta
    with tifffile.TiffFile(ruta) as tf:
        p0 = _l0_page(tf)
        ifds = []
        for p in tf.pages:
            ifds.append({"ancho": p.imagewidth, "alto": p.imagelength, "teselado": p.is_tiled,
                         "subfiletype": int(p.subfiletype)})
        tags = sorted({t.code for p in tf.pages for t in p.tags})
        d270 = p0.tags.get(270)
        fmt = {"bigtiff": tf.is_bigtiff, "n_ifd": len(tf.pages),
               "tesela_l0": [p0.tilewidth, p0.tilelength],
               "compresion": p0.compression.name, "fotometrica": p0.photometric.name,
               "subsampling": list(p0.subsampling) if p0.subsampling else None,
               "tags": tags, "ifd_no_teselados": sum(not i["teselado"] for i in ifds),
               "tag270_longitud": len(d270.value) if d270 is not None else None}
    def fn(man):
        ent = man["laminas"][op]
        ent["mpp"] = lam.mpp_l0
        ent["dimensiones_l0"] = list(lam.dimensiones_l0)
        ent["niveles"] = [{"factor": f, "mpp": round(m, 5), "ancho": w, "alto": h}
                          for f, m, (w, h) in lam.niveles]
        ent["formato"] = fmt
    C.actualiza_manifiesto(base, fn)
    return lam, fmt


def _tile_info(ruta):
    import tifffile
    with tifffile.TiffFile(ruta) as tf:
        p0 = _l0_page(tf)
        return (list(p0.dataoffsets), list(p0.databytecounts), p0.tilewidth, p0.tilelength,
                p0.imagewidth, p0.imagelength)


def zona(base, op):
    """Zona escaneada = teselas L0 que no son relleno. El relleno es byte a byte idéntico (un
    JPEG de blanco puro): se reconoce por bytes y se CONFIRMA decodificando una muestra con el
    lector único (todo 255) y otra de escaneadas (ninguna toda 255)."""
    import numpy as np
    from shapely.geometry import box, mapping
    from shapely.ops import unary_union
    import laminillas_lector as L
    lam = L.abre(op)
    offs, cnts, tw, th, W, H = _tile_info(lam.ruta)
    nx, ny = (W + tw - 1) // tw, (H + th - 1) // th
    if len(offs) != nx * ny:
        raise ValueError("%s: %d teselas en L0, esperaba %d" % (op, len(offs), nx * ny))
    fd = os.open(lam.ruta, os.O_RDONLY)
    try:
        grupos = collections.Counter()
        hashes = {}
        for k, (o, c) in enumerate(zip(offs, cnts)):
            if c and c < 20000:
                hsh = hashlib.sha1(os.pread(fd, c, o)).hexdigest()
                hashes[k] = hsh
                grupos[(c, hsh)] += 1
    finally:
        os.close(fd)
    clase = np.ones((ny, nx), np.uint8)                   # 1 escaneada, 0 relleno
    relleno_bytes = None
    if grupos:
        (c_rel, h_rel), n_rel = grupos.most_common(1)[0]
        if n_rel >= 4:
            relleno_bytes = c_rel
            for k, hsh in hashes.items():
                if cnts[k] == c_rel and hsh == h_rel:
                    clase.flat[k] = 0
    mpp0 = lam.mpp_l0
    rng = np.random.default_rng([SEMILLA, OPACOS.index(op), 1])

    def todo_blanco(k):
        i, j = divmod(int(k), nx)
        a = L.lee_region(lam, mpp0, j * tw, i * th, tw, th)
        return bool((a == 255).all())

    # Teselas pequeñas no idénticas al relleno: se decodifican TODAS (¿relleno con otra
    # codificación?). Muestra de confirmación: 32 de relleno y 32 escaneadas.
    dudosas = [k for k in range(nx * ny) if clase.flat[k] == 1 and relleno_bytes
               and cnts[k] <= 1.5 * relleno_bytes]
    reclasificadas = 0
    for k in dudosas:
        if todo_blanco(k):
            clase.flat[k] = 0
            reclasificadas += 1
    idx_rel = np.flatnonzero(clase.ravel() == 0)
    idx_esc = np.flatnonzero(clase.ravel() == 1)
    m_rel = rng.choice(idx_rel, size=min(32, len(idx_rel)), replace=False) if len(idx_rel) else []
    m_esc = rng.choice(idx_esc, size=min(32, len(idx_esc)), replace=False)
    conf_rel = sum(todo_blanco(k) for k in m_rel)
    conf_esc = sum(todo_blanco(k) for k in m_esc)
    if conf_rel != len(m_rel) or conf_esc != 0:
        raise ValueError("%s: el relleno no se confirma al decodificar (%d/%d relleno blanco, "
                         "%d/%d escaneadas blancas)" % (op, conf_rel, len(m_rel), conf_esc,
                                                         len(m_esc)))
    cajas = []
    for i in range(ny):
        fila = clase[i]
        j = 0
        while j < nx:
            if fila[j]:
                j0 = j
                while j < nx and fila[j]:
                    j += 1
                cajas.append(box(j0 * tw, i * th, min(j * tw, W), min((i + 1) * th, H)))
            else:
                j += 1
    geom = unary_union(cajas).buffer(0)
    os.makedirs(os.path.join(base, "zona"), mode=0o700, exist_ok=True)
    rel_geo = os.path.join("zona", op + ".geojson")
    rel_npz = os.path.join("zona", op + "-teselas.npz")
    with open(os.path.join(base, rel_geo), "w", encoding="utf-8") as f:
        json.dump({"type": "Feature", "properties": {"opaco": op, "unidades": "px L0"},
                   "geometry": mapping(geom)}, f)
    np.savez_compressed(os.path.join(base, rel_npz), clase=clase, tw=tw, th=th, W=W, H=H)
    mm2 = geom.area * mpp0 * mpp0 / 1e6
    total_mm2 = W * H * mpp0 * mpp0 / 1e6
    info = {"fichero": rel_geo, "sha256": _sha256(os.path.join(base, rel_geo)),
            "teselas": rel_npz, "teselas_sha256": _sha256(os.path.join(base, rel_npz)),
            "mm2": round(mm2, 3), "mm2_imagen": round(total_mm2, 3),
            "teselas_escaneadas": int(clase.sum()), "teselas_relleno": int((clase == 0).sum()),
            "fraccion_relleno": round(float((clase == 0).mean()), 4),
            "bytes_tesela_relleno": relleno_bytes, "reclasificadas_al_decodificar": reclasificadas,
            "confirmacion": "%d/%d teselas de relleno decodificadas = 255 exacto; %d/%d escaneadas "
                            "no son blanco puro" % (conf_rel, len(m_rel), len(m_esc) - conf_esc,
                                                    len(m_esc)),
            "metodo": "teselas L0 no idénticas byte a byte a la tesela de relleno, más las "
                      "pequeñas que al decodificar no son 255 exacto; unión de teselas, recortada "
                      "a la imagen"}

    def fn(man):
        man["laminas"][op]["zona_escaneada"] = info
    C.actualiza_manifiesto(base, fn)
    return info


def _clase(base, op):
    import numpy as np
    with np.load(os.path.join(base, "zona", op + "-teselas.npz")) as z:
        return z["clase"], int(z["tw"]), int(z["th"]), int(z["W"]), int(z["H"])


def i0(base, op):
    """I0 local: mediana del vidrio por tesela L0 (medida en el nivel ×8), suavizada con una
    gaussiana normalizada de FWHM ~1 mm; si hay patrón por franja (columnas o filas de teselas
    con desvío sistemático > 3 niveles), campo plano separable fila×columna sobre lo suavizado."""
    import numpy as np
    from scipy.ndimage import gaussian_filter
    import laminillas_lector as L
    lam = L.abre(op)
    clase, tw, th, W, H = _clase(base, op)
    ny, nx = clase.shape
    f8, mpp8, _ = lam.nivel(8)
    b = tw // 8
    candm = np.full((ny, nx, 3), np.nan, np.float32)
    frac_c = np.zeros((ny, nx), np.float32)
    tiras = []
    for i in range(ny):
        tira = L.lee_region(lam, mpp8, 0, i * th, nx * b, b)
        bloques = tira.reshape(b, nx, b, 3).transpose(1, 0, 2, 3).reshape(nx, b * b, 3)
        tiras.append(bloques)
        for j in range(nx):
            if not clase[i, j]:
                continue
            px = bloques[j]
            no_rel = ~(px == 255).all(1)
            cand = no_rel & (px.min(1) >= 200) & ((px.max(1).astype(int) - px.min(1)) <= 20)
            frac_c[i, j] = cand.mean()
            if cand.sum() >= 50:
                candm[i, j] = np.median(px[cand], axis=0)
    puros = frac_c >= 0.8
    if puros.sum() < 3:
        puros = frac_c >= 0.5
    if puros.sum() == 0:
        raise ValueError("%s: sin teselas de vidrio para el I0" % op)
    ref = np.nanmedian(candm[puros], axis=0)
    med = np.full((ny, nx, 3), np.nan, np.float32)
    frac_v = np.zeros((ny, nx), np.float32)
    for i in range(ny):
        bloques = tiras[i]
        for j in range(nx):
            if not clase[i, j]:
                continue
            px = bloques[j]
            no_rel = ~(px == 255).all(1)
            vid = (no_rel & (px.min(1) >= 200) & ((px.max(1).astype(int) - px.min(1)) <= 20)
                   & (px >= (ref - 12)).all(1))
            frac_v[i, j] = vid.mean()
            if vid.sum() >= 0.10 * b * b:
                med[i, j] = np.median(px[vid], axis=0)
    del tiras
    valido = ~np.isnan(med[..., 0])
    sigma = (I0_FWHM_UM / 2.3548) / (tw * lam.mpp_l0)
    den = gaussian_filter(valido.astype(np.float64), sigma, mode="nearest")
    sm = np.empty_like(med)
    for c in range(3):
        num = gaussian_filter(np.where(valido, med[..., c], 0).astype(np.float64), sigma,
                              mode="nearest")
        sm[..., c] = np.where(den > 1e-3, num / np.maximum(den, 1e-12), ref[c])
    resid = np.where(valido[..., None], med - sm, np.nan)
    with np.errstate(all="ignore"):
        cols = np.array([np.nanmedian(resid[:, j], axis=0) if valido[:, j].sum() >= 5
                         else np.full(3, np.nan) for j in range(nx)])
        filas = np.array([np.nanmedian(resid[i], axis=0) if valido[i].sum() >= 5
                          else np.full(3, np.nan) for i in range(ny)])

    def rango(a):
        a = a[~np.isnan(a).any(1)]
        if len(a) < 5:
            return 0.0
        return float(np.max(np.percentile(a, 95, axis=0) - np.percentile(a, 5, axis=0)))
    amp_c, amp_f = rango(cols), rango(filas)
    franja = max(amp_c, amp_f) > FRANJA_MAX
    campo = sm.copy()
    if franja:
        campo += np.nan_to_num(cols)[None, :, :] + np.nan_to_num(filas)[:, None, :]
    os.makedirs(os.path.join(base, "i0"), mode=0o700, exist_ok=True)
    rel = os.path.join("i0", op + ".npz")
    np.savez_compressed(os.path.join(base, rel), i0=campo.astype(np.float32), paso_l0=float(tw),
                        x0=0.0, y0=0.0, crudo=med, valido=valido, ref=ref, frac_vidrio=frac_v)
    info = {"fichero": rel, "sha256": _sha256(os.path.join(base, rel)), "paso_l0": tw,
            "nivel_medido": "x8 (%.4f µm/px)" % mpp8,
            "vidrio_referencia_rgb": [round(float(v), 1) for v in ref],
            "teselas_con_vidrio": int(valido.sum()), "teselas_escaneadas": int(clase.sum()),
            "rango_i0_p5_p95": [[round(float(v), 1) for v in np.percentile(campo[clase == 1], q,
                                                                           axis=0)]
                                for q in (5, 95)],
            "franja": bool(franja), "amplitud_franja_columnas": round(amp_c, 2),
            "amplitud_franja_filas": round(amp_f, 2),
            "metodo": "mediana por tesela L0 de los píxeles de vidrio (no relleno, mín. canal ≥200, "
                      "croma ≤20, cada canal ≥ vidrio de referencia − 12) medidos en ×8; gaussiana "
                      "normalizada FWHM %.0f µm; campo plano fila×columna si franja > %.0f" % (
                          I0_FWHM_UM, FRANJA_MAX)}

    def fn(man):
        man["laminas"][op]["i0"] = info
    C.actualiza_manifiesto(base, fn)
    return info


def _od_suma(img, i0rgb):
    import numpy as np
    x = np.clip(img.astype(np.float32), 1.0, 255.0)
    od = -np.log10(x / np.maximum(i0rgb, 1.0))
    return np.clip(od, 0, None).sum(-1)


def tejido(base, op):
    """Máscara de tejido a ~8 µm/px (bloques 4×4 del nivel ×8 nativo, 2 µm/px), fuera de la zona
    escaneada = 0. Por bloque: media de ODsum (Σ max(0, −log10(I/I0 local))) y su desviación
    típica a 2 µm (textura). Ruido del vidrio estimado en la propia lámina (mediana + MAD de los
    bloques de teselas con ≥90 % de vidrio según `i0`; con <200, umbrales por defecto). Regla principal, por histéresis: semilla = media
    > ALTO; crece por (media > BAJO) o (textura > ruido de textura + 8 MAD), con BAJO = max(0,02,
    ruido de media + 8 MAD). Además, área a umbrales fijos de media ODsum (sensibilidad). Guarda
    la media de ODsum y la máscara a 8 µm y a 32 µm (bloques 16×16; máscara por mayoría)."""
    import numpy as np
    from scipy import ndimage as ndi
    from skimage.filters import apply_hysteresis_threshold
    import laminillas_lector as L
    lam = L.abre(op)
    clase, tw, th, W, H = _clase(base, op)
    f8, mpp8, (w8n, h8n) = lam.nivel(8)
    ds8 = W / float(w8n)
    r = 4                                                   # 4 px de ×8 ≈ 8 µm
    mpp_t = mpp8 * r
    wb, hb = (w8n + r - 1) // r, (h8n + r - 1) // r
    arr, tf = L.i0_raster(lam)
    f = L.interpola_i0(arr, tf)
    media = np.zeros((hb, wb), np.float32)
    desv = np.zeros((hb, wb), np.float32)
    xs = (np.arange(wb * r) + 0.5) * ds8
    filas = 128 * r
    for y0 in range(0, hb * r, filas):
        hh = min(filas, hb * r - y0)
        img = L.lee_region(lam, mpp8, 0, int(round(y0 * ds8)), wb * r, hh)
        ys = (np.arange(y0, y0 + hh) + 0.5) * ds8
        X, Y = np.meshgrid(xs, ys)
        od = _od_suma(img, f(X, Y))
        blo = od.reshape(hh // r, r, wb, r)
        media[y0 // r:(y0 + hh) // r] = blo.mean(axis=(1, 3))
        desv[y0 // r:(y0 + hh) // r] = blo.std(axis=(1, 3))
    escala = mpp_t / lam.mpp_l0                             # px L0 por bloque
    cx = (np.arange(wb) + 0.5) * escala
    cy = (np.arange(hb) + 0.5) * escala
    ti = np.clip((cy // th).astype(int), 0, clase.shape[0] - 1)
    tj = np.clip((cx // tw).astype(int), 0, clase.shape[1] - 1)
    dentro = (clase[ti][:, tj].astype(bool) & (cy[:, None] < H) & (cx[None, :] < W))
    media[~dentro] = 0
    desv[~dentro] = 0
    # Ruido del vidrio: bloques de teselas L0 con ≥90 % de píxeles de vidrio (los mide `i0`).
    with np.load(os.path.join(base, "i0", op + ".npz")) as z:
        frac_v = z["frac_vidrio"]
    vidrio = (frac_v[ti][:, tj] >= 0.9) & dentro
    n_vidrio = int(vidrio.sum())
    if n_vidrio >= 200:
        vm, vs = media[vidrio], desv[vidrio]
        med_m, mad_m = float(np.median(vm)), float(np.median(np.abs(vm - np.median(vm))))
        med_s, mad_s = float(np.median(vs)), float(np.median(np.abs(vs - np.median(vs))))
        bajo = max(TEJIDO_BAJO, med_m + 8 * 1.4826 * mad_m)
        t_tex = max(TEXTURA_MIN, med_s + 8 * 1.4826 * mad_s)
    else:                                   # casi sin vidrio puro: umbrales por defecto
        med_m = mad_m = med_s = mad_s = float("nan")
        bajo, t_tex = TEJIDO_BAJO, TEXTURA_DEFECTO
    candidato = ((media > bajo) | (desv > t_tex)) & dentro
    semilla = (media > TEJIDO_ALTO) & dentro
    lab, n = ndi.label(candidato, structure=np.ones((3, 3)))
    con_semilla = np.zeros(n + 1, bool)
    if n:
        con_semilla[np.unique(lab[semilla])] = True
        con_semilla[0] = False
    mask = con_semilla[lab]
    mask = ndi.binary_closing(mask, structure=np.ones((3, 3))) & dentro
    px_mm2 = (mpp_t / 1000.0) ** 2

    def frags(m):
        lab, n = ndi.label(m, structure=np.ones((3, 3)))
        tam = np.bincount(lab.ravel())[1:] * px_mm2 if n else np.array([])
        grandes = sorted((float(a) for a in tam if a > FRAG_MIN_MM2), reverse=True)
        return lab, tam, grandes
    lab, tam, grandes = frags(mask)
    principal = {"mm2": round(float(mask.sum() * px_mm2), 3),
                 "fragmentos_gt_0_2mm2": len(grandes),
                 "mm2_fragmentos": [round(a, 2) for a in grandes[:12]]}
    centro = None
    if len(tam):
        k = int(np.argmax(tam)) + 1
        yy, xx = ndi.center_of_mass(lab == k)
        centro = [float((xx + 0.5) * escala), float((yy + 0.5) * escala)]
    est = {}
    for T in UMBRALES_OD:
        m = ndi.binary_closing(media > T, structure=np.ones((3, 3))) & dentro
        _, _, g = frags(m)
        est["%.2f" % T] = {"mm2": round(float((media > T).sum() * px_mm2), 3),
                           "fragmentos_gt_0_2mm2": len(g), "mm2_fragmentos": [round(a, 2)
                                                                              for a in g[:8]]}
    q = int(round(MPP_HUELLA / mpp_t))                      # 4 bloques de 8 µm = 32 µm
    hp, wp = (hb + q - 1) // q * q, (wb + q - 1) // q * q
    mp = np.zeros((hp, wp), np.float32)
    mp[:hb, :wb] = mask
    mask32 = mp.reshape(hp // q, q, wp // q, q).mean(axis=(1, 3)) >= 0.5
    op_ = np.zeros((hp, wp), np.float32)
    op_[:hb, :wb] = media
    media32 = op_.reshape(hp // q, q, wp // q, q).mean(axis=(1, 3))
    os.makedirs(os.path.join(base, "mascaras"), mode=0o700, exist_ok=True)
    rel8 = os.path.join("mascaras", op + "-8um.npz")
    rel32 = os.path.join("mascaras", op + "-32um.npz")
    np.savez_compressed(os.path.join(base, rel8), odsum=media.astype(np.float16),
                        textura=desv.astype(np.float16), mascara=mask, dentro=dentro, mpp=mpp_t,
                        escala_l0=escala)
    np.savez_compressed(os.path.join(base, rel32), odsum=media32.astype(np.float32),
                        mascara=mask32, mpp=mpp_t * q, escala_l0=escala * q)
    info = {"principal": principal, "por_umbral_media_odsum": est,
            "ruido_vidrio": {"bloques": n_vidrio, "media_mediana": round(med_m, 4),
                             "media_mad": round(mad_m, 4), "textura_mediana": round(med_s, 4),
                             "textura_mad": round(mad_s, 4)},
            "umbral_bajo": round(bajo, 4), "umbral_alto": TEJIDO_ALTO,
            "umbral_textura": round(t_tex, 4), "mpp_mascara": round(mpp_t, 4),
            "centro_fragmento_mayor_l0": centro,
            "odsum_8um": rel8, "odsum_8um_sha256": _sha256(os.path.join(base, rel8)),
            "odsum_32um": rel32, "odsum_32um_sha256": _sha256(os.path.join(base, rel32)),
            "metodo": tejido.__doc__.split("\n\n")[0].replace("\n    ", " ")}

    def fn(man):
        man["laminas"][op]["tejido"] = info
    C.actualiza_manifiesto(base, fn)
    return info


def prueba_lector(base, op):
    """(a) dos lecturas de la misma tesela, sha256 idéntico; (b) OpenSlide vs tifffile (zarr por
    tesela), diferencia máxima ≤2; deja los recortes ×8 de OpenSlide para (c), que corre en el
    venv valis con pyvips."""
    import numpy as np
    import tifffile
    import zarr
    import laminillas_lector as L
    lam = L.abre(op)
    clase, tw, th, W, H = _clase(base, op)
    ny, nx = clase.shape
    idx = np.flatnonzero(clase.ravel() == 1)
    rng = np.random.default_rng([SEMILLA, OPACOS.index(op)])
    sel = np.sort(rng.choice(idx, size=min(N_PRUEBA, len(idx)), replace=False))
    mpp0 = lam.mpp_l0
    f8, mpp8, (w8, h8) = lam.nivel(8)
    ds8 = W / float(w8)
    a_ok, difs, mal_b = 0, [], 0
    recortes, coords = [], []
    with tifffile.TiffFile(lam.ruta) as tf:
        z = zarr.open(_l0_page(tf).aszarr(), mode="r")
        for k in sel:
            i, j = divmod(int(k), nx)
            x, y = j * tw, i * th
            a1 = L.lee_region(lam, mpp0, x, y, tw, th)
            a2 = L.lee_region(lam, mpp0, x, y, tw, th)
            if hashlib.sha256(a1.tobytes()).digest() == hashlib.sha256(a2.tobytes()).digest():
                a_ok += 1
            wv, hv = min(tw, W - x), min(th, H - y)
            ref = np.asarray(z[y:y + hv, x:x + wv])
            d = int(np.abs(a1[:hv, :wv].astype(np.int16) - ref.astype(np.int16)).max())
            difs.append(d)
            mal_b += d > 2
            lado = tw // 8
            recortes.append(L.lee_region(lam, mpp8, x, y, lado, lado))
            coords.append((x, y))
    os.makedirs(os.path.join(base, "lector"), mode=0o700, exist_ok=True)
    rel = os.path.join("lector", op + "-x8-openslide.npz")
    np.savez_compressed(os.path.join(base, rel), recortes=np.stack(recortes),
                        coords=np.array(coords), ds8=ds8)
    difs = np.array(difs)
    info = {"semilla": [SEMILLA, OPACOS.index(op)], "n_teselas": int(len(sel)),
            "a_sha256_identico": "%d/%d" % (a_ok, len(sel)), "a_ok": a_ok == len(sel),
            "b_dif_max": int(difs.max()), "b_dif_p99": float(np.percentile(difs, 99)),
            "b_teselas_dif_gt2": int(mal_b), "b_ok": bool(mal_b == 0),
            "b_lector_referencia": "tifffile %s (zarr por tesela, imagecodecs)" % tifffile.__version__,
            "c_recortes": rel, "c_recortes_sha256": _sha256(os.path.join(base, rel)),
            "factor_x8_real": round(ds8, 6)}

    def fn(man):
        p = man["laminas"][op].setdefault("prueba_lector", {})
        p.update(info)
    C.actualiza_manifiesto(base, fn)
    return info


def revision(base, op):
    """PNG de revisión (≤1024 px): miniatura de la lámina entera y un campo ×8 de 1024×1024 en el
    fragmento mayor. El relleno se pinta del color local del vidrio (plan F1.1)."""
    import numpy as np
    from PIL import Image
    import laminillas_lector as L
    lam = L.abre(op)
    clase, tw, th, W, H = _clase(base, op)
    mpp0 = lam.mpp_l0
    f = L.i0_local(lam)

    def pinta(img, x0_l0, y0_l0, paso_l0):
        h, w = img.shape[:2]
        xs = x0_l0 + (np.arange(w) + 0.5) * paso_l0
        ys = y0_l0 + (np.arange(h) + 0.5) * paso_l0
        ti = np.clip((ys // th).astype(int), 0, clase.shape[0] - 1)
        tj = np.clip((xs // tw).astype(int), 0, clase.shape[1] - 1)
        fuera = ~clase[ti][:, tj].astype(bool) | (ys[:, None] >= H) | (xs[None, :] >= W)
        # Píxeles mezcla de relleno (255) y vidrio en el borde de la zona escaneada, o relleno
        # dentro de una tesela escaneada: tras el remuestreo por área quedan en 250-255 y se ven
        # como rayas blancas. El vidrio real llega a 253 y el tejido no pasa de ahí en los tres
        # canales: pintarlos con el I0 local no cambia nada que se vaya a mirar.
        fuera |= img.min(axis=-1) >= 250
        if fuera.any():
            X, Y = np.meshgrid(xs, ys)
            vid = np.rint(f(X, Y)).astype(np.uint8)
            img = img.copy()
            img[fuera] = vid[fuera]
        return img
    lado = max(W, H)
    mpp_t = mpp0 * lado / 1024.0
    wt, ht = max(1, int(round(W * mpp0 / mpp_t))), max(1, int(round(H * mpp0 / mpp_t)))
    mini = pinta(L.lee_region(lam, mpp_t, 0, 0, wt, ht), 0, 0, mpp_t / mpp0)
    f8, mpp8, _ = lam.nivel(8)
    centro = C.lee_manifiesto(base)["laminas"][op].get("tejido", {}).get(
        "centro_fragmento_mayor_l0")
    cx, cy = centro or (W / 2.0, H / 2.0)
    ext = 1024 * mpp8 / mpp0
    x0 = int(max(0, min(W - ext, cx - ext / 2)))
    y0 = int(max(0, min(H - ext, cy - ext / 2)))
    wc = int(min(1024, math.floor((W - x0) * mpp0 / mpp8)))
    hc = int(min(1024, math.floor((H - y0) * mpp0 / mpp8)))
    campo = pinta(L.lee_region(lam, mpp8, x0, y0, wc, hc), x0, y0, mpp8 / mpp0)
    d = os.path.join(base, "revision")
    os.makedirs(d, mode=0o700, exist_ok=True)
    rel_m = os.path.join("revision", op + "__thumbnail.png")
    rel_c = os.path.join("revision", op + "__x8.png")
    Image.fromarray(mini).save(os.path.join(base, rel_m))
    Image.fromarray(campo).save(os.path.join(base, rel_c))
    info = {"miniatura": {"fichero": rel_m, "ancho": wt, "alto": ht, "mpp": round(mpp_t, 4),
                          "sha256": _sha256(os.path.join(base, rel_m))},
            "campo_x8": {"fichero": rel_c, "ancho": wc, "alto": hc, "mpp": round(mpp8, 4),
                         "x_l0": x0, "y_l0": y0, "sha256": _sha256(os.path.join(base, rel_c))},
            "relleno": "pintado con el I0 local"}

    def fn(man):
        man["laminas"][op]["revision"] = info
    C.actualiza_manifiesto(base, fn)
    return info


def _version_exporta(largo=False):
    """sha256 de tools/exporta_n1.py (el OCR y la Puerta con que se revisa el cristal)."""
    h = _sha256(os.path.join(_AQUI, "exporta_n1.py"))
    return h if largo else h[:10]


# Revisión del cristal r4 (2-oct-26), con la Puerta de N1 de la tercera pasada (7a7562a): la
# MISMA decisión que tomaría exporta_n1 al sacar la lámina (`_motivos_cristal_tiff` para el TIFF,
# `revisar_cristal(…, ESCALAS_PNG, con_puerta=True)` para cada PNG), leída como la lee la Puerta
# (las teselas del TIFF, no OpenSlide), y además DÓNDE cae cada palabra y cada cifra de trazos y
# coherencia. `laminillas_exporta` exige esta versión y el sha256 de exporta_n1 con que se hizo.
# r5 (2-oct-26, el mismo día): `n1_apta` = ningún motivo de TEXTO o TRAZOS (OCR + Puerta en ×8,
# niveles menores, ×4 entero y PNG; trazos en ×8, menores y PNG). La coherencia de niveles se mide
# y se guarda aparte (`coherencia_salta`), y la sigue exigiendo exporta_n1 al sacar el TIFF (paso
# 1-bis si salta): no es texto, no toca a los PNG —que la Puerta mira píxel a píxel— y en r4, que
# la metía en n1_apta, saltó en las 4 primeras láminas reales (diferencia 43-245) y las cerraba
# enteras sin una sola palabra leída.
# Misma r5, con más campos (2-oct-26, relleno de tesela y hoja del 1-bis): el sha256 del TIFF que
# se revisó (`tiff_sha256`), por nivel las ventanas de la coherencia que pasan el umbral, y el
# relleno que rehúsa exporta_n1 (`coherencia["relleno"]`). La hoja calcula con ellos la huella de
# su orden; una r5 sin `tiff_sha256` la marca TODAVÍA NO. La vigencia la sigue fijando el sha256 de
# exporta_n1 (que cambió con esto: hay que repetirla).
REVISION_CRISTAL = "r5"


def _trozos_con_origen(img, lado=6000, solape=1200):
    """Los mismos trozos, en el mismo orden, que `exporta_n1._trozos`, con su esquina (x, y)."""
    w, h = img.size
    if w <= lado and h <= lado:
        yield 0, 0, img
        return
    paso = lado - solape
    for y in range(0, max(1, h - solape), paso):
        for x in range(0, max(1, w - solape), paso):
            yield x, y, img.crop((x, y, min(w, x + lado), min(h, y + lado)))


def _lecturas_con_cajas(img, escalas):
    """Las pasadas de `exporta_n1.lecturas_ocr` (escala × trozo × psm, en ese orden), cada una
    como (escala, psm, [(palabra, confianza, x0, y0, x1, y1)]) con la caja en px de `img`."""
    from PIL import Image
    import exporta_n1 as E
    img = img.convert("RGB")
    pasadas = []
    for e in escalas:
        im = img if e == 1.0 else img.resize((max(1, int(img.width * e)), max(1, int(img.height * e))),
                                              Image.LANCZOS)
        for tx, ty, trozo in _trozos_con_origen(im):
            for psm in E.PSMS:
                pasadas.append((e, psm, [(w, c, (tx + l) / e, (ty + a) / e, (tx + l + an) / e,
                                          (ty + a + al) / e)
                                         for w, c, l, a, an, al in _ocr_cajas(trozo, psm)]))
    return pasadas


def _localizador(base, op, mpp0):
    """(donde, fragmento) para una caja en px de L0: clase de sitio según la máscara de 8 µm
    (tejido / borde ≤200 µm / vidrio / relleno) y mm² del fragmento que contiene su centro (una
    mancha de tinta sobre vidrio también es «tejido» para la máscara: su fragmento es diminuto)."""
    import numpy as np
    from scipy import ndimage as ndi
    with np.load(os.path.join(base, "mascaras", op + "-8um.npz")) as z:
        mask, dentro = z["mascara"].astype(bool), z["dentro"].astype(bool)
        esc = float(z["escala_l0"])                       # px L0 por bloque de ~8 µm
    dist_um = ndi.distance_transform_edt(~mask) * esc * mpp0
    etiquetas, _n = ndi.label(mask, structure=np.ones((3, 3)))
    area_frag = np.bincount(etiquetas.ravel()) * (esc * mpp0 / 1000.0) ** 2

    def _ij(cx, cy):
        j = int(min(max(cx // esc, 0), mask.shape[1] - 1))
        i = int(min(max(cy // esc, 0), mask.shape[0] - 1))
        return i, j

    def donde(x0, y0, x1, y1):
        i, j = _ij((x0 + x1) / 2.0, (y0 + y1) / 2.0)
        if not dentro[i, j]:
            return "relleno (fuera de la zona escaneada)"
        if mask[i, j]:
            return "dentro del tejido"
        return "borde (≤200 µm)" if dist_um[i, j] <= 200 else "vidrio (>200 µm del tejido)"

    def fragmento(x0, y0, x1, y1):
        k = etiquetas[_ij((x0 + x1) / 2.0, (y0 + y1) / 2.0)]
        return round(float(area_frag[k]), 3) if k else 0.0
    return donde, fragmento


def _revisa_imagen(img, escalas, trazos, a_l0, loc, mpp0):
    """Lo que `exporta_n1.revisar_cristal(img, escalas, con_puerta=True, trazos=trazos)` decide
    (mismos motivos, mismo texto: lo fija un test), de UNA pasada de OCR que además guarda la caja
    de cada palabra. `a_l0(x, y)`: un punto de `img` en px de L0. Nunca guarda la palabra."""
    import exporta_n1 as E
    con_cajas = _lecturas_con_cajas(img, escalas)
    pasadas = [[(w, c) for w, c, *_caja in p] for _e, _psm, p in con_cajas]
    motivos = []
    hits = E._sospechosas(pasadas)
    if hits:
        motivos.append("OCR: %d palabra(s) de ≥4 alfanuméricos" % len(hits))
    capas = E._motivos_texto_ocr(pasadas)
    if capas:
        motivos.append("OCR: lo leído no pasa la Puerta de N1 (%s)" % ", ".join(capas))
    det = {"ancho": img.width, "alto": img.height, "capas_puerta": capas}
    if trazos:
        n, umbral = E.trazos_de_rotulador(img)
        if n >= umbral:
            motivos.append("trazos de rotulador: %d px (umbral %d)" % (n, umbral))
        det["trazos"] = {"px": n, "umbral": umbral, "rojo_px": px_rojo(img)[200], "salta": n >= umbral}

    def mm(v):
        return round(v * mpp0 / 1000.0, 3)
    donde, fragmento = loc
    palabras = []
    for e, psm, p in con_cajas:
        for w, c, x0, y0, x1, y1 in p:
            if c < E.CONF_OCR or not E.RE_PALABRA_OCR.search(w):
                continue
            (X0, Y0), (X1, Y1) = a_l0(x0, y0), a_l0(x1, y1)
            palabras.append({"escala": e, "psm": psm, "confianza": round(c, 1), "longitud": len(w),
                             "tipo": _clase_cadena(w), "donde": donde(X0, Y0, X1, Y1),
                             "fragmento_mm2": fragmento(X0, Y0, X1, Y1),
                             "caja_mm": [mm(X0), mm(Y0), mm(X1), mm(Y1)]})
    confs = sorted(h["confianza"] for h in palabras)
    det.update({"motivos": motivos, "n_palabras": len(palabras),
                "confianza_min_mediana_max": ([confs[0], confs[len(confs) // 2], confs[-1]]
                                              if confs else None),
                "donde": dict(collections.Counter(h["donde"] for h in palabras)),
                "palabras": palabras})
    return det


def _cristal_tiff(base, op, fichero, loc, mpp0):
    """({fuente: detalle}, {nivel: coherencia}, motivos) del TIFF, como `_motivos_cristal_tiff`:
    OCR + Puerta + trazos del ×8 y cada nivel menor, OCR + Puerta del ×4 entero y coherencia de
    cada nivel mayor que el ×8 con el ×8 (sus teselas decodificadas en estricto). Una tesela que no
    decodifica limpia no es texto en el cristal: queda en la coherencia como «error» (y la copia
    TIFF no saldría por exporta_n1). Igual el RELLENO de tesela (2-oct-26): si exporta_n1 lo rehúsa
    (`_comprueba_sin_relleno`, sin paso 1-bis), queda como `coherencia["relleno"]` con su «error».
    Cada nivel guarda además las ventanas de la coherencia que pasan el umbral (hasta
    TOPE_VENTANAS_1BIS, y cuántas son): lo que enseña la hoja del paso 1-bis. Replica la decisión
    del CRISTAL, no las reglas de forma de la copia (tags, tesela canónica, 262 frente a los ids de
    componente: FOTOMÉTRICA de exporta_n1), que aplica `copia_tiff_n1` al sacarla. Y lo que la
    Puerta no ve tampoco lo ve esto: un rótulo pequeño pintado en un solo nivel o solo en el croma
    sale limpio (cotas en los LÍMITES DECLARADOS de exporta_n1)."""
    import exporta_n1 as E
    t = E.Tiff(os.path.join(base, fichero))
    try:
        k8 = E._nivel_x8(t)
        if k8 is None:
            raise RuntimeError("%s: sin nivel ×8" % op)
        factores = E._comprueba_piramide(t)
        lado8 = max(t.enteros(t.ifds[k8], 256)[0], t.enteros(t.ifds[k8], 257)[0])
        fuentes, coherencia, motivos, img8 = {}, {}, [], None
        try:
            E._comprueba_sin_relleno(t)                # la misma decisión que la copia N1
        except E.PuertaCerrada as e:
            coherencia["relleno"] = {"error": str(e)[:200], "salta": None}
        for k, ifd in enumerate(t.ifds):
            if max(t.enteros(ifd, 256)[0], t.enteros(ifd, 257)[0]) <= lado8:
                img = E._imagen_de_nivel(t, k)
                img8 = img if k == k8 else img8
                f = factores[k]
                d = _revisa_imagen(img, E.ESCALAS_X8, True, lambda x, y, f=f: (x * f, y * f), loc, mpp0)
                fuentes["IFD%d_x%d" % (k, f)] = d
                motivos += ["IFD %d: %s" % (k, m) for m in d["motivos"]]
        for k in range(k8):
            den = factores[k8] // factores[k]
            if den not in (2, 4, 8):
                raise RuntimeError("%s: IFD %d a ×%d del ×8" % (op, k, den))
            clave = "IFD%d_x%d" % (k, factores[k])
            try:
                # La MISMA cuenta que la Puerta (`_coherencia_nivel`): todo el nivel, ventanas
                # parciales del borde incluidas, y PuertaCerrada si el ×8 no lo cubre (2-oct-26).
                dif, malas = E._coherencia_nivel_detalle(t, k, den, img8)
                coherencia[clave] = {"diferencia": dif, "umbral": E.UMBRAL_COHERENCIA,
                                     "salta": dif > E.UMBRAL_COHERENCIA, "n_ventanas": len(malas),
                                     "ventanas": [[k] + list(m) for m in malas[:E.TOPE_VENTANAS_1BIS]]}
                if dif > E.UMBRAL_COHERENCIA:
                    motivos.append("IFD %d: reducido no se parece al ×8 (diferencia %d; umbral %d)"
                                   % (k, dif, E.UMBRAL_COHERENCIA))
            except E.PuertaCerrada as e:
                coherencia[clave] = {"error": str(e)[:200], "salta": None}
            if den == 2:                                         # el ×4: OCR entero, sin trazos
                f = factores[k]
                img4, rehusa = _imagen_sin_tope(E, t, k)
                d = _revisa_imagen(img4, E.ESCALAS_X8, False, lambda x, y, f=f: (x * f, y * f),
                                   loc, mpp0)
                del img4
                if _rehusa_exporta(E, rehusa):
                    d["exporta_n1_rehusa"] = rehusa
                fuentes[clave + "_entero"] = d
                motivos += ["IFD %d (×4): %s" % (k, m) for m in d["motivos"]]
    finally:
        t.f.close()
    return fuentes, coherencia, motivos


def _rehusa_exporta(E, aviso):
    """¿Rehusaría `E` (exporta_n1) la copia TIFF por el tamaño del ×4? Un exporta_n1 que lee el
    ×4 con el tope de Pillow revienta (no es PuertaCerrada) y la copia no sale. Desde 239b28a lo
    lee por teselas (`lecturas_ocr_nivel`) y ya no lo rehúsa: no se anota (2-oct). La revisión
    del cristal lo mira entero en los dos casos."""
    return bool(aviso) and not hasattr(E, "lecturas_ocr_nivel")


def _imagen_sin_tope(E, t, k):
    """(imagen del nivel `k` como la lee exporta_n1, aviso o None). Un ×4 de una lámina grande (el
    hueso: ~478 Mpx) pasa del doble de `Image.MAX_IMAGE_PIXELS` y Pillow lanza
    DecompressionBombError: exporta_n1 no podría revisarlo (ni sacar el TIFF), pero la revisión
    del cristal tiene que mirarlo igual; se levanta el tope solo para esta lectura."""
    from PIL import Image
    tope = Image.MAX_IMAGE_PIXELS
    px = t.enteros(t.ifds[k], 256)[0] * t.enteros(t.ifds[k], 257)[0]
    if not tope or px <= 2 * tope:
        return E._imagen_de_nivel(t, k), None
    Image.MAX_IMAGE_PIXELS = None
    try:
        return E._imagen_de_nivel(t, k), ("Pillow DecompressionBombError: %d Mpx > %d Mpx"
                                          % (px // 10 ** 6, 2 * tope // 10 ** 6))
    finally:
        Image.MAX_IMAGE_PIXELS = tope


def cristal(base, op):
    """Texto en el cristal, r5 (ver REVISION_CRISTAL): la decisión de la Puerta de N1 sobre el
    TIFF (×8 y menores con OCR + Puerta + trazos, rojo incluido; ×4 entero con OCR + Puerta;
    coherencia de L0 y ×4 con el ×8) y sobre los dos PNG de revisión (OCR a ×4/×2/×1/×0,5 +
    Puerta + trazos). `n1_apta` = ningún motivo de texto o trazos; la coherencia, aparte
    (`coherencia_salta`; la exige exporta_n1 al sacar el TIFF). `motivos_tiff` es lo que diría
    `_motivos_cristal_tiff`. Nunca guarda el texto leído: por palabra, su confianza, longitud,
    tipo y dónde cae; por imagen, píxeles de trazo y diferencia de niveles."""
    from PIL import Image, ImageDraw, ImageFont
    import exporta_n1 as E
    t0 = time.time()
    E.puerta.exigir_diccionario()                       # con Puerta: fail-closed
    # Controles positivos dentro de la jaula: un rótulo y un trazo rojo sintéticos TIENEN que
    # saltar; si no, el OCR o el detector no funcionan aquí y «limpio» no significaría nada.
    ctl = Image.new("RGB", (1600, 600), (244, 244, 242))
    try:
        fuente = ImageFont.load_default(size=120)
    except TypeError:
        fuente = ImageFont.load_default()
    ImageDraw.Draw(ctl).text((80, 200), "CONTROL 2468", fill=(30, 30, 30), font=fuente)
    if not E.revisar_cristal(ctl, (1.0, 0.5)):
        raise RuntimeError("el control positivo del OCR no salta dentro de la jaula")
    ctl = Image.new("RGB", (800, 300), (232, 233, 234))
    ImageDraw.Draw(ctl).line([(50, 150), (750, 150)], fill=(200, 30, 40), width=12)
    n, umbral = E.trazos_de_rotulador(ctl)
    if n < umbral:
        raise RuntimeError("el control positivo del trazo rojo no salta dentro de la jaula")
    man = C.lee_manifiesto(base)
    ent = man["laminas"][op]
    mpp0 = float(ent["mpp"])
    loc = _localizador(base, op, mpp0)
    fuentes, coherencia, motivos_tiff = _cristal_tiff(base, op, ent["fichero"], loc, mpp0)
    rev = ent.get("revision", {})
    for clave in ("miniatura", "campo_x8"):
        if clave not in rev:
            raise RuntimeError("%s: falta el PNG de revisión «%s» (paso lamina)" % (op, clave))
        r = rev[clave]
        ox, oy, paso = float(r.get("x_l0", 0)), float(r.get("y_l0", 0)), r["mpp"] / mpp0
        img = Image.open(os.path.join(base, r["fichero"])).convert("RGB")
        d = _revisa_imagen(img, E.ESCALAS_PNG, True,
                           lambda x, y, ox=ox, oy=oy, p=paso: (ox + x * p, oy + y * p), loc, mpp0)
        fuentes["png_" + clave] = d
    motivos = ["%s: %s" % (nombre, m) for nombre, d in fuentes.items() for m in d["motivos"]]
    salta = bool(motivos)
    coh_salta = any(c.get("salta") for c in coherencia.values())
    coh_error = any("error" in c for c in coherencia.values())
    rehusa = [d["exporta_n1_rehusa"] for d in fuentes.values() if d.get("exporta_n1_rehusa")]
    info = {"version": REVISION_CRISTAL, "motivos": motivos or "limpio", "salta": salta,
            "coherencia_salta": coh_salta, "coherencia_error": coh_error,
            "motivos_tiff": motivos_tiff or "limpio",
            "tiff_exporta_n1_rehusa": rehusa or None,
            "tiff_sin_1bis": not motivos_tiff and not coh_error and not rehusa,
            "fuentes": fuentes, "coherencia": coherencia,
            "control_positivo": "OCR y trazo rojo saltan (ok)",
            "exporta_n1_sha256": _version_exporta(largo=True),
            "tiff_sha256": _sha256(os.path.join(base, ent["fichero"])),
            "n1_apta_previa": ent.get("n1_apta"),
            "segundos": round(time.time() - t0, 1),
            "metodo": "la Puerta de N1 de exporta_n1 (tercera pasada): tesseract --psm 3 y 11, "
                      "palabras de confianza ≥60 y ≥4 alfanuméricos + lo leído por la Puerta; TIFF "
                      "leído por sus teselas: ×8 y menores a 1/0,5/0,25 con trazos (verde-cian, azul, "
                      "negro y rojo S≥200), ×4 entero sin trazos, coherencia de L0 y ×4 reducidos "
                      "con el ×8 (ventana 16 px, umbral %d); PNG a 4/2/1/0,5 con trazos"
                      % E.UMBRAL_COHERENCIA}

    def fn(m):
        m["laminas"][op]["cristal"] = info
        m["laminas"][op]["n1_apta"] = not salta
    C.actualiza_manifiesto(base, fn)
    return info


def _imprime_cristal(op, r):
    """Una línea por lámina y una por fuente, solo con números y clases (ni una cadena leída)."""
    _imprime("%-10s cristal %s: %s · n1_apta %s → %s · coherencia %s · TIFF %s · %.0f s" % (
        op, r["version"], "SALTA" if r["salta"] else "limpio", r["n1_apta_previa"],
        not r["salta"], "SALTA" if r["coherencia_salta"] else ("ERROR" if r["coherencia_error"]
                                                               else "pasa"),
        "sale sin 1-bis" if r["tiff_sin_1bis"] else "pide 1-bis o no sale", r["segundos"]))
    for nombre, d in r["fuentes"].items():
        tz = d.get("trazos")
        _imprime("   · %-16s %5dx%-5d palabras %d conf %s dónde %s · Puerta %s · trazos %s%s" % (
            nombre, d["ancho"], d["alto"], d["n_palabras"], d["confianza_min_mediana_max"],
            d["donde"] or "—", d["capas_puerta"] or "—",
            "%d/%d (rojo %d)%s" % (tz["px"], tz["umbral"], tz["rojo_px"],
                                    " SALTA" if tz["salta"] else "") if tz else "no mira",
            " · exporta_n1 lo rehúsa (%s)" % d["exporta_n1_rehusa"]
            if d.get("exporta_n1_rehusa") else ""))
    for nombre, c in r["coherencia"].items():
        _imprime("   · %-16s coherencia con el ×8: %s" % (
            nombre, "%d/%d · %d ventana(s) sobre el umbral%s" % (
                c["diferencia"], c["umbral"], c.get("n_ventanas", 0), " SALTA" if c["salta"] else "")
            if "diferencia" in c else "ERROR " + c["error"]))


# ══ (c) pyvips frente a OpenSlide (venv valis) ══════════════════════════════════════════════
def prueba_vips(base, op):
    import numpy as np
    import pyvips
    man = C.lee_manifiesto(base)
    ent = man["laminas"][op]
    ruta = os.path.join(base, ent["fichero"])
    pl = ent["prueba_lector"]
    rel = pl["c_recortes"]
    if _sha256(os.path.join(base, rel)) != pl["c_recortes_sha256"]:
        raise ValueError("%s: recortes de (c) con sha256 distinto" % op)
    with np.load(os.path.join(base, rel)) as d:
        recortes, coords, ds8 = d["recortes"], d["coords"], float(d["ds8"])
    n_pag = pyvips.Image.new_from_file(ruta, access="random").get("n-pages")
    anchos = [pyvips.Image.tiffload(ruta, page=p).width for p in range(n_pag)]
    w0 = max(anchos)
    pag = [p for p, w in enumerate(anchos) if abs(w0 / float(w) - 8.0) <= 0.08]
    if not pag:
        raise ValueError("%s: pyvips no ve un nivel ×8" % op)
    im = pyvips.Image.tiffload(ruta, page=pag[0], access="random")
    difs, fracc = [], 0
    for rec, (x, y) in zip(recortes, coords):
        xs, ys = x / ds8, y / ds8
        fracc += (abs(xs - round(xs)) > 1e-6) or (abs(ys - round(ys)) > 1e-6)
        x8, y8 = int(round(xs)), int(round(ys))
        w = min(rec.shape[1], im.width - x8)
        h = min(rec.shape[0], im.height - y8)
        v = np.ndarray(buffer=im.crop(x8, y8, w, h).write_to_memory(), dtype=np.uint8,
                       shape=(h, w, im.bands))[..., :3]
        difs.append(int(np.abs(rec[:h, :w].astype(np.int16) - v.astype(np.int16)).max()))
    difs = np.array(difs)
    info = {"c_dif_max": int(difs.max()), "c_dif_p99": float(np.percentile(difs, 99)),
            "c_recortes_dif_gt2": int((difs > 2).sum()), "c_ok": bool((difs <= 2).all()),
            "c_n": int(len(difs)), "c_origen_fraccionario": int(fracc),
            "c_lector": "pyvips %s (libvips %d.%d) tiffload página %d" % (
                pyvips.__version__, pyvips.version(0), pyvips.version(1), pag[0])}

    def fn(m):
        m["laminas"][op].setdefault("prueba_lector", {}).update(info)
    C.actualiza_manifiesto(base, fn)
    return info


# ══ F1.3 eslabón 1: huella por máscaras a 32 µm/px ══════════════════════════════════════════
def _mascara32(base, op):
    """Máscara principal de tejido a ~32 µm/px (la de `tejido`), sin objetos < 0,02 mm²,
    recortada a su caja."""
    import numpy as np
    from scipy import ndimage as ndi
    with np.load(os.path.join(base, "mascaras", op + "-32um.npz")) as z:
        m = z["mascara"].astype(bool)
        mpp = float(z["mpp"])
    lab, n = ndi.label(m, structure=np.ones((3, 3)))
    if n:
        tam = np.bincount(lab.ravel())
        tam[0] = 0
        m = tam[lab] * (mpp / 1000.0) ** 2 >= 0.02
    ys, xs = np.nonzero(m)
    if len(ys) == 0:
        return m[:1, :1]
    return m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def _rota(m, ang):
    import numpy as np
    from scipy import ndimage as ndi
    if ang % 360 == 0:
        return m.astype(np.float32)
    return (ndi.rotate(m.astype(np.float32), ang, reshape=True, order=1, mode="constant") > 0.5
            ).astype(np.float32)


def _en_lienzo(m, S):
    import numpy as np
    out = np.zeros((S, S), np.float32)
    h, w = m.shape
    y0, x0 = (S - h) // 2, (S - w) // 2
    out[y0:y0 + h, x0:x0 + w] = m
    return out


def _mejor_iou(FA, nA, B, S):
    import numpy as np
    from scipy import fft
    Bc = _en_lienzo(B, S)
    nB = float(Bc.sum())
    inter = fft.irfft2(FA * np.conj(fft.rfft2(Bc, workers=-1)), s=(S, S), workers=-1)
    iou = inter / np.maximum(nA + nB - inter, 1e-9)
    return float(iou.max())


def busca_par(A, B, paso=E1_PASO_GRUESO, fino=E1_PASO_FINO):
    """Mejor IoU de B sobre A en rotación (y traslación por FFT), directo y en espejo (volteo
    horizontal de B). Devuelve dict con iou, ángulo y los mismos del espejo."""
    import numpy as np
    from scipy import fft
    R = int(math.ceil(max(math.hypot(*A.shape), math.hypot(*B.shape)) / 2.0)) + 2
    S = fft.next_fast_len(4 * R)
    Ac = _en_lienzo(A.astype(np.float32), S)
    FA = fft.rfft2(Ac, workers=-1)
    nA = float(Ac.sum())
    res = {}
    for espejo in (False, True):
        Bm = np.fliplr(B) if espejo else B
        angs = np.arange(0, 360, paso)
        vals = [_mejor_iou(FA, nA, _rota(Bm, a), S) for a in angs]
        k = int(np.argmax(vals))
        finos = np.arange(angs[k] - paso, angs[k] + paso + 1e-9, fino)
        vf = [_mejor_iou(FA, nA, _rota(Bm, a), S) for a in finos]
        kf = int(np.argmax(vf))
        res["espejo" if espejo else "directo"] = {"iou": round(vf[kf], 4),
                                                  "angulo": round(float(finos[kf]) % 360, 2)}
    return res


def _envuelve(a):
    return (a + 180.0) % 360.0 - 180.0


def evalua_grupo(pares, ops):
    """Aplica la regla del plan a los resultados por par. `pares[(a, b)]` con a<b en el orden
    de `ops`. Devuelve (por_lamina, cierres)."""
    import itertools
    pasa_par = {}
    for (a, b), r in pares.items():
        d, e = r["directo"]["iou"], r["espejo"]["iou"]
        pasa_par[(a, b)] = d >= E1_IOU and (d - e) >= E1_MARGEN
    cierres = []
    for a, b, c in itertools.combinations(ops, 3):
        if pasa_par.get((a, b)) and pasa_par.get((b, c)) and pasa_par.get((a, c)):
            t = (pares[(a, b)]["directo"]["angulo"] + pares[(b, c)]["directo"]["angulo"]
                 - pares[(a, c)]["directo"]["angulo"])
            cierres.append(((a, b, c), abs(_envuelve(t))))
    por = {}
    for o in ops:
        socios = [p for p in pares if o in p and pasa_par[p]]
        cie = [v for trio, v in cierres if o in trio]
        margenes = [pares[p]["directo"]["iou"] - pares[p]["espejo"]["iou"] for p in socios]
        cierre_max = max(cie) if cie else None
        pasa = len(socios) >= 2 and cierre_max is not None and cierre_max <= E1_CIERRE
        ious = [pares[p]["directo"]["iou"] for p in pares if o in p]
        ious_e = [pares[p]["espejo"]["iou"] for p in pares if o in p]
        por[o] = {"pasa": pasa, "socios_que_pasan": len(socios),
                  "iou_directo_rango": [min(ious), max(ious)] if ious else None,
                  "iou_espejo_rango": [min(ious_e), max(ious_e)] if ious_e else None,
                  "margen_minimo_en_socios": round(min(margenes), 4) if margenes else None,
                  "margen_estrecho": bool(margenes) and min(margenes) < E1_MARGEN_ESTRECHO,
                  "cierre_max_grados": round(cierre_max, 2) if cierre_max is not None else None}
    return por, cierres, pasa_par


def eslabon1(base):
    import itertools
    import numpy as np
    t0 = time.time()
    masc = {o: _mascara32(base, o) for o in OPACOS}
    pares = {}
    for a, b in itertools.combinations(IHQ, 2):
        pares[(a, b)] = busca_par(masc[a], masc[b])
        r = pares[(a, b)]
        _imprime("%-9s ↔ %-9s IoU %.3f (%.1f°) · espejo %.3f (%.1f°)" % (
            a, b, r["directo"]["iou"], r["directo"]["angulo"], r["espejo"]["iou"],
            r["espejo"]["angulo"]))
    por, cierres, pasa_par = evalua_grupo(pares, IHQ)
    hueso = busca_par(masc["B-HE-1"], masc["B-HE-2"])
    d, e = hueso["directo"]["iou"], hueso["espejo"]["iou"]
    hueso_pasa = d >= E1_IOU and (d - e) >= E1_MARGEN
    _imprime("B-HE-1 ↔ B-HE-2 IoU %.3f (%.1f°) · espejo %.3f → %s" % (
        d, hueso["directo"]["angulo"], e, "pasa" if hueso_pasa else "no pasa"))
    he_ihq = {}
    for o in IHQ:
        he_ihq[o] = busca_par(masc[o], masc["P-HE"])
        r = he_ihq[o]
        _imprime("P-HE ↔ %-9s IoU %.3f (%.1f°) · espejo %.3f (informativo)" % (
            o, r["directo"]["iou"], r["directo"]["angulo"], r["espejo"]["iou"]))
    vc = [v for _, v in cierres]
    resumen = {
        "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"), "segundos": round(time.time() - t0, 1),
        "regla": {"mpp": MPP_HUELLA, "mascara": "principal de `tejido` (histéresis), por "
                  "mayoría a 32 µm/px, sin objetos < 0,02 mm²", "iou_min": E1_IOU,
                  "margen_sobre_espejo": E1_MARGEN, "frente_a": "≥2 del grupo",
                  "cierre_max_grados": E1_CIERRE, "margen_estrecho": E1_MARGEN_ESTRECHO,
                  "busqueda": "rotación %.1f° y refinado %.2f°, traslación por correlación FFT, "
                              "espejo = volteo horizontal" % (E1_PASO_GRUESO, E1_PASO_FINO),
                  "declarado": "umbral fijado tras el reconocimiento (plan F1.3)"},
        "pares": {"%s|%s" % k: dict(v, pasa=pasa_par[k]) for k, v in pares.items()},
        "por_lamina": por,
        "cierres": {"n_triadas": len(vc), "max": round(max(vc), 2) if vc else None,
                    "mediana": round(float(np.median(vc)), 2) if vc else None},
        "hueso": dict(hueso, pasa=hueso_pasa),
        "he_ihq_informativo": he_ihq}
    os.makedirs(os.path.join(base, "identidad"), mode=0o700, exist_ok=True)
    rel = os.path.join("identidad", "eslabon1.json")
    C.escribe_json(os.path.join(base, rel), resumen)

    def fn(man):
        man["eslabon1"] = {"fichero": rel, "sha256": _sha256(os.path.join(base, rel)),
                           "regla": resumen["regla"], "cierres": resumen["cierres"]}
        for o in IHQ:
            man["laminas"][o]["huella_ihq"] = por[o]
        man["laminas"]["B-HE-2"]["huella_hueso"] = {"frente_a": "B-HE-1", "pasa": hueso_pasa,
                                                    "iou_directo": d, "iou_espejo": e}
    C.actualiza_manifiesto(base, fn)
    n_pasa = sum(v["pasa"] for v in por.values())
    _imprime("eslabón 1: %d/%d IHQ pasan; cierre máx %s°, mediana %s° en %d tríadas" % (
        n_pasa, len(IHQ), resumen["cierres"]["max"], resumen["cierres"]["mediana"], len(vc)))
    for o in IHQ:
        p = por[o]
        _imprime("  %-9s %s · socios %d · IoU %s · espejo %s · margen mín %s%s · cierre máx %s°" % (
            o, "PASA" if p["pasa"] else "no pasa", p["socios_que_pasan"], p["iou_directo_rango"],
            p["iou_espejo_rango"], p["margen_minimo_en_socios"],
            " (narrow margin)" if p["margen_estrecho"] else "", p["cierre_max_grados"]))
    return resumen


# ══ F1.2: atribución y tanda ════════════════════════════════════════════════════════════════
TANDA = None          # la fija `atribucion` con lo que se haya podido ver; ver F1.2


def atribucion(base, tanda):
    man = C.lee_manifiesto(base)
    ide = {}
    if os.path.isfile(IDENTIDAD_HE):
        with open(IDENTIDAD_HE, encoding="utf-8") as f:
            ide = json.load(f).get("laminas", {})

    def fn(m):
        m["tanda"] = tanda
        for op in OPACOS:
            ent = m["laminas"][op]
            niveles = []
            if op in IHQ:
                h = ent.get("huella_ihq", {})
                if h.get("pasa"):
                    niveles.append("huella-IHQ (mismo bloque que ≥2 IHQ%s)" % (
                        "; narrow margin" if h.get("margen_estrecho") else ""))
                else:
                    niveles.append("sin huella-IHQ: no registrable con el grupo (sola y rotulada)")
                if ent.get("nombre_original_con_pii", {}).get("nombre_titular"):
                    niveles.append("nombre-de-fichero (nombre de la titular%s)" % (
                        ", con errata" if any("nombre" in e for e in ent.get("erratas", []))
                        else ""))
                niveles.append("enlace a la accesión de P-HE: pendiente del eslabón 2 (piloto)")
            else:
                r = ide.get(op, {})
                if r.get("casa_exacta_con_identidad"):
                    niveles.append("nombre-de-fichero: accesión casa con el informe AP original "
                                   "(identidad verificada)")
                elif r.get("casa_exacta"):
                    niveles.append("nombre-de-fichero: accesión casa con el informe AP original; "
                                   "identidad del informe NO acreditada por identidad_paciente "
                                   "(veredicto %s): pendiente del cotejo de {{TITULAR}} (paso 3)"
                                   % "/".join(r.get("identidad_de_esos_informes") or ["?"]))
                elif op == "B-HE-2":
                    hh = ent.get("huella_hueso", {})
                    if hh.get("pasa"):
                        niveles.append("huella frente a B-HE-1 (mismo método que el eslabón 1); "
                                       "accesión del nombre con errata (distancia %s)"
                                       % r.get("distancia_minima"))
                    else:
                        niveles.append("no atribuible")
                else:
                    niveles.append("no atribuible por accesión (no casa con ningún informe AP "
                                   "abierto)")
                if op == "P-HE":
                    niveles.append("huella-H&E: pendiente del eslabón 2 (piloto)")
            ent["nivel_atribucion"] = niveles
    C.actualiza_manifiesto(base, fn)
    man = C.lee_manifiesto(base)
    for op in OPACOS:
        _imprime("%-10s %s" % (op, " | ".join(man["laminas"][op]["nivel_atribucion"])))
    return 0


# ══ entradas por jaula ══════════════════════════════════════════════════════════════════════
def _lista(args):
    ops = [a for a in args if a in OPACOS]
    malos = [a for a in args if a not in OPACOS]
    if malos:
        raise SystemExit("opacos desconocidos: %s" % ", ".join(malos))
    return ops or list(OPACOS)


def _paso(base, nombre, op, fn, productos=()):
    if C.esta_hecho(base, nombre, op):
        _imprime("%-10s %s: ya hecho" % (op, nombre))
        return None
    t = time.time()
    r = fn(base, op)
    C.marca_hecho(base, nombre, op, productos=[p for p in productos if
                                               os.path.isfile(os.path.join(base, p))])
    _imprime("%-10s %s: %.1f s" % (op, nombre, time.time() - t))
    return r


def main(args):
    """Jaula analisis-ingesta (SESION + ORIGEN)."""
    base = C.sesion()
    if not args:
        _imprime("pasos: inventario | copia | extrae | identidad | atribucion <tanda>")
        return 2
    paso = args[0]
    if paso == "inventario":
        return inventario(ZIP_DESCARGAS)
    if paso == "copia":
        return copia()
    if paso == "extrae":
        return extrae(base)
    if paso == "identidad":
        return identidad(base)
    if paso == "atribucion":
        tanda = TANDAS.get(args[1] if len(args) > 1 else "", None)
        if tanda is None:
            _imprime("tanda desconocida; válidas: %s" % ", ".join(TANDAS))
            return 2
        return atribucion(base, tanda)
    _imprime("paso desconocido")
    return 2


def main_qc(args):
    """Jaula analisis (solo SESION), venv patologia."""
    base = C.sesion()
    if not args:
        _imprime("pasos: lamina [OP…] | cristal [OP…] | eslabon1")
        return 2
    paso, resto = args[0], args[1:]
    if paso == "lamina":
        for op in _lista(resto):
            _imprime("── %s" % op)
            r = _paso(base, "metadatos", op, lambda b, o: metadatos(b, o)[1])
            z = _paso(base, "zona", op, zona, [os.path.join("zona", op + ".geojson"),
                                               os.path.join("zona", op + "-teselas.npz")])
            if z:
                _imprime("%-10s zona escaneada %.2f mm² (%d teselas; relleno %.0f %%; %s)" % (
                    op, z["mm2"], z["teselas_escaneadas"], 100 * z["fraccion_relleno"],
                    z["confirmacion"]))
            r = _paso(base, "i0-v2", op, i0, [os.path.join("i0", op + ".npz")])
            if r:
                _imprime("%-10s I0 vidrio %s; franja %s (col %.1f / fila %.1f)" % (
                    op, r["vidrio_referencia_rgb"], r["franja"], r["amplitud_franja_columnas"],
                    r["amplitud_franja_filas"]))
            r = _paso(base, "tejido-v2", op, tejido, [os.path.join("mascaras", op + "-8um.npz"),
                                                   os.path.join("mascaras", op + "-32um.npz")])
            if r:
                p = r["principal"]
                _imprime("%-10s tejido (principal, bajo %.3f/alto %.2f/textura %.3f): %.2f mm², "
                         "%d fragmentos >0,2 mm² %s" % (op, r["umbral_bajo"], r["umbral_alto"],
                                                       r["umbral_textura"], p["mm2"],
                                                       p["fragmentos_gt_0_2mm2"],
                                                       p["mm2_fragmentos"][:6]))
                _imprime("%-10s sensibilidad (media ODsum): %s" % (op, " · ".join(
                    "%s→%.2f mm²/%d" % (T, e["mm2"], e["fragmentos_gt_0_2mm2"])
                    for T, e in r["por_umbral_media_odsum"].items())))
            r = _paso(base, "lector", op, prueba_lector,
                      [os.path.join("lector", op + "-x8-openslide.npz")])
            if r:
                _imprime("%-10s lector (a) %s · (b) dif máx %d, >2 en %d teselas" % (
                    op, r["a_sha256_identico"], r["b_dif_max"], r["b_teselas_dif_gt2"]))
            _paso(base, "revision-v3", op, revision, [os.path.join("revision", op + "__thumbnail.png"),
                                                   os.path.join("revision", op + "__x8.png")])
            import laminillas_lector as L
            L.cierra_todo()
        return 0
    if paso == "cristal":
        for op in _lista(resto):
            # El «hecho» depende de la versión de exporta_n1 (su OCR y su Puerta) y de la de esta
            # revisión: si cambia cualquiera, se repite y `n1_apta` se recalcula.
            r = _paso(base, "cristal-%s-%s" % (REVISION_CRISTAL, _version_exporta()), op, cristal)
            if r:
                _imprime_cristal(op, r)
        return 0
    if paso == "eslabon1":
        eslabon1(base)
        return 0
    if paso == "diag":
        for op in _lista(resto):
            diag(base, op)
        return 0
    if paso == "resumen":
        resumen(base)
        return 0
    if paso == "rojo":
        for op in _lista(resto):
            rojo(base, op)
        return 0
    if paso == "cristal-detalle":
        cristal_detalle(base, _lista(resto))
        return 0
    if paso == "coherencia":
        for op in _lista(resto):
            coherencia_detalle(base, op)
        return 0
    if paso == "hoja-1bis":
        import laminillas_exporta as X
        return X.imprime_hoja_1bis(base, _lista(resto))
    _imprime("paso desconocido")
    return 2


# ── detalle del cristal (sin imprimir NINGUNA cadena leída) ─────────────────────────────────
# Mismos parámetros que la revisión que saltó (exporta_n1 de 9d13001): tesseract psm 3 y 11,
# confianza ≥60, palabra = ≥4 alfanuméricos seguidos; ×8 a escalas 1/0,5/0,25 y PNG a 2/0,5,
# en trozos de 6000 px con solape de 1200.
_RE_PALABRA = re.compile(r"[A-Za-z0-9]{4,}")
_MESES_ES = ("enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|"
             "noviembre|diciembre")
_MESES_EN = ("January|February|March|April|May|June|July|August|September|October|November|"
             "December")
# RE_FECHA y RE_TELEFONO tal cual en puerta_n1 de 9d13001 (respaldo si la del árbol no importa).
_RE_FECHA = re.compile(
    r"(?<!\d)\d{4}-(?:0?[1-9]|1[0-2])-(?:0?[1-9]|[12]\d|3[01])(?!\d)"
    r"|(?<![\d.,/-])(?:0?[1-9]|[12]\d|3[01])([/.\-])(?:0?[1-9]|1[0-2])\1"
    r"(?:19\d\d|20\d\d|\d{2})(?![\d])"
    r"|(?i:\b\d{1,2}\s+de\s+(?:%s)\b)" % _MESES_ES
    + r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:%s)\b" % _MESES_EN
    + r"|\b(?:%s)\s+\d{1,2}(?:st|nd|rd|th)?\b(?:,?\s+\d{4})?" % _MESES_EN)
_RE_TELEFONO = re.compile(r"\+\d(?:[\s.\-]?\d){7,}|(?<!\d)[6-9]\d{8}(?!\d)")
_CAPAS_ABORTA = frozenset({"diccionario", "regex:campo_nombre", "regex:email", "regex:dni",
                           "regex:nhc", "regex:nombre_titular", "lista:nombres"})


def _ocr_cajas(img, psm):
    """[(texto, conf, izq, arr, ancho, alto)] de tesseract (TSV), sin umbral."""
    import io as _io
    buf = _io.BytesIO()
    img.save(buf, "PNG")
    p = subprocess.run(["tesseract", "stdin", "stdout", "--psm", str(psm), "tsv"],
                       input=buf.getvalue(), capture_output=True, timeout=900)
    if p.returncode != 0:
        raise RuntimeError("tesseract rc=%d" % p.returncode)
    out = []
    for ln in p.stdout.decode("utf-8", "replace").splitlines()[1:]:
        c = ln.split("\t")
        if len(c) >= 12 and c[11].strip():
            try:
                out.append((c[11].strip(), float(c[10]), int(c[6]), int(c[7]), int(c[8]),
                            int(c[9])))
            except ValueError:
                continue
    return out


def _capas_pii(texto):
    """Nombres de capa (nunca el texto) por los que la Puerta abortaría. Primero la puerta_n1
    del árbol; si no importa, deid.detectar + _RE_CODIGO_AP + fecha/teléfono de 9d13001."""
    capas, via = set(), None
    try:
        import puerta_n1
        capas |= {c for c, _ in puerta_n1.motivos(texto)}
        via = "puerta_n1.motivos (árbol de trabajo)"
    except Exception as e:                                    # noqa: BLE001
        via = "respaldo (puerta_n1 no importa: %s)" % type(e).__name__
        import deid
        import caso_publico
        capas |= {c for _a, _b, c in deid.detectar(texto) if c in _CAPAS_ABORTA}
        if caso_publico._RE_CODIGO_AP.search(texto):
            capas.add("codigo_ap")
        if _RE_FECHA.search(texto):
            capas.add("fecha")
        if _RE_TELEFONO.search(texto):
            capas.add("telefono")
    return sorted(capas), via


def _clase_cadena(t):
    al = re.sub(r"[^A-Za-z0-9]", "", t)
    if al.isalpha():
        return "solo letras"
    if al.isdigit():
        return "solo cifras"
    return "mezcla"


def cristal_detalle(base, ops):
    """Por qué saltó la revisión del cristal, sin guardar ni imprimir ninguna cadena leída:
    nº de palabras y confianza, dónde caen (tejido / borde ≤200 µm / vidrio / relleno) con su caja
    en mm desde el origen de L0, longitud y tipo de cada cadena, capas de PII de la Puerta y
    trazos de rotulador con su localización. No toca `n1_apta`. Es el diagnóstico de la revisión
    r1-r3 (parámetros de 9d13001); la r5 (`cristal`) ya guarda este detalle con la Puerta vigente."""
    import numpy as np
    from PIL import Image
    import laminillas_lector as L
    man = C.lee_manifiesto(base)
    salida = {"fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "parametros": "tesseract psm 3 y 11, conf ≥60, ≥4 alfanuméricos; ×8 a 1/0,5/0,25, "
                            "PNG a 2/0,5; trozos 6000/1200 (como la revisión que saltó)",
              "laminas": {}}
    for op in ops:
        lam = L.abre(op)
        mpp0 = lam.mpp_l0
        donde, fragmento = _localizador(base, op, mpp0)

        def mm(v):
            return round(v * mpp0 / 1000.0, 3)
        fuentes = [("x8_completo", Image.fromarray(L.lee_nivel(lam, 8)), (1.0, 0.5, 0.25),
                    0.0, 0.0, 8.0)]
        rev = man["laminas"][op].get("revision", {})
        for clave in ("miniatura", "campo_x8"):
            if clave in rev:
                r = rev[clave]
                fuentes.append(("png_" + clave, Image.open(os.path.join(base, r["fichero"])).convert(
                    "RGB"), (2.0, 0.5), float(r.get("x_l0", 0)), float(r.get("y_l0", 0)),
                    r["mpp"] / mpp0))
        hits, trazos = [], {}
        for nombre, img, escalas, ox, oy, paso in fuentes:
            for e in escalas:
                im = img if e == 1.0 else img.resize((max(1, int(img.width * e)),
                                                      max(1, int(img.height * e))), Image.LANCZOS)
                W_, H_ = im.size
                lado, solape = 6000, 1200
                if W_ <= lado and H_ <= lado:
                    trozos = [(0, 0)]
                else:
                    trozos = [(x, y) for y in range(0, max(1, H_ - solape), lado - solape)
                              for x in range(0, max(1, W_ - solape), lado - solape)]
                for tx, ty in trozos:
                    tro = im.crop((tx, ty, min(W_, tx + lado), min(H_, ty + lado)))
                    for psm in (3, 11):
                        for t, conf, l, a, w, h in _ocr_cajas(tro, psm):
                            if conf < 60 or not _RE_PALABRA.search(t):
                                continue
                            f = paso / e
                            x0, y0 = ox + (tx + l) * f, oy + (ty + a) * f
                            x1, y1 = x0 + w * f, y0 + h * f
                            capas, via = _capas_pii(t)
                            hits.append({"fuente": nombre, "escala": e, "psm": psm,
                                         "confianza": round(conf, 1), "longitud": len(t),
                                         "tipo": _clase_cadena(t), "donde": donde(x0, y0, x1, y1),
                                         "fragmento_mm2": fragmento(x0, y0, x1, y1),
                                         "caja_mm": [mm(x0), mm(y0), mm(x1), mm(y1)],
                                         "capas_pii": capas, "puerta": via})
            # Trazos de rotulador: mismos umbrales que exporta_n1 (9d13001), con localización.
            hsv = np.asarray(img.convert("RGB").convert("HSV"))
            hh, ss, vv = hsv[..., 0], hsv[..., 1], hsv[..., 2]
            mk = (((hh >= 42) & (hh < 142) & (ss >= 128) & (vv >= 50))
                  | ((hh >= 142) & (hh < 184) & (ss >= 192) & (vv >= 76))
                  | ((vv < 38) & (ss < 64)))
            n = int(mk.sum())
            umbral = max(50, int(0.0005 * img.width * img.height))
            info = {"px": n, "umbral": umbral, "salta": n >= umbral}
            if n:
                ys, xs = np.nonzero(mk)
                X, Y = ox + (xs + 0.5) * paso, oy + (ys + 0.5) * paso
                cls = collections.Counter(donde(x, y, x, y) for x, y in
                                          zip(X[:: max(1, n // 2000)], Y[:: max(1, n // 2000)]))
                info.update({"caja_mm": [mm(X.min()), mm(Y.min()), mm(X.max()), mm(Y.max())],
                             "donde_muestra": dict(cls)})
            trazos[nombre] = info
        confs = [h["confianza"] for h in hits]
        res = {"n_palabras": len(hits),
               "confianza_min_mediana_max": ([min(confs), float(np.median(confs)), max(confs)]
                                             if confs else None),
               "donde": dict(collections.Counter(h["donde"] for h in hits)),
               "capas_pii": sorted({c for h in hits for c in h["capas_pii"]}),
               "palabras": hits, "trazos": trazos,
               "n1_apta": man["laminas"][op].get("n1_apta")}
        salida["laminas"][op] = res
        L.cierra_todo()
        _imprime("%-9s %d palabra(s) · conf %s · dónde %s · PII %s · trazos %s" % (
            op, len(hits), res["confianza_min_mediana_max"], res["donde"],
            res["capas_pii"] or "ninguna",
            {k: ("SALTA" if v["salta"] else "no") + " %d/%d px" % (v["px"], v["umbral"])
             for k, v in trazos.items()}))
        for h in hits:
            _imprime("   · %s ×%s psm%d conf %.0f · %d car. %s · %s (fragmento %.2f mm²) · caja mm "
                     "%s · PII %s" % (h["fuente"], h["escala"], h["psm"], h["confianza"],
                                      h["longitud"], h["tipo"], h["donde"], h["fragmento_mm2"],
                                      h["caja_mm"], h["capas_pii"] or "ninguna"))
    C.escribe_json(os.path.join(base, "cristal_detalle.json"), salida)
    _imprime("→ SESION/cristal_detalle.json (sin cadenas leídas; n1_apta sin tocar)")
    return salida


# ── detalle de la coherencia de niveles (solo números; 2-oct-26) ───────────────────────────────
def _mapa_dif(a, b, ventana=16):
    """(D, A, B): diferencia máxima por canal entre las medias por ventana de `a` y `b` (lo que
    `exporta_n1.incoherencia` resume con su máximo), y las dos medias, como arrays."""
    import numpy as np
    from PIL import Image
    w, h = min(a.width, b.width), min(a.height, b.height)
    nw, nh = w // ventana, h // ventana
    caja = (0, 0, nw * ventana, nh * ventana)
    am = np.asarray(a.convert("RGB").crop(caja).resize((nw, nh), Image.BOX), dtype=np.int16)
    bm = np.asarray(b.convert("RGB").crop(caja).resize((nw, nh), Image.BOX), dtype=np.int16)
    return np.abs(am - bm).max(axis=2), am, bm


def coherencia_detalle(base, op):
    """Por qué un nivel mayor no se parece al ×8, solo con números (no toca `n1_apta`): forma del
    TIFF (fotométrica, ids y muestreo del SOF0 por nivel, teselas vacías), el ×8 por las dos vías
    de decodificación de la Puerta (Pillow y libjpeg-turbo), y por nivel la distribución de la
    diferencia por ventana, dónde caen las ventanas que pasan el umbral (tejido, borde, vidrio,
    relleno; caja en mm), sus colores medios en cada nivel y si un desplazamiento de ±3 px del ×8
    la arregla (desalineado) o no."""
    import numpy as np
    import exporta_n1 as E
    ent = C.lee_manifiesto(base)["laminas"][op]
    mpp0 = float(ent["mpp"])
    donde, _frag = _localizador(base, op, mpp0)
    t = E.Tiff(os.path.join(base, ent["fichero"]))
    try:
        k8 = E._nivel_x8(t)
        factores = E._comprueba_piramide(t)
        f8 = factores[k8]
        forma = []
        for k, ifd in enumerate(t.ifds):
            cnts = t.enteros(ifd, 325)
            sof = None
            for o, c in zip(t.enteros(ifd, 324), cnts):
                if c:
                    b = t.lee(o, c)
                    i = b.find(b"\xff\xc0")
                    nf = b[i + 9] if i >= 0 else 0
                    sof = [(b[i + 10 + 3 * j], b[i + 11 + 3 * j]) for j in range(nf)] if i >= 0 else None
                    break
            forma.append({"ifd": k, "factor": factores[k], "ancho": t.enteros(ifd, 256)[0],
                          "alto": t.enteros(ifd, 257)[0],
                          "fotometrica": t.enteros(ifd, 262)[0] if 262 in ifd else None,
                          "teselas": len(cnts), "vacias": sum(1 for c in cnts if not c),
                          "sof0_id_muestreo": sof})
        img8 = E._imagen_de_nivel(t, k8)
        out = {"forma": forma, "x8_pillow_vs_turbo": E.incoherencia(E._mosaico(t, k8, 1), img8)}
        for k in range(k8):
            den = f8 // factores[k]
            m = E._mosaico(t, k, den)
            D, A, B = _mapa_dif(m, img8)
            malas = np.argwhere(D > E.UMBRAL_COHERENCIA)
            cls = collections.Counter()
            for i, j in malas[:: max(1, len(malas) // 2000)]:
                x, y = (j + 0.5) * 16 * f8, (i + 0.5) * 16 * f8
                cls[donde(x, y, x, y)] += 1

            def mm(v):
                return round(float(v) * 16 * f8 * mpp0 / 1000.0, 2)
            mejor = None
            for dy in range(-3, 4):
                for dx in range(-3, 4):
                    a = m.crop((max(0, dx), max(0, dy), m.width, m.height))
                    b = img8.crop((max(0, -dx), max(0, -dy), img8.width, img8.height))
                    Dd = _mapa_dif(a, b)[0]
                    p = (float(np.percentile(Dd, 99.9)), int(Dd.max()), dx, dy)
                    mejor = p if mejor is None or p < mejor else mejor
            out["IFD%d_x%d" % (k, factores[k])] = {
                "ventanas": int(D.size), "pasan_umbral": int(len(malas)),
                "p50_p90_p99_p999_max": [float(np.percentile(D, q)) for q in (50, 90, 99, 99.9)]
                                        + [int(D.max())],
                "donde_pasan": dict(cls),
                "caja_pasan_mm": ([mm(malas[:, 1].min()), mm(malas[:, 0].min()),
                                   mm(malas[:, 1].max() + 1), mm(malas[:, 0].max() + 1)]
                                  if len(malas) else None),
                "rgb_medio_pasan_nivel_y_x8": ([A[D > E.UMBRAL_COHERENCIA].mean(axis=0).round(1).tolist(),
                                                B[D > E.UMBRAL_COHERENCIA].mean(axis=0).round(1).tolist()]
                                               if len(malas) else None),
                "mejor_desplazamiento_px_x8": {"dx": mejor[2], "dy": mejor[3], "p999": mejor[0],
                                               "max": mejor[1]}}
    finally:
        t.f.close()
    _imprime("%-10s coherencia: %s" % (op, json.dumps(out, ensure_ascii=False)))
    return out


def resumen(base):
    """Tabla por lámina, solo del manifiesto (sin PII: opacos, cifras y veredictos)."""
    man = C.lee_manifiesto(base)
    _imprime("tanda: %s" % (man.get("tanda") or {}).get("valor"))
    e1 = man.get("eslabon1", {})
    _imprime("eslabón 1: cierres %s" % e1.get("cierres"))
    _imprime("op|tinción|mpp|sha256|zona mm²|tejido mm²|frag|I0|lector a/b/c|atribución|cristal|N1")
    for op in OPACOS:
        e = man["laminas"].get(op, {})
        p = e.get("prueba_lector", {})
        t = (e.get("tejido") or {}).get("principal", {})
        cr = e.get("cristal", {})
        _imprime("%s|%s|%.4f|%s|%.2f|%.2f|%s|%s|%s/%s/%s|%s|%s|%s" % (
            op, e.get("tincion"), e.get("mpp", 0), e.get("sha256", "")[:12],
            (e.get("zona_escaneada") or {}).get("mm2", 0), t.get("mm2", 0),
            t.get("fragmentos_gt_0_2mm2"), (e.get("i0") or {}).get("vidrio_referencia_rgb"),
            "ok" if p.get("a_ok") else "NO", "ok" if p.get("b_ok") else "NO(%s)" % p.get("b_dif_max"),
            "ok" if p.get("c_ok") else "NO(%s)" % p.get("c_dif_max"),
            " / ".join(e.get("nivel_atribucion") or []),
            "SALTA" if cr.get("salta") else ("limpio" if cr else "—"),
            "sí" if e.get("n1_apta") else "no"))


def diag(base, op):
    """Solo números: percentiles de ODsum dentro de la zona escaneada y área por umbral."""
    import numpy as np
    with np.load(os.path.join(base, "mascaras", op + "-8um.npz")) as z:
        od = z["odsum"].astype(np.float32)
        dentro = z["dentro"]
        px = (float(z["mpp"]) / 1000.0) ** 2
    v = od[dentro]
    q = np.percentile(v, [10, 25, 50, 60, 70, 75, 80, 85, 90, 95, 99])
    _imprime("%-10s ODsum en zona: p10..p99 %s" % (op, " ".join("%.3f" % x for x in q)))
    _imprime("%-10s área (mm²) por umbral: %s" % (op, " ".join(
        "%.2f:%.2f" % (T, (v > T).sum() * px) for T in (0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.1,
                                                        0.15, 0.25))))


# ── Tinta roja (diagnóstico, 2-oct-26) ───────────────────────────────────────────────────────
# El detector de trazos de exporta_n1 no mira el rojo («no se distingue de la eosina»), y con
# rótulos sintéticos el rotulador rojo a mano solo salta 7 de 60 veces en el campo ×8. Antes de
# proponer un detector hay que saber cuánto de la H&E REAL cae en la ventana roja. Ventana en HSV
# de PIL (0-255): tono ≥245 o ≤10, valor ≥60, y la saturación a varios umbrales. Sintético: el
# rotulador (200, 30, 40) da tono 252 y saturación 217; la eosina sintética, tono 233 y 51.
UMBRALES_S_ROJO = (150, 180, 200, 220)


def px_rojo(img):
    """{umbral de saturación: nº de píxeles en la ventana roja}."""
    from PIL import ImageChops
    h, s, v = img.convert("RGB").convert("HSV").split()
    tono = ImageChops.lighter(h.point(lambda x: 255 if x >= 245 else 0),
                              h.point(lambda x: 255 if x <= 10 else 0))
    ventana = ImageChops.multiply(tono, v.point(lambda x: 255 if x >= 60 else 0))
    return {u: ImageChops.multiply(ventana, s.point(lambda x, u=u: 255 if x >= u else 0))
            .histogram()[255] for u in UMBRALES_S_ROJO}


def rojo(base, op):
    """Solo números, no escribe nada: píxeles en la ventana roja del ×8 COMPLETO y de los dos PNG
    de revisión, al lado del umbral del detector de trazos (máx(50, 0,05 % del área)). Control
    positivo dentro de la jaula: un trazo rojo sintético tiene que contar."""
    from PIL import Image, ImageDraw
    import laminillas_lector as L
    ctl = Image.new("RGB", (800, 300), (232, 233, 234))
    ImageDraw.Draw(ctl).line([(50, 150), (750, 150)], fill=(200, 30, 40), width=12)
    if px_rojo(ctl)[200] < 5000:
        raise RuntimeError("el control positivo de tinta roja no cuenta")
    imgs = {"x8": Image.fromarray(L.lee_nivel(L.abre(op), 8))}
    rev = C.lee_manifiesto(base)["laminas"][op].get("revision", {})
    for clave in ("miniatura", "campo_x8"):
        if clave in rev:
            imgs[clave] = Image.open(os.path.join(base, rev[clave]["fichero"]))
    import exporta_n1 as E
    for clave, img in imgs.items():
        n = px_rojo(img)
        nt, umbral = E.trazos_de_rotulador(img)      # las ventanas no se solapan: se suman
        _imprime("%-10s rojo %-10s %5dx%-5d umbral %6d · trazos %6d · trazos+rojo(S≥200) %6d · "
                 "S≥ %s" % (op, clave, img.width, img.height, umbral, nt, nt + n[200],
                             " ".join("%d:%d" % kv for kv in n.items())))
    L.cierra_todo()


def main_vips(args):
    """Venv valis: prueba (c)."""
    base = C.sesion()
    for op in _lista(args):
        r = _paso(base, "lector-c", op, prueba_vips)
        if r:
            _imprime("%-10s lector (c) pyvips vs OpenSlide ×8: dif máx %d, >2 en %d/%d" % (
                op, r["c_dif_max"], r["c_recortes_dif_gt2"], r["c_n"]))
    return 0


# Tanda de tinción (F1.2): laboratorio y año con nivel de prueba. Se rellena con la evidencia que
# se haya podido VER; sin ella, «no determinada» y por qué.
TANDAS = {
    # Visto el 1-oct-26 (Chrome, visor de adjuntos de Gmail, sin descargar): IMG_3720 muestra tres
    # cajas portaláminas con la etiqueta impresa de la lámina delantera de cada una: serie del
    # laboratorio B de 2026 con el número de su revisión de material (el mismo de su informe de
    # 10-jun-26), cortes A1-1 «H-E», A1-7 «HER2, MAMA IH» y A1-12 «P63 IH», todos del
    # bloque A1 del primario; y un casete rosa con la misma serie, bloque A002. IMG_3733 es el
    # sobre cerrado (sin información). Aquí NO va ningún número: nivel de prueba y alcance.
    "lab-b-2026": {
        "valor": "laboratorio B, 2026: recortes del bloque A1 del primario, numerados A1-n bajo "
                 "la revisión de material del laboratorio B",
        "nivel_de_prueba": ("etiqueta física fotografiada en 3 de las 15 láminas (la delantera de "
                            "cada caja: H&E A1-1, HER2 «MAMA IH» A1-7, p63 A1-12); el resto, "
                            "inferido por ir en las mismas cajas"),
        "vinculo_escaneo_cristal": ("los escaneos no traen etiqueta ni macro: el vínculo es el texto "
                                    "del marcador en el nombre de fichero (P-HER2 lleva «MAMA IH» "
                                    "como la etiqueta), no la imagen"),
        "hueso": ("cortes A2-1/A2-2 del bloque A2 de la revisión del laboratorio B según el nombre "
                  "de fichero; "
                  "su etiqueta no se ve en la foto"),
        "fuente": "correo enviado del 18-sep-26 (envío al laboratorio receptor), adjunto IMG_3720; "
                  "IMG_3733 sin datos",
    },
    "no-determinada": {
        "valor": "no determinada",
        "nivel_de_prueba": "ninguno",
        "por_que": ("sin ver las fotos IMG_3720/IMG_3733 del correo del 18-sep (material "
                    "enviado) no hay evidencia del laboratorio: los escaneos no traen etiqueta "
                    "ni macro, y el conector de Gmail solo da los metadatos del adjunto"),
        "inferencia_del_plan": ("probablemente la tanda del laboratorio B, 2026 [inferido: "
                                "nomenclatura A2-x; RA y p63 solo en B] — no verificado"),
    },
}


# ── inventario ──────────────────────────────────────────────────────────────────────────────
def inventario(zip_ruta):
    with zipfile.ZipFile(zip_ruta) as z:
        infos = z.infolist()
        _imprime("entradas: %d" % len(infos))
        for k, i in enumerate(infos):
            try:
                op = clasifica(i.filename)
            except ValueError:
                op = "¿?"
            _imprime("%2d %s · %.1f MB%s" % (k, esqueleto(i.filename), i.file_size / 1e6,
                                             " → %s" % op if op else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
