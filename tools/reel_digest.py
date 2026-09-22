#!/usr/bin/env python3
"""tools/reel_digest.py — entiende un enlace COMPLETO, no solo el caption.

Para un reel/post de Instagram (o un vídeo de YouTube) saca las TRES capas:
  1. caption/texto de la página (lo pasa quien llama, o se rellena aparte con el Chrome logueado)
  2. VOZ hablada  → Whisper local (audio del vídeo)
  3. TEXTO EN PANTALLA → Apple Vision / ocrmac sobre frames muestreados
y las funde en un .md cacheado por shortcode. Para X/Twitter delega en grok.py; para web
abierta hace un fetch simple. Objetivo: que Claude entienda el enlace "venga de donde venga".

MURO (no negociable):
  · El vídeo de un tercero es CONTENIDO PÚBLICO (N0). Se puede procesar y, si hiciera falta,
    mandar a una IA multimodal. Lo que NUNCA sale ni se automatiza es la SESIÓN/cookie de IG.
  · La descarga de IG necesita la sesión de {{TITULAR}} (`--cookies-from-browser`). Eso SOLO en
    modo ATENDIDO (sesión interactiva). En `--unattended` (lazo 24/7) NO se toca la sesión:
    el enlace se marca 'pendiente' y se difiere a la próxima sesión. Veredicto del comité de
    verificación (11-jul): la cookie es el único dato sensible aquí; su fuga = suplantación +
    riesgo de baneo. El token, cuando se usa, va SOLO a instagram.com (igual que el navegador).

Reparto de máquinas: la descarga con sesión corre donde está el Chrome logueado (el portátil);
la transcodificación/Whisper/OCR corre en Polaris (tiene ffmpeg + whisper + ocrmac en su .venv).

Uso:
  python3 tools/reel_digest.py "https://www.instagram.com/reel/XXXX/" [--caption-file f.txt]
  python3 tools/reel_digest.py "<url>" --unattended     # no toca la sesión; difiere si IG
  python3 tools/reel_digest.py "<url>" --model small     # base|small|medium (def. small)
"""
import json
import os
import re
import subprocess
import sys

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
OUTDIR = os.path.join(REPO, "00_FUENTE-DE-VERDAD", "_PRIVADO_INSTAGRAM", "reels")
BUZON = os.path.join(REPO, "00_FUENTE-DE-VERDAD", "04 · IA", "Aportes-de-IAs", "Ideas-de-{{TITULAR}}.md")
POLARIS = os.environ.get("BTP_POLARIS_HOST", "polaris")
REMOTE_TMP = "~/reel_tmp"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
LOCAL_YTDLP = os.path.join(REPO, ".venv", "bin", "yt-dlp")
REMOTE_PY = "~/claudecode/.venv/bin/python"

_IG_RE = re.compile(r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)", re.I)
_YT_RE = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]+)", re.I)
_X_RE = re.compile(r"(?:twitter|x)\.com/", re.I)


def classify(url):
    if _IG_RE.search(url):
        return "ig"
    if _YT_RE.search(url):
        return "youtube"
    if _X_RE.search(url):
        return "x"
    return "web"


def shortcode(url):
    m = _IG_RE.search(url) or _YT_RE.search(url)
    return m.group(1) if m else re.sub(r"\W+", "_", url)[-40:]


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# --- Procesamiento pesado en Polaris (audio→Whisper, frames→Apple Vision) --------------
# Se envía este script al mini y se ejecuta allí; emite UNA línea JSON por stdout.
_REMOTE_PROC = r'''
import glob, json, os, sys
sc = sys.argv[1]; model = sys.argv[2]
os.chdir(os.path.expanduser("~/reel_tmp"))
os.environ["PATH"] = "/opt/homebrew/bin:" + os.environ.get("PATH", "")
out = {"transcript": "", "lang": "", "onscreen": "", "err": ""}
mp4 = sc + ".mp4"
try:
    os.system('/opt/homebrew/bin/ffmpeg -y -i %s -vn -ar 16000 -ac 1 audio.wav >/dev/null 2>&1' % mp4)
    os.system('rm -f frames_*.jpg; /opt/homebrew/bin/ffmpeg -y -i %s -vf "fps=1/2.5" frames_%%03d.jpg >/dev/null 2>&1' % mp4)
    import whisper
    r = whisper.load_model(model).transcribe("audio.wav")
    out["lang"] = r.get("language", "")
    out["transcript"] = (r.get("text") or "").strip()
except Exception as e:
    out["err"] += "whisper:%r " % e
try:
    from ocrmac import ocrmac
    seen = set(); lines = []
    for f in sorted(glob.glob("frames_*.jpg")):
        try:
            res = ocrmac.OCR(f, language_preference=["es-ES", "en-US"]).recognize()
        except Exception:
            res = []
        for item in res:
            t = (item[0] or "").strip(); k = t.lower()
            if len(t) >= 3 and k not in seen:
                seen.add(k); lines.append(t)
    out["onscreen"] = " | ".join(lines)[:4000]
except Exception as e:
    out["err"] += "ocr:%r " % e
print(json.dumps(out))
'''


def _process_on_polaris(sc, local_mp4, model):
    """Sube el mp4 a Polaris, corre Whisper + OCR allí, devuelve dict."""
    subprocess.run(["ssh", POLARIS, "mkdir -p %s" % REMOTE_TMP], check=False)
    scp = _run(["scp", "-q", local_mp4, "%s:%s/%s.mp4" % (POLARIS, REMOTE_TMP, sc)])
    if scp.returncode != 0:
        return {"err": "scp: " + scp.stderr}
    # deja el script de proceso en el mini
    put = subprocess.run(["ssh", POLARIS, "cat > %s/_proc.py" % REMOTE_TMP],
                         input=_REMOTE_PROC, text=True)
    if put.returncode != 0:
        return {"err": "no pude escribir _proc.py"}
    res = _run(["ssh", POLARIS,
                "%s %s/_proc.py %s %s" % (REMOTE_PY, REMOTE_TMP, sc, model)])
    line = [l for l in res.stdout.splitlines() if l.startswith("{")]
    if not line:
        return {"err": "sin JSON: " + (res.stdout + res.stderr)[-400:]}
    return json.loads(line[-1])


def _download_ig(url, sc, attended):
    if not attended:
        return None, "IG requiere sesión: diferido a sesión interactiva (modo --unattended)"
    dest = "/tmp/reel_%s.mp4" % sc
    r = _run([LOCAL_YTDLP, "--no-warnings", "--cookies-from-browser", "chrome",
              "-o", dest, url])
    return (dest, None) if os.path.exists(dest) else (None, "yt-dlp IG: " + r.stderr[-300:])


def _download_yt(url, sc):
    dest = "/tmp/reel_%s.mp4" % sc
    r = _run([LOCAL_YTDLP, "--no-warnings", "-f", "mp4", "-o", dest, url])
    return (dest, None) if os.path.exists(dest) else (None, "yt-dlp YT: " + r.stderr[-300:])


def video_digest(url, attended=True, model="small", caption=""):
    sc = shortcode(url)
    os.makedirs(OUTDIR, exist_ok=True)
    cache = os.path.join(OUTDIR, sc + ".md")
    if os.path.exists(cache):
        return cache, open(cache, encoding="utf-8").read()

    kind = classify(url)
    if kind == "ig":
        local_mp4, err = _download_ig(url, sc, attended)
    else:
        local_mp4, err = _download_yt(url, sc)
    if err:
        return None, err

    data = _process_on_polaris(sc, local_mp4, model)
    md = _assemble(url, sc, caption, data)
    with open(cache, "w", encoding="utf-8") as f:
        f.write(md)
    try:
        os.remove(local_mp4)
    except OSError:
        pass
    return cache, md


def _assemble(url, sc, caption, data):
    p = ["# Reel %s\n" % sc, "%s\n" % url]
    if data.get("err"):
        p.append("> ⚠️ %s\n" % data["err"])
    if caption:
        p.append("## Caption\n%s\n" % caption.strip())
    if data.get("transcript"):
        p.append("## Voz (Whisper, %s)\n%s\n" % (data.get("lang", "?"), data["transcript"]))
    if data.get("onscreen"):
        p.append("## Texto en pantalla (Apple Vision)\n%s\n" % data["onscreen"])
    return "\n".join(p)


_URL_LINE = re.compile(r"^- `[^`]+` — (\S+)")
_PEND = "⏳"


def _teaser(data, n=160):
    t = (data.get("transcript") or data.get("onscreen") or "").strip().replace("\n", " ")
    return (t[:n] + "…") if len(t) > n else t


def drain_buzon(buzon_path=BUZON, attended=True, max_n=5, model="small"):
    """Procesa los enlaces de vídeo PENDIENTES (⏳) del buzón: los digiere y marca ✅ con
    puntero al .md + teaser. Solo IG/YouTube. Deja ⏳ lo que no se pudo (p.ej. IG sin sesión).
    Devuelve (procesados, diferidos, total_pendientes_video)."""
    import datetime
    if not os.path.exists(buzon_path):
        return 0, 0, 0
    lines = open(buzon_path, encoding="utf-8").read().split("\n")
    # candidatos: URL de vídeo cuya línea siguiente (o subsiguiente no vacía) es ⏳
    cand = []
    for i, ln in enumerate(lines):
        m = _URL_LINE.match(ln)
        if not m:
            continue
        url = m.group(1)
        if classify(url) not in ("ig", "youtube"):
            continue
        for j in range(i + 1, min(i + 3, len(lines))):
            if _PEND in lines[j]:
                cand.append((url, j))
                break
            if lines[j].strip() and not lines[j].startswith("  "):
                break
    done = deferred = 0
    today = datetime.date.today().isoformat()
    for url, j in cand:
        if done >= max_n:
            break
        try:
            path, data = video_digest(url, attended=attended, model=model)
        except Exception as e:
            path, data = None, "excepción: %r" % e
        if path:
            sc = shortcode(url)
            rel = os.path.relpath(path, REPO)
            teaser = _teaser(data if isinstance(data, dict) else {})
            lines[j] = "  ✅ digerido auto %s (voz+texto en pantalla) → `%s`%s" % (
                today, rel, (" — " + teaser) if teaser else "")
            done += 1
        else:
            deferred += 1  # se queda ⏳ (p.ej. IG sin sesión atendida)
    if done:
        tmp = buzon_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        os.replace(tmp, buzon_path)
    return done, deferred, len(cand)


# Operaciones sobre el buzón CANÓNICO del mini (se ejecutan EN el mini vía ssh, stdlib sola).
# `mark` es APPEND-ONLY → race-safe con el append del bot de Telegram (buzon_ideas.capturar),
# sin tener que tocar el bot. `pending` dedup por la nota '✅ digerido auto:' → idempotente.
_BUZON_OPS = '''# -*- coding: utf-8 -*-
import sys, os, re
BUZON = os.path.expanduser("~/claudecode/00_FUENTE-DE-VERDAD/04 \\u00b7 IA/Aportes-de-IAs/Ideas-de-{{TITULAR}}.md")
URL_LINE = re.compile(r"^- `[^`]+` \\u2014 (\\S+)")
VIDEO = re.compile(r"instagram\\.com/(?:reel|reels|p|tv)/|youtube\\.com/watch|youtu\\.be/", re.I)
def content():
    try:
        return open(BUZON, encoding="utf-8").read()
    except FileNotFoundError:
        return ""
def pending():
    c = content(); lines = c.split("\\n"); out = []
    for i, ln in enumerate(lines):
        m = URL_LINE.match(ln)
        if not m: continue
        u = m.group(1)
        if not VIDEO.search(u): continue
        if ("digerido auto: " + u) in c: continue
        for j in range(i + 1, min(i + 3, len(lines))):
            if "\\u23f3" in lines[j]:
                out.append(u); break
            if lines[j].strip() and not lines[j].startswith("  "): break
    sys.stdout.write("\\n".join(out))
def mark(url, pointer):
    with open(BUZON, "a", encoding="utf-8") as f:
        f.write("\\n> \\u2705 digerido auto: %s \\u2192 `%s`\\n" % (url, pointer))
if __name__ == "__main__":
    (pending() if sys.argv[1] == "pending" else mark(sys.argv[2], sys.argv[3]))
'''


def drain_remote(host=POLARIS, attended=True, max_n=5, model="small"):
    """Drena el buzón CANÓNICO del mini: lee pendientes allí, descarga aquí (Chrome), procesa en
    el mini, escribe el digest .md en el mini y marca el buzón del mini (append-only, race-safe).
    En una máquina sin sesión de IG, la descarga IG falla → se difiere (⏳ intacto). Idempotente."""
    subprocess.run(["ssh", host, "mkdir -p %s" % REMOTE_TMP], check=False)
    subprocess.run(["ssh", host, "cat > %s/_buzon_ops.py" % REMOTE_TMP], input=_BUZON_OPS, text=True)
    r = _run(["ssh", host, "%s %s/_buzon_ops.py pending" % (REMOTE_PY, REMOTE_TMP)])
    pend = [u for u in r.stdout.splitlines() if u.strip()]
    done = deferred = 0
    for url in pend:
        if done >= max_n:
            break
        try:
            path, md = video_digest(url, attended=attended, model=model)
        except Exception:
            path = None
        if not path:
            deferred += 1
            continue
        sc = shortcode(url)
        rel = "00_FUENTE-DE-VERDAD/_PRIVADO_INSTAGRAM/reels/%s.md" % sc
        subprocess.run(["ssh", host,
                        "mkdir -p ~/claudecode/00_FUENTE-DE-VERDAD/_PRIVADO_INSTAGRAM/reels && "
                        "cat > ~/claudecode/%s" % rel],
                       input=md, text=True)
        subprocess.run(["ssh", host, "%s %s/_buzon_ops.py mark '%s' '%s'"
                        % (REMOTE_PY, REMOTE_TMP, url, rel)], check=False)
        done += 1
    return done, deferred, len(pend)


def main(argv):
    if "--drain" in argv:
        attended = "--unattended" not in argv
        model = argv[argv.index("--model") + 1] if "--model" in argv else "small"
        maxn = int(argv[argv.index("--max") + 1]) if "--max" in argv else 5
        if "--remote" in argv:
            host = argv[argv.index("--remote") + 1]
            done, deferred, total = drain_remote(host, attended=attended, max_n=maxn, model=model)
            print("drain(remote %s): %d digeridos, %d diferidos, %d pendientes" % (host, done, deferred, total))
            return 0
        bz = argv[argv.index("--buzon") + 1] if "--buzon" in argv else BUZON
        done, deferred, total = drain_buzon(bz, attended=attended, max_n=maxn, model=model)
        print("drain: %d digeridos, %d diferidos, %d pendientes de vídeo" % (done, deferred, total))
        return 0
    args = [a for a in argv if not a.startswith("--")]
    attended = "--unattended" not in argv
    model = "small"
    if "--model" in argv:
        model = argv[argv.index("--model") + 1]
    caption = ""
    if "--caption-file" in argv:
        cf = argv[argv.index("--caption-file") + 1]
        caption = open(cf, encoding="utf-8").read() if os.path.exists(cf) else ""
    if not args:
        print(__doc__)
        return 2
    url = args[0]
    kind = classify(url)
    if kind == "x":
        print("→ X/Twitter: usa tools/grok.py (lee X en vivo). Este tool es para vídeo.")
        return 0
    if kind == "web":
        print("→ Web abierta: usa WebFetch / el navegador. Este tool es para vídeo (IG/YouTube).")
        return 0
    path, out = video_digest(url, attended=attended, model=model, caption=caption)
    if not path:
        print("SIN DIGEST: " + str(out), file=sys.stderr)
        return 1
    print(out)
    print("\n[cacheado en %s]" % path, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
