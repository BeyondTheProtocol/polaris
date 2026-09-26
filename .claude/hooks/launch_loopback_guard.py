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
`--host`/`--hostname`/`--bind`/`-b`/`-H` con ese valor. También `vite` (y `uvicorn` en Bash), que ya
escuchan en localhost por defecto. Si el comando es `pnpm/npm/yarn [run] dev`, se lee el script del
package.json del proyecto para saber qué arranca de verdad. Con `--host` a secas o a 0.0.0.0, nunca.
Una configuración que solo trae `url` (se engancha a un servidor que ya corre) no arranca nada.

`nuxt dev` NO ES SEGURO POR DEFECTO (26-sep-26, segunda vuelta). La app sí escucha en localhost
(listhen), pero el websocket HMR de su instancia Vite de SERVIDOR no: Nuxt 4.4.4 le fija el PUERTO
(24678, plugin `nuxt:server-hmr-port` de `@nuxt/vite-builder`) y no el HOST, y Vite sin host lo abre
en todas las interfaces. Medido con lsof en la web real: `--host 127.0.0.1` → `*:24678`;
`vite.server.hmr.host` → `*`; hook `vite:extendConfig` → `*` (solo llega a la instancia de cliente,
aunque en un Nuxt mínimo parecía bastar). Lo único que lo deja en `127.0.0.1` es un plugin de Vite
con `enforce: 'post'` que asigna `hmr.host` en su `config()`. Por eso `nuxt dev` pasa solo si el
nuxt.config del proyecto trae ese plugin (ficha `nuxt-dev-hmr-escucha-todas-interfaces`).

BASH (26-sep-26). Los dos servidores que pusieron rojo el test ese día se lanzaron por Bash, sin
launch.json. El guard mira también los comandos de Bash: trocea con shlex (respeta comillas, así que
`grep vite` o `git commit -m "nuxt dev"` no cuentan), sigue los `cd` y juzga cada tramo que arranca un
servidor conocido. `node <fichero>.js` a secas no se juzga: el host está en el código, no en el
comando; ahí sigue el test de puertos.

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026: la
regla «nada escucha fuera de loopback» (S14) es suya; esto la adelanta al momento de escribir.

FAIL-OPEN como sus hermanos (worktree_guard, rama_vista_guard): si el JSON no se puede leer o el
hook revienta, deja pasar; el test de puertos sigue detrás. Escotilla: `BTP_LOOPBACK_OK=1`.
LÍMITE: no ve un `launch.json` escrito por Bash (heredoc, `cp`) ni un servidor que decide su host
en el código; ahí queda el test de puertos.

Contrato de hooks: exit 0 permite · exit 2 deniega (el motivo va por stderr).
"""
import json
import os
import re
import shlex
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


DEV_LOCAL = re.compile(r"\bvite\b")
NUXT_DEV = re.compile(r"\bnux[ti] dev\b")
ABIERTO = re.compile(r"(--host(name)?|--bind|--listen|(?<!\S)-H)(=|\s+)(0\.0\.0\.0|::|\*)(?!\S)"
                     r"|(--host|--hostname)(?=\s*$|\s+-)|\b(HOST|HOSTNAME|NITRO_HOST|BIND)=(0\.0\.0\.0|::)\b")
HMR_POST = re.compile(r"enforce\s*:\s*['\"]post['\"]")
HMR_ASIGNA = re.compile(r"hmr\.host\s*(\?\?=|=)\s*['\"](127\.0\.0\.1|localhost|::1)['\"]")
ARREGLO_NUXT = ("en nuxt.config, vite.plugins: [{ name: 'hmr-solo-en-este-mac', enforce: 'post', "
                "config(c) { const h = c.server?.hmr; if (h && typeof h === 'object' && !h.server) "
                "h.host ??= '127.0.0.1' } }] (ni --host, ni vite.server.hmr.host, ni vite:extendConfig "
                "llegan al HMR del servidor; medido 26-sep-26)")


def nuxt_hmr_loopback(raiz):
    """True si el nuxt.config del proyecto ata el HMR a loopback con un plugin Vite `enforce: 'post'`
    que asigna `hmr.host` (lo único que funciona en Nuxt 4.4.4 con la web real, medido el 26-sep-26).
    False si hay nuxt.config y no lo hace. None si no hay nuxt.config que leer (monorepo, cwd raro):
    el que llama decide (fail-open)."""
    for nombre in ("nuxt.config.ts", "nuxt.config.js", "nuxt.config.mjs"):
        try:
            with open(os.path.join(raiz or "", nombre), encoding="utf-8") as fh:
                texto = fh.read()
        except Exception:
            continue
        return bool(HMR_POST.search(texto) and HMR_ASIGNA.search(texto))
    return None


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
        abierto = bool(re.search(r"--host\b|\bHOST=", real))
        if NUXT_DEV.search(real):
            if not abierto and nuxt_hmr_loopback(raiz) is not False:
                continue                               # nuxt dev con el HMR atado a loopback
        elif DEV_LOCAL.search(real) and not abierto:
            continue                                   # vite: localhost por defecto
        malas.append(c.get("name") or "(sin nombre)")
    return malas


ENVOLTORIOS = {"nohup", "exec", "env", "npx", "bunx", "time", "caffeinate"}
SEPARADORES = {"&&", "||", ";", "|", "&", ";;", "(", ")", "\n"}


def _tramos(comando):
    """Parte un comando de Bash en tramos (listas de tokens) por &&, ;, |, &. Respeta comillas."""
    lx = shlex.shlex(comando.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lx.whitespace_split = True
    tramos, actual = [], []
    for tok in lx:
        if tok in SEPARADORES or set(tok) <= set("&|;()"):
            if actual:
                tramos.append(actual)
            actual = []
        else:
            actual.append(tok)
    if actual:
        tramos.append(actual)
    return tramos


def _nucleo(tokens):
    """Quita asignaciones VAR=x y envoltorios (nohup, npx, pnpm exec…) del principio.
    Devuelve (entorno, tokens restantes con el primero reducido a su basename)."""
    env, i = [], 0
    while i < len(tokens):
        t = tokens[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
            env.append(t)
        elif os.path.basename(t) in ENVOLTORIOS:
            pass
        elif t in ("pnpm", "yarn", "bun") and i + 1 < len(tokens) and tokens[i + 1] in ("exec", "dlx", "x"):
            i += 1
        elif t == "timeout" and i + 1 < len(tokens):
            i += 1
        else:
            break
        i += 1
    resto = tokens[i:]
    if resto:
        resto = [os.path.basename(resto[0])] + resto[1:]
    return env, resto


def _juzgar(env, toks, raiz, prof=0):
    """None si el tramo no arranca un servidor conocido o lo arranca en loopback; si no, el tipo
    de fallo: 'nuxt' (falta atar el HMR) o 'host' (falta HOST/--host/--bind a loopback).
    Primero se decide SI es un servidor; el host se mira después (si no, `git commit -m "vite
    --host"` saldría denegado)."""
    if not toks:
        return None
    texto = " ".join(env + toks)
    abierto = bool(ABIERTO.search(texto))
    loop = bool(POR_ENV.search(texto) or POR_FLAG.search(texto)) and not abierto
    a, b = toks[0], (toks[1] if len(toks) > 1 else "")
    if a in ("nuxt", "nuxi") and b == "dev":
        return None if not abierto and nuxt_hmr_loopback(raiz) is not False else "nuxt"
    if a == "vite" and b not in ("build", "optimize"):
        return "host" if abierto else None             # localhost por defecto (y su HMR va dentro)
    if a == "uvicorn":
        return "host" if abierto else None             # 127.0.0.1 por defecto
    if a == "next" and b in ("dev", "start"):
        return None if loop else "host"
    if a.startswith("python") and "-m" in toks and "http.server" in toks:
        return None if loop else "host"
    if a in ("node", "bun") and any(t.endswith(".output/server/index.mjs") for t in toks[1:]):
        return None if loop else "host"
    if a in ("pnpm", "npm", "yarn", "bun") and prof == 0:
        nombre = toks[2] if b == "run" and len(toks) > 2 else b
        if nombre not in ("dev", "preview", "start", "serve"):
            return None
        script = _script(raiz, nombre)
        if not script:
            return None
        extra = [x for x in (toks[3:] if b == "run" else toks[2:]) if x != "--"]
        for t in _tramos(script):
            e2, r2 = _nucleo(t)
            veredicto = _juzgar(env + e2, r2 + extra, raiz, prof + 1)
            if veredicto:
                return veredicto
    return None


def expuestos_bash(comando, cwd):
    """[(tramo, tipo)] de los tramos de un comando de Bash que abren un servidor a la red."""
    raiz, malos = cwd, []
    for toks in _tramos(comando):
        env, resto = _nucleo(toks)
        if resto[:1] == ["cd"] and len(resto) > 1:
            raiz = os.path.normpath(os.path.join(raiz or "", os.path.expanduser(resto[1])))
            continue
        tipo = _juzgar(env, resto, raiz)
        if tipo:
            malos.append((" ".join(toks), tipo))
    return malos


def main_bash(data):
    comando = (data.get("tool_input") or {}).get("command") or ""
    if not re.search(r"nux[ti]|vite|next|uvicorn|http\.server|\.output/server|pnpm|npm|yarn|bun", comando):
        return 0                                       # vía rápida: ni huele a servidor
    malos = expuestos_bash(comando, data.get("cwd") or os.getcwd())
    if not malos:
        return 0
    tramo, tipo = malos[0]
    if tipo == "nuxt":
        arreglo = ("`nuxt dev` abre el websocket HMR de su servidor en todas las interfaces (*:24678) "
                   "aunque la app vaya a localhost. Arreglo: " + ARREGLO_NUXT + ".")
    else:
        arreglo = ("Arreglo: `HOST=127.0.0.1` (Nitro/Nuxt build), `-H 127.0.0.1` (Next), "
                   "`--bind 127.0.0.1` (http.server), `--host 127.0.0.1` (vite/uvicorn).")
    sys.stderr.write(
        "LOOPBACK ⛔ «%s» arranca un servidor abierto a toda la red, visible desde la wifi en la que "
        "esté el portátil (26-sep-26: test_puertos_loopback en rojo por esto).\n%s "
        "Escotilla: BTP_LOOPBACK_OK=1.\n" % (tramo[:120], arreglo))
    return 2


def main():
    if os.environ.get("BTP_LOOPBACK_OK") == "1":
        return 0
    try:
        data = json.loads(sys.stdin.read() or "{}")
        tool = data.get("tool_name") or ""
        entrada = data.get("tool_input") or {}
        if tool == "Bash":
            return main_bash(data)
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
        "`--host/--bind 127.0.0.1`) a runtimeArgs o a `env`. Si es `nuxt dev`, además %s. "
        "Escotilla: BTP_LOOPBACK_OK=1.\n"
        % (", ".join("«%s»" % m for m in malas), ARREGLO_NUXT))
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
