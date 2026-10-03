"""Genera la voz del vídeo con historia, frase a frase, y la línea de tiempo que sale de ella.

Uso: python3 voz.py <build> [--idioma es]   (sin --idioma: el EN firmado, guion_voz.json)
Deja en <build>/voz/<id>.mp3 cada frase, <build>/voz.wav (la pista entera, con pausas) y
<build>/assets/tiempos.js con el inicio y fin de cada frase. La duración del vídeo sale SOLO de
aquí (consejero-arquitectura, 28-sep): ni polaris.html ni grabar.mjs llevan un número a mano.

Solo se manda TEXTO a ElevenLabs (su clon ya existe en su cuenta); la voz no sale de aquí.
Si una frase ya está generada con el mismo texto, la misma voz y los mismos ajustes, no se vuelve a pedir.
«ajustes» en guion_voz.json fija el tono (stability/similarity/style) y el tempo (atempo).
"""
import hashlib, json, os, shutil, subprocess, sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, ".."))
import elevenlabs_voz  # noqa: E402
sys.path.insert(0, AQUI)
import guion  # noqa: E402

SR = 48000


VOZ_LUFS = -18.0


def lufs(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    return float(json.loads(r[r.rindex("{"):r.rindex("}") + 1])["input_i"])


def dur(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                                capture_output=True, text=True, check=True).stdout)


def variacion_tono(path):
    """Cuánto sube y baja la voz (desviación del tono en semitonos). Plano = lectura monótona."""
    import numpy as np
    x = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                                     capture_output=True, check=True).stdout, np.int16) / 32768
    sr, n, f0 = 16000, 640, []
    for k in range(0, len(x) - n, 320):
        w = x[k:k + n]
        if np.sqrt((w ** 2).mean()) < 0.02:
            continue
        w = w - w.mean(); ac = np.correlate(w, w, "full")[n - 1:]; lo, hi = sr // 400, sr // 75
        j = lo + int(np.argmax(ac[lo:hi]))
        if ac[j] > 0.4 * ac[0]:
            f0.append(sr / j)
    f0 = np.array(f0)
    return float((12 * np.log2(f0 / np.median(f0))).std()) if len(f0) > 5 else 0.0


def elige_toma(tomas):
    """La toma con más variación de tono, descartando las que duran raro (pausas extrañas o atropelladas)."""
    ds = {x: dur(p) for x, p in tomas}
    med = sorted(ds.values())[len(ds) // 2]
    validas = [(x, p) for x, p in tomas if 0.8 * med <= ds[x] <= 1.25 * med] or tomas
    return max(validas, key=lambda xp: variacion_tono(xp[1]))[0]


def main(build, idioma="en", ruta_guion=None):
    g = json.load(open(ruta_guion, encoding="utf-8")) if ruta_guion else guion.cargar(idioma)
    g.setdefault("anclas", {})
    vd = os.path.join(build, "voz"); os.makedirs(vd, exist_ok=True)
    os.makedirs(os.path.join(build, "assets"), exist_ok=True)
    t, tramos, trozos = g["entrada"], [], []
    for i, f in enumerate(g["frases"]):
        mp3 = os.path.join(vd, f["id"] + ".mp3")
        aj = g.get("ajustes", {})
        # Enlazado: cada frase se genera sabiendo qué va antes y después, para que se entone como parte
        # de un discurso y no como una lectura suelta ({{TITULAR}}, 28-sep: «sobre todo es el tono de hablar»).
        antes = " ".join(x["texto"] for x in g["frases"][max(0, i - 2):i]) if g.get("enlazado") else ""
        despues = g["frases"][i + 1]["texto"] if g.get("enlazado") and i + 1 < len(g["frases"]) else ""
        n = g.get("n_tomas", 1)
        # v4: «prefijo» es la etiqueta de acento (no se lee en voz alta) y «language_code» fija el idioma.
        prefijo, lc = g.get("prefijo", ""), g.get("language_code")
        # «decir»: lo que oye la voz cuando la v4 pronuncia mal el texto firmado (ES: «Convirtió» → «Convertió», «3D» → «3»).
        # En pantalla y en los tiempos de palabra sigue el texto firmado; «decir» solo cambia la grafía, nunca las palabras.
        decir = f.get("decir", f["texto"])
        # «semilla»: fija la toma cuando solo una suena bien (ES 3d, 29-sep: el tono subía al final de «convirtió»)
        semilla = f.get("semilla")
        base = (g["voice_id"] + g["model"] + prefijo + decir + json.dumps(aj, sort_keys=True) + (lc or "") + str(semilla or "")
                + (antes + "|" + despues if n > 1 else ""))
        tomas = []
        for x in "abcdefgh"[:n]:
            ruta = os.path.join(vd, "tomas", f"{f['id']}-{x}.mp3") if n > 1 else mp3
            firma = hashlib.sha1((base + (x if n > 1 else "")).encode()).hexdigest()
            marca = ruta + ".sha1"
            if not (os.path.exists(ruta) and os.path.exists(marca) and open(marca).read() == firma):
                os.makedirs(os.path.dirname(ruta), exist_ok=True)
                crudo = ruta + ".crudo.mp3"
                elevenlabs_voz.speak(prefijo + decir, g["voice_id"], g["model"], crudo, stability=aj.get("stability", 0.5),
                                     similarity=aj.get("similarity", 0.85), style=aj.get("style", 0.0),
                                     previous_text=antes, next_text=despues, language_code=lc,
                                     seed=None if semilla is None else semilla + "abcdefgh".index(x))
                # atempo acelera sin cambiar el tono de la voz
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", crudo, "-af", "atempo=%s" % aj.get("tempo", 1.0),
                                "-b:a", "160k", ruta], check=True)
                os.remove(crudo)
                open(marca, "w").write(firma)
            tomas.append((x, ruta))
        if n > 1:
            elegida = g.get("tomas", {}).get(f["id"]) or elige_toma(tomas)
            shutil.copyfile(dict(tomas)[elegida], mp3)
            print(f"  {f['id']}: toma {elegida}" + (" (elegida a mano)" if g.get("tomas", {}).get(f["id"]) else " (la más viva)"))
        d = dur(mp3)
        tramos.append({"id": f["id"], "escena": f["escena"], "texto": f["texto"], "t0": round(t, 3), "t1": round(t + d, 3)})
        trozos.append((t, mp3))
        t += d + (f.get("pausa_despues", g["pausa_entre_frases"]) if i < len(g["frases"]) - 1 else 0)
    total = round(t + g["salida"], 3)
    # Pista de voz: cada frase en su sitio exacto (adelay), sobre silencio de la duración total.
    # Cada frase se iguala a VOZ_LUFS con una ganancia FIJA (no compresión: su dinámica natural se queda).
    # ElevenLabs entrega cada frase a su aire: en la v15 iban de -16 a -28 LUFS, y el cierre era lo más bajo.
    entradas = sum((["-i", p] for _, p in trozos), [])
    filtros = ";".join(f"[{k}:a]aresample={SR},aformat=channel_layouts=stereo,volume={VOZ_LUFS - lufs(p):.2f}dB,"
                       f"adelay={int(t0*1000)}|{int(t0*1000)}[a{k}]"
                       for k, (t0, p) in enumerate(trozos))
    mezcla = "".join(f"[a{k}]" for k in range(len(trozos))) + f"amix=inputs={len(trozos)}:normalize=0,apad=whole_dur={total}[v]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *entradas, "-filter_complex", filtros + ";" + mezcla,
                    "-map", "[v]", "-ar", str(SR), "-t", str(total), os.path.join(build, "voz.wav")], check=True)
    with open(os.path.join(build, "assets", "tiempos.js"), "w", encoding="utf-8") as fh:
        fh.write("window.TIEMPOS = " + json.dumps({"total": total, "tramos": tramos}, ensure_ascii=False) + ";\n")
    for tr in tramos:
        print(f"  {tr['t0']:6.2f} → {tr['t1']:6.2f}  {tr['id']}")
    print("✓ total", total, "s")


if __name__ == "__main__":
    # voz.py <build> [--idioma es] [--guion <ruta.json>]  (sin nada: el vídeo de Polaris en EN)
    idioma, a = guion.idioma_de_args(sys.argv[1:])
    main(a[0], idioma, a[a.index("--guion") + 1] if "--guion" in a else None)
