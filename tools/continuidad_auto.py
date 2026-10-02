#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""continuidad_auto.py — la continuidad se escribe SOLA al cerrar o compactar una sesión.

POR QUÉ (29-sep-26, plan «Vega al mando», Fase 1.1)
----------------------------------------------------
`continuity.py record` solo se llamaba a mano. Medido ese día: 192 entradas, a ráfagas (51 el
24-sep, 1 el 27-sep) y semanas enteras sin ninguna. Lo que {{TITULAR}} decidía en el chat («vale,
WES», «a fuego») no quedaba en ningún sitio que la sesión siguiente leyera.

QUÉ HACE
--------
Hook `SessionEnd` y `PreCompact`. Lee de la transcripción SOLO lo nuevo desde la última pasada
(offset por sesión), y de ahí SOLO los mensajes de {{TITULAR}} y mis textos: nunca resultados de
herramientas, que son contenido externo (anti-inyección). Si hay conversación de verdad, Haiku la
resume (decidido · reglas nuevas · hecho · pendiente), el resumen pasa por `deid.py` y se apunta
con `continuity.record`. Si no queda limpio de identificadores, NO se escribe (continuity no
guarda PII ni clínico: lo dice su cabecera).

Coste: Haiku por la suscripción Max (token del Llavero), € marginal 0. Nunca la API medida.

GARANTÍAS
---------
· No frena el cierre: el hook se desengancha y sale al instante; el trabajo va en segundo plano.
· No hay bucle: el `claude -p` hijo corre con los hooks desactivados y con
  BTP_CONTINUIDAD_AUTO_HIJO=1, que hace salir a este script si llegara a dispararse.
· Fail-open hacia el cierre, fail-closed hacia la escritura: cualquier error → exit 0 sin escribir.

BARRIDO (`--barrer [--seco]`, 1-oct-2026): para las sesiones que el hook no ve (las abiertas
fuera de ~/claudecode y las que nunca terminan). Lo lanza el arranque de sesión en segundo plano.

Bypass: BTP_CONTINUIDAD_AUTO_OFF=1. Ganchos de test: BTP_CLAUDE_BIN, BTP_STATE_DIR,
BTP_CONTINUIDAD_TOKEN (evita el Llavero).
"""
import fcntl
import glob
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import continuity  # noqa: E402

MIN_MENSAJES_TITULAR = 2      # menos que esto no es una sesión, es un vistazo
MIN_CARACTERES = 400
MAX_EXTRACTO = 40000         # cola de la conversación que ve Haiku
MAX_RESUMEN = 1200

# Archivo de la conversación (Fase 1.3). Las transcripciones crudas de Claude Code se borran a los
# 30 días y no se pueden guardar más: ~430 MB/día medidos el 29-sep, un año no cabe en el disco.
# Solo la conversación ({{TITULAR}} + textos del asistente) son ~10 MB/día: esa sí se guarda, en la
# zona privada, donde kb.py la indexa como `private` y nunca sale de la máquina.
def _dir_sesiones():
    return os.environ.get("BTP_SESIONES_DIR") or os.path.join(
        continuity.REPO, "00_FUENTE-DE-VERDAD", "_PRIVADO_SESIONES", "conversaciones")


def archivar(session_id, turnos):
    """Anexa los turnos nuevos al archivo de la sesión. Devuelve la ruta o None."""
    if not turnos:
        return None
    from datetime import datetime
    carpeta = os.path.join(_dir_sesiones(), datetime.now().strftime("%Y-%m"))
    os.makedirs(carpeta, mode=0o700, exist_ok=True)
    ruta = os.path.join(carpeta, "%s.md" % "".join(c for c in session_id if c.isalnum() or c == "-"))
    nueva = not os.path.exists(ruta)
    with open(ruta, "a", encoding="utf-8") as fh:
        if nueva:
            fh.write("# Sesión %s\n" % session_id)
        fh.write("\n## %s\n\n%s\n" % (datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                                      "\n\n".join(turnos)))
    os.chmod(ruta, 0o600)
    return ruta

PROMPT = """Eres el cronista interno de un asistente de IA. Abajo va un tramo de conversación entre
{{TITULAR}} (la usuaria) y el asistente. Escribe en español, máximo 900 caracteres, SOLO con estas
secciones (omite las vacías):
Decidido: lo que {{TITULAR}} aprobó o decidió.
Reglas nuevas: instrucciones de cómo quiere que se trabaje a partir de ahora, casi literales.
Hecho: lo que quedó terminado (con commits o rutas si aparecen).
Pendiente: lo que quedó a medias o espera algo.
Prohibido incluir: datos clínicos o de salud, nombres de personas distintas de {{TITULAR}}, correos,
teléfonos, direcciones o claves. Si no hay nada que valga la pena guardar, responde solo: NADA

--- CONVERSACIÓN ---
"""


def _estado_offsets():
    return os.path.join(continuity.CONT, "auto_offsets.json")


def _leer_offsets():
    try:
        with open(_estado_offsets(), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _guardar_offset(session_id, offset):
    # Con candado: el hook y el barrido pueden escribir a la vez, y un leer-cambiar-escribir sin
    # bloqueo pierde el offset del otro (Haiku resumiría dos veces la misma sesión).
    os.makedirs(continuity.CONT, mode=0o700, exist_ok=True)
    with open(_estado_offsets() + ".lock", "w") as candado:
        fcntl.flock(candado, fcntl.LOCK_EX)
        d = _leer_offsets()
        d[session_id] = offset
        tmp = _estado_offsets() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        os.replace(tmp, _estado_offsets())


def _texto_de(contenido):
    if isinstance(contenido, str):
        return contenido
    if isinstance(contenido, list):
        return "\n".join(p.get("text", "") for p in contenido
                         if isinstance(p, dict) and p.get("type") == "text")
    return ""


def extraer(transcript_path, desde=0):
    """(turnos, n_titular, offset_final). Solo lo nuevo desde `desde` (en bytes)."""
    turnos, n_titular = [], 0
    with open(transcript_path, "rb") as fh:
        fh.seek(desde)
        datos = fh.read()
    offset = desde + len(datos)
    for linea in datos.decode("utf-8", "replace").splitlines():
        try:
            d = json.loads(linea)
        except Exception:
            continue
        if d.get("isSidechain"):
            continue
        tipo = d.get("type")
        msg = d.get("message") or {}
        if tipo == "user" and isinstance(msg.get("content"), str):
            # Un str es un mensaje humano. Los tool_result llegan como lista: se descartan.
            texto = msg["content"].strip()
            if texto and not texto.startswith("<"):
                turnos.append("TITULAR: " + texto)
                n_titular += 1
        elif tipo == "assistant":
            texto = _texto_de(msg.get("content")).strip()
            if texto:
                turnos.append("ASISTENTE: " + texto)
    return turnos, n_titular, offset


def _token():
    t = os.environ.get("BTP_CONTINUIDAD_TOKEN")
    if t is not None:
        return t
    try:
        return subprocess.run(["security", "find-generic-password", "-s", "btp-claude-oauth-token",
                               "-w"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def resumir(extracto):
    token = _token()
    if not token:
        return None
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env.update(CLAUDE_CODE_OAUTH_TOKEN=token, BTP_CONTINUIDAD_AUTO_HIJO="1")
    cmd = [os.environ.get("BTP_CLAUDE_BIN", "claude"), "-p", "--model", "haiku",
           "--settings", json.dumps({"disableAllHooks": True}), PROMPT + extracto]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env,
                           cwd=tempfile.gettempdir())
    except Exception:
        return None
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out or out.upper().startswith("NADA"):
        return None
    return out[:MAX_RESUMEN]


def limpiar(resumen):
    """Desidentifica. None si no queda limpio: fail-closed hacia la escritura."""
    try:
        import deid
        texto, _n, ok, _motivo = deid.de_identificar_verificado(resumen)
        return texto if ok else None
    except Exception:
        return None


def procesar(evento):
    sid = str(evento.get("session_id") or "")
    ruta = evento.get("transcript_path") or ""
    if not sid or not ruta or not os.path.exists(ruta):
        return "sin transcripción"
    desde = int(_leer_offsets().get(sid, 0))
    turnos, n_titular, offset = extraer(ruta, desde)
    extracto = "\n\n".join(turnos)
    # El archivo lleva SU PROPIO offset: el del resumen no avanza cuando Haiku falla (se reintenta),
    # y reusarlo duplicaría la conversación archivada en cada reintento.
    clave_arch = sid + "#archivo"
    desde_arch = int(_leer_offsets().get(clave_arch, 0))
    try:
        turnos_arch, n_arch, off_arch = extraer(ruta, desde_arch)
        if n_arch:
            archivar(sid, turnos_arch)
        _guardar_offset(clave_arch, off_arch)
    except Exception:
        pass
    if n_titular < MIN_MENSAJES_TITULAR or len(extracto) < MIN_CARACTERES:
        _guardar_offset(sid, offset)
        return "sesión demasiado corta"
    resumen = resumir(extracto[-MAX_EXTRACTO:])
    if not resumen:
        return "sin resumen"      # offset intacto: la próxima pasada lo reintenta
    limpio = limpiar(resumen)
    if not limpio:
        _guardar_offset(sid, offset)
        return "descartado: quedaban identificadores"
    nombre = str(evento.get("hook_event_name") or "auto")
    continuity.record(limpio, procedencia="confiable", fuente="auto %s %s" % (nombre, sid[:8]))
    _guardar_offset(sid, offset)
    return "apuntado"


# ─── Barrido (1-oct-2026, plan «Vega aprende y se adelanta», eslabón 1) ─────────────────────
# El hook es de proyecto: las sesiones abiertas en /Users/polaris no lo cargan (medido el 1-oct:
# 0 de 2.190 sesiones procesadas venían de ahí) y en la app de escritorio una sesión puede no
# terminar nunca. El barrido pasa `procesar()` por las transcripciones quietas de las carpetas
# permitidas. Es idempotente con el hook: los dos avanzan el mismo offset por sesión.
INACTIVA_SEG = 2 * 3600          # más quieta que esto = la conversación ha parado (por ahora)
VENTANA_DIAS = 3
MAX_RESUMENES_BARRIDO = 10       # tope de llamadas a Haiku por pasada; lo demás, a la siguiente
_SIN_HAIKU = ("sesión demasiado corta", "sin transcripción")


def _projects_dir():
    return os.environ.get("BTP_PROJECTS_DIR") or os.path.expanduser("~/.claude/projects")


def _permitidas():
    """Carpetas de ~/.claude/projects que se barren. Una entrada con «*» final es prefijo.
    Por defecto, las sesiones abiertas en el home y en ~/claudecode (worktrees incluidos): así
    quedan fuera las del `claude -p` hijo de este script (cwd = tmp) y las de otros proyectos."""
    crudo = os.environ.get("BTP_CONTINUIDAD_CARPETAS")
    if crudo:
        return [c.strip() for c in crudo.split(",") if c.strip()]
    home = os.path.expanduser("~").replace("/", "-")
    return [home, home + "-claudecode*"]


def _carpeta_permitida(nombre, permitidas):
    for p in permitidas:
        if p.endswith("*") and nombre.startswith(p[:-1]):
            return True
        if nombre == p:
            return True
    return False


def _es_interactiva(ruta, lineas=60):
    """True si la transcripción es de una persona (app de escritorio o terminal). Los `claude -p`
    del lazo llevan entrypoint «sdk-…»: su contenido es de jobs (dato derivado, no confiable) y
    Vega ya recibe sus partes por otra vía. Sin entrypoint, fuera (fail-closed)."""
    try:
        with open(ruta, encoding="utf-8", errors="replace") as fh:
            for i, linea in enumerate(fh):
                if i >= lineas:
                    break
                try:
                    ep = json.loads(linea).get("entrypoint")
                except Exception:
                    continue
                if ep:
                    return not str(ep).startswith("sdk")
    except OSError:
        pass
    return False


def candidatas_barrido(ahora=None):
    """Transcripciones quietas, recientes, de carpetas permitidas y con algo sin procesar.
    De la más vieja a la más nueva, para que continuity quede en orden."""
    ahora = ahora or time.time()
    permitidas = _permitidas()
    offsets = _leer_offsets()
    out = []
    for ruta in glob.glob(os.path.join(_projects_dir(), "*", "*.jsonl")):
        if not _carpeta_permitida(os.path.basename(os.path.dirname(ruta)), permitidas):
            continue
        try:
            st = os.stat(ruta)
        except OSError:
            continue
        if ahora - st.st_mtime < INACTIVA_SEG or ahora - st.st_mtime > VENTANA_DIAS * 86400:
            continue
        sid = os.path.basename(ruta)[:-len(".jsonl")]
        if (int(offsets.get(sid, 0)) >= st.st_size
                and int(offsets.get(sid + "#archivo", 0)) >= st.st_size):
            continue
        if not _es_interactiva(ruta):
            continue
        out.append((st.st_mtime, sid, ruta))
    return [(sid, ruta) for _m, sid, ruta in sorted(out)]


def barrer(seco=False):
    """Una pasada. Devuelve {sid: resultado}. Un candado evita dos barridos a la vez."""
    os.makedirs(continuity.CONT, mode=0o700, exist_ok=True)
    with open(os.path.join(continuity.CONT, "barrido.lock"), "w") as candado:
        try:
            fcntl.flock(candado, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"_": "otro barrido en curso"}
        res, con_haiku = {}, 0
        for sid, ruta in candidatas_barrido():
            if seco:
                res[sid] = "candidata"
                continue
            if con_haiku >= MAX_RESUMENES_BARRIDO:
                break
            try:
                r = procesar({"session_id": sid, "transcript_path": ruta,
                              "hook_event_name": "Barrido"})
            except Exception as e:  # una transcripción rota no para el barrido
                r = "error: %s" % type(e).__name__
            res[sid] = r
            if r not in _SIN_HAIKU:
                con_haiku += 1
        return res


def main(argv):
    if os.environ.get("BTP_CONTINUIDAD_AUTO_OFF") or os.environ.get("BTP_CONTINUIDAD_AUTO_HIJO"):
        return 0
    if argv and argv[0] == "--barrer":
        res = barrer(seco="--seco" in argv)
        for sid, r in res.items():
            print("%s  %s" % (sid[:8], r))
        print("barrido: %d sesiones" % len(res))
        return 0
    try:
        if argv and argv[0] == "--sync":
            with open(argv[1], encoding="utf-8") as fh:
                evento = json.load(fh)
            os.remove(argv[1])
            print(procesar(evento))
            return 0
        evento = json.loads(sys.stdin.read() or "{}")
        fd, ruta = tempfile.mkstemp(prefix="cont_auto_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(evento, fh)
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--sync", ruta],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
