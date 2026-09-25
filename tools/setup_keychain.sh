#!/usr/bin/env bash
# setup_keychain.sh — guarda en el Llavero de macOS las claves de Beyond the Protocol.
#
# Ejecútalo TÚ en tu terminal:   bash ~/claudecode/tools/setup_keychain.sh
# Cada clave se lee SIN eco; no se imprime, no queda en el historial del shell.
# Flujo: rota la clave en su proveedor → cuando este script te la pida, pégala → Enter.
# Deja una vacía (solo Enter) para SALTARLA.
set -euo pipefail

add_secret() {
  local service="$1" prompt="$2" value
  printf '\n%s\n  (vacío + Enter = saltar)\n  > ' "$prompt"
  IFS= read -rs value; echo
  if [ -z "$value" ]; then echo "  · saltada ($service)"; return; fi
  security add-generic-password -U -a "$USER" -s "$service" -w "$value"
  echo "  ✔ guardada en el Llavero como '$service'"
}

# Para secretos multilínea (p. ej. el JSON de una Service Account): se guarda el
# CONTENIDO de un fichero. Se pide la RUTA (eso sí puede verse; el contenido no).
add_secret_file() {
  local service="$1" prompt="$2" path
  printf '\n%s\n  (vacío + Enter = saltar; puedes ARRASTRAR el fichero al terminal)\n  > ' "$prompt"
  IFS= read -r path
  # quita comillas/espacios que añade el arrastrar-soltar
  path="${path%\"}"; path="${path#\"}"; path="${path%\'}"; path="${path#\'}"
  path="$(printf '%s' "$path" | sed -e 's/^ *//' -e 's/ *$//')"
  if [ -z "$path" ]; then echo "  · saltada ($service)"; return; fi
  if [ ! -f "$path" ]; then echo "  ✗ no existe el fichero: $path"; return; fi
  # Minificar a UNA línea antes de guardar: el Llavero devuelve los secretos con
  # saltos de línea como volcado hex (0x7b…), y eso rompe el json.loads al leerlos
  # (pasó con la Service Account del calendario, 22/6/26). JSON en una línea = texto
  # limpio de vuelta. El validador de arriba ya garantizó que es JSON parseable.
  local min
  if ! min="$(python3 -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1])),separators=(',',':')))" "$path" 2>/dev/null)"; then
    echo "  ✗ no pude minificar el JSON — no lo guardo"; return
  fi
  security add-generic-password -U -a "$USER" -s "$service" -w "$min"
  echo "  ✔ guardada en el Llavero como '$service' (contenido del fichero, minificado)"
}

echo "== Llavero · Beyond the Protocol =="
add_secret "btp-grok-api"        "Clave Grok/xAI NUEVA (empieza por xai-):"
add_secret "btp-telegram-token"  "Token de Telegram NUEVO (de @BotFather, forma 123456:ABC...):"
add_secret "btp-telegram-chatid" "Chat ID de Telegram (número; suele NO cambiar al rotar el token):"
add_secret "btp-umami-api"       "Clave Umami NUEVA:"
add_secret "btp-perplexity-api"  "Clave Perplexity NUEVA (empieza por pplx-):"
add_secret "btp-youtube-api"     "Clave de YouTube Data API v3 (empieza por AIza...):"
add_secret "btp-anthropic-api"   "Clave Anthropic API para el CLI 24/7 (sk-ant-...), solo si eliges esa vía:"
add_secret_file "btp-gcal-sa-key" "Fichero .json de la Service Account de Google (para leer tu calendario):"
add_secret "btp-fugu-api"        "Clave de Sakana Fugu (API de pago, para consultas puntuales aisladas):"
add_secret "btp-elicit-api"      "Clave de Elicit (SOLO si tu plan es Pro+; gratis en Pro, se crea en ajustes de la cuenta):"

echo
echo "== Google Drive OAuth (tools/drive.py) =="
add_secret "btp-gdrive-client-id"     "Google Drive OAuth Client ID (forma: xxxxxxxx.apps.googleusercontent.com):"
add_secret "btp-gdrive-client-secret" "Google Drive OAuth Client Secret:"
echo "  (el refresh token se guarda automáticamente con: python3 tools/drive.py auth)"

echo
echo "Hecho. Comprueba una con:"
echo "  security find-generic-password -s btp-grok-api -w"
echo "(imprime la clave guardada; si no, revisa que no la saltaste)."
