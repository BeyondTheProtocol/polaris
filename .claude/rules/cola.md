---
paths:
  - "tools/cola.py"
  - "tools/dispatcher*.py"
  - "tools/state/cola/**"
  - "tests/test_cola*.py"
---

# Cola: esquema consumer-first (regla, 23/6/26)

La allowlist `CAMPOS` es **CERRADA a propósito** (anti-inyección, fail-closed): un job con un campo, o con un **VALOR de enum** (`perfil` / `tipo` / `prioridad`), que el consumidor no conoce se **RECHAZA a `failed/`**, no se ejecuta.

**Eso es CORRECTO, no un bug.** Un campo nuevo puede ser un *freno*, y un consumidor viejo que lo «ignorase» estaría haciendo *fail-open* en una restricción de seguridad. Por eso **NO se relaja la allowlist**.

**Cómo se añade un campo o un valor de enum, en este orden:**

| # | Paso |
|---|---|
| 1 | Se **fusiona a casa base** el consumidor (`tools/cola.py`) |
| 2 | Se **RECARGA el daemon** |
| 3 | **Solo entonces** un productor puede emitir jobs con ese campo |

Los worktrees parten de casa base commiteada y al día. El dispatcher **siempre** usa las tools de casa base (`REPO=${BTP_REPO:-$HOME/claudecode}`), así que un worktree con un campo nuevo **no se autodespliega**.

Si aun así cae un job de *skew*, va a `failed/` con motivo **`schema-desconocido`** (distinto de `schema-invalido`, que es un job plantado) = **recuperable re-encolando después de fusionar**.

Detalle: memoria [[feedback-cola-consumer-first]].
