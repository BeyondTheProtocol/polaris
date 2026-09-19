# Polaris en el MacBook Air — runbook (1 página)

> ## ⚡ Desde el 25-jul-26 hay TRES vías. El Air ya no es un ordenador con sus cosas: es una ventana al mini
>
> **0 · LA PANTALLA (la de {{TITULAR}}, para trabajar con la APP de Claude).** Ves el escritorio del
> mini y usas la app tal cual, con sus sesiones abiertas. Nada que sincronizar, porque no estás
> usando el Air: estás usando el mini a través del cristal.
>
> En el Air: **Finder → `Cmd+K` → `vnc://polaris.taild7f51c.ts.net`** (usuario y contraseña del
> mini; marca «recordar»). No hay que instalar nada: Compartir pantalla ya está activo en el mini.
>
> Ventaja añadida: como es la sesión gráfica de verdad, **no da el problema del Llavero** que sí
> aparece por ssh (ver más abajo). Con Tailscale funciona también fuera de casa.
>
> **1 · LA TERMINAL (para algo rápido, o si la conexión va justa).** Más ligera que la pantalla.
> ```bash
> tools/mini.sh          # entra o vuelve a tu sesión de trabajo del mini
> tools/mini.sh --estado # ¿está despierto? ¿qué sesiones hay abiertas?
> ```
> La sesión vive en `tmux` en el mini: cierras la tapa, la abres mañana y sigue igual. No hay
> dos copias, así que **no hay nada que sincronizar y nada que pueda diverger**. Lo pidió {{TITULAR}}:
> *«que se conecte al mini y se actualice, pero virtualmente»*.
>
> La primera vez, Claude puede pedir login: por ssh el Llavero viene bloqueado y el token del
> fichero caducó el 13-jul. Se arregla una vez con
> `security unlock-keychain ~/Library/Keychains/login.keychain-db` (o `/login`).
>
> **2 · OFFLINE (lo de abajo).** La copia local del Air, para cuando de verdad no hay red:
> avión, hospital sin cobertura, mini dormido. Se mantiene sola al día (`tools/ff_al_abrir.sh`,
> fast-forward al abrir sesión). Lo que trabajes ahí hay que fusionarlo luego con
> `tools/deploy_ff.sh from-polaris` / `to-polaris`.
>
> **Cuál usar:** con la app de Claude → **la pantalla**. Algo rápido por consola o red justa →
> **la terminal**. Sin red de ninguna clase → **offline**. La copia local es el bote salvavidas,
> no el barco.

Llevar el gabinete FÍSICO en el Air, offline, sin depender de conexión. El mini se queda en casa
encendido como base 24/7. En el Air usas todo a mano; los automatismos van apagados para no duplicar.

## En el MINI (yo te guío)
1. **Bundle.** Elige canal:
   - AirDrop (rápido, sin cacharros): `tools/migrar_a_air.sh ~/Desktop/Polaris-Air nucleo` → ~3 GB (sin DICOM; el RAG sigue por índice).
   - SSD/cable (todo, incluida imagen): `tools/migrar_a_air.sh /Volumes/TU_SSD completo` → ~15 GB.
2. **Secretos (gateado).** `tools/migrar_secretos_air.sh ~/Desktop/Polaris-Air` → pide una passphrase tuya, deja `secretos-polaris.enc`. (FileVault del Air ON.)
3. Lleva al Air la carpeta `claudecode/` **y** `secretos-polaris.enc` (AirDrop o SSD).

## En el AIR
4. Pon la carpeta donde sea y corre:  `bash claudecode/tools/setup_polaris_air.sh`
   - coloca el repo en `~/claudecode`, reescribe rutas a tu usuario, recrea el venv (1 vez con internet),
     importa los secretos (passphrase), corre la batería de tests. Daemons quedan **apagados**.
5. **Smoke** (que confirma que funciona):
   ```
   cd ~/claudecode
   python3 tools/seguimiento.py        # tablero
   python3 tools/correo_imap.py | head # tu Gmail (usa secretos)
   python3 tools/kb.py ask "…"         # RAG
   ```
   y abre **Claude Code en `~/claudecode`** para el gabinete completo.

## Mientras viajas
- Úsalo a mano. Los daemons 24/7 están **off** (el mini los corre en casa).
- Si quieres que el Air sea el primario 24/7: `tools/polaris-daemons on` (y apaga los del mini para no duplicar).

## Al volver
- Mini y Air tendrán estado distinto (los dos vivieron). Se consolida con `tools/reconciliar_estado.py`
  y eligiendo qué `tools/state` es la verdad. Nada se pierde; solo hay que unir.

## Qué NO viaja igual
- Extras de ML local (voz Chatterbox, imagen, Fugu, Open WebUI, NVIDIA local): pueden no arrancar en un
  Air. El núcleo (gabinete, correo, tablero, agentes, MCP, RAG) sí.
- Algún MCP autenticado por claude.ai puede pedir re-login una vez en el Air.
