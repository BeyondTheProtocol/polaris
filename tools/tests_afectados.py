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
    """Salida de git. Un fallo SUBE (antes se ignoraba el rc: con una base inexistente salía una
    lista vacía, o sea «no hay cambios», y la puerta se abría)."""
    p = subprocess.run(["git", "-c", "core.quotepath=false", "-C", RAIZ] + list(args),
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError("git %s falló (rc=%d): %s" % (" ".join(args[:2]), p.returncode,
                                                         (p.stderr or "").strip()[:200]))
    return p.stdout


def cambiados(base="master"):
    """Ficheros tocados por la rama frente a `base`, más lo no commiteado. Levanta RuntimeError si
    git no puede decirlo (base inexistente, sin merge-base…): quien llama NO debe concluir «nada»."""
    _git("rev-parse", "--verify", "--quiet", "%s^{commit}" % base)
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


# ── PUERTA DE FUSIÓN POR IMPACTO (10-oct-26) ──────────────────────────────────────────────────
# `--cambiados` sirve para ITERAR. La puerta decide si un cambio puede fusionar sin la suite
# entera, y FALLA CERRADA (revisión de consejero-arquitectura, 10-oct-26): COMPLETA es la respuesta
# por defecto; RAPIDA solo para lo que está EXPLÍCITAMENTE en una lista de seguros. Una lista de
# peligros deja pasar lo que nadie pensó (.mcp.json, un plist, un fichero nuevo, un test editado).
#
# SEGURO (lista blanca), y solo si además el fichero no está en el cierre de tools del muro:
#   · un .md fuera de CLAUDE.md y .claude/ (documentación);
#   · un test `tests/test_*.py|sh` REGISTRADO en test_all.sh y que no sea batería del muro;
#   · una tool `tools/<x>.py` que ya existía en la base, que no es del muro (ni directa ni
#     transitivamente) y no está en la lista de tools por rol.
# TODO lo demás (json, plist, .sh, ficheros nuevos, borrados, .claude/**, .github/**, .mcp.json…)
# exige la completa, con el motivo concreto o «no clasificado como seguro».
_PUERTA_COMPLETA_FIJOS = (
    "tests/test_all.sh", "tools/tests_afectados.py", "tools/dependencias.py", "tools/mutantes.py",
    "tools/normas.json", "tools/deuda.py", "tools/cerrar_sesion.py", "tools/git_mutex.py",
    "tools/ramas.py", "tools/salida.py", "tools/enruta.py", "tools/deid.py", "tools/codigo_rojo.py",
    "tests/rojos_conocidos.json",
)
# Tools del muro POR ROL (aunque ningún hook las importe hoy): egress 0, puertas de N1, jaulas,
# identidad del paciente. Se suman al cierre calculado.
_PUERTA_TOOLS_POR_ROL = (
    "tools/local.py", "tools/nube_n1.py", "tools/vision_n1.py", "tools/exporta_n1.py",
    "tools/puerta_n1.py", "tools/identidad_paciente.py", "tools/laminillas_jaulas.py",
    "tools/lector_clinico.py",
)


def _refs_de_tool(ruta, existentes):
    """Tools que `ruta` importa (en cualquier nivel, también perezosos) o nombra como «x.py» en una
    cadena. Con `ast`, no con regex sobre el texto: los comentarios no cuentan, los import dentro de
    una función sí. Si no parsea, cae a regex (más ancho: ante la duda, se incluye)."""
    import ast
    import re
    try:
        texto = open(ruta, encoding="utf-8", errors="replace").read()
    except Exception:
        return set()
    nombres = set()
    try:
        arbol = ast.parse(texto)
    except SyntaxError:
        nombres |= set(re.findall(r"^\s*(?:import|from)\s+([A-Za-z0-9_]+)", texto, re.M))
        nombres |= set(re.findall(r"([A-Za-z0-9_]+)\.py\b", texto))
        return nombres & existentes
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            nombres |= {a.name.split(".")[0] for a in nodo.names}
        elif isinstance(nodo, ast.ImportFrom):
            if nodo.module:
                nombres.add(nodo.module.split(".")[0])
            nombres |= {a.name for a in nodo.names}      # `from tools import x`
        elif isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            nombres |= set(re.findall(r"([A-Za-z0-9_]+)\.py\b", nodo.value))
            if re.fullmatch(r"[A-Za-z0-9_]+", nodo.value):   # importlib.import_module("x")
                nombres.add(nodo.value)
    return nombres & existentes


_CACHE_MURO = {}


def tools_de_hooks(raiz=RAIZ, transitivo=True):
    """Con caché por (raiz, transitivo): el cierre parsea ~280 ficheros y la puerta lo pide una vez
    por fichero cambiado (sin caché, test_tests_afectados pasó de 1 s a 7 min)."""
    k = (os.path.realpath(raiz), transitivo)
    if k not in _CACHE_MURO:
        _CACHE_MURO[k] = frozenset(_calcula_tools_de_hooks(raiz, transitivo))
    return set(_CACHE_MURO[k])


def _calcula_tools_de_hooks(raiz=RAIZ, transitivo=True):
    """tools/*.py del muro: las que los hooks importan o citan por ruta (nivel 1) Y TODO lo que
    esas tools importan, a cualquier profundidad (cierre transitivo). Con un solo nivel quedaban
    fuera tools alcanzables desde un hook (identidad_paciente, puerta_n1, laminillas_jaulas…) y
    tocarlas pasaba por la puerta rápida. Se calcula, no se lista a mano."""
    import re
    existentes = {f[:-3] for f in os.listdir(os.path.join(raiz, "tools")) if f.endswith(".py")}
    carpeta = os.path.join(raiz, ".claude", "hooks")
    hallados = set()
    for nombre in sorted(os.listdir(carpeta)) if os.path.isdir(carpeta) else []:
        try:
            texto = open(os.path.join(carpeta, nombre), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        hallados |= set(re.findall(r"([A-Za-z0-9_]+)\.py\b", texto))
        hallados |= set(re.findall(r"^\s*(?:import|from)\s+([A-Za-z0-9_]+)", texto, re.M))
    cierre = hallados & existentes
    if transitivo:
        pendientes = list(cierre)
        while pendientes:
            t = pendientes.pop()
            for r in _refs_de_tool(os.path.join(raiz, "tools", t + ".py"), existentes):
                if r not in cierre:
                    cierre.add(r)
                    pendientes.append(r)
    return {"tools/%s.py" % n for n in cierre}


def motivo_completa(f, muro_tools, rastreados, disponibles, raiz=RAIZ):
    """None si `f` es SEGURO; si no, el motivo por el que la puerta exige la suite COMPLETA."""
    base = os.path.basename(f)
    if f.startswith(".claude/"):
        if f.startswith(".claude/hooks/"):
            return "hook del muro"
        if base.startswith("settings"):
            return "settings del harness"
        return "constitución, reglas, agentes, skills o configuración: los leen tests y el harness"
    if f == "CLAUDE.md":
        return "constitución: la leen tests y el harness"
    if f in _PUERTA_COMPLETA_FIJOS:
        return "runner, gate o herramienta que decide qué se prueba"
    if f in muro_tools:
        return "tool del muro (la usa un hook, directa o transitivamente)"
    if f in _PUERTA_TOOLS_POR_ROL:
        return "tool del muro por rol (egress, N1, jaulas, identidad)"
    if not os.path.exists(os.path.join(raiz, f)):
        return "fichero borrado o renombrado"
    if f.endswith(".md"):
        return None
    if f.startswith("tests/") and base.startswith("test_") and base.endswith((".py", ".sh")) and "/" not in f[6:]:
        if base.startswith(_MURO_PREFIJOS):
            return "batería del muro editada: debilitarla no se vería"
        if base not in disponibles:
            return "test sin registrar en test_all.sh (nadie lo corre)"
        return None
    if f.startswith("tools/") and f.endswith(".py") and "/" not in f[6:]:
        if f not in rastreados:
            return "tool nueva sin clasificar"
        return None
    return "no clasificado como seguro"


def exige_completa(ficheros, muro_tools=None, rastreados=None, disponibles=None, raiz=RAIZ):
    """[(fichero, motivo)] de los cambios que obligan a la suite COMPLETA antes de fusionar."""
    if muro_tools is None:
        muro_tools = tools_de_hooks(raiz)
    if rastreados is None:
        if "HEAD" not in _CACHE_MURO:
            _CACHE_MURO["HEAD"] = frozenset(_git("ls-tree", "-r", "--name-only", "HEAD").splitlines())
        rastreados = _CACHE_MURO["HEAD"]
    if disponibles is None:
        disponibles = baterias()
    out = []
    for f in ficheros:
        m = motivo_completa(f, muro_tools, rastreados, disponibles, raiz)
        if m:
            out.append((f, m))
    return out


def nucleo_muro(disponibles):
    """Núcleo fijo del muro: corre SIEMPRE en la puerta rápida, lo haya tocado el cambio o no."""
    return {b for b in disponibles if b.startswith(_MURO_PREFIJOS)}


def puerta(ficheros, disponibles, grafo=None, muro_tools=None, rastreados=None, raiz=RAIZ):
    """{'veredicto': 'COMPLETA'|'RAPIDA', 'motivos': [...], 'seleccion': set}"""
    motivos = exige_completa(ficheros, muro_tools, rastreados, disponibles, raiz)
    if motivos:
        return {"veredicto": "COMPLETA", "motivos": motivos, "seleccion": set(disponibles)}
    _todo, sel = afectados(ficheros, disponibles, grafo)
    return {"veredicto": "RAPIDA", "motivos": [], "seleccion": sel | nucleo_muro(disponibles)}


def _rastreados_en(base):
    return set(_git("ls-tree", "-r", "--name-only", base).splitlines())


def main(argv):
    base = argv[argv.index("--base") + 1] if "--base" in argv else "master"
    modo_puerta = "--puerta" in argv
    try:
        ficheros = cambiados(base)
        disponibles = baterias()
        if not disponibles:
            raise RuntimeError("test_all.sh no lista ninguna batería")
        if modo_puerta:
            r = puerta(ficheros, disponibles, rastreados=_rastreados_en(base))
        else:
            todo, sel = afectados(ficheros, disponibles)
    except (Exception, SystemExit) as e:        # FALLA CERRADA: lo que no se pudo decidir va completo
        print("COMPLETA" if modo_puerta else "TODO")
        if modo_puerta:
            print("# puerta no concluyente: %s" % e)
        return 0
    if modo_puerta:
        if r["veredicto"] == "COMPLETA":
            print("COMPLETA")
            for f, m in r["motivos"]:
                print("# %s: %s" % (f, m))
        else:
            print("RAPIDA")
            for b in sorted(r["seleccion"]):
                print(b)
        return 0
    if todo:
        print("TODO")
    elif not sel:
        print("NINGUNA")
    for b in sorted(sel):
        print(b)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
