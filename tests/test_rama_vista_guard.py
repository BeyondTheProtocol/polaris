#!/usr/bin/env python3
"""test_rama_vista_guard.py — si otra sesión te cambia la rama, el commit no sale.

NORMA: `feedback-web-repo-checkout-compartido` (clase BLOQUEO, repetida) — «el repo web NO está
worktree-aislado; sesiones paralelas cambian la rama bajo tus pies. Verifica la rama justo antes
de cada commit/push».

Es un TOCTOU y se prueba como tal: el test SIMULA la sesión intrusa haciendo un `git checkout`
de verdad sobre el repo entre el momento en que la sesión mira y el momento en que commitea.
Sin simular la carrera no se estaría probando la norma, sino un `if`.

Lo que NO se puede romper: que dos sesiones distintas compartan apunte (sería el mismo bug), y
que un `checkout` PROPIO se confunda con la deriva de otro.
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
GUARD = _os.path.join(ROOT, ".claude", "hooks", "rama_vista_guard.py")

_TMP = tempfile.mkdtemp(prefix="ramavista_")
REPO = _os.path.join(_TMP, "repo-compartido")
APUNTES = _os.path.join(_TMP, "apuntes")
_os.makedirs(REPO)
_sh = dict(cwd=REPO, check=True, capture_output=True)
subprocess.run(["git", "init", "-q", "-b", "main"], **_sh)
subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                "--allow-empty", "-m", "i"], **_sh)
subprocess.run(["git", "branch", "rama-de-otra-sesion"], **_sh)
subprocess.run(["git", "branch", "mi-rama"], **_sh)

ENV = dict(_os.environ, BTP_RAMA_VISTA_DIR=APUNTES)
ENV.pop("BTP_RAMA_OK", None)


def _rc(cmd, sesion="sesion-A", env=None, tool="Bash"):
    p = subprocess.run([_sys.executable, GUARD], capture_output=True, text=True, timeout=25,
                       env=env or ENV,
                       input=json.dumps({"tool_name": tool, "session_id": sesion,
                                         "tool_input": {"command": cmd}, "cwd": REPO}))
    return p.returncode, (p.stderr or "").strip()


def _otra_sesion_cambia_a(rama):
    """La carrera de verdad: otra sesión hace checkout sobre el MISMO árbol."""
    subprocess.run(["git", "checkout", "-q", rama], cwd=REPO, check=True, capture_output=True)


fallos = 0
casos = []


def check(desc, ok):
    global fallos
    casos.append((desc, ok))
    if not ok:
        fallos += 1


# ── el caso de la norma, con la carrera simulada ──────────────────────────────────────────
subprocess.run(["git", "checkout", "-q", "mi-rama"], cwd=REPO, check=True, capture_output=True)
rc, _ = _rc("git status --short")                      # la sesión mira: apunta mi-rama
check("mirar la rama pasa y deja apunte", rc == 0)
rc, _ = _rc("git commit -m 'trabajo'")                 # sin deriva → pasa
check("commit en la rama que viste → PASA", rc == 0)

_otra_sesion_cambia_a("rama-de-otra-sesion")           # ← la carrera
rc, msg = _rc("git commit -m 'trabajo'")
check("commit DESPUÉS de que otra sesión cambie la rama → DENIEGA", rc == 2)
check("el mensaje dice cuál viste y en cuál está",
      "mi-rama" in msg and "rama-de-otra-sesion" in msg)
rc, _ = _rc("git push origin HEAD")
check("push tras la deriva → DENIEGA", rc == 2)

# ── volver a mirar re-establece la línea base ─────────────────────────────────────────────
rc, _ = _rc("git status")
check("volver a mirar pasa", rc == 0)
rc, _ = _rc("git commit -m x")
check("commit tras haber vuelto a mirar → PASA", rc == 0)

# ── un checkout PROPIO no es deriva ───────────────────────────────────────────────────────
rc, _ = _rc("git checkout mi-rama")
check("mi propio checkout pasa", rc == 0)
subprocess.run(["git", "checkout", "-q", "mi-rama"], cwd=REPO, check=True, capture_output=True)
rc, _ = _rc("git commit -m x")
check("commit tras MI checkout → PASA (no es deriva ajena)", rc == 0)
rc, _ = _rc("git checkout -b rama-nueva")
check("checkout -b pasa", rc == 0)
subprocess.run(["git", "checkout", "-q", "-b", "rama-nueva"], cwd=REPO, check=True, capture_output=True)
rc, _ = _rc("git commit -m x")
check("commit tras `checkout -b` → PASA (se apunta la rama destino)", rc == 0)

# ── ambigüedad → se borra el apunte, no se inventa ────────────────────────────────────────
rc, _ = _rc("git checkout -- fichero.txt")
check("`checkout -- fichero` (restaurar) pasa", rc == 0)
rc, _ = _rc("git commit -m x")
check("commit tras un checkout ambiguo → PASA (apunte borrado, no falso positivo)", rc == 0)

# ── dos sesiones NO comparten apunte ──────────────────────────────────────────────────────
subprocess.run(["git", "checkout", "-q", "mi-rama"], cwd=REPO, check=True, capture_output=True)
_rc("git status", sesion="sesion-A")
_otra_sesion_cambia_a("rama-de-otra-sesion")
_rc("git status", sesion="sesion-B")                   # B mira y ve la nueva
rc, _ = _rc("git commit -m x", sesion="sesion-B")
check("la sesión B, que SÍ vio la rama nueva, commitea → PASA", rc == 0)
rc, _ = _rc("git commit -m x", sesion="sesion-A")
check("la sesión A, que NO la vio, sigue denegada → DENIEGA", rc == 2)

# ── lo que no se toca ─────────────────────────────────────────────────────────────────────
rc, _ = _rc("git log --oneline -3", sesion="sesion-A")
check("un git de lectura nunca deniega", rc == 0)
rc, _ = _rc("ls -la && echo hola", sesion="sesion-C")
check("un comando que no es git → PASA", rc == 0)
rc, _ = _rc("cd /tmp && git commit -m x", sesion="sesion-D")
check("commit en un directorio SIN repo → PASA (nada que vigilar)", rc == 0)
subprocess.run(["git", "checkout", "-q", "mi-rama"], cwd=REPO, check=True, capture_output=True)
_rc("git status", sesion="sesion-E")
_otra_sesion_cambia_a("rama-de-otra-sesion")
rc, _ = _rc("git commit -m x", sesion="sesion-E", env=dict(ENV, BTP_RAMA_OK="1"))
check("BTP_RAMA_OK=1 → PASA (escotilla explícita)", rc == 0)
rc, _ = _rc("git commit -m x", sesion="sesion-E", env=dict(ENV, BTP_RAMA_OK="0"))
check("BTP_RAMA_OK=0 NO cuenta como escotilla → DENIEGA", rc == 2)
rc, _ = _rc("git commit -m x", sesion="sesion-NUEVA")
check("primer contacto de una sesión → PASA (detecta deriva, no exige haber mirado)", rc == 0)
rc, _ = _rc("git commit -m x", tool="Write")
check("tool que no es Bash → PASA", rc == 0)
p = subprocess.run([_sys.executable, GUARD], input="{no es json", capture_output=True,
                   text=True, timeout=20, env=ENV)
check("payload ilegible → PASA (fail-OPEN deliberado)", p.returncode == 0)
rc, _ = _rc("git commit -m 'comilla sin cerrar", sesion="sesion-F")
check("comando no tokenizable → PASA (fail-OPEN deliberado)", rc == 0)

for desc, ok in casos:
    print(("  ✅ " if ok else "  ❌ ") + desc)
shutil.rmtree(_TMP, ignore_errors=True)
print()
print("RESULTADO rama_vista: %d de %d OK" % (len(casos) - fallos, len(casos)))
print("✅ LA RAMA NO CAMBIA BAJO LOS PIES" if not fallos else "❌ revisar")
raise SystemExit(1 if fallos else 0)
