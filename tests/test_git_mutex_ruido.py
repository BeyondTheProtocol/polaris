#!/usr/bin/env python3
"""test_git_mutex_ruido.py — una fusión DIRECTA con `git_mutex.py merge` también verifica casa base y hace ruido.

POR QUÉ EXISTE (10-oct-26, punto I de consejero-arquitectura). El aviso de «casa base quedó roja tras fusionar» vivía
solo en `cerrar_sesion --apply`. Cuatro fusiones del mismo día se hicieron con `git_mutex.py merge` directo: no corrió
ninguna batería, no se abrió deuda, no se avisó y `rojas_casa_base.json` ni existía. Ahora `git_mutex` llama a la MISMA
función (`cerrar_sesion.verificar_tras_fusion`). Se prueba con fusiones reales en una casa base de usar y tirar:
  1. merge --no-ff con una batería que la rama ROMPE y línea base fresca → deuda `casa-base-roja-<bat>`, aviso, línea base nueva,
     rc 0 y la fusión hecha (no se bloquea ni revierte nada);
  2. lo mismo con un fast-forward (el padre anterior sale de ORIG_HEAD);
  3. lo que ya estaba roja no se atribuye a la rama;
  4. sin línea base: se abre la deuda pero no se culpa a la rama;
  5. un merge con CONFLICTO (rc≠0) no verifica nada ni toca la línea base;
  6. BTP_CIERRE_SIN_VERIFICAR=1 lo salta; un subcomando que no es merge no corre nada.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.environ.get("BTP_GIT_MUTEX_TOOL") or os.path.join(ROOT, "tools", "git_mutex.py")
fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True).stdout.strip()


BATERIA = "import sys\nSKIP = 77\nsys.exit(1 if 'malo' in open('valor.txt').read() else 0)\n"
SIEMPRE_ROJA = "import sys\nSKIP = 77\nsys.exit(1)\n"


def casa(tmp, baseline=None, siempre_roja=(), conflicto=False):
    base = os.path.join(tmp, "casa")
    os.makedirs(os.path.join(base, "tests"))
    git(base, "init", "-q", "-b", "master")
    git(base, "config", "user.email", "t@t")
    git(base, "config", "user.name", "t")
    open(os.path.join(base, ".gitignore"), "w").write(".claude/worktrees/\n")
    open(os.path.join(base, "valor.txt"), "w").write("bueno\n")
    open(os.path.join(base, "tests", "test_dep.py"), "w").write(BATERIA)
    for n in siempre_roja:
        open(os.path.join(base, "tests", n), "w").write(SIEMPRE_ROJA)
    git(base, "add", "-A")
    git(base, "commit", "-q", "-m", "inicio")
    wt = os.path.join(base, ".claude", "worktrees", "x")
    git(base, "worktree", "add", "-q", "-b", "claude/x", wt)
    open(os.path.join(wt, "valor.txt"), "w").write("malo\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "empeora el valor")
    open(os.path.join(wt, "g.txt"), "w").write("segundo commit de la rama\n")   # 2 commits: en un fast-forward HEAD^1 NO es casa base de antes
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "segundo")
    if conflicto:                       # casa base cambia lo mismo de otra manera → el merge entra en conflicto
        open(os.path.join(base, "valor.txt"), "w").write("distinto\n")
        git(base, "commit", "-qam", "otro cambio")
    estado = os.path.join(tmp, "state")
    os.makedirs(estado)
    if baseline is not None:
        os.makedirs(os.path.join(estado, "cerrar_sesion"))
        sha = git(base, "rev-parse", "HEAD")
        json.dump({"sha": sha, "fecha": "2026-10-10T00:00:00", "rojas": baseline},
                  open(os.path.join(estado, "cerrar_sesion", "rojas_casa_base.json"), "w"))
    return base, estado


def fusiona(base, estado, *args, extra=None):
    sumidero = os.path.join(estado, "avisos.jsonl")
    env = dict(os.environ, BTP_REPO=base, BTP_STATE_DIR=estado, BTP_CIERRE_AVISO_A=sumidero, BTP_GIT_BASE_OK="1")
    env.pop("BTP_CIERRE_SIN_VERIFICAR", None)
    env.update(extra or {})
    p = subprocess.run([sys.executable, TOOL, "-C", base] + list(args), capture_output=True, text=True, env=env,
                       timeout=300, stdin=subprocess.DEVNULL)
    avisos = [json.loads(l) for l in open(sumidero)] if os.path.exists(sumidero) else []
    try:
        libro = json.load(open(os.path.join(estado, "deuda.json")))
    except Exception:
        libro = {}
    try:
        lb = json.load(open(os.path.join(estado, "cerrar_sesion", "rojas_casa_base.json")))
    except Exception:
        lb = None
    return p, avisos, libro, lb


print("1) merge --no-ff directo que ROMPE una batería verde")
tmp = tempfile.mkdtemp(prefix="gmruido_")
base, est = casa(tmp, baseline=[])
p, avisos, libro, lb = fusiona(base, est, "merge", "--no-ff", "claude/x", "-m", "fusion directa")
check(p.returncode == 0 and "malo" in open(os.path.join(base, "valor.txt")).read(), "rc 0 y la fusión se hizo (no se bloquea ni revierte)")
check("casa-base-roja-test_dep.py" in libro and "claude/x" in libro["casa-base-roja-test_dep.py"]["que"], "abre la deuda casa-base-roja-test_dep.py con la rama")
check(len(avisos) == 1 and "test_dep.py" in avisos[0]["texto"], "emite el aviso")
check(lb is not None and lb["rojas"] == ["test_dep.py"] and lb["sha"] == git(base, "rev-parse", "HEAD"), "deja la línea base de la próxima fusión")
check("casa base roja" in p.stderr and "NUEVA(S)" in p.stderr, "y lo dice por stderr: %r" % p.stderr[-160:])

print("2) fast-forward")
tmp = tempfile.mkdtemp(prefix="gmruido_")
base, est = casa(tmp, baseline=[])
p, avisos, libro, lb = fusiona(base, est, "merge", "claude/x")
check(p.returncode == 0 and git(base, "rev-list", "--parents", "-n", "1", "HEAD").count(" ") == 1, "fue un fast-forward (sin commit de merge)")
check("casa-base-roja-test_dep.py" in libro and len(avisos) == 1 and "NUEVA(S)" in p.stderr, "igualmente atribuye a la rama (el padre anterior sale de ORIG_HEAD)")

print("3) lo que ya estaba roja no se atribuye a la rama")
tmp = tempfile.mkdtemp(prefix="gmruido_")
base, est = casa(tmp, baseline=["test_previa.py"], siempre_roja=("test_previa.py",))
p, avisos, libro, lb = fusiona(base, est, "merge", "--no-ff", "claude/x", "-m", "m")
check("casa-base-roja-test_previa.py" not in libro and avisos and "test_previa.py" not in avisos[0]["texto"], "test_previa.py ni deuda ni culpa")
check("casa-base-roja-test_dep.py" in libro, "pero sí la que rompió la rama")

print("4) sin línea base: deuda sí, culpa no")
tmp = tempfile.mkdtemp(prefix="gmruido_")
base, est = casa(tmp, baseline=None)
p, avisos, libro, lb = fusiona(base, est, "merge", "--no-ff", "claude/x", "-m", "m")
check("casa-base-roja-test_dep.py" in libro and "NO hay línea base" in libro["casa-base-roja-test_dep.py"]["que"] and not avisos and "NUEVA" not in p.stderr,
      "abre deuda, no avisa y no dice que la rompió la fusión")

print("5) merge con conflicto: no verifica nada")
tmp = tempfile.mkdtemp(prefix="gmruido_")
# test_siempre.py está roja y NO figura en la línea base: si el merge fallido verificara, la atribuiría (y abriría deuda)
base, est = casa(tmp, baseline=[], conflicto=True, siempre_roja=("test_siempre.py",))
p, avisos, libro, lb = fusiona(base, est, "merge", "--no-ff", "claude/x", "-m", "m")
check(p.returncode != 0 and not libro and not avisos and lb is not None and lb["rojas"] == [], "rc≠0, sin deuda ni aviso y la línea base no se toca")

print("6) interruptor y subcomandos que no fusionan")
tmp = tempfile.mkdtemp(prefix="gmruido_")
base, est = casa(tmp, baseline=[])
p, avisos, libro, lb = fusiona(base, est, "merge", "--no-ff", "claude/x", "-m", "m", extra={"BTP_CIERRE_SIN_VERIFICAR": "1"})
check(p.returncode == 0 and not libro and not avisos, "BTP_CIERRE_SIN_VERIFICAR=1 lo salta")
tmp = tempfile.mkdtemp(prefix="gmruido_")
base, est = casa(tmp, baseline=[])
p, avisos, libro, lb = fusiona(base, est, "status", "--short")
check(p.returncode == 0 and not libro and not avisos, "un `status` no corre nada")

if fallos:
    print("\n❌ %d fallo(s)" % len(fallos))
    sys.exit(1)
print("\nOK")
