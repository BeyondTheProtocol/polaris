#!/usr/bin/env python3
"""tests/test_laminillas_jaulas_secretos.py — en ninguna jaula SBPL de las laminillas un allow
puede reabrir un secreto ni la zona clínica (hallazgo del 2-oct-26).

El fallo: `laminillas_jaulas` emitía el deny de `tools/.x_secrets.json` ANTES de los allow, y la
ventanilla lanzaba red-sin-zona (CON RED) con `extra_lectura=[tools]`: en SBPL gana la última regla
que casa, así que la jaula leía el señuelo, y con él los tokens de `tools/state/`, los overlays de
PII, el estado vivo y cualquier `_PRIVADO_*` que colgara de `tools/`. En las jaulas
`(allow default)` además se leían `~/.ssh`, `~/.config/gh`, el Llavero en fichero, etc.

Para CADA jaula y cada combinación de extras que usan de verdad sus llamadores (ventanilla,
laminillas_stack, vision_n1/nube_n1) y, por clase, también con un extra `tools/` entero o de solo
código en todas:
  (a) por texto: ningún allow detrás del primer deny de secretos; `secretos_al_final` y
      `orden_correcto` dan ok;
  (b) de verdad, con `sandbox-exec`: una sonda intenta abrir señuelos sintéticos (secretos del
      repo, credenciales en HOME, zona clínica bajo el extra y, en las jaulas con red, lo privado
      del repo) y TIENE que fallar; los controles positivos (lo que la jaula sí lee) tienen que
      leerse, para que un perfil que lo niega todo no pase de rebote.
  Y sobre el HOME y la casa base REALES: las credenciales que existan no se abren en ninguna jaula.

Segunda verificación adversarial (2-oct-26), aquí también:
  · `extra_codigo` solo abre los `*.py` VERSIONADOS (`git ls-files`): un `.py` sin versionar y
    `__pycache__/` no se leen (el árbol sintético es un repo git de verdad);
  · iCloud y CloudStorage, en el bloque final: ni el señuelo sintético se lee ni las carpetas
    reales se listan (sonda booleana: nunca nombres);
  · Llavero: ni una COPIA de `security` fuera de /usr/bin ni Python por ctypes (llavero en fichero
    y de protección de datos) llegan a buscar un servicio SINTÉTICO; con el deny de mach-lookup
    quitado (mutante), ctypes sí llega;
  · LÍMITE DECLARADO `test_limite_declarado_enlace_duro_plantado`: desde dentro no se crea un enlace
    duro ni un clon a un secreto; plantado desde fuera en lo que la jaula lee, se lee.

SIN DATOS: la sonda solo devuelve si `open()` tuvo éxito (nunca contenido). Los señuelos son
sintéticos y viven en un árbol temporal que hace de HOME y de repo. El Llavero solo se consulta por
un servicio sintético inexistente y sin pedir la contraseña.
"""
import json
import os
import pwd
import re
import secrets as _azar
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(RAIZ, "tools")
SANDBOX = "/usr/bin/sandbox-exec"
PY_JAULA = "/opt/homebrew/bin/python3.12"      # legible en las jaulas deny-default (/opt/homebrew)
HAY_JAULA = os.path.exists(SANDBOX) and os.path.exists(PY_JAULA)
CON_RED = ("red-sin-zona", "vision")
COMPILADOR = [p for p in ("/Applications/Xcode.app", "/Library/Developer/CommandLineTools")
              if os.path.isdir(p)]

# Solo booleanos: ¿se abrió y se leyó 1 byte? Nunca imprime contenido.
SONDA = r"""
import json, sys
out = {}
for k, r in json.loads(sys.argv[1]).items():
    try:
        with open(r, "rb") as f:
            f.read(1)
        out[k] = "lee"
    except Exception as e:
        out[k] = type(e).__name__
print(json.dumps(out))
"""

# Genera varios perfiles en UN proceso con el HOME/BTP_REPO que se le den (las constantes del
# generador cuelgan de HOME al importarse). Imprime {id: ruta}.
GENERA = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import laminillas_jaulas as J
out = {}
for c in json.loads(sys.argv[2]):
    kw = dict(c["kw"])
    if kw.pop("_tmp_red", False):
        kw["tmpdir"] = J.TMP_RED
    out[c["id"]] = J.escribe(c["perfil"], c["dir"], **kw)
print(json.dumps({"rutas": out, "venvs": J.VENVS, "cache": J.CACHE, "home": J.HOME}))
"""


# Solo booleanos: ¿se puede LISTAR la carpeta? Nunca imprime nombres.
SONDA_LISTA = r"""
import json, os, sys
out = {}
for k, r in json.loads(sys.argv[1]).items():
    try:
        os.listdir(r)
        out[k] = "lista"
    except Exception as e:
        out[k] = type(e).__name__
print(json.dumps(out))
"""

# Enlace duro y clon (clonefile) creados DESDE la jaula hacia un secreto. Solo el resultado.
SONDA_ENLACE = r"""
import ctypes, json, os, sys
libc = ctypes.CDLL(None, use_errno=True)
out = {}
for k, (a, b) in json.loads(sys.argv[1]).items():
    try:
        if k.startswith("link"):
            os.link(a, b)
        else:
            if libc.clonefile(a.encode(), b.encode(), 0) != 0:
                raise OSError(ctypes.get_errno(), "clonefile")
        out[k] = "creado"
    except OSError as e:
        out[k] = "errno %d" % e.errno
print(json.dumps(out))
"""

# Llavero por ctypes, con un servicio SINTÉTICO inexistente y sin pedir la contraseña (punteros
# NULL / sin kSecReturnData): solo el OSStatus. -25300 (errSecItemNotFound) = llegó y buscó.
# «fichero»: SecKeychainFindGenericPassword (SecurityServer, login.keychain-db); «datos»:
# SecItemCopyMatching con kSecUseDataProtectionKeychain (secd).
SONDA_LLAVERO = r"""
import ctypes, json, sys
vp = ctypes.c_void_p
cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
sec = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
s = sys.argv[1].encode()
f = sec.SecKeychainFindGenericPassword
f.restype = ctypes.c_int32
fichero = f(None, len(s), s, 0, None, None, None, None)
cf.CFStringCreateWithCString.restype = vp
cf.CFStringCreateWithCString.argtypes = [vp, ctypes.c_char_p, ctypes.c_uint32]
cf.CFDictionaryCreateMutable.restype = vp
cf.CFDictionaryCreateMutable.argtypes = [vp, ctypes.c_long, vp, vp]
cf.CFDictionarySetValue.argtypes = [vp, vp, vp]
sec.SecItemCopyMatching.restype = ctypes.c_int32
sec.SecItemCopyMatching.argtypes = [vp, ctypes.POINTER(vp)]
k = lambda n, lib=sec: vp.in_dll(lib, n).value
d = cf.CFDictionaryCreateMutable(None, 0, ctypes.addressof(ctypes.c_char.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")),
                                 ctypes.addressof(ctypes.c_char.in_dll(cf, "kCFTypeDictionaryValueCallBacks")))
cf.CFDictionarySetValue(d, k("kSecClass"), k("kSecClassGenericPassword"))
cf.CFDictionarySetValue(d, k("kSecAttrService"), cf.CFStringCreateWithCString(None, s, 0x08000100))
cf.CFDictionarySetValue(d, k("kSecUseDataProtectionKeychain"), k("kCFBooleanTrue", cf))
datos = sec.SecItemCopyMatching(d, ctypes.byref(vp()))
print(json.dumps({"fichero": fichero, "datos": datos}))
"""
NO_ENCONTRADO = -25300          # errSecItemNotFound: la búsqueda llegó al Llavero


def _servicio_sintetico():
    return "btp-sintetico-inexistente-" + _azar.token_hex(8)


def _git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    r = subprocess.run(["git"] + list(args), cwd=repo, env=env, capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr[-300:]


def _escribe(ruta, texto="senuelo-sintetico"):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w") as f:
        f.write(texto)


def _sonda(perfil_ruta, objetivos, home):
    r = subprocess.run([SANDBOX, "-f", perfil_ruta, PY_JAULA, "-c", SONDA, json.dumps(objetivos)],
                       capture_output=True, text=True, cwd="/", timeout=120,
                       env={"PATH": "/usr/bin:/bin", "HOME": home})
    if r.returncode != 0:
        raise AssertionError("la sonda no corrió en %s (rc %d)" % (os.path.basename(perfil_ruta),
                                                                   r.returncode))
    return json.loads(r.stdout)


def allow_tras_secretos(texto):
    """Líneas `(allow …)` detrás del PRIMER deny de secretos. Independiente de la implementación
    (no usa MARCA_FINAL): con el código de antes también tiene que señalar el fallo."""
    lineas = texto.splitlines()
    idx = next((i for i, ln in enumerate(lineas) if ln.startswith("(deny") and "secrets" in ln),
               None)
    if idx is None:
        return ["(no hay deny de secretos)"]
    return [ln[:70] for ln in lineas[idx + 1:] if ln.startswith("(allow")]


@unittest.skipUnless(HAY_JAULA, "sin sandbox-exec o sin %s" % PY_JAULA)
class SecretosAlFinal(unittest.TestCase):
    """Árbol sintético: HOME y BTP_REPO apuntan a un temporal."""

    @classmethod
    def setUpClass(cls):
        cls.t = t = os.path.realpath(tempfile.mkdtemp(prefix="lam-jaulas-secretos-"))
        cls.home = h = os.path.join(t, "home")
        cls.repo = os.path.join(t, "repo")
        tools = cls.tools = os.path.join(cls.repo, "tools")
        lam = os.path.join(h, "Clinico-PRIVADO", "laminillas-DFCI")
        cls.sesion, cls.origen = os.path.join(lam, "sesion"), os.path.join(lam, "origen")
        cls.n1 = os.path.join(h, "Laminillas-N1")
        venvs = os.path.join(h, ".polaris-venvs")
        nfd = unicodedata.normalize("NFD", "Historial clínico Señuelo")
        # Lo que NINGUNA jaula puede abrir.
        cls.secretos = {
            "tools/.x_secrets.json": os.path.join(tools, ".x_secrets.json"),
            "tools/.y_secrets.py": os.path.join(tools, ".y_secrets.py"),     # casa *.py del extra
            "worktree tools/.x_secrets.json": os.path.join(
                cls.repo, ".claude", "worktrees", "w", "tools", ".x_secrets.json"),
            "state/nube": os.path.join(tools, "state", "nube", "cuenta.json"),
            "state/scite/tokens.json": os.path.join(tools, "state", "scite", "tokens.json"),
            "state/consensus/client.json": os.path.join(tools, "state", "consensus", "client.json"),
            "state/borde_gateway/token": os.path.join(tools, "state", "borde_gateway", "token"),
            "state/x/api_secret.txt": os.path.join(tools, "state", "x", "api_secret.txt"),
            "~/.ssh": os.path.join(h, ".ssh", "id_senuelo"),
            "~/.config/gh": os.path.join(h, ".config", "gh", "hosts.yml"),
            "~/.config/gh-token.env": os.path.join(h, ".config", "gh-token.env"),
            "~/Library/Keychains": os.path.join(h, "Library", "Keychains", "s.keychain-db"),
            "~/.cache/huggingface/token": os.path.join(h, ".cache", "huggingface", "token"),
            "HF_HOME ventanilla/token": os.path.join(venvs, "cache", "hf", "token"),
            "HF_HOME ventanilla/stored_tokens": os.path.join(venvs, "cache", "hf", "stored_tokens"),
            "~/.claude/.credentials.json": os.path.join(h, ".claude", ".credentials.json"),
            "~/.claude.json": os.path.join(h, ".claude.json"),
            "~/.docker/config.json": os.path.join(h, ".docker", "config.json"),
            "~/.netrc": os.path.join(h, ".netrc"),
            "~/.aws": os.path.join(h, ".aws", "credentials"),
            # zona clínica colgando del extra: un extra no la reabre
            "tools/_PRIVADO_CLINICO": os.path.join(tools, "_PRIVADO_CLINICO", "c.txt"),
            "tools/Historial clínico (NFD)": os.path.join(tools, nfd, "c.txt"),
            # lo que se sincroniza con la nube (2-oct-26): bloque final de todas las jaulas
            "~/Library/Mobile Documents": os.path.join(h, "Library", "Mobile Documents",
                                                       "com~apple~CloudDocs", "c.txt"),
            "~/Library/CloudStorage": os.path.join(h, "Library", "CloudStorage",
                                                   "GoogleDrive-sintetico", "c.txt"),
        }
        # Lo que las jaulas CON RED no pueden abrir (las sin red sí: exporta usa overlays y estado).
        cls.privado_red = {
            "state/caso (estado vivo)": os.path.join(tools, "state", "caso", "c.json"),
            "tools/perfil.local.json": os.path.join(tools, "perfil.local.json"),
            "tools/launchd/logs": os.path.join(tools, "launchd", "logs", "x.out"),
        }
        cls.no_codigo = os.path.join(tools, "datos.json")      # ni secreto ni código
        _escribe(cls.no_codigo)
        # Código que NO está versionado (2-oct-26): un borrador suelto y el .pyc de un módulo.
        cls.sin_versionar = {"tools/borrador_sin_versionar.py": os.path.join(tools, "borrador_sin_versionar.py"),
                             "tools/__pycache__/ok.pyc": os.path.join(tools, "__pycache__",
                                                                      "ok.cpython-312.pyc")}
        for r in cls.sin_versionar.values():
            _escribe(r)
        # Controles positivos.
        cls.ctrl = {
            "tools/ok.py": os.path.join(tools, "ok.py"),
            "venvs": os.path.join(venvs, "control.txt"),
            "n1": os.path.join(cls.n1, "c.txt"),
            "sesion": os.path.join(cls.sesion, "c.txt"),
        }
        for r in list(cls.secretos.values()) + list(cls.privado_red.values()) + list(cls.ctrl.values()):
            _escribe(r)
        # El repo sintético es un checkout de git: `extra_codigo` abre solo lo versionado. El
        # `.y_secrets.py` se versiona a propósito: aun así lo cierra el bloque final.
        _git(cls.repo, "init", "-q")
        _git(cls.repo, "add", "tools/ok.py", "tools/.y_secrets.py")
        cls.venvs = venvs
        tmp_ses = os.path.join(cls.sesion, "tmp")
        os.makedirs(tmp_ses, exist_ok=True)
        os.makedirs(cls.origen, exist_ok=True)
        cls.tmp_ses = tmp_ses
        jd = os.path.join(t, "jaulas")
        datos = dict(sesion=cls.sesion, origen=cls.origen, n1=cls.n1)
        # (id, perfil, kwargs, quién lo usa así)
        combos = [
            ("ventanilla-red", "red-sin-zona", dict(_tmp_red=True, extra_codigo=[tools])),
            ("stack-valis", "red-sin-zona", dict(_tmp_red=True, extra_lectura=COMPILADOR)),
            ("stack", "red-sin-zona", dict(_tmp_red=True)),
            ("vision_n1", "vision", dict(n1=cls.n1)),
            ("visor-n1", "visor-n1", dict(n1=cls.n1)),
            ("analisis-metal", "analisis", dict(datos, tmpdir=tmp_ses,
                                                metal_cache=os.path.join(t, "metal"))),
        ]
        for p in ("analisis", "analisis-ingesta", "visor-clinico", "exporta"):
            combos.append(("ventanilla-" + p, p, dict(datos, tmpdir=tmp_ses)))
        # Por clase: cualquier jaula con un extra `tools/` (entero, como el de antes, o solo código).
        for p in ("red-sin-zona", "vision", "analisis", "analisis-ingesta", "visor-clinico",
                  "visor-n1", "exporta"):
            base = dict(datos, tmpdir=tmp_ses) if p not in CON_RED else (
                dict(_tmp_red=True) if p == "red-sin-zona" else dict(n1=cls.n1))
            combos.append(("clase-lectura-" + p, p, dict(base, extra_lectura=[tools])))
            combos.append(("clase-codigo-" + p, p, dict(base, extra_codigo=[tools])))
        specs = [{"id": i, "perfil": p, "kw": kw, "dir": os.path.join(jd, i)} for i, p, kw in combos]
        cls.combos = {i: p for i, p, _ in combos}
        cls.extras = {i: kw for i, _, kw in combos}
        env = dict(os.environ, HOME=h, BTP_REPO=cls.repo)
        r = subprocess.run([sys.executable, "-c", GENERA, TOOLS, json.dumps(specs)], env=env,
                           capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stderr[-800:]
        gen = json.loads(r.stdout.strip().splitlines()[-1])
        cls.rutas = gen["rutas"]
        assert gen["venvs"] == venvs, "el generador no tomó el HOME sintético"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.t, ignore_errors=True)

    def _texto(self, cid):
        with open(self.rutas[cid], encoding="utf-8") as f:
            return f.read()

    # ── (a) por texto ──
    def test_a_ningun_allow_tras_los_secretos(self):
        sys.path.insert(0, TOOLS)
        import laminillas_jaulas as J
        for cid in self.rutas:
            texto = self._texto(cid)
            self.assertEqual(allow_tras_secretos(texto), [], "%s: allow tras los secretos" % cid)
            ok, motivo = J.secretos_al_final(texto)
            self.assertTrue(ok, "%s: %s" % (cid, motivo))
            ok, motivo = J.orden_correcto(texto, [self.sesion, self.origen, self.n1])
            self.assertTrue(ok, "%s: %s" % (cid, motivo))

    def test_a_detector_caza_el_mutante(self):
        """El detector de texto no es vacuo: con el bloque final movido delante de los allow,
        tanto `allow_tras_secretos` como `secretos_al_final` lo señalan."""
        sys.path.insert(0, TOOLS)
        import laminillas_jaulas as J
        lineas = self._texto("ventanilla-red").splitlines()
        i = lineas.index(J.MARCA_FINAL)
        final, resto = lineas[i:], lineas[:i]
        j = next(k for k, ln in enumerate(resto) if ln.startswith(";; ── allows del plan"))
        mutante = "\n".join(resto[:j] + final + resto[j:]) + "\n"
        self.assertNotEqual(allow_tras_secretos(mutante), [])
        self.assertFalse(J.secretos_al_final(mutante)[0])

    # ── (b) de verdad, con sandbox-exec ──
    def test_b_ninguna_jaula_abre_secretos_ni_zona_clinica(self):
        objetivos = dict(self.secretos)
        objetivos.update(self.privado_red)
        objetivos.update({"CTRL " + k: v for k, v in self.ctrl.items()})
        for cid, perfil in self.combos.items():
            res = _sonda(self.rutas[cid], objetivos, self.home)
            for k in self.secretos:
                self.assertNotEqual(res[k], "lee", "%s (%s) abre %s" % (cid, perfil, k))
            if perfil in CON_RED:
                for k in self.privado_red:
                    self.assertNotEqual(res[k], "lee", "%s (%s, con red) abre %s" % (cid, perfil, k))
            # controles positivos: la jaula no lo niega todo
            kw = self.extras[cid]
            esperados = []
            if perfil == "red-sin-zona":
                esperados.append("venvs")
            if perfil in ("vision", "visor-n1", "exporta"):
                esperados.append("n1")
            if perfil in ("analisis", "analisis-ingesta", "visor-clinico", "exporta"):
                esperados += ["sesion", "tools/ok.py"]
            if kw.get("extra_lectura") == [self.tools] or kw.get("extra_codigo") == [self.tools]:
                esperados.append("tools/ok.py")
            for k in esperados:
                self.assertEqual(res["CTRL " + k], "lee", "%s (%s) no lee el control %s"
                                 % (cid, perfil, k))

    def test_b_extra_codigo_solo_lee_codigo(self):
        """`extra_codigo` (lo que pasa la ventanilla con red) no abre nada de `tools/` que no sea
        un `*.py` del primer nivel VERSIONADO en git (2-oct-26): ni datos, ni un `.py` sin
        versionar, ni `__pycache__/`. Mutante: con la regla de antes (regex `*.py` y
        `__pycache__/` entero) el borrador sin versionar y el .pyc se leen."""
        objetivos = dict({"datos": self.no_codigo, "ok": self.ctrl["tools/ok.py"]}, **self.sin_versionar)
        res = _sonda(self.rutas["ventanilla-red"], objetivos, self.home)
        self.assertEqual(res["ok"], "lee")
        self.assertNotEqual(res["datos"], "lee")
        for k in self.sin_versionar:
            self.assertNotEqual(res[k], "lee", "la jaula con red lee %s" % k)
        texto = self._texto("ventanilla-red")
        extras = [ln for ln in texto.splitlines() if ln.startswith("(allow file-read* (literal") and
                  self.tools in ln]
        self.assertEqual(len(extras), 1, extras)
        self.assertNotIn("regex", extras[0])
        self.assertNotIn("__pycache__", extras[0])
        viejo = '(allow file-read* (literal "%s") (regex #"^%s/[^/]+\\.py$") (subpath "%s/__pycache__"))' % (
            self.tools, re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", self.tools), self.tools)
        ruta = os.path.join(self.t, "mutante-codigo-viejo.sb")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(texto.replace(extras[0], viejo))
        mut = _sonda(ruta, objetivos, self.home)
        self.assertEqual([mut[k] for k in self.sin_versionar], ["lee"] * len(self.sin_versionar), mut)

    # Una combinación por perfil, la de su llamador de verdad.
    UNA = {"red-sin-zona": "ventanilla-red", "vision": "vision_n1", "visor-n1": "visor-n1",
           "analisis": "ventanilla-analisis", "analisis-ingesta": "ventanilla-analisis-ingesta",
           "visor-clinico": "ventanilla-visor-clinico", "exporta": "ventanilla-exporta"}

    def test_c_nube_en_el_bloque_final(self):
        """iCloud y CloudStorage (2-oct-26): en TODAS las jaulas, tras `MARCA_FINAL` (los señuelos
        sintéticos no se abren: `test_b_ninguna_jaula_abre_secretos_ni_zona_clinica`). Mutante: sin
        esas dos líneas, una jaula `(allow default)` los lee."""
        sys.path.insert(0, TOOLS)
        import laminillas_jaulas as J
        for cid in self.rutas:
            final = self._texto(cid).split(J.MARCA_FINAL, 1)[1]
            for rel in ("Library/Mobile Documents", "Library/CloudStorage"):
                self.assertIn('(deny file-read* file-write* (subpath "%s"))' % os.path.join(self.home, rel),
                              final, "%s sin %s en el bloque final" % (cid, rel))
        texto = self._texto("ventanilla-analisis")
        mut = "\n".join(ln for ln in texto.splitlines() if "Mobile Documents" not in ln and
                        "CloudStorage" not in ln) + "\n"
        ruta = os.path.join(self.t, "mutante-sin-nube.sb")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(mut)
        nube = {k: self.secretos[k] for k in ("~/Library/Mobile Documents", "~/Library/CloudStorage")}
        self.assertEqual(set(_sonda(ruta, nube, self.home).values()), {"lee"})
        self.assertNotIn("lee", _sonda(self.rutas["ventanilla-analisis"], nube, self.home).values())

    def _llavero(self, perfil_ruta, servicio, env):
        r = subprocess.run([SANDBOX, "-f", perfil_ruta, PY_JAULA, "-c", SONDA_LLAVERO, servicio],
                           capture_output=True, text=True, timeout=120, env=env, cwd="/")
        self.assertEqual(r.returncode, 0, "la sonda del Llavero no corrió (rc %d)" % r.returncode)
        return json.loads(r.stdout)

    def test_d_llavero_ni_por_copia_de_security_ni_por_ctypes(self):
        """El exec de /usr/bin/security se niega por RUTA literal. (1) Una COPIA del binario fuera
        de /usr/bin, dentro de cada jaula, buscando un servicio SINTÉTICO sin -w: no puede
        responder «could not be found» (eso sería que llegó al Llavero). Medido el 2-oct-26: macOS
        la mata antes («Launch Constraint Violation»), dentro y fuera de la jaula. (2) Lo que sí
        depende de la jaula: Python por ctypes, en el llavero en fichero (SecurityServer) y en el
        de protección de datos (secd), no llega a buscar (-25300). Hasta el 2-oct-26 secd se
        alcanzaba desde las 5 jaulas `(allow default)`. Mutante: sin el deny de mach-lookup, una
        jaula `(allow default)` llega a los dos."""
        servicio = _servicio_sintetico()
        env = {"PATH": "/usr/bin:/bin", "HOME": pwd.getpwuid(os.getuid()).pw_dir}
        control = subprocess.run([PY_JAULA, "-c", SONDA_LLAVERO, servicio], capture_output=True, text=True,
                                 timeout=120, env=env)
        ctl = json.loads(control.stdout) if control.returncode == 0 else {}
        if ctl.get("fichero") != NO_ENCONTRADO:
            self.skipTest("el Llavero no se ve desde esta sesión (control sin jaula: %r)" % ctl.get("fichero"))
        donde = {"red-sin-zona": os.path.join(self.venvs, "tmp"), "vision": self.n1, "visor-n1": self.n1}
        for perfil, cid in self.UNA.items():
            ruta = self.rutas[cid]
            res = self._llavero(ruta, servicio, env)
            for via in ("fichero", "datos"):
                self.assertNotEqual(res[via], NO_ENCONTRADO, "%s: ctypes llega al Llavero (%s)" % (perfil, via))
            d = donde.get(perfil, os.path.join(self.t, "copia-security"))
            os.makedirs(d, exist_ok=True)
            copia = os.path.join(d, "security-copia")
            if not os.path.exists(copia):
                shutil.copy("/usr/bin/security", copia)
            r = subprocess.run([SANDBOX, "-f", ruta, copia, "find-generic-password", "-s", servicio],
                               capture_output=True, text=True, timeout=120, env=env, cwd="/")
            self.assertNotIn("could not be found", r.stdout + r.stderr, perfil)
            self.assertNotIn(r.returncode, (0, 44), perfil)
        texto = self._texto("ventanilla-analisis")
        mut = re.sub(r"^\(deny mach-lookup .*$", "", texto, flags=re.M)
        self.assertNotEqual(mut, texto)
        ruta = os.path.join(self.t, "mutante-sin-mach.sb")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(mut)
        self.assertEqual(self._llavero(ruta, servicio, env), {"fichero": NO_ENCONTRADO, "datos": NO_ENCONTRADO})

    def test_limite_declarado_enlace_duro_plantado(self):
        """LÍMITE DECLARADO (laminillas_jaulas, cabecera): SBPL compara RUTAS. (1) Desde DENTRO, en
        lo que cada jaula escribe, un enlace duro o un clon (clonefile) de un secreto o de zona
        clínica no se crea (EPERM), y el de un fichero cualquiera de esa carpeta sí (control). (2)
        Plantado desde FUERA —un proceso con permisos del usuario, que ya podría leer el secreto
        directamente— en una carpeta que la jaula lee (VENVS, N1, SESION), se lee desde las 7
        jaulas: ese es el límite. Por su ruta, el secreto sigue cerrado."""
        objetivo = {k: self.secretos[k] for k in ("~/.ssh", "tools/.x_secrets.json", "tools/_PRIVADO_CLINICO")}
        escribe = {"red-sin-zona": os.path.join(self.venvs, "tmp"), "analisis": self.tmp_ses,
                   "analisis-ingesta": self.origen, "exporta": self.n1}
        for perfil, d in escribe.items():
            os.makedirs(d, exist_ok=True)
            propio = os.path.join(d, "propio-%s.txt" % perfil)
            _escribe(propio)
            ops = {"link control": [propio, os.path.join(d, "enlace-control-%s" % perfil)]}
            for i, (k, src) in enumerate(sorted(objetivo.items())):
                for tipo in ("link", "clone"):
                    ops["%s %s" % (tipo, k)] = [src, os.path.join(d, "%s-%s-%d" % (tipo, perfil, i))]
            r = subprocess.run([SANDBOX, "-f", self.rutas[self.UNA[perfil]], PY_JAULA, "-c", SONDA_ENLACE,
                                json.dumps(ops)], capture_output=True, text=True, timeout=120,
                               env={"PATH": "/usr/bin:/bin", "HOME": self.home}, cwd="/")
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            res = json.loads(r.stdout)
            self.assertEqual(res.pop("link control"), "creado", perfil)
            for k, v in res.items():
                self.assertEqual(v, "errno 1", "%s: %s desde dentro" % (perfil, k))
                self.assertFalse(os.path.exists(ops[k][1]), "%s: %s existe" % (perfil, k))
        lee = {"red-sin-zona": self.venvs, "vision": self.n1, "visor-n1": self.n1, "analisis": self.sesion,
               "analisis-ingesta": self.sesion, "visor-clinico": self.sesion, "exporta": self.sesion}
        for perfil, d in lee.items():
            plantados = {}
            for i, (k, src) in enumerate(sorted(objetivo.items())):
                dst = os.path.join(d, "plantado-%s-%d" % (perfil, i))
                os.link(src, dst)                                  # el proceso de FUERA
                plantados[k] = dst
            try:
                res = _sonda(self.rutas[self.UNA[perfil]],
                             dict(plantados, **{"ruta " + k: v for k, v in objetivo.items()}), self.home)
            finally:
                for dst in plantados.values():
                    os.unlink(dst)
            for k in objetivo:
                self.assertNotEqual(res["ruta " + k], "lee", "%s abre %s por su ruta" % (perfil, k))
                self.assertEqual(res[k], "lee", "%s: el límite cambió (%s plantado no se lee): "
                                 "actualiza la cabecera de laminillas_jaulas" % (perfil, k))


@unittest.skipUnless(HAY_JAULA, "sin sandbox-exec o sin %s" % PY_JAULA)
class SecretosReales(unittest.TestCase):
    """HOME y casa base REALES: las credenciales que existan no se abren en ninguna jaula. Solo
    booleanos; los mensajes nombran la etiqueta, nunca el contenido."""

    def test_credenciales_reales_no_se_abren(self):
        sys.path.insert(0, TOOLS)
        import laminillas_jaulas as J
        casa = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
        homes = sorted({J.HOME, pwd.getpwuid(os.getuid()).pw_dir})
        cand = {}
        for h in homes:
            for rel in (".ssh/id_ed25519", ".ssh/id_rsa", ".config/gh/hosts.yml",
                        ".config/gh-token.env", "Library/Keychains/login.keychain-db",
                        ".cache/huggingface/token", ".claude/.credentials.json", ".claude.json",
                        ".docker/config.json", ".polaris-venvs/cache/hf/token"):
                cand["%s:~/%s" % (h, rel)] = os.path.join(h, rel)
        privado = {}
        for b in sorted({casa, RAIZ}):
            st = os.path.join(b, "tools", "state")
            for rel in ("nube", "scite", "consensus", "borde_gateway"):
                d = os.path.join(st, rel)
                if os.path.isdir(d):
                    for n in sorted(os.listdir(d)):
                        # nube/ entera (datos de cuenta); en las demás, los ficheros de credencial
                        # (sus logs no lo son: en las jaulas sin red se leen, por diseño)
                        if rel == "nube" or n in ("token", "tokens.json", "client.json"):
                            cand["%s:state/%s/%s" % (b, rel, n)] = os.path.join(d, n)
            t = os.path.join(b, "tools")
            if os.path.isdir(t):
                for n in sorted(os.listdir(t)):
                    if n.startswith(".") and "secrets" in n:
                        cand["%s:tools/%s" % (b, n)] = os.path.join(t, n)
                    if n.endswith(".local.json"):
                        privado["%s:tools/%s" % (b, n)] = os.path.join(t, n)
            if os.path.isfile(os.path.join(st, "seguimiento.json")):
                privado["%s:state/seguimiento.json" % b] = os.path.join(st, "seguimiento.json")
        cand = {k: v for k, v in cand.items() if os.path.isfile(v)}
        privado = {k: v for k, v in privado.items() if os.path.isfile(v)}
        if not cand:
            self.skipTest("no hay credenciales reales en esta máquina")
        d = tempfile.mkdtemp(prefix="lam-jaulas-secretos-real-")
        try:
            falsa = os.path.join(d, "sesion-falsa")
            casa_tools = os.path.join(casa, "tools")
            combos = [("red-sin-zona", dict(tmpdir=J.TMP_RED, extra_codigo=[casa_tools])),
                      ("red-sin-zona", dict(tmpdir=J.TMP_RED, extra_lectura=[casa_tools])),
                      ("red-sin-zona", dict(tmpdir=J.TMP_RED, extra_lectura=COMPILADOR)),
                      ("vision", {})]
            combos += [(p, dict(sesion=falsa, tmpdir=d)) for p in
                       ("analisis", "analisis-ingesta", "visor-clinico", "visor-n1", "exporta")]
            for i, (p, kw) in enumerate(combos):
                ruta = J.escribe(p, os.path.join(d, str(i)), **kw)
                obj = dict(cand)
                if p in CON_RED:
                    obj.update(privado)
                res = _sonda(ruta, obj, J.HOME)
                for k in obj:
                    self.assertNotEqual(res[k], "lee", "%s #%d abre una credencial real (%s)"
                                        % (p, i, k.split(":", 1)[1]))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_nube_real_no_se_lista(self):
        """iCloud y CloudStorage del HOME real (2-oct-26: se listaban desde las 5 jaulas
        `(allow default)`): ninguna jaula los lista. Sonda booleana, nunca nombres; no se escribe
        nada en ellas (se sincronizan)."""
        sys.path.insert(0, TOOLS)
        import laminillas_jaulas as J
        homes = sorted({J.HOME, pwd.getpwuid(os.getuid()).pw_dir})
        obj = {"%s:%s" % (h, rel): os.path.join(h, rel) for h in homes
               for rel in ("Library/Mobile Documents", "Library/CloudStorage")}
        obj = {k: v for k, v in obj.items() if os.path.isdir(v)}
        if not obj:
            self.skipTest("ni iCloud ni CloudStorage en esta máquina")
        d = tempfile.mkdtemp(prefix="lam-jaulas-nube-real-")
        try:
            falsa = os.path.join(d, "sesion-falsa")
            for p in J.PERFILES:
                kw = dict(tmpdir=J.TMP_RED) if p == "red-sin-zona" else ({} if p == "vision" else
                                                                       dict(sesion=falsa, tmpdir=d))
                ruta = J.escribe(p, os.path.join(d, p), **kw)
                r = subprocess.run([SANDBOX, "-f", ruta, PY_JAULA, "-c", SONDA_LISTA, json.dumps(obj)],
                                   capture_output=True, text=True, timeout=120, cwd="/",
                                   env={"PATH": "/usr/bin:/bin", "HOME": J.HOME})
                self.assertEqual(r.returncode, 0, p)
                for k, v in json.loads(r.stdout).items():
                    self.assertNotEqual(v, "lista", "%s lista %s" % (p, k.split(":", 1)[1]))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_codigo_sin_versionar_de_la_casa_base_no_entra(self):
        """`extra_codigo` sobre `tools/` de la casa base: el perfil nombra uno a uno los `*.py`
        versionados y ningún `*.py` sin versionar (el verificador vio que la jaula con red leía
        uno). Solo se mira el TEXTO del perfil: el fichero sin versionar no se abre."""
        sys.path.insert(0, TOOLS)
        import laminillas_jaulas as J
        casa_tools = os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"), "tools")
        if not os.path.isdir(casa_tools):
            self.skipTest("sin casa base")
        versionados = set(J.py_versionados(casa_tools))
        if not versionados:
            self.skipTest("la casa base no es un checkout de git")
        sueltos = [n for n in os.listdir(casa_tools) if n.endswith(".py") and n not in versionados]
        d = tempfile.mkdtemp(prefix="lam-jaulas-codigo-real-")
        try:
            with open(J.escribe("red-sin-zona", d, tmpdir=J.TMP_RED, extra_codigo=[casa_tools]),
                      encoding="utf-8") as f:
                texto = f.read()
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertTrue(all(J._q(os.path.join(casa_tools, n)) in texto for n in versionados))
        dentro = [n for n in sueltos if J._q(os.path.join(casa_tools, n)) in texto]
        self.assertEqual(len(dentro), 0, "%d .py sin versionar abiertos a la jaula con red" % len(dentro))


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=1).result
    n = r.testsRun
    print("test_laminillas_jaulas_secretos: %d casos, %d fallos, %d errores, %d saltados"
          % (n, len(r.failures), len(r.errors), len(r.skipped)))
    sys.exit(0 if r.wasSuccessful() else 1)
