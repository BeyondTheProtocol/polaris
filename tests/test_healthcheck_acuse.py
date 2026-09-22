#!/usr/bin/env python3
"""test_healthcheck_acuse.py — supersede (heartbeat malo tapado por un OK más reciente) + acuse
("visto, en ello" de tools/salud.py) enganchado en healthcheck._emitir_si_cambia.

Contexto (3/7/26): dos falsas alarmas (asistente 08:20, correo-triaje 06:26) que YA se habían
recuperado en pasadas posteriores, pero el vigía miraba el heartbeat clavado en la pasada vieja sin
comprobar si una pasada MÁS NUEVA había ido bien. Este test cubre:
  1. supersede en _salud_daemons() (VEGA_DAEMONS) vía observabilidad.hubo_ok_desde.
  2. supersede en seguimiento._heartbeats_problema() (la fuente de 'frescura_agente_fallo:*').
  3. el acuse: tras `salud.ack`, _emitir_si_cambia dice "✋ Visto, en ello" y no re-nag dentro de
     la ventana; tras `salud.resuelto`/despejarse la condición, un solo "✅ Resuelto".

Aislado (tmp, salida mock, sin tocar el estado vivo de casa base). No envía nada de verdad.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")
import datetime
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="hc_acuse_test_")
os.environ["BTP_STATE_DIR"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))

import healthcheck as hc     # noqa: E402
import observabilidad as obs  # noqa: E402
import seguimiento as seg     # noqa: E402
import salud                  # noqa: E402
import salida                 # noqa: E402
import cost_guard             # noqa: E402

hc.HC = os.path.join(_TMP, "hc")
os.makedirs(hc.HC, exist_ok=True)
obs.OBS_DIR = os.path.join(_TMP, "obs")
os.makedirs(obs.OBS_DIR, exist_ok=True)
salud.HC = hc.HC
salud.ACUSES = os.path.join(hc.HC, "acuses.json")

_sent = []
salida.report_to_titular = lambda *a, **k: (_sent.append(a[0] if a else ""), {"ok": True})[1]
cost_guard.check_before_job = lambda *a, **k: (True, "test con saldo", 0.0)

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _obs_escribir(agente, resultado, hace_horas):
    """Escribe una traza de observabilidad con ts_fin hace `hace_horas` horas."""
    fin = datetime.datetime.now() - datetime.timedelta(hours=hace_horas)
    rec = {
        "ts_ini": fin.strftime("%Y-%m-%dT%H:%M:%S"), "ts_fin": fin.strftime("%Y-%m-%dT%H:%M:%S"),
        "agente": agente, "job": "run_agent", "duracion_ms": 100, "tokens_in": None,
        "tokens_out": None, "modelo": None, "eur_estimado": None, "resultado": resultado,
        "error_type": None, "stack_trace": None, "severidad": None,
    }
    path = os.path.join(obs.OBS_DIR, "observabilidad-%s.jsonl" % fin.strftime("%Y-%m-%d"))
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _limpiar_obs():
    for f in os.listdir(obs.OBS_DIR):
        os.remove(os.path.join(obs.OBS_DIR, f))


def supersede_hubo_ok_desde_tests():
    """observabilidad.hubo_ok_desde: la primitiva que usan ambos supersedes."""
    _limpiar_obs()
    ts_heartbeat_malo = (datetime.datetime.now() - datetime.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S")
    ok(obs.hubo_ok_desde("asistente", ts_heartbeat_malo) is False,
       "sin trazas → no hay ok posterior (conservador)")

    _obs_escribir("asistente", "fail", hace_horas=2.5)   # ANTES del heartbeat malo (más viejo) → no tapa
    ok(obs.hubo_ok_desde("asistente", ts_heartbeat_malo) is False,
       "un fail más viejo que el heartbeat no cuenta como ok posterior")

    _obs_escribir("asistente", "ok", hace_horas=1.0)      # DESPUÉS del heartbeat malo → SÍ tapa
    ok(obs.hubo_ok_desde("asistente", ts_heartbeat_malo) is True,
       "un ok ESTRICTAMENTE posterior al heartbeat malo → hubo_ok_desde=True")

    _limpiar_obs()
    ok(obs.hubo_ok_desde("asistente", None) is False, "ts_iso None → False (fail-soft, conservador)")
    ok(obs.hubo_ok_desde("asistente", "no-es-una-fecha") is False, "ts_iso ilegible → False")


def salud_daemons_supersede_tests():
    """_salud_daemons() (VEGA_DAEMONS): un heartbeat 'fallo' se tapa si observabilidad tiene un 'ok'
    MÁS NUEVO para ese mismo agente; si NO hay ok más nuevo (o el fail se REPITE sin recuperación),
    la alerta se sigue disparando — el supersede no enmascara un fallo genuino."""
    hb_dir = os.path.join(_TMP, "hb_super")
    os.makedirs(hb_dir, exist_ok=True)
    hc.HB_DIR = hb_dir

    def clear():
        for f in os.listdir(hb_dir):
            os.remove(os.path.join(hb_dir, f))
        hb_iso("centinela-ned", "ok", 0.1)
        hb_iso("bot-telegram", "ok", 0.01)
        hb_local("calendar-sync", "ok", 1)

    def hb_iso(agente, est, edad_h):
        ts = (datetime.datetime.utcnow() - datetime.timedelta(hours=edad_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
        json.dump({"agente": agente, "ts": ts, "estado": est, "modelo": "sonnet"},
                  open(os.path.join(hb_dir, agente + ".json"), "w"))

    def hb_local(agente, est, edad_h):
        ts = (datetime.datetime.now() - datetime.timedelta(hours=edad_h)).strftime("%Y-%m-%d %H:%M")
        json.dump({"estado": est, "citas": 4, "ts": ts}, open(os.path.join(hb_dir, agente + ".json"), "w"))

    def claves(al):
        return [a[0] if isinstance(a, tuple) else a for a in al]

    # CASO REAL (3/7): heartbeat 'asistente' clavado en 'fallo' de hace 2h, pero observabilidad
    # tiene un 'ok' de hace 30 min (una pasada posterior YA se recuperó) → NO se alerta.
    _limpiar_obs(); clear()
    hb_iso("asistente", "fallo", 2.0)
    _obs_escribir("asistente", "ok", hace_horas=0.5)
    al, info = hc._salud_daemons()
    ok("daemon_asistente_fallo" not in claves(al), "fallo tapado por un ok posterior en observabilidad → sin alerta")
    ok(info.get("asistente", {}).get("supersedido_por_ok_posterior") is True, "info marca el supersede (para el Observatorio)")

    # SIN ok posterior (nunca se recuperó) → SÍ alerta — el supersede no enmascara un fallo real.
    _limpiar_obs(); clear()
    hb_iso("asistente", "fallo", 2.0)
    al, info = hc._salud_daemons()
    ok("daemon_asistente_fallo" in claves(al), "fallo SIN ok posterior → sigue alertando (no se enmascara)")

    # El 'ok' posterior tiene que ser ESTRICTAMENTE más nuevo que el heartbeat: uno más VIEJO no tapa.
    _limpiar_obs(); clear()
    hb_iso("asistente", "fallo", 1.0)
    _obs_escribir("asistente", "ok", hace_horas=5.0)   # más viejo que el heartbeat malo
    al, _ = hc._salud_daemons()
    ok("daemon_asistente_fallo" in claves(al), "un ok MÁS VIEJO que el heartbeat no tapa nada")

    _limpiar_obs(); clear()


def frescura_supersede_tests():
    """seguimiento._heartbeats_problema(): 'correo-triaje' corre bajo BTP_AGENT=asistente (mismo
    caso que bot-telegram/correo-urgente) — su heartbeat puede quedarse clavado en 'fallo' aunque
    una pasada posterior de 'asistente' en observabilidad ya saliera OK. Caso real 3/7."""
    hb_dir = os.path.join(_TMP, "hb_frescura")
    os.makedirs(hb_dir, exist_ok=True)
    seg.HEARTBEAT_DIR = hb_dir

    def clear():
        for f in os.listdir(hb_dir):
            os.remove(os.path.join(hb_dir, f))

    def hb(agente, est, hace_horas):
        p = os.path.join(hb_dir, agente + ".json")
        json.dump({"agente": agente, "estado": est}, open(p, "w", encoding="utf-8"))
        t = time.time() - hace_horas * 3600.0
        os.utime(p, (t, t))

    def nombres_fallo():
        return [n for n, clase, _e in seg._heartbeats_problema() if clase == "fallo"]

    # 'correo-triaje' falló hace 2h (heartbeat propio), pero 'asistente' (su BTP_AGENT real) tiene
    # una pasada OK en observabilidad de hace 30 min → el fallo queda TAPADO.
    _limpiar_obs(); clear()
    hb("correo-triaje", "fallo", 2.0)
    _obs_escribir("asistente", "ok", hace_horas=0.5)
    ok("correo-triaje" not in nombres_fallo(), "correo-triaje: fallo tapado por un ok posterior de 'asistente' en observabilidad")

    # Sin esa pasada OK posterior → el fallo se sigue reportando (no se enmascara un fallo real).
    _limpiar_obs(); clear()
    hb("correo-triaje", "fallo", 2.0)
    ok("correo-triaje" in nombres_fallo(), "correo-triaje: sin ok posterior → sigue reportando el fallo")

    # Un agente SIN mapeo especial (p.ej. 'orquestador') usa su propio nombre tal cual — un ok de
    # 'orquestador' en observabilidad sí lo tapa, uno de otro agente no.
    _limpiar_obs(); clear()
    hb("orquestador", "fallo", 2.0)
    _obs_escribir("otro-agente", "ok", hace_horas=0.5)
    ok("orquestador" in nombres_fallo(), "un ok de OTRO agente no tapa el fallo de 'orquestador'")
    _obs_escribir("orquestador", "ok", hace_horas=0.3)
    ok("orquestador" not in nombres_fallo(), "un ok del propio agente sí lo tapa")

    _limpiar_obs(); clear()


def acuse_tests():
    """tools/salud.py + el enganche en _emitir_si_cambia: ack silencia (voz 'Visto, en ello'),
    no re-nag mientras el acuse siga fresco, y al despejarse la condición → un solo '✅ Resuelto'."""
    hb_dir = os.path.join(_TMP, "hb_acuse_unused")
    os.makedirs(hb_dir, exist_ok=True)
    hc.HB_DIR = hb_dir

    # limpia estado de _emitir_si_cambia + acuses para este bloque
    for fn in os.listdir(hc.HC):
        os.remove(os.path.join(hc.HC, fn))
    _sent.clear()

    clave = "daemon_asistente_fallo"
    texto = "El barrido diario de Vega falló en su última pasada."

    # 1. Sin acuse previo, clave NUEVA → ACUSE AUTOMÁTICO genérico ("lo estoy mirando"), no el grito
    #    seco ni la voz "Visto — en ello" (esa es solo para acuses "por" != "healthcheck"/nota propia).
    r1 = hc._emitir_si_cambia([(clave, texto)])
    ok(r1 is True, "1ª vez sin acuse → avisa (con el acuse automático ya puesto)")
    ok(any(texto in m and "lo estoy mirando" in m and "🔧" in m for m in _sent),
       "sin acuse previo, el aviso automático ya dice 'lo estoy mirando' (no un grito seco)")
    ok(salud.get(clave) is not None and salud.get(clave)["estado"] == "en_arreglo",
       "el acuse automático quedó registrado (en_arreglo)")
    ok(salud.get(clave)["por"] == "healthcheck", "el acuse automático genérico queda con por='healthcheck'")
    # …y desde el 25-jul-26 ese "lo estoy mirando" tiene detrás una investigación ENCOLADA de verdad
    # (hallazgo medio nº4: antes era una promesa vacía y {{TITULAR}} dejaba de vigilar la alerta). La nota
    # lleva el id del job, que es lo que la hace comprobable.
    ok("job" in (salud.get(clave)["nota"] or ""),
       "el acuse nombra el job de investigación encolado (la promesa es comprobable)")

    # 1b. Y si NO se puede encolar (HALT: el lazo está parado), NO se dice "lo estoy mirando" —
    #     sería la misma promesa vacía otra vez. Se dice la verdad.
    import salida as _sal
    _halted_orig = _sal.halted
    _sal.halted = lambda: True
    try:
        _sent.clear()
        hc._emitir_si_cambia([("clave_con_halt", "Algo se rompió con el lazo parado.")])
        ok(not any("lo estoy mirando" in m for m in _sent),
           "con HALT no se promete que alguien lo esté mirando")
        ok(any("no tengo ejecutor" in m for m in _sent),
           "con HALT el aviso dice la verdad: queda anotado y se retoma al volver")
    finally:
        _sal.halted = _halted_orig
        salud.purgar("clave_con_halt")
        for fn in os.listdir(hc.HC):
            os.remove(os.path.join(hc.HC, fn))
        _sent.clear()
        hc._emitir_si_cambia([(clave, texto)])   # restaura el escenario del punto 2

    # 2. Tras `salud ack` MANUAL (una sesión humana ya se puso), la MISMA alerta (persistente, aún sin
    #    pasar el cooldown) debe hablar con la voz de "Visto — en ello" (pisa el auto-ack genérico).
    #    Forzamos que "haya algo que entregar" con una alerta NUEVA acompañante, y comprobamos que la
    #    acusada usa la voz manual, mientras la nueva recibe SU PROPIO acuse automático.
    salud.ack(clave, "investigando el heartbeat clavado", por="Claude-test")
    r2 = hc._emitir_si_cambia([(clave, texto), ("otra_clave_nueva", "otra cosa nueva")])
    ok(r2 is True, "hay una alerta NUEVA junto a la acusada → sí entrega (la nueva lo merece)")
    ok(any("✋ Visto" in m and "Claude-test" in m and "investigando" in m for m in _sent),
       "la clave con ack MANUAL habla con voz 'Visto — en ello (Claude-test...)', con su nota (pisa el auto-ack)")
    ok(any("otra cosa nueva" in m and "lo estoy mirando" in m for m in _sent),
       "la clave NUEVA sin ack manual recibe su propio acuse automático ('lo estoy mirando')")

    # 3. Mientras el acuse siga FRESCO y no haya nada más nuevo → no re-nag (ni siquiera con la voz
    #    de "Visto"): dentro de la ventana de cooldown, sin alertas nuevas, se calla del todo.
    #    Mantiene "otra_clave_nueva" presente todavía (si la quitáramos aquí, empezaría su propio
    #    ciclo de cierre y contaminaría el escenario limpio del punto 4).
    n_antes = len(_sent)
    r3 = hc._emitir_si_cambia([(clave, texto), ("otra_clave_nueva", "otra cosa nueva")])
    ok(r3 is False, "misma alerta acusada, sin nada nuevo, dentro de la ventana → NO re-nag")
    ok(len(_sent) == n_antes, "no se mandó ningún mensaje nuevo")

    # 4. Se despejan AMBAS condiciones (ya no aparecen) → tras DOS ciclos seguidos sin ellas,
    #    un solo "✅ Resuelto" (con las dos etiquetas), y el acuse de la primera se purga.
    ok(hc._emitir_si_cambia([]) is False, "1er ciclo sin ninguna alerta → aún pendiente de confirmar")
    n_antes = len(_sent)
    r4 = hc._emitir_si_cambia([])
    ok(r4 is True, "2º ciclo seguido sin ninguna → confirma resuelto")
    ok(len(_sent) - n_antes == 1, "el cierre confirmado, sin nada más activo, entrega UN solo mensaje")
    ok(any(m.startswith("✅ Resuelto") for m in _sent[n_antes:]), "el mensaje de cierre lleva '✅ Resuelto'")
    ok(salud.get(clave) is None, "el acuse se purgó al confirmarse el cierre")


def acuse_envejece_vuelve_a_escalar_tests():
    """Un acuse 'en_arreglo' que envejece más allá de ACUSE_VENTANA_H sin resolverse deja de
    silenciar — vuelve a hablar con la voz normal (no confiar en un ack viejo para siempre)."""
    for fn in os.listdir(hc.HC):
        os.remove(os.path.join(hc.HC, fn))
    _sent.clear()

    clave = "daemon_correo_triaje_fallo_test"
    texto = "correo-triaje falló (prueba de envejecimiento del acuse)."
    salud.ack(clave, "mirándolo", por="Claude-test")
    # Envejece el acuse manualmente más allá de la ventana.
    d = salud._load()
    d[clave]["visto_ts"] = time.time() - (hc.ACUSE_VENTANA_H + 1) * 3600
    salud._write_atomic(salud.ACUSES, d)

    r = hc._emitir_si_cambia([(clave, texto)])
    ok(r is True, "acuse vencido + alerta persistente pasada la ventana → vuelve a hablar")
    ok(any(texto in m and "Visto" not in m for m in _sent),
       "con el acuse vencido, la voz vuelve a ser la normal (no 'Visto')")


def daemon_fallando_supersede_tests():
    """SUPERSEDE en el chequeo por CÓDIGO DE SALIDA (daemon_fallando, exit≠0 vía `launchctl`):
    misma regla que ya usan los chequeos por latido — si observabilidad tiene una pasada 'ok' MÁS
    RECIENTE que 'fail' para el BTP_AGENT real detrás del label (com.btp.correo → asistente, vía
    _obs_agente_de_label leyendo EnvironmentVariables.BTP_AGENT de los plists), el fallo queda
    TAPADO. Sin esa 'ok' (o con un 'fail' más reciente que cualquier 'ok') → sigue alertando."""
    import cost_guard
    hc.KICKSTART_WAIT = 0
    orig = (hc._launchctl_estado, hc._daemons_keepalive, hc._daemons_roster, hc._parked,
            hc._kickstart_daemon, cost_guard.check_before_job, hc._obs_agente_de_label)
    cost_guard.check_before_job = lambda *a, **k: (True, "test con saldo", 0.0)
    hc._parked = lambda: set()
    hc._daemons_keepalive = lambda: set()
    hc._daemons_roster = lambda: {"com.btp.correo"}
    hc._kickstart_daemon = lambda label: True   # el intento de autofix no debe afectar la decisión de alertar
    hc._obs_agente_de_label = lambda: {"com.btp.correo": "asistente"}

    def claves(al):
        return [a[0] if isinstance(a, tuple) else a for a in al]

    # CASO REAL (3/7): exit 1 en `launchctl list`, pero observabilidad tiene una 'ok' de 'asistente'
    # (el BTP_AGENT real de com.btp.correo) MÁS RECIENTE que cualquier 'fail' conocido → NO alerta.
    _limpiar_obs()
    hc._launchctl_estado = lambda: {"com.btp.correo": ("-", "1")}
    _obs_escribir("asistente", "fail", hace_horas=2.0)
    _obs_escribir("asistente", "ok", hace_horas=0.5)
    al, info = hc._check_roster_daemons()
    ok("daemon_fallando:com.btp.correo" not in claves(al),
       "exit 1 + 'ok' posterior de asistente en observabilidad → NO alerta (supersedido)")
    ok(info.get("fallando_supersedido_por_ok_posterior", {}).get("com.btp.correo") == "asistente",
       "info marca el supersede con el agente real")

    # Sin ninguna 'ok' posterior (nunca se recuperó) → SÍ alerta — el supersede no enmascara un
    # fallo real que se repite.
    _limpiar_obs()
    hc._launchctl_estado = lambda: {"com.btp.correo": ("-", "1")}
    al, info = hc._check_roster_daemons()
    ok("daemon_fallando:com.btp.correo" in claves(al),
       "exit 1 SIN ninguna 'ok' en observabilidad → sigue alertando (no se enmascara)")
    ok("com.btp.correo" not in info.get("fallando_supersedido_por_ok_posterior", {}),
       "sin ok posterior, no se marca como supersedido")

    # La última traza conocida es 'fail' (más reciente que la 'ok' vieja) → sigue siendo problema.
    _limpiar_obs()
    hc._launchctl_estado = lambda: {"com.btp.correo": ("-", "1")}
    _obs_escribir("asistente", "ok", hace_horas=3.0)
    _obs_escribir("asistente", "fail", hace_horas=0.2)
    al, _ = hc._check_roster_daemons()
    ok("daemon_fallando:com.btp.correo" in claves(al),
       "la traza MÁS RECIENTE es 'fail' (aunque hubo un 'ok' más viejo) → sigue alertando")

    # Un label SIN BTP_AGENT declarado (no corre agéntico) no tiene traza que comparar → se alerta
    # igual (conservador, sin cambio de comportamiento para daemons no-agénticos).
    _limpiar_obs()
    hc._obs_agente_de_label = lambda: {}
    hc._launchctl_estado = lambda: {"com.btp.correo": ("-", "1")}
    al, _ = hc._check_roster_daemons()
    ok("daemon_fallando:com.btp.correo" in claves(al),
       "label sin BTP_AGENT mapeado → sin supersede posible, se alerta igual")

    _limpiar_obs()
    (hc._launchctl_estado, hc._daemons_keepalive, hc._daemons_roster, hc._parked,
     hc._kickstart_daemon, cost_guard.check_before_job, hc._obs_agente_de_label) = orig


def acuse_automatico_autofix_tests():
    """El PRIMER aviso ya lleva el 'me pongo': cuando `_check_roster_daemons` intentó un autofix
    (kickstart) para daemon_fallando, deja el acuse por='autofix' ANTES de que `_emitir_si_cambia`
    decida la voz — el mensaje sale "🔧 ... y me pongo a arreglarlo (reinicio automático)", no el
    genérico "lo estoy mirando". Al despejarse la condición → un solo '✅ Resuelto'."""
    import cost_guard
    hc.KICKSTART_WAIT = 0
    orig = (hc._launchctl_estado, hc._daemons_keepalive, hc._daemons_roster, hc._parked,
            hc._kickstart_daemon, cost_guard.check_before_job, hc._obs_agente_de_label)
    cost_guard.check_before_job = lambda *a, **k: (True, "test con saldo", 0.0)
    hc._parked = lambda: set()
    hc._daemons_keepalive = lambda: set()
    hc._daemons_roster = lambda: {"com.btp.correo"}
    hc._kickstart_daemon = lambda label: True
    hc._obs_agente_de_label = lambda: {}   # sin mapeo → sin supersede, solo probamos el autofix+acuse
    _limpiar_obs()

    for fn in os.listdir(hc.HC):
        os.remove(os.path.join(hc.HC, fn))
    _sent.clear()

    hc._launchctl_estado = lambda: {"com.btp.correo": ("-", "1")}
    al, info = hc._check_roster_daemons()
    clave = "daemon_fallando:com.btp.correo"
    ok(salud.get(clave) is not None and salud.get(clave)["por"] == "autofix",
       "_check_roster_daemons deja el acuse por='autofix' tras intentar el kickstart")

    r = hc._emitir_si_cambia(al)
    ok(r is True, "1er aviso con autofix ya intentado → se entrega")
    ok(any("me pongo a arreglarlo" in m and "reinicio automático" in m for m in _sent),
       "la voz del aviso es '... y me pongo a arreglarlo (reinicio automático)', no el genérico")
    ok(not any("lo estoy mirando" in m for m in _sent),
       "con autofix ya intentado, NO sale la voz genérica 'lo estoy mirando'")

    # Se despeja (el daemon vuelve a correr bien) → tras 2 ciclos sin la condición, un solo Resuelto.
    hc._launchctl_estado = lambda: {}
    hc._daemons_roster = lambda: set()
    ok(hc._emitir_si_cambia([]) is False, "1er ciclo sin la condición → aún pendiente de confirmar")
    n_antes = len(_sent)
    r2 = hc._emitir_si_cambia([])
    ok(r2 is True, "2º ciclo seguido sin la condición → confirma resuelto")
    ok(any(m.startswith("✅ Resuelto") for m in _sent[n_antes:]), "el cierre lleva '✅ Resuelto'")
    ok(salud.get(clave) is None, "el acuse se purgó al confirmarse el cierre")

    _limpiar_obs()
    (hc._launchctl_estado, hc._daemons_keepalive, hc._daemons_roster, hc._parked,
     hc._kickstart_daemon, cost_guard.check_before_job, hc._obs_agente_de_label) = orig


def credito_cuenta_frescura_tests():
    """_credito_cuenta_bajo() ya exigía frescura del heartbeat ('credito_agotado' solo cuenta si es
    reciente); el matiz de esta tarea es que un blip de 'too low' que se RECUPERÓ (una pasada 'ok'
    posterior en observabilidad para el mismo agente agéntico) no debe seguir avisando 'recarga el
    crédito' — igual criterio de supersede que el resto (hubo_ok_desde)."""
    hb_dir = os.path.join(_TMP, "hb_credito")
    os.makedirs(hb_dir, exist_ok=True)
    hc.HB_DIR = hb_dir

    def clear():
        for f in os.listdir(hb_dir):
            os.remove(os.path.join(hb_dir, f))

    def hb_iso(agente, est, edad_h):
        ts = (datetime.datetime.utcnow() - datetime.timedelta(hours=edad_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
        json.dump({"agente": agente, "ts": ts, "estado": est, "modelo": "sonnet"},
                  open(os.path.join(hb_dir, agente + ".json"), "w"))

    # Heartbeat agéntico con 'credito_agotado' FRESCO y SIN pasada OK posterior → sigue siendo True
    # (comportamiento ya existente: guarda de frescura sola, sin recuperación conocida).
    _limpiar_obs(); clear()
    hb_iso("asistente", "credito_agotado", 0.2)
    ok(hc._credito_cuenta_bajo() is True,
       "credito_agotado fresco, SIN ok posterior en observabilidad → sigue avisando (agotamiento real)")

    # El MISMO blip, pero con una pasada 'ok' posterior en observabilidad (se recuperó solo, como
    # el caso real del 1/7 a las 14:31) → _credito_cuenta_bajo() ya NO debe avisar.
    _limpiar_obs(); clear()
    hb_iso("asistente", "credito_agotado", 0.5)
    _obs_escribir("asistente", "ok", hace_horas=0.1)   # posterior al heartbeat agotado
    ok(hc._credito_cuenta_bajo() is False,
       "credito_agotado con una 'ok' posterior en observabilidad (blip recuperado) → NO avisa")

    # Un 'ok' ANTERIOR al heartbeat agotado (más viejo, con margen amplio frente a cualquier
    # desfase de huso horario entre el heartbeat UTC y la traza en hora local — mismo patrón que
    # salud_daemons_supersede_tests) no cuenta como recuperación.
    _limpiar_obs(); clear()
    hb_iso("asistente", "credito_agotado", 1.0)
    _obs_escribir("asistente", "ok", hace_horas=6.0)   # más viejo que el heartbeat agotado
    ok(hc._credito_cuenta_bajo() is True,
       "un 'ok' más VIEJO que el heartbeat agotado no cuenta como recuperación → sigue avisando")

    _limpiar_obs(); clear()


def cli_smoke_test():
    """CLI de salud.py: list/ack/resuelto no lanzan y devuelven código 0."""
    rc_list = salud.main(["list"])
    ok(rc_list == 0, "salud.py list → rc 0")
    rc_ack = salud.main(["ack", "clave_cli_test", "nota de prueba"])
    ok(rc_ack == 0, "salud.py ack → rc 0")
    ok(salud.get("clave_cli_test", ) is not None, "ack via CLI queda persistido")
    rc_res = salud.main(["resuelto", "clave_cli_test"])
    ok(rc_res == 0, "salud.py resuelto → rc 0")
    ok(salud.get("clave_cli_test")["estado"] == "resuelto", "resuelto via CLI actualiza el estado")


def salud_list_alertas_abiertas_tests():
    """`salud list` (3/7/26): antes solo miraba acuses.json — una alerta viva SIN acuse (nunca
    acusada, o su acuse purgado) hacía que dijera 'Sin acuses registrados' aunque hubiera algo vivo.
    Ahora listar_alertas_abiertas() lee last_alert_state-humano.json (lo último que se avisó) y el
    CLI combina ambas fuentes."""
    for fn in os.listdir(hc.HC):
        os.remove(os.path.join(hc.HC, fn))

    ok(salud.listar_alertas_abiertas() == [], "sin last_alert_state-humano.json → lista vacía")

    state_path = os.path.join(hc.HC, "last_alert_state-humano.json")
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), "claves": ["clave_viva"], "textos": {"clave_viva": "algo pasó"}},
                  f, ensure_ascii=False)
    abiertas = salud.listar_alertas_abiertas()
    ok(len(abiertas) == 1 and abiertas[0]["clave"] == "clave_viva",
       "una alerta en last_alert_state SIN acuse aparece en listar_alertas_abiertas()")
    ok(abiertas[0]["texto"] == "algo pasó", "el texto visible se recupera del estado guardado")

    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        salud.main(["list"])
    salida_cli = buf.getvalue()
    ok("clave_viva" in salida_cli and "SIN acuse" in salida_cli,
       "`salud list` muestra la alerta abierta aunque no tenga acuse (ya no dice 'Sin acuses registrados')")

    os.remove(state_path)


def alerta_operativa_resuelta_se_purga_tests():
    """Una alerta OPERATIVA resuelta se cierra en el libro, no solo las 'humanas'.

    El 13-sep-26 había 9 alertas `daemon_fallando:*` con 26 h en `en_arreglo`, los nueve
    daemons en exit=0 y el propio healthcheck diciendo «ok» de ellos en el mismo ciclo. La
    causa: la purga colgaba del `categoria == "humano"` que decide si se manda un Telegram.
    Las operativas no pasan por ahí, así que no se cerraban nunca. Una lista de alertas que
    ya no son ciertas es ruido, y el ruido enseña a no mirarla.
    """
    global _pass, _fail
    state_path = os.path.join(_TMP, "healthcheck", "alertas_op.json")
    for f in (state_path,):
        if os.path.exists(f):
            os.remove(f)
    try:
        salud.purgar("daemon_fallando:com.btp.prueba")
    except Exception:
        pass

    clave, texto = "daemon_fallando:com.btp.prueba", "el daemon de prueba lleva 3 fallos."

    # 1) se detecta: la alerta entra en el libro
    hc._emitir_si_cambia([(clave, texto)], categoria="operativo")
    salud.ack(clave, "autofix: kickstart", por="autofix")
    ok(salud.get(clave) is not None, "operativa: la alerta queda registrada en el libro")

    # 2) la condición se va. Hacen falta DOS lecturas limpias (anti-rebote), igual que en humano
    hc._emitir_si_cambia([], categoria="operativo")
    hc._emitir_si_cambia([], categoria="operativo")

    # 3) el libro tiene que quedar limpio, aunque NUNCA se mandara un Telegram de «✅ Resuelto»
    ok(salud.get(clave) is None,
       "operativa: la condición se fue en 2 lecturas → la alerta se PURGA del libro")



def main():
    supersede_hubo_ok_desde_tests()
    salud_daemons_supersede_tests()
    frescura_supersede_tests()
    acuse_tests()
    acuse_envejece_vuelve_a_escalar_tests()
    daemon_fallando_supersede_tests()
    acuse_automatico_autofix_tests()
    credito_cuenta_frescura_tests()
    salud_list_alertas_abiertas_tests()
    alerta_operativa_resuelta_se_purga_tests()
    cli_smoke_test()
    print("RESULTADO healthcheck_acuse (supersede + acuse): %d OK, %d fallos" % (_pass, _fail))
    print("✅ HEALTHCHECK_ACUSE EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
