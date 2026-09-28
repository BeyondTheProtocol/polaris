#!/usr/bin/env python3
"""test_video_polaris.py — el vídeo público de Polaris no enseña lo que no debe.

tools/video_polaris/ produce un vídeo que sale a redes (27-sep-2026). Frena:
  · léxico vetado del muro en cualquier fichero de la herramienta ({{CONTACTO}}, ingeniera, scientist, pecho)
  · cifras clínicas escritas a mano en el HTML (mm, SUV, CA 15-3, RECIST…): el vídeo no lleva ninguna
  · que las cifras del sistema se escriban a mano: tienen que salir de window.CIFRAS (anatomia.py)
  · que el grabador del hígado deje de ocultar los rótulos de medida del visor (.lv-rotulo)
  · que preparar.py vuelva a tirar del clip del 20-sep, que enseña la mama con su cifra
Vídeo con historia (28-sep, guion firmado por {{TITULAR}} en guion_voz.json):
  · el guion no dice «liver», «helped plan my biopsy» ni «Spanish protocols» (decisión suya: «standard»)
  · la duración sale SOLO de la voz: ni historia.html ni grabar.mjs llevan un 26 fijo para este vídeo
  · si hay un render en la fuente de verdad, dura lo que suman las frases (±0,5 s)
"""
import json
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "tools", "video_polaris")


def leer(n):
    with open(os.path.join(DIR, n), encoding="utf-8") as fh:
        return fh.read()


class TestVideoPolaris(unittest.TestCase):
    def test_lexico_vetado(self):
        for n in os.listdir(DIR):
            if n.endswith((".html", ".mjs", ".py", ".sh", ".md")):
                txt = leer(n)
                # el test y el LEEME pueden NOMBRAR el veto; el resto no puede usarlo
                if n == "LEEME.md":
                    continue
                for mala in ("contacto", "ingeniera", "ingeniera", "scientist", "pecho"):
                    self.assertNotIn(mala, txt.lower(), f"{n} contiene «{mala}»")

    def test_sin_cifras_clinicas_en_pantalla(self):
        html = leer("polaris.html")
        texto = re.sub(r"<(script|style)[\s\S]*?</\1>", "", html)  # solo lo que se pinta
        texto = re.sub(r"<[^>]+>", " ", texto)
        for pat in (r"\d\s*mm\b", r"\bSUV\b", r"CA\s*15-3", r"\bRECIST\b", r"\bECOG\b", r"\d+\s*lesions?"):
            self.assertIsNone(re.search(pat, texto, re.I), f"cifra clínica en pantalla: {pat}")

    def test_cifras_del_sistema_salen_de_la_anatomia(self):
        html = leer("polaris.html")
        self.assertIn("window.CIFRAS[", html)
        for n in re.findall(r'data-n="(\w+)">([^<]*)<', html):
            self.assertEqual(n[1], "0", f"la cifra {n[0]} está escrita a mano")
        self.assertIn('"inventario", "--json"', leer("preparar.py"))

    def test_grabador_oculta_rotulos_y_no_graba_la_mama(self):
        g = leer("grabar_higado.mjs")
        self.assertIn('[class$="-rotulo"]', g)  # oculta TODOS los rótulos de medida (hígado lv-, mama bv-, los que vengan)
        # por defecto graba el hígado; la mama solo si se pide («Right breast»), decisión de {{TITULAR}} del 28-sep para el vídeo con historia
        self.assertIn("TARJETA = 'Liver'", g)
        self.assertNotIn("x-lesiones-web-1920x1080-20260920", leer("preparar.py"))

    def test_guion_con_historia(self):
        g = json.loads(leer("guion_voz.json"))
        texto = " ".join(f["texto"] for f in g["frases"]).lower()
        for mala in ("liver", "helped plan my biopsy", "spanish protocols", "scientist", "contacto"):
            self.assertNotIn(mala, texto)
        self.assertIn("without my sign-off", texto)  # la puerta con gate humano (consejero-arquitectura)

    def test_duracion_sale_de_la_voz(self):
        h = leer("historia.html")
        self.assertIn("window.TIEMPOS", h)
        self.assertIsNone(re.search(r"\b26\b", re.sub(r"<!--[\s\S]*?-->", "", h)), "historia.html lleva un 26 fijo")
        self.assertIn("hasta = 'auto'", leer("grabar.mjs"))

    def test_su_voz_no_se_acelera_ni_se_exagera(self):
        # 28-sep: con tempo 1,06 y style 0,55 a {{TITULAR}} le sonó «super raro, muy acelerada, no natural».
        # La energía de promo va en la música y el movimiento, nunca en su voz.
        aj = json.loads(leer("guion_voz.json")).get("ajustes", {})
        self.assertEqual(aj.get("tempo", 1.0), 1.0, "no se acelera su voz")
        self.assertLessEqual(aj.get("style", 0.0), 0.2, "style alto = voz exagerada")
        self.assertGreaterEqual(aj.get("stability", 0.5), 0.45, "stability baja = voz inestable")

    def test_escenas_sin_solape_y_motivo_quieto(self):
        # diseño devolvió el render del 28-sep por doble exposición entre escenas y la estrella girando sin fin
        h = leer("historia.html")
        self.assertIn("TR[i].t0 - CORTE", h)
        self.assertIn("TR[i + 1].t0 - CORTE", h)  # la saliente acaba donde empieza la entrante
        self.assertNotIn("rotate(${t *", h)       # ningún giro que crezca con t sin tope

    def test_render_dura_lo_que_la_voz(self):
        # el render vigente es el ÚLTIMO «polaris-story-v*-remotion-*.mp4», con su timeline archivado al lado
        import glob
        carpeta = os.path.expanduser("~/claudecode/00_FUENTE-DE-VERDAD/07 · Marca/Videos-Polaris")
        # orden por número de versión: por texto, «v9» quedaba detrás de «v15» y se comprobaba un render viejo
        mp4s = sorted(glob.glob(os.path.join(carpeta, "polaris-story-v*-remotion-1920x1080-BORRADOR.mp4")),
                      key=lambda p: int(re.search(r"-v(\d+)-", os.path.basename(p)).group(1)))
        if not mp4s:
            self.skipTest("sin render de Remotion en esta máquina")
        mp4 = mp4s[-1]
        sidecar = mp4.replace("-1920x1080-BORRADOR.mp4", "-timeline.json")
        self.assertTrue(os.path.exists(sidecar), f"falta el timeline del render: {sidecar}")
        tl = json.load(open(sidecar))
        d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", mp4],
                                 capture_output=True, text=True).stdout)
        self.assertLess(abs(d - tl["total"]), 0.5, f"el mp4 dura {d} y el timeline {tl['total']}")
        g = json.loads(leer("guion_voz.json"))
        self.assertEqual([x["id"] for x in tl["tramos"]], [f["id"] for f in g["frases"]], "el render no es del guion vigente")
        # techo de audio: X recodifica al subir; con el pico a -1.3 dBTP el AAC puede recortar (onetake: ≤ -3)
        r = subprocess.run(["ffmpeg", "-hide_banner", "-i", mp4, "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True).stderr
        tp = float(json.loads(r[r.rindex("{"):r.rindex("}") + 1])["input_tp"])
        self.assertLessEqual(tp, -3.0, f"pico del render {tp} dBTP (> -3)")

    def test_guiones_narrados(self):
        # vídeos narrados (narrado.py + Narrado.tsx): cada guion es válido, sin léxico vetado, y cada escena existe
        import glob
        tsx = leer("remotion/src/Narrado.tsx")
        tipos = set(re.findall(r"case '(\w+)':", tsx))
        guiones = glob.glob(os.path.join(DIR, "guiones", "*.json"))
        self.assertTrue(guiones, "no hay guiones narrados")
        for p in guiones:
            g = json.load(open(p, encoding="utf-8"))
            texto = json.dumps(g, ensure_ascii=False).lower()
            for mala in ("contacto", "ingeniera", "scientist", "pecho"):
                self.assertNotIn(mala, texto, f"{os.path.basename(p)} contiene «{mala}»")
            for k in ("slug", "idioma", "voice_id", "model", "capitulos"):
                self.assertIn(k, g, f"{os.path.basename(p)}: falta «{k}»")
            for c in g["capitulos"]:
                self.assertTrue(c["frases"], f"capítulo {c['id']} sin frases")
                e = c["escena"]
                for x in [e] + e.get("pasos", []):
                    self.assertIn(x["tipo"], tipos | {"portada", "cierre"}, f"escena desconocida «{x['tipo']}» en {c['id']}")
        # lo que está escrito en pantalla no se subtitula: el filtro vive en el código, no a mano
        self.assertIn("const enPantalla", tsx)
        self.assertIn("< 0.6", tsx)

    def test_master_y_subtitulos(self):
        rs = leer("remotion/render.sh")
        self.assertIn("TP=-4", rs)  # la receta de master vive en el repo, no en un historial
        tsx = leer("remotion/src/Polaris.tsx")
        # lo que ya está rotulado entero no se subtitula (launch-video-kit: nunca el mismo texto dos veces)
        self.assertIn("const ROTULADO", tsx)
        # las palabras rotuladas van por idioma en textos.ts (28-sep: en ES se colaban subtitulos duplicados)
        textos = leer(os.path.join("remotion", "src", "textos.ts"))
        bloques = re.findall(r"rotulado: \{(.*?)\},\n", textos)
        self.assertEqual(len(bloques), 2, "falta el rotulado de EN o de ES")
        for b in bloques:
            for tramo in ("problema", "polaris", "medicos", "ned", "'web-final'"):
                self.assertIn(tramo, b)


class TestVideoPolarisES(unittest.TestCase):
    """Versión ES (28-sep): guion_voz_es.json + textos.ts. Mismo componente, mismas escenas, sus anclas."""

    def setUp(self):
        self.en = json.loads(leer("guion_voz.json"))
        self.es = json.loads(leer("guion_voz_es.json"))

    def test_mismas_frases_y_escenas_que_el_en(self):
        self.assertEqual([(f["id"], f["escena"]) for f in self.es["frases"]], [(f["id"], f["escena"]) for f in self.en["frases"]])
        self.assertEqual(self.es["idioma"], "es")
        self.assertEqual(self.es["voice_id"], "51aLNh96A9WJZODzYIyP", "su clon PVC entrenado con su podcast en español")

    def test_guion_es_dice_lo_que_ella_firmo_en_en(self):
        texto = " ".join(f["texto"] for f in self.es["frases"]).lower()
        for mala in ("pecho", "ingeniera", "ingeniera", "contacto", "españoles", "espanoles", "explica polaris"):
            self.assertNotIn(mala, texto)
        for buena in ("{{DIAGNOSTICO}}", "estándar", "ingeniera", "sin mi firma", "ayudó a elegir", "biopsiar", "deciden mis médicos", "help titular"):
            self.assertIn(buena, texto)

    def test_su_voz_es_tampoco_se_acelera(self):
        aj = self.es.get("ajustes", {})
        self.assertEqual(aj.get("tempo", 1.0), 1.0)
        self.assertLessEqual(aj.get("style", 0.0), 0.2)
        self.assertGreaterEqual(aj.get("stability", 0.5), 0.45)

    def test_cada_ancla_casa_con_una_palabra_de_su_frase(self):
        # el montaje busca palabras por prefijo EN; en ES cada clave tiene que existir y casar, o la escena cae en t0 sin avisar
        sys.path.insert(0, DIR)
        import guion
        tsx = leer(os.path.join("remotion", "src", "Polaris.tsx"))
        usos = set(re.findall(r"dice\('([\w-]+)', '([\w-]+)'", tsx))
        usos |= set(re.findall(r'palabra\(tr\["([\w-]+)"\], "([\w-]+)"\)', leer("sfx.py")))
        usos |= {("problema", p) for p in ("rare", "breast", "ultra", "metastatic", "two")} | {("olvido", "lot")}
        usos |= {("carga", p) for p in ("samples", "messages", "trips")} | {("polaris", "polaris")}
        frases = {f["id"]: [guion.norm(w) for w in (f.get("pantalla") or f["texto"]).replace("N-E-D", "NED").split()] for f in self.es["frases"]}
        for fid, clave in sorted(usos):
            self.assertIn(clave, self.es["anclas"], f"falta el ancla ES de «{clave}»")
            pref = self.es["anclas"][clave]
            self.assertTrue(any(w.startswith(pref) for w in frases[fid]), f"«{pref}» ({clave}) no está en la frase {fid}")

    def test_textos_de_pantalla_fuera_del_componente(self):
        tsx = leer(os.path.join("remotion", "src", "Polaris.tsx"))
        for fijo in ("TRIALS TRACKED", "It doesn't decide", "Still going for NED", ">DRAFT<", "HUMAN GATE", "no evidence of disease",
                     "So I built Polaris", "a team of AI agents", "'en-US'"):
            self.assertNotIn(fijo, tsx, f"texto de pantalla fijo en el componente: {fijo}")
        textos = leer(os.path.join("remotion", "src", "textos.ts"))
        self.assertIn("const ES: Textos", textos)
        for mala in ("pecho", "ingeniera", "ingeniera", "contacto"):
            self.assertNotIn(mala, textos.lower())

    def test_whisper_escucha_en_el_idioma_del_guion(self):
        self.assertNotIn('language="en"', leer("palabras.py"))


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    ok = r.wasSuccessful()
    print(("OK" if ok else "FALLO"), f"test_video_polaris: {r.testsRun} comprobaciones")
    sys.exit(0 if ok else 1)
