---
paths:
  - ".claude/hooks/**"
  - "tools/rodaje_muro.py"
  - "tools/replay_guard.py"
---

# Tocar un hook del muro

- **Hooks del muro: rodaje antes de fusionar** (24 h en rama, o replay de las llamadas reales, guard viejo vs nuevo); fusionar es gate suyo. [[feedback-muro-soak-antes-de-fusionar]]
- **Canario del muro (P9, 25-sep-26):** todo `settings.*.json` que use `run_agent.sh` lleva `canario_muro.sh` en SessionStart; sin él el lazo falla cerrado y dispara código rojo. Settings nuevo → añádelo y a `tests/test_canario_muro.py`. Tras actualizar Claude Code, el primer run repite el preflight solo. El lazo usa la versión fijada por `tools/claude_lazo.py` (`estado` para verla); solo sube si la nueva pasa el canario en todos los settings.
- Herramientas: `python3 tools/rodaje_muro.py iniciar|revisar` y `python3 tools/replay_guard.py` (PreToolUse). Para el gate de salida (hook Stop), `python3 tools/replay_gate.py`.

Vivía en `normas-que-se-me-olvidan.md` (carga fija). Se movió aquí el 22-sep-26 para dar margen a la carga fija: esta norma solo aplica al editar un hook, y con `paths:` se carga justo entonces.
