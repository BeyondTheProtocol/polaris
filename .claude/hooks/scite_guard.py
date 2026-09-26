#!/usr/bin/env python3
"""scite_guard.py — lo que sale a scite pasa por el muro (PreToolUse, matcher `mcp__scite__.*`).

POR QUÉ EXISTE (26-sep-2026, hallazgo A1 del prompt-audit, plan aprobado por {{TITULAR}}). Las reglas
de qué puede salir a scite (`.claude/rules/scite-mcp.md`) solo existían como texto, y ese texto no
se carga al llamar a scite: la regla tiene `paths:` y una llamada MCP no lee ningún fichero. En el
lazo no hay hueco (`muro_guard.py` deniega todo MCP), pero en sesión interactiva nadie miraba los
argumentos. `salida_guard.py` solo pedía confirmación, y en bypass solo avisaba.

QUÉ HACE, en este orden:
  1. Herramientas que ESCRIBEN colecciones → deniega (los agentes solo leen colecciones).
  2. Todos los textos de `tool_input` → `borde.egress_cientifico`, la MISMA política que ya aplica el
     cliente del lazo (`tools/scite_mcp.py`): nombre, deny-list, PII dura, huella genómica.
  3. `borde.senas_caso` → con 3 o más señas (edad, histología, receptores, variante, línea) en la
     misma llamada, deniega: juntas describen a una paciente concreta.
  Los prompts `fact-check-claim` y `systematic-review-screen` son PROMPTS MCP, no herramientas: un
  hook no los ve. Lo recuerda el mensaje de denegación.

MODO. `tools/state/scite_guard_modo` (casa base) o `BTP_SCITE_GUARD`: `sombra` (por defecto, rodaje
de 24 h de hooks-muro.md) apunta «habría denegado» y deja pasar con aviso; `bloquea` deniega. Pasar
a `bloquea` es un OK de {{TITULAR}}.

LOG. `tools/state/scite_guard.jsonl`: herramienta, decisión, motivo y hash de los argumentos.
NUNCA el texto (sería copiar a disco justo lo que se está protegiendo).

FALLO. Una vez reconocida la herramienta como scite, un error interno deniega (es salida de datos),
salvo en modo sombra, donde un fallo del guard no puede cortar el trabajo. Escotilla consciente:
BTP_SCITE_OK=1, que queda registrada.
"""
import hashlib
import json
import os
import sys
import time

ESCRIBE = {
    "create_collection", "update_collection", "delete_collection",
    "add_dois_to_collection", "remove_dois_from_collection",
    "create_collection_note", "update_collection_note", "delete_collection_note",
}
UMBRAL_SENAS = 3


def _casa_base():
    ov = os.environ.get("BTP_REPO")
    raiz = os.path.abspath(ov) if ov else os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return raiz.split(os.sep + os.path.join(".claude", "worktrees") + os.sep)[0]


def _estado(nombre):
    d = os.environ.get("BTP_STATE_DIR") or os.path.join(_casa_base(), "tools", "state")
    return os.path.join(d, nombre)


def modo():
    m = (os.environ.get("BTP_SCITE_GUARD") or "").strip().lower()
    if not m:
        try:
            m = open(_estado("scite_guard_modo"), encoding="utf-8").read().strip().lower()
        except OSError:
            m = ""
    return "bloquea" if m == "bloquea" else "sombra"


def textos(x):
    """Todos los valores de texto de un tool_input, a cualquier profundidad."""
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from textos(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from textos(v)


def veredicto(tool, ti):
    """(deniega, motivo). Puro salvo el sello que ya hace borde.egress_cientifico."""
    corto = tool.split("__")[-1]
    if corto in ESCRIBE:
        return True, "escribe colecciones de scite (los agentes solo las leen)"
    junto = "\n".join(t for t in textos(ti) if t and t.strip())
    if not junto.strip():
        return False, "sin texto"
    # borde del MISMO árbol que este hook (en un worktree, el de la rama; el estado, en casa base)
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "tools"))
    import borde
    ok, motivo = borde.egress_cientifico(junto, destino="scite")
    if not ok:
        return True, motivo
    n, senas = borde.senas_caso(junto)
    if n >= UMBRAL_SENAS:
        return True, "%d señas del caso juntas (%s)" % (n, ", ".join(senas))
    return False, "ok"


def _log(tool, decision, motivo, ti):
    try:
        linea = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": tool, "decision": decision,
                 "motivo": motivo, "modo": modo(),
                 "sello": hashlib.sha256(json.dumps(ti, sort_keys=True, ensure_ascii=False)
                                         .encode("utf-8")).hexdigest()[:16]}
        p = _estado("scite_guard.jsonl")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(linea, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _salida(decision, razon, contexto=None):
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                  "permissionDecisionReason": razon}}
    if contexto:
        out["hookSpecificOutput"]["additionalContext"] = contexto
    print(json.dumps(out, ensure_ascii=False))


AYUDA = ("A scite solo sale terminología: genes, fármacos, histologías, DOI, NCT, sin nada del caso "
         "(nombre, fechas, NHC, variantes exactas) y nunca 3 señas juntas (edad, histología, "
         "receptores, variante, línea). Reformula la consulta en genérico. Los prompts "
         "`fact-check-claim` y `systematic-review-screen` no se usan desde agentes. "
         "Regla: .claude/rules/scite-mcp.md")


def main():
    data = json.loads(sys.stdin.read() or "{}")
    tool = data.get("tool_name") or ""
    if not tool.startswith("mcp__scite__"):
        return 0
    ti = data.get("tool_input") or {}
    try:
        deniega, motivo = veredicto(tool, ti)
    except Exception as e:  # reconocida como scite: un fallo interno no deja salir nada
        if modo() == "sombra":
            _log(tool, "error-sombra", repr(e)[:120], ti)
            return 0
        _log(tool, "deny", "error interno: %r" % (e,), ti)
        _salida("deny", "scite_guard falló por dentro (%r): no dejo salir la consulta." % (e,))
        return 0
    if not deniega:
        _log(tool, "allow", motivo, ti)
        return 0
    if os.environ.get("BTP_SCITE_OK") == "1":
        _log(tool, "allow-escotilla", motivo, ti)
        return 0
    if modo() == "sombra":
        _log(tool, "habria-denegado", motivo, ti)
        _salida("allow", "scite_guard en rodaje", "⚠️ scite_guard (rodaje, aún no bloquea) habría "
                "DENEGADO esta llamada: %s. %s" % (motivo, AYUDA))
        return 0
    _log(tool, "deny", motivo, ti)
    _salida("deny", "MURO ⛔ scite: %s. %s" % (motivo, AYUDA))
    return 0


if __name__ == "__main__":
    # Watchdog: si tardo más que el timeout de settings (5 s), deniego antes de que Claude Code me
    # cancele y lo convierta en un permitir. Ver _watchdog.py.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import _watchdog
    except ImportError:
        _watchdog = None
        sys.stderr.write("scite_guard: falta _watchdog.py; sigo sin vigilante\n")
    if _watchdog:
        _watchdog.armar(5, 'scite_guard')
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        # Ni siquiera se pudo leer la entrada: sin saber si es scite, deniega solo en modo bloquea.
        if modo() == "bloquea":
            sys.stderr.write("scite_guard: error %r; deniego (salida de datos)\n" % (e,))
            sys.exit(2)
        sys.exit(0)
