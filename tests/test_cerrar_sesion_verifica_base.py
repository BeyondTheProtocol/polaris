#!/usr/bin/env python3
"""test_cerrar_sesion_verifica_base.py — tras fusionar, se prueba en casa base lo que el worktree salta.

POR QUÉ EXISTE (26-sep-2026, deuda `fusion-sin-baterias-que-se-saltan-en-worktree`). Una fusión dio
«TODO EN VERDE» en el worktree y dejó `test_fuga.sh` ROJO en casa base: esa batería se salta (rc 77)
fuera de `~/claudecode`. Lo cazó otra sesión. Ya había pasado el 22-sep y la memoria no bastó.
Ahora `cerrar_sesion --apply`, tras fusionar, corre en casa base los tests que pueden saltarse y lo
dice en la línea de resumen. Se reproduce con una casa base de usar y tirar (BTP_REPO).
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "cerrar_sesion.py")
fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True).stdout.strip()


def casa(tmp, rojo):
    base = os.path.join(tmp, "casa")
    os.makedirs(os.path.join(base, "tests"))
    git(base, "init", "-q", "-b", "master")
    git(base, "config", "user.email", "t@t")
    git(base, "config", "user.name", "t")
    with open(os.path.join(base, ".gitignore"), "w") as f:
        f.write(".claude/worktrees/\n")
    # Un test que fuera de casa base se saltaría (lleva la marca de rc 77) y aquí da su resultado.
    with open(os.path.join(base, "tests", "test_solo_casa.py"), "w") as f:
        f.write("import sys\nSKIP = 77\nsys.exit(%d)\n" % (1 if rojo else 0))
    with open(os.path.join(base, "tests", "test_se_salta.py"), "w") as f:
        f.write("import sys\nsys.exit(77)\n")
    # Como test_fuga.sh: sale rojo si ve el permiso de excepción con el que se llama al cierre.
    with open(os.path.join(base, "tests", "test_sin_permisos.py"), "w") as f:
        f.write("import os, sys\nSKIP = 77\nsys.exit(1 if os.environ.get('BTP_GIT_BASE_OK') else 0)\n")
    git(base, "add", "-A")
    git(base, "commit", "-q", "-m", "inicio")
    wt = os.path.join(base, ".claude", "worktrees", "x")
    git(base, "worktree", "add", "-q", "-b", "claude/x", wt)
    with open(os.path.join(wt, "g.txt"), "w") as f:
        f.write("trabajo\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "trabajo")
    return base, wt


def cerrar(base, wt, tmp):
    env = dict(os.environ, BTP_REPO=base, BTP_GIT_BASE_OK="1", BTP_STATE_DIR=os.path.join(tmp, "state"))
    for k in ("CLAUDECODE", "BTP_CIERRE_SIN_VERIFICAR"):
        env.pop(k, None)
    p = subprocess.run([sys.executable, TOOL, "--apply", "--no-poda"], cwd=wt, env=env,
                       capture_output=True, text=True, timeout=300)
    return p.stdout + p.stderr


def main():
    print("── casa base roja tras fusionar: se dice ──")
    tmp = tempfile.mkdtemp(prefix="cierre_rojo_")
    base, wt = casa(tmp, rojo=True)
    salida = cerrar(base, wt, tmp)
    check(os.path.exists(os.path.join(base, "g.txt")), "fusiona")
    check("CASA BASE ROJA" in salida and "test_solo_casa.py" in salida,
          "la línea de resumen avisa y nombra la batería roja: %r" % salida[-200:])
    check("test_se_salta.py" not in salida.split("CASA BASE ROJA")[-1],
          "un test que también se salta en casa base (rc 77) no cuenta como rojo")

    print("── casa base en verde: no hay aviso ──")
    tmp = tempfile.mkdtemp(prefix="cierre_verde_")
    base, wt = casa(tmp, rojo=False)
    salida = cerrar(base, wt, tmp)
    check("CASA BASE ROJA" not in salida and "en verde" in salida,
          "dice en verde (y el permiso del cierre no llega a los tests): %r" % salida[-200:])

    print("\ntest_cerrar_sesion_verifica_base: %d fallos" % len(fallos))
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
