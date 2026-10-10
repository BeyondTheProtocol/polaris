#!/usr/bin/env python3
"""instala_muro_usuario.py — el muro mínimo, en TODAS las carpetas desde las que se abre Claude.

POR QUÉ EXISTE (10-oct-2026, plan «Claude a punto para Polaris», paso 7). Los hooks del muro viven
en `~/claudecode/.claude/settings.json`, que solo se carga dentro de claudecode. Una sesión abierta
en `~/projects/…` o en `$HOME` corría sin ninguno: ni la ventanilla clínica, ni el freno de salida,
ni el de entrada. Los lanzadores de `~/.claude/hooks/` (2-oct) se hicieron a mano y no tenían test:
un hook nuevo sin lanzador da rc 127 y falla ABIERTO.

QUÉ HACE
  1. Copia a `~/.claude/settings.json` las entradas de CUATRO guards tal cual están en el
     settings.json de casa base: `clinico_guard`, `salida_guard`, `scite_guard`, `entrada_guard`.
     El resto de hooks (enrutado, plan, worktree…) son de claudecode y se quedan allí.
  2. Regenera un lanzador en `~/.claude/hooks/` por cada hook de casa base.

LA CADENA DEL COMANDO VA IDÉNTICA A LA DEL PROYECTO, y no es estética. Claude Code ejecuta una sola
vez un hook declarado igual en dos ficheros de settings; con un espacio de diferencia lo ejecuta
dos veces (probado el 10-oct-26 con la 2.1.287: 1 ejecución contra 2). Por eso este instalador no
escribe comandos: los copia.

Uso:
  python3 tools/instala_muro_usuario.py            # dice qué falta o sobra, no toca nada (rc 1 si hay deriva)
  python3 tools/instala_muro_usuario.py --apply    # lo deja al día (copia previa en ~/.claude/backups/)
Quitar: borra la clave `hooks` de `~/.claude/settings.json`.
"""
import json
import os
import shutil
import stat
import sys
import time

GUARDS = ("clinico_guard.py", "salida_guard.py", "scite_guard.py", "entrada_guard.py")
EVENTOS = ("PreToolUse", "PostToolUse")
MARCA = ("Muro mínimo para toda carpeta. Lo escribe tools/instala_muro_usuario.py copiando las "
         "entradas de ~/claudecode/.claude/settings.json: NO editar a mano (la cadena debe ser "
         "idéntica o el hook corre dos veces dentro de claudecode).")


def casa_base():
    ov = os.environ.get("BTP_REPO")
    raiz = os.path.abspath(ov) if ov else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return raiz.split(os.sep + os.path.join(".claude", "worktrees") + os.sep)[0]


def dir_usuario():
    return os.environ.get("BTP_CLAUDE_USER_DIR") or os.path.expanduser("~/.claude")


def _es_guard(h):
    return (h.get("command") or "").endswith(tuple("/.claude/hooks/" + g for g in GUARDS))


def bloque(settings_proyecto):
    """Las entradas de los cuatro guards, copiadas del settings del proyecto sin tocar una coma."""
    out = {}
    for evento in EVENTOS:
        for grupo in (settings_proyecto.get("hooks") or {}).get(evento) or []:
            hooks = [dict(h) for h in grupo.get("hooks") or [] if _es_guard(h)]
            if hooks:
                nuevo = {k: v for k, v in grupo.items() if k not in ("hooks", "_comment")}
                nuevo["hooks"] = hooks
                out.setdefault(evento, []).append(nuevo)
    return out


def lanzador(real):
    if real.endswith(".py"):
        return ("#!/usr/bin/env python3\n"
                "# Lanzador (instala_muro_usuario.py): sesiones que arrancan en $HOME. Ejecuta el hook "
                "real de casa base.\n"
                "import runpy, sys\nsys.argv[0] = %r\nrunpy.run_path(%r, run_name='__main__')\n"
                % (real, real))
    return ("#!/bin/sh\n# Lanzador (instala_muro_usuario.py): sesiones que arrancan en $HOME. "
            "Ejecuta el hook real de casa base.\nexec %s \"$@\"\n" % real)


def hooks_de_casa_base(base):
    d = os.path.join(base, ".claude", "hooks")
    return sorted(f for f in os.listdir(d)
                  if f.endswith((".py", ".sh")) and not f.startswith("_"))


def deriva(base, usuario):
    """Lista de textos: qué no cuadra entre casa base y los ajustes de usuario. Vacía = al día."""
    falta = []
    proyecto = json.load(open(os.path.join(base, ".claude", "settings.json"), encoding="utf-8"))
    quiero = bloque(proyecto)
    vistos = {os.path.basename(h["command"]) for g in quiero.values() for m in g for h in m["hooks"]}
    for g in GUARDS:
        if g not in vistos:
            falta.append("casa base no declara %s: no hay nada que copiar" % g)
    try:
        tengo = json.load(open(os.path.join(usuario, "settings.json"), encoding="utf-8")).get("hooks") or {}
    except (OSError, ValueError):
        tengo = {}
    tengo = {k: v for k, v in tengo.items() if k != "_comment"}
    if tengo != quiero:
        falta.append("el bloque de hooks de usuario no coincide con el de casa base")
    for f in hooks_de_casa_base(base):
        real = os.path.join(base, ".claude", "hooks", f)
        p = os.path.join(usuario, "hooks", f)
        try:
            igual = open(p, encoding="utf-8").read() == lanzador(real)
        except OSError:
            igual = False
        if not igual:
            falta.append("lanzador ausente o distinto: %s" % f)
        elif not os.access(p, os.X_OK):
            falta.append("lanzador sin permiso de ejecución: %s" % f)
    return falta


def aplicar(base, usuario):
    proyecto = json.load(open(os.path.join(base, ".claude", "settings.json"), encoding="utf-8"))
    quiero = bloque(proyecto)
    p = os.path.join(usuario, "settings.json")
    try:
        actual = json.load(open(p, encoding="utf-8"))
    except OSError:
        actual = {}
    if os.path.exists(p):
        os.makedirs(os.path.join(usuario, "backups"), exist_ok=True)
        shutil.copy(p, os.path.join(usuario, "backups",
                                    "settings.json.antes-muro-" + time.strftime("%Y%m%d-%H%M%S")))
    actual["hooks"] = dict({"_comment": MARCA}, **quiero)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(actual, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)
    os.makedirs(os.path.join(usuario, "hooks"), exist_ok=True)
    for f in hooks_de_casa_base(base):
        dest = os.path.join(usuario, "hooks", f)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(lanzador(os.path.join(base, ".claude", "hooks", f)))
        os.chmod(dest, os.stat(dest).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def main(argv):
    base, usuario = casa_base(), dir_usuario()
    if "--apply" in argv:
        aplicar(base, usuario)
    falta = deriva(base, usuario)
    for t in falta:
        print("  ✗ " + t)
    print("muro de usuario: %s (%s ← %s)" % ("AL DÍA" if not falta else "%d cosa(s) sin cuadrar"
                                             % len(falta), usuario, base))
    return 1 if falta else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
