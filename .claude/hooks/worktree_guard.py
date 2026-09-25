#!/usr/bin/env python3
"""worktree_guard.py — en sesión con worktree, se edita DENTRO del worktree.

NORMA (registro: tools/normas.json) `feedback-worktree-editar-rutas-del-worktree`, clase
BLOQUEO y `repetida: true`: «en sesión con worktree, editar SIEMPRE rutas DENTRO del worktree,
no rutas absolutas de casa base». Llevaba desde el 31-jul-26 clasificada y sin mecanizar, o
sea: dependiendo de que yo me acordara.

POR QUÉ HACE FALTA, MEDIDO (18-sep-26, barrido de los transcripts reales de las 56 sesiones con
worktree): de 1122 escrituras, **245 salieron del worktree hacia casa base**, y 117 de ellas
aterrizaron dentro del worktree de OTRA sesión viva. No es un riesgo teórico: es el mecanismo
por el que un commit se cae de master y hay que rescatarlo con cherry-pick, y por el que dos
sesiones paralelas se pisan (norma hermana `feedback-sesion-paralela-seguridad-primero`).

DÓNDE ESTÁ LA LÍNEA. No es «todo casa base»: el contenido (`00_FUENTE-DE-VERDAD/`), los
informes y el estado vivo del lazo (`tools/state/cost/`) NO están versionados — existen SOLO en
casa base, así que una sesión-worktree no tiene ninguna ruta alternativa donde escribirlos.
Bloquearlos rompería trabajo real (51 de esas 245). El corte es:

    ¿el directorio de destino existe TAMBIÉN dentro de mi worktree?
        sí  → es árbol versionado: ahí es donde se escribe            → DENY
        no  → solo vive en casa base (contenido/estado/overlays)      → ALLOW

Ese predicado cuesta un `isdir` y coincide con «¿está versionado en git?» en los 245 casos
reales — así que no se paga un subproceso de git en cada Write. Y como mira el DIRECTORIO y no
el fichero, un fichero NUEVO en `tools/` también cae (que si no, era el agujero obvio).

FAIL-OPEN A PROPÓSITO, al revés que el muro. `muro_guard.py` es fail-closed porque defiende de
un adversario en el lazo 24/7. Esto defiende de un despiste MÍO en sesión interactiva: si el
guard revienta, la consecuencia de fallar cerrado sería dejarla sin poder editar nada. Cualquier
excepción interna → exit 0. Lo que este guard no pueda juzgar, no lo bloquea.

Escotilla explícita: `BTP_CASA_BASE_OK=1` (misma convención que BTP_MURO_OK / BTP_GIT_BASE_OK)
para cuando SÍ se quiere tocar casa base a sabiendas.

LÍMITE HONESTO: cubre las tools escritoras (Write/Edit/MultiEdit/NotebookEdit). NO cubre `Bash`
—un `cd ~/claudecode && git commit` sigue pasando—, que es la otra mitad de cómo se cae un
commit de master. Eso es la norma `feedback-sesion-paralela-seguridad-primero`, aún sin
mecanizar, y pide verificar la rama, no la ruta.

Contrato de hooks (code.claude.com/docs/hooks): exit 0 permite · exit 2 deniega.
"""
import json
import os
import sys

ESCRITORAS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
MARCA_WT = os.sep + os.path.join(".claude", "worktrees") + os.sep


def _raiz_del_proyecto():
    """El árbol al que pertenece ESTE fichero: el worktree si vive en uno, o casa base.

    Se deduce de `__file__` y no del cwd a propósito: el cwd se mueve durante la sesión (un `cd`
    en un Bash anterior), mientras que el hook que corre es siempre el del árbol de la sesión —
    Claude Code resuelve `${CLAUDE_PROJECT_DIR}/.claude/hooks/...` contra el proyecto abierto.
    `BTP_WT_RAIZ_OVERRIDE` es SOLO para los tests (mismo patrón que BTP_GIT_REPO_OVERRIDE).
    """
    ov = os.environ.get("BTP_WT_RAIZ_OVERRIDE")
    if ov:
        return os.path.abspath(ov)
    aqui = os.path.dirname(os.path.abspath(__file__))        # <raiz>/.claude/hooks
    return os.path.dirname(os.path.dirname(aqui))


def _dentro(path, raiz):
    return path == raiz or path.startswith(raiz + os.sep)


def _candidatas(valor, cwd):
    """Formas absolutas de la ruta pedida: la normalizada y la real (por si hay symlink)."""
    p = os.path.expanduser(str(valor))
    if not os.path.isabs(p):
        p = os.path.join(cwd, p)
    n = os.path.normpath(p)
    out = [n]
    try:
        r = os.path.realpath(n)
        if r != n:
            out.append(r)
    except Exception:
        pass
    return out


def _veredicto(path, mi_wt, casa):
    """None si pasa; si no, (motivo, sugerencia-dentro-del-worktree o None)."""
    if _dentro(path, mi_wt):
        return None
    if not _dentro(path, casa):
        return None                      # fuera del repo entero: no es asunto de esta norma
    otros = os.path.join(casa, ".claude", "worktrees")
    if _dentro(path, otros):
        return ("es el worktree de OTRA sesión (ahí hay trabajo vivo de alguien más)", None)
    rel = os.path.relpath(path, casa)
    espejo = os.path.join(mi_wt, os.path.dirname(rel))
    if os.path.isdir(espejo):
        return ("es árbol VERSIONADO de casa base", os.path.join(mi_wt, rel))
    return None                          # solo vive en casa base (contenido/estado): pasa


def main():
    data = json.loads(sys.stdin.read())
    if data.get("tool_name") not in ESCRITORAS:
        return 0
    if os.environ.get("BTP_CASA_BASE_OK") == "1":
        return 0
    raiz = _raiz_del_proyecto()
    if MARCA_WT not in raiz + os.sep:
        return 0                         # sesión en casa base: no hay nada que separar
    casa = raiz.split(MARCA_WT)[0]
    cwd = data.get("cwd")
    cwd = cwd if isinstance(cwd, str) and os.path.isabs(cwd) else raiz
    ti = data.get("tool_input") or {}
    for clave in ("file_path", "notebook_path"):
        valor = ti.get(clave)
        if not valor:
            continue
        for cand in _candidatas(valor, cwd):
            v = _veredicto(cand, raiz, casa)
            if v:
                motivo, sugerencia = v
                sys.stderr.write(
                    "WORKTREE ⛔ esta sesión trabaja en %s y esa ruta %s.\n"
                    "   pedido:  %s\n%s"
                    "   Si de verdad quieres tocar casa base: BTP_CASA_BASE_OK=1.\n"
                    % (os.path.basename(raiz), motivo, valor,
                       ("   escribe:  %s\n" % sugerencia) if sugerencia else ""))
                return 2
    return 0


if __name__ == "__main__":
    # Watchdog (25-sep-26): si tardo más que el timeout de settings (5 s), deniego yo antes de
    # que Claude Code me cancele y lo convierta en un permitir. Ver _watchdog.py.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:  # sin el vigilante el guard sigue siendo el guard: su falta no lo tumba
        import _watchdog
    except ImportError:
        _watchdog = None
        sys.stderr.write("worktree_guard: falta _watchdog.py; sigo sin vigilante\n")
    if _watchdog:
        _watchdog.armar(5, 'worktree_guard')
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        # FAIL-OPEN deliberado (ver cabecera): un fallo de este guard no puede dejarla sin editar.
        sys.exit(0)
