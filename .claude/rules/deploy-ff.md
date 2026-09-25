---
paths:
  - "tools/deploy_ff.sh"
  - "tools/githooks/**"
---

# Desplegar código Air↔Polaris: SOLO fast-forward (regla, 12-jul-26)

El fork Air↔Polaris del 12-jul se creó porque los deploys **RE-COMMITEABAN** el trabajo del otro lado (mensajes «…desplegado desde portátil») en vez de mover `master` por fast-forward. Resultado: dos historias que arriesgaban **borrar trabajo**.

**NUNCA se despliega copiando ficheros + `git commit` en la otra máquina, ni reescribiendo mensajes.**

La única vía es:
```bash
tools/deploy_ff.sh to-polaris    # o from-polaris
```
(bundle + `git fetch … --ff-only`): mueve `master` **solo** si es fast-forward limpio, y **REHÚSA** si las dos han divergido, pidiendo reconciliar a mano.

**Regla de oro:** tras commitear en UNA máquina, despliega a la otra **antes** de que la otra commitee. Si ambas commitean a la vez, el deploy rehúsa (no es FF) y toca reconciliar con merge auditado, como el 12-jul.

**Nunca `git push` del repo a GitHub** (historial clínico).

El gate blando `tools/githooks/pre-commit` (frena commits directos a `master`, INERTE hasta que exista `.claude/hooks/.base_gate_on`) está disponible en ambas máquinas vía `core.hooksPath=tools/githooks`. Desde el 11-sep-26 lo completa `tools/githooks/reference-transaction`, que frena **cualquier** cambio de `master` sin `BTP_GIT_BASE_OK=1` (merge, fast-forward, reset, cherry-pick). `deploy_ff.sh` lo abre solo en su paso de fast-forward ya comprobado.

⚠️ Al reconciliar a mano: una reconciliación puede **tirar una protección** sin que se note. Detalle: memoria [[feedback-reconciliacion-puede-tirar-proteccion]].
