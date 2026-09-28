"""Tiempo exacto de cada palabra de su voz, con Whisper EN LOCAL (egress 0), frase a frase.

Uso: ~/claudecode/.venv/bin/python palabras.py <build> [--idioma es]
Lee <build>/voz/<id>.mp3 (voz.py) y el guion del idioma (guion.py); Whisper escucha en ese idioma; escribe <build>/palabras.json:
  {id: [{"w": palabra_del_guion, "a": s, "b": s}, ...]}  (tiempos relativos al inicio de la frase)
En pantalla van SIEMPRE las palabras del guion firmado (campo «pantalla» si existe), nunca lo que
Whisper crea oír: Whisper solo aporta los tiempos. Si una palabra no casa, hereda un tiempo
proporcional entre sus vecinas.
"""
import json, os, re, sys
import whisper

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import guion  # noqa: E402
norm = lambda s: guion.norm(s).replace("-", "")


def alinea(guion, oidas, dur):
    out = [{"w": w, "a": None, "b": None} for w in guion]
    j = 0
    for i, w in enumerate(guion):
        for k in range(j, min(j + 4, len(oidas))):  # busca un poco por delante: Whisper a veces parte o junta palabras
            if norm(oidas[k]["word"]) and (norm(oidas[k]["word"]) == norm(w) or norm(w).startswith(norm(oidas[k]["word"]))
                                          or norm(oidas[k]["word"]).startswith(norm(w))):
                out[i]["a"], out[i]["b"] = oidas[k]["start"], oidas[k]["end"]; j = k + 1; break
    # huecos: interpolación proporcional por caracteres entre anclas conocidas
    idx = [i for i, o in enumerate(out) if o["a"] is not None]
    anclas = [(-1, 0.0)] + [(i, out[i]["a"]) for i in idx] + [(len(out), dur)]
    for (i0, t0), (i1, t1) in zip(anclas, anclas[1:]):
        hueco = list(range(i0 + 1, i1))
        if not hueco: continue
        ini = out[i0]["b"] if i0 >= 0 else t0
        tot = sum(len(out[i]["w"]) for i in hueco) or 1; t = ini
        for i in hueco:
            d = (t1 - ini) * len(out[i]["w"]) / tot; out[i]["a"], out[i]["b"] = t, t + d; t += d
    for o in out:
        o["a"], o["b"] = round(o["a"], 3), round(o["b"], 3)
    return out


def main(build, idioma="en", ruta_guion=None):
    g = json.load(open(ruta_guion, encoding="utf-8")) if ruta_guion else guion.cargar(idioma)
    g.setdefault("anclas", {})
    m = whisper.load_model("small")
    res, fallos = {}, 0
    for f in g["frases"]:
        mp3 = os.path.join(build, "voz", f["id"] + ".mp3")
        r = m.transcribe(mp3, language=g.get("idioma", "en"), word_timestamps=True, fp16=False)
        oidas = [w for s in r["segments"] for w in s.get("words", [])]
        dur = r["segments"][-1]["end"] if r["segments"] else 1.0
        pal = (f.get("pantalla") or f["texto"]).replace("N-E-D", "NED").split()
        res[f["id"]] = alinea(pal, oidas, dur)
        casadas = sum(1 for o in oidas if o)
        print(f"  {f['id']:10s} {len(pal):3d} palabras · Whisper oyó: {r['text'].strip()[:70]}")
    json.dump(res, open(os.path.join(build, "palabras.json"), "w"), ensure_ascii=False, indent=1)
    print("✓ palabras.json")


if __name__ == "__main__":
    # palabras.py <build> [--idioma es] [--guion <ruta.json>]  (sin nada: el vídeo de Polaris en EN)
    idioma, a = guion.idioma_de_args(sys.argv[1:])
    main(a[0], idioma, a[a.index("--guion") + 1] if "--guion" in a else None)
