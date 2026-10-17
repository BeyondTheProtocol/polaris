"""¿Suena la narración de un tirón? Mide, capítulo a capítulo, lo que delata una voz pegada a trozos: el tono (mediana y
cuánto se mueve), el volumen y el ritmo, y marca los saltos entre un capítulo y el siguiente. También mira que cada pausa
que se tocó esté en silencio de verdad (nada de voz cortada).

Uso:  ~/claudecode/.venv/bin/python continuidad.py <build>     (el build de narrado.py: voz.wav, timeline.json, cortes.json)

{{TITULAR}}, 4-oct-2026: «necesito que repases bien que toda la voz tenga continuidad en todo el tiempo que se habla».
Esto NO sustituye a su oído (28-sep: mi medida de tono no detectó que la voz iba «a trompicones»): sirve para no
enseñarle una toma con un salto que ya se ve en los números. Umbrales: 1,5 semitonos, 2 dB, 15 % de ritmo.
"""
import json, os, re, subprocess, sys
import numpy as np

SR = 16000


def tono(x):
    """Mediana del tono (Hz) y cuánto se mueve (desviación en semitonos) de un trozo de voz."""
    n, f0 = 640, []
    for k in range(0, len(x) - n, 320):
        w = x[k:k + n]
        if np.sqrt((w ** 2).mean()) < 0.02:
            continue
        w = w - w.mean(); ac = np.correlate(w, w, "full")[n - 1:]; lo, hi = SR // 400, SR // 75
        j = lo + int(np.argmax(ac[lo:hi]))
        if ac[j] > 0.4 * ac[0]:
            f0.append(SR / j)
    f0 = np.array(f0)
    return (float(np.median(f0)), float((12 * np.log2(f0 / np.median(f0))).std())) if len(f0) > 5 else (0.0, 0.0)


def main(build):
    T = json.load(open(os.path.join(build, "timeline.json"), encoding="utf-8"))
    x = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", os.path.join(build, "voz.wav"), "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                                     capture_output=True, check=True).stdout, np.int16) / 32768
    filas = []
    for c in T["capitulos"]:
        y = x[int(c["t0"] * SR):int(c["t1"] * SR)]
        habla = np.concatenate([x[int(f["t0"] * SR):int(f["t1"] * SR)] for f in c["frases"]])
        med, mueve = tono(y)
        vol = 20 * np.log10(np.sqrt((habla ** 2).mean()) + 1e-9)
        # sílabas (grupos de vocales) y no palabras: un capítulo de palabras cortas no es un capítulo más rápido
        ritmo = sum(len(re.findall(r"[aeiouáéíóúü]+", w["w"].lower())) or 1 for f in c["frases"] for w in f["palabras"]) / sum(f["t1"] - f["t0"] for f in c["frases"])
        filas.append({"id": c["id"], "hz": med, "mueve": mueve, "db": vol, "ritmo": ritmo})
    print(f"{'capítulo':12s} {'tono Hz':>8s} {'se mueve':>9s} {'volumen':>9s} {'síl/s':>6s}   salto respecto al anterior")
    avisos = 0
    for k, f in enumerate(filas):
        nota = ""
        if k:
            a = filas[k - 1]
            st, db, rt = 12 * np.log2(f["hz"] / a["hz"]) if f["hz"] and a["hz"] else 0, f["db"] - a["db"], f["ritmo"] / a["ritmo"] - 1
            malos = [m for m, v in ((f"tono {st:+.1f} st", abs(st) > 1.5), (f"volumen {db:+.1f} dB", abs(db) > 2), (f"ritmo {100 * rt:+.0f} %", abs(rt) > 0.15)) if v]
            nota = ("⚠ " + ", ".join(malos)) if malos else f"tono {st:+.1f} st · {db:+.1f} dB · ritmo {100 * rt:+.0f} %"
            avisos += bool(malos)
        print(f"{f['id']:12s} {f['hz']:8.0f} {f['mueve']:7.2f} st {f['db']:7.1f} dB {f['ritmo']:6.2f}   {nota}")
    hz = [f["hz"] for f in filas if f["hz"]]
    print(f"\ntono: de {min(hz):.0f} a {max(hz):.0f} Hz ({12 * np.log2(max(hz) / min(hz)):.1f} semitonos entre el capítulo más grave y el más agudo)"
          f" · volumen: {max(f['db'] for f in filas) - min(f['db'] for f in filas):.1f} dB de diferencia máxima")
    p = os.path.join(build, "cortes.json")
    if os.path.exists(p):
        C = json.load(open(p, encoding="utf-8"))
        tocadas = [q for q in C.get("pausas", []) if q["final"] != q["natural"]]
        print(f"toma {C.get('toma')}: {len(tocadas)} pausas tocadas de {len(C.get('pausas', []))}; "
              f"la que más, {max((q['final'] - q['natural'] for q in tocadas), default=0):+.2f} s; pegadas sin pausa: {sum(q['natural'] < 0.06 for q in C.get('pausas', []))}")
    # voz cortada: en cada frontera de frase el audio tiene que estar callado justo antes de empezar la siguiente
    fr = [f for c in T["capitulos"] for f in c["frases"]]
    sucias = 0
    for a, b in zip(fr, fr[1:]):
        hueco = x[int(a["t1"] * SR):int(b["t0"] * SR)]
        if len(hueco) > 800 and np.sqrt((hueco[len(hueco) // 3: 2 * len(hueco) // 3] ** 2).mean()) > 0.02:
            sucias += 1; print(f"  ⚠ entre «{a['texto'][-25:]}» y «{b['texto'][:25]}» no hay silencio limpio")
    print(f"fronteras de frase con ruido: {sucias} de {len(fr) - 1}")
    print("✓ sin saltos por encima de los umbrales" if not avisos and not sucias else f"⚠ {avisos} saltos y {sucias} fronteras que conviene oír")


if __name__ == "__main__":
    main(sys.argv[1])
