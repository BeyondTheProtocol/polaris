#!/usr/bin/env python3
"""tests/test_autopoda_sesion_viva_cwd.py — la poda no borra el worktree de una sesión viva.

EL FALLO (26-sep-2026, 05:40). La rutina com.btp.git-barrido corre `ramas.py autopoda` (bien) y luego
el agente Haiku, que la volvió a correr. Dentro del agente el PATH es el de `run_agent.sh`, sin
/usr/sbin: `lsof` no existía, `_cwd_de` devolvía "" para las 16 sesiones vivas, todas salieron
«fuera del repo o sin cwd legible» y la poda se llevó 7 worktrees con sesión dentro, entre ellos
`upbeat-cartwright-d71aae` (PID 12810). Esas sesiones siguieron corriendo SIN hooks del muro.
La poda determinista de 20 s antes, con PATH completo, sí las había respetado.

Congela:
  · `lsof` se llama por ruta absoluta: con el PATH de run_agent.sh el cwd sigue leyéndose;
  · una sesión viva con cwd ilegible → `autopoda` no poda NADA (rc 75) y lo dice;
  · si `ps` falla → igual (no se sabe qué sesiones hay);
  · protección por CWD además de por rama: una sesión dentro de un worktree en detached lo salva,
    y solo a ese;
  · `cerrar_sesion._sesion_viva_en` también se niega con sesiones de cwd ilegible.
Casa base de usar y tirar (BTP_REPO): no toca la de verdad.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OK, FALLOS = [], []


def ok(msg, cond, extra=""):
    (OK if cond else FALLOS).append(msg)
    print(("  ✅ " if cond else "  ❌ ") + msg + (("  " + extra) if extra and not cond else ""))


def git(cwd, *a):
    return subprocess.run(["git", "-C", cwd] + list(a), capture_output=True, text=True)


tmp = tempfile.mkdtemp(prefix="test-poda-viva-")
base = os.path.join(tmp, "base")
os.makedirs(base)
git(base, "init", "-q", "-b", "master")
git(base, "config", "user.email", "t@t"); git(base, "config", "user.name", "t")
open(os.path.join(base, ".gitignore"), "w").write(".claude/\ntools/state/\n")
git(base, "add", "-A"); git(base, "commit", "-qm", "base")
os.environ.update(BTP_REPO=base, BTP_STATE_DIR=os.path.join(tmp, "state"), BTP_TEST_BATTERY="1")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import ramas  # noqa: E402
import cerrar_sesion  # noqa: E402


def wts_nuevos():
    """Dos worktrees fusionados y limpios: uno en rama, otro en detached."""
    a = os.path.join(base, ".claude", "worktrees", "wt-rama")
    b = os.path.join(base, ".claude", "worktrees", "wt-detached")
    git(base, "worktree", "prune")
    if not os.path.isdir(a):
        r = git(base, "worktree", "add", "-q", "-b", "claude/wt-rama", a)
        if r.returncode:
            git(base, "worktree", "add", "-q", a, "claude/wt-rama")
    if not os.path.isdir(b):
        git(base, "worktree", "add", "-q", "--detach", b)
    assert os.path.isdir(a) and os.path.isdir(b), "no se pudieron crear los worktrees de prueba"
    return a, b


def con_sesiones(lista, fn):
    orig = ramas.claude_sessions
    ramas.claude_sessions = lambda: [dict(x) for x in lista]
    try:
        return fn()
    finally:
        ramas.claude_sessions = orig


# 1. La raíz: con el PATH de run_agent.sh, el cwd de un proceso se sigue leyendo.
path_agente = os.path.expanduser("~/.local/bin") + ":/opt/homebrew/bin:/usr/bin:/bin"
p = subprocess.run([sys.executable, "-c",
                    "import os,sys; sys.path.insert(0, %r); import ramas; "
                    "print(ramas._cwd_de(os.getpid()))" % os.path.join(ROOT, "tools")],
                   cwd=tmp, env=dict(os.environ, PATH=path_agente), capture_output=True, text=True)
ok("con el PATH de run_agent.sh (sin /usr/sbin) el cwd se lee igual",
   os.path.realpath(p.stdout.strip() or "/nada") == os.path.realpath(tmp),
   "-> %r %r" % (p.stdout.strip(), p.stderr[-200:]))

# 2. El incidente: sesión viva con cwd ilegible → no se poda nada.
a, b = wts_nuevos()
rc = con_sesiones([{"pid": "12810", "cwd": ""}], lambda: ramas.limpia(si=True))
ok("sesión con cwd ilegible: autopoda devuelve 75", rc == 75, "-> rc=%r" % rc)
ok("sesión con cwd ilegible: ningún worktree podado", os.path.isdir(a) and os.path.isdir(b))
ok("sesión con cwd ilegible: tampoco se proponen candidatas",
   con_sesiones([{"pid": "1", "cwd": ""}], ramas._candidatas_vacias) == [])

# 3. `ps` falla → sesión desconocida → no se poda.
orig_ps = ramas._PS
ramas._PS = os.path.join(tmp, "no-existe-ps")
try:
    ok("ps que falla da una sesión de cwd ilegible", any(not s["cwd"] for s in ramas.claude_sessions()))
    rc = ramas.limpia(si=True)
    ok("ps que falla: autopoda devuelve 75 y no poda", rc == 75 and os.path.isdir(a) and os.path.isdir(b))
finally:
    ramas._PS = orig_ps

# 4. Por CWD: una sesión dentro del worktree en detached lo salva a él, no al otro.
a, b = wts_nuevos()
rc = con_sesiones([{"pid": "7", "cwd": os.path.join(b, "sub")}], lambda: ramas.limpia(si=True))
ok("sesión dentro del worktree detached: ese NO se poda", os.path.isdir(b))
ok("el otro worktree (fusionado, limpio, sin sesión) SÍ se poda", not os.path.isdir(a), "-> rc=%r" % rc)

# 4b. Sin sesiones: la poda sigue funcionando (el freno no la mata).
a, b = wts_nuevos()
rc = con_sesiones([], lambda: ramas.limpia(si=True))
ok("sin sesiones vivas, los dos fusionados y limpios se podan", not os.path.isdir(a) and not os.path.isdir(b),
   "-> rc=%r" % rc)

# 5. cerrar_sesion: sesiones de cwd ilegible también lo frenan.
a, _ = wts_nuevos()
env_cc = os.environ.pop("CLAUDECODE", None)
try:
    motivo = con_sesiones([{"pid": "9", "cwd": ""}], lambda: cerrar_sesion._sesion_viva_en(a))
    ok("cerrar_sesion no poda si hay sesiones con cwd ilegible", bool(motivo), "-> %r" % motivo)
    motivo = con_sesiones([], lambda: cerrar_sesion._sesion_viva_en(a))
    ok("cerrar_sesion sin sesiones vivas sí deja podar", motivo == "", "-> %r" % motivo)
finally:
    if env_cc is not None:
        os.environ["CLAUDECODE"] = env_cc

import shutil  # noqa: E402
shutil.rmtree(tmp, ignore_errors=True)
print("\ntest_autopoda_sesion_viva_cwd: %d ok, %d fallos" % (len(OK), len(FALLOS)))
sys.exit(1 if FALLOS else 0)
