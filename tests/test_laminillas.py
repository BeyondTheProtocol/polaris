#!/usr/bin/env python3
"""tests/test_laminillas.py — ventanilla y jaulas de las laminillas DFCI (plan, «Verificación»).

SIN DATOS: todo canario es sintético y vive en un árbol temporal que hace de HOME y de repo. Las
pocas pruebas sobre el perfil REAL (HOME de verdad) solo comprueban que algo FALLA, sin imprimir
nada de lo que hay (ni siquiera nombres).

Cubre (plan → «Verificación»):
  · Ventanilla: rechaza rutas clínicas y accesiones por argumento; código 99 al pasar TOPE_GB; sin
    red ni HF_TOKEN dentro de la jaula; entorno en lista blanca exacta; puertas de disco, memoria
    (con un solo aviso) y sha256; «hecho» que se invalida si cambia el producto.
  · Jaulas: en analisis / visor-clinico / exporta se LEE un canario en SESION y FALLA en ORIGEN, en
    `Clinico-PRIVADO/<otra>/`, bajo las demás raíces, bajo un `_PRIVADO_*` sintético (también por
    enlace sin nombre clínico y con tilde NFC/NFD) y `tools/.x_secrets.json`; en red-sin-zona,
    vision y visor-n1 falla todo, SESION incluida; un perfil con el allow antes del deny hace fallar
    la comprobación; `security` falla en todas; `pip index versions pip` responde en red-sin-zona;
    analisis no escribe fuera de SESION ni exporta fuera de N1; en exporta real `deid._diccionario()`
    no está vacío.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(RAIZ, "tools")
sys.path.insert(0, TOOLS)

PY_JAULA = "/opt/homebrew/bin/python3.12"      # legible en las jaulas deny-default (/opt/homebrew)
SANDBOX = "/usr/bin/sandbox-exec"
VENV_PAT = os.path.expanduser("~/.polaris-venvs/patologia/bin/python")

SONDA = r"""
import json, os, sys
out = {}
for etiqueta, ruta in json.loads(sys.argv[1]).items():
    try:
        with open(ruta, "rb") as f:
            f.read(1)
        out[etiqueta] = "lee"
    except Exception as e:
        out[etiqueta] = type(e).__name__
for etiqueta, ruta in json.loads(sys.argv[2]).items():
    try:
        with open(ruta, "w") as f:
            f.write("x")
        out["W:" + etiqueta] = "escribe"
    except Exception as e:
        out["W:" + etiqueta] = type(e).__name__
print(json.dumps(out))
"""

GENERA = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import laminillas_jaulas as J
a = json.loads(sys.argv[2])
print(J.escribe(a["perfil"], a["dir"], sesion=a["sesion"], origen=a["origen"], n1=a["n1"],
                tmpdir=a["tmp"]))
"""


def _escribe(ruta, texto="canario-sintetico"):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w") as f:
        f.write(texto)


@unittest.skipUnless(os.path.exists(SANDBOX) and os.path.exists(PY_JAULA), "sin sandbox-exec")
class Jaulas(unittest.TestCase):
    """Árbol sintético: HOME y BTP_REPO apuntan a un temporal; el generador corre con ese entorno."""

    @classmethod
    def setUpClass(cls):
        cls.t = os.path.realpath(tempfile.mkdtemp(prefix="lam-jaulas-"))
        t = cls.t
        cls.home = os.path.join(t, "home")
        cls.repo = os.path.join(t, "repo")
        lam = os.path.join(cls.home, "Clinico-PRIVADO", "laminillas-DFCI")
        cls.sesion, cls.origen = os.path.join(lam, "sesion"), os.path.join(lam, "origen")
        cls.n1 = os.path.join(cls.home, "Laminillas-N1")
        cls.tmp = os.path.join(cls.sesion, "tmp")
        fuera = os.path.join(t, "fuera")
        nfc = unicodedata.normalize("NFC", "Historial clínico Prueba")
        nfd = unicodedata.normalize("NFD", "Historial clínico Otra")
        cls.lectura = {
            "sesion": os.path.join(cls.sesion, "c.txt"),
            "origen": os.path.join(cls.origen, "c.txt"),
            "otra": os.path.join(cls.home, "Clinico-PRIVADO", "otra", "c.txt"),
            "informes": os.path.join(cls.repo, "informes", "c.txt"),
            "nova": os.path.join(cls.repo, "docu enviada a nova", "c.txt"),
            "dicom": os.path.join(cls.home, "DICOM_Seattle", "c.txt"),
            "privado": os.path.join(fuera, "a", "_PRIVADO_CLINICO", "c.txt"),
            "privado_mayus": os.path.join(fuera, "b", "_privado_expediente", "c.txt"),
            "tilde_nfc": os.path.join(fuera, nfc, "c.txt"),
            "tilde_nfd": os.path.join(fuera, nfd, "c.txt"),
            "enlace": os.path.join(cls.repo, "00_FUENTE-DE-VERDAD", "x", "_PRIVADO_NUCLEO", "c.txt"),
            "secretos": os.path.join(cls.repo, "tools", ".x_secrets.json"),
            "worktree_informes": os.path.join(cls.repo, ".claude", "worktrees", "r", "informes", "c.txt"),
            "n1": os.path.join(cls.n1, "c.txt"),
            "neutro": os.path.join(fuera, "neutro", "c.txt"),
            "dms": os.path.join(fuera, "c", "_PRIVADO_DMS", "c.txt"),          # exento (familia C)
        }
        for k, r in cls.lectura.items():
            if k != "enlace":
                _escribe(r)
        # Familia A por ENLACE: la carpeta descubierta apunta a un sitio cuyo nombre NO es clínico.
        plano = os.path.join(fuera, "plano")
        _escribe(os.path.join(plano, "c.txt"))
        os.makedirs(os.path.join(cls.repo, "00_FUENTE-DE-VERDAD", "x"), exist_ok=True)
        os.symlink(plano, os.path.join(cls.repo, "00_FUENTE-DE-VERDAD", "x", "_PRIVADO_NUCLEO"))
        cls.lectura["enlace_real"] = os.path.join(plano, "c.txt")
        os.makedirs(cls.tmp, exist_ok=True)
        cls.escritura = {
            "sesion": os.path.join(cls.sesion, "w.txt"),
            "tmp": os.path.join(cls.tmp, "w.txt"),
            "n1": os.path.join(cls.n1, "w.txt"),
            "fuera": os.path.join(fuera, "neutro", "w.txt"),
            "origen": os.path.join(cls.origen, "w.txt"),
        }
        cls.jaulas = os.path.join(t, "jaulas")
        cls.perfiles = {p: cls._genera(p) for p in (
            "red-sin-zona", "analisis", "analisis-ingesta", "visor-clinico", "visor-n1",
            "exporta", "vision")}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.t, ignore_errors=True)

    @classmethod
    def _genera(cls, perfil, dir_=None):
        env = dict(os.environ, HOME=cls.home, BTP_REPO=cls.repo)
        # red-sin-zona tiene su TMPDIR propio, fuera de SESION (como en producción: TMP_RED)
        tmp = os.path.join(cls.t, "tmp-red") if perfil in ("red-sin-zona", "vision") else cls.tmp
        arg = json.dumps({"perfil": perfil, "dir": dir_ or cls.jaulas, "sesion": cls.sesion,
                          "origen": cls.origen, "n1": cls.n1, "tmp": tmp})
        r = subprocess.run([sys.executable, "-c", GENERA, TOOLS, arg], env=env,
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout.strip().splitlines()[-1]

    def _sonda(self, perfil_ruta, lectura=None, escritura=None):
        for r in (escritura or self.escritura).values():
            if os.path.exists(r):
                os.unlink(r)
        r = subprocess.run([SANDBOX, "-f", perfil_ruta, PY_JAULA, "-c", SONDA,
                            json.dumps(lectura or self.lectura),
                            json.dumps(escritura or self.escritura)],
                           capture_output=True, text=True, cwd="/",
                           env={"PATH": "/usr/bin:/bin", "HOME": self.home, "TMPDIR": self.tmp})
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        return json.loads(r.stdout)

    # ── lecturas ──
    PROHIBIDAS = ("origen", "otra", "informes", "nova", "dicom", "privado", "privado_mayus",
                  "tilde_nfc", "tilde_nfd", "enlace", "enlace_real", "secretos",
                  "worktree_informes")

    def _comprueba_datos(self, perfil, lee_sesion=True, lee_origen=False, lee_n1=None):
        res = self._sonda(self.perfiles[perfil])
        self.assertEqual(res["sesion"] == "lee", lee_sesion, (perfil, res["sesion"]))
        for k in self.PROHIBIDAS:
            if k == "origen" and lee_origen:
                self.assertEqual(res[k], "lee", (perfil, k))
                continue
            self.assertNotEqual(res[k], "lee", "%s lee %s" % (perfil, k))
        if lee_n1 is not None:
            self.assertEqual(res["n1"] == "lee", lee_n1, (perfil, "n1"))
        return res

    def test_analisis(self):
        res = self._comprueba_datos("analisis")
        self.assertEqual(res["neutro"], "lee")                     # allow default fuera de zona
        self.assertEqual(res["dms"], "lee")                        # _privado_dms, exento
        self.assertEqual(res["W:sesion"], "escribe")
        self.assertEqual(res["W:tmp"], "escribe")
        for k in ("n1", "fuera", "origen"):
            self.assertNotEqual(res["W:" + k], "escribe", "analisis escribe en %s" % k)

    def test_analisis_ingesta_unico_con_origen(self):
        res = self._comprueba_datos("analisis-ingesta", lee_origen=True)
        self.assertEqual(res["W:origen"], "escribe")
        self.assertNotEqual(res["W:fuera"], "escribe")

    def test_visor_clinico(self):
        res = self._comprueba_datos("visor-clinico")
        for k in self.escritura:
            self.assertNotEqual(res["W:" + k], "escribe", "visor escribe en %s" % k)

    def test_exporta(self):
        res = self._comprueba_datos("exporta", lee_n1=True)
        self.assertEqual(res["W:n1"], "escribe")
        for k in ("sesion", "tmp", "fuera", "origen"):
            self.assertNotEqual(res["W:" + k], "escribe", "exporta escribe en %s" % k)

    def test_sin_datos_falla_todo(self):
        for perfil in ("red-sin-zona", "vision", "visor-n1"):
            res = self._sonda(self.perfiles[perfil])
            for k in ("sesion",) + self.PROHIBIDAS:
                self.assertNotEqual(res[k], "lee", "%s lee %s" % (perfil, k))
            for k in ("sesion", "tmp", "origen", "fuera"):
                self.assertNotEqual(res["W:" + k], "escribe", "%s escribe en %s" % (perfil, k))
        self.assertEqual(self._sonda(self.perfiles["visor-n1"])["n1"], "lee")
        self.assertEqual(self._sonda(self.perfiles["vision"])["n1"], "lee")

    def test_orden_allow_tras_deny(self):
        import laminillas_jaulas as J
        with open(self.perfiles["analisis"]) as f:
            texto = f.read()
        ok, motivo = J.orden_correcto(texto, [self.sesion, self.origen, self.n1])
        self.assertTrue(ok, motivo)
        # Mutante: el allow de SESION emitido ANTES de los deny → la comprobación lo caza…
        lineas = texto.splitlines()
        allows = [ln for ln in lineas if ln.startswith("(allow file-read*") and self.sesion in ln]
        self.assertTrue(allows)
        malo = [lineas[0], lineas[1]] + allows + [ln for ln in lineas[2:] if ln not in allows]
        ok, _ = J.orden_correcto("\n".join(malo), [self.sesion])
        self.assertFalse(ok)
        # …y de verdad deja la jaula sin SESION (el deny posterior la tapa).
        ruta = os.path.join(self.t, "malo.sb")
        with open(ruta, "w") as f:
            f.write("\n".join(malo) + "\n")
        self.assertNotEqual(self._sonda(ruta)["sesion"], "lee")

    def test_llavero_denegado_en_todas(self):
        for perfil, ruta in self.perfiles.items():
            r = subprocess.run([SANDBOX, "-f", ruta, "/usr/bin/security", "find-generic-password",
                                "-s", "canario-sintetico-laminillas", "-w"],
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0, perfil)
            self.assertIn("Operation not permitted", r.stderr, perfil)
            # y desde un hijo de la jaula, no solo como primer exec
            r = subprocess.run([SANDBOX, "-f", ruta, PY_JAULA, "-c",
                                "import subprocess,sys; sys.exit(subprocess.call(['/usr/bin/security',"
                                "'find-generic-password','-s','canario-sintetico-laminillas']))"],
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0, perfil)
            with open(ruta) as f:
                texto = f.read()
            self.assertIn('(global-name "com.apple.SecurityServer")', texto)
            self.assertIn('(global-name "com.apple.securityd")', texto)

    def test_sin_red_en_las_de_datos(self):
        codigo = ("import socket; s=socket.socket(); s.settimeout(3)\n"
                  "try:\n s.connect(('1.1.1.1', 443)); print('red')\n"
                  "except Exception as e: print(type(e).__name__)")
        for perfil in ("analisis", "analisis-ingesta", "exporta", "visor-clinico"):
            r = subprocess.run([SANDBOX, "-f", self.perfiles[perfil], PY_JAULA, "-c", codigo],
                               capture_output=True, text=True)
            self.assertNotEqual(r.stdout.strip(), "red", perfil)


class JaulasReales(unittest.TestCase):
    """Perfil con el HOME real: solo se comprueba que lo clínico FALLA (sin imprimir nada)."""

    @unittest.skipUnless(os.path.exists(SANDBOX), "sin sandbox-exec")
    def test_perfiles_reales_compilan_rapido(self):
        # Regresión del 1-oct-26: regex con tildes que no terminaban de compilar (3 GB de RAM por
        # perfil) o reventaban (rc -6 / «data object length exceeds maximum»).
        import time
        import laminillas_jaulas as J
        d = tempfile.mkdtemp()
        try:
            for perfil in J.PERFILES:
                ruta = J.escribe(perfil, d, tmpdir=d)
                t = time.time()
                r = subprocess.run([SANDBOX, "-f", ruta, "/bin/echo", "ok"], capture_output=True,
                                   text=True, timeout=30)
                self.assertEqual((r.returncode, r.stdout.strip()), (0, "ok"),
                                 "%s: %s" % (perfil, r.stderr[:200]))
                self.assertLess(time.time() - t, 5, perfil)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    @unittest.skipUnless(os.path.exists(SANDBOX), "sin sandbox-exec")
    def test_raiz_clinica_real_denegada(self):
        import laminillas_jaulas as J
        d = tempfile.mkdtemp()
        try:
            for perfil in ("analisis", "exporta", "visor-clinico"):
                ruta = J.escribe(perfil, d, sesion=os.path.join(d, "sesion-falsa"), tmpdir=d)
                codigo = ("import os\nfor r in %r:\n try:\n  os.listdir(r); print('LEE')\n"
                          " except Exception: print('no')" % (
                              [os.path.join(J.HOME, "Clinico-PRIVADO"), J.ORIGEN],))
                r = subprocess.run([SANDBOX, "-f", ruta, PY_JAULA, "-c", codigo],
                                   capture_output=True, text=True)
                self.assertNotIn("LEE", r.stdout, perfil)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    @unittest.skipUnless(os.path.exists(SANDBOX), "sin sandbox-exec")
    def test_exporta_real_carga_diccionario(self):
        import laminillas_jaulas as J
        d = tempfile.mkdtemp()
        try:
            ruta = J.escribe("exporta", d, tmpdir=d)
            codigo = ("import sys; sys.path.insert(0, %r); import deid; print(len(deid._diccionario()))"
                      % TOOLS)
            fuera = subprocess.run(["/usr/bin/python3", "-c", codigo], capture_output=True,
                                   text=True)
            dentro = subprocess.run([SANDBOX, "-f", ruta, "/usr/bin/python3", "-c", codigo],
                                    capture_output=True, text=True)
            n_fuera = int(fuera.stdout.strip() or 0)
            if n_fuera == 0:
                self.skipTest("sin overlay de deid en esta máquina")
            self.assertEqual(int(dentro.stdout.strip() or 0), n_fuera, dentro.stderr[-300:])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    @unittest.skipUnless(os.path.exists(VENV_PAT), "sin venv patologia")
    def test_pip_en_red_sin_zona(self):
        import laminillas_stack as S
        r = S.corre("patologia", [VENV_PAT, "-m", "pip", "index", "versions", "pip"], captura=True)
        if r.returncode and ("NewConnectionError" in r.stderr or "Name or service" in r.stderr):
            self.skipTest("sin red")
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("Available versions", r.stdout)


class Ventanilla(unittest.TestCase):
    def setUp(self):
        import laminillas_ventanilla as V
        self.V = V

    def test_rechaza_rutas_y_accesiones(self):
        malos = ["/tmp/x", "~/algo", "../P-HE", os.path.expanduser("~/Clinico-PRIVADO/x"),
                 "_PRIVADO_CLINICO", "11B2223334", "99B-12345", "B2099.12345", "VH-99-B-12345",
                 "ab cd", "P-HE;rm"]
        for a in malos:
            self.assertIsNotNone(self.V.valida_args(["P-HE", a]), a)
        for a in ("P-HE", "P-KI67", "tesela=12", "mpp=0.5", "uni2h"):
            self.assertIsNone(self.V.valida_args([a]), a)

    def test_ejecuta_rechaza_antes_de_lanzar(self):
        lanzado = []
        orig = self.V.subprocess.call
        self.V.subprocess.call = lambda *a, **k: lanzado.append(a) or 0
        try:
            rc = self.V.ejecuta("test", "laminillas_qc", ["/Users/x/Clinico-PRIVADO/y"],
                                log=lambda m: None)
            self.assertEqual(rc, self.V.COD_ARGS)
            rc = self.V.ejecuta("test", "laminillas_qc", ["12B0000001"], log=lambda m: None)
            self.assertEqual(rc, self.V.COD_ARGS)
        finally:
            self.V.subprocess.call = orig
        self.assertEqual(lanzado, [])

    def test_lector_clinico_delega(self):
        import lector_clinico as L
        for n in ("laminillas_ingesta", "laminillas", "laminillas_registro", "laminillas_qc",
                  "laminillas_visor", "laminillas_pesos", "laminillas_congela",
                  "laminillas_segmenta"):
            self.assertIn(n, L.PROCESADORES)
            self.assertIn(n, L.LAMINILLAS)
            self.assertTrue(L.LAMINILLAS[n][1].startswith(os.path.expanduser("~/.polaris-venvs/")))
        # Lo que la ventanilla sabe lanzar, lector_clinico lo registra (y con el mismo script y
        # venv): un procesador en CONF sin registro sería «no sancionado» por `procesa`.
        for n, c in self.V.CONF.items():
            self.assertIn(n, L.LAMINILLAS, n)
            self.assertEqual(L.LAMINILLAS[n][0], c["script"], n)
            self.assertIn("/%s/" % c["venv"], L.LAMINILLAS[n][1], n)

    # ── laminillas_congela y laminillas_segmenta (Puerta del piloto, puntos 2-4) ──
    def test_congela_ordenes_son_las_del_script(self):
        import re
        c = self.V.CONF["laminillas_congela"]
        self.assertEqual((c["script"], c["venv"], c["perfil"], c["modo"], c["datos"]),
                         ("tools/laminillas_congela.py", "patologia", "analisis", None, True))
        self.assertGreaterEqual(c["mem"], 9)
        with open(os.path.join(TOOLS, "laminillas_congela.py"), encoding="utf-8") as f:
            src = f.read()
        m = re.search(r'add_argument\("orden", choices=\(([^)]*)\)', src)
        self.assertIsNotNone(m, "laminillas_congela.main ya no declara sus órdenes con choices")
        self.assertEqual(set(c["ordenes"]), set(re.findall(r'"([a-z]+)"', m.group(1))))
        self.assertEqual(self.V.mem_de(c, ["verifica"]), 0)        # solo lee JSON: sin cerrojo
        self.assertEqual(self.V.mem_de(c, ["precongela"]), c["mem"])
        s = self.V.CONF["laminillas_segmenta"]
        self.assertEqual((s["script"], s["modo"], s["perfil"], s["datos"]),
                         ("tools/laminillas_proc.py", "segmenta", "analisis", True))

    def test_congela_args_validos_y_orden_cerrada(self):
        c = self.V.CONF["laminillas_congela"]
        for a in (["congela", "--fecha", "2026-10-02", "--traza", "laminillas_congela,laminillas"],
                  ["recongela", "--fecha", "2026-10-02", "--causa", "I0", "--traza=laminillas"],
                  ["congela", "--fecha", "2026-10-02", "--revision-alto", "pigmento:0.25",
                   "--vectores-i0-revisados"], ["precongela", "--para-recongelar"], ["verifica"]):
            self.assertIsNone(self.V.valida_args(a), a)
            self.assertIsNone(self.V.valida_orden(c, a), a)
        for a in ([], ["borra"], ["P-KI67"], ["--fecha", "2026-10-02"]):
            self.assertIsNotNone(self.V.valida_orden(c, a), a)
        lanzado = []
        orig = self.V.subprocess.call
        self.V.subprocess.call = lambda *a, **k: lanzado.append(a) or 0
        d = tempfile.mkdtemp()
        try:
            for a in ([], ["borra"], ["congela", "--traza", "no_es_procesador"],
                      ["congela", "--traza", "laminillas_congela"]):     # sin traza en SESION
                self.assertEqual(self.V.ejecuta("test", "laminillas_congela", a, sesion=d,
                                                log=lambda m: None), self.V.COD_ARGS, a)
        finally:
            self.V.subprocess.call = orig
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(lanzado, [])

    def test_traduce_trazas(self):
        d = tempfile.mkdtemp()
        try:
            _escribe(os.path.join(d, "trazas", "laminillas_congela.tsv"), "0\t1\t1\t1\t0\n")
            _escribe(os.path.join(d, "trazas", "laminillas.tsv"), "0\t2\t2\t2\t0\n")
            _escribe(os.path.join(d, "trazas", "laminillas_qc.tsv"), "")       # vacía: no vale
            t = self.V.traduce_trazas
            self.assertEqual(t(["congela", "--fecha", "2026-10-02", "--traza",
                                "laminillas_congela,laminillas"], d),
                             (["congela", "--fecha", "2026-10-02", "--traza",
                               "laminillas_congela=trazas/laminillas_congela.tsv,"
                               "laminillas=trazas/laminillas.tsv"], None))
            self.assertEqual(t(["recongela", "--traza=laminillas", "--causa", "lector"], d)[0],
                             ["recongela", "--traza", "laminillas=trazas/laminillas.tsv",
                              "--causa", "lector"])
            self.assertEqual(t(["verifica"], d), (["verifica"], None))
            for malo in (["congela", "--traza"], ["congela", "--traza", "laminillas_segmenta"],
                         ["congela", "--traza", "otro"], ["congela", "--traza", "laminillas_qc"],
                         ["congela", "--traza", ","], ["congela", "--traza=laminillas=x"]):
                nuevos, motivo = t(malo, d)
                self.assertIsNone(nuevos, malo)
                self.assertTrue(motivo, malo)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_traza_solo_primera_corrida_sobre_her2neg(self):
        d = tempfile.mkdtemp()
        try:
            tt = self.V.toca_traza
            self.assertTrue(tt(d, "laminillas_congela", ["precongela"]))   # sin láminas: la 1.ª
            self.assertTrue(tt(d, "laminillas_qc", ["P-HER2NEG"]))
            self.assertTrue(tt(d, "laminillas_qc", ["P-HER2", "P-HER2NEG"]))
            self.assertFalse(tt(d, "laminillas_qc", ["P-KI67"]))           # otra lámina: no
            self.assertFalse(tt(d, "laminillas_qc", ["B-HE-1"]))
            _escribe(self.V.ruta_traza(d, "laminillas_qc"), "0\t1\t1\t1\t0\n")
            self.assertFalse(tt(d, "laminillas_qc", ["P-HER2NEG"]))        # ya hay: no se pisa
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_cierra_traza(self):
        d = tempfile.mkdtemp()
        try:
            final = self.V.ruta_traza(d, "laminillas_qc")
            _escribe(final + ".parcial", "0\t1\t1\t1\t0\n")
            self.assertEqual(self.V.cierra_traza(final + ".parcial", final, 99), final[:-4] +
                             ".fallida.tsv")
            self.assertFalse(os.path.exists(final))                      # rc ≠ 0: no es LA traza
            _escribe(final + ".parcial", "0\t1\t1\t1\t0\n")
            self.assertEqual(self.V.cierra_traza(final + ".parcial", final, 0), final)
            self.assertEqual(os.stat(final).st_mode & 0o777, 0o600)
            self.assertFalse(os.path.exists(final + ".parcial"))
            self.assertIsNone(self.V.cierra_traza(final + ".parcial", final, 0))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_ejecuta_pasa_la_traza_y_traduce(self):
        """Sin jaula ni venv: puertas, jaula y guarda_memoria sustituidos; se mira QUÉ se lanza."""
        import types
        V = self.V
        d = os.path.realpath(tempfile.mkdtemp(prefix="lam-traza-"))
        sesion = os.path.join(d, "sesion")
        llamadas = []

        def corre(comando, tope, env=None, inactivo_s=None, traza=None):
            llamadas.append({"comando": list(comando), "traza": traza})
            if traza:
                with open(traza, "w") as f:
                    f.write("0\t1.00\t1.00\t1.00\t0.00\n3\t2.50\t2.50\t2.00\t0.00\n")
            return rc[0], 2.0
        rc = [0]
        falso = types.ModuleType("guarda_memoria")
        falso.corre = corre
        orig = {k: getattr(V, k) for k in ("puerta_memoria", "puerta_disco", "interprete",
                                           "colima_arriba", "CERROJO")}
        orig_j = V.J.escribe
        orig_gm = sys.modules.get("guarda_memoria")
        try:
            V.puerta_memoria = lambda nombre, gb, log=None: (True, "test")
            V.puerta_disco = lambda gb: (True, "test")
            V.interprete = lambda venv: sys.executable
            V.colima_arriba = lambda: False
            V.CERROJO = os.path.join(d, "cerrojo")
            V.J.escribe = lambda *a, **k: os.path.join(d, "perfil.sb")
            sys.modules["guarda_memoria"] = falso
            ej = lambda n, a: V.ejecuta("test", n, a, log=lambda m: None, sesion=sesion)  # noqa
            traza = V.ruta_traza(sesion, "laminillas_congela")
            rc[0] = 99                                    # primera corrida fallida: no cuenta
            self.assertEqual(ej("laminillas_congela", ["precongela"]), 99)
            self.assertEqual(llamadas[-1]["traza"], traza + ".parcial")
            self.assertFalse(os.path.exists(traza))
            self.assertTrue(os.path.exists(traza[:-4] + ".fallida.tsv"))
            rc[0] = 0                                     # la siguiente vuelve a medir
            self.assertEqual(ej("laminillas_congela", ["precongela"]), 0)
            self.assertEqual(llamadas[-1]["traza"], traza + ".parcial")
            self.assertTrue(os.path.isfile(traza))
            with open(traza) as f:
                primera = f.read()
            self.assertEqual(ej("laminillas_congela", ["precongela"]), 0)
            self.assertIsNone(llamadas[-1]["traza"])     # ya hay traza: no se pisa
            with open(traza) as f:
                self.assertEqual(f.read(), primera)
            cmd = llamadas[-1]["comando"]
            self.assertEqual(cmd[:3], ["/usr/bin/sandbox-exec", "-f", os.path.join(d,
                                                                                 "perfil.sb")])
            self.assertTrue(cmd[4].endswith("tools/laminillas_congela.py"))
            self.assertEqual(cmd[5:], ["precongela"])     # sin modo antepuesto
            n = len(llamadas)
            self.assertEqual(ej("laminillas_congela", ["congela", "--fecha", "2026-10-02",
                                                       "--traza", "laminillas_congela"]), 0)
            self.assertEqual(llamadas[-1]["comando"][5:], [
                "congela", "--fecha", "2026-10-02", "--traza",
                "laminillas_congela=trazas/laminillas_congela.tsv"])
            # `verifica` no pide memoria: ni cerrojo ni vigilante (subprocess.call directo)
            orig_call = V.subprocess.call
            V.subprocess.call = lambda c, env=None: llamadas.append({"directo": c}) or 0
            try:
                self.assertEqual(ej("laminillas_congela", ["verifica"]), 0)
            finally:
                V.subprocess.call = orig_call
            self.assertEqual(llamadas[-1]["directo"][5:], ["verifica"])
            self.assertEqual(len(llamadas), n + 2)
            # segmenta sobre una diana: corre, pero su traza no es la del umbral (no es HER2NEG)
            self.assertEqual(ej("laminillas_segmenta", ["P-KI67"]), 0)
            self.assertIsNone(llamadas[-1]["traza"])
            self.assertEqual(llamadas[-1]["comando"][5:], ["segmenta", "P-KI67"])
            self.assertEqual(ej("laminillas_segmenta", ["P-HER2NEG"]), 0)
            self.assertEqual(llamadas[-1]["traza"],
                             V.ruta_traza(sesion, "laminillas_segmenta") + ".parcial")
        finally:
            for k, v in orig.items():
                setattr(V, k, v)
            V.J.escribe = orig_j
            if orig_gm is None:
                sys.modules.pop("guarda_memoria", None)
            else:
                sys.modules["guarda_memoria"] = orig_gm
            shutil.rmtree(d, ignore_errors=True)

    def test_disco(self):
        G = self.V.GiB
        self.assertFalse(self.V.puerta_disco(0, 14 * G)[0])
        self.assertTrue(self.V.puerta_disco(0, 16 * G)[0])
        self.assertFalse(self.V.puerta_disco(20, 25 * G)[0])      # declara 20 → hacen falta 30
        self.assertTrue(self.V.puerta_disco(20, 31 * G)[0])

    def test_memoria_espera_un_aviso_y_corta(self):
        reloj = [0.0]
        avisos = []
        malo = dict(swap_gb=5.0, libre_gb=12.0, ollama_vacio=True, gordos=[(3.0, 1, "x")],
                    swap_creciendo_mb=300.0)
        ok, motivo = self.V.puerta_memoria(
            "laminillas", 9, log=lambda m: None, medir=lambda: malo,
            dormir=lambda s: reloj.__setitem__(0, reloj[0] + s), reloj=lambda: reloj[0],
            aviso=lambda *a: avisos.append(a), espera_max=3600)
        self.assertFalse(ok)
        self.assertIn("swap", motivo)
        self.assertEqual(len(avisos), 1)                           # UNO, no uno por sondeo

    def test_memoria_criterios(self):
        b = dict(swap_gb=1.0, libre_gb=10.0, ollama_vacio=True, gordos=[])
        self.assertTrue(self.V.memoria_basta(b, 9)[0])
        self.assertFalse(self.V.memoria_basta(dict(b, ollama_vacio=False), 9)[0])
        # swap ≥ 4 GB: bloquea si crece o si no se sabe; si está quieto (rancio), pasa.
        self.assertFalse(self.V.memoria_basta(dict(b, swap_gb=4.0, swap_creciendo_mb=200), 9)[0])
        self.assertFalse(self.V.memoria_basta(dict(b, swap_gb=4.0, swap_creciendo_mb=None), 9)[0])
        self.assertFalse(self.V.memoria_basta(dict(b, swap_gb=4.0), 9)[0])
        self.assertTrue(self.V.memoria_basta(dict(b, swap_gb=7.7, swap_creciendo_mb=0.0), 9)[0])
        self.assertFalse(self.V.memoria_basta(dict(b, libre_gb=8.9), 9)[0])
        self.assertFalse(self.V.memoria_basta(None, 1)[0])

    def test_manifiesto_sha256(self):
        d = tempfile.mkdtemp()
        try:
            _escribe(os.path.join(d, "L-01.tiff"), "pixeles-sinteticos")
            sha = self.V.sha256(os.path.join(d, "L-01.tiff"))
            with open(os.path.join(d, "manifiesto.json"), "w") as f:
                json.dump({"laminas": {"P-X": {"fichero": "L-01.tiff", "sha256": sha}}}, f)
            self.assertTrue(self.V.verifica_manifiesto(d, ["P-X"])[0])
            _escribe(os.path.join(d, "L-01.tiff"), "pixeles-cambiados")
            self.assertFalse(self.V.verifica_manifiesto(d, ["P-X"])[0])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_hecho_se_invalida(self):
        import laminillas_comun as C
        d = tempfile.mkdtemp()
        try:
            _escribe(os.path.join(d, "out", "a.json"), "1")
            C.marca_hecho(d, "qc", "P-X", 3, productos=["out/a.json"])
            self.assertTrue(C.esta_hecho(d, "qc", "P-X", 3))
            _escribe(os.path.join(d, "out", "a.json"), "2")
            self.assertFalse(C.esta_hecho(d, "qc", "P-X", 3))
            with self.assertRaises(ValueError):
                C.ruta_hecho(d, "qc", "../x")
        finally:
            shutil.rmtree(d, ignore_errors=True)


@unittest.skipUnless(os.path.exists(VENV_PAT) and os.path.exists(SANDBOX), "sin venv patologia")
class VentanillaEnJaula(unittest.TestCase):
    """Lanza procesadores de prueba por `ejecuta`, con jaula real sobre una SESION temporal."""

    def setUp(self):
        import laminillas_ventanilla as V
        self.V = V
        self.d = os.path.realpath(tempfile.mkdtemp(prefix="lam-vent-"))
        self.sesion = os.path.join(self.d, "Clinico-PRIVADO", "laminillas-DFCI", "sesion")
        self._orig = (V.puerta_memoria, V.TOPE_GB, V.CERROJO, dict(V.CONF))
        V.puerta_memoria = lambda nombre, gb, log=None: (True, "test")
        V.CERROJO = os.path.join(self.d, "cerrojo")

    def tearDown(self):
        V = self.V
        V.puerta_memoria, V.TOPE_GB, V.CERROJO, conf = self._orig
        V.CONF.clear()
        V.CONF.update(conf)
        os.environ.pop("HF_TOKEN", None)
        shutil.rmtree(self.d, ignore_errors=True)

    def _proc(self, nombre, codigo, **conf):
        script = os.path.join(self.d, nombre + ".py")
        with open(script, "w") as f:
            f.write(codigo)
        c = dict(script=script, modo=None, venv="patologia", perfil="analisis", mem=1, disco=0,
                 datos=False)
        c.update(conf)
        self.V.CONF[nombre] = c
        return self.V.ejecuta("test", nombre, [], log=lambda m: None, sesion=self.sesion,
                              jaulas_dir=os.path.join(self.d, "jaulas"))

    def test_entorno_lista_blanca_sin_red_ni_token(self):
        os.environ["HF_TOKEN"] = "hf_canario_sintetico"
        rc = self._proc("lam_env", (
            "import json, os, socket\n"
            "s = socket.socket(); s.settimeout(3)\n"
            "try:\n s.connect(('1.1.1.1', 443)); red = True\n"
            "except Exception: red = False\n"
            "json.dump({'env': sorted(os.environ), 'red': red, 'tok': os.environ.get('HF_TOKEN')},"
            " open('env.json', 'w'))\n"))
        self.assertEqual(rc, 0)
        with open(os.path.join(self.sesion, "env.json")) as f:
            r = json.load(f)
        self.assertFalse(r["red"])
        self.assertIsNone(r["tok"])
        permitidas = {"HOME", "PATH", "TMPDIR", "HF_HOME", "TORCH_HOME", "MPLCONFIGDIR",
                      "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "PYTORCH_ENABLE_MPS_FALLBACK",
                      "BTP_VENTANILLA", "INSTANSEG_BIOIMAGEIO_PATH", "HF_MODULES_CACHE",
                      "NUMBA_CACHE_DIR", "XDG_CACHE_HOME"}
        # Las tres últimas las pone el propio sistema/intérprete dentro de la jaula (LC_CTYPE:
        # coerción de locale de PEP 538), no vienen del padre.
        sobra = set(r["env"]) - permitidas - {"__CF_USER_TEXT_ENCODING", "__PYVENV_LAUNCHER__",
                                              "LC_CTYPE"}
        self.assertEqual(sobra, set())

    def test_codigo_99_al_pasar_tope(self):
        self.V.TOPE_GB = 0.3
        rc = self._proc("lam_tope", (
            "import time\nb = bytearray(900 * 1024 * 1024)\n"
            "for i in range(0, len(b), 4096): b[i] = 1\n"
            "time.sleep(30)\n"))
        self.assertEqual(rc, 99)
        # el 99 no deja LA traza del procesador (no vale para el umbral), sí la fallida
        self.assertFalse(os.path.exists(self.V.ruta_traza(self.sesion, "lam_tope")))
        self.assertTrue(os.path.isfile(os.path.join(self.sesion, "trazas",
                                                    "lam_tope.fallida.tsv")))

    def test_traza_de_memoria_real_y_legible_por_congela(self):
        """guarda_memoria de verdad, en la jaula real: la primera corrida deja
        SESION/trazas/<procesador>.tsv, `laminillas_congela.lee_traza_memoria` la lee y la segunda
        corrida no la pisa."""
        codigo = "import time\nb = bytearray(64 * 1024 * 1024)\ntime.sleep(4)\n"
        self.assertEqual(self._proc("lam_traza", codigo), 0)
        traza = self.V.ruta_traza(self.sesion, "lam_traza")
        self.assertTrue(os.path.isfile(traza))
        self.assertEqual(os.stat(traza).st_mode & 0o777, 0o600)
        with open(traza) as f:
            filas = [ln.rstrip("\n").split("\t") for ln in f if ln.strip()]
        self.assertGreaterEqual(len(filas), 1)
        self.assertTrue(all(len(x) == 5 for x in filas), filas)
        r = subprocess.run([VENV_PAT, "-c", (
            "import json, sys; sys.path.insert(0, sys.argv[1]); import laminillas_congela as K; "
            "print(json.dumps(K.lee_traza_memoria(sys.argv[2])))"), TOOLS, traza],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        leida = json.loads(r.stdout)
        self.assertEqual(leida["muestras"], len(filas))
        self.assertGreater(leida["pico_gb"], 0.0)
        antes = os.stat(traza).st_mtime_ns
        self.assertEqual(self._proc("lam_traza", codigo), 0)
        self.assertEqual(os.stat(traza).st_mtime_ns, antes)


HUMO_COLOR = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
d = sys.argv[2]
import numpy as np, openslide, tifffile
import laminillas_humo as H
img = H.imagen_sintetica()
out = {}
bien = os.path.join(d, "bien.tiff")
H.escribe_grundium(bien, img)
out["bien"] = H.prueba_color(bien)
s = openslide.OpenSlide(bien)
out["niveles"] = s.level_count
out["relleno"] = np.unique(np.asarray(s.read_region((H.L0[0] - 900, 100), 0, (256, 256))
                                      .convert("RGB")).reshape(-1, 3), axis=0).tolist()
with tifffile.TiffFile(bien) as t:
    out["fotometrica"] = [p.photometric.name for p in t.pages]
    out["submuestreo"] = list(t.pages[0].subsampling)
s.close()
# El fallo de antes (RGB dado como YCbCr) tiene que hacer saltar la prueba de color
malo = os.path.join(d, "malo.tiff")
with tifffile.TiffWriter(malo, bigtiff=True) as tw:
    for f in (1, 4, 8):
        kw = dict(tile=(512, 512), compression="jpeg", compressionargs={"level": 90},
                  photometric="ycbcr", subsampling=(2, 2), metadata=None)
        if f > 1:
            kw["subfiletype"] = 1
        tw.write(np.ascontiguousarray(img[::f, ::f]), **kw)
try:
    H.prueba_color(malo)
    out["malo"] = "pasa"
except AssertionError as e:
    out["malo"] = str(e)
json.dump(out, open(os.path.join(d, "out.json"), "w"))
"""


@unittest.skipUnless(os.path.exists(VENV_PAT), "sin venv patologia")
class HumoColor(unittest.TestCase):
    """F2: el TIFF sintético del humo conserva el color (antes salía [255, 121, 255]) y la prueba de
    color del humo detecta el fallo si vuelve."""

    def test_color_conservado_y_fallo_detectado(self):
        d = os.path.realpath(tempfile.mkdtemp(prefix="lam-humo-"))
        try:
            r = subprocess.run([VENV_PAT, "-c", HUMO_COLOR, TOOLS, d], capture_output=True,
                               text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            with open(os.path.join(d, "out.json")) as f:
                o = json.load(f)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertIn("≤3", o["bien"])
        self.assertEqual(o["niveles"], 3)
        self.assertEqual(o["relleno"], [[255, 255, 255]])          # el relleno sigue siendo 255
        self.assertEqual(set(o["fotometrica"]), {"YCBCR"})
        self.assertEqual(o["submuestreo"], [2, 2])
        self.assertIn("colores corruptos", o["malo"])
        self.assertIn("vidrio", o["malo"])


PROC_SEGMENTA = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
base = sys.argv[2]
import numpy as np
from shapely.geometry import Point
import laminillas_comun as C
import laminillas_proc as P
import laminillas_sello as SL

class Lam:
    def __init__(self, n):
        self.opaco, self.mpp_l0 = n, 0.25

class Lector:
    @staticmethod
    def abre(n):
        return Lam(n)

llamadas = []
def seg(lector, lamina, nombre, **kw):
    llamadas.append([nombre, sorted(kw)])
    pol = [Point(100 + 40 * i, 200).buffer(12) for i in range(30)]
    return {"celulas": pol, "mpp_l0": lamina.mpp_l0, "dispositivo": "cpu", "lote": 1,
            "aviso_check_input_tile": False, "teselado": {"n": 4}, "pesos": {"sha256": "x"}}

out = {}
try:
    P.segmenta(base, ["P-KI67"], lector=Lector, segmentador=seg, log=lambda m: None)
    out["diana"] = "midio"
except SL.SelloAusente as e:
    out["diana"] = "SelloAusente"
out["llamadas_diana"] = len(llamadas)
out["sin_producto_diana"] = not os.path.exists(os.path.join(base, "segmenta",
                                                            "nucleos_P-KI67.npz"))
out["hechas"] = P.segmenta(base, ["P-HER2NEG"], lector=Lector, segmentador=seg,
                           log=lambda m: None)
out["kw"] = llamadas[-1][1]
z = np.load(os.path.join(base, "segmenta", "nucleos_P-HER2NEG.npz"))
out["claves"] = sorted(z.files)
out["n"] = int(len(z["centroide"]))
out["offs"] = int(z["pol_offs"][-1]) == len(z["pol_xy"])
r = json.load(open(os.path.join(base, "segmenta", "nucleos_P-HER2NEG.json")))
out["resumen"] = {k: r[k] for k in ("n", "sello", "dispositivo", "sin")}
out["mediana"] = r["area_nuclear"]["mediana_um2"]
out["hecho"] = C.esta_hecho(base, "segmenta", "P-HER2NEG")
out["segunda"] = P.segmenta(base, ["P-HER2NEG"], lector=Lector, segmentador=seg,
                            log=lambda m: None)
out["llamadas"] = len(llamadas)
os.environ["BTP_VENTANILLA"] = "1"
os.chdir(base)
out["main_sin_lamina"] = P.main(["segmenta"])
out["main_diana_sin_sello"] = P.main(["segmenta", "P-KI67"])
json.dump(out, open(os.path.join(base, "out.json"), "w"))
"""


@unittest.skipUnless(os.path.exists(VENV_PAT), "sin venv patologia")
class ProcSegmenta(unittest.TestCase):
    """`laminillas_proc segmenta` (punto 4 (i)/(i-bis)): diana sin sello no abre nada; suelo,
    núcleos guardados con su «hecho» y sin repetir. Segmentador y lector de mentira."""

    def test_guardia_productos_y_hecho(self):
        d = os.path.realpath(tempfile.mkdtemp(prefix="lam-proc-seg-"))
        try:
            r = subprocess.run([VENV_PAT, "-c", PROC_SEGMENTA, TOOLS, d], capture_output=True,
                               text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            with open(os.path.join(d, "out.json")) as f:
                o = json.load(f)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(o["diana"], "SelloAusente")
        self.assertEqual(o["llamadas_diana"], 0)                  # ni un píxel de la diana
        self.assertTrue(o["sin_producto_diana"])
        self.assertEqual(o["hechas"], ["P-HER2NEG"])
        self.assertIn("con_desplazada", o["kw"])
        self.assertIn("con_grandqc", o["kw"])
        self.assertEqual(o["claves"], ["area_um2", "centroide", "pol_offs", "pol_xy"])
        self.assertEqual(o["n"], 30)
        self.assertTrue(o["offs"])
        self.assertEqual(o["resumen"]["n"], 30)
        self.assertIsNone(o["resumen"]["sello"])                    # suelo, antes del sello
        self.assertAlmostEqual(o["mediana"], 3.14159 * 144 * 0.0625, delta=1.0)
        self.assertTrue(o["hecho"])
        self.assertEqual(o["segunda"], [])                          # hecho: no repite
        self.assertEqual(o["llamadas"], 1)
        self.assertEqual(o["main_sin_lamina"], 3)
        self.assertEqual(o["main_diana_sin_sello"], 3)


class VersionRegistro(unittest.TestCase):
    """2-oct: el «hecho» del registro no llevaba versión del código, así que un arreglo (la
    traslación del eslabón 1, 995fb22) nunca se habría aplicado a un par ya registrado."""

    def test_producto_sin_version_o_de_otra_no_se_salta(self):
        import laminillas_proc as P
        self.assertFalse(P.registro_de_esta_version({"tipo": "registro-par"}))
        self.assertFalse(P.registro_de_esta_version({"version_registro": "v1"}))
        self.assertFalse(P.registro_de_esta_version(None))
        self.assertTrue(P.registro_de_esta_version({"version_registro": P.VERSION_REGISTRO}))


class GrandQCenHE(unittest.TestCase):
    """Plan, punto 2: con reglas clásicas en las IHQ, en las H&E GrandQC «sigue como estaba».
    Hasta el 2-oct, P-HE se segmentaba sin GrandQC porque el régimen sellado era «clásicas»."""

    def setUp(self):
        import laminillas_proc as P
        self.P = P

    def test_he_siempre_ihq_segun_regimen(self):
        class Sello:
            def __init__(self, rige):
                self.d = {"umbral": {"rige": rige}}
        P = self.P
        for he in ("P-HE", "B-HE-1", "B-HE-2"):
            self.assertTrue(P._pide_grandqc(he, Sello("clasicas")), he)
        self.assertFalse(P._pide_grandqc("P-KI67", Sello("clasicas")))
        self.assertTrue(P._pide_grandqc("P-KI67", Sello("grandqc")))
        self.assertFalse(P._pide_grandqc("P-HE", Sello("clasicas"), con_grandqc=False))

    def test_he_segmentada_sin_grandqc_se_reconoce(self):
        d = tempfile.mkdtemp(prefix="lam-gq-he-")
        try:
            os.makedirs(os.path.join(d, self.P.DIR_SEGMENTA))
            ruta = os.path.join(d, self.P.DIR_SEGMENTA, "nucleos_P-HE.json")
            self.assertFalse(self.P._segmentada_con_grandqc(d, "P-HE"))
            with open(ruta, "w") as f:
                json.dump({"grandqc": None}, f)
            self.assertFalse(self.P._segmentada_con_grandqc(d, "P-HE"))
            with open(ruta, "w") as f:
                json.dump({"grandqc": {"carga": False, "motivo": "x"}}, f)
            self.assertTrue(self.P._segmentada_con_grandqc(d, "P-HE"))
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ── F1.1-F1.4: ingesta, lector único, identidad (sin datos reales: PII y TIFF sintéticos) ──
# Nombres de fichero con la FORMA de los del zip y PII inventada (nada de la titular real).
TITULAR_SINT = {"nombre": "Ramona", "apellidos": ["Quintanilla", "Esteve"]}
NOMBRES_SINT = {
    "2. Pyramid Tiff/1. IH/RE.tif": "P-RE",
    "2. Pyramid Tiff/1. IH/RP.tif": "P-RP",
    "2. Pyramid Tiff/1. IH/REC_ANDRO.tif": "P-RA",
    "2. Pyramid Tiff/1. IH/HER2, MAMA IH.tif": "P-HER2",
    "2. Pyramid Tiff/1. IH/HER2, NEG (RamonaQuintanila extra Esteve).tif": "P-HER2NEG",
    "2. Pyramid Tiff/1. IH/KI67.tif": "P-KI67",
    "2. Pyramid Tiff/1. IH/CK19.tif": "P-CK19",
    "2. Pyramid Tiff/1. IH/AE1AE3 (Ramona Quintanilla Esteve).tif": "P-AE1AE3",
    "2. Pyramid Tiff/1. IH/p63 (Nota de corte).tif": "P-P63",
    "2. Pyramid Tiff/1. IH/SINAPTOF.tif": "P-SYN",
    "2. Pyramid Tiff/1. IH/CROMOG.tif": "P-CHGA",
    "2. Pyramid Tiff/1. IH/{{DIANA3}}.tif": "P-{{DIANA3}}",
    "2. Pyramid Tiff/2. H&E/99B7654321-A1 (ramona quintanilla esteve).tif": "P-HE",
    "2. Pyramid Tiff/2. H&E/98B1234567-A1 A2-1 (Bone_1).tif": "B-HE-1",
    "2. Pyramid Tiff/2. H&E/98B12345067-A1 A2-2 (Bone_2).tif": "B-HE-2",
}


class IngestaNombres(unittest.TestCase):
    def setUp(self):
        import laminillas_ingesta as I
        self.I = I

    def test_esqueleto_no_deja_pasar_pii(self):
        for n in NOMBRES_SINT:
            e = self.I.esqueleto(n)
            for trozo in ("Ramona", "ramona", "Quintan", "Esteve", "7654321", "1234567",
                          "12345067", "Nota"):
                self.assertNotIn(trozo, e, (n, e))
        self.assertIn("KI67", self.I.esqueleto("2. Pyramid Tiff/1. IH/KI67.tif"))
        self.assertIn("A2-1", self.I.esqueleto("98B1234567-A1 A2-1 (Bone_1).tif"))
        self.assertIn("<D7>", self.I.esqueleto("98B1234567-A1.tif"))

    def test_clasifica_las_15_y_nada_mas(self):
        vistos = {self.I.clasifica(n): n for n in NOMBRES_SINT}
        self.assertEqual({self.I.clasifica(n) for n in NOMBRES_SINT}, set(self.I.OPACOS))
        for n, op in NOMBRES_SINT.items():
            self.assertEqual(self.I.clasifica(n), op, n)
        self.assertEqual(len(vistos), 15)
        self.assertIsNone(self.I.clasifica("1. Raw Tiff/IH Ramona x/RE.tif"))
        self.assertIsNone(self.I.clasifica("Presentacion Ramona.pptx"))
        self.assertIsNone(self.I.clasifica("2. Pyramid Tiff/1. IH/"))
        with self.assertRaises(ValueError) as cm:
            self.I.clasifica("2. Pyramid Tiff/1. IH/DESCONOCIDO Ramona.tif")
        self.assertNotIn("Ramona", str(cm.exception))        # el error lleva el esqueleto

    def test_senales_pii_y_erratas(self):
        s = self.I.senales_pii
        a = s("AE1AE3 (Ramona Quintanilla Esteve).tif", TITULAR_SINT)
        self.assertEqual((a["nombre"], a["errata_distancia"]), ("exacto", None))
        b = s("HER2, NEG (RamonaQuintanila extra Esteve).tif", TITULAR_SINT)
        self.assertEqual(b["nombre"], "exacto")               # «Ramona» y «Esteve» exactos…
        self.assertEqual(b["errata_distancia"], 1)            # …pero la errata no se tapa
        c = s("KI67.tif", TITULAR_SINT)
        self.assertEqual((c["nombre"], c["accesion"]), ("no", False))
        d = s("98B12345067-A1 A2-2 (Bone_2).tif", TITULAR_SINT)
        self.assertEqual((d["accesion"], d["cifras_accesion"]), (True, 8))
        self.assertFalse(s("HER2, MAMA IH.tif", TITULAR_SINT)["iniciales"])
        self.assertTrue(s("HER2, MAMA RQ.tif", TITULAR_SINT)["iniciales"])

    def test_tandas_sin_numeros_identificables(self):
        import caso_publico
        texto = json.dumps(self.I.TANDAS, ensure_ascii=False)
        self.assertIsNone(caso_publico._RE_CODIGO_AP.search(texto))
        self.assertNotRegex(texto, r"\d{5,}")

    @unittest.skipUnless(os.path.exists(VENV_PAT), "sin venv patologia")
    def test_eslabon1_rotacion_espejo_y_cierre(self):
        r = subprocess.run([VENV_PAT, "-c", ESLABON, TOOLS], capture_output=True, text=True,
                           timeout=600)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        o = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertGreater(o["rot"]["directo"]["iou"], 0.9)
        self.assertLess(abs(o["ang_err"]), 1.5)
        self.assertGreater(o["rot"]["directo"]["iou"] - o["rot"]["espejo"]["iou"], 0.1)
        self.assertGreater(o["esp"]["espejo"]["iou"], o["esp"]["directo"]["iou"])
        self.assertEqual(o["grupo_pasa"], {"a": True, "b": True, "c": True, "z": False})
        self.assertEqual(o["cierre0"], 0.0)
        self.assertFalse(o["a_con_cierre_10"])

    def test_exporta_por_ventanilla_registrado(self):
        import laminillas_ventanilla as V
        import lector_clinico as L
        c = V.CONF["laminillas_exporta"]
        self.assertEqual((c["script"], c["perfil"], c["modo"]),
                         ("tools/laminillas_exporta.py", "exporta", None))
        self.assertIn("laminillas_exporta", L.LAMINILLAS)
        self.assertIsNone(V.valida_args(["P-HE", "tiff"]))

    def test_exporta_no_salta_el_paso_1bis(self):
        """Una lámina con `n1_apta` distinto de True no entra en N1, ni fuera de la ventanilla; y
        con `n1_apta` True pero de una revisión que no es la vigente (r3, u otro exporta_n1), tampoco."""
        d = tempfile.mkdtemp(prefix="lam-exp-")
        try:
            vieja = {"version": "r3", "exporta_n1_sha256": "0" * 64}
            with open(os.path.join(d, "manifiesto.json"), "w") as f:
                json.dump({"laminas": {"P-RA": {"fichero": "P-RA.tif", "sha256": "0" * 64,
                                                "n1_apta": False},
                                       "P-RP": {"fichero": "P-RP.tif", "sha256": "0" * 64},
                                       "P-RE": {"fichero": "P-RE.tif", "sha256": "0" * 64,
                                                "n1_apta": True, "cristal": vieja}}}, f)
            cod = ("import sys, os, json; sys.path.insert(0, %r); import laminillas_exporta as X\n"
                   "out = {}\n"
                   "for op, env in (('P-RA', '1'), ('P-RP', '1'), ('P-RA', '0'), ('P-RE', '1')):\n"
                   "    os.environ['BTP_VENTANILLA'] = env\n"
                   "    try:\n"
                   "        X.exporta_sesion(op, 'png', base=%r); out[op + env] = 'exporta'\n"
                   "    except X.PuertaCerrada as e:\n"
                   "        out[op + env] = str(e)\n"
                   "print(json.dumps(out))\n" % (TOOLS, d))
            r = subprocess.run([sys.executable, "-c", cod], capture_output=True, text=True,
                               env=dict(os.environ, BTP_N1_DIR=os.path.join(d, "n1")), timeout=120)
            if r.returncode != 0:
                self.skipTest("no carga exporta_n1 aquí: %s" % r.stderr.strip()[-120:])
            o = json.loads(r.stdout.strip().splitlines()[-1])
            if "diccionario" in o["P-RA1"]:
                self.skipTest("sin overlays de la Puerta en esta máquina")
            self.assertIn("1-bis", o["P-RA1"])
            self.assertIn("1-bis", o["P-RP1"])                  # sin revisión = no apta
            self.assertIn("ventanilla", o["P-RA0"])
            self.assertIn("no es la vigente", o["P-RE1"])       # revisión de otra Puerta
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_revision_del_cristal_misma_version_en_ingesta_y_exporta(self):
        import laminillas_ingesta as I
        with open(os.path.join(TOOLS, "laminillas_exporta.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('REVISION_CRISTAL = "%s"' % I.REVISION_CRISTAL, src)


# ── Revisión del cristal r5 y retirada de N1 (2-oct-26) ──────────────────────────────────────
# Con la Puerta de verdad (overlays de esta máquina; sin ellos, skip) y nada que salga de aquí:
# N1 y SESION son temporales, las imágenes son sintéticas y nada imprime lo que lee el OCR.
REPLICA = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])
d = sys.argv[3]
import exporta_n1 as E
try:
    E.puerta.exigir_diccionario()
except E.PuertaCerrada:
    print(json.dumps("sin diccionario")); sys.exit(0)
import laminillas_ingesta as I
from test_laminillas_n1 import escribe_tiff, _piramide_coherente, _textura, _texto_con_alto
base = _textura(2048, 1536, 5)
niveles = _piramide_coherente(base)
x4 = niveles[1][2].copy()                     # rótulo SOLO en el ×4: OCR del ×4 y coherencia
x4.paste(_texto_con_alto("QWERTY", 40, margen=6), (40, 40))
niveles[1] = (niveles[1][0], niveles[1][1], x4)
ruta = os.path.join(d, "P-KI67.tif")
escribe_tiff(ruta, niveles)
loc = (lambda *a: "vidrio (>200 µm del tejido)", lambda *a: 0.0)
fuentes, coh, mot = I._cristal_tiff(d, "P-KI67", "P-KI67.tif", loc, 0.2506)
t = E.Tiff(ruta)
try:
    ref = E._motivos_cristal_tiff(t)
finally:
    t.f.close()
png = base.resize((1024, 768))
png.paste(_texto_con_alto("ZXQW", 40, color=(20, 20, 20), fondo=(240, 240, 240), margen=8), (300, 300))
dp = I._revisa_imagen(png, E.ESCALAS_PNG, True, lambda x, y: (2 * x, 2 * y), loc, 0.2506)
ref_png = E.revisar_cristal(png, E.ESCALAS_PNG, con_puerta=True)
volcado = json.dumps([fuentes, coh, dp])
print(json.dumps({"mot": mot, "ref": ref, "mot_png": dp["motivos"], "ref_png": ref_png,
                  "coh": coh, "n_png": dp["n_palabras"], "donde_png": dp["donde"],
                  "texto_en_detalle": any(w in volcado for w in ("QWERTY", "ZXQW"))}))
"""

RETIRA = r"""
import hashlib, json, os, sys
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])
ses = sys.argv[3]
os.environ["BTP_VENTANILLA"] = "1"
import exporta_n1 as E
try:
    E.puerta.exigir_diccionario()
except E.PuertaCerrada:
    print(json.dumps("sin diccionario")); sys.exit(0)
import laminillas_exporta as X
from test_laminillas_n1 import _textura
os.makedirs(os.path.join(ses, "revision"))
_textura(800, 600, 11).save(os.path.join(ses, "revision", "P-RP__thumbnail.png"))
_textura(1024, 1024, 12).save(os.path.join(ses, "revision", "P-RP__x8.png"))
sha_e = hashlib.sha256(open(os.path.join(sys.argv[1], "exporta_n1.py"), "rb").read()).hexdigest()
rev = {"miniatura": {"fichero": "revision/P-RP__thumbnail.png", "mpp": 30.0},
       "campo_x8": {"fichero": "revision/P-RP__x8.png", "mpp": 2.0044}}
json.dump({"laminas": {"P-RP": {"fichero": "P-RP.tif", "sha256": "0" * 64, "n1_apta": True,
                                "cristal": {"version": X.REVISION_CRISTAL, "exporta_n1_sha256": sha_e},
                                "revision": rev}}}, open(os.path.join(ses, "manifiesto.json"), "w"))
n1 = E.puerta.n1_dir()
out = {}
def intenta(clave, f, *a, **k):
    try:
        r = f(*a, **k); out[clave] = "ok"; return r
    except E.PuertaCerrada as e:
        out[clave] = "🛑 " + str(e)
intenta("exporta", X.exporta_sesion, "P-RP", "png", base=ses)
out["verifica1"] = X.verifica_n1(base=ses)
_textura(800, 600, 13).save(os.path.join(ses, "revision", "P-RP__thumbnail.png"))   # PNG nuevo
out["verifica2"] = X.verifica_n1(base=ses)
intenta("motivo_malo", X.retira_n1, "P-RP", "thumbnail", "porque-si")
intenta("retira", X.retira_n1, "P-RP", "thumbnail", "rayas")
man = json.load(open(os.path.join(n1, "manifiesto.json")))
out["en_ficheros"] = "P-RP__thumbnail.png" in man["ficheros"]
out["retirados"] = man.get("retirados")
out["movido"] = os.path.isfile(os.path.join(n1, "_retirados", "P-RP__thumbnail.png"))
nota = open(os.path.join(n1, "_retirados", "P-RP__thumbnail.png.retirada.txt")).read()
out["nota"] = [ln.split(":")[0] for ln in nota.splitlines()]
out["nota_motivo"] = "ad73a9d" in nota
out["raiz_libre"] = not os.path.exists(os.path.join(n1, "P-RP__thumbnail.png"))
intenta("reexporta", X.exporta_sesion, "P-RP", "png", base=ses, nivel="thumbnail")
out["verifica3"] = X.verifica_n1(base=ses)
intenta("retira_otra_vez", X.retira_n1, "P-RP", "thumbnail", "rayas")
intenta("retira_x8", X.retira_n1, "P-RP", "x8", "puerta")
out["x8_en_ficheros"] = any("__x8__" in n for n in json.load(open(os.path.join(n1, "manifiesto.json")))["ficheros"])
print(json.dumps(out))
"""


COHERENCIA_APARTE = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])
d = sys.argv[3]
import exporta_n1 as E
try:
    E.puerta.exigir_diccionario()
except E.PuertaCerrada:
    print(json.dumps("sin diccionario")); sys.exit(0)
import numpy as np
import laminillas_comun as C, laminillas_ingesta as I
from test_laminillas_n1 import escribe_tiff, _piramide_coherente, _textura
base = _textura(2048, 1536, 5)
niv = _piramide_coherente(base)
x4 = niv[1][2].copy()                         # una mancha lisa SOLO en el ×4: incoherente, sin texto
x4.paste((150, 110, 160), (200, 120, 300, 200))
niv[1] = (niv[1][0], niv[1][1], x4)
escribe_tiff(os.path.join(d, "P-KI67.tif"), niv)
os.makedirs(os.path.join(d, "mascaras")); os.makedirs(os.path.join(d, "revision"))
m = np.zeros((48, 64), bool); m[5:45, 5:60] = True
np.savez(os.path.join(d, "mascaras", "P-KI67-8um.npz"), mascara=m, dentro=np.ones_like(m), escala_l0=32.0)
base.resize((1024, 768)).save(os.path.join(d, "revision", "P-KI67__thumbnail.png"))
base.crop((0, 0, 1024, 1024)).save(os.path.join(d, "revision", "P-KI67__x8.png"))
rev = {"miniatura": {"fichero": "revision/P-KI67__thumbnail.png", "mpp": 0.5012},
       "campo_x8": {"fichero": "revision/P-KI67__x8.png", "mpp": 2.0048, "x_l0": 0, "y_l0": 0}}
C.escribe_json(C.ruta_manifiesto(d), {"version": 1, "laminas": {"P-KI67": {
    "fichero": "P-KI67.tif", "mpp": 0.2506, "n1_apta": True, "revision": rev}}})
r = I.cristal(d, "P-KI67")
det = I.coherencia_detalle(d, "P-KI67")
print(json.dumps({"salta": r["salta"], "n1_apta": C.lee_manifiesto(d)["laminas"]["P-KI67"]["n1_apta"],
                  "coh": r["coherencia_salta"], "tiff": r["tiff_sin_1bis"], "version": r["version"],
                  "motivos_tiff": r["motivos_tiff"], "pasan": det["IFD1_x4"]["pasan_umbral"],
                  "donde": det["IFD1_x4"]["donde_pasan"], "l0": det["IFD0_x1"]["pasan_umbral"],
                  "pil_turbo": det["x8_pillow_vs_turbo"]}))
"""


class CristalR4YRetirada(unittest.TestCase):
    """La revisión r5 decide lo mismo que la Puerta de N1 (réplica fijada contra exporta_n1) y
    guarda dónde cae lo leído sin guardarlo; la retirada de N1 mueve, anota y libera el nombre."""

    def _corre(self, script, d, py=sys.executable):
        r = subprocess.run([py, "-c", script, TOOLS, os.path.join(RAIZ, "tests"), d],
                           capture_output=True, text=True, timeout=900,
                           env=dict(os.environ, BTP_N1_DIR=os.path.join(d, "n1")))
        if r.returncode != 0:                 # PIL, tesseract o libturbojpeg que faltan: ROJO
            self.fail(r.stdout[-300:] + r.stderr[-1500:])
        o = json.loads(r.stdout.strip().splitlines()[-1])
        if o == "sin diccionario":
            self.skipTest("sin overlays de la Puerta en esta máquina")
        return o

    def test_replica_de_la_puerta(self):
        d = tempfile.mkdtemp(prefix="lam-r4-")
        try:
            o = self._corre(REPLICA, d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(o["mot"], o["ref"])                  # TIFF: los mismos motivos, en orden
        self.assertEqual(o["mot_png"], o["ref_png"])          # PNG: idem
        self.assertTrue(o["ref"] and o["ref_png"])            # y no por vacíos los dos
        self.assertTrue(o["coh"]["IFD1_x4"]["salta"], o["coh"])    # el rótulo solo en el ×4
        self.assertFalse(o["coh"]["IFD0_x1"]["salta"], o["coh"])   # L0 se parece al ×8
        self.assertGreaterEqual(o["n_png"], 1)
        self.assertEqual(o["donde_png"], {"vidrio (>200 µm del tejido)": o["n_png"]})
        self.assertFalse(o["texto_en_detalle"])                # nunca la cadena leída

    @unittest.skipUnless(os.path.exists(VENV_PAT), "sin venv patologia")
    def test_coherencia_aparte_de_n1_apta(self):
        """r5: un nivel que no se parece al ×8 sin una palabra leída no cierra la lámina (n1_apta
        sigue True), pero se anota y el TIFF pediría el paso 1-bis; el detalle la localiza."""
        d = tempfile.mkdtemp(prefix="lam-coh-")
        try:
            o = self._corre(COHERENCIA_APARTE, d, py=VENV_PAT)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(o["version"], "r5")
        self.assertFalse(o["salta"], o)
        self.assertTrue(o["n1_apta"], o)
        self.assertTrue(o["coh"], o)
        self.assertFalse(o["tiff"], o)
        self.assertTrue(any("no se parece al ×8" in m for m in o["motivos_tiff"]), o)
        self.assertGreater(o["pasan"], 0)
        self.assertEqual(o["l0"], 0)                          # L0 sí se parece al ×8
        self.assertEqual(set(o["donde"]), {"dentro del tejido"})
        self.assertEqual(o["pil_turbo"], 0)                   # las dos vías de decodificación

    def test_retira_y_reexporta(self):
        d = tempfile.mkdtemp(prefix="lam-ret-")
        try:
            o = self._corre(RETIRA, d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(o["exporta"], "ok", o)
        self.assertEqual([(n, v, p) for n, v, p in o["verifica1"]],
                         [("P-RP__thumbnail.png", "pasa", 0), ("P-RP__x8__mpp2.0044.png", "pasa", 0)])
        self.assertGreater(o["verifica2"][0][2], 0)            # el PNG de SESION ya es otro
        self.assertIn("motivo de retirada", o["motivo_malo"])
        self.assertEqual(o["retira"], "ok", o)
        self.assertFalse(o["en_ficheros"])
        self.assertEqual([r["motivo"] for r in o["retirados"]["P-RP__thumbnail.png"]], ["rayas"])
        self.assertTrue(o["movido"] and o["raiz_libre"])
        self.assertEqual(o["nota"], ["fecha", "motivo", "sha256", "via"])
        self.assertTrue(o["nota_motivo"])
        self.assertEqual(o["reexporta"], "ok", o)              # el nombre quedó libre
        self.assertEqual(o["verifica3"][0][1:], ["pasa", 0])
        self.assertIn("ya hay un", o["retira_otra_vez"])       # no pisa un retirado
        self.assertEqual(o["retira_x8"], "ok", o)
        self.assertFalse(o["x8_en_ficheros"])


ESLABON = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import numpy as np
from scipy import ndimage as ndi
import laminillas_ingesta as I
A = np.zeros((160, 140), bool)
A[20:60, 15:110] = True
A[80:150, 30:60] = True
A[110:130, 80:125] = True
B = ndi.rotate(A.astype(float), 30, reshape=True, order=0) > 0.5
rot = I.busca_par(A, B, paso=2.0, fino=0.5)
esp = I.busca_par(A, np.fliplr(B), paso=2.0, fino=0.5)
par = lambda d, e, ang: {"directo": {"iou": d, "angulo": ang}, "espejo": {"iou": e, "angulo": 0}}
ops = ("a", "b", "c", "z")
pares = {("a", "b"): par(0.7, 0.3, 10), ("b", "c"): par(0.6, 0.3, 20),
         ("a", "c"): par(0.65, 0.3, 30), ("a", "z"): par(0.2, 0.15, 0),
         ("b", "z"): par(0.15, 0.1, 0), ("c", "z"): par(0.5, 0.45, 0)}
por, cierres, _ = I.evalua_grupo(pares, ops)
pares[("a", "c")] = par(0.65, 0.3, 40)
por2, _, _ = I.evalua_grupo(pares, ops)
print(json.dumps({"rot": rot, "esp": esp, "ang_err": I._envuelve(rot["directo"]["angulo"] + 30),
                  "grupo_pasa": {k: v["pasa"] for k, v in por.items()},
                  "cierre0": round(cierres[0][1], 6), "a_con_cierre_10": por2["a"]["pasa"]}))
"""


PIXELES = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
d = sys.argv[2]
os.environ["BTP_VENTANILLA"] = "1"
import numpy as np, tifffile
from skimage.transform import downscale_local_mean
import laminillas_comun as C, laminillas_lector as L, laminillas_ingesta as I
rng = np.random.default_rng(0)
W, H = 6144, 4096
img = np.full((H, W, 3), 244, np.uint8)
yy, xx = np.mgrid[0:H, 0:W]
tej = ((xx - 3600) / 1800.0) ** 2 + ((yy - 2000) / 1300.0) ** 2 < 1
img[tej] = (200, 150, 190)
img = np.clip(img.astype(np.int16) + rng.integers(-4, 5, img.shape), 0, 255).astype(np.uint8)
img[:, :1024] = 255                                  # relleno (zona no escaneada)
from PIL import Image as _I, ImageDraw as _D, ImageFont as _F
_p = _I.fromarray(img)
try:
    _f = _F.load_default(size=420)
except TypeError:
    _f = _F.load_default()
_D.Draw(_p).text((1150, 60), "ZXQW", fill=(25, 25, 25), font=_f)   # rótulo sintético en vidrio
img = np.asarray(_p).copy()
ruta = os.path.join(d, "P-KI67.tif")
with tifffile.TiffWriter(ruta, bigtiff=True) as tw:
    for f in (1, 4, 8):
        niv = img if f == 1 else downscale_local_mean(img, (f, f, 1)).astype(np.uint8)
        kw = dict(tile=(512, 512), compression="jpeg", compressionargs={"level": 90},
                  photometric="rgb", metadata=None)
        if f == 1:
            kw.update(resolution=(1e4 / 0.2506, 1e4 / 0.2506), resolutionunit="CENTIMETER")
        else:
            kw.update(subfiletype=1)
        tw.write(niv, **kw)
C.escribe_json(C.ruta_manifiesto(d), {"version": 1, "laminas": {"P-KI67": {
    "fichero": "P-KI67.tif", "sha256": C.sha256_fichero(ruta)}}})
L.configura(d)
out = {}
lam = L.abre("P-KI67")
out["niveles"] = [n[0] for n in lam.niveles]
out["mpp"] = lam.mpp_l0
a = L.lee_region(lam, 0.2506, 2048, 2048, 64, 64)
b = L.lee_region(lam, 0.5, 2048, 2048, 32, 32)
out["area_ok"] = int(np.abs(a.reshape(32, 2, 32, 2, 3).astype(float).mean(axis=(1, 3)) - b).max())
out["forma_mpp32"] = list(L.lee_region(lam, 32, 0, 0, 40, 30).shape)
out["fuera"] = L.lee_region(lam, 0.2506, W - 10, H - 10, 20, 20)[15, 15].tolist()
try:
    L.lee_region(lam, 0.1, 0, 0, 10, 10); out["fino"] = "no lanza"
except ValueError:
    out["fino"] = "lanza"
I.metadatos(d, "P-KI67")
out["zona"] = I.zona(d, "P-KI67")
out["i0"] = I.i0(d, "P-KI67")
out["tejido"] = I.tejido(d, "P-KI67")
out["lector"] = I.prueba_lector(d, "P-KI67")
out["revision"] = I.revision(d, "P-KI67")
det = I.cristal_detalle(d, ["P-KI67"])["laminas"]["P-KI67"]
out["detalle"] = {"n": det["n_palabras"], "donde": det["donde"],
                  "frag": [h["fragmento_mm2"] for h in det["palabras"]],
                  "conf": det["confianza_min_mediana_max"]}
out["texto_en_json"] = "ZXQW" in open(os.path.join(d, "cristal_detalle.json")).read()
out["zona_api"] = L.zona_escaneada("P-KI67").area
out["i0_api"] = L.i0_local("P-KI67")(3000, 3500).tolist()
json.dump(out, open(os.path.join(d, "out.json"), "w"))
"""


@unittest.skipUnless(os.path.exists(VENV_PAT), "sin venv patologia")
class IngestaPixelesSinteticos(unittest.TestCase):
    """Lector único, zona escaneada, I0, tejido, prueba del lector y PNG sobre un TIFF sintético
    (BigTIFF, teselas 512 JPEG, ×1/×4/×8, franja de relleno 255)."""

    @classmethod
    def setUpClass(cls):
        cls.d = os.path.realpath(tempfile.mkdtemp(prefix="lam-ingesta-"))
        r = subprocess.run([VENV_PAT, "-c", PIXELES, TOOLS, cls.d], capture_output=True,
                           text=True, timeout=600)
        if r.returncode != 0:
            raise AssertionError(r.stderr[-1500:])
        with open(os.path.join(cls.d, "out.json")) as f:
            cls.o = json.load(f)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_lector(self):
        o = self.o
        self.assertEqual(o["niveles"], [1, 4, 8])
        self.assertAlmostEqual(o["mpp"], 0.2506, places=4)
        self.assertLessEqual(o["area_ok"], 1)                # 0,5 µm = media 2×2 de L0
        self.assertEqual(o["forma_mpp32"], [30, 40, 3])
        self.assertEqual(o["fuera"], [255, 255, 255])        # fuera de imagen = relleno
        self.assertEqual(o["fino"], "lanza")                 # más fino que L0: no sobremuestrea

    def test_zona_escaneada_excluye_el_relleno(self):
        z = self.o["zona"]
        self.assertEqual(z["teselas_relleno"], 2 * 8)        # 2 columnas × 8 filas de teselas
        self.assertEqual(z["teselas_escaneadas"], 10 * 8)
        self.assertAlmostEqual(self.o["zona_api"], (6144 - 1024) * 4096, delta=1)
        self.assertIn("16/16", z["confirmacion"])

    def test_i0_tejido_y_prueba_del_lector(self):
        self.assertTrue(all(abs(v - 244) <= 2 for v in self.o["i0"]["vidrio_referencia_rgb"]))
        self.assertTrue(all(abs(v - 244) <= 2 for v in self.o["i0_api"]))
        self.assertFalse(self.o["i0"]["franja"])
        area = 3.14159 * 1800 * 1300 * 0.2506 ** 2 / 1e6        # elipse de tejido, mm²
        t = self.o["tejido"]["principal"]
        self.assertAlmostEqual(t["mm2"], area, delta=0.15 * area)
        self.assertEqual(t["fragmentos_gt_0_2mm2"], 1)
        p = self.o["lector"]
        self.assertTrue(p["a_ok"] and p["b_ok"])
        self.assertEqual(p["n_teselas"], 80)
        rv = self.o["revision"]
        self.assertLessEqual(max(rv["miniatura"]["ancho"], rv["miniatura"]["alto"]), 1024)
        self.assertLessEqual(max(rv["campo_x8"]["ancho"], rv["campo_x8"]["alto"]), 1024)

    def test_detalle_del_cristal_localiza_sin_guardar_el_texto(self):
        o = self.o["detalle"]
        self.assertGreaterEqual(o["n"], 1, o)                  # el rótulo sintético se ve
        self.assertGreaterEqual(o["conf"][0], 60)
        self.assertNotIn("relleno (fuera de la zona escaneada)", o["donde"])
        self.assertTrue(all(f < 0.2 for f in o["frag"]), o)   # mancha de tinta, no un fragmento
        self.assertFalse(self.o["texto_en_json"])                # nunca la cadena leída

    def test_revision_cabe_en_1024(self):
        rv = self.o["revision"]
        self.assertLessEqual(max(rv["campo_x8"]["ancho"], rv["campo_x8"]["alto"]), 1024)


# ── Puerta del piloto tras (0): `laminillas_proc analisis <orden>` (puntos 4-6 del plan) ────────
class PilotoOrdenes(unittest.TestCase):
    """Lista cerrada de órdenes, memoria por orden y por bandera, umbral sellado, pares del
    registro y despacho sin datos (stdlib: corre con el python del sistema)."""

    def setUp(self):
        import laminillas_proc as P
        import laminillas_ventanilla as V
        self.P, self.V = P, V

    def test_ordenes_de_la_ventanilla_son_las_del_procesador(self):
        c = self.V.CONF["laminillas"]
        self.assertEqual((c["script"], c["modo"], c["venv"], c["perfil"], c["datos"]),
                         ("tools/laminillas_proc.py", "analisis", "patologia", "analisis", True))
        self.assertEqual(tuple(c["ordenes"]), self.P.ORDENES)
        for o in self.P.ORDENES:
            self.assertIsNone(self.V.valida_orden(c, [o]), o)
            self.assertIsNone(self.V.valida_args([o, "P-KI67", "P-CK19", "--valis"]), o)
        for malo in ([], ["borra"], ["P-KI67"], ["analisis"], ["ingesta"]):
            self.assertIsNotNone(self.V.valida_orden(c, malo), malo)

    def test_memoria_por_orden_bandera_y_sello(self):
        import laminillas_sello as SL
        c = self.V.CONF["laminillas"]
        self.assertEqual(self.V.mem_de(c, ["tribunal-listo"]), 0)       # solo JSON: sin cerrojo
        self.assertEqual(self.V.mem_de(c, ["piloto-i"]), 5)             # InstanSeg (plan: 5)
        self.assertEqual(self.V.mem_de(c, ["registro-par", "P-KI67", "P-CK19"]), 5)
        self.assertEqual(self.V.mem_de(c, ["registro-par", "P-KI67", "P-CK19", "--valis"]), 9)
        d = tempfile.mkdtemp()
        try:
            self.assertIsNone(self.V.mem_sellada(d, "laminillas"))       # sin sello: el de partida
            SL.sella(os.path.join(d, SL.FICHERO),
                     {"memoria": {"umbral_gb": {"laminillas": 7.4}}}, "2026-10-02")
            self.assertAlmostEqual(self.V.mem_sellada(d, "laminillas"), 7.4)
            self.assertIsNone(self.V.mem_sellada(d, "laminillas_qc"))
            with open(os.path.join(d, SL.FICHERO), "r+", encoding="utf-8") as f:
                t = f.read().replace("7.4", "1.0")
                f.seek(0)
                f.write(t)
                f.truncate()
            self.assertIsNone(self.V.mem_sellada(d, "laminillas"))       # tocado: no vale
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_ejecuta_rechaza_orden_antes_de_lanzar(self):
        lanzado = []
        orig = self.V.subprocess.call
        self.V.subprocess.call = lambda *a, **k: lanzado.append(a) or 0
        d = tempfile.mkdtemp()
        try:
            for a in ([], ["borra"], ["piloto-i", "/tmp/x"], ["metricas", "12B0000001"]):
                self.assertEqual(self.V.ejecuta("test", "laminillas", a, sesion=d,
                                                log=lambda m: None), self.V.COD_ARGS, a)
        finally:
            self.V.subprocess.call = orig
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(lanzado, [])

    def test_pares_del_registro(self):
        vp = self.P.valida_par
        self.assertEqual(vp("P-KI67", "P-CK19"), ("P-KI67", "P-CK19", "ii"))
        self.assertEqual(vp("P-CK19", "P-KI67"), ("P-KI67", "P-CK19", "ii"))
        self.assertEqual(vp("P-HER2NEG", "P-CK19"), ("P-HER2NEG", "P-CK19", "ii-bis"))
        self.assertEqual(vp("P-HER2", "P-CK19"), ("P-HER2", "P-CK19", "ii-bis"))
        self.assertEqual(vp("P-CK19", "P-HE"), ("P-HE", "P-CK19", "iii"))
        self.assertEqual(vp("P-KI67", "P-HE"), ("P-HE", "P-CK19", "iii"))
        self.assertEqual(vp("P-SYN", "P-CK19"), ("P-SYN", "P-CK19", "extension"))
        for a, b in (("P-KI67", "P-KI67"), ("P-KI67", "P-HER2NEG"), ("P-HE", "P-HER2"),
                     ("P-AE1AE3", "P-CK19"), ("B-HE-1", "P-CK19"), ("P-HE", "P-SYN")):
            with self.assertRaises(ValueError, msg=(a, b)):
                vp(a, b)

    def test_despacho_rechaza_sin_tocar_nada(self):
        d = tempfile.mkdtemp()
        try:
            an = self.P.analisis
            q = dict(log=lambda m: None)
            self.assertEqual(an(d, [], **q), 2)
            self.assertEqual(an(d, ["borra"], **q), 2)
            self.assertEqual(an(d, ["registro-par", "P-KI67"], **q), 2)
            self.assertEqual(an(d, ["metricas", "P-KI67", "--valis"], **q), 2)
            self.assertEqual(an(d, ["piloto-i", "P-CK19"], **q), 2)
            self.assertEqual(an(d, ["metricas", "P-P63"], **q), 2)       # puerta propia
            self.assertEqual(an(d, ["metricas", "P-KI67"], **q), 3)      # sin sello: NO MIDO
            self.assertEqual(os.listdir(d), [])                          # nada escrito
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_ruta_sello_sin_la_nota_caducada(self):
        doc = self.P.ruta_sello.__doc__
        self.assertNotIn("hasta que se alineen", doc)
        self.assertEqual(self.P.ruta_sello("/x"), os.path.join("/x", "congelacion.json"))


class _CalibraFalsa:
    """El contrato de `tools/panel_vision/calibra.py` que usa `tribunal_listo` (sin su código)."""
    PROTOCOLO = "panel-vision/1"
    TAREAS = ("nucleos", "ck19", "registro")

    @staticmethod
    def huella_preguntas():
        return "h1"

    @staticmethod
    def modelos_autorizados(resultados, tarea):
        return list((resultados.get("uso_test") or {}).get(tarea, []))


class TribunalListo(unittest.TestCase):
    """Requisito BLOQUEANTE 5-bis por código: (i) calibración sintética y después N1 real; (ii)
    cada modelo habilitado revisó las capas N1 del piloto de sus tareas; (iii) Gemini solo con
    auditoría «apto» y trust-cloud tecleado, y si falta NO bloquea."""

    def setUp(self):
        import laminillas_proc as P
        self.P = P
        self.d = tempfile.mkdtemp(prefix="lam-tribunal-")
        self.repo = os.path.join(self.d, "repo")
        self.n1 = os.path.join(self.d, "n1")
        self.confiados = set()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _json(self, ruta, obj):
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def _calib(self, sint=True, real=True, uso=None, puntuado_real="2026-10-03T10:00:00"):
        base = {"protocolo": "panel-vision/1", "huella_preguntas": "h1",
                "modelos": {"ollama:qwen@x": {}}}
        if sint:
            self._json(os.path.join(self.repo, self.P.CALIB_SINTETICA),
                       dict(base, sha_conjunto="sint", puntuado="2026-10-02T10:00:00"))
        if real:
            self._json(os.path.join(self.n1, self.P.N1_PANEL, self.P.CALIB_N1),
                       dict(base, sha_conjunto="real", puntuado=puntuado_real,
                            uso_test=uso or {"nucleos": ["ollama:qwen@x"]}))

    def _capas(self, tareas):
        ficheros = {}
        capas = {}
        for t, fs in tareas.items():
            capas[t] = fs
            for f in fs:
                ficheros[f] = {"sha256": "sha-" + f}
        self._json(os.path.join(self.n1, "manifiesto.json"), {"ficheros": ficheros})
        self._json(os.path.join(self.n1, self.P.N1_PANEL, self.P.CAPAS_PILOTO), {"tareas": capas})

    def _revisa(self, *filas):
        r = os.path.join(self.n1, self.P.N1_PANEL, self.P.REVISIONES)
        os.makedirs(os.path.dirname(r), exist_ok=True)
        with open(r, "a", encoding="utf-8") as f:
            for clave, tarea, fich, *err in filas:
                f.write(json.dumps({"clave": clave, "tarea": tarea, "fichero": fich,
                                    "sha256": "sha-" + fich, "error": err[0] if err else None})
                        + "\n")

    def _listo(self):
        return self.P.tribunal_listo(repo=self.repo, n1=self.n1, calibra=_CalibraFalsa,
                                     confia=lambda d: d in self.confiados)

    def test_i_sin_calibracion_sintetica_bloquea(self):
        self._calib(sint=False)
        r = self._listo()
        self.assertFalse(r["listo"])
        self.assertTrue(any("(i) calibración sintética" in f for f in r["faltan"]), r["faltan"])
        self.assertFalse(r["ii"].get("evaluado", True))

    def test_i_sin_calibracion_n1_real_bloquea(self):
        self._calib(real=False)
        r = self._listo()
        self.assertFalse(r["listo"])
        self.assertTrue(any("N1 reales" in f for f in r["faltan"]), r["faltan"])

    def test_i_n1_real_antes_o_igual_que_la_sintetica_bloquea(self):
        self._calib(puntuado_real="2026-10-01T00:00:00")
        self.assertFalse(self._listo()["listo"])
        shutil.rmtree(self.d)
        self._calib()
        ruta = os.path.join(self.n1, self.P.N1_PANEL, self.P.CALIB_N1)
        with open(ruta, encoding="utf-8") as f:
            d = json.load(f)
        d["sha_conjunto"] = "sint"                                   # el mismo conjunto
        self._json(ruta, d)
        self.assertFalse(self._listo()["listo"])

    def test_i_preguntas_de_otra_version_bloquean(self):
        self._calib()
        ruta = os.path.join(self.repo, self.P.CALIB_SINTETICA)
        with open(ruta, encoding="utf-8") as f:
            d = json.load(f)
        d["huella_preguntas"] = "otra"
        self._json(ruta, d)
        r = self._listo()
        self.assertFalse(r["listo"])
        self.assertIn("huella", r["i"]["sintetica"]["motivo"])

    def test_ii_falta_una_revision_bloquea_y_completa_pasa(self):
        self._calib(uso={"nucleos": ["ollama:qwen@x"], "ck19": ["ollama:qwen@x", "claude:s@y"]})
        self._capas({"nucleos": ["n_01.png", "n_02.png"], "ck19": ["c_01.png"]})
        self._revisa(("ollama:qwen@x", "nucleos", "n_01.png"), ("ollama:qwen@x", "ck19", "c_01.png"),
                     ("claude:s@y", "ck19", "c_01.png", "timeout"))      # con error: no cuenta
        r = self._listo()
        self.assertFalse(r["listo"])
        faltan = {(x["modelo"], x["tarea"], x["fichero"]) for x in r["ii"]["sin_revisar"]}
        self.assertEqual(faltan, {("ollama:qwen@x", "nucleos", "n_02.png"),
                                  ("claude:s@y", "ck19", "c_01.png")})
        self._revisa(("ollama:qwen@x", "nucleos", "n_02.png"), ("claude:s@y", "ck19", "c_01.png"))
        r = self._listo()
        self.assertTrue(r["listo"], r["faltan"])
        self.assertTrue(any("registro" in a for a in r["avisos"]))        # tarea sin modelo: aviso

    def test_ii_tarea_habilitada_sin_capas_declaradas_bloquea(self):
        self._calib()
        self._capas({"ck19": ["c_01.png"]})
        r = self._listo()
        self.assertFalse(r["listo"])
        self.assertTrue(any("nucleos" in f and "sin capas" in f for f in r["faltan"]), r["faltan"])

    def test_iii_gemini_sin_confianza_no_bloquea_y_se_declara(self):
        self._calib(uso={"nucleos": ["ollama:qwen@x", "gemini:3-pro@z"]})
        self._capas({"nucleos": ["n_01.png"]})
        self._revisa(("ollama:qwen@x", "nucleos", "n_01.png"))
        r = self._listo()
        self.assertTrue(r["listo"], r["faltan"])
        self.assertIn(self.P.GEMINI_NO, r["declaraciones"])
        self.assertEqual(r["ii"]["habilitados"]["nucleos"], ["ollama:qwen@x"])
        # confiado pero sin auditoría «apto»: igual
        self.confiados.add(self.P.DESTINO_GEMINI)
        self.assertIn(self.P.GEMINI_NO, self._listo()["declaraciones"])

    def test_iii_gemini_apto_y_confiado_entra_y_exige_sus_revisiones(self):
        self._calib(uso={"nucleos": ["ollama:qwen@x", "gemini:3-pro@z"]})
        self._capas({"nucleos": ["n_01.png"]})
        self._revisa(("ollama:qwen@x", "nucleos", "n_01.png"))
        self._json(os.path.join(self.repo, self.P.AUDITORIAS), {"gemini": {"veredicto": "apto"}})
        self.confiados.add(self.P.DESTINO_GEMINI)
        r = self._listo()
        self.assertFalse(r["listo"])
        self.assertTrue(r["iii"]["usa_gemini"])
        self.assertNotIn(self.P.GEMINI_NO, r["declaraciones"])
        self._revisa(("gemini:3-pro@z", "nucleos", "n_01.png"))
        self.assertTrue(self._listo()["listo"])

    def test_iii_apto_con_condiciones_solo_entra_con_las_condiciones_cumplidas(self):
        # La auditoría real de Gemini (1-oct-26) es «apto con condiciones»: sin constancia de que
        # {{TITULAR}} las cumplió, Gemini sale del panel y se declara; con ella, entra.
        self._calib(uso={"nucleos": ["ollama:qwen@x", "gemini:3-pro@z"]})
        self._capas({"nucleos": ["n_01.png"]})
        self._revisa(("ollama:qwen@x", "nucleos", "n_01.png"), ("gemini:3-pro@z", "nucleos", "n_01.png"))
        self.confiados.add(self.P.DESTINO_GEMINI)
        aud = os.path.join(self.repo, self.P.AUDITORIAS)
        for pendiente in ({"veredicto": "apto con condiciones"},
                          {"veredicto": "apto con condiciones", "condiciones_cumplidas": False},
                          {"veredicto": "apto con condiciones", "condiciones_cumplidas": "sí"},
                          {"veredicto": "no apto", "condiciones_cumplidas": True}):
            self._json(aud, {"gemini": pendiente})
            r = self._listo()
            self.assertFalse(r["iii"]["usa_gemini"], pendiente)
            self.assertIn(self.P.GEMINI_NO, r["declaraciones"], pendiente)
        self._json(aud, {"gemini": {"veredicto": "Apto con condiciones", "condiciones_cumplidas": True}})
        r = self._listo()
        self.assertTrue(r["iii"]["usa_gemini"])
        self.assertTrue(r["listo"], r["faltan"])

    def test_orden_por_la_ventanilla_da_rc_4_y_deja_informe(self):
        s = os.path.join(self.d, "sesion")
        os.makedirs(s)
        rc = self.P.analisis(s, ["tribunal-listo"], repo=self.repo, n1=self.n1,
                             log=lambda m: None)
        self.assertEqual(rc, 4)
        with open(os.path.join(s, "piloto", "tribunal_listo.json"), encoding="utf-8") as f:
            self.assertFalse(json.load(f)["listo"])

    @unittest.skipUnless(os.path.exists(os.path.join(TOOLS, "panel_vision", "calibra.py")),
                         "sin tools/panel_vision/calibra.py")
    def test_contrato_con_el_calibra_real(self):
        sys.path.insert(0, os.path.join(TOOLS, "panel_vision"))
        import calibra
        for nombre in ("PROTOCOLO", "TAREAS", "huella_preguntas", "modelos_autorizados"):
            self.assertTrue(hasattr(calibra, nombre), nombre)
        self.assertEqual(calibra.modelos_autorizados(
            {"protocolo": calibra.PROTOCOLO, "huella_preguntas": calibra.huella_preguntas(),
             "modelos": {}}, calibra.TAREAS[0]), [])


# Sesión SINTÉTICA de productos (sin píxeles): sello completo, núcleos con epitelio conocido,
# máscara CK19, registro con traslaciones conocidas. Mide con el código de verdad.
PROC_PILOTO = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
base = os.path.realpath(sys.argv[2])
import numpy as np
from shapely.geometry import Point, box
import laminillas_comun as C
import laminillas_proc as P
import laminillas_sello as SL
import laminillas_metricas as MET
import laminillas_segmenta as S
import laminillas_congela as K
import laminillas_registro as R

MPP = 0.25
DIMS = (12000, 8000)
FCS_UM = [box(300, 300, 1500, 1100), box(1700, 1100, 2900, 1900)]
EPI_UM = [box(300, 300, 900, 1100), box(1700, 1100, 2300, 1900)]
T = 0.10
rng = np.random.default_rng(5)
out = {}

def unit(v):
    v = np.asarray(v, float)
    return (v / np.linalg.norm(v)).tolist()

def sella(rige="clasicas"):
    h, d = unit([0.65, 0.70, 0.29]), unit([0.27, 0.57, 0.78])
    reg = {"p999": 0.04, "rige": "suelo_fijo", "rotulos": [], "T": T, "banda": [0.06, 0.15],
           "estado": "ok"}
    fp = {"k": 1, "n": 2000, "fraccion": 0.0005, "ic95": [0.0, 0.002]}
    c = {"vectores": {"H": h, "DAB": d, "tercero": unit(np.cross(h, d))},
         "residuo": {"por_lamina": {n: {"supera": False} for n in K.IHQ_CON_TEJIDO}},
         "precongelacion": {"sha256": "sintetico"}, "parametros": K.parametros(),
         "umbral": {"T": {"nucleo": T, "anillo": T},
                    "banda": {"nucleo": [0.06, 0.15], "anillo": [0.06, 0.15]},
                    "rige": rige, "rotulos": [],
                    "regimenes": {rige: {"nucleo": dict(reg), "anillo": dict(reg)}}},
         "fp_her2": {"nucleo": fp, "anillo": fp}, "registro": K.REGISTRO, "hotspot": K.HOTSPOT,
         "regla_L": K.REGLA_L, "modulo_b": MET.contenido_partida(),
         "semillas": {"maestra": 20261001, "bootstrap": 20261001, "pixeles": 20261101,
                      "galeria_focal": 20261201},
         "medida": {"hscore": {"cortes": ["T", 0.4, 0.6]}, "semilla_galeria_focal": 20261201}}
    return SL.sella(os.path.join(base, SL.FICHERO), c, "2026-10-02")

def M_de(tx, ty):
    return np.array([[1, 0, tx], [0, 1, ty], [0, 0, 1.0]])

def lamina(nombre, M, pct_epi, pct_estroma, anillo_epi=None):
    Minv = np.linalg.inv(M)
    xs, epi = [], []
    for fc, ep in zip(FCS_UM, EPI_UM):
        x0, y0, x1, y1 = fc.bounds
        n = int(fc.area / 1e6 * 3000)
        p = np.column_stack([rng.uniform(x0, x1, n), rng.uniform(y0, y1, n)])
        e = p[:, 0] < ep.bounds[2]
        extra = p[e] + rng.normal(0, 6, (int(e.sum()), 2))
        xs.append(np.vstack([p, extra]))
        epi.append(np.concatenate([e, np.ones(len(extra), bool)]))
    ref, epi = np.vstack(xs), np.concatenate(epi)
    lam = (ref @ Minv[:2, :2].T + Minv[:2, 2]) / MPP
    r = (np.where(epi, 4.2, 2.6) + rng.normal(0, 0.2, len(ref))) / MPP
    pos = rng.random(len(ref)) < np.where(epi, pct_epi, pct_estroma)
    pols = [Point(x, y).buffer(rr, 8) for (x, y), rr in zip(lam, r)]
    cents = S.centroides(pols)
    pxy, offs = S.empaqueta(pols)
    os.makedirs(os.path.join(base, "segmenta"), exist_ok=True)
    np.savez_compressed(os.path.join(base, "segmenta", "nucleos_%s.npz" % nombre),
                        centroide=cents, area_um2=S.areas_um2(pols, MPP), pol_xy=pxy,
                        pol_offs=offs, centroide_desplazada=cents[np.arange(len(cents)) % 400 != 0])
    json.dump({"lamina": nombre, "n": len(pols), "mpp_l0": MPP, "dimensiones_l0": list(DIMS),
               "teselado": {"pasa": True}, "aviso_check_input_tile": False,
               "area_nuclear": S.mediana_area(pols, MPP),
               "acuerdo_area": S.acuerdo_area(np.full(300, 0.95)),
               "pesos": {"sha256": S.INSTANSEG["sha256"], "bytes": S.INSTANSEG["bytes"]}},
              open(os.path.join(base, "segmenta", "nucleos_%s.json" % nombre), "w"))
    C.marca_hecho(base, "segmenta", nombre, productos=["segmenta/nucleos_%s.npz" % nombre,
                                                       "segmenta/nucleos_%s.json" % nombre])
    n = len(pols)
    # positividad en SU compartimento (nuclear: núcleo; citoplasmático: anillo); el otro, fondo
    senal = np.where(pos, 0.45, 0.02) + rng.normal(0, 0.01, n)
    fondo = 0.02 + rng.normal(0, 0.01, n)
    if anillo_epi is not None:
        dab, dab_a = fondo, np.where(epi, anillo_epi, 0.02) + rng.normal(0, 0.01, n)
    elif SL.COMPARTIMENTO.get(nombre) == "anillo":
        dab, dab_a = fondo, senal
    else:
        dab, dab_a = senal, fondo
    prov = S.mascara_provisional(cents, MPP, DIMS)
    rasgos, _ = P.rasgos_morfologicos(pxy, offs, MPP)
    z = np.zeros(n, bool)
    arr = dict(centroide=cents, area_um2=S.areas_um2(pols, MPP), od_nucleo=np.zeros((n, 3)),
               od_anillo=np.zeros((n, 3)), dab_nucleo=dab, dab_anillo=dab_a,
               hema=np.full(n, 0.3), saturado=z, foco_bajo=z, pliegue=z,
               fragmento=S.fragmento_de(prov, cents), borde_um=S.distancia_borde(prov, cents),
               foco_tesela=np.ones(4), tejido_tesela=np.ones(4), origen_tesela=np.zeros((4, 2)),
               tesela=np.zeros(n, int), rasgos=rasgos, prov_fragmentos=prov["fragmentos"],
               pol_xy=pxy, pol_offs=offs)
    hu = P._huella(base, P._rels_nucleos(base, nombre) + ["congelacion.json"])
    P._npz(base, P._rel("objetos_%s.npz" % nombre), **arr)
    P._escribe(base, P._rel("objetos_%s.json" % nombre),
               {"lamina": nombre, "n": n, "mpp_l0": MPP, "dimensiones_l0": list(DIMS),
                "prov_f": prov["f"], "prov_mpp": prov["mpp"], "corte_tercil_foco": None})
    C.marca_hecho(base, "objetos", nombre, hu, productos=[P._rel("objetos_%s.npz" % nombre),
                                                         P._rel("objetos_%s.json" % nombre)])
    return epi, pos

def mascara_ck19():
    m = np.zeros((2000, 3200), bool)
    for e in EPI_UM:
        x0, y0, x1, y1 = (int(v) for v in e.bounds)
        m[y0:y1, x0:x1] = True
    P._npz(base, P._rel("ck19", "mascara.npz"), bits=np.packbits(m, axis=None),
           forma=np.asarray(m.shape))
    P._escribe(base, P._rel("ck19", "mascara.json"),
               {"lamina": "P-CK19", "origen_l0": [0.0, 0.0], "mpp": 1.0, "mpp_l0": MPP,
                "forma": list(m.shape), "umbral": {"regla": "valle", "T": 0.2, "rotulos": []},
                "sensibilidad": {"dependiente": False, "por_fragmento": {}}, "rotulos": [],
                "galeria_borde": []})

def registro(movil, M, extiende=True):
    mk_l = []
    for k, fc in enumerate(FCS_UM):
        mk = np.zeros((1000, 1600), bool)
        x0, y0, x1, y1 = (int(v / 2.0) for v in fc.bounds)
        mk[y0:y1, x0:x1] = True
        mk_l.append({"id": "FC%d" % (k + 1), "mascara": mk, "mpp": 2.0,
                     "area_um2": float(mk.sum() * 4.0)})
    if movil == "P-KI67":
        P._guarda_fcs(base, mk_l)
    frs = [{"id": f["id"], "matriz_um": M.tolist(), "pasa": True, "area_um2": f["area_um2"],
            "evaluacion": {"p90_um": 12.0, "rige": "b", "pasa": True}} for f in mk_l]
    o = {"tipo": "registro-par", "fija": "P-CK19", "movil": movil, "tre_p90_um": 12.0,
         "resumen": {"fija": "P-CK19", "movil": movil, "global": {"matriz_um": M.tolist()},
                     "fragmentos": frs, "parametros": dict(R.PARAMS_PARTIDA)},
         "fc_piloto_sha256": P._sha_fc(base)}
    if movil == "P-KI67":
        o["ii"] = {"fraccion_area_verificada_ki67": 1.0 if extiende else 0.2,
                   "extiende": extiende, "nota": "x"}
    rel = P._rel("registro", "%s.json" % movil)
    P._escribe(base, rel, o)
    C.marca_hecho(base, "registro-par", movil, "P-CK19",
                  productos=[rel] + ([P.REL_FC] if movil == "P-KI67" else []))

q = dict(log=lambda m: None)
sella()
# máscara CK19: erosión y dilatación por centroide (rejilla de 2 µm)
mascara_ck19()
ck = P.MascaraCK19(base)
pts = np.array([[600.0, 305.0], [600.0, 320.0], [600.0, 295.0], [600.0, 280.0]])
out["ck_dentro"] = ck.dentro(pts).tolist()
out["ck_erosion10"] = ck.erosionada(pts, 10.0).tolist()
out["ck_dilata10"] = ck.dilatada(pts, 10.0).tolist()
# (i-bis) en P-CK19: regla morfométrica frente al anillo CK19+ (verdad sintética conocida)
lamina("P-CK19", np.eye(3), 0.0, 0.0, anillo_epi=0.5)
r = P.regla_morfometrica(base, **q)
out["regla"] = [r["en_muestra"]["sensibilidad"], r["en_muestra"]["especificidad"]]
out["regla_lofo"] = [v.get("sensibilidad") for v in r["validacion_por_fragmento"].values()]
out["regla_hecho"] = P.regla_morfometrica(base, **q)["sha256"] == r["sha256"]
out["rc_diag"] = P.analisis(base, ["piloto-ibis", "P-CK19", "--diagnostico"], **q)
out["rc_diag_ki67"] = P.analisis(base, ["piloto-ibis", "P-KI67", "--diagnostico"], **q)
dg = json.load(open(os.path.join(base, "piloto", "diagnostico_regla.json")))
out["diag"] = [dg["usados_por_la_regla"]["n"], r["n"], dg["usados_por_la_regla"]["anillo_pos"],
               r["n_ck19_pos"], sorted(dg["artefacto_por_componente"])]
Mki = M_de(100.0, -50.0)
epi_k, pos_k = lamina("P-KI67", Mki, 0.30, 0.05)
out["verdad_ki67"] = float(100 * pos_k[epi_k].mean())
for orden in (["metricas", "P-KI67"], ["geojson", "P-KI67"]):
    out["rc_sin_ii_" + orden[0]] = P.analisis(base, orden, **q)
registro("P-KI67", Mki)
logs_metricas = []
m = P.metricas(base, "P-KI67", log=logs_metricas.append)
res = m["resultado"]
# 2-oct: ninguna cifra de diana sale al log (el log sale de la zona clínica)
_cifras = [res["porcentaje"]["pct"], (res.get("hotspot") or {}).get("maximo_pct")]
out["log_metricas_sin_cifras"] = not any(
    v is not None and any(("%.1f" % v) in l or ("%.2f" % v) in l or repr(v) in l
                          for l in logs_metricas) for v in _cifras)
out["log_metricas_n"] = len(logs_metricas)
out["ki67"] = {"pct": res["porcentaje"]["pct"], "den": res["porcentaje"]["pct_por_denominador"],
               "L": m["L_um"], "hotspot": (res.get("hotspot") or {}).get("maximo_pct"),
               "senal": res["senal"]["estado"], "estado": m["estado"],
               "rotulos": res["porcentaje"]["rotulos"]}
out["L_json"] = P._lee(base, P._rel("L.json"))["L_um"]
g = P.geojson(base, "P-KI67", **q)
fc = json.load(open(os.path.join(base, g)))
import laminillas_geojson as G
out["geo_valido"] = G.valida(fc) == []
out["geo_det"] = sum(1 for f in fc["features"] if f["properties"]["objectType"] == "detection")
out["geo_reg"] = sum(1 for f in fc["features"] if f["properties"]["objectType"] == "annotation")
out["geo_capas"] = fc["laminillas"]["capas"]
out["n_ki67"] = int(len(pos_k))
capa = P._lee(base, P.capa_visor(base, "P-KI67", **q))
out["visor"] = {k: len(v) for k, v in capa["clases"].items()}
h1 = m["huella"]
out["repite_salta"] = P.metricas(base, "P-KI67", **q)["huella"] == h1
# NE: P-SYN con otra traslación, mismo código (extensión)
Msyn = M_de(-80.0, 40.0)
lamina("P-SYN", Msyn, 0.02, 0.0)
try:
    P.metricas(base, "P-SYN", **q)
except P.FaltaEntrada as e:
    out["syn_sin_registro"] = "registro-par P-SYN" in str(e)
registro("P-SYN", Msyn)
ms = P.metricas(base, "P-SYN", **q)
out["syn"] = {"pct": ms["resultado"]["porcentaje"]["pct"], "senal": ms["resultado"]["senal"]["estado"],
              "patron": "patron_puntos" in ms["resultado"], "hscore": "hscore" in ms["resultado"],
              "heterogeneidad": "heterogeneidad" in ms["resultado"], "L": ms["L_um"]}
out["nunca"] = MET.barre_nunca([m, ms])
# lista post-congelación: los ítems que no leen píxeles, y la parada al primer «no pasa»
L = {"base": base, "lector": None, "sello": SL.exige(P.ruta_sello(base), ["P-KI67"])}
items = dict(P.ITEMS_LISTA)
for nombre in ("teselado_instanseg", "grandqc_ki67", "mascara_ck19", "regla_morfometrica",
               "falsos_positivos", "erosion_ck19", "tre", "mascara_tejido", "solo_piloto"):
    out["item_" + nombre] = items[nombre](L)["estado"]
out["teselado_rej"] = items["teselado_instanseg"](L)["rejilla_desplazada"]["pasa"]
# (i) en P-KI67: la puerta del área es CRITERIO_AREA (InstanSeg/B, la misma de la pre-congelación);
# la «mediana 25-80 µm²» ya no decide (núcleos de ~14 µm² pasan, como en las láminas reales)
rj = os.path.join(base, "segmenta", "nucleos_P-KI67.json")
orig = json.load(open(rj))
def escribe_nucleos(d):
    json.dump(d, open(rj, "w"))
    C.marca_hecho(base, "segmenta", "P-KI67", productos=["segmenta/nucleos_P-KI67.npz",
                                                       "segmenta/nucleos_P-KI67.json"])
def teselado_con(**cambios):
    d = dict(orig, **cambios)
    if d["acuerdo_area"] is None:
        del d["acuerdo_area"]
    escribe_nucleos(d)
    return items["teselado_instanseg"](L)
pequenos = {"mediana_um2": 13.8, "n": 9000, "pasa": False}
it = teselado_con(area_nuclear=pequenos)
out["area_criterio"] = [it["estado"], K.S.CRITERIO_AREA in it["criterio"], "25-80 µm²;" in it["criterio"],
                        it["acuerdo_area"]["criterio"] == K.S.CRITERIO_AREA]
it = teselado_con(acuerdo_area=S.acuerdo_area(np.full(300, 0.25)))      # escala ×2 en lineal
out["area_cociente_malo"] = [it["estado"], " ".join(it["motivos"])]
it = teselado_con(acuerdo_area=S.acuerdo_area(np.full(150, 0.95)))      # < 200 pares
out["area_pocos_pares"] = it["estado"]
it = teselado_con(acuerdo_area=None)
out["area_sin_puerta"] = [it["estado"], " ".join(it["motivos"])]
escribe_nucleos(orig)
falso = [("a", lambda c: {"estado": "pasa"}), ("b", lambda c: {"estado": "rama"}),
         ("c", lambda c: {"estado": "no pasa", "motivos": ["x"]}), ("d", lambda c: {"estado": "pasa"})]
inf = P.corre_lista({}, items=falso, log=lambda m: None)
out["lista"] = [inf["pasa"], inf["parada_en"], [f["estado"] for f in inf["items"]], inf["ramas"]]
def falta(c):
    raise P.FaltaEntrada("x")
inf = P.corre_lista({}, items=[("a", falta), ("b", lambda c: {"estado": "pasa"})], log=lambda m: None)
out["lista_falta"] = [inf["parada_en"], inf["items"][1]["estado"]]
# escalera: (ii) <50 % → denominador morfométrico, CK19 caído y se dice
registro("P-KI67", Mki, extiende=False)
P._escribe(base, P._rel("registro", "escalera.json"), {"tipo": "escalera-sin-registro"})
me = P.metricas(base, "P-KI67", **q)
out["escalera"] = {"estado": me["estado"], "den": me["denominador"],
                   "pct": me["resultado"]["porcentaje"]["pct"],
                   "decl": me["resultado"]["porcentaje"]["declaraciones"][0]}
json.dump(out, open(os.path.join(base, "out.json"), "w"), default=str)
"""

# Piloto real del 2-oct: 3,8 % de anillos CK19+ y corte fijo 0,5 → la regla no marcaba ninguna
# célula (sens 0) y la lista la dejaba pasar; y la escalera paraba porque ningún L llegaba a 100.
REGLA_CORTE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import numpy as np
import laminillas_proc as P
import laminillas_metricas as MET
rng = np.random.default_rng(5)
n = 6000
y = rng.random(n) < 0.04
X = rng.normal(size=(n, 6))
X[y, 0] += 1.2
X[y, 5] += 0.8
g = rng.integers(0, 4, n)
out = {"prev": float(y.mean())}
viejo = P.ajusta_regla(X, y)
out["sens_corte_05"] = P._sens_esp(P.probabilidad_regla(viejo, X) >= 0.5, y)["sensibilidad"]
r = P.ajusta_regla_con_corte(X, y)
pred = P.aplica_regla(r, X)
out["frac_pred"] = float(pred.mean())
out["sens"] = P._sens_esp(pred, y)["sensibilidad"]
out["auc"] = P.auc(P.probabilidad_regla(r, X), y)
lofo = P.valida_regla(X, y, g)
out["lofo_sens"] = [v["sensibilidad"] for v in lofo.values()]
out["lofo_auc"] = [v["auc"] for v in lofo.values()]
out["degenerada"] = P.regla_degenerada({
    "en_muestra": {"sensibilidad": 0.0},
    "validacion_por_fragmento": {"0": {"evaluado": True, "tp": 0, "fn": 20, "sensibilidad": 0.0}}})
out["sana"] = P.regla_degenerada({"en_muestra": {"sensibilidad": out["sens"]},
                                  "validacion_por_fragmento": lofo})
out["sin_corte"] = None
try:
    P.aplica_regla({k: v for k, v in r.items() if k != "corte"}, X)
except KeyError:
    out["sin_corte"] = "KeyError"
class Ctx:
    p = {"L_candidatos_um": [100, 150, 200, 300, 400], "L_frac_area_mediana": 0.5,
         "L_mediana_min": 100}
MET.rejilla = lambda fcs, L: L
MET.cuenta_regiones = lambda nuc, rej, ctx, den: {"a": {"n": int(rej / 10), "frac_area": 1.0}}
out["L_bajo"] = list(P._L_densidad(None, None, Ctx(), "morfometrico"))
MET.cuenta_regiones = lambda nuc, rej, ctx, den: {"a": {"n": int(rej), "frac_area": 1.0}}
out["L_ok"] = list(P._L_densidad(None, None, Ctx(), "morfometrico"))
print(json.dumps(out))
"""


CACHE_L = r"""
import json, os, sys, tempfile
sys.path.insert(0, sys.argv[1])
import laminillas_proc as P
import laminillas_metricas as MET
base = tempfile.mkdtemp()
llamadas = []
MET.elige_L = lambda nuc, fcs, tre, ctx, den: llamadas.append(1) or {"L_um": 200.0, "motivo": None}
class Nuc:
    lamina = "P-KI67"
def escribe(rel, d):
    p = os.path.join(base, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(d, open(p, "w"))
out = {}
escribe("congelacion.json", {"v": 1})
out["primera"] = P._valor_L(base, Nuc(), {}, None, 10.0, "REG")[0]
out["cache"] = P._valor_L(base, Nuc(), {}, None, 10.0, "REG")[0]
out["llamadas_tras_cache"] = len(llamadas)
escribe("congelacion.json", {"v": 2})                         # recongelación: otro sello
P._valor_L(base, Nuc(), {}, None, 10.0, "REG")
out["llamadas_tras_recongelar"] = len(llamadas)
print(json.dumps(out))
"""


@unittest.skipUnless(os.path.exists(VENV_PAT), "sin venv patologia")
class CacheL(unittest.TestCase):
    """L.json se recalcula si cambian las entradas del denominador (p. ej. una recongelación que
    cambia los artefactos), no solo si cambia el registro (2-oct)."""

    def test_recongelar_invalida_L(self):
        r = subprocess.run([VENV_PAT, "-c", CACHE_L, TOOLS], capture_output=True, text=True,
                           timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        o = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual((o["primera"], o["cache"]), (200.0, 200.0))
        self.assertEqual(o["llamadas_tras_cache"], 1)
        self.assertEqual(o["llamadas_tras_recongelar"], 2)


@unittest.skipUnless(os.path.exists(VENV_PAT), "sin venv patologia")
class ReglaCortePrevalencia(unittest.TestCase):
    """La regla morfométrica con prevalencia baja: corte de prevalencia, guarda de regla degenerada
    y L de la escalera en el máximo sellado (no bloquea)."""

    @classmethod
    def setUpClass(cls):
        r = subprocess.run([VENV_PAT, "-c", REGLA_CORTE, TOOLS], capture_output=True, text=True,
                           timeout=600)
        cls.err = r.stderr[-2000:]
        cls.o = json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 else None

    def setUp(self):
        self.assertIsNotNone(self.o, self.err)

    def test_corte_05_degenerado_y_el_de_prevalencia_marca(self):
        self.assertLess(self.o["sens_corte_05"] or 0, 0.1)
        self.assertGreater(self.o["sens"], 0.3)
        self.assertLess(abs(self.o["frac_pred"] - self.o["prev"]), 0.2 * self.o["prev"])
        self.assertGreater(self.o["auc"], 0.75)

    def test_fuera_de_muestra_marca_en_cada_fragmento_y_da_auc(self):
        self.assertTrue(all(s and s > 0.2 for s in self.o["lofo_sens"]), self.o["lofo_sens"])
        self.assertTrue(all(a and a > 0.7 for a in self.o["lofo_auc"]), self.o["lofo_auc"])

    def test_degenerada_no_pasa_y_sana_si(self):
        self.assertEqual(len(self.o["degenerada"]), 2)
        self.assertEqual(self.o["sana"], [])
        self.assertEqual(self.o["sin_corte"], "KeyError")

    def test_escalera_L_maximo_sellado_con_rotulo(self):
        L, tabla, rot = self.o["L_bajo"]
        self.assertEqual(L, 400.0)
        self.assertEqual(len(tabla), 5)
        self.assertIn("top of the sealed list", rot)
        self.assertIn("= 40 (<100)", rot)
        self.assertEqual(self.o["L_ok"][0], 100.0)
        self.assertIsNone(self.o["L_ok"][2])


@unittest.skipUnless(os.path.exists(VENV_PAT), "sin venv patologia")
class ProcPiloto(unittest.TestCase):
    """`metricas`, `geojson`, capa del visor, regla morfométrica, máscara CK19, escalera, NE y la
    lista post-congelación sobre una SESION sintética de productos (sin píxeles)."""

    @classmethod
    def setUpClass(cls):
        cls.d = os.path.realpath(tempfile.mkdtemp(prefix="lam-proc-piloto-"))
        r = subprocess.run([VENV_PAT, "-c", PROC_PILOTO, TOOLS, cls.d], capture_output=True,
                           text=True, timeout=1200)
        cls.err = r.stderr[-3000:]
        cls.o = None
        if r.returncode == 0:
            with open(os.path.join(cls.d, "out.json")) as f:
                cls.o = json.load(f)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def setUp(self):
        self.assertIsNotNone(self.o, self.err)

    def test_mascara_ck19_erosion_y_dilatacion_por_centroide(self):
        """Borde superior del epitelio en y = 300 µm; rejilla de 2 µm (tolerancia declarada)."""
        self.assertEqual(self.o["ck_dentro"], [True, True, False, False])
        self.assertEqual(self.o["ck_erosion10"], [False, True, False, False])
        self.assertEqual(self.o["ck_dilata10"], [True, True, True, False])

    def test_metricas_no_sacan_cifras_de_diana_al_log(self):
        """2-oct, piloto real: `metricas` imprimía el % de Ki67 en el log, que sale de la zona
        clínica, y una cifra vista antes de tiempo contamina decisiones de método."""
        self.assertGreater(self.o["log_metricas_n"], 0)
        self.assertTrue(self.o["log_metricas_sin_cifras"])

    def test_regla_morfometrica_ajusta_y_valida_por_fragmento(self):
        self.assertGreater(self.o["regla"][0], 0.9)
        self.assertGreater(self.o["regla"][1], 0.9)
        self.assertTrue(all(s is not None and s > 0.85 for s in self.o["regla_lofo"]))
        self.assertTrue(self.o["regla_hecho"])

    def test_diagnostico_regla_solo_numeros_y_casa_con_la_regla(self):
        self.assertEqual(self.o["rc_diag"], 0)
        self.assertEqual(self.o["rc_diag_ki67"], 2)
        n_diag, n_regla, pos_diag, pos_regla, comps = self.o["diag"]
        self.assertEqual(n_diag, n_regla)
        self.assertEqual(pos_diag, pos_regla)
        self.assertEqual(comps[:3], ["foco_bajo", "pliegue", "saturado"])

    def test_sin_registro_ii_falta_entrada(self):
        self.assertEqual(self.o["rc_sin_ii_metricas"], 5)
        self.assertEqual(self.o["rc_sin_ii_geojson"], 5)

    def test_ki67_registrado_recupera_el_porcentaje(self):
        """Verdad: % de positivos en el epitelio CK19+ (≈30 %). Tolerancia ±2 puntos."""
        k = self.o["ki67"]
        self.assertEqual(k["estado"], "registrado")
        self.assertAlmostEqual(k["pct"], self.o["verdad_ki67"], delta=2.0)
        self.assertEqual(set(k["den"]) >= {"ck19_erosionada", "ck19_sin_erosionar",
                                           "morfometrico"}, True)
        self.assertEqual(k["senal"], "quantifiable")
        self.assertIsNotNone(k["hotspot"])
        self.assertEqual(k["L"], self.o["L_json"])
        self.assertTrue(self.o["repite_salta"])

    def test_geojson_y_capa_del_visor(self):
        self.assertTrue(self.o["geo_valido"])
        self.assertEqual(self.o["geo_det"], self.o["n_ki67"])
        self.assertGreater(self.o["geo_reg"], 0)
        self.assertIn("annotation: in situ/exclude (editable; 0 drawn)", self.o["geo_capas"])
        self.assertEqual(sum(self.o["visor"].values()), self.o["n_ki67"])

    def test_ne_mismo_codigo_que_luminal(self):
        self.assertTrue(self.o["syn_sin_registro"])
        s = self.o["syn"]
        self.assertTrue(s["hscore"] and s["heterogeneidad"])
        self.assertEqual(s["senal"], "focal")
        self.assertTrue(s["patron"])                                  # NE focal: patrón de puntos
        self.assertIsNotNone(s["pct"])                                # el % no se esconde
        self.assertEqual(s["L"], self.o["L_json"])
        self.assertEqual(self.o["nunca"], [])

    def test_escalera_sin_registro(self):
        e = self.o["escalera"]
        self.assertEqual((e["estado"], e["den"]), ("escalera", "morfometrico"))
        self.assertIn("CK19 denominator dropped", e["decl"])
        self.assertAlmostEqual(e["pct"], self.o["verdad_ki67"], delta=3.0)

    def test_lista_postcongelacion_items_y_parada(self):
        for it in ("teselado_instanseg", "grandqc_ki67", "mascara_ck19", "regla_morfometrica",
                   "falsos_positivos", "erosion_ck19", "tre", "solo_piloto"):
            self.assertEqual(self.o["item_" + it], "pasa", it)
        self.assertIn(self.o["item_mascara_tejido"], ("pasa", "rama"))
        self.assertTrue(self.o["teselado_rej"])

    def test_teselado_puerta_de_area_es_criterio_area(self):
        """Punto 4 (i) en P-KI67, alineado con la pre-congelación: la puerta es CRITERIO_AREA
        (InstanSeg/detector B en µm², 0,70-1,30 con ≥ 200 pares), importado, no copiado."""
        self.assertEqual(self.o["area_criterio"], ["pasa", True, False, True])
        estado, motivos = self.o["area_cociente_malo"]
        self.assertEqual(estado, "no pasa")
        self.assertIn("InstanSeg/B 0.25", motivos)
        self.assertEqual(self.o["area_pocos_pares"], "no pasa")
        estado, motivos = self.o["area_sin_puerta"]
        self.assertEqual(estado, "no pasa")
        self.assertIn("segmenta P-KI67", motivos)
        self.assertEqual(self.o["lista"], [False, "c", ["pasa", "rama", "no pasa", "no evaluado"],
                                           ["b"]])
        self.assertEqual(self.o["lista_falta"], ["a", "no evaluado"])


# `registro-par` con el registro del módulo B de verdad, sobre cortes sintéticos pintados.
PROC_REGISTRO = r"""
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[3])
base = os.path.realpath(sys.argv[2])
import numpy as np
from shapely.geometry import LineString
import laminillas_comun as C
import laminillas_proc as P
import laminillas_sello as SL
import laminillas_metricas as MET
import laminillas_congela as K
import laminillas_registro as R
import _laminillas_sinteticas as SINT

def unit(v):
    v = np.asarray(v, float)
    return (v / np.linalg.norm(v)).tolist()

h, d = unit(SINT.RUIFROK_H), unit(SINT.RUIFROK_DAB)
reg = {"p999": 0.04, "rige": "suelo_fijo", "rotulos": [], "T": 0.1, "banda": [0.06, 0.15],
       "estado": "ok"}
fp = {"k": 1, "n": 2000, "fraccion": 0.0005, "ic95": [0.0, 0.002]}
SL.sella(os.path.join(base, SL.FICHERO), {
    "vectores": {"H": h, "DAB": d, "tercero": unit(np.cross(h, d))},
    "residuo": {"por_lamina": {}}, "precongelacion": {"sha256": "x"},
    "parametros": K.parametros(),
    "umbral": {"T": {"nucleo": 0.1, "anillo": 0.1},
               "banda": {"nucleo": [0.06, 0.15], "anillo": [0.06, 0.15]}, "rige": "clasicas",
               "rotulos": [], "regimenes": {"clasicas": {"nucleo": reg, "anillo": reg}}},
    "fp_her2": {"nucleo": fp, "anillo": fp}, "registro": K.REGISTRO, "hotspot": K.HOTSPOT,
    "regla_L": K.REGLA_L, "modulo_b": MET.contenido_partida(),
    "semillas": {"maestra": 20261001, "bootstrap": 20261001, "pixeles": 20261101,
                 "galeria_focal": 20261201},
    "medida": {"hscore": {"cortes": ["T", 0.4, 0.6]}, "semilla_galeria_focal": 20261201}},
    "2026-10-02")
bloque = SINT.Bloque(11, ancho_um=2200.0, alto_um=1500.0, fragmentos=[
    LineString([(400, 500), (1100, 430), (1800, 600)]).buffer(300),
    LineString([(450, 1150), (1500, 1200)]).buffer(200)])
_, cortes, L = SINT.serie(11, [dict(nombre="P-CK19", fase=0.0, ck19=True),
                              dict(nombre="P-KI67", fase=0.25, angulo=37.0, ki67=0.25)],
                          bloque=bloque)
cortes = {c.nombre: c for c in cortes}
os.makedirs(os.path.join(base, "segmenta"), exist_ok=True)
for n in cortes:
    cen = L.centroides_l0(n)
    sq = np.array([[-4, -4], [4, -4], [4, 4], [-4, 4], [-4, -4]], float)
    np.savez_compressed(os.path.join(base, "segmenta", "nucleos_%s.npz" % n), centroide=cen,
                        area_um2=np.full(len(cen), 4.0), pol_xy=(cen[:, None] + sq).reshape(-1, 2),
                        pol_offs=np.arange(len(cen) + 1) * 5)
    json.dump({"lamina": n, "mpp_l0": SINT.MPP_L0, "n": len(cen)},
              open(os.path.join(base, "segmenta", "nucleos_%s.json" % n), "w"))
    C.marca_hecho(base, "segmenta", n, productos=["segmenta/nucleos_%s.npz" % n,
                                                  "segmenta/nucleos_%s.json" % n])
q = dict(log=lambda m: None)
out = {}
try:
    P.registro_par(base, "P-HER2NEG", "P-CK19", lector=L, **q)
except P.FaltaEntrada as e:
    out["iibis_sin_ii"] = True
t0 = time.time()
r = P.registro_par(base, "P-CK19", "P-KI67", lector=L, **q)
out["t"] = time.time() - t0
out["ii"] = r["ii"]
out["tre"] = r["tre_p90_um"]
out["escalera"] = r["escalera"]
out["productos"] = sorted(os.listdir(os.path.join(base, "piloto", "registro")))
res, _ = P.resultado_desde_disco(base, "P-KI67")
esp = cortes["P-CK19"].A @ cortes["P-KI67"].T
cen = L.centroides_l0("P-KI67")
xy, ids = res.nucleos_a_referencia(cen, SINT.MPP_L0)
ok = np.array([i is not None for i in ids])
verdad = cen[ok] * SINT.MPP_L0 @ esp[:2, :2].T + esp[:2, 2]
out["err_p95_um"] = float(np.percentile(np.linalg.norm(xy[ok] - verdad, axis=1), 95))
out["frac_mapeados"] = float(ok.mean())
lecturas = L.lecturas
P.registro_par(base, "P-KI67", "P-CK19", lector=L, **q)
out["segunda_sin_leer"] = L.lecturas == lecturas
try:
    P.registro_par(base, "P-KI67", "P-CK19", lector=L, valis=True, **q)
except ValueError:
    out["valis_sin_escalera"] = True
# `registro-par … --diagnostico`: solo números; no toca productos del registro ni «hecho».
# VALIS falso que no corre (el diagnóstico lo dice y sigue).


def huellas():
    h = {}
    for sub in ("hecho", os.path.join("piloto", "registro")):
        for nm in sorted(os.listdir(os.path.join(base, sub))):
            h[os.path.join(sub, nm)] = C.sha256_fichero(os.path.join(base, sub, nm))
    return h


class _NoCorre:
    returncode, stdout, stderr = 1, "", "VALIS falso: no corre"


real_ej = R._ejecutor_valis
R._ejecutor_valis = lambda cmd: _NoCorre()
antes = huellas()
out["rc_diag"] = P.analisis(base, ["registro-par", "P-KI67", "P-CK19", "--diagnostico"],
                            lector=L, **q)
out["diag_no_toca"] = huellas() == antes
dg = json.load(open(os.path.join(base, "piloto", "diagnostico_registro.json")))
pr = dg["pares"]["P-KI67→P-CK19"]
out["diag_inliers"] = pr["sellado"]["directa"]["ratio_0.8"]["ransac_inliers"]
out["diag_valis_motivo"] = pr["valis_directa"]["motivo"]
out["diag_fc"] = len(pr["por_fc"])
out["diag_idem_p90"] = pr["por_fc"][0]["tre"]["identidad_fija_contra_si_misma"]["b"]["p90_um"]
out["rc_diag_mal"] = [P.analisis(base, a, lector=L, **q) for a in (
    ["registro-par", "--diagnostico", "--valis"], ["registro-par", "P-HE", "P-CK19", "--diagnostico"],
    ["registro-par", "P-KI67", "--diagnostico"])]
R._ejecutor_valis = real_ej
# escalera con el registro sustituido (<50 %), y la vuelta única con VALIS. El registro falso
# devuelve un par cuyo FC llegó al paso 4 con el intento VALIS que se le dé (o ninguno).
real = R.registra_serie


class _Res:
    fcs = []


def falsa(intentos_valis=None, sin_par=False):
    def f(*a, **k):
        ii = {"fraccion_area_verificada_ki67": 0.2, "extiende": False, "nota": "<50 %"}
        if sin_par:
            return {"ii": ii, "resultados": {}, "pares": {}, "fcs": []}
        resumen = {"fragmentos": [{"id": "fc1", "pasa": False, "area_um2": 1.0, "intentos": [
            {"metodo": "sift", "pasa": False}] + list(intentos_valis or [])}], "valis": None}
        return {"ii": ii, "resultados": {"P-KI67": _Res()}, "pares": {"P-KI67": resumen},
                "fcs": [], "orden": None}
    return f


NO_CORRE = {"metodo": "VALIS", "pasa": False, "corrio": False,
            "motivo": "VALIS did not run: rc=1; TypeError: Object of type int64 is not JSON "
                      "serializable"}
CORRE = {"metodo": "VALIS", "pasa": False, "corrio": True, "n_inliers": None, "p90_um": 80.0,
         "escala": 1.0, "anisotropia": 1.0}
rel_esc = os.path.join("piloto", "registro", "escalera.json")
rel_ki = os.path.join("piloto", "registro", "P-KI67.json")
lee = lambda rel: json.load(open(os.path.join(base, rel)))
valis_cmd = ["registro-par", "P-KI67", "P-CK19", "--valis"]
R.registra_serie = falsa()
os.remove(os.path.join(base, "hecho", "registro-par__P-KI67__P-CK19.json"))
r2 = P.registro_par(base, "P-KI67", "P-CK19", lector=L, **q)
out["escalera2"] = r2["escalera"]
out["estado"] = P.estado_ii(base)[0]
# LEGADO (el estado real del piloto del 2-oct): valis_intentado true sin valis_corrio, y los
# intentos VALIS del producto «sin resultado» → no se gastó la vuelta.
esc = lee(rel_esc)
esc.pop("valis_corrio", None)
esc["valis_intentado"] = True
P._escribe(base, rel_esc, esc)
ki = lee(rel_ki)
ki["resumen"]["fragmentos"][0]["intentos"].append(
    {"metodo": "VALIS", "pasa": False, "motivo": "sin resultado"})
P._escribe(base, rel_ki, ki)
out["legado_sin_resultado"] = P.valis_gastado(base)[0]
ki["resumen"]["fragmentos"][0]["intentos"][-1] = {"metodo": "VALIS", "pasa": False,
                                                  "escala": 1.0, "anisotropia": 1.0}
P._escribe(base, rel_ki, ki)
out["legado_con_transformada"] = P.valis_gastado(base)[0]
os.rename(os.path.join(base, rel_ki), os.path.join(base, rel_ki + ".x"))
out["legado_sin_producto"] = P.valis_gastado(base)[0]
os.rename(os.path.join(base, rel_ki + ".x"), os.path.join(base, rel_ki))
ki["resumen"]["fragmentos"][0]["intentos"][-1] = {"metodo": "VALIS", "pasa": False,
                                                  "motivo": "sin resultado"}
P._escribe(base, rel_ki, ki)
# --valis con VALIS que NO corre: código 6, la vuelta NO se gasta, `valis_fallos` dice por qué
R.registra_serie = falsa([NO_CORRE])
out["rc_no_corre"] = P.analisis(base, valis_cmd, lector=L, **q)
out["esc_no_corre"] = lee(rel_esc)
out["rc_no_corre_2"] = P.analisis(base, valis_cmd, lector=L, **q)       # se puede repetir
R.registra_serie = falsa(sin_par=True)                                    # ni se lanza
out["rc_sin_lanzar"] = P.analisis(base, valis_cmd, lector=L, **q)
out["esc_sin_lanzar"] = lee(rel_esc)
# --valis con VALIS que corre (aunque su TRE no pase): la vuelta se gasta, una sola vez
R.registra_serie = falsa([CORRE])
r3 = P.registro_par(base, "P-KI67", "P-CK19", lector=L, valis=True, **q)
out["valis_intentado"] = r3["escalera"]["valis_intentado"]
out["esc_corre"] = r3["escalera"]
out["rc_valis_dos_veces"] = P.analisis(base, valis_cmd, lector=L, **q)
try:
    P.registro_par(base, "P-KI67", "P-CK19", lector=L, valis=True, **q)
except P.NoPasa:
    out["valis_dos_veces"] = "NoPasa"
# (ii) sin --valis rehecho después (p. ej. tras recongelar): no borra la vuelta gastada
R.registra_serie = falsa()
os.remove(os.path.join(base, "hecho", "registro-par__P-KI67__P-CK19.json"))
out["tras_rehacer"] = P.registro_par(base, "P-KI67", "P-CK19", lector=L, **q)["escalera"]
R.registra_serie = real
json.dump(out, open(os.path.join(base, "out.json"), "w"), default=str)
"""


@unittest.skipUnless(os.path.exists(VENV_PAT), "sin venv patologia")
class ProcRegistroPar(unittest.TestCase):
    """`registro-par` (ii) con el registro de verdad sobre cortes sintéticos (giro 37° conocido):
    productos, reconstrucción del par desde disco, «hecho», y la regla del 50 % con su escalera y
    la vuelta única con VALIS (registro sustituido)."""

    @classmethod
    def setUpClass(cls):
        cls.d = os.path.realpath(tempfile.mkdtemp(prefix="lam-proc-reg-"))
        r = subprocess.run([VENV_PAT, "-c", PROC_REGISTRO, TOOLS, cls.d,
                            os.path.join(RAIZ, "tests")], capture_output=True, text=True,
                           timeout=1800)
        cls.err = r.stderr[-3000:]
        cls.o = None
        if r.returncode == 0:
            with open(os.path.join(cls.d, "out.json")) as f:
                cls.o = json.load(f)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def setUp(self):
        self.assertIsNotNone(self.o, self.err)

    def test_ii_registra_y_se_reconstruye_desde_disco(self):
        """Tolerancias: p95 del error de los núcleos llevados a la referencia ≤ 5 µm (≈2,5 px a
        ×8) frente a la transformada VERDADERA; ≥50 % del área de consenso verificada."""
        self.assertTrue(self.o["iibis_sin_ii"])
        self.assertTrue(self.o["ii"]["extiende"])
        self.assertGreaterEqual(self.o["ii"]["fraccion_area_verificada_ki67"], 0.5)
        self.assertIsNone(self.o["escalera"])
        self.assertIn("fc_piloto.npz", self.o["productos"])
        self.assertIn("P-KI67.json", self.o["productos"])
        self.assertLess(self.o["err_p95_um"], 5.0)
        self.assertGreater(self.o["frac_mapeados"], 0.5)
        self.assertTrue(self.o["segunda_sin_leer"])                   # «hecho»: no relee píxeles
        self.assertTrue(self.o["valis_sin_escalera"])

    def test_diagnostico_solo_numeros_sin_tocar_productos(self):
        """`registro-par P-KI67 P-CK19 --diagnostico` (2-oct, piloto real: SIFT 0-3 inliers en
        todos los pares): rc 0, deja `piloto/diagnostico_registro.json` y NO cambia ningún
        producto del registro ni ningún «hecho» (sha256 iguales). En la serie sintética, SIFT
        sellado ≥30 inliers y la medida contra sí misma con p90 0. VALIS que no corre: lo dice y
        sigue. Banderas o pares fuera de lugar: código 2."""
        self.assertEqual(self.o["rc_diag"], 0)
        self.assertTrue(self.o["diag_no_toca"])
        self.assertGreaterEqual(self.o["diag_inliers"], 30)
        self.assertIn("VALIS did not run: rc=1", self.o["diag_valis_motivo"])
        self.assertGreaterEqual(self.o["diag_fc"], 1)
        self.assertEqual(self.o["diag_idem_p90"], 0.0)
        self.assertEqual(self.o["rc_diag_mal"], [2, 2, 2])

    def test_regla_del_50_escalera_y_valis_una_vez(self):
        e = self.o["escalera2"]
        self.assertEqual(e["tipo"], "escalera-sin-registro")
        self.assertIn("CK19 denominator", e["caen"])
        self.assertFalse(e["valis_intentado"])
        self.assertFalse(e["valis_corrio"])
        self.assertEqual(self.o["estado"], "escalera")
        # VALIS que corre (devuelve transformada, aunque su TRE no pase) gasta la vuelta
        self.assertTrue(self.o["valis_intentado"])
        self.assertTrue(self.o["esc_corre"]["valis_corrio"])
        self.assertNotIn("valis_fallos", self.o["esc_corre"])
        self.assertEqual(self.o["esc_corre"]["vuelve_al_tribunal"], "done (VALIS ran)")
        self.assertEqual(self.o["valis_dos_veces"], "NoPasa")
        self.assertEqual(self.o["rc_valis_dos_veces"], 4)
        # y una corrida posterior de (ii) sin --valis no la «devuelve»
        self.assertTrue(self.o["tras_rehacer"]["valis_intentado"])
        self.assertTrue(self.o["tras_rehacer"]["valis_corrio"])

    def test_valis_que_no_corre_no_gasta_la_vuelta(self):
        """Piloto real, 2-oct-26: `--valis` con rc 0 dejó `valis_intentado: true` y VALIS no había
        corrido (rc 1 en el subproceso, «sin resultado» en los tres FC grandes). Ahora: código 6,
        `valis_intentado` false, `valis_fallos` con el motivo, y se puede volver a pedir."""
        self.assertEqual(self.o["rc_no_corre"], 6)
        e = self.o["esc_no_corre"]
        self.assertFalse(e["valis_intentado"])
        self.assertFalse(e["valis_corrio"])
        self.assertEqual(e["valis_fallos"][0]["fc"], "fc1")
        self.assertIn("VALIS did not run: rc=1", e["valis_fallos"][0]["motivo"])
        self.assertIn("--valis", e["vuelve_al_tribunal"])
        self.assertEqual(self.o["rc_no_corre_2"], 6)                  # no 4: no se gastó
        self.assertEqual(self.o["rc_sin_lanzar"], 6)
        self.assertIn("VALIS not launched", self.o["esc_sin_lanzar"]["valis_fallos"][0]["motivo"])
        self.assertFalse(self.o["esc_sin_lanzar"]["valis_intentado"])

    def test_escalera_de_antes_con_valis_que_no_corrio(self):
        """El estado que dejó el piloto real: `valis_intentado: true` sin `valis_corrio` y los
        intentos VALIS del producto «sin resultado» → NO gastada. Con un intento con transformada
        (`escala`), o sin poder leer el producto, sí (lado seguro de la regla)."""
        self.assertFalse(self.o["legado_sin_resultado"])
        self.assertTrue(self.o["legado_con_transformada"])
        self.assertTrue(self.o["legado_sin_producto"])


# El camino de PÍXELES (objetos, QC, máscara CK19, regla, recall, detector clásico) sobre cortes
# sintéticos pintados; el lector lleva el mpp pedido al nivel nativo más cercano (humo).
PROC_PIXELES = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[3])
base = os.path.realpath(sys.argv[2])
import numpy as np
from shapely.geometry import LineString, Point
import laminillas_comun as C
import laminillas_proc as P
import laminillas_sello as SL
import laminillas_metricas as MET
import laminillas_congela as K
import laminillas_segmenta as S
import _laminillas_sinteticas as SINT

def unit(v):
    v = np.asarray(v, float)
    return (v / np.linalg.norm(v)).tolist()

h, d = unit(SINT.RUIFROK_H), unit(SINT.RUIFROK_DAB)
reg = {"p999": 0.04, "rige": "suelo_fijo", "rotulos": [], "T": 0.1, "banda": [0.06, 0.15],
       "estado": "ok"}
fp = {"k": 1, "n": 2000, "fraccion": 0.0005, "ic95": [0.0, 0.002]}
SL.sella(os.path.join(base, SL.FICHERO), {
    "vectores": {"H": h, "DAB": d, "tercero": unit(np.cross(h, d))},
    "residuo": {"por_lamina": {n: {"supera": False} for n in K.IHQ_CON_TEJIDO}},
    "precongelacion": {"sha256": "x"}, "parametros": K.parametros(),
    "umbral": {"T": {"nucleo": 0.1, "anillo": 0.1},
               "banda": {"nucleo": [0.06, 0.15], "anillo": [0.06, 0.15]}, "rige": "clasicas",
               "rotulos": [], "regimenes": {"clasicas": {"nucleo": reg, "anillo": reg}}},
    "fp_her2": {"nucleo": fp, "anillo": fp}, "registro": K.REGISTRO, "hotspot": K.HOTSPOT,
    "regla_L": K.REGLA_L, "modulo_b": MET.contenido_partida(),
    "semillas": {"maestra": 20261001, "bootstrap": 20261001, "pixeles": 20261101,
                 "galeria_focal": 20261201},
    "medida": {"hscore": {"cortes": ["T", 0.4, 0.6]}, "semilla_galeria_focal": 20261201}},
    "2026-10-02")
bloque = SINT.Bloque(11, ancho_um=1600.0, alto_um=1100.0, fragmentos=[
    LineString([(350, 450), (1250, 520)]).buffer(280)])
_, cortes, L0 = SINT.serie(11, [dict(nombre="P-CK19", fase=0.0, ck19=True),
                               dict(nombre="P-KI67", fase=0.25, angulo=20.0, ki67=0.25)],
                           bloque=bloque)

class Lector:
    def __init__(self, L):
        self.L = L
    def abre(self, n):
        return self.L.abre(n)
    def lee_region(self, lam, mpp, x, y, w, h):
        m = min((n[1] for n in lam.niveles), key=lambda v: abs(v - mpp))
        return self.L.lee_region(lam, m, x, y, w, h)
    def zona_escaneada(self, lam):
        return self.L.zona_escaneada(lam)
    def i0_local(self, lam):
        return self.L.i0_local(lam)
    def manifiesto(self):
        return self.L.manifiesto()

L = Lector(L0)
os.makedirs(os.path.join(base, "segmenta"), exist_ok=True)
for n in ("P-CK19", "P-KI67"):
    cen = L0.centroides_l0(n)
    pols = [Point(x, y).buffer(3.0 / SINT.MPP_L0, 6) for x, y in cen]
    xy, offs = S.empaqueta(pols)
    np.savez_compressed(os.path.join(base, "segmenta", "nucleos_%s.npz" % n), centroide=cen,
                        area_um2=S.areas_um2(pols, SINT.MPP_L0), pol_xy=xy, pol_offs=offs)
    json.dump({"lamina": n, "mpp_l0": SINT.MPP_L0, "n": len(cen)},
              open(os.path.join(base, "segmenta", "nucleos_%s.json" % n), "w"))
    C.marca_hecho(base, "segmenta", n, productos=["segmenta/nucleos_%s.npz" % n,
                                                  "segmenta/nucleos_%s.json" % n])
q = dict(log=lambda m: None)
out = {}
o = P.objetos(base, "P-KI67", L, **q)
dab = np.asarray(o["dab_nucleo"])
out["obj"] = {"n": int(len(dab)), "finitos": float(np.isfinite(dab).mean()),
              "p90": float(np.nanpercentile(dab, 90)), "p50": float(np.nanpercentile(dab, 50)),
              "rasgos": list(o["rasgos"].shape), "corte": o["corte_tercil_foco"],
              "recorte": [len(np.asarray(o.get("frac_recorte_nucleo", []))),
                          len(np.asarray(o.get("frac_recorte_anillo", [])))]}
lect = [json.loads(l)["que"] for l in open(os.path.join(base, "lecturas_diana.jsonl"))]
out["lecturas"] = lect
out["obj_hecho"] = P.objetos(base, "P-KI67", L, **q)["entradas"] == o["entradas"]
qc = P.qc_lamina(base, "P-KI67", L, **q)
out["qc"] = {"i0": qc["i0"]["pasa"], "seccion": qc["seccion"],
             "contacto": [v["fraccion_toca_borde_escaneado"]
                          for v in qc["contacto_borde_escaneado"].values()]}
P.objetos(base, "P-CK19", L, **q)
mk = P.mascara_ck19(base, L, **q)
out["ck19"] = {"regla": mk["umbral"].get("regla"), "dentro": mk["fraccion_dentro"],
               "galeria": len(mk["galeria_borde"])}
r = P.regla_morfometrica(base, **q)
out["regla"] = [r["en_muestra"]["sensibilidad"], r["en_muestra"]["especificidad"]]
s = SL.exige(P.ruta_sello(base), ["P-KI67"])
rc = P.recall_por_clase(base, L, s)
out["recall"] = {"estratos": len(rc["cobertura"]) + len(rc["estratos_fuera"]),
                 "cob": [min(v.values()) for v in rc["cobertura"].values()]}
cen = np.asarray(o["centroide"], float)
nuc = MET.Nucleos("P-KI67", cen * SINT.MPP_L0, dab, ["F0"] * len(dab),
                  {"principal": np.ones(len(dab), bool)})
Pd = {"lamina": "P-KI67", "nuc": nuc, "ob": o, "sello": s,
      "ctx": MET.Contexto(P.ruta_sello(base), "P-KI67")}
nc = P.nucleos_clasicos(base, Pd, L)
out["clasico"] = {"n": len(nc), "cerca": float(nc.den["principal"].mean()),
                  "pos": float(np.mean(nc.dab[np.isfinite(nc.dab)] > 0.1))}
json.dump(out, open(os.path.join(base, "out.json"), "w"), default=str)
"""


@unittest.skipUnless(os.path.exists(VENV_PAT), "sin venv patologia")
class ProcPilotoPixeles(unittest.TestCase):
    """Humo del camino de píxeles con el código de medida del módulo A (pasada de objetos,
    pliegues, I0, máscara CK19) sobre cortes pintados: produce cifras con sentido y deja
    constancia de cada lectura diana."""

    def test_objetos_qc_ck19_regla_recall_y_clasico(self):
        d = os.path.realpath(tempfile.mkdtemp(prefix="lam-proc-px-"))
        try:
            r = subprocess.run([VENV_PAT, "-c", PROC_PIXELES, TOOLS, d,
                                os.path.join(RAIZ, "tests")], capture_output=True, text=True,
                               timeout=1800)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            with open(os.path.join(d, "out.json")) as f:
                o = json.load(f)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        ob = o["obj"]
        self.assertGreater(ob["n"], 500)
        self.assertGreater(ob["finitos"], 0.95)
        self.assertGreater(ob["p90"], 0.2)                 # Ki67 sintético 25 %: cola positiva
        self.assertLess(ob["p50"], 0.1)
        self.assertEqual(ob["rasgos"][1], 6)
        self.assertIsNotNone(ob["corte"])
        self.assertEqual(ob["recorte"], [ob["n"], ob["n"]])    # regla v2: recorte anotado por célula
        self.assertIn("objetos", o["lecturas"])
        self.assertTrue(o["obj_hecho"])
        self.assertTrue(o["qc"]["i0"])
        self.assertEqual(o["qc"]["seccion"], "fraction of the section scanned: unknown, no macro "
                                             "image")
        self.assertEqual(o["ck19"]["regla"], "valle")
        self.assertEqual(o["ck19"]["galeria"], 20)
        self.assertGreater(o["regla"][0], 0.8)
        self.assertGreater(o["regla"][1], 0.7)
        self.assertGreaterEqual(o["recall"]["estratos"], 1)
        self.assertTrue(all(c > 0.6 for c in o["recall"]["cob"]), o["recall"])
        self.assertGreater(o["clasico"]["n"], 200)
        self.assertGreater(o["clasico"]["pos"], 0.05)


SEGMENTA_EXT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
base = sys.argv[2]
import numpy as np
from shapely.geometry import Point, box
import laminillas_comun as C
import laminillas_proc as P
import laminillas_sello as SL
import laminillas_congela as K
import laminillas_metricas as MET

SL.sella(os.path.join(base, SL.FICHERO), {
    "umbral": {"rige": "grandqc"}, "vectores": {}, "residuo": {}, "precongelacion": {},
    "parametros": K.parametros()}, "2026-10-02")

class Lam:
    def __init__(self, n):
        self.opaco, self.mpp_l0, self.dimensiones_l0 = n, 0.25, (4000, 2000)

class Lector:
    @staticmethod
    def abre(n):
        return Lam(n)

kw = []
def seg(lector, lamina, nombre, **k):
    kw.append(k)
    pol = [Point(200 + 30 * i, 300).buffer(12) for i in range(80)]
    return {"celulas": pol, "celulas_desplazadas": pol[:79], "mpp_l0": 0.25,
            "dispositivo": "cpu", "lote": 1, "aviso_check_input_tile": False,
            "teselado": {"pasa": True}, "pesos": {},
            "grandqc": {"carga": True, "artefactos": [(box(150, 250, 800, 350), "fold")]}}

acuerdos = []
def acuerdo(lector, nombre, pol, semilla):                # el de píxeles, en ProcAcuerdoArea
    acuerdos.append([nombre, len(pol), semilla])
    return dict(K.S.acuerdo_area(np.full(300, 0.95)), ventanas=20)
P.acuerdo_area = acuerdo
P.segmenta(base, ["P-KI67", "P-CK19"], lector=Lector, segmentador=seg, log=lambda m: None)
z = np.load(os.path.join(base, "segmenta", "nucleos_P-KI67.npz"))
z2 = np.load(os.path.join(base, "segmenta", "nucleos_P-CK19.npz"))
j = json.load(open(os.path.join(base, "segmenta", "nucleos_P-KI67.json")))
out = {"kw": [sorted(k.items()) for k in kw], "ki67": sorted(z.files), "ck19": sorted(z2.files),
       "marcados": int(z["grandqc"].sum()), "gq_carga": j["grandqc"]["carga"],
       "lecturas": [json.loads(l)["que"] for l in open(os.path.join(base, "lecturas_diana.jsonl"))],
       "acuerdos": acuerdos, "acuerdo_ki67": j["acuerdo_area"]["pasa"],
       "area_ki67": j["area_nuclear"],
       "sin_ck19": json.load(open(os.path.join(base, "segmenta", "nucleos_P-CK19.json")))["sin"]}
json.dump(out, open(os.path.join(base, "out.json"), "w"), default=str)
"""


@unittest.skipUnless(os.path.exists(VENV_PAT), "sin venv patologia")
class ProcSegmentaPiloto(unittest.TestCase):
    """Tras el sello, `segmenta` corre la rejilla desplazada en P-KI67 (la lista post-congelación
    repite ahí el punto 2) y GrandQC donde el sello lo hace regir; deja constancia de la lectura."""

    def test_desplazada_en_ki67_y_grandqc_si_rige(self):
        d = os.path.realpath(tempfile.mkdtemp(prefix="lam-seg-ext-"))
        try:
            r = subprocess.run([VENV_PAT, "-c", SEGMENTA_EXT, TOOLS, d], capture_output=True,
                               text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            with open(os.path.join(d, "out.json")) as f:
                o = json.load(f)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(o["kw"][0], [["con_desplazada", True], ["con_grandqc", True]])
        self.assertEqual(o["kw"][1], [["con_desplazada", False], ["con_grandqc", True]])
        self.assertIn("centroide_desplazada", o["ki67"])
        self.assertNotIn("centroide_desplazada", o["ck19"])
        self.assertIn("grandqc", o["ki67"])
        self.assertTrue(o["gq_carga"])
        self.assertGreater(o["marcados"], 0)
        self.assertEqual(o["lecturas"], ["celulas", "acuerdo_area", "celulas"])
        # la puerta del área (CRITERIO_AREA) en P-KI67, con la semilla maestra del sello + 19
        self.assertEqual(o["acuerdos"], [["P-KI67", 80, 20261001 + 19]])
        self.assertTrue(o["acuerdo_ki67"])
        self.assertEqual(o["area_ki67"]["puerta"], "acuerdo_area")       # 25-80: referencia
        self.assertEqual(o["area_ki67"]["referencia_plan_um2"], [25.0, 80.0])
        self.assertIn("acuerdo de área", o["sin_ck19"])


# `acuerdo_area` de proc con píxeles: la MISMA puerta que la pre-congelación (congela), sobre una
# lámina sintética con núcleos de tamaño conocido (verdad física) y con un error de escala.
PROC_ACUERDO = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[3])
base = sys.argv[2]
from shapely import affinity
import _laminillas_piloto_a as F
import laminillas_proc as P
import laminillas_congela as K
import laminillas_segmenta as S
lam = F.lamina("P-KI67", 21)
lector = F.LectorSint({"P-KI67": lam})
pol = F.poligonos(lam.verdad["nucleos"])
r = P.acuerdo_area(lector, "P-KI67", pol, K.SEMILLA)
ref = K.acuerdo_area_lamina(lector, lam, lam.zona, lector.i0_local(lam), pol, S.centroides(pol),
                            K.SEMILLA)
mitad = [affinity.scale(p, 0.5, 0.5, origin="centroid") for p in pol]   # escala lineal ×½
r2 = P.acuerdo_area(lector, "P-KI67", mitad, K.SEMILLA)
out = {"pasa": r["pasa"], "mediana": r["mediana"], "n_pares": r["n_pares"],
       "igual_que_congela": json.dumps(r, sort_keys=True, default=str)
                            == json.dumps(ref, sort_keys=True, default=str),
       "criterio": r["criterio"] == S.CRITERIO_AREA, "mitad": [r2["pasa"], r2["n_pares"]]}
json.dump(out, open(os.path.join(base, "out.json"), "w"))
"""


@unittest.skipUnless(os.path.exists(VENV_PAT), "sin venv patologia")
class ProcAcuerdoArea(unittest.TestCase):
    """Punto 4 (i): `laminillas_proc.acuerdo_area` es la puerta de la pre-congelación
    (`laminillas_congela.acuerdo_area_lamina`, CRITERIO_AREA), no una copia: mismo resultado,
    pasa con la verdad y no pasa con un error de escala ×½ (0 pares a IoU > 0,5)."""

    def test_misma_puerta_que_la_precongelacion(self):
        d = os.path.realpath(tempfile.mkdtemp(prefix="lam-acuerdo-"))
        try:
            r = subprocess.run([VENV_PAT, "-c", PROC_ACUERDO, TOOLS, d,
                                os.path.join(RAIZ, "tests")], capture_output=True, text=True,
                               timeout=600)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            with open(os.path.join(d, "out.json")) as f:
                o = json.load(f)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertTrue(o["pasa"], o)
        self.assertAlmostEqual(o["mediana"], 1.0, delta=0.15)
        self.assertGreaterEqual(o["n_pares"], 200)
        self.assertTrue(o["igual_que_congela"])
        self.assertTrue(o["criterio"])
        self.assertFalse(o["mitad"][0])
        self.assertLess(o["mitad"][1], 200)


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=1).result
    n = r.testsRun
    print("test_laminillas: %d casos, %d fallos, %d errores, %d saltados"
          % (n, len(r.failures), len(r.errors), len(r.skipped)))
    sys.exit(0 if r.wasSuccessful() else 1)
