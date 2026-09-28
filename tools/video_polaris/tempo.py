"""Mide el tempo (BPM) y la fase del compás de una pista, para montar el vídeo sobre su rejilla.

Uso: python3 tempo.py <musica.mp3> [--json salida.json]
Método (stdlib): ffmpeg decodifica a mono 11 025 Hz; envolvente de ataques = subida de energía por
ventanas de 512 muestras; autocorrelación entre 80 y 150 BPM; la fase es el desplazamiento que más
energía de ataques recoge sobre la rejilla. También marca las secciones por nivel (para cuadrar el «drop»).
"""
import json, math, struct, subprocess, sys

SR, HOP = 11025, 256


def main(ruta, salida=None):
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", ruta, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    x = struct.unpack("<%dh" % (len(pcm) // 2), pcm)
    en = [sum(v * v for v in x[i:i + HOP]) / HOP for i in range(0, len(x) - HOP, HOP)]
    env = [max(0.0, math.log1p(en[i]) - math.log1p(en[i - 1])) for i in range(1, len(en))]
    fps = SR / HOP
    mejor = (0, 0)
    for bpm10 in range(800, 1501):
        lag = fps * 60 / (bpm10 / 10)
        l = int(round(lag))
        s = sum(env[i] * env[i + l] for i in range(len(env) - l)) / (len(env) - l)
        if s > mejor[0]:
            mejor = (s, bpm10 / 10)
    bpm = mejor[1]; beat = 60 / bpm
    fases = []
    for k in range(100):
        f = k / 100 * beat; tot = 0.0; t = f
        while t < len(env) / fps:
            i = int(t * fps)
            tot += max(env[max(0, i - 1):i + 2] or [0]); t += beat
        fases.append((tot, f))
    fase = max(fases)[1]
    # nivel por segundo, para ver secciones
    nivel = [round(10 * math.log10(1e-9 + sum(en[int(s * fps):int((s + 1) * fps)]) / max(1, len(en[int(s * fps):int((s + 1) * fps)]))), 1)
             for s in range(int(len(en) / fps))]
    r = {"bpm": bpm, "beat": round(beat, 4), "fase": round(fase, 4), "nivel_db_por_segundo": nivel}
    print("BPM %.1f · beat %.3f s · primer golpe en %.3f s" % (bpm, beat, fase))
    if salida:
        json.dump(r, open(salida, "w"), indent=1)
    return r


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None)
