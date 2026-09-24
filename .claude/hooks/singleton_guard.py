#!/usr/bin/env python3
"""singleton_guard.py — las 3 operaciones que NO admiten dos sesiones a la vez van por su puerta.

NORMA (registro: tools/normas.json) `feedback-sesion-paralela-seguridad-primero`, clase BLOQUEO
y `repetida: true`: «sesión paralela = NORMAL (forma de trabajar de {{TITULAR}}): aíslate en tu propia
rama/worktree y SIGUE; solo paras/serializas en los 3 singletons (activar launchd, fusionar a
base, control de pantalla)».

LA MITAD QUE NO SE MECANIZA, DICHA PRIMERO: «aíslate y SIGUE» es criterio, no bloqueo — ningún
hook puede obligarme a no pararme a preguntar. Lo que sí se mecaniza es la otra mitad: que las
operaciones que de verdad se pisan pasen por el candado en vez de por un Bash suelto.

EL HUECO ERA CONOCIDO Y ESTABA DOCUMENTADO. `tools/git_mutex.py` (10-jul-26, hallazgo B del
comité de arquitectura) dice en su propia cabecera: «el agente git/git-barrido invoca git
DIRECTAMENTE por Bash … este módulo no lo intercepta … queda como riesgo residual documentado».
Lo mismo con `tools/activar_daemon.py`, que se declara «ÚNICA vía sancionada» para encender un
daemon y toma `_lock.lock('launchd')`. Las dos puertas con candado existen desde julio; lo que
faltaba era que alguien las usara. Medido sobre los transcripts reales (18-sep-26), contando con
el clasificador de ESTE fichero y no con un grep: ver la nota de la norma en tools/normas.json.

Un hook NO puede sostener un candado entre tool-calls (lo dice `tools/_lock.py` en su cabecera:
la serialización a nivel de agente se hace con un SCRIPT que toma el candado mientras muta). Así
que esto no cierra ningún candado: ENRUTA. Deniega la forma cruda y nombra la puerta que sí lo
toma. Es un cambio de vía, no un muro.

QUÉ ENTRA (y por qué exactamente eso):
  S1 · launchd — `launchctl` en sus formas MUTANTES (load/unload/bootstrap/bootout/enable/
       disable/kickstart/remove/submit) → `tools/activar_daemon.py`. Las de lectura
       (list/print/blame/dumpstate/procinfo) no se tocan: son media herramienta de diagnóstico.
  S2 · fusionar a base — exactamente las mutaciones que `git_mutex.py` documenta como suyas:
       `merge`, `worktree remove|prune`, `branch -d|-D`. NO entra `commit` (sería insoportable) y
       NO entra `push` (no muta el .git compartido; además el muro ya lo cierra en el lazo).
  S3 · `git add` en bloque (`-A`, `--all`, `-u`, `.`) SIN rutas explícitas — es como el trabajo
       de otra sesión acaba dentro de tu commit. Con rutas detrás, pasa: `git add -A tests/x.py`
       es explícito y es lo que hace el trabajo real.

QUÉ NO ENTRA: el tercer singleton, **control de pantalla**. No tiene puerta con candado que
nombrar (no hay un `tools/pantalla.py`), así que enrutar no es posible todavía y no me invento
una. Queda nombrado aquí y en la nota de la norma como lo que falta.

ÁMBITO: solo el repo de casa base y sus worktrees (comparten el mismo `.git`, que es el recurso
que se pisa). Un `git merge` en ~/projects/titular-{{APELLIDO}}-case no es este singleton y pasa.

FAIL-OPEN, como worktree_guard y por la misma razón: esto atrapa un despiste en sesión
interactiva; si el guard revienta, dejarla sin poder tocar git sería peor que el despiste.
Escotilla explícita: `BTP_SINGLETON_OK=1`.

Contrato de hooks (code.claude.com/docs/hooks): exit 0 permite · exit 2 deniega.
"""
import json
import os
import shlex
import sys

LAUNCHCTL_MUTA = {"load", "unload", "bootstrap", "bootout", "enable", "disable",
                  "kickstart", "remove", "submit", "setenv", "unsetenv"}
GIT_MUTEX_SUBS = {"merge", "worktree", "branch"}
ADD_EN_BLOQUE = {"-A", "--all", "-u", "--update", "."}
_PUNT = set("();<>|&\n")


def subcomandos(command):
    """Tokeniza como bash y parte por separadores. Mismo enfoque que muro_guard: por TOKEN.

    Importa que sea por token y no por subcadena: `git merge-base --is-ancestor` NO es un merge
    y `git add -A tests/x.py` SÍ es explícito. Un grep los cuenta como infracciones (verificado:
    el primer barrido de esta norma salió inflado justo por eso).
    """
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n")
        lex.whitespace = " \t\r"
        lex.commenters = "#"
        lex.whitespace_split = True
        crudos = list(lex)
    except ValueError:
        return []
    subs, cur = [], []
    for t in crudos:
        if t and set(t) <= _PUNT:
            if cur:
                subs.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        subs.append(cur)
    return subs


def _git_sub(args):
    """El subcomando real de git, saltando las opciones globales (`-C <dir>`, `--no-pager`…)."""
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-C", "--git-dir", "--work-tree", "--namespace", "-c"):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        return a, args[i + 1:]
    return None, []


def clasifica(sub):
    """None si pasa; si no, (singleton, qué es, por dónde va)."""
    if not sub:
        return None
    binario = os.path.basename(sub[0])
    args = sub[1:]

    if binario == "launchctl":
        sub1 = next((a for a in args if not a.startswith("-")), None)
        if sub1 in LAUNCHCTL_MUTA:
            return ("S1 launchd", "encender/apagar/recargar un daemon",
                    "python3 tools/activar_daemon.py <plist|label> [--reemplaza] [--kick]")
        return None

    if binario != "git":
        return None
    sg, resto = _git_sub(args)
    if sg not in GIT_MUTEX_SUBS and sg != "add":
        return None

    if sg == "merge":
        return ("S2 fusionar a base", "un merge muta las refs del .git compartido",
                "python3 tools/git_mutex.py -C <repo> merge --no-ff <rama> -m '...'")
    if sg == "worktree":
        acc = next((a for a in resto if not a.startswith("-")), None)
        if acc in ("remove", "prune"):
            return ("S2 fusionar a base", "podar worktrees muta .git/worktrees/ compartido",
                    "python3 tools/git_mutex.py -C <repo> worktree %s <...>" % acc)
        return None
    if sg == "branch":
        if any(a in ("-d", "-D", "--delete") for a in resto):
            return ("S2 fusionar a base", "borrar una rama muta las refs compartidas",
                    "python3 tools/git_mutex.py -C <repo> branch -d <rama>")
        return None
    if sg == "add":
        en_bloque = [a for a in resto if a in ADD_EN_BLOQUE]
        rutas = [a for a in resto if not a.startswith("-") and a not in ADD_EN_BLOQUE]
        if en_bloque and not rutas:
            return ("S3 git add en bloque",
                    "`git add %s` se lleva lo que otra sesión dejó en el árbol" % en_bloque[0],
                    "git add <rutas explícitas>")
        return None
    return None


def _repo_de_casa(cwd, casa):
    """¿El cwd cuelga de casa base (o de uno de sus worktrees, que comparten el mismo .git)?"""
    if not cwd or not casa:
        return False
    cwd = os.path.abspath(os.path.expanduser(cwd))
    return cwd == casa or cwd.startswith(casa + os.sep)


def _casa_base():
    ov = os.environ.get("BTP_WT_RAIZ_OVERRIDE")
    raiz = os.path.abspath(ov) if ov else os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    marca = os.sep + os.path.join(".claude", "worktrees") + os.sep
    return raiz.split(marca)[0]


def main():
    data = json.loads(sys.stdin.read())
    if data.get("tool_name") != "Bash":
        return 0
    if os.environ.get("BTP_SINGLETON_OK") == "1":
        return 0
    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command:
        return 0
    casa = _casa_base()
    aqui = data.get("cwd")
    # El `cd` de un subcomando manda sobre los siguientes, y va en LOS DOS SENTIDOS: sin esto,
    # `cd ~/projects/titular-{{APELLIDO}}-case && git merge x` desde una sesión de claudecode se
    # denegaría sin ser este singleton, y `cd ~/claudecode && git merge x` desde la sesión de la
    # web se colaría — que es justo la forma de tocar casa base desde donde no toca.
    for sub in subcomandos(command):
        if os.path.basename(sub[0]) == "cd":
            destino_cd = next((a for a in sub[1:] if not a.startswith("-")), None)
            if destino_cd and "$" not in destino_cd:
                d = os.path.expanduser(destino_cd)
                aqui = d if os.path.isabs(d) else os.path.join(aqui or "", d)
            continue
        v = clasifica(sub)
        if not v:
            continue
        destino = aqui
        if os.path.basename(sub[0]) == "git" and "-C" in sub:
            try:
                destino = sub[sub.index("-C") + 1]
            except IndexError:
                destino = aqui
        if not _repo_de_casa(destino, casa):
            continue                      # otro repo (p.ej. la web): no es este singleton
        singleton, que, puerta = v
        sys.stderr.write(
            "SINGLETON ⛔ %s — %s, y varias sesiones trabajan en paralelo.\n"
            "   esa operación va por la puerta que toma el candado:\n"
            "     %s\n"
            "   Si de verdad quieres la forma cruda: BTP_SINGLETON_OK=1.\n"
            % (singleton, que, puerta))
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)   # FAIL-OPEN deliberado (ver cabecera)
