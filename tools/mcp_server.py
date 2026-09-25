#!/usr/bin/env python3
"""tools/mcp_server.py — Servidor MCP propio de Polaris (Fase 2 de la migración {{CONTACTO}}=cara).

Expone SOLO las herramientas NO-CLÍNICAS de Polaris a {{CONTACTO}} (u otro cliente MCP) vía transporte
stdio estándar. El cliente JAMÁS ve nada clínico/sensible: la allowlist es CERRADA y fail-closed.

Guardarraíles (el muro manda SIEMPRE):
  · Allowlist CERRADA (3 tools): kb_buscar, memoria_recall, comite.
  · Comités CLÍNICOS prohibidos (lista dura, fail-closed): si el cliente pide uno → REJECT.
  · TODO input de cliente = no confiable (anti-inyección): se normaliza y valida antes de pasar.
  · No pasa por borde.egress_check (es punto de ENTRADA, no de salida), pero llama a borde.clasificar
    sobre los RESULTADOS antes de devolverlos, para nunca sacar sensible por aquí.
  · Respeta el HALT del muro: si .btp.HALT o .HALT existen → RECHAZAR (safe-halt).
  · Log de metadatos (sello del input, tool, estado) — nunca el contenido en claro.

Transporte: stdio (estándar MCP). El proceso corre como hijo del cliente.
SDK: mcp (≥1.28) — vía .venv-consensus (la que ya tiene el SDK instalado).

NO enciende nada. NO crea plist. NO modifica config de {{CONTACTO}}.

Para conectar {{CONTACTO}} (línea que {{TITULAR}} aprobaría en su config — NO aplicar aquí):
    mcps:
      - name: polaris
        command: /Users/titular/claudecode/.venv-consensus/bin/python3
        args: [/Users/titular/claudecode/tools/mcp_server.py]
        env: {}

Cómo probar sin {{CONTACTO}} (stdio):
    echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | \\
        /Users/titular/claudecode/.venv-consensus/bin/python3 tools/mcp_server.py

Tests automáticos: tests/test_mcp_server.py
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# ── Rutas (idéntico a borde_gateway.py — una sola convención en todo el sistema) ──────────
REPO = Path(os.environ.get("BTP_REPO") or Path(__file__).resolve().parents[1])
TOOLS = Path(__file__).resolve().parent
STATE = Path(os.environ.get("BTP_STATE_DIR") or (REPO / "tools" / "state"))
MCP_DIR = STATE / "mcp_server"
HALT_FILES = (
    tuple(os.environ["BTP_HALT_FILES"].split(":"))
    if os.environ.get("BTP_HALT_FILES")
    else (str(Path.home() / ".btp.HALT"), str(REPO / ".HALT"))
)

# ── Allowlist CERRADA (fail-closed). TODO lo que no esté aquí → REJECT inmediato ──────────
TOOLS_PERMITIDAS: frozenset[str] = frozenset({"kb_buscar", "memoria_recall", "comite"})

# Comités NO-CLÍNICOS permitidos por nombre de agente.
# FAIL-CLOSED: si no está en esta lista, se rechaza — aunque "parezca" no-clínico.
# Para añadir un comité: revisión del muro + OK de {{TITULAR}} + commit a main (consumer-first).
COMITES_PERMITIDOS: frozenset[str] = frozenset({
    "orquestador",
    "diseno",
    "prensa",
    "redes-contenido",
    "monitor-lanzamiento",
    "periodista",
    "investigador",
    "comunidad",
    "finanzas-transparencia",
    "escritor-memorias",
    "consejero-marketing",
    "consejero-acceso",
    "consejero-arquitectura",
    "consejero-arneses",
    "auto-mejora",
    "coach-colaboracion",
    "asistente",
    "x-inbox",
    "dm-inbox",
    "agencia-viajes",
    "legal-burocracia",
})

# Comités CLÍNICOS prohibidos (lista dura + explícita para que nunca se cuelen por derivación).
# No es la lista exhaustiva de "todo lo clínico del mundo": es la guarda contra los que SÍ
# existen en este sistema y un cliente podría intentar invocar.
COMITES_CLINICOS_PROHIBIDOS: frozenset[str] = frozenset({
    "comite-medico",
    "oncologo-virtual",
    "herramientas-medicas",
    "verificacion",
    "cascada-clinica",
    "pipeline-vacuna",
    "centinela-ned",
})

# Longitud máxima de un argumento de entrada (anti-inyección / anti-flood)
_MAX_ARG_LEN = 2000
_MAX_NOMBRE_COMITE = 80
_MAX_INTENCION = 1000


# ── Utilidades de muro ─────────────────────────────────────────────────────────────────────

def _halted() -> bool:
    """Respeta el kill-switch del muro (igual que borde.py)."""
    return any(os.path.exists(h) for h in HALT_FILES)


def _normalizar(s: str) -> str:
    """NFKD + sin diacríticos + lower — cierra evasión por homoglifo (igual que borde.py)."""
    n = unicodedata.normalize("NFKD", s)
    return "".join(c for c in n if not unicodedata.combining(c)).lower()


def _limpiar_input(s: str, maxlen: int = _MAX_ARG_LEN) -> str:
    """Limpia y acota un argumento de entrada del cliente (anti-inyección básica).
    El contenido de un cliente MCP es no confiable: se trata como dato, nunca como instrucción."""
    if not isinstance(s, str):
        raise ValueError("argumento debe ser texto")
    # quita zero-width y caracteres de control (salvo saltos de línea y tabulaciones)
    cleaned = "".join(
        c for c in s
        if unicodedata.category(c) not in ("Cf", "Cc") or c in "\n\r\t"
    )
    if len(cleaned) > maxlen:
        raise ValueError(f"argumento demasiado largo (máx. {maxlen} caracteres)")
    return cleaned.strip()


def _log(tool: str, estado: str, sello: str) -> None:
    """Metadatos SOLO (sello sha256 del input, tool, estado) — nunca el contenido."""
    try:
        MCP_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tool": tool,
            "estado": estado,
            "sello": sello,
        }
        log_path = MCP_DIR / ("log-%s.jsonl" % datetime.now().strftime("%Y-%m-%d"))
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _sello(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:16]


def _resultado_sensible(texto: str) -> bool:
    """Comprueba que el resultado a devolver NO contiene contenido sensible.
    Reutiliza borde.clasificar (la única fuente de verdad del muro)."""
    try:
        sys.path.insert(0, str(TOOLS))
        import borde  # type: ignore
        es_sens, _ = borde.clasificar(texto)
        return es_sens
    except Exception:
        # fail-closed: si no podemos comprobar, no devolvemos
        return True


# ── Implementación de las 3 tools ─────────────────────────────────────────────────────────

def _tool_kb_buscar(query: str) -> str:
    """Búsqueda RAG sobre la fuente de verdad pública (no-sensible).
    Usa kb.py con scope='internal' (nunca private/clínico)."""
    query = _limpiar_input(query)
    if not query:
        return "Error: query vacía."
    # kb.py ask imprime a stdout. Capturamos la salida.
    python = str(TOOLS.parent / ".venv-consensus" / "bin" / "python3")
    if not os.path.exists(python):
        python = sys.executable
    kb_script = str(TOOLS / "kb.py")
    result = subprocess.run(
        [python, kb_script, "ask", query, "--scope", "internal"],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "BTP_REPO": str(REPO)},
    )
    output = result.stdout.strip() or result.stderr.strip() or "(sin resultados)"
    # Guardarraíl de salida: si el resultado contiene algo sensible, no lo devolvemos
    if _resultado_sensible(output):
        return "El resultado contenía información sensible y fue retenido por el muro."
    return output


def _tool_memoria_recall(query: str) -> str:
    """Recall BM25 sobre memorias NO sensibles. Usa memoria_radar.py con scope limitado."""
    query = _limpiar_input(query)
    if not query:
        return "Error: query vacía."
    python = str(TOOLS.parent / ".venv-consensus" / "bin" / "python3")
    if not os.path.exists(python):
        python = sys.executable
    script = str(TOOLS / "memoria_radar.py")
    # memoria_radar no tiene un 'ask' directo; usamos 'resucitar' con el foco como query.
    # Pasamos la query como variable de entorno para que la use como foco de resucitar.
    result = subprocess.run(
        [python, script, "resucitar", "--json"],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ,
             "BTP_REPO": str(REPO),
             "BTP_MEMORIA_FOCO": query},  # foco override para el recall
    )
    raw = result.stdout.strip() or result.stderr.strip()
    if not raw:
        return "(sin resultados de memoria)"
    # intentamos parsear JSON si es JSON
    try:
        data = json.loads(raw)
        # Devolvemos solo los campos no sensibles de cada resultado
        items = []
        for item in (data if isinstance(data, list) else [data]):
            nombre = item.get("fichero", item.get("file", ""))
            relevancia = item.get("score", item.get("relevancia", ""))
            resumen = item.get("resumen", item.get("snippet", ""))[:300]
            if nombre and not _resultado_sensible(str(resumen)):
                items.append(f"- {nombre} (rel. {relevancia}): {resumen}")
        return "\n".join(items) if items else "(sin memorias relevantes)"
    except Exception:
        # si no es JSON, devuelve texto plano saneado
        if _resultado_sensible(raw):
            return "El resultado contenía información sensible y fue retenido."
        return raw[:2000]


def _tool_comite(nombre: str, intencion: str) -> str:
    """Lanza un comité NO-CLÍNICO y devuelve su respuesta.
    FAIL-CLOSED: solo los comités de COMITES_PERMITIDOS; cualquier comité clínico → REJECT."""
    nombre = _limpiar_input(nombre, maxlen=_MAX_NOMBRE_COMITE)
    intencion = _limpiar_input(intencion, maxlen=_MAX_INTENCION)

    # Normalizar para anti-bypass por mayúsculas/espacios/homoglifos
    nombre_norm = _normalizar(nombre).strip()

    # Primero: ¿es un comité clínico explícitamente prohibido?
    if nombre_norm in {_normalizar(c) for c in COMITES_CLINICOS_PROHIBIDOS}:
        return (
            f"RECHAZADO por el muro: '{nombre}' es un comité clínico. "
            "Este servidor MCP solo expone herramientas NO-CLÍNICAS (Fase 2). "
            "Los comités clínicos no se exponen a clientes externos."
        )

    # Segundo: ¿está en la allowlist de permitidos?
    if nombre_norm not in {_normalizar(c) for c in COMITES_PERMITIDOS}:
        return (
            f"RECHAZADO (fail-closed): '{nombre}' no está en la allowlist de comités "
            "permitidos por este servidor. Solo se permiten comités no-clínicos registrados. "
            "Para añadir un comité: revisión del muro + OK de {{TITULAR}}."
        )

    if not intencion:
        return "Error: 'intencion' no puede estar vacía."

    # El comité existe y es no-clínico. Lo invocamos vía subprocess (el agente CLI).
    # Nota: aquí no hay un agente CLI directo para cada comité; lo dejamos como stub
    # que devuelve un placeholder. La integración real con run_agent.sh / el dispatcher
    # es Fase 3 (post-OK de {{TITULAR}}). Por ahora, devuelve la intención + nombre para
    # que el cliente sepa que el gate pasó y el comité fue identificado correctamente.
    return (
        f"[Comité '{nombre}' identificado y autorizado]\n"
        f"Intención recibida: {intencion[:200]}\n\n"
        "Nota: la invocación real del agente requiere integración con el dispatcher "
        "(Fase 3, pendiente de OK de {{TITULAR}}). El gate de allowlist pasó correctamente."
    )


# ── Definiciones de tools MCP (esquema JSON para el cliente) ──────────────────────────────

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "kb_buscar",
        "description": (
            "Búsqueda BM25 sobre la fuente de verdad pública/interna de Polaris "
            "(documentos de proyecto, estrategia, registro de comités, etc.). "
            "NO incluye información clínica, genómica, PII ni material de _PRIVADO_*. "
            "Devuelve los pasajes más relevantes con cita de fichero y sección."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Pregunta o términos de búsqueda (máx. 2000 caracteres).",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "memoria_recall",
        "description": (
            "Recall BM25 sobre las memorias del sistema (lecciones aprendidas, feedback, "
            "referencias de proyecto). Solo memorias no sensibles. "
            "Útil para recuperar contexto de trabajo, preferencias, decisiones pasadas."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Foco de búsqueda en las memorias (máx. 2000 caracteres).",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "comite",
        "description": (
            "Invoca un comité NO-CLÍNICO del gabinete (orquestador, diseno, prensa, etc.) "
            "con una intención. FAIL-CLOSED: solo la allowlist de comités no-clínicos registrados. "
            "Los comités clínicos (comite-medico, oncologo-virtual, herramientas-medicas, "
            "verificacion) son PROHIBIDOS en este servidor."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "nombre": {
                    "type": "string",
                    "description": "Nombre del agente/comité (p. ej. 'orquestador', 'diseno', 'prensa').",
                },
                "intencion": {
                    "type": "string",
                    "description": "Intención o tarea para el comité (máx. 1000 caracteres).",
                },
            },
            "required": ["nombre", "intencion"],
        },
    },
]


# ── Servidor MCP stdio (protocolo JSON-RPC 2.0) ───────────────────────────────────────────
# Implementamos el protocolo MCP mínimo directamente (sin FastMCP) para evitar que el
# framework añada capas que no controlamos. El protocolo MCP sobre stdio es JSON-RPC 2.0:
# una línea por mensaje, stdin→stdout, stderr para logs del servidor.

class McpServer:
    """Servidor MCP stdio mínimo. Solo implementa los métodos que necesitamos."""

    def handle(self, msg: dict) -> dict | None:
        method = msg.get("method", "")
        params = msg.get("params") or {}
        req_id = msg.get("id")

        if method == "initialize":
            return self._ok(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "polaris-mcp", "version": "0.2.0"},
            })

        if method == "initialized":
            # notificación (sin id) — no hay que responder
            return None

        if method == "tools/list":
            return self._ok(req_id, {"tools": TOOL_SCHEMAS})

        if method == "tools/call":
            return self._handle_call(req_id, params)

        if method == "ping":
            return self._ok(req_id, {})

        # Método desconocido → error MCP estándar
        return self._err(req_id, -32601, "Método no implementado: %s" % method)

    def _handle_call(self, req_id, params: dict) -> dict:
        tool_name = params.get("name", "")
        args = params.get("arguments") or {}
        sello = _sello(str(tool_name) + str(args))

        # Guardarraíl 1: HALT activo
        if _halted():
            _log(tool_name, "halt", sello)
            return self._tool_err(req_id, "El sistema está en HALT. No se procesan peticiones.")

        # Guardarraíl 2: tool no en allowlist (fail-closed)
        if tool_name not in TOOLS_PERMITIDAS:
            _log(tool_name, "reject-allowlist", sello)
            return self._tool_err(
                req_id,
                f"Tool '{tool_name}' no está en la allowlist de este servidor. "
                "Solo se permiten: " + ", ".join(sorted(TOOLS_PERMITIDAS))
            )

        # Ejecutar la tool
        try:
            if tool_name == "kb_buscar":
                result = _tool_kb_buscar(args.get("query", ""))
            elif tool_name == "memoria_recall":
                result = _tool_memoria_recall(args.get("query", ""))
            elif tool_name == "comite":
                result = _tool_comite(
                    args.get("nombre", ""),
                    args.get("intencion", ""),
                )
            else:
                # No debería llegar aquí (ya filtrado arriba), pero fail-closed
                _log(tool_name, "reject-internal", sello)
                return self._tool_err(req_id, "Tool no implementada (error interno)")

            _log(tool_name, "ok", sello)
            return self._ok(req_id, {
                "content": [{"type": "text", "text": result}]
            })

        except ValueError as e:
            _log(tool_name, "reject-input", sello)
            return self._tool_err(req_id, f"Input inválido: {e}")
        except subprocess.TimeoutExpired:
            _log(tool_name, "timeout", sello)
            return self._tool_err(req_id, "Tiempo de espera agotado.")
        except Exception as e:
            _log(tool_name, "error", sello)
            return self._tool_err(req_id, f"Error interno: {type(e).__name__}")

    @staticmethod
    def _ok(req_id, result) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _err(req_id, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": code, "message": message}}

    @staticmethod
    def _tool_err(req_id, message: str) -> dict:
        """Error de tool (se devuelve como contenido de texto, no como error JSON-RPC,
        según la convención MCP para errores de ejecución de herramienta)."""
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "content": [{"type": "text", "text": f"ERROR: {message}"}],
            "isError": True,
        }}


def main() -> None:
    """Bucle stdio: lee una línea JSON por stdin, responde en stdout."""
    server = McpServer()
    sys.stderr.write("[polaris-mcp] servidor MCP listo (stdio)\n")
    sys.stderr.flush()
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError as e:
            resp = {"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": "JSON inválido: %s" % e}}
            print(json.dumps(resp, ensure_ascii=False), flush=True)
            continue
        try:
            resp = server.handle(msg)
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": msg.get("id"),
                    "error": {"code": -32603, "message": "Error interno: %s" % e}}
        if resp is not None:
            print(json.dumps(resp, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
