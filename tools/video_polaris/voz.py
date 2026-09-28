"""Genera la voz del vídeo con historia, frase a frase, y la línea de tiempo que sale de ella.

Uso: python3 voz.py <build>
Deja en <build>/voz/<id>.mp3 cada frase, <build>/voz.wav (la pista entera, con pausas) y
<build>/assets/tiempos.js con el inicio y fin de cada frase. La duración del vídeo sale SOLO de
aquí (consejero-arquitectura, 28-sep): ni polaris.html ni grabar.mjs llevan un número a mano.

Solo se manda TEXTO a ElevenLabs (su clon ya existe en su cuenta); la voz no sale de aquí.
Si una frase ya está generada con el mismo texto, la misma voz y los mismos ajustes, no se vuelve a pedir.
«ajustes» en guion_voz.json fija el tono (stability/similarity/style) y el tempo (atempo).
"""
import hashlib, json, os, subprocess, sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, ".."))
import elevenlabs_voz  # noqa: E402

SR = 48000


def dur(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                                capture_output=True, text=True, check=True).stdout)


def main(build):
    g = json.load(open(os.path.join(AQUI, "guion_voz.json"), encoding="utf-8"))
    vd = os.path.join(build, "voz"); os.makedirs(vd, exist_ok=True)
    os.makedirs(os.path.join(build, "assets"), exist_ok=True)
    t, tramos, trozos = g["entrada"], [], []
    for i, f in enumerate(g["frases"]):
        mp3 = os.path.join(vd, f["id"] + ".mp3")
        aj = g.get("ajustes", {})
        firma = hashlib.sha1((g["voice_id"] + g["model"] + f["texto"] + json.dumps(aj, sort_keys=True)).encode()).hexdigest()
        marca = mp3 + ".sha1"
        if not (os.path.exists(mp3) and os.path.exists(marca) and open(marca).read() == firma):
            crudo = mp3 + ".crudo.mp3"
            elevenlabs_voz.speak(f["texto"], g["voice_id"], g["model"], crudo, stability=aj.get("stability", 0.5),
                                 similarity=aj.get("similarity", 0.85), style=aj.get("style", 0.0))
            # atempo acelera sin cambiar el tono de la voz
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", crudo, "-af", "atempo=%s" % aj.get("tempo", 1.0),
                            "-b:a", "160k", mp3], check=True)
            os.remove(crudo)
            open(marca, "w").write(firma)
        d = dur(mp3)
        tramos.append({"id": f["id"], "escena": f["escena"], "texto": f["texto"], "t0": round(t, 3), "t1": round(t + d, 3)})
        trozos.append((t, mp3))
        t += d + (g["pausa_entre_frases"] if i < len(g["frases"]) - 1 else 0)
    total = round(t + g["salida"], 3)
    # Pista de voz: cada frase en su sitio exacto (adelay), sobre silencio de la duración total
    entradas = sum((["-i", p] for _, p in trozos), [])
    filtros = ";".join(f"[{k}:a]aresample={SR},aformat=channel_layouts=stereo,adelay={int(t0*1000)}|{int(t0*1000)}[a{k}]"
                       for k, (t0, _) in enumerate(trozos))
    mezcla = "".join(f"[a{k}]" for k in range(len(trozos))) + f"amix=inputs={len(trozos)}:normalize=0,apad=whole_dur={total}[v]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *entradas, "-filter_complex", filtros + ";" + mezcla,
                    "-map", "[v]", "-ar", str(SR), "-t", str(total), os.path.join(build, "voz.wav")], check=True)
    with open(os.path.join(build, "assets", "tiempos.js"), "w", encoding="utf-8") as fh:
        fh.write("window.TIEMPOS = " + json.dumps({"total": total, "tramos": tramos}, ensure_ascii=False) + ";\n")
    for tr in tramos:
        print(f"  {tr['t0']:6.2f} → {tr['t1']:6.2f}  {tr['id']}")
    print("✓ total", total, "s")


if __name__ == "__main__":
    main(sys.argv[1])
