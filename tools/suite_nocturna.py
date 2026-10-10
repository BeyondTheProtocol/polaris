#!/usr/bin/env python3
"""tools/suite_nocturna.py — la suite COMPLETA, de noche, sobre casa base.

POR QUÉ EXISTE (10-oct-26, plan de aceleración de test_all.sh). La puerta de fusión por impacto
(`tools/tests_afectados.py --puerta`) deja fusionar con las baterías afectadas + el núcleo del muro.
Lo que ya NO se corre en cada fusión (el efecto indirecto, las baterías gordas, lo que solo corre de
verdad en casa base) tiene que correrse en algún sitio, todos los días, y si sale rojo tiene que
enterarse alguien. Esto es ese sitio. NO sustituye a nada que exista: `evals` es el arnés de agentes
(lunes 06:30), `seguridad-sweep` barre seguridad, el CI público corre un subconjunto portable en
Linux sobre el espejo (no cubre las baterías solo-casa-base ni `test_fuga.sh`/`test_halt.sh` reales).

QUÉ HACE, en orden:
  1. Solo corre desde casa base (se niega en un worktree). Con HALT activo no corre y lo apunta.
  2. `bash tests/test_all.sh` en paralelo (BTP_JOBS=6; los DOMINGOS en serie, la referencia: un rojo
     que sale solo en las noches paralelas es del paralelismo y entra en SOLO_SERIE). Los domingos
     corre además las campañas de mutantes lentas de la puerta (no caben en la pasada diaria).
  3. Clasifica los rojos con `tests/_rojos_conocidos.py` (conocido = con deuda, dueño y caducidad).
  4. Cada rojo NUEVO se repite SOLO una vez: sigue rojo → deuda `suite-nocturna-<batería>`;
     pasa solo → FLAKY → deuda `suite-nocturna-flaky-<batería>` (hoy un flake es invisible).
  5. Si algún rojo (o flaky) es de una batería del NÚCLEO DEL MURO, AVISA a {{TITULAR}} esa misma noche,
     sin esperar a que la deuda escale. Los demás van a la deuda y escalan por repetición.
  6. Deja latido (`heartbeat/suite-nocturna.json`), una línea en `suite_nocturna/historial.jsonl` y
     `suite_nocturna/<fecha>/` con el log y los rojos. Si el propio runner se cuelga o revienta,
     estado «fallo» y aviso: una nocturna muda es peor que ninguna.

`condiciones` dice si se cumplen las TRES condiciones para cambiar la norma «suite completa antes
de fusionar» (decisión 10-oct-26): A) la puerta falla cerrada en casa base, B) existe la lista de
rojos conocidos con sus vetos, C) tres noches seguidas con latido y un rojo sembrado a propósito que
llegó a {{TITULAR}} y ella confirmó. Sale 0 solo si las tres; no hace falta fiarse de la memoria.

Uso:
  python3 tools/suite_nocturna.py                 # la pasada de esta noche (lo que lanza launchd)
  python3 tools/suite_nocturna.py --sembrar       # + un rojo sembrado a propósito (prueba el aviso)
  python3 tools/suite_nocturna.py --sembrar --solo-aviso   # SOLO el aviso de prueba, sin correr la suite
  python3 tools/suite_nocturna.py condiciones     # ¿se cumplen las tres condiciones? rc 0 = sí
  python3 tools/suite_nocturna.py confirmar-sembrado <código>   # el del aviso; exige un prompt HUMANO que lo contenga
AVISO DE UN ROJO DEL MURO A LAS 03:00 — COMPORTAMIENTO ACTUAL (10-oct-26, tal cual es, comprobado en el código y en la config):
  va por `errores.registrar(OPERATIVO)` → `salida.report_to_titular(texto)` NO urgente. Eso respeta (a) el presupuesto diario del
  portero de ruido (`TOPE_AVISOS_DIA`, 3 avisos entregados por día natural): agotado, el aviso queda «aplazado al parte»;
  (b) el silencio nocturno, que hoy NO existe: {{TITULAR}} lo quitó el 29/6/26 (`tools/state/notif/config.json`:
  silencio_inicio == silencio_fin == 0, «Vega/avisos pueden llegar de noche»); y (c) el anti-spam de 12 h de `errores`.
  Consecuencia: a las 03:00 el contador del día acaba de reiniciarse, así que el aviso llega AL MOMENTO salvo que ya
  hubiera 3 entregados entre las 00:00 y las 03:00 (o HALT). `historial.jsonl` guarda el estado real en `entrega_muro` y
  `avisado_muro` es True solo si se entregó. Si volviera una ventana de silencio, quedaría «retenido» hasta el resumen de las 08:00.
  Opciones para cerrar el hueco del cupo agotado (decisión de {{TITULAR}}, no se fuerza): ver la nota de la tanda.
Stdlib pura. Determinista, $0.
"""
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
HB_DIR = os.path.join(STATE, "heartbeat")
HB_NAME = "suite-nocturna"
DIR = os.path.join(STATE, "suite_nocturna")
HISTORIAL = os.path.join(DIR, "historial.jsonl")
SEMBRADO = os.path.join(DIR, "sembrado.json")
TOPE_S = int(os.environ.get("BTP_SUITE_NOCTURNA_TOPE_S", "10800"))      # 3 h
TOPE_REPETIR_S = int(os.environ.get("BTP_SUITE_NOCTURNA_TOPE_REPETIR_S", "600"))
DOMINGO = 6


# ── latido, historial, aviso ──────────────────────────────────────────────────────────────
def _heartbeat(estado, **extra):
    """Mismo formato ISO-Z que el resto de daemons. Best-effort: nunca lanza."""
    try:
        os.makedirs(HB_DIR, exist_ok=True)
        tmp = os.path.join(HB_DIR, "." + HB_NAME + ".tmp")
        d = {"agente": HB_NAME, "ts": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "estado": estado}
        d.update(extra)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(HB_DIR, HB_NAME + ".json"))
    except Exception:
        pass


def _historial(entrada):
    try:
        os.makedirs(DIR, exist_ok=True)
        with open(HISTORIAL, "a", encoding="utf-8") as f:
            f.write(json.dumps(entrada, ensure_ascii=False) + "\n")
    except Exception as e:   # noqa: BLE001
        sys.stderr.write("suite_nocturna: no pude escribir el historial: %r\n" % e)


def leer_historial():
    out = []
    try:
        for l in open(HISTORIAL, encoding="utf-8"):
            try:
                out.append(json.loads(l))
            except Exception:
                continue
    except Exception:
        pass
    return out


def _estado(res):
    """(estado, motivo) de lo que `salida` hizo de verdad (la clasificación vive en errores._estado_entrega)."""
    import errores
    return errores._estado_entrega(res)


def avisar(texto, muro=False, sembrado=False):
    """Aviso a {{TITULAR}}. Devuelve LA VERDAD: {'avisado': bool, 'estado': str, 'motivo': str}; `avisado` es True SOLO si
    `salida` lo ENTREGÓ (no si quedó aplazado al parte, en el log operativo, retenido por el silencio nocturno o frenado).

    · El aviso de un rojo del MURO va por `errores.registrar(OPERATIVO)`: respeta el presupuesto diario de avisos (3/día), el
      silencio nocturno (hoy sin ventana: lo quitó {{TITULAR}} el 29/6) y el anti-spam de 12 h. A las 03:00 el contador del día es
      nuevo, así que normalmente llega al momento; con el cupo agotado queda «aplazado al parte» y `avisado` es False.
    · El aviso SEMBRADO (una prueba que ella pidió) va por la vía legítima del portero de ruido para «contestar a lo
      que ella acaba de pedir»: `categoria="respuesta"`, exenta SOLO del presupuesto (el silencio nocturno y el HALT
      siguen mandando). No es urgente y no se salta nada por la puerta de atrás.
    BTP_SUITE_NOCTURNA_AVISO_A=<fichero>: en vez de avisar, escribe aquí la alerta (para los tests); con
    BTP_SUITE_NOCTURNA_AVISO_ESTADO=<estado> simula que `salida` la dejó en ese estado (por defecto «entregado»)."""
    sumidero = os.environ.get("BTP_SUITE_NOCTURNA_AVISO_A")
    if sumidero:
        estado = os.environ.get("BTP_SUITE_NOCTURNA_AVISO_ESTADO") or "entregado"
        with open(sumidero, "a", encoding="utf-8") as f:
            f.write(json.dumps({"texto": texto, "muro": muro, "sembrado": sembrado, "estado": estado}, ensure_ascii=False) + "\n")
        return {"avisado": estado == "entregado", "estado": estado, "motivo": "sumidero de pruebas"}
    try:
        if sembrado:
            import salida
            res = salida.report_to_titular(texto, categoria="respuesta", voz="sobria", fuente="suite_nocturna_sembrado")
            estado, motivo = _estado(res)
            return {"avisado": estado == "entregado", "estado": estado, "motivo": motivo}
        import errores
        r = errores.registrar(origen="suite_nocturna", error=texto, severidad=errores.OPERATIVO,
                              job="suite_nocturna_muro" if muro else "suite_nocturna", detalle=texto[:1200]) or {}
        return {"avisado": bool(r.get("avisado")), "estado": r.get("entrega") or "desconocido", "motivo": r.get("entrega_motivo", "")}
    except Exception as e:   # noqa: BLE001
        sys.stderr.write("suite_nocturna: no pude avisar: %r\n" % e)
        return {"avisado": False, "estado": "error", "motivo": repr(e)}


def _deuda(clave, que, muro):
    """Abre (o refresca) la deuda. Devuelve True si quedó escrita. Nunca lanza."""
    try:
        import deuda
        deuda.abrir(clave, que, ned="alto" if muro else "medio", muro=muro, dueno="suite-nocturna")
        return True
    except Exception as e:   # noqa: BLE001
        sys.stderr.write("suite_nocturna: no pude abrir la deuda %s: %r\n" % (clave, e))
        return False


_MURO_RESPALDO = ("test_muro", "test_salida_guard", "test_ok_envio", "test_permiso", "test_gate", "test_fuga",
                  "test_halt", "test_clinico", "test_casa_base", "test_singleton", "test_rama_vista",
                  "test_launch_loopback", "test_copy_web", "test_token_rotacion", "test_regla_en_accion",
                  "test_entrada_guard", "test_canario_muro", "test_worktree_guard", "test_enrutado")


def _es_muro(bateria):
    try:
        sys.path.insert(0, os.path.join(REPO, "tests"))
        import _rojos_conocidos
        return _rojos_conocidos.es_muro(bateria)
    except Exception:
        return str(bateria).startswith(_MURO_RESPALDO)     # sin el módulo: nunca MENOS vetos


def _env_limpio(extra=None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("CI", "BTP_JOBS", "BTP_ROJO_DIR", "BTP_PORTABLE", "CLAUDE_PROJECT_DIR")
           and not re.match(r"(BTP_\w*_OK|MURO_ALLOW\w*|BTP_CIERRE_SIN_VERIFICAR)$", k)}
    env.update(extra or {})
    return env


def halt_activo():
    return any(os.path.exists(p) for p in (os.path.expanduser("~/.btp.HALT"), os.path.join(REPO, ".HALT")))


def repetir_sola(bateria):
    """True si la batería PASA corrida sola (=> el rojo era un flake)."""
    ruta = os.path.join(REPO, "tests", bateria)
    if not os.path.isfile(ruta):
        return False
    cmd = ["bash", ruta] if bateria.endswith(".sh") else [sys.executable, ruta]
    try:
        p = subprocess.run(cmd, cwd=REPO, env=_env_limpio(), capture_output=True, text=True,
                           timeout=TOPE_REPETIR_S, stdin=subprocess.DEVNULL)
        return p.returncode in (0, 77)
    except Exception:
        return False


# ── el rojo sembrado y su confirmación ───────────────────────────────────────────────────
def _hash_codigo(codigo, sal):
    """PBKDF2-HMAC-SHA256 con 300.000 vueltas (lento a propósito; `hashlib.scrypt` no existe en el Python del sistema): el código
    NO se guarda en claro, y con 8 hex (4,3·10^9 posibilidades) probarlos todos son años de CPU. Límite: no es una garantía
    criptográfica contra un adversario con tiempo, es que un agente no lo lea de pasada del estado."""
    return hashlib.pbkdf2_hmac("sha256", str(codigo).encode(), bytes.fromhex(sal), 300000).hex()


def _sembrar(hoy):
    """Manda el aviso de prueba (el camino de «batería del muro en rojo») y apunta el código de confirmación SOLO como hash
    salado (PBKDF2). Cada llamada genera un código NUEVO: un sembrado anterior queda invalidado. El código solo viaja en el
    aviso que recibe {{TITULAR}}; confirmarlo exige que ELLA lo teclee en una sesión. Devuelve {'avisado','estado','motivo'}:
    `avisado` es True solo si el aviso se ENTREGÓ (si quedó aplazado al parte, no cuenta)."""
    import secrets
    nonce = secrets.token_hex(4)
    sal = secrets.token_hex(8)
    r = avisar("[SEMBRADO A PROPÓSITO, NO ES UN FALLO REAL] Prueba de la suite nocturna: así te llegaría el aviso de que una "
               "batería del muro ha salido roja. Para confirmar que lo has recibido, escribe en la sesión de Claude Code "
               "este código: %s" % nonce, muro=True, sembrado=True)
    try:
        os.makedirs(DIR, exist_ok=True)
        with open(SEMBRADO, "w", encoding="utf-8") as f:
            json.dump({"fecha": hoy.isoformat(), "ts": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "avisado": r["avisado"], "entrega": r["estado"], "entrega_motivo": r["motivo"],
                       "sal": sal, "nonce_hash": _hash_codigo(nonce, sal), "confirmado": None}, f, ensure_ascii=False)
    except Exception:
        pass
    return r


def _prueba_humana(nonce, desde_ts):
    """(ruta, extracto) del primer prompt TECLEADO POR UNA PERSONA, posterior a `desde_ts`, que contiene `nonce`; o None.

    Es la misma prueba que ya usa el sistema para un OK suyo (`permiso_envio`): los transcripts de Claude Code los
    escribe el harness, no el agente, y los guards deniegan escribirlos; un mensaje de subagente, de otro agente
    (origin peer), de una tarea programada o un resultado de herramienta NO es un prompt humano. Un agente puede leer
    el código en sembrado.json, pero no puede hacer que aparezca en un prompt humano: eso lo teclea ella.
    LÍMITE (el de permiso_envio): todo corre con el mismo usuario de macOS; no es una garantía criptográfica."""
    if not nonce or not desde_ts:
        return None
    import glob
    import permiso_envio as PE
    base = os.environ.get("BTP_TRANSCRIPTS_DIR") or os.path.expanduser("~/.claude/projects")
    try:
        desde = datetime.datetime.strptime(desde_ts, "%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None
    for ruta in sorted(glob.glob(os.path.join(base, "*", "*.jsonl"))):
        try:
            if os.path.getmtime(ruta) < desde.replace(tzinfo=datetime.timezone.utc).timestamp():
                continue
            with open(ruta, encoding="utf-8", errors="replace") as f:
                for linea in f:
                    if nonce not in linea:
                        continue
                    try:
                        e = json.loads(linea)
                    except Exception:
                        continue
                    if PE._es_humano(e):
                        texto = PE.texto_prompt(e) or ""
                    elif PE._es_humano_encolado(e):
                        texto = PE.solo_suyo(e["attachment"].get("prompt", ""))
                    else:
                        continue
                    ts = str(e.get("timestamp", ""))[:19]
                    try:
                        if datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S") < desde:
                            continue
                    except Exception:
                        continue
                    if nonce in texto:
                        return os.path.basename(ruta), texto.strip()[:120]
        except OSError:
            continue
    return None


# ── la pasada ─────────────────────────────────────────────────────────────────────────────
def _hoy():
    """BTP_SUITE_NOCTURNA_HOY=AAAA-MM-DD fija «hoy» (los tests); si no, la fecha real."""
    s = os.environ.get("BTP_SUITE_NOCTURNA_HOY")
    return datetime.date.fromisoformat(s) if s else datetime.date.today()


def _ahora():
    """BTP_SUITE_NOCTURNA_AHORA=AAAA-MM-DDTHH:MM:SS (UTC) fija «ahora» (los tests)."""
    s = os.environ.get("BTP_SUITE_NOCTURNA_AHORA")
    return datetime.datetime.fromisoformat(s) if s else datetime.datetime.utcnow()


def pasada(hoy=None, sembrar=False):
    """Una noche. Devuelve (rc, entrada_de_historial)."""
    hoy = hoy or _hoy()
    domingo = hoy.weekday() == DOMINGO
    entrada = {"fecha": hoy.isoformat(), "corrio": False, "dia": "domingo (serie)" if domingo else "paralelo",
               "jobs": 1 if domingo else int(os.environ.get("BTP_SUITE_NOCTURNA_JOBS", "6")),
               "nuevos": [], "conocidos": [], "flaky": [], "sin_log": 0, "sembrado": bool(sembrar), "avisado": None}

    real = os.path.realpath(REPO)
    if "/.claude/worktrees/" in real and not os.environ.get("BTP_SUITE_NOCTURNA_PERMITE_WORKTREE"):
        sys.stderr.write("suite_nocturna: se niega a correr desde un worktree (%s): va sobre casa base.\n" % real)
        return 2, entrada
    if halt_activo():
        entrada["nota"] = "HALT activo: no corrí (el HALT es pausa total)"
        _heartbeat("ok_sin_novedad", corrio=False, nota=entrada["nota"])
        _historial(entrada)
        return 0, entrada

    carpeta = os.path.join(DIR, hoy.isoformat())
    rojos = os.path.join(carpeta, "rojos")
    os.makedirs(rojos, exist_ok=True)
    log = os.path.join(carpeta, "salida.log")
    t0 = time.time()
    runner_ok, rc_suite = True, None
    try:
        with open(log, "w", encoding="utf-8") as f:
            p = subprocess.run(["bash", os.path.join(REPO, "tests", "test_all.sh")], cwd=REPO, stdout=f,
                               stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=TOPE_S,
                               env=_env_limpio({"BTP_JOBS": str(entrada["jobs"]), "BTP_ROJO_DIR": rojos}))
        rc_suite = p.returncode
    except subprocess.TimeoutExpired:
        runner_ok = False
        entrada["error"] = "la suite no acabó en %d s (colgada o la máquina va muy justa)" % TOPE_S
    except Exception as e:   # noqa: BLE001
        runner_ok = False
        entrada["error"] = "no pude lanzar la suite: %r" % e
    entrada["segundos"] = int(time.time() - t0)
    entrada["corrio"] = True
    entrada["rc"] = rc_suite

    if not runner_ok:
        a = avisar("La suite nocturna NO pudo completarse: %s. Sin pasada de verdad no hay red de seguridad esta noche."
                   % entrada["error"], muro=False)
        entrada["avisado"], entrada["entrega"] = a["avisado"], a["estado"]
        entrada["estado"] = "fallo"
        _heartbeat("fallo", corrio=True, error=entrada["error"])
        _historial(entrada)
        return 1, entrada

    # clasificación de los rojos (conocidos / nuevos)
    nuevos, conocidos = [], []
    try:
        sys.path.insert(0, os.path.join(REPO, "tests"))
        import _rojos_conocidos as RC
        r = RC.clasifica(rojos, completa=False)
        nuevos = [n for n, _m in r["nuevos"]]
        conocidos = [n for n, _d in r["conocidos"]]
    except Exception as e:   # noqa: BLE001  falla cerrado: todo rojo con log es nuevo
        sys.stderr.write("suite_nocturna: clasificador roto (%r): todo es nuevo\n" % e)
        nuevos = sorted(f[5:-4] for f in os.listdir(rojos) if f.startswith("rojo-") and f.endswith(".log"))
    entrada["conocidos"] = conocidos
    entrada["sin_log"] = max(0, (rc_suite or 0) - len(nuevos)) if (rc_suite or 0) > 0 else 0

    # repetir cada nuevo SOLO; deuda; ¿muro?
    muro_rojos = []
    for b in nuevos:
        if repetir_sola(b):
            entrada["flaky"].append(b)
            _deuda("suite-nocturna-flaky-%s" % b,
                   "La batería %s salió ROJA en la pasada nocturna del %s y VERDE corrida sola: intermitente. "
                   "Un flake es un fallo sin dueño; se arregla o se justifica." % (b, hoy.isoformat()), _es_muro(b))
        else:
            entrada["nuevos"].append(b)
            _deuda("suite-nocturna-%s" % b,
                   "La batería %s sale ROJA en la pasada nocturna sobre casa base (%s), también corrida sola. "
                   "Log: %s" % (b, hoy.isoformat(), os.path.join(rojos, "rojo-%s.log" % b)), _es_muro(b))
        if _es_muro(b):
            muro_rojos.append(b)
    if entrada["sin_log"]:
        entrada["nota_sin_log"] = ("%d rojo(s) sin log propio (meta-check de huérfanos, drift de agentes o campaña "
                                   "de mutantes): mira %s" % (entrada["sin_log"], log))

    # domingos: campañas de mutantes lentas de la puerta
    if domingo:
        for camp in ("test_all_paralelo", "test_all_puerta", "rojos_conocidos_sh", "suite_nocturna", "cerrar_sesion_ruido", "test_all_copia_fija", "git_mutex_ruido"):
            ruta = os.path.join(REPO, "tests", "mutantes", camp + ".json")
            if not os.path.isfile(ruta):
                continue
            try:
                q = subprocess.run([sys.executable, os.path.join(REPO, "tools", "mutantes.py"), ruta], cwd=REPO,
                                   env=_env_limpio(), capture_output=True, text=True, timeout=TOPE_S,
                                   stdin=subprocess.DEVNULL)
                if q.returncode != 0:
                    nombre = "mutantes-" + camp
                    entrada["nuevos"].append(nombre)
                    _deuda("suite-nocturna-%s" % nombre,
                           "La campaña de mutantes %s tiene mutantes que SOBREVIVEN (o su test está rojo): "
                           "una defensa de la puerta de fusión ya no está cubierta. Última línea: %s"
                           % (camp, (q.stdout.strip().splitlines() or [""])[-1][:200]), True)
                    muro_rojos.append(nombre)
            except Exception as e:   # noqa: BLE001
                entrada["nuevos"].append("mutantes-" + camp)
                entrada.setdefault("errores", []).append("%s: %r" % (camp, e))

    # el rojo sembrado a propósito: prueba el camino del aviso de punta a punta, sin deuda
    if sembrar:
        r_sem = _sembrar(hoy)
        entrada["avisado"], entrada["entrega"] = r_sem["avisado"], r_sem["estado"]

    # aviso a {{TITULAR}} la PRIMERA noche si hay muro en rojo (no espera a que la deuda escale)
    if muro_rojos:
        solo_flaky = set(muro_rojos) <= set(entrada["flaky"])
        a = avisar("Pasada nocturna del %s sobre casa base: batería(s) del MURO en rojo: %s. %sPuede que el muro esté roto; "
                   "no he parado nada (eso lo decides tú). Detalle en %s"
                   % (hoy.isoformat(), ", ".join(muro_rojos),
                      "(Salió roja y verde al repetirla sola: intermitente.) " if solo_flaky else "", rojos), muro=True)
        entrada["avisado_muro"], entrada["entrega_muro"] = a["avisado"], a["estado"]

    hay_rojo = entrada["nuevos"] or entrada["flaky"] or entrada["sin_log"]
    entrada["estado"] = "rojo_muro" if muro_rojos else ("rojo_nuevo" if hay_rojo else "ok")
    _heartbeat(entrada["estado"], corrio=True, segundos=entrada["segundos"], nuevos=len(entrada["nuevos"]),
               conocidos=len(conocidos), flaky=len(entrada["flaky"]))
    _historial(entrada)
    return (0 if entrada["estado"] == "ok" else 1), entrada


# ── las tres condiciones para cambiar la norma ───────────────────────────────────────────
def _muro_en_la_noche(x):
    """¿Esa noche hubo una batería del NÚCLEO DEL MURO en rojo (nueva, intermitente o conocida)? Una noche así NO cuenta
    para la racha: el muro en rojo no es una red sana, aunque la pasada «corriera»."""
    if x.get("estado") == "rojo_muro":
        return True
    return any(_es_muro(b) for b in set(x.get("nuevos", [])) | set(x.get("flaky", [])) | set(x.get("conocidos", [])))


def condiciones(ahora=None):
    """[(clave, cumplida, detalle)] — A) puerta cerrada en casa base · B) lista de rojos conocidos con vetos ·
    C) tres noches seguidas con latido + rojo sembrado recibido y confirmado."""
    ahora = ahora or _ahora()
    out = []
    # A: comportamiento, no existencia de ficheros
    try:
        p = subprocess.run([sys.executable, os.path.join(REPO, "tools", "tests_afectados.py"), "--puerta", "--base",
                            "rama-que-no-existe-jamas"], cwd=REPO, capture_output=True, text=True, timeout=120,
                           stdin=subprocess.DEVNULL)
        primera = (p.stdout.splitlines() or [""])[0]
        hay_test = os.path.isfile(os.path.join(REPO, "tests", "test_all_puerta.py"))
        out.append(("A", p.returncode == 0 and primera == "COMPLETA" and hay_test,
                    "la puerta con una base inexistente dice «%s» (debe ser COMPLETA) · test_all_puerta.py %s"
                    % (primera, "existe" if hay_test else "NO existe")))
    except Exception as e:   # noqa: BLE001
        out.append(("A", False, "no pude comprobarlo: %r" % e))
    # B: existe y VETA de verdad
    try:
        sys.path.insert(0, os.path.join(REPO, "tests"))
        import importlib
        RC = importlib.import_module("_rojos_conocidos")
        e = {"bateria": "test_fuga.sh", "deuda": "x", "dueno": "x", "desde": "2026-01-01", "caduca": "2026-01-10",
             "firma": ["x"]}
        veta = any("VETADA" in x for x in RC.problemas_de_entrada(e, datetime.date(2026, 1, 5), {"x"}, {"test_fuga.sh"}))
        registrado = "test_rojos_conocidos.py" in open(os.path.join(REPO, "tests", "test_all.sh"), encoding="utf-8").read()
        fich = os.path.isfile(os.path.join(REPO, "tests", "rojos_conocidos.json"))
        out.append(("B", veta and registrado and fich,
                    "veta test_fuga* sin OK de {{TITULAR}}: %s · fichero: %s · test enganchado: %s" % (veta, fich, registrado)))
    except Exception as ex:   # noqa: BLE001
        out.append(("B", False, "no pude comprobarlo: %r" % ex))
    # C1: tres noches seguidas, con pasada de verdad, sin «fallo» y SIN el núcleo del muro en rojo (10-oct-26,
    # consejero-arquitectura: una racha de tres noches con `test_gate_*` roja no puede dar por buena la red)
    todas = [x for x in leer_historial() if x.get("corrio") and x.get("estado") != "fallo"]
    h = [x for x in todas if not _muro_en_la_noche(x)]
    descartadas = sorted({x["fecha"] for x in todas if x not in h and x.get("fecha")})
    fechas = sorted({x["fecha"] for x in h if x.get("fecha")})
    seguidas = 0
    if fechas:
        ds = [datetime.date.fromisoformat(f) for f in fechas]
        seguidas = 1
        for i in range(len(ds) - 1, 0, -1):
            if (ds[i] - ds[i - 1]).days == 1:
                seguidas += 1
            else:
                break
        if (ahora.date() - ds[-1]).days > 1:
            seguidas = 0            # la racha tiene que llegar a ayer u hoy
    try:
        hb = json.load(open(os.path.join(HB_DIR, HB_NAME + ".json"), encoding="utf-8"))
        edad_h = (ahora - datetime.datetime.strptime(hb["ts"], "%Y-%m-%dT%H:%M:%SZ")).total_seconds() / 3600
    except Exception:
        edad_h = None
    out.append(("C1", seguidas >= 3 and edad_h is not None and edad_h <= 30,
                "noches seguidas con pasada, sin «fallo» y sin el muro en rojo: %d (hacen falta 3) · último latido hace %s%s"
                % (seguidas, ("%.1f h" % edad_h) if edad_h is not None else "—",
                   (" · descartadas por muro en rojo: %s" % ", ".join(descartadas)) if descartadas else "")))
    # C2: el sembrado llegó y ella lo confirmó
    try:
        s = json.load(open(SEMBRADO, encoding="utf-8"))
    except Exception:
        s = None
    conf = (s or {}).get("confirmado") or {}
    codigo_ok = bool(s and conf.get("codigo") and s.get("sal") and s.get("nonce_hash")
                     and _hash_codigo(conf["codigo"], s["sal"]) == s["nonce_hash"])
    prueba = _prueba_humana(conf.get("codigo"), s.get("ts")) if codigo_ok else None
    entregado = bool(s and s.get("avisado") is True and s.get("entrega") == "entregado")
    out.append(("C2", bool(entregado and codigo_ok and prueba),
                "rojo sembrado: %s" % ("sin sembrar (python3 tools/suite_nocturna.py --sembrar --solo-aviso)" if not s else
                                       "entrega del aviso=%s (debe ser «entregado»), código confirmado coincide=%s, prueba de un prompt HUMANO con el código=%s"
                                       % (s.get("entrega") or ("sin dato" if "entrega" not in s else None), codigo_ok, bool(prueba)))))
    return out


def main(argv):
    if argv[:1] in (["-h"], ["--help"]):
        print(__doc__)
        return 0
    if argv[:1] == ["condiciones"]:
        res = condiciones()
        for k, ok, d in res:
            print("%s %s — %s" % ("✅" if ok else "❌", k, d))
        todo = all(ok for _k, ok, _d in res)
        print("CONDICIONES: %s" % ("SE CUMPLEN LAS TRES; se puede cambiar la norma"
                                   if todo else "NO se cumplen todas; la norma NO se cambia todavía"))
        return 0 if todo else 1
    if argv[:1] == ["confirmar-sembrado"]:
        codigo = " ".join(argv[1:]).strip()
        try:
            sem = json.load(open(SEMBRADO, encoding="utf-8"))
        except Exception:
            print("no hay rojo sembrado: corre antes `--sembrar --solo-aviso`")
            return 2
        if not (sem.get("nonce_hash") and sem.get("sal")):
            print("este sembrado es de una versión anterior (el código estaba en claro en el estado): queda INVALIDADO. "
                  "Genera uno nuevo con `--sembrar --solo-aviso`.")
            return 2
        if sem.get("entrega") != "entregado":
            print("el aviso sembrado NO llegó a {{TITULAR}} (estado: %s). Un aviso aplazado no se confirma: repite el sembrado "
                  "cuando pueda entregarse." % (sem.get("entrega") or "sin dato"))
            return 2
        if not codigo or _hash_codigo(codigo, sem["sal"]) != sem["nonce_hash"]:
            print("el código no es el del aviso sembrado: tiene que ser el que recibió {{TITULAR}} en su aviso")
            return 2
        prueba = _prueba_humana(codigo, sem.get("ts"))
        if not prueba:
            print("NO hay un prompt HUMANO posterior al aviso que contenga el código. Esto no lo confirma un agente: "
                  "{{TITULAR}} tiene que escribir el código en una sesión de Claude Code. Pídeselo y repite.")
            return 2
        sem["confirmado"] = {"codigo": codigo, "ts": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                             "transcript": prueba[0], "extracto": prueba[1]}
        with open(SEMBRADO, "w", encoding="utf-8") as f:
            json.dump(sem, f, ensure_ascii=False)
        print("confirmado (prompt humano en %s)" % prueba[0])
        return 0
    if "--sembrar" in argv and "--solo-aviso" in argv:
        r = _sembrar(_hoy())
        if r["estado"] == "aplazado":
            msg = "APLAZADO al parte, NO entregado: no se ha confirmado nada (%s)" % r["motivo"]
        elif r["avisado"]:
            msg = "entregado: {{TITULAR}} debe teclear el código en una sesión; luego `confirmar-sembrado <código>`"
        else:
            msg = "NO entregado (estado: %s; %s)" % (r["estado"], r["motivo"])
        sys.stderr.write("suite_nocturna: aviso sembrado " + msg + "\n")
        print(json.dumps({"sembrado": True, "avisado": r["avisado"], "entrega": r["estado"], "mensaje": msg}, ensure_ascii=False))
        return 0 if r["avisado"] else 1
    rc, entrada = pasada(sembrar="--sembrar" in argv)
    print(json.dumps(entrada, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception as e:   # noqa: BLE001  el harness caído se oye: latido «fallo» y aviso
        _heartbeat("fallo", error=repr(e))
        try:
            avisar("La suite nocturna ha reventado antes de empezar: %r" % e)
        except Exception:
            pass
        sys.exit(2)
