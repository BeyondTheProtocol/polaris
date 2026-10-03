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


class TestVozV4(unittest.TestCase):
    """29-sep-2026: la voz pasa a Eleven v4 en inglés americano ({{TITULAR}}: «me parece espectacular de verdad»)."""

    def test_drop_cae_en_polaris_aunque_la_voz_llegue_tarde(self):
        # con la v4, «Polaris» llega después del drop del tema (EN +0,68 s, ES +3,43 s): antes el inicio negativo se
        # recortaba a 0 y el drop se adelantaba; ahora se repiten compases de la intro y hay música desde el segundo 0
        sys.path.insert(0, DIR)
        from montaje import piezas_intro
        compas = 4 * 0.5222
        for w_pol, drop in ((15.901, 16.28), (16.96, 16.28), (19.71, 16.28)):
            ps = piezas_intro(w_pol, drop, compas)
            self.assertAlmostEqual(ps[0][0], 0.0, msg="la música suena desde el principio")
            for v0, m0, d in ps:
                self.assertGreaterEqual(m0, 0, "la música no puede empezar antes del principio del tema")
            self.assertAlmostEqual(sum(d for _, _, d in ps), w_pol, places=6)
            v0, m0, d = ps[-1]
            self.assertAlmostEqual(v0 + (drop - m0), w_pol, places=6, msg="el drop del tema tiene que caer en «Polaris»")
            if len(ps) == 2:  # el salto de la repetición cae en compás entero del tema
                fin = ps[0][1] + ps[0][2]
                self.assertAlmostEqual(fin / compas, round(fin / compas), places=6)

    def test_v4_con_acento_americano_y_sin_style(self):
        g = json.loads(leer("guion_voz.json"))
        if g["model"] != "eleven_v4":
            self.skipTest("el guion EN no está en v4")
        self.assertEqual(g.get("language_code"), "en")
        self.assertIn("American accent", g.get("prefijo", ""), "sin etiqueta su clon en v4 sale británico")
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import elevenlabs_voz
        enviado = {}
        viejo_req, viejo_key = elevenlabs_voz._req, elevenlabs_voz._key
        elevenlabs_voz._req = lambda url, key, data=None, accept="": (enviado.update(data or {}), (b"x", ""))[1]
        elevenlabs_voz._key = lambda: "k"
        try:
            import tempfile
            with tempfile.TemporaryDirectory() as d:
                elevenlabs_voz.speak(g["prefijo"] + "Hi.", "v", "eleven_v4", os.path.join(d, "a.mp3"), style=0.2,
                                     language_code="en")
        finally:
            elevenlabs_voz._req, elevenlabs_voz._key = viejo_req, viejo_key
        self.assertEqual(enviado["language_code"], "en")
        self.assertNotIn("style", enviado["voice_settings"], "v4 no admite style (can_use_style=False)")
        self.assertTrue(enviado["text"].startswith("[General American accent] "))

    def test_decir_solo_lo_oye_la_voz(self):
        # ES v2: la v4 decía «Convertió» y se comía la D de «3D»; «decir» cambia la grafía para la voz, nunca la pantalla
        self.assertIn('f.get("decir", f["texto"])', leer("voz.py"))
        for n in ("palabras.py", "montaje.py", "sfx.py"):
            self.assertNotIn("decir", leer(n), f"{n} no puede usar «decir»: en pantalla va el texto firmado")
        for ruta in ("guion_voz.json", "guion_voz_es.json"):
            for f in json.loads(leer(ruta))["frases"]:
                if "decir" in f:
                    self.assertNotEqual(f["decir"], f["texto"])

    def _bloque(self, idioma):
        s = leer(os.path.join("remotion", "src", "textos.ts"))
        return s[s.index(f"const {idioma.upper()}: Textos = {{"):].split("\n};", 1)[0]

    def test_cadena_el_texto_cabe_en_su_caja(self):
        # ES v2 (29-sep, {{TITULAR}}): «Pruebas y mensajes» se salía de la caja. Fraunces 600 ≈ 0,53 em por carácter (medido por diseño)
        for idioma in ("en", "es"):
            b = self._bloque(idioma)
            geo = re.search(r"cadena: \{xs: \[([^\]]+)\], w: \[(\d+), (\d+)\], letra: \[(\d+), (\d+)\]\}", b)
            self.assertIsNotNone(geo, f"{idioma}: falta la geometría de la cadena en textos.ts")
            wg, wb, lg, lb = map(int, geo.groups()[1:])
            nodos = re.findall(r"\['[^']*', '([^']*)'\]", re.search(r"nodos: \[(.*?)\]\],", b).group(1) + "]")
            salidas = re.findall(r"\['[^']*', '([^']*)'\]", re.search(r"salida: \[(.*?)\]\],", b).group(1) + "]")
            for v in nodos[:4]:
                self.assertLessEqual(len(v) * lg * 0.53, wg - 20, f"{idioma}: «{v}» se sale de la caja grande")
                self.assertLessEqual(len(v) * lb * 0.53, wb - 20, f"{idioma}: «{v}» se sale de la caja en barra")
            for v in [nodos[4]] + salidas:  # la caja de salida mide 300 en los dos estados
                self.assertLessEqual(len(v) * lb * 0.53, 300 - 20, f"{idioma}: «{v}» se sale de la caja de salida")
            self.assertLessEqual(len(nodos[4]) * lg * 0.53, 300 - 20)

    def test_capturas_y_urls_en_su_idioma(self):
        # ES v2 (29-sep, {{TITULAR}}): «las capturas de la web están en inglés, debería salir la versión en español»
        es = self._bloque("es")
        self.assertIn("dir: 'es/'", es)
        for ruta in ("helptitular.com/ciencia", "helptitular.com/datos"):
            self.assertIn(ruta, es)
        self.assertNotIn("helptitular.com/science", es)
        cap = leer("capturar.mjs")
        es_map = cap[cap.index("const ES = {"):cap.index("const TOMAS = ")]
        self.assertNotIn("/en", es_map, "las capturas ES no pueden venir de /en/")
        tsx = leer(os.path.join("remotion", "src", "Polaris.tsx"))
        for img in ("science.png", "biopsia.png", "esqueleto.png", "dos-caras.png"):
            self.assertNotIn(f"staticFile('{img}')", tsx, f"{img} tiene que pasar por captura() para salir en su idioma")
        sys.path.insert(0, DIR)
        from preparar import RECORTES, RECORTES_ES
        self.assertIn("datos-cielo", RECORTES_ES)
        self.assertEqual(RECORTES["esqueleto"], (373, 251, 691, 864), "sin el margen de 4 px vuelven las esquinas claras")

    def test_revision_distingue_ned_deletreado(self):
        # una toma de la v19 dijo «Ned» de corrido: el revisor tiene que verlo, no darlo por igual a «N-E-D»
        sys.path.insert(0, DIR)
        from revisa_tomas import plano
        self.assertNotEqual(plano("We're still going for N-E-D."), plano("We're still going for NED."))


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    ok = r.wasSuccessful()
    print(("OK" if ok else "FALLO"), f"test_video_polaris: {r.testsRun} comprobaciones")
    sys.exit(0 if ok else 1)
