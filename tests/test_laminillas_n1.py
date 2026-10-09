#!/usr/bin/env python3
"""test_laminillas_n1.py — la Puerta de N1, `exporta_n1`, `vision_n1` y `nube_n1` (plan
«laminillas DFCI», bloques de Verificación `exporta-n1` y `vision_n1 y nube_n1`).

TODO SINTÉTICO. Ningún dato real: titular, nombres, accesiones, canarios y láminas se inventan
aquí. Cada caso corre en un ÁRBOL COPIADO (las tools en un directorio temporal, sin overlays) con
`BTP_REPO` apuntando a una «casa base» falsa con overlays sintéticos: así los overlays reales de
{{TITULAR}} no se cargan nunca, y el test dice lo mismo corra desde un worktree o desde casa base.
Ningún caso llama a una API externa: Gemini (vision_n1), Scaleway (nube_n1) y MedGemma (local) van con
transportes, clave y cargador FALSOS inyectados, y en la batería el transporte real y la clave real
se niegan por sí solos (`BTP_TEST_BATTERY=1`). Ningún caso le escribe a {{TITULAR}}: en el árbol copiado `salida.py` es un
FALSO que solo apunta cada aviso en un fichero (norma «envío opt-in en tests»), y además va
`BTP_TEST_BATTERY=1` con el estado aislado.

PIL y tesseract: sin ellos solo se saltan las clases que fabrican imágenes (PuertaImagen, TiffN1,
Vision y los casos de Nube con TIFF), y solo con BTP_PORTABLE; en casa base, que falten es ROJO
(rc 1), como en test_visor3d_losa.
"""
import base64
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
PY = sys.executable

try:
    from PIL import Image, ImageDraw, ImageFont
    HAY_PIL = True
except ImportError:
    HAY_PIL = False
# libjpeg-turbo 3 (TurboJPEG): exporta_n1 decodifica cada tesela en estricto con ella (2-oct-26).
HAY_TJ = any(os.path.exists(p) for p in ("/opt/homebrew/lib/libturbojpeg.0.dylib",
                                         "/opt/homebrew/opt/jpeg-turbo/lib/libturbojpeg.0.dylib",
                                         "/usr/local/lib/libturbojpeg.0.dylib",
                                         "/usr/local/opt/jpeg-turbo/lib/libturbojpeg.0.dylib"))
FALTA = [n for n, ok in (("PIL", HAY_PIL), ("tesseract", bool(shutil.which("tesseract"))),
                         ("libturbojpeg", HAY_TJ)) if not ok]
if FALTA and not os.environ.get("BTP_PORTABLE"):
    print("ROJO: falta %s; sin eso la Puerta de N1 de imágenes, TIFF y visión no se prueba"
          % " y ".join(FALTA))
    sys.exit(1)
CON_IMAGEN = unittest.skipUnless(not FALTA, "falta %s (modo portátil)" % ", ".join(FALTA))

TITULAR = {"titular": {"nombre": "Leocadia Rosa", "apellidos": "Quintanar Tallon",
                       "nacimiento": "1979-03-14"}}
NOMBRES = {"nombres": ["eustaquio", "fulgencio"], "lugares_ruta": ["{{CENTRO}}", "DFCI"]}
IDENTIDAD = {"ids": ["X9988776Q"]}
CANARIO = "CANARIO-7f3a9c11"
FUENTES = ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Avenir Next Condensed.ttc")

# `salida.py` del árbol copiado: no habla con nadie. Mismo contrato que el real en lo que usa la
# puerta (halted, report_to_titular con HALT, STATE y el registro del TEXTO de lo que entrega, retiene
# o aplaza, con su `ts` en hora local como el real, que es donde puerta_n1 busca el id del aviso
# desde el 2-oct-26). BTP_TEST_SALIDA_MODO elige el veredicto: entregado (por defecto), retenido o
# aplazado.
SALIDA_FALSA = '''"""salida.py FALSO de test_laminillas_n1: apunta cada aviso en BTP_TEST_SALIDA_LOG."""
import json
import os
import time
HALT_FILES = tuple(p for p in (os.environ.get("BTP_HALT_FILES") or "").split(":") if p)
STATE = os.environ.get("BTP_STATE_DIR") or ""


def halted():
    return any(os.path.exists(p) for p in HALT_FILES)


def _registra(sub, nombre, texto):
    d = os.path.join(STATE, sub)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, nombre), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "texto": texto}) + "\\n")


def report_to_titular(text, **kw):
    if halted():
        return {"delivered": False, "blocked": True, "reason": "HALT activo"}
    with open(os.environ["BTP_TEST_SALIDA_LOG"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"texto": text, "kw": kw}) + "\\n")
    modo, hoy = os.environ.get("BTP_TEST_SALIDA_MODO", "entregado"), time.strftime("%Y-%m-%d")
    if modo == "retenido":
        _registra("notif", "holding-%s.jsonl" % hoy, text)
        return {"delivered": False, "blocked": False, "reason": "retenido (falso)", "retenido": True}
    if modo == "aplazado":
        _registra("aplazados", "%s.jsonl" % hoy, text)
        return {"delivered": False, "blocked": False, "reason": "aplazado (falso)", "aplazado": True}
    if modo == "falla":
        return {"delivered": False, "blocked": False, "reason": "canal caído (falso)"}
    _registra("salida", "enviados-%s.jsonl" % hoy, text)
    return {"delivered": True, "blocked": False, "reason": "entregado (falso)"}
'''

# Parche de la salida REAL para el test de contrato: ni red, ni token, ni el chat de {{TITULAR}}. Se
# importa puerta_n1 antes (fija BTP_STATE_DIR en la casa base falsa) para que salida.STATE sea esa.
PARCHE_SALIDA_REAL = '''import puerta_n1
import salida
import urllib.request
def _sin_red(*a, **k):
    raise RuntimeError("red prohibida en test_laminillas_n1")
urllib.request.urlopen = _sin_red
salida.get_secret = lambda *a, **k: None
salida._self_chatid = lambda: "1"
salida._bajo_bateria_test = lambda: False
salida._en_silencio = lambda cfg=None: False
salida._DELIVERERS["telegram"] = lambda dest, texto, reply_to=None: (True, "falso del test")
'''


# Gemini FALSO para vision_n1: se ejecuta en el subproceso del caso (con las tools copiadas en
# sys.path). Inyecta transporte y clave; nunca abre un socket. `envia` devuelve el texto o el motivo
# de cierre, y las llamadas que recibió el transporte (con cuántos `vision_n1_envio` había ya en la
# cadena en ese instante: el sello va ANTES de la llamada).
GEMINI_FALSO = r'''
import json as _j
import vision_n1
import puerta_n1 as _pu

RESPUESTA = '{"hay_error": false, "tipo": null, "cuadrante": null}'
CLAVE = "clave-falsa-GEMINI-0123456789"
_CLAVE_REAL = vision_n1._clave


def respuesta(texto=RESPUESTA):
    return _j.dumps({"candidates": [{"content": {"parts": [{"text": "pensando…", "thought": True},
                                                           {"text": texto}]}, "finishReason": "STOP"}],
                     "usageMetadata": {"promptTokenCount": 1290, "candidatesTokenCount": 22,
                                       "thoughtsTokenCount": 300, "totalTokenCount": 1612}}).encode()


class Transporte:
    def __init__(self, status=200, crudo=None, texto=RESPUESTA):
        self.llamadas, self.status, self.crudo, self.texto = [], status, crudo, texto

    def __call__(self, url, datos, cab, timeout):
        sellados = [e for e in _pu._eventos_cadena() if e.get("evento") == "vision_n1_envio"]
        self.llamadas.append({"url": url, "cuerpo": _j.loads(datos.decode("ascii")), "cab": dict(cab),
                              "sellados": len(sellados)})
        if self.crudo is not None:
            return self.status, self.crudo
        return self.status, respuesta(self.texto)


T = Transporte()


def envia(rutas, avisar=True, modelo="", destino="vision-n1:gemini", prompt="Review the Ki67 overlay",
          t=None, sin_clave=False):
    t = t or T
    vision_n1._clave = _CLAVE_REAL if sin_clave else (lambda proveedor: CLAVE)
    try:
        r = vision_n1.enviar(destino, modelo, prompt, rutas, avisar=avisar, transporte=t, verboso=False)
    except vision_n1.PuertaCerrada as e:
        r = "CERRADA " + str(e)
    except vision_n1.ErrorProveedor as e:
        r = "ERROR " + str(e)
    return {"r": r, "llamadas": t.llamadas}
'''

# Scaleway FALSO para nube_n1: Instances, Block y Object Storage (S3) en memoria, con las rutas que
# usa nube_n1. Comprueba lo que se puede comprobar sin el proveedor: zona fr-par-2, filtro por
# proyecto, X-Auth-Token solo hacia la API, firma SigV4 (forma) y la clave secreta NUNCA hacia S3
# ni en una URL o un cuerpo. Como dice la nota de la decisión, borrar el servidor deja su disco de
# arranque y su IP: nube_n1 tiene que borrarlos aparte. `al_encender(sim, sid)` hace de máquina.
SCALEWAY_FALSO = r'''
import base64 as _b64, hashlib as _h, json as _j, re as _re
import urllib.parse as _up
import nube_n1

AK = "SCWAAAAAAAAAAAAAAAAA"
PROYECTO = "11111111-2222-4333-8444-555555555555"
SECRETO = "secreto-falso-scaleway-XYZ"
S3 = ".s3.fr-par.scw.cloud"


class Reloj:
    def __init__(self, t=1790000000.0):
        self.t = t

    def ahora(self):
        return self.t

    def duerme(self, s):
        self.t += s


class Sim:
    def __init__(self, reloj=None, pegajoso=None, sin_cifrado=False, al_encender=None, falla_en=None,
                 no_borra_servidor=False):
        self.reloj = reloj or Reloj()
        self.llamadas, self.srv, self.vol, self.snap, self.ips, self.grupos = [], {}, {}, {}, {}, {}
        self.buckets, self.user_data, self.n, self.creado, self.sellos_al_crear = {}, {}, 0, None, None
        self.pegajoso, self.sin_cifrado, self.al_encender = pegajoso, sin_cifrado, al_encender
        self.falla_en, self.no_borra_servidor, self.expira = falla_en, no_borra_servidor, []

    def nid(self):
        self.n += 1
        return "%08d-0000-4000-8000-%012d" % (self.n, self.n)

    def __call__(self, metodo, url, cuerpo, cab, timeout):
        p = _up.urlsplit(url)
        self.llamadas.append([metodo, p.hostname, p.path, p.query])
        if self.falla_en and self.falla_en(metodo, p):
            raise RuntimeError("caída simulada de la red")
        assert p.scheme == "https", url
        visible = url + (cuerpo or b"").decode("latin-1")
        if p.hostname == "api.scaleway.com":
            assert cab.get("X-Auth-Token") == SECRETO, "API sin X-Auth-Token"
            assert SECRETO not in visible, "la clave en la URL o en el cuerpo"
            return self._api(metodo, p, cuerpo)
        assert SECRETO not in visible + _j.dumps(cab), "la clave secreta viaja hacia S3"
        assert "X-Auth-Token" not in cab
        auth = cab.get("Authorization", "")
        assert _re.match(r"^AWS4-HMAC-SHA256 Credential=%s/\d{8}/fr-par/s3/aws4_request, "
                         r"SignedHeaders=[a-z0-9;-]+, Signature=[0-9a-f]{64}$" % AK, auth), auth
        assert cab.get("x-amz-content-sha256") == _h.sha256(cuerpo or b"").hexdigest()
        return self._s3(metodo, p, cuerpo or b"", cab)

    def _api(self, metodo, p, cuerpo):
        q = dict(_up.parse_qsl(p.query))
        m = _re.match(r"^/(instance/v1|block/v1alpha1)/zones/([a-z0-9-]+)/(.*)$", p.path)
        if not m or m.group(2) != "fr-par-2":
            return 404, {}, b'{"message": "zona o ruta"}'
        api, resto = m.group(1).split("/")[0], m.group(3)
        body = _j.loads(cuerpo) if cuerpo and metodo == "POST" else None

        def ok(obj, st=200):
            return st, {}, _j.dumps(obj).encode()
        if metodo == "GET" and "/" not in resto:
            assert (q.get("project") or q.get("project_id")) == PROYECTO, "lista sin filtrar por proyecto"
            if int(q.get("page", "1")) > 1:
                return ok({resto: []})
            if api == "instance":
                tablas = {"servers": [dict(v, id=k) for k, v in self.srv.items()],
                          "volumes": [{"id": k} for k, v in self.vol.items() if v == "instance"],
                          "snapshots": [{"id": k} for k, v in self.snap.items() if v == "instance"],
                          "ips": [{"id": k} for k in self.ips],
                          "security_groups": [{"id": k, "name": v["name"]} for k, v in self.grupos.items()] +
                                             [{"id": "sg-defecto", "name": "Default security group"}]}
            else:
                tablas = {"volumes": [{"id": k} for k, v in self.vol.items() if v == "block"],
                          "snapshots": [{"id": k} for k, v in self.snap.items() if v == "block"]}
            return ok({resto: tablas[resto]})
        partes = resto.split("/")
        if api == "instance" and partes[0] == "security_groups":
            if metodo == "POST":
                assert body["inbound_default_policy"] == "drop" and body["project"] == PROYECTO
                gid = self.nid()
                self.grupos[gid] = body
                return ok({"security_group": {"id": gid}}, 201)
            if metodo == "DELETE":
                if partes[1] not in self.grupos:
                    return 404, {}, b"{}"
                if any(s.get("security_group") == partes[1] for s in self.srv.values()):
                    return 409, {}, b'{"message": "in use"}'
                del self.grupos[partes[1]]
                return 204, {}, b""
        if api == "instance" and partes[0] == "servers":
            if metodo == "POST" and len(partes) == 1:
                assert body["commercial_type"] == "L40S-1-48G" and body["project"] == PROYECTO
                self.creado = body
                import puerta_n1
                self.sellos_al_crear = [e.get("evento") for e in puerta_n1._eventos_cadena()]
                sid, vid, ip = self.nid(), self.nid(), self.nid()
                self.vol[vid], self.ips[ip] = "block", True
                self.srv[sid] = {"state": "stopped", "security_group": body["security_group"],
                                 "volumes": {"0": {"id": vid}}, "public_ip": {"id": ip}, "name": body["name"]}
                return ok({"server": dict(self.srv[sid], id=sid)}, 201)
            sid = partes[1]
            if sid not in self.srv:
                return 404, {}, b'{"message": "not found"}'
            if len(partes) == 2 and metodo == "GET":
                return ok({"server": dict(self.srv[sid], id=sid)})
            if len(partes) == 2 and metodo == "DELETE":
                if self.srv[sid]["state"] != "stopped" or self.no_borra_servidor:
                    return 400, {}, b'{"message": "no se puede"}'
                del self.srv[sid]
                return 204, {}, b""
            if partes[2:] == ["user_data", "cloud-init"] and metodo == "PATCH":
                self.user_data[sid] = cuerpo.decode("utf-8")
                return 204, {}, b""
            if partes[2:] == ["action"] and metodo == "POST":
                if body["action"] == "poweron":
                    self.srv[sid]["state"] = "running"
                    if self.al_encender:
                        self.al_encender(self, sid)
                elif body["action"] == "poweroff":
                    self.srv[sid]["state"] = "stopped"
                else:
                    return 400, {}, b"{}"
                return ok({"task": {}}, 202)
        if partes[0] in ("volumes", "snapshots", "ips") and metodo == "DELETE":
            tabla = {"volumes": self.vol, "snapshots": self.snap, "ips": self.ips}[partes[0]]
            k = partes[1]
            if k not in tabla or (partes[0] != "ips" and tabla[k] != api):
                return 404, {}, b"{}"
            if partes[0] == "volumes" and any(v["id"] == k for s in self.srv.values() for v in s["volumes"].values()):
                return 409, {}, b'{"message": "in use"}'
            del tabla[k]
            return 204, {}, b""
        return 404, {}, b'{"message": "ruta desconocida"}'

    def _s3(self, metodo, p, cuerpo, cab):
        q = dict(_up.parse_qsl(p.query, keep_blank_values=True))
        if p.hostname == S3[1:]:
            nombres = "".join("<Bucket><Name>%s</Name></Bucket>" % b for b in sorted(self.buckets))
            return 200, {}, ("<ListAllMyBucketsResult><Buckets>%s</Buckets></ListAllMyBucketsResult>" % nombres).encode()
        assert p.hostname.endswith(S3), p.hostname
        b = p.hostname[:-len(S3)]
        clave = _up.unquote(p.path[1:])
        bk = self.buckets.get(b)
        if clave == "":
            if metodo == "HEAD":
                return (200 if bk is not None else 404), {}, b""
            if metodo == "PUT" and "encryption" in q:
                if bk is None:
                    return 404, {}, b""
                if self.sin_cifrado:
                    return 501, {}, b"<Error><Code>NotImplemented</Code></Error>"
                assert b"<SSEAlgorithm>AES256</SSEAlgorithm>" in cuerpo and cab.get("Content-MD5")
                bk["cifrado"] = "AES256"
                return 200, {}, b""
            if metodo == "PUT":
                assert cab.get("x-amz-acl") == "private"
                if bk is not None:
                    return 409, {}, b"<Error><Code>BucketAlreadyOwnedByYou</Code></Error>"
                self.buckets[b] = {"obj": {}, "cifrado": None}
                return 200, {}, b""
            if bk is None:
                return 404, {}, b"<Error><Code>NoSuchBucket</Code></Error>"
            if metodo == "GET" and "encryption" in q:
                if not bk["cifrado"]:
                    return 404, {}, b"<Error><Code>ServerSideEncryptionConfigurationNotFoundError</Code></Error>"
                return 200, {}, (b"<ServerSideEncryptionConfiguration><Rule><ApplyServerSideEncryptionByDefault>"
                                 b"<SSEAlgorithm>AES256</SSEAlgorithm></ApplyServerSideEncryptionByDefault></Rule>"
                                 b"</ServerSideEncryptionConfiguration>")
            if metodo == "GET" and "versioning" in q:
                return 200, {}, b"<VersioningConfiguration/>"
            if metodo == "GET" and "versions" in q:
                v = "".join("<Version><Key>%s</Key><VersionId>null</VersionId></Version>" % k for k in sorted(bk["obj"]))
                return 200, {}, ("<ListVersionsResult><IsTruncated>false</IsTruncated>%s</ListVersionsResult>" % v).encode()
            if metodo == "DELETE":
                if bk["obj"]:
                    return 409, {}, b"<Error><Code>BucketNotEmpty</Code></Error>"
                del self.buckets[b]
                return 204, {}, b""
            return 400, {}, b""
        if bk is None:
            return 404, {}, b"<Error><Code>NoSuchBucket</Code></Error>"
        if metodo == "PUT":
            assert cab.get("x-amz-server-side-encryption") == "AES256"
            bk["obj"][clave] = cuerpo
            return 200, {}, b""
        if metodo in ("GET", "HEAD"):
            if clave not in bk["obj"]:
                return 404, {}, b""
            return 200, {}, (bk["obj"][clave] if metodo == "GET" else b"")
        if metodo == "DELETE":
            if self.pegajoso and clave == self.pegajoso:
                return 403, {}, b"<Error><Code>AccessDenied</Code></Error>"
            assert q.get("versionId") in (None, "null")
            bk["obj"].pop(clave, None)
            return 204, {}, b""
        return 400, {}, b""

    def put_prefirmado(self, url, datos):
        """Lo que hace la máquina con una URL prefirmada de su cloud-init."""
        p = _up.urlsplit(url)
        q = dict(_up.parse_qsl(p.query))
        assert q["X-Amz-Algorithm"] == "AWS4-HMAC-SHA256" and q["X-Amz-SignedHeaders"] == "host"
        assert q["X-Amz-Credential"].startswith(AK + "/") and q["X-Amz-Credential"].endswith("/fr-par/s3/aws4_request")
        assert _re.match(r"^[0-9a-f]{64}$", q["X-Amz-Signature"]) and SECRETO not in url
        self.buckets[p.hostname[:-len(S3)]]["obj"][_up.unquote(p.path[1:])] = datos
        return int(q["X-Amz-Expires"])

    def vacio(self):
        return not (self.srv or self.vol or self.snap or self.ips or self.grupos or self.buckets)


def partes_ci(sim, sid):
    """(ejecuta.sh, a_enganche.py, mapa) del cloud-init que recibió el servidor `sid`."""
    trozos = _re.findall(r"content: (\S+)", sim.user_data[sid])
    return (_b64.b64decode(trozos[0]).decode(), _b64.b64decode(trozos[1]).decode(),
            _j.loads(_b64.b64decode(trozos[2]).decode()))


def urls_de(script):
    return {_up.unquote(_up.urlsplit(u).path[1:]): u for u in _re.findall(r"'(https://[^']+)'", script)}


CELLS = {"type_map": {"0": "tumor", "1": "lymphocyte"},
         "cells": [{"centroid": [100.5, 200.25], "type": 0, "type_prob": 0.91},
                   {"centroid": [3000.0, 1500.0], "type": 1, "type_prob": 0.66}]}


def maquina(fin="DONE"):
    """`al_encender` que hace el trabajo con lo que lleva el cloud-init: convierte con el
    _a_enganche.py QUE VIAJA, sube con sus URLs y escribe DONE (o FAILED, o nada)."""
    def hook(sim, sid):
        script, enganche, mapa = partes_ci(sim, sid)
        urls = urls_de(script)
        if fin == "DONE":
            ns = {"__name__": "a_enganche"}
            exec(compile(enganche, "a_enganche.py", "exec"), ns)
            for c, m in sorted(mapa.items()):
                datos, meta = ns["convierte"](CELLS, c, 0.2506, m, "CellViT-SAM-H-x40", "1.0.9")
                sim.expira.append(sim.put_prefirmado(urls["P-HE.%s.csv" % c], datos))
                sim.expira.append(sim.put_prefirmado(urls["P-HE.%s.json" % c], _j.dumps(meta).encode()))
            sim.put_prefirmado(urls["DONE"], b"DONE trabajo=x")
        elif fin == "FAILED":
            sim.put_prefirmado(urls["FAILED"], b"FAILED trabajo=x paso=docker rc=1 <script>")
    return hook


def backend(sim, cfg=None):
    return nube_n1.ScalewayBackend({"access_key": AK, "project_id": PROYECTO}, cfg or nube_n1.carga_config(),
                                   lambda: SECRETO, transporte=sim, ahora=sim.reloj.ahora, duerme=sim.reloj.duerme)


def intenta(f, *a, **k):
    try:
        return f(*a, **k)
    except nube_n1.PuertaCerrada as e:
        return "CERRADA " + str(e)
'''


def con_tty_huerfano(argv, env, cwd, palabra, pide, espera=180):
    """Lanza `argv` HUÉRFANO (doble fork: lo adopta launchd) con un pseudo-terminal de control, como
    test_trust_cloud_tty, y teclea `palabra` cuando sale `pide`. Que el doble fork esquive la
    comprobación de ancestros de borde es su límite declarado. Devuelve (rc, salida)."""
    import fcntl
    import re
    import select
    import signal
    import termios
    import time
    envoltura = ["/bin/sh", "-c", 'echo "__PID=$$"; "$@"; echo "__RC=$?"', "sh"] + list(argv)
    master, slave = os.openpty()
    pid = os.fork()
    if pid == 0:
        try:
            if os.fork() != 0:
                os._exit(0)
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
            for fd in (0, 1, 2):
                os.dup2(slave, fd)
            for fd in (master, slave):
                if fd > 2:
                    os.close(fd)
            os.chdir(cwd)
            os.execve(envoltura[0], envoltura, env)
        finally:
            os._exit(127)
    os.waitpid(pid, 0)
    os.close(slave)
    salida, enviado, fin = b"", False, time.time() + espera
    while time.time() < fin and b"__RC=" not in salida:
        r, _, _ = select.select([master], [], [], 0.5)
        if master in r:
            try:
                trozo = os.read(master, 4096)
            except OSError:
                break
            if not trozo:
                break
            salida += trozo
        if not enviado and pide in salida:
            os.write(master, palabra.encode() + b"\n")
            enviado = True
    texto = salida.decode("utf-8", "replace")
    m = re.search(r"__RC=(\d+)", texto)
    if not m:
        p = re.search(r"__PID=(\d+)", texto)
        if p:
            try:
                os.killpg(int(p.group(1)), signal.SIGKILL)
            except OSError:
                pass
    os.close(master)
    return (int(m.group(1)) if m else -9), texto


def _lee(ruta, modo="rb"):
    with open(ruta, modo) as fh:
        return fh.read()


def _fuente(tam):
    for f in FUENTES:
        if os.path.exists(f):
            return ImageFont.truetype(f, tam)
    return ImageFont.load_default(size=tam)


def _texto_con_alto(texto, alto_px, color=(70, 40, 120), fondo=(236, 200, 220), margen=20):
    """Imagen con `texto` cuyas mayúsculas miden ~`alto_px` de alto."""
    tam = alto_px
    for _ in range(6):                                   # ajusta el cuerpo al alto pedido
        caja = _fuente(tam).getbbox("XK7Q")
        tam = max(4, int(round(tam * alto_px / float(caja[3] - caja[1]))))
    f = _fuente(tam)
    caja = f.getbbox(texto)
    img = Image.new("RGB", (caja[2] - caja[0] + 2 * margen, caja[3] - caja[1] + 2 * margen), fondo)
    ImageDraw.Draw(img).text((margen - caja[0], margen - caja[1]), texto, font=f, fill=color)
    return img


def _textura(w, h, semilla=7):
    """H&E de mentira: ruido suave entre rosa (eosina) y morado (hematoxilina), sin texto."""
    import random
    random.seed(semilla)
    peq = Image.new("L", (max(1, w // 16), max(1, h // 16)))
    peq.putdata([random.randint(0, 255) for _ in range(peq.width * peq.height)])
    gris = peq.resize((w, h), Image.BICUBIC)
    from PIL import ImageOps
    return ImageOps.colorize(gris, (238, 190, 215), (120, 70, 150))


def _textura_con_rotulo(texto, alto=48, semilla=3):
    img = _textura(900, 500, semilla)
    img.paste(_texto_con_alto(texto, alto, margen=12), (60, 200))
    return img


PALABRAS_SUELTAS = ("QZ7K", "W4XP", "R8NJ", "H2VM", "T5LC", "B9DF")


def _palabras_sueltas():
    """Seis palabras de 32 px dispersas sobre textura (semilla 7). Medido el 1-oct-26: psm 3 no
    lee ninguna (a ×2 ni a ×0,5); psm 11 lee QZ7K y W4XP a ×2."""
    import random
    img = _textura(1000, 700, 7)
    random.seed(7 * 100 + 32)
    for w in PALABRAS_SUELTAS:
        t = _texto_con_alto(w, 32, margen=4, fondo=(236, 200, 220))
        img.paste(t, (random.randint(0, 1000 - t.width), random.randint(0, 700 - t.height)))
    return img


# ── TIFF sintético con la estructura del Grundium ─────────────────────────────────────────────
def _jpeg_limpio(img, extra=b""):
    """JPEG baseline 4:2:0 sin APP0 (solo DQT, DHT, SOF0, SOS); `extra` se mete tras el SOI."""
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85, subsampling=2)
    b = buf.getvalue()
    i, out = 2, bytearray(b[:2]) + extra
    while True:
        m = b[i + 1]
        if m == 0xDA:
            out += b[i:]
            break
        ln = struct.unpack(">H", b[i + 2:i + 4])[0]
        if not (0xE0 <= m <= 0xEF):
            out += b[i:i + 2 + ln]
        i += 2 + ln
    return bytes(out)


def lado_sin_relleno(w, h, maximo=512):
    """El lado de tesela más grande (múltiplo de 16, ≤ `maximo`) que divide `w` y `h`: un nivel sin
    RELLENO, como los 47 de las 15 láminas del Grundium (medido el 2-oct-26). None si no hay."""
    for lado in range(maximo, 15, -16):
        if w % lado == 0 and h % lado == 0:
            return lado
    return None


def escribe_tiff(ruta, niveles, extra_tags=True, com_en_tesela=False, extra_tesela=b"", tras_eoi=b"",
                 tags_l0=None, transforma=None, tags_k=None, tesela=None, cada_tesela=None):
    """BigTIFF little-endian. `niveles`: [(ancho, alto, imagen_PIL_o_None)] de mayor a menor;
    sin imagen, todas las teselas del nivel son la misma tesela rosa (una imagen en modo YCbCr se
    codifica tal cual: ids 1,2,3). `extra_tesela` se mete tras
    el SOI de la primera tesela de L0 (un segmento APPn, p. ej.) y `tras_eoi` se añade al final
    de esa tesela; `transforma(bytes) → bytes`, si se da, se le aplica después; `cada_tesela`,
    a TODAS las teselas de todos los niveles. `tags_l0` =
    {tag: (tipo, valor)} que se añaden o sustituyen en L0; `tags_k` = {k: {tag: (tipo, valor)}},
    lo mismo en el IFD k. `tesela`: lado de las teselas; por defecto, por nivel, el de
    `lado_sin_relleno` (sin relleno, como el Grundium) o 512 si no hay ninguno (entonces las del
    borde llevan RELLENO negro, el de `crop` fuera de la imagen, y exporta_n1 no lo exporta)."""
    rosas = {}
    datos = bytearray(b"\x00" * 16)
    ifds = []
    for k, (w, h, img) in enumerate(niveles):
        lado = tesela or lado_sin_relleno(w, h) or 512
        if lado not in rosas:
            rosas[lado] = _jpeg_limpio(Image.new("RGB", (lado, lado), (236, 200, 220)))
        rosa = rosas[lado]
        nx, ny = (w + lado - 1) // lado, (h + lado - 1) // lado
        offs, cnts = [], []
        for ty in range(ny):
            for tx in range(nx):
                if img is None:
                    t = rosa
                else:
                    t = _jpeg_limpio(img.crop((tx * lado, ty * lado, tx * lado + lado, ty * lado + lado)))
                if k == 0 and not offs:
                    if com_en_tesela:
                        texto = b"Leocadia"
                        t = t[:2] + b"\xff\xfe" + struct.pack(">H", len(texto) + 2) + texto + t[2:]
                    t = t[:2] + extra_tesela + t[2:] + tras_eoi
                    if transforma:
                        t = transforma(t)
                if cada_tesela:
                    t = cada_tesela(t)
                offs.append(len(datos))
                cnts.append(len(t))
                datos += t
        tags = {254: (4, [0 if k == 0 else 1]), 256: (3, [w]), 257: (3, [h]), 258: (3, [8, 8, 8]),
                259: (3, [7]), 262: (3, [6]), 277: (3, [3]), 284: (3, [1]),
                322: (3, [lado]), 323: (3, [lado]), 324: (16, offs), 325: (16, cnts),
                530: (3, [2, 2])}
        if k == 0:
            tags.update({282: (5, [39904, 1]), 283: (5, [39904, 1]), 296: (3, [3]),
                         270: (2, b"Leocadia R Q\x00")})
            if extra_tags:
                tags.update({305: (2, b"Escaner 1.0\x00"), 306: (2, b"2026:04:30 10:00:00\x00")})
            tags.update(tags_l0 or {})
        tags.update((tags_k or {}).get(k, {}))
        ifds.append(tags)
    fmt = {3: "H", 4: "I", 5: "II", 16: "Q"}
    pos_ifd = []
    for tags in ifds:
        fuera = {}
        for tag, (tipo, v) in sorted(tags.items()):
            if tipo in (1, 2):
                crudo, cnt = bytes(v), len(v)
            elif tipo == 5:                                   # [num, den, num, den…]
                crudo, cnt = struct.pack("<" + "II" * (len(v) // 2), *v), len(v) // 2
            else:
                crudo, cnt = struct.pack("<" + fmt[tipo] * len(v), *v), len(v)
            if len(crudo) > 8:
                fuera[tag] = (len(datos), crudo, cnt)
                datos += crudo
            else:
                fuera[tag] = (None, crudo, cnt)
        pos_ifd.append(len(datos))
        datos += struct.pack("<Q", len(tags))
        for tag, (tipo, _v) in sorted(tags.items()):
            off, crudo, cnt = fuera[tag]
            datos += struct.pack("<HHQ", tag, tipo, cnt)
            datos += struct.pack("<Q", off) if off is not None else crudo + b"\x00" * (8 - len(crudo))
        datos += b"\x00" * 8
    for a, b in zip(pos_ifd, pos_ifd[1:]):                # encadena
        n = struct.unpack("<Q", datos[a:a + 8])[0]
        datos[a + 8 + 20 * n:a + 16 + 20 * n] = struct.pack("<Q", b)
    datos[:16] = b"II" + struct.pack("<HHHQ", 43, 8, 0, pos_ifd[0])
    with open(ruta, "wb") as fh:
        fh.write(datos)


NOMBRE_SINTETICO = b"Leocadia Quintanar 24B0001043"


def _segmentos(t):
    """[(marca, inicio, fin)] de los segmentos de cabecera de una tesela JPEG hasta el SOS."""
    i, out = 2, []
    while True:
        m = t[i + 1]
        ln = struct.unpack(">H", t[i + 2:i + 4])[0]
        out.append((m, i, i + 2 + ln))
        if m == 0xDA:
            return out
        i += 2 + ln


def _piramide_coherente(base, texto_l0=None, alto_texto=12):
    """[(ancho, alto, imagen)] de L0 (`base`), ×4 y ×8 sacados de L0 (como un escáner). Con
    `texto_l0`, ese rótulo pequeño se pinta en L0 ANTES de reducir."""
    w, h = base.size
    if texto_l0:
        base = base.copy()
        base.paste(_texto_con_alto(texto_l0, alto_texto, margen=4), (700, 300))
    return [(w, h, base), (w // 4, h // 4, base.resize((w // 4, h // 4), Image.LANCZOS)),
            (w // 8, h // 8, base.resize((w // 8, h // 8), Image.LANCZOS))]


def lee_tiff(ruta):
    """[(tags {tag: (tipo, crudo)}, [teselas bytes])] de un BigTIFF little-endian (o clásico)."""
    b = _lee(ruta)
    big = struct.unpack("<H", b[2:4])[0] == 43
    off = struct.unpack("<Q", b[8:16])[0] if big else struct.unpack("<I", b[4:8])[0]
    tam = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 16: 8}
    fmt = {1: "B", 3: "H", 4: "I", 16: "Q"}
    out = []
    while off:
        n = struct.unpack("<Q" if big else "<H", b[off:off + (8 if big else 2)])[0]
        p = off + (8 if big else 2)
        tags = {}
        for i in range(n):
            e = b[p + i * (20 if big else 12): p + (i + 1) * (20 if big else 12)]
            tag, tipo = struct.unpack("<HH", e[:4])
            cnt = struct.unpack("<Q" if big else "<I", e[4:12 if big else 8])[0]
            campo = e[12:] if big else e[8:]
            nb = tam[tipo] * cnt
            if nb <= len(campo):
                crudo = campo[:nb]
            else:
                q = struct.unpack("<Q" if big else "<I", campo)[0]
                crudo = b[q:q + nb]
            tags[tag] = (tipo, crudo, cnt)
        p += n * (20 if big else 12)
        off = struct.unpack("<Q" if big else "<I", b[p:p + (8 if big else 4)])[0]

        def ints(t):
            tipo, crudo, cnt = tags[t]
            return struct.unpack("<" + fmt[tipo] * cnt, crudo)
        teselas = [b[o:o + c] for o, c in zip(ints(324), ints(325))]
        out.append((tags, teselas))
    return out


class Base(unittest.TestCase):
    """Árbol copiado + casa base falsa + N1 y SESION temporales + salida.py falso."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lam-n1-")
        self.copia = os.path.join(self.tmp, "arbol", "tools")
        os.makedirs(self.copia)
        for f in os.listdir(TOOLS):
            if f.endswith(".py"):
                shutil.copy(os.path.join(TOOLS, f), self.copia)
        shutil.copytree(os.path.join(TOOLS, "nube_n1_cloudinit"), os.path.join(self.copia, "nube_n1_cloudinit"))
        os.makedirs(os.path.join(self.copia, "panel_vision"))
        for f in os.listdir(os.path.join(TOOLS, "panel_vision")):
            if f.endswith(".py"):
                shutil.copy(os.path.join(TOOLS, "panel_vision", f), os.path.join(self.copia, "panel_vision"))
        with open(os.path.join(self.copia, "salida.py"), "w", encoding="utf-8") as fh:
            fh.write(SALIDA_FALSA)
        self.casa = os.path.join(self.tmp, "casa")
        self.casa_tools = os.path.join(self.casa, "tools")
        os.makedirs(os.path.join(self.casa_tools, "state", "borde"))
        for nombre, d in (("perfil.local.json", TITULAR), ("nombres.local.json", NOMBRES),
                          ("identidad.local.json", IDENTIDAD)):
            self._json(os.path.join(self.casa_tools, nombre), d)
        self._json(os.path.join(self.casa_tools, "state", "borde", "canarios.json"), [CANARIO])
        self.n1 = os.path.join(self.tmp, "Laminillas-N1")
        self.sesion = os.path.join(self.tmp, "sesion")
        os.makedirs(os.path.join(self.sesion, "nube"))
        self.halt = os.path.join(self.tmp, "no-halt")
        self.log_salida = os.path.join(self.tmp, "salida-falsa.jsonl")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("BTP_")}
        self.env.update(BTP_REPO=self.casa, BTP_N1_DIR=self.n1, BTP_LAMINILLAS_SESION=self.sesion,
                        BTP_HALT_FILES=self.halt, BTP_TEST_BATTERY="1",
                        BTP_TEST_SALIDA_LOG=self.log_salida,
                        BTP_NUBE_SERVICIO_CLAVE="btp-test-inexistente-laminillas-n1",
                        BTP_VISION_SERVICIO_CLAVE="btp-test-inexistente-laminillas-n1")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _json(ruta, d):
        with open(ruta, "w", encoding="utf-8") as fh:
            json.dump(d, fh)

    def corre(self, script, *args, env=None):
        return subprocess.run([PY, os.path.join(self.copia, script)] + list(args), capture_output=True,
                              text=True, timeout=600, env=env or self.env, cwd=self.tmp,
                              stdin=subprocess.DEVNULL, start_new_session=True)

    def codigo(self, src, env=None):
        """Ejecuta `src` con las tools copiadas en sys.path; devuelve el JSON que imprime."""
        r = subprocess.run([PY, "-c", "import sys, json; sys.path.insert(0, %r)\n" % self.copia + src],
                           capture_output=True, text=True, timeout=600, env=env or self.env,
                           cwd=self.tmp, stdin=subprocess.DEVNULL, start_new_session=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def fichero(self, nombre, contenido):
        ruta = os.path.join(self.tmp, nombre)
        with open(ruta, "w" if isinstance(contenido, str) else "wb") as fh:
            fh.write(contenido)
        return ruta

    def confia(self, destino, via="tty"):
        """Fixture: la casa base falsa tiene `destino` confiado, con su evento en la cadena. Desde la
        revisión del muro (1-oct-26) `_sellar` solo acepta un trust_cloud de píxeles con el sello de
        `trust_cloud()`: la fixture lo pasa a propósito (forja de test, en una casa base falsa)."""
        state = os.path.join(self.casa_tools, "state")
        ev = {"evento": "trust_cloud", "destino": destino, "para": "n1", "quien": "titular", "nivel": "info"}
        if via:
            ev["via"] = via
        env = dict(self.env, BTP_STATE_DIR=state)
        self.codigo("import borde\nborde._sellar(%r, _sello=borde._SELLO_CONFIANZA)\nprint(json.dumps(1))" % ev,
                    env=env)
        conf = os.path.join(state, "borde", "cloud_confiados.json")
        d = json.load(open(conf)) if os.path.exists(conf) else {}
        d[destino] = {"para": "n1", "quien": "titular", "revocado": False}
        self._json(conf, d)

    def sella(self, evento, fixture=False):
        """Sella `evento` (sin pasar por trust_cloud) en la cadena de la casa base falsa. Devuelve
        "SELLADO" o "RECHAZADO" (PermissionError de `_sellar`). `fixture`: por la puerta de las
        fixtures (`_SELLO_CONFIANZA`), que deja alarma antes."""
        env = dict(self.env, BTP_STATE_DIR=os.path.join(self.casa_tools, "state"))
        return self.codigo("import borde\ntry:\n    borde._sellar(%r%s)\n    print(json.dumps('SELLADO'))\n"
                           "except PermissionError:\n    print(json.dumps('RECHAZADO'))"
                           % (evento, ", _sello=borde._SELLO_CONFIANZA" if fixture else ""), env=env)

    def revision(self, sha, via="tty", opaco="P-F"):
        """Fixture: la revisión 1-bis de `sha` en la cadena, por la puerta de las fixtures (desde el
        2-oct-26 solo la sella borde.revisar_cristal_en_tty, que pide VISTO-N1 en la terminal)."""
        ev = {"evento": "n1_revision_humana", "opaco": opaco, "sha256": sha, "nivel": "info"}
        if via:
            ev["via"] = via
        return self.sella(ev, fixture=True)

    def eventos(self, evento=None):
        d = os.path.join(self.casa_tools, "state", "borde")
        out = []
        for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if fn.startswith("ledger-"):
                out += [json.loads(x) for x in _lee(os.path.join(d, fn), "r").splitlines() if x.strip()]
        return [e for e in out if evento is None or e.get("evento") == evento]

    def colar_en_n1(self, nombre, datos):
        """Lo que la revisión del muro temía: un fichero que NO pasó por exporta_n1, en N1 y con
        su sha256 apuntado a mano en el manifiesto."""
        os.makedirs(self.n1, exist_ok=True)
        ruta = os.path.join(self.n1, nombre)
        with open(ruta, "wb") as fh:
            fh.write(datos)
        man_p = os.path.join(self.n1, "manifiesto.json")
        man = json.load(open(man_p)) if os.path.exists(man_p) else {"version": 1, "ficheros": {}}
        man["ficheros"][nombre] = {"sha256": hashlib.sha256(datos).hexdigest(), "tipo": "colado"}
        self._json(man_p, man)
        return ruta

    def avisos(self):
        if not os.path.exists(self.log_salida):
            return []
        return [json.loads(ln) for ln in _lee(self.log_salida, "r").splitlines() if ln.strip()]

    def condiciones(self, cumplidas=True, modelo="gemini-3.1-pro-preview", veredicto="apto con condiciones",
                    donde=None, proveedor="gemini"):
        """auditorias.json de la casa base falsa (o de `donde`) con la entrada de `proveedor` y
        ninguna otra (por defecto, Gemini)."""
        d = os.path.join(donde or self.casa_tools, "panel_vision")
        os.makedirs(d, exist_ok=True)
        self._json(os.path.join(d, "auditorias.json"), {proveedor: {
            "veredicto": veredicto, "modelo": modelo, "condiciones_cumplidas": cumplidas}})

    def cuenta(self, proyecto="11111111-2222-4333-8444-555555555555", **extra):
        """La cuenta NO secreta de Scaleway en `tools/state/nube/scaleway.json` de la casa base
        falsa, con la forma del de verdad (project_id a null hasta que llegue)."""
        d = os.path.join(self.casa_tools, "state", "nube")
        os.makedirs(d, exist_ok=True)
        self._json(os.path.join(d, "scaleway.json"), dict({
            "access_key": "SCWAAAAAAAAAAAAAAAAA", "project_id": proyecto, "zona": "fr-par-2",
            "llavero_secret": "btp-scaleway-api", "nota": "no secreta"}, **extra))

    def png_n1(self, opaco="P-KI67", w=900, h=600):
        p = self.fichero("tex-%s.png" % opaco, b"")
        _textura(w, h).save(p)
        r = self.corre("exporta_n1.py", "png", p, "--opaco", opaco, "--nivel", "x8", "--mpp", "2.0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return os.path.join(self.n1, "%s__x8__mpp2.0000.png" % opaco)


class PuertaTexto(Base):
    """Los textos que tiene que parar y los que tiene que dejar pasar (exporta_n1 texto)."""

    def _veredictos(self, casos, ext="txt"):
        src = ("import exporta_n1 as e\nout = {}\nfor i, t in enumerate(%r):\n"
               "    p = '/%s/c%%d.%s' %% i\n"
               "    open(p, 'w').write(t)\n"
               "    try:\n        e.exporta_texto(p, 'caso-%%d.%s' %% i)\n        out[t] = 'PASA'\n"
               "    except e.PuertaCerrada as x:\n        out[t] = 'ABORTA ' + str(x)\n"
               "print(json.dumps(out))" % (list(casos), self.tmp.strip("/"), ext, ext))
        return self.codigo(src)

    def test_aborta(self):
        casos = ["Slide P-HE (Leocadia Quintanar)",           # nombre entre paréntesis
                 "Quintnaar",                                    # errata por transposición
                 "accession 31B00024681 block A1",              # accesión con un dígito de más
                 "scanned 30.4.2026", "2026-04-30",
                 "sent by {{CENTRO}}",
                 "reviewed with eustaquio",                      # nombre de la deny-list
                 "taplan",                                       # 6 letras, distancia 2 de «tallon»
                 "Rusa",                                         # 4 letras, distancia 1 de «rosa»
                 "call +34 612 345 678", "612345678",
                 "Leocadia",
                 "30 April 2026",
                 "id X9988776Q",                                 # identificador del diccionario
                 CANARIO]
        v = self._veredictos(casos)
        for t in casos:
            self.assertTrue(v[t].startswith("ABORTA"), "%r → %s" % (t, v[t]))
        self.assertNotIn("Leocadia", " ".join(v.values()))       # el motivo no repite el texto

    def test_aborta_ampliaciones_de_la_revision(self):
        """Hallazgos 11-14 y 21 de la revisión del muro (1-oct-26): cada caso pasaba la puerta."""
        casos = {
            11: ["24B 0001043", "26B 0008505", "24 B 0001043", "24-B-0001043", "24b0001043",
                 "26B.0008505", "HE_24B0001043", "slide24B0001043", "vh26b 17664", "b2026.22813",
                 "24b0001043a1"],                         # 12 hexadecimales: no se tapa
            12: ["04/30/2026", "2026/04/30", "1979/03/14", "20260430", "30 Apr 2026", "Apr 30, 2026",
                 "14 Mar 1979", "30 april 2026", "APRIL 30 2026", "30APR2026", "30 abril 2026",
                 "30-abr-2026", "Apr 2026"],
            13: ["612 345 678", "612-345-678", "0034612345678", "93 227 54 00", "617-632-3000",
                 "(617) 632-3000", "612 34 56 78"],
            14: ["LeocadiaRosa", "QuintanarTallon", "Leo\u200bcadia", "QUINTANARTALLON",
                 "HE_LeocadiaRosa"],
            21: ["MRN 12345678", "MRN: 12345678", "medical record number 12345678",
                 "patient id 1234567"],
        }
        todos = [t for lista in casos.values() for t in lista]
        v = self._veredictos(todos)
        fallos = ["h%d %r → %s" % (h, t, v[t]) for h, lista in casos.items() for t in lista
                  if not v[t].startswith("ABORTA")]
        self.assertEqual(fallos, [])
        self.assertNotIn("Quintanar", " ".join(v.values()))

    def test_no_aborta(self):
        # La lista del plan, más lo que las ampliaciones podrían haber roto: «may» verbo, palabras
        # cuyas letras juntas contendrían una parte de la titular («micro samples»: «rosa» solo
        # si se quitan los espacios), dimensiones de red, un sha256 y un uuid con tramos que
        # parecen teléfono, accesión y fecha.
        sha = ("612345678a12b3456c20260430d" + "0" * 64)[:64]
        casos = ["RE,85.2", "um_per_px", "pero", "peritumoral", "95% CI 38.5-44.1",
                 "IoU 0.47-0.78", "0.17/2.19/3.39", "timm 1.0.30", "512-128-256",
                 "Ki67 positive nuclei above T in the receiving laboratory's slide",
                 "cells may 12 hours later", "micro samples", "head 768-512-256",
                 "sha256 " + sha, "id 3f2a12b3-4567-4abc-8def-612345678abc",
                 "Figure 2B shows 300 nuclei", "grade 3 of 3", "x4 and x8 levels",
                 "sample sizes 600 300 150 per arm", "n = 123456 nuclei",
                 "record number 100000 nuclei", "textura sin texto"]
        v = self._veredictos(casos)
        for t in casos:
            self.assertEqual(v[t], "PASA", "%r → %s" % (t, v[t]))

    # Segunda pasada (2-oct-26): por detector, lo que tiene que parar (fugas que dejaron los
    # verificadores) y lo que tiene que pasar (falsos positivos que añadió la primera pasada).
    # Los negativos llevan además la lista del plan. Quedan como coste DECLARADO, y por eso no
    # están en ninguna lista: «NOV 3» y «seed 19790314» (fecha en mayúsculas o AAAAMMDD en texto),
    # «coords 812 345 678», «tile 712-345-678», «mean 612.345.678» (3-3-3 con prefijo español
    # válido) y «rosary», «stallion», «talloned» (distancia ≤2 del plan a una parte de la titular).
    DETECTORES = {
        "codigo_ap": (
            ["x_26-28381", "x26-28381", "26_28381", "26 - 28381", "24\u00a0B\u00a00001043",
             "24B\u20090001043", "24B/0001043", "24B,0001043", "24B:0001043", "24B\n0001043",
             "B-2026-22813", "24\uff220001043", "24\u04120001043", "24B_0001043", "HE_VH26B17664",
             "e_b2026_22813", "24B\u200b0001043", "26B00086505",
             # tercera pasada: la forma corta de caso_publico y el punto medio
             "24b-1043", "24B 1043", "24B.1043", "24B_1043", "24 B 1043", "24B 000 1043",
             "24B\u00b70001043"],
            ["Llama 70B 4096 context", "sizes 13B 1000", "batch 32 b 128", "panel 12 b 300 cells",
             "rows 10-b-200", "x20 B 100", "ViT-B 16", "sizes 26 28381", "mean 26.28381",
             "26/28381 positive", "Ki67 index 24 b 100"]),
        "fecha": (
            ["P-HE-14-03-1979", "HE-30-04-2026", "HE-04-30-2026", "HE-30.04.2026", "x-30/04/2026",
             "HE_30_04_2026", "HE_2026_04_30", "scan 30_04_2026", "2026 04 30", "abr 30 2026",
             "abril 30, 2026", "30 de abr de 2026", "abril de 2026", "30042026", "04302026",
             "slide_30042026", "30.IV.2026", "Apr 30", "30 Apr", "Mar 1", "30 april", "4-Oct",
             "Oct-4-2026", "30 abr.", "area 20260430.0",
             # tercera pasada: año de dos cifras con el mes en letra, mes español delante, espacios
             # alrededor del separador, «the», romano con año de dos cifras y «may» pegado
             "30-apr-26", "14-mar-79", "30abr26", "30 abr 26", "marzo 14", "abril 30",
             "30 . 04 . 2026", "March the 14th", "30-IV-26", "30-may-26", "30·04·2026"],
            ["OCT4 positive", "Oct-4 expression", "SEPT9 methylation", "MARCH5", "DEC1 staining",
             "c-Jun 2", "mar 1", "4 mar", "jun 2 genes", "sep 3 channels", "dec 2 places",
             "sample 12 ago", "area 20260430.5", "cells may 12 hours later", "tile 3 of 12, level 2 of 4"]),
        "telefono": (
            ["61 234 56 78", "Tel.612 345 678", "a.612 345 678", "phone,612 345 678",
             "tlf/612 345 678", "movil-612 345 678", "telefono,612 345 678", "612 345-678",
             "612-34 56 78", "617 632-3000", "612\u00a0345\u00a0678", "612\u2013345\u2013678",
             "617\u2013632\u20133000", "612\t345\t678", "612  345  678", "612/345/678",
             "0016176323000", "34 612 345 678", "932 275 400", "712 345 678",
             # tercera pasada
             "34612345678", "001 617 632 3000", "612,345,678", "612_345_678"],
            ["counts 701 233 845", "epochs 700 350 175", "bbox 612 345 678 901",
             "boxes 100 612 345 678", "free 900 350 175", "values 612, 345, 678",
             "counts 701,233,845"]),
        "mrn": (
            ["Record: 12345678", "MR# 1234567", "MRN=12345678", "MRN, 12345678",
             "the patient's MRN, 12345678", "MRN (12345678)", "MRN; 12345678",
             "MRN \u2013 12345678", "MRN\u00a012345678", "MRN:\n12345678", "MRN_12345678",
             "MRN/12345678", "MRN :: 12345678", "MRN # : 12345678", "MRN is 12345678",
             "MRN number 12345678", "MRN 1234-5678", "MRN 12-345-678", "MRN 123 456 78",
             "MRN: AB1234567", "MRN 1234567X", "\uff2d\uff32\uff2e 12345678", "MRN 12345678901234",
             "Med. Rec. # 1234567", "Unit number 1234567", "hospital number 123456",
             "patient id 123456",
             # tercera pasada
             "pt id 12345678", "PID 12345678", "EMPI 12345678", "MRN ≠ 12345678",
             "MRN → 12345678"],
            ["patient number 12345 of the cohort", "record #12345 in table", "mRNA 12345 reads",
             "rapid 123456 cells", "empirical 1234567"]),
        "casi_titular": (
            ["LQuintanar", "HeLeocadia", "QUINTANARtallon", "QuintnaarTallon", "quintnaartalon",
             "leocadiarsa", "Leo\u034fcadia", "Leo\ufe0fcadia", "Leo\u0301cadia",
             "Le\u0301ocadiaRo\u0301sa", "rosahe", "heleocadiahe"],
            ["rosacea", "prosaic", "metallothionein"]),
    }

    def test_cada_detector_con_positivos_y_negativos(self):
        todos = [t for pos, neg in self.DETECTORES.values() for t in pos + neg]
        v = self.codigo("import puerta_n1 as pu\nout = {}\nfor t in %r:\n"
                        "    out[t] = sorted({c for c, _p in pu.motivos(t)})\n"
                        "print(json.dumps(out))" % todos)
        fallos = []
        for capa, (pos, neg) in self.DETECTORES.items():
            fallos += ["%s debía parar %r → %s" % (capa, t, v[t]) for t in pos if capa not in v[t]]
            fallos += ["%s debía pasar %r → %s" % (capa, t, v[t]) for t in neg if v[t]]
        self.assertEqual(fallos, [])

    def test_limite_declarado_variantes_de_texto(self):
        """LÍMITE DECLARADO (puerta_n1, cabecera): variantes que siguen pasando —letras sueltas o
        cambiadas por cifras, cifras separadas una a una, etiquetas que no son de identificador,
        un año suelto— y costes que siguen abortando sin ser un dato suyo. Cerrar las primeras
        haría saltar texto N1 corriente; abrir los segundos, dejar pasar una fuga."""
        pasan = ["Le0cadia", "L e o c a d i a", "6 1 2 3 4 5 6 7 8", "6l2 345 678",
                 "2 4 B 0 0 0 1 0 4 3", "acct 12345678", "dob 1979", "3 nov"]
        abortan = ["NOV 3", "seed 19790314", "coords 812 345 678", "total 612,345,678 px",
                   "mean 612.345.678", "stallion"]
        v = self.codigo("import puerta_n1 as pu\nprint(json.dumps({t: bool(pu.motivos(t)) for t in %r}))"
                        % (pasan + abortan))
        self.assertEqual([t for t in pasan if v[t]], [])
        self.assertEqual([t for t in abortan if not v[t]], [])

    def _exporta(self, nombre, contenido):
        src = self.fichero("orig" + os.path.splitext(nombre)[1], contenido)
        return self.corre("exporta_n1.py", "texto", src, "--nombre", nombre)

    def test_numeros_y_cabeceras_que_son_identificadores(self):
        """Hallazgos 12, 18 y 21: lo que parsea como número no pasa por los detectores de texto."""
        malos = [("P-A1.csv", "id,valor\n1,612345678.0\n"),          # NHC con un NaN en la columna
                 ("P-A2.csv", "id,valor\n1,6.12345678e8\n"),
                 ("P-A3.json", json.dumps({"v": 612345678.0})),
                 ("P-A4.json", '{"v": 6.12345678e8}'),
                 ("P-A5.csv", "fecha,valor\n20260430,1\n"),            # AAAAMMDD como número
                 ("P-A6.json", '{"f": 20260430}'),
                 ("P-A7.csv", "MRN,valor\n12345678,1\n"),              # cabecera de identificador
                 ("P-A8.json", '{"mrn": 12345678}'),
                 # segunda pasada (2-oct-26): la cabecera o clave que LLEVA el identificador dentro
                 ("P-A9.csv", "Patient_MRN,valor\n12345678,1\n"),
                 ("P-A10.csv", "﻿mrn,valor\n12345678,1\n"),       # BOM de Excel
                 ("P-A11.csv", "MRN;valor\n12345678;1\n"),             # CSV con punto y coma
                 ("P-A12.csv", "M.R.N.,valor\n1,1\n"),
                 ("P-A13.json", '{"patient_mrn": 1}'),
                 ("P-A14.json", '{"properties": {"mrn-bwh": 1}}'),
                 ("P-A15.csv", "fecha,valor\n04302026,1\n"),           # MMDDAAAA con su cero
                 ("P-A16.csv", "area_um2,valor\n612345678.0,1\n"),     # ≥9 cifras: sin exención
                 ("P-A17.json", '{"area": 6.12345678e8}'),
                 # tercera pasada: el número como CADENA (entre comillas o agrupado), y PID
                 ("P-A18.json", '{"id": "123456789"}'),
                 ("P-A19.json", '{"v": "0123456789"}'),
                 ("P-A20.csv", 'id,valor\n1,"612,345,678"\n'),
                 ("P-A21.csv", "id,valor\n1,1_2345_6789\n"),
                 ("P-A22.json", '{"123456789": 1}'),
                 ("P-A23.csv", "PID,valor\n1234,1\n")]
        for nombre, contenido in malos:
            r = self._exporta(nombre, contenido)
            self.assertEqual(r.returncode, 3, "%s → %s" % (nombre, r.stdout + r.stderr))
            self.assertFalse(os.path.exists(os.path.join(self.n1, nombre)), nombre)
        buenos = [("P-B1.csv", "area_um2,x,y\n152345678.25,10234.5,8812\n"),
                  ("P-B2.json", '{"area_um2": 152345678.25, "n": 40}'),
                  # falsos positivos del hallazgo 18: una medida con forma de fecha, y un área
                  # redonda en notación ingeniera
                  ("P-B3.csv", "Nucleus: Area µm^2,x\n20260430,1\n20120415.0,2\n"),
                  ("P-B4.json", '{"area_um2": 20260430, "n": [20120415]}'),
                  ("P-B5.csv", "tissue_area_um2,x\n1.5e9,1\n2.5e8,2\n"),
                  ("P-B6.json", '{"area_um2": 2.5e8}'),
                  ("P-B7.csv", "mRNA_level,Object ID,Parent ID\n1,2,3\n"),
                  # lo que el plan exige que pase, como cadena de un CSV/JSON
                  ("P-B8.csv", 'red,ci,iou\n"512-128-256","95% CI 38.5-44.1","0.47-0.78"\n'),
                  ("P-B9.json", '{"timm": "1.0.30", "v": "0.17/2.19/3.39", "n": "12,345"}')]
        for nombre, contenido in buenos:
            r = self._exporta(nombre, contenido)
            self.assertEqual(r.returncode, 0, "%s → %s" % (nombre, r.stdout + r.stderr))

    def test_nombres_de_fichero_con_accesion_o_nombre(self):
        """Hallazgos 11 y 14: RE_NOMBRE_TEXTO deja pasar estos nombres; la puerta no."""
        ok = self.fichero("ok.csv", "marker,value\nRE,85.2\n")
        for nombre in ("HE_24B0001043.csv", "24b0001043.csv", "QuintanarTallon-HE.csv"):
            r = self.corre("exporta_n1.py", "texto", ok, "--nombre", nombre)
            self.assertEqual(r.returncode, 3, "%s → %s" % (nombre, r.stdout + r.stderr))
            self.assertFalse(os.path.exists(os.path.join(self.n1, nombre)), nombre)

    def test_csv_y_geojson_por_esquema(self):
        ok = self.fichero("ok.csv", "marker,value\nRE,85.2\nKi67,40\n")
        r = self.corre("exporta_n1.py", "texto", ok, "--nombre", "P-KI67-tabla.csv")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        largo = self.fichero("largo.csv", "id,valor\n1,123456789\n")
        r = self.corre("exporta_n1.py", "texto", largo, "--nombre", "P-KI67-largo.csv")
        self.assertEqual(r.returncode, 3, r.stdout)
        geo = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {
            "type": "Point", "coordinates": [10234.5, 8812]}, "properties": {"clase": "nucleo"}}]}
        g = self.fichero("ok.geojson", json.dumps(geo))
        self.assertEqual(self.corre("exporta_n1.py", "texto", g, "--nombre", "P-KI67-celulas.geojson").returncode, 0)
        geo["features"][0]["properties"]["imagen"] = "/Users/x/P-HE.tif"
        g2 = self.fichero("ruta.geojson", json.dumps(geo))
        self.assertEqual(self.corre("exporta_n1.py", "texto", g2, "--nombre", "P-KI67-c2.geojson").returncode, 3)
        man = json.load(open(os.path.join(self.n1, "manifiesto.json")))
        self.assertEqual(sorted(man["ficheros"]), ["P-KI67-celulas.geojson", "P-KI67-tabla.csv"])
        self.assertEqual(len(man["ficheros"]["P-KI67-tabla.csv"]["sha256"]), 64)
        # Hallazgo 28: el mismo --nombre otra vez, con otro contenido, no sustituye nada.
        sha_antes = man["ficheros"]["P-KI67-tabla.csv"]["sha256"]
        otro = self.fichero("otro.csv", "marker,value\nRE,10.0\n")
        r = self.corre("exporta_n1.py", "texto", otro, "--nombre", "P-KI67-tabla.csv")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("no sobrescribo", r.stdout)
        man = json.load(open(os.path.join(self.n1, "manifiesto.json")))
        self.assertEqual(man["ficheros"]["P-KI67-tabla.csv"]["sha256"], sha_antes)
        self.assertEqual(hashlib.sha256(_lee(os.path.join(self.n1, "P-KI67-tabla.csv"))).hexdigest(),
                         sha_antes)
        # Residuo del 28 (2-oct-26): el nombre en otra caja, el fichero borrado a mano con su
        # entrada aún en el manifiesto, y un symlink colgante en el destino tampoco sustituyen.
        os.remove(os.path.join(self.n1, "P-KI67-tabla.csv"))
        os.symlink(os.path.join(self.tmp, "no-existe.csv"), os.path.join(self.n1, "P-KI67-sym.csv"))
        for nombre in ("P-KI67-tabla.csv", "p-ki67-tabla.csv", "P-KI67-sym.csv"):
            r = self.corre("exporta_n1.py", "texto", otro, "--nombre", nombre)
            self.assertEqual(r.returncode, 3, "%s → %s" % (nombre, r.stdout + r.stderr))
            self.assertIn("no sobrescribo", r.stdout)
        man = json.load(open(os.path.join(self.n1, "manifiesto.json")))
        self.assertEqual(man["ficheros"]["P-KI67-tabla.csv"]["sha256"], sha_antes)
        self.assertNotIn("P-KI67-sym.csv", man["ficheros"])
        self.assertTrue(os.path.islink(os.path.join(self.n1, "P-KI67-sym.csv")))

    def test_geojson_ruta_fuera_de_properties(self):
        """Hallazgo 19: el nombre de la imagen original viaja en `metadata` o en la raíz."""
        base = {"type": "FeatureCollection", "features": [{
            "type": "Feature", "id": "3f2a12b3-4567-4abc-8def-612345678abc",
            "geometry": {"type": "Point", "coordinates": [10.5, 20]},
            "properties": {"objectType": "detection"}}],
            "laminillas": {"tabla_opaco_marcador": [{"lamina": "P-KI67", "marcador": "Ki67"}],
                           "umbral": {"sello_sha256_prefijo": "a12b3456c7d8"}}}
        g = self.fichero("ok.geojson", json.dumps(base))
        r = self.corre("exporta_n1.py", "texto", g, "--nombre", "P-KI67-meta.geojson")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)           # uuid y prefijo de sha: pasan
        for i, extra in enumerate(({"metadata": {"image": "/Volumes/scan/HE_x.tif"}},
                                   {"image": "scan-x.svs"}, {"/Users/x/a.tif": 1},
                                   # segunda pasada: URI, ruta relativa, formatos WSI que faltaban
                                   {"image": "file:///Volumes/scan/slide"},
                                   {"image": "Volumes/scan/slide"}, {"image": "./scan/slide"},
                                   {"image": "slide.vsi"}, {"image": "slide.qptiff"})):
            d = dict(base, **extra)
            g = self.fichero("malo%d.geojson" % i, json.dumps(d))
            r = self.corre("exporta_n1.py", "texto", g, "--nombre", "P-KI67-m%d.geojson" % i)
            self.assertEqual(r.returncode, 3, "%r → %s" % (extra, r.stdout + r.stderr))
            self.assertIn("ruta o nombre de imagen", r.stdout)
        # falso positivo del hallazgo 19: una barra suelta o una lista de clases no son una ruta
        for i, extra in enumerate(({"nota": "ratio a / b"}, {"clases": "Tumor/Stroma/Other"},
                                   {"tinciones": "H/E/DAB"})):
            g = self.fichero("bueno%d.geojson" % i, json.dumps(dict(base, **extra)))
            r = self.corre("exporta_n1.py", "texto", g, "--nombre", "P-KI67-b%d.geojson" % i)
            self.assertEqual(r.returncode, 0, "%r → %s" % (extra, r.stdout + r.stderr))

    def test_fail_closed_tambien_como_libreria(self):
        """Hallazgo 15: sin overlays, las funciones de librería no escriben en N1."""
        otra = os.path.join(self.tmp, "sin-overlays")
        os.makedirs(os.path.join(otra, "tools", "state", "borde"))
        self._json(os.path.join(otra, "tools", "state", "borde", "canarios.json"), [CANARIO])
        env = dict(self.env, BTP_REPO=otra)
        p = self.fichero("ok.csv", "marker,value\nRE,85.2\n")
        v = self.codigo(
            "import exporta_n1 as e, puerta_n1 as pu\nout = []\n"
            "for f in (lambda: e.exporta_texto(%r, 'P-X.csv'), lambda: pu.motivos('Leocadia'),\n"
            "          lambda: pu.revisar_csv('a,b\\n1,2\\n'), lambda: pu.revisar_texto('x'),\n"
            # residuo del 15: la escritura directa, y el cristal aunque el OCR no lea nada (con
            # img=None: la guarda va antes de mirar la imagen)
            "          lambda: e._escribe_n1('P-Y.csv', b'a,b\\n1,2\\n', {'tipo': 'csv'}),\n"
            "          lambda: e.revisar_cristal(None, con_puerta=True)):\n"
            "    try:\n        f()\n        out.append('ABIERTA')\n"
            "    except pu.PuertaCerrada as x:\n        out.append(str(x))\n"
            "print(json.dumps(out))" % p, env=env)
        self.assertEqual(len(v), 6)
        for x in v:
            self.assertIn("puerta sin diccionario", x)
        self.assertFalse(os.path.exists(os.path.join(self.n1, "P-X.csv")))
        self.assertFalse(os.path.exists(os.path.join(self.n1, "P-Y.csv")))


@CON_IMAGEN
class PuertaImagen(Base):

    def test_texto_rasterizado_16px_en_png_aborta(self):
        p = self.fichero("t16.png", b"")
        _texto_con_alto("XK7Q 2B9W", 16).save(p)
        r = self.corre("exporta_n1.py", "png", p, "--opaco", "P-KI67", "--nivel", "x8", "--mpp", "2.0048")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("OCR", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.n1, "P-KI67__x8__mpp2.0048.png")))

    def test_fecha_e_iniciales_rasterizadas_abortan(self):
        """Hallazgo 17: «30.4.26» o «L.Q.T.» no llegan a 4 alfanuméricos seguidos. Fondos elegidos
        donde tesseract SÍ lee el rótulo (medido el 1-oct-26: sobre otros fondos, un «30.4.26»
        de 48 px a veces no lo lee nadie; eso es límite del OCR, no de la puerta)."""
        for i, (texto, semilla) in enumerate((("30.4.26", 4), ("30/04/26", 4), ("L.Q.T.", 5),
                                               ("L.Q.T. 30.4.26", 3))):
            p = self.fichero("f%d.png" % i, b"")
            _textura_con_rotulo(texto, 48, semilla=semilla).save(p)
            r = self.corre("exporta_n1.py", "png", p, "--opaco", "P-F%d" % i, "--nivel", "x8", "--mpp", "2.0")
            self.assertEqual(r.returncode, 3, "%r → %s" % (texto, r.stdout + r.stderr))
            self.assertIn("Puerta", r.stdout)
            self.assertFalse(os.path.exists(os.path.join(self.n1, "P-F%d__x8__mpp2.0000.png" % i)))

    def test_ocr_multiescala_y_psm11(self):
        """Hallazgo 27 (2-oct-26, rehecho: la versión anterior exigía que ESCALAS_PNG leyera algo
        que ×1 ya leía, y aprobaba al mutante ESCALAS_PNG=(1.0,)). Tres hechos, medidos con Pillow
        11.3 y 12.3 y tesseract 5.5: un rótulo de 300 px sobre fondo liso solo se lee REDUCIDO
        (×0,5), uno de 10 px solo AMPLIADO (×2/×4), y seis palabras sueltas sobre textura solo
        las segmenta psm 11. Cada caso comprueba primero que el fixture vale (×1, o psm 3, no lo
        lee) y después que ESCALAS_PNG / PSMS sí: quitar ×0,5, quitar las ampliaciones o quitar
        el psm 11 lo rompe."""
        grande = Image.new("RGB", (1024, 640), (236, 200, 220))
        rot = _texto_con_alto("QZ7K", 300, margen=10)
        grande.paste(rot, ((1024 - rot.width) // 2, (640 - rot.height) // 2))
        pg = self.fichero("grande300.png", b"")
        grande.save(pg)
        pp = self.fichero("peq10.png", b"")
        _texto_con_alto("XK7Q", 10).save(pp)
        ps = self.fichero("sueltas.png", b"")
        _palabras_sueltas().save(ps)
        v = self.codigo(
            "import exporta_n1 as e\nfrom PIL import Image\n"
            "def lee(p, esc):\n    return sorted(set(e.texto_en_imagen(Image.open(p), esc)))\n"
            "def psm3(p):\n    img, out = Image.open(p).convert('RGB'), set()\n"
            "    for s in e.ESCALAS_PNG:\n"
            "        im = img if s == 1.0 else img.resize((int(img.width * s), int(img.height * s)),\n"
            "                                             Image.LANCZOS)\n"
            "        out |= {w for w, c in e._ocr(im, 3) if c >= e.CONF_OCR}\n"
            "    return sorted(out)\n"
            "print(json.dumps({'g1': lee(%r, (1.0,)), 'g': lee(%r, e.ESCALAS_PNG),\n"
            "                  'p1': lee(%r, (1.0,)), 'p': lee(%r, e.ESCALAS_PNG),\n"
            "                  's3': psm3(%r), 's': lee(%r, e.ESCALAS_PNG), 'psms': list(e.PSMS)}))"
            % (pg, pg, pp, pp, ps, ps))
        self.assertNotIn("QZ7K", v["g1"])                    # el fixture: a ×1 no se lee
        self.assertIn("QZ7K", v["g"], v)                     # …y ESCALAS_PNG lo lee (×0,5)
        self.assertNotIn("XK7Q", v["p1"])
        self.assertIn("XK7Q", v["p"], v)                     # ×2 o ×4
        self.assertFalse(set(v["s3"]) & set(PALABRAS_SUELTAS), v["s3"])   # psm 3 no las ve
        self.assertTrue(set(v["s"]) & set(PALABRAS_SUELTAS), v)           # psm 11 sí
        self.assertIn(11, v["psms"])

    def test_png_mayor_de_1024_aborta(self):
        p = self.fichero("grande.png", b"")
        _textura(1100, 600).save(p)
        r = self.corre("exporta_n1.py", "png", p, "--opaco", "P-KI67", "--nivel", "x8", "--mpp", "2.0")
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("1024", r.stdout)

    def test_trazo_de_rotulador_aborta(self):
        img = _textura(800, 500)
        ImageDraw.Draw(img).line([(50, 400), (700, 80)], fill=(20, 160, 60), width=14)
        p = self.fichero("trazo.png", b"")
        img.save(p)
        r = self.corre("exporta_n1.py", "png", p, "--opaco", "P-KI67", "--nivel", "x8", "--mpp", "2.0")
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("trazos", r.stdout)

    def test_trazo_rojo_de_rotulador_aborta(self):
        """2-oct-26: el rotulador ROJO (200,30,40) tiene S 217 en el HSV de PIL; la ventana roja
        (tono ≥245 o ≤10, S≥200, V≥60) lo coge. Antes pasaba («no se distingue de la eosina»)."""
        img = _textura(800, 500)
        ImageDraw.Draw(img).line([(50, 400), (700, 80)], fill=(200, 30, 40), width=14)
        p = self.fichero("rojo.png", b"")
        img.save(p)
        r = self.corre("exporta_n1.py", "png", p, "--opaco", "P-KI67", "--nivel", "x8", "--mpp", "2.0")
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("trazos", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.n1, "P-KI67__x8__mpp2.0000.png")))

    def test_textura_sin_texto_pasa_sin_metadatos(self):
        img = _textura(1000, 700)
        p = self.fichero("tex.png", b"")
        from PIL import PngImagePlugin
        info = PngImagePlugin.PngInfo()
        info.add_text("Author", "Leocadia")
        img.save(p, pnginfo=info)
        r = self.corre("exporta_n1.py", "png", p, "--opaco", "P-KI67", "--nivel", "x8", "--mpp", "2.0048")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = os.path.join(self.n1, "P-KI67__x8__mpp2.0048.png")
        crudo = _lee(out)
        self.assertNotIn(b"Leocadia", crudo)
        self.assertNotIn(b"tEXt", crudo)
        man = json.load(open(os.path.join(self.n1, "manifiesto.json")))
        self.assertEqual(man["ficheros"]["P-KI67__x8__mpp2.0048.png"]["mpp"], 2.0048)


# Corre DENTRO de cada intérprete: dibuja los rótulos con su Pillow y los lee con su exporta_n1.
_SRC_ROTULOS = '''
import json, sys
p = json.loads(sys.argv[1])
sys.path[:0] = p["rutas"]
import PIL
import exporta_n1 as e
import test_laminillas_n1 as tl
dos = sorted(set(e.texto_en_imagen(tl._texto_con_alto("XK7Q 2B9W", 16), e.ESCALAS_PNG)))
fallan = ["%s@%d" % (t, h) for t, h in p["rotulos"]
          if not e.texto_en_imagen(tl._texto_con_alto(t, h), e.ESCALAS_PNG)]
print(json.dumps([PIL.__version__, dos, fallan]))
'''


@CON_IMAGEN
class OcrTextoPequeno(Base):
    """Rótulos pequeños con el intérprete de PRODUCCIÓN (2-oct-26). `exporta_n1` corre en el venv
    de `laminillas_exporta` (patologia, Pillow 12.3) y esta batería en /usr/bin/python3 (Pillow
    11.3). El detector lee igual con las dos versiones; lo que cambia es el dibujo del rótulo (un
    píxel de ancho), y con las escalas (2, 0,5) «2B9W» de 16 px dibujado con Pillow 12 ya no se
    leía. Con (4, 2, 1, 0,5), 66 de 70 rótulos sintéticos de una palabra; los que siguen sin
    leerse («2B9W» a 10 y 18 px) no están aquí: son el límite conocido."""

    ROTULOS = [["XK7Q", 16], ["2B9W", 14], ["2B9W", 16], ["24B0001043", 10], ["Q7RT", 12],
               ["LQTR", 10]]

    def _interpretes(self):
        prod = self.codigo("import laminillas_ventanilla as V\n"
                           "print(json.dumps(V.interprete(V.CONF['laminillas_exporta']['venv'])))")
        if os.path.exists(prod):
            return [PY, prod]
        if not os.environ.get("BTP_PORTABLE"):
            self.fail("falta %s, el intérprete de exporta_n1 en producción: su Pillow no se prueba"
                      % prod)
        return [PY]

    def test_rotulos_pequenos_con_cada_interprete(self):
        param = json.dumps({"rutas": [os.path.join(ROOT, "tests"), self.copia], "rotulos": self.ROTULOS})
        for py in self._interpretes():
            r = subprocess.run([py, "-c", _SRC_ROTULOS, param], capture_output=True, text=True,
                               timeout=600, env=dict(self.env, PYTHONDONTWRITEBYTECODE="1"),
                               cwd=self.tmp, stdin=subprocess.DEVNULL, start_new_session=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            version, dos, fallan = json.loads(r.stdout.strip().splitlines()[-1])
            donde = "%s (Pillow %s)" % (py, version)
            self.assertEqual(dos, ["2B9W", "XK7Q"], donde)
            self.assertEqual(fallan, [], donde)


@CON_IMAGEN
class TiffN1(Base):

    def _lamina(self, nombre, x8=None, extra=(), x4=None, **kw):
        ruta = os.path.join(self.tmp, nombre)
        escribe_tiff(ruta, [(16384, 4096, None), (4096, 1024, x4), (2048, 512, x8)] + list(extra), **kw)
        return ruta

    def _rechaza(self, src, *esperado):
        r = self.corre("exporta_n1.py", "tiff", src, "--opaco", "P-HE")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        for e in esperado:
            self.assertIn(e, r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.n1, "P-HE.n1.tif")))
        return r

    def test_copia_sin_recomprimir_y_solo_tags_blancos(self):
        src = self._lamina("lim.tif")
        r = self.corre("exporta_n1.py", "tiff", src, "--opaco", "P-HE")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        dst = os.path.join(self.n1, "P-HE.n1.tif")
        blancos = {254, 256, 257, 258, 259, 262, 270, 274, 277, 282, 283, 284, 296, 297,
                   322, 323, 324, 325, 530, 532}
        a, b = lee_tiff(src), lee_tiff(dst)
        self.assertEqual(len(a), len(b))
        for (ta, sa), (tb, sb) in zip(a, b):
            self.assertTrue(set(tb) <= blancos, set(tb) - blancos)
            self.assertEqual(sa, sb)                              # teselas byte a byte
        self.assertEqual(b[0][0][270][1], b"polaris-n1\x00")
        self.assertNotIn(b"Leocadia", _lee(dst))
        self.assertNotIn(b"2026:04:30", _lee(dst))
        man = json.load(open(os.path.join(self.n1, "manifiesto.json")))["ficheros"]["P-HE.n1.tif"]
        self.assertEqual(man["tags_quitados"], [305, 306])
        self.assertAlmostEqual(man["mpp"], 0.2506, places=3)

    def test_tesela_con_com_se_rechaza(self):
        self._rechaza(self._lamina("com.tif", com_en_tesela=True), "0xFFFE")

    def test_tesela_con_app1_se_rechaza(self):
        """Hallazgo 23: EXIF/XMP (APP1) es el sitio habitual de metadatos en un JPEG."""
        texto = b"Leocadia Quintanar 31B00024681"
        app1 = b"\xff\xe1" + struct.pack(">H", len(texto) + 2) + texto
        self._rechaza(self._lamina("app1.tif", extra_tesela=app1), "0xFFE1")

    def test_tesela_con_datos_tras_eoi_se_rechaza(self):
        self._rechaza(self._lamina("eoi.tif", tras_eoi=b"Leocadia Quintanar"), "tras EOI")

    # ── Tercera pasada (2-oct-26): la tesela se lee campo a campo, se reescribe canónica y se
    # decodifica en estricto. Todo en la PRIMERA tesela de L0, que el OCR no mira.
    def test_tesela_con_tablas_de_mas_o_segmentos_alargados_se_rechaza(self):
        dqt_libre = b"\xff\xdb" + struct.pack(">H", 67) + b"\x02" + NOMBRE_SINTETICO.ljust(64, b".")
        self._rechaza(self._lamina("dqt2.tif", extra_tesela=dqt_libre), "DQT que no usa")
        dqt_doble = b"\xff\xdb" + struct.pack(">H", 67) + b"\x00" + NOMBRE_SINTETICO.ljust(64, b".")
        self._rechaza(self._lamina("dqt0.tif", extra_tesela=dqt_doble), "definida dos veces")

        def sof_largo(t):
            _m, a, b = next(s for s in _segmentos(t) if s[0] == 0xC0)
            ln = struct.unpack(">H", t[a + 2:a + 4])[0]
            return t[:a + 2] + struct.pack(">H", ln + len(NOMBRE_SINTETICO)) + t[a + 4:b] \
                + NOMBRE_SINTETICO + t[b:]
        self._rechaza(self._lamina("sof.tif", transforma=sof_largo), "SOF0 de longitud")

    def test_tesela_que_no_decodifica_limpia_se_rechaza(self):
        """Texto en los datos de entropía: detrás de los de verdad (bytes sobrantes antes del EOI)
        o en su lugar. La forma de los segmentos es correcta; libjpeg-turbo en estricto, no."""
        def sobrantes(t):
            return t[:-2] + NOMBRE_SINTETICO + t[-2:]

        def sustituida(t):
            fin = _segmentos(t)[-1][2]
            return t[:fin] + (NOMBRE_SINTETICO + b" ") * 4 + b"\xff\xd9"
        for nombre, f, motivo in (("sobra.tif", sobrantes, "extraneous"),
                                  ("entropia.tif", sustituida, "premature end")):
            src = self._lamina(nombre, transforma=f)
            self.assertIn(NOMBRE_SINTETICO, _lee(src))
            self._rechaza(src, "no decodifica limpia", motivo)

    def test_tesela_no_canonica_se_reescribe_y_el_envio_la_acepta(self):
        """Las dos DQT en un solo segmento (JPEG válido, no es lo que escribe libjpeg): la copia
        N1 lleva la tesela en la forma canónica de libjpeg (los bytes de la tesela original de
        Pillow) y la re-validación del envío, que exige copia canónica = fichero, la acepta."""
        rosa = _jpeg_limpio(Image.new("RGB", (512, 512), (236, 200, 220)))

        def junta_dqt(t):
            dqts = [s for s in _segmentos(t) if s[0] == 0xDB]
            cuerpo = b"".join(t[a + 4:b] for _m, a, b in dqts)
            return (t[:dqts[0][1]] + b"\xff\xdb" + struct.pack(">H", len(cuerpo) + 2) + cuerpo
                    + t[dqts[-1][2]:])
        src = self._lamina("junta.tif", transforma=junta_dqt)
        self.assertNotEqual(lee_tiff(src)[0][1][0], rosa)
        r = self.corre("exporta_n1.py", "tiff", src, "--opaco", "P-HE")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        dst = os.path.join(self.n1, "P-HE.n1.tif")
        self.assertEqual(lee_tiff(dst)[0][1][0], rosa)
        v = self.codigo("import exporta_n1 as e, hashlib\n"
                        "b = e.revalidar_n1(%r, hashlib.sha256(open(%r, 'rb').read()).hexdigest())\n"
                        "print(json.dumps(len(b)))" % (dst, dst))
        self.assertEqual(v, len(_lee(dst)))

    def test_x4_rotulado_con_x8_limpio_aborta(self):
        """Tercera pasada: el ×4 pasa entero por el OCR y, reducido, tiene que parecerse al ×8. Un
        rótulo solo en el ×4 salta por las dos vías; una mancha sin texto, por la coherencia."""
        x4 = Image.new("RGB", (4096, 1024), (236, 200, 220))
        x4.paste(_texto_con_alto("QUINTANAR 24B0001043", 300, margen=20), (100, 300))
        r = self._rechaza(self._lamina("x4.tif", x4=x4), "1-bis", "IFD 1 (×4): OCR",
                          "IFD 1: reducido no se parece al ×8")
        self.assertNotIn("QUINTANAR", r.stdout)
        x4 = Image.new("RGB", (4096, 1024), (236, 200, 220))
        ImageDraw.Draw(x4).rectangle((1000, 200, 1600, 800), fill=(90, 40, 130))
        r = self._rechaza(self._lamina("x4b.tif", x4=x4), "1-bis", "IFD 1: reducido no se parece")
        self.assertNotIn("OCR", r.stdout)

    def test_piramide_coherente_con_textura_exporta_limpia(self):
        """Sin falsos positivos de la coherencia: L0 con textura y ×4/×8 sacados de L0 (medido el
        2-oct-26 con texturas suaves y de bordes duros y cuatro filtros: diferencia ≤7, umbral 24)."""
        src = os.path.join(self.tmp, "coherente.tif")
        escribe_tiff(src, _piramide_coherente(_textura(4096, 1024, 5)))
        r = self.corre("exporta_n1.py", "tiff", src, "--opaco", "P-HE")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        man = json.load(open(os.path.join(self.n1, "manifiesto.json")))["ficheros"]["P-HE.n1.tif"]
        self.assertEqual(man["cristal"], "limpio")

    def test_limite_declarado_microtexto_en_l0(self):
        """LÍMITE DECLARADO (exporta_n1, cabecera): L0 no pasa por el OCR (una lámina entera son
        decenas de miles de teselas). Un rótulo de 24 px en L0, que el OCR de L0 sí lee, es 6 px
        en el ×4 y 3 en el ×8: ni el OCR lo lee ahí ni mueve la media de una ventana de la
        coherencia (medido el 2-oct-26: 12-24 px, ningún motivo). Sale. Los más grandes que
        también salen (hasta 80 px de L0 en todos los niveles; 100 solo en L0) y dónde se cierran:
        `RotuloEnUnNivel.test_limite_declarado_rotulo_de_un_solo_nivel`."""
        niveles = _piramide_coherente(_textura(4096, 1024, 5), "LEOCADIA QUINTANAR", 24)
        src = os.path.join(self.tmp, "micro.tif")
        escribe_tiff(src, niveles)
        lee = self.codigo("import exporta_n1 as e\nt = e.Tiff(%r)\n"
                          "img = e._mosaico(t, 0, 1).crop((600, 250, 1300, 420))\n"
                          "print(json.dumps(sorted(set(e.texto_en_imagen(img, (2.0, 1.0))))))" % src)
        self.assertTrue(lee, "el fixture no vale: el rótulo de L0 no se lee ni a ×2")
        r = self.corre("exporta_n1.py", "tiff", src, "--opaco", "P-HE")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)            # el límite: sale

    def test_limite_declarado_valores_de_una_dqt_usada(self):
        """LÍMITE DECLARADO (exporta_n1, cabecera): los 63 valores de AC de una DQT que SÍ se usa
        son libres (una tesela lisa no tiene AC: no cambian ni un píxel). Exigir las tablas de IJG
        rompería una lámina con tablas propias del escáner. El texto llega a la copia N1."""
        def dqt_con_texto(t):
            _m, a, b = next(s for s in _segmentos(t) if s[0] == 0xDB)
            return t[:a + 6] + NOMBRE_SINTETICO.ljust(63, b".") + t[b:]
        src = self._lamina("dqt-usada.tif", transforma=dqt_con_texto)
        r = self.corre("exporta_n1.py", "tiff", src, "--opaco", "P-HE")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(NOMBRE_SINTETICO, _lee(os.path.join(self.n1, "P-HE.n1.tif")))

    def test_tag_blanco_con_tipo_o_count_fuera_del_formato(self):
        """Hallazgos 20 y 23: un tag de la lista con tipo ASCII, o tipo BYTE con 30 valores, es
        texto en claro dentro del TIFF N1."""
        self._rechaza(self._lamina("t274.tif", tags_l0={274: (2, b"Leocadia\x00")}), "tipo 2 no permitido")
        self._rechaza(self._lamina("t297.tif", tags_l0={297: (1, b"Leocadia Quintanar 24B0001043")}),
                      "tag 297")
        self._rechaza(self._lamina("t297n.tif", tags_l0={297: (3, [0, 1, 2, 3])}), "tag 297")
        # Desde el 2-oct-26 el 297 de origen va por lista blanca (GrundiumReal): en L0 de 3 niveles,
        # (0, 2) del Grundium o (0, 3) canónico; (0, 1) ya no.
        self._rechaza(self._lamina("t297u.tif", tags_l0={297: (3, [0, 1])}), "tag 297 (PageNumber)")
        r = self.corre("exporta_n1.py", "tiff", self._lamina("t297ok.tif", tags_l0={297: (3, [0, 2])}),
                       "--opaco", "P-HE")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_valores_de_tags_blancos_sin_texto(self):
        """Hallazgo 20 bis (2-oct-26): con tipo y count correctos, los VALORES de 282/283 (8 bytes
        cada uno), 297 (4) y 532 (48) llevaban texto a N1. Ahora 297 y 532 se acotan, y 282/283
        se reescriben a su valor canónico: lo que salga ya no son los bytes de entrada."""
        leo = list(struct.unpack("<II", b"Leocadia"))
        self._rechaza(self._lamina("r282.tif", tags_l0={282: (5, leo), 283: (5, leo)}), "resolución")
        self._rechaza(self._lamina("r283.tif", tags_l0={283: (5, [39905, 1])}), "XResolution")
        self._rechaza(self._lamina("p297.tif", tags_l0={297: (3, list(struct.unpack("<HH", b"24B0")))}),
                      "tag 297")
        rbw = list(struct.unpack("<" + "I" * 12,
                                 b"Leocadia Quintanar 24B0001043 {{CENTRO}} 30".ljust(48, b".")))
        self._rechaza(self._lamina("r532.tif", tags_l0={532: (5, rbw)}), "tag 532")
        # Resolución plausible con «basura» en las cifras bajas: sale, pero canónica.
        crudo = struct.pack("<II", 3990412345, 100000)
        src = self._lamina("r-ok.tif", tags_l0={282: (5, [3990412345, 100000]), 283: (5, [3990412345, 100000])})
        self.assertIn(crudo, _lee(src))
        r = self.corre("exporta_n1.py", "tiff", src, "--opaco", "P-HE")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        dst = os.path.join(self.n1, "P-HE.n1.tif")
        self.assertNotIn(crudo, _lee(dst))
        tags = lee_tiff(dst)[0][0]
        self.assertEqual(struct.unpack("<II", tags[282][1]), (399041, 10))      # «%.6g» de 39904,12345
        self.assertEqual(tags[282][1], tags[283][1])
        man = json.load(open(os.path.join(self.n1, "manifiesto.json")))["ficheros"]["P-HE.n1.tif"]
        self.assertAlmostEqual(man["mpp"], 0.2506, places=3)

    def test_ifd_que_no_es_piramide_aborta(self):
        """Hallazgo 16: una etiqueta teselada (1024x512, otra proporción) no es un nivel."""
        rotulo = Image.new("RGB", (1024, 512), (236, 200, 220))
        rotulo.paste(_texto_con_alto("QUINTANAR 24B0001043", 60, margen=10), (20, 200))
        self._rechaza(self._lamina("etiqueta.tif", extra=[(1024, 512, rotulo)]), "pirámide")

    def test_nivel_menor_que_x8_con_texto_aborta(self):
        """Hallazgo 16: el OCR mira también los niveles menores que el ×8 (aquí el ×16)."""
        x16 = Image.new("RGB", (1024, 256), (236, 200, 220))
        x16.paste(_texto_con_alto("XK7Q", 150, margen=10), (100, 30))
        self._rechaza(self._lamina("x16.tif", extra=[(1024, 256, x16)]), "1-bis", "IFD 3")

    def test_texto_400px_en_el_cristal_x8_aborta(self):
        x8 = Image.new("RGB", (2048, 512), (236, 200, 220))
        txt = _texto_con_alto("XK7Q", 400, margen=10)
        x8.paste(txt, (100, (512 - txt.height) // 2))
        src = self._lamina("t400.tif", x8=x8)
        r = self.corre("exporta_n1.py", "tiff", src, "--opaco", "P-HE")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("1-bis", r.stdout)
        # Con «revisado» tampoco: el rótulo está SOLO en el ×8 y la coherencia salta en más ventanas
        # de las que una hoja puede enseñar (2-oct-26): ni se pregunta ni se sella nada.
        r = self.corre("exporta_n1.py", "tiff", src, "--opaco", "P-HE", "--revisado-en-tty",
                       "--hoja", self._huella(src))
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("demasiadas para revisarlas a mano", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.n1, "P-HE.n1.tif")))
        self.assertFalse(self.eventos("n1_revision_rechazada") + self.eventos("n1_revision_humana"))

    def _rotulada(self, nombre):
        """Pirámide coherente con un rótulo de 320 px en L0 (40 en el ×8, que el OCR lee; «XK7Q» no
        lo lee ahí, medido el 2-oct-26): motivo del paso 1-bis por el OCR y ninguna ventana."""
        src = os.path.join(self.tmp, nombre)
        escribe_tiff(src, _piramide_coherente(_textura(4096, 1024, 5), "QZ7K", 320))
        return src

    def _huella(self, src, otra=False):
        """La huella del paso 1-bis de `src` como la calcula exporta_n1 al exportar (la de la hoja
        la fija test_laminillas_1bis); `otra`: la de una hoja que enseñara una ventana más."""
        return self.codigo(
            "import exporta_n1 as e\nt = e.Tiff(%r)\nm, v = e._cristal_tiff_detalle(t)\n"
            "v = v + [[0, 0, 0, 16, 16, 99]] if %r else v\n"
            "print(json.dumps(e.huella_1bis(e._sha256_fichero(e.__file__), e._sha256_fichero(%r), m, v)))"
            % (src, otra, src))

    def test_revisado_en_tty_sin_terminal_deja_rastro(self):
        """«revisado» sin terminal no vale: la palabra la teclea {{TITULAR}}. Con la huella de su hoja,
        llega a borde, que lo rechaza y deja rastro en la cadena (desde el 2-oct-26)."""
        src = self._rotulada("rot.tif")
        r = self.corre("exporta_n1.py", "tiff", src, "--opaco", "P-HE", "--revisado-en-tty",
                       "--hoja", self._huella(src))
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("revisión 1-bis BLOQUEADA", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.n1, "P-HE.n1.tif")))
        rech = self.eventos("n1_revision_rechazada")
        self.assertEqual([(e["opaco"], e["nivel"]) for e in rech], [("P-HE", "alarma")], self.eventos())
        self.assertFalse(self.eventos("n1_revision_humana"))

    def test_revisado_en_tty_sin_la_huella_de_su_hoja_no_pregunta(self):
        """Deuda puerta-n1-1bis-no-ensena-lo-de-l0 (2-oct-26): VISTO-N1 confirma solo lo que enseñó
        la hoja. Sin `--hoja`, con la de otra hoja (una ventana de más) o con una huella mal formada,
        exporta_n1 se para ANTES de preguntar: en la terminal no sale la pregunta, no se sella nada
        (ni siquiera el rechazo) y no hay copia N1."""
        src = self._rotulada("sinhoja.tif")
        for extra, motivo in (([], "huella de su hoja"), (["--hoja", self._huella(src, otra=True)], "no enseña"),
                              (["--hoja", "XYZ"], "16 cifras")):
            rc, out = con_tty_huerfano([PY, os.path.join(self.copia, "exporta_n1.py"), "tiff", src, "--opaco",
                                        "P-HE", "--revisado-en-tty"] + extra, self.env, self.tmp, "VISTO-N1",
                                       b"VISTO-N1 para confirmar")
            self.assertEqual(rc, 3, out)
            self.assertIn(motivo, out)
            self.assertNotIn("Escribe VISTO-N1", out)
        self.assertFalse(os.path.exists(os.path.join(self.n1, "P-HE.n1.tif")))
        self.assertFalse(self.eventos("n1_revision_rechazada") + self.eventos("n1_revision_humana"))

    def test_limite_declarado_huella_calculable_sin_hoja(self):
        """LÍMITE DECLARADO (exporta_n1, cabecera): la huella del paso 1-bis no es secreta ni una
        firma; ata la hoja a lo que se exporta. Quien la calcula a propósito con `huella_1bis`, sin
        haber generado ninguna hoja, llega a la pregunta; VISTO-N1 sigue haciendo falta, tecleado en
        la terminal de {{TITULAR}} (borde.revisar_cristal_en_tty). Aquí, con el pty de los tests: sale."""
        src = self._rotulada("limite.tif")
        rc, out = con_tty_huerfano([PY, os.path.join(self.copia, "exporta_n1.py"), "tiff", src, "--opaco",
                                    "P-HE", "--revisado-en-tty", "--hoja", self._huella(src)], self.env,
                                   self.tmp, "VISTO-N1", b"VISTO-N1 para confirmar")
        self.assertEqual(rc, 0, out)                                             # el límite: sale
        self.assertIn("Escribe VISTO-N1", out)

    def test_revisado_en_tty_con_visto_exporta_y_sella_via_tty(self):
        """El paso 1-bis de verdad: {{TITULAR}} en su terminal (aquí, un pty huérfano) teclea VISTO-N1.
        La revisión la sella borde con `via: 'tty'` y el sha256 de la copia N1, y es la que la
        re-validación del envío acepta. Con f3c2a2f se sellaba sin `via` desde exporta_n1."""
        src = self._rotulada("t400v.tif")
        rc, out = con_tty_huerfano([PY, os.path.join(self.copia, "exporta_n1.py"), "tiff", src, "--opaco",
                                    "P-HE", "--revisado-en-tty", "--hoja", self._huella(src)], self.env,
                                   self.tmp, "VISTO-N1", b"VISTO-N1 para confirmar")
        self.assertEqual(rc, 0, out)
        dst = os.path.join(self.n1, "P-HE.n1.tif")
        sha = hashlib.sha256(_lee(dst)).hexdigest()
        rev = self.eventos("n1_revision_humana")
        self.assertEqual([(e["via"], e["sha256"], e["opaco"]) for e in rev], [("tty", sha, "P-HE")])
        self.assertFalse(self.eventos("sello_rechazado") + self.eventos("sello_fuera_de_trust_cloud"))
        self.assertIs(self.codigo("import puerta_n1 as pu\nprint(json.dumps(pu.revision_humana(%r)))" % sha), True)
        man = json.load(open(os.path.join(self.n1, "manifiesto.json")))["ficheros"]["P-HE.n1.tif"]
        self.assertEqual(man["cristal"], "revisado-en-tty")


VENV_PAT = os.path.expanduser("~/.polaris-venvs/patologia/bin/python")
_OPENSLIDE_X8 = r'''
import sys, openslide
s = openslide.OpenSlide(sys.argv[1])
k = s.level_count - 1
s.read_region((0, 0), k, s.level_dimensions[k]).convert("RGB").save(sys.argv[2])
'''


def _ids_rgb(t):
    """La tesela con los ids de componente del SOF0 y del SOS cambiados de (1,2,3) a «R», «G», «B»
    (los datos siguen siendo YCbCr: solo cambia cómo los interpreta libjpeg)."""
    t = bytearray(t)
    i = t.index(b"\xff\xc0")
    assert (t[i + 10], t[i + 13], t[i + 16]) == (1, 2, 3)
    t[i + 10], t[i + 13], t[i + 16] = 82, 71, 66
    j = t.index(b"\xff\xda")
    t[j + 5], t[j + 7], t[j + 9] = 82, 71, 66
    return bytes(t)


def _piramide_croma(dcb, dcr, w=8192, h=2048, alto_x8=70):
    """[(ancho, alto, imagen YCbCr)]: textura y un rótulo SINTÉTICO de `alto_x8` px de alto en el
    ×8 pintado SOLO en el croma (Cb + `dcb`, Cr + `dcr`; la luma Y, la del fondo), en L0 y con ×4 y
    ×8 sacados de L0: una pirámide coherente. Devuelve también la caja del rótulo en el ×8."""
    from PIL import ImageChops
    base = _textura(w, h, 5).convert("YCbCr")
    y, cb, cr = base.split()
    m = Image.new("L", (w, h), 0)
    t = _texto_con_alto(NOMBRE_SINTETICO.decode(), alto_x8 * 8, color=(255, 255, 255), fondo=(0, 0, 0),
                        margen=0).convert("L")
    m.paste(t, (400, (h - t.height) // 2))

    def mueve(canal, d):
        if not d:
            return canal
        otro = Image.new("L", (w, h), abs(d))
        return Image.composite(ImageChops.add(canal, otro) if d > 0 else ImageChops.subtract(canal, otro), canal, m)
    l0 = Image.merge("YCbCr", (y, mueve(cb, dcb), mueve(cr, dcr)))
    caja8 = tuple(v // 8 for v in m.getbbox())
    return [(w, h, l0), (w // 4, h // 4, l0.resize((w // 4, h // 4), Image.LANCZOS)),
            (w // 8, h // 8, l0.resize((w // 8, h // 8), Image.LANCZOS))], caja8


def _vista_por_262(src, k=2):
    """El nivel `k` como lo ve un lector que manda por el 262 = 2 (libtiff, OpenSlide, tifffile):
    el JPEG sin conversión de color (YCbCr crudo tomado por RGB). Pillow con `draft("YCbCr")`."""
    tags, teselas = lee_tiff(src)[k]
    w, h = (struct.unpack("<H", tags[t][1][:2])[0] for t in (256, 257))
    tw = struct.unpack("<H", tags[322][1][:2])[0]
    porfila = -(-w // tw)
    lienzo = Image.new("RGB", (porfila * tw, -(-h // tw) * tw))
    for i, b in enumerate(teselas):
        im = Image.open(io.BytesIO(b))
        im.draft("YCbCr", im.size)
        lienzo.paste(Image.frombytes("RGB", im.size, im.tobytes()), ((i % porfila) * tw, (i // porfila) * tw))
    return lienzo.crop((0, 0, w, h))


@CON_IMAGEN
class Fotometrica(Base):
    """Verificador independiente, 2.ª pasada (2-oct-26): el 262 (fotométrica) de la copia es el de
    origen, y la Puerta decodifica cada tesela con libjpeg, que decide el espacio de color por los
    ids del SOF0; libtiff, OpenSlide y tifffile mandan por el 262. Si no casan, ven otra imagen."""

    def _tiff(self, nombre, niveles, fot, rgb=False):
        src = os.path.join(self.tmp, nombre)
        tags = {0: {297: (3, [0, 2]), 262: (3, [fot])}, 1: {297: (3, [2, 2]), 262: (3, [fot])},
                2: {297: (3, [3, 2]), 262: (3, [fot])}}
        escribe_tiff(src, niveles, tags_k=tags, cada_tesela=_ids_rgb if rgb else None)
        return src

    def _ocr(self, src, png=None):
        """[lo lee la Puerta en su vista del ×8, lo lee en `png`] (sí/no; nunca el texto)."""
        return self.codigo(
            "import exporta_n1 as e\nfrom PIL import Image\nt = e.Tiff(%r)\n"
            "p = bool(e.texto_en_imagen(e._imagen_de_nivel(t, 2), e.ESCALAS_X8))\n"
            "o = bool(e.texto_en_imagen(Image.open(%r).convert('RGB'), e.ESCALAS_X8)) if %r else None\n"
            "print(json.dumps([p, o]))" % (src, png or "", bool(png)))

    def test_fotometrica_que_no_casa_con_los_ids_no_sale(self):
        """Teselas YCbCr (ids 1,2,3) con un rótulo solo en el croma y 262 = 2: la Puerta no lo lee
        (lo ve con la luma del fondo) y un lector que manda por el 262 sí; antes salía a N1 con rc
        0 (medido, y aquí el mutante). Ahora: rc 3 sin paso 1-bis, y la verificación del envío lo
        cierra si alguien lo cuela en N1. Al revés (262 = 6 con ids «RGB»), igual."""
        niveles, _caja = _piramide_croma(60, 0)
        src = self._tiff("f262-2.tif", niveles, 2)
        lector = os.path.join(self.tmp, "lector.png")
        _vista_por_262(src).save(lector)
        self.assertEqual(self._ocr(src, lector), [False, True])          # la premisa
        if os.path.exists(VENV_PAT):                                      # y con OpenSlide de verdad
            os_png = os.path.join(self.tmp, "openslide.png")
            r = subprocess.run([VENV_PAT, "-c", _OPENSLIDE_X8, src, os_png], capture_output=True, text=True,
                               timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            self.assertEqual(self._ocr(src, os_png), [False, True])
        r = self.corre("exporta_n1.py", "tiff", src, "--opaco", "P-HE")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("fotométrica (262) 2", r.stdout)
        self.assertNotIn("1-bis", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.n1, "P-HE.n1.tif")))
        mutante = self.codigo(
            "import exporta_n1 as e\ne._IDS_POR_FOTOMETRICA = {2: e._IDS_COMPONENTES, 6: e._IDS_COMPONENTES}\n"
            "d, ent = e.exporta_tiff(%r, 'P-MU')\nprint(json.dumps(ent['cristal']))" % src)
        self.assertEqual(mutante, "limpio")                                 # sin la regla, salía
        colado = self.colar_en_n1("P-CO.n1.tif", _lee(src))
        v = self.codigo("import exporta_n1 as e, hashlib\nb = open(%r, 'rb').read()\n"
                        "try:\n    e.revalidar_n1(%r, hashlib.sha256(b).hexdigest()); print(json.dumps('acepta'))\n"
                        "except e.PuertaCerrada as x:\n    print(json.dumps(str(x)))" % (colado, colado))
        self.assertIn("fotométrica (262) 2", v)
        src6 = self._tiff("f262-6-rgb.tif", niveles, 6, rgb=True)
        r = self.corre("exporta_n1.py", "tiff", src6, "--opaco", "P-RG")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("ids de componente [82, 71, 66] y fotométrica (262) 6", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.n1, "P-RG.n1.tif")))

    def test_sin_262_no_sale(self):
        """Sin 262, cada lector supone una cosa: no se exporta."""
        src = self._tiff("sin262.tif", _piramide_croma(0, 0)[0], 6)
        datos = bytearray(_lee(src))                     # el 262 del IFD 1 pasa a ser el tag 263
        off = struct.unpack("<Q", datos[8:16])[0]
        n = struct.unpack("<Q", datos[off:off + 8])[0]
        off = struct.unpack("<Q", datos[off + 8 + 20 * n:off + 16 + 20 * n])[0]       # IFD 1
        n = struct.unpack("<Q", datos[off:off + 8])[0]
        entradas = [off + 8 + 20 * i for i in range(n)]
        e262 = [p for p in entradas if struct.unpack("<H", datos[p:p + 2])[0] == 262]
        self.assertEqual(len(e262), 1)
        datos[e262[0]:e262[0] + 2] = struct.pack("<H", 263)          # (263 sigue ordenado: no hay 263)
        with open(src, "wb") as fh:
            fh.write(bytes(datos))
        self.assertNotIn(262, lee_tiff(src)[1][0])
        r = self.corre("exporta_n1.py", "tiff", src, "--opaco", "P-HE")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("sin fotométrica (262)", r.stdout)

    def test_limite_declarado_rotulo_solo_en_el_croma(self):
        """LÍMITE DECLARADO (exporta_n1, cabecera): con el 262 casando, la Puerta y los lectores ven
        la MISMA imagen, pero un rótulo pintado solo en el croma (la luma Y, la del fondo) es casi
        invisible para el OCR, que lee en luma. Medido el 2-oct-26 en sintético (rótulo de 70 px en
        el ×8, en L0 y en toda la pirámide): salen Cb +60, Cb +90, Cb −60 y Cr +90; los cierra el
        OCR del ×4 en Cb +120 y Cr +60 y los trazos en Cr −60. Un rótulo real (tinta, rotulador)
        cambia la luma; esto hay que fabricarlo. Una persona o un modelo de visión sí lo ven (en el
        ×8 de la Puerta, Cb +60 es (177, 111, 251) sobre (177, 129, 182)). Aquí: Cb +60 sale y Cr
        −60 se cierra (si una mejora del OCR cierra el primero, actualiza la cabecera y este test)."""
        niveles, _caja = _piramide_croma(60, 0)
        src = self._tiff("croma60.tif", niveles, 6)
        lector = os.path.join(self.tmp, "lector6.png")
        self.codigo("import exporta_n1 as e\nt = e.Tiff(%r)\ne._imagen_de_nivel(t, 2).save(%r)\nprint(1)"
                    % (src, lector))
        self.assertEqual(self._ocr(src, lector), [False, False])
        r = self.corre("exporta_n1.py", "tiff", src, "--opaco", "P-HE")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)                 # el límite: sale
        niveles, _caja = _piramide_croma(0, -60)
        r = self.corre("exporta_n1.py", "tiff", self._tiff("cromacr.tif", niveles, 6), "--opaco", "P-CR")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("trazos de rotulador", r.stdout)


VIDRIO = (229, 230, 229)


def _piramide_con_relleno(borde=2950, tejido=2000, vidrio8=VIDRIO, w=4096, h=1024, en_l0=None, en_x4=None):
    """[(ancho, alto, imagen)] con la forma que midió `qc coherencia` en las 15 láminas reales
    (2-oct-26): tejido, vidrio y, desde `borde`, la zona NO escaneada en blanco 255 en L0 y en el ×4
    (sacado de L0) y con color de vidrio (`vidrio8`) en el ×8. `en_l0(img)` / `en_x4(img)` pintan
    algo SOLO en ese nivel (después de sacar los demás)."""
    base = _textura(w, h, 5)
    ImageDraw.Draw(base).rectangle((tejido, 0, w, h), fill=VIDRIO)
    ImageDraw.Draw(base).rectangle((borde, 0, w, h), fill=(255, 255, 255))
    x8 = base.resize((w // 8, h // 8), Image.LANCZOS)
    ImageDraw.Draw(x8).rectangle((borde // 8, 0, w, h), fill=vidrio8)
    x4 = base.resize((w // 4, h // 4), Image.LANCZOS)
    for f, img in ((en_l0, base), (en_x4, x4)):
        if f:
            f(img)
    return [(w, h, base), (w // 4, h // 4, x4), (w // 8, h // 8, x8)]


@CON_IMAGEN
class GrundiumReal(Base):
    """La prueba REAL de la Puerta (2-oct-26, por la ventanilla, solo números) no sacó ni un TIFF:
    tres cosas del formato del Grundium la cerraban en las 15 láminas sin una palabra leída. Aquí,
    cada una en sintético, y que la Puerta no se relaja: lo pintado en un solo nivel sigue saltando."""

    GRUNDIUM_3 = {0: {297: (3, [0, 2])}, 1: {297: (3, [2, 2])}, 2: {297: (3, [3, 2])}}
    LAMINA = [(16384, 4096, None), (4096, 1024, None), (2048, 512, None)]

    def _tiff(self, nombre, niveles, **kw):
        src = os.path.join(self.tmp, nombre)
        escribe_tiff(src, niveles, **kw)
        return src

    def _exporta(self, src, opaco="P-HE"):
        return self.corre("exporta_n1.py", "tiff", src, "--opaco", opaco)

    def test_297_del_grundium_se_reescribe_canonico(self):
        """Tag 297: el Grundium escribe (log2 del factor, nº de IFDs − 1) —(0,2), (2,2), (3,2); en
        B-HE-2 (0,3), (2,3), (3,3), (4,3)— y la Puerta abortaba en el IFD 1 (página ≥ total). Sale,
        y la copia N1 lleva (k, nº de IFDs), derivado de la estructura, nunca el valor de origen; la
        verificación del envío (copia canónica = fichero) la acepta. Cualquier otra forma aborta."""
        g4 = {0: {297: (3, [0, 3])}, 1: {297: (3, [2, 3])}, 2: {297: (3, [3, 3])}, 3: {297: (3, [4, 3])}}
        for nombre, niveles, tags, canonico in (
                ("g3.tif", self.LAMINA, self.GRUNDIUM_3, [(0, 3), (1, 3), (2, 3)]),
                ("g4.tif", self.LAMINA + [(1024, 256, None)], g4, [(0, 4), (1, 4), (2, 4), (3, 4)])):
            shutil.rmtree(self.n1, ignore_errors=True)
            r = self._exporta(self._tiff(nombre, niveles, tags_k=tags))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            dst = os.path.join(self.n1, "P-HE.n1.tif")
            self.assertEqual([struct.unpack("<HH", tg[297][1]) for tg, _t in lee_tiff(dst)], canonico)
            v = self.codigo("import exporta_n1 as e, hashlib\n"
                            "b = e.revalidar_n1(%r, hashlib.sha256(open(%r, 'rb').read()).hexdigest())\n"
                            "print(json.dumps(len(b)))" % (dst, dst))
            self.assertEqual(v, len(_lee(dst)))
        for nombre, tags in (("p0.tif", {0: {297: (3, [1, 2])}}),       # L0 es la página 0
                             ("p1.tif", {1: {297: (3, [1, 2])}}),       # el ×4: log2 4 = 2
                             ("p2.tif", {2: {297: (3, [2, 2])}}),       # ni (3, 2) ni (2, 3)
                             ("p3.tif", {1: {297: (3, [2, 3])}})):      # total de otra lámina
            r = self._exporta(self._tiff(nombre, self.LAMINA, tags_k=tags), "P-RE")
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
            self.assertIn("tag 297 (PageNumber)", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.n1, "P-RE.n1.tif")))

    def test_relleno_blanco_no_salta(self):
        """Coherencia: saltaba en las 15 (29-245) y todas las ventanas que pasaban caían en la zona
        no escaneada (L0 y ×4 en 255 exacto; el ×8, color de vidrio). El fixture lo reproduce (con
        la cuenta de antes salta: se comprueba aquí) y sale limpio."""
        src = self._tiff("relleno.tif", _piramide_con_relleno(), tags_k=self.GRUNDIUM_3)
        nuevo, viejo, blanco = self.codigo(
            "import exporta_n1 as e\nt = e.Tiff(%r)\nimg8 = e._imagen_de_nivel(t, 2)\n"
            "nuevo = [e._coherencia_nivel(t, k, den, img8) for k, den in ((0, 8), (1, 2))]\n"
            "e.FRACCION_RELLENO = 2\n"
            "viejo = [e._coherencia_nivel(t, k, den, img8) for k, den in ((0, 8), (1, 2))]\n"
            "print(json.dumps([nuevo, viejo, e._mosaico(t, 0, 8).crop((400, 0, 512, 128)).getextrema()]))"
            % src)
        self.assertEqual(blanco, [[255, 255]] * 3)                     # el relleno, en 255 exacto
        self.assertTrue(all(d > 24 for d in viejo), viejo)                # la cuenta de antes salta
        self.assertTrue(all(d <= 24 for d in nuevo), nuevo)
        r = self._exporta(src)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        man = json.loads(_lee(os.path.join(self.n1, "manifiesto.json"), "r"))["ficheros"]["P-HE.n1.tif"]
        self.assertEqual(man["cristal"], "limpio")

    def test_lo_pintado_en_un_nivel_sigue_saltando(self):
        """Sin relajarla: un rótulo solo en el ×4, dentro del tejido, salta por la coherencia y por
        el OCR; una mancha solo en el ×4 y un rótulo solo en L0, los dos sobre el relleno, saltan
        por la coherencia (el blanco del relleno ya no cuenta, y tampoco tapa lo pintado encima)."""
        def rotulo_x4(img):
            img.paste(_texto_con_alto("QUINTANAR 24B0001043", 40, margen=6), (60, 100))

        def mancha_x4(img):                                  # relleno del ×4: x ≥ 2950/4
            ImageDraw.Draw(img).rectangle((800, 60, 860, 90), fill=(90, 40, 130))

        def rotulo_l0(img):                                  # relleno de L0: x ≥ 2950
            img.paste(_texto_con_alto("QUINTANAR", 160, color=(40, 30, 60), fondo=(255, 255, 255),
                                      margen=10), (3000, 300))
        for nombre, kw, motivos in (
                ("x4t.tif", {"en_x4": rotulo_x4}, ("IFD 1: reducido no se parece", "IFD 1 (×4): OCR")),
                ("x4m.tif", {"en_x4": mancha_x4}, ("IFD 1: reducido no se parece",)),
                ("l0t.tif", {"en_l0": rotulo_l0}, ("IFD 0: reducido no se parece",))):
            r = self._exporta(self._tiff(nombre, _piramide_con_relleno(**kw), tags_k=self.GRUNDIUM_3))
            self.assertEqual(r.returncode, 3, nombre + r.stdout + r.stderr)
            self.assertIn("1-bis", r.stdout, nombre)
            for m in motivos:
                self.assertIn(m, r.stdout, nombre)
            self.assertNotIn("QUINTANAR", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.n1, "P-HE.n1.tif")))

    def test_limite_declarado_blanco_en_nivel_fino(self):
        """LÍMITE DECLARADO (exporta_n1, cabecera y RELLENO): una forma en blanco puro pintada solo
        en el ×4, sobre el vidrio, es para la coherencia lo mismo que el relleno no escaneado y no
        salta (antes, 255 frente a vidrio ~229 era justo el falso positivo del relleno). No es
        texto: el OCR del ×4 no tiene nada que leer. Sale."""
        def blanco_x4(img):                                  # vidrio del ×4: 2000/4 ≤ x < 2950/4
            ImageDraw.Draw(img).rectangle((560, 80, 700, 180), fill=(255, 255, 255))
        r = self._exporta(self._tiff("blanco.tif", _piramide_con_relleno(en_x4=blanco_x4),
                                     tags_k=self.GRUNDIUM_3))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)          # el límite: sale

    def test_x4_por_teselas_como_entero(self):
        """El ×4 del hueso (478-557 Mpx) hacía saltar el tope de Pillow. Su OCR y la coherencia van
        ahora por teselas con el MISMO resultado que sobre el nivel entero: con trozos de 700 px
        (solape 140), bloques de 300 px y bandas de 48 filas, para que salgan muchos, cada trozo a
        ×1, ×0,5 y ×0,25 es el del nivel entero (±1 a ×0,25: el lado no es múltiplo de 4 y el
        redondeo en coma flotante cambia algún peso), y la coherencia por bandas, la entera."""
        src = self._tiff("teselas.tif", [(16400, 4120, None), (4100, 1030, _textura(4100, 1030, 9)),
                                         (2050, 515, None)])
        trozos, coh = self.codigo(
            "import exporta_n1 as e\nfrom PIL import Image, ImageChops\nt = e.Tiff(%r)\n"
            "e.LADO_TROZO, e.SOLAPE_TROZO, e.LADO_FUENTE, e.FILAS_BANDA = 700, 140, 300, 48\n"
            "entero, out = e._imagen_de_nivel(t, 1), []\n"
            "for esc in e.ESCALAS_X8:\n"
            "    im = entero if esc == 1.0 else entero.resize(e._tam_escalado(*entero.size, esc), Image.LANCZOS)\n"
            "    cajas = e._cajas_trozos(*im.size)\n"
            "    out.append([len(cajas), max(max(hi for _lo, hi in ImageChops.difference(\n"
            "        e._trozo_de_nivel(t, 1, esc, c), tr).getextrema()) for c, tr in zip(cajas, e._trozos(im)))])\n"
            "img8 = e._imagen_de_nivel(t, 2)\n"
            "coh = [[e._coherencia_nivel(t, k, den, img8), e.incoherencia(e._mosaico(t, k, den), img8)]\n"
            "       for k, den in ((0, 8), (1, 2))]\n"
            "print(json.dumps([out, coh]))" % src)
        self.assertEqual([n for n, _d in trozos], [16, 4, 2])
        self.assertEqual([d for _n, d in trozos[:2]], [0, 0])
        self.assertLessEqual(trozos[2][1], 1)
        self.assertGreater(coh[1][0], 24)                    # el ×4 (textura) no es el ×8 (rosa)
        for por_bandas, entera in coh:
            self.assertEqual(por_bandas, entera)

    def test_rotulo_en_la_junta_de_dos_trozos_se_lee(self):
        """Solape ≥ 2× el rótulo: con trozos de 700 px y solape 200, un rótulo de 40 px de alto que
        cruza el borde del primer trozo (x = 700) cae entero en el segundo (empieza en 500) y el OCR
        por teselas del ×4 lo lee."""
        x4 = Image.new("RGB", (2048, 512), (236, 200, 220))
        txt = _texto_con_alto("XK7Q", 40, margen=4)
        self.assertLessEqual(txt.width, 200)
        x4.paste(txt, (700 - txt.width // 2, 200))
        src = self._tiff("junta.tif", [(8192, 2048, None), (2048, 512, x4), (1024, 256, None)])
        cajas, leidas = self.codigo(
            "import exporta_n1 as e\nt = e.Tiff(%r)\ne.LADO_TROZO, e.SOLAPE_TROZO = 700, 200\n"
            "print(json.dumps([e._cajas_trozos(2048, 512)[:2], e._sospechosas(e.lecturas_ocr_nivel(t, 1, (1.0,)))]))"
            % src)
        self.assertEqual(cajas, [[0, 0, 700, 512], [500, 0, 1200, 512]])
        self.assertIn("XK7Q", leidas)

    def test_x4_mayor_que_el_tope_de_pillow(self):
        """Con Image.MAX_IMAGE_PIXELS a 1,5 Mpx, el ×4 de 4,2 Mpx pasa del doble del tope, como el
        del hueso: la exportación ya no lo abre entero y sale. Con un tope en el que tampoco cabe el
        ×8, PuertaCerrada controlada (antes, DecompressionBombError sin capturar)."""
        src = self._tiff("tope.tif", self.LAMINA)
        prog = ("from PIL import Image\nImage.MAX_IMAGE_PIXELS = %d\nimport exporta_n1 as e\n"
                "try:\n    d, ent = e.exporta_tiff(%r, %r)\n    print(json.dumps(['sale', ent['cristal']]))\n"
                "except e.PuertaCerrada as x:\n    print(json.dumps(['puerta', str(x)]))\n")
        self.assertEqual(self.codigo(prog % (1500000, src, "P-HE")), ["sale", "limpio"])
        veredicto, motivo = self.codigo(prog % (400000, src, "P-RE"))
        self.assertEqual(veredicto, "puerta")
        self.assertIn("DecompressionBombError", motivo)


TINTA = (40, 30, 60)


def _rotula(img, alto, xy, color=TINTA):
    """Pinta NOMBRE_SINTETICO directamente sobre `img` (sin fondo propio), con mayúsculas de ~`alto`
    px, desde `xy`. Devuelve la caja (x0, y0, x1, y1) del texto."""
    texto = NOMBRE_SINTETICO.decode()
    f = _fuente(alto)
    for _ in range(6):
        caja = f.getbbox("XK7Q")
        f = _fuente(max(4, int(round(f.size * alto / float(caja[3] - caja[1])))))
    caja = f.getbbox(texto)
    x, y = xy
    ImageDraw.Draw(img).text((x - caja[0], y - caja[1]), texto, font=f, fill=color)
    return (x, y, x + caja[2] - caja[0], y + caja[3] - caja[1])


def _piramide_rotulo(nivel, alto, y, w=4096, h=2048):
    """[(ancho, alto, imagen)] de L0 (textura, semilla 5), ×4 y ×8 sacados de L0 y el rótulo
    SINTÉTICO de `alto` px de SU nivel, a la altura `y` (px de L0), pintado: «escaner», en L0 ANTES
    de reducir (lo que haría un escáner); «L0» o «x4», SOLO en ese nivel DESPUÉS de reducir (hay que
    fabricar el fichero). Devuelve también la caja del rótulo en px de su nivel."""
    base = _textura(w, h, 5)
    caja = _rotula(base, alto, (200, y)) if nivel == "escaner" else None
    x4, x8 = base.resize((w // 4, h // 4), Image.LANCZOS), base.resize((w // 8, h // 8), Image.LANCZOS)
    if nivel == "L0":
        caja = _rotula(base, alto, (200, y))
    elif nivel == "x4":
        caja = _rotula(x4, alto, (50, y // 4))
    return [(w, h, base), (w // 4, h // 4, x4), (w // 8, h // 8, x8)], caja


@CON_IMAGEN
class RotuloEnUnNivel(Base):
    """Segunda verificación adversarial (2-oct-26): un rótulo pintado en UN solo nivel (L0 o ×4)
    después de sacar ×4 y ×8 solo lo ve la coherencia (L0 no pasa por el OCR; el ×4 sí, pero no
    lo lee si es pequeño), y la coherencia promedia ventanas de 16×16 px del ×8 (128×128 de L0):
    un rótulo que no mueve la media de una ventana más de UMBRAL_COHERENCIA sale con rc 0."""

    # (cómo, alto en px de su nivel, y en px de L0, sale). Ventanas de la coherencia: filas
    # 512-640 de L0 (64-80 del ×8; 128-160 del ×4). «dentro»: el rótulo cabe en una fila de
    # ventanas; «a caballo»: centrado en la frontera 640 (160 del ×4).
    CASOS = (("L0", 60, 546, True), ("L0", 80, 536, False),           # L0, dentro
             ("L0", 100, 590, True), ("L0", 120, 580, False),         # L0, a caballo
             ("x4", 14, 548, True), ("x4", 16, 544, False),           # ×4, dentro
             ("x4", 24, 592, True), ("x4", 28, 584, False),           # ×4, a caballo
             ("escaner", 80, 536, True), ("escaner", 240, 520, False))  # en todos los niveles

    def test_limite_declarado_rotulo_de_un_solo_nivel(self):
        """LÍMITE DECLARADO (exporta_n1, cabecera): cotas medidas el 2-oct-26 (rótulo sintético de
        tinta (40, 30, 60) sobre la textura, geometría del Grundium sin relleno). Solo en L0: sale
        hasta 60 px de alto dentro de una fila de ventanas (80 se cierra) y hasta 100 px a caballo
        de dos (120 se cierra). Solo en el ×4: 14 px dentro (16 se cierra), 24 a caballo (28 se
        cierra). Y lo que haría un escáner, el rótulo en todos los niveles: 80 px de L0 sale (el
        OCR del ×4 y del ×8 no lo lee sobre tejido) y 240 se cierra por el OCR del ×4 y del ×8;
        entre 100 y 180 depende de la posición y de la versión de Pillow (con la 11.3 de
        /usr/bin/python3, que usa test_all, 160 y 180 salen; con la 12.2, 180 se cierra), y por eso
        la cota fijada es 240 (desde 200 se cierra con las dos). Las de un solo nivel son de la
        coherencia y salen iguales con las dos versiones. El peor que
        sale (100 px de L0, a caballo) lleva sus píxeles en la copia N1 y `revalidar_n1` (lo que
        usan vision_n1 y nube_n1 antes de enviar) la acepta. Un cambio que empeore estas cotas
        rompe el test; uno que las mejore, también: actualiza la cabecera de exporta_n1."""
        GR3 = GrundiumReal.GRUNDIUM_3
        for i, (como, alto, y, sale) in enumerate(self.CASOS):
            niveles, caja = _piramide_rotulo(como, alto, y)
            src = os.path.join(self.tmp, "un-nivel-%d.tif" % i)
            escribe_tiff(src, niveles, tags_k=GR3)
            opaco = "P-UN%d" % i
            r = self.corre("exporta_n1.py", "tiff", src, "--opaco", opaco)
            dst = os.path.join(self.n1, "%s.n1.tif" % opaco)
            caso = "%s %d px (y %d)" % (como, alto, y)
            self.assertNotIn("Quintanar", r.stdout + r.stderr)
            if sale:
                self.assertEqual(r.returncode, 0, caso + r.stdout + r.stderr)        # el límite: sale
                self.assertTrue(os.path.exists(dst), caso)
                continue
            self.assertEqual(r.returncode, 3, caso + r.stdout + r.stderr)
            self.assertIn("1-bis", r.stdout, caso)
            self.assertIn("IFD 1 (×4): OCR" if como == "escaner" else
                          "IFD %d: reducido no se parece al ×8" % (0 if como == "L0" else 1), r.stdout, caso)
            self.assertFalse(os.path.exists(dst), caso)
        # El peor que sale: L0, 100 px a caballo (CASOS[2]). La copia lleva el rótulo y el envío la acepta.
        niveles, caja = _piramide_rotulo("L0", 100, 590)
        dst = os.path.join(self.n1, "P-UN2.n1.tif")
        limpia = (caja[0], caja[1] + 300, caja[2], caja[3] + 300)
        v = self.codigo(
            "import exporta_n1 as e, hashlib\nt = e.Tiff(%r)\n"
            "g = lambda c: min(e._region_de_nivel(t, 0, c).convert('L').getdata())\n"
            "b = e.revalidar_n1(%r, hashlib.sha256(open(%r, 'rb').read()).hexdigest())\n"
            "print(json.dumps([g(%r), g(%r), len(b) == len(open(%r, 'rb').read())]))"
            % (dst, dst, dst, list(caja), list(limpia), dst))
        self.assertLess(v[0], 60, "la copia N1 no lleva el rótulo (gris mínimo %d)" % v[0])
        self.assertGreater(v[1], v[0] + 30)
        self.assertTrue(v[2])


def _piramide_borde(w, h, rotulo=None, donde=(0, 0)):
    """[(ancho, alto, imagen)]: textura y, desde x = 2000, vidrio; ×4 y ×8 sacados de L0 (Lanczos)
    y, DESPUÉS, `rotulo` (imagen) pegado SOLO en L0 en `donde`."""
    base = _textura(w, h, 5)
    ImageDraw.Draw(base).rectangle((2000, 0, w, h), fill=VIDRIO)
    x4, x8 = base.resize((w // 4, h // 4), Image.LANCZOS), base.resize((w // 8, h // 8), Image.LANCZOS)
    if rotulo is not None:
        base.paste(rotulo, donde)
    return [(w, h, base), (w // 4, h // 4, x4), (w // 8, h // 8, x8)]


@CON_IMAGEN
class CoherenciaBordes(Base):
    """Verificador independiente, 2-oct-26: `incoherencia` y `_coherencia_nivel` redondeaban hacia
    abajo a múltiplos de la ventana (16 px del ×8), así que la franja inferior y la derecha del ×8
    sin ventana completa (hasta 15 px del ×8, ~120 px de L0) no se comparaban, y L0 no pasa por el
    OCR: un rótulo pintado SOLO en L0 ahí salía a N1 como limpio (rc 0). Ahora las ventanas
    parciales del borde se comparan con su media entre su área real, el mismo umbral y el mismo
    relleno. Franja inferior, derecha y esquina; y en cada una, que la cuenta de ANTES (solo las
    ventanas enteras) no lo ve: el mutante.
    Desde el RELLENO DE TESELA (2-oct-26, `RellenoDeTesela`) estas geometrías (niveles que no son un
    número entero de teselas) ya no salen: se cierran antes, sin paso 1-bis. Las ventanas parciales
    se siguen comprobando aquí función a función: son la defensa si esa regla cambia."""

    GR3 = GrundiumReal.GRUNDIUM_3
    ROTULO = NOMBRE_SINTETICO.decode()

    def _tiff(self, nombre, niveles):
        src = os.path.join(self.tmp, nombre)
        escribe_tiff(src, niveles, tags_k=self.GR3)
        return src

    def _coherencias(self, src):
        """[nueva, de antes] de L0 frente al ×8: la de ahora y la de las ventanas enteras solas."""
        return self.codigo(
            "import exporta_n1 as e\nt = e.Tiff(%r)\nimg8 = e._imagen_de_nivel(t, 2)\n"
            "a = e._mosaico(t, 0, 8)\nv = e.VENTANA_COHERENCIA\n"
            "caja = (0, 0, img8.width // v * v, img8.height // v * v)\n"
            "print(json.dumps([e._coherencia_nivel(t, 0, 8, img8),"
            " e._dif_ventanas(a.crop(caja), img8.crop(caja))]))" % src)

    def _salta(self, nombre, w, h, rotulo, donde, opaco):
        src = self._tiff(nombre, _piramide_borde(w, h, rotulo, donde))
        r = self.corre("exporta_n1.py", "tiff", src, "--opaco", opaco)
        self.assertFalse(os.path.exists(os.path.join(self.n1, "%s.n1.tif" % opaco)),
                         "%s: el rótulo solo en L0, en el borde, entra en N1" % nombre)
        self.assertEqual(r.returncode, 3, nombre + r.stdout + r.stderr)
        self.assertIn("RELLENO", r.stdout, nombre)                   # ya no llega a la coherencia
        self.assertNotIn("1-bis", r.stdout, nombre)
        self.assertNotIn("Quintanar", r.stdout)
        motivos = self.codigo("import exporta_n1 as e\nt = e.Tiff(%r)\n"
                              "print(json.dumps(e._motivos_cristal_tiff(t)))" % src)
        self.assertIn("IFD 0: reducido no se parece al ×8", " ".join(motivos), nombre)
        nueva, antes = self._coherencias(src)
        self.assertLessEqual(antes, 24, "%s: el fixture no vale, la cuenta de antes ya lo veía" % nombre)
        self.assertGreater(nueva, 24, nombre)

    def test_rotulo_solo_en_l0_en_la_franja_inferior(self):
        """L0 4000×1000: ×8 500×125, 7 ventanas de alto (112 filas); filas 112-124 del ×8 = 896-999
        de L0. El rótulo del verificador (77 px de alto) en las filas 905-982."""
        rot = _texto_con_alto(self.ROTULO, 60, margen=8)
        self._salta("inf.tif", 4000, 1000, rot, (2200, 905), "P-BI")

    def test_rotulo_solo_en_l0_en_la_franja_derecha(self):
        """L0 3960×1280: ×8 495×160, 30 ventanas de ancho (480 columnas); columnas 480-494 del ×8 =
        3840-3959 de L0. El mismo rótulo, en vertical (77 px de ancho), en 3865-3942."""
        rot = _texto_con_alto(self.ROTULO, 60, margen=8).rotate(90, expand=True)
        self.assertLessEqual(rot.width, 95)
        self._salta("der.tif", 3960, 1280, rot, (3865, 60), "P-BD")

    def test_rotulo_solo_en_l0_en_la_esquina(self):
        """L0 3960×1000: la esquina sin ventana entera es de 120×104 px de L0 (15×13 del ×8). Una
        etiqueta de 98×40 px (amarilla, para que mueva la media de UNA ventana parcial: medido el
        2-oct-26, 46 frente a 1 con la cuenta de antes; la misma en rosa, 19)."""
        rot = _texto_con_alto("24B0", 30, color=(40, 30, 60), fondo=(250, 230, 90), margen=6)
        self.assertLessEqual(rot.width, 112)
        self.assertLessEqual(rot.height, 100)
        self._salta("esq.tif", 3960, 1000, rot, (3844, 898), "P-BE")

    def test_sin_rotulo_las_mismas_geometrias_salen_limpias(self):
        """Las ventanas parciales no traen falsos positivos en sintético: sin rótulo, la coherencia
        de L0 y del ×4 con el ×8 queda bajo el umbral en las tres geometrías, y sin ventanas que
        enseñar. Desde el relleno de tesela no salen: se cierran por RELLENO, sin paso 1-bis."""
        for nombre, w, h in (("l-inf.tif", 4000, 1000), ("l-der.tif", 3960, 1280), ("l-esq.tif", 3960, 1000)):
            src = self._tiff(nombre, _piramide_borde(w, h))
            coh = self.codigo("import exporta_n1 as e\nt = e.Tiff(%r)\nimg8 = e._imagen_de_nivel(t, 2)\n"
                              "print(json.dumps([e._coherencia_nivel_detalle(t, k, den, img8) for k, den in "
                              "((0, 8), (1, 2))]))" % src)
            self.assertTrue(all(d <= 24 and not v for d, v in coh), (nombre, coh))
        r = self.corre("exporta_n1.py", "tiff", src, "--opaco", "P-BL")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("RELLENO", r.stdout)
        self.assertNotIn("1-bis", r.stdout)

    def test_x8_mas_corto_que_el_nivel_no_exporta(self):
        """El ±1 % de `_comprueba_piramide` deja un ×8 de 2032 px para un L0 de 16384 (2048): las
        columnas 16256-16383 de L0 no tendrían con qué compararse. Antes salía (rc 0); ahora
        PuertaCerrada, sin paso 1-bis (la miniatura tampoco las enseña). Un resto de < 8 px de L0
        (un ×8 de floor(L0/8) con L0 no múltiplo de 8) sí se acepta: dentro del microtexto. Sin
        relleno (teselas de 16 px en el ×8): desde el 2-oct-26 un ×8 de 507 px se cierra antes."""
        src = self._tiff("corto.tif", [(16384, 4096, None), (4096, 1024, None), (2032, 512, None)])
        r = self.corre("exporta_n1.py", "tiff", src, "--opaco", "P-BC")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("no cubre el nivel reducido", r.stdout)
        self.assertNotIn("1-bis", r.stdout)
        zona = self.codigo("import exporta_n1 as e\nprint(json.dumps([e._zona_coherencia(0, 4007, 1003, 8, 500, 125),"
                           " e._zona_coherencia(1, 1001, 250, 2, 500, 125)]))")
        self.assertEqual(zona, [[500, 125], [500, 125]])

    def test_la_r5_de_la_ingesta_decide_lo_mismo_en_el_borde(self):
        """`laminillas_ingesta._cristal_tiff` (r5) replica la decisión de la Puerta: con el rótulo
        de la franja inferior da el mismo motivo de coherencia, y con el ×8 corto, el mismo cierre
        (la coherencia como error, «no cubre»; antes la ingesta la daba por buena)."""
        rot = _texto_con_alto(self.ROTULO, 60, margen=8)
        inf = self._tiff("r5-inf.tif", _piramide_borde(4000, 1000, rot, (2200, 905)))
        corto = self._tiff("r5-corto.tif", [(16384, 4096, None), (4096, 1024, None), (2032, 512, None)])
        v = self.codigo(
            "import os, exporta_n1 as E, laminillas_ingesta as I\nloc = (lambda *a: 'vidrio', lambda *a: 0.0)\n"
            "out = {}\nfor src in (%r, %r):\n"
            "    _f, coh, mot = I._cristal_tiff(os.path.dirname(src), 'P-X', os.path.basename(src), loc, 0.25)\n"
            "    t = E.Tiff(src)\n"
            "    try:\n        ref = E._motivos_cristal_tiff(t)\n"
            "    except E.PuertaCerrada as e:\n        ref = 'cerrada: ' + str(e)\n"
            "    finally:\n        t.f.close()\n"
            "    out[os.path.basename(src)] = [mot, ref, coh]\n"
            "print(json.dumps(out))" % (inf, corto))
        mot, ref, coh = v["r5-inf.tif"]
        self.assertEqual(mot, ref)
        self.assertTrue(any(m.startswith("IFD 0: reducido no se parece") for m in mot), mot)
        self.assertTrue(coh["IFD0_x1"]["salta"])
        self.assertIn("RELLENO", coh["relleno"]["error"])            # y no saldría: relleno
        self.assertIsNone(coh["relleno"]["salta"])
        mot, ref, coh = v["r5-corto.tif"]
        self.assertNotIn("relleno", coh)
        self.assertIn("no cubre el nivel reducido", ref)
        self.assertIn("no cubre el nivel reducido", coh["IFD0_x1"].get("error", ""), coh)
        self.assertIsNone(coh["IFD0_x1"]["salta"])

    def test_ventanas_cubren_toda_la_imagen(self):
        """`_ventanas` parte la imagen entera, sin huecos ni solapes, para cualquier tamaño."""
        cajas = self.codigo(
            "import exporta_n1 as e\nout = []\n"
            "for w, h in ((500, 125), (495, 160), (16, 16), (15, 7), (1, 1), (33, 17), (4096, 1024)):\n"
            "    area, ok = 0, True\n"
            "    for (x0, y0, x1, y1), vx, vy in e._ventanas(w, h):\n"
            "        area += (x1 - x0) * (y1 - y0)\n"
            "        ok = ok and (x1 - x0) % vx == 0 and (y1 - y0) % vy == 0 and 0 < vx <= 16 and 0 < vy <= 16\n"
            "    out.append([area == w * h, ok])\n"
            "print(json.dumps(out))")
        self.assertEqual(cajas, [[True, True]] * 7)


@CON_IMAGEN
class RellenoDeTesela(Base):
    """Deuda puerta-n1-relleno-de-tesela-sin-mirar (2-oct-26). exporta_n1 copiaba a N1 las teselas
    del borde enteras, pero todo lo que mira (OCR, Puerta, trazos, coherencia) se recorta antes al
    ancho y alto declarados: un rótulo pintado en el RELLENO de una tesela del borde salía con rc 0
    y un OCR lo leía decodificando esa tesela. Medido en las 15 láminas reales por la ventanilla
    (`laminillas_exporta -- diagnostico relleno`, solo números): ninguno de sus 47 niveles tiene
    relleno y sus 114 235 teselas declaran 512×512 en el SOF0. La Puerta exige eso: relleno, ni
    liso; un JPEG más pequeño que su tesela, tampoco. PuertaCerrada sin paso 1-bis."""

    GR3 = GrundiumReal.GRUNDIUM_3
    W, H = 3600, 1024                         # L0: la última columna de teselas cubre 3584-4095

    def _con_relleno(self, nombre, rotulo=True, relleno=None):
        """L0 de 3600×1024 en teselas de 512 (496 px de relleno en la última columna); ×4 y ×8 de lo
        visible. `rotulo`: QUINTANAR pintado SOLO en el relleno; `relleno`: color liso del relleno."""
        lienzo = _textura(4096, self.H, 5)
        vis = lienzo.crop((0, 0, self.W, self.H))
        if relleno:
            ImageDraw.Draw(lienzo).rectangle((self.W, 0, 4096, self.H), fill=relleno)
        if rotulo:
            lienzo.paste(_texto_con_alto("QUINTANAR", 60, margen=8), (3610, 300))
        src = os.path.join(self.tmp, nombre)
        escribe_tiff(src, [(self.W, self.H, lienzo),
                           (self.W // 4, self.H // 4, vis.resize((self.W // 4, self.H // 4), Image.LANCZOS)),
                           (self.W // 8, self.H // 8, vis.resize((self.W // 8, self.H // 8), Image.LANCZOS))],
                     tags_k=self.GR3, tesela=512)
        return src

    def _cerrada(self, src, opaco, *esperado):
        r = self.corre("exporta_n1.py", "tiff", src, "--opaco", opaco)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        for e in esperado:
            self.assertIn(e, r.stdout)
        self.assertNotIn("1-bis", r.stdout)                  # la miniatura no lo enseña: sin 1-bis
        self.assertNotIn("QUINTANAR", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.n1, "%s.n1.tif" % opaco)))
        return r

    def test_rotulo_en_el_relleno_no_sale(self):
        """La reproducción de la deuda. El fixture vale: decodificando la tesela del borde ENTERA,
        el OCR lee el rótulo en su relleno, y nada de lo recortado a 3600×1024 lo ve (el OCR del ×8
        y los niveles menores, la coherencia y los trazos no dan ningún motivo). Con el exporta_n1
        de antes salía (rc 0: el mutante, medido el 2-oct-26). Ahora, RELLENO y sin paso 1-bis; la
        r5 de la ingesta lo anota igual (`coherencia["relleno"]`) y la hoja dice TODAVÍA NO."""
        src = self._con_relleno("rot.tif")
        leido, motivos = self.codigo(
            "import exporta_n1 as e\nfrom PIL import Image\nt = e.Tiff(%r)\n"
            "o, c = t.enteros(t.ifds[0], 324)[7], t.enteros(t.ifds[0], 325)[7]\n"
            "w, h, rgb = e.decodifica_jpeg(e.tesela_canonica(t.lee(o, c), 512, 512))\n"
            "relleno = Image.frombytes('RGB', (w, h), rgb).crop((16, 0, 512, 512))\n"
            "print(json.dumps([e._sospechosas(e.lecturas_ocr(relleno, (1.0, 0.5))), e._motivos_cristal_tiff(t)]))"
            % src)
        self.assertTrue(any("QUINTANAR" in w for w in leido), leido)     # el rótulo está y se lee
        self.assertEqual(motivos, [])                            # y lo recortado no lo ve
        self._cerrada(src, "P-PD", "RELLENO", "3600x1024")
        v = self.codigo(
            "import os, laminillas_ingesta as I, laminillas_exporta as X\n"
            "loc = (lambda *a: 'vidrio', lambda *a: 0.0)\n"
            "_f, coh, mot = I._cristal_tiff(os.path.dirname(%r), 'P-PD', os.path.basename(%r), loc, 0.25)\n"
            "print(json.dumps([coh.get('relleno'), mot, X.llega_a_preguntar(os.path.dirname(%r), "
            "{'fichero': os.path.basename(%r)})]))" % (src, src, src, src))
        relleno_r5, mot_r5, llega = v
        self.assertIn("RELLENO", relleno_r5["error"])
        self.assertIsNone(relleno_r5["salta"])
        self.assertEqual(mot_r5, [])
        self.assertIn("RELLENO", llega)

    def test_relleno_liso_tampoco_sale(self):
        """Más estricto que «relleno liso» (decisión del 2-oct-26 con lo medido: el Grundium no
        escribe relleno): ni blanco ni negro uniformes, y sin umbral de ruido que fijar."""
        for color, opaco in (((255, 255, 255), "P-LB"), ((0, 0, 0), "P-LN")):
            self._cerrada(self._con_relleno("liso-%s.tif" % opaco, rotulo=False, relleno=color), opaco,
                          "RELLENO")

    def test_tesela_con_jpeg_menor_que_la_tesela_no_sale(self):
        """La otra cara del relleno: una tesela de 512 cuyo JPEG mide 512×500 deja 12 filas sin
        cubrir (blancas al pegarla) y guarda en su último MCU filas que ningún decodificador saca.
        Las 114 235 teselas reales declaran 512×512: PuertaCerrada sin 1-bis."""
        src = os.path.join(self.tmp, "corto.tif")
        escribe_tiff(src, [(16384, 4096, None), (4096, 1024, None), (2048, 512, None)], tags_k=self.GR3,
                     transforma=lambda t: _jpeg_limpio(Image.new("RGB", (512, 500), (236, 200, 220))))
        self._cerrada(src, "P-PC", "512x500")
        relleno_r5, llega = self.codigo(
            "import os, laminillas_ingesta as I, laminillas_exporta as X\n"
            "loc = (lambda *a: 'vidrio', lambda *a: 0.0)\n"
            "_f, coh, mot = I._cristal_tiff(os.path.dirname(%r), 'P-PC', os.path.basename(%r), loc, 0.25)\n"
            "print(json.dumps([coh.get('relleno'), X.llega_a_preguntar(os.path.dirname(%r), "
            "{'fichero': os.path.basename(%r)})]))" % (src, src, src, src))
        self.assertIn("su JPEG no mide", relleno_r5["error"])         # la r5 y la hoja, lo mismo
        self.assertIn("su JPEG no mide", llega)

    def test_sin_relleno_sale_y_el_envio_lo_acepta(self):
        """El formato real: cada nivel, un número entero de teselas (aquí 512, 256 y 128, como da
        `lado_sin_relleno`), sale limpio y la re-validación del envío lo acepta."""
        src = os.path.join(self.tmp, "entero.tif")
        escribe_tiff(src, _piramide_coherente(_textura(4096, 1024, 5)), tags_k=self.GR3)
        self.assertEqual([struct.unpack("<H", tg[322][1])[0] for tg, _t in lee_tiff(src)], [512, 256, 128])
        r = self.corre("exporta_n1.py", "tiff", src, "--opaco", "P-SR")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        dst = os.path.join(self.n1, "P-SR.n1.tif")
        v = self.codigo("import exporta_n1 as e, hashlib\n"
                        "b = e.revalidar_n1(%r, hashlib.sha256(open(%r, 'rb').read()).hexdigest())\n"
                        "print(json.dumps(len(b)))" % (dst, dst))
        self.assertEqual(v, len(_lee(dst)))


class ManifiestoN1(Base):
    """El manifiesto N1 se revisa por esquema (1-oct-26): el `bytes` de cada entrada es len(datos),
    lo pone exporta_n1, y una copia N1 de lámina entera pasa de 9 cifras. Antes, cualquier TIFF de
    ≥100 MB abortaba con «json: entero de ≥9 cifras en $.ficheros.P-HE.n1.tif.bytes». Solo ese
    valor se exime: el mismo número en otra clave, o en un JSON/CSV de usuario, sigue abortando."""

    GRANDE = 1500000000

    def _revisa(self, funcion, texto):
        return self.codigo(
            "import puerta_n1 as pu\ntry:\n    pu.%s(%r)\n    print(json.dumps('PASA'))\n"
            "except pu.PuertaCerrada as x:\n    print(json.dumps('ABORTA ' + str(x)))" % (funcion, texto))

    def _man(self, **entrada):
        ent = dict({"sha256": "ab" * 32, "tipo": "tiff-n1", "cristal": "limpio"}, **entrada)
        return {"version": 1, "ficheros": {"P-HE.n1.tif": ent}}

    def test_bytes_de_lamina_entera_pasa(self):
        # 20260430 bytes (~20 MB) tiene además forma de fecha AAAAMMDD: tampoco es un dato suyo
        for n in (self.GRANDE, 20260430, 0):
            v = self._revisa("revisar_manifiesto", json.dumps(self._man(bytes=n)))
            self.assertEqual(v, "PASA", "bytes=%d → %s" % (n, v))

    def test_el_mismo_valor_en_otra_clave_aborta(self):
        malos = {"otra clave de la entrada": self._man(bytes=10, ancho=self.GRANDE),
                 "bytes en la raíz": dict(self._man(bytes=10), bytes=self.GRANDE),
                 "bytes anidado": self._man(bytes=10, niveles=[{"bytes": self.GRANDE}])}
        for caso, man in malos.items():
            v = self._revisa("revisar_manifiesto", json.dumps(man))
            self.assertTrue(v.startswith("ABORTA") and "9 cifras" in v, "%s → %s" % (caso, v))

    def test_json_y_csv_de_usuario_no_se_relajan(self):
        v = self._revisa("revisar_json", json.dumps(self._man(bytes=self.GRANDE)))
        self.assertTrue(v.startswith("ABORTA") and "9 cifras" in v, v)
        v = self._revisa("revisar_csv", "fichero,bytes\nP-HE.n1.tif,%d\n" % self.GRANDE)
        self.assertTrue(v.startswith("ABORTA") and "9 cifras" in v, v)

    def test_escribe_n1_con_lamina_entera(self):
        """Por el sitio real (`_escribe_n1`): una «lámina» cuyo len() es 1,5 GB entra, el manifiesto
        lo apunta, y la exportación siguiente (que revisa el manifiesto entero) también pasa."""
        v = self.codigo(
            "import exporta_n1 as e\n"
            "class Grande(bytes):\n    def __len__(self):\n        return %d\n"
            "out = []\n"
            "for nombre, datos, entrada in (('P-HE.n1.tif', Grande(b'II*\\x00'), {'tipo': 'tiff-n1'}),\n"
            "                               ('P-KI67-t.csv', b'marker,value\\nRE,85.2\\n', {'tipo': 'csv'}),\n"
            "                               ('P-KI67-u.csv', b'a\\n', {'tipo': 'csv', 'ancho': %d})):\n"
            "    try:\n        e._escribe_n1(nombre, datos, entrada)\n        out.append('PASA')\n"
            "    except e.PuertaCerrada as x:\n        out.append('ABORTA ' + str(x))\n"
            "print(json.dumps(out))" % (self.GRANDE, self.GRANDE))
        self.assertEqual(v[:2], ["PASA", "PASA"], v)
        self.assertTrue(v[2].startswith("ABORTA") and "9 cifras" in v[2], v)
        with open(os.path.join(self.n1, "manifiesto.json"), encoding="utf-8") as fh:
            man = json.load(fh)["ficheros"]
        self.assertEqual(man["P-HE.n1.tif"]["bytes"], self.GRANDE)
        self.assertEqual(sorted(man), ["P-HE.n1.tif", "P-KI67-t.csv"])
        self.assertFalse(os.path.exists(os.path.join(self.n1, "P-KI67-u.csv")))


class FailClosed(Base):

    def test_canario_de_casa_base_en_csv(self):
        self.assertFalse(os.path.exists(os.path.join(self.copia, "state")))   # el árbol no lo tiene
        p = self.fichero("can.csv", "nota,valor\n%s,1\n" % CANARIO)
        r = self.corre("exporta_n1.py", "texto", p, "--nombre", "P-KI67-x.csv")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        # Hallazgo 25: el motivo concreto, no el mensaje fail-closed (que también dice «canario»)
        self.assertIn("canario@", r.stdout)
        self.assertNotIn("puerta sin diccionario", r.stdout)

    def test_state_vacio_aborta(self):
        vacio = tempfile.mkdtemp(dir=self.tmp)
        p = self.fichero("ok.csv", "marker,value\nRE,85.2\n")
        r = self.corre("exporta_n1.py", "texto", p, "--nombre", "P-KI67-x.csv",
                       env=dict(self.env, BTP_STATE_DIR=vacio))
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("puerta sin diccionario", r.stdout)

    def test_sin_pil_en_casa_base_es_rojo(self):
        """Residuo del hallazgo 26 (2-oct-26): la rama ROJO de la cabecera de este fichero no la
        probaba nada. Sin PIL (un PIL de mentira que no importa) y sin BTP_PORTABLE: rc 1 y ROJO,
        nunca un verde con todo saltado."""
        stub = os.path.join(self.tmp, "stub", "PIL")
        os.makedirs(stub)
        with open(os.path.join(stub, "__init__.py"), "w") as fh:
            fh.write("raise ImportError('PIL de mentira (test)')\n")
        env = {k: v for k, v in self.env.items() if k != "BTP_PORTABLE"}
        env["PYTHONPATH"] = os.path.dirname(stub)
        r = subprocess.run([PY, os.path.abspath(__file__)], capture_output=True, text=True, timeout=120,
                           env=env, cwd=self.tmp, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("ROJO: falta PIL", r.stdout)

    def test_sin_pil_y_portatil_las_clases_de_imagen_se_saltan(self):
        """Hallazgo 26 de la 3.ª pasada: 6ee8efd dejó a Vision sin `@CON_IMAGEN` y, sin PIL y con
        BTP_PORTABLE, sus casos daban ERROR en el setUp. Cada clase que fabrica imágenes en el
        setUp o en todos sus casos se SALTA entera; ninguna da ERROR."""
        stub = os.path.join(self.tmp, "stub", "PIL")
        os.makedirs(stub)
        with open(os.path.join(stub, "__init__.py"), "w") as fh:
            fh.write("raise ImportError('PIL de mentira (test)')\n")
        env = dict(self.env, BTP_PORTABLE="1", PYTHONPATH=os.path.dirname(stub))
        clases = ["PuertaImagen", "OcrTextoPequeno", "TiffN1", "Vision"]
        r = subprocess.run([PY, os.path.abspath(__file__)] + clases, capture_output=True, text=True,
                           timeout=300, env=env, cwd=self.tmp, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("ERROR", r.stderr)
        self.assertRegex(r.stderr, r"OK \(skipped=\d+\)")
        n = len([x for x in r.stderr.splitlines() if " ... skipped" in x])
        self.assertGreaterEqual(n, 25, r.stderr)

    def test_repo_sin_overlays_aborta_exporta_y_vision(self):
        otra = os.path.join(self.tmp, "sin-overlays")
        os.makedirs(os.path.join(otra, "tools", "state", "borde"))
        self._json(os.path.join(otra, "tools", "state", "borde", "canarios.json"), [CANARIO])
        env = dict(self.env, BTP_REPO=otra)
        p = self.fichero("ok.csv", "marker,value\nRE,85.2\n")
        r = self.corre("exporta_n1.py", "texto", p, "--nombre", "P-KI67-x.csv", env=env)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("puerta sin diccionario", r.stdout)
        r = self.corre("vision_n1.py", "comprobar", "--destino", "vision-n1:gemini", p, env=env)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("puerta sin diccionario", r.stdout)


class RevisionHumana(Base):
    """`puerta_n1.revision_humana` (hueco de f3c2a2f, 2-oct-26): solo cuenta un `n1_revision_humana`
    con `via: 'tty'` (que solo sella borde.revisar_cristal_en_tty) y con la cadena íntegra."""
    SHA = "cd" * 32

    def _rev(self, sha=SHA):
        return self.codigo("import puerta_n1 as pu\nprint(json.dumps(pu.revision_humana(%r)))" % sha)

    def test_solo_cuenta_con_via_tty_y_la_cadena_integra(self):
        ev = {"evento": "n1_revision_humana", "opaco": "P-F", "sha256": self.SHA, "nivel": "info"}
        self.assertEqual(self.sella(ev), "RECHAZADO")            # f3c2a2f: SELLADO, y abría
        self.assertIs(self._rev(), False)
        self.revision(self.SHA, via=None)
        self.assertIs(self._rev(), False)                         # sin via tty no cuenta
        self.revision(self.SHA)
        self.assertIs(self._rev(), True)
        self.assertEqual([e.get("de") for e in self.eventos("sello_fuera_de_trust_cloud")],
                         ["n1_revision_humana"] * 2)
        # Una línea escrita a mano en el ledger (sin seq, prev ni hash), con otro sha: no cuenta, y
        # con la cadena rota ya no cuenta ninguna.
        otro = "ef" * 32
        d = os.path.join(self.casa_tools, "state", "borde")
        led = os.path.join(d, sorted(f for f in os.listdir(d) if f.startswith("ledger-"))[-1])
        with open(led, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(ev, sha256=otro, via="tty")) + "\n")
        self.assertIs(self._rev(otro), False)
        self.assertIs(self._rev(), False)


# 6ee8efd metió RevisionHumana entre el decorador y Vision, que lo perdió: sin PIL y con
# BTP_PORTABLE, sus 14 casos daban ERROR en el setUp en vez de saltarse (hallazgo 26, 3.ª pasada).
@CON_IMAGEN
class Vision(Base):

    def setUp(self):
        super().setUp()
        p = self.fichero("tex.png", b"")
        _textura(900, 600).save(p)
        r = self.corre("exporta_n1.py", "png", p, "--opaco", "P-KI67", "--nivel", "x8", "--mpp", "2.0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.png = os.path.join(self.n1, "P-KI67__x8__mpp2.0000.png")

    def _vision(self, *args, destino="vision-n1:gemini", prompt="Review the Ki67 overlay"):
        return self.corre("vision_n1.py", "comprobar", "--destino", destino, "--prompt", prompt, *args)

    def _enviar(self, *args):
        return self.corre("vision_n1.py", "enviar", "--destino", "vision-n1:gemini", "--modelo", "m",
                          "--prompt", "Ki67 overlay", *args)

    def test_no_confiado_y_luego_confiado_desde_casa_base(self):
        r = self._vision(self.png)
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("no está confiado", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.copia, "state")))
        self.confia("vision-n1:gemini")
        r = self._vision(self.png)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_evento_sin_via_tty_no_abre(self):
        self.confia("vision-n1:gemini", via=None)
        r = self._vision(self.png)
        self.assertEqual(r.returncode, 3, r.stdout)
        # Desde el 1-oct-26 ya `es_trusted` exige el trust_cloud con via tty para vision-n1:*, así que
        # la puerta cierra antes, con «no está confiado»; antes cerraba con «…no se tecleó en TTY».
        self.assertRegex(r.stdout, r"TTY|no está confiado")

    def test_cadena_alterada_no_abre(self):
        """Hallazgo 22 (1): un ledger retocado después de confiar no abre."""
        self.confia("vision-n1:gemini")
        self.assertEqual(self._vision(self.png).returncode, 0)
        d = os.path.join(self.casa_tools, "state", "borde")
        led = os.path.join(d, sorted(f for f in os.listdir(d) if f.startswith("ledger-"))[-1])
        lineas = _lee(led, "r").splitlines()
        rec = json.loads(lineas[-1])
        self.assertEqual(rec["evento"], "trust_cloud")
        rec["para"] = "otra-cosa"
        lineas[-1] = json.dumps(rec, ensure_ascii=False, sort_keys=True)
        with open(led, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lineas) + "\n")
        r = self._vision(self.png)
        self.assertEqual(r.returncode, 3, r.stdout)
        # Desde la 2.ª pasada del grupo trust-hook (2-oct-26) `borde.es_trusted` exige también la
        # cadena íntegra, así que puede cerrar antes («no está confiado»). La guarda propia de la
        # puerta se mira aparte, con es_trusted forzado a True: sin ella, abriría.
        self.assertRegex(r.stdout, r"no está íntegra|no está confiado")
        v = self.codigo("import puerta_n1 as pu\npu.borde.es_trusted = lambda d: True\n"
                        "try:\n    pu.exigir_confianza('vision-n1:gemini', 'vision-n1:')\n"
                        "    print(json.dumps('ABRE'))\nexcept pu.PuertaCerrada as e:\n"
                        "    print(json.dumps(str(e)))")
        self.assertIn("no está íntegra", v)

    def test_revoke_posterior_no_abre(self):
        """Hallazgo 22 (2): un revoke_cloud sellado después cierra, aunque cloud_confiados.json
        siga diciendo que está confiado."""
        self.confia("vision-n1:gemini")
        self.sella({"evento": "revoke_cloud", "destino": "vision-n1:gemini", "nivel": "info"})
        r = self._vision(self.png)
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertRegex(r.stdout, r"no se tecleó en TTY|no está confiado")
        v = self.codigo("import puerta_n1 as pu\nprint(json.dumps(pu.evento_confianza('vision-n1:gemini')))")
        self.assertIsNone(v)

    def test_halt_no_abre(self):
        self.confia("vision-n1:gemini")
        with open(self.halt, "w") as fh:
            fh.write("1")
        r = self._vision(self.png)
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("HALT", r.stdout)

    def test_ruta_fuera_de_n1_y_fuera_del_manifiesto(self):
        self.confia("vision-n1:gemini")
        fuera = self.fichero("fuera.png", b"")
        _textura(100, 100).save(fuera)
        r = self._vision(fuera)
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("fuera de N1", r.stdout)
        colado = os.path.join(self.n1, "colado.png")
        shutil.copy(fuera, colado)
        r = self._vision(colado)
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("manifiesto", r.stdout)
        with open(self.png, "ab") as fh:                       # alterado tras exportar
            fh.write(b"\x00")
        r = self._vision(self.png)
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("sha256", r.stdout)

    def test_png_colado_con_metadatos_o_texto_no_sale(self):
        """Hallazgo 9: el manifiesto no es prueba de procedencia. Un PNG que no pasó por
        exporta_n1, en N1 y apuntado a mano, se vuelve a mirar entero antes de salir."""
        self.confia("vision-n1:gemini")
        from PIL import PngImagePlugin
        info = PngImagePlugin.PngInfo()
        info.add_text("Author", "Leocadia Quintanar")
        info.add_itxt("Comment", "24B0001043")
        buf = io.BytesIO()
        _textura(400, 300).save(buf, "PNG", pnginfo=info)
        p = self.colar_en_n1("P-X__x8__mpp2.0000.png", buf.getvalue())
        r = self._vision(p)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("chunk", r.stdout)
        buf = io.BytesIO()
        _textura_con_rotulo("Leocadia Quintanar", 48).save(buf, "PNG")
        p = self.colar_en_n1("P-Y__x8__mpp2.0000.png", buf.getvalue())
        r = self._vision(p)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("texto en la imagen", r.stdout)
        p = self.colar_en_n1("P-Z.tif", b"II*\x00" + b"\x00" * 64)
        r = self._vision(p)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("solo van PNG o texto", r.stdout)

    @staticmethod
    def _chunks(datos):
        i, out = 8, []
        while i < len(datos):
            ln = struct.unpack(">I", datos[i:i + 4])[0]
            out.append((datos[i + 4:i + 8], datos[i + 8:i + 8 + ln]))
            i += 12 + ln
        return out

    @staticmethod
    def _png(chunks):
        out = bytearray(b"\x89PNG\r\n\x1a\n")
        for tipo, datos in chunks:
            out += struct.pack(">I", len(datos)) + tipo + datos
            out += struct.pack(">I", __import__("zlib").crc32(tipo + datos) & 0xFFFFFFFF)
        return bytes(out)

    def test_png_colado_con_paleta_cola_zlib_o_alfa_no_sale(self):
        """Hallazgo 9 bis (2-oct-26): tres sitios de un PNG donde va texto que ningún píxel RGB
        enseña, y que exporta_n1 nunca escribe. Con CRC correctos, en N1 y en el manifiesto."""
        self.confia("vision-n1:gemini")
        texto = b"Leocadia Quintanar 24B0001043 {{CENTRO}} 2026-04-30"
        # B: paleta (PLTE) con el texto en entradas que no usa ningún píxel
        buf = io.BytesIO()
        _textura(400, 300).quantize(16).save(buf, "PNG")
        ch = [(t, (d[:48] + texto.ljust(96, b" ")) if t == b"PLTE" else d)
              for t, d in self._chunks(buf.getvalue())]
        self.assertEqual(ch[0][1][9], 3)                        # el fixture: indexado, con texto
        p = self.colar_en_n1("P-B__x8__mpp2.0000.png", self._png(ch))
        self.assertIn(texto, _lee(p))
        r = self._vision(p)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("PLTE", r.stdout)
        # C: el texto tras el fin del flujo zlib, dentro del último IDAT
        ch = self._chunks(_lee(self.png))
        k = max(i for i, (t, _d) in enumerate(ch) if t == b"IDAT")
        ch[k] = (b"IDAT", ch[k][1] + texto)
        p = self.colar_en_n1("P-C__x8__mpp2.0000.png", self._png(ch))
        r = self._vision(p)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("flujo IDAT", r.stdout)
        # D: el nombre escrito solo en el canal alfa (el OCR mira RGB)
        rgba = _textura(400, 300).convert("RGBA")
        alfa = Image.new("L", rgba.size, 255)
        ImageDraw.Draw(alfa).text((20, 120), "LEOCADIA QUINTANAR", font=_fuente(30), fill=0)
        rgba.putalpha(alfa)
        buf = io.BytesIO()
        rgba.save(buf, "PNG")
        p = self.colar_en_n1("P-D__x8__mpp2.0000.png", buf.getvalue())
        r = self._vision(p)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("RGB de 8 bits", r.stdout)
        # Control: el PNG que escribió exporta_n1 pasa la misma verificación.
        self.assertEqual(self._vision(self.png).returncode, 0)

    def _png_con_nombre_en_los_pixeles(self):
        """PNG RGB de forma válida (solo IHDR/IDAT/IEND) cuyo zlib va en bloques ALMACENADOS (nivel
        0) y con el nombre como valores de 10 píxeles: el nombre, en claro en los bytes."""
        import zlib
        w, h = 400, 300
        img = _textura(w, h)
        filas = [bytearray(img.crop((0, y, w, y + 1)).tobytes()) for y in range(h)]
        filas[150][30:30 + len(NOMBRE_SINTETICO)] = NOMBRE_SINTETICO
        crudo = b"".join(b"\x00" + bytes(f) for f in filas)
        datos = self._png([(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)),
                           (b"IDAT", zlib.compress(crudo, 0)), (b"IEND", b"")])
        self.assertIn(NOMBRE_SINTETICO, datos)
        return datos

    def test_png_colado_con_texto_en_claro_sale_recodificado(self):
        """Hallazgo N1 de la 3.ª pasada: el PNG de arriba pasaba `estructura_png_n1` y salía con el
        nombre en claro. Ahora sale su recodificación canónica (`png_canonico`): los mismos píxeles,
        otros bytes, y el nombre ya no está en claro en lo que se envía."""
        self.confia("vision-n1:gemini")
        p = self.colar_en_n1("P-V__x8__mpp2.0000.png", self._png_con_nombre_en_los_pixeles())
        v = self.codigo("import vision_n1 as v, hashlib\nplan = v.preparar('vision-n1:gemini', 'Ki67', [%r])\n"
                        "f = plan['ficheros'][0]\n"
                        "print(json.dumps([%r in f['datos'], f['sha256'] == hashlib.sha256(open(%r, 'rb')"
                        ".read()).hexdigest(), f['sha256_enviado'] == hashlib.sha256(f['datos']).hexdigest(),"
                        " f['datos'] == open(%r, 'rb').read()]))"
                        % (p, NOMBRE_SINTETICO, p, p))
        self.assertEqual(v, [False, True, True, False])
        # el PNG que escribió exporta_n1 ya es canónico: sale con sus mismos bytes
        v = self.codigo("import vision_n1 as v\nplan = v.preparar('vision-n1:gemini', 'Ki67', [%r])\n"
                        "print(json.dumps(plan['ficheros'][0]['datos'] == open(%r, 'rb').read()))"
                        % (self.png, self.png))
        self.assertIs(v, True)

    def test_limite_declarado_texto_en_valores_de_pixel(self):
        """LÍMITE DECLARADO (exporta_n1, cabecera): lo que va en los VALORES de los píxeles es la
        imagen y sale con ella (aquí, 10 píxeles cuyos bytes RGB son el nombre). La recodificación
        lo quita de los bytes en claro, no de la imagen decodificada; un fichero de puro ruido
        hasta puede salir en bloques zlib almacenados. Es esteganografía de un agente que fabrica
        el fichero a propósito: obstáculo y rastro, no frontera."""
        self.confia("vision-n1:gemini")
        p = self.colar_en_n1("P-W__x8__mpp2.0000.png", self._png_con_nombre_en_los_pixeles())
        v = self.codigo("import vision_n1 as v, io\nfrom PIL import Image\n"
                        "plan = v.preparar('vision-n1:gemini', 'Ki67', [%r])\n"
                        "rgb = Image.open(io.BytesIO(plan['ficheros'][0]['datos'])).tobytes()\n"
                        "print(json.dumps(%r in rgb))" % (p, NOMBRE_SINTETICO))
        self.assertIs(v, True)

    def test_nombre_de_fichero_con_identidad_no_sale(self):
        """Hallazgo N2 de la 3.ª pasada: el NOMBRE de un fichero colado en N1 (contenido limpio)
        salía, y en la nube es el nombre del objeto. Lista blanca de exporta_n1 + Puerta."""
        self.confia("vision-n1:gemini")
        limpio = _lee(self.png)
        for nombre in ("24B0001043.png", "Leocadia-Quintanar__x8.png", "P-HE__x8.png",
                       "P-24B0001043__x8__mpp2.0000.png", "P-QUINTANAR__thumbnail.png"):
            p = self.colar_en_n1(nombre, limpio)
            r = self._vision(p)
            self.assertEqual(r.returncode, 3, "%s → %s" % (nombre, r.stdout + r.stderr))
            self.assertIn("nombre N1", r.stdout)
            self.assertNotIn("Leocadia", r.stdout)
            v = self.codigo("import exporta_n1 as e, hashlib\ntry:\n"
                            "    e.revalidar_n1(%r, hashlib.sha256(open(%r, 'rb').read()).hexdigest())\n"
                            "    print(json.dumps('SALE'))\nexcept e.PuertaCerrada as x:\n"
                            "    print(json.dumps(str(x)))" % (p, p))
            self.assertIn("nombre N1", v)
        self.assertEqual(self._vision(self.png).returncode, 0)          # el opaco de exporta_n1

    def test_cola_truncada_no_abre(self):
        """Residuo del hallazgo 22 (2-oct-26): `verificar_cadena` no comparaba la cola con
        head.txt, así que borrar la ÚLTIMA línea (un revoke_cloud reciente) dejaba la cadena
        «íntegra» y la confianza abierta."""
        self.confia("vision-n1:gemini")
        self.sella({"evento": "revoke_cloud", "destino": "vision-n1:gemini", "nivel": "info"})
        d = os.path.join(self.casa_tools, "state", "borde")
        led = os.path.join(d, sorted(f for f in os.listdir(d) if f.startswith("ledger-"))[-1])
        lineas = _lee(led, "r").splitlines()
        self.assertEqual(json.loads(lineas[-1])["evento"], "revoke_cloud")
        with open(led, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lineas[:-1]) + "\n")
        r = self._vision(self.png)
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertRegex(r.stdout, r"no está íntegra|no está confiado")
        v = self.codigo("import puerta_n1 as pu\npu.borde.es_trusted = lambda d: True\n"
                        "try:\n    pu.exigir_confianza('vision-n1:gemini', 'vision-n1:')\n"
                        "    print(json.dumps('ABRE'))\nexcept pu.PuertaCerrada as e:\n"
                        "    print(json.dumps(str(e)))")
        self.assertIn("cola", v)

    def test_gemini_a_secas_tamano_y_numero(self):
        self.confia("vision-n1:gemini")
        self.confia("gemini")
        r = self._vision(self.png, destino="gemini")
        self.assertEqual(r.returncode, 3, r.stdout)
        grande = os.path.join(self.n1, "P-G__x8__mpp2.0000.png")    # nombre opaco (3.ª pasada)
        buf = io.BytesIO()
        _textura(1600, 400).save(buf, "PNG")
        self.colar_en_n1("P-G__x8__mpp2.0000.png", buf.getvalue())
        r = self._vision(grande)
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("1568", r.stdout)
        r = self._vision(*([self.png] * 21))
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("20", r.stdout)
        self.assertEqual(self._vision(*([self.png] * 20)).returncode, 0)

    def test_prompt(self):
        self.confia("vision-n1:gemini")
        self.assertEqual(self._vision(self.png, prompt="Ki67 hotspots: check the mask").returncode, 0)
        for malo in ("Patient Leocadia Quintanar", "block 31B0002468", "scanned 2026-04-30"):
            r = self._vision(self.png, prompt=malo)
            self.assertEqual(r.returncode, 3, malo + " → " + r.stdout)
            self.assertIn("prompt", r.stdout)

    def _gemini(self, src, env=None):
        return self.codigo(GEMINI_FALSO + src, env=env)

    def _envia(self, rutas, env=None, antes="", **kw):
        return self._gemini(antes + "print(json.dumps(envia(%r%s)))" % (
            list(rutas), "".join(", %s=%r" % kv for kv in kw.items())), env=env)

    def test_enviar_sin_condiciones_de_la_auditoria_no_sale(self):
        """Condición de la auditoría (2-oct-26): sin `condiciones_cumplidas: true` para Gemini en el
        auditorias.json de CASA BASE no sale nada, aunque haya trust-cloud tecleado, clave y
        transporte. Un true en el árbol (worktree) no basta. Ni aviso, ni sello, ni llamada."""
        self.confia("vision-n1:gemini")
        r = self._enviar(self.png)                      # CLI: la casa base falsa no tiene auditoría
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("auditoría", r.stdout)
        for kw in (dict(cumplidas=False), dict(cumplidas="true"), dict(cumplidas=None),
                   dict(modelo="gemini-2.5-pro"), dict(veredicto="no apto")):
            self.condiciones(**kw)
            v = self._envia([self.png])
            self.assertTrue(v["r"].startswith("CERRADA"), (kw, v["r"]))
            self.assertEqual(v["llamadas"], [], kw)
        self.condiciones(cumplidas=False)
        self.condiciones(donde=self.copia)               # el árbol dice true; casa base, no
        v = self._envia([self.png])
        self.assertIn("condiciones_cumplidas", v["r"])
        self.assertEqual(v["llamadas"], [])
        self.assertEqual(self.avisos(), [])
        self.assertEqual(self.eventos("vision_n1_envio"), [])

    def test_gemini_envia_con_todas_las_guardas_y_en_orden(self):
        """Con confianza tecleada, condiciones cumplidas y clave: UNA petición generateContent a
        gemini-3.1-pro-preview con el PNG canónico inline, sin nada más en el cuerpo (ni tools, ni
        caché, ni systemInstruction, ni temperatura), media_resolution alta; el aviso del primer
        envío ANTES del sello y el sello ANTES de la llamada; la clave solo en su cabecera."""
        self.confia("vision-n1:gemini")
        self.condiciones()
        v = self._envia([self.png])
        self.assertEqual(v["r"], '{"hay_error": false, "tipo": null, "cuadrante": null}')
        (ll,) = v["llamadas"]
        self.assertEqual(ll["url"], "https://generativelanguage.googleapis.com/v1beta/models/"
                                    "gemini-3.1-pro-preview:generateContent")
        self.assertEqual(ll["cab"], {"Content-Type": "application/json",
                                     "x-goog-api-key": "clave-falsa-GEMINI-0123456789"})
        self.assertEqual(set(ll["cuerpo"]), {"contents", "generationConfig"})
        self.assertEqual(ll["cuerpo"]["generationConfig"], {"mediaResolution": "MEDIA_RESOLUTION_HIGH",
                                                            "responseMimeType": "application/json"})
        (cont,) = ll["cuerpo"]["contents"]
        img, txt = cont["parts"]
        self.assertEqual(img["inlineData"]["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(img["inlineData"]["data"]), _lee(self.png))   # canónico
        self.assertEqual(txt, {"text": "Review the Ki67 overlay"})
        self.assertEqual(ll["sellados"], 1)                         # sellado ANTES de la llamada
        self.assertEqual(len(self.avisos()), 1)
        seq = {}
        for e in self.eventos():
            seq.setdefault(e["evento"], e["seq"])
        self.assertLess(seq["n1_aviso_primer_envio"], seq["vision_n1_envio"])
        self.assertLess(seq["vision_n1_envio"], seq["vision_n1_respuesta"])
        env = self.eventos("vision_n1_envio")[-1]
        self.assertEqual((env["proveedor"], env["modelo"]), ("gemini", "gemini-3.1-pro-preview"))
        self.assertEqual(env["sha256_enviado"], [hashlib.sha256(_lee(self.png)).hexdigest()])
        self.assertEqual(self.eventos("vision_n1_respuesta")[-1]["uso"]["totalTokenCount"], 1612)
        cadena = json.dumps(self.eventos())
        self.assertNotIn("clave-falsa-GEMINI", cadena)
        self.assertNotIn("hay_error", cadena)                       # ni el texto de la respuesta
        self._envia([self.png])
        self.assertEqual(len(self.avisos()), 1)                     # el segundo no avisa
        self.assertEqual(len(self.eventos("vision_n1_envio")), 2)

    # ── Claude por API (8-oct-26) ──
    CLAUDE_OK = ("T = Transporte(crudo=_j.dumps({'type': 'message', 'model': 'claude-opus-5-5', "
                 "'stop_reason': 'end_turn', 'content': [{'type': 'thinking', 'thinking': ''}, "
                 "{'type': 'text', 'text': RESPUESTA}], 'usage': {'input_tokens': 1480, "
                 "'output_tokens': 310}}).encode())\n")

    def _claude(self, rutas, antes=None, **kw):
        return self._envia(rutas, antes=self.CLAUDE_OK if antes is None else antes,
                           destino="vision-n1:claude", **kw)

    def test_claude_envia_con_todas_las_guardas_y_en_orden(self):
        """Con confianza tecleada, la auditoría de `claude` cumplida y clave: UNA petición a Messages
        con el modelo fijado, el PNG canónico en un bloque image base64 y el prompt al final; nada
        más en el cuerpo (ni system, ni tools, ni fallbacks, ni thinking); la clave solo en
        x-api-key; el aviso antes del sello y el sello antes de la llamada; ni clave ni texto en la
        cadena."""
        self.confia("vision-n1:claude")
        self.condiciones(proveedor="claude", modelo="claude-opus-5-5")
        v = self._claude([self.png])
        self.assertEqual(v["r"], '{"hay_error": false, "tipo": null, "cuadrante": null}')
        (ll,) = v["llamadas"]
        self.assertEqual(ll["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(ll["cab"], {"Content-Type": "application/json", "anthropic-version": "2023-06-01",
                                     "x-api-key": "clave-falsa-GEMINI-0123456789"})
        self.assertEqual(set(ll["cuerpo"]), {"model", "max_tokens", "output_config", "messages"})
        self.assertEqual(ll["cuerpo"]["model"], "claude-opus-5-5")
        self.assertEqual(ll["cuerpo"]["output_config"], {"effort": "high"})
        (msg,) = ll["cuerpo"]["messages"]
        self.assertEqual(msg["role"], "user")
        img, txt = msg["content"]
        self.assertEqual((img["type"], img["source"]["type"], img["source"]["media_type"]),
                         ("image", "base64", "image/png"))
        self.assertEqual(base64.b64decode(img["source"]["data"]), _lee(self.png))       # canónico
        self.assertEqual(txt, {"type": "text", "text": "Review the Ki67 overlay"})
        self.assertEqual(ll["sellados"], 1)                         # sellado ANTES de la llamada
        self.assertEqual(len(self.avisos()), 1)
        seq = {}
        for e in self.eventos():
            seq.setdefault(e["evento"], e["seq"])
        self.assertLess(seq["n1_aviso_primer_envio"], seq["vision_n1_envio"])
        self.assertLess(seq["vision_n1_envio"], seq["vision_n1_respuesta"])
        env = self.eventos("vision_n1_envio")[-1]
        self.assertEqual((env["proveedor"], env["modelo"], env["endpoint"]),
                         ("claude", "claude-opus-5-5", "messages"))
        self.assertEqual(self.eventos("vision_n1_respuesta")[-1]["uso"],
                         {"input_tokens": 1480, "output_tokens": 310})
        cadena = json.dumps(self.eventos())
        self.assertNotIn("clave-falsa", cadena)
        self.assertNotIn("hay_error", cadena)

    def test_claude_sin_su_auditoria_o_con_otro_modelo_no_sale(self):
        """El adaptador escrito no abre nada por sí solo: la auditoría de Gemini no vale para
        Claude, ni una de Claude hecha para otro modelo, ni una sin condiciones cumplidas; y el
        modelo va fijado."""
        self.confia("vision-n1:claude")
        self.condiciones()                                           # solo Gemini
        self.assertIn("sin auditoría", self._claude([self.png])["r"])
        self.condiciones(proveedor="claude", modelo="claude-opus-5")
        self.assertIn("hecha para", self._claude([self.png])["r"])
        self.condiciones(proveedor="claude", modelo="claude-opus-5-5", cumplidas=False)
        self.assertIn("condiciones_cumplidas", self._claude([self.png])["r"])
        self.condiciones(proveedor="claude", modelo="claude-opus-5-5")
        v = self._claude([self.png], modelo="claude-sonnet-5-5")
        self.assertIn("fijado", v["r"])
        self.assertEqual(v["llamadas"], [])
        self.assertEqual((self.avisos(), self.eventos("vision_n1_envio")), ([], []))

    def test_claude_negativa_corte_y_error_http_son_errores_sin_clave(self):
        """`refusal` y `max_tokens` no devuelven texto (fail-closed, sin relevo a otro modelo); un
        error HTTP que repite la clave sale limpio; todo queda sellado como respuesta con error."""
        self.confia("vision-n1:claude")
        self.condiciones(proveedor="claude", modelo="claude-opus-5-5")
        casos = [("{'stop_reason': 'refusal', 'stop_details': {'type': 'refusal', 'category': 'bio'}, "
                  "'content': []}", 200, "declinó la petición (bio)"),
                 ("{'stop_reason': 'max_tokens', 'content': [{'type': 'text', 'text': 'a medias'}]}",
                  200, "cortada por max_tokens"),
                 ("{'stop_reason': 'end_turn', 'content': [{'type': 'thinking', 'thinking': 'x'}]}",
                  200, "sin texto"),
                 ("{'type': 'error', 'error': {'type': 'authentication_error', "
                  "'message': 'invalid x-api-key ' + CLAVE}}", 401, "Claude HTTP 401")]
        for cuerpo, status, motivo in casos:
            v = self._claude([self.png], antes="T = Transporte(status=%d, crudo=_j.dumps(%s).encode())\n"
                             % (status, cuerpo))
            self.assertTrue(v["r"].startswith("ERROR"), (motivo, v["r"]))
            self.assertIn(motivo, v["r"])
            self.assertNotIn("clave-falsa", v["r"])
            self.assertEqual(len(v["llamadas"]), 1, motivo)
        self.assertNotIn("clave-falsa", json.dumps(self.eventos()))
        self.assertEqual(len([e for e in self.eventos("vision_n1_respuesta") if e.get("error")]), len(casos))

    def test_claude_lee_la_clave_cedida_y_nunca_la_de_ia(self):
        """El servicio del Llavero es el de la clave cedida; el adaptador no nombra la de ia.py."""
        v = self.codigo("import vision_n1 as V\n"
                        "print(json.dumps([V.ADAPTADORES['claude']['servicio'], V.ADAPTADORES['claude']['host'],"
                        " V.ADAPTADORES['claude']['cabecera_clave'], sorted(V.HOSTS)]))")
        self.assertEqual(v, ["btp-anthropic-api-prestada", "api.anthropic.com", "x-api-key",
                             ["api.anthropic.com", "generativelanguage.googleapis.com"]])

    def test_sin_confianza_sin_clave_o_sin_aviso_no_sale(self):
        self.condiciones()
        v = self._envia([self.png])
        self.assertIn("no está confiado", v["r"])
        self.assertEqual(v["llamadas"], [])
        self.confia("vision-n1:gemini")
        v = self._envia([self.png], sin_clave=True)                 # servicio de test inexistente
        self.assertIn("sin clave", v["r"])
        self.assertEqual(v["llamadas"], [])
        env = dict(self.env)
        env.pop("BTP_VISION_SERVICIO_CLAVE")
        v = self._envia([self.png], env=env, sin_clave=True)        # en la batería, la real ni se busca
        self.assertIn("no se lee", v["r"])
        self.assertEqual(v["llamadas"], [])
        self.assertEqual((self.avisos(), self.eventos("vision_n1_envio")), ([], []))
        v = self._envia([self.png], env=dict(self.env, BTP_TEST_SALIDA_MODO="falla"))
        self.assertIn("no pude avisar", v["r"])                     # aviso no aceptado: no sale
        self.assertEqual(v["llamadas"], [])
        v = self._envia([self.png], avisar=False)                   # librería sin avisar: opt-in
        self.assertIn("opt-in", v["r"])
        self.assertEqual(v["llamadas"], [])
        self.assertEqual(self.eventos("vision_n1_envio"), [])

    def test_png_no_canonico_nombre_no_opaco_modelo_y_tipo_no_salen(self):
        self.confia("vision-n1:gemini")
        self.confia("vision-n1:claude")
        self.confia("vision-n1:qwen")
        self.condiciones()
        from PIL import PngImagePlugin
        info = PngImagePlugin.PngInfo()
        info.add_text("Author", "Leocadia Quintanar")
        buf = io.BytesIO()
        _textura(400, 300).save(buf, "PNG", pnginfo=info)
        csv_ok = self.fichero("ok.csv", "marker,value\nRE,85.2\n")
        self.assertEqual(self.corre("exporta_n1.py", "texto", csv_ok, "--nombre", "P-KI67-t.csv").returncode, 0)
        casos = [([self.colar_en_n1("P-X__x8__mpp2.0000.png", buf.getvalue())], {}, "chunk"),
                 ([self.colar_en_n1("24B0001043.png", _lee(self.png))], {}, "nombre N1"),
                 ([self.png], {"modelo": "gemini-2.5-pro"}, "fijado"),
                 ([self.png], {"destino": "vision-n1:claude"}, "sin auditoría"),   # adaptador sí; auditoría no
                 ([self.png], {"destino": "vision-n1:qwen"}, "sin escribir"),
                 ([os.path.join(self.n1, "P-KI67-t.csv")], {}, "solo van PNG")]
        for rutas, kw, motivo in casos:
            v = self._envia(rutas, **kw)
            self.assertIn(motivo, v["r"], (motivo, v["r"]))
            self.assertNotIn("Leocadia", v["r"])
            self.assertEqual(v["llamadas"], [], motivo)
        self.assertEqual((self.avisos(), self.eventos("vision_n1_envio")), ([], []))
        # PNG de forma válida con el nombre en claro en bloques zlib almacenados: sale RECODIFICADO
        p = self.colar_en_n1("P-V__x8__mpp2.0000.png", self._png_con_nombre_en_los_pixeles())
        v = self._envia([p])
        (ll,) = v["llamadas"]
        enviado = base64.b64decode(ll["cuerpo"]["contents"][0]["parts"][0]["inlineData"]["data"])
        self.assertNotEqual(enviado, _lee(p))
        self.assertNotIn(NOMBRE_SINTETICO, enviado)

    def test_peticion_grande_error_y_bloqueo_del_proveedor(self):
        self.confia("vision-n1:gemini")
        self.condiciones()
        v = self._envia([self.png], antes="vision_n1.ADAPTADORES['gemini']['max_peticion'] = 1000\n")
        self.assertIn("pasa de 1000", v["r"])
        self.assertEqual((v["llamadas"], self.avisos()), ([], []))
        eco = json.dumps({"error": {"message": "API key clave-falsa-GEMINI-0123456789 not valid"}}).encode()
        v = self._envia([self.png], antes="T = Transporte(400, %r)\n" % eco)
        self.assertTrue(v["r"].startswith("ERROR Gemini HTTP 400"), v["r"])
        self.assertNotIn("clave-falsa-GEMINI", v["r"])
        self.assertEqual(len(v["llamadas"]), 1)                     # salió y se selló
        self.assertEqual(len(self.eventos("vision_n1_envio")), 1)
        self.assertNotIn("clave-falsa-GEMINI", json.dumps(self.eventos()))
        bloqueo = json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}).encode()
        v = self._envia([self.png], antes="T = Transporte(200, %r)\n" % bloqueo)
        self.assertIn("bloqueó", v["r"])

    def test_transporte_real_prohibido_en_tests_y_solo_a_su_host(self):
        src = ("import vision_n1\ntry:\n    vision_n1.transporte_https(%r, b'', {})\n    print(json.dumps('ABRE'))\n"
               "except vision_n1.PuertaCerrada as e:\n    print(json.dumps(str(e)))")
        self.assertIn("batería de tests", self.codigo(src % "https://generativelanguage.googleapis.com/x"))
        env = dict(self.env)
        env.pop("BTP_TEST_BATTERY")
        for url in ("https://evil.example.com/v1", "http://generativelanguage.googleapis.com/x"):
            self.assertIn("no permitido", self.codigo(src % url, env=env))

    def test_primer_envio_avisa_a_titular_una_vez(self):
        """El primer envío a cada destino avisa por salida.py (aquí, el falso del test); el
        segundo no. Con HALT, ni aviso ni envío. Por librería sin `avisar`, no sale callado."""
        self.confia("vision-n1:gemini")
        self.condiciones()
        v = self._envia([self.png], avisar=False)
        self.assertIn("opt-in", v["r"])
        self.assertEqual(self.avisos(), [])
        for _ in range(2):
            self.assertFalse(self._envia([self.png])["r"].startswith("CERRADA"))
        avisos = self.avisos()
        self.assertEqual(len(avisos), 1, avisos)
        self.assertIn("vision-n1:gemini", avisos[0]["texto"])
        self.assertNotIn("Leocadia", avisos[0]["texto"])
        self.confia("vision-n1:gemini")                      # re-confiado: vuelve a avisar
        with open(self.halt, "w") as fh:
            fh.write("1")
        v = self._envia([self.png])
        self.assertIn("HALT", v["r"])
        self.assertEqual((v["llamadas"], len(self.avisos())), ([], 1))
        os.remove(self.halt)
        self.assertFalse(self._envia([self.png])["r"].startswith("CERRADA"))
        self.assertEqual(len(self.avisos()), 2)

    def test_gemini_py_sigue_denegando_marcador(self):
        fuente = _lee(os.path.join(TOOLS, "gemini.py"), "r")
        self.assertIn('borde.guard_cli(q, "gemini")', fuente)
        v = self.codigo("import borde\nprint(json.dumps(borde.guard_cli('Ki67 al 40% en la lamina', 'gemini')))")
        self.assertIs(v, False)


class Nube(Base):

    FALSO = r'''
class Falso:
    def __init__(self, pegajoso=False):
        self.obj = {"P-HE.n1.tif": b"x", "resultado.geojson": b"{}"}
        self.inst, self.vol, self.claves, self.pegajoso = ["i1"], ["v1"], ["k1"], pegajoso
        self.subidos = []
    def listar(self):
        return {"instancias": list(self.inst), "volumenes": list(self.vol),
                "objetos": sorted(self.obj), "claves": list(self.claves)}
    def borrar_instancias(self): self.inst = []
    def borrar_volumenes(self): self.vol = []
    def borrar_objeto(self, o):
        if not (self.pegajoso and o == "P-HE.n1.tif"):
            self.obj.pop(o)
    def borrar_bucket(self): pass
    def borrar_claves(self): self.claves = []
    def bajar(self, o): return b'{"type": "FeatureCollection", "features": []}'
    def subir(self, nombre, datos):
        import hashlib
        self.subidos.append([nombre, hashlib.sha256(datos).hexdigest()])

def sube(rutas, avisar=True):
    f = Falso()
    try:
        r = nube_n1.subir('scaleway', rutas, backend=f, avisar=avisar)
    except nube_n1.PuertaCerrada as e:
        r = 'CERRADA ' + str(e)
    return [r, f.subidos]
'''

    def test_lanzar_sin_confianza_aborta(self):
        r = self.corre("nube_n1.py", "lanzar")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("no está confiado", r.stdout)

    # ── Scaleway simulado (SCALEWAY_FALSO): ni una llamada real ─────────────────────────────────
    MAPA = {"nucls_main": {"tumor": "neoplastic", "lymphocyte": "inflammatory"},
            "panoptils": {"tumor": "neoplastic", "lymphocyte": "inflammatory"}}

    def _sim(self, src, env=None):
        return self.codigo(SCALEWAY_FALSO + src, env=env)

    def sella_config(self, **cambios):
        """config.json SELLADO con valores de prueba (en el árbol copiado, nunca en el real)."""
        ruta = os.path.join(self.copia, "nube_n1_cloudinit", "config.json")
        cfg = json.load(open(ruta, encoding="utf-8"))
        s = cfg["scaleway"]
        s["imagen_gpu"] = {"id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "nombre": "prueba", "sellada": "2026-10-02"}
        s["docker"]["digest"] = "sha256:" + "a" * 64
        for ck in s["checkpoints"]:
            ck["sha256"] = hashlib.sha256(ck["nombre"].encode()).hexdigest()
        s["clasificadores"] = sorted(self.MAPA)
        s["mapa_clases"] = dict(self.MAPA)
        s.update(cambios)
        self._json(ruta, cfg)

    def prepara_he(self, mpp=0.2506):
        """P-HE.n1.tif «ya subido»: en N1 con su mpp en el manifiesto y su `nube_n1_subir` sellado
        (lanzar no re-valida el TIFF: lo hizo `subir`; el flujo entero va en su propio caso)."""
        datos = b"II*\x00" + b"\x00" * 64
        self.colar_en_n1("P-HE.n1.tif", datos)
        man_p = os.path.join(self.n1, "manifiesto.json")
        man = json.load(open(man_p))
        man["ficheros"]["P-HE.n1.tif"].update(mpp=mpp, niveles=[{"ancho": 50000, "alto": 40000, "factor": 1.0}])
        self._json(man_p, man)
        sha = hashlib.sha256(datos).hexdigest()
        self.assertEqual(self.sella({"evento": "nube_n1_subir", "destino": "nube-n1:scaleway", "nivel": "info",
                                     "objeto": "P-HE.n1.tif", "sha256": sha, "sha256_enviado": sha}), "SELLADO")
        return datos

    def test_sin_clave_en_el_llavero_no_llama(self):
        self.confia("nube-n1:scaleway")
        r = self.corre("nube_n1.py", "listar")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("sin cuenta", r.stdout)                         # primero, la cuenta
        self.cuenta(proyecto=None)                                    # como hoy: sin project_id
        r = self.corre("nube_n1.py", "listar")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("sin project_id", r.stdout)
        for extra, motivo in (({"zona": "fr-par-1"}, "fijada"), ({"llavero_secret": "otra-clave"}, "única")):
            self.cuenta(**extra)
            r = self.corre("nube_n1.py", "listar")
            self.assertIn(motivo, r.stdout)
        self.cuenta()
        r = self.corre("nube_n1.py", "listar")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("sin clave", r.stdout)
        env = dict(self.env)
        env.pop("BTP_NUBE_SERVICIO_CLAVE")
        r = self.corre("nube_n1.py", "listar", env=env)               # en la batería, la real ni se busca
        self.assertIn("no se lee", r.stdout)

    def test_clave_solo_al_usarla_y_cuenta_sin_secretos(self):
        """La clave se pide en la PRIMERA llamada (no al abrir, no en `cuenta`), y el transporte
        real se niega en la batería. `cuenta` solo actualiza access key y proyecto, con forma, en
        tools/state/nube/scaleway.json de casa base, sin pisar zona, llavero_secret ni nota."""
        self.cuenta(proyecto=None)
        r = self.corre("nube_n1.py", "cuenta", "--proyecto", "11111111-2222-4333-8444-555555555555")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        ruta = os.path.join(self.casa_tools, "state", "nube", "scaleway.json")
        self.assertEqual(oct(os.stat(ruta).st_mode & 0o777), "0o600")
        self.assertEqual(json.load(open(ruta)), {"access_key": "SCWAAAAAAAAAAAAAAAAA", "zona": "fr-par-2",
                                                 "project_id": "11111111-2222-4333-8444-555555555555",
                                                 "llavero_secret": "btp-scaleway-api", "nota": "no secreta"})
        for malo in (["--access-key", "sk-secreto", "--proyecto", "11111111-2222-4333-8444-555555555555"],
                     ["--access-key", "SCWAAAAAAAAAAAAAAAAA", "--proyecto", "proyecto"]):
            self.assertEqual(self.corre("nube_n1.py", "cuenta", *malo).returncode, 3)
        self.confia("nube-n1:scaleway")
        v = self.codigo("import nube_n1\nn = []\n"
                        "def clave(p):\n    n.append(p)\n    return 'secreto-falso'\n"
                        "nube_n1._clave = clave\n_d, _e, be = nube_n1._abrir('scaleway')\nantes = len(n)\n"
                        "try:\n    be.listar()\n    r = 'LLAMÓ'\nexcept nube_n1.PuertaCerrada as e:\n    r = str(e)\n"
                        "print(json.dumps([antes, len(n), r]))")
        self.assertEqual(v[:2], [0, 1])
        self.assertIn("transporte real está prohibido", v[2])

    def test_config_fija_zona_region_y_tipo(self):
        for cambio in ({"zona": "fr-par-1"}, {"region_s3": "nl-ams"}, {"tipo": "H100-1-80G"}):
            self.sella_config(**cambio)
            v = self.codigo("import nube_n1\ntry:\n    nube_n1.carga_config()\n    print(json.dumps('ABRE'))\n"
                            "except nube_n1.PuertaCerrada as e:\n    print(json.dumps(str(e)))")
            self.assertIn("fijado en el código", v, cambio)

    def test_firma_sigv4_contra_los_vectores_publicados_por_aws(self):
        """Los tres ejemplos de la documentación de AWS SigV4 (get-vanilla, GET de S3 con Range y
        URL prefirmada de S3): la firma es exacta, no solo de forma."""
        v = self.codigo(
            "import nube_n1 as n\nE = n._sha_hex(b'')\nprint(json.dumps([\n"
            " n.firma_v4('GET', 'example.amazonaws.com', '/', [], {'X-Amz-Date': '20150830T123600Z'}, E,"
            " 'AKIDEXAMPLE', 'wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY', 'us-east-1', 'service', '20150830T123600Z'),\n"
            " n.firma_v4('GET', 'examplebucket.s3.amazonaws.com', '/test.txt', [], {'Range': 'bytes=0-9',"
            " 'x-amz-content-sha256': E, 'x-amz-date': '20130524T000000Z'}, E, 'AKIAIOSFODNN7EXAMPLE',"
            " 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY', 'us-east-1', 's3', '20130524T000000Z'),\n"
            " n.presigna_v4('GET', 'examplebucket.s3.amazonaws.com', '/test.txt', 'AKIAIOSFODNN7EXAMPLE',"
            " 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY', 'us-east-1', 's3', '20130524T000000Z', 86400)]))")
        self.assertTrue(v[0].endswith("Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"))
        self.assertTrue(v[1].endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"))
        self.assertIn("SignedHeaders=host;range;x-amz-content-sha256;x-amz-date", v[1])
        self.assertTrue(v[2].endswith("X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"))

    def test_transporte_real_prohibido_en_tests_y_solo_a_scaleway(self):
        src = ("import nube_n1\ntry:\n    nube_n1.transporte_https('GET', %r, None, {})\n    print(json.dumps('ABRE'))\n"
               "except nube_n1.PuertaCerrada as e:\n    print(json.dumps(str(e)))")
        self.assertIn("batería de tests", self.codigo(src % "https://api.scaleway.com/instance/v1"))
        env = dict(self.env)
        env.pop("BTP_TEST_BATTERY")
        for url in ("https://evil.example.com/", "http://api.scaleway.com/x", "https://s3.fr-par.scw.cloud.evil.com/"):
            self.assertIn("no permitido", self.codigo(src % url, env=env))

    def test_subir_crea_bucket_privado_y_cifrado(self):
        """Primera subida: bucket privado, cifrado AES256 por defecto comprobado y sin versionado;
        el objeto con su cabecera de cifrado; firma SigV4; la clave nunca hacia S3. Si el
        proveedor no deja cifrar, el bucket vacío se borra y no sube nada."""
        self.confia("nube-n1:scaleway")
        bueno = self._csv_en_n1()
        v = self._sim("sim = Sim()\nbe = backend(sim)\nr = intenta(nube_n1.subir, 'scaleway', [%r], backend=be, avisar=True)\n"
                      "print(json.dumps([r, {b: sorted(d['obj']) for b, d in sim.buckets.items()},"
                      " [d['cifrado'] for d in sim.buckets.values()], be.bucket]))" % bueno)
        r, buckets, cifrado, bucket = v
        self.assertEqual(r, ["P-KI67-t.csv"])
        self.assertEqual(buckets, {bucket: ["P-KI67-t.csv"]})
        self.assertEqual(cifrado, ["AES256"])
        self.assertRegex(bucket, r"^btp-n1-[0-9a-f]{16}$")
        v = self._sim("sim = Sim(sin_cifrado=True)\nr = intenta(nube_n1.subir, 'scaleway', [%r], backend=backend(sim),"
                      " avisar=True)\nprint(json.dumps([r, sim.buckets]))" % bueno)
        self.assertIn("cifrado", v[0])
        self.assertEqual(v[1], {})

    def desella_config(self):
        """config.json SIN sellar en el árbol copiado: el real está sellado desde 49d401d (2-oct),
        así que este caso ya no puede apoyarse en que el real esté vacío."""
        ruta = os.path.join(self.copia, "nube_n1_cloudinit", "config.json")
        cfg = json.load(open(ruta, encoding="utf-8"))
        s = cfg["scaleway"]
        s["imagen_gpu"]["id"] = None
        s["docker"]["digest"] = None
        for ck in s["checkpoints"]:
            ck["sha256"] = None
        s["mapa_clases"] = None
        self._json(ruta, cfg)

    def test_config_real_sellada_pasa_la_comprobacion(self):
        """El config.json REAL (el que viajaría) pasa `exigir_sellada`: un pin o un sha256 roto
        al tocar la receta se ve aquí, no con la máquina encendida y pagando."""
        v = self._sim("import json as _j\ncfg = _j.load(open(%r, encoding='utf-8'))['scaleway']\n"
                      "print(json.dumps(intenta(nube_n1.exigir_sellada, cfg)))"
                      % os.path.join(TOOLS, "nube_n1_cloudinit", "config.json"))
        self.assertIsInstance(v, list, v)
        self.assertEqual(sorted(v), ["nucls_main", "nucls_super", "panoptils"])

    def test_lanzar_sin_sellar_no_crea_nada(self):
        self.confia("nube-n1:scaleway")
        self.desella_config()
        self.prepara_he()
        v = self._sim("sim = Sim()\nr = intenta(nube_n1.lanzar, 'scaleway', backend=backend(sim))\n"
                      "print(json.dumps([r, [x for x in sim.llamadas if x[1] == 'api.scaleway.com']]))")
        self.assertIn("sin sellar", v[0])
        for falta in ("imagen_gpu.id", "docker", "checkpoint", "mapa_clases"):
            self.assertIn(falta, v[0])
        self.assertEqual(v[1], [])

    def test_lanzar_hace_el_trabajo_y_borra_la_maquina(self):
        """L40S en fr-par-2 desde la imagen sellada, grupo con la entrada cerrada, cloud-init
        versionado con el digest, los pins, los sha256 y URLs prefirmadas (sin clave); el sello
        ANTES de crear nada; DONE; y la máquina BORRADA con su disco de arranque, su IP y su grupo.
        Después `bajar` valida el enganche que produjo el _a_enganche.py que viajó."""
        self.confia("nube-n1:scaleway")
        self.sella_config()
        datos = self.prepara_he()
        v = self._sim(
            "sim = Sim(al_encender=maquina('DONE'))\nbe = backend(sim)\n"
            "sim.buckets[be.bucket] = {'obj': {'P-HE.n1.tif': %r}, 'cifrado': 'AES256'}\n"
            "r = intenta(nube_n1.lanzar, 'scaleway', backend=be)\n"
            "(sid,) = sim.user_data\nscript, eng, mapa = partes_ci(sim, sid)\n"
            "b = intenta(nube_n1.bajar, 'scaleway', 'nucls_main', backend=be)\n"
            "print(json.dumps({'r': r, 'creado': sim.creado, 'sellos': sim.sellos_al_crear, 'ci': sim.user_data[sid],"
            " 'script': script, 'mapa': mapa, 'expira': sim.expira, 'srv': sim.srv, 'vol': sim.vol, 'ips': sim.ips,"
            " 'grupos': sim.grupos, 'obj': sorted(sim.buckets[be.bucket]['obj']), 'bajar': b}))" % datos)
        self.assertEqual(v["r"]["resultado"], "DONE", v["r"])
        self.assertTrue(v["r"]["maquina_borrada"])
        self.assertEqual((v["srv"], v["vol"], v["ips"], v["grupos"]), ({}, {}, {}, {}))
        self.assertEqual(v["creado"]["image"], "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
        self.assertIn("nube_n1_lanzar", v["sellos"])                       # sellado antes de crear
        ci, script = v["ci"], v["script"]
        self.assertTrue(ci.startswith("#cloud-config"))
        self.assertNotIn("@@", ci + script)
        self.assertNotIn("secreto-falso-scaleway", ci + script)
        self.assertIn("docker pull pytorch/pytorch@sha256:" + "a" * 64, script)
        for pin in ("cellvit==1.0.9", "numpy==1.26.4", "pydantic==1.10.22", "--no-binary=asciitree,pyturbojpeg",
                    "--network none", "--wsi_mpp 0.250600"):
            self.assertIn(pin, script)
        for ck in ("CellViT-SAM-H-x40-AMP.pth", "classifier.zip"):
            self.assertIn(hashlib.sha256(ck.encode()).hexdigest(), script)
        self.assertIn(hashlib.sha256(datos).hexdigest(), script)            # la entrada, con su sha
        self.assertEqual(v["mapa"], self.MAPA)
        self.assertEqual(set(v["expira"]), {25200})
        sh = self.fichero("ejecuta.sh", script)                          # el script rellenado, bash válido
        self.assertEqual(subprocess.run(["bash", "-n", sh], capture_output=True).returncode, 0)
        self.assertIn("P-HE.nucls_main.csv", v["obj"])
        self.assertIn("DONE", v["obj"])
        (rj, sj), (rc, sc) = v["bajar"]
        self.assertEqual(os.path.dirname(rj), os.path.join(os.path.realpath(self.sesion), "nube", "cellvitpp"))
        self.assertEqual(oct(os.stat(rc).st_mode & 0o777), "0o600")
        borrada = self.eventos("nube_n1_maquina_borrada")[-1]
        self.assertEqual((borrada["vacio"], borrada["resultado"]), (True, "DONE"))
        try:
            import numpy  # noqa: F401 — con el venv patologia: el validador de laminillas_he también lo acepta
        except ImportError:
            return
        w = self.codigo("import laminillas_he as H\nm = json.load(open(%r))\n"
                        "r = H.valida_cellvitpp(m, %r, 'nucls_main', (50000, 40000), 0.2506)\n"
                        "print(json.dumps(r['meta']))" % (rj, rc))
        self.assertEqual(w["clasificador"], "nucls_main")

    def test_lanzar_borra_la_maquina_con_failed_espera_agotada_o_caida(self):
        self.confia("nube-n1:scaleway")
        self.sella_config(espera_max_s=600, sondeo_s=60)
        datos = self.prepara_he()
        plantilla = ("sim = Sim(al_encender=%s%s)\nbe = backend(sim)\n"
                     "sim.buckets[be.bucket] = {'obj': {'P-HE.n1.tif': %r}, 'cifrado': 'AES256'}\n"
                     "try:\n    r = nube_n1.lanzar('scaleway', backend=be)\nexcept Exception as e:\n"
                     "    r = type(e).__name__ + ' ' + str(e)\n"
                     "print(json.dumps([r, sim.srv, sim.vol, sim.ips, sim.grupos]))")
        caida = ", falla_en=lambda m, p, _n=[]: m == 'GET' and p.path == '/DONE' and not _n and not _n.append(1)"
        for hook, extra, esperado in (("maquina('FAILED')", "", "FAILED"), ("None", "", "SIN_TERMINAR"),
                                      ("maquina('NADA')", caida, "RuntimeError")):
            r, srv, vol, ips, grupos = self._sim(plantilla % (hook, extra, datos))
            if isinstance(r, dict):
                self.assertEqual(r["resultado"], esperado)
                self.assertTrue(r["maquina_borrada"])
            else:
                self.assertTrue(r.startswith(esperado), r)
            self.assertEqual((srv, vol, ips, grupos), ({}, {}, {}, {}), esperado)
        r = self._sim(plantilla % ("maquina('FAILED')", "", datos))[0]
        self.assertIn("paso=docker", r["detalle"])
        self.assertNotIn("<", r["detalle"])                                  # texto externo saneado
        r, srv = self._sim(plantilla.replace("Sim(al_encender", "Sim(no_borra_servidor=True, al_encender")
                           % ("maquina('DONE')", "", datos))[:2]
        self.assertIn("NO pude borrar la máquina", r)
        self.assertEqual(len(srv), 1)
        self.assertEqual(self.eventos("nube_n1_maquina_borrada")[-1]["nivel"], "alarma")

    def test_lanzar_solo_lo_subido_y_permitido(self):
        self.confia("nube-n1:scaleway")
        self.sella_config()
        v = self._sim("sim = Sim()\nprint(json.dumps([intenta(nube_n1.lanzar, 'scaleway', backend=backend(sim)),"
                      " intenta(nube_n1.lanzar, 'scaleway', ['P-KI67.n1.tif'], backend=backend(sim))]))")
        self.assertIn("sin mpp", v[0])
        self.assertIn("permitidas", v[1])
        self.prepara_he()
        v = self._sim("sim = Sim()\nprint(json.dumps(intenta(nube_n1.lanzar, 'scaleway', backend=backend(sim))))")
        self.assertIn("no está subido", v)

    def test_bajar_solo_a_sesion_nube_y_valida_el_enganche(self):
        """Destino fuera de SESION/nube/: ni una llamada. Un par roto (sha, versión, mapa, campos,
        n, prob, clase sin mapa, coordenadas fuera de la lámina): no se escribe nada."""
        self.confia("nube-n1:scaleway")
        self.prepara_he()
        for d in (self.sesion, os.path.join(self.tmp, "otro"), os.path.join(self.sesion, "nube", "..")):
            r = self.corre("nube_n1.py", "bajar", "nucls_main", "--destino-dir", d)
            self.assertEqual(r.returncode, 3, r.stdout)
            self.assertIn("SESION/nube", r.stdout)
        base = ("ns = {'__name__': 'x'}\nexec(open(%r).read(), ns)\n"
                "datos, meta = ns['convierte'](CELLS, 'nucls_main', 0.2506, %r, 'CellViT-SAM-H-x40', '1.0.9')\n"
                % (os.path.join(self.copia, "nube_n1_cloudinit", "_a_enganche.py"), self.MAPA["nucls_main"]))
        estropeos = {"sha": "datos = datos + b'1,1,tumor,0.5\\n'",
                     "version": "meta['version'] = '1.0.8'",
                     "mapa": "meta['mapa_clases'] = {'tumor': 'tumoral', 'lymphocyte': 'inflammatory'}",
                     "campo de más": "meta['ruta'] = 'x'",
                     "n": "meta['n'] = 3",
                     "prob": "datos = datos.replace(b'0.910000', b'1.500000'); meta['sha256_csv'] = __import__('hashlib').sha256(datos).hexdigest()",
                     "clase": "datos = datos.replace(b'lymphocyte', b'stroma'); meta['sha256_csv'] = __import__('hashlib').sha256(datos).hexdigest()",
                     "fuera": "datos = datos.replace(b'3000.00', b'90000.00'); meta['sha256_csv'] = __import__('hashlib').sha256(datos).hexdigest()",
                     "bien": "pass"}
        for nombre, cambio in estropeos.items():
            v = self._sim(base + cambio + "\nsim = Sim()\nbe = backend(sim)\n"
                          "sim.buckets[be.bucket] = {'obj': {'P-HE.nucls_main.csv': datos, "
                          "'P-HE.nucls_main.json': json.dumps(meta).encode()}, 'cifrado': 'AES256'}\n"
                          "print(json.dumps(intenta(nube_n1.bajar, 'scaleway', 'nucls_main', backend=be)))")
            dest = os.path.join(self.sesion, "nube", "cellvitpp")
            if nombre == "bien":
                self.assertEqual(len(v), 2, v)
                self.assertEqual(sorted(os.listdir(dest)), ["P-HE.nucls_main.csv", "P-HE.nucls_main.json"])
            else:
                self.assertTrue(isinstance(v, str) and v.startswith("CERRADA"), (nombre, v))
                self.assertFalse(os.path.isdir(dest) and os.listdir(dest), nombre)
        v = self._sim(base + "sim = Sim()\nbe = backend(sim)\nsim.buckets[be.bucket] = {'obj': {'P-HE.nucls_main.csv': "
                      "datos, 'P-HE.nucls_main.json': json.dumps(meta).encode()}, 'cifrado': 'AES256'}\n"
                      "print(json.dumps(intenta(nube_n1.bajar, 'scaleway', 'nucls_main', backend=be)))")
        self.assertIn("no sobrescribo", v)

    def test_borrar_lo_borra_todo_y_listar_queda_vacio(self):
        """Instancia encendida con disco de arranque, IP y grupo; otro volumen; snapshots de los dos
        tipos; dos objetos y el bucket: todo fuera, `listar` vacío, sello y marca de las 24 h."""
        self.confia("nube-n1:scaleway")
        v = self._sim(
            "sim = Sim()\nbe = backend(sim)\n"
            "g = sim.nid(); sim.grupos[g] = {'name': 'btp-n1-viejo'}\nb, i = sim.nid(), sim.nid()\n"
            "sim.vol[b] = 'block'; sim.ips[i] = True\n"
            "sim.srv[sim.nid()] = {'state': 'running', 'security_group': g, 'volumes': {'0': {'id': b}}, 'public_ip': {'id': i}}\n"
            "sim.vol[sim.nid()] = 'instance'; sim.snap[sim.nid()] = 'block'; sim.snap[sim.nid()] = 'instance'\n"
            "sim.buckets[be.bucket] = {'obj': {'P-HE.n1.tif': b'x', 'DONE': b'y'}, 'cifrado': 'AES256'}\n"
            "r = intenta(nube_n1.borrar, 'scaleway', backend=be, ahora=lambda: 1000)\n"
            "print(json.dumps([r, sim.vacio(), nube_n1.listar('scaleway', backend=be)]))")
        r, vacio, listado = v
        self.assertTrue(vacio, r)
        self.assertEqual(set(r), {"instancias", "volumenes", "snapshots", "ips", "grupos_seguridad", "objetos", "buckets"})
        self.assertFalse(any(r.values()))
        self.assertFalse(any(listado.values()))
        self.assertTrue(self.eventos("nube_n1_borrar")[-1]["vacio"])
        marca = json.load(open(os.path.join(self.casa_tools, "state", "nube", "pendiente_24h.json")))
        self.assertEqual(marca, {"scaleway": {"ts": 1000}})

    def test_borrar_falla_si_el_bucket_no_se_vacia(self):
        self.confia("nube-n1:scaleway")
        v = self._sim("sim = Sim(pegajoso='P-HE.n1.tif')\nbe = backend(sim)\n"
                      "sim.buckets[be.bucket] = {'obj': {'P-HE.n1.tif': b'x', 'DONE': b'y'}, 'cifrado': 'AES256'}\n"
                      "print(json.dumps(intenta(nube_n1.borrar, 'scaleway', backend=be)))")
        self.assertIn("NO terminó", v)
        self.assertIn("objetos=1", v)
        self.assertIn("buckets=1", v)
        ev = self.eventos("nube_n1_borrar")[-1]
        self.assertEqual((ev["vacio"], ev["nivel"]), (False, "alarma"))

    def test_comprobar_a_las_24_h(self):
        """Tras `borrar`, la segunda comprobación: antes de 24 h no mira; a las 24 h, vacío cierra
        la marca; con restos, alarma y (con avisar) aviso a {{TITULAR}} por salida.py."""
        self.confia("nube-n1:scaleway")
        src = ("sim = Sim()\nbe = backend(sim)\nnube_n1.borrar('scaleway', backend=be, ahora=lambda: 1000)\n%s\n"
               "print(json.dumps([intenta(nube_n1.comprobar_24h, 'scaleway', backend=be, ahora=lambda: t, avisar=True)"
               " for t in (1000 + 3600, 1000 + 86400 + 1)]))")
        antes, despues = self._sim(src % "")
        self.assertEqual(antes["estado"], "aún no")
        self.assertEqual(despues["estado"], "vacío a las 24 h")
        self.assertEqual(json.load(open(os.path.join(self.casa_tools, "state", "nube", "pendiente_24h.json"))), {})
        self.assertEqual(self.avisos(), [])
        antes, despues = self._sim(src % "sim.vol[sim.nid()] = 'block'")
        self.assertIn("CERRADA", despues)
        self.assertIn("volumenes=1", despues)
        self.assertEqual(len(self.avisos()), 1)
        self.assertIn("24 h", self.avisos()[0]["texto"])
        self.assertEqual(self.eventos("nube_n1_listar_24h")[-1]["nivel"], "alarma")
        r = self.corre("nube_n1.py", "plist-24h")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("<string>comprobar-24h</string>", r.stdout)

    def test_el_conversor_que_viaja_casa_con_laminillas_he(self):
        v = self.codigo("sys.path.insert(0, %r)\nimport _a_enganche as A, laminillas_he as H\n"
                        "try:\n    A.convierte({'type_map': {'0': 'x'}, 'cells': [{'centroid': [1, 1], 'type': 0, 'type_prob': 0.5}]},"
                        " 'nucls_main', 0.25, {'tumor': 'neoplastic'}, 'm', '1.0.9')\n    r = 'PASA'\n"
                        "except A.Invalido as e:\n    r = str(e)\n"
                        "print(json.dumps([list(A.COMUN) == list(H.COMUN), A.COLUMNAS == H.CELLVITPP['salida']['csv_columnas'],"
                        " A.LAMINA == H.PRIMARIO, r]))" % os.path.join(self.copia, "nube_n1_cloudinit"))
        self.assertEqual(v[:3], [True, True, True])
        self.assertIn("sin mapa", v[3])

    @CON_IMAGEN
    def test_flujo_entero_subir_lanzar_bajar_borrar(self):
        """La copia N1 de verdad de exporta_n1 (TIFF sintético): subir (re-validada), lanzar, bajar
        y borrar, todo contra el Scaleway simulado; al final, nada en el proveedor."""
        self.confia("nube-n1:scaleway")
        self.sella_config()
        src = os.path.join(self.tmp, "limpia.tif")
        escribe_tiff(src, [(16384, 4096, None), (4096, 1024, None), (2048, 512, None)])
        self.assertEqual(self.corre("exporta_n1.py", "tiff", src, "--opaco", "P-HE").returncode, 0)
        tif = os.path.join(self.n1, "P-HE.n1.tif")
        v = self._sim("import os\nsim = Sim(al_encender=maquina('DONE'))\nbe = backend(sim)\n"
                      "s = nube_n1.subir('scaleway', [%r], backend=be, avisar=True)\n"
                      "l = nube_n1.lanzar('scaleway', backend=be)\n"
                      "b = [os.path.basename(x) for x, _s in nube_n1.bajar('scaleway', 'panoptils', backend=be)]\n"
                      "d = nube_n1.borrar('scaleway', backend=be)\n"
                      "print(json.dumps([s, l['resultado'], b, any(d.values()), sim.vacio()]))" % tif,
                      )
        self.assertEqual(v, [["P-HE.n1.tif"], "DONE", ["P-HE.panoptils.json", "P-HE.panoptils.csv"], False, True])
        self.assertEqual(len(self.avisos()), 1)

    def _sube(self, rutas, avisar=True, env=None, antes=""):
        return self.codigo(antes + self.FALSO + "import nube_n1\nprint(json.dumps(sube(%r, %r)))"
                           % (rutas, avisar), env=env)

    def _csv_en_n1(self):
        ok = self.fichero("ok.csv", "marker,value\nRE,85.2\n")
        self.assertEqual(self.corre("exporta_n1.py", "texto", ok, "--nombre", "P-KI67-t.csv").returncode, 0)
        return os.path.join(self.n1, "P-KI67-t.csv")

    def _registro_salida(self):
        st = os.path.join(self.casa_tools, "state")
        out = []
        for sub in ("salida", "notif", "aplazados"):
            d = os.path.join(st, sub)
            for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
                if fn.endswith(".jsonl"):
                    out += [json.loads(x)["texto"] for x in _lee(os.path.join(d, fn), "r").splitlines()
                            if x.strip()]
        return out

    # ── El aviso del primer envío ya no se calla sellando el evento (hueco de f3c2a2f, 2-oct-26) ──
    def test_aviso_forjado_no_calla_el_aviso(self):
        """Con f3c2a2f, un `n1_aviso_primer_envio` sellado a mano callaba el aviso del primer envío.
        Ahora el evento solo cuenta si su id está en el registro de salida.py: el forjado (sin id o
        con uno inventado) no calla nada, deja `n1_aviso_sin_registro` y el aviso lo dice."""
        import re
        self.confia("nube-n1:scaleway")
        bueno = self._csv_en_n1()
        for extra in ({}, {"aviso": "N1-0123456789ab"}):
            self.assertEqual(self.sella(dict({"evento": "n1_aviso_primer_envio",
                                              "destino": "nube-n1:scaleway", "nivel": "info"}, **extra)),
                             "SELLADO")
        r, subidos = self._sube([bueno])
        self.assertEqual(r, ["P-KI67-t.csv"], r)
        avisos = self.avisos()
        self.assertEqual(len(avisos), 1, avisos)
        texto = avisos[0]["texto"]
        self.assertIn("Ojo", texto)
        alarma = self.eventos("n1_aviso_sin_registro")
        self.assertEqual([(e["n"], e["nivel"]) for e in alarma], [(2, "alarma")])
        id_aviso = re.match(r"Aviso (N1-[0-9a-f]{12}):", texto).group(1)
        self.assertEqual(self.eventos("n1_aviso_primer_envio")[-1]["aviso"], id_aviso)
        self.assertTrue(any(id_aviso in t for t in self._registro_salida()))
        self._sube([bueno])                                     # ya avisado de verdad: no repite
        self.assertEqual(len(self.avisos()), 1)
        self.assertEqual(len(self.eventos("n1_aviso_sin_registro")), 1)

    def test_aviso_retenido_o_aplazado_cuenta_como_avisado(self):
        """Lo que salida.py retiene por el silencio nocturno o aplaza al parte le llega a {{TITULAR}} más
        tarde: cuenta. Re-confiar el destino vuelve a pedir aviso."""
        bueno = None
        for i, modo in enumerate(("retenido", "aplazado")):
            self.confia("nube-n1:scaleway")
            bueno = bueno or self._csv_en_n1()
            env = dict(self.env, BTP_TEST_SALIDA_MODO=modo)
            for _ in range(2):
                r, _s = self._sube([bueno], env=env)
                self.assertEqual(r, ["P-KI67-t.csv"], r)
            self.assertEqual(len(self.avisos()), i + 1, modo)
        self.assertFalse(self.eventos("n1_aviso_sin_registro"))
        self.assertEqual(len(self._registro_salida()), 2)

    def test_aviso_con_la_salida_real_deja_su_id_en_el_registro(self):
        """Contrato con el salida.py REAL (parcheado: sin red, sin token, chat falso). Con f3c2a2f el
        texto era una sola línea con `tools/borde.py`, y la casa de estilo de salida.py borra esa
        línea: el aviso salía vacío. Ahora la primera línea lleva el id y la orden para cortar
        sobrevive; el id queda en `salida/enviados-*.jsonl` y el segundo envío no repite."""
        import re
        shutil.copy(os.path.join(TOOLS, "salida.py"), os.path.join(self.copia, "salida.py"))
        self.confia("nube-n1:scaleway")
        bueno = self._csv_en_n1()
        for _ in range(2):
            r, subidos = self._sube([bueno], antes=PARCHE_SALIDA_REAL)
            self.assertEqual(r, ["P-KI67-t.csv"], r)
        d = os.path.join(self.casa_tools, "state", "salida")
        enviados = [json.loads(x) for fn in sorted(os.listdir(d)) if fn.startswith("enviados-")
                    for x in _lee(os.path.join(d, fn), "r").splitlines() if x.strip()]
        self.assertEqual(len(enviados), 1, enviados)
        self.assertEqual(enviados[0]["fuente"], "n1-primer-envio")
        texto = enviados[0]["texto"]
        id_aviso = re.search(r"Aviso (N1-[0-9a-f]{12}):", texto).group(1)
        self.assertIn("revoke-cloud nube-n1:scaleway", texto)
        self.assertEqual(self.eventos("n1_aviso_primer_envio")[-1]["aviso"], id_aviso)
        self.assertFalse(self.eventos("n1_aviso_sin_registro"))

    def test_subir_comprueba_n1_manifiesto_sha256_y_avisa(self):
        """Hallazgo 24: `subir` no lo llamaba ningún test. Fuera de N1, fuera del manifiesto o
        alterado: el backend no recibe nada. Bueno: lo recibe, con un aviso a {{TITULAR}} la primera
        vez (y sin `avisar`, no sale)."""
        self.confia("nube-n1:scaleway")
        ok = self.fichero("ok.csv", "marker,value\nRE,85.2\n")
        self.assertEqual(self.corre("exporta_n1.py", "texto", ok, "--nombre", "P-KI67-t.csv").returncode, 0)
        bueno = os.path.join(self.n1, "P-KI67-t.csv")
        colado = os.path.join(self.n1, "colado.csv")
        shutil.copy(ok, colado)
        for ruta, motivo in ((ok, "fuera de N1"), (colado, "manifiesto")):
            r, subidos = self._sube([ruta])
            self.assertIn(motivo, r)
            self.assertEqual(subidos, [])
        r, subidos = self._sube([bueno], avisar=False)
        self.assertIn("opt-in", r)
        self.assertEqual(subidos, [])
        self.assertEqual(self.avisos(), [])
        for _ in range(2):
            r, subidos = self._sube([bueno])
            self.assertEqual(r, ["P-KI67-t.csv"])
            self.assertEqual(subidos, [["P-KI67-t.csv", hashlib.sha256(_lee(bueno)).hexdigest()]])
        self.assertEqual(len(self.avisos()), 1)
        self.assertIn("nube-n1:scaleway", self.avisos()[0]["texto"])
        with open(bueno, "a") as fh:
            fh.write("Leocadia,1\n")
        r, subidos = self._sube([bueno])
        self.assertIn("sha256", r)
        self.assertEqual(subidos, [])

    def test_subir_texto_colado_con_identidad_no_sale(self):
        """Hallazgo 9 (texto): un CSV colado en N1 con su sha256 en el manifiesto pasa por la
        Puerta antes de salir."""
        self.confia("nube-n1:scaleway")
        p = self.colar_en_n1("P-X-t.csv", b"nota,valor\nLeocadia Quintanar,1\n")
        r, subidos = self._sube([p])
        self.assertIn("CERRADA", r)
        self.assertEqual(subidos, [])

    def test_subir_con_nombre_de_objeto_con_identidad_no_sale(self):
        """Hallazgo N2 de la 3.ª pasada: el nombre del fichero N1 es el del objeto en el bucket."""
        self.confia("nube-n1:scaleway")
        limpio = b"marker,value\nRE,85.2\n"
        for nombre in ("P-24B0001043.csv", "Leocadia-Quintanar.csv", "24B0001043.png", "manifiesto.json"):
            p = os.path.join(self.n1, nombre)
            if nombre != "manifiesto.json":
                p = self.colar_en_n1(nombre, limpio)
            r, subidos = self._sube([p])
            self.assertIn("CERRADA", r, nombre)
            self.assertEqual(subidos, [], nombre)
            self.assertNotIn("Leocadia", r)

    # ── El aviso del primer envío, atado a su destino y a su confianza (3.ª pasada) ──
    def _id_del_ultimo_aviso(self):
        import re
        return re.search(r"Aviso (N1-[0-9a-f]{12}):", self.avisos()[-1]["texto"]).group(1)

    @CON_IMAGEN
    def test_aviso_con_id_viejo_no_calla_ni_otra_confianza_ni_otro_destino(self):
        """Hallazgo N5: con 6ee8efd, sellar un `n1_aviso_primer_envio` con el id de un aviso VIEJO
        (que sí está en el registro de salida) callaba el aviso tras re-confiar, o el de otro
        destino. Ahora el id no puede repetirse y su línea del registro tiene que nombrar el destino
        y la confianza (su ts) y ser posterior a ella."""
        self.confia("nube-n1:scaleway")
        bueno = self._csv_en_n1()
        self.assertEqual(self._sube([bueno])[0], ["P-KI67-t.csv"])
        viejo = self._id_del_ultimo_aviso()
        self.assertEqual(len(self.avisos()), 1)
        # (a) mismo destino, revocado y re-confiado; se sella el id viejo
        self.sella({"evento": "revoke_cloud", "destino": "nube-n1:scaleway", "nivel": "info"})
        self.confia("nube-n1:scaleway")
        self.assertEqual(self.sella({"evento": "n1_aviso_primer_envio", "destino": "nube-n1:scaleway",
                                     "nivel": "info", "aviso": viejo}), "SELLADO")
        self.assertEqual(self._sube([bueno])[0], ["P-KI67-t.csv"])
        self.assertEqual(len(self.avisos()), 2)                    # vuelve a avisar
        self.assertEqual(len(self.eventos("n1_aviso_sin_registro")), 1)
        # (b) otro destino, con el id del aviso de nube
        self.confia("vision-n1:gemini")
        self.condiciones()
        png = self.png_n1()
        self.sella({"evento": "n1_aviso_primer_envio", "destino": "vision-n1:gemini", "nivel": "info",
                    "aviso": self._id_del_ultimo_aviso()})
        v = self.codigo(GEMINI_FALSO + "print(json.dumps(envia([%r])))" % png)
        self.assertEqual(len(v["llamadas"]), 1, v["r"])            # salió…
        self.assertEqual(len(self.avisos()), 3)                    # …tras avisar
        self.assertIn("vision-n1:gemini", self.avisos()[-1]["texto"])
        self.assertEqual(len(self.eventos("n1_aviso_sin_registro")), 2)

    def test_limite_declarado_aviso_con_registro_escrito_a_mano(self):
        """LÍMITE DECLARADO (puerta_n1, cabecera): el registro de salida.py es un fichero de casa
        base sin firma. Quien escriba a mano en `salida/enviados-<hoy>.jsonl` una línea con un id
        nuevo, el destino, el ts de la confianza y la hora de ahora, y selle el evento con ese id,
        calla el aviso del primer envío. Queda el rastro del evento en la cadena."""
        import time as _t
        self.confia("nube-n1:scaleway")
        bueno = self._csv_en_n1()
        ts = [e for e in self.eventos("trust_cloud") if e["destino"] == "nube-n1:scaleway"][-1]["ts"]
        d = os.path.join(self.casa_tools, "state", "salida")
        os.makedirs(d, exist_ok=True)
        texto = ("Aviso N1-00000000beef: primer envío de copias N1 de tus láminas (sin identificar) a "
                 "nube-n1:scaleway.\n1 fichero(s). Lo autoriza tu trust-cloud del %s." % ts)
        with open(os.path.join(d, "enviados-%s.jsonl" % _t.strftime("%Y-%m-%d")), "a") as fh:
            fh.write(json.dumps({"ts": _t.strftime("%Y-%m-%dT%H:%M:%S"), "texto": texto}) + "\n")
        self.sella({"evento": "n1_aviso_primer_envio", "destino": "nube-n1:scaleway", "nivel": "info",
                    "aviso": "N1-00000000beef"})
        self.assertEqual(self._sube([bueno])[0], ["P-KI67-t.csv"])
        self.assertEqual(self.avisos(), [])                        # el límite: no avisa
        self.assertEqual([e["aviso"] for e in self.eventos("n1_aviso_primer_envio")], ["N1-00000000beef"])

    @CON_IMAGEN
    def test_subir_tiff_colado_no_sale_y_el_exportado_si(self):
        """Hallazgo 9 (TIFF): `copia_tiff_n1` en modo verificación. Un TIFF con tags de más o con
        el 270 sin reescribir, colado en N1, no sale; la copia de exporta_n1, sí, byte a byte."""
        self.confia("nube-n1:scaleway")
        niveles = [(16384, 4096, None), (4096, 1024, None), (2048, 512, None)]
        for i, extra in enumerate((True, False)):
            src = os.path.join(self.tmp, "colado%d.tif" % i)
            escribe_tiff(src, niveles, extra_tags=extra)              # 270 = «Leocadia R Q»
            p = self.colar_en_n1("P-C%d.n1.tif" % i, _lee(src))
            r, subidos = self._sube([p])
            self.assertIn("CERRADA", r)
            self.assertRegex(r, "lista blanca" if extra else "copia canónica")
            self.assertEqual(subidos, [])
        src = os.path.join(self.tmp, "limpia.tif")
        escribe_tiff(src, niveles)
        self.assertEqual(self.corre("exporta_n1.py", "tiff", src, "--opaco", "P-HE").returncode, 0)
        dst = os.path.join(self.n1, "P-HE.n1.tif")
        r, subidos = self._sube([dst])
        self.assertEqual(r, ["P-HE.n1.tif"], r)
        self.assertEqual(subidos, [["P-HE.n1.tif", hashlib.sha256(_lee(dst)).hexdigest()]])

    @CON_IMAGEN
    def test_subir_tiff_canonico_con_texto_en_el_cristal(self):
        """Hallazgo 9 bis (2-oct-26): la copia CANÓNICA de un TIFF con el nombre rasterizado en el
        ×8 (lo que copia_tiff_n1 sacaría, sin pasar el OCR de exporta_tiff), colada en N1, no sale:
        el envío vuelve a pasar OCR + Puerta. Sale solo con la revisión humana del paso 1-bis
        atada a ESE sha256."""
        self.confia("nube-n1:scaleway")
        x8 = Image.new("RGB", (2048, 512), (236, 200, 220))
        x8.paste(_texto_con_alto("LEOCADIA QUINTANAR 24B0001043", 60, margen=10), (40, 200))
        src = os.path.join(self.tmp, "rotulada.tif")
        escribe_tiff(src, [(16384, 4096, None), (4096, 1024, None), (2048, 512, x8)])
        canon = os.path.join(self.tmp, "canonica.tif")
        self.codigo("import exporta_n1 as e\nt = e.Tiff(%r)\nb, _ = e.copia_tiff_n1(t)\n"
                    "open(%r, 'wb').write(b)\nprint(json.dumps(1))" % (src, canon))
        p = self.colar_en_n1("P-F.n1.tif", _lee(canon))
        sha = hashlib.sha256(_lee(p)).hexdigest()
        r, subidos = self._sube([p])
        self.assertIn("CERRADA", r)
        self.assertIn("1-bis", r)
        self.assertEqual(subidos, [])
        # El hueco de f3c2a2f: `_sellar` a mano fabricaba la revisión. Ya no sella (deja alarma), y
        # una por la puerta de las fixtures sin `via: 'tty'` tampoco cuenta.
        ev = {"evento": "n1_revision_humana", "opaco": "P-F", "sha256": sha, "nivel": "info"}
        self.assertEqual(self.sella(ev), "RECHAZADO")
        self.assertEqual(self.sella(dict(ev, via="tty")), "RECHAZADO")
        self.assertEqual(self.revision(sha, via=None), "SELLADO")
        r, subidos = self._sube([p])
        self.assertIn("CERRADA", r)
        self.assertEqual(subidos, [])
        self.revision("0" * 64)
        r, subidos = self._sube([p])                            # otra revisión, otro sha: no
        self.assertIn("CERRADA", r)
        self.revision(sha)
        r, subidos = self._sube([p])
        self.assertEqual(r, ["P-F.n1.tif"], r)
        self.assertEqual(subidos, [["P-F.n1.tif", sha]])
        self.assertEqual(len(self.eventos("sello_rechazado")), 2)

    @CON_IMAGEN
    def test_subir_tiff_canonico_con_x4_rotulado(self):
        """Hallazgo N4 de la 3.ª pasada, por la re-validación del envío: la copia CANÓNICA de un
        TIFF con el nombre solo en el ×4 (×8 limpio), colada en N1, no sale sin el paso 1-bis."""
        self.confia("nube-n1:scaleway")
        x4 = Image.new("RGB", (4096, 1024), (236, 200, 220))
        x4.paste(_texto_con_alto("QUINTANAR 24B0001043", 300, margen=20), (100, 300))
        src = os.path.join(self.tmp, "x4.tif")
        escribe_tiff(src, [(16384, 4096, None), (4096, 1024, x4), (2048, 512, None)])
        canon = os.path.join(self.tmp, "canonica.tif")
        self.codigo("import exporta_n1 as e\nt = e.Tiff(%r)\nb, _ = e.copia_tiff_n1(t)\n"
                    "open(%r, 'wb').write(b)\nprint(json.dumps(1))" % (src, canon))
        r, subidos = self._sube([self.colar_en_n1("P-X4.n1.tif", _lee(canon))])
        self.assertIn("CERRADA", r)
        self.assertIn("1-bis", r)
        self.assertIn("(×4)", r)
        self.assertEqual(subidos, [])


class MedGemma(Base):
    """MedGemma 4B en LOCAL (`panel_vision/medgemma_local.py`): sin pesos sellados o sin ellos en la
    caché, falla cerrado con «pendiente de acceso» y el panel lo marca así; con pesos sellados (de juguete) y un cargador
    falso, responde; con un byte cambiado, no carga. Nada se descarga ni sale del Mac."""

    def _mg(self, src, env=None):
        return self.codigo("sys.path.insert(0, %r)\nimport medgemma_local as M, calibra as C\n"
                           % os.path.join(self.copia, "panel_vision") + src, env=env)

    def _pesos(self, ficheros, editar_tras_sellar=False):
        """pesos.json + .sha256 de juguete con la entrada medgemma, y su snapshot en un HF_HOME falso."""
        hf = os.path.join(self.tmp, "hf")
        commit = "c" * 40
        snap = os.path.join(hf, "hub", "models--google--medgemma-4b-it", "snapshots", commit)
        os.makedirs(snap)
        meta = {}
        for nombre, datos in ficheros.items():
            with open(os.path.join(snap, nombre), "wb") as fh:
                fh.write(datos)
            meta[nombre] = {"bytes": len(datos), "sha256": hashlib.sha256(datos).hexdigest()}
        pesos = os.path.join(self.tmp, "pesos.json")
        crudo = json.dumps({"modelos": {"medgemma": {"repo": "google/medgemma-4b-it", "commit": commit,
                                                     "estado": "OK", "ficheros": meta}}}).encode()
        with open(pesos, "wb") as fh:
            fh.write(crudo)
        with open(pesos + ".sha256", "w") as fh:
            fh.write(hashlib.sha256(crudo).hexdigest() + "  pesos.json\n")
        if editar_tras_sellar:
            with open(pesos, "ab") as fh:
                fh.write(b" ")
        return pesos, hf, snap

    def test_sin_pesos_sellados_pendiente_de_acceso_y_el_panel_lo_marca(self):
        # La copia del árbol no trae laminillas_stack: sin pesos.json, «pendiente de acceso».
        v = self._mg("est, motivo = M.estado()\ntry:\n    C.adaptador('medgemma').comprueba()\n    r = 'CARGA'\n"
                     "except C.AdaptadorPendiente as e:\n    r = [type(e).__name__, str(e)]\n"
                     "print(json.dumps([est, motivo, r, C.estado_proveedores()['medgemma']]))")
        est, _motivo, r, panel = v
        self.assertEqual(est, "pendiente de acceso")
        self.assertEqual(r[0], "PendienteDeAcceso")
        self.assertIn("pendiente de acceso", r[1])
        self.assertTrue(panel.startswith("pendiente de acceso"), panel)
        # pesos.json sellado pero sin la entrada medgemma (como antes del 2-oct-26): el motivo es «gated».
        sin = os.path.join(self.tmp, "pesos-sin-medgemma.json")
        crudo = json.dumps({"modelos": {}}).encode()
        with open(sin, "wb") as fh:
            fh.write(crudo)
        with open(sin + ".sha256", "w") as fh:
            fh.write(hashlib.sha256(crudo).hexdigest() + "  pesos.json\n")
        v = self._mg("print(json.dumps(M.estado(pesos=%r)))" % sin)
        self.assertEqual(v[0], "pendiente de acceso")
        self.assertIn("gated", v[1])
        # El pesos.json REAL (medgemma sellado el 2-oct-26) con una caché vacía: no carga.
        v = self._mg("print(json.dumps(M.estado(pesos=%r, hf_home=%r)))"
                     % (os.path.join(TOOLS, "laminillas_stack", "pesos.json"), os.path.join(self.tmp, "hf-vacio")))
        self.assertEqual(v[0], "pendiente de acceso")
        self.assertIn("faltan", v[1])

    def test_con_pesos_sellados_responde_y_con_un_byte_cambiado_no_carga(self):
        pesos, hf, snap = self._pesos({"config.json": b'{"a": 1}', "model.safetensors": b"\x00" * 64})
        png = self.fichero("caso.png", b"")
        _textura(64, 64).save(png)
        src = ("cargas = []\n"
               "def cargador(carpeta, dispositivo):\n    cargas.append([carpeta, dispositivo])\n"
               "    return lambda prompt, img: '{\"hay_error\": false, \"tipo\": null, \"cuadrante\": null}|%%dx%%d' %% img.size\n"
               "a = M.MedGemmaLocal(pesos=%r, hf_home=%r, cargador=cargador)\n"
               "try:\n    cfg = a.comprueba()\n    r = [cfg['commit'], a.responder('p', %r, None), cargas]\n"
               "except C.AdaptadorPendiente as e:\n    r = str(e)\n"
               "print(json.dumps([M.estado(pesos=%r, hf_home=%r)[0], r]))")
        est, r = self._mg(src % (pesos, hf, png, pesos, hf))
        self.assertEqual(est, "listo")
        self.assertEqual(r[0], "c" * 40)
        self.assertTrue(r[1].endswith("|64x64"))
        self.assertEqual(r[2], [[snap, "mps"]])
        with open(os.path.join(snap, "model.safetensors"), "r+b") as fh:
            fh.write(b"\x01")
        _est, r = self._mg(src % (pesos, hf, png, pesos, hf))
        self.assertIn("no son los bytes sellados", r)

    def test_pesos_json_editado_no_vale(self):
        pesos, hf, _snap = self._pesos({"config.json": b"{}"}, editar_tras_sellar=True)
        v = self._mg("print(json.dumps(M.estado(pesos=%r, hf_home=%r)))" % (pesos, hf))
        self.assertEqual(v[0], "pendiente de acceso")
        self.assertIn(".sha256", v[1])


@CON_IMAGEN
class PanelGemini(Base):
    """El arnés de calibración (`panel_vision/calibra.py`) con Gemini por vision_n1: las imágenes
    SINTÉTICAS pasan a N1 por exporta_n1 (`a-n1`), y `correr` pregunta por la boca autorizada con
    todas sus guardas. Transporte y clave falsos."""

    def setUp(self):
        super().setUp()
        sys.path.insert(0, os.path.join(TOOLS, "panel_vision"))
        try:
            import calibra as C
        finally:
            sys.path.pop(0)
        self.dir = os.path.join(self.tmp, "conjunto")
        os.makedirs(os.path.join(self.dir, "casos"))
        casos = []
        for i, (tarea, ctx) in enumerate((("nucleos", {}), ("ck19", {}), ("registro", {"mpp": 2.0}),
                                          ("artefacto", {}))):
            cid = "%012x" % (0xabc000 + i)
            ruta = os.path.join(self.dir, "casos", cid + ".png")
            _textura(320, 320, semilla=11 + i).save(ruta)
            casos.append({"id": cid, "tarea": tarea, "png": "casos/%s.png" % cid,
                          "sha256": hashlib.sha256(_lee(ruta)).hexdigest(), "prompt": C.pregunta(tarea, ctx),
                          "verdad": {"tarea": tarea, "hay_error": False, "tipo": None, "cuadrante": None}})
        with open(os.path.join(self.dir, "preguntas.jsonl"), "w") as fh:
            for c in casos:
                fh.write(json.dumps({k: c[k] for k in ("id", "tarea", "png", "prompt")}) + "\n")
        self._json(os.path.join(self.dir, "verdad.json"), {c["id"]: c["verdad"] for c in casos})
        self._json(os.path.join(self.dir, "manifiesto.json"), {
            "protocolo": C.PROTOCOLO, "huella_preguntas": C.huella_preguntas(), "semilla": 1, "lado": 320,
            "n_por_clase": 1, "tareas": [c["tarea"] for c in casos], "casos": {c["id"]: c["sha256"] for c in casos},
            "sha_conjunto": C._huella_conjunto(casos), "creado": "2026-10-02T00:00:00+00:00"})

    def _cal(self, src, env=None):
        return self.codigo("sys.path.insert(0, %r)\nimport calibra as C\n" % os.path.join(self.copia, "panel_vision")
                           + GEMINI_FALSO + src, env=env)

    def test_las_sinteticas_pasan_a_n1_y_gemini_se_calibra_por_vision_n1(self):
        # Sin a-n1, las imágenes de calibración no son de N1: no van a ningún sitio.
        self.confia("vision-n1:gemini")
        self.condiciones()
        v = self._cal("vision_n1._clave = lambda p: CLAVE\n"
                      "try:\n    C.correr(%r, C.Externo('gemini', avisar=True, transporte=T))\n    r = 'CORRE'\n"
                      "except C.AdaptadorPendiente as e:\n    r = [type(e).__name__, str(e)]\n"
                      "print(json.dumps([r, T.llamadas]))" % self.dir)
        self.assertEqual(v[0][0], "FueraDeN1")
        self.assertEqual(v[1], [])
        r = self._cal("print(json.dumps(C.a_n1(%r)))" % self.dir)
        self.assertEqual((r["nuevos"], r["ya_estaban"]), (4, 0))
        mapa = json.load(open(os.path.join(self.dir, "n1.json")))
        nombres = sorted(e["n1"] for e in mapa["casos"].values())
        self.assertEqual([n[:9] for n in nombres], ["CAL-00001", "CAL-00002", "CAL-00003", "CAL-00004"])
        self.assertEqual(sorted(n[9:] for n in nombres), ["__L0__mpp0.5000.png"] * 3 + ["__L0__mpp2.0000.png"])
        man = json.load(open(os.path.join(self.n1, "manifiesto.json")))["ficheros"]
        self.assertTrue(all(man[e["n1"]]["sha256"] == e["sha256_n1"] for e in mapa["casos"].values()))
        self.assertEqual(self._cal("print(json.dumps(C.a_n1(%r)['nuevos']))" % self.dir), 0)   # reanudable
        v = self._cal("vision_n1._clave = lambda p: CLAVE\n"
                      "r = C.correr(%r, C.Externo('gemini', avisar=True, transporte=T))\n"
                      "res = C.puntua_conjunto(%r, escribe=False)\n"
                      "print(json.dumps([r['nuevas'], r['clave'], list(res['modelos']), T.llamadas]))" % (self.dir, self.dir))
        nuevas, clave, modelos, llamadas = v
        self.assertEqual(nuevas, 4)
        self.assertTrue(clave.startswith("gemini:gemini-3.1-pro-preview@"), clave)
        self.assertEqual(modelos, [clave])
        self.assertEqual(len(llamadas), 4)
        from PIL import Image
        por_sha = {}
        for c in json.load(open(os.path.join(self.dir, "verdad.json"))):
            ruta = os.path.join(self.dir, "casos", c + ".png")
            por_sha[Image.open(ruta).convert("RGB").tobytes()] = c
        for ll in llamadas:                      # cada envío, una imagen con los píxeles de SU caso
            (img, _txt) = ll["cuerpo"]["contents"][0]["parts"]
            pix = Image.open(io.BytesIO(base64.b64decode(img["inlineData"]["data"]))).convert("RGB").tobytes()
            self.assertIn(pix, por_sha)
        self.assertEqual(len(self.eventos("vision_n1_envio")), 4)
        self.assertEqual(len(self.avisos()), 1)

    def test_sin_condiciones_o_sin_confianza_no_corre_ni_escribe(self):
        self._cal("print(json.dumps(C.a_n1(%r)['nuevos']))" % self.dir)
        for preparar in ((lambda: None), (lambda: self.condiciones()), (lambda: self.condiciones(cumplidas=False))):
            preparar()
            v = self._cal("try:\n    C.correr(%r, C.Externo('gemini', avisar=True, transporte=T))\n    r = 'CORRE'\n"
                          "except C.AdaptadorPendiente as e:\n    r = [type(e).__name__, str(e)]\n"
                          "print(json.dumps([r, T.llamadas]))" % self.dir)
            self.assertEqual(v[0][0], "ProveedorNoListo", v)
            self.assertEqual(v[1], [])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "respuestas")))
        v = self._cal("import io, contextlib\nb = io.StringIO()\nwith contextlib.redirect_stdout(b):\n"
                      "    rc = C.main(['correr', '--dir', %r, '--modelo', 'gemini:gemini-2.5-pro'])\n"
                      "print(json.dumps(rc))" % self.dir)
        self.assertEqual(v, 2)                                          # modelo no fijado: ni se construye


if __name__ == "__main__":
    unittest.main(verbosity=2)
