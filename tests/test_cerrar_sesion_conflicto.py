#!/usr/bin/env python3
"""test_cerrar_sesion_conflicto.py — una fusión que choca NO puede dejar casa base a medio fusionar.

POR QUÉ EXISTE (22-sep-2026). Dos sesiones arreglaron el mismo rojo de `test_all` en paralelo; la
segunda en cerrar (`cerrar_sesion.py --apply`) chocó en `tests/test_fuga.sh` y `tools/anatomia.py`.
La tool devolvió «fusión falló» y SE FUE: casa base —el sistema vivo 24/7— quedó con `UU` y
marcadores `<<<<<<<` en `tools/anatomia.py` hasta que se abortó a mano. Cualquier daemon que
importara ese fichero en esos minutos caía.

Se reproduce con repos de usar y tirar (BTP_REPO apunta a una casa base falsa): el test nunca toca
la de verdad.
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
    p = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True)
    return p.stdout.strip()


def main():
    tmp = tempfile.mkdtemp(prefix="cerrar_conflicto_")
    base = os.path.join(tmp, "casa")
    os.makedirs(base)
    git(base, "init", "-q", "-b", "master")
    git(base, "config", "user.email", "t@t")
    git(base, "config", "user.name", "t")
    with open(os.path.join(base, "f.txt"), "w") as f:
        f.write("original\n")
    with open(os.path.join(base, ".gitignore"), "w") as f:
        f.write(".claude/worktrees/\n")          # como en la casa base de verdad
    git(base, "add", "-A")
    git(base, "commit", "-q", "-m", "inicio")
    wt = os.path.join(base, ".claude", "worktrees", "rama-x")
    git(base, "worktree", "add", "-q", "-b", "claude/rama-x", wt)
    # La rama cambia la línea…
    with open(os.path.join(wt, "f.txt"), "w") as f:
        f.write("version de la rama\n")
    git(wt, "commit", "-q", "-am", "rama")
    # …y otra sesión ya fusionó un cambio distinto de la MISMA línea.
    with open(os.path.join(base, "f.txt"), "w") as f:
        f.write("version de otra sesion\n")
    git(base, "commit", "-q", "-am", "otra sesion")
    antes = git(base, "rev-parse", "HEAD")

    env = dict(os.environ, BTP_REPO=base, BTP_GIT_BASE_OK="1",
               BTP_STATE_DIR=os.path.join(tmp, "state"))
    p = subprocess.run([sys.executable, TOOL, "--apply", "--no-poda"], cwd=wt, env=env,
                       capture_output=True, text=True, timeout=120)
    salida = p.stdout + p.stderr

    check("fusión falló" in salida or "conflicto" in salida.lower(),
          "la tool dice que la fusión ha fallado")
    check(not os.path.exists(os.path.join(base, ".git", "MERGE_HEAD")),
          "casa base NO se queda a medio fusionar (sin MERGE_HEAD)")
    st = git(base, "status", "--porcelain")
    check(st == "", "casa base queda limpia (sin UU): %r" % st[:120])
    check("<<<<<<<" not in open(os.path.join(base, "f.txt")).read(),
          "sin marcadores de conflicto en el disco de casa base")
    check(git(base, "rev-parse", "HEAD") == antes, "master no se ha movido")
    check(git(wt, "log", "-1", "--format=%s") == "rama", "el trabajo de la rama sigue intacto")
    check("abort" in salida.lower() or "deshecha" in salida.lower(),
          "la salida dice que la fusión se ha deshecho, no solo que falló")

    base_en_rama()
    merge_a_medias_en_la_rama()
    fusion_ajena_a_medias()

    if fallos:
        print("❌ %d fallo(s)\n--- salida de la tool ---\n%s" % (len(fallos), salida[-1500:]))
        return 1
    print("✅ CERRAR_SESION CONFLICTO EN VERDE")
    return 0


def base_en_rama():
    """Casa base aparcada en otra rama (22-sep-26). `cerrar_sesion` fusionaba en el HEAD que
    tuviera casa base, fuera master o no: en 60 días casa base estuvo 27 veces fuera de master,
    una de ellas 10,5 h (auto-mejora). Una sesión que cerrara entonces metía su trabajo en la
    rama de la rutina, no en master, y el freno de master ni se enteraba."""
    print("── casa base en otra rama: NO se fusiona ──")
    tmp = tempfile.mkdtemp(prefix="cerrar_enrama_")
    base = os.path.join(tmp, "casa")
    os.makedirs(base)
    git(base, "init", "-q", "-b", "master")
    git(base, "config", "user.email", "t@t")
    git(base, "config", "user.name", "t")
    with open(os.path.join(base, ".gitignore"), "w") as f:
        f.write(".claude/worktrees/\n")
    with open(os.path.join(base, "f.txt"), "w") as f:
        f.write("original\n")
    git(base, "add", "-A")
    git(base, "commit", "-q", "-m", "inicio")
    wt = os.path.join(base, ".claude", "worktrees", "sesion")
    git(base, "worktree", "add", "-q", "-b", "claude/sesion", wt)
    with open(os.path.join(wt, "g.txt"), "w") as f:
        f.write("trabajo de la sesion\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "sesion")
    git(base, "checkout", "-q", "-b", "auto-mejora-2026-09-13")   # la rutina aparca casa base
    rutina_antes = git(base, "rev-parse", "HEAD")
    master_antes = git(base, "rev-parse", "master")
    env = dict(os.environ, BTP_REPO=base, BTP_GIT_BASE_OK="1",
               BTP_STATE_DIR=os.path.join(tmp, "state"))
    p = subprocess.run([sys.executable, TOOL, "--apply", "--no-poda"], cwd=wt, env=env,
                       capture_output=True, text=True, timeout=120)
    salida = p.stdout + p.stderr
    check(git(base, "rev-parse", "HEAD") == rutina_antes,
          "la rama aparcada NO recibe la fusión de la sesión")
    check(git(base, "rev-parse", "master") == master_antes, "master tampoco se ha movido")
    check("auto-mejora-2026-09-13" in salida and "master" in salida,
          "la salida dice en qué rama está casa base y que debería estar en master")
    check(git(wt, "log", "-1", "--format=%s") == "sesion", "el trabajo de la sesión sigue en su rama")
    # Y en master sí fusiona (que el freno no rompa el caso normal).
    git(base, "checkout", "-q", "master")
    p = subprocess.run([sys.executable, TOOL, "--apply", "--no-poda"], cwd=wt, env=env,
                       capture_output=True, text=True, timeout=120)
    check(os.path.exists(os.path.join(base, "g.txt")), "con casa base en master, fusiona como siempre")



def merge_a_medias_en_la_rama():
    """El caso del 22-sep-26: `git merge master` choca DENTRO del worktree, y el cierre commitea
    y fusiona los marcadores `<<<<<<<` como si fueran un cambio normal. Le pasó a
    `tools/run_agent.sh` (el script de todos los agentes del lazo)."""
    print("── merge a medias en la rama: NO se commitea ni se fusiona ──")
    tmp = tempfile.mkdtemp(prefix="cerrar_medias_")
    base = os.path.join(tmp, "casa")
    os.makedirs(base)
    git(base, "init", "-q", "-b", "master")
    git(base, "config", "user.email", "t@t")
    git(base, "config", "user.name", "t")
    with open(os.path.join(base, ".gitignore"), "w") as f:
        f.write(".claude/worktrees/\n")
    with open(os.path.join(base, "f.sh"), "w") as f:
        f.write("echo original\n")
    git(base, "add", "-A")
    git(base, "commit", "-q", "-m", "inicio")
    wt = os.path.join(base, ".claude", "worktrees", "medias")
    git(base, "worktree", "add", "-q", "-b", "claude/medias", wt)
    with open(os.path.join(wt, "f.sh"), "w") as f:
        f.write("echo de la rama\n")
    git(wt, "commit", "-q", "-am", "rama")
    with open(os.path.join(base, "f.sh"), "w") as f:
        f.write("echo de otra sesion\n")
    git(base, "commit", "-q", "-am", "otra sesion")
    git(wt, "merge", "--no-edit", "master")          # choca: queda a medias en el worktree
    master_antes = git(base, "rev-parse", "master")
    env = dict(os.environ, BTP_REPO=base, BTP_GIT_BASE_OK="1",
               BTP_STATE_DIR=os.path.join(tmp, "state"))
    p = subprocess.run([sys.executable, TOOL, "--apply", "--no-poda"], cwd=wt, env=env,
                       capture_output=True, text=True, timeout=120)
    salida = p.stdout + p.stderr
    check(git(base, "rev-parse", "master") == master_antes, "master NO recibe el merge a medias")
    check("<<<<<<<" not in open(os.path.join(base, "f.sh")).read(),
          "casa base sin marcadores de conflicto")
    check("conflicto" in salida.lower() or "a medias" in salida.lower(),
          "la salida dice que hay un merge a medias: %r" % salida[-200:])



def fusion_ajena_a_medias():
    """24-sep-26: casa base tenía la fusión de OTRA sesión a medias (MERGE_HEAD, conflicto ya
    resuelto a mano y en el índice). El cierre de una rama cualquiera hacía `git merge`, fallaba
    por «no has concluido tu fusión» y respondía con `git merge --abort`: la resolución ajena se
    perdía y la tool decía «casa base sigue como estaba». Reproducido en repos de pega."""
    print("── fusión de OTRA sesión a medias en casa base: no se toca ──")
    tmp = tempfile.mkdtemp(prefix="cerrar_ajena_")
    base = os.path.join(tmp, "casa")
    os.makedirs(base)
    git(base, "init", "-q", "-b", "master")
    git(base, "config", "user.email", "t@t")
    git(base, "config", "user.name", "t")
    with open(os.path.join(base, ".gitignore"), "w") as f:
        f.write(".claude/worktrees/\n")
    for n, t in (("f.txt", "original\n"), ("g.txt", "g\n")):
        with open(os.path.join(base, n), "w") as f:
            f.write(t)
    git(base, "add", "-A")
    git(base, "commit", "-q", "-m", "inicio")
    # La otra sesión: su rama choca con master en f.txt.
    git(base, "checkout", "-q", "-b", "otra")
    with open(os.path.join(base, "f.txt"), "w") as f:
        f.write("de la otra\n")
    git(base, "commit", "-q", "-am", "otra")
    git(base, "checkout", "-q", "master")
    with open(os.path.join(base, "f.txt"), "w") as f:
        f.write("de master\n")
    git(base, "commit", "-q", "-am", "master")
    # Mi rama toca OTRO fichero: por sí sola fusionaría limpio.
    wt = os.path.join(base, ".claude", "worktrees", "mia")
    git(base, "worktree", "add", "-q", "-b", "claude/mia", wt)
    with open(os.path.join(wt, "g.txt"), "w") as f:
        f.write("g de mi rama\n")
    git(wt, "commit", "-q", "-am", "mia")
    # La otra sesión fusiona, choca y RESUELVE a mano: trabajo vivo, aún sin commitear.
    subprocess.run(["git", "-C", base, "merge", "otra"], capture_output=True)
    resolucion = "RESOLUCION A MANO de la otra sesion\n"
    with open(os.path.join(base, "f.txt"), "w") as f:
        f.write(resolucion)
    git(base, "add", "f.txt")
    merge_head = open(os.path.join(base, ".git", "MERGE_HEAD")).read()
    master_antes = git(base, "rev-parse", "master")

    env = dict(os.environ, BTP_REPO=base, BTP_GIT_BASE_OK="1",
               BTP_STATE_DIR=os.path.join(tmp, "state"))
    p = subprocess.run([sys.executable, TOOL, "--apply", "--no-poda"], cwd=wt, env=env,
                       capture_output=True, text=True, timeout=120)
    salida = p.stdout + p.stderr
    mh = os.path.join(base, ".git", "MERGE_HEAD")
    check(os.path.exists(mh) and open(mh).read() == merge_head,
          "la fusión de la otra sesión sigue a medias, con SU MERGE_HEAD")
    check(open(os.path.join(base, "f.txt")).read() == resolucion,
          "su resolución a mano sigue en el disco de casa base")
    check("f.txt" in git(base, "diff", "--cached", "--name-only"), "y sigue en el índice")
    check(git(base, "rev-parse", "master") == master_antes, "master no se ha movido")
    check("ocupada" in salida.lower() or "a medias" in salida.lower(),
          "la salida dice que casa base tiene una fusión de otro a medias: %r" % salida[-200:])
    check("sigue como estaba" not in salida, "no afirma que casa base «sigue como estaba»")
    check(git(wt, "log", "-1", "--format=%s") == "mia", "el trabajo de mi rama sigue intacto")


if __name__ == "__main__":
    sys.exit(main())
