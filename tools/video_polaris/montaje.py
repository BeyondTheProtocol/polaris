"""Montaje «música primero»: coloca su voz sobre la rejilla del tema y reordena la música por compases.

Uso: python3 montaje.py <build> <musica.mp3> [--idioma es]
Necesita <build>/voz/*.mp3 (voz.py), <build>/palabras.json (palabras.py) y tempo.py.
Escribe:
  <build>/timeline.json   frases con inicio/fin reales en el vídeo + palabras + cortes en el compás
  <build>/voz.wav         su voz colocada
  <build>/musica.wav      la música reordenada: intro → drop en «Polaris» → cuerpo → calma bajo
                          «My doctors do» → vuelta en «still going for NED» → cierre
Criterio de motion (28-sep): los cortes caen en media parte; lo grande (Polaris) cae en el drop.
"""
import json, math, os, subprocess, sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import tempo as tempo_mod
from voz import VOZ_LUFS, lufs  # cada frase igualada con ganancia fija (v16: iban de -16 a -28 LUFS)
import guion

HUECO, INTRO_TIEMPOS, COLA = 0.3, 2, 2.6  # cierre: 2,5 s de tarjeta con vida y fundido corto (director + diseño, 28-sep)


def dur(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                                capture_output=True, text=True, check=True).stdout)


def drop_exacto(musica, aprox=16.0):
    """Instante del salto de energía más grande en ±2 s del drop medido por segundos (resolución 20 ms)."""
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(aprox - 2), "-t", "4", "-i", musica, "-ac", "1", "-ar", "8000",
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    import struct
    x = struct.unpack("<%dh" % (len(pcm) // 2), pcm); h = 160
    e = [sum(v * v for v in x[i:i + h]) for i in range(0, len(x) - h, h)]
    k = max(range(5, len(e)), key=lambda i: math.log1p(sum(e[i:i + 5])) - math.log1p(sum(e[i - 5:i])))
    return aprox - 2 + k * h / 8000


def main(build, musica, idioma="en"):
    g = guion.cargar(idioma)
    pal = json.load(open(os.path.join(build, "palabras.json"), encoding="utf-8"))
    tm = tempo_mod.main(musica)
    beat, medio = tm["beat"], tm["beat"] / 2
    compas = 4 * beat
    drop = drop_exacto(musica)
    niveles = tm["nivel_db_por_segundo"]
    # 1. su voz, en orden natural, cada frase empezando en la siguiente media parte
    t = INTRO_TIEMPOS * beat; tramos = []
    for f in g["frases"]:
        mp3 = os.path.join(build, "voz", f["id"] + ".mp3"); d = dur(mp3)
        t = math.ceil((t - 1e-6) / medio) * medio
        tramos.append({"id": f["id"], "escena": f["escena"], "t0": round(t, 3), "t1": round(t + d, 3), "mp3": mp3,
                       "texto": f.get("pantalla") or f["texto"],
                       "palabras": [{"w": w["w"], "a": round(t + w["a"], 3), "b": round(t + w["b"], 3)} for w in pal[f["id"]]]})
        t += d + HUECO + f.get("pausa_despues", 0)  # respiro con música donde el montaje lo pide
    total = round(tramos[-1]["t1"] + COLA, 3)
    tr = {x["id"]: x for x in tramos}
    # 2. anclas de música: «Polaris» cae en el drop; la calma bajo «doctors»; la vuelta en «still going for NED»
    w_pol = next(w for w in tr["polaris"]["palabras"] if guion.norm(w["w"]).startswith(guion.ancla(g, "polaris")))["a"]
    t_calma = tr["medicos"]["t0"] - 0.3
    t_vuelta = tr["ned"]["t0"] - 0.05
    # secciones del tema original (por nivel): calma = tramo tranquilo después del cuerpo; vuelta = el siguiente fuerte
    fuerte = [i for i, v in enumerate(niveles) if v > max(niveles) - 6]
    cuerpo_fin = max(i for i in fuerte if i < 50)
    calma_ini = next(i for i in range(cuerpo_fin, len(niveles)) if niveles[i] < max(niveles) - 12)
    vuelta_ini = next(i for i in range(calma_ini, len(niveles)) if niveles[i] > max(niveles) - 6)
    piezas = []  # (inicio_en_video, inicio_en_musica, duración)
    intro = w_pol  # lo que suena antes del drop
    piezas.append((0.0, drop - intro, intro))
    # cuerpo: del drop hasta la calma, en compases enteros, repitiendo el cuerpo del tema si hace falta
    hueco_cuerpo = t_calma - w_pol
    n_comp = max(1, round(hueco_cuerpo / compas))
    cuerpo_len = (cuerpo_fin + 1 - drop) // compas * compas
    tv, restante = w_pol, n_comp * compas
    while restante > 1e-3:
        d = min(restante, cuerpo_len); piezas.append((tv, drop, d)); tv += d; restante -= d
    # calma: hasta la vuelta
    d_calma = max(compas, t_vuelta - tv)
    piezas.append((tv, calma_ini + 0.0, d_calma)); tv += d_calma
    # vuelta y cierre: lo que quede del tema desde la vuelta, con cola
    piezas.append((tv, vuelta_ini + 0.0, max(1.0, total - tv)))
    # 3. render de audio
    filtros, entradas = [], []
    for k, (tv, tm0, d) in enumerate(piezas):
        entradas += ["-ss", "%.3f" % max(0, tm0), "-t", "%.3f" % (d + 0.08), "-i", musica]
        fo = 0.05 if k < len(piezas) - 1 else 2.5
        filtros.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,afade=t=in:d=0.03,afade=t=out:st={max(0, d + 0.08 - fo):.3f}:d={fo},adelay={int(tv * 1000)}|{int(tv * 1000)}[m{k}]")
    mezcla = "".join(f"[m{k}]" for k in range(len(piezas))) + f"amix=inputs={len(piezas)}:normalize=0,apad=whole_dur={total}[mus]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *entradas, "-filter_complex", ";".join(filtros) + ";" + mezcla,
                    "-map", "[mus]", "-t", str(total), os.path.join(build, "musica.wav")], check=True)
    ent = sum((["-i", x["mp3"]] for x in tramos), [])
    fil = ";".join(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,volume={VOZ_LUFS - lufs(x['mp3']):.2f}dB,"
                   f"adelay={int(x['t0'] * 1000)}|{int(x['t0'] * 1000)}[v{k}]" for k, x in enumerate(tramos))
    mez = "".join(f"[v{k}]" for k in range(len(tramos))) + f"amix=inputs={len(tramos)}:normalize=0,apad=whole_dur={total}[v]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *ent, "-filter_complex", fil + ";" + mez, "-map", "[v]", "-t", str(total),
                    os.path.join(build, "voz.wav")], check=True)
    for x in tramos:
        x.pop("mp3")
    json.dump({"idioma": g["idioma"], "anclas": g["anclas"], "total": total, "fps": 30, "beat": beat, "drop": round(w_pol, 3), "calma": round(t_calma, 3), "vuelta": round(t_vuelta, 3),
               "piezas_musica": [[round(a, 3), round(b, 3), round(c, 3)] for a, b, c in piezas], "tramos": tramos},
              open(os.path.join(build, "timeline.json"), "w"), ensure_ascii=False, indent=1)
    print(f"✓ total {total} s · beat {beat} · drop del tema {drop:.2f}s → en vídeo {w_pol:.2f}s («Polaris»)")
    for a, b, c in piezas:
        print(f"   música: vídeo {a:6.2f}s ← tema {b:6.2f}s durante {c:5.2f}s")
    for x in tramos:
        print(f"   {x['t0']:6.2f} → {x['t1']:6.2f}  {x['id']}")


if __name__ == "__main__":
    idioma, args = guion.idioma_de_args(sys.argv[1:])
    main(args[0], args[1], idioma)
