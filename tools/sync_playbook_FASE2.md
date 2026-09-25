# sync_playbook — Bootstrap + Fase 2 (triggers)

Estado (11-jul): **bootstrap HECHO (Air→mini), tool desplegada al mini, heartbeat de la Air
y pull-al-abrir ACTIVADOS.** Falta solo el heartbeat mini→Air (gated por Remote Login del Air).

## 0) Bootstrap (arranque) — HECHO
Ejecutado 11-jul: `--elige local --apply` desde la Air (Air→mini). Verificado antes con diff de
solo lectura (el mini no tenía nada único, solo versiones viejas). Ambas copias idénticas,
baseline fijado, backup del mini a salvo. Referencia de cómo se hace, por si hay que rehacerlo:
La 1ª sync no tiene baseline y las dos copias ya divergen, así que la guarda de conflicto se
niega a elegir (por diseño). {{TITULAR}} decide qué copia es la buena y se declara:

```bash
# ver qué haría, sin escribir (recomendado antes de aplicar):
python3 tools/sync_playbook.py --elige local          # dry
# aplicar el bootstrap elegido:
python3 tools/sync_playbook.py --elige local --apply   # "local" = la copia de ESTA máquina
python3 tools/sync_playbook.py --elige remoto --apply  # "remoto" = la copia del otro equipo
```

- `--elige` es la **única** vía de pisar sin baseline. Hace backup del destino, respeta la guarda
  anti-corrupto (no propaga un fichero < 200 B) y deja el baseline fijado.
- **Decisión abierta (⚠️ ojo con el `.HALT` del 11-jul):** el `.HALT` de topología dice
  "mini = primario 24/7 (canónico)". Pero en frescura de CONTENIDO la copia de la Air era la
  más nueva (9-jul vs 27-jun del mini). "Primario de ejecución" ≠ "copia con el contenido bueno".
  → Confirmar con {{TITULAR}} qué copia gana ANTES de `--apply`.

## Fase 2 — triggers

### A) Heartbeat — vive en la AIR (`tools/launchd/com.btp.sync-playbook-air.plist`) ✅ ACTIVADO
Realidad: **el Remote Login de la Air está OFF**, así que el mini NO alcanza a la Air, pero la
Air sí alcanza al mini (polaris, 24/7). Por eso el heartbeat útil corre en la AIR: cada 30 min
cose con el mini en ambos sentidos de propagación. Cargado 11-jul:
```bash
cp tools/launchd/com.btp.sync-playbook-air.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.btp.sync-playbook-air.plist
```
El heartbeat mini→Air (`com.btp.sync-playbook.plist`, desplegado al mini pero SIN cargar) solo
tiene sentido si algún día enciendes **Remote Login en la Air** + creas un alias ssh `air` en el
mini. Hoy no hace falta: los triggers de la Air ya cubren las dos direcciones.

### B) Pull-al-abrir — `tools/sync_al_abrir.sh` ✅ ACTIVADO (en la Air)
Cose el playbook antes de que el orquestador lo lea, throttled (`--si-viejo 20`) y **fail-open**
(detachado, nunca bloquea la sesión). Enganchado en `.claude/hooks/session_start.sh` (una línea
en background). Variables: `SYNC_REMOTO` (def `polaris`), `SYNC_MIN` (def 20).

## Orden de activación (ya respetado)
1. **Bootstrap primero** (hecho) — con baseline, los triggers no alertan en falso.
2. Heartbeat de la Air + pull-al-abrir (hechos).

## Fase 3 — nudge de frescura ✅ ACTIVADO (en el mini)
`sync_playbook.py --nudge-frescura DIAS --apply`: si el CONTENIDO de ESTADO-ACTUAL.md lleva
> DIAS sin cambiar (mismo sha256 persistente), avisa a {{TITULAR}} por su canal (Telegram) para que
lo refresque. NO autogenera nada clínico. Anti-spam: como mucho un aviso al día; el reloj se
reinicia solo cuando el contenido cambia. Corre en el MINI (24/7) vía
`com.btp.sync-playbook-nudge.plist` (rutas /Users/polaris, diario 09:00, umbral 7 días).

## Pendiente
- (opcional) Heartbeat mini→Air: encender Remote Login en la Air + alias ssh `air` en el mini.
  Hoy redundante: los triggers de la Air ya cubren ambas direcciones.
