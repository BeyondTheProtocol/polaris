# Activación 24/7 de Polaris — checklist (hacer JUNTOS)

> Estado: **PARCIALMENTE ACTIVADO.** ✅ Activas (20-jun-2026): las 3 rutinas de X — `com.btp.x-mentions` (8:40), `com.btp.x-radar` (lun 9:00), `com.btp.x-centinela` (8:45). El RESTO sigue **preparado, sin activar** (pasos de abajo, contigo: piden contraseña/QR/OK).

## 0 · Caja despierta (energía)
- [ ] **(sudo, tú)** Que no duerma y se reencienda tras un apagón:
  ```
  sudo pmset -a sleep 0 disksleep 0 womp 1 autorestart 1
  ```
- [ ] **caffeinate** (cinturón extra, ya preparado en `com.btp.caffeinate.plist`):
  ```
  cp ~/claudecode/tools/launchd/com.btp.caffeinate.plist ~/Library/LaunchAgents/
  launchctl load -w ~/Library/LaunchAgents/com.btp.caffeinate.plist
  ```
- [x] ~~Inicio de sesión automático~~ → **OMITIDO por el muro** (decidido 20/6/26): con **FileVault ON** (disco cifrado = datos clínicos protegidos) macOS **no permite** auto-login. Se mantiene FileVault. La resiliencia ante apagón NO se logra abriendo el cifrado, sino con un **SAI/UPS** (añadido a la lista de compras). Tras un apagón largo, desbloquear a mano una vez.
- [ ] **(GUI, tú)** Energía → "Iniciar tras un corte de corriente" **ON** (la cajita se enciende sola; con FileVault esperará al desbloqueo).

## 1 · MCPs nuevos (BioMCP + cBioPortal)
- [ ] **(tú)** Reinicia Claude en Polaris → al abrir el proyecto te pedirá **habilitar 2 servidores de proyecto** (`biomcp`, `cbioportal`) definidos en `~/claudecode/.mcp.json` → **Aprobar**.
- Verás herramientas nuevas de variantes/ensayos (BioMCP) y frecuencia de mutaciones (cBioPortal). **Muro genómico: nunca se les pasa tu VCF/HLA/PII** (solo consultas públicas).

## 2 · WhatsApp (Fase 1)
- [ ] **(tú)** Abre **WhatsApp** (ya instalada en `/Applications`) → *Dispositivos vinculados* → escanea el **QR** con el móvil (una sola vez).
- [ ] Tras vincular, `wa_tracker.py` ya puede leer la base local y transcribir tus notas de voz. (Si Bash no puede leerla: Claude necesita *Acceso a Disco Completo* en Polaris.)

## 3 · Rutinas (decidir local vs nube y cablear JUNTOS)
> Ninguna creada aún (caja nueva). Las **locales** (tocan ficheros/WhatsApp de Polaris) van por **launchd**; las de **agente** (razonan con Claude) pueden ir por **launchd con `claude -p` headless** o por el **agendador en la nube** de Claude. Decidimos y probamos `claude -p` una vez.

| Rutina | Hora | Tipo | Comando previsto |
|---|---|---|---|
| wa_tracker (transcribe audios) | 8:07 | local | `~/claudecode/.venv/bin/python ~/claudecode/tools/wa_tracker.py` |
| enviar HOY a Telegram | 8:12 | local | `~/claudecode/.venv/bin/python ~/claudecode/tools/enviar_hoy.py` |
| HOY (componer el parte) | ~8:10 | agente | `claude -p` (agente orquestador) → escribe `Gestion/HOY.md` |
| auto-mejora | 5:08 | agente | `claude -p` (agente `auto-mejora`) |
| monitor prensa | 8:42 | agente | `claude -p` (agente `prensa`) |
| radar literatura | día 1 | agente | `claude -p` (agente `comite-medico`/radar) |
| **menciones X (Fase 1, pública)** | 8:40 | local | `~/claudecode/.venv/bin/python ~/claudecode/tools/x_mentions.py` — plist listo: `com.btp.x-mentions.plist` |
| **buzón X (Fase 2: guardados+DMs)** | tras menciones | agente | `claude -p` (agente `x-inbox`, Haiku) — **necesita sesión de X iniciada + Chrome MCP**; cablear JUNTOS. Solo lectura |
| **radar X (5 modos)** | lunes 9:00 | local | `~/claudecode/.venv/bin/python ~/claudecode/tools/x_radar.py --hours 168` — plist listo: `com.btp.x-radar.plist`. Modos: expertos·ensayos → `comite-medico` · prensa → `prensa` · financiacion → `finanzas-transparencia` · comunidad → `comunidad`. Congresos (#ASCO/#AACR/#ESMO/#SITC) = correr extra en sus ventanas |
| **centinela del muro X** | diario 8:45 | local | `~/claudecode/.venv/bin/python ~/claudecode/tools/x_centinela.py --hours 24` — plist listo: `com.btp.x-centinela.plist`. Dueño: `verificacion`. Para FILTRACIÓN: crear `tools/.centinela_secrets.json` (gitignored) con los términos protegidos (plantilla `.example.json`). Defensivo, solo lee |
| **Instagram (publicaciones+comentarios+menciones)** | 9:20·14:20·20:20 | local | `~/claudecode/.venv/bin/python ~/claudecode/tools/instagram.py` — plist listo: `com.btp.instagram.plist` (**preparado, SIN cargar**). Solo lectura (Graph API de Meta). **Requiere token en el Llavero `btp-instagram-api`** (cuenta Profesional + Página de FB + app de Meta; guía en `04 · IA/Monitorizacion-Redes-Accesos.md`). Digest → `_PRIVADO_INSTAGRAM/` (gitignored). DMs no accesibles por API → a mano/Chrome |
| **Cronista — captura (capa 1)** | diario 23:40 | local | `/usr/bin/python3 ~/claudecode/tools/cronica.py capturar` — plist listo: `com.btp.cronica.plist` (**preparado, SIN cargar**). Determinista, **sin LLM, egress-cero**. Registra los eventos del día en la §Bitácora de `CRONICA.md` + registro máquina + estado. Que algo esté en la bitácora = **registrado** |
| **Cronista — narrar (capa 2)** | domingo 5:20 | agente | `run_agent.sh` (agente `periodista`, Opus) — plist listo: `com.btp.cronica-narrar.plist` (**preparado, SIN cargar**). Teje la §Bitácora en las FASES, bien contada, y la retira. Que algo esté arriba = **narrado**. El muro manda |

### Activar la rutina de Instagram (cuando ella quiera, tras poner el token)
```
cp ~/claudecode/tools/launchd/com.btp.instagram.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.btp.instagram.plist
```
Antes de cargarla, prueba que el token funciona: `~/claudecode/.venv/bin/python ~/claudecode/tools/instagram.py --whoami` (debe imprimir tu IG user-id). Para parar: `launchctl unload -w ~/Library/LaunchAgents/com.btp.instagram.plist`.

### Activar el Cronista automático (que se va registrando todo, solo)
Dos rutinas: captura diaria (determinista) + pase narrativo semanal (el agente `periodista`).
```
# Capa 1 — captura diaria (23:40), sin LLM
cp ~/claudecode/tools/launchd/com.btp.cronica.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.btp.cronica.plist
# Capa 2 — pase narrativo semanal (domingo 5:20)
cp ~/claudecode/tools/launchd/com.btp.cronica-narrar.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.btp.cronica-narrar.plist
```
Comprobar el estado cuando quieras: `python3 ~/claudecode/tools/cronica.py estado` (dice «al día hasta DD/MM» y si se ha atrasado). Para parar cualquiera: `launchctl unload -w ~/Library/LaunchAgents/com.btp.cronica.plist`.

- [ ] Probar headless: `/Applications/Claude.app/Contents/MacOS/claude -p "ping"` (confirmar que corre sin GUI).
- [ ] Generar un plist por rutina (`StartCalendarInterval`, que dispara aunque la hora pasara dormida) + `launchctl load`.

## Puente de calendario (Google Calendar → vigía) — `com.btp.calendar-sync` (preparado, SIN cargar)
> Lee tu Google Calendar en SOLO LECTURA con una **Service Account** y mete las próximas citas en el vigía (`seguimiento.json`). Datos **solo en local**; el espejo de Notion las excluye; los títulos (pueden ser citas médicas) **no salen por Telegram** (`privado=True`). Corre a las **8:05**, antes del parte HOY. Detalle: plan `keen-scribbling-fox`.

**Pasos previos (manos de {{TITULAR}}, una vez):**
1. **Google Cloud Console** (cuenta de {{TITULAR}}): proyecto (puede reusar `btp-stitch`) → **habilitar "Google Calendar API"** → **Service Account** → **crear clave JSON** y descargarla. Copia el **email** de la Service Account.
2. **Google Calendar** → ⚙️ Configuración → tu calendario → **Compartir con personas concretas** → añadir ese email con **"Ver todos los detalles del evento"** (solo lectura).
3. Guardar la clave: `bash ~/claudecode/tools/setup_keychain.sh` → en "Service Account de Google" arrastra el `.json`.
4. Probar a demanda: `~/claudecode/.venv/bin/python ~/claudecode/tools/calendar_sync.py` (debe decir "ok: N cita(s)…") y `python3 tools/seguimiento.py revisar` (deben verse las citas).

**Activar la rutina (cuando ella quiera, tras los pasos previos):**
```
cp ~/claudecode/tools/launchd/com.btp.calendar-sync.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.btp.calendar-sync.plist
```
Para parar: `launchctl unload -w ~/Library/LaunchAgents/com.btp.calendar-sync.plist`.
- Calendarios a leer: `tools/state/calendar_sync.config.json` (añade más compartiéndolos con la SA y poniendo su ID).
- Selftest sin red (sanidad del código): `~/claudecode/.venv/bin/python ~/claudecode/tools/calendar_sync.py --selftest`.

## Vega (tu guardiana): asistente proactiva diaria · `com.btp.asistente` (ACTIVA desde 22/6/26)
> Hace el barrido diario de hilos abiertos (impacto-NED) y deja su sección para el parte de HOY. Corre a las **7:55** (antes de `hoy-compose` a las 8:10). Agente `asistente`, modelo Sonnet. No envía nada por Telegram ella (lo hace `enviar-hoy`); si un hilo amenaza el goal, dispara código rojo.

**Activar (cuando {{TITULAR}} quiera):**
```
cp ~/claudecode/tools/launchd/com.btp.asistente.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.btp.asistente.plist
```
Para parar: `launchctl unload -w ~/Library/LaunchAgents/com.btp.asistente.plist`.

### Modo follonera — recordatorios de tarde/noche · `com.btp.asistente-recordatorio` (ACTIVA desde 22/6/26)
> Vega da la lata con lo DATADO (citas/plazos): pasadas a las **14:30 y 19:30**. Si hay algo con fecha hoy/mañana sin hacer, manda un recordatorio corto por Telegram; si no, se calla. Mismo agente `asistente`, Sonnet, dentro del muro (solo avisa, nunca hacia fuera). Respeta el silencio nocturno salvo código rojo.
> Apagar: `launchctl unload -w ~/Library/LaunchAgents/com.btp.asistente-recordatorio.plist`.

## Antesala (ver/probar features como en producción, en privado) · `com.btp.staging`
> Sirve, por **Tailscale** (solo tus dispositivos), una **simulación de producción** de cualquier
> feature antes de mandarla a producción. Privada: nada sale a internet. **No despliega** (el
> "mandar a producción" lleva al PR; mergeas tú/{{CONTACTO}}). Sustituye al viejo `com.btp.preview-web`
> (relay del dev server), que queda **obsoleto** (no lo cargues a la vez: usan la misma idea de relay).
>
> **No hace falta launchd para usarla puntualmente:** `staging.sh web <rama>` ya la levanta. El `on`
> solo sirve para dejarla viva 24/7 (tras reinicios). URL fija para {{TITULAR}}: **http://100.114.113.73:3010**

**Uso normal (lo hago yo al construir una feature):**
```
bash ~/claudecode/tools/staging.sh web <rama> [--modo prod|dev]   # web helptitular.com
bash ~/claudecode/tools/staging.sh port <N> --nombre "X"          # un tool con interfaz web
bash ~/claudecode/tools/staging.sh estado                          # ver qué hay cargado
```

**Dejarla viva 24/7 (gate de {{TITULAR}}, cuando quieras):**
```
bash ~/claudecode/tools/staging.sh on    # carga com.btp.staging en launchd
bash ~/claudecode/tools/staging.sh off   # para todo y descarga
```
Requiere Tailscale activo en Polaris y en tu dispositivo. Logs en `tools/launchd/logs/staging.*`.

## Vega recoge tareas de WhatsApp/voz (`com.btp.wa-tareas`)

Pre-filtro determinista que estaciona candidatos de los volcados de WhatsApp (`wa_tracker`) en la
bandeja `tools/state/tareas/wa_candidatos.json`; **Vega (el agente `asistente`) los JUZGA** en su
barrido diario y solo asciende las tareas reales a `/calma` (como `por_confirmar` + `privado`). El
determinista sobre-captura a propósito; la precisión la pone Vega. Corre a las **08:15** (tras
`wa-tracker` 08:07). `RunAtLoad=false`.

**Doble gate (apagado por defecto):**
1. **Cargar el plist** en launchd (una vez):
   ```
   launchctl bootstrap gui/$(id -u) ~/claudecode/tools/launchd/com.btp.wa-tareas.plist
   ```
2. **Abrir el gate** (esto es lo que lo hace realmente activo; sin esto el script sale sin hacer nada):
   ```
   touch ~/claudecode/.claude/hooks/.wa_cosecha_on     # o exportar BTP_WA_COSECHA_OK=1
   ```
   Para **apagarlo** sin descargar el plist: `rm ~/claudecode/.claude/hooks/.wa_cosecha_on`.

**Antes de encender — pasada supervisada (recomendado):**
```
python3 ~/claudecode/tools/cosecha_whatsapp.py --since 7        # SECO: mira qué candidatos saldrían
```
Respeta `.HALT` (no estaciona con HALT activo). Corre en la máquina donde vive WhatsApp Desktop
(misma BD que `wa_tracker`). Logs en `tools/launchd/logs/wa-tareas.*`.

## Notas
- venvs: tools (whisper/PDF) = `~/claudecode/.venv` (Py 3.9) · MCP bio = `.venv-biomcp` y `.venv-cbioportal` (Py 3.12).
- launchd `StartCalendarInterval` recupera la ejecución si la hora pasó mientras estaba apagada/dormida (más robusto que cron ante apagones).
