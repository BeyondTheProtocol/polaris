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


def _git_dir_base():
    rc, gd, _ = _git(["rev-parse", "--git-dir"], BASE)
    gd = gd.strip()
    return (gd if os.path.isabs(gd) else os.path.join(BASE, gd)) if rc == 0 and gd else ""


def _base_ocupada():
    """Motivo (str) si casa base tiene una operación de git A MEDIAS o cambios de otro; "" si no.

    POR QUÉ (24-sep-26). Otra sesión fusionaba en casa base, chocaba y resolvía a mano; el candado
    `git-mutex` ya lo había soltado al volver su `git merge`. Un cierre que llegara entonces hacía
    su propio `git merge`, fallaba por «no has concluido tu fusión» y respondía con `merge --abort`:
    la resolución ajena se perdía y la tool decía «casa base sigue como estaba». Reproducido en
    `tests/test_cerrar_sesion_conflicto.py::fusion_ajena_a_medias`. Lo sin seguimiento (`??`) no
    cuenta: casa base siempre tiene alguno y no lo toca un merge."""
    gd = _git_dir_base()
    if gd:
        for marca, que in (("MERGE_HEAD", "una fusión"), ("CHERRY_PICK_HEAD", "un cherry-pick"),
                           ("REVERT_HEAD", "un revert"), ("rebase-merge", "un rebase"),
                           ("rebase-apply", "un rebase")):
            if os.path.exists(os.path.join(gd, marca)):
                return "%s de otro a medias (%s)" % (que, marca)
    _, sin_resolver, _ = _git(["diff", "--name-only", "--diff-filter=U"], BASE)
    if sin_resolver.strip():
        return "ficheros sin resolver: " + ", ".join(sin_resolver.split()[:5])
    _, st, _ = _git(["status", "--porcelain", "--untracked-files=no"], BASE)
    if st.strip():
        return "cambios de otro sin commitear: " + ", ".join(l[3:] for l in st.splitlines()[:5])
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


def _registrar_en_vega(rama, base, rojas_base):
    """Cada fusión de sesión queda en el registro de aprobaciones de Vega (1-oct-26, {{TITULAR}}: «quiero
    que Vega sea la orquestadora de todo»). Hasta hoy las sesiones fusionaban a casa base y Vega no
    se enteraba. `codigo_fusion` es nivel A en la política (Vega aprueba sola la fontanería), así que
    el registro lleva la prueba (merge + baterías) y cómo deshacerlo; Vega lo ve en su resumen y
    `verificacion` lo muestrea cada semana. Fail-soft: un fallo del registro no deshace la fusión,
    pero se dice en el resumen del cierre."""
    try:
        sha = _git(["rev-parse", "HEAD"], BASE)[1].strip()
        asuntos = _git(["log", "--format=%s", "--no-merges", "%s^1..%s^2" % (sha, sha)], BASE)[1]
        que = "; ".join(l for l in asuntos.splitlines() if l.strip())[:600] or rama
        prueba = "merge %s en %s · baterías de casa base: %s" % (
            sha[:9], base, ("ROJAS: " + ", ".join(rojas_base)) if rojas_base else
            ("en verde" if rojas_base is not None else "sin verificar (BTP_CIERRE_SIN_VERIFICAR)"))
        import aprobaciones
        aprobaciones.registrar("codigo_fusion", que, "cierre de sesión de %s" % rama, prueba,
                               "git -C %s revert -m 1 %s" % (BASE, sha[:9]),
                               quien="sesion:%s" % rama)
        return "vega: fusión apuntada en su registro de aprobaciones (%s)" % sha[:9]
    except Exception as e:  # noqa: BLE001
        return "vega: ‼️ NO se pudo apuntar la fusión en su registro (%s: %s)" % (type(e).__name__, e)


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

    # CASA BASE OCUPADA (24-sep-26): una fusión (u otra operación) de otro a medias. No se toca:
    # ni se fusiona encima ni se aborta lo suyo. Antes del commit, como el chequeo de master.
    ocupada = _base_ocupada() if apply else ""
    if ocupada:
        return dict(p, error="casa base está ocupada: %s. No la toco (ni fusiono encima ni aborto lo "
                             "suyo). Tu rama está intacta: repite el cierre cuando acabe." % ocupada,
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
    head_antes = None   # HEAD de casa base justo ANTES de fusionar (línea base de F)
    if n > 0:
        if apply:
            # (c) FUSIÓN a casa base — SINGLETON: candado COMPARTIDO "git-mutex" (mismo que la poda
            # de abajo y que tools/ramas.py) para serializar con CUALQUIER otro actor que mute el
            # `.git` de la casa base, no solo con otros cierres de sesión (A1, 10-jul-26).
            try:
                with _lock.lock(GIT_MUTEX, timeout=60.0):
                    # Otra vez DENTRO del candado (24-sep-26): entre el chequeo de arriba y aquí
                    # otra sesión ha podido empezar su fusión.
                    ocupada = _base_ocupada()
                    if ocupada:
                        return dict(p, error="casa base está ocupada: %s. No la toco (ni fusiono "
                                             "encima ni aborto lo suyo). Tu rama está intacta: "
                                             "repite el cierre cuando acabe." % ocupada,
                                    acciones=acciones)
                    head_antes = _git(["rev-parse", "HEAD"], BASE)[1].strip()
                    punta = _git(["rev-parse", rama], wt)[1].strip()
                    rc, _, err = _git(["merge", "--no-ff", rama, "-m",
                                       "merge(%s): cierre de sesión → casa base" % rama], BASE)
                    if rc != 0:
                        # Deshacer AQUÍ, dentro del candado (22-sep-26). Sin esto, un conflicto
                        # dejaba casa base —el sistema vivo 24/7— con `UU` y marcadores
                        # `<<<<<<<` en disco hasta que alguien lo abortara a mano: pasó con dos
                        # sesiones que arreglaron el mismo rojo a la vez. La rama no se toca.
                        # Solo se aborta la fusión PROPIA (24-sep-26): la que apunta a mi rama.
                        conflictos = _git(["diff", "--name-only", "--diff-filter=U"], BASE)[1]
                        gd = _git_dir_base()
                        mh = os.path.join(gd, "MERGE_HEAD") if gd else ""
                        mia = bool(mh) and os.path.exists(mh) and \
                            open(mh).read().split()[:1] == [punta]
                        if not mia:
                            estado = ("no había fusión mía que deshacer: no toco casa base"
                                      if not (mh and os.path.exists(mh)) else
                                      "‼️ hay una fusión en curso que NO es mía: no la toco")
                        else:
                            rc_ab, _, err_ab = _git(["merge", "--abort"], BASE)
                            limpia = not _base_ocupada() and \
                                _git(["rev-parse", "HEAD"], BASE)[1].strip() == head_antes
                            estado = ("fusión deshecha (merge --abort): casa base sigue como estaba"
                                      if rc_ab == 0 and limpia else
                                      "‼️ NO se pudo deshacer del todo (merge --abort: %s) — casa base "
                                      "a medio fusionar, arréglalo a mano YA" % (err_ab or rc_ab or
                                                                                 "estado distinto"))
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

    rojas_base = None
    clasif = None
    if apply and fusionado and not os.environ.get("BTP_CIERRE_SIN_VERIFICAR"):
        rojas_base, corridas = _verificar_en_casa_base()
        acciones.append("casa base: %d batería(s) que en el worktree se saltan, corridas allí → %s"
                        % (corridas, ("ROJAS: " + ", ".join(rojas_base)) if rojas_base else "en verde"))
        try:
            post = _git(["rev-parse", "HEAD"], BASE)[1].strip()
            if rojas_base:
                clasif = hacer_ruido(rojas_base, head_antes, post, rama)
                acciones.append("casa base roja → %s · deuda: %s · aviso: %s" % (
                    clasif["resumen"], ", ".join(clasif["abiertas"]) or "ninguna nueva",
                    {True: "enviado", False: "NO se pudo enviar", None: "no hacía falta"}[clasif["avisado"]]))
            guardar_linea_base(post, rojas_base)
        except Exception as e:  # noqa: BLE001  registrar y avisar no puede deshacer la fusión ni tumbar el cierre
            acciones.append("‼️ casa base roja: NO pude abrir la deuda / avisar (%s: %s)" % (type(e).__name__, e))
    if apply and fusionado:
        acciones.append(_registrar_en_vega(rama, base, rojas_base))

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
        # debes hacerlo tú»): se archiva a casa base con `ramas.rescatar` —el mismo rescate que usa
        # la autopoda, no otro— y se poda. `_trabajo_vivo` ya descuenta el residuo reconocido
        # (restos de la batería, copia exacta de casa base, lo ya rescatado), así que aquí solo
        # queda lo que de verdad hay que salvar. Siguen parando la poda los borradores y encargos
        # VIVOS (esperan su firma: archivarlos los sacaría del outbox) y un rescate que falle.
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
        viva = _sesion_viva_en(wt) if (limpio and seguro) else None
        if viva:
            acciones.append("poda: aplazada — %s. Podarlo le quitaría los hooks del muro a esa sesión; "
                            "lo poda la autopoda diaria (`ramas.py autopoda`, rutina com.btp.git-barrido) cuando se cierre" % viva)
        elif apply and limpio and seguro:
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

    return dict(p, aplicado=apply, fusionado=fusionado, podado=podado, acciones=acciones,
                rojas_casa_base=rojas_base, rojas_clasificadas=clasif)


def _sesion_viva_en(wt):
    """Motivo si hay una sesión de Claude viva DENTRO de `wt` (la que llama incluida), o "".

    POR QUÉ (25-sep-26). Los hooks de una sesión se resuelven con `${CLAUDE_PROJECT_DIR}`, que es
    su worktree. `--apply` podaba siempre el worktree desde el que se le llamaba, así que la sesión
    que fusionaba y SEGUÍA trabajando se quedaba sin ningún hook: probado, `gh issue create --help`
    pasó sin denegar en la sesión podada mientras el mismo `salida_guard.py` en casa base sí lo
    denegaba. Fail-closed: si no se puede saber, se trata como ocupado."""
    raiz = os.path.realpath(wt).rstrip("/")
    if os.environ.get("CLAUDECODE") == "1":
        yo = os.path.realpath(os.getcwd())
        if yo == raiz or yo.startswith(raiz + "/"):
            return "la sesión que llama (PID %s) sigue viva en este worktree" % os.environ.get("CLAUDE_PID", "?")
    try:
        vivas = _ramas.sesiones()
    except Exception as e:                                     # noqa: BLE001
        return "no se pudo comprobar si hay sesiones vivas (%s)" % type(e).__name__
    ilegibles = sum(1 for s in vivas if not s.get("cwd"))
    if ilegibles:
        # 26-sep-26: sin cwd no se sabe si están aquí (el agente del barrido podó así 7 vivos).
        return "%d sesión(es) viva(s) sin cwd legible: no se sabe si están aquí" % ilegibles
    for s in vivas:
        c = os.path.realpath(s.get("cwd") or "") if s.get("cwd") else ""
        if c and (c == raiz or c.startswith(raiz + "/")):
            return "hay una sesión de Claude viva en este worktree (PID %s)" % s.get("pid", "?")
    return ""


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


# Lo que en un worktree se SALTA (rc=77, `tests/_entorno.exige`) solo se prueba en casa base. El
# 26-sep-26 una fusión dio «TODO EN VERDE» en el worktree y dejó `test_fuga.sh` ROJO en casa base
# (lo cazó otra sesión); ya había pasado el 22-sep y la memoria `feedback-verde-en-worktree-no-es-
# verde` no bastó. Así que el cierre, tras fusionar, corre ESAS baterías allí y lo dice.
_PUEDE_SALTAR = re.compile(r"exit\(77\)|exit 77|_entorno\.exige|\bexige\(|SKIP\s*=\s*77")


def _verificar_en_casa_base(tope_s=600):
    """(rojas, corridas): corre en casa base los tests que pueden saltarse (rc 77) fuera de ella.
    rc 0 y 77 son verde/saltado; cualquier otro, o no acabar a tiempo, es rojo."""
    from concurrent.futures import ThreadPoolExecutor
    tdir = os.path.join(BASE, "tests")
    candidatos = []
    for nombre in sorted(os.listdir(tdir)) if os.path.isdir(tdir) else []:
        if not (nombre.startswith("test_") and nombre.endswith((".py", ".sh"))):
            continue
        try:
            with open(os.path.join(tdir, nombre), encoding="utf-8", errors="ignore") as f:
                if _PUEDE_SALTAR.search(f.read()):
                    candidatos.append(nombre)
        except Exception:
            continue

    def uno(nombre):
        cmd = (["bash"] if nombre.endswith(".sh") else [sys.executable]) + [os.path.join(tdir, nombre)]
        env = dict(os.environ)
        for k in ("BTP_STATE_DIR", "BTP_REPO", "CLAUDE_PROJECT_DIR"):
            env.pop(k, None)
        # Ni los permisos de excepción con los que se llama al cierre (`BTP_GIT_BASE_OK=1`…): el
        # 26-sep daban `test_fuga.sh` rojo en casa base porque el muro veía el permiso puesto.
        for k in [k for k in env if re.match(r"(BTP_\w*_OK|MURO_ALLOW\w*|BTP_CIERRE_SIN_VERIFICAR)$", k)]:
            env.pop(k, None)
        try:
            p = subprocess.run(cmd, cwd=BASE, env=env, capture_output=True, text=True,
                               timeout=tope_s, stdin=subprocess.DEVNULL)
            return nombre, p.returncode in (0, 77)
        except subprocess.TimeoutExpired:
            return nombre, False
        except Exception:
            return nombre, False
    with ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(uno, candidatos))
    # Lo rojo en paralelo se repite SOLO, de uno en uno: en su estreno (26-sep) dio `test_fuga.sh`
    # rojo con 4 a la vez y verde corrido solo (se estorban). Cuenta como rojo lo que repite.
    return [n for n, ok in res if not ok and not uno(n)[1]], len(candidatos)


# ── F (10-oct-26): una fusión que deja ROJA una batería HACE RUIDO ───────────────────────────────
# Hasta hoy, si tras fusionar una batería de casa base salía roja, el cierre solo lo IMPRIMÍA: quien no
# leyera esa línea no se enteraba, y la deuda no se abría. Ahora (1) abre deuda estable por batería,
# (2) avisa por el canal de salud (salida.report_to_titular, el mismo que healthcheck) y de forma
# INMEDIATA si es del núcleo del muro, y (3) lo deja en el resumen de una línea. NO revierte ni bloquea.
# Y no culpa a la rama de lo que ya estaba: se compara con la LÍNEA BASE anterior a la fusión.
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(BASE, "tools", "state")
LINEA_BASE = os.path.join(STATE, "cerrar_sesion", "rojas_casa_base.json")
_MURO_RESPALDO = ("test_muro", "test_salida_guard", "test_ok_envio", "test_permiso", "test_gate", "test_fuga",
                  "test_halt", "test_clinico", "test_casa_base", "test_singleton", "test_rama_vista",
                  "test_launch_loopback", "test_copy_web", "test_token_rotacion", "test_regla_en_accion",
                  "test_entrada_guard", "test_canario_muro", "test_worktree_guard", "test_enrutado")


def _es_muro_bateria(nombre):
    """¿Es del núcleo del muro? Los mismos prefijos que la puerta de fusión; si no se pueden importar, la
    copia (nunca MENOS vetos)."""
    try:
        import tests_afectados
        return str(nombre).startswith(tuple(tests_afectados._MURO_PREFIJOS))
    except Exception:  # noqa: BLE001
        return str(nombre).startswith(_MURO_RESPALDO)


def leer_linea_base():
    """{'sha','fecha','rojas'} de la última verificación en casa base, o None."""
    try:
        with open(LINEA_BASE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("rojas"), list) else None
    except Exception:  # noqa: BLE001
        return None


def guardar_linea_base(sha, rojas):
    """Lo que quedó rojo en casa base tras esta fusión = la línea base de la PRÓXIMA. Atómico, fail-soft."""
    try:
        import datetime
        os.makedirs(os.path.dirname(LINEA_BASE), exist_ok=True)
        tmp = LINEA_BASE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"sha": sha, "fecha": datetime.datetime.now().isoformat(timespec="seconds"),
                       "rojas": sorted(rojas or [])}, f, ensure_ascii=False)
        os.replace(tmp, LINEA_BASE)
        return True
    except Exception:  # noqa: BLE001
        return False


def _rojas_de_la_nocturna():
    """Baterías que la última pasada nocturna ya daba por rojas (nuevas, flaky o conocidas)."""
    try:
        with open(os.path.join(STATE, "suite_nocturna", "historial.jsonl"), encoding="utf-8") as f:
            ult = [json.loads(l) for l in f if l.strip()][-1]
        return set(ult.get("nuevos", [])) | set(ult.get("flaky", [])) | set(ult.get("conocidos", []))
    except Exception:  # noqa: BLE001
        return set()


def _deudas_abiertas():
    try:
        import deuda
        return set(deuda.abiertas())
    except Exception:  # noqa: BLE001
        return set()


def _claves_previas(bateria):
    return ("casa-base-roja-%s" % bateria, "suite-nocturna-%s" % bateria, "suite-nocturna-flaky-%s" % bateria)


def clasifica_rojas(rojas, pre_sha, linea_base, deudas, nocturna):
    """Reparte las rojas de casa base tras la fusión:
      · previas        — ya estaban rojas (línea base, deuda abierta o la nocturna): NO son de esta rama;
      · nuevas         — la línea base es de justo antes de esta fusión y la batería NO estaba roja: la rompió esta fusión;
      · sin_atribuir   — no hay línea base fiable (no existe o es de otro punto de casa base): no se culpa a nadie.
    """
    fresca = bool(linea_base) and linea_base.get("sha") == pre_sha
    res = {"nuevas": [], "previas": [], "sin_atribuir": [],
           "linea_base": "fresca" if fresca else ("desfasada" if linea_base else "sin")}
    for b in sorted(rojas or []):
        conocida = ((linea_base is not None and b in linea_base.get("rojas", [])) or b in nocturna
                    or any(k in deudas for k in _claves_previas(b)))
        if conocida:
            res["previas"].append(b)
        elif fresca:
            res["nuevas"].append(b)
        else:
            res["sin_atribuir"].append(b)
    return res


def _avisar_fusion(texto, urgente):
    """Aviso por el canal de salud del sistema (el mismo que healthcheck: salida.report_to_titular, categoría
    «humano»). `urgente` salta el silencio nocturno. BTP_CIERRE_AVISO_A=<fichero>: sumidero para los tests."""
    sumidero = os.environ.get("BTP_CIERRE_AVISO_A")
    if sumidero:
        with open(sumidero, "a", encoding="utf-8") as f:
            f.write(json.dumps({"texto": texto, "urgente": urgente}, ensure_ascii=False) + "\n")
        return True
    try:
        import salida
        salida.report_to_titular(texto, categoria="humano", urgente=urgente, voz="sobria", fuente="cerrar_sesion")
        return True
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("cerrar_sesion: no pude avisar de la casa base roja: %r\n" % e)
        return False


def hacer_ruido(rojas, pre_sha, post_sha, rama, linea_base=None, deudas=None, nocturna=None,
                abrir=None, visto=None, avisar=None):
    """Abre deuda, avisa y devuelve {'nuevas','previas','sin_atribuir','abiertas','avisado','resumen'}.
    `abrir/visto/avisar` se pueden inyectar (tests); por defecto, tools/deuda.py y el canal de salud."""
    linea_base = leer_linea_base() if linea_base is None else linea_base
    deudas = _deudas_abiertas() if deudas is None else deudas
    nocturna = _rojas_de_la_nocturna() if nocturna is None else nocturna
    if abrir is None or visto is None:
        import deuda
        abrir = abrir or (lambda clave, que, muro: deuda.abrir(
            clave, que, ned="alto" if muro else "medio", muro=muro, dueno="cierre-de-sesion"))
        visto = visto or (lambda clave, nota: deuda.visto(clave, nota))
    avisar = avisar or _avisar_fusion
    c = clasifica_rojas(rojas, pre_sha, linea_base, deudas, nocturna)
    c.update(abiertas=[], avisado=None)
    corto = (post_sha or "")[:9]
    for b in c["previas"]:
        clave = next((k for k in _claves_previas(b) if k in deudas), None)
        if clave:
            visto(clave, "sigue roja tras la fusión de %s (%s): ya estaba, no es de esta rama" % (rama, corto))
    for b in c["nuevas"] + c["sin_atribuir"]:
        muro = _es_muro_bateria(b)
        if b in c["nuevas"]:
            que = ("La batería %s pasó de VERDE a ROJA en casa base con la fusión de la rama %s (commit %s, sobre %s). "
                   "La línea base anterior a la fusión la daba en verde. Se corre en casa base porque en un worktree se "
                   "salta (rc 77). No se ha revertido nada." % (b, rama, corto, (pre_sha or "?")[:9]))
        else:
            que = ("La batería %s está ROJA en casa base tras la fusión de la rama %s (commit %s). NO hay línea base "
                   "fiable de antes de fusionar (%s): no se sabe si ya estaba roja, así que NO se atribuye a la rama. "
                   "No se ha revertido nada." % (b, rama, corto,
                                                 "no existe" if c["linea_base"] == "sin" else "es de otro punto de casa base"))
        try:
            abrir("casa-base-roja-%s" % b, que, muro)
            c["abiertas"].append("casa-base-roja-%s" % b)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write("cerrar_sesion: no pude abrir la deuda de %s: %r\n" % (b, e))
    # aviso: las NUEVAS siempre; las sin atribuir solo si son del muro (una roja del muro en casa base se oye)
    a_avisar = list(c["nuevas"]) + [b for b in c["sin_atribuir"] if _es_muro_bateria(b)]
    if a_avisar:
        muro_rojas = [b for b in a_avisar if _es_muro_bateria(b)]
        texto = ("Una fusión a casa base (rama %s, commit %s) deja en ROJO: %s. %s%sNo he revertido ni bloqueado nada. "
                 "Deuda abierta: %s."
                 % (rama, corto, ", ".join(a_avisar),
                    "ENTRE ELLAS HAY BATERÍAS DEL MURO (%s). " % ", ".join(muro_rojas) if muro_rojas else "",
                    "Antes de fusionar estaban en verde. " if c["nuevas"] else "No hay línea base previa: no sé si ya estaban rojas. ",
                    ", ".join("casa-base-roja-" + b for b in a_avisar)))
        c["avisado"] = bool(avisar(texto, bool(muro_rojas)))
    partes = []
    if c["nuevas"]:
        partes.append("%d NUEVA(S) por esta fusión (%s)" % (len(c["nuevas"]), ", ".join(c["nuevas"])))
    if c["previas"]:
        partes.append("%d ya estaban rojas" % len(c["previas"]))
    if c["sin_atribuir"]:
        partes.append("%d sin línea base previa, no se atribuyen" % len(c["sin_atribuir"]))
    c["resumen"] = "casa base roja: " + " · ".join(partes) if partes else ""
    return c


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
    cl = r.get("rojas_clasificadas") or {}
    if cl.get("resumen"):
        linea += " · " + cl["resumen"]          # la línea de UNA línea lo dice (F, 10-oct-26)
    if r.get("rojas_casa_base"):
        linea += ("\n🔴 CASA BASE ROJA tras fusionar: %s. En el worktree se saltaban, así que tu "
                  "«en verde» no las cubría. Arréglalo antes de dar nada por hecho."
                  % ", ".join(r["rojas_casa_base"]))
        if cl:
            if cl.get("nuevas"):
                linea += ("\n   ↳ LAS ROMPIÓ ESTA FUSIÓN (estaban en verde antes): %s. Deuda abierta y aviso hecho."
                          % ", ".join(cl["nuevas"]))
            if cl.get("previas"):
                linea += "\n   ↳ ya estaban rojas antes de fusionar (no son de esta rama): %s." % ", ".join(cl["previas"])
            if cl.get("sin_atribuir"):
                linea += ("\n   ↳ sin línea base previa, NO se atribuyen a esta rama: %s (deuda abierta igualmente)."
                          % ", ".join(cl["sin_atribuir"]))
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
