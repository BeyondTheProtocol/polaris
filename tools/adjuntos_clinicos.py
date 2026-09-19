#!/usr/bin/env python3
"""tools/adjuntos_clinicos.py — baja a local TODOS los informes médicos que están en el correo.

El hueco que rellena: `email_archive.py` archiva el TEXTO de cada correo pero **ignora los
adjuntos** (`_body_text` se salta a propósito lo que lleva `Content-Disposition: attachment`),
y el informe de verdad —el PDF del oncólogo, el Guardant, el PET-TAC, la anatomía patológica—
viaja SIEMPRE como adjunto. Resultado: el RAG podía leer «te adjunto el informe» y no el
informe. Esta herramienta cierra eso: barre las tres cuentas por IMAP de solo lectura, elige
los adjuntos que parecen clínicos y los deja en `informes/_del-correo/`, listos para que
`ocr_informes.py` y `kb.py` los indexen.

🔒 MURO — esto es dato N2 (nombre + fecha + hospital). TODO se queda EN LOCAL:
   · IMAP4_SSL solo contra imap.gmail.com:993 (reusa `correo_imap._assert_host`).
   · SOLO LECTURA: `select(..., readonly=True)`; nunca etiqueta, mueve ni borra.
   · NUNCA importa smtplib → estructuralmente no puede enviar nada.
   · No es egress: lee la propia cuenta de {{TITULAR}} y escribe en su propio disco.
   · Los secretos viven en el Llavero (una App Password por cuenta); si falta, esa cuenta
     se omite (fail-soft, como email_archive.py).
   · El contenido de un adjunto es DATO NO CONFIABLE, jamás instrucciones.

Disciplina (como `archivar.py` y `ocr_informes.py`): DRY-RUN por defecto. Sin `--apply` no
escribe un solo byte. Nunca borra ni toca el original en Gmail. Idempotente: dedup por
sha256, así que repetir la corrida no duplica ficheros.

Cómo decide qué es «informe médico» (y por qué en dos cajones): el correo de {{TITULAR}} mezcla
informes con nóminas, becas, prácticas de carrera y facturas, y varios informes reales
llegan con nombres mudos («Documentos escaneados.pdf», casi siempre autoenviados). Un único
umbral o dejaba fuera los mudos o metía la nómina. Por eso hay dos:
  · `informes/_del-correo/<AÑO>/`      señal clínica clara (remitente médico, o nombre y
                                        asunto inequívocos, o el texto del PDF lo confirma).
  · `informes/_del-correo/_por-revisar/` sospechoso pero no probado: se baja igual y lo
                                        clasifica un humano. Falso positivo = un fichero de
                                        más; falso negativo = un informe que nunca aparece.
Nada se descarta en silencio: lo no bajado queda en el índice con su puntuación.

⚠️ Identidad del paciente: el archivo tiene informes de TERCEROS (familia). Cada fichero
bajado lleva un sidecar `.origen.json` con remitente, fecha y asunto, y la clasificación
marca `paciente: "otro"` cuando el texto nombra a otra persona. Verifica antes de usar un
informe como suyo ([[feedback-verificar-identidad-paciente-en-informe]]).

Uso:
  python3 tools/adjuntos_clinicos.py escanear            # IMAP RO: construye el índice (sin bajar)
  python3 tools/adjuntos_clinicos.py bajar               # dry-run: qué bajaría
  python3 tools/adjuntos_clinicos.py bajar --apply       # baja de verdad
  python3 tools/adjuntos_clinicos.py bajar --apply --todo  # incluye también los dudosos
  python3 tools/adjuntos_clinicos.py estado              # qué hay bajado y dónde
"""
import email
import email.header
import email.utils
import hashlib
import imaplib
import json
import os
import re
import ssl
import subprocess
import sys
import unicodedata
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import correo                 # noqa: E402  (CORREO_DIR, _write_atomic, detecta_inyeccion)
import correo_imap as ci      # noqa: E402  (_assert_host, _decode, IMAP_HOST/PORT, FaltaClave)
import email_archive as ea    # noqa: E402  (ACCOUNTS, _connect, _all_mail_folder, _quote_mailbox)

HOME = os.path.expanduser("~")
REPO = os.environ.get("BTP_REPO") or os.path.join(HOME, "claudecode")
# Casa base, no el worktree: los informes son estado vivo de {{TITULAR}}, no código versionable
# ([[feedback-estado-vivo-resuelve-casa-base]]). La carpeta ya es zona clínica para el guard.
DESTINO = os.path.join(REPO, "informes", "_del-correo")
REVISAR = os.path.join(DESTINO, "_por-revisar")
INDICE = os.path.join(correo.CORREO_DIR, "adjuntos_clinicos_indice.json")
MANIFEST = os.path.join(DESTINO, "_manifest.json")

# Extensiones que pueden contener un informe. Lo demás (logos, .ics, .m4a…) ni se puntúa.
EXT_OK = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".heif",
          ".doc", ".docx", ".rtf", ".odt", ".xls", ".xlsx", ".zip", ".dcm", ".dicom"}

# Dominios y buzones de los que un adjunto es clínico salvo prueba en contra.
DOMINIOS_CLINICOS = (
    "{{CENTRO}}.net", "mdanderson.org", "fredhutch.org", "{{CENTRO}}.ch", "biontech.de",
    "iobmadridtrials.com", "guardanthealth.com", "foundationmedicine.com", "carm.es",
    "quironsalud.es", "sanitas.es", "hospital", "clinic", "onco", "salud", "sanidad",
    "genomica", "labco", "synlab", "cerba", "eurofins", "dipcan", "imegen", "veritas",
    "biomedicallogistics.com", "kernis.bio", "solti.es", "seom.org", "{{CENTRO}}",
    "sjd.es", "vhebron", "clinicbarcelona.org", "cun.es", "mdanderson.es",
)

# Señal fuerte: si esto aparece en el nombre del fichero o en el asunto, es clínico.
FUERTE = re.compile(
    r"(?i)biops|anatom[íi]a.?patol|patolog|histolog|inmunohistoq|citolog|"
    r"oncol[óo]g|guardant|foundation.?(one|medicine)|dipcan|contacto|"
    r"pet.?tac|pettac|pet.?ct|\btac\b|\brmn\b|resonan|ecograf|mamograf|gammagraf|"
    r"densitom|escintigraf|centellograf|"
    r"anal[íi]tic|hemogram|bioqu[íi]mic|marcador(es)? tumoral|ca.?15.?3|ca.?125|\bcea\b|"
    r"her2|ki.?67|receptor(es)? hormonal|estr[óo]gen|progester|"
    r"informe (m[ée]dic|cl[íi]nic|oncol|radiol|de alta|de consulta)|"
    r"consultas? externas?|alta hospital|epicrisis|historia cl[íi]nica|"
    r"secuenciaci[óo]n|exoma|\bngs\b|panel gen[ée]tic|estudio gen[ée]tic|genomic|"
    r"immunopeptidom|inmunopeptidom|neoantigen|neoant[íi]gen|"
    r"radioterap|\bsbrt\b|quimioterap|inmunoterap|"
    r"\bdicom\b|clinical case|pathology|radiolog"
)

# Señal media: pinta clínica pero ambigua fuera de contexto.
MEDIA = re.compile(
    r"(?i)informe|resultado|prueba|laboratorio|\blab\b|diagn[óo]stic|report|"
    r"scan|\bmri\b|\bct\b|peticion|petici[óo]n|volante|consulta|revisi[óo]n|"
    r"hospital|cl[íi]nic|doctor|\bdra?\.\b|paciente|tratamiento|medicaci[óo]n"
)

# Ruido conocido del buzón: nóminas, becas, prácticas de carrera, marketing, facturas.
RUIDO = re.compile(
    r"(?i)factur|invoice|recib[oí]|n[óo]mina|contrato|presupuesto|tarifa|"
    r"curriculum|curr[íi]culum|\bcv\b|vida laboral|seguridad social|alta aut[óo]nomo|"
    r"alta actividad|036\b|modelo 1\d\d|hacienda|irpf|"
    r"beca|matr[íi]cul|asignatura|pr[áa]ctica|plantilla|apuntes|tfg|tfm|universidad|upct|"
    r"matomo|analytics|newsletter|boletin|bolet[íi]n|marketing|campa[ñn]a|"
    r"logo|banner|firma_|signature|bookings_|greetings|"
    r"billete|boarding|reserva|hotel|vuelo|entrada[s]?\.com|pedido|env[íi]o|"
    r"colaboraci[óo]n|embajador|patrocin|propuesta comercial|"
    r"veterinari|mascota|felin|canin"
)

# Nombres mudos que casi siempre esconden un informe escaneado (el patrón del autoenvío).
MUDO = re.compile(r"(?i)^(documentos?[ _-]escaneados?|scanned[ _-]documents?|img[-_]?\d+|"
                  r"image[-_]?\d+|photo|foto|doc\d*|adjunto|attachment|whatsapp image)")

CUENTAS_PROPIAS = {"titular.mgp@gmail.com", "titular@gmail.com",
                   "titular@gmail.com"}

UMBRAL_FUERTE = 4      # → informes/_del-correo/<AÑO>/
UMBRAL_REVISAR = 1     # → informes/_del-correo/_por-revisar/


# ─────────────────────────── utilidades ───────────────────────────

def _norm(s):
    """Minúsculas sin tildes: 'Analítica' y 'analitica' tienen que puntuar igual."""
    s = unicodedata.normalize("NFD", str(s or ""))
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def _ext(nombre):
    return os.path.splitext(nombre or "")[1].lower()


def _seguro(nombre, limite=110):
    """Nombre de fichero utilizable: sin separadores ni caracteres que rompan el FS."""
    nombre = (nombre or "adjunto").replace("/", "-").replace("\\", "-").replace("\x00", "")
    nombre = re.sub(r'[:*?"<>|\r\n\t]', "_", nombre).strip(" .") or "adjunto"
    raiz, ext = os.path.splitext(nombre)
    return raiz[:limite] + ext[:12]


def _emisor_dominio(remitente):
    m = re.search(r"[\w.+-]+@([\w.-]+)", remitente or "")
    return (m.group(1) if m else "").lower()


def _correo_de(remitente):
    m = re.search(r"[\w.+-]+@[\w.-]+", remitente or "")
    return (m.group(0) if m else "").lower()


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# ─────────────────────────── puntuación ───────────────────────────

def puntuar(remitente, asunto, nombre_fichero):
    """Cuánto huele a informe médico. Devuelve (score, motivos).

    Suma señales en vez de decidir con una sola regla: el remitente clínico basta, pero un
    nombre de fichero inequívoco también, y un autoenvío con nombre mudo (el patrón con el
    que {{TITULAR}} se pasa los informes escaneados) llega a «revisar» sin arrastrar la nómina.
    """
    score, motivos = 0, []
    dom = _emisor_dominio(remitente)
    n_fich, n_asunto = _norm(nombre_fichero), _norm(asunto)
    ext = _ext(nombre_fichero)

    if any(d in dom for d in DOMINIOS_CLINICOS):
        score += 4
        motivos.append("remitente clínico (%s)" % dom)

    if FUERTE.search(n_fich):
        score += 4
        motivos.append("nombre inequívoco")
    elif MEDIA.search(n_fich):
        score += 1
        motivos.append("nombre ambiguo")

    if FUERTE.search(n_asunto):
        score += 3
        motivos.append("asunto inequívoco")
    elif MEDIA.search(n_asunto):
        score += 1
        motivos.append("asunto ambiguo")

    if _correo_de(remitente) in CUENTAS_PROPIAS:
        # Autoenvío: así mueve ella los informes entre dispositivos. Vale como indicio, no
        # como prueba — sin otra señal se queda en «por revisar», que es donde debe estar.
        if ext in {".pdf", ".jpg", ".jpeg", ".png", ".heic", ".zip", ".tif", ".tiff"}:
            score += 2
            motivos.append("autoenvío")
        if MUDO.match(os.path.basename(n_fich)):
            score += 1
            motivos.append("nombre mudo (escaneo)")

    if ext in {".dcm", ".dicom"} or "dicom" in n_fich:
        score += 4
        motivos.append("imagen médica DICOM")

    if RUIDO.search(n_fich) or RUIDO.search(n_asunto):
        score -= 5
        motivos.append("ruido conocido (−5)")

    return score, motivos


# Nombre, Content-ID y tamaño de una parte, tal y como los serializa BODYSTRUCTURE:
#   ("NAME" "image001.jpg") "<image001@01D5...>" NIL "BASE64" 8462 118
_PARTE = re.compile(
    r'"(?:NAME|FILENAME)"\s+"((?:[^"\\]|\\.)*)"\s*\)?\s*'      # nombre
    r'(?:(NIL|"[^"]*")\s+)?'                                    # Content-ID (o NIL)
    r'(?:(?:NIL|"[^"]*")\s+)?'                                  # descripción
    r'"(?:BASE64|QUOTED-PRINTABLE|7BIT|8BIT|BINARY)"\s+(\d+)',  # encoding + octetos
    re.I)

MIN_IMAGEN = 40_000   # bytes: por debajo es logo o firma, nunca un informe escaneado


def _partes_de_bodystructure(meta):
    """Adjuntos que asoman en el BODYSTRUCTURE: [(nombre, es_inline, octetos)].

    Se usa en el escaneo para no bajar 6.500 mensajes enteros solo para mirar la etiqueta:
    el FETCH completo se reserva para los candidatos. El Content-ID importa porque delata
    la imagen EMBEBIDA (la firma corporativa `image001.jpg`), que inundaba el cajón de
    dudosos con 2.400 entradas de ruido.
    """
    fuera, vistos = [], set()
    for nombre, cid, octetos in _PARTE.findall(meta):
        nombre = nombre.replace('\\"', '"').replace("\\\\", "\\")
        try:
            nombre = ci._decode(nombre)
        except Exception:
            pass
        inline = bool(cid) and cid.upper() != "NIL"
        try:
            octetos = int(octetos)
        except Exception:
            octetos = 0
        clave = (nombre, inline, octetos)
        if clave in vistos:
            continue
        vistos.add(clave)
        fuera.append(clave)
    return fuera


# ─────────────────────────── escaneo (IMAP RO) ───────────────────────────

def escanear(cuentas=None, verbose=True):
    """Recorre las cuentas y devuelve el índice de adjuntos candidatos. No baja bytes."""
    fuera = []
    for user, service in (cuentas or ea.ACCOUNTS):
        try:
            M = ea._connect(user, service)
        except Exception as e:
            print("  ⚠️  %s omitida: %s" % (user, e), file=sys.stderr)
            continue
        try:
            box = ea._all_mail_folder(M)
            typ, _ = M.select(ea._quote_mailbox(box), readonly=True)
            if typ != "OK":
                M.select("INBOX", readonly=True)
            typ, data = M.uid("SEARCH", "X-GM-RAW", '"has:attachment"')
            uids = data[0].split() if (typ == "OK" and data and data[0]) else []
            if verbose:
                print("  %s → %d correos con adjunto" % (user, len(uids)), file=sys.stderr)
            for i in range(0, len(uids), 100):
                lote = b",".join(uids[i:i + 100]).decode()
                typ, resp = M.uid("FETCH", lote,
                                  "(BODYSTRUCTURE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                if typ != "OK":
                    continue
                for item in resp:
                    if not isinstance(item, tuple):
                        continue
                    meta = item[0].decode("utf-8", "replace")
                    hdr = email.message_from_bytes(item[1])
                    m = re.search(r"UID (\d+)", meta)
                    if not m:
                        continue
                    remitente = ci._decode(hdr.get("From", ""))
                    asunto = ci._decode(hdr.get("Subject", ""))
                    fecha = hdr.get("Date", "")
                    for nombre, inline, octetos in _partes_de_bodystructure(meta):
                        ext = _ext(nombre)
                        if ext not in EXT_OK:
                            continue
                        es_imagen = ext in {".jpg", ".jpeg", ".png", ".tif", ".tiff",
                                            ".heic", ".heif"}
                        if es_imagen and (inline or (octetos and octetos < MIN_IMAGEN)):
                            continue        # firma o logo embebido, no un informe
                        score, motivos = puntuar(remitente, asunto, nombre)
                        if score < UMBRAL_REVISAR:
                            continue
                        fuera.append({
                            "cuenta": user, "uid": m.group(1), "fichero": nombre,
                            "from": remitente, "subject": asunto, "date": fecha,
                            "bytes": octetos, "score": score, "motivos": motivos,
                        })
        finally:
            try:
                M.logout()
            except Exception:
                pass
    fuera.sort(key=lambda r: -r["score"])
    return fuera


# ─────────────────────────── bajada ───────────────────────────

def _anio(fecha_cabecera):
    try:
        return email.utils.parsedate_to_datetime(fecha_cabecera).strftime("%Y")
    except Exception:
        return "sin-fecha"


def _texto_pdf(ruta, paginas=3):
    """Primeras páginas de un PDF con capa de texto. 100% local (poppler), sin red."""
    try:
        out = subprocess.run(["pdftotext", "-l", str(paginas), "-q", ruta, "-"],
                             capture_output=True, timeout=40)
        return out.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def confirmar_por_contenido(ruta):
    """Segunda opinión leyendo el propio fichero: (delta_score, paciente, evidencia).

    Un nombre puede mentir en las dos direcciones; el texto del informe no. Solo aplica a
    PDFs con capa de texto: el escaneado sin OCR se queda como estaba y ya lo recogerá
    `ocr_informes.py`.
    """
    if _ext(ruta) != ".pdf":
        return 0, "?", ""
    txt = _texto_pdf(ruta)
    if len(txt.strip()) < 80:
        return 0, "?", "sin capa de texto"
    n = _norm(txt)
    delta, evid = 0, []
    if FUERTE.search(n):
        delta += 3
        evid.append("texto clínico")
    if RUIDO.search(n[:1500]) and not FUERTE.search(n[:1500]):
        delta -= 3
        evid.append("cabecera de documento no clínico")
    paciente = "?"
    if re.search(r"titular", n) and re.search(r"{{APELLIDO}}", n):
        paciente = "titular"
        evid.append("nombra a {{TITULAR}} {{APELLIDO}}")
    elif re.search(r"(?:paciente|nombre)\s*:?\s*[a-zñ]+\s+[a-zñ]+", n):
        paciente = "otro?"
        evid.append("nombra a otro paciente")
    return delta, paciente, "; ".join(evid)


def bajar(indice, apply=False, incluir_dudosos=False, maximo=None):
    """Descarga los candidatos del índice. Sin `apply` solo dice qué haría."""
    manifest = _read_json(MANIFEST, {"ficheros": [], "hashes": {}})
    hashes = dict(manifest.get("hashes") or {})
    ya = {(f["cuenta"], f["uid"], f["fichero"]) for f in manifest.get("ficheros", [])}

    umbral = UMBRAL_REVISAR if incluir_dudosos else UMBRAL_FUERTE
    pendientes = [r for r in indice
                  if r["score"] >= umbral and (r["cuenta"], r["uid"], r["fichero"]) not in ya]
    if maximo:
        pendientes = pendientes[:maximo]

    porcuenta = {}
    for r in pendientes:
        porcuenta.setdefault(r["cuenta"], []).append(r)

    nuevos, saltados, dup = [], [], 0
    servicios = dict(ea.ACCOUNTS)
    for user, filas in porcuenta.items():
        if not apply:
            continue
        try:
            M = ea._connect(user, servicios.get(user))
        except Exception as e:
            print("  ⚠️  %s omitida: %s" % (user, e), file=sys.stderr)
            continue
        try:
            box = ea._all_mail_folder(M)
            typ, _ = M.select(ea._quote_mailbox(box), readonly=True)
            if typ != "OK":
                M.select("INBOX", readonly=True)
            por_uid = {}
            for r in filas:
                por_uid.setdefault(r["uid"], []).append(r)
            for uid, rs in por_uid.items():
                typ, resp = M.uid("FETCH", uid, "(BODY.PEEK[])")
                if typ != "OK":
                    continue
                crudo = next((it[1] for it in resp if isinstance(it, tuple)), None)
                if not crudo:
                    continue
                msg = email.message_from_bytes(crudo)
                quiero = {_norm(r["fichero"]): r for r in rs}
                for part in msg.walk():
                    nombre = part.get_filename()
                    if not nombre:
                        continue
                    try:
                        nombre = ci._decode(nombre)
                    except Exception:
                        pass
                    fila = quiero.get(_norm(nombre))
                    if fila is None:
                        continue
                    datos = part.get_payload(decode=True)
                    if not datos:
                        continue
                    h = hashlib.sha256(datos).hexdigest()
                    if h in hashes:
                        dup += 1
                        continue
                    fuerte = (fila["score"] + fila.get("delta_contenido", 0)) >= UMBRAL_FUERTE
                    carpeta = os.path.join(DESTINO, _anio(fila["date"])) if fuerte else REVISAR
                    os.makedirs(carpeta, exist_ok=True)
                    destino = os.path.join(carpeta, _seguro(nombre))
                    raiz, ext = os.path.splitext(destino)
                    k = 2
                    while os.path.exists(destino):
                        destino = "%s (%d)%s" % (raiz, k, ext)
                        k += 1
                    with open(destino, "wb") as f:
                        f.write(datos)
                    delta, paciente, evid = confirmar_por_contenido(destino)
                    # El contenido manda sobre la etiqueta, en los DOS sentidos: asciende el
                    # escaneo mudo que resulta ser un informe y degrada el PDF que prometía
                    # informe y era una factura. El fichero se mueve, no se vuelve a bajar.
                    ahora_fuerte = (fila["score"] + delta) >= UMBRAL_FUERTE
                    if ahora_fuerte != fuerte:
                        nueva = os.path.join(DESTINO, _anio(fila["date"])) if ahora_fuerte else REVISAR
                        os.makedirs(nueva, exist_ok=True)
                        movido = os.path.join(nueva, os.path.basename(destino))
                        if not os.path.exists(movido):
                            os.replace(destino, movido)
                            destino = movido
                    reg = {
                        "ruta": os.path.relpath(destino, REPO), "sha256": h,
                        "bytes": len(datos), "cuenta": user, "uid": uid,
                        "fichero": nombre, "from": fila["from"], "subject": fila["subject"],
                        "date": fila["date"], "score": fila["score"],
                        "motivos": fila["motivos"], "delta_contenido": delta,
                        "paciente": paciente, "evidencia": evid,
                        "bajado": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    }
                    with open(destino + ".origen.json", "w", encoding="utf-8") as f:
                        json.dump(reg, f, ensure_ascii=False, indent=1)
                    hashes[h] = reg["ruta"]
                    nuevos.append(reg)
        finally:
            try:
                M.logout()
            except Exception:
                pass

    if apply:
        manifest = {"actualizado": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "ficheros": manifest.get("ficheros", []) + nuevos, "hashes": hashes}
        os.makedirs(DESTINO, exist_ok=True)
        _write_json(MANIFEST, manifest)
    return {"candidatos": len(pendientes), "bajados": len(nuevos),
            "duplicados": dup, "saltados": saltados, "nuevos": nuevos}


# ─────────────────────────── CLI ───────────────────────────

def _resumen_indice(idx):
    fuertes = [r for r in idx if r["score"] >= UMBRAL_FUERTE]
    dudosos = [r for r in idx if UMBRAL_REVISAR <= r["score"] < UMBRAL_FUERTE]
    print("Índice: %d adjuntos candidatos — %d claros, %d por revisar"
          % (len(idx), len(fuertes), len(dudosos)))
    return fuertes, dudosos


def main(argv):
    cmd = (argv[0] if argv else "estado").lower()
    apply = "--apply" in argv
    todo = "--todo" in argv
    maximo = None
    for i, a in enumerate(argv):
        if a == "--max" and i + 1 < len(argv):
            maximo = int(argv[i + 1])

    if cmd == "escanear":
        print("Escaneando (IMAP solo lectura)…", file=sys.stderr)
        idx = escanear()
        _write_json(INDICE, {"actualizado": datetime.now().strftime("%Y-%m-%d %H:%M"),
                             "filas": idx})
        fuertes, dudosos = _resumen_indice(idx)
        for r in fuertes[:25]:
            print("  %2d  %-58s  %s" % (r["score"], r["fichero"][:58], r["from"][:40]))
        print("\nÍndice en %s" % INDICE)
        return 0

    if cmd == "bajar":
        idx = _read_json(INDICE, {}).get("filas")
        if not idx:
            print("No hay índice. Corre primero: adjuntos_clinicos.py escanear", file=sys.stderr)
            return 1
        _resumen_indice(idx)
        res = bajar(idx, apply=apply, incluir_dudosos=todo, maximo=maximo)
        if not apply:
            umbral = UMBRAL_REVISAR if todo else UMBRAL_FUERTE
            print("\nDRY-RUN: bajaría %d adjuntos (score ≥ %d). Repite con --apply."
                  % (res["candidatos"], umbral))
            return 0
        print("\n✅ %d bajados, %d duplicados saltados → %s"
              % (res["bajados"], res["duplicados"], os.path.relpath(DESTINO, REPO)))
        otros = [r for r in res["nuevos"] if r["paciente"] == "otro?"]
        if otros:
            print("⚠️  %d ficheros nombran a OTRO paciente: verifica antes de usarlos." % len(otros))
        return 0

    if cmd == "estado":
        man = _read_json(MANIFEST, {})
        fich = man.get("ficheros", [])
        print("Bajados: %d ficheros (manifest %s)" % (len(fich), man.get("actualizado", "—")))
        porcarpeta = {}
        for f in fich:
            porcarpeta.setdefault(os.path.dirname(f["ruta"]), 0)
            porcarpeta[os.path.dirname(f["ruta"])] += 1
        for k in sorted(porcarpeta):
            print("  %-52s %d" % (k, porcarpeta[k]))
        return 0

    print(__doc__.split("Uso:")[-1].strip())
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
