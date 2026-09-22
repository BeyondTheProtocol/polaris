#!/usr/bin/env python3
"""tools/drive.py — gestor completo de Google Drive para Beyond the Protocol.

Operaciones soportadas: crear/subir, listar, mover a papelera, borrar permanente, mover,
renombrar, actualizar contenido, obtener metadatos, setup OAuth.

Garantías del muro:
  · delete() pide confirmación explícita antes de borrar permanentemente (irreversible).
  · trash() es reversible; no requiere doble confirmación.
  · Credenciales en el Llavero de macOS; nunca en disco ni env vars.
  · Kill-switches: si ~/.btp.HALT o ./.HALT existen, este tool se bloquea.
  · Egress solo a accounts.google.com (auth) y www.googleapis.com (Drive API).

Setup inicial (una sola vez):
  1. Google Cloud Console → proyecto "BTP Internal" → Drive API habilitada
  2. Credenciales OAuth 2.0 Desktop → Client ID + Secret
  3. Guardar en Llavero:
       security add-generic-password -U -a "$USER" -s btp-gdrive-client-id -w "<CLIENT_ID>"
       security add-generic-password -U -a "$USER" -s btp-gdrive-client-secret -w "<CLIENT_SECRET>"
  4. python3 tools/drive.py auth   → abre el navegador, aprueba, guarda refresh token

Modelo de credenciales (híbrido):
  · LECTURA (list/meta) → Service Account `btp-gcal-sa-key` (robusta, headless, NO caduca).
    Requiere: compartir la carpeta BTP de Drive con el `client_email` de la SA + Drive API activada.
  · ESCRITURA (upload/move/rename/trash/delete) → OAuth de usuario (una SA no puede ser dueña
    de ficheros en una "Mi unidad" de Gmail de consumidor). Para que no caduque a los 7 días,
    la app OAuth debe estar en "En producción". `auth` usa loopback localhost (OOB está deprecado).
  · `doctor` chequea ambas credenciales y avisa si algo está caído.

Uso CLI:
  python3 tools/drive.py doctor                           # chequea credenciales (SA + usuario)
  python3 tools/drive.py auth
  python3 tools/drive.py create <RUTA_LOCAL> [--name NOMBRE] [--folder FOLDER_ID] [--gdoc]
  python3 tools/drive.py list [QUERY]
  python3 tools/drive.py download <FILE_ID> <RUTA>      # baja un fichero a disco
  python3 tools/drive.py trash <FILE_ID> [FILE_ID ...]
  python3 tools/drive.py delete <FILE_ID> [FILE_ID ...]      # pide confirmación
  python3 tools/drive.py move <FILE_ID> <FOLDER_ID>
  python3 tools/drive.py rename <FILE_ID> <NEW_NAME>
  python3 tools/drive.py meta <FILE_ID>
  python3 tools/drive.py mkdir <NOMBRE> [PARENT_ID]       # crea carpeta (idempotente)
  python3 tools/drive.py upload <RUTA> [FOLDER_ID]        # sube fichero desde disco
"""
import json
import os
import ssl
import subprocess
import sys
import urllib.parse
import urllib.request

_tools_dir = os.path.dirname(os.path.abspath(__file__))
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)
import _secrets  # noqa: E402
# 20-sep-2026: aquí se purgaban TODAS las apariciones de tools/ en sys.path para que
# `tools/queue.py` no tapara la `queue` de la stdlib que usa urllib3 (queue.LifoQueue). Ese
# fichero se renombró a `tools/cola.py` el 11-jul-26: hoy no hay nada que tapar (barrido contra
# sys.stdlib_module_names: cero coincidencias). La purga sí hacía daño — borraba también la
# entrada del llamador. Caso medido: `historial_sync` importa drive y luego `import salida` para
# mandarle el parte a {{TITULAR}}, dentro de un except que se lo traga. El guard vive ahora en
# `tests/test_tools_no_sombrea_stdlib.py`.

# Claves del Llavero
_SVC_CLIENT_ID     = "btp-gdrive-client-id"
_SVC_CLIENT_SECRET = "btp-gdrive-client-secret"
_SVC_REFRESH_TOKEN = "btp-gdrive-refresh-token"
_SA_KEYCHAIN       = "btp-gcal-sa-key"   # MISMA service account que el calendario, reusada para LECTURA de Drive

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
SCOPES      = [DRIVE_SCOPE]
TOKEN_URI   = "https://oauth2.googleapis.com/token"
AUTH_URI    = "https://accounts.google.com/o/oauth2/auth"
OOB_REDIRECT = "urn:ietf:wg:oauth:2.0:oob"

# ---------------------------------------------------------------------------
# Kill-switch
# ---------------------------------------------------------------------------

def _check_halt():
    halt_paths = [os.path.expanduser("~/.btp.HALT"), ".HALT"]
    for p in halt_paths:
        if os.path.exists(p):
            print("🛑 HALT activo (%s) — drive.py bloqueado." % p, file=sys.stderr)
            sys.exit(99)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

# --- Service Account (LECTURA: robusta, headless, sin caducidad) --------------

def _sa_info():
    """JSON de la Service Account desde el Llavero. NUNCA lo imprime (es una credencial)."""
    raw = _secrets.get(_SA_KEYCHAIN)
    if not raw:
        raise RuntimeError(
            "no encuentro la clave de la Service Account en el Llavero (%s)." % _SA_KEYCHAIN)
    try:
        return json.loads(raw)
    except Exception as e:
        raise RuntimeError("la clave del Llavero (%s) no es JSON válido: %r" % (_SA_KEYCHAIN, e))


def sa_email():
    """El client_email de la SA (identificador, NO secreto) — el correo que hay que
    COMPARTIR en Drive para dar acceso a la SA."""
    return _sa_info().get("client_email", "")


def _sa_creds():
    try:
        from google.oauth2 import service_account
    except ImportError:
        _pip_hint()
        sys.exit(1)
    return service_account.Credentials.from_service_account_info(_sa_info(), scopes=SCOPES)


# --- OAuth de usuario (ESCRITURA: subir/mover/borrar) -------------------------
# En una cuenta Gmail de consumidor una Service Account NO puede ser dueña de
# ficheros en "Mi unidad" (storageQuotaExceeded), así que las escrituras van bajo
# la identidad del usuario.

def _creds_user():
    """Credenciales OAuth de usuario desde el Llavero. Auto-refresca; si el refresh token
    caducó (app en modo 'Testing' → 7 días) da un error accionable en vez de un traceback."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google.auth.exceptions import RefreshError
    except ImportError:
        _pip_hint()
        sys.exit(1)

    client_id     = _secrets.get(_SVC_CLIENT_ID)
    client_secret = _secrets.get(_SVC_CLIENT_SECRET)
    refresh_token = _secrets.get(_SVC_REFRESH_TOKEN)

    if not client_id or not client_secret or not refresh_token:
        print("✗ Falta el OAuth de usuario en el Llavero. Ejecuta: python3 tools/drive.py auth",
              file=sys.stderr)
        sys.exit(3)

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    try:
        creds.refresh(Request())
    except RefreshError as e:
        print("✗ El token OAuth de usuario está CADUCADO/revocado (%s).\n"
              "  Causa habitual: la app OAuth en modo 'Testing' (Google caduca el token a los 7 días).\n"
              "  Fix de raíz: pantalla de consentimiento → 'En producción', luego re-loguea:\n"
              "     python3 tools/drive.py auth\n"
              "  Mientras tanto, para SUBIR a Drive usa el conector MCP (identidad de usuario)." % e,
              file=sys.stderr)
        sys.exit(5)
    return creds


def _build(creds):
    try:
        from googleapiclient.discovery import build
    except ImportError:
        _pip_hint()
        sys.exit(1)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _service_read():
    """Cliente de Drive para LECTURA — Service Account."""
    return _build(_sa_creds())


def _service_write():
    """Cliente de Drive para ESCRITURA — OAuth de usuario."""
    return _build(_creds_user())


def _service():
    """Alias retro-compatible: por defecto = escritura (usuario)."""
    return _service_write()


def _pip_hint():
    print("✗ Faltan dependencias. Instala con:\n"
          "  pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client",
          file=sys.stderr)


def cmd_auth():
    """Re-login OAuth de usuario (para ESCRITURA headless). Usa redirect LOOPBACK localhost —
    el flujo OOB (urn:ietf:...:oob) está deprecado por Google. Guarda el refresh token en el
    Llavero. Para que el token NO caduque a los 7 días, la app debe estar en 'En producción'."""
    import http.server
    import socket
    import webbrowser

    client_id     = _secrets.get(_SVC_CLIENT_ID)
    client_secret = _secrets.get(_SVC_CLIENT_SECRET)
    if not client_id or not client_secret:
        print("✗ Antes de hacer auth, guarda Client ID y Secret en el Llavero:\n"
              "  security add-generic-password -U -a \"$USER\" -s btp-gdrive-client-id -w \"<ID>\"\n"
              "  security add-generic-password -U -a \"$USER\" -s btp-gdrive-client-secret -w \"<SECRET>\"",
              file=sys.stderr)
        sys.exit(3)

    # Puerto libre para el redirect loopback (los clientes OAuth "Desktop" permiten localhost).
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    redirect_uri = "http://127.0.0.1:%d/" % port

    captured = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            captured.update(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<h2>Listo. Cierra esta pestaña y vuelve a la terminal.</h2>".encode("utf-8"))

        def log_message(self, *a):  # silencio
            pass

    params = {
        "client_id":     client_id,
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         DRIVE_SCOPE,
        "access_type":   "offline",
        "prompt":        "consent",
    }
    auth_url = AUTH_URI + "?" + urllib.parse.urlencode(params)
    print("\n🔗 Abre esta URL (ya logueada en la cuenta de BTP) y autoriza:\n")
    print(auth_url + "\n")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    srv = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    srv.handle_request()   # atiende UNA sola petición: el redirect con ?code=
    srv.server_close()

    if "error" in captured:
        print("✗ Google devolvió error: %s" % captured["error"][0], file=sys.stderr)
        sys.exit(4)
    code = (captured.get("code") or [""])[0]
    if not code:
        print("✗ No llegó el 'code' del redirect.", file=sys.stderr)
        sys.exit(4)

    data = urllib.parse.urlencode({
        "code":          code,
        "client_id":     client_id,
        "client_secret": client_secret,
        "redirect_uri":  redirect_uri,
        "grant_type":    "authorization_code",
    }).encode()
    req = urllib.request.Request(TOKEN_URI, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req) as r:
        tokens = json.loads(r.read())

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("✗ No se recibió refresh_token. Respuesta:", tokens, file=sys.stderr)
        sys.exit(4)

    subprocess.run(
        ["security", "add-generic-password", "-U", "-a", os.environ.get("USER", "btp"),
         "-s", _SVC_REFRESH_TOKEN, "-w", refresh_token],
        check=True
    )
    print("✅ Auth completado. Refresh token guardado en el Llavero como '%s'." % _SVC_REFRESH_TOKEN)


def cmd_doctor():
    """Comprueba las DOS credenciales de Drive y avisa si algo está caído (en vez de fallar
    en silencio): Service Account (lectura) + OAuth de usuario (escritura)."""
    ok = True

    # 1) Service Account (lectura)
    email = "(SA)"
    try:
        email = sa_email() or "(SA sin client_email)"
        _service_read().files().list(pageSize=1, fields="files(id)").execute()
        print("✅ Service Account (lectura): auth OK — %s\n"
              "    (solo VE las carpetas que le hayas COMPARTIDO a ese correo)." % email)
    except SystemExit:
        raise
    except Exception as e:
        ok = False
        m = str(e).lower()
        if "403" in m or "insufficient" in m or "not been shared" in m or "storagequota" in m or "accessnotconfigured" in m or "has not been used" in m:
            print("⚠️  Service Account (lectura): la clave carga pero SIN acceso a Drive.\n"
                  "    → Comparte la carpeta BTP de Drive con:  %s\n"
                  "    → y confirma que la Drive API está ACTIVADA en su proyecto (quiet-antler-500122-p9)." % email)
        else:
            print("✗ Service Account (lectura): FALLO — %s" % e)

    # 2) OAuth de usuario (escritura)
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google.auth.exceptions import RefreshError
        cid = _secrets.get(_SVC_CLIENT_ID)
        csec = _secrets.get(_SVC_CLIENT_SECRET)
        rt = _secrets.get(_SVC_REFRESH_TOKEN)
        if not (cid and csec and rt):
            ok = False
            print("⚠️  OAuth de usuario (escritura): sin credenciales en el Llavero — subidas "
                  "headless no disponibles (usa el conector MCP mientras).")
        else:
            creds = Credentials(token=None, refresh_token=rt, token_uri=TOKEN_URI,
                                client_id=cid, client_secret=csec, scopes=SCOPES)
            try:
                creds.refresh(Request())
                print("✅ OAuth de usuario (escritura): OK")
            except RefreshError:
                ok = False
                print("✗ OAuth de usuario (escritura): CADUCADO/revocado.\n"
                      "    → Fix de raíz: pantalla de consentimiento → 'En producción' + "
                      "python3 tools/drive.py auth.\n"
                      "    → Mientras, sube por el conector MCP (identidad de usuario).")
    except ImportError:
        _pip_hint()
        ok = False

    print("—")
    print("Resumen: %s" % ("TODO OK ✅" if ok else "HAY ALGO QUE ATENDER ⚠️ (ver arriba)"))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Operaciones
# ---------------------------------------------------------------------------

def list_files(query=None, folder_id=None, max_results=50):
    """Lista archivos. query = texto libre (nombre, tipo…). folder_id filtra por carpeta."""
    svc = _service_read()
    q_parts = []
    if query:
        q_parts.append("name contains '%s'" % query.replace("'", "\\'"))
    if folder_id:
        q_parts.append("'%s' in parents" % folder_id)
    q_parts.append("trashed = false")
    q = " and ".join(q_parts)
    fields = "files(id,name,mimeType,modifiedTime,size,parents)"
    results = svc.files().list(q=q, pageSize=max_results, fields=fields).execute()
    return results.get("files", [])


def list_folder(folder_id):
    """Todo lo que cuelga de una carpeta (sin papelera), paginando: `list_files` se para en 50."""
    svc = _service_read()
    out, token = [], None
    while True:
        r = svc.files().list(q="'%s' in parents and trashed = false" % folder_id,
                             pageSize=1000, pageToken=token,
                             fields="nextPageToken,files(id,name,mimeType,size)").execute()
        out += r.get("files", [])
        token = r.get("nextPageToken")
        if not token:
            return out


def get_metadata(file_id):
    """Devuelve metadatos de un archivo."""
    svc = _service_read()
    return svc.files().get(fileId=file_id,
                           fields="id,name,mimeType,modifiedTime,size,parents,webViewLink").execute()


def download(file_id, destino):
    """Baja un fichero de Drive a disco. Es LECTURA: usa la cuenta de servicio, igual que
    `list_files`, y no pasa por el gate de subida (que vigila lo que SALE de esta máquina).

    Existe porque el espejo local del historial tenía una sola dirección: se sabía subir y no
    bajar, así que un informe que {{TITULAR}} colgaba en Drive no llegaba nunca al archivo local ni,
    por tanto, al conocimiento de Polaris.
    """
    import io
    from googleapiclient.http import MediaIoBaseDownload
    svc = _service_read()
    req = svc.files().get_media(fileId=file_id)
    os.makedirs(os.path.dirname(os.path.abspath(destino)) or ".", exist_ok=True)
    tmp = destino + ".parcial"
    with io.FileIO(tmp, "wb") as fh:
        dl = MediaIoBaseDownload(fh, req, chunksize=1024 * 1024)
        hecho = False
        while not hecho:
            _estado, hecho = dl.next_chunk()
    os.replace(tmp, destino)          # atómico: nadie ve un PDF a medias
    return destino


def trash(file_id):
    """Manda el archivo a la papelera (reversible)."""
    svc = _service()
    result = svc.files().update(fileId=file_id, body={"trashed": True}).execute()
    return result


def delete(file_id, confirm=False):
    """Borrado PERMANENTE. Requiere confirm=True explícito."""
    if not confirm:
        raise RuntimeError("delete() requiere confirm=True — el borrado es IRREVERSIBLE.")
    svc = _service()
    svc.files().delete(fileId=file_id).execute()


def move(file_id, folder_id):
    """Mueve un archivo a otra carpeta."""
    svc = _service()
    meta = svc.files().get(fileId=file_id, fields="parents").execute()
    prev_parents = ",".join(meta.get("parents", []))
    return svc.files().update(
        fileId=file_id,
        addParents=folder_id,
        removeParents=prev_parents,
        fields="id,name,parents"
    ).execute()


def rename(file_id, new_name):
    """Renombra un archivo."""
    svc = _service()
    return svc.files().update(fileId=file_id, body={"name": new_name},
                              fields="id,name").execute()


# ---------------------------------------------------------------------------
# Gate clínico de SUBIDA (25-jul-26)
# ---------------------------------------------------------------------------
# Este fichero era la boca de subida sin ninguna puerta: `create`/`upload_file`/
# `update_content` mandaban cualquier ruta local a una cuenta de Drive COMPARTIDA sin
# consultar `borde`, sin dejar borrador en el outbox y sin pedir confirmación (la única que
# había era la de `delete`). Y es la boca que mejor encaja con el formato en el que vive el
# N2: los PDFs del hospital.
PALABRA_SUBIR_CLINICO = "SUBIR-CLINICO"


def _politica_clinica():
    """Carga zonas_clinicas.py (fuente única). FAIL-CLOSED: si no puedo saber si algo es
    clínico, no subo nada."""
    import importlib.util
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ruta = os.path.join(base, ".claude", "hooks", "zonas_clinicas.py")
    if not os.path.exists(ruta):
        ruta = os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"),
                            ".claude", "hooks", "zonas_clinicas.py")
    spec = importlib.util.spec_from_file_location("zonas_clinicas", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _confirmar_subida_en_tty(ruta):
    """Exige teclear la palabra en /dev/tty. Un agente headless no puede abrirlo ni
    responder: la subida de un clínico muere aquí. Mismo patrón que correo_smtp."""
    try:
        r = open("/dev/tty", "r")
        w = open("/dev/tty", "w")
    except Exception:
        raise RuntimeError("subida BLOQUEADA: %s es zona clínica y no hay terminal (TTY) "
                           "para que {{TITULAR}} lo autorice." % ruta)
    try:
        if not (r.isatty() and w.isatty()):
            raise RuntimeError("subida BLOQUEADA: no hay TTY real.")
        w.write("\n⚠️  Vas a subir un fichero de ZONA CLÍNICA a Drive (cuenta compartida).\n"
                "    %s\n"
                "    Escribe %s para continuar: " % (ruta, PALABRA_SUBIR_CLINICO))
        w.flush()
        if (r.readline() or "").strip() != PALABRA_SUBIR_CLINICO:
            raise RuntimeError("subida cancelada por el humano.")
        return True
    finally:
        r.close()
        w.close()


def _gate_subida(ruta_local=None, contenido=None, nombre=None):
    """Puerta única de las tres funciones de subida. Orden fail-closed."""
    _check_halt()
    try:
        ZC = _politica_clinica()
    except Exception as e:
        raise RuntimeError("subida BLOQUEADA: no pude cargar la política clínica (%r). "
                           "Sin saber qué es clínico no se sube nada." % e)
    if ruta_local and ZC.es_ruta_clinica(ruta_local):
        _confirmar_subida_en_tty(ruta_local)
        return
    # Para lo no-clínico, el clasificador de `borde` sobre lo que se puede leer como texto
    # (nombre + primeros 64 KB). Un binario no se puede clasificar: si su nombre o su ruta
    # huelen a clínico ya lo ha parado la rama de arriba; si no, pasa.
    muestra = str(nombre or ruta_local or "")
    if contenido is not None:
        try:
            muestra += "\n" + (contenido.decode("utf-8", "ignore")
                               if isinstance(contenido, bytes) else str(contenido))[:65536]
        except Exception:
            pass
    try:
        import borde
    except Exception:
        raise RuntimeError("subida BLOQUEADA: no pude importar borde (la puerta de egress).")
    if not borde.guard_cli(muestra, "google-drive", etiqueta="drive"):
        raise RuntimeError("subida BLOQUEADA por el borde: contenido sensible a Drive.")


def update_content(file_id, content, mime_type="text/html"):
    """Actualiza el contenido de un archivo existente."""
    _gate_subida(contenido=content, nombre=str(file_id))
    try:
        from googleapiclient.http import MediaInMemoryUpload
    except ImportError:
        _pip_hint()
        sys.exit(1)
    svc = _service()
    media = MediaInMemoryUpload(content.encode("utf-8") if isinstance(content, str) else content,
                                mimetype=mime_type)
    return svc.files().update(fileId=file_id, media_body=media, fields="id,name,modifiedTime").execute()


def create(local_path, name=None, folder_id=None, mime_type=None, convert_to=None):
    """Sube un fichero local NUEVO a Drive (la 'C' del CRUD). Devuelve metadatos con webViewLink.
    convert_to='application/vnd.google-apps.document' lo convierte a Google Doc editable en el navegador;
    sin él, se sube tal cual (p. ej. un .docx queda como Word editable)."""
    _gate_subida(ruta_local=local_path, nombre=name)
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        _pip_hint()
        sys.exit(1)
    import mimetypes
    svc = _service()
    name = name or os.path.basename(local_path)
    mime_type = mime_type or mimetypes.guess_type(local_path)[0] or "application/octet-stream"
    body = {"name": name}
    if folder_id:
        body["parents"] = [folder_id]
    if convert_to:
        body["mimeType"] = convert_to
    media = MediaFileUpload(local_path, mimetype=mime_type, resumable=True)
    return svc.files().create(body=body, media_body=media,
                              fields="id,name,webViewLink,mimeType").execute()


def make_folder(name, parent_id=None):
    """Crea una carpeta y devuelve sus metadatos (id incluido). Si ya existe una con
    el mismo nombre bajo el mismo padre, devuelve la existente (idempotente)."""
    svc = _service()
    safe = name.replace("'", "\\'")
    q = ("mimeType = 'application/vnd.google-apps.folder' and trashed = false "
         "and name = '%s'" % safe)
    if parent_id:
        q += " and '%s' in parents" % parent_id
    existing = svc.files().list(q=q, pageSize=1,
                                fields="files(id,name,webViewLink)").execute().get("files", [])
    if existing:
        return svc.files().get(fileId=existing[0]["id"],
                               fields="id,name,webViewLink").execute()
    body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        body["parents"] = [parent_id]
    return svc.files().create(body=body, fields="id,name,webViewLink").execute()


def upload_file(path, folder_id=None, name=None, mime_type=None):
    """Sube un fichero DESDE DISCO (streaming, sin pasar el contenido por memoria del
    proceso llamante). Devuelve los metadatos del fichero creado."""
    _gate_subida(ruta_local=path, nombre=name)
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        _pip_hint()
        sys.exit(1)
    import mimetypes
    svc = _service()
    name = name or os.path.basename(path)
    mime_type = mime_type or mimetypes.guess_type(path)[0] or "application/octet-stream"
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    body = {"name": name}
    if folder_id:
        body["parents"] = [folder_id]
    media = MediaFileUpload(path, mimetype=mime_type, resumable=True)
    return svc.files().create(body=body, media_body=media,
                              fields="id,name,size,webViewLink").execute()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _fmt_size(s):
    try:
        n = int(s)
        if n > 1024*1024: return "%.1f MB" % (n/1024/1024)
        if n > 1024: return "%.0f KB" % (n/1024)
        return "%d B" % n
    except Exception:
        return "—"


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    cmd = argv[0]

    if cmd == "auth":
        cmd_auth()
        return 0

    if cmd == "doctor":
        return cmd_doctor()

    _check_halt()

    if cmd == "list":
        # `--folder ID` lista una carpeta ENTERA (paginando) y `--json` saca los nombres sin
        # recortar. Existen porque `historial.py subir` pasaba «'ID' in parents» como query,
        # que aquí se convierte en `name contains` → siempre vacío → daba por pendiente todo.
        if "--folder" in argv:
            i = argv.index("--folder")
            if i + 1 >= len(argv):
                print("uso: drive.py list --folder <FOLDER_ID> [--json]", file=sys.stderr)
                return 2
            files = list_folder(argv[i + 1])
            if "--json" in argv:
                print(json.dumps([{"id": f["id"], "name": f["name"]} for f in files],
                                 ensure_ascii=False))
                return 0
        else:
            query = argv[1] if len(argv) > 1 else None
            files = list_files(query)
        if not files:
            print("(sin resultados)")
            return 0
        for f in files:
            size = _fmt_size(f.get("size", ""))
            print("  %-55s  %-12s  %s" % (f["name"][:54], size, f["id"]))
        return 0

    if cmd == "download":
        if len(argv) < 3:
            print("uso: drive.py download <FILE_ID> <RUTA_DESTINO>", file=sys.stderr)
            return 2
        ruta = download(argv[1], argv[2])
        print("✓ %s (%s)" % (ruta, _fmt_size(str(os.path.getsize(ruta)))))
        return 0

    if cmd == "meta":
        if len(argv) < 2:
            print("uso: drive.py meta <FILE_ID>", file=sys.stderr); return 2
        meta = get_metadata(argv[1])
        print(json.dumps(meta, indent=2, ensure_ascii=False))
        return 0

    if cmd == "trash":
        if len(argv) < 2:
            print("uso: drive.py trash <FILE_ID> [FILE_ID ...]", file=sys.stderr); return 2
        for fid in argv[1:]:
            result = trash(fid)
            print("🗑  Movido a papelera: %s  (%s)" % (result.get("name", fid), fid))
        return 0

    if cmd == "delete":
        if len(argv) < 2:
            print("uso: drive.py delete <FILE_ID> [FILE_ID ...]", file=sys.stderr); return 2
        ids = argv[1:]
        print("⚠️  BORRADO PERMANENTE de %d archivo(s). Esto NO SE PUEDE DESHACER." % len(ids))
        for fid in ids:
            try:
                meta = get_metadata(fid)
                print("   · %s  (%s)" % (meta.get("name", fid), fid))
            except Exception:
                print("   · %s" % fid)
        resp = input("¿Confirmas el borrado permanente? Escribe BORRAR para continuar: ").strip()
        if resp != "BORRAR":
            print("Cancelado — no se borró nada.")
            return 0
        for fid in ids:
            delete(fid, confirm=True)
            print("🗑  Borrado permanentemente: %s" % fid)
        return 0

    if cmd == "move":
        if len(argv) < 3:
            print("uso: drive.py move <FILE_ID> <FOLDER_ID>", file=sys.stderr); return 2
        result = move(argv[1], argv[2])
        print("📁 Movido: %s → carpeta %s" % (result.get("name", argv[1]), argv[2]))
        return 0

    if cmd == "rename":
        if len(argv) < 3:
            print("uso: drive.py rename <FILE_ID> <NUEVO_NOMBRE>", file=sys.stderr); return 2
        result = rename(argv[1], argv[2])
        print("✏️  Renombrado: %s" % result.get("name", argv[2]))
        return 0

    if cmd == "create":
        if len(argv) < 2:
            print("uso: drive.py create <RUTA_LOCAL> [--name NOMBRE] [--folder FOLDER_ID] [--gdoc]",
                  file=sys.stderr); return 2
        path = argv[1]
        name = folder = convert = None
        rest, j = argv[2:], 0
        while j < len(rest):
            if rest[j] == "--name" and j + 1 < len(rest): name = rest[j + 1]; j += 2
            elif rest[j] == "--folder" and j + 1 < len(rest): folder = rest[j + 1]; j += 2
            elif rest[j] == "--gdoc": convert = "application/vnd.google-apps.document"; j += 1
            else: j += 1
        r = create(path, name=name, folder_id=folder, convert_to=convert)
        print("⬆️  Subido: %s  (%s)" % (r.get("name"), r.get("id")))
        if r.get("webViewLink"):
            print("   %s" % r["webViewLink"])
        return 0

    if cmd == "mkdir":
        if len(argv) < 2:
            print("uso: drive.py mkdir <NOMBRE> [PARENT_ID]", file=sys.stderr); return 2
        parent = argv[2] if len(argv) > 2 else None
        result = make_folder(argv[1], parent)
        print("📁 Carpeta: %s  id=%s\n   %s" % (
            result.get("name"), result["id"], result.get("webViewLink", "")))
        return 0

    if cmd == "upload":
        if len(argv) < 2:
            print("uso: drive.py upload <RUTA> [FOLDER_ID]", file=sys.stderr); return 2
        folder = argv[2] if len(argv) > 2 else None
        result = upload_file(argv[1], folder)
        print("⬆️  Subido: %s  (%s)  id=%s\n   %s" % (
            result.get("name"), _fmt_size(result.get("size", "")),
            result["id"], result.get("webViewLink", "")))
        return 0

    print("comando desconocido: %r\nUsa --help para ver los disponibles." % cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
