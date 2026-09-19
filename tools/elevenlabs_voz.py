#!/usr/bin/env python3
"""Voz hablada de {{TITULAR}} vía ElevenLabs (el "pase de voz" HABLADO, en español).

Por qué: el clon local (chatterbox) leía el español con acento inglés ("guiri").
ElevenLabs clona y habla español nativo. {{TITULAR}} decidió usarlo (21/6/26).

SEGURIDAD / MURO:
- La CLAVE va al Llavero (servicio `btp-elevenlabs-api`), nunca en git ni por el chat:
      security add-generic-password -U -a "$USER" -s btp-elevenlabs-api -w 'sk_...'
- La voz es BIOMETRÍA: al usar ElevenLabs su audio va a SU nube (decisión informada de {{TITULAR}}).
  Nada de subir audio a otros sitios. En su panel se puede borrar la voz/datos.
- Solo GENERA borradores de audio (a su buzón); no publica ni envía nada.

Uso:
  python3 elevenlabs_voz.py --list-voices                 # ver voces y coger el voice_id de {{TITULAR}}
  python3 elevenlabs_voz.py "Hola, soy {{TITULAR}}..."         # usa voice_id de config/--voice
  python3 elevenlabs_voz.py "texto" --voice <VOICE_ID> --out ~/Downloads/para-claude/prueba.mp3
Config no-secreta opcional (voice_id/model): tools/.elevenlabs.json {"voice_id":"...","model":"..."}
"""
import json
import os
import sys
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secrets import get as get_secret  # noqa: E402

API = "https://api.elevenlabs.io/v1"
CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".elevenlabs.json")
# multilingual_v2 = español nativo y estable. turbo_v2_5 admite language_code y es más barato/rápido.
DEFAULT_MODEL = "eleven_multilingual_v2"
BUZON = os.path.expanduser("~/Downloads/para-claude")


def _cfg():
    try:
        with open(CFG) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _key():
    k = get_secret("btp-elevenlabs-api")
    if not k:
        sys.exit("Falta la clave de ElevenLabs. Guárdala en el Llavero:\n"
                 "  security add-generic-password -U -a \"$USER\" -s btp-elevenlabs-api -w 'sk_...'")
    return k


def _req(url, key, data=None, accept="application/json"):
    headers = {"xi-api-key": key, "Accept": accept}
    if data is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(data).encode("utf-8")
    r = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        sys.exit("ElevenLabs HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:500]))


def list_voices():
    key = _key()
    body, _ = _req(API + "/voices", key)
    voices = json.loads(body).get("voices", [])
    if not voices:
        print("(sin voces aún — clona tu voz en elevenlabs.io y vuelve a listar)")
        return
    print("VOICE_ID                         NOMBRE        CATEGORÍA")
    for v in voices:
        print("%-32s %-13s %s" % (v.get("voice_id", "?"), (v.get("name") or "")[:13], v.get("category", "")))


def speak(text, voice_id, model, out, stability=0.5, similarity=0.85, style=0.0):
    key = _key()
    if not voice_id:
        sys.exit("Falta voice_id. Pásalo con --voice <ID> o ponlo en tools/.elevenlabs.json. "
                 "Lístalas con: python3 elevenlabs_voz.py --list-voices")
    body = {
        "text": text,
        "model_id": model,
        "voice_settings": {"stability": stability, "similarity_boost": similarity,
                           "style": style, "use_speaker_boost": True},
    }
    if model == "eleven_turbo_v2_5":
        body["language_code"] = "es"  # multilingual_v2 autodetecta; turbo admite forzar idioma
    audio, ctype = _req(API + "/text-to-speech/%s?output_format=mp3_44100_128" % voice_id, key,
                        data=body, accept="audio/mpeg")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as f:
        f.write(audio)
    print("OK · %d KB · %s" % (len(audio) // 1024, out))


def clone(path, name):
    """Crea un clon instantáneo (IVC) de la voz a partir de un audio y guarda el voice_id."""
    key = _key()
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        sys.exit("No existe el audio: %s" % path)
    with open(path, "rb") as f:
        audio = f.read()
    boundary = "----btpEleven7c1f9a"
    fn = os.path.basename(path)
    body = b"".join([
        ("--%s\r\nContent-Disposition: form-data; name=\"name\"\r\n\r\n%s\r\n" % (boundary, name)).encode("utf-8"),
        ("--%s\r\nContent-Disposition: form-data; name=\"files\"; filename=\"%s\"\r\n"
         "Content-Type: application/octet-stream\r\n\r\n" % (boundary, fn)).encode("utf-8"),
        audio,
        ("\r\n--%s--\r\n" % boundary).encode("utf-8"),
    ])
    req = urllib.request.Request(
        API + "/voices/add", data=body, method="POST",
        headers={"xi-api-key": key, "Accept": "application/json",
                 "Content-Type": "multipart/form-data; boundary=%s" % boundary})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            res = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit("ElevenLabs clone HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:600]))
    vid = res.get("voice_id")
    cfg = _cfg(); cfg["voice_id"] = vid; cfg.setdefault("model", DEFAULT_MODEL)
    with open(CFG, "w") as f:
        json.dump(cfg, f, indent=2)
    print("OK · clon creado · voice_id=%s · guardado en %s" % (vid, CFG))
    return vid


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args[0] == "--list-voices":
        list_voices()
        return
    if args[0] == "--clone":
        if len(args) < 2:
            sys.exit("Uso: --clone <audio> [--name \"{{TITULAR}}\"]")
        name = args[args.index("--name") + 1] if "--name" in args else "{{TITULAR}}"
        clone(args[1], name)
        return
    cfg = _cfg()
    text = args[0]
    voice_id = cfg.get("voice_id")
    model = cfg.get("model", DEFAULT_MODEL)
    out = os.path.join(BUZON, "voz-titular-prueba.mp3")
    stab, sim, sty = 0.5, 0.85, 0.0
    i = 1
    while i < len(args):
        if args[i] == "--voice" and i + 1 < len(args):
            voice_id = args[i + 1]; i += 2
        elif args[i] == "--model" and i + 1 < len(args):
            model = args[i + 1]; i += 2
        elif args[i] == "--out" and i + 1 < len(args):
            out = os.path.expanduser(args[i + 1]); i += 2
        elif args[i] == "--stability" and i + 1 < len(args):
            stab = float(args[i + 1]); i += 2
        elif args[i] == "--similarity" and i + 1 < len(args):
            sim = float(args[i + 1]); i += 2
        elif args[i] == "--style" and i + 1 < len(args):
            sty = float(args[i + 1]); i += 2
        else:
            i += 1
    speak(text, voice_id, model, out, stability=stab, similarity=sim, style=sty)


if __name__ == "__main__":
    main()
