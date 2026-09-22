#!/usr/bin/env python3
"""tools/leer_contacto.py — lee la conversación de WhatsApp de un contacto, BAJO DEMANDA.

Para Vega (y cualquier carril de confianza): cuando {{TITULAR}} menciona a alguien
("qué dice {{CONTACTO}}", "el sitio que mandó Marina"), esto le da la conversación en UNA línea,
en vez de pedirle que la copie. Si el volcado local está viejo o no existe, lanza
`wa_tracker.py` para ESE contacto y devuelve los mensajes recientes.

100% LOCAL, sin egress: reusa `tools/wa_tracker.py` (lee ChatStorage.sqlite local; texto +
notas de voz transcritas con Whisper + OCR de imágenes). No reimplementa nada de eso.

MURO: la salida es PII (conversación privada). Quien la consuma la trata como dato sensible:
va a un cerebro de CONFIANZA (Claude/local), NUNCA a un cerebro gratis externo ni a Telegram
en crudo. Este helper no envía nada a ningún sitio: solo lee local y devuelve texto.

Uso:
  python3 tools/leer_contacto.py "{{CONTACTO}} {{CONTACTO}}"            # conversación reciente (con audio)
  python3 tools/leer_contacto.py "Marina" --no-audio       # solo texto (más rápido)
  python3 tools/leer_contacto.py "{{CONTACTO}}" --frescura 1     # re-volca si el .md tiene > 1h
  python3 tools/leer_contacto.py "{{CONTACTO}}" --full           # toda la conversación, no solo el final
  python3 tools/leer_contacto.py "{{CONTACTO}}" --max 60         # últimos 60 mensajes (def. 40)

En Python:
  from leer_contacto import leer
  texto = leer("{{CONTACTO}} {{CONTACTO}}")   # str con la conversación, o un aviso claro si no se pudo
"""
import datetime
import glob
import os
import re
import subprocess
import sys
import time
import unicodedata

# Los datos privados (WhatsApp/correo) viven SOLO en casa base (gitignored, no en worktrees).
# Por eso apuntamos a casa base (BTP_REPO o ~/claudecode), nunca al árbol relativo al fichero.
REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
ROOT = os.path.join(REPO, "00_FUENTE-DE-VERDAD", "_PRIVADO_WHATSAPP")
WA = os.path.join(REPO, "tools", "wa_tracker.py")
DB = os.path.expanduser("~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite")

FRESCURA_H = 6.0     # si el .md es más nuevo que esto, no re-volca (cache)
MAX_MSGS = 40        # mensajes recientes por defecto (el final es lo que suele importar)
_MSG_RE = re.compile(r"^\*\*\[", re.M)   # cada mensaje del volcado empieza así


def _norm(s):
    """minúsculas + sin acentos, para casar nombres de forma laxa."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.lower().strip()


def _safe(name):
    """mismo saneado de nombre→fichero que wa_tracker.export (para predecir el .md)."""
    return re.sub(r"[^\w\- ]", "_", name).strip() or "chat"


def _buscar_md(nombre):
    """El .md del contacto: primero el nombre exacto saneado; si no, cualquier .md cuyo
    nombre contenga (parcial) el buscado, el más reciente. Devuelve ruta o None."""
    exacto = os.path.join(ROOT, _safe(nombre) + ".md")
    if os.path.isfile(exacto):
        return exacto
    k = _norm(nombre)
    cands = []
    for p in glob.glob(os.path.join(ROOT, "*.md")):
        base = _norm(os.path.basename(p)[:-3])
        if k and (k in base or base in k):
            cands.append(p)
    return max(cands, key=os.path.getmtime) if cands else None


def _tail(ruta, max_msgs, full=False):
    """Cabecera del volcado + últimos `max_msgs` mensajes (o todo si full)."""
    try:
        with open(ruta, encoding="utf-8") as f:
            txt = f.read()
    except OSError as e:
        return "[no pude leer el volcado: %s]" % e
    if full:
        return txt.strip()
    partes = _MSG_RE.split(txt)
    cabecera = partes[0].strip()
    msgs = ["**[" + m for m in partes[1:]]
    recientes = msgs[-max_msgs:]
    aviso = "" if len(msgs) <= max_msgs else "\n_(mostrando los %d mensajes más recientes de %d; usa --full para todo)_\n" % (max_msgs, len(msgs))
    return (cabecera + "\n\n" + aviso + "\n".join(m.strip() for m in recientes)).strip()


def leer(nombre, *, frescura_h=FRESCURA_H, no_audio=False, max_msgs=MAX_MSGS, full=False, model=None):
    """Devuelve la conversación reciente de `nombre` (str). Re-volca con wa_tracker si el
    volcado falta o está más viejo que `frescura_h`. Nunca lanza: ante un fallo devuelve un
    aviso en texto claro para que Vega actúe igual."""
    if not os.path.exists(DB):
        return "[WhatsApp no accesible: no encuentro ChatStorage.sqlite en este Mac]"

    md = _buscar_md(nombre)
    fresco = md and (time.time() - os.path.getmtime(md)) < frescura_h * 3600
    if not fresco:
        cmd = [sys.executable, WA]
        if no_audio:
            cmd.append("--no-audio")
        if model:
            cmd += ["--model", model]
        cmd.append(nombre)
        try:
            r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=600)
            if "no encontrado" in (r.stdout + r.stderr):
                return "[no encuentro a «%s» en WhatsApp; prueba con otro nombre o revisa que el chat exista]" % nombre
        except subprocess.TimeoutExpired:
            # transcribir audio puede tardar; si había un volcado viejo, lo damos igual
            if md:
                return _tail(md, max_msgs, full) + "\n\n_(volcado anterior; el refresco no terminó a tiempo)_"
            return "[el volcado de «%s» está tardando demasiado; reintenta con --no-audio]" % nombre
        except Exception as e:
            if md:
                return _tail(md, max_msgs, full)
            return "[no pude leer WhatsApp de «%s»: %s]" % (nombre, e)
        md = _buscar_md(nombre)

    if not md:
        return "[no hay conversación de «%s» (¿nombre distinto en WhatsApp?). Lista: python3 tools/wa_tracker.py --list]" % nombre
    return _tail(md, max_msgs, full)


def _main(argv):
    args = list(argv)
    no_audio = "--no-audio" in args
    full = "--full" in args
    args = [a for a in args if a not in ("--no-audio", "--full")]
    frescura_h, max_msgs, model = FRESCURA_H, MAX_MSGS, None
    for flag, conv in (("--frescura", float), ("--max", int), ("--model", str)):
        if flag in args:
            i = args.index(flag)
            if i + 1 < len(args):
                val = args[i + 1]
                del args[i:i + 2]
                try:
                    v = conv(val)
                except ValueError:
                    continue
                if flag == "--frescura":
                    frescura_h = v
                elif flag == "--max":
                    max_msgs = v
                else:
                    model = v
    nombre = " ".join(args).strip()
    if not nombre:
        print('uso: python3 tools/leer_contacto.py "<contacto>" [--no-audio] [--full] [--frescura H] [--max N]')
        return 2
    print(leer(nombre, frescura_h=frescura_h, no_audio=no_audio, max_msgs=max_msgs, full=full, model=model))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
