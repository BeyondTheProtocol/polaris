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
    d = voz.dur(src)
    # si al tema le faltan pocos segundos, se alarga con silencio: encadenarlo consigo mismo volvía a empezar la
    # melodía justo en la tarjeta final (pitch de ClinicOmics, 16-oct-2026)
    n = 1 if total <= d + 8 else max(1, int(total // (d - 4)) + 1)
    ins = sum((["-i", src] for _ in range(n)), [])
    filtro, prev = "", "[0:a]"
    for k in range(1, n):
        filtro += f"{prev}[{k}:a]acrossfade=d=4:c1=tri:c2=tri[x{k}];"; prev = f"[x{k}]"
    filtro += f"{prev}apad=whole_dur={total},atrim=0:{total},afade=t=in:d=1.5,afade=t=out:st={max(0, total - 3)}:d=3,aresample=48000[o]"
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
        import hashlib
        _w = []  # Whisper se carga solo si alguna toma no tiene ya su transcripción guardada
        def oir(p):
            # caché junto a la toma, firmada por su contenido: sin ella, cada pasada retranscribía las 21 tomas
            # (8-10 min) aunque solo hubiera cambiado una frase (16-oct-2026)
            firma = hashlib.sha1(open(p, "rb").read()).hexdigest()
            cache = p + ".oido.json"
            if os.path.exists(cache):
                c = json.load(open(cache, encoding="utf-8"))
                if c.get("sha1") == firma and c.get("idioma") == g["idioma"]:
                    return c["oido"]
            if not _w:
                _w.append(whisper.load_model("small"))
            oido = _w[0].transcribe(p, language=g["idioma"], fp16=False)["text"].strip()
            json.dump({"sha1": firma, "idioma": g["idioma"], "oido": oido}, open(cache, "w", encoding="utf-8"), ensure_ascii=False)
            return oido
        elegidas, informe = dict(g.get("tomas", {})), {}
        for f in plano["frases"]:
            filas = []
            for x in "abcdefgh"[:n]:
                p = os.path.join(build, "voz", "tomas", f"{f['id']}-{x}.mp3")
                oido = oir(p)
                filas.append({"toma": x, "d": round(voz.dur(p), 2), "viva": round(voz.variacion_tono(p), 2), "oido": oido,
                              "texto": round(difflib.SequenceMatcher(None, guion_mod.norm(f["decir"]), guion_mod.norm(oido), autojunk=False).ratio(), 3)})
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


def voz_entera(g, build):
    """TODA la narración en UNA toma también con Eleven v4 ({{TITULAR}}, 4-oct-2026: «que toda la voz tenga continuidad en todo
    el tiempo que se habla»). Por capítulos eran once tomas pegadas, cada una con su energía. Sin <break>: cada capítulo
    va en su párrafo y la pausa la pone el modelo; aquí solo se ALARGA (o se acorta en el silencio) la pausa entre
    capítulos, nunca se corta ni se pega voz. Se piden n tomas enteras; gana la que dice el texto exacto en todos los
    capítulos y no dice nada de «evitar» (grafías que delatan una palabra mal acentuada, p. ej. «neuroendócrina»); entre
    esas, la más viva. Los tiempos de cada palabra los da Whisper sobre la toma. Deja lo mismo que voz_por_capitulos."""
    import difflib, hashlib, re, elevenlabs_voz, palabras, whisper
    import guion as guion_mod
    n1 = lambda s: guion_mod.norm(s).replace("-", "")
    caps = [{"id": c["id"], "frases": c["frases"], "pausa": c.get("pausa_despues", g.get("pausa_entre_capitulos", 0.9)),
             "decir": " ".join(f.get("decir") or f["texto"] for f in c["frases"]),
             "pal": [w for f in c["frases"] for w in (f.get("pantalla") or f["texto"]).split()]} for c in g["capitulos"]]
    texto = "\n\n".join(c["decir"] for c in caps)
    aj, lc, n = g.get("ajustes", {}), g.get("language_code"), g.get("n_tomas", 1)
    base = g["voice_id"] + g["model"] + g.get("prefijo", "") + texto + json.dumps(aj, sort_keys=True) + (lc or "")
    vd = os.path.join(build, "voz"); os.makedirs(vd, exist_ok=True)
    w, filas = whisper.load_model("small"), []
    elegida = g.get("tomas", {}).get("entera")  # ya elegida (a mano, o una mezcla de mezcla_tomas.py): solo se escucha esa
    for x in ([elegida] if elegida else "abcdefghijkl"[:n]):
        mp3 = os.path.join(vd, f"entera-{x}.mp3"); marca = mp3 + ".sha1"
        firma = hashlib.sha1((base + x).encode()).hexdigest()
        if not (os.path.exists(mp3) and os.path.exists(marca) and open(marca).read() == firma):
            elevenlabs_voz.speak(g.get("prefijo", "") + texto, g["voice_id"], g["model"], mp3, stability=aj.get("stability", 0.5),
                                 similarity=aj.get("similarity", 0.85), style=aj.get("style", 0.0), language_code=lc)
            open(marca, "w").write(firma)
        r = w.transcribe(mp3, language=g["idioma"], word_timestamps=True, fp16=False)
        oidas = [p for s in r["segments"] for p in s.get("words", [])]
        # alineación GLOBAL guion↔oído (difflib), para saber qué trozo de lo oído es cada capítulo aunque Whisper junte o parta palabras
        todas = [(k, p) for k, c in enumerate(caps) for p in c["pal"]]
        bloques = difflib.SequenceMatcher(None, [n1(p) for _, p in todas], [n1(o["word"]) for o in oidas], autojunk=False).get_matching_blocks()
        par = {a + d: b + d for a, b, tam in bloques for d in range(tam)}  # índice de palabra del guion → índice de palabra oída
        tramo, i0 = [], 0
        for k, c in enumerate(caps):
            mias = [par[i] for i in range(i0, i0 + len(c["pal"])) if i in par]; i0 += len(c["pal"])
            tramo.append((min(mias), max(mias)) if mias else None)
        if any(t is None for t in tramo):
            filas.append({"toma": x, "ok": False, "motivo": "un capítulo entero no se oye"}); continue
        # la frontera entre dos capítulos es la pausa MÁS LARGA entre la última palabra segura de uno y la primera del
        # siguiente: si Whisper no casa justo la primera palabra de un capítulo, el corte no cae en mitad de una frase
        lim = [0]
        for k in range(len(caps) - 1):
            cand = range(tramo[k][1], max(tramo[k][1] + 1, tramo[k + 1][0]))
            lim.append(1 + max(cand, key=lambda j: oidas[j + 1]["start"] - oidas[j]["end"] if j + 1 < len(oidas) else -1))
        lim.append(len(oidas))
        por_cap, peor = [], 1.0
        for k, c in enumerate(caps):
            mias = oidas[lim[k]:lim[k + 1]]
            oido = "".join(o["word"] for o in mias).strip()
            ratio = difflib.SequenceMatcher(None, guion_mod.norm(c["decir"]), guion_mod.norm(oido), autojunk=False).ratio()
            peor = min(peor, ratio)
            por_cap.append({"id": c["id"], "a": mias[0]["start"], "b": mias[-1]["end"], "texto": round(ratio, 3), "oido": oido, "oidas": mias})
        malas = [m for m in g.get("evitar", []) if m.lower() in r["text"].lower()] + \
                ["falta «" + m + "»" for m in g.get("exigir", []) if not re.search(m, r["text"], re.I)]
        filas.append({"toma": x, "ok": True, "mp3": mp3, "d": round(voz.dur(mp3), 2), "viva": round(voz.variacion_tono(mp3), 2),
                      "peor": round(peor, 3), "media": round(sum(c["texto"] for c in por_cap) / len(por_cap), 4), "evitar": malas, "caps": por_cap})
        print(f"  toma {x}: {voz.dur(mp3):.1f} s · texto peor={peor:.3f} media={filas[-1]['media']:.4f} · viva={filas[-1]['viva']:.2f}" + (f" · DICE {malas}" if malas else ""))
    with open(os.path.join(build, "revision_toma_elegida.json" if elegida else "revision_tomas.json"), "w", encoding="utf-8") as fh:
        json.dump([{k: v for k, v in f.items() if k != "caps"} | {"caps": [{a: b for a, b in c.items() if a != "oidas"} for c in f.get("caps", [])]}
                   for f in filas], fh, ensure_ascii=False, indent=1)
    buenas = [f for f in filas if f["ok"]]
    if not buenas:
        sys.exit("ninguna toma entera vale")
    T = next(f for f in buenas if f["toma"] == elegida) if elegida else \
        max(buenas, key=lambda f: (-len(f["evitar"]), round(f["peor"], 2), round(f["media"], 3), f["viva"]))
    print(f"  → toma {T['toma']}" + (" (elegida a mano)" if elegida else ""))
    # Las pausas: lo que dio el modelo, tocado solo DENTRO del silencio. Entre capítulos se estira (o se recoge) hasta la
    # pausa pedida; entre frases solo se estira hasta pausa_entre_frases cuando el modelo las dejó más juntas (la toma
    # entera corre un 9 % más que las tomas por capítulo de la v8, y a ella la voz acelerada le suena rara). La voz no se toca.
    import bisect
    import numpy as np
    ent, cs, pf = g["entrada"], T["caps"], g.get("pausa_entre_frases", 0.35)
    x = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", T["mp3"], "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                                     capture_output=True, check=True).stdout, np.int16) / 32768
    rms = np.sqrt(np.convolve(x ** 2, np.ones(320) / 320, "same")[::160])  # una medida cada 10 ms
    umbral = 0.1 * np.percentile(rms, 90)

    def silencio(a, b):
        """(inicio, fin) del silencio más largo entre el final de una frase y el final de la PRIMERA palabra de la
        siguiente: Whisper a veces mete la pausa dentro de esa palabra y, buscando solo junto al final de la anterior,
        el silencio se medía corto y se estiraba de más."""
        i0, i1 = max(0, int((a - 0.15) * 100)), min(len(rms), int(b * 100))
        mejor, desde = None, None
        for i in range(i0, i1 + 1):
            callado = i < i1 and rms[i] < umbral
            if callado and desde is None:
                desde = i
            if not callado and desde is not None:
                if mejor is None or i - desde > mejor[1] - mejor[0]:
                    mejor = (desde, i)
                desde = None
        return (mejor[0] / 100, mejor[1] / 100) if mejor else None
    frases = []  # en orden, con el tiempo de cada palabra DENTRO de la toma
    for k, c in enumerate(caps):
        a0, b0 = cs[k]["a"], cs[k]["b"]
        rel = [{"word": o["word"], "start": o["start"] - a0, "end": o["end"] - a0} for o in cs[k]["oidas"]]
        ws, j = palabras.alinea([p.replace("N-E-D", "NED") for p in c["pal"]], rel, b0 - a0), 0
        for i, f in enumerate(c["frases"]):
            m = len((f.get("pantalla") or f["texto"]).split())
            frases.append({"id": f"{c['id']}-{i}", "cap": k, "texto": f["texto"], "pal": [{"w": p["w"], "a": a0 + p["a"], "b": a0 + p["b"]} for p in ws[j:j + m]]})
            j += m
    acum = ent - frases[0]["pal"][0]["a"]
    cortes, desp, pausas, de_cap = [0.0], [acum], [], [0]
    for f, sig in zip(frases, frases[1:]):
        fin_f, ini_s = f["pal"][-1]["b"], sig["pal"][0]["a"]
        hueco = silencio(fin_f, max(ini_s, sig["pal"][0]["b"] - 0.08))
        if hueco:  # los bordes de las dos palabras, al silencio de verdad
            f["pal"][-1]["b"] = max(f["pal"][-1]["a"] + 0.05, min(fin_f, hueco[0] + 0.03))
            sig["pal"][0]["a"] = min(sig["pal"][0]["b"] - 0.05, max(ini_s, hueco[1] - 0.03))
        centro, largo = ((hueco[0] + hueco[1]) / 2, hueco[1] - hueco[0]) if hueco else (None, 0.0)
        if f["cap"] != sig["cap"]:
            if centro is None:
                centro, largo = (fin_f + ini_s) / 2, max(0.0, ini_s - fin_f)
            final = caps[f["cap"]]["pausa"]
        elif centro is not None and 0.12 <= largo < pf:
            final = pf
        else:
            pausas.append({"tras": f["id"], "natural": round(largo, 2), "final": round(largo, 2)}); continue
        acum += final - largo
        cortes.append(centro); desp.append(acum); de_cap.append(sig["cap"])
        pausas.append({"tras": f["id"], "natural": round(largo, 2), "final": round(final, 2), "capitulo": f["cap"] != sig["cap"]})
    cortes.append(voz.dur(T["mp3"]))
    sale = lambda t: t + desp[max(0, bisect.bisect_right(cortes, t) - 1)]  # del tiempo de la toma al tiempo del vídeo
    total = round(sale(frases[-1]["pal"][-1]["b"]) + g["salida"], 3)
    gan = voz.VOZ_LUFS - voz.lufs(T["mp3"])  # una sola toma: una ganancia para toda ella…
    # …y un retoque por capítulo, de ±3 dB como mucho y aplicado en el silencio: la toma entera arranca más alta y va
    # bajando (4,5 dB entre el saludo y el final, medido el 4-oct), y eso se oye como un salto. La dinámica de cada frase no cambia.
    db = [20 * np.log10(np.sqrt(np.mean(np.concatenate([x[int(f["pal"][0]["a"] * 16000):int(f["pal"][-1]["b"] * 16000)] for f in frases if f["cap"] == k]) ** 2)) + 1e-9)
          for k in range(len(caps))]
    retoque = [float(np.clip(np.median(db) - d, -3, 3)) for d in db]
    n_seg = len(desp)
    filtros = ";".join(f"[0:a]atrim={cortes[k]:.3f}:{cortes[k + 1]:.3f},asetpts=PTS-STARTPTS,afade=t=in:d=0.015,"
                       f"afade=t=out:st={max(0, cortes[k + 1] - cortes[k] - 0.015):.3f}:d=0.015,"
                       f"aresample={voz.SR},aformat=channel_layouts=stereo,volume={gan + retoque[de_cap[k]]:.2f}dB,adelay={int((cortes[k] + desp[k]) * 1000)}|{int((cortes[k] + desp[k]) * 1000)}[a{k}]"
                       for k in range(n_seg))
    mezcla = "".join(f"[a{k}]" for k in range(n_seg)) + f"amix=inputs={n_seg}:normalize=0,apad=whole_dur={total}[v]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", T["mp3"], "-filter_complex", filtros + ";" + mezcla, "-map", "[v]",
                    "-ar", str(voz.SR), "-t", str(total), os.path.join(build, "voz.wav")], check=True)
    tramos, por_frase = [], {}
    for f in frases:
        a, b = sale(f["pal"][0]["a"]), sale(f["pal"][-1]["b"])
        tramos.append({"id": f["id"], "escena": caps[f["cap"]]["id"], "texto": f["texto"], "t0": round(a, 3), "t1": round(b, 3)})
        por_frase[f["id"]] = [{"w": p["w"], "a": round(sale(p["a"]) - a, 3), "b": round(sale(p["b"]) - a, 3)} for p in f["pal"]]
    os.makedirs(os.path.join(build, "assets"), exist_ok=True)
    with open(os.path.join(build, "assets", "tiempos.js"), "w", encoding="utf-8") as fh:
        fh.write("window.TIEMPOS = " + json.dumps({"total": total, "tramos": tramos}, ensure_ascii=False) + ";\n")
    with open(os.path.join(build, "palabras.json"), "w", encoding="utf-8") as fh:
        json.dump(por_frase, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(build, "cortes.json"), "w", encoding="utf-8") as fh:  # para continuidad.py: dónde se tocó el silencio y cuánto
        json.dump({"toma": T["toma"], "pausas": pausas, "retoque_db": {c["id"]: round(r, 2) for c, r in zip(caps, retoque)}}, fh, ensure_ascii=False, indent=1)
    print(f"  pausas tocadas: {sum(p['final'] != p['natural'] for p in pausas)} de {len(pausas)} (solo en silencio)")

def palabras_en_que_faltan(g):
    """Cada elemento de escena que entra «cuando la voz dice su palabra» (campo `en`) tiene que tener esa palabra en
    su capítulo. Si no, Pitch/Narrado lo hacen entrar al principio del capítulo, fuera de orden: el 16-oct-2026 la
    cifra «3 tumor types» salió sola antes que «~600» porque la frase nueva ya no decía «three»."""
    import unicodedata, re as _re
    def norm(x):
        x = unicodedata.normalize("NFD", x.lower())
        return _re.sub(r"[^a-z0-9 ]", " ", "".join(ch for ch in x if unicodedata.category(ch) != "Mn")).split()
    faltan = []
    for c in g["capitulos"]:
        dicho = [w for f in c["frases"] for w in norm(f.get("texto", ""))]
        e = c.get("escena", {})
        for clave in ("palabras", "nodos", "items", "cifras"):
            for it in e.get(clave, []):
                pref = (norm(it.get("en", "")) or [""])[0]
                if pref and not any(w.startswith(pref) for w in dicho):
                    faltan.append(f"{c['id']}.{clave}: «{it.get('en')}» no se dice en el capítulo")
        if e.get("gancho_en") and not any(w.startswith(norm(e["gancho_en"])[0]) for w in dicho):
            faltan.append(f"{c['id']}.gancho_en: «{e['gancho_en']}» no se dice en el capítulo")
    return faltan


def main(guion, build):
    g = json.load(open(guion, encoding="utf-8"))
    faltan = palabras_en_que_faltan(g)
    if faltan:
        sys.exit("✗ elementos que entrarían fuera de orden (su palabra no está en la voz):\n  " + "\n  ".join(faltan))
    os.makedirs(build, exist_ok=True)
    plano = aplanar(g)
    ruta_plano = os.path.join(build, "guion_plano.json")
    json.dump(plano, open(ruta_plano, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    modo = g.get("toma_unica", True)
    if modo == "entera":  # una sola toma de toda la narración, también con Eleven v4
        voz_entera(g, build)
    elif modo == "capitulo":  # una toma por capítulo (Eleven v4, que no admite <break>)
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
        caps.append({k: c.get(k, "") for k in ("id", "kicker", "titulo", "escena")} | {"t0": frases[0]["t0"], "t1": frases[-1]["t1"], "frases": frases})
    cifras = json.load(open(os.path.join(AQUI, "remotion", "public", "cifras.json"), encoding="utf-8"))
    cifras.update(g.get("cifras_extra", {}))  # cifras propias del vídeo (p. ej. alcance de un post), en el guion y no a mano
    T = {"slug": g["slug"], "idioma": g["idioma"], "total": tv["total"], "fps": 30, "capitulos": caps, "cifras": cifras}
    if g.get("broll"):  # planos reales (stock con licencia) a pantalla completa sobre una frase: los pinta Pitch.tsx
        T["broll"] = g["broll"]
    if g.get("tema"):  # marca de un vídeo que no es de Polaris (p. ej. un pitch de terceros): la pinta Pitch.tsx
        T["tema"] = g["tema"]
    if g.get("aviso"):  # aviso de voz clonada (art. 50.4 RIA): Narrado.tsx lo pinta desde el fotograma 0
        T["aviso"] = g["aviso"] | {"entrada": g["entrada"]}
    os.makedirs(PUBLICO, exist_ok=True)
    json.dump(T, open(os.path.join(PUBLICO, "timeline.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    shutil.copyfile(os.path.join(PUBLICO, "timeline.json"), os.path.join(build, "timeline.json"))  # public/ no está en git: la copia que dura va con las tomas
    acabado = g.get("acabado_voz")  # EQ/compresión en local sobre las MISMAS tomas (no se vuelve a pedir nada a ElevenLabs)
    if acabado:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", os.path.join(build, "voz.wav"), "-af", acabado,
                        "-ar", "48000", "-ac", "2", os.path.join(PUBLICO, "voz.wav")], check=True)
    else:
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
