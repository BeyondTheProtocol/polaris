#!/usr/bin/env python3
"""tests/test_git_mutex_poda.py — el freno de poda guarda TODAS las puertas, no una.

EL FALLO (20-sep-2026). `cerrar_sesion.py` ya se negaba a podar un worktree con ficheros que git
no ve (el panel del lazo, el log de auditoría clínica). El mismo día avisó —«se perderían 1
fichero(s)»— y la poda se hizo igual por otra puerta: `git_mutex.py worktree remove`, que es la
vía sancionada para mutar el .git y no preguntaba nada. Un freno que solo guarda una puerta no
guarda.

Y de paso, el log del dispatcher: decía «sin saldo de pago» siempre, también cuando lo que pasaba
era el tope diario con el prepago sano. Leído así se concluye que la API no tiene crédito.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GM = os.path.join(ROOT, "tools", "git_mutex.py")
OK, FALLOS = [], []


def ok(msg, cond, extra=""):
    (OK if cond else FALLOS).append(msg)
    print(("  ✅ " if cond else "  ❌ ") + msg + (("  " + extra) if extra and not cond else ""))


def git(cwd, *a):
    return subprocess.run(["git", "-C", cwd] + list(a), capture_output=True, text=True)


def gm(cwd, *a, env=None):
    e = dict(os.environ)
    e.pop("BTP_PODA_PERDER_OK", None)
    e["BTP_STATE_DIR"] = os.path.join(cwd, "_state")      # el candado, fuera del sistema vivo
    e.update(env or {})
    return subprocess.run([sys.executable, GM] + list(a), cwd=cwd, capture_output=True,
                          text=True, env=e, timeout=60)


tmp = tempfile.mkdtemp(prefix="test-gm-poda-")
base = os.path.join(tmp, "base")
os.makedirs(base)
git(base, "init", "-q", "-b", "master")
git(base, "config", "user.email", "t@t"); git(base, "config", "user.name", "t")
open(os.path.join(base, ".gitignore"), "w").write("00_FUENTE-DE-VERDAD/\n.claude/logs/\n")
git(base, "add", "-A"); git(base, "commit", "-qm", "base")

# 1. Worktree con el panel del lazo dentro (gitignored): NO se poda.
wt1 = os.path.join(tmp, "wt-con-panel")
git(base, "worktree", "add", "-q", "--detach", wt1)
os.makedirs(os.path.join(wt1, "00_FUENTE-DE-VERDAD", "Gestion"))
open(os.path.join(wt1, "00_FUENTE-DE-VERDAD", "Gestion", "PANEL-LAZO.md"), "w").write("## job x\n")
r = gm(base, "worktree", "remove", wt1)
ok("se niega a podar un worktree con el panel del lazo dentro", r.returncode == 3,
   "-> rc=%d %s" % (r.returncode, r.stderr[:120]))
ok("y dice QUÉ se perdería", "PANEL-LAZO.md" in r.stderr, "-> %r" % r.stderr[:160])
ok("el worktree sigue ahí", os.path.isdir(wt1))

# 2. `--force` de git no salta el freno: va de cambios sin commitear, no de lo ignorado.
r = gm(base, "worktree", "remove", "--force", wt1)
ok("--force NO salta el freno", r.returncode == 3 and os.path.isdir(wt1),
   "-> rc=%d" % r.returncode)

# 3. El log de auditoría clínica, igual.
wt2 = os.path.join(tmp, "wt-con-log")
git(base, "worktree", "add", "-q", "--detach", wt2)
os.makedirs(os.path.join(wt2, ".claude", "logs"))
open(os.path.join(wt2, ".claude", "logs", "clinico-access.log"), "w").write("x\n")
ok("se niega a podar con el log de auditoría dentro",
   gm(base, "worktree", "remove", wt2).returncode == 3 and os.path.isdir(wt2))

# 4. Escape NOMBRADO: con BTP_PODA_PERDER_OK=1 sí poda (ya rescatado o basura).
r = gm(base, "worktree", "remove", "--force", wt2, env={"BTP_PODA_PERDER_OK": "1"})
ok("con BTP_PODA_PERDER_OK=1 sí poda", r.returncode == 0 and not os.path.isdir(wt2),
   "-> rc=%d %s" % (r.returncode, r.stderr[:100]))

# 5. Un worktree limpio se poda sin fricción: no se trata de bloquear todo.
wt3 = os.path.join(tmp, "wt-limpio")
git(base, "worktree", "add", "-q", "--detach", wt3)
r = gm(base, "worktree", "remove", wt3)
ok("un worktree limpio se poda normal", r.returncode == 0 and not os.path.isdir(wt3),
   "-> rc=%d %s" % (r.returncode, r.stderr[:100]))

# 6. Otros comandos pasan tal cual.
ok("otros comandos de git no se tocan", gm(base, "status", "--short").returncode == 0)

# 7. Dispatcher: el motivo real llega al log, no un «sin saldo» genérico.
src = open(os.path.join(ROOT, "tools", "btp_dispatcher.sh"), encoding="utf-8").read()
ok("el dispatcher ya no dice «sin saldo de pago» a ciegas", "sin saldo de pago →" not in src)
ok("distingue el tope local del prepago agotado",
   "[tope_local]" in src and "[prepago_agotado]" in src)

subprocess.run(["rm", "-rf", tmp], capture_output=True)
print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS
      else "test_git_mutex_poda: %d/%d OK" % (len(OK), len(OK)))
sys.exit(1 if FALLOS else 0)
