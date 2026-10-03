"""Revisa cada toma de su voz antes de montar: dice el texto exacto y, en inglés, suena americana.

Uso: ~/claudecode/.venv/bin/python revisa_tomas.py <build> [--idioma es]
Lee <build>/voz/tomas/<id>-<x>.mp3 (voz.py) y escribe <build>/revision_tomas.json. Todo en local (egress 0).
  · texto: Whisper escucha y se compara con el guion (0-1). Por debajo de ~0,97, mirar lo que oyó.
  · acento (solo EN): clasificador CommonAccent ECAPA (speechbrain), puntuación «us» frente a «england».
    Es una señal, no una prueba: en frases de menos de ~4 s es ruidosa.
  · viva: variación de tono (voz.variacion_tono).
Por qué (29-sep-2026): en Eleven v4 su clon en inglés tira a británico. La etiqueta de acento no pega igual en todas
las frases ni en todas las tomas, y una toma dijo «Ned» en vez de «N-E-D». Las elegidas van a mano en «tomas» del guion.
"""
import difflib, json, os, subprocess, sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import guion  # noqa: E402
from voz import dur, variacion_tono  # noqa: E402

MODELOS = os.path.expanduser("~/.cache/polaris/speechbrain")
plano = guion.norm  # conserva los guiones: «N-E-D» (letra a letra) y «NED» (de corrido) tienen que distinguirse


def main(build, idioma="en"):
    import whisper
    g = guion.cargar(idioma)
    w = whisper.load_model("small")
    acento = None
    if idioma == "en":
        from speechbrain.inference.classifiers import EncoderClassifier
        acento = EncoderClassifier.from_hparams(source="Jzuluaga/accent-id-commonaccent_ecapa",
                                                savedir=os.path.join(MODELOS, "accent-ecapa-en"))
        lab = acento.hparams.label_encoder
        us, gb = lab.encode_label("us"), lab.encode_label("england")
    res = {}
    for f in g["frases"]:
        fila = []
        for x in "abcdefgh"[:g.get("n_tomas", 1)]:
            p = os.path.join(build, "voz", "tomas", f"{f['id']}-{x}.mp3")
            if not os.path.exists(p):
                continue
            oido = w.transcribe(p, language=idioma)["text"].strip()
            r = {"toma": x, "d": round(dur(p), 2), "viva": round(variacion_tono(p), 2),
                 "texto": round(difflib.SequenceMatcher(None, plano(f["texto"]), plano(oido)).ratio(), 3), "oido": oido}
            if acento:
                wav = p[:-4] + ".16k.wav"
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", p, "-ar", "16000", "-ac", "1", wav], check=True)
                sc = acento.classify_file(wav)[0][0]
                r["us"], r["gb"] = round(float(sc[us]), 2), round(float(sc[gb]), 2)
                os.remove(wav)
            fila.append(r)
        res[f["id"]] = fila
        print(f["id"])
        for r in fila:
            extra = f" us={r['us']:.2f} gb={r['gb']:.2f}" if acento else ""
            aviso = "" if r["texto"] > 0.97 else f"  «{r['oido'][:90]}»"
            print(f"   {r['toma']} d={r['d']:5.2f} viva={r['viva']:.2f}{extra} texto={r['texto']:.3f}{aviso}")
    with open(os.path.join(build, "revision_tomas.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    idioma, a = guion.idioma_de_args(sys.argv[1:])
    main(a[0], idioma)
