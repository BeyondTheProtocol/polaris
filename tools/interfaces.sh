#!/bin/bash
# tools/interfaces.sh — arranca/para las DOS caras de Polaris (Open WebUI + LibreChat), endurecidas
# y hablando SOLO con el borde_gateway (:8799). Pensado para ser "a un clic": comprueba lo que hace
# falta y te dice en llano qué falta tú (lo humano), sin dejar nada a medias en silencio.
#
#   tools/interfaces.sh estado   # qué hay levantado y qué falta
#   tools/interfaces.sh up       # levanta ambas + relays Tailscale, e imprime las URLs del móvil
#   tools/interfaces.sh down     # las para (y los relays)
#
# Caja intercambiable: para usar una sola, edita docker-compose.yml o usa `up openwebui|librechat`.
set -euo pipefail
REPO="${BTP_REPO:-$HOME/claudecode}"
DIR="$REPO/tools/interfaces"
PY="$REPO/.venv/bin/python3"; [ -x "$PY" ] || PY="$(command -v python3 || echo python3)"
OWUI_PORT="${OWUI_PORT:-3030}"; LC_PORT="${LC_PORT:-3031}"
OWUI_TS="${OWUI_TS:-3040}";     LC_TS="${LC_TS:-3041}"   # puertos del relay Tailscale (móvil)
COLIMA_DNS="${BTP_COLIMA_DNS:-1.1.1.1 8.8.8.8}"          # DNS propio: colima no arranca tras NordVPN sin esto
VM_MTU="${BTP_VM_MTU:-1280}"                              # MTU de la VM: descargas grandes caen en agujero negro bajo VPN con 1500

c_red(){ printf '\033[31m%s\033[0m\n' "$*"; }
c_grn(){ printf '\033[32m%s\033[0m\n' "$*"; }
c_yel(){ printf '\033[33m%s\033[0m\n' "$*"; }

docker_ok(){ docker info >/dev/null 2>&1; }
compose_ok(){ docker compose version >/dev/null 2>&1; }
gateway_ok(){ lsof -nP -iTCP:8799 -sTCP:LISTEN >/dev/null 2>&1; }
have_colima(){ command -v colima >/dev/null 2>&1; }

# Si el runtime es colima (no Docker Desktop), arranca la VM con DNS propio y deja el MTU
# a 1280. Bajo NordVPN, sin DNS propio colima NO arranca, y con MTU 1500 las descargas
# grandes (imágenes de varios GB) caen en agujero negro. Inofensivo sin VPN. Idempotente.
# Ver [[reference-colima-docker-nordvpn-dns-mtu]]. Desactivable con BTP_SKIP_COLIMA=1.
ensure_colima(){
  [ "${BTP_SKIP_COLIMA:-0}" = "1" ] && return 0
  have_colima || return 0
  if ! docker_ok; then
    c_yel "Docker apagado → arranco colima (DNS propio + MTU $VM_MTU, para VPN)…"
    colima start --dns ${COLIMA_DNS} >/dev/null 2>&1 || { c_red "❌ no pude arrancar colima"; return 1; }
  fi
  colima status >/dev/null 2>&1 || return 0
  local cur; cur="$(colima ssh -- cat /sys/class/net/eth0/mtu 2>/dev/null || echo '')"
  if [ "$cur" != "$VM_MTU" ]; then
    colima ssh -- sudo ip link set dev eth0 mtu "$VM_MTU" 2>/dev/null \
      && c_grn "✅ MTU de la VM a $VM_MTU (descargas grandes bajo VPN)" \
      || c_yel "⚠️  no pude ajustar el MTU de la VM → descargas grandes podrían cortarse bajo VPN"
  fi
}

token(){
  local t="$REPO/tools/state/borde_gateway/token"
  [ -f "$t" ] && { cat "$t"; return 0; }
  security find-generic-password -s btp-gateway-token -w 2>/dev/null || true
}

prechequeo(){
  local fallo=0
  if docker_ok; then c_grn "✅ Docker corriendo"; else c_red "❌ Docker NO corre → abre Docker Desktop (o arranca tu runtime) y reintenta"; fallo=1; fi
  if compose_ok; then c_grn "✅ docker compose disponible"; else c_red "❌ docker compose no disponible (instala el plugin de compose o usa Docker Desktop)"; fallo=1; fi
  if gateway_ok; then c_grn "✅ Gateway escuchando en :8799"; else c_yel "⚠️  Gateway :8799 apagado → enciéndelo (launchd com.btp.borde-gateway) o las caras no tendrán cerebro"; fi
  [ -n "$(token)" ] && c_grn "✅ Token del gateway presente" || { c_red "❌ Falta el token del gateway (tools/state/borde_gateway/token o Llavero btp-gateway-token)"; fallo=1; }
  return $fallo
}

case "${1:-estado}" in
  estado)
    echo "— Estado de las caras de Polaris —"
    prechequeo || true
    if docker_ok; then
      echo "— contenedores —"; docker ps --filter "name=polaris-" --format '  {{.Names}}  {{.Status}}' 2>/dev/null || true
    fi
    echo "— acceso —"
    echo "  local:    http://127.0.0.1:$OWUI_PORT (Open WebUI) · http://127.0.0.1:$LC_PORT (LibreChat)"
    echo "  Tailscale: pon en marcha 'up' para abrir los relays del móvil ($OWUI_TS / $LC_TS)"
    ;;
  up)
    ensure_colima || true   # arranca colima con DNS+MTU correctos si el runtime es colima
    prechequeo || { c_red "Prerrequisitos sin cumplir (arriba). No arranco a medias."; exit 1; }
    TOK="$(token)"; export GATEWAY_TOKEN="$TOK"
    export OWUI_PORT LC_PORT
    cd "$DIR"
    SVC="${2:-}"   # opcional: openwebui|librechat para una sola
    case "$SVC" in
      openwebui) docker compose up -d open-webui ;;
      librechat) docker compose up -d librechat librechat-mongo ;;
      *)         docker compose up -d ;;
    esac
    c_grn "✅ contenedores arrancando"
    # Relays Tailscale (reusa el patrón ya probado del repo). Best-effort.
    if [ -f "$REPO/tools/preview_remoto.py" ]; then
      ("$PY" "$REPO/tools/preview_remoto.py" "$OWUI_PORT" "$OWUI_TS" >/dev/null 2>&1 &) || true
      ("$PY" "$REPO/tools/preview_remoto.py" "$LC_PORT" "$LC_TS" >/dev/null 2>&1 &) || true
      c_grn "✅ relays Tailscale lanzados (Open WebUI :$OWUI_TS · LibreChat :$LC_TS)"
    else
      c_yel "⚠️  no encuentro preview_remoto.py; abre el relay a mano para el móvil"
    fi
    echo ""
    c_grn "Listo. En el móvil (con Tailscale ON): http://<IP-Tailscale>:$OWUI_TS  y  :$LC_TS"
    echo "1ª vez: crea tu usuario local en cada una. En LibreChat elige el endpoint 'Polaris (gateway)'."
    ;;
  down)
    if docker_ok; then cd "$DIR"; docker compose down; c_grn "✅ contenedores parados"; fi
    pkill -f "preview_remoto.py $OWUI_PORT $OWUI_TS" 2>/dev/null || true
    pkill -f "preview_remoto.py $LC_PORT $LC_TS" 2>/dev/null || true
    c_grn "✅ relays parados"
    ;;
  *)
    echo "uso: tools/interfaces.sh estado|up|down  [up openwebui|librechat]"; exit 2 ;;
esac
