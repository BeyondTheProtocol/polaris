#!/usr/bin/env python3
"""tests/test_laminillas_1bis.py — la hoja del paso 1-bis (`laminillas_exporta.hoja_1bis`, la lanza
`procesa laminillas_qc -- hoja-1bis`) y la orden que lleva para Terminal.app (`comando_1bis`).

TODO SINTÉTICO, sobre el árbol copiado y la casa base falsa de test_laminillas_n1 (overlays,
cadena del borde y N1 temporales; ningún aviso real). Dos láminas inventadas con un rótulo «QZ7K»
pintado en L0 (sale en todos los niveles): P-KI67, con un TIFF que exporta_n1 acepta, y P-RP, con
un tag 297 fuera del formato (lo que tenían las láminas reales del Grundium). Y dos sin texto, con
algo pintado SOLO en L0 (deuda puerta-n1-1bis-no-ensena-lo-de-l0, 2-oct-26): P-RA, una mancha que
salta en unas pocas ventanas de la coherencia, y P-HER2, media lámina repintada (más ventanas de
las que se revisan a mano). La revisión r5 real (`laminillas_ingesta.cristal`, con tesseract) deja
las dos primeras con n1_apta = no y las otras dos con n1_apta = sí pero con el TIFF pidiendo el
paso 1-bis por la coherencia, y entonces:
  · la hoja escribe el PDF en SESION/revision/, con una página por lámina (en el orden pedido) y
    las instrucciones; los recortes van al nivel donde se leyó, ×4, con confianza ≥60; P-RP lleva
    «TODAVÍA NO»; el texto del PDF (pdftotext) lleva cada orden tal cual y NUNCA lo leído;
  · la orden de P-KI67, tecleada en zsh (tilde incluida) en un pseudo-terminal huérfano, como
    test_laminillas_n1: con otra palabra no sella nada; con VISTO-N1 exporta y
    `borde.revisar_cristal_en_tty` sella `n1_revision_humana` con `via: 'tty'` y el sha256 de la
    copia N1, el que acepta `puerta_n1.revision_humana`;
  · la orden de P-RP se para antes de preguntar (lo que la hoja anuncia con «TODAVÍA NO»);
  · P-RA entra en la hoja aunque n1_apta sea sí: cada ventana que saltó, con su par de recortes
    (L0 y el mismo campo del ×8), su diferencia y su umbral; su orden lleva `--hoja <huella>` y con
    VISTO-N1 exporta y sella. Con la orden de una hoja que enseñaba una ventana menos, exporta_n1
    se para ANTES de preguntar;
  · P-HER2: «TODAVÍA NO: demasiadas ventanas para revisarlas a mano» y ninguna orden; tecleando la
    orden a mano, exporta_n1 tampoco pregunta.
Que el doble fork esquive la comprobación de ancestros de borde es su límite declarado (cabecera
de PREFIJOS_TTY); aquí solo sirve para simular la terminal de {{TITULAR}} sobre datos inventados.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import test_laminillas_n1 as N  # noqa: E402 — sale en ROJO si faltan PIL, tesseract o libturbojpeg

VENV_PAT = os.path.expanduser("~/.polaris-venvs/patologia/bin/python")
PDFTOTEXT = shutil.which("pdftotext") or "/opt/homebrew/bin/pdftotext"
PDFINFO = shutil.which("pdfinfo") or "/opt/homebrew/bin/pdfinfo"
ZSH = "/bin/zsh"
PALABRA = "QZ7K"

FIXTURE = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])
d, py = sys.argv[3], sys.argv[4]
os.environ["BTP_VENTANILLA"] = "1"
import exporta_n1 as E
E.puerta.exigir_diccionario()
import numpy as np
from PIL import ImageDraw
import laminillas_comun as C, laminillas_ingesta as I, laminillas_exporta as X
from test_laminillas_n1 import escribe_tiff, _piramide_coherente, _textura
os.makedirs(os.path.join(d, "mascaras")); os.makedirs(os.path.join(d, "revision"))
niv = _piramide_coherente(_textura(4096, 2048, 5), texto_l0="QZ7K", alto_texto=320)
escribe_tiff(os.path.join(d, "P-KI67.tif"), niv)
escribe_tiff(os.path.join(d, "P-RP.tif"), niv, tags_l0={297: (3, [2, 2])})   # página = total
def solo_en_l0(caja, color):                 # pintado en L0 DESPUÉS de sacar el ×4 y el ×8
    n = _piramide_coherente(_textura(4096, 1024, 7))
    l0 = n[0][2].copy()
    ImageDraw.Draw(l0).rectangle(caja, fill=color)
    return [(4096, 1024, l0)] + n[1:]
NIV = {"P-KI67": niv, "P-RP": niv, "P-RA": solo_en_l0((1600, 400, 1800, 560), (40, 30, 60)),
       "P-HER2": solo_en_l0((2048, 0, 4095, 1023), (60, 40, 90))}
for op in ("P-RA", "P-HER2"):
    escribe_tiff(os.path.join(d, op + ".tif"), NIV[op])
laminas = {"P-RE": {"fichero": "P-RE.tif", "mpp": 0.2506, "n1_apta": True}}
for op, n in NIV.items():
    m = np.ones((64, 128), bool)
    np.savez(os.path.join(d, "mascaras", op + "-8um.npz"), mascara=m, dentro=np.ones_like(m), escala_l0=32.0)
    n[0][2].resize((1024, n[0][1] // 4)).save(os.path.join(d, "revision", op + "__thumbnail.png"))
    n[2][2].save(os.path.join(d, "revision", op + "__x8.png"))
    laminas[op] = {"fichero": op + ".tif", "mpp": 0.2506, "n1_apta": True, "revision": {
        "miniatura": {"fichero": "revision/%s__thumbnail.png" % op, "mpp": 0.2506 * 4},
        "campo_x8": {"fichero": "revision/%s__x8.png" % op, "mpp": 0.2506 * 8, "x_l0": 0, "y_l0": 0}}}
C.escribe_json(C.ruta_manifiesto(d), {"version": 1, "laminas": laminas})
for op in NIV:
    I.cristal(d, op)
# Una hoja CADUCADA de P-RA: la r5 sin su primera ventana (la Puerta verá una que esa hoja no enseña).
coh_ra = C.lee_manifiesto(d)["laminas"]["P-RA"]["cristal"]["coherencia"]
def pon_coh(valor):
    def fn(m):
        m["laminas"]["P-RA"]["cristal"]["coherencia"] = valor
    return fn
caducada = json.loads(json.dumps(coh_ra))
caducada["IFD0_x1"]["ventanas"] = caducada["IFD0_x1"]["ventanas"][1:]
caducada["IFD0_x1"]["n_ventanas"] -= 1
C.actualiza_manifiesto(d, pon_coh(caducada))
f_cad = X.hoja_1bis(d, ["P-RA"], tools=sys.argv[1], py=py)["laminas"][0]
C.actualiza_manifiesto(d, pon_coh(coh_ra))
def sha_cristal(sha):
    def fn(m):
        viejo = m["laminas"]["P-KI67"]["cristal"]["exporta_n1_sha256"]
        m["laminas"]["P-KI67"]["cristal"]["exporta_n1_sha256"] = sha or viejo[::-1]
        return viejo
    return fn
real = C.lee_manifiesto(d)["laminas"]["P-KI67"]["cristal"]["exporta_n1_sha256"]
C.actualiza_manifiesto(d, sha_cristal(None))          # revisión hecha con OTRO exporta_n1
otra = X.hoja_1bis(d, ["P-KI67"], tools=sys.argv[1], py=py)["laminas"][0]["todavia_no"]
C.actualiza_manifiesto(d, sha_cristal(real))
h = X.hoja_1bis(d, ["P-RE", "P-KI67", "P-RP", "P-RA", "P-HER2"], tools=sys.argv[1], py=py)
man = C.lee_manifiesto(d)["laminas"]
# Paginación de las instrucciones con 30 láminas (bloques de 3 líneas, como P-RP): solo esa página.
rp = [f for f in h["laminas"] if f["op"] == "P-RP"][0]
muchas = [dict(rp, op="P-X%d" % i, comando=rp["comando"].replace("P-RP", "P-X%d" % i)) for i in range(30)]
pdf_muchas = os.path.join(d, "revision", "muchas.pdf")
pag_muchas = X._escribe_pdf(X._paginas_1bis(muchas)[-1:], pdf_muchas)
print(json.dumps({
    "n1_apta": {op: man[op]["n1_apta"] for op in NIV},
    "caducada": [f_cad["comando"], len(f_cad["pares"]), f_cad["todavia_no"]],
    "her2_a_mano": X.comando_1bis("P-HER2", "P-HER2.tif", d, tools=sys.argv[1], py=py, huella="0" * 16),
    "coherencia_r5": {op: {k: [c.get("n_ventanas"), c.get("diferencia")] for k, c in
                           man[op]["cristal"]["coherencia"].items()} for op in NIV},
    "motivos_tiff": {op: man[op]["cristal"]["motivos_tiff"] for op in NIV},
    "revision_dir": sorted(os.listdir(os.path.join(d, "revision"))),
    "pdf": h["pdf"], "paginas": h["paginas"], "omitidas": h["omitidas"],
    "otra_version": otra, "pdf_muchas": pdf_muchas, "pag_muchas": pag_muchas,
    "rehusa_r5": X.llega_a_preguntar(d, {"fichero": "P-KI67.tif", "cristal": {"tiff_exporta_n1_rehusa": ["x"]}}),
    "x4_por_teselas": hasattr(E, "lecturas_ocr_nivel"),
    "muchas": [[f["op"], f["comando"]] for f in muchas],
    "cajas_l0": [[f, [round(v * 1000 / 0.2506) for v in p["caja_mm"]], p["confianza"]]
                 for f, dd in man["P-KI67"]["cristal"]["fuentes"].items() for p in dd["palabras"]],
    "laminas": [{"op": f["op"], "sitios": f["sitios"], "lecturas": f["lecturas"], "todavia_no": f["todavia_no"],
                 "no_pregunta_hoy": f["no_pregunta_hoy"], "vigente": f["vigente"], "comando": f["comando"],
                 "ventanas": f["ventanas"], "demasiadas": f["demasiadas"], "huella": f["huella"],
                 "pares": [[p["n"], p["nivel"], p["dif"], list(p["fino"].size), list(p["x8"].size), p["pie"]]
                           for p in f["pares"]],
                 "recortes": [[r["sitio"], r["nivel"], r["confianzas"], r["factor"]] for r in f["recortes"]]}
                for f in h["laminas"]],
    "por_defecto": X.comando_1bis("P-KI67", "P-KI67.tif", d),
    "pide": X.PIDE_VISTO, "redirige": X.main(["hoja-1bis"]),
    "manifiesto_sin_palabra": "QZ7K" not in open(C.ruta_manifiesto(d)).read()}))
"""


@unittest.skipUnless(os.path.exists(VENV_PAT), "sin venv patologia")
class Hoja1bis(N.Base):

    def _fixture(self, ses):
        env = dict(self.env, HOME=self.tmp, MPLCONFIGDIR=os.path.join(self.tmp, "mpl"))
        r = subprocess.run([VENV_PAT, "-c", FIXTURE, self.copia, AQUI, ses, VENV_PAT], capture_output=True,
                           text=True, timeout=1200, env=env, cwd=self.tmp, stdin=subprocess.DEVNULL,
                           start_new_session=True)
        self.assertEqual(r.returncode, 0, r.stdout[-500:] + r.stderr[-3000:])
        return json.loads(r.stdout.strip().splitlines()[-1])

    def _teclea(self, comando, palabra, pide):
        """La orden tal cual la teclea {{TITULAR}}: zsh la interpreta (tilde, comillas) en su terminal."""
        return N.con_tty_huerfano([ZSH, "-f", "-c", comando], dict(self.env, HOME=self.tmp), self.tmp,
                                  palabra, pide.encode(), espera=900)

    def test_hoja_y_orden_en_terminal(self):
        ses = os.path.join(self.tmp, "ses1bis")
        o = self._fixture(ses)
        self.assertEqual(o["n1_apta"], {"P-KI67": False, "P-RP": False, "P-RA": True, "P-HER2": True}, o)
        self.assertTrue(o["manifiesto_sin_palabra"])
        self.assertEqual(o["redirige"], 2)                   # por laminillas_exporta, no
        self.assertEqual(o["omitidas"], [["P-RE", "n1_apta True"]])
        self.assertEqual([f["op"] for f in o["laminas"]], ["P-KI67", "P-RP", "P-RA", "P-HER2"])  # el orden pedido
        ki, rp, ra, her2 = o["laminas"]
        self.assertTrue(ki["vigente"] and rp["vigente"])
        self.assertIsNone(ki["no_pregunta_hoy"])
        self.assertEqual(ki["todavia_no"], [])
        self.assertEqual(ki["ventanas"], 0)
        # P-RA: solo la coherencia (n1_apta = sí), pocas ventanas, todas en L0 y sobre el umbral, cada
        # una con su par de recortes legible (L0 a ×1 y el ×8 ampliado al mismo lado).
        self.assertTrue(all("reducido no se parece al ×8" in m for m in o["motivos_tiff"]["P-RA"]), o["motivos_tiff"])
        n_ra = o["coherencia_r5"]["P-RA"]["IFD0_x1"][0]
        self.assertTrue(1 <= n_ra <= 24, o["coherencia_r5"])
        self.assertEqual((ra["ventanas"], len(ra["pares"]), ra["todavia_no"], ra["demasiadas"]), (n_ra, n_ra, [], False))
        for n, nivel, dif, t_fino, t_x8, pie in ra["pares"]:
            self.assertEqual(nivel, "L0")
            self.assertGreater(dif, 24)
            self.assertGreaterEqual(min(t_fino + t_x8), 256)                       # a escala legible
            self.assertIn("diferencia %d (umbral 24)" % dif, pie)
        self.assertRegex(ra["comando"], r" --revisado-en-tty --hoja [0-9a-f]{16}$")
        self.assertTrue(ra["comando"].endswith(ra["huella"]))
        # P-HER2: más ventanas que el tope: TODAVÍA NO, sin recortes y sin orden.
        self.assertGreater(o["coherencia_r5"]["P-HER2"]["IFD0_x1"][0], 24)
        self.assertTrue(her2["demasiadas"])
        self.assertIsNone(her2["comando"])
        self.assertEqual(her2["pares"], [])
        self.assertTrue(any(m.startswith("demasiadas ventanas para revisarlas a mano") for m in her2["todavia_no"]))
        # La hoja CADUCADA (una ventana menos) lleva otra huella.
        cmd_cad, pares_cad, todavia_cad = o["caducada"]
        self.assertEqual((pares_cad, todavia_cad), (n_ra - 1, []))
        self.assertNotEqual(cmd_cad, ra["comando"])
        self.assertIn("tag 297", rp["no_pregunta_hoy"])
        self.assertEqual(rp["todavia_no"], [rp["no_pregunta_hoy"]])
        # El ×4 «rehusado» que anota la r5 por tamaño solo cuenta si exporta_n1 no lo lee por teselas.
        self.assertEqual(o["rehusa_r5"] is None, o["x4_por_teselas"], o["rehusa_r5"])
        self.assertEqual(len(o["otra_version"]), 1)                                 # recuadros de otro exporta_n1
        self.assertIn("otra versión de exporta_n1", o["otra_version"][0])
        self.assertGreaterEqual(ki["sitios"], 1)
        # El rótulo (pegado en L0 en x≈700-2000, y≈300-630) cae donde está: cajas de L0 bien llevadas.
        en_rotulo = [c for c in o["cajas_l0"] if 600 <= c[1][0] and c[1][2] <= 2600 and 200 <= c[1][1]
                     and c[1][3] <= 750]
        self.assertTrue(en_rotulo, o["cajas_l0"])
        # Un recorte por sitio y nivel donde se leyó, con su confianza (≥60) y ×4 si cabe.
        conocidos = {"×4", "×8", "PNG miniatura", "PNG campo ×8"}
        self.assertTrue({r[1] for r in ki["recortes"]} <= conocidos, ki["recortes"])
        self.assertEqual({r[0] for r in ki["recortes"]}, set(range(1, ki["sitios"] + 1)))
        self.assertEqual(sorted(c for r in ki["recortes"] for c in r[2]),
                         sorted(c[2] for c in o["cajas_l0"]))                       # cada lectura, una vez
        self.assertTrue(all(c >= 60 for r in ki["recortes"] for c in r[2]), ki["recortes"])
        self.assertIn(4, [r[3] for r in ki["recortes"]])
        # La orden: intérprete del venv, exporta_n1 del árbol, la copia de SESION con tilde y la
        # huella de lo que enseña la hoja.
        self.assertEqual(ki["comando"], "%s ~/arbol/tools/exporta_n1.py tiff ~/ses1bis/P-KI67.tif --opaco P-KI67 "
                         "--revisado-en-tty --hoja %s" % (VENV_PAT, ki["huella"]))
        self.assertTrue(o["por_defecto"].startswith("~/.polaris-venvs/patologia/bin/python ~/arbol/tools/"
                                                    "exporta_n1.py tiff "), o["por_defecto"])
        # El PDF: en SESION, páginas contadas = las de pdfinfo, cada orden tal cual y nada leído.
        pdf = o["pdf"]
        self.assertEqual(pdf, os.path.join(ses, "revision", "hoja-1bis.pdf"))
        self.assertEqual(os.stat(pdf).st_mode & 0o777, 0o600)
        self.assertGreaterEqual(o["paginas"], 6)          # 4 láminas, la coherencia de P-RA e instrucciones
        # Los recortes son imágenes de la lámina: solo en SESION (el PDF), nada nuevo en revision/.
        self.assertEqual(o["revision_dir"], sorted(["hoja-1bis.pdf", "muchas.pdf"] + [
            "%s__%s.png" % (op, n) for op in ("P-KI67", "P-RP", "P-RA", "P-HER2") for n in ("thumbnail", "x8")]))
        if os.path.exists(PDFTOTEXT) and os.path.exists(PDFINFO):
            info = subprocess.run([PDFINFO, pdf], capture_output=True, text=True).stdout
            self.assertRegex(info, r"(?m)^Pages:\s+%d$" % o["paginas"])
            texto = subprocess.run([PDFTOTEXT, "-raw", pdf, "-"], capture_output=True, text=True).stdout
            self.assertNotIn(PALABRA, texto)
            self.assertIn(o["pide"], texto)
            i_ki, i_rp = texto.find(ki["comando"]), texto.find(rp["comando"])
            self.assertTrue(0 <= i_ki < i_rp, (ki["comando"], texto[-1500:]))
            self.assertIn("TODAVÍA NO", texto[texto.find("2. P-RP"):i_rp])
            self.assertNotIn("TODAVÍA NO", texto[texto.find("1. P-KI67"):i_ki])
            i_ra = texto.find(ra["comando"])
            self.assertTrue(i_rp < i_ra, ra["comando"])
            self.assertNotIn("TODAVÍA NO", texto[texto.find("3. P-RA"):i_ra])
            self.assertIn("P-RA · paso 1-bis · Coherencia", texto)
            for n, _niv, dif, _f, _x, _pie in ra["pares"]:
                self.assertIn("Ventana %d de %d · L0 frente al ×8 · diferencia %d (umbral 24)" % (n, n_ra, dif),
                              texto)
            self.assertIn("TODAVÍA NO: demasiadas ventanas para revisarlas a mano", texto[texto.find("4. P-HER2"):])
            self.assertNotIn("--opaco P-HER2", texto)                               # sin orden
            self.assertNotIn("P-HER2 · paso 1-bis · Coherencia", texto)
            # Ninguna página separa el nombre de una lámina de su orden.
            paginas = subprocess.run([PDFTOTEXT, "-raw", o["pdf_muchas"], "-"], capture_output=True,
                                     text=True).stdout.split("\f")
            self.assertGreaterEqual(o["pag_muchas"], 2)
            for i, (op, cmd) in enumerate(o["muchas"], 1):
                con = [p for p in paginas if cmd in p]
                self.assertEqual(len(con), 1, op)
                self.assertIn("%d. %s ·" % (i, op), con[0])
        # Con otra palabra: cancelado, sin copia N1 ni sello.
        rc, out = self._teclea(ki["comando"], "NO-LO-VEO", o["pide"])
        self.assertEqual(rc, 3, out[-2000:])
        self.assertIn("palabra distinta", out)
        self.assertFalse(os.path.exists(os.path.join(self.n1, "P-KI67.n1.tif")))
        self.assertFalse(self.eventos("n1_revision_humana"))
        self.assertEqual([e["motivo"] for e in self.eventos("n1_revision_rechazada")], ["palabra distinta"])
        # Con VISTO-N1: exporta y sella vía tty, atado al sha256 de la copia N1.
        rc, out = self._teclea(ki["comando"], "VISTO-N1", o["pide"])
        self.assertEqual(rc, 0, out[-2000:])
        self.assertIn(o["pide"], out)
        dst = os.path.join(self.n1, "P-KI67.n1.tif")
        with open(dst, "rb") as fh:
            sha = hashlib.sha256(fh.read()).hexdigest()
        rev = self.eventos("n1_revision_humana")
        self.assertEqual([(e["via"], e["sha256"], e["opaco"]) for e in rev], [("tty", sha, "P-KI67")])
        self.assertIs(self.codigo("import puerta_n1 as pu\nprint(json.dumps(pu.revision_humana(%r)))" % sha), True)
        with open(os.path.join(self.n1, "manifiesto.json")) as fh:
            self.assertEqual(json.load(fh)["ficheros"]["P-KI67.n1.tif"]["cristal"], "revisado-en-tty")
        # La de P-RP se para antes de preguntar: «TODAVÍA NO» dice la verdad.
        rc, out = self._teclea(rp["comando"], "VISTO-N1", o["pide"])
        self.assertEqual(rc, 3, out[-2000:])
        self.assertIn("tag 297", out)
        self.assertNotIn(o["pide"], out)
        self.assertFalse(os.path.exists(os.path.join(self.n1, "P-RP.n1.tif")))
        self.assertEqual(len(self.eventos("n1_revision_humana")), 1)
        # P-RA con la orden de la hoja CADUCADA: la Puerta ve una ventana que esa hoja no enseñó y
        # se para ANTES de preguntar (ni pregunta, ni sella, ni rechazo en la cadena).
        rc, out = self._teclea(cmd_cad, "VISTO-N1", o["pide"])
        self.assertEqual(rc, 3, out[-2000:])
        self.assertIn("no enseña", out)
        self.assertNotIn(o["pide"], out)
        self.assertFalse(os.path.exists(os.path.join(self.n1, "P-RA.n1.tif")))
        self.assertEqual((len(self.eventos("n1_revision_humana")), len(self.eventos("n1_revision_rechazada"))), (1, 1))
        # P-HER2, con una orden tecleada a mano: demasiadas ventanas, tampoco pregunta.
        rc, out = self._teclea(o["her2_a_mano"], "VISTO-N1", o["pide"])
        self.assertEqual(rc, 3, out[-2000:])
        self.assertIn("demasiadas para revisarlas a mano", out)
        self.assertNotIn(o["pide"], out)
        self.assertFalse(os.path.exists(os.path.join(self.n1, "P-HER2.n1.tif")))
        # P-RA con la orden de SU hoja: pregunta (y lista la coherencia), VISTO-N1 exporta y sella.
        rc, out = self._teclea(ra["comando"], "VISTO-N1", o["pide"])
        self.assertEqual(rc, 0, out[-2000:])
        self.assertIn(o["pide"], out)
        self.assertIn("reducido no se parece al ×8", out)
        with open(os.path.join(self.n1, "P-RA.n1.tif"), "rb") as fh:
            sha_ra = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual([(e["sha256"], e["opaco"]) for e in self.eventos("n1_revision_humana")],
                         [(sha, "P-KI67"), (sha_ra, "P-RA")])


class RehusaR5(unittest.TestCase):
    """La r5 solo anota el ×4 «rehusado por exporta_n1» si ESE exporta_n1 lo leería con el tope
    de Pillow; desde 239b28a lo lee por teselas y la marca quedaba mal en el manifiesto (2-oct)."""

    def test_marca_solo_si_exporta_n1_no_lee_por_teselas(self):
        sys.path.insert(0, os.path.join(os.path.dirname(AQUI), "tools"))
        import exporta_n1
        import laminillas_ingesta as I
        aviso = "Pillow DecompressionBombError: 478 Mpx > 178 Mpx"
        self.assertTrue(hasattr(exporta_n1, "lecturas_ocr_nivel"))
        self.assertFalse(I._rehusa_exporta(exporta_n1, aviso))
        self.assertTrue(I._rehusa_exporta(object(), aviso))
        self.assertFalse(I._rehusa_exporta(object(), None))


if __name__ == "__main__":
    unittest.main()
