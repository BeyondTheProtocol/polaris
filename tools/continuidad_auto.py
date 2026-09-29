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

Bypass: BTP_CONTINUIDAD_AUTO_OFF=1. Ganchos de test: BTP_CLAUDE_BIN, BTP_STATE_DIR,
BTP_CONTINUIDAD_TOKEN (evita el Llavero).
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import continuity  # noqa: E402

MIN_MENSAJES_TITULAR = 2      # menos que esto no es una sesión, es un vistazo
MIN_CARACTERES = 400
MAX_EXTRACTO = 40000         # cola de la conversación que ve Haiku
MAX_RESUMEN = 1200

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
    d = _leer_offsets()
    d[session_id] = offset
    os.makedirs(continuity.CONT, mode=0o700, exist_ok=True)
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


def main(argv):
    if os.environ.get("BTP_CONTINUIDAD_AUTO_OFF") or os.environ.get("BTP_CONTINUIDAD_AUTO_HIJO"):
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
