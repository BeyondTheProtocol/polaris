#!/usr/bin/env python3
"""tests/test_laminillas_capas.py — `capas-piloto` (tools/laminillas_capas.py): el productor de
`~/Laminillas-N1/panel_vision/capas_piloto.json` (plan «laminillas DFCI», 5-bis y actualización 15).

TODO SINTÉTICO: una SESION y un N1 en un directorio temporal (`_laminillas_capas_mundo`), tejido
inventado por `panel_vision/sinteticos`, ninguna lámina real, ningún ~/Laminillas-N1 real.

Verifica el EFECTO:
  · las capas pasan los DOS consumidores del contrato: `siembra_n1._carga_capas` (y la siembra
    entera, `genera_conjunto_n1`, sobre las cinco tareas) y `calibra.revisa_capas`, y
    `laminillas_proc.tribunal_listo` las reconoce (bloquea sin revisiones; listo con ellas);
  · los colores, escalas y cajas de la PREGUNTA: ámbar por núcleo, magenta CK19, naranja de
    GrandQC, superposición que `siembra_n1.separa_registro` invierte con barra de 100 µm, figura
    con barra = escala_px y cajas en un solo cuadrante;
  · una capa que no pasa la Puerta NO entra en el JSON ni en el manifiesto; la cuota se completa
    con el campo siguiente;
  · la selección NO depende del DAB de la diana: no lee un píxel, y dos SESIONES que solo difieren
    en el DAB de P-KI67 (píxeles y objetos) dan los mismos campos; un MUTANTE que ordena por DAB de
    Ki67 hace fallar esa misma comprobación;
  · reanuda con la misma huella (sin exportar de nuevo) y abre ronda nueva si cambia un producto;
  · por la ventanilla: `laminillas_exporta capas-piloto` con la Puerta de N1 DE VERDAD (OCR,
    subproceso con casa base falsa): una capa con un apellido rotulado no entra y las demás se
    re-validan con `siembra_n1.PuertaN1` (revalidar_n1).

Corre con el intérprete del venv `patologia` (se re-ejecuta con él); sin venv, SKIP (rc 77).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

VENV_DIR = os.environ.get("BTP_VENV_PATOLOGIA") or os.path.expanduser("~/.polaris-venvs/patologia")
VENV_PY = os.path.join(VENV_DIR, "bin", "python")
if os.path.realpath(sys.prefix) != os.path.realpath(VENV_DIR):
    if os.path.exists(VENV_PY) and not os.environ.get("BTP_CAPAS_REEXEC"):
        os.environ["BTP_CAPAS_REEXEC"] = "1"
        if "unittest" in os.path.basename(sys.argv[0]):           # `python3 -m unittest tests.…`
            os.execv(VENV_PY, [VENV_PY, "-m", "unittest"] + sys.argv[1:])
        os.execv(VENV_PY, [VENV_PY, os.path.abspath(__file__)] + sys.argv[1:])
    print("SKIP: falta el venv patologia (%s)" % VENV_DIR)
    sys.exit(77)

import numpy as np  # noqa: E402

AQUI = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(AQUI)
TOOLS = os.path.join(ROOT, "tools")
PANEL = os.path.join(TOOLS, "panel_vision")
for _p in (AQUI, PANEL, TOOLS):
    sys.path.insert(0, _p)
os.environ.pop("BTP_VENTANILLA", None)          # en proceso, fuera de la ventanilla (sello libre)

import _laminillas_capas_mundo as W  # noqa: E402
import calibra as C  # noqa: E402
import laminillas_capas as M  # noqa: E402
import laminillas_proc as P  # noqa: E402
import siembra_n1 as SN  # noqa: E402
import sinteticos as S  # noqa: E402

# Geometría y cuotas de prueba (la de producción, en M.GEOMETRIA y M.CUOTAS): mismas reglas, campos
# más pequeños para que quepan en láminas sintéticas de 0,8-1,5 mm.
GEO = {"lado_vista": 512, "lado_registro": 256, "factor_registro": 8, "lado_figura": 512, "panel_figura": 256}
CUOTAS = {"nucleos": 3, "ck19": 3, "artefacto": 3, "figura": 4, "registro": 3}
# exporta_n1.RE_NOMBRE_PNG, copiada (importar exporta_n1 en proceso fija el estado de la Puerta)
RE_NOMBRE_PNG = re.compile(r"^([A-Z][A-Z0-9]{0,3}(?:-[A-Z0-9]{1,12}){0,3})__(?:(?:L0|x4|x8)__mpp\d{1,3}\.\d{4}"
                           r"|thumbnail)\.png$")
CAJAS = ("etiqueta", "escala", "leyenda")

_MUNDO = {}


def mundo():
    if not _MUNDO:
        he, ck = W.mundo_tejido()
        _MUNDO.update(he=he, ck=ck, A=W.laminas(he, ck, "A"), B=W.laminas(he, ck, "B"))
    return _MUNDO


class RechazoFalso(RuntimeError):
    pass


class ExportadorFalso:
    """Hace de exporta_n1 + puerta_n1 en proceso: escribe el PNG y su sha256 en el manifiesto de un
    N1 temporal (como `_escribe_n1`, sin sobrescribir) y rechaza las llamadas de `rechaza`."""

    def __init__(self, n1, rechaza=()):
        self.n1, self.rechaza, self.llamadas, self.json_revisados = n1, set(rechaza), 0, 0

    def version(self):
        return "exportador-falso/1"

    def n1_dir(self):
        return self.n1

    def manifiesto(self):
        try:
            with open(os.path.join(self.n1, "manifiesto.json")) as fh:
                return json.load(fh)["ficheros"]
        except OSError:
            return {}

    def canonico(self, rgb):
        return S.png_bytes(rgb)

    def exporta(self, datos, opaco, nivel, mpp):
        self.llamadas += 1
        if self.llamadas in self.rechaza:
            raise RechazoFalso("texto en la imagen (falso)")
        nombre = "%s__%s__mpp%.4f.png" % (opaco, nivel, mpp)
        assert RE_NOMBRE_PNG.match(nombre), nombre
        man = self.manifiesto()
        assert nombre not in man and not os.path.exists(os.path.join(self.n1, nombre)), nombre
        os.makedirs(self.n1, exist_ok=True)
        with open(os.path.join(self.n1, nombre), "wb") as fh:
            fh.write(datos)
        man[nombre] = {"sha256": C._sha(datos), "tipo": "png", "nivel": nivel, "mpp": mpp, "bytes": len(datos)}
        with open(os.path.join(self.n1, "manifiesto.json"), "w") as fh:
            json.dump({"version": 1, "ficheros": man}, fh)
        return nombre, man[nombre]["sha256"]

    def es_rechazo(self, e):
        return isinstance(e, RechazoFalso)

    def lee(self, nombre):
        from PIL import Image
        with Image.open(os.path.join(self.n1, nombre)) as im:
            return np.array(im.convert("RGB"))

    def revisa_json(self, texto):
        json.loads(texto)
        self.json_revisados += 1


class PuertaFalsa:
    """La Puerta de siembra_n1 en proceso (la de verdad, en el subproceso de la ventanilla)."""
    nombre = "falsa (test)"

    def fuente(self, ruta, sha256):
        with open(ruta, "rb") as fh:
            datos = fh.read()
        if C._sha(datos) != sha256:
            raise RuntimeError("sha256 distinto del manifiesto")
        return datos

    def imagen(self, rgb):
        return S.png_bytes(rgb)

    def texto(self, texto, donde):
        pass


class Oraculo:
    """Responde con la verdad de los conjuntos que conoce y «sin error» en lo demás (las capas)."""
    local = True

    def __init__(self, *dirs, nombre="oraculo:capas"):
        self.verdad, self.nombre = {}, nombre
        for d in dirs:
            with open(os.path.join(d, "verdad.json")) as fh:
                self.verdad.update(json.load(fh))

    def comprueba(self):
        return {"adaptador": "oraculo", "para": "test_laminillas_capas"}

    def responder(self, prompt, ruta_png, esquema):
        v = self.verdad.get(os.path.basename(ruta_png)[:-4])
        if v and v["hay_error"]:
            return json.dumps({"hay_error": True, "tipo": v["tipo"], "cuadrante": v["cuadrante"]})
        return json.dumps({"hay_error": False, "tipo": None, "cuadrante": None})


def _prepara(raiz, variante="A", **kw):
    m = mundo()
    ses, n1 = os.path.join(raiz, "SESION"), os.path.join(raiz, "Laminillas-N1")
    W.sesion(ses, m["he"], m["ck"], m[variante], **kw)
    return ses, n1, W.LectorFalso(m[variante])


def _clave_plan(plan):
    out = {}
    for t, campos in plan["plan"].items():
        out[t] = [(c.get("par"), c.get("x0"), c.get("y0"), c.get("ux0"), c.get("uy0")) for c in campos]
    return out


class _Minimo:
    def __init__(self, n):
        self.n = n

    def __enter__(self):
        self.viejo, M.MINIMO = M.MINIMO, self.n

    def __exit__(self, *a):
        M.MINIMO = self.viejo


class Contrato(unittest.TestCase):
    """Una corrida de `produce` sobre la SESION sintética: lo escrito cumple a los dos consumidores."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="lam-capas-")
        cls.ses, cls.n1, cls.lector = _prepara(cls.tmp)
        cls.exp = ExportadorFalso(cls.n1)
        cls.log = []
        with _Minimo(2):
            cls.r = M.produce(cls.ses, cls.exp, cls.lector, GEO, CUOTAS, log=cls.log.append)
        with open(os.path.join(cls.n1, C.N1_PANEL, C.CAPAS_PILOTO)) as fh:
            cls.doc = json.load(fh)
        with open(os.path.join(cls.n1, "manifiesto.json")) as fh:
            cls.man = json.load(fh)["ficheros"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _rgb(self, nombre):
        return self.exp.lee(nombre)

    def test_escribe_las_cinco_tareas_con_su_cuota(self):
        self.assertEqual(self.r["rc"], 0, "\n".join(self.log))
        self.assertEqual(set(self.doc["tareas"]), set(C.TAREAS))
        self.assertEqual(tuple(M.TAREAS), tuple(C.TAREAS))
        for t, fs in self.doc["tareas"].items():
            self.assertEqual(len(fs), CUOTAS[t], t)
            for f in fs:
                self.assertRegex(f, RE_NOMBRE_PNG)
                self.assertTrue(f.startswith("PV-%s-R1-" % M.CODIGO[t]), f)
                with open(os.path.join(self.n1, f), "rb") as fh:
                    self.assertEqual(C._sha(fh.read()), self.man[f]["sha256"])     # en el manifiesto con su sha
        self.assertFalse(os.path.exists(os.path.join(self.n1, C.N1_PANEL, M.PARCIAL)))
        self.assertEqual(self.doc["faltan"], {})
        self.assertEqual(sum(self.doc["rechazadas_puerta"].values()), 0)
        self.assertGreaterEqual(self.exp.json_revisados, 2)                    # el JSON, por la Puerta

    def test_pasa_carga_capas_de_la_siembra(self):
        capas = SN._carga_capas(self.n1, PuertaFalsa())
        self.assertEqual(set(capas), set(C.TAREAS))
        for t in SN.VISTA:
            for c in capas[t]:
                self.assertAlmostEqual(c["mpp"], SN.MPP_PREGUNTA)
                self.assertEqual(c["rgb"].shape, (GEO["lado_vista"],) * 2 + (3,))
        for c in capas["registro"]:
            self.assertEqual(set(c["contexto"]), set(C.CONTEXTO["registro"]))
            self.assertAlmostEqual(c["mpp"], W.MPP * 8)
        for c in capas["figura"]:
            self.assertEqual(set(c["contexto"]), set(C.CONTEXTO["figura"]))
            self.assertEqual(set(c["cajas"]), set(CAJAS))
            C.pregunta("figura", c["contexto"])                                 # la pregunta se puede hacer

    def test_la_siembra_entera_corre_sobre_estas_capas(self):
        d = os.path.join(self.n1, C.N1_PANEL, "conjunto_prueba")
        man = SN.genera_conjunto_n1(self.n1, d, n=2, lado=256, puerta=PuertaFalsa())
        self.assertEqual(len(man["casos"]), 2 * 2 * len(C.TAREAS))
        self.assertEqual(set(man["origen"]["capas"]), set(C.TAREAS))
        shutil.rmtree(d)

    def test_colores_de_la_pregunta(self):
        def hay(rgb, color, tol=0):
            return int((np.abs(rgb.astype(int) - np.array(color)) <= tol).all(axis=2).sum())
        for f in self.doc["tareas"]["nucleos"]:
            self.assertGreater(hay(self._rgb(f), S.COLOR_NUCLEOS), 500, f)
        for f in self.doc["tareas"]["ck19"]:
            self.assertGreater(hay(self._rgb(f), S.COLOR_CK19), 200, f)
        for f in self.doc["tareas"]["artefacto"]:                                # GrandQC marca 1 de cada 7
            self.assertGreater(hay(self._rgb(f), S.COLOR_QC), 100, f)
            self.assertIn("GrandQC marks", self.doc["origen"][f]["detector"])
        for f in self.doc["tareas"]["registro"]:
            rgb = self._rgb(f)
            mpp = self.doc["contexto"][f]["mpp"]
            barra = SN._cerca(rgb, S.COLOR_BARRA, 0)
            ys, xs = np.nonzero(barra)
            self.assertEqual(xs.max() - xs.min() + 1, int(round(100.0 / mpp)))      # 100 µm
            n = rgb.shape[0]
            self.assertGreater(ys.min(), n // 2)                                     # abajo
            self.assertLessEqual(abs((xs.min() + xs.max()) / 2.0 - n / 2.0), 1.0)    # en el centro
            wa, wb, res = SN.separa_registro(rgb)
            self.assertLess(np.percentile(res[~barra], 99), 3.0)                     # pinta_registro, invertible
            self.assertGreater((wa > 0.5).mean(), 0.05)
            self.assertGreater((wb > 0.5).mean(), 0.05)
        for f in self.doc["tareas"]["figura"]:
            rgb, ctx, cajas = self._rgb(f), self.doc["contexto"][f], self.doc["siembra"][f]["cajas"]
            H, Wd = rgb.shape[:2]
            qs = {SN.cuadrante_de_caja(*cajas[e], H, Wd) for e in CAJAS}
            self.assertNotIn(None, qs)
            self.assertEqual(len(qs), 3)                                              # cada elemento en su cuadrante
            x0, y0, x1, y1 = cajas["escala"]
            b = SN._cerca(rgb[y0:y1, x0:x1], S.COLOR_BARRA, 6)
            xs = np.nonzero(b)[1]
            self.assertEqual(xs.max() - xs.min() + 1, ctx["escala_px"])
            self.assertEqual(ctx["marcador"], M.MARCADOR_FIGURA)

    def test_registro_alinea_con_la_transformada(self):
        """Con la transformada del pipeline, B cae sobre A: el tejido de los dos casi coincide."""
        for f in self.doc["tareas"]["registro"]:
            wa, wb, _res = SN.separa_registro(self._rgb(f))
            ta, tb = wa > 0.3, wb > 0.3
            self.assertGreater((ta & tb).sum() / max(1, (ta | tb).sum()), 0.8, f)

    def test_figuras_recorren_los_cuadrantes(self):
        por = {e: set() for e in CAJAS}
        for f in self.doc["tareas"]["figura"]:
            rgb = self._rgb(f)
            for e, caja in self.doc["siembra"][f]["cajas"].items():
                por[e].add(SN.cuadrante_de_caja(*caja, *rgb.shape[:2]))
        self.assertTrue(all(len(v) == 4 for v in por.values()), por)

    def test_revisa_capas_y_tribunal_listo(self):
        """Calibración sintética y N1 (sembrada sobre ESTAS capas) con un oráculo: tribunal_listo
        bloquea hasta que el habilitado revisa las capas, y con las revisiones está listo."""
        d = tempfile.mkdtemp(dir=self.tmp)
        repo = os.path.join(d, "repo")
        os.makedirs(os.path.join(repo, "tools", "panel_vision"))
        shutil.copy(os.path.join(PANEL, "auditorias.json"), os.path.join(repo, P.AUDITORIAS))
        sint = os.path.join(d, "sint")
        conj = os.path.join(self.n1, C.N1_PANEL, "conjunto_tribunal")
        viejo = C._ahora
        try:
            C._ahora = lambda: "2026-10-02T10:00:00+00:00"
            C.genera_conjunto(sint, n=30, semilla=11, lado=256, tareas=("figura",))
            C.correr(sint, Oraculo(sint))
            C.exporta_calibracion(sint, os.path.join(repo, P.CALIB_SINTETICA))
            C._ahora = lambda: "2026-10-03T10:00:00+00:00"
            SN.genera_conjunto_n1(self.n1, conj, n=30, lado=256, tareas=("figura",), puerta=PuertaFalsa())
            C.correr(conj, Oraculo(conj))
            cal_n1 = os.path.join(self.n1, P.N1_PANEL, P.CALIB_N1)
            C.exporta_calibracion(conj, cal_n1)
            r = P.tribunal_listo(repo=repo, n1=self.n1, calibra=C, confia=lambda dd: False, log=None)
            self.assertTrue(r["i"]["n1_real"]["vale"], r["i"])
            self.assertFalse(r["listo"])
            self.assertEqual({x["fichero"] for x in r["ii"]["sin_revisar"]}, set(self.doc["tareas"]["figura"]))
            rev = C.revisa_capas(self.n1, cal_n1, Oraculo(conj))
            self.assertEqual((rev["tareas"], rev["nuevas"]), (["figura"], CUOTAS["figura"]))
            r = P.tribunal_listo(repo=repo, n1=self.n1, calibra=C, confia=lambda dd: False, log=None)
            self.assertTrue(r["listo"], r["faltan"])
        finally:
            C._ahora = viejo
            for x in (P.CALIB_N1, P.REVISIONES):
                try:
                    os.remove(os.path.join(self.n1, P.N1_PANEL, x))
                except OSError:
                    pass
            shutil.rmtree(conj, ignore_errors=True)

    def test_reanuda_sin_exportar_y_ronda_nueva_si_cambia_un_producto(self):
        exp = ExportadorFalso(self.n1)
        with _Minimo(2):
            r = M.produce(self.ses, exp, self.lector, GEO, CUOTAS, log=lambda m: None)
        self.assertEqual(exp.llamadas, 0)                                        # todo ya estaba en N1
        self.assertEqual(r["doc"]["tareas"], self.doc["tareas"])
        self.assertEqual(r["doc"]["generacion"], 1)
        d = tempfile.mkdtemp(dir=self.tmp)                                       # otra SESION: otra huella
        shutil.copytree(self.ses, os.path.join(d, "SESION"))
        ses2 = os.path.join(d, "SESION")
        ruta = os.path.join(ses2, "piloto", "regla_morfometrica.json")           # fuera de las entradas: igual
        with open(ruta, "w") as fh:
            fh.write("{}")
        with _Minimo(2):
            r = M.produce(ses2, ExportadorFalso(self.n1), self.lector, GEO, CUOTAS, log=lambda m: None)
        self.assertEqual(r["doc"]["generacion"], 1)
        z = os.path.join(ses2, "piloto", "objetos_P-CK19.json")                  # un producto de entrada cambia
        with open(z) as fh:
            o = json.load(fh)
        o["n"] += 0
        o["nota"] = "recalculado"
        with open(z, "w") as fh:
            json.dump(o, fh)
        with _Minimo(2):
            r = M.produce(ses2, ExportadorFalso(self.n1), self.lector, GEO, CUOTAS, log=lambda m: None)
        self.assertEqual(r["doc"]["generacion"], 2)
        self.assertTrue(all("-R2-" in f for fs in r["doc"]["tareas"].values() for f in fs))
        with open(os.path.join(self.n1, C.N1_PANEL, C.CAPAS_PILOTO), "w") as fh:  # deja la de la clase
            json.dump(self.doc, fh)


class PuertaRechaza(unittest.TestCase):
    def test_capa_rechazada_no_entra_y_la_cuota_se_completa(self):
        tmp = tempfile.mkdtemp(prefix="lam-capas-rechazo-")
        try:
            ses, n1, lector = _prepara(tmp)
            sano = ExportadorFalso(os.path.join(tmp, "n1-sano"))
            with _Minimo(2):
                M.produce(ses, sano, lector, GEO, CUOTAS, log=lambda m: None)
            exp = ExportadorFalso(n1, rechaza={2})                                # el 2.º campo de nucleos
            with _Minimo(2):
                r = M.produce(ses, exp, lector, GEO, CUOTAS, log=lambda m: None)
            doc = r["doc"]
            with open(os.path.join(n1, C.N1_PANEL, C.CAPAS_PILOTO)) as fh:
                self.assertEqual(json.load(fh)["tareas"], doc["tareas"])
            self.assertEqual(doc["rechazadas_puerta"]["nucleos"], 1)
            self.assertEqual(len(doc["tareas"]["nucleos"]), CUOTAS["nucleos"])
            with open(os.path.join(tmp, "n1-sano", C.N1_PANEL, C.CAPAS_PILOTO)) as fh:
                sano_doc = json.load(fh)
            campos = [tuple(doc["origen"][f]["campo_l0"]) for f in doc["tareas"]["nucleos"]]
            sano_campos = [tuple(sano_doc["origen"][f]["campo_l0"]) for f in sano_doc["tareas"]["nucleos"]]
            rechazado = sano_campos[1]
            self.assertNotIn(rechazado, campos)                                   # el campo rechazado, fuera
            self.assertEqual(campos[0], sano_campos[0])
            with open(os.path.join(n1, "manifiesto.json")) as fh:
                man = json.load(fh)["ficheros"]
            self.assertEqual(set(man), {f for fs in doc["tareas"].values() for f in fs})   # nada de más
            self.assertEqual(exp.llamadas, sum(len(v) for v in doc["tareas"].values()) + 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_capa_en_n1_con_otros_pixeles_no_se_reutiliza(self):
        tmp = tempfile.mkdtemp(prefix="lam-capas-ajena-")
        try:
            ses, n1, lector = _prepara(tmp)
            with _Minimo(2):
                r = M.produce(ses, ExportadorFalso(n1), lector, GEO, CUOTAS, log=lambda m: None)
            f = r["doc"]["tareas"]["ck19"][1]
            rgb = ExportadorFalso(n1).lee(f)
            rgb[:8, :8] = 0                                                       # otros píxeles, mismo nombre
            datos = S.png_bytes(rgb)
            with open(os.path.join(n1, f), "wb") as fh:
                fh.write(datos)
            with open(os.path.join(n1, "manifiesto.json")) as fh:
                man = json.load(fh)
            man["ficheros"][f]["sha256"] = C._sha(datos)
            with open(os.path.join(n1, "manifiesto.json"), "w") as fh:
                json.dump(man, fh)
            with self.assertRaises(M.CapaAjena):
                M.produce(ses, ExportadorFalso(n1), lector, GEO, CUOTAS, log=lambda m: None)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_subir_la_cuota_completa_la_misma_generacion(self):
        tmp = tempfile.mkdtemp(prefix="lam-capas-cuota-")
        try:
            ses, n1, lector = _prepara(tmp)
            with _Minimo(2):
                r1 = M.produce(ses, ExportadorFalso(n1), lector, GEO, dict(CUOTAS, ck19=2), log=lambda m: None)
            exp = ExportadorFalso(n1)
            r2 = M.produce(ses, exp, lector, GEO, CUOTAS, log=lambda m: None)            # y otro MINIMO
            self.assertEqual(r2["doc"]["generacion"], r1["doc"]["generacion"])
            self.assertEqual(r2["doc"]["tareas"]["ck19"][:2], r1["doc"]["tareas"]["ck19"])
            self.assertEqual(exp.llamadas, 1)                                              # solo la nueva
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_puerta_sistemica_para_la_orden(self):
        tmp = tempfile.mkdtemp(prefix="lam-capas-sistemica-")
        try:
            ses, n1, lector = _prepara(tmp)
            exp = ExportadorFalso(n1, rechaza=set(range(1, 100)))
            with self.assertRaises(M.PuertaSistemica):
                M.produce(ses, exp, lector, GEO, dict(CUOTAS, nucleos=30), log=lambda m: None)
            self.assertFalse(os.path.exists(os.path.join(n1, C.N1_PANEL, C.CAPAS_PILOTO)))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _planes(mutante=None):
    """Planes de las dos SESIONES (variantes A y B, que solo difieren en el DAB de P-KI67: píxeles y
    objetos) y las lecturas de píxeles que hizo cada planificación. `mutante` sustituye a
    `_orden_sellado` y recibe la P-KI67 de la variante que se planifica."""
    original = M._orden_sellado
    planes, lecturas = [], []
    try:
        for v in ("A", "B"):
            if mutante is not None:
                ki = mundo()[v]["P-KI67"]
                M._orden_sellado = lambda c, r, sep=4, limite=None, ki=ki: mutante(ki, c, r, sep, limite)
            tmp = tempfile.mkdtemp(prefix="lam-capas-%s-" % v)
            try:
                ses, _n1, lector = _prepara(tmp, v)
                planes.append(_clave_plan(M.planifica(ses, lector, GEO, CUOTAS)))
                lecturas.append(list(lector.lecturas))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    finally:
        M._orden_sellado = original
    return planes, lecturas


def _comprueba_invariancia(test, planes, lecturas):
    """La planificación no lee ni un píxel y da los mismos campos con cualquier DAB de P-KI67."""
    test.assertEqual(lecturas, [[], []])
    test.assertTrue(all(planes[0].get(t) for t in C.TAREAS), planes[0])
    test.assertEqual(planes[0], planes[1])


class SeleccionSinDiana(unittest.TestCase):
    def test_mundo_A_y_B_difieren_solo_en_el_dab_de_ki67(self):
        m = mundo()
        self.assertFalse(np.array_equal(m["A"]["P-KI67"], m["B"]["P-KI67"]))
        for op in ("P-HE", "P-CK19", "P-HER2NEG"):
            self.assertTrue(np.array_equal(m["A"][op], m["B"][op]))

    def test_seleccion_no_depende_del_dab_de_ki67(self):
        _comprueba_invariancia(self, *_planes())

    def test_mutante_que_ordena_por_dab_de_ki67_se_detecta(self):
        """Si la selección mirara la positividad de la diana (aquí: ordenar los candidatos por DAB
        de P-KI67), la misma comprobación sale ROJA."""
        original = M._orden_sellado

        class _SinBarajar:
            @staticmethod
            def permutation(n):
                return np.arange(n)

        def mutante(ki, cands, rng, sep=4, limite=None):
            def marron(c):
                if isinstance(c[0], str):                                      # registro: (par, b, a, ux0, uy0)
                    x, y = int(c[3] / W.MPP), int(c[4] / W.MPP)
                else:                                                          # vista: (i, j, x0, y0)
                    x, y = c[2], c[3]
                v = ki[y:y + 512, x:x + 512].astype(int)
                return -int(((v[..., 0] - v[..., 2]) > 50).sum())
            return original(sorted(cands, key=marron), _SinBarajar(), sep, limite)

        planes, lecturas = _planes(mutante)
        self.assertNotEqual(planes[0], planes[1])                              # el mutante cambia los campos…
        with self.assertRaises(AssertionError):                                # …y la comprobación lo caza
            _comprueba_invariancia(self, planes, lecturas)


class Entradas(unittest.TestCase):
    def test_sin_sello_no_elige_nada(self):
        tmp = tempfile.mkdtemp(prefix="lam-capas-sinsello-")
        try:
            ses, n1, lector = _prepara(tmp)
            os.remove(os.path.join(ses, "congelacion.json"))
            r = M.produce(ses, ExportadorFalso(n1), lector, GEO, CUOTAS, log=lambda m: None)
            self.assertEqual(r["rc"], 5)
            self.assertFalse(os.path.exists(os.path.join(n1, "manifiesto.json")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_sin_registro_ni_mascara_lo_dice_y_escribe_lo_demas(self):
        tmp = tempfile.mkdtemp(prefix="lam-capas-parcial-")
        try:
            ses, n1, lector = _prepara(tmp, sin=("registro", "mascara"))
            with _Minimo(2):
                r = M.produce(ses, ExportadorFalso(n1), lector, GEO, CUOTAS, log=lambda m: None)
            self.assertEqual(r["rc"], 4)
            self.assertEqual(set(r["doc"]["faltan"]), {"registro", "ck19", "figura"})
            self.assertEqual(set(r["doc"]["tareas"]), {"nucleos", "artefacto"})
            capas = SN._carga_capas(n1, PuertaFalsa())
            self.assertEqual(set(capas), {"nucleos", "artefacto"})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_producto_de_otro_sello_no_se_pinta(self):
        tmp = tempfile.mkdtemp(prefix="lam-capas-otrosello-")
        try:
            ses, n1, lector = _prepara(tmp)
            ruta = os.path.join(ses, "piloto", "registro", "P-KI67.json")
            with open(ruta) as fh:
                d = json.load(fh)
            d["sello"] = "0" * 64
            with open(ruta, "w") as fh:
                json.dump(d, fh)
            import laminillas_comun as LC
            LC.marca_hecho(ses, "registro-par", "P-KI67", "P-CK19", productos=[os.path.relpath(ruta, ses)])
            pl = M.planifica(ses, lector, GEO, CUOTAS)
            self.assertEqual({c["par"] for c in pl["plan"]["registro"]}, {"P-HER2NEG"})
            self.assertIn("registro P-KI67", pl["ent"]["motivos"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_ventanilla_admite_la_orden_sin_lista_cerrada(self):
        import laminillas_ventanilla as V
        conf = V.CONF["laminillas_exporta"]
        self.assertEqual(conf["perfil"], "exporta")
        self.assertIsNone(V.valida_args(["capas-piloto"]))
        self.assertIsNone(V.valida_orden(conf, ["capas-piloto"]))


DRIVER = r"""
import json, os, sys
tests, tools, laminas_dir, geo, cuotas = sys.argv[1], sys.argv[2], sys.argv[3], json.loads(sys.argv[4]), json.loads(sys.argv[5])
for p in (tests, tools, os.path.join(tools, "panel_vision")):
    sys.path.insert(0, p)
import _laminillas_capas_mundo as W
import laminillas_capas as M
M.LECTOR = W.LectorFalso.desde_dir(laminas_dir)
M.GEOMETRIA.update(geo)
M.CUOTAS.update(cuotas)
M.MINIMO = 1
import laminillas_exporta
rc = laminillas_exporta.main(["capas-piloto"])
out = {"rc": rc}
n1 = os.environ["BTP_N1_DIR"]
try:
    import siembra_n1 as SN
    capas = SN._carga_capas(n1, SN.PuertaN1())
    out["revalidadas"] = {t: len(v) for t, v in capas.items()}
except Exception as e:
    out["revalidadas_error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
# la figura con la geometría de PRODUCCIÓN (768 px, panel de 384), en sus cuatro disposiciones,
# por el OCR + Puerta + trazos de exporta_n1 (lo que hace exporta_png con cada capa)
import exporta_n1 as E
from PIL import Image
geo_prod = dict(M.GEOMETRIA, lado_figura=768, panel_figura=384)
pl = M.planifica(os.getcwd(), M.LECTOR, None, M.CUOTAS)          # campos de la geometría de prueba
motivos = []
for k in range(4):
    campo = dict(pl["plan"]["figura"][k % len(pl["plan"]["figura"])], lado=geo_prod["panel_figura"])
    capa = M.pinta_figura(M.LECTOR, campo, pl["ent"], k, geo_prod)
    motivos.append(E.revisar_cristal(Image.fromarray(capa["rgb"]), E.ESCALAS_PNG, con_puerta=True))
out["figura_produccion"] = motivos
print("RESULTADO " + json.dumps(out))
"""


@unittest.skipUnless(shutil.which("tesseract"), "sin tesseract")
class PorLaVentanillaConLaPuertaDeVerdad(unittest.TestCase):
    """`laminillas_exporta.main(["capas-piloto"])` con exporta_n1 y puerta_n1 de verdad (OCR +
    Puerta + trazos), por la tubería /dev/fd, en un subproceso con una casa base falsa: lo pintado
    pasa la Puerta, un campo con el apellido de la titular rotulado no entra, y lo que entra lo
    re-valida `siembra_n1.PuertaN1` (revalidar_n1)."""

    def test_puerta_de_verdad(self):
        tmp = tempfile.mkdtemp(prefix="lam-capas-puerta-")
        try:
            casa = os.path.join(tmp, "casa")
            estado = os.path.join(casa, "tools", "state")
            os.makedirs(os.path.join(estado, "borde"))
            for nombre, dd in (("perfil.local.json", {"titular": {"nombre": "Leocadia Rosa",
                                                                  "apellidos": "Quintanar Tallon"}}),
                               ("nombres.local.json", {"nombres": ["eustaquio", "fulgencio"]}),
                               ("identidad.local.json", {"ids": ["X9988776Q"]})):
                with open(os.path.join(casa, "tools", nombre), "w") as fh:
                    json.dump(dd, fh)
            with open(os.path.join(estado, "borde", "canarios.json"), "w") as fh:
                json.dump(["CANARIO-7f3a9c11"], fh)
            cuotas = {"nucleos": 2, "ck19": 1, "artefacto": 1, "figura": 2, "registro": 1}
            ses, _n1, lector = _prepara(tmp)
            pl = M.planifica(ses, lector, GEO, cuotas)
            c0 = pl["plan"]["nucleos"][0]                                         # el primer campo de núcleos
            from PIL import Image, ImageDraw
            lams = {op: a.copy() for op, a in mundo()["A"].items()}
            img = Image.fromarray(lams["P-HE"])
            ImageDraw.Draw(img).text((c0["x0"] + 60, c0["y0"] + 200), "QUINTANAR", fill=(40, 40, 40),
                                     font=S._fuente(44))
            lams["P-HE"] = np.array(img)
            W.guarda_laminas(os.path.join(tmp, "laminas"), lams)
            n1 = os.path.join(tmp, "Laminillas-N1")
            env = {k: v for k, v in os.environ.items() if not k.startswith("BTP_")}
            env.update(BTP_REPO=casa, BTP_STATE_DIR=estado, BTP_HALT_FILES=os.path.join(tmp, "no-halt"),
                       BTP_TEST_BATTERY="1", BTP_N1_DIR=n1, BTP_VENTANILLA="1")
            r = subprocess.run([sys.executable, "-c", DRIVER, AQUI, TOOLS, os.path.join(tmp, "laminas"),
                                json.dumps(GEO), json.dumps(cuotas)], capture_output=True, text=True,
                               timeout=1800, env=env, cwd=ses, stdin=subprocess.DEVNULL)
            salida = r.stdout + r.stderr
            self.assertNotIn("QUINTANAR", salida)
            linea = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULTADO ")]
            self.assertTrue(linea, salida[-3000:])
            res = json.loads(linea[-1][len("RESULTADO "):])
            self.assertEqual(res["rc"], 0, salida[-3000:])
            with open(os.path.join(n1, C.N1_PANEL, C.CAPAS_PILOTO)) as fh:
                doc = json.load(fh)
            self.assertEqual({t: len(v) for t, v in doc["tareas"].items()}, cuotas)
            self.assertEqual(res.get("revalidadas"), cuotas, res)                 # revalidar_n1 de verdad
            self.assertEqual(res.get("figura_produccion"), [[], [], [], []], res)  # 768 px: pasa el OCR
            self.assertGreaterEqual(doc["rechazadas_puerta"]["nucleos"], 1)
            x0, y0 = c0["x0"], c0["y0"]
            for f, o in doc["origen"].items():
                if o.get("lamina") == "P-HE":
                    self.assertNotEqual((o["campo_l0"][0], o["campo_l0"][1]), (x0, y0), f)
            with open(os.path.join(n1, "manifiesto.json")) as fh:
                man = json.load(fh)["ficheros"]
            self.assertEqual(set(man), {f for fs in doc["tareas"].values() for f in fs})
            self.assertTrue(all(man[f]["tipo"] == "png" for f in man))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
