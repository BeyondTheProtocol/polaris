#!/usr/bin/env python3
"""test_gate_subagente.py — el gate de salida también lee los informes de los subagentes (P8, pieza A).

Fija el contrato de `.claude/hooks/gate_subagente.py` con un gate FALSO (sin red, sin disco real):
  · solo pasan los checks de integridad de evidencia; los de voz/conversación se descartan;
  · el aviso va como additionalContext de PostToolUse, ≤1.500 caracteres, y nunca bloquea;
  · Agent en segundo plano (sin content), otro tool, texto vacío o BTP_GATE_SUB_OFF=1 → silencio;
  · la traza no lleva ni una palabra del informe;
  · si el gate revienta, fail-open (silencio, sin excepción).
"""
import importlib.util
import json
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="test_gate_sub_")
os.environ["BTP_GATE_SUB_TRAZA"] = os.path.join(_TMP, "traza.jsonl")
os.environ.pop("BTP_GATE_SUB_OFF", None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("gate_subagente", os.path.join(ROOT, ".claude", "hooks", "gate_subagente.py"))
gs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gs)

_pass = _fail = 0


def check(nombre, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % nombre)


class GateFalso:
    def __init__(self, hall):
        self.hall = hall

    def revisar(self, texto, tools=None):
        return list(self.hall)


class GateRoto:
    def revisar(self, texto, tools=None):
        raise RuntimeError("boom")


SECRETO = "INFORME-PRIVADO-XYZ el fármaco cura seguro"


def llamada(content, tool="Agent", agente="comite-medico"):
    return {"tool_name": tool, "tool_input": {"subagent_type": agente, "prompt": "p"},
            "tool_response": {"status": "completed", "agentType": agente, "content": content}}


def main():
    hall = [("clinico_asumido", "feedback-a", "Das por hecha la medicación."),
            ("falsa_certeza", "feedback-d", "ruido en informes"),
            ("tells_ia", "feedback-b", "guion largo"),
            ("cita_no_respalda", "feedback-c", "PMID 1 no dice eso." * 200)]
    out = gs.procesar(llamada([{"type": "text", "text": SECRETO}]), GateFalso(hall))
    ctx = (out or {}).get("hookSpecificOutput", {}).get("additionalContext", "")
    check("devuelve aviso PostToolUse", out and out["hookSpecificOutput"]["hookEventName"] == "PostToolUse")
    check("incluye clinico_asumido", "[clinico_asumido]" in ctx)
    check("descarta falsa_certeza (6-7 de 8 falsos en informes)", "falsa_certeza" not in ctx)
    check("incluye cita_no_respalda", "[cita_no_respalda]" in ctx)
    check("descarta tells_ia (voz, no evidencia)", "tells_ia" not in ctx)
    check("aviso ≤1.500 caracteres", 0 < len(ctx) <= gs.MAX_AVISO)
    check("nombra al agente", "comite-medico" in ctx)
    check("nunca bloquea", "decision" not in (out or {}) and "permissionDecision" not in json.dumps(out))

    check("solo voz → sin aviso",
          gs.procesar(llamada("texto largo"), GateFalso([("tells_ia", "s", "m")])) is None)
    check("Agent en segundo plano (sin content) → silencio",
          gs.procesar({"tool_name": "Agent", "tool_response": {"status": "async_launched"}}, GateFalso(hall)) is None)
    check("otro tool → silencio", gs.procesar(llamada("x", tool="Bash"), GateFalso(hall)) is None)
    check("texto vacío → silencio", gs.procesar(llamada([{"type": "text", "text": "  "}]), GateFalso(hall)) is None)
    check("gate roto → fail-open", gs.procesar(llamada("texto"), GateRoto()) is None)
    check("content como str también vale", gs.procesar(llamada("texto"), GateFalso(hall)) is not None)

    os.environ["BTP_GATE_SUB_OFF"] = "1"
    check("bypass BTP_GATE_SUB_OFF=1", gs.procesar(llamada("texto"), GateFalso(hall)) is None)
    os.environ.pop("BTP_GATE_SUB_OFF")

    traza = open(os.environ["BTP_GATE_SUB_TRAZA"], encoding="utf-8").read()
    check("hay traza", traza.count("\n") >= 3)
    check("la traza no lleva el informe", "INFORME-PRIVADO" not in traza and "fármaco" not in traza)
    check("la traza guarda también lo no avisado", "falsa_certeza" in traza)

    print("test_gate_subagente: %d ok, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
