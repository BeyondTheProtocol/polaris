#!/usr/bin/env python3
"""tools/calendar_write.py — escritor de eventos en Google Calendar (API · Service Account).

Hermano de ESCRITURA del lector `calendar_sync.py` (que es solo-lectura). Permite que el
sistema **añada citas** al calendario de {{TITULAR}} de forma autónoma (regla de {{TITULAR}} 26/6:
«lo del calendario lo deberías hacer solo sin preguntar»).

Diseño:
  · Reusa la MISMA Service Account del Llavero que el lector (`btp-gcal-sa-key`), pero con
    scope de ESCRITURA `calendar.events`. El lector sigue intacto y read-only.
  · PRERREQUISITO (su mano, una vez): el calendario destino debe estar compartido con el
    email de la SA con permiso «Hacer cambios en los eventos». Sin eso, la API devuelve 403
    y esta tool lo dice claro (no finge que escribió).
  · Idempotente: marca cada evento que crea con extendedProperties.private.btp_key = hash
    estable de (summary|start|calendar). Antes de insertar, busca por esa marca y, si ya
    existe, NO duplica (devuelve el existente). Re-ejecutar es seguro.
  · Muro: escribe SOLO en el calendario de la propia {{TITULAR}} (su infra), nunca manda nada a
    terceros. El contenido del evento es de ella / del sistema, no texto externo obedecido.

Uso:
  python3 tools/calendar_write.py --summary "Cita X" \
      --start 2026-06-30T19:00:00 --end 2026-06-30T19:30:00 --tz Europe/Madrid \
      [--desc "..."] [--location "..."] [--calendar titular.mgp@gmail.com] [--dry-run]
  python3 tools/calendar_write.py --selftest   # sin red: valida cuerpo/clave idempotente
"""
import argparse
import hashlib
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore", category=FutureWarning, module=r"google.*")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
import _secrets  # noqa: E402 — Llavero

SA_KEYCHAIN = "btp-gcal-sa-key"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
DEFAULT_CALENDAR = "titular.mgp@gmail.com"   # MISMO calendario que LEE calendar_sync.py (8-jul): antes
# apuntaba a titular@ (otra cuenta) → el escritor creaba eventos donde el lector nunca miraba, así que
# nunca se reflejaban en el parte. Debe coincidir con calendar_sync.config.json. Requiere que este
# calendario esté compartido con la SA con permiso de ESCRITURA ("Hacer cambios en eventos").


# ── Credenciales ──────────────────────────────────────────────────────────────────
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
        raise RuntimeError("la clave del Llavero (%s) no es JSON válido: %r" % (SA_KEYCHAIN, e))


def sa_email():
    """El client_email de la SA (identificador, no secreto) — el que hay que compartir."""
    return _sa_info().get("client_email", "")


def _service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_info(_sa_info(), scopes=SCOPES)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


# ── Construcción del evento ────────────────────────────────────────────────────────
def btp_key(summary, start, calendar_id):
    """Clave estable para dedupe (idempotencia)."""
    base = "%s|%s|%s" % ((summary or "").strip(), (start or "").strip(), (calendar_id or "").strip())
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


def build_body(summary, start, end, tz, desc=None, location=None, calendar_id=DEFAULT_CALENDAR):
    if not (summary and start and end):
        raise ValueError("summary, start y end son obligatorios")
    body = {
        "summary": summary,
        "start": {"dateTime": start, "timeZone": tz},
        "end": {"dateTime": end, "timeZone": tz},
        "extendedProperties": {"private": {"btp_key": btp_key(summary, start, calendar_id)}},
    }
    if desc:
        body["description"] = desc
    if location:
        body["location"] = location
    return body


# ── Alta idempotente ───────────────────────────────────────────────────────────────
def add_event(summary, start, end, tz="Europe/Madrid", desc=None, location=None,
              calendar_id=DEFAULT_CALENDAR, dry_run=False):
    """Crea el evento si no existe ya (por btp_key). Devuelve dict con id/htmlLink/estado."""
    body = build_body(summary, start, end, tz, desc, location, calendar_id)
    if dry_run:
        return {"estado": "dry-run", "body": body, "calendar": calendar_id}

    from googleapiclient.errors import HttpError
    svc = _service()
    key = body["extendedProperties"]["private"]["btp_key"]
    # ¿ya existe? (dedupe)
    try:
        existing = svc.events().list(
            calendarId=calendar_id,
            privateExtendedProperty="btp_key=%s" % key,
            maxResults=1, singleEvents=True,
        ).execute().get("items", [])
        if existing:
            ev = existing[0]
            return {"estado": "ya-existia", "id": ev.get("id"),
                    "htmlLink": ev.get("htmlLink"), "calendar": calendar_id}
        ev = svc.events().insert(calendarId=calendar_id, body=body).execute()
        return {"estado": "creado", "id": ev.get("id"),
                "htmlLink": ev.get("htmlLink"), "calendar": calendar_id}
    except HttpError as e:
        code = getattr(e, "status_code", None) or getattr(getattr(e, "resp", None), "status", None)
        if str(code) in ("403", "404"):
            raise RuntimeError(
                "Google rechazó la escritura (HTTP %s) en '%s'. Casi seguro: el calendario "
                "NO está compartido con la Service Account con permiso de ESCRITURA.\n"
                "Arréglalo: Google Calendar (web) → Configuración del calendario '%s' → "
                "«Compartir con determinadas personas» → añade %s con «Hacer cambios en los "
                "eventos»." % (code, calendar_id, calendar_id, sa_email())) from e
        raise


# ── CLI / selftest ──────────────────────────────────────────────────────────────────
def _selftest():
    b1 = build_body("Cita X", "2026-06-30T19:00:00", "2026-06-30T19:30:00", "Europe/Madrid",
                    desc="d", location="Meet")
    assert b1["summary"] == "Cita X" and b1["start"]["timeZone"] == "Europe/Madrid"
    k = b1["extendedProperties"]["private"]["btp_key"]
    assert k == btp_key("Cita X", "2026-06-30T19:00:00", DEFAULT_CALENDAR), "btp_key inestable"
    # dry-run no toca la red
    r = add_event("Cita X", "2026-06-30T19:00:00", "2026-06-30T19:30:00", dry_run=True)
    assert r["estado"] == "dry-run" and r["body"]["summary"] == "Cita X"
    try:
        build_body("", "x", "y", "Europe/Madrid")
    except ValueError:
        pass
    else:
        raise AssertionError("debería exigir summary")
    print("selftest OK")


def main():
    ap = argparse.ArgumentParser(description="Añade un evento al Google Calendar de {{TITULAR}} (SA).")
    ap.add_argument("--summary")
    ap.add_argument("--start", help="ISO local sin zona, ej. 2026-06-30T19:00:00")
    ap.add_argument("--end", help="ISO local sin zona, ej. 2026-06-30T19:30:00")
    ap.add_argument("--tz", default="Europe/Madrid")
    ap.add_argument("--desc")
    ap.add_argument("--location")
    ap.add_argument("--calendar", default=DEFAULT_CALENDAR)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--sa-email", action="store_true", help="imprime el email de la SA y sale")
    a = ap.parse_args()

    if a.selftest:
        _selftest()
        return
    if a.sa_email:
        print(sa_email())
        return
    if not (a.summary and a.start and a.end):
        ap.error("--summary, --start y --end son obligatorios (o usa --selftest)")
    r = add_event(a.summary, a.start, a.end, a.tz, a.desc, a.location, a.calendar, a.dry_run)
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
