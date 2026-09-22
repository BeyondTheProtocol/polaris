#!/usr/bin/env python3
"""tools/postdicom.py — Subida REUTILIZABLE de estudios DICOM a tu PostDICOM (estándar DICOMweb / STOW-RS).

Por qué existe: cada vez que entra imagen nueva (un CD del hospital, una RM, un PET) hay que
meterla en tu PostDICOM para poder compartirla con tu equipo (p. ej. Fred Hutch / Seattle). En vez
de arrastrar a mano cada vez, esta tool sube un estudio por API, **sin duplicar** lo que ya está, y
queda lista para todas las próximas veces.

DÓNDE ENCAJA EN EL MURO (importante):
  · Esto SACA imagen clínica con PII a la nube → es un EGRESS deliberado y GATEADO, no automático.
  · El destino es TU PROPIA cuenta de PostDICOM (tu almacén clínico elegido), no infra de terceros
    no confiable. La de-identificación NO se aplica por defecto: tu equipo necesita identificarte.
  · Fail-closed: sin credenciales NO envía nada. Por defecto va en **simulación** (`--dry-run`); la
    subida real exige `--yes`. Nunca imprime PII (ni nombre ni nº de historia): solo UIDs y conteos.
  · Compartir el acceso con un equipo concreto = OTRO acto, tuyo, desde la web de PostDICOM. Esta
    tool solo SUBE a tu cuenta; no comparte con nadie.

AUTENTICACIÓN (DICOMweb usa Basic Auth). Tres secretos en el Llavero de macOS:
  security add-generic-password -s btp-postdicom-url  -a btp -w 'https://.../dicom-web'   # WebAddress DICOMweb de tu cuenta
  security add-generic-password -s btp-postdicom-user -a btp -w 'tu-email@dominio'         # tu usuario PostDICOM
  security add-generic-password -s btp-postdicom-pass -a btp -w '****'                      # tu contraseña (idealmente la específica de DICOMweb/app)
  (Requisito de cuenta: PostDICOM debe tener DICOMweb HABILITADO en tu plan / API Settings; ahí sale la WebAddress.)

USO:
  python3 tools/postdicom.py scan  <carpeta>              # lee y agrupa por estudio (offline, sin red)
  python3 tools/postdicom.py list                          # QIDO-RS: qué estudios ya hay en tu cuenta
  python3 tools/postdicom.py upload <carpeta> [--dry-run]  # sube; --dry-run (por defecto) NO envía
  python3 tools/postdicom.py upload <carpeta> --yes        # subida REAL (egress de PII a tu PostDICOM)
    opciones: --study <UID>  (solo ese estudio) · --batch N (instancias por petición, def. 20)
              --anonymize     (sube de-identificado — normalmente NO, tu equipo te necesita identificada)

Intérprete: requiere pydicom (está en ~/claudecode/.venv-imagen). Ej.:
  ~/claudecode/.venv-imagen/bin/python tools/postdicom.py scan ~/DICOM_Seattle/RM-mama-2026-06-19
"""
import argparse
import base64
import importlib.util
import os
import sys
import uuid
from urllib.request import Request, urlopen

_TOOLS = os.path.dirname(os.path.abspath(__file__))
# Python mete el dir del script (tools/) en sys.path[0]. Ahí vive `queue.py` (tu cola), que
# SOMBREARÍA la `queue` de la stdlib que usan pydicom→requests→urllib3. Lo quitamos del path
# ANTES de importar pydicom; _secrets/_net se cargan por ruta explícita, sin re-ensuciar el path.
sys.path = [p for p in sys.path if os.path.abspath(p or ".") != _TOOLS]

try:
    import pydicom  # noqa: E402  (tras limpiar el path)
except ImportError:
    pydicom = None


def _load_local(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_TOOLS, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_secrets = _load_local("_secrets")
_net = _load_local("_net")

KC = "btp-postdicom"
DICM_MAGIC = b"DICM"


# ---------------------------------------------------------------- credenciales
def _creds():
    """(url, user, pass) del Llavero; None si falta alguna."""
    url = _secrets.get(f"{KC}-url")
    user = _secrets.get(f"{KC}-user")
    pw = _secrets.get(f"{KC}-pass")
    if not (url and user and pw):
        return None
    return url.rstrip("/"), user, pw


def _auth_header(user, pw):
    raw = f"{user}:{pw}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _stow_url(base):
    """Normaliza a .../studies (acepta que ya venga con /studies)."""
    b = base.rstrip("/")
    return b if b.endswith("/studies") else b + "/studies"


def _qido_url(base):
    return base.rstrip("/").removesuffix("/studies") + "/studies"


# --------------------------------------------------------------- lectura local
def _is_dicom(path):
    """True si el fichero tiene la firma 'DICM' en el offset 128 (preámbulo DICOM).
    Cubre los CDs de hospital, cuyos ficheros NO suelen tener extensión .dcm."""
    try:
        with open(path, "rb") as f:
            f.seek(128)
            return f.read(4) == DICM_MAGIC
    except Exception:
        return False


def _walk_dicom(folder):
    """Lista de rutas a ficheros DICOM bajo folder (ignora DICOMDIR, visores, etc.)."""
    out = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if name.upper() == "DICOMDIR":
                continue
            p = os.path.join(root, name)
            if name.lower().endswith(".dcm") or _is_dicom(p):
                out.append(p)
    return sorted(out)


def _group_by_study(paths):
    """{StudyInstanceUID: {'files':[...], 'date':..., 'desc':..., 'modalities':set, 'series':set}}.
    Lee solo cabeceras (stop_before_pixels) para ser rápido."""
    if not pydicom:
        sys.exit("Falta pydicom. Usa ~/claudecode/.venv-imagen/bin/python para ejecutar esta tool.")
    studies = {}
    for p in paths:
        try:
            ds = pydicom.dcmread(p, stop_before_pixels=True, force=True)
        except Exception:
            continue
        uid = getattr(ds, "StudyInstanceUID", None)
        if not uid:
            continue
        s = studies.setdefault(uid, {
            "files": [], "date": getattr(ds, "StudyDate", ""),
            "desc": getattr(ds, "StudyDescription", ""), "modalities": set(), "series": set()})
        s["files"].append(p)
        if getattr(ds, "Modality", ""):
            s["modalities"].add(ds.Modality)
        if getattr(ds, "SeriesInstanceUID", ""):
            s["series"].add(ds.SeriesInstanceUID)
    return studies


def _fmt_study(uid, s):
    return (f"  · {s['date'] or '????'} | {s['desc'] or '(sin desc)'} | "
            f"{sorted(s['modalities']) or '?'} | series {len(s['series'])} | "
            f"imágenes {len(s['files'])}\n    StudyUID {uid}")


# ------------------------------------------------------------------- red (STOW)
def _http(req, timeout=300):
    return _net.with_retries(lambda: urlopen(req, timeout=timeout))


def _qido_existing_uids(base, user, pw):
    """UIDs de estudios ya presentes en la cuenta (para no duplicar)."""
    url = _qido_url(base) + "?includefield=00080018&limit=10000"
    req = Request(url, headers={
        "Accept": "application/dicom+json",
        "Authorization": _auth_header(user, pw)})
    import json
    try:
        with _http(req, timeout=60) as r:
            data = json.loads(r.read().decode() or "[]")
    except Exception as e:
        print(f"  ⚠️  No pude consultar lo ya existente (QIDO-RS): {e}. Sigo sin dedup por servidor.")
        return None
    uids = set()
    for item in data:
        v = item.get("0020000D", {}).get("Value", [])
        if v:
            uids.add(v[0])
    return uids


def _stow_batch(base, user, pw, files):
    """Envía una lista de ficheros DICOM en UN multipart/related (STOW-RS). Devuelve HTTP status."""
    boundary = uuid.uuid4().hex
    sep = f"--{boundary}\r\n".encode()
    body = bytearray()
    for fp in files:
        with open(fp, "rb") as f:
            data = f.read()
        body += sep
        body += b"Content-Type: application/dicom\r\n\r\n"
        body += data
        body += b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = Request(_stow_url(base), data=bytes(body), method="POST", headers={
        "Content-Type": f'multipart/related; type="application/dicom"; boundary={boundary}',
        "Accept": "application/dicom+json",
        "Authorization": _auth_header(user, pw)})
    with _http(req) as r:
        return r.status


def _batched(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ---------------------------------------------------------------------- comandos
def cmd_scan(args):
    paths = _walk_dicom(args.folder)
    studies = _group_by_study(paths)
    print(f"📂 {args.folder}\n   {len(paths)} ficheros DICOM · {len(studies)} estudio(s):")
    for uid, s in sorted(studies.items(), key=lambda kv: kv[1]["date"]):
        print(_fmt_study(uid, s))
    return studies


def cmd_list(args):
    c = _creds()
    if not c:
        sys.exit("⛔ Faltan credenciales en el Llavero (btp-postdicom-url/-user/-pass). Ver cabecera del fichero.")
    base, user, pw = c
    uids = _qido_existing_uids(base, user, pw)
    if uids is None:
        sys.exit(1)
    print(f"🗄️  {len(uids)} estudio(s) ya en tu PostDICOM:")
    for u in sorted(uids):
        print(f"  · {u}")


def cmd_upload(args):
    paths = _walk_dicom(args.folder)
    studies = _group_by_study(paths)
    if args.study:
        studies = {k: v for k, v in studies.items() if k == args.study}
        if not studies:
            sys.exit(f"No encuentro el estudio {args.study} en {args.folder}.")
    print(f"📂 {args.folder} · {len(studies)} estudio(s) candidato(s):")
    for uid, s in sorted(studies.items(), key=lambda kv: kv[1]["date"]):
        print(_fmt_study(uid, s))

    dry = not args.yes  # seguro por defecto: solo sube con --yes
    c = _creds()
    if not c:
        print("\n⛔ No hay credenciales en el Llavero → NO se puede subir.")
        print("   Añádelas (ver cabecera de tools/postdicom.py) y vuelve a ejecutar con --yes.")
        print("   [simulación] Esto es lo que se subiría una vez configurado.")
        dry = True
    if dry:
        total = sum(len(s["files"]) for s in studies.values())
        print(f"\n🧪 SIMULACIÓN (--dry-run). Subiría {total} imágenes de {len(studies)} estudio(s). "
              f"Nada se ha enviado. Para subir de verdad: añade '--yes'.")
        return

    base, user, pw = c
    if args.anonymize:
        sys.exit("La de-identificación en subida no está implementada (tu equipo te necesita identificada). "
                 "Quita --anonymize para subir el estudio real a TU cuenta.")
    existing = _qido_existing_uids(base, user, pw) or set()
    for uid, s in sorted(studies.items(), key=lambda kv: kv[1]["date"]):
        if uid in existing:
            print(f"  ⏭️  Ya está en tu cuenta, no lo resubo: {s['date']} {s['desc']}")
            continue
        files = s["files"]
        print(f"  ⬆️  Subiendo {s['date']} {s['desc']} ({len(files)} imágenes)…")
        sent = 0
        for chunk in _batched(files, args.batch):
            status = _stow_batch(base, user, pw, chunk)
            if status not in (200, 202):
                sys.exit(f"     ✗ STOW-RS devolvió HTTP {status}. Paro para no dejarlo a medias.")
            sent += len(chunk)
            print(f"     … {sent}/{len(files)}")
        print(f"  ✅ Hecho: {s['date']} {s['desc']}")
    print("\n✅ Subida terminada. Compartir el acceso con tu equipo se hace desde la web de PostDICOM (acto tuyo).")


def main():
    ap = argparse.ArgumentParser(description="Subida reutilizable de DICOM a PostDICOM (STOW-RS).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_scan = sub.add_parser("scan", help="lee y agrupa por estudio (offline)")
    p_scan.add_argument("folder")
    sub.add_parser("list", help="lista estudios ya en tu cuenta (QIDO-RS)")
    p_up = sub.add_parser("upload", help="sube estudios (dry-run por defecto)")
    p_up.add_argument("folder")
    p_up.add_argument("--yes", action="store_true", help="subida REAL (sin esto, simulación)")
    p_up.add_argument("--dry-run", action="store_true", help="forzar simulación")
    p_up.add_argument("--study", help="solo este StudyInstanceUID")
    p_up.add_argument("--batch", type=int, default=20, help="imágenes por petición (def. 20)")
    p_up.add_argument("--anonymize", action="store_true", help="(no implementado) subir de-identificado")
    args = ap.parse_args()
    if getattr(args, "dry_run", False):
        args.yes = False
    {"scan": cmd_scan, "list": cmd_list, "upload": cmd_upload}[args.cmd](args)


if __name__ == "__main__":
    main()
