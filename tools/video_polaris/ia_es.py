"""¿Dice «IA» en español (i-a) o en inglés (ei-ai)? {{TITULAR}}, 6-oct-2026: «en la voz dice IA en inglés, debe decirlo en español».
Whisper escribe «IA» en los dos casos, así que no lo delata. Aquí se mide el final de la palabra: en español acaba en /a/
(energía entre 700 y 1500 Hz) y en inglés acaba en /i/ (energía por encima de 1900 Hz). Se calibra en la misma toma con
palabras que acaban en «a» (prueba, biopsia, primera…) y en «í» (así, pedí, conseguí).
Uso: python ia_es.py <build> [letra ...]      → una línea por toma y capítulo, con el veredicto."""
import json, os, re, subprocess, sys, unicodedata
import numpy as np
import whisper

B = sys.argv[1]
rev = {}
for n in ("revision_tomas.json", "revision_toma_elegida.json"):  # la elegida (una mezcla) solo está en el segundo
    if os.path.exists(os.path.join(B, n)):
        rev.update({f["toma"]: f for f in json.load(open(os.path.join(B, n), encoding="utf-8")) if f.get("ok")})
letras = sys.argv[2:] or sorted(rev)
w = whisper.load_model("small")
norm = lambda s: re.sub(r"[^a-z]", "", unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode())
EN_A = {"prueba", "biopsia", "primera", "investiga", "neuroendocrina", "parte"[:0] or "una"}
EN_I = {"asi", "pedi", "consegui"}


def final(x, a, b):
    """log de la energía «de /a/» sobre la «de /i/» en el último tercio de la palabra."""
    d = b - a
    s = x[int((a + 0.62 * d) * 16000):int((b - 0.01) * 16000)]
    if len(s) < 400:
        return None
    P = np.abs(np.fft.rfft(s * np.hanning(len(s)), 4096)) ** 2
    f = np.fft.rfftfreq(4096, 1 / 16000)
    return float(np.log10(P[(f > 700) & (f < 1500)].sum() / (P[(f > 1900) & (f < 3000)].sum() + 1e-12)))


for l in letras:
    mp3 = os.path.join(B, "voz", f"entera-{l}.mp3")
    for cap in rev[l]["caps"]:
        if cap["id"] not in ("investigar", "antes"):
            continue
        a0 = max(0, cap["a"] - 0.25)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(a0), "-t", str(cap["b"] - a0 + 0.4), "-i", mp3, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                             capture_output=True, check=True).stdout
        x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
        r = w.transcribe(x, language="es", word_timestamps=True, fp16=False)
        ws = [p for s in r["segments"] for p in s.get("words", [])]
        va = [final(x, p["start"], p["end"]) for p in ws if norm(p["word"]) in EN_A]
        vi = [final(x, p["start"], p["end"]) for p in ws if norm(p["word"]) in EN_I]
        va, vi = [v for v in va if v is not None], [v for v in vi if v is not None]
        ia = [p for p in ws if norm(p["word"]) == "ia"]
        if not ia:
            print(f"toma {l} · {cap['id']:10s} · no encuentro «IA» (oído: {r['text'].strip()[:70]})"); continue
        p = ia[0]
        v = final(x, p["start"], p["end"])
        ma, mi = (np.mean(va) if va else np.nan), (np.mean(vi) if vi else np.nan)
        corte = (ma + mi) / 2 if va and vi else (ma - 0.6 if va else np.nan)
        dice = "?" if v is None or np.isnan(corte) else ("español (acaba en a)" if v > corte else "INGLÉS (acaba en i)")
        print(f"toma {l} · {cap['id']:10s} · «IA» {p['end'] - p['start']:.2f} s · final={v if v is None else round(v, 2)} · /a/={ma:.2f} (n={len(va)}) /i/={mi:.2f} (n={len(vi)}) → {dice}")
