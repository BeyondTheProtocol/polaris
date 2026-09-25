#!/usr/bin/env python3
"""tests/test_rodaje_hook_fijo.py — el rodaje juzga con una copia fija, no con el worktree.

POR QUÉ EXISTE (25-sep-2026, deuda `rodaje-muro-worktree-podado-antes-de-tiempo`, 2x). El estado
del rodaje apuntaba al hook DENTRO del worktree de la rama, y el árbol no aguanta las 24 h:
  1. `silly-chatelet-516735` se podó a media ventana → revisar habría dicho «no puede juzgar».
  2. la app RECICLÓ `great-mahavira-a54bcb` para otra rama → su session_start.sh pasó a ser el de
     master y el rodaje comparaba el viejo consigo mismo, SIN avisar.
Ahora `iniciar` copia los dos hooks (y los módulos hermanos que importan) a casa base con su
sha256, y `revisar` se niega a juzgar si la copia falta o cambió. Los estados de antes de las
copias avisan si su árbol se podó o cambió de commit.

De paso (deuda `replay-guard-aviso-zonas-clinicas-falso`): `replay_guard.comprueba()` solo avisa
de que falta `zonas_clinicas.py` si el hook la usa de verdad.
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))
import replay_guard as RG      # noqa: E402
import rodaje_muro as RM       # noqa: E402

fallos = 0


def ok(cond, desc, detalle=""):
    global fallos
    if not cond:
        fallos += 1
    print(("  ✅ " if cond else "  ❌ ") + desc + ("" if cond or not detalle else "  → %s" % detalle))


class Args:
    horas = 24
    nota = "test"


tmp = tempfile.mkdtemp(prefix="test-rodaje-fijo-")
guardado = RM.ESTADO
RM.ESTADO = os.path.join(tmp, "state", "rodaje_muro", "rama-de-prueba.json")
try:
    # Un «worktree» de usar y tirar con un hook que importa un módulo hermano.
    arbol = os.path.join(tmp, "worktree", ".claude", "hooks")
    os.makedirs(arbol)
    with open(os.path.join(arbol, "mi_guard.py"), "w") as fh:
        fh.write("import sys\nimport zonas_clinicas\nsys.exit(0)\n")
    with open(os.path.join(arbol, "zonas_clinicas.py"), "w") as fh:
        fh.write("ZONAS = []\n")
    with open(os.path.join(arbol, "viejo_guard.py"), "w") as fh:
        fh.write("import sys\nsys.exit(0)\n")

    a = Args()
    a.nuevo = os.path.join(arbol, "mi_guard.py")
    a.viejo = os.path.join(arbol, "viejo_guard.py")
    with contextlib.redirect_stdout(io.StringIO()):
        rc = RM.iniciar(a)
    ok(rc == 0, "iniciar funciona", "rc=%s" % rc)
    import json
    with open(RM.ESTADO) as fh:
        e = json.load(fh)
    ok("/worktree/" not in e["hook_nuevo"] and "/worktree/" not in e["hook_viejo"],
       "el estado apunta a copias FUERA del worktree", e["hook_nuevo"])
    ok(e.get("sha256_nuevo") and e.get("sha256_viejo"), "guarda la huella sha256 de las dos copias")
    ok(os.path.isfile(os.path.join(os.path.dirname(e["hook_nuevo"]), "zonas_clinicas.py")),
       "copia también el módulo hermano que el hook importa")
    ok(e["origen_nuevo"] == a.nuevo, "recuerda de dónde salió cada hook")

    # Caso 1: se poda el worktree a media ventana → sigue pudiendo juzgar.
    shutil.rmtree(os.path.join(tmp, "worktree"))
    ok(RM._no_puede_juzgar(e) == "", "podado el worktree, el rodaje SIGUE pudiendo juzgar",
       RM._no_puede_juzgar(e))

    # Alguien toca la copia → se niega a juzgar.
    with open(e["hook_nuevo"], "a") as fh:
        fh.write("# tocado\n")
    motivo = RM._no_puede_juzgar(e)
    ok("cambió" in motivo, "si la copia cambió, NO juzga", motivo or "lo dio por bueno")

    # Falta la copia → se niega a juzgar.
    os.remove(e["hook_viejo"])
    ok("falta la copia" in RM._no_puede_juzgar(e), "si falta una copia, NO juzga")

    # Estado ANTIGUO (sin huellas) cuyo árbol se podó → dice cómo recuperarlo.
    viejo_fmt = {"hook_nuevo": "/no/existe/salida_guard.py", "hook_viejo": "/no/existe/v.py",
                 "sha": "ffdad56", "arbol": "/no/existe"}
    motivo = RM._no_puede_juzgar(viejo_fmt)
    ok("git show ffdad56" in motivo, "estado antiguo con el árbol podado: dice cómo sacarlo del commit",
       motivo)

    # Caso 2: estado ANTIGUO cuyo árbol cambió de rama → el hook ya no es el del commit rodado.
    casa = os.path.join(tmp, "casa")
    os.makedirs(os.path.join(casa, ".claude", "hooks"))
    hook_casa = os.path.join(casa, ".claude", "hooks", "g.py")
    with open(hook_casa, "w") as fh:
        fh.write("print('arreglo')\n")
    git = ["git", "-C", casa, "-c", "user.name=t", "-c", "user.email=t@t.example"]
    subprocess.run(["git", "init", "-q", casa], check=True)
    subprocess.run(git + ["add", "."], check=True)
    subprocess.run(git + ["commit", "-qm", "arreglo"], check=True)
    sha = subprocess.run(git + ["rev-parse", "--short", "HEAD"], capture_output=True,
                         text=True).stdout.strip()
    old_repo = os.environ.get("BTP_REPO")
    os.environ["BTP_REPO"] = casa
    try:
        ant = {"hook_nuevo": hook_casa, "hook_viejo": hook_casa, "sha": sha, "arbol": casa}
        ok(RM._no_puede_juzgar(ant) == "", "estado antiguo con el hook intacto: juzga")
        with open(hook_casa, "w") as fh:
            fh.write("print('master')\n")          # el árbol se recicló para otra rama
        motivo = RM._no_puede_juzgar(ant)
        ok("ya no es el de" in motivo, "estado antiguo con el árbol reciclado: NO juzga (antes pasaba mudo)",
           motivo or "lo dio por bueno")
    finally:
        if old_repo is None:
            os.environ.pop("BTP_REPO", None)
        else:
            os.environ["BTP_REPO"] = old_repo

    # replay_guard.comprueba(): el aviso de zonas_clinicas solo si el hook la usa.
    solo = os.path.join(tmp, "solo")
    os.makedirs(solo)
    sin_zonas = os.path.join(solo, "salida_like.py")
    con_zonas = os.path.join(solo, "clinico_like.py")
    with open(sin_zonas, "w") as fh:
        fh.write("import sys\nsys.exit(0)\n")
    with open(con_zonas, "w") as fh:
        fh.write("import sys\ntry:\n    import zonas_clinicas\nexcept Exception:\n    pass\nsys.exit(0)\n")
    for hook, debe in ((sin_zonas, False), (con_zonas, True)):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            RG.comprueba(hook)
        avisa = "zonas_clinicas.py al lado" in err.getvalue()
        ok(avisa == debe, "comprueba(%s): %s" % (os.path.basename(hook),
                                                  "avisa (la usa)" if debe else "calla (no la usa)"),
           "avisó=%s" % avisa)
finally:
    RM.ESTADO = guardado
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("test_rodaje_hook_fijo: %d fallos" % fallos)
raise SystemExit(1 if fallos else 0)
