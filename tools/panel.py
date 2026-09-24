#!/usr/bin/env python3
"""tools/panel.py — panel del lazo (append-only) en Gestion/PANEL-LAZO.md.

Supervisar sin babysitting: cada acción del lazo deja un bloque {qué hizo · qué decidió ·
qué espera tu OK · qué falló · coste}. Append atómico (O_APPEND), como el audit de
salida.py. Lo escribe el dispatcher. Cuerpos sensibles van por hash/ID (el detalle vive
en el outbox), nunca PII en claro aquí. Sin dependencias (stdlib).
"""
import os
import sys
import time

# Casa base SIEMPRE, no el árbol donde vive esta copia del fichero. El 20-sep-2026 se perdieron
# dos veces entradas del panel por esto: un job del lazo que corría desde un worktree las escribía
# allí, y como 00_FUENTE-DE-VERDAD está gitignored, `git status` decía «limpio» y la poda se las
# llevaba. Se rescataron a mano (52 + 12) porque alguien miró los ignorados antes de borrar.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _casa import casa_base  # noqa: E402

PANEL = os.path.join(casa_base(), "00_FUENTE-DE-VERDAD", "Gestion", "PANEL-LAZO.md")


def ruta_panel():
    """Dónde escribe ESTA llamada. Se resuelve en cada append, no al importar.

    `BTP_PANEL` lo redirige (los tests lo apuntan a su tmp, como BTP_BANDEJA). Y en batería de
    tests (`BTP_TEST_BATTERY=1`) sin `BTP_PANEL`, nunca el panel de verdad: test_dispatcher.sh
    pasa `BTP_REPO=<árbol bajo prueba>` y cada pasada de test_all dejaba 12 entradas de jobs
    falsos en el PANEL-LAZO de ese árbol. En un worktree, eso bloqueaba la poda (gitignored =
    «se perdería»); en casa base, ensuciaba el panel real (22-sep-2026).
    """
    if os.environ.get("BTP_PANEL"):
        return os.environ["BTP_PANEL"]
    if os.environ.get("BTP_TEST_BATTERY") == "1":
        import tempfile
        return os.path.join(tempfile.gettempdir(), "btp-test-panel-%d" % os.getuid(), "PANEL-LAZO.md")
    return os.path.join(casa_base(), "00_FUENTE-DE-VERDAD", "Gestion", "PANEL-LAZO.md")


def append(job=None, did="", decided="", awaiting="", failed="", cost=""):
    PANEL = ruta_panel()
    os.makedirs(os.path.dirname(PANEL), exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    block = "\n## %s  job %s\n" % (ts, job or "-")
    block += "- QUÉ HIZO: %s\n" % (did or "-")
    block += "- QUÉ DECIDIÓ: %s\n" % (decided or "-")
    block += "- ESPERA OK: %s\n" % (awaiting or "—")
    block += "- FALLÓ: %s\n" % (failed or "—")
    block += "- COSTE: %s\n" % (cost or "-")
    fd = os.open(PANEL, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, block.encode("utf-8"))
    finally:
        os.close(fd)
    try:                      # espejo humano en BANDEJA.md (fail-soft; nunca rompe el panel)
        import bandeja
        if failed:
            bandeja.fallo(job or "-", failed)
        else:
            bandeja.resultado(job or "-", did or "-", awaiting)
    except Exception:
        pass


def _arg(a, name, default=""):
    return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else default


def main(argv):
    if not argv or argv[0] != "append":
        print('uso: panel.py append [--job id] [--did ..] [--decided ..] [--awaiting ..] [--failed ..] [--cost ..]')
        return 2
    a = argv[1:]
    append(job=_arg(a, "--job"), did=_arg(a, "--did"), decided=_arg(a, "--decided"),
           awaiting=_arg(a, "--awaiting"), failed=_arg(a, "--failed"), cost=_arg(a, "--cost"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
