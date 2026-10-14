# com.btp.firmante — rodaje, activación, fijar la clave pública y marcha atrás

Pieza A del muro (aprobada por {{TITULAR}} el 13-oct-2026). El daemon `tools/firmante.py` firma con
Ed25519 el permiso de salida; `tools/permiso_envio.py` lo comprueba con la clave pública. Sirve
para que su orden abra el permiso también cuando entra al Mac por SSH, donde el Llavero no se
deja leer.

Todo lo de esta página lo hace **la sesión que cierra, desde casa base, o {{TITULAR}} en el Mac**.
Nada de esto se hizo al construir la rama: no se cargó el daemon, no se creó ninguna clave y
no se tocó el estado vivo.

## Qué cambia en cada paso (para saber dónde se está)

| Estado | Quién firma | Por SSH abre | Cómo se reconoce |
|---|---|---|---|
| 0 · rama fusionada, daemon sin cargar | HMAC, como antes | no | `python3 tools/firmante.py estado` dice «NO existe» |
| 1 · daemon cargado, pública sin fijar | HMAC, como antes | no | hay socket y hay «pública que anuncia»; `PUBLICA` sigue en `None` |
| 2 · pública fijada por commit y fusionada | el firmante | **sí** | `estado` dice «coinciden: sí» |

El paso 1 no cambia nada para ninguna sesión: con `PUBLICA = None` el hook ni siquiera llama al
daemon. El corte es el commit del paso 2.

## 0 · Antes de fusionar la rama: rodaje

La rama toca un hook del muro (`.claude/hooks/salida_guard.py`) y el emisor
(`.claude/hooks/ok_envio_prompt.py`). Norma: 24 h en rama o replay, y fusionar es gate de {{TITULAR}}.

```bash
# desde casa base, con la rama en un worktree <WT>
python3 tools/rodaje_muro.py iniciar --nuevo <WT>/.claude/hooks/salida_guard.py \
    --viejo .claude/hooks/salida_guard.py --horas 24 --nota "pieza A: firmante + guard llavero/socket"
python3 tools/replay_guard.py .claude/hooks/salida_guard.py <WT>/.claude/hooks/salida_guard.py \
    --salida /tmp/replay-piezaA.json
python3 tools/rodaje_muro.py revisar
```

`replay_guard.py` no toca el estado vivo (aísla `BTP_STATE_DIR` y el log en un temporal): solo
hace decidir a los dos guards sobre las órdenes reales del historial. Lo que hay que mirar:
ALLOW→DENY (posible falso positivo; se esperan solo órdenes que lanzan `security` sobre la clave
del permiso con un envoltorio delante, o que hablan con `firmante.sock`) y DENY→ALLOW (agujero:
no debe haber ninguno).

Antes de fusionar, la suite completa: `bash tests/test_all.sh`.

## 1 · Activar el daemon (gate de {{TITULAR}}; uno de los tres singletons)

Desde casa base, con la rama ya fusionada, y en una sesión que **no** sea por SSH si se puede
(el daemon corre en `gui/501` de todos modos; es para poder hacer luego el paso 2a):

```bash
python3 tools/launchd/registro.py estado            # ¿algo cubre ya este trabajo? (no debería)
python3 tools/activar_daemon.py com.btp.firmante --dry
python3 tools/activar_daemon.py com.btp.firmante
python3 tools/firmante.py estado
tail -5 tools/launchd/logs/firmante.out tools/launchd/logs/firmante.err
```

En su primer arranque el daemon crea la clave privada en el Llavero (servicio
`btp-firmante-ed25519`) **solo si no existe**. Si existe y no se deja leer, no crea otra:
escucha igual y contesta «llavero-ilegible» hasta que pueda leerla.

Qué tiene que salir en `firmante.out`: `firmante: escuchando en … · clave ok · pública <64 hex>`.

Si sale `clave ilegible`: el Mac se reinició y nadie ha iniciado sesión en la pantalla, o el
Llavero está bloqueado. Se arregla desbloqueándolo, no creando otra clave.

**Sin verificar en la máquina real** (se probó con un `security` simulado, no contra el
Llavero): que `security -i` acepte `add-generic-password … -w <valor>` por su entrada. El código
relee la clave tras crearla y, si no es la que escribió, no firma nada (`clave ilegible`). Si
pasa eso, se crea a mano desde Terminal en el Mac y se recarga el daemon:

```bash
/usr/bin/security add-generic-password -a "$USER" -s btp-firmante-ed25519 -w "$(/usr/bin/openssl rand -hex 32)"
python3 tools/activar_daemon.py com.btp.firmante --reemplaza --kick
```

## 2 · Fijar la clave pública (el corte)

### 2a · Sacar la pública de una fuente de fiar

La buena es el Llavero, leído en una sesión **local** del Mac (Terminal, no SSH):

```bash
python3 tools/firmante.py publica        # imprime 64 hex; por stderr, la línea exacta a pegar
```

Si solo hay SSH, el daemon la deja escrita en `tools/state/ok_envio/firmante_publica.txt` (es
lo que enseña `firmante.py estado`). Ese fichero es un aviso, no una fuente: lo puede escribir
cualquier proceso del usuario. Si se usa, hay que hacer la comprobación 2c sin falta. Fijar una
pública equivocada no abre un agujero silencioso: el daemon contesta «clave-distinta» y deja de
firmar, y los permisos que firme otra clave siguen necesitando la línea de {{TITULAR}} en el
transcript para valer.

### 2a-bis · Antes del corte: ¿el hook de una sesión por SSH tiene `cryptography`?

Los dos hooks (`ok_envio_prompt.py`, `salida_guard.py`) se lanzan por su shebang
(`#!/usr/bin/env python3`): qué python3 es lo decide el PATH que les da el harness.
`/usr/bin/python3` NO trae `cryptography`; `/opt/homebrew/bin/python3` sí (49.0.0). Sin la
librería no se puede comprobar ninguna firma y TODO envío queda denegado.

Comprobado: en el Bash de una sesión por SSH, `python3` resuelve a `/opt/homebrew/bin/python3`.
**Sin comprobar: qué PATH recibe el hook.** Dos redes, y una comprobación:

- red 1 (en el código): si a un hook le falta la librería en modo Ed25519 y existe
  `/opt/homebrew/bin/python3`, se relanza solo con ese (`permiso_envio.relanzar_si_falta_cripto`);
- red 2: si aun así no la hay, deniega con el motivo «falta la librería `cryptography`»;
- la comprobación, desde una sesión por SSH real y ANTES de fusionar el 2b:

```bash
/usr/bin/env python3 -c "import cryptography, sys; print(sys.executable, cryptography.__version__)"
ls -l /opt/homebrew/bin/python3 && /opt/homebrew/bin/python3 -c "import cryptography"
```

Si la segunda falla, no se fija la pública hasta instalarla ahí
(`/opt/homebrew/bin/python3 -m pip install cryptography`, con su OK).

### 2b · El commit

En una rama (worktree nuevo), **un solo cambio** en `tools/permiso_envio.py`:

```python
PUBLICA = "<los 64 hex del paso 2a>"
```

```bash
bash tests/test_all.sh            # las baterías del HMAC fijan su modo: tienen que seguir en verde
                                  # (probado el 14-oct con una pública de mentira en 17 baterías
                                  #  del permiso y del guard, no en la suite entera)
git commit -am "feat(muro): fija la clave pública del firmante (retira el HMAC)"
```

y se fusiona a casa base como cualquier otra rama (su OK). **Justo después de fusionar, se
recarga el daemon** (singleton de launchd, de uno en uno y diciéndolo): `PUBLICA` es una
constante de módulo y el daemon la leyó al arrancar; sin recargar sigue creyendo que no hay
ninguna fijada y no avisaría de una clave distinta.

```bash
python3 tools/activar_daemon.py com.btp.firmante --reemplaza --kick
python3 tools/firmante.py estado          # «coinciden: sí»
```

Lo mismo tras CUALQUIER fusión que toque `tools/permiso_envio.py`, `tools/lote_envio.py`,
`tools/firmante.py` o `tools/_casa.py`: el daemon corre el código que tenía en memoria.

A partir de esa fusión:

- las sesiones cuyo árbol tiene ese commit piden el permiso al firmante y lo comprueban sin
  Llavero (también por SSH);
- en ese árbol un permiso HMAC deja de valer: `permiso_envio.modo()` pasa a `ed25519`.

### 2c · Comprobar que abre de verdad (con una orden real suya, inocua)

En una sesión nueva (nacida después de la fusión), por SSH, {{TITULAR}} escribe una orden que abra
algo que no tenga efecto, por ejemplo «abre el PR» sin que haya nada que abrir. Tiene que
aparecer en el contexto de la sesión «🔓 {{TITULAR}} ha pedido ABRIR un PR…» y:

```bash
python3 tools/firmante.py estado          # «coinciden: sí»
python3 tools/ok_envio.py --estado        # «permiso ABIERTO para: …» ; luego: --cerrar
tail -3 tools/state/ok_envio/firmante_log.jsonl     # «emitido», con el pid del hook
```

Lo que NO se sabe todavía y esta prueba contesta: si el harness apunta la línea del mensaje en
el transcript antes o después de lanzar el hook `UserPromptSubmit`. El diseño aguanta las dos:
si la línea aún no está, el aviso es «⏳ … abrirá el permiso en cuanto aparezca» y en
`firmante_log.jsonl` queda `pendiente` seguido de `tarde:emitido`. Si lo que queda es
`tarde:pendiente` con «no apareció en el transcript», el harness tarda más de 45 s o no apunta
ese mensaje: es un bloqueo real y hay que subir `ESPERA_S` o mirar el transcript.

## Worktrees viejos y el HMAC

Un worktree nacido antes del commit 2b sigue con su código: emite y comprueba HMAC (si su
sesión puede leer el Llavero) hasta que se cierra o se pone al día. No se puede cortar desde
fuera: borrar la clave `btp-ok-envio-mac` no sirve, porque el hook viejo la vuelve a crear en
una sesión local. Para listarlos:

```bash
for wt in $(git worktree list --porcelain | awk '/^worktree /{print $2}'); do
  git -C "$wt" merge-base --is-ancestor <commit-2b> HEAD 2>/dev/null || echo "sin el corte: $wt"
done
```

Cuando no quede ninguno, el código HMAC de `permiso_envio.py` y las baterías que lo prueban se
pueden retirar (deuda a abrir al fijar la pública: `hmac-ok-envio-retirar-tras-firmante`).

## Pendiente (no hecho en esta rama; para decidir)

- **Anatomía.** El daemon nuevo cambia la superficie de Polaris. En un worktree
  `tests/test_anatomia_al_dia.py` se salta y sellar daría una huella falsa (la anatomía lee casa
  base). Tras fusionar, en casa base: mirar el panel y `python3 tools/anatomia.py sellar`. Hasta
  entonces ese test estará rojo allí.
- **Proteger el código que decide.** `tools/permiso_envio.py` (donde vive `PUBLICA`),
  `tools/firmante.py`, `tools/lote_envio.py` y `tools/_casa.py` no están entre las rutas que los
  guards deniegan escribir; en casa base la regla de permisos solo cubre `.claude/hooks/`.
  Propuesta: añadirlas a esa regla de `settings.json` (deny de Edit/Write en casa base, como
  los hooks) y a las rutas protegidas de `_escribe_bash`, con replay. No se ha tocado nada.
- **32 peticiones basura dejan «ocupado».** Cada petición por un mensaje que aún no está ocupa
  un vigía hasta 45 s (`MAX_VIGIAS`). Un proceso del usuario que mande 32 seguidas hace que una
  orden real que llegue en esa ventana conteste «ocupado» y no se abra. Falla cerrado; cuesta
  repetir la orden.
- **Cotejar el hash también en las líneas tecleadas.** Para un mensaje normal la línea se
  localiza por `promptId`; el `hash_prompt` solo se usa en los encolados.
- **`firmante_publica.txt` se abre siguiendo enlaces.** Un enlace puesto ahí antes de arrancar
  haría que el daemon escribiese su pública (no es secreta) en otro fichero del usuario.
- **Lo que el daemon recuerda haber firmado se pierde al reiniciarlo.** «Una línea se firma una
  vez» aguanta con el libro vaciado mientras el proceso viva; tras un reinicio manda el libro.
- **Deuda a abrir al fijar la pública:** `hmac-ok-envio-retirar-tras-firmante`.

## Marcha atrás

| Qué falla | Qué se hace | Efecto |
|---|---|---|
| El firmante da problemas tras el paso 2 | revertir el commit 2b (`PUBLICA = None`) y fusionar | vuelve el HMAC en los árboles con el revert; por SSH vuelve a no abrir |
| Hay que apagar el daemon | `launchctl bootout gui/$(id -u)/com.btp.firmante` y borrar `~/Library/LaunchAgents/com.btp.firmante.plist` (singleton: de uno en uno y diciéndolo) | con `PUBLICA` fijada nadie abre nada (fail-closed): el hook avisa «el firmante NO RESPONDE» |
| Se sospecha de la clave privada | apagar el daemon, `/usr/bin/security delete-generic-password -s btp-firmante-ed25519` desde Terminal, volver a activar (crea otra) y repetir el paso 2 con la pública nueva | los permisos de la clave vieja dejan de valer al fusionar la pública nueva |
| Toda la pieza | `git revert` de los commits de la rama | se vuelve al estado anterior; la clave del Llavero queda sin uso |

Apagar el daemon con `PUBLICA` fijada deja a {{TITULAR}} sin poder enviar ni fusionar desde Polaris
hasta que vuelva: se avisa antes.

## Qué mirar si «no abre»

El aviso del hook distingue la causa; cada una se arregla distinto:

| Aviso | Causa | Arreglo |
|---|---|---|
| «el firmante NO RESPONDE» | daemon caído o sin activar; socket ausente | `python3 tools/firmante.py estado`; `launchctl print gui/$(id -u)/com.btp.firmante` |
| «NO HAY CLAVE de firma» | el Llavero no tiene `btp-firmante-ed25519` | paso 1 (crearla desde el Mac) |
| «el LLAVERO NO SE DEJA LEER» | la clave existe; el daemon no puede leerla | iniciar sesión en la pantalla del Mac; no crear otra |
| «no es la que está fijada» | la pública del daemon y `PUBLICA` no coinciden | repetir el paso 2 |
| «falta la librería `cryptography`» | el hook corrió con un python3 sin ella | `python3 -c "import cryptography"` con el python3 del PATH de la sesión; instalarla ahí |
| «HALT o un código rojo activo» | sistema parado | levantarlo es acto de {{TITULAR}} |
