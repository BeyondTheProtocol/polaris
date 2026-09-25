#!/usr/bin/env python3
"""tools/dependencias.py — quién depende de quién dentro de Polaris.

La idea es de graphify (un grafo del repo para consultar en vez de grepear), pero
hecha a nuestra medida. Medido el 25-sep-2026: en este repo hay ~1570 referencias
a `tools/x.py` escritas como RUTA (subprocess, .sh, plists de launchd, hooks de
settings.json) frente a ~490 imports. Un grafo que solo mire imports se pierde
tres de cada cuatro enlaces, y son justo los que rompen algo sin avisar.

Aristas (origen → destino), por tipo:
  import   — un .py importa un módulo del repo (ast, sin ejecutar nada)
  ruta     — un .py o .sh nombra la ruta de un script (subprocess, os.path.join…)
  launchd  — un .plist lanza el script
  config   — un .json lo nombra (settings de Claude Code: hooks o permisos)
  doc      — un .md lo menciona (no cuenta para «qué se rompe»)

Uso:
  python3 tools/dependencias.py quien <script> [--hondo]   # quién depende de él
  python3 tools/dependencias.py usa <script>               # de quién depende él
  python3 tools/dependencias.py huerfanos [--dir tools]    # scripts que nadie nombra (ni fuente ni memoria)
  python3 tools/dependencias.py centrales [-n 15]          # los más usados
  python3 tools/dependencias.py json                       # el grafo entero

<script> vale como ruta (`tools/salida.py`) o nombre (`salida`, `salida.py`).
Solo lectura. Sin LLM, sin dependencias fuera de la stdlib.
"""
import ast
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

RAIZ = os.environ.get("BTP_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPTS = (".py", ".sh")
FUENTES = (".py", ".sh", ".plist", ".json", ".md")
DUROS = ("import", "ruta", "launchd", "config")  # los que rompen algo si el destino cambia
_TOKEN = re.compile(r"[\w./${}-]*?[\w-]+\.(?:py|sh)\b")
_SALTAR = ("node_modules/", ".claude/worktrees/", "00_FUENTE-DE-VERDAD/")


def _ficheros(raiz):
    try:
        out = subprocess.run(["git", "-C", raiz, "ls-files"], capture_output=True,
                             text=True, timeout=30, stdin=subprocess.DEVNULL)
        if out.returncode == 0 and out.stdout.strip():
            return [f for f in out.stdout.splitlines() if not f.startswith(_SALTAR)]
    except (OSError, subprocess.SubprocessError):
        pass
    res = []
    for d, subdirs, fs in os.walk(raiz):
        subdirs[:] = [s for s in subdirs if not s.startswith(".git")]
        for f in fs:
            rel = os.path.relpath(os.path.join(d, f), raiz)
            if not rel.startswith(_SALTAR):
                res.append(rel)
    return res


def _tipo(origen):
    ext = os.path.splitext(origen)[1]
    return {".plist": "launchd", ".json": "config", ".md": "doc"}.get(ext, "ruta")


class Grafo:
    def __init__(self, raiz=RAIZ):
        self.raiz = raiz
        todos = _ficheros(raiz)
        self.scripts = sorted(f for f in todos if f.endswith(SCRIPTS))
        self._set = set(self.scripts)
        self._por_nombre = defaultdict(list)
        for s in self.scripts:
            self._por_nombre[os.path.basename(s)].append(s)
        self.aristas = set()  # (origen, destino, tipo)
        for f in todos:
            if f.endswith(FUENTES):
                self._leer(f)

    # ── resolución de un token a un script del repo ──
    def _resolver(self, token, origen):
        t = token.replace("${CLAUDE_PROJECT_DIR}", "").replace("$CLAUDE_PROJECT_DIR", "")
        t = t.lstrip("./")
        partes = t.split("/")
        for i in range(len(partes)):  # sufijos: a/b/tools/x.py → tools/x.py → x.py
            cand = "/".join(partes[i:])
            if cand in self._set:
                return cand
        base = partes[-1]
        junto = os.path.normpath(os.path.join(os.path.dirname(origen), base))
        if junto in self._set:
            return junto
        unicos = self._por_nombre.get(base, [])
        return unicos[0] if len(unicos) == 1 else None

    def _modulo(self, nombre, origen):
        base = nombre.split(".")[0] + ".py"
        for d in (os.path.dirname(origen), "tools", ".claude/hooks", "tests"):
            cand = os.path.normpath(os.path.join(d, base))
            if cand in self._set:
                return cand
        return None

    def _leer(self, f):
        try:
            with open(os.path.join(self.raiz, f), encoding="utf-8", errors="replace") as fh:
                texto = fh.read()
        except OSError:
            return
        tipo = _tipo(f)
        for m in _TOKEN.finditer(texto):
            dst = self._resolver(m.group(0), f)
            if dst and dst != f:
                self.aristas.add((f, dst, tipo))
        if f.endswith(".py"):
            try:
                arbol = ast.parse(texto)
            except (SyntaxError, ValueError):
                return
            for nodo in ast.walk(arbol):
                nombres = []
                if isinstance(nodo, ast.Import):
                    nombres = [a.name for a in nodo.names]
                elif isinstance(nodo, ast.ImportFrom) and nodo.module:
                    nombres = [nodo.module]
                for n in nombres:
                    dst = self._modulo(n, f)
                    if dst and dst != f:
                        self.aristas.add((f, dst, "import"))

    # ── consultas ──
    def buscar(self, que):
        if que in self._set:
            return que
        q = que if que.endswith(SCRIPTS) else que + ".py"
        cands = self._por_nombre.get(os.path.basename(q), [])
        if len(cands) == 1:
            return cands[0]
        if len(cands) > 1:
            raise SystemExit("ambiguo, di la ruta: " + ", ".join(cands))
        raise SystemExit("no encuentro el script: %s" % que)

    def quien(self, destino, hondo=False):
        """{origen: tipos} de quien depende de `destino` (transitivo si hondo, solo aristas duras)."""
        entrantes = defaultdict(list)
        for o, d, t in self.aristas:
            entrantes[d].append((o, t))
        res = defaultdict(set)
        pila, vistos = [destino], {destino}
        while pila:
            x = pila.pop()
            for o, t in entrantes[x]:
                if x != destino and t not in DUROS:
                    continue
                res[o].add(t if x == destino else "vía " + x)
                if hondo and t in DUROS and o not in vistos:
                    vistos.add(o)
                    pila.append(o)
        res.pop(destino, None)
        return res

    def usa(self, origen):
        res = defaultdict(set)
        for o, d, t in self.aristas:
            if o == origen:
                res[d].add(t)
        return res

    def grado_entrada(self, solo_duros=True):
        g = defaultdict(set)
        for o, d, t in self.aristas:
            if not solo_duros or t in DUROS:
                g[d].add(o)
        return g

    def huerfanos(self, directorio="tools", fuera=None):
        """Scripts de `directorio` que nada del repo nombra. Con `fuera` (carpetas de texto que git
        no rastrea: la fuente de verdad, la memoria), separa los que SÍ se documentan ahí.
        Devuelve (sin_mencion, solo_fuera). Por qué (26-sep-26): la primera versión dio 10 huérfanos
        y 7 estaban documentados como herramientas manuales en la fuente o la memoria (backup.py,
        lazo_telegram.sh…). Borrar por esa lista habría roto el backup a USB."""
        g = self.grado_entrada(solo_duros=False)
        pref = directorio.rstrip("/") + "/"
        cands = [s for s in self.scripts
                 if s.startswith(pref) and "/" not in s[len(pref):]
                 and not os.path.basename(s).startswith("_") and not g.get(s)]
        textos = []
        for d in fuera or []:
            for base, _dirs, fs in os.walk(d):
                for f in fs:
                    if f.endswith((".md", ".txt", ".json", ".jsonl")):
                        try:
                            with open(os.path.join(base, f), encoding="utf-8", errors="replace") as fh:
                                textos.append(fh.read())
                        except OSError:
                            pass
        todo = "\n".join(textos)
        solo_fuera = [s for s in cands if os.path.basename(s) in todo
                      or os.path.splitext(os.path.basename(s))[0] in todo]
        return [s for s in cands if s not in solo_fuera], solo_fuera


def _imprimir(res):
    for k in sorted(res, key=lambda k: (not any(t in DUROS for t in res[k]), k)):
        print("  %-55s %s" % (k, ", ".join(sorted(res[k]))))


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, resto = argv[0], argv[1:]
    g = Grafo()
    if cmd == "quien" and resto:
        dst = g.buscar(resto[0])
        res = g.quien(dst, hondo="--hondo" in resto)
        duros = sum(1 for v in res.values() if any(t in DUROS or t.startswith("vía") for t in v))
        print("%s ← %d dependientes (%d que se rompen si cambia; el resto son docs)" % (dst, len(res), duros))
        _imprimir(res)
    elif cmd == "usa" and resto:
        src = g.buscar(resto[0])
        res = g.usa(src)
        print("%s → usa %d scripts" % (src, len(res)))
        _imprimir(res)
    elif cmd == "huerfanos":
        d = resto[resto.index("--dir") + 1] if "--dir" in resto else "tools"
        fuera = [os.path.join(RAIZ, "00_FUENTE-DE-VERDAD"),
                 os.path.expanduser("~/.claude/projects/-Users-polaris-claudecode/memory")]
        hs, doc = g.huerfanos(d, fuera=[f for f in fuera if os.path.isdir(f)])
        print("%d scripts en %s/ sin ninguna mención (ni en el repo, ni en la fuente, ni en la memoria):"
              % (len(hs), d))
        for h in hs:
            print("  " + h)
        if doc:
            print("%d más que el repo no nombra pero la fuente o la memoria sí (uso manual, NO borrar a ciegas):"
                  % len(doc))
            for h in doc:
                print("  " + h)
    elif cmd == "centrales":
        n = int(resto[resto.index("-n") + 1]) if "-n" in resto else 15
        g_in = g.grado_entrada()
        for s, os_ in sorted(g_in.items(), key=lambda kv: -len(kv[1]))[:n]:
            print("  %4d  %s" % (len(os_), s))
    elif cmd == "json":
        json.dump({"scripts": g.scripts,
                   "aristas": [{"de": o, "a": d, "tipo": t} for o, d, t in sorted(g.aristas)]},
                  sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
