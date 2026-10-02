#!/usr/bin/env python3
"""tools/nube_n1.py — boca única hacia la GPU en la nube (plan «laminillas DFCI», «Potencia y
nube»): CellViT++ en P-HE, solo con copias N1. Proveedor: Scaleway, zona fr-par-2, L40S-1-48G, sin
HDS (decisión de {{TITULAR}} del 2-oct-26; nota archivada «laminillas-nube-gpu-para-cellvit-scaleway-l40s»).

VERBOS
  subir   — ficheros de `~/Laminillas-N1/` con sha256 en el manifiesto, a un bucket PRIVADO y
            CIFRADO (AES256 por defecto del bucket, comprobado antes de cada subida; sin versionado)
            que crea nube_n1 en `fr-par`. Si el cifrado no se puede poner o comprobar, no sube nada.
  lanzar  — instancia L40S-1-48G en fr-par-2 desde la imagen GPU del proveedor (id SELLADO en
            `nube_n1_cloudinit/config.json`) con el cloud-init versionado del mismo directorio:
            `docker pull pytorch/pytorch@<digest>`, baja las entradas con URLs PREFIRMADAS (sin
            clave en la máquina), instala `cellvit==1.0.9` con sus pins, comprueba el sha256 sellado
            de cada checkpoint, ejecuta sin red, convierte al enganche de laminillas_he, sube con
            URLs prefirmadas y escribe DONE o FAILED. Grupo de seguridad propio con la entrada
            cerrada. `lanzar` espera a DONE/FAILED y BORRA la máquina al terminar —también si falla,
            se agota la espera o se interrumpe—: una máquina apagada sigue facturando. Borrar es
            apagar, eliminar la instancia y después su disco de arranque, su IP y su grupo.
  estado  — instancias y objetos DONE/FAILED.
  bajar   — el par `P-HE.<clasificador>.{json,csv}` del bucket, SOLO a `SESION/nube/` (por defecto
            `SESION/nube/cellvitpp/`, donde lo lee laminillas_he), validando el esquema del
            enganche de CellViT++ de `laminillas_he.CELLVITPP` (commit dc16108) antes de escribir.
  listar  — instancias, volúmenes, snapshots, IPs, grupos de seguridad propios, objetos y buckets.
  borrar  — todo eso: instancias, volúmenes (también el de arranque), snapshots, IPs, grupos,
            todas las versiones de todos los objetos y el bucket. Después `listar` tiene que dar
            vacío; si algo queda (un bucket que no se vacía, por ejemplo), falla y lo dice. Deja
            pendiente la segunda comprobación a las 24 h (`comprobar-24h`).
  comprobar-24h — si pasaron 24 h desde el último `borrar`, vuelve a listar; vacío: lo sella y
            cierra; con restos: alarma (y con --avisar, aviso a {{TITULAR}} por salida.py). Idempotente:
            se puede programar cada hora (`plist-24h` imprime el LaunchAgent; no lo instala).
  cuenta  — actualiza la access key (SCW…) y/o el id de proyecto, que NO son secretos, en
            `tools/state/nube/scaleway.json` de CASA BASE (gitignorado; con `zona` y `llavero_secret`,
            que no pisa). Sin `project_id` (llega tras el alta) o sin el secreto en el Llavero, todo
            verbo que hable con el proveedor falla cerrado. La clave secreta no pasa nunca por aquí.

GUARDAS: `bajar` comprueba su destino ANTES que nada. Todos los verbos que hablan con el
proveedor exigen la Puerta fail-closed, sin HALT, `es_trusted('nube-n1:<proveedor>')`, cadena
íntegra y su `trust_cloud` con `via: 'tty'` (paso 7 de {{TITULAR}}). La clave `btp-scaleway-api`, la
ÚNICA, del Llavero por `_secrets` y solo cuando se hace la primera llamada (nunca al importar, ni
en `cuenta`, ni en un error, ni en un sello). `subir` vuelve a validar el CONTENIDO de cada fichero
justo antes (`exporta_n1.revalidar_n1`) y su NOMBRE (`nombre_n1`), sube los bytes verificados (de un
PNG, su recodificación canónica) y avisa a {{TITULAR}} del PRIMER envío por `salida.py` antes de sellar.
Cada subida, lanzamiento, borrado y bajada se sella en la cadena; la de `lanzar`, ANTES de crear
nada, y si no se puede sellar no se lanza. `scp`/`rsync`/`ssh` a mano los deniega `salida_guard`;
el host no se añade a `_destinos_salida.py`. En la batería de tests (`BTP_TEST_BATTERY=1`) el
transporte HTTP real y la clave real se niegan: los tests inyectan un Scaleway simulado.

LÍMITES DECLARADOS (2-oct-26):
  · La cuenta no secreta ya está en casa base (2-oct-26), pero NINGUNA ruta de la API de Scaleway
    (Instances, Block Storage, Object Storage S3 con firma SigV4) se ha probado contra el proveedor. Las rutas y campos son los de su
    documentación pública según la auditoría y mi conocimiento [inferencia]; la firma SigV4 sí se
    comprueba en los tests contra los vectores publicados por AWS.
  · `config.json` está SELLADO desde el 2-oct-26 (cada valor con su URL de origen y fecha, en el
    propio fichero): id de la imagen GPU (catálogo público de Scaleway), digest amd64 de la imagen
    Docker (registro de Docker Hub), sha256 de los checkpoints (bajados dentro de red-sin-zona.sb)
    y los nombres de clase de nucls_*/panoptils (los de la config de cada checkpoint, que es lo que
    CellViT++ 1.0.9 escribe en `type_map`). Su destino en la taxonomía común es INFERENCIA MÍA
    (como `laminillas_he.A_COMUN`), revisable. Con cualquiera a null, `lanzar` no lanza.
  · La instalación de la máquina (`pip` de config.json: TODO fijado con ==, `pip_solo_fuente` los dos
    sin rueda en PyPI) se resolvió EN SECO, sin Docker ni GPU, con el pip 23.3.1 de la imagen, los
    marcadores de su CPython 3.10.14 en Linux x86_64 y lo que la imagen ya trae como restricciones
    (detalle y fecha en `pip_origen`). No se ha instalado de verdad en la imagen: si algo falla,
    FAILED en «instala» o «comprueba» con la última línea del log, y `lanzar` borra la máquina.
  · Que la imagen GPU traiga Docker y el runtime de NVIDIA está documentado pero no probado: si no,
    el trabajo escribe FAILED en el paso «docker» y `lanzar` borra la máquina (se cobra la hora).
  · `lanzar` bloquea hasta DONE/FAILED (máx. `espera_max_s`). Si el Mac se duerme o el proceso
    muere sin pasar por su `finally`, la máquina sigue viva: `borrar` y la alerta de facturación
    de 20 € (paso de {{TITULAR}}) son la red.
  · La llamada HTTP la hace un hijo dentro de la jaula `vision.sb` (`vision_n1.http_en_jaula`:
    red sí, lee solo N1 y el intérprete, sin zona clínica, SESION, secretos en fichero ni Llavero;
    no escribe nada). El X-Auth-Token sale de las cabeceras aquí y viaja SOLO por el entorno del
    hijo; hacia S3 no viaja ninguna clave (la firma SigV4 y las URLs prefirmadas se hacen aquí).
    El sello, la confianza, la Puerta y la escritura de lo bajado se quedan en este proceso.

Uso:
  python3 tools/nube_n1.py cuenta [--access-key SCW…] [--proyecto <uuid>]
  python3 tools/nube_n1.py subir <fichero N1>…
  python3 tools/nube_n1.py lanzar [P-HE.n1.tif]
  python3 tools/nube_n1.py estado | listar | borrar
  python3 tools/nube_n1.py bajar <clasificador> [--destino-dir SESION/nube/cellvitpp]
  python3 tools/nube_n1.py comprobar-24h [--avisar]
  python3 tools/nube_n1.py plist-24h
"""
import argparse
import base64
import csv
import hashlib
import hmac
import io
import json
import math
import os
import re
import shlex
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import puerta_n1 as puerta  # noqa: E402 — fija el estado de casa base antes de importar borde
from puerta_n1 import PuertaCerrada  # noqa: E402
import exporta_n1  # noqa: E402 — la re-validación del contenido justo antes de subir
import vision_n1  # noqa: E402 — la jaula vision.sb y su hijo HTTP (una sola implementación)
import _casa  # noqa: E402
import _secrets  # noqa: E402

PREFIJO = "nube-n1:"
PROVEEDORES = ("scaleway",)
ZONA = "fr-par-2"                 # fijada en el código (decisión de {{TITULAR}} del 2-oct-26)
REGION_S3 = "fr-par"
TIPO = "L40S-1-48G"
REGIONES = {"scaleway": ZONA}
SERVICIO_CLAVE = {"scaleway": "btp-scaleway-api"}
API_HOST = "api.scaleway.com"
S3_HOST = "s3.%s.scw.cloud" % REGION_S3
ETIQUETA = "btp-n1"
PREFIJO_RECURSO = "btp-n1-"
MARCAS = ("DONE", "FAILED")
HOSTS_CHECKPOINT = ("zenodo.org",)
DIA_S = 86400
TIMEOUT_API_S, TIMEOUT_S3_S = 120, 1800
ESPERA_APAGADO_S, ESPERA_BORRADO_S = 900, 300

DIR_CI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nube_n1_cloudinit")
CONFIG = os.path.join(DIR_CI, "config.json")
PLANTILLA_CI = os.path.join(DIR_CI, "cellvit.cloud-init.yaml")
EJECUTA = os.path.join(DIR_CI, "_ejecuta.sh")
ENGANCHE = os.path.join(DIR_CI, "_a_enganche.py")
MAX_CLOUDINIT = 60 * 1024

RE_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
RE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
RE_SHA = re.compile(r"^[0-9a-f]{64}$")
RE_ACCESS = re.compile(r"^SCW[A-Z0-9]{17}$")
RE_PIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*==[0-9][0-9A-Za-z.]*$")     # versión EXACTA, siempre
RE_PAQUETE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
RE_IMAGEN_DOCKER = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*$")
RE_NOMBRE_FICHERO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
RE_TRABAJO = re.compile(r"^[0-9a-f]{8}$")
RE_CLAVE_MODELO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$")


class ErrorNube(RuntimeError):
    """El proveedor respondió con un error. Nunca lleva la clave."""


# ── Rutas de la sesión ─────────────────────────────────────────────────────────────────────
def sesion_dir():
    return os.path.realpath(os.environ.get("BTP_LAMINILLAS_SESION") or
                            os.path.expanduser("~/Clinico-PRIVADO/laminillas-DFCI/sesion"))


def nube_dir():
    return os.path.join(sesion_dir(), "nube")


def _he():
    """El formato del enganche vive en laminillas_he (una sola fuente de verdad)."""
    import laminillas_he
    return laminillas_he


def enganche_dir():
    return os.path.join(sesion_dir(), _he().CELLVITPP["salida"]["carpeta"])


def _dentro(ruta, base):
    r = os.path.realpath(ruta)
    return r == base or r.startswith(base + os.sep)


# ── Cuenta (no secreta) y configuración versionada ─────────────────────────────────────────
def _estado_nube():
    """`tools/state/nube/` de CASA BASE (gitignorado): la cuenta no secreta y las marcas de 24 h."""
    return os.path.join(_casa.state_dir(), "nube")


def cuenta_path(proveedor="scaleway"):
    return os.path.join(_estado_nube(), "%s.json" % proveedor)


def _lee_cuenta(proveedor):
    try:
        with open(cuenta_path(proveedor), encoding="utf-8") as fh:
            c = json.load(fh)
    except (OSError, ValueError):
        return None
    return c if isinstance(c, dict) else None


def guarda_cuenta(access_key="", proyecto="", proveedor="scaleway"):
    """Actualiza (sin pisar el resto: `zona`, `llavero_secret`, `nota`) la access key y/o el id de
    proyecto, que NO son secretos, en `tools/state/nube/<proveedor>.json` de casa base."""
    c = _lee_cuenta(proveedor) or {"zona": ZONA, "llavero_secret": SERVICIO_CLAVE.get(proveedor)}
    if access_key:
        if not RE_ACCESS.match(access_key):
            raise PuertaCerrada("access key con forma inesperada (SCW + 17 mayúsculas o cifras)")
        c["access_key"] = access_key
    if proyecto:
        if not RE_UUID.match(proyecto):
            raise PuertaCerrada("id de proyecto con forma inesperada (uuid en minúsculas)")
        c["project_id"] = proyecto
    if not access_key and not proyecto:
        raise PuertaCerrada("nada que guardar: --access-key y/o --proyecto")
    os.makedirs(_estado_nube(), mode=0o700, exist_ok=True)
    ruta = cuenta_path(proveedor)
    fd = os.open(ruta + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(c, fh, ensure_ascii=False, indent=1)
    os.replace(ruta + ".tmp", ruta)
    return ruta


def carga_cuenta(proveedor="scaleway"):
    """{"access_key", "project_id"} de `tools/state/nube/<proveedor>.json` (casa base). Falla
    cerrado si falta el fichero, la access key o el project_id (llega tras el alta), si la zona no
    es la fijada o si nombra otro servicio del Llavero que el único permitido."""
    c = _lee_cuenta(proveedor)
    rel = os.path.join("tools", "state", "nube", "%s.json" % proveedor)
    if c is None or not RE_ACCESS.match(str(c.get("access_key"))):
        raise PuertaCerrada("sin cuenta de %s en %s (access key SCW…; paso 7): no se llama" % (proveedor, rel))
    if not RE_UUID.match(str(c.get("project_id"))):
        raise PuertaCerrada("sin project_id en %s (llega tras crear el proyecto; nube_n1.py cuenta "
                            "--proyecto <uuid>): no se llama" % rel)
    if c.get("zona", ZONA) != ZONA:
        raise PuertaCerrada("%s dice zona %r, pero está fijada en %s: no se llama" % (rel, c.get("zona"), ZONA))
    if c.get("llavero_secret", SERVICIO_CLAVE.get(proveedor)) != SERVICIO_CLAVE.get(proveedor):
        raise PuertaCerrada("%s nombra otra clave del Llavero; la única es %s: no se llama"
                            % (rel, SERVICIO_CLAVE.get(proveedor)))
    return {"access_key": c["access_key"], "project_id": c["project_id"]}


def carga_config(ruta=None, proveedor="scaleway"):
    """La configuración versionada. Zona, región y tipo tienen que ser los del código."""
    try:
        with open(ruta or CONFIG, encoding="utf-8") as fh:
            cfg = (json.load(fh) or {}).get(proveedor)
    except (OSError, ValueError):
        cfg = None
    if not isinstance(cfg, dict):
        raise PuertaCerrada("configuración de %s ilegible (%s)" % (proveedor, os.path.relpath(ruta or CONFIG)))
    for k, fijo in (("zona", ZONA), ("region_s3", REGION_S3), ("tipo", TIPO)):
        if cfg.get(k) != fijo:
            raise PuertaCerrada("config: %s=%r, pero está fijado en el código a %r" % (k, cfg.get(k), fijo))
    return cfg


def _canon(nombre):
    """Nombre canónico de un paquete de PyPI (PEP 503)."""
    return re.sub(r"[-_.]+", "-", nombre).lower()


def exigir_sellada(cfg):
    """Lanza PuertaCerrada si a la configuración le falta algo para `lanzar` (todo lo que va a la
    máquina, comprobado por forma). Devuelve la lista de clasificadores."""
    faltan = []
    if not RE_UUID.match(str((cfg.get("imagen_gpu") or {}).get("id"))):
        faltan.append("imagen_gpu.id (imagen GPU del proveedor, sin sellar)")
    dk = cfg.get("docker") or {}
    if not RE_IMAGEN_DOCKER.match(str(dk.get("imagen"))) or not RE_DIGEST.match(str(dk.get("digest"))):
        faltan.append("docker.imagen@digest (sha256:…, sin sellar)")
    pins = cfg.get("pip") or []
    nombres = [_canon(str(p).split("==")[0]) for p in pins]
    if not pins or not all(RE_PIN.match(str(p)) for p in pins) or len(set(nombres)) != len(nombres) or \
            "cellvit==%s" % (cfg.get("cellvit") or {}).get("version") not in pins:
        faltan.append("pip (cellvit==<versión> y TODO fijado con ==, sin repetir paquete)")
    fuente = cfg.get("pip_solo_fuente")
    if not isinstance(fuente, list) or not all(isinstance(f, str) and RE_PAQUETE.match(f) and _canon(f) == f
                                               and f in nombres for f in fuente):
        faltan.append("pip_solo_fuente (nombres canónicos de paquetes fijados en pip; [] si ninguno)")
    cv = cfg.get("cellvit") or {}
    if cv.get("version") != _he().CELLVITPP_VERSION or cv.get("modelo") not in ("SAM",) or \
            not RE_CLAVE_MODELO.match(str(cv.get("nombre_modelo"))) or cv.get("magnificacion") not in (20, 40):
        faltan.append("cellvit (versión del enganche %s, modelo SAM, magnificación)" % _he().CELLVITPP_VERSION)
    for ck in cfg.get("checkpoints") or [None]:
        ck = ck or {}
        u = urllib.parse.urlsplit(str(ck.get("url")))
        if not RE_NOMBRE_FICHERO.match(str(ck.get("nombre"))) or u.scheme != "https" or \
                u.hostname not in HOSTS_CHECKPOINT or not RE_SHA.match(str(ck.get("sha256"))):
            faltan.append("checkpoint %s (https de %s y sha256 sellado)" % (ck.get("nombre"), "/".join(HOSTS_CHECKPOINT)))
    he = _he()
    clas = cfg.get("clasificadores") or []
    mapa = cfg.get("mapa_clases") or {}
    if not clas or not all(he._RE_CLASIF.match(str(c)) for c in clas):
        faltan.append("clasificadores")
    for c in clas:
        m = mapa.get(c)
        if not isinstance(m, dict) or not m or not all(isinstance(k, str) and v in he.COMUN for k, v in m.items()):
            faltan.append("mapa_clases.%s (clase original → %s; decisión ingeniera, sin sellar)"
                          % (c, "/".join(he.COMUN)))
    ent = cfg.get("entradas_permitidas") or []
    if not ent or not all(exporta_n1.RE_NOMBRE_TIFF.match(str(e)) for e in ent):
        faltan.append("entradas_permitidas (copias N1 de lámina entera)")
    caduca, espera, sondeo = cfg.get("url_caduca_s"), cfg.get("espera_max_s"), cfg.get("sondeo_s")
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (caduca, espera, sondeo)) or \
            not 3600 <= caduca <= 7 * DIA_S or not 600 <= espera <= caduca - 600 or not 5 <= sondeo <= 600:
        faltan.append("tiempos (url_caduca_s 1 h-7 d; espera_max_s ≤ caducidad − 10 min; sondeo_s 5-600)")
    if faltan:
        raise PuertaCerrada("configuración sin sellar, no se lanza: " + "; ".join(faltan))
    return list(clas)


# ── La clave, solo al usarla ───────────────────────────────────────────────────────────────
def _servicio_clave(proveedor):
    real = SERVICIO_CLAVE.get(proveedor)
    servicio = os.environ.get("BTP_NUBE_SERVICIO_CLAVE") or real
    if not servicio:
        raise PuertaCerrada("proveedor '%s' sin servicio de clave conocido" % proveedor)
    if os.environ.get("BTP_TEST_BATTERY") == "1" and servicio == real:
        raise PuertaCerrada("batería de tests: la clave real '%s' no se lee" % servicio)
    return servicio


def _clave(proveedor):
    """La clave secreta de API, del Llavero (sin fichero de respaldo). Sin ella, PuertaCerrada."""
    servicio = _servicio_clave(proveedor)
    v = _secrets.get(servicio)
    if not v:
        raise PuertaCerrada("sin clave '%s' en el Llavero (paso 7 de {{TITULAR}}): no se llama" % servicio)
    return v


# ── Firma SigV4 (S3) ───────────────────────────────────────────────────────────────────────
def _sha_hex(b):
    return hashlib.sha256(b or b"").hexdigest()


def _q(s):
    return urllib.parse.quote(s, safe="-_.~")


def uri_canonica(ruta):
    return "/".join(_q(seg) for seg in (ruta or "/").split("/")) or "/"


def query_canonica(pares):
    return "&".join("%s=%s" % (_q(k), _q(v)) for k, v in sorted((str(k), str(v)) for k, v in pares))


def _clave_firma(secreto, fecha, region, servicio):
    k = hmac.new(("AWS4" + secreto).encode("utf-8"), fecha.encode("utf-8"), hashlib.sha256).digest()
    for parte in (region, servicio, "aws4_request"):
        k = hmac.new(k, parte.encode("utf-8"), hashlib.sha256).digest()
    return k


def _firma(metodo, host, ruta, pares, cabeceras, sha_cuerpo, sk, region, servicio, amz):
    cab = {str(k).lower().strip(): " ".join(str(v).strip().split()) for k, v in cabeceras.items()}
    cab["host"] = host
    firmadas = ";".join(sorted(cab))
    canon = "\n".join([metodo, uri_canonica(ruta), query_canonica(pares),
                       "".join("%s:%s\n" % (k, cab[k]) for k in sorted(cab)), firmadas, sha_cuerpo])
    ambito = "%s/%s/%s/aws4_request" % (amz[:8], region, servicio)
    sts = "\n".join(["AWS4-HMAC-SHA256", amz, ambito, _sha_hex(canon.encode("utf-8"))])
    return firmadas, ambito, hmac.new(_clave_firma(sk, amz[:8], region, servicio), sts.encode("utf-8"),
                                      hashlib.sha256).hexdigest()


def firma_v4(metodo, host, ruta, pares, cabeceras, sha_cuerpo, ak, sk, region, servicio, amz):
    """Cabecera Authorization de AWS SigV4 (la que acepta el Object Storage de Scaleway)."""
    firmadas, ambito, f = _firma(metodo, host, ruta, pares, cabeceras, sha_cuerpo, sk, region, servicio, amz)
    return "AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s" % (ak, ambito, firmadas, f)


def presigna_v4(metodo, host, ruta, ak, sk, region, servicio, amz, expira):
    """URL prefirmada (SigV4 por query, solo `host` firmado, cuerpo sin firmar)."""
    pares = [("X-Amz-Algorithm", "AWS4-HMAC-SHA256"),
             ("X-Amz-Credential", "%s/%s/%s/%s/aws4_request" % (ak, amz[:8], region, servicio)),
             ("X-Amz-Date", amz), ("X-Amz-Expires", str(int(expira))), ("X-Amz-SignedHeaders", "host")]
    _fir, _amb, f = _firma(metodo, host, ruta, pares, {}, "UNSIGNED-PAYLOAD", sk, region, servicio, amz)
    return "https://%s%s?%s&X-Amz-Signature=%s" % (host, uri_canonica(ruta), query_canonica(pares), f)


# ── Transporte ─────────────────────────────────────────────────────────────────────────────
def _host_permitido(host):
    return host == API_HOST or host == S3_HOST or (host or "").endswith("." + S3_HOST)


def transporte_https(metodo, url, cuerpo, cabeceras, timeout=TIMEOUT_API_S):
    """(status, cabeceras en minúscula, bytes). HTTPS, sin proxies, solo a Scaleway, hecho por el
    hijo dentro de vision.sb. Hacia la API, el X-Auth-Token va solo por el entorno del hijo; hacia
    S3, ninguna clave (si una cabecera X-Auth-Token llegara aquí, no se llama). Antes de nada, los
    nombres de cabecera: token de RFC 9110 y comparados normalizados (`vision_n1.nombre_cabecera`;
    «X-Auth-Token » o « X-Auth-Token» pasaban la guarda de S3, que miraba el nombre exacto: 2-oct-26).
    Sin respuesta, ErrorNube. En la batería de tests se niega."""
    nombres = [vision_n1.nombre_cabecera(k) for k in cabeceras]          # antes de nada
    if os.environ.get("BTP_TEST_BATTERY") == "1":
        raise PuertaCerrada("batería de tests: el transporte real está prohibido")
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or not _host_permitido(p.hostname):
        raise PuertaCerrada("destino HTTP no permitido: %s" % p.hostname)
    if p.hostname == API_HOST:
        hosts, sufijos, clave_en = (API_HOST,), (), "X-Auth-Token"
    else:
        if "x-auth-token" in nombres:
            raise PuertaCerrada("X-Auth-Token hacia el Object Storage: no se llama")
        hosts, sufijos, clave_en = (S3_HOST,), (S3_HOST,), None
    try:
        return vision_n1.http_en_jaula(metodo, url, cuerpo, cabeceras, timeout, hosts=hosts,
                                       sufijos=sufijos, cabecera_clave=clave_en)
    except vision_n1.ErrorRed as e:
        raise ErrorNube("Scaleway %s %s: sin respuesta (%s)" % (metodo, p.path.split("?")[0][:80], e))


def _tag(el):
    return el.tag.rsplit("}", 1)[-1]


def _xml(b):
    try:
        return ET.fromstring(b or b"<x/>")
    except ET.ParseError:
        raise ErrorNube("respuesta XML ilegible del Object Storage")


# ── Cliente de Scaleway ────────────────────────────────────────────────────────────────────
class ScalewayBackend:
    """Instances + Block Storage (X-Auth-Token) y Object Storage S3 (SigV4) de Scaleway, en
    fr-par-2 / fr-par. `transporte(metodo, url, cuerpo, cabeceras, timeout) -> (status, cab, bytes)`.
    La clave se pide a `clave_fn` en la PRIMERA llamada y solo vive en esta instancia."""

    def __init__(self, cuenta, cfg, clave_fn, transporte=None, ahora=time.time, duerme=time.sleep):
        self.ak, self.proyecto, self.cfg = cuenta["access_key"], cuenta["project_id"], cfg
        self._clave_fn, self._clave = clave_fn, None
        self._t = transporte or transporte_https
        self.ahora, self.duerme = ahora, duerme
        self.bucket = PREFIJO_RECURSO + hashlib.sha256(self.proyecto.encode("ascii")).hexdigest()[:16]
        self.zona = ZONA

    def clave(self):
        if self._clave is None:
            self._clave = self._clave_fn()
        return self._clave

    def _err(self, que, st, b):
        msg = ""
        try:
            obj = json.loads(b.decode("utf-8"))
            msg = obj.get("message") or obj.get("type") or ""
        except (ValueError, UnicodeDecodeError, AttributeError):
            try:
                m = _xml(b).find(".//{*}Code")
                msg = m.text if m is not None else ""
            except ErrorNube:
                msg = ""
        # Limpiar ANTES de cortar (2-oct-26): al revés, una clave a caballo del corte salía a medias.
        texto = "Scaleway %s → HTTP %s %s" % (que, st, vision_n1._limpia_clave(msg, self._clave, 200))
        return ErrorNube(vision_n1._limpia_clave(texto, self._clave))

    # ── Instances / Block (JSON) ──
    def _api(self, metodo, ruta, cuerpo=None, texto=None, ok=(200, 201, 202, 204), falta_ok=False):
        cab = {"X-Auth-Token": self.clave()}
        datos = None
        if cuerpo is not None:
            datos, cab["Content-Type"] = json.dumps(cuerpo).encode("utf-8"), "application/json"
        elif texto is not None:
            datos, cab["Content-Type"] = texto.encode("utf-8"), "text/plain"
        st, _h, b = self._t(metodo, "https://%s%s" % (API_HOST, ruta), datos, cab, TIMEOUT_API_S)
        if falta_ok and st == 404:
            return None
        if st not in ok:
            raise self._err("%s %s" % (metodo, ruta.split("?")[0]), st, b)
        return json.loads(b.decode("utf-8")) if b else {}

    def _lista(self, ruta, clave, filtro_proyecto="project"):
        out, pag = [], 1
        while True:
            sep = "&" if "?" in ruta else "?"
            r = self._api("GET", "%s%s%s=%s&per_page=100&page=%d" % (ruta, sep, filtro_proyecto, self.proyecto, pag))
            items = (r or {}).get(clave) or []
            out += items
            if len(items) < 100:
                return out
            pag += 1

    def _inst(self, recurso):
        return "/instance/v1/zones/%s/%s" % (self.zona, recurso)

    def _block(self, recurso):
        return "/block/v1alpha1/zones/%s/%s" % (self.zona, recurso)

    def servidores(self):
        return self._lista(self._inst("servers"), "servers")

    def volumenes(self):
        return ([("instance", v["id"]) for v in self._lista(self._inst("volumes"), "volumes")] +
                [("block", v["id"]) for v in self._lista(self._block("volumes"), "volumes", "project_id")])

    def snapshots(self):
        return ([("instance", s["id"]) for s in self._lista(self._inst("snapshots"), "snapshots")] +
                [("block", s["id"]) for s in self._lista(self._block("snapshots"), "snapshots", "project_id")])

    def ips(self):
        return [ip["id"] for ip in self._lista(self._inst("ips"), "ips")]

    def grupos(self):
        return [g["id"] for g in self._lista(self._inst("security_groups"), "security_groups")
                if str(g.get("name", "")).startswith(PREFIJO_RECURSO)]

    def estado_servidor(self, sid):
        r = self._api("GET", self._inst("servers/%s" % sid), falta_ok=True)
        return None if r is None else (r.get("server") or {}).get("state")

    def crea_maquina(self, nombre, cloudinit, ids):
        """Grupo de seguridad (entrada cerrada), servidor, cloud-init y encendido. Va anotando lo
        creado en `ids` para que quien llama lo borre pase lo que pase."""
        g = self._api("POST", self._inst("security_groups"), {
            "name": nombre, "project": self.proyecto, "description": "btp nube_n1",
            "inbound_default_policy": "drop", "outbound_default_policy": "accept", "stateful": True,
            "tags": [ETIQUETA]})
        ids["grupo"] = g["security_group"]["id"]
        s = self._api("POST", self._inst("servers"), {
            "name": nombre, "commercial_type": TIPO, "image": self.cfg["imagen_gpu"]["id"],
            "project": self.proyecto, "security_group": ids["grupo"], "tags": [ETIQUETA],
            "dynamic_ip_required": True})["server"]
        ids["servidor"] = s["id"]
        ids["volumenes"] = [v["id"] for v in (s.get("volumes") or {}).values() if isinstance(v, dict) and v.get("id")]
        ips = [x.get("id") for x in (s.get("public_ips") or []) if isinstance(x, dict)]
        if isinstance(s.get("public_ip"), dict):
            ips.append(s["public_ip"].get("id"))
        ids["ips"] = sorted({i for i in ips if i})
        self._api("PATCH", self._inst("servers/%s/user_data/cloud-init" % ids["servidor"]), texto=cloudinit)
        self._api("POST", self._inst("servers/%s/action" % ids["servidor"]), {"action": "poweron"})
        return ids

    def _espera(self, cond, maximo):
        fin = self.ahora() + maximo
        while not cond():
            if self.ahora() >= fin:
                return False
            self.duerme(10)
        return True

    def _borra_volumen(self, tipo, vid):
        rutas = [self._inst("volumes/%s" % vid), self._block("volumes/%s" % vid)]
        for ruta in (rutas if tipo == "instance" else rutas[::-1]):
            if self._api("DELETE", ruta, falta_ok=True) is not None:
                return

    def borra_servidor(self, sid):
        """Apaga, elimina y espera a que no exista. Devuelve los volúmenes que tenía."""
        r = self._api("GET", self._inst("servers/%s" % sid), falta_ok=True)
        if r is None:
            return []
        s = r.get("server") or {}
        vols = [v["id"] for v in (s.get("volumes") or {}).values() if isinstance(v, dict) and v.get("id")]
        if s.get("state") not in ("stopped",):
            if s.get("state") not in ("stopping",):
                self._api("POST", self._inst("servers/%s/action" % sid), {"action": "poweroff"})
            if not self._espera(lambda: self.estado_servidor(sid) in ("stopped", None), ESPERA_APAGADO_S):
                raise ErrorNube("el servidor %s no se apagó en %d s" % (sid, ESPERA_APAGADO_S))
        self._api("DELETE", self._inst("servers/%s" % sid), falta_ok=True)
        if not self._espera(lambda: self.estado_servidor(sid) is None, ESPERA_BORRADO_S):
            raise ErrorNube("el servidor %s sigue existiendo tras borrarlo" % sid)
        return vols

    def borra_maquina(self, ids):
        """Servidor, sus volúmenes (el de arranque también), sus IPs y su grupo. Devuelve lo que
        quede de ESTOS recursos ({tipo: [id]}); vacío = borrada."""
        vols = list(ids.get("volumenes") or [])
        if ids.get("servidor"):
            vols += [v for v in self.borra_servidor(ids["servidor"]) if v not in vols]
        for v in vols:
            self._borra_volumen("instance", v)
        for ip in ids.get("ips") or []:
            self._api("DELETE", self._inst("ips/%s" % ip), falta_ok=True)
        if ids.get("grupo"):
            self._api("DELETE", self._inst("security_groups/%s" % ids["grupo"]), falta_ok=True)
        quedan = {"instancias": [s["id"] for s in self.servidores() if s.get("id") == ids.get("servidor")],
                  "volumenes": [v for _t, v in self.volumenes() if v in vols],
                  "ips": [i for i in self.ips() if i in (ids.get("ips") or [])],
                  "grupos_seguridad": [g for g in self.grupos() if g == ids.get("grupo")]}
        return {k: v for k, v in quedan.items() if v}

    # ── Object Storage (S3, SigV4) ──
    def _s3(self, metodo, objeto="", pares=(), cuerpo=b"", cabeceras=None, en_bucket=True):
        host = "%s.%s" % (self.bucket, S3_HOST) if en_bucket else S3_HOST
        ruta = "/" + objeto
        amz = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self.ahora()))
        sha = _sha_hex(cuerpo)
        cab = dict(cabeceras or {})
        cab.update({"x-amz-date": amz, "x-amz-content-sha256": sha})
        cab["Authorization"] = firma_v4(metodo, host, ruta, list(pares), {k: v for k, v in cab.items()},
                                        sha, self.ak, self.clave(), REGION_S3, "s3", amz)
        url = "https://%s%s%s" % (host, uri_canonica(ruta), ("?" + query_canonica(pares)) if pares else "")
        return self._t(metodo, url, cuerpo if cuerpo else None, cab, TIMEOUT_S3_S)

    def presigna(self, metodo, objeto, expira):
        amz = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self.ahora()))
        return presigna_v4(metodo, "%s.%s" % (self.bucket, S3_HOST), "/" + objeto, self.ak, self.clave(),
                           REGION_S3, "s3", amz, expira)

    def bucket_existe(self):
        st, _h, b = self._s3("HEAD")
        if st == 404:
            return False
        if st != 200:
            raise self._err("HEAD bucket", st, b)
        return True

    def _cifrado_y_sin_versionado(self):
        st, _h, b = self._s3("GET", pares=[("encryption", "")])
        algos = [e.text for e in _xml(b).iter() if _tag(e) == "SSEAlgorithm"] if st == 200 else []
        st2, _h2, b2 = self._s3("GET", pares=[("versioning", "")])
        estados = [e.text for e in _xml(b2).iter() if _tag(e) == "Status"] if st2 == 200 else ["?"]
        return "AES256" in algos and "Enabled" not in estados and "?" not in estados

    def asegura_bucket(self):
        """Bucket privado, cifrado por defecto (AES256) y sin versionado. Si no se puede poner o
        comprobar el cifrado, se borra el bucket vacío y PuertaCerrada: no se sube nada."""
        if not self.bucket_existe():
            st, _h, b = self._s3("PUT", cabeceras={"x-amz-acl": "private"})
            if st not in (200,):
                raise self._err("PUT bucket", st, b)
            xml = (b'<ServerSideEncryptionConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                   b'<Rule><ApplyServerSideEncryptionByDefault><SSEAlgorithm>AES256</SSEAlgorithm>'
                   b'</ApplyServerSideEncryptionByDefault></Rule></ServerSideEncryptionConfiguration>')
            md5 = base64.b64encode(hashlib.md5(xml).digest()).decode("ascii")
            st, _h, b = self._s3("PUT", pares=[("encryption", "")], cuerpo=xml,
                                 cabeceras={"Content-MD5": md5, "Content-Type": "application/xml"})
            if st not in (200, 204) or not self._cifrado_y_sin_versionado():
                self._s3("DELETE")
                raise PuertaCerrada("el bucket no quedó cifrado (AES256) y sin versionado: lo borro y no se "
                                    "sube nada")
        elif not self._cifrado_y_sin_versionado():
            raise PuertaCerrada("el bucket existe pero no está cifrado o tiene versionado: no se sube nada")

    def subir(self, nombre, datos):
        self.asegura_bucket()
        st, _h, b = self._s3("PUT", nombre, cuerpo=datos, cabeceras={
            "Content-Type": "application/octet-stream", "x-amz-server-side-encryption": "AES256"})
        if st != 200:
            raise self._err("PUT objeto", st, b)

    def bajar(self, nombre):
        st, _h, b = self._s3("GET", nombre)
        if st == 404:
            raise PuertaCerrada("%s no está en el bucket" % nombre)
        if st != 200:
            raise self._err("GET objeto", st, b)
        return b

    def existe(self, nombre):
        st, _h, b = self._s3("HEAD", nombre)
        if st == 404:
            return False
        if st != 200:
            raise self._err("HEAD objeto", st, b)
        return True

    def marca(self, nombre):
        """Los primeros bytes de DONE/FAILED, o None si no existe."""
        st, _h, b = self._s3("GET", nombre)
        if st == 404:
            return None
        if st != 200:
            raise self._err("GET marca", st, b)
        return b[:400]

    def borra_objeto(self, nombre, version=None):
        pares = [("versionId", version)] if version else []
        st, _h, b = self._s3("DELETE", nombre, pares=pares)
        if st not in (200, 204, 404):
            raise self._err("DELETE objeto", st, b)

    def versiones(self):
        """[(clave, versionId)] de TODAS las versiones y marcas de borrado del bucket."""
        if not self.bucket_existe():
            return []
        out, km, vm = [], None, None
        while True:
            pares = [("versions", "")] + ([("key-marker", km)] if km else []) + \
                    ([("version-id-marker", vm)] if vm else [])
            st, _h, b = self._s3("GET", pares=pares)
            if st != 200:
                raise self._err("GET versiones", st, b)
            raiz = _xml(b)
            for el in raiz:
                if _tag(el) in ("Version", "DeleteMarker"):
                    k = next((c.text for c in el if _tag(c) == "Key"), None)
                    v = next((c.text for c in el if _tag(c) == "VersionId"), None)
                    if k:
                        out.append((k, v))
            trunc = next((c.text for c in raiz if _tag(c) == "IsTruncated"), "false")
            if trunc != "true":
                return out
            km = next((c.text for c in raiz if _tag(c) == "NextKeyMarker"), None)
            vm = next((c.text for c in raiz if _tag(c) == "NextVersionIdMarker"), None)

    def objetos(self):
        return sorted({k for k, _v in self.versiones()})

    def borra_bucket(self):
        st, _h, b = self._s3("DELETE")
        if st in (200, 204, 404):
            return
        if st == 409:
            raise PuertaCerrada("el bucket no está vacío: no se puede borrar")
        raise self._err("DELETE bucket", st, b)

    def buckets(self):
        st, _h, b = self._s3("GET", en_bucket=False)
        if st != 200:
            raise self._err("GET buckets", st, b)
        return sorted(e.text for e in _xml(b).iter() if _tag(e) == "Name" and
                      str(e.text or "").startswith(PREFIJO_RECURSO))

    # ── Inventario y borrado total ──
    def listar(self):
        return {"instancias": [s["id"] for s in self.servidores()],
                "volumenes": [v for _t, v in self.volumenes()],
                "snapshots": [s for _t, s in self.snapshots()],
                "ips": self.ips(), "grupos_seguridad": self.grupos(),
                "objetos": self.objetos(), "buckets": self.buckets()}

    def borrar_todo(self):
        """Todo lo del proyecto en la zona y el bucket. Sigue aunque algo falle; devuelve los errores."""
        errores = []

        def intenta(f, *a):
            try:
                f(*a)
            except (ErrorNube, PuertaCerrada) as e:
                errores.append(str(e))

        for s in self.servidores():
            intenta(self.borra_servidor, s["id"])
        for tipo, sid in self.snapshots():
            intenta(self._api, "DELETE", (self._inst if tipo == "instance" else self._block)("snapshots/%s" % sid),
                    None, None, (200, 202, 204), True)
        for tipo, vid in self.volumenes():
            intenta(self._borra_volumen, tipo, vid)
        for ip in self.ips():
            intenta(self._api, "DELETE", self._inst("ips/%s" % ip), None, None, (200, 202, 204), True)
        for g in self.grupos():
            intenta(self._api, "DELETE", self._inst("security_groups/%s" % g), None, None, (200, 202, 204), True)
        for k, v in self.versiones():
            intenta(self.borra_objeto, k, v)
        intenta(self.borra_bucket)
        return errores


# ── Guardas comunes y sellos ───────────────────────────────────────────────────────────────
def _abrir(proveedor, backend=None):
    """Guardas comunes. Devuelve (destino, evento, backend). La clave NO se lee aquí."""
    if proveedor not in PROVEEDORES:
        raise PuertaCerrada("proveedor '%s' no auditado (solo %s)" % (proveedor, ", ".join(PROVEEDORES)))
    destino = PREFIJO + proveedor
    puerta.exigir_diccionario()
    ev = puerta.exigir_confianza(destino, PREFIJO)
    if backend is None:
        backend = ScalewayBackend(carga_cuenta(proveedor), carga_config(proveedor=proveedor),
                                  lambda: _clave(proveedor))
    return destino, ev, backend


def _sella(evento, destino, **kw):
    return puerta.borde._sellar(dict({"evento": evento, "destino": destino, "nivel": "info",
                                      "zona": ZONA, "region": REGION_S3}, **kw))


def _sella_o_cierra(evento, destino, **kw):
    if not _sella(evento, destino, **kw):
        raise PuertaCerrada("no pude sellar '%s' en la cadena: no se sigue" % evento)


# ── Verbos ─────────────────────────────────────────────────────────────────────────────────
def subir(proveedor, rutas, backend=None, avisar=False):
    """Sube ficheros N1. Antes de llamar al backend: ruta en N1 y sha256 del manifiesto (barato),
    guardas comunes, el CONTENIDO de cada fichero re-validado por su tipo y, en el primer envío a
    este proveedor, el aviso a {{TITULAR}} (`avisar`, OPT-IN: lo pone el CLI; sin él y con el aviso
    pendiente, PuertaCerrada). Se suben los bytes verificados, no una relectura del disco."""
    if not rutas:
        raise PuertaCerrada("nada que subir")
    man = puerta.cargar_manifiesto()
    verificados = [(puerta.fichero_n1_verificado(r, man), os.path.realpath(r)) for r in rutas]
    for (nombre, _ent), _real in verificados:
        exporta_n1.nombre_n1(nombre)        # es el nombre del objeto en la nube (3.ª pasada)
    destino, ev, be = _abrir(proveedor, backend)
    lote = [(nombre, ent, exporta_n1.revalidar_n1(real, ent["sha256"]))
            for (nombre, ent), real in verificados]
    puerta.avisar_primer_envio(destino, ev, len(lote), sum(len(d) for _n, _e, d in lote),
                               avisar=avisar)
    for nombre, ent, datos in lote:
        _sella_o_cierra("nube_n1_subir", destino, objeto=nombre, sha256=ent["sha256"],
                        sha256_enviado=hashlib.sha256(datos).hexdigest(), bytes=len(datos))
        be.subir(nombre, datos)
    return [n for n, _e, _d in lote]


def _ultimo_subido(destino, objeto):
    sha = None
    for rec in puerta._eventos_de(destino) or ():
        if rec.get("evento") == "nube_n1_subir" and rec.get("objeto") == objeto:
            sha = rec.get("sha256_enviado")
    return sha


def salidas_esperadas(clasificadores):
    he = _he()
    return ["%s.%s.%s" % (he.PRIMARIO, c, ext) for c in clasificadores for ext in ("csv", "json")]


def _lee(ruta):
    with open(ruta, encoding="utf-8") as fh:
        return fh.read()


def cloudinit(cfg, trabajo, entrada, sha_entrada, mpp, urls):
    """El cloud-init del trabajo: la plantilla versionada con los valores validados. `urls`:
    {"entrada": url GET, "salidas": {fichero: url PUT}, "DONE": url, "FAILED": url}."""
    clas = exigir_sellada(cfg)
    if not RE_TRABAJO.match(trabajo) or not exporta_n1.RE_NOMBRE_TIFF.match(entrada) or \
            not RE_SHA.match(sha_entrada or "") or not (isinstance(mpp, float) and 0.05 < mpp < 5):
        raise PuertaCerrada("valores del trabajo con forma inesperada: no se lanza")
    todas = [urls["entrada"], urls["DONE"], urls["FAILED"]] + list(urls["salidas"].values())
    for u in todas:
        p = urllib.parse.urlsplit(u)
        if p.scheme != "https" or not str(p.hostname).endswith("." + S3_HOST) or "'" in u:
            raise PuertaCerrada("URL prefirmada fuera del bucket: no se lanza")
    q = shlex.quote
    dk = cfg["docker"]
    imagen = "%s@%s" % (dk["imagen"], dk["digest"])
    w = '"$W"'
    baja_in = "baja %s %s/in/%s %s" % (q(urls["entrada"]), w, q(entrada), q(sha_entrada))
    baja_ck = "\n".join("baja %s %s/cache/%s %s" % (q(ck["url"]), w, q(ck["nombre"]), q(ck["sha256"]))
                        for ck in cfg["checkpoints"])
    sube = "\n".join("sube %s/res/%s %s" % (w, q(f), q(u)) for f, u in sorted(urls["salidas"].items()))
    valores = {"@@URL_FAILED@@": q(urls["FAILED"]), "@@URL_DONE@@": q(urls["DONE"]), "@@TRABAJO@@": q(trabajo),
               "@@IMAGEN@@": q(imagen), "@@PINS@@": " ".join(q(p) for p in cfg["pip"]),
               "@@SOLO_FUENTE@@": q(",".join(cfg["pip_solo_fuente"]) or ":none:"),
               "@@CLASIFICADORES@@": " ".join(q(c) for c in clas), "@@ENTRADA@@": q(entrada),
               "@@STEM@@": q(entrada[:-len(".tif")]), "@@MPP@@": q("%.6f" % mpp),
               "@@MAGNIF@@": q(str(cfg["cellvit"]["magnificacion"])), "@@MODELO@@": q(cfg["cellvit"]["modelo"]),
               "@@NOMBRE_MODELO@@": q(cfg["cellvit"]["nombre_modelo"]), "@@VERSION@@": q(cfg["cellvit"]["version"]),
               "@@BAJA_ENTRADAS@@": baja_in, "@@BAJA_CHECKPOINTS@@": baja_ck, "@@SUBE_RESULTADOS@@": sube}
    script = _lee(EJECUTA)
    for k, v in valores.items():
        script = script.replace(k, v)
    if "@@" in script:
        raise PuertaCerrada("plantilla _ejecuta.sh con marcadores sin rellenar: no se lanza")
    mapa = json.dumps({c: cfg["mapa_clases"][c] for c in clas}, sort_keys=True)
    b64 = lambda s: base64.b64encode(s.encode("utf-8")).decode("ascii")  # noqa: E731
    texto = _lee(PLANTILLA_CI).replace("@@EJECUTA_B64@@", b64(script)).replace(
        "@@ENGANCHE_B64@@", b64(_lee(ENGANCHE))).replace("@@MAPA_B64@@", b64(mapa))
    if "@@" in texto or not texto.startswith("#cloud-config"):
        raise PuertaCerrada("plantilla de cloud-init con marcadores sin rellenar: no se lanza")
    if len(texto.encode("utf-8")) > MAX_CLOUDINIT:
        raise PuertaCerrada("cloud-init de %d bytes: pasa de %d" % (len(texto), MAX_CLOUDINIT))
    return texto


def _texto_seguro(b):
    return re.sub(r"[^A-Za-z0-9=_ .:-]", "?", (b or b"").decode("ascii", "replace"))[:200]


def lanzar(proveedor, entradas=None, backend=None):
    """Lanza el trabajo CellViT++ sobre las copias N1 ya subidas, espera a DONE/FAILED y BORRA la
    máquina pase lo que pase (también con Ctrl-C). Devuelve {"resultado", "detalle", "trabajo",
    "maquina_borrada"}. Si la máquina no se puede borrar del todo, PuertaCerrada con lo que queda."""
    destino, _ev, be = _abrir(proveedor, backend)
    cfg = be.cfg
    clas = exigir_sellada(cfg)
    entradas = list(entradas or cfg["entradas_permitidas"])
    if len(entradas) != 1 or entradas[0] not in cfg["entradas_permitidas"]:
        raise PuertaCerrada("una sola entrada y de las permitidas (%s)" % ", ".join(cfg["entradas_permitidas"]))
    entrada = exporta_n1.nombre_n1(entradas[0])
    ent_man = puerta.cargar_manifiesto()["ficheros"].get(entrada) or {}
    mpp = ent_man.get("mpp")
    if not isinstance(mpp, float):
        raise PuertaCerrada("%s sin mpp en el manifiesto N1: no se lanza" % entrada)
    sha = _ultimo_subido(destino, entrada)
    if not sha or not be.existe(entrada):
        raise PuertaCerrada("%s no está subido (nube_n1.py subir): no se lanza" % entrada)
    salidas = salidas_esperadas(clas)
    for viejo in list(MARCAS) + salidas:            # restos de otro lanzamiento: fuera antes
        if be.existe(viejo):
            be.borra_objeto(viejo)
    trabajo = os.urandom(4).hex()
    caduca = cfg["url_caduca_s"]
    urls = {"entrada": be.presigna("GET", entrada, caduca), "DONE": be.presigna("PUT", "DONE", caduca),
            "FAILED": be.presigna("PUT", "FAILED", caduca),
            "salidas": {f: be.presigna("PUT", f, caduca) for f in salidas}}
    texto = cloudinit(cfg, trabajo, entrada, sha, mpp, urls)
    _sella_o_cierra("nube_n1_lanzar", destino, trabajo=trabajo, tipo=TIPO, imagen=cfg["imagen_gpu"]["id"],
                    docker=cfg["docker"]["digest"], entrada=entrada, sha256_entrada=sha,
                    sha256_cloudinit=hashlib.sha256(texto.encode("utf-8")).hexdigest(),
                    clasificadores=clas, url_caduca_s=caduca)
    ids, resultado, detalle = {}, "SIN_TERMINAR", None
    try:
        be.crea_maquina(PREFIJO_RECURSO + trabajo, texto, ids)
        _sella("nube_n1_maquina", destino, trabajo=trabajo, servidor=ids.get("servidor"))
        limite = be.ahora() + cfg["espera_max_s"]
        while be.ahora() < limite:
            if be.marca("DONE") is not None:
                resultado = "DONE"
                break
            f = be.marca("FAILED")
            if f is not None:
                resultado, detalle = "FAILED", _texto_seguro(f)
                break
            st = be.estado_servidor(ids["servidor"])
            if st not in ("starting", "running"):
                resultado, detalle = "MAQUINA_PARADA", str(st)
                break
            be.duerme(cfg["sondeo_s"])
    finally:
        try:
            restos = be.borra_maquina(ids) if ids else {}
        except (ErrorNube, PuertaCerrada) as e:
            _sella("nube_n1_maquina_borrada", destino, trabajo=trabajo, vacio=False, nivel="alarma",
                   error=str(e)[:200])
            raise PuertaCerrada("NO pude borrar la máquina del trabajo %s (%s): bórrala YA con nube_n1.py "
                                "borrar; factura mientras exista" % (trabajo, e))
        _sella("nube_n1_maquina_borrada", destino, trabajo=trabajo, vacio=not restos,
               nivel="info" if not restos else "alarma", resultado=resultado)
    if restos:
        raise PuertaCerrada("la máquina del trabajo %s NO se borró del todo: queda %s; nube_n1.py borrar"
                            % (trabajo, ", ".join("%s=%d" % (k, len(v)) for k, v in restos.items())))
    return {"resultado": resultado, "detalle": detalle, "trabajo": trabajo, "maquina_borrada": True}


def estado(proveedor, backend=None):
    _d, _e, be = _abrir(proveedor, backend)
    falla = be.marca("FAILED") if be.bucket_existe() else None
    return {"instancias": [(s.get("id"), s.get("state")) for s in be.servidores()],
            "DONE": be.bucket_existe() and be.marca("DONE") is not None,
            "FAILED": _texto_seguro(falla) if falla is not None else None}


def listar(proveedor, backend=None):
    return _abrir(proveedor, backend)[2].listar()


def valida_enganche(meta_b, csv_b, clasificador, mpp_l0, dims_l0=None):
    """El par de CellViT++ contra `laminillas_he.CELLVITPP` (campos EXACTOS, versión, lámina,
    clasificador, mpp ±1 %, mapa a la taxonomía común, sha256 del CSV, cabecera, n filas, números
    finitos, prob en [0, 1], clases con mapa y, con `dims_l0`, coordenadas dentro de la lámina).
    Es dato externo: cualquier fallo, PuertaCerrada, y no se escribe nada."""
    he = _he()
    sal = he.CELLVITPP["salida"]
    try:
        meta = json.loads(meta_b.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise PuertaCerrada("enganche %s: el JSON no es JSON" % clasificador)
    if not isinstance(meta, dict) or set(meta) != set(sal["json_campos"]):
        raise PuertaCerrada("enganche %s: campos del JSON ≠ %s" % (clasificador, sorted(sal["json_campos"])))
    if meta["lamina"] != he.PRIMARIO or meta["clasificador"] != clasificador or \
            str(meta["version"]) != he.CELLVITPP_VERSION or not isinstance(meta["modelo"], str):
        raise PuertaCerrada("enganche %s: lámina, clasificador, versión o modelo no casan" % clasificador)
    m = meta["mpp_l0"]
    if isinstance(m, bool) or not isinstance(m, (int, float)) or not math.isfinite(m) or \
            abs(float(m) - mpp_l0) / mpp_l0 > 0.01:
        raise PuertaCerrada("enganche %s: mpp_l0 no es el del manifiesto" % clasificador)
    mapa = meta["mapa_clases"]
    if not isinstance(mapa, dict) or not mapa or any(not isinstance(k, str) or v not in he.COMUN
                                                     for k, v in mapa.items()):
        raise PuertaCerrada("enganche %s: mapa_clases vacío o fuera de %s" % (clasificador, "/".join(he.COMUN)))
    n = meta["n"]
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise PuertaCerrada("enganche %s: n no es un entero" % clasificador)
    if hashlib.sha256(csv_b).hexdigest() != meta["sha256_csv"]:
        raise PuertaCerrada("enganche %s: sha256 del CSV no casa" % clasificador)
    try:
        filas = list(csv.reader(io.StringIO(csv_b.decode("utf-8"))))
    except (UnicodeDecodeError, csv.Error):
        raise PuertaCerrada("enganche %s: CSV ilegible" % clasificador)
    if not filas or filas[0] != sal["csv_columnas"]:
        raise PuertaCerrada("enganche %s: cabecera ≠ %s" % (clasificador, sal["csv_columnas"]))
    if len(filas) - 1 != n:
        raise PuertaCerrada("enganche %s: n=%d y el CSV tiene %d filas" % (clasificador, n, len(filas) - 1))
    for fila in filas[1:]:
        if len(fila) != 4:
            raise PuertaCerrada("enganche %s: fila con %d campos" % (clasificador, len(fila)))
        try:
            x, y, p = float(fila[0]), float(fila[1]), float(fila[3])
        except ValueError:
            raise PuertaCerrada("enganche %s: número ilegible" % clasificador)
        if not all(math.isfinite(v) for v in (x, y, p)) or x < 0 or y < 0 or not 0 <= p <= 1:
            raise PuertaCerrada("enganche %s: coordenada o prob fuera de rango" % clasificador)
        if dims_l0 and (x >= dims_l0[0] or y >= dims_l0[1]):
            raise PuertaCerrada("enganche %s: coordenadas fuera de la lámina" % clasificador)
        if fila[2] not in mapa:
            raise PuertaCerrada("enganche %s: clase sin mapa" % clasificador)
    return meta


def bajar(proveedor, clasificador, destino_dir=None, backend=None):
    """El par del enganche de `clasificador`, del bucket a `SESION/nube/` (por defecto
    `SESION/nube/cellvitpp/`) y a ningún otro sitio. El destino se mira lo primero; después el
    esquema entero; solo entonces se escriben los dos ficheros (sin sobrescribir)."""
    base = os.path.realpath(nube_dir())
    destino_dir = destino_dir or enganche_dir()
    if not _dentro(destino_dir, base):
        raise PuertaCerrada("bajar solo escribe en SESION/nube/, no en %s" % destino_dir)
    he = _he()
    if not isinstance(clasificador, str) or not he._RE_CLASIF.match(clasificador):
        raise PuertaCerrada("clasificador no opaco: no se baja nada")
    n_json, n_csv = "%s.%s.json" % (he.PRIMARIO, clasificador), "%s.%s.csv" % (he.PRIMARIO, clasificador)
    entrada = "%s.n1.tif" % he.PRIMARIO
    ent_man = puerta.cargar_manifiesto()["ficheros"].get(entrada) or {}
    mpp = ent_man.get("mpp")
    if not isinstance(mpp, float):
        raise PuertaCerrada("%s sin mpp en el manifiesto N1: no puedo validar el enganche" % entrada)
    niv = (ent_man.get("niveles") or [{}])[0]
    dims = (niv.get("ancho"), niv.get("alto")) if niv.get("ancho") and niv.get("alto") else None
    destino, _ev, be = _abrir(proveedor, backend)
    meta_b, csv_b = be.bajar(n_json), be.bajar(n_csv)
    valida_enganche(meta_b, csv_b, clasificador, mpp, dims)
    os.makedirs(destino_dir, mode=0o700, exist_ok=True)
    real = os.path.realpath(destino_dir)
    rutas = [os.path.join(real, n) for n in (n_json, n_csv)]
    if any(os.path.lexists(r) for r in rutas):
        raise PuertaCerrada("el par de %s ya existe en %s: no sobrescribo" % (clasificador, destino_dir))
    out = []
    for ruta, datos in zip(rutas, (meta_b, csv_b)):
        fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(datos)
        sha = hashlib.sha256(datos).hexdigest()
        _sella("nube_n1_bajar", destino, objeto=os.path.basename(ruta), sha256=sha, origen="externo")
        out.append((ruta, sha))
    return out


def _marca_24h_path():
    return os.path.join(_estado_nube(), "pendiente_24h.json")


def _lee_marcas_24h():
    try:
        with open(_marca_24h_path(), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _escribe_marcas_24h(d):
    os.makedirs(_estado_nube(), mode=0o700, exist_ok=True)
    with open(_marca_24h_path() + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    os.replace(_marca_24h_path() + ".tmp", _marca_24h_path())


def borrar(proveedor, backend=None, ahora=time.time):
    """Borra instancias, volúmenes, snapshots, IPs, grupos propios, TODAS las versiones de todos
    los objetos y el bucket. Después lista: si queda algo, falla (PuertaCerrada) y lo dice.
    Devuelve el listado vacío como evidencia y deja pendiente la comprobación de las 24 h."""
    destino, _ev, be = _abrir(proveedor, backend)
    errores = be.borrar_todo()
    queda = be.listar()
    restos = {k: v for k, v in queda.items() if v}
    _sella("nube_n1_borrar", destino, vacio=not restos, errores=len(errores),
           nivel="info" if not restos else "alarma")
    marcas = _lee_marcas_24h()
    marcas[proveedor] = {"ts": int(ahora())}
    _escribe_marcas_24h(marcas)
    if restos:
        raise PuertaCerrada("borrar NO terminó: queda %s%s" % (
            ", ".join("%s=%d" % (k, len(v)) for k, v in restos.items()),
            (" (%s)" % "; ".join(errores[:3])) if errores else ""))
    return queda


def comprobar_24h(proveedor, backend=None, ahora=time.time, avisar=False):
    """La segunda comprobación: a las 24 h del último `borrar`, `listar` tiene que seguir vacío."""
    marca = _lee_marcas_24h().get(proveedor)
    if not marca:
        return {"estado": "nada pendiente"}
    falta = int(marca.get("ts", 0)) + DIA_S - int(ahora())
    if falta > 0:
        return {"estado": "aún no", "faltan_s": falta}
    destino, _ev, be = _abrir(proveedor, backend)
    queda = be.listar()
    restos = {k: v for k, v in queda.items() if v}
    _sella("nube_n1_listar_24h", destino, vacio=not restos, nivel="info" if not restos else "alarma")
    if restos:
        texto = ("Nube N1 (%s): a las 24 h del borrado sigue habiendo %s. Algo factura: "
                 "nube_n1.py borrar." % (proveedor, ", ".join("%s=%d" % (k, len(v)) for k, v in restos.items())))
        if avisar:
            import salida  # noqa: E402 — solo aquí
            salida.report_to_titular(texto, voz="sobria", fuente="nube-n1-24h")
        raise PuertaCerrada(texto)
    marcas = _lee_marcas_24h()
    marcas.pop(proveedor, None)
    _escribe_marcas_24h(marcas)
    return {"estado": "vacío a las 24 h", "listado": queda}


PLIST_24H = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
 <key>Label</key><string>com.btp.nube-n1-24h</string>
 <key>ProgramArguments</key><array><string>/usr/bin/python3</string><string>%s</string>
  <string>comprobar-24h</string><string>--avisar</string></array>
 <key>StartInterval</key><integer>3600</integer>
 <key>StandardOutPath</key><string>%s</string>
 <key>StandardErrorPath</key><string>%s</string>
</dict></plist>
"""


def main(argv):
    ap = argparse.ArgumentParser(prog="nube_n1.py")
    ap.add_argument("verbo", choices=("subir", "lanzar", "estado", "bajar", "listar", "borrar",
                                      "comprobar-24h", "cuenta", "plist-24h"))
    ap.add_argument("--proveedor", default="scaleway")
    ap.add_argument("--destino-dir", default="")
    ap.add_argument("--access-key", default="")
    ap.add_argument("--proyecto", default="")
    ap.add_argument("--avisar", action="store_true")
    ap.add_argument("args", nargs="*")
    a = ap.parse_args(argv)
    try:
        if a.verbo == "subir":
            print("subido:", ", ".join(subir(a.proveedor, a.args, avisar=True)))
        elif a.verbo == "bajar":
            if len(a.args) != 1:
                print("uso: nube_n1.py bajar <clasificador> [--destino-dir SESION/nube/cellvitpp]")
                return 2
            for ruta, sha in bajar(a.proveedor, a.args[0], a.destino_dir or None):
                print("bajado: %s (sha256 %s)" % (os.path.basename(ruta), sha))
        elif a.verbo == "borrar":
            print(json.dumps(borrar(a.proveedor), ensure_ascii=False))
        elif a.verbo == "lanzar":
            print(json.dumps(lanzar(a.proveedor, a.args or None), ensure_ascii=False))
        elif a.verbo == "comprobar-24h":
            print(json.dumps(comprobar_24h(a.proveedor, avisar=a.avisar), ensure_ascii=False))
        elif a.verbo == "cuenta":
            print("cuenta guardada en %s" % guarda_cuenta(a.access_key, a.proyecto, a.proveedor))
        elif a.verbo == "plist-24h":
            log = os.path.join(_estado_nube(), "comprobar-24h.log")
            print(PLIST_24H % (os.path.join(_casa.casa_base(), "tools", "nube_n1.py"), log, log), end="")
        else:
            print(json.dumps({"estado": estado, "listar": listar}[a.verbo](a.proveedor),
                             ensure_ascii=False, default=str))
    except PuertaCerrada as e:
        print("🛑 %s" % e)
        return 3
    except ErrorNube as e:
        print("⚠️ %s" % e)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
