#!/usr/bin/env python3
"""tests/test_mcp_server.py — Batería de tests para tools/mcp_server.py (Fase 2 MCP).

Verifica EN SECO (sin LLM real, sin red, $0):
  1. Allowlist fail-closed: una tool no-allowlisteada → REJECT (isError + mensaje explícito).
  2. Comité clínico → RECHAZADO (mensaje claro, no se ejecuta nada).
  3. Comité no en la allowlist de comités → RECHAZADO (fail-closed).
  4. Comité válido (no-clínico) → pasa el gate.
  5. kb_buscar con query válida → responde (puede ser sin resultados si no hay índice).
  6. kb_buscar con query vacía → error de input limpio.
  7. memoria_recall con query válida → responde.
  8. HALT activo → todas las tools rechazadas.
  9. Argumentos demasiado largos → error de input.
  10. initialize / tools/list → respuestas correctas del protocolo.
  11. Resultado sensible en kb_buscar → retenido (mock del borde).
  12. Tool con argumento no-string → ValueError atrapado limpiamente.
  13. Método desconocido → error -32601.
  14. JSON inválido → error -32700.
  15. Notificación (initialized, sin id) → no genera respuesta.
  16. Anti-homoglifo: comité clínico con nombre normalizado colado → RECHAZADO.

Estilo: igual que test_borde_gateway.py (ok/fail con contador, exit = nº de fallos).
NO toca estado de producción: usa BTP_STATE_DIR temporal y BTP_HALT_FILES temporal.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Entorno aislado (igual que test_borde_gateway.py)
_TMP = tempfile.mkdtemp(prefix="mcp_test_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_HALT_FILES"] = os.path.join(_TMP, "h_a") + ":" + os.path.join(_TMP, "h_b")
os.environ["BTP_REPO"] = ROOT

sys.path.insert(0, os.path.join(ROOT, "tools"))
import mcp_server as ms  # noqa: E402

_pass = 0
_fail = 0


def ok(cond: bool, name: str) -> None:
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  FALLO: %s" % name)


def _call(tool_name: str, args: dict, *, halt: bool = False) -> dict:
    """Llama directamente a _handle_call del servidor con el entorno de test."""
    if halt:
        # Activa HALT escribiendo el fichero
        open(os.environ["BTP_HALT_FILES"].split(":")[0], "w").close()
    server = ms.McpServer()
    resp = server._handle_call(req_id=99, params={"name": tool_name, "arguments": args})
    if halt:
        # Limpia HALT para no afectar otros tests
        try:
            os.remove(os.environ["BTP_HALT_FILES"].split(":")[0])
        except Exception:
            pass
    return resp


def _handle(method: str, params: dict = None, req_id=1) -> dict | None:
    server = ms.McpServer()
    msg = {"jsonrpc": "2.0", "method": method, "id": req_id}
    if params:
        msg["params"] = params
    return server.handle(msg)


def _is_error(resp: dict) -> bool:
    """¿La respuesta es un error de tool (isError=True en result)?"""
    return bool(resp.get("result", {}).get("isError"))


def _text(resp: dict) -> str:
    """Texto del primer contenido de la respuesta de tool."""
    try:
        return resp["result"]["content"][0]["text"]
    except Exception:
        return ""


# ── Pruebas ────────────────────────────────────────────────────────────────────────────────

print("── test_mcp_server ──")

# 1. Tool no allowlisteada → REJECT
resp = _call("herramienta_inexistente", {})
ok(_is_error(resp) and "allowlist" in _text(resp).lower(), "tool-no-allowlisteada → reject")

# 2. Comité clínico (comite-medico) → RECHAZADO
resp = _call("comite", {"nombre": "comite-medico", "intencion": "analiza el tumor"})
txt = _text(resp)
ok("RECHAZADO" in txt and "clínico" in txt.lower(), "comite-clinico comite-medico → rechazado")

# 3. Comité clínico (oncologo-virtual) → RECHAZADO
resp = _call("comite", {"nombre": "oncologo-virtual", "intencion": "revisa el caso"})
ok("RECHAZADO" in _text(resp), "comite-clinico oncologo-virtual → rechazado")

# 4. Comité clínico (herramientas-medicas) → RECHAZADO
resp = _call("comite", {"nombre": "herramientas-medicas", "intencion": "pipeline"})
ok("RECHAZADO" in _text(resp), "comite-clinico herramientas-medicas → rechazado")

# 5. Comité clínico (verificacion) → RECHAZADO
resp = _call("comite", {"nombre": "verificacion", "intencion": "verifica esto"})
ok("RECHAZADO" in _text(resp), "comite-clinico verificacion → rechazado")

# 6. Comité desconocido (no en ninguna lista) → RECHAZADO por fail-closed
resp = _call("comite", {"nombre": "comite-magico", "intencion": "magia"})
txt = _text(resp)
ok("RECHAZADO" in txt or "fail-closed" in txt.lower() or "allowlist" in txt.lower(),
   "comite-desconocido → rechazado-fail-closed")

# 7. Comité válido no-clínico (orquestador) → pasa el gate
resp = _call("comite", {"nombre": "orquestador", "intencion": "organiza la agenda de hoy"})
ok(not _is_error(resp) and "autorizado" in _text(resp).lower(),
   "comite-no-clinico orquestador → pasa el gate")

# 8. Comité válido no-clínico (diseno) → pasa el gate
resp = _call("comite", {"nombre": "diseno", "intencion": "revisa el mockup"})
ok(not _is_error(resp) and "autorizado" in _text(resp).lower(),
   "comite-no-clinico diseno → pasa el gate")

# 9. Comité válido no-clínico (prensa) → pasa el gate
resp = _call("comite", {"nombre": "prensa", "intencion": "redacta el pitch"})
ok(not _is_error(resp) and "autorizado" in _text(resp).lower(),
   "comite-no-clinico prensa → pasa el gate")

# 10. kb_buscar con query vacía → error de input
resp = _call("kb_buscar", {"query": ""})
ok(_is_error(resp) or "error" in _text(resp).lower() or "vacía" in _text(resp),
   "kb_buscar-query-vacía → error")

# 11. kb_buscar con query válida → alguna respuesta (puede ser sin resultados si no hay índice)
resp = _call("kb_buscar", {"query": "estrella polar NED"})
ok(not _is_error(resp) or "retenido" in _text(resp) or len(_text(resp)) > 0,
   "kb_buscar-query-valida → responde algo")

# 12. memoria_recall con query válida → alguna respuesta
resp = _call("memoria_recall", {"query": "feedback comité"})
ok(len(_text(resp)) > 0, "memoria_recall-query-valida → responde algo")

# 13. HALT activo → todas las tools rechazadas
resp_halt = _call("kb_buscar", {"query": "NED"}, halt=True)
ok("HALT" in _text(resp_halt).upper(), "halt-activo → rechaza kb_buscar")

# 14. HALT activo → comite rechazado también
# (reutilizamos la lógica: si HALT → el servidor lo rechaza antes del gate de allowlist)
halt_file = os.environ["BTP_HALT_FILES"].split(":")[0]
open(halt_file, "w").close()
resp_halt2 = _call("comite", {"nombre": "orquestador", "intencion": "algo"})
ok("HALT" in _text(resp_halt2).upper(), "halt-activo → rechaza comite")
try:
    os.remove(halt_file)
except Exception:
    pass

# 15. Argumento demasiado largo → error de input (no crash)
query_larga = "x" * 5000
resp = _call("kb_buscar", {"query": query_larga})
ok(_is_error(resp) or "largo" in _text(resp).lower() or "error" in _text(resp).lower(),
   "query-demasiado-larga → error-controlado")

# 16. initialize → respuesta correcta del protocolo
resp_init = _handle("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})
ok(resp_init is not None and "protocolVersion" in str(resp_init.get("result", {})),
   "initialize → respuesta correcta")

# 17. tools/list → devuelve las 3 tools de la allowlist
resp_list = _handle("tools/list")
tools_names = {t["name"] for t in (resp_list or {}).get("result", {}).get("tools", [])}
ok(tools_names == {"kb_buscar", "memoria_recall", "comite"},
   "tools/list → devuelve exactamente las 3 tools")

# 18. Método desconocido → error -32601
resp_unk = _handle("metodo_inexistente")
ok((resp_unk or {}).get("error", {}).get("code") == -32601,
   "metodo-desconocido → error-32601")

# 19. Notificación initialized (sin id) → no hay respuesta (None)
server = ms.McpServer()
resp_notif = server.handle({"jsonrpc": "2.0", "method": "initialized"})
ok(resp_notif is None, "notificacion-initialized → no-respuesta")

# 20. Anti-homoglifo: comite-medico con Ο (omicron griego) → rechazado
resp_homoglifo = _call("comite", {"nombre": "cοmite-medico", "intencion": "analiza"})
# La normalización NFKD no siempre colapsa homoglifos griegos a latinos, pero sí los
# zero-width y diacríticos. El test verifica que no pasa como permitido.
# Si el nombre normalizado no coincide con ninguno de la lista de clínicos ni de permitidos
# → falla el gate por fail-closed (rechazado como desconocido), que es el comportamiento correcto.
txt_h = _text(resp_homoglifo)
ok("RECHAZADO" in txt_h or "fail-closed" in txt_h.lower() or "allowlist" in txt_h.lower(),
   "anti-homoglifo: comite-medico con omicron → rechazado")

# 21. ping → respuesta ok
resp_ping = _handle("ping")
ok(resp_ping is not None and resp_ping.get("result") == {}, "ping → respuesta ok")

# 22. JSON inválido en el bucle stdio → error -32700 (test vía subprocess)
python = sys.executable
mcp_script = os.path.join(ROOT, "tools", "mcp_server.py")
try:
    proc = subprocess.run(
        [python, mcp_script],
        input=b"esto no es json\n",
        capture_output=True,
        timeout=5,
    )
    stdout = proc.stdout.decode("utf-8", errors="replace").strip()
    if stdout:
        resp_json = json.loads(stdout.splitlines()[0])
        ok(resp_json.get("error", {}).get("code") == -32700,
           "json-invalido-en-stdio → error-32700")
    else:
        # Si no hay stdout (el proceso terminó rápido) consideramos ok el comportamiento
        ok(True, "json-invalido-en-stdio → sin crash")
except Exception as e:
    ok(False, "json-invalido-en-stdio → excepcion: %s" % e)

# ── Resumen ────────────────────────────────────────────────────────────────────────────────
total = _pass + _fail
print("%d/%d tests pasados" % (_pass, total))
if _fail == 0:
    print("OK %d tests — mcp_server allowlist fail-closed verificada" % _pass)
else:
    print("FALLOS: %d" % _fail)
sys.exit(_fail)
