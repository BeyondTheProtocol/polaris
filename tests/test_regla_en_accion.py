#!/usr/bin/env python3
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("estado")
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
    entorno = dict(os.environ, TMPDIR=tmpdir, CLAUDE_PROJECT_DIR=tmpdir)
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
    entorno = dict(os.environ, TMPDIR=tmpdir, CLAUDE_PROJECT_DIR=wt)
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
                "tool_input": {"command": "git push origin master"}}, tmp)
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
                 "tool_input": {"command": "git push origin master"}}, tmp)
    r2 = correr({"session_id": "z", "tool_name": "Bash",
                 "tool_input": {"command": "git push origin master"}}, tmp)
    check(r1 is not None and r2 is None, "la misma regla no se repite en la misma sesión")
    r3 = correr({"session_id": "otra", "tool_name": "Bash",
                 "tool_input": {"command": "git push origin master"}}, tmp)
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

    print("── la regla hermana de launchd, por fin probada dentro de un worktree ──")
    r = correr_desde_worktree({"session_id": "w10", "tool_name": "Bash",
                               "tool_input": {"command": "launchctl kickstart -k gui/501/com.btp.x"}},
                              tmp, wt)
    check(r is not None and r.get("permissionDecision") == "ask",
          "encender un daemon desde un worktree pide confirmación")
    r = correr({"session_id": "w11", "tool_name": "Bash",
                "tool_input": {"command": "launchctl kickstart -k gui/501/com.btp.x"}}, tmp)
    check(r is None, "…y desde casa base no molesta")

    print("── fail-open ──")
    p = subprocess.run([sys.executable, HOOK], input="esto no es json",
                       capture_output=True, text=True, env=dict(os.environ, TMPDIR=tmp))
    check(p.returncode == 0, "con entrada corrupta sale 0 y no estorba")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fallos:
        print("❌ %d fallo(s): %s" % (len(fallos), "; ".join(fallos)))
        return 1
    print("✅ regla_en_accion OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
