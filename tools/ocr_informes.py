#!/usr/bin/env python3
"""ocr_informes.py — OCR 100% LOCAL de informes escaneados → texto para el RAG.

El hueco que rellena: `kb.py` ya indexa PDFs CON capa de texto, pero un informe
ESCANEADO (o una foto/imagen) es solo píxeles → kb no puede leerlo. Esta herramienta
detecta esos casos y saca su texto con OCR, dejándolo en un sidecar `.ocr.txt` al lado
del original para que kb.py lo indexe.

🔒 MURO — dato clínico N2 (nombre + fecha + hospital): TODO ocurre EN LOCAL.
   Sin red, sin APIs, sin nube. tesseract + pdftoppm son binarios en la máquina.
   No hay una sola llamada saliente. (Nivel N2 se queda en casa → riesgo nulo de
   egress; ver regla de niveles de sensibilidad en CLAUDE.md.)

Disciplina (como archivar.py): DRY-RUN por defecto (no escribe nada, solo dice qué
haría); con --apply escribe los sidecar; NUNCA mueve ni borra ni toca el original.
Idempotente: si el sidecar ya existe y es más nuevo que el original, se salta.

Uso:
  python3 tools/ocr_informes.py                 # dry-run sobre las carpetas de informes
  python3 tools/ocr_informes.py --apply         # escribe los .ocr.txt
  python3 tools/ocr_informes.py --dir "ruta"    # otra carpeta (repetible)
  python3 tools/ocr_informes.py --apply --reindex   # además reindexa el RAG al terminar

Requisitos (ya presentes): tesseract (spa+eng), pdftoppm/pdfinfo (poppler).
"""
import os, sys, subprocess, shutil
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"))
# El venv, NO el `python3` del PATH: pypdf solo vive ahí, y sin él `kb.build()` se niega a
# indexar (bien: dejaría el índice sin los PDFs del historial). Este tool es justo el que OCRiza
# esos PDFs, así que con el intérprete equivocado su reindexado NUNCA entraba.
VENV_PY = ROOT / ".venv" / "bin" / "python3"

# Carpetas de informes de TEXTO por defecto (locales; la privada vive fuera del repo).
# OJO: NO metemos ~/Clinico-PRIVADO entero — ahí viven los DICOM, renders 3D y PNGs de
# imagen médica (T1.png, dual_iliaco.png…), que son PÍXELES SIN TEXTO: OCRearlos es inútil
# y lentísimo. Solo la subcarpeta de informes redactados.
DEFAULT_DIRS = [
    ROOT / "informes",
    Path.home() / "Clinico-PRIVADO" / "Informes-y-analisis",
]

# Partes de ruta que delatan imagen médica / visores / renders (NO son texto): se excluyen.
EXCLUDE_PARTS = {
    "dicom", "imagen-procesada", "visores", "visualiz", "poster", "3d",
    "huesos-3d", "segment", "-rm-", "morfolog", "secuencias",
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".heic", ".heif"}
LANGS = "spa+eng"           # castellano + inglés (informes mezclan siglas EN)
TEXT_LAYER_MIN_CHARS = 120   # si un PDF ya da >= esto de texto real, NO es escaneado
DPI = 300                    # resolución de rasterizado para OCR

APPLY = "--apply" in sys.argv
REINDEX = "--reindex" in sys.argv
# Por defecto SOLO PDFs escaneados (los informes de texto lo son). Las imágenes sueltas
# suelen ser imagen médica (PET/gammagrafía/renders), no texto → opt-in explícito.
IMAGES = "--images" in sys.argv


def _have(bin_name: str) -> bool:
    return shutil.which(bin_name) is not None


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def pdf_text_layer_chars(pdf: Path) -> int:
    """Cuánto texto REAL tiene ya el PDF (para saber si está escaneado)."""
    if _have("pdftotext"):
        r = _run(["pdftotext", "-q", str(pdf), "-"])
        return len((r.stdout or "").strip())
    # Fallback: sin pdftotext, asumimos que hay que mirarlo (0 = trátalo como escaneado)
    return 0


def ocr_image(img: Path) -> str:
    """OCR de una imagen con tesseract. HEIC → png con sips antes."""
    tmp = None
    src = img
    if img.suffix.lower() in {".heic", ".heif"} and _have("sips"):
        tmp = Path(tempfile.mkdtemp()) / (img.stem + ".png")
        _run(["sips", "-s", "format", "png", str(img), "--out", str(tmp)])
        src = tmp
    r = _run(["tesseract", str(src), "stdout", "-l", LANGS])
    if tmp:
        shutil.rmtree(tmp.parent, ignore_errors=True)
    return (r.stdout or "").strip()


def _tsv_a_texto(tsv: str):
    """TSV de tesseract → (texto con el layout respetado, confianza media 0-100, nº de palabras).

    POR QUÉ (12-sep-2026). Antes se pedía `stdout` pelado, que **lineariza**: tesseract no trae
    modelo de layout, así que una página a dos columnas sale entrelazada y una tabla se convierte
    en una ristra de palabras. Justo lo que más hay en lo que ella necesita buscar (analíticas,
    tablas de anatomía patológica, informes a dos columnas), y `kb.py` indexa ESE texto, así que
    el destrozo se propaga a todas las búsquedas del RAG.

    El TSV ya trae `left/top/width/height` y `conf` por palabra, sin dependencia nueva. Con eso:
      · las palabras se agrupan por línea real (block/par/line) y se ordenan por `left`;
      · un salto horizontal grande dentro de una línea se marca con «  ·  », que es lo que separa
        las celdas de una tabla en vez de pegarlas;
      · los bloques se ordenan por COLUMNA y luego por altura, así una página a dos columnas se
        lee entera la izquierda y luego la derecha, no a saltos.

    La confianza se devuelve para que el llamante pueda cantar una página ilegible: hasta hoy un
    sidecar de basura era indistinguible de uno limpio.
    """
    filas = []
    for n, linea in enumerate(tsv.splitlines()):
        if n == 0 or not linea.strip():
            continue
        c = linea.split("\t")
        if len(c) < 12:
            continue
        try:
            blk, par, ln = int(c[2]), int(c[3]), int(c[4])
            left, top, width, alto = int(c[6]), int(c[7]), int(c[8]), int(c[9])
            conf = float(c[10])
        except ValueError:
            continue
        txt = c[11].strip()
        if not txt:
            continue
        filas.append((blk, par, ln, left, top, width, alto, conf, txt))
    if not filas:
        return "", 0.0, 0

    # LO QUE NO HACE, y conviene que conste. Probé además una detección de «canal vertical» para
    # reordenar páginas a dos columnas, y la quité: con columnas limpias tesseract YA las separa
    # en bloques distintos y sale bien sin ayuda, y en una página mixta (columnas arriba, tabla
    # abajo) la tabla ocupa el centro y el canal no se detecta. O sea, complejidad que no arregló
    # ninguno de los dos casos que probé. Si algún día se mide degradación real en documentos de
    # verdad, el sitio es este; hasta entonces, no se paga por adelantado.
    lineas = {}
    for blk, par, ln, left, top, width, alto, conf, txt in filas:
        g = lineas.setdefault((blk, par, ln), {"top": top, "palabras": []})
        g["top"] = min(g["top"], top)
        g["palabras"].append((left, width, alto, conf, txt))

    orden = sorted(lineas, key=lambda k: (lineas[k]["top"], k[0], k[1], k[2]))
    fuera, confs = [], []
    bloque_previo = None
    for clave in orden:
        palabras = sorted(lineas[clave]["palabras"], key=lambda p: p[0])
        # El umbral de «esto es una celda, no un espacio» se mide contra el TAMAÑO DE LETRA de la
        # línea, no contra el ancho de la página: así no depende del DPI, del tamaño del papel ni
        # de cuánto texto haya en la página. Un hueco de más de metro y medio de altura de letra
        # no es un espacio entre palabras, es una columna. (El primer intento lo medía contra el
        # ancho y separaba «el zorro veloz» en tres celdas; lo cazó tests/test_ocr_layout.py.)
        altos = [p[2] for p in palabras if p[2] > 0]
        umbral = (sum(altos) / len(altos)) * 1.5 if altos else 1e9
        trozos, fin_previo = [], None
        for left, width, _alto, conf, txt in palabras:
            if conf >= 0:
                confs.append(conf)
            if fin_previo is not None and (left - fin_previo) > umbral:
                trozos.append("  ·  ")
            elif trozos:
                trozos.append(" ")
            trozos.append(txt)
            fin_previo = left + width
        if bloque_previo is not None and clave[0] != bloque_previo:
            fuera.append("")          # línea en blanco entre bloques: conserva la estructura
        bloque_previo = clave[0]
        fuera.append("".join(trozos))
    media = round(sum(confs) / len(confs), 1) if confs else 0.0
    return "\n".join(fuera).strip(), media, len(confs)


CONF_MINIMA = 60.0   # por debajo de esto, tesseract está adivinando: la página se marca


def ocr_pdf(pdf: Path) -> str:
    """Rasteriza cada página (pdftoppm) y la OCRea respetando columnas y celdas.

    Cada página lleva su confianza en la cabecera, y por debajo de CONF_MINIMA se marca con un
    aviso explícito: antes, una página ilegible producía un sidecar indistinguible de uno bueno,
    y `kb.py` indexaba la basura sin que nadie se enterara.
    """
    out = []
    with tempfile.TemporaryDirectory() as td:
        base = os.path.join(td, "pg")
        _run(["pdftoppm", "-r", str(DPI), "-png", str(pdf), base])
        pages = sorted(Path(td).glob("pg*.png"))
        for i, pg in enumerate(pages, 1):
            r = _run(["tesseract", str(pg), "stdout", "-l", LANGS, "tsv"])
            txt, conf, n = _tsv_a_texto(r.stdout or "")
            if not txt:
                continue
            cab = f"\n\n----- página {i} · confianza {conf:.0f}/100 · {n} palabras -----"
            if conf < CONF_MINIMA:
                cab += ("\n⚠️ OCR POCO FIABLE: por debajo de %.0f de confianza. No cites cifras de "
                        "esta página sin mirar el original." % CONF_MINIMA)
            out.append(f"{cab}\n{txt}")
    return "".join(out).strip()


def is_scanned_pdf(pdf: Path) -> bool:
    return pdf_text_layer_chars(pdf) < TEXT_LAYER_MIN_CHARS


def sidecar_for(src: Path) -> Path:
    return src.with_suffix(src.suffix + ".ocr.txt")


def needs_ocr(src: Path):
    """Devuelve (True, motivo) si hay que OCRear, o (False, motivo) si no."""
    sc = sidecar_for(src)
    if sc.exists() and sc.stat().st_mtime >= src.stat().st_mtime:
        return (False, "ya OCReado (sidecar al día)")
    ext = src.suffix.lower()
    if ext in IMAGE_EXTS:
        return (True, "imagen")
    if ext == ".pdf":
        if is_scanned_pdf(src):
            return (True, "PDF escaneado (sin capa de texto)")
        return (False, "PDF con texto (kb.py ya lo lee)")
    return (False, "no es imagen ni PDF")


def collect(dirs):
    files = []
    for d in dirs:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.is_file() and (p.suffix.lower() in IMAGE_EXTS or p.suffix.lower() == ".pdf"):
                if p.name.endswith(".ocr.txt"):
                    continue
                if p.suffix.lower() in IMAGE_EXTS and not IMAGES:
                    continue  # imágenes solo con --images (suelen ser imagen médica, no texto)
                low = str(p).lower()
                if any(part in low for part in EXCLUDE_PARTS):
                    continue  # imagen médica / render / visor: no es texto
                files.append(p)
    return files


def main():
    # Carpetas objetivo
    dirs = []
    args = sys.argv[1:]
    while "--dir" in args:
        i = args.index("--dir")
        dirs.append(Path(os.path.expanduser(args[i + 1])))
        args = args[:i] + args[i + 2:]
    if not dirs:
        dirs = DEFAULT_DIRS

    # Comprobaciones de entorno (fail-loud, no a medias)
    missing = [b for b in ("tesseract", "pdftoppm") if not _have(b)]
    if missing:
        print("✗ Falta(n) binario(s) local(es): %s. Instala con Homebrew "
              "(brew install tesseract tesseract-lang poppler)." % ", ".join(missing))
        return 1

    todo, skip = [], []
    for src in collect(dirs):
        do, why = needs_ocr(src)
        (todo if do else skip).append((src, why))

    print("🔒 OCR LOCAL (sin red) · carpetas: %s" % ", ".join(str(d) for d in dirs))
    print("   a OCRear: %d · se saltan: %d" % (len(todo), len(skip)))
    print("   modo: %s\n" % ("APPLY (escribe sidecars)" if APPLY else "DRY-RUN (no escribe nada)"))

    for src, why in todo[:40]:
        rel = src.name
        print("  · %-55s [%s]" % (rel[:55], why))
    if len(todo) > 40:
        print("  … y %d más" % (len(todo) - 40))

    if not APPLY:
        print("\n(DRY-RUN) Repite con --apply para escribir los .ocr.txt al lado de cada original.")
        return 0

    done = 0
    for src, why in todo:
        try:
            text = ocr_image(src) if src.suffix.lower() in IMAGE_EXTS else ocr_pdf(src)
        except Exception as e:  # nunca dejar a medias en silencio
            print("  ✗ error OCR %s: %s" % (src.name, e))
            continue
        if not text:
            print("  ⚠ sin texto reconocible: %s" % src.name)
            continue
        sc = sidecar_for(src)
        header = ("<!-- OCR LOCAL (tesseract %s) del escaneado '%s'. Generado por "
                  "ocr_informes.py. Dato clínico: NO sacar de local. -->\n\n" % (LANGS, src.name))
        sc.write_text(header + text, encoding="utf-8")
        done += 1
        print("  ✅ %s → %s (%d car.)" % (src.name[:45], sc.name[:45], len(text)))

    print("\n✅ OCReados %d/%d." % (done, len(todo)))

    if REINDEX and done:
        print("↻ Reindexando el RAG…")
        r = _run([str(VENV_PY) if VENV_PY.exists() else sys.executable,
                  str(ROOT / "tools" / "kb.py"), "index"])
        print(r.stdout.strip() or r.stderr.strip())
    elif done:
        print("Para que el RAG los vea: python3 tools/kb.py index")
    return 0


if __name__ == "__main__":
    sys.exit(main())
