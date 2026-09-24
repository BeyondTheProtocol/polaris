#!/usr/bin/env python3
"""muro_guard.py — guard PreToolUse del «muro» de Polaris (Beyond the Protocol). v2.

Política FAIL-CLOSED para el lazo autónomo 24/7. Lee el JSON del hook por stdin,
tokeniza el comando Bash con shlex (no substring/regex) y aplica ALLOWLIST de
binarios por perfil (todo lo no permitido se DENIEGA), más reglas finas para los
binarios permitidos con formas peligrosas (git/curl/python/find/rm).

v2 cierra los bypasses del red-team (verificacion, setup day):
  · separador GENÉRICO de subcomandos (cualquier token de pura puntuación shell:
    |& >& ;; ;& || && ... ) -> ya no se cuela `echo x |& bash -c id`.
  · allowlist en privileged -> make/cmake/awk/sed/ed/vim/perl5.30/ruby2.7/parallel… fuera.
  · git -c/-p (pager/alias/config = ejecución) prohibido.
  · curl -K/--config/-T/--upload-file/@upload prohibido.
  · cuarentena sin awk/sed/jq; find sin -fprint.
  · SECRET_HINTS en minúsculas + ampliado (.env, ~/.aws, ~/.ssh, credentials…).

v4 (A1, 10-jul-26 — hallazgo A del comité de arquitectura) cierra la escalada de sandbox en un
retry: un transcript real del barrido de git (30-jun) mostró que un `Bash` DENEGADO por el
sistema de permisos (p.ej. `git branch -d ...`, sin `dangerouslyDisableSandbox`) se reintentó
en la MISMA sesión con `"dangerouslyDisableSandbox": true` — "denegado → reintentar quitando el
candado" en vez de aplazar/preguntar. Generalizado a `worktree prune/remove`, `push`, `reset`,
`clean` una noche sin supervisión, esa clase de reintento podría mutar `.git` sin dejar un
comando "raro" en el transcript (justo el patrón que el informe del 4-jul no pudo descartar).
Este guard deniega SIEMPRE cualquier `Bash` con `dangerouslyDisableSandbox` truthy cuyo comando
contenga una mutación de git de la lista de riesgo — pase lo que pase con el resto del análisis
(incluida la allowlist normal de `branch`, que sí permite `-d`/`-D` SIN el flag de escalada).

v3 (B1/B2/B3) cierra la clase «plantar + auto-ejecutar» saltandose el choke-point
de salida (tools/salida.py), barrida sobre TODOS los escritores y TODOS los
disparadores (no caso a caso):
  · escritores: Write/Edit (B1) y cp/mv/ln/tee/split/touch/dd/install/rsync
    (FILE_WRITE_BINS) y curl -O / wget no pueden crear codigo/ejecutables (.py/.sh/
    .pth/…) ni config critica (.git/, .venv/site-packages, dotfiles de shell, …).
  · disparadores: python solo corre .py del repo (que ya no se pueden plantar);
    git hooks (.git/ en WRITE_CRIT) y git config = lista BLANCA de claves
    (sin -f/--file/--blob/-e → no se cuela editor/pager/filter/gpg.program/…);
    restic --password/verbose-command prohibidos.
  · entrega hacia fuera: api.telegram.org fuera del NET_ALLOW de bash → la unica
    boca al exterior es tools/salida.py (auditado, con allowlist destino+accion).

Contrato (code.claude.com/docs/hooks): exit 0 -> permite · exit 2 -> DENIEGA.
El wrapper muro_guard.sh convierte CUALQUIER salida != 0 en exit 2 (fail-closed).

Límite RESIDUAL (honesto): el perfil privilegiado ejecuta los .py de CONFIANZA del
repo y algunos llaman a APIs de modelo (api.x.ai, api.perplexity.ai, api.anthropic.com)
vía urllib — pero SOLO desde los .py del repo (grok.py, perplexity.py, nvidia.py),
nunca desde bash curl (GRAVE-1: hosts de modelo fuera de NET_ALLOW desde 2026-06-28).
El muro de comandos bloquea las vías conocidas de plantar/ejecutar código nuevo, pero
un allowlist de binarios no se puede demostrar hermético: la contención ÚLTIMA es
ARQUITECTÓNICA — el privilegiado se confía por no ver NUNCA texto crudo no confiable
(eso corre en cuarentena, sin python/red), + (futuro) firewall saliente. El choke-point
de salida es la barrera final de «nada fuera sin OK», en CÓDIGO, no en instrucción.
"""
import glob
import json
import os
import re
import shlex
import subprocess
import sys

REPO = os.path.expanduser("~/claudecode")
# Kill-switch: el de dentro del repo (.HALT) Y el de fuera (~/.btp.HALT, A1 — el que se
# le enseña a {{TITULAR}}). El muro debe frenar al agente EN VUELO ante CUALQUIERA de los dos.
# BTP_HALT_FILES (":" sep) permite aislarlos en tests sin tocar los reales.
HALT_FILES = tuple(os.environ["BTP_HALT_FILES"].split(":")) if os.environ.get("BTP_HALT_FILES") \
    else (os.path.join(REPO, ".HALT"), os.path.expanduser("~/.btp.HALT"))
PROFILE = os.environ.get("MURO_PROFILE", "privileged")

# Allowlist de red para `curl`/`wget` en BASH = SOLO bases de lectura ingeniera y
# servicios auxiliares propios. Los hosts de ENTREGA hacia fuera (api.telegram.org,
# gmail) NO van aquí a propósito (B3): la única vía de salida es tools/salida.py.
#
# GRAVE-1 (2026-06-28): los hosts de MODELO (api.x.ai, api.perplexity.ai,
# api.anthropic.com, claude.ai, downloads.claude.ai) han sido ELIMINADOS de esta
# lista. Esos modelos son accesibles ÚNICAMENTE por las lane-tools Python
# (grok.py / perplexity.py / nvidia.py) vía urllib.request — nunca por bash curl.
# Permitir curl a un modelo permitía a un agente privilegiado hablar con un LLM
# saltándose el borde (borde.py). Ahora esa vía queda cerrada en el muro.
NET_ALLOW = {
    # analytics propio (umami.py llama con curl, no hay SDK Python disponible)
    "api.umami.is",
    # bases científicas de lectura (verifica_citas.py + otras tools de investigación)
    "pubmed.ncbi.nlm.nih.gov", "eutils.ncbi.nlm.nih.gov", "www.ncbi.nlm.nih.gov",
    "clinicaltrials.gov",
    # GitHub (lectura de repos públicos, raw assets)
    "api.github.com", "raw.githubusercontent.com",
}

# Allowlist de binarios del perfil PRIVILEGIADO (orquestador/trabajo).
ALLOW_PRIV = {
    # lectura / navegación
    "ls", "cat", "head", "tail", "wc", "grep", "egrep", "fgrep", "rg", "find",
    "file", "stat", "du", "df", "tree", "sort", "uniq", "cut", "tr", "comm",
    "diff", "cmp", "basename", "dirname", "realpath", "pwd", "cd", "date",
    "echo", "printf", "which", "type", "sleep", "seq", "tac", "nl", "rev",
    "column", "fold", "head", "shasum", "sha256sum", "md5", "md5sum", "xxd",
    "base64", "expr", "test", "true", "false", "jq", "cksum", "wc",
    # ficheros locales
    "mkdir", "mv", "cp", "rm", "touch", "chmod", "ln", "mktemp", "tee", "split",
    # sistema del proyecto
    "git", "python", "python3", "python2", "python3.9", "python3.12",
    "restic", "gitleaks", "curl", "wget",
}

PY_BINS = {"python", "python3", "python2", "python3.9", "python3.12", "python3.13"}
# git: ALLOWLIST de subcomandos (una denylist siempre filtra — checkout→switch→archive
# →bundle→format-patch…). Solo lo que el comité `git` necesita (inspección + commit/rama
# local). Todo lo demás → DENY: red (push/clone/fetch/pull), restauración del árbol
# (checkout-path/switch/restore/reset/stash/cherry-pick/rebase/merge/revert/clean/rm/mv),
# y escritura de salida a ruta arbitraria (archive/bundle/format-patch -o), exec (difftool/
# mergetool), plomería (apply/am/read-tree/checkout-index/hash-object/update-index/…).
GIT_ALLOW_SUB = {
    "status", "diff", "log", "show", "branch", "rev-parse", "describe", "var",
    "ls-files", "ls-tree", "cat-file", "symbolic-ref", "merge-base", "blame",
    "shortlog", "reflog", "rev-list", "name-rev", "for-each-ref", "show-ref",
    "diff-tree", "diff-index",
    "add", "commit", "checkout", "tag", "config",   # trabajo local (checkout/config acotados abajo)
}
# `git config` tiene DECENAS de claves que ejecutan un comando (core.editor, *.pager,
# filter.*.clean/.smudge, *.textconv, merge.*.driver, sequence.editor, core.sshCommand,
# core.fsmonitor, credential.*.helper, alias.*…). Una lista NEGRA siempre deja un hueco
# → escritura de config = lista BLANCA: solo estas claves inocuas; cualquier otra → DENY.
GIT_CONFIG_WRITE_ALLOW = {"user.name", "user.email", "commit.gpgsign", "core.autocrlf"}
GIT_CONFIG_READ_FLAGS = {"--get", "--get-all", "--get-regexp", "--get-urlmatch",
                         "--list", "-l", "--name-only", "--show-origin"}
FIND_DENY = {"-exec", "-execdir", "-ok", "-okdir", "-delete",
             "-fprintf", "-fprint", "-fls", "-fprint0"}
CURL_DANGER = {"-K", "--config", "-T", "--upload-file"}
# restic ejecuta comandos arbitrarios via estas opciones (las pasa a `sh -c`):
# --password-command / --verbose-command / hooks de opciones backend. Ejecucion fuera
# del choke-point con un binario permitido -> prohibido (defensa en profundidad B3).
RESTIC_EXEC_FLAGS = ("--password-command", "--verbose-command")

# A1 (10-jul-26, hallazgo A) — mutaciones de `git` que jamás se sirven bajo una escalada de
# sandbox (dangerouslyDisableSandbox=true), sea cual sea el resto del análisis. Valor = None
# ("siempre" que se vea ese subcomando) o una tupla de flags/subcomandos que lo hacen mutante.
# `branch`/`worktree` sin flag mutante (p.ej. `git branch` de solo listar, `git worktree list`)
# NO caen aquí — no son la clase de riesgo (fail-safe, no sobre-bloquea lectura).
GIT_MUTATE_DENY_ON_ESCALATION = {
    "push": None, "reset": None, "clean": None, "rm": None,
    "branch": ("-d", "-D", "--delete", "--delete-force"),
    "worktree": ("prune", "remove"),
}

# Fix (c) (11-jul-26, radar `_cajita/radar-mejoras-10jul/C-guard-merge-master.md`) — gate
# BLANDO de escritura a la BASE (master/main). Origen: una sesión con permisos escalados
# fusionó ramas y commiteó a master anoche (10-jul) sin gate humano. Política elegida:
# BLANDA (env var), no el token-de-humano con TTL — fricción mínima contra lo ACCIDENTAL.
#
# TOGGLE apagado por defecto (deploy-safe/inerte): la regla SOLO actúa si existe el fichero
# `.claude/hooks/.base_gate_on`. Sin ese fichero, `_BASE_GATE_ON` es False y el bloque de
# abajo no cambia NADA del comportamiento de siempre. BTP_BASE_GATE_TOGGLE (ruta) permite
# aislar el toggle en tests sin tocar el fichero real — mismo patrón que BTP_HALT_FILES.
_BASE_GATE_TOGGLE_PATH = os.environ.get(
    "BTP_BASE_GATE_TOGGLE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".base_gate_on"))
_BASE_GATE_ON = os.path.exists(_BASE_GATE_TOGGLE_PATH)

# Subcomandos de `git` que la regla vigila (los tres que ESCRIBEN a la base: fusionar,
# commitear directo, empujar). NOTA: `merge` y `push` YA están fuera de GIT_ALLOW_SUB (no
# se sirven en NINGÚN caso, con o sin este toggle — "push ya cubierto por el muro", per el
# diseño). Este gate solo cambia comportamiento OBSERVABLE para `commit` (que sí está en la
# allowlist); para merge/push queda como defensa en profundidad si algún día se allowlistan.
GIT_BASE_GATE_SUBS = {"merge", "commit", "push"}
GIT_BASE_BRANCHES = {"master", "main"}
# Repo sobre el que se pregunta la rama actual — override SOLO para tests (mismo patrón que
# BTP_HALT_FILES): la producción SIEMPRE mira ~/claudecode (REPO), nunca un repo arbitrario
# que el propio comando pudiera intentar apuntar (eso no es una var que el agente controle
# desde el comando bajo análisis).
_GIT_REPO_FOR_BRANCH = os.environ.get("BTP_GIT_REPO_OVERRIDE", REPO)

# Allowlist PURA del perfil cuarentena (lectura; SIN awk/sed/jq/python/red/git).
ALLOW_QUARANTINE = {
    "ls", "cat", "head", "tail", "wc", "grep", "egrep", "fgrep", "rg", "find",
    "file", "stat", "echo", "printf", "sort", "uniq", "cut", "tr", "basename",
    "dirname", "pwd", "date", "true", "false", "test", "realpath", "comm", "nl",
}

SECRET_HINTS = ("_secrets", ".credentials", "credentials", "secrets.json",
                "id_rsa", "id_ed25519", "id_ecdsa", ".ssh", "/.aws", ".env",
                "application_default_credentials", ".netrc", ".pgpass",
                "kubeconfig", ".kube/", ".pem", ".p12", ".pfx", "gcloud")

# Datos CLÍNICOS de {{TITULAR}} (su historia médica, en iCloud fuera del repo). Defensa
# en profundidad sobre la LECTURA: el muro ya cierra el EGRESS con rigor, pero hasta
# ahora Read/Grep/Glob/LS y `cat` pasaban SIN mirar la ruta (SAFE_TOOLS los daba por
# seguros por ser locales, sin distinguir un .py del repo de un informe de biopsia).
# El lazo 24/7 NO tiene por qué leer el clínico; solo el COMITÉ CLÍNICO
# (comite-medico/oncologo-virtual/verificacion/herramientas-medicas) lo necesita, y
# esos agentes se lanzan con MURO_ALLOW_CLINICAL=1 (run_agent.sh). Cualquier otro
# agente/skill del lazo que intente leer el clínico → DENY. Marcadores anclados a las
# carpetas de salud (no pegan con lecturas normales del repo/tools).
# 14-jul-26 — AGUJERO CAZADO: estos marcadores solo cubrian la ruta iCloud del PORTATIL
# ("00_Salud/claudecode"). En Polaris el repo vive en /Users/polaris/claudecode y los datos
# reales estan en _PRIVADO_CLINICO / _PRIVADO_NUCLEO / _PRIVADO_CORREO / _PRIVADO_EXPEDIENTE
# y ~/Clinico-PRIVADO -> NINGUNO casaba, asi que check_read() no denegaba NADA, ni siquiera
# en el lazo 24/7 donde si esta enganchado. Verificado por ejecucion contra el hook.
# 25-jul-26 — La política se mudó a .claude/hooks/zonas_clinicas.py (fuente ÚNICA, compartida
# con clinico_guard y lector_clinico). Motivo: había CUATRO listas divergentes y las carpetas
# donde de verdad vive el N2 (`informes/`, `docu enviada a nova/`) no estaban en ninguna.
# El import es defensivo porque muro_guard.sh convierte cualquier rc≠0 en deny: un ImportError
# aquí denegaría TODAS las llamadas del lazo 24/7. Si falla → se usa la lista de abajo, que es
# lo que había hasta hoy; `test_zonas_clinicas` asserta que sigue cubierta (nunca menos).
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import zonas_clinicas as _ZC
except Exception:                       # noqa: BLE001
    _ZC = None

_CLINICAL_HINTS_FALLBACK = ("_privado_clinico", "_privado_nucleo", "_privado_correo",
                  "_privado_expediente",
                  # 15-jul-26: el "_privado" SUELTO tapaba TODO _PRIVADO_* por substring,
                  # incluido _PRIVADO_DMS (la cajita NO clinica de dm-inbox) -> no podia leer su
                  # propio archivo de dedupe. Se enumera explicito: las carpetas de PII social
                  # (instagram/whatsapp/x/yt) SIGUEN protegidas de lectura Y borrado por el lazo;
                  # solo _PRIVADO_DMS queda fuera. Si nace un _PRIVADO_* nuevo, se anade aqui.
                  "_privado_instagram", "_privado_whatsapp", "_privado_x", "_privado_yt",
                  "clinico-privado",
                  "00_salud", "00 - salud", "historial clinico", "historial clínico",
                  "historial-clinico")
CLINICAL_ALLOWED = os.environ.get("MURO_ALLOW_CLINICAL") == "1"

SELF_PROTECT = ("muro_guard", ".claude/settings", ".claude/hooks", "settings.autonomous",
                "settings.quarantine", ".halt")

# B1 — el lazo autonomo NO escribe codigo/ejecutables ni config critica (Write/Edit).
WRITE_EXEC_EXT = (".py", ".sh", ".bash", ".zsh", ".command", ".pl", ".rb", ".js",
                  ".mjs", ".cjs", ".ts", ".php", ".rs", ".go", ".c", ".cc", ".cpp",
                  ".m", ".swift", ".applescript", ".scpt", ".plist", ".bat", ".ps1",
                  ".so", ".dylib", ".dll", ".pyc",
                  ".pth")  # .pth: una línea `import …` se ejecuta al arrancar ese python.
# OJO: incluir ".git/" SIN barra inicial además de "/.git/" — un destino RELATIVO
# (cp x .git/hooks/pre-commit) no contiene "/.git/" y, si no, colaría: plantar un git
# hook ejecutable + `git commit` (permitido) = código arbitrario fuera del muro.
# .venv/site-packages: el lazo arranca con el python del venv (plists/MCP) → un .pth o
# un fichero de paquete plantado ahí se ejecuta; el árbol del venv es zona prohibida.
WRITE_CRIT = (".mcp.json", "/.git/", ".git/", ".github/", ".gitignore", ".gitleaksignore",
              "launchagents/", "launchdaemons/", "/.config/", "/.local/bin/",
              ".venv", "site-packages/",
              # H1/H4 (Fase 0): el lazo NO reescribe los agentes del gabinete, ni los
              # datos del FRENO DE GASTO (tools/state/cost/ = limits.json + ledger del
              # día), ni los plist de launchd. Va en WRITE_CRIT (bloquea ESCRITURA por
              # Write/Edit y por cp/mv/tee/… ; NO lectura — el lazo SÍ los lee).
              # Alcance afinado a tools/state/COST/ (no a todo tools/state/): la cola
              # (tools/state/queue, transiciones por `mv`) y continuity SÍ los escribe el
              # lazo. cost_guard escribe su ledger por open() de python (no por la tool)
              # → no se ve afectado; lo que se cierra es subir el tope / resetear gasto.
              ".claude/agents/", "tools/state/cost/", "tools/launchd/",
              # El permiso de envío y su libro de usados, por NOMBRE (22-sep-26): segunda red
              # por si la ruta real no se puede resolver (`$VAR/ok_envio.json`).
              "ok_envio.json", "ok_envio_usados.jsonl",
              # Dotfiles de SHELL: un proceso permitido (cualquier shell nuevo, y el propio
              # muro_guard.sh corre bajo bash) los auto-ejecuta al arrancar -> misma clase B3
              # (plantar+autoejecutar saltandose el choke-point). .zshenv se lee en TODO zsh.
              ".bashrc", ".bash_profile", ".bash_login", ".profile",
              ".zshrc", ".zshenv", ".zprofile", ".zlogin", ".zlogout",
              ".kshrc", ".cshrc", ".tcshrc", ".inputrc",
              # Hooks/config de git de usuario y plantilla por defecto que git ejecuta.
              ".gitconfig", "/git/config", "/git/ignore")

# B3 — los binarios de BASH que CREAN/SOBREESCRIBEN ficheros deben respetar la MISMA
# política que Write/Edit (B1): el lazo no fabrica codigo/ejecutables ni toca config
# critica por esta via. Cierra el bypass `cp/mv/ln/tee/split/touch -> tools/emisor.py`
# que, si no, dejaria plantar un .py emisor y abrir un socket saltandose el choke-point.
# OJO (caza de la CLASE, no del caso): aqui va TODO binario de ALLOW_PRIV capaz de
# ESCRIBIR a una ruta dada por argumento, no solo cp/mv. `sort -o destino` y `uniq
# entrada destino` (2º operando = salida) tambien plantan/sobrescriben un .py (una linea
# de python sobrevive a sort/uniq) -> van en la lista. dd/install/rsync NO estan en
# ALLOW_PRIV hoy (se deniegan antes) pero quedan como defensa si algun dia se permiten.
# REGLA VIVA: al meter un binario en ALLOW_PRIV, si escribe ficheros, va tambien aqui.
FILE_WRITE_BINS = {"cp", "mv", "ln", "tee", "split", "touch", "sort", "uniq",
                   "dd", "install", "rsync"}

# H2 (Fase 0): cuando el matcher del hook cubra TODAS las tools (no solo Bash/Write),
# estas son las ÚNICAS permitidas además de Bash/Write — allowlist FAIL-CLOSED. El
# resto (MCP de ENVÍO: gmail/notion/chrome/computer-use/scheduled; Task; WebFetch/
# WebSearch = texto externo NO confiable en perfil privilegiado) se DENIEGA: la única
# boca al exterior es tools/salida.py y la web no confiable entra por cuarentena.
SAFE_TOOLS = {"Read", "Grep", "Glob", "LS", "NotebookRead", "TodoWrite"}
# MCP: NINGUNO permitido en el perfil privilegiado por defecto. Incluso un read-MCP de
# investigación (p.ej. `mcp__biomcp__fetch`) trae TEXTO EXTERNO crudo al lazo privilegiado
# — justo lo que la contención arquitectónica prohíbe (eso va por cuarentena). Si el lazo
# necesitara una consulta estructurada concreta, se allowlista AQUÍ por nombre EXACTO de
# tool (no por prefijo, que auto-confiaría un `fetch`/`submit` futuro). Vacío = fail-closed.
SAFE_MCP_TOOLS = frozenset()

# EXCEPCION DE SUPERVISION (item Opus-hibrido, 17-jul-26): en privileged, Task esta denegado por
# defecto (anti-inyeccion). Estos DOS subagent_type — y solo estos, por nombre EXACTO — se permiten
# para que el lazo pueda consultar su MESA FIJA fail-closed (¿es cierto? / ¿esta bien construido?)
# en el modo desatendido, donde mas se necesita. NO abre poderes nuevos: el sub-agente invocado
# sigue corriendo bajo el MISMO muro_guard (privileged) — sus tool calls (Bash/WebFetch/MCP) pasan
# por aqui igual. Solo abre una SEGUNDA VOZ dentro del mismo sandbox, no una via de salida. Ambos
# tienen tools: sin Task/Agent (verificacion: Read/Grep/Glob/Bash; consejero-arquitectura: idem) ->
# sin ping-pong. Crecer esta lista pasa por el MISMO review que el muro.
TASK_ALLOW_PRIV = frozenset({"verificacion", "consejero-arquitectura"})

SYS_BIN_DIRS = ("/usr/bin/", "/bin/", "/usr/sbin/", "/sbin/",
                "/opt/homebrew/bin/", "/usr/local/bin/")

_SHELL_PUNCT = set(";|&()<>{}\n")


# A1 (hallazgo A): true SOLO durante el check del comando actual si su tool_input traía
# dangerouslyDisableSandbox=true. Módulo de un solo uso por invocación del hook (un proceso
# por llamada), así que el global es seguro (sin concurrencia dentro del mismo proceso).
_SANDBOX_ESCALADO = False


def deny(reason):
    sys.stderr.write("MURO ⛔ [%s] %s\n" % (PROFILE, reason))
    sys.exit(2)


def allow(reason=""):
    """Aprobación ESTRUCTURADA (contrato PreToolUse de Claude Code, hooks-guide 2026).

    Sin este JSON, un `exit 0` NO auto-aprueba la tool: cae al flujo normal de permisos, que en
    modo headless (`claude -p --permission-mode acceptEdits`) DENIEGA todo Bash no-allowlistado —
    y el agente gasta turnos reintentando hasta `max_turns` (era el fallo de hoy-compose/asistente).
    Emitiendo `permissionDecision: "allow"` el muro se vuelve la ÚNICA puerta efectiva: lo que ÉL
    considera seguro (su allowlist fail-closed) se ejecuta sin prompt; lo peligroso sigue en `deny()`
    (exit 2). OJO (doc oficial): un `permissions.deny` en settings GANA sobre este allow — hoy no hay
    ninguno, así que el muro manda. La salida (stdout) queda limpia: solo este JSON."""
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "permissionDecisionReason": reason or ("muro %s: permitido" % PROFILE),
    }}))
    sys.exit(0)


def _git_quita_C(args):
    """Quita los `-C <dir>` GLOBALES (los que van antes del subcomando; `git commit -C HEAD` es
    otra opción y no se toca) y devuelve el resto. Solo vale un <dir> dentro del repo (casa base o
    sus worktrees): `git -C /otro/repo commit` ejecutaría los hooks de ese repo.
    Antes (hasta 11-sep-26) el <dir> se tomaba por subcomando y `git -C` se denegaba siempre
    (deuda muro_git_C_inservible). La rama del gate de la base se sigue mirando en REPO: con -C
    a un worktree en rama, un commit se puede denegar de más, nunca de menos."""
    args = list(args)
    while args and args[0] == "-C":
        if len(args) < 2:
            deny("git -C sin directorio (fail-closed).")
        destino = args[1]
        if any(ch in _EXPANSION for ch in destino):
            deny("git -C con expansion de shell (no verificable): %s" % destino)
        destino = os.path.expanduser(destino)
        bases = [""] if os.path.isabs(destino) else _CWDS
        raiz = _norm(REPO)
        for b in bases:
            n = _norm(os.path.join(b, destino))
            if not (n == raiz or n.startswith(raiz + os.sep)):
                deny("git -C fuera del repo: %s" % args[1])
        args = args[2:]
    return args


def _es_git_mutante_de_riesgo(sub):
    """True si `sub` (tokens de UN comando, sub[0]=='git') es una de las mutaciones que el A1
    marcó como jamás-bajo-escalada (branch -d/-D, worktree prune/remove, push, reset, clean, rm)."""
    args = _git_quita_C(sub[1:])
    gsub = next((a for a in args if not a.startswith("-")), None)
    if gsub not in GIT_MUTATE_DENY_ON_ESCALATION:
        return False
    marcador = GIT_MUTATE_DENY_ON_ESCALATION[gsub]
    return marcador is None or any(a in marcador for a in args)


def _rama_actual():
    """Fix (c) — `git rev-parse --abbrev-ref HEAD` sobre _GIT_REPO_FOR_BRANCH. Defensivo:
    devuelve "" ante CUALQUIER fallo/timeout (git no instalado, no es un repo, detached
    HEAD raro, proceso colgado…) — fail-safe: "" nunca coincide con GIT_BASE_BRANCHES, así
    que un fallo aquí NUNCA bloquea de más (solo podría dejar de aplicar el gate en un caso
    borde, y push sigue exigiendo el gate SIEMPRE, sin mirar rama)."""
    try:
        r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                           cwd=_GIT_REPO_FOR_BRANCH, capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return ""
        return r.stdout.strip()
    except Exception:
        return ""


def _quita_comentarios(cmd):
    """Quita los comentarios de shell como bash: un `#` FUERA de comillas, sin escapar y a
    principio de palabra (inicio, espacio o tras ;&|()<> o salto) abre comentario hasta el salto
    de línea, que se CONSERVA (es el separador). Dentro de '…' no hay escapes; dentro de "…" y
    fuera, la barra escapa el carácter siguiente. Si se equivoca, es hacia más estricto: dejar
    un texto que bash ignora solo añade argumentos que el muro juzga."""
    out, i, n = [], 0, len(cmd)
    simple = doble = False
    while i < n:
        c = cmd[i]
        if simple:
            simple = c != "'"
        elif doble:
            if c == "\\" and i + 1 < n:
                out.append(c)
                i += 1
                c = cmd[i]
            elif c == '"':
                doble = False
        elif c == "\\" and i + 1 < n:
            out.append(c)
            i += 1
            c = cmd[i]
        elif c == "$" and i + 1 < n and cmd[i + 1] == "'":
            # $'…' FUERA de comillas (comillas ANSI de bash): admite \' dentro y shlex no lo sabe,
            # así que un separador quedaría "dentro" para el muro y fuera para bash. Dentro de
            # comillas (`grep 'foo$'`, `"…$"`) es texto y no se toca.
            deny("comillas $'...' prohibidas (el muro no puede seguirlas).")
        elif c == "'":
            simple = True
        elif c == '"':
            doble = True
        elif c == "#" and (i == 0 or cmd[i - 1] in " \t\r\n;&|()<>"):
            while i < n and cmd[i] != "\n":
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def is_separator(tok):
    return tok != "" and set(tok) <= _SHELL_PUNCT


# Redirecciones (>, >>, <, <<, >&, &>, <>): el shlex con punctuation_chars parte estos signos
# como tokens sueltos, igual que un separador de comando. Pero NO lo son: el token que SIGUE a una
# redirección es su DESTINO (un fichero o un fd), no un comando nuevo. Tratarlo como comando era el
# bug que denegaba `2>/dev/null` ("ejecucion por ruta: /dev/null") y `2>&1` ("binario no permitido:
# 1"), forzando a los agentes a reintentar sin redirección y QUEMAR TURNOS (reventones de max_turns
# del barrido/correo-urgente, 26/6). Aquí los reconocemos para CONSUMIR el destino SIN abrir ninguna
# vía de escritura nueva: solo se permite descartar a /dev/null y duplicar fds; cualquier otro
# destino (un fichero real) es una ESCRITURA → sigue DENY (va por la tool Write). Fail-closed.
_REDIR_CHARS = set("<>&")
SAFE_REDIR_TARGETS = {"/dev/null", "/dev/stdout", "/dev/stderr"}


def is_redirect(tok):
    """True si el token es un operador de redirección (contiene < o >). `&` o `&&` solos NO lo son
    (siguen siendo separadores: background / encadenado)."""
    return tok != "" and set(tok) <= _REDIR_CHARS and (("<" in tok) or (">" in tok))


def host_of(token):
    t = token
    if "://" in t:
        t = t.split("://", 1)[1]
    t = t.split("/", 1)[0]
    t = t.split("@")[-1].split(":")[0]
    return t.lower()


def _hits_crit(path):
    """True si `path` cae en WRITE_CRIT, NORMALIZANDO la barra final en ambos lados.
    Cierra el bypass `rm -rf tools/state/cost` (sin barra) vs entrada 'tools/state/cost/'."""
    p = str(path).lower().rstrip("/")
    return any(h.rstrip("/") in p for h in WRITE_CRIT)


# ─── ZONAS POR RUTA REAL (11-sep-26, deudas muro_hits_crit_substring y
#     muro_healthcheck_state_escribible) ──────────────────────────────────────────────────────
# _hits_crit compara TEXTO: `tools/state/./cost/limits.json`, `cd tools/state/cost && tee
# limits.json`, `cp -r /tmp/x tools/state` o `tools/st${x}ate/…` pasaban (reproducido con este
# guard el 11-sep). Aquí las rutas se comparan como RUTAS: expanduser → unidas a CADA directorio
# posible (el cwd del harness, REPO y cada `cd` del mismo comando, todos a la vez, porque un `cd`
# dentro de un `|` no cambia el directorio real) → realpath → minúsculas NFC (APFS no distingue
# mayúsculas ni normalización) → prefijo de carpeta. _hits_crit sigue como segunda red.
# Zonas: el FRENO DE GASTO, la SEÑAL DE PRESENCIA de {{TITULAR}} (dead-man) y los TRANSCRIPTS de Claude
# Code (healthcheck los lee para creer un prompt humano). Excepción: <proyecto>/memory/, donde
# auto-mejora escribe memorias. Todo lo de aquí se ESCRIBE por open() de python (healthcheck,
# cost_guard, el harness), que el muro no ve: cerrar la tool y la shell no rompe nada legítimo.
_HOME = os.path.expanduser("~")
PROYECTOS_CC = os.path.join(_HOME, ".claude", "projects")
# El PERMISO DE ENVÍO (22-sep-26, hallazgo 3.1): `ok_envio.json` abría el freno de salida con
# solo existir, y el lazo lo podía escribir con Write, `tee`, `cp` o `mv`. Lo emite únicamente
# `.claude/hooks/ok_envio_prompt.py` (por open() de python, que el muro no ve); su libro de usados
# lo escribe `salida_guard.py`. Se añaden también bajo `BTP_STATE_DIR` si el estado vive en otro
# sitio. La firma HMAC (tools/permiso_envio.py) es la red de debajo si alguien encuentra otra vía.
_STATE_REAL = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
_PERMISO_ENVIO = tuple(os.path.join(d, n)
                       for d in sorted({os.path.join(REPO, "tools", "state"), _STATE_REAL})
                       for n in ("ok_envio.json", "ok_envio_usados.jsonl"))
ZONAS_ESCRITURA = (os.path.join(REPO, "tools", "state", "cost"),
                   os.path.join(REPO, "tools", "state", "healthcheck"),
                   PROYECTOS_CC) + _PERMISO_ENVIO
_EXPANSION = set("$*?[{")   # lo que la shell expande DESPUÉS de que el muro haya mirado
_CWDS = [REPO]              # directorios posibles del comando actual (un proceso por llamada)


def _norm(p):
    r = os.path.realpath(p)
    try:   # regla de los hooks: todo import nuevo con fallback (aquí, sin NFC: más estricto no, igual)
        import unicodedata
        r = unicodedata.normalize("NFC", r)
    except Exception:
        pass
    return r.lower().rstrip(os.sep) or os.sep


def _es_memoria(n):
    """True si la ruta normalizada `n` vive en <proyecto>/memory/ de los proyectos de Claude Code."""
    raiz = _norm(PROYECTOS_CC)
    partes = n[len(raiz) + 1:].split(os.sep) if n.startswith(raiz + os.sep) else []
    return len(partes) >= 2 and partes[1] == "memory"


def _en_zona(ruta):
    """True si `ruta` (absoluta) está DENTRO de una zona o es un ANCESTRO de una (cp -r/mv/ln/chmod
    sobre la carpeta madre sobrescribe la zona entera)."""
    n = _norm(ruta)
    for z in ZONAS_ESCRITURA:
        zn = _norm(z)
        if n == zn or n.startswith(zn + os.sep):
            if zn == _norm(PROYECTOS_CC) and _es_memoria(n):
                continue
            return True
        if zn.startswith(n.rstrip(os.sep) + os.sep):
            return True
    return False


def _candidatos(arg):
    """Rutas que puede nombrar un argumento: el propio token, o la parte pegada a un flag
    (`--output=x`, `-ox`)."""
    if arg.startswith("--") and "=" in arg:
        return [arg.split("=", 1)[1]]
    if arg.startswith("-") and not arg.startswith("--"):
        return [arg[2:]] if len(arg) > 2 else []
    return [arg] if arg else []


def _toca_zona(arg, cwds=None):
    """True si el argumento puede escribir en una zona. Con expansión de shell ($, *, {, ?, [) se
    juzga la carpeta del prefijo LITERAL: `tools/st${x}ate/y` → `tools`, ancestro de las zonas.
    Un `$VAR` SUELTO (prefijo vacío) se juzga desde el cwd, que es ancestro de todo → DENY a
    conciencia (visto por verificacion el 11-sep como falso positivo de `tee $LOG`): el entorno
    ya trae variables que apuntan al repo ($CLAUDE_PROJECT_DIR, $PWD, $HOME), así que
    `tee $CLAUDE_PROJECT_DIR/tools/state/cost/limits.json` sería un bypass si se relajara."""
    for c in _candidatos(arg):
        corte = min((i for i, ch in enumerate(c) if ch in _EXPANSION), default=None)
        if corte is not None:
            c = os.path.dirname(c[:corte]) or "."
        c = os.path.expanduser(c)
        bases = [""] if os.path.isabs(c) else (cwds or _CWDS)
        if any(_en_zona(os.path.join(b, c)) for b in bases):
            return True
    return False


def _nuevo_cd(args):
    """Apunta el destino de un `cd` como directorio posible del resto del comando."""
    destino = next((a for a in args if not a.startswith("-")), _HOME)
    if any(ch in _EXPANSION for ch in destino):
        deny("cd con expansion de shell (destino no verificable): %s" % destino)
    destino = os.path.expanduser(destino)
    nuevos = [destino] if os.path.isabs(destino) else [os.path.join(b, destino) for b in _CWDS]
    for d in nuevos:
        if d not in _CWDS:
            _CWDS.append(d)


_MSG_ZONA = ("el lazo no toca el freno de gasto, la señal de presencia de {{TITULAR}}, los "
             "transcripts de Claude Code ni el permiso de envío: %s")


def check_subcommand(sub):
    if not sub:
        return
    first = sub[0]
    if "=" in first and first.split("=", 1)[0].isidentifier():
        deny("asignacion de entorno inline antes del comando (evasion).")

    joined_l = (" ".join(sub)).lower()
    if any(h in joined_l for h in SECRET_HINTS):
        deny("referencia a un fichero de secretos/credenciales.")
    if any(h in joined_l for h in SELF_PROTECT):
        deny("intento de tocar el propio muro (settings/hooks/guard/.HALT).")
    if not CLINICAL_ALLOWED and _ZC is None and any(h in joined_l for h in _CLINICAL_HINTS_FALLBACK):
        # Solo el camino de FALLBACK juzga la cadena entera. Con la política cargada, el
        # check clínico va más abajo, sobre los ARGUMENTOS resueltos (necesita saber el
        # binario para descartar el patrón de grep).
        deny("referencia a datos clinicos fuera del comite clinico.")

    raw0 = sub[0]
    if raw0.startswith("/"):
        if not raw0.startswith(SYS_BIN_DIRS):
            deny("ejecucion por ruta absoluta fuera de dirs de sistema: %s" % raw0)
    elif "/" in raw0:
        deny("ejecucion de fichero por ruta relativa (script creado por el agente?): %s" % raw0)

    binname = os.path.basename(raw0)
    args = sub[1:]
    if binname == "cd":
        _nuevo_cd(args)   # las rutas relativas que vengan detrás se juzgan también desde aquí

    # Clínico por ARGUMENTOS, no por la cadena. Va aquí y no arriba porque necesita el binario:
    # en grep/rg/sed el primer operando es el PATRÓN, no una ruta, y denegarlo hacía imposible
    # trabajar sobre el propio muro (`grep -rn "_PRIVADO_CLINICO" tools/` se denegaba solo).
    # Se estrecha un eje a conciencia: MENCIONAR una ruta clínica deja de denegarse. No era una
    # fuga (mencionar no lee, y el egress lo cierran salida.py/borde.py) y era la causa real de
    # los falsos positivos que paralizaban el trabajo.
    if not CLINICAL_ALLOWED and _ZC is not None:
        _malas = _ZC.rutas_clinicas_en_tokens(sub)
        if _malas:
            deny("lectura de datos clinicos fuera del comite clinico: %s" % _malas[0])

    # A1 (hallazgo A) — PRIMERO que cualquier otra cosa y en CUALQUIER perfil: un retry con el
    # sandbox desactivado nunca sirve para colar una mutación de git de la lista de riesgo, ni
    # siquiera una que la allowlist normal permitiría sin el flag (p.ej. `branch -d` sin escalada
    # SÍ está permitido; `branch -d` CON `dangerouslyDisableSandbox` no lo está NUNCA).
    if _SANDBOX_ESCALADO and binname == "git" and _es_git_mutante_de_riesgo(sub):
        deny("escalada de sandbox (dangerouslyDisableSandbox) para una mutación de git "
             "(branch -d/-D, worktree prune/remove, push, reset, clean, rm) prohibida: "
             "un reintento denegado se APLAZA, nunca se sirve quitando el candado (A1, 10-jul-26).")

    if PROFILE == "quarantine":
        if binname not in ALLOW_QUARANTINE:
            deny("cuarentena: binario fuera de la allowlist de lectura: %s" % binname)
        # V2 (item #4, 16-jul-26) — INVARIANTE: la cuarentena NUNCA escribe; emite solo por
        # RETORNO. sort/uniq (los unicos FILE_WRITE_BINS dentro de ALLOW_QUARANTINE) escriben
        # por ARGUMENTO (`sort -o x`, `uniq in out`), sin pasar por la redireccion de shell que
        # is_redirect ya bloquea -> podian forjar un job en tools/state/queue/pending y colar
        # una orden al privilegiado. El chequeo general de FILE_WRITE_BINS vive DESPUES del
        # `return` de este perfil (nunca lo alcanzaba), y ademas no cubre escrituras a la cola.
        # Fail-closed: cualquier bin con capacidad de escritura queda prohibido en cuarentena.
        if binname in FILE_WRITE_BINS:
            deny("cuarentena: bin con capacidad de escritura (%s) prohibido; la cuarentena solo lee y emite por retorno." % binname)
        if binname == "find":
            for a in args:
                if a in FIND_DENY:
                    deny("find %s prohibido." % a)
        return

    # ---- perfil privilegiado: ALLOWLIST ----
    if binname not in ALLOW_PRIV:
        deny("binario no permitido en modo autonomo: %s" % binname)

    if binname in PY_BINS:
        for a in args:
            if a == "-" or a.startswith("-c") or a.startswith("-m"):
                deny("python -c/-m/- (codigo arbitrario) prohibido; usa un script .py.")
        script = next((a for a in args if (not a.startswith("-")) and a.endswith(".py")), None)
        if not script and not any(a in ("-V", "--version", "-h", "--help") for a in args):
            deny("python sin script .py (modo interactivo/stdin = codigo arbitrario).")
        if script:
            sp = script if script.startswith("/") else os.path.join(REPO, script)
            ap = os.path.abspath(sp)
            if ap != REPO and not ap.startswith(REPO + os.sep):
                deny("python solo ejecuta scripts dentro del repo: %s" % script)

    if binname == "git":
        for a in args:
            al = a.lower()
            if a in ("-c", "-p", "--paginate", "--exec-path", "--config-env") \
               or a.startswith("-c") or a.startswith("--config"):
                deny("git -c/-p/--config (pager/alias = ejecucion arbitraria) prohibido.")
            # Segunda red (defensa en profundidad) por si un valor con forma rara burla
            # la deteccion de escritura de abajo: tokens de claves que ejecutan comandos.
            if any(k in al for k in ("pager", "alias.", "hookspath", "sshcommand",
                                     "textconv", ".external", "fsmonitor", "filter.",
                                     ".clean", ".smudge", "editor", "driver", "helper")):
                deny("git config peligrosa en argumentos.")
        args = _git_quita_C(args)   # el resto del bloque juzga el comando sin el `-C <dir>`
        gsub = next((a for a in args if not a.startswith("-")), None)

        # Fix (c) (11-jul-26) — gate BLANDO de escritura a la base. INERTE si el toggle
        # `.claude/hooks/.base_gate_on` no existe (_BASE_GATE_ON False). Con el toggle
        # puesto: `push` exige el gate SIEMPRE (toca remoto, sin mirar rama); `merge`/
        # `commit` solo si la rama ACTUAL es master/main (en una rama de trabajo, libre).
        if _BASE_GATE_ON and gsub in GIT_BASE_GATE_SUBS \
                and os.environ.get("BTP_GIT_BASE_OK") != "1":
            toca_base = gsub == "push" or (_rama_actual() in GIT_BASE_BRANCHES)
            if toca_base:
                deny("escritura a la base sin gate: trabaja en rama, o exporta "
                     "BTP_GIT_BASE_OK=1 con OK humano (fix c, 11-jul-26).")

        if gsub not in GIT_ALLOW_SUB:
            deny("git %r fuera de la allowlist en modo autonomo (status/diff/log/add/commit/branch/checkout -b/show/…)." % gsub)
        if gsub == "checkout":
            # Solo cambio de rama (checkout -b <rama> / checkout <rama>). NO restaurar
            # ficheros: `--`, pathspec (./ * / HEAD / HEAD~/^), ruta protegida, o >1
            # positional (forma `checkout <ref> <pathspec>`) → DENY. Una rama (incl.
            # `feat/x` con barra) es UN solo token sin ./*//HEAD.
            branchish = [a for a in args if not a.startswith("-")][1:]
            if ("--" in args or len(branchish) > 1
                    or any(_hits_crit(a) for a in branchish)
                    or any(p in (".", "*", "HEAD") or p.startswith(("HEAD~", "HEAD^"))
                           for p in branchish)):
                deny("git checkout que restaura ficheros prohibido; usa -b/<rama> para ramas.")
        if gsub == "config":
            if any(a in ("-e", "--edit") for a in args):
                deny("git config -e (abre el editor = ejecucion) prohibido.")
            # `-f/--file/--blob` apuntan git config a un fichero ARBITRARIO: (a) eclipsan la
            # deteccion de clave (su argumento queda como primer token no-flag) y (b) pueden
            # escribir el .git/config que git SI lee en commit -> ejecucion (gpg.program…).
            # El lazo no las necesita (config local/global por defecto basta) -> DENY.
            if any(a in ("-f", "--file", "--blob") or a.startswith(("--file=", "--blob="))
                   for a in args):
                deny("git config -f/--file/--blob (fichero de config arbitrario) prohibido.")
            # Lista BLANCA de ESCRITURAS por FORMA de la clave (no por posicion). Una clave
            # de config se reconoce por su forma `seccion.algo` (primer segmento = identificador
            # con un punto). Cualquier clave-candidata que NO sea de lectura pura y NO este en
            # la allowlist -> DENY, este donde este entre los argumentos.
            #   OJO bypass cerrado (verificacion, cierre B3): `git config gpg.program -payload`
            #   escribia la clave porque git acepta como VALOR un token que empieza por '-', y
            #   el conteo de positionales lo descartaba (len<2) -> ALLOW falso. Detectar la clave
            #   por su FORMA, no contando positionales, cierra el truco del guion (gpg.program,
            #   core.gitProxy, *.command, core.askPass, core.alternateRefsCommand, init.templateDir…).
            # La CLAVE es el PRIMER token no-flag tras `config`; el resto es VALOR (no se
            # valida como clave, p.ej. un email con puntos). El truco del guion metia el
            # valor como token que empieza por '-' para que `len(positionales)<2` y saltarse
            # la allowlist: ya no contamos positionales -> en cuanto hay una clave de config
            # (token con forma `seccion.algo`) que no es lectura pura ni inocua -> DENY.
            is_read = any(a in GIT_CONFIG_READ_FLAGS for a in args)
            key = next((a for a in args if a != "config" and not a.startswith("-")), None)
            if not is_read and key is not None:
                head = key.split(".", 1)[0]
                looks_like_key = ("." in key) and head.replace("-", "_").isidentifier()
                if looks_like_key and key.lower() not in GIT_CONFIG_WRITE_ALLOW:
                    deny("git config: escritura de clave no permitida (posible ejecucion): %s" % key)

    if binname in ("curl", "wget"):
        if any(a in CURL_DANGER or a.startswith("--config") or a.startswith("@")
               or a.startswith("-d@") for a in args):
            deny("curl/wget con config/upload/@fichero externo prohibido.")
        seen_url = False
        for a in args:
            if a.startswith(("http://", "https://")):
                seen_url = True
                if host_of(a) not in NET_ALLOW:
                    deny("red a dominio no permitido: %s" % host_of(a))
            elif a.startswith(("ftp://", "file://", "dict://", "gopher://", "scp://")):
                deny("esquema de red no permitido: %s" % a)
            elif (not a.startswith("-")) and ("." in a) and ("/" in a) and not a.startswith("/"):
                seen_url = True
                if host_of(a) not in NET_ALLOW:
                    deny("red a dominio no permitido: %s" % host_of(a))
        if not seen_url:
            deny("curl/wget sin URL reconocible (fail-closed).")
        # B3: el FICHERO que guarda la descarga no puede ser codigo/ejecutable ni config
        # critica. curl -O / wget SIN -O guardan con el nombre remoto (basename de la URL)
        # -> desde un host permitido (raw.githubusercontent) plantarian un .py emisor.
        urls = [a for a in args if a.startswith(("http://", "https://"))]
        outs = []
        for i, a in enumerate(args):
            nxt = args[i + 1] if i + 1 < len(args) else None
            # OJO: `-O` = remote-name (sin arg) en curl, pero = fichero de salida en wget.
            if binname == "curl" and a in ("-o", "--output") and nxt is not None:
                outs.append(nxt)
            elif binname == "wget" and a in ("-O", "--output-document") and nxt is not None:
                outs.append(nxt)
            elif a.startswith(("--output=", "--output-document=")):
                outs.append(a.split("=", 1)[1])
        curl_remote = binname == "curl" and any(
            a in ("-O", "--remote-name", "--remote-name-all") for a in args)
        wget_default = binname == "wget" and not any(
            a in ("-O", "--output-document") or a.startswith("--output-document=") for a in args)
        if curl_remote or wget_default:
            outs += [u.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] for u in urls]
        for o in outs:
            ol = o.lower()
            if ol.endswith(WRITE_EXEC_EXT) or _hits_crit(o):
                deny("%s no puede guardar codigo/ejecutables ni config critica: %s" % (binname, o))
            if _toca_zona(o):
                deny(_MSG_ZONA % o)

    if binname == "restic":
        for a in args:
            al = a.lower()
            # forma --flag=valor y --flag valor: basta con detectar el flag.
            if any(al == f or al.startswith(f + "=") for f in RESTIC_EXEC_FLAGS):
                deny("restic %s (ejecuta un comando = bypass del choke-point) prohibido." % a)

    if binname == "find":
        for a in args:
            if a in FIND_DENY:
                deny("find %s (ejecucion/escritura) prohibido." % a)

    if binname == "rm":
        # H4: borrar el ledger/limits de coste resetea el gasto a 0 (fail-open). rm de
        # cualquier ruta WRITE_CRIT (coste/agentes/git/launchd/…) prohibido. (SELF_PROTECT
        # y SECRET_HINTS ya se filtran arriba sobre el comando entero.) La cola
        # (tools/state/queue, fuera de WRITE_CRIT) sí se puede limpiar.
        for a in args:
            if not a.startswith("-") and _hits_crit(a):
                deny("rm sobre config/estado protegido (coste/agentes/git/launchd): %s" % a)
            # _repo_protegido (abajo) resuelve desde el cwd del HOOK, no del comando:
            # `cd tools/state/queue && rm ../healthcheck/degraded.flag` se le escapaba.
            if not a.startswith("-") and _toca_zona(a):
                deny("rm: " + _MSG_ZONA % a)
        recursive = any(p.startswith("-") and ("r" in p.lower()) for p in args)
        for a in args:
            if a.startswith("-"):
                continue
            p = os.path.abspath(os.path.expanduser(a))
            # FASE 0b: rm (recursivo o no) que DESTRUYE codigo/estado protegido o toca
            # el disco clinico -> DENY. La cola/estado operativo del lazo si se puede.
            if _es_clinico_path(a):
                deny("rm sobre el disco clinico: %s" % p)
            if _repo_protegido(a):
                deny("rm sobre zona protegida del repo (codigo/tools/skills/...): %s" % p)
            if recursive and (p in ("/", os.path.expanduser("~"), REPO) or p.count("/") <= 1):
                deny("rm -r sobre ruta peligrosa: %s" % p)

    if binname in FILE_WRITE_BINS:
        # Misma vara que check_write (B1): ningun argumento puede ser/crear codigo
        # ejecutable ni config critica. (SELF_PROTECT/SECRET_HINTS ya se filtran arriba
        # sobre el comando entero.) Asi cp/mv/ln/tee/sort/uniq no plantan un .py emisor.
        # NO descartamos los tokens que empiezan por '-': el destino puede venir PEGADO a
        # un flag (`sort -odestino.py`, `--output=x.py`, `split --additional-suffix=.py`)
        # y, si lo saltaramos, colaria. Un flag inocuo (-r/-f/-s/-o/-a/-l) no acaba en una
        # extension de codigo ni contiene un marcador critico -> revisar TODO no sobre-bloquea.
        for a in args:
            al = a.lower()
            if al.endswith(WRITE_EXEC_EXT):
                deny("%s no puede crear/sobrescribir codigo/ejecutables (%s); el codigo va por rama/PR supervisado." % (binname, os.path.basename(a)))
            if _hits_crit(a):
                deny("%s no puede escribir config critica (mcp/git/launchd/estado-coste/agentes): %s" % (binname, a))
            # FASE 0b: NADA que toque el disco clinico (ni leer-para-copiar-fuera).
            if _es_clinico_path(a):
                deny("%s no puede tocar el disco clinico (usa la ventanilla auditada): %s" % (binname, a))
        # (chmod se mira aparte, más abajo: no está en FILE_WRITE_BINS.)
        # Zonas por ruta real. `cp` solo escribe en su DESTINO (el último operando): copiar
        # DESDE una zona es leer. El resto (mv/ln/tee/touch/split/sort/uniq…) puede escribir,
        # mover o enlazar con cualquiera de sus operandos -> se miran todos.
        operandos = [a for a in args if not a.startswith("-")]
        mirar = operandos[-1:] if binname == "cp" else args
        for a in mirar:
            if _toca_zona(a):
                deny("%s: %s" % (binname, _MSG_ZONA % a))
        # mv/ln SACAN el original: si mueve una ruta protegida del repo a FUERA -> DENY.
        # (cp no destruye el origen -> se permite copiar un fichero normal a /tmp.)
        if binname in ("mv", "ln"):
            noflags = [a for a in args if not a.startswith("-")]
            if len(noflags) >= 2:
                origen, destino = noflags[0], noflags[-1]
                if _repo_protegido(origen) and _sale_del_repo(destino):
                    deny("%s no puede SACAR del repo una ruta protegida (%s -> %s)" % (binname, origen, destino))

    if binname == "chmod":
        # `chmod 000 tools/state/healthcheck` deja al dead-man sin poder escribir su aviso, y
        # sobre cost/ puede tumbar el freno de gasto. chmod no estaba mirado por nada.
        for a in args:
            if _toca_zona(a):
                deny("chmod: " + _MSG_ZONA % a)


# --- FASE 0b (14-jul-26): cerrar la costura rm/mv DESTRUCTIVO ----------------------------
# muro_guard cerraba "plantar codigo" pero dejaba BORRAR/mover el arbol y el estado no
# versionado (rm -rf tools/skills, mv tools/state /tmp, rm run_agent.sh). El .git esta
# protegido, pero un agente inyectado podia dejar el lazo tirado. Se cierra SOLO lo
# destructivo (no la copia, que es inofensiva) y todo lo clinico.
# Zonas del repo donde el lazo SI puede borrar/mover legitimamente (cola de jobs, estado
# operativo): no se protegen.
_LAZO_ESTADO_OK = ("/tools/state/queue", "/tools/state/done", "/tools/state/locks",
                   "/tools/state/heartbeat")


def _es_clinico_path(path):
    if _ZC is not None:
        return _ZC.es_ruta_clinica(path)
    return any(h in str(path).lower() for h in _CLINICAL_HINTS_FALLBACK)


def _repo_protegido(path):
    """True si `path` cae dentro del repo y NO es una zona operativa del lazo.
    Destruir/mover esto = romper el sistema."""
    p = os.path.abspath(os.path.expanduser(path))
    dentro = p == REPO or p.startswith(REPO + os.sep)
    if not dentro:
        return False
    return not any(z in p for z in _LAZO_ESTADO_OK)


def _sale_del_repo(path):
    """True si `path` (destino de un mv) esta FUERA del repo."""
    p = os.path.abspath(os.path.expanduser(path))
    return not (p == REPO or p.startswith(REPO + os.sep))


def check_write(data):
    # Write/Edit/MultiEdit/NotebookEdit en el lazo autonomo.
    if PROFILE == "quarantine":
        deny("cuarentena: prohibido escribir/editar (perfil de solo lectura).")
    ti = data.get("tool_input") or {}
    paths = [p for p in (ti.get("file_path"), ti.get("notebook_path")) if p]
    if not paths:
        deny("write/edit sin file_path (fail-closed).")
    for p in paths:
        low = str(p).lower()
        if any(h in low for h in SELF_PROTECT):
            deny("escribir el propio muro (settings/hooks/guard/.HALT): %s" % p)
        if any(h in low for h in SECRET_HINTS):
            deny("escribir un fichero de secretos/credenciales: %s" % p)
        if _hits_crit(p):
            deny("escribir config critica (mcp/git/launchd/estado-coste/agentes): %s" % p)
        if low.endswith(WRITE_EXEC_EXT):
            deny("el lazo NO escribe codigo/ejecutables (%s); el codigo va por rama/PR supervisado." % os.path.basename(str(p)))
        # Ruta real (realpath sigue un symlink ya plantado hacia la zona).
        if _toca_zona(str(p)):
            deny(_MSG_ZONA % p)


def check_read(data):
    # Lectura por tool NATIVA (Read/Grep/Glob/LS/NotebookRead) de datos CLÍNICOS.
    # Hermano de check_write, pero sobre lectura. El comité clínico opta-in con
    # MURO_ALLOW_CLINICAL=1; el resto del lazo tiene el clínico VEDADO (defensa en
    # profundidad — el egress ya está cerrado, esto cierra también la lectura).
    if CLINICAL_ALLOWED:
        return
    ti = data.get("tool_input") or {}
    # Read→file_path · NotebookRead→notebook_path · Grep/Glob/LS→path (dir o fichero).
    for k in ("file_path", "notebook_path", "path"):
        v = ti.get(k)
        if v and _es_clinico_path(v):
            deny("lectura de datos clinicos fuera del comite clinico: %s" % v)
    # `pattern` (Glob/Grep) NO es una ruta que resolver, pero un patron que NOMBRA la zona
    # clinica esta pescando en ella. clinico_guard ya lo miraba; aqui faltaba, asi que un
    # Glob("**/_PRIVADO_CLINICO/**") se colaba entero por el lazo 24/7.
    pat = ti.get("pattern")
    if pat:
        malo = _ZC.es_patron_clinico(pat) if _ZC is not None \
            else any(h in str(pat).lower() for h in _CLINICAL_HINTS_FALLBACK)
        if malo:
            deny("patron de busqueda sobre datos clinicos fuera del comite clinico: %s" % pat)


# ── Un secreto impreso está QUEMADO (norma feedback-secreto-impreso-esta-quemado) ──────────
# Clase BLOQUEO. `SECRET_HINTS` ya deniega NOMBRAR un fichero de secretos (check_subcommand y
# check_write), así que `cat .env` o `base64 tools/.telegram_secrets.json` ya caían. Lo que
# faltaba es lo que la shell IMPRIME sin que el guard llegue a verlo, porque lo expande DESPUÉS
# de que él haya tokenizado. Dos formas, las dos hermanas de `$(...)` — por eso este check vive
# pegado a esa comprobación y no más abajo, entre las reglas por binario:
#
#   · `echo $ANTHROPIC_API_KEY` → el guard ve un literal `$ANTHROPIC_API_KEY`; bash escribe la
#     clave en stdout, y stdout es la transcripción, que se guarda.
#   · `cat tools/.tele*.json` → el patrón no casa con ningún SECRET_HINT como literal, pero
#     resuelve al fichero de secretos. Se juzga por ARGUMENTO RESUELTO, no por la cadena
#     (norma feedback-muro-soak-antes-de-fusionar, segunda mitad).
#
# POR QUÉ MIRA EL NOMBRE DE LA VARIABLE Y NO EL `$`: denegar toda expansión tumbaría `echo $PATH`
# y medio lazo. El intento del 17-sep-26 se revirtió por denegar `ls -la` y `git status`; ninguno
# de los dos lleva `$` ni glob, y los dos son ahora regresión fija en
# tests/test_muro_secreto_stdout.py.
SECRET_VAR_HINTS = ("KEY", "TOKEN", "SECRET", "PASS", "CRED", "AUTH", "BEARER", "PRIVATE")
# `$VAR`, `${VAR}`, `${VAR:-x}`. `$1`/`$@`/`$?` no casan (exigimos identificador).
_VAR_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")
_GLOB_CHARS = "*?["
# Un fichero de CÓDIGO no es un almacén de claves aunque se llame como uno: `tools/_secrets.py`
# es el módulo que LEE los secretos. Sin esta talla, `ls tools/*.py` (comando cotidiano del lazo:
# 2 apariciones en los logs reales de launchd) se denegaba — exactamente la clase de falso
# positivo que obligó a revertir el intento del 17-sep. Se juzga por la ruta RESUELTA, así que
# aquí basta mirar su extensión; el camino por LITERAL de SECRET_HINTS no cambia.
_EXT_NO_SECRETO = (".py", ".sh", ".bash", ".zsh", ".js", ".ts", ".rb", ".pl", ".go", ".md", ".rst")
_GLOB_MAX = 500          # techo de expansión: un patrón absurdo no cuelga el hook
_GLOB_MAX_PATRONES = 20  # ídem para el número de palabras-patrón que se expanden


def _var_secreta(nombre):
    """¿El NOMBRE de la variable delata un secreto? (mayúsculas: KEY/TOKEN/SECRET/…)"""
    up = nombre.upper()
    return any(h in up for h in SECRET_VAR_HINTS)


def _globs_a_secreto(command):
    """Expande los patrones del comando y devuelve el primer resultado que sea un secreto.

    Fail-OPEN a propósito (devuelve None si algo revienta al expandir): este check es defensa
    ADICIONAL sobre la que ya hace SECRET_HINTS por la cadena, y el resto del muro sigue
    aplicándose entero después. Hacerlo fail-closed convertiría cualquier rareza del sistema de
    ficheros en un DENY de comandos legítimos — que es exactamente cómo se rompió el muro el
    17-sep. Lo que este check añade no se pierde: lo que no se pueda expandir, no se abre.
    """
    patrones = 0
    for palabra in command.split():
        if not any(ch in palabra for ch in _GLOB_CHARS):
            continue
        palabra = palabra.strip("\"'")
        if "$" in palabra:
            continue  # lleva variable dentro: no es expandible aquí, lo juzga el check de arriba
        patrones += 1
        if patrones > _GLOB_MAX_PATRONES:
            return None
        pat = os.path.expanduser(palabra)
        bases = [""] if os.path.isabs(pat) else list(_CWDS)
        for base in bases:
            entero = pat if not base else os.path.join(base, pat)
            try:
                vistos = 0
                for hit in glob.iglob(entero):       # sin recursive=True: `**` no baja el árbol
                    vistos += 1
                    if vistos > _GLOB_MAX:
                        break
                    low = hit.lower()
                    if low.endswith(_EXT_NO_SECRETO):
                        continue
                    if any(h in low for h in SECRET_HINTS):
                        return hit
            except Exception:
                continue
    return None


def check_secreto_a_stdout(command):
    """Nada que imprima un secreto: la transcripción se guarda y lo deja quemado."""
    for nombre in _VAR_RE.findall(command):
        if _var_secreta(nombre):
            deny("un secreto impreso queda QUEMADO en la transcripcion: expansion de $%s. "
                 "Las claves salen por el portapapeles o por un comando que corra {{TITULAR}}, "
                 "nunca por stdout." % nombre)
    hit = _globs_a_secreto(command)
    if hit:
        deny("el patron del comando resuelve a un fichero de secretos (%s): imprimirlo lo "
             "QUEMA en la transcripcion." % os.path.basename(hit))


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except Exception:
        deny("entrada del hook no es JSON (fail-closed).")
    if any(os.path.exists(h) for h in HALT_FILES):
        deny(".HALT activo: sistema en pausa total.")
    # Directorios desde los que se resuelven las rutas relativas (zonas por ruta real): el cwd
    # que manda el harness, el del proceso y REPO. Más candidatos = solo más prudencia.
    for _c in (data.get("cwd"), os.getcwd()):
        if isinstance(_c, str) and os.path.isabs(_c) and _c not in _CWDS:
            _CWDS.append(_c)

    tool = data.get("tool_name")
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        check_write(data)
        allow("escritura/edición local permitida")
    # Lecturas locales por tool nativa: pasan, pero NO sobre el clínico (check_read).
    if tool in ("Read", "Grep", "Glob", "LS", "NotebookRead"):
        check_read(data)
        allow("lectura local permitida")
    # H2: resto de lecturas locales seguras (y MCP explícitamente allowlistados, hoy ninguno).
    if tool in SAFE_TOOLS or tool in SAFE_MCP_TOOLS:
        allow("tool de lectura segura")
    # EXCEPCION DE SUPERVISION: Task SOLO para la mesa fija fail-closed (verificacion /
    # consejero-arquitectura), por nombre EXACTO. El sub-agente sigue bajo este mismo muro
    # (privileged): no gana egress, solo una segunda voz. Cualquier otro subagent_type -> DENY.
    # 25-jul-2026: se comprobaba solo "Task" y el harness manda "Agent" para el MISMO tool, asi
    # que la excepcion llevaba 12 reconfirmaciones muerta (el DENY generico de abajo se comia
    # verificacion/consejero-arquitectura en privileged). Los dos nombres, mismo trato: el
    # subagent_type sigue siendo la puerta, esto no abre nada nuevo.
    if tool in ("Task", "Agent"):
        subtype = (data.get("tool_input") or {}).get("subagent_type")
        if PROFILE == "privileged" and subtype in TASK_ALLOW_PRIV:
            allow("Task de supervisión permitido: %s" % subtype)
        deny("Task fuera de la excepcion de supervision (solo verificacion/consejero-arquitectura): %r" % subtype)
    if tool != "Bash":
        deny("tool no permitida en modo autonomo: %r (solo lectura local; MCP/Task/WebFetch fuera; la salida hacia fuera es solo tools/salida.py)" % tool)
    ti = data.get("tool_input") or {}
    command = ti.get("command")
    if not command or not isinstance(command, str):
        deny("comando vacio o no-string.")
    # A1 (hallazgo A): el flag que la sesión usó en el transcript real del 30-jun para reintentar
    # un `git branch -d` denegado. Global de un solo uso por invocación (ver _SANDBOX_ESCALADO).
    global _SANDBOX_ESCALADO
    _SANDBOX_ESCALADO = bool(ti.get("dangerouslyDisableSandbox"))

    check_secreto_a_stdout(command)

    if "$(" in command or "`" in command or "<(" in command or ">(" in command:
        deny("substitucion de comandos/procesos prohibida ($()/`/<()).")

    # SALTO DE LÍNEA = SEPARADOR (11-sep-26, deuda muro_salto_de_linea). shlex lo tomaba por
    # espacio: `ls\ncurl https://x` se leía como `ls` con argumentos y el curl de la 2ª línea se
    # ejecutaba sin pasar por la allowlist (verificado en casa base; el lazo lo usaba sin querer
    # para colar `timeout … bash` y `python3 -c`). Ahora \n es puntuación (dentro de comillas
    # sigue siendo texto) y shlex NO trata comentarios: los consumía HASTA el salto, incluido, y
    # `ls # x\ncurl …` volvía a pegar las líneas. Los comentarios se quitan antes, como bash:
    # `#` fuera de comillas y a principio de palabra, hasta el salto (sin comérselo).
    command = _quita_comentarios(command)
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n")
        lex.whitespace = " \t\r"
        lex.commenters = ""
        lex.whitespace_split = True
        crudos = list(lex)
    except ValueError:
        deny("no pude tokenizar (comillas mal cerradas) -> fail-closed.")
    # shlex agrupa signos seguidos (`\n>`, `;\n`): el salto va suelto para que una redirección
    # pegada a él se siga reconociendo como redirección.
    tokens = []
    for t in crudos:
        if "\n" in t and set(t) <= _SHELL_PUNCT and t != "\n":
            for j, trozo in enumerate(t.split("\n")):
                if j:
                    tokens.append("\n")
                if trozo:
                    tokens.append(trozo)
        else:
            tokens.append(t)

    cur = []
    i, n = 0, len(tokens)
    while i < n:
        t = tokens[i]
        if is_redirect(t):
            # El token siguiente es el DESTINO de la redirección, no un comando.
            target = tokens[i + 1] if i + 1 < n else None
            if target is None:
                deny("redireccion sin destino -> fail-closed.")
            # Solo descarte a /dev/null (etc.) o duplicado de fd (2>&1 -> destino '1'); cualquier otro
            # destino es una ESCRITURA a un fichero real -> va por la tool Write, no por la shell.
            if not (target.isdigit() or target in SAFE_REDIR_TARGETS):
                deny("redireccion a fichero prohibida en modo autonomo (usa la tool Write): %s" % target)
            # El fd numerico que quedo en `cur` antes del operador (el '2' de `2>`) es parte de la
            # redireccion, no un argumento del comando -> quitalo (si no, `git checkout -b foo 2>x`
            # veria 'foo' y '2' como dos positionals y lo denegaria en falso).
            if cur and cur[-1].isdigit():
                cur.pop()
            i += 2          # consume operador + destino
            continue
        if is_separator(t):
            check_subcommand(cur)
            cur = []
        else:
            cur.append(t)
        i += 1
    check_subcommand(cur)
    allow("bash seguro (allowlist del muro)")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        sys.stderr.write("MURO ⛔ excepcion del guard (fail-closed): %r\n" % (e,))
        sys.exit(2)
