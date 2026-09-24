#!/usr/bin/env python3
"""casa_base_guard.py — PreToolUse(Bash): nadie mueve el árbol vivo de casa base «para mirar».

POR QUÉ (22-sep-2026, deuda `checkout-en-casa-base-sin-freno`). Casa base (~/claudecode) es el
sistema vivo 24/7 y solo recibe fusiones. Ese día, dos veces:
  · 15:11, una sesión hizo `git checkout <commit> --` en casa base para comparar historia: HEAD
    desacoplado ~40 s y `tools/run_agent.sh` (el script de todos los agentes) volvió al árbol vivo
    con marcadores de conflicto;
  · otra sesión hizo lo mismo en su worktree (sin daño, pero la misma clase).
`muro_guard.py` acota `checkout` solo en el lazo AUTÓNOMO; en una sesión interactiva no había
freno. La norma (`feedback-nunca-checkout-en-casa-base`) dice: la historia se lee con
`git show/diff/log`, nunca con checkout/switch/reset/restore/stash en casa base.

QUÉ HACE
  Deniega `git checkout|switch|reset|restore|stash` cuando el repositorio destino es casa base
  (el cwd, un `cd` del mismo comando o `git -C <dir>`), SALVO:
    · volver a master (`git checkout master` / `git switch master`, con o sin -q): es la reparación;
    · `BTP_CASA_BASE_OK=1` en el entorno (acto humano explícito).
  Los worktrees (`~/claudecode/.claude/worktrees/*`) son árboles aparte: ahí no aplica.
  Tampoco en el lazo 24/7 (MURO_PROFILE puesto): allí manda muro_guard.py, y la auto-mejora crea
  su rama en casa base por diseño.

QUÉ NO HACE
  No es el muro: si no puede interpretar el comando, DEJA PASAR (fail-open). Un fallo suyo no debe
  bloquear todo Bash en sesiones interactivas; lo que protege aquí es un error honesto, no un
  atacante (eso lo cubren muro_guard/clinico_guard).
"""
import json
import os
import shlex
import sys

CASA = os.path.realpath(os.environ.get("BTP_CASA_BASE") or os.path.expanduser("~/claudecode"))
WORKTREES = os.path.join(CASA, ".claude", "worktrees")
PROHIBIDOS = {"checkout", "switch", "reset", "restore", "stash"}
RAMAS_BASE = {"master", "main"}


def _en_casa_base(ruta):
    """¿Esta ruta está dentro del árbol de casa base (y NO en uno de sus worktrees)?"""
    try:
        r = os.path.realpath(os.path.expanduser(ruta))
    except Exception:
        return False
    if r == WORKTREES or r.startswith(WORKTREES + os.sep):
        return False
    return r == CASA or r.startswith(CASA + os.sep)


def _trozos(command):
    """Cada orden simple del comando, como lista de tokens (separa por ; && || | & ( ) y saltos)."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    actual = []
    for tok in lex:
        if tok and set(tok) <= set(";&|()\n"):      # separador (o varios pegados)
            if actual:
                yield actual
            actual = []
        else:
            actual.append(tok)
    if actual:
        yield actual


def motivo_denegar(command, cwd):
    """Texto del motivo si hay que denegar; None si pasa. Nunca lanza (fail-open arriba)."""
    dir_actual = cwd or os.getcwd()
    for sub in _trozos(command):
        # Prefijos tipo `VAR=x git ...` / `env git ...` / `command git ...`.
        while sub and ("=" in sub[0] and not sub[0].startswith("-") or sub[0] in ("env", "command", "time")):
            sub = sub[1:]
        if not sub:
            continue
        if sub[0] == "cd":
            if len(sub) > 1 and not any(c in sub[1] for c in "$`*?"):
                destino = os.path.expanduser(sub[1])
                dir_actual = destino if os.path.isabs(destino) else os.path.join(dir_actual, destino)
            continue
        if os.path.basename(sub[0]) != "git":
            continue
        args, repo = sub[1:], dir_actual
        while args and args[0] in ("-C", "--git-dir", "--work-tree") and len(args) > 1:
            d = os.path.expanduser(args[1])
            if args[0] == "-C":
                repo = d if os.path.isabs(d) else os.path.join(repo, d)
            elif args[0] == "--work-tree":
                repo = d if os.path.isabs(d) else os.path.join(repo, d)
            args = args[2:]
        gsub = next((a for a in args if not a.startswith("-")), None)
        if gsub not in PROHIBIDOS or not _en_casa_base(repo):
            continue
        resto = [a for a in args[args.index(gsub) + 1:] if not a.startswith("-")]
        if gsub == "stash" and resto[:1] in (["list"], ["show"]):
            continue                                  # solo leen
        if gsub in ("checkout", "switch") and len(resto) == 1 and resto[0] in RAMAS_BASE \
                and "--" not in args:
            continue                                  # volver a master = la reparación
        return ("`git %s` sobre casa base (%s) mueve el árbol vivo 24/7 que ejecutan los daemons. "
                "Para leer historia: `git show <commit>:<ruta>`, `git diff A B -- <ruta>`, `git log`. "
                "Para trabajar: un worktree. Si de verdad hace falta (acto humano), "
                "BTP_CASA_BASE_OK=1. Norma: feedback-nunca-checkout-en-casa-base."
                % (gsub, CASA))
    return None


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
        if data.get("tool_name") != "Bash" or os.environ.get("BTP_CASA_BASE_OK") == "1":
            return 0
        # El lazo 24/7 (run_agent.sh exporta MURO_PROFILE) tiene su propio guard, muro_guard.py,
        # que ya acota checkout; y la auto-mejora crea su rama en casa base POR DISEÑO. Este hook
        # es para las sesiones interactivas, que no tenían ningún freno.
        if os.environ.get("MURO_PROFILE"):
            return 0
        command = (data.get("tool_input") or {}).get("command") or ""
        motivo = motivo_denegar(command, data.get("cwd"))
    except Exception:
        return 0                                          # fail-open: ver docstring
    if motivo:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "CASA BASE ⛔ " + motivo}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
