#!/usr/bin/env python3
"""test_vigia.py — el VIGÍA caza las malas respuestas y se arregla/escala (plan polished-swimming-deer).

Replica los 3 fallos REALES de hoy (24/6):
  (a) respuesta floja "no está en tus datos" → detectada como necesita_codigo (la reviso yo).
  (b) job "y" aplazado en bucle → detectado auto_arreglable → RETIRADO (a failed/).
  (c) acción real aplazada en bucle → detectada como 'informar' (espera al cerebro principal, NO se retira).
+ dedup (no repite) + _respuesta_floja del respondedor + el AVISO a {{TITULAR}} (envío capturado).
Aislado (tmp; sin red, sin LLM; la salida se monkeypatchea, nunca toca Telegram de verdad).
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="vigia_test_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_LOGS_DIR"] = os.path.join(_TMP, "logs")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import vigia  # noqa: E402
import responder_con_datos as rc  # noqa: E402

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _mkdirs():
    for d in ("vigia", "logs", "queue/pending", "queue/processing", "queue/failed"):
        os.makedirs(os.path.join(_TMP, d), exist_ok=True)


def _job(job_id, msg):
    obj = {"id": job_id, "tipo": "triage", "procedencia": "telegram",
           "intencion": "CLASIFICADOR... Mensaje: <<<%s>>>" % msg}
    p = os.path.join(_TMP, "queue", "pending", "0-2026-%s.json" % job_id)
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    return p


def main():
    _mkdirs()
    # (a) respuesta floja reciente, marcada por el respondedor
    fecha = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    with open(os.path.join(_TMP, "vigia", "respuestas-%s.jsonl" % fecha), "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": vigia._iso(), "floja": True, "brain": "claude",
                            "deferred": False, "parado": False, "sensible": False,
                            "motivo": "ok", "sello": "abc123"}) + "\n")
    # (b) y (c) jobs aplazados en bucle (5 veces cada uno) en dispatcher.out
    with open(os.path.join(_TMP, "logs", "dispatcher.out"), "w", encoding="utf-8") as f:
        for _ in range(5):
            f.write("2026-06-24T09:00:00 job TRIV1 APLAZADO (rc=75) → requeue\n")
            f.write("2026-06-24T09:00:00 job REAL1 APLAZADO (rc=75) → requeue\n")
            f.write("2026-06-24T09:00:00 job GONE1 APLAZADO (rc=75) → requeue\n")  # ya resuelto: sin job en pending
    _job("TRIV1", "y")                               # trivial → auto_arreglable
    real_path = _job("REAL1", "investiga a la Dra. {{CONTACTO}} y prepara el dossier")  # real → informar
    # GONE1: NO se crea fichero en pending → simula un job ya retirado/resuelto (líneas históricas)

    # ── ciclo 1 ──
    res = vigia.run()
    tipos = {a["tipo"]: a for a in res["nuevas"]}
    ok("respuesta_floja" in tipos, "(a) caza la respuesta floja")
    ok(tipos.get("respuesta_floja", {}).get("clase") == "necesita_codigo", "(a) floja → necesita_codigo")
    bucles = [a for a in res["nuevas"] if a["tipo"] == "accion_en_bucle"]
    triv = [a for a in bucles if "TRIV1" in a["key"]]
    real = [a for a in bucles if "REAL1" in a["key"]]
    ok(triv and triv[0]["clase"] == "auto_arreglable", "(b) job trivial en bucle → auto_arreglable")
    ok(triv and triv[0].get("auto_resuelta") is True, "(b) auto-arreglado (retirado)")
    ok(not os.path.exists(os.path.join(_TMP, "queue", "pending", "0-2026-TRIV1.json")),
       "(b) el job trivial ya NO está en pending")
    ok(os.path.exists(os.path.join(_TMP, "queue", "failed", "0-2026-TRIV1.json")),
       "(b) el job trivial está en failed/ (reversible)")
    ok(real and real[0]["clase"] == "informar", "(c) acción real en bucle → informar (NO se retira)")
    ok(os.path.exists(real_path), "(c) el job real SIGUE en pending (no se toca)")
    ok(not any("GONE1" in a["key"] for a in res["nuevas"]),
       "(d) job YA resuelto (no en pending) → NO se flaguea (sin falsos positivos por log histórico)")
    ok(os.path.exists(vigia.ANOMALIAS), "registra las anomalías en anomalias.jsonl")

    # ── ciclo 2: dedup (nada nuevo) — ANTES de tocar más logs, para probar el dedup limpio ──
    res2 = vigia.run()
    ok(len(res2["nuevas"]) == 0, "dedup: el 2º ciclo no repite anomalías")

    # crash-loop: reinicios LIMPIOS (recarga mía / blip de red) NO disparan; CON errores reales SÍ
    errp = os.path.join(_TMP, "logs", "bot-telegram.err")
    with open(errp, "w", encoding="utf-8") as f:
        f.write("\n".join(["bot_telegram: daemon arriba (long-poll)"] * 6
                          + ["salida.poll_updates: aviso de red: timeout"] * 3))
    ok(vigia.detectar_crash_loop() == [], "crash: reinicios limpios (recarga/red) → NO es crash (sin falso positivo)")
    with open(errp, "a", encoding="utf-8") as f:
        f.write("\nTraceback (most recent call last):\nImportError: boom\n")
    ok(len(vigia.detectar_crash_loop()) == 1, "crash: reinicios + errores reales → SÍ es crash")

    # ── _respuesta_floja del respondedor ──
    ok(rc._respuesta_floja("Ese enlace no aparece en los datos que me has pasado") is True,
       "responder: 'no aparece en los datos' = floja")
    ok(rc._respuesta_floja("no tengo acceso a tus tareas") is True, "responder: 'no tengo acceso' = floja")
    ok(rc._respuesta_floja("") is True, "responder: vacío = floja")
    ok(rc._respuesta_floja("🔴 Biopsia Zúrich 8-jul: confirma la muestra a Harvard.") is False,
       "responder: respuesta con datos reales NO es floja")

    # ── aviso a {{TITULAR}} (fail-LOUD con anti-spam) — envío CAPTURADO, nunca toca la red ──
    import types
    enviados = []
    fake = types.ModuleType("salida")
    fake.report_to_titular = lambda text, **kw: enviados.append(text)
    sys.modules["salida"] = fake                      # _avisar hace `import salida` → coge este
    vigia.ALERTA_STATE = os.path.join(_TMP, "vigia", "last_alert_test.json")
    try:
        os.remove(vigia.ALERTA_STATE)
    except OSError:
        pass
    anom_bot = [{"tipo": "bot_inestable", "key": "crash:2026-06-26T21", "clase": "necesita_codigo"}]
    ok(vigia._avisar_a_titular(anom_bot) is True, "aviso: una anomalía nueva → avisa")
    ok(len(enviados) == 1, "aviso: se mandó exactamente 1 mensaje")
    ok(enviados and "bucle" in enviados[0] and "para_codigo" not in enviados[0] and "necesita_codigo" not in enviados[0],
       "aviso: texto en llano, sin jerga interna")
    ok(vigia._avisar_a_titular(anom_bot) is False, "aviso: la MISMA anomalía dentro de 12h → silencio (anti-spam)")
    ok(len(enviados) == 1, "aviso: no hubo segundo envío")
    auto = [{"tipo": "accion_en_bucle", "key": "bucle:Z", "clase": "auto_arreglable", "auto_resuelta": True}]
    ok(vigia._avisar_a_titular(auto) is False, "aviso: lo que el vigía ya auto-resolvió NO se avisa")

    # ── B (watch-the-watcher): el vigía deja su latido y vigila a healthcheck ──
    import datetime as _dt
    ok(os.path.exists(vigia.LAST_RUN), "B: el vigía deja last_run.json al correr (latido para healthcheck)")
    vigia.HC_LAST_CHECK = os.path.join(_TMP, "hc_last_check.json")
    try:
        os.remove(vigia.HC_LAST_CHECK)
    except OSError:
        pass
    ok(vigia.detectar_healthcheck_parado() == [], "B: healthcheck sin baseline → silencio (sin falso positivo)")
    json.dump({"ts": _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}, open(vigia.HC_LAST_CHECK, "w"))
    ok(vigia.detectar_healthcheck_parado() == [], "B: healthcheck fresco → silencio")
    viejo = (_dt.datetime.now() - _dt.timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%S")
    json.dump({"ts": viejo}, open(vigia.HC_LAST_CHECK, "w"))
    out = vigia.detectar_healthcheck_parado()
    ok(len(out) == 1 and out[0]["tipo"] == "healthcheck_parado", "B: healthcheck parado > umbral → anomalía")

    # ── Pieza 2: auto-curación del healthcheck (intentar → verificar → escalar solo si no revive) ──
    os.environ["BTP_VIGIA_NO_KICKSTART"] = "1"          # NO tocar launchctl de verdad en el test
    vigia.AUTOFIX = os.path.join(_TMP, "vigia", "autofix_test.json")
    try:
        os.remove(vigia.AUTOFIX)
    except OSError:
        pass
    anom_hc = {"tipo": "healthcheck_parado", "clase": "informar",
               "key": "healthcheck_parado:2026-07-11T18", "detalle": "healthcheck lleva 90 min sin correr"}
    a1 = vigia._autoheal_healthcheck(dict(anom_hc))
    ok(a1["clase"] == "auto_reintento" and a1["escalar_humano"] is False,
       "hc: 1er intento → reanima y NO escala (le da un ciclo)")
    ok(a1.get("auto_resuelta") is True, "hc: 1er intento lanza el kickstart (simulado)")
    ok(json.load(open(vigia.AUTOFIX))["healthcheck_parado"]["n"] == 1, "hc: presupuesto de intentos gastado (n=1)")
    a2 = vigia._autoheal_healthcheck(dict(anom_hc))
    ok(a2["clase"] == "informar" and a2["escalar_humano"] is True,
       "hc: presupuesto agotado y sigue caído → escala a {{TITULAR}} (watch-the-watcher)")
    ok("ya intenté reiniciarlo" in a2["detalle"], "hc: el aviso deja constancia de que ya lo intenté")

    # el reintento en curso NO molesta a {{TITULAR}}; el escalado SÍ, con categoría 'humano'
    del enviados[:]
    captura_cat = []
    fake.report_to_titular = lambda text, **kw: (enviados.append(text), captura_cat.append(kw.get("categoria")))
    vigia.ALERTA_STATE = os.path.join(_TMP, "vigia", "last_alert_hc.json")
    try:
        os.remove(vigia.ALERTA_STATE)
    except OSError:
        pass
    ok(vigia._avisar_a_titular([a1]) is False, "hc: un reintento en curso NO manda aviso (anti-spam del 'dormido')")
    ok(vigia._avisar_a_titular([a2]) is True, "hc: el escalado (agotado) SÍ avisa")
    ok(captura_cat and captura_cat[-1] == "humano", "hc: el escalado va con categoría 'humano'")

    # ── boot-grace: recién arrancado → no es fallo aunque last_check sea viejo (mata el 'portátil dormido') ──
    _orig_up = vigia._uptime_seg
    vigia._uptime_seg = lambda: 60.0                    # 1 min despierto → aún no ha tocado el healthcheck
    ok(vigia.detectar_healthcheck_parado() == [], "boot-grace: recién arrancado → no dispara")
    vigia._uptime_seg = lambda: 10 * 3600.0             # 10 h despierto → ya debería haber corrido
    ok(len(vigia.detectar_healthcheck_parado()) == 1, "boot-grace: despierto rato y viejo → sí dispara")
    vigia._uptime_seg = _orig_up

    # ── revive: kickstart falla porque el job está disabled/descargado (un CÓDIGO ROJO hizo unload -w) →
    #    enable+bootstrap+kickstart lo revive. Sin esto el autoheal escala en vano (kickstart no basta) ──
    os.environ.pop("BTP_VIGIA_NO_KICKSTART", None)      # ejercita el camino REAL (con subprocess mockeado)
    import subprocess as _sub
    _orig_run, _orig_exists = _sub.run, os.path.exists
    _llamadas = []

    class _R:
        def __init__(self, rc, err=""):
            self.returncode, self.stderr, self.stdout = rc, err, ""

    def _fake_run(cmd, **kw):
        _llamadas.append(cmd)
        if cmd[:2] == ["launchctl", "kickstart"]:       # 1er kickstart falla (disabled); el 2º va OK
            es_primero = sum(1 for c in _llamadas if c[:2] == ["launchctl", "kickstart"]) == 1
            return _R(113, "Could not find service") if es_primero else _R(0)
        return _R(0)                                    # enable / bootstrap → OK

    os.path.exists = lambda p: True if str(p).endswith("com.btp.healthcheck.plist") else _orig_exists(p)
    _sub.run = _fake_run
    try:
        okk, desc = vigia._kickstart_healthcheck()
    finally:
        _sub.run, os.path.exists = _orig_run, _orig_exists
        os.environ["BTP_VIGIA_NO_KICKSTART"] = "1"
    ok(okk is True and "revivido" in desc, "revive: kickstart falla (disabled) → enable+bootstrap+kickstart lo revive")
    ok(any(c[:2] == ["launchctl", "enable"] for c in _llamadas)
       and any(c[:2] == ["launchctl", "bootstrap"] for c in _llamadas),
       "revive: se ejecuta enable + bootstrap antes del 2º kickstart")

    # ── Pieza 1: el puente vigía → cola (lo que necesita CÓDIGO se encola para el cerebro principal) ──
    import cola  # noqa: F401
    os.environ.pop("BTP_VIGIA_ENCOLAR", None)
    pend_dir = os.path.join(_TMP, "queue", "pending")
    antes = len(os.listdir(pend_dir))
    vigia._encolar_para_codigo([
        {"tipo": "respuesta_floja", "clase": "necesita_codigo", "detalle": "no encontró datos", "key": "floja:zzz"},
        {"tipo": "accion_en_bucle", "clase": "informar", "detalle": "x", "key": "bucle:zzz"},  # NO es código
    ])
    despues = os.listdir(pend_dir)
    ok(len(despues) == antes + 1, "puente: SOLO lo 'necesita_codigo' se encola (lo 'informar' no)")
    job_vigia = None
    for f in despues:
        j = json.load(open(os.path.join(pend_dir, f)))
        if j.get("procedencia") == "vigia":
            job_vigia = j
    ok(job_vigia is not None and "[vigía]" in job_vigia.get("intencion", ""),
       "puente: el job encolado viene del vigía (lo recogerá Claude en Polaris)")
    # 20-sep-26: el encargo declara QUÉ tiene que quedar escrito, o el dispatcher no lo cierra.
    # Se busca ESTE job por su clave: en la carpeta hay más jobs del vigía de tramos anteriores.
    este = None
    for f in despues:
        j = json.load(open(os.path.join(pend_dir, f)))
        if j.get("procedencia") == "vigia" and "floja:zzz" in j.get("intencion", ""):
            este = j
    ok((este or {}).get("prueba") == {"tipo": "deuda", "clave": "vigia:floja:zzz"},
       "puente: el job declara su entregable (anotación en el libro con clave estable)")
    ok("arréglalo" not in (este or {}).get("intencion", "").lower()
       and "déjalo escrito" in (este or {}).get("intencion", "").lower(),
       "puente: no se pide lo imposible (el muro no deja escribir código en ningún perfil)")
    os.environ["BTP_VIGIA_ENCOLAR"] = "0"
    antes2 = len(os.listdir(pend_dir))
    vigia._encolar_para_codigo([{"tipo": "respuesta_floja", "clase": "necesita_codigo", "detalle": "x", "key": "floja:yyy"}])
    ok(len(os.listdir(pend_dir)) == antes2, "puente: BTP_VIGIA_ENCOLAR=0 lo desactiva")
    os.environ.pop("BTP_VIGIA_ENCOLAR", None)

    print("RESULTADO vigía: %d OK, %d fallos" % (_pass, _fail))
    print("✅ VIGÍA EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
