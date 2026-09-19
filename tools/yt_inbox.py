#!/usr/bin/env python3
"""Captura DETERMINISTA (sin LLM, sin tokens) del "buzón" de YouTube de {{TITULAR}}.

YouTube no tiene DMs: el buzón = comentarios en sus propios vídeos + menciones
públicas (vídeos de terceros que la nombran). Reutiliza tools/youtube.py (Data
API v3, solo lectura) para:
  1) resolver su canal (handle configurable, ver CHANNEL abajo),
  2) sacar comentarios NUEVOS de sus últimos vídeos,
  3) sacar menciones nuevas (búsqueda pública "{{TITULAR}} {{APELLIDO}} ..."),
  4) volcar todo a 00_FUENTE-DE-VERDAD/_PRIVADO_YT/ (gitignored) con dedup por id.

Handle verificado en vivo (3/7/26) vía `youtube.py --channel @titular`:
"{{TITULAR}} {{APELLIDO}}" (UCGWCTqqrSaS09vjSXbSFESw, ~16.9k subs) — vídeos de cáncer de
{{DIAGNOSTICO}} + episodio de Carlos Roca confirman que es su canal real.
Configurable con la variable BTP_YT_CHANNEL por si cambiara.

SOLO LECTURA / monitorización. NUNCA publica, responde ni borra. Sin LLM: esto
solo captura y archiva; el triaje (qué importa) lo hace Vega (agente `asistente`)
minando el volcado, igual que ya hace con WhatsApp y las menciones de X.

Uso:
  python3 tools/yt_inbox.py                 # pasada normal: comentarios + menciones
  python3 tools/yt_inbox.py --channel @otro  # canal distinto (debug)
  python3 tools/yt_inbox.py --videos 5       # nº de últimos vídeos a mirar (def. 8)
  python3 tools/yt_inbox.py --print          # imprime, no escribe fichero ni estado

Fail-soft: si falta la clave (Llavero `btp-youtube-api`) o la API falla, avisa
y termina limpio (exit 0) — no rompe el daemon ni genera falsa alarma.
"""
import json
import os
import sys
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import youtube as yt  # noqa: E402 — reusa api()/resolve_channel()/load_key(), no duplica llamadas

CHANNEL = os.environ.get("BTP_YT_CHANNEL", "@titular")  # handle real de {{TITULAR}}, verificado 3/7/26
SEARCH_QUERIES = ["{{TITULAR}} {{APELLIDO}} Beyond the Protocol", "{{TITULAR}} {{APELLIDO}} {{DIAGNOSTICO}} vacuna"]
N_VIDEOS = 8            # últimos vídeos del canal a revisar por comentarios nuevos
N_COMMENTS = 20          # comentarios recientes por vídeo (tope de la API, ver youtube.py)

OUTDIR = os.path.join(HERE, "..", "00_FUENTE-DE-VERDAD", "_PRIVADO_YT")
STATE = os.path.join(OUTDIR, ".seen.json")  # ids ya capturados (comentarios + menciones), gitignored


def _load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"comments": [], "mentions": []}


def _save_state(state):
    os.makedirs(OUTDIR, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def latest_videos(channel_id, key, n):
    data, err = yt.api("search", {"part": "snippet", "channelId": channel_id, "order": "date",
                                   "type": "video", "maxResults": n}, key)
    if err:
        return [], err
    out = []
    for it in data.get("items", []):
        vid = it.get("id", {}).get("videoId")
        sn = it.get("snippet", {})
        if vid:
            out.append((vid, sn.get("title", ""), sn.get("publishedAt", "")))
    return out, None


def new_comments(video_id, seen_ids, key):
    data, err = yt.api("commentThreads", {"part": "snippet", "videoId": video_id,
                                           "order": "time", "maxResults": N_COMMENTS}, key)
    if err:
        # Vídeos con comentarios desactivados devuelven 403 — no es un fallo real, sáltalo.
        return [], None if "403" in err else err
    out = []
    for it in data.get("items", []):
        cid = it.get("id", "")
        if not cid or cid in seen_ids:
            continue
        c = it.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
        out.append({
            "id": cid, "video_id": video_id,
            "author": c.get("authorDisplayName", ""),
            "text": c.get("textDisplay", ""),
            "published": c.get("publishedAt", ""),
        })
    return out, None


def new_mentions(query, seen_ids, key):
    data, err = yt.api("search", {"part": "snippet", "q": query, "type": "video",
                                   "order": "date", "maxResults": 15}, key)
    if err:
        return [], err
    out = []
    for it in data.get("items", []):
        vid = it.get("id", {}).get("videoId", "")
        if not vid or vid in seen_ids:
            continue
        sn = it.get("snippet", {})
        out.append({
            "id": vid,
            "channel": sn.get("channelTitle", ""),
            "title": sn.get("title", ""),
            "published": sn.get("publishedAt", ""),
        })
    return out, None


def render(comments, mentions, now):
    lines = [f"# YouTube — buzón de {{TITULAR}} ({CHANNEL}) — {now:%Y-%m-%d %H:%M}\n",
              "> Captura DETERMINISTA, sin LLM. Solo lectura. Triaje lo hace Vega sobre este fichero.\n"]
    lines.append(f"\n## 💬 Comentarios nuevos ({len(comments)})\n")
    if not comments:
        lines.append("Ninguno nuevo desde la última pasada.\n")
    for c in comments:
        lines.append(f"- **{c['author']}** ({c['published'][:10]}) en https://youtu.be/{c['video_id']}\n"
                      f"  > {c['text'][:300]}\n")
    lines.append(f"\n## 📣 Menciones nuevas ({len(mentions)})\n")
    if not mentions:
        lines.append("Ninguna nueva desde la última pasada.\n")
    for m in mentions:
        lines.append(f"- [{m['published'][:10]}] **{m['channel']}**: {m['title']}\n"
                      f"  https://youtu.be/{m['id']}\n")
    return "".join(lines)


def main():
    args = sys.argv[1:]
    channel = CHANNEL
    n_videos = N_VIDEOS
    do_print = "--print" in args
    if do_print:
        args.remove("--print")
    if "--channel" in args:
        i = args.index("--channel")
        channel = args[i + 1]; args = args[:i] + args[i + 2:]
    if "--videos" in args:
        i = args.index("--videos")
        n_videos = int(args[i + 1]); args = args[:i] + args[i + 2:]

    key = yt.load_key()
    if not key:
        return 0  # fail-soft: ya avisó youtube.load_key(); no romper el daemon

    channel_id = yt.resolve_channel(channel, key)
    if not channel_id:
        print(f"No pude resolver el canal «{channel}» — dejo configurable BTP_YT_CHANNEL.")
        return 0

    state = _load_state()
    seen_comments = set(state.get("comments", []))
    seen_mentions = set(state.get("mentions", []))

    videos, err = latest_videos(channel_id, key, n_videos)
    if err:
        print(err); return 0

    all_comments = []
    for vid, _title, _pub in videos:
        cs, err = new_comments(vid, seen_comments, key)
        if err:
            print(f"  ! comentarios de {vid}: {err}")
            continue
        all_comments.extend(cs)

    all_mentions = []
    for q in SEARCH_QUERIES:
        ms, err = new_mentions(q, seen_mentions, key)
        if err:
            print(f"  ! menciones «{q}»: {err}")
            continue
        for m in ms:
            if m["id"] not in {x["id"] for x in all_mentions}:  # dedup entre queries de esta misma pasada
                all_mentions.append(m)

    now = datetime.datetime.now()
    text = render(all_comments, all_mentions, now)

    if do_print:
        print(text)
        return 0

    if not all_comments and not all_mentions:
        print("· 0 comentarios y 0 menciones nuevas — nada que volcar.")
        return 0

    os.makedirs(OUTDIR, exist_ok=True)
    path = os.path.join(OUTDIR, f"yt-{now:%Y-%m-%d}.md")
    # Si ya hubo una pasada hoy, añade (no pisa) para no perder lo capturado antes.
    mode = "a" if os.path.exists(path) else "w"
    with open(path, mode, encoding="utf-8") as f:
        if mode == "a":
            f.write("\n---\n\n")
        f.write(text)

    seen_comments.update(c["id"] for c in all_comments)
    seen_mentions.update(m["id"] for m in all_mentions)
    _save_state({"comments": sorted(seen_comments), "mentions": sorted(seen_mentions)})

    print(f"✅ {len(all_comments)} comentarios + {len(all_mentions)} menciones nuevas → "
          f"{os.path.relpath(path, os.path.join(HERE, '..'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
