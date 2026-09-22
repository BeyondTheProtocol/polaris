---
paths:
  - ".claude/hooks/**"
  - "tools/rodaje_muro.py"
  - "tools/replay_guard.py"
---

# Tocar un hook del muro

- **Hooks del muro: rodaje antes de fusionar** (24 h en rama, o replay de las llamadas reales, guard viejo vs nuevo); fusionar es gate suyo. [[feedback-muro-soak-antes-de-fusionar]]
- Herramientas: `python3 tools/rodaje_muro.py iniciar|revisar` y `python3 tools/replay_guard.py` (PreToolUse). Para el gate de salida (hook Stop), `python3 tools/replay_gate.py`.

Vivía en `normas-que-se-me-olvidan.md` (carga fija). Se movió aquí el 22-sep-26 para dar margen a la carga fija: esta norma solo aplica al editar un hook, y con `paths:` se carga justo entonces.
