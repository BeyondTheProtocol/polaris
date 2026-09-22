#!/usr/bin/env python3
"""test_raices_casa_base.py — un tool que toca el sistema VIVO no puede resolver su raíz al worktree.

POR QUÉ EXISTE (12-sep-2026). Ese día el mismo bug mordió TRES veces, y cada una se arregló por
separado antes de ver que era uno solo:

  · `polaris_estado.py` dio 93/100 desde el worktree y 98/100 desde casa base, el mismo segundo.
  · `radar_taller.py` devolvió 0 candidatos en silencio durante semanas (había 141).
  · `archivar_nota.py` escribió cuatro entregables en un árbol que iba a borrarse, diciendo
    «RAG reindexado» — porque `kb.py` sí reindexa casa base.

La causa: `ROOT = dirname(dirname(__file__))`. Desde casa base acierta; desde un worktree apunta
al worktree, que es efímero y gitignorado. Y como {{TITULAR}} trabaja con varias sesiones a la vez,
cada una en su worktree, ese caso es el normal. Lo que hace daño es que **no falla**: devuelve algo
plausible y sigue.

QUÉ HACE ESTE TEST. Congela el problema: los tools que ya usaban el patrón el 12-sep quedan en una
lista explícita (`CONOCIDOS`), y **cualquier tool nuevo o recién tocado que lo use falla aquí**.
Según se vayan migrando a `_casa.casa_base()`, se borran de la lista y ya no pueden volver.

No adivina cuáles «tocan estado»: eso lo decide una persona al escribir el tool. Lo que sí impide
es que la lista crezca sin que nadie se entere.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)

PATRON = re.compile(r"^\s*(ROOT|REPO|BASE|HERE)\s*=\s*os\.path\.dirname\(os\.path\.dirname", re.M)

# Los que lo hacían el 12-sep-2026, menos los ya migrados. Esta lista SOLO PUEDE ENCOGER.
# Migrados ese día: polaris_estado.py (daba 93 vs 98 según el árbol) y continuity.py
# (escribía el traspaso entre sesiones en un árbol que se borra). El 20-sep se migró panel.py,
# que escribía el panel del lazo en el worktree: dos pérdidas el mismo día (52 y 12 entradas),
# rescatadas a mano porque alguien miró los ignorados antes de podar.
# NO se migran los que usan su raíz para leer CÓDIGO del árbol actual, que es correcto:
# p.ej. capacidades.py lee `.claude/agents`, y las fichas de agentes SON las de la rama.
# Al migrar uno a `_casa.casa_base()`, bórralo de aquí: si alguien lo revierte, este test lo canta.
CONOCIDOS = {
    "_lock.py", "archivar_nota.py", "bandeja.py", "borde.py", "borde_gateway.py",
    "calendar_sync.py", "calendar_write.py", "capacidades.py", "cascada_clinica.py",
    "contexto_caso.py", "correo.py", "correo_responder.py", "cost_guard.py",
    "digest.py", "evals.py", "fugu.py", "healthcheck.py", "ia.py", "ig_inbox.py",
    "instagram_dm.py", "pendientes.py", "rebuild_agents.py",
    "responder_con_datos.py", "salida.py", "seguridad_sweep.py", "staging.py",
    "subir_historial_drive.py", "transcribe_audios.py",
    "vega_gate.py", "vigia.py",
}

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def main():
    # --- 1. El helper hace lo que promete ---
    import _casa
    antes = os.environ.pop("BTP_REPO", None)
    try:
        ok(_casa.casa_base() == os.path.expanduser("~/claudecode"),
           "sin BTP_REPO, casa_base() es ~/claudecode (no el árbol de al lado)")
        os.environ["BTP_REPO"] = "/tmp/otra-casa"
        ok(_casa.casa_base() == "/tmp/otra-casa", "BTP_REPO manda")
        ok(_casa.state_dir() == os.path.join("/tmp/otra-casa", "tools", "state"),
           "state_dir() cuelga de casa base")
        os.environ["BTP_STATE_DIR"] = "/tmp/estado-aislado"
        ok(_casa.state_dir() == "/tmp/estado-aislado", "BTP_STATE_DIR aísla el estado en tests")
        del os.environ["BTP_STATE_DIR"]
        # La trampa que motivó todo, comprobada por COMPORTAMIENTO y no leyendo el texto (el
        # primer intento buscaba la cadena «__file__» en el cuerpo y saltaba por el comentario
        # que explica justamente que no se usa): se copia el helper a otro directorio, se importa
        # desde allí, y tiene que seguir apuntando al sistema vivo. Eso es lo que pasa de verdad
        # cuando un tool vive en un worktree.
        import shutil
        import tempfile
        _otro = tempfile.mkdtemp(prefix="casa_lejos_")
        shutil.copy(os.path.join(TOOLS, "_casa.py"), os.path.join(_otro, "_casa_copia.py"))
        sys.path.insert(0, _otro)
        del os.environ["BTP_REPO"]
        import _casa_copia
        ok(_casa_copia.casa_base() == os.path.expanduser("~/claudecode"),
           "una COPIA del helper en otro árbol sigue apuntando a casa base (no a su directorio)")
        sys.path.remove(_otro)
    finally:
        os.environ.pop("BTP_REPO", None)
        if antes is not None:
            os.environ["BTP_REPO"] = antes

    # --- 2. La lista solo puede encoger: ningún tool NUEVO puede estrenar el patrón ---
    actuales = set()
    for nombre in sorted(os.listdir(TOOLS)):
        if not nombre.endswith(".py"):
            continue
        # El helper enseña el antipatrón en su docstring a propósito: no se juzga a sí mismo.
        if nombre == "_casa.py":
            continue
        try:
            src = open(os.path.join(TOOLS, nombre), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        if PATRON.search(src):
            actuales.add(nombre)

    nuevos = sorted(actuales - CONOCIDOS)
    ok(not nuevos,
       "ningún tool NUEVO resuelve su raíz al árbol actual — si sale alguno, o usa "
       "_casa.casa_base() o se añade a CONOCIDOS a conciencia: %s" % nuevos)

    # Los que ya se migraron no pueden volver atrás sin que se note.
    migrados = sorted(CONOCIDOS - actuales)
    if migrados:
        print("  ℹ️  ya migrados (bórralos de CONOCIDOS): %s" % ", ".join(migrados))

    print("  · con el patrón hoy: %d · en la lista: %d" % (len(actuales), len(CONOCIDOS)))
    print("RESULTADO raices_casa_base: %d OK, %d fallos" % (_pass, _fail))
    print("✅ RAÍCES EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
