#!/usr/bin/env python3
"""test_git_mutex_rama_ajena.py — a casa base solo llega la rama de la sesión que fusiona.

POR QUÉ EXISTE (26-sep-2026, deuda `fusion-de-rama-ajena-sin-ok`). El 25-sep la rama
`claude/serene-davinci-4f48b4`, que cambiaba un hook del muro, llegó dos veces a casa base desde
OTRA sesión (1a22fe8 a las 19:55, sin rodaje ni verificación adversarial; b3c8462 a las 20:31),
sin el OK que {{TITULAR}} da en la sesión dueña. `git_mutex` solo miraba BTP_GIT_BASE_OK=1.

Todo con worktrees y sesiones INYECTADOS: nada toca el .git real ni lanza un merge.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import git_mutex as gm  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


BASE = "/casa"
WT_A = "/casa/.claude/worktrees/a"
WT_B = "/casa/.claude/worktrees/b"
WTS = [{"path": BASE, "branch": "master", "es_base": True},
       {"path": WT_A, "branch": "claude/a", "es_base": False},
       {"path": WT_B, "branch": "claude/b", "es_base": False}]
SES = [{"pid": 101, "cwd": WT_A, "rama": "claude/a"},
       {"pid": 202, "cwd": BASE, "rama": "casa base"}]


def ajena(argv, cwd, ses=SES, wts=WTS):
    real = os.path.realpath
    os.path.realpath = lambda p: p            # rutas ficticias: sin resolver enlaces
    try:
        return gm._rama_ajena(argv, cwd=cwd, _sesiones=ses, _worktrees=wts)
    finally:
        os.path.realpath = real


def main():
    os.environ.pop("BTP_FUSION_AJENA_OK", None)
    m = ["-C", BASE, "merge", "--no-ff", "claude/a", "-m", "merge(claude/a): x"]

    r = ajena(m, cwd=WT_B)
    check("rama con sesión viva AJENA → se rechaza y dice de quién es (%s)" % r,
          "otra sesión viva" in r and "101" in r)
    check("…también desde casa base, si la sesión dueña sigue viva", ajena(m, cwd=BASE) != "")
    check("desde su PROPIO worktree → pasa", ajena(m, cwd=WT_A) == "")
    check("desde un subdirectorio de su worktree → pasa", ajena(m, cwd=WT_A + "/tools") == "")

    m_b = ["-C", BASE, "merge", "--no-ff", "claude/b"]
    check("rama con worktree pero SIN sesión viva → como hoy (pasa)", ajena(m_b, cwd=BASE) == "")
    check("rama sin worktree (ya podado) → pasa", ajena(["-C", BASE, "merge", "claude/zzz"], cwd=BASE) == "")

    os.environ["BTP_FUSION_AJENA_OK"] = "claude/otra"
    try:
        check("override que nombra OTRA rama → se sigue rechazando", ajena(m, cwd=WT_B) != "")
        os.environ["BTP_FUSION_AJENA_OK"] = "claude/a"
        check("override con el nombre EXACTO de la rama → pasa", ajena(m, cwd=WT_B) == "")
        os.environ["BTP_FUSION_AJENA_OK"] = "1"
        check("override genérico «1» → no vale", ajena(m, cwd=WT_B) != "")
    finally:
        os.environ.pop("BTP_FUSION_AJENA_OK", None)

    check("el mensaje -m no se confunde con una rama",
          gm._ramas_del_merge(["--no-ff", "claude/a", "-m", "claude/b"]) == ["claude/a"])
    check("merge --abort no se juzga", ajena(["-C", BASE, "merge", "--abort"], cwd=WT_B) == "")
    check("lo que no es merge no se juzga", ajena(["-C", BASE, "status"], cwd=WT_B) == "")

    # Si no se puede saber de quién es la rama → se rechaza (fail-closed).
    import types
    roto = types.ModuleType("ramas")
    roto.worktrees = lambda: (_ for _ in ()).throw(RuntimeError("lsof no responde"))
    roto.sesiones = roto.worktrees
    real_mod = sys.modules.get("ramas")
    sys.modules["ramas"] = roto
    try:
        r = gm._rama_ajena(m, cwd=WT_B)
        check("sin poder saber de quién es → rechazo (%s)" % r, r.startswith("no pude saber"))
    finally:
        if real_mod is not None:
            sys.modules["ramas"] = real_mod
        else:
            sys.modules.pop("ramas", None)

    # main(): el camino entero, con el gate encendido y BTP_GIT_BASE_OK=1, NO lanza el merge.
    orig = (gm._mueve_la_base, gm._gate_encendido, gm._rama_ajena, gm.run)
    lanzados = []
    gm._mueve_la_base = lambda argv: "merge sobre master de casa base"
    gm._gate_encendido = lambda: True
    gm._rama_ajena = lambda argv: "la rama claude/a es de otra sesión viva (pid 101, en %s)" % WT_A
    gm.run = lambda *a, **k: lanzados.append(a) or (0, "", "")
    os.environ["BTP_GIT_BASE_OK"] = "1"
    try:
        rc = gm.main(m)
        check("main: rama ajena con BTP_GIT_BASE_OK=1 → rc 3 y sin merge", rc == 3 and lanzados == [])
    finally:
        gm._mueve_la_base, gm._gate_encendido, gm._rama_ajena, gm.run = orig
        os.environ.pop("BTP_GIT_BASE_OK", None)

    print("test_git_mutex_rama_ajena: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
