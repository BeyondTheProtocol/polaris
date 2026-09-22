#!/usr/bin/env python3
"""test_muro_a1_sandbox_escalada.py — regresión del FIX 2 de A1 (10-jul-26, hallazgo A):
un `Bash` con `dangerouslyDisableSandbox: true` NUNCA se sirve para una mutación de git de
riesgo (branch -d/-D, worktree prune/remove, push, reset, clean, rm), pase lo que pase con el
resto del análisis del muro — ni siquiera cuando la MISMA operación SIN el flag de escalada sí
está permitida (p.ej. `git branch -d` normal es fail-safe y está en la allowlist).

Origen: un transcript real del barrido de git (30-jun-26, tools/launchd/logs/git-barrido.out)
mostró la secuencia "Bash denegado -> mismo comando reintentado con dangerouslyDisableSandbox:
true" — "denegado -> reintentar quitando el candado" en vez de aplazar/preguntar.

Invoca el guard REAL (muro_guard.py) por stdin, igual que test_muro_fase0.py.

Item #27 (saneamiento 15-jul): este test dependia de la rama REAL del repo desde el que se
ejecutaba. El caso "git commit ESCALADO (no es mutante de riesgo)" no toca la lista de riesgo
de A1, pero SI puede chocar con el gate base independiente (fix c, 11-jul-26,
test_muro_base_gate.py): con el toggle `.claude/hooks/.base_gate_on` activo (commiteado y
VIVO en master), `commit` esta en GIT_BASE_GATE_SUBS y el guard llama a `_rama_actual()`, que
hace un `git rev-parse --abbrev-ref HEAD` REAL sobre `_GIT_REPO_FOR_BRANCH` (por defecto, el
propio repo del test). Si ese repo real estaba en master/main -> DENY inesperado (toca_base=
True); si estaba en una rama de trabajo -> ALLOW. Mismo comando, mismo test, resultado
distinto segun desde donde se lanzara -- justo lo que se vio como "ya fallaba antes de la
auditoria (git commit ESCALADO)".

Fix: aislar el test del repo real con BTP_GIT_REPO_OVERRIDE, mismo patron que
test_muro_base_gate.py -- un repo temporal (tempfile) en una rama de TRABAJO fija, nunca
master/main, para que toca_base sea SIEMPRE False aqui (este test verifica A1, no el gate
base; ese ya tiene su propia bateria dedicada).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(ROOT, ".claude", "hooks", "muro_guard.py")

_TMP = tempfile.mkdtemp(prefix="test_a1_sandbox_")
_REPO_TRABAJO = os.path.join(_TMP, "repo_trabajo")


def _git(repo, *args):
    subprocess.run(["git"] + list(args), cwd=repo, check=True, capture_output=True)


def _mk_repo(path, branch):
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "-q", "-b", branch)
    _git(path, "-c", "user.email=t@t.com", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "init")


ENV = dict(os.environ, MURO_PROFILE="privileged",
           BTP_HALT_FILES="/tmp/__nh_a1_sandbox_a:/tmp/__nh_a1_sandbox_b",
           BTP_GIT_REPO_OVERRIDE=_REPO_TRABAJO)
_pass = 0
_fail = 0


def _rc(payload):
    p = subprocess.run([sys.executable, GUARD], input=json.dumps(payload).encode(),
                       capture_output=True, env=ENV)
    return p.returncode


def bash(cmd, escalado=False):
    ti = {"command": cmd}
    if escalado:
        ti["dangerouslyDisableSandbox"] = True
    return {"tool_name": "Bash", "tool_input": ti}


def deny(name, payload):
    global _pass, _fail
    if _rc(payload) == 2:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ esperaba DENY: %s" % name)


def allow(name, payload):
    global _pass, _fail
    if _rc(payload) == 0:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ esperaba ALLOW: %s" % name)


def main():
    _mk_repo(_REPO_TRABAJO, "fix/test-a1-escalada")
    # --- El caso REAL del transcript: git branch -d ESCALADO -> DENY (aunque -d normal ALLOW) ---
    deny("branch -d ESCALADO (el caso del 30-jun)",
         bash("git branch -d worktree-agent-a33fa366e142be967", escalado=True))
    deny("branch -D ESCALADO", bash("git branch -D alguna-rama", escalado=True))
    deny("branch --delete ESCALADO", bash("git branch --delete alguna-rama", escalado=True))

    # --- Otras mutaciones de la lista de riesgo, ESCALADAS -> DENY ---
    deny("worktree prune ESCALADO", bash("git worktree prune", escalado=True))
    deny("worktree remove ESCALADO", bash("git worktree remove /tmp/x", escalado=True))
    deny("push ESCALADO", bash("git push origin main", escalado=True))
    deny("reset ESCALADO", bash("git reset --hard HEAD~1", escalado=True))
    deny("clean ESCALADO", bash("git clean -fdx", escalado=True))
    deny("rm ESCALADO", bash("git rm archivo.txt", escalado=True))

    # --- Control: la MISMA operación SIN escalada mantiene su comportamiento de SIEMPRE ---
    # branch -d SIN escalada ya era ALLOW (fail-safe, rechaza lo no fusionado) — el fix NO debe
    # sobre-bloquear el camino normal, solo la escalada tras un denegado.
    allow("branch -d SIN escalada (comportamiento normal intacto)",
          bash("git branch -d worktree-agent-a33fa366e142be967"))
    # worktree/push/reset/clean/rm SIN escalada YA estaban denegados por la allowlist normal
    # (no están en GIT_ALLOW_SUB) — siguen denegados, pero por la razón de SIEMPRE, no por A1.
    deny("worktree prune SIN escalada (ya denegado por la allowlist normal)",
         bash("git worktree prune"))
    deny("push SIN escalada (ya denegado por la allowlist normal)",
         bash("git push origin main"))

    # --- No sobre-bloquear: escalada en un comando NO listado como mutante de riesgo ---
    allow("git status ESCALADO (no es mutación de riesgo)", bash("git status", escalado=True))
    allow("git branch SIN -d/-D ESCALADO (solo listar)", bash("git branch", escalado=True))
    allow("git branch --list ESCALADO (solo listar)", bash("git branch --list", escalado=True))
    allow("ls ESCALADO (ni siquiera es git)", bash("ls -la", escalado=True))
    allow("git commit ESCALADO (no es mutante de riesgo)",
          bash("git commit -m mensaje", escalado=True))

    print("RESULTADO muro A1 sandbox-escalada (fix 2): %d OK, %d fallos" % (_pass, _fail))
    print("✅ EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
