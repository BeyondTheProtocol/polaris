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
        mp4s = sorted(glob.glob(os.path.join(carpeta, "polaris-story-v*-remotion-1920x1080-BORRADOR.mp4")))
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


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    ok = r.wasSuccessful()
    print(("OK" if ok else "FALLO"), f"test_video_polaris: {r.testsRun} comprobaciones")
    sys.exit(0 if ok else 1)
