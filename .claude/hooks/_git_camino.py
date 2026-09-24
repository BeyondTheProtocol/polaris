#!/usr/bin/env python3
"""_git_camino.py — a qué repo llega de verdad un `git` escrito en el shell.

POR QUÉ (24-sep-2026). Dos hooks necesitaban la MISMA respuesta: `casa_base_guard.py` («¿este git
mueve el árbol vivo de casa base?») y la regla `push-repo-clinico` de `regla_en_accion.py` («¿este
push sale de casa base o de un worktree suyo?»). Ese día se descubrió que las dos sesiones habían
escrito el freno por separado y ya discrepaban entre ellas: el analizador vive aquí, una sola vez.

Sigue `cd` (con `-P`, `-L`, `--`), `pushd`, `popd`, `cd -`, el ámbito de un subshell `( … )`,
`$( )` y backticks (también entre comillas, y con el directorio que rija donde aparecen),
`sh -c`/`bash -c` (también `-lc`) y el cuerpo de un heredoc, `GIT_DIR=`/`GIT_WORK_TREE=` (con
comillas, tras `env`, con `env -i`, con `export`, seguido de un wrapper o de una ruta absoluta),
`env -C`/`--chdir`, las opciones globales antes del subcomando, `-C`, `$HOME`, los wrappers
delante con sus opciones y argumentos (`command`, `sudo -u root`, `nice -n 5`, `timeout -k 5 10`,
`exec -a x`, `then`, `if`, `{`…) y cualquier variable de entorno que el propio hook tenga
(`expandvars`, no solo `$HOME`).

LO QUE NO SIGUE, Y ES UNA DECISIÓN (lista única: el docstring de `git_en_casa_base` remite aquí,
para que no puedan divergir, y `tests/test_casa_base_guard.py` la fija payload a payload):
  · alias (`git -c alias.co=checkout co`): resolverlo exige la config de git del usuario y la del
    repo, y un alias puede apuntar a un script.
  · texto que se convierte en orden en tiempo de ejecución: `eval`, la orden guardada en una
    variable, el programa que sale de una sustitución (`$(which git) checkout`), `xargs git`,
    `env -S`, un script por fichero (`bash s.sh`) o por stdin, y `subprocess` desde `python3 -c`.
  · un `GIT_DIR` exportado que se usa MÁS TARDE, con otra orden por medio
    (`export GIT_DIR=…; echo hola; git stash`): solo se sigue si el `git` es la orden inmediata.
  · dos formas raras de escribir lo que sí se cubre: el valor pegado a la opción corta
    (`env -C<dir>`) y `sh -c` anidado más de tres veces. Deuda `camino-git-formas-pegadas`.
Verlos exigiría INTERPRETAR el shell, no leerlo, y cubrirlos a medias daría falsa confianza.

El troceo del shell lo pone `salida_guard._ordenes`: una sola definición para todos los hooks.
"""
import os
import re

_CWD = ""       # lo fija quien llama (el `cwd` del payload PreToolUse)


def fijar_cwd(cwd):
    global _CWD
    _CWD = str(cwd or "")


def _casa_base():
    """Raíz de casa base. `BTP_CASA_BASE` la sustituye en los tests (no toca la de verdad)."""
    raiz = os.environ.get("BTP_CASA_BASE")
    if raiz:
        return os.path.expanduser(raiz)
    proyecto = os.environ.get("CLAUDE_PROJECT_DIR") or ""
    marca = "/.claude/worktrees/"
    if marca in proyecto:
        return proyecto.split(marca)[0]
    return os.path.expanduser("~/claudecode")


def _sin_heredocs(cmd):
    """Quita el CUERPO de los heredocs para el repaso en crudo (el cuerpo se analiza aparte)."""
    return re.sub(r"<<-?\s*'?\"?(\w+)'?\"?[\s\S]*?\n\1\b",
                  lambda m: m.group(0).split("\n", 1)[0] + "\n", cmd)


# Los `git` que cambian el árbol o el HEAD del checkout donde se ejecutan. En casa base eso es
# cambiar lo que ejecutan los daemons: el 22-sep-26 a las 15:11 un `git checkout <commit>` para
# «mirar» desacopló HEAD ~40 s y devolvió al árbol vivo un run_agent.sh con marcadores de
# conflicto. `merge` y `commit` NO están: casa base recibe fusiones (cerrar_sesion.py, que va por
# subprocess y no por Bash). Las lecturas (show, diff, log, status…) tampoco.
# `merge` NO está: casa base RECIBE fusiones y es la vía documentada (cerrar_sesion.py, que va por
# subprocess y este hook no ve). El resto los añadió `verificacion` el 24-sep: `bisect` y `apply`
# son justo la clase «mirar historia» que rompió run_agent.sh.
MUEVEN_CASA_BASE = frozenset({"checkout", "switch", "reset", "restore", "stash", "rebase",
                              "clean", "cherry-pick", "revert", "am", "pull", "bisect", "apply",
                              "rm", "mv", "read-tree", "checkout-index", "symbolic-ref",
                              "update-ref"})


_RE_COMMIT = re.compile(r"^(?:HEAD[~^]\d*|HEAD@\{.*\}|@\{.*\}|[0-9a-f]{7,40}|origin/\S+|master|main|"
                        r"\S+[~^]\d*)$")


def _es_ruta_de_verdad(token, args, repo=None):
    """¿Ese argumento de `git reset` es un FICHERO y no una rama? Fail-closed: solo cuenta como
    ruta si va tras `--` o si existe en el disco. Antes bastaba con una barra o un punto, así que
    `git reset claude/otra-rama` o `git reset v1.0.0` (que MUEVEN HEAD) pasaban por ficheros
    (`verificacion`, 24-sep-26)."""
    if "--" in args and args.index("--") < args.index(token):
        return True
    base = repo or (_CWD or os.getcwd())
    if os.path.exists(os.path.join(base, token)):
        return True
    # Si no existe (o no se puede mirar): una extensión ALFABÉTICA es un fichero («tools/x.json»);
    # una numérica es un tag («v1.0.0»), y una rama no tiene («claude/otra-rama») → ref, y mueve.
    ext = os.path.splitext(token)[1].lstrip(".")
    return bool(ext) and ext.isalpha()


def mueve_de_verdad(sub, args):
    """Dentro de los subcomandos que mueven, las formas que solo LEEN o solo tocan el ÍNDICE no
    cambian el árbol vivo (replay del 22-sep: `stash list`, `restore --staged`, `reset HEAD f`)."""
    if sub == "stash":
        return not (args[:1] and args[0] in ("list", "show"))
    if sub == "restore":
        solo_indice = any(a in ("--staged", "-S") for a in args)
        return not solo_indice or any(a in ("--worktree", "-W") for a in args)
    if sub == "reset":
        if any(a in ("--hard", "--soft", "--merge", "--keep") for a in args):
            return True
        posicionales = [a for a in args if not a.startswith("-") and a != "--"]
        if not posicionales or posicionales[0] == "HEAD":
            return False               # `git reset`, `git reset HEAD f`: solo deshacen el stage
        return not _es_ruta_de_verdad(posicionales[0], args)
    if (sub in ("checkout", "switch")
            and [a for a in args if not a.startswith("-")] in (["master"], ["main"])
            # …y sin flags que hagan otra cosa: `-B master` con HEAD desacoplado REESCRIBE master
            # al commit viejo, o sea, convierte el incidente en permanente (verificacion, 24-sep).
            and all(a in ("-q", "--quiet", "-f", "--force") for a in args if a.startswith("-"))):
        # Volver a master es la REPARACIÓN de un HEAD desacoplado, no el error (24-sep-26: mi regla
        # la bloqueaba y `casa_base_guard.py` la permitía; con las dos activas ganaba el bloqueo).
        return False
    if sub == "symbolic-ref":
        # `git symbolic-ref HEAD` (y `--short`) solo LEE a dónde apunta; escribe con 2 posicionales
        # o con --delete (replay del 24-sep: dos lecturas legítimas bloqueadas).
        posicionales = [a for a in args if not a.startswith("-")]
        return len(posicionales) >= 2 or any(a in ("-d", "--delete") for a in args)
    solo_lee = _RE_SOLO_LEE.get(sub)
    if solo_lee and solo_lee.search(" " + " ".join(args)):
        return False                   # `git clean -n`, `git rebase --show-current-patch`
    if any(a in ("--help", "-h") for a in args):
        return False
    if sub in ("checkout", "switch") and not args:
        return False                   # sin argumentos solo informan
    return True


# Palabras y wrappers que van DELANTE de una orden y no son el programa: sin esto, `command git
# checkout`, `then git checkout` o `sudo git checkout` se colaban (verificacion, 24-sep-26).
_ANTEPUESTOS = frozenset({"command", "exec", "builtin", "sudo", "nohup", "time", "nice", "timeout",
                          "caffeinate", "then", "do", "else", "elif", "if", "while", "until",
                          "{", "!", "eval", "env"})
# Los flags de cada wrapper que llevan su VALOR aparte. Sin esta tabla el valor queda en primera
# posición y pasa por el programa: `sudo -u root git stash` se leía como el programa `root`.
_FLAGS_CON_VALOR = {
    "sudo": frozenset({"-u", "-g", "-p", "-C", "-h", "-D", "-U", "-T", "-R", "-r", "-t",
                       "--user", "--group", "--prompt", "--chdir", "--host", "--close-from",
                       "--command-timeout", "--chroot", "--role", "--type"}),
    "timeout": frozenset({"-k", "-s", "--kill-after", "--signal"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "env": frozenset({"-u", "-C", "--chdir", "--unset"}),
    "exec": frozenset({"-a"}),
    "caffeinate": frozenset({"-t", "-w"}),
}
# Los flags de un wrapper que además son un `cd` (el directorio donde corre ESA orden).
_FLAGS_CHDIR = {"env": ("-C", "--chdir"), "sudo": ("-D", "--chdir")}
# Argumentos POSICIONALES obligatorios del wrapper: la duración de `timeout`. `nice` NO lleva
# ninguno —su ajuste va siempre tras `-n` o pegado (`-5`)—, y comerle un posicional se tragaba el
# propio `git`: `nice git checkout` pasaba entero (regresión que cazó `verificacion`, 24-sep-26).
_POSICIONALES_DE_WRAPPER = {"timeout": 1}
# Lo que abre otro contexto donde puede haber un `git` que sí se ejecuta.
# El CONTENIDO de una sustitución es una orden que se ejecuta: se cambia por una MARCA y se analiza
# cuando el bucle llega a la orden que la lleva, con el directorio vigente ahí. Cambiar `$(` por
# `;` descuadraba las comillas (`echo "$(git checkout …)"`) y analizarlo suelto perdía los `cd`
# anteriores: `cd <casa> && echo $(git stash)` se colaba (regresión, `verificacion` 24-sep-26).
_RE_SUSTITUCION = re.compile(r"'[^']*'|\$\(([^()]*)\)|`([^`]*)`|[<>]\(([^()]*)\)")
_MARCA_SUB = "__BTPSUB%dX__"
_RE_MARCA_SUB = re.compile(r"__BTPSUB(\d+)X__")
# `python3 -c "…"` es TEXTO, no shell: el repaso crudo de GIT_DIR no puede leer dentro.
_RE_CODIGO_EMBEBIDO = re.compile(r"\b(?:python3?|perl|ruby|node)\s+-\w*c\w*\s+('[^']*'|\"(?:[^\"\\]|\\.)*\")")
_RE_SOLO_LEE = {
    "clean": re.compile(r"(?:^|\s)(?:-n|--dry-run)\b"),
    "rebase": re.compile(r"--show-current-patch|--edit-todo\b"),
}


def git_en_casa_base(cmd, subcomandos, *, incluir_worktrees, filtro=None, _dir=None, _hondo=0,
                     _subs=None, _hechas=None):
    """¿Algún `git <sub>` EJECUTADO en este comando cae en casa base? (`incluir_worktrees`: también
    en sus worktrees, que es lo que importa para el push.) El troceo del shell es el de
    `salida_guard.py` —una sola definición, no dos que diverjan—: un «git checkout» dentro de un
    echo, un heredoc o un mensaje no cuenta.

    SIGUE cómo se llega a casa base (pruebas adversariales del 22 y el 24-sep-26): `cd`, `pushd`,
    `popd`, `cd -`, subshell `( … )` con su ámbito, `$( )` y backticks, `sh -c`/`bash -c` (también
    `-lc`, `-ec`) y el CUERPO de un heredoc a bash, `GIT_DIR=`/`GIT_WORK_TREE=` (con comillas, tras
    `env`, con `env -i`), las opciones globales antes del subcomando (`-c k=v`, `--git-dir`,
    `--work-tree`, `--no-pager`), un `-C` a una subcarpeta, `$HOME`, y los wrappers y palabras de
    shell delante (`command`, `sudo`, `then`, `{`…). `Git` en mayúscula cuenta (el disco es
    insensible a mayúsculas).

    Desde el 24-sep-26 también: `env -C` y `env --chdir`, `export GIT_DIR=…;`, `GIT_DIR=` seguido
    de un wrapper o de una ruta absoluta, el CONTENIDO de `$( )` y de los backticks (aunque vayan
    entre comillas, y con el directorio vigente donde aparecen, no el del principio), `cd` con
    opciones (`-P`, `-L`, `--`), `if`/`while`/`until` delante, y los wrappers con sus opciones y
    argumentos, con una tabla de qué flag lleva valor en cada uno (`sudo -u root`, `nice -n 5`,
    `timeout -k 5 10`, `exec -a x`) en vez de comerse un argumento a ciegas.

    LO QUE NO SIGUE está en el docstring del MÓDULO, una sola vez: dos listas separadas acabarían
    diciendo cosas distintas, que es justo la mentira que este fichero no puede permitirse.
    `tests/test_casa_base_guard.py` fija las dos —lo cubierto y lo declarado sin cubrir—, así que
    si algún día lo declarado pasa a denegarse, será una decisión y no un accidente."""
    if "git" not in cmd.lower() and not (_subs and _RE_MARCA_SUB.search(cmd)):
        return False               # una marca heredada ya no dice «git», y su contenido sí puede
    try:
        import importlib.util
        ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "salida_guard.py")
        spec = importlib.util.spec_from_file_location("_salida_guard_ordenes", ruta)
        sg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sg)
        # `$( )`, backticks y `<( )`: su contenido sale del texto ANTES del troceo (si no, las
        # comillas de `echo "$(…)"` lo esconden) y deja una MARCA pegada en su sitio, para
        # analizarlo con el directorio que rija en esa orden. Dentro de comillas SIMPLES no se
        # sustituye nada: ahí bash no ejecuta, es texto.
        dentro = _subs if _subs is not None else []

        def _marca(m):
            if m.group(0).startswith("'"):
                return m.group(0)
            dentro.append(next((g for g in m.groups() if g is not None), ""))
            return _MARCA_SUB % (len(dentro) - 1)

        # El troceo quita las asignaciones del principio de cada orden, y con ellas se iría la marca
        # de `x=$(git stash)`. Se repite suelta al lado para que llegue con su directorio.
        texto = re.sub(r"(^|[\s;&|(])(\w+)=(['\"]?)(__BTPSUB\d+X__)\3",
                       r"\1\2=\3\4\3 \4", _RE_SUSTITUCION.sub(_marca, cmd))
        ordenes = sg._ordenes(texto)
    except Exception:
        # FAIL-OPEN de verdad (verificacion, 24-sep): el regex de emergencia ignoraba el cwd, así
        # que si `salida_guard.py` dejaba de importar, este hook —hoy el ÚNICO freno— denegaba
        # `git stash list` o `git checkout -b x` en CUALQUIER worktree, diciendo que era casa base.
        return False
    base = os.path.realpath(_casa_base())

    def en_casa_base(ruta):
        r = os.path.realpath(os.path.expandvars(ruta))
        if r == base:
            return True
        if not r.startswith(base + os.sep):
            return False
        if incluir_worktrees:
            return True
        wts = os.path.join(base, ".claude", "worktrees")
        if r.startswith(wts + os.sep):
            return False               # DENTRO de un worktree (exista o no en disco todavía)
        if r == wts:
            return True                # el directorio padre NO es un worktree: git resuelve a base
        # Un repo ANIDADO (`_cajita/publico`) tiene su propio `.git`: tampoco es casa base.
        d = r
        while d != base and d.startswith(base + os.sep):
            if os.path.exists(os.path.join(d, ".git")):
                return False
            d = os.path.dirname(d)
        return True

    def ruta_desde(dirs, destino):
        return os.path.join(dirs, os.path.expanduser(os.path.expandvars(destino)))

    dirs = _dir or (os.path.abspath(_CWD) if _CWD else os.getcwd())
    dirs0, hechas = dirs, (_hechas if _hechas is not None else set())

    def mira_marcas(textos, aqui):
        """Analiza el contenido de las sustituciones que aparezcan en estos textos, con `aqui` como
        directorio. Cada marca se mira una vez: `hechas` va compartido con las recursiones."""
        for t in textos:
            for m in _RE_MARCA_SUB.finditer(t or ""):
                i = int(m.group(1))
                if i in hechas or i >= len(dentro):
                    continue
                hechas.add(i)
                if dentro[i].strip() and _hondo < 3 and git_en_casa_base(
                        dentro[i], subcomandos, incluir_worktrees=incluir_worktrees, filtro=filtro,
                        _dir=aqui, _hondo=_hondo + 1, _subs=dentro, _hechas=hechas):
                    return True
        return False

    anterior, pila = dirs, []
    # `_ordenes` quita las asignaciones del principio («GIT_DIR=… git checkout»), y con ellas el
    # repo real: se miran en el texto crudo, sin heredocs ni código embebido entre comillas.
    crudo = _RE_CODIGO_EMBEBIDO.sub("''", _sin_heredocs(cmd))
    for m in re.finditer(r"\b(?:export\s+)?GIT_(DIR|WORK_TREE)=['\"]?([^\s'\";]+)['\"]?"
                         r"[\s;&]+(?:\w+=\S+\s+|export\s+\w+=\S+\s*[;&]*\s*)*"
                         r"(?:(?:command|exec|sudo|nohup|env|time|nice|timeout|caffeinate|builtin)"
                         r"\s+(?:-\S+\s+|\d+\w*\s+)*)*(?:\S*/)?git\s+"
                         r"((?:-\S+\s+(?:\S+\s+)?)*)(%s)\b([^;&|\n]*)"
                         % "|".join(map(re.escape, subcomandos)), crudo, re.I):
        valor = ruta_desde(dirs, m.group(2))
        repo = valor if m.group(1).upper() == "WORK_TREE" else os.path.dirname(valor.rstrip("/"))
        if en_casa_base(repo) and (filtro is None or filtro(m.group(4), m.group(5).split())):
            return True
    for pal, cuerpo in ordenes:
        # Los paréntesis del SUBSHELL se pelan; los que son del propio argumento, no. `strip("()")`
        # a secas le arrancaba el cierre a `bash -c '… $(git stash)'`, y el hijo recibía una
        # sustitución sin cerrar que ya no casaba con nada (verificacion, 24-sep-26).
        def _pela(t):
            t = t.lstrip("(")
            while t.endswith(")") and t.count(")") > t.count("("):
                t = t[:-1]
            return t

        abre = sum(1 for t in pal if t.startswith("(") and t.count("(") > t.count(")"))
        cierra = sum(1 for t in pal if t.endswith(")") and t.count(")") > t.count("("))
        pal = [t for t in (_pela(x) for x in pal) if t]
        if abre > cierra:                      # `(cd … ; git …)`: el cd no sale del subshell
            pila.append(dirs)
        aqui, delegada = dirs, False           # el `$( )` corre en el directorio de ESTA orden
        entorno = {}
        while pal and (pal[0] == "env" or ("=" in pal[0] and not pal[0].startswith("-"))
                       or (pila and pal[0].startswith("-"))):
            if pal[0] == "env":
                pal = pal[1:]
                while pal and pal[0].startswith("-"):    # `env -i`, `env -u VAR`, `env -C <dir>`
                    if pal[0] in ("-C", "--chdir") and len(pal) >= 2:
                        dirs = ruta_desde(dirs, pal[1])  # `env -C` es un cd con otro nombre
                        pal = pal[2:]
                        continue
                    if pal[0].startswith("--chdir="):
                        dirs = ruta_desde(dirs, pal[0].split("=", 1)[1])
                        pal = pal[1:]
                        continue
                    if pal[0] in ("-u",) and len(pal) >= 2:
                        pal = pal[2:]
                        continue
                    pal = pal[1:]
                continue
            if "=" in pal[0]:
                k, _, v = pal[0].partition("=")
                entorno[k.upper()] = ruta_desde(dirs, v.strip("'\""))
            pal = pal[1:]
        while pal and pal[0] in _ANTEPUESTOS:
            jefe = pal[0]
            con_valor = _FLAGS_CON_VALOR.get(jefe, frozenset())
            pal = pal[1:]
            chdir = _FLAGS_CHDIR.get(jefe, ())
            while pal and pal[0].startswith("-"):
                # `env -C <dir>` y `sudo -D <dir>` cambian el directorio de ESA orden: son un cd.
                if pal[0] in chdir and len(pal) >= 2:
                    dirs = aqui = ruta_desde(dirs, pal[1])
                    pal = pal[2:]
                    continue
                if chdir and pal[0].startswith("--chdir="):
                    dirs = aqui = ruta_desde(dirs, pal[0].split("=", 1)[1])
                    pal = pal[1:]
                    continue
                if pal[0] in con_valor and len(pal) >= 2:
                    pal = pal[2:]                       # `sudo -u root`, `nice -n 5`, `timeout -k 5`
                    continue
                pal = pal[1:]
            for _ in range(_POSICIONALES_DE_WRAPPER.get(jefe, 0)):
                # Nunca se come un `git`: si el wrapper viene mal escrito, mejor frenar de más.
                if (pal and not pal[0].startswith("-") and "=" not in pal[0]
                        and os.path.basename(pal[0]).lower() != "git"):
                    pal = pal[1:]
            while pal and "=" in pal[0] and not pal[0].startswith("-"):
                k, _, v = pal[0].partition("=")          # `env VAR=x git …`
                entorno[k.upper()] = ruta_desde(dirs, v.strip("'\""))
                pal = pal[1:]
        if not pal:
            if cierra > abre and pila:
                dirs = pila.pop()
            continue
        prog, args = os.path.basename(pal[0]).lower(), pal[1:]
        if prog in ("cd", "pushd"):
            while args and args[0].startswith("-"):      # `cd -P`, `cd -L`, `cd --`
                if args[0] == "-":
                    break                                # `cd -` es el destino, no una opción
                args = args[1:]
            destino = args[0] if args else "~"
            nuevo_dir = anterior if destino == "-" else ruta_desde(dirs, destino)
            if prog == "pushd":
                pila.append(dirs)
            anterior, dirs = dirs, nuevo_dir
        elif prog == "popd":
            if pila:
                anterior, dirs = dirs, pila.pop()
        elif prog in ("sh", "bash", "zsh") and _hondo < 3:
            trozos = [cuerpo] if cuerpo else []
            for k, a in enumerate(args):
                if a.startswith("-") and "c" in a.lstrip("-") and k + 1 < len(args):
                    trozos.append(args[k + 1])
                    break
            # La marca que vaya AHÍ dentro la resuelve el hijo, con el `cd` de dentro ya aplicado.
            delegada = bool(trozos)
            for t in trozos:
                if t and git_en_casa_base(t, subcomandos, incluir_worktrees=incluir_worktrees,
                                           filtro=filtro, _dir=dirs, _hondo=_hondo + 1,
                                           _subs=dentro, _hechas=hechas):
                    return True
        elif prog == "git":
            repo = entorno.get("GIT_WORK_TREE") or (os.path.dirname(entorno["GIT_DIR"].rstrip("/"))
                                                    if "GIT_DIR" in entorno else dirs)
            while args and args[0].startswith("-"):
                op = args[0]
                if op == "-C" and len(args) >= 2:
                    repo = ruta_desde(repo, args[1]); args = args[2:]
                elif op in ("-c", "--namespace") and len(args) >= 2:
                    args = args[2:]
                elif op in ("--git-dir", "--work-tree") and len(args) >= 2:
                    v = ruta_desde(dirs, args[1])
                    repo = v if op == "--work-tree" else os.path.dirname(v.rstrip("/"))
                    args = args[2:]
                elif op.startswith("--work-tree=") or op.startswith("--git-dir="):
                    v = ruta_desde(dirs, op.split("=", 1)[1])
                    repo = v if op.startswith("--work-tree=") else os.path.dirname(v.rstrip("/"))
                    args = args[1:]
                else:
                    args = args[1:]
            if args and args[0] in subcomandos and (filtro is None or filtro(args[0], args[1:])):
                if en_casa_base(repo):
                    return True
        if not delegada and mira_marcas(list(pal) + [cuerpo], aqui):
            return True
        if cierra > abre and pila:
            dirs = pila.pop()
    # Una marca que el troceo se comió (redirección, `VAR="…"`, heredoc) no puede quedar sin mirar:
    # se analiza con el directorio de arranque, que es lo que hacía el hook antes de las marcas. Sin
    # esto, `echo x > $(git stash)` o `x="$(git stash)"` en casa base se colaban (verificacion,
    # 24-sep-26, segunda ronda: el arreglo de la primera había abierto ocho de estos).
    if _subs is not None:
        return False                           # solo el nivel de arriba barre lo que quedó suelto
    return mira_marcas([_MARCA_SUB % i for i in range(len(dentro))], dirs0)


def mueve_casa_base(cmd, cwd=None):
    """¿Algún `git` de este comando mueve el árbol o el HEAD de casa base MISMA? Los worktrees y
    los repos anidados no cuentan: ahí `checkout` y `reset` son el trabajo normal."""
    if cwd is not None:
        fijar_cwd(cwd)
    return git_en_casa_base(cmd, MUEVEN_CASA_BASE, incluir_worktrees=False, filtro=mueve_de_verdad)


def push_desde_casa_base(cmd, cwd=None):
    """¿Algún `git push` sale de casa base o de uno de sus worktrees? (el repo lleva datos clínicos)"""
    if cwd is not None:
        fijar_cwd(cwd)
    return git_en_casa_base(cmd, ("push",), incluir_worktrees=True)
