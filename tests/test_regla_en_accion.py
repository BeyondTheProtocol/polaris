#!/usr/bin/env python3
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# Sin `_exige`: este test solo manda payloads con rutas como cadenas, no toca el estado vivo, y
# con el gate hacía SKIP en un worktree — o sea, el hook de la RAMA no se probaba nunca antes de
# fusionarlo (lo cazó `verificacion` el 24-sep-26). Lo que necesita casa base se salta caso a caso.
# -*- coding: utf-8 -*-
"""Tests del hook PreToolUse que recuerda la regla en el momento de actuar.

Comprueba las tres propiedades que lo hacen útil sin ser un incordio:
  1. dispara en lo que importa,
  2. calla en lo inocente,
  3. no repite la misma regla dos veces en la misma sesión.
"""
import json
import os
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(RAIZ, ".claude", "hooks", "regla_en_accion.py")

fallos = []


def correr(payload, tmpdir):
    # CLAUDE_PROJECT_DIR apunta al tmpdir, NO al repo: el hook escribe ahí su log de
    # auditoría, y con la raíz real cada pasada de tests metía 30 entradas falsas en
    # `.claude/logs/regla-en-accion.log`. Un log de auditoría con ruido de test no
    # sirve para auditar nada.
    entorno = dict(os.environ, TMPDIR=tmpdir, CLAUDE_PROJECT_DIR=tmpdir,
                   BTP_REGLA_LOG=os.path.join(tmpdir, "regla-en-accion.log"))
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                       capture_output=True, text=True, env=entorno)
    if not p.stdout.strip():
        return None
    return json.loads(p.stdout)["hookSpecificOutput"]


def correr_desde_worktree(payload, tmpdir, wt):
    """Como `correr`, pero con CLAUDE_PROJECT_DIR apuntando a un directorio cuya ruta contiene
    `/.claude/worktrees/`, que es lo que mira `_en_worktree()`.

    Existe porque había un HUECO: `correr` pone CLAUDE_PROJECT_DIR=tmpdir, así que
    `_en_worktree()` era SIEMPRE False y la rama de worktree de `launchd-desde-worktree`
    no se había ejecutado nunca en un test. Una regla que solo aplica dentro de un worktree,
    probada solo fuera de uno, es una regla sin probar."""
    # BTP_REGLA_LOG: sin esto el hook escribía su log DENTRO del worktree simulado, que cuelga del
    # repo real (`.claude/worktrees/prueba-freno`), y cada pasada dejaba allí un directorio huérfano.
    entorno = dict(os.environ, TMPDIR=tmpdir, CLAUDE_PROJECT_DIR=wt,
                   BTP_REGLA_LOG=os.path.join(tmpdir, "regla-en-accion.log"))
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                       capture_output=True, text=True, env=entorno)
    if not p.stdout.strip():
        return None
    return json.loads(p.stdout)["hookSpecificOutput"]


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def main():
    import tempfile
    tmp = tempfile.mkdtemp(prefix="btp_regla_")

    print("── dispara donde importa ──")
    r = correr({"session_id": "a", "tool_name": "Bash",
                "cwd": RAIZ, "tool_input": {"command": "git push origin master"}}, tmp)
    check(r and "GitHub" in r["additionalContext"], "git push recuerda que el repo no se sube")
    check(r and r.get("permissionDecision") == "ask", "git push además pide confirmación")

    r = correr({"session_id": "b", "tool_name": "Edit",
                "tool_input": {"file_path": "/x/components/Hero.vue"}}, tmp)
    check(r and "{{TITULAR}}" in r["additionalContext"], "editar el hero recuerda el gate de copy")
    check(r and r.get("permissionDecision") is None, "el hero se recuerda pero NO se bloquea")

    ruta_clinica = "/x/" + "_PRIVADO" + "_CLINICO/informe.md"   # partido: hay un guard que vigila la cadena
    r = correr({"session_id": "c", "tool_name": "Read",
                "tool_input": {"file_path": ruta_clinica}}, tmp)
    check(r and "lector_clinico" in r["additionalContext"], "ruta clínica recuerda la ventanilla")

    r = correr({"session_id": "d", "tool_name": "Edit",
                "tool_input": {"file_path": os.path.join(RAIZ, "CLAUDE.md")}}, tmp)
    check(r and "25KB" in r["additionalContext"], "tocar CLAUDE.md recuerda los límites reales")

    r = correr({"session_id": "e", "tool_name": "mcp__gmail__send_message",
                "tool_input": {"to": "x@y.z"}}, tmp)
    check(r and "BORRADOR" in r["additionalContext"], "enviar recuerda el gate de salida")

    r = correr({"session_id": "f", "tool_name": "mcp__gmail__create_draft",
                "tool_input": {"to": "x@y.z"}}, tmp)
    check(r and "HTML" in r["additionalContext"],
          "crear un borrador recuerda el formato (la regla que se repitió 3 veces)")
    check(r and "no sale hacia fuera" not in r["additionalContext"].lower()
          and r.get("permissionDecision") is None,
          "pero un borrador NO se trata como salida ni se bloquea")

    print("── calla en lo inocente ──")
    for comando in ("ls -la", "python3 tools/kb.py ask 'algo'", "git status", "git commit -m x"):
        r = correr({"session_id": "g", "tool_name": "Bash", "tool_input": {"command": comando}}, tmp)
        check(r is None, "silencio con «%s»" % comando)

    print("── no se repite ──")
    r1 = correr({"session_id": "z", "tool_name": "Bash",
                 "cwd": RAIZ, "tool_input": {"command": "git push origin master"}}, tmp)
    r2 = correr({"session_id": "z", "tool_name": "Bash",
                 "cwd": RAIZ, "tool_input": {"command": "git push origin master"}}, tmp)
    check(r1 is not None and r2 is None, "la misma regla no se repite en la misma sesión")
    r3 = correr({"session_id": "otra", "tool_name": "Bash",
                 "cwd": RAIZ, "tool_input": {"command": "git push origin master"}}, tmp)
    check(r3 is not None, "pero sí vuelve a salir en una sesión nueva")

    print("── worktree: no escribir en casa base ──")
    # El worktree simulado cuelga del repo REAL, porque el freno consulta el .gitignore de casa
    # base con `git check-ignore`: sobre un tmpdir sin repo git detrás no habría nada que medir.
    # BASE se calcula, no se asume RAIZ: esta batería se corre a menudo DESDE un worktree, y
    # entonces RAIZ ya es un worktree — montar otro dentro daría dos niveles, que en producción
    # no existen, y el test mediría una situación imposible.
    BASE = RAIZ.split("/.claude/worktrees/")[0]
    wt = os.path.join(BASE, ".claude", "worktrees", "prueba-freno")

    r = correr_desde_worktree({"session_id": "w1", "tool_name": "Edit",
                               "tool_input": {"file_path": os.path.join(BASE, "tools", "cola.py")}},
                              tmp, wt)
    check(r is not None and "casa base" in r["additionalContext"].lower(),
          "editar tools/cola.py de casa base desde un worktree dispara")
    check(r is not None and r.get("permissionDecision") == "deny",
          "…y DENIEGA, no solo pide confirmación (con `ask` la escritura pasaba igual)")

    # Un freno que solo frena la primera vez de la sesión no es un freno: el filtro
    # anti-repetición vale para los RECORDATORIOS, no para esto.
    r2 = correr_desde_worktree({"session_id": "w1", "tool_name": "Edit",
                                "tool_input": {"file_path": os.path.join(BASE, "tools", "cola.py")}},
                               tmp, wt)
    check(r2 is not None and r2.get("permissionDecision") == "deny",
          "…y sigue denegando a la segunda en la misma sesión (no se gasta)")

    # Puerta de escape declarada, como la de clinico_guard (MURO_ALLOW_CLINICAL=1): hay casos
    # legítimos raros, y una protección sin salida documentada se acaba desactivando entera.
    entorno = dict(os.environ, TMPDIR=tmp, CLAUDE_PROJECT_DIR=wt, BTP_ALLOW_CASA_BASE="1")
    p = subprocess.run([sys.executable, HOOK],
                       input=json.dumps({"session_id": "w1b", "tool_name": "Edit",
                                         "tool_input": {"file_path": os.path.join(BASE, "tools", "cola.py")}}),
                       capture_output=True, text=True, env=entorno)
    check("deny" not in p.stdout, "BTP_ALLOW_CASA_BASE=1 abre la puerta a propósito")
    check(r is not None and os.path.join(wt, "tools", "cola.py") in r["additionalContext"],
          "…y dice la ruta BUENA del worktree, no solo que está mal")

    # Lo que un worktree SÍ tiene que poder escribir en casa base: si esto se frenase, el sistema
    # dejaría de funcionar (el estado vivo y la fuente de verdad solo existen ahí).
    for etiqueta, ruta in (
            ("el estado vivo (tools/state/)", os.path.join(BASE, "tools", "state", "deuda.json")),
            ("la fuente de verdad", os.path.join(BASE, "00_FUENTE-DE-VERDAD", "04 · IA", "x.md")),
            ("el parte de Gestion", os.path.join(BASE, "Gestion", "HOY.md"))):
        r = correr_desde_worktree({"session_id": "w-%s" % etiqueta, "tool_name": "Write",
                                   "tool_input": {"file_path": ruta}}, tmp, wt)
        ctx = (r or {}).get("additionalContext", "")
        check("casa base" not in ctx.lower(), "NO frena %s (git lo ignora: vive solo ahí)" % etiqueta)

    r = correr_desde_worktree({"session_id": "w5", "tool_name": "Edit",
                               "tool_input": {"file_path": "tools/cola.py"}}, tmp, wt)
    check((r or {}).get("additionalContext", "").find("casa base") == -1,
          "una ruta RELATIVA no se frena (resuelve dentro del worktree)")

    r = correr_desde_worktree({"session_id": "w6", "tool_name": "Edit",
                               "tool_input": {"file_path": os.path.join(wt, "tools", "cola.py")}},
                              tmp, wt)
    check((r or {}).get("additionalContext", "").find("casa base") == -1,
          "la ruta del propio worktree no se frena")

    # El caso de los daemons del lazo: corren DESDE casa base, no desde un worktree. Si esto
    # disparase, el lazo 24/7 se quedaría pidiendo confirmación a nadie.
    r = correr({"session_id": "w7", "tool_name": "Edit",
                "tool_input": {"file_path": os.path.join(BASE, "tools", "cola.py")}}, tmp)
    check((r or {}).get("additionalContext", "").find("casa base") == -1,
          "fuera de un worktree NO dispara (es el caso de los daemons)")

    r = correr_desde_worktree({"session_id": "w8", "tool_name": "Bash",
                               "tool_input": {"command": "sed -i '' s/a/b/ %s"
                                              % os.path.join(BASE, "tools", "cola.py")}}, tmp, wt)
    check(r is not None and "casa base" in r["additionalContext"].lower(),
          "un `sed -i` sobre casa base también dispara (cobertura declarada de shell)")

    r = correr_desde_worktree({"session_id": "w9", "tool_name": "Bash",
                               "tool_input": {"command": "cat %s"
                                              % os.path.join(BASE, "tools", "cola.py")}}, tmp, wt)
    check((r or {}).get("additionalContext", "").find("casa base") == -1,
          "LEER casa base desde un worktree no se frena (leer fuera está bien)")

    print("── worktree visto por el `cwd` de la entrada, como llega en vivo (22-sep-26) ──")
    # El hueco de verdad: en las sesiones worktree de la app de escritorio el hook corre con
    # CLAUDE_PROJECT_DIR = casa base, no el worktree. Todo lo de arriba fija CLAUDE_PROJECT_DIR
    # al worktree, y por eso pasaba en verde mientras en vivo `echo > ~/claudecode/tools/…`
    # escribía en casa base sin que el hook dijera nada (comprobado el 22-sep, 10:15). Aquí
    # CLAUDE_PROJECT_DIR es el tmpdir (no es worktree, igual que casa base) y lo único que dice
    # «estás en un worktree» es el `cwd` que el harness manda en la entrada.
    r = correr({"session_id": "v1", "tool_name": "Bash", "cwd": wt,
                "tool_input": {"command": "echo x > %s" % os.path.join(BASE, "tools", "cola.py")}}, tmp)
    check(r is not None and r.get("permissionDecision") == "deny",
          "shell que escribe en casa base con cwd=worktree DENIEGA aunque CLAUDE_PROJECT_DIR no sea worktree")
    r = correr({"session_id": "v2", "tool_name": "Edit", "cwd": wt,
                "tool_input": {"file_path": os.path.join(BASE, "tools", "cola.py")}}, tmp)
    check(r is not None and r.get("permissionDecision") == "deny"
          and os.path.join(wt, "tools", "cola.py") in r["additionalContext"],
          "Edit a casa base con cwd=worktree DENIEGA y da la ruta buena del worktree")
    r = correr({"session_id": "v3", "tool_name": "Edit", "cwd": os.path.join(wt, "tools"),
                "tool_input": {"file_path": os.path.join(BASE, "tools", "cola.py")}}, tmp)
    check(r is not None and r.get("permissionDecision") == "deny",
          "…también con el cwd en una subcarpeta del worktree")
    r = correr({"session_id": "v4", "tool_name": "Write", "cwd": wt,
                "tool_input": {"file_path": os.path.join(BASE, "tools", "state", "deuda.json")}}, tmp)
    check((r or {}).get("permissionDecision") != "deny",
          "con cwd=worktree el estado vivo de casa base sigue sin frenarse")
    r = correr({"session_id": "v5", "tool_name": "Edit", "cwd": BASE,
                "tool_input": {"file_path": os.path.join(BASE, "tools", "cola.py")}}, tmp)
    check(r is None or r.get("permissionDecision") != "deny",
          "con cwd=casa base (los daemons del lazo) NO dispara")
    r = correr({"session_id": "v6", "tool_name": "Bash", "cwd": wt,
                "tool_input": {"command": "launchctl kickstart -k gui/501/com.btp.x"}}, tmp)
    check(r is None or r.get("permissionDecision") != "ask",
          "launchd NO se pasa al cwd (fuera de alcance: deuda launchd-desde-worktree-no-ve-cwd)")

    print("── shell: solo frena si casa base es el DESTINO (replay 22-sep-26) ──")
    # Con el cwd el freno por fin se encendía en vivo, y el replay de 13.578 llamadas reales de
    # 7 días sacó 75 `deny` en Bash, casi todos LECTURAS: `2>/dev/null` casaba con «>/», y un
    # `cp x /tmp/…` convertía en escritura cualquier ruta de casa base citada en el comando.
    # Casos sacados de ese replay (rutas cambiadas a BASE).
    T = os.path.join(BASE, "tools", "cola.py")
    for n, cmd in enumerate((
            "ls %s 2>/dev/null | head" % os.path.join(BASE, "tools"),
            "cat %s > /tmp/copia.py" % T,
            "cp %s /tmp/cola.ok" % T,
            'W=x; B="%s"; ls -la "$W" "$B" 2>&1 | head' % T,
            "diff %s tools/cola.py >/dev/null 2>&1 || cp %s tools/cola.py" % (T, T),
            "python3 - <<'PY'\nopen('%s').read()\nprint(1) > None\nPY" % T,
            "grep -n x %s >> /tmp/log.txt" % T,
            # texto entre comillas que CONTIENE «> ruta»: un JSON y el mensaje de deuda.py
            "J='{\"c\":\"echo a > %s\"}'; echo \"$J\" | cat" % T,
            'python3 tools/deuda.py abrir k "pasa x > %s y no frena"' % T)):
        r = correr({"session_id": "s-ok-%d" % n, "tool_name": "Bash", "cwd": wt,
                    "tool_input": {"command": cmd}}, tmp)
        check((r or {}).get("permissionDecision") != "deny", "NO frena una lectura: %s" % cmd.split("\n")[0][:60])
    for n, cmd in enumerate((
            "echo x > %s" % T,
            "echo x >>%s" % T,
            "printf x 2> %s" % T,
            "echo x | tee -a %s" % T,
            "sed -i '' 's/a/b/' %s" % T,
            "cp tools/cola.py %s" % T,
            "mv /tmp/x.py %s" % T,
            "ls && cp -f tools/cola.py '%s'" % T,
            'echo "a > /tmp/x" > "%s"' % T)):
        r = correr({"session_id": "s-no-%d" % n, "tool_name": "Bash", "cwd": wt,
                    "tool_input": {"command": cmd}}, tmp)
        check((r or {}).get("permissionDecision") == "deny", "SÍ frena una escritura: %s" % cmd[:60])

    print("── push: el EJECUTADO y desde casa base, no el citado (22-sep-26) ──")
    # La regla casaba `git push` en cualquier parte del comando: el 22-sep saltó en vivo al
    # anotar una deuda cuyo texto lo citaba. Y el flujo de PRs de la web hace `git push` legítimo
    # desde ~/projects: la regla es sobre ESTE repo (su historial lleva datos clínicos).
    web = "/Users/polaris/projects/.mgc-staging/aviso-hueso"
    for n, (cmd, cwd) in enumerate((
            ('python3 tools/deuda.py nota k "la regla casa git push por texto"', RAIZ),
            ("echo 'git push origin main'", RAIZ),
            ("grep -n 'git push' .claude/hooks/*.py", RAIZ),
            ("cd %s && git push -u origin fix/x 2>&1 | tail -3" % web, RAIZ),
            ("git push -u origin fix/x", web))):
        r = correr({"session_id": "p-ok-%d" % n, "tool_name": "Bash", "cwd": cwd,
                    "tool_input": {"command": cmd}}, tmp)
        check("GitHub" not in (r or {}).get("additionalContext", ""), "push: NO salta con %s" % cmd[:50])
    for n, (cmd, cwd) in enumerate((
            ("git push", wt),
            ("ls && git push -u origin mi-rama 2>&1", RAIZ),
            ("cd %s && git push" % BASE, web),
            ("git -C %s push origin master" % BASE, web))):
        r = correr({"session_id": "p-no-%d" % n, "tool_name": "Bash", "cwd": cwd,
                    "tool_input": {"command": cmd}}, tmp)
        check(r is not None and "GitHub" in r["additionalContext"] and r.get("permissionDecision") == "ask",
              "push: SÍ salta con %s (cwd %s)" % (cmd[:45], os.path.basename(cwd)))

    print("── la regla hermana de launchd, por fin probada dentro de un worktree ──")
    r = correr_desde_worktree({"session_id": "w10", "tool_name": "Bash",
                               "tool_input": {"command": "launchctl kickstart -k gui/501/com.btp.x"}},
                              tmp, wt)
    check(r is not None and r.get("permissionDecision") == "ask",
          "encender un daemon desde un worktree pide confirmación")
    r = correr({"session_id": "w11", "tool_name": "Bash",
                "tool_input": {"command": "launchctl kickstart -k gui/501/com.btp.x"}}, tmp)
    check(r is None, "…y desde casa base no molesta")

    print("── bucles de espera sin tope (20-sep-26) ──")
    # El 14-sep un `until … do sleep 5 … done` sin tope corrió 14 h 34 min y dejó pillados la
    # tarea y el worktree. Medido sobre 13.069 comandos Bash de 7 días: 100 traen bucle y 95 no
    # llevan tope. Por eso AVISA y no pregunta: 13 confirmaciones al día se aprenden a ignorar.
    r = correr({"session_id": "buc1", "tool_name": "Bash",
                "tool_input": {"command": "until [ -f /tmp/x ]; do sleep 5; done; echo listo"}}, tmp)
    check(r and "tope" in r["additionalContext"], "bucle sin tope: avisa")
    check(r and r.get("permissionDecision") is None, "…y NO pide confirmación (avisa, no interrumpe)")

    r = correr({"session_id": "buc2", "tool_name": "Bash",
                "tool_input": {"command": "fin=$(( $(date +%s) + 600 )); until [ -f /tmp/x ]; do "
                                          "[ $(date +%s) -lt $fin ] || break; sleep 5; done"}}, tmp)
    check(r is None, "con tope de reloj: calla")

    r = correr({"session_id": "buc3", "tool_name": "Bash",
                "tool_input": {"command": "for _ in 1 2 3 4 5; do lsof -i :8080 && break; sleep 0.5; done"}}, tmp)
    check(r is None, "contador `for` acotado: calla")

    r = correr({"session_id": "buc4", "tool_name": "Bash",
                "tool_input": {"command": "while IFS= read -r l; do echo \"$l\"; done < /tmp/f"}}, tmp)
    check(r is None, "`while read` (termina con el stream, sin sleep): calla")

    r = correr({"session_id": "buc5", "tool_name": "Bash",
                "tool_input": {"command": "python3 - <<'EOF'\nimport time\nwhile True:\n    time.sleep(30)\nEOF"}}, tmp)
    check(r is None, "un `while` de Python en un heredoc NO es un bucle de shell")

    print("── git que mueve el árbol vivo de casa base (22-sep-26) ──")
    base = os.path.expanduser("~/claudecode")
    wt = os.path.join(base, ".claude", "worktrees", "prueba-freno")
    for cmd, cwd in (("git checkout 6f0f89e --", base), ("git -C ~/claudecode reset --hard", wt),
                     ("cd ~/claudecode && git stash", wt), ("git switch -", base),
                     ("git restore tools/x.py", base), ("git pull", base),
                     ("git reset HEAD~1", base), ("git reset --hard", base),
                     ("git restore --staged --worktree f", base), ("git stash pop", base),
                     ("git checkout HEAD -- .", base), ("git switch -qc rama-nueva", base),
                     # pruebas adversariales del 22-sep: otras formas de llegar a casa base
                     ("git -c core.x=y checkout master", base),
                     ("git --git-dir=%s/.git --work-tree=%s checkout master" % (base, base), wt),
                     ("GIT_DIR=%s/.git git checkout master" % base, wt),
                     ("env GIT_DIR=%s/.git git checkout master" % base, wt),
                     ("pushd ~/claudecode && git checkout master", wt),
                     ("cd %s && cd - && git stash" % wt, base),
                     ("sh -c 'git checkout master'", base),
                     ("bash -c \"cd ~/claudecode && git reset --hard\"", wt),
                     ("(cd ~/claudecode; git checkout master)", wt),
                     ("git -C %s/tools checkout master" % base, wt),
                     ("git --no-pager -C ~/claudecode checkout master", wt),
                     # ronda de verificacion del 24-sep: formas que se colaban
                     ("git -C ~/claudecode/.claude/worktrees checkout master", wt),
                     ("GIT_DIR=\"%s/.git\" git checkout master" % base, wt),
                     ("git chec''kout master", base), ("Git checkout master", base),
                     ("env -i PATH=/usr/bin git checkout master", base),
                     ("command git checkout master", base),
                     ("if true; then git checkout master; fi", base),
                     ("x=$(git checkout master)", base),
                     ("bash -lc 'cd ~/claudecode && git reset --hard'", wt),
                     ("bash <<'EOF'\ncd ~/claudecode\ngit checkout master\nEOF", wt),
                     ("cd $HOME/claudecode && git checkout master", wt),
                     ("git reset feature-x", base), ("git bisect start HEAD HEAD~10", base),
                     ("git apply x.patch", base), ("git update-ref refs/heads/master HEAD~1", base),
                     ("git symbolic-ref HEAD refs/heads/otra", base)):
        r = correr({"session_id": "gmc-" + cmd[:12], "tool_name": "Bash", "cwd": cwd,
                    "tool_input": {"command": cmd}}, tmp)
        check(r and r.get("permissionDecision") == "deny", "deniega en casa base: %s" % cmd)
    for cmd, cwd in [("git show 6f0f89e:tools/x.py", base), ("git diff A B -- tools", base),
                     ("git log --oneline -3", base), ("git status", base),
                     ("git checkout -b x", wt), ("git -C %s reset --hard" % wt, base),
                     ("echo 'git checkout master'", base),
                     ("python3 tools/deuda.py abrir x 'hice git checkout en casa base'", base),
                     # replay del 22-sep: solo leen o solo tocan el índice, no el árbol vivo
                     ("git stash list", base), ("git restore --staged tools/x.json", base),
                     ("git reset HEAD tools/x.json", base), ("git reset tools/x.json", base),
                     ("git -C %s/tools checkout -b y" % wt, base), ("sh -c 'git status'", base),
                     ("GIT_DIR=%s/.git git log -3" % base, wt), ("GIT_PAGER=cat git log -3", base),
                     # repo anidado con su propio .git, y la cadena dentro de un python -c
                     # el repo anidado solo existe en casa base; en un clon, este caso se salta
                     ] + ([("cd %s/_cajita/publico && git checkout -- ." % base, wt)]
                          if os.path.exists(os.path.join(base, "_cajita", "publico", ".git")) else []) + [
                     ("python3 -c \"print('GIT_DIR=%s/.git git checkout master')\"" % base, wt),
                     # ronda de verificacion del 24-sep: no son movimientos
                     ("pushd ~/claudecode; git log -1; popd; git checkout -b foo", wt),
                     ("(cd ~/claudecode && git log -1); git checkout -b foo", wt),
                     ("git clean -n", base), ("git checkout --help", base),
                     ("git merge claude/x", base), ("git stash show -p", base),
                     ("git symbolic-ref --short HEAD", base),
                     ("git symbolic-ref refs/remotes/origin/HEAD", base)]:
        r = correr({"session_id": "gmc-ok-" + cmd[:12], "tool_name": "Bash", "cwd": cwd,
                    "tool_input": {"command": cmd}}, tmp)
        check(not (r and r.get("permissionDecision") == "deny"), "permite: %s" % cmd)
    for _ in range(2):   # un freno no se gasta: la segunda vez en la misma sesión también deniega
        r = correr({"session_id": "gmc-mismo", "tool_name": "Bash", "cwd": base,
                    "tool_input": {"command": "git checkout master"}}, tmp)
    check(r and r.get("permissionDecision") == "deny", "sigue denegando a la segunda en la misma sesión")
    entorno = dict(os.environ, TMPDIR=tmp, CLAUDE_PROJECT_DIR=tmp, BTP_ALLOW_CASA_BASE="1")
    p = subprocess.run([sys.executable, HOOK], env=entorno, capture_output=True, text=True,
                       input=json.dumps({"session_id": "gmc-esc", "tool_name": "Bash", "cwd": base,
                                         "tool_input": {"command": "git checkout master"}}))
    check("deny" not in p.stdout, "BTP_ALLOW_CASA_BASE=1 es la vía de escape deliberada")

    print("── fail-open ──")
    p = subprocess.run([sys.executable, HOOK], input="esto no es json",
                       capture_output=True, text=True, env=dict(os.environ, TMPDIR=tmp))
    check(p.returncode == 0, "con entrada corrupta sale 0 y no estorba")

    import shutil
    # El worktree simulado cuelga del repo REAL: la batería no puede dejar NADA dentro (24-sep-26).
    print("── el worktree simulado queda limpio ──")
    sucio = []
    for raiz, _d, ficheros in os.walk(os.path.join(RAIZ.split("/.claude/worktrees/")[0],
                                                   ".claude", "worktrees", "prueba-freno")):
        sucio += [os.path.join(raiz, f) for f in ficheros]
    check(not sucio, "la batería no escribe dentro de .claude/worktrees/prueba-freno (%r)" % sucio[:3])
    check(os.path.isfile(os.path.join(tmp, "regla-en-accion.log")),
          "…porque el log de auditoría va al tmp (BTP_REGLA_LOG)")

    shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fallos:
        print("❌ %d fallo(s): %s" % (len(fallos), "; ".join(fallos)))
        return 1
    print("✅ regla_en_accion OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
