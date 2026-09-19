#!/bin/bash
# mini.sh — trabaja DENTRO del mini desde el Air, sin copias que diverjan.
#
# QUÉ RESUELVE (25-jul-26, lo pidió {{TITULAR}})
#   «Que el Air cada vez que quiera hacer algo se conecte al mini y se actualice, pero
#   virtualmente, sin que se quede en memoria si no hace falta.»
#
#   Hasta hoy el Air tenía una COPIA del repo (~2.8 GB) que había que sincronizar, y dos
#   copias es justo lo que produce las divergencias. Esto es el patrón cliente ligero:
#   el estado y el cómputo viven en el mini, el portátil pone pantalla y teclado. Nada
#   que sincronizar, porque no hay dos sitios.
#
#   La sesión vive en `tmux` DENTRO del mini: cierras la tapa, la abres mañana, y sigue
#   exactamente donde estaba. Eso es lo que hace que «se caiga la conexión» deje de
#   importar — que fue justo el motivo por el que {{TITULAR}} descartó lo remoto el 30/6.
#
# SE EJECUTA EN EL AIR (aquí, en el mini, no tiene sentido y sale avisando).
#
# Uso:
#   tools/mini.sh            # entra (o vuelve) a la sesión de trabajo
#   tools/mini.sh --nueva    # una sesión aparte, sin tocar la de siempre
#   tools/mini.sh --estado   # ¿está el mini? ¿qué sesiones hay abiertas?
set -uo pipefail

REMOTO="${MINI_REMOTO:-polaris}"
SESION="${MINI_SESION:-titular}"
# Dónde aterriza la sesión al crearla: el repo, no el home (si no, `cd` cada vez).
REPO_REMOTO="${MINI_REPO:-~/claudecode}"
MODO="${1:-}"

rojo()  { printf '\033[31m%s\033[0m\n' "$1"; }
verde() { printf '\033[32m%s\033[0m\n' "$1"; }
gris()  { printf '\033[2m%s\033[0m\n'  "$1"; }

# Guarda: en el propio mini esto no pinta nada.
if [ "$(hostname -s 2>/dev/null)" = "Polaris" ]; then
  rojo "Ya estás EN el mini: aquí no hay a dónde conectarse."
  exit 0
fi

# ¿Se llega? ConnectTimeout corto: mejor un «no está» en 4s que un cuelgue.
alcanzable() {
  ssh -o BatchMode=yes -o ConnectTimeout=4 -o StrictHostKeyChecking=accept-new \
      "$REMOTO" 'exit 0' >/dev/null 2>&1
}

# La causa nº1 (verificada el 27-jul: el Air llevaba un día fuera de la tailnet) es que
# Tailscale se ha desconectado aquí. Se comprueba ANTES de culpar a la red o al mini,
# para no dar tres causas posibles cuando se sabe cuál es.
TS="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
if [ -x "$TS" ] && ! "$TS" status >/dev/null 2>&1; then
  rojo "Tailscale esta desconectado EN ESTE MAC."
  gris "  Sin el no hay camino hasta el mini."
  gris "  Abre Tailscale (icono de la barra de arriba) y dale a Connect."
  gris "  A veces se desloguea solo despues de unos dias: si lo pide, entra otra vez."
  exit 1
fi

if ! alcanzable; then
  rojo "No llego al mini ($REMOTO)."
  gris "  Comprueba, por este orden:"
  gris "   1. Tailscale encendido en el Air (y en el mini)"
  gris "   2. que el mini esté despierto: no se conecta a un Mac dormido"
  gris "   3. \`ssh $REMOTO\` a pelo, para ver el error de verdad"
  echo
  gris "  Mientras tanto puedes trabajar con la copia local del Air, que para eso está:"
  gris "     cd ~/claudecode && claude"
  gris "  Ojo: lo que hagas ahí habrá que fusionarlo luego (tools/deploy_ff.sh)."
  exit 1
fi

if [ "$MODO" = "--estado" ]; then
  verde "✅ El mini responde."
  echo "— sesiones abiertas allí —"
  ssh "$REMOTO" 'tmux ls 2>/dev/null || echo "  (ninguna)"'
  exit 0
fi

# mosh aguanta cortes de red, cambios de wifi y la tapa cerrada; ssh no. Se usa si está
# en LAS DOS máquinas (si falta en una, no sirve de nada y se cae a ssh sin drama).
#
# OJO CON EL PATH: mosh arranca `mosh-server` por ssh NO interactivo, y en esa clase de
# sesión zsh solo lee `~/.zshenv` — que en el mini no existe, así que /opt/homebrew/bin
# NO está en el PATH y saldría el clásico «mosh-server: command not found». Por eso se
# busca la ruta ABSOLUTA y se le pasa con --server, en vez de tocar la configuración
# global del mini para arreglar un caso de uso.
#
# LO MISMO LE PASA A TMUX, y esto sí lo pagó {{TITULAR}} el 25-jul: `ssh -t polaris "tmux …"`
# es un comando remoto, no un login shell, así que zsh tampoco lee `.zprofile` y `tmux`
# tampoco estaba en el PATH. La conexión moría con «tmux: command not found» y volvía al
# Air sin explicar nada. Se resuelve igual: ruta absoluta, buscada en el remoto.
ruta_remota() {
  ssh -o BatchMode=yes -o ConnectTimeout=4 "$REMOTO" '
    for p in /opt/homebrew/bin/'"$1"' /usr/local/bin/'"$1"' /usr/bin/'"$1"'; do
      [ -x "$p" ] && { echo "$p"; exit 0; }
    done
    command -v '"$1"' 2>/dev/null' 2>/dev/null | head -1
}

TMUX_REMOTO="$(ruta_remota tmux)"
if [ -z "$TMUX_REMOTO" ]; then
  rojo "El mini responde, pero no encuentro tmux allí."
  gris "  Instálalo en el mini con:  brew install tmux"
  gris "  (sin tmux se puede entrar igual, pero la sesión NO sobrevive a cerrar la tapa)"
  gris "  Entrar sin sesión persistente:  ssh $REMOTO"
  exit 1
fi

CLIENTE="ssh"
SERVIDOR_MOSH=""
if command -v mosh >/dev/null 2>&1; then
  SERVIDOR_MOSH="$(ruta_remota mosh-server)"
  [ -n "$SERVIDOR_MOSH" ] && CLIENTE="mosh"
fi

if [ "$MODO" = "--nueva" ]; then
  SESION="${SESION}-$(date +%H%M%S)"
  gris "abriendo una sesión aparte: $SESION"
fi

# OJO CON LAS LLAVES: aquí la variable iba pegada a unas comillas angulares de adorno.
# Con `set -u`, bash se come esos bytes multibyte como parte del NOMBRE de la variable,
# no la encuentra y aborta: "SESION?: unbound variable". El script moría JUSTO antes de
# conectar, y por eso desde el mini no se veía ni rastro (25-jul, se lo comió {{TITULAR}}).
# Regla: toda variable con ${} y separada de cualquier carácter que no sea ASCII.
gris "conectando por ${CLIENTE} - sesion tmux: ${SESION}"
gris "para salir dejandola VIVA: Ctrl+B y luego D   (con 'exit' la cierras del todo)"
gris "si Claude pide login: es el Llavero, que por ssh viene bloqueado. Una vez:"
gris "   security unlock-keychain ~/Library/Keychains/login.keychain-db   (o /login)"
echo

# `new -A` = engancha si existe, la crea si no. Sin sorpresas y sin duplicar sesiones.
if [ "$CLIENTE" = "mosh" ]; then
  exec mosh --server="$SERVIDOR_MOSH" "$REMOTO" -- "$TMUX_REMOTO" new -A -s "$SESION" -c "$REPO_REMOTO"
else
  exec ssh -t "$REMOTO" "'$TMUX_REMOTO' new -A -s '$SESION' -c '$REPO_REMOTO'"
fi
