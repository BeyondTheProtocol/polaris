#!/usr/bin/env python3
"""tools/vision_n1.py — la boca por la que copias N1 de sus láminas llegan a un modelo de visión
externo (plan «laminillas DFCI», F1.4; decisión 3 de {{TITULAR}}: «debería procesarlas lo mejor, no solo
Anthropic»). Los modelos de visión REVISAN capas y figuras; no puntúan biomarcadores.

POR QUÉ NO VA POR `gemini.py`/`guard_cli`: `gemini.py` solo manda texto, y `guard_cli` marca «Ki67»
y «HER2» como clínicos (su `_RE_CLIN`), que es justo de lo que habla una revisión de capas. Aquí
el texto pasa por la Puerta de N1, que mira identidad (nombre, accesión, fecha, teléfono,
canarios) y deja pasar la biología.

GUARDAS, en este orden (todas en el proceso padre, ninguna con red):
  1. destino `vision-n1:<proveedor>` (nunca `gemini`/`chatgpt` a secas: `es_trusted` ignora el
     `para` con el que se confió, así que un destino propio es la única forma de acotarlo);
  2. Puerta fail-closed (`exigir_diccionario`);
  3. 1-20 ficheros, todos en `~/Laminillas-N1/` y con su sha256 en el manifiesto; imágenes PNG de
     lado largo ≤1568 px (la API de Claude reduce sin avisar por encima);
  4. el prompt, por la Puerta;
  5. sin HALT, `es_trusted(destino)`, cadena del borde íntegra y su último `trust_cloud` con
     `via: 'tty'` (lo teclea {{TITULAR}} en su terminal: paso 6). Se imprime fecha y `quien` de ese
     evento;
  6. el CONTENIDO de cada fichero, otra vez, justo antes de enviar (`exporta_n1.revalidar_n1`):
     el manifiesto es un JSON sin firma y cualquier proceso puede escribir en N1, así que no es
     prueba de procedencia. PNG: la forma exacta que escribe exporta_n1 (RGB de 8 bits, solo
     IHDR/IDAT/IEND, sin paleta ni alfa ni bytes tras el flujo zlib), ≤1024 px, OCR + Puerta +
     trazos; texto: la Puerta. Lo que se envía son esos bytes verificados, no una relectura del
     disco; de un PNG, su recodificación canónica (tercera pasada, 2-oct-26), y el sello del envío
     lleva también su sha256 (`sha256_enviado`).
  3-bis. el NOMBRE de cada fichero, por la lista blanca de exporta_n1 (`nombre_n1`) y la Puerta
     (tercera pasada): «24B0001043.png», colado en N1, ya no sale.
`enviar`, además, por este orden: proveedor con adaptador escrito y su MODELO fijado; las
CONDICIONES de su auditoría cumplidas (abajo); el cuerpo de la petición armado y medido; la CLAVE,
del Llavero, leída aquí y en ningún otro momento; el PRIMER envío a cada destino (desde su último
trust_cloud) avisa a {{TITULAR}} por `salida.py` (HALT y anti-spam de salida) y sin aviso aceptado no se
envía; el SELLO del envío en la cadena (proveedor, modelo, sha256, bytes) ANTES de la llamada, y si
no se puede sellar, no se envía; la llamada; el sello de la respuesta (sha256 y tokens, nunca el
texto ni la clave).

ADAPTADOR GEMINI (auditoría de `acceso-herramientas` y `legal-burocracia`, 2-oct-26, «apto con
condiciones»; nota archivada «laminillas-auditoría-de-la-api-de-gemini-para-el-panel-de-vi»):
  · modelo FIJADO `gemini-3.1-pro-preview` (otro id, PuertaCerrada);
  · `POST …/v1beta/models/gemini-3.1-pro-preview:generateContent` con las imágenes en `inlineData`
    `image/png` y el prompt en una parte de texto; la petición entera ≤20 MB. Nada de File API,
    Interactions API, grounding (tools), caché de contexto ni instrucciones de sistema: el cuerpo
    sale de una lista blanca de claves (`contents`, `generationConfig`) y la URL, de una constante;
  · `generationConfig`: `mediaResolution` MEDIA_RESOLUTION_HIGH (1120 tokens por imagen, lo que la
    auditoría recomienda para análisis de imagen) y `responseMimeType` application/json; ni
    temperatura ni `thinkingConfig`: los de por defecto (temperatura 1.0, la que recomienda Google
    para Gemini 3). El esquema JSON de la respuesta NO se manda como `responseJsonSchema`: no he
    abierto en esta sesión la página que diga que ese campo vale para este modelo; el cierre lo
    hace `calibra.interpreta` (claves exactas, vocabulario cerrado, inválida = fallo) [inferencia];
  · la clave `btp-gemini-api` por `_secrets.get` (solo Llavero, sin fichero de respaldo) en el
    momento del envío, en la cabecera `x-goog-api-key`; nunca en la URL, en un sello ni en un error;
  · CONDICIÓN de la auditoría: no envía si `tools/panel_vision/auditorias.json` DE CASA BASE no
    tiene, para `gemini`, veredicto «apto»/«apto con condiciones», el modelo fijado y
    `condiciones_cumplidas: true` (que se escribe cuando {{TITULAR}} confirma nivel de pago y logging
    apagado en AI Studio; nunca por inferencia). Aunque haya `trust-cloud`. Se lee de casa base,
    no del árbol del fichero: un worktree no se habilita a sí mismo.
  · transporte HTTPS sin proxies del entorno, solo a `generativelanguage.googleapis.com`, hecho
    por un hijo dentro de la jaula `vision.sb` (abajo); en la batería de tests
    (`BTP_TEST_BATTERY=1`) el transporte real y la clave real se niegan: los tests inyectan
    transporte y clave falsos.

JAULA `vision.sb` (plan, «F1. Jaulas»; 2-oct-26). La llamada HTTP de vision_n1 Y de nube_n1 la hace
un HIJO: `sandbox-exec -f vision.sb` + Python de /opt/homebrew en modo aislado (`-I -S`) con su
código en `-c` (no lee nada del repo). La jaula: red sí; lee solo `~/Laminillas-N1/` y el
intérprete; sin zona clínica, sin SESION, sin `tools/.x_secrets.json`, sin Llavero (mach-lookup y
exec de /usr/bin/security denegados); no escribe nada. El perfil se genera en cada llamada con las
raíces clínicas del momento (`laminillas_jaulas.perfil("vision")`) en un fichero propio 0600 en
JAULAS, y se borra al terminar. La petición entra por stdin (una línea JSON y el cuerpo en bruto,
leído a trozos) y la respuesta sale por stdout igual. La CLAVE nunca va en argv ni en stdin: sale de
las cabeceras en el padre y viaja SOLO por el entorno del hijo (`BTP_CLAVE_HIJO`; el entorno es una
lista blanca de dos variables), que la vuelve a poner en su cabecera. El hijo no sigue
redirecciones y vuelve a comprobar el host. El sello, la confianza, la Puerta, la clave del
Llavero y la escritura de lo bajado se quedan en el padre.

CABECERAS Y ERRORES (verificador independiente, 2-oct-26, con clave FALSA): antes de nada, cada
nombre de cabecera tiene que ser un token de RFC 9110 y se compara normalizado (`nombre_cabecera`:
«X-Auth-Token » pasaba la guarda «nada de X-Auth-Token hacia S3», que miraba el nombre exacto);
una cabecera de clave solo viaja como LA cabecera de clave de esa llamada; los valores, ASCII
visible; y la clave, ASCII visible sin espacios ni saltos (`clave_valida`: con un salto de línea,
http.client lanzaba un ValueError con su repr y la clave entera llegaba al error del padre). Todo
texto de error se LIMPIA antes de cortarlo (`_limpia_clave`: la clave, sus formas escapadas y
cualquier trozo suyo de ≥ MIN_TROZO_CLAVE caracteres); antes se cortaba a 300 y una clave a caballo
del corte salía a medias. El hijo hace lo mismo con lo suyo.

LÍMITES DECLARADOS:
  · Los nombres de campo de la API (`inlineData`, `mediaResolution`, `responseMimeType`) son los
    del REST v1beta según la auditoría y mi conocimiento; no he hecho ninguna llamada real (no hay
    trust-cloud ni condiciones). La primera llamada real los confirma o los tumba con un 400.
  · Claude y MedGemma no van por aquí: Claude por API no tiene adaptador; MedGemma corre en LOCAL
    (`tools/panel_vision/medgemma_local.py`), sin salir del Mac.

Uso:
  python3 tools/vision_n1.py comprobar --destino vision-n1:gemini --prompt "…" <fichero N1>…
  python3 tools/vision_n1.py estado    --destino vision-n1:gemini
  python3 tools/vision_n1.py enviar    --destino vision-n1:gemini --prompt "…" <fichero N1>…
"""
import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import puerta_n1 as puerta  # noqa: E402 — fija el estado de casa base antes de importar borde
from puerta_n1 import PuertaCerrada  # noqa: E402
import exporta_n1  # noqa: E402 — la re-validación del contenido (PNG, texto) justo antes de enviar
import _casa  # noqa: E402
import _secrets  # noqa: E402

PREFIJO = "vision-n1:"
LADO_MAX = 1568
MAX_IMAGENES = 20
TIMEOUT_S = 600

GEMINI_MODELO = "gemini-3.1-pro-preview"
GEMINI_HOST = "generativelanguage.googleapis.com"
GEMINI_URL = "https://%s/v1beta/models/%%s:generateContent" % GEMINI_HOST
GEMINI_SERVICIO_CLAVE = "btp-gemini-api"
GEMINI_MAX_PETICION = 20 * 1000 * 1000          # inline: toda la petición ≤20 MB (auditoría)
GEMINI_MEDIA_RESOLUTION = "MEDIA_RESOLUTION_HIGH"
AUDITORIAS_REL = ("tools", "panel_vision", "auditorias.json")
VEREDICTOS_APTOS = ("apto", "apto con condiciones")


class ErrorProveedor(RuntimeError):
    """El proveedor respondió con error, bloqueo o sin texto. No es una guarda: se envió (y se
    selló) y la respuesta no sirve. Nunca lleva la clave."""


# ── Guardas de ficheros, prompt y confianza ────────────────────────────────────────────────
def _destino_valido(destino):
    if not isinstance(destino, str) or not destino.startswith(PREFIJO) or len(destino) <= len(PREFIJO):
        raise PuertaCerrada("destino '%s' no vale: tiene que ser %s<proveedor>" % (destino, PREFIJO))
    return destino[len(PREFIJO):]


def preparar(destino, prompt, rutas):
    """Todas las guardas. Devuelve {"destino", "evento", "ficheros": [...], "bytes"}, con los
    bytes verificados de cada fichero en `ficheros[i]["datos"]` (lo que se envía); lanza
    PuertaCerrada al primer fallo."""
    _destino_valido(destino)
    puerta.exigir_diccionario()
    if not rutas:
        raise PuertaCerrada("ningún fichero")
    if len(rutas) > MAX_IMAGENES:
        raise PuertaCerrada("%d ficheros: el máximo por petición es %d" % (len(rutas), MAX_IMAGENES))
    man = puerta.cargar_manifiesto()
    candidatos = []
    for r in rutas:
        nombre, ent = puerta.fichero_n1_verificado(r, man)
        real = os.path.realpath(r)
        ext = os.path.splitext(nombre)[1].lower()
        if ext == ".png":
            with open(real, "rb") as fh:
                w, h = exporta_n1.dimensiones_png(fh.read())
            if max(w, h) > LADO_MAX:
                raise PuertaCerrada("%s mide %dx%d: pasa de %d px" % (nombre, w, h, LADO_MAX))
        elif ext in puerta.EXT_TEXTO:
            w = h = None
        else:
            raise PuertaCerrada("%s: a un modelo de visión solo van PNG o texto N1" % nombre)
        exporta_n1.nombre_n1(nombre)          # lista blanca + Puerta (3.ª pasada): el nombre también sale
        candidatos.append((nombre, ent, real, w, h))
    puerta.revisar_texto(prompt or "", "prompt")
    evento = puerta.exigir_confianza(destino, PREFIJO)
    # El contenido, otra vez y al final (lo caro): nada del manifiesto se da por bueno.
    verificados, ficheros, total = {}, [], 0
    for nombre, ent, real, w, h in candidatos:
        clave = (real, ent["sha256"])
        if clave not in verificados:
            verificados[clave] = exporta_n1.revalidar_n1(real, ent["sha256"])
        datos = verificados[clave]
        total += len(datos)
        ficheros.append({"nombre": nombre, "sha256": ent["sha256"], "ancho": w, "alto": h,
                         "bytes": len(datos), "datos": datos,
                         "sha256_enviado": hashlib.sha256(datos).hexdigest()})
    return {"destino": destino, "evento": evento, "ficheros": ficheros, "bytes": total}


# ── Condiciones de la auditoría (casa base) ────────────────────────────────────────────────
def auditorias_path():
    return os.path.join(_casa.casa_base(), *AUDITORIAS_REL)


def exigir_condiciones(proveedor, modelo):
    """La entrada de `proveedor` en `auditorias.json` de casa base, si deja enviar: veredicto apto
    (o apto con condiciones), el MISMO modelo y `condiciones_cumplidas` exactamente `true`. Si no,
    PuertaCerrada (fail-closed: sin fichero, ilegible o sin la entrada, tampoco)."""
    ruta = auditorias_path()
    try:
        with open(ruta, encoding="utf-8") as fh:
            aud = json.load(fh)
    except (OSError, ValueError):
        raise PuertaCerrada("auditoría de '%s' ilegible o ausente en casa base (%s): no se envía"
                            % (proveedor, os.path.join(*AUDITORIAS_REL)))
    ent = aud.get(proveedor) if isinstance(aud, dict) else None
    if not isinstance(ent, dict):
        raise PuertaCerrada("'%s' sin auditoría en %s: no se envía" % (proveedor, os.path.join(*AUDITORIAS_REL)))
    veredicto = str(ent.get("veredicto", "")).strip().lower()
    if veredicto not in VEREDICTOS_APTOS:
        raise PuertaCerrada("auditoría de '%s': veredicto %r, no apto: no se envía" % (proveedor, veredicto))
    if ent.get("modelo") != modelo:
        raise PuertaCerrada("auditoría de '%s' hecha para %r, no para %r: no se envía"
                            % (proveedor, ent.get("modelo"), modelo))
    if ent.get("condiciones_cumplidas") is not True:
        raise PuertaCerrada("auditoría de '%s': condiciones_cumplidas no es true (pago y logging de AI "
                            "Studio sin confirmar por {{TITULAR}}): no se envía, aunque haya trust-cloud"
                            % proveedor)
    return ent


# ── La clave, solo en el momento del envío ─────────────────────────────────────────────────
def _servicio_clave(proveedor):
    servicio = os.environ.get("BTP_VISION_SERVICIO_CLAVE") or ADAPTADORES[proveedor]["servicio"]
    if os.environ.get("BTP_TEST_BATTERY") == "1" and servicio == ADAPTADORES[proveedor]["servicio"]:
        raise PuertaCerrada("batería de tests: la clave real '%s' no se lee" % servicio)
    return servicio


def _clave(proveedor):
    """La clave de API, del Llavero (sin fichero de respaldo). Sin ella, PuertaCerrada."""
    servicio = _servicio_clave(proveedor)
    v = _secrets.get(servicio)
    if not v:
        raise PuertaCerrada("sin clave '%s' en el Llavero: no se envía" % servicio)
    return v


# ── Jaula vision.sb: la llamada HTTP, en un hijo enjaulado (vision_n1 y nube_n1) ───────────
SANDBOX = "/usr/bin/sandbox-exec"
PY_JAULA = ("/opt/homebrew/bin/python3.12", "/opt/homebrew/bin/python3")   # legibles en vision.sb
ENV_CLAVE_HIJO = "BTP_CLAVE_HIJO"
JAULAS_DIR = None                 # None → laminillas_jaulas.JAULAS (zona clínica, 0700)
MARGEN_HIJO_S = 120               # vigía: timeout + margen + 1 s por cada 128 KiB de cuerpo
BYTES_POR_S_MIN = 128 * 1024

# El código del hijo: solo stdlib, sin leer ficheros. Entrada: una línea JSON y, si `con_cuerpo`,
# exactamente `bytes` bytes en bruto. Salida: una línea JSON ({"status", "cabeceras"} o {"error"},
# con "n") y n bytes. rc 3 = se negó (destino, clave); rc 4 = sin respuesta (red, DNS, TLS, tiempo).
HIJO_HTTP = r'''
import json, os, sys, urllib.error, urllib.parse, urllib.request
ENT, SAL = sys.stdin.buffer, sys.stdout.buffer
CLAVE = os.environ.get("BTP_CLAVE_HIJO") or ""
MIN_TROZO = 6


def limpia(t):
    """Sin la clave, ni sus formas escapadas, ni ningún trozo suyo de >=MIN_TROZO caracteres; y
    DESPUES, cortado a 300 (al revés, una clave a caballo del corte salía a medias)."""
    t = str(t)[:65536]
    if CLAVE:
        formas = {CLAVE, repr(CLAVE)[1:-1], repr(CLAVE.encode("utf-8", "replace"))[2:-1],
                  json.dumps(CLAVE)[1:-1], urllib.parse.quote(CLAVE, safe="")}
        for v in sorted(formas, key=len, reverse=True):
            for n in range(len(v), MIN_TROZO - 1, -1):
                for i in range(len(v) - n + 1):
                    if v[i:i + n] in t:
                        t = t.replace(v[i:i + n], "<clave>")
    return t[:300]


def sal(cab, cuerpo=b"", rc=0):
    cab["n"] = len(cuerpo)
    SAL.write(json.dumps(cab).encode("ascii") + b"\n")
    SAL.write(cuerpo)
    SAL.flush()
    sys.exit(rc)


class Trozos:
    def __init__(self, n):
        self.falta = n

    def read(self, k=65536):
        if self.falta <= 0:
            return b""
        b = ENT.read(min(k if k and k > 0 else 65536, self.falta))
        if not b:
            raise EOFError("cuerpo truncado")
        self.falta -= len(b)
        return b


class SinRedirecciones(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


try:
    p = json.loads(ENT.readline().decode("utf-8"))
    u = urllib.parse.urlsplit(p["url"])
    h = (u.hostname or "").lower()
    if u.scheme != "https" or not (h in p["hosts"] or any(h.endswith("." + s) for s in p["sufijos"])):
        sal({"error": "destino no permitido: %s" % h[:80]}, rc=3)
    cab = dict(p["cabeceras"])
    if p.get("cabecera_clave"):
        if not CLAVE:
            sal({"error": "sin clave en el entorno"}, rc=3)
        if not all(33 <= ord(c) <= 126 for c in CLAVE):
            sal({"error": "clave del entorno con caracteres que no van en una cabecera"}, rc=3)
        cab[p["cabecera_clave"]] = CLAVE
    datos = None
    if p.get("con_cuerpo"):
        datos = Trozos(int(p["bytes"]))
        cab["Content-Length"] = str(int(p["bytes"]))
    req = urllib.request.Request(p["url"], data=datos, headers=cab, method=p["metodo"])
    abre = urllib.request.build_opener(urllib.request.ProxyHandler({}), SinRedirecciones()).open
    try:
        with abre(req, timeout=float(p["timeout"])) as r:
            st, hs, cuerpo = r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        st, hs, cuerpo = e.code, e.headers, e.read()
    sal({"status": st, "cabeceras": {k.lower(): v for k, v in (hs or {}).items()}}, cuerpo)
except SystemExit:
    raise
except BaseException as e:
    sal({"error": limpia("%s: %s" % (type(e).__name__, e))}, rc=4)
'''


class ErrorRed(OSError):
    """El hijo enjaulado no obtuvo respuesta (red, DNS, TLS, tiempo) o la jaula no devolvió nada
    legible. Nunca lleva la clave ni el stderr de sandbox-exec (puede citar rutas del perfil)."""


MIN_TROZO_CLAVE = 6               # trozo de la clave que ya no se deja en un texto de error
MAX_TEXTO_LIMPIO = 65536          # lo que se limpia de un texto de error (luego se corta más)


def _formas_clave(clave):
    """La clave y las formas en que un error puede citarla: escapada por repr (str y bytes), por
    JSON y en una URL."""
    return {clave, repr(clave)[1:-1], repr(clave.encode("utf-8", "replace"))[2:-1],
            json.dumps(clave)[1:-1], urllib.parse.quote(clave, safe="")}


def _limpia_clave(texto, clave, maximo=None):
    """`texto` sin la clave, ni sus formas (`_formas_clave`), ni ningún trozo suyo de
    ≥MIN_TROZO_CLAVE caracteres, y DESPUÉS cortado a `maximo` (2-oct-26: antes se cortaba primero
    y una clave a caballo del corte salía a medias)."""
    texto = str(texto)[:MAX_TEXTO_LIMPIO]
    if clave:
        for v in sorted(_formas_clave(str(clave)), key=len, reverse=True):
            for n in range(len(v), MIN_TROZO_CLAVE - 1, -1):
                for i in range(len(v) - n + 1):
                    if v[i:i + n] in texto:
                        texto = texto.replace(v[i:i + n], "«clave»")
    return texto if maximo is None else texto[:maximo]


# Nombres de cabecera: token de RFC 9110 §5.6.2 (tchar). Valores: ASCII visible, con espacios o
# tabuladores solo por dentro. Clave: ASCII visible, sin espacios ni saltos.
RE_TOKEN_CABECERA = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
RE_VALOR_CABECERA = re.compile(r"[\x21-\x7e](?:[\x20-\x7e\t]*[\x21-\x7e])?")
RE_CLAVE = re.compile(r"[\x21-\x7e]+")
CABECERAS_DE_CLAVE = frozenset(("x-auth-token", "x-goog-api-key"))


def nombre_cabecera(k):
    """El nombre de cabecera `k` normalizado (strip + casefold) para compararlo, si es un token de
    RFC 9110; si no (espacios, «:», saltos, no ASCII), PuertaCerrada. El mensaje no lo repite."""
    if not isinstance(k, str) or not RE_TOKEN_CABECERA.fullmatch(k):
        raise PuertaCerrada("cabecera cuyo nombre no es un token HTTP (RFC 9110): no se llama")
    return k.strip().casefold()


def clave_valida(clave):
    """`clave` si es ASCII visible sin espacios ni saltos; si no, PuertaCerrada (sin la clave)."""
    if not isinstance(clave, str) or not RE_CLAVE.fullmatch(clave):
        raise PuertaCerrada("clave con caracteres que no van en una cabecera (solo ASCII visible, sin "
                            "espacios ni saltos): no se llama")
    return clave


def _cabeceras_de_la_llamada(cabeceras, cabecera_clave):
    """(cabeceras sin la clave, clave o None). Antes de nada: nombres por `nombre_cabecera`, sin
    repetir; la cabecera de clave de ESTA llamada sale aparte (`clave_valida`); cualquier otra
    cabecera de clave conocida (X-Auth-Token, x-goog-api-key) cierra; los valores, ASCII visible."""
    clave_n = nombre_cabecera(cabecera_clave) if cabecera_clave else None
    cab, clave, vistos = {}, None, set()
    for k, v in dict(cabeceras).items():
        n = nombre_cabecera(k)
        if n in vistos:
            raise PuertaCerrada("cabecera %s repetida: no se llama" % n)
        vistos.add(n)
        if n == clave_n:
            clave = clave_valida(v)
        elif n in CABECERAS_DE_CLAVE:
            raise PuertaCerrada("cabecera de clave %s hacia un destino que no la lleva: no se llama" % n)
        elif not isinstance(v, str) or not RE_VALOR_CABECERA.fullmatch(v):
            raise PuertaCerrada("cabecera %s con un valor que no va en HTTP (solo ASCII visible): no se "
                                "llama" % n)
        else:
            cab[k] = v
    return cab, clave


def interprete_jaula():
    """El Python del hijo: de /opt/homebrew (lo deja leer la base de vision.sb; el de Xcode, no)."""
    for py in PY_JAULA:
        if os.path.exists(py) and os.path.realpath(py).startswith("/opt/homebrew/"):
            return py
    raise PuertaCerrada("sin Python de /opt/homebrew para la jaula vision.sb: no se llama")


def perfil_jaula(destino_dir=None):
    """Genera vision.sb con las raíces clínicas del momento en un fichero PROPIO de esta llamada
    (0600, O_EXCL) en JAULAS (o `destino_dir`) y devuelve su ruta; quien llama lo borra. Si no se
    puede generar, PuertaCerrada: sin jaula no hay llamada."""
    try:
        import laminillas_jaulas as J
        texto = J.perfil("vision")
        d = destino_dir or JAULAS_DIR or J.JAULAS
        os.makedirs(d, mode=0o700, exist_ok=True)
        ruta = os.path.join(d, "vision-%d-%s.sb" % (os.getpid(), os.urandom(6).hex()))
        fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(texto)
    except Exception as e:                          # noqa: BLE001 — fail-closed
        raise PuertaCerrada("no pude generar la jaula vision.sb (%s): no se llama" % type(e).__name__)
    return ruta


def comando_jaula(perfil, codigo=None):
    """argv del hijo. Sin la clave (va por entorno) y sin ningún fichero del repo."""
    return [SANDBOX, "-f", perfil, interprete_jaula(), "-I", "-S", "-c", codigo or HIJO_HTTP]


def entorno_jaula(clave=None):
    """Lista blanca: PATH y, si hace falta, la clave. Nada de os.environ."""
    env = {"PATH": "/usr/bin:/bin"}
    if clave:
        env[ENV_CLAVE_HIJO] = clave
    return env


def _respuesta_hijo(salida, rc, clave):
    linea, _sep, resto = salida.partition(b"\n")
    try:
        cab = json.loads(linea.decode("utf-8"))
        n = int(cab["n"])
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise ErrorRed("la jaula vision.sb no devolvió respuesta legible (rc %s)" % rc)
    if len(resto) != n:
        raise ErrorRed("respuesta de la jaula truncada (%d de %d bytes)" % (len(resto), n))
    if "error" in cab:
        texto = "jaula vision.sb: %s" % _limpia_clave(cab["error"], clave, 300)
        if rc == 3:
            raise PuertaCerrada(texto)
        raise ErrorRed(texto)
    st = cab.get("status")
    if isinstance(st, bool) or not isinstance(st, int):
        raise ErrorRed("la jaula vision.sb devolvió un status ilegible")
    return st, {str(k): str(v) for k, v in (cab.get("cabeceras") or {}).items()}, resto


def http_en_jaula(metodo, url, cuerpo, cabeceras, timeout, hosts, sufijos=(), cabecera_clave=None,
                  jaulas_dir=None):
    """UNA petición HTTPS hecha por el hijo dentro de vision.sb → (status, cabeceras en minúscula,
    bytes). `hosts`/`sufijos`: a dónde puede ir (lo comprueban el padre y el hijo).
    `cabecera_clave`: la cabecera que lleva la clave; su valor SALE de `cabeceras` y viaja solo por
    el entorno del hijo. PuertaCerrada si la jaula no se puede montar, el destino no está
    permitido o falta la clave; ErrorRed si no hubo respuesta."""
    cab, clave = _cabeceras_de_la_llamada(cabeceras, cabecera_clave)       # antes de nada
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    if p.scheme != "https" or not (host in hosts or any(host.endswith("." + s) for s in sufijos)):
        raise PuertaCerrada("destino HTTP no permitido: %s" % host)
    if not os.path.exists(SANDBOX):
        raise PuertaCerrada("sin sandbox-exec: sin jaula no se llama")
    if cabecera_clave and not clave:
        raise PuertaCerrada("petición sin su clave (%s): no se llama" % cabecera_clave)
    n = len(cuerpo) if cuerpo is not None else 0
    linea = json.dumps({"metodo": metodo, "url": url, "cabeceras": cab, "timeout": timeout,
                        "hosts": list(hosts), "sufijos": list(sufijos),
                        "cabecera_clave": cabecera_clave if clave else None,
                        "con_cuerpo": cuerpo is not None, "bytes": n}).encode("utf-8") + b"\n"
    interprete_jaula()                               # sin intérprete, ni se escribe el perfil
    perfil = perfil_jaula(jaulas_dir)
    try:
        proc = subprocess.Popen(comando_jaula(perfil), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=entorno_jaula(clave), cwd="/")

        def escribe():
            try:
                proc.stdin.write(linea)
                if n:
                    proc.stdin.write(cuerpo)
            except OSError:
                pass
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

        hilo = threading.Thread(target=escribe, daemon=True)
        hilo.start()
        vigia = threading.Timer(float(timeout) + MARGEN_HIJO_S + n / BYTES_POR_S_MIN, proc.kill)
        vigia.start()
        try:
            salida = proc.stdout.read()
            rc = proc.wait()
        finally:
            vigia.cancel()
            hilo.join(5)
    finally:
        try:
            os.unlink(perfil)
        except OSError:
            pass
    return _respuesta_hijo(salida, rc, clave)


# ── Transporte ─────────────────────────────────────────────────────────────────────────────
def transporte_https(url, datos, cabeceras, timeout=TIMEOUT_S):
    """POST por HTTPS, sin proxies del entorno, solo a los hosts de los adaptadores, hecho por el
    hijo dentro de vision.sb (`http_en_jaula`); la clave del adaptador, solo por su entorno.
    Devuelve (status, bytes). En la batería de tests, se niega (los tests inyectan uno falso)."""
    if os.environ.get("BTP_TEST_BATTERY") == "1":
        raise PuertaCerrada("batería de tests: el transporte real está prohibido")
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or p.hostname not in HOSTS:
        raise PuertaCerrada("destino HTTP no permitido: %s" % p.hostname)
    a = next(a for a in ADAPTADORES.values() if a["host"] == p.hostname)
    try:
        st, _cab, crudo = http_en_jaula("POST", url, datos, cabeceras, timeout, hosts=(a["host"],),
                                        cabecera_clave=a["cabecera_clave"])
    except ErrorRed as e:
        raise ErrorProveedor("sin respuesta de %s (%s)" % (p.hostname, e))
    return st, crudo


# ── Gemini ─────────────────────────────────────────────────────────────────────────────────
def gemini_cuerpo(modelo, prompt, ficheros):
    """(url, bytes del cuerpo JSON). Solo PNG (auditoría: inline_data image/png)."""
    partes = []
    for f in ficheros:
        if not f["nombre"].lower().endswith(".png"):
            raise PuertaCerrada("%s: a Gemini solo van PNG (inline image/png)" % f["nombre"])
        partes.append({"inlineData": {"mimeType": "image/png",
                                      "data": base64.b64encode(f["datos"]).decode("ascii")}})
    partes.append({"text": prompt})
    cuerpo = {"contents": [{"role": "user", "parts": partes}],
              "generationConfig": {"mediaResolution": GEMINI_MEDIA_RESOLUTION,
                                   "responseMimeType": "application/json"}}
    return GEMINI_URL % modelo, json.dumps(cuerpo, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def gemini_cabeceras(clave):
    return {"Content-Type": "application/json", "x-goog-api-key": clave}


def _limpia(texto, clave, maximo=None):
    return _limpia_clave(texto, clave, maximo)


def gemini_respuesta(status, crudo, clave=None):
    """(texto, uso) de una respuesta de generateContent; ErrorProveedor si no hay texto útil."""
    try:
        obj = json.loads(crudo.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, AttributeError):
        obj = None
    if status != 200:
        msg = ((obj or {}).get("error") or {}).get("message") if isinstance(obj, dict) else None
        raise ErrorProveedor("Gemini HTTP %s: %s" % (status, _limpia(msg or "", clave, 300)))
    if not isinstance(obj, dict):
        raise ErrorProveedor("Gemini: respuesta que no es JSON")
    bloqueo = (obj.get("promptFeedback") or {}).get("blockReason")
    if bloqueo:
        raise ErrorProveedor("Gemini bloqueó la petición (%s)" % str(bloqueo)[:60])
    cands = obj.get("candidates") or []
    if not cands or not isinstance(cands[0], dict):
        raise ErrorProveedor("Gemini: sin candidatos")
    partes = (cands[0].get("content") or {}).get("parts") or []
    texto = "".join(p.get("text", "") for p in partes
                    if isinstance(p, dict) and not p.get("thought") and isinstance(p.get("text"), str))
    if not texto.strip():
        raise ErrorProveedor("Gemini: sin texto (finishReason=%s)" % str(cands[0].get("finishReason"))[:40])
    u = obj.get("usageMetadata") or {}
    uso = {k: u.get(k) for k in ("promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount",
                                 "totalTokenCount") if isinstance(u.get(k), int)}
    return texto, uso


ADAPTADORES = {
    "gemini": {"modelo": GEMINI_MODELO, "servicio": GEMINI_SERVICIO_CLAVE, "host": GEMINI_HOST,
               "cabecera_clave": "x-goog-api-key",
               "max_peticion": GEMINI_MAX_PETICION, "cuerpo": gemini_cuerpo,
               "cabeceras": gemini_cabeceras, "respuesta": gemini_respuesta,
               "config": {"endpoint": "generateContent", "media_resolution": GEMINI_MEDIA_RESOLUTION,
                          "response_mime_type": "application/json", "temperatura": "defecto",
                          "thinking": "defecto"}},
}
HOSTS = frozenset(a["host"] for a in ADAPTADORES.values())


def _adaptador(proveedor):
    a = ADAPTADORES.get(proveedor)
    if a is None:
        raise PuertaCerrada("adaptador de '%s' sin escribir (solo %s): no se envía nada"
                            % (proveedor, ", ".join(sorted(ADAPTADORES))))
    return a


def exigir_modelo(proveedor, modelo):
    """El modelo fijado del proveedor; vacío = ese. Otro id, PuertaCerrada."""
    fijo = _adaptador(proveedor)["modelo"]
    if modelo in (None, "", fijo):
        return fijo
    raise PuertaCerrada("modelo %r: el de '%s' está fijado en %s (auditoría)" % (modelo, proveedor, fijo))


def listo(destino, modelo=""):
    """¿Se podría enviar a `destino`? Todas las guardas que no dependen de los ficheros ni de la
    clave: adaptador, modelo fijado, Puerta con diccionario, condiciones de la auditoría y
    confianza tecleada. Devuelve {"destino", "proveedor", "modelo", "evento", "config"}."""
    proveedor = _destino_valido(destino)
    a = _adaptador(proveedor)
    modelo = exigir_modelo(proveedor, modelo)
    exigir_condiciones(proveedor, modelo)
    puerta.exigir_diccionario()
    ev = puerta.exigir_confianza(destino, PREFIJO)
    return {"destino": destino, "proveedor": proveedor, "modelo": modelo, "evento": ev,
            "config": dict(a["config"], modelo=modelo)}


def _sella_o_cierra(evento):
    if not puerta.borde._sellar(evento):
        raise PuertaCerrada("no pude sellar '%s' en la cadena: no se envía" % evento.get("evento"))


def enviar(destino, modelo, prompt, rutas, avisar=False, esquema=None, transporte=None, verboso=True):
    """Envía `rutas` (N1) y `prompt` al proveedor de `destino` y devuelve el texto de la respuesta.

    `avisar` (OPT-IN, lo pone el CLI): el primer envío a `destino` avisa a {{TITULAR}} por salida.py;
    con avisar=False y el aviso pendiente, PuertaCerrada (nunca un primer envío callado).
    `esquema` (el JSON Schema cerrado de calibra) no se manda: queda en el sello como huella.
    `transporte(url, datos, cabeceras, timeout) -> (status, bytes)`: por defecto HTTPS real."""
    proveedor = _destino_valido(destino)
    a = _adaptador(proveedor)                        # sin adaptador: nada, ni aviso ni sello
    modelo = exigir_modelo(proveedor, modelo)
    exigir_condiciones(proveedor, modelo)            # barato: antes del OCR
    plan = preparar(destino, prompt, rutas)
    url, cuerpo = a["cuerpo"](modelo, prompt, plan["ficheros"])
    if urllib.parse.urlsplit(url).hostname != a["host"]:
        raise PuertaCerrada("URL del adaptador fuera de su host: no se envía")
    if len(cuerpo) > a["max_peticion"]:
        raise PuertaCerrada("petición de %d bytes: pasa de %d (inline)" % (len(cuerpo), a["max_peticion"]))
    clave = _clave(proveedor)                        # del Llavero, ahora y solo ahora
    ev = plan["evento"]
    if verboso:
        print("confianza de %s: %s por %s (via %s)" % (destino, ev.get("ts"), ev.get("quien"), ev.get("via")))
        for f in plan["ficheros"]:
            print("  · %s %s %s bytes" % (f["nombre"], "%sx%s" % (f["ancho"], f["alto"]) if f["ancho"] else "texto",
                                         f["bytes"]))
    puerta.avisar_primer_envio(destino, ev, len(plan["ficheros"]), plan["bytes"], avisar=avisar)
    sha_cuerpo = hashlib.sha256(cuerpo).hexdigest()
    _sella_o_cierra({"evento": "vision_n1_envio", "destino": destino, "proveedor": proveedor,
                     "modelo": str(modelo), "sha256": [f["sha256"] for f in plan["ficheros"]],
                     "sha256_enviado": [f["sha256_enviado"] for f in plan["ficheros"]],
                     "bytes": plan["bytes"], "bytes_peticion": len(cuerpo), "sha256_peticion": sha_cuerpo,
                     "endpoint": a["config"]["endpoint"],
                     "esquema": hashlib.sha256(json.dumps(esquema, sort_keys=True).encode()).hexdigest()[:16]
                     if esquema is not None else None, "nivel": "info"})
    status, crudo = (transporte or transporte_https)(url, cuerpo, a["cabeceras"](clave), TIMEOUT_S)
    try:
        texto, uso = a["respuesta"](status, crudo, clave)
    except ErrorProveedor as e:
        puerta.borde._sellar({"evento": "vision_n1_respuesta", "destino": destino, "status": status,
                              "sha256_peticion": sha_cuerpo, "error": _limpia(e, clave, 200), "nivel": "aviso"})
        raise
    puerta.borde._sellar({"evento": "vision_n1_respuesta", "destino": destino, "status": status,
                          "sha256_peticion": sha_cuerpo, "sha256_respuesta": hashlib.sha256(texto.encode("utf-8")).hexdigest(),
                          "uso": uso, "nivel": "info"})
    return texto


def main(argv):
    ap = argparse.ArgumentParser(prog="vision_n1.py")
    ap.add_argument("accion", choices=("comprobar", "estado", "enviar"))
    ap.add_argument("--destino", required=True)
    ap.add_argument("--modelo", default="")
    ap.add_argument("--prompt", default="")
    ap.add_argument("ficheros", nargs="*")
    a = ap.parse_intermixed_args(argv)
    try:
        if a.accion == "comprobar":
            plan = preparar(a.destino, a.prompt, a.ficheros)
            print("✅ pasa las guardas: %d fichero(s), %d bytes, confiado %s por %s" % (
                len(plan["ficheros"]), plan["bytes"], plan["evento"].get("ts"), plan["evento"].get("quien")))
            return 0
        if a.accion == "estado":
            r = listo(a.destino, a.modelo)
            print("✅ %s listo: %s, confiado %s por %s (via %s)" % (
                a.destino, r["modelo"], r["evento"].get("ts"), r["evento"].get("quien"), r["evento"].get("via")))
            return 0
        print(enviar(a.destino, a.modelo, a.prompt, a.ficheros, avisar=True))
        return 0
    except PuertaCerrada as e:
        print("🛑 %s" % e)
        return 3
    except ErrorProveedor as e:
        print("⚠️ %s" % e)
        return 4


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
