#!/usr/bin/env python3
"""Trackea chats de WhatsApp desde la base LOCAL del WhatsApp de escritorio
(ChatStorage.sqlite). 100% local, SIN WhatsApp Web (sin riesgo de ban); nada sale del Mac.
Vuelca los chats elegidos a 00_FUENTE-DE-VERDAD/_PRIVADO_WHATSAPP/ (gitignored).

Las NOTAS DE VOZ (tipo 3: .opus en el app antiguo, .m4a/AAC en el Catalyst nuevo) se
transcriben con Whisper en local (decodificación vía ffmpeg) y se integran en el
.md del chat en ORDEN CRONOLÓGICO junto al texto, marcadas con 🎙️. Solo se transcriben las
que WhatsApp ya descargó a este Mac (ZMEDIALOCALPATH presente + fichero en disco); las que
nunca se descargaron no están en el disco y no se pueden transcribir hasta abrir ese chat.
La transcripción se cachea (idempotente): reejecutar no re-transcribe lo ya hecho.

Uso:
  python3 tools/wa_tracker.py --list                 # lista chats (nº msgs + nombre)
  python3 tools/wa_tracker.py --active [--since 24]  # chats con actividad reciente (modo diario, def. 24h)
  python3 tools/wa_tracker.py "{{CONTACTO}} {{CONTACTO}}" ...    # trackea esos chats (texto + audios)
  python3 tools/wa_tracker.py --exported             # trackea los que exportaste (~/Downloads/.wa_extract)
  python3 tools/wa_tracker.py --no-audio "..."        # solo texto (no transcribe)
  python3 tools/wa_tracker.py --model small "..."     # modelo whisper (base|small|medium; def. base)

Requiere para audio: ffmpeg (brew install ffmpeg) + openai-whisper soundfile numpy
(en Polaris: usar el venv ~/claudecode/.venv que ya los trae).
"""
import os, sys, sqlite3, shutil, tempfile, datetime, re, glob, unicodedata, json

# Esta carpeta (tools/) NO debe tapar módulos de la stdlib: al ejecutar
# `python tools/wa_tracker.py`, Python mete tools/ en sys.path[0] y un dep de Whisper
# (`from queue import Queue`) cazaría tools/queue.py (la cola del lazo 24/7) en vez del
# `queue` estándar → la transcripción se cae. wa_tracker no importa tools hermanos por
# nombre, así que quitar su propio dir de sys.path es seguro y arregla los audios.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _HERE]

DB = os.path.expanduser("~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite")
# Las rutas ZMEDIALOCALPATH ("Media/<jid>/x/y/uuid.opus") son relativas a .shared/Message/
MEDIA_BASE = os.path.expanduser("~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/Message")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "00_FUENTE-DE-VERDAD", "_PRIVADO_WHATSAPP")
CACHE = os.path.join(ROOT, ".audio_cache.json")  # transcripciones cacheadas (gitignored), idempotencia
EPOCH = 978307200  # Core Data epoch: 2001-01-01
AUDIO_TYPE = 3     # ZMESSAGETYPE de nota de voz (.opus app antiguo / .m4a app nuevo) — verificado en esta BD
IMG_CACHE = os.path.join(ROOT, ".image_cache.json")  # texto OCR cacheado (gitignored), idempotente
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".heic")  # imágenes a OCR-ar (Apple Vision local)
SKIP = ("locuras ia",)  # chats que {{TITULAR}} pidió ignorar (ya no existen)

# --- Transcripción de voz (Whisper en local) ---------------------------------
# Carga perezosa: solo se importa/instancia whisper si hay audios que transcribir.
_WHISPER = {"model": None, "name": None, "cache": None, "deps_ok": None}

def _load_cache():
    if _WHISPER["cache"] is None:
        try:
            _WHISPER["cache"] = json.load(open(CACHE, encoding="utf-8"))
        except Exception:
            _WHISPER["cache"] = {}
    return _WHISPER["cache"]

def _save_cache():
    if _WHISPER["cache"] is not None:
        os.makedirs(ROOT, exist_ok=True)
        tmp = CACHE + ".tmp"
        json.dump(_WHISPER["cache"], open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, CACHE)

def transcribe(relpath, model_name):
    """Transcribe una nota de voz (.opus/.m4a, ruta relativa a Message/) → texto. Cacheado e idempotente.
    Devuelve None si el fichero no está en disco o faltan dependencias."""
    return _transcribe_file(os.path.join(MEDIA_BASE, relpath), model_name, key=relpath)

def transcribe_abs(abspath, model_name, key=None):
    """Igual que transcribe(), pero para una ruta ABSOLUTA (p.ej. audios de una exportación de
    WhatsApp del móvil, fuera de Message/). Reusa el mismo motor y cache de Whisper; no duplica
    lógica. `key` fija la clave de cache (por defecto la propia ruta absoluta). Lo usa voz_intima.py."""
    return _transcribe_file(abspath, model_name, key=key or abspath)

def _transcribe_file(fp, model_name, key):
    """Núcleo compartido: decodifica el fichero `fp`, transcribe con Whisper y cachea por `key`.
    Devuelve None si el fichero no está en disco o faltan dependencias."""
    if not os.path.isfile(fp):
        return None
    cache = _load_cache()
    if key in cache:
        return cache[key]
    # Importa deps solo la primera vez que de verdad hace falta transcribir algo
    if _WHISPER["deps_ok"] is None:
        try:
            import numpy, soundfile, whisper  # noqa: F401
            _WHISPER["deps_ok"] = True
        except Exception as e:
            print("  ! falta dependencia para audio (%s). Instala: pip install --user openai-whisper soundfile" % e)
            _WHISPER["deps_ok"] = False
    if not _WHISPER["deps_ok"]:
        return None
    import numpy as np, soundfile as sf, whisper
    if _WHISPER["model"] is None or _WHISPER["name"] != model_name:
        print("  · cargando whisper '%s' (una vez)…" % model_name)
        _WHISPER["model"] = whisper.load_model(model_name)
        _WHISPER["name"] = model_name
    # Copia el audio a una carpeta SIN protección TCC antes de decodificar: así ffmpeg
    # (whisper.load_audio) y libsndfile nunca tocan el contenedor protegido de WhatsApp y
    # macOS NO pide permiso por cada nota. La copia la hace este Python, que SÍ tiene FDA.
    ext = os.path.splitext(fp)[1] or ".m4a"
    tmp_audio = os.path.join(tempfile.gettempdir(), "wa_audio_%d%s" % (os.getpid(), ext))
    try:
        try:
            shutil.copy2(fp, tmp_audio)
            src = tmp_audio
        except Exception:
            src = fp  # si la copia fallara, decodifica directo (puede pedir permiso una vez)
        try:
            # Vía robusta: ffmpeg (whisper.load_audio) decodifica cualquier formato
            # (.m4a/AAC del WhatsApp Catalyst nuevo, .opus del antiguo) → 16 kHz mono float32.
            data = whisper.load_audio(src)
        except Exception:
            # Reserva sin ffmpeg: soundfile (solo lo que libsndfile decodifica, p.ej. .opus/.wav).
            data, sr = sf.read(src, dtype="float32")
            if getattr(data, "ndim", 1) > 1:
                data = data.mean(axis=1)
            if sr != 16000:  # whisper espera 16 kHz; remuestreo simple sin ffmpeg
                n = int(len(data) * 16000 / sr)
                data = np.interp(np.linspace(0, len(data), n, endpoint=False), np.arange(len(data)), data).astype("float32")
        txt = _WHISPER["model"].transcribe(data, language="es", fp16=False)["text"].strip()
    except Exception as e:
        txt = "[ERROR transcribiendo: %s]" % e
    finally:
        try:
            os.remove(tmp_audio)
        except Exception:
            pass  # benigno: cleanup best-effort del temporal (puede no existir ya)
    cache[key] = txt
    _save_cache()  # persistir tras cada audio: si se corta de noche, no se pierde lo hecho
    return txt

# --- OCR de imágenes (Apple Vision en local, vía ocrmac) ----------------------
# Mismo patrón que la voz: solo imágenes YA descargadas en disco; texto cacheado e idempotente;
# 100% local (Apple Vision on-device), nada sale del Mac. Solo se vuelca la imagen si tiene texto.
_OCR = {"cache": None, "ok": None}

def _load_img_cache():
    if _OCR["cache"] is None:
        try:
            _OCR["cache"] = json.load(open(IMG_CACHE, encoding="utf-8"))
        except Exception:
            _OCR["cache"] = {}
    return _OCR["cache"]

def _save_img_cache():
    if _OCR["cache"] is not None:
        os.makedirs(ROOT, exist_ok=True)
        tmp = IMG_CACHE + ".tmp"
        json.dump(_OCR["cache"], open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, IMG_CACHE)

def ocr_image(relpath):
    """OCR LOCAL (Apple Vision vía ocrmac) de una imagen ya descargada (ruta relativa a Message/).
    Cacheado/idempotente. Devuelve el texto hallado ('' si la imagen no tiene texto) o None si el
    fichero no está en disco o falta ocrmac. Como en el audio, se copia a /tmp antes de leer para
    no tocar el contenedor protegido de WhatsApp (evita prompts de permiso por cada imagen)."""
    fp = os.path.join(MEDIA_BASE, relpath)
    if not os.path.isfile(fp):
        return None
    cache = _load_img_cache()
    if relpath in cache:
        return cache[relpath]
    if _OCR["ok"] is None:
        try:
            from ocrmac import ocrmac  # noqa: F401
            _OCR["ok"] = True
        except Exception as e:
            print("  ! falta ocrmac para imágenes (%s). Instala: pip install ocrmac" % e)
            _OCR["ok"] = False
    if not _OCR["ok"]:
        return None
    from ocrmac import ocrmac
    ext = os.path.splitext(fp)[1] or ".jpg"
    tmp_img = os.path.join(tempfile.gettempdir(), "wa_img_%d%s" % (os.getpid(), ext))
    try:
        try:
            shutil.copy2(fp, tmp_img); src = tmp_img
        except Exception:
            src = fp
        try:
            ann = ocrmac.OCR(src, recognition_level="accurate",
                             language_preference=["es-ES", "en-US"]).recognize()
            txt = "\n".join(t for (t, conf, _b) in ann if t and conf >= 0.3).strip()
        except Exception as e:
            txt = "[ERROR OCR: %s]" % e
    finally:
        try:
            os.remove(tmp_img)
        except Exception:
            pass  # benigno: cleanup best-effort del temporal (puede no existir ya)
    cache[relpath] = txt
    _save_img_cache()  # persistir tras cada imagen (idempotencia aunque se corte)
    return txt

def norm(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower().strip()

def connect():
    tmp = os.path.join(tempfile.gettempdir(), "wa_copy.sqlite")
    shutil.copy2(DB, tmp)  # copia para no chocar con WhatsApp abierto
    # WhatsApp usa SQLite en modo WAL: los mensajes MÁS RECIENTES viven en el fichero
    # `-wal` hasta que se hace checkpoint. Copiar solo el `.sqlite` se los PIERDE (p.ej. un
    # mensaje recién enviado/recibido no aparece). Copiamos también `-wal`/`-shm` con el MISMO
    # basename para que SQLite aplique el WAL al abrir y veamos el estado al día.
    for ext in ("-wal", "-shm"):
        src, dst = DB + ext, tmp + ext
        if os.path.exists(src):
            shutil.copy2(src, dst)
        elif os.path.exists(dst):
            os.remove(dst)  # limpia restos de una corrida previa para no leer un WAL viejo
    return sqlite3.connect(tmp)

def all_chats(c):
    return c.execute("SELECT Z_PK, ZPARTNERNAME, ZMESSAGECOUNTER FROM ZWACHATSESSION WHERE ZPARTNERNAME IS NOT NULL").fetchall()

def active_chats(c, hours=24):
    """Sesiones con al menos un mensaje (entrante o saliente) en las últimas `hours` horas.
    Para el modo diario: 'sigue los chats con los que he hablado o me han hablado'."""
    thr = int(datetime.datetime.now().timestamp()) - EPOCH - hours * 3600  # umbral en unidades ZMESSAGEDATE
    return c.execute(
        "SELECT DISTINCT s.Z_PK, s.ZPARTNERNAME "
        "FROM ZWACHATSESSION s JOIN ZWAMESSAGE m ON m.ZCHATSESSION = s.Z_PK "
        "WHERE s.ZPARTNERNAME IS NOT NULL AND m.ZMESSAGEDATE >= ? "
        "ORDER BY s.ZPARTNERNAME", (thr,)).fetchall()

def find_pk(allc, name):
    k = norm(name)
    exact = [(pk, cnt or 0) for pk, nm, cnt in allc if norm(nm) == k]
    part = [(pk, cnt or 0) for pk, nm, cnt in allc if k and (k in norm(nm) or norm(nm) in k)]
    cands = exact or part          # exacto si lo hay; si no, parcial
    return max(cands, key=lambda x: x[1])[0] if cands else None   # la sesión con MÁS mensajes

def export(c, pk, name, do_audio=True, model_name="base"):
    # 1) Mensajes de texto
    rows = c.execute(
        "SELECT ZMESSAGEDATE, ZISFROMME, ZTEXT FROM ZWAMESSAGE "
        "WHERE ZCHATSESSION=? AND ZTEXT IS NOT NULL ORDER BY ZMESSAGEDATE", (pk,)).fetchall()
    # Cada item del timeline: (fecha, es_mio, es_audio, contenido)
    items = [(d or 0, bool(fromme), "", t) for d, fromme, t in rows]

    # 2) Notas de voz (tipo 3) con fichero ya descargado en disco → transcribir
    n_audio = n_audio_ok = 0
    if do_audio:
        arows = c.execute(
            "SELECT m.ZMESSAGEDATE, m.ZISFROMME, mi.ZMEDIALOCALPATH "
            "FROM ZWAMESSAGE m JOIN ZWAMEDIAITEM mi ON m.ZMEDIAITEM=mi.Z_PK "
            "WHERE m.ZCHATSESSION=? AND m.ZMESSAGETYPE=? "
            "AND mi.ZMEDIALOCALPATH IS NOT NULL "
            "AND (mi.ZMEDIALOCALPATH LIKE '%.opus' OR mi.ZMEDIALOCALPATH LIKE '%.m4a') "
            "ORDER BY m.ZMESSAGEDATE", (pk, AUDIO_TYPE)).fetchall()
        for d, fromme, rel in arows:
            n_audio += 1
            txt = transcribe(rel, model_name)
            if txt is None:           # no en disco / sin deps → marcar como pendiente, no romper orden
                txt = "[audio no descargado en este Mac — abre el chat en WhatsApp para bajarlo]"
            else:
                n_audio_ok += 1
            items.append((d or 0, bool(fromme), "audio", txt))

    # 2b) Imágenes con texto (fotos de vuelos, entradas, reservas, capturas) → OCR LOCAL (Apple Vision).
    # Solo las ya descargadas y que CONTIENEN texto; las demás (selfies, stickers, no bajadas) se omiten
    # para no ensuciar el .md. Idempotente por cache.
    n_img = n_img_ok = 0
    like = " OR ".join("LOWER(mi.ZMEDIALOCALPATH) LIKE '%" + e + "'" for e in IMG_EXTS)
    irows = c.execute(
        "SELECT m.ZMESSAGEDATE, m.ZISFROMME, mi.ZMEDIALOCALPATH "
        "FROM ZWAMESSAGE m JOIN ZWAMEDIAITEM mi ON m.ZMEDIAITEM=mi.Z_PK "
        "WHERE m.ZCHATSESSION=? AND mi.ZMEDIALOCALPATH IS NOT NULL AND (" + like + ") "
        "ORDER BY m.ZMESSAGEDATE", (pk,)).fetchall()
    for d, fromme, rel in irows:
        n_img += 1
        txt = ocr_image(rel)
        if not txt:            # no descargada, sin ocrmac, o imagen sin texto → no ensuciar el .md
            continue
        n_img_ok += 1
        items.append((d or 0, bool(fromme), "image", txt))

    # 3) Orden cronológico (texto + audio + imágenes entrelazados) y volcado
    items.sort(key=lambda x: x[0])
    os.makedirs(ROOT, exist_ok=True)
    safe = re.sub(r"[^\w\- ]", "_", name).strip() or "chat"
    with open(os.path.join(ROOT, safe + ".md"), "w", encoding="utf-8") as f:
        f.write("# WhatsApp · %s\n\n> Volcado LOCAL privado · %d mensajes (%d notas de voz 🎙️, %d imágenes con texto 🖼️) · whisper `%s` · OCR Apple Vision · actualizado %s\n\n" % (
            name, len(items), n_audio_ok, n_img_ok, model_name if do_audio else "—",
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
        for d, fromme, kind, content in items:
            ts = datetime.datetime.fromtimestamp(EPOCH + d).strftime("%d/%m/%y %H:%M")
            who = "yo" if fromme else name
            if kind == "audio":
                f.write("**[%s] %s:** 🎙️ %s\n\n" % (ts, who, content))
            elif kind == "image":
                f.write("**[%s] %s:** 🖼️ %s\n\n" % (ts, who, content))
            else:
                f.write("**[%s] %s:** %s\n\n" % (ts, who, content))
    return len(items), n_audio, n_audio_ok, n_img, n_img_ok

def main():
    if not os.path.exists(DB):
        print("No encuentro ChatStorage.sqlite"); return
    c = connect(); allc = all_chats(c)
    args = sys.argv[1:]
    # Flags: --no-audio (solo texto) · --model <name> (whisper base|small|medium)
    do_audio, model_name = True, "base"
    if "--no-audio" in args:
        do_audio = False; args = [a for a in args if a != "--no-audio"]
    if "--model" in args:
        i = args.index("--model")
        if i + 1 < len(args):
            model_name = args[i + 1]; del args[i:i + 2]
    # --active [--since H]: sigue los chats con actividad reciente (modo diario, def. 24h)
    if "--active" in args:
        args = [a for a in args if a != "--active"]
        hours = 24
        if "--since" in args:
            i = args.index("--since")
            if i + 1 < len(args):
                try: hours = int(args[i + 1])
                except ValueError as _e:
                    try:
                        import errores as _err
                        _err.registrar("wa_tracker", _e, _err.CONFIG, job="args", escalar=True)
                    except Exception:
                        pass  # benigno: el bus de errores ya es fail-safe; este es el blindaje del propio aviso
                del args[i:i + 2]
        actives = active_chats(c, hours)
        print("· %d chats con actividad en las ultimas %dh" % (len(actives), hours))
        ok = 0
        for pk, name in actives:
            if any(s in norm(name) for s in SKIP):
                continue
            try:
                total, n_aud, n_ok, n_img, n_img_ok = export(c, pk, name, do_audio=do_audio, model_name=model_name)
                extra = ((" · %d/%d audios 🎙️" % (n_ok, n_aud)) if n_aud else "") + ((" · %d🖼️" % n_img_ok) if n_img_ok else "")
                print("  ok %s: %d msgs%s" % (name, total, extra)); ok += 1
            except Exception as e:
                print("  ERROR %s: %s" % (name, e))
        print("--- %d chats activos volcados -> %s" % (ok, os.path.normpath(ROOT)))
        return
    if args and args[0] == "--list":
        for pk, n, cnt in sorted(allc, key=lambda x: -(x[2] or 0))[:80]:
            print("%6d  %s" % (cnt or 0, n))
        return
    if args and args[0] == "--exported":
        names = [os.path.basename(p).replace("WhatsApp Chat - ", "")
                 for p in glob.glob(os.path.expanduser("~/Downloads/.wa_extract/*"))]
    else:
        names = args
    if not names:
        print("Dame nombres de chat, --list o --exported"); return
    ok = 0
    for n in names:
        if any(s in norm(n) for s in SKIP):
            continue
        pk = find_pk(allc, n)
        if not pk:
            print("  x no encontrado: %s" % n); continue
        try:
            total, n_aud, n_ok, n_img, n_img_ok = export(c, pk, n, do_audio=do_audio, model_name=model_name)
            extra = ((" · %d/%d audios transcritos 🎙️" % (n_ok, n_aud)) if n_aud else "") + ((" · %d imágenes con texto 🖼️" % n_img_ok) if n_img_ok else "")
            print("  ok %s: %d msgs%s" % (n, total, extra)); ok += 1
        except Exception as e:
            print("  ERROR %s: %s" % (n, e))
    print("--- %d chats volcados -> %s" % (ok, os.path.normpath(ROOT)))

if __name__ == "__main__":
    main()
