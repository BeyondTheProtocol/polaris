#!/bin/bash
# radar_ned_dia.sh — el ciclo DIARIO de vigilancia hacia NED (17-sep-2026).
#
# Dos pasos, uno gratis y otro caro, con un gate determinista en medio (misma filosofía que
# vega_gate: mirar es gratis, pensar se gatea):
#   1. tools/radar_ned_diario.py run   → barrido público sin LLM, $0. Escribe digest + avisa.
#   2. SI el barrido trajo algo nuevo en una diana de PRIORIDAD ALTA y RADAR_NED_TRIAJE=1,
#      arranca al comité médico para triarlo (verificación contra fuente primaria, CTIS con
#      navegador, contradicción explícita). Eso cuesta ~1-3 $ por pasada, por eso es opt-in.
#
# Kill-switch: run_agent.sh ya respeta .HALT; el paso 1 es local y también se abstiene si el
# borde está en HALT (borde.egress_cientifico devuelve deny → el barrido lo reporta y no sale).
set -uo pipefail
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
REPO="${BTP_REPO:-$HOME/claudecode}"
cd "$REPO" || exit 1
PY="$REPO/.venv/bin/python3"; [ -x "$PY" ] || PY="$(command -v python3 || echo python3)"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') radar NED diario ==="
"$PY" tools/radar_ned_diario.py run --dias "${RADAR_NED_DIAS:-3}"
rc_run=$?
[ "$rc_run" -ne 0 ] && echo "⚠️ el barrido devolvió rc=$rc_run"


# --- CTIS por el VPS de Hong Kong (18-sep-2026) ------------------------------------------------
# CTIS es una SPA sin API, pero SI se deja conducir por Chromium headless, y el VPS de Alibaba lo
# tiene instalado (/opt/radar-venv + /opt/radar_cn_vps.py). Asi el registro europeo entra en el
# barrido DIARIO y deja de depender de que haya sesion con navegador. ChiCTR y el CDE no: su WAF
# bloquea las IPs de datacenter (verificado el 18-sep), y siguen en el carril manual.
# Fail-soft en todo: si el VPS no responde, el barrido local ya ha corrido y no se pierde nada.
VPS="${BTP_CN_VPS:-root@47.243.53.161}"
CLAVE="${BTP_CN_VPS_KEY:-$HOME/.ssh/hk_lecheng}"
if [ -f "$CLAVE" ] && [ "${RADAR_CTIS:-1}" = "1" ]; then
  TMP_CTIS="${TMPDIR:-/tmp}/ctis_$(date +%Y%m%d).json"
  if ssh -i "$CLAVE" -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no "$VPS" \
       "/opt/radar-venv/bin/python /opt/radar_cn_vps.py ctis 'breast cancer'" > "$TMP_CTIS" 2>/dev/null \
     && [ -s "$TMP_CTIS" ]; then
    "$PY" tools/radar_navegador.py ingerir --fuente ctis --fichero "$TMP_CTIS" --terminos "breast cancer" \
      || echo "⚠️ la ingesta de CTIS falló (el barrido local sigue siendo válido)"
  else
    echo "⚠️ CTIS por el VPS no respondió; queda para el carril manual"
  fi
fi

# --- ICTRP (espejo OMS de ChiCTR) por el VPS de Hong Kong (20-sep-2026) --------------------------
# ChiCTR bloquea las IPs de datacenter (405). Su espejo en la OMS (trialsearch.who.int) no, e importó
# el fichero de ChiCTR el 14-sep-2026. Una sola llamada corre la batería entera en el VPS
# (radar_cn_vps.py ictrp perfil): 'breast cancer' x China x últimos RADAR_ICTRP_DIAS días, filtrado
# por título contra su perfil, más los 9 pares condición x intervención (neoantigen, personalized
# vaccine, mRNA vaccine, TIL, CDK2, FGFR, {{DIANA2}}, TROP2, ADC). Medido el 20-sep: ~40 s, 9 ítems.
# Fail-soft como CTIS. ChinaXiv NO entra (rendimiento oncológico ~0, ver radar_ned_diario.py).
if [ -f "$CLAVE" ] && [ "${RADAR_ICTRP:-1}" = "1" ]; then
  TMP_ICTRP="${TMPDIR:-/tmp}/ictrp_$(date +%Y%m%d).json"
  if ssh -i "$CLAVE" -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no "$VPS" \
       "timeout 560 /opt/radar-venv/bin/python /opt/radar_cn_vps.py ictrp perfil --dias ${RADAR_ICTRP_DIAS:-14}" \
       > "$TMP_ICTRP" 2>/dev/null \
     && [ -s "$TMP_ICTRP" ] && ! grep -q '^{"error"' "$TMP_ICTRP"; then
    "$PY" tools/radar_navegador.py ingerir --fuente ictrp --fichero "$TMP_ICTRP" \
      --terminos "breast cancer x China x ${RADAR_ICTRP_DIAS:-14}d,pares de intervención" \
      || echo "⚠️ la ingesta de ICTRP falló (el barrido local sigue siendo válido)"
  else
    echo "⚠️ ICTRP por el VPS no respondió o devolvió error; ChiCTR queda SIN cubrir hoy (no se finge)"
    [ -s "$TMP_ICTRP" ] && head -c 300 "$TMP_ICTRP" && echo
  fi
fi

if [ "${RADAR_NED_TRIAJE:-0}" != "1" ]; then
  echo "triaje LLM desactivado (RADAR_NED_TRIAJE!=1) → me quedo en el barrido gratis."
  exit 0
fi

if "$PY" tools/radar_ned_diario.py check; then
  echo "--- gate abierto: arranco el comité para triar ---"
  BTP_AGENT=comite-medico BTP_MODEL=fable MURO_PROFILE=privileged \
  "$REPO/tools/run_agent.sh" "Tria el barrido de hoy del radar NED diario. Lee el digest más reciente en '00_FUENTE-DE-VERDAD/04 · IA/Radar/Radar-NED-diario-*.md' y el estado en tools/state/radar_ned/ultimo.json. Para cada novedad de las dianas de PRIORIDAD ALTA: di qué mecanismo toca, si aplica al perfil real de {{TITULAR}} (revisión central {{CENTRO}} 19-may-2026: luminal B HER2-, componente neuroendocrino focal/heterogéneo, Ki-67 40 %, TILs < 5 %; FGF19 amplificado pero FGFR4 receptor NUNCA medido; quimio-naïve y ADC-naïve; mielotoxicidad G3-G4 previa; y FoundationOne CDx del bloque de HUESO de {{CIUDAD}}, salida 19-ago-2026: CCND1/FGF19/FGF3/FGF4 amplificados x37, FGFR1/NSD3/ZNF703 x33, ERBB2-ESR1-PIK3CA-PTEN-BRCA1/2 SIN alteracion reportable, HRD negativa, MS-Stable, TMB 4 Muts/Mb, MUTYH G382D al 43 % pendiente de test germinal. No des por hecho ESR1 mutado ni RB1 perdido: el informe de hueso no los sostiene) y pon el contraargumento al lado, no después. Etiqueta el nivel de evidencia de cada hallazgo (in vitro, animal, caso, cohorte, fase 1, RCT, guía) y marca como SIN VERIFICAR todo lo que no hayas abierto. Descarta sin piedad lo que no cambie nada. Al terminar, escribe el triaje junto al digest y ejecuta 'python3 tools/radar_ned_diario.py sellar'. NO cierres ningun lead de la cola de verificacion: en modo autonomo el muro te deniega WebFetch y los MCP, asi que NO puedes abrir ninguna fuente primaria. La cola solo se cierra abriendo el DOI o la ficha en una sesion, con veredicto. Si tu triaje no puede verificar algo, dilo y dejalo en cola. Si no hay nada que mueva el mapa, dilo en una línea y no rellenes."
  echo "--- comité terminado (rc=$?) ---"
else
  echo "gate cerrado: nada nuevo en diana de prioridad alta → no gasto LLM."
fi
