#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de mini.sh — el Air trabajando dentro del mini.

Lo que importa aquí no es que conecte (eso depende de que el mini esté despierto),
sino que **cuando no puede, lo diga rápido y con la salida**. Una herramienta de
conexión que se cuelga o que miente es peor que no tenerla: te deja parada sin saber
por qué, y con el portátil eso pasa a menudo (mini dormido, wifi de hospital, avión).
"""
import os
import subprocess
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(RAIZ, "tools", "mini.sh")

fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def _entorno_falso(base, hostname="MacBook-Air", ssh_ok=False):
    """PATH con `hostname` y `ssh` de mentira, para probar sin tocar la red."""
    binp = os.path.join(base, "bin")
    os.makedirs(binp, exist_ok=True)

    with open(os.path.join(binp, "hostname"), "w") as fh:
        fh.write("#!/bin/bash\necho %s\n" % hostname)
    # ssh falso: falla o responde según el caso, y NUNCA sale a la red.
    with open(os.path.join(binp, "ssh"), "w") as fh:
        fh.write("#!/bin/bash\n"
                 + ("echo 'sesion-falsa'; exit 0\n" if ssh_ok else "exit 255\n"))
    for n in ("hostname", "ssh"):
        os.chmod(os.path.join(binp, n), 0o755)
    return dict(os.environ, PATH=binp + os.pathsep + os.environ["PATH"])


def correr(env, args=(), timeout=20):
    t0 = time.time()
    p = subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True,
                       env=env, timeout=timeout)
    return p, time.time() - t0


def main():
    base = tempfile.mkdtemp(prefix="btp_mini_")

    print("── en el propio mini no hace nada ──")
    env = _entorno_falso(base, hostname="Polaris")
    p, _ = correr(env)
    check(p.returncode == 0, "sale 0 en el mini")
    check("Ya estás EN el mini" in p.stdout, "lo dice claro en vez de intentar conectar")

    print("── el mini no responde ──")
    env = _entorno_falso(base, ssh_ok=False)
    p, tardo = correr(env)
    check(p.returncode == 1, "sale con error, no finge que conectó")
    check("No llego al mini" in p.stdout, "lo dice en cristiano")
    check("Tailscale" in p.stdout and "dormido" in p.stdout,
          "explica las causas reales (Tailscale, mini dormido)")
    check("copia local" in p.stdout, "ofrece la salida: trabajar con la copia local")
    check("deploy_ff" in p.stdout, "avisa de que luego habrá que fusionar")
    check(tardo < 15, "no se queda colgado (tardó %.1fs)" % tardo)

    print("── --estado con el mini vivo ──")
    env = _entorno_falso(base, ssh_ok=True)
    p, _ = correr(env, ["--estado"])
    check(p.returncode == 0 and "responde" in p.stdout, "informa de que el mini está")

    print("── el aviso del login sale ANTES de conectar ──")
    fuente = open(SCRIPT, encoding="utf-8").read()
    check("unlock-keychain" in fuente and "login" in fuente,
          "explica el Llavero bloqueado por ssh (el token del fichero caducó el 13-jul)")
    check("new -A" in fuente, "usa `tmux new -A`: engancha la sesión viva, no duplica")
    check("ConnectTimeout" in fuente, "todas las conexiones llevan timeout corto")
    # mosh arranca mosh-server por ssh NO interactivo, donde zsh solo lee ~/.zshenv
    # (que en el mini no existe): sin ruta absoluta sale «mosh-server: command not found».
    check("--server=" in fuente, "a mosh se le pasa la RUTA del servidor, no se fía del PATH")

    # ESTE es el bug que {{TITULAR}} se comió el 25-jul: `ssh -t polaris "tmux …"` es un
    # comando remoto, no un login shell, así que zsh no lee `.zprofile` y /opt/homebrew
    # no está en el PATH. Salía «tmux: command not found» y la conexión moría sin decir
    # por qué. Lo mismo que ya estaba blindado para mosh y que aquí se me había pasado.
    check("ruta_remota" in fuente, "resuelve las rutas remotas en vez de fiarse del PATH")
    check('"$TMUX_REMOTO"' in fuente or "'$TMUX_REMOTO'" in fuente,
          "invoca tmux por su ruta absoluta")
    check("tmux new -A -s" not in fuente,
          "ya no queda ninguna llamada a `tmux` a pelo")
    check("no encuentro tmux" in fuente,
          "y si el mini no tuviera tmux, lo dice claro en vez de morir en silencio")

    print("── ninguna variable pegada a un carácter no ASCII ──")
    # El fallo del 25-jul: «$SESION» pegado a las comillas angulares. Con `set -u`, bash
    # toma los bytes del » como parte del nombre, no encuentra la variable y ABORTA justo
    # antes de conectar. Es invisible leyendo el código y solo revienta en ejecución.
    import re as _re
    pegadas = []
    for n, linea in enumerate(fuente.splitlines(), 1):
        if linea.lstrip().startswith("#"):
            continue
        for m in _re.finditer(r"\$[A-Za-z_][A-Za-z0-9_]*", linea):
            siguiente = linea[m.end():m.end() + 1]
            if siguiente and ord(siguiente) > 127:
                pegadas.append("línea %d: %s pegada a %r" % (n, m.group(), siguiente))
    check(not pegadas, "sin variables pegadas a multibyte (%s)" % (pegadas or "ninguna"))

    print("── arranca con un locale pobre ──")
    # La terminal del Air puede venir sin UTF-8; el script no puede depender de eso.
    env_pobre = _entorno_falso(os.path.join(base, "locale"), ssh_ok=False)
    env_pobre.update(LANG="C", LC_ALL="C")
    p, _ = correr(env_pobre)
    check("unbound" not in (p.stdout + p.stderr), "no revienta con LANG=C")

    import shutil
    shutil.rmtree(base, ignore_errors=True)

    print()
    if fallos:
        print("❌ %d fallo(s): %s" % (len(fallos), "; ".join(fallos)))
        return 1
    print("✅ mini.sh OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
