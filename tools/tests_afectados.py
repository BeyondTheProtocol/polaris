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
     python3 tools/tests_afectados.py --puerta           → la PUERTA DE FUSIÓN por impacto (10-oct-26,
     propuesta): primera línea «COMPLETA» (con los motivos como comentarios `# …`) o «RAPIDA» (y debajo
     las afectadas + el núcleo fijo del muro). No cambia `--cambiados`.
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


# ── PUERTA DE FUSIÓN POR IMPACTO (10-oct-26, propuesta) ──────────────────────────────────────
# `--cambiados` sirve para ITERAR. La puerta decide si un cambio puede fusionar sin la suite
# entera. Regla: lo que PUEDE romper el muro sin que ningún grafo lo vea exige la completa. El
# resto, las afectadas + un núcleo fijo del muro. La completa corre además cada noche sobre casa
# base (la rutina se propone aparte, no se instala aquí).
#
# FICHEROS FIJOS que exigen la completa, además de todo lo de `.claude/hooks/` y `settings*`:
_PUERTA_COMPLETA_FIJOS = (
    "tests/test_all.sh", "tools/tests_afectados.py", "tools/dependencias.py", "tools/mutantes.py",
    "tools/normas.json", "tools/deuda.py", "tools/cerrar_sesion.py", "tools/git_mutex.py",
    "tools/ramas.py", "tools/salida.py", "tools/enruta.py", "tools/deid.py", "tools/codigo_rojo.py",
)


def tools_de_hooks(raiz=RAIZ):
    """tools/*.py que los hooks importan o citan por ruta: la parte de tools/ que ES el muro.
    Se calcula, no se lista a mano: un hook que empieza a usar una tool la mete en el muro solo."""
    import re
    existentes = {f[:-3] for f in os.listdir(os.path.join(raiz, "tools")) if f.endswith(".py")}
    carpeta = os.path.join(raiz, ".claude", "hooks")
    hallados = set()
    for nombre in sorted(os.listdir(carpeta)) if os.path.isdir(carpeta) else []:
        try:
            texto = open(os.path.join(carpeta, nombre), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for m in re.finditer(r"tools/([A-Za-z0-9_]+)\.py", texto):
            hallados.add(m.group(1))
        for m in re.finditer(r"^\s*(?:import|from)\s+([A-Za-z0-9_]+)", texto, re.M):
            hallados.add(m.group(1))
    return {"tools/%s.py" % n for n in hallados & existentes}


def exige_completa(ficheros, muro_tools=None):
    """[(fichero, motivo)] de los cambios que obligan a la suite COMPLETA antes de fusionar."""
    if muro_tools is None:
        muro_tools = tools_de_hooks()
    motivos = []
    for f in ficheros:
        base = os.path.basename(f)
        if f.startswith(".claude/hooks/"):
            motivos.append((f, "hook del muro"))
        elif f.startswith(".claude/") and base.startswith("settings"):
            motivos.append((f, "settings del harness"))
        elif f in _PUERTA_COMPLETA_FIJOS:
            motivos.append((f, "runner, gate o herramienta que decide qué se prueba"))
        elif f in muro_tools:
            motivos.append((f, "tool que usa un hook del muro"))
        elif f == "CLAUDE.md" or f.startswith((".claude/rules/", ".claude/agents/", ".claude/skills/")):
            # Son .md: el grafo no los ve y «solo documentación» no corre nada, pero los LEEN tests
            # (constitución, frontmatter de agentes, coherencia de modelos) y el harness los carga.
            motivos.append((f, "constitución, reglas, agentes o skills: los leen tests y el harness"))
        elif f.startswith("tests/") and base.startswith("_"):
            motivos.append((f, "ayudante compartido por muchas baterías"))
        elif f.startswith("tools/config/") or (f.startswith("tools/launchd/") and f.endswith(".json")):
            motivos.append((f, "política o registro que el muro lee"))
    return motivos


def nucleo_muro(disponibles):
    """Núcleo fijo del muro: corre SIEMPRE en la puerta rápida, lo haya tocado el cambio o no."""
    return {b for b in disponibles if b.startswith(_MURO_PREFIJOS)}


def puerta(ficheros, disponibles, grafo=None, muro_tools=None):
    """{'veredicto': 'COMPLETA'|'RAPIDA', 'motivos': [...], 'seleccion': set}"""
    motivos = exige_completa(ficheros, muro_tools)
    if motivos:
        return {"veredicto": "COMPLETA", "motivos": motivos, "seleccion": set(disponibles)}
    _todo, sel = afectados(ficheros, disponibles, grafo)
    return {"veredicto": "RAPIDA", "motivos": [], "seleccion": sel | nucleo_muro(disponibles)}


def main(argv):
    base = argv[argv.index("--base") + 1] if "--base" in argv else "master"
    if "--puerta" in argv:
        r = puerta(cambiados(base), baterias())
        if r["veredicto"] == "COMPLETA":
            print("COMPLETA")
            for f, m in r["motivos"]:
                print("# %s: %s" % (f, m))
        else:
            print("RAPIDA")
            for b in sorted(r["seleccion"]):
                print(b)
        return 0
    todo, sel = afectados(cambiados(base), baterias())
    if todo:
        print("TODO")
    for b in sorted(sel):
        print(b)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
