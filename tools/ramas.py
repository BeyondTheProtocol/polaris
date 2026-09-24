#!/usr/bin/env python3
"""tools/ramas.py — ver y limpiar las RAMAS de trabajo (git worktrees) de Polaris.

{{TITULAR}} trabaja con muchas sesiones a la vez. Para no pisarse, cada sesión que edita el repo trabaja en
su propia rama (worktree) — ver CLAUDE.md «Trabajar en paralelo». Esta tool muestra qué ramas hay vivas
y qué sesión de Claude está en cada una, y poda las que sobran. Solo-lectura salvo `limpia --si`.
Sin dependencias (stdlib).

Uso:
  python3 tools/ramas.py list            # ramas (worktrees) + qué sesión claude en cada una
  python3 tools/ramas.py conflictos      # ficheros que tocan DOS ramas a la vez (exit 1 si hay)
  python3 tools/ramas.py limpia          # git worktree prune + LISTA las ramas limpias y vacías
  python3 tools/ramas.py limpia --si     # además elimina esas ramas limpias y vacías
  python3 tools/ramas.py autopoda        # = limpia --si --avisar (rutina diaria git-barrido):
                                         #   poda lo fusionado y limpio (el residuo de tests no
                                         #   cuenta como trabajo) y avisa, sin spam, de lo dudoso
  python3 tools/ramas.py json            # salida JSON (la usa El Observatorio)

`limpia`/`limpia --si` MUTAN el `.git` de la casa base (worktree prune/remove) — por eso pasan
por el candado COMPARTIDO "git-mutex" (tools/git_mutex.py, el mismo que usa cerrar_sesion.py al
fusionar/podar). Antes mutaban SIN candado: era uno de los tres actores sin serializar que el
comité de arquitectura encontró sobre el `.git` compartido (A1, 10-jul-26 — hallazgo B).
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import git_mutex  # noqa: E402 — candado compartido "git-mutex" para las mutaciones de abajo

# Casa base = el repo PRINCIPAL (no un worktree). Si esta tool se importa DESDE un worktree (p.ej.
# seguimiento.py al detectar cabos sueltos), dirname(__file__) apuntaría al worktree y `es_base` se
# calcularía mal (el worktree se vería a sí mismo como base y casa base saldría como "rama colgada").
# Por eso resolvemos casa base por BTP_REPO/~/claudecode si existe — igual criterio que seguimiento.py;
# si no, caemos al árbol del fichero (retrocompatible al correr la tool desde casa base).
_FILE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HOME_BASE = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
ROOT = _HOME_BASE if os.path.isdir(os.path.join(_HOME_BASE, ".git")) else _FILE_ROOT


def _run(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return ""


def _run_rc(args):
    """Como `_run`, pero devuelve None si el comando FALLA, en vez de "" — que es
    indistinguible de una salida vacía legítima y se acababa leyendo como un cero."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=15)
    except Exception:            # noqa: BLE001
        return None
    return p.stdout if p.returncode == 0 else None


def worktrees():
    """[{path, branch, head, es_base}] a partir de `git worktree list --porcelain`."""
    out = _run(["git", "-C", ROOT, "worktree", "list", "--porcelain"])
    wts, cur = [], {}
    for ln in out.splitlines():
        if ln.startswith("worktree "):
            if cur:
                wts.append(cur)
            cur = {"path": ln[len("worktree "):], "branch": "(detached)", "head": ""}
        elif ln.startswith("branch "):
            cur["branch"] = ln[len("branch "):].replace("refs/heads/", "")
        elif ln.startswith("HEAD "):
            cur["head"] = ln[len("HEAD "):][:10]
    if cur:
        wts.append(cur)
    for w in wts:
        w["es_base"] = os.path.realpath(w["path"]) == os.path.realpath(ROOT)
    return wts


def _cwd_de(pid):
    out = _run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
    for ln in out.splitlines():
        if ln.startswith("n"):
            return ln[1:]
    return ""


def claude_sessions():
    """Sesiones de Claude Code vivas (no los procesos helper de la app)."""
    ps = _run(["ps", "-axo", "pid=,command="])
    sess = []
    for ln in ps.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        pid, _, cmd = ln.partition(" ")
        # el binario de sesión es .../claude-code/<ver>/claude.app/Contents/MacOS/claude
        if "/claude-code/" in cmd and "/MacOS/claude" in cmd and "--type=" not in cmd:
            sess.append({"pid": pid, "cwd": _cwd_de(pid)})
    return sess


def sesiones():
    """Cada sesión viva mapeada a su rama (worktree). [{pid, cwd, rama, es_base}]."""
    wts = worktrees()
    out = []
    for s in claude_sessions():
        cwd = s["cwd"]
        best = None
        for w in wts:
            p = w["path"]
            if cwd and (cwd == p or cwd.startswith(p.rstrip("/") + "/")):
                if best is None or len(p) > len(best["path"]):
                    best = w
        rama, es_base = "(fuera del repo)", False
        if best:
            es_base = best["es_base"]
            rama = "casa base" if es_base else best.get("branch", "?")
        out.append({"pid": s["pid"], "cwd": cwd, "rama": rama, "es_base": es_base})
    return out


def _rama_base():
    for w in worktrees():
        if w["es_base"]:
            return w.get("branch", "")
    return ""


# Trabajo VIVO que git no ve. `tools/state/` está gitignored, así que `git status --porcelain`
# dice «limpio» aunque dentro haya un borrador esperando la firma de {{TITULAR}} o un encargo suyo
# sin ejecutar: `limpia --si` se llevaba el worktree y con él el borrador, sin excepción ni log.
#
# Se comprueba por FICHERO y no con `--ignored`, porque git colapsa la salida a `!! tools/state/`
# (el directorio, no su contenido) y ese directorio existe en TODOS los worktrees — mirarlo
# entero no distinguiría un borrador vivo de la basura de siempre.
_RUTAS_TRABAJO_VIVO = (
    "tools/state/outbox/pending",     # borradores esperando su OK (el gate de salida)
    "tools/state/queue/pending",      # encargos aún sin ejecutar
    "tools/state/queue/processing",   # encargos a medio ejecutar
)

# Árboles enteros que NUNCA deberían tener nada dentro de un worktree: si hay algo, es que un
# proceso resolvió su ruta contra el árbol donde corría en vez de contra casa base, y eso se
# pierde en la poda sin que `git status` diga nada (están gitignored).
# El 20-sep-2026 pasó DOS VECES el mismo día: 52 y 12 entradas del panel del lazo, más 52.682
# líneas del log de auditoría clínica. Se salvaron porque alguien miró a mano; el freno es esto.
_ARBOLES_QUE_NO_TOCAN_AQUI = (
    "00_FUENTE-DE-VERDAD",            # la fuente de verdad vive SOLO en casa base
    ".claude/logs",                   # trazas de auditoría (clinico-access.log, regla-en-accion)
)
_TOPE_HALLAZGOS = 40                  # con saber que hay, basta; no hace falta listarlo todo


# RESIDUO DE TESTS (22-sep-2026). `tests/test_dispatcher.sh` escribía 12 jobs falsos en el
# PANEL-LAZO del árbol bajo prueba en cada pasada de test_all (ya no: panel.ruta_panel()). Ese
# residuo bloqueaba la poda igual que un panel de verdad, y el 21 y 22-sep hubo que podar a mano.
# Se reconoce SOLO si todo encaja; ante cualquier duda, es trabajo vivo (fail-closed):
#   · el fichero entero son bloques del panel con su forma exacta (nada más dentro);
#   · van en ráfagas: cada pasada de la batería escribe una docena de entradas a segundos unas de
#     otras (y en un worktree se corre test_all muchas veces: una ráfaga por pasada). Toda ráfaga
#     debe tener al menos _RAFAGA_MIN entradas y durar menos de _RAFAGA_MAX_S; una entrada suelta
#     es ritmo del lazo real, que tarda minutos por job;
#   · ningún job es conocido en casa base (ni en su cola ni en su panel): un job real que corrió
#     desde un worktree (el caso del 20-sep) sí está en la cola de casa base.
_PANEL_REL = os.path.join("00_FUENTE-DE-VERDAD", "Gestion", "PANEL-LAZO.md")
_BLOQUE_PANEL = re.compile(
    r"\n?## (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)  job (\S+)\n"
    r"- QUÉ HIZO: [^\n]*\n- QUÉ DECIDIÓ: [^\n]*\n- ESPERA OK: [^\n]*\n"
    r"- FALLÓ: [^\n]*\n- COSTE: [^\n]*\n")
_RAFAGA_HUECO_S = 30                  # más separación que esto entre dos entradas = otra ráfaga
_RAFAGA_MIN = 5
_RAFAGA_MAX_S = 120


def _jobs_de_casa_base():
    """Ids de job que casa base conoce: nombres de la cola (`<rango>-<ts>-<id>.json`) + panel."""
    sys.path.insert(0, HERE)
    import _casa
    ids = set()
    for _raiz, _dirs, ficheros in os.walk(os.path.join(_casa.state_dir(), "queue")):
        for f in ficheros:
            if f.endswith(".json"):
                ids.add(f[:-5].rsplit("-", 1)[-1])
    try:
        with open(os.path.join(_casa.casa_base(), _PANEL_REL), encoding="utf-8") as fh:
            ids.update(re.findall(r"^## \S+  job (\S+)$", fh.read(), re.M))
    except OSError:
        pass
    return ids


def _es_residuo_de_tests(fichero):
    """¿Este PANEL-LAZO.md es solo residuo de la batería? Ver el bloque de arriba."""
    try:
        with open(fichero, encoding="utf-8") as fh:
            txt = fh.read()
    except (OSError, UnicodeDecodeError):
        return False
    bloques = list(_BLOQUE_PANEL.finditer(txt))
    if not bloques or _BLOQUE_PANEL.sub("", txt).strip():
        return False
    try:
        ts = [datetime.strptime(b.group(1), "%Y-%m-%dT%H:%M:%S") for b in bloques]
    except ValueError:
        return False
    rafagas, actual = [], [ts[0]]
    for t in ts[1:]:
        if abs((t - actual[-1]).total_seconds()) <= _RAFAGA_HUECO_S:
            actual.append(t)
        else:
            rafagas.append(actual)
            actual = [t]
    rafagas.append(actual)
    for r in rafagas:
        if len(r) < _RAFAGA_MIN or (max(r) - min(r)).total_seconds() > _RAFAGA_MAX_S:
            return False
    conocidos = _jobs_de_casa_base()
    return not any(b.group(2) in conocidos for b in bloques)


# COPIAS DE CASA BASE (22-sep-2026). La app, al crear un worktree, copia dentro los ignorados de
# `.claude/` de casa base: 8 MB de `.claude/logs/clinico-access.log` y compañía en CADA worktree.
# El freno los tomaba por trabajo vivo y ningún worktree nuevo se podaba nunca. Un fichero cuyo
# contenido es PREFIJO del mismo fichero en casa base (idéntico, o casa base siguió añadiendo
# líneas, que es lo que hacen los logs) no pierde nada al podarse. Una sola línea que casa base
# no tenga (el caso del 20-sep: accesos escritos solo en el worktree) lo hace trabajo vivo.
_TROZO = 1 << 20


def _es_copia_de_casa_base(fichero, rel):
    import _casa
    origen = os.path.join(_casa.casa_base(), rel)
    if os.path.realpath(origen) == os.path.realpath(fichero):
        return False
    try:
        if os.path.getsize(fichero) > os.path.getsize(origen):
            return False
        with open(fichero, "rb") as a, open(origen, "rb") as b:
            while True:
                x = a.read(_TROZO)
                if not x:
                    return True
                if b.read(len(x)) != x:
                    return False
    except OSError:
        return False


def _motivo_residuo(path, rel):
    """Por qué este fichero ignorado del worktree se puede perder sin pena, o None si no se sabe."""
    f = os.path.join(path, rel)
    if rel == _PANEL_REL and _es_residuo_de_tests(f):
        return "residuo de tests"
    if _es_copia_de_casa_base(f, rel):
        return "copia de casa base"
    return None


def residuo(path):
    """[(rel, motivo)] de los ficheros ignorados del worktree que NO son trabajo: se pierden sin pena."""
    out = []
    for rel in _ARBOLES_QUE_NO_TOCAN_AQUI:
        d = os.path.join(path, rel)
        for raiz, _dirs, ficheros in (os.walk(d) if os.path.isdir(d) else ()):
            for f in ficheros:
                r = os.path.relpath(os.path.join(raiz, f), path)
                m = None if f.startswith(".") else _motivo_residuo(path, r)
                if m:
                    out.append((r, m))
    return out


def residuo_de_tests(path):
    """Compat: solo los ficheros que son residuo de la batería de tests."""
    return [r for r, m in residuo(path) if m == "residuo de tests"]


def _trabajo_vivo(path):
    """Ficheros que se perderían al podar este worktree (invisible para git).
    El residuo reconocido (`residuo`: tests o copia exacta de casa base) no cuenta."""
    fuera = []
    for rel in _RUTAS_TRABAJO_VIVO:
        d = os.path.join(path, rel)
        try:
            fuera += [os.path.join(rel, f) for f in os.listdir(d) if not f.startswith(".")]
        except OSError:
            pass
    for rel in _ARBOLES_QUE_NO_TOCAN_AQUI:
        d = os.path.join(path, rel)
        if not os.path.isdir(d):
            continue
        for raiz, _dirs, ficheros in os.walk(d):
            for f in ficheros:
                if f.startswith("."):
                    continue
                rel_f = os.path.relpath(os.path.join(raiz, f), path)
                if _motivo_residuo(path, rel_f):
                    continue
                fuera.append(rel_f)
                if len(fuera) >= _TOPE_HALLAZGOS:
                    return fuera
    return fuera


def rescatar(wt, base, destino, copiar=True):
    """Guarda lo que la poda se llevaría, para que cerrar una sesión no dependa de {{TITULAR}}.

    Regla suya (22-sep-2026, «todo esto de gestión debes hacerlo tú, grábalo a fuego»): el cierre
    se paraba a preguntarle por 20 ficheros ignorados que eran copias viejas de casa base y restos
    de `test_all.sh`. Ahora se clasifica cada fichero de `_ARBOLES_QUE_NO_TOCAN_AQUI`:
      · repetido: todas sus líneas ya están en el mismo fichero de casa base → no hay nada que salvar;
      · rescatado: tiene algo propio → se COPIA a `destino/<ruta>` (nunca se mezcla en el de casa
        base: pueden ser restos de tests y ensuciarían el panel o el log de auditoría);
      · bloquea: borradores y encargos vivos (`_RUTAS_TRABAJO_VIVO`) o una copia que falla. Eso es
        trabajo de alguien a medias y la poda sigue negándose.
    Sin tope de hallazgos: aquí no basta con saber que hay, hay que salvarlo todo.
    Devuelve {"repetidos": [...], "rescatados": [...], "bloquean": [...]}."""
    import shutil
    out = {"repetidos": [], "rescatados": [], "bloquean": []}
    for rel in _RUTAS_TRABAJO_VIVO:
        d = os.path.join(wt, rel)
        if os.path.isdir(d):
            out["bloquean"] += [os.path.join(rel, f) for f in os.listdir(d) if not f.startswith(".")]
    for arbol in _ARBOLES_QUE_NO_TOCAN_AQUI:
        d = os.path.join(wt, arbol)
        if not os.path.isdir(d):
            continue
        for raiz, _dirs, ficheros in os.walk(d):
            for f in ficheros:
                if f.startswith("."):
                    continue
                src = os.path.join(raiz, f)
                rel = os.path.relpath(src, wt)
                if _lineas_ya_en(src, os.path.join(base, rel)):
                    out["repetidos"].append(rel)
                    continue
                if copiar:
                    dst = os.path.join(destino, rel)
                    try:
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(src, dst)
                        if os.path.getsize(dst) != os.path.getsize(src):
                            raise OSError("la copia no mide lo mismo")
                    except OSError as e:
                        out["bloquean"].append("%s (no se pudo copiar: %s)" % (rel, e))
                        continue
                out["rescatados"].append(rel)
    return out


def _lineas_ya_en(src, dst):
    """¿Todas las líneas de `src` están ya en `dst`? Si no se puede leer alguno, NO (fail-closed)."""
    try:
        with open(dst, "rb") as fb:
            ya = set(fb.read().splitlines())
        with open(src, "rb") as fs:
            return all(l in ya for l in fs.read().splitlines())
    except OSError:
        return False


def _candidatas_vacias():
    """Worktrees NO-base, sin cambios sin commitear, SIN commits propios respecto a la base,
    SIN trabajo vivo invisible para git (ver `_trabajo_vivo`) y SIN sesión de Claude dentro.

    Lo de la sesión viva no es teórico: el 25-jul-26 `limpia` proponía podar dos worktrees que
    tenían sesiones de {{TITULAR}} trabajando dentro en ese momento. Podar por debajo de una sesión
    que está escribiendo es la forma más rápida de perderle trabajo a otra de sus ventanas.
    `sesiones()` ya existía y solo la usaba la vista de ramas paradas.

    Y el 20-sep-26, la otra mitad del mismo error: un worktree en *detached HEAD* no tiene
    nombre de rama, así que `git rev-list master..(detached)` fallaba, `_run` devolvía "" y
    `or "0"` lo leía como «cero commits propios». Salía listado como «limpio y vacío» un
    worktree cuyo HEAD era la punta exacta de una rama con 14 commits SIN fusionar. Un
    comando que falla no es un cero: ahora se cuenta contra el HEAD y, si no se puede
    contar, NO se propone podar (fail-closed).
    """
    base = _rama_base()
    ocupadas = {x["rama"] for x in sesiones()
                if not x["es_base"] and x["rama"] not in ("(fuera del repo)", "casa base")}
    cand = []
    for w in worktrees():
        if w["es_base"]:
            continue
        if w.get("branch") in ocupadas:
            continue
        # `_run` devuelve "" también si git FALLA (timeout, árbol roto): eso no es «limpio».
        sucio = _run_rc(["git", "-C", w["path"], "status", "--porcelain"])
        if sucio is None or sucio.strip():
            continue
        if _trabajo_vivo(w["path"]):
            continue
        if _ahead_ref(_ref_de(w), base) != 0:
            continue
        cand.append(w)
    return cand


def _ref_de(w):
    """La ref que de verdad identifica el trabajo de un worktree: su rama, o su HEAD si está
    *detached*. Sin esto, un worktree detached parece no tener nada aunque su HEAD sea la punta
    de una rama con commits sin fusionar."""
    br = (w or {}).get("branch")
    if br and br != "(detached)":
        return br
    return (w or {}).get("head") or ""


def _ahead_ref(ref, base):
    """Commits de `ref` que NO están en la base. Devuelve None cuando NO SE PUEDE saber
    (sin base, sin ref, o git falla): quien decide podar tiene que tratar ese None como
    «no toques», nunca como cero."""
    if not base or not ref:
        return None
    p = _run_rc(["git", "-C", ROOT, "rev-list", "--count", "%s..%s" % (base, ref)])
    if p is None:
        return None
    try:
        return int(p.strip() or "0")
    except ValueError:
        return None


def _ahead(branch, base):
    """Nº de commits de `branch` que NO están en la base (commits propios sin fusionar)."""
    n = _ahead_ref(branch if branch != "(detached)" else "", base)
    return 0 if n is None else n


def _edad_dias(branch):
    """Días desde el último commit de la rama. -1 si no se puede datar."""
    out = (_run(["git", "-C", ROOT, "log", "-1", "--format=%ct", branch]) or "").strip()
    if not out:
        return -1
    try:
        import time as _t
        return int((_t.time() - int(out)) // 86400)
    except (ValueError, OverflowError):
        return -1


def huerfanas():
    """Ramas de trabajo PARADAS (cabos sueltos): worktree NO-base, NO transitorio de subagente
    (.claude/worktrees/agent-*), con commits propios SIN fusionar (ahead>0) y SIN sesión de Claude viva.
    El que llama filtra por `edad_dias` si quiere. SOLO LECTURA de git: aquí no se fusiona ni se borra
    (eso es singleton + gate de {{TITULAR}}). [{rama, path, ahead, edad_dias, sin_commitear}]."""
    base = _rama_base()
    ocupadas = {x["rama"] for x in sesiones()
                if not x["es_base"] and x["rama"] not in ("(fuera del repo)", "casa base")}
    out = []
    for w in worktrees():
        if w["es_base"]:
            continue
        if "/.claude/worktrees/agent-" in (w.get("path") or ""):
            continue                                   # worktree efímero de un subagente, no es trabajo de {{TITULAR}}
        rama = w.get("branch", "")
        if rama in ("", "(detached)") or rama in ocupadas:
            continue
        ahead = _ahead(rama, base)
        if ahead <= 0:
            continue                                   # ya fusionada / sin commits propios → no es cabo suelto
        out.append({
            "rama": rama, "path": w["path"], "ahead": ahead,
            "edad_dias": _edad_dias(rama),
            "sin_commitear": bool(_run(["git", "-C", w["path"], "status", "--porcelain"]).strip()),
        })
    return out


def resumen():
    s = sesiones()
    wts = worktrees()
    huer = huerfanas()
    conf = conflictos()
    return {
        "worktrees": wts,
        "sesiones": s,
        "n_sesiones": len(s),
        "n_base": sum(1 for x in s if x["es_base"]),
        "n_ramas": sum(1 for w in wts if not w["es_base"]),
        "huerfanas": huer,
        "n_huerfanas": len(huer),
        # Colisiones entre sesiones: El Observatorio puede pintarlas en la tarjeta «Sesiones».
        "conflictos": conf.get("choques", []),
        "n_conflictos": conf.get("n_choques", 0),
    }


def _print_list():
    wts = worktrees()
    s = sesiones()
    print("Ramas de trabajo (worktrees):")
    for w in wts:
        etq = "  [casa base]" if w["es_base"] else ""
        ocup = [x["pid"] for x in s if x["cwd"] and (x["cwd"] == w["path"] or x["cwd"].startswith(w["path"].rstrip("/") + "/"))]
        quien = ("  · sesiones: " + ", ".join(ocup)) if ocup else "  · (sin sesión)"
        print("  • %-28s %s%s%s" % (w.get("branch", "?"), w["path"], etq, quien))
    fuera = [x for x in s if not x["cwd"] or x["rama"] == "(fuera del repo)"]
    print("\nSesiones de Claude vivas: %d (en casa base: %d)" % (len(s), sum(1 for x in s if x["es_base"])))
    if fuera:
        print("  (%d sesión/es fuera del repo o sin cwd legible)" % len(fuera))
    huer = huerfanas()
    if huer:
        print("\nRamas paradas (sin sesión + cambios sin fusionar):")
        for h in huer:
            edad = ("%dd" % h["edad_dias"]) if h["edad_dias"] >= 0 else "?"
            print("  ⚠️  %-28s · %d commit(s) sin fusionar · %s sin sesión" % (h["rama"], h["ahead"], edad))
    conf = conflictos()
    if conf["n_choques"]:
        print("\n💥 Mismo fichero en varias ramas a la vez (colisión esperando a pasar):")
        for c in conf["choques"][:12]:
            print("  %-52s %s" % (c["fichero"], " ↔ ".join(c["ramas"])))
        if conf["n_choques"] > 12:
            print("  … y %d más (`ramas.py conflictos`)" % (conf["n_choques"] - 12))


def _ficheros_tocados(w, base):
    """Ficheros que ESE worktree está tocando: los commiteados en su rama (vs base) MÁS los
    que tiene sin commitear ahora mismo. Lo segundo es la mitad que importa — una colisión real
    ocurre mientras dos sesiones escriben, mucho antes de que ninguna commitee."""
    br = w.get("branch", "")
    path = w.get("path") or ""
    files = set()
    if br and br != "(detached)" and not w.get("es_base"):
        files |= set(_run(["git", "-C", ROOT, "diff", "--name-only",
                           "%s...%s" % (base, br)]).splitlines())
    for ln in _run(["git", "-C", path, "status", "--porcelain"]).splitlines():
        f = ln[3:].strip()
        if " -> " in f:                      # renombrado: cuenta el destino
            f = f.split(" -> ", 1)[1]
        if f:
            files.add(f.strip('"'))
    return {f for f in files if f}


def conflictos():
    """Ficheros que MÁS DE UN worktree vivo está tocando a la vez = colisión esperando a pasar.

    De dónde sale (25-jul-2026): idea copiada de Fleet Deck (`x:2076775237577023513`), que avisa
    de conflictos de ficheros entre sesiones ANTES de que choquen. Nosotros ya teníamos el «quién
    toca este subsistema» (`en-vuelo`) pero no el cruce automático, y la colisión entre sesiones
    paralelas es un dolor documentado ([[feedback-serializar-ficheros-hot-compartidos]]).

    Incluye casa base a propósito: si casa base tiene algo sin commitear que otra rama también
    toca, esa es la trampa conocida — un worktree nace de HEAD y NO se lleva lo no commiteado
    ([[feedback-casa-base-commiteada-para-aislar]]). Solo lectura.
    """
    base = _rama_base() or "master"
    por_fichero, por_wt = {}, {}
    for w in worktrees():
        if "/.claude/worktrees/agent-" in (w.get("path") or ""):
            continue
        quien = "casa base" if w.get("es_base") else (w.get("branch") or "?")
        tocados = _ficheros_tocados(w, base)
        por_wt[quien] = sorted(tocados)
        for f in tocados:
            por_fichero.setdefault(f, set()).add(quien)
    choques = [{"fichero": f, "ramas": sorted(v)} for f, v in sorted(por_fichero.items()) if len(v) > 1]
    return {"choques": choques, "n_choques": len(choques), "por_rama": por_wt, "base": base}


def trabajo_en_vuelo(patron):
    """Ramas/worktrees VIVOS cuyos ficheros tocados (vs base) casan `patron` (substring).
    Consulta ANTES de construir en un subsistema: "alguien ya esta en esto?". Solo lectura."""
    base = _rama_base() or "master"
    res = []
    for w in worktrees():
        br = w.get("branch", "")
        if w.get("es_base") or "/.claude/worktrees/agent-" in (w.get("path") or ""):
            continue
        if not br or br == "(detached)":
            continue
        files = _run(["git", "-C", ROOT, "diff", "--name-only", "%s...%s" % (base, br)]).splitlines()
        hit = [f for f in files if patron in f]
        if hit:
            res.append({"rama": br, "path": w.get("path"), "ficheros": hit})
    return res


def fusionar_a_base(rama=None, base="master", ok_humano=False):
    """Fusiona `rama` en `base` SIN tocar ningún working tree. Devuelve (ok, mensaje).

    POR QUÉ ASÍ, y no con `git merge` (13-sep-2026, hallazgo `freno-base-deja-el-arbol-a-medio-
    fusionar`, que pasó DOS veces el mismo día). El freno de la base es un hook
    `reference-transaction`: exige `BTP_GIT_BASE_OK=1` para mover `refs/heads/<base>`, y salta
    también en un fast-forward. El problema no es el gate, es CUÁNDO bloquea: para entonces git ya
    ha actualizado el working tree y el índice, así que al rechazar el movimiento de la ref deja
    casa base con **los ficheros del commit nuevo y HEAD en el viejo**. A medio fusionar, sin nada
    en `git log` que lo delate, y hubo que repararlo a mano las dos veces.

    La salida no es ablandar el freno: es no tocar el árbol. `git push . <rama>:<base>` mueve la
    referencia y nada más, así que el hook puede rechazar sin dejar residuo. Y de regalo resuelve
    el otro problema del mismo día: casa base estaba en OTRA rama con dos sesiones vivas encima, y
    `git merge` habría exigido cambiar de rama bajo sus pies — que es justo lo que en junio hizo
    desaparecer `salida.py` del disco.

    Dos cosas que NO hace, a propósito:
      · no fusiona si no es fast-forward (si hay divergencia real, que lo mire una persona);
      · no pone `BTP_GIT_BASE_OK` por su cuenta: `ok_humano=True` solo debe venir de un OK
        explícito de {{TITULAR}}. El gate es suyo, no del código.
    """
    rama = rama or _rama_actual()
    if not rama:
        return False, "no sé qué rama fusionar"
    if rama == base:
        return False, "la rama y la base son la misma (%s)" % base
    if not ok_humano:
        return False, ("fusionar a la base es gate de {{TITULAR}}: hace falta su OK explícito "
                       "(--ok-humano). Sin eso no se toca %s" % base)
    # ¿Está `base` checked out en algún árbol? Si lo está, mover su ref por debajo lo dejaría
    # inconsistente con su HEAD. Eso NO se hace: que la sesión que lo tiene lo fusione.
    try:
        wt = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=ROOT,
                            capture_output=True, text=True, timeout=30).stdout
    except Exception as e:
        return False, "no pude listar los worktrees (%r)" % e
    if ("branch refs/heads/%s\n" % base) in wt:
        return False, ("%s está checked out en algún árbol: no muevo su ref por debajo. "
                       "Que lo fusione la sesión que lo tiene, o espera a que lo suelte." % base)
    ff = subprocess.run(["git", "merge-base", "--is-ancestor", base, rama], cwd=ROOT,
                        capture_output=True, timeout=30)
    if ff.returncode != 0:
        return False, ("no es fast-forward: %s ha avanzado por su cuenta. Lo miras tú antes de "
                       "mezclar nada." % base)
    env = dict(os.environ, BTP_GIT_BASE_OK="1")
    p = subprocess.run(["python3", os.path.join(ROOT, "tools", "git_mutex.py"),
                        "push", ".", "%s:%s" % (rama, base)],
                       cwd=ROOT, capture_output=True, text=True, timeout=120, env=env)
    if p.returncode != 0:
        return False, "la fusión falló: %s" % (p.stderr or p.stdout or "")[-300:]
    sha = subprocess.run(["git", "rev-parse", "--short", base], cwd=ROOT,
                         capture_output=True, text=True, timeout=30).stdout.strip()
    return True, "%s → %s (%s), sin tocar ningún working tree" % (rama, base, sha)


def _rama_actual():
    try:
        return subprocess.run(["git", "branch", "--show-current"], cwd=ROOT, capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def dudosos():
    """Worktrees YA fusionados (0 commits propios) que no se podan porque algo no cuadra: cambios
    sin commitear o trabajo que git no ve. Son los que hay que mirar; no se tocan solos.
    [{rama, path, motivo}]. Los que tienen sesión viva no salen: están en uso, no son dudosos."""
    base = _rama_base()
    ocupadas = {x["rama"] for x in sesiones()
                if not x["es_base"] and x["rama"] not in ("(fuera del repo)", "casa base")}
    out = []
    for w in worktrees():
        if w["es_base"] or w.get("branch") in ocupadas:
            continue
        if _ahead_ref(_ref_de(w), base) != 0:
            continue                  # sin fusionar: eso lo decide {{TITULAR}} por otra vía (huerfanas)
        sucio = _run_rc(["git", "-C", w["path"], "status", "--porcelain"])
        vivo = _trabajo_vivo(w["path"])
        if sucio is None:
            motivo = "git status falla en el worktree"
        elif sucio.strip():
            motivo = "%d cambio(s) sin commitear" % len(sucio.strip().splitlines())
        elif vivo:
            motivo = "%d fichero(s) que git no ve: %s" % (len(vivo), ", ".join(vivo[:3]))
        else:
            continue
        out.append({"rama": w.get("branch", "?"), "path": w["path"], "motivo": motivo})
    return out


def _avisar_dudosos(lista):
    """Una línea operativa (no Telegram) por worktree dudoso NUEVO. Anti-spam: se recuerda qué
    se avisó y no se repite mientras siga igual; si se resuelve, se olvida."""
    import _casa
    ruta = os.path.join(_casa.state_dir(), "autopoda_avisados.json")
    try:
        with open(ruta, encoding="utf-8") as fh:
            ya = json.load(fh)
    except (OSError, ValueError):
        ya = {}
    ahora = {d["path"]: d["motivo"] for d in lista}
    nuevos = [d for d in lista if ya.get(d["path"]) != d["motivo"]]
    if nuevos:
        try:
            import salida
            salida.report_to_titular(
                "🌿 Worktrees ya fusionados que NO podé por algo dudoso (míralos antes de borrar): "
                + "; ".join("%s → %s" % (d["rama"], d["motivo"]) for d in nuevos),
                categoria="operativo", voz="sobria", fuente="ramas.autopoda")
        except Exception as e:           # noqa: BLE001 — el aviso nunca tumba la poda
            print("  (no pude dejar el aviso operativo: %s)" % e)
    try:
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(ahora, fh, ensure_ascii=False, indent=1)
        os.replace(ruta + ".tmp", ruta)
    except OSError:
        pass
    return nuevos


def limpia(si=False, avisar=False):
    """Poda los worktrees limpios, fusionados y sin sesión; dice (y con `avisar`, avisa) lo dudoso.
    MUTA `.git/worktrees/` → candado compartido "git-mutex" (fail-closed: si no se puede tomar,
    se avisa y NO se poda a ciegas; ver cerrar_sesion.py, mismo candado)."""
    try:
        _, out, err = git_mutex.run(["-C", ROOT, "worktree", "prune"])
        print(out or err or "prune: ok (sin entradas obsoletas).")
    except TimeoutError as e:
        print("prune: NO se pudo tomar el candado compartido (otro actor muta la casa base ahora): %s" % e)
        return 75
    # Lo que NO se poda por tener trabajo vivo se DICE. Un "no hay nada que podar" que en
    # realidad significa "hay un borrador dentro" se lee como cola vacía, y el borrador se
    # queda ahí sin que nadie sepa que existe.
    _ocupadas = {x["rama"] for x in sesiones()
                 if not x["es_base"] and x["rama"] not in ("(fuera del repo)", "casa base")}
    for w in worktrees():
        if w["es_base"]:
            continue
        if w.get("branch") in _ocupadas:
            print("⏸️  %s NO se poda: tienes una sesión trabajando dentro" % w.get("branch", "?"))
            continue
        vivo = _trabajo_vivo(w["path"])
        if vivo:
            print("⏸️  %s NO se poda: tiene %d fichero(s) sin ejecutar/firmar (%s)"
                  % (w.get("branch", "?"), len(vivo), ", ".join(vivo[:3])))
    cand = _candidatas_vacias()
    if avisar:
        _avisar_dudosos(dudosos())
    if not cand:
        print("No hay ramas limpias y vacías que podar.")
        return 0
    print("Ramas limpias y vacías (sin cambios ni commits propios):")
    for w in cand:
        res = residuo(w["path"])
        print("  • %s  (%s)%s" % (w.get("branch", "?"), w["path"],
                                  "  · %d fichero(s) ignorados sin pena (%s)"
                                  % (len(res), ", ".join(sorted({m for _, m in res}))) if res else ""))
    if si:
        for w in cand:
            try:
                _, out, err = git_mutex.run(["-C", ROOT, "worktree", "remove", w["path"]])
                print(out or err or ("eliminada: " + w["branch"]))
            except TimeoutError as e:
                print("  NO se pudo eliminar %s: candado compartido ocupado (%s)" % (w["branch"], e))
    else:
        print("(usa `limpia --si` para eliminarlas)")
    return 0


def main(argv):
    cmd = argv[0] if argv else "list"
    if cmd == "fusionar":
        ok_h = "--ok-humano" in argv
        rama = next((a for a in argv[1:] if not a.startswith("-")), None)
        ok_f, msg = fusionar_a_base(rama, ok_humano=ok_h)
        print(("✅ " if ok_f else "⛔ ") + msg)
        return 0 if ok_f else 1
    if cmd == "list":
        _print_list()
    elif cmd == "json":
        print(json.dumps(resumen(), ensure_ascii=False, indent=2))
    elif cmd == "conflictos":
        conf = conflictos()
        if not conf["n_choques"]:
            print("Sin colisiones: ninguna rama viva toca un fichero que otra esté tocando.")
            return 0
        print("💥 %d fichero(s) tocados por más de una rama a la vez — habla antes de seguir:"
              % conf["n_choques"])
        for c in conf["choques"]:
            print("  %-52s %s" % (c["fichero"], " ↔ ".join(c["ramas"])))
        print("\nSi «casa base» sale en la lista, es la trampa conocida: un worktree nace de HEAD y "
              "no se lleva lo que no está commiteado.")
        return 1                      # exit 1 = hay colisión (para usarlo como check)
    elif cmd == "en-vuelo":
        patron = argv[1] if len(argv) > 1 else ""
        if not patron:
            print("uso: ramas.py en-vuelo <patron-de-ruta>  (p.ej. correo)")
            return 2
        hits = trabajo_en_vuelo(patron)
        if not hits:
            print("Ninguna rama viva toca %r ahora mismo." % patron)
        else:
            print("Ramas VIVAS que ya tocan %r (mira antes de construir):" % patron)
            for h in hits:
                extra = "..." if len(h["ficheros"]) > 4 else ""
                print("  [!] %s  (%d fichero/s: %s%s)" % (h["rama"], len(h["ficheros"]),
                      ", ".join(h["ficheros"][:4]), extra))
    elif cmd in ("limpia", "autopoda"):
        # `autopoda` = `limpia --si --avisar`: lo que corre la rutina diaria com.btp.git-barrido.
        auto = cmd == "autopoda"
        return limpia(si=auto or "--si" in argv, avisar=auto or "--avisar" in argv)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
