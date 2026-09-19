#!/bin/bash
# test_all.sh — corre toda la batería del sistema (muro + lazo P1) en verde o falla.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY=/usr/bin/python3; [ -x "$PY" ] || PY=python3
fail=0
skip=0
run() { echo "── $1 ──"; bash "$ROOT/tests/$1" >/tmp/t.$$ 2>&1; local rc=$?; tail -1 /tmp/t.$$;
        [ $rc -eq 77 ] && { skip=$((skip+1)); return 0; }
        [ $rc -ne 0 ] && { fail=$((fail+1)); cp /tmp/t.$$ "/tmp/rojo-$1.log" 2>/dev/null;
                           echo "  🔴 ROJO: $1 (rc=$rc · log: /tmp/rojo-$1.log)"; }; }
# El nombre del test que se pone ROJO se DICE (27/7/26). Antes runpy solo incrementaba el contador:
# la batería acababa en "❌ 1 batería(s) con fallos" sin decir cuál, y había que ir a mano fichero a
# fichero. Con el log guardado, además, el fallo se puede mirar después (importa para los flakes).
runpy() { echo "── $1 ──"; "$PY" "$ROOT/tests/$1" >/tmp/t.$$ 2>/dev/null; local rc=$?; tail -1 /tmp/t.$$;
          [ $rc -eq 77 ] && { skip=$((skip+1)); return 0; }
          [ $rc -ne 0 ] && { fail=$((fail+1)); cp /tmp/t.$$ "/tmp/rojo-$1.log" 2>/dev/null;
                             echo "  🔴 ROJO: $1 (rc=$rc · log: /tmp/rojo-$1.log)"; }; }

run   test_fuga.sh
run   test_halt.sh
runpy test_muro_fase0.py
runpy test_f1_opus_supervision.py
runpy test_cola.py
runpy test_cola_diario.py
runpy test_dispatcher_env_turnos.py
runpy test_enruta.py
runpy test_enruta_comite.py
runpy test_decide_peticion.py
runpy test_lentes.py
runpy test_gate_salida.py
runpy test_gate_etiqueta.py
runpy test_gate_citas.py
runpy test_gate_preclinico.py
runpy test_verifica_citas_estados.py
runpy test_tier_evidencia.py
# (no publicado: cubre un detector de PHI que vive solo en local)
runpy test_kpi_ned.py
runpy test_backup.py
runpy test_salud_reconciliar.py
runpy test_hoy_ruta_unica.py
runpy test_obs_nombra_el_trabajo.py
runpy test_migrar_secretos.py
runpy test_etiquetar_hilos.py
runpy test_agentes_frontmatter.py
runpy test_audit_comites_uso.py
runpy test_cost_guard.py
runpy test_bot_triage.py
runpy test_triage_route_enrutado.py
runpy test_buzon_ideas.py
runpy test_cosecha_correcciones.py
runpy test_cosecha_hilos.py
runpy test_cosecha_entregables.py
runpy test_archivar_nota.py
runpy test_raices_casa_base.py
runpy test_radar_no_silenciar.py
runpy test_deuda_disponibilidad.py
runpy test_deuda_cerrar_ejecuta.py
runpy test_ramas_fusionar.py
runpy test_ocr_layout.py
runpy test_salida_reintento.py
runpy test_dominios_con_dueno.py
runpy test_honestidad_lint.py
runpy test_verifica_citas.py
runpy test_memoria_radar.py
runpy test_memoria_sistema.py
runpy test_constitucion_sin_perdida.py
runpy test_normas_registro.py
runpy test_normas_mecanizadas.py
runpy test_githooks_base.py
runpy test_recall_memoria.py
runpy test_perfil_clinico_al_dia.py
runpy test_portero_ruido.py
runpy test_parte_exento_cupo.py
runpy test_regla_en_accion.py
runpy test_ff_al_abrir.py
runpy test_mini.py
runpy test_lazo_estres.py
runpy test_codigo_rojo.py
runpy test_codigo_rojo_repeticion.py
runpy test_decision_alto_riesgo.py
runpy test_frescura_dosier.py
runpy test_dosier_invariantes.py
runpy test_cotejo_invariante.py
runpy test_elegibilidad_ensayos.py
runpy test_guardian_evals.py
runpy test_cumbre.py
runpy test_cascada_clinica.py
runpy test_seguimiento.py
runpy test_seguimiento_carrera.py
runpy test_avisos_nueva_tarea.py
runpy test_reconciliar_estado.py
runpy test_dedup_hilos.py
runpy test_seguridad_sweep_daemon.py
runpy test_seguridad_sweep.py
runpy test_pipeline_vacuna.py
runpy test_pipeline_datos_reales.py
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
runpy test_healthcheck_drive.py
runpy test_subir_historial_drive.py
runpy test_pendientes.py
runpy test_correo_smtp.py
runpy test_correo_triage.py
runpy test_triage_route_correo.py
# (no publicado: cubre un detector de PHI que vive solo en local)
runpy test_instagram_dm.py
runpy test_dm_inbox_buzones.py
# (no publicado: cubre un detector de PHI que vive solo en local)
runpy test_borde.py
runpy test_deid.py
runpy test_kb_pdf_avisos.py
runpy test_kb_fts5.py
runpy test_kb_hibrido.py
runpy test_contexto_caso.py
runpy test_ia.py
runpy test_centralita_cerebros.py
runpy test_cerebros_generativo.py
runpy test_evidencia_no_cuelga.py
runpy test_oauth_refresh.py
runpy test_freno_criticidad.py
runpy test_run_agent_f2.py
runpy test_xurl.py
runpy test_x_daemons.py
runpy test_x_guardados_enriquecido.py
runpy test_ramas_conflictos.py
runpy test_ramas_borrador.py
runpy test_cierre_continuidad.py
runpy test_bot_free.py
runpy test_bot_heartbeat.py
runpy test_responder_datos.py
runpy test_healthcheck.py
runpy test_llavero_mudo.py
runpy test_saldo_api.py
runpy test_healthcheck_syspath.py
runpy test_jobs_caidos.py
runpy test_healthcheck_acuse.py
runpy test_bucles_colgados.py
runpy test_activar_daemon.py
runpy test_plists_home.py
runpy test_vigia.py
runpy test_anatomia.py
runpy test_anatomia_tecnica.py
runpy test_anatomia_mapa.py
runpy test_anatomia_al_dia.py
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
run   test_dispatcher.sh
run   test_credito_agotado.sh

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
runpy test_clinico_guard.py
runpy test_zonas_clinicas.py
runpy test_drive_gate.py
runpy test_audit_constelacion.py
runpy test_audit_herramientas.py
runpy test_x_guardados_honestidad.py
runpy test_x_guardados_cli.py
runpy test_onco.py
runpy test_publicar_fuga.py
runpy test_publicar_sync.py
runpy test_ci_barrido.py
runpy test_capacidades.py
runpy test_centralita_blacklist.py
runpy test_correo_smtp_gate.py
runpy test_cosecha_checklists.py
runpy test_cronica.py
runpy test_elicit.py
runpy test_git_mutex.py
runpy test_paso_consolidacion.py
runpy test_caja.py
runpy test_cosecha_panel.py
runpy test_contrato_asiento.py
runpy test_portguard.py
runpy test_rebuild_agents.py

# ── Hallazgos de impacto MEDIO de la auditoría del 25-jul-26 ─────────────────────
# Cada uno cierra un hallazgo con ficha del informe. La regla es que «arreglado» solo
# existe con un TEST: sin esto, el arreglo es una afirmación.
runpy test_reap_vivo.py             # nº7   · reap_stuck no rescata lo que sigue corriendo
runpy test_healthcheck_deadman.py   # nº4+5 · el acuse encola de verdad · dead-man sin `pending>0`
runpy test_centinela_vencidos.py    # nº14  · los plazos vencidos dejan de ser mudos
runpy test_honestidad_lint_repo.py  # nº10  · el barrido cubre algo (barría 0 documentos)
runpy test_evals_honestidad.py      # nº13  · el sello de evidencia ya tiene golden set
runpy test_coste_repo.py            # nº15  · el gasto se mide en TODO el repo, no solo casa base
runpy test_coste_modelos.py         # 13-sep · todo modelo claude-* gastado tiene precio; se ven subagentes y workflows
runpy test_saldo_prepago.py         # el estimador del prepago deja de afirmar lo que no sabe

# ⛔ NO añadir aquí (a propósito, no por olvido): test_avisos_origen.py, test_casa_estilo.py,
# test_observatorio.py, test_salida.py, test_tablero.py y test_triage.py importan `salida` SIN
# exportar BTP_TEST_BATTERY, así que meterlos en la batería le mandaría Telegram REAL a {{TITULAR}}.
# test_avisos_origen.py:34 lo dice explícito: «aquí NO ponemos BTP_TEST_BATTERY. Este test
# necesita que salida.send() llegue hasta la boca». Es diseño, no descuido: el 12-jul-2026 una
# pasada de tests le mandó 14 mensajes de verdad. Para engancharlos hace falta antes un modo
# dry/fixture; hasta entonces se corren a mano.

# Meta-check: que este runner no se vuelva a quedar atrás solo.
echo "── meta: tests no invocados ──"
_huerf=""
for _f in "$ROOT"/tests/test_*.py "$ROOT"/tests/test_*.sh; do
  _b=$(basename "$_f")
  case "$_b" in test_avisos_origen.py|test_casa_estilo.py|test_observatorio.py|test_salida.py|test_tablero.py|test_triage.py) continue;; esac
  grep -q "$_b" "$ROOT/tests/test_all.sh" || _huerf="$_huerf $_b"
done
if [ -n "$_huerf" ]; then
  echo "❌ tests escritos que NADIE corre:$_huerf"
  fail=$((fail+1))
else
  echo "✅ ningún test huérfano"
fi

echo
# Un SKIP no es ni verde ni rojo: es «necesita algo que aquí no está» (ver tests/_entorno.py).
# Se dice aparte para que el número de rojos signifique lo que parece.
[ "$skip" -gt 0 ] && echo "⏭️  $skip batería(s) saltada(s): falta el contenido, el estado vivo, los overlays locales o el lazo (HALT activo)"
[ "$fail" -eq 0 ] && echo "✅✅ TODO EN VERDE (muro + lazo P1)" || echo "❌ $fail batería(s) con fallos"
exit "$fail"
