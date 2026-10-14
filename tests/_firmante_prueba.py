#!/usr/bin/env python3
"""tests/_firmante_prueba.py — banco de pruebas del firmante: daemon de verdad en un socket
temporal, clave de prueba en proceso, transcripts con la forma real y los hooks por subproceso.

Nada de esto toca el Llavero, el estado de casa base ni el socket del firmante real: la clave
se genera en memoria, el estado es un directorio temporal y la pública de prueba llega a los
hooks por `tests/_corre_hook.py` (costura en proceso, no una variable que lea producción).

`test_all.sh` corre con /usr/bin/python3, que no trae `cryptography`. Las baterías que firman
llaman a `reexec()` al principio: se relanzan con un intérprete que sí la tiene. Si no hay
ninguno, FALLAN (en el CI portable, saltan con rc=77): un test del muro que no corre no puede
pasar por verde.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(ROOT, ".claude", "hooks")
EMISOR = os.path.join(HOOKS, "ok_envio_prompt.py")
SALIDA = os.path.join(HOOKS, "salida_guard.py")
CORRE = os.path.join(ROOT, "tests", "_corre_hook.py")
CASA = os.path.expanduser("~/claudecode")
GMAIL = "mcp__b47695e8-1614-4b1d-81db-9d1d88117c68__"
CANDIDATOS = (sys.executable, "/opt/homebrew/bin/python3", shutil.which("python3") or "")


def _tiene_cripto(py):
    if not py or not os.path.exists(py):
        return False
    try:
        return subprocess.run([py, "-c", "import cryptography.hazmat.primitives.asymmetric.ed25519"],
                              capture_output=True, timeout=30).returncode == 0
    except Exception:
        return False


def interprete():
    """Un python3 con `cryptography`, o None."""
    for py in CANDIDATOS:
        if _tiene_cripto(py):
            return py
    return None


def reexec():
    """Si este intérprete no puede firmar, relanza el test con uno que sí."""
    try:
        import cryptography.hazmat.primitives.asymmetric.ed25519  # noqa: F401
        return
    except Exception:
        pass
    py = interprete()
    if py and os.path.realpath(py) != os.path.realpath(sys.executable):
        os.execv(py, [py] + sys.argv)
    print("NO HAY ningún python3 con la librería `cryptography` (probados: %s). Sin ella no se "
          "puede comprobar la firma del permiso: en producción todo envío quedaría denegado."
          % ", ".join(c for c in CANDIDATOS if c))
    sys.exit(77 if os.environ.get("BTP_PORTABLE") else 1)


def ahora_z(hace_s=0):
    """La hora como la escribe Claude Code en el transcript: UTC, con milisegundos y Z."""
    t = datetime.now(timezone.utc) - timedelta(seconds=hace_s)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (t.microsecond // 1000)


def decision(r):
    if r.returncode == 2:
        return "deny"
    if not r.stdout.strip():
        return None
    return json.loads(r.stdout)["hookSpecificOutput"].get("permissionDecision")


class Banco(object):
    """Un firmante de prueba vivo, su clave, su estado y una sesión con transcript."""

    def __init__(self, inline_s=0.4, espera_s=1.5, arrancar=True, hay_halt=None):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import firmante as F
        import permiso_envio as P
        from cryptography.hazmat.primitives.asymmetric import ed25519
        self.F, self.P = F, P
        self.tmp = tempfile.mkdtemp(prefix="fm-")
        self.estado = os.path.join(self.tmp, "st")
        self.raiz = os.path.join(self.tmp, "projects")
        self.proyecto = os.path.join(self.raiz, "-p")
        os.makedirs(self.proyecto)
        os.makedirs(self.estado)
        self._viejo = dict(BTP_STATE_DIR=os.environ.get("BTP_STATE_DIR"))
        os.environ["BTP_STATE_DIR"] = self.estado
        self.privada = ed25519.Ed25519PrivateKey.generate()
        self.publica = F.publica_de(self.privada)
        P.PUBLICA_TEST, P.RAIZ_TRANSCRIPTS_TEST, P.MODO_TEST = self.publica, self.raiz, P.ALG
        self.halt = [False]
        self.daemon = F.Firmante(self.privada, inline_s=inline_s, espera_s=espera_s, sondeo_s=0.05,
                                 hay_halt=hay_halt or (lambda: self.halt[0]))
        self.hilo = None
        if arrancar:
            self.arrancar()
        self.nueva_sesion()

    # ·· vida del daemon
    def arrancar(self):
        self.daemon._parar.clear()
        self.daemon.abrir()
        self.hilo = threading.Thread(target=self.daemon.servir, daemon=True)
        self.hilo.start()

    def parar(self):
        """El daemon se cae: deja de escuchar y su socket desaparece."""
        self.daemon.cerrar()
        if self.hilo:
            self.hilo.join(timeout=3)
            self.hilo = None

    def cerrar(self):
        self.parar()
        P = self.P
        P.PUBLICA_TEST = P.RAIZ_TRANSCRIPTS_TEST = P.MODO_TEST = None
        if self._viejo["BTP_STATE_DIR"] is None:
            os.environ.pop("BTP_STATE_DIR", None)
        else:
            os.environ["BTP_STATE_DIR"] = self._viejo["BTP_STATE_DIR"]
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ·· la sesión y su transcript
    def nueva_sesion(self):
        self.sesion = str(uuid.uuid4())
        self.transcript = os.path.join(self.proyecto, self.sesion + ".jsonl")
        open(self.transcript, "w").close()
        return self.sesion

    def linea(self, d, ruta=None):
        with open(ruta or self.transcript, "a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    def humano(self, texto, origen="human", lateral=False, hace_s=0, pid=None, ruta=None):
        pid = pid or str(uuid.uuid4())
        d = dict(type="user", promptId=pid, isSidechain=lateral, sessionId=self.sesion,
                 entrypoint="claude-desktop", timestamp=ahora_z(hace_s),
                 message=dict(role="user", content=texto))
        if origen is not None:
            d["origin"] = dict(kind=origen)
        self.linea(d, ruta)
        return pid

    def asistente(self, texto):
        self.linea(dict(type="assistant", isSidechain=False, timestamp=ahora_z(),
                        message=dict(role="assistant", content=[dict(type="text", text=texto)])))

    def herramienta(self, nombre, entrada):
        self.linea(dict(type="assistant", isSidechain=False, timestamp=ahora_z(),
                        message=dict(role="assistant", content=[
                            dict(type="tool_use", id="toolu_" + uuid.uuid4().hex[:8],
                                 name=nombre, input=entrada)])))

    def encolado(self, texto, origen="human", modo="prompt", humano=True, hace_s=0):
        self.linea(dict(type="attachment", isSidechain=False, sessionId=self.sesion,
                        timestamp=ahora_z(hace_s),
                        attachment=dict(type="queued_command", prompt=texto, commandMode=modo,
                                        origin=dict(kind=origen), humanTurn=humano)))

    # ·· los hooks, como los lanza el harness
    def env(self, **mas):
        e = dict(os.environ, BTP_STATE_DIR=self.estado, BTP_TEST_RAIZ_TRANSCRIPTS=self.raiz,
                 BTP_TEST_PUBLICA=self.publica, BTP_TEST_MODO=self.P.ALG)
        for clave in ("MURO_PROFILE", "BTP_OK_ENVIO_CLAVE"):
            e.pop(clave, None)
        e.update(mas)
        return e

    def emitir(self, texto, pid, sesion=None, env=None):
        """Lanza el hook UserPromptSubmit. Devuelve el proceso (su stdout es el aviso)."""
        carga = dict(hook_event_name="UserPromptSubmit", prompt=texto, prompt_id=pid,
                     session_id=sesion or self.sesion, transcript_path=self.transcript)
        return subprocess.run([sys.executable, CORRE, EMISOR], input=json.dumps(carga),
                              capture_output=True, text=True, timeout=60, env=env or self.env())

    def ordenar(self, texto, **kw):
        """{{TITULAR}} escribe `texto`: el harness lo apunta en el transcript y lanza el hook."""
        pid = self.humano(texto)
        self.ultimo = self.emitir(texto, pid, **kw)
        return pid

    def guard(self, tool, entrada, sesion=None, cwd=CASA, env=None, crudo=False):
        carga = dict(hook_event_name="PreToolUse", tool_name=tool, tool_input=entrada,
                     session_id=sesion or self.sesion, transcript_path=self.transcript,
                     cwd=cwd, permission_mode="bypassPermissions")
        r = subprocess.run([sys.executable, CORRE, SALIDA], input=json.dumps(carga),
                           capture_output=True, text=True, timeout=60, env=env or self.env())
        return r if crudo else decision(r)

    def enviar(self, entrada=None, tool="send_message", **kw):
        return self.guard(GMAIL + tool, entrada or dict(to=["alguien@hospital.org"],
                                                        subject="s", body="b"), **kw)

    # ·· lo que hay en disco
    def token(self, sesion=None):
        return os.path.join(self.estado, "ok_envio", (sesion or self.sesion) + ".ok2")

    def lote(self, sesion=None):
        return os.path.join(self.estado, "ok_envio", (sesion or self.sesion) + ".lote2")

    def pedir(self, pid, texto, sesion=None, **kw):
        """Petición directa al socket, sin pasar por el hook."""
        return self.F.pedir(sesion or self.sesion, pid, self.P._hash_texto(texto), **kw)

    def registro(self):
        try:
            with open(self.F.log_path(), encoding="utf-8") as f:
                return [json.loads(l) for l in f]
        except FileNotFoundError:
            return []
