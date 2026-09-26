#!/usr/bin/env python3
"""test_worktree_guard.py — una sesión con worktree no escribe en casa base.

NORMA: `feedback-worktree-editar-rutas-del-worktree` — «en sesión con worktree, editar SIEMPRE
rutas DENTRO del worktree, no rutas absolutas de casa base». Clase BLOQUEO, `repetida: true`,
y hasta hoy (18-sep-26) con `mecanismo: null`.

POR QUÉ IMPORTA, MEDIDO (no de memoria): barriendo los transcripts reales de las 56 sesiones
con worktree, de 1122 escrituras **245 fueron de una sesión-worktree hacia casa base**. 117 de
ellas cayeron dentro del worktree de OTRA sesión. Ese es el mecanismo por el que un commit se
cae de master y hay que rescatarlo con cherry-pick.

DÓNDE ESTÁ LA LÍNEA (y por qué no es «todo casa base»): el contenido (`00_FUENTE-DE-VERDAD/`),
los informes y el estado vivo (`tools/state/cost/`) NO están versionados — existen SOLO en casa
base, así que una sesión-worktree no tiene ninguna ruta alternativa donde escribirlos y
bloquearlos rompería el trabajo real (51 de las 245). El corte exacto es: **¿ese directorio
existe también dentro de mi worktree?** Si existe, es árbol versionado y ahí es donde se
escribe. Ese predicado barato coincide con «¿está versionado en git?» en los 245 casos reales,
sin pagar un subproceso de git por cada Write.
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
GUARD = _os.path.join(ROOT, ".claude", "hooks", "worktree_guard.py")

# Casa base y worktree de MENTIRA, con la misma forma que los de verdad.
_TMP = tempfile.mkdtemp(prefix="wtguard_")
CASA = _os.path.join(_TMP, "claudecode")
MI_WT = _os.path.join(CASA, ".claude", "worktrees", "mi-rama")
OTRO_WT = _os.path.join(CASA, ".claude", "worktrees", "otra-sesion")
# Subagente con isolation "worktree": hermano de MI_WT, pero el hook que corre es el del padre.
AGENTE_WT = _os.path.join(CASA, ".claude", "worktrees", "agent-a256534a4f28f0cf0")
OTRO_AGENTE_WT = _os.path.join(CASA, ".claude", "worktrees", "agent-otro0000")
for d in (
    # árbol VERSIONADO: existe en casa base y también dentro de cada worktree
    _os.path.join(CASA, "tools"), _os.path.join(CASA, "tests"),
    _os.path.join(CASA, ".claude", "hooks"),
    _os.path.join(MI_WT, "tools"), _os.path.join(MI_WT, "tests"),
    _os.path.join(MI_WT, ".claude", "hooks"),
    _os.path.join(OTRO_WT, "tools"),
    _os.path.join(AGENTE_WT, "tools"), _os.path.join(OTRO_AGENTE_WT, "tools"),
    # `tools/state/` SÍ está versionado (herramientas.json) y por eso existe en el worktree;
    # `tools/state/cost/` NO lo está: es el estado vivo del lazo. La línea pasa entre los dos.
    _os.path.join(MI_WT, "tools", "state"),
    # árbol NO versionado: solo existe en casa base
    _os.path.join(CASA, "00_FUENTE-DE-VERDAD", "01 · Tratamiento"),
    _os.path.join(CASA, "informes", "_historial"),
    _os.path.join(CASA, "tools", "state", "cost"),
):
    _os.makedirs(d, exist_ok=True)

ENV = dict(_os.environ, BTP_WT_RAIZ_OVERRIDE=MI_WT)
ENV.pop("BTP_CASA_BASE_OK", None)


def _rc(path, tool="Write", env=None, cwd=None):
    payload = {"tool_name": tool, "tool_input": {"file_path": path}, "cwd": cwd or MI_WT}
    p = subprocess.run([_sys.executable, GUARD], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=20, env=env or ENV)
    return p.returncode, (p.stderr or "").strip()


DENY = [
    (_os.path.join(CASA, "tools", "seguimiento.py"), "código de casa base por ruta absoluta"),
    (_os.path.join(CASA, "tests", "test_x.py"), "test de casa base"),
    (_os.path.join(CASA, "tools", "nuevo_del_todo.py"), "fichero NUEVO en zona versionada de casa base"),
    (_os.path.join(CASA, "CLAUDE.md"), "la constitución en la raíz de casa base"),
    (_os.path.join(CASA, ".claude", "hooks", "muro_guard.py"), "un hook de casa base"),
    (_os.path.join(OTRO_WT, "tools", "anatomia.py"), "el worktree de OTRA sesión (117 casos reales)"),
    (_os.path.join(CASA, "tools", "state", "herramientas.json"), "fichero versionado dentro de tools/state"),
]

ALLOW = [
    (_os.path.join(MI_WT, "tools", "seguimiento.py"), "mi propio worktree (el trabajo normal)"),
    (_os.path.join(MI_WT, "tests", "test_x.py"), "mis tests"),
    (_os.path.join(MI_WT, "tools", "nuevo.py"), "fichero nuevo en mi worktree"),
    (_os.path.join(CASA, "00_FUENTE-DE-VERDAD", "01 · Tratamiento", "borrador.md"),
     "CONTENIDO: no versionado, solo vive en casa base (42 casos reales)"),
    (_os.path.join(CASA, "informes", "_historial", "pet.md"), "informes: idem (8 casos reales)"),
    (_os.path.join(CASA, "tools", "state", "cost", "limits.json"), "estado VIVO del lazo, no versionado"),
    (_os.path.expanduser("~/.claude/settings.json"), "fuera del repo entero"),
    ("/tmp/borrador.txt", "fuera del repo entero"),
]

fallos = 0
print("=== DEBE DENEGAR (casa base o el worktree de otra sesión) ===")
for p, desc in DENY:
    rc, msg = _rc(p)
    if rc == 2:
        print("  ✅ %s" % desc)
    else:
        fallos += 1
        print("  ❌ PASA (MAL): %s  ->  %s" % (desc, p))

print()
print("=== DEBE PASAR (mi worktree, el contenido y el estado vivo) ===")
for p, desc in ALLOW:
    rc, msg = _rc(p)
    if rc == 0:
        print("  ✅ %s" % desc)
    else:
        fallos += 1
        print("  ❌ DENEGADO (MAL): %s  ->  %s\n     %s" % (desc, p, msg))

print()
print("=== LO DEMÁS ===")
casos = []
# 1. sesión SIN worktree (casa base): el guard no se mete en nada
env_casa = dict(_os.environ, BTP_WT_RAIZ_OVERRIDE=CASA)
env_casa.pop("BTP_CASA_BASE_OK", None)
rc, _ = _rc(_os.path.join(CASA, "tools", "x.py"), env=env_casa, cwd=CASA)
casos.append(("sesión en casa base (sin worktree) escribe en casa base → PASA", rc == 0))
# 2. escotilla explícita
env_ok = dict(ENV, BTP_CASA_BASE_OK="1")
rc, _ = _rc(_os.path.join(CASA, "tools", "x.py"), env=env_ok)
casos.append(("BTP_CASA_BASE_OK=1 → PASA (escotilla explícita)", rc == 0))
rc, _ = _rc(_os.path.join(CASA, "tools", "x.py"), env=dict(ENV, BTP_CASA_BASE_OK="0"))
casos.append(("BTP_CASA_BASE_OK=0 NO cuenta como escotilla → DENIEGA", rc == 2))
# 3. ruta RELATIVA: se resuelve contra el cwd de la sesión, que es el worktree
rc, _ = _rc("tools/seguimiento.py")
casos.append(("ruta relativa desde el worktree → PASA", rc == 0))
# 4. las otras tools escritoras, y las que no lo son
for t in ("Edit", "MultiEdit", "NotebookEdit"):
    rc, _ = _rc(_os.path.join(CASA, "tools", "x.py"), tool=t)
    casos.append(("%s también está cubierta → DENIEGA" % t, rc == 2))
rc, _ = _rc(_os.path.join(CASA, "tools", "x.py"), tool="Read")
casos.append(("Read NO se toca (esto no es el guard clínico) → PASA", rc == 0))
# 5. .. para salir del worktree no cuela
rc, _ = _rc(_os.path.join(MI_WT, "tools", "..", "..", "..", "..", "tools", "x.py"))
casos.append(("salir del worktree con '..' → DENIEGA", rc == 2))
# 6. payload roto: FAIL-OPEN a propósito (un fallo aquí no puede dejarla sin editar)
p = subprocess.run([_sys.executable, GUARD], input="{no es json",
                   capture_output=True, text=True, timeout=20, env=ENV)
casos.append(("payload ilegible → PASA (fail-OPEN deliberado)", p.returncode == 0))
# 7. subagente aislado (bug 26-sep-26): raíz = worktree del padre, cwd = su agent-worktree
rc, msg = _rc(_os.path.join(AGENTE_WT, "tools", "visor3d.py"), tool="Edit", cwd=AGENTE_WT)
casos.append(("subagente edita SU agent-worktree (raíz del padre, cwd agent-X) → PASA", rc == 0))
rc, _ = _rc("tools/visor3d.py", tool="Write", cwd=_os.path.join(AGENTE_WT, "tools"))
casos.append(("subagente, ruta relativa desde un subdirectorio de agent-X → PASA", rc == 0))
rc, _ = _rc(_os.path.join(OTRO_WT, "tools", "anatomia.py"), tool="Edit", cwd=AGENTE_WT)
casos.append(("subagente edita el worktree de OTRA sesión → DENIEGA", rc == 2))
rc, _ = _rc(_os.path.join(OTRO_AGENTE_WT, "tools", "x.py"), tool="Edit", cwd=AGENTE_WT)
casos.append(("subagente edita el agent-worktree de OTRO subagente → DENIEGA", rc == 2))
rc, _ = _rc(_os.path.join(CASA, "tools", "x.py"), tool="Edit", cwd=AGENTE_WT)
casos.append(("subagente edita casa base versionada → DENIEGA", rc == 2))
rc, _ = _rc(_os.path.join(OTRO_WT, "tools", "anatomia.py"), tool="Edit", cwd=OTRO_WT)
casos.append(("cwd en el worktree de otra sesión (no agent-*) NO abre la puerta → DENIEGA", rc == 2))
rc, _ = _rc(_os.path.join(AGENTE_WT, "tools", "..", "..", "otra-sesion", "tools", "a.py"),
            tool="Edit", cwd=AGENTE_WT)
casos.append(("salir de agent-X con '..' hacia otra sesión → DENIEGA", rc == 2))
for desc, ok in casos:
    if ok:
        print("  ✅ %s" % desc)
    else:
        fallos += 1
        print("  ❌ %s" % desc)

shutil.rmtree(_TMP, ignore_errors=True)
print()
print("RESULTADO worktree_guard: %d fallos" % fallos)
print("✅ EL WORKTREE NO PISA CASA BASE" if not fallos else "❌ revisar")
raise SystemExit(1 if fallos else 0)
