"""Vídeo NARRADO con su voz, de un guion por capítulos a los ficheros que pinta Remotion (composición «Narrado»).

Uso:  ~/claudecode/.venv/bin/python narrado.py <guion.json> <build>
      después:  bash remotion/render.sh <version> Narrado <slug>

Encadena lo que ya existe, sin duplicarlo:
  voz.py       su clon, frases ENLAZADAS, 3 tomas y la más viva, cada frase igualada a -18 LUFS
  palabras.py  el tiempo de cada palabra con Whisper en local (egress 0)
  sfx.py       los sonidos sintetizados (barrido por capítulo, clic por fila, golpe en NED)
y escribe en remotion/public/narrado/: timeline.json, voz.wav, musica.wav, sfx.wav.

El guion (guiones/<slug>.<idioma>.json) va por CAPÍTULOS: kicker, titulo (la palabra de acento entre *asteriscos*),
escena {tipo, props} y frases [{texto, pantalla?}]. Lo que se ve lo decide la escena; los subtítulos quitan solos
lo que ya está escrito en pantalla (Narrado.tsx). Solo se manda TEXTO a ElevenLabs.
"""
import json, os, shutil, struct, subprocess, sys, wave

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import voz, sfx  # noqa: E402

PUBLICO = os.path.join(AQUI, "remotion", "public", "narrado")
MARCA = os.path.expanduser("~/claudecode/00_FUENTE-DE-VERDAD/07 · Marca/Videos-Polaris")


def aplanar(g):
    """Capítulos → la lista de frases que entienden voz.py y palabras.py (misma cabecera del guion)."""
    plano = {k: v for k, v in g.items() if k != "capitulos"}
    plano["frases"] = []
    for c in g["capitulos"]:
        for i, f in enumerate(c["frases"]):
            x = {"id": f"{c['id']}-{i}", "escena": c["id"], "texto": f["texto"]}
            if f.get("pantalla"):
                x["pantalla"] = f["pantalla"]
            if i == len(c["frases"]) - 1:
                x["pausa_despues"] = g.get("pausa_entre_capitulos", 0.9)
            plano["frases"].append(x)
    return plano


def tiempos(build):
    js = open(os.path.join(build, "assets", "tiempos.js"), encoding="utf-8").read()
    return json.loads(js[js.index("{"):js.rindex("}") + 1])


def cama_musical(src, total, out):
    """Alarga el tema encadenándolo consigo mismo con fundidos largos (4 s), y lo funde al final."""
    d = voz.dur(src); n = max(1, int(total // (d - 4)) + 1)
    ins = sum((["-i", src] for _ in range(n)), [])
    filtro, prev = "", "[0:a]"
    for k in range(1, n):
        filtro += f"{prev}[{k}:a]acrossfade=d=4:c1=tri:c2=tri[x{k}];"; prev = f"[x{k}]"
    filtro += f"{prev}atrim=0:{total},afade=t=in:d=1.5,afade=t=out:st={max(0, total - 3)}:d=3,aresample=48000[o]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", filtro, "-map", "[o]", "-ac", "2", out], check=True)


def palabra(T, cap, pref):
    c = next(x for x in T["capitulos"] if x["id"] == cap)
    for fr in c["frases"]:
        for w in fr["palabras"]:
            if w["w"].lower().strip("¿?¡!.,:;").startswith(pref.lower()):
                return w["a"]
    return c["t0"]


def efectos(T, g, out):
    n = int(T["total"] * sfx.SR); buf = [0.0] * n
    for c in T["capitulos"][1:]:
        sfx.poner(buf, c["t0"] - 0.45, sfx.whoosh(0.5), 0.09)
    for cap in g["capitulos"]:
        e = cap["escena"]
        for it in e.get("items", []) + e.get("nodos", []):
            if it.get("en"):
                sfx.poner(buf, palabra(T, cap["id"], it["en"]), sfx.clic(1500), 0.12)
    for c in T["capitulos"]:
        for fr in c["frases"]:
            for w in fr["palabras"]:
                if w["w"].strip("¿?.,:").upper() == "NED":
                    sfx.poner(buf, w["a"], sfx.golpe(0.8, 80), 0.3)
    pico = max(abs(v) for v in buf) or 1; gan = 0.7 / pico
    with wave.open(out, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sfx.SR)
        w.writeframes(b"".join(struct.pack("<hh", int(32767 * gan * v), int(32767 * gan * v)) for v in buf))


def voz_unica(g, plano, build):
    """TODA la narración en UNA toma (pausas entre capítulos con <break>): entonación continua, como cuando habla.
    Los tiempos de cada palabra salen de la propia toma (alineación por caracteres de ElevenLabs), sin Whisper.
    Deja voz.wav, assets/tiempos.js y palabras.json con el mismo formato que voz.py + palabras.py."""
    import hashlib, elevenlabs_voz
    from palabras import alinea
    pausa = g.get("pausa_entre_capitulos", 0.9)
    trozos = []
    for f in plano["frases"]:
        trozos.append(f["texto"])
        if "pausa_despues" in f and f is not plano["frases"][-1]:
            trozos.append(f'<break time="{pausa:.1f}s" />')
    texto = " ".join(trozos)
    aj = g.get("ajustes", {})
    firma = hashlib.sha1((g["voice_id"] + g["model"] + texto + json.dumps(aj, sort_keys=True)).encode()).hexdigest()[:12]
    mp3 = os.path.join(build, "voz", f"toma-unica-{firma}.mp3"); ali = mp3 + ".json"
    if not (os.path.exists(mp3) and os.path.exists(ali)):
        a = elevenlabs_voz.speak_con_tiempos(texto, g["voice_id"], g["model"], mp3, stability=aj.get("stability", 0.5),
                                             similarity=aj.get("similarity", 0.85), style=aj.get("style", 0.0))
        json.dump(a, open(ali, "w"))
    a = json.load(open(ali))
    # caracteres → palabras (fuera las etiquetas <break>), en el orden en que se dijeron
    oidas, cur, ini, dentro = [], "", None, False
    for ch, s0, s1 in zip(a["characters"], a["character_start_times_seconds"], a["character_end_times_seconds"]):
        if ch == "<": dentro = True
        if dentro:
            if ch == ">": dentro = False
            continue
        if ch.isspace():
            if cur: oidas.append({"word": cur, "start": ini, "end": fin_}); cur = ""
            continue
        if not cur: ini = s0
        cur += ch; fin_ = s1
    if cur: oidas.append({"word": cur, "start": ini, "end": fin_})
    ent = g["entrada"]; tramos, pal, k = [], {}, 0
    for f in plano["frases"]:
        n = len(f["texto"].split()); mias = oidas[k:k + n]; k += n
        t0, t1 = ent + mias[0]["start"], ent + mias[-1]["end"]
        tramos.append({"id": f["id"], "escena": f["escena"], "texto": f["texto"], "t0": round(t0, 3), "t1": round(t1, 3)})
        rel = [{"word": w["word"], "start": w["start"] + ent - t0, "end": w["end"] + ent - t0} for w in mias]
        pal[f["id"]] = alinea((f.get("pantalla") or f["texto"]).replace("N-E-D", "NED").split(), rel, t1 - t0)
    total = round(ent + voz.dur(mp3) + g["salida"], 3)
    gan = voz.VOZ_LUFS - voz.lufs(mp3)  # una sola toma: una sola ganancia, su dinámica intacta
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", mp3, "-af", f"volume={gan:.2f}dB,adelay={int(ent * 1000)}|{int(ent * 1000)},apad=whole_dur={total}",
                    "-ar", "48000", "-ac", "2", "-t", str(total), os.path.join(build, "voz.wav")], check=True)
    os.makedirs(os.path.join(build, "assets"), exist_ok=True)
    open(os.path.join(build, "assets", "tiempos.js"), "w", encoding="utf-8").write(
        "window.TIEMPOS = " + json.dumps({"total": total, "tramos": tramos}, ensure_ascii=False) + ";\n")
    json.dump(pal, open(os.path.join(build, "palabras.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"  toma única: {len(oidas)} palabras, {voz.dur(mp3):.1f} s")


def voz_por_capitulos(g, build):
    """Para modelos sin <break> (Eleven v4): UNA toma por capítulo, entera y enlazada con sus vecinos, con n tomas.
    Se queda la que dice el texto exacto (Whisper, en local) y, entre esas, la más viva; las pausas entre capítulos
    las pone el montaje de voz.py. Frase a frase sonaba «a trompicones» ({{TITULAR}}, 28-sep): por capítulos, cada idea
    va de un tirón. Deja voz.wav, assets/tiempos.js y palabras.json por FRASE, como voz_unica."""
    import difflib, palabras, whisper
    import guion as guion_mod
    plano = {k: v for k, v in g.items() if k != "capitulos"}
    plano["pausa_entre_frases"] = g.get("pausa_entre_capitulos", 0.9)
    plano["frases"] = [{"id": c["id"], "escena": c["id"],
                        "texto": " ".join(f.get("pantalla") or f["texto"] for f in c["frases"]),
                        "decir": " ".join(f.get("decir") or f["texto"] for f in c["frases"])} for c in g["capitulos"]]
    ruta = os.path.join(build, "guion_capitulos.json")

    def guarda():
        with open(ruta, "w", encoding="utf-8") as fh:
            json.dump(plano, fh, ensure_ascii=False, indent=1)
    guarda()
    voz.main(build, ruta_guion=ruta)  # genera las tomas (o las reutiliza: van firmadas por texto, voz y ajustes)
    n = g.get("n_tomas", 1)
    if n > 1:
        w, elegidas, informe = whisper.load_model("small"), dict(g.get("tomas", {})), {}
        for f in plano["frases"]:
            filas = []
            for x in "abcdefgh"[:n]:
                p = os.path.join(build, "voz", "tomas", f"{f['id']}-{x}.mp3")
                oido = w.transcribe(p, language=g["idioma"], fp16=False)["text"].strip()
                filas.append({"toma": x, "d": round(voz.dur(p), 2), "viva": round(voz.variacion_tono(p), 2), "oido": oido,
                              "texto": round(difflib.SequenceMatcher(None, guion_mod.norm(f["decir"]), guion_mod.norm(oido)).ratio(), 3)})
            informe[f["id"]] = filas
            if f["id"] not in elegidas:
                mejor = max(r["texto"] for r in filas)
                # primero el texto exacto: con un margen ancho ganó una toma que decía «ingeniería» por «ingeniera» (3-oct-2026)
                elegidas[f["id"]] = max((r for r in filas if r["texto"] >= mejor - 0.001), key=lambda r: r["viva"])["toma"]
            print(f"  {f['id']}: " + " · ".join(f"{r['toma']} texto={r['texto']:.3f} viva={r['viva']:.2f}" for r in filas) + f" → {elegidas[f['id']]}")
        with open(os.path.join(build, "revision_tomas.json"), "w", encoding="utf-8") as fh:
            json.dump(informe, fh, ensure_ascii=False, indent=1)
        plano["tomas"] = elegidas
        guarda()
        voz.main(build, ruta_guion=ruta)  # monta con las elegidas; no vuelve a pedir nada a ElevenLabs
    palabras.main(build, ruta_guion=ruta)
    # de capítulos a frases: las palabras de cada capítulo se reparten por el número de palabras de cada frase
    tv, pal = tiempos(build), json.load(open(os.path.join(build, "palabras.json"), encoding="utf-8"))
    por_cap, tramos, por_frase = {x["id"]: x for x in tv["tramos"]}, [], {}
    for c in g["capitulos"]:
        ws, k, t0c = pal[c["id"]], 0, por_cap[c["id"]]["t0"]
        for i, f in enumerate(c["frases"]):
            m = len((f.get("pantalla") or f["texto"]).replace("N-E-D", "NED").split()); mias = ws[k:k + m]; k += m
            a, b = t0c + mias[0]["a"], t0c + mias[-1]["b"]
            tramos.append({"id": f"{c['id']}-{i}", "escena": c["id"], "texto": f["texto"], "t0": round(a, 3), "t1": round(b, 3)})
            por_frase[f"{c['id']}-{i}"] = [{"w": x["w"], "a": round(t0c + x["a"] - a, 3), "b": round(t0c + x["b"] - a, 3)} for x in mias]
    with open(os.path.join(build, "assets", "tiempos.js"), "w", encoding="utf-8") as fh:
        fh.write("window.TIEMPOS = " + json.dumps({"total": tv["total"], "tramos": tramos}, ensure_ascii=False) + ";\n")
    with open(os.path.join(build, "palabras.json"), "w", encoding="utf-8") as fh:
        json.dump(por_frase, fh, ensure_ascii=False, indent=1)


def main(guion, build):
    g = json.load(open(guion, encoding="utf-8"))
    os.makedirs(build, exist_ok=True)
    plano = aplanar(g)
    ruta_plano = os.path.join(build, "guion_plano.json")
    json.dump(plano, open(ruta_plano, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    modo = g.get("toma_unica", True)
    if modo == "capitulo":  # una toma por capítulo (Eleven v4, que no admite <break>)
        voz_por_capitulos(g, build)
    elif modo:
        voz_unica(g, plano, build)
    else:  # frase a frase (el camino del vídeo promo)
        voz.main(build, ruta_guion=ruta_plano)
        import palabras  # Whisper: solo con el venv de Polaris
        palabras.main(build, ruta_guion=ruta_plano)
    tv, pal = tiempos(build), json.load(open(os.path.join(build, "palabras.json"), encoding="utf-8"))
    por_id = {x["id"]: x for x in tv["tramos"]}
    caps = []
    for c in g["capitulos"]:
        frases = []
        for i, f in enumerate(c["frases"]):
            tr = por_id[f"{c['id']}-{i}"]
            frases.append({"t0": tr["t0"], "t1": tr["t1"], "texto": f.get("pantalla") or f["texto"],
                           "palabras": [{"w": w["w"], "a": round(tr["t0"] + w["a"], 3), "b": round(tr["t0"] + w["b"], 3)}
                                        for w in pal[f"{c['id']}-{i}"]]})
        caps.append({k: c[k] for k in ("id", "kicker", "titulo", "escena")} | {"t0": frases[0]["t0"], "t1": frases[-1]["t1"], "frases": frases})
    cifras = json.load(open(os.path.join(AQUI, "remotion", "public", "cifras.json"), encoding="utf-8"))
    cifras.update(g.get("cifras_extra", {}))  # cifras propias del vídeo (p. ej. alcance de un post), en el guion y no a mano
    T = {"slug": g["slug"], "idioma": g["idioma"], "total": tv["total"], "fps": 30, "capitulos": caps, "cifras": cifras}
    if g.get("aviso"):  # aviso de voz clonada (art. 50.4 RIA): Narrado.tsx lo pinta desde el fotograma 0
        T["aviso"] = g["aviso"] | {"entrada": g["entrada"]}
    os.makedirs(PUBLICO, exist_ok=True)
    json.dump(T, open(os.path.join(PUBLICO, "timeline.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    shutil.copyfile(os.path.join(build, "voz.wav"), os.path.join(PUBLICO, "voz.wav"))
    musica = g.get("musica")
    if musica:
        src = musica if os.path.isabs(musica) else os.path.join(MARCA, musica)
        cama_musical(src, T["total"], os.path.join(PUBLICO, "musica.wav"))
    efectos(T, g, os.path.join(PUBLICO, "sfx.wav"))
    for c in caps:
        print(f"  {c['t0']:6.2f} → {c['t1']:6.2f}  {c['id']}")
    print(f"✓ {g['slug']}: {T['total']} s → {PUBLICO}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
