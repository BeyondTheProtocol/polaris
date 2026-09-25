#!/usr/bin/env python3
"""Lee YouTube (Data API v3) en SOLO LECTURA: menciones, comentarios y stats de canal. Sin dependencias (stdlib).
La clave la pones TÚ en el Llavero (btp-youtube-api) o en tools/.youtube_secrets.json — yo nunca la veo.

⚠️  SOLO LECTURA / monitorización. NUNCA publica, responde ni borra. Material para borradores; publica {{TITULAR}}.

Setup (1 vez):
  1. console.cloud.google.com → crea/elige proyecto → "APIs y servicios" → habilita **YouTube Data API v3**.
  2. Credenciales → "Crear credenciales" → **Clave de API**. Cópiala (AIza...). (Restríngela a YouTube Data API).
  3. Guárdala en el Llavero:
       security add-generic-password -a "$USER" -s btp-youtube-api -w 'AIza...'
     o crea tools/.youtube_secrets.json con:  {"api_key": "AIza..."}

Uso:
  python3 youtube.py --search "{{TITULAR}} {{APELLIDO}} vacuna"   # vídeos recientes que mencionan algo (menciones)
  python3 youtube.py --channel @titular               # stats + últimos vídeos de un canal (handle o ID)
  python3 youtube.py --comments <videoId>                # comentarios recientes de un vídeo
  python3 youtube.py --json ...                          # JSON crudo de la API

Notas: YouTube no tiene "DMs"; el buzón = comentarios + menciones. La búsqueda es pública (basta API key).
"""
import json, os, sys, urllib.request, urllib.parse, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secrets import get as get_secret
from _net import with_retries

SECRETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".youtube_secrets.json")
BASE = "https://www.googleapis.com/youtube/v3"


def load_key():
    key = (get_secret("btp-youtube-api", SECRETS, "api_key") or "").strip()
    if not key:
        print("Falta la clave de YouTube. Guárdala en el Llavero (btp-youtube-api) o en "
              "tools/.youtube_secrets.json. Sácala en console.cloud.google.com (YouTube Data API v3 → Clave de API).")
        return None
    return key


def api(path, params, key):
    params = {**params, "key": key}
    url = f"{BASE}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        return json.load(with_retries(lambda: urllib.request.urlopen(req, timeout=60))), None
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:600]
        hint = "  → revisa que la API esté habilitada, la clave válida y la cuota." if e.code in (400, 403) else ""
        return None, f"YouTube API error {e.code}: {detail}{hint}"
    except Exception as e:
        return None, f"Error de red: {e}"


def resolve_channel(token, key):
    """Acepta un channelId (UC...), un @handle o un nombre; devuelve channelId o None."""
    if token.startswith("UC") and len(token) > 20:
        return token
    p = {"part": "id", "forHandle": token if token.startswith("@") else "@" + token}
    data, err = api("channels", p, key)
    if not err and data.get("items"):
        return data["items"][0]["id"]
    # fallback: búsqueda por nombre
    data, err = api("search", {"part": "snippet", "q": token, "type": "channel", "maxResults": 1}, key)
    if not err and data.get("items"):
        return data["items"][0]["snippet"]["channelId"]
    return None


def main():
    args = sys.argv[1:]
    if not args or "--help" in args or "-h" in args:
        print(__doc__); return
    want_json = "--json" in args
    if want_json:
        args.remove("--json")
    key = load_key()
    if not key:
        return

    def opt(name):
        if name in args:
            i = args.index(name)
            return args[i + 1] if i + 1 < len(args) else ""
        return None

    if (q := opt("--search")) is not None:
        data, err = api("search", {"part": "snippet", "q": q, "type": "video",
                                   "order": "date", "maxResults": 15}, key)
        if err:
            print(err); return
        if want_json:
            print(json.dumps(data, ensure_ascii=False, indent=2)); return
        items = data.get("items", [])
        print(f"🔎 {len(items)} vídeos recientes para «{q}»:")
        for it in items:
            s = it.get("snippet", {})
            vid = it.get("id", {}).get("videoId", "")
            print(f"  [{s.get('publishedAt','')[:10]}] {s.get('channelTitle','')}: {s.get('title','')}")
            print(f"     https://youtu.be/{vid}")
        return

    if (ch := opt("--channel")) is not None:
        cid = resolve_channel(ch, key)
        if not cid:
            print(f"No pude resolver el canal «{ch}»."); return
        data, err = api("channels", {"part": "snippet,statistics", "id": cid}, key)
        if err:
            print(err); return
        if want_json:
            print(json.dumps(data, ensure_ascii=False, indent=2)); return
        it = (data.get("items") or [{}])[0]
        sn, st = it.get("snippet", {}), it.get("statistics", {})
        print(f"📺 {sn.get('title','')}  ({cid})")
        print(f"   subs: {st.get('subscriberCount','?')} · vídeos: {st.get('videoCount','?')} · "
              f"visitas: {st.get('viewCount','?')}")
        latest, err = api("search", {"part": "snippet", "channelId": cid, "order": "date",
                                     "type": "video", "maxResults": 5}, key)
        if not err:
            print("   últimos vídeos:")
            for v in latest.get("items", []):
                s = v.get("snippet", {})
                print(f"     [{s.get('publishedAt','')[:10]}] {s.get('title','')}  "
                      f"https://youtu.be/{v.get('id',{}).get('videoId','')}")
        return

    if (vid := opt("--comments")) is not None:
        data, err = api("commentThreads", {"part": "snippet", "videoId": vid,
                                           "order": "time", "maxResults": 20}, key)
        if err:
            print(err); return
        if want_json:
            print(json.dumps(data, ensure_ascii=False, indent=2)); return
        items = data.get("items", [])
        print(f"💬 {len(items)} comentarios recientes en {vid}:")
        for it in items:
            c = it["snippet"]["topLevelComment"]["snippet"]
            print(f"  {c.get('authorDisplayName','')}: {c.get('textDisplay','')[:200]}")
        return

    print("Indica una acción: --search / --channel / --comments  (--help para todo).")


if __name__ == "__main__":
    main()
