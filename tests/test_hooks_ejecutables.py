#!/usr/bin/env python3
"""test_hooks_ejecutables.py — un hook registrado tiene que poder ARRANCAR tal como lo lanza el harness.

POR QUÉ EXISTE (22-sep-2026). `settings.json` lanza los hooks como `${CLAUDE_PROJECT_DIR}/.claude/
hooks/x.py`, sin intérprete delante. Si el fichero no tiene bit de ejecución, el harness recibe
«permission denied», lo trata como error no bloqueante y sigue: el hook NO CORRE y nadie se entera.
Así estuvieron desde que se crearon `regla_en_accion.py` (25-jul), `salida_guard.py`,
`entrada_guard.py` y `ok_envio_prompt.py` (20-sep): el freno de envíos sin OK de {{TITULAR}} y el
escáner anti-inyección, apagados en todas las sesiones interactivas. Sus tests pasaban porque los
invocan con `python3 hook.py`, que no necesita el bit. Se cazó al probar un freno EN VIVO.

Qué se exige a cada comando de hook de cada `.claude/settings*.json`:
  · si empieza por un intérprete (`python3 …`, `bash …`), nada más;
  · si no, el fichero existe, tiene bit de ejecución en disco Y en el índice de git (100755): el
    del índice es el que viaja a los worktrees y a otra máquina; el del disco, el que usa esta.
"""
import glob
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTERPRETES = ("python", "python3", "bash", "sh", "zsh", "node", "/usr/bin/env")

fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def comandos():
    for f in sorted(glob.glob(os.path.join(ROOT, ".claude", "settings*.json"))):
        try:
            s = json.load(open(f, encoding="utf-8"))
        except Exception as e:
            check(False, "%s se puede leer (%s)" % (os.path.basename(f), e))
            continue
        for evento, lista in (s.get("hooks") or {}).items():
            for h in lista or []:
                for c in h.get("hooks") or []:
                    if c.get("type") == "command" and c.get("command"):
                        yield os.path.basename(f), evento, c["command"]


def modo_git(rel):
    p = subprocess.run(["git", "-C", ROOT, "ls-files", "-s", "--", rel],
                       capture_output=True, text=True)
    return p.stdout.split()[0] if p.stdout.strip() else ""


def revisar_worktrees(rutas):
    """Los worktrees VIEJOS no heredan el arreglo (24-sep-2026).

    Arreglar el bit en el índice de git solo sirve para los worktrees que NAZCAN después: los ya
    creados se quedan con el 644 que copiaron. El 22-sep, en `cancel-netlify-helptitular-deploy`,
    `salida_guard.py` no era ejecutable, el hook falló en modo no bloqueante y un `reply` real a
    {{CENTRO}} tras un simple «Siguiente» lo frenó el clasificador del modo auto, no el muro. Cada
    worktree es una sesión posible: uno con el freno apagado es un freno apagado.

    Mira los worktrees de CASA BASE (donde viven), no los del árbol desde el que corre el test.
    Si no hay casa base con worktrees —runner público, otra máquina—, no hay nada que revisar."""
    casa = os.path.expanduser("~/claudecode")
    base = os.path.join(casa, ".claude", "worktrees")
    if not os.path.isdir(base):
        return
    nombres = {os.path.basename(r) for r in rutas}
    n = 0
    for wt in sorted(glob.glob(os.path.join(base, "*"))):
        hooks = os.path.join(wt, ".claude", "hooks")
        if not os.path.isdir(hooks):
            continue
        for h in sorted(glob.glob(os.path.join(hooks, "*"))):
            if os.path.basename(h) not in nombres:
                continue          # no está registrado como comando: da igual su bit
            n += 1
            check(os.access(h, os.X_OK), "ejecutable en el worktree %s: %s"
                  % (os.path.basename(wt), os.path.basename(h)))
    check(n > 0, "hay hooks de worktrees que revisar (%d)" % n)


def main():
    vistos = set()
    n = 0
    for fichero, evento, cmd in comandos():
        n += 1
        primero = cmd.strip().split()[0]
        if os.path.basename(primero) in INTERPRETES:
            continue
        ruta = primero.replace("${CLAUDE_PROJECT_DIR}", ROOT).replace("$CLAUDE_PROJECT_DIR", ROOT)
        if ruta in vistos:
            continue
        vistos.add(ruta)
        rel = os.path.relpath(ruta, ROOT)
        etiqueta = "%s (%s, %s)" % (rel, evento, fichero)
        check(os.path.isfile(ruta), "existe: " + etiqueta)
        check(os.access(ruta, os.X_OK), "ejecutable en disco: " + etiqueta)
        check(modo_git(rel) == "100755", "100755 en git: " + etiqueta)
    check(n > 0, "hay hooks registrados que revisar (%d)" % n)
    revisar_worktrees(vistos)
    if fallos:
        print("❌ %d fallo(s). Arreglo: git update-index --chmod=+x <hook> && chmod +x <hook>"
              % len(fallos))
        return 1
    print("✅ HOOKS EJECUTABLES EN VERDE (%d ficheros)" % len(vistos))
    return 0


if __name__ == "__main__":
    sys.exit(main())
