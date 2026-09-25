---
paths:
  - "tools/launchd/**"
  - "tools/activar_daemon.py"
  - "**/*.plist"
  - "tools/healthcheck.py"
  - "tools/vigia.py"
---

# Encender daemons 24/7: regla anti-colisión

Un daemon launchd se enciende **SOLO desde casa base** (nunca desde un worktree o rama de feature) y **SOLO** vía `tools/activar_daemon.py <label>`. Es la única vía sancionada: valida label único, no-worktree y concern único (`tools/launchd/REGISTRO.json`), y serializa con `_lock`.

**Antes de crear un plist nuevo:** `python3 tools/launchd/registro.py estado` (¿qué trabajo ya está cubierto?).

**Rutinas activas** (las que `CLAUDE.md` prohíbe duplicar): HOY 8:12 · Radar día 1 · auto-mejora lun/mié/vie/dom ~5:08 · prensa diario · WhatsApp ~8:07.
**Antes de construir en un subsistema:** `python3 tools/ramas.py en-vuelo <patron>` (¿otra rama ya lo toca?).

Activar o recargar launchd es uno de los **3 singletons** que sí se serializan entre sesiones paralelas. Encender y fusionar siguen siendo **gate de {{TITULAR}}**.

## Salud del sistema: auto-detección y resolución (regla de {{TITULAR}}, 26/6/26)

Un problema operativo (daemon caído, relay o puerto no alcanzable, job en `failed/`, capacidad degradada) **NO espera a que {{TITULAR}} lo note** ni se queda en un chip pasivo. El sistema lo **detecta**, intenta un **autofix acotado y seguro** (p. ej. `kickstart`) y, si no puede, **lanza investigación y AVISA** en llano (`salida.py`, respetando HALT + anti-spam).

Vale también para mí en cualquier sesión: si veo un fallo de fontanería, lo **investigo y resuelvo** (o lo dejo a un clic + aviso). **No lo aparco.** Es el hermano operativo del 🔴 código rojo (que es para amenazas al GOAL; esto es salud del sistema).

**✋ El acuse (3/7/26):** al ver una alerta de salud, lo PRIMERO es
```bash
python3 tools/salud.py ack <clave> "<qué voy a hacer>"
```
{{TITULAR}} ve *«✋ Visto, en ello»* en vez del grito, y no hay re-nag mientras trabajo. **El acuse no sustituye el arreglo**, que sigue siendo obligatorio. Al cerrar: `python3 tools/salud.py resuelto <clave>`. La `<clave>` es la misma que muestra el aviso o `salud.py list`.

**🔧 Matiz (3/7/26): el acuse ya es automático en el propio aviso.** `healthcheck._emitir_si_cambia` lo hace sola para cada alerta nueva, ANTES de mandarla: si hay autofix aplicable lo intenta ya y el aviso sale *«🔧 Detecté X y me pongo a arreglarlo…»*; si no, sale *«🔧 Detecté X, lo estoy mirando.»*. El primer mensaje que le llega a {{TITULAR}} nunca es un grito seco.

Detalle: memoria [[feedback-auto-detectar-resolver-problemas]] y [[project-acuse-alertas-salud]].
