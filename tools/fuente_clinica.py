#!/usr/bin/env python3
"""tools/fuente_clinica.py — cotejar una afirmación contra un informe de la bóveda, de verdad.

POR QUÉ EXISTE (24-sep-2026, auditoría externa de Marcos Gorgojo, hallazgo 1.1). El panel de
decisión de alto riesgo sellaba un veredicto como «verificado» si su `contra_fuente` tenía FORMA
de puntero a `_PRIVADO_CLINICO`. Nadie abría el fichero. Lo reproduje antes de tocar nada: dos
lentes del mismo agente, «comprobadas» contra `_PRIVADO_CLINICO/informe-que-no-existe-2099.md`,
salían entregables, con confianza alta y cero bloqueos.

Lo que este módulo hace es lo único que un modelo no puede fingir escribiendo un JSON: RESOLVER
el puntero a un fichero real dentro de la bóveda, y comprobar que el FRAGMENTO que el comprobador
dice haber visto está DENTRO de ese fichero.

  cotejar(ref, fragmento=None, quien="?") -> dict
      ok, estado, motivo, ruta (la referencia tal cual llegó), sha256 (del fichero),
      sha256_fragmento (de su forma normalizada), identidad (veredicto de filiación)

  Estados: confirmada · existe (sin fragmento pedido) · no_existe · vacia · fuera_de_boveda ·
           no_legible · no_cotejable · fragmento_corto · fragmento_ausente · identidad_ajena ·
           sin_ventanilla. Solo `confirmada` y `existe` son ok. Todo lo demás es fail-closed.

MURO
  · La lectura pasa por la VENTANILLA (`lector_clinico`): cada cotejo deja su línea en el
    registro de accesos clínicos y se comprueba de quién es el informe (estar en su carpeta no
    prueba que sea suyo). Sin ventanilla importable → `sin_ventanilla`, no se lee nada.
  · El fragmento y el contenido NO salen de aquí: el resultado lleva hashes y un sí/no. Quien
    necesite el texto va por `lector_clinico.py` y deja su propia línea.
  · Contención por `realpath`: ni `..` ni un enlace simbólico sacan el cotejo de la bóveda.

LÍMITE QUE NO SE ESCONDE. Esto acredita PROCEDENCIA, no fidelidad: prueba que el fragmento
existe en ese informe, no que sostenga la afirmación del veredicto. Y todo corre con el mismo
usuario de macOS, así que un proceso decidido podría escribir un fichero a medida en la bóveda.
Lo que ya no se puede es sellar una fuente sin abrirla y encontrar dentro lo que se cita. La
fidelidad semántica sigue necesitando revisión clínica humana.
"""
import hashlib
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _casa  # noqa: E402

SEGMENTO = "_PRIVADO_CLINICO"
# Dónde vive la bóveda en casa base: la misma ruta que usa `archivar.py` (clave CLINICAL).
BOVEDA_REL = os.path.join("00_FUENTE-DE-VERDAD", "01 · Tratamiento", SEGMENTO)
MIN_FRAGMENTO = 15            # caracteres normalizados; «HER2» casa con cualquier informe
TEXTO = (".md", ".txt", ".json", ".csv", ".tsv", ".html", ".htm", ".xml", ".log")

# Inyectables en tests. None → la ventanilla real (`lector_clinico`).
_IDENTIDAD = None             # (texto, ruta) -> (veredicto, detalle)
_LOG = None                   # (agente, resultado, ruta) -> None
# Solo si la ventanilla no trae su política (mismo contenido que `identidad_paciente.SIRVE`).
_SIRVE_DEFECTO = {"coincide": True, "parcial": True, "no_consta": True, "no_textual": True,
                  "ambiguo": True, "otro_paciente": False, "sin_overlay": False}


def _ventanilla():
    """El módulo de la ventanilla auditada, o None si no se puede cargar."""
    try:
        import lector_clinico
        return lector_clinico
    except Exception:
        return None


def bovedas():
    """Carpetas `_PRIVADO_CLINICO` contra las que se resuelve. BTP_BOVEDA_CLINICA solo para tests."""
    env = os.environ.get("BTP_BOVEDA_CLINICA")
    if env:
        return [env]
    return [os.path.join(_casa.casa_base(), BOVEDA_REL)]


def punteros(texto):
    """Punteros a la bóveda dentro de un texto libre («ref; _PRIVADO_CLINICO/x.pdf; PMID:1»).
    Se parte por `;` y saltos de línea, no por espacios: las rutas de la bóveda los llevan
    («01 · Tratamiento»). Si delante del segmento hay una ruta absoluta, se toma entera."""
    out = []
    for trozo in re.split(r"[;\n]", texto or ""):
        i = trozo.find(SEGMENTO)
        if i < 0:
            continue
        m = re.search(r"(?:^|\s)([/~])", trozo[:i])
        ini = m.start(1) if m else i
        p = trozo[ini:].strip().strip("`'\"()[]")
        if p:
            out.append(p)
    return out


def _candidatas(ref):
    r = os.path.expanduser(ref.strip().strip("`'\""))
    if os.path.isabs(r):
        return [r]
    partes = re.split(r"[\\/]", r)
    idx = [i for i, p in enumerate(partes) if p.upper() == SEGMENTO]
    if not idx:
        return []
    resto = partes[idx[0] + 1:]
    return [os.path.join(b, *resto) for b in bovedas()]


def _dentro(real):
    for b in bovedas():
        try:
            rb = os.path.realpath(b)
        except Exception:
            continue
        if real == rb or real.startswith(rb + os.sep):
            return True
    return False


def normaliza(s):
    """Sin tildes, minúsculas, y todo lo que no es letra/número/% colapsado a un espacio."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().replace("%", " % ")
    return re.sub(r"[^a-z0-9%]+", " ", s).strip()      # «90%» y «90 %» son lo mismo


def huella_fragmento(fragmento):
    return hashlib.sha256(normaliza(fragmento).encode("utf-8")).hexdigest()


def _texto_de(real, ventanilla):
    """(texto|None, motivo). Texto plano tal cual; PDF por su sidecar OCR o por pdftotext."""
    ext = os.path.splitext(real)[1].lower()
    if ext in TEXTO:
        with open(real, "rb") as f:
            return f.read().decode("utf-8", errors="replace"), "texto"
    if ext != ".pdf":
        return None, "formato %s sin capa de texto que cotejar" % (ext or "sin extensión")
    sidecar = real + ".ocr.txt"
    if os.path.lexists(sidecar):
        rs = os.path.realpath(sidecar)
        if not os.path.islink(sidecar) and _dentro(rs) and os.path.isfile(rs):
            with open(rs, "rb") as f:
                return f.read().decode("utf-8", errors="replace"), "sidecar OCR"
    exe = ventanilla._pdftotext() if hasattr(ventanilla, "_pdftotext") else None
    if not exe:
        return None, "PDF sin sidecar OCR y sin pdftotext"
    import subprocess
    try:
        r = subprocess.run([exe, "-q", "-layout", real, "-"], capture_output=True, timeout=120)
    except Exception as e:
        return None, "pdftotext falló (%s)" % type(e).__name__
    if r.returncode != 0 or not r.stdout.strip():
        return None, ("PDF sin capa de texto (escaneado): sácale el OCR por la ventanilla "
                      "(`lector_clinico.py procesa ocr_informes`) y vuelve a cotejar")
    return r.stdout.decode("utf-8", errors="replace"), "pdftotext"


def cotejar(ref, fragmento=None, quien="?"):
    base = {"ok": False, "ruta": ref, "sha256": None, "sha256_fragmento": None, "identidad": None}

    def fin(estado, motivo, **kw):
        d = dict(base, estado=estado, motivo=motivo, **kw)
        d["ok"] = estado in ("confirmada", "existe")
        return d

    if fragmento is not None:
        base["sha256_fragmento"] = huella_fragmento(fragmento)
    cands = _candidatas(ref or "")
    if not cands:
        return fin("fuera_de_boveda", "«%s» no apunta a %s" % (ref, SEGMENTO))
    real = None
    for c in cands:
        rc = os.path.realpath(c)
        if not _dentro(rc):
            return fin("fuera_de_boveda", "«%s» resuelve fuera de la bóveda (¿`..` o un enlace?)" % ref)
        if os.path.isfile(rc):
            real = rc
            break
    if real is None:
        return fin("no_existe", "«%s» no existe en la bóveda: un puntero a nada no se sella" % ref)
    try:
        if os.path.getsize(real) == 0:
            return fin("vacia", "«%s» está vacío: no hay nada contra lo que cotejar" % ref)
        with open(real, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
    except OSError as e:
        return fin("no_legible", "«%s» no se puede leer (%s)" % (ref, type(e).__name__))
    base["sha256"] = sha
    ventanilla = _ventanilla()
    log = _LOG or (getattr(ventanilla, "_log", None) if ventanilla else None)
    agente = "fuente_clinica:%s" % quien
    if fragmento is None:
        # Existencia + huella: metadato, no contenido. Se apunta igual, si hay dónde.
        if log:
            log(agente, "COTEJO-existe", real)
        return fin("existe", "existe en la bóveda (sin fragmento que cotejar)")

    ident = _IDENTIDAD
    if ident is None and ventanilla is not None and hasattr(ventanilla, "_verificar_identidad"):
        ident = ventanilla._verificar_identidad
    if log is None or ident is None:
        return fin("sin_ventanilla", "sin la ventanilla auditada (`lector_clinico`) no se lee dato clínico")

    frag = normaliza(fragmento)
    if len(frag) < MIN_FRAGMENTO:
        log(agente, "COTEJO-fragmento_corto", real)
        return fin("fragmento_corto", "fragmento de %d caracteres: casaría con cualquier informe "
                   "(mínimo %d)" % (len(frag), MIN_FRAGMENTO))
    try:
        texto, via = _texto_de(real, ventanilla)
    except OSError as e:
        log(agente, "COTEJO-no_legible", real)
        return fin("no_legible", "«%s» no se puede leer (%s)" % (ref, type(e).__name__))
    if texto is None:
        log(agente, "COTEJO-no_cotejable", real)
        return fin("no_cotejable", "«%s»: %s" % (ref, via))
    veredicto, _detalle = ident(texto, real)
    base["identidad"] = veredicto
    # Qué veredictos de filiación valen: los de la ventanilla (una sola política, no dos).
    mod_id = getattr(ventanilla, "_ID", None) if ventanilla is not None else None
    servibles = getattr(mod_id, "SIRVE", None) or _SIRVE_DEFECTO
    if not servibles.get(veredicto, False):
        log(agente, "COTEJO-identidad-%s" % veredicto, real)
        return fin("identidad_ajena", "«%s»: no se acredita que el informe sea suyo (%s)" % (ref, veredicto))
    if frag not in normaliza(texto):
        log(agente, "COTEJO-fragmento_ausente", real)
        return fin("fragmento_ausente", "el fragmento citado NO está en «%s» (%s)" % (ref, via))
    log(agente, "COTEJO-confirmada-%s" % veredicto, real)
    return fin("confirmada", "fragmento encontrado en «%s» (%s)" % (ref, via))


if __name__ == "__main__":
    import json
    if len(sys.argv) < 2:
        print('uso: fuente_clinica.py "<_PRIVADO_CLINICO/…>" ["<fragmento>"] [--quien X]')
        sys.exit(2)
    a = sys.argv[1:]
    quien = a[a.index("--quien") + 1] if "--quien" in a and a.index("--quien") + 1 < len(a) else "cli"
    pos = [x for i, x in enumerate(a) if x != "--quien" and (i == 0 or a[i - 1] != "--quien")]
    r = cotejar(pos[0], pos[1] if len(pos) > 1 else None, quien=quien)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    sys.exit(0 if r["ok"] else 1)
