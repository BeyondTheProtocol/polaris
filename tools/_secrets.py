#!/usr/bin/env python3
"""Carga de secretos para las tools de Beyond the Protocol.

Orden de búsqueda:
  1) Llavero de macOS  (security find-generic-password -s <servicio> -w)
  2) fichero .X_secrets.json  (fallback de retro-compatibilidad)

Objetivo de seguridad: las claves viven en el Llavero; el fichero queda solo
como red mientras se completa la migración. Nunca imprime el valor.
"""
import json
import os
import subprocess

# Ruta ABSOLUTA (13-oct-26). Con el nombre desnudo, el `security` que se ejecuta lo elige el PATH
# de quien lanza la tool: un `security` falso en un directorio anterior recibiría el secreto que
# se guarda (va en argv) y podría devolver el valor que quisiera al leer.
# Test: tests/test_permiso_security_ruta.py.
SECURITY = "/usr/bin/security"
# Las claves que firman el permiso de salida no se leen ni se escriben por aquí (14-oct-26):
# `_secrets.get("btp-firmante-ed25519")` desde cualquier script era la vía más corta para
# sacarlas en una sesión local. Quien las necesita las lee él mismo (permiso_envio, firmante).
RESERVADOS = frozenset(("btp-ok-envio-mac", "btp-firmante-ed25519"))

# El broker de la sesión gráfica (17-oct-26, tools/secretos_broker.py). Por SSH el Llavero sale
# bloqueado (rc=36) aunque {{TITULAR}} haya entrado en el Mac; el broker vive en gui/501, donde sí se
# lee, y sirve una lista cerrada. Si no contesta, se cae a `security` como siempre.
# `BTP_SECRETOS_BROKER=0` lo apaga (lo exporta test_all.sh) y un proceso que ES un test
# (`tests/test_*.py`, lanzado a mano o por la suite) tampoco lo usa salvo `=1` explícito: con el
# broker encendido, un test que mockea `security` recibiría un valor real y un assert lo imprimiría.
# Último estado que dio el broker por servicio (nunca el valor), para quien quiera explicar un None.
_ULTIMO_BROKER = {}
T_BROKER = 2.0
# Las ÚNICAS que sirve el broker (tramo A del plan del 17-oct-26). Ampliarla = commit + OK de
# {{TITULAR}}; las que firman, autentican, descifran o publican están en `secretos_broker.NUNCA` y no
# entran aunque se añadan aquí (el test lo fija).
BROKER_PERMITIDAS = frozenset((
    "btp-gmail-app-password", "btp-gmail-app-password-2", "btp-gmail-app-password-3",
    "btp-gdrive-client-id", "btp-gdrive-client-secret", "btp-gdrive-refresh-token",
    "btp-umami-api", "btp-gcal-sa-key", "btp-hf-token", "btp-synapse-token",
    "btp-youtube-api", "btp-canario-llavero",
    # Tramo B, opción 1 ({{TITULAR}}, 17-oct-26: «abre el tramo B, opción 1»): las claves de modelo
    # cuyo gasto llega a cost_guard (Claude lo gatea en ia.py; las otras ocho lo apuntan vía
    # gasto.registrar → avisa al pasar el tope, no corta antes), y ElevenLabs (cuota de su plan,
    # sin contador nuestro). Telegram y el resto de APIs de pago siguen fuera.
    "btp-anthropic-api", "btp-openai-api", "btp-gemini-api", "btp-grok-api", "btp-glm-api",
    "btp-nan-api", "btp-nvidia-api", "btp-openrouter-api", "btp-perplexity-api",
    "btp-elevenlabs-api",
))


def broker_socket():
    """La ruta del socket del broker. Sigue a BTP_STATE_DIR (los tests no ven el real)."""
    state = os.environ.get("BTP_STATE_DIR") or os.path.join(
        os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"), "tools", "state")
    return os.path.join(state, "_secrets", "broker.sock")


def _broker_pedir(peticion, ruta=None, timeout=T_BROKER):
    """La respuesta (dict) del broker, o None si no está o no contesta a tiempo."""
    import socket
    ruta = ruta or broker_socket()
    if not os.path.exists(ruta):
        return None
    s = None
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(ruta)
        s.sendall((json.dumps(peticion) + "\n").encode("utf-8"))
        datos = b""
        while b"\n" not in datos and len(datos) < 65536:
            trozo = s.recv(65536)
            if not trozo:
                break
            datos += trozo
        r = json.loads(datos.split(b"\n", 1)[0].decode("utf-8"))
        return r if isinstance(r, dict) else None
    except Exception:
        return None
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


def _es_test():
    """¿Este proceso es un test? Mismo criterio que `_casa.es_proceso_de_test` (aquí sin importar
    nada: _secrets lo carga medio repo)."""
    import sys
    a = sys.argv[0] if sys.argv else ""
    if a in ("", "-c", "-"):
        return False
    a = os.path.abspath(a)
    return os.path.basename(os.path.dirname(a)) == "tests" and os.path.basename(a).startswith("test_")


def _broker(service):
    """El secreto vía broker, o None (sin broker, apagado, o no lo sirve)."""
    modo = os.environ.get("BTP_SECRETOS_BROKER", "")
    if modo == "0" or (modo != "1" and _es_test()) or service not in BROKER_PERMITIDAS:
        return None
    r = _broker_pedir({"v": 1, "servicio": service})
    if r is None:
        return None
    _ULTIMO_BROKER[service] = str(r.get("estado") or "")[:20]
    v = r.get("valor") if r.get("estado") == "ok" else None
    return v if isinstance(v, str) and v.strip() else None


def _keychain(service):
    """Devuelve el secreto del Llavero o None."""
    if str(service or "").strip().lower() in RESERVADOS:
        return None
    v = _broker(service)
    if v:
        return v.strip()
    try:
        r = subprocess.run(
            [SECURITY, "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            v = r.stdout.strip()
            return v or None
    except Exception:
        pass
    return None


# Por qué se distingue (17-oct-26): con el login.keychain BLOQUEADO, `-w` da rc=36 («User
# interaction is not allowed») aunque la clave exista, y las tools decían «falta en el Llavero».
# Mandaba a buscar una credencial que estaba ahí. Y desbloquear desde su Terminal tampoco basta:
# las sesiones de Claude entran por SSH (sesión «Background», verificado con `launchctl
# managername`) y no heredan el desbloqueo del login gráfico. El arreglo de raíz es un broker en
# la sesión gráfica (rama claude/awesome-babbage-cee7dd). Test: tests/test_correo_outbox_hilo.py.
DESBLOQUEO = ("bloqueado para las sesiones por SSH: no lo arregla desbloquear en tu Terminal; "
              "arreglo de raíz, el broker de secretos (rama claude/awesome-babbage-cee7dd); "
              "para adjuntar, la vía Chrome de .claude/rules/correo.md")


def estado(service):
    """"ok", "bloqueado" (existe pero el Llavero está cerrado) o "falta". Nunca devuelve el valor."""
    if _keychain(service):
        return "ok"
    try:
        r = subprocess.run([SECURITY, "find-generic-password", "-s", service],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return "bloqueado"
    except Exception:
        pass
    return "falta"


def _file_value(path, key):
    try:
        with open(path) as f:
            return (json.load(f) or {}).get(key)
    except Exception:
        return None


def set(service, valor, cuenta=None):
    """Guarda un secreto en el Llavero y COMPRUEBA que entró entero.

    Por qué no vale `security add-generic-password -w` a secas: su prompt interactivo —y su
    lectura por stdin— TRUNCAN el valor a 128 caracteres sin decir nada. El 20-sep-26 el token
    personal de Synapse (765 caracteres) se guardó cortado tres veces y la API respondía
    401; el mismo valor pasado como ARGUMENTO entró entero. Afecta a cualquier token largo
    (OAuth, refresh tokens, JWT).

    El valor aparece un instante en `argv`, visible en `ps`: por eso esto lo llama Polaris y no
    se teclea en una shell (no deja rastro en el historial), y si el secreto ya se ha visto en
    claro, se revoca y se regenera. A cambio, se guarda entero y se verifica releyéndolo.
    """
    import getpass
    valor = (valor or "").strip()
    if not valor:
        raise ValueError("secreto vacío: no se guarda")
    if str(service or "").strip().lower() in RESERVADOS:
        raise ValueError("esa clave firma el permiso de salida: no se escribe por aquí")
    cuenta = cuenta or os.environ.get("USER") or getpass.getuser()
    r = subprocess.run([SECURITY, "add-generic-password", "-U", "-a", cuenta,
                        "-s", service, "-w", valor], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError("el Llavero rechazó el secreto (rc=%d)" % r.returncode)
    leido = _keychain(service) or ""
    if leido != valor:
        raise RuntimeError("el Llavero guardó %d caracteres de %d: NO te fíes de este secreto"
                           % (len(leido), len(valor)))
    return len(valor)


def get(service, file_path=None, file_key=None, default=None):
    """Secreto: Llavero[service] o, si falta, fichero[file_key]; si no, default."""
    v = _keychain(service)
    if v:
        return v
    if file_path and file_key:
        v = _file_value(file_path, file_key)
        if v:
            return v
    return default
