#!/usr/bin/env python3
"""test_cerrar_sesion_ruido.py — una fusión que deja ROJA una batería de casa base HACE RUIDO.

POR QUÉ EXISTE (10-oct-26, punto F; revisión de consejero-arquitectura). `cerrar_sesion --apply` corría
en casa base lo que un worktree salta y, si salía rojo, solo lo IMPRIMÍA: quien no leyera esa línea no se
enteraba y nadie abría deuda. Hoy mismo `test_anatomia_al_dia` quedó roja por la fusión de OTRA sesión y
una rama posterior habría cargado con ella. Ahora el cierre compara con la LÍNEA BASE anterior a la fusión
y, sin revertir ni bloquear nada:
  · rojo NUEVO (la línea base fresca lo daba en verde) → deuda `casa-base-roja-<bat>` con la rama y el commit,
    aviso, y aviso URGENTE si es del núcleo del muro;
  · rojo PREVIO (línea base, deuda abierta o nocturna) → no se atribuye, no avisa, `visto` en su deuda;
  · SIN línea base fiable (no existe o es de otro punto) → se abre deuda pero NO se culpa a la rama; avisa solo
    si es del muro;
  · el resumen de una línea lo dice. Se prueba con fusiones REALES sobre una casa base de usar y tirar.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.environ.get("BTP_CIERRE_TOOL") or os.path.join(ROOT, "tools", "cerrar_sesion.py")
fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True).stdout.strip()


BATERIA = "import sys\nSKIP = 77\nsys.exit(1 if 'malo' in open('valor.txt').read() else 0)\n"
SIEMPRE_ROJA = "import sys\nSKIP = 77\nsys.exit(1)\n"


def casa(tmp, rompe=("test_dep.py",), siempre_roja=(), baseline=None, deudas=None):
    """Casa base + un worktree cuya rama EMPEORA `valor.txt` (rompe las baterías que lo leen)."""
    base = os.path.join(tmp, "casa")
    os.makedirs(os.path.join(base, "tests"))
    git(base, "init", "-q", "-b", "master")
    git(base, "config", "user.email", "t@t")
    git(base, "config", "user.name", "t")
    open(os.path.join(base, ".gitignore"), "w").write(".claude/worktrees/\n")
    open(os.path.join(base, "valor.txt"), "w").write("bueno\n")
    for n in rompe:
        open(os.path.join(base, "tests", n), "w").write(BATERIA)
    for n in siempre_roja:
        open(os.path.join(base, "tests", n), "w").write(SIEMPRE_ROJA)
    git(base, "add", "-A")
    git(base, "commit", "-q", "-m", "inicio")
    wt = os.path.join(base, ".claude", "worktrees", "x")
    git(base, "worktree", "add", "-q", "-b", "claude/x", wt)
    open(os.path.join(wt, "valor.txt"), "w").write("malo\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "empeora el valor")
    estado = os.path.join(tmp, "state")
    os.makedirs(estado)
    if baseline is not None:
        os.makedirs(os.path.join(estado, "cerrar_sesion"))
        sha = git(base, "rev-parse", "HEAD") if baseline.get("sha") == "HEAD" else baseline.get("sha", "otra")
        json.dump({"sha": sha, "fecha": "2026-10-10T00:00:00", "rojas": baseline.get("rojas", [])},
                  open(os.path.join(estado, "cerrar_sesion", "rojas_casa_base.json"), "w"))
    if deudas:
        json.dump({k: {"estado": "abierto", "veces": 1, "que": "previa", "impacto_ned": "medio", "muro": False,
                       "abierto_ts": 1, "visto_ts": 1, "test": None, "dueno": "x", "nota": ""} for k in deudas},
                  open(os.path.join(estado, "deuda.json"), "w"))
    return base, wt, estado


def cierra(base, wt, estado):
    sumidero = os.path.join(estado, "avisos.jsonl")
    env = dict(os.environ, BTP_REPO=base, BTP_GIT_BASE_OK="1", BTP_STATE_DIR=estado, BTP_CIERRE_AVISO_A=sumidero)
    for k in ("CLAUDECODE", "BTP_CIERRE_SIN_VERIFICAR"):
        env.pop(k, None)
    p = subprocess.run([sys.executable, TOOL, "--apply", "--no-poda"], cwd=wt, env=env, capture_output=True,
                       text=True, timeout=300, stdin=subprocess.DEVNULL)
    avisos = [json.loads(l) for l in open(sumidero)] if os.path.exists(sumidero) else []
    try:
        libro = json.load(open(os.path.join(estado, "deuda.json")))
    except Exception:
        libro = {}
    try:
        lb = json.load(open(os.path.join(estado, "cerrar_sesion", "rojas_casa_base.json")))
    except Exception:
        lb = None
    return p.stdout + p.stderr, avisos, libro, lb


print("1) la fusión ROMPE una batería que estaba verde")
tmp = tempfile.mkdtemp(prefix="ruido_")
base, wt, est = casa(tmp, rompe=("test_dep.py",), siempre_roja=("test_previa.py",),
                     baseline={"sha": "HEAD", "rojas": ["test_previa.py"]})
out, avisos, libro, lb = cierra(base, wt, est)
check(os.path.exists(os.path.join(base, "valor.txt")) and "malo" in open(os.path.join(base, "valor.txt")).read(), "la fusión se hizo (no se bloquea ni revierte)")
check("casa-base-roja-test_dep.py" in libro, "abre deuda estable casa-base-roja-test_dep.py (%s)" % sorted(libro))
d = libro.get("casa-base-roja-test_dep.py", {})
check("claude/x" in d.get("que", "") and "VERDE a ROJA" in d.get("que", ""), "la deuda dice la rama y que estaba verde")
check(len(avisos) == 1 and "test_dep.py" in avisos[0]["texto"] and not avisos[0]["urgente"], "emite UN aviso con la batería (no urgente: no es del muro)")
check("test_previa.py" not in avisos[0]["texto"] if avisos else False, "el aviso NO culpa a la rama de test_previa.py, que ya estaba roja")
t0 = avisos[0]["texto"] if avisos else ""
check("claude/x" in t0 and "casa-base-roja-test_dep.py" in t0 and "No he revertido" in t0 and "estaban en verde" in t0,
      "el aviso dice la rama, la deuda, que estaban en verde y que no se ha revertido nada: %r" % t0[:160])
check("casa-base-roja-test_previa.py" not in libro, "no abre deuda de lo que ya estaba (previa)")
linea = [l for l in out.splitlines() if l.startswith("✅ APLICADO")]
check(linea and "1 NUEVA(S) por esta fusión (test_dep.py)" in linea[0] and "1 ya estaban rojas" in linea[0],
      "la línea de RESUMEN (la de una línea) lo dice: %r" % (linea[:1],))
check("LAS ROMPIÓ ESTA FUSIÓN" in out and "ya estaban rojas antes de fusionar (no son de esta rama): test_previa.py" in out, "y separa lo que rompió de lo que ya estaba")
check(lb and sorted(lb["rojas"]) == ["test_dep.py", "test_previa.py"], "deja la línea base nueva para la próxima fusión")

print("2) la que rompe es del NÚCLEO DEL MURO → aviso inmediato")
tmp = tempfile.mkdtemp(prefix="ruido_")
base, wt, est = casa(tmp, rompe=("test_fuga_toy.py",), baseline={"sha": "HEAD", "rojas": []})
out, avisos, libro, lb = cierra(base, wt, est)
check(len(avisos) == 1 and avisos[0]["urgente"] and "MURO" in avisos[0]["texto"], "aviso URGENTE que nombra el muro")
check(libro.get("casa-base-roja-test_fuga_toy.py", {}).get("muro") is True, "deuda marcada muro")

print("3) SIN línea base: no se culpa a la rama")
tmp = tempfile.mkdtemp(prefix="ruido_")
base, wt, est = casa(tmp, rompe=("test_dep.py",), baseline=None)
out, avisos, libro, lb = cierra(base, wt, est)
check("casa-base-roja-test_dep.py" in libro and "NO hay línea base" in libro["casa-base-roja-test_dep.py"]["que"], "abre deuda diciendo que no sabe si ya estaba")
check(not avisos, "y no avisa (no es del muro, y no se sabe que la rompiera esta rama)")
check("NUEVA" not in out and "sin línea base previa, no se atribuyen" in out, "el resumen NO dice que la rompió la fusión")
check(lb is not None, "pero deja línea base para la próxima")
tmp = tempfile.mkdtemp(prefix="ruido_")
base, wt, est = casa(tmp, rompe=("test_fuga_toy.py",), baseline=None)
out, avisos, libro, lb = cierra(base, wt, est)
check(len(avisos) == 1 and avisos[0]["urgente"], "sin línea base pero del MURO: avisa igual, urgente")

print("4) línea base de OTRO punto de casa base (desfasada)")
tmp = tempfile.mkdtemp(prefix="ruido_")
base, wt, est = casa(tmp, rompe=("test_dep.py",), baseline={"sha": "0000000000000000000000000000000000000000", "rojas": []})
out, avisos, libro, lb = cierra(base, wt, est)
check(not avisos and "NUEVA" not in out and "casa-base-roja-test_dep.py" in libro, "desfasada = sin atribuir (deuda sí, culpa no)")

print("5) ya había deuda abierta → previa, y 'visto' en vez de duplicar")
tmp = tempfile.mkdtemp(prefix="ruido_")
base, wt, est = casa(tmp, rompe=("test_dep.py",), baseline=None, deudas=("suite-nocturna-test_dep.py",))
out, avisos, libro, lb = cierra(base, wt, est)
check(not avisos and "casa-base-roja-test_dep.py" not in libro, "la nocturna ya la tenía: ni aviso ni deuda duplicada")
check(libro["suite-nocturna-test_dep.py"].get("veces", 1) >= 2, "su deuda registra un 'visto' (veces=%s)" % libro["suite-nocturna-test_dep.py"].get("veces"))
check("1 ya estaban rojas" in out, "el resumen la cuenta como previa")
tmp = tempfile.mkdtemp(prefix="ruido_")
base, wt, est = casa(tmp, rompe=("test_dep.py",), baseline={"sha": "HEAD", "rojas": []}, deudas=("casa-base-roja-test_dep.py",))
out, avisos, libro, lb = cierra(base, wt, est)
check(sorted(k for k in libro if k.startswith("casa-base-roja")) == ["casa-base-roja-test_dep.py"] and not avisos, "con su propia deuda ya abierta no se duplica ni se vuelve a avisar")

print("5b) la pasada nocturna ya la daba por roja")
tmp = tempfile.mkdtemp(prefix="ruido_")
base, wt, est = casa(tmp, rompe=("test_dep.py",), baseline={"sha": "HEAD", "rojas": []})
os.makedirs(os.path.join(est, "suite_nocturna"))
open(os.path.join(est, "suite_nocturna", "historial.jsonl"), "w").write(json.dumps({"fecha": "2026-10-10", "nuevos": ["test_dep.py"], "flaky": [], "conocidos": []}) + "\n")
out, avisos, libro, lb = cierra(base, wt, est)
check(not avisos and "NUEVA" not in out and "1 ya estaban rojas" in out, "la nocturna la tenía: es previa, no de la rama")

print("6) todo en verde")
tmp = tempfile.mkdtemp(prefix="ruido_")
base, wt, est = casa(tmp, rompe=(), baseline={"sha": "HEAD", "rojas": ["test_vieja.py"]})
open(os.path.join(base, "tests", "test_ok.py"), "w").write("import sys\nSKIP = 77\nsys.exit(0)\n")
out, avisos, libro, lb = cierra(base, wt, est)
check(not avisos and not libro and "CASA BASE ROJA" not in out, "ni deuda ni aviso")
check(lb is not None and lb["rojas"] == [], "y la línea base queda limpia (sin rojas)")

if fallos:
    print("\n❌ %d fallo(s)" % len(fallos))
    sys.exit(1)
print("\nOK")
