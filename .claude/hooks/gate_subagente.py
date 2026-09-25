#!/usr/bin/env python3
"""gate_subagente.py — hook PostToolUse (Agent|Task): el gate de salida también lee a los subagentes.

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.

EL AGUJERO (25-sep-2026, fase 0 de P8)
  `gate_salida.py` revisa lo que YO le digo a {{TITULAR}} (hook Stop). Los informes de los subagentes no
  los revisaba nadie: no hay hook SubagentStop, y lo que un comité devuelve me llega como resultado
  de herramienta. Replay de 14 días sobre `gate_salida.revisar()`: el 48,2 % de los informes de
  subagente con al menos un hallazgo, frente al 15,7 % del hilo principal. `falsa_certeza` 18,3 %,
  `cita_no_respalda` 13,0 %. Y yo resumo esos informes para ella.

POR QUÉ PostToolUse Y NO SubagentStop
  La doc de hooks (code.claude.com/docs/en/hooks, sección SubagentStop) dice que su
  `additionalContext` va AL SUBAGENTE y lo mantiene corriendo, y remite a PostToolUse sobre `Agent`
  para avisar a la sesión madre. Aquí se quiere lo segundo: que el aviso me llegue a MÍ, pegado al
  informe, antes de que lo resuma. No se le pide al subagente que rehaga nada (ni tokens, ni bucles).

QUÉ HACE (y qué NO)
  · Pasa el texto del informe por `gate_salida.revisar()` y se queda solo con los checks de
    INTEGRIDAD DE LA EVIDENCIA (`CHECKS_INFORME`). Voz, tablas, disclaimers o enrutado son normas de
    cómo le hablo a {{TITULAR}}, no de un informe interno que voy a reescribir.
  · Si hay hallazgos, devuelve `additionalContext` (≤1.500 caracteres) con el check y su motivo.
    MODO AVISO: nunca bloquea, nunca decide nada, nunca reescribe el resultado.
  · Deja una traza SIN contenido en `tools/state/gate_subagente.jsonl` de casa base (hora, tipo de
    agente, longitud, checks). Es la serie que dirá si la pieza B (inyección en SubagentStart) baja
    la tasa.
  · No ve los agentes lanzados en segundo plano: su informe llega después, como notificación, no en
    esta respuesta.

FAIL-OPEN, sin excepciones: cualquier error sale 0 y en silencio. Bypass `BTP_GATE_SUB_OFF=1`.

Uso directo:
  echo '{"tool_name":"Agent","tool_response":{...}}' | python3 .claude/hooks/gate_subagente.py
  python3 .claude/hooks/gate_subagente.py --replay 14   # informes REALES: a cuántos avisaría
"""
import importlib.util
import json
import os
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
TRAZA = os.environ.get("BTP_GATE_SUB_TRAZA") or os.path.join(REPO, "tools", "state", "gate_subagente.jsonl")
HERRAMIENTAS = ("Agent", "Task")
MAX_AVISO = 1500

# Integridad de la evidencia: lo que, si lo relayo sin mirar, se convierte en una mentira mía.
# FUERA a propósito (medido el 25-sep sobre 8 informes reales al azar):
#   · falsa_certeza: 6-7 de 8 falsos. Salta justo cuando el subagente hace lo correcto («no
#     encontrado ≠ no existe») o ya nombra la fuente («PubMed, Europe PMC y Crossref: ninguno»).
#     Era 104 de los 162 avisos: con él, el aviso se aprende a ignorar. Queda en la traza.
#   · pendientes_sin_verificar, no_se_sin_mirar: hablan de lo que YO le digo a {{TITULAR}}.
CHECKS_INFORME = (
    "citas_fabricadas", "cita_no_respalda", "preclinico_aplanado", "clinico_asumido",
    "convergencia", "china_omitida", "geografia_como_filtro",
)


def _gate():
    spec = importlib.util.spec_from_file_location("gate_salida_sub", os.path.join(AQUI, "gate_salida.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def texto_informe(resp):
    """Texto del informe de un Agent síncrono; '' si no lo hay (async, error, formato raro)."""
    if isinstance(resp, str):
        return resp
    if not isinstance(resp, dict):
        return ""
    c = resp.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def hallazgos(texto, gate=None):
    """(todos, de_informe): todo lo que dice el gate y lo que se avisa. Nunca lanza."""
    try:
        gate = gate or _gate()
        todos = list(gate.revisar(texto))
    except Exception:
        return [], []
    return todos, [h for h in todos if h[0] in CHECKS_INFORME]


def aviso(agente, hall):
    lineas = ["🔎 GATE SOBRE EL INFORME DE `%s` (modo aviso): antes de resumírselo a {{TITULAR}}, "
              "coteja o marca «sin verificar» lo que salta aquí." % (agente or "subagente")]
    for check, _slug, motivo in hall:
        lineas.append("· [%s] %s" % (check, " ".join(str(motivo).split())[:260]))
    out = "\n".join(lineas)
    return out if len(out) <= MAX_AVISO else out[:MAX_AVISO - 1] + "…"


def _trazar(agente, n, hall, todos):
    """Sin contenido: hora, agente, longitud, checks avisados y todos los que vio el gate."""
    try:
        os.makedirs(os.path.dirname(TRAZA), exist_ok=True)
        with open(TRAZA, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": round(time.time(), 1), "agente": agente, "chars": n,
                                 "checks": sorted({h[0] for h in hall}),
                                 "todos": sorted({h[0] for h in todos})}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def procesar(datos, gate=None):
    """Devuelve el dict de salida del hook, o None si no hay nada que decir."""
    if os.environ.get("BTP_GATE_SUB_OFF") == "1":
        return None
    if not isinstance(datos, dict) or datos.get("tool_name") not in HERRAMIENTAS:
        return None
    resp = datos.get("tool_response")
    texto = texto_informe(resp)
    if not texto.strip():
        return None
    entrada = datos.get("tool_input") if isinstance(datos.get("tool_input"), dict) else {}
    agente = (resp.get("agentType") if isinstance(resp, dict) else None) or entrada.get("subagent_type") or "general-purpose"
    todos, hall = hallazgos(texto, gate)
    _trazar(agente, len(texto), hall, todos)
    if not hall:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                   "additionalContext": aviso(agente, hall)}}


def replay(dias):
    """Informes finales REALES de subagentes (carpetas subagents/): a cuántos avisaría y cuánto tarda."""
    import glob
    import collections
    corte = time.time() - dias * 86400
    gate = _gate()
    n = con = 0
    por = collections.Counter()
    lento = 0.0
    base = os.path.join(os.environ.get("BTP_PROJECTS_DIR") or os.path.expanduser("~/.claude/projects"))
    for f in glob.glob(os.path.join(base, "**", "subagents", "*.jsonl"), recursive=True):
        try:
            if os.path.getmtime(f) < corte:
                continue
            ultimo = None
            for linea in open(f, encoding="utf-8", errors="ignore"):
                o = json.loads(linea)
                if o.get("type") == "assistant":
                    t = texto_informe(o.get("message") or {})
                    if t.strip():
                        ultimo = t
        except Exception:
            continue
        if not ultimo:
            continue
        t0 = time.time()
        hall = [h for h in gate.revisar(ultimo) if h[0] in CHECKS_INFORME]
        lento = max(lento, time.time() - t0)
        n += 1
        con += bool(hall)
        for h in hall:
            por[h[0]] += 1
    print("informes: %d · avisaría en %d (%.1f %%) · peor tiempo %.2f s" % (n, con, con * 100.0 / max(n, 1), lento))
    for c, v in por.most_common():
        print("  %-26s %d" % (c, v))
    return 0


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--replay":
        return replay(int(sys.argv[2]) if len(sys.argv) > 2 else 14)
    try:
        out = procesar(json.load(sys.stdin))
        if out:
            print(json.dumps(out, ensure_ascii=False))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
