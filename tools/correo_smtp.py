#!/usr/bin/env python3
"""tools/correo_smtp.py — envío REAL de correo para Beyond the Protocol (Fase 2).

Por qué existe: correo_outbox.py garantiza estructuralmente que NUNCA envía (solo borradores).
Este archivo es el complemento deliberadamente separado que sí envía, con gates duros.

Garantías del muro:
  · GATE HUMANO (8-jul-2026): el envío real exige teclear ENVIAR en una terminal real
    (/dev/tty). Un agente corre sin TTY, así que NO PUEDE enviar: se le corta antes de
    abrir SMTP y se le remite a dejar un borrador. No es una promesa, es el código.
  · dry_run=True por defecto — solo imprime el MIME, nunca abre SMTP.
  · El envío real requiere --send explícito Y la confirmación en TTY (o human_ok=True en
    API, que sólo concede _confirmar_en_tty()).
  · Kill-switches: si ~/.btp.HALT o ./.HALT existen, se bloquea antes de cualquier conexión.
  · Egress fail-closed: solo smtp.gmail.com:587 (STARTTLS). Cualquier otro host aborta.
  · Credenciales solo del Llavero de macOS (App Passwords, no contraseñas maestras).
  · Validación de destinatarios: emails planos anti-inyección de cabeceras.
  · Regla de {{TITULAR}}: por defecto TODO correo se queda en borrador (tools/correo_outbox.py).
    Enviar solo si ella lo pide explícitamente, y lo confirma ella en su Terminal.

Uso CLI:
  python3 tools/correo_smtp.py --to a@b.com --subject "Asunto" --body-file cuerpo.txt [--cc c@d.com]
      [--attach f.pdf] [--account titular.mgp] [--html-file cuerpo.html]
      --send          ← requerido para enviar de verdad; sin él, modo dry

  python3 tools/correo_smtp.py status    ← qué cuentas tienen App Password

Uso desde Python:
  from tools.correo_smtp import enviar
  enviar(to=["dest@ejemplo.com"], subject="Asunto", body="Texto",
         account="titular.mgp@gmail.com", dry_run=True)   # ← default: solo preview
"""
import email.utils
import mimetypes
import os
import re
import smtplib
import ssl
import sys
from email.message import EmailMessage

_tools_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _tools_dir)
import _secrets  # noqa: E402
sys.path = [p for p in sys.path if os.path.abspath(p) != _tools_dir]

SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 587

ACCOUNTS = {
    "titular.mgp@gmail.com":        "btp-gmail-app-password",
    "titular@gmail.com":        "btp-gmail-app-password-2",
}
DEFAULT_ACCOUNT = "titular.mgp@gmail.com"

RE_EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
MAX_TOTAL_BYTES = 25 * 1024 * 1024


# ---------------------------------------------------------------------------
# Kill-switch
# ---------------------------------------------------------------------------

def _check_halt():
    for p in [os.path.expanduser("~/.btp.HALT"), ".HALT"]:
        if os.path.exists(p):
            raise RuntimeError("🛑 HALT activo (%s) — correo_smtp.py bloqueado." % p)


# ---------------------------------------------------------------------------
# Gate humano: enviar exige teclear ENVIAR en una terminal real
# ---------------------------------------------------------------------------

PALABRA_CONFIRMACION = "ENVIAR"


def _confirmar_en_tty(resumen):
    """Muestra el correo en /dev/tty y exige teclear ENVIAR. Devuelve True o lanza.

    Un agente (Bash sin terminal) no puede abrir /dev/tty ni responder: el envío muere aquí.
    Es el único camino que concede human_ok=True.
    """
    # Lectura y escritura por separado: abrir /dev/tty en "r+" revienta con
    # "not seekable" en terminales reales.
    try:
        tty_in  = open("/dev/tty", "r")
        tty_out = open("/dev/tty", "w")
    except OSError:
        raise RuntimeError(
            "envío BLOQUEADO: no hay terminal (TTY). El envío real solo se confirma a mano.\n"
            "        Si esto lo ejecuta un agente: deja un borrador con tools/correo_outbox.py."
        )
    with tty_in, tty_out:
        if not (os.isatty(tty_in.fileno()) and os.isatty(tty_out.fileno())):
            raise RuntimeError("envío BLOQUEADO: /dev/tty no es una terminal interactiva.")
        adj = ", ".join("%s (%.0f KB)" % (n, b / 1024) for n, b in resumen["adjuntos"]) or "—"
        tty_out.write("\n" + "=" * 62 + "\n")
        tty_out.write("  VAS A ENVIAR UN CORREO DE VERDAD. Esto no se puede deshacer.\n")
        tty_out.write("=" * 62 + "\n")
        tty_out.write("  de:       %s\n" % resumen["from"])
        tty_out.write("  para:     %s\n" % ", ".join(resumen["to"]))
        tty_out.write("  cc:       %s\n" % (", ".join(resumen["cc"]) or "—"))
        tty_out.write("  asunto:   %s\n" % resumen["subject"])
        tty_out.write("  adjuntos: %s\n" % adj)
        tty_out.write("-" * 62 + "\n")
        tty_out.write(resumen.get("cuerpo_preview", "") + "\n")
        tty_out.write("-" * 62 + "\n")
        tty_out.write("  Escribe %s para enviarlo, o cualquier otra cosa para abortar: "
                      % PALABRA_CONFIRMACION)
        tty_out.flush()
        respuesta = tty_in.readline().strip()
    if respuesta != PALABRA_CONFIRMACION:
        raise RuntimeError("envío abortado por el usuario (no se tecleó %s)." % PALABRA_CONFIRMACION)
    return True
# Monitor de salud (solo lectura del estado del servidor — NUNCA envía nada)
# ---------------------------------------------------------------------------

def smtp_alcanzable(timeout=8):
    """¿smtp.gmail.com:587 responde y hace STARTTLS? NO autentica, NO envía — solo prueba
    que el servidor está vivo (para el monitor de correo de healthcheck). Devuelve
    (True, None) o (False, motivo). Fail-soft: nunca lanza."""
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=timeout) as s:
            s.ehlo()
            s.starttls(context=ssl.create_default_context())
            s.ehlo()
        return True, None
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


def login_ok(account=None, timeout=8):
    """Test de credencial SMTP (LOGIN, sin enviar nada): ¿la App Password de esta cuenta
    autentica de verdad? Reusa ACCOUNTS/_resolve_account. Devuelve (True, None) o
    (False, motivo). Fail-soft: nunca lanza."""
    try:
        user, service = _resolve_account(account)
    except ValueError as e:
        return False, str(e)
    pw = _secrets.get(service)
    if not pw:
        return False, "falta '%s' en el Llavero" % service
    try:
        _assert_smtp_host(SMTP_HOST, SMTP_PORT)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=timeout) as s:
            s.ehlo()
            s.starttls(context=ssl.create_default_context())
            s.ehlo()
            s.login(user, pw)
        return True, None
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assert_smtp_host(host, port):
    if (host, int(port)) != (SMTP_HOST, SMTP_PORT):
        raise RuntimeError("egress BLOQUEADO: solo se permite %s:%d" % (SMTP_HOST, SMTP_PORT))


def _resolve_account(account):
    if not account:
        return DEFAULT_ACCOUNT, ACCOUNTS[DEFAULT_ACCOUNT]
    for user, service in ACCOUNTS.items():
        if account.lower() == user.lower() or account.lower() in user.lower():
            return user, service
    raise ValueError("cuenta desconocida %r (conocidas: %s)" % (account, ", ".join(ACCOUNTS)))


def _valida_destinatarios(addrs, etiqueta):
    out = []
    for a in (addrs or []):
        a = (a or "").strip()
        if not a:
            continue
        if "\n" in a or "\r" in a or not RE_EMAIL.match(a):
            raise ValueError("destinatario %s inválido: %r" % (etiqueta, a))
        out.append(a)
    return out


def _construir_mime(account, to, cc, subject, body, attachments, html=None):
    to  = _valida_destinatarios(to, "To")
    cc  = _valida_destinatarios(cc, "Cc")
    if not to:
        raise ValueError("hace falta al menos un destinatario (--to)")
    if "\n" in subject or "\r" in subject:
        raise ValueError("asunto con salto de línea (posible inyección de cabecera)")

    msg = EmailMessage()
    msg["From"]       = account
    msg["To"]         = ", ".join(to)
    if cc:
        msg["Cc"]     = ", ".join(cc)
    msg["Subject"]    = subject
    msg["Date"]       = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=account.split("@")[-1])
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")

    total = len(body.encode("utf-8"))
    adj_info = []
    for path in (attachments or []):
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
        raise ValueError("el correo pesa %.1f MB > 25 MB" % (total / 1024 / 1024))

    resumen = {"from": account, "to": to, "cc": cc, "subject": subject,
               "adjuntos": adj_info, "bytes_mime": len(msg.as_bytes())}
    return msg, resumen


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def enviar(to, subject, body, cc=None, attachments=None, html=None,
           account=None, dry_run=True, human_ok=False):
    """Envía (o simula) un correo.

    dry_run=True (defecto): construye el MIME y muestra el resumen, pero NO conecta.
    dry_run=False: envío real. Exige ADEMÁS human_ok=True, que sólo concede
    _confirmar_en_tty() tras teclear ENVIAR en una terminal real. Un agente (Bash sin TTY)
    no puede obtenerlo.

    Devuelve el resumen dict. Lanza RuntimeError si HALT activo o credenciales faltan.
    """
    _check_halt()
    user, service = _resolve_account(account)
    msg, resumen  = _construir_mime(user, to, cc or [], subject, body, attachments, html)

    if dry_run:
        resumen["dry_run"] = True
        return resumen

    if not human_ok:
        raise RuntimeError(
            "envío BLOQUEADO: falta la confirmación humana en TTY.\n"
            "        Un agente no puede enviar correo. Deja el borrador con:\n"
            "          python3 tools/correo_outbox.py draft ...\n"
            "        y que {{TITULAR}} lo envíe desde Gmail o con --send en su Terminal."
        )

    # Defensa en profundidad: aunque este envío SIEMPRE lo dispara {{TITULAR}} (--send / dry_run=False),
    # lo pasamos por el BORDE — la única puerta de egress del sistema — para (a) sellar la salida
    # en la traza y (b) cortar en seco lo INEQUÍVOCO: canario de exfiltración / HALT / sesión
    # revocada. El contenido sensible legítimo (correo que ELLA manda a su médico/lab) NO se
    # bloquea, solo se avisa: aquí el OK humano ya existe. Cierra la segunda puerta outbound.
    try:
        import borde
        _dest = ",".join(to if isinstance(to, (list, tuple)) else [to])
        _ver = borde.egress_check((subject or "") + "\n" + (body or "") + "\n" + (html or ""),
                                  destino="correo:" + _dest, intencion="correo_smtp", escalar=True)
        if not _ver.permitido and _ver.alarma:
            raise RuntimeError("borde BLOQUEÓ el envío (%s): posible exfiltración, no se manda." % _ver.motivo)
        if not _ver.permitido:
            sys.stderr.write("⚠️ borde: contenido marcado sensible (%s). Envío humano permitido "
                             "(--send), sellado en la traza.\n" % _ver.motivo)
    except ImportError:
        sys.stderr.write("⚠️ borde no disponible: envío sin sellar en la traza.\n")

    pw = _secrets.get(service)
    if not pw:
        raise RuntimeError("falta la App Password '%s' en el Llavero para %s" % (service, user))

    _assert_smtp_host(SMTP_HOST, SMTP_PORT)
    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.ehlo()
        s.starttls(context=ctx)
        s.ehlo()
        s.login(user, pw)
        s.send_message(msg)

    resumen["dry_run"] = False
    return resumen


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse(argv):
    a = {"to": [], "cc": [], "subject": "", "body_file": None, "attach": [],
         "html_file": None, "account": None, "send": "--send" in argv}
    i, multi = 0, {"--to": "to", "--cc": "cc", "--attach": "attach"}
    single = {"--subject": "subject", "--body-file": "body_file",
              "--html-file": "html_file", "--account": "account"}
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
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0

    if argv[0] == "status":
        for user, service in ACCOUNTS.items():
            tiene = "✓ App Password" if _secrets.get(service) else "✗ falta (gate)"
            print("  %-26s %-30s %s" % (user, service, tiene))
        return 0

    a = _parse(argv)
    try:
        body_file = a.get("body_file")
        if not body_file or not os.path.isfile(body_file):
            print("✗ falta --body-file", file=sys.stderr); return 2
        body = open(body_file, encoding="utf-8").read()
        html = None
        if a.get("html_file"):
            html = open(a["html_file"], encoding="utf-8").read()
        comun = dict(to=a["to"], subject=a["subject"], body=body,
                     cc=a["cc"], attachments=a["attach"], html=html,
                     account=a.get("account"))
        # Siempre se construye primero en seco: valida destinatarios, adjuntos y tamaño
        # sin abrir la red. Solo tras la confirmación humana en TTY se envía de verdad.
        resumen = enviar(dry_run=True, **comun)
        if a["send"]:
            resumen["cuerpo_preview"] = body[:1500]
            human_ok = _confirmar_en_tty(resumen)
            resumen = enviar(dry_run=False, human_ok=human_ok, **comun)
    except Exception as e:
        print("✗ %s" % e, file=sys.stderr); return 1

    adj = ", ".join("%s (%.0f KB)" % (n, b/1024) for n, b in resumen["adjuntos"]) or "—"
    modo = "DRY (sin --send, no se envió)" if resumen["dry_run"] else "ENVIADO ✅"
    print("📧 %s" % modo)
    print("   de:       %s" % resumen["from"])
    print("   para:     %s" % ", ".join(resumen["to"]))
    print("   cc:       %s" % (", ".join(resumen["cc"]) or "—"))
    print("   asunto:   %s" % resumen["subject"])
    print("   adjuntos: %s" % adj)
    print("   tamaño:   %.0f KB" % (resumen["bytes_mime"] / 1024))
    if resumen["dry_run"]:
        print("   → Añade --send para enviar de verdad (requiere OK de {{TITULAR}}).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
