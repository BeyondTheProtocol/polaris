#!/usr/bin/env python3
"""tools/borde_gateway.py — F3: el GATEWAY del borde (plan playful-crafting-pancake / typed-swinging-wand).

Un shim HTTP **OpenAI-compatible** que enruta TODO por la centralita `ia.ask` (→ el borde). Sirve para
ENJAULAR a un orquestador agéntico abierto (OpenCode/Goose): se le apunta su `base_url` aquí y NO se le
dan claves de nube → físicamente no puede llegar a ningún modelo salvo a través del borde, que clasifica,
bloquea lo sensible hacia destinos no confiables y sella cada llamada. Convierte F3 de "instalar un agente
que podría fugar" en "instalar un agente enjaulado".

Invariantes (el muro NO se reimplementa aquí; vive en ia.ask/borde):
  · Bind SOLO loopback (127.0.0.1). Nada hacia fuera. Otra máquina = relay Tailscale aparte (no aquí).
  · Token local obligatorio (Authorization: Bearer …) — solo el orquestador autorizado lo usa.
  · Cada petición pasa por `ia.ask`: sensible→destino no confiable se NIEGA (devolvemos un rechazo, NUNCA
    el dato); aplazo (sin saldo/cerebro) → 503 (el cliente reintenta), sin inventar respuesta.
  · Log de metadatos (sello sha256 del prompt, cerebro, resultado) — NUNCA el contenido. El egreso real
    queda además sellado en el ledger hash-chained del borde.
  · NO enciende nada: se corre a mano para el piloto; el daemon launchd = gate de {{TITULAR}}.

Modo "solo-no-sensible" (--solo-no-sensible / BORDE_GW_NO_SENSIBLE=1, default OFF):
  Endurece el muro para un orquestador abierto enjaulado. Cuando está ON, ANTES de enrutar nada
  clasificamos el prompt con `borde.clasificar()`; si sale SENSIBLE → 403 muro_denied de inmediato,
  SEA CUAL SEA el destino (incluido Claude/local de confianza), sin filtrar el contenido y sellando
  un `deny` (solo metadatos) en el ledger. La jaula no recibe NUNCA clínico/sensible, ni vía Claude.
  OFF = comportamiento idéntico al de siempre (retrocompatible).

CLI:
  python3 tools/borde_gateway.py serve [--port N] [--solo-no-sensible]   # levanta el gateway (127.0.0.1)
  python3 tools/borde_gateway.py --health              # cerebros vivos (reusa ia.health)
  python3 tools/borde_gateway.py --token               # imprime la ruta del token local
"""
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import unicodedata
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ia       # noqa: E402 — la centralita (y, dentro, el borde)
import borde    # noqa: E402 — clasificador del muro (solo LECTURA: clasificar/_sellar)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
GW_DIR = os.path.join(STATE, "borde_gateway")
HOST = os.environ.get("BTP_GATEWAY_HOST", "127.0.0.1")
PORT = int(os.environ.get("BTP_GATEWAY_PORT", "8799"))
_MAX_BODY = 256 * 1024
# Auto-reporte INMEDIATO: si la puerta (Vivir) no logra contestar a {{TITULAR}} (503), se lo decimos en
# el instante del fallo —no esperamos al healthcheck— para que un error como el «tope gastado» se
# vea y se arregle YA. Anti-spam por cooldown (Open WebUI reintenta) y respeta el HALT.
_AVISO_PUERTA_COOLDOWN_S = int(os.environ.get("BTP_GATEWAY_AVISO_COOLDOWN_S", "1800"))   # 30 min
MODOS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "personas", "modos.json")


def cargar_modos():
    """Modos seleccionables (el desplegable de Open WebUI = botón de modo, estilo Los Sims). [] si falta
    el fichero. El MODO lo elige {{TITULAR}} (el tema); el CEREBRO real lo sigue eligiendo el borde (el poder)."""
    try:
        data = json.load(open(MODOS_FILE, encoding="utf-8")) or {}
        return [m for m in data.get("modos", []) if isinstance(m, dict) and m.get("name")]
    except Exception:
        return []


def _modo_default(modos=None):
    """El modo por defecto (Vivir): el marcado default, o el primero. None si no hay modos."""
    modos = modos if modos is not None else cargar_modos()
    if not modos:
        return None
    for m in modos:
        if m.get("default"):
            return m
    return modos[0]


def _override_manual(model_id):
    """¿El cliente eligió un modo a mano? Solo cuenta como override EXPLÍCITO si `model` nombra un
    modo NO-default (Misión/Cuidar/Construir). El default (Vivir) o un id genérico/ausente = "sin
    elección" → toca auto-detectar. Devuelve el modo elegido o None."""
    modos = cargar_modos()
    if not modos or not model_id:
        return None
    for m in modos:
        if m.get("name") == model_id and not m.get("default"):
            return m
    return None


def _normaliza(s):
    """minúsculas + sin tildes — para que el matcheo de palabras clave sea robusto (anti-homoglifo
    básico: NFKD y descartar diacríticos). Las claves de `deteccion` ya van des-tildadas o parciales."""
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _detectar_modo(texto):
    """AUTO-DETECCIÓN barata y DETERMINISTA del modo por la intención del mensaje (sin LLM, sin coste).
    Puntúa cada modo por cuántas de sus palabras clave (`deteccion` en modos.json) aparecen en el texto;
    gana el de mayor señal. Empate o señal cero → el default (Vivir) = comportamiento actual. Devuelve
    (modo, auto) donde auto=True si la elección vino de aquí (para anunciarla). NUNCA toca el muro: solo
    elige qué persona anteponer; ia.ask/borde siguen clasificando y bloqueando igual."""
    modos = cargar_modos()
    if not modos:
        return None, False
    t = _normaliza(texto)
    mejor, mejor_pts = None, 0
    for m in modos:
        claves = m.get("deteccion") or []
        pts = sum(1 for k in claves if _normaliza(k) in t)
        if pts > mejor_pts:
            mejor, mejor_pts = m, pts
    if mejor_pts <= 0 or mejor is None:
        return _modo_default(modos), False
    deflt = _modo_default(modos)
    return mejor, (mejor.get("name") != (deflt or {}).get("name"))


def _resolver_modo(model_id, texto=None):
    """Elige el modo para esta petición. PRIORIDAD: (1) override manual explícito de {{TITULAR}} (el
    desplegable nombra Misión/Cuidar/Construir) SIEMPRE gana; (2) si no, AUTO-DETECCIÓN por la
    intención del `texto` (heurística barata); (3) si no hay texto/modos, el default (Vivir). Devuelve
    (modo, auto) — auto=True solo cuando la elección la hizo la auto-detección (para anunciarla)."""
    modos = cargar_modos()
    if not modos:
        return None, False
    manual = _override_manual(model_id)
    if manual is not None:
        return manual, False                      # el override manual manda: ni se auto-detecta
    if texto:
        return _detectar_modo(texto)
    return _modo_default(modos), False


def _persona_texto(modo):
    """Lee el .md de persona del modo. '' si no hay o no se puede leer (nunca rompe el chat)."""
    if not modo or not modo.get("persona"):
        return ""
    try:
        return open(os.path.join(os.path.dirname(MODOS_FILE), modo["persona"]), encoding="utf-8").read().strip()
    except Exception:
        return ""


def _solo_no_sensible():
    """Modo endurecido (default OFF). ON por env BORDE_GW_NO_SENSIBLE=1 o por --solo-no-sensible
    (que setea la env antes de servir). Se lee en cada petición → testeable sin reiniciar."""
    return os.environ.get("BORDE_GW_NO_SENSIBLE", "").strip() in ("1", "true", "yes", "on")


def _host_es_privado(h=None):
    """Guard del muro: el gateway SOLO se levanta en loopback."""
    return (h or HOST) in ("127.0.0.1", "::1", "localhost")


# ── Token local ──────────────────────────────────────────────────────────────────────────
def _token_path():
    return os.path.join(GW_DIR, "token")


def cargar_token():
    """Token del gateway; lo crea (0600) la primera vez. Lo lee el orquestador para autenticarse."""
    p = _token_path()
    try:
        t = open(p, encoding="utf-8").read().strip()
        if t:
            return t
    except Exception:
        pass
    os.makedirs(GW_DIR, exist_ok=True)
    t = secrets.token_urlsafe(24)
    with open(p, "w", encoding="utf-8") as f:
        f.write(t)
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass
    return t


# ── Traducción OpenAI ↔ ia.ask ─────────────────────────────────────────────────────────────
def _texto(content):
    """content de un message: str, o lista de partes {type,text} (formato vision). Coerce a str."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content
                         if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


def _mensajes_a_prompt(messages):
    """messages OpenAI → (prompt, system). Los system se concatenan; el resto se renderiza como
    transcripción (rol: texto) si hay varios turnos, o el texto pelado si es uno solo."""
    system = "\n".join(_texto(m.get("content")) for m in messages
                       if isinstance(m, dict) and m.get("role") == "system")
    turnos = [m for m in messages if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    if len(turnos) == 1:
        prompt = _texto(turnos[0].get("content"))
    else:
        prompt = "\n".join("%s: %s" % (m["role"], _texto(m.get("content"))) for m in turnos)
    return prompt.strip(), (system.strip() or None)


def _ok_openai(text, brain):
    return {"id": "chatcmpl-borde", "object": "chat.completion", "created": int(time.time()),
            "model": brain or "borde", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


def _err_openai(msg, typ):
    return {"error": {"message": msg, "type": typ, "code": typ}}


def _log_gateway(prompt, r, status):
    """Metadatos SOLO (sello del prompt, cerebro, resultado) — nunca el contenido."""
    try:
        os.makedirs(GW_DIR, exist_ok=True)
        rec = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "sello": hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()[:16],
               "brain": r.get("brain"), "status": status, "motivo": r.get("motivo"),
               "degradado": bool(r.get("degradado"))}
        with open(os.path.join(GW_DIR, "log-%s.jsonl" % datetime.now().strftime("%Y-%m-%d")),
                  "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _avisar_puerta_caida(motivo):
    """Auto-reporte INMEDIATO cuando la puerta (Vivir) no pudo contestar a {{TITULAR}} (503). Detecta en
    el instante del fallo, sin esperar al healthcheck. El relevo de cortesía ya evita el caso común,
    así que un 503 aquí = algo más hondo (p.ej. el cerebro local también caído): merece que ella lo
    sepa YA. Anti-spam por cooldown (Open WebUI reintenta) + respeta el HALT. Best-effort: NUNCA rompe
    la respuesta ni lanza. La salida real es opt-in/monkeypatcheable (los tests fijan cooldown/stub)."""
    try:
        import salida
        if salida.halted():
            return
        st = os.path.join(GW_DIR, "aviso_puerta.json")
        ahora = time.time()
        try:
            prev = json.load(open(st, encoding="utf-8"))
        except Exception:
            prev = {}
        if ahora - float(prev.get("ts", 0)) < _AVISO_PUERTA_COOLDOWN_S:
            return                                    # ya avisado hace poco → no spamear
        if "tope" in (motivo or "").lower():
            txt = ("⚠️ La puerta de Polaris (Vivir) no pudo contestarte: topó el presupuesto del día "
                   "y el cerebro local tampoco respondió. Dime «sube» y te contesta Claude — lo estoy mirando.")
        else:
            txt = ("⚠️ La puerta de Polaris (Vivir) no pudo contestarte ahora mismo (%s). Lo estoy "
                   "mirando para que no te vuelva a pasar." % (motivo or "sin cerebro disponible"))
        salida.report_to_titular(txt, voz="sobria")    # respeta silencio nocturno (no es código rojo)
        os.makedirs(GW_DIR, exist_ok=True)
        tmp = st + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": ahora, "motivo": (motivo or "")[:200]}, f)
        os.replace(tmp, st)
    except Exception:
        pass


def _sellar_deny_no_sensible(prompt, motivo):
    """Modo solo-no-sensible: deja rastro del rechazo SIN contenido. En el log de metadatos del
    gateway y, best-effort, en el ledger hash-chained del borde (solo metadatos: sello + motivo)."""
    _log_gateway(prompt, {"brain": None, "motivo": motivo}, 403)
    try:
        borde._sellar({"evento": "egress", "permitido": False, "destino": "gateway:solo-no-sensible",
                       "sens": True, "motivo": motivo,
                       "sello": hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()[:16]})
    except Exception:
        pass


def responder_chat(payload):
    """(status, body_dict) para /v1/chat/completions. TODO pasa por ia.ask (→ borde). Núcleo
    testeable sin socket."""
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list) \
            or not payload["messages"]:
        return 400, _err_openai("falta 'messages'", "invalid_request_error")
    prompt, system = _mensajes_a_prompt(payload["messages"])
    if not prompt:
        return 400, _err_openai("prompt vacío", "invalid_request_error")
    # MODO (el desplegable de Open WebUI = botón Vivir/Misión/Cuidar/Construir). Si {{TITULAR}} NO eligió a
    # mano un modo no-default, se AUTO-DETECTA por la intención del mensaje (heurística barata/determinista,
    # sin LLM ni coste). El override manual SIEMPRE gana. El modo SOLO antepone una persona y pasa una pista
    # de routing (nivel/prefer); NO toca el muro. Sin señal/sin modos → Vivir (comportamiento actual).
    modo, modo_auto = _resolver_modo(payload.get("model"), texto=prompt)
    nivel = (modo or {}).get("nivel")
    prefer = (modo or {}).get("prefer")
    # Modo endurecido: si está ON y el prompt (system del USUARIO incluido) es sensible → 403 ANTES de
    # enrutar, sea cual sea el destino. Se clasifica el contenido del USUARIO, NO nuestra persona interna.
    if _solo_no_sensible():
        es_sens, mot = borde.clasificar("\n".join(x for x in (system, prompt) if x))
        if es_sens:
            _sellar_deny_no_sensible(prompt, "solo-no-sensible: %s" % mot)
            return 403, _err_openai(
                "borde: modo solo-no-sensible — petición clasificada sensible, rechazada (%s)" % mot,
                "muro_denied")
    # Anteponer la persona del modo SOLO para la llamada al cerebro (ia.ask clasifica el prompt, no el
    # system, así que la persona no altera la decisión del muro).
    persona = _persona_texto(modo)
    system_ef = (persona + "\n\n---\n" + system) if (persona and system) else (persona or system)
    # interactivo=True: es {{TITULAR}} EN VIVO en la puerta. Le reserva la última franja de gasto del día
    # (cost_guard) para que el loop de fondo 24/7 nunca la deje sin Claude estando ella delante.
    r = ia.ask(prompt, system=system_ef, nivel=nivel, prefer=prefer,
               interactivo=True)   # ← el borde clasifica/bloquea/sella aquí dentro
    if r.get("text") is not None:
        status = 200
    elif r.get("deferred"):
        status = 503                              # sin cerebro/saldo → reintenta, sin inventar
    else:
        status = 403                             # rechazo del muro → nunca devolvemos el dato
    _log_gateway(prompt, r, status)
    if status == 200:
        texto = r["text"]
        # Transparencia honesta (1 línea): si respondí DEGRADADA en un cerebro local/gratis —porque el
        # de pago topó el presupuesto del día—, que {{TITULAR}} lo sepa y pueda subirlo si quiere Claude. Solo
        # cuando de verdad cayó a un cerebro libre (no en relevos entre cerebros de pago).
        brain = r.get("brain") or ""
        if r.get("degradado") and brain != "claude":
            try:
                libre = any(c.get("name") == brain and (c.get("free") or c.get("kind") == "openai_local")
                            for c in ia.cargar_registro())
            except Exception:
                libre = False
            if libre:
                texto = ("⚪ _Te respondo en un cerebro local: el de pago topó el presupuesto de hoy. "
                         "Dime «sube» por Telegram si quieres que conteste Claude._\n\n" + texto)
        # Anunciar el cambio de modo en 1 línea (solo si lo eligió la auto-detección, no {{TITULAR}}): que
        # sepa en qué modo le respondo, sin preguntar. El default (Vivir) no se anuncia (es lo de siempre).
        if modo_auto and modo:
            etq = modo.get("label", modo.get("name", ""))
            texto = "→ modo %s\n\n%s" % (etq, texto)
        return 200, _ok_openai(texto, r.get("brain"))
    if status == 503:
        # La puerta no pudo contestar a {{TITULAR}} → auto-reporte INMEDIATO (anti-spam, respeta HALT).
        _avisar_puerta_caida(r.get("motivo"))
        return 503, _err_openai("sin cerebro disponible ahora (aplazado): %s" % r.get("motivo"),
                                "service_unavailable")
    return 403, _err_openai("borde: rechazado por el muro (%s)" % r.get("motivo"), "muro_denied")


def modelos():
    """/v1/models = los MODOS seleccionables (Vivir/Construir…) si hay modos.json; si no, fallback al
    registro de cerebros (retrocompatible). Así el desplegable muestra modos, no backends crudos."""
    modos = cargar_modos()
    if modos:
        return {"object": "list",
                "data": [{"id": m["name"], "object": "model", "owned_by": "polaris",
                          "label": m.get("label", m["name"])} for m in modos]}
    data = [{"id": c.get("name"), "object": "model", "owned_by": "borde-polaris",
             "trusted": bool(c.get("trusted"))}
            for c in ia.cargar_registro() if c.get("enabled")]
    return {"object": "list", "data": data}


# ── Servidor HTTP (loopback) ────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):                   # silencio (como observatorio/staging)
        return

    def _send(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, ok_body):
        """Re-emite una respuesta OK (200) como stream SSE OpenAI-compatible. NO abre ninguna vía
        nueva: el contenido YA pasó por `ia.ask`/borde en responder_chat; esto solo cambia el
        envoltorio para los clientes que piden `stream:true` (OpenCode/Goose). ia.ask devuelve el
        texto entero (no hay streaming real de tokens) → un único delta + cierre, válido OpenAI."""
        text = ok_body["choices"][0]["message"]["content"]
        base = {"id": ok_body.get("id", "chatcmpl-borde"), "object": "chat.completion.chunk",
                "created": ok_body.get("created", int(time.time())),
                "model": ok_body.get("model", "borde")}
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def chunk(delta, finish=None):
            d = dict(base, choices=[{"index": 0, "delta": delta, "finish_reason": finish}])
            self.wfile.write(("data: %s\n\n" % json.dumps(d, ensure_ascii=False)).encode("utf-8"))

        chunk({"role": "assistant"})
        chunk({"content": text})
        chunk({}, finish="stop")
        self.wfile.write(b"data: [DONE]\n\n")

    def _autorizado(self):
        tok = cargar_token()
        hdr = self.headers.get("Authorization", "")
        got = hdr[7:].strip() if hdr.startswith("Bearer ") else ""
        return bool(got) and hmac.compare_digest(got, tok)

    def do_GET(self):
        if self.path.rstrip("/") != "/v1/models":
            return self._send(404, _err_openai("ruta desconocida", "not_found"))
        if not self._autorizado():
            return self._send(401, _err_openai("token inválido o ausente", "unauthorized"))
        self._send(200, modelos())

    def do_POST(self):
        if self.path.rstrip("/") != "/v1/chat/completions":
            return self._send(404, _err_openai("ruta desconocida", "not_found"))
        if not self._autorizado():
            return self._send(401, _err_openai("token inválido o ausente", "unauthorized"))
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > _MAX_BODY:
            return self._send(400, _err_openai("body vacío o demasiado grande", "invalid_request_error"))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._send(400, _err_openai("JSON inválido", "invalid_request_error"))
        status, body = responder_chat(payload)
        # stream:true (OpenCode/Goose) → SSE solo si el muro dejó pasar (200). Errores/rechazos
        # (401/403/503/4xx) van como JSON: el cliente OpenAI los entiende antes de abrir el stream.
        if status == 200 and isinstance(payload, dict) and payload.get("stream"):
            return self._send_sse(body)
        self._send(status, body)


def serve(host=None, port=None):
    host = host or HOST
    port = port or PORT
    if not _host_es_privado(host):
        sys.stderr.write("borde_gateway: me niego a bindear fuera de loopback (%s). El muro manda.\n" % host)
        return 2
    cargar_token()                               # asegúrate de que existe antes de aceptar peticiones
    srv = ThreadingHTTPServer((host, port), Handler)
    modo = "  [MODO solo-no-sensible: sensible→403 sea cual sea el destino]" if _solo_no_sensible() else ""
    print("Gateway del borde en http://%s:%d  (token: %s)%s" % (host, port, _token_path(), modo),
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__); return 0
    if argv and argv[0] == "--health":
        for c in ia.health():
            print("%s %-14s %s%s" % ("✅" if c["disponible"] else "⚪", c["name"],
                                     "[confianza] " if c["trusted"] else "",
                                     "(habilitado)" if c["enabled"] else "(off)"))
        return 0
    if argv and argv[0] == "--token":
        print(_token_path()); cargar_token(); return 0
    if "--solo-no-sensible" in argv:
        os.environ["BORDE_GW_NO_SENSIBLE"] = "1"   # equivale a la env; lo lee cada petición
    port = PORT
    if "--port" in argv:
        port = int(argv[argv.index("--port") + 1])
    return serve(port=port)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
