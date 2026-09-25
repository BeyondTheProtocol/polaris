#!/usr/bin/env python3
"""launch_loopback_guard.py — un servidor de preview escucha solo en este Mac, nunca en la red.

POR QUÉ EXISTE (26-sep-26). `tests/test_puertos_loopback.py` se puso ROJO en casa base: un
preview de la web (`node .mgc-staging/ruta-animada/.output/server/index.mjs`) escuchaba en
`*:3217`, es decir, en TODAS las interfaces, visible desde la wifi en la que esté el portátil.
Lo había arrancado otra sesión desde su `.claude/launch.json` con `PORT=3217` y sin `HOST`: el
servidor de Nitro, sin host, se ata a 0.0.0.0. La deuda `preview_node_expuesto_lan` lo recoge.

El test lo ve DESPUÉS, con el puerto ya abierto. Este hook lo para ANTES: cuando una sesión
escribe o edita un `.claude/launch.json`, cada configuración que arranca un servidor (tiene
`port` y un comando) tiene que decir explícitamente que escucha en loopback. Si no, se deniega
con el arreglo concreto. Es la clase entera, no el caso: vale para Nitro, Next, `http.server`,
uvicorn o lo que venga.

QUÉ CUENTA COMO LOOPBACK: `HOST=`/`HOSTNAME=`/`NITRO_HOST=`/`BIND=` a 127.0.0.1 o localhost, o
`--host`/`--hostname`/`--bind`/`-b`/`-H` con ese valor. También los servidores de DESARROLLO que ya
escuchan en localhost por defecto, sin `--host`: `vite` y `nuxt dev`/`nuxi dev` (comprobado el
26-sep-26 en el código de listhen 1.10, que usa Nuxt: sin host y sin modo público, `localhost`).
Si el comando es `pnpm/npm/yarn [run] dev`, se lee el script `dev` del package.json del proyecto
para saber qué arranca de verdad. Con `--host` a secas o a 0.0.0.0, nunca. Una configuración que solo trae
`url` (se engancha a un servidor que ya corre) no arranca nada y no se mira.

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026: la
regla «nada escucha fuera de loopback» (S14) es suya; esto la adelanta al momento de escribir.

FAIL-OPEN como sus hermanos (worktree_guard, rama_vista_guard): si el JSON no se puede leer o el
hook revienta, deja pasar; el test de puertos sigue detrás. Escotilla: `BTP_LOOPBACK_OK=1`.
LÍMITE: no ve un `launch.json` escrito por Bash (heredoc, `cp`); ahí queda el test de puertos.

Contrato de hooks: exit 0 permite · exit 2 deniega (el motivo va por stderr).
"""
import json
import os
import re
import sys

LOOP = r"(127\.0\.0\.1|localhost|::1|\[::1\])"
POR_ENV = re.compile(r"\b(HOST|HOSTNAME|NITRO_HOST|BIND|SERVER_HOST)=" + LOOP + r"\b", re.I)
POR_FLAG = re.compile(r"(--host(name)?|--bind|--listen|(?<!\S)-b|(?<!\S)-H)(=|\s+)" + LOOP, re.I)


def es_launch(path):
    return bool(path) and os.path.normpath(path).endswith(os.path.join(".claude", "launch.json"))


def contenido_final(tool, entrada):
    """El texto del launch.json tal como quedaría tras la herramienta. None si no se sabe."""
    path = entrada.get("file_path") or ""
    if tool == "Write":
        return entrada.get("content")
    try:
        with open(path, encoding="utf-8") as fh:
            texto = fh.read()
    except Exception:
        return None
    ediciones = entrada.get("edits") if tool == "MultiEdit" else [entrada]
    for e in ediciones or []:
        viejo, nuevo = e.get("old_string"), e.get("new_string")
        if viejo is None or nuevo is None or viejo not in texto:
            return None
        texto = texto.replace(viejo, nuevo) if e.get("replace_all") else texto.replace(viejo, nuevo, 1)
    return texto


DEV_LOCAL = re.compile(r"\bvite\b|\bnux[ti] dev\b")


def _script(raiz, nombre):
    """El script `nombre` del package.json del proyecto, o "" si no se puede leer."""
    try:
        with open(os.path.join(raiz or "", "package.json"), encoding="utf-8") as fh:
            return str((json.load(fh).get("scripts") or {}).get(nombre) or "")
    except Exception:
        return ""


def expuestas(texto, raiz=None):
    """Nombres de las configuraciones que arrancan un servidor sin atarlo a loopback.
    `raiz`: el proyecto del launch.json, para leer qué hace `pnpm dev`."""
    d = json.loads(texto)
    malas = []
    for c in d.get("configurations") or []:
        if not isinstance(c, dict) or not c.get("port"):
            continue
        partes = [str(c.get("runtimeExecutable") or "")] + [str(a) for a in c.get("runtimeArgs") or []]
        env = c.get("env") or {}
        partes += ["%s=%s" % (k, v) for k, v in env.items()] if isinstance(env, dict) else []
        cmd = " ".join(p for p in partes if p)
        if not cmd.strip():
            continue                                   # solo `url`: se engancha, no arranca
        if POR_ENV.search(cmd) or POR_FLAG.search(cmd):
            continue
        m = re.search(r"\b(pnpm|npm|yarn|bun)\s+(run\s+)?(\w[\w:-]*)", cmd)
        real = cmd + " " + (_script(raiz, m.group(3)) if m else "")
        if DEV_LOCAL.search(real) and not re.search(r"--host\b|\bHOST=", real):
            continue                                   # vite / nuxt dev: localhost por defecto
        malas.append(c.get("name") or "(sin nombre)")
    return malas


def main():
    if os.environ.get("BTP_LOOPBACK_OK") == "1":
        return 0
    try:
        data = json.loads(sys.stdin.read() or "{}")
        tool = data.get("tool_name") or ""
        entrada = data.get("tool_input") or {}
        if tool not in ("Write", "Edit", "MultiEdit") or not es_launch(entrada.get("file_path")):
            return 0
        texto = contenido_final(tool, entrada)
        if texto is None:
            return 0
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(entrada.get("file_path"))))
        malas = expuestas(texto, raiz)
    except Exception:
        return 0                                       # fail-open: el test de puertos sigue detrás
    if not malas:
        return 0
    sys.stderr.write(
        "LOOPBACK ⛔ %s arranca un servidor sin decir que escucha solo en este Mac. Sin host, "
        "Nitro, Next, http.server y compañía se abren a toda la red (26-sep-26: *:3217, "
        "test_puertos_loopback en rojo).\n"
        "Arreglo: añade `HOST=127.0.0.1` (Nitro/Nuxt, Next: `-H 127.0.0.1`, uvicorn/http.server: "
        "`--host/--bind 127.0.0.1`) a runtimeArgs o a `env`. Escotilla: BTP_LOOPBACK_OK=1.\n"
        % ", ".join("«%s»" % m for m in malas))
    return 2


if __name__ == "__main__":
    # Watchdog: si tardo más que el timeout de settings (5 s), deniego yo antes de que Claude Code
    # me cancele y lo convierta en un permitir. Ver _watchdog.py y tests/test_guard_timeout.py.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:  # sin el vigilante el guard sigue siendo el guard: su falta no lo tumba
        import _watchdog
    except ImportError:
        _watchdog = None
        sys.stderr.write("launch_loopback_guard: falta _watchdog.py; sigo sin vigilante\n")
    if _watchdog:
        _watchdog.armar(5, 'launch_loopback_guard')
    sys.exit(main())
