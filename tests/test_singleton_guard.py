#!/usr/bin/env python3
"""test_singleton_guard.py — los 3 singletons pasan por su puerta con candado, el resto no se toca.

NORMA: `feedback-sesion-paralela-seguridad-primero` (clase BLOQUEO, repetida) — «sesión paralela
= NORMAL: aíslate en tu rama/worktree y SIGUE; solo paras/serializas en los 3 singletons
(activar launchd, fusionar a base, control de pantalla)».

Lo que prueba este fichero es la mitad mecanizable: que la forma CRUDA de un singleton se
deniegue y se nombre la puerta que sí toma el candado (`tools/git_mutex.py`,
`tools/activar_daemon.py`), y —sobre todo— que NO se toque nada más. La mitad no mecanizable
(«y SIGUE», o sea: no pararse a preguntar por una sesión paralela) es criterio y se dice así.

EL FILO ESTÁ EN LOS FALSOS POSITIVOS, y por eso la lista ALLOW es más larga que la DENY:
`git merge-base` y `git merge-tree` NO son merges, `git worktree list/add` no poda nada,
`git branch --show-current` no borra, `launchctl list` es diagnóstico y `git add -A tests/x.py`
SÍ es explícito. Un grep los contaba a todos como infracciones (el primer barrido de esta norma
salió inflado justo por eso: 126 merges donde había 112, 134 `add` donde había 83).
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import json  # noqa: E402
import subprocess  # noqa: E402

ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
GUARD = _os.path.join(ROOT, ".claude", "hooks", "singleton_guard.py")
CASA = "/Users/polaris/claudecode"
WT = CASA + "/.claude/worktrees/una-rama"
OTRO_REPO = "/Users/polaris/projects/titular-{{APELLIDO}}-case"

ENV = dict(_os.environ, BTP_WT_RAIZ_OVERRIDE=WT)
ENV.pop("BTP_SINGLETON_OK", None)


def _rc(cmd, cwd=WT, env=None, tool="Bash"):
    p = subprocess.run([_sys.executable, GUARD], capture_output=True, text=True, timeout=20,
                       env=env or ENV,
                       input=json.dumps({"tool_name": tool, "tool_input": {"command": cmd}, "cwd": cwd}))
    return p.returncode, (p.stderr or "").strip()


DENY = [
    # S1 launchd
    ("launchctl load ~/Library/LaunchAgents/com.btp.correo.plist", "cargar un daemon a pelo"),
    ("launchctl kickstart -k gui/501/com.btp.correo", "kickstart (46 casos reales)"),
    ("launchctl bootout gui/501/com.btp.correo", "bootout"),
    # S2 fusionar a base
    ("git merge --no-ff mi-rama -m 'x'", "merge directo (107 casos reales)"),
    ("git worktree remove .claude/worktrees/vieja", "podar un worktree (muta .git compartido)"),
    ("git worktree prune", "prune"),
    ("git branch -d mi-rama", "borrar rama"),
    ("git -C /Users/polaris/claudecode merge rama", "-C apuntando a casa base"),
    ("cd /Users/polaris/claudecode && git merge rama", "cd a casa base y fusionar"),
    # S3 git add en bloque
    ("git add -A", "add -A sin rutas (79 casos reales)"),
    ("git add .", "add ."),
    ("git add -u", "add -u"),
    ("git add -A && git commit -m x", "add -A encadenado"),
]

ALLOW = [
    # git de LECTURA que un grep confundiría con el singleton
    ("git merge-base --is-ancestor rama master", "merge-base NO es un merge"),
    ("git merge-tree --write-tree master HEAD", "merge-tree NO es un merge"),
    ("git worktree list", "listar worktrees"),
    ("git worktree add .claude/worktrees/nueva -b r master", "crear worktree no muta lo de nadie"),
    ("git branch --show-current", "ver la rama"),
    ("git branch -a", "listar ramas"),
    ("git log --oneline -5", "log"),
    ("git status --short", "status"),
    ("git diff --stat", "diff"),
    # launchctl de LECTURA
    ("launchctl list", "listar daemons (diagnóstico)"),
    ("launchctl print gui/501/com.btp.correo", "print"),
    # add explícito: el trabajo real
    ("git add tools/x.py tests/test_x.py", "rutas explícitas"),
    ("git add -A tests/test_seguimiento.py", "-A CON rutas detrás sí es explícito"),
    ("git commit -m 'x'", "commit no es singleton (sería insoportable)"),
    ("git push", "push no muta el .git compartido; fuera de ámbito a propósito"),
    # la puerta sancionada NO se deniega a sí misma
    ("python3 tools/git_mutex.py -C /Users/polaris/claudecode merge --no-ff r -m x", "git_mutex"),
    ("python3 tools/activar_daemon.py com.btp.correo --kick", "activar_daemon"),
    # otro repo
    ("cd /Users/polaris/projects/titular-{{APELLIDO}}-case && git merge rama", "merge en la web: otro repo"),
    ("git -C /Users/polaris/projects/titular-{{APELLIDO}}-case merge rama", "-C a otro repo"),
]

fallos = 0
print("=== DEBE DENEGAR (la forma cruda de un singleton) ===")
for cmd, desc in DENY:
    rc, _ = _rc(cmd)
    if rc == 2:
        print("  ✅ %s" % desc)
    else:
        fallos += 1
        print("  ❌ PASA (MAL): %s  ->  %s" % (desc, cmd))

print()
print("=== DEBE PASAR (todo lo demás, que es casi todo) ===")
for cmd, desc in ALLOW:
    rc, msg = _rc(cmd)
    if rc == 0:
        print("  ✅ %s" % desc)
    else:
        fallos += 1
        print("  ❌ DENEGADO (MAL): %s  ->  %s\n     %s" % (desc, cmd, msg.split("\n")[0]))

print()
print("=== LO DEMÁS ===")
casos = []
rc, _ = _rc("git merge rama", env=dict(ENV, BTP_SINGLETON_OK="1"))
casos.append(("BTP_SINGLETON_OK=1 → PASA (escotilla explícita)", rc == 0))
rc, _ = _rc("git merge rama", env=dict(ENV, BTP_SINGLETON_OK="0"))
casos.append(("BTP_SINGLETON_OK=0 NO cuenta como escotilla → DENIEGA", rc == 2))
rc, _ = _rc("git merge rama", cwd=OTRO_REPO)
casos.append(("sesión en otro repo → PASA", rc == 0))
rc, _ = _rc("git merge rama", cwd=CASA)
casos.append(("sesión en casa base → DENIEGA", rc == 2))
rc, _ = _rc("cd /Users/polaris/projects/titular-{{APELLIDO}}-case && git merge r", cwd=CASA)
casos.append(("cd HACIA fuera y fusionar → PASA (no es este singleton)", rc == 0))
rc, _ = _rc("cd ~/claudecode && git merge r", cwd=OTRO_REPO)
casos.append(("cd HACIA casa base desde otro repo → DENIEGA", rc == 2))
rc, _ = _rc("echo hola", tool="Write")
casos.append(("tool que no es Bash → PASA", rc == 0))
rc, _ = _rc("git merge 'comilla sin cerrar", cwd=CASA)
casos.append(("comando no tokenizable → PASA (fail-OPEN deliberado)", rc == 0))
p = subprocess.run([_sys.executable, GUARD], input="{no es json", capture_output=True,
                   text=True, timeout=20, env=ENV)
casos.append(("payload ilegible → PASA (fail-OPEN deliberado)", p.returncode == 0))
rc, msg = _rc("git merge rama")
casos.append(("el mensaje NOMBRA la puerta con candado", "git_mutex.py" in msg))
rc, msg = _rc("launchctl load x.plist")
casos.append(("el mensaje de launchd nombra activar_daemon.py", "activar_daemon.py" in msg))
for desc, ok in casos:
    if ok:
        print("  ✅ %s" % desc)
    else:
        fallos += 1
        print("  ❌ %s" % desc)

print()
print("RESULTADO singleton_guard: %d fallos" % fallos)
print("✅ LOS SINGLETONS VAN POR SU PUERTA" if not fallos else "❌ revisar")
raise SystemExit(1 if fallos else 0)
