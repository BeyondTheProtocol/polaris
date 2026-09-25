# Las caras de Polaris (interfaz = caja intercambiable)

Dos UIs de chat self-hosted (**Open WebUI** y **LibreChat**) que hablan **solo** con el `borde_gateway`
(:8799) → `ia.ask` → borde. Son la "app" para manejar Polaris desde el móvil sin abrir la consola de
dev de Claude, y **agnósticas**: eliges el cerebro desde la propia UI. Plan: `~/.claude/plans/curious-gliding-gosling.md`.

## Cómo se usa
```
tools/interfaces.sh estado   # qué hay y qué falta
tools/interfaces.sh up       # levanta ambas + relays Tailscale (imprime las URLs del móvil)
tools/interfaces.sh down     # las para
tools/interfaces.sh up openwebui   # solo una
```

## Prerrequisitos (el paso humano)
1. **Docker corriendo** (Docker Desktop abierto, o tu runtime). Hoy el daemon estaba apagado.
2. **Gateway encendido** (:8799): launchd `com.btp.borde-gateway` (tu gate) o arrancarlo a demanda.
3. **Tailscale ON** para verlo en el móvil.
El script comprueba los tres y **no arranca a medias**: si falta algo, te lo dice en llano.

## Banco de pruebas (decidir por uso)
Corre las dos ~2 semanas y quédate con la que de verdad abres; apaga la otra. El gasto de tokens
NO se duplica (es el del cerebro/gateway, común); lo que se duplica es solo el mantenimiento.

## Muro (por qué es seguro)
- Cada UI apunta su única salida a un LLM al **gateway**; ese es el camino, y pasa por el borde.
- Bind **solo loopback**; el móvil entra por el **relay Tailscale**, no exponiendo el contenedor.
- **Telemetría y funciones agénticas/web propias de la UI: apagadas** (la inteligencia y las
  herramientas las pone Polaris, no la cara).
- Pendiente de verificar EN VIVO (Docker estaba apagado al montarlo): que `host.docker.internal:8799`
  alcanza el gateway en loopback bajo Docker Desktop; si no, ajustar el bind del gateway o el host del compose.
