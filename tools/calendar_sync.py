#!/usr/bin/env python3
"""tools/calendar_sync.py — puente Google Calendar (API · Service Account) → vigía.

Lee los próximos eventos del/los calendario(s) de {{TITULAR}} vía la API de Google con una
**Service Account** (clave privada en el Llavero, sin caducidad de token) y los vuelca en
el vigía determinista (`seguimiento.json`) como hilos con fecha. Así la agenda deja de ser
un punto ciego: la asistente avisa y prepara lo que toque ANTES de cada cita.

Diseño (ver plan keen-scribbling-fox):
  · SOLO LECTURA (scope calendar.readonly). Datos SOLO en local.
  · `singleEvents=True` → Google expande recurrencias/zonas horarias (sin parser de RRULE).
  · Sumidero = seguimiento.json, reutilizando su formato y `_write_atomic`.
  · Idempotente: en cada pasada BORRA sus propias filas (origen=='calendar') y reescribe la
    ventana actual → sin duplicados ni eventos pasados acumulados.
  · Privacidad/muro: las citas entran como categoria='personal' (NO está en la allowlist del
    espejo de Notion → nunca suben) y privado=True (el título, que puede ser una cita médica,
    NO sale por Telegram; se ve en local). Anti-inyección: el título es texto externo, se
    SANEA, y la severidad nunca sale de la prosa (la calcula seguimiento.py solo de fechas).

Uso:
  python3 tools/calendar_sync.py            # sincroniza (lee Google, escribe el vigía)
  python3 tools/calendar_sync.py --selftest # prueba sin red (prune/idempotencia/privacidad)
"""
import hashlib
import json
import os
import re
import sys
import warnings
from datetime import datetime, timedelta, timezone

# Silencia el aviso EOL de Python 3.9 que emite google-auth (ruido en logs de cron).
warnings.filterwarnings("ignore", category=FutureWarning, module=r"google.*")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
import _secrets          # noqa: E402  — Llavero
import seguimiento as seg  # noqa: E402  — sumidero determinista

STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
CONFIG = os.path.join(STATE, "calendar_sync.config.json")
HEARTBEAT_DIR = os.path.join(STATE, "heartbeat")

SA_KEYCHAIN = "btp-gcal-sa-key"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
ORIGEN = "calendar"                 # marca de las filas que esta tool posee (para el prune)
CATEGORIA = "personal"             # NO está en NOTION_CATEGORIAS_OK → no sube a la nube
MAX_TITULO = 120
DEFAULT_VENTANA_DIAS = 21


# ── Config (no secreta) ─────────────────────────────────────────────────────────
def load_config():
    """Lista de calendarios a leer + ventana. Si no hay config, usa el correo de {{TITULAR}}."""
    cfg = {"calendarios": [{"id": "titular.mgp@gmail.com", "etiqueta": "Personal"}],
           "ventana_dias": DEFAULT_VENTANA_DIAS}
    try:
        with open(CONFIG, encoding="utf-8") as f:
            user = json.load(f) or {}
        if isinstance(user.get("calendarios"), list) and user["calendarios"]:
            cfg["calendarios"] = user["calendarios"]
        if isinstance(user.get("ventana_dias"), int) and user["ventana_dias"] > 0:
            cfg["ventana_dias"] = user["ventana_dias"]
    except FileNotFoundError:
        pass
    except Exception as e:
        print("aviso: config ilegible (%r) → uso valores por defecto" % e, file=sys.stderr)
    return cfg


# ── Credenciales / servicio ──────────────────────────────────────────────────────
def _sa_info():
    """JSON de la Service Account desde el Llavero. Nunca lo imprime."""
    raw = _secrets.get(SA_KEYCHAIN)
    if not raw:
        raise RuntimeError(
            "no encuentro la clave de la Service Account en el Llavero (%s). "
            "Guárdala con: bash tools/setup_keychain.sh" % SA_KEYCHAIN)
    try:
        return json.loads(raw)
    except Exception as e:
        raise RuntimeError("la clave del Llavero (%s) no es un JSON válido: %r" % (SA_KEYCHAIN, e))


def _service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_info(_sa_info(), scopes=SCOPES)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def fetch_events(service, cal_id, dias):
    """Eventos del calendario en la ventana [ahora, ahora+dias], recurrencias expandidas."""
    ahora = datetime.now(timezone.utc)
    time_min = ahora.isoformat()
    time_max = (ahora + timedelta(days=dias)).isoformat()
    eventos, token = [], None
    while True:
        resp = service.events().list(
            calendarId=cal_id, timeMin=time_min, timeMax=time_max,
            singleEvents=True, orderBy="startTime", maxResults=250,
            pageToken=token).execute()
        eventos.extend(resp.get("items", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return eventos


# ── Transformación evento → hilo ──────────────────────────────────────────────────
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _sanitize(s):
    s = _CTRL.sub(" ", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:MAX_TITULO] or "(sin título)"


def _plazo_y_hora(ev):
    """Devuelve (plazo_iso 'YYYY-MM-DD', etiqueta_hora). Soporta evento con hora y de día completo."""
    start = ev.get("start", {}) or {}
    dt = start.get("dateTime")
    if dt:                       # p.ej. '2026-06-22T18:00:00+02:00'
        return dt[:10], dt[11:16] or "—"
    d = start.get("date")        # día completo
    if d:
        return d[:10], "todo el día"
    return None, "—"


def event_to_hilo(ev, etiqueta=""):
    """Construye el dict que consume seguimiento.add_hilo (validado allí)."""
    plazo, hora = _plazo_y_hora(ev)
    titulo = _sanitize(ev.get("summary", ""))
    ev_uid = "%s:%s" % (etiqueta, ev.get("id", titulo))
    hid = "agenda-" + hashlib.sha1(ev_uid.encode("utf-8")).hexdigest()[:12]
    cal = (" · " + etiqueta) if etiqueta else ""
    return {
        "id": hid,
        "titulo": "🗓️ " + titulo + cal,
        "categoria": CATEGORIA,
        "estado": "por_confirmar",         # derivado de fuera (convención anti-inyección)
        "plazo": plazo,
        "siguiente_accion": "%s — preparar lo que tengas que llevar o pasar a esta cita." % hora,
        "privado": True,                    # el título puede ser sensible → no sale por Telegram
        "origen": ORIGEN,
        "fuente": "google calendar",
    }


# ── Sumidero: prune + reescritura en seguimiento.json ──────────────────────────────
def _prune_calendar_rows():
    """Quita las filas que esta tool posee (origen=='calendar') y reescribe el fichero."""
    data = seg.load_seguimiento()
    if data.get("_error"):
        raise RuntimeError(data["_error"])
    antes = data.get("hilos", [])
    data["hilos"] = [h for h in antes if h.get("origen") != ORIGEN]
    data["actualizado"] = datetime.now().strftime("%Y-%m-%d")
    seg._write_atomic(seg.SEG, data)
    return len(antes) - len(data["hilos"])


def write_heartbeat(estado, n):
    try:
        os.makedirs(HEARTBEAT_DIR, exist_ok=True)
        p = os.path.join(HEARTBEAT_DIR, "calendar-sync.json")
        seg._write_atomic(p, {"estado": estado, "citas": n,
                              "ts": datetime.now().strftime("%Y-%m-%d %H:%M")})
    except Exception:
        pass


def sync(events_by_cal=None):
    """Sincroniza. Si events_by_cal se pasa (selftest), no toca la red.
    events_by_cal = lista de (etiqueta, [eventos])."""
    cfg = load_config()
    if events_by_cal is None:
        service = _service()
        events_by_cal = []
        for c in cfg["calendarios"]:
            evs = fetch_events(service, c.get("id"), cfg["ventana_dias"])
            events_by_cal.append((c.get("etiqueta", ""), evs))
    _prune_calendar_rows()
    n = 0
    for etiqueta, evs in events_by_cal:
        for ev in evs:
            hilo = event_to_hilo(ev, etiqueta)
            if not hilo["plazo"]:
                continue                     # sin fecha utilizable → fuera
            seg.add_hilo(hilo)               # sumidero VALIDADO (estado/ISO/upsert por id)
            n += 1
    write_heartbeat("ok", n)
    return n


# ── Selftest sin red ───────────────────────────────────────────────────────────────
def _selftest():
    import tempfile
    tmp = tempfile.mkdtemp(prefix="calsync-")
    seg.SEG = os.path.join(tmp, "seguimiento.json")
    # Semilla: un hilo NO-calendario (debe sobrevivir) + una fila vieja de calendario (debe morir).
    seg._write_atomic(seg.SEG, {"hilos": [
        {"id": "otro-hilo", "titulo": "Hilo operativo", "categoria": "infra",
         "estado": "en_curso", "origen": "manual"},
        {"id": "agenda-vieja", "titulo": "🗓️ Cita de ayer", "categoria": CATEGORIA,
         "estado": "por_confirmar", "plazo": "2020-01-01", "origen": ORIGEN, "privado": True},
    ]})
    fake = [("Personal", [
        {"id": "evt1", "summary": "Clase de inglés", "start": {"dateTime": "2026-06-22T18:00:00+02:00"}},
        {"id": "evt2", "summary": "Cita con\nsaltos", "start": {"date": "2026-06-25"}},
        {"id": "evt3", "summary": "Oncología (dato sensible)", "start": {"dateTime": "2026-06-30T09:00:00+02:00"}},
    ])]
    n1 = sync(events_by_cal=fake)
    n2 = sync(events_by_cal=fake)               # segunda pasada: no debe duplicar
    data = seg.load_seguimiento()
    hilos = data["hilos"]
    ids = [h["id"] for h in hilos]
    cal_rows = [h for h in hilos if h.get("origen") == ORIGEN]

    fallos = []
    if n1 != 3 or n2 != 3:
        fallos.append("conteo inesperado n1=%s n2=%s (esperaba 3)" % (n1, n2))
    if "otro-hilo" not in ids:
        fallos.append("se perdió el hilo NO-calendario")
    if "agenda-vieja" in ids:
        fallos.append("no se purgó la fila de calendario vieja")
    if len(cal_rows) != 3:
        fallos.append("hay %d filas de calendario (esperaba 3 → no idempotente)" % len(cal_rows))
    # Saneado: sin control chars ni saltos en títulos.
    if any(_CTRL.search(h["titulo"]) or "\n" in h["titulo"] for h in cal_rows):
        fallos.append("título sin sanear")
    # Privacidad: ninguna cita sale al export de Notion.
    export_ids = {r.get("id") for r in seg.construir_export("notion")}
    if export_ids & set(ids[:]) and any(i.startswith("agenda-") for i in export_ids):
        fallos.append("¡una cita personal se filtró al export de Notion!")
    if any(i.startswith("agenda-") for i in export_ids):
        fallos.append("export de Notion contiene filas de agenda (debería excluirlas)")
    # Severidad por fecha (no por prosa): la cita pasada/cercana debe puntuar.
    dig = seg.construir_digest("local")
    if "Clase de inglés" not in dig:
        fallos.append("la clase no aparece en el digest local")
    # Telegram: privado=True → el título NO debe salir por Telegram.
    tg = seg.construir_digest("telegram")
    if "Oncología" in tg:
        fallos.append("¡un título sensible salió por el canal Telegram!")

    if fallos:
        print("❌ SELFTEST FALLÓ:")
        for f in fallos:
            print("  ·", f)
        return 1
    print("✅ SELFTEST OK — prune, idempotencia, saneado y privacidad correctos.")
    print("   (sandbox: %s)" % seg.SEG)
    return 0


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv and argv[0] == "--selftest":
        return _selftest()
    try:
        n = sync()
    except Exception as e:
        write_heartbeat("fallo", 0)
        print("calendar_sync: error: %s" % e, file=sys.stderr)
        return 1
    print("ok: %d cita(s) sincronizada(s) al vigía." % n)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
