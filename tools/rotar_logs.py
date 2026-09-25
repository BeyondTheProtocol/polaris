#!/usr/bin/env python3
"""tools/rotar_logs.py — higiene de logs y JSONL del sistema (Fase 3c).

Rota/trunca los logs de launchd y mueve/borra los JSONL de estado viejos.
Determinista, $0, sin LLM. No toca NADA del día de hoy.

Targets:
  · tools/launchd/logs/*.{out,err}     → trunca si > MAX_LOG_MB; nunca borra (logs de daemons)
  · tools/state/observabilidad/*.jsonl → mueve a archive/ o borra si > MAX_DIAS_OBS días
  · tools/state/borde/ledger-*.jsonl   → igual, MAX_DIAS_BORDE
  · tools/state/outbox/audit-*.jsonl   → igual, MAX_DIAS_OUTBOX

Por defecto NO borra ni mueve — solo informa (modo dry-run implícito). Para actuar:
  python3 tools/rotar_logs.py --run

CLI:
  python3 tools/rotar_logs.py              # informa sin tocar nada (dry-run por defecto)
  python3 tools/rotar_logs.py --dry-run    # ídem, explícito
  python3 tools/rotar_logs.py --run        # actúa (mueve a archive/, trunca)
  python3 tools/rotar_logs.py --run --borrar  # borra en vez de archivar (permanente)

Muro: el BORRADO de ledger/audit NUNCA se automatiza. Excepcion (15-jul-26, OK de {{TITULAR}}):
existe com.btp.rotar-logs mensual configurado para NO tocar trazas de auditoria
(BTP_ROT_DIAS_BORDE/OUTBOX=99999 => nunca caducan); solo trunca logs y archiva observabilidad.
JAMAS usar --borrar desde un daemon. A mano sigue valiendo todo.
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
TOOLS = os.path.join(REPO, "tools")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(TOOLS, "state")
LOGS_DIR = os.environ.get("BTP_LOGS_DIR") or os.path.join(TOOLS, "launchd", "logs")
ARCHIVE = os.path.join(STATE, "archive")

# Umbrales (ajustables por env).
MAX_LOG_MB      = float(os.environ.get("BTP_ROT_MAX_LOG_MB",      "10"))    # trunca .out/.err a partir de aquí
MAX_DIAS_OBS    = int(os.environ.get("BTP_ROT_DIAS_OBS",          "14"))    # observabilidad JSONL
MAX_DIAS_BORDE  = int(os.environ.get("BTP_ROT_DIAS_BORDE",        "30"))    # borde/ledger-*.jsonl
MAX_DIAS_OUTBOX = int(os.environ.get("BTP_ROT_DIAS_OUTBOX",       "30"))    # outbox/audit-*.jsonl
# Tope de archivado/borrado por pasada (evita inundar en un run sobre un sistema lleno).
MAX_POR_PASADA  = int(os.environ.get("BTP_ROT_MAX_POR_PASADA",    "200"))


def _hoy():
    return datetime.now().date().isoformat()


def _fecha_de_nombre(fname):
    """Extrae la fecha YYYY-MM-DD del nombre de un fichero (p.ej. 'observabilidad-2026-06-01.jsonl').
    Devuelve None si no se puede extraer."""
    import re
    m = re.search(r"(\d{4}-\d{2}-\d{2})", fname)
    return m.group(1) if m else None


def _es_de_hoy(fname):
    fecha = _fecha_de_nombre(fname)
    return fecha == _hoy()


def _es_viejo(fname, max_dias):
    """True si la fecha del nombre es anterior al corte (hoy - max_dias días)."""
    fecha = _fecha_de_nombre(fname)
    if not fecha:
        return False
    try:
        corte = (datetime.now() - timedelta(days=max_dias)).date().isoformat()
        return fecha < corte
    except Exception:
        return False


def _tam_mb(path):
    try:
        return os.path.getsize(path) / (1024 * 1024)
    except OSError:
        return 0.0


def _truncar(path, dry_run, informe):
    """Trunca un log .out/.err a 0 bytes (descarta el histórico viejo para liberar espacio).
    El fichero queda vacío pero existente → launchd puede seguir escribiendo."""
    tam = _tam_mb(path)
    informe.append({"accion": "truncar", "path": path, "tam_mb": round(tam, 2), "dry": dry_run})
    if not dry_run:
        try:
            with open(path, "w", encoding="utf-8"):
                pass   # abre en modo escritura y cierra: deja vacío
        except OSError as e:
            informe[-1]["error"] = str(e)


def _archivar_o_borrar(path, dry_run, borrar, informe):
    """Mueve a archive/ o borra. NUNCA toca el fichero del día de hoy."""
    fname = os.path.basename(path)
    accion = "borrar" if borrar else "archivar"
    informe.append({"accion": accion, "path": path, "dry": dry_run})
    if dry_run:
        return
    try:
        if borrar:
            os.remove(path)
        else:
            os.makedirs(ARCHIVE, exist_ok=True)
            dest = os.path.join(ARCHIVE, fname)
            # Si ya existe en archive (pasadas anteriores), añade timestamp para no sobreescribir.
            if os.path.exists(dest):
                dest = dest + ".%d" % int(time.time())
            os.replace(path, dest)
    except OSError as e:
        informe[-1]["error"] = str(e)


# ─── Rotadores por categoría ──────────────────────────────────────────────────

def rotar_logs_launchd(dry_run, informe):
    """Trunca .out/.err que superen MAX_LOG_MB. NUNCA borra estos ficheros."""
    if not os.path.isdir(LOGS_DIR):
        return
    for fname in os.listdir(LOGS_DIR):
        if not (fname.endswith(".out") or fname.endswith(".err")):
            continue
        path = os.path.join(LOGS_DIR, fname)
        if _tam_mb(path) > MAX_LOG_MB:
            _truncar(path, dry_run, informe)


def rotar_jsonl(subdir, patron, max_dias, dry_run, borrar, informe, contador):
    """Archiva o borra JSONL de estado viejos bajo STATE/<subdir>/."""
    d = os.path.join(STATE, subdir)
    if not os.path.isdir(d):
        return contador
    for fname in sorted(os.listdir(d)):
        if not fname.endswith(".jsonl"):
            continue
        if not any(fname.startswith(p) for p in patron):
            continue
        if _es_de_hoy(fname):
            continue   # NUNCA el día de hoy
        if not _es_viejo(fname, max_dias):
            continue
        if contador >= MAX_POR_PASADA:
            informe.append({"accion": "tope_pasada", "subdir": subdir, "dry": dry_run})
            break
        _archivar_o_borrar(os.path.join(d, fname), dry_run, borrar, informe)
        contador += 1
    return contador


def run(dry_run=True, borrar=False):
    """Punto de entrada principal. dry_run=True por defecto (no toca nada)."""
    informe = []
    contador = 0

    # 1. Logs de launchd (trunca, nunca borra)
    rotar_logs_launchd(dry_run, informe)

    # 2. JSONL de observabilidad
    contador = rotar_jsonl(
        "observabilidad", ["observabilidad-"],
        MAX_DIAS_OBS, dry_run, borrar, informe, contador)

    # 3. Ledger del borde
    contador = rotar_jsonl(
        "borde", ["ledger-"],
        MAX_DIAS_BORDE, dry_run, borrar, informe, contador)

    # 4. Audit del outbox
    contador = rotar_jsonl(
        os.path.join("outbox"), ["audit-"],
        MAX_DIAS_OUTBOX, dry_run, borrar, informe, contador)

    # 5. Registro del ciclo de vida de los agentes (14-sep-2026, tools/ciclo_agentes.py). Solo lo que
    # escribe Polaris en su estado; los transcripts de ~/.claude/projects NO se tocan aquí.
    contador = rotar_jsonl(
        "agentes", ["ciclo-"],
        int(os.environ.get("BTP_ROT_DIAS_AGENTES", "30")), dry_run, borrar, informe, contador)

    return informe


def _imprimir(informe, dry_run):
    if not informe:
        print("rotar_logs: nada que rotar/truncar en esta pasada.")
        return
    acc = {"truncar": 0, "archivar": 0, "borrar": 0, "tope_pasada": 0, "error": 0}
    for it in informe:
        a = it.get("accion", "?")
        acc[a] = acc.get(a, 0) + 1
        if "error" in it:
            acc["error"] += 1
        linea = "[%s%s]  %s" % (
            a.upper(), " DRY" if dry_run else "",
            it.get("path") or it.get("subdir") or "",
        )
        if "tam_mb" in it:
            linea += "  (%.1f MB)" % it["tam_mb"]
        if "error" in it:
            linea += "  ERROR: %s" % it["error"]
        print(linea)
    print("Resumen: %d truncar · %d archivar · %d borrar · %d errores%s" % (
        acc["truncar"], acc["archivar"], acc["borrar"], acc["error"],
        "  (DRY-RUN — sin cambios reales)" if dry_run else ""))


def main(argv=None):
    global MAX_LOG_MB, MAX_DIAS_OBS, MAX_DIAS_BORDE, MAX_DIAS_OUTBOX   # pueden sobreescribirse por CLI
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(
        description="Higiene de logs y JSONL de Polaris (Fase 3c). "
                    "Por defecto solo informa (dry-run); usa --run para actuar.")
    p.add_argument("--run",      action="store_true", help="actúa (mueve/trunca); sin esto = dry-run")
    p.add_argument("--dry-run",  action="store_true", help="solo informa (por defecto)")
    p.add_argument("--borrar",   action="store_true", help="borra en vez de archivar (permanente)")
    p.add_argument("--max-log-mb",    type=float, default=MAX_LOG_MB,      help="trunca .out/.err a partir de X MB")
    p.add_argument("--dias-obs",      type=int,   default=MAX_DIAS_OBS,    help="retención observabilidad JSONL (días)")
    p.add_argument("--dias-borde",    type=int,   default=MAX_DIAS_BORDE,  help="retención ledger borde (días)")
    p.add_argument("--dias-outbox",   type=int,   default=MAX_DIAS_OUTBOX, help="retención audit outbox (días)")
    args = p.parse_args(argv)

    # Sobreescribir umbrales si se pasan por CLI
    MAX_LOG_MB      = args.max_log_mb
    MAX_DIAS_OBS    = args.dias_obs
    MAX_DIAS_BORDE  = args.dias_borde
    MAX_DIAS_OUTBOX = args.dias_outbox

    dry_run = not args.run   # por defecto dry-run (solo actúa con --run)
    informe = run(dry_run=dry_run, borrar=args.borrar)
    _imprimir(informe, dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
