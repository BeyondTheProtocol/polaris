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
  python3 tools/ramas.py json            # salida JSON (la usa El Observatorio)

`limpia`/`limpia --si` MUTAN el `.git` de la casa base (worktree prune/remove) — por eso pasan
por el candado COMPARTIDO "git-mutex" (tools/git_mutex.py, el mismo que usa cerrar_sesion.py al
fusionar/podar). Antes mutaban SIN candado: era uno de los tres actores sin serializar que el
comité de arquitectura encontró sobre el `.git` compartido (A1, 10-jul-26 — hallazgo B).
"""
import json
import os
import subprocess
import sys

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


def _trabajo_vivo(path):
    """Ficheros que se perderían al podar este worktree (invisible para git)."""
    fuera = []
    for rel in _RUTAS_TRABAJO_VIVO:
        d = os.path.join(path, rel)
        try:
            fuera += [os.path.join(rel, f) for f in os.listdir(d) if not f.startswith(".")]
        except OSError:
            pass
    return fuera


def _candidatas_vacias():
    """Worktrees NO-base, sin cambios sin commitear, SIN commits propios respecto a la base,
    SIN trabajo vivo invisible para git (ver `_trabajo_vivo`) y SIN sesión de Claude dentro.

    Lo de la sesión viva no es teórico: el 25-jul-26 `limpia` proponía podar dos worktrees que
    tenían sesiones de {{TITULAR}} trabajando dentro en ese momento. Podar por debajo de una sesión
    que está escribiendo es la forma más rápida de perderle trabajo a otra de sus ventanas.
    `sesiones()` ya existía y solo la usaba la vista de ramas paradas.
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
        sucio = _run(["git", "-C", w["path"], "status", "--porcelain"]).strip()
        if sucio:
            continue
        if _trabajo_vivo(w["path"]):
            continue
        ahead = "0"
        if base:
            ahead = (_run(["git", "-C", ROOT, "rev-list", "--count", "%s..%s" % (base, w["branch"])]) or "0").strip()
        if ahead in ("", "0"):
            cand.append(w)
    return cand


def _ahead(branch, base):
    """Nº de commits de `branch` que NO están en la base (commits propios sin fusionar)."""
    if not base or not branch or branch == "(detached)":
        return 0
    out = (_run(["git", "-C", ROOT, "rev-list", "--count", "%s..%s" % (base, branch)]) or "0").strip()
    try:
        return int(out or "0")
    except ValueError:
        return 0


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
    elif cmd == "limpia":
        # MUTA `.git/worktrees/` → candado compartido "git-mutex" (fail-closed: si no se puede
        # tomar, se avisa y NO se poda a ciegas; ver cerrar_sesion.py, mismo candado).
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
        if not cand:
            print("No hay ramas limpias y vacías que podar.")
            return 0
        print("Ramas limpias y vacías (sin cambios ni commits propios):")
        for w in cand:
            print("  • %s  (%s)" % (w.get("branch", "?"), w["path"]))
        if "--si" in argv:
            for w in cand:
                try:
                    _, out, err = git_mutex.run(["-C", ROOT, "worktree", "remove", w["path"]])
                    print(out or err or ("eliminada: " + w["branch"]))
                except TimeoutError as e:
                    print("  NO se pudo eliminar %s: candado compartido ocupado (%s)" % (w["branch"], e))
        else:
            print("(usa `limpia --si` para eliminarlas)")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
