#!/usr/bin/env python3
"""ficha_guard.py — no nace una herramienta en tools/ sin su ficha (PreToolUse Write).

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.

POR QUÉ. Cada problema nuevo tendía a crear una pieza nueva, y una herramienta que nadie encuentra
se reescribe. Este hook para el `Write` que CREA `tools/**/<nueva>.py|.sh` si antes no existe su
ficha completa en `tools/fichas/`, y en el mismo instante enseña las 5 piezas más parecidas:
«busca antes de crear; si hay algo parecido, amplíalo».

QUÉ NO HACE
  · No toca las ediciones: si el fichero ya existe, pasa.
  · No es el freno último. Un `cat > tools/x.py` por Bash no pasa por aquí; lo caza
    `tests/test_fichas.py` en `test_all.sh`. Este hook es la ayuda en el momento de crear.
  · No exige que el test ya exista (se escribe después de la herramienta); eso lo exige la batería.

La ficha se busca en el MISMO árbol que el fichero pedido (el worktree de la sesión, o casa
base), no en el del hook: con muchas sesiones en paralelo, una ficha recién escrita en un
worktree todavía no está en casa base.

Contrato de hooks: exit 0 permite · exit 2 deniega (el motivo va por stderr).
FAIL-OPEN: una excepción interna deja pasar; lo que este guard no pueda juzgar no lo bloquea.
"""
import json
import os
import sys


def _raiz_de(ruta):
    """El árbol al que pertenece la ruta: el primer ancestro con `.git` (dir o fichero)."""
    d = os.path.dirname(ruta)
    while d and d != os.path.dirname(d):
        if os.path.exists(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return None


def veredicto(ruta):
    """None si puede pasar; texto del motivo si hay que denegar."""
    ruta = os.path.normpath(os.path.abspath(os.path.expanduser(ruta)))
    if os.path.exists(ruta) or not ruta.endswith((".py", ".sh")):
        return None
    raiz = _raiz_de(ruta)
    if not raiz:
        return None
    tools = os.path.join(raiz, "tools")
    if not ruta.startswith(tools + os.sep):
        return None
    sys.path.insert(0, tools)
    import fichas
    rel = os.path.relpath(ruta, tools).replace(os.sep, "/")
    if not fichas.es_pieza(rel):
        return None
    ficha = fichas.leer(rel, raiz)
    fallos = fichas.problemas_de(ficha, rel, raiz, mirar_test=False)
    if not fallos:
        return None
    lineas = ["FICHA ⛔ tools/%s no tiene ficha completa: %s." % (rel, fichas.AVISO),
              "   " + "; ".join(fallos)]
    try:
        cerca = fichas.parecidas(rel, raiz)
    except Exception:
        cerca = []
    if cerca:
        lineas.append("   Lo más parecido que ya hay:")
        lineas += ["     · tools/%s — %s" % (p, (para or "")[:100]) for p, para in cerca]
    lineas.append("   Si de verdad es nueva, escribe antes %s (campos: %s; estado_ficha «completa»)."
                  % (os.path.relpath(fichas.ruta_ficha(rel, raiz), raiz), ", ".join(fichas.CAMPOS)))
    lineas.append("   Plantilla: python3 tools/fichas.py plantilla %s" % rel)
    return "\n".join(lineas) + "\n"


def main():
    data = json.loads(sys.stdin.read() or "{}")
    if data.get("tool_name") != "Write":
        return 0
    ruta = (data.get("tool_input") or {}).get("file_path")
    if not ruta:
        return 0
    if not os.path.isabs(ruta):
        ruta = os.path.join(data.get("cwd") or os.getcwd(), ruta)
    motivo = veredicto(ruta)
    if motivo:
        sys.stderr.write(motivo)
        return 2
    return 0


if __name__ == "__main__":
    # Watchdog: si tardo más que el timeout de settings (10 s), deniego yo antes de que Claude Code
    # me cancele y lo convierta en un permitir. Ver _watchdog.py y tests/test_guard_timeout.py.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:  # sin el vigilante el guard sigue siendo el guard: su falta no lo tumba
        import _watchdog
    except ImportError:
        _watchdog = None
        sys.stderr.write("ficha_guard: falta _watchdog.py; sigo sin vigilante\n")
    if _watchdog:
        _watchdog.armar(10, 'ficha_guard')
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
