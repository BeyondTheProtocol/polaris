#!/usr/bin/env python3
"""_git_camino.py — a qué repo llega de verdad un `git` escrito en el shell.

POR QUÉ (24-sep-2026). Dos hooks necesitaban la MISMA respuesta: `casa_base_guard.py` («¿este git
mueve el árbol vivo de casa base?») y la regla `push-repo-clinico` de `regla_en_accion.py` («¿este
push sale de casa base o de un worktree suyo?»). Ese día se descubrió que las dos sesiones habían
escrito el freno por separado y ya discrepaban entre ellas: el analizador vive aquí, una sola vez.

Sigue `cd`, `pushd`, `popd`, `cd -`, el ámbito de un subshell `( … )`, `$( )` y backticks,
`sh -c`/`bash -c` (también `-lc`) y el cuerpo de un heredoc, `GIT_DIR=`/`GIT_WORK_TREE=` (con
comillas, tras `env`, con `env -i`), las opciones globales antes del subcomando, `-C`, `$HOME` y
los wrappers delante (`command`, `sudo`, `then`, `{`…) y cualquier variable de entorno que el
propio hook tenga (`expandvars`, no solo `$HOME`).

NO sigue, y hay deuda abierta por ello (`camino-git-huecos-heredados`): `xargs git`, alias
(`git -c alias.co=checkout co`), `subprocess` desde `python3 -c`, un script en fichero o por
stdin, `env -C`, `GIT_DIR=` seguido de `command`/una ruta absoluta/un salto de línea,
`export GIT_DIR=…;`, `$( )` dentro de comillas dobles, `cd` con opciones (`-P`, `--`), wrappers
con opciones (`sudo -E`, `nice -n 5`, `timeout 10`) y `if git … ; then`. El shell arbitrario no
se cubre entero: decirlo a medias sería peor que no cubrirlo.

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
_ANTEPUESTOS = frozenset({"command", "exec", "builtin", "sudo", "nohup", "time", "nice", "caffeinate",
                          "then", "do", "else", "elif", "{", "!", "eval"})
# Lo que abre otro contexto donde puede haber un `git` que sí se ejecuta.
_RE_SUBSHELL = re.compile(r"\$\(|`")
# `python3 -c "…"` es TEXTO, no shell: el repaso crudo de GIT_DIR no puede leer dentro.
_RE_CODIGO_EMBEBIDO = re.compile(r"\b(?:python3?|perl|ruby|node)\s+-\w*c\w*\s+('[^']*'|\"(?:[^\"\\]|\\.)*\")")
_RE_SOLO_LEE = {
    "clean": re.compile(r"(?:^|\s)(?:-n|--dry-run)\b"),
    "rebase": re.compile(r"--show-current-patch|--edit-todo\b"),
}


def git_en_casa_base(cmd, subcomandos, *, incluir_worktrees, filtro=None, _dir=None, _hondo=0):
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

    NO SIGUE, y se declara: `xargs git`, alias, `python3 -c "subprocess…"`, un script en fichero
    (`bash s.sh`) o por stdin (`echo … | bash`), y las variables que no sean `$HOME` (`$s` como
    subcomando). El shell arbitrario no se cubre entero."""
    if "git" not in cmd.lower():
        return False
    try:
        import importlib.util
        ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "salida_guard.py")
        spec = importlib.util.spec_from_file_location("_salida_guard_ordenes", ruta)
        sg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sg)
        # `$(…)` y los backticks abren una orden nueva que el troceo no separa: se vuelven `;`.
        ordenes = sg._ordenes(_RE_SUBSHELL.sub(";", cmd))
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
    anterior, pila = dirs, []
    # `_ordenes` quita las asignaciones del principio («GIT_DIR=… git checkout»), y con ellas el
    # repo real: se miran en el texto crudo, sin heredocs ni código embebido entre comillas.
    crudo = _RE_CODIGO_EMBEBIDO.sub("''", _sin_heredocs(cmd))
    for m in re.finditer(r"\bGIT_(DIR|WORK_TREE)=['\"]?([^\s'\";]+)['\"]?\s+(?:\w+=\S+\s+)*git\s+"
                         r"((?:-\S+\s+(?:\S+\s+)?)*)(%s)\b([^;&|\n]*)"
                         % "|".join(map(re.escape, subcomandos)), crudo, re.I):
        valor = ruta_desde(dirs, m.group(2))
        repo = valor if m.group(1).upper() == "WORK_TREE" else os.path.dirname(valor.rstrip("/"))
        if en_casa_base(repo) and (filtro is None or filtro(m.group(4), m.group(5).split())):
            return True
    for pal, cuerpo in ordenes:
        abre = sum(t.startswith("(") for t in pal)
        cierra = sum(t.endswith(")") for t in pal)
        pal = [t for t in (x.strip("()") for x in pal) if t]
        if abre > cierra:                      # `(cd … ; git …)`: el cd no sale del subshell
            pila.append(dirs)
        entorno = {}
        while pal and (pal[0] == "env" or ("=" in pal[0] and not pal[0].startswith("-"))
                       or (pila and pal[0].startswith("-"))):
            if pal[0] == "env":
                pal = pal[1:]
                while pal and pal[0].startswith("-"):    # `env -i`, `env -u VAR`
                    pal = pal[1:]
                continue
            if "=" in pal[0]:
                k, _, v = pal[0].partition("=")
                entorno[k.upper()] = ruta_desde(dirs, v.strip("'\""))
            pal = pal[1:]
        while pal and pal[0] in _ANTEPUESTOS:
            pal = pal[1:]
        if not pal:
            if cierra > abre and pila:
                dirs = pila.pop()
            continue
        prog, args = os.path.basename(pal[0]).lower(), pal[1:]
        if prog in ("cd", "pushd"):
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
            for t in trozos:
                if t and git_en_casa_base(t, subcomandos, incluir_worktrees=incluir_worktrees,
                                           filtro=filtro, _dir=dirs, _hondo=_hondo + 1):
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
        if cierra > abre and pila:
            dirs = pila.pop()
    return False


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
