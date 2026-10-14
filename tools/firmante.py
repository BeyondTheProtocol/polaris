#!/usr/bin/env python3
"""firmante.py — el daemon que firma el permiso de salida de {{TITULAR}} (14-oct-2026, «pieza A»).

EL PROBLEMA. {{TITULAR}} trabaja desde la app de Claude en otro dispositivo, que entra al Mac por
SSH. En esa sesión el Llavero no se deja leer (`security` da el código 36, «User interaction is
not allowed»), y el permiso de envío se firmaba y se COMPROBABA con una clave del Llavero. Su
«fusiona» no abría nada.

LA IDEA. La clave privada (Ed25519) la tiene SOLO este proceso, que corre bajo launchd en la
sesión gráfica (`gui/501`), donde el Llavero sí se lee. El permiso se comprueba con la clave
PÚBLICA (`permiso_envio.PUBLICA`), que no es secreta: la sesión ya no necesita el Llavero.

QUÉ HACE, Y SOLO ESO. Escucha en un socket Unix (0600, dentro de `tools/state/ok_envio/`, 0700)
y entiende UNA operación, `emitir`, con tres datos: la sesión, el `prompt_id` y el hash del
mensaje. No recibe rutas ni textos, y nunca firma bytes que le manden. Con esos tres datos:
  1. busca ÉL el transcript de esa sesión (`<sesión>.jsonl` dentro de `~/.claude/projects`,
     sin enlaces): el llamante no elige qué fichero se mira;
  2. espera a que aparezca el mensaje y exige lo de siempre (`permiso_envio.contexto`): prompt
     HUMANO, el último suyo, con una orden, no de subagente ni de tarea programada;
  3. fecha el permiso con la hora de ESA línea del transcript, no con su reloj, y no firma si
     tiene más de 10 minutos, si ese mensaje ya gastó su permiso o si hay HALT;
  4. decide él si la orden es de un solo uso o de LOTE (`lote_envio.evaluar`);
  5. firma y ESCRIBE ÉL el permiso (`<sesión>.ok2` o `.lote2`). Al cliente solo le contesta
     códigos de una lista cerrada: quien pregunta no recibe nada firmado.
Como el permiso lo escribe el daemon cuando VE el mensaje, da igual si el harness apunta la
línea antes o después de lanzar el hook: si aún no está, contesta «pendiente» y sigue mirando
unos segundos en segundo plano.

DE QUIÉN SE FÍA. Del transcript (lo escribe el harness; los guards deniegan escribirlo) y de
nada más. El par del socket tiene que ser un proceso del mismo usuario (LOCAL_PEERCRED) y cada
petición queda en `firmante_log.jsonl` con su pid. Hablarle directamente no da nada que no
diera el hook: solo firma lo que el transcript confirma.

LÍMITE QUE NO SE ESCONDE. Mismo usuario de macOS: un proceso suyo que escriba un transcript por
una vía que los guards no reconozcan consigue una firma válida. La firma asimétrica quita la
necesidad del Llavero en la sesión y hace inútil un servidor falso; no convierte el transcript
en algo que el propio usuario no pueda tocar.

USO
  python3 tools/firmante.py estado      # ¿hay socket?, ¿qué pública anuncia?, ¿casa con la fijada?
  python3 tools/firmante.py publica     # deriva la pública del Llavero (solo en sesión local)
  firmante.py servir --crear-clave-si-falta     # SOLO launchd (com.btp.firmante)
Activación, fijar la pública y marcha atrás: tools/launchd/FIRMANTE.md.
"""
import json
import os
import re
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _casa  # noqa: E402
import permiso_envio as P  # noqa: E402

LABEL = "com.btp.firmante"
SERVICIO = P.SERVICIO_FIRMANTE
SECURITY = "/usr/bin/security"
V_PROTO = 1
MAX_LINEA = 4096
T_CLIENTE = 3.0          # lo que espera el hook (su timeout en settings es de 10 s)
T_CONEXION = 2.0         # lo que el daemon espera a que el par termine de escribir
INLINE_S = 1.5           # cuánto se espera la línea ANTES de contestar «pendiente»
ESPERA_S = 45.0          # cuánto se sigue mirando después, en segundo plano
SONDEO_S = 0.2
MAX_VIGIAS = 32
EXT_USO, EXT_LOTE = ".ok2", ".lote2"
VIDA_LOTE_S = 900
MAX_SOCKET = 100         # sun_path admite 104 bytes en macOS

ESTADOS = ("emitido", "pendiente", "rechazado")
# Lo ÚNICO que el daemon contesta y el cliente acepta. Sin texto libre: un servidor falso no
# puede meter una frase en el contexto de la sesión a través del hook.
MOTIVOS = ("", "halt", "sin-clave", "llavero-ilegible", "clave-distinta", "sin-cripto",
           "peticion-invalida", "version", "par-ajeno", "no-es-orden-suya", "ya-usado",
           "caducado", "sin-hora", "ocupado", "error")
LOTES = ("", "no", "invalido", "no-es-orden", "abre")
TIPOS = ("", "uso", "lote")
# Los que solo pone el CLIENTE cuando no llega a tener una respuesta que valga.
M_NO_RESPONDE = "no-responde"
M_RESPUESTA = "respuesta-invalida"
CAMPOS_PETICION = frozenset(("v", "op", "session_id", "prompt_id", "hash_prompt"))
CAMPOS_RESPUESTA = frozenset(("v", "estado", "motivo", "tipo", "n", "lote"))
_RE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_RE_HASH = re.compile(r"^[0-9a-f]+$")


def socket_path():
    return os.path.join(P.dir_permisos(), "firmante.sock")


def log_path():
    return os.path.join(P.dir_permisos(), "firmante_log.jsonl")


def publica_path():
    """Donde el daemon deja su clave pública para que se pueda leer sin el Llavero. Es un
    AVISO, no la fuente: lo que manda es la constante `permiso_envio.PUBLICA`."""
    return os.path.join(P.dir_permisos(), "firmante_publica.txt")


def _id_ok(v):
    return isinstance(v, str) and 0 < len(v) <= 64 and bool(_RE_ID.match(v))


def _hash_ok(v):
    return isinstance(v, str) and len(v) == 64 and bool(_RE_HASH.match(v))


def _respuesta(estado, motivo="", tipo="", n=0, lote=""):
    return dict(v=V_PROTO, estado=estado, motivo=motivo, tipo=tipo, n=n, lote=lote)


def respuesta_valida(r):
    """¿`r` es EXACTAMENTE una respuesta del protocolo? Esquema cerrado o nada."""
    return (isinstance(r, dict) and frozenset(r) == CAMPOS_RESPUESTA and r["v"] == V_PROTO
            and r["estado"] in ESTADOS and r["motivo"] in MOTIVOS and r["tipo"] in TIPOS
            and r["lote"] in LOTES and isinstance(r["n"], int) and not isinstance(r["n"], bool)
            and 0 <= r["n"] <= 10)


# ── el cliente (lo usa el hook) ──────────────────────────────────────────────────────────────
def pedir(session_id, prompt_id, hash_prompt, timeout=T_CLIENTE, ruta=None):
    """Pide al firmante que emita. Devuelve SIEMPRE una respuesta del esquema (nunca lanza):
    si el daemon no está, tarda, contesta otra cosa o es de otra versión, sale «rechazado» con
    `no-responde`, `respuesta-invalida` o `version`. Fail-closed: lo que no es un «emitido» o
    un «pendiente» bien formado no abre nada, y un «emitido» tampoco: el permiso es el fichero
    que escribe el daemon, y quien lo usa comprueba su firma."""
    import stat
    ruta = ruta or socket_path()
    limite = time.time() + timeout
    falla = dict(_respuesta("rechazado"), motivo=M_NO_RESPONDE)
    mala = dict(_respuesta("rechazado"), motivo=M_RESPUESTA)
    try:
        if len(ruta.encode("utf-8")) > MAX_SOCKET:
            return falla
        st = os.lstat(ruta)
        if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
            return falla
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except Exception:
        return falla
    datos = b""
    try:
        s.settimeout(max(0.05, limite - time.time()))
        s.connect(ruta)
        s.sendall((json.dumps(dict(v=V_PROTO, op="emitir", session_id=session_id,
                                   prompt_id=prompt_id, hash_prompt=hash_prompt)) + "\n").encode("utf-8"))
        while b"\n" not in datos and len(datos) <= MAX_LINEA:
            if time.time() >= limite:
                return falla
            s.settimeout(max(0.05, limite - time.time()))
            trozo = s.recv(MAX_LINEA)
            if not trozo:
                break
            datos += trozo
    except Exception:
        return falla
    finally:
        try:
            s.close()
        except Exception:
            pass
    if b"\n" not in datos or len(datos) > MAX_LINEA:
        return mala if datos else falla
    try:
        r = json.loads(datos.split(b"\n", 1)[0].decode("utf-8"))
    except Exception:
        return mala
    if isinstance(r, dict) and r.get("v") not in (None, V_PROTO):
        return dict(_respuesta("rechazado"), motivo="version")
    return r if respuesta_valida(r) else mala


# ── la clave ─────────────────────────────────────────────────────────────────────────────────
def _privada_de(hexa):
    ed, motivo = P.cripto()
    if not ed:
        raise RuntimeError(motivo)
    return ed.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(hexa))


def publica_de(privada):
    """La clave pública, en hex (64)."""
    from cryptography.hazmat.primitives import serialization
    return privada.public_key().public_bytes(serialization.Encoding.Raw,
                                             serialization.PublicFormat.Raw).hex()


def leer_clave():
    """(hex | None, estado) con estado "ok" | "no-existe" | "ilegible". Ver
    `permiso_envio._llavero_estado`: 44 es «no existe»; 36 (y cualquier otro fallo) es «existe
    o no se sabe, pero no se puede leer», y ahí NUNCA se crea otra."""
    try:
        r = subprocess.run([SECURITY, "find-generic-password", "-s", SERVICIO, "-w"],
                           capture_output=True, text=True, timeout=5)
    except Exception:
        return None, "ilegible"
    if r.returncode == 0:
        v = r.stdout.strip()
        return (v, "ok") if len(v) == 64 and _RE_HASH.match(v) else (None, "ilegible")
    return None, ("no-existe" if r.returncode == 44 else "ilegible")


def crear_clave():
    """Crea la clave privada en el Llavero y la devuelve (hex) SOLO si al releerla es la misma.
    El valor entra por la ENTRADA de `security -i`, no por argv: así no se ve en `ps` en el
    único momento en que existe fuera del Llavero. No se llama con el estado aislado (tests)."""
    import getpass
    if P._state_aislado():
        return None
    valor = secrets.token_bytes(32).hex()
    cuenta = os.environ.get("USER") or getpass.getuser()
    if not _RE_ID.match(cuenta):
        return None
    orden = "add-generic-password -a %s -s %s -w %s\n" % (cuenta, SERVICIO, valor)
    try:
        subprocess.run([SECURITY, "-i"], input=orden, capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    leida, estado = leer_clave()
    return leida if (estado == "ok" and leida == valor) else None


def halted():
    """¿Hay HALT o código rojo? Los mismos ficheros que mira `salida.halted()`."""
    rutas = [p for p in (os.environ.get("BTP_HALT_FILES") or "").split(":") if p] or \
        [os.path.join(os.path.expanduser("~"), ".btp.HALT"), os.path.join(_casa.casa_base(), ".HALT")]
    return any(os.path.exists(p) for p in rutas)


# ── el servidor ──────────────────────────────────────────────────────────────────────────────
class Firmante(object):

    def __init__(self, privada=None, estado_clave="ok", ruta=None, inline_s=INLINE_S,
                 espera_s=ESPERA_S, sondeo_s=SONDEO_S, hay_halt=None, relee_clave=None):
        self.privada, self.estado_clave = privada, estado_clave
        self.ruta = ruta or socket_path()
        self.inline_s, self.espera_s, self.sondeo_s = inline_s, espera_s, sondeo_s
        self.hay_halt = hay_halt or halted
        self.relee_clave = relee_clave
        self._ultima_relectura = 0.0
        self._vigias, self._cerrojo = set(), threading.Lock()
        self._sin_cambios = dict()       # (sesión, prompt, hash) -> huella de los transcripts ya mirados
        self._firmados = dict()          # (sesión, hora de la línea) -> (fichero, nonce) de lo ya firmado
        self._sock, self._parar = None, threading.Event()
        self.publica = publica_de(privada) if privada is not None else None

    # ·· registro
    def _log(self, pid_par, sesion, prompt, resultado, motivo="", detalle=""):
        try:
            linea = json.dumps(dict(ts=datetime.now().replace(microsecond=0).isoformat(),
                                    pid_par=pid_par, session_id=sesion, prompt_id=prompt,
                                    resultado=resultado, motivo=motivo, detalle=detalle),
                               ensure_ascii=False) + "\n"
            fd = os.open(log_path(), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, linea.encode("utf-8"))
            finally:
                os.close(fd)
        except Exception:
            pass

    # ·· el transcript lo busca el daemon
    def _transcripts(self, sesion):
        """Los transcripts con el nombre de esa sesión (ver `permiso_envio.transcripts_de`)."""
        return P.transcripts_de(sesion)

    @staticmethod
    def _huella(ruta):
        try:
            st = os.stat(ruta)
            return (ruta, st.st_size, st.st_mtime_ns)
        except Exception:
            return (ruta, -1, -1)

    def _clave_lista(self):
        """"" si se puede firmar; si no, el motivo. Si el Llavero no se dejaba leer al arrancar,
        se vuelve a intentar (como mucho cada 30 s), sin crear nada."""
        if self.privada is None and self.relee_clave and time.time() - self._ultima_relectura > 30:
            self._ultima_relectura = time.time()
            try:
                self.privada, self.estado_clave = self.relee_clave()
                self.publica = publica_de(self.privada) if self.privada is not None else None
            except Exception:
                self.privada = None
        if self.privada is None:
            return "sin-clave" if self.estado_clave == "no-existe" else "llavero-ilegible"
        if P.publica() and P.publica() != self.publica:
            return "clave-distinta"      # lo que firmase no lo daría por bueno nadie
        return ""

    def _intentar(self, sesion, prompt, hash_prompt):
        """(respuesta, detalle). Todo lo que decide si se firma, de una vez."""
        if self.hay_halt():
            return _respuesta("rechazado", "halt"), ""
        sin_clave = self._clave_lista()
        if sin_clave:
            return _respuesta("rechazado", sin_clave), ""
        rutas = self._transcripts(sesion)
        if not rutas:
            return _respuesta("pendiente"), "sin transcript todavía"
        # Releer un transcript de 150 MB cuesta ~0,25 s (medido): mientras se espera a que
        # aparezca la línea, solo se vuelve a leer si el fichero ha cambiado.
        clave, marca = (sesion, prompt, hash_prompt), tuple(self._huella(r) for r in rutas)
        if self._sin_cambios.get(clave) == marca:
            return _respuesta("pendiente"), P.M_NO_ESTA
        # TODAS las copias del transcript de esa sesión cuentan. Vale la que contiene su mensaje,
        # pero basta con que UNA lo contenga y lo rechace (escribió «espera» después, ya no es
        # el último) para no firmar: quedarse con la primera que decía que sí dejaba sin efecto
        # su revocación cuando había una copia vieja sin ella (verificacion, 14-oct-26).
        d, ctx, rechazo = None, None, ""
        for ruta in rutas:
            cand = dict(session_id=sesion, prompt_id=prompt, hash_prompt=hash_prompt, transcript_path=ruta)
            ok, motivo, c = P.contexto(cand)
            if ok and d is None:
                d, ctx = cand, c
            elif not ok and motivo not in (P.M_NO_ESTA, P.M_SIN_TRANSCRIPT):
                rechazo = rechazo or motivo
        if rechazo:
            return _respuesta("rechazado", "no-es-orden-suya"), rechazo
        if d is None:
            if len(self._sin_cambios) > 256:
                self._sin_cambios.clear()
            self._sin_cambios[clave] = marca
            return _respuesta("pendiente"), P.M_NO_ESTA
        self._sin_cambios.pop(clave, None)
        ruta = d["transcript_path"]
        ts = ctx.get("ts_linea")
        edad = P.edad(ts) if ts else None
        if edad is None:
            return _respuesta("rechazado", "sin-hora"), "la línea no trae hora"
        if edad > P.VIDA_S or edad < -60:
            return _respuesta("rechazado", "caducado"), "línea de hace %d s" % edad
        # A partir de aquí el permiso se identifica por la LÍNEA (sesión + su hora), no por el
        # `prompt_id` que mande quien llama: un mensaje encolado se localiza por el hash de su
        # texto, y con ids inventados se pedían permisos sin fin para un solo mensaje.
        d.update(v=P.V_PERMISO, ts=ts)
        if P._usado(d):
            return _respuesta("rechazado", "ya-usado"), ""
        import lote_envio as L
        que, items, m_lote = L.evaluar(ctx.get("crudo") or "", prompt, ruta)
        es_lote = que == "abre"
        ya = self._firmados.get((sesion, ts))
        if ya:
            # Una línea se firma UNA vez mientras este proceso viva, esté como esté el libro. Si
            # el permiso que se firmó sigue en disco, es ese (no se emite otro); si ya no está,
            # se gastó o se anuló, y no vuelve.
            try:
                with open(ya[0], encoding="utf-8") as f:
                    vivo = json.load(f).get("nonce") == ya[1]
            except Exception:
                vivo = False
            if not vivo:
                return _respuesta("rechazado", "ya-usado"), "ya se firmó para esa línea"
            if es_lote:
                return _respuesta("emitido", tipo="lote", n=len(items), lote="abre"), "ya firmado"
            return _respuesta("emitido", tipo="uso", lote=que), "ya firmado"
        d.update(alg=P.ALG, kid=P.kid(self.publica), origen="prompt",
                 motivo=re.sub(r"\s+", " ", ctx.get("texto") or "").strip()[:200],
                 nonce=secrets.token_hex(8), tipo="lote" if es_lote else "uso")
        if es_lote:
            d.update(n=len(items))
        destino = os.path.join(P.dir_permisos(), P._sid(sesion) + (EXT_LOTE if es_lote else EXT_USO))
        d["firma"] = self.privada.sign(P._canon2(d)).hex()
        self._escribir(destino, d)
        if len(self._firmados) > 2048:
            self._firmados.clear()
        self._firmados[(sesion, ts)] = (destino, d["nonce"])
        self._barrer()
        if es_lote:
            return _respuesta("emitido", tipo="lote", n=len(items), lote="abre"), ""
        return _respuesta("emitido", tipo="uso", lote=que), m_lote

    def _escribir(self, destino, d):
        os.makedirs(os.path.dirname(destino), mode=0o700, exist_ok=True)
        tmp = "%s.%d.%s.tmp" % (destino, os.getpid(), secrets.token_hex(4))
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, destino)

    def _barrer(self):
        """Se van los permisos del firmante que ya caducaron (de cualquier sesión)."""
        try:
            nombres = os.listdir(P.dir_permisos())
        except Exception:
            return
        for n in nombres:
            if not n.endswith((EXT_USO, EXT_LOTE)):
                continue
            ruta = os.path.join(P.dir_permisos(), n)
            try:
                with open(ruta, encoding="utf-8") as f:
                    edad = P.edad(json.load(f).get("ts"))
            except Exception:
                edad = None
            if edad is None or edad > VIDA_LOTE_S:
                try:
                    os.remove(ruta)
                except Exception:
                    pass

    # ·· una petición
    def atender(self, peticion, pid_par=None):
        """La respuesta a UNA petición ya leída (dict o lo que haya llegado)."""
        if isinstance(peticion, dict) and peticion.get("v") not in (None, V_PROTO):
            self._log(pid_par, None, None, "rechazado", "version")
            return _respuesta("rechazado", "version")
        if not (isinstance(peticion, dict) and frozenset(peticion) == CAMPOS_PETICION
                and peticion["v"] == V_PROTO and peticion["op"] == "emitir"
                and _id_ok(peticion["session_id"]) and _id_ok(peticion["prompt_id"])
                and _hash_ok(peticion["hash_prompt"])):
            self._log(pid_par, None, None, "rechazado", "peticion-invalida")
            return _respuesta("rechazado", "peticion-invalida")
        sesion, prompt, h = peticion["session_id"], peticion["prompt_id"], peticion["hash_prompt"]
        limite = time.time() + self.inline_s
        while True:
            try:
                r, detalle = self._intentar(sesion, prompt, h)
            except Exception as e:
                r, detalle = _respuesta("rechazado", "error"), type(e).__name__
            if r["estado"] != "pendiente" or time.time() >= limite:
                break
            time.sleep(self.sondeo_s)
        if r["estado"] == "pendiente" and not self._vigilar(sesion, prompt, h, pid_par):
            r, detalle = _respuesta("rechazado", "ocupado"), "demasiadas órdenes esperando"
        self._log(pid_par, sesion, prompt, r["estado"], r["motivo"], detalle)
        return r

    def _vigilar(self, sesion, prompt, h, pid_par):
        """Sigue mirando el transcript en segundo plano hasta `espera_s`. True si queda alguien
        mirando (este u otro que ya lo hacía)."""
        clave = (sesion, prompt, h)
        with self._cerrojo:
            if clave in self._vigias:
                return True
            if len(self._vigias) >= MAX_VIGIAS:
                return False
            self._vigias.add(clave)

        def _vigia():
            limite = time.time() + self.espera_s
            r, detalle = _respuesta("pendiente"), "no apareció en el transcript"
            try:
                while time.time() < limite and not self._parar.is_set():
                    time.sleep(self.sondeo_s)
                    try:
                        r, detalle = self._intentar(sesion, prompt, h)
                    except Exception as e:
                        r, detalle = _respuesta("rechazado", "error"), type(e).__name__
                    if r["estado"] != "pendiente":
                        break
            finally:
                with self._cerrojo:
                    self._vigias.discard(clave)
                self._log(pid_par, sesion, prompt, "tarde:" + r["estado"], r["motivo"], detalle)
        threading.Thread(target=_vigia, daemon=True).start()
        return True

    # ·· el socket
    @staticmethod
    def par(conn):
        """(uid, pid) del proceso al otro lado. (None, None) si no se puede saber."""
        try:
            if sys.platform == "darwin":
                cred = conn.getsockopt(0, getattr(socket, "LOCAL_PEERCRED", 1), 76)   # struct xucred
                uid = struct.unpack("=II", cred[:8])[1]
                try:
                    pid = struct.unpack("=i", conn.getsockopt(0, 2, 4))[0]            # LOCAL_PEERPID
                except Exception:
                    pid = None
                return uid, pid
            pid, uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            return uid, pid
        except Exception:
            return None, None

    def _conexion(self, conn):
        try:
            conn.settimeout(T_CONEXION)
            uid, pid = self.par(conn)
            if uid != os.getuid():
                self._log(pid, None, None, "rechazado", "par-ajeno", "uid %r" % uid)
                r = _respuesta("rechazado", "par-ajeno")
            else:
                datos = b""
                while b"\n" not in datos and len(datos) <= MAX_LINEA:
                    trozo = conn.recv(MAX_LINEA)
                    if not trozo:
                        break
                    datos += trozo
                try:
                    if b"\n" not in datos or len(datos) > MAX_LINEA:
                        raise ValueError("línea incompleta o demasiado larga")
                    peticion = json.loads(datos.split(b"\n", 1)[0].decode("utf-8"))
                except Exception:
                    peticion = None
                r = self.atender(peticion, pid)
            conn.sendall((json.dumps(r) + "\n").encode("utf-8"))
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def abrir(self):
        """Prepara el directorio (0700) y el socket (0600). Lanza si la ruta no cabe o si ya hay
        un firmante vivo escuchando ahí."""
        if len(self.ruta.encode("utf-8")) > MAX_SOCKET:
            raise RuntimeError("la ruta del socket no cabe en sun_path: %s" % self.ruta)
        carpeta = os.path.dirname(self.ruta)
        os.makedirs(carpeta, mode=0o700, exist_ok=True)
        os.chmod(carpeta, 0o700)
        if os.path.lexists(self.ruta):
            prueba = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            prueba.settimeout(0.5)
            try:
                prueba.connect(self.ruta)
                vivo = True
            except Exception:
                vivo = False
            finally:
                prueba.close()
            if vivo:
                raise RuntimeError("ya hay un firmante escuchando en %s" % self.ruta)
            os.remove(self.ruta)
        viejo = os.umask(0o177)
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.bind(self.ruta)
        finally:
            os.umask(viejo)
        os.chmod(self.ruta, 0o600)
        s.listen(16)
        s.settimeout(0.5)
        self._sock = s
        return self

    def servir(self):
        while not self._parar.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except Exception:
                if self._parar.is_set():
                    break
                time.sleep(0.2)
                continue
            threading.Thread(target=self._conexion, args=(conn,), daemon=True).start()

    def cerrar(self):
        self._parar.set()
        try:
            if self._sock is not None:
                self._sock.close()
        finally:
            try:
                os.remove(self.ruta)
            except Exception:
                pass


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────
def _cargar_clave(crear=False):
    """(privada | None, estado)."""
    valor, estado = leer_clave()
    if valor is None and estado == "no-existe" and crear:
        valor = crear_clave()
        estado = "ok" if valor else "ilegible"
    return (_privada_de(valor) if valor else None), estado


def _servir(crear):
    if _casa.en_worktree():
        print("firmante: no se sirve desde un worktree; solo desde casa base y por launchd.", file=sys.stderr)
        return 2
    if not P.cripto()[0]:
        print("firmante: " + P.cripto()[1], file=sys.stderr)
        return 3
    privada, estado = _cargar_clave(crear)
    f = Firmante(privada, estado, relee_clave=lambda: _cargar_clave(False))
    try:
        f.abrir()
    except RuntimeError as e:
        print("firmante: %s" % e, file=sys.stderr)
        return 0 if "ya hay un firmante" in str(e) else 4
    if f.publica:
        with open(publica_path(), "w", encoding="utf-8") as fh:
            fh.write(f.publica + "\n")
        os.chmod(publica_path(), 0o600)
    print("firmante: escuchando en %s · clave %s · pública %s" % (
        f.ruta, estado, f.publica or "(ninguna)"), flush=True)
    if f.publica and P.PUBLICA and P.PUBLICA != f.publica:
        print("firmante: OJO, la pública fijada en permiso_envio.PUBLICA NO es la de esta clave: "
              "no firmo nada hasta que coincidan.", file=sys.stderr, flush=True)
    try:
        f.servir()
    finally:
        f.cerrar()
    return 0


def _estado():
    ruta = socket_path()
    hay = os.path.lexists(ruta)
    print("socket: %s (%s)" % (ruta, "existe" if hay else "NO existe: el firmante no está activo"))
    try:
        with open(publica_path(), encoding="utf-8") as f:
            anunciada = f.read().strip()
    except Exception:
        anunciada = ""
    print("pública que anuncia el daemon: %s" % (anunciada or "(ninguna)"))
    print("pública fijada en permiso_envio.PUBLICA: %s" % (P.PUBLICA or "(ninguna: sigue el HMAC de antes)"))
    if anunciada and P.PUBLICA:
        print("coinciden: %s" % ("sí" if anunciada == P.PUBLICA else "NO"))
    print("cryptography en este python: %s" % ("sí" if P.cripto()[0] else "NO"))
    return 0 if hay else 1


def _publica():
    valor, estado = leer_clave()
    if not valor:
        print("no puedo leer la clave del Llavero (%s). Esto solo funciona en una sesión LOCAL "
              "del Mac (Terminal), no por SSH." % estado, file=sys.stderr)
        return 1
    pub = publica_de(_privada_de(valor))
    print(pub)
    print("kid %s · línea para tools/permiso_envio.py:  PUBLICA = \"%s\"" % (P.kid(pub), pub), file=sys.stderr)
    return 0


def main(argv):
    orden = argv[1] if len(argv) > 1 else ""
    if orden == "servir":
        return _servir("--crear-clave-si-falta" in argv[2:])
    if orden == "estado":
        return _estado()
    if orden == "publica":
        return _publica()
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
