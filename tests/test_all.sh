#!/bin/bash
# test_all.sh — corre toda la batería del sistema (muro + lazo P1) en verde o falla.
# COPIA FIJA (10-oct-26). bash lee un script POR TROZOS, a medida que lo ejecuta: si alguien lo reescribe EN SITIO
# (un editor, `cat >`, un script; git no, git crea un inodo nuevo) con la pasada en marcha, bash sigue leyendo desde un
# desplazamiento que ya no corresponde y ejecuta dos veces un trozo (resultados y rojos DUPLICADOS) o se salta otro.
# Así que al arrancar se copia a un temporal propio de la ejecución y se re-ejecuta desde ahí, conservando ROOT; los
# trabajadores del modo paralelo usan la MISMA copia. Si no se puede copiar, sigue desde el original (como antes).
# Las variables de la copia no bajan a las baterías (una que lance otra suite no se confunde). Test: test_all_copia_fija.py.
_ROOT_FIJA="$BTP_TEST_ALL_ROOT"; _COPIA="$BTP_TEST_ALL_COPIA"; _DUENA="$BTP_TEST_ALL_DUENA"
unset BTP_TEST_ALL_ROOT BTP_TEST_ALL_COPIA BTP_TEST_ALL_DUENA
if [ -n "$_ROOT_FIJA" ]; then ROOT="$_ROOT_FIJA"; else ROOT="$(cd "$(dirname "$0")/.." && pwd)"; fi
if [ -z "$_COPIA" ] && [ -f "$0" ]; then
  _c=$(mktemp "${TMPDIR:-/tmp}/test_all.XXXXXX" 2>/dev/null) && cp "$0" "$_c" 2>/dev/null &&
    BTP_TEST_ALL_ROOT="$ROOT" BTP_TEST_ALL_COPIA="$_c" BTP_TEST_ALL_DUENA=1 exec "${BASH:-bash}" "$_c" "$@"
  [ -n "$_c" ] && rm -f "$_c"
fi
[ -n "$_DUENA" ] && trap 'rm -f "$_COPIA"' EXIT
PY=/usr/bin/python3; [ -x "$PY" ] || PY=python3
fail=0
skip=0
# Los logs de los rojos, en una carpeta POR EJECUCIÓN (25-sep-26, deuda
# test-all-log-rojo-tmp-compartido): con /tmp/rojo-<test>.log fijo, las sesiones en paralelo se
# pisaban el log y el de una rama enseñaba el fallo de otra. En el CI hay un runner por ejecución
# y el workflow público y healthcheck los buscan en /tmp: allí se queda. Test: test_all_rojo_dir.py.
if [ -n "$BTP_ROJO_DIR" ]; then ROJO_DIR="$BTP_ROJO_DIR"; mkdir -p "$ROJO_DIR"
elif [ -n "$CI" ]; then ROJO_DIR=/tmp
else _t="${TMPDIR:-/tmp}"; ROJO_DIR=$(mktemp -d "${_t%/}/rojo.XXXXXX"); fi
ROJO_DIR="${ROJO_DIR%/}"
# Toda la suite corre con stdin CERRADO (25-sep-26, deuda test-session-start-topologia-cuelgue-
# transitorio): lanzada desde el Bash de un agente, su stdin es un pipe que nunca se cierra, y cada
# test que lanzaba algo que lee stdin sin darle `input` lo heredaba y esperaba para siempre.
# Test: test_all_stdin_cerrado.py.
exec </dev/null
# 19-sep-2026 — BTP_PORTABLE=1 (lo usa el CI del repo público en Linux): salta las baterías
# que solo pueden pasar en la casa base: Llavero de macOS, plists de launchd, el binario
# `xurl`, el panel técnico de la anatomía. No son opcionales, es que allí no hay con qué
# correrlas. La batería completa sigue siendo la de casa base.
# 20-sep-2026: entra `test_healthcheck_halt_inactividad.py`. Sus 3 comprobaciones del roster
# llaman a `hc._check_roster_daemons()`, que sin `launchctl` no puede listar nada: en Linux
# daban 10 OK y 3 fallos FIJOS, y un rojo permanente convierte el CI en decoración. Las otras
# 10 sí pasarían allí; se pierden a cambio de que el semáforo vuelva a significar algo.
SOLO_CASA_BASE="test_xurl.py test_x_guardados_enriquecido.py test_llavero_mudo.py
test_bucles_colgados.py test_plists_home.py test_anatomia_tecnica.py test_auto_mejora_turnos.py
test_digest.sh test_muro_costura_rm.py test_coste_repo.py test_healthcheck_halt_inactividad.py
test_chrome_headless_cierra.py"
# `$(echo …)` colapsa los saltos de línea de la lista: sin eso, las baterías que caen al
# principio o al final de cada línea no casaban y seguían corriendo (4 rojos en el primer CI).
# Cronómetro por batería (26-sep-26): una medición de 7 días dio ~35 h-sesión bloqueadas en
# 473 pasadas de test_all (mediana 4,0 min). Al final se imprimen las 10 más lentas: el dato para
# saber qué test adelgazar. No cambia ni el resultado ni el código de salida.
# 10-oct-26: el cronómetro llevaba DESAPARECIDO desde una fusión (la línea `_tiempos+=` se perdió y
# el bloque de las 10 más lentas imprimía vacío sin que nadie lo notara). Vuelve, con test
# (test_all_paralelo.py).
_tiempos=""
_salta() { [ -n "$BTP_PORTABLE" ] || return 1
           case " $(echo $SOLO_CASA_BASE) " in *" $1 "*) return 0;; esac; return 1; }

# _corre <run|py> <batería> — UNA batería con todo lo suyo: cabecera, última línea y log del rojo
# (en `py`, stdout+stderr: `unittest` escribe AHÍ el fallo, 22-sep-26; en `run`, ambos mezclados).
# El nombre del test que se pone ROJO se DICE (27/7/26) y su log se guarda para mirarlo después.
# Devuelve 0 verde · 1 rojo · 2 saltada (rc 77 o solo-casa-base). Los temporales son ÚNICOS
# (mktemp): antes eran /tmp/t.$$, que dos trabajos del mismo proceso se habrían pisado.
_corre() {
  local _k="$1" _n="$2" _o _e _rc
  _salta "$_n" && { echo "── $_n ── (solo casa base)"; return 2; }
  echo "── $_n ──"
  _o=$(mktemp "${TMPDIR:-/tmp}/t.XXXXXX"); _e="$_o.err"
  if [ "$_k" = "py" ]; then "$PY" "$ROOT/tests/$_n" >"$_o" 2>"$_e"; else bash "$ROOT/tests/$_n" >"$_o" 2>&1; fi
  _rc=$?
  tail -1 "$_o"
  if [ $_rc -eq 77 ]; then rm -f "$_o" "$_e"; return 2; fi
  if [ $_rc -ne 0 ]; then
    if [ "$_k" = "py" ]; then cat "$_o" "$_e" > "$ROJO_DIR/rojo-$_n.log" 2>/dev/null
    else cp "$_o" "$ROJO_DIR/rojo-$_n.log" 2>/dev/null; fi
    echo "  🔴 ROJO: $_n (rc=$_rc · log: $ROJO_DIR/rojo-$_n.log)"
    rm -f "$_o" "$_e"; return 1
  fi
  rm -f "$_o" "$_e"; return 0
}

# Modo trabajador (lo lanza _vacia_cola, nunca a mano): corre UNA batería y deja su bloque de
# salida, su código y su tiempo en la carpeta de la ejecución. El padre decide qué cuenta como rojo.
if [ "$1" = "--worker" ]; then
  # BTP_JOBS NO baja a las baterías: test_all_rojo_dir.py y test_all_stdin_cerrado.py ejecutan la
  # cabecera de este script y, heredándolo, ENCOLABAN en vez de correr (2 falsos rojos en la 1ª pasada).
  unset BTP_JOBS
  _w="$2"; _i="$3"; _t0=$SECONDS
  _corre "$4" "$5" > "$_w/$_i.out" 2>&1; _r=$?
  echo "$((SECONDS-_t0))" > "$_w/$_i.t"; echo "$_r" > "$_w/$_i.rc"
  [ "$_r" -eq 1 ] && echo "🔴 (en vivo) $5" >&2
  exit 0
fi

# Paralelismo (10-oct-26, prototipo): BTP_JOBS=N o --jobs N. Por defecto 1 = serie, idéntico a
# siempre. Con N>1 las baterías se ENCOLAN y se corren N a la vez, salvo las de SOLO_SERIE, que van
# después una a una con la máquina libre. La salida sale en el MISMO orden de siempre, al final.
JOBS="${BTP_JOBS:-1}"; _COLA=()
# Comparten estado real (git del repo, Llavero, procesos del sistema, relojes, ficheros fijos) o miden
# tiempos/carga: en paralelo darían rojos intermitentes. Lista medida, no supuesta (ver la nota del plan).
SOLO_SERIE="test_fuga.sh test_halt.sh test_anatomia.py test_anatomia_al_dia.py test_contrato_asiento.py
test_cosecha_panel.py test_githooks_base.py test_healthcheck.py test_cascada_clinica.py"
# · fuga/halt: el cierre de sesión ya midió (26-sep) que test_fuga.sh se pone rojo con 4 a la vez.
# · anatomia, contrato_asiento, cosecha_panel, githooks_base, healthcheck: SOLO corren de verdad en casa
#   base; en un worktree saltan (rc 77), así que NINGUNA pasada de prueba las vio en paralelo. Sin
#   evidencia, serie. Si la rutina nocturna las pasa 10 noches en paralelo sin rojo, se sacan de aquí.
# · cascada_clinica: crea 00_FUENTE-DE-VERDAD/ en la raíz y eso decide si saltan otras (acopla el orden).
# Las más lentas medidas el 10-oct-26 (≥ 40 s, ~1 núcleo cada una): arrancan primero en el pool.
LARGAS="test_laminillas.py test_laminillas_n1.py test_laminillas_piloto_b.py test_anatomia_tecnica.py
test_anatomia_push.py test_laminillas_piloto_congela.py test_laminillas_f3.py test_visor3d_modelo_port.py
test_dispatcher.sh test_visor3d_cuelgue.py test_laminillas_capas.py test_laminillas_1bis.py
test_correos_publicables.py test_deid_eval.py test_laminillas_valis.py test_panel_vision.py"
_lanza() {   # <run|py> <batería>
  _fuera "$2" && return 0
  if [ "$JOBS" -gt 1 ]; then _COLA+=("$1 $2"); return 0; fi
  local _t0=$SECONDS _r
  _corre "$1" "$2"; _r=$?
  _tiempos+="$((SECONDS-_t0)) $2"$'\n'
  [ $_r -eq 1 ] && fail=$((fail+1))
  [ $_r -eq 2 ] && skip=$((skip+1))
  return 0
}
run()   { _lanza run "$1"; }
runpy() { _lanza py "$1"; }

# Corre lo encolado (solo con JOBS>1) y lo reproduce en orden: salida, rojos, saltos y tiempos
# acaban donde acabarían en serie. FAIL-CLOSED: un trabajo que murió sin dejar código cuenta ROJO.
_vacia_cola() {
  [ "$JOBS" -gt 1 ] && [ "${#_COLA[@]}" -gt 0 ] || return 0
  local _w _i=0 _l _n _t _r _pared=$SECONDS _suma=0
  _w=$(mktemp -d "${TMPDIR:-/tmp}/par.XXXXXX"); : > "$_w/pool"; : > "$_w/serie"
  # Las gordas, primero (LPT): una de 440 s que arranca la última alarga la pasada entera 440 s más.
  for _l in "${_COLA[@]}"; do
    _i=$((_i+1)); _n="${_l#* }"
    case " $(echo $SOLO_SERIE) " in *" $_n "*) echo "$_i $_l" >> "$_w/serie";; *)
      case " $(echo $LARGAS) " in *" $_n "*) echo "$_i $_l" >> "$_w/gordas";; *) echo "$_i $_l" >> "$_w/pool";; esac;; esac
  done
  if [ -s "$_w/gordas" ]; then cat "$_w/gordas" "$_w/pool" > "$_w/pool2"; mv "$_w/pool2" "$_w/pool"; fi
  local _yo="${_COPIA:-$ROOT/tests/test_all.sh}"      # los trabajadores corren la MISMA copia fija, no el fichero vivo
  BTP_TEST_ALL_ROOT="$ROOT" BTP_TEST_ALL_COPIA="$_yo" BTP_ROJO_DIR="$ROJO_DIR" xargs -L1 -P "$JOBS" bash "$_yo" --worker "$_w" < "$_w/pool"
  BTP_TEST_ALL_ROOT="$ROOT" BTP_TEST_ALL_COPIA="$_yo" BTP_ROJO_DIR="$ROJO_DIR" xargs -L1 -P 1 bash "$_yo" --worker "$_w" < "$_w/serie"
  _i=0
  for _l in "${_COLA[@]}"; do
    _i=$((_i+1)); _n="${_l#* }"
    if [ ! -f "$_w/$_i.rc" ]; then
      echo "── $_n ──"; echo "  🔴 ROJO: $_n (sin resultado: el trabajo murió antes de acabar)"; fail=$((fail+1)); continue
    fi
    cat "$_w/$_i.out"; _r=$(cat "$_w/$_i.rc"); _t=$(cat "$_w/$_i.t")
    _tiempos+="$_t $_n"$'\n'; _suma=$((_suma+_t))
    [ "$_r" -eq 1 ] && fail=$((fail+1))
    [ "$_r" -eq 2 ] && skip=$((skip+1))
  done
  echo "⚙️  paralelo: $JOBS trabajos · ${#_COLA[@]} baterías · pared $((SECONDS-_pared))s frente a ${_suma}s de suma en serie"
  case "$_w" in */par.*) rm -r "$_w";; esac
}

# --cambiados (26-sep-26): solo las baterías que tocan los ficheros cambiados de la rama
# (tools/tests_afectados.py). Es para comprobar sobre la marcha sin esperar 10+ minutos, con
# varias sesiones a la vez en la misma máquina. NO vale para fusionar a casa base: el resumen lo
# dice y no escribe «TODO EN VERDE». Test: test_tests_afectados.py.
CAMBIADOS=""; SELECCION=""; fuera=0
_pide_cambiados=""; _pide_puerta=""; PUERTA=""
while [ $# -gt 0 ]; do
  case "$1" in
    --cambiados) _pide_cambiados=1;;
    --puerta) _pide_puerta=1;;
    --jobs|-j) JOBS="$2"; shift;;
    --jobs=*) JOBS="${1#--jobs=}";;
  esac
  shift
done
case "$JOBS" in ''|*[!0-9]*) JOBS=1;; esac
[ "$JOBS" -ge 1 ] || JOBS=1
# FALLA CERRADA (10-oct-26, revisión de consejero-arquitectura): el selector tiene que contestar
# limpio (rc 0 y una primera línea que entendemos). Si revienta, no existe o imprime vacío, NO se
# concluye «no hay nada que correr»: va la suite completa. Test: test_all_puerta.py.
if [ -n "$_pide_cambiados" ]; then
  _sel=$("$PY" "$ROOT/tools/tests_afectados.py" 2>/dev/null); _rcsel=$?
  _p1=$(echo "$_sel" | head -1)
  if [ $_rcsel -eq 0 ] && [ "$_p1" = "NINGUNA" ]; then
    CAMBIADOS=1; SELECCION=" "
    echo "⚡ MODO --cambiados: ninguna batería afectada (solo documentación). Antes de fusionar, la suite completa."
  elif [ $_rcsel -eq 0 ] && [ -n "$_p1" ] && [ "$_p1" != "TODO" ]; then
    CAMBIADOS=1; SELECCION=" $(echo $_sel) "
    echo "⚡ MODO --cambiados: $(echo "$_sel" | grep -c .) batería(s) afectada(s). Antes de fusionar, la suite completa."
  elif [ $_rcsel -eq 0 ] && [ "$_p1" = "TODO" ]; then
    echo "⚡ --cambiados: se tocó test_all.sh, así que va la suite completa."
  else
    echo "⚡ --cambiados: el selector no contestó limpio (rc=$_rcsel), así que va la suite completa."
  fi
fi
# --puerta (10-oct-26, propuesta): la puerta de fusión por impacto (tools/tests_afectados.py --puerta).
# COMPLETA (se tocó un hook, el runner o una tool del muro) → corre la suite entera, como siempre.
# RAPIDA → las afectadas + el núcleo fijo del muro; el resumen NO dice «TODO EN VERDE».
if [ -n "$_pide_puerta" ]; then
  _sel=$("$PY" "$ROOT/tools/tests_afectados.py" --puerta 2>/dev/null); _rcsel=$?
  _p1=$(echo "$_sel" | head -1)
  _nsel=$(echo "$_sel" | grep -vcE '^(RAPIDA|COMPLETA|#)')
  if [ $_rcsel -eq 0 ] && [ "$_p1" = "RAPIDA" ] && [ "$_nsel" -gt 0 ]; then
    CAMBIADOS=1; PUERTA=1; SELECCION=" $(echo "$_sel" | grep -vE '^(RAPIDA|#)' | tr '\n' ' ') "
    echo "🚪 PUERTA: RAPIDA · $_nsel batería(s) (afectadas + núcleo del muro)."
  elif [ $_rcsel -eq 0 ] && [ "$_p1" = "COMPLETA" ]; then
    echo "🚪 PUERTA: COMPLETA. Motivo:"; echo "$_sel" | grep '^#' | sed 's/^# /   /'
  else
    echo "🚪 PUERTA: COMPLETA. Motivo:"
    echo "   el selector no contestó limpio (rc=$_rcsel, primera línea «${_p1}», $_nsel baterías): ante la duda, la completa."
  fi
fi
_fuera() { [ -n "$CAMBIADOS" ] || return 1; case "$SELECCION" in *" $1 "*) return 1;; esac; fuera=$((fuera+1)); return 0; }

run   test_fuga.sh
run   test_halt.sh
runpy test_muro_fase0.py
runpy test_muro_clase_plantar.py
runpy test_token_rotacion.py
runpy test_terminos_carril.py
runpy test_f1_opus_supervision.py
runpy test_cola.py
runpy test_cola_diario.py
runpy test_entorno_cola_aislada.py
runpy test_tools_no_sombrea_stdlib.py
runpy test_healthcheck_drift_plists.py
runpy test_prueba_entregable.py
runpy test_dispatcher_env_turnos.py
runpy test_enruta.py
runpy test_enruta_salud_compartida.py   # 25-sep · una sola caché de salud para todos los árboles; sonda de grok sin búsqueda
runpy test_enruta_comite.py
runpy test_decide_peticion.py
runpy test_enrutado_herencia.py        # 26-sep · las órdenes de control («Fusiona», «Siguiente») no heredan el comité clínico
runpy test_lentes.py
runpy test_gate_salida.py
runpy test_replay_gate.py
runpy test_gate_punto08.py
runpy test_gate_s15.py   # 25-sep · S15 {{CONTACTO}}+KAI: 12 normas de salida más con check (aviso), medidas con replay
runpy test_juez_capa3.py   # 26-sep · juez capa 3 ({{CONTACTO}}+KAI), pasos 1, 2 y 5: prefiltro, frase_inventada (aviso), 4 normas a contexto
runpy test_juez_normas.py  # 26-sep · juez capa 3 ({{CONTACTO}}+KAI), pasos 3 y 4: banco doble ciego, juez Claude sin tools, sin rutina hasta pasar el listón
runpy test_gate_presupuesto.py
runpy test_launch_loopback.py
runpy test_nvidia_tope.py
runpy test_readme_modelos.py
runpy test_stdin_canalizado.py
runpy test_bash_return_explicito.py
runpy test_casa_base_guard.py
runpy test_gate_etiqueta.py
runpy test_gate_escalera.py   # 25-sep · escalera del gate con listón numérico (idea de {{CONTACTO}} + KAI)
runpy test_gate_citas.py
runpy test_all_rojo_dir.py   # 25-sep · cada ejecución guarda sus rojos en SU carpeta (deuda test-all-log-rojo-tmp-compartido)
runpy test_all_stdin_cerrado.py   # 25-sep · la suite cierra stdin: ningún test hereda un pipe que no se cierra
runpy test_all_copia_fija.py   # 10-oct · reescribir test_all.sh EN SITIO a mitad de una pasada no la corrompe (se ejecuta desde una copia fija)
runpy test_all_paralelo.py   # 10-oct · BTP_JOBS: serie y paralelo dicen lo mismo, fail-closed, SOLO_SERIE, cronómetro (campaña de mutantes: tests/mutantes/test_all_paralelo.json, a mano o de noche: ~8 min)
runpy test_suite_nocturna.py   # 10-oct · la suite de noche sobre casa base: avisa el muro la 1ª noche, deuda, flaky, latido y las 3 condiciones para cambiar la norma (campaña de mutantes: tests/mutantes/suite_nocturna.json, domingos de noche)
runpy test_rojos_conocidos.py   # 10-oct · «sin rojos NUEVOS»: deuda, dueño, caducidad ≤ 14 d, firma exacta y núcleo del muro VETADO sin el OK de {{TITULAR}}
runpy test_all_puerta.py   # 10-oct · la puerta FALLA CERRADA: selector roto/vacío/desconocido → suite completa; el meta-check corre también en la rápida
runpy test_gate_red_caida.py   # 25-sep · punto 07 {{CONTACTO}}+KAI: sin red, la cita sale «sin verificar», nunca verificada
runpy test_gate_preclinico.py
runpy test_verifica_citas_estados.py
runpy test_tier_evidencia.py
runpy test_soporte_cita.py
runpy test_soporte_cita_juez.py
# (no publicado: cubre un detector de PHI que vive solo en local)
runpy test_deuda_texto_sin_alarma.py
runpy test_deuda_duplicadas.py
runpy test_rodaje_utc.py
runpy test_rodaje_casa_base.py
runpy test_rodaje_hook_fijo.py
runpy test_replay_hook_roto.py
runpy test_replay_guard_json.py
runpy test_kpi_ned.py
runpy test_backup.py
runpy test_dependencias.py
runpy test_saldo_aprende.py
runpy test_scite_guard.py
runpy test_salud_reconciliar.py
runpy test_hoy_ruta_unica.py
runpy test_obs_nombra_el_trabajo.py
runpy test_migrar_secretos.py
runpy test_etiquetar_hilos.py
runpy test_agentes_frontmatter.py
runpy test_modelo_coherente.py
runpy test_agentes_ritmo.py
runpy test_radar_personas.py
runpy test_salida_guard.py
runpy test_salida_guard_vias.py   # 24-sep · auditoría 3.2: lo que lleva datos a la red se juzga por el DESTINO
runpy test_trust_cloud_tty.py     # 1-oct · confiar una nube de píxeles N1 solo tecleando en su terminal
runpy test_laminillas_n1.py       # 1-oct · Puerta de N1, exporta_n1, vision_n1, nube_n1 (PII sintética)
runpy test_laminillas_n1_jaula.py # 2-oct · jaula vision.sb de la llamada HTTP de vision_n1/nube_n1 (canarios sintéticos) y config.json sellado
runpy test_laminillas_jaulas_secretos.py # 2-oct · ninguna jaula SBPL (×extras reales) abre secretos ni zona clínica: deny de secretos al FINAL, sandbox-exec con señuelos
runpy test_nivel_salida.py   # 25-sep · P3 F1: nivel de cada salida EN SOMBRA (anota, no manda)
runpy test_ok_envio_blindado.py
runpy test_lote_envio.py   # 9-oct · una orden envía N DMs leídos: manifiesto en el transcript, 15 min, un uso por ítem, tope 10
runpy test_correos_publicables.py
runpy test_entrada_guard.py
runpy test_audit_comites_uso.py
runpy test_cost_guard.py
runpy test_bot_triage.py
runpy test_triage_route_enrutado.py
runpy test_buzon_ideas.py
runpy test_cosecha_correcciones.py
runpy test_repeticiones_semana.py
runpy test_gate_subagente.py
runpy test_subagente_contexto.py
runpy test_cosecha_hilos.py
runpy test_cosecha_entregables.py
runpy test_archivar_nota.py
runpy test_email_archive_reindex.py   # 25-sep · tras archivar correo, el RAG se reindexa en el acto
runpy test_colgados_stdin.py
runpy test_raices_casa_base.py
runpy test_radar_no_silenciar.py
runpy test_deuda_disponibilidad.py
runpy test_deuda_cerrar_ejecuta.py
runpy test_atribucion.py   # 24-sep · regla de {{TITULAR}}: todo lo que alguien aporta, con su nombre y en «Gracias»
runpy test_clave_deuda_una_forma.py   # 24-sep · una sola forma de clave (backticks) + la alerta resumen exige su hallazgo concreto
runpy test_ramas_fusionar.py
runpy test_ocr_layout.py
runpy test_salida_reintento.py
runpy test_salida_idempotente.py   # 24-sep · auditoría 3.5: una aprobación entrega UNA vez; lo incierto no se reenvía solo
runpy test_dominios_con_dueno.py
runpy test_honestidad_lint.py
runpy test_verifica_citas.py
runpy test_verifica_citas_datacite.py   # 24-sep · un 404 de Crossref no es cita fabricada (Zenodo/DataCite) + DOI sin markdown pegado
runpy test_memoria_radar.py
runpy test_memoria_sistema.py
runpy test_constitucion_sin_perdida.py
runpy test_escalado_inteligencia.py
runpy test_normas_registro.py
runpy test_normas_gracia.py
runpy test_normas_mecanizadas.py
runpy test_githooks_base.py
runpy test_githooks_marcas.py
runpy test_recall_memoria.py
runpy test_memoria_contradicciones.py   # 26-sep · capa 5: memorias que se contradicen (solo informa)
runpy test_perfil_clinico_al_dia.py
runpy test_portero_ruido.py
runpy test_parte_exento_cupo.py
runpy test_aplazados_pendientes.py   # 24-sep · el parte recoge lo aplazado de CUALQUIER día (855 avisos perdidos desde el 27-jul)
runpy test_regla_en_accion.py
runpy test_hooks_ejecutables.py
runpy test_guard_timeout.py   # 25-sep · {{CONTACTO}}/KAI: un guard lento deniega, no deja pasar; deny de respaldo
runpy test_instala_muro_usuario.py   # 10-oct · el muro mínimo llega a las carpetas de fuera de claudecode (ajustes de usuario + lanzadores)
runpy test_cerrar_sesion_conflicto.py
runpy test_cerrar_sesion_poda_viva.py   # 25-sep · no podar el worktree de una sesión viva (le apagaba el muro)
runpy test_cerrar_sesion_verifica_base.py   # 26-sep · tras fusionar, corre en casa base lo que el worktree salta
runpy test_cerrar_sesion_ruido.py   # 10-oct · F: una fusión que deja roja una batería de casa base abre deuda, avisa (urgente si es del muro) y no culpa a la rama de lo que ya estaba (campaña de mutantes: tests/mutantes/cerrar_sesion_ruido.json, domingos de noche)
runpy test_run_agent_casa_master.py
runpy test_ff_al_abrir.py
runpy test_mini.py
runpy test_lazo_estres.py
runpy test_codigo_rojo.py
runpy test_codigo_rojo_repeticion.py
runpy test_decision_alto_riesgo.py
runpy test_fuente_clinica.py
runpy test_biomarcadores_vhio.py
runpy test_biomarcadores_ggt_alias.py
runpy test_biomarcadores_fecha_extraccion.py
runpy test_biomarcadores_muestras.py
runpy test_biomarcadores_hormonas.py    # 26-sep · marcadores y hormonas nuevos, «<15» con su comparador, MD Anderson acotado
runpy test_rag_lab_origen.py  # analíticas activas extraídas del PDF, no transcritas a mano (25-sep)
runpy test_web_citas_futuras.py  # web: ninguna cita futura con día junto a un lugar (acoso, 25-sep)
runpy test_frescura_dosier.py
runpy test_dosier_invariantes.py
runpy test_cotejo_invariante.py
runpy test_elegibilidad_ensayos.py
runpy test_guardian_evals.py
runpy test_cumbre.py
runpy test_cumbre_integridad.py
runpy test_cascada_clinica.py
runpy test_seguimiento.py
runpy test_seguimiento_carrera.py
runpy test_seguimiento_objetivo_ned.py
runpy test_seguimiento_hecho_cuando.py
runpy test_avisos_nueva_tarea.py
runpy test_reconciliar_estado.py
runpy test_dedup_hilos.py
runpy test_esquema_estado.py
runpy test_web_novedad.py
runpy test_web_lint.py
runpy test_caso_publico.py
runpy test_seguridad_sweep_daemon.py
runpy test_seguridad_sweep.py
runpy test_pipeline_vacuna.py
runpy test_pipeline_datos_reales.py
runpy test_pipeline_alelos_muestra.py
runpy test_investigacion_fuga.py
runpy test_vega_gate.py
runpy test_centinela_ned.py
runpy test_radar_ned_diario.py
runpy test_radar_navegador.py
runpy test_persecucion.py
runpy test_memoria_contactos.py
runpy test_anticipa.py
runpy test_vega_metrics.py
# (no publicado: cubre un detector de PHI que vive solo en local)
runpy test_correo_imap.py
# (no publicado: cubre un detector de PHI que vive solo en local)
# (no publicado: cubre un detector de PHI que vive solo en local)
# (no publicado: cubre un detector de PHI que vive solo en local)
runpy test_historial_sync.py
runpy test_historial_trazador.py
runpy test_healthcheck_drive.py
runpy test_healthcheck_cerebro.py
runpy test_healthcheck_ci_publico.py
runpy test_subir_historial_drive.py
runpy test_subir_historial_ocr_sidecar.py  # el verificador de identidad LEE el OCR (X.pdf.ocr.txt)
runpy test_pendientes.py
runpy test_correo_smtp.py
runpy test_correo_triage.py
runpy test_triage_route_correo.py
# (no publicado: cubre un detector de PHI que vive solo en local)
runpy test_instagram_dm.py
runpy test_nonce_a4_blindado.py   # 26-sep · el nonce A4 no queda en disco; solo le llega a {{TITULAR}}
runpy test_dm_inbox_buzones.py
# (no publicado: cubre un detector de PHI que vive solo en local)
runpy test_borde.py
runpy test_canarios.py
runpy test_cerebro_enlace.py
runpy test_reescribe_consulta.py
runpy test_deid.py
runpy test_deid_procedencia.py   # 24-sep · auditoría 3.3: lo del caso es N2 por procedencia aunque el detector no vea nada
runpy test_deid_ner.py           # 25-sep · P4: capa NER del BSC; con NER pedido y caído, nada a medias
runpy test_deid_diccionario.py   # 25-sep · P4: sus identificadores concretos, sin etiqueta y partidos
runpy test_deid_eval.py          # 25-sep · P4: el banco mide bien y el suelo de recall sobre MEDDOCAN no baja
runpy test_kb_pdf_avisos.py
runpy test_kb_fts5.py
runpy test_kb_hibrido.py
runpy test_kb_lock.py
runpy test_contexto_caso.py
runpy test_ia.py
runpy test_cn_fetch.py
runpy test_cde_fetch.py
runpy test_cn.py
runpy test_archivar_nota_honesto.py
runpy test_archivar_nota_venv.py
runpy test_umami_pagina.py
runpy test_centralita_cerebros.py
runpy test_cerebros_generativo.py
runpy test_evidencia_no_cuelga.py
runpy test_oauth_refresh.py
runpy test_freno_criticidad.py
runpy test_run_agent_f2.py
runpy test_run_agent_reserva_max.py
runpy test_continuidad_auto.py
runpy test_continuidad_contexto.py
runpy test_historial_identidad.py
runpy test_estado_caso.py
runpy test_promesas_caso.py   # 1-oct · F2.1 Vega al mando: promesas con plazo de los chats del caso (fecha en Python)
runpy test_incongruencias_caso.py   # 1-oct · F2b Vega al mando: misma muestra con cifras distintas entre informes (N1 al parte)
runpy test_vega_sesion.py   # 1-oct · F3 Vega al mando: una sesión persistente de Vega en Telegram (--resume, rotación, delta)
runpy test_perfil_vega.py   # 1-oct · plan «Vega aprende y se adelanta», eslabón 2: el perfil de Vega tiene quien lo escriba
runpy test_acciones_datadas.py   # 2-oct · plan «Vega aprende y se adelanta», eslabón 3: lo que tiene fecha se propone solo
runpy test_reacciones_vega.py   # 2-oct · plan «Vega aprende y se adelanta», eslabón 4: las reacciones de {{TITULAR}} son señal para Vega
runpy test_respuesta_sin_cupo.py   # 8-oct · contestarle a {{TITULAR}} no gasta el cupo de avisos (le escribió a Vega y la respuesta se aplazó)
runpy test_aprobaciones_atascos.py   # 1-oct · F4+F5 Vega al mando: política A/B/C con registro y atascos en el parte
runpy test_canario_muro.py
runpy test_claude_lazo.py
runpy test_xurl.py
runpy test_x_mcp_puente.py   # el MCP de X relanza su puente en vez de quedarse ciego (14-sep-26)
runpy test_x_daemons.py
runpy test_x_guardados_enriquecido.py
runpy test_x_guardados_analisis.py   # 1-oct · los guardados de X se analizan cada 3 días (cola → parte a Vega)
runpy test_ramas_detached.py
runpy test_poda_no_se_lleva_ignorados.py
runpy test_git_mutex_poda.py
runpy test_autopoda_residuo.py     # 22-sep · los worktrees fusionados se podan solos; el residuo de tests no los bloquea, lo dudoso sí
runpy test_autopoda_sesion_viva_cwd.py  # 26-sep · la poda no borra el worktree de una sesión viva: lsof por ruta absoluta, cwd ilegible = no se poda
runpy test_ramas_conflictos.py
runpy test_ramas_borrador.py
runpy test_cierre_continuidad.py
runpy test_traspaso_compact.py
runpy test_bot_free.py
runpy test_bot_heartbeat.py
runpy test_responder_datos.py
runpy test_healthcheck.py
runpy test_llavero_mudo.py
runpy test_saldo_api.py
runpy test_healthcheck_syspath.py
runpy test_jobs_caidos.py
runpy test_healthcheck_acuse.py
runpy test_healthcheck_llms.py
runpy test_perplexity_agent.py
runpy test_healthcheck_alerta_str.py
runpy test_bucles_colgados.py
runpy test_chrome_headless_cierra.py   # 26-sep · el ayudante de Chrome headless lo cierra siempre (Chrome real, solo casa base)
runpy test_healthcheck_cpu.py   # 26-sep · CPU saturada dos vueltas seguidas avisa; un pico no
runpy test_tests_afectados.py   # 26-sep · test_all --cambiados elige bien las baterías
runpy test_activar_daemon.py
runpy test_activar_daemon_deshabilitado.py
runpy test_plists_home.py
runpy test_correo_cuenta_principal.py
runpy test_vigia.py
runpy test_anatomia.py
runpy test_anatomia_tecnica.py
runpy test_anatomia_mapa.py
runpy test_anatomia_al_dia.py
runpy test_anatomia_temporales.py   # 26-sep · la huella no cuenta los _mutante_*.py de una batería en marcha
runpy test_anatomia_worktree.py    # 26-sep · desde un worktree, la huella usa el código de la rama
runpy test_traza_subagente.py
runpy test_ciclo_agentes.py
runpy test_presencia_cc.py
runpy test_anatomia_push.py
runpy test_errores.py
runpy test_rc_turnos_agotados.py
runpy test_auto_mejora_turnos.py
runpy test_cola_turnos.py
runpy test_evals.py
runpy test_borde_gateway.py
runpy test_yt_inbox.py
runpy test_mcp_server.py
run   test_git_barrido_poda.sh  # 22-sep · la poda diaria no depende de que el modelo esté disponible
run   test_dispatcher.sh
runpy test_panel_aislado.py       # 22-sep · la batería no escribe en el PANEL-LAZO de verdad (bloqueaba la poda)
run   test_credito_agotado.sh

# Mutantes sobre los frenos del MURO: una defensa sin mutante que la mate no está cubierta.
# ~20 s. Nació de dos checks que pasaban EN VACÍO el 20-sep-26 y que solo aparecieron al
# romper a propósito lo que decían proteger.
echo "── mutantes: tests/mutantes/lector_clinico.json ──"
"$PY" "$ROOT/tools/mutantes.py" tests/mutantes/lector_clinico.json >/tmp/t.$$ 2>&1; _rcm=$?; tail -1 /tmp/t.$$
[ $_rcm -ne 0 ] && { fail=$((fail+1)); cp /tmp/t.$$ "$ROJO_DIR/rojo-mutantes.log" 2>/dev/null;
                     echo "  🔴 ROJO: campaña de mutantes (log: $ROJO_DIR/rojo-mutantes.log)"; }
echo "── mutantes: tests/mutantes/soporte_cita.json ──"
"$PY" "$ROOT/tools/mutantes.py" tests/mutantes/soporte_cita.json >/tmp/t.$$ 2>&1; _rcm=$?; tail -1 /tmp/t.$$
[ $_rcm -ne 0 ] && { fail=$((fail+1)); cp /tmp/t.$$ "$ROJO_DIR/rojo-mutantes-soporte.log" 2>/dev/null;
                     echo "  🔴 ROJO: campaña de mutantes soporte_cita (log: $ROJO_DIR/rojo-mutantes-soporte.log)"; }
echo "── mutantes: tests/mutantes/biomarcadores.json ──"
"$PY" "$ROOT/tools/mutantes.py" tests/mutantes/biomarcadores.json >/tmp/t.$$ 2>&1; _rcm=$?; tail -1 /tmp/t.$$
[ $_rcm -ne 0 ] && { fail=$((fail+1)); cp /tmp/t.$$ "$ROJO_DIR/rojo-mutantes-biomarcadores.log" 2>/dev/null;
                     echo "  🔴 ROJO: campaña de mutantes biomarcadores (log: $ROJO_DIR/rojo-mutantes-biomarcadores.log)"; }
echo "── mutantes: tests/mutantes/biomarcadores_fecha.json ──"
"$PY" "$ROOT/tools/mutantes.py" tests/mutantes/biomarcadores_fecha.json >/tmp/t.$$ 2>&1; _rcm=$?; tail -1 /tmp/t.$$
[ $_rcm -ne 0 ] && { fail=$((fail+1)); cp /tmp/t.$$ "$ROJO_DIR/rojo-mutantes-biomarcadores-fecha.log" 2>/dev/null;
                     echo "  🔴 ROJO: campaña de mutantes biomarcadores_fecha (log: $ROJO_DIR/rojo-mutantes-biomarcadores-fecha.log)"; }
echo "── mutantes: tests/mutantes/caso_publico.json ──"
"$PY" "$ROOT/tools/mutantes.py" tests/mutantes/caso_publico.json >/tmp/t.$$ 2>&1; _rcm=$?; tail -1 /tmp/t.$$
[ $_rcm -ne 0 ] && { fail=$((fail+1)); cp /tmp/t.$$ "$ROJO_DIR/rojo-mutantes-caso-publico.log" 2>/dev/null;
                     echo "  🔴 ROJO: campaña de mutantes caso_publico (log: $ROJO_DIR/rojo-mutantes-caso-publico.log)"; }
echo "── mutantes: tests/mutantes/rojos_conocidos.json ──"
"$PY" "$ROOT/tools/mutantes.py" tests/mutantes/rojos_conocidos.json >/tmp/t.$$ 2>&1; _rcm=$?; tail -1 /tmp/t.$$
[ $_rcm -ne 0 ] && { fail=$((fail+1)); cp /tmp/t.$$ "$ROJO_DIR/rojo-mutantes-rojos-conocidos.log" 2>/dev/null;
                     echo "  🔴 ROJO: campaña de mutantes rojos_conocidos (log: $ROJO_DIR/rojo-mutantes-rojos-conocidos.log)"; }
echo "── mutantes: tests/mutantes/tests_afectados.json ──"
"$PY" "$ROOT/tools/mutantes.py" tests/mutantes/tests_afectados.json >/tmp/t.$$ 2>&1; _rcm=$?; tail -1 /tmp/t.$$
[ $_rcm -ne 0 ] && { fail=$((fail+1)); cp /tmp/t.$$ "$ROJO_DIR/rojo-mutantes-tests-afectados.log" 2>/dev/null;
                     echo "  🔴 ROJO: campaña de mutantes tests_afectados (log: $ROJO_DIR/rojo-mutantes-tests-afectados.log)"; }

# Pieza 10 del arnés agéntico: drift de agentes críticos (determinista, sin LLM)
echo "── evals/test_drift_agentes.py ──"
"$PY" "$ROOT/evals/test_drift_agentes.py" >/tmp/t.$$ 2>/dev/null; _rc=$?; tail -1 /tmp/t.$$; [ $_rc -ne 0 ] && fail=$((fail+1))

# Sistema de errores — Fases 2, 3a, 3c
runpy test_recover.py
runpy test_rotar_logs.py
runpy test_viajes_precios.py

# ── Huérfanos enganchados el 25-jul-26 (auditoría) ───────────────────────────
# Estaban escritos y en verde pero NADIE los corría: cada test nuevo había que añadirlo
# a mano aquí y se olvidaba. Ese desfase es lo que dejó invisible durante un MES que
# test_audit_constelacion estaba en rojo y que el digest de guardados estaba muerto.
# Cuatro de ellos son baterías del MURO: «TODO EN VERDE» lo nombraba sin haberlas corrido.
run   test_digest.sh
runpy test_muro_hook.py
runpy test_muro_base_gate.py
runpy test_muro_costura_rm.py
runpy test_muro_a1_sandbox_escalada.py
runpy test_muro_secreto_stdout.py
runpy test_clinico_guard.py
runpy test_log_auditoria_casa_base.py
runpy test_lector_clinico_binario.py
runpy test_laminillas.py   # 1-oct · laminillas DFCI F1-infra: ventanilla enjaulada (sandbox-exec), puertas y jaulas con canarios sintéticos
runpy test_laminillas_piloto_color.py; runpy test_laminillas_piloto_segmenta.py; runpy test_laminillas_piloto_congela.py   # 1-oct · piloto módulo A (venv patologia; sin él, SKIP 77)
runpy test_laminillas_piloto_b.py   # 1-oct · piloto módulo B: registro, métricas y GeoJSON sobre cortes sintéticos (venv patologia; sin él, SKIP 77)
runpy test_laminillas_valis.py   # 2-oct · respaldo VALIS del registro con VALIS REAL: corre (también dentro de analisis) y Slide.M recupera una transformada conocida <1 px, directo y espejo (~40 s; sin venvs, SKIP 77)
runpy test_laminillas_f3.py   # 2-oct · F3 parte A: FC de consenso, puerta de p63, (a-bis) y gemela NE, (b) y ROI de Carlos, sintéticos (venv patologia; sin él, SKIP 77)
runpy test_laminillas_he.py   # 2-oct · F3 parte B: H&E del primario (mini-puerta HistoPLUS/NuLite, regiones, infiltrado), hueso sin células y exploratorio, sintéticos (venv patologia; sin él, SKIP 77)
runpy test_laminillas_1bis.py   # 2-oct · hoja del paso 1-bis (PDF en SESION) y su orden tecleada en un pty: VISTO-N1 sella vía tty, sintético (venv patologia)
runpy test_laminillas_capas.py   # 2-oct · capas-piloto: capas N1 del panel de visión → capas_piloto.json; pasan siembra_n1 y revisa_capas, tribunal_listo las ve, la Puerta de verdad rechaza un rotulado, selección sin DAB de Ki67 (mutante) (~70 s; venv patologia, sin él SKIP 77)
runpy test_panel_vision.py   # 1-oct · panel de visión: calibración con errores sembrados, criterio por tarea, ollama solo local (imágenes con venv patologia; sin él, solo stdlib)
runpy test_mutantes.py
runpy test_visor3d.py
runpy test_visor3d_mps.py
runpy test_esqueleto_niveles.py
runpy test_esqueleto_referencia.py
runpy test_secretos_largos.py
runpy test_secretos_largos_paralelo.py
runpy test_guarda_memoria.py
runpy test_visor3d_cuelgue.py
runpy test_lector_sirve_sin_vigilante.py
runpy test_visor3d_malla_recorte.py
runpy test_visor3d_ficha.py
runpy test_visor3d_carga.py
runpy test_visor3d_marcas.py
runpy test_visor3d_mascara_union.py
runpy test_visor3d_banco.py
runpy test_visor3d_cateter.py
runpy test_visor3d_losa.py
runpy test_visor3d_modelo_port.py
runpy test_visor3d_traquea_inclinada.py
runpy test_visor3d_tubo_a_rayas.py
runpy test_visor3d_procedencia.py
runpy test_visor3d_colab.py
runpy test_sonda_silencio.py
runpy test_vigia_latidos.py
runpy test_tiempo_sesiones.py
runpy test_cola_ruido.py
runpy test_worktree_guard.py
runpy test_zonas_clinicas.py
runpy test_drive_gate.py
runpy test_audit_constelacion.py
runpy test_audit_herramientas.py
runpy test_x_guardados_honestidad.py
runpy test_x_guardados_cli.py
runpy test_onco.py
runpy test_inventario.py
runpy test_edad_en_publico.py   # 26-sep · la edad de {{TITULAR}} SÍ se dice en público
runpy test_inventario_viejas_modelos.py   # 24-sep · issues #2 y #5 (PR #23 de fuera, incorporado con cambios)
runpy test_fichas.py                      # 25-sep · ninguna tool sin ficha; idea de {{CONTACTO}} + KAI
runpy test_inventario_uso.py              # 25-sep · uso real acumulado y ciclo 60/90 (clasificar no es borrar)
runpy test_estado_rutina.py               # 24-sep · issue #4 (PR #22 de fuera, incorporado con cambios)
runpy test_rutinas_latido.py              # 25-sep · cadencia real + periodo declarado + cola del comité ({{CONTACTO}}+KAI)
runpy test_audit_agentes_daemon.py
runpy test_publicar_fuga.py
runpy test_publicar_personas.py  # ficha de persona `alto`: ni su apellido ni su ficha salen al espejo
runpy test_publicar_publicos.py  # excepción con consentimiento: la cadena exacta, solo en sus ficheros
runpy test_espejo_reiniciar_historial.py  # reiniciar el historial del espejo: ensayo por defecto, sin la palabra no empuja
runpy test_publicar_overlay_casa_base.py
runpy test_publicar_sync.py
runpy test_publicar_sync_candado.py  # dos publicaciones a la vez NO se pisan el árbol
runpy test_lock_dueno.py          # 25-sep · el candado compartido no se le quita a un dueño VIVO
runpy test_espejo_ensayo.py  # no se publica en rojo, y el espejo se reconoce por su marca
runpy test_ci_barrido.py
runpy test_capacidades.py
runpy test_centralita_blacklist.py
runpy test_correo_smtp_gate.py
runpy test_cosecha_checklists.py
runpy test_cronica.py
runpy test_elicit.py
runpy test_git_mutex.py
runpy test_git_mutex_freno_base.py
runpy test_git_mutex_merge_fallido.py
runpy test_git_mutex_rama_ajena.py   # 26-sep · a casa base solo llega la rama de la sesión que fusiona
runpy test_singleton_guard.py
runpy test_rama_vista_guard.py
runpy test_copy_web_guard.py
runpy test_reservas_decision.py
runpy test_identidad_paciente.py
runpy test_identidad_tabla.py       # 24-sep · la ventanilla marcaba como AJENAS sus propias Rx de 2024 (tabla markdown)
runpy test_borrador_unico.py
runpy test_web_i18n.py
runpy test_paso_consolidacion.py
runpy test_caja.py
runpy test_cosecha_panel.py
runpy test_contrato_asiento.py
runpy test_portguard.py
runpy test_puertos_loopback.py
runpy test_observatorio_movil.py
runpy test_staging_loopback.py
runpy test_rebuild_agents.py
runpy test_video_polaris.py         # 27-sep · el vídeo público de Polaris: sin léxico vetado ni cifras clínicas

# ── Hallazgos de impacto MEDIO de la auditoría del 25-jul-26 ─────────────────────
# Cada uno cierra un hallazgo con ficha del informe. La regla es que «arreglado» solo
# existe con un TEST: sin esto, el arreglo es una afirmación.
runpy test_reap_vivo.py             # nº7   · reap_stuck no rescata lo que sigue corriendo
runpy test_healthcheck_deadman.py   # nº4+5 · el acuse encola de verdad · dead-man sin `pending>0`
runpy test_healthcheck_halt_inactividad.py  # el HALT no dispara «daemon inactivo» (1.177 falsos en 42 d)
runpy test_kickstart_bootstrap.py   # el autofix levanta un daemon caído del dominio (kickstart→bootstrap)
runpy test_healthcheck_state_aislado.py  # BTP_STATE_DIR aísla de verdad: la batería no escribe en el estado vivo
runpy test_sys_path_limpio.py       # importar una tool no decide de dónde importan las demás
runpy test_centinela_vencidos.py    # nº14  · los plazos vencidos dejan de ser mudos
runpy test_honestidad_lint_repo.py  # nº10  · el barrido cubre algo (barría 0 documentos)
runpy test_evals_honestidad.py      # nº13  · el sello de evidencia ya tiene golden set
runpy test_coste_repo.py            # nº15  · el gasto se mide en TODO el repo, no solo casa base
runpy test_coste_modelos.py         # 13-sep · todo modelo claude-* gastado tiene precio; se ven subagentes y workflows
runpy test_gasto_tarifa.py          # 24-sep · un modelo sin tarifa se DICE; una sola tabla y un solo matcher
runpy test_saldo_prepago.py         # el estimador del prepago deja de afirmar lo que no sabe
runpy test_bench_jev.py           # 21-sep · a Jev solo sale lo que pasa el borde + fechas/@/URLs
runpy test_contador_carriles.py       # 25-sep · NVIDIA y Jev apuntan cada respuesta en el ledger de gasto; pings marcados
runpy test_bench_modelos.py       # 25-sep · P5: partición, umbral en calibración, freno MLX, 0 textos a disco
runpy test_presorteo.py           # 25-sep · P5: el presorteo ordena, nunca decide; fail-closed; 0 títulos a disco
runpy test_cosecha_whatsapp.py    # 25-sep · la bandeja de WA muestra 300 pero no pierde ninguno (desbordamiento)
runpy test_eval_triage_residuo.py # 21-sep · el set dorado no sale con fechas, URLs, @handles ni números largos
runpy test_radar_orden_jev.py     # 21-sep · Jev solo ordena la cola del radar: nunca archiva ni toca lo cruzado
runpy test_radar_encaje_n1.py     # 22-sep · encaje N1: sin trust o con perfil caducado no sale nada; solo ordena
runpy test_radar_encaje_n1_huella.py  # 26-sep · candado N1 por huella de §1: si §1 cambió sin mover la fecha, no sale
runpy test_radar_gate_multicohorte.py # 21-sep · un «encaja» en un ensayo multicohorte exige citar SU cohorte
runpy test_radar_archivo_cerrados.py # 21-sep · pasar de 200 cierres no borra veredictos ni re-encola leads
runpy test_radar_revision.py   # 1-oct · lo descartado se revisa: cambio de perfil o >180 días (directo {{CONTACTO}}+KAI)
runpy test_radar_reintentos.py     # 21-sep · un fallo de red pasajero no deja un tema del radar sin nada
runpy test_session_start_topologia.py  # 24-sep · el HALT del código rojo no hace creer al mini que es el Air
runpy test_session_start_lazo.py      # 25-sep · el lazo no lanza el drenaje de reels (sesión de IG)

# ── Huérfanos registrados el 10-oct-26 ────────────────────────────────────────────────────────
# El meta-check de abajo llevaba rojo con estos 8 tests escritos y en verde que nadie corría. Se
# comprobaron UNO A UNO antes de engancharlos (los 8 salen rc 0 y están aislados con BTP_STATE_DIR /
# BTP_HALT_FILES / mocks: ninguno toca estado vivo ni dispara nada de verdad).
runpy test_codigo_rojo_excepciones.py   # 2-oct · una decisión deliberada de {{TITULAR}} no vuelve a parar el sistema (MURO: código rojo)
runpy test_backup_latido.py             # 2-oct · backup.sh late «ok» solo con copia hecha
runpy test_cerrar_sesion_registra_vega.py   # 1-oct · cada fusión queda en el registro de aprobaciones de Vega
runpy test_session_start_vega.py        # 1-oct · la sesión arranca con la visión de Vega
runpy test_vega_vision_fusiones.py      # 1-oct · la visión de Vega ve las fusiones
runpy test_cribado_pmid.py              # la vía PMID del borde: solo un PMID, solo a destinos de la lista, HALT y canario cortan (MURO, egress)
runpy test_nan.py                       # 8-oct · cliente de NaN Community y su entrada en el registro, sin red
runpy test_x_guardados_etiquetas.py     # 1-oct · las palabras clave de los guardados casan por palabra, no dentro de otras

# ⛔ NO añadir aquí (a propósito, no por olvido): test_avisos_origen.py, test_casa_estilo.py,
# test_observatorio.py, test_salida.py, test_tablero.py y test_triage.py importan `salida` SIN
# exportar BTP_TEST_BATTERY, así que meterlos en la batería le mandaría Telegram REAL a {{TITULAR}}.
# test_avisos_origen.py:34 lo dice explícito: «aquí NO ponemos BTP_TEST_BATTERY. Este test
# necesita que salida.send() llegue hasta la boca». Es diseño, no descuido: el 12-jul-2026 una
# pasada de tests le mandó 14 mensajes de verdad. Para engancharlos hace falta antes un modo
# dry/fixture; hasta entonces se corren a mano.

_vacia_cola   # con BTP_JOBS>1 aquí corre todo lo encolado; en serie no hace nada

# Meta-check: que este runner no se vuelva a quedar atrás solo.
if [ -z "$CAMBIADOS" ] || [ -n "$PUERTA" ]; then   # también en la puerta rápida: un test nuevo sin registrar no se cuela
echo "── meta: tests no invocados ──"
_huerf=""
for _f in "$ROOT"/tests/test_*.py "$ROOT"/tests/test_*.sh; do
  _b=$(basename "$_f")
  case "$_b" in test_avisos_origen.py|test_casa_estilo.py|test_observatorio.py|test_salida.py|test_tablero.py|test_triage.py) continue;; esac
  grep -q "$_b" "${_COPIA:-$ROOT/tests/test_all.sh}" || _huerf="$_huerf $_b"
done
if [ -n "$_huerf" ]; then
  echo "❌ tests escritos que NADIE corre:$_huerf"
  fail=$((fail+1))
else
  echo "✅ ningún test huérfano"
fi
fi

echo
echo "⏱️  las 10 baterías más lentas (s) · total ${SECONDS}s:"
printf '%s' "$_tiempos" | sort -rn | head -10 | sed 's/^/   /'
echo
# Un SKIP no es ni verde ni rojo: es «necesita algo que aquí no está» (ver tests/_entorno.py).
# Se dice aparte para que el número de rojos signifique lo que parece.
[ "$skip" -gt 0 ] && echo "⏭️  $skip batería(s) saltada(s): falta el contenido, el estado vivo, los overlays locales o el lazo (HALT activo)"
# ROJOS CONOCIDOS (10-oct-26): lo registrado en tests/rojos_conocidos.json con deuda abierta, dueño,
# caducidad <= 14 días y la misma firma de fallo NO cuenta para el código de salida; se nombra igual.
# Falla cerrado: si el clasificador no contesta con su línea RESUMEN, todo sigue siendo rojo nuevo.
CONOCIDOS=0
if [ "$fail" -gt 0 ] && [ -f "$ROOT/tests/_rojos_conocidos.py" ] && ls "$ROJO_DIR"/rojo-*.log >/dev/null 2>&1; then
  _plus=""; [ -z "$CAMBIADOS" ] && _plus="--completa"
  _clas=$("$PY" "$ROOT/tests/_rojos_conocidos.py" clasifica "$ROJO_DIR" $_plus 2>&1)
  echo "$_clas" | grep -v '^RESUMEN'
  _k=$(echo "$_clas" | sed -n 's/^RESUMEN conocidos=\([0-9][0-9]*\) nuevos=.*/\1/p' | head -1)
  case "$_k" in ''|*[!0-9]*) _k=0;; esac
  [ "$_k" -le "$fail" ] && CONOCIDOS=$_k
  fail=$((fail-CONOCIDOS))
fi
if [ -n "$PUERTA" ]; then
  [ "$fail" -eq 0 ] && echo "✅ PUERTA RAPIDA en verde: afectadas + núcleo del muro ($fuera batería(s) fuera). NO es «todo en verde»: la suite completa la cubre la rutina nocturna sobre casa base." || echo "❌ $fail batería(s) con fallos (puerta rápida) · logs: $ROJO_DIR"
elif [ -n "$CAMBIADOS" ]; then
  [ "$fail" -eq 0 ] && echo "✅ PARCIAL en verde: $fuera batería(s) no se corrieron (--cambiados). NO es «todo en verde»: antes de fusionar, la suite completa." || echo "❌ $fail batería(s) con fallos (parcial, --cambiados) · logs: $ROJO_DIR"
elif [ "$fail" -eq 0 ] && [ "$CONOCIDOS" -gt 0 ]; then
echo "✅ SIN ROJOS NUEVOS: $CONOCIDOS rojo(s) CONOCIDO(S) con deuda (arriba). NO es «TODO EN VERDE»: con un conocido abierto no se puede decir."
else
[ "$fail" -eq 0 ] && echo "✅✅ TODO EN VERDE (muro + lazo P1)" || echo "❌ $fail batería(s) con fallos · logs de ESTA ejecución: $ROJO_DIR"
fi
# Sin rojos, la carpeta propia sobra (nunca /tmp ni una que haya dado el llamador).
[ "$fail" -eq 0 ] && [ -z "$CI" ] && [ -z "$BTP_ROJO_DIR" ] && rmdir "$ROJO_DIR" 2>/dev/null
exit "$fail"
