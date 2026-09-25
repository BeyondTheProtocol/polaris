#!/usr/bin/env python3
"""tools/cronica.py — el Cronista AUTOMÁTICO: que se va registrando todo, solo.

Capa 1 (esto): captura DETERMINISTA y diaria (sin LLM → no puede alucinar ni
saltarse nada) de los eventos datables del caso. Deja tres salidas:
  · registro máquina  → tools/state/cronica/diario-YYYY-MM-DD.jsonl  (append-only)
  · estado            → tools/state/cronica.json  (al_dia_hasta, ...) para el Observatorio/parte
  · §Bitácora         → cola de CRONICA.md, en una región fenced <!-- CRONICA-AUTO -->
Capa 2 (el agente `periodista`, semanal): TEJE la §Bitácora en las FASES de arriba,
bien contada, y la retira. Que algo esté en la bitácora = REGISTRADO; arriba = NARRADO.

Reglas duras (el muro):
  · ANTI-INYECCIÓN: de los ficheros con contenido externo (dossiers de contactos = DMs)
    se capturan SOLO metadatos (fecha, quién, tipo — todo derivado del NOMBRE del fichero),
    NUNCA el cuerpo. Esta capa no abre el contenido de ningún fichero de fuente externa.
  · EGRESS-CERO: `capturar` y `estado` no hablan con la red. El único punto que puede
    avisar a {{TITULAR}} (`estado --avisar`, solo si la crónica se atrasa) importa `salida`
    de forma perezosa y respeta HALT/silencio. tools/state/ y la crónica son gitignored.
  · IDEMPOTENTE: cada evento tiene una CLAVE estable; lo ya visto (seen.json) no se
    re-escribe. Re-correr no duplica ni corrompe (escritura atómica).

Sin dependencias (stdlib). Patrón de seguimiento.py / cumbre.py.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, date

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")  # casa base SIEMPRE: la fuente de verdad y el estado vivo viven ahí, nunca en un worktree
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
FUENTE = os.path.join(REPO, "00_FUENTE-DE-VERDAD")

# Salidas
CRONICA = os.path.join(FUENTE, "Cronica-Memorias", "CRONICA.md")
CRONICA_DIR = os.path.join(STATE, "cronica")
STATUS = os.path.join(STATE, "cronica.json")
SEEN = os.path.join(CRONICA_DIR, "seen.json")

# Fuentes deterministas
HOY = os.path.join(FUENTE, "Gestion", "HOY.md")
SEG_JSON = os.path.join(STATE, "seguimiento.json")
CUMBRE_JSON = os.path.join(STATE, "cumbre.json")

# Carpetas con ficheros datados. `externo=True` ⇒ contenido NO confiable: SOLO metadatos.
DIRS_EVENTOS = [
    ("Seguimiento-Contactos", True),   # DMs/digests de terceros → externo
    ("Comunidad", False),
    ("Necesidades", False),
    ("Gestion", False),
]

# Marcadores de la región auto-gestionada dentro de CRONICA.md
MARK_START = "<!-- CRONICA-AUTO:START (no editar a mano: lo gestiona tools/cronica.py; el Cronista lo teje arriba) -->"
MARK_END = "<!-- CRONICA-AUTO:END -->"
BITACORA_CABECERA = (
    "## §Bitácora automática — capturado, pendiente de tejer\n\n"
    "> Registro determinista (sin LLM) de eventos datables. El Cronista (`periodista`) los\n"
    "> teje en las FASES de arriba y los retira de aquí. En la bitácora = **registrado**;\n"
    "> arriba = **narrado**. No editar a mano: esta región la gestiona `tools/cronica.py`.\n"
)

ATRASO_DIAS = 2          # más de esto sin captura = la crónica se está quedando atrás
_FECHA_RE = re.compile(r"(20\d{2})[-_.](\d{2})[-_.](\d{2})")


# ---------- utilidades ----------
def _today():
    return date.today()


def _now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _parse_iso(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _ensure_dirs():
    os.makedirs(CRONICA_DIR, exist_ok=True)


def _write_atomic(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or default
    except Exception:
        return default


def _clave(*parts):
    return ":".join(str(p) for p in parts)


def _h(text):
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:10]


# ---------- cosechadores (cada uno devuelve lista de eventos {fecha,tipo,que,quien,fuente,clave,externo}) ----------
def harvest_git(since):
    """Commits desde `since` (YYYY-MM-DD). Read-only; si no hay git, lista vacía."""
    out = []
    try:
        r = subprocess.run(
            ["git", "-C", REPO, "log", "--since=%s 00:00" % since,
             "--no-merges", "--pretty=format:%h\x1f%ad\x1f%s", "--date=short"],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode != 0:
            return out
        for ln in r.stdout.splitlines():
            if "\x1f" not in ln:
                continue
            h, fecha, subj = ln.split("\x1f", 2)
            subj = subj.strip()
            out.append({
                "fecha": fecha, "tipo": "git", "que": subj, "quien": "sistema",
                "fuente": "git:%s" % h, "clave": _clave("git", h), "externo": False,
            })
    except Exception:
        return out
    return out


def harvest_files(since):
    """Ficheros datados nuevos. De carpetas EXTERNAS: SOLO el nombre (jamás el cuerpo)."""
    out = []
    since_d = _parse_iso(since)
    for sub, externo in DIRS_EVENTOS:
        base = os.path.join(FUENTE, sub)
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for fn in files:
                if not fn.lower().endswith((".md", ".pdf", ".txt", ".docx")):
                    continue
                m = _FECHA_RE.search(fn)
                if not m:
                    continue
                fecha = "%s-%s-%s" % (m.group(1), m.group(2), m.group(3))
                fd = _parse_iso(fecha)
                if since_d and fd and fd < since_d:
                    continue
                stem = os.path.splitext(fn)[0]
                # 'quien' se deriva del nombre (antes del primer guion), NUNCA del contenido.
                quien = re.split(r"[-_]", stem)[0] if externo else "—"
                rel = os.path.relpath(os.path.join(root, fn), REPO)
                out.append({
                    "fecha": fecha, "tipo": "fichero",
                    "que": "nuevo documento: %s" % stem,
                    "quien": quien, "fuente": rel,
                    "clave": _clave("file", rel), "externo": externo,
                })
    return out


def _hoy_fecha(texto):
    # cabecera tipo "# 🗓️ HOY — ... · 21-jun-2026"
    m = re.search(r"(\d{1,2})-([a-z]{3})-(20\d{2})", texto)
    meses = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
             "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}
    if m and m.group(2) in meses:
        return "%s-%02d-%02d" % (m.group(3), meses[m.group(2)], int(m.group(1)))
    return _today().isoformat()


def harvest_hoy():
    """Eventos de las secciones de cierre de HOY.md (✅ RESUELTO). Interno/confiable."""
    out = []
    try:
        with open(HOY, encoding="utf-8") as f:
            texto = f.read()
    except Exception:
        return out
    fecha = _hoy_fecha(texto)
    # Solo la sección de RESUELTO (completados reales que no salen por git).
    seccion = re.split(r"\n##\s", texto)
    for bloque in seccion:
        if bloque.lstrip().startswith(("✅", "RESUELTO")) or "RESUELTO" in bloque[:40]:
            for ln in bloque.splitlines():
                ln = ln.strip()
                if not ln.startswith(("- ", "* ")):
                    continue
                limpio = re.sub(r"[*_`#>]", "", ln[2:]).strip()
                limpio = re.sub(r"\s+", " ", limpio)
                if len(limpio) < 6:
                    continue
                out.append({
                    "fecha": fecha, "tipo": "hoy", "que": limpio, "quien": "—",
                    "fuente": "HOY.md", "clave": _clave("hoy", fecha, _h(limpio)),
                    "externo": False,
                })
    return out


def harvest_state():
    """Hitos del estado determinista (cumbre = dónde estamos; tareas hechas hoy)."""
    out = []
    cumbre = _read_json(CUMBRE_JSON, {})
    if cumbre:
        aqui = cumbre.get("aqui_estamos")
        act = (cumbre.get("actualizado") or "")[:10]
        if aqui and act:
            out.append({
                "fecha": act, "tipo": "estado",
                "que": "foco del caso: %s" % aqui, "quien": "cumbre",
                "fuente": "cumbre.json", "clave": _clave("cumbre", aqui), "externo": False,
            })
    # 14-jul-26: aqui se leia tools/state/tareas.json, el almacen LEGACY que el propio
    # CLAUDE.md declaraba "retirado" desde junio. La cronica lo seguia citando, asi que el
    # sistema publicaba a diario eventos de un almacen muerto — y mientras tanto sus 7
    # tareas vivas (incl. una del frente legal) llevaban 3 semanas sin que nadie las
    # persiguiera. Rescatadas a seguimiento.json (fuente unica) y cortado de raiz.
    return out


def cosechar(since):
    eventos = []
    for fn in (harvest_git, harvest_files):
        eventos += fn(since)
    eventos += harvest_hoy()
    eventos += harvest_state()
    # saneo: fecha válida obligatoria; nada con saltos de línea (cierra inyección de formato)
    out = []
    for e in eventos:
        if not _parse_iso(e.get("fecha")):
            continue
        e["que"] = re.sub(r"\s+", " ", str(e.get("que", ""))).strip()[:280]
        e["quien"] = re.sub(r"\s+", " ", str(e.get("quien", "—"))).strip()[:60]
        out.append(e)
    return out


# ---------- §Bitácora en CRONICA.md ----------
def _linea_bitacora(e):
    return "- **%s · %s** · %s · %s · [auto]" % (
        e["fecha"], e["que"], e["quien"], e["fuente"])


def _fecha_de_linea(ln):
    m = re.match(r"-\s+\*\*(\d{4}-\d{2}-\d{2})", ln)
    return m.group(1) if m else "9999-99-99"


def _bitacora_actualizar(nuevas_lineas):
    """Inserta `nuevas_lineas` en la región fenced de CRONICA.md, ordenadas por fecha, sin duplicar."""
    try:
        with open(CRONICA, encoding="utf-8") as f:
            texto = f.read()
    except Exception:
        texto = "# CRÓNICA\n"
    if MARK_START in texto and MARK_END in texto:
        pre, resto = texto.split(MARK_START, 1)
        cuerpo, post = resto.split(MARK_END, 1)
        existentes = [ln for ln in cuerpo.splitlines() if ln.startswith("- **")]
    else:
        pre, post, existentes = texto.rstrip() + "\n", "\n", []
    vistas = set(existentes)
    todas = existentes + [ln for ln in nuevas_lineas if ln not in vistas]
    todas = sorted(set(todas), key=_fecha_de_linea)
    region = "%s\n\n%s\n%s\n%s\n" % (MARK_START, BITACORA_CABECERA, "\n".join(todas), MARK_END)
    nuevo = "%s\n%s%s" % (pre.rstrip(), region, post if post.strip() else "\n")
    tmp = CRONICA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(nuevo)
    os.replace(tmp, CRONICA)
    return len(todas)


# ---------- comandos ----------
def _since_por_defecto():
    st = _read_json(STATUS, {})
    return st.get("al_dia_hasta") or "2026-06-18"


def capturar(since=None, dry=False):
    _ensure_dirs()
    since = since or _since_por_defecto()
    seen = set(_read_json(SEEN, {}).get("claves", []))
    eventos = cosechar(since)
    frescos = [e for e in eventos if e["clave"] not in seen]
    # dedup intra-tanda por clave
    porclave = {}
    for e in frescos:
        porclave.setdefault(e["clave"], e)
    frescos = sorted(porclave.values(), key=lambda e: (e["fecha"], e["tipo"]))

    resumen = {
        "since": since, "encontrados": len(eventos), "frescos": len(frescos),
        "dry": dry, "por_tipo": {},
    }
    for e in frescos:
        resumen["por_tipo"][e["tipo"]] = resumen["por_tipo"].get(e["tipo"], 0) + 1
    if dry:
        resumen["muestra"] = [_linea_bitacora(e) for e in frescos[:40]]
        return resumen

    # 1) registro máquina (append-only por fecha de evento)
    for e in frescos:
        jp = os.path.join(CRONICA_DIR, "diario-%s.jsonl" % e["fecha"])
        with open(jp, "a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    # 2) §Bitácora visible
    n_bitacora = _bitacora_actualizar([_linea_bitacora(e) for e in frescos])
    # 3) seen
    seen |= {e["clave"] for e in frescos}
    _write_atomic(SEEN, {"claves": sorted(seen)})
    # 4) estado
    hoy = _today().isoformat()
    prev = _read_json(STATUS, {})
    al_dia = max([hoy, prev.get("al_dia_hasta") or "0000-00-00"] + [e["fecha"] for e in frescos])
    _write_atomic(STATUS, {
        "al_dia_hasta": al_dia,
        "ultima_captura": _now_iso(),
        "n_pendientes_narrar": n_bitacora,
        "n_total_registrado": len(seen),
        "fuentes_ok": True,
    })
    resumen["al_dia_hasta"] = al_dia
    resumen["n_pendientes_narrar"] = n_bitacora
    return resumen


def estado(avisar=False):
    st = _read_json(STATUS, {})
    al_dia = st.get("al_dia_hasta")
    d = _parse_iso(al_dia)
    atraso = (_today() - d).days if d else None
    info = {
        "al_dia_hasta": al_dia,
        "ultima_captura": st.get("ultima_captura"),
        "n_pendientes_narrar": st.get("n_pendientes_narrar"),
        "dias_atraso": atraso,
        "atrasada": (atraso is not None and atraso > ATRASO_DIAS),
    }
    if avisar and info["atrasada"]:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import salida
            txt = "🗂️ La crónica lleva %s días sin registrar (al día hasta %s). Algo paró la captura." % (
                atraso, al_dia)
            salida.report_to_titular(txt)
        except Exception as e:
            info["aviso_error"] = repr(e)
    return info


def narrar():
    """Material para el pase del Cronista: la §Bitácora pendiente de tejer."""
    try:
        with open(CRONICA, encoding="utf-8") as f:
            texto = f.read()
    except Exception:
        return {"pendientes": 0, "bitacora": ""}
    if MARK_START in texto and MARK_END in texto:
        cuerpo = texto.split(MARK_START, 1)[1].split(MARK_END, 1)[0]
        lineas = [ln for ln in cuerpo.splitlines() if ln.startswith("- **")]
        return {"pendientes": len(lineas), "bitacora": "\n".join(lineas)}
    return {"pendientes": 0, "bitacora": ""}


def main(argv):
    cmd = argv[0] if argv else "estado"
    if cmd in ("-h", "--help"):
        print(__doc__)
        return 0
    if cmd == "capturar":
        since = None
        if "--since" in argv:
            since = argv[argv.index("--since") + 1]
        res = capturar(since=since, dry="--dry" in argv)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    if cmd == "estado":
        print(json.dumps(estado(avisar="--avisar" in argv), ensure_ascii=False, indent=2))
        return 0
    if cmd == "narrar":
        print(json.dumps(narrar(), ensure_ascii=False, indent=2))
        return 0
    print("uso: cronica.py [capturar [--since FECHA] [--dry] | estado [--avisar] | narrar]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
