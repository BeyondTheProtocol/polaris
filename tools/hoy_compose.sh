#!/bin/bash
# hoy_compose.sh — wrapper del compositor diario del parte HOY.md.
#
# Arregla dos cosas que hacían que HOY.md se quedara congelado (clavado en "21-jun"):
#   1) FECHA REAL: inyecta la fecha del reloj del sistema en el prompt, en vez de dejar
#      que el orquestador la derive de tools/state/cumbre.json (que se congela).
#   2) ESCRITURA EXPLÍCITA: le ordena ESCRIBIR el parte con la herramienta Write (antes el
#      prompt decía "compón ... en HOY.md" y el agente solo lo redactaba y lo devolvía,
#      sin tocar el fichero → no había ninguna tool_use de Write en los logs).
#
# Y una tercera (17-sep-26), la que dejó el parte clavado 4 días sin que nadie se enterara:
#   3) NO SALIR EN VERDE SIN PARTE. Tras el reinicio del 13-sep el Llavero quedó bloqueado,
#      `run_agent.sh` no pudo leer btp-anthropic-api, imprimió «Falta btp-anthropic-api en el
#      Llavero» y salió con 0. launchd lo apuntó como pasada correcta y healthcheck no vio
#      ningún daemon fallando: solo un parte viejo, que es un síntoma mucho más silencioso.
#      Ahora se compara el mtime del parte antes y después: si no se reescribió, esto sale con
#      código != 0 y el fallo se ve donde se tiene que ver.
#      Porqué-no `exec`: reemplazaba el proceso y no quedaba nadie para comprobar el resultado.
#
# Lo invoca el plist com.btp.hoy-compose (que ya exporta BTP_AGENT=orquestador,
# BTP_MODEL=sonnet, MURO_PROFILE=privileged). Sin `exec`, el entorno se hereda igual.
set -euo pipefail
# BTP_REPO manda (CONTRIBUTING: sin rutas absolutas); casa base por defecto.
REPO="${BTP_REPO:-$HOME/claudecode}"
HOY_REL="00_FUENTE-DE-VERDAD/Gestion/HOY.md"
HOY_ABS="$REPO/$HOY_REL"
FECHA="$(date +%Y-%m-%d)"   # fiable: reloj del sistema, NO cumbre.json

PROMPT="Hoy es ${FECHA} (dato fiable del reloj del sistema; formatéalo en español en la cabecera del parte, p. ej. «🌅 HOY · <día de la semana> <DD> <mes>»). Compón el parte de HOY de {{TITULAR}} siguiendo tu rutina diaria de regenerar Gestion/HOY.md. **MUY IMPORTANTE: ESCRÍBELO con la herramienta Write en ${HOY_REL}** (sobrescribe el fichero entero con el parte nuevo). NO te limites a devolver el texto en tu respuesta: si no lo escribes a disco con Write, NO cuenta. NO derives la fecha de cumbre.json ni copies el HOY.md viejo."

antes="$(stat -f %m "$HOY_ABS" 2>/dev/null || echo 0)"

salida="$(mktemp -t hoy-compose)"
rc=0
"$REPO/tools/run_agent.sh" "$PROMPT" 2>&1 | tee "$salida" || rc=$?

despues="$(stat -f %m "$HOY_ABS" 2>/dev/null || echo 0)"

if [ "$despues" = "$antes" ]; then
    echo "hoy-compose: el agente terminó (rc=$rc) pero NO reescribió $HOY_REL." >&2
    # La causa se LEE de la salida del agente, no se supone. El 18-sep-26 la primera versión de
    # este aviso afirmaba «Llavero bloqueado» como causa típica, alguien lo creyó, y la causa real
    # era el saldo de API agotado. Un aviso que adivina la causa dirige el arreglo al sitio
    # equivocado; mejor citar lo que dijo la API y, si no la reconoce, no inventar ninguna.
    if grep -q "Credit balance is too low" "$salida" 2>/dev/null; then
        echo "hoy-compose: la API respondió «Credit balance is too low» → NO hay saldo en la" >&2
        echo "             cuenta de Anthropic. Recarga el prepago; no es cosa de este Mac." >&2
    elif grep -qi "Falta btp-anthropic-api en el Llavero" "$salida" 2>/dev/null; then
        echo "hoy-compose: run_agent no pudo leer btp-anthropic-api del Llavero. Si esto sale" >&2
        echo "             desde un LaunchAgent (sesión Aqua), el Llavero está bloqueado de verdad:" >&2
        echo "             security unlock-keychain ~/Library/Keychains/login.keychain-db" >&2
    else
        echo "hoy-compose: causa no reconocida. Últimas líneas del agente:" >&2
        tail -5 "$salida" >&2
    fi
    rm -f "$salida"
    exit 1
fi

rm -f "$salida"
exit "$rc"
