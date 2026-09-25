#!/usr/bin/env python3
"""tools/git_mutex.py — CANDADO ÚNICO para TODA mutación del `.git` de la CASA BASE.

Origen (A1, comité de arquitectura, 10-jul-26 — hallazgo B): `cerrar_sesion.py` solo tomaba
el candado `_lock.py` alrededor del `git merge`, NO alrededor de la poda del worktree
(`git worktree remove`, dos bloques después); `tools/ramas.py` (`limpia`/`limpia --si`) hace
`git worktree prune` y `git worktree remove` SIN candado alguno; y el agente `git`/rutina
`git-barrido` (charter, shell libre) también muta ramas/worktrees por Bash sin pasar por
ningún candado. Resultado: al menos tres actores mutando el MISMO `.git/worktrees/` y las
refs de la casa base con SOLO una de las operaciones de UNO de los tres protegida — el hueco
concreto que separa "loop stateless con checks idempotentes" de una carrera real entre dos
mutaciones idempotentes que se pisan sobre el mismo recurso no versionado.

Este módulo cierra el hueco para los actores en PYTHON (cerrar_sesion.py, ramas.py): en vez
de invocar `git` a pelo para una mutación de la casa base, invocan `git_mutex.run([...])`, que
envuelve la llamada real en el candado compartido `_lock.py` con nombre fijo "git-mutex" (el
MISMO nombre para TODOS los llamantes → serializa entre sí, no solo consigo mismo). Reentrante
en el sentido de _lock.py: si el candado no se puede tomar en `timeout` s, lanza (fail-closed;
mejor fallar visible que mutar sin candado).

El agente `git`/`git-barrido` invoca `git` DIRECTAMENTE por Bash (es un charter en lenguaje
natural, no un script) — este módulo no lo intercepta (ver limitación en el propio A1: mover
esa vía a `git_mutex.sh`/allowlist del muro es un cambio de PRODUCCIÓN sobre el charter y el
muro, fuera del alcance de "solo tools/" de este parche; queda como riesgo residual documentado).

Uso (en vez de `git -C <repo> <args...>` para una mutación de la casa base):
  python3 tools/git_mutex.py -C <repo> worktree remove <path>
  python3 tools/git_mutex.py -C <repo> worktree prune
  python3 tools/git_mutex.py -C <repo> branch -d <rama>
  python3 tools/git_mutex.py -C <repo> merge --no-ff <rama> -m "..."

O desde Python: `import git_mutex; rc, out, err = git_mutex.run(["-C", repo, "worktree", "remove", wt])`.

Stdlib puro. Mismo patrón que `_lock.py`/`cerrar_sesion.py`.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _lock  # noqa: E402

NOMBRE = "git-mutex"
TIMEOUT = float(os.environ.get("BTP_GIT_MUTEX_TIMEOUT", "60"))


def run(args, timeout=None, check_output_timeout=120):
    """Ejecuta `git <args>` bajo el candado compartido "git-mutex". Devuelve (rc, stdout, stderr).

    Lanza TimeoutError si no se puede tomar el candado en `timeout` s (otro actor lo tiene) —
    fail-closed: el llamante debe tratarlo como "aplázalo", NUNCA como "mútalo sin candado"."""
    with _lock.lock(NOMBRE, timeout=timeout if timeout is not None else TIMEOUT):
        es_merge, prefijo = _es_merge_nuevo(args)
        habia_merge = es_merge and _hay_merge_head(prefijo, check_output_timeout)
        p = subprocess.run(["git"] + list(args), capture_output=True, text=True,
                            timeout=check_output_timeout)
        err = p.stderr
        # Merge fallido → deshacer AQUÍ, dentro del candado (24-sep-2026). Un merge bloqueado por el
        # freno de la base (hook reference-transaction) o por un conflicto dejaba casa base —el
        # sistema vivo 24/7— con el árbol mezclado, staged y MERGE_HEAD hasta que alguien abortara a
        # mano: pasó dos veces el mismo día (deuda merge-bloqueado-deja-casa-base-sucia). Misma
        # política que cerrar_sesion.py. Solo si el MERGE_HEAD lo creó ESTE merge: uno previo es de
        # otra sesión a medio resolver y no se toca.
        if es_merge and p.returncode != 0 and not habia_merge \
                and _hay_merge_head(prefijo, check_output_timeout):
            ab = subprocess.run(["git"] + prefijo + ["merge", "--abort"], capture_output=True,
                                text=True, timeout=check_output_timeout)
            if ab.returncode == 0:
                err += ("git_mutex: merge fallido → deshecho (merge --abort): el repo sigue como "
                        "estaba y tu rama está intacta.\n")
            else:
                err += ("‼️ git_mutex: merge fallido y NO se pudo deshacer (merge --abort: %s). "
                        "El repo está a medio fusionar: arréglalo YA.\n" % (ab.stderr.strip() or ab.returncode))
        return p.returncode, p.stdout, err


def _es_merge_nuevo(args):
    """(True, [-C repo…]) si args es un `git merge` que EMPIEZA una fusión; (False, prefijo) si no.
    `--abort/--continue/--quit` gestionan una fusión ya abierta: esas no se deshacen."""
    args = list(args)
    prefijo, i = [], 0
    while i < len(args) and args[i] == "-C" and i + 1 < len(args):
        prefijo += args[i:i + 2]
        i += 2
    resto = args[i:]
    if not resto or resto[0] != "merge":
        return False, prefijo
    if any(a in ("--abort", "--continue", "--quit") for a in resto[1:]):
        return False, prefijo
    return True, prefijo


def _hay_merge_head(prefijo, timeout):
    """¿Hay una fusión abierta (MERGE_HEAD) en el repo de `prefijo`? Si no se puede saber, True:
    ante la duda NO se aborta nada (el abort solo corre si antes era False y después True)."""
    try:
        p = subprocess.run(["git"] + prefijo + ["rev-parse", "-q", "--verify", "MERGE_HEAD"],
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0
    except Exception:            # noqa: BLE001
        return True


def _ruta_worktree_remove(argv):
    """Si argv es `worktree remove [opciones] <ruta>`, devuelve la ruta; si no, None."""
    if len(argv) < 3 or argv[0] != "worktree" or argv[1] != "remove":
        return None
    resto = [a for a in argv[2:] if not a.startswith("-")]
    return resto[0] if resto else None


def _lo_que_se_perderia(ruta):
    """Ficheros de `ruta` que git NO ve y que la poda borraría (ver ramas._trabajo_vivo).
    Si no se puede comprobar, devuelve None: quien llama lo trata como «no sé» y no poda."""
    try:
        import ramas
        return ramas._trabajo_vivo(os.path.abspath(ruta))
    except Exception:            # noqa: BLE001
        return None


# ── Freno de la base en la puerta (24-sep-2026, deuda freno-base-deja-el-arbol-a-medio-fusionar) ──
# El freno nativo (`tools/githooks/reference-transaction`) rechaza mover master sin
# BTP_GIT_BASE_OK=1, pero lo rechaza TARDE: git ya ha escrito índice y árbol. Un
# `git_mutex.py -C casa-base merge --no-ff <rama>` sin el OK dejó casa base con MERGE_HEAD y los
# ficheros de la rama staged unos 17 min (24-sep, sesión frosty-hertz), y ninguna otra sesión
# podía fusionar. `cerrar_sesion.py` ya comprobaba el gate antes; esta puerta no. Ahora sí:
#   1. ANTES: lo que movería master de casa base sin el OK no se lanza (rc 3, nada tocado).
#   2. DESPUÉS: el `merge` fallido ya lo deshace `run()` (otra sesión, el mismo 24-sep, deuda
#      merge-bloqueado-deja-casa-base-sucia). Aquí se cubre lo que `run()` no ve: `pull`,
#      `cherry-pick` y `revert` sobre casa base que fallan y dejan un estado a medias nuevo.
MUEVEN_HEAD = ("merge", "pull", "reset", "cherry-pick", "rebase", "revert", "commit", "am")
A_MEDIAS = (("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"))
RAMAS_BASE = ("master", "main")


def _partir(argv):
    """(repo, resto): consume los `-C <ruta>` iniciales, como hace git."""
    repo, i = os.getcwd(), 0
    while i + 1 < len(argv) and argv[i] == "-C":
        repo = os.path.join(repo, argv[i + 1])
        i += 2
    return repo, argv[i:]


def _casa():
    try:
        from _casa import casa_base
    except ImportError:          # hay quien copia git_mutex.py suelto (test_ramas_fusionar)
        def casa_base():
            return os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
    return os.path.realpath(casa_base())


def _es_casa_base(repo):
    try:
        top = subprocess.run(["git", "-C", repo, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return bool(top) and os.path.realpath(top) == _casa()
    except Exception:            # noqa: BLE001
        return False


def _rama(repo):
    try:
        return subprocess.run(["git", "-C", repo, "symbolic-ref", "--short", "-q", "HEAD"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:            # noqa: BLE001
        return ""


def _mueve_la_base(argv):
    """Motivo (str) si `argv` movería master/main de casa base; "" si no."""
    repo, resto = _partir(argv)
    if not resto:
        return ""
    sub, args = resto[0], resto[1:]
    if sub == "push":
        for a in args:
            destino = a.split(":", 1)[1] if ":" in a else ""
            if destino.replace("refs/heads/", "").lstrip("+") in RAMAS_BASE:
                return "push a %s" % destino
        return ""
    if sub == "update-ref":
        refs = [a for a in args if not a.startswith("-")]
        if refs and refs[0] in tuple("refs/heads/" + r for r in RAMAS_BASE):
            return "update-ref %s" % refs[0]
        return ""
    if sub not in MUEVEN_HEAD or any(a in ("--abort", "--quit") for a in args):
        return ""
    if _es_casa_base(repo) and _rama(repo) in RAMAS_BASE:
        return "%s sobre %s de casa base" % (sub, _rama(repo))
    return ""


def _gate_encendido():
    return os.path.exists(os.path.join(_casa(), ".claude", "hooks", ".base_gate_on"))


# Opciones de `git merge` que llevan valor: su valor no es una rama.
_MERGE_CON_VALOR = ("-m", "-F", "-s", "-X", "--message", "--file", "--strategy", "--strategy-option",
                    "--cleanup", "--into-name", "-S", "--gpg-sign")


def _ramas_del_merge(args):
    """Las ramas/commits que un `git merge <args>` fusiona (sin las opciones ni sus valores)."""
    fuera, salta = [], False
    for a in args:
        if salta:
            salta = False
            continue
        if a in _MERGE_CON_VALOR:
            salta = True
            continue
        if a.startswith("-"):
            continue
        fuera.append(a)
    return fuera


def _rama_ajena(argv, cwd=None, _sesiones=None, _worktrees=None):
    """Motivo (str) si `argv` fusiona a casa base una rama cuyo worktree tiene sesiones VIVAS de
    otra sesión; "" si la rama es de quien llama, no tiene worktree o nadie trabaja en ella.

    Por qué (26-sep-2026, deuda `fusion-de-rama-ajena-sin-ok`): la rama de un hook del muro llegó
    dos veces a casa base el 25-sep desde OTRA sesión (1a22fe8 y b3c8462), sin el OK que {{TITULAR}} da
    en la suya, porque este gate solo miraba BTP_GIT_BASE_OK=1. Override deliberado, y solo para
    ESA rama: BTP_FUSION_AJENA_OK=<nombre exacto>. Si no se puede saber de quién es → se rechaza."""
    repo, resto = _partir(argv)
    if not resto or resto[0] != "merge" or any(a in ("--abort", "--quit", "--continue") for a in resto):
        return ""
    cwd = os.path.realpath(cwd or os.getcwd())
    try:
        if _sesiones is None or _worktrees is None:
            import ramas
            _worktrees = ramas.worktrees() if _worktrees is None else _worktrees
            _sesiones = ramas.sesiones() if _sesiones is None else _sesiones
    except Exception as e:            # noqa: BLE001
        return "no pude saber de quién es la rama (%r)" % (e,)
    for rama in _ramas_del_merge(resto[1:]):
        wts = [w for w in _worktrees if w.get("branch") == rama and not w.get("es_base")]
        for w in wts:
            p = os.path.realpath(w["path"])
            if cwd == p or cwd.startswith(p.rstrip("/") + "/"):
                break                                   # es la rama de quien llama
            vivas = [s for s in _sesiones if s.get("rama") == rama]
            if vivas and os.environ.get("BTP_FUSION_AJENA_OK") != rama:
                return ("la rama %s es de otra sesión viva (pid %s, en %s)"
                        % (rama, ", ".join(str(s.get("pid")) for s in vivas[:3]), w["path"]))
    return ""


def _git_dir(repo):
    try:
        return subprocess.run(["git", "-C", repo, "rev-parse", "--absolute-git-dir"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:            # noqa: BLE001
        return ""


def _a_medias(gd):
    return {cmd for f, cmd in A_MEDIAS if gd and os.path.exists(os.path.join(gd, f))}


def main(argv):
    if not argv:
        sys.stderr.write("uso: git_mutex.py <args de git...>\n")
        return 2
    motivo = _mueve_la_base(argv)
    if motivo and _gate_encendido() and os.environ.get("BTP_GIT_BASE_OK") != "1":
        sys.stderr.write(
            "git_mutex: NO lanzo %s sin BTP_GIT_BASE_OK=1. Mover master de casa base es gate de "
            "{{TITULAR}}, y el freno nativo lo rechazaría tarde, con el árbol ya a medio escribir. Con su "
            "OK explícito: BTP_GIT_BASE_OK=1 (mejor, `cerrar_sesion.py --apply`).\n" % motivo)
        return 3
    ajena = _rama_ajena(argv) if motivo and _gate_encendido() else ""
    if ajena:
        sys.stderr.write(
            "git_mutex: NO fusiono a casa base: %s. Fusionar a base es gate de {{TITULAR}}, y su OK se da "
            "en la sesión dueña de la rama (esa sesión cierra con `cerrar_sesion.py --apply`). Si de "
            "verdad toca desde aquí: BTP_FUSION_AJENA_OK=<nombre exacto de la rama>.\n" % ajena)
        return 3
    repo, resto = _partir(argv)
    vigilar = bool(resto) and resto[0] in ("pull", "cherry-pick", "revert") \
        and _es_casa_base(repo)
    gd = _git_dir(repo) if vigilar else ""
    antes = _a_medias(gd)
    # ── Freno de poda (20-sep-2026) ─────────────────────────────────────────────────────
    # `cerrar_sesion.py` ya se negaba a podar un worktree con ficheros ignorados dentro (panel
    # del lazo, log de auditoría clínica…). Pero el freno vivía SOLO allí: el mismo día lo avisó
    # —«se perderían 1 fichero(s)»— y la poda se hizo igual con `git_mutex.py worktree remove`,
    # que es la vía sancionada para mutar el .git y no preguntaba nada. Un freno que solo guarda
    # una puerta no guarda. `--force` de git no lo salta: ese flag va de cambios sin commitear,
    # no de lo ignorado. El único paso es nombrado y queda en el entorno de quien lo ejecuta:
    # BTP_PODA_PERDER_OK=1, para cuando ya se rescató o de verdad es basura.
    ruta = _ruta_worktree_remove(argv)
    if ruta and os.environ.get("BTP_PODA_PERDER_OK") != "1":
        perdible = _lo_que_se_perderia(ruta)
        if perdible is None:
            sys.stderr.write("git_mutex: NO podo %s — no he podido comprobar qué hay dentro que git "
                             "no ve. Míralo a mano.\n" % ruta)
            return 3
        if perdible:
            sys.stderr.write(
                "git_mutex: NO podo %s — se perderían %d fichero(s) que git no ve (gitignored):\n%s\n"
                "Rescátalos a casa base. Si ya lo están o son basura: BTP_PODA_PERDER_OK=1\n"
                % (ruta, len(perdible), "\n".join("  · " + f for f in perdible[:10])))
            return 3
    try:
        rc, out, err = run(argv)
    except TimeoutError as e:
        sys.stderr.write("git_mutex: no pude tomar el candado %r (otro actor muta la casa base ahora mismo): %s\n"
                          % (NOMBRE, e))
        return 75  # EX_TEMPFAIL — aplázalo; nunca lo sortees sin candado.
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err)
    if vigilar and rc != 0:
        for cmd in sorted(_a_medias(gd) - antes):
            try:
                rc_ab, _, err_ab = run(["-C", repo, cmd, "--abort"])
            except TimeoutError as e:
                rc_ab, err_ab = 75, str(e)
            if rc_ab == 0:
                sys.stderr.write("git_mutex: %s fallido deshecho (%s --abort): casa base sigue como "
                                 "estaba.\n" % (cmd, cmd))
            else:
                sys.stderr.write("git_mutex: ‼️ casa base A MEDIAS y no pude deshacerlo (%s --abort: "
                                 "%s). Arréglalo YA: bloquea las fusiones de todas las sesiones.\n"
                                 % (cmd, (err_ab or "").strip()[-200:]))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
