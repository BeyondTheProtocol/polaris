#!/usr/bin/env python3
"""traza_subagente.py — hook PostToolUse: deja constancia de qué comité se usó.

EL AGUJERO (25-jul-26, al construir La Anatomía):
  `tools/run_agent.sh` YA registra una traza por ejecución, pero solo pasan por él
  los comités que corren como DAEMON (asistente, auto-mejora, prensa, dm-inbox,
  git, orquestador). Los otros 29 se invocan como sub-agentes DENTRO de una
  sesión, y esa vía no toca run_agent.sh: no dejaban ni una línea. Resultado: el
  sistema no sabía a quién había usado, y el mapa no se encendía nunca.

QUÉ HACE (y qué NO):
  · Al terminar una llamada al Agent/Task, apunta el NOMBRE del comité, la hora y
    si fue bien. Nada más.
  · NO guarda el prompt, ni la respuesta, ni un solo dato del caso. Esta traza es
    para saber QUIÉN trabajó, no QUÉ dijo.
  · Solo apunta nombres que existen como comité en `.claude/agents/`; los agentes
    de fábrica (Explore, Plan, general-purpose) no son del gabinete y se ignoran.
  · Escribe SIEMPRE en el estado de CASA BASE, aunque la sesión viva en un
    worktree: el sistema vivo es uno solo y La Anatomía lo lee de ahí.

FAIL-OPEN, sin excepciones: este hook corre en cada sub-agente y no puede romper
una sesión ni bloquear nada. Cualquier error se traga y sale 0. No es un guard,
es un cuaderno de bitácora.
"""
import json
import os
import sys

# Casa base, NUNCA el worktree: el estado vivo se resuelve en un único sitio.
REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")

# El harness manda "Agent"; "Task" queda por compatibilidad (ver la lección de
# muro_guard, que comprobaba solo "Task" y tuvo la regla muerta una semana).
HERRAMIENTAS = ("Agent", "Task")


def _es_comite(nombre):
    if not nombre or "/" in nombre or "\\" in nombre or nombre.startswith("."):
        return False
    return os.path.isfile(os.path.join(REPO, ".claude", "agents", nombre + ".md"))


def _apuntar():
    datos = json.load(sys.stdin)
    if not isinstance(datos, dict) or datos.get("tool_name") not in HERRAMIENTAS:
        return

    # Ciclo de vida (14-sep-2026, a raíz de «¿Polaris conserva el agent_id y el motivo de salida?»):
    # agent_id, tool_use_id, sesión, tipo y status inicial de CUALQUIER subagente con agentId, no solo
    # de los comités. Sigue la misma promesa de arriba: ni prompt, ni descripción, ni respuesta. El
    # final lo resuelve después `tools/ciclo_agentes.py cerrar`. Con su propia red: si falla, la línea
    # de observabilidad de abajo se escribe igual.
    try:
        sys.path.insert(0, os.path.join(REPO, "tools"))
        import ciclo_agentes
        ciclo_agentes.apuntar_lanzamiento(datos)
    except Exception:
        pass

    entrada = datos.get("tool_input")
    if not isinstance(entrada, dict):
        return
    comite = entrada.get("subagent_type")
    if not _es_comite(comite):
        return

    # El resultado: el harness marca el error en la respuesta cuando lo hay.
    resp = datos.get("tool_response")
    malo = False
    if isinstance(resp, dict):
        malo = bool(resp.get("is_error") or resp.get("isError"))
    elif isinstance(resp, str):
        malo = resp.startswith("Error:")

    sys.path.insert(0, os.path.join(REPO, "tools"))
    import observabilidad
    observabilidad.registrar(
        agente=comite,
        job="sesion",
        resultado="fail" if malo else "ok",
    )


def main():
    # Una sola red, la de fuera, y bien ancha: da igual qué falle ahí dentro
    # (stdin raro, observabilidad ausente, disco lleno), esto sale en 0 y calla.
    try:
        _apuntar()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
