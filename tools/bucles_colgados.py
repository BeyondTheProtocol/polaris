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
import bisect
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


# ── Detector de bucles de espera sin tope (reescrito el 25-sep-2026, dos vueltas adversariales) ─
# El hook `regla_en_accion` pasó ese día de avisar a DENEGAR. Dos verificaciones adversariales
# (130 payloads ejecutados) y una comparación sobre 31.302 comandos Bash reales dieron la forma:
#   · el bucle se localiza por PASOS (palabra clave en posición de comando → `do` → el primer
#     `done` que tenga un `sleep` delante), cruzando líneas: un agente casi siempre escribe
#     `do` ↵ `  sleep 5` ↵ `done`, y la regex de una línea de antes no lo vio nunca;
#   · el tope se juzga DENTRO del bucle, en sus tests: reloj (date +%s/+%H, SECONDS, o una
#     variable sacada de `date +%s`), autoincremento, o un contador que avanza de verdad y no se
#     reinicia; o un `timeout N bash -c` que lo envuelve. Comparar un estado externo con un
#     número (`until [ "$(… last_uid)" -gt 5000 ]`) no es tope;
#   · el cuerpo de un heredoc solo se ignora si el receptor es inerte, la etiqueta va entre
#     comillas o el cuerpo no tiene `$(`/backticks, hay línea de cierre, y nada lo ejecuta
#     después en la cabecera (tubería, shell).
# FUERA DE ALCANCE, a propósito: este freno es para un agente que se olvida del tope, no para
# alguien que quiera colarse. No se persigue: ofuscación (`done` en variable, `s\leep`, base64),
# esperas sin la palabra `sleep` (perl select, read -t, bucle activo), contadores FINGIDOS
# (`n+0`, `|| true`, `-eq` inalcanzable, reinicio a 1, subshell), un script escrito a fichero y
# ejecutado después, `os.system` desde python, `.shell` de sqlite3, alias `!` de git.

# Shells que EJECUTAN lo que se les pasa por un heredoc o una tubería.
_SHELLS_RECEPTOR = ("zsh", "bash", "sh", "ksh", "dash", "fish")
# Receptores INERTES de un heredoc: lo que reciben es texto o código de otro lenguaje, no shell.
_INERTES = ("cat", "tee", "git", "gh", "jq", "node", "ruby", "perl", "wc", "pbcopy")
# Prefijos que no son el programa: `sudo bash`, `env X=1 bash`, `nohup`, `time`, `exec`…
_PREFIJOS = ("sudo", "env", "nohup", "time", "exec", "caffeinate", "nice", "command", "builtin")

_LIM_DO, _LIM_DONE = 2000, 20000
_RE_HEREDOC_INI = re.compile(r"(?:^|(?<=[\s;&|(]))<<(?!<)(-?)[ \t]*(['\"]?)([A-Za-z_][\w.-]*)\2")
_RE_PALABRA_BUCLE = re.compile(r"\b(until|while)\b")
_RE_DO = re.compile(r"\bdo\b")
_RE_DONE = re.compile(r"\bdone\b")
_RE_SLEEP = re.compile(r"\bsleep\b")
_RE_WHILE_READ = re.compile(r"^while\s+(?:IFS=\S*\s+)?read\b")
_RE_ENTRADA_INFINITA = re.compile(r"\btail\s+(?:-\w*\s+)*-[a-zA-Z]*[fF]|<\s*<\(|\byes\s*\|")
# `timeout N … bash -c '…'` delante del bucle, en la MISMA línea y no en un comentario.
_RE_TIMEOUT = re.compile(r"(?<![\w-])g?timeout\s+\d")
_RE_TIMEOUT_ENVUELVE = re.compile(r"(?<![\w-])g?timeout\s+\d+\S*\s+(?:\S+\s+){0,3}(?:ba|z|k|da)?sh\b")
_MARGEN_TIMEOUT = 160
# Expresiones de test del bucle: `[ … ]`, `[[ … ]]`, `(( … ))`, `test …`.
_RE_TEST = re.compile(r"\[\[?[^\]\n]*\]\]?|\(\([^)\n]*\)\)|\btest\s[^;&|\n]*")
_RE_RELOJ = re.compile(r"date\s+(?:-u\s+)?['\"]?\+['\"]?%[sH]|\b(?:EPOCH)?SECONDS\b")
_RE_VAR_RELOJ = re.compile(r"\b(\w+)=\$\((?:\(\s*\$\()?\s*date\s+[^)]*%s")
_RE_AUTOINC = re.compile(r"\w\+\+|\+\+\s*\w")                    # (( tries++ < 30 )), $(( ++n ))
_OP = r"-(?:ge|gt|le|lt|eq)"
_LIM = r"(?:\d+|\$\{?\w+\}?|\"\$\{?\w+\}?\")"
_RE_COMPARA_VAR = re.compile(
    r"\$\{?(\w+)\}?\"?\s+%s\s+%s" % (_OP, _LIM)                  # [ $n -lt 60 ] / [ $n -ge $MAX ]
    + r"|%s\s+%s\s+\"?\$\{?(\w+)\}?" % (r"\d+", _OP)              # [ 10 -gt $i ]
    + r"|\(\(\s*\$?(\w+)\s*[<>]=?\s*\$?\w+")                     # (( n < MAX ))
# Mensajes de commit / PR con la palabra «until … sleep … done» dentro no son un bucle.
_RE_MENSAJE = re.compile(r"(?:\s-m|--message|--body|--title)\s+(\"(?:[^\"\\]|\\.)*\"|'[^']*')")


def _es_receptor_shell(palabras):
    return any(os.path.basename(p) in _SHELLS_RECEPTOR for p in palabras)


def _programa(palabras):
    for p in palabras:
        base = os.path.basename(p)
        if base in _PREFIJOS or p.startswith("-") or re.match(r"^\w+=", p):
            continue
        return base
    return ""


def _sin_heredocs(cmd):
    """El comando sin el cuerpo de los heredocs INERTES (python, cat a un fichero, git commit -F
    -…). Ante la duda, el cuerpo SE QUEDA: receptor desconocido, shell o tubería en la cabecera,
    etiqueta sin comillas con `$(`/backticks en el cuerpo (bash los expande), o sin cierre."""
    lineas = cmd.split("\n")
    posiciones = {}
    for k, l in enumerate(lineas):
        posiciones.setdefault(l, []).append(k)
        if l.lstrip("\t") != l:
            posiciones.setdefault(l.lstrip("\t"), []).append(k)
    out, i = [], 0
    while i < len(lineas):
        linea = lineas[i]
        m = _RE_HEREDOC_INI.search(linea)
        out.append(linea)
        i += 1
        if not m:
            continue
        guion, comilla, etiqueta = m.group(1), m.group(2), m.group(3)
        antes, despues = linea[:m.start()], linea[m.end():]
        segmento = re.split(r"&&|\|\||;|\|", antes)[-1].split()
        prog = _programa(segmento)
        inerte = ((prog in _INERTES or prog.startswith("python")) and "|" not in despues
                  and not _es_receptor_shell(antes.split() + despues.split()))
        if not inerte:
            continue
        cierre = bisect.bisect_left(posiciones.get(etiqueta, []), i)
        cands = posiciones.get(etiqueta, [])
        j = None
        for c in cands[cierre:]:
            if guion or lineas[c] == etiqueta:
                j = c
                break
        if j is None:
            continue                                   # sin cierre: ante la duda, se lee
        cuerpo = "\n".join(lineas[i:j])
        if not comilla and ("$(" in cuerpo or "`" in cuerpo):
            continue                                   # bash expande el cuerpo: se ejecuta
        i = j
    return "\n".join(out)


def _en_posicion_de_comando(txt, ini):
    """`until`/`while` como COMANDO, no dentro de una cadena o de un nombre de fichero."""
    previo = txt[max(0, ini - 40):ini].rstrip(" \t")   # ventana fija: sin ella, cuadrático
    if (not previo and ini < 40) or (previo and previo[-1] in ";&|(\n{!'\""):
        return True                                    # también `bash -c 'while …'`
    # `-c` sin comillas: así sale en `ps` el shell de un bucle vivo (`zsh -c while true; do …`).
    return bool(re.search(r"(?:^|[\s;])(?:do|then|else|time|exec|nohup|-c)$", previo))


def _bucles(cmd):
    """[(ini, fin, texto_del_bucle, texto, relojes)] de cada `until|while … do … done` que duerme dentro,
    sobre el comando sin heredocs inertes ni mensajes de commit."""
    txt = _sin_heredocs(_RE_MENSAJE.sub(" -m ''", cmd or ""))
    infinita = bool(_RE_ENTRADA_INFINITA.search(txt))
    # Posiciones precalculadas + búsqueda binaria: con 200 KB hostiles la versión que recorría el
    # texto por cada palabra clave tardaba 32 s, y el hook tiene 8 s (medido el 25-sep-2026).
    dos = [m.end() for m in _RE_DO.finditer(txt)]
    sleeps = [m.start() for m in _RE_SLEEP.finditer(txt)]
    dones = [(m.start(), m.end()) for m in _RE_DONE.finditer(txt)]
    ini_dones = [a for a, _b in dones]
    relojes = frozenset(_RE_VAR_RELOJ.findall(txt))    # una vez por comando, no por bucle
    res = []
    for m in _RE_PALABRA_BUCLE.finditer(txt):
        if not _en_posicion_de_comando(txt, m.start()):
            continue
        k = bisect.bisect_left(dos, m.end() + 2)
        if k == len(dos) or dos[k] - m.end() > _LIM_DO:
            continue
        d_fin = dos[k]
        s = bisect.bisect_left(sleeps, d_fin)          # el primer `sleep` tras el `do`…
        if s == len(sleeps) or sleeps[s] - d_fin > _LIM_DONE:
            continue
        f = bisect.bisect_left(ini_dones, sleeps[s])   # …y el primer `done` tras ese `sleep`
        if f == len(dones) or dones[f][0] - d_fin > _LIM_DONE:
            continue
        cuerpo = txt[m.start():dones[f][1]]
        if not (_RE_WHILE_READ.match(cuerpo) and not infinita):
            res.append((m.start(), dones[f][1], cuerpo, txt, relojes))
    return res


def _incrementa(var, cuerpo):
    """¿`var` avanza dentro del bucle y nunca vuelve a empezar? (`z=0` dentro = no es contador)."""
    v = re.escape(var)
    n = r"[1-9]\d*"
    formas = (r"\b{v}=\$\(\(\s*\$?{v}\s*\+\s*{n}\s*\)\)", r"\b{v}=\$\(\(\s*{n}\s*\+\s*\$?{v}\s*\)\)",
              r"\b{v}\+\+", r"\+\+\s*{v}\b", r"\b{v}\s*\+=\s*{n}", r"\blet\s+[\"']?{v}\+\+",
              r"\blet\s+[\"']?{v}\s*=\s*\$?{v}\s*\+\s*{n}", r"\(\(\s*{v}\s*=\s*\$?{v}\s*\+\s*{n}",
              r"\b{v}=\$\[\s*\$?{v}\s*\+\s*{n}\s*\]", r"\b{v}=\$\(expr\s+\$?{v}\s*\+\s*{n}\s*\)")
    sube = any(re.search(f.format(v=v, n=n), cuerpo) for f in formas)
    reinicia = re.search(r"(?<![\w$])%s=0\b" % v, cuerpo)
    return sube and not reinicia


def _con_tope(bucle):
    """¿Este bucle se corta solo? Reloj o autoincremento en un test, un contador que avanza y no
    se reinicia, o un `timeout N … sh -c` que lo envuelve en su misma línea."""
    ini, _fin, cuerpo, txt, relojes = bucle
    if _RE_TIMEOUT.search(cuerpo):
        return True
    linea = txt[max(0, ini - _MARGEN_TIMEOUT):ini].split("\n")[-1].split("#")[0]
    if _RE_TIMEOUT_ENVUELVE.search(linea):
        return True
    d = _RE_DO.search(cuerpo)
    dentro = cuerpo[d.end():] if d else cuerpo
    for t in _RE_TEST.findall(cuerpo):
        if _RE_RELOJ.search(t) or _RE_AUTOINC.search(t):
            return True
        if any(re.search(r"\$\{?%s\b" % re.escape(r), t) for r in relojes):
            return True
        for m in _RE_COMPARA_VAR.finditer(t):
            var = m.group(1) or m.group(2) or m.group(3)
            if var and _incrementa(var, dentro):
                return True
    return False


def es_bucle_espera(cmd):
    """¿El comando trae un bucle de espera de shell (`until|while … do … sleep … done`)?"""
    return bool(_bucles(cmd))


def tiene_tope(cmd):
    """¿TODOS sus bucles de espera llevan algo que los corte solos (reloj, contador, timeout)?"""
    return all(_con_tope(b) for b in _bucles(cmd))


def bucle_sin_tope(cmd):
    """La pregunta completa, para que el hook y el matador usen UNA sola definición."""
    return any(not _con_tope(b) for b in _bucles(cmd))


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


# El shell de Claude Code envuelve cada comando: `/bin/zsh -c source <snapshot> … && eval '<CMD>'
# < /dev/null && pwd -P >| /tmp/claude-XXXX-cwd`. Hasta el 25-sep-2026 el extracto eran los
# primeros 200 caracteres, que son SIEMPRE ese preámbulo: las cinco paradas del registro decían
# «source …shell-snapshots…» y ninguna qué bucle era. Se guarda lo de dentro del `eval`.
_RE_EVAL = re.compile(r"\beval '(.*)' < /dev/null", re.S)


def _redactar_truncar(cmd, n=300):
    m = _RE_EVAL.search(cmd or "")
    nucleo = m.group(1).replace("'\\''", "'") if m else (cmd or "")
    limpio = " ".join("[REDACTADO]" if "_PRIVADO" in tok else tok for tok in nucleo.split())
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


# ── Chrome headless huérfano (26-sep-2026, deuda `chrome_headless_huerfano`) ─────────────────
# Tres Chrome headless que lanzó `tools/captura_visor.mjs` se quedaron huérfanos cuando el
# script murió a medias: ~27 h con dos pestañas cada uno al 100 % de CPU, unos 6 de los 10
# núcleos del Mac. Los 9 hooks de cada Bash pasaron de 0,7 s a 6,5 s y todo Polaris iba lento.
# Nadie avisó. `tools/_chrome_headless.mjs` ya cierra Chrome en cualquier salida del script;
# esto es la red de debajo para lo que no se puede atrapar (un `kill -9` al script) y para los
# scripts que no usen el ayudante.
#
# CANDIDATO = proceso principal de Google Chrome (no un Helper) que:
#   · lleva `--headless`: el Chrome de {{TITULAR}} no lo lleva, así que nunca se toca;
#   · tiene el perfil en /tmp o en el TMPDIR del sistema (/var/folders): es de un script;
#   · es HUÉRFANO (PPID 1): si su padre sigue vivo, alguien lo está usando;
#   · es de nuestro uid y lleva más de UMBRAL_CHROME_SEG vivo.
# Se para el proceso y sus hijos directos (las pestañas, que eran las que quemaban CPU).
UMBRAL_CHROME_SEG = int(os.environ.get("BTP_CHROME_UMBRAL_SEG") or 3600)
_RE_CHROME_PRINCIPAL = re.compile(r"Google Chrome\.app/Contents/MacOS/Google Chrome(?=\s|$)")
_RE_PERFIL_TEMPORAL = re.compile(
    r"--user-data-dir=(?:/private)?(?:/tmp/|/var/folders/)")


def detectar_chrome(ps_runner=None, umbral_seg=None):
    """(candidatos, por_pid). candidatos es None si `ps` no fue fiable: fail-safe, no se mata."""
    umbral = UMBRAL_CHROME_SEG if umbral_seg is None else umbral_seg
    lista = _listar_ps(ps_runner)
    if lista is None:
        return None, {}
    por_pid = {p["pid"]: p for p in lista}
    mi_uid = os.getuid()
    candidatos = []
    for p in lista:
        cmd = p["cmd"]
        if p["uid"] != mi_uid or p["ppid"] != 1:
            continue
        if not _RE_CHROME_PRINCIPAL.search(cmd) or "--headless" not in cmd:
            continue
        if not _RE_PERFIL_TEMPORAL.search(cmd):
            continue
        etime_seg = _etime_a_seg(p["etime"])
        if etime_seg is None or etime_seg < umbral:
            continue
        c = dict(p)
        c["etime_seg"] = etime_seg
        c["hijos"] = [q["pid"] for q in lista if q["ppid"] == p["pid"] and q["uid"] == mi_uid]
        candidatos.append(c)
    return candidatos, por_pid


def _parar_chrome(p, dry_run=False, wait=WAIT_SIGKILL):
    """SIGTERM al Chrome y a sus pestañas; SIGKILL a lo que siga vivo tras `wait`. Nunca lanza."""
    perfil = re.search(r"--user-data-dir=(\S+)", p["cmd"])
    registro = {"tipo": "chrome_headless_huerfano", "pid": p["pid"], "hijos": p.get("hijos", []),
                "etime": p.get("etime"), "etime_seg": p.get("etime_seg"),
                "perfil": perfil.group(1) if perfil else None}
    if dry_run:
        registro.update(parado=False, accion="dry-run: no se tocó")
        return registro
    pids = [p["pid"]] + list(p.get("hijos", []))
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
    deadline = time.time() + wait
    while time.time() < deadline and any(_existe(x) for x in pids):
        time.sleep(0.1)
    accion = "SIGTERM"
    vivos = [x for x in pids if _existe(x)]
    if vivos:
        accion = "SIGTERM+SIGKILL"
        for pid in vivos:
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
        time.sleep(0.2)
    vivo = _existe(p["pid"])
    registro.update(parado=not vivo, accion=accion if not vivo else accion + " (sigue vivo)")
    _log_auditable(registro)
    return registro


def run_chrome(*, dry_run=None, umbral_seg=None, ps_runner=None):
    """Mismo contrato que run(): (alertas, info)."""
    if dry_run is None:
        dry_run = SOLO_AVISO
    umbral = UMBRAL_CHROME_SEG if umbral_seg is None else umbral_seg
    alertas, info = [], {"umbral_seg": umbral, "dry_run": dry_run}
    candidatos, _ = detectar_chrome(ps_runner=ps_runner, umbral_seg=umbral)
    if candidatos is None:
        info["ps_error"] = True
        return alertas, info
    info["candidatos"] = len(candidatos)
    if not candidatos:
        return alertas, info
    resultados = [_parar_chrome(p, dry_run=dry_run) for p in candidatos]
    info["resultado"] = resultados
    parados = [r for r in resultados if r.get("parado")]
    pids = ", ".join(str(p["pid"]) for p in candidatos)
    if dry_run:
        alertas.append(("chrome_huerfano_detectado",
                        "🧟 %d Chrome headless huérfano(s) de un script de captura llevan más de %s "
                        "vivos (dry-run, no se tocaron): pid %s." % (len(candidatos), _fmt_dur(umbral), pids)))
    elif parados:
        try:
            import deuda as _deuda_mod
            _deuda_mod.visto("chrome_headless_huerfano",
                             nota="parado(s) por bucles_colgados.py: %d, ej. pid %d, perfil %s"
                                  % (len(parados), parados[0]["pid"], parados[0].get("perfil")))
        except Exception:
            pass
        alertas.append(("chrome_huerfano_parado",
                        "🧟 Cerré %d Chrome headless huérfano(s) de un script de captura (más de %s "
                        "vivos, sin padre): pid %s. Se comían CPU para nada. Registro en "
                        "tools/state/healthcheck/bucles_colgados.jsonl."
                        % (len(parados), _fmt_dur(umbral), ", ".join(str(r["pid"]) for r in parados))))
    return alertas, info


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
    ch_alertas, ch_info = run_chrome(dry_run=True if dry else None)
    print(json.dumps({"alertas": alertas + ch_alertas, "info": info, "chrome": ch_info},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
