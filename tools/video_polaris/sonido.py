"""Fondo sonoro del vídeo, sintetizado en local (opción b del plan: cero terceros, cero licencias).

Uso: python3 sonido.py <salida.wav> [duración=26]
     python3 sonido.py <salida.wav> --tiempos <build>/assets/tiempos.js   (vídeo con historia: todo cuadra con su voz)
     python3 sonido.py <salida.wav> --tiempos <build>/assets/tiempos.js --ritmo   (base de promo con bombo y bajo)
Capas, todas cuadradas con los tiempos de polaris.html:
  pad       acorde de La mayor abierto, con respiración lenta; entra y sale con fundido
  aire      soplo de ruido filtrado en cada cambio de escena
  nodos     cuatro notas suaves cuando se encienden los nodos del diagrama (12,4 s + 0,45 s)
  tics      el contador del bloque 6 (17,2 → 19 s), cada vez más espaciados, como las cifras
  campana   al entrar la tarjeta final (23,1 s)
"""
import math, random, struct, sys, wave

SR = 48000
ESCENAS = [2.0, 5.0, 9.0, 12.0, 17.0, 23.0]
TAU = 2 * math.pi

def poner(buf, t, x, g=1.0):
    i = int(t * SR)
    for k, v in enumerate(x):
        if i + k >= len(buf): break
        buf[i + k] += g * v

def main(salida, dur=26.0, escenas=None, nodos=12.4, tics=17.2, campana=23.1):
    escenas = ESCENAS if escenas is None else escenas
    n = int(dur * SR); mezcla = [0.0] * n
    # pad, con respiración y fundidos
    voces = [(f + d, g) for f, g in ((110, .5), (164.81, .35), (277.18, .22), (440, .10)) for d in (-0.6, 0.6)]
    for i in range(n):
        t = i / SR
        e = min(1.0, t / 1.6, (dur - t) / 2.5)
        mezcla[i] = 0.10 * e * (0.8 + 0.2 * math.sin(TAU * t / 6.5)) * sum(g * math.sin(TAU * f * t) for f, g in voces)
    rnd = random.Random(7)
    # aire: ruido de paso bajo de un polo con ventana de Hann de 0,7 s centrada en el corte
    for t0 in escenas:
        m = int(0.7 * SR); a = 0.0; y = []
        for k in range(m):
            a += 0.035 * (rnd.gauss(0, 1) - a)
            y.append(a * 0.5 * (1 - math.cos(TAU * k / (m - 1))))
        poner(mezcla, t0 - 0.35, y, 0.9)
    # nodos del diagrama
    for i, f in enumerate((523.25, 659.25, 783.99, 1046.5)):
        poner(mezcla, nodos + i * 0.45, [math.sin(TAU * f * k / SR) * math.exp(-14 * k / SR) for k in range(int(0.35 * SR))], 0.10)
    # tics del contador, cada vez más espaciados
    t0, paso = tics, 0.045
    tic = [math.sin(TAU * 2200 * k / SR) * math.exp(-300 * k / SR) for k in range(int(0.02 * SR))]
    while t0 < tics + 1.8:
        poner(mezcla, t0, tic, 0.06); t0 += paso; paso *= 1.07
    # campana de la tarjeta final
    bell = [sum(g * math.sin(TAU * f * k / SR) * math.exp(-d * k / SR) for f, g, d in ((880, 1, 1.6), (1318.5, .5, 2.4), (1760, .3, 3.2)))
            for k in range(int(2.8 * SR))]
    poner(mezcla, campana, bell, 0.09)
    pico = max(abs(v) for v in mezcla); g = 0.5 / pico  # pico a -6 dBFS: fondo, no protagonista
    retraso = 240                                      # 5 ms entre canales: algo de anchura
    with wave.open(salida, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<hh", int(32767 * g * mezcla[i]), int(32767 * g * mezcla[i - retraso] if i >= retraso else 0)) for i in range(n)))
    print("✓", salida, dur, "s")

def ritmo(salida, T, bpm=112):
    """Base de vídeo promo (28-sep, «más de startup»): bombo a negras, charles a corcheas, bajo en
    La-Fa#-Re-Mi, pad suave, subida de ruido antes de cada escena y un golpe grave al entrar cada una.
    Arranca en seco (sin fundido de entrada) y cierra con la campana de siempre sobre la tarjeta final."""
    dur = T["total"]; n = int(dur * SR); mezcla = [0.0] * n; b = 60.0 / bpm
    tr = {x["id"]: x for x in T["tramos"]}
    rnd = random.Random(11)
    bombo = [math.sin(TAU * (45 + 70 * math.exp(-k / SR * 30)) * k / SR) * math.exp(-k / SR * 9) for k in range(int(0.35 * SR))]
    charles = []
    a = 0.0
    for k in range(int(0.05 * SR)):
        r = rnd.gauss(0, 1); charles.append((r - a) * math.exp(-k / SR * 90)); a = r
    raices = (55.0, 46.25, 36.71, 41.2)  # La, Fa#, Re, Mi (graves)
    t, i = 0.0, 0
    while t < dur - 1.5:
        poner(mezcla, t, bombo, 0.55)
        poner(mezcla, t + b / 2, charles, 0.10)
        if i % 2 == 0:
            f = raices[(i // 8) % 4]
            poner(mezcla, t, [math.sin(TAU * f * k / SR) * math.exp(-k / SR * 2.2) for k in range(int(b * 2 * SR))], 0.35)
        t += b; i += 1
    # pad por debajo, entra en seco y respira
    for k in range(n):
        tt = k / SR
        e = min(1.0, (dur - tt) / 2.5)
        mezcla[k] += 0.05 * e * (math.sin(TAU * 220 * tt) + 0.6 * math.sin(TAU * 277.18 * tt) + 0.5 * math.sin(TAU * 329.63 * tt)) * (0.7 + 0.3 * math.sin(TAU * tt / 4))
    # subida de ruido (0,8 s) antes de cada escena + golpe al entrar
    golpe = [math.sin(TAU * 38 * k / SR) * math.exp(-k / SR * 5) for k in range(int(0.6 * SR))]
    for x in T["tramos"][1:]:
        m = int(0.8 * SR); a = 0.0; y = []
        for k in range(m):
            a += (0.02 + 0.2 * k / m) * (rnd.gauss(0, 1) - a); y.append(a * (k / m) ** 2)
        poner(mezcla, x["t0"] - 0.85, y, 0.5)
        poner(mezcla, x["t0"] - 0.05, golpe, 0.45)
    # campana final
    bell = [sum(g * math.sin(TAU * f * k / SR) * math.exp(-d * k / SR) for f, g, d in ((880, 1, 1.6), (1318.5, .5, 2.4), (1760, .3, 3.2)))
            for k in range(int(2.8 * SR))]
    poner(mezcla, tr["ned"]["t0"] - 0.1, bell, 0.12)
    pico = max(abs(v) for v in mezcla); g = 0.6 / pico
    with wave.open(salida, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<hh", int(32767 * g * mezcla[i]), int(32767 * g * mezcla[i - 180] if i >= 180 else 0)) for i in range(n)))
    print("✓", salida, dur, "s (ritmo", bpm, "BPM)")


def desde_tiempos(salida, ruta):
    """Lee tiempos.js (voz.py) y coloca cada acento sobre la frase que le toca."""
    import json
    T = json.loads(open(ruta, encoding="utf-8").read().split("=", 1)[1].rstrip().rstrip(";"))
    tr = {x["id"]: x for x in T["tramos"]}
    pol = tr["polaris"]
    main(salida, T["total"], escenas=[x["t0"] - 0.1 for x in T["tramos"][1:]],
         nodos=pol["t0"] + 0.72 * (pol["t1"] - pol["t0"]), tics=tr["olvido"]["t0"], campana=tr["ned"]["t0"] - 0.1)


if __name__ == "__main__":
    if "--tiempos" in sys.argv and "--ritmo" in sys.argv:
        import json
        ruta = sys.argv[sys.argv.index("--tiempos") + 1]
        ritmo(sys.argv[1], json.loads(open(ruta, encoding="utf-8").read().split("=", 1)[1].rstrip().rstrip(";")))
    elif "--tiempos" in sys.argv:
        desde_tiempos(sys.argv[1], sys.argv[sys.argv.index("--tiempos") + 1])
    else:
        main(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 26.0)
