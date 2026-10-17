"""Cuando ninguna toma ENTERA sale limpia: una toma base y, para los capítulos donde falla, el mismo capítulo de otra toma
entera de la misma tanda. Se corta en el silencio entre capítulos, así que las dos mitades son la misma interpretación de
toda la narración (no tomas sueltas por capítulo, que es lo que sonaba «a trompicones»).

Uso:  ~/claudecode/.venv/bin/python mezcla_tomas.py <guion.json> <build> <letra nueva> <letra base> <capítulo>=<letra> [...]
      p. ej.  mezcla_tomas.py guiones/orellana-circulo.es.json <build> k e portada=b caso=b
Deja <build>/voz/entera-<nueva>.mp3 con su firma; después, en el guion, "tomas": {"entera": "<nueva>"} y narrado.py.

4-oct-2026, Orellana v9: de diez tomas enteras de Eleven v4, ninguna limpia: seis decían «Billón de Protocol», cinco
«cáncer de mamá», una «ingeniería». La que solo fallaba en «mamá» (capítulo «caso») se completó con ese capítulo de otra.
"""
import hashlib, json, os, subprocess, sys
import numpy as np

GUION, BUILD, NUEVA, BASE = sys.argv[1:5]
CAMBIOS = dict(x.split("=") for x in sys.argv[5:])
g = json.load(open(GUION, encoding="utf-8"))
ids = [c["id"] for c in g["capitulos"]]
rev = {f["toma"]: f for f in json.load(open(os.path.join(BUILD, "revision_tomas.json"), encoding="utf-8")) if f.get("ok")}
vd = os.path.join(BUILD, "voz")


def cortes(letra):
    """Centro del silencio entre cada capítulo y el siguiente, en el tiempo de esa toma."""
    mp3 = os.path.join(vd, f"entera-{letra}.mp3")
    x = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", mp3, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                                     capture_output=True, check=True).stdout, np.int16) / 32768
    rms = np.sqrt(np.convolve(x ** 2, np.ones(320) / 320, "same")[::160])
    umbral = 0.1 * np.percentile(rms, 90)
    cs, out = rev[letra]["caps"], [0.0]
    for a, b in zip(cs, cs[1:]):
        i0, i1 = max(0, int((a["b"] - 0.15) * 100)), min(len(rms), int((b["a"] + 0.7) * 100))
        mejor, desde = None, None
        for i in range(i0, i1 + 1):
            callado = i < i1 and rms[i] < umbral
            if callado and desde is None:
                desde = i
            if not callado and desde is not None:
                if mejor is None or i - desde > mejor[1] - mejor[0]:
                    mejor = (desde, i)
                desde = None
        assert mejor and mejor[1] - mejor[0] >= 15, f"sin silencio claro entre {a['id']} y {b['id']} en la toma {letra}"
        out.append((mejor[0] + mejor[1]) / 200)
    return mp3, out + [len(x) / 16000]


tomas = {l: cortes(l) for l in {BASE, *CAMBIOS.values()}}
entradas, filtros = [], []
orden = sorted(tomas)
for l in orden:
    entradas += ["-i", tomas[l][0]]
for k, cid in enumerate(ids):
    l = CAMBIOS.get(cid, BASE); c = tomas[l][1]
    filtros.append(f"[{orden.index(l)}:a]atrim={c[k]:.3f}:{c[k + 1]:.3f},asetpts=PTS-STARTPTS,aresample=44100,aformat=channel_layouts=mono[s{k}]")
    print(f"  {cid:11s} de la toma {l}  ({c[k]:7.2f}–{c[k + 1]:7.2f} s)")
fc = ";".join(filtros) + ";" + "".join(f"[s{k}]" for k in range(len(ids))) + f"concat=n={len(ids)}:v=0:a=1[o]"
out = os.path.join(vd, f"entera-{NUEVA}.mp3")
subprocess.run(["ffmpeg", "-v", "error", "-y", *entradas, "-filter_complex", fc, "-map", "[o]", "-b:a", "192k", out], check=True)
# la misma firma que calcula narrado.voz_entera, para que la dé por buena sin pedir nada a ElevenLabs
texto = "\n\n".join(" ".join(f.get("decir") or f["texto"] for f in c["frases"]) for c in g["capitulos"])
base = g["voice_id"] + g["model"] + g.get("prefijo", "") + texto + json.dumps(g.get("ajustes", {}), sort_keys=True) + (g.get("language_code") or "")
open(out + ".sha1", "w").write(hashlib.sha1((base + NUEVA).encode()).hexdigest())
print("ok:", out, "· base", BASE, "· cambios", CAMBIOS)
