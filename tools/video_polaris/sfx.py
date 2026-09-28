"""Efectos de sonido del vídeo, sintetizados en local (stdlib) y colocados con el timeline.

Uso: python3 sfx.py <build>   → <build>/sfx.wav (misma duración que el vídeo)
Barrido (whoosh) en cada corte de escena, golpe grave en el drop («Polaris») y al llegar NED,
clic suave cuando entra cada chip / nodo, y tics mientras cuentan las cifras. Nada de terceros.
"""
import json, math, os, random, struct, sys, wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guion import norm  # noqa: E402

SR, TAU = 48000, 2 * math.pi
rnd = random.Random(3)


def whoosh(d=0.4):
    n = int(d * SR); a = b = 0.0; out = []
    for k in range(n):
        p = k / n; c = 0.01 + 0.12 * math.sin(math.pi * p) ** 2  # filtro más cerrado: más suave ({{TITULAR}}: «se escucha muy fuerte», 28-sep)
        a += c * (rnd.gauss(0, 1) - a); b += c * (a - b)
        out.append(b * math.sin(math.pi * p) ** 1.5)
    return out


def golpe(d=0.9, f0=70):
    return [math.sin(TAU * (f0 * (1 + 1.5 * math.exp(-k / SR * 25))) * k / SR) * math.exp(-k / SR * 4.5) for k in range(int(d * SR))]


def clic(f=1800, d=0.06):
    return [math.sin(TAU * f * k / SR) * math.exp(-k / SR * 70) for k in range(int(d * SR))]


def poner(buf, t, x, g):
    i = int(t * SR)
    for k, v in enumerate(x):
        if 0 <= i + k < len(buf): buf[i + k] += g * v


ANCLAS = {}  # clave EN → prefijo en el idioma del timeline (montaje.py las copia del guion)


def palabra(tr, pref):
    pref = ANCLAS.get(pref, pref)
    return next((w["a"] for w in tr["palabras"] if norm(w["w"]).startswith(pref)), tr["t0"])


def main(build):
    T = json.load(open(os.path.join(build, "timeline.json"), encoding="utf-8"))
    ANCLAS.update(T.get("anclas", {}))
    n = int(T["total"] * SR); buf = [0.0] * n; tr = {x["id"]: x for x in T["tramos"]}
    for x in T["tramos"][1:]:
        poner(buf, x["t0"] - 0.35, whoosh(), 0.1)  # era 0,35: demasiado fuerte
    poner(buf, T["drop"] - 0.02, golpe(), 0.55)
    for pal in ("rare", "breast", "ultra", "metastatic"):   # la apertura cinética: un golpe por palabra (28-sep)
        poner(buf, palabra(tr["problema"], pal) - 0.03, golpe(0.5, 85), 0.3)
    poner(buf, palabra(tr["polaris"], "ned"), golpe(0.7, 90), 0.4)
    poner(buf, palabra(tr["ned"], "ned"), golpe(1.2, 60), 0.5)
    for p in ("samples", "messages", "trips"):
        poner(buf, palabra(tr["carga"], p), clic(1500), 0.18)
    poner(buf, palabra(tr["carga"], "takes") + 0.1, whoosh(0.5), 0.12)   # los tres borradores vuelan juntos a la firma
    poner(buf, palabra(tr["carga"], "takes") + 0.68, golpe(0.5, 110), 0.3)
    t0 = palabra(tr["polaris"], "nothing")
    for i in range(5):
        poner(buf, t0 + 0.2 + i * 0.25, clic(1200 + 150 * i), 0.14)
    t, paso = tr["olvido"]["t0"], 0.05
    while t < tr["olvido"]["t0"] + 1.8:
        poner(buf, t, clic(2600, 0.02), 0.06); t += paso; paso *= 1.08
    pico = max(abs(v) for v in buf) or 1; g = 0.7 / pico
    with wave.open(os.path.join(build, "sfx.wav"), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<hh", int(32767 * g * v), int(32767 * g * v)) for v in buf))
    print("✓ sfx.wav", T["total"], "s")


if __name__ == "__main__":
    main(sys.argv[1])
