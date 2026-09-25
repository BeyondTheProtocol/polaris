#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""regla_en_accion.py — PreToolUse: trae la regla JUSTO cuando se va a actuar.

EL PROBLEMA (25-jul-26)
-----------------------
Las reglas llegan al principio de la sesión (CLAUDE.md) o al escribir el mensaje
(`memoria_recall.sh`). Pero la acción arriesgada ocurre 40 turnos después, y encima
lo que inyecta un hook NO sobrevive a un `/compact`. Resultado medido: 22 memorias
registran correcciones que {{TITULAR}} ha tenido que repetir.

Este hook cierra ese hueco: mira lo que se va a EJECUTAR y, si toca una de las líneas
sensibles, recuerda la regla exacta en ese instante.

QUÉ NO ES
---------
No es el muro. **No bloquea** por defecto: quien bloquea es `muro_guard.py` (lazo 24/7)
y `clinico_guard.py` (lectura clínica). Duplicar el bloqueo aquí daría dos sitios que
mantener y una sesión rota cuando discrepen. Aquí se RECUERDA.

La excepción son cuatro acciones donde un recordatorio silencioso no basta: tres piden
confirmación (`ask`) —push del repo clínico, cargar launchd desde un worktree, desplegar
Air↔Polaris por fuera de `deploy_ff.sh`— y una DENIEGA (`deny`): escribir en casa base desde
un worktree, porque en vivo su `ask` se resolvía solo (ver la regla). Los `git` que mueven el
árbol vivo de casa base los frena `casa_base_guard.py`, con el mismo analizador (`_git_camino`);
aquí estuvieron del 22 al 24-sep-26, duplicados con aquel hook. El worktree se detecta
por CLAUDE_PROJECT_DIR o por el `cwd` de la entrada (22-sep-26, ver `_raiz_sesion`). Se usa
`ask` porque su efecto está garantizado por la API de hooks; `additionalContext` es el
canal preferido para el resto, y si una versión de Claude Code lo ignorase, lo único que
se pierde es un recordatorio blando, no una protección.

LÍMITE DECLARADO de `escritura-en-casa-base` (25-jul-26)
--------------------------------------------------------
Cubre entera la vía Edit/Write/NotebookEdit, que es por donde pasa el 90% del riesgo. Por
Bash cubre solo las escrituras cuyo DESTINO sabe leer (redirección, `tee`, `sed -i`, último
argumento de `cp`/`mv`); `python3 -c` con `open(`, las variables y `eval` NO se cubren desde el
22-sep-26 (daban falsos `deny` sobre lecturas). El shell arbitrario no se puede cubrir del todo, y decirlo aquí
importa porque este freno nació justo de un fichero —`tools/normas.json`— que declaraba una
protección que no existía. Media verdad sobre una protección es peor que ninguna.

FAIL-OPEN: ante cualquier error interno, sale 0 y no estorba. Un bug aquí no puede
dejar a {{TITULAR}} sin trabajar.
"""
import hashlib
import json
import os
import re
import sys
import time

REPO = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.expanduser("~/claudecode")
MARCA_WT = "/.claude/worktrees/"
# Misma clase que el log clínico (20-sep-2026): un worktree tiene `.git` como FICHERO, y
# resolver el log contra él partía la traza por sesión, en un directorio gitignored que la
# poda se lleva sin que `git status` diga nada. El log va a casa base.
# `BTP_REGLA_LOG` lo redirige (los tests lo apuntan a su tmp). Y el worktree se detecta también
# por la RUTA: un `.git` fichero no basta — `tests/test_regla_en_accion.py` monta un worktree
# simulado (`.claude/worktrees/prueba-freno`) que no tiene `.git` ninguno, así que el log de cada
# pasada de test_all se quedaba allí dentro y dejaba un directorio huérfano con pinta de worktree
# (95 KB acumulados a 24-sep-2026).
_EN_WT = MARCA_WT in REPO or os.path.isfile(os.path.join(REPO, ".git"))
LOG = os.environ.get("BTP_REGLA_LOG") or os.path.join(
    os.path.expanduser("~/claudecode") if _EN_WT else REPO,
    ".claude", "logs", "regla-en-accion.log")
# Anti-ruido: cada regla se recuerda UNA vez por sesión. Repetirla en cada llamada
# la convertiría en ruido de fondo, que es justo como se pierden las reglas.
STAMP_DIR = os.path.join(os.environ.get("TMPDIR", "/tmp"), "btp_regla_en_accion")


# (clave, ¿aplica?, texto de la regla, ¿pedir confirmación?)
def _cmd(entrada):
    ti = entrada.get("tool_input") or {}
    return str(ti.get("command", "") or "")


def _rutas(entrada):
    ti = entrada.get("tool_input") or {}
    campos = [ti.get("file_path"), ti.get("path"), ti.get("notebook_path")]
    return " ".join(str(c) for c in campos if c)


# `cwd` que manda el harness en la entrada del PreToolUse; lo fija `main()`. (22-sep-26)
_CWD = ""


def _raiz_sesion():
    """Raíz del árbol en el que trabaja la sesión: el worktree si lo hay, si no REPO.

    CLAUDE_PROJECT_DIR NO basta. En las sesiones worktree de la app de escritorio el hook corre
    con CLAUDE_PROJECT_DIR = casa base, así que mirando solo eso `_en_worktree()` era siempre
    False y el freno de escritura en casa base no frenaba nunca en vivo (22-sep, 10:15: un
    `echo > ~/claudecode/tools/…` desde un worktree escribió sin que el hook dijera nada). El
    `cwd` de la entrada sí dice dónde está la sesión; `muro_guard.py` ya lo usa igual.
    Fail-open: sin `cwd` legible se queda como antes."""
    raiz = os.path.abspath(REPO)
    if MARCA_WT in raiz:
        return raiz
    cwd = os.path.abspath(_CWD) if _CWD else ""
    if MARCA_WT in cwd:
        base, resto = cwd.split(MARCA_WT, 1)
        nombre = resto.split(os.sep)[0]
        if nombre:
            return os.path.join(base + MARCA_WT.rstrip(os.sep), nombre)
    return raiz


def _en_worktree():
    return MARCA_WT in _raiz_sesion()


def _proyecto_en_worktree():
    """Lo de antes del 22-sep: solo CLAUDE_PROJECT_DIR. Lo usa `launchd-desde-worktree`, que
    NO se ha pasado al `cwd`: el replay sacó 28 avisos nuevos en 7 días, todos
    `cd ~/claudecode; launchctl …` lanzados desde una sesión worktree, y decidir si eso debe
    avisar es otra discusión (deuda `launchd-desde-worktree-no-ve-cwd`)."""
    return MARCA_WT in os.path.abspath(REPO)


def _bucle_sin_tope(cmd):
    """¿Un `until|while … do … sleep … done` sin nada que lo corte solo?

    UNA sola definición para el hook y para el matador: se importa la de
    `tools/bucles_colgados.py`, que es quien ya sabe distinguir un bucle de shell de un `while`
    de Python dentro de un heredoc. Si no se puede importar, el hook calla (fail-open): este
    aviso no vale una sesión rota."""
    # Dos sitios donde buscarlo: el proyecto declarado y el repo del propio hook. El segundo
    # hace falta porque CLAUDE_PROJECT_DIR puede apuntar a otro sitio (los tests lo mandan a un
    # tmp para no ensuciar el log de auditoría), y sin él la regla no dispararía nunca.
    _aqui = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for base in (REPO, _aqui):
        try:
            ruta = os.path.join(base, "tools")
            if ruta not in sys.path:
                sys.path.insert(0, ruta)
            import bucles_colgados
            return bucles_colgados.bucle_sin_tope(cmd)
        except Exception:
            continue
    return False


def _casa_base():
    """Raíz de casa base. Si la sesión vive en un worktree, se DERIVA de su propia ruta
    (`<casa base>/.claude/worktrees/<rama>`) en vez de dar por hecho `~/claudecode`: así el
    freno sigue valiendo si el repo se mueve de sitio."""
    raiz = _raiz_sesion()
    if MARCA_WT in raiz:
        return raiz.split(MARCA_WT)[0]
    return os.path.expanduser("~/claudecode")


def _versionado_en_casa_base(ruta):
    """True si `ruta` apunta a un fichero de casa base que git VERSIONA (o versionaría).

    El discriminador no es una lista de exclusiones a mano —esas se quedan viejas— sino lo que
    DEFINE el problema: bajo casa base, lo que git ignora existe SOLO ahí (el estado vivo de
    `tools/state/`, la fuente de verdad, lo clínico) y escribirlo desde un worktree es correcto y
    a menudo obligatorio; lo que git versiona tiene una COPIA en el worktree, y editar la de casa
    base es exactamente lo que anula el aislamiento.

    Se usa `check-ignore` y no `ls-files` porque acierta también con un fichero que todavía no
    existe: pregunta por la intención del `.gitignore`, no por el índice.

    Fail-OPEN (False ante cualquier duda): este hook nunca puede dejar a {{TITULAR}} sin trabajar."""
    try:
        if not ruta or not os.path.isabs(ruta):
            return False          # una ruta relativa resuelve DENTRO del worktree: esa es la buena
        base = _casa_base().rstrip(os.sep)
        real = os.path.realpath(ruta)   # realpath: que un symlink o un `..` no lo esquiven
        if not real.startswith(base + os.sep):
            return False
        # El marcador se busca en lo que va DESPUÉS de casa base, no en la ruta entera: la propia
        # casa base puede colgar de una carpeta que lleve `worktrees` en el nombre (pasa en la
        # batería, que corre desde un worktree) y entonces se rechazaba todo en silencio.
        if MARCA_WT.strip(os.sep) in real[len(base):].split(os.sep):
            return False
        import subprocess
        r = subprocess.run(["git", "-C", base, "check-ignore", "-q", real],
                           capture_output=True, timeout=4)
        return r.returncode != 0      # rc 0 = ignorado (vive solo aquí) → dejar pasar
    except Exception:
        return False


def _rutas_lista(entrada):
    ti = entrada.get("tool_input") or {}
    return [str(c) for c in (ti.get("file_path"), ti.get("path"),
                             ti.get("notebook_path")) if c]


# Escrituras por shell que sí se saben leer. NO es todo el shell posible y el docstring del
# módulo lo dice: prometer cobertura total sería repetir el error que originó este freno.
#
# Se mira el DESTINO, no el comando entero (22-sep-26). La versión anterior daba por escrita
# cualquier ruta de casa base que apareciera en un comando con algo de pinta de escritura
# (`>/`, `cp`, `tee`…) en cualquier parte. Mientras el freno no se encendía en vivo daba igual;
# al encenderlo, el replay de 13.578 llamadas reales sacó 75 `deny` en Bash y casi todos eran
# LECTURAS: `ls ~/claudecode/tools 2>/dev/null`, `cp ~/claudecode/x /tmp/`, `cat x > /tmp/y`.
# Un freno que deniega lecturas se acaba desactivando entero.
_RE_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n(.*?)(?:\n[ \t]*\2[ \t]*(?=\n|$)|\Z)", re.S)
_RE_REDIR = re.compile(r"(?:&|\d)?>>?\|?\s*(\"[^\"]*\"|'[^']*'|[^\s;|&()<>]+)")
_RE_SEPARA = re.compile(r"\|\||&&|[;|\n]")


def _sin_heredocs(cmd):
    """Quita el CUERPO de los heredocs: es texto que se le pasa a un programa (código Python,
    un JSON…), no shell, y ahí dentro «> /ruta» no redirige nada."""
    return _RE_HEREDOC.sub(lambda m: m.group(0).split("\n", 1)[0] + "\n", cmd)


_RE_COMILLAS = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'[^']*'")


def _sin_textos(txt):
    """Vacía lo que va entre comillas, salvo que sea justo el destino de una redirección
    (`> "/ruta"`): un `J='{… > /ruta …}'` o el mensaje de un `deuda.py abrir "…"` es texto,
    no una redirección (falsos positivos reales del replay)."""
    def _cambia(m):
        antes = txt[:m.start()].rstrip()
        return m.group(0) if antes.endswith(">") else "''"
    return _RE_COMILLAS.sub(_cambia, txt)


def _destinos_shell(cmd):
    """Rutas que un comando de shell va a ESCRIBIR, en la medida en que se sabe leer: destino de
    `>`/`>>`/`2>`/`&>`, argumentos de `tee`, ficheros de `sed -i` y último argumento de
    `cp`/`mv`. Lo que no se sabe leer (variables, `python3 -c`, `eval`) no se adivina."""
    import shlex
    txt = _sin_heredocs(cmd)
    # Redirecciones: sobre el texto SIN lo entrecomillado. Argumentos: sobre el original, que
    # shlex ya sabe leer las comillas (un `cp x '/ruta'` tiene el destino entre comillas).
    destinos = [m.group(1).strip("\"'") for m in _RE_REDIR.finditer(_sin_textos(txt))]
    for trozo in _RE_SEPARA.split(_RE_REDIR.sub(" ", txt)):
        try:
            pal = shlex.split(trozo)
        except ValueError:
            pal = trozo.split()
        while pal and re.match(r"^\w+=", pal[0]):          # VAR=x comando …
            pal = pal[1:]
        if not pal:
            continue
        prog, args = os.path.basename(pal[0]), pal[1:]
        libres = [a for a in args if not a.startswith("-")]
        if prog == "tee":
            destinos += libres
        elif prog in ("sed", "gsed") and any(a.startswith("-i") or a.startswith("--in-place") for a in args):
            destinos += libres
        elif prog in ("cp", "mv") and len(libres) >= 2:
            destinos.append(libres[-1])
    return destinos


def _escritura_shell_a_casa_base(entrada):
    """Rutas de casa base versionadas que un comando de shell va a ESCRIBIR."""
    cmd = _cmd(entrada)
    if not cmd:
        return []
    try:
        destinos = _destinos_shell(cmd)
    except Exception:
        return []                                            # fail-open
    base = _casa_base().rstrip(os.sep)
    return [d for d in dict.fromkeys(destinos)
            if (d == base or d.startswith(base + os.sep)) and _versionado_en_casa_base(d)]


def _push_de_casa_base(cmd):
    """¿Algún `git push` EJECUTADO sale de casa base o de uno de sus worktrees? El camino real
    hasta el repo lo calcula `_git_camino`, compartido con `casa_base_guard.py` (24-sep-26): antes
    esta lógica estaba aquí y duplicada en aquel hook, y ya discrepaban."""
    import importlib.util
    try:
        ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_git_camino.py")
        spec = importlib.util.spec_from_file_location("_git_camino_regla", ruta)
        camino = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(camino)
    except Exception:
        return bool(re.search(r"\bgit\s+push\b", cmd))
    return camino.push_desde_casa_base(cmd, _CWD)


def _texto_casa_base(entrada, _tool):
    """Mensaje CONCRETO: nombra el fichero y da la ruta buena, para que la salida no sea un «no»
    a secas sino el camino correcto."""
    malas = [r for r in _rutas_lista(entrada) if _versionado_en_casa_base(r)] \
        or _escritura_shell_a_casa_base(entrada)
    base, aqui = _casa_base(), _raiz_sesion()
    detalle = ""
    if malas:
        buena = malas[0].replace(base.rstrip(os.sep), aqui.rstrip(os.sep), 1)
        detalle = "\n  vas a escribir: %s\n  deberías escribir: %s" % (malas[0], buena)
    return (
        "🛑 Estás en un worktree y esto escribe en CASA BASE, sobre un fichero que git versiona "
        "(o sea: existe una copia aquí, en tu rama). Eso anula el aislamiento — tus cambios se "
        "quedan sin commitear en casa base, donde otra sesión paralela los puede pisar." + detalle +
        "\n  Leer fuera del worktree está bien; el `cd` para leer NO autoriza a escribir ahí. "
        "El estado vivo (`tools/state/`), la fuente de verdad y lo clínico SÍ son de casa base y "
        "no se frenan. Ver `.claude/rules/tools-python.md`.")


REGLAS = [
    (
        "push-repo-clinico",
        lambda e, t: t == "Bash" and _push_de_casa_base(_cmd(e)),
        "🛑 El repo NO se sube a GitHub: su historial lleva datos clínicos. "
        "Para mover código entre Air y Polaris la única vía es `tools/deploy_ff.sh` "
        "(fast-forward). Ver `.claude/rules/deploy-ff.md`.",
        True,
    ),
    (
        "launchd-desde-worktree",
        lambda e, t: (t == "Bash"
                      and re.search(r"launchctl\s+(load|bootstrap|enable|kickstart)", _cmd(e))
                      and _proyecto_en_worktree()),
        "🛑 Estás en un worktree y esto enciende un daemon. Los daemons se activan SOLO "
        "desde casa base (`~/claudecode`) y SOLO con `tools/activar_daemon.py <label>`, "
        "que valida label único, no-worktree y concern único. Encenderlo es gate de {{TITULAR}}.",
        True,
    ),
    (
        "deploy-a-mano",
        lambda e, t: (t == "Bash"
                      and re.search(r"(rsync|scp).*(polaris|air)", _cmd(e), re.I)),
        "🛑 Desplegar copiando ficheros fue justo lo que forkeó Air↔Polaris el 12-jul y "
        "estuvo a punto de borrar trabajo. La única vía es `tools/deploy_ff.sh "
        "{to-polaris|from-polaris}`, que rehúsa si las dos historias divergen.",
        True,
    ),
    (
        # Rota TRES veces en este repo (26-jun, 28-jun, 25-jul), siempre igual: el fichero que
        # hace falta leer vive fuera del worktree (00_FUENTE-DE-VERDAD está gitignored), un
        # `cd ~/claudecode` para encontrarlo, y de ahí ya no se vuelve. El 25-jul fueron 13 tools
        # editadas en casa base; el 28-jun otra sesión paralela committeó lo mismo y el trabajo
        # sin commitear se perdió. Se pide confirmación UNA vez por sesión (_ya_dicho): pilla el
        # primer Edit, que es donde empieza el error, sin dar la lata trece veces.
        # DENY y no `ask` (corregido el 25-jul-26 tras probarlo en vivo): se implementó con `ask`
        # y la escritura pasó IGUAL. El log del hook y el sello anti-repetición prueban que la
        # regla disparó y emitió su `ask`; simplemente, con el modo de permisos de una sesión
        # normal ese `ask` se resuelve solo y nadie ve nada. Un freno que solo frena a veces es
        # el mismo problema que vine a arreglar. `deny` sí está garantizado — es lo que usa
        # `clinico_guard.py`. Se puede saltar a propósito con BTP_ALLOW_CASA_BASE=1, igual que
        # el comité clínico salta su guard con MURO_ALLOW_CLINICAL=1.
        "escritura-en-casa-base",
        lambda e, t: (_en_worktree()
                      and os.environ.get("BTP_ALLOW_CASA_BASE") != "1"
                      and ((t in ("Edit", "Write", "NotebookEdit")
                            and any(_versionado_en_casa_base(r) for r in _rutas_lista(e)))
                           or (t == "Bash" and _escritura_shell_a_casa_base(e)))),
        _texto_casa_base,
        "deny",
    ),
    (
        "hacia-fuera-sin-ok",
        lambda e, t: (re.search(r"(send|reply|publish|post|tweet)", t or "", re.I)
                      and not re.search(r"draft", t or "", re.I)),
        "🛑 Gate de salida: nada sale hacia fuera sin el OK explícito de {{TITULAR}}. "
        "Déjalo en BORRADOR y que firme ella.",
        False,
    ),
    (
        # Cazada por `tools/reglas_repetidas.py`: a {{TITULAR}} se le repitió 3 veces.
        "correo-formato",
        lambda e, t: re.search(r"draft|correo|email|mail", t or "", re.I),
        "✉️ El correo va en HTML (negritas en lo clave, enlaces clicables, listas), pasa "
        "por `voz-titular`, y se manda desde la cuenta del hilo. Queda en BORRADOR: firma "
        "{{TITULAR}}. Ver `.claude/rules/correo.md`.",
        False,
    ),
    (
        "copy-publicado",
        lambda e, t: (t in ("Edit", "Write", "NotebookEdit")
                      and re.search(r"(hero|landing|index\.(vue|html)|tagline)", _rutas(e), re.I)),
        "⚠️ Copy ya publicado: editarlo no es zona autónoma. Un «dale» rápido no basta, "
        "lo mergea {{TITULAR}}. Páginas o secciones NUEVAS sí son autónomas. "
        "Ver `.claude/rules/marca-copy.md`.",
        False,
    ),
    (
        "clinico",
        lambda e, t: re.search(r"_PRIVADO_|Clinico-PRIVADO", _rutas(e) + " " + _cmd(e)),
        "⚠️ Dato clínico: la lectura va por `tools/lector_clinico.py` (ventanilla auditada) "
        "y nada N2 (relato o informe crudo con nombre) sale a un SaaS sin contrato. "
        "De-identifica con `deid.py`. Ver `.claude/rules/clinico.md`.",
        False,
    ),
    (
        "constitucion",
        lambda e, t: (t in ("Edit", "Write")
                      and re.search(r"(CLAUDE\.md|MEMORY\.md|\.claude/rules/)", _rutas(e))),
        "📏 Estás tocando las reglas del sistema. Los límites son reales: MEMORY.md se corta "
        "a 200 líneas o 25KB (en silencio), y CLAUDE.md pierde adherencia por encima de 15KB. "
        "Después: `python3 tools/salud_memoria.py`. Ver `.claude/rules/memoria-sistema.md`.",
        False,
    ),
    (
        "borrado",
        lambda e, t: t == "Bash" and re.search(r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\b", _cmd(e)),
        "⚠️ Borrado recursivo. Mira antes qué hay dentro: lo irreversible es gate de {{TITULAR}}, "
        "y lo clínico y el estado vivo no se recuperan de un `rm`.",
        False,
    ),
    (
        "bucle-sin-tope",
        lambda e, t: t == "Bash" and _bucle_sin_tope(_cmd(e)),
        "⏳ Ese bucle de espera no tiene tope. El 14-sep uno así (`until … do sleep 5 … done`) "
        "corrió 14 h 34 min esperando algo que nunca llegó y dejó pillados la tarea y el "
        "worktree. Ponle reloj — aquí NO hay `timeout` ni `gtimeout`:\n"
        "    fin=$(( $(date +%s) + 600 )); until <cond>; do \\\n"
        "      [ $(date +%s) -lt $fin ] || { echo 'tope alcanzado'; break; }; sleep 5; done\n"
        "Si de verdad hay que esperar más, súbelo a propósito. `tools/bucles_colgados.py` mata "
        "lo que pase de 45 min, pero llegar a eso ya te ha costado la tarde.",
        False,
    ),
    (
        "dinero",
        lambda e, t: re.search(r"(pay|payment|checkout|purchase|transfer)", t or "", re.I),
        "🛑 Dinero: no se paga ni se mueve dinero. Se deja «a un clic» y firma {{TITULAR}}.",
        True,
    ),
]


def _ya_dicho(session, clave):
    """True si esta regla ya se recordó en esta sesión (y la marca si no)."""
    try:
        os.makedirs(STAMP_DIR, exist_ok=True)
        h = hashlib.sha1(("%s|%s" % (session, clave)).encode()).hexdigest()[:16]
        marca = os.path.join(STAMP_DIR, h)
        if os.path.exists(marca) and (time.time() - os.path.getmtime(marca)) < 6 * 3600:
            return True
        open(marca, "w").close()
        return False
    except Exception:
        return False   # ante la duda, mejor recordarla otra vez que callarla


def _log(clave, tool):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write("%s\t%s\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), clave, tool))
    except Exception:
        pass


def main():
    try:
        entrada = json.load(sys.stdin)
    except Exception:
        return 0
    tool = entrada.get("tool_name") or ""
    global _CWD
    _CWD = str(entrada.get("cwd") or "") if isinstance(entrada, dict) else ""
    session = str(entrada.get("session_id") or "sin-sesion")

    disparadas = []
    for clave, aplica, texto, confirmar in REGLAS:
        try:
            if aplica(entrada, tool):
                disparadas.append((clave, texto, confirmar))
        except Exception:
            continue
    if not disparadas:
        return 0

    # El texto puede ser una función (entrada, tool) → str, para poder nombrar el fichero
    # concreto y la ruta buena en vez de soltar una regla genérica. Si esa función peta, se
    # cae a un texto neutro: fail-open, un mensaje feo antes que una sesión rota.
    def _render(texto):
        if not callable(texto):
            return texto
        try:
            return texto(entrada, tool)
        except Exception:
            return "🛑 Esta acción toca una regla del sistema. Revísala antes de seguir."

    disparadas = [(c, _render(t), k) for c, t, k in disparadas]
    for clave, _t, _k in disparadas:
        _log(clave, tool)

    # DENIEGAN. Va ANTES del filtro anti-repetición a propósito: un recordatorio se dice una vez,
    # pero un freno que solo frena la primera vez de la sesión no es un freno.
    #
    # Se usa la forma JSON `permissionDecision: "deny"` + exit 0, y NO `exit 2`.
    #
    # ⚠️ SIN CONFIRMAR EN VIVO (25-jul-26). Se probó escribiendo de verdad en casa base desde una
    # sesión en worktree y el resultado NO permite afirmar que frene:
    #   · con `ask`, la escritura pasó. El log del hook y el sello anti-repetición prueban que la
    #     regla disparó, así que el `ask` se resolvió solo sin que nadie viera nada.
    #   · con `exit 2`, también pasó. Ese rc SÍ frena un `Bash` (clinico_guard me paró ese mismo
    #     día con él), pero no frenó la tool `Write`.
    #   · a partir de ahí el hook dejó de CONSULTARSE para `Write` en esa carpeta —ni una línea
    #     más en su log—, que es justo lo que se esperaría si el harness cacheó el permiso que
    #     concedió aquel primer `ask`. O sea que la sesión quedó contaminada y no se pudo probar
    #     la versión `deny` en condiciones limpias.
    # Lo que SÍ está probado: la lógica de la regla (14 casos en tests/test_regla_en_accion.py) y
    # que invocado con la configuración de producción emite el `deny` correcto.
    # Queda por verificar en una sesión NUEVA. Hasta entonces `tools/normas.json` mantiene esta
    # norma en clase `contexto`, NO `bloqueo`, y la deuda `worktree_escritura_sin_freno` sigue
    # ABIERTA — que es el motivo entero por el que existe este freno: el fichero que lo originó
    # declaraba una protección que no existía, y repetir eso aquí sería peor que no hacer nada.
    deniegan = [(c, t) for c, t, k in disparadas if k == "deny"]
    if deniegan:
        motivo = "\n\n".join(t for _c, t in deniegan)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": motivo,
            "additionalContext": motivo,
        }}, ensure_ascii=False))
        return 0

    nuevas = [(c, t, k) for c, t, k in disparadas if not _ya_dicho(session, c)]
    if not nuevas:
        return 0

    texto = "\n".join(t for _c, t, _k in nuevas)
    pedir = any(k is True for _c, _t, k in nuevas)

    salida = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": "REGLA QUE APLICA A ESTA ACCIÓN (de {{TITULAR}}, no es una sugerencia):\n" + texto,
    }}
    if pedir:
        salida["hookSpecificOutput"]["permissionDecision"] = "ask"
        salida["hookSpecificOutput"]["permissionDecisionReason"] = texto
    print(json.dumps(salida, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    # Watchdog (25-sep-26): si tardo más que el timeout de settings (8 s), deniego yo antes de
    # que Claude Code me cancele y lo convierta en un permitir. Ver _watchdog.py.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:  # sin el vigilante el guard sigue siendo el guard: su falta no lo tumba
        import _watchdog
    except ImportError:
        _watchdog = None
        sys.stderr.write("regla_en_accion: falta _watchdog.py; sigo sin vigilante\n")
    if _watchdog:
        _watchdog.armar(8, 'regla_en_accion')
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)   # fail-open, siempre
