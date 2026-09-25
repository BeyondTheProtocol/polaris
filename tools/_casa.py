#!/usr/bin/env python3
"""tools/_casa.py — dónde vive el sistema de verdad. Una sola respuesta para todos.

EL PROBLEMA QUE RESUELVE (12-sep-2026). Treinta y tres tools resolvían su raíz así:

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

Desde casa base eso da casa base y todo va bien. Desde un **worktree** da el worktree, que es un
árbol efímero y gitignorado donde no vive ni el estado, ni los guardados, ni la fuente de verdad.
Y como {{TITULAR}} trabaja con varias sesiones a la vez, y cada sesión se aísla en su worktree, ese
"desde un worktree" es el caso NORMAL, no el raro.

Lo que duele no es que falle: es que **no falla**. Devuelve una respuesta plausible y sigue:

  · `polaris_estado.py` dio **93/100 desde el worktree y 98/100 desde casa base**, el mismo segundo.
  · `radar_taller.py` devolvió **0 candidatos** en silencio durante semanas (había 141).
  · `archivar_nota.py` escribió cuatro entregables en un árbol que iba a borrarse, mientras decía
    «RAG reindexado» — porque `kb.py` sí reindexa casa base.

Los tres se arreglaron **por separado** el mismo día, que es la señal de que el problema era de
clase y no tres casualidades.

CÓMO SE USA

    from _casa import casa_base          # o: import _casa; _casa.casa_base()
    ROOT = casa_base()

Y para lo que de verdad debe resolverse contra el árbol actual (leer el código de al lado, correr
un test de este árbol), se sigue usando `dirname(dirname(__file__))` a propósito y **con un
comentario que lo diga**: `tests/test_raices_casa_base.py` distingue lo uno de lo otro por una
lista explícita, no adivinando.

Modelo copiado de `kb.py`, que ya lo hacía bien y lo dejó escrito en su línea 35: «casa base
SIEMPRE: la fuente de verdad y el índice viven ahí (gitignored), nunca en un worktree».
"""
import os

__all__ = ("casa_base", "en_worktree", "state_dir", "es_proceso_de_test")


def casa_base():
    """La raíz del sistema VIVO. `BTP_REPO` manda (tests y despliegues raros); si no, `~/claudecode`.

    No se deriva de `__file__` a propósito: esa es exactamente la trampa. Un tool copiado a un
    worktree tiene que seguir apuntando al sistema vivo, no a su copia.
    """
    return os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")


def en_worktree(ruta=None):
    """¿Este árbol es un worktree y no casa base? Útil para avisar antes de escribir algo que se
    va a perder cuando el worktree desaparezca."""
    aqui = os.path.abspath(ruta or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.abspath(casa_base()) != aqui


def state_dir():
    """El estado vivo, siempre en casa base. `BTP_STATE_DIR` lo aísla en tests."""
    return os.environ.get("BTP_STATE_DIR") or os.path.join(casa_base(), "tools", "state")


def es_proceso_de_test(argv0=None):
    """¿Este proceso ES un test (`tests/test_*.py`)? Para que el dinero real no dependa de que cada
    test se acuerde de aislarse (25-sep-2026: `test_gasto_tarifa` metía $4 falsos en el contador
    de casa base en cada pasada de la batería, y `test_perplexity_agent` su simulacro en el ledger
    de APIs). `python -c` y el stdin no cuentan: sin fichero no hay forma de saberlo."""
    import sys
    a = argv0 if argv0 is not None else (sys.argv[0] if sys.argv else "")
    if a in ("", "-c", "-"):
        return False
    a = os.path.abspath(a)
    return (os.path.basename(os.path.dirname(a)) == "tests"
            and os.path.basename(a).startswith("test_"))
