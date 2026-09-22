#!/usr/bin/env python3
"""Captura PERMANENTE de la voz hablada ÍNTIMA de {{TITULAR}} a partir de SUS notas de voz de
WhatsApp, para alimentar su gemelo de voz (agente `voz-titular`).

Su voz escrita ya la tenemos de sobra; lo que falta es cómo habla de verdad, en confianza:
el ritmo, las coletillas, cómo arranca y cómo cierra una idea cuando no está redactando. Eso
solo vive en las notas de voz que MANDA ella. Este script las recoge, las transcribe en local
con Whisper (reutilizando el motor de wa_tracker.py, sin duplicarlo) y va construyendo un corpus
datado en 00_FUENTE-DE-VERDAD/_PRIVADO_NUCLEO/voz-intima-corpus.md.

100% local y privado. NADA sale del Mac: solo lee su WhatsApp local y escribe el corpus en una
carpeta gitignored. Solo entran SUS notas de voz (ZISFROMME=1), nunca las de otra persona. El
audio crudo no se versiona; solo el texto, en una carpeta que git ignora.

Dos modos:

  python3 tools/voz_intima.py --collect
      Recorre la BD local de WhatsApp y recoge las notas de voz de ELLA que ya estén
      descargadas en este Mac. En el WhatsApp "Catalyst" nuevo los audios de SALIDA no se
      guardan legibles en disco, así que aquí esperamos pocas o cero hasta que lleguen nuevas
      o se siembre con --ingest-export. Es lo normal; el script lo dice claro y no peta con 0.

  python3 tools/voz_intima.py --ingest-export "~/Downloads/WhatsApp Chat - Fulano"
      Siembra el historial desde una EXPORTACIÓN de WhatsApp del móvil (carpeta con _chat.txt
      + los audios .opus/.m4a). En un export 1-a-1 el chat lleva el nombre del CONTACTO, así
      que el emisor que NO es el contacto es ella. Transcribe SOLO esas notas. Si no puede
      atribuir un audio con fiabilidad, lo descarta y avisa (jamás mete voz de otra persona).
      Como el nombre del emisor NO basta (un número reasignado/compartido cuela voz ajena),
      añade dos salvaguardas: avisa fuerte si el emisor abarca fechas muy separadas y, por
      defecto, pasa un gate de PITCH que descarta lo que no suene a voz femenina (--no-pitch-gate
      lo apaga). Necesita ffmpeg+librosa para el gate; si faltan, se desactiva avisando.

Requiere para audio: ffmpeg + openai-whisper soundfile numpy. En Polaris, usar el venv que ya
los trae: ~/claudecode/.venv/bin/python tools/voz_intima.py ...
"""
import os, sys, re, glob, sqlite3, shutil, tempfile, datetime, unicodedata

# tools/ se mete en sys.path[0] al ejecutar el script y puede tapar módulos de la stdlib que
# Whisper importa (p.ej. `queue`). wa_tracker ya se defiende quitando su dir; aquí, para poder
# importarlo, lo añadimos explícito y luego dejamos que él se limpie a sí mismo.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import wa_tracker  # noqa: E402  (motor de Whisper + constantes de la BD; NO duplicar su lógica)

CORPUS = os.path.join(_HERE, "..", "00_FUENTE-DE-VERDAD", "_PRIVADO_NUCLEO", "voz-intima-corpus.md")
MODEL = "small"  # un punto por encima de "base": la voz íntima merece algo más de fidelidad

# --- Salvaguarda de voz: la atribución por NOMBRE no basta -----------------------
# Un número reasignado/compartido mete voz AJENA bajo el mismo nombre de emisor (caso real:
# este export tenía decenas de notas masculinas de 2018-19 etiquetadas "{{TITULAR}} {{APELLIDO}}", y
# alguna masculina colada incluso en 2025-26). Dos defensas, de barata a cara:
#   1) AVISO FUERTE si el emisor abarca rangos de fechas muy separados (señal de reasignación).
#      Sin dependencias: siempre activo.
#   2) Gate de PITCH (opcional, --no-pitch-gate para apagarlo): la voz de {{TITULAR}} es femenina
#      (F0≈165-240 Hz); descarta audios con F0 mediana en rango no-femenino (~100-140 Hz = voz
#      masculina) y marca los dudosos. Necesita ffmpeg+librosa; si faltan, se desactiva avisando.
F0_MIN_HZ = 150.0    # F0 mediana por debajo => casi seguro NO es su voz -> se RECHAZA
F0_SOFT_HZ = 165.0   # 150-165 Hz = zona dudosa -> se acepta pero se AVISA para revisar

# Script de medición de F0 que corre en el intérprete que TENGA librosa (pyin: robusto, validado
# para masculino~120 vs femenino~185; fmin=75 evita el error de octava-abajo). Reutilizado tanto
# en proceso como por subproceso a .venv-voz.
_F0_SNIPPET = (
    "import sys,subprocess,warnings;warnings.filterwarnings('ignore');"
    "import numpy as np,librosa;"
    "o=subprocess.run(['ffmpeg','-v','error','-i',sys.argv[1],'-t','45','-ac','1',"
    "'-ar','16000','-f','f32le','-'],capture_output=True);"
    "y=np.frombuffer(o.stdout,dtype=np.float32);"
    "sys.exit(0) if (o.returncode!=0 or y.size<8000) else None;"
    "f0,_,_=librosa.pyin(y,fmin=75,fmax=400,sr=16000,frame_length=2048,hop_length=256);"
    "v=f0[~np.isnan(f0)];print(float(np.median(v)) if v.size>=10 else '')"
)

def _f0_mediana(path, max_seconds=45):
    """F0 mediana (Hz) de los frames sonoros, vía librosa.pyin. Si el intérprete actual no tiene
    librosa, delega en el venv .venv-voz del repo (que sí la trae) por subproceso. Devuelve None
    si no se puede medir; en ese caso el gate NO filtra a ciegas, avisa."""
    import subprocess
    try:
        import numpy as np, librosa  # camino en proceso (p.ej. .venv-voz)
        p = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-t", str(max_seconds),
             "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True)
        if p.returncode != 0 or not p.stdout:
            return None
        y = np.frombuffer(p.stdout, dtype=np.float32)
        if y.size < 8000:
            return None
        f0, _, _ = librosa.pyin(y, fmin=75, fmax=400, sr=16000,
                                frame_length=2048, hop_length=256)
        v = f0[~np.isnan(f0)]
        return float(np.median(v)) if v.size >= 10 else None
    except ImportError:
        pass  # sin librosa aquí -> probamos con .venv-voz
    except Exception:
        return None
    vv = os.path.join(_HERE, "..", ".venv-voz", "bin", "python")
    if not os.path.isfile(vv):
        return None  # no hay dónde medir -> gate degrada con aviso
    try:
        r = subprocess.run([vv, "-c", _F0_SNIPPET, path], capture_output=True, text=True, timeout=120)
        out = (r.stdout or "").strip()
        return float(out) if out else None
    except Exception:
        return None

def _aviso_numero_reasignado(dts):
    """Avisa FUERTE si las notas atribuidas a ella abarcan rangos muy separados en el tiempo."""
    ds = sorted(d for d in dts if d)
    if len(ds) < 2:
        return
    span = (ds[-1] - ds[0]).days
    gap = max((ds[i + 1] - ds[i]).days for i in range(len(ds) - 1))
    if span > 540 or gap > 365:
        b = "!" * 74
        print("\n" + b)
        print("⚠️  AVISO: este emisor abarca %d días (hueco máximo entre notas: %d días)." % (span, gap))
        print("    Un mismo NOMBRE repartido en rangos tan separados es señal típica de NÚMERO")
        print("    REASIGNADO o compartido: puede haber VOZ AJENA mezclada bajo tu etiqueta.")
        print("    Deja el gate de pitch ACTIVO y, ante la duda, escucha una muestra antes de fiarte.")
        print(b + "\n")

# --- Ofuscación del chat -----------------------------------------------------
# No guardamos el nombre del contacto en claro: solo iniciales + un número estable, para poder
# distinguir "de qué conversación salió" sin exponer con quién habla. Privacidad por defecto.
def chat_tag(name):
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    inis = "".join(p[0] for p in parts[:2]).upper() or "X"
    # número estable derivado del nombre (mismo contacto -> mismo tag entre ejecuciones)
    n = sum(ord(ch) for ch in (name or "")) % 100
    return "chat%s%02d" % (inis, n)

def fecha_es(dt):
    return dt.strftime("%d/%m/%y")

# --- Corpus (idempotente) ----------------------------------------------------
# Cada entrada lleva un marcador HTML con su clave (ruta/stanza del audio). Reejecutar no
# duplica: si la clave ya está en el fichero, esa nota se salta.
ENTRY_RE = re.compile(r"<!--\s*key:(?P<key>.+?)\s*-->")

def _leer_keys_existentes():
    if not os.path.isfile(CORPUS):
        return set()
    with open(CORPUS, encoding="utf-8") as f:
        return set(m.group("key") for m in ENTRY_RE.finditer(f.read()))

def _asegurar_cabecera():
    if os.path.isfile(CORPUS):
        return
    os.makedirs(os.path.dirname(CORPUS), exist_ok=True)
    with open(CORPUS, "w", encoding="utf-8") as f:
        f.write(
            "# Voz íntima de {{TITULAR}} (corpus de sus notas de voz)\n\n"
            "> PRIVADO · solo en este Mac · carpeta gitignored. Transcripciones de las notas de\n"
            "> voz que MANDA {{TITULAR}}, para que su gemelo de voz (`voz-titular`) suene a cómo habla\n"
            "> de verdad. Solo su voz; el audio crudo no se versiona. El nombre del contacto va\n"
            "> ofuscado (iniciales + número).\n\n"
            "---\n\n")

def _append_entradas(entradas):
    """entradas: lista de (key, fecha_str, tag, texto). Devuelve cuántas se escribieron nuevas."""
    existentes = _leer_keys_existentes()
    nuevas = [e for e in entradas if e[0] not in existentes and (e[3] or "").strip()]
    if not nuevas:
        return 0
    _asegurar_cabecera()
    with open(CORPUS, "a", encoding="utf-8") as f:
        for key, fecha, tag, texto in nuevas:
            f.write("**[%s] (%s):** %s <!-- key:%s -->\n\n" % (fecha, tag, texto.strip(), key))
    return len(nuevas)

# --- Modo --collect: notas de voz de ella en la BD local ---------------------
def collect():
    if not os.path.exists(wa_tracker.DB):
        print("No encuentro ChatStorage.sqlite; ¿está instalado WhatsApp de escritorio?")
        return
    tmp = os.path.join(tempfile.gettempdir(), "voz_intima_copy.sqlite")
    shutil.copy2(wa_tracker.DB, tmp)  # copia para no chocar con WhatsApp abierto
    c = sqlite3.connect(tmp)
    # Notas de voz de ELLA (ZISFROMME=1, tipo 3) ya descargadas en disco (ZMEDIALOCALPATH).
    rows = c.execute(
        "SELECT m.ZMESSAGEDATE, m.ZSTANZAID, s.ZPARTNERNAME, mi.ZMEDIALOCALPATH "
        "FROM ZWAMESSAGE m JOIN ZWAMEDIAITEM mi ON m.ZMEDIAITEM=mi.Z_PK "
        "LEFT JOIN ZWACHATSESSION s ON m.ZCHATSESSION=s.Z_PK "
        "WHERE m.ZMESSAGETYPE=? AND m.ZISFROMME=1 AND mi.ZMEDIALOCALPATH IS NOT NULL "
        "AND (mi.ZMEDIALOCALPATH LIKE '%.opus' OR mi.ZMEDIALOCALPATH LIKE '%.m4a') "
        "ORDER BY m.ZMESSAGEDATE", (wa_tracker.AUDIO_TYPE,)).fetchall()

    total_suyas = c.execute(
        "SELECT COUNT(*) FROM ZWAMESSAGE WHERE ZMESSAGETYPE=? AND ZISFROMME=1",
        (wa_tracker.AUDIO_TYPE,)).fetchone()[0]

    print("· %d notas de voz tuyas en la BD; %d con el audio ya descargado en este Mac." % (
        total_suyas, len(rows)))

    entradas = []
    en_disco = 0
    for d, stanza, partner, rel in rows:
        fp = os.path.join(wa_tracker.MEDIA_BASE, rel)
        if not os.path.isfile(fp):
            continue
        en_disco += 1
        txt = wa_tracker.transcribe(rel, MODEL)  # idempotente: cacheado por ruta relativa
        if not txt:
            continue
        fecha = fecha_es(datetime.datetime.fromtimestamp(wa_tracker.EPOCH + (d or 0)))
        key = "db:" + (stanza or rel)  # clave estable de idempotencia del corpus
        entradas.append((key, fecha, chat_tag(partner), txt))

    nuevas = _append_entradas(entradas)
    if en_disco == 0:
        print("· 0 audios en disco para transcribir. Es lo esperado: el WhatsApp nuevo no guarda")
        print("  legibles los audios de SALIDA. Siembra el historial con --ingest-export, o deja")
        print("  que lleguen notas nuevas que sí se descarguen.")
    print("· %d transcripciones nuevas añadidas al corpus." % nuevas)
    print("→ %s" % os.path.normpath(CORPUS))

# --- Modo --ingest-export: sembrar desde una exportación del móvil -----------
# _chat.txt típico de iOS:
#   [21/06/26, 14:03:21] {{TITULAR}}: ‎<adjunto: 00000042-AUDIO-2026-06-21.opus>
# Variantes que toleramos: con/sin corchetes, "adjunto"/"attached", marcas LRM/RLM invisibles,
# y la coma o el guion entre fecha y hora.
ATTACH_RE = re.compile(
    r"‎?<\s*(?:adjunto|attached|archivo adjunto|attachment)\s*:\s*(?P<file>[^>]+?)\s*>",
    re.IGNORECASE)
LINE_RE = re.compile(
    r"^\[?\s*(?P<fecha>\d{1,2}[/.]\d{1,2}[/.]\d{2,4})\s*[,]?\s*"
    r"(?P<hora>\d{1,2}:\d{2}(?::\d{2})?)?\s*(?:[AaPp]\.?[Mm]\.?)?\s*\]?\s*"
    r"(?P<resto>.*)$")
SENDER_RE = re.compile(r"^\s*(?P<sender>[^:]{1,80}?)\s*:\s*(?P<msg>.*)$")

def _clean(s):
    # Quita marcas direccionales invisibles que WhatsApp mete antes de los adjuntos.
    return "".join(ch for ch in (s or "") if ch not in "‎‏‪‫‬⁨⁩").strip()

def _contacto_de_dir(d):
    base = os.path.basename(os.path.normpath(d))
    # "WhatsApp Chat - Fulano" / "Chat de WhatsApp con Fulano"
    m = re.search(r"(?:Chat\s*-\s*|con\s+|with\s+)(.+)$", base, re.IGNORECASE)
    return _clean(m.group(1)) if m else _clean(base)

def _parse_chat(path):
    """Devuelve lista de (fecha_dt|None, sender, msg_o_adjunto) y el set de emisores vistos."""
    out, senders = [], []
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")
            lm = LINE_RE.match(line)
            if not lm:
                # continuación de mensaje multilínea: la colgamos del anterior si lo había
                if out and line.strip():
                    out[-1] = (out[-1][0], out[-1][1], out[-1][2] + " " + line.strip())
                continue
            resto = _clean(lm.group("resto"))
            sm = SENDER_RE.match(resto)
            if not sm:
                continue  # línea de sistema ("cifrado de extremo a extremo", etc.)
            sender = _clean(sm.group("sender"))
            msg = sm.group("msg")
            dt = None
            try:
                f_ = lm.group("fecha").replace(".", "/")
                h_ = lm.group("hora") or "00:00:00"
                if len(h_) == 5:
                    h_ += ":00"
                yy = f_.split("/")[2]
                fmt = "%d/%m/%Y %H:%M:%S" if len(yy) == 4 else "%d/%m/%y %H:%M:%S"
                dt = datetime.datetime.strptime(f_ + " " + h_, fmt)
            except Exception:
                dt = None
            out.append((dt, sender, msg))
            if sender not in senders:
                senders.append(sender)
    return out, senders

def ingest_export(d, me=None, pitch_gate=True):
    d = os.path.expanduser(d)
    if not os.path.isdir(d):
        print("No es una carpeta: %s" % d)
        print("Pásame la carpeta exportada de WhatsApp (con _chat.txt + los audios dentro).")
        return
    chats = glob.glob(os.path.join(d, "_chat.txt")) or glob.glob(os.path.join(d, "*.txt"))
    if not chats:
        print("No encuentro _chat.txt en %s" % d)
        return
    contacto = _contacto_de_dir(d)
    msgs, senders = _parse_chat(chats[0])

    # Atribución de quién eres TÚ en el chat:
    #  1) si pasas --me "<nombre>", uso ese emisor exacto (robusto en grupos / multi-emisor).
    #  2) si no, en un 1-a-1 (2 emisores) eres la que NO es el contacto.
    yo = None
    if me:
        match = [s for s in senders if wa_tracker.norm(s) == wa_tracker.norm(me)]
        if not match:
            print("No encuentro a '%s' entre los emisores del export: %s" % (me, senders))
            print("Pásame --me con tu nombre EXACTO tal y como aparece en el chat.")
            return
        yo = match[0]
    else:
        otros = [s for s in senders if wa_tracker.norm(s) != wa_tracker.norm(contacto)]
        if len(senders) == 2 and len(otros) == 1:
            yo = otros[0]
        elif len(senders) == 1 and wa_tracker.norm(senders[0]) != wa_tracker.norm(contacto):
            yo = senders[0]  # export raro con un solo emisor que no es el contacto

    if not yo:
        print("No puedo atribuir con fiabilidad de quién es cada nota en este export "
              "(emisores: %s; contacto deducido: '%s')." % (senders, contacto))
        print("Por seguridad NO meto nada: no quiero colar voz de otra persona en tu corpus.")
        print("Si es un chat 1-a-1, comprueba que la carpeta se llama por el contacto.")
        return

    print("· export de '%s'. Tus notas = las del emisor '%s'." % (contacto, yo))

    # 1ª pasada: recoge las notas de voz ATRIBUIDAS a ella (con su fecha y ruta).
    mias = []  # (dt, fname, fp)
    for dt, sender, msg in msgs:
        am = ATTACH_RE.search(_clean(msg))
        if not am:
            continue
        fname = _clean(am.group("file"))
        if not re.search(r"\.(opus|m4a|aac|mp3|ogg|wav)$", fname, re.IGNORECASE):
            continue  # solo audio (ignora fotos/vídeos/docs adjuntos)
        if wa_tracker.norm(sender) != wa_tracker.norm(yo):
            continue  # el emisor no es ella -> fuera (1er filtro, por nombre)
        mias.append((dt, fname, os.path.join(d, fname)))

    # Defensa 1 (gratis): aviso fuerte si el nombre abarca rangos de fechas muy separados.
    _aviso_numero_reasignado([dt for dt, _, _ in mias])

    entradas = []
    n_audios_mios = n_ok = n_pitch_out = 0
    gate_no_disp = False
    for dt, fname, fp in mias:
        n_audios_mios += 1
        if not os.path.isfile(fp):
            print("  · falta el fichero en disco, lo salto: %s" % fname)
            continue
        # Defensa 2 (gate de pitch): la atribución por nombre no basta; confirma que es voz femenina.
        if pitch_gate:
            f0 = _f0_mediana(fp)
            if f0 is None:
                if not gate_no_disp:
                    print("  · (gate de pitch no disponible —falta librosa/ffmpeg—: sigo SIN filtrar por voz)")
                    gate_no_disp = True
            elif f0 < F0_MIN_HZ:
                n_pitch_out += 1
                print("  · DESCARTADA: voz no femenina (F0≈%.0f Hz < %.0f) -> %s" % (f0, F0_MIN_HZ, fname))
                continue  # casi seguro NO es su voz -> jamás al corpus
            elif f0 < F0_SOFT_HZ:
                print("  · ⚠ dudosa (F0≈%.0f Hz, zona límite): la meto pero revísala -> %s" % (f0, fname))
        txt = wa_tracker.transcribe_abs(fp, MODEL, key="export:" + fname)
        if not txt:
            continue
        n_ok += 1
        fecha = fecha_es(dt) if dt else "fecha?"
        entradas.append(("export:" + fname, fecha, chat_tag(contacto), txt))

    nuevas = _append_entradas(entradas)
    print("· %d notas atribuidas a ti; %d descartadas por pitch; %d transcritas; %d nuevas al corpus." % (
        n_audios_mios, n_pitch_out, n_ok, nuevas))
    if not pitch_gate:
        print("  (gate de pitch DESACTIVADO por --no-pitch-gate; te fías solo del nombre del emisor)")
    print("→ %s" % os.path.normpath(CORPUS))

def main():
    args = sys.argv[1:]
    me = None
    pitch_gate = True
    if "--no-pitch-gate" in args:        # apaga el filtro por voz (te fías solo del nombre)
        pitch_gate = False; args.remove("--no-pitch-gate")
    if "--me" in args:
        j = args.index("--me")
        if j + 1 < len(args):
            me = args[j + 1]; del args[j:j + 2]
        else:
            print('Uso: --me "<tu nombre EXACTO tal y como sale en el chat>"'); return
    if "--ingest-export" in args:
        i = args.index("--ingest-export")
        if i + 1 >= len(args):
            print('Uso: python3 tools/voz_intima.py --ingest-export <carpeta> [--me "<nombre>"] [--no-pitch-gate]')
            return
        ingest_export(args[i + 1], me=me, pitch_gate=pitch_gate)
        return
    # --collect es el modo por defecto
    collect()

if __name__ == "__main__":
    main()
