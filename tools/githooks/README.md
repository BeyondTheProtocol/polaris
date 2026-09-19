# tools/githooks — Fix (A): gate nativo de git contra commit/push a master/main

Contexto completo: `_cajita/radar-mejoras-10jul/C-guard-merge-master.md` (Fix c, el gate
software del muro). Este directorio es el complemento **a nivel de git**: `.claude/hooks/`
solo lo leen las sesiones de Claude Code; una sesión con
`--allow-dangerously-skip-permissions` se salta esos hooks pero **no** los hooks nativos de
`git` — por eso este segundo cinturón vive aquí, versionado, no en `.git/hooks/` (que no se
versiona y que cada máquina tiene distinto).

## Qué hace

- `pre-commit`: bloquea un `commit` hecho estando en la rama `master`/`main`.
- `pre-push`: bloquea un `push` cuyo ref remoto es `refs/heads/master` o `refs/heads/main`
  (mira el destino, no la rama local — cubre empujar una rama de trabajo directo a la base).
- `reference-transaction` (11-sep-26): bloquea **cualquier** cambio de `refs/heads/master|main`
  sin gate, venga de donde venga: `merge` limpio, fast-forward, `reset`, `cherry-pick`,
  `update-ref`, `branch -f`. Existe porque `pre-commit` solo corre en `git commit`: una fusión sin
  conflictos a la base pasaba sin `BTP_GIT_BASE_OK=1` (visto en vivo el 11-sep). Deja pasar
  `pack-refs`/`gc`, que reescriben la ref sin cambiar su valor. El interruptor lo busca en la casa
  base (vía `--git-common-dir`), así que también frena desde un worktree.
  Quién lo abre a propósito: `deploy_ff.sh` en su fast-forward ya comprobado. `cerrar_sesion.py
  --apply` NO lo abre: pide el `BTP_GIT_BASE_OK=1` del OK humano antes de empezar.

Misma política **blanda** que el Fix (c): variable de entorno `BTP_GIT_BASE_OK=1` puesta a
mano tras un OK humano explícito abre el paso. Frena lo accidental (justo lo que pasó la
noche del 10-jul), no a alguien decidido a saltárselo (ver Limitación, abajo).

## Toggle compartido con el Fix (c) — apagado por defecto

Ambos hooks son **inertes** (exit 0 inmediato, no cambian nada) a menos que exista:

```
.claude/hooks/.base_gate_on
```

Es el MISMO fichero que enciende el gate software del muro (`muro_guard.py`) — un solo
interruptor prende las dos capas a la vez. Sin ese fichero, tener `core.hooksPath` apuntando
aquí no bloquea nada.

## Cómo activar (no lo hace este cambio; lo deja preparado)

```sh
cd ~/claudecode
git config core.hooksPath tools/githooks   # ruta RELATIVA: funciona igual en el portátil y en Polaris
touch .claude/hooks/.base_gate_on          # enciende el gate (las dos capas)
```

### Bug que esto corrige de paso

Hoy `core.hooksPath` en este repo apunta a `/Users/polaris/claudecode/.git/hooks` (absoluto,
de Polaris) — en el portátil esa ruta no existe, así que **los hooks llevan tiempo muertos**
sin que nadie lo note. Usar la ruta relativa `tools/githooks` arregla eso en ambas máquinas
a la vez que despliega el Fix (A).

## Limitación honesta

`git commit --no-verify` y `git push --no-verify` **se saltan estos hooks** (es
comportamiento estándar de git, no un bug de este script). Esto frena lo accidental/por
defecto — un agente o una sesión que no está pensando en saltarse la base — pero no frena a
un agente que decide usar `--no-verify` a propósito. La garantía real contra eso sigue siendo
la de siempre: **no correr enjambres de sesiones `--allow-dangerously-skip-permissions` sin
supervisión**, no este hook. Defensa en profundidad, no una bóveda.

## Pruebas

Antes de activar, verificar en un repo desechable (no en `~/claudecode`) las 5 combinaciones:
toggle off, toggle on + master sin gate, toggle on + master con `BTP_GIT_BASE_OK=1`, toggle
on + rama de trabajo, y `pre-push` a master sin gate. Hecho el 11-jul-26; salida guardada en
el mensaje del PR/commit que introduce este directorio.
