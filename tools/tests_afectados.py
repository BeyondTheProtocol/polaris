#!/usr/bin/env python3
"""tests_afectados.py — qué baterías de tests/test_all.sh tocan los ficheros cambiados de la rama.

POR QUÉ EXISTE (26-sep-26). `tests/test_all.sh` corre 377 baterías en serie y tarda más de
10 minutos. Varias sesiones la lanzaban a la vez para comprobaciones intermedias, en una máquina
que ya iba justa (ese día, con Chrome huérfanos comiéndose 6 núcleos). Para saber si lo que
acabas de tocar sigue en pie no hacen falta las 377: basta con las que dependen de ello.

NO SUSTITUYE A LA SUITE COMPLETA. Antes de fusionar a casa base se corre `test_all.sh` entero,
como siempre. Esto es para ir más rápido mientras se trabaja: `bash tests/test_all.sh --cambiados`.

Qué entra (unión):
  · los tests que la rama cambió directamente;
  · los tests que dependen DIRECTAMENTE de un fichero cambiado (`tools/dependencias.py`, grafo
    de imports y rutas). Lo indirecto lo cubre la suite completa antes de fusionar;
  · los tests que NOMBRAN el fichero cambiado (lo que el grafo no ve: `.mjs`, rutas a trozos);
  · si se tocó un hook (`.claude/hooks/`) o `settings*.json`, todas las baterías del muro:
    ahí un fallo no se ve en el grafo y es justo lo que no se puede romper;
  · si se tocó `tests/test_all.sh`, todo (no hay forma honesta de acotarlo).
Cambios solo en `.md`, memorias o docs: ninguna batería.

Uso: python3 tools/tests_afectados.py [--base master]   → nombres de batería, uno por línea
     (con «TODO» en la primera línea si hay que correrlo todo).
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(HERE)
sys.path.insert(0, HERE)

# Baterías que se corren siempre que se toque el muro (hooks o settings).
_MURO_PREFIJOS = ("test_muro", "test_salida_guard", "test_ok_envio", "test_permiso", "test_gate",
                  "test_fuga", "test_halt", "test_clinico", "test_casa_base", "test_singleton",
                  "test_rama_vista", "test_launch_loopback", "test_copy_web", "test_token_rotacion",
                  "test_regla_en_accion", "test_entrada_guard", "test_canario_muro",
                  "test_worktree_guard", "test_enrutado")


def _git(*args):
    return subprocess.run(["git", "-C", RAIZ] + list(args), capture_output=True, text=True,
                          timeout=30).stdout


def cambiados(base="master"):
    """Ficheros tocados por la rama frente a `base`, más lo no commiteado."""
    out = _git("diff", "--name-only", "%s...HEAD" % base)
    out += _git("diff", "--name-only", "HEAD")
    out += _git("ls-files", "--others", "--exclude-standard")
    return sorted({l.strip() for l in out.splitlines() if l.strip()})


def baterias():
    """Nombres de batería que test_all.sh sabe correr (los que aparecen en él)."""
    try:
        texto = open(os.path.join(RAIZ, "tests", "test_all.sh"), encoding="utf-8").read()
    except Exception:
        return set()
    return {f for f in os.listdir(os.path.join(RAIZ, "tests"))
            if f.startswith("test_") and f.endswith((".py", ".sh")) and f in texto}


def afectados(ficheros, disponibles, grafo=None):
    """(todo, set de baterías)."""
    if any(f == "tests/test_all.sh" for f in ficheros):
        return True, set(disponibles)
    sel = set()
    toca_muro = any(f.startswith(".claude/hooks/")
                    or (f.startswith(".claude/") and os.path.basename(f).startswith("settings"))
                    for f in ficheros)
    if toca_muro:
        sel |= {b for b in disponibles if b.startswith(_MURO_PREFIJOS)}
    codigo = [f for f in ficheros if f.endswith((".py", ".sh", ".mjs", ".js", ".json"))
              and not f.startswith("tests/")]
    sel |= {os.path.basename(f) for f in ficheros if f.startswith("tests/")} & disponibles
    if codigo:
        if grafo is None:
            import dependencias
            grafo = dependencias.Grafo()
        for f in codigo:
            # Dependientes DIRECTOS: con `hondo=True` cualquier tool arrastraba 358 de 386
            # baterías (todo cuelga de healthcheck), y el modo no ahorraba nada. `buscar` hace
            # sys.exit con lo que no conoce (un .mjs): se atrapa, no tumba la selección.
            try:
                dst = grafo.buscar(f)
                deps = grafo.quien(dst, hondo=False) if dst else {}
            except (Exception, SystemExit):
                deps = {}
            sel |= {os.path.basename(d) for d in deps if d.startswith("tests/")} & disponibles
        # Lo que el grafo no ve (un .mjs, una ruta armada a trozos): los tests que NOMBRAN el
        # fichero cambiado. Sin esto, tocar captura_visor.mjs o _chrome_headless.mjs no corría nada.
        nombres = {os.path.basename(f) for f in codigo}
        for b in disponibles:
            try:
                texto = open(os.path.join(RAIZ, "tests", b), encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            if any(n in texto for n in nombres):
                sel.add(b)
    return False, sel


def main(argv):
    base = argv[argv.index("--base") + 1] if "--base" in argv else "master"
    todo, sel = afectados(cambiados(base), baterias())
    if todo:
        print("TODO")
    for b in sorted(sel):
        print(b)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
