#!/usr/bin/env python3
"""Transcribe notas de voz .opus de WhatsApp (sin ffmpeg, vía soundfile + whisper).
Uso: python3 transcribe_audios.py [modelo]   (modelo: base|small, def. base)
Idempotente: salta audios ya transcritos. Salida: una .md por chat en Audios-Transcritos/."""
import glob, os, re, sys
import numpy as np, soundfile as sf, whisper

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # raíz del repo (este fichero vive en tools/)
SRC = os.path.expanduser("~/Downloads/.wa_audio")
OUT = os.path.join(ROOT, "00_FUENTE-DE-VERDAD", "Audios-Transcritos")
MODEL = sys.argv[1] if len(sys.argv) > 1 else "base"
os.makedirs(OUT, exist_ok=True)
print("Cargando whisper", MODEL, "…"); m = whisper.load_model(MODEL)

def date_of(fn):
    mo = re.search(r'(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})', fn)
    return f"{mo.group(1)}-{mo.group(2)}-{mo.group(3)} {mo.group(4)}:{mo.group(5)}" if mo else fn

total = 0
for chatdir in sorted(glob.glob(SRC + "/*/")):
    chat = os.path.basename(chatdir.rstrip("/"))
    files = sorted(glob.glob(chatdir + "*.opus"))
    if not files: continue
    outpath = os.path.join(OUT, re.sub(r'[^\w. -]', '_', chat) + ".md")
    seen = open(outpath, encoding="utf-8").read() if os.path.exists(outpath) else ""
    if not seen:
        with open(outpath, "w", encoding="utf-8") as f: f.write(f"# Audios transcritos — {chat}\n\n> whisper `{MODEL}` · transcripción automática (puede tener errores).\n\n")
    for fp in sorted(files):
        fn = os.path.basename(fp)
        if fn in seen: continue
        try:
            data, sr = sf.read(fp, dtype="float32")
            if data.ndim > 1: data = data.mean(axis=1)
            if sr != 16000:
                n = int(len(data) * 16000 / sr)
                data = np.interp(np.linspace(0, len(data), n, endpoint=False), np.arange(len(data)), data).astype("float32")
            txt = m.transcribe(data, language="es", fp16=False)["text"].strip()
        except Exception as e:
            txt = f"[ERROR: {e}]"
        with open(outpath, "a", encoding="utf-8") as f:
            f.write(f"**[{date_of(fn)}]** `{fn}`\n{txt}\n\n")
        total += 1; print(f"  {chat}/{fn[:28]} → {len(txt)} chars")
print(f"DONE · {total} audios nuevos transcritos en {OUT}")
