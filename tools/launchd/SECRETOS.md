# com.btp.secretos: activación, prueba por SSH, cierre de deudas y marcha atrás

El broker (`tools/secretos_broker.py`) vive en la sesión gráfica, que es donde el Llavero se deja leer. Desde ahí sirve una **lista cerrada** de claves a las sesiones de Claude Code que entran por SSH, donde `security -w` da rc=36. Plan aprobado por {{TITULAR}} el 17-oct-2026.

En la rama **no se encendió nada ni se cerró ninguna deuda**. Lo de esta página lo hace la sesión que cierra, desde casa base, con la firma de {{TITULAR}}.

## Qué hay al terminar cada paso

| Estado | Qué leen las sesiones por SSH | Cómo se reconoce |
|---|---|---|
| 0 · rama fusionada, daemon sin cargar | nada (como hoy) | `secretos_broker.py estado` dice «NO existe» |
| 1 · daemon cargado | las claves de `PERMITIDAS` | `estado` dice «responde: sí» |

En el paso 0 el cliente (`_secrets._keychain`) no encuentra el socket y cae a `security`, exactamente como antes.

## 1 · Activar (gate de {{TITULAR}}; es uno de los tres singletons)

```bash
python3 tools/launchd/registro.py estado
python3 tools/activar_daemon.py com.btp.secretos --dry
python3 tools/activar_daemon.py com.btp.secretos
python3 tools/secretos_broker.py estado
tail -5 tools/launchd/logs/secretos.out tools/launchd/logs/secretos.err
```

El plist solo se diferencia del de `com.btp.firmante`, que ya funciona en gui/501, en una clave: `LimitLoadToSessionType=Aqua`. Esa clave todavía no se ha probado con una carga real de launchd. Si `bootstrap` falla por ella, quítala: el firmante corre en gui/501 sin ella y lee el Llavero.

## 2 · Probarlo de punta a punta desde una sesión por SSH

```bash
python3 tools/secretos_broker.py estado     # responde: sí
python3 tools/umami.py 1                     # cifras, no «Falta la clave»
python3 tools/correo_imap.py --help          # y una lectura real del buzón
```

Ninguna de estas órdenes imprime un secreto. Para ver quién pidió qué, mira `tools/state/_secrets/broker_log.jsonl`: guarda servicio, estado, pid y proceso, y nunca el valor.

## 3 · Solo si el paso 2 sale bien: cerrar las deudas

```bash
python3 tools/deuda.py cerrar llavero_bloqueado --test tests/test_secretos_broker.py
python3 tools/deuda.py cerrar llavero-imap-bloqueado --test tests/test_secretos_broker.py
python3 tools/deuda.py cerrar correo_imap_login:titular_mgp --test tests/test_secretos_broker.py
```

`umami-clave-vacia-llavero` y `umami-api-key-invalida-401` **siguen abiertas**. Los 401 del 14-sep, el 20-sep y el 10-oct no los explica el Llavero.

## Ampliar la lista

La lista es `_secrets.BROKER_PERMITIDAS`: una constante del código, no configuración. El cliente ni siquiera pregunta por las claves que no están en ella, así que no deja ruido en el log. Una clave nueva entra con un commit y el OK de {{TITULAR}}. Las de `NUNCA` no entran ni aunque se añadan: el test lo impide.

Del tramo B (APIs de pago y Telegram), {{TITULAR}} abrió la opción 1 el 17-oct-26:
- **Entran:** las claves de modelo cuyo gasto llega a `cost_guard`: anthropic, openai, gemini, grok, glm, nan, nvidia, openrouter y perplexity. Además entra ElevenLabs.
  - Claude se corta en `ia.py` antes de gastar.
  - Las otras ocho apuntan el gasto después de cada llamada. Avisan al pasar el tope, pero no lo cortan.
  - ElevenLabs gasta de la cuota de su plan, sin contador nuestro.
- **Siguen fuera:** Telegram, anthropic-prestada, serpapi, scaleway, fugu, typesafe y elicit.

## Límites conocidos

- Tras un reinicio con FileVault, el broker contesta `ilegible` hasta que {{TITULAR}} entra en la sesión gráfica.
- Si el Llavero se bloquea solo (por tiempo o al dormir), contesta `ilegible`. Hoy está en «no-timeout».
- Un `.py` escrito a mano puede hablar con el socket, igual que hoy puede llamar a `security` en una sesión local. Desde Bash, el muro deniega nombrar la ruta (`_secrets`).

## Marcha atrás

```bash
launchctl bootout gui/$(id -u)/com.btp.secretos
rm ~/Library/LaunchAgents/com.btp.secretos.plist
```

Sin el socket, `_secrets` vuelve solo al comportamiento de antes. No hay nada más que deshacer.
