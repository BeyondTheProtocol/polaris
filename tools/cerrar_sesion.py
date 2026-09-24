#!/usr/bin/env python3
"""tools/cerrar_sesion.py — cierre AUTOMÁTICO y DETERMINISTA de una sesión de trabajo.

Lo dispara {{TITULAR}} diciendo "hemos terminado": recoge el trabajo del worktree ACTUAL, lo deja
commiteado con un scope claro, fusiona lo ÚTIL a casa base (~/claudecode), copia los docs nuevos
de la fuente de verdad, reindexa el RAG y poda el worktree. Así puede cerrar sesiones en paralelo
sin vigilar nada: nada se queda en el aire (lo que sí queda, lo saca Vega vía ramas.huerfanas()).

Pasos (idempotentes):
  (a) COMMIT     — lo sin commitear del worktree → un commit con scope (git excluye por .gitignore
                   clínico/secretos/estado/venv; además un deny-list de seguridad por si acaso).
  (b) DOCS       — detecta docs NUEVOS de 00_FUENTE-DE-VERDAD/ creados en esta rama (vs casa base).
  (c) FUSIÓN     — merge --no-ff de la rama a casa base (fusión LOCAL pre-aprobada; SINGLETON: toma
                   el candado compartido "git-mutex" para serializar con otras sesiones que cierren
                   a la vez y con CUALQUIER otro actor que mute el `.git` de la casa base).
  (d) RAG        — reindexa kb.py desde casa base (los docs nuevos ya están fusionados).
  (e) PODA       — git worktree remove del worktree (solo si está limpio y ya fusionado). Toma el
                   MISMO candado "git-mutex" que la fusión (A1, 10-jul-26 — hallazgo B: antes esta
                   poda mutaba sin candado, la única grieta de aislamiento real encontrada).
  (f) RESUMEN    — 1 línea de qué se guardó / fusionó / podó.

Seguridad (el muro):
  · NADA a `main`/`master` remoto, NADA de push. Solo fusión LOCAL a casa base (gate ya aprobado).
  · `--dry-run` es el DEFAULT seguro: enseña el plan y NO toca nada. `--apply` ejecuta.
  · La poda del worktree NO borra la casa base ni la rama base; solo el worktree de la sesión.
  · El estado vivo, secretos, clínico y venvs NO viajan (gitignored) — además se filtran aquí.

Uso:
  python3 tools/cerrar_sesion.py                 # DRY-RUN: plan de cierre del worktree actual
  python3 tools/cerrar_sesion.py --apply         # ejecuta el cierre (commit→fusión→reindex→poda)
  python3 tools/cerrar_sesion.py --apply --no-poda   # cierra y fusiona pero NO poda el worktree
  python3 tools/cerrar_sesion.py --scope "feat(modos): …"   # mensaje de commit a medida
  python3 tools/cerrar_sesion.py --json          # salida estructurada (para Vega/Observatorio)

Sin dependencias (stdlib). Patrón de ramas.py / _lock.py.
"""
import json
import re
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _lock
import ramas as _ramas   # `_trabajo_vivo`: lo que se perdería al podar y git no ve (gitignored)

# Candado COMPARTIDO "git-mutex" (git_mutex.py) para serializar TODA mutación del `.git` de la
# casa base (fusión Y poda), no solo la fusión.
GIT_MUTEX = "git-mutex"

# Casa base = el sistema vivo 24/7. La resolvemos por BTP_REPO o ~/claudecode (NUNCA el árbol relativo:
# este fichero corre DESDE un worktree). El worktree actual es el cwd / la raíz del git de aquí.
BASE = os.path.realpath(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"))
VENV_PY = os.path.join(BASE, ".venv", "bin", "python3")
FV = "00_FUENTE-DE-VERDAD"

# Belt-and-suspenders: aunque .gitignore ya excluye esto, si algún path sensible aparece "staged"
# abortamos el commit (fail-closed). Coincide con lo gitignored: clínico, secretos, estado, venv, núcleo.
DENY = ("_secrets", "/state/", "tools/state", ".venv", "_PRIVADO_", "Historial clinico",
        "_PRIVADO_CLINICO", "_PRIVADO_NUCLEO", ".telegram", ".grok_secrets", ".env")


def _run(args, cwd=None, check=False):
    """Ejecuta y devuelve (rc, stdout, stderr). Nunca lanza salvo check=True con rc!=0."""
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=120)
    if check and p.returncode != 0:
        raise RuntimeError("falló %s:\n%s" % (" ".join(args), p.stderr.strip()))
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def _git(args, cwd, check=False):
    return _run(["git", "-C", cwd] + args, check=check)


# Sin `=======`: en Markdown es el subrayado de un título y daría falsos positivos.
_MARCADOR = re.compile(r"^\+(<<<<<<< |>>>>>>> )", re.M)


def _conflicto_en_worktree(wt, base):
    """Motivo (str) si la rama NO se puede cerrar por un conflicto; "" si está limpia.
    Mira: merge sin terminar (MERGE_HEAD), ficheros sin resolver, y marcadores `<<<<<<<` en lo
    que se va a fusionar (lo commiteado de la rama frente a la base, y lo pendiente)."""
    rc, gd, _ = _git(["rev-parse", "--git-dir"], wt)
    gd = gd.strip()
    if rc == 0 and gd:
        gd = gd if os.path.isabs(gd) else os.path.join(wt, gd)
        if os.path.exists(os.path.join(gd, "MERGE_HEAD")):
            return "merge sin terminar"
    _, sin_resolver, _ = _git(["diff", "--name-only", "--diff-filter=U"], wt)
    if sin_resolver.strip():
        return "sin resolver: " + ", ".join(sin_resolver.split()[:5])
    for args in (["diff", "%s...HEAD" % base], ["diff", "HEAD"]):
        _, d, _ = _git(args, wt)
        if _MARCADOR.search(d or ""):
            return "marcadores de conflicto en el diff"
    return ""


def _gate_base_encendido():
    """El mismo interruptor que los hooks de git y el muro: `.claude/hooks/.base_gate_on`."""
    return os.path.exists(os.path.join(BASE, ".claude", "hooks", ".base_gate_on"))


def _worktree_root():
    """Raíz del worktree desde el que se invoca (cwd). Vacío si no es un repo git."""
    rc, out, _ = _run(["git", "rev-parse", "--show-toplevel"], cwd=os.getcwd())
    return os.path.realpath(out) if rc == 0 and out else ""


def _rama(cwd):
    _, out, _ = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    return out


def _rama_base():
    """Rama que tiene cargada casa base (la de fusión: master/main)."""
    _, out, _ = _git(["rev-parse", "--abbrev-ref", "HEAD"], BASE)
    return out


def _sin_commitear(cwd):
    """Lista [(estado, path)] de lo modificado/nuevo, ya filtrado por .gitignore (git no lista lo ignorado)."""
    _, out, _ = _git(["status", "--porcelain"], cwd)
    res = []
    for ln in out.splitlines():
        if len(ln) < 4:
            continue
        res.append((ln[:2].strip(), ln[3:].strip()))
    return res


def _viola_deny(paths):
    """Paths sensibles que NO deberían viajar (backstop sobre .gitignore). [] si todo limpio."""
    mal = []
    for p in paths:
        if any(tok in p for tok in DENY):
            mal.append(p)
    return mal


def _walk_md(raiz):
    """Rutas RELATIVAS de los .md bajo `raiz`, saltando lo privado/índices (no debe viajar)."""
    out = set()
    if not os.path.isdir(raiz):
        return out
    for dp, dns, fns in os.walk(raiz):
        dns[:] = [d for d in dns if not d.startswith("_PRIVADO")]   # nada privado
        for fn in fns:
            if fn.endswith(".md") and not fn.startswith("."):
                out.add(os.path.relpath(os.path.join(dp, fn), raiz))
    return out


def _docs_nuevos(cwd, base):
    """Docs NUEVOS de la fuente de verdad creados en este worktree y que NO existen en casa base.
    OJO: 00_FUENTE-DE-VERDAD/ está GITIGNORED (no viaja por git ni worktrees) — por eso se compara
    por SISTEMA DE FICHEROS, no por git, y se COPIAN a casa base (no se fusionan). [] si no hay FV."""
    aqui = _walk_md(os.path.join(cwd, FV))
    alla = _walk_md(os.path.join(BASE, FV))
    return sorted(aqui - alla)


def _commits_propios(cwd, base):
    """Nº de commits de esta rama que NO están en la base (lo que se fusionaría)."""
    rama = _rama(cwd)
    _, out, _ = _run(["git", "-C", BASE, "rev-list", "--count", "%s..%s" % (base, rama)])
    try:
        return int(out or "0")
    except ValueError:
        return 0


def plan():
    """Calcula el plan de cierre SIN tocar nada. Lo usa el dry-run y --apply (que luego ejecuta)."""
    wt = _worktree_root()
    if not wt:
        return {"error": "no estoy dentro de un repo git (worktree)."}
    if wt == BASE:
        return {"error": "esto es CASA BASE, no un worktree de sesión — el cierre no se corre aquí."}
    base = _rama_base()
    rama = _rama(wt)
    pend = _sin_commitear(wt)
    viola = _viola_deny([p for _, p in pend])
    return {
        "worktree": wt, "base_repo": BASE, "rama": rama, "rama_base": base,
        "sin_commitear": pend, "viola_deny": viola,
        "docs_nuevos": _docs_nuevos(wt, base),
        "commits_propios_antes": _commits_propios(wt, base),
    }


def cerrar(apply=False, scope=None, podar=True):
    """Ejecuta (o simula) el cierre. Devuelve el dict de resultado."""
    p = plan()
    if p.get("error"):
        return p
    wt, base, rama = p["worktree"], p["rama_base"], p["rama"]
    acciones = []

    # GATE DE LA BASE (11-sep-26). La fusión mueve master, y el freno nativo
    # (`tools/githooks/reference-transaction`) la para sin OK humano. Antes pasaba por un hueco:
    # pre-commit no corre en `git merge`. Se comprueba AQUÍ, al principio, para no dejar el cierre
    # a medias (commit hecho, fusión rechazada) y para decir claro qué falta.
    if apply and _gate_base_encendido() and os.environ.get("BTP_GIT_BASE_OK") != "1":
        return dict(p, error="falta el OK humano para fusionar a la casa base: con OK explícito "
                             "de {{TITULAR}}, repite con BTP_GIT_BASE_OK=1 en el entorno",
                    acciones=acciones)

    # CASA BASE EN MASTER (22-sep-26). `git merge` fusiona en lo que casa base tenga cargado, y
    # eso no siempre es master: en 60 días estuvo 27 veces aparcada en otra rama (rutinas y jobs
    # que no pueden aislarse en worktree), una de ellas 10,5 h. Una sesión que cerrara entonces
    # metía su trabajo en la rama de la rutina, y el freno de master ni se enteraba. Va antes del
    # commit para no dejar el cierre a medias.
    if apply and base not in ("master", "main"):
        return dict(p, error="casa base está en la rama %r, no en master: alguien la dejó aparcada "
                             "(una rutina o un job). No fusiono ahí. Si está limpia, devuélvela con "
                             "`git -C %s checkout master` y repite el cierre; si tiene cambios, "
                             "son de otro: no los toques y avisa." % (base, BASE),
                    acciones=acciones)

    # MERGE A MEDIAS EN EL WORKTREE (22-sep-26). Un `git merge master` que choca deja la rama con
    # ficheros sin resolver y marcadores `<<<<<<<`; el paso (a) hacía `add -A` + commit y los
    # FUSIONABA a casa base como un cambio normal. Pasó con `tools/run_agent.sh` (script de TODOS
    # los agentes del lazo: no pasaba `bash -n`). Se para aquí, antes de commitear nada.
    conflicto = _conflicto_en_worktree(wt, base)
    if conflicto:
        return dict(p, error="ABORTO: la rama tiene un merge a medias o marcadores de conflicto (%s). "
                             "Resuélvelo en el worktree y repite el cierre; no se commitea ni se "
                             "fusiona nada así." % conflicto, acciones=acciones)

    # (a) COMMIT — fail-closed si algo sensible se coló pese al .gitignore.
    if p["sin_commitear"]:
        if p["viola_deny"]:
            return dict(p, error="ABORTO: paths sensibles en el árbol (no deben viajar): %s"
                        % ", ".join(p["viola_deny"]), acciones=acciones)
        msg = scope or ("chore(sesión): cierre automático de %s" % rama)
        if apply:
            _git(["add", "-A"], wt, check=True)
            # Re-chequea lo STAGED contra el deny-list (por si add -A capturó algo no listado en status).
            _, staged, _ = _git(["diff", "--cached", "--name-only"], wt)
            mal = _viola_deny(staged.splitlines())
            if mal:
                _git(["reset"], wt)
                return dict(p, error="ABORTO: staging tocaba paths sensibles: %s" % ", ".join(mal),
                            acciones=acciones)
            _git(["commit", "-m", msg], wt, check=True)
        acciones.append("commit: %s (%d cambios)" % (msg, len(p["sin_commitear"])))
    else:
        acciones.append("commit: nada sin commitear")

    # ¿Hay algo que fusionar? (commits propios tras el commit anterior)
    n = _commits_propios(wt, base) if apply else (p["commits_propios_antes"] + (1 if p["sin_commitear"] else 0))
    fusionado = False
    if n > 0:
        if apply:
            # (c) FUSIÓN a casa base — SINGLETON: candado COMPARTIDO "git-mutex" (mismo que la poda
            # de abajo y que tools/ramas.py) para serializar con CUALQUIER otro actor que mute el
            # `.git` de la casa base, no solo con otros cierres de sesión (A1, 10-jul-26).
            try:
                with _lock.lock(GIT_MUTEX, timeout=60.0):
                    rc, _, err = _git(["merge", "--no-ff", rama, "-m",
                                       "merge(%s): cierre de sesión → casa base" % rama], BASE)
                    if rc != 0:
                        # Deshacer AQUÍ, dentro del candado (22-sep-26). Sin esto, un conflicto
                        # dejaba casa base —el sistema vivo 24/7— con `UU` y marcadores
                        # `<<<<<<<` en disco hasta que alguien lo abortara a mano: pasó con dos
                        # sesiones que arreglaron el mismo rojo a la vez. La rama no se toca.
                        conflictos = _git(["diff", "--name-only", "--diff-filter=U"], BASE)[1]
                        rc_ab, _, err_ab = _git(["merge", "--abort"], BASE)
                        estado = ("fusión deshecha (merge --abort): casa base sigue como estaba"
                                  if rc_ab == 0 else
                                  "‼️ NO se pudo deshacer (merge --abort: %s) — casa base a medio "
                                  "fusionar, arréglalo a mano YA" % (err_ab or rc_ab))
                        return dict(p, error="fusión falló (conflicto/estado): %s%s · %s. Tu rama "
                                             "está intacta: actualízala con casa base y vuelve a cerrar."
                                    % (err or "", (" · en conflicto: %s" % ", ".join(conflictos.split()))
                                       if conflictos else "", estado),
                                    acciones=acciones)
                    fusionado = True
            except TimeoutError as e:
                return dict(p, error="no pude tomar el candado de fusión: %s" % e, acciones=acciones)
        acciones.append("fusión: %d commit(s) de %s → casa base (%s)" % (n, rama, base))
    else:
        acciones.append("fusión: nada propio que fusionar")

    # (b+d) DOCS + RAG — la fuente de verdad está GITIGNORED, así que los docs NUEVOS no viajan por la
    # fusión: se COPIAN a mano a casa base y se reindexa el RAG desde allí. Solo .md, nunca privados.
    copiados = []
    if p["docs_nuevos"]:
        if apply:
            import shutil
            for rel in p["docs_nuevos"]:
                src = os.path.join(wt, FV, rel)
                dst = os.path.join(BASE, FV, rel)
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    copiados.append(rel)
                except Exception as e:
                    acciones.append("docs: NO se pudo copiar %s (%s)" % (rel, e))
            acciones.append("docs: %d doc(s) nuevos copiados a casa base" % len(copiados))
            # El venv, NO el `python3` del PATH: pypdf solo vive ahí y `kb.build()` se niega
            # (bien) a indexar sin él antes que dejar el índice sin los PDFs del historial.
            # Mismo criterio que historial_sync.reindexar() y archivar_nota._build().
            rc, out, err = _run([VENV_PY if os.path.exists(VENV_PY) else sys.executable,
                                 os.path.join(BASE, "tools", "kb.py"), "index"], cwd=BASE)
            acciones.append("rag: %s" % (out.splitlines()[-1] if (rc == 0 and out) else ("reindex falló: " + err)))
        else:
            acciones.append("docs+rag: copiar %d doc(s) nuevos a casa base y reindexar el RAG"
                            % len(p["docs_nuevos"]))
    elif fusionado:
        acciones.append("docs: sin docs nuevos de la fuente de verdad")

    # (e) PODA — solo si está limpio Y ya fusionado (o no había nada que fusionar): no perder trabajo.
    podado = False
    if podar:
        limpio = not _sin_commitear(wt) if apply else not p["sin_commitear"]
        seguro = (fusionado or n == 0)
        # Lo que git NO ve. `sin_commitear` y `fusionado` son ciegos a los ficheros IGNORADOS,
        # y ahí es donde se pierde lo que duele: el 20-sep-2026 este worktree se podó con 52
        # entradas del panel del lazo dentro, y otro con 52.682 líneas del log de auditoría
        # clínica. Las dos veces se salvaron porque alguien miró a mano antes de borrar.
        # Ahora no se poda: se dice qué hay y dónde, y que se rescate primero.
        # También en dry-run: el plan es justo donde quieres ver esto ANTES de aplicar.
        #
        # Y desde el 22-sep-2026 el rescate lo hace el cierre, no {{TITULAR}} («todo esto de gestión
        # debes hacerlo tú»): lo repetido de casa base se descarta, lo propio se copia a un cajón
        # de casa base y se poda. Solo siguen parando la poda los borradores/encargos vivos y
        # una copia que falle. Se rescata únicamente si la poda va a ocurrir (limpio y seguro).
        perdible = _ramas._trabajo_vivo(wt)
        if perdible:
            cajon = os.path.join(BASE, "tools", "state", "rescate-poda",
                                 "%s-%s" % (os.path.basename(wt.rstrip("/")),
                                            __import__("time").strftime("%Y%m%d-%H%M%S")))
            r = _ramas.rescatar(wt, BASE, cajon, copiar=bool(apply and limpio and seguro))
            if r["bloquean"]:
                seguro = False
                acciones.append(
                    "poda: NO — %d fichero(s) son trabajo vivo o no se pudieron guardar: %s%s"
                    % (len(r["bloquean"]), ", ".join(r["bloquean"][:4]),
                       " y %d más" % (len(r["bloquean"]) - 4) if len(r["bloquean"]) > 4 else ""))
            else:
                perdible = []
                acciones.append(
                    "rescate: %d ignorado(s) ya repetidos en casa base; %d con algo propio %s"
                    % (len(r["repetidos"]), len(r["rescatados"]),
                       ("guardados en " + os.path.relpath(cajon, BASE)) if (apply and r["rescatados"])
                       else "(se guardarían en tools/state/rescate-poda/ al aplicar)"))
        if apply and limpio and seguro:
            # Candado COMPARTIDO "git-mutex" — MISMO nombre que la fusión de arriba (A1, 10-jul-26,
            # hallazgo B): antes esta poda mutaba `.git/worktrees/` SIN candado, así que una fusión
            # concurrente de OTRA sesión (que sí lo tomaba) no la serializaba con esta poda — la
            # grieta de aislamiento real que encontró el comité de arquitectura sobre el `.git`
            # compartido. Reentrante NO hace falta: es una toma nueva, secuencial tras soltar la de
            # la fusión (arriba), no anidada.
            try:
                with _lock.lock(GIT_MUTEX, timeout=60.0):
                    rc, _, err = _run(["git", "-C", BASE, "worktree", "remove", wt])
                podado = rc == 0
                acciones.append("poda: worktree %s" % ("eliminado" if podado else ("NO se pudo: " + err)))
            except TimeoutError as e:
                acciones.append("poda: NO se pudo tomar el candado compartido (%s) — reintenta luego" % e)
        elif perdible:
            pass                      # el motivo ya se dijo arriba; no hace falta repetirlo
        elif not seguro:
            acciones.append("poda: NO (queda trabajo sin fusionar — no se poda para no perderlo)")
        else:
            acciones.append("poda: pendiente (corre con --apply)")
    else:
        acciones.append("poda: omitida (--no-poda)")

    return dict(p, aplicado=apply, fusionado=fusionado, podado=podado, acciones=acciones)


def _continuidad_al_dia():
    """(al_dia: bool, dias: float|None, ultima: str). ¿Se ha escrito la memoria de sesión?

    El cierre commiteaba, fusionaba, reindexaba y podaba... y no dejaba ni una línea de QUÉ pasó.
    El resultado, medido el 3-sep-2026: la última entrada de continuidad era del 27-jul. **38 días
    sin memoria de sesión**, mientras se cerraban sesiones con normalidad. Todo lo aprendido en
    ellas quedó solo en transcripciones que nadie relee y que además se borran (la sesión que
    produjo el paquete de FGFR4 desapareció y con ella el entregable).

    Esto no bloquea el cierre: avisar es suficiente y bloquear un cierre por una nota sería peor
    que la enfermedad. Pero deja de ser invisible.
    """
    try:
        import time
        cont = os.path.join(BASE, "tools", "state", "continuity")
        entradas = [f for f in os.listdir(cont) if f != "INDEX.md"] if os.path.isdir(cont) else []
        idx = os.path.join(cont, "INDEX.md")
        mtimes = [os.path.getmtime(os.path.join(cont, f)) for f in entradas]
        if os.path.exists(idx):
            mtimes.append(os.path.getmtime(idx))
        if not mtimes:
            return False, None, "nunca"
        dias = (time.time() - max(mtimes)) / 86400.0
        import datetime as _dt
        ultima = _dt.datetime.fromtimestamp(max(mtimes)).strftime("%Y-%m-%d")
        return dias < 2.0, dias, ultima
    except Exception:
        return True, None, "?"        # ante la duda no se molesta: es un aviso, no un guardia


def _una_linea(r):
    if r.get("error"):
        return "⚠️ cierre: " + r["error"]
    modo = "APLICADO" if r.get("aplicado") else "DRY-RUN"
    n_cambios = len(r.get("sin_commitear", []))
    n_docs = len(r.get("docs_nuevos", []))
    linea = ("✅ %s · rama %s · %d cambio(s) commiteado(s) · %s · %d doc(s) nuevos · worktree %s"
             % (modo, r.get("rama", "?"), n_cambios,
                "fusionado a casa base" if r.get("fusionado") else "sin fusión",
                n_docs, "podado" if r.get("podado") else "intacto"))
    al_dia, dias, ultima = _continuidad_al_dia()
    if not al_dia:
        cuanto = ("nunca" if dias is None else "hace %.0f día(s), la última es del %s"
                  % (dias, ultima))
        linea += ("\n⚠️  MEMORIA DE SESIÓN sin escribir (%s). Lo que se aprendió hoy se pierde "
                  "cuando se cierre esta ventana: las transcripciones no se releen y además se "
                  "borran. Escríbela antes de terminar:\n"
                  "     python3 tools/continuity.py record --confiable \"qué se hizo, qué se "
                  "decidió, qué espera\"" % cuanto)
    return linea


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    apply = "--apply" in argv
    podar = "--no-poda" not in argv
    scope = None
    if "--scope" in argv:
        i = argv.index("--scope")
        if i + 1 < len(argv):
            scope = argv[i + 1]
    r = cerrar(apply=apply, scope=scope, podar=podar)
    if "--json" in argv:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if not r.get("error") else 1
    if r.get("error"):
        print(_una_linea(r))
        return 1
    print("Cierre de sesión — %s" % ("APLICANDO" if apply else "DRY-RUN (usa --apply para ejecutar)"))
    print("  worktree: %s  (rama %s → %s)" % (r["worktree"], r["rama"], r["rama_base"]))
    for a in r.get("acciones", []):
        print("  · " + a)
    print(_una_linea(r))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
