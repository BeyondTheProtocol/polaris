#!/usr/bin/env python3
"""test_run_agent_f2.py — F2: cableado run_agent.sh ↔ centralita (plan expressive-plotting-flame).

Verifica que cuando Claude está agotado (límite/sin saldo), las rutinas NO-clínicas relevan a la
CENTRALITA (cerebro de respaldo, tras el borde) en vez de aplazar a secas, y que el carril CLÍNICO
sigue aplazando SIEMPRE (nunca cae a un cerebro de nube). Ganchos: BTP_CLAUDE_BIN (claude falso),
BTP_API_KEY_OVERRIDE, BTP_IA_FAKE (respuesta de cerebro falsa, sin tocar borde/routing).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="ra_f2_")
os.makedirs(os.path.join(_TMP, "borde"), exist_ok=True)
_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _clean_env(**overrides):
    """Entorno HERMÉTICO para el subproceso: parte del de este proceso pero BORRA todo lo que
    empiece por BTP_ (y MURO_PROFILE) antes de aplicar lo que el test quiere fijar.

    Por qué (25-jul-26): antes se hacía `dict(os.environ, …)`, que solo AÑADE. Eso es hermético en
    CI o en `test_all.sh` (entorno limpio), pero deja de serlo cuando la batería corre DENTRO de una
    sesión real que ya trae esas variables puestas — p. ej. la propia auto-mejora, cuyo plist trae
    `BTP_MAX_TURNS=80` y `BTP_AVISA_APLAZO=1`. El valor real se filtraba y pisaba el DEFAULT que el
    test creía estar midiendo: 4 fallos fantasma con `run_agent.sh` intacto (reproducido con las 4
    combinaciones; ver memoria reference-test-run-agent-f2-entorno-heredado). Borrar el prefijo
    entero, y no solo las dos variables culpables, evita que una variable BTP_ nueva reviva el bug.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("BTP_") and k != "MURO_PROFILE"}
    env.update(overrides)
    return env


# claude falso: 429 (límite) en todos los modelos → cadena agotada
_BIN = os.path.join(_TMP, "claude_429.sh")
open(_BIN, "w").write('#!/bin/bash\necho \'{"api_error_status": 429, "is_error": true}\'\n')
os.chmod(_BIN, 0o755)
# claude falso: 400 "Credit balance is too low" → saldo de PREPAGO agotado (lo detecta is_credit_out
# POST-API; ≠ tope local de cost_guard, que se topa PRE-API). Para probar el aviso 'recarga'.
_BIN_CREDIT = os.path.join(_TMP, "claude_credit.sh")
open(_BIN_CREDIT, "w").write(
    '#!/bin/bash\necho \'{"api_error_status": 400, "is_error": true, "result": "Credit balance is too low"}\'\n')
os.chmod(_BIN_CREDIT, 0o755)
# registro con un solo cerebro de NUBE (no confianza) → para lo sensible NO hay candidato
_REG = os.path.join(_TMP, "peripheries.json")
json.dump({"cerebros": [{"name": "nvidia-free", "kind": "carril_gratis", "destino": "nvidia",
                         "trusted": False, "free": True, "orden": 10, "enabled": True}]},
          open(_REG, "w"))

# SALUD (subproceso): run_agent corre en otro proceso, el monkeypatch en-proceso no llega.
# Sembramos una cache de salud FRESCA que marca el free-lane disponible, para que el gate no lo
# filtre por falta de clave NVIDIA en la mini headless (rojo AMBIENTAL). Ver reference-ia-ask-gate-salud-en-tests.
os.makedirs(os.path.join(_TMP, "ia"), exist_ok=True)
json.dump({"ts": "test", "cerebros": [{"name": "nvidia-free", "disponible": True},
                                     {"name": "claude", "disponible": True}]},
          open(os.path.join(_TMP, "ia", "health.json"), "w"))

# claude falso: SIEMPRE responde OK (saldo real sano) — para probar que lo crítico SALTA el tope
# interno cuando el dinero de verdad está ahí (regla de {{TITULAR}}, 2/7/26).
_BIN_OK = os.path.join(_TMP, "claude_ok.sh")
open(_BIN_OK, "w").write(
    '#!/bin/bash\necho \'{"subtype":"success","is_error":false,"total_cost_usd":0.01,"result":"ok-real"}\'\n')
os.chmod(_BIN_OK, 0o755)
# claude falso que GRABA sus args (para afirmar --max-turns) y responde OK (run normal, no aplazo)
_BIN_REC = os.path.join(_TMP, "claude_rec.sh")
_ARGFILE = os.path.join(_TMP, "claude_args.txt")
open(_BIN_REC, "w").write(
    '#!/bin/bash\nprintf "%s\\n" "$*" > "' + _ARGFILE + '"\n'
    'echo \'{"subtype":"success","is_error":false,"total_cost_usd":0.01,"result":"ok"}\'\n')
os.chmod(_BIN_REC, 0o755)
# claude falso: JSON de ÉXITO pero el PROCESO sale con rc=1 (12-sep-2026, jobs_caidos
# "rc=1 · success · N turnos") — simula un hook PreToolUse del muro que deniega una herramienta
# a mitad de turno: el modelo termina igual con un resultado válido, pero el binario `claude`
# puede salir con rc≠0. run_agent.sh debe fiarse del JSON (is_error:false), no del rc crudo.
_BIN_RC1_EXITO = os.path.join(_TMP, "claude_rc1_exito.sh")
open(_BIN_RC1_EXITO, "w").write(
    '#!/bin/bash\necho \'{"subtype":"success","is_error":false,"num_turns":1,'
    '"total_cost_usd":0.001,"result":"listo (con una denegación del muro por medio)"}\'\n'
    'exit 1\n')
os.chmod(_BIN_RC1_EXITO, 0o755)


def run_agent(prompt, agent, ia_fake="DESDE-CENTRALITA", free_ok=False, criticidad=None):
    env = _clean_env(BTP_CLAUDE_BIN=_BIN, BTP_API_KEY_OVERRIDE="x",
               BTP_COST_GUARDED="1", BTP_STATE_DIR=_TMP, BTP_PERIPHERIES=_REG,
               BTP_IA_FAKE=ia_fake, BTP_AGENT=agent, BTP_MODEL="sonnet", BTP_REPO=ROOT,
               BTP_HALT_FILES=os.path.join(_TMP, "nh_a") + ":" + os.path.join(_TMP, "nh_b"))
    if free_ok:
        env["BTP_FREE_OK"] = "1"
    if criticidad:
        env["BTP_CRITICIDAD"] = criticidad
    p = subprocess.run(["bash", os.path.join(ROOT, "tools", "run_agent.sh"), prompt],
                       capture_output=True, text=True, env=env)
    hb = {}
    try:
        hb = json.load(open(os.path.join(_TMP, "heartbeat", agent + ".json")))
    except Exception:
        pass
    return p.stdout, p.stderr, hb.get("estado"), p.returncode


def _turnos_de(agent, criticidad=None, max_turns_env=None):
    """Lanza run_agent con un claude que graba sus args y devuelve el --max-turns que recibió."""
    import re
    env = _clean_env(BTP_CLAUDE_BIN=_BIN_REC, BTP_API_KEY_OVERRIDE="x",
               BTP_COST_GUARDED="1", BTP_STATE_DIR=_TMP, BTP_PERIPHERIES=_REG,
               BTP_AGENT=agent, BTP_MODEL="sonnet", BTP_REPO=ROOT,
               BTP_HALT_FILES=os.path.join(_TMP, "nh_a") + ":" + os.path.join(_TMP, "nh_b"))
    if criticidad:
        env["BTP_CRITICIDAD"] = criticidad
    if max_turns_env:
        env["BTP_MAX_TURNS"] = max_turns_env
    subprocess.run(["bash", os.path.join(ROOT, "tools", "run_agent.sh"), "haz algo"],
                   capture_output=True, text=True, env=env)
    try:
        m = re.search(r"--max-turns (\d+)", open(_ARGFILE).read())
        return int(m.group(1)) if m else None
    except OSError:
        return None


def exito_real_no_depende_de_rc_tests():
    """12-sep-2026, daemon_fallando gigantes (enviar-hoy 1266x, bot-telegram 1257x) y
    jobs_caidos "rc=1 · success · N turnos" (35/40 de tools/state/queue/failed/): el binario
    `claude` puede salir con rc≠0 aunque su propio JSON diga is_error:false (un hook del muro
    denegó una herramienta a mitad de turno, sin impedir que el modelo entregara un resultado
    válido). run_agent.sh debe exit 0 en ese caso — el dispatcher no puede tratar un no-op
    (el muro hizo su trabajo) como una avería."""
    env = _clean_env(BTP_CLAUDE_BIN=_BIN_RC1_EXITO, BTP_API_KEY_OVERRIDE="x",
                      BTP_COST_GUARDED="1", BTP_STATE_DIR=_TMP, BTP_PERIPHERIES=_REG,
                      BTP_AGENT="tecnico", BTP_MODEL="sonnet", BTP_REPO=ROOT,
                      BTP_HALT_FILES=os.path.join(_TMP, "nh_a") + ":" + os.path.join(_TMP, "nh_b"))
    p = subprocess.run(["bash", os.path.join(ROOT, "tools", "run_agent.sh"), "diagnostica"],
                       capture_output=True, text=True, env=env)
    hb = {}
    try:
        hb = json.load(open(os.path.join(_TMP, "heartbeat", "tecnico.json")))
    except Exception:
        pass
    ok(p.returncode == 0,
       "JSON éxito con proceso rc=1 → run_agent.sh sale 0 (no rc=%d)" % p.returncode)
    ok(hb.get("estado") == "ok", "heartbeat 'ok', no 'fallo' (era %r)" % hb.get("estado"))
    ok('"is_error":false' in p.stdout or '"is_error": false' in p.stdout,
       "el JSON real del agente sigue intacto en stdout para el dispatcher")


def antifuga_tests():
    """ANTI-FUGA (25/6): la RUTINA acota turnos (no se va de madre); lo CLÍNICO/CRÍTICO mantiene
    margen amplio (no truncar el análisis); el override explícito manda. La tool Task ya la veta el
    muro, así que el coste de un run = su propio largo → acotar turnos de rutina tapa la fuga."""
    ok(_turnos_de("orquestador", criticidad="rutina") == 25, "RUTINA → --max-turns 25 (acota la fuga)")
    ok(_turnos_de("comite-medico") == 60, "CLÍNICO (comite-medico) → --max-turns 60 (no trunca el análisis)")
    ok(_turnos_de("orquestador", criticidad="critico") == 60, "CRÍTICO marcado → --max-turns 60")
    ok(_turnos_de("orquestador", criticidad="rutina", max_turns_env="40") == 40,
       "BTP_MAX_TURNS explícito (plist) manda sobre el default")


def _tmp_tope_minusculo(prefix):
    tmp = tempfile.mkdtemp(prefix=prefix)
    os.makedirs(os.path.join(tmp, "cost"), exist_ok=True)
    json.dump({"tope_diario_usd": 0.001, "tope_job_usd": 0.001, "tope_mensual_usd": 0.001},
              open(os.path.join(tmp, "cost", "limits.json"), "w"))          # tope minúsculo → check falla
    os.makedirs(os.path.join(tmp, "notif"), exist_ok=True)
    json.dump({"token": "x", "chat_id": "1"}, open(os.path.join(tmp, "notif", "config.json"), "w"))
    return tmp


def _run_comite_medico(tmp, claude_bin, extra_env=None):
    """comite-medico = clínico ⇒ crítico; SIN BTP_COST_GUARDED ⇒ el gate PRE-API de cost_guard corre
    (es justo el punto que se está probando: ¿el tope interno frena o no a lo clínico?)."""
    fake_salida = os.path.join(tmp, "salida_fake.py")
    msgfile = os.path.join(tmp, "msgs.txt")
    if not os.path.exists(fake_salida):
        open(fake_salida, "w").write(
            "import sys\nopen(%r,'a').write(' '.join(sys.argv[1:])+'\\n')\n" % msgfile)
    env = _clean_env(BTP_CLAUDE_BIN=claude_bin, BTP_API_KEY_OVERRIDE="x", BTP_STATE_DIR=tmp,
               BTP_PERIPHERIES=_REG, BTP_AGENT="comite-medico", BTP_MODEL="sonnet", BTP_REPO=ROOT,
               BTP_SALIDA=fake_salida, BTP_HALT_FILES=os.path.join(tmp, "a") + ":" + os.path.join(tmp, "b"))
    if extra_env:
        env.update(extra_env)
    p = subprocess.run(["bash", os.path.join(ROOT, "tools", "run_agent.sh"), "revisa el caso"],
                       capture_output=True, text=True, env=env)
    try:
        hb = json.load(open(os.path.join(tmp, "heartbeat", "comite-medico.json"))).get("estado")
    except Exception:
        hb = None
    msgs = open(msgfile).read() if os.path.exists(msgfile) else ""
    return p.stdout, p.returncode, hb, msgs, msgfile


def clinico_salta_tope_si_saldo_sano_tests():
    """🔴 Regla de {{TITULAR}} (2/7/26): lo CLÍNICO/CRÍTICO/SENSIBLE NO se corta por nuestro TOPE
    DIARIO interno cuando el saldo REAL de Anthropic está sano. Antes (25/6) una tarea crítica que
    topaba el cap se dejaba en PAUSA pidiendo aprobación — eso YA NO basta: ahora, si el saldo real
    funciona, ni siquiera pausa, sigue directa a Claude. El tope interno solo protege lo RUTINARIO.

    (1) CLÍNICA + tope diario topado + saldo REAL sano (_BIN_OK) → responde de verdad, NO bloquea.
    (2) RUTINARIA + mismo tope topado → sigue respetando el tope como siempre (comportamiento intacto).
    (3) CLÍNICA + saldo REAL agotado (400 'Credit balance is too low') → SÍ bloquea + aviso fuerte
        (el freno real sigue siendo el prepago de Anthropic, no nuestro cap)."""
    # (1) clínica, tope local topado, saldo real SANO → debe pasar y devolver la respuesta real.
    tmp1 = _tmp_tope_minusculo("ra_critico_saltatope_")
    out, rc, hb, msgs, _ = _run_comite_medico(tmp1, _BIN_OK)
    ok(rc == 0 and "ok-real" in out, "clínica + tope topado + saldo sano → NO bloquea, responde real")
    ok(hb not in ("critico_bloqueado", "aplazado_tope_local"),
       "clínica + tope topado + saldo sano → heartbeat NO es de bloqueo (%r)" % hb)
    ok("He PARADO una tarea" not in msgs, "clínica + tope topado + saldo sano → SIN alarma roja")

    # (2) rutina (orquestador, sin BTP_CRITICIDAD) con el MISMO tope minúsculo → sigue respetando
    #     el tope (disciplina de gasto intacta para lo NO crítico): aplaza, no llama a Claude.
    tmp2 = _tmp_tope_minusculo("ra_rutina_respeta_tope_")
    fake_salida2 = os.path.join(tmp2, "salida_fake.py")
    msgfile2 = os.path.join(tmp2, "msgs.txt")
    open(fake_salida2, "w").write(
        "import sys\nopen(%r,'a').write(' '.join(sys.argv[1:])+'\\n')\n" % msgfile2)
    env2 = _clean_env(BTP_CLAUDE_BIN=_BIN_OK, BTP_API_KEY_OVERRIDE="x", BTP_STATE_DIR=tmp2,
                BTP_PERIPHERIES=_REG, BTP_AGENT="orquestador", BTP_MODEL="sonnet", BTP_REPO=ROOT,
                BTP_SALIDA=fake_salida2,
                BTP_HALT_FILES=os.path.join(tmp2, "a") + ":" + os.path.join(tmp2, "b"))
    p2 = subprocess.run(["bash", os.path.join(ROOT, "tools", "run_agent.sh"), "tarea rutinaria"],
                        capture_output=True, text=True, env=env2)
    ok("ok-real" not in p2.stdout and p2.returncode == 75,
       "rutina + tope topado + saldo sano → SIGUE aplazando (el tope manda para lo NO crítico)")

    # (3) clínica + saldo REAL agotado (400) con el mismo tope minúsculo → SÍ bloquea (el freno
    #     legítimo es el prepago de Anthropic, que el tope-check no puede saltarse a ciegas).
    tmp3 = _tmp_tope_minusculo("ra_critico_prepago_real_")
    out, rc, hb, msgs, _ = _run_comite_medico(tmp3, _BIN_CREDIT)
    ok(rc == 75 and hb == "critico_bloqueado",
       "clínica + tope topado + saldo REAL agotado → SÍ bloquea (critico_bloqueado)")
    ok("recarga" in msgs.lower() or "saldo de prepago" in msgs.lower(),
       "clínica + prepago real agotado → el aviso pide RECARGAR (no 'sube')")


def aprobacion_gate_tests():
    """Gate de aprobación — caso residual: una tarea CRÍTICA que topa el cap Y además la API real
    falla por LÍMITE de tasa (429, reintentable, no es ni tope local ni prepago agotado) sigue
    dejándose en PAUSA con aviso FUERTE (no se corta en silencio, no se sirve con un cerebro flojo).
    (El texto exacto del aviso de "Claude no disponible" —límite o crédito— es un mensaje PREVIO a
    este cambio y no se toca aquí; lo que se afirma es que SIGUE avisando fuerte y respeta el
    anti-spam, no la redacción literal.)"""
    tmp = _tmp_tope_minusculo("ra_aprob_")

    def run_crit():
        return _run_comite_medico(tmp, _BIN)   # _BIN = 429 en todos los modelos (límite real)

    out, rc, hb, msgs, msgfile = run_crit()
    ok(rc == 75 and hb == "critico_bloqueado", "CAP + crítico + límite 429 real → exit 75 + critico_bloqueado")
    ok("report-urgente" in msgs and "PARADO" in msgs,
       "CAP + crítico + límite 429 real → aviso FUERTE (report-urgente, no en silencio)")
    n1 = msgs.count("report-urgente")
    run_crit()                                   # 2ª pasada inmediata (misma hora)
    msgs2 = open(msgfile).read() if os.path.exists(msgfile) else ""
    n2 = msgs2.count("report-urgente")
    ok(n2 == n1, "2ª pasada inmediata NO re-avisa (dedup, ventana 1h): %d→%d" % (n1, n2))

    # El dedup reusa ia._debe_avisar (namespace 'run_agent_critico:<agente>', cooldown 1h) — NO un
    # flag por-día: si retrocedemos el timestamp guardado más de 1h, SÍ debe re-avisar (ventana
    # corta real, no "1 vez al día"). Verifica que el mecanismo reusado es el correcto, no uno propio.
    aviso_json = os.path.join(tmp, "ia", "aviso_parado.json")
    ok(os.path.exists(aviso_json), "el dedup persiste en state/ia/aviso_parado.json (mecanismo reusado)")
    data = json.load(open(aviso_json))
    ok(any(k.startswith("run_agent_critico:comite-medico:") for k in data),
       "la clave de dedup usa el namespace 'run_agent_critico:<agente>' (ia._debe_avisar)")
    for k in list(data.keys()):
        if k.startswith("run_agent_critico:"):
            data[k] -= 2 * 3600                  # retrocede 2h → fuera de la ventana de 1h
    json.dump(data, open(aviso_json, "w"))
    run_crit()                                   # 3ª pasada, "1h+ después" → debe re-avisar
    msgs3 = open(msgfile).read() if os.path.exists(msgfile) else ""
    n3 = msgs3.count("report-urgente")
    ok(n3 == n2 + 1, "pasado el cooldown de 1h → SÍ vuelve a avisar (no es dedup de 1/día): %d→%d" % (n2, n3))


def _run_rutina(tmp, claude_bin, avisa=True, guarded=False):
    """auto-mejora = NO clínico. guarded=False ⇒ corre el gate PRE-API de cost_guard (tope local).
    guarded=True (BTP_COST_GUARDED=1) ⇒ salta ese gate y llega al bucle de modelos → ahí se detecta
    el saldo de PREPAGO agotado POST-API (is_credit_out)."""
    fake_salida = os.path.join(tmp, "salida_fake.py")
    msgfile = os.path.join(tmp, "msgs.txt")
    open(fake_salida, "w").write("import sys\nopen(%r,'a').write(' '.join(sys.argv[1:])+'\\n')\n" % msgfile)
    env = _clean_env(BTP_CLAUDE_BIN=claude_bin, BTP_API_KEY_OVERRIDE="x", BTP_STATE_DIR=tmp,
               BTP_PERIPHERIES=_REG, BTP_AGENT="auto-mejora", BTP_MODEL="opus", BTP_REPO=ROOT,
               BTP_SALIDA=fake_salida, BTP_HALT_FILES=os.path.join(tmp, "a") + ":" + os.path.join(tmp, "b"))
    if guarded:
        env["BTP_COST_GUARDED"] = "1"
    if avisa:
        env["BTP_AVISA_APLAZO"] = "1"
        env["BTP_RUTINA_NOMBRE"] = "la auto-mejora del sistema"
    p = subprocess.run(["bash", os.path.join(ROOT, "tools", "run_agent.sh"), "mejórate"],
                       capture_output=True, text=True, env=env)
    try:
        hb = json.load(open(os.path.join(tmp, "heartbeat", "auto-mejora.json"))).get("estado")
    except Exception:
        hb = None
    msgs = open(msgfile).read() if os.path.exists(msgfile) else ""
    return p.stdout, p.returncode, hb, msgs, msgfile


def _setup_tmp(prefix, tope):
    """tmp con notif + limits.json (tope minúsculo → topa el cap local; generoso → pasa el cap)."""
    tmp = tempfile.mkdtemp(prefix=prefix)
    os.makedirs(os.path.join(tmp, "cost"), exist_ok=True)
    json.dump({"tope_diario_usd": tope, "tope_job_usd": tope, "tope_mensual_usd": tope},
              open(os.path.join(tmp, "cost", "limits.json"), "w"))
    os.makedirs(os.path.join(tmp, "notif"), exist_ok=True)
    json.dump({"token": "x", "chat_id": "1"}, open(os.path.join(tmp, "notif", "config.json"), "w"))
    return tmp


def aviso_aplazo_rutina_tests():
    """Aviso al APLAZAR una rutina NED por falta de presupuesto (lección 24-26/6: la auto-mejora se
    cayó en silencio días). Una rutina NO-clínica con BTP_AVISA_APLAZO=1 NO finge (sin centralita),
    sale exit 75, AVISA a {{TITULAR}} 1×/día NOMBRANDO la rutina, y es anti-spam la 2ª pasada del día.
    PERO el mensaje distingue la CAUSA (regla 29/6, commit fix(coste) «sube» vs «recarga»):
      · TOPE LOCAL de {{TITULAR}} (cap de cost_guard, PRE-API): el prepago SÍ tiene saldo → pide «sube»
        (1 clic amplía el tope), NUNCA 'recarga'. heartbeat 'aplazado_tope_local'.
      · PREPAGO de Anthropic AGOTADO (400 'Credit balance is too low', POST-API): pide 'recarga' en
        la consola, NUNCA «sube» (un tope mayor no trae dinero). heartbeat 'credito_agotado'.
    Una rutina SIN el flag sigue aplazando MUDA (default OFF = comportamiento de siempre)."""

    # --- A) TOPE LOCAL (cap de cost_guard, pre-API): el dinero está, falta margen de hoy → «sube».
    tmp = _setup_tmp("ra_tope_", 0.001)              # tope minúsculo → el check de cost_guard falla
    out, rc, hb, msgs, msgfile = _run_rutina(tmp, _BIN, avisa=True)
    ok(rc == 75 and hb == "aplazado_tope_local", "tope local → exit 75 (aplaza, no finge)")
    ok("DESDE-CENTRALITA" not in out, "rutina aplazada NO la sirve el cerebro de respaldo")
    ok("auto-mejora del sistema" in msgs, "el aviso NOMBRA la rutina concreta (tope local)")
    ok("sube" in msgs.lower(), "tope local → pide «sube» (1 clic amplía el tope; el saldo está)")
    ok("recarga" not in msgs.lower(), "NO dice 'recarga' (el prepago tiene saldo; es la baranda)")
    n1 = msgs.count("report")
    _run_rutina(tmp, _BIN, avisa=True)               # 2ª pasada el mismo día
    n2 = (open(msgfile).read() if os.path.exists(msgfile) else "").count("report")
    ok(n2 == n1, "tope local: 2ª pasada NO re-avisa (anti-spam 1/día): %d→%d" % (n1, n2))

    # --- B) PREPAGO AGOTADO (400 Credit balance, post-API): un tope mayor no trae dinero → «recarga».
    # guarded=True salta el cap local (pre-API) para llegar al bucle de modelos, donde la API
    # devuelve el 400 "Credit balance is too low" → el manejador POST-API pide 'recarga'.
    tmpb = _setup_tmp("ra_credito_", 9999)
    out, rc, hb, msgs, msgfile = _run_rutina(tmpb, _BIN_CREDIT, avisa=True, guarded=True)
    ok(rc == 75 and hb == "credito_agotado", "prepago agotado → exit 75 + heartbeat 'credito_agotado'")
    ok("DESDE-CENTRALITA" not in out, "prepago agotado: NO la sirve el cerebro de respaldo")
    ok("auto-mejora del sistema" in msgs, "el aviso NOMBRA la rutina concreta (prepago)")
    ok("recarga" in msgs.lower(), "prepago agotado → pide 'recarga' (saldo real a 0, no es el cap)")
    ok("sube" not in msgs.lower(), "NO pide «sube» (un tope mayor no trae dinero al prepago)")
    n1 = msgs.count("report")
    _run_rutina(tmpb, _BIN_CREDIT, avisa=True)       # 2ª pasada el mismo día
    n2 = (open(msgfile).read() if os.path.exists(msgfile) else "").count("report")
    ok(n2 == n1, "prepago: 2ª pasada NO re-avisa (anti-spam 1/día): %d→%d" % (n1, n2))

    # Sin el flag → aplazo MUDO (default OFF preserva el comportamiento de siempre).
    tmp2 = tempfile.mkdtemp(prefix="ra_mudo_")
    os.makedirs(os.path.join(tmp2, "cost"), exist_ok=True)
    json.dump({"tope_diario_usd": 0.001, "tope_job_usd": 0.001, "tope_mensual_usd": 0.001},
              open(os.path.join(tmp2, "cost", "limits.json"), "w"))
    os.makedirs(os.path.join(tmp2, "notif"), exist_ok=True)
    json.dump({"token": "x", "chat_id": "1"}, open(os.path.join(tmp2, "notif", "config.json"), "w"))
    msgfile2 = os.path.join(tmp2, "msgs.txt")
    fake_salida2 = os.path.join(tmp2, "salida_fake.py")
    open(fake_salida2, "w").write("import sys\nopen(%r,'a').write(' '.join(sys.argv[1:])+'\\n')\n" % msgfile2)
    env = _clean_env(BTP_CLAUDE_BIN=_BIN, BTP_API_KEY_OVERRIDE="x", BTP_STATE_DIR=tmp2,
               BTP_PERIPHERIES=_REG, BTP_AGENT="auto-mejora", BTP_MODEL="opus", BTP_REPO=ROOT,
               BTP_SALIDA=fake_salida2, BTP_HALT_FILES=os.path.join(tmp2, "a") + ":" + os.path.join(tmp2, "b"))
    p = subprocess.run(["bash", os.path.join(ROOT, "tools", "run_agent.sh"), "mejórate"],
                       capture_output=True, text=True, env=env)
    ok(p.returncode == 75 and not os.path.exists(msgfile2),
       "sin BTP_AVISA_APLAZO → aplazo MUDO (default OFF intacto)")


def cadena_fable_tests():
    """Fable (2/jul/26, verificado en vivo — `claude --model fable` responde en esta cuenta) = tier
    de MÁXIMA potencia; CADENA de degradación fable→opus→sonnet (NUNCA se queda sin cerebro fuerte).
    Fake claude: 429 (límite) en fable Y opus, éxito en sonnet → afirma que el bucle prueba los TRES
    modelos EN ORDEN antes de responder, leyendo el --model de cada intento (fichero append, no
    overwrite, para poder reconstruir la secuencia completa)."""
    def _cadena_con_limites(agente):
        tmp = tempfile.mkdtemp(prefix="ra_fable_")
        seq_file = os.path.join(tmp, "modelos_probados.txt")
        _bin_fable = os.path.join(tmp, "claude_fable_degrada.sh")
        # Lee el --model de "$@" (el flag va seguido de su valor) y lo apunta; 429 salvo en sonnet.
        open(_bin_fable, "w").write(
            '#!/bin/bash\n'
            'm=""\n'
            'while [ $# -gt 0 ]; do\n'
            '  if [ "$1" = "--model" ]; then m="$2"; fi\n'
            '  shift\n'
            'done\n'
            'echo "$m" >> "' + seq_file + '"\n'
            'if [ "$m" = "sonnet" ]; then\n'
            '  echo \'{"subtype":"success","is_error":false,"total_cost_usd":0.01,"result":"ok"}\'\n'
            'else\n'
            '  echo \'{"api_error_status": 429, "is_error": true}\'\n'
            'fi\n'
        )
        os.chmod(_bin_fable, 0o755)
        env = _clean_env(BTP_CLAUDE_BIN=_bin_fable, BTP_API_KEY_OVERRIDE="x",
                   BTP_COST_GUARDED="1", BTP_STATE_DIR=tmp, BTP_PERIPHERIES=_REG,
                   BTP_AGENT=agente, BTP_MODEL="fable", BTP_REPO=ROOT,
                   BTP_HALT_FILES=os.path.join(tmp, "nh_a") + ":" + os.path.join(tmp, "nh_b"))
        p = subprocess.run(["bash", os.path.join(ROOT, "tools", "run_agent.sh"), "revisa el caso"],
                           capture_output=True, text=True, env=env)
        return p, (open(seq_file).read().split() if os.path.exists(seq_file) else [])

    # RUTINA (no clínica): la cadena degrada y responde, como siempre.
    p, secuencia = _cadena_con_limites("orquestador")
    ok(secuencia == ["fable", "opus", "sonnet"],
       "rutina: BTP_MODEL=fable → CADENA fable→opus→sonnet en orden (visto: %r)" % secuencia)
    ok(p.returncode == 0, "rutina: la cadena degrada y ACABA respondiendo (rc=0)")
    # CRÍTICA (clínica) — 25-sep-26, deuda carril-clinico-degrada-y-sirve-sin-marcar: antes este
    # mismo caso con comite-medico degradaba a sonnet y entregaba (rc=0) como si nada. Un límite
    # es pasajero: la tarea ESPERA a Fable (rc 75, se reencola) y no se prueba ningún otro modelo.
    p, secuencia = _cadena_con_limites("comite-medico")
    ok(secuencia == ["fable"],
       "crítica: con límite en fable NO degrada, solo se prueba fable (visto: %r)" % secuencia)
    ok(p.returncode == 75, "crítica: con límite → exit 75 (espera y se reencola), no rc=0")
    ok('"result":"ok"' not in p.stdout,
       "crítica: no se entrega ninguna respuesta de un modelo de menos potencia")

    # BTP_MODEL=fable sin degradación (fable responde a la primera) → solo se prueba fable.
    tmp2 = tempfile.mkdtemp(prefix="ra_fable_ok_")
    seq_file2 = os.path.join(tmp2, "modelos_probados.txt")
    _bin_fable_ok = os.path.join(tmp2, "claude_fable_ok.sh")
    open(_bin_fable_ok, "w").write(
        '#!/bin/bash\n'
        'm=""\n'
        'while [ $# -gt 0 ]; do\n'
        '  if [ "$1" = "--model" ]; then m="$2"; fi\n'
        '  shift\n'
        'done\n'
        'echo "$m" >> "' + seq_file2 + '"\n'
        'echo \'{"subtype":"success","is_error":false,"total_cost_usd":0.01,"result":"ok"}\'\n'
    )
    os.chmod(_bin_fable_ok, 0o755)
    env2 = _clean_env(BTP_CLAUDE_BIN=_bin_fable_ok, BTP_API_KEY_OVERRIDE="x",
                BTP_COST_GUARDED="1", BTP_STATE_DIR=tmp2, BTP_PERIPHERIES=_REG,
                BTP_AGENT="comite-medico", BTP_MODEL="fable", BTP_REPO=ROOT,
                BTP_HALT_FILES=os.path.join(tmp2, "nh_a") + ":" + os.path.join(tmp2, "nh_b"))
    subprocess.run(["bash", os.path.join(ROOT, "tools", "run_agent.sh"), "revisa el caso"],
                   capture_output=True, text=True, env=env2)
    secuencia2 = open(seq_file2).read().split() if os.path.exists(seq_file2) else []
    ok(secuencia2 == ["fable"], "fable sin límite → NO degrada (solo se prueba fable): %r" % secuencia2)


def orquestador_fallback_tests():
    """SALVAGUARDA del piloto de auth por suscripción: si el orquestador NO-default (claude-suscripcion)
    no tiene su token en el Llavero (aún no generado / caducado a los 12 meses), run_agent NO se
    rompe → cae a 'claude' (API medida) y AVISA una vez, nunca deja el daemon mudo (regla
    feedback-credito-no-bloquea-degrada / auto-mejora-no-degradar-silencio). El default 'claude' no
    se ve afectado por el gancho de test. El gancho BTP_ORQ_TOKEN_MISSING simula el secreto ausente."""
    reg = os.path.join(_TMP, "orq_reg.json")
    json.dump({"orquestadores": [
        {"name": "claude", "cmd": "claude", "secret": "btp-anthropic-api",
         "auth_env": "ANTHROPIC_API_KEY", "enabled": True},
        {"name": "claude-suscripcion", "cmd": "claude", "secret": "btp-claude-oauth-token",
         "auth_env": "CLAUDE_CODE_OAUTH_TOKEN", "nivel_min": "rutina", "enabled": True},
    ]}, open(reg, "w"))

    def _run(orq, token_missing):
        env = _clean_env(BTP_CLAUDE_BIN=_BIN_OK, BTP_API_KEY_OVERRIDE="x",
                   BTP_COST_GUARDED="1", BTP_STATE_DIR=_TMP, BTP_PERIPHERIES=_REG,
                   BTP_ORQUESTADORES=reg, BTP_ORQUESTADOR=orq,
                   BTP_AGENT="orquestador", BTP_MODEL="sonnet", BTP_REPO=ROOT,
                   BTP_HALT_FILES=os.path.join(_TMP, "nh_a") + ":" + os.path.join(_TMP, "nh_b"))
        if token_missing:
            env["BTP_ORQ_TOKEN_MISSING"] = "1"
        p = subprocess.run(["bash", os.path.join(ROOT, "tools", "run_agent.sh"), "haz algo"],
                           capture_output=True, text=True, env=env)
        return p.stdout, p.stderr, p.returncode

    # (1) suscripción SIN token → cae a la API, corre igual, avisa (NO exit 1, NO mudo)
    out, err, rc = _run("claude-suscripcion", token_missing=True)
    ok(rc == 0 and "ok-real" in out, "suscripción sin token → NO se rompe, corre por la API (fallback)")
    ok("sigo con la API medida" in err, "suscripción sin token → avisa del fallback (no queda mudo)")

    # (2) suscripción CON token → corre normal, SIN aviso de fallback
    out, err, rc = _run("claude-suscripcion", token_missing=False)
    ok(rc == 0 and "ok-real" in out and "sigo con la API medida" not in err,
       "suscripción con token → corre normal, sin fallback espurio")

    # (3) default 'claude' + gancho token-missing → el gancho NO aplica al default (path intacto)
    out, err, rc = _run("claude", token_missing=True)
    ok(rc == 0 and "ok-real" in out and "sigo con la API medida" not in err,
       "default claude → el gancho de token-missing no le afecta")


def refusal_degrada_tests():
    """🩺 Agujero silencioso (evaluación de stack 6/7/26, rescatado 12/7): un clasificador de
    seguridad (bio/cyber) puede rechazar la petición con HTTP 200 + stop_reason:"refusal" + content
    VACÍO — eso NO es un api_error_status, así que is_credit_out/is_limit no lo veían y el run se
    daba por "éxito" con una respuesta hueca. Verifica que run_agent.sh detecta el refusal y degrada
    a Opus (+ traza 'FALLBACK: refusal'), sin bucle y sin tratarlo como éxito silencioso."""
    # (1) Fable refusal → degrada a Opus → Opus responde con éxito real.
    tmp = tempfile.mkdtemp(prefix="ra_refusal_degrada_")
    seq_file = os.path.join(tmp, "modelos_probados.txt")
    _bin = os.path.join(tmp, "claude_refusal_degrada.sh")
    open(_bin, "w").write(
        '#!/bin/bash\n'
        'm=""\n'
        'while [ $# -gt 0 ]; do\n'
        '  if [ "$1" = "--model" ]; then m="$2"; fi\n'
        '  shift\n'
        'done\n'
        'echo "$m" >> "' + seq_file + '"\n'
        'if [ "$m" = "fable" ]; then\n'
        '  echo \'{"subtype":"success","is_error":false,"stop_reason":"refusal","result":""}\'\n'
        'else\n'
        '  echo \'{"subtype":"success","is_error":false,"total_cost_usd":0.01,"result":"respuesta real de opus"}\'\n'
        'fi\n'
    )
    os.chmod(_bin, 0o755)
    fake_salida = os.path.join(tmp, "salida_fake.py")
    msgfile = os.path.join(tmp, "msgs.txt")
    open(fake_salida, "w").write(
        "import sys\nopen(%r,'a').write(' '.join(sys.argv[1:])+'\\n')\n" % msgfile)
    os.makedirs(os.path.join(tmp, "notif"), exist_ok=True)      # avisos opt-in: aquí, sí
    json.dump({"token": "x", "chat_id": "1"}, open(os.path.join(tmp, "notif", "config.json"), "w"))
    env = _clean_env(BTP_CLAUDE_BIN=_bin, BTP_API_KEY_OVERRIDE="x",
               BTP_COST_GUARDED="1", BTP_STATE_DIR=tmp, BTP_PERIPHERIES=_REG,
               BTP_AGENT="comite-medico", BTP_MODEL="fable", BTP_REPO=ROOT,
               BTP_SALIDA=fake_salida,
               BTP_HALT_FILES=os.path.join(tmp, "nh_a") + ":" + os.path.join(tmp, "nh_b"))
    p = subprocess.run(["bash", os.path.join(ROOT, "tools", "run_agent.sh"), "revisa el caso"],
                       capture_output=True, text=True, env=env)
    secuencia = open(seq_file).read().split() if os.path.exists(seq_file) else []
    ok(secuencia == ["fable", "opus"],
       "refusal en fable → degrada a opus (visto: %r)" % secuencia)
    ok(p.returncode == 0 and "respuesta real de opus" in p.stdout,
       "tras degradar, la respuesta ENTREGADA es la real de Opus, no el hueco del refusal")
    # 25-sep-26 (decisión de {{TITULAR}}: rechazo → responde el siguiente, MARCADO). Antes la de Opus
    # llegaba como una respuesta normal de Fable; ahora el `.result` lleva la marca delante.
    try:
        entregado = json.loads(p.stdout)["result"]
    except Exception:
        entregado = ""
    ok(entregado.startswith("⚠️ Respondida por opus porque fable la rechazó"),
       "crítica degradada por rechazo: el .result lleva la MARCA delante (visto: %r)" % entregado[:80])
    ok(entregado.rstrip().endswith("respuesta real de opus"),
       "…y detrás, la respuesta de Opus intacta")
    msgs = open(msgfile).read() if os.path.exists(msgfile) else ""
    ok("report-urgente" in msgs and "fable la rechazó" in msgs,
       "…y un aviso URGENTE que dice quién rechazó y quién respondió (visto: %r)" % msgs[:120])
    ok("FALLBACK: refusal en fable" in p.stderr,
       "deja traza reconocible 'FALLBACK: refusal en <modelo> → <siguiente>' en stderr")

    # (2) TODA la cadena rechaza (fable, opus Y sonnet) → NUNCA se entrega como 'ok' silencioso.
    tmp2 = tempfile.mkdtemp(prefix="ra_refusal_agotada_")
    seq_file2 = os.path.join(tmp2, "modelos_probados.txt")
    _bin2 = os.path.join(tmp2, "claude_refusal_total.sh")
    open(_bin2, "w").write(
        '#!/bin/bash\n'
        'm=""\n'
        'while [ $# -gt 0 ]; do\n'
        '  if [ "$1" = "--model" ]; then m="$2"; fi\n'
        '  shift\n'
        'done\n'
        'echo "$m" >> "' + seq_file2 + '"\n'
        'echo \'{"subtype":"success","is_error":false,"stop_reason":"refusal","result":""}\'\n'
    )
    os.chmod(_bin2, 0o755)
    fake_salida2 = os.path.join(tmp2, "salida_fake.py")
    msgfile2 = os.path.join(tmp2, "msgs.txt")
    open(fake_salida2, "w").write(
        "import sys\nopen(%r,'a').write(' '.join(sys.argv[1:])+'\\n')\n" % msgfile2)
    os.makedirs(os.path.join(tmp2, "notif"), exist_ok=True)
    json.dump({"token": "x", "chat_id": "1"}, open(os.path.join(tmp2, "notif", "config.json"), "w"))
    env2 = _clean_env(BTP_CLAUDE_BIN=_bin2, BTP_API_KEY_OVERRIDE="x",
                BTP_COST_GUARDED="1", BTP_STATE_DIR=tmp2, BTP_PERIPHERIES=_REG,
                BTP_AGENT="comite-medico", BTP_MODEL="fable", BTP_REPO=ROOT,
                BTP_SALIDA=fake_salida2,
                BTP_HALT_FILES=os.path.join(tmp2, "nh_a") + ":" + os.path.join(tmp2, "nh_b"))
    p2 = subprocess.run(["bash", os.path.join(ROOT, "tools", "run_agent.sh"), "revisa el caso"],
                        capture_output=True, text=True, env=env2)
    secuencia2 = open(seq_file2).read().split() if os.path.exists(seq_file2) else []
    ok(secuencia2 == ["fable", "opus", "sonnet"],
       "refusal en TODA la cadena → prueba los TRES modelos, no se cuelga ni repite: %r" % secuencia2)
    ok(p2.returncode == 75, "cadena agotada por refusal → exit 75 (aplaza, NO 'ok' silencioso)")
    try:
        hb2 = json.load(open(os.path.join(tmp2, "heartbeat", "comite-medico.json"))).get("estado")
    except Exception:
        hb2 = None
    ok(hb2 in ("critico_bloqueado", "aplazado_refusal"),
       "heartbeat refleja el bloqueo/aplazo por refusal, NUNCA 'ok' (visto: %r)" % hb2)


def critico_bordes_tests():
    """Los tres casos que encontró `verificacion` el 25-sep-26 en el arreglo del carril clínico:
    (A) Fable rechaza y Opus FALLA sin texto → no puede quedar en exit 75 con la salida vacía
        (reencolado sin fin y 3 USD pesimistas por vuelta): sigue el camino de fallo normal;
    (A') Fable rechaza y Opus falla CON texto → no se marca ni se avisa «te llega marcada»: no se
        entrega, va a fallidos;
    (B) una respuesta BUENA que cita «rate limit» no es un límite: se entrega, no se bloquea."""
    refusal = '{"subtype":"success","is_error":false,"stop_reason":"refusal","result":""}'

    def correr(por_modelo):
        tmp = tempfile.mkdtemp(prefix="ra_bordes_")
        b = os.path.join(tmp, "claude.sh")
        casos = "".join("  %s) echo '%s' ;;\n" % (m, j) for m, j in por_modelo.items())
        open(b, "w").write('#!/bin/bash\nm=""\nwhile [ $# -gt 0 ]; do [ "$1" = "--model" ] && m="$2"; '
                           'shift; done\ncase "$m" in\n' + casos + 'esac\n')
        os.chmod(b, 0o755)
        msg = os.path.join(tmp, "msgs.txt")
        fake = os.path.join(tmp, "salida_fake.py")
        open(fake, "w").write("import sys\nopen(%r,'a').write(' '.join(sys.argv[1:])+'\\n')\n" % msg)
        os.makedirs(os.path.join(tmp, "notif"), exist_ok=True)
        json.dump({"token": "x", "chat_id": "1"}, open(os.path.join(tmp, "notif", "config.json"), "w"))
        env = _clean_env(BTP_CLAUDE_BIN=b, BTP_API_KEY_OVERRIDE="x", BTP_COST_GUARDED="1",
                         BTP_STATE_DIR=tmp, BTP_PERIPHERIES=_REG, BTP_AGENT="comite-medico",
                         BTP_MODEL="fable", BTP_REPO=ROOT, BTP_SALIDA=fake,
                         BTP_HALT_FILES=os.path.join(tmp, "nh_a") + ":" + os.path.join(tmp, "nh_b"))
        p = subprocess.run(["bash", os.path.join(ROOT, "tools", "run_agent.sh"), "revisa el caso"],
                           capture_output=True, text=True, env=env)
        return p, (open(msg).read() if os.path.exists(msg) else "")

    p, msgs = correr({"fable": refusal, "opus": '{"is_error":true,"subtype":"error_during_execution"}'})
    ok(p.returncode != 75 and p.stdout.strip() != "",
       "(A) rechazo → fallo sin texto: no queda en exit 75 con la salida vacía (rc=%d)" % p.returncode)
    p, msgs = correr({"fable": refusal,
                      "opus": '{"is_error":true,"subtype":"error_max_turns","result":"parcial"}'})
    ok("Respondida por" not in p.stdout and "report-urgente" not in msgs,
       "(A') rechazo → fallo con texto: ni marca ni aviso de algo que no se entrega")
    p, msgs = correr({"fable": '{"is_error":false,"subtype":"success","result":"La API tiene un rate limit de 50 rpm."}'})
    ok(p.returncode == 0 and "rate limit de 50 rpm" in p.stdout,
       "(B) una respuesta buena que cita «rate limit» se entrega, no es un límite (rc=%d)" % p.returncode)


def clinico_sin_modelo_tests():
    """Deuda clinico-sin-modelo-va-a-sonnet (25-sep-26). Un job clínico sin modelo caía en el
    sonnet por defecto, pisando el `model: fable` de la ficha del agente; tres jobs de la cola
    corrieron así. Ahora usa el de la ficha. Un agente de rutina sin modelo sigue en sonnet."""
    def primer_modelo(agente):
        tmp = tempfile.mkdtemp(prefix="ra_sin_modelo_")
        seq = os.path.join(tmp, "modelos.txt")
        b = os.path.join(tmp, "claude.sh")
        open(b, "w").write('#!/bin/bash\nm=""\nwhile [ $# -gt 0 ]; do [ "$1" = "--model" ] && m="$2"; '
                           'shift; done\necho "$m" >> "' + seq + '"\n'
                           "echo '{\"subtype\":\"success\",\"is_error\":false,\"result\":\"ok\"}'\n")
        os.chmod(b, 0o755)
        env = _clean_env(BTP_CLAUDE_BIN=b, BTP_API_KEY_OVERRIDE="x", BTP_COST_GUARDED="1",
                         BTP_STATE_DIR=tmp, BTP_PERIPHERIES=_REG, BTP_AGENT=agente, BTP_REPO=ROOT,
                         BTP_HALT_FILES=os.path.join(tmp, "nh_a") + ":" + os.path.join(tmp, "nh_b"))
        env.pop("BTP_MODEL", None)
        subprocess.run(["bash", os.path.join(ROOT, "tools", "run_agent.sh"), "x"],
                       capture_output=True, text=True, env=env)
        return (open(seq).read().split() or [""])[0] if os.path.exists(seq) else ""
    for agente in ("comite-medico", "oncologo-virtual", "verificacion", "herramientas-medicas"):
        m = primer_modelo(agente)
        ok(m == "fable", "clínico sin modelo (%s) → el de su ficha, fable (visto: %r)" % (agente, m))
    m = primer_modelo("orquestador")
    ok(m == "sonnet", "rutina sin modelo sigue en sonnet (visto: %r)" % m)


def main():
    # 1) Rutina AGÉNTICA (sin BTP_FREE_OK) + Claude agotado → APLAZA con exit 75, NO finge con un 3B
    out, err, hb, rc = run_agent("revisa la cola y avanza lo rutinario", "orquestador")
    ok("DESDE-CENTRALITA" not in out and rc == 75 and hb == "aplazado_limite",
       "agéntico + Claude agotado → exit 75 (aplaza, no finge con la centralita)")

    # 2) Trabajo DISCRETO opt-in (BTP_FREE_OK=1) + Claude agotado + limpio → la centralita responde
    out, err, hb, rc = run_agent("resume esto en una frase", "orquestador", free_ok=True)
    ok("DESDE-CENTRALITA" in out and rc == 0 and hb == "centralita",
       "discreto opt-in + Claude agotado → respaldo por la centralita")

    # 3) CLÍNICO + Claude agotado → 🔴 BLOQUEO crítico (exit 75, heartbeat 'critico_bloqueado'),
    #    NUNCA centralita ni con opt-in. Clínico ⇒ crítico ⇒ el freno PARA, no aplaza a secas ni
    #    sirve un cerebro flojo. (Antes era 'aplazado_limite'; ahora el freno lo marca como bloqueo.)
    out, err, hb, rc = run_agent("revisa el caso", "comite-medico", free_ok=True)
    ok("DESDE-CENTRALITA" not in out and rc == 75 and hb == "critico_bloqueado",
       "clínico + Claude agotado → exit 75 BLOQUEO crítico (nunca a nube ni a cerebro flojo)")

    # 4) CRÍTICO por BTP_CRITICIDAD (agente NO clínico) + Claude agotado → 🔴 BLOQUEO crítico,
    #    estado 'critico_bloqueado' DISTINTO del aplazado de rutina; nunca cae a la centralita.
    out, err, hb, rc = run_agent("tarea importante marcada crítica", "orquestador",
                                 free_ok=True, criticidad="critico")
    ok("DESDE-CENTRALITA" not in out and rc == 75 and hb == "critico_bloqueado",
       "crítico (BTP_CRITICIDAD) + Claude agotado → exit 75 'critico_bloqueado' (no centralita)")

    # 5) RUTINA (default, sin BTP_CRITICIDAD) + Claude agotado + agéntico → aplazo NORMAL
    #    (heartbeat 'aplazado_limite'), NO el bloqueo crítico → no se sobre-bloquea lo de rutina.
    out, err, hb, rc = run_agent("avanza lo rutinario", "orquestador", criticidad="rutina")
    ok(rc == 75 and hb == "aplazado_limite",
       "rutina + Claude agotado → aplazo normal (no sobre-bloqueado como crítico)")

    exito_real_no_depende_de_rc_tests()
    antifuga_tests()
    clinico_salta_tope_si_saldo_sano_tests()
    aprobacion_gate_tests()
    aviso_aplazo_rutina_tests()
    cadena_fable_tests()
    orquestador_fallback_tests()
    refusal_degrada_tests()
    critico_bordes_tests()
    clinico_sin_modelo_tests()
    print("RESULTADO run_agent F2: %d OK, %d fallos" % (_pass, _fail))
    print("✅ F2 EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
