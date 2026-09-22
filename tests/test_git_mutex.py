#!/usr/bin/env python3
"""test_git_mutex.py — regresión del FIX 1 de A1 (10-jul-26, hallazgo B): candado ÚNICO
"git-mutex" para TODA mutación del `.git` de la casa base.

Antes de este fix: `cerrar_sesion.py` solo tomaba un candado ("fusion") alrededor del `merge`;
la PODA del worktree (dos bloques después) mutaba SIN candado; y `tools/ramas.py` (`limpia`/
`limpia --si`) mutaba `worktree prune`/`worktree remove` sin candado alguno. Tres actores sobre
el MISMO `.git` compartido, solo uno protegido en una sola de sus operaciones — la carrera que
el comité de arquitectura encontró (A1). Este test prueba el código REAL (no la estructura):
monkeypatchea el punto exacto donde cada módulo invoca `git` para comprobar, en tiempo de
ejecución, que el candado compartido `tools/git_mutex.py` ("git-mutex") está tomado.

CERO git real: todo `subprocess.run(["git", ...])` se sustituye por un doble de prueba (nunca se
ejecuta el binario `git`); el estado del candado se aísla con BTP_STATE_DIR a un tmp propio (no
toca `tools/state/locks/` de la casa base real).
"""
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")

# Aislar el candado (mkdir en tools/state/locks/) del estado REAL antes de importar nada que lo use.
# realpath() en la CREACIÓN (no solo al comparar) evita que un symlink de sistema (p.ej. /tmp en
# macOS) desincronice las comparaciones de rutas más abajo con lo que cerrar_sesion.py/ramas.py
# calculan internamente (ellos SIEMPRE aplican realpath a lo que reciben de "git").
_TMP_STATE = os.path.realpath(tempfile.mkdtemp(prefix="git_mutex_test_state_"))
os.environ["BTP_STATE_DIR"] = _TMP_STATE
_TMP_BASE = os.path.realpath(tempfile.mkdtemp(prefix="git_mutex_test_base_"))
os.environ["BTP_REPO"] = _TMP_BASE

sys.path.insert(0, TOOLS)
import _lock          # noqa: E402
import git_mutex       # noqa: E402
import cerrar_sesion    # noqa: E402
import ramas            # noqa: E402

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


class _FakeCompleted:
    def __init__(self, rc=0, out="", err=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


# ---------------------------------------------------------------------------------------------
# 1) git_mutex.run() serializa invocaciones concurrentes bajo el candado compartido "git-mutex".
# ---------------------------------------------------------------------------------------------
def test_git_mutex_serializa():
    intervals = []
    intervals_lk = threading.Lock()

    def fake_run(args, capture_output=True, text=True, timeout=None):
        t0 = time.time()
        time.sleep(0.15)
        t1 = time.time()
        with intervals_lk:
            intervals.append((t0, t1))
        return _FakeCompleted(0, "ok", "")

    orig = git_mutex.subprocess.run
    git_mutex.subprocess.run = fake_run
    try:
        threads = [threading.Thread(target=lambda: git_mutex.run(["status"])) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        ok(len(intervals) == 3, "las 3 invocaciones concurrentes completaron")
        intervals.sort()
        sin_solape = all(intervals[i][1] <= intervals[i + 1][0] for i in range(len(intervals) - 1))
        ok(sin_solape, "git_mutex.run() serializa (ningún intervalo se solapa): %r" % (intervals,))
    finally:
        git_mutex.subprocess.run = orig


# ---------------------------------------------------------------------------------------------
# 2) git_mutex CLI: si el candado ya está tomado, aplaza (rc=75), NUNCA muta sin candado.
# ---------------------------------------------------------------------------------------------
def test_git_mutex_cli_aplaza_en_timeout():
    """El CLI (main()) traduce un TimeoutError de run() en rc=75 (EX_TEMPFAIL: aplázalo, como
    cola.py) y NUNCA imprime un resultado como si la mutación hubiera corrido. Mockeamos run()
    directamente (no el candado real): _lock.py reclama por EDAD del directorio del lock, no por
    vida del proceso — un probe que empieza a esperar DESPUÉS de que el candado ya existe verá
    siempre `dir_age >= su_propia_espera`, así que con timeouts cortos SIEMPRE reclama en vez de
    lanzar (comportamiento correcto y preexistente de _lock.py, fuera del alcance de este fix).
    Lo que SÍ es contrato de ESTE módulo es: si run() lanza, main() aplaza limpio (75)."""
    orig_run = git_mutex.run

    def fake_run_raises(args, timeout=None, check_output_timeout=120):
        raise TimeoutError("candado ocupado (simulado)")

    git_mutex.run = fake_run_raises
    try:
        rc = git_mutex.main(["-C", "/tmp", "worktree", "remove", "/tmp/x"])
    finally:
        git_mutex.run = orig_run
    ok(rc == 75, "git_mutex CLI: TimeoutError del candado -> rc=75 (aplaza, EX_TEMPFAIL): got %r" % rc)


# ---------------------------------------------------------------------------------------------
# 3) cerrar_sesion.py: MERGE y PODA se ejecutan ambos con el candado "git-mutex" tomado.
# ---------------------------------------------------------------------------------------------
def _preparar_worktree(nombre):
    wt = os.path.join(_TMP_BASE, "wt-" + nombre)
    os.makedirs(wt, exist_ok=True)
    return os.path.realpath(wt)


def test_cerrar_sesion_merge_y_poda_bajo_candado():
    wt = _preparar_worktree("merge_poda")
    estados = {}

    def fake_run(args, cwd=None, check=False):
        if args[:1] == ["git"] and "-C" in args:
            idx = args.index("-C")
            path = args[idx + 1]
            sub = args[idx + 2:]
        elif args[:1] == ["git"]:
            path = None
            sub = args[1:]
        else:
            return (0, "", "")   # p.ej. kb.py index (no debería llegar aquí, sin docs_nuevos)

        if sub[:2] == ["rev-parse", "--show-toplevel"]:
            return (0, wt, "")
        if sub[:2] == ["rev-parse", "--abbrev-ref"]:
            return (0, ("feat/mi-rama" if path == wt else "master"), "")
        if sub[:1] == ["status"]:
            return (0, "", "")                 # limpio, sin cambios
        if sub[:2] == ["rev-list", "--count"]:
            return (0, "2", "")                # 2 commits propios -> hay que fusionar
        if sub[:1] == ["merge"]:
            estados["merge_lock_held"] = _lock.held(cerrar_sesion.GIT_MUTEX)
            return (0, "Merge made", "")
        if sub[:2] == ["worktree", "remove"]:
            estados["poda_lock_held"] = _lock.held(cerrar_sesion.GIT_MUTEX)
            return (0, "", "")
        return (0, "", "")

    orig = cerrar_sesion._run
    cerrar_sesion._run = fake_run
    try:
        r = cerrar_sesion.cerrar(apply=True, podar=True)
    finally:
        cerrar_sesion._run = orig

    ok(not r.get("error"), "cerrar(apply=True) sin error: %r" % r.get("error"))
    ok(r.get("fusionado") is True, "se fusionó (n>0)")
    ok(r.get("podado") is True, "se podó el worktree")
    ok(estados.get("merge_lock_held") is True,
       "el MERGE se ejecutó con el candado 'git-mutex' tomado")
    ok(estados.get("poda_lock_held") is True,
       "la PODA se ejecutó con el candado 'git-mutex' tomado (hallazgo B — antes NO lo tomaba)")
    ok(not _lock.held(cerrar_sesion.GIT_MUTEX), "el candado quedó libre al terminar (no se filtró)")


# ---------------------------------------------------------------------------------------------
# 4) CANARIO — la PODA (aislada, sin fusión: n==0) ESPERA a un candado "git-mutex" tomado por
#    OTRO actor en vez de mutar en paralelo. Si se quita el `with _lock.lock(...)` de la poda en
#    cerrar_sesion.py (revertir el fix), este test debe ir a ROJO (elapsed ≈ 0).
# ---------------------------------------------------------------------------------------------
def test_cerrar_sesion_poda_espera_candado_externo():
    wt = _preparar_worktree("solo_poda")
    momento_poda = {}

    def fake_run(args, cwd=None, check=False):
        if args[:1] == ["git"] and "-C" in args:
            idx = args.index("-C")
            path = args[idx + 1]
            sub = args[idx + 2:]
        elif args[:1] == ["git"]:
            path = None
            sub = args[1:]
        else:
            return (0, "", "")

        if sub[:2] == ["rev-parse", "--show-toplevel"]:
            return (0, wt, "")
        if sub[:2] == ["rev-parse", "--abbrev-ref"]:
            return (0, ("feat/otra-rama" if path == wt else "master"), "")
        if sub[:1] == ["status"]:
            return (0, "", "")
        if sub[:2] == ["rev-list", "--count"]:
            return (0, "0", "")                # nada que fusionar -> solo corre la poda
        if sub[:2] == ["worktree", "remove"]:
            momento_poda["ts"] = time.time()
            return (0, "", "")
        return (0, "", "")

    liberar = threading.Event()
    tomado = threading.Event()

    def otro_actor_muta():
        with _lock.lock(cerrar_sesion.GIT_MUTEX, timeout=5):
            tomado.set()
            liberar.wait(3)

    t = threading.Thread(target=otro_actor_muta)
    t.start()
    tomado.wait(2)
    t_inicio_espera = time.time()

    orig = cerrar_sesion._run
    cerrar_sesion._run = fake_run
    try:
        # Suelta al "otro actor" a los 0.35s, EN OTRO HILO, mientras cerrar() corre en éste.
        threading.Timer(0.35, liberar.set).start()
        r = cerrar_sesion.cerrar(apply=True, podar=True)
    finally:
        cerrar_sesion._run = orig
        t.join(timeout=3)

    ok(not r.get("error"), "cerrar(apply=True, solo poda) sin error: %r" % r.get("error"))
    ok(r.get("podado") is True, "la poda se completó tras liberarse el candado externo")
    espera = momento_poda.get("ts", 0) - t_inicio_espera
    ok(espera >= 0.30,
       "la poda ESPERÓ al candado tomado por otro actor en vez de mutar en paralelo (%.2fs)" % espera)


# ---------------------------------------------------------------------------------------------
# 5) tools/ramas.py `limpia --si`: worktree prune + worktree remove pasan por git_mutex.run(),
#    con el candado "git-mutex" tomado durante la mutación real.
# ---------------------------------------------------------------------------------------------
def test_ramas_limpia_usa_git_mutex():
    wt_path = os.path.realpath(os.path.join(_TMP_BASE, "wt-huerfana"))
    os.makedirs(wt_path, exist_ok=True)
    estados = {"prune": None, "remove": None}

    def fake_ramas_run(args):
        # Solo-lectura (worktree list / status / rev-list) — NO son las mutaciones bajo prueba.
        if args[:3] == ["git", "-C", ramas.ROOT] and args[3:5] == ["worktree", "list"]:
            return ("worktree %s\nbranch refs/heads/master\n\n"
                    "worktree %s\nbranch refs/heads/huerfana\n") % (ramas.ROOT, wt_path)
        if "status" in args:
            return ""                # limpio
        if "rev-list" in args:
            return "0"                # sin cambios -> candidata vacía a podar
        return ""

    def fake_git_mutex_subprocess_run(args, capture_output=True, text=True, timeout=None):
        # args = ["git", "-C", ROOT, "worktree", "prune"] o [..., "worktree", "remove", path]
        if "prune" in args:
            estados["prune"] = _lock.held(git_mutex.NOMBRE)
        if "remove" in args:
            estados["remove"] = _lock.held(git_mutex.NOMBRE)
        return _FakeCompleted(0, "", "")

    orig_run = ramas._run
    orig_gm_subprocess = git_mutex.subprocess.run
    ramas._run = fake_ramas_run
    git_mutex.subprocess.run = fake_gm = fake_git_mutex_subprocess_run
    try:
        rc = ramas.main(["limpia", "--si"])
    finally:
        ramas._run = orig_run
        git_mutex.subprocess.run = orig_gm_subprocess

    ok(rc in (0, None), "ramas.py limpia --si terminó sin error")
    ok(estados["prune"] is True, "worktree prune se ejecutó con 'git-mutex' tomado")
    ok(estados["remove"] is True, "worktree remove se ejecutó con 'git-mutex' tomado "
                                  "(hallazgo B — antes ramas.py mutaba sin candado)")


# ---------------------------------------------------------------------------------------------
# 6) El nombre del candado es el MISMO en los tres puntos de llamada (evita drift silencioso).
# ---------------------------------------------------------------------------------------------
def test_nombre_candado_consistente():
    ok(cerrar_sesion.GIT_MUTEX == git_mutex.NOMBRE == "git-mutex",
       "cerrar_sesion.GIT_MUTEX y git_mutex.NOMBRE son el MISMO candado 'git-mutex'")


def main():
    test_git_mutex_serializa()
    test_git_mutex_cli_aplaza_en_timeout()
    test_cerrar_sesion_merge_y_poda_bajo_candado()
    test_cerrar_sesion_poda_espera_candado_externo()
    test_ramas_limpia_usa_git_mutex()
    test_nombre_candado_consistente()
    print("RESULTADO git_mutex (A1 fix 1): %d OK, %d fallos" % (_pass, _fail))
    print("✅ CANDADO GIT COMPARTIDO EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
