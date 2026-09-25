#!/usr/bin/env python3
"""tools/bucles_colgados.py — para bucles de espera (until/while + sleep) colgados de una
sesión de Claude Code, antes de que se queden dando vueltas horas y bloqueen el worktree.

POR QUÉ (14-sep-2026, ver feedback-bucles-espera-con-tope). Un subagente `tecnico` dejó en
segundo plano `/bin/zsh -c "until [ -f <output> ] || grep -q EXIT_TEST_ALL <log>; do sleep 5;
done; echo listo"` esperando una marca que nunca llegó. Corrió 14 h 34 min, colgado del proceso
`claude` de esa sesión (cwd en un worktree). Mantuvo la tarea abierta y bloqueó el worktree (el
agente `git` no pudo borrarlo porque veía "sesión viva"). Deuda: `bucle_espera_sin_tope`.

CANDIDATO = proceso shell (zsh/bash/sh) cuyo comando contiene un bucle `until`/`while` con
`sleep`, Y cuya cadena de padres llega a un binario `claude` de Claude Code (p. ej.
".../claude-code/2.1.266/claude.app/Contents/MacOS/claude"), Y cuyo tiempo transcurrido supera
UMBRAL_SEG_DEFAULT (2h, configurable con BTP_BUCLE_UMBRAL_SEG).

SEGURIDAD, innegociable (cada punto tiene test en tests/test_bucles_colgados.py):
  · Nunca se toca un proceso cuya cadena de padres no pase por un binario `claude` de Claude
    Code. Los daemons launchd `com.btp.*` (padre 1, launchd) y sus propios `sleep` quedan
    SIEMPRE fuera — no hay forma de que su cadena de padres llegue a `claude`.
  · Nunca se mata el propio proceso `claude`/Claude.app (no puede casar con el patrón de shell
    de bucle: no es zsh/bash/sh), ni un proceso de OTRO usuario (uid distinto al nuestro).
  · Solo SIGTERM al shell del bucle — sus hijos `sleep` mueren solos en cuanto el bucle deja de
    relanzarlos. SIGKILL solo si sigue vivo tras un reintento (`wait` segundos), y solo a ESE
    mismo shell.
  · `--dry-run` (o BTP_BUCLE_SOLO_AVISO=1): lista y no mata. Por defecto, ACTIVO (mata) para
    bucles de más del umbral — aprobado por {{TITULAR}} el 14-sep-2026. BTP_BUCLE_SOLO_AVISO=1
    vuelve a solo-aviso sin tocar código, para poder apagar el autofix sin desplegar.
  · Si el parseo de `ps` falla, o hay CUALQUIER duda sobre la cadena de padres (excepción al
    resolverla), no se mata — fail-safe hacia NO MATAR — y se cuenta como `ps_error` / se
    descarta el candidato en vez de arriesgar.

Se engancha al patrón de tools/healthcheck.py: `run()` devuelve (alertas, info) con el mismo
shape que el resto de checks (lista de tuplas clave-estable/texto + dict de diagnóstico), para
que healthcheck lo sume a su propia lista de alertas y lo reparta por salida.py como ya hace con
todo lo demás. No abre canal nuevo.

Auditoría: cada parada real dice pid, etime, un extracto del comando (truncado a 200 caracteres,
sin rutas `_PRIVADO*`) y el cwd, en tools/state/healthcheck/bucles_colgados.jsonl. Al parar
alguno (modo activo) se llama una vez `deuda.visto("bucle_espera_sin_tope", ...)` por ciclo (no
por cada pid — si el barrido caza varios en la misma pasada es la MISMA recurrencia, no N
distintas; evita inflar el contador de escalada del libro de deuda).

Uso:
  python3 tools/bucles_colgados.py              # activo: para lo que proceda
  python3 tools/bucles_colgados.py --dry-run     # solo lista, no toca nada
"""
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _casa  # noqa: E402  (raíz del sistema vivo: BTP_REPO/BTP_STATE_DIR, nunca __file__)

LOG = os.path.join(_casa.state_dir(), "healthcheck", "bucles_colgados.jsonl")

# 45 min, no 2 h (20-sep-2026). El daño de un bucle de espera colgado ES el tiempo: el incidente
# que abrió la deuda corrió 14 h 34 min y dejó tarea y worktree bloqueados. Los tres paros reales
# del 20-sep ocurrieron a las 2 h clavadas, o sea que el umbral viejo era el que mandaba, no la
# realidad de la espera: ninguna de esas esperas iba a resolverse sola. Con 45 min el worktree se
# libera antes y nadie pierde una tarde. `BTP_BUCLE_UMBRAL_SEG` sigue mandando sobre esto.
UMBRAL_SEG_DEFAULT = int(os.environ.get("BTP_BUCLE_UMBRAL_SEG") or 45 * 60)
# Interruptor de vuelta a solo-aviso sin tocar código (no cambia el default activo aprobado).
SOLO_AVISO = os.environ.get("BTP_BUCLE_SOLO_AVISO") == "1"
WAIT_SIGKILL = float(os.environ.get("BTP_BUCLE_WAIT_SIGKILL") or 3.0)

_PS = next((p for p in ("/bin/ps", "/usr/bin/ps", "ps") if os.path.exists(p)), "ps")
_LSOF = next((p for p in ("/usr/sbin/lsof", "/usr/bin/lsof", "lsof") if os.path.exists(p)), "lsof")

# Gramática real de un bucle de espera de shell: "until COND; do sleep N; done" (o "while").
# NO basta con que aparezcan las palabras "until"/"while" y "sleep" en cualquier parte del
# comando: un `zsh -c "... eval 'python3 -u - <<EOF ... while cond: time.sleep(30) ... EOF'"`
# real (visto en esta misma máquina el 14-sep-26, otra sesión de Claude probando el MCP de X)
# también las contiene, y NO es un bucle de shell sin tope — es un script con su propio timeout.
# Exigir "do" y "done" (palabra suelta, gramática de shell) alrededor descarta ese caso: Python
# no tiene ni "do" ni "done" como palabras propias. Reluctante y sin cruzar saltos de línea
# reales para acotar el coste del regex en comandos largos.
_RE_BUCLE_ESPERA = re.compile(
    r"\b(until|while)\b[^\n]*?\bdo\b[^\n]*?\bsleep\b[^\n]*?\bdone\b", re.IGNORECASE)
# Binario de Claude Code: p. ej. ".../claude-code/2.1.266/claude.app/Contents/MacOS/claude".
_RE_CLAUDE_BIN = re.compile(r"claude-code/[^/]+/claude\.app/Contents/MacOS/claude\b")
_SHELLS = ("zsh", "bash", "sh")


# ── Lectura de procesos (fail-safe: cualquier duda → None, el llamante no mata nada) ──────────
def _listar_ps(ps_runner=None):
    """[{pid, ppid, etime, uid, cmd}] de TODOS los procesos, o None si el parseo no es fiable.

    `etime` en formato macOS/BSD ("mm:ss", "hh:mm:ss" o "dd-hh:mm:ss"), NO segundos planos:
    el `ps` de macOS no soporta la keyword `etimes` de procps/Linux.
    """
    runner = ps_runner or (lambda: subprocess.run(
        [_PS, "-axo", "pid=,ppid=,etime=,uid=,command="],
        capture_output=True, text=True, timeout=10,
    ).stdout)
    try:
        salida_ps = runner()
    except Exception:
        return None
    if not isinstance(salida_ps, str):
        return None
    procesos = []
    for linea in salida_ps.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        partes = linea.split(None, 4)
        if len(partes) < 5:
            continue
        try:
            pid, ppid, uid = int(partes[0]), int(partes[1]), int(partes[3])
        except ValueError:
            continue
        procesos.append({"pid": pid, "ppid": ppid, "etime": partes[2], "uid": uid, "cmd": partes[4]})
    return procesos


def _etime_a_seg(etime):
    """"19:17:19" → segundos; "3-01:02:03" → con días; "05:23" → mm:ss. None si no se entiende
    (fail-safe: el candidato con etime ilegible no pasa el umbral, no se toca)."""
    try:
        dias = 0
        resto = etime
        if "-" in etime:
            dias_s, resto = etime.split("-", 1)
            dias = int(dias_s)
        partes = [int(x) for x in resto.split(":")]
        if not partes or len(partes) > 3:
            return None
        while len(partes) < 3:
            partes.insert(0, 0)
        h, m, s = partes
        return dias * 86400 + h * 3600 + m * 60 + s
    except Exception:
        return None


def _es_shell(cmd):
    primero = cmd.split(None, 1)[0] if cmd else ""
    base = os.path.basename(primero.lstrip("-"))
    return base in _SHELLS


# Lo que cuenta como TOPE. Medido sobre 13.069 comandos Bash reales de 7 días: 100 traían bucle
# de espera y solo 5 llevaban alguna de estas marcas. OJO: en este Mac NO existe `timeout` ni
# `gtimeout` (command not found), así que el idioma bueno de aquí es el del reloj:
#   fin=$(( $(date +%s) + 600 )); until COND; do [ $(date +%s) -lt $fin ] || break; sleep 5; done
_MARCAS_DE_TOPE = ("timeout ", "gtimeout ", "date +%s", "$SECONDS", "SECONDS -",
                   "for _ in", "for i in")


def es_bucle_espera(cmd):
    """¿El comando trae un bucle de espera de shell (`until|while … do … sleep … done`)?"""
    return bool(_RE_BUCLE_ESPERA.search(cmd or ""))


def tiene_tope(cmd):
    """¿Ese bucle lleva algo que lo corte solo (reloj, contador, timeout)?"""
    return any(m in (cmd or "") for m in _MARCAS_DE_TOPE)


def bucle_sin_tope(cmd):
    """La pregunta completa, para que el hook y el matador usen UNA sola definición."""
    return es_bucle_espera(cmd) and not tiene_tope(cmd)


_es_bucle_espera = es_bucle_espera   # nombre viejo, por si algo lo usaba


def _cadena_pasa_por_claude(ppid_inicial, por_pid, patron=_RE_CLAUDE_BIN, tope=64):
    """Sube por ppid desde `ppid_inicial` buscando un binario `claude` de Claude Code. Cualquier
    duda (pid sin datos, bucle en el árbol) → False: fail-safe hacia NO tocar."""
    actual = ppid_inicial
    vistos = set()
    for _ in range(tope):
        if actual is None or actual <= 1:
            return False
        p = por_pid.get(actual)
        if p is None:
            return False
        if patron.search(p["cmd"]):
            return True
        if actual in vistos:
            return False
        vistos.add(actual)
        actual = p["ppid"]
    return False


def _cwd_de(pid):
    try:
        out = subprocess.run(
            [_LSOF, "-p", str(pid), "-a", "-d", "cwd", "-Fn"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return None
    for linea in out.splitlines():
        if linea.startswith("n"):
            return linea[1:]
    return None


def _redactar_truncar(cmd, n=200):
    limpio = " ".join("[REDACTADO]" if "_PRIVADO" in tok else tok for tok in cmd.split())
    return limpio[:n]


def _fmt_dur(seg):
    return "%.1fh" % (seg / 3600.0)


# ── Detección ──────────────────────────────────────────────────────────────────────────────
def detectar(ps_runner=None, umbral_seg=None, bajo_claude_fn=None):
    """Devuelve (candidatos, por_pid). candidatos es None si `ps` no fue fiable (fail-safe: el
    llamante no debe matar nada). `bajo_claude_fn(pid)` es inyectable para tests — por defecto
    resuelve la cadena de padres real con `ps`."""
    umbral = UMBRAL_SEG_DEFAULT if umbral_seg is None else umbral_seg
    lista = _listar_ps(ps_runner)
    if lista is None:
        return None, {}
    por_pid = {p["pid"]: p for p in lista}
    if bajo_claude_fn is None:
        def bajo_claude_fn(pid, _por_pid=por_pid):
            p = _por_pid.get(pid)
            if p is None:
                return False
            return _cadena_pasa_por_claude(p["ppid"], _por_pid)

    mi_uid = os.getuid()
    mi_pid = os.getpid()
    candidatos = []
    for p in lista:
        if p["pid"] == mi_pid:
            continue
        if p["uid"] != mi_uid:
            continue                                   # nunca un proceso de otro usuario
        etime_seg = _etime_a_seg(p["etime"])
        if etime_seg is None or etime_seg < umbral:
            continue
        if not _es_shell(p["cmd"]):
            continue
        if not _es_bucle_espera(p["cmd"]):
            continue
        try:
            if not bajo_claude_fn(p["pid"]):
                continue
        except Exception:
            continue                                   # duda en la cadena de padres → no tocar
        candidato = dict(p)
        candidato["etime_seg"] = etime_seg
        candidatos.append(candidato)
    return candidatos, por_pid


# ── Parada ─────────────────────────────────────────────────────────────────────────────────
def _existe(pid):
    """¿Sigue habiendo trabajo real en `pid`? Un ZOMBIE ya terminó (solo falta que su padre real
    lo recoja, que no es cosa nuestra) — cuenta como "no vivo" para no forzar un SIGKILL de más
    sobre algo que ya está muerto."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                                    # existe, no es nuestro (no debería darse aquí)
    except Exception:
        return False
    try:
        estado = subprocess.run(
            [_PS, "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=3,
        ).stdout.strip()
        if estado.startswith("Z"):
            return False
    except Exception:
        pass
    return True


def _log_auditable(registro):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        r = dict(registro)
        r["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _parar_uno(p, dry_run=False, wait=WAIT_SIGKILL):
    """SIGTERM al shell del bucle; SIGKILL solo si sigue vivo tras `wait` segundos. Nunca lanza."""
    registro = {
        "pid": p["pid"],
        "etime": p.get("etime"),
        "etime_seg": p.get("etime_seg"),
        "cmd": _redactar_truncar(p["cmd"]),
        "cwd": _cwd_de(p["pid"]),
    }
    if dry_run:
        registro["parado"] = False
        registro["accion"] = "dry-run: no se tocó"
        return registro

    try:
        os.kill(p["pid"], signal.SIGTERM)
    except ProcessLookupError:
        registro["parado"] = True
        registro["accion"] = "ya no existía"
        _log_auditable(registro)
        return registro
    except Exception as e:
        registro["parado"] = False
        registro["accion"] = "error al enviar SIGTERM: %r" % e
        _log_auditable(registro)
        return registro

    accion = "SIGTERM"
    deadline = time.time() + wait
    vivo = _existe(p["pid"])
    while vivo and time.time() < deadline:
        time.sleep(0.1)
        vivo = _existe(p["pid"])
    if vivo:
        try:
            os.kill(p["pid"], signal.SIGKILL)
            accion = "SIGTERM+SIGKILL"
        except ProcessLookupError:
            pass
        except Exception:
            pass
        time.sleep(0.2)
        vivo = _existe(p["pid"])

    registro["parado"] = not vivo
    registro["accion"] = accion if not vivo else accion + " (sigue vivo)"
    _log_auditable(registro)
    return registro


# ── Enganche al patrón de healthcheck.py: run() → (alertas, info) ────────────────────────────
def run(*, dry_run=None, umbral_seg=None, ps_runner=None, bajo_claude_fn=None):
    if dry_run is None:
        dry_run = SOLO_AVISO
    umbral = UMBRAL_SEG_DEFAULT if umbral_seg is None else umbral_seg
    alertas = []
    info = {"umbral_seg": umbral, "dry_run": dry_run}

    candidatos, _ = detectar(ps_runner=ps_runner, umbral_seg=umbral, bajo_claude_fn=bajo_claude_fn)
    if candidatos is None:
        info["ps_error"] = True
        return alertas, info
    info["candidatos"] = len(candidatos)
    if not candidatos:
        return alertas, info

    resultados = [_parar_uno(p, dry_run=dry_run) for p in candidatos]
    info["resultado"] = resultados
    parados = [r for r in resultados if r.get("parado")]

    if dry_run:
        alertas.append((
            "bucle_colgado_detectado",
            "🔁 %d bucle(s) de espera bajo Claude Code llevan más de %s sin tope (dry-run, no "
            "se tocaron): pid %s."
            % (len(candidatos), _fmt_dur(umbral), ", ".join(str(p["pid"]) for p in candidatos)),
        ))
    elif parados:
        # Solo cuenta como RECAÍDA lo que nació DESPUÉS de cerrarse la deuda. El 20-sep la deuda
        # se cerró con el aviso al escribir el bucle (c3a91e6, 20:46), y a las 2,6 h estaba
        # reabierta y ESCALADA como «regresión». No lo era: los 7 bucles que este vigía mató ese
        # día habían empezado antes de las 20:46 (el último, a las 20:15). Matarlos era su
        # trabajo; contarlos como fallo del arreglo hacía que un arreglo que funciona pareciera
        # roto. Los residuos se siguen matando y registrando, pero no reabren nada.
        nuevos = _nacidos_tras_cierre(parados, "bucle_espera_sin_tope")
        if nuevos:
            try:
                import deuda as _deuda_mod
                _deuda_mod.visto(
                    "bucle_espera_sin_tope",
                    nota="parado(s) por bucles_colgados.py: %d pid(s) NACIDOS tras el cierre, "
                         "ej. pid %d (%s)"
                         % (len(nuevos), nuevos[0]["pid"], _redactar_truncar(nuevos[0]["cmd"], 60)),
                )
            except Exception:
                pass
        alertas.append((
            "bucle_colgado_parado",
            "🔁 Paré %d bucle(s) de espera colgado(s) bajo Claude Code (más de %s sin tope): "
            "pid %s. Registro en tools/state/healthcheck/bucles_colgados.jsonl."
            % (len(parados), _fmt_dur(umbral), ", ".join(str(p["pid"]) for p in parados)),
        ))
    return alertas, info


def _nacidos_tras_cierre(parados, clave, ahora=None):
    """De los procesos parados, los que EMPEZARON después de que se cerrara la deuda `clave`.

    Si la deuda no está cerrada (o no se puede leer), todos cuentan: sin cierre no hay «antes
    del arreglo», y ante la duda se reporta. `ahora` es para los tests.
    """
    try:
        import deuda as _d
        it = (_d._cargar() or {}).get(clave) or {}
        cierre = it.get("cerrado_ts") if it.get("estado") == "cerrado" else None
    except Exception:            # noqa: BLE001
        cierre = None
    if not cierre:
        return list(parados)
    t = ahora if ahora is not None else time.time()
    return [p for p in parados if (t - _etime_a_seg(p.get("etime") or "0:0")) > float(cierre)]


def main(argv):
    dry = "--dry-run" in argv
    alertas, info = run(dry_run=True if dry else None)
    print(json.dumps({"alertas": alertas, "info": info}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
