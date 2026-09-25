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
  Deniega los `git` que mueven el árbol o el HEAD (checkout, switch, reset, restore, stash,
  rebase, clean, cherry-pick, revert, am, pull, bisect, apply, rm, mv, read-tree, checkout-index,
  symbolic-ref, update-ref) cuando el repositorio destino es casa base — siguiendo el camino real
  (`cd`, `pushd`, `-C`, `GIT_DIR=`, `sh -c`, heredoc, subshell: ver `_git_camino`) —, SALVO:
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
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _git_camino as camino  # noqa: E402

# Los subcomandos, las excepciones de solo-lectura y el «volver a master» viven en `_git_camino`.


def motivo_denegar(command, cwd):
    """Texto del motivo si hay que denegar; None si pasa. Nunca lanza (fail-open arriba).

    Quién decide es `_git_camino`, el analizador de shell compartido con la regla del push de
    `regla_en_accion.py` (24-sep-2026): antes había DOS frenos para esta misma clase, escritos por
    dos sesiones en paralelo, y ya discrepaban entre sí — este hook dejaba pasar «volver a master»
    y el otro lo denegaba. Ahora el camino hasta el repo se calcula en un solo sitio y aquí se
    queda la POLÍTICA: qué se perdona y cómo se explica."""
    camino.fijar_cwd(cwd or os.getcwd())
    if not camino.mueve_casa_base(command):
        return None
    return ("este `git` mueve el árbol vivo de casa base (%s), que es lo que ejecutan los daemons, "
            "y pisa lo que fusionen otras sesiones. Para leer historia: `git show <commit>:<ruta>`, "
            "`git diff A B -- <ruta>`, `git log`. Para trabajar: un worktree. Volver a master sí "
            "está permitido (es la reparación). Si de verdad hace falta otra cosa (acto humano), "
            "BTP_CASA_BASE_OK=1. Norma: feedback-nunca-checkout-en-casa-base." % camino._casa_base())


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
        # Dos nombres de escape: el suyo y el que usaba la regla que esto sustituye, para no
        # romper lo que cada sesión ya tenga escrito.
        if data.get("tool_name") != "Bash" or "1" in (os.environ.get("BTP_CASA_BASE_OK"),
                                                      os.environ.get("BTP_ALLOW_CASA_BASE")):
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
    # Watchdog (25-sep-26): si tardo más que el timeout de settings (5 s), deniego yo antes de
    # que Claude Code me cancele y lo convierta en un permitir. Ver _watchdog.py.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:  # sin el vigilante el guard sigue siendo el guard: su falta no lo tumba
        import _watchdog
    except ImportError:
        _watchdog = None
        sys.stderr.write("casa_base_guard: falta _watchdog.py; sigo sin vigilante\n")
    if _watchdog:
        _watchdog.armar(5, 'casa_base_guard')
    sys.exit(main())
