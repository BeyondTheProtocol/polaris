#!/usr/bin/env python3
"""test_run_agent_casa_master.py — un agente del lazo no deja casa base aparcada en otra rama.

POR QUÉ EXISTE (22-sep-2026, deuda `jobs-exec-sin-worktree-dejan-casa-base-parada`). Los jobs del
dispatcher y la auto-mejora corren en casa base (no pueden aislarse en worktree) y hacen
`git checkout -b` ahí. En 60 días casa base estuvo 27 veces fuera de master, una de ellas 10,5 h.
Mientras tanto los daemons leen el código de esa rama y `cerrar_sesion.py` fusionaba en ella.

Qué se fija aquí (`run_agent.sh`, trap de salida):
  1. si el agente empezó en master y lo deja en otra rama con el árbol LIMPIO → vuelve a master
     (la rama conserva sus commits: no se pierde nada);
  2. si lo deja con cambios SIN commitear → NO toca nada (son trabajo de alguien) y lo apunta;
  3. si hay OTRO run_agent vivo → NO toca nada: la rama puede ser la suya, a mitad de trabajo;
  4. el código de salida del agente no cambia.
Casa base es un repo de usar y tirar (BTP_CASA_GIT); nunca se toca la de verdad.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="ra_casa_")
fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def git(cwd, *a):
    return subprocess.run(["git", "-C", cwd] + list(a), capture_output=True, text=True).stdout.strip()


def casa_nueva(nombre):
    c = os.path.join(TMP, nombre)
    os.makedirs(c)
    git(c, "init", "-q", "-b", "master")
    git(c, "config", "user.email", "t@t")
    git(c, "config", "user.name", "t")
    open(os.path.join(c, "f.txt"), "w").write("x\n")
    git(c, "add", "-A")
    git(c, "commit", "-q", "-m", "inicio")
    return c


def claude_falso(nombre, cuerpo, rc=0):
    """Un `claude` que hace lo que haría un job (cuerpo de bash) y responde éxito."""
    p = os.path.join(TMP, nombre + ".sh")
    open(p, "w").write("#!/bin/bash\n" + cuerpo + "\n"
                       "echo '{\"subtype\":\"success\",\"is_error\":false,\"total_cost_usd\":0.001,"
                       "\"result\":\"ok\"}'\nexit %d\n" % rc)
    os.chmod(p, 0o755)
    return p


def correr(casa, bin_, extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("BTP_") and k != "MURO_PROFILE"}
    st = os.path.join(TMP, "state-" + os.path.basename(casa))
    env.update(BTP_CLAUDE_BIN=bin_, BTP_API_KEY_OVERRIDE="x", BTP_COST_GUARDED="1",
               BTP_STATE_DIR=st, BTP_AGENT="tecnico", BTP_MODEL="sonnet", BTP_REPO=ROOT,
               BTP_CASA_GIT=casa, BTP_DEUDA_OFF="1", BTP_OTROS_AGENTES="0",
               BTP_HALT_FILES=os.path.join(TMP, "nh_a") + ":" + os.path.join(TMP, "nh_b"))
    env.update(extra or {})
    p = subprocess.run(["bash", os.path.join(ROOT, "tools", "run_agent.sh"), "haz algo"],
                       capture_output=True, text=True, env=env, timeout=120)
    return p


def return_a_secas():
    """Líneas de run_agent.sh con un `return` sin número. En bash >= 4.4, dentro de la trampa EXIT
    devuelve el estado de antes de la trampa (CI de Linux rojo, 22-sep); el bash 3.2 del Mac no lo
    reproduce, así que la parte dinámica de este test pasa en casa aunque vuelva el fallo."""
    import re
    patron = re.compile(r"(^|[;&|{(\s])return\s*($|[;}#)]|&&|\|\|)")
    malas = []
    for n, linea in enumerate(open(os.path.join(ROOT, "tools", "run_agent.sh")), 1):
        if not linea.lstrip().startswith("#") and patron.search(linea):
            malas.append("%d: %s" % (n, linea.strip()))
    return malas


def main():
    print("── estático: ningún `return` a secas en run_agent.sh ──")
    malas = return_a_secas()
    check(not malas, "todo `return` lleva número (bash >= 4.4 en trampa EXIT): %s" % malas)

    print("── limpio: vuelve a master ──")
    c = casa_nueva("limpia")
    b = claude_falso("aparca", 'git -C "$BTP_CASA_GIT" checkout -q -b docs/arquitectura-mapa\n'
                               'echo y > "$BTP_CASA_GIT/g.txt"; git -C "$BTP_CASA_GIT" add -A; '
                               'git -C "$BTP_CASA_GIT" commit -q -m trabajo')
    p = correr(c, b)
    check(git(c, "branch", "--show-current") == "master", "casa base vuelve a master")
    check(git(c, "log", "-1", "--format=%s", "docs/arquitectura-mapa") == "trabajo",
          "la rama del job conserva su commit")
    check("docs/arquitectura-mapa" in p.stderr, "lo dice (qué rama había): %r" % p.stderr[-200:])
    check(p.returncode == 0, "el código de salida del agente no cambia (rc=%d)" % p.returncode)

    print("── con cambios sin commitear: no toca nada ──")
    c = casa_nueva("sucia")
    b = claude_falso("aparca_sucio", 'git -C "$BTP_CASA_GIT" checkout -q -b job-a-medias\n'
                                     'echo cambio >> "$BTP_CASA_GIT/f.txt"')
    p = correr(c, b)
    check(git(c, "branch", "--show-current") == "job-a-medias", "se queda en la rama (no pierde nada)")
    check("cambio" in open(os.path.join(c, "f.txt")).read(), "los cambios siguen en disco")
    check("sin commitear" in p.stderr, "avisa de que la deja aparcada y por qué")

    print("── otro run_agent vivo: no toca nada ──")
    c = casa_nueva("concurrente")
    b = claude_falso("aparca2", 'git -C "$BTP_CASA_GIT" checkout -q -b auto-mejora-hoy')
    p = correr(c, b, {"BTP_OTROS_AGENTES": "1"})
    check(git(c, "branch", "--show-current") == "auto-mejora-hoy", "la rama de otro se respeta")
    check("otro agente" in p.stderr, "dice por qué no la devuelve")

    print("── empezó fuera de master: no es suya, no la toca ──")
    c = casa_nueva("ya_aparcada")
    git(c, "checkout", "-q", "-b", "de-antes")
    b = claude_falso("nada", "true")
    correr(c, b)
    check(git(c, "branch", "--show-current") == "de-antes", "no mueve una rama que ya estaba")

    print("── rc del agente intacto aunque falle ──")
    c = casa_nueva("falla")
    b = claude_falso("falla", 'git -C "$BTP_CASA_GIT" checkout -q -b rota', rc=3)
    p = correr(c, b)
    check(git(c, "branch", "--show-current") == "master", "vuelve a master también si el job falla")

    if fallos:
        print("❌ %d fallo(s)" % len(fallos))
        return 1
    print("✅ RUN_AGENT CASA EN MASTER EN VERDE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
