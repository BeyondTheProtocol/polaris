#!/usr/bin/env python3
"""tools/memoria_contactos.py — F4.2: la ficha viva de un contacto, EN UNA LLAMADA (Vega va a la fuente).

Cuando {{TITULAR}} menciona a alguien ("qué dice {{CONTACTO}}", "lo de {{CONTACTO}}"), Vega NO debe re-grepear ni
preguntarle el canal: ya lo SABE. Este tool AGREGA, determinista y LOCAL, lo que el sistema ya tiene
del contacto: su dossier curado (`00_FUENTE-DE-VERDAD/Seguimiento-Contactos/`) + el CANAL preferente
(deducido del nombre del dossier: X-DM, IG-DM, LinkedIn, WhatsApp, audio, correo) + la conversación
reciente de WhatsApp (reusa `leer_contacto`). Una sola llamada = contexto completo, sin re-derivar.
Cierra la lección [[feedback-canal-y-contexto-del-contacto-ya-lo-se]] ({{CONTACTO}} = DM de X).

Muro / privacidad: 100% LOCAL, 0 egress propio. La salida es PII (datos/conversación del contacto):
quien la consuma la trata como SENSIBLE (cerebro de CONFIANZA, nunca carril gratis ni Telegram en
crudo; `deid.py` antes de cualquier cerebro). Cachea en `_PRIVADO_NUCLEO/asistente/contactos/<slug>.md`
(gitignored). Resuelve a casa base (BTP_REPO o ~/claudecode). Espejo del estilo de `leer_contacto.py`.

Uso:
  python3 tools/memoria_contactos.py "{{CONTACTO}}"            # ficha agregada
  python3 tools/memoria_contactos.py "{{CONTACTO}}" --no-whatsapp
"""
import glob
import os
import re
import sys
import unicodedata
from datetime import datetime

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
FUENTE = os.environ.get("BTP_FUENTE_DIR") or os.path.join(REPO, "00_FUENTE-DE-VERDAD")
CONTACTOS = os.path.join(FUENTE, "Seguimiento-Contactos")
CACHE_DIR = os.path.join(FUENTE, "_PRIVADO_NUCLEO", "asistente", "contactos")
MAX_DOSSIER = 4000

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Canal preferente deducido del nombre del dossier (lo más concreto primero).
_CANALES = [("x-dm", "DM de X"), ("ig-dm", "DM de Instagram"), ("instagram", "DM de Instagram"),
            ("linkedin", "DM de LinkedIn"), ("whatsapp", "WhatsApp"), ("1a1", "WhatsApp"),
            ("audio", "nota de voz"), ("email", "correo"), ("correo", "correo")]


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def _canal(fname):
    f = _norm(fname)
    for k, v in _CANALES:
        if k in f:
            return v
    return None


def _dossiers(nombre):
    """.md de Seguimiento-Contactos cuyo nombre contiene el buscado (más recientes primero)."""
    parts = _norm(nombre).split()
    k = parts[0] if parts else ""
    if not k:
        return []
    out = [p for p in glob.glob(os.path.join(CONTACTOS, "*.md")) if k in _norm(os.path.basename(p))]
    return sorted(out, key=os.path.getmtime, reverse=True)


def ficha(nombre, con_whatsapp=True):
    """Ficha agregada (str). Nunca lanza: ante un fallo devuelve un aviso en texto."""
    doss = _dossiers(nombre)
    lineas = ["# Ficha de contacto: %s" % nombre]
    canal = next((c for c in (_canal(os.path.basename(p)) for p in doss) if c), None)
    if canal:
        lineas.append("**Canal preferente:** %s  (de su dossier — no le preguntes a {{TITULAR}})" % canal)
    if doss:
        lineas.append("**Dossier(s):** " + ", ".join(os.path.basename(p) for p in doss[:4]))
        try:
            with open(doss[0], encoding="utf-8") as f:
                txt = f.read()[:MAX_DOSSIER]
            lineas += ["", "## Dossier curado (la fuente):", txt.strip()]
        except OSError as e:
            lineas.append("(no pude leer el dossier: %s)" % e)
    else:
        lineas.append("(sin dossier en Seguimiento-Contactos para «%s»)" % nombre)
    if con_whatsapp:
        try:
            import leer_contacto
            wa = leer_contacto.leer(nombre, no_audio=True)
            if wa and not wa.startswith("["):          # "[...]" = aviso de error de leer_contacto
                lineas += ["", "## WhatsApp reciente:", wa[:3000]]
        except Exception:
            pass
    lineas.append("\n_(LOCAL · PII → solo cerebro de confianza · %s)_" % datetime.now().strftime("%Y-%m-%d %H:%M"))
    out = "\n".join(lineas)
    _cachear(nombre, out)
    return out


def _cachear(nombre, contenido):
    try:
        os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
        slug = re.sub(r"[^\w\-]", "_", _norm(nombre))[:50] or "x"
        p = os.path.join(CACHE_DIR, slug + ".md")
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(contenido)
        os.replace(tmp, p)
    except Exception:
        pass


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    con_wa = "--no-whatsapp" not in argv
    if not args or (argv and argv[0] in ("-h", "--help")):
        print('uso: memoria_contactos.py "<contacto>" [--no-whatsapp]')
        return 0 if not args else 0
    print(ficha(" ".join(args), con_whatsapp=con_wa))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
