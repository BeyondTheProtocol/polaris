#!/usr/bin/env python3
"""tools/correo_outbox.py — el MOTOR DE SALIDA de correo de {{TITULAR}} (Fase 1: borradores con adjuntos).

Por qué existe: el conector de Gmail (MCP) que usa el agente NO sabe adjuntar bien — adjuntar por
ahí obliga a meter el fichero ENTERO (base64) por el modelo (~96k tokens por un PDF de 3 páginas).
Esto lo arregla: construye el MIME y lo deja como BORRADOR en Gmail vía IMAP APPEND, leyendo los
adjuntos DEL DISCO (cero coste de tokens). Es "lo de Cárdenas", bien hecho.

Garantías del muro (Fase 1):
  · DRAFT-ONLY estructural: NUNCA importa smtplib → este fichero NO PUEDE enviar. Solo deja
    borradores en la carpeta Borradores de la PROPIA cuenta de {{TITULAR}}. Enviar = Fase 2 (separada,
    gateada por salida.py) o lo hace ella desde Gmail.
  · Egress fail-closed: única conexión imap.gmail.com:993 (_assert_host); cualquier otro host aborta.
  · Cero egress a terceros: escribe en el buzón de la PROPIA {{TITULAR}}; nada sale a nadie hasta que
    ELLA pulse enviar. Un borrador no es un envío.
  · Adjuntos clínicos/PII: van a SU Gmail (zona de confianza); el envío al mundo lo firma ella.
  · Secreto en el Llavero (App Password por cuenta, vía tools/_secrets); si falta, se ABORTA con un
    mensaje claro (fail-soft, no se inventa nada).
  · Anti-inyección: este tool no interpreta el cuerpo; recibe asunto/cuerpo/destinatarios ya
    decididos por el agente (que trata el correo entrante como DATO). Valida que los destinatarios
    sean emails planos (no cabeceras inyectadas).

Uso:
  python3 tools/correo_outbox.py draft --to a@b.com [--cc c@d.com] \\
      --subject "Asunto" --body-file ruta.txt [--attach f1.pdf f2.pdf] [--account titular.mgp] [--dry]
  python3 tools/correo_outbox.py status      # qué cuentas tienen App Password en el Llavero

`--dry` construye y valida el MIME (destinatarios, adjuntos, tamaño) SIN conectar ni tocar la red:
así se prueba sin credenciales. Sin `--dry`, hace falta la App Password (gate de {{TITULAR}}).
"""
import email.utils
import imaplib
import mimetypes
import os
import re
import ssl
import sys
import time
from email.message import EmailMessage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _secrets  # noqa: E402  (get(service) → Llavero de macOS)

# Única boca de IMAP permitida (egress fail-closed). Mismo host que el archivador de correo.
IMAP_HOST, IMAP_PORT = "imap.gmail.com", 993

# (usuario, servicio del Llavero) — espejo de email_archive.ACCOUNTS (fuente: una App Password/cuenta).
ACCOUNTS = {
    "titular.mgp@gmail.com": "btp-gmail-app-password",
    "titular@gmail.com": "btp-gmail-app-password-2",
}
DEFAULT_ACCOUNT = "titular.mgp@gmail.com"

RE_EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
MAX_TOTAL_BYTES = 25 * 1024 * 1024   # tope de Gmail; por encima, abortar (no romper el envío)


class FaltaClave(RuntimeError):
    pass


def _assert_host(host, port):
    """Cortafuegos de egress: solo se permite imap.gmail.com:993. Cualquier otro destino aborta."""
    if (host, int(port)) != (IMAP_HOST, IMAP_PORT):
        raise RuntimeError("egress BLOQUEADO: este tool solo habla con %s:%d" % (IMAP_HOST, IMAP_PORT))


def _resolve_account(account):
    """Acepta el email completo o un fragmento ('titular.mgp', 'titular'). Devuelve (user, service)."""
    if not account:
        return DEFAULT_ACCOUNT, ACCOUNTS[DEFAULT_ACCOUNT]
    for user, service in ACCOUNTS.items():
        if account.lower() == user.lower() or account.lower() in user.lower():
            return user, service
    raise ValueError("cuenta desconocida %r (conocidas: %s)" % (account, ", ".join(ACCOUNTS)))


def _valida_destinatarios(addrs, etiqueta):
    """Cada destinatario debe ser un email plano (anti-inyección de cabeceras CRLF/extra)."""
    out = []
    for a in addrs:
        a = (a or "").strip()
        if not a:
            continue
        if "\n" in a or "\r" in a or not RE_EMAIL.match(a):
            raise ValueError("destinatario %s inválido: %r (solo emails planos)" % (etiqueta, a))
        out.append(a)
    return out


def construir_mime(account, to, cc, subject, body, attachments, html=None):
    """Construye el EmailMessage con cuerpo + adjuntos leídos DEL DISCO. Devuelve (msg, resumen)."""
    to = _valida_destinatarios(to, "To")
    cc = _valida_destinatarios(cc or [], "Cc")
    if not to:
        raise ValueError("hace falta al menos un destinatario (--to)")
    if "\n" in subject or "\r" in subject:
        raise ValueError("asunto con salto de línea (posible inyección de cabecera)")

    msg = EmailMessage()
    msg["From"] = account
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=account.split("@")[-1])
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")  # multipart/alternative: cuerpo plano + HTML

    adj_info = []
    total = len(body.encode("utf-8"))
    for path in attachments or []:
        if not os.path.isfile(path):
            raise FileNotFoundError("adjunto no encontrado: %s" % path)
        data = open(path, "rb").read()
        total += len(data)
        ctype, _ = mimetypes.guess_type(path)
        maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream",
                           filename=os.path.basename(path))
        adj_info.append((os.path.basename(path), len(data)))
    if total > MAX_TOTAL_BYTES:
        raise ValueError("el correo pesa %.1f MB > 25 MB (tope de Gmail)" % (total / 1024 / 1024))

    resumen = {"from": account, "to": to, "cc": cc, "subject": subject,
               "adjuntos": adj_info, "bytes_mime": len(msg.as_bytes())}
    return msg, resumen


def _drafts_folder(M):
    """Carpeta de Borradores de Gmail por el atributo \\Drafts (independiente del idioma)."""
    try:
        typ, data = M.list()
        if typ == "OK":
            for raw in data or []:
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                if "\\Drafts" in line:
                    m = re.search(r'"([^"]+)"\s*$', line) or re.search(r"(\S+)\s*$", line)
                    if m:
                        return m.group(1)
    except Exception:
        pass
    return "[Gmail]/Drafts"


def dejar_borrador(account, service, msg):
    """Conecta por IMAP y APPENDea el MIME a Borradores con el flag \\Draft. Devuelve la carpeta."""
    pw = _secrets.get(service)
    if not pw:
        raise FaltaClave("falta la App Password '%s' en el Llavero para %s (gate de {{TITULAR}})"
                         % (service, account))
    _assert_host(IMAP_HOST, IMAP_PORT)
    M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ssl.create_default_context())
    try:
        M.login(account, pw)
        folder = _drafts_folder(M)
        typ, resp = M.append(folder, "\\Draft", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        if typ != "OK":
            raise RuntimeError("IMAP APPEND falló: %s %s" % (typ, resp))
        return folder
    finally:
        try:
            M.logout()
        except Exception:
            pass


def borrar_borrador(account, service, uid_or_subject):
    """Busca y borra un borrador por UID numérico o por asunto (parcial). Devuelve cuántos borró."""
    pw = _secrets.get(service)
    if not pw:
        raise FaltaClave("falta la App Password '%s' en el Llavero para %s" % (service, account))
    _assert_host(IMAP_HOST, IMAP_PORT)
    M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ssl.create_default_context())
    try:
        M.login(account, pw)
        folder = _drafts_folder(M)
        M.select(folder)
        if uid_or_subject.isdigit():
            uids = [uid_or_subject.encode()]
        else:
            typ, data = M.uid("SEARCH", None, 'SUBJECT "%s"' % uid_or_subject.replace('"', ''))
            uids = data[0].split() if typ == "OK" and data[0] else []
        if not uids:
            return 0
        for uid in uids:
            M.uid("STORE", uid, "+FLAGS", "\\Deleted")
        M.expunge()
        return len(uids)
    finally:
        try:
            M.logout()
        except Exception:
            pass


def _parse(argv):
    a = {"to": [], "cc": [], "subject": "", "body_file": None, "attach": [],
         "html_file": None, "account": None, "dry": "--dry" in argv}
    i = 0
    multi = {"--to": "to", "--cc": "cc", "--attach": "attach"}
    single = {"--subject": "subject", "--body-file": "body_file", "--html-file": "html_file", "--account": "account"}
    while i < len(argv):
        tok = argv[i]
        if tok in multi:
            i += 1
            while i < len(argv) and not argv[i].startswith("--"):
                a[multi[tok]].append(argv[i]); i += 1
            continue
        if tok in single:
            if i + 1 < len(argv):
                a[single[tok]] = argv[i + 1]; i += 2
            else:
                i += 1
            continue
        i += 1
    return a


def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd in ("-h", "--help"):
        print(__doc__); return 0
    if cmd == "status":
        for user, service in ACCOUNTS.items():
            tiene = "✓ App Password" if _secrets.get(service) else "✗ falta en el Llavero (gate)"
            print("  %-26s %-26s %s" % (user, service, tiene))
        return 0
    if cmd == "delete-draft":
        if len(argv) < 2:
            print("uso: correo_outbox.py delete-draft <UID|asunto> [--account …]", file=sys.stderr); return 2
        uid_or_subj = argv[1]
        acct_arg = None
        if "--account" in argv:
            idx = argv.index("--account")
            acct_arg = argv[idx + 1] if idx + 1 < len(argv) else None
        try:
            account, service = _resolve_account(acct_arg)
            n = borrar_borrador(account, service, uid_or_subj)
        except FaltaClave as e:
            print("✗ %s" % e, file=sys.stderr); return 3
        except Exception as e:
            print("✗ %s" % e, file=sys.stderr); return 1
        if n:
            print("✅ %d borrador(es) eliminado(s) de %s" % (n, account))
        else:
            print("⚠️  No se encontró ningún borrador que coincida con '%s'" % uid_or_subj)
        return 0
    if cmd != "draft":
        print("uso: correo_outbox.py [draft … | delete-draft … | status]", file=sys.stderr); return 2

    a = _parse(argv[1:])
    try:
        account, service = _resolve_account(a["account"])
        if not a["body_file"] or not os.path.isfile(a["body_file"]):
            print("✗ falta --body-file (un fichero de texto con el cuerpo)", file=sys.stderr); return 2
        body = open(a["body_file"], encoding="utf-8").read()
        html = None
        if a["html_file"]:
            if not os.path.isfile(a["html_file"]):
                print("✗ --html-file no existe: %s" % a["html_file"], file=sys.stderr); return 2
            html = open(a["html_file"], encoding="utf-8").read()
        msg, r = construir_mime(account, a["to"], a["cc"], a["subject"], body, a["attach"], html=html)
    except Exception as e:
        print("✗ %s" % e, file=sys.stderr); return 1

    adj = ", ".join("%s (%.0f KB)" % (n, b / 1024) for n, b in r["adjuntos"]) or "—"
    print("📧 Borrador preparado:")
    print("   de:       %s" % r["from"])
    print("   para:     %s" % ", ".join(r["to"]))
    print("   cc:       %s" % (", ".join(r["cc"]) or "—"))
    print("   asunto:   %s" % r["subject"])
    print("   adjuntos: %s" % adj)
    print("   tamaño:   %.0f KB" % (r["bytes_mime"] / 1024))
    if a["dry"]:
        print("   (DRY: MIME válido; NO se ha conectado ni dejado nada. Sin --dry lo APPENDea a Borradores.)")
        return 0
    try:
        folder = dejar_borrador(account, service, msg)
    except FaltaClave as e:
        print("✗ %s" % e, file=sys.stderr); return 3
    except Exception as e:
        print("✗ al dejar el borrador: %s" % e, file=sys.stderr); return 1
    print("✅ Borrador dejado en Gmail (carpeta %s). Revísalo y envíalo tú — nada ha salido solo." % folder)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
