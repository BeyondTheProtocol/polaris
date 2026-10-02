#!/usr/bin/env python3
"""tests/test_laminillas_n1_jaula.py — la jaula `vision.sb` de la llamada HTTP de vision_n1 y
nube_n1 (plan «laminillas DFCI», «F1. Jaulas») y el `config.json` sellado de nube_n1.

SIN DATOS Y SIN RED EXTERNA. Todo canario es sintético y vive en un árbol temporal que hace de HOME
y de casa base; las pocas pruebas sobre el perfil REAL (HOME de verdad) solo comprueban que algo
FALLA, sin imprimir nada de lo que hay. Ninguna llamada a Google ni a Scaleway: el único socket es
un servidor TLS local con certificado autofirmado (el hijo tiene que NEGARSE a hablarle).

Cubre:
  · Dentro de la jaula que monta vision_n1 (`perfil_jaula` + `comando_jaula` + `entorno_jaula`):
    leer un canario en zona clínica, en SESION, en `tools/.x_secrets.json`, bajo un `_PRIVADO_*`,
    en `~/.polaris-venvs` o fuera de todo FALLA; N1 se lee; no se escribe en ningún sitio; el
    Llavero está denegado (exec de /usr/bin/security y mach-lookup); el entorno es la lista blanca
    con la clave y nada del padre.
  · `http_en_jaula`: la clave sale de las cabeceras y va SOLO por el entorno (ni argv ni stdin); el
    perfil es propio de la llamada (0600) y se borra; el hijo se niega a otro host, a http y sin
    clave; dentro de la jaula hay red pero el TLS se verifica (autofirmado → ErrorRed sin la clave).
  · Los transportes reales de vision_n1 y nube_n1 van por la jaula (Gemini con x-goog-api-key,
    API de Scaleway con X-Auth-Token, S3 sin clave; X-Auth-Token hacia S3 no sale).
  · Perfil REAL: la jaula no lista `~/Clinico-PRIVADO` ni SESION ni lee los `tools/.x_secrets.json`
    reales, y el Llavero falla.
  · `config.json` sellado: pasa `exigir_sellada`, con origen y fecha en cada valor, y el sha256 de
    cada checkpoint casa con la caché propia (si está).
  · La instalación de la máquina: `pip` todo fijado con ==, coherente con los `requires_dist` de
    cellvit 1.0.9 y pathopatch 1.0.10 (o con lo que trae la imagen), y `exigir_sellada` rechaza
    pins sueltos. El `_ejecuta.sh` RELLENADO corre en bash con docker/curl/nvidia-smi falsos: sano,
    instala la lista exacta (solo ruedas salvo `pip_solo_fuente`), la comprueba sin red y escribe
    DONE; si pip o la comprobación o un sha256 fallan, FAILED con el paso y la última línea del log
    saneada, y no sigue.
"""
import hashlib
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(RAIZ, "tools")
PY = sys.executable
SANDBOX = "/usr/bin/sandbox-exec"
PY_JAULA = "/opt/homebrew/bin/python3.12"
OPENSSL = shutil.which("openssl") or "/usr/bin/openssl"
CLAVE = "clave-canario-sintetica-JAULA-0123456789"
HAY_JAULA = os.path.exists(SANDBOX) and os.path.exists(PY_JAULA)
CACHE_CVPP = os.path.expanduser("~/.polaris-venvs/cache/cellvitpp")

# Corre DENTRO de la jaula (Python -I -S, sin leer ficheros del repo).
SONDA = r"""
import json, os, subprocess, sys
out = {}
for k, r in json.loads(sys.argv[1]).items():
    try:
        with open(r, "rb") as f:
            f.read(1)
        out[k] = "lee"
    except Exception as e:
        out[k] = type(e).__name__
for k, r in json.loads(sys.argv[2]).items():
    try:
        with open(r, "w") as f:
            f.write("x")
        out["W:" + k] = "escribe"
    except Exception as e:
        out["W:" + k] = type(e).__name__
for k, r in json.loads(sys.argv[3]).items():
    try:
        os.listdir(r)
        out["L:" + k] = "lista"
    except Exception as e:
        out["L:" + k] = type(e).__name__
try:
    out["llavero"] = subprocess.call(["/usr/bin/security", "find-generic-password", "-s",
                                      "canario-sintetico-jaula-n1"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
except Exception as e:
    out["llavero"] = type(e).__name__
out["entorno"] = sorted(os.environ)
out["clave"] = os.environ.get("BTP_CLAVE_HIJO")
print(json.dumps(out))
"""

# Corre en un subproceso con el entorno que se le dé (HOME y casa base sintéticos o reales):
# monta la jaula con las funciones de vision_n1 y lanza `codigo` dentro.
EN_JAULA = r"""
import json, os, subprocess, sys
sys.path.insert(0, %r)
import vision_n1 as V
a = json.loads(sys.argv[1])
perfil = V.perfil_jaula(a["dir"])
try:
    modo = oct(os.stat(perfil).st_mode & 0o777)
    with open(perfil, encoding="utf-8") as fh:
        texto = fh.read()
    import laminillas_jaulas as J
    cmd = V.comando_jaula(perfil, a["codigo"]) + a["args"]
    r = subprocess.run(cmd, env=V.entorno_jaula(a.get("clave")), capture_output=True, text=True,
                       cwd="/", timeout=120)
    print(json.dumps({"rc": r.returncode, "out": r.stdout, "err_bytes": len(r.stderr), "modo": modo,
                      "igual_que_J": texto == J.perfil("vision"), "texto": texto if a.get("texto") else "",
                      "n1": J.N1, "venvs": J.VENVS, "cmd_sin_clave": all(%r not in c for c in cmd)}))
finally:
    os.unlink(perfil)
""" % (TOOLS, CLAVE)


def _escribe(ruta, texto="canario-sintetico"):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w") as f:
        f.write(texto)


def _ultima_json(r):
    lineas = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    if r.returncode != 0 or not lineas:
        raise AssertionError("subproceso rc=%s: %s" % (r.returncode, (r.stderr or "")[-1500:]))
    return json.loads(lineas[-1])


class _Arbol(unittest.TestCase):
    """HOME, casa base y estado sintéticos; vision_n1 se importa SIEMPRE en un subproceso."""

    @classmethod
    def setUpClass(cls):
        cls.t = os.path.realpath(tempfile.mkdtemp(prefix="lam-n1-jaula-"))
        t = cls.t
        cls.home = os.path.join(t, "home")
        cls.repo = os.path.join(t, "repo")
        cls.estado = os.path.join(t, "estado")
        cls.jaulas = os.path.join(t, "jaulas")
        lam = os.path.join(cls.home, "Clinico-PRIVADO", "laminillas-DFCI")
        cls.sesion = os.path.join(lam, "sesion")
        cls.n1 = os.path.join(cls.home, "Laminillas-N1")
        fuera = os.path.join(t, "fuera")
        cls.lectura = {
            "clinica": os.path.join(cls.home, "Clinico-PRIVADO", "otra", "c.txt"),
            "sesion": os.path.join(cls.sesion, "c.txt"),
            "secretos": os.path.join(cls.repo, "tools", ".x_secrets.json"),
            "privado": os.path.join(fuera, "a", "_PRIVADO_CLINICO", "c.txt"),
            "venvs": os.path.join(cls.home, ".polaris-venvs", "cache", "c.txt"),
            "neutro": os.path.join(fuera, "neutro", "c.txt"),
            "n1": os.path.join(cls.n1, "c.txt"),
        }
        for r in cls.lectura.values():
            _escribe(r)
        cls.escritura = {"n1": os.path.join(cls.n1, "w.txt"),
                         "fuera": os.path.join(fuera, "neutro", "w.txt"),
                         "sesion": os.path.join(cls.sesion, "w.txt"),
                         "tmp": os.path.join(t, "w.txt")}
        cls.listados = {"clinico": os.path.join(cls.home, "Clinico-PRIVADO"), "sesion": cls.sesion}
        os.makedirs(cls.estado)
        cls.env = {"PATH": "/usr/bin:/bin", "HOME": cls.home, "BTP_REPO": cls.repo,
                   "BTP_STATE_DIR": cls.estado, "BTP_TEST_BATTERY": "1",
                   "BTP_CANARIO_PADRE": "no-tiene-que-cruzar", "TMPDIR": t}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.t, ignore_errors=True)

    def en_jaula(self, codigo, args=(), clave=None, env=None, texto=False):
        a = {"dir": self.jaulas, "codigo": codigo, "args": list(args), "clave": clave, "texto": texto}
        r = subprocess.run([PY, "-c", EN_JAULA, json.dumps(a)], env=env or self.env,
                           capture_output=True, text=True, timeout=180)
        return _ultima_json(r)

    def py(self, codigo, env=None):
        r = subprocess.run([PY, "-c", "import json, os, sys\nsys.path.insert(0, %r)\n" % TOOLS + codigo],
                           env=env or self.env, capture_output=True, text=True, timeout=180)
        return _ultima_json(r)


@unittest.skipUnless(HAY_JAULA, "sin sandbox-exec o sin %s" % PY_JAULA)
class JaulaSintetica(_Arbol):

    def sonda(self):
        for r in self.escritura.values():
            if os.path.exists(r):
                os.unlink(r)
        v = self.en_jaula(SONDA, [json.dumps(self.lectura), json.dumps(self.escritura),
                                  json.dumps(self.listados)], clave=CLAVE)
        self.assertEqual(v["rc"], 0, v)
        return v, json.loads(v["out"])

    def test_canarios_fallan_y_n1_se_lee(self):
        v, res = self.sonda()
        self.assertTrue(v["igual_que_J"], "vision_n1 no monta laminillas_jaulas.perfil('vision')")
        self.assertEqual(v["n1"], self.n1)
        for k in ("clinica", "sesion", "secretos", "privado", "venvs", "neutro"):
            self.assertNotEqual(res[k], "lee", "la jaula lee %s" % k)
        self.assertEqual(res["n1"], "lee")
        for k in ("clinico", "sesion"):
            self.assertNotEqual(res["L:" + k], "lista", "la jaula lista %s" % k)

    def test_no_escribe_nada(self):
        _v, res = self.sonda()
        for k in self.escritura:
            self.assertNotEqual(res["W:" + k], "escribe", "la jaula escribe en %s" % k)
            self.assertFalse(os.path.exists(self.escritura[k]), k)

    def test_llavero_denegado(self):
        v, res = self.sonda()
        self.assertNotEqual(res["llavero"], 0)
        texto = self.en_jaula("print('x')", texto=True)["texto"]
        self.assertIn('(deny mach-lookup (global-name "com.apple.SecurityServer")', texto)
        self.assertIn('(global-name "com.apple.securityd")', texto)
        self.assertIn('(deny process-exec (literal "/usr/bin/security"))', texto)
        # y como primer exec de la jaula, no solo desde el hijo de Python
        gen = ("import vision_n1 as V, subprocess\np = V.perfil_jaula(%r)\n"
               "r = subprocess.run([V.SANDBOX, '-f', p, '/usr/bin/security', 'find-generic-password', '-s',"
               " 'canario-sintetico-jaula-n1'], capture_output=True, text=True)\nos.unlink(p)\n"
               "print(json.dumps([r.returncode, 'Operation not permitted' in r.stderr]))" % self.jaulas)
        rc, eperm = self.py(gen)
        self.assertNotEqual(rc, 0)
        self.assertTrue(eperm)

    def test_entorno_lista_blanca_con_la_clave(self):
        v, res = self.sonda()
        self.assertEqual(res["clave"], CLAVE)
        self.assertTrue(v["cmd_sin_clave"], "la clave en argv")
        self.assertNotIn("BTP_CANARIO_PADRE", res["entorno"])
        self.assertNotIn("HOME", res["entorno"])
        # lo que no es nuestro lo pone el propio sistema/intérprete (p. ej. __CF_USER_TEXT_ENCODING)
        propios = set(res["entorno"]) - {"PATH", "BTP_CLAVE_HIJO"}
        self.assertFalse(propios & set(self.env), propios)

    def test_perfil_lee_solo_n1_y_no_escribe(self):
        v = self.en_jaula("print('x')", texto=True)
        self.assertEqual(v["modo"], "0o600")
        allows = [ln for ln in v["texto"].splitlines() if ln.startswith("(allow file-read*")]
        propios = [ln for ln in allows if self.home in ln or v["venvs"] in ln]
        self.assertEqual(len(propios), 1, propios)
        self.assertIn('(subpath "%s")' % self.n1, propios[0])
        # En la sección de allows no aparece VENVS. El bloque FINAL de secretos (2-oct-26) sí
        # puede nombrarlo, pero solo en deny (el token del HF_HOME de la ventanilla).
        seccion = v["texto"].split(";; ── allows")[-1].split(";; ── FINAL")[0]
        self.assertNotIn(v["venvs"], seccion)
        final = v["texto"].split(";; ── FINAL")[-1].splitlines()[1:]
        self.assertTrue(final and all(ln.startswith("(deny ") for ln in final), final[:3])
        escr = [ln for ln in v["texto"].splitlines() if ln.startswith("(allow file-write*")]
        self.assertEqual(len(escr), 1, escr)
        self.assertIn('(literal "/dev/null")', escr[0])
        self.assertEqual(os.listdir(self.jaulas), [], "el perfil de la llamada no se borró")


@unittest.skipUnless(HAY_JAULA, "sin sandbox-exec o sin %s" % PY_JAULA)
class HijoHttp(_Arbol):

    def hijo(self, pet, entorno=None, cuerpo=b""):
        """El código del hijo, SIN jaula, con una petición a mano: se niega antes de abrir red."""
        cod = "import sys; sys.path.insert(0, %r); import vision_n1; print(vision_n1.HIJO_HTTP)" % TOOLS
        hijo = subprocess.run([PY, "-c", cod], env=self.env, capture_output=True, text=True).stdout
        r = subprocess.run([PY_JAULA, "-I", "-S", "-c", hijo], input=json.dumps(pet).encode() + b"\n" + cuerpo,
                           env=entorno or {"PATH": "/usr/bin:/bin"}, capture_output=True, timeout=60)
        linea, _s, resto = r.stdout.partition(b"\n")
        return r.returncode, json.loads(linea.decode()), resto, r.stdout + r.stderr

    def pet(self, url, **kw):
        p = {"metodo": "GET", "url": url, "cabeceras": {}, "timeout": 5, "hosts": ["api.scaleway.com"],
             "sufijos": [], "cabecera_clave": None, "con_cuerpo": False, "bytes": 0}
        p.update(kw)
        return p

    def test_hijo_se_niega_a_otro_host_a_http_y_sin_clave(self):
        rc, cab, _r, todo = self.hijo(self.pet("https://evil.example.com/x"),
                                      {"PATH": "/usr/bin:/bin", "BTP_CLAVE_HIJO": CLAVE})
        self.assertEqual((rc, cab["n"]), (3, 0))
        self.assertIn("destino no permitido", cab["error"])
        self.assertNotIn(CLAVE.encode(), todo)
        rc, cab, _r, _t = self.hijo(self.pet("http://api.scaleway.com/x"))
        self.assertEqual(rc, 3)
        rc, cab, _r, _t = self.hijo(self.pet("https://api.scaleway.com.evil.com/x"))
        self.assertEqual(rc, 3)
        rc, cab, _r, _t = self.hijo(self.pet("https://api.scaleway.com/x", cabecera_clave="X-Auth-Token"))
        self.assertEqual(rc, 3)
        self.assertIn("sin clave", cab["error"])

    def test_clave_solo_por_entorno_y_perfil_propio_borrado(self):
        """`http_en_jaula` con un Popen FALSO: qué argv, qué entorno y qué stdin recibiría el hijo."""
        cod = r"""
import io
import vision_n1 as V
vista = {}
class Entrada(io.BytesIO):
    def close(self):
        vista["stdin"] = self.getvalue().decode("latin-1")
class Falso:
    def __init__(self, cmd, stdin=None, stdout=None, stderr=None, env=None, cwd=None):
        vista.update(cmd=cmd, env=env, cwd=cwd, perfil_existe=os.path.exists(cmd[2]),
                     modo=oct(os.stat(cmd[2]).st_mode & 0o777), perfil=cmd[2])
        self.stdin = Entrada()
        self.stdout = io.BytesIO(b'{"status": 200, "cabeceras": {"x": "1"}, "n": 2}\nok')
    def wait(self):
        return 0
    def kill(self):
        pass
V.subprocess.Popen = Falso
r = V.http_en_jaula("POST", "https://generativelanguage.googleapis.com/v1beta/x", b'{"a": 1}',
                    {"Content-Type": "application/json", "x-goog-api-key": %r}, 30,
                    hosts=("generativelanguage.googleapis.com",), cabecera_clave="x-goog-api-key",
                    jaulas_dir=%r)
vista["r"] = [r[0], r[1], r[2].decode()]
vista["borrado"] = not os.path.exists(vista["perfil"])
vista["quedan"] = os.listdir(%r)
print(json.dumps(vista))
""" % (CLAVE, self.jaulas, self.jaulas)
        v = self.py(cod)
        self.assertEqual(v["r"], [200, {"x": "1"}, "ok"])
        self.assertEqual(v["env"], {"PATH": "/usr/bin:/bin", "BTP_CLAVE_HIJO": CLAVE})
        self.assertFalse(any(CLAVE in c for c in v["cmd"]), "la clave en argv")
        self.assertNotIn(CLAVE, v["stdin"], "la clave en stdin")
        cab = json.loads(v["stdin"].split("\n", 1)[0])
        self.assertEqual(cab["cabeceras"], {"Content-Type": "application/json"})
        self.assertEqual((cab["cabecera_clave"], cab["bytes"], cab["con_cuerpo"]), ("x-goog-api-key", 8, True))
        self.assertTrue(v["stdin"].endswith('{"a": 1}'))
        self.assertEqual(v["cmd"][:2], ["/usr/bin/sandbox-exec", "-f"])
        self.assertEqual(v["cmd"][3:6], [PY_JAULA, "-I", "-S"])
        self.assertTrue(v["perfil_existe"])
        self.assertEqual(v["modo"], "0o600")
        self.assertTrue(v["borrado"])
        self.assertEqual(v["quedan"], [])
        self.assertEqual(v["cwd"], "/")

    @unittest.skipUnless(os.path.exists(OPENSSL), "sin openssl")
    def test_red_si_pero_tls_verificado(self):
        """Servidor TLS local con certificado autofirmado: el hijo, dentro de la jaula, conecta (hay
        red) y se NIEGA (verifica el certificado). ErrorRed, sin la clave, y sin perfil que quede."""
        d = os.path.join(self.t, "tls")
        os.makedirs(d, exist_ok=True)
        cert, key = os.path.join(d, "c.pem"), os.path.join(d, "k.pem")
        r = subprocess.run([OPENSSL, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key,
                            "-out", cert, "-subj", "/CN=localhost", "-days", "1"], capture_output=True)
        if r.returncode:
            self.skipTest("openssl no generó el certificado")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        srv.settimeout(30)
        puerto = srv.getsockname()[1]
        vistos = []

        def atiende():
            try:
                c, _a = srv.accept()
                c.settimeout(10)
                vistos.append("conexion")
                try:
                    ctx.wrap_socket(c, server_side=True).recv(10)
                    vistos.append("PETICION")
                except (ssl.SSLError, OSError):
                    vistos.append("tls_rechazado")
                finally:
                    c.close()
            except OSError:
                pass

        h = threading.Thread(target=atiende, daemon=True)
        h.start()
        cod = r"""
import vision_n1 as V
try:
    V.http_en_jaula("GET", "https://localhost:%d/v1/x", None, {"X-Auth-Token": %r}, 20,
                    hosts=("localhost",), cabecera_clave="X-Auth-Token", jaulas_dir=%r)
    print(json.dumps(["RESPONDE", ""]))
except V.ErrorRed as e:
    print(json.dumps(["ErrorRed", str(e)]))
except V.PuertaCerrada as e:
    print(json.dumps(["PuertaCerrada", str(e)]))
""" % (puerto, CLAVE, self.jaulas)
        tipo, msg = self.py(cod)
        h.join(10)
        srv.close()
        self.assertEqual(tipo, "ErrorRed", msg)
        self.assertIn("CERTIFICATE_VERIFY_FAILED", msg)
        self.assertNotIn(CLAVE, msg)
        self.assertIn("conexion", vistos)
        self.assertNotIn("PETICION", vistos)
        self.assertEqual(os.listdir(self.jaulas), [])


class Transportes(_Arbol):
    """Los transportes reales delegan en la jaula (http_en_jaula sustituido: no se abre nada)."""

    def test_vision_y_nube_van_por_la_jaula(self):
        env = dict(self.env)
        env.pop("BTP_TEST_BATTERY")
        cod = r"""
import vision_n1 as V, nube_n1 as N
vistas = []
def falso(metodo, url, cuerpo, cab, timeout, hosts, sufijos=(), cabecera_clave=None, jaulas_dir=None):
    vistas.append([metodo, url, sorted(cab), list(hosts), list(sufijos), cabecera_clave])
    if "fallo" in url:
        raise V.ErrorRed("sin red (falso)")
    return 200, {}, b"{}"
V.http_en_jaula = falso
out = {}
out["gemini"] = V.transporte_https("https://generativelanguage.googleapis.com/v1beta/models/m:generateContent",
                                   b"{}", {"Content-Type": "application/json", "x-goog-api-key": "k"})[0]
out["api"] = N.transporte_https("GET", "https://api.scaleway.com/instance/v1/zones/fr-par-2/servers", None,
                                {"X-Auth-Token": "k"})[0]
out["s3"] = N.transporte_https("PUT", "https://btp-n1-0123.s3.fr-par.scw.cloud/o", b"x",
                               {"Authorization": "AWS4-HMAC-SHA256 …"})[0]
for nombre, f in (("s3_token", lambda: N.transporte_https("GET", "https://btp-n1-0123.s3.fr-par.scw.cloud/o",
                                                           None, {"X-Auth-Token": "k"})),
                  ("nube_red", lambda: N.transporte_https("GET", "https://api.scaleway.com/fallo", None,
                                                           {"X-Auth-Token": "k"})),
                  ("vision_red", lambda: V.transporte_https("https://generativelanguage.googleapis.com/fallo",
                                                            b"{}", {"x-goog-api-key": "k"})),
                  ("vision_otro", lambda: V.transporte_https("https://evil.example.com/x", b"{}", {}))):
    try:
        f()
        out[nombre] = "SALE"
    except Exception as e:
        out[nombre] = type(e).__name__
out["vistas"] = vistas
print(json.dumps(out))
"""
        v = self.py(cod, env=env)
        self.assertEqual((v["gemini"], v["api"], v["s3"]), (200, 200, 200))
        g, api, s3, nube_red, vision_red = v["vistas"]
        self.assertEqual(g[3:], [["generativelanguage.googleapis.com"], [], "x-goog-api-key"])
        self.assertEqual(api[3:], [["api.scaleway.com"], [], "X-Auth-Token"])
        self.assertEqual(s3[3:], [["s3.fr-par.scw.cloud"], ["s3.fr-par.scw.cloud"], None])
        self.assertEqual(v["s3_token"], "PuertaCerrada")
        self.assertEqual(v["nube_red"], "ErrorNube")
        self.assertEqual(v["vision_red"], "ErrorProveedor")
        self.assertEqual(v["vision_otro"], "PuertaCerrada")
        self.assertEqual(len(v["vistas"]), 5)          # X-Auth-Token hacia S3 y evil: ni se llama

    def test_bateria_niega_antes_de_montar_la_jaula(self):
        cod = r"""
import vision_n1 as V, nube_n1 as N
V.http_en_jaula = lambda *a, **k: (_ for _ in ()).throw(AssertionError("montó la jaula"))
out = []
for f in (lambda: V.transporte_https("https://generativelanguage.googleapis.com/x", b"", {}),
          lambda: N.transporte_https("GET", "https://api.scaleway.com/x", None, {})):
    try:
        f()
        out.append("SALE")
    except V.PuertaCerrada as e:
        out.append(str(e))
print(json.dumps(out))
"""
        for m in self.py(cod):
            self.assertIn("batería de tests", m)


FALSA = "FAKEKEY0123456789abcdef"                    # clave FALSA del verificador (2-oct-26)
FALSA_NL = "FAKEKEYNL_\nCOLA_FALSA"

# El Popen de la jaula, FALSO: apunta si se llegó a lanzar el hijo (y con qué stdin y entorno).
POPEN_FALSO = r"""
import io
import vision_n1 as V
lanzados = []
class _Entrada(io.BytesIO):
    def close(self):
        lanzados[-1]["stdin"] = self.getvalue().decode("latin-1")
class _Falso:
    def __init__(self, cmd, stdin=None, stdout=None, stderr=None, env=None, cwd=None):
        lanzados.append({"env": env})
        self.stdin = _Entrada()
        self.stdout = io.BytesIO(b'{"status": 200, "cabeceras": {}, "n": 0}\n')
    def wait(self):
        return 0
    def kill(self):
        pass
V.subprocess.Popen = _Falso
"""


def _trozos(clave, n=6):
    """Todos los trozos de `clave` de `n` caracteres: ninguno puede quedar en un texto de error."""
    return {clave[i:i + n] for i in range(len(clave) - n + 1)}


class CabecerasYClave(_Arbol):
    """Verificador independiente, 2-oct-26, con clave FALSA (sin red, sin Llavero, sin jaula real):
    (a) la guarda «nada de X-Auth-Token hacia S3» comparaba el nombre exacto y «X-Auth-Token » o
    « X-Auth-Token» pasaban al transporte; (b) el error del hijo se cortaba a 300 caracteres ANTES de
    limpiar la clave y una clave a caballo del corte salía a medias; (c) una clave con «\n» hacía que
    http.client lanzara un ValueError con su repr, y la clave entera llegaba al error del padre."""

    def _transporte(self, llamadas):
        """[(resultado, ¿lanzó el hijo?, ¿algún trozo de la clave en el error o en el stdin?)] de cada
        (modulo, args) con el transporte REAL (sin batería) y el Popen de la jaula falso."""
        env = dict(self.env)
        env.pop("BTP_TEST_BATTERY")
        cod = POPEN_FALSO + "V.JAULAS_DIR = %r\n" % self.jaulas + r"""
import nube_n1 as N
claves, out = %r, []
for mod, args in %r:
    del lanzados[:]
    try:
        (V if mod == "V" else N).transporte_https(*args)
        res, texto = "SALE", ""
    except Exception as e:
        res, texto = type(e).__name__, str(e)
    vistos = texto + "".join(l.get("stdin", "") for l in lanzados)
    out.append([res, bool(lanzados), any(t in vistos for c in claves for t in c)])
print(json.dumps(out))
""" % ([sorted(_trozos(FALSA)), sorted(_trozos("FAKEKEYNL_")), sorted(_trozos("COLA_FALSA"))], llamadas)
        return self.py(cod, env=env)

    def test_cabecera_de_clave_con_otro_nombre_no_sale(self):
        s3 = "https://btp-n1-0123.s3.fr-par.scw.cloud/o"
        api = "https://api.scaleway.com/instance/v1/zones/fr-par-2/servers"
        gem = "https://generativelanguage.googleapis.com/v1beta/models/m:generateContent"
        malas = [("N", ["GET", s3, None, {nombre: FALSA}])
                 for nombre in ("X-Auth-Token ", " X-Auth-Token", "X-Auth-Token\t", "X-Auth-Token\n",
                                "X-Auth-Token:", "X-Auth-Tokеn", "x-auth-token", "x-goog-api-key")]
        malas += [("N", ["GET", api, None, {"X-Auth-Token": FALSA, "x-auth-token": FALSA}]),
                  ("N", ["GET", api, None, {"X-Auth-Token": FALSA, "x-goog-api-key": FALSA}]),
                  ("N", ["GET", api, None, {"X-Auth-Token": FALSA, "X-Extra": "a\r\nX-Auth-Token: " + FALSA}]),
                  ("V", [gem, b"{}", {"x-goog-api-key ": FALSA}])]
        buenas = [("N", ["GET", api, None, {"X-Auth-Token": FALSA}]),
                  ("N", ["PUT", s3, b"x", {"Content-Type": "application/octet-stream", "x-amz-date": "20261002T000000Z",
                                           "Authorization": "AWS4-HMAC-SHA256 Credential=SCW/x, Signature=0"}]),
                  ("V", [gem, b"{}", {"Content-Type": "application/json", "x-goog-api-key": FALSA}])]
        res = self._transporte(malas + buenas)
        for (mod, args), (r, lanzo, trozo) in zip(malas, res[:len(malas)]):
            self.assertEqual((r, lanzo), ("PuertaCerrada", False), (mod, sorted(args[-1]), r))
            self.assertFalse(trozo, "la clave en el error: %r" % sorted(args[-1]))
        for (mod, args), (r, lanzo, trozo) in zip(buenas, res[len(malas):]):
            self.assertEqual((r, lanzo, trozo), ("SALE", True, False), (mod, sorted(args[-1])))

    def test_clave_con_salto_no_llega_ni_al_hijo(self):
        """(c) La clave se valida en el padre (ASCII visible, sin espacios ni saltos) antes de lanzar
        el hijo; el mensaje no la lleva."""
        res = self._transporte([("N", ["GET", "https://api.scaleway.com/x", None, {"X-Auth-Token": c}])
                                for c in (FALSA_NL, FALSA + " ", " " + FALSA, "FAKE KEY 01234", "FAKEKEY\x00NUL",
                                          "FAKEKEYñ0123")])
        for r, lanzo, trozo in res:
            self.assertEqual((r, lanzo, trozo), ("PuertaCerrada", False, False))

    def test_error_se_limpia_antes_de_cortar(self):
        """(b) Padre e hijo: la clave a caballo del corte de 300, la clave escapada (repr de str y de
        bytes, JSON, URL) y trozos sueltos de ≥6 caracteres no quedan en el texto de error."""
        claves = [FALSA, FALSA_NL, FALSA + "/=?"]
        cod = r"""
import urllib.parse
import vision_n1 as V
F, NL = %r, %r
textos = ["x" * 290 + F, "x" * 297 + F, "a" * 5 + F + "b" * 400, "x" * 290 + NL, "x" * 280 + NL,
          "Invalid header value %%r" %% (NL.encode(),), "valor %%r" %% (NL,), json.dumps({"k": NL}),
          "url ?key=" + urllib.parse.quote(F + "/=?", safe=""), "cola " + F[3:17], "cola " + NL[4:16]]
out = {"padre": [], "hijo": [], "respuesta_hijo": [], "gemini": []}
ns = {}
cod = V.HIJO_HTTP
exec(cod[:cod.index("def sal(")], ns)
for t in textos:
    for i, clave in enumerate(%r):
        try:
            out["padre"].append([i, V._limpia_clave(t, clave, 300)])
        except TypeError:                      # la de antes no cortaba: limpiar y cortar, en ese orden
            out["padre"].append([i, V._limpia_clave(t, clave)[:300]])
        ns["CLAVE"] = clave
        out["hijo"].append([i, ns["limpia"](t)])
        try:
            V._respuesta_hijo(json.dumps({"error": t, "n": 0}).encode() + b"\n", 4, clave)
        except V.ErrorRed as e:
            out["respuesta_hijo"].append([i, str(e)])
        try:
            V.gemini_respuesta(400, json.dumps({"error": {"message": t}}).encode(), clave)
        except V.ErrorProveedor as e:
            out["gemini"].append([i, str(e)])
print(json.dumps(out))
""" % (FALSA, FALSA_NL, claves)
        out = self.py(cod)
        import urllib.parse
        trozos = []                            # por clave: los suyos, los de sus formas y los de cada línea
        for c in claves:
            formas = {c, repr(c)[1:-1], json.dumps(c)[1:-1], urllib.parse.quote(c, safe="")}
            trozos.append(set().union(*[_trozos(f) for f in formas], *[_trozos(p) for p in c.split("\n")]))
        for donde, textos in out.items():
            self.assertEqual(len(textos), 33, donde)
            for i, t in textos:
                self.assertFalse([tr for tr in trozos[i] if tr in t], (donde, i, t[-90:]))
        self.assertTrue(all(len(t) <= 300 for _i, t in out["padre"] + out["hijo"]))

    @unittest.skipUnless(HAY_JAULA, "sin sandbox-exec o sin %s" % PY_JAULA)
    def test_el_hijo_con_clave_con_salto_se_niega_sin_citarla(self):
        """(c) en el hijo, por si la clave le llegara igual: se niega (rc 3) antes de armar la
        petición, y su salida no lleva ningún trozo de la clave."""
        cod = "import sys; sys.path.insert(0, %r); import vision_n1; print(vision_n1.HIJO_HTTP)" % TOOLS
        hijo = subprocess.run([PY, "-c", cod], env=self.env, capture_output=True, text=True).stdout
        pet = {"metodo": "GET", "url": "https://ejemplo.invalid/x", "cabeceras": {}, "timeout": 5,
               "hosts": ["ejemplo.invalid"], "sufijos": [], "cabecera_clave": "x-goog-api-key",
               "con_cuerpo": False, "bytes": 0}
        r = subprocess.run([PY_JAULA, "-I", "-S", "-c", hijo], input=json.dumps(pet).encode() + b"\n",
                           env={"PATH": "/usr/bin:/bin", "BTP_CLAVE_HIJO": FALSA_NL}, capture_output=True,
                           timeout=60)
        todo = (r.stdout + r.stderr).decode("utf-8", "replace")
        self.assertEqual(r.returncode, 3, todo)
        self.assertFalse([t for t in _trozos("FAKEKEYNL_") | _trozos("COLA_FALSA") if t in todo], todo)

    def test_guarda_de_s3_de_nube_normaliza_el_nombre(self):
        """(a) en la guarda PROPIA de nube_n1 (con http_en_jaula sustituido): ningún nombre que sea
        X-Auth-Token con otra forma llega al transporte hacia S3, y el error no lleva la clave."""
        env = dict(self.env)
        env.pop("BTP_TEST_BATTERY")
        cod = r"""
import vision_n1 as V, nube_n1 as N
llamadas = []
V.http_en_jaula = lambda *a, **k: llamadas.append(1) or (200, {}, b"")
out = {}
for nombre in ("X-Auth-Token", "x-auth-token", "X-Auth-Token ", " X-Auth-Token", "X-AUTH-TOKEN\t",
               "x-auth-token:"):
    del llamadas[:]
    try:
        N.transporte_https("GET", "https://%%s/b" %% N.S3_HOST, None, {nombre: %r})
        out[repr(nombre)] = "SALE"
    except V.PuertaCerrada as e:
        out[repr(nombre)] = "cerrada" + (" CON CLAVE" if %r in str(e) else "")
    out[repr(nombre)] += " llamó" if llamadas else ""
print(json.dumps(out))
""" % (FALSA, FALSA)
        v = self.py(cod, env=env)
        self.assertEqual(set(v.values()), {"cerrada"}, v)

    def test_error_de_nube_limpia_la_clave_antes_de_cortar(self):
        """(b) en `ScalewayBackend._err`: una clave a caballo del corte de 200 no sale a medias."""
        cod = r"""
import nube_n1 as N
b = N.ScalewayBackend({"access_key": "AK", "project_id": "p"}, {}, lambda: %r)
b.clave()
msg = "x" * 190 + %r + " cola"
e = b._err("GET", 403, json.dumps({"message": msg}).encode())
print(json.dumps(str(e)))
""" % (FALSA, FALSA)
        texto = self.py(cod)
        self.assertFalse([t for t in _trozos(FALSA) if t in texto], texto)


@unittest.skipUnless(HAY_JAULA, "sin sandbox-exec o sin %s" % PY_JAULA)
class JaulaReal(unittest.TestCase):
    """Perfil con el HOME y la casa base reales: solo se comprueba que FALLA (sin imprimir nada)."""

    def test_zona_clinica_sesion_y_secretos_reales_fallan(self):
        t = tempfile.mkdtemp(prefix="lam-n1-jaula-real-")
        try:
            env = dict(os.environ, BTP_TEST_BATTERY="1", BTP_STATE_DIR=os.path.join(t, "estado"))
            casa = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
            secretos = [os.path.join(b, "tools", n) for b in sorted({casa, RAIZ})
                        for n in (os.listdir(os.path.join(b, "tools")) if os.path.isdir(os.path.join(b, "tools")) else ())
                        if n.startswith(".") and "secrets" in n]
            cod = ("import sys; sys.path.insert(0, %r); import laminillas_jaulas as J; import json\n"
                   "print(json.dumps([J.HOME + '/Clinico-PRIVADO', J.SESION, J.ORIGEN]))" % TOOLS)
            r = subprocess.run([PY, "-c", cod], env=env, capture_output=True, text=True)
            clinico, sesion, origen = _ultima_json(r)
            listados = {"clinico": clinico, "sesion": sesion, "origen": origen}
            lectura = {"secreto%d" % i: s for i, s in enumerate(secretos)}
            a = {"dir": os.path.join(t, "jaulas"), "codigo": SONDA, "clave": CLAVE,
                 "args": [json.dumps(lectura), json.dumps({}), json.dumps(listados)]}
            r = subprocess.run([PY, "-c", EN_JAULA, json.dumps(a)], env=env, capture_output=True,
                               text=True, timeout=180)
            v = _ultima_json(r)
            self.assertEqual(v["rc"], 0, "rc %s, stderr de %s bytes" % (v["rc"], v["err_bytes"]))
            res = json.loads(v["out"])
            for k in listados:
                self.assertNotEqual(res["L:" + k], "lista", "la jaula real lista %s" % k)
            for k in lectura:
                self.assertNotEqual(res[k], "lee", "la jaula real lee un tools/.*secrets*")
            self.assertNotEqual(res["llavero"], 0)
            self.assertTrue(v["igual_que_J"])
            self.assertEqual(os.listdir(os.path.join(t, "jaulas")), [])
        finally:
            shutil.rmtree(t, ignore_errors=True)


class ConfigSellado(_Arbol):

    def cfg(self):
        with open(os.path.join(TOOLS, "nube_n1_cloudinit", "config.json"), encoding="utf-8") as fh:
            return json.load(fh)["scaleway"]

    def test_exigir_sellada_pasa_con_el_config_real(self):
        cod = ("import nube_n1 as N\nc = N.carga_config()\nprint(json.dumps(N.exigir_sellada(c)))")
        self.assertEqual(self.py(cod), ["nucls_main", "nucls_super", "panoptils"])

    def test_cada_valor_con_origen_y_fecha(self):
        s = self.cfg()
        self.assertTrue(s["imagen_gpu"]["origen"].startswith("https://api.scaleway.com/marketplace/v2/"))
        self.assertEqual(s["imagen_gpu"]["zona"], "fr-par-2")
        self.assertEqual(s["imagen_gpu"]["tipo"], "instance_sbs")
        self.assertIn("L40S-1-48G", s["imagen_gpu"]["compatible"])
        self.assertTrue(s["docker"]["origen"].startswith("https://registry-1.docker.io/v2/pytorch/pytorch/"))
        self.assertEqual(s["docker"]["plataforma"], "linux/amd64")
        for ck in s["checkpoints"]:
            self.assertTrue(ck["url"].startswith("https://zenodo.org/records/15094831/files/"))
            self.assertRegex(ck["md5_zenodo"], r"^[0-9a-f]{32}$")
            self.assertRegex(ck["sellado"], r"^2026-10-02")
        self.assertRegex(s["imagen_gpu"]["sellada"], r"^2026-10-02")
        for v in (s["docker"], s["mapa_origen"]):
            self.assertRegex(v["sellado"], r"^2026-10-02")

    def test_mapa_cubre_exactamente_las_clases_del_checkpoint(self):
        """Las claves del mapa son EXACTAMENTE los nombres de `type_map` (la config de cada
        checkpoint); un nombre de más o de menos haría fallar el enganche en la máquina."""
        s = self.cfg()
        comun = ("neoplastic", "inflammatory", "connective", "dead", "epithelial", "other")
        for c in s["clasificadores"]:
            origen = s["mapa_origen"]["clases"][c]
            self.assertEqual(sorted(s["mapa_clases"][c]), sorted(origen.values()), c)
            self.assertTrue(all(v in comun for v in s["mapa_clases"][c].values()), c)

    @unittest.skipUnless(os.path.isdir(CACHE_CVPP), "sin la caché propia de CellViT++")
    def test_sha256_casa_con_la_cache(self):
        for ck in self.cfg()["checkpoints"]:
            ruta = os.path.join(CACHE_CVPP, ck["nombre"])
            if not os.path.exists(ruta):
                self.skipTest("falta %s en la caché" % ck["nombre"])
            h = hashlib.sha256()
            with open(ruta, "rb") as fh:
                for b in iter(lambda: fh.read(1 << 22), b""):
                    h.update(b)
            self.assertEqual(h.hexdigest(), ck["sha256"], ck["nombre"])
            self.assertEqual(os.path.getsize(ruta), ck["bytes"], ck["nombre"])

    # ── La instalación de la máquina: todo fijado y coherente con lo que piden cellvit y pathopatch ──
    # `requires_dist` de PyPI (https://pypi.org/pypi/<p>/<v>/json, leído el 2-oct-26), sin extras.
    REQUIERE = {
        "cellvit 1.0.9": ["colorama", "colour", "einops>=0.6.1", "geojson>=2.0.0", "natsort", "numba>=0.58.0",
                          "numpy<2.0.0", "opencv-python-headless==4.7.0.72", "opt-einsum>=3.3.0", "pandas",
                          "pathopatch>=1.0.9", "pydantic<2.0,>=1.10.16", "pydicom==2.4.4", "ray>=2.9.3",
                          "scikit-image<0.27,>=0.19.3", "scipy>=1.8.0", "shapely<=2.0.5,>=1.8.5.post1",
                          "ujson==5.8.0", "python-snappy", "tqdm", "psutil", "pyaml"],
        "pathopatch 1.0.10": ["pillow>=9.5.0", "pyyaml", "shapely<=2.0.5,>=1.8.5.post1", "colorama", "future",
                              "geojson>=3.0.0", "matplotlib", "natsort", "numpy<2.0.0", "opencv-python-headless",
                              "openslide-python", "pandas", "pydantic<2.0,>=1.10.16", "rasterio", "requests",
                              "scikit-image<0.27", "setuptools<=65.6.3", "tqdm", "torchvision", "torch", "wsidicom",
                              "wsidicomizer", "pydicom==2.4.4"],
    }

    def test_pip_todo_fijado_y_casa_con_cellvit_y_pathopatch(self):
        """Cada requisito de cellvit 1.0.9 y pathopatch 1.0.10 está fijado en `pip` (o lo trae la
        imagen) a una versión que lo cumple; ningún pin sin ==; el torch de la imagen, como guarda."""
        try:
            from pip._vendor.packaging.specifiers import SpecifierSet
        except ImportError:
            self.skipTest("sin packaging (el de pip) en este Python")
        s = self.cfg()
        canon = lambda n: re.sub(r"[-_.]+", "-", n).lower()  # noqa: E731
        pins = {}
        for p in s["pip"]:
            self.assertRegex(p, r"^[a-z0-9][a-z0-9-]*==[0-9][0-9a-z.]*$", p)
            n, v = p.split("==")
            self.assertNotIn(n, pins, "paquete repetido: %s" % n)
            pins[n] = v
        version = dict(s["pip_origen"]["en_imagen"], **pins)        # lo fijado manda sobre la imagen
        for quien, reqs in self.REQUIERE.items():
            for r in reqs:
                n, spec = re.match(r"^([A-Za-z0-9_.-]+)(.*)$", r).groups()
                self.assertIn(canon(n), version, "%s pide %s y nadie lo fija" % (quien, n))
                self.assertTrue(SpecifierSet(spec).contains(version[canon(n)], prereleases=True),
                                "%s pide %s y va %s" % (quien, r, version[canon(n)]))
        for guarda in ("cellvit==1.0.9", "pathopatch==1.0.10", "torch==2.2.2", "torchvision==0.17.2",
                       "numpy==1.26.4", "setuptools==65.6.3", "openslide-bin==4.0.0.8"):
            self.assertIn(guarda, s["pip"])
        self.assertTrue(SpecifierSet(">=1.4").contains(pins["openslide-python"]))
        self.assertEqual(s["pip_solo_fuente"], ["asciitree", "pyturbojpeg"])
        self.assertTrue(set(s["pip_solo_fuente"]) <= set(pins))
        self.assertEqual(s["docker"]["python"], "3.10.14")
        self.assertTrue(set(s["pip_origen"]) >= {"metodo", "python", "decisiones", "solo_fuente", "openslide"})
        self.assertRegex(s["pip_origen"]["sellado"], r"^2026-10-02")

    def test_exigir_sellada_rechaza_pins_sueltos(self):
        cod = ("import nube_n1 as N\nc = N.carga_config()\nout = []\n"
               "for cambio in ({'pip': c['pip'] + ['numpy<2']}, {'pip': c['pip'] + ['ray==2.9.3']},\n"
               "               {'pip_solo_fuente': ['PyTurboJPEG']}, {'pip_solo_fuente': ['no-fijado']},\n"
               "               {'pip_solo_fuente': None}):\n"
               "    try:\n        N.exigir_sellada(dict(c, **cambio))\n        out.append('PASA')\n"
               "    except N.PuertaCerrada as e:\n        out.append(str(e))\n"
               "print(json.dumps(out))")
        out = self.py(cod)
        for motivo in out[:2]:
            self.assertIn("pip (cellvit", motivo)
        for motivo in out[2:]:
            self.assertIn("pip_solo_fuente", motivo)

    # ── El script de la máquina, corrido en bash con docker/curl/nvidia-smi falsos ───────────────
    STUBS = {
        "nvidia-smi": 'echo "GPU 0: NVIDIA L40S (UUID: GPU-falsa)"\n',
        "timeout": 'shift\nexec "$@"\n',
        "sha256sum": 'cat > /dev/null\n[ "$BTP_FALLA_EN" != sha ]\n',
        "curl": ('f=""; url=""; put=""\n'
                 'while [ $# -gt 0 ]; do\n'
                 '  case "$1" in\n'
                 '    --upload-file) f=$2; shift;;\n'
                 '    -X) put=$2; shift;;\n'
                 '    -o|--retry) shift;;\n'
                 '    https://*) url=$1;;\n'
                 '  esac\n'
                 '  shift\n'
                 'done\n'
                 'if [ "$put" = PUT ]; then\n'
                 '  o=${url%%\\?*}; o=${o##*/}\n'
                 '  if [ -f "$f" ]; then cp "$f" "$BTP_CAPTURA/$o"; else : > "$BTP_CAPTURA/$o"; fi\n'
                 'fi\n'),
        "docker": ('for a in "$@"; do printf "%s\\037" "$a"; done >> "$BTP_DOCKER_LOG"\n'
                   'printf "\\036" >> "$BTP_DOCKER_LOG"\n'
                   'comprueba=""; prev=""\n'
                   'for a in "$@"; do [ "$prev" = python ] && [ "$a" = -c ] && comprueba=1; prev=$a; done\n'
                   'if [ "$1 $2 $3" = "run --name btp-instala" ] && [ "$BTP_FALLA_EN" = instala ]; then\n'
                   '  echo "Collecting ray==9.9" ; echo "" >&2\n'
                   '  echo "ERROR: Could not find a version that satisfies the requirement ray==9.9 <script>" >&2\n'
                   '  exit 1\n'
                   'fi\n'
                   'if [ -n "$comprueba" ] && [ "$BTP_FALLA_EN" = comprueba ]; then\n'
                   '  echo "comprueba: pins sin instalar o con otra version: ray==2.48.0" >&2; exit 1\n'
                   'fi\n'),
    }

    def _script_relleno(self):
        """El _ejecuta.sh tal como viajaría (nube_n1.cloudinit con el config.json real)."""
        cod = ("import base64, re, nube_n1 as N\nc = N.carga_config()\n"
               "S = 'https://btp-n1-0123456789abcdef.s3.fr-par.scw.cloud/'\n"
               "u = lambda o: S + o + '?X-Amz-Expires=25200&X-Amz-Signature=' + 'f' * 64\n"
               "sal = N.salidas_esperadas(N.exigir_sellada(c))\n"
               "urls = {'entrada': u('P-HE.n1.tif'), 'DONE': u('DONE'), 'FAILED': u('FAILED'),"
               " 'salidas': {f: u(f) for f in sal}}\n"
               "ci = N.cloudinit(c, 'abcdef12', 'P-HE.n1.tif', 'e' * 64, 0.2506, urls)\n"
               "t = re.findall(r'content: (\\S+)', ci)\n"
               "print(json.dumps([base64.b64decode(t[0]).decode(), len(ci.encode()), N.MAX_CLOUDINIT, sal]))")
        return self.py(cod)

    def _maquina(self, falla_en=""):
        """Corre el script rellenado con bash, con `BTP_RAIZ` en un temporal y los binarios de la
        máquina sustituidos. Devuelve (rc, {objeto PUT: contenido}, [llamadas a docker], script)."""
        script, _n, _max, _sal = self._script_relleno()
        d = tempfile.mkdtemp(dir=self.t)
        raiz, binr, cap = (os.path.join(d, x) for x in ("raiz", "bin", "captura"))
        for x in (raiz, binr, cap):
            os.makedirs(x)
        _escribe(os.path.join(raiz, "a_enganche.py"), "")
        _escribe(os.path.join(raiz, "mapa.json"), "{}")
        for nombre, cuerpo in self.STUBS.items():
            _escribe(os.path.join(binr, nombre), "#!/bin/bash\n" + cuerpo)
            os.chmod(os.path.join(binr, nombre), 0o755)
        sh = os.path.join(raiz, "ejecuta.sh")
        _escribe(sh, script)
        dlog = os.path.join(d, "docker.log")
        env = {"PATH": binr + ":/usr/bin:/bin", "BTP_RAIZ": raiz, "BTP_CAPTURA": cap, "BTP_DOCKER_LOG": dlog,
               "BTP_FALLA_EN": falla_en}
        with open(os.path.join(raiz, "ejecuta.log"), "w") as log:      # como el runcmd del cloud-init
            rc = subprocess.run(["/bin/bash", sh], env=env, stdout=log, stderr=subprocess.STDOUT,
                                timeout=120).returncode
        objetos = {}
        for f in os.listdir(cap):
            with open(os.path.join(cap, f), encoding="utf-8", errors="replace") as fh:
                objetos[f] = fh.read()
        llamadas = []
        if os.path.exists(dlog):
            with open(dlog, encoding="utf-8") as fh:
                llamadas = [c.split("\x1f")[:-1] for c in fh.read().split("\x1e") if c]
        return rc, objetos, llamadas, script

    def test_script_sano_instala_con_la_lista_exacta_y_escribe_done(self):
        rc, objetos, llamadas, script = self._maquina()
        self.assertEqual(rc, 0, objetos)
        self.assertEqual(objetos.get("DONE"), "DONE trabajo=abcdef12\n")
        self.assertNotIn("FAILED", objetos)
        pins = self.cfg()["pip"]
        orden = [c[0] if c[0] != "run" else ("instala" if "btp-instala" in c else
                                              "comprueba" if "-c" in c else
                                              "cellvit" if "cellvit-inference" in c else "enganche")
                 for c in llamadas]
        self.assertEqual(orden, ["pull", "instala", "commit", "rm", "comprueba"] + ["cellvit", "enganche"] * 3)
        instala = llamadas[1]
        i = instala.index("install")
        self.assertEqual(instala[i + 1:i + 6], ["--no-cache-dir", "--disable-pip-version-check",
                                                "--only-binary=:all:", "--no-binary=asciitree,pyturbojpeg",
                                                "--no-build-isolation"])
        self.assertEqual(instala[i + 6:], pins)                       # la lista exacta, entera y en orden
        comprueba = llamadas[4]
        self.assertEqual(comprueba[:4], ["run", "--rm", "--network", "none"])
        self.assertEqual(comprueba[comprueba.index("-c") + 2:], pins)
        self.assertNotIn("api.scaleway.com", script)                  # la máquina no lleva clave ni API
        _s, n_ci, maximo, _sal = self._script_relleno()
        self.assertLess(n_ci, maximo)

    def test_falla_al_instalar_escribe_failed_claro_y_para(self):
        """pip falla dentro del contenedor: FAILED con paso=instala, el código y la última línea del
        log saneada; ni commit, ni comprueba, ni cellvit, ni DONE. (Al ver FAILED, `lanzar` borra la
        máquina: eso lo cubre test_laminillas_n1, Nube.test_lanzar_borra_la_maquina_con_failed….)"""
        rc, objetos, llamadas, _script = self._maquina("instala")
        self.assertEqual(rc, 1)
        self.assertEqual(sorted(objetos), ["FAILED"])
        f = objetos["FAILED"]
        self.assertRegex(f, r"^FAILED trabajo=abcdef12 paso=instala rc=1 ultimo=.*"
                            r"Could not find a version that satisfies the requirement ray==9\.9 script")
        self.assertNotRegex(f, r"[<>'\"/?&]")                         # saneado: ni HTML ni URL
        self.assertEqual([c[:3] for c in llamadas][-1], ["run", "--name", "btp-instala"])
        rc, objetos, _ll, _s = self._maquina("comprueba")
        self.assertEqual(rc, 1)
        self.assertRegex(objetos["FAILED"], r"^FAILED trabajo=abcdef12 paso=comprueba rc=1 ultimo=.*"
                                            r"pins sin instalar o con otra version: ray==2\.48\.0")
        rc, objetos, _ll, _s = self._maquina("sha")
        self.assertEqual(rc, 1)
        self.assertRegex(objetos["FAILED"], r"^FAILED trabajo=abcdef12 paso=entradas rc=1 ultimo=sha256 no casa: "
                                            r"P-HE\.n1\.tif")


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=1).result
    n = r.testsRun
    print("test_laminillas_n1_jaula: %d casos, %d fallos, %d errores, %d saltados"
          % (n, len(r.failures), len(r.errors), len(r.skipped)))
    sys.exit(0 if r.wasSuccessful() else 1)
