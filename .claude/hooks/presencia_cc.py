#!/usr/bin/env python3
"""presencia_cc.py — hook UserPromptSubmit: candidata a señal de presencia de {{TITULAR}}.

EL AGUJERO (11-sep-26, deuda `dead_man_ausencia`, 1241 detecciones):
  El dead-man de healthcheck.py solo oía a Telegram. {{TITULAR}} trabaja sobre todo en sesiones
  de Claude Code y usa Telegram para RECIBIR, así que el sistema la daba por ausente y bajaba
  el gasto al 30% justo mientras ella trabajaba.

QUÉ HACE (y qué NO):
  · Apunta {prompt_id, session_id, transcript_path, ts} en la cola
    tools/state/healthcheck/presencia_pendiente.jsonl de CASA BASE. Nada más.
  · NO llama a mark_seen. La decisión la toma healthcheck.py (_procesar_presencia_cc) leyendo
    el transcript: solo un prompt con origin == {"kind": "human"} y sin envoltorio de
    automatismo cuenta. El entorno del proceso NO sirve de prueba: un `claude -p` lanzado
    desde una sesión de escritorio hereda CLAUDE_CODE_ENTRYPOINT=claude-desktop (verificado
    con sonda el 11-sep-26).
  · NO guarda el texto del prompt.
  · Filtro barato de entrada: el lazo (BTP_AGENT_DEPTH / BTP_AGENT / MURO_PROFILE) y los
    envoltorios de automatismo conocidos no se apuntan siquiera. El filtro de verdad está en
    healthcheck; esto solo ahorra ruido.

FAIL-OPEN para el prompt (siempre sale 0, nunca bloquea ni inyecta contexto) y FAIL-CLOSED
para la presencia (ante cualquier duda no apunta, y lo apuntado aún se verifica después).
"""
import json
import os
import sys
import time

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
COLA = os.path.join(STATE, "healthcheck", "presencia_pendiente.jsonl")

# Marcadores que pone el lazo al lanzar un agente (run_agent.sh / muro).
MARCAS_LAZO = ("BTP_AGENT_DEPTH", "BTP_AGENT", "MURO_PROFILE")
# Envoltorios con los que el harness inyecta prompts que no teclea nadie.
AUTOMATICOS = ("<task-notification", "<scheduled-task", "<ci-monitor-event")
# Tope de la cola: healthcheck la vacía cada ~30 min; si crece más, algo va mal y no se apunta.
COLA_MAX_BYTES = 256 * 1024


def _apuntar():
    if any(os.environ.get(k) for k in MARCAS_LAZO):
        return
    datos = json.load(sys.stdin)
    if not isinstance(datos, dict):
        return
    prompt = datos.get("prompt")
    if isinstance(prompt, str) and prompt.lstrip().startswith(AUTOMATICOS):
        return
    campos = {k: datos.get(k) for k in ("prompt_id", "session_id", "transcript_path")}
    if not all(isinstance(v, str) and v for v in campos.values()):
        return
    try:
        if os.path.getsize(COLA) > COLA_MAX_BYTES:
            return
    except OSError:
        pass
    campos["ts"] = time.time()
    os.makedirs(os.path.dirname(COLA), mode=0o700, exist_ok=True)
    with open(COLA, "a", encoding="utf-8") as f:
        f.write(json.dumps(campos, ensure_ascii=False) + "\n")


def main():
    try:
        _apuntar()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
