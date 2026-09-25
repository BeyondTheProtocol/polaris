#!/usr/bin/env python3
"""test_muro_base_gate.py — regresión del fix (c) (11-jul-26,
_cajita/radar-mejoras-10jul/C-guard-merge-master.md): gate BLANDO de escritura a la BASE
(master/main) para agentes autónomos.

Origen: una sesión con permisos escalados fusionó ramas y commiteó a master anoche (10-jul)
sin gate humano. La regla nueva vive en check_subcommand() del muro real (mismo sitio que el
Fix 2 de A1): si el subcomando git es merge/commit/push Y toca la base (push SIEMPRE; merge/
commit solo si la rama ACTUAL es master/main) Y no hay BTP_GIT_BASE_OK=1 -> DENY.

TOGGLE apagado por defecto: la regla solo actúa si existe `.claude/hooks/.base_gate_on`. Este
test usa BTP_BASE_GATE_TOGGLE para apuntar a un fichero de scratch (ON) o a una ruta que no
existe (OFF) -- el fichero real NUNCA se toca ni se crea aquí.

NOTA IMPORTANTE (para no sobre-prometer): `merge` y `push` YA están fuera de GIT_ALLOW_SUB
hoy -- se deniegan SIEMPRE, con o sin este toggle ("push ya cubierto por el muro", según el
propio diseño). El único subcomando cuyo comportamiento OBSERVABLE cambia con este fix es
`commit` (que sí está en la allowlist). Por eso los casos (a)-(d) del encargo se verifican
sobre todo con `commit`; para merge/push se confirma que siguen SIEMPRE denegados (no se
ABREN de más por error), documentado explícitamente en cada caso.

Invoca el guard REAL (muro_guard.py) por stdin, igual que test_muro_fase0.py /
test_muro_a1_sandbox_escalada.py. CERO git real sobre ~/claudecode: los repos de prueba son
temporales (tempfile), aislados con BTP_GIT_REPO_OVERRIDE (mismo patrón que BTP_HALT_FILES).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(ROOT, ".claude", "hooks", "muro_guard.py")

_TMP = tempfile.mkdtemp(prefix="test_base_gate_")
TOGGLE_ON = os.path.join(_TMP, "toggle_on")
TOGGLE_OFF = os.path.join(_TMP, "toggle_off_no_existe")  # a propósito: nunca se crea
REPO_MASTER = os.path.join(_TMP, "repo_master")
REPO_MAIN = os.path.join(_TMP, "repo_main")
REPO_FEATURE = os.path.join(_TMP, "repo_feature")

_pass = 0
_fail = 0


def _git(repo, *args):
    subprocess.run(["git"] + list(args), cwd=repo, check=True,
                   capture_output=True)


def _mk_repo(path, branch):
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "-q", "-b", branch)
    _git(path, "-c", "user.email=t@t.com", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "init")


def _setup():
    with open(TOGGLE_ON, "w") as f:
        f.write("on")
    _mk_repo(REPO_MASTER, "master")
    _mk_repo(REPO_MAIN, "main")
    _mk_repo(REPO_FEATURE, "feat/mi-rama")


def _rc(cmd, repo, toggle, gate_ok=None):
    env = dict(os.environ, MURO_PROFILE="privileged",
               BTP_HALT_FILES="/tmp/__nh_basegate_a:/tmp/__nh_basegate_b",
               BTP_BASE_GATE_TOGGLE=toggle,
               BTP_GIT_REPO_OVERRIDE=repo)
    if gate_ok is not None:
        env["BTP_GIT_BASE_OK"] = gate_ok
    else:
        env.pop("BTP_GIT_BASE_OK", None)
    payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
    p = subprocess.run([sys.executable, GUARD], input=json.dumps(payload).encode(),
                       capture_output=True, env=env)
    return p.returncode


def deny(name, rc):
    global _pass, _fail
    if rc == 2:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ esperaba DENY: %s (rc=%s)" % (name, rc))


def allow(name, rc):
    global _pass, _fail
    if rc == 0:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ esperaba ALLOW: %s (rc=%s)" % (name, rc))


def main():
    _setup()
    try:
        # ------------------------------------------------------------------------------
        # (a) toggle OFF -> commit a master PERMITIDO (inerte, comportamiento de siempre).
        #     merge/push siguen DENEGADOS -- pero por la razón de SIEMPRE (no están en
        #     GIT_ALLOW_SUB), no por este gate; el toggle OFF no cambia nada de eso.
        # ------------------------------------------------------------------------------
        allow("(a) toggle OFF: commit en master ALLOW (inerte)",
              _rc("git commit -m x", REPO_MASTER, TOGGLE_OFF))
        allow("(a) toggle OFF: commit en main ALLOW (inerte)",
              _rc("git commit -m x", REPO_MAIN, TOGGLE_OFF))
        deny("(a) toggle OFF: merge en master DENY (pre-existente, no es este gate)",
             _rc("git merge feat/x", REPO_MASTER, TOGGLE_OFF))
        deny("(a) toggle OFF: push DENY (pre-existente, no es este gate)",
             _rc("git push origin master", REPO_MASTER, TOGGLE_OFF))

        # ------------------------------------------------------------------------------
        # (b) toggle ON + SIN BTP_GIT_BASE_OK -> commit en master/main y push DENEGADOS.
        # ------------------------------------------------------------------------------
        deny("(b) toggle ON sin gate: commit en master DENY",
             _rc("git commit -m x", REPO_MASTER, TOGGLE_ON))
        deny("(b) toggle ON sin gate: commit en main DENY",
             _rc("git commit -m x", REPO_MAIN, TOGGLE_ON))
        deny("(b) toggle ON sin gate: push DENY",
             _rc("git push origin master", REPO_MASTER, TOGGLE_ON))

        # ------------------------------------------------------------------------------
        # (c) toggle ON + BTP_GIT_BASE_OK=1 -> commit en master/main PERMITIDO.
        #     push SIGUE denegado (razón pre-existente: no está en GIT_ALLOW_SUB; el gate
        #     no puede "abrir" un subcomando que la allowlist general nunca sirvió).
        # ------------------------------------------------------------------------------
        allow("(c) toggle ON + gate=1: commit en master ALLOW",
              _rc("git commit -m x", REPO_MASTER, TOGGLE_ON, gate_ok="1"))
        allow("(c) toggle ON + gate=1: commit en main ALLOW",
              _rc("git commit -m x", REPO_MAIN, TOGGLE_ON, gate_ok="1"))
        deny("(c) toggle ON + gate=1: push DENY (allowlist previa, no este gate)",
             _rc("git push origin master", REPO_MASTER, TOGGLE_ON, gate_ok="1"))

        # ------------------------------------------------------------------------------
        # (d) toggle ON + en una rama de TRABAJO (no master/main) -> commit PERMITIDO,
        #     sin necesidad de gate (es lo normal, el gate solo vigila la base).
        #     push en rama de trabajo SIGUE exigiendo el gate (toca remoto siempre).
        # ------------------------------------------------------------------------------
        allow("(d) toggle ON, rama de trabajo: commit ALLOW sin gate",
              _rc("git commit -m x", REPO_FEATURE, TOGGLE_ON))
        deny("(d) toggle ON, rama de trabajo: push SIN gate DENY (push exige gate siempre)",
             _rc("git push origin feat/mi-rama", REPO_FEATURE, TOGGLE_ON))

        # ------------------------------------------------------------------------------
        # Control: comandos no-git / lecturas no se ven afectados por el toggle.
        # ------------------------------------------------------------------------------
        allow("control: git status con toggle ON sigue ALLOW",
              _rc("git status", REPO_MASTER, TOGGLE_ON))
        allow("control: git log con toggle ON en master sigue ALLOW",
              _rc("git log", REPO_MASTER, TOGGLE_ON))
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)

    print("RESULTADO muro base-gate (fix c): %d OK, %d fallos" % (_pass, _fail))
    print("✅ EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
