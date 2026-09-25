#!/usr/bin/env python3
"""tests/test_log_auditoria_casa_base.py — que la traza del muro clínico no se parta por worktree.

EL FALLO (20-sep-2026). `clinico_guard.py` resolvía el log de accesos contra el árbol donde
corre el hook (`CLAUDE_PROJECT_DIR`), así que cada sesión con worktree escribía SU propia
traza. Medido al podar uno: 52.682 líneas allí dentro, con accesos que no estaban en el log de
casa base (22 de las 200 últimas). Y como `.claude/logs` está gitignored, `git status` decía
«limpio» y la poda se los habría llevado sin avisar a nadie — se vio mirando los ficheros
ignorados a mano antes de borrar, no por ningún freno.

La traza de quién tocó la zona clínica es justo lo que no puede fragmentarse ni perderse: es la
evidencia de que el muro hizo su trabajo. Por eso el log va SIEMPRE a casa base.

Cómo se distingue, barato y sin llamar a git: un worktree tiene `.git` como FICHERO (un puntero
al .git compartido); la casa base lo tiene como DIRECTORIO. Y un CLAUDE_PROJECT_DIR que apunte
a un directorio cualquiera (los tests lo hacen) se respeta tal cual, porque allí no hay `.git`.
"""
import os
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
ARBOL = os.path.dirname(AQUI)
sys.path.insert(0, os.path.join(ARBOL, ".claude", "hooks"))

OK, FALLOS = [], []


def ok(msg, cond, extra=""):
    (OK if cond else FALLOS).append(msg)
    print(("  ✅ " if cond else "  ❌ ") + msg + (("  " + extra) if extra and not cond else ""))


CASA = os.path.expanduser("~/claudecode")
tmp = tempfile.mkdtemp(prefix="test-log-aud-")

# 1. Un árbol que PARECE un worktree (.git como fichero) → el log va a casa base.
falso_wt = os.path.join(tmp, "worktree")
os.makedirs(falso_wt)
open(os.path.join(falso_wt, ".git"), "w").write("gitdir: /Users/polaris/claudecode/.git/x\n")

# 2. Un árbol con .git de DIRECTORIO (casa base) → se respeta.
falsa_casa = os.path.join(tmp, "casabase")
os.makedirs(os.path.join(falsa_casa, ".git"))

# 3. Un directorio SIN .git (lo que hacen los tests) → se respeta tal cual.
pelado = os.path.join(tmp, "pelado")
os.makedirs(pelado)

for nombre, hook, fichero in (("clinico_guard", "clinico_guard", "clinico-access.log"),
                              ("regla_en_accion", "regla_en_accion", "regla-en-accion.log")):
    for caso, repo, espera_casa in (("worktree", falso_wt, True),
                                    ("casa base", falsa_casa, False),
                                    ("dir sin .git (tests)", pelado, False)):
        os.environ["CLAUDE_PROJECT_DIR"] = repo
        for m in list(sys.modules):
            if m in ("clinico_guard", "regla_en_accion", "zonas_clinicas"):
                del sys.modules[m]
        mod = __import__(hook)
        esperado = os.path.join(CASA if espera_casa else repo, ".claude", "logs", fichero)
        ok("%s desde %s → %s" % (nombre, caso, "casa base" if espera_casa else "su propio árbol"),
           mod.LOG == esperado, "-> %r" % mod.LOG)

# 4. Y lo que de verdad importa: NUNCA dentro de .claude/worktrees.
os.environ["CLAUDE_PROJECT_DIR"] = falso_wt
for m in list(sys.modules):
    if m in ("clinico_guard", "zonas_clinicas"):
        del sys.modules[m]
import clinico_guard  # noqa: E402
ok("el log clínico NUNCA cae dentro de .claude/worktrees",
   "/.claude/worktrees/" not in clinico_guard.LOG, "-> %r" % clinico_guard.LOG)

import shutil  # noqa: E402
shutil.rmtree(tmp, ignore_errors=True)
os.environ.pop("CLAUDE_PROJECT_DIR", None)

print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS
      else "test_log_auditoria_casa_base: %d/%d OK" % (len(OK), len(OK)))
sys.exit(1 if FALLOS else 0)
